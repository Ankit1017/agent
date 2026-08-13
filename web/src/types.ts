export interface Workspace {
  workspace_id: string;
  label: string;
  path: string;
  created_at: string;
  is_control: boolean;
  busy?: boolean;
}

export interface SessionSummary {
  session_id: string;
  model: string;
  created_at: string;
  updated_at: string;
  preview: string;
  summary: string;
  max_turns_override: number | null;
  token_budget_override: number | null;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  request_number: number | null;
}

export interface ProgressEvent {
  sequence: number;
  call_number: number;
  kind: string;
  summary: string;
  target: string;
  status: "started" | "success" | "warning" | "error";
  duration_ms: number;
  request_number: number | null;
  tags: string[];
  input_tokens: number;
  output_tokens: number;
  usage_source: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface TaskStep {
  step_id: number;
  description: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  result: string;
  requires_verification: boolean;
}

export interface TaskPlan {
  request_number: number;
  goal: string;
  status: "active" | "completed" | "blocked";
  steps: TaskStep[];
  updated_at: string;
}

export interface CompletionEvidence {
  request_number: number;
  changed_files: string[];
  checks: string[];
  sources: string[];
  limitations: string[];
  workflow_id?: string;
  completed_stages?: string[];
  blocked_stages?: string[];
  unmet_requirements?: string[];
}

export interface WorkflowStageDefinition {
  stage_id: string;
  description: string;
  tools: string[];
  required: boolean;
}

export interface WorkflowDefinition {
  workflow_id: string;
  title: string;
  description: string;
  version: number;
  stages: WorkflowStageDefinition[];
}

export interface WorkflowStageRun {
  stage_id: string;
  description: string;
  status: "pending" | "in_progress" | "completed" | "skipped" | "blocked";
  attempts: number;
  result: string;
}

export interface WorkflowRun {
  request_number: number;
  workflow_id: string;
  confidence: number;
  selection_source: string;
  status: "active" | "completed" | "blocked";
  current_stage_id: string;
  stages: WorkflowStageRun[];
}

export interface ProjectMemoryStatus {
  available: boolean;
  generation: number;
  files: number;
  symbols: number;
  dependencies: number;
  embedding_model: string;
  embedding_dimensions: number;
  embedding_available: boolean;
  retrieval_mode: "semantic" | "lexical" | "unavailable";
  updated_at: string;
  stale: boolean;
  warning: string;
}

export interface EvaluationStatus {
  enabled: boolean;
  observations: number;
  pass_rate: number;
  verification_rate: number;
  tokens: number;
  llm_calls: number;
  component_fingerprint: string;
}

export interface EvaluationObservation {
  observation_id: string;
  request_number: number;
  score: {
    outcome: "pass" | "fail" | "unknown";
    verified: boolean;
    llm_calls: number;
    input_tokens: number;
    output_tokens: number;
    runtime_ms: number;
  };
  failures: string[];
}

export interface HarnessCandidate {
  candidate_id: string;
  component_ids: string[];
  proposal: string;
  predicted_changes: string[];
  risks: string[];
  required_suite: string;
  status: "proposed" | "approved" | "rejected";
}

export interface SessionDetail extends SessionSummary {
  workspace: string;
  messages: Message[];
  events: ProgressEvent[];
  plans?: TaskPlan[];
  evidence?: CompletionEvidence[];
  workflows?: WorkflowRun[];
  pending_workflow_override?: string | null;
  has_older_messages: boolean;
  info: string;
}

export interface StreamEvent {
  version: number;
  event_id: number;
  type: string;
  workspace_id: string;
  session_id: string;
  task_id: string;
  request_number: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Approval {
  approval_id: string;
  kind: "command" | "patch" | "maintenance";
  command?: string;
  preview?: string;
  explanation?: string;
  workspace?: string;
  warning?: string;
  action?: string;
  details?: string;
}

export interface SpeechFormat {
  sample_rate: number;
  channels: number;
  sample_width: number;
  encoding: "s16le";
}

export interface SpeechVoice {
  voice_id: string;
  display_name: string;
  language: string;
  audio_format: SpeechFormat;
  license_summary: string;
  default: boolean;
  loaded: boolean;
}

export interface VoiceConversationSummary {
  conversation_id: string;
  title: string;
  model: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  mode?: "protected" | "agent";
  profile_id?: string;
  profile_revision?: number;
  profile_name?: string;
  workspace_label?: string;
  configuration_status?: "current" | "outdated" | "detached" | "unavailable";
  active_task_state?: string | null;
  auto_speak?: boolean;
  voice_id?: string;
  speaking_rate?: number;
}

export interface VoiceConversationMessage {
  message_id: string;
  role: "user" | "assistant";
  content: string;
  speech_text: string;
  created_at: string;
}

export interface VoiceConversationDetail extends VoiceConversationSummary {
  messages: VoiceConversationMessage[];
  has_older_messages: boolean;
  snapshot?: VoiceAgentSnapshot | null;
}

export interface VoiceAgentSnapshot {
  profile_id: string;
  revision: number;
  name: string;
  instructions: string;
  workspace_id: string;
  model: string;
  allowed_tools: string[];
  project_context_enabled: boolean;
  workflow_mode: "off" | "auto";
  max_turns: number;
  token_budget: number;
  context_max_chars: number;
  max_answer_chars: number;
  tool_schema_limit: number;
  tool_activation_limit: number;
  voice_id: string;
  speaking_rate: number;
  auto_speak: boolean;
}

export interface VoiceAgentProfile extends VoiceAgentSnapshot {
  created_at: string;
  updated_at: string;
  available: boolean;
  unavailable_reasons: string[];
}

export interface VoiceAgentTool {
  name: string;
  description: string;
  profile: string;
  risk: string;
  approval_required?: boolean;
}

export interface VoiceAgentCatalog {
  models: string[];
  voices: SpeechVoice[];
  workspaces: Array<{
    workspace_id: string;
    label: string;
    tools: VoiceAgentTool[];
  }>;
  bounds: Record<string, number | number[]>;
  templates: Array<{
    template_id: string;
    name: string;
    immutable?: boolean;
    allowed_tools: string[];
  }>;
}

export interface VoiceConversationTurn {
  conversation: VoiceConversationSummary;
  user_message: VoiceConversationMessage;
  assistant_message: VoiceConversationMessage;
  speech_text: string;
  redacted: boolean;
  usage: { input_tokens: number | null; output_tokens: number | null };
}

export interface SpeechInputStatus {
  enabled: boolean;
  setup?: string;
  wake_phrase?: string;
  languages?: string[];
  max_seconds?: number;
  silence_ms?: number;
  audio_format?: SpeechFormat;
}

export interface SpeechInputTranscript {
  utterance_id: string;
  text: string;
  language: string;
  redacted: boolean;
  completion: "silence" | "max_duration" | "manual_stop";
}

export interface SpeechInputEvent {
  type:
    | "ready"
    | "wake_detected"
    | "speech_started"
    | "transcribing"
    | "transcript"
    | "timeout"
    | "paused"
    | "cancelled"
    | "busy"
    | "error";
  transcript?: SpeechInputTranscript | null;
  reason?: string;
}
