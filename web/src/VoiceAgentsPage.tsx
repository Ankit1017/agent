import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, bootstrap } from "./api";
import type { VoiceAgentCatalog, VoiceAgentProfile } from "./types";
import { AppDialog, AppHeader, Badge, StatusRegion } from "./ui";

type EditableProfile = Omit<
  VoiceAgentProfile,
  | "profile_id"
  | "revision"
  | "created_at"
  | "updated_at"
  | "available"
  | "unavailable_reasons"
>;

const emptyProfile: EditableProfile = {
  name: "Workspace Reader",
  instructions: "",
  workspace_id: "",
  model: "",
  allowed_tools: [],
  project_context_enabled: true,
  workflow_mode: "off",
  max_turns: 8,
  token_budget: 0,
  context_max_chars: 30000,
  max_answer_chars: 1500,
  tool_schema_limit: 8,
  tool_activation_limit: 5,
  voice_id: "",
  speaking_rate: 1,
  auto_speak: true,
};

export default function VoiceAgentsPage() {
  const [catalog, setCatalog] = useState<VoiceAgentCatalog | null>(null);
  const [profiles, setProfiles] = useState<VoiceAgentProfile[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<EditableProfile>(emptyProfile);
  const [notice, setNotice] = useState("Loading voice-agent configuration...");
  const [saving, setSaving] = useState(false);
  const [profileSearch, setProfileSearch] = useState("");
  const [toolSearch, setToolSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<VoiceAgentProfile | null>(
    null,
  );
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [baseline, setBaseline] = useState(JSON.stringify(emptyProfile));
  const [pendingProfile, setPendingProfile] =
    useState<VoiceAgentProfile | null>(null);
  const [pendingNew, setPendingNew] = useState(false);
  const workspace = catalog?.workspaces.find(
    (item) => item.workspace_id === draft.workspace_id,
  );
  const tools = useMemo(() => workspace?.tools ?? [], [workspace]);
  const visibleProfiles = useMemo(() => {
    const query = profileSearch.trim().toLowerCase();
    return query
      ? profiles.filter((profile) =>
          [profile.name, profile.model].join(" ").toLowerCase().includes(query),
        )
      : profiles;
  }, [profileSearch, profiles]);
  const visibleTools = useMemo(() => {
    const query = toolSearch.trim().toLowerCase();
    return query
      ? tools.filter((tool) =>
          [tool.name, tool.description, tool.profile, tool.risk]
            .join(" ")
            .toLowerCase()
            .includes(query),
        )
      : tools;
  }, [toolSearch, tools]);
  const dirty = JSON.stringify(draft) !== baseline;

  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);

  async function refresh(preferred?: string) {
    const values = await api.voiceAgentProfiles();
    setProfiles(values);
    if (preferred) setSelectedId(preferred);
  }

  useEffect(() => {
    void bootstrap()
      .then(async () => {
        const [nextCatalog, nextProfiles] = await Promise.all([
          api.voiceAgentCatalog(),
          api.voiceAgentProfiles(),
        ]);
        setCatalog(nextCatalog);
        setProfiles(nextProfiles);
        const initialDraft = {
          ...emptyProfile,
          workspace_id: nextCatalog.workspaces[0]?.workspace_id ?? "",
          model: nextCatalog.models[0] ?? "",
          voice_id:
            nextCatalog.voices.find((voice) => voice.default)?.voice_id ??
            nextCatalog.voices[0]?.voice_id ??
            "",
          context_max_chars: Array.isArray(nextCatalog.bounds.context_max_chars)
            ? nextCatalog.bounds.context_max_chars[1]
            : 30000,
        };
        setDraft(initialDraft);
        setBaseline(JSON.stringify(initialDraft));
        setNotice("Profiles are snapshotted when a conversation starts.");
      })
      .catch((error: Error) => setNotice(error.message));
  }, []);

  function loadProfile(profile: VoiceAgentProfile) {
    const value = { ...profile } as Partial<VoiceAgentProfile>;
    delete value.profile_id;
    delete value.revision;
    delete value.created_at;
    delete value.updated_at;
    delete value.available;
    delete value.unavailable_reasons;
    setSelectedId(profile.profile_id);
    const next = value as EditableProfile;
    setDraft(next);
    setBaseline(JSON.stringify(next));
    setNotice(
      `Editing revision ${profile.revision}. Existing conversations stay unchanged.`,
    );
  }

  function edit(profile: VoiceAgentProfile) {
    if (dirty) {
      setPendingProfile(profile);
      return;
    }
    loadProfile(profile);
  }

  function startNew() {
    if (!catalog) return;
    const next = {
      ...emptyProfile,
      workspace_id: catalog.workspaces[0]?.workspace_id ?? "",
      model: catalog.models[0] ?? "",
      voice_id: catalog.voices[0]?.voice_id ?? "",
    };
    setSelectedId("");
    setDraft(next);
    setBaseline(JSON.stringify(next));
    setNotice("Create a profile from the safe defaults or a template.");
  }

  function applyTemplate(id: string) {
    const template = catalog?.templates.find((item) => item.template_id === id);
    if (!template || template.immutable) return;
    setSelectedId("");
    setDraft((current) => ({
      ...current,
      name: template.name,
      allowed_tools: template.allowed_tools.filter((name) =>
        tools.some((tool) => tool.name === name),
      ),
    }));
    setNotice(`${template.name} template applied. Review it before saving.`);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const saved = selectedId
        ? await api.updateVoiceAgentProfile(selectedId, draft)
        : await api.createVoiceAgentProfile(draft);
      await refresh(saved.profile_id);
      loadProfile(saved);
      setNotice(`Saved ${saved.name} revision ${saved.revision}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Profile save failed");
    } finally {
      setSaving(false);
    }
  }

  async function clone() {
    if (!selectedId) return;
    try {
      const saved = await api.cloneVoiceAgentProfile(selectedId);
      await refresh(saved.profile_id);
      loadProfile(saved);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Clone failed");
    }
  }

  function remove() {
    const profile = profiles.find((item) => item.profile_id === selectedId);
    if (!profile) return;
    setDeleteConfirmation("");
    setDeleteTarget(profile);
  }

  async function confirmRemove() {
    if (!deleteTarget || deleteConfirmation !== deleteTarget.name) return;
    try {
      await api.deleteVoiceAgentProfile(deleteTarget.profile_id);
      setSelectedId("");
      setDeleteTarget(null);
      setDeleteConfirmation("");
      await refresh();
      setNotice(
        "Profile deleted. Existing conversation snapshots remain available.",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Delete failed");
    }
  }

  function toggleTool(name: string) {
    setDraft((current) => ({
      ...current,
      allowed_tools: current.allowed_tools.includes(name)
        ? current.allowed_tools.filter((item) => item !== name)
        : [...current.allowed_tools, name],
    }));
  }

  if (!catalog) return <main className="agent-config-loading">{notice}</main>;
  return (
    <div className="speech-page agent-config-page">
      <AppHeader
        current="agents"
        title="Voice Agent Profiles"
        status={
          <StatusRegion tone={saving ? "info" : "success"}>
            {notice}
          </StatusRegion>
        }
      />
      <main className="agent-config-layout" id="main-content">
        <aside className="agent-profile-list">
          <button
            className="send"
            onClick={() => {
              if (dirty) setPendingNew(true);
              else startNew();
            }}
          >
            New profile
          </button>
          <label className="profile-search">
            <span>Find profiles</span>
            <input
              type="search"
              value={profileSearch}
              onChange={(event) => setProfileSearch(event.target.value)}
              placeholder="Search name or model"
            />
          </label>
          <article className="profile-card protected">
            <strong>Protected Voice Chat</strong>
            <small>Immutable · one call · no tools · no workspace</small>
          </article>
          {visibleProfiles.map((profile) => (
            <button
              key={profile.profile_id}
              className={`profile-card ${selectedId === profile.profile_id ? "selected" : ""}`}
              onClick={() => edit(profile)}
            >
              <strong>{profile.name}</strong>
              <Badge tone={profile.available ? "success" : "danger"}>
                {profile.available ? "Available" : "Unavailable"}
              </Badge>
              <small>
                Revision {profile.revision} ·{" "}
                {profile.available ? "Available" : "Unavailable"}
              </small>
            </button>
          ))}
        </aside>
        <form
          className="agent-profile-editor"
          onSubmit={(event) => void save(event)}
        >
          <div className="editor-heading">
            <div>
              <h1>{selectedId ? "Edit profile" : "Create profile"}</h1>
              {dirty && <Badge tone="warning">Unsaved changes</Badge>}
              <p aria-live="polite">{notice}</p>
            </div>
            <div className="conversation-actions">
              <button
                type="button"
                disabled={!selectedId}
                onClick={() => void clone()}
              >
                Clone
              </button>
              <button
                type="button"
                disabled={!selectedId}
                onClick={() => void remove()}
              >
                Delete
              </button>
              <button className="send" disabled={saving} type="submit">
                Save profile
              </button>
            </div>
          </div>
          <fieldset>
            <legend>Identity</legend>
            <label>
              Name
              <input
                required
                maxLength={80}
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label>
              Additional instructions
              <textarea
                maxLength={4000}
                value={draft.instructions}
                onChange={(e) =>
                  setDraft({ ...draft, instructions: e.target.value })
                }
              />
            </label>
            <small>
              These instructions are sanitized and appended beneath immutable
              harness safety rules.
            </small>
          </fieldset>
          <fieldset>
            <legend>Workspace and Model</legend>
            <div className="editor-grid">
              <label>
                Workspace
                <select
                  required
                  value={draft.workspace_id}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      workspace_id: e.target.value,
                      allowed_tools: [],
                    })
                  }
                >
                  {catalog.workspaces.map((item) => (
                    <option key={item.workspace_id} value={item.workspace_id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Model
                <select
                  required
                  value={draft.model}
                  onChange={(e) =>
                    setDraft({ ...draft, model: e.target.value })
                  }
                >
                  {catalog.models.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </label>
            </div>
          </fieldset>
          <fieldset>
            <legend>Context</legend>
            <div className="editor-grid">
              <label className="check">
                <input
                  type="checkbox"
                  checked={draft.project_context_enabled}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      project_context_enabled: e.target.checked,
                    })
                  }
                />
                Automatic project context
              </label>
              <label>
                Workflow
                <select
                  value={draft.workflow_mode}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      workflow_mode: e.target.value as "off" | "auto",
                    })
                  }
                >
                  <option value="off">Off</option>
                  <option value="auto">Auto</option>
                </select>
              </label>
              <NumberField
                label="Context characters"
                value={draft.context_max_chars}
                min={4000}
                max={Number((catalog.bounds.context_max_chars as number[])[1])}
                onChange={(value) =>
                  setDraft({ ...draft, context_max_chars: value })
                }
              />
            </div>
          </fieldset>
          <fieldset>
            <legend>Tools and Risk</legend>
            <div className="template-row">
              {catalog.templates
                .filter((item) => !item.immutable)
                .map((item) => (
                  <button
                    type="button"
                    key={item.template_id}
                    onClick={() => applyTemplate(item.template_id)}
                  >
                    {item.name}
                  </button>
                ))}
            </div>
            <label className="tool-search">
              Search available tools
              <input
                type="search"
                value={toolSearch}
                onChange={(event) => setToolSearch(event.target.value)}
                placeholder="Name, capability, or risk"
              />
            </label>
            <p className="tool-selection-summary">
              {draft.allowed_tools.length} selected · {visibleTools.length}{" "}
              shown
            </p>
            <div className="tool-grid">
              {visibleTools.map((tool) => (
                <label className="tool-card" key={tool.name}>
                  <input
                    type="checkbox"
                    checked={draft.allowed_tools.includes(tool.name)}
                    onChange={() => toggleTool(tool.name)}
                  />
                  <span>
                    <strong>{tool.name}</strong>
                    <small>{tool.description}</small>
                    <em>
                      {tool.profile} · {tool.risk}
                      {tool.approval_required ? " · click approval" : ""}
                    </em>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend>Execution Limits</legend>
            <div className="editor-grid">
              <NumberField
                label="Maximum model calls"
                value={draft.max_turns}
                min={1}
                max={100}
                onChange={(value) => setDraft({ ...draft, max_turns: value })}
              />
              <NumberField
                label="Token budget (0 disables)"
                value={draft.token_budget}
                min={0}
                max={1000000}
                onChange={(value) =>
                  setDraft({ ...draft, token_budget: value })
                }
              />
              <NumberField
                label="Final answer characters"
                value={draft.max_answer_chars}
                min={500}
                max={5000}
                onChange={(value) =>
                  setDraft({ ...draft, max_answer_chars: value })
                }
              />
              <NumberField
                label="Tool schema limit"
                value={draft.tool_schema_limit}
                min={1}
                max={32}
                onChange={(value) =>
                  setDraft({
                    ...draft,
                    tool_schema_limit: value,
                    tool_activation_limit: Math.min(
                      draft.tool_activation_limit,
                      value,
                    ),
                  })
                }
              />
              <NumberField
                label="Deferred activation limit"
                value={draft.tool_activation_limit}
                min={1}
                max={draft.tool_schema_limit}
                onChange={(value) =>
                  setDraft({ ...draft, tool_activation_limit: value })
                }
              />
            </div>
          </fieldset>
          <fieldset>
            <legend>Voice Output</legend>
            <div className="editor-grid">
              <label>
                Voice
                <select
                  value={draft.voice_id}
                  onChange={(e) =>
                    setDraft({ ...draft, voice_id: e.target.value })
                  }
                >
                  {catalog.voices.map((voice) => (
                    <option key={voice.voice_id} value={voice.voice_id}>
                      {voice.display_name} · {voice.language}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Speaking rate: {draft.speaking_rate.toFixed(2)}x
                <input
                  type="range"
                  min="0.75"
                  max="1.5"
                  step="0.05"
                  value={draft.speaking_rate}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      speaking_rate: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={draft.auto_speak}
                  onChange={(e) =>
                    setDraft({ ...draft, auto_speak: e.target.checked })
                  }
                />
                Speak final answers automatically
              </label>
            </div>
          </fieldset>
        </form>
      </main>
      {deleteTarget && (
        <AppDialog
          title="Delete voice-agent profile"
          onClose={() => setDeleteTarget(null)}
          actions={
            <>
              <button type="button" onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button
                className="danger-button"
                type="button"
                disabled={deleteConfirmation !== deleteTarget.name}
                onClick={() => void confirmRemove()}
              >
                Delete permanently
              </button>
            </>
          }
        >
          <p>
            Existing conversation snapshots remain available. Type the exact
            profile name to confirm permanent deletion.
          </p>
          <label>
            Profile name
            <input
              autoComplete="off"
              value={deleteConfirmation}
              onChange={(event) => setDeleteConfirmation(event.target.value)}
            />
          </label>
        </AppDialog>
      )}
      {pendingProfile && (
        <AppDialog
          title="Discard unsaved changes?"
          onClose={() => setPendingProfile(null)}
          actions={
            <>
              <button type="button" onClick={() => setPendingProfile(null)}>
                Keep editing
              </button>
              <button
                className="danger-button"
                type="button"
                onClick={() => {
                  const target = pendingProfile;
                  setPendingProfile(null);
                  loadProfile(target);
                }}
              >
                Discard and switch
              </button>
            </>
          }
        >
          Your current profile changes have not been saved.
        </AppDialog>
      )}
      {pendingNew && (
        <AppDialog
          title="Discard unsaved changes?"
          onClose={() => setPendingNew(false)}
          actions={
            <>
              <button type="button" onClick={() => setPendingNew(false)}>
                Keep editing
              </button>
              <button
                className="danger-button"
                type="button"
                onClick={() => {
                  setPendingNew(false);
                  startNew();
                }}
              >
                Discard and create new
              </button>
            </>
          }
        >
          Your current profile changes have not been saved.
        </AppDialog>
      )}
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
