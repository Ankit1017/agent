"""Complete branch tests for the public-web URL and address policy."""

from __future__ import annotations

import pytest

from local_harness.domain.errors import PolicyViolation
from local_harness.guardrails.web_url_policy import PublicWebUrlPolicy


def test_web_url_policy_normalizes_safe_urls() -> None:
    """Public HTTP(S) syntax is canonicalized without fragments or default ports."""
    policy = PublicWebUrlPolicy()

    assert policy.normalize(" HTTPS://Example.COM:443/docs?q=1#part ") == (
        "https://example.com/docs?q=1"
    )
    assert policy.normalize("http://example.com") == "http://example.com/"
    assert policy.normalize("https://example.com:80/path") == "https://example.com:80/path"
    assert policy.normalize("https://[2606:4700:4700::1111]/") == (
        "https://[2606:4700:4700::1111]/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp/a",
        "https://user:pass@example.com/",
        "https:///missing",
        "https://example.com:8080/",
        "https://example.com:bad/",
        "https://./",
        "https://\ud800.example/",
    ],
)
def test_web_url_policy_rejects_unsafe_syntax(url: str) -> None:
    """Malformed schemes, hosts, credentials, and ports are rejected."""
    with pytest.raises(PolicyViolation):
        PublicWebUrlPolicy().normalize(url)


def test_web_url_policy_accepts_only_complete_public_dns_answers() -> None:
    """Every resolved address must be valid and globally routable."""
    policy = PublicWebUrlPolicy()
    policy.validate_addresses(["8.8.8.8", "2606:4700:4700::1111"])

    for addresses in (
        [],
        ["not-an-ip"],
        ["127.0.0.1"],
        ["8.8.8.8", "10.0.0.1"],
        ["::ffff:127.0.0.1"],
        ["169.254.169.254"],
    ):
        with pytest.raises(PolicyViolation):
            policy.validate_addresses(addresses)
