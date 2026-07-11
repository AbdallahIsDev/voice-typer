"""Shared security helpers for cloud-style HTTP clients.

RELIABILITY-004 / SEC-002 follow-up: centralized helpers for
- redacting API keys from log messages and exception strings
- asserting that a caller-supplied URL matches an allowlist of trusted
  cloud providers (defense against SEC-002 endpoint-swap attacks)

These helpers are intentionally framework-agnostic (no requests, no
httpx) so they can be used from ``cloud_engines.py``, ``llm_polish.py``,
and any future HTTP client without coupling.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ── API key redaction ─────────────────────────────────────────────────────
#
# A "redactable" secret is any string that looks like an API key or
# bearer token.  We match conservatively: the patterns below cover
# the common provider prefixes (sk-, sk-or-, sk-proj-, Bearer, Token)
# and generic long hex/base64 strings that are at least 32 chars long.
# Short strings (<20) are not redacted to avoid mangling ordinary
# words in exception messages.

# Order matters: more-specific patterns (with a captured prefix like
# "Bearer " or "sk-") are applied first, so the prefix is preserved
# in the output.  The generic 32+ char alphanumeric pattern is applied
# last as a catch-all.
_KEY_PATTERNS = [
    # Authorization headers (case-insensitive) — keep "Bearer " / "Token "
    # prefix in output, redact the rest.
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.=]+", re.IGNORECASE),
    re.compile(r"(Token\s+)[A-Za-z0-9_\-\.=]+", re.IGNORECASE),
    # OpenAI-style keys: sk- followed by 8+ word chars.  Replace the
    # entire run (sk- and everything after that's still word-char).
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    # Generic long alphanumeric run (>= 32 chars).  This catches
    # bare hex/base64 keys that don't match a known prefix.  Uses
    # \b to avoid partial-word matches inside longer words.
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
]

# Minimum length below which we don't bother redacting — too likely
# to be an ordinary word.
_MIN_REDACT_LEN = 20


def redact_secret(value: object) -> str:
    """Redact API keys and bearer tokens from a value.

    Parameters
    ----------
    value : object
        Any value; non-strings are stringified via ``str(value)``.

    Returns
    -------
    str
        The value with likely-secret substrings replaced by
        ``"<prefix>***"`` (for prefixed patterns like ``Bearer``) or
        ``"***"`` (for bare keys).  Short strings (under
        ``_MIN_REDACT_LEN`` characters and not matching any prefix
        pattern) are returned unchanged so ordinary error messages
        aren't mangled.

    Notes
    -----
    This is a best-effort heuristic.  It will not catch every possible
    secret format, and it may occasionally redact a non-secret that
    happens to look like one.  The goal is to make log-grepping for
    leaked keys reliable, not to provide cryptographic guarantees.
    """
    if value is None:
        return "None"
    if not isinstance(value, str):
        value = str(value)
    if len(value) < _MIN_REDACT_LEN:
        return value
    redacted = value
    for pat in _KEY_PATTERNS:
        def _sub(m: re.Match[str]) -> str:
            if m.lastindex:
                # Pattern has a prefix group (e.g. "Bearer ").  Keep
                # the prefix, redact the rest.
                return m.group(1) + "***"
            # No prefix group — redact the whole match.
            return "***"
        redacted = pat.sub(_sub, redacted)
    return redacted


def redact_url(url: str) -> str:
    """Redact userinfo (user:pass@) from a URL.

    Leaves the scheme, host, port, and path intact so the URL remains
    useful for debugging, but strips any embedded credentials.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return url
    if not parsed.username and not parsed.password:
        return url
    # Reconstruct without userinfo
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc).geturl()


# ── Cloud URL allowlist ───────────────────────────────────────────────────
#
# Default allowlist of trusted cloud ASR / LLM provider hostnames.
# When a user sets a custom ``cloud_api_url`` or ``llm_api_url``, the
# HTTP client asserts the URL's hostname is in this allowlist (or in
# an explicit user-extended allowlist) before sending any data.
#
# To extend the allowlist at runtime (e.g. for a self-hosted vLLM
# endpoint), call ``extend_url_allowlist(["my-host.example.com"])``.
# Extensions are process-global and apply to all HTTP clients.

_DEFAULT_ALLOWED_HOSTS = frozenset({
    # OpenAI
    "api.openai.com",
    # Groq
    "api.groq.com",
    # Deepgram
    "api.deepgram.com",
    # Anthropic (Claude) — common LLM polish target
    "api.anthropic.com",
    # Google Gemini / Vertex
    "generativelanguage.googleapis.com",
    # Local self-hosted endpoints — explicitly allowed for development
    "localhost",
    "127.0.0.1",
    "::1",
})

_user_extensions: set[str] = set()


def extend_url_allowlist(hosts: Iterable[str]) -> None:
    """Add hostnames to the runtime URL allowlist.

    Hostnames are normalized to lowercase and stripped of port.
    Duplicate additions are idempotent.
    """
    for h in hosts:
        if not h:
            continue
        # Strip port if present
        host = h.split(":")[0].strip().lower()
        if host:
            _user_extensions.add(host)


def get_url_allowlist() -> frozenset[str]:
    """Return the current effective allowlist (defaults + user extensions)."""
    return _DEFAULT_ALLOWED_HOSTS | _user_extensions


def is_url_allowed(url: str) -> bool:
    """Return True if the URL's host is in the allowlist.

    Empty URLs are considered allowed (they fail later with a clearer
    error from the HTTP layer).  URLs with no hostname (e.g.
    ``javascript:alert(1)``) are rejected.
    """
    if not url:
        return True
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host in get_url_allowlist()


def assert_url_allowed(
    url: str,
    *,
    field_name: str = "url",
    client_name: str = "client",
    require_https: bool = True,
) -> None:
    """Raise ``ValueError`` if ``url`` is not in the allowlist.

    Parameters
    ----------
    url : str
        The URL to check.
    field_name : str
        The config field name (for the error message).
    client_name : str
        The client name (for the error message).
    require_https : bool
        NEW-SEC-003: when True (default), non-loopback hosts must use
        HTTPS. Loopback hosts (localhost, 127.0.0.1, ::1) are exempt
        so local development servers can use HTTP. This prevents a
        loopback IPC attacker from exfiltrating transcribed text to
        ``http://attacker.example.com/steal`` even if the attacker
        somehow adds the host to the allowlist.

    Raises
    ------
    ValueError
        If the URL's scheme is not http/https or its host is not in
        the allowlist.  The error message does NOT include the URL
        itself, to avoid leaking a potentially-malicious URL into logs.
    """
    if not url:
        raise ValueError(f"{client_name}: {field_name} is empty")

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{client_name}: {field_name} is not a valid URL: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"{client_name}: {field_name} must use http or https scheme "
            f"(got {parsed.scheme!r})"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"{client_name}: {field_name} has no hostname")
    if host not in get_url_allowlist():
        raise ValueError(
            f"{client_name}: {field_name} host {host!r} is not in the "
            f"trusted allowlist.  Call extend_url_allowlist() to add it."
        )
    # NEW-SEC-003: enforce HTTPS for non-loopback hosts to prevent
    # cleartext exfiltration of transcribed text + API keys.
    _loopback_hosts = frozenset({"localhost", "127.0.0.1", "::1"})
    if require_https and parsed.scheme == "http" and host not in _loopback_hosts:
        raise ValueError(
            f"{client_name}: {field_name} must use HTTPS for non-loopback "
            f"host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1 "
            f"for local development). Cleartext transmission of API keys "
            f"and transcribed text over the public internet is not permitted."
        )
