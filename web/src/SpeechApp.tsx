import { useEffect, useMemo, useRef, useState } from "react";
import { api, bootstrap } from "./api";
import { PcmPlayer, type PcmMetadata, wavBlob } from "./speech-audio";
import type { SpeechVoice } from "./types";
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
  const player = useRef<PcmPlayer | null>(null);
  const controller = useRef<AbortController | null>(null);
  const animation = useRef(0);
  const audio = useRef<Uint8Array[]>([]);
  const metadata = useRef<PcmMetadata | null>(null);
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

  function animateLevel(active: PcmPlayer) {
    if (reducedMotion) return;
    const update = () => {
      setMouthLevel(active.level());
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
    animateLevel(active);
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
          <svg
            className={`speech-avatar ${busy ? "active" : ""}`}
            viewBox="0 0 320 320"
            role="img"
            aria-label={notice}
          >
            <circle className="avatar-halo" cx="160" cy="160" r="145" />
            <rect
              className="avatar-head"
              x="65"
              y="55"
              width="190"
              height="210"
              rx="72"
            />
            <circle className="avatar-eye" cx="125" cy="135" r="12" />
            <circle className="avatar-eye" cx="195" cy="135" r="12" />
            <ellipse
              className="avatar-mouth"
              cx="160"
              cy="205"
              rx="32"
              ry={6 + mouthLevel * 22}
            />
          </svg>
          <p className={`speech-status ${state}`} aria-live="polite">
            {notice}
          </p>
          {redacted && (
            <p className="warning-text">
              Sensitive-looking text was redacted before speech.
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
              onClick={() => void speak()}
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
