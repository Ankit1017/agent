import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SpeechApp from "./SpeechApp";

const bootstrapMock = vi.fn();
const voicesMock = vi.fn();
const streamMock = vi.fn();
const enqueueMock = vi.fn();
const stopMock = vi.fn();

vi.mock("./api", () => ({
  bootstrap: () => bootstrapMock(),
  api: {
    speechVoices: () => voicesMock(),
    streamSpeech: (...args: unknown[]) => streamMock(...args),
  },
}));

vi.mock("./speech-audio", async (original) => {
  const actual = await original<typeof import("./speech-audio")>();
  return {
    ...actual,
    PcmPlayer: class {
      start = vi.fn().mockResolvedValue(undefined);
      finish = vi.fn().mockResolvedValue(undefined);
      enqueue = enqueueMock;
      stop = stopMock;
      level = () => 0.5;
    },
  };
});

const voice = {
  voice_id: "voice",
  display_name: "Local Voice",
  language: "English",
  audio_format: {
    sample_rate: 22050,
    channels: 1,
    sample_width: 2,
    encoding: "s16le",
  },
  license_summary: "Prototype terms",
  default: true,
  loaded: true,
};

function streamResponse(redacted = false): Response {
  const chunks = [new Uint8Array([1, 0]), new Uint8Array([2, 0])];
  let index = 0;
  return {
    headers: new Headers({
      "X-Speech-Sample-Rate": "22050",
      "X-Speech-Channels": "1",
      "X-Speech-Sample-Width": "2",
      "X-Speech-Redacted": String(redacted),
    }),
    body: {
      getReader: () => ({
        read: async () =>
          index < chunks.length
            ? { done: false, value: chunks[index++] }
            : { done: true, value: undefined },
      }),
    },
  } as unknown as Response;
}

describe("independent speech page", () => {
  beforeEach(() => {
    bootstrapMock.mockReset();
    voicesMock.mockReset();
    streamMock.mockReset();
    enqueueMock.mockReset();
    stopMock.mockReset();
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    vi.stubGlobal(
      "AudioContext",
      class {
        currentTime = 0;
        resume = vi.fn().mockResolvedValue(undefined);
        close = vi.fn().mockResolvedValue(undefined);
      },
    );
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 1),
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });
  afterEach(cleanup);

  it("shows setup guidance while speech is disabled", async () => {
    bootstrapMock.mockResolvedValue({
      speech_enabled: false,
      speech_max_chars: 5000,
    });
    render(<SpeechApp onVoice={vi.fn()} />);
    expect(await screen.findByText(/setup-voices.ps1/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Speak" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Chat" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("streams, exposes redaction, enables replay/download, and reacts to audio", async () => {
    bootstrapMock.mockResolvedValue({
      speech_enabled: true,
      speech_max_chars: 5000,
    });
    voicesMock.mockResolvedValue([voice]);
    streamMock.mockResolvedValue(streamResponse(true));
    render(<SpeechApp onVoice={vi.fn()} />);
    await screen.findByText("Ready to speak locally");
    fireEvent.change(screen.getByLabelText("Speech text"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speak" }));
    await screen.findByText("Speech complete");
    expect(streamMock).toHaveBeenCalledWith(
      "hello",
      "voice",
      1,
      expect.any(AbortSignal),
    );
    expect(enqueueMock).toHaveBeenCalledTimes(2);
    expect(
      screen.getByText(/Sensitive-looking text was redacted/),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Replay" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Download WAV" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    await waitFor(() =>
      expect(screen.getByText("Replay complete")).toBeVisible(),
    );
    expect(screen.getByRole("img", { name: "Replay complete" })).toHaveClass(
      "speech-avatar",
    );
  });

  it("stops a live request and suppresses motion when reduced motion is preferred", async () => {
    const frame = vi.fn(() => 1);
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    vi.stubGlobal("requestAnimationFrame", frame);
    bootstrapMock.mockResolvedValue({
      speech_enabled: true,
      speech_max_chars: 5000,
    });
    voicesMock.mockResolvedValue([voice]);
    const response = streamResponse();
    const body = response.body!;
    body.getReader = (() => ({
      read: () => new Promise(() => undefined),
    })) as typeof body.getReader;
    streamMock.mockResolvedValue(response);
    render(<SpeechApp onVoice={vi.fn()} />);
    await screen.findByText("Ready to speak locally");
    fireEvent.change(screen.getByLabelText("Speech text"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speak" }));
    await screen.findByText("Speaking");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText("Speech stopped")).toBeVisible();
    expect(stopMock).toHaveBeenCalled();
    expect(frame).not.toHaveBeenCalled();
  });
});
