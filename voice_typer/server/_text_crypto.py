"""At-rest text crypto for history."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time

from voice_typer.server import credential_store as _cs
from voice_typer.server.credential_store import _dek

log = logging.getLogger(__name__)

# Lazy probe for cryptography; missing dep degrades to plaintext (no crash).
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    _AESGCM = None
    _CRYPTOGRAPHY_AVAILABLE = False

#: Blob prefix, version tag for the on-disk ciphertext format.
BLOB_PREFIX = "enc:v1:"

#: Nonce length in bytes (96-bit, NIST SP 800-38D random-nonce budget).
_NONCE_LENGTH = 12

#: GCM tag length in bytes (128-bit, appended by ``AESGCM.encrypt``).
_TAG_LENGTH = 16

#: DEK length in bytes (AES-256).
_DEK_LENGTH = 32

#: Placeholder returned when a row's ciphertext cannot be decrypted.
DECRYPTION_FAILED_PLACEHOLDER = "<decryption failed>"

#: Minimum seconds between two identical rate-limited log records.
_RATE_LIMITED_LOG_INTERVAL_S = 60.0

# Decrypt failures can fire once per row per read (a 50-row History page
_log_rate_lock = threading.Lock()
_log_rate_last: dict[str, float] = {}


def _rate_limited_log(level: int, key: str, msg: str, *args: object) -> None:
    """Emit ``log.log(level, msg, *args)`` at most once per interval per key."""
    now = time.monotonic()
    with _log_rate_lock:
        last = _log_rate_last.get(key, 0.0)
        if now - last < _RATE_LIMITED_LOG_INTERVAL_S:
            return
        _log_rate_last[key] = now
    log.log(level, msg, *args)


def log_key_unavailable_error() -> None:
    """Rate-limited ERROR for the key-unavailable state."""
    _rate_limited_log(
        logging.ERROR,
        "history:key-unavailable",
        "[HISTORY] encrypted rows exist but the data-encryption key is "
        "unavailable, returning '<decryption failed>' placeholders and "
        "writing new rows in plaintext; the DEK was NOT regenerated "
        "(regenerating would orphan the existing encrypted rows)",
    )


def _get_aesgcm(dek: bytes):
    """Return an ``AESGCM`` instance for ``dek`` (validated for 32 bytes)."""
    if not _CRYPTOGRAPHY_AVAILABLE or _AESGCM is None:
        raise RuntimeError(
            "cryptography package is not installed, at-rest encryption unavailable; history continues in plaintext mode"
        )
    if not isinstance(dek, bytes | bytearray) or len(dek) != _DEK_LENGTH:
        raise ValueError(
            f"DEK must be {_DEK_LENGTH} bytes for AES-256 "
            f"(got {len(dek) if isinstance(dek, bytes | bytearray) else type(dek).__name__})"
        )
    return _AESGCM(bytes(dek))


def is_encrypted(text: str) -> bool:
    """Return True when ``text`` looks like an encrypted blob (prefix check)."""
    return isinstance(text, str) and text.startswith(BLOB_PREFIX)


def _encrypt_with_nonce(plaintext: str, dek: bytes, nonce: bytes) -> str:
    """Encrypt with an explicit nonce (test/known-answer hook)."""
    if len(nonce) != _NONCE_LENGTH:
        raise ValueError(f"nonce must be {_NONCE_LENGTH} bytes (got {len(nonce)})")
    aes = _get_aesgcm(dek)
    # AESGCM.encrypt returns ciphertext || tag(16B).
    body = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return BLOB_PREFIX + base64.b64encode(nonce + body).decode("ascii")


def encrypt_text(plaintext: str, dek: bytes) -> str:
    """Encrypt ``plaintext`` into a self-describing blob string."""
    return _encrypt_with_nonce(plaintext, dek, os.urandom(_NONCE_LENGTH))


def decrypt_text(blob: str, dek: bytes) -> str:
    """Decrypt a blob produced by :func:`encrypt_text`."""
    if not isinstance(blob, str) or not blob.startswith(BLOB_PREFIX):
        # Covers plaintext (no prefix), a future "enc:v2:" blob (unknown
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:format",
            "[HISTORY] refusing to decrypt a row whose text is not a v1 "
            "ciphertext blob, returning '<decryption failed>'",
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    rest = blob[len(BLOB_PREFIX) :]
    try:
        raw = base64.b64decode(rest, validate=True)
    except Exception as e:  # noqa: BLE001, corrupt row must not crash reads
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:base64",
            "[HISTORY] ciphertext blob is not valid base64 (%s), returning '<decryption failed>'",
            type(e).__name__,
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    if len(raw) < _NONCE_LENGTH + _TAG_LENGTH:
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:truncated",
            "[HISTORY] ciphertext blob is truncated (%d bytes; need at least %d), returning '<decryption failed>'",
            len(raw),
            _NONCE_LENGTH + _TAG_LENGTH,
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    nonce, body = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
    try:
        aes = _get_aesgcm(dek)
        plaintext_bytes = aes.decrypt(nonce, body, associated_data=None)
    except Exception as e:  # noqa: BLE001, includes InvalidTag + bad DEK
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:auth",
            "[HISTORY] ciphertext authentication failed (%s, wrong key or "
            "tampered data), returning '<decryption failed>'",
            type(e).__name__,
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    try:
        return plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # GCM authenticated the bytes, so this is data written by a
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:utf8",
            "[HISTORY] decrypted bytes are not valid UTF-8, returning '<decryption failed>'",
        )
        return DECRYPTION_FAILED_PLACEHOLDER


# ``resolve_dek`` runs once per process (from the HistoryDB writer thread
_dek_cache: bytes | None = None
_dek_resolved: bool = False
_dek_lock = threading.Lock()


def get_dek_cached() -> bytes | None:
    """Return the resolved DEK, or ``None`` (unavailable / disabled)."""
    with _dek_lock:
        return _dek_cache


def resolve_dek(encrypted_rows_exist: bool) -> bytes | None:
    """Resolve the DEK once per process. Return the DEK or ``None``."""
    global _dek_cache, _dek_resolved
    with _dek_lock:
        if _dek_resolved:
            return _dek_cache
        # If the crypto backend is missing, don't load/generate a DEK —
        if not _CRYPTOGRAPHY_AVAILABLE:
            _dek_resolved = True
            _dek_cache = None
            if encrypted_rows_exist:
                # The DEK may be perfectly healthy in the keyring, the
                _rate_limited_log(
                    logging.ERROR,
                    "history:crypto-missing",
                    "[HISTORY] the 'cryptography' package is not installed in this "
                    "runtime, encrypted history rows cannot be decrypted and new "
                    "rows are written in plaintext. The data-encryption key is NOT "
                    "lost: reinstalling the application (or installing the "
                    "'cryptography' package into the runtime environment) restores "
                    "decryption on the next start.",
                )
            return None
        dek = _dek.load_dek()
        if dek is None and not encrypted_rows_exist and _cs.is_keyring_available():
            candidate = _dek.generate_dek()
            if _dek.store_dek(candidate):
                log.info(
                    "[HISTORY] generated a new data-encryption key in the OS keyring, at-rest encryption is now active"
                )
                dek = candidate
            # else: store_dek already logged; dek stays None → plaintext.
        elif dek is None and encrypted_rows_exist:
            # Key loss, surface via the shared rate-limited ERROR so the
            log_key_unavailable_error()
        _dek_cache = dek
        _dek_resolved = True
        return _dek_cache


def reset_dek_cache() -> None:
    """Test hook: forget the resolved DEK (simulates a fresh process)."""
    global _dek_cache, _dek_resolved
    with _dek_lock:
        _dek_cache = None
        _dek_resolved = False


def encryption_status(dek: bytes | None, encrypted_rows_exist: bool) -> str:
    """Map ``(dek, encrypted_rows_exist)`` to a status string.

    Returns ``"active"`` (DEK available, new rows are encrypted),
    """
    if dek is not None:
        return "active"
    if encrypted_rows_exist:
        return "key-unavailable"
    return "disabled"


__all__ = [
    "BLOB_PREFIX",
    "DECRYPTION_FAILED_PLACEHOLDER",
    "decrypt_text",
    "encrypt_text",
    "encryption_status",
    "get_dek_cached",
    "is_encrypted",
    "log_key_unavailable_error",
    "reset_dek_cache",
    "resolve_dek",
]
