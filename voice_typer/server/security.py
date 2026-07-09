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
from pathlib import Path
from typing import Any

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
    except Exception:
        pass
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
    except Exception:
        return False


def consume_restart_token() -> None:
    """Delete the restart token file after successful verification."""
    try:
        from voice_typer.server.config import _config_dir
        (_config_dir() / ".restart_token").unlink(missing_ok=True)
    except Exception:
        pass


# ─── SEC-009: PII Redaction Filter ──────────────────────────────────────


class PIIRedactionFilter(logging.Filter):
    """Redact potential PII from log messages.

    Patterns redacted:
      - Email addresses
      - Phone numbers (US-style)
      - SSN-like patterns
      - Credit-card-like patterns
    """

    _PATTERNS: list[tuple[re.Pattern[str], str]] = [
        # Email addresses
        (re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b'), '[EMAIL]'),
        # Phone numbers (various formats)
        (re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'), '[PHONE]'),
        # SSN-like patterns
        (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN]'),
        # Credit card-like patterns
        (re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'), '[CC]'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in self._PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = ()
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
    # Hardcoded fallback (matches model_hashes.json defaults)
    return {
        "nvidia/parakeet-tdt-0.6b-v3": {
            "revision": "main",  # TODO: pin to specific commit SHA after audit
        },
        "Systran/faster-whisper-tiny.en": {
            "revision": "main",  # TODO: pin to specific commit SHA after audit
        },
        "Systran/faster-whisper-small.en": {
            "revision": "main",  # TODO: pin to specific commit SHA after audit
        },
        "Systran/faster-whisper-medium.en": {
            "revision": "main",  # TODO: pin to specific commit SHA after audit
        },
        "Systran/faster-whisper-large-v3": {
            "revision": "main",  # TODO: pin to specific commit SHA after audit
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
    pinned_files = manifest.get("files", {})
    if pinned_files:
        for filename, expected_hash in pinned_files.items():
            file_path = model_path / filename
            if not file_path.exists():
                log.warning(
                    "[SECURITY] Model integrity: pinned file %s missing in %s",
                    filename, local_dir,
                )
                return False
            actual_hash = compute_file_sha256(file_path)
            if not hmac.compare_digest(actual_hash, expected_hash):
                log.warning(
                    "[SECURITY] Model integrity: hash mismatch for %s in %s "
                    "(expected %s..., got %s...) — refusing to load tampered model",
                    filename, local_dir,
                    expected_hash[:16], actual_hash[:16],
                )
                return False
        log.info("[SECURITY] Model integrity check passed for %s (%d pinned files verified)",
                 repo_id, len(pinned_files))
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
            "model_hashes.json has empty \"files\" dict for this repo. "
            "Computed hashes are logged below; copy them into "
            "model_hashes.json under the repo's \"files\" field to "
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

