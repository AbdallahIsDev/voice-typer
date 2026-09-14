"""Tests for the genuine "no model selected" state.

``model_size == \"\"`` (the ``NO_MODEL_SIZE`` sentinel in
``model_registry.py``) means the user has NO active model, the config
can hold this value end-to-end (load, IPC, tray, Models page) instead
of being reset to the default, and the app must not pretend a phantom
model is selected.

Covered here:

- ``Config.load()`` preserves ``model_size=\"\"`` (no reset, no warning).
- The IPC ``set_config`` validator accepts ``model_size=\"\"``.
- The tray models submenu marks NOTHING active when ``model_size=\"\"``.
- ``is_active_model_downloaded`` returns False for the no-model state.
- The load path refuses with a "No model selected" message instead of
  attempting to load the empty size.
"""

from __future__ import annotations

import json

from voice_typer.server.model_registry import NO_MODEL_SIZE


class TestConfigLoadPreservesNoModelState:
    def test_empty_model_size_is_preserved(self, tmp_config_dir):
        """model_size=\"\" loads as-is, no reset to DEFAULT_MODEL_SIZE,
        no \"config corrected\" warning (it's a real state, not garbage)."""
        from voice_typer.server.config import Config

        (tmp_config_dir / "config.json").write_text(json.dumps({"model_size": NO_MODEL_SIZE}))

        c = Config.load()
        assert c.model_size == NO_MODEL_SIZE, (
            f"model_size must stay {NO_MODEL_SIZE!r} (no model selected), got {c.model_size!r}"
        )
        assert not c.last_load_warnings, (
            f"loading the no-model state must not emit a config-correction warning, got: {c.last_load_warnings}"
        )

    def test_invalid_model_size_still_resets_to_default(self, tmp_config_dir):
        """Sanity check: only the real sentinel is preserved, garbage
        values are still corrected to DEFAULT_MODEL_SIZE."""
        from voice_typer.server.config import Config
        from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

        (tmp_config_dir / "config.json").write_text(json.dumps({"model_size": "not-a-real-model"}))

        c = Config.load()
        assert c.model_size == DEFAULT_MODEL_SIZE
        assert c.last_load_warnings, "invalid model_size must surface a warning"


class TestIpcValidatorAcceptsNoModelState:
    def test_set_config_accepts_empty_model_size(self):
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"model_size": NO_MODEL_SIZE})
        assert not errors, f"no-model size must be accepted, got errors: {errors}"
        assert validated == {"model_size": NO_MODEL_SIZE}

    def test_set_config_still_rejects_unknown_model_size(self):
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"model_size": "made-up"})
        assert errors, "unknown model sizes must still be rejected"
        assert "model_size" not in validated


class TestTrayNoModelState:
    def test_tray_submenu_marks_nothing_active_when_no_model(self, tmp_path):
        """With model_size=\"\", no tray submenu row (whisper, parakeet, or
        qwen) may render as active, even backend-keyed rows."""
        from unittest.mock import MagicMock, patch

        from voice_typer.server import tray_models

        config_provider = MagicMock()
        config_provider.model_size = NO_MODEL_SIZE
        config_provider.asr_backend = "whisper"
        config_provider.qwen_model_path = None

        with patch("voice_typer.server.asr_setup.ensure_hf_env", lambda: None):
            data = tray_models.build_models_submenu_data(
                lambda: tmp_path,
                lambda name: None,
                config_provider=config_provider,
            )

        assert data, "submenu should still enumerate candidates"
        for name, _downloaded, is_active, _change_fn in data:
            assert not is_active, f"'{name}' must not be marked active when model_size == \"\" (no model selected)"

    def test_is_active_model_downloaded_false_when_no_model(self):
        """The active-model probe returns False for the no-model state so
        the load path refuses instead of loading the empty size."""
        from voice_typer.server.config import Config
        from voice_typer.server.tray_models import is_active_model_downloaded

        cfg = Config()
        cfg.model_size = NO_MODEL_SIZE
        cfg.asr_backend = "whisper"

        assert is_active_model_downloaded(cfg) is False, (
            "no model selected -> the active-model probe must report absent"
        )


class TestModelManagerNoModelRefusal:
    def test_load_refusal_message_for_no_model(self):
        """When the config holds the no-model sentinel, the load path
        refuses with a \"No model selected\" message, not a claim that a
        named model \"is not downloaded\"."""
        from voice_typer.server.asr_errors import ModelNotDownloadedError
        from voice_typer.server.config import Config
        from voice_typer.server.model_manager import ModelManager

        cfg = Config()
        cfg.model_size = NO_MODEL_SIZE
        cfg.asr_backend = "whisper"

        class _App:
            config = cfg
            _shutting_down = False

        captured: list[str] = []

        class _Recorder(ModelManager):
            def __init__(self, app):
                # Skip the real init (registry construction etc.), the
                # refusal path only needs the app + pending-dictation flag.
                self._app = app
                self._pending_dictation = False

            def _model_downloaded_precheck(self) -> bool:
                return False

            def _notify_model_load_refused(self, error, *, backend):
                # ``ModelNotDownloadedError`` is a ``RuntimeError``, its
                # message lives in ``args[0]`` (no ``.message`` attr).
                captured.append(str(error))
                assert isinstance(error, ModelNotDownloadedError)

        _Recorder(_App()).load_background()

        assert captured, "load refusal must have run"
        assert "No model selected" in captured[0], f"refusal message must say 'No model selected', got: {captured[0]}"


