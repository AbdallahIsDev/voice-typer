"""Fail-closed regression test for ``config_sanitizer._derive_secret_fields``.

Background
----------
``config_sanitizer.SECRET_CONFIG_FIELDS`` is structurally derived from
``credential_store.PROVIDER_TO_CONFIG_FIELD.values()`` at import time
(via the ``_derive_secret_fields`` helper). Previously, if the
``credential_store`` import failed for any reason (broken sandbox,
partial-install, future refactor), the helper SILENTLY fell back to a
hardcoded 5-field literal frozenset. That silent fallback was a
security degradation: if a new provider had been added to
``PROVIDER_TO_CONFIG_FIELD`` but the import was failing in some
environment, the new provider's API key would NOT be in the fallback
set and would be echoed in plaintext over the loopback IPC socket
(SEC-003 regression).

The fix: the helper now logs ``CRITICAL`` and RE-RAISES on import
failure (fail-closed). The application refuses to start with broken
secret redaction rather than silently degrading the redaction
boundary.

These tests pin both invariants:

1. **Parity (positive case)** — when both modules import cleanly,
   ``SECRET_CONFIG_FIELDS`` must equal
   ``frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.values())``.
   A future contributor adding a provider to
   ``PROVIDER_TO_CONFIG_FIELD`` without wiring it into the sanitizer
   would fail here.

2. **Fail-closed (negative case)** — when the
   ``credential_store`` import fails, ``_derive_secret_fields`` must
   RAISE (not silently fall back to a hardcoded literal). This is
   verified by poisoning ``sys.modules`` so the function-local
   ``from voice_typer.server.credential_store import ...`` raises
   ``ImportError``, then asserting the helper propagates the error.
"""

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
    """When both modules import cleanly, the derived set must equal the
    credential_store source-of-truth. This is the defense-in-depth
    parity check (acceptance criterion 2)."""

    def test_secret_config_fields_equals_provider_map_values(self):
        """``SECRET_CONFIG_FIELDS`` must be EXACTLY
        ``frozenset(PROVIDER_TO_CONFIG_FIELD.values())``.

        A future contributor adding a provider (e.g. ``"mistral":
        "mistral_api_key"``) to ``PROVIDER_TO_CONFIG_FIELD`` is
        automatically picked up by the structural derivation in
        ``_derive_secret_fields``. If someone reintroduces a hand-
        maintained literal (regression of the fail-closed fix), this
        test will catch the divergence.
        """
        expected = frozenset(PROVIDER_TO_CONFIG_FIELD.values())
        assert expected == SECRET_CONFIG_FIELDS, (
            "SECRET_CONFIG_FIELDS must equal "
            "frozenset(credential_store.PROVIDER_TO_CONFIG_FIELD.values()). "
            f"Expected: {sorted(expected)!r}. "
            f"Got: {sorted(SECRET_CONFIG_FIELDS)!r}."
        )

    def test_secret_config_fields_is_frozenset(self):
        """Type pin — the helper returns ``frozenset[str]``, not a
        ``set`` or ``list`` (frozenset is hashable and immutable so
        callers cannot accidentally mutate the redaction list)."""
        assert isinstance(SECRET_CONFIG_FIELDS, frozenset)

    def test_secret_config_fields_contains_all_known_providers(self):
        """The set must contain at least the 5 historical provider
        fields. Asserts 'contains at least' (not 'exactly') so adding
        a new provider doesn't break this test — the parity test
        above pins the exact-equality invariant."""
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
        """``_SECRET_CONFIG_FIELDS`` is an alias for the SAME frozenset
        object (not a copy). Asserting object identity catches a
        regression where the underscore alias becomes a divergent
        literal."""
        assert config_sanitizer._SECRET_CONFIG_FIELDS is SECRET_CONFIG_FIELDS


