"""Durable product identity/history behavior independent of the agent runtime."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mia_dpp.domain.product import MessageRole, RunStatus
from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.workflow.identity import canonical_product_url


def test_canonical_url_removes_tracking_and_normalizes_host() -> None:
    left = "HTTPS://WWW.Example.com/products/42/?utm_source=x&variant=red#details"
    right = "https://example.com/products/42?variant=red"
    assert canonical_product_url(left) == canonical_product_url(right)


def test_same_product_url_reuses_product_identity(tmp_path: Path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    first, created = catalogue.get_or_create_product(
        "https://www.example.com/product/42/?utm_source=mail"
    )
    second, created_again = catalogue.get_or_create_product("https://example.com/product/42")
    assert created is True
    assert created_again is False
    assert second.id == first.id


def test_concurrent_product_resolution_creates_one_identity(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"

    def resolve(_: int):
        return ProductCatalogue(path).get_or_create_product(
            "https://example.com/product/42?utm_source=concurrent"
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        resolved = tuple(executor.map(resolve, range(8)))

    assert len({product.id for product, _ in resolved}) == 1
    assert sum(created for _, created in resolved) == 1


def test_attempts_messages_and_versions_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"
    catalogue = ProductCatalogue(path)
    product, _ = catalogue.get_or_create_product("https://example.com/product/42")
    failed = catalogue.start_run(product.id, "thread-catalogue")
    catalogue.add_message("thread-catalogue", MessageRole.USER, "Create the DPP", run_id=failed.id)
    catalogue.finish_run(failed.id, RunStatus.FAILED, error="fixture failure")
    successful = catalogue.start_run(product.id, "thread-catalogue")
    version = catalogue.create_dpp_version(
        product.id,
        successful.id,
        dpp_artifact_id="artifact-dpp",
        deployable=True,
    )
    catalogue.finish_run(successful.id, RunStatus.COMPLETED)

    restarted = ProductCatalogue(path)
    assert [item.status for item in restarted.list_runs(product.id)] == [
        RunStatus.COMPLETED,
        RunStatus.FAILED,
    ]
    assert restarted.list_messages("thread-catalogue")[0].timestamp.tzinfo is not None
    assert restarted.latest_successful_dpp(product.id) == version


def test_concurrent_dpp_versions_are_unique_and_sequential(tmp_path: Path) -> None:
    path = tmp_path / "catalogue.sqlite3"
    catalogue = ProductCatalogue(path)
    product, _ = catalogue.get_or_create_product("https://example.com/product/versioned")
    runs = tuple(catalogue.start_run(product.id, f"thread-{index}") for index in range(6))

    def create(index: int):
        return ProductCatalogue(path).create_dpp_version(
            product.id,
            runs[index].id,
            dpp_artifact_id=f"artifact-{index}",
            deployable=True,
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        versions = tuple(executor.map(create, range(len(runs))))

    assert sorted(item.version for item in versions) == list(range(1, len(runs) + 1))
