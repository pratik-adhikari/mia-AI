"""Durable product/run/message/event/DPP catalogue for SQLite and PostgreSQL."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from mia_dpp.domain.product import (
    ChatMessage,
    DppVersion,
    MessageRole,
    ProductRecord,
    ProductRun,
    RunEvent,
    RunStatus,
)
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.workflow.identity import canonical_product_url

ModelT = TypeVar("ModelT", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
    id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    started_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS runs_product ON runs(product_id, started_at);
CREATE TABLE IF NOT EXISTS messages(
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    run_id TEXT,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, timestamp);
CREATE TABLE IF NOT EXISTS events(
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, timestamp);
CREATE TABLE IF NOT EXISTS artifacts(
    id TEXT PRIMARY KEY,
    product_id TEXT,
    run_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_product ON artifacts(product_id, created_at);
CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts(run_id, created_at);
CREATE TABLE IF NOT EXISTS dpp_versions(
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(product_id, version),
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class _Cursor(Protocol):
    rowcount: int

    def fetchone(self): ...

    def fetchall(self): ...


class _Connection:
    """Tiny DB-API compatibility wrapper for SQLite and psycopg."""

    def __init__(self, raw: Any, *, postgres: bool) -> None:
        self._raw = raw
        self._postgres = postgres

    def __enter__(self) -> _Connection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            (self._raw.commit if exc_type is None else self._raw.rollback)()
        finally:
            self._raw.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if self._postgres:
            sql = sql.replace("?", "%s")
        return self._raw.execute(sql, params)

    def executescript(self, script: str) -> None:
        if not self._postgres:
            self._raw.executescript(script)
            return
        for statement in script.split(";"):
            if statement.strip():
                self._raw.execute(statement)


class ProductCatalogue:
    """Long-lived product history independent of graph checkpoint internals."""

    def __init__(self, location: Path | str) -> None:
        raw = str(location)
        self._database_url = (
            raw if raw.startswith(("postgres://", "postgresql://")) else None
        )
        self._path = None if self._database_url else Path(location)
        self._lock = Lock()
        self._ready = False

    @property
    def backend(self) -> str:
        return "postgres" if self._database_url else "sqlite"

    def get_or_create_product(self, url: str) -> tuple[ProductRecord, bool]:
        canonical = canonical_product_url(url)
        found = self._one(
            ProductRecord,
            "SELECT payload FROM products WHERE canonical_url=?",
            (canonical,),
        )
        if found is not None:
            return found, False
        product = ProductRecord(
            id=_new_id("product"),
            canonical_url=canonical,
            original_url=url,
        )
        self._execute(
            "INSERT INTO products(id, canonical_url, payload, updated_at) VALUES(?,?,?,?)",
            (product.id, canonical, product.model_dump_json(), _now().isoformat()),
        )
        return product, True

    def update_product(self, product: ProductRecord) -> ProductRecord:
        product = product.model_copy(update={"updated_at": _now()})
        changed = self._execute(
            "UPDATE products SET canonical_url=?, payload=?, updated_at=? WHERE id=?",
            (
                product.canonical_url,
                product.model_dump_json(),
                product.updated_at.isoformat(),
                product.id,
            ),
        )
        if changed != 1:
            raise KeyError(product.id)
        return product

    def get_product(self, product_id: str) -> ProductRecord | None:
        return self._one(ProductRecord, "SELECT payload FROM products WHERE id=?", (product_id,))

    def find_product_by_url(self, url: str) -> ProductRecord | None:
        return self._one(
            ProductRecord,
            "SELECT payload FROM products WHERE canonical_url=?",
            (canonical_product_url(url),),
        )

    def list_products(self) -> tuple[ProductRecord, ...]:
        return self._many(ProductRecord, "SELECT payload FROM products ORDER BY updated_at DESC")

    def start_run(
        self,
        product_id: str,
        thread_id: str,
        *,
        refresh_requested: bool = False,
        reused_from_run_id: str | None = None,
    ) -> ProductRun:
        run = ProductRun(
            id=_new_id("run"),
            product_id=product_id,
            thread_id=thread_id,
            refresh_requested=refresh_requested,
            reused_from_run_id=reused_from_run_id,
            status=RunStatus.REUSED if reused_from_run_id else RunStatus.RUNNING,
        )
        self._execute(
            "INSERT INTO runs(id, product_id, thread_id, status, payload, started_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                run.id,
                product_id,
                thread_id,
                run.status.value,
                run.model_dump_json(),
                run.started_at.isoformat(),
            ),
        )
        return run

    def finish_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        metrics: dict[str, int | float | str | bool | None] | None = None,
        error: str | None = None,
    ) -> ProductRun:
        run = self._require(self.get_run(run_id), run_id)
        run = run.model_copy(
            update={
                "status": status,
                "finished_at": _now(),
                "metrics": metrics or run.metrics,
                "error": error,
            }
        )
        self._execute(
            "UPDATE runs SET status=?, payload=? WHERE id=?",
            (status.value, run.model_dump_json(), run_id),
        )
        return run

    def get_run(self, run_id: str) -> ProductRun | None:
        return self._one(ProductRun, "SELECT payload FROM runs WHERE id=?", (run_id,))

    def list_runs(self, product_id: str) -> tuple[ProductRun, ...]:
        return self._many(
            ProductRun,
            "SELECT payload FROM runs WHERE product_id=? ORDER BY started_at DESC",
            (product_id,),
        )

    def add_message(
        self,
        thread_id: str,
        role: MessageRole,
        content: str,
        *,
        run_id: str | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            id=_new_id("message"),
            thread_id=thread_id,
            run_id=run_id,
            role=role,
            content=content,
        )
        self._execute(
            "INSERT INTO messages(id, thread_id, run_id, timestamp, payload) VALUES(?,?,?,?,?)",
            (
                message.id,
                thread_id,
                run_id,
                message.timestamp.isoformat(),
                message.model_dump_json(),
            ),
        )
        return message

    def list_messages(self, thread_id: str) -> tuple[ChatMessage, ...]:
        return self._many(
            ChatMessage,
            "SELECT payload FROM messages WHERE thread_id=? ORDER BY timestamp, rowid",
            (thread_id,),
        )

    def add_event(
        self,
        run_id: str,
        event_type: str,
        summary: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> RunEvent:
        event = RunEvent(
            id=_new_id("event"),
            run_id=run_id,
            event_type=event_type,
            summary=summary,
            metadata=metadata or {},
        )
        self._execute(
            "INSERT INTO events(id, run_id, timestamp, payload) VALUES(?,?,?,?)",
            (event.id, run_id, event.timestamp.isoformat(), event.model_dump_json()),
        )
        return event

    def list_events(self, run_id: str) -> tuple[RunEvent, ...]:
        return self._many(
            RunEvent,
            "SELECT payload FROM events WHERE run_id=? ORDER BY timestamp, rowid",
            (run_id,),
        )

    def register_artifact(self, artifact: StoredArtifact) -> StoredArtifact:
        self._execute(
            "INSERT INTO artifacts(id, product_id, run_id, payload, created_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "product_id=excluded.product_id, run_id=excluded.run_id, "
            "payload=excluded.payload, created_at=excluded.created_at",
            (
                artifact.id,
                artifact.product_id,
                artifact.run_id,
                artifact.model_dump_json(),
                artifact.created_at.isoformat(),
            ),
        )
        return artifact

    def get_artifact(self, artifact_id: str) -> StoredArtifact | None:
        return self._one(
            StoredArtifact,
            "SELECT payload FROM artifacts WHERE id=?",
            (artifact_id,),
        )

    def list_artifacts(
        self, *, product_id: str | None = None, run_id: str | None = None
    ) -> tuple[StoredArtifact, ...]:
        if product_id is not None:
            return self._many(
                StoredArtifact,
                "SELECT payload FROM artifacts WHERE product_id=? ORDER BY created_at",
                (product_id,),
            )
        if run_id is not None:
            return self._many(
                StoredArtifact,
                "SELECT payload FROM artifacts WHERE run_id=? ORDER BY created_at",
                (run_id,),
            )
        return self._many(StoredArtifact, "SELECT payload FROM artifacts ORDER BY created_at")

    def create_dpp_version(
        self,
        product_id: str,
        run_id: str,
        *,
        dpp_artifact_id: str,
        aas_artifact_id: str | None = None,
        validation_artifact_id: str | None = None,
        source_fingerprint: str | None = None,
        deployable: bool,
    ) -> DppVersion:
        row = self._fetchone(
            "SELECT COALESCE(MAX(version), 0) FROM dpp_versions WHERE product_id=?",
            (product_id,),
        )
        version = int(row[0]) + 1
        record = DppVersion(
            id=_new_id("dpp"),
            product_id=product_id,
            run_id=run_id,
            version=version,
            dpp_artifact_id=dpp_artifact_id,
            aas_artifact_id=aas_artifact_id,
            validation_artifact_id=validation_artifact_id,
            source_fingerprint=source_fingerprint,
            deployable=deployable,
        )
        self._execute(
            "INSERT INTO dpp_versions(id, product_id, run_id, version, payload, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                record.id,
                product_id,
                run_id,
                version,
                record.model_dump_json(),
                record.created_at.isoformat(),
            ),
        )
        return record

    def list_dpp_versions(self, product_id: str) -> tuple[DppVersion, ...]:
        return self._many(
            DppVersion,
            "SELECT payload FROM dpp_versions WHERE product_id=? ORDER BY version DESC",
            (product_id,),
        )

    def latest_successful_dpp(self, product_id: str) -> DppVersion | None:
        return next((item for item in self.list_dpp_versions(product_id) if item.deployable), None)

    def _raw_connect(self) -> _Connection:
        if self._database_url is not None:
            try:
                import psycopg  # type: ignore[import-not-found]
            except ImportError as error:  # pragma: no cover - production dependency
                raise RuntimeError(
                    "PostgreSQL catalogue requires psycopg; install the production extras"
                ) from error
            return _Connection(psycopg.connect(self._database_url), postgres=True)
        assert self._path is not None
        db = sqlite3.connect(self._path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return _Connection(db, postgres=False)

    def _connect(self) -> _Connection:
        self._setup()
        return self._raw_connect()

    def _setup(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._raw_connect() as db:
                if self._database_url is None:
                    db.execute("PRAGMA journal_mode=WAL")
                db.executescript(SCHEMA)
            self._ready = True

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()):
        with self._connect() as db:
            return db.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()):
        with self._connect() as db:
            return db.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._connect() as db:
            return db.execute(sql, params).rowcount

    def _one(
        self, model: type[ModelT], sql: str, params: tuple[Any, ...] = ()
    ) -> ModelT | None:
        row = self._fetchone(sql, params)
        return model.model_validate_json(row[0]) if row else None

    def _many(
        self, model: type[ModelT], sql: str, params: tuple[Any, ...] = ()
    ) -> tuple[ModelT, ...]:
        return tuple(model.model_validate_json(row[0]) for row in self._fetchall(sql, params))

    @staticmethod
    def _require(value: ModelT | None, key: str) -> ModelT:
        if value is None:
            raise KeyError(key)
        return value
