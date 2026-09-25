import type { JevPolicyDecision, JevRoutingTrace } from "@/lib/types";
import { useState } from "react";

function label(value: string) {
  if (value === "__no_idta_location__") return "No IDTA location";
  if (value === "__unresolved__") return "Unresolved";
  return value.replaceAll("|", " · ").replaceAll("/", " / ");
}

function distribution(options: string[], probabilities: Record<string, number>) {
  const weights = options.map((option) => {
    const value = probabilities[option] ?? 0;
    return Number.isFinite(value) && value > 0 ? value : 0;
  });
  const total = weights.reduce((sum, value) => sum + value, 0);
  if (total === 0) return Object.fromEntries(options.map((option) => [option, 0]));

  // Allocate tenths of a percent so every displayed choice adds to exactly 100.0%.
  const exact = weights.map((value) => value / total * 1000);
  const tenths = exact.map(Math.floor);
  const remainder = 1000 - tenths.reduce((sum, value) => sum + value, 0);
  const order = options.map((_, index) => index).sort((a, b) =>
    (exact[b] - tenths[b]) - (exact[a] - tenths[a]) || a - b
  );
  for (let index = 0; index < remainder; index += 1) tenths[order[index]] += 1;
  return Object.fromEntries(options.map((option, index) => [option, tenths[index]]));
}

function percent(tenths: number) {
  return `${(tenths / 10).toFixed(1)}%`;
}

const scopeDescriptions: Record<string, string> = {
  property: "The source fact and its own hierarchy. No other facts are included.",
  siblings: "The source fact plus facts with the same source hierarchy path.",
  full_product: "A selected sample from the product, prioritizing the source section and nearby facts. Section counts describe the wider product.",
  parent: "The source fact with its immediate parent hierarchy.",
  section: "The source fact plus facts in the same source section.",
};

function scopeName(scope: string) {
  if (scope === "property") return "Property only";
  if (scope === "siblings") return "Same hierarchy";
  return scope === "full_product" ? "Product context" : scope.replaceAll("_", " ");
}

function ScopeInput({ trace }: { trace: JevRoutingTrace }) {
  const [expanded, setExpanded] = useState(false);
  const state = trace.requestState;
  const records = Array.isArray(state?.contextEvidence) ? state.contextEvidence : [];
  const total = typeof state?.contextEvidenceTotal === "number" ? state.contextEvidenceTotal : null;
  const omitted = typeof state?.contextEvidenceOmitted === "number" ? state.contextEvidenceOmitted : null;

  return <div className="mt-2 rounded-lg border border-hairline bg-mist/40 p-2.5 text-[11px]">
    <p className="font-medium text-ink">Source context sent to Jev</p>
    <p className="mt-1 text-muted">{scopeDescriptions[trace.scope] ?? "A bounded source-context view."}</p>
    {state ? <>
      <p className="mt-1 text-muted">{records.length} facts sent{total !== null ? ` from ${total} in this scope` : ""}{omitted ? ` · ${omitted} omitted by the request limit` : ""}. The focus fact may also appear in the context list.</p>
      <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded} className="mt-2 font-medium text-signal underline underline-offset-2">
        {expanded ? "Hide exact source context" : "Show exact source context"}
      </button>
      {expanded && <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-paper p-2 font-mono text-[10px] text-ink">{JSON.stringify(state, null, 2)}</pre>}
    </> : <p className="mt-1 text-muted">Exact input was not saved for this earlier run.</p>}
  </div>;
}

function ChoiceRows({ options, probabilities, choice }: {
  options: string[];
  probabilities: Record<string, number>;
  choice: string;
}) {
  return <div className="space-y-1.5">{options.map((option) => {
    const probability = probabilities[option] ?? 0;
    const selected = option === choice;
    return <div key={option}>
      <div className="flex items-start justify-between gap-2 text-[10px]">
        <span className={`break-all ${selected ? "font-semibold text-ok" : "text-muted"}`} title={option}>{selected ? "● " : ""}{label(option)}</span>
        <span className="shrink-0 font-mono text-ink">{percent(probability)}</span>
      </div>
      <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-mist">
        <div className={`h-full rounded-full ${selected ? "bg-ok" : "bg-signal/35"}`} style={{ width: percent(probability) }} />
      </div>
    </div>;
  })}</div>;
}

