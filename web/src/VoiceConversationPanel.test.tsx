import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import VoiceConversationPanel from "./VoiceConversationPanel";

const {
  apiMock,
  bootstrapMock,
  enqueueMock,
  stopMock,
  inputClient,
  inputHandler,
} = vi.hoisted(() => ({
  apiMock: {
    speechVoices: vi.fn(),
    voiceConversations: vi.fn(),
    voiceConversation: vi.fn(),
    createVoiceConversation: vi.fn(),
    updateVoiceConversation: vi.fn(),
    deleteVoiceConversation: vi.fn(),
    completeVoiceTurn: vi.fn(),
    streamSpeech: vi.fn(),
    speechInputStatus: vi.fn(),
  },
  bootstrapMock: vi.fn(),
  enqueueMock: vi.fn(),
  stopMock: vi.fn(),
  inputClient: {
    enable: vi.fn().mockResolvedValue(undefined),
    beginTap: vi.fn(),
    finish: vi.fn(),
    pause: vi.fn(),
    rearm: vi.fn(),
    cancel: vi.fn(),
    disable: vi.fn().mockResolvedValue(undefined),
  },
  inputHandler: { current: vi.fn() as (event: unknown) => void },
}));

vi.mock("./api", () => ({
  bootstrap: () => bootstrapMock(),
  api: apiMock,
  clientId: "test-client",
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
      level = () => 0.4;
    },
  };
});

vi.mock("./speech-input-client", () => ({
  LocalSpeechInputClient: class {
    constructor(handler: (event: unknown) => void) {
      inputHandler.current = handler;
    }
    enable = inputClient.enable;
    beginTap = inputClient.beginTap;
    finish = inputClient.finish;
    pause = inputClient.pause;
    rearm = inputClient.rearm;
    cancel = inputClient.cancel;
    disable = inputClient.disable;
  },
}));

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
  license_summary: "Prototype",
  default: true,
  loaded: true,
};
const summary = {
  conversation_id: "a".repeat(32),
  title: "Saved conversation",
  model: "model-a",
  message_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const detail = {
  ...summary,
  messages: [],
  has_older_messages: false,
};

function streamResponse(): Response {
  let sent = false;
  return {
    headers: new Headers({
      "X-Speech-Sample-Rate": "22050",
      "X-Speech-Channels": "1",
      "X-Speech-Sample-Width": "2",
    }),
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined };
          sent = true;
          return { done: false, value: new Uint8Array([1, 0]) };
        },
      }),
    },
  } as unknown as Response;
}

