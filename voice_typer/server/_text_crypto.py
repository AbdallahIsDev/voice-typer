"""Application-layer AES-256-GCM encryption for dictated history text.

This is the ONE canonical crypto module for the history at-rest-encryption
feature (E7 — no parallel crypto subsystems). Design gate:
``docs/adr/XZ-R11-04-at-rest-encryption.md`` §2 / §4.

Cipher
------
AES-256-GCM via ``cryptography.hazmat.primitives.ciphers.aead.AESGCM``:

- 256-bit key (the DEK — 32 raw bytes from the OS keyring, see
  ``voice_typer/server/credential_store/_dek.py``).
- 96-bit random nonce per encryption (``os.urandom(12)``).
- 128-bit authentication tag appended by ``AESGCM.encrypt``.

On-disk blob format (stored in the existing ``transcriptions.text`` TEXT
column, so no column-type migration is needed)::

    "enc:v1:" + base64( nonce(12) || ciphertext || tag(16) )

The ASCII prefix makes plaintext-vs-ciphertext detection trivial and
version-safe: ``is_encrypted()`` is a ``startswith`` check, and a future
cipher-suite bump ("enc:v2:") is distinguishable without parsing. The
per-row ``text_is_encrypted`` SQLite flag remains the authoritative
detector — the prefix check is a defense-in-depth cross-check used by
tests and diagnostics.

Failure policy (never crash the dictation hot path)
---------------------------------------------------
:func:`decrypt_text` NEVER raises and NEVER passthrough-decodes: any
failure (unknown version prefix, malformed base64, truncated blob, wrong
key, tampered tag → ``InvalidTag``) returns the placeholder
``"<decryption failed>"`` after a rate-limited WARNING. The row's
metadata (timestamp, model, duration, ...) stays readable so the user
can delete the row manually.

DEK cache
---------
The DEK is resolved ONCE per process (:func:`resolve_dek`) and then read
from the module-level cache (:func:`get_dek_cached`) by the history
writer thread and the read seams — no per-call keyring I/O (ADR §7.4).
Resolution policy (key-loss policy — stricter than ADR §9):

- DEK present in the keyring → use it (encryption active).
- DEK absent AND no encrypted rows exist AND the keyring is available →
  generate + store a new DEK (first run).
- DEK absent AND encrypted rows exist → **key-unavailable**: NEVER
  regenerate (a fresh key cannot decrypt the existing rows; regenerating
  would silently orphan them). Reads of encrypted rows return the
  placeholder; NEW writes stay plaintext (flag 0) so no further rows
  are lost. ``HistoryDB.encryption_status()`` surfaces this state.

No on-disk DEK fallback exists (ADR §9.3): encrypting user data with a
key stored next to the ciphertext provides zero security.

Offline guarantee: this module performs ZERO network calls (C-DATA-1).
The only I/O is the OS-keychain IPC via ``credential_store``.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time

from voice_typer.server import credential_store as _cs
from voice_typer.server.credential_store import _dek

log = logging.getLogger(__name__)

# Lazy-once availability probe for the ``cryptography`` dependency. It
# is a base dependency (pyproject.toml) so a normal install provides
# it, but a frozen/stripped runtime (Nuitka onefile, a minimal venv
# that predates the dependency) may lack it. When missing, this module
# degrades to the documented plaintext mode instead of raising
# ModuleNotFoundError on every encrypt/decrypt call — which previously
# surfaced as repeated ``[HISTORY_DB] Fire-and-forget write failed:
# No module named 'cryptography'`` errors in the history writer thread.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    _AESGCM = None
    _CRYPTOGRAPHY_AVAILABLE = False

#: Blob prefix — version tag for the on-disk ciphertext format.
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

# ── Rate-limited logging ─────────────────────────────────────────────────
#
# Decrypt failures can fire once per row per read (a 50-row History page
# with a lost key would otherwise emit 50 identical WARNINGs per render,
# and the key-loss ERROR would repeat on every keystroke in the search
# box). A tiny module-level keyed rate limiter keeps the log readable
# without silencing distinct failure sites.
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
    """Rate-limited ERROR for the key-unavailable state.

    Called by the history read seams when a row is flagged encrypted but
    no DEK is available (keyring wiped/unavailable). Distinct from the
    first-run plaintext-passthrough mode, which logs nothing.
    """
    _rate_limited_log(
        logging.ERROR,
        "history:key-unavailable",
        "[HISTORY] encrypted rows exist but the data-encryption key is "
        "unavailable — returning '<decryption failed>' placeholders and "
        "writing new rows in plaintext; the DEK was NOT regenerated "
        "(regenerating would orphan the existing encrypted rows)",
    )


# ── Cipher primitives ────────────────────────────────────────────────────


def _get_aesgcm(dek: bytes):
    """Return an ``AESGCM`` instance for ``dek`` (validated for 32 bytes)."""
    if not _CRYPTOGRAPHY_AVAILABLE or _AESGCM is None:
        raise RuntimeError(
            "cryptography package is not installed — at-rest encryption "
            "unavailable; history continues in plaintext mode"
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
    """Encrypt with an explicit nonce (test/known-answer hook).

    Production code uses :func:`encrypt_text` (fresh random nonce); tests
    pin the nonce to assert exact ciphertext bytes.
    """
    if len(nonce) != _NONCE_LENGTH:
        raise ValueError(f"nonce must be {_NONCE_LENGTH} bytes (got {len(nonce)})")
    aes = _get_aesgcm(dek)
    # AESGCM.encrypt returns ciphertext || tag(16B).
    body = aes.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return BLOB_PREFIX + base64.b64encode(nonce + body).decode("ascii")


def encrypt_text(plaintext: str, dek: bytes) -> str:
    """Encrypt ``plaintext`` into a self-describing blob string.

    Random 96-bit nonce per call — never reused with the same key (NIST
    SP 800-38D random-nonce budget is ~2^32 invocations per key, far
    beyond a human lifetime of dictation).
    """
    return _encrypt_with_nonce(plaintext, dek, os.urandom(_NONCE_LENGTH))


def decrypt_text(blob: str, dek: bytes) -> str:
    """Decrypt a blob produced by :func:`encrypt_text`.

    Never raises and never passthrough-decodes: on ANY failure (unknown
    version prefix, malformed base64, truncated body, wrong key,
    ``InvalidTag``) returns :data:`DECRYPTION_FAILED_PLACEHOLDER` after a
    rate-limited WARNING. The caller decides whether the row is worth
    surfacing; the metadata columns remain readable either way.
    """
    if not isinstance(blob, str) or not blob.startswith(BLOB_PREFIX):
        # Covers plaintext (no prefix), a future "enc:v2:" blob (unknown
        # version — this build cannot decode it), and non-str garbage.
        # Never passthrough-decode: the flagged row is corrupted from
        # this build's perspective.
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:format",
            "[HISTORY] refusing to decrypt a row whose text is not a v1 "
            "ciphertext blob — returning '<decryption failed>'",
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    rest = blob[len(BLOB_PREFIX) :]
    try:
        raw = base64.b64decode(rest, validate=True)
    except Exception as e:  # noqa: BLE001 — corrupt row must not crash reads
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:base64",
            "[HISTORY] ciphertext blob is not valid base64 (%s) — returning '<decryption failed>'",
            type(e).__name__,
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    if len(raw) < _NONCE_LENGTH + _TAG_LENGTH:
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:truncated",
            "[HISTORY] ciphertext blob is truncated (%d bytes; need at least %d) — returning '<decryption failed>'",
            len(raw),
            _NONCE_LENGTH + _TAG_LENGTH,
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    nonce, body = raw[:_NONCE_LENGTH], raw[_NONCE_LENGTH:]
    try:
        aes = _get_aesgcm(dek)
        plaintext_bytes = aes.decrypt(nonce, body, associated_data=None)
    except Exception as e:  # noqa: BLE001 — includes InvalidTag + bad DEK
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:auth",
            "[HISTORY] ciphertext authentication failed (%s — wrong key or "
            "tampered data) — returning '<decryption failed>'",
            type(e).__name__,
        )
        return DECRYPTION_FAILED_PLACEHOLDER
    try:
        return plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # GCM authenticated the bytes, so this is data written by a
        # non-UTF-8 path — treat as corruption, never crash.
        _rate_limited_log(
            logging.WARNING,
            "history:decrypt:utf8",
            "[HISTORY] decrypted bytes are not valid UTF-8 — returning '<decryption failed>'",
        )
        return DECRYPTION_FAILED_PLACEHOLDER


# ── Process-lifetime DEK cache ───────────────────────────────────────────
#
# ``resolve_dek`` runs once per process (from the HistoryDB writer thread
# after schema init — see ``history_db._init_encryption``) and ``get_dek_cached``
# is the zero-I/O accessor used by the insert paths and the read seams.
# ``reset_dek_cache`` exists for tests that need to simulate a fresh
# process or a keyring that changes state between HistoryDB instances.
_dek_cache: bytes | None = None
_dek_resolved: bool = False
_dek_lock = threading.Lock()


def get_dek_cached() -> bytes | None:
    """Return the resolved DEK, or ``None`` (unavailable / disabled).

    Does NOT touch the keyring and does NOT generate: if
    :func:`resolve_dek` has not run yet (or resolved to ``None``), this
    returns ``None`` and callers take the plaintext path. That keeps the
    write/read seams free of keyring I/O and mid-session key churn.
    """
    with _dek_lock:
        return _dek_cache


def resolve_dek(encrypted_rows_exist: bool) -> bytes | None:
    """Resolve the DEK once per process. Return the DEK or ``None``.

    Policy (key-loss policy — see module docstring):

    1. Load the DEK from the keyring. Present → cache + return it.
    2. Absent, keyring available, and ``encrypted_rows_exist`` is False →
       generate a new DEK and store it. If the store fails, return
       ``None`` (plaintext mode) — never encrypt with an unstorable key.
    3. Absent and ``encrypted_rows_exist`` is True → key loss: return
       ``None`` WITHOUT regenerating. The existing ciphertext is
       undecryptable with any new key; regenerating would silently
       orphan it.
    4. Absent and the keyring is unavailable → ``None`` (plaintext
       passthrough mode — ADR §9.1).

    Idempotent: after the first call the cached result is returned as-is
    (the keyring is not re-probed) until :func:`reset_dek_cache` runs.
    """
    global _dek_cache, _dek_resolved
    with _dek_lock:
        if _dek_resolved:
            return _dek_cache
        # If the crypto backend is missing, don't load/generate a DEK —
        # the key would be unusable (every encrypt/decrypt call would
        # raise).  Plaintext mode — matches the documented degrade-to-
        # plaintext guarantee (the module docstring says "degrades to
        # plaintext if the import fails").
        if not _CRYPTOGRAPHY_AVAILABLE:
            _dek_resolved = True
            _dek_cache = None
            return None
        dek = _dek.load_dek()
        if dek is None and not encrypted_rows_exist and _cs.is_keyring_available():
            candidate = _dek.generate_dek()
            if _dek.store_dek(candidate):
                log.info(
                    "[HISTORY] generated a new data-encryption key in the OS keyring — at-rest encryption is now active"
                )
                dek = candidate
            # else: store_dek already logged; dek stays None → plaintext.
        elif dek is None and encrypted_rows_exist:
            # Key loss — surface via the shared rate-limited ERROR so the
            # log carries the same explanation the read seams emit.
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

    Returns ``"active"`` (DEK available — new rows are encrypted),
    ``"key-unavailable"`` (encrypted rows exist but no DEK — placeholder
    reads, plaintext writes, no regeneration), or ``"disabled"`` (no DEK
    and nothing encrypted — plain passthrough, identical to pre-encryption
    behavior).
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
