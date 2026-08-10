"""``Config._last_saved_bytes`` retains plaintext API keys after GDPR delete.

``Config._last_saved_bytes`` is a serialized-JSON bytes cache stashed
on the Config dataclass after every successful ``Config.save()``. When
keyring is unavailable, ``Config.save()`` writes the REAL plaintext
API keys to disk (routing in ``Config._save_unlocked`` only
replaces the value with a ``keyring://<provider>`` reference token
when keyring is available), and stashes the serialized bytes on the
long-lived Config instance.

``clear_in_memory_secrets`` only ``setattr``s the dataclass fields to
``""`` — it doesn't trigger a save, so the byte cache holds the
PRE-clear plaintext JSON until the next successful save (which may be
never if the user doesn't change settings again before closing the
app).

fix: in ``clear_in_memory_secrets(config)``, after the
``setattr`` loop, also call
``object.__setattr__(config, '_last_saved_bytes', None)`` to
invalidate the byte cache. ``object.__setattr__`` bypasses any frozen-
dataclass ``__setattr__`` override so the clear works even on a frozen
Config.

This test file asserts:

  1. ``_last_saved_bytes`` is ``None`` after
     ``clear_in_memory_secrets()`` (was non-None before).
  2. The clear works on a frozen dataclass (where regular ``setattr``
     would raise).
  3. The clear is best-effort — a config object whose
     ``object.__setattr__`` raises (exotic ``__setattr__`` override
     that calls ``object.__setattr__`` for unrelated attrs but raises
     for ``_last_saved_bytes``) doesn't break the rest of
     ``clear_in_memory_secrets``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store

# ── Fixtures (mirrors tests/test_credential_store.py) ───────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache and the plaintext config
    cache so each test re-probes / re-populates from scratch.
    """
    credential_store._reset_keyring_cache()
    credential_store._clear_plaintext_config_cache()
    yield
    credential_store._reset_keyring_cache()
    credential_store._clear_plaintext_config_cache()


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing).

    specifically affects the plaintext-fallback path — when
    keyring is available, plaintext API keys never reach
    ``Config._last_saved_bytes`` in the first place.
    """

    class _FailKeyring:
        name = "fail"

        def get_password(self, service, username):
            raise RuntimeError("no backend available")

        def set_password(self, service, username, password):
            raise RuntimeError("no backend available")

        def delete_password(self, service, username):
            raise RuntimeError("no backend available")

    fail_backend = _FailKeyring()
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = fail_backend

    fail_module = MagicMock()
    fail_module.Keyring = _FailKeyring
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
    )
    return fake_keyring


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_config_with_last_saved_bytes(*, plaintext_bytes: bytes | None) -> MagicMock:
    """Build a fake Config whose ``_last_saved_bytes`` is set.

    Mirrors the real ``Config`` dataclass's ``__post_init__`` +
    ``save()`` flow: ``_last_saved_bytes`` starts as ``None`` and is
    stashed after every successful save. We use a ``MagicMock`` with
    spec covering every provider field so ``setattr(config, field, '')``
    inside ``clear_in_memory_secrets`` doesn't raise (a plain
    ``MagicMock()`` accepts any setattr).
    """
    config = MagicMock()
    # Set every provider field to a non-empty value (simulating a Config
    # that was just loaded with plaintext API keys).
    for field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.values():
        setattr(config, field_name, f"sk-{field_name}")
    # Stash the byte cache as the real Config.save() would.
    # ``object.__setattr__`` works on MagicMock because MagicMock
    # doesn't override __setattr__ to raise.
    object.__setattr__(config, "_last_saved_bytes", plaintext_bytes)
    return config


# clear_in_memory_secrets clears _last_saved_bytes ─────────────


class TestClearInMemorySecretsClearsLastSavedBytes:
    """``clear_in_memory_secrets(config)`` must set ``_last_saved_bytes = None``."""

    def test_last_saved_bytes_cleared_after_clear_in_memory_secrets(self):
        """``_last_saved_bytes`` (containing serialized plaintext
        API keys) must be ``None`` after ``clear_in_memory_secrets()``."""
        # Arrange: a Config with non-None _last_saved_bytes (simulating
        # a Config that was just saved with plaintext API keys because
        # keyring was unavailable).
        plaintext_bytes = b'{"openai_api_key": "sk-leaked-via-bytes"}'
        config = _make_config_with_last_saved_bytes(plaintext_bytes=plaintext_bytes)
        assert object.__getattribute__(config, "_last_saved_bytes") == plaintext_bytes

        # Act.
        credential_store.clear_in_memory_secrets(config)

        # Assert: the byte cache is invalidated ().
        assert object.__getattribute__(config, "_last_saved_bytes") is None, (
            "_last_saved_bytes must be None after clear_in_memory_secrets() "
            "so a memory dump between the GDPR delete and the next app restart "
            "doesn't expose the serialized plaintext API keys"
        )

    def test_last_saved_bytes_cleared_even_if_already_none(self):
        """the clear is idempotent — setting None to None is a no-op."""
        config = _make_config_with_last_saved_bytes(plaintext_bytes=None)
        assert object.__getattribute__(config, "_last_saved_bytes") is None

        # Must not raise.
        credential_store.clear_in_memory_secrets(config)

        assert object.__getattribute__(config, "_last_saved_bytes") is None

    def test_last_saved_bytes_cleared_on_frozen_dataclass(self):
        """the clear must work on a frozen dataclass, where
        regular ``setattr(config, '_last_saved_bytes', None)`` would
        raise ``FrozenInstanceError``. We use ``object.__setattr__`` to
        bypass the frozen-dataclass ``__setattr__`` override."""

        @dataclass(frozen=True)
        class _FrozenConfig:
            openai_api_key: str = "sk-openai"
            groq_api_key: str = "gsk_groq"
            deepgram_api_key: str = "dg-deepgram"
            cloud_api_key: str = "cl-cloud"
            llm_api_key: str = "llm-llm"
            _last_saved_bytes: bytes | None = field(default=None, repr=False)

            def __post_init__(self):
                # Simulate a Config that was just saved with plaintext
                # API keys (keyring unavailable → no reference-token
                # replacement in _save_unlocked).
                object.__setattr__(self, "_last_saved_bytes", b'{"openai_api_key":"sk-leak"}')

        config = _FrozenConfig()
        assert object.__getattribute__(config, "_last_saved_bytes") is not None

        # Act — clear_in_memory_secrets wraps each setattr in try/except
        # so the frozen-dataclass setattr failures on the api_key fields
        # don't abort the loop. The _last_saved_bytes clear uses
        # ``object.__setattr__`` so it succeeds even on a frozen dataclass.
        cleared = credential_store.clear_in_memory_secrets(config)

        # Assert: _last_saved_bytes is None ().
        assert object.__getattribute__(config, "_last_saved_bytes") is None, (
            "_last_saved_bytes must be None after clear_in_memory_secrets() "
            "even on a frozen dataclass (uses object.__setattr__)"
        )
        # The api_key fields are still set to their original values
        # because the frozen dataclass raised on setattr — that's the
        # documented best-effort behavior. The cleared count is 0
        # because every setattr raised.
        assert cleared == 0
        assert config.openai_api_key == "sk-openai"

    def test_last_saved_bytes_clear_is_best_effort(self):
        """if ``object.__setattr__`` raises (e.g. a class with
        ``__slots__`` that doesn't declare ``_last_saved_bytes``), the
        failure is logged at debug and swallowed so the rest of
        clear_in_memory_secrets still completes. We verify by using a
        class with ``__slots__`` — ``object.__setattr__`` raises
        ``AttributeError`` because the slot doesn't exist."""

        class _SlotsConfig:
            # ``__slots__`` WITHOUT ``_last_saved_bytes`` — so
            # ``object.__setattr__(config, '_last_saved_bytes', None)``
            # raises ``AttributeError`` (no slot to hold the value).
            __slots__ = list(credential_store.PROVIDER_TO_CONFIG_FIELD.values())

            def __init__(self):
                for field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.values():
                    setattr(self, field_name, f"sk-{field_name}")

        config = _SlotsConfig()
        # Sanity: the api_key fields are set.
        for field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.values():
            assert getattr(config, field_name) == f"sk-{field_name}"
        # Sanity: object.__setattr__ raises for _last_saved_bytes.
        with pytest.raises(AttributeError, match="_last_saved_bytes"):
            object.__setattr__(config, "_last_saved_bytes", None)

        # Act — must NOT raise (the clear is best-effort; the
        # AttributeError is caught + logged at debug).
        cleared = credential_store.clear_in_memory_secrets(config)

        # Assert: all 5 api_key fields were cleared (the loop completed
        # — the _last_saved_bytes failure didn't abort the rest).
        assert cleared == 5
        for field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.values():
            assert getattr(config, field_name) == ""
