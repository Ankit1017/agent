"""Command-line entry point for deterministic and opt-in live harness evaluation."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from local_harness.bootstrap import build_runtime
from local_harness.domain.errors import HarnessError


def main(argv: list[str] | None = None) -> None:
    """Run or compare workspace-local harness evaluation suites."""
    parser = argparse.ArgumentParser(description="Local Harness evaluation runner")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--suite", default="core")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "CANDIDATE"))
    args = parser.parse_args(argv)
    try:
        runtime = build_runtime(args.workspace)
        if runtime.evaluation is None:
            raise HarnessError("Evaluation is disabled")
        value = (
            runtime.evaluation.compare(*args.compare)
            if args.compare
            else runtime.evaluation.run_suite(args.suite, live=args.live)
        )
        print(json.dumps(asdict(value), indent=2))
    except HarnessError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
