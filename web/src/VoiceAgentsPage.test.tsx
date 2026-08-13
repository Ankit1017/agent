import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VoiceAgentsPage from "./VoiceAgentsPage";

const mockApi = vi.hoisted(() => ({
  voiceAgentCatalog: vi.fn(),
  voiceAgentProfiles: vi.fn(),
  createVoiceAgentProfile: vi.fn(),
  updateVoiceAgentProfile: vi.fn(),
  cloneVoiceAgentProfile: vi.fn(),
  deleteVoiceAgentProfile: vi.fn(),
}));

vi.mock("./api", () => ({
  bootstrap: vi.fn().mockResolvedValue({ csrf_token: "csrf" }),
  api: mockApi,
}));

const catalog = {
  models: ["model-a"],
  voices: [
    {
      voice_id: "voice-a",
      display_name: "English",
      language: "en",
      default: true,
      loaded: true,
      license_summary: "local",
      audio_format: {
        sample_rate: 22050,
        channels: 1,
        sample_width: 2,
        encoding: "s16le",
      },
    },
  ],
  workspaces: [
    {
      workspace_id: "workspace-a",
      label: "Demo",
      tools: [
        {
          name: "read_file",
          description: "Read a file",
          profile: "coding",
          risk: "read",
        },
        {
          name: "apply_patch",
          description: "Apply a patch",
          profile: "coding",
          risk: "approval",
          approval_required: true,
        },
      ],
    },
  ],
  bounds: { context_max_chars: [4000, 30000] },
  templates: [
    {
      template_id: "protected",
      name: "Protected Voice Chat",
      immutable: true,
      allowed_tools: [],
    },
    {
      template_id: "reader",
      name: "Workspace Reader",
      allowed_tools: ["read_file"],
    },
  ],
};

describe("voice-agent profile configuration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.voiceAgentCatalog.mockResolvedValue(catalog);
    mockApi.voiceAgentProfiles.mockResolvedValue([]);
    mockApi.createVoiceAgentProfile.mockImplementation(async (value) => ({
      ...value,
      profile_id: "profile-a",
      revision: 1,
      created_at: "now",
      updated_at: "now",
      available: true,
      unavailable_reasons: [],
    }));
  });

  it("shows immutable protection, applies an exact template, and saves", async () => {
    render(<VoiceAgentsPage />);
    expect(await screen.findByText("Protected Voice Chat")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Workspace Reader" }));
    expect(screen.getByRole("checkbox", { name: /read_file/ })).toBeChecked();
    expect(screen.getByText(/click approval/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() =>
      expect(mockApi.createVoiceAgentProfile).toHaveBeenCalledOnce(),
    );
  });
});
