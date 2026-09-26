# Backend Architecture Refinement Plan

Status: implemented on refactor branch; agentic/GUI executors remain future work  
Branch: `refactor/lego-backend`  
Base: `develop@96b8ea0ba7745836bc11fa4a8ebed016a3b1df25`

## 1. Purpose

The backend needs to become easier to understand, modify, and recombine before adding a second agentic architecture.

The target is a LEGO-style backend:

- business capabilities are reusable blocks;
- orchestration decides only **when** and **in what order** blocks are used;
- the current LangGraph workflow remains one orchestration implementation;
- a future single-controller LLM becomes another orchestration implementation;
- both architectures reuse the same extraction, semantic, mapping, research, validation, persistence, and AAS components;
- duplicated code and duplicated responsibility are reduced;
- each component can be understood and tested independently.

This cleanup is required before significant agentic work. Otherwise the repository would evolve into two separate backends with duplicated logic.

---

## 2. Why the cleanup is needed

The current backend already contains many strong reusable pieces, but the dependency boundaries are inconsistent.

Examples from the current code:

- `workflow/context.py::MiaContext` is effectively a global dependency container, but it lives inside the workflow package.
- `services/deep_research.py` imports:
  - `workflow.context.MiaContext`
  - `workflow.workspace.RunWorkspace`
  - `workflow.nodes_product.merge_packages`
- `api/agent_view.py` imports `workflow.context.MiaContext`.
- large workflow nodes perform business logic, persistence, event recording, fingerprinting, snapshot updates, and graph-state mutation together.
- `mia.py` currently composes models, providers, persistence, semantic components, graph runtime, conversation logic, recovery, debugging, and response construction.

This creates three problems.

### 2.1 Repeated responsibilities

The same categories of work appear in several places:

| Responsibility | Current examples |
|---|---|
| Artifact loading/writing | `RunWorkspace`, workflow nodes, deep research |
| Event recording | workflow nodes, deep research |
| Semantic mapping decisions | workflow nodes, semantic modules, deep research |
| Evidence merging | workflow node helper reused by service |
| Runtime dependency access | `MiaContext` used by workflow, API, service |
| Model/provider construction | concentrated inside `Mia.__init__` |
| Run/product persistence | workflow nodes and services |
| Mapping refresh/integration | workflow mapping flow and deep research flow |

The goal is not only fewer lines of code. The goal is to have **one clear owner for each responsibility**.

### 2.2 Business logic is partially graph-shaped

A reusable operation should ideally be callable independently:

```python
result = await extraction.extract(request)
```

Today some operations are only naturally callable through a graph node because the node also handles:

- graph state;
- artifact IDs;
- event recording;
- snapshot updates;
- run fencing;
- routing-specific flags.

That makes experimentation harder.

### 2.3 A second architecture would amplify the problem

If the agentic architecture is added now, it would likely need its own versions of:

- extraction flow;
- evidence persistence;
- semantic mapping integration;
- JEV retry logic;
- research integration;
- coverage calculation;
- build/validation handling.

That would produce:

```text
Graph backend logic
+
Agent backend logic
+
shared code in some places
+
duplicated code in other places
```

The refined architecture should instead produce:

```text
Shared backend capabilities
        ▲
        │
   ┌────┴────┐
   │         │
Graph     Agentic
```

---

## 3. Architectural goal

The backend should have five clearly separated levels.

| Level | Purpose | May know about orchestration? |
|---|---|---:|
| API / Application | HTTP boundary, use-case entry points, response construction | Yes |
| Orchestration | Graph, agentic, future experimental architectures | Yes |
| Capabilities | Product work: extraction, mapping, research, AAS, review | No |
| Ports | Interfaces for replaceable dependencies | No |
| Adapters / Infrastructure | OpenRouter, Crawl4AI, ECLASS, DB, blob storage | No |

The most important dependency rule is:

> **Orchestrators may depend on capabilities. Capabilities must never depend on orchestrators.**

That means:

```text
graph     ─────► extraction
graph     ─────► semantic
agentic   ─────► extraction
agentic   ─────► semantic

research  ──X──► graph
semantic  ──X──► agentic
storage   ──X──► workflow
```

---

## 4. Refined backend hierarchy

### 4.1 Logical hierarchy

