"""Regression tests for the 7 cleanup helpers extracted
from ``orchestrator.run()``'s 196-line finally block.

Pre-refactor: ``DictationPipeline.run()``'s ``finally`` block inlined
7 cleanup steps (sentinel unlink, audio zero, watchdog reset,
streaming-session cancel, busy-event clear, transcription-thread
clear, gc.collect). Each step had its own nested try/except with a
``log.debug`` failure log. The 196-line finally block was the
most-mutated code path in the dictation pipeline.

Post-refactor: each cleanup step is now a named
private method (``_cleanup_<purpose>``) on ``_OrchestratorMixin``.
The finally block reads as 7 sequential calls. Each helper owns its
own try/except with byte-identical log line TEXT (per C-LOG-1) — no
behavior change, just decomposition.

These tests pin the contract:

  1. Each helper can be called in isolation (no ``run()`` context
     needed) — required so future changes to one cleanup step don't
     require re-running the entire ``run()`` to verify.
  2. A failure in helper N does NOT prevent helper N+1 from running.
     The original finally block guaranteed this via per-step
     try/except; the extracted helpers must preserve it. This is the
     critical contract — a single broken cleanup must not leak the
     busy state, the watchdog thread, or the streaming-session slot.
  3. The full finally block runs even on abort (``_PipelineAbortEmpty``),
     cancel (``_PipelineAbortCancelled``), and body Exception paths —
     the 7 helpers all fire regardless of why ``run()``'s try block
     exited.
  4. Log line TEXT is byte-identical to the pre-refactor inline blocks
     (per C-LOG-1). The format strings ``"[PIPELINE] finally cleanup
     step <name> failed"`` and ``"[TRANSCRIBE] ..."`` are pinned.
"""

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
    """Minimal non-magic test app for orchestrator helper tests.

    Mirrors the stub pattern in
    ``test_dictation_pipeline_finally_logging.py``: a custom class
    (not ``MagicMock``) so the notify-once flag attributes default
    to ``False`` via ``getattr(..., False)`` — MagicMock would
    auto-create truthy children.
    """

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
        # ``recording`` is a MagicMock — tests that need real lock
        # semantics override it via ``_configure_recording_for_helpers``.
        self.recording = MagicMock()
        # ``recorder.recording`` is read by the streaming-session
        # cleanup branch — make it False so the
        # ``if session is not None and not recorder.recording``
        # branch short-circuits when ``pop_streaming_session`` returns
        # None (the default MagicMock return).
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
        # auto-create the notify-once flag names.
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
    """Build a fresh DictationPipeline tied to ``app`` via ``__new__``.

    Mirrors how ``RecordingController._stop_impl`` constructs a new
    pipeline per transcription cycle. Bypasses ``__init__`` (which
    expects a real VoiceTyperApp) and manually sets the attributes
    the cleanup helpers read.
    """
    pipeline = DictationPipeline.__new__(DictationPipeline)
    pipeline._app = app
    pipeline._duration = 1.0
    pipeline._cycle_id = "test-cycle"
    pipeline._audio = None
    pipeline._audio_stats = None
    pipeline._recorded_rms = 0.0
    pipeline._device_info = ""
    pipeline._watchdog = None
    pipeline._last_resources_check_ts = 0.0
    pipeline._resources_check_interval = 60.0
    pipeline._templates_applied = False
    return pipeline


def _configure_recording_for_helpers(app: _TestApp) -> None:
    """Configure ``app.recording`` so the watchdog-reset and
    streaming-session-cleanup helpers short-circuit cleanly.

    Without this, the MagicMock auto-creates child mocks for
    ``_cancelled_cycle_ids`` (a set) and ``_cancelled_cycle_ids_lock``
    (a lock) that don't support the ``with _cancelled_lock:`` /
    ``_cancelled_set.discard()`` contract.
    """
    app.recording._cancelled_cycle_ids = set()
    app.recording._cancelled_cycle_ids_lock = threading.Lock()
    app.recording.pop_streaming_session = MagicMock(return_value=None)
    app.recording._reset_watchdog = MagicMock()
    app.recording._stop_watchdog_thread = MagicMock()
    app.recording._watchdog_lock = threading.Lock()
    app.recording._transcription_thread = None


# ─── 1. Each helper can be called in isolation ─────────────────────


