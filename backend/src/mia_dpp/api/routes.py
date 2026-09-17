"""FastAPI routes translating HTTP requests into MIA capabilities."""

from __future__ import annotations

import json

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
from mia_dpp.api.schemas import (
    DppBuildRequest,
    HealthResponse,
    ProductDetail,
    ProductLibraryItem,
)
from mia_dpp.domain.product import ChatMessage
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
) -> AgentResponse:
    """Run one checkpointed autonomous turn for a trusted thread."""

    try:
        return await _application(http_request).message(payload)
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
) -> AgentResponse:
    """Resume an interrupt with trusted mapping-review decisions."""

    try:
        return await _application(http_request).review(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"review could not be applied: {error}",
        ) from error


@router.post("/api/agent/value", response_model=AgentResponse)
async def agent_value(payload: AgentValueRequest, http_request: Request) -> AgentResponse:
    """Resume an interrupt with a trusted human-supplied requirement value."""
    try:
        return await _application(http_request).provide_value(payload)
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
) -> tuple[WorkspaceArtifact, ...]:
    """List the manifest entries belonging to one thread workspace."""

    try:
        return _application(http_request).store.list_artifacts(thread_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/workspaces/{thread_id}/artifacts/{artifact_id}")
async def read_workspace_artifact(
    thread_id: str,
    artifact_id: str,
    http_request: Request,
    download: bool = Query(default=False),
) -> Response:
    """Read or download an artifact resolved only through its manifest ID."""

    try:
        artifact, data = _application(http_request).store.read_artifact(thread_id, artifact_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    headers = (
        {"Content-Disposition": f'attachment; filename="{artifact.name}"'} if download else None
    )
    return Response(content=data, media_type=artifact.content_type, headers=headers)


@router.get("/api/workspaces/{thread_id}/download")
async def download_workspace(thread_id: str, http_request: Request) -> Response:
    """Download all manifest-registered artifacts in one ZIP archive."""

    try:
        data = _application(http_request).store.export_zip(thread_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{thread_id}-workspace.zip"'},
    )


@router.get("/api/workspaces/{thread_id}/export")
async def export_workspace(thread_id: str, http_request: Request) -> dict[str, object]:
    """Return the combined structured workspace export as JSON."""

    try:
        return _application(http_request).store.combined_export(thread_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/api/workspaces/{thread_id}/trace",
    response_model=tuple[AgentTraceEvent, ...],
)
async def workspace_trace(
    thread_id: str,
    http_request: Request,
) -> tuple[AgentTraceEvent, ...]:
    """Return normalized activity events without exposing hidden model reasoning."""

    return _application(http_request).store.list_events(thread_id)


@router.get(
    "/api/mapping-knowledge",
    response_model=tuple[MappingKnowledgeEntry, ...],
)
async def mapping_knowledge(http_request: Request) -> tuple[MappingKnowledgeEntry, ...]:
    """List backend-owned mapping knowledge for the Integration Graph."""

    return _application(http_request).store.list_mapping_knowledge()


@router.post("/api/dpp", response_model=DppPackage)
async def create_dpp(payload: DppBuildRequest, http_request: Request) -> DppPackage:
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
async def thread_messages(thread_id: str, http_request: Request) -> tuple[ChatMessage, ...]:
    """Return timestamped human/assistant chat history for one durable thread."""

    return _application(http_request).context.catalogue.list_messages(thread_id)


@router.get("/api/products", response_model=tuple[ProductLibraryItem, ...])
async def product_library(http_request: Request) -> tuple[ProductLibraryItem, ...]:
    """List every durable product with its latest successful passport."""

    catalogue = _application(http_request).context.catalogue
    return tuple(
        ProductLibraryItem(
            product=product,
            latest_dpp=catalogue.latest_successful_dpp(product.id),
            run_count=len(catalogue.list_runs(product.id)),
        )
        for product in catalogue.list_products()
    )


@router.get("/api/products/{product_id}", response_model=ProductDetail)
async def product_detail(product_id: str, http_request: Request) -> ProductDetail:
    """Show product presentation data, attempts, DPP versions, and artifacts."""

    catalogue = _application(http_request).context.catalogue
    product = catalogue.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="unknown product")
    return ProductDetail(
        product=product,
        runs=catalogue.list_runs(product_id),
        dpp_versions=catalogue.list_dpp_versions(product_id),
        artifacts=catalogue.list_artifacts(product_id=product_id),
    )


@router.get("/api/artifacts/{artifact_id}")
async def read_durable_artifact(artifact_id: str, http_request: Request) -> Response:
    """Read a product-library artifact by its durable catalogue identity."""

    mia = _application(http_request)
    artifact = mia.context.catalogue.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return Response(
        content=mia.context.artifacts.get(artifact),
        media_type=artifact.content_type,
    )
