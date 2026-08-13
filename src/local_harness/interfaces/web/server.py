"""Command-line launcher for the localhost browser harness."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from local_harness.bootstrap import (
    build_speech_input_service,
    build_speech_service,
    build_voice_agent_profile_service,
    build_voice_conversation_service,
)
from local_harness.interfaces.web.api import create_app
from local_harness.interfaces.web.coordinator import WebRuntimeCoordinator


def main(argv: list[str] | None = None) -> None:
    """Run the host-side GUI server on the IPv4 loopback interface."""
    parser = argparse.ArgumentParser(description="Local AI Harness browser server")
    parser.add_argument("--control-workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--catalog", type=Path, default=Path("local-ai/runtime/harness-workspaces.json")
    )
    parser.add_argument("--static-dir", type=Path, default=Path("web/dist"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        parser.error("harness-web binds only to 127.0.0.1")
    control = args.control_workspace.resolve(strict=True)
    coordinator = WebRuntimeCoordinator(control, args.catalog.resolve())
    speech_service = build_speech_service(control, coordinator.settings)
    speech_input_service = build_speech_input_service(control, coordinator.settings)
    voice_conversation_service = build_voice_conversation_service(control, coordinator.settings)
    voice_agent_profile_service = build_voice_agent_profile_service(
        control,
        coordinator.settings,
        workspace_ids=lambda: {item.workspace_id for item in coordinator.workspaces()},
        tool_names=lambda workspace_id: {
            *(
                tool.definition.name
                for tool in coordinator.state(workspace_id).runtime.registry.tools
            ),
            "task_plan",
        },
        voices=(
            tuple(item.voice_id for item in speech_service.voices())
            if speech_service is not None
            else coordinator.settings.tts_voices
        ),
    )
    origins = frozenset(
        {
            f"http://127.0.0.1:{args.port}",
            f"http://localhost:{args.port}",
        }
    )
    app = create_app(
        coordinator,
        args.static_dir.resolve(),
        speech_service=speech_service,
        speech_input_service=speech_input_service,
        voice_conversation_service=voice_conversation_service,
        voice_agent_profile_service=voice_agent_profile_service,
        origins=origins,
        trusted_hosts=["127.0.0.1", "localhost", f"127.0.0.1:{args.port}"],
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
