"""Tests for the genuine "no model selected" state.

``model_size == \"\"`` (the ``NO_MODEL_SIZE`` sentinel in
``model_registry.py``) means the user has NO active model — the config
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
        """model_size=\"\" loads as-is — no reset to DEFAULT_MODEL_SIZE,
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
        """Sanity check: only the real sentinel is preserved — garbage
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
        qwen) may render as active — even backend-keyed rows."""
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
        refuses with a \"No model selected\" message — not a claim that a
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
                # Skip the real init (registry construction etc.) — the
                # refusal path only needs the app + pending-dictation flag.
                self._app = app
                self._pending_dictation = False

            def _model_downloaded_precheck(self) -> bool:
                return False

            def _notify_model_load_refused(self, error, *, backend):
                # ``ModelNotDownloadedError`` is a ``RuntimeError`` — its
                # message lives in ``args[0]`` (no ``.message`` attr).
                captured.append(str(error))
                assert isinstance(error, ModelNotDownloadedError)

        _Recorder(_App()).load_background()

        assert captured, "load refusal must have run"
        assert "No model selected" in captured[0], f"refusal message must say 'No model selected', got: {captured[0]}"
