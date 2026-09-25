import type {
  AgentReviewDecision,
  MappingResult,
  MappingTarget,
  Requirement,
  RequirementInventory,
  SemanticReviewItem,
} from "./types";

function fixedRequirement(inventory: RequirementInventory | null, id: string | null | undefined) {
  return inventory?.requirements.find((requirement) =>
    requirement.id === id && requirement.kind === "value" &&
    !requirement.wildcard && requirement.semanticId,
  );
}

function proposedRequirement(item: SemanticReviewItem, inventory: RequirementInventory | null) {
  if (!item.mapping) return undefined;
  const target = item.mapping.target;
  const matches = (requirement: Requirement) =>
    requirement.templateKey === target.templateKey &&
    requirement.templateRelease === target.templateRelease &&
    JSON.stringify(requirement.templatePath) === JSON.stringify(target.templatePath);
  const supplied = fixedRequirement(inventory, item.requirementId);
  if (supplied && matches(supplied)) return supplied;
  const candidates = inventory?.requirements.filter((requirement) =>
    matches(requirement) && fixedRequirement(inventory, requirement.id),
  ) ?? [];
  return candidates.length === 1 ? candidates[0] : undefined;
}

function verifiedDirectTarget(item: SemanticReviewItem, semanticId: string) {
  const candidates = item.alternativeTargets.filter(
    (target) => target.semanticId.keys[0]?.value === semanticId,
  );
  return candidates.length === 1 ? candidates[0] : undefined;
}

export function defaultReviewDecision(
  item: SemanticReviewItem,
  inventory: RequirementInventory | null,
): AgentReviewDecision | undefined {
  if (!item.mapping) return undefined;
  if (item.targetKind === "direct") {
    const semanticId = item.mapping.target.semanticId.keys[0]?.value;
    if (!semanticId || !verifiedDirectTarget(item, semanticId)) return undefined;
    return { reviewId: item.id, decision: "change_target", correctedSemanticId: semanticId };
  }
  if (item.status === "uncertain") {
    const requirement = proposedRequirement(item, inventory);
    return requirement
      ? { reviewId: item.id, decision: "change_target", correctedRequirementId: requirement.id }
      : undefined;
  }
  return { reviewId: item.id, decision: "keep" };
}

export function mergeReviewDecisions(
  previous: Record<string, AgentReviewDecision>,
  previousRows: SemanticReviewItem[],
  nextRows: SemanticReviewItem[],
  inventory: RequirementInventory | null,
  editedEvidenceIds: ReadonlySet<string>,
): Record<string, AgentReviewDecision> {
  const oldRows = new Map(previousRows.map((row) => [row.evidenceId, row]));
  const next: Record<string, AgentReviewDecision> = {};
  for (const row of nextRows) {
    const old = oldRows.get(row.evidenceId);
    const edited = old && editedEvidenceIds.has(row.evidenceId)
      ? previous[old.id]
      : undefined;
    const choice = edited
      ? { ...edited, reviewId: row.id }
      : defaultReviewDecision(row, inventory);
    if (choice) next[row.id] = choice;
  }
  return next;
}

function projectionIdentity(target: MappingTarget): string {
  return JSON.stringify([
    target.instancePath,
    target.listInstanceBindings.map((binding) => [binding.templatePath, binding.instanceKey]),
  ]);
}

export function reviewSubmissionIssue(
  rows: SemanticReviewItem[],
  decisions: Record<string, AgentReviewDecision>,
  inventory: RequirementInventory | null,
  mappingResult: MappingResult | null,
): string | null {
  const seen = new Map<string, string>();
  const reviewedEvidence = new Set(rows.map((row) => row.evidenceId));
  const targets: { evidenceId: string; target: MappingTarget }[] =
    (mappingResult?.mapped ?? [])
      .filter((mapping) => !reviewedEvidence.has(mapping.evidenceId))
      .map((mapping) => ({ evidenceId: mapping.evidenceId, target: mapping.target }));

  for (const row of rows) {
    const choice = decisions[row.id];
    if (!choice || choice.reviewId !== row.id) return "Choose a target or disposition for every review row.";
    if (["unmapped", "irrelevant", "reject"].includes(choice.decision)) continue;
    if (choice.decision === "keep") {
      if (!row.mapping || (row.status === "uncertain" && row.targetKind !== "direct")) {
        return "An uncertain proposal needs an explicit verified target.";
      }
      targets.push({ evidenceId: row.evidenceId, target: row.mapping.target });
      continue;
    }
    if (choice.decision !== "change_target") return "Choose a valid review decision.";
    if (row.targetKind === "direct") {
      const target = verifiedDirectTarget(row, choice.correctedSemanticId ?? "");
      if (!target) return "Choose a verified semantic target for each direct mapping.";
      targets.push({ evidenceId: row.evidenceId, target });
    } else {
      const requirement = fixedRequirement(inventory, choice.correctedRequirementId);
      if (!requirement) return "Choose a fixed official value target for each mapping.";
      targets.push({
        evidenceId: row.evidenceId,
        target: {
          templateKey: requirement.templateKey,
          templateRelease: requirement.templateRelease,
          templatePath: requirement.templatePath,
          instancePath: requirement.templatePath,
          idShort: requirement.idShort ?? requirement.templatePath.at(-1) ?? "",
          semanticId: requirement.semanticId!,
          listInstanceBindings: [],
        },
      });
    }
  }
  for (const { evidenceId, target } of targets) {
    const identity = projectionIdentity(target);
    const first = seen.get(identity);
    if (first) {
      return `Two selections target the same projected field (${first} and ${evidenceId}). Change or dismiss one before confirming.`;
    }
    seen.set(identity, evidenceId);
  }
  return null;
}
