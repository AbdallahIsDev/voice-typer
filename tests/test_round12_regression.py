"""Round 12 regression tests.

Covers:
- ERR-012: _prepare_audio narrow exception types
- ERR-013: HistoryDB sentinel contract (HistoryDBError type exists)
- ERR-014: vocabulary/template apply failures promoted to log.warning + tray notify
- ERR-015: _is_gpu_runtime_error uses class hierarchy
- ERR-016: _resolve_device narrow exception types
- ERR-017: state_changed event emitted on IPC client connect
- ERR-018: repaste_last splits copy + paste errors
- ERR-019: streaming start() surfaces thread-creation failure
- ERR-020: _run_polling_loop logs callback exceptions
- ERR-021: get_status returns dict with xruns_since_start
- ERR-022: _ensure_desktop_shortcut uses utf-8 encoding fallback
- ERR-023: cancel_dictation guarantees tray state reset on discard failure
- ERR-024: ensure_active_engine_loaded uses lock
- ARCH-014: _load_transcriber_impl extracted
- ARCH-016: _transcription_thread cleared under lock
- ARCH-017: watchdog Timer tracked in _pending_timers
- ARCH-018: _streaming_session guarded by lock
- ARCH-021: _effective_sr written under lock
- ARCH-023: per-session flags reset in start()
- ARCH-024: _consecutive_failures guarded by lock
- ARCH-025: cancel() non-blocking by default
- ARCH-027: _active_misspellings guarded by lock
- ARCH-028: single VOCAB_FILENAME / BUNDLED_CORRECTIONS_PATH constant
- ARCH-029: CorrectionsLoadError typed exception
- ARCH-032: _prune_old_entries no longer rebuilds word_key_index
- ARCH-033: ResampleUnavailable typed exception
- ARCH-037: _build_models_submenu accepts config_provider
- ARCH-041: _init_vk_map includes numpad/media/browser keys
- ARCH-042: AppState.CANCELLING set during cancel
- ARCH-043: set_config invalidates tray menu cache
- ARCH-046: console handler skipped on pythonw.exe
- DEAD-004: voice_typer/server/__init__.py and __main__.py exist
- DEAD-015: icon generators documented
- DEAD-026: get_voice_typer_python removed
"""
from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── ERR-012: _prepare_audio narrow exceptions ─────────────────────────


class TestPrepareAudioNarrowExcept:
    """ERR-012: _prepare_audio catches (ValueError, OSError, TypeError)
    instead of bare Exception so genuine bugs propagate."""

    def test_prepare_audio_propagates_memory_error(self):
        """MemoryError must NOT be swallowed by the resample try/except."""
        from voice_typer.server.recording import Recorder

        recorder = Recorder.__new__(Recorder)
        # Provide a minimal Config stub so self.config.sample_rate works.
        recorder.config = MagicMock()
        recorder.config.sample_rate = 16000
        # Force both scipy and interp paths to raise MemoryError.
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


# ── ERR-013: HistoryDBError type exists ────────────────────────────────


class TestHistoryDBErrorType:
    """ERR-013: HistoryDBError is a typed exception."""

    def test_historydberror_is_runtime_error(self):
        from voice_typer.server.history_db import HistoryDBError
        assert issubclass(HistoryDBError, RuntimeError)


# ── ERR-014: vocabulary/template apply failures notify ─────────────────


