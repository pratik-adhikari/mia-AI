"""FastAPI routes translating HTTP requests into MIA capabilities."""

from __future__ import annotations

import hashlib
import json
import secrets

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
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
from mia_dpp.api.auth import AuthenticatedUser, AuthenticatedUserName
from mia_dpp.api.schemas import (
    DppBuildRequest,
    HealthResponse,
    ProductDetail,
    ProductLibraryItem,
    StorageStatus,
)
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.product import (
    DppReleaseStatus,
    BackgroundJob,
    BackgroundJobStatus,
    ChatMessage,
    RunStatus,
    ThreadRecord,
)
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


@router.get("/api/runtime/storage", response_model=StorageStatus)
async def runtime_storage(
    http_request: Request,
    _user_id: AuthenticatedUser,
) -> StorageStatus:
    """Report which durable storage adapters the running deployment actually selected."""

    application = _application(http_request)
    database_url = application.settings.database_url or ""
    database_backend = application.context.catalogue.backend
    provider = (
        "supabase"
        if "supabase.com" in database_url.casefold()
        else ("postgres" if database_backend == "postgres" else "local")
    )
    artifact_backend = (
        "vercel_blob"
        if application.context.artifacts.__class__.__name__ == "VercelBlobArtifactStore"
        else "filesystem"
    )
    return StorageStatus(
        database_backend=database_backend,
        database_provider=provider,
        artifact_backend=artifact_backend,
        durable_metadata=database_backend == "postgres",
        durable_artifacts=artifact_backend == "vercel_blob",
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
    except Exception as error:
        # Temporary integration-branch diagnostic: surface the exception class
        # and a bounded message so runtime failures can be located without
        # exposing tracebacks or database credentials to the browser.
        detail = str(error) or type(error).__name__
        if "://" in detail:
            detail = "runtime dependency failed; inspect server logs for connection details"
        raise HTTPException(
            status_code=500,
            detail=f"{type(error).__name__}: {detail[:500]}",
        ) from error


@router.post("/api/agent/review", response_model=AgentResponse)
async def agent_review(
    payload: AgentReviewRequest,
    http_request: Request,
    user_id: AuthenticatedUser,
    actor_name: AuthenticatedUserName,
) -> AgentResponse:
    """Resume an interrupt with trusted mapping-review decisions."""

    try:
        trusted = payload.model_copy(update={"actor_name": actor_name})
        return await _application(http_request).review(trusted, user_id=user_id)
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
    actor_name: AuthenticatedUserName,
) -> AgentResponse:
    """Resume an interrupt with a trusted human-supplied requirement value."""
    try:
        trusted = payload.model_copy(update={"actor_name": actor_name})
        return await _application(http_request).provide_value(trusted, user_id=user_id)
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
    user_id: AuthenticatedUser,
) -> tuple[MappingKnowledgeEntry, ...]:
    """List backend-owned mapping knowledge for the Integration Graph."""

    return _application(http_request).store.list_mapping_knowledge(user_id=user_id)


