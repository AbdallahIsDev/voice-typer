"""Focused tests for the transcription.py refactors."""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_faster_whisper(monkeypatch):
    """Mock faster_whisper + ctranslate2 so no real model is loaded."""
    mock_fw = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", mock_fw)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    mock_ct2 = MagicMock()
    mock_ct2.get_cuda_device_count.return_value = 0
    monkeypatch.setitem(sys.modules, "ctranslate2", mock_ct2)


class TestNvidiaDllPathManagerIsolation:
    """The manager class lets tests construct a fresh instance with its"""

    def test_fresh_instance_has_empty_state(self):
        from voice_typer.server.transcription import _NvidiaDllPathManager

        mgr = _NvidiaDllPathManager(
            state_dict={
                "_nvidia_dll_path_handles": [],
                "_nvidia_dll_paths_configured": False,
                "_nvidia_config_lock": threading.Lock(),
            }
        )
        assert mgr.handles == []
        assert mgr.configured is False
        assert isinstance(mgr.lock, type(threading.Lock()))

    def test_free_handles_clears_list_and_calls_close(self):
        from voice_typer.server.transcription import _NvidiaDllPathManager

        mgr = _NvidiaDllPathManager(
            state_dict={
                "_nvidia_dll_path_handles": [],
                "_nvidia_dll_paths_configured": False,
                "_nvidia_config_lock": threading.Lock(),
            }
        )
        fake_handle = MagicMock()
        mgr.handles.append(fake_handle)
        mgr.free_handles()
        assert mgr.handles == []
        fake_handle.close.assert_called_once()

    def test_module_singleton_reflects_module_global_writes(self):
        """Production singleton reads/writes through ``globals()`` so"""
        import voice_typer.server.transcription as mod

        # Save and restore original state.
        orig_handles = mod._nvidia_dll_path_handles
        orig_configured = mod._nvidia_dll_paths_configured
        try:
            mod._nvidia_dll_path_handles = [MagicMock()]
            mod._nvidia_dll_paths_configured = True
            assert mod._nvidia_dll_paths.handles == mod._nvidia_dll_path_handles
            assert mod._nvidia_dll_paths.configured is True
            mod._nvidia_dll_paths.free_handles()
            assert mod._nvidia_dll_path_handles == []
        finally:
            mod._nvidia_dll_path_handles = orig_handles
            mod._nvidia_dll_paths_configured = orig_configured

    def test_module_level_functions_delegate_to_singleton(self):
        """singleton."""
        import voice_typer.server.transcription as mod

        orig_handles = mod._nvidia_dll_path_handles
        try:
            mod._nvidia_dll_path_handles = [MagicMock()]
            mod._free_nvidia_dll_path_handles()
            assert mod._nvidia_dll_path_handles == []
        finally:
            mod._nvidia_dll_path_handles = orig_handles


