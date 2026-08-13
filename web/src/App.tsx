import {
  FormEvent,
  ReactNode,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { api, bootstrap, clientId } from "./api";
import { normalizeMarkdownForDisplay, safeExternalHref } from "./markdown";
import {
  codeLanguage,
  initialTheme,
  nextTheme,
  nodeText,
  statusIcon,
  upsertEvent,
} from "./presentation";
import { buildRequestTimeline, type TimelineStep } from "./timeline";
import type {
  Approval,
  EvaluationObservation,
  EvaluationStatus,
  HarnessCandidate,
  ProgressEvent,
  ProjectMemoryStatus,
  SessionDetail,
  SessionSummary,
  StreamEvent,
  Workspace,
  WorkflowDefinition,
} from "./types";

function App() {
  const [theme, setTheme] = useState<"system" | "dark" | "light">(initialTheme);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [connected, setConnected] = useState(false);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [notice, setNotice] = useState("Starting local harness…");
  const [eventFilter, setEventFilter] = useState("");
  const [showWorkspace, setShowWorkspace] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showMobileActivity, setShowMobileActivity] = useState(false);
  const [projectMemory, setProjectMemory] =
    useState<ProjectMemoryStatus | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState("");
  const [evaluationStatus, setEvaluationStatus] =
    useState<EvaluationStatus | null>(null);
  const [evaluationHistory, setEvaluationHistory] = useState<
    EvaluationObservation[]
  >([]);
  const [candidates, setCandidates] = useState<HarnessCandidate[]>([]);
  const lastEvent = useRef(0);
  const workspace = workspaces.find(
    (item) => item.workspace_id === workspaceId,
  );

  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    localStorage.setItem("harness-theme", theme);
  }, [theme]);

  const refreshWorkspaces = useCallback(async () => {
    const values = await api.workspaces();
    setWorkspaces(values);
    return values;
  }, []);

  const loadSession = useCallback(
    async (selectedWorkspace: string, sessionId: string) => {
      const detail = await api.session(selectedWorkspace, sessionId);
      setSession(detail);
      setBusy(
        Boolean(
          workspaces.find((item) => item.workspace_id === selectedWorkspace)
            ?.busy,
        ),
      );
    },
    [workspaces],
  );

  const loadSessions = useCallback(
    async (selectedWorkspace: string) => {
      const values = await api.sessions(selectedWorkspace);
      setProjectMemory(await api.projectMemory(selectedWorkspace));
      setWorkflows(await api.workflows(selectedWorkspace));
      setEvaluationStatus(await api.evaluationStatus(selectedWorkspace));
      setEvaluationHistory(await api.evaluationHistory(selectedWorkspace));
      setCandidates(await api.candidates(selectedWorkspace));
      setSessions(values);
      if (values.length)
        await loadSession(selectedWorkspace, values[0].session_id);
      else setSession(await api.newSession(selectedWorkspace));
    },
    [loadSession],
  );

  useEffect(() => {
    void bootstrap()
      .then(async (data) => {
        setModel(data.model);
        setModels(data.models);
        setWorkspaces(data.workspaces);
        const remembered = localStorage.getItem("harness-workspace");
        const selected = data.workspaces.find(
          (item) => item.workspace_id === remembered,
        )
          ? remembered!
          : (data.workspaces[0]?.workspace_id ?? "");
        setWorkspaceId(selected);
        if (selected) await loadSessions(selected);
        setNotice("Ready");
      })
      .catch((error: Error) => setNotice(`Startup error: ${error.message}`));
    // Bootstrap is intentionally performed once; callbacks read the initial empty state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!workspaceId) return;
    localStorage.setItem("harness-workspace", workspaceId);
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(
      `${protocol}://${location.host}/api/v1/stream?client_id=${clientId}&after=${lastEvent.current}`,
    );
    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onmessage = (message) => {
      const event = JSON.parse(String(message.data)) as StreamEvent;
      lastEvent.current = Math.max(lastEvent.current, event.event_id);
      if (event.workspace_id && event.workspace_id !== workspaceId) return;
      if (
        event.type === "progress" &&
        session &&
        event.session_id === session.session_id
      ) {
        const progress = event.payload as unknown as ProgressEvent;
        setSession((current) =>
          current
            ? { ...current, events: upsertEvent(current.events, progress) }
            : current,
        );
      } else if (event.type === "task.queued") {
        setBusy(true);
        setNotice("Queued for the local model");
      } else if (event.type === "task.started") {
        setBusy(true);
        setNotice("Working…");
      } else if (
        event.type === "task.completed" ||
        event.type === "task.failed"
      ) {
        setBusy(false);
        setNotice(
          event.type === "task.completed"
            ? "Completed"
            : String(event.payload.error ?? "Failed"),
        );
        if (session) void loadSession(workspaceId, session.session_id);
        void refreshWorkspaces();
      } else if (event.type === "approval.requested") {
        setApproval(event.payload as unknown as Approval);
      } else if (event.type.startsWith("approval.")) {
        setApproval(null);
      } else if (event.type === "resync_required" && session) {
        void loadSession(workspaceId, session.session_id);
      }
    };
    return () => socket.close();
    // Reconnect only when the selected workspace/session identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, session?.session_id]);

  async function changeWorkspace(id: string) {
    if (busy) return setNotice("Wait for this workspace task to finish.");
    setWorkspaceId(id);
    setSession(null);
    await loadSessions(id);
  }

  async function createSession() {
    if (!workspaceId || busy) return;
    const created = await api.newSession(workspaceId);
    setSession(created);
    setSessions(await api.sessions(workspaceId));
  }

  async function changeModel(value: string) {
    if (!session || busy) return;
    try {
      const result = await api.updateModel(
        workspaceId,
        session.session_id,
        value,
      );
      setSession({ ...session, model: result.model });
      setNotice(`Model switched to ${result.model}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Model switch failed");
    }
  }

  async function sendPrompt() {
    const value = prompt.trim();
    if (!value || !session || busy) return;
    if (value.startsWith("/")) {
      setPrompt("");
      await runSlashCommand(value);
      return;
    }
    setPrompt("");
    const requestNumber =
      Math.max(0, ...session.messages.map((item) => item.request_number ?? 0)) +
      1;
    setBusy(true);
    try {
      const submitted = await api.submit(
        workspaceId,
        session.session_id,
        value,
        selectedWorkflow || undefined,
      );
      setSelectedWorkflow("");
      setSession({
        ...session,
        messages: [
          ...session.messages,
          {
            role: "user",
            content: submitted.display_prompt,
            request_number: requestNumber,
          },
        ],
      });
      if (submitted.redacted) {
        setNotice("Sensitive text was redacted before it was sent.");
      }
    } catch (error) {
      setBusy(false);
      setNotice(error instanceof Error ? error.message : "Request failed");
    }
  }

  async function runSlashCommand(value: string) {
    if (!session) return;
    const [name] = value.split(/\s+/, 1);
    if (name === "/events") setShowMobileActivity(true);
    if (name === "/help") setShowHelp(true);
    try {
      const result = await api.command(workspaceId, session.session_id, value);
      if (result.session) {
        setSession(result.session as SessionDetail);
        setSessions(await api.sessions(workspaceId));
      }
      setNotice(String(result.message ?? `${name} completed`));
      if (name === "/index" || name === "/memory") {
        setProjectMemory(await api.projectMemory(workspaceId));
      }
      if (name === "/eval" || name === "/candidate") {
        setEvaluationStatus(await api.evaluationStatus(workspaceId));
        setEvaluationHistory(await api.evaluationHistory(workspaceId));
        setCandidates(await api.candidates(workspaceId));
      }
      if (
        !["/help", "/events", "/sessions", "/exit", "/new", "/resume"].includes(
          name,
        )
      ) {
        await loadSession(workspaceId, session.session_id);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Command failed");
    }
  }

  function composerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void sendPrompt();
    }
  }

  const filteredEvents = useMemo(() => {
    const query = eventFilter.trim().toLowerCase();
    if (!session || !query) return session?.events ?? [];
    return session.events.filter((event) =>
      [event.kind, event.summary, event.target, event.status, ...event.tags]
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }, [session, eventFilter]);

  const tokens =
    session?.events.reduce(
      (total, event) => total + event.input_tokens + event.output_tokens,
      0,
    ) ?? 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <span className="logo">H</span>
          <strong>Local AI Harness</strong>
          <a className="top-nav-link" href="/speech">
            Speaking avatar
          </a>
        </div>
        <div className="status-line">
          <span className={`dot ${connected ? "ok" : "bad"}`} />
          {connected ? "Connected" : "Disconnected"}
          <span>{session?.model || model}</span>
          <span>{tokens.toLocaleString()} tokens</span>
          <button
            className="theme-toggle"
            aria-label={`Theme: ${theme}. Change theme`}
            onClick={() => setTheme(nextTheme(theme))}
          >
            Theme: {theme}
          </button>
        </div>
      </header>
      <aside className="left-panel">
        <div className="panel-heading">
          <span>Workspaces</span>
          <button
            aria-label="Add workspace"
            onClick={() => setShowWorkspace(true)}
          >
            ＋
          </button>
        </div>
        <nav aria-label="Workspaces">
          {workspaces.map((item) => (
            <button
              className={`nav-item ${item.workspace_id === workspaceId ? "selected" : ""}`}
              key={item.workspace_id}
              onClick={() => void changeWorkspace(item.workspace_id)}
            >
              <span>{item.label}</span>
              <small>{item.busy ? "Working" : item.path}</small>
            </button>
          ))}
        </nav>
        <div className="panel-heading">
          <span>Sessions</span>
          <button aria-label="New session" onClick={() => void createSession()}>
            ＋
          </button>
        </div>
        <nav aria-label="Sessions" className="sessions">
          {sessions.map((item) => (
            <button
              className={`nav-item ${item.session_id === session?.session_id ? "selected" : ""}`}
              key={item.session_id}
              onClick={() => void loadSession(workspaceId, item.session_id)}
            >
              <span>{item.preview || "New session"}</span>
              <small>{new Date(item.updated_at).toLocaleString()}</small>
            </button>
          ))}
        </nav>
      </aside>
      <main className="conversation">
        <div className="conversation-head">
          <div>
            <strong>{workspace?.label ?? "Workspace"}</strong>
            <small>{session?.session_id.slice(0, 8)}</small>
          </div>
          <div className="actions">
            <label className="model-select">
              <span>Model</span>
              <select
                aria-label="Model for current session"
                disabled={!session || busy}
                value={session?.model ?? model}
                onChange={(event) => void changeModel(event.target.value)}
              >
                {models.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <button onClick={() => setShowMobileActivity(true)}>
              Activity
            </button>
            <SessionMenu
              workspaceId={workspaceId}
              session={session}
              busy={busy}
              onNotice={setNotice}
              onReload={() =>
                session && loadSession(workspaceId, session.session_id)
              }
            />
          </div>
        </div>
        <div className="transcript" aria-live="polite">
          {!session?.messages.length && (
            <div className="empty">
              <h2>What shall we build?</h2>
              <p>
                Describe a task. The harness will inspect, act with approval,
                and verify.
              </p>
            </div>
          )}
          {session?.messages.map((message, index) => (
            <div
              className="request-block"
              key={`${message.request_number}-${message.role}-${index}`}
            >
              <article className={`message ${message.role}`}>
                <div className="role">
                  {message.role === "user" ? "You" : "Assistant"}
                </div>
                {message.role === "assistant" ? (
                  <SafeMarkdown text={message.content} />
                ) : (
                  <p>{message.content}</p>
                )}
              </article>
              {message.role === "user" && (
                <RequestActivity
                  requestNumber={message.request_number}
                  events={session.events}
                  busy={busy}
                />
              )}
            </div>
          ))}
        </div>
        <div className="composer-wrap">
          <label className="workflow-select">
            <span>Workflow</span>
            <select
              aria-label="Workflow for next request"
              disabled={busy || !session}
              value={selectedWorkflow}
              onChange={(event) => setSelectedWorkflow(event.target.value)}
            >
              <option value="">Auto-select</option>
              {workflows
                .filter((item) => item.workflow_id !== "general_assistance")
                .map((item) => (
                  <option value={item.workflow_id} key={item.workflow_id}>
                    {item.title}
                  </option>
                ))}
            </select>
          </label>
          <textarea
            aria-label="Prompt"
            value={prompt}
            disabled={busy || !session}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={composerKeyDown}
            placeholder={
              busy ? "The harness is working…" : "Ask about this workspace…"
            }
          />
          <button
            className="send"
            disabled={busy || !prompt.trim()}
            onClick={() => void sendPrompt()}
          >
            Send
          </button>
          <div className="composer-foot">
            <span>Ctrl+Enter to send</span>
            <span>{notice}</span>
          </div>
        </div>
      </main>
      <aside
        className={`right-panel ${showMobileActivity ? "mobile-open" : ""}`}
      >
        <div className="panel-heading">
          <span>Activity</span>
          <button onClick={() => setShowMobileActivity(false)}>×</button>
        </div>
        <input
          className="filter"
          aria-label="Filter events"
          placeholder="Filter model, tool, errors, tags…"
          value={eventFilter}
          onChange={(event) => setEventFilter(event.target.value)}
        />
        <section
          className="plan-card evaluation-card"
          aria-label="Harness evaluation"
        >
          <strong>Harness evaluation</strong>
          {evaluationStatus?.enabled ? (
            <>
              <p>
                {evaluationStatus.pass_rate}% pass ·{" "}
                {evaluationStatus.verification_rate}% verified
              </p>
              <small>
                {evaluationStatus.observations} observations ·{" "}
                {evaluationStatus.llm_calls} calls · {evaluationStatus.tokens}{" "}
                tokens
              </small>
              <div className="memory-actions">
                <button
                  disabled={busy}
                  onClick={() => void runSlashCommand("/eval run core")}
                >
                  Run offline suite
                </button>
                <button
                  disabled={busy || !session}
                  onClick={async () => {
                    if (!session) return;
                    setBusy(true);
                    try {
                      await api.proposeCandidate(
                        workspaceId,
                        session.session_id,
                      );
                      setCandidates(await api.candidates(workspaceId));
                      setNotice(
                        "Candidate proposal recorded; no source was changed.",
                      );
                    } catch (error) {
                      setNotice(
                        error instanceof Error
                          ? error.message
                          : "Proposal failed",
                      );
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Propose improvement
                </button>
              </div>
              {evaluationHistory.slice(0, 3).map((item) => (
                <small key={item.observation_id}>
                  Request {item.request_number}: {item.score.outcome} ·{" "}
                  {item.score.input_tokens + item.score.output_tokens} tokens
                </small>
              ))}
              {candidates.slice(0, 2).map((candidate) => (
                <div className="candidate-card" key={candidate.candidate_id}>
                  <small>
                    {candidate.component_ids.join(", ")} · {candidate.status}
                  </small>
                  <p>{candidate.proposal}</p>
                  {candidate.status === "proposed" && (
                    <div className="memory-actions">
                      <button
                        onClick={async () => {
                          await api.decideCandidate(
                            workspaceId,
                            candidate.candidate_id,
                            true,
                          );
                          setCandidates(await api.candidates(workspaceId));
                        }}
                      >
                        Approve proposal
                      </button>
                      <button
                        onClick={async () => {
                          await api.decideCandidate(
                            workspaceId,
                            candidate.candidate_id,
                            false,
                          );
                          setCandidates(await api.candidates(workspaceId));
                        }}
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </>
          ) : (
            <p className="muted">Disabled</p>
          )}
        </section>
        <section className="plan-card memory-card" aria-label="Project memory">
          <strong>Project memory</strong>
          {projectMemory ? (
            <>
              <p>
                {projectMemory.retrieval_mode} · generation{" "}
                {projectMemory.generation}
              </p>
              <small>
                {projectMemory.files} files · {projectMemory.symbols} symbols ·{" "}
                {projectMemory.dependencies} dependencies
              </small>
              <small>
                {projectMemory.embedding_model} ·{" "}
                {projectMemory.embedding_available
                  ? "embeddings ready"
                  : "lexical fallback"}
              </small>
              {projectMemory.warning && (
                <p className="warning-text">⚠ {projectMemory.warning}</p>
              )}
              <div className="memory-actions">
                <button
                  disabled={busy}
                  onClick={() => void runSlashCommand("/index refresh")}
                >
                  Refresh
                </button>
                <button
                  disabled={busy}
                  onClick={() => void runSlashCommand("/index rebuild")}
                >
                  Rebuild
                </button>
              </div>
            </>
          ) : (
            <p className="muted">Disabled</p>
          )}
        </section>
        {session?.plans?.length ? (
          <section className="plan-card" aria-label="Current task plan">
            <strong>{session.plans.at(-1)?.goal}</strong>
            <ol>
              {session.plans.at(-1)?.steps.map((step) => (
                <li key={step.step_id} data-status={step.status}>
                  <span>{step.status.replace("_", " ")}</span>{" "}
                  {step.description}
                </li>
              ))}
            </ol>
          </section>
        ) : null}
        {session?.workflows?.length ? (
          <section
            className="plan-card workflow-card"
            aria-label="Current workflow"
          >
            <strong>
              Workflow:{" "}
              {session.workflows.at(-1)?.workflow_id.replaceAll("_", " ")}
            </strong>
            <p>
              {session.workflows.at(-1)?.status} · confidence{" "}
              {Math.round((session.workflows.at(-1)?.confidence ?? 0) * 100)}%
            </p>
            <ol>
              {session.workflows.at(-1)?.stages.map((stage) => (
                <li key={stage.stage_id} data-status={stage.status}>
                  <span>{stage.status.replace("_", " ")}</span>{" "}
                  {stage.description}
                </li>
              ))}
            </ol>
          </section>
        ) : null}
        <div className="event-list">
          {filteredEvents
            .slice()
            .reverse()
            .map((event) => (
              <EventRow event={event} key={event.sequence} />
            ))}
          {!filteredEvents.length && <p className="muted">No activity yet.</p>}
        </div>
      </aside>
      {approval && (
        <ApprovalDialog
          approval={approval}
          workspaceId={workspaceId}
          onClose={() => setApproval(null)}
          onNotice={setNotice}
        />
      )}
      {showWorkspace && (
        <WorkspaceDialog
          onClose={() => setShowWorkspace(false)}
          onAdded={async () => {
            await refreshWorkspaces();
            setShowWorkspace(false);
          }}
        />
      )}
      {showHelp && (
        <Modal title="Harness help" onClose={() => setShowHelp(false)}>
          <p>
            <kbd>Ctrl</kbd>+<kbd>Enter</kbd> sends a prompt. Every command,
            patch, and maintenance action requires explicit approval.
          </p>
          <p>
            Commands: /new, /sessions, /events, /max-turns, /quota, /tag, /tags,
            /export, /archive, /archives, /restore, /session-info, /summarize,
            /session-check, /plugins, /tools, /workflows, /workflow, /plan,
            /index, /memory, /eval, /handoff, /candidate, /help, /exit.
          </p>
        </Modal>
      )}
    </div>
  );
}

export function SafeMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      skipHtml
      components={{
        a: ({ href, children }) => {
          const safe = safeExternalHref(href);
          return safe ? (
            <a href={safe} target="_blank" rel="noopener noreferrer">
              {children}
              <span className="external-label"> (opens source)</span>
            </a>
          ) : (
            <span className="unsafe-link">{children}</span>
          );
        },
        table: ({ children }) => (
          <div
            className="table-scroll"
            role="region"
            aria-label="Scrollable table"
          >
            <table>{children}</table>
          </div>
        ),
        pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
      }}
    >
      {normalizeMarkdownForDisplay(text)}
    </ReactMarkdown>
  );
}

export function CodeBlock({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const text = nodeText(children).replace(/\n$/, "");
  const language = codeLanguage(children);
  async function copy() {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="code-shell">
      <div className="code-toolbar">
        <span>{language}</span>
        <button onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export function RequestActivity({
  requestNumber,
  events,
  busy,
}: {
  requestNumber: number | null;
  events: ProgressEvent[];
  busy: boolean;
}) {
  const requestEvents = events.filter(
    (event) => event.request_number === requestNumber,
  );
  const values = buildRequestTimeline(events, requestNumber);
  const errors = values.filter((event) => event.status === "error").length;
  const warnings = values.filter((event) => event.status === "warning").length;
  const completed = requestEvents.some(
    (event) => event.kind === "model_complete" && event.target === "final",
  );
  const duration =
    values.reduce((total, event) => total + event.duration_ms, 0) / 1000;
  const title = completed
    ? `${errors || warnings ? "Completed with issues" : "Completed"} · ${values.length} steps · ${duration.toFixed(1)}s`
    : busy
      ? `Working…${values.at(-1)?.label ? ` · ${values.at(-1)?.label}` : ""}`
      : "Activity unavailable";
  return (
    <details
      className={`working ${errors || warnings ? "warning" : completed ? "complete" : ""}`}
    >
      <summary>{title}</summary>
      {values.map((event) => (
        <TimelineRow step={event} key={event.key} />
      ))}
    </details>
  );
}

export function TimelineRow({ step }: { step: TimelineStep }) {
  return (
    <div className={`event ${step.status}`}>
      <span className="event-icon" aria-hidden="true">
        {statusIcon(step.status)}
      </span>
      <div>
        <strong>{step.label}</strong>
        <small>
          {step.target} · {(step.duration_ms / 1000).toFixed(1)}s
        </small>
      </div>
    </div>
  );
}

export function EventRow({ event }: { event: ProgressEvent }) {
  return (
    <div className={`event ${event.status}`}>
      <span className="event-icon">{statusIcon(event.status)}</span>
      <div>
        <strong>{event.summary}</strong>
        <small>
          {event.target} · {(event.duration_ms / 1000).toFixed(1)}s{" "}
          {event.tags.map((tag) => `#${tag}`).join(" ")}
        </small>
      </div>
    </div>
  );
}

export function SessionMenu({
  workspaceId,
  session,
  busy,
  onNotice,
  onReload,
}: {
  workspaceId: string;
  session: SessionDetail | null;
  busy: boolean;
  onNotice: (value: string) => void;
  onReload: () => void;
}) {
  async function act(name: string) {
    if (!session || busy) return;
    try {
      if (name === "summary")
        await api.summarize(workspaceId, session.session_id);
      if (name === "md" || name === "csv") {
        const result = await api.export(workspaceId, session.session_id, name);
        onNotice(`Exported to ${result.path}`);
      }
      if (name === "turns") {
        const raw = window.prompt("Maximum LLM calls (1-100), blank to reset:");
        if (raw !== null)
          await api.updateTurns(
            workspaceId,
            session.session_id,
            raw ? Number(raw) : null,
          );
      }
      if (name === "quota") {
        const raw = window.prompt("Advisory token budget, blank to reset:");
        if (raw !== null)
          await api.updateQuota(
            workspaceId,
            session.session_id,
            raw ? Number(raw) : null,
          );
      }
      onReload();
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "Action failed");
    }
  }
  return (
    <select
      aria-label="Session controls"
      disabled={!session || busy}
      defaultValue=""
      onChange={(event) => {
        void act(event.target.value);
        event.target.value = "";
      }}
    >
      <option value="" disabled>
        Session controls
      </option>
      <option value="summary">Generate summary</option>
      <option value="md">Export Markdown</option>
      <option value="csv">Export CSV</option>
      <option value="turns">Configure call limit</option>
      <option value="quota">Configure quota</option>
    </select>
  );
}

export function ApprovalDialog({
  approval,
  workspaceId,
  onClose,
  onNotice,
}: {
  approval: Approval;
  workspaceId: string;
  onClose: () => void;
  onNotice: (v: string) => void;
}) {
  const [feedback, setFeedback] = useState("");
  const reject = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    reject.current?.focus();
  }, []);
  async function resolve(approved: boolean) {
    try {
      await api.approval(approval.approval_id, workspaceId, approved, feedback);
      onClose();
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "Approval failed");
    }
  }
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onKeyDown={(event) => {
        if (event.key === "Escape") void resolve(false);
      }}
    >
      <section
        className="modal approval"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-title"
      >
        <h2 id="approval-title">Approve {approval.kind}</h2>
        <p className="warning-text">
          {approval.warning ?? "Review the exact operation before continuing."}
        </p>
        <p>{approval.explanation ?? approval.action}</p>
        <small>{approval.workspace}</small>
        <pre>{approval.command ?? approval.preview ?? approval.details}</pre>
        <label>
          Optional rejection feedback
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
        </label>
        <div className="modal-actions">
          <button ref={reject} onClick={() => void resolve(false)}>
            Reject
          </button>
          <button className="approve" onClick={() => void resolve(true)}>
            Approve
          </button>
        </div>
      </section>
    </div>
  );
}

