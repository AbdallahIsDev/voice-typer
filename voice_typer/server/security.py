"""Security utilities for Voice Typer.

SEC-002: Secure file reading with symlink protection.
SEC-009: PII redaction filter for log messages.
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

import hashlib
import hmac
import json
import logging
import re
import traceback as _traceback
from pathlib import Path
from typing import Any

from voice_typer.server._secrets import redact_secret, redact_url

log = logging.getLogger(__name__)


# ─── SEC-009: PII Redaction Filter ──────────────────────────────────────

# XV-122: fast-path trigger for ``_redact_text``.  The redaction pass
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
_FAST_TRIGGER = re.compile(r"[@+]|\d{3,}|Bearer|Token|sk-|key=|[A-Za-z0-9_\-]{20,}")


def _redact_text(text: str) -> str:
    """Apply PII + API-secret + URL-credential redaction to *text*.

    RW-6: shared helper used by :class:`PIIRedactionFilter` for both
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

    XV-122: a single :data:`_FAST_TRIGGER` scan gates the whole pass.
    Every trigger in the alternation is a *necessary* condition for at
    least one downstream pattern to match, so a miss lets us return
    *text* unchanged without issuing the 8-12 ``re.sub`` calls (a
    5-10x speedup for the common log line that carries no secret /
    PII / URL-credential trigger).
    """
    # XV-122: fast path — no trigger means no pattern can match, so
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

    RW-6: in addition to the formatted log message, the filter also
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
        record.msg = msg
        record.args = ()

        # RW-6: pre-format and redact the traceback so exceptions
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
    """SEC-009: Redact potential PII from a text string.

    Standalone helper that applies the same redaction patterns as
    ``PIIRedactionFilter`` but can be used directly on arbitrary
    strings (e.g. before logging transcription text, error messages,
    or other user-visible content).

    Patterns redacted:
      - Email addresses → [EMAIL]
      - Phone numbers (US-style 7-digit) → [PHONE]
      - Phone numbers (international, E.164-ish) → [PHONE]
        (``+1 (415) 555-2671``, ``+44 20 7946 0958``)
      - IBAN (international bank account number) → [IBAN]
        (``GB82WEST12345698765432``)
      - SSN-like patterns → [SSN]
      - Credit-card-like patterns → [CC]

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
    for pattern, replacement in PIIRedactionFilter._PATTERNS:
        text = pattern.sub(replacement, text)
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

    TASK-14: the value type is widened from ``dict[str, str]`` to
    ``dict[str, Any]`` because each manifest entry mixes value kinds
    (``"revision": "main"`` is a str, ``"files": {filename: hash}`` is
    a nested dict).  The narrower annotation made
    ``manifest.get("files", {})`` infer as ``str`` and broke the
    downstream ``.items()`` call in both security.py and qwen_engine.py.
    """
    json_path = Path(__file__).parent / "model_hashes.json"
    if json_path.exists():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
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
        # NF-R18-9: add the ``qwen`` entry to the hardcoded fallback so
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


def compute_file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file.

    Reads in 64 KB chunks to avoid loading large model files
    entirely into memory.

    Parameters
    ----------
    path : Path
        File to hash.

    Returns
    -------
    str
        Hex-encoded SHA-256 digest.
    """
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

    # NF-R18-9: hard-FAIL for local models with an empty ``files`` dict.
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
    # NF-R18-9: this branch is now reachable ONLY for HuggingFace
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
            actual_hash = compute_file_sha256(file_path)
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
                h = compute_file_sha256(entry)
                rel = entry.relative_to(model_path).as_posix()
                log.info("[SECURITY]   %s: sha256=%s", rel, h)
            except Exception as exc:
                log.debug("[SECURITY]   failed to hash %s: %s", entry, exc)

    return True


# SEC-009: The standalone ``_redact_pii`` helper that previously lived
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
# stderr buffer) unredacted — defeating the SEC-009 / RW-6 redaction
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
