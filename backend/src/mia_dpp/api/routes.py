"""FastAPI routes translating HTTP requests into MIA capabilities."""

from __future__ import annotations

import json
import secrets

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from pydantic_ai.exceptions import UnexpectedModelBehavior

from mia_dpp import __version__
from mia_dpp.aas.build import build_dpp
from mia_dpp.aas.models import DppPackage
from mia_dpp.aas.templates import (
    STANDARDS_REPOSITORY_COMMIT,
    TemplateRepositoryError,
)
from mia_dpp.agent.models import (
    AgentRequest,
    AgentResponse,
    AgentReviewRequest,
    AgentTraceEvent,
    AgentValueRequest,
)
from mia_dpp.api.auth import AuthenticatedUser
from mia_dpp.api.schemas import (
    DppBuildRequest,
    HealthResponse,
    ProductDetail,
    ProductLibraryItem,
)
from mia_dpp.domain.product import BackgroundJob, ChatMessage, ThreadRecord
from mia_dpp.domain.targets import TemplateSummary
from mia_dpp.errors import MiaError
from mia_dpp.mia import Mia
from mia_dpp.storage.models import WorkspaceArtifact
from mia_dpp.tools.mapping.models import (
    MappingKnowledgeEntry,
)
from mia_dpp.tools.search import SearchUnavailableError
from mia_dpp.tools.web.models import (
    ExtractionDependencyError,
    PageLoadError,
    ProductUrlRejectedError,
)

router = APIRouter()


def _application(request: Request) -> Mia:
    app: FastAPI = request.app
    return app.state.mia  # type: ignore[no-any-return]


@router.get("/health", response_model=HealthResponse)
async def health(http_request: Request) -> HealthResponse:
    """Report readiness of both the process and its pinned standards data."""

    try:
        _application(http_request).templates.load("digital_nameplate")
    except TemplateRepositoryError:
        return HealthResponse(
            status="not_ready",
            version=__version__,
            standards_ready=False,
            standards_commit=STANDARDS_REPOSITORY_COMMIT,
        )
    return HealthResponse(
        status="ok",
        version=__version__,
        standards_ready=True,
        standards_commit=STANDARDS_REPOSITORY_COMMIT,
    )


@router.get("/api/templates", response_model=tuple[TemplateSummary, ...])
async def template_catalog(http_request: Request) -> tuple[TemplateSummary, ...]:
    """Expose the two pinned templates currently used to prove generic loading."""

    try:
        templates = _application(http_request).templates
        return tuple(templates.summary(key) for key in templates.keys())
    except TemplateRepositoryError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/api/agent/messages", response_model=AgentResponse)
