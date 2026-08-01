"""Focused tests for the transcription.py refactors.

Covers:
- ``_NvidiaDllPathManager`` singleton encapsulation (replaces the three
  module-level mutable globals ``_nvidia_dll_path_handles``,
  ``_nvidia_dll_paths_configured``, ``_nvidia_config_lock``).
- ``_with_lock_and_deferred_gc`` context manager (extracts the 3-way
  duplicated lock + deferred-gc wrapper from ``transcribe`` /
  ``transcribe_with_fallback`` / ``transcribe_words``).
- ``_with_gpu_fallback`` unified helper (replaces the two near-identical
  ``_transcribe_with_fallback_unlocked`` and
  ``_transcribe_words_with_fallback_unlocked`` 30-line methods).
- ``_pre_download_model`` phase helpers (``_probe_cache``,
  ``_require_consent``, ``_check_disk``, ``_download_and_verify``)
  replacing the 188-line monolith with a thin orchestrator.
"""

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


# ─── _NvidiaDllPathManager (AC-79) ────────────────────────────────────


class TestNvidiaDllPathManagerIsolation:
    """The manager class lets tests construct a fresh instance with its
    own state_dict instead of resetting module-level globals."""

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
        """Production singleton reads/writes through ``globals()`` so
        existing tests that poke ``mod._nvidia_dll_path_handles``
        directly still work."""
        import voice_typer.server.transcription as mod

        # Save and restore original state.
        orig_handles = mod._nvidia_dll_path_handles
        orig_configured = mod._nvidia_dll_paths_configured
        try:
            mod._nvidia_dll_path_handles = [MagicMock()]
            mod._nvidia_dll_paths_configured = True
            assert mod._nvidia_dll_paths.handles == mod._nvidia_dll_path_handles
            assert mod._nvidia_dll_paths.configured is True
            # free_handles via the singleton should clear the module-level
            # list (same backing dict).
            mod._nvidia_dll_paths.free_handles()
            assert mod._nvidia_dll_path_handles == []
        finally:
            mod._nvidia_dll_path_handles = orig_handles
            mod._nvidia_dll_paths_configured = orig_configured

    def test_module_level_functions_delegate_to_singleton(self):
        """The public ``_free_nvidia_dll_path_handles`` and
        ``_configure_nvidia_dll_paths`` functions delegate to the
        singleton."""
        import voice_typer.server.transcription as mod

        orig_handles = mod._nvidia_dll_path_handles
        try:
            mod._nvidia_dll_path_handles = [MagicMock()]
            mod._free_nvidia_dll_path_handles()
            assert mod._nvidia_dll_path_handles == []
        finally:
            mod._nvidia_dll_path_handles = orig_handles


# ─── _with_lock_and_deferred_gc (AC-77) ───────────────────────────────


class TestWithLockAndDeferredGc:
    """The context manager acquires ``self._lock`` for the body and
    performs the deferred gc.collect() + release_gpu_memory() AFTER the
    lock is released (RACE-023)."""

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
                # been called yet (lock is still held).
                assert engine._pending_gc_collect is True
                gc_calls_before = mock_gc.call_count
            # After the with-block exits, the deferred gc should fire.
            assert mock_gc.call_count > gc_calls_before
        # Flag was cleared by the deferred cleanup.
        assert engine._pending_gc_collect is False

    def test_no_gc_when_flag_not_set(self):
        import gc as real_gc

        engine = self._make_engine()
        engine._pending_gc_collect = False  # no fallback — no gc needed

        with patch.object(real_gc, "collect") as mock_gc, engine._with_lock_and_deferred_gc():
            pass
        # Flag was False, so gc.collect should NOT have been called.
        assert mock_gc.call_count == 0

    def test_lock_is_acquired_during_body(self):
        engine = self._make_engine()
        # If the lock is held during the body, a second acquire attempt
        # would block. We test non-blocking acquire.
        with engine._with_lock_and_deferred_gc():
            assert not engine._lock.acquire(blocking=False)
        # After the body, the lock is released.
        assert engine._lock.acquire(blocking=False)
        engine._lock.release()


# ─── _with_gpu_fallback (AC-76) ───────────────────────────────────────


class TestWithGpuFallback:
    """The unified helper retries on GPU runtime errors and re-raises
    non-GPU errors unchanged."""

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
            raise ValueError("plain value error — no relevant keywords here")

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
        # gc deferred via flag.
        assert engine._pending_gc_collect is True
        # Reload was called.
        engine._reload_under_lock.assert_called_once()

    def test_cpu_device_never_triggers_fallback(self):
        """On a CPU device, even a CUDA-looking error must NOT trigger
        the fallback (the classifier short-circuits at the top)."""
        engine = self._make_engine(device="cpu")

        def inner(audio, *args, **kwargs):
            raise RuntimeError("CUDA cublas error")

        with pytest.raises(RuntimeError, match="CUDA cublas error"):
            engine._with_gpu_fallback(inner, np.zeros(16, dtype=np.float32))
        # No fallback.
        assert engine._device == "cpu"
        assert engine._pending_gc_collect is False


# ─── _pre_download_model phase helpers (AC-75) ────────────────────────


