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


## DUMMY values and release status

Structural AAS validity and product-data trust are different concepts. A human-approved DUMMY can be
type-compatible and allow the AAS validator to pass, but it is not verified manufacturer data.

Every durable DPP version therefore has a release status:

- `verified`: deployable and contains no accepted DUMMY mappings;
- `provisional`: deployable/structurally valid, but one or more accepted mappings are explicit
  human-approved DUMMY placeholders.

A provisional DPP is still stored and reusable so the workflow can finish instead of repeatedly
failing. Its DUMMY mapping IDs are retained on the DPP version and product snapshot, and the UI
labels the version as provisional. `lastVerifiedAt` advances only for verified releases.

When a real value later replaces the placeholder, normal coverage/review/build creates a new DPP
version. The old provisional version remains part of the audit history instead of being rewritten.


## Human identity is server-bound

The browser may submit review decisions and values, but it is not authoritative for reviewer
identity. The review and value endpoints now overwrite any client-supplied `actorName` with the
name resolved from the authenticated account.

The immutable audit record therefore binds two related identities:

- `user_id`: the authenticated account subject used for ownership/security;
- `actor_name`: a server-resolved human-readable label used in the UI and audit exports.

This prevents a client from forging another person's name while still allowing local development to
use the explicit `Local user` identity.


## Product identifiers are not all the same kind

URL identity is useful but not sufficient, so MIA now models identifiers separately from the
`ProductRecord`. Every identifier has an explicit role:

- `identity`: may identify the same product across URLs, e.g. GTIN or a manufacturer-namespaced MPN;
- `instance`: identifies one physical unit, e.g. a serial number;
- `classification`: describes what kind of product/property it is, e.g. ECLASS classification/IRDI.

This distinction is critical for ECLASS integration. An ECLASS class or semantic identifier can be
shared by many different products and therefore **must never be used as a unique product key**.

Manufacturer article/part numbers are treated as identity only when a manufacturer namespace is
known. Generic labels without enough namespace context are retained as evidence but are not promoted
to identity keys.

The identifier registry is the basis for future cross-URL reconciliation. It intentionally stores
and queries identity keys before implementing automatic product-record merging, so duplicate
histories can be detected without performing unsafe merges.


## Snapshot revisions and atomic pointer updates

The current `ProductWorkSnapshot` is optimized for fast continuation, but a version counter alone is
not enough for recovery/audit if old payloads disappear.

Every successful snapshot write now performs two database changes in the same transaction:

1. conditionally advance the current snapshot using optimistic version checking;
2. append the exact new payload to immutable `product_work_snapshot_history`.

If either write fails, neither is committed. A stale workflow therefore cannot create a misleading
history revision or overwrite current state.

Large artifacts are still persisted before the snapshot pointer moves. If a process crashes after an
artifact is written but before the snapshot transaction commits, that artifact may be orphaned, but
the previous snapshot remains authoritative and resumable. This is deliberate: an orphan artifact is
safer than a snapshot pointing to incomplete state.


## One active chat per user and product

For the MVP, product work is single-session per user. If the same product already has a live
`RUNNING` or `AWAITING_HUMAN` run, a new request for that product must not create another
workflow.

MIA now applies this rule at two levels:

1. the application preflights explicit product URLs and redirects the request into the existing
   product thread/checkpoint;
2. `ProductCatalogue.start_run()` serializes creation per product and rejects a second active run,
   which closes the race window for discovery or simultaneous requests.

The workspace follows a redirected `threadId` and loads the existing message/checkpoint history, so
this behaves as one continuing chat rather than two chats that happen to share product data.

Deleting that chat marks any live run `incomplete`, preserving its product artifacts/history while
allowing later work to start again.

### Provisional DPP policy for the MVP

A provisional DPP containing explicit human-approved DUMMY placeholders is intentionally usable by
the MVP. If no newer product work/data exists, it may be returned and deployed so the user can see
the generated DPP/QR experience.

Therefore `provisional` is a trust label, not a hard deployment blocker in this branch. The reuse
priority is:

1. existing active product chat/checkpoint;
2. otherwise the latest deployable DPP, including provisional;
3. otherwise saved incomplete product work;
4. otherwise fresh extraction.

If a human explicitly requests source refresh, fresh source acquisition still bypasses cached DPP
reuse.


## Refresh and stale-run recovery stay inside the same chat

A same-product redirect must not ignore the user's new intent, but it must also never terminate a
live executor merely because the graph has no human interrupt.

- **Live RUNNING lease**: join the existing work. If refresh is requested, queue that refresh on the
  same thread and let the live invocation return safely.
- **Expired RUNNING lease**: atomically claim recovery, close the abandoned run, advance the internal
  workflow generation, and continue saved work in the same visible chat.
- **AWAITING_HUMAN**: preserve the exact interrupt until the review/value API resumes it.
- **Queued refresh after live completion**: atomically reserve the next generation in the same
  visible chat and start it with `refreshRequested=true`.

### Workflow generations

The public chat/thread ID does not change during recovery. Instead, `ThreadRecord.workflowGeneration`
increments and becomes part of the internal LangGraph checkpoint/Agent Server thread key.

