"use client";

import { useState } from "react";
import type { CoverageReport, EvidenceRecord, FieldMapping, MappingResult, Requirement, RequirementCoverage, RequirementInventory } from "@/lib/types";

type Filter = "all" | "missing" | "candidate" | "satisfied";

function key(template: string, path: string[]) {
  return [template, ...path].join("\0");
}

function status(mappings: FieldMapping[], coverage?: RequirementCoverage) {
  if (mappings.some((item) => item.status === "auto" || item.status === "approved")) return "satisfied";
  if (mappings.length) return "candidate";
  if (coverage?.status === "satisfied") return "candidate";
  return coverage?.status ?? "missing";
}

export function CoveragePanel({ report, inventory, evidence, mappingResult }: {
  report: CoverageReport | null;
  inventory: RequirementInventory | null;
  evidence: EvidenceRecord[];
  mappingResult: MappingResult | null;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const templates = report?.inventory ?? inventory;
  if (!templates) return <p className="mx-auto max-w-sm pt-20 text-center text-[13px] leading-relaxed text-muted">Import a product website to inspect the official template fields.</p>;

  const coverageById = new Map(report?.coverage.map((item) => [item.requirementId, item]) ?? []);
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));
  const mappingsByTarget = new Map<string, FieldMapping[]>();
  for (const mapping of [...(mappingResult?.mapped ?? []), ...(mappingResult?.ambiguous ?? [])]) {
    const target = key(mapping.target.templateKey, mapping.target.templatePath);
    mappingsByTarget.set(target, [...(mappingsByTarget.get(target) ?? []), mapping]);
  }
  const valuesFor = (requirement: Requirement) => mappingsByTarget.get(key(requirement.templateKey, requirement.templatePath)) ?? [];
  const statusFor = (requirement: Requirement) => status(valuesFor(requirement), coverageById.get(requirement.id));

  return <div className="space-y-4">
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">Official IDTA templates</p>
      <h2 className="mt-1 text-xl font-semibold text-ink">Template</h2>
      <p className="mt-2 text-[12px] leading-relaxed text-muted">Every actionable end field and structure in the enabled templates. Values come from source evidence; review candidates are not final DPP values.</p>
      <p className="mt-2 font-mono text-[11px] text-muted">{templates.requirements.length} template endpoints · {templates.requirements.filter((item) => statusFor(item) === "satisfied").length} mapped</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {(["all", "missing", "candidate", "satisfied"] as const).map((value) => <button key={value} onClick={() => setFilter(value)} className={`rounded-full px-3 py-1 text-[11px] capitalize ${filter === value ? "bg-ink text-white" : "border border-hairline bg-paper text-muted"}`}>{value === "satisfied" ? "Mapped" : value === "candidate" ? "Needs review" : value}</button>)}
      </div>
    </div>
    {templates.selectedTemplates.map((template) => {
      const requirements = templates.requirements.filter((item) => item.templateKey === template.key);
      const visible = requirements.filter((item) => filter === "all" || statusFor(item) === filter || (filter === "candidate" && coverageById.get(item.id)?.status === "ambiguous"));
      const mapped = requirements.filter((item) => statusFor(item) === "satisfied").length;
      return <details key={`${template.key}-${template.release}`} open className="rounded-xl border border-hairline bg-paper">
        <summary className="cursor-pointer px-4 py-3"><span className="flex justify-between gap-3"><span className="text-[13px] font-semibold">{template.family} <span className="font-mono text-[10px] text-muted">v{template.release}</span></span><span className="font-mono text-[11px] text-muted">{mapped} / {requirements.length} mapped</span></span></summary>
        <div className="space-y-2 border-t border-hairline p-3">
          {visible.map((requirement) => <RequirementCard key={requirement.id} requirement={requirement} coverage={coverageById.get(requirement.id)} mappings={valuesFor(requirement)} evidenceById={evidenceById} />)}
          {visible.length === 0 && <p className="p-3 text-[12px] text-muted">No template endpoints match this filter.</p>}
        </div>
      </details>;
    })}
  </div>;
}

function RequirementCard({ requirement, coverage, mappings, evidenceById }: {
  requirement: Requirement;
  coverage?: RequirementCoverage;
  mappings: FieldMapping[];
  evidenceById: Map<string, EvidenceRecord>;
}) {
  const state = status(mappings, coverage);
  const mapped = state === "satisfied";
  const sourceIds = coverage?.status === "satisfied" ? coverage.supportingEvidenceIds : coverage?.candidateEvidenceIds ?? [];
  return <article className="rounded-lg bg-mist/60 p-3">
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className={`font-mono text-[12px] font-semibold ${mapped ? "text-ok" : "text-ink"}`}>{requirement.idShort ?? requirement.templatePath.at(-1)}</p>
        <p className={`mt-1 break-words font-mono text-[10px] ${mapped ? "text-ok" : "text-muted"}`}>{requirement.templatePath.join(" / ")}</p>
      </div>
      <span className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] uppercase ${mapped ? "bg-ok/10 text-ok" : state === "candidate" || state === "ambiguous" ? "bg-yellow-100 text-yellow-800" : "bg-paper text-muted"}`}>{mapped ? "✓ Mapped" : state === "candidate" || state === "ambiguous" ? "Review" : "Empty"}</span>
    </div>
    <p className="mt-2 text-[11px] leading-relaxed text-ink">{requirement.description || "The official template does not provide a description for this endpoint."}</p>
    <p className="mt-2 text-[10px] text-muted">{requirement.kind === "structural" ? "Structure" : requirement.modelType}{requirement.valueType ? ` · ${requirement.valueType}` : ""}{requirement.unit ? ` · ${requirement.unit}` : ""}{requirement.allowedValues.length ? ` · Allowed: ${requirement.allowedValues.join(", ")}` : ""} · {requirement.required ? "Required" : requirement.conditional ? "Required when parent exists" : "Optional"}{requirement.wildcard ? " · Open/repeated target" : ""}</p>
    {mappings.length ? <div className="mt-3 space-y-1.5 border-t border-hairline pt-2">{mappings.map((mapping) => <div key={mapping.id} className="text-[11px]"><span className="text-muted">{mapping.status === "auto" || mapping.status === "approved" ? "Mapped value" : "Proposed value"}: </span><strong className={mapping.status === "auto" || mapping.status === "approved" ? "text-ok" : "text-yellow-800"}>{mapping.sourceValue}</strong><span className="ml-1 text-muted">from {mapping.sourceField}</span></div>)}</div>
      : sourceIds.length ? <div className="mt-3 space-y-1 border-t border-hairline pt-2">{sourceIds.map((id) => { const item = evidenceById.get(id); return item ? <p key={id} className="text-[11px] text-muted">Candidate evidence: {item.sourceLabel ?? item.predicate} = {String(item.value)}{item.unit ? ` ${item.unit}` : ""}</p> : null; })}</div>
      : <p className="mt-3 text-[11px] text-muted">No source-backed value assigned.</p>}
  </article>;
}
