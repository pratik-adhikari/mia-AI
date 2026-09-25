"""Controlled end-to-end LangGraph workflow over the real MIA services."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mia_dpp.agent.models import (
    AgentRequest,
    AgentReviewDecision,
    AgentReviewRequest,
    AgentStatus,
    AgentValueRequest,
)
from mia_dpp.agents.semantic_mapping.agent import (
    deterministic_hints,
    lean_evidence,
    lean_targets,
)
from mia_dpp.config import Settings
from mia_dpp.domain.evidence import ExtractedAsset
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.domain.targets import TemplateIndex
from mia_dpp.mia import Mia
from mia_dpp.tools.mapping.models import (
    BatchSemanticMappingResult,
    EvidenceMappingDecision,
    SemanticMappingMetrics,
    SemanticMappingRun,
)
from mia_dpp.tools.search import SearchHit
from mia_dpp.tools.web.models import RenderedPage
from mia_dpp.tools.web.tool import WebExtractionTool
from mia_dpp.tools.web.url_policy import ProductUrlPolicy

FIXTURE = Path(__file__).parent / "fixtures" / "web" / "website-product.html"
PRODUCT_URL = "https://manufacturer.example/products/pg-16"


class _Search:
    async def search(self, query: str, *, limit: int = 8) -> tuple[SearchHit, ...]:
        return ()


class _Loader:
    async def load(self, url: str) -> RenderedPage:
        return RenderedPage(
            url=url,
            html=FIXTURE.read_text(encoding="utf-8"),
            observed_assets=(
                ExtractedAsset(
                    url="https://manufacturer.example/files/pg-16-datasheet.pdf",
                    kind="document",
                    label="PG-16 datasheet",
                ),
            ),
        )


class _WebTool(WebExtractionTool):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.download_calls = 0

    async def download_source(self, url: str, *, max_bytes: int = 25 * 1024 * 1024):
        self.download_calls += 1
        raise AssertionError("the shallow seed pass must defer binary asset downloads")


class _SemanticMapper:
    def __init__(self) -> None:
        self.calls = 0

    async def map(
        self,
        package,
        template_index: TemplateIndex,
        deterministic: MappingResult,
        *,
        reviewed_knowledge: tuple[dict[str, object], ...] = (),
    ) -> SemanticMappingRun:
        self.calls += 1
        evidence = lean_evidence(package)
        targets = lean_targets(template_index)
        hints = deterministic_hints(deterministic, template_index)
        authoritative = {item.evidence_id: item.requirement_id for item in hints}
        result = BatchSemanticMappingResult(
            decisions=tuple(
                EvidenceMappingDecision(
                    evidence_id=item.id,
                    status="mapped" if item.id in authoritative else "unmapped",
                    requirement_id=authoritative.get(item.id),
                    reason=(
                        "Preserve the deterministic mapping."
                        if item.id in authoritative
                        else "No suitable official target in this controlled fixture."
                    ),
                )
                for item in evidence
            )
        )
        return SemanticMappingRun(
            evidence=evidence,
            targets=targets,
            deterministic_mappings=hints,
            result=result,
            metrics=SemanticMappingMetrics(
                evidence_count=len(evidence),
                target_count=len(targets),
                lean_evidence_json_bytes=1,
                lean_target_json_bytes=1,
                model_requests=1,
            ),
        )


async def _public_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def _human_value(value_type: str | None, allowed_values: tuple[str, ...]) -> str:
    if allowed_values:
        return allowed_values[0]
    return {
        "xs:boolean": "true",
        "xs:date": "2026-01-01",
        "xs:dateTime": "2026-01-01T00:00:00Z",
        "xs:decimal": "1.0",
        "xs:double": "1.0",
        "xs:float": "1.0",
        "xs:int": "1",
        "xs:integer": "1",
        "xs:long": "1",
    }.get(value_type or "", "Fixture value")


@pytest.mark.asyncio
async def test_graph_review_resume_build_and_reuse_are_one_durable_workflow(tmp_path: Path) -> None:
    mapper = _SemanticMapper()
    settings = Settings(
        _env_file=None,
        openrouter_api_key=None,
        MIA_CATALOGUE_PATH=tmp_path / "catalogue.sqlite3",
        MIA_THREAD_STORE_PATH=tmp_path / "checkpoints.sqlite3",
        MIA_WORKSPACE_ROOT=tmp_path / "artifacts",
    )
    web_tool = _WebTool(
        loader=_Loader(),
        url_policy=ProductUrlPolicy(_public_resolver),
    )
    mia = Mia(
        settings=settings,
        search_provider=_Search(),
        web_tool=web_tool,
        semantic_mapper=mapper,
    )
    thread_id = "thread-controlled-e2e"
    try:
        response = await asyncio.wait_for(
            mia.message(
                AgentRequest(
                    thread_id=thread_id,
                    message=f"Import product website: {PRODUCT_URL}",
                )
            ),
            timeout=10,
        )
        assert response.status is AgentStatus.AWAITING_REVIEW
        assert mapper.calls == 1
        assert web_tool.download_calls == 0
        assert response.current_product is not None
        assert response.current_product.pending_reviews

        product = response.current_product
        response = await mia.review(
            AgentReviewRequest(
                thread_id=thread_id,
                product_id=product.product_id,
                mapping_cycle_id=product.mapping_cycle_id,
                decisions=tuple(
                    AgentReviewDecision(review_id=item.id, decision="keep")
                    for item in product.pending_reviews
                ),
            )
        )

        human_values = 0
        while response.status is AgentStatus.AWAITING_INPUT:
            assert response.pending_human_request is not None
            assert response.current_product is not None
            requirement_id = response.pending_human_request.requirement_id
            assert requirement_id is not None
            requirement = next(
                item
                for item in response.current_product.template_index.requirements
                if item.id == requirement_id
            )
            response = await mia.provide_value(
                AgentValueRequest(
                    thread_id=thread_id,
                    product_id=response.current_product.product_id,
                    requirement_id=requirement_id,
                    value=_human_value(requirement.value_type, requirement.allowed_values),
                )
            )
            human_values += 1
            assert human_values < 20

        assert response.status is AgentStatus.COMPLETED
        assert mapper.calls == 1
        assert human_values > 0
        keys = {
            item.key for item in mia.context.catalogue.list_artifacts(product_id=product.product_id)
        }
        assert {
            "evidence/product-knowledge.json",
            "mapping/semantic-run.json",
            "mapping/review-decisions.json",
            "mapping/coverage.json",
            "dpp/package.json",
            "aas/validation.json",
        } <= keys

        reused = await mia.message(
            AgentRequest(thread_id=thread_id, message=f"Import product website: {PRODUCT_URL}")
        )
        assert reused.status is AgentStatus.COMPLETED
        assert mapper.calls == 1
        runs = mia.context.catalogue.list_runs(product.product_id)
        assert len(runs) == 2
        assert runs[0].reused_from_run_id == runs[1].id
    finally:
        await mia.close()
