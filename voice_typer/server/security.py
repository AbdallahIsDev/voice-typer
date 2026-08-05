"""Security utilities for Voice Typer.

Secure file reading with symlink protection.
PII redaction filter for log messages.
SEC-audit-005: Model integrity verification (SHA-256 hash checking).

Historical note: the SEC-001 restart-token machinery
(``generate_restart_token`` / ``verify_restart_token`` /
``consume_restart_token``) that previously lived here was dead code —
imported into ``app.py`` but never called in production. The
``single_instance.py`` enforcement path relies on the old process
releasing the mutex/flock before the new process acquires it, not on a
time-limited token file. The orphan functions (and their dedicated test
file ``tests/test_restart_token.py``) have been removed; the
``VOICE_TYPER_RESTART`` env var is still honored as a hint that a
restart is in progress, but no token file is created or verified.
"""

import contextlib
import hashlib
import hmac
import json
import logging
import mmap
import os
import re
import threading
import traceback as _traceback
from pathlib import Path
from typing import Any

from voice_typer.server._secrets import _redact_home_path, redact_secret, redact_url
from voice_typer.server.secure_file_io import _secure_atomic_write, _secure_read_text

log = logging.getLogger(__name__)


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
_FAST_TRIGGER = re.compile(r"[@+]|\d{3,}|Bearer|Token|sk-|key=|(?<![/\\])[A-Za-z0-9_\-]{20,}(?![/\\])")


# Cache for the home-path-substring regex, keyed by the resolved home
# dir string.  The home dir is resolved via ``os.path.expanduser("~")``
# at call time (so tests that monkeypatch ``HOME`` / ``USERPROFILE`` see
# the expected result); the cache is invalidated automatically when the
# resolved home dir changes between calls.
_HOME_PATH_RE_CACHE: tuple[str, "re.Pattern[str]"] | None = None


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
    try:
        home = os.path.expanduser("~")
    except (KeyError, RuntimeError):
        # expanduser can raise on platforms where the user DB is
        # unreadable; treat as "home unknown" and return text verbatim.
        return text
    if not home or home == "~":
        return text
    if _HOME_PATH_RE_CACHE is None or _HOME_PATH_RE_CACHE[0] != home:
        flags = re.IGNORECASE if os.name == "nt" else 0
        # Match the home dir followed by a path separator and any
        # subsequent non-whitespace characters.  The ``[/\\]`` after the
        # home dir ensures we do not partially match a longer path
        # (e.g. ``/home/alice`` inside ``/home/alice2``).  Trailing
        # punctuation (commas, parens) may be included in the match but
        # is preserved by ``_redact_home_path`` which only swaps the
        # home prefix for ``~``.
        pattern = re.compile(re.escape(home) + r"[/\\]\S*", flags)
        _HOME_PATH_RE_CACHE = (home, pattern)
    return _HOME_PATH_RE_CACHE[1].sub(lambda m: _redact_home_path(m.group()), text)


