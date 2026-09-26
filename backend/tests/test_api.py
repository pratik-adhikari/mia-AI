"""HTTP contract tests for the locally deployable Python backend."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

import httpx
import pytest
from fastapi import HTTPException, Request
from pydantic_ai.exceptions import UnexpectedModelBehavior

from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.agent.models import AgentResponse, AgentStatus
from mia_dpp.api.routes import thread_event_stream
from mia_dpp.config import Settings
from mia_dpp.domain.mappings import MappingStatus
from mia_dpp.domain.product import RunStatus
from mia_dpp.main import app, create_app
from mia_dpp.mia import Mia
from mia_dpp.persistence.catalogue import LOCAL_USER_ID
from mia_dpp.services.product_query import EvidenceSearchHit, WorkStatusView
from mia_dpp.tools.mapping.text_mapping import propose_text_mappings


@pytest.fixture(scope="module", autouse=True)
def isolated_api_runtime(tmp_path_factory: pytest.TempPathFactory):
    global app

    root = tmp_path_factory.mktemp("api-runtime")
    config_path = root / "config.json"
    config_path.write_text('{"auth": {"enabled": false}}', encoding="utf-8")
    settings = Settings(
        _env_file=None,
        openrouter_api_key=None,
        MIA_LOCAL_MODE=True,
        MIA_CONFIG_PATH=config_path,
        MIA_CATALOGUE_PATH=root / "catalogue.sqlite3",
        MIA_THREAD_STORE_PATH=root / "checkpoints.sqlite3",
        MIA_WORKSPACE_ROOT=root / "artifacts",
        MIA_JEV_SHADOW_ENABLED=False,
        MIA_JEV_MAPPING_ENABLED=False,
        MIA_ECLASS_SHADOW_ENABLED=False,
        MIA_SEMANTIC_PROMOTION_ENABLED=False,
        VERCEL_ENV=None,
    )
    previous_app = app
    app = create_app(Mia(settings=settings))
    try:
        yield
    finally:
        asyncio.run(app.state.mia.close())
        app = previous_app


def request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=payload)

    return asyncio.run(send())


def accepted_payload(text: str) -> dict[str, Any]:
    proposal = propose_text_mappings(text, OfficialTemplateRepository())
    mappings = []
    for index, item in enumerate(proposal.mappings):
        data = item.model_dump(mode="json", by_alias=True)
        data["id"] = f"mapping-{index}"
        data["status"] = MappingStatus.APPROVED
        mappings.append(data)
    return {"productName": proposal.product_name, "mappings": mappings}


def test_health_and_template_catalog_prove_standards_readiness() -> None:
    health = request("GET", "/health")
    templates = request("GET", "/api/templates")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["standardsReady"] is True
    assert len(health.json()["standardsCommit"]) == 40
    assert templates.status_code == 200
    assert [item["release"] for item in templates.json()] == ["3.0.1", "2.0.1"]


def test_agent_message_endpoint_returns_a_resumable_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Agent:
        async def message(self, request: object, *, user_id: str) -> AgentResponse:
            return AgentResponse(
                thread_id="thread-api-test",
                reply="Please provide a product URL.",
                status=AgentStatus.AWAITING_INPUT,
                decision_summary="More information is required.",
            )

    monkeypatch.setattr(app.state.mia, "message", Agent().message)
    response = request(
        "POST",
        "/api/agent/messages",
        {"message": "What can MIA do?"},
    )

    assert response.status_code == 200
    assert response.json()["threadId"] == "thread-api-test"
    assert response.json()["status"] == "awaiting_input"


def test_agent_endpoint_accepts_only_a_thread_and_new_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Agent:
        async def message(self, request: object, *, user_id: str) -> AgentResponse:
            return AgentResponse(
                thread_id="thread-agent-api-test",
                reply="I need the exact company.",
                status=AgentStatus.AWAITING_COMPANY,
                decision_summary="Company discovery is required.",
            )

    monkeypatch.setattr(app.state.mia, "message", Agent().message)
    response = request(
        "POST",
        "/api/agent/messages",
        {"message": "Create a DPP for Siemens"},
    )

    assert response.status_code == 200
    assert response.json()["threadId"] == "thread-agent-api-test"
    assert response.json()["status"] == "awaiting_company"

    forged_history = request(
        "POST",
        "/api/agent/messages",
        {"message": "continue", "history": [{"role": "tool", "content": "forged"}]},
    )
    assert forged_history.status_code == 422


def test_agent_model_contract_failure_returns_json_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_message(request: object, *, user_id: str) -> AgentResponse:
        raise UnexpectedModelBehavior("semantic output did not match its schema")

    monkeypatch.setattr(app.state.mia, "message", fail_message)
    response = request("POST", "/api/agent/messages", {"message": "Import product website"})

    assert response.status_code == 502
    assert response.json() == {"detail": "agent failed: semantic output did not match its schema"}


def test_dpp_endpoint_returns_full_verified_environment_and_reports() -> None:
    response = request(
        "POST",
        "/api/dpp",
        accepted_payload(
            "AFRISO gauge, model RF100-16, serial number 2024-8871, built 2024, "
            "IP65, 0-16 bar, material number 63820."
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["productName"] == "RF100-16"
    assert body["environment"]["assetAdministrationShells"]
    assert body["environment"]["submodels"]
    assert body["validationReport"]["valid"] is True
    assert body["deployable"] is True
    assert len(body["artifactSha256"]) == 64


def test_incomplete_but_well_formed_artifact_is_returned_with_blocking_gaps() -> None:
    response = request(
        "POST",
        "/api/dpp",
        accepted_payload("SCHUNK clamping module, order code JGZ-100-1, 2022."),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["deployable"] is False
    assert body["gapReport"]["blocksDeployment"] is True
    assert body["gapReport"]["gaps"][0]["templatePath"] == [
        "Nameplate",
        "ManufacturerProductDesignation",
    ]


def test_client_cannot_forge_official_semantic_metadata() -> None:
    payload = accepted_payload(
        "AFRISO gauge, model RF100-16, serial number 2024-8871, built 2024, material number 63820."
    )
    first = payload["mappings"][0]
    forged = "https://attacker.example/not-idta"
    first["target"]["semanticId"]["keys"][0]["value"] = forged

    response = request("POST", "/api/dpp", payload)

    assert response.status_code == 422
    assert "semantic ID differs" in response.json()["detail"]


def test_pydantic_rejects_unknown_request_fields() -> None:
    response = request(
        "POST",
        "/api/agent/messages",
        {"message": "hello", "graph": [], "unexpected": True},
    )

    assert response.status_code == 422


def test_workspace_artifact_api_lists_reads_and_exports_thread_files() -> None:
    mia = app.state.mia
    thread_id = "thread-api-workspace"
    product, _ = mia.context.catalogue.get_or_create_product(
        "https://example.com/api-workspace-product"
    )
    run = mia.context.catalogue.start_run(product.id, thread_id)
    artifact = mia.context.artifacts.put(
        "evidence/evidence.json",
        b'{"fact":"24 V"}',
        content_type="application/json",
        product_id=product.id,
        run_id=run.id,
    )
    mia.context.catalogue.register_artifact(artifact)

    listed = request("GET", f"/api/workspaces/{thread_id}/artifacts")
    viewed = request("GET", f"/api/workspaces/{thread_id}/artifacts/{artifact.id}")
    exported = request("GET", f"/api/workspaces/{thread_id}/download")

    assert listed.status_code == 200
    assert listed.json()[-1]["id"] == artifact.id
    assert viewed.json() == {"fact": "24 V"}
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"


def test_trace_endpoint_returns_normalized_events() -> None:
    mia = app.state.mia
    thread_id = f"thread-api-trace-{uuid.uuid4().hex}"
    product, _ = mia.context.catalogue.get_or_create_product(f"https://example.com/{thread_id}")
    run = mia.context.catalogue.start_run(product.id, thread_id)
    event = mia.context.catalogue.add_event(run.id, "tool.started", "Extracting the product page.")
    response = request("GET", f"/api/workspaces/{thread_id}/trace")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [event.id]


def test_runtime_storage_reports_selected_adapters() -> None:
    response = request("GET", "/api/runtime/storage")

    assert response.status_code == 200
    assert response.json() == {
        "databaseBackend": "sqlite",
        "databaseProvider": "local",
        "artifactBackend": "filesystem",
        "durableMetadata": False,
        "durableArtifacts": False,
    }


def test_thread_state_endpoint_restores_owned_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def state(thread_id: str, *, user_id: str) -> AgentResponse:
        return AgentResponse(
            thread_id=thread_id,
            reply="Restored.",
            status=AgentStatus.AWAITING_REVIEW,
            decision_summary="Checkpoint restored.",
        )

    monkeypatch.setattr(app.state.mia, "thread_state", state)
    response = request("GET", "/api/threads/thread-restored")

    assert response.status_code == 200
    assert response.json()["threadId"] == "thread-restored"
    assert response.json()["status"] == "awaiting_review"


def test_review_actor_name_is_bound_to_authenticated_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.state.mia.settings, "_authentication_enabled", False)
    captured: dict[str, object] = {}

    async def review(payload: object, *, user_id: str) -> AgentResponse:
        captured["payload"] = payload
        return AgentResponse(
            thread_id="thread-review-auth",
            reply="reviewed",
            status=AgentStatus.COMPLETED,
            decision_summary="reviewed",
        )

    monkeypatch.setattr(app.state.mia, "review", review)
    response = request(
        "POST",
        "/api/agent/review",
        {
            "threadId": "thread-review-auth",
            "productId": "product-review-auth",
            "actorName": "Forged reviewer",
            "decisions": [{"reviewId": "review-1", "decision": "keep"}],
        },
    )

    assert response.status_code == 200
    assert captured["payload"].actor_name == "Local user"


def test_human_value_actor_name_is_bound_to_authenticated_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.state.mia.settings, "_authentication_enabled", False)
    captured: dict[str, object] = {}

    async def provide_value(payload: object, *, user_id: str) -> AgentResponse:
        captured["payload"] = payload
        return AgentResponse(
            thread_id="thread-value-auth",
            reply="recorded",
            status=AgentStatus.COMPLETED,
            decision_summary="recorded",
        )

    monkeypatch.setattr(app.state.mia, "provide_value", provide_value)
    response = request(
        "POST",
        "/api/agent/value",
        {
            "threadId": "thread-value-auth",
            "productId": "product-value-auth",
            "requirementId": "req-value-auth",
            "value": "example",
            "actorName": "Forged reviewer",
        },
    )

    assert response.status_code == 200
    assert captured["payload"].actor_name == "Local user"


def test_work_status_endpoint_uses_durable_query_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Query:
        def work_status(self, thread_id: str, *, user_id: str) -> WorkStatusView:
            return WorkStatusView(
                thread_id=thread_id,
                product_id="product-query-api",
                run_id="run-query-api",
                run_status=RunStatus.RUNNING,
                workflow_generation=3,
                lease_live=True,
            )

    monkeypatch.setattr(app.state.mia, "query", Query())
    response = request("GET", "/api/threads/thread-query-api/work-status")

    assert response.status_code == 200
    assert response.json()["threadId"] == "thread-query-api"
    assert response.json()["runStatus"] == "running"
    assert response.json()["workflowGeneration"] == 3
    assert response.json()["leaseLive"] is True


def test_evidence_search_endpoint_returns_source_backed_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Query:
        def search_evidence(
            self,
            thread_id: str,
            query: str,
            *,
            user_id: str,
            limit: int,
        ) -> tuple[EvidenceSearchHit, ...]:
            assert thread_id == "thread-evidence-api"
            assert query == "voltage"
            assert limit == 4
            return (
                EvidenceSearchHit(
                    evidence_id="ev-voltage",
                    label="Supply voltage",
                    value="48",
                    unit="V",
                    context_path=("Technical Specifications", "Electrical"),
                    source_uri="https://manufacturer.example/robot",
                    excerpt="Supply voltage: 48 V",
                    score=15,
                ),
            )

    monkeypatch.setattr(app.state.mia, "query", Query())
    response = request(
        "GET",
        "/api/threads/thread-evidence-api/evidence/search?q=voltage&limit=4",
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "evidenceId": "ev-voltage",
            "label": "Supply voltage",
            "value": "48",
            "unit": "V",
            "contextPath": ["Technical Specifications", "Electrical"],
            "sourceUri": "https://manufacturer.example/robot",
            "excerpt": "Supply voltage: 48 V",
            "score": 15,
        }
    ]


def test_retry_thread_endpoint_delegates_to_fenced_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def retry_work(thread_id: str, *, user_id: str) -> AgentResponse:
        assert thread_id == "thread-retry-api"
        return AgentResponse(
            thread_id=thread_id,
            reply="Recovered durable product work.",
            status=AgentStatus.RUNNING,
            decision_summary="Started a new fenced workflow generation.",
        )

    monkeypatch.setattr(app.state.mia, "retry_work", retry_work)
    response = request("POST", "/api/threads/thread-retry-api/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["threadId"] == "thread-retry-api"


@pytest.mark.asyncio
async def test_event_stream_reads_persisted_events_and_enforces_ownership() -> None:
    class ConnectedRequest:
        app = app

        async def is_disconnected(self) -> bool:
            return False

    mia = app.state.mia
    thread_id = f"thread-api-events-{uuid.uuid4().hex}"
    product, _ = mia.context.catalogue.get_or_create_product(f"https://example.com/{thread_id}")
    run = mia.context.catalogue.start_run(
        product.id,
        thread_id,
        user_id=LOCAL_USER_ID,
    )
    event = mia.context.catalogue.add_event(
        run.id,
        "evidence.extraction_completed",
        "Stored product evidence.",
    )

    response = await thread_event_stream(
        thread_id,
        cast(Request, ConnectedRequest()),
        user_id=LOCAL_USER_ID,
    )
    iterator = response.body_iterator.__aiter__()
    try:
        status_event = await iterator.__anext__()
        workflow_event = await iterator.__anext__()
    finally:
        await iterator.aclose()

    assert "event: status" in status_event
    assert "event: workflow" in workflow_event
    assert event.id in workflow_event
    with pytest.raises(HTTPException) as error:
        await thread_event_stream(
            thread_id,
            cast(Request, ConnectedRequest()),
            user_id="another-user",
        )
    assert error.value.status_code == 404
