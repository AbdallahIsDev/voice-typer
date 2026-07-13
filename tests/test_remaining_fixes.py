"""Tests for remaining forensic review fixes (PERF-007, PERF-009, PERF-015).

These tests verify the newly added warm-up inference, batch API,
and LRU model eviction features.
"""

from unittest.mock import MagicMock, patch

import numpy as np


class TestWarmUpInference:
    """PERF-007: Verify warm-up inference after model load."""

    def test_warm_up_method_exists(self):
        """TranscriptionEngine should have a _warm_up_model method."""
        from voice_typer.server.transcription import TranscriptionEngine
        assert hasattr(TranscriptionEngine, '_warm_up_model')

    def test_warm_up_no_model(self):
        """Warm-up should be a no-op when no model is loaded."""
        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._model = None
        engine._device = "cuda"
        # Should not raise
        engine._warm_up_model()

    def test_warm_up_cpu_model(self):
        """Warm-up should be skipped for CPU models."""
        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._model = MagicMock()
        engine._device = "cpu"
        # Should not call transcribe
        engine._warm_up_model()
        engine._model.transcribe.assert_not_called()

    def test_warm_up_cuda_model(self):
        """Warm-up should call transcribe for CUDA models."""
        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._model = MagicMock()
        engine._device = "cuda"
        engine.language = "en"
        mock_segments = iter([MagicMock(text="")])
        engine._model.transcribe.return_value = (mock_segments, MagicMock())
        engine._warm_up_model()
        engine._model.transcribe.assert_called_once()

    def test_warm_up_failure_non_critical(self):
        """Warm-up failure should not propagate exceptions."""
        from voice_typer.server.transcription import TranscriptionEngine
        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._model = MagicMock()
        engine._device = "cuda"
        engine.language = "en"
        engine._model.transcribe.side_effect = RuntimeError("GPU error")
        # Should not raise
        engine._warm_up_model()


class TestBatchTranscription:
    """PERF-009: Verify batch transcription API for QwenEngine."""

    def test_batch_method_exists(self):
        """QwenEngine should have a transcribe_batch method."""
        from voice_typer.server.qwen_engine import QwenEngine
        assert hasattr(QwenEngine, 'transcribe_batch')

    def test_batch_empty_input(self):
        """Batch transcription with empty input returns empty list."""
        from voice_typer.server.qwen_engine import QwenEngine
        engine = QwenEngine.__new__(QwenEngine)
        result = engine.transcribe_batch([])
        assert result == []

    def test_batch_calls_transcribe(self):
        """Batch transcription should call transcribe for each chunk."""
        from voice_typer.server.qwen_engine import QwenEngine
        engine = QwenEngine.__new__(QwenEngine)
        engine._lock = MagicMock()
        engine._inference_event = MagicMock()
        engine._model = MagicMock()
        engine.language = "en"

        # Mock the transcribe method
        with patch.object(engine, 'transcribe', return_value="hello") as mock_t:
            chunks = [np.zeros(100, dtype=np.float32), np.zeros(100, dtype=np.float32)]
            results = engine.transcribe_batch(chunks)
            assert len(results) == 2
            assert mock_t.call_count == 2


