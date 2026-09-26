from mia_dpp.domain.evidence import EvidenceRecord, ProductKnowledgePackage
from mia_dpp.domain.evidence_merge import merge_packages


def _record(record_id: str, value: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=record_id,
        predicate="name",
        value=value,
        source_url="https://example.com/product",
        source_label="Name",
    )


def test_merge_packages_preserves_or_replaces_duplicate_evidence_by_policy() -> None:
    existing = ProductKnowledgePackage(
        product_id="product-a",
        evidence=(_record("evidence-1", "existing"),),
    )
    incoming = ProductKnowledgePackage(
        product_id="product-a",
        evidence=(
            _record("evidence-1", "incoming"),
            _record("evidence-2", "new"),
        ),
    )

    replaced = merge_packages(existing, incoming)
    preserved = merge_packages(existing, incoming, preserve_existing=True)

    assert {item.id: item.value for item in replaced.evidence} == {
        "evidence-1": "incoming",
        "evidence-2": "new",
    }
    assert {item.id: item.value for item in preserved.evidence} == {
        "evidence-1": "existing",
        "evidence-2": "new",
    }
