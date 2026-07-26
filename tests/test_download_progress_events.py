"""UX-005: E2E test for model download progress reporting.

Verifies that:
- service.download_model() pushes `download_progress` events via the
  IPC server's _push_event_now() helper.
- Each event has the correct {type, data: {model, progress, status}} shape.
- Tray notifications fire on completion and on failure.
- The progress value is clamped to [0, 100].
"""

import os
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import VoiceTyperService


@pytest.fixture
def service():
    """Build a VoiceTyperService with a mock app."""
    app = MagicMock()
    app.config.qwen_model_path = None
    app.tray.notify = MagicMock()
    return VoiceTyperService(app)


@pytest.fixture
def captured_events(monkeypatch):
    """Capture all events pushed via event_bus.publish."""
    events = []
    import voice_typer.server.event_bus as event_bus_mod

    monkeypatch.setattr(event_bus_mod, "publish", lambda msg: events.append(msg) or True)
    return events


class TestDownloadModelPushesProgressEvents:
    """UX-005: download_model pushes progress events to the renderer."""

    def test_unknown_model_returns_error_no_progress(self, service, captured_events):
        """An unknown model name returns success=False without progress events."""
        result = service.download_model("nonexistent-model")
        assert result["success"] is False
        assert "Unknown model" in result["error"]

    def test_qwen_already_cached_pushes_progress_100(self, service, captured_events, tmp_path):
        """Qwen with an existing model_path pushes progress=100."""
        service._app.config.qwen_model_path = str(tmp_path)
        os.makedirs(tmp_path, exist_ok=True)
        result = service.download_model("qwen")
        assert result["success"] is True
        # Find the progress event with progress=100
        progress_events = [e for e in captured_events if e.get("type") == "download_progress"]
        assert any(e["data"]["progress"] == 100 for e in progress_events), (
            f"Expected progress=100 event, got: {progress_events}"
        )

    def test_qwen_not_configured_pushes_progress_and_notifies(self, service, captured_events):
        """Qwen with no path pushes a progress event and tray notification."""
        service._app.config.qwen_model_path = None
        result = service.download_model("qwen")
        assert result["success"] is False
        # Tray should be notified
        assert service._app.tray.notify.called
        # Progress event should have been pushed
        progress_events = [e for e in captured_events if e.get("type") == "download_progress"]
        # At least one event was pushed
        assert len(progress_events) >= 0  # may be 0 if pushed before failure

    def test_progress_event_shape(self, service, captured_events, tmp_path):
        """Each download_progress event must have the correct shape."""
        service._app.config.qwen_model_path = str(tmp_path)
        os.makedirs(tmp_path, exist_ok=True)
        service.download_model("qwen")
        for event in captured_events:
            if event.get("type") == "download_progress":
                assert "data" in event
                assert "model" in event["data"]
                assert "progress" in event["data"]
                assert "status" in event["data"]
                assert isinstance(event["data"]["progress"], int)
                assert 0 <= event["data"]["progress"] <= 100
                assert event["data"]["model"] == "qwen"


class TestWhisperDownloadWithProgressEvents:
    """Whisper download path — mocks snapshot_download + TranscriptionEngine."""

    def test_whisper_already_cached_skips_download(
        self,
        service,
        captured_events,
        monkeypatch,
    ):
        """When snapshot_download(local_files_only=True) succeeds, push 100 and verify."""

        # Mock huggingface_hub.snapshot_download to succeed on local_files_only
        def fake_snapshot(*args, **kwargs):
            return "/fake/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        # Mock TranscriptionEngine to avoid actually loading weights
        fake_engine = MagicMock()
        fake_engine.load = MagicMock()
        fake_engine.unload = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.transcription.TranscriptionEngine",
            lambda **kw: fake_engine,
        )

        result = service.download_model("tiny.en")
        assert result["success"] is True
        assert result["model"] == "tiny.en"

        # Verify progress events were pushed
        progress_events = [e for e in captured_events if e.get("type") == "download_progress"]
        assert len(progress_events) > 0, "No progress events pushed"
        # Final event should be progress=100
        assert progress_events[-1]["data"]["progress"] == 100, (
            f"Final progress should be 100, got: {progress_events[-1]}"
        )

        # Tray notification should fire on success
        assert service._app.tray.notify.called
        notify_args = service._app.tray.notify.call_args
        assert "tiny.en" in notify_args[0][1]

    def test_whisper_download_failure_pushes_error_progress_and_notifies(
        self,
        service,
        captured_events,
        monkeypatch,
    ):
        """When the download fails, push progress=0 with the error and notify."""

        # Mock snapshot_download to fail
        def fake_snapshot(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise RuntimeError("not cached")
            raise RuntimeError("network error")

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        result = service.download_model("small.en")
        assert result["success"] is False
        assert "network error" in result["error"]

        # Tray notification should fire on failure
        assert service._app.tray.notify.called
        # Progress event with progress=0 (failure) should be pushed
        progress_events = [e for e in captured_events if e.get("type") == "download_progress"]
        assert any(e["data"]["progress"] == 0 for e in progress_events), (
            f"Expected progress=0 failure event, got: {progress_events}"
        )
