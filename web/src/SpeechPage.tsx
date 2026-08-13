import { useState } from "react";
import DirectSpeechPanel from "./SpeechApp";
import VoiceConversationPanel from "./VoiceConversationPanel";

export default function SpeechPage() {
  const [mode, setMode] = useState<"conversation" | "direct">(
    new URLSearchParams(location.search).get("mode") === "direct"
      ? "direct"
      : "conversation",
  );

  function selectMode(value: "conversation" | "direct") {
    setMode(value);
    const url = value === "direct" ? "/speech?mode=direct" : "/speech";
    history.replaceState(null, "", url);
  }

  return mode === "conversation" ? (
    <VoiceConversationPanel onDirect={() => selectMode("direct")} />
  ) : (
    <DirectSpeechPanel onVoice={() => selectMode("conversation")} />
  );
}
