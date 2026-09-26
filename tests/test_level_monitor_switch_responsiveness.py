"""Polls stay responsive while the level monitor opens or switches devices."""

from __future__ import annotations

import contextlib
import threading
import time

import pytest


@pytest.fixture()
def _clean_monitor():
    import voice_typer.server.level_monitor as lm

    lm._reset_state_for_tests()
    yield lm
    with contextlib.suppress(Exception):
        lm.stop_monitoring()
    lm._reset_state_for_tests()


def _make_stream_class(streams, *, construct_delay=0.0, entered=None):
    class _Stream:
        def __init__(self, *args, **kwargs):
            if entered is not None:
                entered.set()
            if construct_delay:
                time.sleep(construct_delay)
            self.closed = False
            streams.append(self)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            self.closed = True

    return _Stream


def _install_audio_fakes(monkeypatch, streams, *, query_delay=0.0, construct_delay=0.0, entered=None):
    import sounddevice as sd

    def _query(*args, **kwargs):
        if query_delay:
            time.sleep(query_delay)
        return {"default_samplerate": 16000}

    monkeypatch.setattr(sd, "query_devices", _query)
    monkeypatch.setattr(
        sd,
        "InputStream",
        _make_stream_class(streams, construct_delay=construct_delay, entered=entered),
    )


def _install_resolver(monkeypatch, mapping):
    monkeypatch.setattr(
        "voice_typer.server.server_platform.resolve_mic_id_to_device_index",
        lambda mic_id: mapping[mic_id],
    )


def _run_in_thread(fn, holder):
    def _target():
        try:
            holder["result"] = fn()
        except Exception as exc:  # surface thread failures in the test body
            holder["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread


class TestSwitchResponsiveness:
    def test_polls_stay_fast_during_slow_switch(self, monkeypatch, _clean_monitor):
        lm = _clean_monitor
        streams: list = []
        _install_audio_fakes(monkeypatch, streams)
        _install_resolver(monkeypatch, {"B": 1})

        assert lm.start_monitoring(mic_id=None)["success"] is True

        _install_audio_fakes(monkeypatch, streams, query_delay=0.4)
        holder: dict = {}
        switcher = _run_in_thread(lambda: lm.start_monitoring(mic_id="B"), holder)

        worst_ms = 0.0
        polls = 0
        deadline = time.monotonic() + 5.0
        while switcher.is_alive() and time.monotonic() < deadline:
            tick = time.perf_counter()
            level = lm.get_level()
            worst_ms = max(worst_ms, (time.perf_counter() - tick) * 1000.0)
            polls += 1
            assert isinstance(level, dict)
            time.sleep(0.01)
        switcher.join(timeout=5.0)

        assert "error" not in holder
        assert holder["result"]["success"] is True
        assert polls >= 3
        assert worst_ms < 200.0
        assert lm._monitor_mic_id == "B"

    def test_switch_commits_new_device_and_closes_old(self, monkeypatch, _clean_monitor):
        lm = _clean_monitor
        streams: list = []
        _install_audio_fakes(monkeypatch, streams)
        _install_resolver(monkeypatch, {"B": 1})

        assert lm.start_monitoring(mic_id=None)["success"] is True
        assert lm.start_monitoring(mic_id="B")["success"] is True

        assert lm._monitor_active is True
        assert lm._monitor_mic_id == "B"
        assert len(streams) == 2
        assert streams[0].closed is True
        assert streams[1].closed is False
        assert lm._monitor_stream is streams[1]

    def test_stop_during_slow_open_stays_stopped(self, monkeypatch, _clean_monitor):
        lm = _clean_monitor
        streams: list = []
        entered = threading.Event()
        _install_audio_fakes(monkeypatch, streams, construct_delay=0.5, entered=entered)

        holder: dict = {}
        opener = _run_in_thread(lambda: lm.start_monitoring(mic_id=None), holder)
        assert entered.wait(timeout=5.0)

        lm.stop_monitoring()
        opener.join(timeout=5.0)

        assert "error" not in holder
        assert lm._monitor_active is False
        assert lm._monitor_stream is None
        assert len(streams) == 1
        assert streams[0].closed is True

    def test_overlapping_switches_single_winner(self, monkeypatch, _clean_monitor):
        lm = _clean_monitor
        first_streams: list = []
        second_streams: list = []
        entered = threading.Event()
        _install_resolver(monkeypatch, {"A": 0, "B": 1})

        import sounddevice as sd

        monkeypatch.setattr(sd, "query_devices", lambda *a, **k: {"default_samplerate": 16000})
        monkeypatch.setattr(
            sd,
            "InputStream",
            _make_stream_class(first_streams, construct_delay=0.4, entered=entered),
        )
        first: dict = {}
        opener = _run_in_thread(lambda: lm.start_monitoring(mic_id="A"), first)
        assert entered.wait(timeout=5.0)

        monkeypatch.setattr(
            sd,
            "InputStream",
            _make_stream_class(second_streams),
        )
        assert lm.start_monitoring(mic_id="B")["success"] is True
        opener.join(timeout=5.0)

        assert "error" not in first
        assert lm._monitor_active is True
        assert lm._monitor_mic_id == "B"
        assert lm._monitor_stream is second_streams[0]
        assert first_streams[0].closed is True
        assert second_streams[0].closed is False
