"""Deterministic final-answer normalization and quality checks."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from local_harness.domain.models import (
    AnswerQualityAssessment,
    AnswerQualityIssue,
    CompletionEvidence,
    Message,
)

_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RAW_HTML = re.compile(
    r"</?(?:script|style|iframe|object|embed|img|div|span|table|tr|td|th|p|br)\b[^>]*>",
    re.IGNORECASE,
)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((https?://[^\s)]+)(?:\s+['\"][^)]*['\"])?\)")
_FENCE = re.compile(r"^\s*```", re.MULTILINE)


def normalize_assistant_markdown(value: str) -> str:
    """Normalize safe Markdown without changing code-fence contents."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    in_fence = False
    for line in normalized.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append(line.rstrip())
            continue
        if not in_fence and _BREAK.search(line):
            if "|" in line:
                line = _BREAK.sub("; ", line)
            else:
                line = _BREAK.sub("\n\n", line)
        output.extend(part.rstrip() for part in line.split("\n"))
    compacted = re.sub(r"\n{4,}", "\n\n\n", "\n".join(output))
    return compacted.strip()


class AnswerQualityPolicy:
    """Check answer structure and citations using only observable tool results."""

    def assess(
        self,
        content: str,
        messages: Sequence[Message],
        request_number: int,
        evidence: CompletionEvidence | None = None,
    ) -> AnswerQualityAssessment:
        """Return deterministic issues for one proposed final answer."""
        issues: list[AnswerQualityIssue] = []
        normalized = normalize_assistant_markdown(content)
        if len(_FENCE.findall(normalized)) % 2:
            issues.append(AnswerQualityIssue("unclosed_fence", "Close every fenced code block."))
        if _RAW_HTML.search(content):
            issues.append(AnswerQualityIssue("raw_html", "Use Markdown instead of raw HTML."))
        if evidence is not None and _claims_successful_check(normalized):
            successful = any(
                check.casefold().endswith(("completed", "passed", "success"))
                for check in evidence.checks
            )
            if not successful:
                issues.append(
                    AnswerQualityIssue(
                        "unsupported_check_claim",
                        "Do not claim checks passed unless completion evidence records success.",
                    )
                )

        read_urls, search_urls = _web_source_urls(messages, request_number)
        allowed = read_urls or search_urls
        if allowed:
            cited = set(_MARKDOWN_LINK.findall(normalized))
            unknown = sorted(cited - set(allowed))
            if unknown:
                issues.append(
                    AnswerQualityIssue(
                        "unknown_citation",
                        "Use only exact URLs returned by the web tools.",
                    )
                )
            if read_urls:
                missing = sorted(set(read_urls) - cited)
                if missing:
                    issues.append(
                        AnswerQualityIssue(
                            "missing_citations",
                            "Cite every successfully read source with a Markdown link.",
                        )
                    )
            elif not cited.intersection(search_urls):
                issues.append(
                    AnswerQualityIssue(
                        "missing_citation",
                        "Cite at least one exact returned search URL.",
                    )
                )
        return AnswerQualityAssessment(tuple(issues), tuple(allowed))

    def correction_instruction(self, assessment: AnswerQualityAssessment) -> str:
        """Build a bounded correction request without asking for private reasoning."""
        problems = " ".join(issue.message for issue in assessment.issues)
        urls = "\n".join(f"- {url}" for url in assessment.source_urls)
        source_block = f"\nAllowed source URLs:\n{urls}" if urls else ""
        return (
            "Rewrite your previous proposed final answer only. Preserve its useful facts, but fix "
            f"these observable presentation issues: {problems}{source_block}\n"
            "Use concise GitHub Markdown, no raw HTML, and begin with the required step_summary."
        )[:6_000]


def _web_source_urls(
    messages: Sequence[Message], request_number: int
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    read: list[str] = []
    search: list[str] = []
    for message in messages:
        if message.role != "tool" or message.request_number != request_number:
            continue
        if message.name not in {"web_search", "read_web_pages"}:
            continue
        try:
            payload = json.loads(message.content or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            key = "final_url" if message.name == "read_web_pages" else "url"
            url = item.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                target = read if message.name == "read_web_pages" else search
                if url not in target:
                    target.append(url)
    return tuple(read), tuple(search)


def _claims_successful_check(value: str) -> bool:
    return bool(
        re.search(
            r"\b(?:all\s+)?(?:tests?|lint|type[ -]?checks?|build)\s+(?:have\s+)?passed\b",
            value,
            re.IGNORECASE,
        )
    )
