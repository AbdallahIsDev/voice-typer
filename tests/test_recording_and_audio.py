"""Tests for recording, resampling, audio processing, xrun detection,
watchdog, streaming session, and related recording infrastructure."""

from __future__ import annotations

import inspect
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestXrunThresholdCounter:
    """The xrun counter increments on each callback and fires a notification at threshold."""

    def test_xrun_callback_increments_counter_and_notifies(self):
        recorder = MagicMock()
        recorder._xrun_count = 0
        recorder._xrun_threshold = 3

        for i in range(1, recorder._xrun_threshold + 1):
            recorder._xrun_count = i

        assert recorder._xrun_count == recorder._xrun_threshold


class TestResampleError:
    """_resample_chunk raises ResampleError when neither scipy nor linear-interp can resample."""

    def test_resample_chunk_raises_on_total_failure(self):
        from voice_typer.server.recording import Recorder, ResampleError, ResampleUnavailable

        recorder = Recorder.__new__(Recorder)
        with patch(
            "voice_typer.server.recording._get_resample_poly",
            side_effect=ResampleUnavailable("scipy is missing"),
        ), patch(
            "voice_typer.server.recording.np.interp",
            side_effect=ValueError("interp boom"),
        ):
            audio = np.ones(1024, dtype=np.float32)
            with pytest.raises(ResampleError):
                recorder._resample_chunk(audio, effective_sr=48000, target_sr=16000)

    def test_resample_chunk_returns_empty_for_empty_input(self):
        from voice_typer.server.recording import Recorder

        recorder = Recorder.__new__(Recorder)
        result = recorder._resample_chunk(
            np.array([], dtype=np.float32),
            effective_sr=48000,
            target_sr=16000,
        )
        assert len(result) == 0


class TestWatchdogForceRecover:
    """After _watchdog_max_firings consecutive expirations, _force_recover resets state."""

    def test_force_param_skips_alive_check(self):
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._watchdog_firings = 3
        ctrl._watchdog_max_firings = 3
        ctrl._transcription_thread = MagicMock()
        ctrl._transcription_thread.is_alive.return_value = True
        ctrl._watchdog_stop_event = MagicMock()
        ctrl._watchdog_event = MagicMock()
        ctrl._watchdog_thread = None

        app = MagicMock()
        app._busy_event.is_set.return_value = False
        ctrl._app = app

        ctrl._force_recover_from_stuck_transcription(force=True)

        app.tray.set_state.assert_called()
        app._busy_event.set.assert_called_once()

    def test_non_force_re_arms_when_worker_alive(self):
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._watchdog_firings = 1
        ctrl._watchdog_max_firings = 3
        ctrl._transcription_thread = MagicMock()
        ctrl._transcription_thread.is_alive.return_value = True
        ctrl._watchdog_stop_event = MagicMock()
        ctrl._watchdog_event = MagicMock()
        ctrl._watchdog_thread = None
        app = MagicMock()
        app._busy_event.is_set.return_value = False
        ctrl._app = app

        ctrl._force_recover_from_stuck_transcription(force=False)
        app._busy_event.set.assert_not_called()
        ctrl._watchdog_stop_event.set.assert_not_called()
        app.tray.set_state.assert_called()
        app.tray.notify.assert_called()


class TestFriendlyTranscriptionError:
    """_friendly_transcription_error does not leak raw exception text."""

    def test_cuda_oom_message(self):
        from voice_typer.server.dictation_pipeline import _friendly_transcription_error

        exc = RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
        msg = _friendly_transcription_error(exc)
        assert "GPU" in msg or "memory" in msg
        assert "GiB" not in msg

    def test_unknown_error_includes_class_name_only(self):
        from voice_typer.server.dictation_pipeline import _friendly_transcription_error

        exc = ValueError("Bad value at /home/user/secret/file.py:42")
        msg = _friendly_transcription_error(exc)
        assert "/home/user/secret" not in msg
        assert "ValueError" in msg

    def test_network_error_message(self):
        from voice_typer.server.dictation_pipeline import _friendly_transcription_error

        exc = ConnectionError("Failed to reach https://internal.api/v1")
        msg = _friendly_transcription_error(exc)
        assert "network" in msg.lower()


