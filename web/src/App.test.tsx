import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionDetail } from "./types";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("crypto", {
  randomUUID: () => "12345678-1234-1234-1234-123456789012",
});

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor() {
    FakeSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }
  close() {
    this.onclose?.();
  }
  emit(value: unknown) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }
}
vi.stubGlobal("WebSocket", FakeSocket);

const workspace = {
  workspace_id: "workspace-1",
  label: "Agent",
  path: "E:\\agent",
  created_at: "2026-01-01T00:00:00Z",
  is_control: true,
  busy: false,
};
const projectMemory = {
  available: true,
  generation: 2,
  files: 12,
  symbols: 30,
  dependencies: 4,
  embedding_model: "embeddinggemma",
  embedding_dimensions: 768,
  embedding_available: true,
  retrieval_mode: "semantic",
  updated_at: "2026-01-01T00:00:00Z",
  stale: false,
  warning: "",
};
const workflows = [
  {
    workflow_id: "review_changes",
    title: "Review changes",
    description: "Review current changes without modifying files.",
    version: 1,
    stages: [],
  },
];
const evaluationStatus = {
  enabled: true,
  observations: 2,
  pass_rate: 100,
  verification_rate: 100,
  tokens: 40,
  llm_calls: 2,
  component_fingerprint: "fingerprint",
};
const candidates = [
  {
    candidate_id: "candidate-1",
    component_ids: ["tool_profiles"],
    proposal: "Prefer project memory before repeated reads",
    predicted_changes: ["tokens -10%"],
    risks: ["stale index"],
    required_suite: "core",
    status: "proposed" as const,
  },
];
const session: SessionDetail = {
  session_id: "session-1",
  model: "gpt-oss:20b",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  preview: "",
  summary: "",
  max_turns_override: null,
  token_budget_override: null,
  workspace: "E:\\agent",
  messages: [],
  events: [],
  has_older_messages: false,
  info: "Session info",
};
let currentSession = session;

function json(value: unknown, ok = true) {
  return Promise.resolve({
    ok,
    statusText: ok ? "OK" : "Error",
    json: async () => value,
  } as Response);
}