| Layer | Main responsibility | Example contents |
|---|---|---|
| 1. API | Transport only | routes, schemas, auth |
| 2. Application | Start/resume runs, select architecture, build response | `MiaApplication`, `ArchitectureRouter`, `ResponseBuilder` |
| 3. Orchestration | Decide operation order and branching | `GraphOrchestrator`, future `AgenticOrchestrator` |
| 4. Capabilities | Perform reusable domain operations | extraction, normalization, semantic routing, mapping, research, coverage, AAS |
| 5. Runtime | Run identity, artifact/event access, dependency container | `RunContext`, `RunStore`, `ServiceContainer` |
| 6. Ports | Stable interfaces for swappable dependencies | `SearchProvider`, `JevDecisionClient`, `ArtifactStore` |
| 7. Adapters | Concrete external implementations | DDGS, OpenRouter, Crawl4AI, ECLASS API/XML, SQLite/Postgres |
| 8. Domain | Core types and invariants | evidence, mappings, product, targets |

### 4.2 Proposed package hierarchy

```text
backend/src/mia_dpp/

├── domain/
│   ├── evidence.py
│   ├── mappings.py
│   ├── product.py
│   ├── product_work.py
│   └── targets.py
│
├── capabilities/
│   ├── discovery/
│   ├── extraction/
│   ├── normalization/
│   ├── semantic/
│   ├── mapping/
│   ├── research/
│   ├── coverage/
│   ├── aas/
│   ├── review/
│   └── memory/
│
├── ports/
│   ├── search.py
│   ├── page_loader.py
│   ├── semantic_mapper.py
│   ├── jev.py
│   ├── eclass.py
│   └── artifact_store.py
│
├── adapters/
│   ├── openrouter/
│   ├── crawl4ai/
│   ├── ddgs/
│   ├── eclass/
│   ├── storage/
│   └── database/
│
├── runtime/
│   ├── run_context.py
│   ├── run_store.py
│   ├── services.py
│   ├── bootstrap.py
│   └── config.py
│
├── orchestration/
│   ├── base.py
│   ├── registry.py
│   │
│   ├── graph/
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── routing.py
│   │   └── nodes/
│   │
│   └── agentic/
│       ├── controller.py
│       ├── state.py
│       ├── tools/
│       └── prompts/
│
├── application/
│   ├── mia.py
│   ├── run_manager.py
│   ├── architecture_router.py
│   └── response.py
│
├── persistence/
├── api/
└── evaluation/
```

This is a target hierarchy, not a request to immediately move every existing file. Dependency cleanup comes first; directory movement follows only where useful.

---

## 5. What is already reusable and should be preserved

A large rewrite is unnecessary.

### High-reuse areas

| Current area | Assessment | Plan |
|---|---|---|
| `domain/` | already architecture-neutral | keep |
| `normalization/` | very clean functional component | keep |
| `aas/` | mostly reusable | keep; add thin capability wrapper if needed |
| `semantic/` algorithms | strongly reusable | keep |
| `semantic/jev.py` | strong port/client separation | preserve |
| ECLASS provider abstraction | reusable | preserve |
| `SearchProvider` | good port | preserve |
| `ArtifactStore` | good port | preserve |
| `SemanticMapper` | good port | preserve |
| mapping review logic | reusable | preserve |

The cleanup should protect these modules rather than reimplement them.

---

## 6. Components that need refinement

### 6.1 Replace workflow-owned `MiaContext`

Current:

```text
workflow/context.py
    MiaContext
```

Problem:

It is used outside the workflow and contains backend-wide dependencies.

Target:

```text
runtime/services.py
    ServiceContainer
```

Possible shape:

```python
@dataclass(slots=True)
class ServiceContainer:
    catalogue: ProductCatalogue
    artifacts: ArtifactStore
    templates: OfficialTemplateRepository
    search: SearchProvider
    extraction: ExtractionService
    mapping: MappingService
    research: ResearchService
    review: MappingReviewService
    jev: JevDecisionClient | None
    eclass: EclassPropertyProvider | None
```

The LangGraph runtime receives this container.

The agentic runtime receives the same container.

### 6.2 Split `RunWorkspace`

Current:

```python
RunWorkspace(
    state: MiaWorkflowState,
    ctx: MiaContext,
)
```

Problem:

Useful persistence/event functionality requires graph state.

Target:

```text
RunContext
    user_id
    thread_id
    product_id
    run_id

RunStore
    load()
    put_model()
    put_json()
    put_bytes()
    event()
    heartbeat()
```

Then both orchestrators can use the same run infrastructure.

### 6.3 Thin graph nodes

Current node pattern:

```text
load
business logic
persist
events
snapshot
fingerprints
state patch
```

Target node pattern:

```text
read state
build capability request
call capability
translate result to graph state
```

Example target:

```python
async def normalize_evidence(state, runtime):
    result = runtime.services.normalization.normalize(
        package=runtime.run_store.load_model(
            state["evidence_artifact_id"],
            ProductKnowledgePackage,
        )
    )

    stored = runtime.run_store.record(result)

    return {
        "normalization_artifact_id": stored.artifact_id,
    }
```

The graph remains responsible for ordering, not the algorithm itself.

### 6.4 Remove workflow imports from services

Especially:

```text
services/deep_research.py
```

It must no longer import:

```text
workflow.context
workflow.workspace
workflow.nodes_product
```

Reusable helpers such as package merging should move to an architecture-neutral location.

For example:

```text
capabilities/extraction/merge.py
    merge_packages(...)
```

or:

```text
domain/evidence_merge.py
```

depending on final ownership.

### 6.5 Reduce `mia.py`

Current `Mia` performs too many roles.

Target:

```text
bootstrap.py
    create adapters and services

MiaApplication
    application-level entry points

ArchitectureRouter
    graph-v1 / agentic-v1

GraphOrchestrator
    current LangGraph

AgenticOrchestrator
    future single-controller architecture
```

`MiaApplication` should not know the detailed construction logic for every provider.

---

## 7. Core reusable capability blocks

The first stable set of backend LEGO blocks should be:

| Capability | Input | Output | Shared by Graph | Shared by Agentic |
|---|---|---|---:|---:|
| Product discovery | user/product query | verified candidates | yes | yes |
| Product identity/reuse | URL + user/run context | product/run decision | yes | yes |
| Extraction | product/source URL | knowledge package | yes | yes |
| Evidence merge | packages | merged package | yes | yes |
| Normalization | knowledge package | normalization report | yes | yes |
| Semantic context | package + normalization | context views | yes | yes |
| JEV routing | contexts + templates | bounded routing result | yes | yes |
| JEV diagnostics | routing result | diagnostics/policy | yes | yes |
| ECLASS resolution | evidence + route + provider | verified concepts | yes | yes |
| Mapping | evidence + targets | mapping result | yes | yes |
| Mapping review | mapping + human input | reviewed mapping | yes | yes |
| Coverage | mapping + template | coverage report | yes | yes |
| Research | missing data + known sources | new evidence | yes | yes |
| AAS build | accepted mapping + evidence | DPP/AAS package | yes | yes |
| Validation | AAS/DPP | validation result | yes | yes |
| Memory retrieval | product/context query | reusable knowledge | optional | yes |

The key idea is:

> The orchestration architecture should not own these operations.

---

## 8. Current vs refined architecture

| Concern | Current | Refined |
|---|---|---|
| Main center | `Mia` + workflow | application + reusable capabilities |
| Workflow context | backend-wide dependency holder | graph-specific state only |
| Dependency container | `MiaContext` inside workflow | architecture-neutral `ServiceContainer` |
| Run persistence | coupled through `RunWorkspace` + graph state | architecture-neutral `RunStore` |
| Business logic | partly embedded inside graph nodes | capability services/functions |
| Graph nodes | orchestration + business + persistence | thin orchestration adapters |
| Deep research | depends on workflow internals | standalone capability |
| Agentic reuse | difficult | direct reuse of same components |
| Provider construction | concentrated in `Mia.__init__` | bootstrap/composition root |
| Experimentation | structural code changes | architecture/config selection |
| Testing | often workflow/e2e dependent | component tests + orchestration tests |
| Understanding changes | difficult because responsibilities overlap | easier because each component has one role |
| Duplicate code risk | increases with new architectures | reduced through shared capability layer |

---

## 9. Graph and agentic architecture after cleanup

### Graph architecture

```text
GraphOrchestrator
      │
      ├── discovery
      ├── extraction
      ├── normalization
      ├── JEV / semantic
      ├── mapping
      ├── coverage
      ├── research
      ├── review
      └── AAS build
```

