"""Defense-in-depth redaction for keyring exception messages and probe reasons.

Owns the ``_redact_sensitive`` concern: strips filesystem paths and
API-key-like substrings from text before it is logged or surfaced to
the renderer via :func:`voice_typer.server.credential_store.get_keyring_status`.
Also truncates to :data:`_REASON_MAX_LEN` chars so a verbose backend
error can't flood the renderer tooltip or the log file.
"""

from __future__ import annotations

import re

from voice_typer.server._secrets import redact_secret

from ._schema import _REASON_MAX_LEN

#: Matches ``/home/<user>``, ``/Users/<user>``, ``~/<path>``, ``C:\\Users\\<user>``.
#: Common in keyring backend error messages (libsecret D-Bus errors
#: referencing the session bus path, pyobjc errors referencing the
#: keychain file). The user's home directory is private metadata —
#: redact it before exposing via IPC.
_PATH_RE = re.compile(
    r"(?:/home/[^/\s]+|/Users/[^/\s]+|~[/][^/\s]+|C:\\Users\\[^\\\s]+)",
    re.IGNORECASE,
)


def _redact_sensitive(text: str | None) -> str | None:
    """Redact filesystem paths and API-key-like substrings from ``text``.

    Used as defense in depth on keyring exception messages and probe
    reasons before they're logged or returned via
    :func:`voice_typer.server.credential_store.get_keyring_status`. Also
    truncates to :data:`_REASON_MAX_LEN` chars so a verbose backend error
    can't flood the renderer tooltip or the log file.

    Returns ``None`` unchanged (so callers can pass through optional
    values without a separate None check).

    API-key redaction delegates to
    :func:`voice_typer.server._secrets.redact_secret` (the canonical
    helper) with ``aggressive=True``. ``redact_secret`` applies BOTH
    the SEC-9 flag / ``key=value`` patterns AND the API-key patterns
    (Bearer / Token / ``sk-`` / 20+ char alphanumeric run). The
    ``aggressive=True`` opt-in bypasses the ``_MIN_REDACT_LEN``
    short-string guard so bare short secrets are still caught.

    The canonical ``redact_secret`` helper uses ``"***"`` as its
    redaction marker; this helper normalizes it to ``"[redacted]"``
    (the IPC-bound marker convention used elsewhere in this package
    and pinned by existing tests) so callers see a consistent marker
    regardless of which underlying helper ran.
    """
    if not text:
        return text
    s = str(text)
    s = _PATH_RE.sub("[path]", s)
    s = redact_secret(s, aggressive=True).replace("***", "[redacted]")
    if len(s) > _REASON_MAX_LEN:
        s = s[: _REASON_MAX_LEN - 3] + "..."
    return s


__all__ = ["_PATH_RE", "_redact_sensitive"]
