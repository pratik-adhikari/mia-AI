from datetime import UTC, datetime
from pathlib import Path

import pytest

from mia_dpp.domain.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    ProductKnowledgePackage,
    SourceLocation,
    SourceType,
)
from mia_dpp.domain.product import ProductIdentifierRole
from mia_dpp.persistence.catalogue import ProductCatalogue, ProductIdentifierConflict
from mia_dpp.services.product_identifiers import discover_product_identifiers


def _evidence(identifier: str, label: str, value: str) -> EvidenceRecord:
    return EvidenceRecord(
        id=identifier,
        predicate="product.identifier",
        source_label=label,
        value=value,
        source_type=SourceType.WEBSITE,
        source_uri="https://example.com/product",
        source_content_sha256="0" * 64,
        source_location=SourceLocation(excerpt=value),
        extraction_method="fixture",
        extractor_name="fixture",
        extractor_version="1",
        status=EvidenceStatus.OBSERVED,
        acquired_at=datetime.now(UTC),
    )


def test_eclass_is_classification_not_product_identity() -> None:
    package = ProductKnowledgePackage(
        product_id="product-one",
        product_name="Fixture",
        evidence=(
            _evidence("ev-gtin", "GTIN", "4012345678901"),
            _evidence("ev-eclass", "ECLASS IRDI", "0173-1#01-ABC123#001"),
        ),
    )

    identifiers = discover_product_identifiers(package, manufacturer="Example GmbH")

    gtin = next(item for item in identifiers if item.scheme == "gtin")
    eclass = next(item for item in identifiers if item.scheme == "eclass_irdi")
    assert gtin.role is ProductIdentifierRole.IDENTITY
    assert eclass.role is ProductIdentifierRole.CLASSIFICATION


def test_manufacturer_part_number_requires_manufacturer_namespace() -> None:
    package = ProductKnowledgePackage(
        product_id="product-one",
        product_name="Fixture",
        evidence=(_evidence("ev-mpn", "MPN", "RF100-16"),),
    )

    assert discover_product_identifiers(package, manufacturer=None) == ()
    identifier = discover_product_identifiers(package, manufacturer="AFRISO")[0]
    assert identifier.role is ProductIdentifierRole.IDENTITY
    assert identifier.namespace == "afriso"


def test_same_strong_identity_on_two_products_is_flagged_not_merged(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    first, _ = catalogue.get_or_create_product("https://example.com/one", user_id="user-a")
    second, _ = catalogue.get_or_create_product("https://example.com/two", user_id="user-a")

    first_identifier = discover_product_identifiers(
        ProductKnowledgePackage(
            product_id=first.id,
            product_name="One",
            evidence=(_evidence("ev-first", "GTIN", "4012345678901"),),
        ),
        manufacturer="Example",
    )[0]
    second_identifier = first_identifier.model_copy(
        update={"id": "product-identifier-second", "product_id": second.id}
    )

    catalogue.register_product_identifier(first_identifier)
    with pytest.raises(ProductIdentifierConflict) as error:
        catalogue.register_product_identifier(second_identifier)

    assert error.value.existing_product_id == first.id
    assert catalogue.get_product(second.id, user_id="user-a") == second



def test_instance_identifiers_are_private_to_the_account(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/shared-model",
        user_id="user-a",
    )
    catalogue.get_or_create_product(
        "https://example.com/shared-model",
        user_id="user-b",
    )
    serial = discover_product_identifiers(
        ProductKnowledgePackage(
            product_id=product.id,
            product_name="Shared model",
            evidence=(_evidence("ev-serial", "Serial number", "SN-A-123"),),
        ),
        manufacturer="Example",
    )[0]

    stored = catalogue.register_product_identifier(serial, user_id="user-a")

    assert stored.owner_user_id == "user-a"
    assert stored in catalogue.list_product_identifiers(product.id, user_id="user-a")
    assert stored not in catalogue.list_product_identifiers(product.id, user_id="user-b")



def test_two_users_can_store_same_private_serial_independently(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/shared-serial-model",
        user_id="user-a",
    )
    catalogue.get_or_create_product(
        "https://example.com/shared-serial-model",
        user_id="user-b",
    )
    serial = discover_product_identifiers(
        ProductKnowledgePackage(
            product_id=product.id,
            product_name="Shared model",
            evidence=(_evidence("ev-same-serial", "Serial number", "SN-SHARED-1"),),
        ),
        manufacturer="Example",
    )[0]

    stored_a = catalogue.register_product_identifier(serial, user_id="user-a")
    stored_b = catalogue.register_product_identifier(serial, user_id="user-b")

    assert stored_a.id != stored_b.id
    assert stored_a.owner_user_id == "user-a"
    assert stored_b.owner_user_id == "user-b"
    assert stored_a in catalogue.list_product_identifiers(product.id, user_id="user-a")
    assert stored_b in catalogue.list_product_identifiers(product.id, user_id="user-b")
