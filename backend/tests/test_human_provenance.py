from mia_dpp.aas.requirements import build_template_index
from mia_dpp.aas.templates import OfficialTemplateRepository
from mia_dpp.domain.evidence import ProductKnowledgePackage
from mia_dpp.domain.mappings import MappingResult
from mia_dpp.tools.mapping.review import MappingReviewService


def _inventory():
    repository = OfficialTemplateRepository()
    return repository, build_template_index((repository.load("digital_nameplate"),))


def test_dummy_value_keeps_explicit_human_provenance() -> None:
    repository, index = _inventory()
    requirement = next(
        item for item in index.requirements
        if item.required and item.semantic_id is not None and not item.wildcard
    )
    package = ProductKnowledgePackage(
        product_id="product-dummy",
        product_name="Dummy fixture",
        evidence=(),
    )
    service = MappingReviewService(repository)

    package, result = service.record_human_value(
        package,
        MappingResult(),
        index,
        requirement_id=requirement.id,
        value="",
        thread_id="thread-dummy",
        actor_name="Pratik",
        use_dummy=True,
    )

    evidence = package.evidence[-1]
    mapping = result.mapped[-1]
    assert evidence.human_actor_name == "Pratik"
    assert evidence.human_value_kind == "dummy"
    assert evidence.predicate == "human.dummy"
    assert mapping.human_actor_name == "Pratik"
    assert mapping.human_value_kind == "dummy"