class TestEachHelperCallableInIsolation:
    """Contract: each ``_cleanup_*`` helper can be invoked directly
    without setting up a full ``run()`` cycle. Required so a future
    change to one step's teardown logic can be unit-tested without
    spinning up the entire 11-stage pipeline.
    """

    def test_cleanup_sentinel_unlink_callable_in_isolation(self, monkeypatch) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)

        # Sentinel file path is patched to a stub that returns False
        # so the unlink branch is skipped — we just verify the helper
        # returns None without raising.
        class _StubPath:
            def __truediv__(self, other: str) -> _StubPath:
                return self

            def exists(self) -> bool:
                return False

            def unlink(self) -> None:  # pragma: no cover — not reached
                raise AssertionError("should not be called when exists() is False")

        monkeypatch.setattr("voice_typer.server._paths.config_dir", lambda: _StubPath())
        # Should not raise.
        assert pipeline._cleanup_sentinel_unlink() is None

    def test_cleanup_audio_zero_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        # _audio is None by default — helper must short-circuit cleanly.
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

    def test_cleanup_busy_event_clear_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        assert pipeline._cleanup_busy_event_clear() is None
        assert app._busy_event.set.called

    def test_cleanup_transcription_thread_clear_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        assert pipeline._cleanup_transcription_thread_clear() is None
        # The transcription_thread field must be cleared (set to None)
        # under the watchdog_lock.
        assert app.recording._transcription_thread is None

    def test_cleanup_gc_collect_callable_in_isolation(self) -> None:
        app = _TestApp()
        _configure_recording_for_helpers(app)
        pipeline = _new_pipeline(app)
        # gc.collect(0) should succeed and return without raising.
        assert pipeline._cleanup_gc_collect() is None


# ─── 2. Failure in helper N does NOT prevent helper N+1 ────────────


class TestFailureInOneHelperDoesNotBlockNext:
    """Contract: a failure in cleanup helper N must NOT prevent
    helper N+1 from running. The pre-refactor finally block guaranteed
    this via per-step try/except; the extracted helpers must preserve
    it (each helper owns its try/except).

    This is the critical contract — a single broken cleanup must not
    leak the busy state, the watchdog thread, or the streaming-session
    slot.
    """

    def test_busy_event_clear_failure_does_not_block_transcription_thread_clear(self, caplog) -> None:
        """If ``_busy_event.set()`` raises, the next helper
        (``_cleanup_transcription_thread_clear``) must STILL run and
        clear the thread reference."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Inject a failure in the busy_event helper.
        app._busy_event.set.side_effect = RuntimeError("simulated busy_event torn down")
        pipeline = _new_pipeline(app)
        # Reset the recording._transcription_thread to a non-None sentinel
        # so we can verify the helper N+1 actually cleared it.
        sentinel_thread = object()
        app.recording._transcription_thread = sentinel_thread

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            pipeline._cleanup_busy_event_clear()
            pipeline._cleanup_transcription_thread_clear()

        # Helper N (busy_event) failed — log emitted.
        busy_fail_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "finally cleanup step busy_event_clear failed" in r.getMessage()
        ]
        assert busy_fail_logs, (
            "busy_event_clear failure must emit the byte-identical "
            "DEBUG log line '[PIPELINE] finally cleanup step busy_event_clear "
            "failed' (per C-LOG-1)."
        )
        # Helper N+1 (transcription_thread) STILL ran — cleared the thread.
        assert app.recording._transcription_thread is None, (
            "a failure in _cleanup_busy_event_clear must NOT prevent "
            "_cleanup_transcription_thread_clear from running. The "
            "transcription_thread field must be cleared regardless."
        )

    def test_gc_collect_failure_does_not_block_prior_helpers(self, caplog, monkeypatch) -> None:
        """If ``gc.collect(0)`` raises, the PRIOR helpers (busy_event,
        transcription_thread) must have already run. We verify this
        by checking side-effects of the prior helpers."""
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
                pipeline._cleanup_busy_event_clear()
                pipeline._cleanup_transcription_thread_clear()
                pipeline._cleanup_gc_collect()
        finally:
            if original_gc is not None:
                sys.modules["gc"] = original_gc
            else:
                sys.modules.pop("gc", None)

        # Prior helpers ran — busy_event was set and thread was cleared.
        assert app._busy_event.set.called, "gc.collect failure must not block prior busy_event clear."
        assert app.recording._transcription_thread is None, (
            "gc.collect failure must not block prior transcription_thread clear."
        )
        # gc.collect helper logged its failure.
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
        """If ``recording._reset_watchdog()`` raises, the next helper
        (``_cleanup_streaming_session_cancel``) must STILL run."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Inject a failure in the watchdog_reset helper.
        app.recording._reset_watchdog.side_effect = RuntimeError("simulated _reset_watchdog failure")
        # Make pop_streaming_session return a mock session so we can
        # verify the streaming helper ran and tried to cancel it.
        mock_session = MagicMock()
        mock_session.cancel = MagicMock()
        app.recording.pop_streaming_session = MagicMock(return_value=mock_session)
        # recorder.recording must be False so the cancel branch fires.
        app.recorder.recording = False
        pipeline = _new_pipeline(app)

        with (
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"),
            contextlib.suppress(Exception),
        ):
            pipeline._cleanup_watchdog_reset()
            pipeline._cleanup_streaming_session_cancel()

        # Helper N (watchdog_reset) failed — log emitted.
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
        # Helper N+1 (streaming_session_cancel) STILL ran — pop_streaming_session
        # was called and session.cancel() was invoked.
        assert app.recording.pop_streaming_session.called, (
            "a failure in _cleanup_watchdog_reset must NOT prevent "
            "_cleanup_streaming_session_cancel from calling pop_streaming_session()."
        )
        assert mock_session.cancel.called, (
            "streaming_session.cancel() must have been invoked even though the prior helper failed."
        )

    def test_sentinel_unlink_failure_does_not_block_audio_zero(self, caplog, monkeypatch) -> None:
        """If ``_sentinel.unlink()`` raises, the next helper
        (``_cleanup_audio_zero``) must STILL run and zero the audio."""
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

        # Helper N (sentinel_unlink) failed — log emitted.
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
        # Helper N+1 (audio_zero) STILL ran — array zeroed in-place.
        assert float(arr.max()) == 0.0, (
            "a failure in _cleanup_sentinel_unlink must NOT prevent _cleanup_audio_zero from zeroing the audio array."
        )
        assert pipeline._audio is None