export function JevDecisionTrail({
  traces,
  policy,
}: {
  traces?: JevRoutingTrace[];
  policy?: JevPolicyDecision;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!traces?.length) return null;

  return (
    <details onToggle={(event) => setExpanded(event.currentTarget.open)} className="mt-3 rounded-lg border border-hairline bg-mist/40 px-3 py-2">
      <summary className="cursor-pointer text-[12px] font-medium text-ink">
        How Jev chose this target · {traces.length} context {traces.length === 1 ? "view" : "views"}
      </summary>
      {expanded && <div className="mt-3 space-y-3">
        {policy && (
          <div className="rounded-md border border-hairline bg-paper p-2 text-[11px]">
            <p className="font-semibold text-ink">Final policy: {policy.priority.toUpperCase()}</p>
            <p className="mt-1 break-words text-muted">Consensus: {label(policy.consensusSignature)}</p>
            <p className="mt-1 text-muted">{policy.reasons.map(label).join(" · ").replaceAll("_", " ")}</p>
          </div>
        )}
        <p className="text-[11px] text-muted">
          Each scope&apos;s choices total 100%. These are Jev&apos;s relative scores, not calibrated chances of being correct.
        </p>
        <div className="rounded-md border border-hairline bg-paper p-2 text-[10px]">
          <p className="mb-1 font-semibold text-ink">How the selected choice changes with context</p>
          {traces.map((trace, index) => {
            const decision = trace.steps.at(-1)?.decision;
            const step = trace.steps.at(-1);
            const probability = step ? distribution(step.options, step.decision.probabilities)[step.decision.choice] ?? 0 : 0;
            const previous = traces[index - 1]?.steps.at(-1)?.decision;
            const previousStep = traces[index - 1]?.steps.at(-1);
            const previousProbability = previous && previousStep ? distribution(previousStep.options, previous.probabilities)[previous.choice] ?? 0 : 0;
            const delta = index ? ` · ${(probability - previousProbability) >= 0 ? "+" : ""}${((probability - previousProbability) / 10).toFixed(1)} points` : "";
            return <p key={trace.contextViewId} className="mt-1 break-words text-muted"><strong className="capitalize text-ink">{scopeName(trace.scope)}</strong> · {label(decision?.choice ?? "unresolved")} · {percent(probability)}{delta}</p>;
          })}
          <p className="mt-1 text-muted">The selected target can change between scopes; percentages describe each scope&apos;s own choice distribution.</p>
        </div>
        {traces.map((trace) => (
          <section key={trace.contextViewId} className="rounded-md border border-hairline bg-paper p-2.5">
            <div className="flex flex-wrap items-center justify-between gap-1 text-[11px]">
              <strong className="capitalize text-ink">{scopeName(trace.scope)}</strong>
              <span className="text-muted">{trace.terminalReason.replaceAll("_", " ")}</span>
            </div>
            <p className="mt-1 break-all font-mono text-[10px] text-muted">
              {trace.selectedPath.length ? trace.selectedPath.join(" / ") : "No target selected"}
            </p>
            {trace.steps.length > 0 && <p className="mt-1 text-[10px] text-muted">Selected at {percent(distribution(trace.steps.at(-1)!.options, trace.steps.at(-1)!.decision.probabilities)[trace.steps.at(-1)!.decision.choice] ?? 0)} in this scope</p>}
            <ScopeInput trace={trace} />
            <div className="mt-2 space-y-3">
              {trace.steps.map((step, index) => {
                const ranked = [...step.options].sort((a, b) => (step.decision.probabilities[b] ?? 0) - (step.decision.probabilities[a] ?? 0));
                const visible = [step.decision.choice, ...ranked.filter((option) => option !== step.decision.choice)].slice(0, 5);
                const remaining = ranked.filter((option) => !visible.includes(option));
                const displayed = distribution(step.options, step.decision.probabilities);
                return <div key={`${trace.contextViewId}-${index}`} className="border-t border-hairline pt-2">
                  <p className="mb-1 text-[11px] font-medium text-ink">
                    {step.decision.questionId === "template_target" ? `Official template endpoints · ${step.options.length - 2} choices` : step.depth === 0 ? "Submodel" : `Level ${step.depth}`}
                    {step.parentPath.length ? ` · ${step.parentPath.join(" / ")}` : ""}
                    {step.deterministic ? " · one official child" : ""}
                  </p>
                  <ChoiceRows options={visible} probabilities={displayed} choice={step.decision.choice} />
                  {remaining.length > 0 && <details className="mt-2"><summary className="cursor-pointer text-[10px] text-muted">Show remaining {remaining.length} choices · together {percent(remaining.reduce((sum, option) => sum + (displayed[option] ?? 0), 0))}</summary><div className="mt-2"><ChoiceRows options={remaining} probabilities={displayed} choice={step.decision.choice} /></div></details>}
                  <p className="mt-2 text-right font-mono text-[10px] text-muted">All {step.options.length} choices: {percent(Object.values(displayed).reduce((sum, value) => sum + value, 0))}</p>
                </div>;
              })}
            </div>
          </section>
        ))}
      </div>}
    </details>
  );
}
