"""Failure and interruption persistence at the LangGraph application boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mia_dpp.agent.models import AgentRequest, AgentResponse, AgentStatus
from mia_dpp.domain.product import RunStatus
from mia_dpp.mia import Mia
from mia_dpp.persistence.catalogue import ProductCatalogue


class _Store:
    def list_events(self, thread_id: str):
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
    return mia


def test_graph_failure_is_recorded_on_the_active_attempt(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product("https://example.com/failing")
    run = catalogue.start_run(product.id, "thread-failing")

    class Graph:
        async def aget_state(self, config):
            return _Snapshot({})

        async def ainvoke(self, update, **kwargs):
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
