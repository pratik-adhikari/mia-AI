# Durable product work: why resume is not enough

MIA originally persisted graph checkpoints and run artifacts, which is enough to continue one
interrupted chat but not enough to treat product knowledge as something that accumulates over time.

A failed AAS attempt must not cause MIA to rediscover the product, repeat semantic mapping, or lose
human decisions. The durable unit therefore needs to be the **product**, not the chat and not the
individual run.

## Three different operations

These operations must stay distinct:

- **Resume** continues the exact interrupted LangGraph checkpoint.
- **Continue saved work** starts from the latest authoritative product snapshot and performs only
  invalid or incomplete stages.
- **Refresh sources** deliberately reacquires external product sources and invalidates downstream
  stages when their inputs changed.

A completed deployable DPP can still use the existing fast reuse path.

## ProductWorkSnapshot

A product snapshot does not duplicate large artifacts. It stores authoritative pointers to them,
plus the fingerprints needed to decide whether each downstream stage is still valid.

The snapshot is user-scoped because the canonical product identity can be shared while human
review, private evidence, and reusable decisions belong to an account.

The snapshot records:

- evidence, target, mapping, coverage, build and validation artifact IDs;
- exact selected template releases;
- evidence/target/mapping/review/build fingerprints;
- the semantic mapper fingerprint;
- unresolved mandatory requirements;
- pending human-review state;
- the highest durable workflow stage.

The snapshot is versioned on every update. This gives us a basis for later optimistic concurrency
protection without making LangGraph checkpoints responsible for product-level ownership.

## Human decisions are history, not mutable mapping fields

Fields such as `humanReviewed` are useful for rendering the current mapping but are not an audit
trail. Each human action is therefore also stored as an immutable `HumanReviewRecord`.

Examples include accepting a proposal, changing the target, correcting a value, rejecting evidence,
and supplying a mandatory real or DUMMY value. Later mapping cycles may replace the current mapping
without erasing what a human previously decided.

## DUMMY values

A DUMMY is an explicit human-approved placeholder, not manufacturer evidence. MIA may generate a
type-compatible placeholder so the AAS can be structurally built, but the provenance must continue
to say `dummy`.

DUMMY values must never be promoted into reusable semantic mapping knowledge. Otherwise a
placeholder such as `0` could become a false learned product fact.

## Mapper independence

Historical reuse is intentionally independent of the semantic mapping implementation. The workflow
decides *whether mapping must run*. Only if it must run does it call the configured
`SemanticMapper` (LLM, Jev, or another future implementation).

The semantic mapper fingerprint belongs in the snapshot so unreviewed machine mappings can be
invalidated when mapper configuration changes while still allowing valid human-reviewed decisions
to survive.

## Invalidation direction

Dependencies only invalidate downstream work:

```text
sources -> evidence -> targets -> mapping -> human review -> coverage -> build -> validation
```

Examples:

- source changed: recompute evidence and every dependent stage;
- template release changed: keep evidence, reconsider mapping and later stages;
- only validation/build failed: keep evidence and reviewed mapping, retry the failed output stage;
- human-reviewed mapping with unchanged evidence and templates: reuse it.

This document describes the target invariants. The implementation is intentionally split into small
commits so the separate Jev semantic-mapping experiment can later merge at the generic mapper seam.


## Implemented invariants on this branch

The branch now enforces the following behavior:

1. A product has one user-scoped, versioned durable work snapshot.
2. New attempts check that snapshot before reacquiring product sources.
3. Explicit refresh never silently reuses saved evidence.
4. Human decisions are stored as immutable audit records in addition to current mapping state.
5. Human-supplied DUMMY values carry explicit provenance and are excluded from reusable semantic
   mapping knowledge.
6. Mapping knowledge is private to the reviewing account unless a future explicit promotion changes
   its scope.
7. Human-reviewed mappings are reused without semantic remapping only when evidence and template
   fingerprints still match.
8. Legacy reviewed mappings without fingerprints are reused but reopened for confirmation.
9. Changed evidence or template fingerprints make the old review stale instead of silently trusted.
10. Chat deletion hides chat history but does not remove the product snapshot, run artifacts, or
    human audit records.
11. A failed build/validation snapshot remains a valid starting point for a later continuation.

## Still intentionally separate

Product identity beyond canonical URL (GTIN, manufacturer part number, ECLASS identifiers), richer
evidence-conflict ranking, and multi-process optimistic locking are separate follow-up concerns.
They build on this snapshot/audit foundation and do not belong inside the semantic mapper itself.


## UI entry points

Assets now exposes all three product-work intentions explicitly:

- **Resume** opens the existing thread/checkpoint when a run is interrupted.
- **Continue saved work** opens a new workflow turn for the stored canonical product URL with
  `refreshRequested=false`; product resolution hydrates the durable snapshot instead of crawling.
- **Refresh sources** sends the same product with `refreshRequested=true`; product resolution
  deliberately bypasses the completed-DPP and saved-work reuse paths and reacquires sources.

The refresh intent is a structured request field rather than inferred from chat wording.


## Requirement-level conflict handling

New evidence must not invalidate every prior human decision. A conflict exists only when new evidence
maps to the same official requirement as a previously human-reviewed mapping and provides a
different normalized value.

When this happens MIA now:

1. keeps both evidence records for audit;
2. marks the competing evidence as `conflicting`;
3. demotes mappings for only the affected requirement back to review;
4. persists a `mapping/evidence-conflicts.json` artifact;
5. creates a focused review batch for those requirement rows;
6. refuses to leave review while that requirement remains ambiguous;
7. leaves unrelated human-reviewed requirements untouched.

This is intentionally requirement-level rather than whole-product invalidation. A newly discovered
IP rating must not force a user to reconfirm an unchanged manufacturer name, model designation, or
other independent fact.


## Concurrent snapshot writes

The product snapshot is a shared durable resource, so two workflows must not use last-write-wins.
Every snapshot write now uses optimistic version checking.

A workflow reads snapshot version N, performs its stage work, and may write only if N is still the
current version. If another workflow has already written N+1, the stale write fails with
`ProductSnapshotConflict` instead of erasing newer evidence, review, or build state.

This matters especially for background research and multiple chats working on the same product:
versioning is no longer only descriptive metadata; it now protects correctness.


### Why background integration has its own route

The semantic-mapping route and the background-integration route both use the words `review` and
`coverage`, but they do not have the same destination graph.

After initial semantic mapping, `coverage` intentionally passes through background-research
integration first. After background-research integration itself, `coverage` must go directly to
coverage calculation. Reusing the first route table would create a self-loop.

The dedicated `after_background_integration` route therefore means:

- conflict found -> human review;
- no conflict -> coverage;
- never route integration back into itself.
