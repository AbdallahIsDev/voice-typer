"""Secret + PII redaction helpers (consolidated from ``_secrets.py`` + ``security.py``).

Part of the :mod:`voice_typer.server.security` package
(consolidation). Hosts the redaction half of the former
``voice_typer.server._secrets`` module (API-key / bearer-token /
flag-form / URL-userinfo / home-path redaction) merged with the PII
redaction filter from the former ``voice_typer.server.security`` module
(``PIIRedactionFilter``, ``redact_pii``, ``install_lastresort_pii_filter``).

The URL-allowlist half of the former ``_secrets.py`` now lives in
:mod:`voice_typer.server.security.url_allowlist`; the model-integrity
half of the former ``security.py`` lives in
:mod:`voice_typer.server.security.model_integrity`.

These helpers are intentionally framework-agnostic (no requests, no
httpx) so they can be used from ``cloud_engines.py``, ``llm_polish.py``,
and any future HTTP client without coupling.
"""

from __future__ import annotations

import logging
import os
import re
import traceback as _traceback
from urllib.parse import urlparse

from voice_typer.server._paths import IPC_TOKEN_ENV_VAR as _IPC_TOKEN_ENV_VAR

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

# ── Public env-var name whitelist ────────────────────────────────────────
#
# Env-var NAMES are public (documented in docs, ADRs, source code, and
# ``spawn.rs``). Redacting them provides ZERO security benefit and HURTS
# operability — operators can't tell which env var is misconfigured from a
# log line like ``[ENV] Invalid value for ***=<redacted>``. Only VALUES
# should be redacted; the name must survive.
#
# This whitelist is the PRIMARY guard: any token that exactly matches a
# name in this set is left untouched by the generic 20+ char alphanumeric
# pattern (the last entry in ``_KEY_PATTERNS``). The Bearer/Token/sk-/gsk_
# prefix patterns are unaffected because env-var names never carry those
# prefixes.
#
# Keep this list in sync with:
#   - ``voice_typer/server/env_validation.py`` (validated env vars)
#   - ``voice_typer/server/_paths.py`` (``IPC_TOKEN_ENV_VAR``)
#   - ``voice_typer/server/_secrets.py`` (``_ENV_TRUSTED_HOSTS_VAR``)
#   - ``src-tauri/src/sidecar/spawn.rs`` (Tauri host → Python sidecar env)
#   - ``voice_typer/server/logging_setup.py`` / ``log/__init__.py``
# A drift-detection guard is provided by the
# ``test_public_env_var_names_not_redacted`` regression test in
# ``tests/test_secrets.py``.
_PUBLIC_ENV_VAR_NAMES: frozenset[str] = frozenset(
    {
        # Voice Typer config / runtime
        "VOICE_TYPER_CONFIG_DIR",
        _IPC_TOKEN_ENV_VAR,  # imported from _paths to avoid bare literal
        "VOICE_TYPER_NATIVE_DIR",
        "VOICE_TYPER_PREWARM_EXE",
        "VOICE_TYPER_RESTART",
        "VOICE_TYPER_QUIET",
        "VOICE_TYPER_DEBUG",
        "VOICE_TYPER_NO_TRAY",
        "VOICE_TYPER_STREAMING",
        "VOICE_TYPER_TRUSTED_HOSTS",
        "VOICE_TYPER_DEBUG_EVENTS",
        "VOICE_TYPER_LOG_JSON",
        "VOICE_TYPER_LOG_LEVEL_MODULES",
        "VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK",
        # Hugging Face
        "HUGGING_FACE_HUB_TOKEN",
        "HF_HOME",
        "HF_ENDPOINT",
        "HF_TOKEN",
        # Tauri host contract
        "TAURI_SIDECAR",
        # Cloud-provider API key env-var names (the NAMES are public —
        # the VALUES are stripped from os.environ at startup by
        # ``env_validation._SENSITIVE_ENV_NAMES``; logging the NAME in a
        # "stripped because it was set" warning is the documented
        # operator diagnostic).
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPGRAM_API_KEY",
        "GROQ_API_KEY",
    }
)

