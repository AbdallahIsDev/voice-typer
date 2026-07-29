"""DJ-29: lazy ``pre_state_dict`` capture in ``config_applier.apply_config``.

Previously, ``apply_config`` called ``dataclasses.asdict(app.config)``
eagerly on EVERY invocation — including no-op updates with an empty
``updates`` dict — which deep-copies all 150+ Config fields per IPC
``set_config`` call. The captured snapshot was only consulted by:

1. The G4-L-20 dirty-check — but since XV-120 that check has used
   ``set_keys`` (captured per-key via ``getattr`` in the setattr
   loop), NOT ``pre_state_dict``. Only the ``is not None`` guard
   referenced it.
2. The G4-H-12 rollback path on ``save_strict()`` failure — but the
   ``set_keys`` log already captures the old value for exactly the
   keys that were mutated, which is the precise set that needs
   restoration.

DJ-29 removes the eager ``asdict()`` call entirely. The dirty-check
is now purely ``pre_values == post_values`` (no ``pre_state_dict``
guard), and the rollback path builds the restoration dict from
``set_keys`` instead of ``pre_state_dict``. The behavioural
guarantees (G4-L-20 skip-on-no-op, G4-H-12 rollback-on-save-failure)
are preserved.

These tests pin the new behaviour so a future refactor can't
accidentally reintroduce the eager ``asdict()`` snapshot.
"""

from __future__ import annotations

import contextlib
import dataclasses
from unittest.mock import MagicMock

import pytest


def _make_service_and_app(tmp_config_dir, monkeypatch):
    """Build a VoiceTyperService backed by a mock app for apply_config tests.

    Mirrors the fixture pattern in ``tests/test_history_and_models.py
    ::TestSVC11ApplyConfigPersistsOnSideEffectFailure._make_service_and_app``
    but returns a real ``Config()`` instance (not a MagicMock) so the
    dirty-check has concrete values to compare.
    """
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    @contextlib.contextmanager
    def _fake_lock():
        yield

    app = MagicMock()
    app._config_mutation_lock = _fake_lock()
    # Use a REAL Config instance so the dirty-check (getattr/setattr
    # on actual fields) behaves like production. MagicMock would make
    # every getattr return a fresh child mock — useless for the
    # ``pre_values == post_values`` comparison.
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

    # credential_store pre-route: no api_key fields in these tests,
    # but the import path is exercised — stub the mapping to empty so
    # nothing is routed.
    import voice_typer.server.credential_store as cs

    monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})

    return service, app


