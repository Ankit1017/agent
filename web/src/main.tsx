import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import SpeechPage from "./SpeechPage";
import VoiceAgentsPage from "./VoiceAgentsPage";
import StudioUnavailablePage from "./StudioUnavailablePage";
import "./styles.css";
import "./design-system.css";

const normalizedPath = location.pathname.replace(/\/$/, "") || "/";
const Page =
  normalizedPath === "/studio"
    ? StudioUnavailablePage
    : normalizedPath === "/speech/agents"
      ? VoiceAgentsPage
      : normalizedPath === "/speech"
        ? SpeechPage
        : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
);
