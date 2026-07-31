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

import inspect
import ipaddress
import logging
import os
import re
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# ── API key redaction ─────────────────────────────────────────────────────
#
# A "redactable" secret is any string that looks like an API key or
# bearer token.  We match conservatively: the patterns below cover
# the common provider prefixes (sk-, sk-or-, sk-proj-, Bearer, Token)
# and generic long hex/base64 strings that are at least 20 chars long.
# Short strings (<20) are not redacted to avoid mangling ordinary
# words in exception messages.
#
# the generic threshold was lowered from 32 to 20 to match
# ``_MIN_REDACT_LEN``. See the comment on ``_KEY_PATTERNS[-1]`` below
# for the rationale.

# Order matters: more-specific patterns (with a captured prefix like
# "Bearer " or "sk-") are applied first, so the prefix is preserved
# in the output.  The generic 20+ char alphanumeric pattern is applied
# last as a catch-all.
_KEY_PATTERNS = [
    # Authorization headers (case-insensitive) — keep "Bearer " / "Token "
    # prefix in output, redact the rest.
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.=]+", re.IGNORECASE),
    re.compile(r"(Token\s+)[A-Za-z0-9_\-\.=]+", re.IGNORECASE),
    # OpenAI-style keys: sk- followed by 8+ word chars.  Replace the
    # entire run (sk- and everything after that's still word-char).
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    # Groq-style keys: gsk- followed by 8+ word chars.  Added back
    # ( reviewer feedback) so short 12-19 char gsk_ values are
    # redacted even when they don't reach the 20-char generic threshold.
    re.compile(r"gsk_[A-Za-z0-9_\-]+"),
    # Generic long alphanumeric run (>= 20 chars).  This catches
    # bare hex/base64 keys that don't match a known prefix.  Uses
    # \b to avoid partial-word matches inside longer words.
    #
    # threshold lowered from 32 to 20 to match
    # ``_MIN_REDACT_LEN``. Pre-fix, a 20-31 char bare token (e.g. a
    # 24-char GitLab PAT, a 20-char GitHub PAT, a 24-char Slack
    # legacy token) fell through the generic pattern AND was already
    # past the 20-char ``_MIN_REDACT_LEN`` early-exit guard — so it
    # was returned UNREDACTED. Aligning the regex threshold with the
    # length guard closes the gap: any bare alphanumeric run long
    # enough to plausibly be a secret (>= 20 chars) is now redacted.
    #
    # (High): the negative lookbehind/lookahead on ``/`` and ``\\``
    # prevents false-positive redaction of 20+ char filesystem path
    # components (e.g. POSIX usernames like ``username_with_long_name``,
    # pytest tmp_path components like ``test_banner_includes_file_path0``,
    # macOS app-support subdirs). A run flanked by a path delimiter is
    # semantically a path component, not a bare secret.
    re.compile(r"(?<![/\\])\b[A-Za-z0-9_\-]{20,}\b(?![/\\])"),
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


