"""Shared factories for ``DictationPipeline`` tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline

__all__ = ["make_test_app", "new_pipeline"]


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests."""

    def __init__(self) -> None:
        # Attributes the pipeline reads, typed as MagicMock so we
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        self.config.crash_recovery_enabled = False
        self.config.templates_enabled = True
        self.config.log_transcriptions = False
        self.config.model_size = "tiny.en"
        self.config.device = "cpu"
        self.history_db = MagicMock()
        self._vocabulary_manager: object = None
        self._template_manager: object = None
        self._crash_recovery = MagicMock()
        self._last_transcription: object = None
        self.models = MagicMock()
        self.recording = MagicMock()
        # NOTE: the four notify-once flags are intentionally NOT

    # The remaining attributes the pipeline touches in the success
    def __getattr__(self, name: str) -> MagicMock:
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
        }:
            raise AttributeError(name)
        # For other attributes, return a fresh MagicMock (auto-mock
        mock = MagicMock()
        # Cache it so subsequent accesses return the same mock.
        object.__setattr__(self, name, mock)
        return mock


def make_test_app() -> _TestApp:
    """Build a minimal test app for DictationPipeline."""
    return _TestApp()


def new_pipeline(app: Any) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    return pipeline