class TestLRUModelEviction:
    """PERF-015: Verify LRU model eviction in ModelManager."""

    def test_evict_method_exists(self):
        """ModelManager should have _evict_lru_model and touch_model methods."""
        from voice_typer.server.model_manager import ModelManager
        assert hasattr(ModelManager, '_evict_lru_model')
        assert hasattr(ModelManager, 'touch_model')

    def test_no_eviction_below_limit(self):
        """Eviction should not happen when models <= _MAX_LOADED_MODELS."""
        from voice_typer.server.model_manager import ModelManager
        mm = ModelManager.__new__(ModelManager)
        mm._model_access_times = {"whisper": 1.0, "qwen": 2.0}
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)
        mm._MAX_LOADED_MODELS = 2
        mm._registry = MagicMock()
        # Should not try to unload anything
        mm._evict_lru_model()
        mm._registry.get.assert_not_called()

    def test_eviction_unloads_oldest(self):
        """Eviction should unload the least recently used model."""
        import time

        from voice_typer.server.model_manager import ModelManager
        mm = ModelManager.__new__(ModelManager)
        now = time.monotonic()
        mm._model_access_times = {
            "whisper": now - 100,  # oldest
            "qwen": now - 10,
            "parakeet": now,
        }
        mm._MAX_LOADED_MODELS = 2
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mm._registry = MagicMock()
        mm._registry.get.return_value = mock_engine

        mm._evict_lru_model()
        # Should have called unload on the oldest (whisper)
        mock_engine.unload.assert_called_once()
        # Should have removed the oldest from access times
        assert "whisper" not in mm._model_access_times

    def test_touch_updates_timestamp(self):
        """touch_model should update the access timestamp."""
        import time

        from voice_typer.server.model_manager import ModelManager
        mm = ModelManager.__new__(ModelManager)
        mm._model_access_times = {}
        mm._model_lru_lock = MagicMock()
        mm._model_lru_lock.__enter__ = MagicMock(return_value=None)
        mm._model_lru_lock.__exit__ = MagicMock(return_value=False)

        before = time.monotonic()
        mm.touch_model("whisper")
        after = time.monotonic()

        assert "whisper" in mm._model_access_times
        assert before <= mm._model_access_times["whisper"] <= after


class TestDocsADirectory:
    """DOC-007: Verify docs/adr directory exists with template and first ADR."""

    def test_adr_directory_exists(self):
        """docs/adr/ directory should exist."""
        from pathlib import Path
        adr_dir = Path(__file__).resolve().parent.parent / "docs" / "adr"
        assert adr_dir.exists(), "docs/adr/ directory should exist"

    def test_template_exists(self):
        """docs/adr/0000-template.md should exist."""
        from pathlib import Path
        template = Path(__file__).resolve().parent.parent / "docs" / "adr" / "0000-template.md"
        assert template.exists(), "ADR template should exist"

    def test_first_adr_exists(self):
        """docs/adr/0001-record-architecture-decisions.md should exist."""
        from pathlib import Path
        adr = Path(__file__).resolve().parent.parent / "docs" / "adr" / "0001-record-architecture-decisions.md"
        assert adr.exists(), "First ADR should exist"


class TestAPIDocs:
    """DOC-008: Verify public API documentation exists."""

    def test_api_docs_exist(self):
        """docs/API.md should exist."""
        from pathlib import Path
        api_doc = Path(__file__).resolve().parent.parent / "docs" / "API.md"
        assert api_doc.exists(), "API documentation should exist"

    def test_api_docs_mention_key_classes(self):
        """API docs should document VoiceTyperApp, Recorder, Config."""
        from pathlib import Path
        api_doc = Path(__file__).resolve().parent.parent / "docs" / "API.md"
        content = api_doc.read_text()
        for keyword in ["VoiceTyperApp", "Recorder", "Config", "ClipboardManager", "IPC"]:
            assert keyword in content, f"API docs should mention {keyword}"


class TestConsolidatedDiagnostics:
    """CQ-016: Verify consolidated diagnostics script."""

    def test_diagnostics_script_exists(self):
        """scripts/diagnostics.py should exist."""
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent / "scripts" / "diagnostics.py"
        assert script.exists(), "Consolidated diagnostics script should exist"

    def test_diagnostics_has_subcommands(self):
        """Diagnostics script should have f2, cublas, runtime, test-runner subcommands."""
        from pathlib import Path
        script = Path(__file__).resolve().parent.parent / "scripts" / "diagnostics.py"
        content = script.read_text()
        for cmd in ["f2", "cublas", "runtime", "test-runner"]:
            assert cmd in content, f"Diagnostics script should have '{cmd}' subcommand"


class TestPlatformUtils:
    """CQ-029: Verify centralized platform utilities."""

    def test_platform_utils_module_exists(self):
        """voice_typer.server.platform_utils should exist."""
        from voice_typer.server import platform_utils
        assert hasattr(platform_utils, 'is_windows')
        assert hasattr(platform_utils, 'is_macos')
        assert hasattr(platform_utils, 'is_linux')

    def test_platform_utils_returns_bool(self):
        """Platform utility functions should return booleans."""
        from voice_typer.server.platform_utils import is_linux, is_macos, is_windows
        assert isinstance(is_windows(), bool)
        assert isinstance(is_macos(), bool)
        assert isinstance(is_linux(), bool)