def redact_secret(value: object, *, aggressive: bool = False) -> str:
    """Redact API keys and bearer tokens from a value.

        Parameters
        ----------
        value : object
            Any value; non-strings are stringified via ``str(value)``.
        aggressive : bool, default False
    when True, BYPASS the ``_MIN_REDACT_LEN`` short-string
            guard so bare short secrets (e.g. a 12-char bare API key with
            no ``Bearer``/``Token``/``--token=`` prefix) are still passed
            through :func:`redact_api_keys`. Use this only in contexts
            where short bare secrets are plausible (e.g. the crash
            excepthook path that dumps arbitrary object repr() into the
            crash marker, or an env-var audit). Default False so ordinary
            log lines retain the short-string guard against false positives
            on ordinary words.

        Returns
        -------
        str
            The value with likely-secret substrings replaced by
            ``"<prefix>***"`` (for prefixed patterns like ``Bearer``) or
            ``"***"`` (for bare keys).  Short strings (under
            ``_MIN_REDACT_LEN`` characters and not matching any prefix
            pattern) are returned unchanged so ordinary error messages
            aren't mangled — UNLESS ``aggressive=True`` is passed, in which
            case the short-string guard is skipped.

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

    known gap: a BARE short secret (e.g. a 12-char bare API
        key with no keyword prefix) is NOT redacted when
        ``aggressive=False``. The ``_MIN_REDACT_LEN`` guard (default 20)
        skips generic-pattern application on short strings to avoid
        false-positives on ordinary words (e.g. ``"helloworld"`` would
        match the 20+ char alphanumeric run pattern but isn't a secret).
        Callers in security-critical contexts where bare short secrets are
        plausible SHOULD pass ``aggressive=True`` to bypass the length
        guard. The crash-excepthook path and env-var audit are the two
        known callers that benefit from this opt-in.
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
    #
    # the ``aggressive`` opt-in bypasses this guard for
    # contexts where bare short secrets are plausible (e.g. the crash
    # excepthook path that dumps arbitrary object repr()). When
    # ``aggressive=True``, fall through to ``redact_api_keys`` even
    # for short inputs.
    if not aggressive and len(value) < _MIN_REDACT_LEN:
        return redacted
    # delegate the API-key-pattern application to the shared
    # ``redact_api_keys`` helper so the canonical "what an API-key-like
    # substring looks like" knowledge lives in exactly one place
    # (``_KEY_PATTERNS`` above). ``credential_store._redact_sensitive``
    # also calls ``redact_api_keys`` (with a different replacement
    # string) for its IPC-bound keyring-exception redaction.
    return redact_api_keys(redacted)


def redact_api_keys(text: str, *, replacement: str = "***") -> str:
    """Redact API keys and bearer tokens from ``text`` (configurable marker).

        This is the **canonical API-key redaction helper** for the codebase.
        It applies :data:`_KEY_PATTERNS` (Bearer / Token / ``sk-`` / generic
        20+ char alphanumeric run) to ``text`` and substitutes each match
        with ``replacement``. Patterns that capture a prefix group
        (``Bearer `` / ``Token ``) preserve the prefix; the secret portion
        is replaced. Patterns without a prefix group replace the whole
        match.

    (DRY consolidation): prior to this helper, the API-key
        pattern knowledge was duplicated between this module
        (:data:`_KEY_PATTERNS`) and ``credential_store._API_KEY_RE``
        (a separate single-regex with different thresholds). The two
        representations drifted — the credential_store version missed
        ``Bearer`` / ``Token`` auth and required 32+ chars for the generic
        catch-all, while this module's version matched 20+ chars and
        recognized the auth-header prefixes. ``redact_api_keys`` is now the
        single source of truth: :func:`redact_secret` (log-message
        redaction, default ``"***"``) and
        ``credential_store._redact_sensitive`` (IPC-bound keyring-exception
        redaction, ``"[redacted]"``) both call it.

        Parameters
        ----------
        text : str
            The text to redact. Must already be a string — callers
            converting from ``object`` should call ``str(value)`` first,
            or use :func:`redact_secret` which does that automatically.
        replacement : str
            The substring to substitute for each redacted secret. Defaults
            to ``"***"`` (the conventional log-redaction marker used by
            :func:`redact_secret`). Use ``"[redacted]"`` for IPC-bound
            messages that the renderer surfaces to the user (matching the
            convention used by ``credential_store._redact_sensitive``).

        Returns
        -------
        str
            ``text`` with every match from :data:`_KEY_PATTERNS` replaced
            by ``replacement`` (or ``prefix + replacement`` for prefix
            patterns).

        Notes
        -----
        Unlike :func:`redact_secret`, this helper:

        - Does **not** apply the SEC-9 flag / ``key=value`` patterns
          (:data:`_FLAG_KEY_PATTERNS`). Those patterns are specific to
          log-message redaction where CLI flag forms (``--token=abc``)
          are common; IPC-bound keyring exception messages don't contain
          flag forms, so applying them there would be needless work (and
          a behavior change for ``credential_store._redact_sensitive``,
          which never had them).
        - Does **not** apply the :data:`_MIN_REDACT_LEN` short-string
          early-exit guard. Short inputs are still effectively pass-through
          because the generic 20+ char alphanumeric pattern only fires on
          long runs, and the prefix patterns (``Bearer`` / ``Token`` /
          ``sk-``) are specific enough to be safe on any length.
        - Does **not** stringify non-string input. Callers must convert
          explicitly (or use :func:`redact_secret`).
    """

    # hoisted _sub out of the loop (was re-created per pattern per call
    # — 4 function objects per call instead of 1). `replacement` is constant
    # for the whole call, so standard closure capture works correctly.
    def _sub(m: re.Match[str]) -> str:
        if m.lastindex:
            # Pattern has a prefix group (e.g. "Bearer ").  Keep
            # the prefix, redact the rest.
            return m.group(1) + replacement
        # No prefix group — redact the whole match.
        return replacement

    for pat in _KEY_PATTERNS:
        text = pat.sub(_sub, text)
    return text


def redact_url(url: str) -> str:
    """Redact credentials from a URL.

        Strips the userinfo component (``user:pass@``) — preserving the
        scheme, host, port, and path so the URL remains useful for
        debugging — and then chains through :func:`redact_secret` so any
        secret-bearing substring *elsewhere* in the URL is also masked.

    pre-fix, only the userinfo component was stripped. A URL
        like ``https://api.example.com/?key=sk-…`` or
        ``https://api.example.com/?access_token=…`` — where the credential
        lives in the query string rather than the userinfo — survived
        redaction verbatim. Any caller that logged the URL (e.g.
        :class:`voice_typer.server._http_safety._NoRedirectHandler` puts
        the redirect target into ``HTTPError.url`` and the error message)
        would leak the query-string secret. Chaining through
        :func:`redact_secret` with ``aggressive=True`` masks query-string
        ``key=value`` / ``token=value`` / ``access_token=value`` forms
        (via :data:`_FLAG_KEY_PATTERNS`) AND bare ``sk-…`` / ``Bearer …``
        / 20+ char alphanumeric runs (via :data:`_KEY_PATTERNS`).

        The chained :func:`redact_secret` pass runs with
        ``aggressive=True`` so short bare secrets (e.g. a 12-char
        ``?key=abc`` value, or a 16-char ``?t=shorttoken``) are also
        masked — the short-string guard from :func:`redact_secret` would
        otherwise skip generic-pattern application on URLs whose total
        length happens to be < 20 chars (rare, but possible for
        ``https://a.b/?k=secret``).
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return url
    if parsed.username or parsed.password:
        # Reconstruct without userinfo
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        url = parsed._replace(netloc=netloc).geturl()
    # chain through redact_secret so query-string / path
    # fragment secrets (?key=sk-…, ?access_token=…, ?api_key=…) are
    # also masked. Pre-fix only the userinfo component was stripped,
    # leaving query-string API keys verbatim. ``aggressive=True``
    # bypasses the short-string guard so short bare secrets in the
    # query string are caught too.
    return redact_secret(url, aggressive=True)


def _redact_home_path(path: str | os.PathLike[str]) -> str:
    """Replace the user-home prefix in ``path`` with ``~``.

    filesystem paths embedded in the diagnostic bundle
        (``sentinel_path``, ``pid_file_path``, ``bundle_path``) leak the
        OS username via the home-directory prefix
        (e.g. ``/Users/alice/.voice-typer/…`` on macOS,
        ``C:\\Users\\alice\\…`` on Windows, ``/home/alice/…`` on Linux).
        Replacing the home prefix with ``~`` preserves the path structure
        (so support engineers can still see "this is under the config
        dir", "this is a relative path", "this is on a different drive")
        without leaking the username.

        The home directory is resolved via :func:`os.path.expanduser` at
        call time (so a test that monkeypatches ``os.path.expanduser`` or
        sets ``HOME`` / ``USERPROFILE`` sees the expected result). On
        platforms where ``expanduser`` cannot determine the home dir it
        returns the literal ``"~"`` — in that case the path is returned
        unchanged (we never *introduce* a ``~`` that wasn't a real
        prefix substitution).

        Comparison is case-insensitive on Windows (NTFS is case-
        insensitive; ``HOMEDRIVE`` / ``HOMEPATH`` / ``USERPROFILE`` can
        vary by case between processes) and case-sensitive on POSIX
        (where the home dir is stable per-user).

        Parameters
        ----------
        path : str or os.PathLike
            The filesystem path to redact. ``PathLike`` inputs are
            stringified via :func:`os.fspath`.

        Returns
        -------
        str
            ``path`` with the user-home prefix replaced by ``~``. If the
            home dir cannot be resolved, or ``path`` does not start with
            it, ``path`` is returned unchanged (stringified).
    """
    s = os.fspath(path) if not isinstance(path, str) else path
    try:
        home = os.path.expanduser("~")
    except (KeyError, RuntimeError):
        # ``expanduser`` can raise on platforms where the user DB is
        # unreadable; treat as "home unknown" and return the path
        # verbatim (the leak is the username in the path, which we
        # can't strip without knowing the home prefix).
        return s
    if not home or home == "~":
        return s
    home_norm = os.path.normpath(home)
    s_norm = os.path.normpath(s)
    # ``os.path.normpath`` collapses ``//`` → ``/`` on POSIX and
    # ``C:\\`` → ``C:\\`` on Windows, but does NOT change case. On
    # Windows we compare case-insensitively because NTFS path
    # components can vary by case (``Users`` vs ``users``).
    if os.name == "nt":
        if s_norm.lower().startswith(home_norm.lower()):
            return "~" + s_norm[len(home_norm) :]
    else:
        if s_norm.startswith(home_norm):
            return "~" + s_norm[len(home_norm) :]
    return s


def redact_for_export(text: str) -> str:
    """Unified PII + secret redaction pipeline for diagnostic exports.

    pre-fix, the codebase ran TWO parallel PII-redaction
        pipelines. :mod:`voice_typer.server.diagnostics_export` chained
        ``redact_secret(redact_pii(line))`` for the live ``voice-typer.log``
        in the diagnostic zip, while :mod:`voice_typer.server.ipc_diagnostics`
        used :func:`voice_typer.server.security._redact_text` (which runs
        the same chain internally but with a fast-path trigger). The two
        pipelines had already drifted once (the diagnostics_export chain
        did not pass ``aggressive=True`` to :func:`redact_secret`, missing
    short bare secrets — ).

        This helper is the single source of truth for "redact this text
        before it lands in a diagnostic bundle / startup-error log". Both
        callers route through it so a future redaction improvement (a new
        pattern, a new keyword, a tighter threshold) only has to land in
        one place.

    Pipeline ():
          1. :func:`redact_pii` — applies the PII patterns (email, phone,
             SSN, CC, IBAN) and then runs :func:`redact_secret` (non-
             aggressive) + :func:`redact_url` internally.
          2. :func:`redact_secret(…, aggressive=True)` — a second pass
             with the short-string guard *bypassed* so bare short secrets
             (e.g. a 12-char bare API key with no ``Bearer`` / ``--token=``
             prefix) that survived the non-aggressive pass inside
             ``redact_pii`` are now masked. Idempotent on already-redacted
             text — the ``***`` mask doesn't match the secret patterns.

        Parameters
        ----------
        text : str
            The text to redact. Must already be a string — callers
            converting from ``object`` should call ``str(value)`` first.

        Returns
        -------
        str
            ``text`` with PII patterns replaced by token markers
            (``[EMAIL]``, ``[PHONE]``, …) and secret patterns replaced by
            ``<prefix>***`` / ``***``.

        Notes
        -----
        :func:`redact_pii` lives in :mod:`voice_typer.server.security`,
        which imports from this module at module load time
        (``from voice_typer.server._secrets import redact_secret,
        redact_url``). Importing :func:`redact_pii` at the top of this
        module would therefore create a circular import
        (``_secrets`` → ``security`` → ``_secrets``). The lazy import
        inside the function body breaks the cycle while preserving the
        call-time patchability that the tests rely on (they monkeypatch
        ``voice_typer.server.security.redact_pii`` and expect the patch
        to take effect on the next call).
    """
    # Lazy import to break the ``_secrets`` ↔ ``security`` cycle.
    from voice_typer.server.security import redact_pii

    return redact_secret(redact_pii(text), aggressive=True)


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

# module-level constant — was a per-call `frozenset({...})` literal
# in assert_url_allowed, re-evaluated on every cloud URL validation.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

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


# DEAD-CODE ( / , 2026-07-26): ``extend_url_allowlist`` has
# ZERO production call sites as of this run (verified by
# ``rg --no-ignore -n 'extend_url_allowlist' voice_typer/``). The only
# importers are tests (``tests/test_secrets.py`` and
# ``tests/test_security_fixes.py``), which exercise the  audit
# log + the host-normalization path in isolation.
#
# The intended production wiring is the  fix proposal: a new
# ``add_trusted_endpoint`` IPC command (with a paired
# ``trusted_extra_hosts: list[str]`` config field) that would call
# ``extend_url_allowlist`` from the IPC dispatch path. That wiring has
# not landed. The function is RETAINED here (not deleted) because:
#
# (1) The  audit-logging + caller-detection logic is non-
# trivial and would have to be re-implemented when
#       lands. Deleting it would lose that work and the test coverage
#       that pins its behavior.
#   (2) The tests still exercise the function and serve as a regression
#       gate for the eventual production wiring.
#
# Future readers: do NOT assume this function is live. If you see it
# called from production code, that means  has landed —
# remove this notice and the DEAD-CODE marker from the function
# docstring.
def extend_url_allowlist(
    hosts: Iterable[str],
    *,
    caller: str | None = None,
) -> None:
    """Add hostnames to the runtime URL allowlist.

    DEAD-CODE ( / ): no production callers as of
    2026-07-26. Retained pending the  wiring (new
        ``add_trusted_endpoint`` IPC command + ``trusted_extra_hosts``
        config field). See module-level DEAD-CODE notice above for
        rationale.

        Hostnames are normalized to lowercase and stripped of port.
        Duplicate additions are idempotent.

        Parameters
        ----------
        hosts : Iterable[str]
            Hostnames (with or without port) to add to the allowlist.
        caller : str, optional
            Identifier of the caller adding the hosts (e.g. ``"env_validation"``,
            ``"cloud_engines"``, ``"config.load"``). When ``None`` (default),
            the caller is auto-detected via :func:`inspect.stack` — the
            caller's module name + function name + line number. Used in the
            WARNING-level audit log so operators can trace every allowlist
            extension back to its origin.

    every call emits a ``WARNING``-level audit log of the
        form ``[URL-Allowlist] extended by <caller> with hosts: <hosts>``.
        This surfaces every runtime expansion of the trusted-host set in
        normal logs, so a malicious or buggy config file that adds an
        attacker-controlled host is visible without greping for the
        specific ``extend_url_allowlist`` call site.
    """
    # capture the caller for audit logging. Auto-detect via
    # inspect.stack() when the caller didn't pass an explicit identifier.
    # The frame of interest is the caller of ``extend_url_allowlist`` —
    # i.e. ``stack()[1]`` (frame 0 is this function itself).
    if caller is None:
        try:
            frame = inspect.stack()[1]
            mod = frame.frame.f_globals.get("__name__", "<unknown>")
            func = frame.function or "<unknown>"
            lineno = frame.lineno
            caller = f"{mod}.{func}:L{lineno}"
        except Exception as exc:  # noqa: BLE001 — inspect failures must not break the call
            caller = f"<inspect-failed: {exc}>"

    # Normalize the input hosts (lowercase, strip port, drop empties)
    # so the audit log shows exactly what was added — not the raw input.
    normalized: list[str] = []
    for h in hosts:
        if not h:
            continue
        # Strip port if present
        host = h.split(":")[0].strip().lower()
        if host:
            normalized.append(host)

    # calibrate the audit log level. WARNING is reserved for the
    # security-relevant case (actual hosts being added). When the call is
    # a no-op (empty iterable, or every host filtered out), demote to INFO
    # — operators still get an audit trail but no longer see WARNING spam
    # for every empty extend call.
    if normalized:
        log.warning(
            "[URL-Allowlist] extended by %s with hosts: %s",
            caller,
            normalized,
        )
    else:
        log.info(
            "[URL-Allowlist] no-op extend call by %s (no new hosts)",
            caller,
        )

    for host in normalized:
        _user_extensions.add(host)


def get_url_allowlist() -> frozenset[str]:
    """Return the current effective allowlist (defaults + user extensions)."""
    return _DEFAULT_ALLOWED_HOSTS | _user_extensions


# SSRF defense — IP-literal blocklist + best-effort DNS rebinding check ──
#
# The hostname allowlist above only checks the textual hostname.  If a
# trusted hostname (e.g. ``api.openai.com``) is made to resolve to a
# private/reserved IP — via ``/etc/hosts`` tampering, compromised DNS,
# DNS rebinding, or a malicious local DNS resolver — the request is sent
# to the private IP, exfiltrating the API key (in the Authorization
# header) and the request body to the cloud metadata endpoint
# (169.254.169.254) or any internal service.
#
# The two helpers below close that gap:
#
#   * ``_is_ip_literal(host)`` — True if the host string is already an
#     IP literal (e.g. ``"10.0.0.1"``, ``"::1"``).  Used to decide
#     between the IP-literal blocklist path and the DNS-rebinding path.
#
#   * ``_is_private_ip(ip_str)`` — True if the IP is in a
#     private/reserved range.  Covers RFC 1918 (10/8, 172.16/12,
#     192.168/16), link-local (169.254/16, including the cloud metadata
#     endpoint 169.254.169.254), loopback (127/8, ::1), unspecified
#     (0.0.0.0, ::), IPv6 ULA (fc00::/7), IPv6 link-local (fe80::/10),
#     and the various ``ipaddress`` ``is_reserved`` ranges.


def _is_ip_literal(host: str) -> bool:
    """Return True if ``host`` is an IP literal (IPv4 or IPv6).

    used by :func:`assert_url_allowed` to decide between the
        IP-literal blocklist path (host is already an IP) and the DNS-
        rebinding path (host is a hostname that needs resolution).

        ``urlparse().hostname`` strips brackets from IPv6 literals (e.g.
        ``"[::1]"`` → ``"::1"``), so the caller passes the bracket-stripped
        form.  ``ipaddress.ip_address`` accepts both bare IPv4 (``"1.2.3.4"``)
        and bare IPv6 (``"::1"``, ``"fe80::1"``); it rejects hostnames,
        empty strings, and malformed IPs with ``ValueError``.
    """
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private_ip(ip_str: str) -> bool:
    """Return True if ``ip_str`` is a private/reserved IP address.

    SSRF defense — rejects IP literals in private/reserved ranges
        so an attacker cannot use a private-IP endpoint (planted in
        ``/etc/hosts`` or via :func:`extend_url_allowlist`) to receive cloud
        API keys.  Covers:

        * RFC 1918 private: ``10/8``, ``172.16/12``, ``192.168/16``
          (via ``ip.is_private``).
        * Link-local: ``169.254/16`` (including the cloud metadata endpoint
          ``169.254.169.254``) and IPv6 ``fe80::/10`` (via
          ``ip.is_link_local``).
        * Loopback: ``127/8`` and ``::1`` (via ``ip.is_loopback``).
        * Unspecified: ``0.0.0.0`` and ``::`` (via ``ip.is_unspecified``).
        * IPv6 unique-local: ``fc00::/7`` (covered by ``ip.is_private``).
        * Reserved ranges: ``240/4``, ``255.255.255.255`` broadcast, etc.
          (via ``ip.is_reserved``).

        Returns ``False`` for non-IP strings (callers should check
        :func:`_is_ip_literal` first to distinguish "not an IP" from
        "public IP").  Returns ``True`` for any IP that would let an
        attacker reach an internal service or the cloud metadata endpoint.
    """
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False  # not an IP literal — caller should resolve first
    # ``is_private`` for IPv4 includes RFC 1918 + 127/8 + 169.254/16 +
    # a few others (per CPython source).  We OR the other checks for
    # defense-in-depth and to cover IPv6 cases that ``is_private`` may
    # not catch (e.g. ``is_link_local`` is the canonical check for
    # ``fe80::/10``).
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved)


