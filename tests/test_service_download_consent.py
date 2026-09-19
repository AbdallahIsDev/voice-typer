"""regression: service.download_model must gate HuggingFace"""

import os
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import VoiceTyperService


@pytest.fixture
def captured_events(monkeypatch):
    """Capture all events pushed via ``event_bus.publish``."""
    events: list[dict] = []
    import voice_typer.server.event_bus as event_bus_mod

    monkeypatch.setattr(event_bus_mod, "publish", lambda msg: events.append(msg) or True)
    return events


def _build_service(*, consent: bool) -> VoiceTyperService:
    """Build a VoiceTyperService with a mock app + explicit consent flag."""
    app = MagicMock()
    app.config.huggingface_consent = consent
    app.tray.notify = MagicMock()
    return VoiceTyperService(app)


class TestWhisperBranchConsentGate:
    """Whisper-family download must be gated on huggingface_consent."""

    def test_whisper_download_blocked_without_consent(self, captured_events, monkeypatch):
        """When consent=False, snapshot_download is never called and the"""
        sd_calls: list[dict] = []

        def fake_snapshot(*args, **kwargs):
            sd_calls.append(kwargs)
            return "/fake/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        service = _build_service(consent=False)
        result = service.download_model("tiny")

        # Consent-gate return shape.
        assert result["success"] is False
        assert result["consent_required"] is True
        assert "consent" in result["error"].lower()
        assert result["model"] == "tiny"
        # snapshot_download must NOT have been invoked, not even the
        assert sd_calls == [], f"snapshot_download must not be called when consent=False; got: {sd_calls}"
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events, f"Expected at least one consent_required event; got: {captured_events}"
        assert consent_events[0]["data"]["provider"] == "huggingface"
        assert consent_events[0]["data"]["model"] == "tiny"
        # No download_progress events should fire on the blocked path
        progress_events = [e for e in captured_events if e.get("type") == "download_progress"]
        assert progress_events == []

    def test_whisper_download_proceeds_with_consent(self, captured_events, monkeypatch):
        """When consent=True, snapshot_download IS invoked (existing flow preserved)."""
        sd_calls: list[dict] = []

        def fake_snapshot(*args, **kwargs):
            sd_calls.append(kwargs)
            # Succeed on both the local_files_only cache check AND any
            return "/fake/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        service = _build_service(consent=True)
        result = service.download_model("tiny")

        # Existing flow preserved.
        assert result["success"] is True
        assert result["model"] == "tiny"
        assert sd_calls, "snapshot_download must be invoked when consent=True (existing flow preserved)"
        # No consent_required event should fire on the happy path.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []


class TestParakeetBranchConsentGate:
    """Parakeet download must be gated on huggingface_consent."""

    def test_parakeet_download_blocked_without_consent(self, captured_events, monkeypatch):
        """When consent=False, download_parakeet_weights is never called."""
        dpw_calls: list[tuple] = []

        def fake_download_parakeet_weights(*args, **kwargs):
            dpw_calls.append((args, kwargs))
            return True

        monkeypatch.setattr(
            "voice_typer.server.asr_setup.download_parakeet_weights",
            fake_download_parakeet_weights,
        )

        service = _build_service(consent=False)
        result = service.download_model("parakeet")

        # Consent-gate return shape.
        assert result["success"] is False
        assert result["consent_required"] is True
        assert "consent" in result["error"].lower()
        assert result["model"] == "parakeet"
        # download_parakeet_weights must NOT have been invoked.
        assert dpw_calls == [], f"download_parakeet_weights must not be called when consent=False; got: {dpw_calls}"
        # A consent_required event must have been published.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events, f"Expected at least one consent_required event; got: {captured_events}"
        assert consent_events[0]["data"]["provider"] == "huggingface"
        assert consent_events[0]["data"]["model"] == "parakeet"

    def test_parakeet_download_proceeds_with_consent(self, captured_events, monkeypatch):
        """When consent=True, download_parakeet_weights IS invoked."""
        dpw_calls: list[tuple] = []

        def fake_download_parakeet_weights(*args, **kwargs):
            dpw_calls.append((args, kwargs))
            return True

        monkeypatch.setattr(
            "voice_typer.server.asr_setup.download_parakeet_weights",
            fake_download_parakeet_weights,
        )

        service = _build_service(consent=True)
        result = service.download_model("parakeet")

        # Existing flow preserved.
        assert result["success"] is True
        assert result["model"] == "parakeet"
        assert dpw_calls, "download_parakeet_weights must be invoked when consent=True (existing flow preserved)"
        # No consent_required event should fire on the happy path.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []


class TestConsentGateIsAdditive:
    """The consent gate must NOT break the existing Qwen /"""

    def test_unknown_model_still_returns_error(self, captured_events):
        """An unknown model name still returns success=False without"""
        service = _build_service(consent=False)
        result = service.download_model("nonexistent-model")
        assert result["success"] is False
        assert "Unknown model" in result["error"]
        # No consent_required event should fire for unknown models.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []

    def test_qwen_branch_does_not_require_consent(self, captured_events, tmp_path):
        """Qwen uses a local file path (no HuggingFace call) so the consent"""
        service = _build_service(consent=False)
        service._app.config.qwen_model_path = str(tmp_path)
        os.makedirs(tmp_path, exist_ok=True)
        result = service.download_model("qwen")
        assert result["success"] is True
        assert result["model"] == "qwen"
        # No consent_required event for Qwen.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []

    def test_qwen_not_configured_does_not_require_consent(self, captured_events):
        """Qwen with no path still returns the existing config-error shape,"""
        service = _build_service(consent=False)
        service._app.config.qwen_model_path = None
        result = service.download_model("qwen")
        assert result["success"] is False
        assert "Qwen model path not configured" in result["error"]
        # No consent_required event for Qwen.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []


class TestConsentGateDefensive:
    """if ``self._app.config`` is None (degenerate path), consent"""

    def test_missing_config_blocks_whisper_download(self, captured_events, monkeypatch):
        """When config is None, the gate treats consent as False and blocks."""
        sd_calls: list[dict] = []

        def fake_snapshot(*args, **kwargs):
            sd_calls.append(kwargs)
            return "/fake/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        app = MagicMock()
        app.config = None  # degenerate path
        app.tray.notify = MagicMock()
        service = VoiceTyperService(app)

        result = service.download_model("tiny")
        assert result["success"] is False
        assert result["consent_required"] is True
        assert sd_calls == []
