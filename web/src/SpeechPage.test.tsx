import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SpeechPage from "./SpeechPage";

vi.mock("./VoiceConversationPanel", () => ({
  default: ({ onDirect }: { onDirect: () => void }) => (
    <button onClick={onDirect}>Open direct mode</button>
  ),
}));
vi.mock("./SpeechApp", () => ({
  default: ({ onVoice }: { onVoice: () => void }) => (
    <button onClick={onVoice}>Open conversation mode</button>
  ),
}));

describe("speech page modes", () => {
  afterEach(cleanup);

  it("defaults to voice conversation and preserves direct TTS as a second mode", () => {
    render(<SpeechPage />);
    fireEvent.click(screen.getByRole("button", { name: "Open direct mode" }));
    expect(
      screen.getByRole("button", { name: "Open conversation mode" }),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Open conversation mode" }),
    );
    expect(
      screen.getByRole("button", { name: "Open direct mode" }),
    ).toBeVisible();
  });
});