class TestPreDownloadPhaseHelpers:
    """The 4 phase helpers split the 188-line ``_pre_download_model``
    into focused, individually testable units."""

    def _make_engine(self, huggingface_consent=True):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        cfg = MagicMock()
        cfg.huggingface_consent = huggingface_consent
        engine.config = cfg
        return engine

    def test_probe_cache_returns_path_on_hit_with_valid_integrity(self, monkeypatch):
        engine = self._make_engine()
        fake_local_dir = "/fake/cache/path"
        fake_snapshot = MagicMock(return_value=fake_local_dir)
        # Patch verify_model_integrity to return True (integrity OK).
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

    def test_probe_cache_returns_integrity_failed_on_tampered_hit(self, monkeypatch):
        engine = self._make_engine()
        fake_snapshot = MagicMock(return_value="/fake/cache/path")
        fake_verify = MagicMock(return_value=False)  # integrity FAILED
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)

        local_dir, integrity_failed = engine._probe_cache(fake_snapshot, "repo", "main", [], "small.en")
        assert local_dir is None
        assert integrity_failed is True

    def test_probe_cache_returns_miss_on_snapshot_exception(self, monkeypatch):
        engine = self._make_engine()
        fake_snapshot = MagicMock(side_effect=Exception("cache miss"))
        # verify_model_integrity should NOT be called on cache miss.
        fake_verify = MagicMock()
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)

        local_dir, integrity_failed = engine._probe_cache(fake_snapshot, "repo", "main", [], "small.en")
        assert local_dir is None
        assert integrity_failed is False
        fake_verify.assert_not_called()

    def test_require_consent_raises_when_not_given(self):
        engine = self._make_engine(huggingface_consent=False)
        from voice_typer.server.asr_errors import ConsentRequiredError

        with pytest.raises(ConsentRequiredError, match="consent not given"):
            engine._require_consent("small.en", None, False, "repo")

    def test_require_consent_cleans_tampered_cache_after_consent(self, monkeypatch):
        engine = self._make_engine(huggingface_consent=True)
        cleaned = []
        monkeypatch.setattr(
            "voice_typer.server.transcription.cleanup_hf_cache_dir",
            lambda repo_id, log_prefix: cleaned.append(repo_id),
        )

        # integrity_failed=True → cache should be cleaned after consent.
        engine._require_consent("small.en", None, True, "Systran/faster-whisper-small.en")
        assert cleaned == ["Systran/faster-whisper-small.en"]

    def test_require_consent_skips_cleanup_when_integrity_ok(self, monkeypatch):
        engine = self._make_engine(huggingface_consent=True)
        cleaned = []
        monkeypatch.setattr(
            "voice_typer.server.transcription.cleanup_hf_cache_dir",
            lambda repo_id, log_prefix: cleaned.append(repo_id),
        )

        engine._require_consent("small.en", None, False, "repo")
        assert cleaned == []

    def test_require_consent_raises_when_config_is_none(self):
        """Defensive: ``self.config`` may be None — treat as not given."""
        from voice_typer.server.asr_errors import ConsentRequiredError
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.config = None
        with pytest.raises(ConsentRequiredError):
            engine._require_consent("small.en", None, False, "repo")

    def test_check_disk_delegates_to_helper(self, monkeypatch):
        engine = self._make_engine()
        called = []
        monkeypatch.setattr(
            "voice_typer.server.transcription._check_disk_space_for_download",
            lambda repo_id, model_size: called.append((repo_id, model_size)),
        )
        engine._check_disk("repo", "small.en")
        assert called == [("repo", "small.en")]

    def test_download_and_verify_raises_on_integrity_failure(self, monkeypatch):
        engine = self._make_engine()
        fake_snapshot = MagicMock(return_value="/fake/download/path")
        fake_download_with_retry = MagicMock(return_value="/fake/download/path")
        fake_verify = MagicMock(return_value=False)  # integrity FAILED
        cleaned = []
        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            fake_download_with_retry,
        )
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)
        monkeypatch.setattr(
            "voice_typer.server.transcription.cleanup_hf_cache_dir",
            lambda repo_id, log_prefix: cleaned.append(repo_id),
        )

        with pytest.raises(RuntimeError, match="integrity verification failed"):
            engine._download_and_verify(fake_snapshot, "repo", "main", [], None, "small.en")
        # Cache was cleaned before raising.
        assert cleaned == ["repo"]

    def test_download_and_verify_succeeds_on_valid_download(self, monkeypatch):
        engine = self._make_engine()
        fake_snapshot = MagicMock()
        fake_download_with_retry = MagicMock(return_value="/fake/download/path")
        fake_verify = MagicMock(return_value=True)  # integrity OK
        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            fake_download_with_retry,
        )
        monkeypatch.setattr("voice_typer.server.security.verify_model_integrity", fake_verify)

        # Should not raise.
        engine._download_and_verify(fake_snapshot, "repo", "main", [], None, "small.en")
        fake_download_with_retry.assert_called_once()

    def test_pre_download_skips_for_parakeet(self):
        """Non-Whisper model sizes are skipped early."""
        engine = self._make_engine()
        # Should be a no-op (no exception, no download attempt).
        engine._pre_download_model("parakeet")
        engine._pre_download_model("qwen")
        engine._pre_download_model("")


# Late import so the autouse fixture can install the mock first.
from unittest.mock import patch  # noqa: E402
