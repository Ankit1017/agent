import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalDialog, SafeMarkdown } from "./App";
import { api, bootstrap, clientId } from "./api";
import { SpeakingAvatar, type AvatarMode } from "./Audio2FaceAvatar";
import { LocalSpeechInputClient } from "./speech-input-client";
import { PcmPlayer, type PcmMetadata, wavBlob } from "./speech-audio";
import { AppHeader, EmptyState, StatusRegion } from "./ui";
import type {
  SpeechVoice,
  VoiceConversationDetail,
  VoiceConversationSummary,
  SpeechInputEvent,
  VoiceAgentProfile,
  Approval,
  StreamEvent,
  Audio2FaceStatus,
  FaceAnimation,
  FaceRigAnimation,
} from "./types";

type VoiceState =
  | "ready"
  | "waiting"
  | "listening"
  | "transcribing"
  | "generating"
  | "tool-running"
  | "awaiting-approval"
  | "cancellation-requested"
  | "speaking"
  | "stopped"
  | "error";

export default function VoiceConversationPanel({
  onDirect,
}: {
  onDirect: () => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [profiles, setProfiles] = useState<VoiceAgentProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("protected");
  const [conversations, setConversations] = useState<
    VoiceConversationSummary[]
  >([]);
  const [conversation, setConversation] =
    useState<VoiceConversationDetail | null>(null);
  const [text, setText] = useState("");
  const [voices, setVoices] = useState<SpeechVoice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [rate, setRate] = useState(1);
  const [state, setState] = useState<VoiceState>("ready");
  const [notice, setNotice] = useState(
    "Preparing protected voice conversation...",
  );
  const [redacted, setRedacted] = useState(false);
  const [mouthLevel, setMouthLevel] = useState(0);
  const [audio2face, setAudio2face] = useState<Audio2FaceStatus | null>(null);
  const [avatarMode, setAvatarMode] = useState<AvatarMode>("2d");
  const [avatarId, setAvatarId] = useState("default");
  const [rigAnimation, setRigAnimation] = useState<FaceRigAnimation | null>(
    null,
  );
  const [animationStartedAt, setAnimationStartedAt] = useState(0);
  const [hasAudio, setHasAudio] = useState(false);
  const [speechInputAvailable, setSpeechInputAvailable] = useState(false);
  const [speechInputSetup, setSpeechInputSetup] = useState("");
  const [microphoneEnabled, setMicrophoneEnabled] = useState(false);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [activity, setActivity] = useState<string[]>([]);
  const player = useRef<PcmPlayer | null>(null);
  const controller = useRef<AbortController | null>(null);
  const animation = useRef(0);
  const audio = useRef<Uint8Array[]>([]);
  const metadata = useRef<PcmMetadata | null>(null);
  const faceAnimation = useRef<FaceAnimation | null>(null);
  const run = useRef(0);
  const speechInput = useRef<LocalSpeechInputClient | null>(null);
  const microphoneEnabledRef = useRef(false);
  const submittedUtterances = useRef(new Set<string>());
  const submitVoice = useRef<(value: string) => void>(() => undefined);
  const activeTask = useRef("");
  const lastEvent = useRef(0);
  const reducedMotion = useMemo(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  async function refreshConversations(preferredId?: string) {
    const saved = await api.voiceConversations();
    setConversations(saved);
    const target = preferredId ?? conversation?.conversation_id;
    if (target && saved.some((item) => item.conversation_id === target)) {
      await selectConversation(target);
    }
  }

  async function selectConversation(id: string) {
    stopPlayback();
    const detail = await api.voiceConversation(id);
    setConversation(detail);
    setSelectedModel(detail.model);
    setSelectedProfile(detail.profile_id ?? "protected");
    if (detail.voice_id) setVoiceId(detail.voice_id);
    if (detail.speaking_rate) setRate(detail.speaking_rate);
    setState("ready");
    setNotice(
      detail.mode === "agent"
        ? "Ready with the saved agent snapshot"
        : "Ready for one tool-free model call",
    );
    setRedacted(false);
  }

  async function createConversation() {
    try {
      stopPlayback();
      const created = await api.createVoiceConversation(
        selectedModel,
        selectedProfile,
      );
      setConversation(created);
      setSelectedModel(created.model);
      await refreshConversations(created.conversation_id);
      setNotice("New conversation ready");
    } catch (error) {
      fail(error, "Could not create conversation");
    }
  }

  async function renameConversation() {
    if (!conversation) return;
    const title = window
      .prompt("Conversation name:", conversation.title)
      ?.trim();
    if (!title) return;
    try {
      const updated = await api.updateVoiceConversation(
        conversation.conversation_id,
        {
          title,
        },
      );
      setConversation(updated);
      await refreshConversations(updated.conversation_id);
    } catch (error) {
      fail(error, "Could not rename conversation");
    }
  }

  async function deleteConversation() {
    if (!conversation) return;
    if (!window.confirm(`Permanently delete "${conversation.title}"?`)) return;
    try {
      stopPlayback();
      await api.deleteVoiceConversation(conversation.conversation_id);
      setConversation(null);
      const saved = await api.voiceConversations();
      setConversations(saved);
      if (saved[0]) await selectConversation(saved[0].conversation_id);
      else setNotice("Conversation deleted. Start a new one when ready.");
    } catch (error) {
      fail(error, "Could not delete conversation");
    }
  }

  async function changeModel(model: string) {
    setSelectedModel(model);
    if (!conversation) return;
    try {
      const updated = await api.updateVoiceConversation(
        conversation.conversation_id,
        {
          model,
        },
      );
      setConversation(updated);
      await refreshConversations(updated.conversation_id);
    } catch (error) {
      fail(error, "Could not change model");
    }
  }

  async function upgradeProfile() {
    if (!conversation?.profile_id) return;
    const current = profiles.find(
      (profile) => profile.profile_id === conversation.profile_id,
    );
    if (!current) return;
    try {
      const updated = await api.upgradeVoiceConversationProfile(
        conversation.conversation_id,
        current.profile_id,
        current.revision,
      );
      setConversation(updated);
      setNotice(
        `Conversation upgraded to ${current.name} revision ${current.revision}`,
      );
    } catch (error) {
      fail(error, "Profile upgrade failed");
    }
  }

  async function loadOlder() {
    if (!conversation?.has_older_messages) return;
    try {
      const older = await api.voiceConversation(
        conversation.conversation_id,
        conversation.messages.length,
      );
      setConversation({
        ...conversation,
        messages: [...older.messages, ...conversation.messages],
        has_older_messages: older.has_older_messages,
      });
    } catch (error) {
      fail(error, "Could not load older messages");
    }
  }

  async function submitMessage(value: string) {
    if (!enabled || !value || value.length > 5000 || state === "generating")
      return;
    speechInput.current?.pause();
    stopPlayback();
    setState("generating");
    setNotice("Generating one model reply without tools...");
    setRedacted(false);
    try {
      let active = conversation;
      if (!active) {
        active = await api.createVoiceConversation(
          selectedModel,
          selectedProfile,
        );
        setConversation(active);
      }
      if (active.mode === "agent") {
        const submitted = await api.completeVoiceAgentTurn(
          active.conversation_id,
          value,
        );
        activeTask.current = submitted.task.task_id;
        setText("");
        setNotice("Agent task queued");
        return;
      }
      const turn = await api.completeVoiceTurn(active.conversation_id, value);
      const messages = [
        ...(active.messages ?? []),
        turn.user_message,
        turn.assistant_message,
      ];
      setConversation({
        ...active,
        ...turn.conversation,
        messages,
        has_older_messages: active.has_older_messages,
      });
      setText("");
      setRedacted(turn.redacted);
      await refreshConversationListOnly();
      if (voiceId) await speakText(turn.speech_text);
      else {
        setState("ready");
        setNotice("Reply saved; local speech is unavailable");
        rearmMicrophone();
      }
    } catch (error) {
      fail(error, "The model could not complete the reply");
      rearmMicrophone();
    }
  }

  function send() {
    void submitMessage(text.trim());
  }

  useEffect(() => {
    submitVoice.current = (value: string) => void submitMessage(value);
  });

  async function refreshConversationListOnly() {
    setConversations(await api.voiceConversations());
  }

  function animateLevel(active: PcmPlayer, face: FaceAnimation | null = null) {
    if (reducedMotion) return;
    const started = performance.now() + 80;
    const update = () => {
      if (face) {
        const elapsed = Math.max(0, (performance.now() - started) / 1000);
        const index = Math.min(
          face.frames.length - 1,
          Math.floor(elapsed * face.fps),
        );
        const frame = face.frames[index];
        setMouthLevel(frame?.mouth_open ?? 0);
      } else {
        setMouthLevel(active.level());
      }
      animation.current = requestAnimationFrame(update);
    };
    animation.current = requestAnimationFrame(update);
  }

  function stopPlayback(updateLevel = true) {
    run.current += 1;
    controller.current?.abort();
    controller.current = null;
    player.current?.stop();
    player.current = null;
    cancelAnimationFrame(animation.current);
    if (updateLevel) {
      setMouthLevel(0);
      setHasAudio(audio.current.length > 0);
    }
  }

  function stop() {
    speechInput.current?.cancel();
    stopPlayback();
    if (activeTask.current) {
      setState("cancellation-requested");
      setNotice(
        "Cancellation requested; waiting for the current operation boundary",
      );
      void api
        .cancelTask(activeTask.current)
        .catch((error: Error) => fail(error, "Task cancellation failed"));
    } else {
      setState("stopped");
      setNotice("Speech stopped");
    }
  }

  async function speakText(value: string) {
    if (!voiceId || !value) return;
    if (
      avatarMode === "3d" &&
      audio2face?.available &&
      audio2face.avatar_available
    ) {
      await speakAnimatedText(value);
      return;
    }
    stopPlayback();
    const activeRun = run.current;
    const abort = new AbortController();
    controller.current = abort;
    audio.current = [];
    setHasAudio(false);
    setState("speaking");
    setNotice("Speaking model reply");
    const context = new AudioContext();
    try {
      await context.resume();
      const response = await api.streamSpeech(
        value,
        voiceId,
        rate,
        abort.signal,
      );
      const sampleRate = Number(response.headers.get("X-Speech-Sample-Rate"));
      const channels = Number(response.headers.get("X-Speech-Channels"));
      const sampleWidth = Number(response.headers.get("X-Speech-Sample-Width"));
      if (
        !sampleRate ||
        channels !== 1 ||
        sampleWidth !== 2 ||
        !response.body
      ) {
        throw new Error("The server returned an unsupported audio stream");
      }
      metadata.current = { sampleRate, channels, sampleWidth };
      faceAnimation.current = null;
      setRigAnimation(null);
      const active = new PcmPlayer(context, sampleRate);
      player.current = active;
      await active.start();
      animateLevel(active);
      const reader = response.body.getReader();
      while (true) {
        const { done, value: chunk } = await reader.read();
        if (done) break;
        const copy = chunk.slice();
        audio.current.push(copy);
        active.enqueue(copy);
      }
      await active.finish();
      setHasAudio(audio.current.length > 0);
      await context.close().catch(() => undefined);
      if (player.current === active) player.current = null;
      if (!abort.signal.aborted && run.current === activeRun) {
        cancelAnimationFrame(animation.current);
        setMouthLevel(0);
        setState("ready");
        setNotice("Reply complete");
        rearmMicrophone();
      }
    } catch (error) {
      await context.close().catch(() => undefined);
      if (abort.signal.aborted) return;
      fail(error, "Speech failed; the text reply remains saved");
      rearmMicrophone();
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }

  async function speakAnimatedText(value: string) {
    stopPlayback();
    const activeRun = run.current;
    const abort = new AbortController();
    controller.current = abort;
    audio.current = [];
    setHasAudio(false);
    setState("speaking");
    setNotice("Generating local 3D facial speech");
    let context: AudioContext | null = null;
    try {
      const result = await api.generateAudio2Face(
        value,
        voiceId,
        rate,
        avatarId,
        abort.signal,
      );
      const bytes = decodeBase64(result.audio_base64);
      const details = {
        sampleRate: result.audio_format.sample_rate,
        channels: result.audio_format.channels,
        sampleWidth: result.audio_format.sample_width,
      };
      if (
        details.channels !== 1 ||
        details.sampleWidth !== 2 ||
        !bytes.length
      ) {
        throw new Error(
          "The server returned unsupported animated speech audio",
        );
      }
      metadata.current = details;
      audio.current = [bytes];
      faceAnimation.current = result.animation;
      setRigAnimation(result.animation.rig);
      setRedacted(result.redacted);
      context = new AudioContext();
      const active = new PcmPlayer(context, details.sampleRate);
      player.current = active;
      await active.start();
      active.enqueue(bytes);
      animateLevel(active, result.animation);
      setAnimationStartedAt(performance.now() + 80);
      setNotice(`Speaking with 3D Audio2Face ${result.animation.model}`);
      await active.finish();
      await context.close().catch(() => undefined);
      context = null;
      setHasAudio(true);
      if (player.current === active) player.current = null;
      if (!abort.signal.aborted && run.current === activeRun) {
        setState("ready");
        setNotice("Reply complete");
        rearmMicrophone();
      }
    } catch (error) {
      player.current?.stop();
      player.current = null;
      await context?.close().catch(() => undefined);
      if (abort.signal.aborted) return;
      fail(error, "3D speech failed; the text reply remains saved");
      rearmMicrophone();
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }

  async function replay() {
    if (!metadata.current || !audio.current.length) return;
    stopPlayback();
    const activeRun = run.current;
    setState("speaking");
    setNotice("Replaying current audio");
    const context = new AudioContext();
    const active = new PcmPlayer(context, metadata.current.sampleRate);
    player.current = active;
    await active.start();
    animateLevel(active);
    setRigAnimation(faceAnimation.current?.rig ?? null);
    setAnimationStartedAt(performance.now() + 80);
    for (const chunk of audio.current) active.enqueue(chunk);
    await active.finish();
    await context.close().catch(() => undefined);
    if (run.current === activeRun) {
      setState("ready");
      setNotice("Replay complete");
      setMouthLevel(0);
    }
  }

  function download() {
    if (!metadata.current || !audio.current.length) return;
    const url = URL.createObjectURL(wavBlob(audio.current, metadata.current));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "voice-conversation-reply.wav";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function fail(error: unknown, fallback: string) {
    setState("error");
    setNotice(error instanceof Error ? error.message : fallback);
  }

  function rearmMicrophone() {
    if (!microphoneEnabledRef.current) return;
    speechInput.current?.rearm();
    setState("waiting");
    setNotice('Waiting for "Hey Buddy"');
  }

  function handleSpeechInputEvent(event: SpeechInputEvent) {
    if (event.type === "ready") {
      const listening = event.reason === "tap" || event.reason === "followup";
      setState(listening ? "listening" : "waiting");
      setNotice(
        listening ? "Listening for your message" : 'Waiting for "Hey Buddy"',
      );
    } else if (event.type === "wake_detected") {
      setState("listening");
      setNotice("Hey Buddy detected — listening");
      playWakeCue();
    } else if (event.type === "speech_started") {
      setState("listening");
      setNotice("Listening — stop speaking to send");
    } else if (event.type === "transcribing") {
      speechInput.current?.pause();
      setState("transcribing");
      setNotice("Transcribing locally...");
    } else if (event.type === "transcript" && event.transcript) {
      if (submittedUtterances.current.has(event.transcript.utterance_id))
        return;
      submittedUtterances.current.add(event.transcript.utterance_id);
      speechInput.current?.pause();
      setText(event.transcript.text);
      setRedacted(event.transcript.redacted);
      submitVoice.current(event.transcript.text);
    } else if (event.type === "timeout") {
      setNotice("No message heard; wake listening resumed");
      speechInput.current?.rearm();
      setState("waiting");
    } else if (event.type === "busy") {
      setState("error");
      setNotice("Microphone is active in another tab");
    } else if (event.type === "error") {
      setState("error");
      setNotice(event.reason || "Local speech recognition failed");
    }
  }

  function playWakeCue() {
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = 660;
    gain.gain.value = 0.04;
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.08);
    oscillator.onended = () => void context.close();
  }

  async function enableMicrophone(mode: "wake" | "tap" = "wake") {
    if (!speechInputAvailable) return;
    try {
      const client = new LocalSpeechInputClient(handleSpeechInputEvent);
      speechInput.current = client;
      setState("waiting");
      setNotice("Requesting microphone permission...");
      await client.enable(mode);
      microphoneEnabledRef.current = true;
      setMicrophoneEnabled(true);
      setState(mode === "tap" ? "listening" : "waiting");
      setNotice(
        mode === "tap"
          ? "Listening for your message"
          : 'Waiting for "Hey Buddy"',
      );
    } catch (error) {
      speechInput.current = null;
      microphoneEnabledRef.current = false;
      setMicrophoneEnabled(false);
      fail(error, "Microphone permission was denied");
    }
  }

  function tapMicrophone() {
    if (!microphoneEnabled) {
      void enableMicrophone("tap");
    } else if (state === "listening") {
      speechInput.current?.finish();
    } else if (!generating && !speaking && state !== "transcribing") {
      speechInput.current?.beginTap();
      setState("listening");
      setNotice("Listening for your message");
    }
  }

  async function disableMicrophone() {
    await speechInput.current?.disable();
    speechInput.current = null;
    microphoneEnabledRef.current = false;
    setMicrophoneEnabled(false);
    setState("ready");
    setNotice("Microphone off");
  }

  useEffect(() => {
    let mounted = true;
    void bootstrap()
      .then(async (config) => {
        if (!mounted) return;
        setEnabled(config.voice_conversation_enabled);
        setModels(config.models);
        setSelectedModel(config.model);
        setSpeechInputAvailable(config.speech_input_enabled);
        const faceStatus = await api.audio2faceStatus();
        if (!mounted) return;
        setAudio2face(faceStatus);
        if (faceStatus.available && faceStatus.avatar_available) {
          setAvatarMode("3d");
          const choices = faceStatus.avatars ?? [];
          let preferred =
            faceStatus.default_avatar_id || choices[0]?.avatar_id || "default";
          try {
            const saved = window.localStorage.getItem(
              "harness.audio2face.avatar",
            );
            if (saved && choices.some((item) => item.avatar_id === saved))
              preferred = saved;
          } catch {
            // Local preference storage is optional.
          }
          setAvatarId(preferred);
        }
        const inputStatus = await api.speechInputStatus();
        if (!mounted) return;
        setSpeechInputAvailable(inputStatus.enabled);
        setSpeechInputSetup(inputStatus.setup ?? "");
        if (!config.voice_conversation_enabled) {
          setState("error");
          setNotice("Voice conversations are unavailable");
          return;
        }
        if (config.speech_enabled) {
          const catalog = await api.speechVoices();
          if (!mounted) return;
          setVoices(catalog);
          setVoiceId(
            catalog.find((voice) => voice.default)?.voice_id ??
              catalog[0]?.voice_id ??
              "",
          );
        }
        const [saved, availableProfiles] = await Promise.all([
          api.voiceConversations(),
          api.voiceAgentProfiles
            ? api.voiceAgentProfiles()
            : Promise.resolve([]),
        ]);
        setProfiles(availableProfiles);
        if (!mounted) return;
        setConversations(saved);
        if (saved[0]) {
          const detail = await api.voiceConversation(saved[0].conversation_id);
          if (!mounted) return;
          setConversation(detail);
          setSelectedModel(detail.model);
          setSelectedProfile(detail.profile_id ?? "protected");
          setNotice(
            detail.mode === "agent"
              ? "Ready with the saved agent snapshot"
              : "Ready for one tool-free model call",
          );
        } else {
          setNotice("Start a new protected voice conversation");
        }
      })
      .catch((error: unknown) => {
        if (!mounted) return;
        setState("error");
        setNotice(
          error instanceof Error
            ? error.message
            : "Voice conversation startup failed",
        );
      });
    return () => {
      mounted = false;
      controller.current?.abort();
      player.current?.stop();
      cancelAnimationFrame(animation.current);
      microphoneEnabledRef.current = false;
      void speechInput.current?.disable();
    };
  }, []);

  useEffect(() => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(
      `${protocol}://${location.host}/api/v1/stream?client_id=${clientId}&after=${lastEvent.current}`,
    );
    socket.onmessage = (message) => {
      const event = JSON.parse(String(message.data)) as StreamEvent;
      lastEvent.current = Math.max(lastEvent.current, event.event_id);
      if (!activeTask.current || event.task_id !== activeTask.current) return;
      if (event.type === "task.started") {
        setState("generating");
        setNotice("Generating with the profile's bounded agent policy");
      } else if (event.type === "progress") {
        const summary = String(event.payload.summary ?? "Agent activity");
        setState("tool-running");
        setNotice(summary);
        setActivity((current) => [...current.slice(-7), summary]);
      } else if (event.type === "approval.requested") {
        speechInput.current?.pause();
        setApproval(event.payload as unknown as Approval);
        setState("awaiting-approval");
        setNotice("Awaiting your click approval");
      } else if (event.type.startsWith("approval.")) {
        setApproval(null);
        setState("generating");
      } else if (event.type === "task.cancelling") {
        setState("cancellation-requested");
      } else if (event.type === "task.cancelled") {
        activeTask.current = "";
        setApproval(null);
        setState("stopped");
        setNotice("Agent task cancelled");
      } else if (event.type === "task.failed") {
        activeTask.current = "";
        setApproval(null);
        setState("error");
        setNotice(String(event.payload.error ?? "Agent task failed"));
        rearmMicrophone();
      } else if (event.type === "task.completed") {
        const currentId = conversation?.conversation_id;
        activeTask.current = "";
        setApproval(null);
        if (!currentId) return;
        void api
          .voiceConversation(currentId)
          .then(async (detail) => {
            setConversation(detail);
            await refreshConversationListOnly();
            const answer = [...detail.messages]
              .reverse()
              .find((item) => item.role === "assistant");
            if (detail.auto_speak !== false && answer?.speech_text && voiceId) {
              await speakText(answer.speech_text);
            } else {
              setState("ready");
              setNotice("Agent reply complete");
              rearmMicrophone();
            }
          })
          .catch((error: Error) =>
            fail(error, "Could not load the agent reply"),
          );
      }
    };
    return () => socket.close();
    // The stream is rebound only when task routing or speech output identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation?.conversation_id, voiceId]);

  const speaking = state === "speaking";
  const generating = [
    "generating",
    "tool-running",
    "awaiting-approval",
    "cancellation-requested",
  ].includes(state);
  return (
    <div className="speech-page voice-chat-page">
      <AppHeader
        current="speech"
        title="Voice Conversation"
        status={
          <StatusRegion
            tone={
              state === "error"
                ? "danger"
                : speaking || generating
                  ? "info"
                  : "success"
            }
          >
            {speaking ? "Speaking" : generating ? "Generating" : "Voice ready"}
          </StatusRegion>
        }
      />
      <nav className="speech-mode-tabs" aria-label="Speech mode">
        <button className="active" aria-current="page">
          Voice Conversation
        </button>
        <button onClick={onDirect}>Direct Text-to-Speech</button>
      </nav>
      <main className="voice-chat-main" id="main-content">
        <aside className="voice-conversation-sidebar">
          <div className="section-heading">
            <div>
              <span>Conversations</span>
              <strong>Saved voice chats</strong>
            </div>
          </div>
          <button
            className="send"
            disabled={generating}
            onClick={() => void createConversation()}
          >
            New conversation
          </button>
          <label>
            Profile for new conversation
            <select
              aria-label="Voice agent profile"
              value={selectedProfile}
              disabled={generating}
              onChange={(event) => setSelectedProfile(event.target.value)}
            >
              <option value="protected">Protected Voice Chat</option>
              {profiles.map((profile) => (
                <option
                  key={profile.profile_id}
                  value={profile.profile_id}
                  disabled={!profile.available}
                >
                  {profile.name} · revision {profile.revision}
                  {!profile.available ? " · unavailable" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            Conversation
            <select
              aria-label="Saved voice conversation"
              value={conversation?.conversation_id ?? ""}
              disabled={generating || !conversations.length}
              onChange={(event) => void selectConversation(event.target.value)}
            >
              {!conversations.length && (
                <option value="">No saved conversations</option>
              )}
              {conversations.map((item) => (
                <option key={item.conversation_id} value={item.conversation_id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Model
            <select
              aria-label="Voice conversation model"
              value={selectedModel}
              disabled={generating || conversation?.mode === "agent"}
              onChange={(event) => void changeModel(event.target.value)}
            >
              {models.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          {conversation?.mode === "agent" && (
            <div
              className={`profile-snapshot ${conversation.configuration_status}`}
            >
              <strong>{conversation.profile_name}</strong>
              <small>
                Snapshot revision {conversation.profile_revision} ·{" "}
                {conversation.workspace_label}
                {" · "}
                {conversation.configuration_status}
              </small>
              {conversation.snapshot && (
                <small>
                  {conversation.snapshot.allowed_tools.length} allowed tools ·{" "}
                  {conversation.snapshot.max_turns} calls maximum
                </small>
              )}
              {conversation.configuration_status === "outdated" && (
                <button
                  disabled={generating}
                  onClick={() => void upgradeProfile()}
                >
                  Apply latest revision
                </button>
              )}
            </div>
          )}
          <div className="conversation-actions">
            <button
              disabled={!conversation || generating}
              onClick={() => void renameConversation()}
            >
              Rename
            </button>
            <button
              disabled={!conversation || generating}
              onClick={() => void deleteConversation()}
            >
              Delete
            </button>
          </div>
        </aside>
        <aside
          className="voice-settings-panel"
          aria-label="Voice and microphone settings"
        >
          <div className="section-heading">
            <div>
              <span>Input and output</span>
              <strong>Voice settings</strong>
            </div>
          </div>
          <label>
            Avatar
            <select
              aria-label="Conversation avatar mode"
              value={avatarMode}
              disabled={speaking}
              onChange={(event) =>
                setAvatarMode(event.target.value as AvatarMode)
              }
            >
              <option
                value="3d"
                disabled={
                  !audio2face?.available || !audio2face.avatar_available
                }
              >
                3D Audio2Face
              </option>
              <option value="2d">2D audio-reactive</option>
            </select>
          </label>
          {avatarMode === "3d" && (audio2face?.avatars?.length ?? 0) > 0 && (
            <label>
              Character
              <select
                aria-label="Conversation 3D avatar character"
                value={avatarId}
                disabled={speaking}
                onChange={(event) => {
                  const selected = event.target.value;
                  setAvatarId(selected);
                  try {
                    window.localStorage.setItem(
                      "harness.audio2face.avatar",
                      selected,
                    );
                  } catch {
                    // Local preference storage is optional.
                  }
                }}
              >
                {audio2face?.avatars?.map((avatar) => (
                  <option key={avatar.avatar_id} value={avatar.avatar_id}>
                    {avatar.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Voice
            <select
              aria-label="Conversation voice"
              value={voiceId}
              disabled={speaking}
              onChange={(event) => setVoiceId(event.target.value)}
            >
              {voices.map((voice) => (
                <option key={voice.voice_id} value={voice.voice_id}>
                  {voice.display_name} - {voice.language}
                </option>
              ))}
            </select>
          </label>
          <label>
            Rate: {rate.toFixed(2)}x
            <input
              aria-label="Conversation speech rate"
              type="range"
              min="0.75"
              max="1.5"
              step="0.05"
              value={rate}
              disabled={speaking}
              onChange={(event) => setRate(Number(event.target.value))}
            />
          </label>
          <div className="microphone-controls">
            <button
              disabled={!speechInputAvailable || microphoneEnabled}
              onClick={() => void enableMicrophone("wake")}
            >
              Enable Hey Buddy
            </button>
            <button
              disabled={
                !speechInputAvailable ||
                generating ||
                speaking ||
                state === "transcribing"
              }
              onClick={tapMicrophone}
            >
              {state === "listening" ? "Finish listening" : "Tap to talk"}
            </button>
            {microphoneEnabled && (
              <button onClick={() => void disableMicrophone()}>
                Microphone off
              </button>
            )}
          </div>
          {!speechInputAvailable && speechInputSetup && (
            <small className="voice-license">{speechInputSetup}</small>
          )}
        </aside>
        <section className="voice-chat-content">
          <div className="voice-chat-avatar-row">
            <SpeakingAvatar
              key={`${avatarMode}:${avatarId}`}
              mode={avatarMode}
              available={Boolean(
                audio2face?.available && audio2face.avatar_available,
              )}
              active={speaking}
              animation={rigAnimation}
              startedAt={animationStartedAt}
              mouthLevel={mouthLevel}
              label={notice}
              fallbackNotice={audio2face?.setup}
              avatarId={avatarId}
              avatarRevision={
                audio2face?.avatars?.find((item) => item.avatar_id === avatarId)
                  ?.sha256
              }
            />
            <div className="voice-chat-summary">
              <p className={`speech-status ${state}`} aria-live="polite">
                {notice}
              </p>
              <small>
                {conversation?.mode === "agent"
                  ? "Bounded agent loop · exact snapshotted tools · final answer only is spoken"
                  : "One LLM call per turn · no tools or workspace context"}
                {" · audio is not saved"}
              </small>
              {redacted && (
                <p className="warning-text">
                  Sensitive-looking text was redacted before the model call.
                </p>
              )}
            </div>
          </div>
          <div
            className="voice-transcript"
            aria-label="Voice conversation transcript"
          >
            {conversation?.has_older_messages && (
              <button onClick={() => void loadOlder()}>
                Load older messages
              </button>
            )}
            {!conversation?.messages.length && (
              <EmptyState title="Start a voice conversation">
                Type or speak a message. Replies are saved as text and spoken
                automatically.
              </EmptyState>
            )}
            {conversation?.messages.map((message) => (
              <article
                key={message.message_id}
                className={`voice-message ${message.role}`}
              >
                <strong>
                  {message.role === "user" ? "You" : "Voice assistant"}
                </strong>
                {message.role === "assistant" ? (
                  <SafeMarkdown text={message.content} />
                ) : (
                  <p>{message.content}</p>
                )}
                {message.role === "assistant" && (
                  <button
                    disabled={generating || speaking || !voiceId}
                    onClick={() => void speakText(message.speech_text)}
                  >
                    Speak
                  </button>
                )}
              </article>
            ))}
          </div>
          {activity.length > 0 && generating && (
            <aside
              className="voice-agent-activity"
              aria-label="Voice agent activity"
            >
              <strong>Agent activity</strong>
              <ol>
                {activity.map((item, index) => (
                  <li key={`${index}-${item}`}>{item}</li>
                ))}
              </ol>
            </aside>
          )}
          <label className="voice-composer">
            Message
            <textarea
              aria-label="Voice conversation message"
              maxLength={5000}
              value={text}
              disabled={!enabled || generating}
              onChange={(event) => setText(event.target.value)}
              placeholder="Ask something in English or Hindi..."
            />
          </label>
          <div className="speech-count">
            {text.length.toLocaleString()} / 5,000
          </div>
          <div className="speech-actions">
            <button
              className="send"
              disabled={!enabled || generating || !text.trim()}
              onClick={() => void send()}
            >
              Send and speak
            </button>
            <button disabled={!speaking && !generating} onClick={stop}>
              Stop
            </button>
            <button
              disabled={generating || speaking || !hasAudio}
              onClick={() => void replay()}
            >
              Replay
            </button>
            <button
              disabled={generating || speaking || !hasAudio}
              onClick={download}
            >
              Download WAV
            </button>
          </div>
        </section>
      </main>
      {approval && conversation?.snapshot && (
        <ApprovalDialog
          approval={approval}
          workspaceId={conversation.snapshot.workspace_id}
          onClose={() => setApproval(null)}
          onNotice={setNotice}
        />
      )}
    </div>
  );
}

function decodeBase64(value: string): Uint8Array {
  const decoded = window.atob(value);
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}
