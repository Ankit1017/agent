import type {
  ProjectMemoryStatus,
  EvaluationStatus,
  EvaluationObservation,
  HarnessCandidate,
  SessionDetail,
  SessionSummary,
  Workspace,
  WorkflowDefinition,
  SpeechVoice,
  Audio2FaceStatus,
  AnimatedSpeechResponse,
  VoiceConversationDetail,
  VoiceConversationSummary,
  VoiceConversationTurn,
  SpeechInputStatus,
  VoiceAgentCatalog,
  VoiceAgentProfile,
} from "./types";

let csrfToken = "";

export const clientId = (() => {
  const stored = sessionStorage.getItem("harness-client-id");
  if (stored) return stored;
  const created = crypto.randomUUID().replaceAll("-", "");
  sessionStorage.setItem("harness-client-id", created);
  return created;
})();

export async function bootstrap(): Promise<{
  csrf_token: string;
  model: string;
  models: string[];
  workspaces: Workspace[];
  speech_enabled: boolean;
  speech_max_chars: number;
  voice_conversation_enabled: boolean;
  speech_input_enabled: boolean;
  audio2face_enabled: boolean;
}> {
  const value = await request<{
    csrf_token: string;
    model: string;
    models: string[];
    workspaces: Workspace[];
    speech_enabled: boolean;
    speech_max_chars: number;
    voice_conversation_enabled: boolean;
    speech_input_enabled: boolean;
    audio2face_enabled: boolean;
  }>("/api/v1/bootstrap");
  csrfToken = value.csrf_token;
  return value;
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET")
    headers.set("X-Harness-CSRF", csrfToken);
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(String(body.detail ?? response.statusText));
  }
  return (await response.json()) as T;
}

