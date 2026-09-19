"""DJ-29: lazy ``pre_state_dict`` capture in ``config_applier.apply_config``."""

from __future__ import annotations

import contextlib
import dataclasses
from unittest.mock import MagicMock

import pytest


def _make_service_and_app(tmp_config_dir, monkeypatch):
    """Build a VoiceTyperService backed by a mock app for apply_config tests."""
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    @contextlib.contextmanager
    def _fake_lock():
        yield

    app = MagicMock()
    app._config_mutation_lock = _fake_lock()
    # Use a REAL Config instance so the dirty-check (getattr/setattr
    app.config = Config()
    app.config.save = MagicMock(return_value=True)
    app.config.save_strict = MagicMock(return_value=None)
    app.clipboard = MagicMock()
    app.tray = MagicMock()
    app.tray.invalidate_menu_cache = MagicMock()
    app._llm_polisher = None
    app.hotkeys = MagicMock()
    app.recorder = MagicMock()
    app._busy_event = MagicMock()
    app._busy_event.is_set = MagicMock(return_value=True)
    app._shutting_down = False

    service = VoiceTyperService(app)

    import voice_typer.server.credential_store as cs

    monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})

    return service, app


class TestLazyPreStateDict:
    """every ``apply_config`` invocation. The dirty-check uses"""

    def test_asdict_not_called_when_updates_is_empty(self, tmp_config_dir, monkeypatch):
        """
        DJ-29 core guarantee: when ``updates`` is empty, ``apply_config``
        MUST NOT call ``dataclasses.asdict(app.config)``. Previously it
        """
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        asdict_call_count = {"n": 0}
        original_asdict = dataclasses.asdict

        def _spy_asdict(obj):
            asdict_call_count["n"] += 1
            return original_asdict(obj)

        monkeypatch.setattr(dataclasses, "asdict", _spy_asdict)

        # Empty updates, the no-op path.
        service.apply_config({})

        assert asdict_call_count["n"] == 0, (
            "DJ-29: dataclasses.asdict() must NOT be called when updates is "
            "empty. The previous implementation eagerly snapshotted the full "
            "Config (150+ fields deep-copy) on every IPC set_config call, "
            "even when the update was a no-op. The dirty-check now uses the "
            "per-key set_keys log captured during the setattr loop, and the "
            "rollback path builds the restoration dict from set_keys too."
        )

    def test_save_strict_not_called_when_updates_is_empty(self, tmp_config_dir, monkeypatch):
        """DJ-29 + G4-L-20: when ``updates`` is empty, the dirty-check"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        service.apply_config({})

        app.config.save_strict.assert_not_called()

    def test_save_strict_not_called_when_no_values_actually_changed(self, tmp_config_dir, monkeypatch):
        """DJ-29: when ``updates`` is non-empty but every value already"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Read the current hotkey value, then "update" it to the same
        current_hotkey = app.config.hotkey
        service.apply_config({"hotkey": current_hotkey})

        app.config.save_strict.assert_not_called()

    def test_save_strict_called_when_a_value_actually_changed(self, tmp_config_dir, monkeypatch):
        """DJ-29: when ``updates`` is non-empty AND at least one value"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Change the hotkey to a different value.
        new_hotkey = "<f4>" if app.config.hotkey != "<f4>" else "<f5>"
        service.apply_config({"hotkey": new_hotkey})

        app.config.save_strict.assert_called_once_with()
        assert app.config.hotkey == new_hotkey

    def test_asdict_not_called_even_when_values_change(self, tmp_config_dir, monkeypatch):
        """DJ-29 stronger guarantee: ``dataclasses.asdict()`` is NEVER"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        asdict_call_count = {"n": 0}
        original_asdict = dataclasses.asdict

        def _spy_asdict(obj):
            asdict_call_count["n"] += 1
            return original_asdict(obj)

        monkeypatch.setattr(dataclasses, "asdict", _spy_asdict)

        new_hotkey = "<f4>" if app.config.hotkey != "<f4>" else "<f5>"
        service.apply_config({"hotkey": new_hotkey})

        assert asdict_call_count["n"] == 0, (
            "DJ-29: dataclasses.asdict() must NEVER be called by "
            "apply_config, not even on the changed-value path. The "
            "G4-H-12 rollback path builds the restoration dict from "
            "set_keys (per-key pre-setattr values) instead of the "
            "eager asdict snapshot."
        )
        app.config.save_strict.assert_called_once_with()

    def test_rollback_restores_changed_keys_on_save_strict_failure(self, tmp_config_dir, monkeypatch):
        """in-memory Config MUST be rolled back to the pre-setattr"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Make save_strict raise to trigger the  rollback path.
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        original_hotkey = app.config.hotkey
        new_hotkey = "<f4>" if original_hotkey != "<f4>" else "<f5>"

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": new_hotkey})

        assert app.config.hotkey == original_hotkey, (
            "DJ-29 + G4-H-12: after save_strict failure, the mutated key "
            "must be restored to its pre-setattr value. The rollback path "
            "now uses set_keys (per-key pre-setattr log) instead of the "
            "eager asdict snapshot, but the restoration behaviour is "
            "identical for the keys the caller asked to change."
        )

    def test_rollback_reruns_side_effects_with_original_values(self, tmp_config_dir, monkeypatch):
        """DJ-29 + G4-H-12: when ``save_strict()`` raises, side-effects"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Make save_strict raise to trigger the  rollback path.
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        # Capture the updates dict passed to apply_config_side_effects
        side_effect_calls: list[dict] = []

        def _capture_side_effect(updates):
            side_effect_calls.append(dict(updates))
            return {"autostart_status": None, "prewarm_status": None}

        monkeypatch.setattr(service._config_applier, "apply_config_side_effects", _capture_side_effect)

        original_hotkey = app.config.hotkey
        new_hotkey = "<f4>" if original_hotkey != "<f4>" else "<f5>"

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": new_hotkey})

        # First call: with the new value (the user's requested change).
        assert len(side_effect_calls) == 2, (
            "DJ-29 + G4-H-12: apply_config_side_effects must be called twice "
            "on save_strict failure, once with the new values (initial "
            "application) and once with the original values (rollback re-run "
            "so live state matches the restored config)."
        )
        assert side_effect_calls[0] == {"hotkey": new_hotkey}
        assert side_effect_calls[1] == {"hotkey": original_hotkey}, (
            "DJ-29 + G4-H-12: the rollback re-run must pass the ORIGINAL "
            "(pre-setattr) values, sourced from set_keys (the per-key "
            "rollback log). Previously these came from pre_state_dict "
            "(asdict snapshot); the content is identical for the keys the "
            "caller asked to change."
        )

    def test_source_does_not_call_asdict(self):
        """Source guard: ``apply_config`` MUST NOT contain an actual"""
        import ast
        import inspect
        import textwrap

        from voice_typer.server.config_applier import ConfigApplier

        src = textwrap.dedent(inspect.getsource(ConfigApplier.apply_config))
        tree = ast.parse(src)

        asdict_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "asdict":
                asdict_calls.append("asdict(...)")
            elif isinstance(func, ast.Attribute) and func.attr == "asdict":
                asdict_calls.append(f"{ast.unparse(func)}.asdict(...)")

        assert not asdict_calls, (
            "DJ-29 regression: ConfigApplier.apply_config contains an "
            "asdict() call expression: " + ", ".join(asdict_calls) + ". "
            "The eager pre-setattr snapshot was removed in DJ-29 because "
            "the dirty-check uses set_keys (per-key getattr log) and the "
            "rollback path builds the restoration dict from set_keys too. "
            "Reintroducing asdict() would re-introduce the O(150+ fields) "
            "deep-copy on every IPC set_config call."
        )

    def test_source_does_not_reference_pre_state_dict(self):
        """Source guard: ``apply_config`` MUST NOT reference"""
        import ast
        import inspect
        import textwrap

        from voice_typer.server.config_applier import ConfigApplier

        src = textwrap.dedent(inspect.getsource(ConfigApplier.apply_config))
        tree = ast.parse(src)

        pre_state_dict_refs: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "pre_state_dict":
                pre_state_dict_refs.append(node.lineno)

        assert not pre_state_dict_refs, (
            "DJ-29 regression: ConfigApplier.apply_config references "
            "pre_state_dict as a Name node in code (lines: "
            + ", ".join(str(ln) for ln in pre_state_dict_refs)
            + "). The variable was removed in DJ-29, the dirty-check "
            "uses set_keys and the rollback path builds the restoration "
            "dict from set_keys."
        )


