# Backend Modularization — Phase 3 Review

Branch: `refactor/backend-modular-phase-3`  
Base: `refactor/backend-modular-phase-2@ca3cd706f223fdaa0e234f8af2e51381a8895a0c`

## Goal

Phase 3 extracts business operations from LangGraph nodes into reusable backend capabilities.

The desired shape is:

```text
LangGraph node
Agent tool
GUI pipeline node
Batch/evaluator
      │
      ▼
reusable capability/service
      │
      ▼
domain + runtime + existing backend dependencies
```

Graph nodes should own:

- sequencing;
- branching;
- LangGraph interrupts;
- translation between graph state and capability inputs/outputs.

Graph nodes should not own:

- crawling/extraction;
- product reuse policy;
- snapshot persistence;
- normalization/JEV/ECLASS execution;
- mapping algorithms;
- research integration;
- AAS building;
- DPP publication.

## Main result

The major workflow nodes are now adapters over reusable services.

Approximate node sizes on this branch:

| Graph module | Before Phase 3 | After Phase 3 |
|---|---:|---:|
| `nodes_product.py` | 470 lines | 139 lines |
| `nodes_semantic.py` | 445 lines | 148 lines |
| `nodes_mapping.py` | 968 lines | 362 lines |
| `nodes_research.py` | 67 lines | 47 lines |
| `nodes_aas.py` | 187 lines | 79 lines |
| `nodes_promotion.py` | business-heavy | 51 lines |

`nodes_mapping.py` remains intentionally larger because the graph still owns the two real HITL boundaries:

- mapping review interrupt;
- mandatory-value interrupt.

Those are orchestration responsibilities and should not be hidden inside a reusable backend service.

## Extracted capabilities

### Product lifecycle

Added:

`services/product_lifecycle.py`

Owns:

- durable product identity resolution;
- active-run resume decisions;
- product reuse policy integration;
- run creation;
- durable cache events;
- completed DPP reuse.

Graph node responsibility is now only:

```text
MiaWorkflowState
      ↓
ProductResolutionRequest
      ↓
ProductLifecycleService
      ↓
state patch
```

### Evidence acquisition

Added:

`services/evidence_acquisition.py`

Owns:

- persisted evidence reuse;
- seed crawl/extraction;
- HTML/markdown/structured artifact persistence;
- deferred asset manifest persistence;
- evidence-package merging;
- source/evidence fingerprinting;
- product metadata updates;
- product identifier registration;
- duplicate-identity diagnostics;
- background-research job creation;
- product snapshot update.

This removes the largest mixed-responsibility block from `nodes_product.py`.

### Evidence metadata

Added:

`services/evidence_metadata.py`

Owns deterministic evidence-derived metadata helpers:

- `evidence_text(...)`;
- `product_image_url(...)`.

`workflow/presentation.py` is now a compatibility re-export rather than a second implementation.

### Product snapshot synchronization

Added:

`services/product_snapshot.py`

Owns:

- model fingerprints;
- semantic mapper fingerprint;
- optimistic product snapshot synchronization.

The neutral function requires explicit:

- `ProductCatalogue`;
- `RunContext`;
- expected snapshot version;
- source generation.

It no longer needs `RunWorkspace` or graph state.

`workflow/product_snapshot.py` is now only a graph compatibility adapter.

### Semantic preparation

Added:

`services/semantic_preparation.py`

Owns:

- evidence normalization;
- semantic context construction;
- hierarchical JEV routing;
- JEV routing diagnostics/policy;
- semantic grouping;
- ECLASS resolution;
- ECLASS diagnostics/policy;
- open-property proposals.

The existing algorithms under `normalization/` and `semantic/` remain unchanged. The new service owns their reusable execution/persistence boundary.

### Mapping preparation and coverage

Added:

`services/mapping_execution.py`

Intentionally narrow after review during implementation.

Owns only:

- target construction;
- deterministic mapping;
- coverage calculation.

It does not own semantic mapping, research integration, or human review.

### Semantic mapping

Added:

`services/semantic_mapping.py`

Owns:

