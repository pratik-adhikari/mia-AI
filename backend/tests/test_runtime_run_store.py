from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from mia_dpp.persistence.catalogue import ProductCatalogue
from mia_dpp.runtime.run_context import RunContext
from mia_dpp.runtime.run_store import RunStore
from mia_dpp.storage.local import LocalArtifactStore
from mia_dpp.storage.models import StoredArtifact
from mia_dpp.workflow.workspace import RunWorkspace


class FixtureModel(BaseModel):
    value: int


class FakeCatalogue:
    def __init__(self) -> None:
        self.assertions: list[str] = []
        self.renewals: list[str] = []
        self.events: list[tuple[str, str, str]] = []
        self.registered: list[StoredArtifact] = []
        self.artifacts: dict[str, StoredArtifact] = {}
        self.fail_generation = False
        self.last_artifact_user_id: str | None = None

    def get_run(self, run_id: str):
        if run_id != "run-a":
            return None
        return SimpleNamespace(id=run_id, product_id="product-a", thread_id="thread-a")

    def get_thread(self, thread_id: str, *, user_id: str):
        if thread_id == "thread-a" and user_id == "user-a":
            return SimpleNamespace(id=thread_id, user_id=user_id)
        return None

    def assert_run_generation(self, run_id: str):
        self.assertions.append(run_id)
        if self.fail_generation:
            raise RuntimeError("workflow generation is stale")
        return self.get_run(run_id)

    def renew_run_lease(self, run_id: str):
        self.renewals.append(run_id)
        return self.get_run(run_id)

    def get_artifact(self, artifact_id: str, *, user_id: str | None = None):
        self.last_artifact_user_id = user_id
        return self.artifacts.get(artifact_id)

    def register_artifact(self, artifact: StoredArtifact):
        self.registered.append(artifact)
        self.artifacts[artifact.id] = artifact
        return artifact

    def add_event(
        self,
        run_id: str,
        event_type: str,
        summary: str,
        *,
        metadata=None,
    ):
        self.events.append((run_id, event_type, summary))
        return SimpleNamespace(run_id=run_id, event_type=event_type, summary=summary)


class FakeArtifactStore:
    def __init__(self) -> None:
        self.bytes_by_id: dict[str, bytes] = {}
        self._next_id = 0

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        product_id: str | None = None,
        run_id: str | None = None,
        derived_from: tuple[str, ...] = (),
    ) -> StoredArtifact:
        self._next_id += 1
        artifact_id = f"artifact-{self._next_id}"
        artifact = StoredArtifact(
            id=artifact_id,
            key=key,
            content_type=content_type,
            sha256="0" * 64,
            size=len(data),
            storage_uri=artifact_id,
            product_id=product_id,
            run_id=run_id,
            derived_from=derived_from,
        )
        self.bytes_by_id[artifact_id] = data
        return artifact

    def get(self, artifact: StoredArtifact) -> bytes:
        return self.bytes_by_id[artifact.id]

    def exists(self, artifact: StoredArtifact) -> bool:
        return artifact.id in self.bytes_by_id


def _context() -> RunContext:
    return RunContext(
        user_id="user-a",
        thread_id="thread-a",
        product_id="product-a",
        run_id="run-a",
    )


def test_run_context_enforces_identity_on_every_construction_path() -> None:
    assert RunContext.from_mapping(
        {
            "user_id": "user-a",
            "thread_id": "thread-a",
            "product_id": "product-a",
            "run_id": "run-a",
        }
    ) == _context()

    for key in ("user_id", "thread_id", "product_id", "run_id"):
        values = {
            "user_id": "user-a",
            "thread_id": "thread-a",
            "product_id": "product-a",
            "run_id": "run-a",
        }
        values.pop(key)
        with pytest.raises(KeyError, match=key):
            RunContext.from_mapping(values)

    with pytest.raises(ValueError, match="run_id"):
        RunContext.from_mapping(
            {
                "user_id": "user-a",
                "thread_id": "thread-a",
                "product_id": "product-a",
                "run_id": "",
            }
        )

    with pytest.raises(ValueError, match="user_id"):
        RunContext(
            user_id="   ",
            thread_id="thread-a",
            product_id="product-a",
            run_id="run-a",
        )


@pytest.mark.parametrize("field", ("user_id", "thread_id", "product_id", "run_id"))
def test_run_context_direct_constructor_rejects_each_blank_identity_field(field: str) -> None:
    values = {
        "user_id": "user-a",
        "thread_id": "thread-a",
        "product_id": "product-a",
        "run_id": "run-a",
    }
    values[field] = "   "

    with pytest.raises(ValueError, match=field):
        RunContext(**values)


