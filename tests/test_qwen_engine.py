"""Tests for the optional Qwen3-ASR backend."""

import os
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def real_faster_whisper(monkeypatch):
    """Restore the REAL ``faster_whisper`` package for the mel path.

    The session-scoped ``mock_heavy_imports`` fixture stubs
    ``faster_whisper`` as a non-package MagicMock, which breaks the lazy
    ``from faster_whisper.feature_extractor import FeatureExtractor`` in
    ``qwen_onnx_model._log_mel_spectrogram``. Loads the real package
    fresh and swaps it in for the test's duration (monkeypatch restores
    the mock afterwards). Mirrors the autouse fixture in
    ``tests/test_qwen_onnx_model.py``.
    """
    import importlib
    import sys

    mocked = sys.modules.get("faster_whisper")
    if mocked is not None and not hasattr(mocked, "__path__"):
        for name in [n for n in list(sys.modules) if n == "faster_whisper" or n.startswith("faster_whisper.")]:
            monkeypatch.delitem(sys.modules, name, raising=False)
        real = importlib.import_module("faster_whisper")
        monkeypatch.setitem(sys.modules, "faster_whisper", real)


class TestQwenEngineUnit:
    """Unit tests for QwenEngine — all model calls are mocked."""

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.server.qwen_engine import QwenEngine

        return QwenEngine(model_path=model_path, **kwargs)

    def test_init_defaults(self):
        engine = self._make_engine()
        assert engine.model_path == "/fake/qwen/model"
        assert engine.device == "cuda"
        assert engine.language == "en"
        assert engine.is_loaded is False

    def test_transcribe_raises_when_not_loaded(self):
        engine = self._make_engine()
        with pytest.raises(RuntimeError, match="not loaded"):
            engine.transcribe(np.zeros(16000, dtype=np.float32))

    def test_transcribe_empty_audio_returns_empty(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        result = engine.transcribe(np.array([], dtype=np.float32))
        assert result == ""

    def test_load_success_onnx_dir(self, tmp_path):
        """A valid ONNX-export directory loads the ONNX backend."""
        from tests.test_qwen_onnx_model import make_onnx_dir, patch_ort, patch_tokenizer, scripted_sessions

        model_dir = make_onnx_dir(tmp_path, hidden=4, vocab=64)
        engine = self._make_engine(model_path=str(model_dir))
        sessions = scripted_sessions(hidden=4, vocab=64)
        with patch_ort(sessions), patch_tokenizer():
            assert engine.load() is True

        assert engine.is_loaded is True
        assert engine.device == "cpu"  # ONNX path is CPU-pinned
        assert engine.device_info == "qwen/cpu"
        assert engine._onnx_model is not None

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert isinstance(result, str)

    def test_load_failure_non_onnx_dir_returns_false(self, tmp_path):
        """A torch/safetensors (non-ONNX) directory is refused — the
        torch engine was removed; load() returns False with a migration
        error instead of crashing."""
        model_dir = tmp_path / "torch_qwen"
        model_dir.mkdir()
        (model_dir / "config.json").write_text('{"arch": "qwen3"}')
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)

        engine = self._make_engine(model_path=str(model_dir))
        assert engine.load() is False
        assert engine.is_loaded is False

    def test_load_failure_missing_dir_returns_false(self):
        engine = self._make_engine()
        result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_failure_incomplete_onnx_dir_raises(self, tmp_path):
        """An ONNX-layout dir that fails mid-load raises RuntimeError
        (fail-closed — no silent fallback)."""
        from tests.test_qwen_onnx_model import make_onnx_dir, patch_ort, patch_tokenizer, scripted_sessions

        model_dir = make_onnx_dir(tmp_path, hidden=4, vocab=64)
        (model_dir / "decoder_step.onnx").unlink()
        engine = self._make_engine(model_path=str(model_dir))
        sessions = scripted_sessions(hidden=4, vocab=64)
        with patch_ort(sessions), patch_tokenizer(), pytest.raises(RuntimeError):
            engine.load()
        assert engine.is_loaded is False

    def test_unload_clears_model(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        assert engine.is_loaded is True

        engine.unload()
        # pyrefly: ignore [unnecessary-comparison]
        assert engine.is_loaded is False

    def test_transcribe_strips_whitespace(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "  hello world  "
        engine._model.transcribe.return_value = [mock_transcription]

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "hello world"

    def test_transcribe_empty_result(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        engine._model.transcribe.return_value = []

        audio = np.ones(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == ""

    # ── abort contract (EO-6: AsrBackend Protocol parity) ──────────

    def test_request_abort_sets_abort_event(self):
        """request_abort() must set the abort token; clear_abort() must
        reset it before a fresh cycle (mirrors ParakeetEngine)."""
        engine = self._make_engine()
        assert engine._abort_event.is_set() is False
        engine.request_abort()
        assert engine._abort_event.is_set() is True
        engine.clear_abort()
        assert engine._abort_event.is_set() is False

    def test_chunk_loop_breaks_early_on_abort(self):
        """When abort is requested mid-chunked-transcription, the
        sequential chunk loop must stop after the current chunk instead
        of decoding all remaining chunks."""
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "chunk text"
        engine._model.transcribe.return_value = [mock_transcription]

        # Force the chunked path: audio longer than _QWEN_CHUNK_SECONDS.
        from voice_typer.server.qwen_engine import _QWEN_CHUNK_SECONDS

        audio = np.ones(16000 * (_QWEN_CHUNK_SECONDS + 10), dtype=np.float32)
        # Request abort BEFORE transcribing: the first chunk iteration
        # sees the event and breaks immediately.
        engine.request_abort()
        result = engine.transcribe(audio)
        # Loop breaks before any chunk is transcribed → empty result
        # (no model call, no hallucination filter).
        assert result == ""
        engine._model.transcribe.assert_not_called()


class TestQwenConfigKeys:
    """Verify new config keys exist and have correct defaults."""

    def test_asr_backend_default_is_whisper(self):
        from voice_typer.server.config import Config

        c = Config()
        assert c.asr_backend == "whisper"

    def test_qwen_model_path_default_is_none(self):
        from voice_typer.server.config import Config

        c = Config()
        assert c.qwen_model_path is None

    def test_asr_backend_persists(self, tmp_config_dir):
        from voice_typer.server.config import Config

        model_dir = tmp_config_dir / "qwen_model"
        model_dir.mkdir()
        c = Config(asr_backend="qwen", qwen_model_path=str(model_dir))
        c.save()
        loaded = Config.load()
        assert loaded.asr_backend == "qwen"
        assert loaded.qwen_model_path == str(model_dir)


class TestQwenBackendSelection:
    """Verify the app uses the correct backend based on config."""

    def test_qwen_backend_requires_model_path(self):
        from voice_typer.server.config import Config

        c = Config(asr_backend="qwen", qwen_model_path=None)
        # Without a model path, Qwen can't load
        assert c.qwen_model_path is None


@pytest.mark.skipif(
    not os.environ.get("VOICE_TYPER_TEST_QWEN"),
    reason="Qwen integration tests require VOICE_TYPER_TEST_QWEN=1 and real model weights",
)
class TestQwenIntegration:
    """Integration tests — only run when user has downloaded Qwen weights."""

    def test_real_qwen_transcribe(self):
        """Test with real Qwen model. Requires VOICE_TYPER_TEST_QWEN=1."""
        from voice_typer.server.qwen_engine import QwenEngine

        model_path = os.environ.get("VOICE_TYPER_QWEN_PATH", "Qwen3-ASR-1.7B")
        engine = QwenEngine(model_path=model_path)
        engine.load()
        assert engine.is_loaded
        # Generate 1s of silence
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert isinstance(result, str)


class TestWhisperSkipWhenQwenActive:
    """P1 fix: Skip Whisper load when Qwen is active, with lazy Whisper fallback."""

    def _make_app_with_qwen(self, monkeypatch, tmp_config_dir, qwen_loaded=True):
        """Create a VoiceTyperApp with Qwen backend configured."""
        import sys
        from unittest.mock import MagicMock

        # Mock heavy imports BEFORE importing the app module
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = []
        monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
        mock_whisper = MagicMock()
        monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)
        monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
        mock_pynput = MagicMock()
        mock_pynput_kb = MagicMock()
        monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_pynput_kb)
        mock_pystray = MagicMock()
        monkeypatch.setitem(sys.modules, "pystray", mock_pystray)
        mock_pil = MagicMock()
        monkeypatch.setitem(sys.modules, "PIL", mock_pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
        monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
        monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])
        monkeypatch.setattr("atexit.register", lambda *a, **kw: None)
        from voice_typer.server.hotkeys import PynputHotkey

        # ``create_hotkey_backend`` was moved from ``app.py`` to
        # ``hotkeys/factory.py`` and is now imported directly by
        # ``hotkey_dispatcher.py``. Patching ``app.create_hotkey_backend``
        # silently no-ops (the attribute no longer exists on ``app``);
        # patch the dispatcher's imported reference instead.
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda hotkey_str, role=None: PynputHotkey(hotkey_str),
        )

        # Write a config with Qwen backend
        import json

        config_file = tmp_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "asr_backend": "qwen",
                    "qwen_model_path": str(tmp_config_dir / "qwen_model"),
                    # dictation now requires explicit voice-biometric
                    # consent — without this the recorder refuses to start.
                    "voice_biometric_consent": True,
                }
            )
        )

        # Create the Qwen model dir so path validation passes
        (tmp_config_dir / "qwen_model").mkdir(exist_ok=True)

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        # Mock the Qwen engine
        app.models._qwen_engine = MagicMock()
        app.models._qwen_engine.is_loaded = qwen_loaded
        app.models._qwen_engine.load = MagicMock()

        # Never duck the developer's REAL system volume (the factory
        # is also global-mocked in ``mock_heavy_imports_session``;
        # belt-and-braces here so dictation tests never touch pycaw).
        app._volume_ducker = MagicMock()
        app._volume_ducker.initialize.return_value = False

        return app

    def test_startup_skips_whisper_when_qwen_active(self, monkeypatch, tmp_config_dir):
        """When Qwen backend is active and loaded, Whisper should NOT be loaded during startup."""
        app = self._make_app_with_qwen(monkeypatch, tmp_config_dir, qwen_loaded=True)
        # Phase 1: the app-level test-seam delegates have been removed;
        # patch the controllers / module-level functions directly.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        app.hotkeys.register = MagicMock()
        app.recorder.warm_up_resampler = MagicMock()

        # Track if Whisper load was attempted
        whisper_load_called = []
        # Phase 2: ``app._try_load_model`` delegate removed; patch
        # the ModelManager method directly.
        original_try_load = app.models.try_load

        def track_try_load(*args, **kwargs):
            whisper_load_called.append(True)
            # Simulate successful load so it doesn't loop
            # pyrefly: ignore [read-only]
            app.models.transcriber.is_loaded = True
            # pyrefly: ignore [read-only]
            app.models.transcriber.device_info = "cpu (int8)"
            # pyrefly: ignore [read-only]
            app.models.transcriber.loaded_via = "cpu/int8/small.en"
            original_try_load(*args, **kwargs)

        app.models.try_load = track_try_load

        app._do_startup()

        # Wait for the background model-load thread to complete
        if app.models._model_load_thread is not None:
            app.models._model_load_thread.join(timeout=5)

        # Qwen engine should have been loaded (via registry)
        # pyrefly: ignore [missing-attribute]
        app.models._qwen_engine.load.assert_called_once()
        # Whisper should NOT have been loaded since Qwen succeeded
        assert len(whisper_load_called) == 0, "Whisper should not be loaded when Qwen is active and loaded"

    def test_startup_falls_back_to_whisper_when_qwen_fails(self, monkeypatch, tmp_config_dir):
        """When Qwen backend fails to load, Whisper should be loaded as fallback."""
        app = self._make_app_with_qwen(monkeypatch, tmp_config_dir, qwen_loaded=False)
        # Phase 1: the app-level test-seam delegates have been removed;
        # patch the controllers / module-level functions directly.
        monkeypatch.setattr("voice_typer.server.startup_tasks.sync_autostart", MagicMock())
        monkeypatch.setattr("voice_typer.server.startup_tasks.load_microphones", MagicMock())
        app.hotkeys.register = MagicMock()
        app.recorder.warm_up_resampler = MagicMock()

        # Mock TranscriptionEngine so _do_startup creates a mock
        mock_transcriber = MagicMock()
        mock_transcriber.is_loaded = False
        mock_transcriber.load = MagicMock()
        mock_transcriber.device_info = "cpu (int8)"
        mock_transcriber.loaded_via = "cpu/int8/small.en"
        monkeypatch.setattr(
            "voice_typer.server.transcription.TranscriptionEngine",
            MagicMock(return_value=mock_transcriber),
        )
        app.models.transcriber = mock_transcriber

        # Make Qwen load() raise so the registry falls back to Whisper
        # pyrefly: ignore [missing-attribute]
        app.models._qwen_engine.load = MagicMock(side_effect=RuntimeError("Qwen unavailable"))

        app._do_startup()

        # Wait for the background model-load thread to complete
        if app.models._model_load_thread is not None:
            app.models._model_load_thread.join(timeout=5)

        # Qwen engine load should have been attempted (via registry)
        # pyrefly: ignore [missing-attribute]
        app.models._qwen_engine.load.assert_called_once()
        # Whisper should have been loaded as fallback (via registry)
        app.models.transcriber.load.assert_called_once()

    def test_start_dictation_lazy_loads_whisper_when_qwen_unavailable(self, monkeypatch, tmp_config_dir):
        """When Qwen is configured but not loaded, _start_dictation should lazy-load Whisper."""
        app = self._make_app_with_qwen(monkeypatch, tmp_config_dir, qwen_loaded=False)
        app.recorder = MagicMock()
        app.recorder.recording = False
        app.tray = MagicMock()
        app.models.transcriber = MagicMock()
        app.models.transcriber.is_loaded = False
        app.models.transcriber.load = MagicMock()
        app.models.transcriber.device_info = "cpu (int8)"
        app.models.transcriber.loaded_via = "cpu/int8/small.en"

        def mock_load(**kwargs):
            # pyrefly: ignore [read-only]
            app.models.transcriber.is_loaded = True

        app.models.transcriber.load = mock_load

        # The source's lazy-load path goes through
        # ``app.models.fallback_to_whisper`` →
        # ``AsrBackendRegistry.load_with_fallback`` →
        # ``transcription._pre_download_model``, which raises
        # ``ConsentRequiredError`` because ``huggingface_consent`` is
        # not set in this test's config (the config only sets
        # ``voice_biometric_consent``). Without HF consent, the
        # fallback fails and ``recorder.start`` is never called.
        #
        # The test's intent is to verify the *recording start* path
        # after a successful lazy-load, NOT to verify the HF consent
        # gate. Mock ``fallback_to_whisper`` to set ``is_loaded=True``
        # on the mock transcriber, AND mock ``active_transcriber`` so
        # the post-fallback read returns the now-loaded mock
        # (``active_transcriber`` normally delegates to the registry,
        # which the local ``transcriber`` mock would also be kept in
        # sync with via the @property setter on
        # ``app.models.transcriber``). Bypassing the registry here
        # keeps the test focused on the start path.
        def fake_fallback(notify_on_failure=False):
            app.models.transcriber.is_loaded = True

        app.models.fallback_to_whisper = fake_fallback
        app.models.active_transcriber = lambda: app.models.transcriber

        app._start_dictation()

        # Should have attempted to start recording (Whisper was lazy-loaded)
        app.recorder.start.assert_called_once()