# ─── 3. Full finally block runs on abort / cancel / device-loss ─────


class TestFinallyRunsOnAbortCancelAndException:
    """Contract: the full 7-helper finally block must run on ALL
    three exit paths from ``run()``'s try block —
    ``_PipelineAbortEmpty`` (no speech detected),
    ``_PipelineAbortCancelled`` (user cancelled mid-paste), and the
    generic ``except Exception`` path (e.g. device loss / CUDA OOM).

    We can't easily trigger the abort sentinels without a real
    ``run()`` call, so we drive ``run()`` end-to-end with a stubbed
    stage list and verify the helpers' side-effects fire on each exit
    path.
    """

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
                watchdog=None,
            )

    def test_finally_runs_on_abort_empty(self) -> None:
        """On ``_PipelineAbortEmpty`` (EmptyCheckStage raised), the
        finally block must still clear the busy_event."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        self._drive_run(app, "abort_empty")
        assert app._busy_event.set.called, (
            "finally block must run on _PipelineAbortEmpty — "
            "busy_event.set() must be called (cleanup_busy_event_clear helper)."
        )

    def test_finally_runs_on_abort_cancelled(self) -> None:
        """On ``_PipelineAbortCancelled`` (CancellationGuard raised
        during paste), the finally block must still clear the
        busy_event."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        self._drive_run(app, "abort_cancelled")
        assert app._busy_event.set.called, (
            "finally block must run on _PipelineAbortCancelled — "
            "busy_event.set() must be called (cleanup_busy_event_clear helper)."
        )

    def test_finally_runs_on_device_loss_exception(self) -> None:
        """On the generic ``except Exception`` path (e.g. CUDA device
        loss), the finally block must still clear the busy_event AND
        reset the watchdog."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        self._drive_run(app, "device_loss")
        assert app._busy_event.set.called, (
            "finally block must run on generic Exception — "
            "busy_event.set() must be called (cleanup_busy_event_clear helper)."
        )
        assert app.recording._reset_watchdog.called, (
            "finally block must run on generic Exception — "
            "recording._reset_watchdog() must be called "
            "(cleanup_watchdog_reset helper)."
        )


# ─── 4. Log line TEXT byte-identical (C-LOG-1 pin) ─────────────────


class TestLogLineTextPinned:
    """C-LOG-1 / C-LOG-2 pin: the log line TEXT passed to ``log.debug``
    in each cleanup helper must be BYTE-IDENTICAL to the pre-refactor
    inline finally block. We pin the exact strings here so a future
    edit that subtly rewords the log line (e.g. "step" → "stage",
    adding a trailing period) will fail this test.
    """

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
            ("_cleanup_busy_event_clear", "[PIPELINE] finally cleanup step busy_event_clear failed"),
            (
                "_cleanup_transcription_thread_clear",
                "[PIPELINE] finally cleanup step transcription_thread_clear_unsafe failed",
            ),
            ("_cleanup_gc_collect", "[PIPELINE] finally cleanup step gc_collect failed"),
        ],
    )
    def test_each_helper_emits_pinned_log_line(self, helper_name: str, expected_log_substring: str, caplog) -> None:
        """Pin the exact log line TEXT for each cleanup helper's
        failure log. C-LOG-1 — byte-identical to pre-refactor."""
        # We trigger each helper's failure path differently — the
        # simplest unified approach is to monkeypatch the helper's
        # internals to raise. For most helpers, this means stubbing
        # the relevant app attribute.
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
            # we patch the module attribute directly using a try/finally.
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
            # monkeypatching of ``arr.fill`` is rejected by numpy (the
            # method slot is read-only on the C-side). A subclass view
            # passes the ``isinstance(self._audio, np.ndarray)`` check
            # while still allowing Python-side method override.
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
            # pop_streaming_session raises → outer except logs
            # "[TRANSCRIBE] finally: session cleanup failed".
            app.recording.pop_streaming_session.side_effect = RuntimeError(
                "pinned-log test: pop_streaming_session fails"
            )
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_streaming_session_cancel()
            # NOTE: this helper has TWO log lines — outer except logs
            # "[TRANSCRIBE] finally: session cleanup failed" and inner
            # except logs "[PIPELINE] ... streaming_session_cancel failed".
            # We pin the OUTER one for this helper since pop_streaming_session
            # is the outer call. The parametrize substring for this helper
            # is the INNER one — to exercise the inner, we'd need a session
            # whose .cancel() raises. We test the inner separately below.
            expected_log_substring = "[TRANSCRIBE] finally: session cleanup failed"
        elif helper_name == "_cleanup_busy_event_clear":
            app._busy_event.set.side_effect = RuntimeError("pinned-log test: busy_event.set fails")
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_busy_event_clear()
        elif helper_name == "_cleanup_transcription_thread_clear":
            # Remove the _watchdog_lock so the AttributeError path fires.
            del app.recording._watchdog_lock
            with caplog.at_level(logging.DEBUG, logger="voice_typer.server.dictation_pipeline"):
                pipeline._cleanup_transcription_thread_clear()
            # NOTE: this helper has TWO log lines — outer logs
            # "[TRANSCRIBE] could not acquire recording._watchdog_lock..."
            # and inner logs "[PIPELINE] ... transcription_thread_clear_unsafe failed".
            # When the lock is absent, the inner branch ALSO needs to fail
            # to fire. We test the OUTER log line here; the inner is tested
            # separately below.
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
        else:  # pragma: no cover — defensive
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
        # exc_info=True must be attached so operators can see the traceback.
        assert debug_logs[0].exc_info is not None, (
            f"C-LOG-1 pin: helper {helper_name!r} failure log must carry "
            "exc_info=True so operators can see the traceback."
        )

    def test_streaming_session_cancel_inner_log_pinned(self, caplog) -> None:
        """C-LOG-1 pin: when ``session.cancel()`` raises, the INNER
        ``log.debug("[PIPELINE] finally cleanup step "
        "streaming_session_cancel failed", exc_info=True)`` must fire
        — distinct from the OUTER ``[TRANSCRIBE] finally: session
        cleanup failed`` log."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # pop_streaming_session succeeds (returns a session whose
        # .cancel() raises).
        mock_session = MagicMock()
        mock_session.cancel.side_effect = RuntimeError("pinned-log test: session.cancel fails")
        app.recording.pop_streaming_session = MagicMock(return_value=mock_session)
        # recorder.recording must be False so the cancel branch fires.
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
        """C-LOG-1 pin: when the lock is absent AND the
        ``_transcription_thread = None`` assignment raises, the INNER
        ``log.debug("[PIPELINE] finally cleanup step "
        "transcription_thread_clear_unsafe failed", exc_info=True)``
        must fire — distinct from the OUTER ``[TRANSCRIBE] could not
        acquire recording._watchdog_lock...`` log."""
        app = _TestApp()
        _configure_recording_for_helpers(app)
        # Remove the _watchdog_lock so the AttributeError path fires.
        del app.recording._watchdog_lock

        # Make the assignment raise by replacing _recording with a
        # MagicMock whose attribute setattr fails.
        # Simpler: replace _recording with None so the inner
        # ``if _recording is not None`` branch short-circuits (helper
        # completes without firing the inner log). To exercise the
        # inner log, we need _recording to be non-None AND the
        # attribute assignment to raise. We use a class that raises
        # on attribute delete/set.
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
