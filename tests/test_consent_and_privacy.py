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
        raises ``ConsentRequiredError`` (EC-FIX-8) and never makes a network
        download call.

        Pre-EC-FIX-8 the SUT silently returned; the test asserted
        ``network_calls == []`` with no exception. EC-FIX-8 changed the
        SUT to raise ``ConsentRequiredError`` so the IPC layer can
        ``isinstance``-check and surface a consent dialog (mirroring
        ``parakeet_engine.load`` and ``cloud_engines.CloudEngine.transcribe``).
        The test now expects the typed exception AND still verifies no
        network call was attempted.
        """
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

        from voice_typer.server.asr_errors import ConsentRequiredError
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.model_size = "small.en"
        engine.config = type("FakeConfig", (), {"huggingface_consent": False})()

        with pytest.raises(ConsentRequiredError):
            engine._pre_download_model("small.en")
        assert network_calls == []


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
        """When ``config`` is None the SUT treats consent as not-given
        (GDPR-safe default) and raises ``ConsentRequiredError`` (EC-FIX-8)
        — it does NOT crash with ``AttributeError`` (the pre-fix bug where
        ``getattr(self.config, ...)`` was called on ``None``).

        The progress callback still receives a "consent required" message
        so the renderer can surface a consent dialog.
        """
        import sys

        fake_module = type(sys)("huggingface_hub")

        def fake_snapshot_download(**kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("not in cache")
            raise AssertionError("snapshot_download called without consent")

        fake_module.snapshot_download = fake_snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)

        from voice_typer.server.asr_errors import ConsentRequiredError
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine(model_size="small.en")
        progress_messages: list[str] = []
        with pytest.raises(ConsentRequiredError):
            engine._pre_download_model(
                "small.en", progress_callback=progress_messages.append
            )
        assert any("consent" in m.lower() for m in progress_messages)

    def test_pre_download_downloads_when_consent_given(self, tmp_path, monkeypatch):
        """When consent is True and the model is not cached, ``_pre_download_model``
        performs the network download (``local_files_only=False``) at least once.

        The post-download ``verify_model_integrity`` check and the
        pre-download ``_check_disk_space_for_download`` guard are both
        mocked — their real behaviour (SHA-256 hash comparison against
        the ``MODEL_HASHES`` manifest; ``shutil.disk_usage`` check
        against the model's estimated size) is exercised by the
        ``test_model_integrity*`` and ``test_disk_space*`` suites, not
        here. The point of this test is the consent gate: when consent
        is True, the download is allowed to proceed.
        """
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

        # Mock the integrity check so the download path completes without
        # raising RuntimeError (the test is about the download happening,
        # not about the integrity verification).
        monkeypatch.setattr(
            "voice_typer.server.security.verify_model_integrity",
            lambda local_dir, repo_id: True,
        )
        # Mock the disk-space guard so the download path is not blocked by
        # the sandbox's limited free space (the test is about consent, not
        # disk capacity — see ``test_disk_space*`` for the real check).
        monkeypatch.setattr(
            "voice_typer.server.asr_utils._check_disk_space_for_download",
            lambda repo_id, model_size: None,
        )
        # ``_check_disk_space_for_download`` is imported into
        # ``transcription.py`` at module load time, so patching the
        # ``asr_utils`` attribute alone is not enough — patch the
        # re-exported name in ``transcription`` too.
        monkeypatch.setattr(
            "voice_typer.server.transcription._check_disk_space_for_download",
            lambda repo_id, model_size: None,
        )

        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = True
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        engine._pre_download_model("small.en")
        non_local_calls = [c for c in download_calls if not c.get("local_files_only")]
        assert len(non_local_calls) >= 1

    def test_pre_download_refuses_download_without_consent(self, tmp_path, monkeypatch):
        """When consent is False and the model is not cached,
        ``_pre_download_model`` raises ``ConsentRequiredError`` (EC-FIX-8)
        and never makes a network download call.

        Pre-EC-FIX-8 the SUT silently returned; the test asserted
        ``non_local_calls == 0`` with no exception. EC-FIX-8 changed the
        SUT to raise ``ConsentRequiredError`` so the IPC layer can
        ``isinstance``-check and surface a consent dialog (mirroring
        ``parakeet_engine.load`` and ``cloud_engines.CloudEngine.transcribe``).
        The test now expects the typed exception AND still verifies no
        network call was attempted.
        """
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

        from voice_typer.server.asr_errors import ConsentRequiredError
        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.huggingface_consent = False
        engine = TranscriptionEngine(model_size="small.en", config=cfg)

        with pytest.raises(ConsentRequiredError):
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
