"""Regression tests for AP-13 / AP-14: the cancel and auto-stop paths"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from voice_typer.server.level_monitor import test_recording as _tr
from voice_typer.server.level_monitor._state import _state


def _populate_test_chunks() -> None:
    """Populate the three test-chunk deques with fake np.ndarray data."""
    chunk = np.zeros(512, dtype=np.float32)
    _state._test_chunks.append(chunk)
    _state._test_raw_chunks.append(chunk.copy())
    _state._test_filtered_chunks.append(chunk.copy())


def _reset_state() -> None:
    """Reset the slice of ``_state`` these tests mutate."""
    _state._test_mode = False
    _state._test_chunks.clear()
    _state._test_raw_chunks.clear()
    _state._test_filtered_chunks.clear()
    _state._test_auto_stop_timer = None
    _state._test_duration = 5.0


@pytest.fixture(autouse=True)
def _isolate_state():
    _reset_state()
    yield
    _reset_state()


class TestCancelTestLockedSecureClears:
    """AP-13: ``_cancel_test_locked`` must call ``_secure_clear_test_chunks``"""

    def test_cancel_calls_secure_clear_before_clear(self):
        """When a test is active and chunks are populated, the cancel"""
        _state._test_mode = True
        _populate_test_chunks()

        with patch.object(_tr, "_secure_clear_test_chunks") as spy:
            result = _tr._cancel_test_locked()

        # Function reports the test was active.
        assert result is True, "AP-13: _cancel_test_locked should return True when a test was active."
        # The helper MUST have been called exactly once.
        assert spy.called, (
            "AP-13: _cancel_test_locked did NOT call "
            "_secure_clear_test_chunks, np.ndarray voice buffers were "
            "cleared via .clear() without being zeroed first (regression "
            "of the privacy fix; the sibling stop_test_recording correctly "
            "calls the helper first)."
        )
        assert spy.call_count == 1, f"AP-13: expected exactly 1 _secure_clear_test_chunks call; got {spy.call_count}."
        args, _ = spy.call_args
        assert len(args) == 3, f"AP-13: _secure_clear_test_chunks expected 3 deque args; got {len(args)}."
        assert args[0] is _state._test_raw_chunks, (
            "AP-13: first arg to _secure_clear_test_chunks must be _state._test_raw_chunks."
        )
        assert args[1] is _state._test_filtered_chunks, (
            "AP-13: second arg to _secure_clear_test_chunks must be _state._test_filtered_chunks."
        )
        assert args[2] is _state._test_chunks, (
            "AP-13: third arg to _secure_clear_test_chunks must be _state._test_chunks."
        )
        # Deques must end up empty after the cancel.
        assert len(_state._test_chunks) == 0
        assert len(_state._test_raw_chunks) == 0
        assert len(_state._test_filtered_chunks) == 0
        # Test mode must be cleared.
        assert _state._test_mode is False

    def test_cancel_noop_when_nothing_active(self):
        """When nothing is active and no chunks remain, the function"""
        _state._test_mode = False

        with patch.object(_tr, "_secure_clear_test_chunks") as spy:
            result = _tr._cancel_test_locked()

        assert result is False
        assert not spy.called, (
            "AP-13: _cancel_test_locked called _secure_clear_test_chunks "
            "even though no test was active, wasteful no-op (the early "
            "return guard is supposed to skip the clear block entirely)."
        )

    def test_cancel_clears_chunks_even_when_mode_already_false(self):
        """cancel path MUST still securely clear them. The early-return"""
        _state._test_mode = False
        _populate_test_chunks()
        # Sanity: chunks are populated.
        assert len(_state._test_raw_chunks) > 0

        with patch.object(_tr, "_secure_clear_test_chunks") as spy:
            result = _tr._cancel_test_locked()

        assert result is False
        assert spy.called, (
            "AP-13: _cancel_test_locked did NOT call "
            "_secure_clear_test_chunks when chunks were still "
            "populated, leftover voice buffers would not be zeroed "
            "before being released."
        )
        assert len(_state._test_raw_chunks) == 0
        assert len(_state._test_filtered_chunks) == 0
        assert len(_state._test_chunks) == 0


class TestDoAutoStopTestSecureClears:
    """AP-14 / T-1: ``_do_auto_stop_test`` publishes the completion"""

    def test_auto_stop_publishes_and_keeps_chunks_for_retrieval(self):
        """function MUST publish the ``microphone_test_complete`` event"""
        _state._test_mode = True
        _populate_test_chunks()

        import voice_typer.server.event_bus as event_bus

        with patch.object(_tr, "_secure_clear_test_chunks") as spy, patch.object(event_bus, "publish") as pub:
            _tr._do_auto_stop_test()

        # Push event must have been published (frontend depends on it).
        assert pub.called, "T-1: _do_auto_stop_test did not publish microphone_test_complete event."
        # Inspect the published event envelope.
        pub_args, _ = pub.call_args
        published_event = pub_args[0]
        assert published_event["type"] == "microphone_test_complete", (
            f"T-1: expected event type 'microphone_test_complete'; got {published_event.get('type')!r}."
        )
        # T-1: auto-stop must NOT clear the chunks, the frontend
        assert not spy.called, (
            "T-1: _do_auto_stop_test called _secure_clear_test_chunks, "
            "clearing on auto-stop breaks the frontend's audio "
            "retrieval via stop_test_recording (eee02942 reversed the "
            "original AP-14 clear-on-auto-stop behavior)."
        )
        # Chunks remain available for retrieval (bounded by maxlen).
        assert len(_state._test_chunks) == 1
        assert len(_state._test_raw_chunks) == 1
        assert len(_state._test_filtered_chunks) == 1
        # Test mode must be cleared (happens in the first lock block).
        assert _state._test_mode is False

    def test_auto_stop_publish_failure_does_not_raise(self):
        """If ``event_bus.publish`` raises, the function must swallow it"""
        _state._test_mode = True
        _populate_test_chunks()

        import voice_typer.server.event_bus as event_bus

        with (
            patch.object(_tr, "_secure_clear_test_chunks") as spy,
            patch.object(
                event_bus,
                "publish",
                side_effect=RuntimeError("simulated IPC failure"),
            ),
        ):
            # Must not re-raise: the publish is wrapped in try/except.
            _tr._do_auto_stop_test()

        # The publish failure must NOT trigger a clear (T-1), the
        assert not spy.called, (
            "T-1: _do_auto_stop_test cleared chunks after a publish "
            "failure, chunks must remain for the frontend retrieval."
        )
        assert len(_state._test_raw_chunks) == 1
        assert len(_state._test_filtered_chunks) == 1
        assert len(_state._test_chunks) == 1

    def test_auto_stop_short_circuits_when_inactive(self):
        """If ``_test_mode`` is already False (e.g. the timer fired"""
        _state._test_mode = False

        with patch.object(_tr, "_secure_clear_test_chunks") as spy:
            _tr._do_auto_stop_test()

        assert not spy.called, (
            "AP-14: _do_auto_stop_test called _secure_clear_test_chunks "
            "even though _test_mode was already False, the early return "
            "guard is supposed to skip the publish + secure-clear block."
        )
