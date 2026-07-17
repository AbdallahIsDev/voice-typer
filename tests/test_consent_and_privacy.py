"""Tests for privacy consent flags, cloud/HF/biometric consent enforcement,
and about-page privacy disclosures."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestConfigDeclaresConsentFlags:
    """The Config dataclass declares the consent flags."""

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
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
        """validate_config_update accepts the consent fields so the renderer can set them."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "huggingface_consent": True,
                "cloud_openai_consent": True,
                "cloud_groq_consent": False,
                "cloud_deepgram_consent": True,
                "voice_biometric_consent": True,
            }
        )
        assert errors == []
        assert validated["huggingface_consent"] is True
        assert validated["cloud_openai_consent"] is True
        assert validated["cloud_deepgram_consent"] is True
        assert validated["voice_biometric_consent"] is True

    def test_consent_fields_reject_non_bool(self):
        """Consent fields must be bool — non-bool values are rejected."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "huggingface_consent": "yes",
            }
        )
        assert errors, "Non-bool consent value should be rejected"
        assert "huggingface_consent" not in validated


class TestCloudEngineRefusesWithoutConsent:
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
        """transcribe() must raise ConsentRequiredError when consent hasn't been given."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine, ConsentRequiredError

        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=False,
        )
        audio = np.zeros(16000, dtype=np.float32)
        with pytest.raises(ConsentRequiredError):
            eng.transcribe(audio)

    def test_transcribe_does_not_raise_with_consent(self):
        """When consent is given, transcribe should not raise ConsentRequiredError."""
        import numpy as np
        from voice_typer.server.cloud_engines import CloudEngine

        eng = CloudEngine(
            provider="openai",
            api_key="sk-test-key",
            consent_given=True,
        )
        empty_audio = np.array([], dtype=np.float32)
        result = eng.transcribe(empty_audio)
        assert result == ""


class TestWhisperPreDownloadRespectsHuggingFaceConsent:
    """TranscriptionEngine._pre_download_model respects huggingface_consent."""

    def test_pre_download_returns_early_without_consent(self, monkeypatch, tmp_path):
        """When consent is False and model is not cached, _pre_download_model
        returns early without calling snapshot_download with local_files_only=False."""
        calls = []

        def fake_snapshot_download(**kwargs):
            calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            return "/fake/path"

        import sys

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        engine._pre_download_model("parakeet")
        assert calls == []

    def test_pre_download_skips_download_without_consent(self, monkeypatch):
        """When consent is False and model is not cached, _pre_download_model
        should not trigger a network download."""
        network_calls = []

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            network_calls.append(kwargs)
            return "/fake/path"

        import sys

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        engine._pre_download_model("small.en")
        assert network_calls == []


