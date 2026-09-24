"""Failure and interruption persistence at the LangGraph application boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mia_dpp.agent.models import AgentRequest, AgentResponse, AgentStatus
from mia_dpp.agents.conversation import ConversationAction, ConversationTurn
from mia_dpp.domain.product import RunStatus
from mia_dpp.mia import Mia
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.services.product_query import WorkStatusView


class _Store:
    def list_events(self, thread_id: str, *, user_id: str):
        return ()

    def list_artifacts(self, thread_id: str, *, user_id: str):
        return ()


class _ResponseView:
    def build(self, state: dict[str, Any], *, trace_offset: int = 0) -> AgentResponse:
        return AgentResponse(
            thread_id=str(state["thread_id"]),
            reply="Waiting for the pending review.",
            status=AgentStatus.AWAITING_REVIEW,
            decision_summary="Review remains pending.",
        )


class _Snapshot:
    def __init__(self, values: dict[str, Any], *, interrupted: bool = False) -> None:
        self.values = values
        interrupt = SimpleNamespace(interrupts=(object(),))
        self.tasks = (interrupt,) if interrupted else ()


def _mia(catalogue: ProductCatalogue, graph: object) -> Mia:
    mia = object.__new__(Mia)
    mia.context = SimpleNamespace(catalogue=catalogue, semantic_mapper=object())
    mia.store = _Store()
    mia._response_view = _ResponseView()
    mia._graph = graph
    mia._checkpoint_cm = None
    mia._agent_client = None
    mia.settings = SimpleNamespace(
        local_mode=False,
        vercel_environment=False,
        agent_server_url=None,
    )
    return mia


def test_graph_failure_is_recorded_on_the_active_attempt(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/failing")
    run = catalogue.start_run(product.id, "thread-failing")

    class Graph:
        async def aget_state(self, config):
            return _Snapshot({})

        async def astream(self, update, **kwargs):
            yield "tasks", {"id": "task-1", "name": "fixture_node", "input": {}}
            raise RuntimeError("fixture workflow failure")

    mia = _mia(catalogue, Graph())
    with pytest.raises(RuntimeError, match="fixture workflow failure"):
        asyncio.run(mia.message(AgentRequest(thread_id="thread-failing", message="Create a DPP")))

    persisted = catalogue.get_run(run.id)
    assert persisted is not None
    assert persisted.status is RunStatus.FAILED
    assert persisted.error == "fixture workflow failure"
    assert catalogue.list_messages("thread-failing")[0].run_id == run.id
    assert catalogue.list_events(run.id)[-1].event_type == "workflow.failed"


def test_message_during_interrupt_is_persisted_without_advancing_graph(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/review")
    run = catalogue.start_run(product.id, "thread-review")
    catalogue.set_run_status(run.id, RunStatus.AWAITING_HUMAN)

    class Graph:
        invoked = False

        async def aget_state(self, config):
            return _Snapshot(
                {
                    "thread_id": "thread-review",
                    "product_id": product.id,
                    "run_id": run.id,
                    "review_required": True,
                },
                interrupted=True,
            )

        async def ainvoke(self, update, **kwargs):
            self.invoked = True
            raise AssertionError("an interrupted graph must be resumed through the review API")

    graph = Graph()
    response = asyncio.run(
        _mia(catalogue, graph).message(
            AgentRequest(thread_id="thread-review", message="Are you still waiting?")
        )
    )

    assert response.status is AgentStatus.AWAITING_REVIEW
    assert graph.invoked is False
    messages = catalogue.list_messages("thread-review")
    assert [item.role.value for item in messages] == ["user", "assistant"]
    assert all(item.run_id == run.id for item in messages)



def test_refresh_restarts_active_product_in_same_chat(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/refresh-same-chat")
    run = catalogue.start_run(product.id, "thread-refresh-same-chat")
    expired = run.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), run.id),
    )

    class Graph:
        invocations: list[dict[str, Any]] = []

        async def aget_state(self, config):
            return _Snapshot(
                {
                    "thread_id": "thread-refresh-same-chat",
                    "product_id": product.id,
                    "run_id": run.id,
                    "product_url": product.canonical_url,
                    "status": "running",
                }
            )

        async def ainvoke(self, update, **kwargs):
            self.invocations.append(update)
            return {
                **update,
                "thread_id": "thread-refresh-same-chat",
                "product_id": product.id,
                "run_id": update["run_id"],
                "status": "running",
                "reply": "Refresh restarted.",
                "decision_summary": "Refresh restarted.",
            }

    graph = Graph()
    response = asyncio.run(
        _mia(catalogue, graph).message(
            AgentRequest(
                thread_id="new-thread-that-must-not-survive",
                message="Import product website: https://example.com/refresh-same-chat",
                refresh_requested=True,
            )
        )
    )

    assert response.thread_id == "thread-refresh-same-chat"
    assert catalogue.get_run(run.id).status is RunStatus.INCOMPLETE
    replacement = catalogue.latest_active_run(product.id)
    assert replacement is not None
    assert replacement.id != run.id
    assert replacement.thread_id == "thread-refresh-same-chat"
    assert replacement.refresh_requested is True
    assert catalogue.get_thread(
        "thread-refresh-same-chat",
        user_id="local-development",
    ).workflow_generation == 1
    assert graph.invocations[-1]["refresh_requested"] is True


def test_live_running_run_is_joined_without_restart(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/live")
    run = catalogue.start_run(product.id, "thread-live")

    class Graph:
        invoked = False

        async def aget_state(self, config):
            return _Snapshot(
                {
                    "thread_id": "thread-live",
                    "product_id": product.id,
                    "run_id": run.id,
                    "product_url": product.canonical_url,
                    "status": "running",
                }
            )

        async def ainvoke(self, update, **kwargs):
            self.invoked = True
            raise AssertionError("a live leased run must not be restarted")

    graph = Graph()
    response = asyncio.run(
        _mia(catalogue, graph).message(
            AgentRequest(
                thread_id="another-thread",
                message="Import product website: https://example.com/live",
            )
        )
    )

    assert response.thread_id == "thread-live"
    assert catalogue.get_run(run.id).status is RunStatus.RUNNING
    assert catalogue.get_thread(
        "thread-live",
        user_id="local-development",
    ).workflow_generation == 0
    assert graph.invoked is False


def test_zombie_running_run_restarts_from_saved_work_in_same_chat(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/zombie")
    run = catalogue.start_run(product.id, "thread-zombie")
    expired = run.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), run.id),
    )

    class Graph:
        async def aget_state(self, config):
            # The execution lease is expired, so this RUNNING record is recoverable.
            return _Snapshot(
                {
                    "thread_id": "thread-zombie",
                    "product_id": product.id,
                    "run_id": run.id,
                    "product_url": product.canonical_url,
                    "status": "running",
                }
            )

        async def ainvoke(self, update, **kwargs):
            return {
                **update,
                "thread_id": "thread-zombie",
                "product_id": product.id,
                "run_id": update["run_id"],
                "status": "running",
                "reply": "Recovered saved work.",
                "decision_summary": "Recovered saved work.",
            }

    response = asyncio.run(
        _mia(catalogue, Graph()).message(
            AgentRequest(
                thread_id="another-thread",
                message="Import product website: https://example.com/zombie",
            )
        )
    )

    assert response.thread_id == "thread-zombie"
    assert catalogue.get_run(run.id).status is RunStatus.INCOMPLETE
    replacement = catalogue.latest_active_run(product.id)
    assert replacement is not None
    assert replacement.id != run.id
    assert replacement.refresh_requested is False
    assert catalogue.get_thread(
        "thread-zombie",
        user_id="local-development",
    ).workflow_generation == 1


def test_awaiting_human_run_is_not_restarted_as_zombie(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/waiting-human")
    run = catalogue.start_run(product.id, "thread-waiting-human")
    catalogue.set_run_status(run.id, RunStatus.AWAITING_HUMAN)

    class Graph:
        invoked = False

        async def aget_state(self, config):
            return _Snapshot(
                {
                    "thread_id": "thread-waiting-human",
                    "product_id": product.id,
                    "run_id": run.id,
                    "review_required": True,
                },
                interrupted=True,
            )

        async def ainvoke(self, update, **kwargs):
            self.invoked = True
            raise AssertionError("human review checkpoint must remain paused")

    graph = Graph()
    response = asyncio.run(
        _mia(catalogue, graph).message(
            AgentRequest(
                thread_id="other-thread",
                message="Import product website: https://example.com/waiting-human",
            )
        )
    )

    assert response.thread_id == "thread-waiting-human"
    assert catalogue.get_run(run.id).status is RunStatus.AWAITING_HUMAN
    assert catalogue.get_thread(
        "thread-waiting-human",
        user_id="local-development",
    ).workflow_generation == 0
    assert graph.invoked is False



def test_refresh_during_live_run_is_queued_without_terminating_executor(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/live-refresh")
    run = catalogue.start_run(product.id, "thread-live-refresh")

    class Graph:
        invoked = False

        async def aget_state(self, config):
            return _Snapshot(
                {
                    "thread_id": "thread-live-refresh",
                    "product_id": product.id,
                    "run_id": run.id,
                    "product_url": product.canonical_url,
                    "status": "running",
                }
            )

        async def ainvoke(self, update, **kwargs):
            self.invoked = True
            raise AssertionError("refresh must wait for the live execution to return")

    graph = Graph()
    response = asyncio.run(
        _mia(catalogue, graph).message(
            AgentRequest(
                thread_id="another-thread",
                message="Import product website: https://example.com/live-refresh",
                refresh_requested=True,
            )
        )
    )

    persisted = catalogue.get_run(run.id)
    thread = catalogue.get_thread("thread-live-refresh", user_id="local-development")
    assert response.thread_id == "thread-live-refresh"
    assert persisted is not None and persisted.status is RunStatus.RUNNING
    assert thread is not None and thread.pending_refresh_requested is True
    assert thread.workflow_generation == 0
    assert graph.invoked is False



def test_stale_executor_failure_does_not_fail_replacement_run(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/fenced-failure")
    old = catalogue.start_run(product.id, "thread-fenced-failure")
    expired = old.model_copy(
        update={"execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    catalogue._execute(
        "UPDATE runs SET payload=? WHERE id=?",
        (expired.model_dump_json(), old.id),
    )
    replacement = catalogue.claim_product_restart(
        user_id="local-development",
        product_id=product.id,
        expected_run_id=old.id,
        expected_generation=0,
        reason="fixture recovery",
        refresh_requested=False,
        require_expired_lease=True,
    )

    mia = _mia(catalogue, object())
    mia._record_failure(
        old.thread_id,
        RuntimeError("stale executor failed"),
        user_id="local-development",
        run_id=old.id,
    )

    assert catalogue.get_run(old.id).status is RunStatus.INCOMPLETE
    assert catalogue.get_run(replacement.id).status is RunStatus.RUNNING



def test_initial_message_unknown_failure_never_fails_replacement_run(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/initial-fence")

    class Graph:
        async def aget_state(self, config):
            return _Snapshot({})

        async def ainvoke(self, update, **kwargs):
            old = catalogue.start_run(product.id, "thread-initial-fence")
            expired = old.model_copy(
                update={
                    "execution_lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)
                }
            )
            catalogue._execute(
                "UPDATE runs SET payload=? WHERE id=?",
                (expired.model_dump_json(), old.id),
            )
            replacement = catalogue.claim_product_restart(
                user_id="local-development",
                product_id=product.id,
                expected_run_id=old.id,
                expected_generation=0,
                reason="concurrent recovery",
                refresh_requested=False,
                require_expired_lease=True,
            )
            self.replacement_id = replacement.id
            raise RuntimeError("stale initial invocation failed")

    graph = Graph()
    with pytest.raises(RuntimeError, match="stale initial invocation failed"):
        asyncio.run(
            _mia(catalogue, graph).message(
                AgentRequest(
                    thread_id="thread-initial-fence",
                    message="Create a DPP",
                )
            )
        )

    replacement = catalogue.get_run(graph.replacement_id)
    assert replacement is not None
    assert replacement.status is RunStatus.RUNNING
    messages = catalogue.list_messages("thread-initial-fence")
    assert messages[0].run_id is None



def test_general_chat_reply_does_not_advance_langgraph(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    catalogue.get_or_create_thread("thread-general-chat", "local-development")

    class Graph:
        invoked = False

        async def aget_state(self, config):
            raise AssertionError("general chat must not depend on checkpoint state")

        async def ainvoke(self, update, **kwargs):
            self.invoked = True
            raise AssertionError("general chat must not execute the workflow")

    class Conversation:
        async def run(self, message, *, thread_id, user_id, recent_messages=()):
            assert message == "What are you doing right now?"
            assert thread_id == "thread-general-chat"
            assert recent_messages[-1]["content"] == message
            return ConversationTurn(
                action=ConversationAction.REPLY,
                reply="There is no active product workflow yet.",
                decision_summary="Answered from durable work status.",
            )

    class Query:
        def work_status(self, thread_id, *, user_id):
            return WorkStatusView(thread_id=thread_id)

    graph = Graph()
    mia = _mia(catalogue, graph)
    mia.conversation = Conversation()
    mia.query = Query()

    response = asyncio.run(
        mia.message(
            AgentRequest(
                thread_id="thread-general-chat",
                message="What are you doing right now?",
            )
        )
    )

    assert response.reply == "There is no active product workflow yet."
    assert response.status is AgentStatus.AWAITING_INPUT
    assert graph.invoked is False
    assert [message.role.value for message in catalogue.list_messages("thread-general-chat")] == [
        "user",
        "assistant",
    ]



def test_transient_network_failure_is_recoverable_incomplete_run(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/transient")
    run = catalogue.start_run(product.id, "thread-transient")

    class Graph:
        async def aget_state(self, config):
            return _Snapshot(
                {
                    "thread_id": "thread-transient",
                    "product_id": product.id,
                    "run_id": run.id,
                    "product_url": product.canonical_url,
                    "status": "running",
                }
            )

        async def ainvoke(self, update, **kwargs):
            request = httpx.Request("GET", product.canonical_url)
            raise httpx.ConnectError("temporary upstream outage", request=request)

    mia = _mia(catalogue, Graph())
    with pytest.raises(httpx.ConnectError):
        asyncio.run(
            mia.message(
                AgentRequest(
                    thread_id="thread-transient",
                    message="Continue the product workflow",
                )
            )
        )

    persisted = catalogue.get_run(run.id)
    assert persisted is not None
    assert persisted.status is RunStatus.INCOMPLETE
    event = catalogue.list_events(run.id)[-1]
    assert event.event_type == "workflow.retryable_failure"
    assert event.metadata["retryable"] is True


def test_retry_work_creates_new_fenced_generation_from_failed_attempt(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/retry")
    failed = catalogue.start_run(product.id, "thread-retry")
    catalogue.finish_run(
        failed.id,
        RunStatus.INCOMPLETE,
        error="temporary upstream outage",
    )

    class Graph:
        invocations: list[dict[str, Any]] = []

        async def ainvoke(self, update, **kwargs):
            self.invocations.append(update)
            return {
                **update,
                "thread_id": "thread-retry",
                "product_id": product.id,
                "run_id": update["run_id"],
                "product_url": product.canonical_url,
                "status": "running",
                "reply": "Recovered product work.",
                "decision_summary": "Recovered product work.",
            }

    graph = Graph()
    mia = _mia(catalogue, graph)
    response = asyncio.run(mia.retry_work("thread-retry"))

    replacement = catalogue.latest_active_run(product.id)
    assert replacement is not None
    assert replacement.id != failed.id
    assert replacement.workflow_generation == 1
    assert catalogue.get_run(failed.id).status is RunStatus.INCOMPLETE
    assert catalogue.get_thread(
        "thread-retry",
        user_id="local-development",
    ).workflow_generation == 1
    assert graph.invocations[-1]["reuse_mode"] == "continue_saved_work"
    assert response.thread_id == "thread-retry"
