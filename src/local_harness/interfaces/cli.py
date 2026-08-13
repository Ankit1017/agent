"""Interactive command-line interface for the local harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal, cast

from local_harness.application.agent import AgentService
from local_harness.application.ports import (
    ApprovalGateway,
    PatchApprovalGateway,
    ProgressSink,
    SessionMaintenanceGateway,
)
from local_harness.application.session_services import session_info
from local_harness.bootstrap import Runtime, build_runtime
from local_harness.domain.errors import HarnessError
from local_harness.domain.models import ProgressEvent, Session
from local_harness.guardrails.redaction import SecretRedactor
from local_harness.interfaces.commands import InterfaceCommand, parse_command
from local_harness.interfaces.console import format_progress_event
from local_harness.interfaces.markdown import write_assistant_markdown
from local_harness.interfaces.ui_mode import UiMode, select_ui_mode


def main(argv: list[str] | None = None) -> None:
    """Run the interactive harness and translate expected errors for users."""
    parser = argparse.ArgumentParser(description="Approval-based local terminal harness")
    parser.add_argument("--resume", metavar="SESSION_ID", help="resume a saved session")
    parser.add_argument(
        "--ui",
        choices=("auto", "tui", "plain"),
        default="auto",
        help="terminal interface mode (default: auto)",
    )
    parser.add_argument(
        "--max-turns",
        type=_parse_max_turns,
        metavar="1-100",
        help="maximum LLM calls per user request",
    )
    args = parser.parse_args(argv)
    try:
        mode = select_ui_mode(cast(UiMode, args.ui), sys.stdin, sys.stdout, os.environ)
        if mode == "tui":
            _run_tui(args.resume, args.max_turns)
            return
        runtime = build_runtime(Path.cwd(), max_turns_override=args.max_turns)
        session = runtime.sessions.load(args.resume) if args.resume else runtime.new_session()
        if Path(session.workspace).resolve() != runtime.workspace:
            print(
                f"Warning: session was created in {session.workspace}; "
                f"tools use {runtime.workspace}."
            )
        _repl(runtime, runtime.agent(session), replay_events=bool(args.resume))
    except KeyboardInterrupt:
        print("\nStopped.")
    except HarnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _run_tui(session_id: str | None, max_turns: int | None) -> None:
    """Compose and run the Textual interface without hard-wiring its ports."""
    from local_harness.interfaces.tui import HarnessApp, TuiBridge

    bridge_holder: list[TuiBridge] = []

    def presentation_factory(
        redactor: SecretRedactor,
    ) -> tuple[ApprovalGateway, PatchApprovalGateway, ProgressSink, SessionMaintenanceGateway]:
        bridge = TuiBridge(redactor)
        bridge_holder.append(bridge)
        return bridge, bridge, bridge, bridge

    runtime = build_runtime(
        Path.cwd(),
        max_turns_override=max_turns,
        presentation_factory=presentation_factory,
    )
    session = runtime.sessions.load(session_id) if session_id else runtime.new_session()
    app = HarnessApp(runtime, runtime.agent(session), bridge_holder[0])
    app.run()


def _repl(runtime: Runtime, agent: AgentService, *, replay_events: bool = False) -> None:
    print(f"Local Terminal Harness | model={agent.session.model}")
    print(f"workspace={runtime.workspace}")
    print(f"session={agent.session.session_id}")
    print(f"max LLM calls/request={agent.max_turns} (source={agent.max_turns_source})")
    integrity_findings = getattr(runtime, "integrity_findings", [])
    if integrity_findings:
        print(f"Warning: {len(integrity_findings)} session integrity issue(s); use /session-check.")
    print("Type /help for commands. PowerShell always requires approval.\n")
    if replay_events:
        _print_events(agent.session.events[-5:])
        print()
    while True:
        user_input = input("you> ").strip()
        if not user_input:
            continue
        parsed = parse_command(user_input)
        if parsed.error:
            print(parsed.error)
            continue
        command = parsed.command
        if command is not None:
            result = _handle_plain_command(runtime, agent, command)
            if result is None:
                print("Session saved. Goodbye.")
                return
            agent = result
            continue
        try:
            sanitizer = getattr(agent, "sanitize_input", lambda value: (value, False))
            safe_input, changed = sanitizer(user_input)
            if changed:
                print(f"Credential-like text was redacted before sending:\n{safe_input}")
            response = agent.submit(safe_input)
            print("\nassistant>")
            write_assistant_markdown(response)
            print()
        except HarnessError as exc:
            print(f"\nError: {exc}\n")


def _handle_plain_command(
    runtime: Runtime, agent: AgentService, command: InterfaceCommand
) -> AgentService | None:
    """Execute one parsed command for the plain interface."""
    if command.name == "exit":
        return None
    if command.name == "help":
        _print_help()
    elif command.name == "sessions":
        _print_sessions(runtime)
    elif command.name == "max-turns":
        raw_value = command.argument
        if not raw_value:
            print(f"max LLM calls/request={agent.max_turns} (source={agent.max_turns_source})")
            return agent
        try:
            if raw_value.casefold() == "reset":
                agent.configure_max_turns(None)
            else:
                agent.configure_max_turns(_parse_max_turns(raw_value))
        except (argparse.ArgumentTypeError, ValueError) as exc:
            print(f"Error: {exc}")
            return agent
        print(f"max LLM calls/request={agent.max_turns} (source={agent.max_turns_source})")
    elif command.name == "models":
        for model in runtime.settings.models:
            marker = "*" if model == agent.session.model else " "
            print(f"{marker} {model}")
    elif command.name == "model":
        if not command.argument:
            print(f"model={agent.session.model}")
        else:
            try:
                agent = runtime.switch_model(
                    agent.session,
                    None if command.argument.casefold() == "reset" else command.argument,
                )
                print(f"model={agent.session.model}")
            except HarnessError as exc:
                print(f"Error: {exc}")
    elif command.name == "events":
        parts = command.argument.split(maxsplit=1)
        try:
            count = int(parts[0]) if parts and parts[0].isdigit() else 20
            if count <= 0:
                raise ValueError
        except ValueError:
            print("Usage: /events [positive-count] [filter]")
            return agent
        query = parts[1] if parts and parts[0].isdigit() and len(parts) > 1 else command.argument
        service = getattr(runtime, "session_service", None)
        events = (
            service.filter_events(agent.session, query)
            if service is not None
            else list(agent.session.events)
        )
        _print_events(events[-count:])
    elif command.name == "session-info":
        session = _command_session(runtime, agent, command.argument)
        if session is not None:
            budget = (
                session.token_budget_override
                if session.token_budget_override is not None
                else runtime.settings.session_token_budget
            )
            print(session_info(session, budget))
    elif command.name == "summarize":
        session = _command_session(runtime, agent, command.argument)
        if session is not None:
            target_agent = agent if session is agent.session else runtime.agent(session)
            print(target_agent.summarize_with_model())
    elif command.name == "quota":
        try:
            if not command.argument:
                pass
            elif command.argument.casefold() == "reset":
                agent.configure_token_budget(None)
            else:
                agent.configure_token_budget(int(command.argument))
            display_budget = agent.token_budget or "disabled"
            print(f"session tokens={agent.token_usage}, advisory budget={display_budget}")
        except ValueError as exc:
            print(f"Error: {exc}")
    elif command.name == "tag":
        try:
            action, sequence, label = command.argument.split(maxsplit=2)
            if action == "add":
                runtime.session_service.add_tag(agent.session, int(sequence), label)
            elif action == "remove":
                runtime.session_service.remove_tag(agent.session, int(sequence), label)
            else:
                raise ValueError
            print(f"Tag {action} completed.")
        except (ValueError, HarnessError) as exc:
            print(f"Error: {exc or 'Usage: /tag add|remove <sequence> <label>'}")
    elif command.name == "tags":
        try:
            _print_events(runtime.session_service.tagged_events(agent.session, command.argument))
        except HarnessError as exc:
            print(f"Error: {exc}")
    elif command.name == "export":
        parts = command.argument.split()
        try:
            session = _command_session(runtime, agent, parts[1] if len(parts) > 1 else "")
            if session is not None:
                result = runtime.session_service.export(session, parts[0])
                print(f"Exported: {result.path}")
        except (IndexError, HarnessError) as exc:
            print(f"Error: {exc}")
    elif command.name == "archive":
        try:
            active = command.argument == agent.session.session_id
            info = runtime.session_service.archive(command.argument)
            print(f"Archived session {info.session_id}")
            if active:
                agent = runtime.agent(runtime.new_session())
                print(f"Started session {agent.session.session_id}")
        except HarnessError as exc:
            print(f"Error: {exc}")
    elif command.name == "archives":
        archives = runtime.session_service.list_archives()
        print(
            "\n".join(
                f"{item.session_id}  {item.archived_at}  {item.summary[:50]}" for item in archives
            )
            or "No archives."
        )
    elif command.name == "restore":
        try:
            restored = runtime.session_service.restore(command.argument)
            print(f"Restored session {restored.session_id}")
        except HarnessError as exc:
            print(f"Error: {exc}")
    elif command.name == "session-check":
        parts = command.argument.split()
        try:
            if len(parts) == 2 and parts[0] == "quarantine":
                print(f"Quarantined: {runtime.session_service.quarantine(parts[1])}")
            elif parts:
                print("Usage: /session-check [quarantine <check-id>]")
            else:
                findings = runtime.session_service.scan()
                print(
                    "\n".join(f"{f.check_id}  {f.filename}  {f.reason}" for f in findings)
                    or "Sessions are healthy."
                )
        except HarnessError as exc:
            print(f"Error: {exc}")
    elif command.name == "plugins":
        print(
            "\n".join(f"{p.name}  {p.state}  {', '.join(p.tools)}" for p in runtime.plugin_statuses)
            or "No plugins discovered."
        )
    elif command.name == "tools":
        tools = agent.tool_catalog(command.argument)
        print(
            "\n".join(
                f"{item.name}  [{item.profile}/{item.risk}]  {item.description}" for item in tools
            )
            or "No matching tools."
        )
    elif command.name == "workflows":
        workflows = agent.workflow_catalog(command.argument)
        print(
            "\n".join(f"{item.workflow_id}  {item.title}  {item.description}" for item in workflows)
            or "No matching workflows."
        )
    elif command.name == "workflow":
        parts = command.argument.split()
        try:
            if not parts or parts == ["status"]:
                run = agent.workflow_status()
                pending = agent.session.pending_workflow_override or "auto"
                print(f"Next workflow={pending}")
                if run is None:
                    print("No workflow has run in this session.")
                else:
                    print(
                        f"Last workflow: {run.workflow_id} [{run.status}] "
                        f"confidence={run.confidence:.2f}"
                    )
                    for stage in run.stages:
                        print(f"- [{stage.status}] {stage.description}")
            elif parts == ["auto"]:
                agent.configure_workflow(None)
                print("Next request will use automatic workflow selection.")
            elif len(parts) == 2 and parts[0] == "use":
                agent.configure_workflow(parts[1])
                print(f"Next request will use workflow {parts[1]}.")
            else:
                print("Usage: /workflow [status|auto|use <id>]")
        except HarnessError as exc:
            print(f"Error: {exc}")
    elif command.name == "eval":
        evaluation = runtime.evaluation
        if evaluation is None:
            print("Evaluation is disabled.")
        else:
            parts = command.argument.split()
            try:
                action = parts[0] if parts else "status"
                if action == "status":
                    print(json.dumps(evaluation.status(), indent=2))
                elif action == "contract":
                    request_number = (
                        int(parts[1]) if len(parts) > 1 else agent.next_request_number - 1
                    )
                    contract = evaluation.contract(agent.session.session_id, request_number)
                    print(
                        json.dumps(asdict(contract), indent=2)
                        if contract
                        else "No evaluation contract."
                    )
                elif action == "mark" and len(parts) >= 2:
                    request_number = agent.next_request_number - 1
                    note = " ".join(parts[2:])
                    observation = evaluation.mark(
                        agent.session.session_id,
                        request_number,
                        cast(Literal["pass", "fail"], parts[1]),
                        note,
                    )
                    print(
                        f"Marked request {observation.request_number} as {observation.user_mark}."
                    )
                elif action == "history":
                    limit = int(parts[1]) if len(parts) > 1 else 20
                    for item in evaluation.history(limit):
                        print(
                            f"{item.observation_id} request={item.request_number} "
                            f"outcome={item.score.outcome} tokens="
                            f"{item.score.input_tokens + item.score.output_tokens}"
                        )
                elif action == "compare" and len(parts) == 3:
                    print(asdict(evaluation.compare(parts[1], parts[2])))
                elif action == "run":
                    suite = next((item for item in parts[1:] if not item.startswith("--")), "core")
                    print(asdict(evaluation.run_suite(suite, live="--live" in parts)))
                else:
                    print(
                        "Usage: /eval status|contract [N]|mark pass|fail [note]|"
                        "run [suite] [--live]|history [N]|compare A B"
                    )
            except (HarnessError, ValueError) as exc:
                print(f"Error: {exc}")
    elif command.name == "handoff":
        evaluation = runtime.evaluation
        handoff = evaluation.handoff(agent.session.session_id) if evaluation is not None else None
        print(json.dumps(asdict(handoff), indent=2) if handoff else "No handoff snapshot.")
    elif command.name == "candidate":
        evaluation = runtime.evaluation
        if evaluation is None:
            print("Evaluation is disabled.")
        else:
            parts = command.argument.split(maxsplit=2)
            try:
                if parts and parts[0] == "propose":
                    candidate = evaluation.propose(
                        runtime.model_client, parts[1] if len(parts) > 1 else ""
                    )
                elif len(parts) == 2 and parts[0] == "show":
                    candidate = evaluation.candidate(parts[1])
                elif len(parts) == 2 and parts[0] == "approve":
                    candidate = evaluation.decide_candidate(parts[1], True)
                elif len(parts) >= 2 and parts[0] == "reject":
                    candidate = evaluation.decide_candidate(
                        parts[1], False, parts[2] if len(parts) > 2 else ""
                    )
                else:
                    raise ValueError(
                        "Usage: /candidate propose [component]|show ID|approve ID|"
                        "reject ID [feedback]"
                    )
                print(json.dumps(asdict(candidate), indent=2))
            except (HarnessError, ValueError) as exc:
                print(f"Error: {exc}")
    elif command.name == "plan":
        plan = next(
            (
                item
                for item in reversed(agent.session.plans)
                if item.request_number <= agent.next_request_number - 1
            ),
            None,
        )
        if plan is None:
            print("No task plan in this session.")
        else:
            print(f"Plan: {plan.goal} [{plan.status}]")
            for step in plan.steps:
                suffix = f" — {step.result}" if step.result else ""
                print(f"{step.step_id}. [{step.status}] {step.description}{suffix}")
    elif command.name == "index":
        try:
            if command.argument not in {"", "refresh", "rebuild"}:
                raise ValueError("Usage: /index [refresh|rebuild]")
            status = (
                agent.refresh_project_index(rebuild=command.argument == "rebuild")
                if command.argument
                else agent.project_index_status()
            )
            if status is None:
                print("Project memory is disabled.")
            else:
                print(
                    f"index generation={status.generation}, files={status.files}, "
                    f"symbols={status.symbols}, dependencies={status.dependencies}, "
                    f"mode={status.retrieval_mode}, stale={status.stale}"
                )
                if status.warning:
                    print(f"Warning: {status.warning}")
        except (HarnessError, ValueError) as exc:
            print(f"Error: {exc}")
    elif command.name == "memory":
        try:
            memory_result = agent.query_project_memory(command.argument)
            print(memory_result.rendered)
            if memory_result.warning:
                print(f"Warning: {memory_result.warning}")
        except HarnessError as exc:
            print(f"Error: {exc}")
    elif command.name == "new":
        agent = runtime.agent(runtime.new_session())
        print(f"Started session {agent.session.session_id}")
    elif command.name == "resume":
        session_id = command.argument
        try:
            session = runtime.sessions.load(session_id)
        except HarnessError as exc:
            print(f"Error: {exc}")
            return agent
        if Path(session.workspace).resolve() != runtime.workspace:
            print(f"Warning: session was created in {session.workspace}.")
        agent = runtime.agent(session)
        print(f"Resumed session {session.session_id}")
        _print_events(session.events[-5:])
    return agent


def _print_help() -> None:
    print("/help                 Show this help")
    print("/new                  Start a new saved session")
    print("/sessions             List saved sessions")
    print("/max-turns [N|reset]  Show, set, or reset calls per request")
    print("/models               List configured LiteLLM model aliases")
    print("/model [name|reset]   Show or switch the current session model")
    print("/events [count]        Show saved progress events (default: 20)")
    print("/session-info [id]     Show summary, usage, tags, and metadata")
    print("/summarize [id]        Generate an explicit LLM session summary")
    print("/quota [N|reset]       Show or set advisory session token budget")
    print("/tag add|remove SEQ TAG  Manage event bookmarks")
    print("/tags [label]          Show tagged events")
    print("/export md|csv [id]    Export the full redacted session")
    print("/archive ID            Archive a session")
    print("/archives              List archives")
    print("/restore ID            Restore an archive")
    print("/session-check [...]   Inspect or quarantine corrupt sessions")
    print("/plugins               Show tool plugin status")
    print("/tools [query]         Show the routed tool catalog")
    print("/workflows [query]     List or search situation-based workflows")
    print("/workflow [...]        Show or override the next request workflow")
    print("/eval [...]            Inspect contracts, outcomes, history, and comparisons")
    print("/handoff               Show the latest deterministic request handoff")
    print("/candidate [...]       Propose or decide a controlled harness improvement")
    print("/plan                  Show the latest persisted task plan")
    print("/index [refresh|rebuild]  Show or update workspace project memory")
    print("/memory <query>        Inspect project-memory retrieval without an LLM call")
    print("/resume <session-id>  Resume a saved session")
    print("/exit                 Save and exit")


def _print_sessions(runtime: Runtime) -> None:
    sessions = runtime.sessions.list_sessions()
    if not sessions:
        print("No saved sessions.")
        return
    for session in sessions:
        preview = next(
            (
                message.content or ""
                for message in session.messages
                if message.role == "user" and message.content
            ),
            "(empty)",
        )
        print(f"{session.session_id}  {session.updated_at}  {preview[:50]}")


def _print_events(events: list[ProgressEvent]) -> None:
    if not events:
        print("No progress events.")
        return
    redactor = SecretRedactor()
    for event in events:
        print(format_progress_event(event, redactor))


def _command_session(runtime: Runtime, agent: AgentService, session_id: str) -> Session | None:
    """Resolve an optional command session ID with consistent error reporting."""
    if not session_id:
        return agent.session
    try:
        return runtime.sessions.load(session_id)
    except HarnessError as exc:
        print(f"Error: {exc}")
        return None


def _parse_max_turns(raw_value: str) -> int:
    from local_harness.domain.limits import validate_max_turns

    try:
        value = int(raw_value)
        return validate_max_turns(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


if __name__ == "__main__":
    main()