- reviewed mapping reuse/reconfirmation;
- fixed-target JEV mapping;
- model-based semantic mapping;
- reviewed mapping knowledge retrieval;
- mapping/review artifact persistence;
- mapping-cycle creation;
- mapping snapshot progression.

This was split out rather than leaving one giant `MappingExecutionService`.

### Research mapping integration

Added:

`services/research_mapping.py`

Owns:

- integrating mapped background evidence;
- filling deferred mapping gaps;
- merging mapping results;
- projection-collision review escalation;
- evidence conflict detection;
- conflict-marked evidence;
- focused review rounds;
- integrated mapping/evidence persistence;
- research integration snapshot progression.

### Human mapping application

Added:

`services/mapping_human.py`

Owns what happens **after** a validated human response exists:

- applying review decisions;
- immutable human audit records;
- reusable trusted mapping memory;
- conflict-resolution validation;
- reviewed evidence/mapping persistence;
- human-supplied mandatory values;
- snapshot updates.

It deliberately does **not** call LangGraph `interrupt()`.

The graph owns pause/resume; the service owns deterministic application of the answer.

### Source research

Added:

`services/source_research.py`

Owns:

- missing-requirement research request;
- manufacturer-domain context;
- source-candidate selection;
- research attempt persistence/events.

`nodes_research.py` is now a simple request/result adapter.

### Semantic promotion

Added:

`services/semantic_promotion.py`

Owns:

- verified open-property promotion;
- promoted mapping/review persistence;
- mapping-cycle regeneration;
- snapshot progression.

### AAS output and release

Added:

`services/aas_output.py`

Owns:

- deterministic DPP/AAS build;
- AAS validation artifact persistence;
- build fingerprinting;
- deployability handling;
- provisional/verified release status;
- DPP version creation;
- product verification timestamp;
- terminal run completion/failure.

`nodes_aas.py` now only selects current artifact IDs and translates the service result.

## Shared deterministic merge ownership

Phase 3 also removes another duplicated ownership problem.

Added:

`domain/mapping_merge.py`

`merge_mapping_results(...)` was previously owned by `DeepResearchService` even though foreground mapping also needed it.

Now:

```text
DeepResearchService ─────┐
ResearchMappingService ──┼──► domain.mapping_merge
                         │
future capabilities ─────┘
```

Like `domain/evidence_merge.py`, this remains a plain function rather than an unnecessary service/factory.

## Current hierarchy

```text
                         API / Application
                               │
                               ▼
                         ServiceContainer
                     orchestration composition
                               │
          ┌────────────────────┴────────────────────┐
          │                                         │
          ▼                                         ▼
     LangGraph workflow                       future orchestrator
          │
          │ state/request translation
          ▼
 ┌───────────────────────────────────────────────────────────┐
 │                    Reusable operations                    │
 │                                                           │
 │ ProductLifecycleService       EvidenceAcquisitionService  │
 │ SemanticPreparationService    MappingExecutionService     │
 │ SemanticMappingService        ResearchMappingIntegration  │
 │ MappingHumanService           SourceResearchService       │
 │ SemanticPromotionService      AasOutputService            │
 └───────────────────────────┬───────────────────────────────┘
                             │
                             ▼
                  runtime / domain / existing ports
                             │
                             ▼
                  persistence / external adapters
```

This is not yet the final ports-and-adapters package layout.

Some services still depend directly on existing concrete backend classes such as:

- `ProductCatalogue`;
- `OfficialTemplateRepository`;
- `WebExtractionTool`;
- `MappingReviewService`.

That is deliberate. Phase 3 establishes operation ownership first; later phases should narrow interfaces only where interchangeability is real.

## What remains graph-owned

### LangGraph interrupts

`nodes_mapping.py` still owns:

```text
interrupt(mapping_review)
interrupt(requirement_value)
```

This is correct.

The workflow is responsible for:

- when execution pauses;
- the interrupt payload;
- validating that the resumed payload belongs to this thread/product/cycle.

The reusable human service is responsible for applying the already-validated answer.

### Graph routing

`workflow/routing.py` remains untouched.

It still decides:

