# Backend Modularization — Phase 1 Review

Branch: `refactor/backend-modular-phase-1`  
Base: `temp`  
Scope: architecture-neutral runtime primitives only

## Goal

Separate reusable runtime responsibilities from LangGraph without changing the existing workflow behavior.

Phase 1 intentionally does **not** extract business logic from workflow nodes and does **not** add an agentic architecture.

## What changed

### 1. Architecture-neutral ServiceContainer

Added:

`backend/src/mia_dpp/runtime/services.py`

`ServiceContainer` now owns reusable backend dependencies such as:

- catalogue
- artifact store
- templates
- extraction/search dependencies
- mapping review
- semantic mapper
- JEV
- ECLASS
- semantic policy/configuration

The application, API view, graph, workflow nodes, and deep research now type against the runtime container instead of defining backend-wide dependencies inside `workflow/context.py`.

### 2. Architecture-neutral RunContext

Added:

`backend/src/mia_dpp/runtime/run_context.py`

`RunContext` contains only stable run identity:

- user_id
- thread_id
- product_id
- run_id

It can be built from any mapping and therefore does not depend on LangGraph state.

### 3. Architecture-neutral RunStore

Added:

`backend/src/mia_dpp/runtime/run_store.py`

`RunStore` owns reusable run-scoped infrastructure:

- generation fencing
- run lease heartbeat
- artifact loading
- JSON/model/binary artifact writes
- artifact registration
- event recording

This logic previously lived in `workflow/workspace.py`.

### 4. RunWorkspace reduced to a graph adapter

`workflow/workspace.py` now subclasses `RunStore`.

It keeps only LangGraph-specific conveniences:

- retaining graph state
- resolving artifact IDs from state keys
- `load_state(...)`
- graph-state-key based `load_json(...)`

The reusable persistence implementation is no longer owned by the workflow package.

### 5. workflow/context.py reduced to compatibility only

The old `MiaContext` implementation was removed.

`workflow/context.py` now provides only a temporary alias:

```python
MiaContext = ServiceContainer
```

New code must import `ServiceContainer` from `mia_dpp.runtime.services`.

The shim exists only to avoid unnecessary breakage during incremental refactoring.

### 6. Existing graph adapted without changing orchestration

`workflow/graph.py` and the current node type annotations now use `ServiceContainer`.

No graph edges, routing decisions, semantic behavior, JEV behavior, persistence policy, review behavior, or AAS behavior were intentionally changed.

## Responsibility change

Before:

```text
workflow/
  context.py      -> backend-wide dependency container
  workspace.py    -> run identity + fencing + persistence + graph-state helpers
```

After:

```text
runtime/
  services.py     -> shared dependency container
  run_context.py  -> shared run identity
  run_store.py    -> shared run persistence/events/fencing

workflow/
  context.py      -> compatibility alias only
  workspace.py    -> LangGraph adapter only
```

## Why this matters

Future execution architectures can now reuse the same runtime primitives:

```text
LangGraph ---------┐
                   │
Agentic controller ├──> ServiceContainer
                   ├──> RunContext
GUI pipeline ------├──> RunStore
                   │
CLI / evaluator ---┘
```

They do not need to construct fake LangGraph state merely to access artifacts, events, or durable run identity.

## What was deliberately NOT changed

- DeepResearchService still uses `RunWorkspace` and a workflow-owned merge helper.
- Large graph nodes still contain business operations.
- `Mia` still performs too much composition work.
- No `Orchestrator` protocol exists yet.
- No capability/service extraction has started.
- No agentic controller or GUI pipeline model was added.

Those belong to later phases.

## Validation status

GitHub CI was triggered for the branch head.

The workflow run concluded as failure before exposing any executable job steps or retrievable job logs. The repository therefore does **not** currently provide a usable CI result for this phase through GitHub Actions.

The connector environment used for this refactor cannot clone GitHub into a local shell, so `make check` could not be independently executed here.

This phase should therefore be reviewed and locally verified with:

```bash
git switch refactor/backend-modular-phase-1
make check
```

before starting Phase 2.

## Review focus

Please review these questions before Phase 2:

1. Is `runtime/` the correct ownership layer for shared execution dependencies?
2. Is the split between `RunStore` and graph-only `RunWorkspace` understandable?
3. Should the temporary `MiaContext` compatibility alias remain for one more phase or be removed immediately after local verification?
4. Are the names `ServiceContainer`, `RunContext`, and `RunStore` clear enough for the future GUI/agentic architecture?

## Next phase after approval

Phase 2 will remove upward workflow dependencies from reusable services, starting with `DeepResearchService`:

- move evidence/package merge logic out of workflow nodes;
- replace `RunWorkspace` use inside services with `RunStore`;
- inject explicit reusable dependencies;
- establish the rule that `services/` cannot import `workflow/`.

No large graph-node extraction should begin until that dependency direction is clean.
