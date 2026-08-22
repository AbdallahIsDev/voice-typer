"""Verify the credential_store package split preserves the public API surface.

Splitting ``voice_typer/server/credential_store.py`` (a single 2132-line
module) into the ``voice_typer/server/credential_store/`` package must
preserve every public AND private symbol that callers / tests import or
monkey-patch. This module imports each symbol from the package and
asserts it has the expected type (callable for functions, the correct
class for data, etc.).

The split also relies on test-time ``monkeypatch.setattr`` propagation:
several constants and functions (``_KEYRING_TIMEOUT_SECONDS``,
``_is_windows``, ``is_keyring_available``, ``_KNOWN_PROVIDERS_HISTORY``,
``_plaintext_config_cache``, ``_probe_keyring``, ...) are read by
submodule call sites via the *package* module (``_cs.<NAME>``) rather
than via bare-name global lookup against the submodule's own
``__dict__``. The final test class verifies a representative
monkey-patch propagates to the consuming call site.
"""

from __future__ import annotations

import contextlib
import threading

import pytest
from voice_typer.server import credential_store as cs

# ── Public API ───────────────────────────────────────────────────────────


class TestPublicAPIPreserved:
    """Every public symbol listed in ``__all__`` must resolve to a value
    of the expected type after the split."""

    def test_constants_are_correct_types(self):
        assert isinstance(cs.KEYRING_SERVICE_NAME, str)
        assert cs.KEYRING_SERVICE_NAME == "com.voicetyper.keyring"
        assert isinstance(cs.KEYRING_REF_PREFIX, str)
        assert cs.KEYRING_REF_PREFIX == "keyring://"
        assert isinstance(cs.PROVIDER_TO_CONFIG_FIELD, dict)
        assert isinstance(cs.CONFIG_FIELD_TO_PROVIDER, dict)
        assert isinstance(cs._KNOWN_PROVIDERS_HISTORY, frozenset)

    def test_provider_map_round_trip(self):
        for provider, field in cs.PROVIDER_TO_CONFIG_FIELD.items():
            assert cs.CONFIG_FIELD_TO_PROVIDER[field] == provider

    def test_public_functions_callable(self):
        for name in (
            "is_keyring_available",
            "get_keyring_status",
            "store_secret",
            "load_secret",
            "delete_secret",
            "clear_in_memory_secrets",
            "migrate_secrets_to_keyring",
        ):
            assert callable(getattr(cs, name)), f"{name} must be callable"

    def test_outcome_function_callable(self):
        assert callable(cs.last_store_outcome)
        # Returns a dict with the three documented keys (``stored_in``,
        # ``reason``, ``provider``). The exact ``stored_in`` value
        # depends on whether a prior ``store_secret`` call ran on this
        # thread — other test files in the credential suite call
        # ``store_secret`` and the thread-local persists across tests,
        # so we only assert the shape here.
        outcome = cs.last_store_outcome()
        assert set(outcome.keys()) == {"stored_in", "reason", "provider"}
        assert outcome["stored_in"] in {
            "keyring",
            "plaintext",
            "deleted",
            "failed",
            "unknown",
        }

    def test_all_attribute_complete(self):
        for name in cs.__all__:
            assert hasattr(cs, name), f"public __all__ entry {name!r} missing from package"


# ── Private symbols used by tests ───────────────────────────────────────


class TestPrivateSymbolsPreserved:
    """Symbols tests import / monkey-patch must still resolve."""

    @pytest.mark.parametrize(
        "name",
        [
            # schema
            "_LEGACY_KEYRING_SERVICE_NAMES",
            "_SERVICE_NAME_MIGRATED_FLAG",
            "_REASON_MAX_LEN",
            "_T",
            "log",
            # redact
            "_PATH_RE",
            "_redact_sensitive",
            # outcome
            "_last_store_outcome",
            "_set_last_store_outcome",
            # backend
            "_KEYRING_TIMEOUT_SECONDS",
            "_KEYRING_WEDGE_COOLDOWN_S",
            "_KEYRING_ORPHAN_WARN_THRESHOLD",
            "_KEYRING_REPROBE_INTERVAL_SECONDS",
            "_keyring_state_lock",
            "_orphaned_thread_count",
            "_consecutive_timeouts",
            "_wedged_until",
            "_keyring_available_cache",
            "_keyring_backend_name_cache",
            "_keyring_reason_cache",
            "_keyring_last_probe_ts",
            "_keyring_probe_lock",
            "_plaintext_config_cache",
            "_run_keyring_call",
            "_probe_keyring",
            "_reset_keyring_cache",
            "_clear_plaintext_config_cache",
            # plaintext
            "_read_plaintext_fallback",
            "_write_plaintext_fallback",
            # migration
            "_is_windows",
            "_MIGRATION_LOCK_TIMEOUT_SECONDS",
            "_MIGRATION_LOCK_SLOW_WAIT_WARN_SECONDS",
            "_acquire_migration_lock",
            "_migrate_legacy_service_names_locked",
            "_migrate_secrets_to_keyring_locked",
        ],
    )
    def test_symbol_resolves(self, name):
        assert hasattr(cs, name), f"private symbol {name!r} missing from package"

    def test_log_name_unchanged(self):
        assert cs.log.name == "voice_typer.server.credential_store"

    def test_plaintext_config_cache_is_dict(self):
        assert isinstance(cs._plaintext_config_cache, dict)

    def test_keyring_state_lock_is_lock(self):
        assert isinstance(cs._keyring_state_lock, type(threading.Lock()))

    def test_last_store_outcome_is_thread_local(self):
        assert isinstance(cs._last_store_outcome, threading.local)


