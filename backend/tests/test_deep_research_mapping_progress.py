"""Deferred research evidence keeps mapping independently of human review."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mia_dpp.domain.mappings import MappingResult
from mia_dpp.services import deep_research as deep_research_module
from mia_dpp.services.deep_research import DeepResearchService


@pytest.mark.parametrize("mapping_pending", [True, False])
def test_exhausted_frontier_retries_deferred_mapping(mapping_pending: bool) -> None:
    service = object.__new__(DeepResearchService)
    package = object()
    calls: list[dict[str, object]] = []
    service._frontier = lambda job, work, seed_id: ((), "sources-1", 1)

    async def map_new(work, selected_package, **kwargs):
        assert selected_package is package
        calls.append(kwargs)
        return {"mappingRefreshPending": mapping_pending}

    service._map_new_evidence = map_new
    work = SimpleNamespace(load=lambda artifact_id, model: package)
    job = SimpleNamespace(
        metadata={
            "seedEvidenceArtifactId": "seed-1",
            "researchEvidenceArtifactId": "research-1",
        }
    )

    metadata, complete = asyncio.run(service._process_batch(job, work, iteration=2))

    assert calls == [
        {
            "added_ids": set(),
            "evidence_artifact_id": "research-1",
            "seed_evidence_artifact_id": "seed-1",
        }
    ]
    assert metadata["mappingRefreshPending"] is mapping_pending
    assert complete is not mapping_pending


def test_last_source_batch_remains_queued_when_targets_are_missing() -> None:
    service = object.__new__(DeepResearchService)
    source = SimpleNamespace(url="https://example.test/spec", title="Specs", text="Specifications")
    service._frontier = lambda job, work, seed_id: ((source,), "sources-1", 0)

    async def extract(link, *, seed_url):
        return link, None, None, 0.0

    async def map_new(work, package, **kwargs):
        return {"mappingRefreshPending": True}

    service._extract_timed = extract
    service._map_new_evidence = map_new
    updates: list[dict[str, object]] = []
    service._catalogue = SimpleNamespace(
        update_background_job=lambda *args, **kwargs: updates.append(kwargs["metadata"]),
    )
    work = SimpleNamespace(
        load=lambda artifact_id, model: object(),
        event=lambda *args, **kwargs: None,
    )
    job = SimpleNamespace(
        id="job-1",
        user_id="user-1",
        metadata={
            "seedEvidenceArtifactId": "seed-1",
            "seedUrl": "https://example.test/product",
        },
    )

    metadata, complete = asyncio.run(service._process_batch(job, work, iteration=1))

    assert metadata["processedSources"] == metadata["totalSources"] == 1
    assert metadata["mappingRefreshPending"] is True
    assert complete is False
    assert updates[0]["nextSourceIndex"] == 1


def test_deferred_evidence_maps_after_targets_arrive_without_review(monkeypatch) -> None:
    service = object.__new__(DeepResearchService)
    seed = SimpleNamespace(evidence=(SimpleNamespace(id="seed-fact"),))
    research = SimpleNamespace(
        evidence=(
            SimpleNamespace(id="seed-fact"),
            SimpleNamespace(id="new-fact"),
        ),
        model_copy=lambda update: SimpleNamespace(**update),
    )
    index = object()
    service._jev_mapping_enabled = True
    service._semantic_mapper = None
    service._jev_decider = object()
    service._templates = object()
    service._jev_routing_scopes = ()
    service._jev_routing_max_concurrency = 1
    service._jev_decision_policy = object()
    service._latest_mapping_artifact = lambda work: None
    service._latest_artifact = lambda work, key: SimpleNamespace(id="targets-1")
    called: list[set[str]] = []

    async def map_without_model_calls(**kwargs):
        called.append(kwargs["evidence_ids"])
        return MappingResult(), (), SimpleNamespace(traces=()), object()

    monkeypatch.setattr(deep_research_module, "map_new_jev_evidence", map_without_model_calls)
    monkeypatch.setattr(deep_research_module, "coverage", lambda *args, **kwargs: object())
    work = SimpleNamespace(
        load=lambda artifact_id, model: seed if artifact_id == "seed-1" else index,
        put_model=lambda key, model, **kwargs: f"artifact-{key}",
        event=lambda *args, **kwargs: None,
    )

    metadata = asyncio.run(
        service._map_new_evidence(
            work,
            research,
            added_ids=set(),
            evidence_artifact_id="research-1",
            seed_evidence_artifact_id="seed-1",
        )
    )

    assert called == [{"new-fact"}]
    assert metadata["mappingRefreshPending"] is False
    assert metadata["integratedMappingArtifactId"] == "artifact-mapping/research-integrated.json"
