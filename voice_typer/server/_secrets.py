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

# SEC-9: explicit flag / key=value forms for secret-bearing keywords.
#
# These patterns are deliberately more specific than the catch-all
# 32+ char pattern: they require an explicit secret-bearing keyword
# (``token``, ``key``, ``secret``, ``password``, etc.) so they fire
# on short values that the generic pattern would miss (e.g.
# ``--token=abc`` is 12 chars — well under the 32-char generic
# threshold — but is unambiguously a secret-bearing flag).
#
# Covers three forms:
#   1. ``--token=abc123``  (long flag, ``=`` delimiter)
#   2. ``--token abc123``  (long flag, space delimiter)
#   3. ``token=abc123``    (bare ``key=value``, e.g. env vars /
#      config files / URL query params)
#
# Single-letter short flags (``-t abc123``) are deliberately NOT
# matched — too ambiguous (could be any of dozens of CLI options
# that happen to share a letter with a secret flag).
_SECRET_KEYWORDS = (
    "token",
    "apikey",
    "api_key",
    "api-key",
    "secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "authorization",
    "authentication",
    "access_token",
    "access-token",
    "refreshtoken",
    "refresh_token",
    "refresh-token",
    "client_secret",
    "client-secret",
    "private_key",
    "private-key",
    # Bare ``key=`` is included last in the alternation so longer
    # keywords (``api_key=``) win when present — Python's ``re``
    # alternation is leftmost-greedy, so we order from most-specific
    # to least-specific. ``\b`` prevents matching inside larger
    # words like ``monkey=`` or ``hotkey=``.
    "key",
)
_KEYWORD_ALT = "|".join(re.escape(k) for k in _SECRET_KEYWORDS)

# Pattern A: long-flag form. Captures the prefix (``--token=`` or
# ``--token ``) in group 1 and the secret value in group 2. The
# replacement keeps the prefix and redacts the value.
#
# SEC-9 fix (PIR-SEC-1): the ``--`` prefix and the ``(?:=|\s+)``
# delimiter must be OUTSIDE the keyword alternation so they apply to
# EVERY alternative, not just the last one. The previous form
# ``(--token|apikey|...|key(?:=|\s+))`` parsed as an alternation
# where only the LAST branch (``key``) carried the delimiter, so
# ``--token=abc`` failed to match (the ``=`` was consumed by the
# ``[^\s=]+`` group's negation, leaving no value to capture), and
# bare keywords like ``password`` matched as a prefix without any
# delimiter at all, greedily consuming the rest of the line as the
# "value" (e.g. ``password@host:port`` → ``password***`` instead of
# being left alone for the URL-redaction pass).
_FLAG_VALUE_PATTERN = re.compile(rf"(?i)(--(?:{_KEYWORD_ALT})(?:=|\s+))([^\s=]+)")

# Pattern B: bare ``key=value`` form (no ``--`` prefix). Captures the
# prefix (``token=``) in group 1 and the value in group 2. ``\b``
# ensures the keyword isn't part of a larger word (e.g. ``monkey=``
# does NOT match ``key=``).
#
# SEC-9 fix (PIR-SEC-1): the ``=`` must be INSIDE capture group 1 so
# the ``_flag_sub`` replacement preserves it in the output
# (``password=hunter2`` → ``password=***``, not ``password***``).
# The previous form ``\b({_KEYWORD_ALT})=([^\s=]+)`` left the ``=``
# outside the group, so the replacement dropped it — every test
# asserting ``password=***`` / ``--token=***`` / etc. failed.
#
# The keyword alternation is wrapped in a non-capturing group
# ``(?:{_KEYWORD_ALT})`` BEFORE the ``=`` so the ``=`` applies to
# EVERY alternative, not just the last one. Without the inner
# non-capturing group, Python's regex engine parses
# ``(token|...|key=)`` as an alternation where only the LAST branch
# (``key``) carries the ``=`` — leaving ``token``, ``password``,
# etc. as bare keyword matches (with no delimiter constraint) that
# greedily consume the rest of the line as the "value"
# (e.g. ``secret-value`` matched as ``secret`` + ``-value``
# instead of ``token=abc123-secret-value`` matched as
# ``token=`` + ``abc123-secret-value``).
_BARE_KEY_VALUE_PATTERN = re.compile(rf"(?i)\b((?:{_KEYWORD_ALT})=)([^\s=]+)")

# Ordered list: pattern A (flag form) runs before pattern B (bare
# form) so a value redacted by A isn't re-matched by B on the
# resulting ``***`` (harmless if it does, but this avoids needless
# regex work).
_FLAG_KEY_PATTERNS = [_FLAG_VALUE_PATTERN, _BARE_KEY_VALUE_PATTERN]


def _flag_sub(m: re.Match[str]) -> str:
    """SEC-9 replacement for flag / key=value patterns.

    Keeps the prefix (group 1, e.g. ``--token=`` or ``token=``) and
    redacts the value (group 2) to ``***``.
    """
    return m.group(1) + "***"


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

    SEC-9: explicit flag / key=value forms (``--token=abc``,
    ``--token abc``, ``token=abc``) are matched BEFORE the
    ``_MIN_REDACT_LEN`` short-string guard because the keyword
    constraint makes them specific enough to be safe on short inputs.
    """
    if value is None:
        return "None"
    if not isinstance(value, str):
        value = str(value)
    # SEC-9: apply the specific flag / key=value patterns first so
    # they fire even on short inputs (e.g. ``--token=abc`` is 12
    # chars but unambiguously a secret-bearing flag).
    redacted = value
    for pat in _FLAG_KEY_PATTERNS:
        redacted = pat.sub(_flag_sub, redacted)
    # Early-exit for short strings: skip the more-generic patterns
    # that could false-positive on ordinary short text. The flag
    # patterns above are specific enough to have already run.
    if len(value) < _MIN_REDACT_LEN:
        return redacted
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

_DEFAULT_ALLOWED_HOSTS = frozenset(
    {
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
    }
)

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
        raise ValueError(f"{client_name}: {field_name} must use http or https scheme (got {parsed.scheme!r})")
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