class TestAboutPageHasPrivacyDisclosure:
    """About page has expanded privacy disclosure.

    i18n-aware: checks that the feature exists either as hardcoded text
    or as a t() key with the English value in en.json.
    """

    def _read_en_json(self) -> str:
        en = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "i18n" / "translations" / "en.json"
        return en.read_text(encoding="utf-8")

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "About-privacy.test.tsx — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_about_page_has_privacy_section(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        en = self._read_en_json()
        assert "Audio processing" in src or "Audio processing" in en
        assert "Model weights" in src or "Model weights" in en
        assert "HuggingFace" in src or "HuggingFace" in en
        assert "Cloud ASR" in src or "Cloud ASR" in en
        assert "Voice biometrics" in src or "Voice biometrics" in en
        assert "BIPA" in src or "BIPA" in en

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_about_page_has_updates_section(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        en = self._read_en_json()
        assert "Check for Updates" in src or "Check for Updates" in en
        assert "LATEST_RELEASE_API" in src
        assert "api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest" in src

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_about_page_has_help_links(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert "README_URL" in src or "README.md" in src
        assert "CHANGELOG_URL" in src or "CHANGELOG.md" in src

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_about_page_has_feedback_links(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        en = self._read_en_json()
        assert "Report a Bug" in src or "Report an Issue" in src or "Report a Bug" in en or "Report an Issue" in en
        assert "github.com/AbdallahIsDev/voice-typer/issues" in src


class TestSettingsTroubleshootHasDiagnosticActions:
    """Settings Troubleshoot section has real diagnostic actions.

    i18n-aware: checks that the feature exists either as hardcoded text
    or as a t() key with the English value in en.json.
    """

    def _read_en_json(self) -> str:
        en = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "i18n" / "translations" / "en.json"
        return en.read_text(encoding="utf-8")

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_settings_has_diagnostics_button(self):
        settings = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings.read_text(encoding="utf-8")
        en = self._read_en_json()
        assert "Diagnostics" in src or "Diagnostics" in en
        assert "Help & FAQ" in src or "Help & FAQ" in en
        assert "Report a Bug" in src or "Report a Bug" in en
        assert "Open Log Folder" in src or "Open Log Folder" in en

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_settings_has_on_navigate_prop(self):
        settings = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "Settings.tsx"
        src = settings.read_text(encoding="utf-8")
        assert "onNavigate" in src


class TestAboutAndSettingsShowVoiceBiometricConsent:
    """About and Settings show voice biometric consent disclosure."""

    def _read_en_json(self) -> str:
        en = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "i18n" / "translations" / "en.json"
        return en.read_text(encoding="utf-8")

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_about_cites_gdpr_article_9(self):
        about = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "pages" / "About.tsx"
        src = about.read_text(encoding="utf-8")
        assert 't("about.voiceBiometricsDesc")' in src
        assert 't("about.voiceBiometricsTitle")' in src

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_settings_has_privacy_consent_section(self):
        settings = (
            REPO_ROOT
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "components"
            / "settings"
            / "PrivacySettingsSection.tsx"
        )
        src = settings.read_text(encoding="utf-8")
        assert 't("settings.privacy.privacyTitle")' in src
        assert 't("settings.privacy.privacyDescription")' in src

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_settings_has_voice_biometric_consent_toggle(self):
        settings = (
            REPO_ROOT
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "components"
            / "settings"
            / "PrivacySettingsSection.tsx"
        )
        src = settings.read_text(encoding="utf-8")
        assert "voice_biometric_consent" in src
        assert 't("settings.privacy.voiceBiometricProcessingInfo")' in src
        assert 't("settings.privacy.voiceBiometricLabel")' in src

    @pytest.mark.skip(
        reason=(
            "rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/"
            "PrivacySettings-consent.test.tsx — remove this Python test "
            "once the vitest is verified on CI"
        )
    )
    def test_settings_has_all_consent_toggles_consolidated(self):
        settings = (
            REPO_ROOT
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "components"
            / "settings"
            / "PrivacySettingsSection.tsx"
        )
        src = settings.read_text(encoding="utf-8")
        assert "huggingface_consent" in src
        assert "voice_biometric_consent" in src
        assert "cloud_openai_consent" in src
        assert "cloud_groq_consent" in src
        assert "cloud_deepgram_consent" in src
        assert "llm_polish_consent" in src


class TestVoiceTyperConfigTypeIncludesAllFields:
    """The VoiceTyperConfig TypeScript interface includes consent fields."""

    @staticmethod
    def _read(rel: str) -> str:
        renderer = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"
        return (renderer / rel).read_text(encoding="utf-8")

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_sound_feedback_enabled_in_type(self):
        config_ts = self._read("types/config.ts")
        assert "sound_feedback_enabled" in config_ts

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_huggingface_consent_in_type(self):
        config_ts = self._read("types/config.ts")
        assert "huggingface_consent" in config_ts

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_cloud_consent_fields_in_type(self):
        config_ts = self._read("types/config.ts")
        assert "cloud_openai_consent" in config_ts
        assert "cloud_groq_consent" in config_ts
        assert "cloud_deepgram_consent" in config_ts

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_voice_biometric_consent_in_type(self):
        config_ts = self._read("types/config.ts")
        assert "voice_biometric_consent" in config_ts

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_llm_polish_consent_in_type(self):
        config_ts = self._read("types/config.ts")
        assert "llm_polish_consent" in config_ts


class TestModelsPageExposesCloudConsentToggles:
    """Models.tsx exposes consent toggles for each cloud provider."""

    @staticmethod
    def _read(rel: str) -> str:
        renderer = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"
        return (renderer / rel).read_text(encoding="utf-8")

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_models_imports_switch(self):
        models = self._read("pages/Models.tsx")
        assert "import { Switch }" in models

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_models_has_set_cloud_consent_handler(self):
        models = self._read("pages/Models.tsx")
        assert "setCloudConsent" in models
        assert "cloud_openai_consent" in models
        assert "cloud_groq_consent" in models
        assert "cloud_deepgram_consent" in models

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_models_has_consent_key_helper(self):
        models = self._read("pages/Models.tsx")
        assert "consentKeyFor" in models

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_models_has_consent_disclosure_text(self):
        models = self._read("pages/Models.tsx")
        assert 't("models.cloud.consentTitle")' in models
        assert '"models.cloud.consentDescription"' in models

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_models_has_hugging_face_consent_banner(self):
        models = self._read("pages/Models.tsx")
        assert 't("models.hfConsent.title")' in models
        assert 't("models.hfConsent.grant")' in models
        assert "setHuggingFaceConsent" in models

    @pytest.mark.skip(
        reason=(
            "RW-1: rewritten as vitest in "
            "voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/"
            "consent-privacy-behavior.test.tsx"
        )
    )
    def test_models_consent_section_only_shown_when_key_present(self):
        models = self._read("pages/Models.tsx")
        assert "apiKeys[provider.key]" in models
        assert "consentKeyFor(provider.key)" in models


class TestEngineAcceptsConfigInRealConstructionPath:
    """TranscriptionEngine constructor accepts a config kwarg with consent flags."""

    def test_engine_accepts_config_kwarg(self, tmp_config_dir):
        from voice_typer.server.config import Config
        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)
        assert engine.config is cfg
        assert engine.config.huggingface_consent is True

    def test_engine_defaults_config_to_none(self, tmp_config_dir):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en")
        assert engine.config is None

    def test_pre_download_does_not_crash_without_config(self, tmp_path, monkeypatch):
        import sys

        fake_module = type(sys)("huggingface_hub")

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            raise AssertionError("snapshot_download called without consent")

        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en")
        progress_messages: list[str] = []
        engine._pre_download_model("small.en", progress_callback=progress_messages.append)
        assert any("consent" in m.lower() for m in progress_messages)

    def test_pre_download_downloads_when_consent_given(self, tmp_path, monkeypatch):
        import sys

        from voice_typer.server.config import Config

        download_calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            download_calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            return str(tmp_path / "fake_model")

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) >= 1

    def test_pre_download_refuses_download_without_consent(self, tmp_path, monkeypatch):
        import sys

        from voice_typer.server.config import Config

        download_calls: list[dict] = []

        def fake_snapshot_download(**kwargs):
            download_calls.append(kwargs)
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            raise AssertionError("snapshot_download called without consent")

        fake_module = type(sys)("huggingface_hub")
        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = False
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) == 0


class TestModelManagerWiresConfigIntoWhisper:
    """ModelManager._ensure_engine passes the live Config to TranscriptionEngine."""

    def test_ensure_engine_passes_config_to_whisper(self, tmp_config_dir, monkeypatch):
        from voice_typer.server.config import Config
        from voice_typer.server.model_manager import ModelManager
        from voice_typer.server.tray import AppState

        class FakeTray:
            state = AppState.IDLE

            def set_state(self, *args, **kwargs):
                pass

            def notify(self, *args, **kwargs):
                pass

        class FakeApp:
            def __init__(self):
                self.config = Config()
                self.config.huggingface_consent = True
                self.tray = FakeTray()
                self._ipc_server = None
                self.models = None
                self._cloud_engine = None
                self._llm_polisher = None
                self._template_manager = None

        app = FakeApp()
        app.models = ModelManager(app)
        registry = app.models._registry
        app.models._ensure_engine("whisper")
        engine = registry.get("whisper")
        assert engine is not None
        assert engine.config is app.config