def test_run_store_rejects_identity_mismatch_before_renewing_lease() -> None:
    catalogue = FakeCatalogue()
    context = RunContext(
        user_id="user-a",
        thread_id="thread-a",
        product_id="wrong-product",
        run_id="run-a",
    )

    with pytest.raises(ValueError, match="belongs to product"):
        RunStore(context, catalogue, FakeArtifactStore())

    assert catalogue.assertions == []
    assert catalogue.renewals == []


def test_run_store_initialization_validates_binding_then_renews_lease() -> None:
    catalogue = FakeCatalogue()
    store = RunStore(_context(), catalogue, FakeArtifactStore())

    assert store.run_id == "run-a"
    assert catalogue.assertions == ["run-a"]
    assert catalogue.renewals == ["run-a"]


def test_run_store_round_trips_model_and_json_by_artifact_id() -> None:
    catalogue = FakeCatalogue()
    artifacts = FakeArtifactStore()
    store = RunStore(_context(), catalogue, artifacts)

    model_id = store.put_model("model.json", FixtureModel(value=7))
    json_id = store.put_json("payload.json", {"answer": 42})

    assert store.load(model_id, FixtureModel) == FixtureModel(value=7)
    assert store.load_json(json_id) == {"answer": 42}
    assert catalogue.last_artifact_user_id == "user-a"
    assert {item.id for item in catalogue.registered} == {model_id, json_id}


def test_run_store_fences_before_artifact_and_event_mutations() -> None:
    catalogue = FakeCatalogue()
    artifacts = FakeArtifactStore()
    store = RunStore(_context(), catalogue, artifacts)

    catalogue.fail_generation = True

    with pytest.raises(RuntimeError, match="workflow generation"):
        store.put_json("blocked.json", {"blocked": True})
    with pytest.raises(RuntimeError, match="workflow generation"):
        store.event("blocked", "must not be recorded")

    assert catalogue.registered == []
    assert catalogue.events == []


def test_run_workspace_preserves_store_contract_and_translates_state_keys() -> None:
    catalogue = FakeCatalogue()
    artifacts = FakeArtifactStore()
    ctx = SimpleNamespace(catalogue=catalogue, artifacts=artifacts)
    state = {
        "user_id": "user-a",
        "thread_id": "thread-a",
        "product_id": "product-a",
        "run_id": "run-a",
    }
    workspace = RunWorkspace(state, ctx)

    artifact_id = workspace.put_json("review.json", {"decision": "confirm"})
    state["review_items_artifact_id"] = artifact_id

    assert workspace.load_json(artifact_id) == {"decision": "confirm"}
    assert workspace.load_state_json("review_items_artifact_id") == {
        "decision": "confirm"
    }
    assert workspace.state_id("review_items_artifact_id") == artifact_id


def test_run_store_binds_real_catalogue_execution_identity(tmp_path) -> None:
    catalogue = ProductCatalogue(tmp_path / "catalogue.sqlite3")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    catalogue.get_or_create_thread("thread-a", "user-a")
    product, _ = catalogue.get_or_create_product(
        "https://example.com/product-a",
        user_id="user-a",
    )
    run = catalogue.start_run(product.id, "thread-a", user_id="user-a")

    valid = RunContext(
        user_id="user-a",
        thread_id="thread-a",
        product_id=product.id,
        run_id=run.id,
    )
    store = RunStore(valid, catalogue, artifacts)
    artifact_id = store.put_json("identity.json", {"valid": True})
    store.event("runtime.identity_validated", "Validated execution identity.")

    assert store.load_json(artifact_id) == {"valid": True}
    assert len(catalogue.list_events(run.id, user_id="user-a")) == 1

    with pytest.raises(ValueError, match="belongs to product"):
        RunStore(
            RunContext(
                user_id="user-a",
                thread_id="thread-a",
                product_id="wrong-product",
                run_id=run.id,
            ),
            catalogue,
            artifacts,
        )

    with pytest.raises(ValueError, match="belongs to thread"):
        RunStore(
            RunContext(
                user_id="user-a",
                thread_id="wrong-thread",
                product_id=product.id,
                run_id=run.id,
            ),
            catalogue,
            artifacts,
        )

    with pytest.raises(PermissionError, match="does not belong to user"):
        RunStore(
            RunContext(
                user_id="wrong-user",
                thread_id="thread-a",
                product_id=product.id,
                run_id=run.id,
            ),
            catalogue,
            artifacts,
        )
