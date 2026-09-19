"""Unit tests for ``make_segmented_progress_tracker``."""

import time
from pathlib import Path

from voice_typer.server.service._download_helpers import (
    make_segmented_progress_tracker,
)

_DOWNLOADS_SRC = (
    Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "service" / "model" / "_downloads.py"
).read_text(encoding="utf-8")


class TestPauseEventsSurvivePhaseAHandoff:
    """(poll loop) → Phase-B (segmented lane) handoff."""

    def test_phase_a_finally_unregisters_but_does_not_clear_pause(self):
        region = _DOWNLOADS_SRC.split("poll_outcome, last_total_bytes_seen = poll_download_progress(")[1].split(
            'if poll_outcome == "cancelled":'
        )[0]
        assert "_unregister_download(download_id)" in region
        assert "clear_download_pause_state" not in region

    def test_true_exit_paths_still_clear_pause(self):
        # Poll-cancelled return.
        cancelled = _DOWNLOADS_SRC.split('if poll_outcome == "cancelled":')[1].split("if download_err:")[0]
        assert "clear_download_pause_state()" in cancelled
        # Phase-A gate-abort return.
        assert _DOWNLOADS_SRC.count("clear_download_pause_state()") >= 4, (
            "cancelled + phase-A abort + phase-B abort + success-path cleanup"
        )


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, event: dict) -> None:
        self.published.append(event)

    def datas(self) -> list[dict]:
        assert all(e["type"] == "download_progress" for e in self.published)
        return [e["data"] for e in self.published]


def _make_tracker(bus, paused_cell, total=100 * 1024 * 1024):
    return make_segmented_progress_tracker(
        event_bus=bus,
        model_name="tiny",
        target_mb=78,
        target_bytes=78 * 1024 * 1024,
        phase_total_bytes=total,
        is_paused_fn=lambda: paused_cell[0],
    )


class TestRegularPushes:
    def test_first_push_shape(self):
        bus = _FakeBus()
        cb = _make_tracker(bus, [False])
        cb(10 * 1024 * 1024, 0)
        (data,) = bus.datas()
        assert data["model"] == "tiny"
        assert data["progress"] == min(95, int(10 + (10 / 100) * 85))
        assert data["downloaded_bytes"] == 10 * 1024 * 1024
        assert data["total_bytes"] == 78 * 1024 * 1024
        assert "MB / ~78 MB" in data["status"]
        assert "paused" not in data
        assert "resumed" not in data

    def test_throttle_suppresses_rapid_calls_but_final_always_pushes(self):
        bus = _FakeBus()
        total = 100 * 1024 * 1024
        cb = _make_tracker(bus, [False], total=total)
        cb(1 * 1024 * 1024, 0)
        cb(2 * 1024 * 1024, 0)
        assert len(bus.published) == 1
        cb(total, total)
        assert len(bus.published) == 2
        assert bus.datas()[-1]["downloaded_bytes"] == total

    def test_speed_and_eta_measured_from_deltas(self):
        bus = _FakeBus()
        total = 100 * 1024 * 1024
        cb = _make_tracker(bus, [False], total=total)
        cb(10 * 1024 * 1024, 0)
        time.sleep(0.3)  # outlast the 0.25 s throttle
        cb(20 * 1024 * 1024, 0)
        assert len(bus.published) == 2
        data = bus.datas()[-1]
        assert data["speed_bytes_per_sec"] > 0
        assert data["eta_seconds"] > 0


class TestPauseResumeTransitions:
    def test_pause_pushes_transition_then_goes_silent(self):
        bus = _FakeBus()
        paused = [False]
        cb = _make_tracker(bus, paused)
        cb(10 * 1024 * 1024, 0)
        paused[0] = True
        cb(11 * 1024 * 1024, 0)
        cb(12 * 1024 * 1024, 0)
        datas = bus.datas()
        assert len(datas) == 2
        transition = datas[-1]
        assert transition["paused"] is True
        assert transition["downloaded_bytes"] == 11 * 1024 * 1024
        assert "speed_bytes_per_sec" not in transition
        assert "paused" not in datas[0]

    def test_resume_pushes_transition_and_resets_speed_baseline(self):
        bus = _FakeBus()
        paused = [False]
        cb = _make_tracker(bus, paused)
        cb(10 * 1024 * 1024, 0)
        paused[0] = True
        cb(10 * 1024 * 1024, 0)
        time.sleep(0.3)  # simulate a real pause interval
        paused[0] = False
        cb(10 * 1024 * 1024, 0)
        datas = bus.datas()
        assert datas[-1]["resumed"] is True
        # The first post-resume regular push must not report a spike
        time.sleep(0.3)
        cb(11 * 1024 * 1024, 0)
        regular = bus.datas()[-1]
        assert "resumed" not in regular
        assert regular["speed_bytes_per_sec"] < 1e12

    def test_starting_paused_emits_no_spurious_transition(self):
        bus = _FakeBus()
        paused = [True]
        cb = _make_tracker(bus, paused)
        # Phase A already announced the pause; a duplicate
        cb(5 * 1024 * 1024, 0)
        assert bus.published == []
        paused[0] = False
        cb(5 * 1024 * 1024, 0)
        (data,) = bus.datas()
        assert data["resumed"] is True