LangGraph decides the sequence and conditional routing.

### Agentic architecture

```text
AgenticOrchestrator
      │
      │ single global LLM decides next action
      │
      ├── discovery tool ───────► same discovery capability
      ├── extraction tool ──────► same extraction capability
      ├── JEV tool ─────────────► same JEV capability
      ├── ECLASS tool ──────────► same ECLASS capability
      ├── mapping tool ─────────► same mapping capability
      ├── research tool ────────► same research capability
      ├── validation tool ──────► same validation capability
      └── AAS tool ─────────────► same AAS capability
```

Only orchestration changes.

Business truth, validators, storage, review, and semantic boundaries remain shared.

---

## 10. Configuration direction

Secrets stay in environment variables.

Examples:

```text
OPENROUTER_API_KEY
database credentials
ECLASS credentials
```

Architecture and algorithm settings should move toward versioned JSON configuration.

Example:

```text
configs/
    architectures/
        graph-v1.json
        agentic-v1.json
    semantic/
        conservative.json
        experimental.json
    models/
        strong.json
        cheap.json
```

Example architecture profile:

```json
{
  "schemaVersion": 1,
  "architecture": "graph-v1",
  "components": {
    "extraction": "default",
    "normalization": "default",
    "semanticRouting": "jev-hierarchical",
    "eclass": "verified",
    "mapping": "default",
    "aasBuilder": "idta"
  }
}
```

This later makes experiments reproducible.

---

## 11. Migration plan

The cleanup should be incremental. Existing behavior must remain stable while responsibilities move.

### Phase 0 — baseline and dependency map

Goal:

- no functional changes;
- identify dependency direction and current ownership.

Actions:

- document current major modules;
- add architecture dependency tests/checks if practical;
- record current `make check` baseline;
- define naming for runtime/capability/orchestration layers.

Exit criteria:

- cleanup boundaries are agreed;
- current workflow is a stable reference implementation.

### Phase 1 — architecture-neutral runtime

Goal:

Remove backend-wide runtime dependencies from `workflow/`.

Actions:

1. introduce `RunContext`;
2. introduce architecture-neutral `RunStore`;
3. introduce `ServiceContainer`;
4. adapt LangGraph runtime to use them;
5. remove API/service imports of `workflow.context.MiaContext`.

Exit criteria:

```text
services/ ──X──► workflow/
api/      ──X──► workflow/context.py
```

### Phase 2 — remove upward service dependencies

Goal:

Services must no longer depend on graph implementation.

First target:

```text
services/deep_research.py
```

Actions:

- move `merge_packages` to architecture-neutral ownership;
- replace `RunWorkspace` dependency with `RunStore`;
- replace `MiaContext` with explicit service dependencies;
- keep existing runtime behavior.

Exit criteria:

- `services/` contains no imports from `workflow/`.

### Phase 3 — extract large graph-node operations

Goal:

Make graph nodes thin.

Suggested order:

1. product identity/reuse;
2. extraction/persistence;
3. semantic preparation;
4. mapping;
5. research integration;
6. AAS build/store.

For each node:

```text
BEFORE
graph node = orchestration + operation

AFTER
graph node = orchestration adapter
capability = operation
```

Exit criteria:

- major capabilities can be directly called in unit tests without LangGraph.

### Phase 4 — isolate current LangGraph architecture

Goal:

Make the current workflow explicitly one orchestrator.

Target:

```text
orchestration/graph/
    graph.py
    state.py
    routing.py
    nodes/
```

Introduce:

```python
class Orchestrator(Protocol):
    async def run(...)
```

and:

```python
class GraphOrchestrator:
    ...
```

Exit criteria:

- application code invokes an orchestrator interface instead of directly owning LangGraph.

### Phase 5 — simplify composition/bootstrap

Goal:

Reduce `mia.py`.

Move provider construction to:

```text
runtime/bootstrap.py
```

The bootstrap layer constructs:

- database/catalogue;
- artifact store;
- search provider;
- Crawl4AI;
- model clients;
- JEV;
- ECLASS;
- reusable services.

`MiaApplication` receives the constructed runtime.

Exit criteria:

- `Mia.__init__` is no longer the central implementation location.

### Phase 6 — architecture configuration

Goal:

