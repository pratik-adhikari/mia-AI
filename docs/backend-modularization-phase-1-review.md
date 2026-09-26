# Backend Modularization — Phase 1 Review

Branch: `refactor/backend-modular-phase-1`  
Base: `temp`  
Scope: architecture-neutral runtime primitives only

## Goal

Separate reusable runtime responsibilities from LangGraph without changing the existing workflow behavior.

Phase 1 intentionally does **not** extract business logic from workflow nodes, remove all service-to-workflow imports, or add an agentic architecture.

## Final Phase 1 design

```text
runtime/
  services.py      -> orchestration-level dependency composition
  run_context.py   -> immutable execution identity
  run_catalogue.py -> narrow active-run catalogue port
  run_store.py     -> active run persistence/events/fencing

workflow/
  context.py       -> temporary compatibility alias only
  workspace.py     -> graph-state adapter over RunStore
```

### ServiceContainer

`ServiceContainer` moved backend-wide dependency ownership out of `workflow/`.

It is available to orchestration, but it is **not** intended to be injected wholesale into every capability. Individual components should receive only the dependencies they actually require.

### RunContext

`RunContext` contains the fully identified execution:

- `user_id`
- `thread_id`
- `product_id`
- `run_id`

Construction from orchestration state now intentionally requires all four values. Focused tests establish this invariant.

### RunStore

`RunStore` owns:

- generation fencing;
- run lease heartbeat;
- artifact loading;
- model/JSON/binary artifact writes;
- artifact registration;
- event recording.

Its constructor is deliberately narrow:

```python
RunStore(
    context,
    catalogue,
    artifacts,
)
```

It does **not** receive or expose the complete `ServiceContainer`. Generic persistence code therefore cannot reach semantic models, search, JEV, ECLASS, agents, or other unrelated services through the store.

### RunWorkspace

`RunWorkspace` remains a thin LangGraph adapter.

It adds only graph-specific concerns:

- retaining `MiaWorkflowState`;
- resolving artifact IDs stored in graph state;
- `load_state(...)`;
- `load_state_json(...)`;
- temporary `ctx` access for existing graph helpers.

Importantly, it does **not** change the meaning of inherited `RunStore.load_json(...)`.

The contract is now:

```text
RunStore.load_json(artifact_id)
RunWorkspace.load_json(artifact_id)

RunWorkspace.load_state_json(state_key)
```

A `RunWorkspace` therefore remains substitutable anywhere a generic `RunStore` is expected.

## Compatibility

`workflow/context.py` temporarily provides:

```python
MiaContext = ServiceContainer
```

New code must import `ServiceContainer` directly from `mia_dpp.runtime.services`.

The alias has a defined removal point after dependent imports are eliminated in the later orchestration cleanup.

## Focused Phase 1 tests

Added `backend/tests/test_runtime_run_store.py` covering:

- complete `RunContext` construction;
- missing/empty run identity rejection;
- initialization generation fencing;
- initialization lease renewal;
- model round trip;
- JSON round trip;
- artifact registration;
- user identity propagation during artifact lookup;
- stale generation blocking artifact mutations;
- stale generation blocking event mutations;
- `RunWorkspace.state_id(...)` translation;
- `RunWorkspace.load_state_json(...)` translation;
- preservation of generic `load_json(artifact_id)` semantics.

Existing product snapshot tests were adapted to the narrowed store constructor.

## Corrected phase boundary

Phase 1 exit criteria are:

```text
backend-wide runtime primitives are no longer defined by workflow/

api/      ──X──► workflow/context.py
new code  ──X──► workflow.context.MiaContext
```

Phase 2 owns the stronger dependency rule:

```text
services/ ──X──► workflow/
```

This resolves the previous contradiction between the architecture plan and the implementation.

## Remaining deliberate migration debt

The following are **not** Phase 1 defects:

- `DeepResearchService` still imports workflow-owned helpers;
- large workflow nodes still contain business operations;
- `Mia` still performs too much composition work;
- `ServiceContainer` is still broad at the orchestration composition boundary;
- `runtime/checkpoints.py` remains LangGraph-specific until the graph package is isolated;
- no `Orchestrator` protocol exists yet.

The rule going forward is:

> The broad `ServiceContainer` may be available to orchestration, but reusable capabilities receive only the dependencies they actually use.

## Code-size interpretation

Phase 1 reduced **responsibility concentration**, not total repository LOC.

The reusable code previously embedded in `RunWorkspace` was made explicit as `RunContext` and `RunStore`, so a temporary net increase in lines is expected. Future phases should reduce duplication as graph nodes and services converge on shared capabilities.

## Final hardening after re-review

The final Phase 1 hardening pass establishes the execution identity boundary rather than trusting caller-supplied metadata.

### RunContext invariant is enforced by the type

Direct construction and `from_mapping(...)` now both reject empty or whitespace-only identity fields. The invariant no longer depends on one factory method.

### RunStore binds all four identifiers before becoming active

Construction now verifies:

```text
run_id
  ├── belongs to product_id
  ├── belongs to thread_id
  └── thread_id belongs to user_id
```

Only after this durable binding succeeds does `RunStore` assert workflow generation and renew the execution lease.

### RunStore is explicitly an active execution object

Creating `RunStore` has execution semantics:

- validate durable identity;
- require the run to be `RUNNING` or `AWAITING_HUMAN`;
- assert current generation;
- renew the execution lease.

Terminal runs (`COMPLETED`, `FAILED`, `INCOMPLETE`, `REUSED`) are rejected.

It is **not** a generic historical reader or report/query object. Future read-only inspection should use a separate query/reader abstraction instead of constructing `RunStore`.

### RunCatalogue protocol

`RunStore` now depends on the narrow `RunCatalogue` protocol rather than concrete `ProductCatalogue`.

The protocol exposes only:

- run/thread lookup needed for identity binding;
- generation assertion;
- lease renewal;
- artifact lookup/registration;
- event recording.

This makes the persistence boundary machine-checkable and prevents `RunStore` from growing arbitrary catalogue responsibilities.

### Product identity ownership

`canonical_product_url(...)` moved from `workflow/identity.py` to `domain/product_identity.py`.

`ProductCatalogue` now depends on the architecture-neutral domain helper, so persistence no longer imports the workflow package through URL canonicalization. `workflow.identity` temporarily re-exports the function for compatibility while retaining graph/application-oriented `direct_product_url(...)`.

### Real-catalogue integration coverage

The Phase 1 runtime tests now include a real SQLite `ProductCatalogue` and `LocalArtifactStore` integration case covering:

- valid execution identity;
- wrong product rejection;
- wrong thread rejection;
- wrong user rejection;
- artifact persistence after successful binding;
- event persistence after successful binding;
- `RUNNING` and `AWAITING_HUMAN` activation;
- rejection of terminal run statuses.

## Validation

GitHub Actions has previously failed on this branch before exposing executable job steps or retrievable logs, so those runs did not provide a meaningful quality signal.

After this correction pass, the required local verification remains:

```bash
git switch refactor/backend-modular-phase-1
make check
```

Do not proceed to Phase 2 until the current Phase 1 head passes the repository quality gates or any failures are reviewed and fixed.

## Phase 2 after approval

Phase 2 removes upward workflow dependencies from reusable services, beginning with `DeepResearchService`:

- move package/evidence merge logic out of workflow nodes;
- replace service use of `RunWorkspace` with `RunStore`;
- inject explicit narrow dependencies;
- establish and test the rule that `services/` cannot import `workflow/`.

No large graph-node extraction should begin until that dependency direction is clean.
