# MIA architecture

MIA separates **workflow control**, **reasoning**, **deterministic product-data logic**, and
**durable storage**. The separation is intentional: an LLM may reason about ambiguous discovery or
semantic mapping, but it does not control the order of safety-critical DPP steps.

## Workflow

```text
chat/request
    |
    v
product discovery agent (only when identity is ambiguous)
    |
    v
resolve product ---- existing successful DPP? ---- yes ---> reuse
    | no
    v
extract evidence
    |
    v
build official IDTA targets
    |
    v
deterministic mapping
    |
    v
semantic mapping agent
    |
    v
human mapping review (LangGraph interrupt)
    |
    v
coverage
   / \
complete gaps
  |       \
  |        source research agent ---> extract new source ---> mapping
  |                                      |
  |                              no public source remains
  |                                      v
  |                              trusted human value interrupt
  v
build + validate AAS
    |
    v
store versioned DPP
```

`backend/src/mia_dpp/workflow/graph.py` is deliberately only wiring. Business logic belongs in
small nodes and the existing extraction, mapping, template, compiler, and validator modules.

## Responsibility boundaries

| Area | Responsibility |
| --- | --- |
| `workflow/` | LangGraph state, routing, nodes, interrupts, artifact references |
| `agents/discovery/` | Resolve ambiguous company/product identity |
| `agents/semantic_mapping/` | Bounded semantic mapping to official requirement IDs |
| `agents/research/` | Choose a gap-driven source-search query |
| `tools/web/` | Provenance-rich deterministic extraction |
| `tools/mapping/` | Deterministic mapping, review policy, coverage |
| `aas/` | Official template loading, compilation, validation |
| `persistence/` | Product/run/chat/DPP catalogue and compatibility workspace view |
| `storage/` | Artifact bytes on filesystem or object storage |
| `runtime/` | Local/production persistence composition |

A useful rule when reviewing the code is:

> **Graph decides when. Agents reason where needed. Existing services decide how. Storage keeps the
> evidence.**

## State versus artifacts

LangGraph checkpoints contain only small workflow facts and artifact IDs. Rendered HTML, evidence,
mapping results, coverage, validation output, and DPP/AAS payloads are stored separately. This keeps
checkpoints inspectable and prevents large source documents from being duplicated in graph state.

## Durable product model

A stable `ProductRecord` owns many `ProductRun` attempts and many versioned `DppVersion` records.
Every chat message and workflow event has a timestamp. Failed and incomplete runs remain queryable;
only successful validated builds become DPP versions.

Canonical product URLs remove fragments/tracking parameters before identity lookup. Requesting the
same known URL therefore reuses a successful DPP unless refresh is explicitly requested.

## Local and Vercel persistence

Local development uses:

```text
LangGraph checkpoints -> SQLite
catalogue             -> SQLite
artifact bytes         -> .mia-data/workspaces/
```

Production/Vercel uses:

```text
LangGraph checkpoints -> PostgreSQL
catalogue             -> PostgreSQL
artifact bytes         -> Vercel Blob
```

MIA refuses to use local filesystem/SQLite as durable production storage when `VERCEL_ENV` is set.
Configure `MIA_DATABASE_URL` (or `DATABASE_URL`) and a connected Vercel Blob store.

## Human authority

Human review/value collection uses native LangGraph `interrupt()`/resume. Agents cannot approve
their own semantic proposals or manufacture missing product values. The graph researches public
sources first; only unresolved mandatory values are escalated to a human.

## Product library API

The durable catalogue is exposed through:

- `GET /api/products`
- `GET /api/products/{product_id}`
- `GET /api/threads/{thread_id}/messages`
- `GET /api/artifacts/{artifact_id}`

The Next.js `/products` pages use these endpoints to show product images, attempts, DPP versions,
and stored artifacts.

## Conversation plane and durable work plane

MIA now separates ordinary conversation from product-work execution.

```text
Chat UI
  -> General conversation supervisor
       -> durable status query
       -> durable evidence search
       -> durable recent-progress query
       -> conversational reply
  OR
       -> typed workflow command
            -> LangGraph durable work plane
```

The General LLM is not the workflow. It may read durable product/work state and explain it, but
it cannot create technical facts, semantic identifiers, mappings, reviews, or AAS data.

### Checkpoint-independent chat

`Mia.message()` persists the user message and invokes the conversation supervisor before accessing
LangGraph or the checkpoint store. A normal status/data/conversation question can therefore be
answered even if the workflow checkpoint service is unavailable.

Explicit product-work commands continue through the existing LangGraph path.

### Durable product query service

`ProductQueryService` reads only catalogue state and immutable artifacts. It exposes:

- current durable run/snapshot status;
- source-backed evidence search;
- recent persisted workflow events.

Product-data answers are grounded in `EvidenceRecord` values, hierarchy, source URI, and source
excerpt. Missing data is reported as unavailable rather than inferred by the General LLM.

### Production work-status/event APIs

```text
GET  /api/threads/{thread}/work-status
GET  /api/threads/{thread}/evidence/search?q=...
GET  /api/threads/{thread}/events/stream
POST /api/threads/{thread}/retry
```

The event stream is generated from persisted run events and durable run/snapshot state rather than
an in-memory worker session.

### Concurrent chat UX

The workspace no longer uses one global boolean request lock. It counts outstanding requests, so
a user can send another chat question while a long product request is still in flight. Backend
generation fencing still prevents concurrent product mutations from becoming authoritative.

### Execution lease heartbeat

`RunWorkspace` now renews the current run lease at node construction and before persisted artifact
or event writes. Each heartbeat first verifies that the worker still owns the current workflow
generation, so an old executor cannot revive itself after a replacement generation has started.

### Recoverable workflow failures

External/transient failures such as HTTP connection failures, search-provider unavailability, page
load failures, and timeouts are persisted as:

```text
workflow.retryable_failure
run status = incomplete
```

Unexpected deterministic failures remain `failed`.

A retry never mutates or resurrects the old run. `retry_work()` creates a new workflow generation
through the existing atomic restart/fencing mechanism and seeds it from the latest durable
`ProductWorkSnapshot` when reusable evidence/reviewed mappings exist.

The General LLM can return the typed `retry_work` action when a user explicitly asks to recover
failed work; the same capability is available through the retry API.

### Deliberately deferred

The entire DPP LangGraph execution has not yet been moved into a separate persistent task queue.
That is a larger deployment change. The current important slice keeps the existing execution
model while making chat independent, reads durable, leases refreshed, and failures safely
recoverable. A full work-queue/coordinator should be added only if deployment behavior shows that
HTTP-lifetime execution itself remains a practical reliability problem.