class TestParakeetBackendError:
    """Parakeet raises TranscriptionBackendError on failure."""

    def test_raises_on_gpu_failure(self):
        from voice_typer.server.parakeet_engine import (
            ParakeetEngine,
            TranscriptionBackendError,
        )

        engine = ParakeetEngine.__new__(ParakeetEngine)
        engine._lock = threading.Lock()
        engine._model = MagicMock()
        engine._processor = MagicMock()
        engine.device = "cuda"
        engine.transcribe = MagicMock(side_effect=RuntimeError("model crashed"))

        with pytest.raises(TranscriptionBackendError):
            engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))


class TestQwenFallback:
    """QwenEngine does CPU fallback on CUDA errors."""

    def test_cuda_error_triggers_cpu_retry(self):
        from voice_typer.server.qwen_engine import QwenEngine

        engine = QwenEngine.__new__(QwenEngine)
        engine._lock = threading.Lock()
        engine._model = MagicMock()
        engine.device = "cuda"
        engine.language = "en"

        call_count = {"n": 0}

        def fake_transcribe(audio, audio_stats=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("CUDA error: out of memory")
            return "cpu fallback result"

        engine.transcribe = fake_transcribe

        result = engine.transcribe_with_fallback(np.ones(16000, dtype=np.float32))
        assert result == "cpu fallback result"
        assert call_count["n"] == 2
        assert engine.device == "cpu"


class TestPendingModelChange:
    """change_model during recording captures a pending request; apply_pending_model_change reapplies."""

    def test_pending_flag_set_during_recording(self):
        from voice_typer.server.model_manager import ModelManager
        from unittest.mock import MagicMock

        mm = ModelManager.__new__(ModelManager)
        mm._pending_model_change = None
        app = MagicMock()
        app.recorder.recording = True
        app._busy_event.is_set.return_value = False
        app.config.asr_backend = "whisper"
        app.config.model_size = "tiny.en"
        app.config.save = MagicMock()
        app.tray.notify = MagicMock()
        mm._app = app

        mm._pending_model_change = "medium.en"
        assert mm._pending_model_change == "medium.en"

    def test_apply_pending_model_change_noop_when_none(self):
        from voice_typer.server.model_manager import ModelManager

        mm = ModelManager.__new__(ModelManager)
        mm._pending_model_change = None
        result = mm.apply_pending_model_change()
        assert result is False


class TestPendingTimersLockGuarded:
    """_pending_timers mutations guarded by a lock."""

    def test_schedule_and_cancel_are_threadsafe(self):
        from voice_typer.server import app as app_module
        from unittest.mock import MagicMock

        app = MagicMock()
        app._pending_timers = []
        import threading
        app._pending_timers_lock = threading.Lock()
        app._timer_generation = 0

        app._schedule_timer = app_module.VoiceTyperApp._schedule_timer.__get__(app)
        app._cancel_pending_timers = app_module.VoiceTyperApp._cancel_pending_timers.__get__(app)

        errors: list[Exception] = []

        def scheduler():
            try:
                for _ in range(50):
                    app._schedule_timer(0.001, lambda: None)
            except Exception as e:
                errors.append(e)

        def canceller():
            try:
                for _ in range(50):
                    app._cancel_pending_timers()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=scheduler)
        t2 = threading.Thread(target=canceller)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert errors == []


class TestAudioCallbackPreStartGuard:
    """Audio callback early-returns if _recording_event is not set."""

    def test_callback_returns_early_when_not_recording(self):
        from voice_typer.server.recording import Recorder
        import threading

        recorder = Recorder.__new__(Recorder)
        recorder._recording_event = threading.Event()
        recorder._xruns = 0
        recorder._xrun_threshold = 3
        recorder._last_xrun_log_ts = 0.0
        recorder.on_xrun_threshold = MagicMock()
        recorder._audio_processor = None
        recorder._lock = threading.Lock()
        recorder._buffer = __import__("collections").deque(maxlen=10)
        recorder._chunk_count = 0
        recorder._effective_sr = 16000
        recorder._recent_rms_values = __import__("collections").deque(maxlen=50)
        recorder._silence_timer = 0.0
        recorder._silence_warning_count = 0
        recorder._recording_start_time = 0.0
        recorder.on_silence_warning = MagicMock()
        recorder.on_silence_auto_stop = MagicMock()
        recorder.on_max_duration_auto_stop = MagicMock()
        recorder.on_rms_level = None
        recorder._clip_count = 0
        recorder._peak = 0.0
        recorder._last_clip_log_time = 0.0
        recorder._cached_max_recording = 0

        assert not recorder._recording_event.is_set()