class TestDeriveSecretFieldsFailClosed:
    """``_derive_secret_fields`` must RAISE on import failure (fail-closed),
    not silently fall back to a hardcoded literal frozenset.

    Acceptance criterion 1: ``except Exception: log.critical(...); raise``.
    """

    def test_derive_raises_when_credential_store_import_fails(self, monkeypatch, caplog):
        """When the ``credential_store`` import fails, the helper must
        propagate the exception (NOT return a fallback literal).

        We simulate the import failure by poisoning
        ``sys.modules['voice_typer.server.credential_store']`` with
        ``None`` — the standard CPython idiom that makes
        ``from voice_typer.server.credential_store import X`` raise
        ``ImportError``. The helper's function-local import will then
        fail, and per the fail-closed contract the helper must
        RE-RAISE rather than swallow the error and fall back.
        """
        # Poison sys.modules so the function-local import raises.
        # Setting a key to None in sys.modules is the CPython idiom
        # for "halt import of this module" — any subsequent
        # ``import voice_typer.server.credential_store`` (or
        # ``from ... import X``) raises ImportError.
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", None)

        with pytest.raises(ImportError):
            _derive_secret_fields()

    def test_derive_logs_critical_on_import_failure(self, monkeypatch, caplog):
        """The helper must log a ``CRITICAL`` message before re-raising
        so the breakage is visible in the application log (not just an
        opaque stack trace). The message must mention
        ``PROVIDER_TO_CONFIG_FIELD`` so operators can grep for the
        specific failure mode."""
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", None)

        with caplog.at_level("CRITICAL", logger="voice_typer.server.config_sanitizer"), pytest.raises(ImportError):
            _derive_secret_fields()

        # At least one CRITICAL record was emitted by our logger.
        critical_records = [r for r in caplog.records if r.levelname == "CRITICAL"]
        assert critical_records, (
            "Fail-closed path must emit at least one CRITICAL log record "
            "before re-raising so operators can diagnose the broken import."
        )
        # The message must mention the missing symbol so a grep for
        # "PROVIDER_TO_CONFIG_FIELD" surfaces this failure mode.
        combined = " ".join(r.getMessage() for r in critical_records)
        assert "PROVIDER_TO_CONFIG_FIELD" in combined, (
            "CRITICAL log message must mention PROVIDER_TO_CONFIG_FIELD so "
            "operators can grep for the specific failure mode. Got: "
            f"{combined!r}"
        )
        # The message must indicate fail-closed behavior so operators
        # understand why the application refused to start.
        assert "fail-closed" in combined.lower(), (
            f"CRITICAL log message must indicate fail-closed behavior. Got: {combined!r}"
        )

    def test_derive_does_not_return_hardcoded_literal_on_failure(self, monkeypatch):
        """The historical fallback literal (5 hardcoded field names)
        must NOT be returned when the import fails. This is the
        core security invariant: a silent fallback to a stale set
        could leave newly added provider API keys un-redacted over
        IPC (SEC-003 regression)."""
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
            # Defense in depth: if the helper somehow returned a value
            # (regression of the fail-closed fix), it must NOT be the
            # hardcoded fallback literal.
            assert result != hardcoded_fallback, (
                "Fail-closed regression: _derive_secret_fields returned the "
                "hardcoded 5-field fallback literal on import failure "
                "instead of raising. This silently degrades the redaction "
                "boundary for any newly added provider (SEC-003 regression)."
            )

    def test_derive_succeeds_when_import_is_healthy(self):
        """Positive control: with a healthy ``credential_store`` import,
        the helper returns the structurally-derived frozenset (no
        raise). This guards against an over-aggressive fail-closed
        implementation that raises even on success."""
        result = _derive_secret_fields()
        assert isinstance(result, frozenset)
        assert result == frozenset(PROVIDER_TO_CONFIG_FIELD.values())

    def test_derive_propagates_arbitrary_exception(self, monkeypatch):
        """Fail-closed must propagate ANY exception type, not just
        ``ImportError``. If a future refactor of ``credential_store``
        raises a different exception type at import time (e.g.
        ``RuntimeError`` from a failed keyring backend probe, or
        ``SyntaxError`` from a typo), the helper must still re-raise
        rather than swallow it."""
        # Simulate a non-ImportError by replacing the credential_store
        # module with an object whose attribute access raises.
        # The helper does ``from voice_typer.server.credential_store
        # import PROVIDER_TO_CONFIG_FIELD`` — this first imports the
        # module (using the cached entry in sys.modules, which we
        # replace with a poisoned stub), then accesses
        # ``PROVIDER_TO_CONFIG_FIELD`` on it.

        class _PoisonedModule:
            def __getattr__(self, name):
                raise RuntimeError(f"simulated credential_store breakage on attr {name!r}")

        # Replace the cached module so the ``from ... import`` binding
        # succeeds (the module is "imported" from cache) but the
        # subsequent attribute lookup raises RuntimeError.
        monkeypatch.setitem(
            sys.modules,
            "voice_typer.server.credential_store",
            _PoisonedModule(),
        )

        with pytest.raises(RuntimeError, match="simulated credential_store"):
            _derive_secret_fields()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