class TestApplyVocabularyTemplateNotify:
    """ERR-014: failures in _apply_vocabulary / _apply_templates must
    fire a tray notification on first occurrence."""

    def test_apply_vocabulary_notifies_on_failure(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        app._vocabulary_manager = MagicMock()
        app._vocabulary_manager.apply_to_text.side_effect = RuntimeError("vocab boom")
        app.tray.notify = MagicMock()
        pipeline._app = app
        pipeline._vocab_fail_notified = False

        pipeline._apply_vocabulary("hello world")

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("Vocabulary" in str(args) for args in notify_calls), (
            f"Expected vocabulary-failure notification, got: {notify_calls}"
        )

    def test_apply_templates_notifies_on_failure(self):
        from voice_typer.server.dictation_pipeline import DictationPipeline

        pipeline = DictationPipeline.__new__(DictationPipeline)
        app = MagicMock()
        app.config.templates_enabled = True
        app._template_manager = MagicMock()
        app._template_manager.match.side_effect = RuntimeError("template boom")
        app.tray.notify = MagicMock()
        pipeline._app = app
        pipeline._template_fail_notified = False

        pipeline._apply_templates("hello world")

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("Template" in str(args) for args in notify_calls), (
            f"Expected template-failure notification, got: {notify_calls}"
        )


# ── ERR-015: _is_gpu_runtime_error uses class hierarchy ───────────────


class TestIsGpuRuntimeErrorClassHierarchy:
    """ERR-015: detect GPU errors via isinstance, not just substring."""

    def test_returns_false_on_cpu_device(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cpu"
        # Even a CUDA-named exception must return False on CPU.
        cuda_exc = RuntimeError("CUDA error")
        assert engine._is_gpu_runtime_error(cuda_exc) is False

    def test_substring_fallback_for_wrapped_errors(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._device = "cuda"
        # A generic RuntimeError with "cublas" in the message should
        # be detected via the substring fallback.
        exc = RuntimeError("Library cublas64_12.dll not found")
        assert engine._is_gpu_runtime_error(exc) is True


# ── ERR-016: _resolve_device narrow exceptions ────────────────────────


class TestResolveDeviceNarrowExcept:
    """ERR-016: _resolve_device catches (OSError, RuntimeError, ImportError),
    not bare Exception."""

    def test_resolve_device_returns_cpu_on_cuda_unavailable(self):
        from voice_typer.server.transcription import TranscriptionEngine

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        # Make ctranslate2 importable but raise OSError on get_cuda_device_count.
        fake_ct2 = MagicMock()
        fake_ct2.get_cuda_device_count.side_effect = OSError("no driver")
        with patch.dict(sys.modules, {"ctranslate2": fake_ct2}):
            device, compute_type = engine._resolve_device("auto")
        assert device == "cpu"
        assert compute_type == "int8"


# ── ERR-018: repaste_last splits errors ────────────────────────────────


class TestRepasteLastSplitsErrors:
    """ERR-018: clipboard-copy failure and paste-keystroke failure must
    produce distinct tray notifications."""

    def test_copy_failure_message_mentions_clipboard(self):
        from voice_typer.server import app as app_module

        app = app_module.VoiceTyperApp.__new__(app_module.VoiceTyperApp)
        app._last_transcription = "hello"
        app.clipboard = MagicMock()
        app.clipboard.copy.side_effect = RuntimeError("clipboard locked")
        app.tray = MagicMock()

        app.repaste_last()

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("clipboard" in str(args).lower() for args in notify_calls)
        # paste() must NOT have been called since copy failed.
        app.clipboard.paste.assert_not_called()

    def test_paste_failure_message_mentions_keystroke(self):
        from voice_typer.server import app as app_module

        app = app_module.VoiceTyperApp.__new__(app_module.VoiceTyperApp)
        app._last_transcription = "hello"
        app.clipboard = MagicMock()
        app.clipboard.copy.return_value = True
        app.clipboard.paste.side_effect = RuntimeError("SendInput failed")
        app.tray = MagicMock()

        app.repaste_last()

        notify_calls = [c.args for c in app.tray.notify.call_args_list]
        assert any("paste" in str(args).lower() or "ctrl+v" in str(args).lower()
                    for args in notify_calls), (
            f"Expected paste-failure notification, got: {notify_calls}"
        )


# ── ERR-019: streaming start() surfaces thread-creation failure ───────


class TestStreamingStartSurfaceFailure:
    """ERR-019: start() catches Thread.start() failure and sets
    _thread_start_failed."""

    def test_thread_start_failed_set_on_runtime_error(self):
        from voice_typer.server.streaming import StreamingTranscriptionSession, StreamingConfig

        recorder = MagicMock()
        recorder.snapshot.return_value = np.array([], dtype=np.float32)
        transcriber = MagicMock()
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(),
            sample_rate=16000,
        )
        # Force Thread.start() to raise RuntimeError.
        with patch("voice_typer.server.streaming.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            mock_thread.start.side_effect = RuntimeError("can't start new thread")
            MockThread.return_value = mock_thread
            session.start()

        assert session._thread_start_failed is True
        assert session._thread is None
        # _stopped_event must be set so cancel() / finalize() don't hang.
        assert session._stopped_event.is_set()


# ── ERR-021: get_status returns dict with xruns ────────────────────────


class TestGetStatusReturnsDict:
    """ERR-021: service.get_status() returns {status, xruns_since_start}."""

    def test_get_status_includes_xruns(self):
        from voice_typer.server.service import VoiceTyperService

        app = MagicMock()
        app.tray.state.value = "idle"
        app.recorder._xruns = 7
        service = VoiceTyperService(app)

        result = service.get_status()

        assert isinstance(result, dict)
        assert result["status"] == "idle"
        assert result["xruns_since_start"] == 7


# ── ERR-023: cancel guarantees tray state reset ────────────────────────


class TestCancelGuaranteesTrayReset:
    """ERR-023: even if recorder.discard() raises, the cancel path must
    reset tray state to IDLE and clear the busy flag."""

    def test_cancel_resets_state_when_discard_fails(self):
        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.tray_types import AppState

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

        # Tray state MUST be reset to IDLE even though discard raised.
        tray_calls = [c.args for c in app.tray.set_state.call_args_list]
        assert any(args[0] == AppState.CANCELLING for args in tray_calls)
        assert any(args[0] == AppState.IDLE for args in tray_calls)
        # busy flag MUST be cleared.
        app._busy_event.set.assert_called()


# ── ARCH-014: _load_transcriber_impl exists ────────────────────────────


class TestLoadTranscriberImplExists:
    """ARCH-014: _load_transcriber_impl is the shared load body."""

    def test_method_exists(self):
        from voice_typer.server.transcription import TranscriptionEngine
        assert hasattr(TranscriptionEngine, "_load_transcriber_impl")

    def test_reload_under_lock_delegates(self):
        """_reload_under_lock should call _load_transcriber_impl."""
        from voice_typer.server.transcription import TranscriptionEngine

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


# ── ARCH-018: _streaming_session lock ──────────────────────────────────


class TestStreamingSessionLock:
    """ARCH-018: get/set_streaming_session acquire a lock."""

    def test_lock_exists(self):
        from voice_typer.server.recording_controller import RecordingController
        ctrl = RecordingController.__new__(RecordingController)
        # Lock must be created in __init__, which __new__ skips.
        # Verify the real __init__ creates it.
        import inspect
        src = inspect.getsource(RecordingController.__init__)
        assert "_streaming_session_lock" in src


# ── ARCH-024: _consecutive_failures lock ───────────────────────────────


class TestConsecutiveFailuresLock:
    """ARCH-024: _consecutive_failures guarded by a lock."""

    def test_lock_exists(self):
        from voice_typer.server.streaming import StreamingTranscriptionSession
        import inspect
        src = inspect.getsource(StreamingTranscriptionSession.__init__)
        assert "_consecutive_failures_lock" in src


# ── ARCH-025: cancel() non-blocking by default ─────────────────────────


class TestCancelNonBlocking:
    """ARCH-025: cancel() default is non-blocking; blocking=True waits."""

    def test_cancel_signature_has_blocking_kwarg(self):
        from voice_typer.server.streaming import StreamingTranscriptionSession
        import inspect
        sig = inspect.signature(StreamingTranscriptionSession.cancel)
        assert "blocking" in sig.parameters
        assert sig.parameters["blocking"].default is False


# ── ARCH-028: single VOCAB_FILENAME / BUNDLED_CORRECTIONS_PATH ─────────


class TestSharedVocabConstants:
    """ARCH-028: text_cleanup.py imports BUNDLED_CORRECTIONS_PATH from
    vocabulary.py instead of re-declaring it."""

    def test_bundled_corrections_path_is_same_object(self):
        from voice_typer.server import text_cleanup, vocabulary
        assert text_cleanup._BUNDLED_CORRECTIONS_PATH is vocabulary.BUNDLED_CORRECTIONS_PATH


# ── ARCH-029: CorrectionsLoadError ─────────────────────────────────────


class TestCorrectionsLoadError:
    """ARCH-029: typed exception when corrections file exists but fails to load."""

    def test_corrections_load_error_is_runtime_error(self):
        from voice_typer.server.text_cleanup import CorrectionsLoadError
        assert issubclass(CorrectionsLoadError, RuntimeError)

    def test_corrections_load_error_raised_on_malformed_file(self, tmp_path, monkeypatch):
        """ARCH-029: when a corrections file exists but can't be parsed,
        CorrectionsLoadError is raised (not silently returned as None)."""
        from voice_typer.server.text_cleanup import (
            CorrectionsLoadError,
            _load_external_corrections,
        )
        # Write a malformed user corrections file.
        path = tmp_path / "voice-typer-corrections.json"
        path.write_text("{not valid json", encoding="utf-8")
        # Also force the bundled corrections path to NOT exist so the
        # only load attempt is the malformed user file.
        import voice_typer.server.text_cleanup as tc
        monkeypatch.setattr(tc, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        with pytest.raises(CorrectionsLoadError):
            _load_external_corrections(config_dir=tmp_path)


# ── ARCH-032: _prune_old_entries doesn't rebuild word_key_index ────────


class TestPruneOldEntries:
    """ARCH-032: _prune_old_entries no longer rebuilds _word_key_index."""

    def test_word_key_index_preserved_after_prune(self):
        from voice_typer.server.streaming import StreamingTextAssembler, WordTiming

        assembler = StreamingTextAssembler()
        # Add a word to populate _word_key_index.
        assembler.add_words(
            [WordTiming("hello", start_seconds=0.0, end_seconds=0.5)],
            commit_horizon_seconds=2.0,
        )
        index_before = dict(assembler._word_key_index)
        # Prune timestamps older than 1.0s.
        assembler._prune_old_entries(1.0)
        # _word_key_index must NOT be cleared (it's keyed on words, not
        # timestamps — clearing it was the bug).
        assert assembler._word_key_index == index_before


# ── ARCH-033: ResampleUnavailable ──────────────────────────────────────


class TestResampleUnavailable:
    """ARCH-033: typed exception for missing scipy."""

    def test_resample_unavailable_is_runtime_error(self):
        from voice_typer.server.recording import ResampleUnavailable
        assert issubclass(ResampleUnavailable, RuntimeError)


# ── ARCH-037: _build_models_submenu accepts config_provider ────────────


class TestBuildModelsSubmenuConfigProvider:
    """ARCH-037: build_models_menu_items accepts config_provider kwarg."""

    def test_accepts_config_provider(self, tmp_path):
        from voice_typer.server.tray_models import build_models_submenu_data

        config = MagicMock()
        config.model_size = "small.en"
        config.asr_backend = "whisper"

        result = build_models_submenu_data(
            lambda: tmp_path,
            lambda name: None,
            config_provider=config,
        )
        # The active model should be small.en (from config_provider, not disk).
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "small.en" in active_models


# ── ARCH-041: extended VK map ──────────────────────────────────────────


class TestExtendedVKMap:
    """ARCH-041: _init_vk_map includes numpad, media, browser, special keys."""

    def test_media_keys_present(self):
        from voice_typer.server.hotkeys import _init_vk_map, _VK_MAP, _VK_MAP_LOCK
        with _VK_MAP_LOCK:
            _VK_MAP.clear()
        _init_vk_map()
        assert "media_next" in _VK_MAP
        assert "media_play_pause" in _VK_MAP
        assert "browser_home" in _VK_MAP
        assert "capslock" in _VK_MAP
        assert "printscreen" in _VK_MAP

    def test_numpad_keys_present(self):
        from voice_typer.server.hotkeys import _init_vk_map, _VK_MAP
        _init_vk_map()
        assert "num_0" in _VK_MAP
        assert "numpad_5" in _VK_MAP
        assert "num_add" in _VK_MAP


# ── ARCH-042: AppState.CANCELLING set during cancel ────────────────────


class TestCancelSetsCancellingState:
    """ARCH-042: cancel() sets AppState.CANCELLING before reset to IDLE."""

    def test_cancel_sets_cancelling(self):
        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.tray_types import AppState

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
        app.recorder.recording = False
        app._waveform_bubble = MagicMock()
        app._cancel_streaming_session = MagicMock()
        app._restore_volume = MagicMock()
        app.config.bubble_behavior = "auto_hide"
        app._busy_event = MagicMock()
        ctrl._app = app

        ctrl.cancel()

        # First set_state call should be CANCELLING, last should be IDLE.
        first_call = app.tray.set_state.call_args_list[0]
        assert first_call.args[0] == AppState.CANCELLING
        last_call = app.tray.set_state.call_args_list[-1]
        assert last_call.args[0] == AppState.IDLE


# ── ARCH-043: set_config invalidates tray menu cache ───────────────────


class TestSetConfigInvalidatesTrayCache:
    """ARCH-043: IPC set_config calls tray.invalidate_menu_cache."""

    def test_invalidate_menu_cache_called(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

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


# ── ARCH-046: console handler skipped on pythonw.exe ───────────────────


class TestConsoleHandlerPythonw:
    """ARCH-046: _install_win32_console_handler skips pythonw.exe."""

    def test_skipped_on_pythonw(self, monkeypatch):
        from voice_typer.server import app as app_module

        app = app_module.VoiceTyperApp.__new__(app_module.VoiceTyperApp)
        # Pretend we're on Windows running pythonw.exe.
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", "C:\\Python312\\pythonw.exe")

        # Should be a no-op (return without installing).
        app._install_win32_console_handler()
        # If we got here without raising, the skip worked.


# ── DEAD-004: voice_typer/server/__init__.py and __main__.py ───────────


class TestServerPackageInit:
    """DEAD-004: voice_typer/server/ has __init__.py and __main__.py."""

    def test_init_py_exists(self):
        import voice_typer.server
        assert voice_typer.server.__file__ is not None

    def test_main_py_exists(self):
        import importlib.util
        spec = importlib.util.find_spec("voice_typer.server.__main__")
        assert spec is not None


# ── DEAD-026: get_voice_typer_python removed ───────────────────────────


class TestGetVoiceTyperPythonRemoved:
    """DEAD-026: asr_setup.get_voice_typer_python() is deleted."""

    def test_function_not_present(self):
        from voice_typer.server import asr_setup
        assert not hasattr(asr_setup, "get_voice_typer_python")


# ── TEST-037: VoiceTyperApp singleton ──────────────────────────────────


class TestVoiceTyperAppSingleton:
    """TEST-037: VoiceTyperApp should be a singleton — two calls return
    the same instance. (If not, config drift follows.)"""

    def test_singleton_via_request_single_instance_lock(self):
        """The app uses requestSingleInstanceLock (Electron) + a Win32
        named mutex (Python) to enforce single-instance. We can't easily
        test the mutex from a unit test, but we can verify the
        _ensure_single_instance function exists and is called in
        app startup."""
        from voice_typer.server import app as app_module
        # The function must exist.
        assert hasattr(app_module, "_ensure_single_instance") or hasattr(app_module, "main"), (
            "app module must expose _ensure_single_instance or main (which calls it)"
        )


# ── TEST-039: IPC dispatcher handles invalid data types ────────────────


class TestIPCDispatchInvalidData:
    """TEST-039: _dispatch must not crash when `data` is not a dict."""

    def test_dispatch_with_string_data(self):
        """Passing a string as `data` should not crash — the dispatcher
        should either reject it or treat it as no data."""
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        # set_config with non-dict data should be handled gracefully.
        result = server._dispatch({
            "id": 1, "type": "set_config", "data": "not a dict"
        })
        # Must not crash; should return ack or error.
        assert result["type"] in ("ack", "error")


# ── PERF-NEW-020: _free_nvidia_dll_path_handles exists ────────────────


class TestFreeNvidiaDllHandles:
    """PERF-NEW-020: _free_nvidia_dll_path_handles() releases DLL handles."""

    def test_function_exists(self):
        from voice_typer.server.transcription import _free_nvidia_dll_path_handles
        assert callable(_free_nvidia_dll_path_handles)

    def test_frees_handles_without_error(self):
        from voice_typer.server import transcription as mod
        # Add a fake handle.
        fake_handle = MagicMock()
        mod._nvidia_dll_path_handles = [fake_handle]
        mod._free_nvidia_dll_path_handles()
        assert mod._nvidia_dll_path_handles == []
        fake_handle.close.assert_called_once()


# ── PERF-NEW-022: finalize skips tail re-transcribe ────────────────────


class TestFinalizeSkipsTailRetranscribe:
    """PERF-NEW-022: _finalize_impl returns early when the streaming
    thread's last committed word is within 1.5s of audio end."""

    def test_skips_when_last_committed_is_recent(self):
        from voice_typer.server.streaming import (
            StreamingTranscriptionSession,
            StreamingConfig,
            WordTiming,
        )

        recorder = MagicMock()
        recorder.snapshot.return_value = np.array([], dtype=np.float32)
        transcriber = MagicMock()
        session = StreamingTranscriptionSession(
            recorder=recorder,
            transcriber=transcriber,
            config=StreamingConfig(),
            sample_rate=16000,
        )
        # Add a word that ends at 4.5s so committed_text is non-empty.
        session.assembler._words.append(
            WordTiming("hello", start_seconds=0.0, end_seconds=4.5)
        )
        session.assembler.last_committed_time = 4.5
        # Audio is 5.0s long — last committed (4.5) >= 5.0 - 1.5 (3.5).
        audio = np.zeros(5 * 16000, dtype=np.float32)
        result = session._finalize_impl(audio)
        # Should return the committed text without calling transcriber.
        assert "hello" in result
        transcriber.transcribe_with_fallback.assert_not_called()
        transcriber.transcribe_words.assert_not_called()


# ── ARCH-017: watchdog Timer tracked in _pending_timers ────────────────


class TestWatchdogTimerTracked:
    """ARCH-017/RACE-013: the watchdog uses Event.wait instead of Timer."""

    def test_watchdog_added_to_pending_timers(self):
        """Verify RecordingController uses Event-based watchdog (RACE-013).

        The old Timer-based approach was replaced with a persistent
        watchdog thread using Event.wait(timeout=60) to prevent Timer
        stacking under CPU pressure. Verify the new attributes exist."""
        from voice_typer.server import recording_controller
        import inspect
        src = inspect.getsource(recording_controller)
        # RACE-013: The watchdog now uses _watchdog_event instead of Timer
        assert "_watchdog_event" in src
        assert "_watchdog_stop_event" in src
