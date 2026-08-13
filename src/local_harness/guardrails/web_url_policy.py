"""Pure URL and resolved-address policy for public webpage retrieval."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

from local_harness.domain.errors import PolicyViolation

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})


class PublicWebUrlPolicy:
    """Validate external web URLs and their complete DNS answer sets."""

    def normalize(self, value: str) -> str:
        """Return a canonical fetchable URL or raise a policy violation."""
        if not value.strip():
            raise PolicyViolation("Web URL cannot be empty")
        try:
            parsed = urlsplit(value.strip())
            port = parsed.port
        except ValueError as exc:
            raise PolicyViolation("Web URL is malformed") from exc
        scheme = parsed.scheme.casefold()
        if scheme not in _ALLOWED_SCHEMES:
            raise PolicyViolation("Web URL must use HTTP or HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise PolicyViolation("Web URLs cannot contain credentials")
        if parsed.hostname is None:
            raise PolicyViolation("Web URL must contain a hostname")
        if port is not None and port not in _ALLOWED_PORTS:
            raise PolicyViolation("Web URL ports are restricted to 80 and 443")
        try:
            hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise PolicyViolation("Web URL hostname is invalid") from exc
        if not hostname:
            raise PolicyViolation("Web URL must contain a hostname")
        netloc = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None and port != _default_port(scheme):
            netloc = f"{netloc}:{port}"
        path = parsed.path or "/"
        return urlunsplit(SplitResult(scheme, netloc, path, parsed.query, ""))

    def validate_addresses(self, addresses: Sequence[str]) -> None:
        """Reject empty, malformed, mixed, or non-public DNS answer sets."""
        if not addresses:
            raise PolicyViolation("Web hostname did not resolve")
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as exc:
                raise PolicyViolation("Web hostname resolved to an invalid address") from exc
            mapped = getattr(address, "ipv4_mapped", None)
            if not address.is_global or (mapped is not None and not mapped.is_global):
                raise PolicyViolation("Web URL resolves to a non-public network address")


def _default_port(scheme: str) -> int:
    return 80 if scheme == "http" else 443
