import type { AgentTraceEvent } from "@/lib/types";
import { activityTimeLabel } from "@/lib/activity-time";

export function AgentActivity({ events }: { events: AgentTraceEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-zinc-500">No MIA agent activity yet.</p>;
  }

  return (
    <div className="space-y-3">
      {events.map((event, index) => (
        <details
          key={event.id}
          className="rounded-xl border border-zinc-200 bg-white px-4 py-3"
        >
          <summary className="cursor-pointer list-none">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-zinc-900">
                  {event.summary}
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  {event.toolName ?? event.eventType}
                </p>
              </div>
              <span className="text-xs uppercase tracking-wide text-zinc-500">
                {event.status}
              </span>
            </div>
          </summary>
          <div className="mt-3 space-y-1 border-t border-zinc-100 pt-3 text-xs text-zinc-600">
            <p>{activityTimeLabel(events, index)}</p>
            {event.inputSummary && <p>Input: {event.inputSummary}</p>}
            {event.outputSummary && <p>Output: {event.outputSummary}</p>}
            {event.durationMs !== null && <p>Recorded duration: {(event.durationMs / 1000).toFixed(1)} s</p>}
            {Object.keys(event.metadata).length > 0 && (
              <pre className="mt-2 overflow-x-auto rounded-lg bg-zinc-50 p-2 font-mono text-[10px]">
                {JSON.stringify(event.metadata, null, 2)}
              </pre>
            )}
          </div>
        </details>
      ))}
    </div>
  );
}
