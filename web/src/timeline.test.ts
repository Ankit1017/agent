import { describe, expect, it } from "vitest";
import { buildRequestTimeline } from "./timeline";
import type { ProgressEvent } from "./types";

function event(value: Partial<ProgressEvent>): ProgressEvent {
  return {
    sequence: 1,
    call_number: 1,
    kind: "model_start",
    summary: "Waiting",
    target: "model",
    status: "started",
    duration_ms: 0,
    request_number: 1,
    tags: [],
    input_tokens: 0,
    output_tokens: 0,
    usage_source: "unknown",
    created_at: "2026-01-01T00:00:00Z",
    ...value,
  };
}

describe("request timeline", () => {
  it("merges a tool request and result while preserving final completion", () => {
    const values = [
      event({}),
      event({
        sequence: 2,
        kind: "model_complete",
        summary: "Read files",
        target: "read_files",
        status: "success",
        duration_ms: 800,
      }),
      event({
        sequence: 3,
        kind: "tool_complete",
        target: "read_files",
        status: "success",
        duration_ms: 200,
      }),
      event({
        sequence: 4,
        call_number: 2,
        kind: "model_complete",
        summary: "Answered",
        target: "final",
        status: "warning",
        duration_ms: 400,
      }),
    ];

    const steps = buildRequestTimeline(values, 1);

    expect(steps).toHaveLength(2);
    expect(steps[0]).toMatchObject({ label: "Read files", duration_ms: 1000 });
    expect(steps[1]).toMatchObject({ target: "final", status: "warning" });
  });

  it("keeps standalone tool errors and excludes another request", () => {
    const steps = buildRequestTimeline(
      [
        event({ kind: "tool_error", status: "error", target: "check" }),
        event({ sequence: 2, request_number: 2, kind: "tool_complete" }),
      ],
      1,
    );
    expect(steps).toHaveLength(1);
    expect(steps[0].status).toBe("error");
  });
});
