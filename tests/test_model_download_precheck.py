"""Tests for the fast model-existence pre-check + tooltip model gating.

2026-08-15 user request: on startup the app must check whether the
configured model actually EXISTS before entering the "Loading model"
state. When it is missing:

- the tray tooltip must NOT advertise the stale ``[small.en]`` suffix
  (the name comes from ``model_size`` in config, which survives the
  model being deleted / never downloaded);
- the tray state message and the Windows notification must be GENERIC
  ("Open the Models page to download a model") with NO model/backend
  name;
- ``load_background`` must refuse BEFORE the heavy engine import /
  LOADING state (the load path would raise ``ModelNotDownloadedError``
  anyway — the registry re-raises for a missing primary, no whisper
  fallback).

The canonical per-backend "downloaded" semantics live in
``service/model.py::_compute_model_status``; the fast single-model
probe ``tray_models.is_active_model_downloaded`` mirrors them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.config import Config
from voice_typer.server.model_manager import ModelManager
from voice_typer.server.tray import TrayIcon
from voice_typer.server.tray_types import AppState


class _MockController:
    """Minimal TrayController protocol stub (mirrors tray test helpers)."""

    def toggle_dictation(self) -> None:
        pass

    def change_microphone(self, mic_id: str | None) -> None:
        pass

    def change_model(self, model: str) -> None:
        pass

    def quit_app(self) -> None:
        pass

    def undo_last(self) -> None:
        pass

    def restart_app(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _fresh_availability_cache():
    """Clear the module-level HF availability TTL cache before/after each
    test so a download marker created in one test doesn't leak into the
    next (and vice versa)."""
    from voice_typer.server.tray_models import invalidate_model_availability_cache

    invalidate_model_availability_cache()
    yield
    invalidate_model_availability_cache()


def _make_whisper_repo_dir(config_dir: Path, model_size: str) -> Path:
    """Simulate a downloaded whisper model in the HF cache by writing the
    ``refs/main`` marker file the availability probe checks."""
    from voice_typer.server.model_registry import get_model_metadata

    meta = get_model_metadata(model_size)
    assert meta is not None, f"model_size {model_size!r} not in MODEL_REGISTRY"
    repo_dir = (
        config_dir
        / "huggingface"
        / "hub"
        / f"models--{meta.repo_id.replace('/', '--')}"
    )
    (repo_dir / "refs").mkdir(parents=True, exist_ok=True)
    (repo_dir / "refs" / "main").write_text("abc123\n")
    return repo_dir


class TestIsActiveModelDownloaded:
    """``tray_models.is_active_model_downloaded`` — the fast probe."""

    def test_non_config_object_returns_true(self, tmp_config_dir):
        """Test doubles (SimpleNamespace) must NOT probe the real user's
        HF cache — return True so no pre-check / tooltip gate misfires."""
        from voice_typer.server.tray_models import is_active_model_downloaded

        cfg = SimpleNamespace(asr_backend="whisper", model_size="small.en")
        assert is_active_model_downloaded(cfg) is True

    def test_whisper_missing_model_false(self, tmp_config_dir):
        cfg = Config(asr_backend="whisper", model_size="small.en")
        from voice_typer.server.tray_models import is_active_model_downloaded

        assert is_active_model_downloaded(cfg) is False

    def test_whisper_downloaded_true(self, tmp_config_dir):
        _make_whisper_repo_dir(tmp_config_dir, "small.en")
        cfg = Config(asr_backend="whisper", model_size="small.en")
        from voice_typer.server.tray_models import is_active_model_downloaded

        assert is_active_model_downloaded(cfg) is True

    def test_cloud_backend_returns_true(self, tmp_config_dir):
        """Cloud backends have no local model — nothing to gate."""
        from voice_typer.server.tray_models import is_active_model_downloaded

        assert is_active_model_downloaded(Config(asr_backend="groq", model_size="small.en")) is True

    def test_qwen_path_dir_true(self, tmp_config_dir):
        """Configured qwen_model_path pointing at an existing dir counts
        as downloaded (mirrors service/model.py)."""
        from voice_typer.server.tray_models import is_active_model_downloaded

        model_dir = tmp_config_dir / "qwen-onnx"
        model_dir.mkdir(parents=True)
        cfg = Config(asr_backend="qwen", qwen_model_path=str(model_dir))
        assert is_active_model_downloaded(cfg) is True

    def test_qwen_missing_false(self, tmp_config_dir):
        from voice_typer.server.tray_models import is_active_model_downloaded

        cfg = Config(asr_backend="qwen", qwen_model_path=None)
        assert is_active_model_downloaded(cfg) is False

    def test_parakeet_path_dir_true(self, tmp_config_dir):
        from voice_typer.server.tray_models import is_active_model_downloaded

        model_dir = tmp_config_dir / "parakeet-onnx"
        model_dir.mkdir(parents=True)
        cfg = Config(asr_backend="parakeet", parakeet_model_path=str(model_dir))
        assert is_active_model_downloaded(cfg) is True

    def test_parakeet_missing_false(self, tmp_config_dir):
        from voice_typer.server.tray_models import is_active_model_downloaded

        cfg = Config(asr_backend="parakeet", parakeet_model_path=None)
        assert is_active_model_downloaded(cfg) is False


class TestComputeTooltipModelSuffix:
    """The ``[model]`` suffix only appears when the model is on disk."""

    def test_missing_model_hides_suffix(self, tmp_config_dir):
        tray = TrayIcon(
            controller=_MockController(),
            config=Config(asr_backend="whisper", model_size="small.en"),
        )
        tooltip = tray._compute_tooltip(
            AppState.ERROR,
            "The model is not downloaded yet. Open the Models page to download a model.",
        )
        assert "[small.en]" not in tooltip, (
            f"A model that is NOT downloaded must not be advertised in the tooltip. Got: {tooltip!r}"
        )
        # The generic message survives.
        assert "Open the Models page to download a model" in tooltip

    def test_downloaded_model_shows_suffix(self, tmp_config_dir):
        _make_whisper_repo_dir(tmp_config_dir, "small.en")
        tray = TrayIcon(
            controller=_MockController(),
            config=Config(asr_backend="whisper", model_size="small.en"),
        )
        tooltip = tray._compute_tooltip(AppState.IDLE, "")
        assert "[small.en]" in tooltip, (
            f"A downloaded model SHOULD be named in the tooltip. Got: {tooltip!r}"
        )


def _make_mm(config: Config) -> tuple[ModelManager, MagicMock]:
    """ModelManager with a REAL Config (so the pre-check probe runs) but
    every heavy dependency mocked."""
    import threading

    app = MagicMock(name="app")
    app.config = config
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()
    app._config_mutation_lock = threading.RLock()
    app.tray = MagicMock()
    app.recorder = MagicMock()
    app.recorder.recording = False
    app._busy_event = MagicMock()
    app._busy_event.is_set.return_value = True

    mm = ModelManager(app)

    mock_registry = MagicMock(name="registry")
    mock_registry.available_backends = ["whisper"]
    mock_registry.active_name = "whisper"
    mock_registry.get_active.return_value = None
    mock_registry.load_with_fallback.return_value = MagicMock(name="engine")
    mm._registry = mock_registry
    mm._ensure_engine = MagicMock()
    mm.touch_model = MagicMock()
    mm._evict_lru_model = MagicMock()
    return mm, app


class TestLoadBackgroundPrecheck:
    """``load_background`` refuses early when the model is missing."""

    def test_missing_model_refuses_before_heavy_import(self, tmp_config_dir):
        mm, app = _make_mm(Config(asr_backend="whisper", model_size="small.en"))

        mm.load_background()

        # The heavy engine construction + load must NOT run.
        mm._ensure_engine.assert_not_called()
        mm._registry.load_with_fallback.assert_not_called()
        # Tray went ERROR with a GENERIC message (no model name).
        states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert any("ERROR" in str(s) for s in states), f"expected ERROR state, got {states}"
        msgs = [c.args[1] for c in app.tray.set_state.call_args_list if len(c.args) > 1]
        assert any("Open the Models page to download a model" in (m or "") for m in msgs), msgs
        assert all("small.en" not in (m or "") for m in msgs), (
            "refusal message must NOT name the missing model"
        )
        # Windows notification is generic too — no backend name.
        notified = [c.args[1] for c in app.tray.notify.call_args_list]
        assert any("Open the Models page to download a model" in (m or "") for m in notified), notified
        assert all("Whisper" not in (m or "") for m in notified), notified
        # A pending dictation must be cleared (no auto-start loop).
        assert app._pending_dictation is False

    def test_downloaded_model_proceeds_to_load(self, tmp_config_dir):
        _make_whisper_repo_dir(tmp_config_dir, "small.en")
        mm, app = _make_mm(Config(asr_backend="whisper", model_size="small.en"))

        mm.load_background()

        mm._ensure_engine.assert_called_once()
        mm._registry.load_with_fallback.assert_called_once()
        # Ended in a non-ERROR state (Ready).
        states = [c.args[0] for c in app.tray.set_state.call_args_list]
        assert not any("ERROR" in str(s) for s in states), f"no ERROR expected, got {states}"


class TestGenericNotDownloadedMessages:
    """The user-facing not-downloaded strings must not name the backend /
    model (2026-08-15 user request)."""

    def test_state_message_generic(self):
        from voice_typer.server import i18n

        s = i18n.t("state.model_manager.model_not_downloaded")
        assert "Open the Models page to download a model." in s
        assert "{backend}" not in s
        assert "model is not downloaded yet" in s

    def test_notify_message_generic(self):
        from voice_typer.server import i18n

        s = i18n.t("notify.model_manager.model_not_downloaded")
        assert "Open the Models page to download a model." in s
        assert "{backend}" not in s
        assert "model is not downloaded yet" in s
