# Backend Modularization — Phase 2 Review

Branch: `refactor/backend-modular-phase-2`  
Base: `refactor/backend-modular-phase-1@41dbc2c10d0fbfac8ca39eaed315b40870caaa1e`  
Scope: remove reusable-service dependencies on the concrete workflow package

## Goal

Phase 2 makes the service layer independent of LangGraph/workflow implementation details.

The required dependency direction is now:

```text
workflow / future agent / future GUI pipeline
                    │
                    ▼
                 services
                    │
                    ▼
           runtime + domain + ports
```

and never:

```text
services ──X──► workflow
```

No graph topology, semantic policy, mapping policy, JEV thresholds, ECLASS behavior, review semantics, or AAS behavior is intentionally changed in this phase.

## What changed

### 1. Evidence package merging moved out of workflow

Before:

```text
workflow/nodes_product.py
    merge_packages(...)

services/deep_research.py
    imports workflow.nodes_product.merge_packages
```

After:

```text
domain/evidence_merge.py
    merge_packages(...)

workflow/nodes_product.py ─┐
workflow/nodes_mapping.py ├──► domain.evidence_merge
services/deep_research.py ┘
```

The merge operation is deterministic and orchestration-neutral, so it now belongs with the evidence domain rather than inside a graph node.

A focused test verifies both duplicate policies:

- incoming evidence replaces existing evidence by default;
- `preserve_existing=True` preserves the existing reviewed/current value.

### 2. DeepResearchService no longer imports workflow

Removed dependencies:

```text
workflow.workspace.RunWorkspace
workflow.nodes_product.merge_packages
workflow context ownership
```

The service now imports only architecture-neutral/domain/runtime dependencies.

### 3. RunWorkspace removed from DeepResearchService

Before:

```text
BackgroundJob
    ↓
fake MiaWorkflowState dict
    ↓
RunWorkspace
    ↓
RunStore
```

After:

```text
BackgroundJob
    ↓
RunContext
    ↓
RunStore
```

The background job already contains:

- `user_id`
- `thread_id`
- `product_id`
- `run_id`

so manufacturing graph state solely to obtain persistence access was unnecessary.

The service now constructs:

```python
RunStore(
    RunContext(
        user_id=job.user_id,
        thread_id=job.thread_id,
        product_id=job.product_id,
        run_id=job.run_id,
    ),
    catalogue,
    artifacts,
)
```

This also means it inherits the Phase 1 active-execution contract:

- durable identity binding;
- current workflow generation;
- `RUNNING` or `AWAITING_HUMAN`;
- lease renewal and mutation fencing.

### 4. ServiceContainer removed from DeepResearchService

Before:

```python
DeepResearchService(context: ServiceContainer)
```

The service could therefore access every backend dependency, including dependencies it did not own.

After:

```python
DeepResearchService(
    catalogue=...,
    artifacts=...,
    templates=...,
    web_tool=...,
    mapping_review=...,
    semantic_mapper=...,
    jev_decider=...,
    jev_mapping_enabled=...,
    jev_routing_scopes=...,
    jev_routing_max_concurrency=...,
    jev_decision_policy=...,
)
```

This is intentionally explicit.

It avoids replacing one broad context object with another differently named broad context object.

The application composition root (`mia.py`) is responsible for taking dependencies from `ServiceContainer` and injecting the subset required by deep research.

### 5. Architectural dependency regression test

Added:

`backend/tests/test_architecture_boundaries.py`

It parses every Python file under:

`backend/src/mia_dpp/services/`

and fails if a reusable service imports:

`mia_dpp.workflow`

or anything below it.

This turns the Phase 2 dependency rule into an executable repository constraint.

## Responsibility change

Before:

```text
DeepResearchService
    │
    ├── ServiceContainer
    ├── RunWorkspace
    └── workflow-owned evidence merge
             │
             ▼
          workflow/
```

After:

```text
DeepResearchService
    │
    ├── explicit research dependencies
    ├── RunContext
    ├── RunStore
    └── domain.evidence_merge

No workflow dependency
```

## Why this matters for the future LEGO architecture

The same research service can now be invoked by:

```text
LangGraph node ─────────┐
Global LLM tool ────────┤
GUI pipeline node ──────┼──► DeepResearchService
Local-model workflow ───┤
Batch/evaluator ────────┘
```

without constructing LangGraph state or importing graph code.

This is the first concrete proof that the planned shared-capability architecture can support multiple orchestrators.

## What Phase 2 deliberately does not change

- Deep research remains one relatively large service.
- Mapping logic inside deep research is not yet extracted into a shared MappingCapability.
- `ProductCatalogue` remains the concrete catalogue dependency of DeepResearchService because research uses many background-job/catalogue operations.
- `ServiceContainer` remains broad at the application/orchestration composition boundary.
- Graph nodes remain large.
- `Mia` remains the central composition root.
- `runtime/checkpoints.py` remains LangGraph-specific.
- No agentic architecture or GUI pipeline representation is added.

Those belong to later phases.

## Tests added/updated

Phase 2 adds or updates coverage for:

- architecture rule: services cannot import workflow;
- architecture-neutral evidence package merging;
- deep-research recovery using RunStore directly;
- existing workflow callers using the same neutral merge function.

## Phase 2 exit criteria

The phase is complete when:

```text
services/ ──X──► workflow/
```

is true and enforced by tests.

Specifically:

- `DeepResearchService` has no `ServiceContainer`, `RunWorkspace`, or `MiaWorkflowState` dependency;
- shared evidence merge logic has neutral ownership;
- application code explicitly injects required deep-research dependencies;
- recovery behavior remains generation-fenced through `RunStore`.

## Validation status

The required quality gate remains:

```bash
git switch refactor/backend-modular-phase-2
make check
```

GitHub Actions in this repository has been terminating without usable job steps/logs, so a failed Actions badge alone is not treated as evidence of a code failure.

Do not start Phase 3 until the current Phase 2 head passes the repository quality gates or any failures are reviewed.

## Review focus

Before Phase 3, review these questions:

1. Is `domain/evidence_merge.py` the right neutral owner for deterministic package merging?
2. Is the explicit `DeepResearchService` constructor understandable, even though it is intentionally longer than the old context-based constructor?
3. Does replacing graph-state-backed `RunWorkspace` with direct `RunStore` preserve the intended recovery/fencing semantics?
4. Is the architectural test strong enough to prevent reusable services from drifting back toward workflow coupling?

## Next phase after approval

Phase 3 should begin extracting large business operations from graph nodes into reusable capabilities.

The recommended first target remains product identity/reuse + extraction, because `workflow/nodes_product.py` currently combines:

- orchestration;
- product/run identity;
- reuse decisions;
- crawling/extraction;
- artifact persistence;
- product metadata updates;
- snapshot updates;
- state translation.

Phase 3 should preserve behavior while turning graph nodes into thin adapters over reusable capabilities.
