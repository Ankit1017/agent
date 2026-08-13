"""Focused text bounding helpers."""


def truncate_text(value: str, limit: int) -> tuple[str, bool]:
    """Truncate text to a character limit and report whether truncation occurred."""
    if len(value) <= limit:
        return value, False
    suffix = "\n...[output truncated by harness]"
    return value[: max(0, limit - len(suffix))] + suffix, True
