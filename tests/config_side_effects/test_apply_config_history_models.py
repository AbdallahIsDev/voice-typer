"""Tests for ``apply_config`` save-strict persistence contract (SVC-11 / PVT-21).

Split out of the former ``tests/test_history_and_models.py`` catch-all.
Verbatim mechanical move — same test names +
assertions, only the file location changed.

TODO: merge into the canonical test_<module>.py file in a future session.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestApplyConfigPersistsOnSideEffectFailure:
    """SVC-11: ``apply_config`` persists config via ``save_strict()``.

    PVT-21 (session-1) extracted ``apply_config`` orchestration from
    ``service.py`` into ``config_applier.py``. The new contract:

    1. ``service.apply_config`` delegates to ``config_applier.apply_config``.
    2. ``config_applier.apply_config`` calls ``apply_config_side_effects``
       then ``app.config.save_strict()`` (NOT ``save()``).
    3. CR-97: ``save_strict()`` raises ``RuntimeError`` if ``save()``
       returned ``False`` (disk write failure) — the IPC handler is
       expected to catch this and surface the error.
    4. G4-H-12: if ``save_strict()`` raises, in-memory Config is rolled
       back to the pre-setattr snapshot so live state matches disk.

    The original SVC-11 "save in finally even when side-effects raise"
    guarantee is replaced by the G4-H-12 rollback pattern: if side-effects
    raise, ``save_strict()`` is NOT called (the raise propagates first),
    but in-memory state is rolled back so it cannot diverge from disk.
    """

    def _make_service_and_app(self):
        import contextlib

        from voice_typer.server.service import VoiceTyperService

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app = MagicMock()
        app._config_mutation_lock = _fake_lock()
        app.config = MagicMock()
        # + : config_applier now calls save_strict() (not save()).
        # save_strict() raises RuntimeError if save() returns False.
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
        return service, app

    def test_save_called_when_side_effects_succeed(self, tmp_config_dir, monkeypatch):
        """Baseline: when side effects succeed, save_strict() is called once."""
        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        # config_applier imports asdict INSIDE apply_config; patch dataclasses.asdict.
        # Use a counter so pre != post (state appears changed) → save_strict called.
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        service.apply_config({"hotkey": "<f4>"})
        app.config.save_strict.assert_called_once_with()

    def test_save_called_when_side_effects_raise(self, tmp_config_dir, monkeypatch):
        """PVT-21: when ``apply_config_side_effects`` raises, the raise
        propagates and ``save_strict()`` is NOT called. The original
        exception is re-raised so the IPC layer can surface the error.
        (Replaces the SVC-11 "save in finally" guarantee — see class docstring.)"""

        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        def _boom(updates):
            raise RuntimeError("side effect blew up")

        # side-effects now live on config_applier, not service.
        monkeypatch.setattr(service._config_applier, "apply_config_side_effects", _boom)

        with pytest.raises(RuntimeError, match="side effect blew up"):
            service.apply_config({"hotkey": "<f4>"})

        # Side-effects raised → save_strict NOT called (raise propagated first).
        app.config.save_strict.assert_not_called()

    def test_save_failure_surfaces_when_side_effects_succeeded(self, tmp_config_dir, monkeypatch):
        """CR-97: if save_strict() raises (e.g. save() returned False →
        RuntimeError, or underlying OSError propagates), the error is
        surfaced to the caller. G4-H-12: in-memory Config is rolled back."""

        service, app = self._make_service_and_app()
        import dataclasses

        import voice_typer.server.config_applier as cap
        import voice_typer.server.credential_store as cs

        monkeypatch.setattr(cs, "CONFIG_FIELD_TO_PROVIDER", {})
        _counter = {"n": 0}

        def _fake_asdict(c):
            _counter["n"] += 1
            return {"_t": _counter["n"]}

        monkeypatch.setattr(dataclasses, "asdict", _fake_asdict)
        monkeypatch.setattr(cap, "_json_dumps_sorted", lambda d: repr(d))

        # save_strict raises when save() returned False.
        # We mock save_strict to raise OSError directly to simulate
        # a disk-write failure that propagated (rather than the
        # save-returns-False → save_strict-raises-RuntimeError path).
        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": "<f4>"})