class TestLoadReturnValues:
    """M23: QwenEngine load() silently eats errors."""

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.server.qwen_engine import QwenEngine

        return QwenEngine(model_path=model_path, **kwargs)

    def test_load_returns_true_on_success(self, tmp_path):
        """A valid ONNX-export directory → load() returns True."""
        from tests.test_qwen_onnx_model import make_onnx_dir, patch_ort, patch_tokenizer, scripted_sessions

        model_dir = make_onnx_dir(tmp_path, hidden=4, vocab=64)
        engine = self._make_engine(model_path=str(model_dir))
        sessions = scripted_sessions(hidden=4, vocab=64)
        with patch_ort(sessions), patch_tokenizer():
            result = engine.load()
        assert result is True
        assert engine.is_loaded is True

    def test_load_returns_false_on_non_onnx_dir(self, tmp_path):
        """A non-ONNX (torch/safetensors) dir → load() returns False."""
        model_dir = tmp_path / "qwen_model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text('{"arch": "qwen3"}')
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)
        engine = self._make_engine(model_path=str(model_dir))
        result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_returns_false_on_missing_dir(self):
        engine = self._make_engine()
        result = engine.load()
        assert result is False
        assert engine.is_loaded is False

    def test_load_returns_true_on_already_loaded(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        result = engine.load()
        assert result is True


class TestHallucinationDetection:
    """M13: QwenEngine no hallucination detection."""

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.server.qwen_engine import QwenEngine

        return QwenEngine(model_path=model_path, **kwargs)

    def test_rejects_hallucination_on_silence(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Thanks for watching"
        engine._model.transcribe.return_value = [mock_transcription]

        # Near-silence audio (RMS < 0.001)
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == ""

    def test_keeps_hallucination_phrase_with_audio(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Thanks for watching"
        engine._model.transcribe.return_value = [mock_transcription]

        # Audio with real speech (RMS > 0.001)
        audio = np.full(16000, 0.05, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "Thanks for watching"

    def test_keeps_non_hallucination_on_silence(self):
        engine = self._make_engine()
        engine._model = MagicMock()
        mock_transcription = MagicMock()
        mock_transcription.text = "Hello world"
        engine._model.transcribe.return_value = [mock_transcription]

        # Near-silence audio but text is not a known hallucination
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)
        assert result == "Hello world"


# ONNX fail-closed regression tests ──────────────────────────


class TestQwenOnnxFailClosed:
    """The ONNX-only engine must be fail-closed: a non-ONNX directory
    (the old torch/safetensors layout) is refused with False, and an
    ONNX-layout directory that fails mid-load raises RuntimeError. The
    old ``_verify_qwen_model_hashes`` helper + the torch manifest gate
    were removed with the torch engine (2026-08-15).
    """

    def _make_engine(self, model_path="/fake/qwen/model", **kwargs):
        from voice_typer.server.qwen_engine import QwenEngine

        return QwenEngine(model_path=model_path, **kwargs)

    def test_torch_layout_dir_returns_false(self, tmp_path):
        """A torch/safetensors Qwen dir (the ONLY layout before
        2026-08-14) is refused with a migration error, not loaded."""
        model_dir = tmp_path / "torch_qwen"
        model_dir.mkdir()
        (model_dir / "config.json").write_text('{"arch": "qwen3"}')
        (model_dir / "model.safetensors").write_bytes(b"\x00" * 100)

        engine = self._make_engine(model_path=str(model_dir))
        result = engine.load()
        assert result is False, "a torch/safetensors Qwen dir must be refused (torch engine removed)"
        assert engine.is_loaded is False

    def test_missing_required_onnx_file_raises(self, tmp_path):
        """An ONNX-layout dir missing a decoder session (a file
        ``is_onnx_model_dir`` does NOT check — it only requires encoder
        + embed_tokens.bin + tokenizer.json) raises RuntimeError
        (fail-closed, no silent fallback)."""
        from tests.test_qwen_onnx_model import make_onnx_dir, patch_ort, patch_tokenizer, scripted_sessions

        model_dir = make_onnx_dir(tmp_path, hidden=4, vocab=64)
        (model_dir / "decoder_step.onnx").unlink()
        engine = self._make_engine(model_path=str(model_dir))
        sessions = scripted_sessions(hidden=4, vocab=64)
        with patch_ort(sessions), patch_tokenizer(), pytest.raises(RuntimeError):
            engine.load()
        assert engine.is_loaded is False
        assert engine._onnx_model is None

    def test_load_succeeds_with_complete_onnx_dir(self, tmp_path):
        """A complete ONNX dir loads and transcribes (scripted sessions)."""
        from tests.test_qwen_onnx_model import make_onnx_dir, patch_ort, patch_tokenizer, scripted_sessions

        model_dir = make_onnx_dir(tmp_path, hidden=4, vocab=64)
        engine = self._make_engine(model_path=str(model_dir))
        sessions = scripted_sessions(hidden=4, vocab=64)
        with patch_ort(sessions), patch_tokenizer():
            assert engine.load() is True
        assert engine.is_loaded is True
        assert engine.device == "cpu"
