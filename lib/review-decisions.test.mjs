import assert from "node:assert/strict";
import test from "node:test";
import {
  defaultReviewDecision,
  mergeReviewDecisions,
  reviewSubmissionIssue,
} from "./review-decisions.ts";

const semanticId = (value) => ({ type: "ExternalReference", keys: [{ type: "GlobalReference", value }] });
const requirement = (id, path) => ({
  id, templateKey: "Nameplate", templateRelease: "3.0", templatePath: ["Nameplate", path],
  idShort: path, semanticId: semanticId(`https://example.test/${path}`), kind: "value", wildcard: false,
});
const first = requirement("req-aaaaaaaaaaaaaaaaaaaaaaaa", "ManufacturerName");
const second = requirement("req-bbbbbbbbbbbbbbbbbbbbbbbb", "SerialNumber");
const inventory = { requirements: [first, second] };
const mapping = (evidenceId, targetRequirement) => ({
  evidenceId,
  target: {
    templateKey: targetRequirement.templateKey,
    templateRelease: targetRequirement.templateRelease,
    templatePath: targetRequirement.templatePath,
    instancePath: targetRequirement.templatePath,
    idShort: targetRequirement.idShort,
    semanticId: targetRequirement.semanticId,
    listInstanceBindings: [],
  },
});
const row = (id, evidenceId, proposedMapping, overrides = {}) => ({
  id, evidenceId, status: "uncertain", targetKind: "requirement",
  requirementId: proposedMapping ? first.id : null,
  alternativeRequirementIds: [], alternativeTargets: [], mapping: proposedMapping,
  ...overrides,
});

test("uncertain Jev proposal defaults to its verified explicit target", () => {
  const proposal = row("review-one", "evidence-one", mapping("evidence-one", first));
  assert.deepEqual(defaultReviewDecision(proposal, inventory), {
    reviewId: "review-one", decision: "change_target", correctedRequirementId: first.id,
  });
  assert.equal(reviewSubmissionIssue([proposal], {
    "review-one": defaultReviewDecision(proposal, inventory),
  }, inventory, null), null);
  assert.equal(defaultReviewDecision(proposal, { requirements: [] }), undefined);
});

test("later batch keeps an edited choice across changed cycle IDs and defaults new rows", () => {
  const old = row("review-old", "evidence-one", mapping("evidence-one", first));
  const updated = row("review-new", "evidence-one", mapping("evidence-one", first));
  const added = row("review-added", "evidence-two", mapping("evidence-two", second), {
    requirementId: second.id,
  });
  const merged = mergeReviewDecisions(
    { "review-old": { reviewId: old.id, decision: "irrelevant" } },
    [old], [updated, added], inventory, new Set(["evidence-one"]),
  );
  assert.deepEqual(merged[updated.id], { reviewId: updated.id, decision: "irrelevant" });
  assert.deepEqual(merged[added.id], {
    reviewId: added.id, decision: "change_target", correctedRequirementId: second.id,
  });
  assert.equal(merged[old.id], undefined);
});

test("an untouched row refreshes its default when a later proposal changes", () => {
  const old = row("review-old", "evidence-one", mapping("evidence-one", first));
  const updated = row("review-new", "evidence-one", mapping("evidence-one", second), {
    requirementId: second.id,
  });
  const merged = mergeReviewDecisions(
    { [old.id]: defaultReviewDecision(old, inventory) },
    [old], [updated], inventory, new Set(),
  );
  assert.equal(merged[updated.id].correctedRequirementId, second.id);
});

test("one complete explicit batch is valid after individual correction", () => {
  const a = row("review-a", "evidence-a", mapping("evidence-a", first));
  const b = row("review-b", "evidence-b", mapping("evidence-b", first));
  const choices = {
    [a.id]: defaultReviewDecision(a, inventory),
    [b.id]: { reviewId: b.id, decision: "change_target", correctedRequirementId: second.id },
  };
  assert.equal(reviewSubmissionIssue([a, b], choices, inventory, null), null);
});

test("abstention has no fabricated target and needs a disposition", () => {
  const abstained = row("review-empty", "evidence-empty", null);
  assert.equal(defaultReviewDecision(abstained, inventory), undefined);
  assert.match(reviewSubmissionIssue([abstained], {}, inventory, null), /Choose a target or disposition/);
  assert.equal(reviewSubmissionIssue([abstained], {
    [abstained.id]: { reviewId: abstained.id, decision: "unmapped" },
  }, inventory, null), null);
  assert.match(reviewSubmissionIssue([abstained], {
    [abstained.id]: { reviewId: abstained.id, decision: "change_target", correctedRequirementId: "req-invalid" },
  }, inventory, null), /fixed official value target/);
});

test("duplicate projection targets block confirmation, including existing mappings", () => {
  const a = row("review-a", "evidence-a", mapping("evidence-a", first));
  const b = row("review-b", "evidence-b", mapping("evidence-b", first));
  const choices = {
    [a.id]: defaultReviewDecision(a, inventory),
    [b.id]: defaultReviewDecision(b, inventory),
  };
  assert.match(reviewSubmissionIssue([a, b], choices, inventory, null), /same projected field/);
  assert.match(reviewSubmissionIssue([a], { [a.id]: choices[a.id] }, inventory, {
    mapped: [mapping("existing-evidence", first)],
  }), /same projected field/);
  choices[b.id] = { reviewId: b.id, decision: "unmapped" };
  assert.equal(reviewSubmissionIssue([a, b], choices, inventory, null), null);
});

test("direct semantic proposal uses only a supplied verified target", () => {
  const proposed = mapping("evidence-direct", first);
  const direct = row("review-direct", "evidence-direct", proposed, {
    targetKind: "direct", requirementId: null, alternativeTargets: [proposed.target],
  });
  const choice = defaultReviewDecision(direct, inventory);
  assert.deepEqual(choice, {
    reviewId: direct.id, decision: "change_target",
    correctedSemanticId: first.semanticId.keys[0].value,
  });
  assert.equal(reviewSubmissionIssue([direct], { [direct.id]: choice }, inventory, null), null);
  assert.equal(defaultReviewDecision({ ...direct, alternativeTargets: [] }, inventory), undefined);
});