describe("Harness browser", () => {
  afterEach(cleanup);
  beforeEach(() => {
    fetchMock.mockReset();
    FakeSocket.instances = [];
    sessionStorage.clear();
    localStorage.clear();
    currentSession = session;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/bootstrap"))
        return json({
          csrf_token: "csrf",
          model: "gpt-oss:20b",
          models: ["gpt-5.5", "gpt-oss:20b"],
          workspaces: [workspace],
        });
      if (url.endsWith("/sessions")) return json([currentSession]);
      if (url.endsWith("/project-memory")) return json(projectMemory);
      if (url.endsWith("/workflows")) return json(workflows);
      if (url.endsWith("/evaluations/status")) return json(evaluationStatus);
      if (url.endsWith("/evaluations/history")) return json([]);
      if (url.endsWith("/candidates")) return json(candidates);
      if (url.endsWith("/model"))
        return json({ model: "gpt-5.5", models: ["gpt-5.5", "gpt-oss:20b"] });
      if (url.includes("/sessions/session-1/requests"))
        return json({
          task_id: "task-1",
          display_prompt: "Inspect this project",
          redacted: false,
        });
      if (url.includes("/sessions/session-1")) return json(currentSession);
      if (url.endsWith("/workspaces")) return json([workspace]);
      return json({});
    });
  });

  it("loads a session, submits a prompt, and renders live completion", async () => {
    const { default: App } = await import("./App");
    render(<App />);
    expect(await screen.findByText("What shall we build?")).toBeInTheDocument();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    const composer = screen.getByLabelText("Prompt");
    fireEvent.change(composer, { target: { value: "Inspect this project" } });
    fireEvent.click(screen.getByText("Send"));
    expect(await screen.findByText("Inspect this project")).toBeInTheDocument();
    expect(composer).toBeDisabled();
    FakeSocket.instances.at(-1)?.emit({
      version: 1,
      event_id: 1,
      type: "progress",
      workspace_id: "workspace-1",
      session_id: "session-1",
      task_id: "task-1",
      request_number: 1,
      payload: {
        sequence: 1,
        call_number: 1,
        kind: "model_start",
        summary: "Inspecting project",
        target: "model",
        status: "started",
        duration_ms: 0,
        request_number: 1,
        tags: [],
        input_tokens: 0,
        output_tokens: 0,
        usage_source: "unknown",
        created_at: "2026-01-01T00:00:00Z",
      },
      created_at: "2026-01-01T00:00:00Z",
    });
    FakeSocket.instances.at(-1)?.emit({
      version: 1,
      event_id: 2,
      type: "task.completed",
      workspace_id: "workspace-1",
      session_id: "session-1",
      task_id: "task-1",
      request_number: 1,
      payload: { response: "Done" },
      created_at: "2026-01-01T00:00:00Z",
    });
    await waitFor(() => expect(composer).not.toBeDisabled());
  });

  it("shows evaluation metrics and runs controlled evaluation actions", async () => {
    const { default: App } = await import("./App");
    render(<App />);

    expect(await screen.findByText("Harness evaluation")).toBeInTheDocument();
    expect(screen.getByText(/100% pass/)).toBeInTheDocument();
    expect(
      screen.getByText("Prefer project memory before repeated reads"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Run offline suite"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/commands"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.click(screen.getByText("Propose improvement"));
    await waitFor(() =>
      expect(screen.getByText(/proposal recorded/i)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Approve proposal"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/candidates/candidate-1"),
        expect.objectContaining({ method: "PUT" }),
      ),
    );
  });

  it("switches the current session model from the configured allowlist", async () => {
    const { default: App } = await import("./App");
    render(<App />);

    const selector = await screen.findByLabelText("Model for current session");
    fireEvent.change(selector, { target: { value: "gpt-5.5" } });
    expect(
      await screen.findByText("Model switched to gpt-5.5"),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/model"),
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ model: "gpt-5.5" }),
      }),
    );
    fetchMock.mockResolvedValueOnce(
      await json({ detail: "Model unavailable" }, false),
    );
    fireEvent.change(selector, { target: { value: "gpt-oss:20b" } });
    expect(await screen.findByText("Model unavailable")).toBeInTheDocument();
  });

  it("shows project memory and runs an explicit refresh", async () => {
    const { default: App } = await import("./App");
    render(<App />);
    expect(
      await screen.findByText("12 files · 30 symbols · 4 dependencies"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Refresh"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/commands"),
        expect.objectContaining({
          body: expect.stringContaining("/index refresh"),
        }),
      ),
    );
  });

  it("opens help and defaults an approval to rejection", async () => {
    const { default: App } = await import("./App");
    render(<App />);
    await screen.findByText("What shall we build?");
    const composer = screen.getByLabelText("Prompt");
    fireEvent.change(composer, { target: { value: "/help" } });
    fireEvent.click(screen.getByText("Send"));
    expect(await screen.findByText("Harness help")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Close"));
    FakeSocket.instances.at(-1)?.emit({
      version: 1,
      event_id: 2,
      type: "approval.requested",
      workspace_id: "workspace-1",
      session_id: "session-1",
      task_id: "task-1",
      request_number: 1,
      payload: {
        approval_id: "approval-1",
        kind: "command",
        command: "Get-Date",
        warning: "Native command",
      },
      created_at: "2026-01-01T00:00:00Z",
    });
    expect(await screen.findByText("Approve command")).toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(await json({ resolved: true }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Reject"));
    await waitFor(() =>
      expect(screen.queryByText("Approve command")).not.toBeInTheDocument(),
    );
    FakeSocket.instances.at(-1)?.emit({
      version: 1,
      event_id: 3,
      type: "approval.rejected",
      workspace_id: "workspace-1",
      session_id: "session-1",
      task_id: "task-1",
      request_number: 1,
      payload: {},
      created_at: "2026-01-01T00:00:00Z",
    });
  });

  it("shows prompt redaction and submission failures", async () => {
    let submitCount = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/bootstrap"))
        return json({
          csrf_token: "csrf",
          model: "gpt-oss:20b",
          models: ["gpt-5.5", "gpt-oss:20b"],
          workspaces: [workspace],
        });
      if (url.endsWith("/sessions")) return json([currentSession]);
      if (url.endsWith("/workflows")) return json(workflows);
      if (url.endsWith("/evaluations/status")) return json(evaluationStatus);
      if (url.endsWith("/evaluations/history")) return json([]);
      if (url.endsWith("/candidates")) return json(candidates);
      if (url.includes("/requests")) {
        submitCount += 1;
        return submitCount === 1
          ? json({
              task_id: "task-1",
              display_prompt: "token=[REDACTED]",
              redacted: true,
            })
          : json({ detail: "Gateway busy" }, false);
      }
      if (url.includes("/sessions/session-1")) return json(currentSession);
      if (url.endsWith("/workspaces")) return json([workspace]);
      return json({});
    });
    const { default: App } = await import("./App");
    render(<App />);
    await screen.findByText("What shall we build?");
    const composer = screen.getByLabelText("Prompt");
    fireEvent.change(composer, { target: { value: "token=secret" } });
    fireEvent.click(screen.getByText("Send"));
    expect(
      await screen.findByText(
        "Sensitive text was redacted before it was sent.",
      ),
    ).toBeInTheDocument();
    FakeSocket.instances.at(-1)?.emit({
      version: 1,
      event_id: 4,
      type: "task.failed",
      workspace_id: "workspace-1",
      session_id: "session-1",
      payload: {},
      created_at: "2026-01-01T00:00:00Z",
    });
    await waitFor(() => expect(composer).not.toBeDisabled());
    fireEvent.change(composer, { target: { value: "retry" } });
    fireEvent.click(screen.getByText("Send"));
    expect(await screen.findByText("Gateway busy")).toBeInTheDocument();
  });

  it("reports a bootstrap failure without opening a socket", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ detail: "Backend unavailable" }, false),
    );
    const { default: App } = await import("./App");
    render(<App />);
    expect(
      await screen.findByText("Startup error: Backend unavailable"),
    ).toBeInTheDocument();
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("persists themes and renders filtered Markdown activity", async () => {
    currentSession = {
      ...session,
      messages: [
        { role: "user", content: "Check formatting", request_number: 1 },
        {
          role: "assistant",
          content:
            "## Result\n\n| Item | Value |\n|---|---|\n| Readable | Yes |",
          request_number: 1,
        },
      ],
      events: [
        {
          sequence: 1,
          call_number: 1,
          kind: "model_complete",
          summary: "Readable answer",
          target: "final",
          status: "success",
          duration_ms: 250,
          request_number: 1,
          tags: ["review"],
          input_tokens: 2,
          output_tokens: 3,
          usage_source: "provider",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    };
    const { default: App } = await import("./App");
    render(<App />);
    expect(await screen.findByText("Result")).toBeInTheDocument();
    expect(screen.getByText("5 tokens")).toBeInTheDocument();
    expect(screen.getByText("Completed · 1 steps · 0.3s")).toBeInTheDocument();

    const theme = screen.getByRole("button", { name: /Theme:/ });
    fireEvent.click(theme);
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(theme);
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(theme);
    expect(document.documentElement.dataset.theme).toBeUndefined();

    fireEvent.change(screen.getByLabelText("Filter events"), {
      target: { value: "missing" },
    });
    expect(screen.getByText("No activity yet.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter events"), {
      target: { value: "review" },
    });
    expect(screen.getAllByText("Readable answer")).toHaveLength(2);
  });

  it("handles the complete live task lifecycle and mobile activity", async () => {
    const { default: App } = await import("./App");
    render(<App />);
    await screen.findByText("What shall we build?");
    await screen.findByText("Connected");
    const socket = FakeSocket.instances.at(-1)!;
    socket.emit({
      version: 1,
      event_id: 1,
      type: "task.queued",
      workspace_id: "workspace-1",
      payload: {},
      created_at: "2026-01-01T00:00:00Z",
    });
    expect(
      await screen.findByText("Queued for the local model"),
    ).toBeInTheDocument();
    socket.emit({
      version: 1,
      event_id: 2,
      type: "task.started",
      workspace_id: "workspace-1",
      payload: {},
      created_at: "2026-01-01T00:00:00Z",
    });
    expect(await screen.findByText("Working…")).toBeInTheDocument();
    socket.emit({
      version: 1,
      event_id: 3,
      type: "resync_required",
      workspace_id: "workspace-1",
      session_id: "session-1",
      payload: {},
      created_at: "2026-01-01T00:00:00Z",
    });
    socket.emit({
      version: 1,
      event_id: 4,
      type: "task.failed",
      workspace_id: "workspace-1",
      session_id: "session-1",
      payload: { error: "Model unavailable" },
      created_at: "2026-01-01T00:00:00Z",
    });
    expect(await screen.findByText("Model unavailable")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Prompt"), {
      target: { value: "/events" },
    });
    fireEvent.keyDown(screen.getByLabelText("Prompt"), {
      key: "Enter",
      ctrlKey: true,
    });
    await waitFor(() =>
      expect(document.querySelector(".right-panel.mobile-open")).not.toBeNull(),
    );
  });

  it("opens workspace management and creates a fresh session", async () => {
    const created = { ...session, session_id: "session-2" };
    fetchMock.mockImplementation(
      (input: RequestInfo | URL, options?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/bootstrap"))
          return json({
            csrf_token: "csrf",
            model: "gpt-oss:20b",
            models: ["gpt-5.5", "gpt-oss:20b"],
            workspaces: [workspace],
          });
        if (url.endsWith("/sessions") && options?.method === "POST")
          return json(created);
        if (url.endsWith("/sessions")) return json([currentSession]);
        if (url.endsWith("/workflows")) return json(workflows);
        if (url.endsWith("/evaluations/status")) return json(evaluationStatus);
        if (url.endsWith("/evaluations/history")) return json([]);
        if (url.endsWith("/candidates")) return json(candidates);
        if (url.includes("/sessions/session-1")) return json(currentSession);
        if (url.endsWith("/workspaces")) return json([workspace]);
        return json({});
      },
    );
    const { default: App } = await import("./App");
    render(<App />);
    await screen.findByText("What shall we build?");
    fireEvent.click(screen.getByLabelText("Add workspace"));
    expect(
      await screen.findByText("Add an allowlisted workspace"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Cancel"));
    fireEvent.click(screen.getByLabelText("New session"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/sessions"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