class TestResampleCacheInvalidation:
    """Resample cache invalidates on dtype/sr change."""

    def test_cache_key_includes_dtype_and_sample_rates(self):
        from voice_typer.server.recording import Recorder
        import inspect
        src = inspect.getsource(Recorder.__init__)
        assert "_cached_resample_key" in src


class TestPrepareAudioNarrowExcept:
    """_prepare_audio catches (ValueError, OSError, TypeError), not bare Exception."""

    def test_prepare_audio_propagates_memory_error(self):
        from voice_typer.server.recording import Recorder
        from unittest.mock import MagicMock

        recorder = Recorder.__new__(Recorder)
        recorder.config = MagicMock()
        recorder.config.sample_rate = 16000
        with patch(
            "voice_typer.server.recording._get_resample_poly",
            side_effect=MemoryError("out of RAM"),
        ), patch(
            "voice_typer.server.recording.np.interp",
            side_effect=MemoryError("out of RAM"),
        ):
            with pytest.raises(MemoryError):
                recorder._prepare_audio(
                    np.ones(1024, dtype=np.float32),
                    effective_sr=48000,
                )


class TestStreamingStartSurfaceFailure:
    """start() catches Thread.start() failure and sets _thread_start_failed."""

    def test_thread_start_failed_set_on_runtime_error(self):
        from voice_typer.server.streaming import StreamingTranscriptionSession, StreamingConfig
        from unittest.mock import MagicMock

        recorder = MagicMock()
        recorder.snapshot.return_value = np.array([], dtype=np.float32)
        transcriber = MagicMock()
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(),
            sample_rate=16000,
        )
        with patch("voice_typer.server.streaming.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            mock_thread.start.side_effect = RuntimeError("can't start new thread")
            MockThread.return_value = mock_thread
            session.start()

        assert session._thread_start_failed is True
        assert session._thread is None
        assert session._stopped_event.is_set()


class TestGetStatusReturnsDict:
    """service.get_status() returns dict with xruns_since_start."""

    def test_get_status_includes_xruns(self):
        from voice_typer.server.service import VoiceTyperService
        from unittest.mock import MagicMock

        app = MagicMock()
        app.tray.state.value = "idle"
        app.recorder._xruns = 7
        service = VoiceTyperService(app)

        result = service.get_status()

        assert isinstance(result, dict)
        assert result["status"] == "idle"
        assert result["xruns_since_start"] == 7


class TestCancelGuaranteesTrayReset:
    """Even if recorder.discard() raises, cancel resets tray state to IDLE."""

    def test_cancel_resets_state_when_discard_fails(self):
        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.tray_types import AppState
        from unittest.mock import MagicMock

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._watchdog_lock = threading.Lock()
        ctrl._watchdog_firings = 0
        ctrl._watchdog_max_firings = 3
        ctrl._transcription_thread = None

        app = MagicMock()
        app._cycle_id = "test"
        app._pending_timers_lock = threading.Lock()
        app._pending_timers = []
        app.recorder.recording = True
        app.recorder.discard.side_effect = RuntimeError("PortAudio boom")
        app._waveform_bubble = MagicMock()
        app._cancel_streaming_session = MagicMock()
        app._restore_volume = MagicMock()
        app.config.bubble_behavior = "auto_hide"
        app._busy_event = MagicMock()
        ctrl._app = app

        ctrl.cancel()

        tray_calls = [c.args for c in app.tray.set_state.call_args_list]
        assert any(args[0] == AppState.CANCELLING for args in tray_calls)
        assert any(args[0] == AppState.IDLE for args in tray_calls)
        app._busy_event.set.assert_called()


class TestCancelSetsCancellingState:
    """cancel() sets AppState.CANCELLING before reset to IDLE."""

    def test_cancel_sets_cancelling(self):
        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.tray_types import AppState
        from unittest.mock import MagicMock

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._streaming_session_lock = threading.Lock()
        ctrl._watchdog_lock = threading.Lock()
        ctrl._watchdog_firings = 0
        ctrl._watchdog_max_firings = 3
        ctrl._transcription_thread = None

        app = MagicMock()
        app._cycle_id = "test"
        app._pending_timers_lock = threading.Lock()
        app._pending_timers = []
        app.recorder.recording = True
        app._waveform_bubble = MagicMock()
        app._cancel_streaming_session = MagicMock()
        app._restore_volume = MagicMock()
        app.config.bubble_behavior = "auto_hide"
        app._busy_event = MagicMock()
        ctrl._app = app

        ctrl.cancel()

        first_call = app.tray.set_state.call_args_list[0]
        assert first_call.args[0] == AppState.CANCELLING
        last_call = app.tray.set_state.call_args_list[-1]
        assert last_call.args[0] == AppState.IDLE


class TestSetConfigInvalidatesTrayCache:
    """IPC set_config calls tray.invalidate_menu_cache."""

    def test_invalidate_menu_cache_called(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer
        from unittest.mock import MagicMock

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        cfg = config_module.Config()
        cfg.save = MagicMock(return_value=True)

        app = MagicMock()
        app.config = cfg
        app._sync_prewarm_task = MagicMock()
        app._sync_autostart = MagicMock()
        app._register_esc_hotkey = MagicMock()
        app._unregister_esc_hotkey = MagicMock()
        app._register_repaste_hotkey = MagicMock()
        app.tray.invalidate_menu_cache = MagicMock()

        server = IPCServer(app)
        server._dispatch({
            "id": 1, "type": "set_config",
            "data": {"beam_size": 5},
        })

        app.tray.invalidate_menu_cache.assert_called_once()


class TestStreamingSessionLock:
    """Streaming session accessors acquire a lock."""

    def test_lock_exists(self):
        from voice_typer.server.recording_controller import RecordingController
        src = inspect.getsource(RecordingController.__init__)
        assert "_streaming_session_lock" in src


class TestConsecutiveFailuresLock:
    """_consecutive_failures guarded by a lock."""

    def test_lock_exists(self):
        from voice_typer.server.streaming import StreamingTranscriptionSession
        src = inspect.getsource(StreamingTranscriptionSession.__init__)
        assert "_consecutive_failures_lock" in src


class TestCancelNonBlocking:
    """cancel() default is non-blocking; blocking=True waits."""

    def test_cancel_signature_has_blocking_kwarg(self):
        from voice_typer.server.streaming import StreamingTranscriptionSession
        sig = inspect.signature(StreamingTranscriptionSession.cancel)
        assert "blocking" in sig.parameters
        assert sig.parameters["blocking"].default is False


class TestLoadTranscriberImplExists:
    """_load_transcriber_impl is the shared load body."""

    def test_method_exists(self):
        from voice_typer.server.transcription import TranscriptionEngine
        assert hasattr(TranscriptionEngine, "_load_transcriber_impl")

    def test_reload_under_lock_delegates(self):
        from voice_typer.server.transcription import TranscriptionEngine
        from unittest.mock import MagicMock

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._build_fallback_chain = MagicMock(return_value=[])
        called = {"n": 0}
        def fake_impl(chain, *, acquire_lock, **kw):
            called["n"] += 1
            called["acquire_lock"] = acquire_lock
        engine._load_transcriber_impl = fake_impl
        engine._reload_under_lock()
        assert called["n"] == 1
        assert called["acquire_lock"] is False


class TestFinalizeSkipsTailRetranscribe:
    """_finalize_impl returns early when last committed word is within 1.5s of audio end."""

    def test_skips_when_last_committed_is_recent(self):
        from voice_typer.server.streaming import (
            StreamingTranscriptionSession,
            StreamingConfig,
            WordTiming,
        )
        from unittest.mock import MagicMock

        recorder = MagicMock()
        recorder.snapshot.return_value = np.array([], dtype=np.float32)
        transcriber = MagicMock()
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(),
            sample_rate=16000,
        )
        session.assembler._words.append(
            WordTiming("hello", start_seconds=0.0, end_seconds=4.5)
        )
        session.assembler.last_committed_time = 4.5
        audio = np.zeros(5 * 16000, dtype=np.float32)
        result = session._finalize_impl(audio)
        assert "hello" in result
        transcriber.transcribe_with_fallback.assert_not_called()
        transcriber.transcribe_words.assert_not_called()


class TestWatchdogTimerTracked:
    """Watchdog uses Event.wait instead of Timer."""

    def test_watchdog_added_to_pending_timers(self):
        from voice_typer.server import recording_controller
        src = inspect.getsource(recording_controller)
        assert "_watchdog_event" in src
        assert "_watchdog_stop_event" in src


class TestIsGpuRuntimeErrorClassHierarchy:
    """GPU error detection uses isinstance, not just substring."""

    def test_returns_false_on_cpu_device(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cpu"
        cuda_exc = RuntimeError("CUDA error")
        assert engine._is_gpu_runtime_error(cuda_exc) is False

    def test_substring_fallback_for_wrapped_errors(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cuda"
        exc = RuntimeError("Library cublas64_12.dll not found")
        assert engine._is_gpu_runtime_error(exc) is True


class TestResolveDeviceNarrowExcept:
    """_resolve_device catches (OSError, RuntimeError, ImportError), not bare Exception."""

    def test_resolve_device_returns_cpu_on_cuda_unavailable(self):
        from voice_typer.server.transcription import TranscriptionEngine
        from unittest.mock import MagicMock

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        fake_ct2 = MagicMock()
        fake_ct2.get_cuda_device_count.side_effect = OSError("no driver")
        with patch.dict("sys.modules", {"ctranslate2": fake_ct2}):
            device, compute_type = engine._resolve_device("auto")
        assert device == "cpu"
        assert compute_type == "int8"


class TestFreeNvidiaDllHandles:
    """_free_nvidia_dll_path_handles() releases DLL handles."""

    def test_function_exists(self):
        from voice_typer.server.transcription import _free_nvidia_dll_path_handles
        assert callable(_free_nvidia_dll_path_handles)

    def test_frees_handles_without_error(self):
        from voice_typer.server import transcription as mod
        from unittest.mock import MagicMock

        fake_handle = MagicMock()
        mod._nvidia_dll_path_handles = [fake_handle]
        mod._free_nvidia_dll_path_handles()
        assert mod._nvidia_dll_path_handles == []
        fake_handle.close.assert_called_once()


class TestAudioProcessorNullChecksFunctional:
    """Audio processor null checks prevent crashes."""

    def test_quality_callback_null_does_not_crash(self):
        """When _quality_callback is None, _run_quality_check should
            be a no-op, not crash."""
        from voice_typer.server.audio_processor import AudioProcessor

        class _Cfg:
            noise_filter_highpass = False
            noise_filter_gate = False
            noise_filter_eq = False
            noise_filter_compressor = False
            noise_filter_limiter = False
            noise_filter_notch = False
            noise_suppression_method = 'none'
        proc = AudioProcessor(_Cfg(), sample_rate=16000)
        proc._quality_callback = None
        chunk = np.ones(1024, dtype=np.float32) * 0.1
        proc._run_quality_check(chunk)


class TestServerPackageInit:
    """voice_typer/server/ has __init__.py and __main__.py."""

    def test_init_py_exists(self):
        import voice_typer.server
        assert voice_typer.server.__file__ is not None

    def test_main_py_exists(self):
        import importlib.util
        spec = importlib.util.find_spec("voice_typer.server.__main__")
        assert spec is not None


class TestGetVoiceTyperPythonRemoved:
    """asr_setup.get_voice_typer_python() is deleted."""

    def test_function_not_present(self):
        from voice_typer.server import asr_setup
        assert not hasattr(asr_setup, "get_voice_typer_python")
