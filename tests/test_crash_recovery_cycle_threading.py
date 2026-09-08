"""Regression tests for cycle-id threading and the
CancellationGuard privacy gate.

``CrashRecovery.add()`` accepts a ``cycle_id`` and the
``.dictation-in-flight`` sentinel persists one, but NONE of the four
production call sites passed it — so ``_detect_and_notify_lost_dictation``
could never match a saved entry and ``recoverable`` was always ``False``
in production (the detection-side tests masked this by passing
``cycle_id=`` manually). These tests go through the PRODUCTION callers
(``DictationPipeline._store_result`` and ``CancellationGuard.run``) and
pin that the writes carry the cycle id.

The CancellationGuard's crash-recovery write was the ONLY path
not gated on ``config.crash_recovery_enabled`` - a user who turned the
privacy opt-out OFF still had ESC-cancelled / watchdog-aborted text
persisted. These tests pin the gate.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.dictation_stages import CancellationGuard, _PipelineAbortCancelled

from tests.fixtures.dictation_pipeline_helpers import make_test_app, new_pipeline


class TestCycleIdThreading:
    """Production crash-recovery writes must carry the cycle id."""

    def test_store_result_passes_cycle_id(self):
        """The stage-10 store path (StorageStep) writes with ``cycle_id``.

        Regression: previously ``add(text, pasted=False)`` with no cycle
        id — the entry was anonymous and could never match a crash
        sentinel, so ``recoverable`` was always False in production.
        """
        app = make_test_app()
        app.config.crash_recovery_enabled = True
        # Skip the history + hash-log branches so the assertion targets
        # exactly the crash-recovery write.
        app.config.history_enabled = False
        app.config.log_transcriptions = False
        pipeline = new_pipeline(app)  # _cycle_id = "test-cycle"
        pipeline._store_result("hello world")
        app._crash_recovery.add.assert_called_once_with("hello world", pasted=False, cycle_id="test-cycle")

    def test_cancellation_guard_passes_cycle_id(self):
        """The CancellationGuard's cancelled-cycle write carries the cycle id.

        Regression: the guard wrote ``add(text, pasted=False)`` with no
        correlation — same anonymous-entry problem as the other sites.
        """
        app = make_test_app()
        app.config.crash_recovery_enabled = True
        app.recording._cancelled_cycle_ids = {"test-cycle"}
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        # The guard now reaches the pipeline via ctx.pipeline
        # (the documented stage contract) for the folded bubble teardown.
        pipeline = new_pipeline(app)
        ctx = SimpleNamespace(app=app, cycle_id="test-cycle", pipeline=pipeline)
        guard = CancellationGuard(wrapped=MagicMock())
        with pytest.raises(_PipelineAbortCancelled):
            guard.run("late text", ctx)
        app._crash_recovery.add.assert_called_once_with("late text", pasted=False, cycle_id="test-cycle")


class TestCancellationGuardPrivacyGate:
    """The guard's recovery write must respect crash_recovery_enabled."""

    def test_disabled_does_not_write(self):
        """Opt-out holds on the guard path — no entry persisted when OFF."""
        app = make_test_app()
        app.config.crash_recovery_enabled = False
        app.recording._cancelled_cycle_ids = {"test-cycle"}
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        pipeline = new_pipeline(app)
        ctx = SimpleNamespace(app=app, cycle_id="test-cycle", pipeline=pipeline)
        guard = CancellationGuard(wrapped=MagicMock())
        with pytest.raises(_PipelineAbortCancelled):
            guard.run("late text", ctx)
        app._crash_recovery.add.assert_not_called()

    def test_enabled_still_writes(self):
        """Opt-in keeps working: the guard persists the late text when ON."""
        app = make_test_app()
        app.config.crash_recovery_enabled = True
        app.recording._cancelled_cycle_ids = {"test-cycle"}
        app.recording._cancelled_cycle_ids_lock = threading.Lock()
        pipeline = new_pipeline(app)
        ctx = SimpleNamespace(app=app, cycle_id="test-cycle", pipeline=pipeline)
        guard = CancellationGuard(wrapped=MagicMock())
        with pytest.raises(_PipelineAbortCancelled):
            guard.run("late text", ctx)
        app._crash_recovery.add.assert_called_once_with("late text", pasted=False, cycle_id="test-cycle")
        # The guard's bubble teardown is folded into the shared
        # ``_hide_or_idle_bubble`` - the pipeline's helper runs (the
        # fixture app is not always_visible → hide()).
        app._waveform_bubble.hide.assert_called_once()
