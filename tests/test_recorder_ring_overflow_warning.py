"""audio worker thread."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def recorder():
    """Construct a real ``Recorder`` instance via the shared factory."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder()


class TestSourceInspection:
    """``_process_audio_chunk`` must call"""

    def test_process_audio_chunk_calls_warning_helper(self):
        from voice_typer.server.recording import Recorder

        src = inspect.getsource(Recorder._process_audio_chunk)
        assert "surface_ring_overflow_warning" in src, (
            "_process_audio_chunk must call _surface_ring_overflow_warning "
            "so ring-buffer overflow is surfaced in real time (not only post-stop)."
        )

    def test_warning_helper_emits_log_warning(self):
        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        src = inspect.getsource(AudioCallbackDispatcher.surface_ring_overflow_warning)
        assert "log.warning" in src, "_surface_ring_overflow_warning must emit a WARNING log."

    def test_warning_helper_does_not_call_event_bus_publish(self):
        """its callees) must NOT call ``event_bus.publish`` directly —"""
        from voice_typer.server.recording.capture import AudioCallbackDispatcher

        def _strip_docstring(src: str) -> str:
            """forbidden literal (documenting this very contract) do not"""
            start = src.find('"""')
            if start == -1:
                return src
            end = src.find('"""', start + 3)
            if end == -1:
                return src
            return src[end + 3 :]

        # Body lives on the collaborator since the god-class split;
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.surface_ring_overflow_warning))
        assert "event_bus.publish" not in src, (
            "_surface_ring_overflow_warning must not call event_bus.publish "
            "directly (contract, route IPC events through _event_queue.put)."
        )

    def test_init_declares_warning_bookkeeping_attrs(self):
        from tests.fixtures.ipc_test_helpers import make_fake_recorder

        recorder = make_fake_recorder()
        assert recorder._last_seen_dropped_ring_chunks == 0, (
            "Recorder construction must declare _last_seen_dropped_ring_chunks at 0."
        )
        assert recorder._ring_overflow_warn_ts == 0.0, (
            "Recorder construction must declare _ring_overflow_warn_ts at 0.0."
        )


class TestRingOverflowWarning:
    """ER-89 behavioral contract: WARNING emitted on counter delta,"""

    def test_no_warning_when_counter_unchanged(self, recorder, caplog):
        """When ``_dropped_ring_chunks`` has not increased since the"""
        recorder._dropped_ring_chunks = 0
        recorder._last_seen_dropped_ring_chunks = 0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._capture.surface_ring_overflow_warning(recorder)
        assert not any("Ring buffer overflow" in r.message for r in caplog.records), (
            "no WARNING expected when _dropped_ring_chunks is unchanged."
        )

    def test_warning_emitted_on_counter_increase(self, recorder, caplog):
        """When ``_dropped_ring_chunks`` increases between checks, a"""
        recorder._dropped_ring_chunks = 5
        recorder._last_seen_dropped_ring_chunks = 0
        # Force the rate-limit window to be expired.
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._capture.surface_ring_overflow_warning(recorder)
        warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(warnings) == 1, "exactly one WARNING expected on delta increase."
        assert "5 chunks dropped" in warnings[0].message
        assert "total this session: 5" in warnings[0].message

    def test_warning_rate_limited_within_interval(self, recorder, caplog):
        """A second call within ``_RING_OVERFLOW_WARN_INTERVAL_S`` does"""
        recorder._dropped_ring_chunks = 5
        recorder._last_seen_dropped_ring_chunks = 0
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._capture.surface_ring_overflow_warning(recorder)  # emits WARNING, sets ts
            recorder._dropped_ring_chunks = 10
            recorder._capture.surface_ring_overflow_warning(recorder)  # rate-limited, no WARNING
        warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(warnings) == 1, "second call within rate-limit interval must NOT emit a WARNING."

    def test_delta_does_not_accumulate_across_rate_limit_windows(self, recorder, caplog):
        """The ``_last_seen_dropped_ring_chunks`` counter is ALWAYS"""
        recorder._dropped_ring_chunks = 5
        recorder._last_seen_dropped_ring_chunks = 0
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._capture.surface_ring_overflow_warning(recorder)  # WARNING delta=5
            recorder._dropped_ring_chunks = 10
            recorder._capture.surface_ring_overflow_warning(recorder)  # rate-limited, last_seen=10
            # Simulate the rate-limit window expiring.
            recorder._ring_overflow_warn_ts = 0.0
            recorder._dropped_ring_chunks = 12
            recorder._capture.surface_ring_overflow_warning(recorder)  # WARNING delta=2
        warnings = [r for r in caplog.records if "Ring buffer overflow" in r.message]
        assert len(warnings) == 2, "expected 2 WARNINGs (first + after rate-limit window expiry)."
        # The second WARNING reports only the delta since the first
        assert "2 chunks dropped" in warnings[1].message, (
            "delta must NOT accumulate across rate-limit windows, "
            "the second WARNING should report only chunks dropped since the "
            "previous WARNING (delta=2), not the session total delta (12)."
        )
        assert "total this session: 12" in warnings[1].message

    def test_no_warning_when_counter_decreases(self, recorder, caplog):
        """If ``_dropped_ring_chunks`` decreases (e.g. ``start()``"""
        recorder._dropped_ring_chunks = 0
        recorder._last_seen_dropped_ring_chunks = 5
        recorder._ring_overflow_warn_ts = 0.0
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.recording"):
            recorder._capture.surface_ring_overflow_warning(recorder)
        assert not any("Ring buffer overflow" in r.message for r in caplog.records), (
            "no WARNING expected when _dropped_ring_chunks decreases."
        )
        # The last-seen counter is still updated so subsequent
        assert recorder._last_seen_dropped_ring_chunks == 0

    def test_process_audio_chunk_invokes_warning_helper(self, recorder, monkeypatch):
        """``_process_audio_chunk`` must call"""
        calls: list[tuple] = []

        def _fake_warning(self_inner):
            calls.append(("warning",))

        monkeypatch.setattr(
            recorder._capture,
            "surface_ring_overflow_warning",
            _fake_warning,
        )
        # Replace the AudioPipeline with a no-op so the delegation
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
