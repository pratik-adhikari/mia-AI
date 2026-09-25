import type { AgentTraceEvent } from "@/lib/types";

export function activityTimeLabel(
  events: AgentTraceEvent[],
  index: number,
): string {
  const event = events[index];
  const activityKey = event.metadata.activityKey;
  if (event.status !== "started" && typeof activityKey === "string") {
    const started = events
      .slice(0, index)
      .reverse()
      .find(
        (item) =>
          item.status === "started" && item.metadata.activityKey === activityKey,
      );
    if (started) {
      return `Took ${secondsBetween(started.timestamp, event.timestamp)}s`;
    }
  }

  if (index === 0) {
    return `Started ${new Date(event.timestamp).toLocaleTimeString()}`;
  }
  return `+${secondsBetween(events[index - 1].timestamp, event.timestamp)}s since previous step`;
}

function secondsBetween(start: string, end: string): string {
  const seconds = Math.max(0, Date.parse(end) - Date.parse(start)) / 1000;
  return Number.isFinite(seconds) ? seconds.toFixed(1) : "?";
}