describe("protected voice conversation page", () => {
  beforeEach(() => {
    for (const mock of Object.values(apiMock)) mock.mockReset();
    bootstrapMock.mockReset();
    enqueueMock.mockReset();
    stopMock.mockReset();
    for (const mock of Object.values(inputClient)) mock.mockClear();
    bootstrapMock.mockResolvedValue({
      voice_conversation_enabled: true,
      speech_enabled: true,
      model: "model-a",
      models: ["model-a", "model-b"],
      speech_input_enabled: false,
    });
    apiMock.speechInputStatus.mockResolvedValue({
      enabled: false,
      setup: "Run local speech input setup",
    });
    apiMock.speechVoices.mockResolvedValue([voice]);
    apiMock.streamSpeech.mockResolvedValue(streamResponse());
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

  it("creates one turn, displays Markdown, and automatically speaks its answer", async () => {
    apiMock.voiceConversations.mockResolvedValue([]);
    apiMock.createVoiceConversation.mockResolvedValue(detail);
    apiMock.completeVoiceTurn.mockResolvedValue({
      conversation: { ...summary, message_count: 2 },
      user_message: {
        message_id: "u".repeat(32),
        role: "user",
        content: "hello",
        speech_text: "",
        created_at: "now",
      },
      assistant_message: {
        message_id: "r".repeat(32),
        role: "assistant",
        content: "**Hello** there",
        speech_text: "Hello there",
        created_at: "now",
      },
      speech_text: "Hello there",
      redacted: false,
      usage: { input_tokens: 2, output_tokens: 3 },
    });
    render(<VoiceConversationPanel onDirect={vi.fn()} />);
    await screen.findByText(/Start a new protected/);
    fireEvent.change(screen.getByLabelText("Voice conversation message"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send and speak" }));
    expect(
      await screen.findByText(
        (_, element) =>
          element?.tagName === "P" && element.textContent === "Hello there",
      ),
    ).toBeVisible();
    await waitFor(() =>
      expect(apiMock.completeVoiceTurn).toHaveBeenCalledWith(
        summary.conversation_id,
        "hello",
      ),
    );
    expect(apiMock.completeVoiceTurn).toHaveBeenCalledTimes(1);
    expect(apiMock.streamSpeech).toHaveBeenCalledWith(
      "Hello there",
      "voice",
      1,
      expect.any(AbortSignal),
    );
    expect(enqueueMock).toHaveBeenCalled();
  });

  it("speaks a saved answer after reload without making an LLM call", async () => {
    apiMock.voiceConversations.mockResolvedValue([summary]);
    apiMock.voiceConversation.mockResolvedValue({
      ...detail,
      messages: [
        {
          message_id: "r".repeat(32),
          role: "assistant",
          content: "Saved **answer**",
          speech_text: "Saved answer",
          created_at: "now",
        },
      ],
    });
    render(<VoiceConversationPanel onDirect={vi.fn()} />);
    await screen.findByText(
      (_, element) =>
        element?.tagName === "P" && element.textContent === "Saved answer",
    );
    fireEvent.click(screen.getByRole("button", { name: "Speak" }));
    await waitFor(() => expect(apiMock.streamSpeech).toHaveBeenCalled());
    expect(apiMock.completeVoiceTurn).not.toHaveBeenCalled();
  });

  it("switches mode and supports rename and confirmed delete", async () => {
    const onDirect = vi.fn();
    apiMock.voiceConversations.mockResolvedValue([summary]);
    apiMock.voiceConversation.mockResolvedValue(detail);
    apiMock.updateVoiceConversation.mockResolvedValue({
      ...detail,
      title: "Renamed",
    });
    apiMock.deleteVoiceConversation.mockResolvedValue({ deleted: true });
    vi.spyOn(window, "prompt").mockReturnValue("Renamed");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<VoiceConversationPanel onDirect={onDirect} />);
    await screen.findByText("Ready for one tool-free model call");
    fireEvent.click(
      screen.getByRole("button", { name: "Direct Text-to-Speech" }),
    );
    expect(onDirect).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() =>
      expect(apiMock.updateVoiceConversation).toHaveBeenCalled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(apiMock.deleteVoiceConversation).toHaveBeenCalledWith(
        summary.conversation_id,
      ),
    );
  });

  it("enables wake input and auto-submits each local transcript exactly once", async () => {
    bootstrapMock.mockResolvedValue({
      voice_conversation_enabled: true,
      speech_enabled: true,
      speech_input_enabled: true,
      model: "model-a",
      models: ["model-a"],
    });
    apiMock.speechInputStatus.mockResolvedValue({
      enabled: true,
      wake_phrase: "hey buddy",
    });
    apiMock.voiceConversations.mockResolvedValue([]);
    apiMock.createVoiceConversation.mockResolvedValue(detail);
    apiMock.completeVoiceTurn.mockResolvedValue({
      conversation: { ...summary, message_count: 2 },
      user_message: {
        message_id: "u".repeat(32),
        role: "user",
        content: "spoken message",
        speech_text: "",
        created_at: "now",
      },
      assistant_message: {
        message_id: "r".repeat(32),
        role: "assistant",
        content: "Reply",
        speech_text: "Reply",
        created_at: "now",
      },
      speech_text: "Reply",
      redacted: false,
      usage: { input_tokens: null, output_tokens: null },
    });
    render(<VoiceConversationPanel onDirect={vi.fn()} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Enable Hey Buddy" }),
    );
    await waitFor(() =>
      expect(inputClient.enable).toHaveBeenCalledWith("wake"),
    );
    const transcript = {
      type: "transcript" as const,
      transcript: {
        utterance_id: "utterance",
        text: "spoken message",
        language: "en",
        redacted: false,
        completion: "silence" as const,
      },
    };
    inputHandler.current(transcript);
    inputHandler.current(transcript);
    await waitFor(() =>
      expect(apiMock.completeVoiceTurn).toHaveBeenCalledWith(
        summary.conversation_id,
        "spoken message",
      ),
    );
    expect(apiMock.completeVoiceTurn).toHaveBeenCalledTimes(1);
    expect(inputClient.pause).toHaveBeenCalled();
    await waitFor(() => expect(inputClient.rearm).toHaveBeenCalled());
  });
});
