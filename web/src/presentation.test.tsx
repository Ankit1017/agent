import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApprovalDialog,
  CodeBlock,
  EventRow,
  Modal,
  RequestActivity,
  SafeMarkdown,
  SessionMenu,
  WorkspaceDialog,
} from "./App";
import {
  codeLanguage,
  initialTheme,
  nextTheme,
  nodeText,
  statusIcon,
  upsertEvent,
} from "./presentation";
import type { Approval, ProgressEvent, SessionDetail } from "./types";

function event(value: Partial<ProgressEvent> = {}): ProgressEvent {
  return {
    sequence: 1,
    call_number: 1,
    kind: "model_complete",
    summary: "Completed answer",
    target: "final",
    status: "success",
    duration_ms: 500,
    request_number: 1,
    tags: [],
    input_tokens: 0,
    output_tokens: 0,
    usage_source: "unknown",
    created_at: "2026-01-01T00:00:00Z",
    ...value,
  };
}

describe("cross-interface browser presentation", () => {
  afterEach(cleanup);
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        statusText: "OK",
        json: async () => ({ path: "export.md" }),
      } as Response),
    );
  });

  it("renders safe tables, links, and highlighted code", async () => {
    render(
      <SafeMarkdown
        text={
          "| Name | Value |\n|---|---|\n| A<br>B | 1 |\n\n" +
          "[Docs](https://docs.example) [Unsafe](javascript:alert(1))\n\n" +
          "```python\nprint('ok')\n```"
        }
      />,
    );

    expect(
      screen.getByRole("region", { name: "Scrollable table" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Docs/ })).toHaveAttribute(
      "href",
      "https://docs.example",
    );
    expect(
      screen.queryByRole("link", { name: /Unsafe/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Copy"));
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith("print('ok')"),
    );
  });

  it("renders merged activity and explicit status text", () => {
    const events = [
      event({
        sequence: 1,
        call_number: 1,
        target: "read_files",
        summary: "Read files",
      }),
      event({
        sequence: 2,
        call_number: 1,
        kind: "tool_error",
        target: "read_files",
        status: "error",
      }),
      event({ sequence: 3, call_number: 2, status: "warning" }),
    ];
    render(<RequestActivity requestNumber={1} events={events} busy={false} />);
    expect(screen.getByText(/Completed with issues/)).toBeInTheDocument();
    render(<EventRow event={event({ status: "warning", tags: ["review"] })} />);
    expect(screen.getByText(/#review/)).toBeInTheDocument();
  });

  it("renders running, warning-only, and unavailable activity states", () => {
    const view = render(
      <RequestActivity requestNumber={1} events={[]} busy={true} />,
    );
    expect(screen.getByText("Working…")).toBeInTheDocument();
    view.rerender(
      <RequestActivity
        requestNumber={1}
        events={[
          event({
            target: "read_files",
            status: "started",
            summary: "Reading",
          }),
        ]}
        busy={true}
      />,
    );
    expect(screen.getByText(/Working… · Reading/)).toBeInTheDocument();
    view.rerender(
      <RequestActivity
        requestNumber={1}
        events={[event({ status: "warning" })]}
        busy={false}
      />,
    );
    expect(view.container.querySelector(".working.warning")).not.toBeNull();
    view.rerender(
      <RequestActivity requestNumber={1} events={[]} busy={false} />,
    );
    expect(screen.getByText("Activity unavailable")).toBeInTheDocument();
  });

  it("covers theme, node, language, status, and event helpers", () => {
    localStorage.setItem("harness-theme", "light");
    expect(initialTheme()).toBe("light");
    localStorage.setItem("harness-theme", "invalid");
    expect(initialTheme()).toBe("system");
    expect(nextTheme("system")).toBe("dark");
    expect(nextTheme("dark")).toBe("light");
    expect(nextTheme("light")).toBe("system");
    expect(nodeText(["a", <span key="b">b</span>, 3, null])).toBe("ab3");
    expect(codeLanguage(<code className="language-python" />)).toBe("python");
    expect(codeLanguage(<code />)).toBe("text");
    expect(codeLanguage("plain")).toBe("text");
    expect(statusIcon("success")).toBe("OK");
    expect(statusIcon("error")).toBe("!");
    expect(statusIcon("warning")).toBe("!");
    expect(statusIcon("started")).toBe("…");
    expect(upsertEvent([], event())).toHaveLength(1);
    expect(
      upsertEvent([event()], event({ summary: "Updated" }))[0].summary,
    ).toBe("Updated");
    expect(
      upsertEvent(
        [event(), event({ sequence: 2, summary: "Keep" })],
        event({ summary: "Changed" }),
      )[1].summary,
    ).toBe("Keep");
  });

  it("copies direct code blocks and reports completion", async () => {
    render(
      <CodeBlock>
        <code className="language-ts">const value = 1;{"\n"}</code>
      </CodeBlock>,
    );
    expect(screen.getByText("ts")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Copy"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("runs session-menu actions and respects busy state", async () => {
    const session = {
      session_id: "session-1",
      model: "model",
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
      info: "",
    } satisfies SessionDetail;
    const notice = vi.fn();
    const reload = vi.fn();
    const prompt = vi.spyOn(window, "prompt");
    render(
      <SessionMenu
        workspaceId="workspace-1"
        session={session}
        busy={false}
        onNotice={notice}
        onReload={reload}
      />,
    );
    const menu = screen.getByLabelText("Session controls");
    fireEvent.change(menu, { target: { value: "summary" } });
    fireEvent.change(menu, { target: { value: "md" } });
    prompt.mockReturnValueOnce("30");
    fireEvent.change(menu, { target: { value: "turns" } });
    prompt.mockReturnValueOnce("");
    fireEvent.change(menu, { target: { value: "quota" } });
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(notice).toHaveBeenCalledWith("Exported to export.md");
  });

  it("covers remaining session-menu configuration and failure paths", async () => {
    const session = {
      ...event(),
      session_id: "session-1",
      model: "model",
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
      info: "",
    } satisfies SessionDetail;
    const notice = vi.fn();
    const reload = vi.fn();
    const prompt = vi.spyOn(window, "prompt");
    const view = render(
      <SessionMenu
        workspaceId="workspace-1"
        session={session}
        busy={false}
        onNotice={notice}
        onReload={reload}
      />,
    );
    const menu = screen.getByLabelText("Session controls");
    fireEvent.change(menu, { target: { value: "csv" } });
    prompt.mockReturnValueOnce(null);
    fireEvent.change(menu, { target: { value: "turns" } });
    prompt.mockReturnValueOnce("1000");
    fireEvent.change(menu, { target: { value: "quota" } });
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(3));

    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      statusText: "Failed",
      json: async () => ({ detail: "Export failed" }),
    } as Response);
    fireEvent.change(menu, { target: { value: "md" } });
    await waitFor(() => expect(notice).toHaveBeenCalledWith("Export failed"));
    view.rerender(
      <SessionMenu
        workspaceId="workspace-1"
        session={null}
        busy={true}
        onNotice={notice}
        onReload={reload}
      />,
    );
    expect(screen.getByLabelText("Session controls")).toBeDisabled();
  });

  it("defaults approvals to rejection and supports approval", async () => {
    const approval: Approval = {
      approval_id: "approval-1",
      kind: "command",
      command: "Get-Date",
      explanation: "Show time",
      workspace: "E:\\agent",
      warning: "Native execution",
    };
    const close = vi.fn();
    const notice = vi.fn();
    const view = render(
      <ApprovalDialog
        approval={approval}
        workspaceId="workspace-1"
        onClose={close}
        onNotice={notice}
      />,
    );
    expect(screen.getByText("Reject")).toHaveFocus();
    fireEvent.click(screen.getByText("Reject"));
    await waitFor(() => expect(close).toHaveBeenCalled());
    view.rerender(
      <ApprovalDialog
        approval={{ ...approval, kind: "patch", preview: "diff" }}
        workspaceId="workspace-1"
        onClose={close}
        onNotice={notice}
      />,
    );
    fireEvent.click(screen.getByText("Approve"));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(2));
  });

  it("rejects approvals on Escape and shows safe failures", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      statusText: "Failed",
      json: async () => ({ detail: "Approval expired" }),
    } as Response);
    const notice = vi.fn();
    const view = render(
      <ApprovalDialog
        approval={{
          approval_id: "approval-2",
          kind: "maintenance",
          action: "Quarantine file",
          details: "session.json",
        }}
        workspaceId="workspace-1"
        onClose={vi.fn()}
        onNotice={notice}
      />,
    );
    fireEvent.change(screen.getByLabelText("Optional rejection feedback"), {
      target: { value: "Not now" },
    });
    fireEvent.keyDown(view.container.querySelector(".modal-backdrop")!, {
      key: "Escape",
    });
    await waitFor(() =>
      expect(notice).toHaveBeenCalledWith("Approval expired"),
    );
    expect(
      screen.getByText("Review the exact operation before continuing."),
    ).toBeInTheDocument();
  });

  it("validates and confirms an exact workspace", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          challenge_id: "challenge-1",
          resolved_path: "E:\\project",
          warning: "Confirm exact path",
        }),
      } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) } as Response);
    const added = vi.fn();
    render(<WorkspaceDialog onClose={vi.fn()} onAdded={added} />);
    fireEvent.change(screen.getByLabelText("Label"), {
      target: { value: "Project" },
    });
    fireEvent.change(screen.getByLabelText("Absolute Windows path"), {
      target: { value: "E:\\project" },
    });
    fireEvent.click(screen.getByText("Validate"));
    expect(await screen.findByText("Confirm exact path")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Add workspace"));
    await waitFor(() => expect(added).toHaveBeenCalled());
  });

  it("reports workspace validation failures", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      statusText: "Invalid",
      json: async () => ({ detail: "Protected root" }),
    } as Response);
    render(<WorkspaceDialog onClose={vi.fn()} onAdded={vi.fn()} />);
    fireEvent.click(screen.getByText("Validate"));
    expect(await screen.findByText("Protected root")).toBeInTheDocument();
  });

  it("closes a generic modal by its explicit close button", () => {
    const close = vi.fn();
    const view = render(
      <Modal title="Details" onClose={close}>
        <p>Body</p>
      </Modal>,
    );
    const closeButton = view.container.querySelector<HTMLButtonElement>(
      'button[aria-label="Close"]',
    );
    expect(closeButton).not.toBeNull();
    fireEvent.click(closeButton!);
    expect(close).toHaveBeenCalled();
    fireEvent.mouseDown(view.container.querySelector(".modal-backdrop")!);
    expect(close).toHaveBeenCalledTimes(2);
    fireEvent.mouseDown(view.container.querySelector(".modal")!);
    expect(close).toHaveBeenCalledTimes(2);
  });
});