class TestNoModelTrayTerminalState:
    """The refusal must land on the VISIBLE tooltip, not just the log.

    Regression: with no model selected the tray froze at boot
    LOADING/"Starting..." forever, even though the refusal ran. The
    chain below (real notify + real tray + real tooltip formatter +
    a live icon stand-in exercising the real icon-render path) proves
    the error reaches the visible title.
    """

    def _booted_tray(self, monkeypatch):
        """Real TrayIcon left in the post-``app.start()`` state."""
        from unittest.mock import MagicMock

        from voice_typer.server import i18n
        from voice_typer.server.app_construction import (
            _register_startup_i18n_fallbacks,
        )
        from voice_typer.server.config import Config
        from voice_typer.server.tray import TrayIcon
        from voice_typer.server.tray_types import AppState

        _register_startup_i18n_fallbacks()
        cfg = Config()
        cfg.model_size = NO_MODEL_SIZE
        cfg.asr_backend = "whisper"
        cfg.hotkey = "<f9>"
        tray = TrayIcon(controller=MagicMock(), config=cfg)
        tray.set_state(AppState.LOADING, i18n.t("state.app.starting"))
        assert tray._message == "Starting..."
        return tray, cfg

    def test_refusal_moves_tray_from_starting_to_error(self, monkeypatch):
        """End to end (minus threads): refusal updates tray state."""
        from voice_typer.server.model_manager import ModelManager
        from voice_typer.server.tray_types import AppState

        tray, cfg = self._booted_tray(monkeypatch)

        class _App:
            def __init__(self, config, tray):
                self.config = config
                self.tray = tray
                self._shutting_down = False

        class _Recorder(ModelManager):
            def __init__(self, app):
                self._app = app
                self._pending_dictation = False

            def _model_downloaded_precheck(self) -> bool:
                return False

        _Recorder(_App(cfg, tray)).load_background()

        assert tray._state == AppState.ERROR, f"expected ERROR, got {tray._state}"
        assert "No model selected" in tray._message, f"tray message must carry the refusal, got: {tray._message!r}"

    def test_refusal_reaches_visible_tooltip_with_live_icon(self, monkeypatch):
        """Same chain with a LIVE icon: the real icon-render path
        (``_apply_state`` + ``_make_icon(ERROR)``) must deliver the
        error to the visible title, not just the internal state."""
        from voice_typer.server.model_manager import ModelManager
        from voice_typer.server.tray_publish import compute_tooltip
        from voice_typer.server.tray_types import AppState

        tray, cfg = self._booted_tray(monkeypatch)

        class _LiveIcon:
            def __init__(self):
                self.titles: list[str] = []
                self._title = ""

            @property
            def title(self) -> str:
                return self._title

            @title.setter
            def title(self, value: str) -> None:
                self._title = value
                self.titles.append(value)

        tray._icon = _LiveIcon()
        # Apply the queued boot state the way run()'s drain does
        # (``_apply_state`` directly, bypassing ``set_state`` dedup),
        # so the icon shows "Starting..." exactly like production at
        # refusal time.
        tray._apply_state(AppState.LOADING, "Starting...")
        assert tray._icon.titles, "LOADING apply must write the icon title"
        assert tray._icon.titles[-1] == "Voice Typer | Starting... (F9)"

        class _App:
            def __init__(self, config, tray):
                self.config = config
                self.tray = tray
                self._shutting_down = False

        class _Recorder(ModelManager):
            def __init__(self, app):
                self._app = app
                self._pending_dictation = False

            def _model_downloaded_precheck(self) -> bool:
                return False

        _Recorder(_App(cfg, tray)).load_background()

        assert tray._state == AppState.ERROR
        assert "No model selected" in tray._icon.titles[-1], (
            f"visible tooltip must carry the refusal, got: {tray._icon.titles[-1]!r}"
        )
        # And the formatter agrees with the applied title.
        assert compute_tooltip(tray, tray._state, tray._message) == tray._icon.titles[-1]

    def test_refusal_tray_failure_is_warning_not_silent(self, monkeypatch, caplog):
        """If the tray update inside the refusal raises, it must WARN
        (diagnosable at INFO level), the previous DEBUG-only line left
        stuck tooltips invisible in production logs."""
        from unittest.mock import MagicMock

        from voice_typer.server.config import Config
        from voice_typer.server.model_manager import ModelManager
        from voice_typer.server.tray_types import AppState

        cfg = Config()
        cfg.model_size = NO_MODEL_SIZE
        cfg.asr_backend = "whisper"
        tray = MagicMock()
        tray.set_state.side_effect = RuntimeError("tray exploded")

        class _App:
            def __init__(self, config, tray):
                self.config = config
                self.tray = tray
                self._shutting_down = False

        class _Recorder(ModelManager):
            def __init__(self, app):
                self._app = app
                self._pending_dictation = False

            def _model_downloaded_precheck(self) -> bool:
                return False

        with caplog.at_level("WARNING", logger="voice_typer.server.model_manager"):
            _Recorder(_App(cfg, tray)).load_background()

        assert any("tray update for load refusal failed" in r.getMessage() for r in caplog.records), (
            "refusal tray failure must WARN"
        )
        # State must be attempted as ERROR even though the write raised.
        assert tray.set_state.call_args[0][0] == AppState.ERROR
