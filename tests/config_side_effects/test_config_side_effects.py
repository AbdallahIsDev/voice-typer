"""Domain: config-apply side effects: ``apply_config`` persists via"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestApplyConfigPersistsOnSideEffectFailure:
    """SVC-11: ``apply_config`` persists config via ``save_strict()``."""

    def _make_service_and_app(self):
        import contextlib

        from voice_typer.server.service import LausuService

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app = MagicMock()
        app._config_mutation_lock = _fake_lock()
        app.config = MagicMock()
        # + : config_applier now calls save_strict() (not save()).
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

        service = LausuService(app)
        return service, app

    def test_save_called_when_side_effects_succeed(self, tmp_config_dir, monkeypatch):
        """Baseline: when side effects succeed, save_strict() is called once."""
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

        service.apply_config({"hotkey": "<f4>"})
        app.config.save_strict.assert_called_once_with()

    def test_save_called_when_side_effects_raise(self, tmp_config_dir, monkeypatch):
        """PVT-21: when ``apply_config_side_effects`` raises, the raise"""

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

        monkeypatch.setattr(service._config_applier, "apply_config_side_effects", _boom)

        with pytest.raises(RuntimeError, match="side effect blew up"):
            service.apply_config({"hotkey": "<f4>"})

        # Side-effects raised → save_strict NOT called (raise propagated first).
        app.config.save_strict.assert_not_called()

    def test_save_failure_surfaces_when_side_effects_succeeded(self, tmp_config_dir, monkeypatch):
        """if save_strict() raises (e.g. save() returned False →"""

        import pytest

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

        app.config.save_strict = MagicMock(side_effect=OSError("disk full"))

        with pytest.raises(OSError, match="disk full"):
            service.apply_config({"hotkey": "<f4>"})
