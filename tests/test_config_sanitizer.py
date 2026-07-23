"""Tests for the transport-neutral config sanitizer.

EC-FIX-15 / EC-22: ``sanitize_config_for_ipc`` is the canonical
implementation that both :mod:`voice_typer.server.service` and
:mod:`voice_typer.server.ipc_server` import.  These tests pin its
contract (SEC-003: never echo secret values back to the IPC client) so
that any future refactor that moves the function or weakens its
redaction breaks loudly here.
"""

from __future__ import annotations

import pytest

from voice_typer.server.config_sanitizer import (
    REDACTED_SENTINEL,
    SECRET_CONFIG_FIELDS,
    sanitize_config_for_ipc,
)


class _FakeConfig:
    """Minimal stand-in for ``voice_typer.server.config.Config``.

    Mirrors the ``__dict__``-based reflection the sanitizer uses, so we
    can populate arbitrary attributes without depending on the real
    Config schema (which would couple this test to the schema validator
    and the on-disk config dir).
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestSecretFieldsRedacted:
    """Every field in :data:`SECRET_CONFIG_FIELDS` must be redacted."""

    @pytest.mark.parametrize("field", sorted(SECRET_CONFIG_FIELDS))
    def test_each_secret_field_is_redacted_when_truthy(self, field):
        real_value = f"sk-{field}-real-value-12345"
        cfg = _FakeConfig(**{field: real_value})
        result = sanitize_config_for_ipc(cfg)
        assert result[field] == REDACTED_SENTINEL
        # SEC-003: the real value must not appear anywhere in the
        # serialized output.
        assert real_value not in str(result)

    def test_all_known_secret_fields_covered(self):
        """Pin the exact set of secret fields so additions are explicit.

        If a new secret field is added to the config schema it MUST be
        added to :data:`SECRET_CONFIG_FIELDS` — otherwise it would be
        echoed back to the IPC client in plaintext.  This test forces
        the explicit update by failing when the set changes.
        """
        assert SECRET_CONFIG_FIELDS == frozenset(
            {
                "cloud_api_key",
                "openai_api_key",
                "groq_api_key",
                "deepgram_api_key",
                "llm_api_key",
            }
        )

    def test_falsy_secret_value_preserved_not_redacted(self):
        """Empty-string / None secrets are preserved so the renderer can
        distinguish "no key set" from "key set but hidden"."""
        cfg = _FakeConfig(
            cloud_api_key="",
            openai_api_key=None,
            groq_api_key="groq-real",
        )
        result = sanitize_config_for_ipc(cfg)
        assert result["cloud_api_key"] == ""
        assert result["openai_api_key"] is None
        assert result["groq_api_key"] == REDACTED_SENTINEL


class TestNonSecretFieldsPreserved:
    """Non-secret fields pass through unchanged."""

    def test_non_secret_fields_pass_through(self):
        cfg = _FakeConfig(
            hotkey="<f9>",
            language="fr",
            model_name="small.en",
            cloud_api_key="sk-real",
        )
        result = sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f9>"
        assert result["language"] == "fr"
        assert result["model_name"] == "small.en"
        assert result["cloud_api_key"] == REDACTED_SENTINEL

    def test_returns_plain_dict(self):
        cfg = _FakeConfig(hotkey="<f2>")
        result = sanitize_config_for_ipc(cfg)
        assert isinstance(result, dict)

    def test_does_not_mutate_input_config(self):
        """Sanitizing must not mutate the original Config object."""
        cfg = _FakeConfig(cloud_api_key="sk-original")
        sanitize_config_for_ipc(cfg)
        assert cfg.cloud_api_key == "sk-original"


class TestMissingFieldsHandledGracefully:
    """Older Config snapshots that lack a secret field must not crash."""

    def test_missing_secret_field_not_synthesized(self):
        cfg = _FakeConfig(hotkey="<f2>")  # no cloud_api_key attribute
        result = sanitize_config_for_ipc(cfg)
        assert result["hotkey"] == "<f2>"
        # The sanitizer must NOT add a cloud_api_key entry that wasn't
        # there — otherwise the renderer would see a phantom "<redacted>"
        # value for a key that was never set.
        assert "cloud_api_key" not in result

    def test_empty_config_returns_empty_dict(self):
        cfg = _FakeConfig()
        result = sanitize_config_for_ipc(cfg)
        assert result == {}


class TestSentinelValue:
    def test_sentinel_is_redacted_marker(self):
        assert REDACTED_SENTINEL == "<redacted>"