async def agent_message(
    payload: AgentRequest,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> AgentResponse:
    """Run one checkpointed autonomous turn for a trusted thread."""

    try:
        return await _application(http_request).message(payload, user_id=user_id)
    except ProductUrlRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ExtractionDependencyError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except PageLoadError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except SearchUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (
        UnexpectedModelBehavior,
        httpx.HTTPError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(status_code=502, detail=f"agent failed: {error}") from error


@router.post("/api/agent/review", response_model=AgentResponse)
async def agent_review(
    payload: AgentReviewRequest,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> AgentResponse:
    """Resume an interrupt with trusted mapping-review decisions."""

    try:
        return await _application(http_request).review(payload, user_id=user_id)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"review could not be applied: {error}",
        ) from error


@router.post("/api/agent/value", response_model=AgentResponse)
async def agent_value(
    payload: AgentValueRequest,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> AgentResponse:
    """Resume an interrupt with a trusted human-supplied requirement value."""
    try:
        return await _application(http_request).provide_value(payload, user_id=user_id)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"human value could not be applied: {error}",
        ) from error


@router.get(
    "/api/workspaces/{thread_id}/artifacts",
    response_model=tuple[WorkspaceArtifact, ...],
)
async def list_workspace_artifacts(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> tuple[WorkspaceArtifact, ...]:
    """List the manifest entries belonging to one thread workspace."""

    try:
        return _application(http_request).store.list_artifacts(thread_id, user_id=user_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/workspaces/{thread_id}/artifacts/{artifact_id}")
async def read_workspace_artifact(
    thread_id: str,
    artifact_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
    download: bool = Query(default=False),
) -> Response:
    """Read or download an artifact resolved only through its manifest ID."""

    try:
        artifact, data = _application(http_request).store.read_artifact(
            thread_id,
            artifact_id,
            user_id=user_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    headers = (
        {"Content-Disposition": f'attachment; filename="{artifact.name}"'} if download else None
    )
    return Response(content=data, media_type=artifact.content_type, headers=headers)


@router.get("/api/workspaces/{thread_id}/download")
async def download_workspace(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> Response:
    """Download all manifest-registered artifacts in one ZIP archive."""

    try:
        data = _application(http_request).store.export_zip(thread_id, user_id=user_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{thread_id}-workspace.zip"'},
    )


@router.get("/api/workspaces/{thread_id}/export")
async def export_workspace(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> dict[str, object]:
    """Return the combined structured workspace export as JSON."""

    try:
        return _application(http_request).store.combined_export(thread_id, user_id=user_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/api/workspaces/{thread_id}/trace",
    response_model=tuple[AgentTraceEvent, ...],
)
async def workspace_trace(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> tuple[AgentTraceEvent, ...]:
    """Return normalized activity events without exposing hidden model reasoning."""

    return _application(http_request).store.list_events(thread_id, user_id=user_id)


@router.get(
    "/api/mapping-knowledge",
    response_model=tuple[MappingKnowledgeEntry, ...],
)
async def mapping_knowledge(
    http_request: Request,
    _user_id: AuthenticatedUser,
) -> tuple[MappingKnowledgeEntry, ...]:
    """List backend-owned mapping knowledge for the Integration Graph."""

    return _application(http_request).store.list_mapping_knowledge()


@router.post("/api/dpp", response_model=DppPackage)
async def create_dpp(
    payload: DppBuildRequest,
    http_request: Request,
    _user_id: AuthenticatedUser,
) -> DppPackage:
    """Build and validate an official-template-backed AAS environment."""

    try:
        return build_dpp(
            payload.product_name,
            list(payload.mappings),
            repository=_application(http_request).templates,
            evidence=payload.evidence,
        )
    except TemplateRepositoryError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except MiaError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/api/threads/{thread_id}/messages",
    response_model=tuple[ChatMessage, ...],
)
async def thread_messages(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> tuple[ChatMessage, ...]:
    """Return timestamped human/assistant chat history for one durable thread."""

    return _application(http_request).context.catalogue.list_messages(
        thread_id,
        user_id=user_id,
    )


@router.get("/api/threads", response_model=tuple[ThreadRecord, ...])
async def threads(
    http_request: Request,
    user_id: AuthenticatedUser,
) -> tuple[ThreadRecord, ...]:
    """List only conversations owned by the authenticated Clerk account."""

    return _application(http_request).context.catalogue.list_threads(user_id)


@router.get(
    "/api/threads/{thread_id}/background-jobs",
    response_model=tuple[BackgroundJob, ...],
)
async def background_jobs(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> tuple[BackgroundJob, ...]:
    """Expose live deep-research progress only to the thread owner."""

    catalogue = _application(http_request).context.catalogue
    if catalogue.get_thread(thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="unknown thread")
    return catalogue.list_background_jobs(user_id=user_id, thread_id=thread_id)


@router.get("/api/background-jobs/{job_id}", response_model=BackgroundJob)
async def background_job(
    job_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> BackgroundJob:
    """Return one job only when it belongs to the authenticated account."""

    job = _application(http_request).context.catalogue.get_background_job(
        job_id,
        user_id=user_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="unknown background job")
    return job


@router.post(
    "/api/background-jobs/{job_id}/execute",
    response_model=BackgroundJob,
)
async def execute_background_job(job_id: str, http_request: Request) -> BackgroundJob:
    """Worker-only idempotent entrypoint invoked by Vercel Workflow steps."""

    mia = _application(http_request)
    configured = mia.settings.workflow_secret
    supplied = http_request.headers.get("x-mia-workflow-secret")
    if configured is None or supplied is None or not secrets.compare_digest(
        supplied,
        configured.get_secret_value(),
    ):
        raise HTTPException(status_code=401, detail="invalid workflow credential")
    job = mia.context.catalogue.get_background_job_internal(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown background job")
    return await mia.run_deep_research(job_id, user_id=job.user_id)


@router.get("/api/products", response_model=tuple[ProductLibraryItem, ...])
async def product_library(
    http_request: Request,
    user_id: AuthenticatedUser,
) -> tuple[ProductLibraryItem, ...]:
    """List every durable product with its latest successful passport."""

    catalogue = _application(http_request).context.catalogue
    return tuple(
        ProductLibraryItem(
            product=product,
            latest_dpp=catalogue.latest_successful_dpp(product.id, user_id=user_id),
            run_count=len(catalogue.list_runs(product.id, user_id=user_id)),
        )
        for product in catalogue.list_products(user_id=user_id)
    )


@router.get("/api/products/{product_id}", response_model=ProductDetail)
async def product_detail(
    product_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> ProductDetail:
    """Show product presentation data, attempts, DPP versions, and artifacts."""

    catalogue = _application(http_request).context.catalogue
    product = catalogue.get_product(product_id, user_id=user_id)
    if product is None:
        raise HTTPException(status_code=404, detail="unknown product")
    return ProductDetail(
        product=product,
        runs=catalogue.list_runs(product_id, user_id=user_id),
        dpp_versions=catalogue.list_dpp_versions(product_id, user_id=user_id),
        artifacts=catalogue.list_artifacts(product_id=product_id, user_id=user_id),
    )


@router.get("/api/artifacts/{artifact_id}")
async def read_durable_artifact(
    artifact_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> Response:
    """Read a product-library artifact by its durable catalogue identity."""

    mia = _application(http_request)
    artifact = mia.context.catalogue.get_artifact(artifact_id)
    if artifact is None or artifact.run_id is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    run = mia.context.catalogue.get_run(artifact.run_id)
    if run is None or mia.context.catalogue.get_thread(run.thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return Response(
        content=mia.context.artifacts.get(artifact),
        media_type=artifact.content_type,
    )