Make architecture selection explicit.

Add:

```text
graph-v1
agentic-v1
```

to architecture configuration.

Initially only `graph-v1` is implemented.

Exit criteria:

- current application behavior is selected through the architecture abstraction.

### Phase 7 — add agentic architecture

Only after the cleanup above.

The agentic architecture should:

- use one global controller LLM;
- use the exact same capability services;
- expose capability wrappers as tools;
- use JEV as one semantic tool;
- preserve deterministic validation and trusted-source boundaries;
- use the same run/artifact/event persistence.

No business capability should need to be rewritten specifically for the agent.

---

## 12. Refactoring priority

| Priority | Refactor | Reason |
|---:|---|---|
| P0 | Generalize `MiaContext` | currently makes workflow the backend center |
| P0 | Generalize `RunWorkspace` | needed for shared graph/agent persistence |
| P0 | Remove workflow imports from `DeepResearchService` | clearest dependency inversion violation |
| P1 | Extract evidence operation from graph node | large responsibility concentration |
| P1 | Extract mapping orchestration logic | needed by both architectures |
| P1 | Extract AAS build/store operation | graph node currently mixes many concerns |
| P1 | Break up `Mia.__init__` | required for alternative orchestration composition |
| P2 | Move graph into explicit orchestration package | makes architecture boundary obvious |
| P2 | Add architecture registry/config | enables experiments |
| P3 | Implement agentic orchestrator | should consume cleaned reusable blocks |

---

## 13. Rules for future code

### Rule 1 — orchestration is replaceable

No reusable backend capability may import:

```text
orchestration.graph
orchestration.agentic
LangGraph state
agent controller state
```

### Rule 2 — one owner per responsibility

Examples:

- extraction service owns extraction;
- run store owns artifact/event persistence helpers;
- mapping service owns mapping operation;
- graph owns execution order;
- agent owns action selection.

### Rule 3 — deterministic business invariants remain outside LLM control

Examples:

- valid template target;
- ECLASS IRDI verification;
- complete evidence accounting;
- projection collision validation;
- AAS structural validation;
- durable generation fencing.

### Rule 4 — adapters are replaceable

Capabilities depend on protocols, not concrete vendors.

### Rule 5 — plain functions stay plain when possible

Do not create abstractions merely for visual symmetry.

Examples already suitable as functions:

- `normalize_package()`;
- `build_context_views()`;
- `apply_decision_policy()`;
- deterministic diagnostics.

Use classes/interfaces only when state or implementation replacement is real.

### Rule 6 — graph nodes and agent tools are adapters

Both should be thin wrappers:

```text
Graph node ─┐
            ├──► Capability
Agent tool ─┘
```

### Rule 7 — configuration is observable

Every experimental run should eventually record:

- architecture ID;
- architecture config version/hash;
- model IDs;
- semantic strategy;
- JEV model;
- important policy settings.

This prevents experimental results from becoming unreproducible.

---

## 14. How we know the cleanup succeeded

The cleanup is successful when the following can be done without LangGraph, HTTP, or the frontend:

```python
package = await extraction.extract(...)

normalized = normalization.normalize(package)

views = semantic_context.build(
    package,
    normalized,
)

routing = await jev_routing.route(
    package,
    normalized,
    views,
)

mapping = await mapping.map(
    package,
    routing,
)

coverage = coverage_service.calculate(
    package,
    mapping,
)

aas = aas_service.build(
    package,
    mapping,
)
```

Then the graph simply determines the sequence:

```text
A → B → C → D
        └─condition→ E
```

while the future agent determines the next operation dynamically:

```text
inspect
→ choose capability
→ inspect result
→ choose next capability
```

The same backend blocks are used in both cases.

---

## 15. Non-goals of this cleanup

This refinement should **not** initially:

- redesign the frontend;
- change semantic mapping behavior;
- change JEV thresholds;
- add a multi-agent architecture;
- replace LangGraph;
- rewrite working domain models;
- introduce excessive abstract factories;
- change authoritative trust rules;
- optimize model cost before measurement exists.

The first objective is structural clarity and reuse.

---

## 16. Desired final result

Before:

```text
Mia
  ├── runtime setup
  ├── model setup
  ├── provider setup
  ├── graph
  ├── services
  ├── conversation
  ├── persistence coordination
  └── recovery/debug behavior

workflow nodes
  ├── orchestration
  ├── business operations
  ├── persistence
  ├── events
  └── state translation
```

