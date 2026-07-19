"""Security utilities for Voice Typer.

SEC-001: Restart token verification (prevents mutex bypass).
SEC-002: Secure file reading with symlink protection.
SEC-009: PII redaction filter for log messages.
SEC-audit-005: Model integrity verification (SHA-256 hash checking).
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import traceback as _traceback
from pathlib import Path
from typing import Any

from voice_typer.server._secrets import redact_secret, redact_url

log = logging.getLogger(__name__)


# ─── SEC-001: Restart Token Verification ─────────────────────────────────


def generate_restart_token() -> str:
    """Generate a random restart token and persist it for verification.

    The token is written to a file in the config directory so that
    ``_verify_restart_token()`` can validate it on the next process
    start.  Uses ``secrets.token_hex`` for cryptographic randomness.
    """
    token = secrets.token_hex(16)
    try:
        from voice_typer.server.config import _config_dir, _secure_atomic_write

        token_path = _config_dir() / ".restart_token"
        _secure_atomic_write(token_path, token)
    except Exception as exc:
        # SEC-001: fail-closed by design (do not surface the error to a
        # potential attacker). DEBUG is invisible at default log levels but
        # available to operators running with --debug / LOG_LEVEL=DEBUG so
        # a permissions issue or misconfigured config dir is diagnosable.
        log.debug("generate_restart_token failed: %s", exc)
    return token


def verify_restart_token() -> bool:
    """Verify that ``VOICE_TYPER_RESTART`` contains a valid token.

    Reads the stored token from the config directory and performs a
    constant-time comparison with the environment variable value.
    Returns ``False`` if:
      - the env var is not set
      - the token file doesn't exist
      - the tokens don't match (including timing-safe comparison)
      - any I/O error occurs
    """
    env_val = os.environ.get("VOICE_TYPER_RESTART", "")
    if not env_val:
        return False
    try:
        from voice_typer.server.config import _config_dir, _secure_read_text

        token_path = _config_dir() / ".restart_token"
        if not token_path.exists():
            return False
        # SEC-002: use _secure_read_text to prevent symlink-TOCTOU attacks
        stored = _secure_read_text(token_path, encoding="utf-8").strip()
        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(stored, env_val)
    except Exception as exc:
        # SEC-001: fail-closed by design (do not reveal *why* to a
        # potential attacker). DEBUG is invisible at default log levels but
        # available to operators running with --debug / LOG_LEVEL=DEBUG so
        # a misconfigured config dir, permissions issue on .restart_token,
        # or corrupted token file is distinguishable from a wrong token.
        log.debug("verify_restart_token failed: %s", exc)
        return False


def consume_restart_token() -> None:
    """Delete the restart token file after successful verification."""
    try:
        from voice_typer.server.config import _config_dir

        (_config_dir() / ".restart_token").unlink(missing_ok=True)
    except Exception as exc:
        # SEC-001: fail-closed by design. DEBUG only — see verify_restart_token.
        log.debug("consume_restart_token failed: %s", exc)


# ─── SEC-009: PII Redaction Filter ──────────────────────────────────────


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
       bearer tokens (``Bearer …``, ``Token …``, ``sk-…``, 32+ char
       bare tokens).  It is a no-op for strings shorter than 20
       characters, so short log lines are untouched.
    3. :func:`redact_url` strips ``user:pass@`` userinfo from URLs.
       Gated on ``"@" in text`` so the comparatively expensive
       :func:`urllib.parse.urlparse` call is skipped for the vast
       majority of log records that contain no ``@``.

    The redaction helpers are imported from :mod:`voice_typer.server._secrets`
    so the secret-matching patterns stay defined in exactly one place
    (no duplicated regexes).
    """
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
      - Phone numbers (US-style) → ``[PHONE]``
      - SSN-like patterns → ``[SSN]``
      - Credit-card-like patterns → ``[CC]``
      - API keys / bearer tokens (``Bearer …``, ``Token …``, ``sk-…``,
        32+ char bare tokens) → ``<prefix>***`` or ``***``
        (via :func:`voice_typer.server._secrets.redact_secret`)
      - URL-embedded credentials (``user:pass@host``) → credentials
        stripped, host preserved
        (via :func:`voice_typer.server._secrets.redact_url`)

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
        # Phone numbers (various formats)
        (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
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
      - Phone numbers (US-style) → [PHONE]
      - SSN-like patterns → [SSN]
      - Credit-card-like patterns → [CC]

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
