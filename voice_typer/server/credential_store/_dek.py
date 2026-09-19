"""Data-encryption-key (DEK) storage in the OS keyring."""

from __future__ import annotations

import base64
import os
import sys

#: Look up the package module (same pattern as ``_backend._cs``) so
_cs = sys.modules["voice_typer.server.credential_store"]

#: DEK length in bytes. AES-256 requires a 32-byte key.
_DEK_LENGTH_BYTES = 32


def generate_dek() -> bytes:
    """Return a fresh 32-byte data-encryption key (AES-256)."""
    return os.urandom(_DEK_LENGTH_BYTES)


def store_dek(dek: bytes) -> bool:
    """Persist ``dek`` in the OS keyring. Return True on success."""
    if not isinstance(dek, bytes | bytearray) or len(dek) != _DEK_LENGTH_BYTES:
        _cs.log.warning(
            "[CREDENTIAL_STORE] refusing to store DEK with invalid length "
            "(expected %d bytes, got %r), staying in plaintext history mode",
            _DEK_LENGTH_BYTES,
            len(dek) if isinstance(dek, bytes | bytearray) else type(dek).__name__,
        )
        return False
    if not _cs.is_keyring_available():
        _cs.log.warning(
            "[CREDENTIAL_STORE] cannot store DEK, keyring backend unavailable "
            "(history stays in plaintext mode; no on-disk key fallback)"
        )
        return False
    try:
        import keyring  # type: ignore[import-not-found]

        from ._schema import DATA_ENCRYPTION_KEY_USERNAME, KEYRING_SERVICE_NAME

        # Base64 transport: keyring backends store strings. Encoding the
        encoded = base64.b64encode(bytes(dek)).decode("ascii")
        _cs._run_keyring_call(
            keyring.set_password,
            KEYRING_SERVICE_NAME,
            DATA_ENCRYPTION_KEY_USERNAME,
            encoded,
        )
        return True
    except Exception as e:  # noqa: BLE001, never raise on keyring failure
        _cs.log.warning(
            "[CREDENTIAL_STORE] storing DEK in the OS keyring failed (%s), history stays in plaintext mode",
            type(e).__name__,
        )
        return False


def load_dek() -> bytes | None:
    """Load the DEK from the OS keyring, or ``None`` when absent/unavailable."""
    if not _cs.is_keyring_available():
        # Expected on headless Linux (fail.Keyring backend), DEBUG, not
        _cs.log.debug("[CREDENTIAL_STORE] not loading DEK, keyring backend unavailable (plaintext history mode)")
        return None
    try:
        import keyring  # type: ignore[import-not-found]

        from ._schema import DATA_ENCRYPTION_KEY_USERNAME, KEYRING_SERVICE_NAME

        encoded = _cs._run_keyring_call(
            keyring.get_password,
            KEYRING_SERVICE_NAME,
            DATA_ENCRYPTION_KEY_USERNAME,
        )
    except Exception as e:  # noqa: BLE001, never raise on keyring failure
        _cs.log.warning(
            "[CREDENTIAL_STORE] loading DEK from the OS keyring failed (%s)",
            type(e).__name__,
        )
        return None
    if not encoded:
        return None  # first run, no DEK generated yet
    try:
        dek = base64.b64decode(encoded, validate=True)
    except Exception as e:  # noqa: BLE001, corrupt keychain entry
        _cs.log.warning(
            "[CREDENTIAL_STORE] stored DEK is not valid base64 (%s), treating "
            "as absent; regenerate only when no encrypted rows exist",
            type(e).__name__,
        )
        return None
    if len(dek) != _DEK_LENGTH_BYTES:
        _cs.log.warning(
            "[CREDENTIAL_STORE] stored DEK has unexpected length %d (expected %d bytes), treating as absent",
            len(dek),
            _DEK_LENGTH_BYTES,
        )
        return None
    return dek


__all__ = [
    "generate_dek",
    "load_dek",
    "store_dek",
]
