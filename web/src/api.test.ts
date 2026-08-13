import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("crypto", {
  randomUUID: () => "12345678-1234-1234-1234-123456789012",
});

async function response(value: unknown, ok = true) {
  return {
    ok,
    statusText: ok ? "OK" : "Bad Request",
    json: async () => value,
  } as Response;
}

describe("browser API client", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    sessionStorage.clear();
  });

  it("bootstraps CSRF and sends guarded mutations", async () => {
    fetchMock.mockResolvedValueOnce(
      await response({
        csrf_token: "csrf",
        model: "model",
        models: ["model"],
        workspaces: [],
      }),
    );
    const { api, bootstrap, clientId } = await import("./api");
    expect((await bootstrap()).model).toBe("model");
    expect(clientId).toHaveLength(32);

    fetchMock.mockResolvedValue(await response({ path: "export.md" }));
    await api.validateWorkspace("Project", "E:\\project");
    await api.confirmWorkspace("challenge", true);
    await api.removeWorkspace("workspace");
    await api.sessions("workspace");
    await api.projectMemory("workspace");
    await api.workflows("workspace");
    await api.evaluationStatus("workspace");
    await api.evaluationHistory("workspace");
    await api.candidates("workspace");
    await api.proposeCandidate("workspace", "session", "tool_profiles");
    await api.decideCandidate("workspace", "candidate", true);
    await api.newSession("workspace");
    await api.session("workspace", "session");
    await api.submit("workspace", "session", "prompt");
    await api.approval("approval", "workspace", false, "feedback");
    await api.summarize("workspace", "session");
    await api.export("workspace", "session", "md");
    await api.updateTurns("workspace", "session", 20);
    await api.updateModel("workspace", "session", "gpt-5.5");
    await api.updateQuota("workspace", "session", null);
    await api.command("workspace", "session", "/help");
    await api.speechVoices();
    await api.speechInputStatus();
    await api.streamSpeech("hello", "voice", 1, new AbortController().signal);
    await api.voiceConversations();
    await api.voiceConversation("conversation", 10, 20);
    await api.createVoiceConversation("model");
    await api.createVoiceConversation();
    await api.updateVoiceConversation("conversation", { title: "Renamed" });
    await api.deleteVoiceConversation("conversation");
    await api.completeVoiceTurn("conversation", "hello");
    await api.voiceAgentCatalog();
    await api.voiceAgentProfiles();
    const profile = {
      name: "Reader",
      instructions: "",
      workspace_id: "workspace",
      model: "model",
      allowed_tools: ["read_file"],
      project_context_enabled: true,
      workflow_mode: "off" as const,
      max_turns: 8,
      token_budget: 0,
      context_max_chars: 30000,
      max_answer_chars: 1500,
      tool_schema_limit: 8,
      tool_activation_limit: 5,
      voice_id: "voice",
      speaking_rate: 1,
      auto_speak: true,
    };
    await api.createVoiceAgentProfile(profile);
    await api.updateVoiceAgentProfile("profile", profile);
    await api.cloneVoiceAgentProfile("profile");
    await api.deleteVoiceAgentProfile("profile");
    await api.upgradeVoiceConversationProfile("conversation", "profile", 2);
    await api.completeVoiceAgentTurn("conversation", "inspect it");
    await api.cancelTask("task");

    const guarded = fetchMock.mock.calls
      .slice(1)
      .filter((call) => call[1]?.method && call[1]?.method !== "GET")
      .map((call) => call[1]?.headers as Headers);
    expect(
      guarded.every((headers) => headers.get("X-Harness-CSRF") === "csrf"),
    ).toBe(true);
  });

  it("translates API errors", async () => {
    fetchMock.mockResolvedValue(await response({ detail: "Denied" }, false));
    const { request } = await import("./api");
    await expect(request("/failure")).rejects.toThrow("Denied");
  });

  it("falls back when an error object omits detail", async () => {
    fetchMock.mockResolvedValue(await response({}, false));
    const { request } = await import("./api");
    await expect(request("/failure")).rejects.toThrow("Bad Request");
  });

  it("uses HTTP status text when an error body is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      statusText: "Unavailable",
      json: async () => {
        throw new Error("not json");
      },
    } as unknown as Response);
    const { request } = await import("./api");
    await expect(request("/failure")).rejects.toThrow("Unavailable");
  });

  it("translates streamed speech errors without exposing response bodies", async () => {
    fetchMock.mockResolvedValue(
      await response({ detail: "Speech busy" }, false),
    );
    const { api } = await import("./api");
    await expect(
      api.streamSpeech("hello", "voice", 1, new AbortController().signal),
    ).rejects.toThrow("Speech busy");

    fetchMock.mockResolvedValue({
      ok: false,
      statusText: "Unavailable",
      json: async () => {
        throw new Error("invalid response");
      },
    } as unknown as Response);
    await expect(
      api.streamSpeech("hello", "voice", 1, new AbortController().signal),
    ).rejects.toThrow("Unavailable");
  });

  it("builds protected local microphone socket and start frames", async () => {
    const socket = vi.fn();
    vi.stubGlobal("WebSocket", socket);
    vi.stubGlobal("location", { protocol: "http:", host: "localhost:5173" });
    const { speechInputSocket, speechInputStartFrame } = await import("./api");
    speechInputSocket();
    expect(socket).toHaveBeenLastCalledWith(
      "ws://localhost:5173/api/v1/speech/input/stream",
    );
    expect(JSON.parse(speechInputStartFrame("wake"))).toMatchObject({
      type: "start",
      mode: "wake",
      sample_rate: 16000,
      channels: 1,
      sample_width: 2,
      encoding: "s16le",
    });

    vi.stubGlobal("location", { protocol: "https:", host: "local.test" });
    speechInputSocket();
    expect(socket).toHaveBeenLastCalledWith(
      "wss://local.test/api/v1/speech/input/stream",
    );
  });
});