This gives recovered or refreshed work a clean checkpoint namespace without deleting old checkpoint
history, while the user continues seeing one conversation.


## Execution leases and safe same-chat recovery

A database run with status `RUNNING` is not automatically stale. It may be executing normally
between LangGraph nodes and have no human interrupt.

Every live run therefore carries a renewable execution lease. A same-product request follows these
rules:

- `AWAITING_HUMAN` always joins the existing interrupt;
- `RUNNING` with an unexpired lease joins the existing work and never terminates it;
- `RUNNING` with an expired lease may be recovered;
- an explicit refresh arriving during a live lease is queued on the same thread instead of killing
  the executor.

Recovery is claimed transactionally under the product lock. The transaction verifies the expected
active run and thread generation, closes the stale/safely-paused run, advances the internal workflow
generation, clears a queued refresh, and reserves the replacement run. Only after that atomic claim
does LangGraph start the replacement checkpoint.

This prevents the earlier unsafe inference that "RUNNING + no human interrupt = zombie".


## Causal snapshot CAS

Workflow snapshot writes now use the snapshot version carried in LangGraph state as the expected
version. A node that computed from snapshot vN may write only against vN.

The helper no longer reloads vN+K and silently rebases stale results onto it. If the durable current
version differs from `state.productSnapshotVersion`, the write raises `ProductSnapshotConflict`
and the stale generation must stop/reconcile.

Resolve/restart paths seed `productSnapshotVersion` from the durable snapshot, and every successful
snapshot write returns the new version into graph state for the next node.


## Late research invalidates cached DPP reuse

A completed background-research job is itself durable invalidation state. If its job ID is newer than
`ProductWorkSnapshot.lastIntegratedResearchJobId`, the reuse service does not return an older
deployable DPP. It continues saved work and passes that completed job into the integration node.

Similarly, a snapshot from a newer failed/incomplete run takes precedence over an older deployable
DPP. A DPP short-circuits only when the authoritative snapshot is `COMPLETED` for that same DPP
run and there is no completed research waiting to be integrated.

This preserves the MVP rule that verified or provisional DPPs are reusable when no newer product
knowledge exists, while preventing stale cached passports from hiding newer evidence.


## Artifact ownership follows the producing run

Products may be globally canonicalized while user workspaces remain private. Artifact authorization
therefore cannot be based on `user_products` alone.

Owned artifact reads now join:

```text
artifact -> run -> thread -> thread.user_id
```

The public product identity may be shared by two accounts, but bytes produced by one account's run
remain inaccessible to the other account unless a separate sharing mechanism is introduced.
Internal workflow loads also use the run owner when resolving artifact metadata.


## Instance identifiers are account-private

Product identity/classification and physical-instance identity have different ownership.

GTIN, manufacturer-namespaced MPN/article numbers, and ECLASS classification remain product-level
facts. Serial numbers and other `instance` identifiers are stored in a user-scoped table and are
returned only to the account that contributed them.

This prevents two accounts associated with the same canonical product record from seeing each
other's physical-unit serial numbers. Identity unique-index races are also translated into
`ProductIdentifierConflict` instead of leaking a raw database exception.


## Reuse verifies durable artifact pointers

Snapshot and DPP metadata are not sufficient by themselves to prove that reusable bytes still exist.

Before selecting saved evidence, reviewed mappings, pending-research seed evidence, or a cached DPP,
the reuse service verifies that the artifact is still registered for the requesting account.

If a snapshot pointer is stale, MIA falls through to older reusable artifacts or fresh extraction
instead of selecting continuation and failing later in `RunWorkspace.load()`. Missing storage is
still observable as a data-quality problem, but it no longer converts a recoverable reuse decision
into an avoidable workflow crash.


## Human audit snapshots are self-contained

Immutable human audit rows now retain compact before/after representations in addition to artifact
IDs:

- proposed and final displayed values;
- proposed and final target template paths;
- proposed/final mapping and requirement IDs;
- actor, timestamp, action and optional comment.

The audit therefore remains understandable even if an old evidence/mapping artifact is later removed.
It intentionally does not duplicate whole mapping objects or evidence packages.


### Artifact existence includes storage bytes

The reuse decision checks both durable catalogue ownership and the configured artifact store.
`LocalArtifactStore.exists()` checks the file path; the Vercel adapter checks the private blob.

This closes the gap where metadata could survive after underlying bytes were deleted. The unit-level
reuse service can still run without a store for pure policy tests, but production workflow nodes
always provide the configured artifact store.


## Deliberately deferred cleanup

Two review suggestions are intentionally deferred until after local behavioral testing:

- extracting same-product execution arbitration from `Mia.message()` into a coordinator service;
- making the semantic mapper fingerprint configuration-aware.

The first is a structural refactor with no remaining correctness requirement and would increase the
surface changed immediately before end-to-end testing. The second should be implemented together
with the Jev branch, because Jev model/version/threshold/configuration are the information the
fingerprint ultimately needs.

The current mapper fingerprint should therefore be treated as an implementation identity, not yet a
complete reproducibility identity.