@router.post("/api/dpp", response_model=DppPackage)
async def create_dpp(
    payload: DppBuildRequest,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> DppPackage:
    """Build, validate, and when scoped to a workspace, durably persist the DPP."""

    try:
        application = _application(http_request)
        package = build_dpp(
            payload.product_name,
            list(payload.mappings),
            repository=application.templates,
            evidence=payload.evidence,
        )
        if payload.thread_id and payload.product_id:
            catalogue = application.context.catalogue
            if catalogue.get_thread(payload.thread_id, user_id=user_id) is None:
                raise ValueError("unknown thread")
            product = catalogue.get_product(payload.product_id, user_id=user_id)
            if product is None:
                raise ValueError("unknown product")
            run = catalogue.start_run(
                product.id,
                payload.thread_id,
                user_id=user_id,
                refresh_requested=True,
            )

            def persist(key: str, data: bytes, content_type: str) -> str:
                artifact = application.context.artifacts.put(
                    key,
                    data,
                    content_type=content_type,
                    product_id=product.id,
                    run_id=run.id,
                )
                catalogue.register_artifact(artifact)
                return artifact.id

            dpp_id = persist(
                "dpp/manual-package.json",
                package.model_dump_json(by_alias=True, indent=2).encode(),
                "application/json",
            )
            aas_id = persist(
                "aas/manual-environment.json",
                json.dumps(package.environment, indent=2, default=str).encode(),
                "application/json",
            )
            validation_id = persist(
                "aas/manual-validation.json",
                package.validation_report.model_dump_json(by_alias=True, indent=2).encode(),
                "application/json",
            )
            catalogue.finish_run(
                run.id,
                RunStatus.COMPLETED if package.deployable else RunStatus.FAILED,
                error=None if package.deployable else "Manual DPP validation blocked deployment",
            )
            if package.deployable:
                dummy_mapping_ids = tuple(
                    item.id for item in payload.mappings if item.human_value_kind == "dummy"
                )
                catalogue.create_dpp_version(
                    product.id,
                    run.id,
                    dpp_artifact_id=dpp_id,
                    aas_artifact_id=aas_id,
                    validation_artifact_id=validation_id,
                    source_fingerprint=hashlib.sha256(
                        package.model_dump_json(by_alias=True).encode()
                    ).hexdigest(),
                    deployable=True,
                    release_status=(
                        DppReleaseStatus.PROVISIONAL
                        if dummy_mapping_ids
                        else DppReleaseStatus.VERIFIED
                    ),
                    dummy_mapping_ids=dummy_mapping_ids,
                )
        return package
    except TemplateRepositoryError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (MiaError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/threads/{thread_id}", response_model=AgentResponse)
async def thread_state(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> AgentResponse:
    """Restore the checkpoint-backed workspace state for an owned conversation."""

    try:
        return await _application(http_request).thread_state(thread_id, user_id=user_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="unknown thread") from error


@router.get("/api/debug/graph/stream")
async def workflow_graph_stream(
    http_request: Request,
    user_id: AuthenticatedUser,
    thread_id: str | None = None,
) -> StreamingResponse:
    """Stream safe LangGraph node lifecycle events for the owned local workflow."""

    application = _application(http_request)
    if not application.graph_debug_enabled:
        raise HTTPException(status_code=404, detail="live graph debugging is local-only")
    if (
        thread_id is not None
        and application.context.catalogue.get_thread(thread_id, user_id=user_id) is None
    ):
        raise HTTPException(status_code=404, detail="unknown thread")

    async def events():
        try:
            async for event in application.graph_debug_stream(thread_id, user_id=user_id):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
        except Exception as error:
            detail = {"errorType": type(error).__name__}
            yield f"event: error\ndata: {json.dumps(detail)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@router.delete("/api/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> Response:
    """Remove a chat from history while retaining product/run artifacts for audit and reuse."""

    catalogue = _application(http_request).context.catalogue
    if catalogue.get_thread(thread_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="unknown thread")
    catalogue.delete_thread(thread_id, user_id=user_id)
    return Response(status_code=204)


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


@router.post("/api/background-jobs/{job_id}/retry", response_model=BackgroundJob)
async def retry_background_job(
    job_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> BackgroundJob:
    """Requeue a failed owned background job for the persistent local/hosted worker."""

    catalogue = _application(http_request).context.catalogue
    job = catalogue.get_background_job(job_id, user_id=user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown background job")
    if job.status is not BackgroundJobStatus.FAILED:
        return job
    return catalogue.requeue_background_job(job.id, user_id=user_id)


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
    if (
        configured is None
        or supplied is None
        or not secrets.compare_digest(
            supplied,
            configured.get_secret_value(),
        )
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

    application = _application(http_request)
    catalogue = application.context.catalogue
    items: list[ProductLibraryItem] = []
    for product in catalogue.list_products(user_id=user_id):
        runs = catalogue.list_runs(product.id, user_id=user_id)
        latest_run = runs[0] if runs else None
        latest_dpp = catalogue.latest_successful_dpp(product.id, user_id=user_id)
        reviewed, _legacy_dummy = _mapping_provenance_counts(application, product.id, user_id)
        snapshot = catalogue.get_product_work_snapshot(product.id, user_id=user_id)
        human_reviews = catalogue.list_human_reviews(product.id, user_id=user_id)
        dummy = sum(item.value_kind == "dummy" for item in human_reviews)
        last_reviewer = next(
            (item.actor_name for item in reversed(human_reviews) if item.actor_name),
            None,
        )
        resumable = latest_run is not None and latest_run.status in {
            RunStatus.RUNNING,
            RunStatus.AWAITING_HUMAN,
        }
        items.append(
            ProductLibraryItem(
                product=product,
                latest_dpp=latest_dpp,
                latest_run=latest_run,
                run_count=len(runs),
                resumable=resumable,
                resume_thread_id=latest_run.thread_id if resumable and latest_run else None,
                workflow_status=(
                    latest_run.status.value
                    if latest_run is not None
                    else ("completed" if latest_dpp is not None else "idle")
                ),
                human_reviewed_mappings=reviewed,
                human_dummy_mappings=dummy,
                human_review_count=len(human_reviews),
                last_human_reviewer=last_reviewer,
                unresolved_required_count=(
                    len(snapshot.unresolved_required_ids) if snapshot is not None else 0
                ),
                snapshot=snapshot,
            )
        )
    return tuple(items)


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
        snapshot=catalogue.get_product_work_snapshot(product_id, user_id=user_id),
        snapshot_history=catalogue.list_product_work_snapshot_history(
            product_id,
            user_id=user_id,
        ),
        human_reviews=catalogue.list_human_reviews(product_id, user_id=user_id),
        identifiers=catalogue.list_product_identifiers(product_id, user_id=user_id),
    )


@router.get("/api/artifacts/{artifact_id}")
async def read_durable_artifact(
    artifact_id: str,
    http_request: Request,
    user_id: AuthenticatedUser,
) -> Response:
    """Read a product-library artifact by its durable catalogue identity."""

    mia = _application(http_request)
    artifact = mia.context.catalogue.get_artifact(artifact_id, user_id=user_id)
    if artifact is None or artifact.run_id is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    run = mia.context.catalogue.get_run(artifact.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown artifact")
    return Response(
        content=mia.context.artifacts.get(artifact),
        media_type=artifact.content_type,
    )


def _mapping_provenance_counts(application: Mia, product_id: str, user_id: str) -> tuple[int, int]:
    preferred = {
        "mapping/human-value.json",
        "mapping/reviewed.json",
        "mapping/research-integrated.json",
        "mapping/reused-reviewed.json",
        "mapping/mapping.json",
    }
    for artifact in reversed(
        application.context.catalogue.list_artifacts(product_id=product_id, user_id=user_id)
    ):
        if artifact.key not in preferred:
            continue
        try:
            mapping = MappingResult.model_validate_json(application.context.artifacts.get(artifact))
        except ValueError:
            continue
        rows = (*mapping.mapped, *mapping.ambiguous, *mapping.rejected)
        return (
            sum(item.human_reviewed for item in rows),
            sum(item.human_value_kind == "dummy" for item in rows),
        )
    return 0, 0
