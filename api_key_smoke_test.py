"""Standalone smoke test for OpenAI and Gemini API credentials.

The script reads credentials from environment variables or hidden terminal
prompts. It never writes credentials to disk and redacts them from API errors.
It uses only the Python standard library and does not import this project.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

Provider = Literal["OpenAI", "Gemini"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Summarize one provider credential check without retaining the key."""

    provider: Provider
    succeeded: bool
    detail: str


def check_openai(api_key: str, model: str, timeout_seconds: float) -> CheckResult:
    """Check an OpenAI key and model with one minimal Responses API request."""
    payload: dict[str, object] = {
        "model": model,
        "input": "Reply with exactly OK.",
        "max_output_tokens": 16,
    }
    return _post_json(
        provider="OpenAI",
        url="https://api.openai.com/v1/responses",
        api_key=api_key,
        headers={"Authorization": f"Bearer {api_key}"},
        payload=payload,
        timeout_seconds=timeout_seconds,
    )


def check_gemini(api_key: str, model: str, timeout_seconds: float) -> CheckResult:
    """Check a Gemini key and model with one minimal generateContent request."""
    payload: dict[str, object] = {
        "contents": [{"parts": [{"text": "Reply with exactly OK."}]}],
        "generationConfig": {"maxOutputTokens": 16},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    return _post_json(
        provider="Gemini",
        url=url,
        api_key=api_key,
        headers={"x-goog-api-key": api_key},
        payload=payload,
        timeout_seconds=timeout_seconds,
    )


def _post_json(
    *,
    provider: Provider,
    url: str,
    api_key: str,
    headers: dict[str, str],
    payload: dict[str, object],
    timeout_seconds: float,
) -> CheckResult:
    encoded = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read(8_192)
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read(8_192).decode("utf-8", errors="replace")
        detail = _http_failure_detail(exc.code, _error_message(body), api_key)
        return CheckResult(provider, False, detail)
    except urllib.error.URLError as exc:
        detail = _redact(f"network error: {exc.reason}", api_key)
        return CheckResult(provider, False, detail)
    except TimeoutError:
        return CheckResult(provider, False, "request timed out")

    if 200 <= status < 300:
        return CheckResult(provider, True, f"working (HTTP {status}; model access confirmed)")
    return CheckResult(provider, False, f"unexpected HTTP status {status}")


def _error_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "API returned a non-JSON error"
    if not isinstance(payload, dict):
        return "API returned an unexpected error"
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    if isinstance(error, str):
        return error
    return "API did not provide an error message"


def _http_failure_detail(status: int, message: str, api_key: str) -> str:
    labels = {
        400: "request rejected",
        401: "authentication failed; key is invalid, expired, or revoked",
        403: "permission denied; check project and model access",
        404: "endpoint or model was not found",
        429: "quota or rate limit reached; authentication is not fully verified",
    }
    label = labels.get(status, "API request failed")
    safe_message = _redact(message, api_key).replace("\r", " ").replace("\n", " ")[:500]
    return f"{label} (HTTP {status}): {safe_message}"


def _redact(value: str, api_key: str) -> str:
    return value.replace(api_key, "[REDACTED]") if api_key else value


def _credential(variable: str, provider: str) -> str:
    value = os.environ.get(variable, "").strip()
    if not value:
        value = getpass.getpass(f"Enter the {provider} API key (hidden): ").strip()
    if not value:
        raise ValueError(f"No {provider} API key was provided")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely smoke-test OpenAI and Gemini API keys without project dependencies."
    )
    parser.add_argument(
        "--provider",
        choices=("both", "openai", "gemini"),
        default="both",
        help="provider to test (default: both)",
    )
    parser.add_argument("--openai-model", default="gpt-5.5")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash-lite")
    parser.add_argument("--timeout", type=float, default=30.0, help="request timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run selected credential checks and return a process-friendly status."""
    args = _parser().parse_args(argv)
    if args.timeout <= 0:
        print("Error: --timeout must be greater than zero", file=sys.stderr)
        return 2

    try:
        results: list[CheckResult] = []
        if args.provider in {"both", "openai"}:
            results.append(
                check_openai(
                    _credential("OPENAI_API_KEY", "OpenAI"),
                    args.openai_model,
                    args.timeout,
                )
            )
        if args.provider in {"both", "gemini"}:
            results.append(
                check_gemini(
                    _credential("GEMINI_API_KEY", "Gemini"),
                    args.gemini_model,
                    args.timeout,
                )
            )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    for result in results:
        marker = "PASS" if result.succeeded else "FAIL"
        print(f"{result.provider}: {marker} - {result.detail}")
    return 0 if all(result.succeeded for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
