"""Regression tests for the 7 cleanup helpers extracted"""

from __future__ import annotations

import contextlib
import logging
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.dictation_pipeline import DictationPipeline
from voice_typer.server.dictation_stages import (
    _PipelineAbortCancelled,
    _PipelineAbortEmpty,
)

# ─── Test helpers (mirrors test_dictation_pipeline_finally_logging) ─


class _TestApp:
    """Minimal non-magic test app for orchestrator helper tests."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self.tray.notify = MagicMock()
        self.config = MagicMock()
        self.config.bubble_behavior = "show_on_record"
        self.config.crash_recovery_enabled = False
        self.config.templates_enabled = True
        self.config.log_transcriptions = False
        self.config.model_size = "tiny.en"
        self.config.device = "cpu"
        self.config.llm_polish = False
        self.config.llm_api_key = ""
        self.config.llm_polish_consent = False
        self.config.llm_api_url = ""
        self.config.llm_model = ""
        self.config.llm_preset = "professional"
        self.history_db = MagicMock()
        self._vocabulary_manager: object = None
        self._template_manager: object = None
        self._llm_polisher: object = None
        self._crash_recovery = MagicMock()
        self._last_transcription: object = None
        self.models = MagicMock()
        # ``recording`` is a MagicMock, tests that need real lock
        self.recording = MagicMock()
        # ``recorder.recording`` is read by the streaming-session
        self.recorder = MagicMock()
        self.recorder.recording = False
        self._busy_event = MagicMock()
        self._schedule_timer = MagicMock()
        self._waveform_bubble = MagicMock()
        self._lock = MagicMock()
        self._lock.__enter__ = MagicMock(return_value=self._lock)
        self._lock.__exit__ = MagicMock(return_value=False)

    def __getattr__(self, name: str) -> MagicMock:
        # Auto-mock unknown attributes (like MagicMock) but DO NOT
        if name in {
            "_vocab_fail_notified",
            "_template_fail_notified",
            "_history_fail_notified",
            "_crash_recovery_fail_notified",
            "_llm_consent_warned",
        }:
            raise AttributeError(name)
        mock = MagicMock()
        object.__setattr__(self, name, mock)
        return mock


def _new_pipeline(app: _TestApp) -> DictationPipeline:
    """Build a fresh DictationPipeline tied to ``app`` via ``__new__``."""
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


def _configure_recording_for_helpers(app: _TestApp) -> None:
    """
    streaming-session-cleanup helpers short-circuit cleanly.
    ``_cancelled_set.discard()`` contract.
    """
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording.pop_streaming_session = MagicMock(return_value=None)
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = None


class TestEachHelperCallableInIsolation:
    """Contract: each ``_cleanup_*`` helper can be invoked directly"""

    def test_cleanup_sentinel_unlink_callable_in_isolation(self, monkeypatch) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)

        # Sentinel file path is patched to a stub that returns False
        class _StubPath:
            def __truediv__(self, other: str) -> _StubPath:
                return self

            def exists(self) -> bool:
                return False

            def unlink(self) -> None:  # pragma: no cover, not reached
                raise AssertionError("should not be called when exists() is False")

        monkeypatch.setattr("voice_typer.server._paths.config_dir", lambda: _StubPath())
        # Should not raise.
        assert pipeline._cleanup_sentinel_unlink() is None

    def test_cleanup_audio_zero_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        # _audio is None by default, helper must short-circuit cleanly.
        assert pipeline._cleanup_audio_zero() is None
        assert pipeline._audio is None

    def test_cleanup_audio_zero_zeros_real_ndarray(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        # Set a real ndarray so the fill(0) branch runs.
        arr = np.ones(64, dtype=np.float32)
        pipeline._audio = arr
        pipeline._cleanup_audio_zero()
        # Array must be zeroed in-place AND reference cleared.
        assert pipeline._audio is None
        assert float(arr.max()) == 0.0

    def test_cleanup_watchdog_reset_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        assert pipeline._cleanup_watchdog_reset() is None
        # The recording's reset methods must have been called.
        assert app.recording._reset_watchdog.called
        assert app.recording._stop_watchdog_thread.called

    def test_cleanup_streaming_session_cancel_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        # Default: pop_streaming_session returns None → cancel branch skipped.
        assert pipeline._cleanup_streaming_session_cancel() is None

    def test_cleanup_busyness_idle_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        assert pipeline._cleanup_busyness_idle() is None
        assert app._busyness.set_idle.called

    def test_cleanup_transcription_thread_clear_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        assert pipeline._cleanup_transcription_thread_clear() is None
        # The transcription_thread field must be cleared (set to None)
        assert app.recording._transcription_thread is None

    def test_cleanup_gc_collect_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        assert pipeline._cleanup_gc_collect() is None


class TestFailureInOneHelperDoesNotBlockNext:
    """
    Contract: a failure in cleanup helper N must NOT prevent
    This is the critical contract, a single broken cleanup must not
    """

    def test_busyness_idle_failure_does_not_block_transcription_thread_clear(self, caplog) -> None:
        """If ``_busyness.set_idle()`` raises, the next helper"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Inject a failure in the busyness-idle helper.
        app._busyness.set_idle.side_effect = RuntimeError("simulated coordinator torn down")
        pipeline = _new_pipeline(app)
        # Reset the recording._transcription_thread to a non-None sentinel
        sentinel_thread = object()
        app.recording._transcription_thread = sentinel_thread

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            pipeline._cleanup_busyness_idle()
            pipeline._cleanup_transcription_thread_clear()

        # Helper N (busyness) failed, log emitted.
        busy_fail_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step busyness idle failed" in r.getMessage()
        ]
        assert busy_fail_logs, (
            "busyness idle failure must emit the byte-identical "
            "DEBUG log line '[PIPELINE] finally cleanup step busyness idle "
            "failed' (per C-LOG-1)."
        )
        # Helper N+1 (transcription_thread) STILL ran, cleared the thread.
        assert app.recording._transcription_thread is None, (
            "a failure in _cleanup_busyness_idle must NOT prevent "
            "_cleanup_transcription_thread_clear from running. The "
            "transcription_thread field must be cleared regardless."
        )

    def test_gc_collect_failure_does_not_block_prior_helpers(self, caplog, monkeypatch) -> None:
        """If ``gc.collect(0)`` raises, the PRIOR helpers (busy_event,"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Set a real transcription_thread so the prior helper clears it.
        sentinel_thread = object()
        app.recording._transcription_thread = sentinel_thread
        pipeline = _new_pipeline(app)

        # Patch gc to raise.
        import sys

        class _BrokenGC:
            def collect(self, generation: int = 2) -> int:
                raise RuntimeError("simulated broken gc.collect")

        original_gc = sys.modules.get("gc")
        sys.modules["gc"] = _BrokenGC()
        try:
            with (
                caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
                contextlib.suppress(Exception),
            ):
                pipeline._cleanup_busyness_idle()
                pipeline._cleanup_transcription_thread_clear()
                pipeline._cleanup_gc_collect()
        finally:
            if original_gc is not None:
                sys.modules["gc"] = original_gc
            else:
                sys.modules.pop("gc", None)

        # Prior helpers ran, busy_event was set and thread was cleared.
        assert app._busyness.set_idle.called, "gc.collect failure must not block prior busyness-idle clear."
        assert app.recording._transcription_thread is None, (
            "gc.collect failure must not block prior transcription_thread clear."
        )
        gc_fail_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step gc_collect failed" in r.getMessage()
        ]
        assert gc_fail_logs, (
            "gc_collect failure must emit the byte-identical DEBUG "
            "log line '[PIPELINE] finally cleanup step gc_collect failed'."
        )

    def test_watchdog_reset_failure_does_not_block_streaming_session_cancel(self, caplog) -> None:
        """If ``recording._reset_watchdog()`` raises, the next helper"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Inject a failure in the watchdog_reset helper.
        app.recording._reset_watchdog.side_effect = RuntimeError("simulated _reset_watchdog failure")
        # Make pop_streaming_session return a mock session so we can
        mock_session = MagicMock()
        mock_session.cancel = MagicMock()
        app.recording.pop_streaming_session = MagicMock(return_value=mock_session)
        app.recorder.recording = False
        pipeline = _new_pipeline(app)

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            pipeline._cleanup_watchdog_reset()
            pipeline._cleanup_streaming_session_cancel()

        # Helper N (watchdog_reset) failed, log emitted.
        watchdog_fail_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step watchdog_reset failed" in r.getMessage()
        ]
        assert watchdog_fail_logs, (
            "watchdog_reset failure must emit the byte-identical "
            "DEBUG log line '[PIPELINE] finally cleanup step watchdog_reset "
            "failed' (per C-LOG-1)."
        )
        # Helper N+1 (streaming_session_cancel) STILL ran, pop_streaming_session
        assert app.recording.pop_streaming_session.called, (
            "a failure in _cleanup_watchdog_reset must NOT prevent "
            "_cleanup_streaming_session_cancel from calling pop_streaming_session()."
        )
        assert mock_session.cancel.called, (
            "streaming_session.cancel() must have been invoked even though the prior helper failed."
        )

    def test_sentinel_unlink_failure_does_not_block_audio_zero(self, caplog, monkeypatch) -> None:
        """If ``_sentinel.unlink()`` raises, the next helper"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        # Set a real ndarray so we can verify audio_zero ran.
        arr = np.ones(32, dtype=np.float32)
        pipeline._audio = arr

        class _FakeSentinelFile:
            def exists(self) -> bool:
                return True

            def unlink(self) -> None:
                raise OSError("simulated read-only mount unlink failure")

        class _FakeConfigDir:
            def __truediv__(self, other: str) -> _FakeSentinelFile:
                return _FakeSentinelFile()

        monkeypatch.setattr("voice_typer.server._paths.config_dir", lambda: _FakeConfigDir())

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            pipeline._cleanup_sentinel_unlink()
            pipeline._cleanup_audio_zero()

        # Helper N (sentinel_unlink) failed, log emitted.
        sentinel_fail_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step sentinel_unlink failed" in r.getMessage()
        ]
        assert sentinel_fail_logs, (
            "sentinel_unlink failure must emit the byte-identical "
            "DEBUG log line '[PIPELINE] finally cleanup step sentinel_unlink "
            "failed' (per C-LOG-1)."
        )
        # Helper N+1 (audio_zero) STILL ran, array zeroed in-place.
        assert float(arr.max()) == 0.0, (
            "a failure in _cleanup_sentinel_unlink must NOT prevent _cleanup_audio_zero from zeroing the audio array."
        )
        assert pipeline._audio is None


class TestFinallyRunsOnAbortCancelAndException:
    """Contract: the full 7-helper finally block must run on ALL"""

    def _make_stages(self, exit_kind: str) -> list:
        """Build a fake stage list that raises the requested sentinel."""
        if exit_kind == "success":

            class _NoopStage:
                name = "noop"
                timed = True

                def run(self, text: str, ctx: object) -> str:
                    return text

            return [_NoopStage()]
        if exit_kind == "abort_empty":

            class _AbortEmptyStage:
                name = "abort_empty"
                timed = True

                def run(self, text: str, ctx: object) -> str:
                    raise _PipelineAbortEmpty()

            return [_AbortEmptyStage()]
        if exit_kind == "abort_cancelled":

            class _AbortCancelledStage:
                name = "abort_cancelled"
                timed = True

                def run(self, text: str, ctx: object) -> str:
                    raise _PipelineAbortCancelled()

            return [_AbortCancelledStage()]
        if exit_kind == "device_loss":

            class _DeviceLossStage:
                name = "device_loss"
                timed = True

                def run(self, text: str, ctx: object) -> str:
                    raise RuntimeError("simulated CUDA device loss")

            return [_DeviceLossStage()]
        raise ValueError(f"unknown exit_kind={exit_kind!r}")

    def _drive_run(self, app: _TestApp, exit_kind: str) -> None:
        """Drive ``run()`` with the stub stage list for the given exit."""

        pipeline = _new_pipeline(app)
        pipeline._stages = self._make_stages(exit_kind)
        # Suppress the body's exception (we're focusing on the finally block).
        with contextlib.suppress(Exception):
            pipeline.run(
                audio=None,
                duration=0.0,
                recorded_rms=0.0,
                cycle_id="test-cycle",
            )

    def test_finally_runs_on_abort_empty(self) -> None:
        """On ``_PipelineAbortEmpty`` (EmptyCheckStage raised), the"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        self._drive_run(app, "abort_empty")
        assert app._busyness.set_idle.called, (
            "finally block must run on _PipelineAbortEmpty, "
            "_busyness.set_idle() must be called (cleanup_busyness_idle helper)."
        )

    def test_finally_runs_on_abort_cancelled(self) -> None:
        """On ``_PipelineAbortCancelled`` (CancellationGuard raised"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        self._drive_run(app, "abort_cancelled")
        assert app._busyness.set_idle.called, (
            "finally block must run on _PipelineAbortCancelled, "
            "_busyness.set_idle() must be called (cleanup_busyness_idle helper)."
        )

    def test_finally_runs_on_device_loss_exception(self) -> None:
        """On the generic ``except Exception`` path (e.g. CUDA device"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        self._drive_run(app, "device_loss")
        assert app._busyness.set_idle.called, (
            "finally block must run on generic Exception, "
            "_busyness.set_idle() must be called (cleanup_busyness_idle helper)."
        )
        assert app.recording._reset_watchdog.called, (
            "finally block must run on generic Exception, "
            "recording._reset_watchdog() must be called "
            "(cleanup_watchdog_reset helper)."
        )


class TestLogLineTextPinned:
    """C-LOG-1 / C-LOG-2 pin: the log line TEXT passed to ``log.debug``"""

    @pytest.mark.parametrize(
        ("helper_name", "expected_log_substring"),
        [
            ("_cleanup_sentinel_unlink", "[PIPELINE] finally cleanup step sentinel_unlink failed"),
            ("_cleanup_audio_zero", "[PIPELINE] finally cleanup step audio_zero failed"),
            ("_cleanup_watchdog_reset", "[PIPELINE] finally cleanup step watchdog_reset failed"),
            (
                "_cleanup_streaming_session_cancel",
                "[PIPELINE] finally cleanup step streaming_session_cancel failed",
            ),
            ("_cleanup_busyness_idle", "[PIPELINE] finally cleanup step busyness idle failed"),
            (
                "_cleanup_transcription_thread_clear",
                "[PIPELINE] finally cleanup step transcription_thread_clear_unsafe failed",
            ),
            ("_cleanup_gc_collect", "[PIPELINE] finally cleanup step gc_collect failed"),
        ],
    )
    def test_each_helper_emits_pinned_log_line(self, helper_name: str, expected_log_substring: str, caplog) -> None:
        """Pin the exact log line TEXT for each cleanup helper's"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)

        # Per-helper failure injection:
        if helper_name == "_cleanup_sentinel_unlink":

            class _FakeSentinelFile:
                def exists(self) -> bool:
                    return True

                def unlink(self) -> None:
                    raise OSError("pinned-log test: unlink fails")

            class _FakeConfigDir:
                def __truediv__(self, other: str) -> _FakeSentinelFile:
                    return _FakeSentinelFile()

            # Patch via monkeypatch would require pytest fixture; instead
            import voice_typer.server._paths as _paths_mod

            orig = _paths_mod.config_dir
            _paths_mod.config_dir = lambda: _FakeConfigDir()
            try:
                with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                    pipeline._cleanup_sentinel_unlink()
            finally:
                _paths_mod.config_dir = orig
        elif helper_name == "_cleanup_audio_zero":
            # Subclass np.ndarray and override ``fill`` to raise. Plain
            class _BrokenNdarray(np.ndarray):
                def fill(self, value: int) -> None:  # type: ignore[override]
                    raise RuntimeError("pinned-log test: fill fails")

            arr = np.zeros(8, dtype=np.float32).view(_BrokenNdarray)
            pipeline._audio = arr
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_audio_zero()
        elif helper_name == "_cleanup_watchdog_reset":
            app.recording._reset_watchdog.side_effect = RuntimeError("pinned-log test: _reset_watchdog fails")
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_watchdog_reset()
        elif helper_name == "_cleanup_streaming_session_cancel":
            app.recording.pop_streaming_session.side_effect = RuntimeError(
                "pinned-log test: pop_streaming_session fails"
            )
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_streaming_session_cancel()
            # We pin the OUTER one for this helper since pop_streaming_session
            expected_log_substring = "[TRANSCRIBE] finally: session cleanup failed"
        elif helper_name == "_cleanup_busyness_idle":
            app._busyness.set_idle.side_effect = RuntimeError("pinned-log test: set_idle fails")
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_busyness_idle()
        elif helper_name == "_cleanup_transcription_thread_clear":
            # Remove the _watchdog_lock so the AttributeError path fires.
            del app.recording._watchdog_lock
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_transcription_thread_clear()
            # NOTE: this helper has TWO log lines, outer logs
            expected_log_substring = (
                "[TRANSCRIBE] could not acquire recording._watchdog_lock "
                "to clear _transcription_thread; assigning without lock"
            )
        elif helper_name == "_cleanup_gc_collect":
            import sys

            class _BrokenGC:
                def collect(self, generation: int = 2) -> int:
                    raise RuntimeError("pinned-log test: gc.collect fails")

            original_gc = sys.modules.get("gc")
            sys.modules["gc"] = _BrokenGC()
            try:
                with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                    pipeline._cleanup_gc_collect()
            finally:
                if original_gc is not None:
                    sys.modules["gc"] = original_gc
                else:
                    sys.modules.pop("gc", None)
        else:  # pragma: no cover, defensive
            pytest.fail(f"unknown helper {helper_name!r}")

        debug_logs = [
            r for r in caplog.records if r.levelno == logging.DEBUG and expected_log_substring in r.getMessage()
        ]
        assert debug_logs, (
            f"C-LOG-1 pin: helper {helper_name!r} failure must emit a DEBUG "
            f"log line containing exactly {expected_log_substring!r}. "
            "Log line TEXT must be byte-identical to the pre-refactor inline "
            "finally block."
        )
        assert debug_logs[0].exc_info is not None, (
            f"C-LOG-1 pin: helper {helper_name!r} failure log must carry "
            "exc_info=True so operators can see the traceback."
        )

    def test_streaming_session_cancel_inner_log_pinned(self, caplog) -> None:
        """C-LOG-1 pin: when ``session.cancel()`` raises, the INNER"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        mock_session = MagicMock()
        mock_session.cancel.side_effect = RuntimeError("pinned-log test: session.cancel fails")
        app.recording.pop_streaming_session = MagicMock(return_value=mock_session)
        app.recorder.recording = False
        pipeline = _new_pipeline(app)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._cleanup_streaming_session_cancel()

        inner_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step streaming_session_cancel failed" in r.getMessage()
        ]
        assert inner_logs, (
            "C-LOG-1 pin: when session.cancel() raises, the INNER DEBUG log "
            "'[PIPELINE] finally cleanup step streaming_session_cancel failed' "
            "must fire (byte-identical to pre-refactor inline finally block)."
        )

    def test_transcription_thread_clear_inner_log_pinned(self, caplog) -> None:
        """``_transcription_thread = None`` assignment raises, the INNER"""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Remove the _watchdog_lock so the AttributeError path fires.
        del app.recording._watchdog_lock

        class _RefusesAttrWrite:
            _watchdog_lock = None  # forces the AttributeError path

            def __setattr__(self, name: str, value: object) -> None:
                # Allow setting _watchdog_lock once for the del above.
                if name == "_watchdog_lock":
                    raise AttributeError("test: cannot set _watchdog_lock")
                raise RuntimeError("pinned-log test: _transcription_thread assign fails")

        # Replace the recording attribute with our refuser.
        app.recording = _RefusesAttrWrite()
        pipeline = _new_pipeline(app)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
            pipeline._cleanup_transcription_thread_clear()

        inner_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG
            and "finally cleanup step transcription_thread_clear_unsafe failed" in r.getMessage()
        ]
        assert inner_logs, (
            "C-LOG-1 pin: when the lock is absent AND the field assignment "
            "raises, the INNER DEBUG log '[PIPELINE] finally cleanup step "
            "transcription_thread_clear_unsafe failed' must fire "
            "(byte-identical to pre-refactor inline finally block)."
        )
