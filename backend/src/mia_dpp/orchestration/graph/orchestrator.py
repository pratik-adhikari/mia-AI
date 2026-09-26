"""LangGraph implementation of the application orchestration contract."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from langgraph.types import Command
from langgraph_sdk import get_client

from mia_dpp.config import Settings
from mia_dpp.orchestration.base import OrchestrationRunRequest, OrchestrationSnapshot
from mia_dpp.runtime.checkpoints import open_checkpointer
from mia_dpp.runtime.services import ServiceContainer
from mia_dpp.workflow.graph import create_graph
from mia_dpp.workflow.state import reset_product_state


@dataclass
class _GraphDebugSession:
    run_id: str
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    listeners: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)


class GraphOrchestrator:
    """Own all LangGraph/Agent-Server execution mechanics for graph-v1."""

    def __init__(
        self,
        settings: Settings,
        services: ServiceContainer,
        *,
        graph: Any | None = None,
        agent_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._services = services
        self._graph = graph
        self._checkpoint_cm: Any | None = None
        self._agent_client = agent_client
        self._debug_sessions: dict[tuple[str, str], _GraphDebugSession] = {}
        self._backends: dict[tuple[str, str, int], str] = {}

    @property
    def external_execution_enabled(self) -> bool:
        return bool(
            self._settings.local_mode
            and not self._settings.vercel_environment
            and self._settings.agent_server_url
        )

    @property
    def debug_enabled(self) -> bool:
        return self.external_execution_enabled

    async def snapshot(
        self,
        thread_id: str,
        user_id: str,
        *,
        create_if_missing: bool = False,
    ) -> OrchestrationSnapshot:
        key = self._execution_key(thread_id, user_id)
        if self.external_execution_enabled:
            raw = await self._agent_server_snapshot(thread_id, user_id)
            if raw is not None:
                self._backends[key] = "remote"
                return self._normalize_snapshot(raw)
            if create_if_missing:
                await self._agent_server_client().threads.create(
                    thread_id=self._agent_server_thread_id(thread_id, user_id),
                    if_exists="do_nothing",
                )
                self._backends[key] = "remote"
                return OrchestrationSnapshot(values={}, status="idle")
            # Existing local SQLite threads may predate Agent Server ownership.
            self._backends[key] = "local"

        graph = await self._ensure_graph()
        raw = await graph.aget_state(self._config(thread_id, user_id), subgraphs=True)
        return self._normalize_snapshot(raw)

    async def run(
        self,
        request: OrchestrationRunRequest,
    ) -> dict[str, Any]:
        run_input = self._graph_input(request)
        if self._backend(request.thread_id, request.user_id) == "remote":
            return await self._run_agent_server(
                request.thread_id,
                request.user_id,
                run_input=run_input,
            )
        graph = await self._ensure_graph()
        config = self._config(request.thread_id, request.user_id)
        await graph.ainvoke(run_input, config=config, context=self._services)
        raw = await graph.aget_state(config, subgraphs=True)
        return self._snapshot_values(raw)

    async def resume(
        self,
        thread_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self._backend(thread_id, user_id) == "remote":
            return await self._run_agent_server(
                thread_id,
                user_id,
                command={"resume": payload},
            )
        graph = await self._ensure_graph()
        config = self._config(thread_id, user_id)
        await graph.ainvoke(
            Command(resume=payload),
            config=config,
            context=self._services,
        )
        raw = await graph.aget_state(config, subgraphs=True)
        return self._snapshot_values(raw)

    async def execution_is_active(self, thread_id: str, user_id: str) -> bool:
        if not self.external_execution_enabled:
            return False
        remote_thread_id = self._agent_server_thread_id(thread_id, user_id)
        try:
            for status in ("running", "pending"):
                if await self._agent_server_client().runs.list(
                    remote_thread_id,
                    status=status,
                    limit=1,
                ):
                    return True
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
        return False

    async def close(self) -> None:
        if self._agent_client is not None:
            await self._agent_client.aclose()
            self._agent_client = None
        if self._checkpoint_cm is not None:
            await self._checkpoint_cm.__aexit__(None, None, None)
            self._checkpoint_cm = None
            self._graph = None

    async def debug_stream(
        self,
        thread_id: str | None,
        *,
        user_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.debug_enabled:
            raise RuntimeError("Live workflow debugging is only enabled in the local Agent Server.")
        graph = await self._agent_server_client().assistants.get_graph("mia", xray=1)
        nodes = [
            {
                "id": str(node.get("id", "")),
                "name": (
                    node["data"].get("name", "")
                    if isinstance(node.get("data"), Mapping)
                    else str(node.get("data") or "")
                ),
                "type": node.get("type"),
            }
            for node in graph.get("nodes", [])
        ]
        edges = [
            {
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "conditional": bool(edge.get("conditional", False)),
                "label": str(edge.get("data", "")) if edge.get("data") else "",
            }
            for edge in graph.get("edges", [])
        ]
        yield {"event": "topology", "data": {"nodes": nodes, "edges": edges}}

        if thread_id is None:
            yield {"event": "run", "data": {"status": "idle"}}
            return

        session = self._debug_sessions.get((user_id, thread_id))
        if session is None:
            raw = await self._agent_server_snapshot(thread_id, user_id)
            if raw is None:
                yield {"event": "run", "data": {"status": "idle"}}
                return
            status = self._snapshot_status(raw)
            metadata = raw.get("metadata", {}) if isinstance(raw, Mapping) else {}
            yield {
                "event": "run",
                "data": {
                    "runId": metadata.get("run_id") if isinstance(metadata, Mapping) else None,
                    "status": status,
                },
            }
            tasks = raw.get("tasks", ()) if isinstance(raw, Mapping) else getattr(raw, "tasks", ())
            for task in tasks:
                task_data = task if isinstance(task, Mapping) else {}
                name = task_data.get("name")
                if not isinstance(name, str):
                    continue
                path = task_data.get("path", ())
                path_items = (
                    [item for item in path if isinstance(item, str)]
                    if isinstance(path, (list, tuple))
                    else []
                )
                namespace_items = [item for item in path_items if item != "__pregel_pull"][:-1]
                task_status = self._task_status(task_data)
                if task_status == "running" and status != "running":
                    task_status = status
                yield {
                    "event": "node",
                    "data": {
                        "nodeId": (
                            f"{':'.join(namespace_items)}:{name}"
                            if namespace_items
                            else name
                        ),
                        "nodeName": name,
                        "namespace": ":".join(namespace_items),
                        "status": task_status,
                        "timestamp": None,
                    },
                }
            if not tasks:
                next_nodes = (
                    raw.get("next", ())
                    if isinstance(raw, Mapping)
                    else getattr(raw, "next", ())
                )
                for node_name in next_nodes:
                    if isinstance(node_name, str):
                        yield {
                            "event": "node",
                            "data": {
                                "nodeId": node_name,
                                "nodeName": node_name,
                                "namespace": "",
                                "status": "waiting" if status != "running" else "running",
                                "timestamp": None,
                            },
                        }
            return

        listener: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        session.listeners.add(listener)
        history = list(session.events)
        try:
            yield {
                "event": "run",
                "data": {"runId": session.run_id, "status": session.status},
            }
            for history_event in history:
                yield history_event
            while session.status == "running":
                incoming_event = await listener.get()
                if incoming_event is None:
                    break
                yield incoming_event
        finally:
            session.listeners.discard(listener)

    async def _ensure_graph(self) -> Any:
        if self._graph is None:
            self._checkpoint_cm = open_checkpointer(self._settings)
            checkpointer = await self._checkpoint_cm.__aenter__()
            self._graph = create_graph(checkpointer)
        return self._graph

    @staticmethod
    def _graph_input(request: OrchestrationRunRequest) -> dict[str, Any]:
        if request.seed is not None:
            seed = request.seed
            update: dict[str, Any] = {
                **reset_product_state(product_url=seed.product_url),
                "thread_id": request.thread_id,
                "user_id": request.user_id,
                "user_message": request.user_message,
                "product_id": seed.product_id,
                "run_id": seed.run_id,
                "workflow_generation": seed.workflow_generation,
                "source_generation": seed.source_generation,
                "refresh_requested": request.refresh_requested,
                "reuse_mode": seed.reuse_mode,
                "reuse_prior_work": seed.reuse_prior_work,
                "seeded_from_run_id": seed.seeded_from_run_id,
                "evidence_artifact_id": seed.evidence_artifact_id,
                "reviewed_mapping_artifact_id": seed.reviewed_mapping_artifact_id,
                "product_snapshot_version": seed.product_snapshot_version,
                "discovery_history_json": seed.discovery_history_json,
                "target_submodels": seed.target_submodels,
                "max_research_attempts": seed.max_research_attempts,
                "research_attempts": 0,
                "status": "running",
            }
            return update

        update = {
            "thread_id": request.thread_id,
            "user_id": request.user_id,
            "user_message": request.user_message,
            "refresh_requested": request.refresh_requested,
        }
        if request.initialize:
            update.update(
                {
                    "discovery_history_json": "[]",
                    "target_submodels": ("digital_nameplate", "technical_data"),
                    "max_research_attempts": 2,
                    "research_attempts": 0,
                    "status": "running",
                }
            )
        return update

    def _backend(self, thread_id: str, user_id: str) -> str:
        key = self._execution_key(thread_id, user_id)
        existing = self._backends.get(key)
        if existing is not None:
            return existing
        backend = "remote" if self.external_execution_enabled else "local"
        self._backends[key] = backend
        return backend

    def _execution_key(self, thread_id: str, user_id: str) -> tuple[str, str, int]:
        thread = self._services.catalogue.get_thread(thread_id, user_id=user_id)
        generation = thread.workflow_generation if thread is not None else 0
        return user_id, thread_id, generation

    def _config(self, thread_id: str, user_id: str) -> dict[str, dict[str, str]]:
        _, _, generation = self._execution_key(thread_id, user_id)
        return {
            "configurable": {
                "thread_id": f"{user_id}:{thread_id}:generation:{generation}"
            }
        }

    def _agent_server_client(self) -> Any:
        if self._agent_client is None:
            if not self._settings.agent_server_url:
                raise RuntimeError("MIA_AGENT_SERVER_URL is not configured.")
            self._agent_client = get_client(url=self._settings.agent_server_url)
        return self._agent_client

    def _agent_server_thread_id(self, thread_id: str, user_id: str) -> str:
        _, _, generation = self._execution_key(thread_id, user_id)
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"mia-dpp:{user_id}:{thread_id}:generation:{generation}",
            )
        )

    async def _agent_server_snapshot(self, thread_id: str, user_id: str) -> Any | None:
        if not self.external_execution_enabled:
            return None
        try:
            return await self._agent_server_client().threads.get_state(
                self._agent_server_thread_id(thread_id, user_id),
                subgraphs=True,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise

    async def _run_agent_server(
        self,
        thread_id: str,
        user_id: str,
        *,
        run_input: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._agent_server_client()
        key = (user_id, thread_id)
        session: _GraphDebugSession | None = None

        def register_run(metadata: Mapping[str, Any]) -> None:
            nonlocal session
            session = _GraphDebugSession(run_id=str(metadata["run_id"]))
            self._debug_sessions[key] = session
            self._publish_debug_event(
                session,
                "run",
                {"runId": session.run_id, "status": "running"},
            )

        try:
            async for part in client.runs.stream(
                thread_id=self._agent_server_thread_id(thread_id, user_id),
                assistant_id="mia",
                input=run_input,
                command=command,
                stream_mode=["debug"],
                stream_subgraphs=True,
                on_disconnect="continue",
                if_not_exists="create",
                on_run_created=register_run,
            ):
                if session is None and part.event == "metadata":
                    run_id = part.data.get("run_id")
                    if isinstance(run_id, str):
                        register_run({"run_id": run_id})
                self._record_debug_part(session, part.event, part.data)
            raw = await self._agent_server_snapshot(thread_id, user_id)
            if raw is None:
                raise RuntimeError("LangGraph Agent Server lost the workflow thread.")
            if session is not None:
                status = self._snapshot_status(raw)
                if status == "failed":
                    raise RuntimeError(
                        "LangGraph Agent Server workflow failed. Open Debug to see the failed node."
                    )
                session.status = status
                self._publish_debug_event(
                    session,
                    "run",
                    {"runId": session.run_id, "status": session.status},
                )
            return self._snapshot_values(raw)
        except Exception as error:
            if session is not None:
                session.status = "failed"
                self._publish_debug_event(
                    session,
                    "run",
                    {
                        "runId": session.run_id,
                        "status": "failed",
                        "errorType": type(error).__name__,
                    },
                )
            raise

    @classmethod
    def _normalize_snapshot(cls, raw: Any) -> OrchestrationSnapshot:
        values = cls._snapshot_values(raw)
        status = cls._snapshot_status(raw) if values or cls._snapshot_interrupted(raw) else "idle"
        return OrchestrationSnapshot(
            values=values,
            status=status,
            interrupted=cls._snapshot_interrupted(raw),
        )

    @classmethod
    def _snapshot_values(cls, snapshot: Any) -> dict[str, Any]:
        values = snapshot.get("values", {}) if isinstance(snapshot, Mapping) else snapshot.values
        result = dict(values) if isinstance(values, Mapping) else {}
        tasks = snapshot.get("tasks", ()) if isinstance(snapshot, Mapping) else snapshot.tasks
        for task in tasks:
            interrupts = (
                task.get("interrupts", ())
                if isinstance(task, Mapping)
                else task.interrupts
            )
            if not interrupts:
                continue
            nested = task.get("state") if isinstance(task, Mapping) else task.state
            if nested is not None:
                result.update(cls._snapshot_values(nested))
        return result

    @classmethod
    def _snapshot_interrupted(cls, snapshot: Any) -> bool:
        state_interrupts = (
            snapshot.get("interrupts", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "interrupts", ())
        )
        if state_interrupts:
            return True
        tasks = snapshot.get("tasks", ()) if isinstance(snapshot, Mapping) else getattr(snapshot, "tasks", ())
        for task in tasks:
            interrupts = task.get("interrupts", ()) if isinstance(task, Mapping) else getattr(task, "interrupts", ())
            if interrupts:
                return True
            nested = task.get("state") if isinstance(task, Mapping) else getattr(task, "state", None)
            if nested is not None and cls._snapshot_interrupted(nested):
                return True
        return False

    @classmethod
    def _snapshot_status(cls, snapshot: Any) -> str:
        next_nodes = (
            snapshot.get("next", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "next", ())
        )
        tasks = (
            snapshot.get("tasks", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "tasks", ())
        )
        if any(cls._task_status(task) == "failed" for task in tasks):
            return "failed"
        state_interrupts = (
            snapshot.get("interrupts", ())
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "interrupts", ())
        )
        if (
            next_nodes
            or state_interrupts
            or any(cls._task_status(task) == "waiting" for task in tasks)
        ):
            return "waiting"
        return "completed"

    @staticmethod
    def _task_status(task: Any) -> str:
        error = task.get("error") if isinstance(task, Mapping) else getattr(task, "error", None)
        interrupts = (
            task.get("interrupts", ())
            if isinstance(task, Mapping)
            else getattr(task, "interrupts", ())
        )
        if error:
            return "failed"
        if interrupts:
            return "waiting"
        return "running"

    @staticmethod
    def _record_debug_part(
        session: _GraphDebugSession | None,
        event_name: str,
        data: Mapping[str, Any],
    ) -> None:
        if session is None or not event_name.startswith("debug"):
            return
        event_type = data.get("type")
        payload = data.get("payload")
        if event_type not in {"task", "task_result"} or not isinstance(payload, Mapping):
            return
        node_name = payload.get("name")
        if not isinstance(node_name, str):
            return
        namespaces = [item for item in event_name.split("|")[1:] if item]
        namespace = ":".join(namespaces)
        node_id = f"{namespace}:{node_name}" if namespace else node_name
        if event_type == "task":
            status = "running"
        elif payload.get("interrupts"):
            status = "waiting"
        else:
            status = "failed" if payload.get("error") else "completed"
        timestamp = data.get("timestamp")
        GraphOrchestrator._publish_debug_event(
            session,
            "node",
            {
                "nodeId": node_id,
                "nodeName": node_name,
                "namespace": namespace,
                "status": status,
                "timestamp": timestamp if isinstance(timestamp, str) else None,
            },
        )

    @staticmethod
    def _publish_debug_event(
        session: _GraphDebugSession,
        event_name: str,
        data: dict[str, Any],
    ) -> None:
        event = {"event": event_name, "data": data}
        session.events.append(event)
        for listener in tuple(session.listeners):
            listener.put_nowait(event)
