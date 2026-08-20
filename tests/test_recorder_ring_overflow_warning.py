"""regression tests: real-time ring-overflow WARNING from the
audio worker thread.

``Recorder._audio_callback_dispatch`` (the RT PortAudio callback)
increments ``_dropped_ring_chunks`` when the SPSC ring buffer is full
(worker thread cannot keep up). Pre-fix, that increment was silent
during the recording — the counter was only surfaced AFTER ``stop()``
by ``RecordingController._stop_impl`` (too late for the user to react
by closing background apps or switching to a lighter filter chain).

The fix adds ``Recorder._surface_ring_overflow_warning`` (called once
per chunk from ``Recorder._process_audio_chunk`` on the worker thread).
It computes the delta between consecutive checks and emits a
rate-limited WARNING (one per ``_RING_OVERFLOW_WARN_INTERVAL_S``)
when the counter increases.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def recorder():
    """Construct a real ``Recorder`` instance with a mock config.

    The recorder is never ``start()``-ed — the tests exercise
    ``_surface_ring_overflow_warning`` (and ``_process_audio_chunk``'s
    delegation) directly. ``MagicMock(sample_rate=..., microphone=None)``
    mirrors the pattern in ``test_recorder_worker_lifecycle.py`` so
    ``__init__`` does not try to enumerate real audio devices.
    """
    from voice_typer.server.recording import Recorder

    config = MagicMock(sample_rate=16000, microphone=None)
    return Recorder(config)


# ── Source-inspection contracts ──────────────────────────────────


class TestSourceInspection:
    """``_process_audio_chunk`` must call
    ``_surface_ring_overflow_warning`` so the worker thread surfaces
    ring-buffer overflows in real time."""

    def test_process_audio_chunk_calls_warning_helper(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._process_audio_chunk)
        assert "_surface_ring_overflow_warning" in src, (
            "_process_audio_chunk must call _surface_ring_overflow_warning "
            "so ring-buffer overflow is surfaced in real time (not only post-stop)."
        )

    def test_warning_helper_emits_log_warning(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._surface_ring_overflow_warning)
        assert "log.warning" in src, "_surface_ring_overflow_warning must emit a WARNING log."

    def test_warning_helper_does_not_call_event_bus_publish(self):
        """contract is preserved: ``_process_audio_chunk`` (and
        its callees) must NOT call ``event_bus.publish`` directly —
        route IPC events through ``self._event_queue.put`` instead.
        The real-time ring-overflow surfacing is log-only (no IPC
        event) so the contract is trivially satisfied, but the
        regression guard pins it so a future change doesn't slip in
        an ``event_bus.publish`` call.
        """
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._surface_ring_overflow_warning)
        assert "event_bus.publish" not in src, (
            "_surface_ring_overflow_warning must not call event_bus.publish "
            "directly (contract — route IPC events through _event_queue.put)."
        )

    def test_init_declares_warning_bookkeeping_attrs(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder.__init__)
        assert "_last_seen_dropped_ring_chunks" in src, "Recorder.__init__ must declare _last_seen_dropped_ring_chunks."
        assert "_ring_overflow_warn_ts" in src, "Recorder.__init__ must declare _ring_overflow_warn_ts."


# ── Behavioral tests ─────────────────────────────────────────────


class TestRingOverflowWarning:
    """ER-89 behavioral contract: WARNING emitted on counter delta,
    rate-limited to one per ``_RING_OVERFLOW_WARN_INTERVAL_S``,
    delta does not accumulate across rate-limit windows."""

    def test_no_warning_when_counter_unchanged(self, recorder, caplog):
        """When ``_dropped_ring_chunks`` has not increased since the
        last check, no WARNING is emitted."""
        recorder._dropped_ring_chunks = 0
        recorder._last_seen_dropped_ring_chunks = 0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._surface_ring_overflow_warning()
        assert not any("Ring buffer overflow" in r.message for r in caplog.records), (
            "no WARNING expected when _dropped_ring_chunks is unchanged."
        )

    def test_warning_emitted_on_counter_increase(self, recorder, caplog):
        """When ``_dropped_ring_chunks`` increases between checks, a
        WARNING is emitted with the delta and the running total."""
        recorder._dropped_ring_chunks = 5
        recorder._last_seen_dropped_ring_chunks = 0
        # Force the rate-limit window to be expired.
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._surface_ring_overflow_warning()
        warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(warnings) == 1, "exactly one WARNING expected on delta increase."
        assert "5 chunks dropped" in warnings[0].message
        assert "total this session: 5" in warnings[0].message

    def test_warning_rate_limited_within_interval(self, recorder, caplog):
        """A second call within ``_RING_OVERFLOW_WARN_INTERVAL_S`` does
        NOT emit a WARNING, even if the counter increased further."""
        recorder._dropped_ring_chunks = 5
        recorder._last_seen_dropped_ring_chunks = 0
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._surface_ring_overflow_warning()  # emits WARNING, sets ts
            recorder._dropped_ring_chunks = 10
            recorder._surface_ring_overflow_warning()  # rate-limited, no WARNING
        warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(warnings) == 1, "second call within rate-limit interval must NOT emit a WARNING."

    def test_delta_does_not_accumulate_across_rate_limit_windows(self, recorder, caplog):
        """The ``_last_seen_dropped_ring_chunks`` counter is ALWAYS
        updated (even when the WARNING is rate-limited), so the next
        WARNING reports only the chunks dropped since the previous
        WARNING, not since the last unthrottled check.

        Sequence:
          1. counter=5, last_seen=0, ts=0  → WARNING (delta=5), ts=now
          2. counter=10, ts=now            → rate-limited, last_seen=10
          3. counter=12, ts=expired        → WARNING (delta=2), NOT 12
        """
        recorder._dropped_ring_chunks = 5
        recorder._last_seen_dropped_ring_chunks = 0
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._surface_ring_overflow_warning()  # WARNING delta=5
            recorder._dropped_ring_chunks = 10
            recorder._surface_ring_overflow_warning()  # rate-limited, last_seen=10
            # Simulate the rate-limit window expiring.
            recorder._ring_overflow_warn_ts = 0.0
            recorder._dropped_ring_chunks = 12
            recorder._surface_ring_overflow_warning()  # WARNING delta=2
        warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(warnings) == 2, "expected 2 WARNINGs (first + after rate-limit window expiry)."
        # The second WARNING reports only the delta since the first
        # WARNING's last_seen update (12 - 10 = 2), NOT 12.
        assert "2 chunks dropped" in warnings[1].message, (
            "delta must NOT accumulate across rate-limit windows — "
            "the second WARNING should report only chunks dropped since the "
            "previous WARNING (delta=2), not the session total delta (12)."
        )
        assert "total this session: 12" in warnings[1].message

    def test_no_warning_when_counter_decreases(self, recorder, caplog):
        """If ``_dropped_ring_chunks`` decreases (e.g. ``start()``
        reset it via ``session_state``), no WARNING is emitted."""
        recorder._dropped_ring_chunks = 0
        recorder._last_seen_dropped_ring_chunks = 5
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._surface_ring_overflow_warning()
        assert not any("Ring buffer overflow" in r.message for r in caplog.records), (
            "no WARNING expected when _dropped_ring_chunks decreases."
        )
        # The last-seen counter is still updated so subsequent
        # increases are measured from the new (lower) baseline.
        assert recorder._last_seen_dropped_ring_chunks == 0

    def test_process_audio_chunk_invokes_warning_helper(self, recorder, monkeypatch):
        """``_process_audio_chunk`` must call
        ``_surface_ring_overflow_warning`` before delegating to
        ``AudioPipeline.process_audio_chunk``. Verified by patching
        the helper to record the call, then calling
        ``_process_audio_chunk`` with a no-op pipeline."""
        calls: list[tuple] = []

        def _fake_warning(self_inner):
            calls.append(("warning",))

        monkeypatch.setattr(
            type(recorder),
            "_surface_ring_overflow_warning",
            _fake_warning,
        )
        # Replace the AudioPipeline with a no-op so the delegation
        # does not require real audio state.
        recorder._audio_pipeline = MagicMock()
        recorder._process_audio_chunk(
            indata=MagicMock(),
            frames=512,
            time_info=None,
            status=0,
            perf_ts=0.0,
        )
        assert calls == [("warning",)], (
            "_process_audio_chunk must call _surface_ring_overflow_warning on every chunk iteration."
        )
        recorder._audio_pipeline.process_audio_chunk.assert_called_once()