After:

```text
Application
     │
ArchitectureRouter
     │
 ┌───┴───────────┐
 │               │
Graph          Agentic
 │               │
 └──────┬────────┘
        │
Reusable Capabilities
        │
  ┌─────┼───────────────────────────────┐
  │     │       │       │       │       │
extract semantic mapping research AAS validation
        │
      Ports
        │
     Adapters
```

The backend should become understandable as:

> **stable components + replaceable orchestration + explicit configuration**

rather than:

> **one growing workflow implementation with new experimental logic added around it**.

---

## 17. Immediate next implementation task

The first implementation PR after this plan should be limited to **Phase 1**:

> Introduce architecture-neutral `RunContext`, `RunStore`, and `ServiceContainer`, then adapt the current LangGraph workflow to use them without changing application behavior.

No agentic code should be added in that first implementation step.

That creates the foundation on which all later architecture experiments can safely build.


---

## 18. Implementation status on `refactor/lego-backend`

The architecture cleanup has now been implemented as a behavior-preserving foundation.

### Implemented

- architecture-neutral `ServiceContainer`;
- architecture-neutral `RunContext` and `RunStore`;
- LangGraph `RunWorkspace` reduced to a compatibility adapter;
- shared evidence merge capability;
- shared mapping-result merge capability;
- shared semantic mapper execution/review capability;
- shared deterministic AAS/DPP build capability;
- `DeepResearchService` no longer imports workflow modules;
- provider/model/ECLASS/JEV construction moved from `Mia` into `runtime/bootstrap.py`;
- `Mia` now consumes an assembled runtime instead of constructing every dependency itself;
- explicit `Orchestrator` protocol;
- `GraphOrchestrator` registered as `graph-v1`;
- architecture selection through `MIA_ARCHITECTURE`;
- machine-readable component catalog;
- serializable `PipelineDefinition` suitable for a future GUI pipeline builder;
- privacy modes `local`, `shareable`, and `hybrid` represented in pipeline definitions;
- architecture-boundary tests prevent reusable layers from importing graph/agent orchestration;
- pipeline-contract tests validate component/dependency references.

### Intentionally not implemented in this refactor

The following are execution architectures, not cleanup prerequisites, and remain separate future work:

- `agentic-v1` controller implementation;
- GUI pipeline editor;
- `pipeline-v1` executor;
- automatic model routing between local and remote models;
- local LLM adapter selection;
- remote high-intelligence escalation policy.

Those features can now be added without duplicating core product operations.

### Resulting hierarchy

```text
Frontend / API
      |
      v
Mia application facade
      |
      v
ArchitectureRegistry
      |
      +---------------------+----------------------+--------------------+
      |                     |                      |
      v                     v                      v
 graph-v1              agentic-v1             pipeline-v1
 implemented             future                  future
      |                     |                      |
      +---------------------+----------------------+
                            |
                            v
                    Capability catalog
                            |
      +----------+----------+----------+----------+-----------+
      |          |          |          |          |           |
   evidence   semantic    mapping   research     AAS       coverage
      |          |          |          |          |           |
      +----------+----------+----------+----------+-----------+
                            |
                            v
                    ServiceContainer
                            |
                 ports / concrete adapters
                            |
          local models / remote models / search /
          ECLASS / storage / database / Crawl4AI
```

### Future execution policy

The intended future selection rule is:

```text
Can deterministic code solve it?
        |
       yes --------------------------> deterministic capability
        |
        no
        v
Can a small/local model solve it?
        |
       yes --------------------------> local/private model
        |
        no
        v
Is the data allowed to leave the environment?
        |
       no ---------------------------> human review / local fallback
        |
       yes
        v
Does the task justify higher cost?
        |
       yes --------------------------> advanced remote model
       no ---------------------------> cheaper remote/local model
```

This policy should live above the capabilities. Extraction, mapping, validation,
AAS compilation, provenance, and storage must not be reimplemented for each
model or orchestration strategy.

That is the basis for the longer-term MIA goal: a general industrial data
integration platform where the same verified transformation blocks can be
assembled as fixed pipelines, locally private workflows, GUI-defined workflows,
or intelligent agentic executions.
