# Jev-first hierarchical semantic mapping

Status: experimental shadow implementation on `feat/jev-hierarchical-semantic-mapping`.

Base branch: `feat/resumable-assets-history-reuse@7e5dae2343b130aec24f74718fb7b173720163ee`.

## Trust boundary

This branch does not let generative text create product truth.

- extracted `EvidenceRecord` objects remain immutable source truth;
- normalization is derived and lossless;
- IDTA routing may only choose nodes that exist in the verified official templates;
- Jev returns bounded choices and the complete probability distribution;
- current production mapping remains authoritative while shadow mode is enabled;
- no Jev result is compiled into the AAS in the first milestone;
- future ECLASS identifiers must come from an authoritative registry provider, never model text;
- any future LLM helper may only produce retrieval hints or human-facing explanations.

## Milestone 1 implemented

The evidence subgraph now runs:

```text
extract_evidence
  -> normalize_evidence
  -> build_semantic_context
  -> shadow_jev_idta_routing
  -> build_targets
  -> deterministic_mapping
  -> existing semantic_mapping
  -> existing review / coverage / research / build
```

The new stages cannot change the trusted mapping result.

### Normalization

`mia_dpp.normalization` parses only safe representation-level syntax:

- numeric values;
- quantities;
- ranges;
- plus/minus tolerances;
- simple booleans;
- simple approximation qualifiers.

Ambiguous locale-sensitive syntax is preserved and marked ambiguous instead of guessed.

Every normalized item keeps the original `evidence_id`, label, context path, and raw value.

### Multi-scope context

`mia_dpp.semantic.context` creates non-destructive views for experimentation:

- property only;
- parent path;
- sibling evidence;
- broad source section;
- full product evidence.

No evidence is merged or deleted.

### Hierarchical IDTA routing

`mia_dpp.semantic.idta_routing` walks the normalized official template tree.

At each level:

1. options come only from verified template children;
2. a single structural child is traversed deterministically;
3. multiple children become a bounded Jev Choice question;
4. `__no_idta_location__` and `__unresolved__` are explicit abstentions;
5. reaching an official wildcard such as
   `TechnicalData/TechnicalPropertyAreas/[]/ArbitraryProperty`
   ends structural routing successfully.

The router currently runs the property, siblings, and full-product scopes independently so their
probability distributions can later be compared.

### Jev API boundary

`mia_dpp.semantic.jev.OpenRouterJevClient` is intentionally policy-free.

It validates that:

- every criterion has a returned probability;
- no unknown criterion is returned;
- probabilities are finite and within [0, 1];
- the distribution sums approximately to 1;
- the selected choice is one of the supplied options;
- Choice option count stays within the supported bound.

The application does not substitute a missing Jev confidence field with another number. The full
distribution is the stored diagnostic.

## Enabling shadow routing

Shadow mode is off by default.

```env
MIA_JEV_SHADOW_ENABLED=true
MIA_JEV_MODEL=typesafe/jev-1.13
MIA_JEV_MAX_CONCURRENCY=8
OPENROUTER_API_KEY=...
```

When disabled, normalization and context artifacts are still built, but Jev routing is skipped and
the current mapper behaves exactly as before.

When enabled, the workflow writes:

```text
semantic/normalization.json
semantic/context-views.json
semantic/jev-idta-routing-shadow.json
```

The routing artifact is diagnostic only.

## Tests added

- `test_normalization.py`
- `test_semantic_context.py`
- `test_jev_decisions.py`
- `test_idta_routing.py`

These test lossless normalization, context scopes, strict probability handling, and routing to the
official Technical Data wildcard using a fake bounded decider.

## Intentionally not implemented yet

The following are subsequent milestones, not hidden behavior in milestone 1:

1. strategy agreement / decision policy across multiple Jev scopes;
2. semantic group IDs;
3. ECLASS registry retrieval and verification;
4. Jev ECLASS candidate selection;
5. wildcard mapping creation from verified ECLASS identifiers;
6. review-policy categories AUTO / OPTIONAL / CONFIRM / ALARM;
7. wildcard-aware human correction and immutable review history;
8. semantic-slot conflict detection for wildcard properties;
9. authoritative switch from legacy mapper to the Jev-first engine;
10. optional LLM query expansion or human-facing disagreement explanations.

Those changes should be added only after the shadow routing artifacts have been inspected on real
products.


## Milestone 2 implemented: multi-scope diagnostics and human-attention policy

After shadow IDTA routing, the graph now derives two additional artifacts without making any model
calls:

```text
semantic/jev-routing-diagnostics.json
semantic/jev-decision-policy.json
```

The flow is now:

```text
shadow_jev_idta_routing
  -> analyze_jev_shadow
       -> distribution diagnostics
       -> cross-scope agreement
       -> AUTO / OPTIONAL / CONFIRM / ALARM
  -> existing trusted mapper
```