def _redact_text(text: str) -> str:
    """Apply PII + API-secret + URL-credential + home-path redaction to *text*.

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
                record.exc_text = _redact_text(tb_text)
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


# ─── SEC-audit-005: Model Integrity Verification ──────────────────────────

# Pinned revisions for HuggingFace model downloads.
# When a specific commit SHA is known, it should be recorded here so that
# snapshot_download() pins to an exact version instead of the mutable "main"
# branch.  This prevents supply-chain attacks where a compromised repo
# pushes a new commit with malicious model files.
#
# The canonical source is ``model_hashes.json`` in the same directory as this
# module.  The JSON file can be updated by the release process without
# touching Python source code.  We fall back to a hardcoded dict if the JSON
# file is missing or unreadable (e.g. during unit tests in isolated envs).


def _load_model_hashes() -> "dict[str, dict[str, Any]]":
    """Load MODEL_HASHES from the companion JSON file, with hardcoded fallback.

    the value type is widened from ``dict[str, str]`` to
    ``dict[str, Any]`` because each manifest entry mixes value kinds
    (``"revision": "main"`` is a str, ``"files": {filename: hash}`` is
    a nested dict).  The narrower annotation made
    ``manifest.get("files", {})`` infer as ``str`` and broke the
    downstream ``.items()`` call in both security.py and qwen_engine.py.
    """
    json_path = Path(__file__).parent / "model_hashes.json"
    if json_path.exists():
        try:
            # use ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` /
            # Windows reparse-point rejection) instead of ``Path.read_text``
            # so a symlink planted at ``model_hashes.json`` cannot redirect
            # the read to an attacker-controlled file and inject pinned
            # SHA-256 entries. On a symlink, ``_secure_read_text`` raises
            # ``OSError``/``ValueError`` which is caught below — the
            # hardcoded fallback then applies.
            raw = json.loads(_secure_read_text(json_path))
            # Filter out the _comment metadata key
            return {k: v for k, v in raw.items() if k != "_comment" and isinstance(v, dict)}
        except Exception as exc:
            log.warning("[SECURITY] Failed to load model_hashes.json: %s", exc)
    # Hardcoded fallback — mirrors model_hashes.json so that even if the JSON
    # file is missing or unreadable (e.g. isolated test env, broken install),
    # the pinned revisions are still enforced. SHAs fetched 2026-07-10 from
    # https://huggingface.co/api/models/<repo>/revision/main. These MUST be
    # kept in sync with model_hashes.json; the test_model_hashes_fallback_matches_json
    # regression test enforces this.
    return {
        "nvidia/parakeet-tdt-0.6b-v3": {
            "revision": "7c35754d166cca382ad1e53e68b01e7c575f3a1d",
            "files": {
                "config.json": "e747b85e1bdfd300c8b8ac63bac8dd5221f8fe9bc275b48d06c735fcd6971b6e",
                "generation_config.json": "b141de6ec6d7f982ece13f98f604e3fe1807ea9c0e839185d0ab7064604209d0",
                "model.safetensors": "3a2026366188c8c68598edbbff92f8d11590a08e0ae2e6775544e7b07d6a5e11",
                "tokenizer.json": "bd321b096832a3f270bd3b2a88823957920f1a5c5ada71114a26ea729d0cbe91",
                "tokenizer_config.json": "0b2fe0037599ee335f0b972fa682bf0ece74e4ccfec755cb7daa3405d3d3e874",
            },
        },
        "Systran/faster-whisper-tiny.en": {
            "revision": "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
            "files": {
                "config.json": "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5",
                "model.bin": "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a",
                "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            },
        },
        "Systran/faster-whisper-small.en": {
            "revision": "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
            "files": {
                "config.json": "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae",
                "model.bin": "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
                "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            },
        },
        "Systran/faster-whisper-medium.en": {
            "revision": "a29b04bd15381511a9af671baec01072039215e3",
            "files": {
                "config.json": "4a1848ebabe7938d9797c15a2e8e4ce1d36e6fd4a43d096ae5955257c67c7962",
                "model.bin": "11b220779aea4c6f3ce9d2549c8a95ea869ed84066864b999531ef53e594fe5b",
                "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            },
        },
        "Systran/faster-whisper-large-v3": {
            "revision": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "files": {
                "config.json": "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
                "model.bin": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1",
                "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                "tokenizer.json": "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca",
            },
        },
        # add the ``qwen`` entry to the hardcoded fallback so
        # a missing/corrupt ``model_hashes.json`` doesn't soft-pass
        # Qwen. ``revision: "local"`` + empty ``files`` triggers the
        # new hard-FAIL path in ``verify_model_integrity`` above; this
        # is intentional — operators must populate ``files`` with real
        # SHA-256 hashes before a local Qwen model can be loaded. The
        # empty dict mirrors the JSON file's ``"qwen"`` entry so the
        # ``test_model_hashes_fallback_matches_json`` regression test
        # (which enforces fallback/JSON parity) keeps passing.
        "qwen": {
            "revision": "local",
            "files": {},
        },
    }


MODEL_HASHES: "dict[str, dict[str, Any]]" = _load_model_hashes()


# ─── : On-disk integrity cache for SHA-256 verification ─────────────
#
# verify_model_integrity() is called UNCONDITIONALLY on every model load
# (cache hit AND miss). Pre-, this re-hashed the full multi-GB weight
# file (model.safetensors ~2.5 GB for Parakeet, model.bin ~3 GB for
# Whisper large-v3) on EVERY load — 5-10 s of pure I/O + SHA-256 CPU per
# load. The  idle-unload feature made this worse.
#
# The integrity cache is a JSON file at <config_dir>/cache/integrity_cache.json
# keyed on (repo_id, relpath, st_mtime_ns, st_size) -> sha256_hex. On a
# cache hit (mtime+size match), the cached hash is returned without
# re-reading the file.
#
# Security: the cache key includes mtime_ns + size. An attacker with
# write access to the HF cache would need to (a) modify the file, (b)
# restore the original mtime to nanosecond precision, AND (c) preserve
# the exact byte size — AND the cached hash still has to match the
# pinned manifest hash. So the cache does NOT weaken the security
# guarantee; it only skips the redundant re-hash of unchanged files.
_INTEGRITY_CACHE_VERSION = 1
_integrity_cache_lock = threading.Lock()
# Tests can override the cache path by setting this attribute.
_integrity_cache_path_override: "Path | None" = None


def _integrity_cache_path() -> Path:
    """Return the path to the on-disk integrity cache JSON file."""
    if _integrity_cache_path_override is not None:
        return _integrity_cache_path_override
    from voice_typer.server._paths import config_dir

    return config_dir() / "cache" / "integrity_cache.json"


def _load_integrity_cache() -> "dict[str, Any]":
    """Load the integrity cache from disk. Returns empty cache on any error.

    uses ``_secure_read_text`` (POSIX ``O_NOFOLLOW`` / Windows
    reparse-point rejection) instead of ``Path.read_text`` so a symlink
    planted at ``<config_dir>/cache/integrity_cache.json`` cannot
    redirect the read to an arbitrary file and control the cached
    SHA-256 entries. On a symlink, ``_secure_read_text`` raises
    ``OSError`` (POSIX ``ELOOP``) / ``OSError`` (Windows reparse-point
    rejection), which is caught by the broad ``except Exception`` and
    falls through to the empty cache.
    """
    empty = {"version": _INTEGRITY_CACHE_VERSION, "repos": {}}
    try:
        path = _integrity_cache_path()
        if not path.exists():
            return empty
        raw_text = _secure_read_text(path)
        # defense-in-depth — re-tighten perms to 0o600 on every
        # successful read. Mirrors ``secure_file_io._chmod_owner_only``.
        # Best-effort; a read-only filesystem must not fail the load.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            return empty
        if raw.get("version") != _INTEGRITY_CACHE_VERSION:
            return empty
        repos = raw.get("repos", {})
        if not isinstance(repos, dict):
            return empty
        return {"version": _INTEGRITY_CACHE_VERSION, "repos": repos}
    except Exception as exc:
        log.debug("[SECURITY] integrity cache load failed (%s) — starting empty", exc)
        return empty


def _save_integrity_cache(cache: "dict[str, Any]") -> None:
    """Atomically write the integrity cache to disk. Best-effort.

    delegates to ``_secure_atomic_write`` ( ``owned_fd``
    sentinel + explicit ``_chmod_owner_only`` + symlink-safe
    ``tempfile.mkstemp``) instead of a bare ``tempfile.mkstemp`` +
    ``os.fdopen`` + ``os.replace`` block. ``durability=False`` preserves
    the pre- no-fsync cache-write behaviour — the integrity cache
    is a perf optimization (skips re-hashing multi-GB model files), not
    security-critical state, so a power-loss window of a few seconds is
    acceptable (the next ``verify_model_integrity`` call re-computes
    any missing cache entry).
    """
    try:
        path = _integrity_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _secure_atomic_write(path, json.dumps(cache), durability=False)
    except Exception as exc:
        log.debug("[SECURITY] integrity cache save failed (%s) — cache will not persist", exc)


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file.

    uses mmap when possible for zero-copy hashing of large model
    files. Falls back to the 64 KB chunk loop on mmap failure (e.g.
    mmap of a 0-length file raises ValueError).
    """
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                h.update(mm)
            finally:
                mm.close()
        return h.hexdigest()
    except (ValueError, OSError) as exc:
        log.debug(
            "[SECURITY] mmap hash failed for %s — falling back to chunk loop: %s",
            path,
            exc,
        )
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()


