"use client";

import { useState } from "react";
import { JevDecisionTrail } from "@/components/JevDecisionTrail";
import { SourceVerificationLink } from "@/components/SourceVerificationLink";
import type {
  AgentReviewDecision,
  EvidenceRecord,
  FieldMapping,
  JevPolicyDecision,
  JevRoutingTrace,
  Requirement,
  SemanticReviewItem,
} from "@/lib/types";

export function MappingRow({
  mapping: m,
  evidence,
  elements,
  onDecide,
  onCorrect,
  jevTraces,
  jevPolicy,
  threadId,
}: {
  mapping: FieldMapping;
  evidence?: EvidenceRecord;
  elements: Requirement[];
  onDecide: (id: string, status: "approved" | "rejected", comment?: string) => void;
  onCorrect: (
    id: string,
    target: Requirement,
    correctedValue?: string,
    comment?: string
  ) => void;
  jevTraces?: JevRoutingTrace[];
  jevPolicy?: JevPolicyDecision;
  threadId: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [targetPath, setTargetPath] = useState(elements[0]?.id ?? "");
  const [correctedValue, setCorrectedValue] = useState(m.sourceValue);
  const [comment, setComment] = useState("");
  const attention = m.status === "review" || m.reviewPriority === "confirm" || m.reviewPriority === "alarm"
    ? "review"
    : m.reviewPriority === "optional" ? "check" : "clear";

  const tone =
    m.status === "rejected"
      ? "opacity-45"
      : m.status === "review"
      ? "border-red-300"
      : "border-hairline";

  return (
    <div className={`rounded-xl border bg-paper p-4 shadow-sm ${tone}`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Source fact → template field</p>
        <span className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[11px] uppercase ${attention === "review" ? "bg-red-50 text-red-700" : attention === "check" ? "bg-yellow-100 text-yellow-800" : "bg-ok/10 text-ok"}`}>
          {attention}
        </span>
      </div>
      <div className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] lg:items-stretch">
        <div className="min-w-0 rounded-lg bg-mist/70 p-3">
          <p className="text-[10px] font-medium uppercase tracking-wide text-muted">Evidence</p>
          <p className="mt-1 break-words text-[13px] font-semibold text-ink">{m.sourceField}</p>
          <p className="mt-1 break-words font-mono text-[13px] text-ink">{m.sourceValue}</p>
          {evidence?.contextPath.length ? <p className="mt-1 break-words text-[10px] text-muted">{evidence.contextPath.join(" / ")}</p> : null}
        </div>
        <span className="hidden self-center text-muted lg:block" aria-hidden="true">→</span>
        <div className={`min-w-0 rounded-lg border p-3 ${attention === "clear" ? "border-ok/20 bg-ok/5" : attention === "check" ? "border-yellow-200 bg-yellow-50" : "border-red-200 bg-red-50/40"}`}>
          <p className="text-[10px] font-medium uppercase tracking-wide text-muted">Selected template field</p>
          <p className={`mt-1 break-words text-[13px] font-semibold ${attention === "clear" ? "text-ok" : attention === "check" ? "text-yellow-800" : "text-red-700"}`}>{m.target.idShort ?? m.target.templatePath.at(-1) ?? "Unknown target"}</p>
          <p className="mt-1 break-words text-[10px] text-muted">{m.target.templatePath.join(" / ")}</p>
        </div>
      </div>

      <div className="mt-2"><SourceVerificationLink threadId={threadId} evidenceId={m.evidenceId} /></div>

      <p className="mt-2 inline-block rounded-full bg-mist px-2 py-0.5 font-mono text-[10px] text-muted">
        {m.mappingOrigin === "semantic_agent"
          ? "AI-assisted proposal"
          : m.mappingOrigin === "semantic_engine"
            ? m.target.templatePath.includes("ArbitraryProperty")
              ? "Jev + verified ECLASS"
              : "Jev"
            : m.mappingOrigin === "human"
            ? "Human supplied/corrected"
            : "Deterministic mapping"}
        {m.humanReviewed ? " · reviewed" : ""}
      </p>
      {m.humanReviewed && (
        <p className="mt-1 inline-block rounded-full bg-violet-100 px-2 py-0.5 font-mono text-[10px] font-medium text-violet-700">
          Human · {m.humanActorName || "authenticated reviewer"}
          {m.humanValueKind === "dummy" ? " · DUMMY VALUE" : ""}
        </p>
      )}

      {m.llmReview && (
        <div className="mt-3 rounded-lg border border-signal/15 bg-signal/5 p-3 text-[11px] leading-relaxed">
          <p className="font-semibold text-ink">LLM Review</p>
          <p className="mt-1"><span className="font-medium">Conclusion:</span> {m.llmReview.conclusion}</p>
          <p className="mt-1 text-muted"><span className="font-medium text-ink">Why:</span> {m.llmReview.rationale}</p>
          {m.llmReview.uncertainties.length > 0 && <p className="mt-1 text-muted"><span className="font-medium text-ink">Uncertainty:</span> {m.llmReview.uncertainties.join(" · ")}</p>}
        </div>
      )}

      <JevDecisionTrail traces={jevTraces} policy={jevPolicy} />

      <details className="mt-2.5 rounded-lg border border-hairline bg-mist/60 px-3 py-2">
        <summary className="cursor-pointer text-[12px] font-medium text-ink">
          Mapping conclusion
        </summary>
        <div className="mt-2 space-y-2">
          <p className="text-[11px] leading-relaxed text-muted">Target: {m.target.templatePath.join(" / ")}</p>
          <p className="text-[11px] leading-relaxed text-muted">Review: {attention}</p>
          {m.mappingOrigin !== "semantic_engine" && (
            <p className="text-[11px] leading-relaxed text-muted">{m.assessment.reason}</p>
          )}
          {m.assessment.uncertainties.length > 0 && (
            <div className="border-t border-hairline pt-2">
              <p className="text-[11px] font-medium text-ink">Requires review because</p>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-muted">
                {m.assessment.uncertainties.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </details>

      {/* Approval gate */}
      {m.status === "review" && !editing && (
        <div className="mt-3 space-y-2">
          <input value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Optional comment for this decision" className="w-full rounded-lg border border-hairline bg-paper px-3 py-2 text-[12px]" />
          <div className="flex items-center gap-2">
          <button
            onClick={() => onDecide(m.id, "approved", comment)}
            className="rounded-full bg-ink px-3 py-1.5 text-[12px] font-medium text-white transition-opacity hover:opacity-85"
          >
            Approve
          </button>
          <button
            onClick={() => setEditing(true)}
            className="rounded-full border border-hairline px-3 py-1.5 text-[12px] font-medium transition-colors hover:bg-mist"
          >
            Correct
          </button>
          <button
            onClick={() => onDecide(m.id, "rejected", comment)}
            className="px-1 text-[12px] text-muted transition-colors hover:text-ink"
          >
            Reject
          </button>
          </div>
        </div>
      )}

      {editing && (
        <div className="mt-3 space-y-2">
          <label className="text-[12px] text-muted">
            Map this field to
            <select
              autoFocus
              value={targetPath}
              onChange={(e) => setTargetPath(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-hairline bg-paper px-3 py-2 font-mono text-[12px] focus:border-signal focus:outline-none"
            >
              {elements.map((e) => (
                <option
                  key={e.id}
                  value={e.id}
                >
                  {e.templatePath.join(" / ")}
                  {e.required ? " (required)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-[12px] text-muted">
            Corrected value
            <input
              value={correctedValue}
              onChange={(event) => setCorrectedValue(event.target.value)}
              className="mt-1.5 w-full rounded-lg border border-hairline bg-paper px-3 py-2 text-[12px] text-ink focus:border-signal focus:outline-none"
            />
          </label>
          <div className="flex gap-2">
            <button
              onClick={() => {
                const target = elements.find(
                  (element) =>
                    element.id === targetPath
                );
                if (target) onCorrect(m.id, target, correctedValue, comment);
                setEditing(false);
              }}
              className="rounded-full bg-ink px-3 py-1.5 text-[12px] font-medium text-white"
            >
              Save correction
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-2 text-[12px] text-muted"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {(m.status === "approved" || m.status === "auto") && (
        <p className="mt-2.5 font-mono text-[11px] text-ok">
          {m.status === "auto"
            ? "Cleared automatically"
            : m.humanActorName
              ? `Approved by ${m.humanActorName}`
              : "Approved by human reviewer"}
        </p>
      )}
      {m.status === "rejected" && (
        <button
          onClick={() => onDecide(m.id, "approved")}
          className="mt-2.5 font-mono text-[11px] text-muted underline underline-offset-2"
        >
          Rejected · undo
        </button>
      )}
    </div>
  );
}

export function MappingReviewRow({
  item,
  evidence,
  targets,
  decision,
  onChange,
  jevTraces,
  jevPolicy,
  threadId,
}: {
  item: SemanticReviewItem;
  evidence: EvidenceRecord;
  targets: Requirement[];
  decision?: AgentReviewDecision;
  onChange: (decision: AgentReviewDecision) => void;
  jevTraces?: JevRoutingTrace[];
  jevPolicy?: JevPolicyDecision;
  threadId: string | null;
}) {
  const [comment, setComment] = useState(decision?.comment ?? "");
  const currentTarget = item.mapping
    ? item.targetKind === "direct"
      ? item.mapping.target.semanticId.keys[0]?.value ?? ""
      : targets.find((candidate) =>
          candidate.templateKey === item.mapping?.target.templateKey &&
          candidate.templateRelease === item.mapping?.target.templateRelease &&
          JSON.stringify(candidate.templatePath) === JSON.stringify(item.mapping?.target.templatePath)
        )?.id ?? ""
    : "";
  const action = decision?.decision ?? "";
  const target =
    item.targetKind === "direct"
      ? decision?.correctedSemanticId ?? currentTarget
      : decision?.correctedRequirementId ?? currentTarget;
  const value = decision?.correctedValue ?? String(evidence.value);
  const origin = item.mapping?.mappingOrigin ?? "semantic_agent";

  const update = (next: Partial<AgentReviewDecision>) =>
    onChange({
      reviewId: item.id,
      decision: decision?.decision ?? "keep",
      correctedRequirementId: decision?.correctedRequirementId ?? null,
      correctedSemanticId: decision?.correctedSemanticId ?? null,
      correctedValue: decision?.correctedValue ?? null,
      comment: comment.trim() || null,
      ...next,
    });

  return (
    <article className={`rounded-xl border bg-paper p-4 shadow-sm ${item.status === "uncertain" ? "border-red-300" : "border-hairline"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted">Source fact → proposed template field</p>
        <span className="rounded-full bg-red-50 px-2 py-0.5 font-mono text-[10px] uppercase text-red-700" title={item.reviewPriority === "alarm" ? "Strong disagreement between context scopes" : "Human decision required"}>Review</span>
      </div>
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <div className="min-w-0 rounded-lg bg-mist/70 p-3">
          <p className="text-[10px] font-medium uppercase tracking-wide text-muted">Evidence</p>
          <p className="mt-1 break-words text-[13px] font-semibold text-ink">{evidence.sourceLabel ?? evidence.predicate}</p>
          <p className="mt-1 break-words font-mono text-[13px] text-ink">{String(evidence.value)}{evidence.unit ? ` ${evidence.unit}` : ""}</p>
          {evidence.contextPath.length > 0 && <p className="mt-1 break-words text-[10px] text-muted">{evidence.contextPath.join(" / ")}</p>}
        </div>
        <div className="min-w-0 rounded-lg border border-red-200 bg-red-50/40 p-3">
          <p className="text-[10px] font-medium uppercase tracking-wide text-muted">Proposed template field</p>
          <p className="mt-1 break-words text-[13px] font-semibold text-red-700">{item.mapping?.target.idShort ?? "No target selected"}</p>
          {item.mapping && <p className="mt-1 break-words text-[10px] text-muted">{item.mapping.target.templatePath.join(" / ")}</p>}
        </div>
      </div>
      <div className="mt-2"><SourceVerificationLink threadId={threadId} evidenceId={evidence.id} /></div>
      <JevDecisionTrail traces={jevTraces} policy={jevPolicy} />
      <details className="mt-2 text-[11px] text-muted">
        <summary className="cursor-pointer">Review conclusion</summary>
        <p className="mt-1">Target: {item.mapping?.target.templatePath.join(" / ") ?? "No target selected"}</p>
        <p className="mt-1">Review: {item.reviewPriority ?? "required"}</p>
        {origin !== "semantic_engine" && <p className="mt-1">{item.reason}</p>}
      </details>
      <p className="mt-2 font-mono text-[10px] text-muted">Source: {origin === "semantic_engine" ? "Jev" : origin === "semantic_agent" ? "Earlier LLM proposal" : origin}</p>
      {item.alternativeRequirementIds.length > 0 && (
        <p className="mt-1 text-[11px] text-muted">
          Alternatives: {item.alternativeRequirementIds.join(", ")}
        </p>
      )}
      {item.targetKind === "direct" && item.alternativeTargets.length > 0 && (
        <p className="mt-1 text-[11px] text-muted">
          Verified ECLASS alternatives:{" "}
          {item.alternativeTargets
            .map(
              (candidate) =>
                `${candidate.idShort} [${candidate.semanticId.keys[0]?.value ?? "unknown"}]`
            )
            .join(", ")}
        </p>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <label className="text-[11px] text-muted">
          Decision
          <select
            value={action}
            onChange={(event) => {
              const nextDecision = event.target.value as AgentReviewDecision["decision"];
              update({
                decision: nextDecision,
                ...(nextDecision === "change_target" && currentTarget
                  ? item.targetKind === "direct"
                    ? { correctedSemanticId: currentTarget }
                    : { correctedRequirementId: currentTarget }
                  : {}),
              });
            }}
            className="mt-1 w-full rounded-lg border border-hairline bg-paper px-3 py-2 text-[12px] text-ink"
          >
            {!decision && <option value="">{item.mapping ? "Choose target…" : "Choose a disposition…"}</option>}
            {item.mapping && item.status !== "uncertain" && item.targetKind !== "direct" && <option value="keep">Confirm proposed target</option>}
            <option value="change_target">{item.mapping ? "Confirm or change target/value" : "Select an official target"}</option>
            <option value="unmapped">Mark unmapped</option>
            <option value="irrelevant">Mark irrelevant</option>
            <option value="reject">Reject invalid evidence</option>
          </select>
        </label>
        <label className="text-[11px] text-muted">
          Comment
          <input
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            onBlur={() => decision && update({ comment: comment.trim() || null })}
            className="mt-1 w-full rounded-lg border border-hairline bg-paper px-3 py-2 text-[12px] text-ink"
          />
        </label>
      </div>
      {action === "change_target" && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className="text-[11px] text-muted">
            {item.targetKind === "direct" ? "Verified ECLASS concept" : "Official target"}
            <select
              value={target}
              onChange={(event) =>
                item.targetKind === "direct"
                  ? update({
                      correctedSemanticId: event.target.value,
                      correctedRequirementId: null,
                    })
                  : update({
                      correctedRequirementId: event.target.value,
                      correctedSemanticId: null,
                    })
              }
              className="mt-1 w-full rounded-lg border border-hairline bg-paper px-3 py-2 font-mono text-[11px] text-ink"
            >
              <option value="">Choose target…</option>
              {item.targetKind === "direct"
                ? item.alternativeTargets.map((candidate) => {
                    const semanticId = candidate.semanticId.keys[0]?.value ?? "";
                    return (
                      <option key={semanticId} value={semanticId}>
                        {candidate.idShort} · {semanticId}
                      </option>
                    );
                  })
                : targets.map((requirement) => (
                    <option key={requirement.id} value={requirement.id}>
                      {requirement.templatePath.join(" / ")}
                    </option>
                  ))}
            </select>
          </label>
          <label className="text-[11px] text-muted">
            Corrected supported value
            <input
              value={value}
              onChange={(event) => update({ correctedValue: event.target.value })}
              className="mt-1 w-full rounded-lg border border-hairline bg-paper px-3 py-2 text-[12px] text-ink"
            />
          </label>
        </div>
      )}
    </article>
  );
}