class TestWithLockAndDeferredGc:
    """lock is released (RACE-023)."""

    def _make_engine(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._lock = threading.Lock()
        engine._pending_gc_collect = False
        return engine

    def test_deferred_gc_runs_after_lock_release(self):
        import gc as real_gc

        engine = self._make_engine()
        engine._pending_gc_collect = True  # simulate fallback set the flag

        gc_calls_before = 0
        with patch.object(real_gc, "collect") as mock_gc:
            with engine._with_lock_and_deferred_gc():
                # Inside the body, the flag is still set and gc has NOT
                assert engine._pending_gc_collect is True
                gc_calls_before = mock_gc.call_count
            # After the with-block exits, the deferred gc should fire.
            assert mock_gc.call_count > gc_calls_before
        # Flag was cleared by the deferred cleanup.
        assert engine._pending_gc_collect is False

    def test_no_gc_when_flag_not_set(self):
        import gc as real_gc

        engine = self._make_engine()
        engine._pending_gc_collect = False  # no fallback, no gc needed

        with patch.object(real_gc, "collect") as mock_gc, engine._with_lock_and_deferred_gc():
            pass
        # Flag was False, so gc.collect should NOT have been called.
        assert mock_gc.call_count == 0

    def test_lock_is_acquired_during_body(self):
        engine = self._make_engine()
        # If the lock is held during the body, a second acquire attempt
        with engine._with_lock_and_deferred_gc():
            assert not engine._lock.acquire(blocking=False)
        # After the body, the lock is released.
        assert engine._lock.acquire(blocking=False)
        engine._lock.release()


class TestWithGpuFallback:
    """The unified helper retries on GPU runtime errors and re-raises"""

    def _make_engine(self, device="cuda"):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = device
        engine._compute_type = "float16"
        engine._model = MagicMock()
        engine._pending_gc_collect = False
        engine._lock = threading.Lock()
        engine._reload_under_lock = MagicMock()
        return engine

    def test_returns_inner_call_result_on_success(self):
        engine = self._make_engine()

        def inner(audio, *args, **kwargs):
            return "ok"

        result = engine._with_gpu_fallback(inner, np.zeros(16, dtype=np.float32))
        assert result == "ok"
        # No fallback triggered.
        assert engine._pending_gc_collect is False

    def test_non_gpu_error_is_reraised(self):
        engine = self._make_engine(device="cuda")

        def inner(audio, *args, **kwargs):
            raise ValueError("plain value error, no relevant keywords here")

        with pytest.raises(ValueError, match="plain value error"):
            engine._with_gpu_fallback(inner, np.zeros(16, dtype=np.float32))
        # No fallback triggered.
        assert engine._pending_gc_collect is False

    def test_gpu_error_triggers_fallback_and_retries(self):
        engine = self._make_engine(device="cuda")
        call_count = {"n": 0}

        def inner(audio, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return "cpu result"

        result = engine._with_gpu_fallback(inner, np.zeros(16, dtype=np.float32))
        assert result == "cpu result"
        # Fallback triggered: model torn down, device switched to CPU.
        assert engine._device == "cpu"
        assert engine._compute_type == "int8"
        assert engine._model is None
        assert engine._pending_gc_collect is True
        # Reload was called.
        engine._reload_under_lock.assert_called_once()

    def test_cpu_device_never_triggers_fallback(self):
        """On a CPU device, even a CUDA-looking error must NOT trigger"""
        engine = self._make_engine(device="cpu")

        def inner(audio, *args, **kwargs):
            raise RuntimeError("CUDA cublas error")

        with pytest.raises(RuntimeError, match="CUDA cublas error"):
            engine._with_gpu_fallback(inner, np.zeros(16, dtype=np.float32))
        # No fallback.
        assert engine._device == "cpu"
        assert engine._pending_gc_collect is False


# ─── load-path cache gate (_probe_cache / _require_model_downloaded) ─


class TestLoadPathCacheGate:
    """The load path NEVER downloads or deletes models automatically."""

    def _make_engine(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.model_size = "small.en"
        engine.config = MagicMock()
        return engine

    def test_probe_cache_returns_path_on_hit_with_valid_integrity(self, monkeypatch):
        engine = self._make_engine()
        fake_local_dir = "/fake/cache/path"
        fake_snapshot = MagicMock(return_value=fake_local_dir)
        fake_verify = MagicMock(return_value=True)
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)

        local_dir, integrity_failed = engine._probe_cache(
            fake_snapshot,
            "Systran/faster-whisper-small.en",
            "main",
            ["*.bin", "*.txt"],
            "small.en",
            progress_callback=None,
        )
        assert local_dir == fake_local_dir
        assert integrity_failed is False
        # The probe must be local-only.
        assert fake_snapshot.call_args.kwargs["local_files_only"] is True

    def test_probe_cache_returns_integrity_failed_on_tampered_hit(self, monkeypatch):
        engine = self._make_engine()
        fake_snapshot = MagicMock(return_value="/fake/cache/path")
        fake_verify = MagicMock(return_value=False)  # integrity FAILED
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)

        local_dir, integrity_failed = engine._probe_cache(fake_snapshot, "repo", "main", [], "small.en")
        assert local_dir is None
        assert integrity_failed is True

    def test_probe_cache_never_deletes_tampered_cache(self, monkeypatch):
        """A tampered cache is NOT deleted by the probe, deletion is an"""
        engine = self._make_engine()
        fake_snapshot = MagicMock(return_value="/fake/cache/path")
        monkeypatch.setattr(
            "voice_typer.server.security.verify_model_integrity",
            lambda local_dir, repo_id: False,
        )
        cleaned = []
        monkeypatch.setattr(
            "voice_typer.server.transcription.cleanup_hf_cache_dir",
            lambda repo_id, log_prefix="": cleaned.append(repo_id),
        )

        local_dir, integrity_failed = engine._probe_cache(fake_snapshot, "repo", "main", [], "small.en")
        assert local_dir is None
        assert integrity_failed is True
        assert cleaned == [], "the cache probe must never delete files"

    def test_probe_cache_returns_miss_on_snapshot_exception(self, monkeypatch):
        engine = self._make_engine()
        fake_snapshot = MagicMock(side_effect=Exception("cache miss"))
        fake_verify = MagicMock()
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)

        local_dir, integrity_failed = engine._probe_cache(fake_snapshot, "repo", "main", [], "small.en")
        assert local_dir is None
        assert integrity_failed is False
        fake_verify.assert_not_called()

    def test_require_model_downloaded_raises_not_downloaded_on_miss(self, monkeypatch):
        engine = self._make_engine()
        monkeypatch.setitem(
            sys.modules,
            "huggingface_hub",
            type(sys)("huggingface_hub"),
        )
        sys.modules["huggingface_hub"].snapshot_download = MagicMock(side_effect=FileNotFoundError("not in cache"))
        from voice_typer.server.asr_errors import ModelNotDownloadedError

        with pytest.raises(ModelNotDownloadedError, match="not downloaded"):
            engine._require_model_downloaded("small.en")

    def test_require_model_downloaded_raises_integrity_error_without_delete(self, monkeypatch):
        engine = self._make_engine()
        fake_hf = type(sys)("huggingface_hub")
        fake_hf.snapshot_download = MagicMock(return_value="/fake/cache/path")
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)
        monkeypatch.setattr(
            "voice_typer.server.security.verify_model_integrity",
            lambda local_dir, repo_id: False,
        )
        cleaned = []
        monkeypatch.setattr(
            "voice_typer.server.transcription.cleanup_hf_cache_dir",
            lambda repo_id, log_prefix="": cleaned.append(repo_id),
        )
        from voice_typer.server.asr_errors import ModelIntegrityError

        with pytest.raises(ModelIntegrityError):
            engine._require_model_downloaded("small.en")
        assert cleaned == [], "the load gate must never delete a tampered cache"

    def test_require_model_downloaded_passes_when_cached(self, monkeypatch):
        engine = self._make_engine()
        fake_hf = type(sys)("huggingface_hub")
        fake_hf.snapshot_download = MagicMock(return_value="/fake/cache/path")
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)
        monkeypatch.setattr(
            "voice_typer.server.security.verify_model_integrity",
            lambda local_dir, repo_id: True,
        )

        # Must not raise.
        engine._require_model_downloaded("small.en")

    def test_require_model_downloaded_skips_non_whisper(self, monkeypatch):
        engine = self._make_engine()
        fake_hf = type(sys)("huggingface_hub")
        fake_hf.snapshot_download = MagicMock(side_effect=AssertionError("must not be called"))
        monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

        # Non-Whisper sizes are handled by their own load path.
        engine._require_model_downloaded("parakeet")
        engine._require_model_downloaded("qwen")
        engine._require_model_downloaded("")


# Late import so the autouse fixture can install the mock first.
from unittest.mock import patch  # noqa: E402