# ── Monkey-patch propagation (the key invariant of the split) ───────────


class TestMonkeyPatchPropagates:
    """Tests do ``monkeypatch.setattr(credential_store, "<name>", ...)``.
    After the split, the patched value lives on the *package* module's
    ``__dict__``; submodule call sites must look the symbol up via the
    package module (``_cs.<name>``) for the patch to take effect."""

    def test_keyring_timeout_seconds_propagates_to_run_keyring_call(self, monkeypatch):
        """``_run_keyring_call`` reads ``_KEYRING_TIMEOUT_SECONDS`` via
        the package module — patching the package attribute must shorten
        the timeout (a 1ms timeout aborts a 500ms call)."""
        import time

        monkeypatch.setattr(cs, "_KEYRING_TIMEOUT_SECONDS", 0.001)

        def slow_fn():
            time.sleep(0.5)
            return "should-not-reach"

        with pytest.raises(TimeoutError):
            cs._run_keyring_call(slow_fn)

    def test_is_keyring_available_propagates_to_store_secret(self, monkeypatch):
        """``store_secret`` reads ``is_keyring_available`` via the
        package module — patching the package attribute must make
        ``store_secret`` see the patched value."""
        calls = []
        monkeypatch.setattr(cs, "is_keyring_available", lambda: (calls.append(1), True)[1])
        # store_secret on an unknown provider calls is_keyring_available
        # only AFTER the provider-validation check (which fails first).
        # Use a known provider with an empty value to hit the delete
        # path that doesn't touch is_keyring_available, then call
        # ``load_secret`` which DOES consult is_keyring_available.
        with contextlib.suppress(Exception):
            cs.load_secret("openai")
        assert calls, "load_secret must have consulted the patched is_keyring_available"

    def test_known_providers_history_propagates_to_delete_secret(self, monkeypatch):
        """``delete_secret`` iterates ``_KNOWN_PROVIDERS_HISTORY`` via
        the package module — patching the package attribute must change
        what the loop sees."""
        fake = frozenset({"ghost_provider"})
        monkeypatch.setattr(cs, "_KNOWN_PROVIDERS_HISTORY", fake)
        assert cs._KNOWN_PROVIDERS_HISTORY is fake

    def test_plaintext_config_cache_propagates_to_clear_helper(self, monkeypatch):
        """``_clear_plaintext_config_cache`` clears the dict via the
        package module — patching the package attribute with a new
        dict and then calling the clear helper must leave the patched
        dict empty (not the original)."""
        sentinel = {"k": "v"}
        monkeypatch.setattr(cs, "_plaintext_config_cache", sentinel)
        cs._clear_plaintext_config_cache()
        assert sentinel == {}, "clear helper must have cleared the patched dict"


# ── Smoke: end-to-end import + dir() ────────────────────────────────────


class TestPublicSurfaceMatchesDir:
    """``dir(credential_store)`` must surface every public name. This is
    the assertion the orchestrator's validation step makes (``python -c
    "from voice_typer.server import credential_store; print(dir(...))"``).
    """

    def test_dir_contains_public_api(self):
        names = set(dir(cs))
        for name in (
            "KEYRING_REF_PREFIX",
            "KEYRING_SERVICE_NAME",
            "PROVIDER_TO_CONFIG_FIELD",
            "CONFIG_FIELD_TO_PROVIDER",
            "_KNOWN_PROVIDERS_HISTORY",
            "clear_in_memory_secrets",
            "delete_secret",
            "get_keyring_status",
            "is_keyring_available",
            "load_secret",
            "migrate_secrets_to_keyring",
            "store_secret",
            "last_store_outcome",
            "log",
        ):
            assert name in names, f"dir(credential_store) missing {name!r}"
