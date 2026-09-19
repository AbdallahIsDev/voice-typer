"""Recovery-path tests for microphone change after a failed recorder build.

When the background recorder build fails (no audio device at startup),
``AppLazyHub.recorder`` re-raises the stored build error on every access,
and may return ``None`` when construction was short-circuited. Changing the
microphone must still persist the choice and notify the user instead of
letting that stale failure abort the handler.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from voice_typer.server.settings_controller import SettingsController


class _StubConfig:
    def __init__(self) -> None:
        self.microphone: str | None = None

    def save(self) -> bool:
        return True


class _StubApp:
    """Minimal stand-in exposing the state ``SettingsController`` touches."""

    def __init__(self, recorder: object) -> None:
        self._config_mutation_lock = threading.Lock()
        self.config = _StubConfig()
        self.tray = MagicMock()
        self._stub_recorder = recorder

    @property
    def recorder(self):  # noqa: ANN204 - stub mirrors the app property
        if isinstance(self._stub_recorder, BaseException):
            raise self._stub_recorder
        return self._stub_recorder


class _StubRecorder:
    def __init__(self, recording: bool = False) -> None:
        self.recording = recording


def test_mic_change_survives_failed_recorder_build() -> None:
    app = _StubApp(RuntimeError("No audio device at startup"))
    SettingsController(app).select_microphone("mic-1")
    assert app.config.microphone == "mic-1"
    app.tray.notify.assert_called_once()


def test_mic_change_survives_missing_recorder() -> None:
    app = _StubApp(None)
    SettingsController(app).select_microphone("mic-1")
    assert app.config.microphone == "mic-1"
    app.tray.notify.assert_called_once()


def test_mic_change_still_defers_while_recording() -> None:
    app = _StubApp(_StubRecorder(recording=True))
    SettingsController(app).select_microphone("mic-2")
    assert app.config.microphone == "mic-2"
    app.tray.notify.assert_called_once()
