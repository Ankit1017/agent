import { useEffect, useMemo, useRef, useState } from "react";
import { api, bootstrap } from "./api";
import { SpeakingAvatar, type AvatarMode } from "./Audio2FaceAvatar";
import { PcmPlayer, type PcmMetadata, wavBlob } from "./speech-audio";
import type {
  Audio2FaceStatus,
  FaceAnimation,
  FaceRigAnimation,
  SpeechVoice,
} from "./types";
import { AppHeader, StatusRegion } from "./ui";

type SpeechState = "idle" | "loading" | "speaking" | "stopped" | "error";

export default function DirectSpeechPanel({
  onVoice,
}: {
  onVoice: () => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [maxChars, setMaxChars] = useState(5000);
  const [voices, setVoices] = useState<SpeechVoice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [text, setText] = useState("");
  const [rate, setRate] = useState(1);
  const [state, setState] = useState<SpeechState>("idle");
  const [notice, setNotice] = useState("Preparing local speech…");
  const [redacted, setRedacted] = useState(false);
  const [mouthLevel, setMouthLevel] = useState(0);
  const [eyeOffset, setEyeOffset] = useState({ x: 0, y: 0 });
  const [audio2face, setAudio2face] = useState<Audio2FaceStatus | null>(null);
  const [avatarMode, setAvatarMode] = useState<AvatarMode>("2d");
  const [avatarId, setAvatarId] = useState("default");
  const [rigAnimation, setRigAnimation] = useState<FaceRigAnimation | null>(
    null,
  );
  const [animationStartedAt, setAnimationStartedAt] = useState(0);
  const player = useRef<PcmPlayer | null>(null);
  const controller = useRef<AbortController | null>(null);
  const animation = useRef(0);
  const audio = useRef<Uint8Array[]>([]);
  const metadata = useRef<PcmMetadata | null>(null);
  const faceAnimation = useRef<FaceAnimation | null>(null);
  const run = useRef(0);
  const reducedMotion = useMemo(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  useEffect(() => {
    void bootstrap()
      .then(async (data) => {
        setEnabled(data.speech_enabled);
        setMaxChars(data.speech_max_chars);
        const status = await api.audio2faceStatus();
        setAudio2face(status);
        if (status.available && status.avatar_available) {
          setAvatarMode("3d");
          const choices = status.avatars ?? [];
          let preferred =
            status.default_avatar_id || choices[0]?.avatar_id || "default";
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
        if (!data.speech_enabled) {
          setNotice(
            "Local speech is disabled. Run scripts/setup-voices.ps1, then enable it.",
          );
          return;
        }
        const catalog = await api.speechVoices();
        setVoices(catalog);
        setVoiceId(
          catalog.find((voice) => voice.default)?.voice_id ??
            catalog[0]?.voice_id ??
            "",
        );
        setNotice("Ready to speak locally");
      })
      .catch((error: Error) => {
        setState("error");
        setNotice(`Speech startup error: ${error.message}`);
      });
    return () => stopPlayback(false);
    // Startup is intentionally performed once for this independent page.
  }, []);

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
        setEyeOffset({ x: frame?.eye_x ?? 0, y: frame?.eye_y ?? 0 });
      } else {
        setMouthLevel(active.level());
        setEyeOffset({ x: 0, y: 0 });
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
    if (updateLevel) setMouthLevel(0);
    if (updateLevel) setEyeOffset({ x: 0, y: 0 });
  }

  function stop() {
    stopPlayback();
    setState("stopped");
    setNotice("Speech stopped");
  }

  async function speak() {
    const value = text.trim();
    if (!enabled || !voiceId || !value || value.length > maxChars) return;
    stopPlayback();
    const activeRun = run.current;
    setState("loading");
    setNotice("Loading local voice…");
    setRedacted(false);
    audio.current = [];
    faceAnimation.current = null;
    setRigAnimation(null);
    const abort = new AbortController();
    controller.current = abort;
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
      setRedacted(response.headers.get("X-Speech-Redacted") === "true");
      const active = new PcmPlayer(context, sampleRate);
      player.current = active;
      await active.start();
      animateLevel(active);
      const reader = response.body.getReader();
      setState("speaking");
      setNotice("Speaking");
      while (true) {
        const { done, value: chunk } = await reader.read();
        if (done) break;
        const copy = chunk.slice();
        audio.current.push(copy);
        active.enqueue(copy);
      }
      await active.finish();
      await context.close().catch(() => undefined);
      if (player.current === active) player.current = null;
      if (!abort.signal.aborted && run.current === activeRun) {
        cancelAnimationFrame(animation.current);
        setMouthLevel(0);
        setState("idle");
        setNotice("Speech complete");
      }
    } catch (error) {
      await context.close().catch(() => undefined);
      if (abort.signal.aborted) return;
      setState("error");
      setNotice(error instanceof Error ? error.message : "Speech failed");
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }

  async function replay() {
    if (!metadata.current || !audio.current.length) return;
    stopPlayback();
    const activeRun = run.current;
    setState("speaking");
    setNotice("Replaying");
    const context = new AudioContext();
    const active = new PcmPlayer(context, metadata.current.sampleRate);
    player.current = active;
    await active.start();
    animateLevel(active, faceAnimation.current);
    setRigAnimation(faceAnimation.current?.rig ?? null);
    setAnimationStartedAt(performance.now() + 80);
    for (const chunk of audio.current) active.enqueue(chunk);
    await active.finish();
    await context.close().catch(() => undefined);
    if (player.current === active) player.current = null;
    if (run.current === activeRun) {
      cancelAnimationFrame(animation.current);
      setMouthLevel(0);
      setState("idle");
      setNotice("Replay complete");
    }
  }

  async function speakWithAudio2Face() {
    const value = text.trim();
    if (
      !enabled ||
      !audio2face?.available ||
      !voiceId ||
      !value ||
      value.length > maxChars
    )
      return;
    stopPlayback();
    const activeRun = run.current;
    setState("loading");
    setNotice("Generating local speech and Audio2Face animation...");
    setRedacted(false);
    audio.current = [];
    const abort = new AbortController();
    controller.current = abort;
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
      setState("speaking");
      setNotice(`Speaking with Audio2Face ${result.animation.model}`);
      await active.finish();
      await context.close().catch(() => undefined);
      context = null;
      if (player.current === active) player.current = null;
      if (run.current === activeRun) {
        cancelAnimationFrame(animation.current);
        setMouthLevel(0);
        setEyeOffset({ x: 0, y: 0 });
        setState("idle");
        setNotice("Audio2Face playback complete");
      }
    } catch (error) {
      player.current?.stop();
      player.current = null;
      await context?.close().catch(() => undefined);
      if (abort.signal.aborted) return;
      setState("error");
      setNotice(
        error instanceof Error ? error.message : "Audio2Face generation failed",
      );
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }

  function download() {
    if (!metadata.current || !audio.current.length) return;
    const url = URL.createObjectURL(wavBlob(audio.current, metadata.current));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "local-speech.wav";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const busy = state === "loading" || state === "speaking";
  return (
    <div className="speech-page">
      <AppHeader
        current="speech"
        title="Direct Text-to-Speech"
        status={
          <StatusRegion
            tone={state === "error" ? "danger" : busy ? "info" : "success"}
          >
            {busy ? "Audio active" : "Local audio"}
          </StatusRegion>
        }
      />
      <nav className="speech-mode-tabs" aria-label="Speech mode">
        <button onClick={onVoice}>Voice Conversation</button>
        <button className="active" aria-current="page">
          Direct Text-to-Speech
        </button>
      </nav>
      <main className="speech-main" id="main-content">
        <section className="avatar-card" aria-label="Speaking avatar">
          <SpeakingAvatar
            key={`${avatarMode}:${avatarId}`}
            mode={avatarMode}
            available={Boolean(
              audio2face?.available && audio2face.avatar_available,
            )}
            active={state === "speaking"}
            animation={rigAnimation}
            startedAt={animationStartedAt}
            mouthLevel={mouthLevel}
            eyeOffset={eyeOffset}
            label={notice}
            fallbackNotice={audio2face?.setup}
            avatarId={avatarId}
            avatarRevision={
              audio2face?.avatars?.find((item) => item.avatar_id === avatarId)
                ?.sha256
            }
          />
          <p className={`speech-status ${state}`} aria-live="polite">
            {notice}
          </p>
          {redacted && (
            <p className="warning-text">
              Sensitive-looking text was redacted before speech.
            </p>
          )}
          {audio2face && !audio2face.available && (
            <p className="voice-license" role="status">
              Audio2Face: {audio2face.setup}
            </p>
          )}
        </section>
        <section className="speech-controls">
          <h1>Turn text into local speech</h1>
          <p>
            Audio is generated on this computer and is not saved by the server.
          </p>
          <label>
            Text
            <textarea
              aria-label="Speech text"
              maxLength={maxChars}
              disabled={!enabled || busy}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Type English or Hindi text…"
            />
          </label>
          <div className="speech-count">
            {text.length.toLocaleString()} / {maxChars.toLocaleString()}
          </div>
          <div className="speech-options">
            <label>
              Avatar
              <select
                aria-label="Avatar mode"
                value={avatarMode}
                disabled={busy}
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
                  aria-label="3D avatar character"
                  value={avatarId}
                  disabled={busy}
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
                    <option value={avatar.avatar_id} key={avatar.avatar_id}>
                      {avatar.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              Voice
              <select
                aria-label="Speech voice"
                disabled={!enabled || busy}
                value={voiceId}
                onChange={(event) => setVoiceId(event.target.value)}
              >
                {voices.map((voice) => (
                  <option value={voice.voice_id} key={voice.voice_id}>
                    {voice.display_name} — {voice.language}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rate: {rate.toFixed(2)}×
              <input
                aria-label="Speech rate"
                type="range"
                min="0.75"
                max="1.5"
                step="0.05"
                disabled={!enabled || busy}
                value={rate}
                onChange={(event) => setRate(Number(event.target.value))}
              />
            </label>
          </div>
          {voiceId && (
            <p className="voice-license">
              {
                voices.find((voice) => voice.voice_id === voiceId)
                  ?.license_summary
              }
            </p>
          )}
          <div className="speech-actions">
            <button
              className="send"
              disabled={!enabled || busy || !text.trim()}
              onClick={() =>
                void (avatarMode === "3d" ? speakWithAudio2Face() : speak())
              }
            >
              Speak
            </button>
            <button disabled={!busy} onClick={stop}>
              Stop
            </button>
            <button
              disabled={busy || !audio.current.length}
              onClick={() => void replay()}
            >
              Replay
            </button>
            <button disabled={busy || !audio.current.length} onClick={download}>
              Download WAV
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}

function decodeBase64(value: string): Uint8Array {
  const decoded = window.atob(value);
  const output = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    output[index] = decoded.charCodeAt(index);
  }
  return output;
}
