"""Recovery-path tests for microphone change after a failed recorder build.

When the background recorder build fails (no audio device at startup),
``AppLazyHub.recorder`` re-raises the stored build error on every access,
and may return ``None`` when construction was short-circuited. Changing the
microphone must still persist the choice, attempt a rebuild with the new
device, and notify the user instead of letting that stale failure abort
the handler.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from voice_typer.server import settings_controller as sc_mod
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
        self._audio_processor = MagicMock()
        self._stub_recorder = recorder
        self._live_recorder = None if isinstance(recorder, BaseException) else recorder

    @property
    def recorder(self):  # noqa: ANN204 - stub mirrors the app property
        if isinstance(self._stub_recorder, BaseException):
            raise self._stub_recorder
        return self._live_recorder

    @recorder.setter
    def recorder(self, value: object) -> None:
        self._live_recorder = value


class _StubRecorder:
    def __init__(self, recording: bool = False) -> None:
        self.recording = recording


def _patch_recorder(monkeypatch, captured: list) -> None:
    class _FakeRecorder:
        def __init__(self, config, audio_processor=None) -> None:
            captured.append({"config": config, "audio_processor": audio_processor})

    monkeypatch.setattr(sc_mod, "Recorder", _FakeRecorder)


def test_mic_change_survives_failed_recorder_build(monkeypatch) -> None:
    app = _StubApp(RuntimeError("No audio device at startup"))
    captured: list = []
    _patch_recorder(monkeypatch, captured)
    SettingsController(app).select_microphone("mic-1")
    assert app.config.microphone == "mic-1"
    assert len(captured) == 1, "rebuild must be attempted after prior build failure"
    assert captured[0]["config"] is app.config
    app.tray.notify.assert_called_once()


def test_mic_change_survives_missing_recorder(monkeypatch) -> None:
    app = _StubApp(None)
    captured: list = []
    _patch_recorder(monkeypatch, captured)
    SettingsController(app).select_microphone("mic-1")
    assert app.config.microphone == "mic-1"
    assert len(captured) == 1, "rebuild must be attempted when no active recorder"
    app.tray.notify.assert_called_once()


def test_mic_change_still_defers_while_recording(monkeypatch) -> None:
    app = _StubApp(_StubRecorder(recording=True))
    captured: list = []
    _patch_recorder(monkeypatch, captured)
    SettingsController(app).select_microphone("mic-2")
    assert app.config.microphone == "mic-2"
    assert captured == [], "must NOT recreate Recorder while recording"
    app.tray.notify.assert_called_once()


def test_mic_change_rebuild_failure_still_notifies(monkeypatch) -> None:
    app = _StubApp(RuntimeError("No audio device at startup"))

    def _boom(config, audio_processor=None):
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(sc_mod, "Recorder", _boom)
    SettingsController(app).select_microphone("mic-9")
    assert app.config.microphone == "mic-9"
    app.tray.notify.assert_called_once()
