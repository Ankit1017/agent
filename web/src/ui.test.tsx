import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppDialog, AppHeader, Badge, EmptyState, StatusRegion } from "./ui";

describe("shared harness presentation", () => {
  beforeEach(() => localStorage.clear());
  afterEach(cleanup);

  it("shows module navigation and persists theme and density preferences", () => {
    render(<AppHeader current="chat" title="Workspace Chat" />);
    expect(screen.getByRole("link", { name: "Chat" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    fireEvent.click(screen.getByRole("button", { name: /Theme:/ }));
    expect(localStorage.getItem("harness-theme")).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: /Density:/ }));
    expect(localStorage.getItem("harness-density")).toBe("compact");
  });

  it("closes mobile navigation with Escape and restores focus", () => {
    render(<AppHeader current="speech" title="Voice Conversation" />);
    const trigger = screen.getByRole("button", {
      name: "Open module navigation",
    });
    fireEvent.click(trigger);
    expect(screen.getByRole("navigation", { name: /Mobile/ })).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(
      screen.queryByRole("navigation", { name: /Mobile/ }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("provides semantic status, badges, and empty states", () => {
    render(
      <>
        <StatusRegion tone="success">Ready</StatusRegion>
        <Badge tone="warning">Outdated</Badge>
        <EmptyState title="Nothing here">Create the first item.</EmptyState>
      </>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Ready");
    expect(screen.getByText("Outdated")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Nothing here" })).toBeVisible();
  });

  it("closes dialogs with Escape", () => {
    const close = vi.fn();
    render(
      <AppDialog
        title="Confirm"
        onClose={close}
        actions={<button>Save</button>}
      >
        Review this action.
      </AppDialog>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
  });
});
