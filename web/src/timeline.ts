import type { ProgressEvent } from "./types";

export interface TimelineStep {
  key: string;
  label: string;
  target: string;
  status: ProgressEvent["status"];
  duration_ms: number;
  sequence: number;
}

export function buildRequestTimeline(
  events: ProgressEvent[],
  requestNumber: number | null,
): TimelineStep[] {
  const relevant = events.filter(
    (event) => event.request_number === requestNumber,
  );
  const consumed = new Set<number>();
  const steps: TimelineStep[] = [];
  for (const event of relevant) {
    if (event.kind === "model_start") continue;
    if (event.kind === "model_complete" && event.target !== "final") {
      const tools = relevant.filter(
        (candidate) =>
          candidate.call_number === event.call_number &&
          ["tool_complete", "tool_error", "plan_update"].includes(
            candidate.kind,
          ) &&
          !consumed.has(candidate.sequence),
      );
      if (tools.length) {
        for (const tool of tools) {
          consumed.add(tool.sequence);
          steps.push({
            key: `tool-${tool.sequence}`,
            label: event.summary,
            target: tool.target,
            status: tool.status,
            duration_ms: event.duration_ms + tool.duration_ms,
            sequence: tool.sequence,
          });
        }
        continue;
      }
    }
    if (consumed.has(event.sequence)) continue;
    steps.push({
      key: `event-${event.sequence}`,
      label: event.summary,
      target: event.target,
      status: event.status,
      duration_ms: event.duration_ms,
      sequence: event.sequence,
    });
  }
  return steps;
}
