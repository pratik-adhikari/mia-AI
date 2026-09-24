"""Durable product/run/message/event/DPP catalogue for SQLite and PostgreSQL."""

from __future__ import annotations

import hashlib
import importlib.resources
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from types import TracebackType
from typing import Any, Protocol, TypeVar, cast

from pydantic import BaseModel

from mia_dpp.domain.mappings import FieldMapping
from mia_dpp.domain.product_work import HumanReviewRecord, ProductWorkSnapshot
from mia_dpp.domain.product import (
    BackgroundJob,
    BackgroundJobStatus,
    ChatMessage,
    DppVersion,
    MessageRole,
    ProductRecord,
    ProductRun,
    RunEvent,
    RunStatus,
    ThreadRecord,
)
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.tools.mapping.models import MappingKnowledgeEntry, MappingKnowledgeStatus
from mia_dpp.workflow.identity import canonical_product_url

ModelT = TypeVar("ModelT", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
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
CREATE TABLE IF NOT EXISTS mapping_knowledge(
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
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

LOCAL_USER_ID = "local-development"


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class _Cursor(Protocol):
    rowcount: int

    def fetchone(self) -> tuple[Any, ...] | None: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


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
        return cast(_Cursor, self._raw.execute(sql, params))

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
        self._database_url = raw if raw.startswith(("postgres://", "postgresql://")) else None
        self._path = None if self._database_url else Path(location)
        self._lock = Lock()
        self._ready = False

    @property
    def backend(self) -> str:
        return "postgres" if self._database_url else "sqlite"

    def get_or_create_product(
        self,
        url: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[ProductRecord, bool]:
        canonical = canonical_product_url(url)
        product = ProductRecord(
            id=_new_id("product"),
            canonical_url=canonical,
            original_url=url,
        )
        inserted = self._execute(
            "INSERT INTO products(id, canonical_url, payload, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(canonical_url) DO NOTHING",
            (product.id, canonical, product.model_dump_json(), _now().isoformat()),
        )
        if inserted == 1:
            self._associate_product(user_id, product.id)
            return product, True
        found = self.find_product_by_url(canonical)
        if found is None:  # pragma: no cover - a database invariant failed
            raise RuntimeError("product insert conflicted but no existing product was found")
        self._associate_product(user_id, found.id)
        return found, False

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

    def get_product(
        self,
        product_id: str,
        *,
        user_id: str | None = None,
    ) -> ProductRecord | None:
        if user_id is not None and not self.user_owns_product(user_id, product_id):
            return None
        return self._one(ProductRecord, "SELECT payload FROM products WHERE id=?", (product_id,))

    def find_product_by_url(self, url: str) -> ProductRecord | None:
        return self._one(
            ProductRecord,
            "SELECT payload FROM products WHERE canonical_url=?",
            (canonical_product_url(url),),
        )

    def list_products(self, *, user_id: str = LOCAL_USER_ID) -> tuple[ProductRecord, ...]:
        return self._many(
            ProductRecord,
            "SELECT products.payload FROM products "
            "JOIN user_products ON user_products.product_id=products.id "
            "WHERE user_products.user_id=? ORDER BY products.updated_at DESC",
            (user_id,),
        )

    def get_or_create_thread(
        self,
        thread_id: str,
        user_id: str,
        *,
        title: str | None = None,
    ) -> ThreadRecord:
        """Create a durable conversation or verify ownership of an existing identifier."""

        now = _now()
        thread = ThreadRecord(id=thread_id, user_id=user_id, title=title)
        self._execute(
            "INSERT INTO threads(id, user_id, payload, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO NOTHING",
            (thread.id, user_id, thread.model_dump_json(), now.isoformat()),
        )
        existing = self.get_thread(thread_id, user_id=user_id)
        if existing is None:
            raise PermissionError("thread belongs to another user")
        return existing

    def get_thread(self, thread_id: str, *, user_id: str) -> ThreadRecord | None:
        return self._one(
            ThreadRecord,
            "SELECT payload FROM threads WHERE id=? AND user_id=?",
            (thread_id, user_id),
        )

    def list_threads(self, user_id: str) -> tuple[ThreadRecord, ...]:
        return tuple(
            thread
            for thread in self._many(
                ThreadRecord,
                "SELECT payload FROM threads WHERE user_id=? ORDER BY updated_at DESC",
                (user_id,),
            )
            if thread.deleted_at is None
        )

    def delete_thread(self, thread_id: str, *, user_id: str) -> ThreadRecord:
        """Hide chat history without deleting product/run artifacts needed for reuse and audit."""

        thread = self._require(self.get_thread(thread_id, user_id=user_id), thread_id)
        now = _now()
        deleted = thread.model_copy(update={"deleted_at": now, "updated_at": now})
        self._execute(
            "UPDATE threads SET payload=?,updated_at=? WHERE id=? AND user_id=?",
            (deleted.model_dump_json(), now.isoformat(), thread_id, user_id),
        )
        return deleted

    def user_owns_product(self, user_id: str, product_id: str) -> bool:
        return (
            self._fetchone(
                "SELECT 1 FROM user_products WHERE user_id=? AND product_id=?",
                (user_id, product_id),
            )
            is not None
        )

    def _associate_product(self, user_id: str, product_id: str) -> None:
        self._execute(
            "INSERT INTO user_products(user_id, product_id, created_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id, product_id) DO NOTHING",
            (user_id, product_id, _now().isoformat()),
        )

    def start_run(
        self,
        product_id: str,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
        refresh_requested: bool = False,
        reused_from_run_id: str | None = None,
        seeded_from_run_id: str | None = None,
    ) -> ProductRun:
        if self.get_thread(thread_id, user_id=user_id) is None:
            if user_id == LOCAL_USER_ID:
                self.get_or_create_thread(thread_id, user_id)
            else:
                raise PermissionError("unknown thread")
        if not self.user_owns_product(user_id, product_id):
            raise PermissionError("unknown product")
        run = ProductRun(
            id=_new_id("run"),
            product_id=product_id,
            thread_id=thread_id,
            refresh_requested=refresh_requested,
            reused_from_run_id=reused_from_run_id,
            seeded_from_run_id=seeded_from_run_id,
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

    def set_run_status(self, run_id: str, status: RunStatus) -> ProductRun:
        run = self._require(self.get_run(run_id), run_id)
        run = run.model_copy(update={"status": status})
        self._execute(
            "UPDATE runs SET status=?, payload=? WHERE id=?",
            (status.value, run.model_dump_json(), run_id),
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

    def list_runs(
        self,
        product_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[ProductRun, ...]:
        return self._many(
            ProductRun,
            "SELECT runs.payload FROM runs JOIN threads ON threads.id=runs.thread_id "
            "WHERE runs.product_id=? AND threads.user_id=? ORDER BY runs.started_at DESC",
            (product_id, user_id),
        )

    def list_runs_for_thread(
        self,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[ProductRun, ...]:
        return self._many(
            ProductRun,
            "SELECT runs.payload FROM runs JOIN threads ON threads.id=runs.thread_id "
            "WHERE runs.thread_id=? AND threads.user_id=? ORDER BY runs.started_at",
            (thread_id, user_id),
        )

    def list_recent_runs(self, *, user_id: str, limit: int = 30) -> tuple[ProductRun, ...]:
        rows = self._fetchall(
            "SELECT runs.payload FROM runs JOIN threads ON threads.id=runs.thread_id "
            "WHERE threads.user_id=? ORDER BY runs.started_at DESC LIMIT ?",
            (user_id, max(1, min(limit, 100))),
        )
        return tuple(ProductRun.model_validate_json(row[0]) for row in rows)

    def add_message(
        self,
        thread_id: str,
        role: MessageRole,
        content: str,
        *,
        run_id: str | None = None,
        user_id: str = LOCAL_USER_ID,
    ) -> ChatMessage:
        thread = self.get_thread(thread_id, user_id=user_id)
        if thread is None:
            if user_id != LOCAL_USER_ID:
                raise PermissionError("unknown thread")
            thread = self.get_or_create_thread(thread_id, user_id)
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
        updated = thread.model_copy(
            update={"updated_at": message.timestamp, "last_message_at": message.timestamp}
        )
        self._execute(
            "UPDATE threads SET payload=?, updated_at=? WHERE id=? AND user_id=?",
            (updated.model_dump_json(), updated.updated_at.isoformat(), thread_id, user_id),
        )
        return message

    def list_messages(
        self,
        thread_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[ChatMessage, ...]:
        return self._many(
            ChatMessage,
            "SELECT messages.payload FROM messages "
            "JOIN threads ON threads.id=messages.thread_id "
            "WHERE messages.thread_id=? AND threads.user_id=? "
            "ORDER BY messages.timestamp, messages.id",
            (thread_id, user_id),
        )

    def assign_message_to_run(self, message_id: str, run_id: str) -> ChatMessage:
        message = self._require(
            self._one(ChatMessage, "SELECT payload FROM messages WHERE id=?", (message_id,)),
            message_id,
        )
        message = message.model_copy(update={"run_id": run_id})
        changed = self._execute(
            "UPDATE messages SET run_id=?, payload=? WHERE id=?",
            (run_id, message.model_dump_json(), message_id),
        )
        if changed != 1:  # pragma: no cover - message was removed concurrently
            raise KeyError(message_id)
        return message

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

    def list_events(self, run_id: str, *, user_id: str | None = None) -> tuple[RunEvent, ...]:
        if user_id is not None:
            run = self.get_run(run_id)
            if run is None or self.get_thread(run.thread_id, user_id=user_id) is None:
                return ()
        return self._many(
            RunEvent,
            "SELECT payload FROM events WHERE run_id=? ORDER BY timestamp, id",
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
        self,
        *,
        product_id: str | None = None,
        run_id: str | None = None,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[StoredArtifact, ...]:
        if product_id is not None:
            return self._many(
                StoredArtifact,
                "SELECT artifacts.payload FROM artifacts "
                "JOIN runs ON runs.id=artifacts.run_id "
                "JOIN threads ON threads.id=runs.thread_id "
                "WHERE artifacts.product_id=? AND threads.user_id=? ORDER BY artifacts.created_at",
                (product_id, user_id),
            )
        if run_id is not None:
            return self._many(
                StoredArtifact,
                "SELECT artifacts.payload FROM artifacts "
                "JOIN runs ON runs.id=artifacts.run_id "
                "JOIN threads ON threads.id=runs.thread_id "
                "WHERE artifacts.run_id=? AND threads.user_id=? ORDER BY artifacts.created_at",
                (run_id, user_id),
            )
        return self._many(
            StoredArtifact,
            "SELECT artifacts.payload FROM artifacts "
            "JOIN runs ON runs.id=artifacts.run_id "
            "JOIN threads ON threads.id=runs.thread_id "
            "WHERE threads.user_id=? ORDER BY artifacts.created_at",
            (user_id,),
        )

    def get_product_work_snapshot(
        self,
        product_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> ProductWorkSnapshot | None:
        if not self.user_owns_product(user_id, product_id):
            return None
        return self._one(
            ProductWorkSnapshot,
            "SELECT payload FROM product_work_snapshots WHERE user_id=? AND product_id=?",
            (user_id, product_id),
        )

    def save_product_work_snapshot(
        self,
        snapshot: ProductWorkSnapshot,
    ) -> ProductWorkSnapshot:
        """Version and replace the authoritative product-level workflow pointer set."""

        if not self.user_owns_product(snapshot.user_id, snapshot.product_id):
            raise PermissionError("unknown product")
        existing = self.get_product_work_snapshot(
            snapshot.product_id,
            user_id=snapshot.user_id,
        )
        now = _now()
        stored = snapshot.model_copy(
            update={
                "version": (existing.version + 1) if existing else 1,
                "created_at": existing.created_at if existing else snapshot.created_at,
                "updated_at": now,
            }
        )
        self._execute(
            "INSERT INTO product_work_snapshots(user_id,product_id,version,payload,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(user_id,product_id) DO UPDATE SET "
            "version=excluded.version,payload=excluded.payload,updated_at=excluded.updated_at",
            (
                stored.user_id,
                stored.product_id,
                stored.version,
                stored.model_dump_json(),
                stored.updated_at.isoformat(),
            ),
        )
        return stored

    def add_human_review(self, review: HumanReviewRecord) -> HumanReviewRecord:
        """Append an immutable human decision; existing records are never updated."""

        if not self.user_owns_product(review.user_id, review.product_id):
            raise PermissionError("unknown product")
        self._execute(
            "INSERT INTO human_reviews(id,user_id,product_id,run_id,thread_id,created_at,payload) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                review.id,
                review.user_id,
                review.product_id,
                review.run_id,
                review.thread_id,
                review.created_at.isoformat(),
                review.model_dump_json(),
            ),
        )
        return review

    def list_human_reviews(
        self,
        product_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[HumanReviewRecord, ...]:
        if not self.user_owns_product(user_id, product_id):
            return ()
        return self._many(
            HumanReviewRecord,
            "SELECT payload FROM human_reviews WHERE user_id=? AND product_id=? "
            "ORDER BY created_at,id",
            (user_id, product_id),
        )

    def latest_reusable_artifacts(
        self,
        product_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[ProductRun | None, dict[str, StoredArtifact]]:
        """Return the newest prior run with durable evidence and its reusable stage artifacts."""

        evidence_keys = (
            "evidence/product-knowledge-human.json",
            "evidence/product-knowledge-reviewed.json",
            "evidence/product-knowledge-integrated.json",
            "evidence/product-knowledge.json",
        )
        mapping_keys = (
            "mapping/human-value.json",
            "mapping/reviewed.json",
            "mapping/research-integrated.json",
            "mapping/reused-reviewed.json",
            "mapping/mapping.json",
        )
        for run in self.list_runs(product_id, user_id=user_id):
            artifacts = self.list_artifacts(run_id=run.id, user_id=user_id)
            by_key = {item.key: item for item in artifacts}
            evidence = next((by_key[key] for key in evidence_keys if key in by_key), None)
            if evidence is None:
                continue
            reusable: dict[str, StoredArtifact] = {"evidence": evidence}
            mapping = next((by_key[key] for key in mapping_keys if key in by_key), None)
            if mapping is not None:
                reusable["reviewed_mapping"] = mapping
            if "mapping/targets.json" in by_key:
                reusable["targets"] = by_key["mapping/targets.json"]
            if "mapping/coverage.json" in by_key:
                reusable["coverage"] = by_key["mapping/coverage.json"]
            return run, reusable
        return None, {}

    def list_artifacts_for_runs(
        self,
        run_ids: tuple[str, ...],
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[StoredArtifact, ...]:
        """Return artifacts for a thread's runs without scanning the global manifest."""

        if not run_ids:
            return ()
        placeholders = ",".join("?" for _ in run_ids)
        return self._many(
            StoredArtifact,
            f"SELECT artifacts.payload FROM artifacts "
            "JOIN runs ON runs.id=artifacts.run_id "
            "JOIN threads ON threads.id=runs.thread_id "
            f"WHERE artifacts.run_id IN ({placeholders}) AND threads.user_id=? "
            "ORDER BY artifacts.created_at, artifacts.id",
            (*run_ids, user_id),
        )

    def remember_mapping_candidate(
        self,
        mapping: FieldMapping,
        *,
        manufacturer: str | None,
        domain: str | None,
        product_family: str | None,
    ) -> MappingKnowledgeEntry:
        return self._upsert_mapping_knowledge(
            mapping,
            manufacturer=manufacturer,
            domain=domain,
            product_family=product_family,
            status=MappingKnowledgeStatus.CANDIDATE,
        )

    def remember_mapping_review(
        self,
        mapping: FieldMapping,
        *,
        decision: str,
        manufacturer: str | None,
        domain: str | None,
        product_family: str | None,
        comment: str | None,
        actor_name: str | None = None,
    ) -> MappingKnowledgeEntry | None:
        # A DUMMY is a workflow placeholder, never reusable semantic knowledge.
        if mapping.human_value_kind == "dummy":
            return None
        status = (
            MappingKnowledgeStatus.TRUSTED
            if decision in {"approve", "correct", "keep", "change_target"}
            else MappingKnowledgeStatus.CANDIDATE
        )
        return self._upsert_mapping_knowledge(
            mapping,
            manufacturer=manufacturer,
            domain=domain,
            product_family=product_family,
            status=status,
            decision=decision,
            comment=comment,
        )

    def list_mapping_knowledge(self) -> tuple[MappingKnowledgeEntry, ...]:
        return self._many(
            MappingKnowledgeEntry,
            "SELECT payload FROM mapping_knowledge ORDER BY id DESC",
        )

    def relevant_mapping_knowledge(
        self,
        source_field: str,
        *,
        manufacturer: str | None,
        domain: str | None,
        template_keys: tuple[str, ...],
    ) -> tuple[MappingKnowledgeEntry, ...]:
        label = self._normalize(source_field)
        return tuple(
            item
            for item in self.list_mapping_knowledge()
            if item.status is MappingKnowledgeStatus.TRUSTED
            and item.target_template in template_keys
            and self._normalize(item.source_field) == label
            and (not item.domain or not domain or item.domain == domain)
            and (not item.manufacturer or not manufacturer or item.manufacturer == manufacturer)
        )

    def _upsert_mapping_knowledge(
        self,
        mapping: FieldMapping,
        *,
        manufacturer: str | None,
        domain: str | None,
        product_family: str | None,
        status: MappingKnowledgeStatus,
        decision: str | None = None,
        comment: str | None = None,
    ) -> MappingKnowledgeEntry:
        identity = "\0".join(
            (
                self._normalize(mapping.source_field),
                domain or "",
                mapping.target.template_key,
                "/".join(mapping.target.template_path),
            )
        )
        entry_id = "knowledge-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        existing = self._one(
            MappingKnowledgeEntry,
            "SELECT payload FROM mapping_knowledge WHERE id=?",
            (entry_id,),
        )
        now = _now()
        values = tuple(
            dict.fromkeys((*((existing.example_values) if existing else ()), mapping.source_value))
        )[-5:]
        comments = tuple(
            dict.fromkeys(
                (*((existing.human_comments) if existing else ()), *((comment,) if comment else ()))
            )
        )
        entry = MappingKnowledgeEntry(
            id=entry_id,
            source_field=mapping.source_field,
            example_values=values,
            target_template=mapping.target.template_key,
            target_path=mapping.target.template_path,
            semantic_id=mapping.target.semantic_id.primary_value,
            manufacturer=manufacturer,
            domain=domain,
            product_family=product_family,
            llm_review_summary=(mapping.llm_review.conclusion if mapping.llm_review else None),
            human_comments=comments,
            confirmations=(existing.confirmations if existing else 0)
            + (decision in {"approve", "keep"}),
            corrections=(existing.corrections if existing else 0)
            + (decision in {"correct", "change_target"}),
            rejections=(existing.rejections if existing else 0) + (decision == "reject"),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            status=(
                status
                if status is MappingKnowledgeStatus.TRUSTED
                else (existing.status if existing else status)
            ),
        )
        self._execute(
            "INSERT INTO mapping_knowledge(id, payload) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (entry.id, entry.model_dump_json()),
        )
        return entry

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
        for _ in range(3):
            try:
                with self._connect() as db:
                    if self._database_url is None:
                        db.execute("BEGIN IMMEDIATE")
                    else:
                        db.execute(
                            "SELECT id FROM products WHERE id=? FOR UPDATE",
                            (product_id,),
                        )
                    row = db.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM dpp_versions WHERE product_id=?",
                        (product_id,),
                    ).fetchone()
                    if row is None:  # pragma: no cover - aggregate queries return one row
                        raise RuntimeError("database did not return a DPP version")
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
                    db.execute(
                        "INSERT INTO dpp_versions"
                        "(id, product_id, run_id, version, payload, created_at) "
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
            except Exception as error:
                if not self._is_unique_violation(error):
                    raise
        raise RuntimeError("could not allocate a unique DPP version after three attempts")

    def list_dpp_versions(
        self,
        product_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> tuple[DppVersion, ...]:
        return self._many(
            DppVersion,
            "SELECT dpp_versions.payload FROM dpp_versions "
            "JOIN runs ON runs.id=dpp_versions.run_id "
            "JOIN threads ON threads.id=runs.thread_id "
            "WHERE dpp_versions.product_id=? AND threads.user_id=? "
            "ORDER BY dpp_versions.version DESC",
            (product_id, user_id),
        )

    def latest_successful_dpp(
        self,
        product_id: str,
        *,
        user_id: str = LOCAL_USER_ID,
    ) -> DppVersion | None:
        return next(
            (
                item
                for item in self.list_dpp_versions(product_id, user_id=user_id)
                if item.deployable
            ),
            None,
        )

    def create_background_job(
        self,
        *,
        user_id: str,
        thread_id: str,
        product_id: str,
        run_id: str,
        job_type: str = "deep_crawl",
        metadata: dict[str, Any] | None = None,
    ) -> BackgroundJob:
        """Create one idempotent job per run/type and return an existing retry-safe record."""

        if self.get_thread(thread_id, user_id=user_id) is None:
            raise PermissionError("unknown thread")
        if not self.user_owns_product(user_id, product_id):
            raise PermissionError("unknown product")
        job = BackgroundJob(
            id=_new_id("job"),
            user_id=user_id,
            thread_id=thread_id,
            product_id=product_id,
            run_id=run_id,
            job_type=job_type,
            metadata=metadata or {},
        )
        self._execute(
            "INSERT INTO background_jobs"
            "(id,user_id,thread_id,product_id,run_id,job_type,status,payload,"
            "created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,job_type) DO NOTHING",
            (
                job.id,
                user_id,
                thread_id,
                product_id,
                run_id,
                job_type,
                job.status.value,
                job.model_dump_json(),
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        existing = self._one(
            BackgroundJob,
            "SELECT payload FROM background_jobs WHERE run_id=? AND job_type=? AND user_id=?",
            (run_id, job_type, user_id),
        )
        return self._require(existing, job.id)

    def get_background_job(self, job_id: str, *, user_id: str) -> BackgroundJob | None:
        return self._one(
            BackgroundJob,
            "SELECT payload FROM background_jobs WHERE id=? AND user_id=?",
            (job_id, user_id),
        )

    def get_background_job_internal(self, job_id: str) -> BackgroundJob | None:
        """Resolve worker-owned job identity after service authentication succeeds."""

        return self._one(
            BackgroundJob,
            "SELECT payload FROM background_jobs WHERE id=?",
            (job_id,),
        )

    def list_background_jobs(
        self,
        *,
        user_id: str,
        thread_id: str | None = None,
    ) -> tuple[BackgroundJob, ...]:
        if thread_id is not None:
            return self._many(
                BackgroundJob,
                "SELECT payload FROM background_jobs WHERE user_id=? AND thread_id=? "
                "ORDER BY created_at",
                (user_id, thread_id),
            )
        return self._many(
            BackgroundJob,
            "SELECT payload FROM background_jobs WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )

    def next_queued_background_job(self) -> BackgroundJob | None:
        """Return the oldest queued worker job for an atomic worker claim."""

        return self._one(
            BackgroundJob,
            "SELECT payload FROM background_jobs WHERE status=? ORDER BY created_at LIMIT 1",
            (BackgroundJobStatus.QUEUED.value,),
        )

    def claim_background_job(self, job_id: str, *, user_id: str) -> BackgroundJob | None:
        """Atomically move a queued/failed job to running; duplicate workers receive ``None``."""

        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM background_jobs WHERE id=? AND user_id=?",
                (job_id, user_id),
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = BackgroundJob.model_validate_json(row[0])
            if job.status not in {BackgroundJobStatus.QUEUED, BackgroundJobStatus.FAILED}:
                return None
            claimed = job.model_copy(
                update={
                    "status": BackgroundJobStatus.RUNNING,
                    "started_at": job.started_at or now,
                    "updated_at": now,
                    "error": None,
                }
            )
            cursor = db.execute(
                "UPDATE background_jobs SET status=?,payload=?,updated_at=? "
                "WHERE id=? AND user_id=? AND status IN (?,?)",
                (
                    claimed.status.value,
                    claimed.model_dump_json(),
                    now.isoformat(),
                    job_id,
                    user_id,
                    BackgroundJobStatus.QUEUED.value,
                    BackgroundJobStatus.FAILED.value,
                ),
            )
            return claimed if cursor.rowcount == 1 else None

    def update_background_job(
        self,
        job_id: str,
        *,
        user_id: str,
        metadata: dict[str, Any],
    ) -> BackgroundJob:
        """Persist monotonic progress metadata while a durable worker is running."""

        job = self._require(self.get_background_job(job_id, user_id=user_id), job_id)
        now = _now()
        updated = job.model_copy(
            update={"updated_at": now, "metadata": {**job.metadata, **metadata}}
        )
        self._execute(
            "UPDATE background_jobs SET payload=?,updated_at=? WHERE id=? AND user_id=?",
            (updated.model_dump_json(), now.isoformat(), job_id, user_id),
        )
        return updated

    def requeue_background_job(
        self,
        job_id: str,
        *,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> BackgroundJob:
        """Checkpoint one completed worker batch and make the same job claimable again."""

        job = self._require(self.get_background_job(job_id, user_id=user_id), job_id)
        now = _now()
        queued = job.model_copy(
            update={
                "status": BackgroundJobStatus.QUEUED,
                "updated_at": now,
                "error": None,
                "metadata": {**job.metadata, **(metadata or {})},
            }
        )
        self._execute(
            "UPDATE background_jobs SET status=?,payload=?,updated_at=? WHERE id=? AND user_id=?",
            (
                queued.status.value,
                queued.model_dump_json(),
                now.isoformat(),
                job_id,
                user_id,
            ),
        )
        return queued

    def finish_background_job(
        self,
        job_id: str,
        *,
        user_id: str,
        status: BackgroundJobStatus,
        metadata: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> BackgroundJob:
        if status not in {
            BackgroundJobStatus.COMPLETED,
            BackgroundJobStatus.FAILED,
            BackgroundJobStatus.CANCELLED,
        }:
            raise ValueError("background job can only finish in a terminal state")
        job = self._require(self.get_background_job(job_id, user_id=user_id), job_id)
        now = _now()
        finished = job.model_copy(
            update={
                "status": status,
                "updated_at": now,
                "completed_at": now,
                "error": error,
                "metadata": {**job.metadata, **(metadata or {})},
            }
        )
        self._execute(
            "UPDATE background_jobs SET status=?,payload=?,updated_at=? WHERE id=? AND user_id=?",
            (
                status.value,
                finished.model_dump_json(),
                now.isoformat(),
                job_id,
                user_id,
            ),
        )
        return finished

    def _raw_connect(self) -> _Connection:
        if self._database_url is not None:
            try:
                import psycopg
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
                    try:
                        db.execute("PRAGMA journal_mode=WAL")
                    except sqlite3.OperationalError as error:
                        # Another process may be enabling WAL for this database.
                        if "database is locked" not in str(error):
                            raise
                db.executescript(SCHEMA)
                self._apply_migrations(db)
                self._backfill_legacy_local_ownership(db)
            self._ready = True

    @staticmethod
    def _apply_migrations(db: _Connection) -> None:
        """Apply packaged SQL migrations exactly once on SQLite or PostgreSQL."""

        root = importlib.resources.files("mia_dpp.persistence").joinpath("migrations")
        for migration in sorted(
            (item for item in root.iterdir() if item.name.endswith(".sql")),
            key=lambda item: item.name,
        ):
            if db.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                (migration.name,),
            ).fetchone():
                continue
            db.executescript(migration.read_text(encoding="utf-8"))
            db.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?) "
                "ON CONFLICT(version) DO NOTHING",
                (migration.name, _now().isoformat()),
            )

    @staticmethod
    def _backfill_legacy_local_ownership(db: _Connection) -> None:
        """Keep pre-auth development history reachable only by the explicit local account."""

        for product_id, updated_at in db.execute("SELECT id,updated_at FROM products").fetchall():
            db.execute(
                "INSERT INTO user_products(user_id,product_id,created_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id,product_id) DO NOTHING",
                (LOCAL_USER_ID, product_id, updated_at),
            )
        for thread_id, started_at in db.execute(
            "SELECT thread_id,MIN(started_at) FROM runs GROUP BY thread_id"
        ).fetchall():
            thread = ThreadRecord(
                id=str(thread_id),
                user_id=LOCAL_USER_ID,
                created_at=datetime.fromisoformat(str(started_at)),
                updated_at=datetime.fromisoformat(str(started_at)),
            )
            db.execute(
                "INSERT INTO threads(id,user_id,payload,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    thread.id,
                    thread.user_id,
                    thread.model_dump_json(),
                    thread.updated_at.isoformat(),
                ),
            )

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        with self._connect() as db:
            return db.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._connect() as db:
            return db.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._connect() as db:
            return db.execute(sql, params).rowcount

    def _one(self, model: type[ModelT], sql: str, params: tuple[Any, ...] = ()) -> ModelT | None:
        row = self._fetchone(sql, params)
        return model.model_validate_json(row[0]) if row else None

    def _many(
        self, model: type[ModelT], sql: str, params: tuple[Any, ...] = ()
    ) -> tuple[ModelT, ...]:
        return tuple(model.model_validate_json(row[0]) for row in self._fetchall(sql, params))

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _is_unique_violation(error: Exception) -> bool:
        if isinstance(error, sqlite3.IntegrityError):
            return "UNIQUE constraint failed" in str(error)
        return getattr(error, "sqlstate", None) == "23505"

    @staticmethod
    def _require(value: ModelT | None, key: str) -> ModelT:
        if value is None:
            raise KeyError(key)
        return value