def is_url_allowed(url: str) -> bool:
    """Return True if the URL's host is in the allowlist.

    Empty URLs are rejected (consistent with :func:`assert_url_allowed`
    which raises ``ValueError`` on empty input).  URLs with no hostname
    (e.g. ``javascript:alert(1)``) are also rejected.
    """
    if not url:
        return False
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
    allow_loopback_http: bool = False,
    check_dns_rebinding: bool = True,
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
    when True (default), non-loopback hosts must use
            HTTPS. Loopback hosts (localhost, 127.0.0.1, ::1) are exempt
            so local development servers can use HTTP. This prevents a
            loopback IPC attacker from exfiltrating transcribed text to
            ``http://attacker.example.com/steal`` even if the attacker
            somehow adds the host to the allowlist.
        allow_loopback_http : bool
    when True, loopback hosts (localhost, 127.0.0.1, ::1)
            are also exempt from the HTTPS requirement — i.e. plain HTTP
            to ``http://localhost:11434`` is permitted. Defaults to False
            so callers must OPT IN to allowing cleartext loopback traffic.
            Callers that send user-supplied text (``llm_polish``,
            ``cloud_engines``) should set this to True when the user has
            explicitly configured a local HTTP endpoint (Ollama, vLLM,
            LM Studio, etc.). Callers that only validate the URL structure
            (env var validation) should leave it False.

    Pre-, loopback was ALWAYS exempt from the HTTPS
            requirement. This meant a caller that just wanted to verify
            URL structure would silently allow HTTP loopback, even when
            the caller's actual data flow never needed cleartext
            transmission. The opt-in kwarg makes the security posture
            explicit at every call site.
        check_dns_rebinding : bool
    when True (default), after the allowlist + HTTPS checks
            pass, perform an SSRF defense check.  For IP-literal hosts the
            check is a blocklist lookup via :func:`_is_private_ip` (always
            run).  For hostname hosts, the check resolves the hostname via
            :func:`socket.getaddrinfo` and rejects if ANY resolved IP is
            private/reserved (catches DNS rebinding, ``/etc/hosts``
            tampering, and compromised-DNS attacks).  The DNS resolution
            is best-effort: a ``socket.gaierror`` (no DNS, offline,
            sandboxed test env) is silently swallowed and the URL is
            allowed — the actual HTTP layer will surface the DNS error in
            the normal way.  Callers that run in a no-network test
            environment can set this to False to skip the resolution
            entirely (the IP-literal blocklist still runs).

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
    # enforce HTTPS for non-loopback hosts to prevent
    # cleartext exfiltration of transcribed text + API keys.
    #
    # the loopback exemption is now gated on
    # ``allow_loopback_http``. Pre-fix, loopback was ALWAYS exempt —
    # so a caller that just wanted to validate URL structure would
    # silently allow HTTP loopback, even when the caller's actual
    # data flow never needed cleartext transmission. Now callers
    # must opt in via the kwarg, making the security posture
    # explicit.
    is_loopback = host in _LOOPBACK_HOSTS  # was per-call frozenset literal
    if require_https and parsed.scheme == "http" and (not is_loopback or not allow_loopback_http):
        if is_loopback:
            # loopback HTTP rejected because caller didn't opt in
            # via ``allow_loopback_http=True``. The error message
            # explicitly mentions the kwarg so the operator knows how
            # to fix the call site.
            raise ValueError(
                f"{client_name}: {field_name} must use HTTPS for loopback "
                f"host {host!r} (HTTP requires explicit opt-in via "
                f"allow_loopback_http=True — local development servers "
                f"should be the only consumers of cleartext loopback)."
            )
        raise ValueError(
            f"{client_name}: {field_name} must use HTTPS for non-loopback "
            f"host {host!r} (HTTP is only allowed for localhost/127.0.0.1/::1 "
            f"for local development). Cleartext transmission of API keys "
            f"and transcribed text over the public internet is not permitted."
        )

    # SSRF defense — after the allowlist + HTTPS checks pass,
    # verify the host is not a private/reserved IP literal (and
    # best-effort, that a hostname doesn't resolve to a private IP).
    #
    # Loopback IPs (127.0.0.1, ::1) are EXEMPTED because they're
    # explicitly allowlisted for local development — the user has
    # already opted in to sending data to localhost.  All other
    # private/reserved IP literals (10/8, 172.16/12, 192.168/16,
    # 169.254/16 including the cloud metadata endpoint, fc00::/7,
    # fe80::/10, 0.0.0.0, ::, etc.) are REJECTED even if the user
    # explicitly added them to the allowlist — defense-in-depth
    # against an attacker who tricks the user into calling
    # ``extend_url_allowlist(["10.0.0.5"])`` and then sets
    # ``cloud_api_url = "https://10.0.0.5/"`` to exfiltrate the API
    # key to an internal service.
    #
    # For hostnames (e.g. ``api.openai.com``), the check resolves via
    # ``socket.getaddrinfo`` and rejects if ANY resolved IP is
    # private/reserved.  This catches DNS rebinding (attacker's DNS
    # returns a public IP for the first resolution, then a private IP
    # for the actual connection — TOCTOU on DNS) and ``/etc/hosts``
    # tampering.  Best-effort: ``gaierror`` is swallowed (offline test
    # environments) and the URL is allowed.
    if is_loopback:
        # Loopback IPs (127.0.0.1, ::1) are explicitly allowlisted for
        # local development — skip the SSRF check (the user has opted
        # in to sending data to localhost).
        return
    if _is_ip_literal(host):
        # IP-literal blocklist (the minimum  fix).  Even if the
        # user explicitly added a private IP to the allowlist, refuse
        # to send cloud API keys to internal endpoints.
        if _is_private_ip(host):
            raise ValueError(
                f"{client_name}: {field_name} host {host!r} is a "
                f"private/reserved IP literal — refusing to prevent "
                f"SSRF. Even if explicitly allowlisted, "
                f"private/reserved IP literals are rejected to "
                f"prevent exfiltration of API keys to internal "
                f"endpoints (e.g. cloud metadata 169.254.169.254)."
            )
    elif check_dns_rebinding:
        # Best-effort post-resolution check (catches DNS rebinding,
        # /etc/hosts tampering, compromised DNS).  Resolve via
        # getaddrinfo; if any resolved IP is private/reserved, reject.
        # Failure to resolve is NON-FATAL (gaierror swallowed) — the
        # HTTP layer will surface the DNS error in the normal way.
        # This means a no-network test environment won't reject
        # allowlisted hostnames (the IP-literal blocklist above still
        # runs for IP literals).
        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            infos = []
        for _family, _type, _proto, _canonname, sockaddr in infos:
            # sockaddr[0] is the IP address string for both AF_INET
            # (host, port) and AF_INET6 (host, port, flowinfo, scopeid).
            ip = sockaddr[0]
            if _is_private_ip(ip):
                raise ValueError(
                    f"{client_name}: {field_name} host {host!r} resolves "
                    f"to private/reserved IP {ip!r} — refusing to "
                    f"prevent SSRF (DNS rebinding defense). If "
                    f"this is a legitimate local endpoint, use the IP "
                    f"literal directly (e.g. http://127.0.0.1:port) "
                    f"which is allowlisted for local development."
                )
