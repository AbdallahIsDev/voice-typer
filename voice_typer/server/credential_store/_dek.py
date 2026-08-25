"""Data-encryption-key (DEK) storage in the OS keyring.

Owns the at-rest-encryption key lifecycle for the history database
(see ``docs/adr/XZ-R11-04-at-rest-encryption.md`` §4.3):

- :func:`generate_dek` — 32 random bytes (``os.urandom``), the AES-256 key.
- :func:`store_dek` — persist the DEK under the existing
  :data:`~voice_typer.server.credential_store._schema.KEYRING_SERVICE_NAME`
  service with the reserved
  :data:`~voice_typer.server.credential_store._schema.DATA_ENCRYPTION_KEY_USERNAME`
  username.
- :func:`load_dek` — read it back, or ``None`` when absent/unavailable.

Why this module bypasses ``store_secret`` / ``load_secret``
-----------------------------------------------------------

``store_secret`` (``_crud.py``) rejects providers not in
:data:`PROVIDER_TO_CONFIG_FIELD` — a guard that keeps orphaned keychain
entries from accumulating. The DEK is not a cloud provider secret, so it
must not flow through that path (nor through its plaintext-``config.json``
fallback, which would defeat the purpose of encrypting the history DB:
a DEK stored next to the ciphertext protects nothing — ADR §9.3). Instead,
the raw ``keyring.set_password`` / ``keyring.get_password`` calls are made
directly through :func:`~voice_typer.server.credential_store._backend._run_keyring_call`
— exactly how ``_probe_keyring`` performs its benign read — so the existing
timeout isolation (5s worker thread + orphan/wedge tracking) applies
unchanged.

Cross-platform note (E11): the ``keyring`` library selects the OS-native
backend (Windows Credential Manager / macOS Keychain / Linux SecretService);
this module contains zero platform-specific code. When no usable backend
exists (headless Linux), ``is_keyring_available`` returns False and the
callers fall back to plaintext history — never an on-disk DEK.

Keyring transports only strings, so the 32 raw bytes are base64-encoded
for storage and decoded on load. A stored value that does not decode to
exactly 32 bytes is treated as absent (logged, ``None`` returned) — a
corrupt keychain entry must never surface as a bogus key that silently
produces undecryptable ciphertext.

Failure policy: neither function ever raises. Failures (backend timeout,
D-Bus error, locked keychain) are logged at WARNING with the exception
*class name only* — keyring exception messages can embed backend-specific
diagnostics, and while the credential store redacts them, a DEK read/write
has nothing user-actionable worth echoing, so we stay maximally terse.
"""

from __future__ import annotations

import base64
import os
import sys

#: Look up the package module (same pattern as ``_backend._cs``) so
#: test-time monkeypatches on ``voice_typer.server.credential_store``
#: (``is_keyring_available``, ``_run_keyring_call``, ``_probe_keyring``)
#: are observed by every call site here at call time, not snapshotted at
#: import time.
_cs = sys.modules["voice_typer.server.credential_store"]

#: DEK length in bytes — AES-256 requires a 32-byte key.
_DEK_LENGTH_BYTES = 32


def generate_dek() -> bytes:
    """Return a fresh 32-byte data-encryption key (AES-256)."""
    return os.urandom(_DEK_LENGTH_BYTES)


def store_dek(dek: bytes) -> bool:
    """Persist ``dek`` in the OS keyring. Return True on success.

    Never raises. Returns ``False`` (with a WARNING) when the keyring is
    unavailable, the write times out, or ``dek`` has the wrong length.
    Callers MUST treat ``False`` as "the key could not be persisted" and
    stay in plaintext mode — encrypting with a key that never made it to
    the keychain would lose the data on next launch.
    """
    if not isinstance(dek, bytes | bytearray) or len(dek) != _DEK_LENGTH_BYTES:
        _cs.log.warning(
            "[CREDENTIAL_STORE] refusing to store DEK with invalid length "
            "(expected %d bytes, got %r) — staying in plaintext history mode",
            _DEK_LENGTH_BYTES,
            len(dek) if isinstance(dek, bytes | bytearray) else type(dek).__name__,
        )
        return False
    if not _cs.is_keyring_available():
        _cs.log.warning(
            "[CREDENTIAL_STORE] cannot store DEK — keyring backend unavailable "
            "(history stays in plaintext mode; no on-disk key fallback)"
        )
        return False
    try:
        import keyring  # type: ignore[import-not-found]

        from ._schema import DATA_ENCRYPTION_KEY_USERNAME, KEYRING_SERVICE_NAME

        # Base64 transport: keyring backends store strings. Encoding the
        # 32 raw bytes keeps the entry opaque and backend-agnostic.
        encoded = base64.b64encode(bytes(dek)).decode("ascii")
        _cs._run_keyring_call(
            keyring.set_password,
            KEYRING_SERVICE_NAME,
            DATA_ENCRYPTION_KEY_USERNAME,
            encoded,
        )
        return True
    except Exception as e:  # noqa: BLE001 — never raise on keyring failure
        _cs.log.warning(
            "[CREDENTIAL_STORE] storing DEK in the OS keyring failed (%s) — history stays in plaintext mode",
            type(e).__name__,
        )
        return False


def load_dek() -> bytes | None:
    """Load the DEK from the OS keyring, or ``None`` when absent/unavailable.

    Never raises. Returns ``None`` when the keyring is unavailable, the
    read times out, no entry exists yet, or the stored value does not
    decode to exactly 32 bytes (treated as absent — a corrupt entry must
    not become a bogus key).
    """
    if not _cs.is_keyring_available():
        # Expected on headless Linux (fail.Keyring backend) — DEBUG, not
        # WARNING: this is the documented plaintext-passthrough mode, not
        # an error condition.
        _cs.log.debug("[CREDENTIAL_STORE] not loading DEK — keyring backend unavailable (plaintext history mode)")
        return None
    try:
        import keyring  # type: ignore[import-not-found]

        from ._schema import DATA_ENCRYPTION_KEY_USERNAME, KEYRING_SERVICE_NAME

        encoded = _cs._run_keyring_call(
            keyring.get_password,
            KEYRING_SERVICE_NAME,
            DATA_ENCRYPTION_KEY_USERNAME,
        )
    except Exception as e:  # noqa: BLE001 — never raise on keyring failure
        _cs.log.warning(
            "[CREDENTIAL_STORE] loading DEK from the OS keyring failed (%s)",
            type(e).__name__,
        )
        return None
    if not encoded:
        return None  # first run — no DEK generated yet
    try:
        dek = base64.b64decode(encoded, validate=True)
    except Exception as e:  # noqa: BLE001 — corrupt keychain entry
        _cs.log.warning(
            "[CREDENTIAL_STORE] stored DEK is not valid base64 (%s) — treating "
            "as absent; regenerate only when no encrypted rows exist",
            type(e).__name__,
        )
        return None
    if len(dek) != _DEK_LENGTH_BYTES:
        _cs.log.warning(
            "[CREDENTIAL_STORE] stored DEK has unexpected length %d (expected %d bytes) — treating as absent",
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
