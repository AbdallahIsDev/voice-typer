"""CR-11 regression: service.download_model must gate HuggingFace
downloads on explicit user consent.

Previously the IPC handler ``service.download_model`` called
``snapshot_download(...)`` (Whisper-family) and
``download_parakeet_weights()`` (Parakeet) with NO check of
``config.huggingface_consent`` — the only consent gate lived in
``TranscriptionEngine._pre_download_model`` which is only invoked
from the engine load path, NOT the IPC download path.  Clicking
"Download" on the Models page therefore phoned home to
huggingface.co without the explicit GDPR Art. 13/44 consent that
``huggingface_consent`` was specifically designed to gate
(NEW-PRIV-005).

These tests verify the fix is purely additive:

- When ``huggingface_consent`` is False, neither ``snapshot_download``
  nor ``download_parakeet_weights`` is invoked, the return value
  contains ``consent_required: True``, and a ``consent_required``
  event is published to the event bus so the renderer can show the
  consent dialog.

- When ``huggingface_consent`` is True, the download IS invoked
  (existing flow preserved — no behavioural regression).

- The Qwen branch (which uses a local file path, not HuggingFace)
  and the unknown-model branch are exempt from the consent gate.
"""

import os
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import VoiceTyperService

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def captured_events(monkeypatch):
    """Capture all events pushed via ``event_bus.publish``.

    Mirrors the pattern in ``tests/test_download_progress_events.py``
    so the two test files stay consistent.
    """
    events: list[dict] = []
    import voice_typer.server.event_bus as event_bus_mod

    monkeypatch.setattr(event_bus_mod, "publish", lambda msg: events.append(msg) or True)
    return events


def _build_service(*, consent: bool) -> VoiceTyperService:
    """Build a VoiceTyperService with a mock app + explicit consent flag.

    ``consent`` is set as a REAL ``bool`` on the mock config (not a
    MagicMock auto-attribute) so the consent gate's
    ``bool(getattr(...))`` evaluates truthiness correctly.  This is
    what production code sees — the field is declared as
    ``huggingface_consent: bool = False`` in ``config.py``.
    """
    app = MagicMock()
    app.config.huggingface_consent = consent
    app.tray.notify = MagicMock()
    return VoiceTyperService(app)


# ── Whisper branch ────────────────────────────────────────────────────


class TestWhisperBranchConsentGate:
    """CR-11: Whisper-family download must be gated on huggingface_consent."""

    def test_whisper_download_blocked_without_consent(self, captured_events, monkeypatch):
        """When consent=False, snapshot_download is never called and the
        return value contains consent_required=True."""
        sd_calls: list[dict] = []

        def fake_snapshot(*args, **kwargs):
            sd_calls.append(kwargs)
            return "/fake/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        service = _build_service(consent=False)
        result = service.download_model("tiny.en")

        # Consent-gate return shape.
        assert result["success"] is False
        assert result["consent_required"] is True
        assert "consent" in result["error"].lower()
        assert result["model"] == "tiny.en"
        # snapshot_download must NOT have been invoked — not even the
        # local_files_only cache probe.
        assert sd_calls == [], f"snapshot_download must not be called when consent=False; got: {sd_calls}"
        # A consent_required event must have been published so the
        # renderer can show the consent dialog.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events, f"Expected at least one consent_required event; got: {captured_events}"
        assert consent_events[0]["data"]["provider"] == "huggingface"
        assert consent_events[0]["data"]["model"] == "tiny.en"
        # No download_progress events should fire on the blocked path
        # (the consent gate returns before _push_progress is reached).
        progress_events = [e for e in captured_events if e.get("type") == "download_progress"]
        assert progress_events == []

    def test_whisper_download_proceeds_with_consent(self, captured_events, monkeypatch):
        """When consent=True, snapshot_download IS invoked (existing flow preserved).

        This is the regression guard required by CR-11's proposed fix:
        "the fix is purely additive (a consent gate before the download
        call)" — it must NOT break the existing download flow when
        consent IS given.
        """
        sd_calls: list[dict] = []

        def fake_snapshot(*args, **kwargs):
            sd_calls.append(kwargs)
            # Succeed on both the local_files_only cache check AND any
            # subsequent network call so the existing fast-path
            # (cached → success) is exercised.
            return "/fake/path"

        monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)

        service = _build_service(consent=True)
        result = service.download_model("tiny.en")

        # Existing flow preserved.
        assert result["success"] is True
        assert result["model"] == "tiny.en"
        # snapshot_download MUST have been invoked at least once (the
        # local_files_only cache probe is the first call).
        assert sd_calls, "snapshot_download must be invoked when consent=True (existing flow preserved)"
        # No consent_required event should fire on the happy path.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []


# ── Parakeet branch ───────────────────────────────────────────────────


class TestParakeetBranchConsentGate:
    """CR-11: Parakeet download must be gated on huggingface_consent."""

    def test_parakeet_download_blocked_without_consent(self, captured_events, monkeypatch):
        """When consent=False, download_parakeet_weights is never called."""
        dpw_calls: list[tuple] = []

        def fake_download_parakeet_weights(*args, **kwargs):
            dpw_calls.append((args, kwargs))
            return True

        # asr_setup.download_parakeet_weights is imported locally
        # inside the parakeet branch (after the consent gate), so
        # patching the attribute on the asr_setup module is sufficient
        # — the local ``from ... import`` will re-bind to this fake.
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
        # download_parakeet_weights MUST have been invoked.
        assert dpw_calls, "download_parakeet_weights must be invoked when consent=True (existing flow preserved)"
        # No consent_required event should fire on the happy path.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []


# ── Additivity: non-HF branches must be unaffected ───────────────────


class TestConsentGateIsAdditive:
    """CR-11: The consent gate must NOT break the existing Qwen /
    unknown-model branches (which don't phone home to HuggingFace)."""

    def test_unknown_model_still_returns_error(self, captured_events):
        """An unknown model name still returns success=False without
        invoking any download function — the gate is only on the
        Whisper and Parakeet branches."""
        service = _build_service(consent=False)
        result = service.download_model("nonexistent-model")
        assert result["success"] is False
        assert "Unknown model" in result["error"]
        # No consent_required event should fire for unknown models.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []

    def test_qwen_branch_does_not_require_consent(self, captured_events, tmp_path):
        """Qwen uses a local file path (no HuggingFace call) so the consent
        gate does not apply — even with consent=False, a cached Qwen model
        still returns success=True."""
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
        """Qwen with no path still returns the existing config-error shape,
        even with consent=False."""
        service = _build_service(consent=False)
        service._app.config.qwen_model_path = None
        result = service.download_model("qwen")
        assert result["success"] is False
        assert "Qwen model path not configured" in result["error"]
        # No consent_required event for Qwen.
        consent_events = [e for e in captured_events if e.get("type") == "consent_required"]
        assert consent_events == []


# ── Defensive: missing config is treated as NOT consented ────────────


class TestConsentGateDefensive:
    """CR-11: if ``self._app.config`` is None (degenerate path), consent
    defaults to False — safe default per GDPR Art. 6/13."""

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

        result = service.download_model("tiny.en")
        assert result["success"] is False
        assert result["consent_required"] is True
        assert sd_calls == []