- where the graph goes next;
- whether review/research/human input/build is required.

### Graph state translation

The node adapters still translate:

`MiaWorkflowState -> service request -> state patch`

This is expected until the current graph is isolated as an explicit orchestrator in Phase 4.

## Architectural guards

The Phase 2 rule remains:

```text
services/ ──X──► workflow/
services/ ──X──► langgraph
```

All newly added Phase 3 services satisfy it.

Phase 3 also extends the architecture test for the extracted node modules.

The following graph-node files are prevented from directly importing low-level business implementation packages such as persistence, normalization, semantic engines, mapping algorithms, storage, and the AAS builder:

- `nodes_product.py`;
- `nodes_semantic.py`;
- `nodes_mapping.py`;
- `nodes_research.py`;
- `nodes_aas.py`;
- `nodes_promotion.py`.

This prevents future changes from silently moving business logic back into graph callbacks.

## Direct capability test

Added:

`backend/tests/test_product_lifecycle_service.py`

It uses real:

- SQLite `ProductCatalogue`;
- `LocalArtifactStore`;
- `ProductLifecycleService`;
- architecture-neutral `update_product_snapshot(...)`.

It verifies that a product can:

```text
resolve product
    ↓
create active run
    ↓
persist product snapshot
    ↓
resolve same product again
    ↓
resume existing durable work
```

without LangGraph or `MiaWorkflowState`.

This is an executable proof of the main Phase 3 design goal.

## Deliberate non-goals

Phase 3 does not:

- move `workflow/` into `orchestration/graph/`;
- introduce `GraphOrchestrator`;
- introduce an agentic orchestrator;
- introduce a GUI pipeline schema;
- rewrite `ServiceContainer`;
- split `Mia.__init__`;
- create ports for every concrete dependency;
- rewrite working semantic algorithms;
- change graph topology;
- change JEV/ECLASS policy;
- change human trust rules;
- change AAS/DPP release semantics.

Those belong to later phases.

## Known remaining debt

### DeepResearchService is still large

`DeepResearchService` is orchestration-neutral after Phase 2, but it still owns several research-batch responsibilities.

Phase 3 deliberately does not reopen it while extracting graph-node business logic. It can be decomposed later if repeated use cases demonstrate stable smaller boundaries.

### Concrete catalogue/template dependencies

Several services still use `ProductCatalogue` and concrete template/web/mapping implementations.

This is visible rather than hidden behind another context object.

Do not introduce ports solely for architectural symmetry. Introduce them when the alternate implementation boundary is real.

### Service construction is repeated in graph adapters

Graph helper functions currently build service objects from `ServiceContainer`.

This is acceptable in Phase 3.

Phase 5's bootstrap/composition-root work should centralize service construction rather than solving that concern prematurely here.

## Phase 3 exit criteria

Phase 3 is structurally complete when:

1. major product/evidence/semantic/mapping/research/AAS operations are callable without LangGraph;
2. graph nodes do not own the extracted algorithms/persistence workflows;
3. HITL interrupts remain graph-owned;
4. reusable services contain no workflow/LangGraph imports;
5. graph node state contracts remain compatible with the existing graph.

The current branch satisfies the first four structurally.

The fifth requires the repository test suite / `make check` before freeze.

## Validation required

Run locally:

```bash
git fetch --all --prune
git switch refactor/backend-modular-phase-3
git pull

pytest backend/tests/test_architecture_boundaries.py -q
pytest backend/tests/test_product_lifecycle_service.py -q
make check
```

Do not begin Phase 4 until failures, if any, are corrected and the branch is reviewed.

## Phase 4 after approval

Phase 4 should isolate the current LangGraph implementation as one explicit orchestrator.

Target direction:

```text
application/
      │
      ▼
Orchestrator
      │
      ├── GraphOrchestrator
      └── future AgenticOrchestrator
```

and:

```text
orchestration/graph/
    graph.py
    state.py
    routing.py
    nodes/
    checkpoints.py
```

Phase 4 should mostly be orchestration packaging/interface work.

It should **not** move business logic back out of the reusable services created in Phase 3.
