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

## Milestone 4 implemented: ECLASS retrieval and bounded Jev concept classification

The shadow graph now continues:

```text
lossless semantic grouping
  -> shadow ECLASS resolution
       -> route eligibility gate
       -> preferred-name retrieval
       -> direct-IRDI verification
       -> sibling-context Jev choice
       -> full-product-context Jev choice
  -> deterministic ECLASS diagnostics / attention policy
  -> existing trusted mapping pipeline
```

Artifacts:

```text
semantic/eclass-resolution-shadow.json
semantic/eclass-resolution-diagnostics.json
semantic/eclass-decision-policy.json
```

None of these artifacts affect `MappingResult`, coverage, human-review state, or AAS generation.

### Eligibility

ECLASS retrieval runs only for evidence that hierarchical IDTA routing placed into the official
open Technical Data wildcard:

```text
TechnicalData
  / TechnicalPropertyAreas
  / []
  / ArbitraryProperty
```

The wildcard route must receive a strict majority of the configured IDTA context scopes. A tied
or fragmented routing result does not trigger ECLASS retrieval.

### Registry trust boundary

The new `EclassPropertyProvider` contract exposes only:

```text
search_properties(query, limit)
get_property(irdi)
```

Search results are discovery hints only. Every search-hit IRDI is fetched again through
`get_property()` before it is eligible for Jev.

The saved artifact distinguishes search query, search hits, rejected/unverified IRDIs, verified
authoritative properties, and Jev decisions.

Jev cannot introduce an IRDI. Its choices are exactly the verified IRDIs plus
`__no_eclass_match__` and `__unresolved__`.

### Retrieval-empty versus semantic no-match

`retrieval_empty` means the configured registry search returned no candidates.
`__no_eclass_match__` can occur only after at least one authoritative candidate has been
retrieved and directly verified, and Jev explicitly decides that none matches.

This distinction is required before any future LLM query-expansion helper is added.

### Official JSON V2 adapter

`EclassJsonV2Provider` is mTLS-capable and configurable. It supports the JSON V2 multilingual
preferred-name/definition shape and keeps canonical IRDIs inside MIA while converting only the
HTTP lookup path to the V2 hyphen form.

Runtime configuration:

```env
MIA_ECLASS_SHADOW_ENABLED=true
MIA_ECLASS_CERTIFICATE_FILE=/absolute/path/to/eclass-client-cert.pem
MIA_ECLASS_KEY_FILE=/absolute/path/to/eclass-client-key.pem
MIA_ECLASS_JSON_BASE_URL=https://eclass-cdp.com/jsonapi/v2
MIA_ECLASS_SEARCH_PARAMETER=preferredName
MIA_ECLASS_CANDIDATE_LIMIT=12
```

Enabling ECLASS shadow mode requires Jev shadow mode. Missing certificate configuration fails
startup instead of silently falling back to another source.

### Multi-scope ECLASS classification

Verified candidates are classified independently with sibling context and full-product context.

The deterministic diagnostics stage records selected probability, runner-up probability, margin,
runner-up/winner ratio, normalized entropy, and cross-scope concept agreement.

Two strongly supported but different ECLASS concepts produce `ALARM`, even with only two scopes.

The normal `AUTO / OPTIONAL / CONFIRM / ALARM` thresholds are reused for ECLASS concept
classification and can be retuned from saved distributions without new ECLASS or Jev calls.

### Tests added for milestone 4

- `test_eclass_provider.py`
- `test_eclass_resolution.py`
- `test_eclass_diagnostics.py`

They verify JSON V2 parsing and path conversion, direct-IRDI verification, route gating, rejected
candidate handling, retrieval-empty versus semantic no-match, tied-route suppression, and
multi-scope ECLASS agreement/disagreement.

## Milestone 5 implemented: shadow open Technical Property proposals

The shadow graph now continues:

```text
verified ECLASS multi-scope consensus
  -> deterministic ArbitraryProperty proposal
  -> semantic-slot identity
  -> wildcard conflict diagnostics
  -> existing trusted mapping pipeline
```

Artifact:

```text
semantic/open-property-proposals-shadow.json
```

The artifact is diagnostic only. No existing `MappingResult`, review interrupt, coverage result, or
AAS compiler input consumes it.

### Proposal eligibility

A wildcard proposal is created only when:

- ECLASS retrieval produced directly verified properties;
- the cross-scope ECLASS consensus is one of those verified IRDIs;
- the ECLASS decision policy is not `ALARM`.

`NO_ECLASS_MATCH`, unresolved classification, retrieval failures, and strong scope disagreement
remain explicit non-proposed dispositions.

### Target construction

The proposal uses the same authoritative `mapping_target()` constructor as the existing mapper.
The official wildcard path is now named once as:

```text
TechnicalData
  / TechnicalPropertyAreas
  / []
  / ArbitraryProperty
```

The proposed target gets:

- template key/release from the verified IDTA Technical Data template;
- semantic ID from the verified ECLASS IRDI;
- deterministic `idShort` from the authoritative ECLASS preferred name;
- wildcard instance path produced by `mapping_target()`.

The compiler and proposal code now share `sanitize_id_short()` and `ID_SHORT_PATTERN`, preventing
identifier-normalization drift.

### Context-aware semantic slot

Each proposal also receives a semantic-slot identity derived from:

```text
template key
template release
wildcard template path
verified ECLASS IRDI
source context identity
```

This means the same ECLASS concept under `Motor A` and `Motor B` is intentionally treated as two
different semantic slots even though the current compiler cannot yet project separate
`TechnicalPropertyArea` list instances.

### Conflict diagnostics

The shadow report distinguishes four cases:

1. `value_conflict`
   - same semantic slot;
   - different normalized values.

2. `redundant_duplicate`
   - same semantic slot;
   - equivalent normalized values;
   - semantically harmless, but promotion must deduplicate because the compiler requires unique
     wildcard instance paths.

3. `id_short_collision`
   - different ECLASS semantic concepts;
   - their authoritative preferred names sanitize to the same AAS `idShort`.

4. `projection_context_collision`
   - same ECLASS concept;
   - different source/component contexts;
   - current compiler would collapse both into the same wildcard instance path.

The fourth case deliberately blocks any assumption that component identity has already been solved
by the existing compiler.

### Normalized conflict comparison

Value-conflict detection uses the derived normalized value rather than raw source spelling.
For example `0.03 mm` and `0,03 mm` produce the same normalized fingerprint and are treated as a
redundant duplicate rather than contradictory values.

### Tests added for milestone 5

- `test_open_property_proposals.py`

It verifies deterministic wildcard target construction, verified IRDI propagation, stable
context-aware slots, value conflicts, normalized duplicates, `idShort` collisions, component
context projection collisions, and the rule that ECLASS `ALARM` decisions never create targets.