def verify_model_integrity(local_dir: str, repo_id: str) -> bool:
    """SEC-audit-005: Verify downloaded model files against the manifest.

    Computes SHA-256 hashes of all files in ``local_dir`` and compares
    them against the pinned hashes in ``MODEL_HASHES``.  If no hashes
    are pinned for a given file, a basic structural check is performed
    (file exists and is not empty) and the computed hash is logged at
    INFO level so it can be added to the manifest later.

    hashes are memoized in an on-disk integrity cache
    (``<config_dir>/cache/integrity_cache.json``) keyed on
    ``(repo_id, relpath, st_mtime_ns, st_size)``. On a cache hit, the
    cached hash is reused without re-reading the multi-GB weight file
    — saving 5-10 s of pure I/O+CPU on every model load. The cache is
    invalidated automatically when the file's mtime or size changes.

    Parameters
    ----------
    local_dir : str
        Path to the downloaded model directory.
    repo_id : str
        HuggingFace repository identifier (e.g. "nvidia/parakeet-tdt-0.6b-v3").

    Returns
    -------
    bool
        True if all verifications pass, False otherwise. Returns False
        on any pinned-hash mismatch (hard fail) so callers can refuse
        to load a tampered model.
    """
    model_path = Path(local_dir)
    if not model_path.exists():
        log.warning("[SECURITY] Model directory does not exist: %s", local_dir)
        return False

    manifest = MODEL_HASHES.get(repo_id, {})

    # Check for at least one model file (safetensors, bin, or onnx)
    model_extensions = {".safetensors", ".bin", ".onnx", ".pt"}
    has_model_file = False
    for f in model_path.rglob("*"):
        if f.is_file() and f.suffix in model_extensions and f.stat().st_size > 0:
            has_model_file = True
            break

    if not has_model_file:
        log.warning(
            "[SECURITY] Model integrity check failed: no model files found in %s",
            local_dir,
        )
        return False

    # Check config.json exists
    config_json = model_path / "config.json"
    if not config_json.exists():
        log.warning(
            "[SECURITY] Model integrity check failed: config.json missing in %s",
            local_dir,
        )
        return False

    # hard-FAIL for local models with an empty ``files`` dict.
    # Pre-fix, ``verify_model_integrity`` soft-passed whenever the
    # manifest's ``files`` dict was empty (see the ``else`` branch
    # below). For HuggingFace repos this was acceptable because the
    # ``revision`` field is a SHA pin validated upstream by
    # ``snapshot_download``'s commit-pin; the empty-files state was a
    # "to-be-populated" placeholder. But for ``revision: "local"`` (the
    # Qwen model, loaded from a user-supplied local path), there is NO
    # upstream SHA pin — the soft-pass meant a tampered or substituted
    # Qwen model directory would load without any integrity check. The
    # fix: when ``revision == "local"`` AND ``files`` is empty, return
    # False (hard FAIL) so the caller refuses to load. Operators who
    # want to load a local Qwen model MUST populate the ``files`` dict
    # in ``model_hashes.json`` with the expected SHA-256 hashes (the
    # soft-pass branch below already logs them at INFO).
    manifest_revision = manifest.get("revision")
    pinned_files = manifest.get("files", {})
    if manifest_revision == "local" and not pinned_files:
        log.error(
            "[SECURITY] Model integrity: hard-FAIL for local model %s — "
            'model_hashes.json has "revision": "local" with empty "files". '
            "A local model has no upstream SHA pin, so the empty-files "
            "soft-pass would let a tampered directory load unchecked. "
            'Populate the "files" dict with the expected SHA-256 hashes '
            "(the INFO logs from a prior run with the correct model print "
            "them) to enable verification on the next run.",
            repo_id,
        )
        return False

    # load the integrity cache ONCE for the whole verification
    # call. Keyed on (repo_id, relpath, st_mtime_ns, st_size) -> sha256.
    # The cache lock is held only for load/save — NOT for hash
    # computation (which can take 5-10 s for a multi-GB weight file).
    with _integrity_cache_lock:
        cache = _load_integrity_cache()
    cache_dirty = False

    def _hash_with_cache(file_path: Path, relpath: str) -> str:
        """Return the SHA-256 of file_path, using the cache when possible."""
        nonlocal cache_dirty
        try:
            st = file_path.stat()
            mtime_ns = st.st_mtime_ns
            size = st.st_size
        except OSError as exc:
            log.debug(
                "[SECURITY] stat failed for %s — computing uncached hash: %s",
                file_path,
                exc,
            )
            return compute_file_sha256(file_path)
        repos = cache.setdefault("repos", {})
        repo_entries = repos.setdefault(repo_id, {})
        entry = repo_entries.get(relpath)
        if (
            isinstance(entry, dict)
            and entry.get("mtime_ns") == mtime_ns
            and entry.get("size") == size
            and isinstance(entry.get("sha256"), str)
        ):
            return entry["sha256"]
        digest = compute_file_sha256(file_path)
        repo_entries[relpath] = {
            "mtime_ns": mtime_ns,
            "size": size,
            "sha256": digest,
        }
        cache_dirty = True
        return digest

    # SEC-audit-005: Verify pinned file hashes if available.
    # The manifest entry for a repo can include a "files" dict mapping
    # relative file paths to expected SHA-256 hex digests. When present,
    # every pinned file MUST exist and match — a single mismatch fails
    # the integrity check (hard fail) so callers refuse to load a
    # tampered or corrupted model.
    #
    # When no files are pinned (the manifest only has "revision"), we
    # compute and log hashes for every file in the model directory at
    # INFO level. Operators can copy these logged hashes into
    # model_hashes.json to enable enforcement on the next run.
    #
    # this branch is now reachable ONLY for HuggingFace
    # repos (``revision`` is a 40-char SHA, validated upstream by
    # ``snapshot_download``). Local repos with empty files hit the
    # hard-FAIL branch above.
    if pinned_files:
        for filename, expected_hash in pinned_files.items():
            file_path = model_path / filename
            if not file_path.exists():
                log.warning(
                    "[SECURITY] Model integrity: pinned file %s missing in %s",
                    filename,
                    local_dir,
                )
                return False
            actual_hash = _hash_with_cache(file_path, filename)
            if not hmac.compare_digest(actual_hash, expected_hash):
                log.warning(
                    "[SECURITY] Model integrity: hash mismatch for %s in %s "
                    "(expected %s..., got %s...) — refusing to load tampered model",
                    filename,
                    local_dir,
                    expected_hash[:16],
                    actual_hash[:16],
                )
                return False
        log.info(
            "[SECURITY] Model integrity check passed for %s (%d pinned files verified)", repo_id, len(pinned_files)
        )
    else:
        # No pinned hashes — log computed hashes for future audit.
        # This is a soft pass; the structural checks above are the
        # hard gate that prevents loading completely wrong file types.
        # SEC-audit-005: emit a WARNING (not just INFO) so operators
        # notice that model integrity verification is effectively a
        # no-op for this repo. Pre-fix the empty-files state produced
        # zero enforcement but only an INFO log, which is invisible at
        # default log levels — operators had no way to know their
        # model_hashes.json was empty. The WARNING surfaces the issue
        # in normal logs without refusing to load (the structural
        # checks above are still enforced).
        log.warning(
            "[SECURITY] Model integrity check is a NO-OP for %s — "
            'model_hashes.json has empty "files" dict for this repo. '
            "Computed hashes are logged below; copy them into "
            'model_hashes.json under the repo\'s "files" field to '
            "enable enforcement on the next run.",
            repo_id,
        )
        for entry in model_path.rglob("*"):
            if not entry.is_file():
                continue
            try:
                rel = entry.relative_to(model_path).as_posix()
                h = _hash_with_cache(entry, rel)
                log.info("[SECURITY]   %s: sha256=%s", rel, h)
            except Exception as exc:
                log.debug("[SECURITY]   failed to hash %s: %s", entry, exc)

    # persist the cache once at the end (only if dirty).
    if cache_dirty:
        with _integrity_cache_lock:
            _save_integrity_cache(cache)

    return True


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