class TestDJ29LazyPreStateDict:
    """DJ-29: ``dataclasses.asdict()`` MUST NOT be called eagerly on
    every ``apply_config`` invocation. The dirty-check uses
    ``set_keys`` (captured per-key in the setattr loop) and the
    rollback path builds the restoration dict from ``set_keys`` too —
    the full asdict snapshot is no longer needed."""

    def test_asdict_not_called_when_updates_is_empty(self, tmp_config_dir, monkeypatch):
        """DJ-29 core guarantee: when ``updates`` is empty, ``apply_config``
        MUST NOT call ``dataclasses.asdict(app.config)``. Previously it
        did — deep-copying 150+ Config fields per no-op IPC call."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        asdict_call_count = {"n": 0}
        original_asdict = dataclasses.asdict

        def _spy_asdict(obj):
            asdict_call_count["n"] += 1
            return original_asdict(obj)

        monkeypatch.setattr(dataclasses, "asdict", _spy_asdict)

        # Empty updates — the no-op path.
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
        """DJ-29 + G4-L-20: when ``updates`` is empty, the dirty-check
        (``pre_values == post_values`` with both empty dicts) returns
        True, so ``save_strict()`` MUST NOT be called. This was the
        original G4-L-20 intent — DJ-29 just removed the
        ``pre_state_dict is not None`` guard that was masking it for
        MagicMock-backed test fixtures (production Config instances
        always had ``pre_state_dict is not None``)."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        service.apply_config({})

        app.config.save_strict.assert_not_called()

    def test_save_strict_not_called_when_no_values_actually_changed(
        self, tmp_config_dir, monkeypatch
    ):
        """DJ-29: when ``updates`` is non-empty but every value already
        equals the current state, the dirty-check (``pre_values ==
        post_values``) returns True, so ``save_strict()`` MUST NOT be
        called. The eager asdict snapshot is NOT needed for this
        check — ``set_keys`` captures the pre-setattr values via
        ``getattr`` and the post-setattr values are also retrieved via
        ``getattr``."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Read the current hotkey value, then "update" it to the same
        # value — no actual change.
        current_hotkey = app.config.hotkey
        service.apply_config({"hotkey": current_hotkey})

        app.config.save_strict.assert_not_called()

    def test_save_strict_called_when_a_value_actually_changed(
        self, tmp_config_dir, monkeypatch
    ):
        """DJ-29: when ``updates`` is non-empty AND at least one value
        actually changed, the dirty-check returns False and
        ``save_strict()`` MUST be called. This is the G4-L-20 + CR-97
        happy path — preserving it ensures the lazy snapshot removal
        didn't break the persistence contract."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Change the hotkey to a different value.
        new_hotkey = "<f4>" if app.config.hotkey != "<f4>" else "<f5>"
        service.apply_config({"hotkey": new_hotkey})

        app.config.save_strict.assert_called_once_with()
        assert app.config.hotkey == new_hotkey

    def test_asdict_not_called_even_when_values_change(self, tmp_config_dir, monkeypatch):
        """DJ-29 stronger guarantee: ``dataclasses.asdict()`` is NEVER
        called by ``apply_config`` — not on the no-op path, not on the
        changed-value path, not on the rollback path. The rollback
        path now uses ``set_keys`` (the per-key pre-setattr value log)
        instead of the eager snapshot."""
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
            "apply_config — not even on the changed-value path. The "
            "G4-H-12 rollback path builds the restoration dict from "
            "set_keys (per-key pre-setattr values) instead of the "
            "eager asdict snapshot."
        )
        app.config.save_strict.assert_called_once_with()

    def test_rollback_restores_changed_keys_on_save_strict_failure(
        self, tmp_config_dir, monkeypatch
    ):
        """DJ-29 + G4-H-12: when ``save_strict()`` raises, the
        in-memory Config MUST be rolled back to the pre-setattr
        values for exactly the keys that were mutated. Previously
        this iterated the full ``pre_state_dict`` from
        ``dataclasses.asdict``; now it iterates ``set_keys`` (the
        per-key pre-setattr value log). Behaviour is identical for
        the keys the caller asked to change — restoring other fields
        was always a no-op because nothing else was mutated."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Make save_strict raise to trigger the G4-H-12 rollback path.
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        original_hotkey = app.config.hotkey
        new_hotkey = "<f4>" if original_hotkey != "<f4>" else "<f5>"

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": new_hotkey})

        # G4-H-12: the in-memory Config MUST be rolled back to the
        # pre-setattr value for the mutated key.
        assert app.config.hotkey == original_hotkey, (
            "DJ-29 + G4-H-12: after save_strict failure, the mutated key "
            "must be restored to its pre-setattr value. The rollback path "
            "now uses set_keys (per-key pre-setattr log) instead of the "
            "eager asdict snapshot, but the restoration behaviour is "
            "identical for the keys the caller asked to change."
        )

    def test_rollback_reruns_side_effects_with_original_values(
        self, tmp_config_dir, monkeypatch
    ):
        """DJ-29 + G4-H-12: when ``save_strict()`` raises, side-effects
        MUST be re-run with the ORIGINAL values (from ``set_keys``) so
        live state (hotkey registration, etc.) matches the restored
        config. Previously the "old updates" dict was built from
        ``pre_state_dict``; now it's built from ``set_keys`` — same
        content for the keys the caller asked to change."""
        service, app = _make_service_and_app(tmp_config_dir, monkeypatch)

        # Make save_strict raise to trigger the G4-H-12 rollback path.
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        # Capture the updates dict passed to apply_config_side_effects
        # across both the initial call and the rollback re-run.
        side_effect_calls: list[dict] = []

        def _capture_side_effect(updates):
            side_effect_calls.append(dict(updates))
            return {"autostart_status": None, "prewarm_status": None}

        monkeypatch.setattr(
            service._config_applier, "apply_config_side_effects", _capture_side_effect
        )

        original_hotkey = app.config.hotkey
        new_hotkey = "<f4>" if original_hotkey != "<f4>" else "<f5>"

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": new_hotkey})

        # First call: with the new value (the user's requested change).
        # Second call: with the original value (rollback re-run).
        assert len(side_effect_calls) == 2, (
            "DJ-29 + G4-H-12: apply_config_side_effects must be called twice "
            "on save_strict failure — once with the new values (initial "
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
        """Source guard: ``apply_config`` MUST NOT contain an actual
        call to ``asdict`` (or ``dataclasses.asdict``). Comments and
        docstrings that mention ``asdict`` for historical context are
        fine; the AST is inspected so only real call expressions are
        flagged."""
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
            # Match ``asdict(...)`` (bare name) or
            # ``dataclasses.asdict(...)`` / ``_asdict(...)`` (attribute
            # access where the attr is ``asdict``).
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
        """Source guard: ``apply_config`` MUST NOT reference
        ``pre_state_dict`` (the variable that held the eager asdict
        snapshot) in actual code. Comments and docstrings that mention
        it for historical context are fine; the AST is inspected so
        only real Name nodes are flagged."""
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
            + "). The variable was removed in DJ-29 — the dirty-check "
            "uses set_keys and the rollback path builds the restoration "
            "dict from set_keys."
        )