export function WorkspaceDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const [label, setLabel] = useState("");
  const [path, setPath] = useState("");
  const [challenge, setChallenge] = useState<{
    challenge_id: string;
    resolved_path: string;
    warning: string;
  } | null>(null);
  const [error, setError] = useState("");
  async function validate(event: FormEvent) {
    event.preventDefault();
    try {
      setChallenge(await api.validateWorkspace(label, path));
    } catch (value) {
      setError(value instanceof Error ? value.message : "Invalid workspace");
    }
  }
  async function confirm() {
    if (!challenge) return;
    try {
      await api.confirmWorkspace(challenge.challenge_id, true);
      onAdded();
    } catch (value) {
      setError(value instanceof Error ? value.message : "Registration failed");
    }
  }
  return (
    <Modal title="Add an allowlisted workspace" onClose={onClose}>
      {!challenge ? (
        <form onSubmit={(event) => void validate(event)}>
          <label>
            Label
            <input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
          </label>
          <label>
            Absolute Windows path
            <input
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="E:\projects\my-app"
            />
          </label>
          <p className="error-text">{error}</p>
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit">Validate</button>
          </div>
        </form>
      ) : (
        <>
          <p className="warning-text">{challenge.warning}</p>
          <pre>{challenge.resolved_path}</pre>
          <p className="error-text">{error}</p>
          <div className="modal-actions">
            <button autoFocus onClick={onClose}>
              Reject
            </button>
            <button className="approve" onClick={() => void confirm()}>
              Add workspace
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="modal" role="dialog" aria-modal="true">
        <button className="modal-close" aria-label="Close" onClick={onClose}>
          ×
        </button>
        <h2>{title}</h2>
        {children}
      </section>
    </div>
  );
}

export default App;