# a previous defense-in-depth heuristic
# (``_ENV_VAR_NAME_RE``) treated ANY 20+ char UPPERCASE_WITH_UNDERSCORES
# token as an env-var NAME and left it un-redacted, even when it wasn't
# in the explicit whitelist. That introduced a silent redaction
# downgrade: real all-caps base64-style secret VALUES (e.g.
# ``SECRET_TOKEN_LIKE_THING_0123456789``) also match the env-var NAME
# shape and leaked into logs / diagnostic bundles verbatim.
#
# There is NO safe syntactic name-vs-value discriminator — the shapes
# are identical. Only the explicit ``_PUBLIC_ENV_VAR_NAMES`` whitelist
# above exempts a token from redaction. A non-whitelisted env var that
# gets logged IS masked, which is the correct failure direction for a
# redaction helper.

# explicit flag / key=value forms for secret-bearing keywords.
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

    # Generic 20+ char alphanumeric pattern (last entry in
    # ``_KEY_PATTERNS``): skip redaction for tokens that are PUBLIC
    # env-var NAMES. Env-var names are documented in docs / ADRs /
    # source code and carry zero secret value; redacting them
    # destroys operability (operators can't tell which env var is
    # misconfigured from ``[ENV] Invalid value for ***=<redacted>``).
    # Only the VALUES are redacted (via the call-site ``=<redacted>``
    # convention in ``env_validation.py`` and via the
    # ``_FLAG_KEY_PATTERNS`` / Bearer / Token / sk- / gsk_ patterns
    # here when a value follows a secret-bearing keyword).
    #
    # The prefix patterns (Bearer / Token / sk- / gsk_) are
    # unaffected — env-var names never carry those prefixes, so they
    # can be applied with the plain ``_sub`` callback.
    generic_pat = _KEY_PATTERNS[-1]

    def _generic_sub(m: re.Match[str]) -> str:
        token = m.group()
        if token in _PUBLIC_ENV_VAR_NAMES:
            return token
        return replacement

    for pat in _KEY_PATTERNS[:-1]:
        text = pat.sub(_sub, text)
    text = generic_pat.sub(_generic_sub, text)
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


