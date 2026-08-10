"""DE-23 regression tests for ``Config.save()`` non-string api_key guard.

These tests pin the DE-23 fix in ``voice_typer/server/config.py``'s
``Config.save()`` method (around lines 1191-1233 post-fix):

    if credential_store.is_keyring_available():
        for provider, field_name in credential_store.PROVIDER_TO_CONFIG_FIELD.items():
            value = data.get(field_name, "")
            # DE-23: defensive type guard for non-string api_key values.
            if not isinstance(value, str):
                if not value:
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    # Coerce to str (backward compat with old configs).
                    value = str(value)
                    data[field_name] = value
                else:
                    # dict / list / other — skip with warning.
                    continue
            if value and not value.startswith(credential_store.KEYRING_REF_PREFIX):
                credential_store.store_secret(provider, value)
                data[field_name] = f"{credential_store.KEYRING_REF_PREFIX}{provider}"

Pre-fix, a non-string ``api_key`` value in the in-memory ``Config``
instance (e.g. ``int`` from a hand-edited config or a buggy IPC
caller) would crash with ``AttributeError: 'int' object has no
attribute 'startswith'`` at ``value.startswith(...)``.  The crash
propagated up through ``Config.save()``'s outer ``except Exception``,
logging a warning and aborting the entire save — so the user's other
config changes were lost.

Post-fix:
- ``int`` / ``float`` values (excluding ``bool``) are coerced to
  ``str`` (backward compat with old configs that stored api_key as
  int — the value is preserved as a string).
- Other non-string truthy values (``dict``, ``list``, etc.) are
  skipped with a WARNING log so the user can locate the corrupted
  field, and the save proceeds for the remaining providers.
- Falsy values (``None``, ``0``, ``[]``, ``{}``, ``""``) are
  skipped silently (matches the historical ``not value`` short-circuit).

See:
- ``voice_typer/server/config.py`` (``Config.save()`` method)
- ``scripts/findings/DE-23.md``
- ``tests/test_credential_store_group_fixes.py`` (covers the
  ``migrate_secrets_to_keyring`` side of DE-23)
"""

from __future__ import annotations

import json
import logging

import pytest
from voice_typer.server.config import Config

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield tmp_config_dir


@pytest.fixture
def mock_keyring_available_in_config_save(monkeypatch):
    """Mock ``credential_store.is_keyring_available`` to return True
    AND capture ``store_secret`` calls so tests can assert what was
    routed through credential_store.

    Returns a dict with ``calls`` (list of (provider, value) tuples)
    and ``store`` (the in-memory keyring store) so tests can verify
    the secret was actually written.
    """
    calls: list[tuple[str, object]] = []
    store: dict[tuple[str, str], str] = {}

    # Late import so the fixture sees the post-patch state.
    from voice_typer.server import credential_store

    service_name = credential_store.KEYRING_SERVICE_NAME

    def fake_store_secret(provider, value, _caller_holds_config_lock=False, **_kwargs):
        calls.append((provider, value))
        store[(service_name, provider)] = value
        return True

    monkeypatch.setattr(credential_store, "is_keyring_available", lambda: True)
    monkeypatch.setattr(credential_store, "store_secret", fake_store_secret)
    return {"calls": calls, "store": store}


# ── Tests: int / float coercion ────────────────────────────────────────


class TestConfigSaveCoercesNumericApiKey:
    """DE-23: ``int`` / ``float`` api_key values are coerced to ``str``."""

    def test_int_api_key_coerced_to_str_and_routed(
        self, isolated_config_dir, mock_keyring_available_in_config_save, caplog
    ):
        """An ``int`` api_key on the Config instance must be coerced
        to ``str`` and routed through ``credential_store.store_secret``.

        Pre-fix: ``AttributeError: 'int' object has no attribute 'startswith'``
        crashed the entire save.
        """
        c = Config()
        # Bypass the dataclass field type (str) — simulate a buggy
        # caller or a hand-edited in-memory state.
        c.openai_api_key = 12345  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = c.save()

        assert result is True, "DE-23: Config.save() must succeed even with a non-string api_key"

        # The int was coerced to str "12345" and routed to credential_store.
        routed = mock_keyring_available_in_config_save["calls"]
        assert ("openai", "12345") in routed, (
            f"DE-23: int api_key 12345 should have been coerced to '12345' "
            f"and routed to credential_store.store_secret. Got: {routed!r}"
        )

        # A WARNING must be logged so the user knows the coercion happened.
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("openai_api_key" in msg and "non-string" in msg and "coercing" in msg for msg in warning_msgs), (
            f"DE-23: a WARNING must be logged when an int api_key is coerced. Got: {warning_msgs!r}"
        )

        # The on-disk config.json has the keyring:// reference, not the int.
        data = json.loads((isolated_config_dir / "config.json").read_text())
        assert data["openai_api_key"] == "keyring://openai", (
            f"DE-23: openai_api_key should be replaced with keyring reference. Got: {data.get('openai_api_key')!r}"
        )

    def test_float_api_key_coerced_to_str(self, isolated_config_dir, mock_keyring_available_in_config_save):
        """A ``float`` api_key (e.g. from a YAML config that parsed
        ``12345.0``) is also coerced to ``str``."""
        c = Config()
        c.groq_api_key = 67890.0  # type: ignore[assignment]

        result = c.save()

        assert result is True
        routed = mock_keyring_available_in_config_save["calls"]
        # float str representation: "67890.0"
        assert ("groq", "67890.0") in routed, (
            f"DE-23: float api_key 67890.0 should have been coerced to '67890.0' and routed. Got: {routed!r}"
        )


