"""Regression tests for Round 1 privacy + UX fixes.

Covers:
  - NEW-PRIV-004: in-app privacy policy disclosure (About page)
  - NEW-PRIV-005: huggingface_consent config field + enforcement
  - NEW-PRIV-006: per-provider cloud consent flags + ConsentRequiredError
  - NEW-PRIV-009: voice_biometric_consent config field
  - NEW-UX-023: About page exposes latest version from GitHub releases API
  - NEW-UX-025: Settings Troubleshooting section has Help/Diagnostics/Bug links
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── NEW-PRIV-005/006/009: consent config fields ──────────────────────


class TestNewPrivConsentConfigFields:
    """The Config dataclass must declare the new consent flags."""

    def test_huggingface_consent_field_exists(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert hasattr(cfg, "huggingface_consent")
        assert cfg.huggingface_consent is False  # default: not given

    def test_cloud_per_provider_consent_fields_exist(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert hasattr(cfg, "cloud_openai_consent")
        assert hasattr(cfg, "cloud_groq_consent")
        assert hasattr(cfg, "cloud_deepgram_consent")
        # Defaults: not given
        assert cfg.cloud_openai_consent is False
        assert cfg.cloud_groq_consent is False
        assert cfg.cloud_deepgram_consent is False

    def test_voice_biometric_consent_field_exists(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert hasattr(cfg, "voice_biometric_consent")
        assert cfg.voice_biometric_consent is False

    def test_consent_fields_round_trip_via_save_load(self, tmp_path, monkeypatch):
        """Consent flags must survive save → load round trip."""
        monkeypatch.setattr(
            "voice_typer.server.config._config_dir", lambda: tmp_path
        )
        from voice_typer.server.config import Config
        cfg = Config()
        cfg.huggingface_consent = True
        cfg.cloud_openai_consent = True
        cfg.voice_biometric_consent = True
        assert cfg.save() is True

        loaded = Config.load()
        assert loaded.huggingface_consent is True
        assert loaded.cloud_openai_consent is True
        assert loaded.cloud_groq_consent is False  # untouched
        assert loaded.voice_biometric_consent is True

    def test_consent_fields_settable_via_ipc_allowlist(self):
        """NEW-PRIV-005/006/009: validate_config_update accepts the
        new consent fields so the renderer can set them."""
        from voice_typer.server.config import validate_config_update
        validated, errors = validate_config_update({
            "huggingface_consent": True,
            "cloud_openai_consent": True,
            "cloud_groq_consent": False,
            "cloud_deepgram_consent": True,
            "voice_biometric_consent": True,
        })
        assert errors == []
        assert validated["huggingface_consent"] is True
        assert validated["cloud_openai_consent"] is True
        assert validated["cloud_deepgram_consent"] is True
        assert validated["voice_biometric_consent"] is True

    def test_consent_fields_reject_non_bool(self):
        """Consent fields must be bool — non-bool values are rejected."""
        from voice_typer.server.config import validate_config_update
        validated, errors = validate_config_update({
            "huggingface_consent": "yes",  # string, not bool
        })
        assert errors, "Non-bool consent value should be rejected"
        assert "huggingface_consent" not in validated


# ── NEW-PRIV-006: ConsentRequiredError + CloudEngine ─────────────────


class TestNewPriv006CloudConsentEnforcement:
    """CloudEngine refuses to transcribe without consent."""

    def test_consent_required_error_is_runtime_error(self):
        from voice_typer.server.cloud_engines import ConsentRequiredError
        assert issubclass(ConsentRequiredError, RuntimeError)

    def test_cloud_engine_not_loaded_without_consent(self):
        from voice_typer.server.cloud_engines import CloudEngine
        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=False,
        )
        # is_loaded should be False because consent is False.
        assert eng.is_loaded is False

    def test_cloud_engine_loaded_with_consent(self):
        from voice_typer.server.cloud_engines import CloudEngine
        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=True,
        )
        assert eng.is_loaded is True

    def test_transcribe_raises_without_consent(self):
        """NEW-PRIV-006: transcribe() must raise ConsentRequiredError
        when consent hasn't been given, even if API key is present."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine, ConsentRequiredError

        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=False,
        )
        audio = np.zeros(16000, dtype=np.float32)  # 1 second of silence
        with pytest.raises(ConsentRequiredError):
            eng.transcribe(audio)

    def test_transcribe_does_not_raise_with_consent(self):
        """When consent IS given, transcribe should NOT raise
        ConsentRequiredError (it will fail later with a network error,
        but that's a different code path)."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine, ConsentRequiredError

        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=True,
        )
        audio = np.zeros(16000, dtype=np.float32)
        # Empty audio returns "" without sending a request.
        # Use empty array to avoid actually hitting the network.
        empty_audio = np.array([], dtype=np.float32)
        result = eng.transcribe(empty_audio)
        assert result == ""


# ── NEW-PRIV-005: HuggingFace consent enforcement in transcription ──


class TestNewPriv005HuggingFaceConsent:
    """WhisperEngine._pre_download_model respects huggingface_consent."""

    def test_pre_download_returns_early_without_consent(self, monkeypatch, tmp_path):
        """When consent is False and model is not cached, _pre_download_model
        returns early WITHOUT calling snapshot_download with local_files_only=False."""
        # We can't easily construct a TranscriptionEngine without mocking a lot
        # of dependencies, so we monkeypatch snapshot_download to detect
        # whether it was called with local_files_only=False (which would
        # mean a real network download).
        calls = []

        def fake_snapshot_download(**kwargs):
            calls.append(kwargs)
            if kwargs.get("local_files_only"):
                # Cache miss — raise to simulate "not cached"
                raise FileNotFoundError("not in cache")
            # Real download attempt — this is what we want to PREVENT
            # when consent is False.
            return "/fake/path"

        # Inject the fake into the huggingface_hub module's namespace
        # so the `from huggingface_hub import snapshot_download` in
        # _pre_download_model picks it up.
        import sys
        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        # Build a minimal engine stub.  We only need _pre_download_model
        # and a config attribute.
        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)  # bypass __init__
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        # Use a non-Whisper model size to test the early-return path.
        engine._pre_download_model("parakeet")
        # Should not have called snapshot_download at all.
        assert calls == [], (
            f"_pre_download_model should not call snapshot_download for "
            f"non-Whisper models.  Calls: {calls}"
        )

    def test_pre_download_skips_download_without_consent(self, monkeypatch):
        """When consent is False and model is not cached, _pre_download_model
        should NOT trigger a network download."""
        network_calls = []

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")  # cache miss
            network_calls.append(kwargs)  # this would be a real download
            return "/fake/path"

        # Inject the fake into the huggingface_hub module's namespace
        # so the `from huggingface_hub import snapshot_download` in
        # _pre_download_model picks it up.
        import sys
        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        # Should return without doing a network download.
        engine._pre_download_model("small.en")
        assert network_calls == [], (
            f"_pre_download_model should NOT do a network download when "
            f"huggingface_consent is False.  Network calls: {network_calls}"
        )


# ── NEW-PRIV-004 / NEW-UX-021/022/023/025: About + Settings UI ──────


class TestNewPriv004AboutPagePrivacy:
    """NEW-PRIV-004: About page has expanded privacy disclosure."""

    def test_about_page_has_privacy_section(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        # The page must mention all the privacy-relevant topics.
        assert "Audio processing" in src, "Missing 'Audio processing' section"
        assert "Model weights" in src, "Missing 'Model weights' section"
        assert "HuggingFace" in src, "Missing HuggingFace disclosure"
        assert "Cloud ASR" in src, "Missing 'Cloud ASR' section"
        assert "Voice biometrics" in src, "Missing 'Voice biometrics' section"
        assert "BIPA" in src, "Missing BIPA mention (Illinois biometric law)"

    def test_about_page_has_updates_section(self):
        """NEW-UX-023: About page has a Check for Updates button."""
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "Check for Updates" in src, "Missing 'Check for Updates' button"
        assert "LATEST_RELEASE_API" in src, "Missing GitHub releases API URL"
        assert "api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest" in src

    def test_about_page_has_help_links(self):
        """NEW-UX-021: About page has Documentation + Changelog links."""
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "README_URL" in src or "README.md" in src
        assert "CHANGELOG_URL" in src or "CHANGELOG.md" in src

    def test_about_page_has_feedback_links(self):
        """NEW-UX-022: About page has feedback / report-a-bug links."""
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "Report a Bug" in src or "Report an Issue" in src
        assert "github.com/AbdallahIsDev/voice-typer/issues" in src


class TestNewUx025SettingsTroubleshoot:
    """NEW-UX-025: Settings Troubleshoot section has real diagnostic actions."""

    def test_settings_has_diagnostics_button(self):
        settings = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings.read_text(encoding="utf-8")
        assert "Diagnostics" in src, "Missing 'Diagnostics' button"
        assert "Help & FAQ" in src, "Missing 'Help & FAQ' button"
        assert "Report a Bug" in src, "Missing 'Report a Bug' button"
        # The "View Logs" button label should be clarified (not just "View Logs").
        assert "Open Log Folder" in src, (
            "Missing 'Open Log Folder' label (NEW-UX-025: clarify that it opens the folder, not a viewer)"
        )

    def test_settings_has_on_navigate_prop(self):
        """NEW-UX-025: Settings component accepts onNavigate so the
        Diagnostics button can route to the About page."""
        settings = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings.read_text(encoding="utf-8")
        assert "onNavigate" in src, "Settings should accept onNavigate prop"
