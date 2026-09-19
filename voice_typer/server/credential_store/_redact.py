"""Defense-in-depth redaction for keyring exception messages and probe reasons."""

from __future__ import annotations

import re

from voice_typer.server._secrets import redact_secret

from ._schema import _REASON_MAX_LEN

#: Matches ``/home/<user>``, ``/Users/<user>``, ``~/<path>``, ``C:\\Users\\<user>``.
_PATH_RE = re.compile(
    r"(?:/home/[^/\s]+|/Users/[^/\s]+|~[/][^/\s]+|C:\\Users\\[^\\\s]+)",
    re.IGNORECASE,
)


def _redact_sensitive(text: str | None) -> str | None:
    """the SEC-9 flag / ``key=value`` patterns AND the API-key patterns"""
    if not text:
        return text
    s = str(text)
    s = _PATH_RE.sub("[path]", s)
    s = redact_secret(s, aggressive=True).replace("***", "[redacted]")
    if len(s) > _REASON_MAX_LEN:
        s = s[: _REASON_MAX_LEN - 3] + "..."
    return s


__all__ = ["_PATH_RE", "_redact_sensitive"]