# ── Tests: dict / list rejection ───────────────────────────────────────


class TestConfigSaveSkipsNonStringApiKey:
    """DE-23: ``dict`` / ``list`` api_key values are skipped with a warning."""

    def test_dict_api_key_skipped_with_warning(
        self, isolated_config_dir, mock_keyring_available_in_config_save, caplog
    ):
        """A ``dict`` api_key must NOT crash the save and must be
        skipped (the dict cannot be coerced to a meaningful secret)."""
        c = Config()
        c.deepgram_api_key = {"secret": "sk-leaked"}  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = c.save()

        assert result is True, "DE-23: Config.save() must not crash on a dict api_key"

        routed = mock_keyring_available_in_config_save["calls"]
        assert all(provider != "deepgram" for provider, _ in routed), (
            f"DE-23: dict api_key should NOT have been routed to store_secret. Got: {routed!r}"
        )

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deepgram_api_key" in msg and "non-string" in msg and "skipping" in msg for msg in warning_msgs), (
            f"DE-23: a WARNING must be logged when a dict api_key is skipped. Got: {warning_msgs!r}"
        )

    def test_list_api_key_skipped_with_warning(
        self, isolated_config_dir, mock_keyring_available_in_config_save, caplog
    ):
        """A ``list`` api_key must NOT crash the save and must be skipped."""
        c = Config()
        c.cloud_api_key = ["sk-leaked"]  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = c.save()

        assert result is True

        routed = mock_keyring_available_in_config_save["calls"]
        assert all(provider != "cloud" for provider, _ in routed), (
            f"DE-23: list api_key should NOT have been routed to store_secret. Got: {routed!r}"
        )

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("cloud_api_key" in msg and "non-string" in msg for msg in warning_msgs), (
            f"DE-23: a WARNING must be logged when a list api_key is skipped. Got: {warning_msgs!r}"
        )

    def test_other_providers_still_migrated_when_one_is_non_string(
        self, isolated_config_dir, mock_keyring_available_in_config_save
    ):
        """DE-23 core guarantee: a non-string api_key for ONE provider
        must not prevent OTHER providers from being routed through
        credential_store. This is the "skip + continue" contract."""
        c = Config()
        c.openai_api_key = {"secret": "sk-leaked"}  # type: ignore[assignment]
        c.groq_api_key = "groq-sk-real-value"

        result = c.save()

        assert result is True
        routed = mock_keyring_available_in_config_save["calls"]
        # groq was routed; openai was NOT.
        assert ("groq", "groq-sk-real-value") in routed, (
            f"DE-23: groq_api_key should have been routed even when openai_api_key "
            f"is a non-string dict. Got: {routed!r}"
        )
        assert all(provider != "openai" for provider, _ in routed), (
            f"DE-23: openai (dict) should NOT have been routed. Got: {routed!r}"
        )


# ── Tests: falsy non-string values ─────────────────────────────────────


class TestConfigSaveFalsyNonStringApiKey:
    """DE-23: falsy non-string api_key values (``None``, ``0``, ``[]``,
    ``{}``) are skipped silently — matches the historical ``not value``
    short-circuit for empty strings."""

    @pytest.mark.parametrize("falsy_value", [None, 0, [], {}])
    def test_falsy_non_string_skipped_silently(
        self, isolated_config_dir, mock_keyring_available_in_config_save, falsy_value
    ):
        """A falsy non-string api_key (None, 0, [], {}) must be skipped
        silently — no warning, no routing, no crash."""
        c = Config()
        c.openai_api_key = falsy_value  # type: ignore[assignment]

        result = c.save()

        assert result is True
        routed = mock_keyring_available_in_config_save["calls"]
        assert all(provider != "openai" for provider, _ in routed), (
            f"DE-23: falsy non-string api_key {falsy_value!r} should NOT have been "
            f"routed to store_secret. Got: {routed!r}"
        )


# ── Tests: bool exclusion ──────────────────────────────────────────────


class TestConfigSaveBoolApiKey:
    """DE-23: ``bool`` values (``True`` / ``False``) are NOT coerced
    to str (``"True"`` / ``"False"`` are not meaningful secrets).
    Truthy bool is skipped with a warning; falsy bool (``False``) is
    skipped silently via the ``not value`` short-circuit."""

    def test_truthy_bool_skipped_with_warning(self, isolated_config_dir, mock_keyring_available_in_config_save, caplog):
        """``True`` is truthy but not int/float — must be skipped (not
        coerced to ``"True"``)."""
        c = Config()
        c.openai_api_key = True  # type: ignore[assignment]

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = c.save()

        assert result is True
        routed = mock_keyring_available_in_config_save["calls"]
        assert all(provider != "openai" for provider, _ in routed), (
            f"DE-23: bool api_key True should NOT have been routed. Got: {routed!r}"
        )
