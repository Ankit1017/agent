import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import SpeechPage from "./SpeechPage";
import VoiceAgentsPage from "./VoiceAgentsPage";
import "./styles.css";

const normalizedPath = location.pathname.replace(/\/$/, "") || "/";
const Page =
  normalizedPath === "/speech/agents"
    ? VoiceAgentsPage
    : normalizedPath === "/speech"
      ? SpeechPage
      : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
);
