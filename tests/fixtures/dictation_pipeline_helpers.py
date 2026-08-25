"""Shared factories for ``DictationPipeline`` tests.

The ``_TestApp`` / ``_make_app`` / ``_new_pipeline``
helpers were previously copy-defined inside a single catch-all test
module. Both the notify-once-flag suites
(``tests/app/test_notify_once_flags.py``) and the transcription
audio-stats suites (``tests/test_transcription_audio_stats.py``) build
pipelines against the same minimal non-magic app, so the factories
live here — ONE place to update when ``DictationPipeline``'s per-cycle
attribute set changes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from voice_typer.server.dictation_pipeline import DictationPipeline

__all__ = ["make_test_app", "new_pipeline"]


class _TestApp:
    """Minimal non-magic test app for DictationPipeline tests.

    Why a custom class instead of ``MagicMock``? MagicMock auto-creates
    a child mock for ANY attribute access, so ``getattr(app, "_flag",
    False)`` returns a truthy MagicMock rather than the ``False``
    default. The production code relies on the default —
    ``VoiceTyperApp`` does NOT pre-create the four notify-once flag
    attributes, so ``getattr(self._app, "_vocab_fail_notified",
    False)`` correctly defaults to ``False`` on a fresh app.

    Using this class lets the tests exercise that default-False
    semantics faithfully, and also lets us verify that the flag is
    set on the app (not the pipeline) after the first failure.
    """

    def __init__(self) -> None:
        # Attributes the pipeline reads — typed as MagicMock so we
        # can assert on call_args_list etc.
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
        # pre-declared — production code relies on getattr-default.

    # The remaining attributes the pipeline touches in the success
    # path (event_bus publish, etc.) are MagicMock-accessed via
    # __getattr__ fallback to keep this class small. We delegate
    # unknown attribute access to a per-instance MagicMock.
    def __getattr__(self, name: str) -> MagicMock:
        # Only called when the attribute is genuinely absent (i.e.
        # not declared in __init__). We do NOT want this for the
        # four notify-once flag names — they must default to False
        # via getattr-with-default, which requires AttributeError to
        # be raised when absent. So we re-raise AttributeError for
        # any name matching the flag pattern.
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
        }:
            raise AttributeError(name)
        # For other attributes, return a fresh MagicMock (auto-mock
        # behavior, like MagicMock itself).
        mock = MagicMock()
        # Cache it so subsequent accesses return the same mock.
        object.__setattr__(self, name, mock)
        return mock


def make_test_app() -> _TestApp:
    """Build a minimal test app for DictationPipeline.

    Unlike a bare ``MagicMock()``, this class does NOT auto-create
    the four notify-once flag attributes — so
    ``getattr(app, "_flag", False)`` correctly defaults to ``False``
    when the flag has never been set (mirroring production behavior
    on ``VoiceTyperApp``).
    """
    return _TestApp()


def new_pipeline(app: Any) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app``.

    Mirrors how ``RecordingController._stop_dictation`` constructs a
    new pipeline per transcription cycle (a-review Finding 2 root
    cause).
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
    return pipeline
