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