The policy is still shadow-only and cannot alter the AAS.

### Diagnostics retained per Jev choice

For every non-deterministic hierarchy decision the system records:

- selected probability;
- top probability;
- runner-up probability;
- top-two margin;
- runner-up / winner ratio;
- normalized entropy;
- whether Jev's selected choice is also the probability argmax.

These values are treated as relative decision diagnostics, **not calibrated probabilities of
correctness**.

### Route and cross-scope diagnostics

For each property/scope route the system records the weakest decision along the path. For each
evidence item it then compares the configured scopes and stores:

- consensus destination;
- scope agreement;
- number of distinct destinations;
- weakest selected probability;
- weakest margin;
- largest runner-up ratio;
- largest normalized entropy;
- unresolved-scope count;
- whether every selected Jev choice was its own probability argmax.

### Starting policy

The default policy deliberately distinguishes dominant distributions from close two-way decisions.

Example:

```text
85 / 15
-> runner-up is material
-> CONFIRM under the starting policy

85 / 7 / diffuse remainder
-> dominant winner + large margin
-> AUTO when configured scopes agree and entropy remains low
```

Strong, mutually inconsistent routes from different context scopes become `ALARM`.

Thresholds are configuration, not model prompts, and can therefore be changed later without
rerunning Jev. Current environment controls include:

```text
MIA_JEV_AUTO_MIN_SELECTED_PROBABILITY
MIA_JEV_AUTO_MIN_MARGIN
MIA_JEV_AUTO_MAX_RUNNER_UP_RATIO
MIA_JEV_AUTO_MAX_ENTROPY
MIA_JEV_OPTIONAL_MIN_SELECTED_PROBABILITY
MIA_JEV_OPTIONAL_MIN_MARGIN
MIA_JEV_OPTIONAL_MAX_RUNNER_UP_RATIO
MIA_JEV_OPTIONAL_MAX_ENTROPY
```

The starting values are hypotheses for inspection, not claims of statistical calibration.

### Tests added for milestone 2

- `test_jev_diagnostics.py`
- `test_jev_decision_policy.py`

They include the explicit 85/15 versus 85/7/... behavior, threshold retuning without model calls,
and high-confidence disagreement between context scopes.


## Milestone 3 implemented: lossless semantic grouping

The shadow flow now also produces:

```text
semantic/jev-semantic-grouping-shadow.json
```

The grouping stage runs after routing diagnostics and before the existing authoritative mapper:

```text
normalize
  -> context views
  -> Jev IDTA routing
  -> routing diagnostics / policy
  -> lossless grouping strategies
  -> existing trusted mapping pipeline
```

Grouping is metadata only. It never merges, rewrites, deletes, or replaces an `EvidenceRecord`.

### Strategies

The current grouping report contains:

1. `hierarchy_baseline`
   - groups evidence that already shares the exact visible source hierarchy;
   - makes no semantic model call;
   - provides a deterministic comparison baseline.

2. `jev_incremental:siblings`
   - processes evidence in source order;
   - asks Jev whether the focus fact belongs to one of the existing groups,
     `__new_group__`, or `__unresolved__`;
   - supplies sibling context.

3. `jev_incremental:full_product`
   - uses the same bounded classification mechanism;
   - supplies full-product context.

The first evidence item creates the first group deterministically. Every later Jev call receives only
existing group IDs plus the two explicit abstention/creation options. Group IDs are MIA-generated
stable identifiers; Jev never creates identifiers.

### Group comparison

Group IDs from different strategies are intentionally not compared directly. The report instead
compares the pairwise relation:

```text
Does strategy X place evidence A and evidence B in the same group?
```

For every evidence pair the artifact records:

- strategies grouping them together;
- strategies separating them;
- strategies where either item is unresolved;
- agreement across strategies that produced a usable pairwise decision.

This makes the grouping experiment comparable even though each strategy builds a different
partition.

### Safety and boundedness

- `__new_group__` creates only a MIA-owned metadata group.
- `__unresolved__` leaves the evidence ungrouped.
- evidence values and hierarchy remain unchanged.
- the configured group limit is capped at 253 because Jev Choice supports 255 options and
  `NEW_GROUP` plus `UNRESOLVED` consume two options.
- reaching the configured group limit turns another `NEW_GROUP` result into unresolved metadata
  rather than exceeding the bounded Jev option space.

### Tests added for milestone 3

`test_semantic_grouping.py` verifies:

- source evidence remains unchanged after grouping;
- exact-hierarchy baseline behavior;
- existing-group assignment;
- explicit new-group creation;
- explicit unresolved classification;
- group-limit fallback to unresolved;
- comparison by pairwise co-grouping instead of group-ID equality.

This stage is still diagnostic-only and has no effect on `MappingResult`, human-review state,
coverage, or AAS generation.
