"""NEW-PRIV-011: cancelable HuggingFace download — Python-level tests."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    return tmp_path


class TestCancelModelDownloadMechanism:
    """Verify the cancel mechanism works at the Python service level."""

    def test_cancel_returns_false_when_no_download_active(self, tmp_config_dir):
        """When no download is in progress, cancel_model_download
        returns {cancelled: False}."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        # No download is active — _download_cancel_event is None.
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_cancel_returns_true_when_download_active(self, tmp_config_dir):
        """When a download IS in progress, cancel_model_download sets
        the event and returns {cancelled: True}."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        # Simulate an active download by creating the cancel event.
        service._download_cancel_event = threading.Event()
        assert not service._download_cancel_event.is_set()

        result = service.cancel_model_download()
        assert result == {"cancelled": True}
        assert service._download_cancel_event.is_set(), (
            "cancel_model_download must set the event so the polling loop exits"
        )

    def test_cancel_event_is_clearable(self, tmp_config_dir):
        """The service must allow clearing the cancel event for the
        next download."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        service._download_cancel_event = threading.Event()
        service.cancel_model_download()
        # After cancellation, the service should allow a new download
        # by resetting _download_cancel_event to None (done in
        # download_model after the polling loop exits).
        service._download_cancel_event = None
        # A subsequent cancel should return False (no active download).
        result = service.cancel_model_download()
        assert result == {"cancelled": False}

    def test_download_cancel_event_starts_as_none(self, tmp_config_dir):
        """Fresh service instance must have _download_cancel_event = None."""
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            config = type("FakeConfig", (), {})()

        service = VoiceTyperService(FakeApp())
        assert service._download_cancel_event is None
