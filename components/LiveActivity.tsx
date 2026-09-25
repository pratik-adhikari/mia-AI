import type { AgentTraceEvent } from "@/lib/types";
import { activityTimeLabel } from "@/lib/activity-time";

export function LiveActivity({
  events,
  backgroundJob,
  reviewPending,
}: {
  events: AgentTraceEvent[];
  backgroundJob: { status: string; metadata: Record<string, unknown> } | null;
  reviewPending: boolean;
}) {
  const completedActivities = new Set(
    events
      .filter((event) => event.status !== "started")
      .map((event) => event.metadata.activityKey)
      .filter((key): key is string => typeof key === "string"),
  );
  const active = [...events]
    .reverse()
    .find(
      (event) =>
        event.status === "started" &&
        (typeof event.metadata.activityKey !== "string" ||
          !completedActivities.has(event.metadata.activityKey)),
    );
  const metadata = backgroundJob?.metadata ?? {};
  const iteration = metadata.iteration;
  const processed = metadata.processedSources;
  const total = metadata.totalSources;

  return (
    <div role="status" aria-live="polite" className="rounded-xl border border-signal/30 bg-white px-4 py-3 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-signal">
        {reviewPending ? "Your review is open; research continues" : "MIA is working"}
      </p>
      <p className="mt-1 text-sm font-medium text-ink">
        {active?.summary ??
          (reviewPending
            ? "New sources are still being collected and mapped in the background."
            : "Preparing the next workflow step…")}
      </p>
      {backgroundJob && (
        <p className="mt-1 text-[11px] text-muted">
          Deep crawl · batch {typeof iteration === "number" ? iteration : 0}
          {typeof processed === "number" && typeof total === "number"
            ? ` · ${processed}/${total} sources scanned`
            : " · discovering sources"}
          {reviewPending ? " · continuing during review" : " · running alongside workflow"}
        </p>
      )}
      <details open className="mt-3 rounded-lg border border-line/70 bg-surface/60">
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-ink">
          Workflow history · {events.length} steps
        </summary>
        <ol className="max-h-80 space-y-1.5 overflow-y-auto border-t border-line/70 px-3 py-2">
        {events.map((event, index) => (
          <li key={event.id} className="flex gap-2 text-[12px] text-muted">
            <span
              className={
                event.status === "started"
                  ? "animate-pulse text-amber-600"
                  : event.status === "failed"
                    ? "text-red-600"
                    : "text-signal"
              }
            >
              {event.status === "started" ? "●" : event.status === "failed" ? "!" : "✓"}
            </span>
            <span className="min-w-0 flex-1">{event.summary}</span>
            <time className="shrink-0 text-[10px] text-muted/80">
              {activityTimeLabel(events, index)}
            </time>
          </li>
        ))}
        {events.length === 0 && (
          <li className="text-[12px] text-muted">Waiting for the first durable progress event…</li>
        )}
        </ol>
      </details>
    </div>
  );
}