def _resolve_home_dirs() -> list[str]:
    """Return candidate home directories, most-specific first.

    Includes the explicit ``HOME`` env override (honoured on EVERY
    platform — :func:`ntpath.expanduser` ignores ``HOME`` on Windows,
    preferring ``USERPROFILE``, which silently defeats redaction when
    ``HOME`` is explicitly overridden) plus the platform-resolved home
    (``USERPROFILE`` on Windows). Redacting against BOTH candidates
    means an explicit override AND the platform default are both
    protected — e.g. under Git-Bash on Windows, ``HOME`` may be the
    POSIX-style ``/c/Users/alice`` while real log paths are
    ``C:\\Users\\alice\\…``; checking both covers that case.

    Deduplicated, non-empty values only. Returns ``[]`` when no home
    can be resolved (callers treat that as "home unknown" and return
    the input unchanged).
    """
    homes: list[str] = []
    env_home = os.environ.get("HOME")
    if env_home:
        homes.append(env_home)
    try:
        resolved = os.path.expanduser("~")
    except (KeyError, RuntimeError):
        resolved = ""
    if resolved and resolved not in homes:
        homes.append(resolved)
    return homes


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

        The home directory is resolved at call time via
        :func:`_resolve_home_dirs` (explicit ``HOME`` override + the
        platform default). On platforms where no home can be determined,
        the path is returned unchanged (we never *introduce* a ``~``
        that wasn't a real prefix substitution).

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
            ``path`` with the user-home prefix replaced by ``~``. If no
            home dir can be resolved, or ``path`` does not start with
            one, ``path`` is returned unchanged (stringified).
    """
    s = os.fspath(path) if not isinstance(path, str) else path
    s_norm = os.path.normpath(s)
    for home in _resolve_home_dirs():
        if not home or home == "~":
            continue
        home_norm = os.path.normpath(home)
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
        :func:`redact_pii` is defined in this same module (the merged
        redaction module). The lazy import through the package namespace
        is retained so the call-time patchability the tests rely on keeps
        working: they monkeypatch ``voice_typer.server.security.redact_pii``
        (the package re-export) and expect the patch to take effect on the
        next call — resolving the name through the package at call time
        guarantees exactly that.
    """
    # Lazy import through the package namespace keeps test monkeypatches
    # of ``voice_typer.server.security.redact_pii`` effective at call time.
    from voice_typer.server.security import redact_pii

    return redact_secret(redact_pii(text), aggressive=True)


# ─── SEC-009: PII Redaction Filter ──────────────────────────────────────

# fast-path trigger for ``_redact_text``.  The redaction pass
# runs 8-12 regex substitutions unconditionally on every log record,
# even though the vast majority of records contain no PII / secret
# pattern.  This single ``re.Pattern.search`` is a *necessary* condition
# for ANY of the redaction patterns (``PIIRedactionFilter._PATTERNS``,
# ``_secrets._KEY_PATTERNS``, ``_secrets._FLAG_KEY_PATTERNS`` whose
# value is ≥20 chars, and the URL-credential branch gated on ``"@"``)
# to match — so a miss here means we can return the input unchanged
# without running any of the heavier substitutions.
#
# Trigger breakdown (each is necessary for at least one pattern):
#   - ``@``      — email pattern (``\b[\w.+-]+@[\w-]+\.[\w.-]+\b``) and
#                  the URL-credential branch (``redact_url``).
#   - ``+``      — international phone pattern (``\+\d{1,3}…``).
#   - ``\d{3,}`` — US phone, SSN, credit-card, and every realistic IBAN
#                  (every country's BBAN format includes 3+ consecutive
#                  digits).
#   - ``Bearer`` — ``_KEY_PATTERNS[0]``.
#   - ``Token``  — ``_KEY_PATTERNS[1]``.
#   - ``sk-``    — ``_KEY_PATTERNS[2]``.
#   - ``key=``   — bare ``key=`` flag form (``_BARE_KEY_VALUE_PATTERN``
#                  with keyword ``key``); also a substring of ``--key=``
#                  and other ``--<keyword>=`` flag forms whose keyword
#                  ends in ``key`` (e.g. ``--api_key=``, ``--api-key=``).
#   - ``[A-Za-z0-9_\-]{20,}`` — the generic 20+ char bare-token pattern
#                  (``_KEY_PATTERNS[3]``); also catches any flag form
#                  whose *value* is 20+ chars (the common production
#                  case — real API keys are long).
# the 20+ char alternation uses negative lookbehind/lookahead on
# path delimiters so filesystem path components are not false-positive
# redacted (mirrors the fix in _secrets._KEY_PATTERNS[-1]).
_FAST_TRIGGER = re.compile(r"[@+]|\d{3,}|Bearer|Token|sk-|key=|(?<![/\\])[A-Za-z0-9_\-]{20,}(?![/\\])|[\x00-\x1f\x7f]")


# Cache for the home-path-substring regex, keyed by the resolved home
# dir string.  The home dir is resolved via ``os.path.expanduser("~")``
# at call time (so tests that monkeypatch ``HOME`` / ``USERPROFILE`` see
# the expected result); the cache is invalidated automatically when the
# resolved home dir changes between calls.
_HOME_PATH_RE_CACHE: tuple[tuple[str, ...], list[re.Pattern[str]]] | None = None


def _redact_home_path_in_text(text: str) -> str:
    """Replace home-directory path prefixes embedded anywhere in *text*.

    :func:`voice_typer.server._secrets._redact_home_path` only redacts
    when the *entire* input string is a single filesystem path under the
    home dir (it checks ``s.startswith(home)``).  Log messages, by
    contrast, embed paths inside larger sentences (e.g.
    ``"Opening log file: /home/alice/.voice-typer/foo.log"``), so the
    whole-string check returns the input unchanged and the OS username
    leaks to ``voice-typer.log``.

    This helper scans *text* for substrings that start with the home
    directory followed by a path separator and applies
    :func:`_redact_home_path` to each match -- replacing the home-dir
    prefix with ``~`` while preserving the rest of the path.  The regex
    is compiled once per unique home dir and cached.
    """
    global _HOME_PATH_RE_CACHE
    # Candidate homes: the explicit ``HOME`` override first (honoured on
    # every platform — ``ntpath.expanduser`` ignores ``HOME`` on Windows,
    # so a simulated / Git-Bash ``HOME`` alone would silently defeat the
    # redaction) plus the platform-resolved home (``USERPROFILE`` on
    # Windows). Both are redacted so an override AND the real home are
    # protected.
    homes = _resolve_home_dirs()
    if not homes:
        return text
    key = tuple(homes)
    if _HOME_PATH_RE_CACHE is None or _HOME_PATH_RE_CACHE[0] != key:
        patterns: list[re.Pattern[str]] = []
        flags = re.IGNORECASE if os.name == "nt" else 0
        for home in homes:
            if not home or home == "~":
                continue
            # Match the home dir followed by a path separator and any
            # subsequent non-whitespace characters.  The ``[/\\]`` after
            # the home dir ensures we do not partially match a longer
            # path (e.g. ``/home/alice`` inside ``/home/alice2``).
            # Trailing punctuation (commas, parens) may be included in
            # the match but is preserved by ``_redact_home_path`` which
            # only swaps the home prefix for ``~``.
            patterns.append(re.compile(re.escape(home) + r"[/\\]\S*", flags))
        _HOME_PATH_RE_CACHE = (key, patterns)
    for pattern in _HOME_PATH_RE_CACHE[1]:
        text = pattern.sub(lambda m: _redact_home_path(m.group()), text)
    return text


# HU-15: C0 control characters (plus DEL) that must be escaped before
# a log message is emitted. A raw ``\n`` / ``\r`` in dictated text (or
# any user-influenced log payload) lets an attacker forge a second log
# line that visually appears as a legitimate ERROR/CRITICAL record;
# raw ANSI escapes (``\x1b``) let them paint arbitrary terminal
# colours. ``\t`` is included so column alignment can't be disturbed by
# a hostile payload. The escape lives in ``_redact_text`` so EVERY log
# record passing through ``PIIRedactionFilter`` gets the scrub — not
# just the transcription-text call sites (which are gated by the
# ``config.log_transcriptions`` opt-in).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _escape_control_chars(text: str) -> str:
    """Replace C0 control characters with visible escapes (HU-15).

    ``\n`` → the literal two-char sequence ``\\n``, ``\r`` → ``\\r``,
    ``\t`` → ``\\t``, and any other C0 control char / DEL → a
    ``\\xNN`` escape. The JSON formatter is unaffected (``json.dumps``
    escapes newlines anyway); the text formatters now emit the escaped
    form so a dictated ``"Hello\n[CRITICAL] fake"`` cannot forge a
    second disk line.
    """

    def _esc(m: re.Match[str]) -> str:
        ch = m.group(0)
        if ch == "\n":
            return "\\n"
        if ch == "\r":
            return "\\r"
        if ch == "\t":
            return "\\t"
        return f"\\x{ord(ch):02x}"

    return _CONTROL_CHAR_RE.sub(_esc, text)


def _redact_text(text: str, *, escape_control_chars: bool = True) -> str:
    """Apply PII + API-secret + URL-credential + home-path redaction to *text*.

    When ``escape_control_chars`` is True (default) C0 control chars are
    escaped so a payload cannot forge extra log lines (HU-15). The
    ``PIIRedactionFilter`` traceback path passes False to preserve
    multi-line traceback readability — its structural newlines are not
    user-controlled, so there is no forgery risk there.

    shared helper used by :class:`PIIRedactionFilter` for both
    the formatted log message and the formatted traceback.  Order
    matters:

    1. PII patterns (email / phone / SSN / CC) are applied first so
       the specific token names (``[EMAIL]``, ``[PHONE]``, …) appear
       in the output rather than the more aggressive ``***`` mask
       produced by :func:`redact_secret`.
    2. :func:`redact_secret` is then applied to catch API keys and
       bearer tokens (``Bearer …``, ``Token …``, ``sk-…``, 20+ char
       bare tokens).  It is a no-op for strings shorter than 20
       characters, so short log lines are untouched.
    3. :func:`redact_url` strips ``user:pass@`` userinfo from URLs.
       Gated on ``"@" in text`` so the comparatively expensive
       :func:`urllib.parse.urlparse` call is skipped for the vast
       majority of log records that contain no ``@``.

    The redaction helpers are imported from :mod:`voice_typer.server._secrets`
    so the secret-matching patterns stay defined in exactly one place
    (no duplicated regexes).

    a single :data:`_FAST_TRIGGER` scan gates the whole pass.
    Every trigger in the alternation is a *necessary* condition for at
    least one downstream pattern to match, so a miss lets us return
    *text* unchanged without issuing the 8-12 ``re.sub`` calls (a
    5-10x speedup for the common log line that carries no secret /
    PII / URL-credential trigger).
    """
    # step 0 — redact the home-directory prefix unconditionally.
    # This MUST run before the fast-path check below: a bare path like
    # ``/home/alice/.voice-typer/foo.log`` carries no fast-path trigger
    # (no ``@`` / ``+`` / 3+ consecutive digits / ``Bearer`` / ``Token``
    # / ``sk-`` / ``key=`` / 20+ char token) so the trigger scan would
    # return the input unchanged and the username would leak to
    # ``voice-typer.log``.  ``_redact_home_path_in_text`` handles paths
    # embedded inside larger sentences (the common log-message case).
    text = _redact_home_path_in_text(text)
    # fast path — no trigger means no pattern can match, so
    # skip the substitution loop entirely.  ``str`` input only; the
    # ``PIIRedactionFilter.filter`` call site always passes the
    # already-stringified ``record.getMessage()`` / traceback text.
    if not _FAST_TRIGGER.search(text):
        return text
    # HU-15: escape C0 control chars BEFORE the PII patterns so a
    # dictated phrase like ``"Hello\n[CRITICAL] fake"`` cannot forge a
    # second log line — the raw newline becomes the literal two-char
    # sequence ``\n`` in the emitted text (and raw ANSI escapes become
    # ``\x1b``). Runs after the fast-path gate, which now includes the
    # control-char class, so control-char-free lines still
    # short-circuit without paying for the substitution loop.
    # ``escape_control_chars=False`` opts out (used by the traceback
    # path — see ``PIIRedactionFilter.filter``) so structurally
    # multi-line content like formatted tracebacks keeps its line
    # breaks instead of collapsing to one line.
    if escape_control_chars:
        text = _escape_control_chars(text)
    for pattern, replacement in PIIRedactionFilter._PATTERNS:
        text = pattern.sub(replacement, text)
    text = redact_secret(text)
    if "@" in text:
        text = redact_url(text)
    return text


class PIIRedactionFilter(logging.Filter):
    """Redact potential PII and API secrets from log messages.

    Patterns redacted:
      - Email addresses → ``[EMAIL]``
      - Phone numbers (US-style 7-digit) → ``[PHONE]``
      - Phone numbers (international, E.164-ish) → ``[PHONE]``
        (covers ``+1 (415) 555-2671``, ``+44 20 7946 0958``,
        ``+86 10 1234 5678``; requires at least 7 trailing digits)
      - IBAN (international bank account number) → ``[IBAN]``
        (``GB82WEST12345698765432``,
        ``DE89370400440532013000``; 2-letter country + 2 check digits
        + 10-30 BBAN chars)
      - SSN-like patterns → ``[SSN]``
      - Credit-card-like patterns → ``[CC]``
      - API keys / bearer tokens (``Bearer …``, ``Token …``, ``sk-…``,
        20+ char bare tokens) → ``<prefix>***`` or ``***``
        (via :func:`voice_typer.server._secrets.redact_secret`)
      - URL-embedded credentials (``user:pass@host``) → credentials
        stripped, host preserved
        (via :func:`voice_typer.server._secrets.redact_url`)
      - Filesystem paths containing the user's home directory
        (``/home/alice/…``, ``/Users/alice/…``,
        ``C:\\Users\\alice\\…``) → home prefix replaced with ``~``
        (via :func:`voice_typer.server._secrets._redact_home_path`)

    Known limitations (NOT redacted — too high a false-positive rate
    on ordinary numeric text):

      - **US ABA routing numbers** (9-digit ``021000021`` form): the
        pattern is just 9 digits with no country prefix or check-digit
        structure; matching it would redact every 9-digit order ID,
        zip+4 extension, and timestamp fragment in operator logs.
        Operators who need routing-number redaction should add it
        explicitly at the call site (e.g. via a per-message
        ``re.sub`` before logging).

      - **Generic 9-20 digit numbers** (potential account / customer
        IDs): same false-positive concern.

    in addition to the formatted log message, the filter also
    pre-formats and redacts the traceback when ``record.exc_info`` is
    set.  The redacted text is cached on ``record.exc_text`` so any
    subsequent :class:`logging.Formatter` that appends ``exc_text``
    (including the default :meth:`logging.Formatter.format`) emits the
    redacted version.  This catches exceptions whose ``str(exc)``
    carries an API key — e.g. a ``requests.exceptions.ConnectionError``
    whose message includes ``?key=sk-…`` — which would otherwise be
    emitted verbatim by ``log.error("...: %s", exc,
    exc_info=True)`` style call sites.
    """

    _PATTERNS: list[tuple[re.Pattern[str], str]] = [
        # Email addresses
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
        # IBAN (international bank account number): 2-letter country
        # code, 2 check digits, then 10-30 BBAN chars (alnum).
        # Examples: ``GB82WEST12345698765432``, ``DE89370400440532013000``.
        # covers all 80+ IBAN-using jurisdictions; the
        # country-code + check-digit prefix keeps false positives
        # negligible. MUST run before phone patterns so the digit
        # portion of an IBAN isn't mis-matched as a phone number.
        (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[IBAN]"),
        # Phone numbers (US-style: 555-123-4567, 5551234567, 555.123.4567)
        (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
        # International phone numbers (E.164-ish and common domestic
        # formats with country code): ``+1 (415) 555-2671``,
        # ``+44 20 7946 0958``, ``+86 10 1234 5678``.
        # added because the US-only pattern above missed the
        # common ``+<country-code> <subscriber>`` form used by every
        # non-US locale. The regex requires a ``+`` prefix to
        # distinguish from bare digit sequences (e.g. US ABA routing
        # numbers, zip codes) that happen to match the digit count.
        (re.compile(r"\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b"), "[PHONE]"),
        # SSN-like patterns
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
        # Credit card-like patterns
        (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"), "[CC]"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        msg = _redact_text(msg)
        # store the redacted message on ``record.redacted_msg`` so
        # downstream structured consumers (metrics exporters, a future
        # MemoryHandler ring buffer that re-emits to a structured backend)
        # can read the redacted version WITHOUT having to re-format
        # ``record.msg`` / ``record.args``. The existing text/JSON
        # formatters continue to consult ``record.msg`` (mutated below)
        # for backward compat — the new attribute is purely additive.
        #
        # We ALSO keep the legacy ``record.msg = msg`` / ``record.args = ()``
        # mutation (the original SEC-009 behavior) because the existing
        # text/JSON formatters and tests rely on ``record.msg`` carrying
        # the redacted text. Future structured consumers should prefer
        # ``record.redacted_msg`` (always set when the filter runs); the
        # legacy ``record.msg`` mutation is preserved as the "text path"
        # so a no-code-change upgrade is possible.
        record.redacted_msg = msg
        record.msg = msg
        record.args = ()

        # pre-format and redact the traceback so exceptions
        # whose ``str(exc)`` carries an API key don't leak the key via
        # the formatted traceback.  ``record.exc_text`` is consulted
        # by :meth:`logging.Formatter.format` (which only re-formats
        # ``exc_info`` when ``exc_text`` is unset), so caching the
        # redacted version here is sufficient — no Formatter subclass
        # required.  ``record.exc_info`` itself is left intact for
        # structured-logging consumers that introspect the actual
        # exception object.
        if record.exc_info:
            try:
                tb_text = "".join(_traceback.format_exception(*record.exc_info))
            except Exception:
                tb_text = ""
            if tb_text:
                # escape_control_chars=False: tracebacks keep their
                # structural newlines (multi-line readability). Their
                # line breaks come from Python's traceback formatter,
                # not from user-controlled payloads, so the HU-15 log-
                # forging vector does not apply here.
                record.exc_text = _redact_text(tb_text, escape_control_chars=False)
        return True


# ─── SEC-009: PII Redaction Helper ──────────────────────────────────────


def redact_pii(text: str) -> str:
    """Redact potential PII and API secrets from a text string.

    Standalone helper that applies the same redaction patterns as
    ``PIIRedactionFilter`` but can be used directly on arbitrary
    strings (e.g. before logging transcription text, error messages,
    or other user-visible content).

    previously this function only applied the four PII
    patterns (email / phone / SSN / CC) — API keys, bearer tokens,
    and URL-embedded credentials passed through verbatim. The
    ``llm_polish.py`` docstring claimed API keys were covered, which
    was false. The function now also applies :func:`redact_secret`
    (API keys / bearer tokens) and :func:`redact_url` (URL userinfo)
    so it is a true single-call redaction helper. Existing callers
    that already chain ``redact_secret(redact_pii(...))`` see no
    behavioural change — both redactions are idempotent on already-
    redacted text.

    Patterns redacted:
      - Filesystem paths containing the user's home directory
        (``/home/alice/…``, ``/Users/alice/…``,
        ``C:\\Users\\alice\\…``) → home prefix replaced with ``~``
        (via :func:`_redact_home_path_in_text` /
        :func:`voice_typer.server._secrets._redact_home_path`)
      - Email addresses → [EMAIL]
      - Phone numbers (US-style 7-digit) → [PHONE]
      - Phone numbers (international, E.164-ish) → [PHONE]
        (``+1 (415) 555-2671``, ``+44 20 7946 0958``)
      - IBAN (international bank account number) → [IBAN]
        (``GB82WEST12345698765432``)
      - SSN-like patterns → [SSN]
      - Credit-card-like patterns → [CC]
      - API keys / bearer tokens (``Bearer …``, ``Token …``, ``sk-…``,
        20+ char bare tokens) → ``<prefix>***`` or ``***``
        (via :func:`voice_typer.server._secrets.redact_secret`)
      - URL-embedded credentials (``user:pass@host``) → credentials
        stripped, host preserved
        (via :func:`voice_typer.server._secrets.redact_url`)

    The home-path redaction runs FIRST (mirroring :func:`_redact_text`)
    so a bare path like ``/home/alice/.voice-typer/foo.log`` is
    sanitised before any of the pattern substitutions see it. This
    closes a PII leak in the cloud-LLM call path (``llm_polish.py``),
    the hallucination filter, the config sanitizer, and the diagnostic
    bundle exporter — all of which call ``redact_pii`` directly or
    indirectly via ``redact_for_export``.

    Known limitations (NOT matched): US ABA routing numbers
    (9-digit form, too high a false-positive rate on ordinary numeric
    text — see ``PIIRedactionFilter`` docstring for details).

    Parameters
    ----------
    text : str
        Input text that may contain PII.

    Returns
    -------
    str
        Text with PII patterns replaced by redaction tokens.
    """
    # step 0 — redact the home-directory prefix unconditionally.
    # This MUST run before the PII / secret / URL passes: a bare path
    # like ``/home/alice/.voice-typer/foo.log`` carries no fast-path
    # trigger and no PII token, so the downstream passes would leave
    # the OS username intact — leaking it to the cloud LLM
    # (``llm_polish.py``), diagnostic bundle (``redact_for_export``),
    # hallucination filter, and config sanitizer. Mirrors the same
    # step in :func:`_redact_text`.
    text = _redact_home_path_in_text(text)
    for pattern, replacement in PIIRedactionFilter._PATTERNS:
        text = pattern.sub(replacement, text)
    # also redact API keys / bearer tokens (idempotent on
    # already-redacted text — the ``<prefix>***`` mask doesn't match
    # the secret patterns).
    text = redact_secret(text)
    # also strip URL userinfo. Gated on ``"@" in text`` for
    # the same perf reason as ``_redact_text`` — the vast majority of
    # inputs carry no ``@`` so the comparatively expensive
    # ``urllib.parse.urlparse`` call is skipped.
    if "@" in text:
        text = redact_url(text)
    return text


# The standalone ``_redact_pii`` helper that previously lived
# here was a DUPLICATE of ``redact_pii`` above (lines 114-140) with
# slightly different regex patterns and replacement tokens. Having two
# parallel implementations of the same logic was a maintenance hazard
# (Q5: parallel systems; Q10: not clean) — see FORENSIC_REVIEW_COMPLETE.md
# → SEC-009.
#
# Additionally, the ``_redact_pii`` regex patterns contained literal
# backspace characters (``\x08``) where word-boundary ``\b`` was
# intended — the function would never have matched anything in practice.
#
# The single canonical implementation is ``redact_pii(text)`` (above)
# and the ``PIIRedactionFilter`` class (also above) which uses the same
# compiled patterns. Use one of those two APIs for any new call site.


# ─── PIIRedactionFilter on logging.lastResort ───────────────────────
#
# By default Python's logging module uses a "last resort" handler — a
# StreamHandler writing to ``sys.stderr`` at WARNING level — when a
# logger has no handlers configured anywhere in its ancestor chain.
# Third-party libraries (``keyring``, ``urllib3``, ``websockets``,
# ``asyncio``) typically do NOT call ``basicConfig`` or attach their
# own handlers, so their WARNING/ERROR output flows through
# ``logging.lastResort`` directly to stderr.
#
# Pre-fix, ``logging.lastResort`` had NO PII-redaction filter
# attached. A buggy keyring backend that logged a credential value, or
# a urllib3 exception whose message echoed a request URL with an API
# key in the query string, would land in stderr (and any captured
# stderr buffer) unredacted — defeating the SEC-009 /  redaction
# that protects the rotating-file handler.
#
# The fix: replace ``logging.lastResort`` with a ``StreamHandler``
# carrying the same ``PIIRedactionFilter`` used by the file/console
# handlers in :func:`voice_typer.server.log.setup_logging`. This way
# third-party logger output is subject to the same PII / secret
# scrubbing as Voice Typer's own loggers.
#
# The function is idempotent and safe to call multiple times: it
# always replaces ``lastResort`` with a fresh handler (no duplicate
# filters accumulate). It runs at module import time so the protection
# is in place as soon as :mod:`voice_typer.server.security` is loaded,
# which happens early in app startup via the logging-setup import
# chain.


def install_lastresort_pii_filter() -> logging.Handler:
    """Install PIIRedactionFilter on ``logging.lastResort``.

    Replaces Python's default last-resort handler (a bare
    :class:`logging.StreamHandler` writing to ``sys.stderr`` at
    WARNING level) with an equivalent handler that carries a
    :class:`PIIRedactionFilter`. This ensures third-party logger
    output (``keyring``, ``urllib3``, ``websockets``) is PII-redacted
    before reaching stderr, closing the gap documented above.

    Returns
    -------
    logging.Handler
        The new last-resort handler (also assigned to
        ``logging.lastResort``).

    Notes
    -----
    Idempotent. Safe to call multiple times — each call replaces the
    prior handler rather than stacking filters.
    """
    handler = logging.StreamHandler()
    handler.setLevel(logging.WARNING)
    handler.addFilter(PIIRedactionFilter())
    logging.lastResort = handler
    return handler


# Install at import time so the protection is in place as soon as the
# security module is loaded — typically during
# :func:`voice_typer.server.log.setup_logging`, which runs early in
# app startup. Tests that want to assert the install happened can
# re-invoke ``install_lastresort_pii_filter()`` or just inspect
# ``logging.lastResort.filters`` directly.
install_lastresort_pii_filter()
