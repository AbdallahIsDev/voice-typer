"""Fail-closed regression test for ``config_sanitizer._derive_secret_fields``."""

from __future__ import annotations

import sys

import pytest
from voice_typer.server import config_sanitizer
from voice_typer.server.config_sanitizer import (
    SECRET_CONFIG_FIELDS,
    _derive_secret_fields,
)
from voice_typer.server.credential_store import PROVIDER_TO_CONFIG_FIELD


class TestSecretFieldsParity:
    """credential_store source-of-truth. This is the defense-in-depth"""

    def test_secret_config_fields_equals_provider_map_values(self):
        """``SECRET_CONFIG_FIELDS`` must be EXACTLY"""
        expected = frozenset(PROVIDER_TO_CONFIG_FIELD.values())
        assert expected == SECRET_CONFIG_FIELDS, (
            "SECRET_CONFIG_FIELDS must equal "
            "frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.values()). "
            f"Expected: {sorted(expected)!r}. "
            f"Got: {sorted(SECRET_CONFIG_FIELDS)!r}."
        )

    def test_secret_config_fields_is_frozenset(self):
        """``set`` or ``list`` (frozenset is hashable and immutable so"""
        assert isinstance(SECRET_CONFIG_FIELDS, frozenset)

    def test_secret_config_fields_contains_all_known_providers(self):
        """The set must contain at least the 5 historical provider"""
        known = {
            "cloud_api_key",
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "llm_api_key",
        }
        assert known <= SECRET_CONFIG_FIELDS, (
            f"SECRET_CONFIG_FIELDS must contain all 5 known provider "
            f"fields. Missing: {sorted(known - SECRET_CONFIG_FIELDS)!r}."
        )

    def test_underscore_alias_is_same_object(self):
        """``_SECRET_CONFIG_FIELDS`` is an alias for the SAME frozenset"""
        assert config_sanitizer._SECRET_CONFIG_FIELDS is SECRET_CONFIG_FIELDS


class TestDeriveSecretFieldsFailClosed:
    """``_derive_secret_fields`` must RAISE on import failure (fail-closed),"""

    def test_derive_raises_when_credential_store_import_fails(self, monkeypatch, caplog):
        """When the ``credential_store`` import fails, the helper must"""
        # Poison sys.modules so the function-local import raises.
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", None)

        with pytest.raises(ImportError):
            _derive_secret_fields()

    def test_derive_logs_critical_on_import_failure(self, monkeypatch, caplog):
        """The helper must log a ``CRITICAL`` message before re-raising"""
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", None)

        with caplog.at_level("CRITICAL", logger="voice_typer.server.config_sanitizer"), pytest.raises(ImportError):
            _derive_secret_fields()

        # At least one CRITICAL record was emitted by our logger.
        critical_records = [r for r in caplog.records if r.levelname == "CRITICAL"]
        assert critical_records, (
            "Fail-closed path must emit at least one CRITICAL log record "
            "before re-raising so operators can diagnose the broken import."
        )
        combined = " ".join(r.getMessage() for r in critical_records)
        assert "PROVIDER_TO_CONFIG_FIELD" in combined, (
            "CRITICAL log message must mention PROVIDER_TO_CONFIG_FIELD so "
            "operators can grep for the specific failure mode. Got: "
            f"{combined!r}"
        )
        # The message must indicate fail-closed behavior so operators
        assert "fail-closed" in combined.lower(), (
            f"CRITICAL log message must indicate fail-closed behavior. Got: {combined!r}"
        )

    def test_derive_does_not_return_hardcoded_literal_on_failure(self, monkeypatch):
        """
        The historical fallback literal (5 hardcoded field names)
        IPC (SEC-003 regression).
        """
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", None)

        hardcoded_fallback = frozenset(
            {
                "cloud_api_key",
                "openai_api_key",
                "groq_api_key",
                "deepgram_api_key",
                "llm_api_key",
            }
        )

        # The helper must raise, not return the hardcoded fallback.
        with pytest.raises(ImportError):
            result = _derive_secret_fields()
            # (regression of the fail-closed fix), it must NOT be the
            assert result != hardcoded_fallback, (
                "Fail-closed regression: _derive_secret_fields returned the "
                "hardcoded 5-field fallback literal on import failure "
                "instead of raising. This silently degrades the redaction "
                "boundary for any newly added provider (SEC-003 regression)."
            )

    def test_derive_succeeds_when_import_is_healthy(self):
        """Positive control: with a healthy ``credential_store`` import,"""
        result = _derive_secret_fields()
        assert isinstance(result, frozenset)
        assert result == frozenset(PROVIDER_TO_CONFIG_FIELD.values())

    def test_derive_propagates_arbitrary_exception(self, monkeypatch):
        """Fail-closed must propagate ANY exception type, not just"""
        # Simulate a non-ImportError by replacing the credential_store

        class _PoisonedModule:
            def __getattr__(self, name):
                raise RuntimeError(f"simulated credential_store breakage on attr {name!r}")

        # Replace the cached module so the ``from ... import`` binding
        monkeypatch.setitem(
            sys.modules,
            "voice_typer.server.credential_store",
            _PoisonedModule(),
        )

        with pytest.raises(RuntimeError, match="simulated credential_store"):
            _derive_secret_fields()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