export const api = {
  speechInputStatus: () =>
    request<SpeechInputStatus>("/api/v1/speech/input/status"),
  voiceConversations: () =>
    request<VoiceConversationSummary[]>("/api/v1/speech/conversations"),
  voiceConversation: (id: string, offset = 0, limit = 100) =>
    request<VoiceConversationDetail>(
      `/api/v1/speech/conversations/${id}?offset=${offset}&limit=${limit}`,
    ),
  createVoiceConversation: (model?: string, profileId?: string) =>
    request<VoiceConversationDetail>("/api/v1/speech/conversations", {
      method: "POST",
      body: JSON.stringify({
        model: model || null,
        profile_id: profileId || null,
      }),
    }),
  voiceAgentCatalog: () =>
    request<VoiceAgentCatalog>("/api/v1/speech/agent-profiles/catalog"),
  voiceAgentProfiles: () =>
    request<VoiceAgentProfile[]>("/api/v1/speech/agent-profiles"),
  createVoiceAgentProfile: (
    value: Omit<
      VoiceAgentProfile,
      | "profile_id"
      | "revision"
      | "created_at"
      | "updated_at"
      | "available"
      | "unavailable_reasons"
    >,
  ) =>
    request<VoiceAgentProfile>("/api/v1/speech/agent-profiles", {
      method: "POST",
      body: JSON.stringify(value),
    }),
  updateVoiceAgentProfile: (
    id: string,
    value: Omit<
      VoiceAgentProfile,
      | "profile_id"
      | "revision"
      | "created_at"
      | "updated_at"
      | "available"
      | "unavailable_reasons"
    >,
  ) =>
    request<VoiceAgentProfile>(`/api/v1/speech/agent-profiles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(value),
    }),
  cloneVoiceAgentProfile: (id: string) =>
    request<VoiceAgentProfile>(`/api/v1/speech/agent-profiles/${id}/clone`, {
      method: "POST",
    }),
  deleteVoiceAgentProfile: (id: string) =>
    request<{ deleted: boolean }>(`/api/v1/speech/agent-profiles/${id}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation: id }),
    }),
  upgradeVoiceConversationProfile: (
    conversationId: string,
    profileId: string,
    revision: number,
  ) =>
    request<VoiceConversationDetail>(
      `/api/v1/speech/conversations/${conversationId}/profile-upgrade`,
      {
        method: "POST",
        body: JSON.stringify({ profile_id: profileId, revision }),
      },
    ),
  completeVoiceAgentTurn: (id: string, text: string) =>
    request<{
      task: { task_id: string; workspace_id: string; state: string };
      conversation: VoiceConversationSummary;
    }>(`/api/v1/speech/conversations/${id}/agent-turns`, {
      method: "POST",
      body: JSON.stringify({ text, client_id: clientId }),
    }),
  cancelTask: (taskId: string) =>
    request<{ state: string }>(`/api/v1/tasks/${taskId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ client_id: clientId }),
    }),
  updateVoiceConversation: (
    id: string,
    value: { title?: string; model?: string },
  ) =>
    request<VoiceConversationDetail>(`/api/v1/speech/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(value),
    }),
  deleteVoiceConversation: (id: string) =>
    request<{ deleted: boolean }>(`/api/v1/speech/conversations/${id}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation: id }),
    }),
  completeVoiceTurn: (id: string, text: string) =>
    request<VoiceConversationTurn>(`/api/v1/speech/conversations/${id}/turns`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  speechVoices: () => request<SpeechVoice[]>("/api/v1/speech/voices"),
  audio2faceStatus: () =>
    request<Audio2FaceStatus>("/api/v1/speech/audio2face/status"),
  generateAudio2Face: (
    text: string,
    voiceId: string,
    rate: number,
    avatarId: string,
    signal: AbortSignal,
  ) =>
    request<AnimatedSpeechResponse>("/api/v1/speech/audio2face/generate", {
      method: "POST",
      body: JSON.stringify({
        text,
        voice_id: voiceId,
        rate,
        avatar_id: avatarId || undefined,
      }),
      signal,
    }),
  streamSpeech: async (
    text: string,
    voiceId: string,
    rate: number,
    signal: AbortSignal,
  ) => {
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Harness-CSRF": csrfToken,
    });
    const response = await fetch("/api/v1/speech/stream", {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify({ text, voice_id: voiceId, rate }),
      signal,
    });
    if (!response.ok) {
      const body = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(String(body.detail ?? response.statusText));
    }
    return response;
  },
  workspaces: () => request<Workspace[]>("/api/v1/workspaces"),
  validateWorkspace: (label: string, path: string) =>
    request<{ challenge_id: string; resolved_path: string; warning: string }>(
      "/api/v1/workspaces/validate",
      { method: "POST", body: JSON.stringify({ label, path }) },
    ),
  confirmWorkspace: (challengeId: string, approved: boolean) =>
    request<Workspace>("/api/v1/workspaces/confirm", {
      method: "POST",
      body: JSON.stringify({ challenge_id: challengeId, approved }),
    }),
  removeWorkspace: (id: string) =>
    request(`/api/v1/workspaces/${id}`, { method: "DELETE" }),
  sessions: (workspaceId: string) =>
    request<SessionSummary[]>(`/api/v1/workspaces/${workspaceId}/sessions`),
  projectMemory: (workspaceId: string) =>
    request<ProjectMemoryStatus | null>(
      `/api/v1/workspaces/${workspaceId}/project-memory`,
    ),
  workflows: (workspaceId: string) =>
    request<WorkflowDefinition[]>(
      `/api/v1/workspaces/${workspaceId}/workflows`,
    ),
  evaluationStatus: (workspaceId: string) =>
    request<EvaluationStatus>(
      `/api/v1/workspaces/${workspaceId}/evaluations/status`,
    ),
  evaluationHistory: (workspaceId: string) =>
    request<EvaluationObservation[]>(
      `/api/v1/workspaces/${workspaceId}/evaluations/history`,
    ),
  candidates: (workspaceId: string) =>
    request<HarnessCandidate[]>(`/api/v1/workspaces/${workspaceId}/candidates`),
  proposeCandidate: (
    workspaceId: string,
    sessionId: string,
    componentId = "",
  ) =>
    request<{ candidate: HarnessCandidate }>(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/candidates`,
      {
        method: "POST",
        body: JSON.stringify({
          client_id: clientId,
          component_id: componentId,
        }),
      },
    ),
  decideCandidate: (
    workspaceId: string,
    candidateId: string,
    approved: boolean,
    feedback = "",
  ) =>
    request<{ candidate: HarnessCandidate }>(
      `/api/v1/workspaces/${workspaceId}/candidates/${candidateId}`,
      {
        method: "PUT",
        body: JSON.stringify({ approved, feedback }),
      },
    ),
  newSession: (workspaceId: string) =>
    request<SessionDetail>(`/api/v1/workspaces/${workspaceId}/sessions`, {
      method: "POST",
    }),
  session: (workspaceId: string, sessionId: string) =>
    request<SessionDetail>(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}`,
    ),
  submit: (
    workspaceId: string,
    sessionId: string,
    prompt: string,
    workflowId?: string,
  ) =>
    request<{
      task_id: string;
      display_prompt: string;
      redacted: boolean;
    }>(`/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/requests`, {
      method: "POST",
      body: JSON.stringify({
        prompt,
        client_id: clientId,
        workflow_id: workflowId || null,
      }),
    }),
  approval: (
    approvalId: string,
    workspaceId: string,
    approved: boolean,
    feedback: string,
  ) =>
    request(`/api/v1/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({
        workspace_id: workspaceId,
        client_id: clientId,
        approved,
        feedback,
      }),
    }),
  summarize: (workspaceId: string, sessionId: string) =>
    request(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/summarize`,
      {
        method: "POST",
        body: JSON.stringify({ client_id: clientId }),
      },
    ),
  export: (workspaceId: string, sessionId: string, format: "md" | "csv") =>
    request<{ path: string }>(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/export`,
      { method: "POST", body: JSON.stringify({ format }) },
    ),
  updateTurns: (workspaceId: string, sessionId: string, value: number | null) =>
    request(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/max-turns`,
      {
        method: "PUT",
        body: JSON.stringify({ value }),
      },
    ),
  updateModel: (workspaceId: string, sessionId: string, model: string | null) =>
    request<{ model: string; models: string[] }>(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/model`,
      {
        method: "PUT",
        body: JSON.stringify({ model }),
      },
    ),
  updateQuota: (workspaceId: string, sessionId: string, value: number | null) =>
    request(`/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/quota`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  command: (workspaceId: string, sessionId: string, value: string) =>
    request<Record<string, unknown>>(
      `/api/v1/workspaces/${workspaceId}/sessions/${sessionId}/commands`,
      {
        method: "POST",
        body: JSON.stringify({ value, client_id: clientId }),
      },
    ),
};

export function speechInputSocket(): WebSocket {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(
    `${scheme}://${location.host}/api/v1/speech/input/stream`,
  );
}

export function speechInputStartFrame(mode: "wake" | "tap"): string {
  return JSON.stringify({
    type: "start",
    csrf_token: csrfToken,
    mode,
    sample_rate: 16000,
    channels: 1,
    sample_width: 2,
    encoding: "s16le",
  });
}