class TestLLMPolisherInvalidation:
    """silently broken polish after every OpenAI key rotation."""

    def test_openai_key_rotation_clears_cached_polisher(self, tmp_config_dir, monkeypatch):
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app._llm_polisher = object()
        service.apply_config({"openai_api_key": "sk-rotated"})
        assert app._llm_polisher is None, (
            "rotating openai_api_key must invalidate the cached polisher "
            "(it snapshots the key by value at construction)"
        )

    def test_llm_key_change_still_clears_cached_polisher(self, tmp_config_dir, monkeypatch):
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        app._llm_polisher = object()
        service.apply_config({"llm_model": "other-model"})
        assert app._llm_polisher is None

    def test_unrelated_change_keeps_cached_polisher(self, tmp_config_dir, monkeypatch):
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        sentinel = object()
        app._llm_polisher = sentinel
        new_hotkey = "<f4>" if app.config.hotkey != "<f4>" else "<f5>"
        service.apply_config({"hotkey": new_hotkey})
        assert app._llm_polisher is sentinel


class TestApplyConfigStepExtraction:
    """
    ``apply_config`` is an orchestrator over named private steps.
    ``tests/regressions/test_concurrency.py`` pin them there).
    """

    def test_sec002_raise_stays_on_outer_method(self):
        """SEC-002 must still hard-fail inside ``apply_config`` itself."""
        import ast
        import inspect
        import textwrap

        from voice_typer.server.config_applier import ConfigApplier

        src = textwrap.dedent(inspect.getsource(ConfigApplier.apply_config))
        tree = ast.parse(src)

        assert "IPC_CONFIG_ALLOWLIST" in src, (
            "SEC-002 regression: apply_config no longer references "
            "IPC_CONFIG_ALLOWLIST. The defense-in-depth check must stay "
            "on the outer method so every caller path hits it."
        )
        assert "SEC-002" in src, (
            "SEC-002 regression: apply_config no longer raises with the SEC-002 marker in the message."
        )
        raise_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
        assert raise_nodes, (
            "SEC-002 regression: apply_config contains no raise statement; the unknown-key hard fail was removed."
        )

    def test_race011_lock_acquisition_stays_on_outer_method(self):
        """``apply_config``, not delegated to a helper that a caller could"""
        import inspect
        import textwrap

        from voice_typer.server.config_applier import ConfigApplier

        src = textwrap.dedent(inspect.getsource(ConfigApplier.apply_config))
        assert "_config_mutation_lock" in src, (
            "RACE-011 regression: apply_config no longer acquires "
            "_config_mutation_lock. The lock scope must stay on the "
            "outer method so the full read-modify-save sequence is "
            "covered."
        )

    def test_step_helpers_exist(self):
        """The named private steps the extraction introduced must exist"""
        from voice_typer.server.config_applier import ConfigApplier

        for name in (
            "_empty_side_effect_status",
            "_maybe_autoswitch_audio_preset",
            "_setattr_updates",
            "_maybe_invalidate_llm_polisher",
            "_save_updates_strict",
            "_route_secrets_post_save",
            "_maybe_refresh_clipboard",
            "_post_save_tray_cleanup",
        ):
            assert callable(getattr(ConfigApplier, name, None)), (
                f"apply_config step helper {name} is missing; the "
                "extraction must keep each phase as a named private "
                "method."
            )

    def test_apply_config_still_applies_allowlisted_update(self, tmp_config_dir, monkeypatch):
        """End-to-end smoke: the orchestrator still mutates Config and"""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)
        new_hotkey = "<f4>" if app.config.hotkey != "<f4>" else "<f5>"
        service.apply_config({"hotkey": new_hotkey})
        assert app.config.hotkey == new_hotkey
        app.config.save_strict.assert_called()
