"""Regression guards for the Microphone page device-selection + test fixes.

Covers two root causes:

1. FALSE ``device_lost`` on intentional stream stop/switch, PortAudio's
   ``PaStreamFinishedCallback`` fires not only when a device vanishes but
   also on every ``stop()``/``close()``, i.e. exactly what selecting a
   different microphone (monitor restart) or leaving the page does.
   The identity-aware guard must suppress the intentional transitions and
   still report a genuine finish of the CURRENT stream.

2. Mic-test WAV transport, completed WAVs (~1 MB each) exceed the 1 MiB
   single-frame IPC cap, so stop persists them to disk under the config
   dir and serves bytes via chunked ``microphone_test_read_audio`` reads:
   keep-only-latest purge on start, containment (path traversal rejected),
   slice/eof correctness, and per-chunk size caps.
"""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════
# 1. finished-callback identity guard
# ═══════════════════════════════════════════════════════════════════════


class _FakeStream:
    """Stand-in for sd.InputStream capturing constructor kwargs."""

    last_instance: _FakeStream | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False
        _FakeStream.last_instance = self

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture()
def monitor_env(monkeypatch):
    """Reset level_monitor state + mock sounddevice + capture publishes.

    SNAPSHOTS every ``_state`` attribute touched below and restores the
    exact prior values afterwards, so sibling test modules running in the
    same xdist worker are not disturbed by hard-coded baseline resets.
    """
    import sounddevice as sd
    import voice_typer.server.level_monitor as lm
    from voice_typer.server.level_monitor._state import _state

    touched = [
        "_test_mode",
        "_test_start_time",
        "_test_duration",
        "_monitor_sample_rate",
        "_monitor_active",
        "_monitor_stream",
        "_monitor_mic_id",
        "_monitor_level",
        "_monitor_peak",
        "_level_processor",
        "_dropped_level_chunks",
        "_consecutive_zero_chunks",
        "_device_lost_emitted",
    ]
    _snapshot = {name: getattr(_state, name) for name in touched}

    def _restore():
        for name, value in _snapshot.items():
            setattr(_state, name, value)

    _state._test_mode = False
    _state._test_raw_chunks.clear()
    _state._test_filtered_chunks.clear()
    _state._test_start_time = 0.0
    _state._test_duration = 10.0
    _state._monitor_sample_rate = 16000
    _state._monitor_active = False
    _state._monitor_stream = None
    _state._monitor_level = 0.0
    _state._monitor_peak = 0.0
    _state._monitor_mic_id = None
    _state._level_processor = None
    _state._dropped_level_chunks = 0
    _state._level_ring_buffer.clear()
    _state._consecutive_zero_chunks = 0
    _state._device_lost_emitted = False
    while _state._mic_level_queue:
        _state._mic_level_queue.popleft()

    captured: list[dict] = []
    monkeypatch.setattr("voice_typer.server.event_bus.publish", lambda e: captured.append(dict(e)))

    class _QueryDevices:
        @staticmethod
        def __call__(device=None, kind=None):
            return {
                "name": "Mock Mic",
                "default_samplerate": 16000,
                "max_input_channels": 1,
                "hostapi": 0,
                "index": 0,
            }

    monkeypatch.setattr(sd, "InputStream", _FakeStream)
    monkeypatch.setattr(sd, "query_devices", _QueryDevices())
    yield {"captured": captured}

    # teardown: stop workers, then restore the exact prior global state
    # (snapshot taken before the hard-coded reset below).
    lm._stop_level_worker()
    lm._stop_mic_level_worker()
    _restore()


def _device_lost(events):
    return [e for e in events if e.get("type") == "device_lost"]


def _current_finished_cb():
    from voice_typer.server.level_monitor._state import _state

    stream = _state._monitor_stream
    assert isinstance(stream, _FakeStream)
    cb = stream.kwargs.get("finished_callback")
    assert callable(cb)
    return cb


class TestFinishedCallbackIdentityGuard:
    def test_device_switch_restart_does_not_emit_device_lost(self, monitor_env):
        """THE bug: selecting a concrete valid mic restarts the monitor;
        closing the OLD stream fired its finished_callback and emitted a
        bogus ``device_lost`` ("Selected microphone disconnected") for a
        perfectly healthy new stream."""
        import voice_typer.server.level_monitor as lm

        captured = monitor_env["captured"]

        assert lm.start_monitoring(mic_id=None)["success"]
        old_cb = _current_finished_cb()
        from voice_typer.server.level_monitor._state import _state as _st

        old_stream_id = id(_st._monitor_stream)

        # User selects a DIFFERENT mic → start_monitoring restarts.
        assert lm.start_monitoring(mic_id="WASAPI|Realtek")["success"]
        assert id(_st._monitor_stream) != old_stream_id, "fixture: restart expected"

        # PortAudio fires the OLD stream's finished callback after close().
        old_cb()

        assert _device_lost(captured) == [], "intentional device switch must NOT emit device_lost"
        assert _st._monitor_active is True, "new stream must stay active"

    def test_stop_monitoring_does_not_emit_device_lost(self, monitor_env):
        """Page unmount / explicit stop is intentional, no loss event."""
        import voice_typer.server.level_monitor as lm

        captured = monitor_env["captured"]
        assert lm.start_monitoring(mic_id=None)["success"]
        cb = _current_finished_cb()

        lm.stop_monitoring()
        cb()  # PortAudio finishes the stopped stream asynchronously

        assert _device_lost(captured) == []

    def test_genuine_finish_of_current_stream_still_emits(self, monitor_env):
        """A REAL unplug finishes the CURRENT active stream → one event."""
        import voice_typer.server.level_monitor as lm

        captured = monitor_env["captured"]
        assert lm.start_monitoring(mic_id=None)["success"]
        cb = _current_finished_cb()

        cb()

        events = _device_lost(captured)
        assert len(events) == 1
        assert events[0]["data"]["source"] == "stream_finished"
        assert lm.is_monitoring() is False

    def test_guard_is_idempotent_per_episode(self, monitor_env):
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor._state import _state

        captured = monitor_env["captured"]
        assert lm.start_monitoring(mic_id=None)["success"]
        cb = _current_finished_cb()
        cb()
        cb()  # double fire within the same episode
        assert len(_device_lost(captured)) == 1
        assert _state._device_lost_emitted is True


# ═══════════════════════════════════════════════════════════════════════
# 2. mic-test disk transport + chunked read endpoint
# ═══════════════════════════════════════════════════════════════════════


def _tiny_wav_bytes(sample_rate=16000, seconds=0.05) -> bytes:
    n = int(sample_rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return buf.getvalue()


class TestTestRecordingDiskTransport:
    def test_stop_persists_wav_files_with_refs(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        tr.start_test_recording(duration=5.0)
        chunk = np.ones((512, 1), dtype=np.float32) * 0.25
        tr._state._test_mode = True
        tr._state._test_raw_chunks.append(chunk)
        tr._state._test_filtered_chunks.append(chunk * 0.5)

        result = tr.stop_test_recording()

        assert result["success"] is True
        assert result["audio_file"] is not None
        assert result["raw_audio_file"] is not None
        assert "audio_base64" not in result, (
            "base64 payloads must NOT ride on the stop response, they "
            "exceeded the 1 MiB IPC frame cap and were silently dropped"
        )
        for ref in (result["audio_file"], result["raw_audio_file"]):
            data = Path(ref["path"]).read_bytes()
            assert len(data) == ref["bytes"] > 44  # WAV header at minimum
            with wave.open(io.BytesIO(data), "rb") as wf:
                assert wf.getnframes() > 0

    def test_start_purges_previous_recordings(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        stale = recordings / "test-filtered-old.wav"
        stale.write_bytes(_tiny_wav_bytes())

        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)
        monkeypatch.setattr(
            "voice_typer.server.level_monitor.monitoring.start_monitoring",
            lambda mic_id=None: {"success": True},
        )
        tr.start_test_recording(duration=1.0)

        assert not stale.exists(), "keep-only-latest: stale WAVs must be purged"

    def test_read_slice_roundtrip_and_eof(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        wav_file = recordings / "test-filtered-x.wav"
        payload = _tiny_wav_bytes(seconds=0.2)
        wav_file.write_bytes(payload)
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        seen = b""
        offset = 0
        chunks = []
        while True:
            res = tr.read_test_recording_slice(str(wav_file), offset, 256 * 1024)
            assert res["success"] is True
            import base64

            seen += base64.b64decode(res["data_b64"])
            chunks.append(len(res["data_b64"]))
            if res["eof"]:
                break
            offset += res["bytes_read"]

        assert seen == payload
        # Single-frame safety: every chunk response must fit the 1 MiB cap
        # even after base64 inflation (~4/3x).
        for encoded_len in chunks:
            assert encoded_len < 1024 * 1024

    @pytest.mark.parametrize(
        "evil",
        [
            "../../secrets.txt",
            "subdir/../test.wav",
            "/etc/passwd",
            "test.wav.bak",
        ],
    )
    def test_read_slice_rejects_paths_outside_recordings_dir(self, tmp_path, monkeypatch, evil):
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        secret = tmp_path / "secret.wav"
        secret.write_bytes(b"TOPSECRET")
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        target = str((recordings / evil).resolve()) if not evil.startswith("/") else evil
        res = tr.read_test_recording_slice(target, 0, 256 * 1024)
        assert res["success"] is False, f"path {evil!r} must be rejected"
        assert res["data_b64"] == ""

    def test_chunk_length_is_capped_at_256kib(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        big = recordings / "test-raw-big.wav"
        big.write_bytes(b"\x00" * (10 * 1024 * 1024))
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        res = tr.read_test_recording_slice(str(big), 0, 100 * 1024 * 1024)
        assert res["success"] is True
        import base64

        assert len(base64.b64decode(res["data_b64"])) <= 256 * 1024


class TestChunkedTransportIntegrity:
    """Playback corruption + rate-limit regressions (post-recording pipeline).

    Two real bugs this pins:
    1. Interior slices whose BYTE length is not a multiple of 3 carry their
       own base64 "=" padding; the renderer joins fragments verbatim, so a
       256*1024-byte default slice (% 3 == 1) injected mid-stream padding
       and corrupted every multi-chunk playback ("Could not play the test
       recording"). Non-final slices must be 3-byte aligned.
    2. The per-command rate-limiter COST for ``microphone_test_read_audio``
       was mistakenly set to 30, as heavy as model downloads. Eight cheap
       slice reads consumed the entire shared 200/s burst budget, the tail
       chunks were rejected, and auto-stop completion lost its audio.
    """

    def test_interior_slices_are_base64_join_safe(self, tmp_path, monkeypatch):
        import base64

        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        # Prime a payload large enough for several interior slices.
        wav_file = recordings / "test-filtered-multi.wav"
        payload = bytes(range(256)) * (300 * 1024 // 256)  # ~300 KiB
        wav_file.write_bytes(payload)
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        seen = b""
        offset = 0
        interior_sizes = []
        while True:
            res = tr.read_test_recording_slice(str(wav_file), offset, 256 * 1024)
            assert res["success"] is True
            chunk_bytes = base64.b64decode(res["data_b64"])
            if not res["eof"]:
                # INVARIANT: non-final fragments must be padding-free so
                # verbatim base64 joining stays valid.
                assert len(chunk_bytes) % 3 == 0, (
                    "interior slice is not 3-byte aligned, joined base64 would be corrupted by mid-stream padding"
                )
            interior_sizes.append(len(chunk_bytes))
            seen += chunk_bytes
            if res["eof"]:
                break
            offset += res["bytes_read"]

        assert seen == payload, "chunked transport must round-trip the exact recorded bytes"
        assert len(interior_sizes) >= 2, "fixture expected multiple slices"

    def test_rate_limiter_cost_is_cheap_read(self):
        """The read command is a bounded small-file disk read; a heavy cost
        weight made every completed test blow the shared burst window."""
        from voice_typer.server.ipc.rate_limiter import COMMAND_COSTS

        assert COMMAND_COSTS.get("microphone_test_read_audio") == 1


class TestHonestMetricsContract:
    """C-MIC-20: transcription-derived metrics must be gated on engine
    availability; audio-derived metrics (volume/noise/clipping/voice) are
    always computed from the captured WAV regardless of model state."""

    def test_no_engine_marks_transcription_unavailable_but_keeps_quality(self, tmp_path, monkeypatch):
        """Without a loaded ASR engine the stop envelope still carries the
        full AUDIO analysis (quality dict) plus the explicit
        ``transcription_unavailable`` marker, the frontend renders N/A
        instead of a fabricated 0% for the transcription-quality row."""
        from types import SimpleNamespace

        from voice_typer.server import level_monitor as lm
        from voice_typer.server.service.microphone_test import MicrophoneTestMixin

        wav_file = tmp_path / "test-filtered.wav"
        wav_file.write_bytes(_tiny_wav_bytes())

        monkeypatch.setattr(
            lm,
            "stop_test_recording",
            lambda: {
                "success": True,
                "audio_file": {"path": str(wav_file), "bytes": wav_file.stat().st_size},
                "raw_audio_file": None,
                "duration_ms": 10000,
                "sample_rate": 16000,
                "message": "ok",
                "quality": {"volume_level": "good", "has_voice": True},
            },
        )
        mixin = MicrophoneTestMixin()
        # ``Any``: the double satisfies only the narrow surface the
        # transcription path touches; AppProtocol's full shape isn't needed.
        mixin._app = cast(Any, SimpleNamespace(models=None))  # no model subsystem at all

        result: dict = mixin.microphone_test_stop()
        assert result["success"] is True
        assert result["transcription_unavailable"] is True
        assert result["transcription_reason"] == "no_engine_loaded"
        # Audio-derived verdict survives untouched.
        assert result["quality"]["has_voice"] is True


class TestBookkeepingFailureClosesStartedStream:
    """Started-stream leak: bookkeeping after ``stream.start()`` must close it.

    ``start_monitoring`` opens the PortAudio stream first and then runs
    fallible bookkeeping (worker spawn, processor rebuild). When that
    bookkeeping throws, the already-started stream must be
    ``stop()``/``close()``d instead of leaked.
    """

    def test_bookkeeping_failure_closes_started_stream(self, monitor_env, monkeypatch):
        import voice_typer.server.level_monitor as lm
        from voice_typer.server.level_monitor._state import _state

        def _boom():
            raise RuntimeError("worker spawn failed")

        # Patch the OWNING submodule's attribute (call-time import
        # contract): ``start_monitoring`` resolves this global from
        # ``monitoring`` at call time.
        monkeypatch.setattr(
            "voice_typer.server.level_monitor.monitoring._ensure_mic_level_worker_running",
            _boom,
        )
        result = lm.start_monitoring(mic_id=None)

        assert result["success"] is False
        assert _state._monitor_active is False
        assert _state._monitor_stream is None
        leaked = _FakeStream.last_instance
        assert leaked is not None
        assert leaked.stopped is True
        assert leaked.closed is True


class TestTinySliceGuard:
    """Zero-length slice edge: 1-2 byte requests aligned down to 0 bytes.

    The base64-join invariant clamps interior slices to multiples of 3,
    so a 1-2 byte request used to return success with ``bytes_read=0``
    and ``eof=False`` mid-file — a busy-loop hazard for fetchers that
    advance by ``bytes_read``. The endpoint must always make progress.
    """

    def test_single_byte_requests_make_progress(self, tmp_path, monkeypatch):
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        wav_file = recordings / "test-filtered-tiny.wav"
        payload = _tiny_wav_bytes(seconds=0.2)
        assert len(payload) > 16
        wav_file.write_bytes(payload)
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        res = tr.read_test_recording_slice(str(wav_file), 0, 1)
        assert res["success"] is True
        assert res["bytes_read"] > 0
        assert res["eof"] is False

        res2 = tr.read_test_recording_slice(str(wav_file), res["bytes_read"], 2)
        assert res2["success"] is True
        assert res2["bytes_read"] > 0

    def test_small_file_round_trips_without_busy_loop(self, tmp_path, monkeypatch):
        import base64

        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        wav_file = recordings / "test-filtered-small.wav"
        payload = bytes(range(256)) * 2  # 512 bytes
        wav_file.write_bytes(payload)
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        seen = b""
        offset = 0
        for _ in range(10000):
            res = tr.read_test_recording_slice(str(wav_file), offset, 2)
            assert res["success"] is True
            seen += base64.b64decode(res["data_b64"])
            offset += res["bytes_read"]
            if res["eof"]:
                break
        else:
            pytest.fail("slice fetcher made no progress to EOF (busy loop)")
        assert seen == payload

    def test_short_tail_returns_exact_bytes_with_eof(self, tmp_path, monkeypatch):
        import base64

        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        wav_file = recordings / "test-filtered-tail.wav"
        payload = _tiny_wav_bytes(seconds=0.2)
        wav_file.write_bytes(payload)
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)

        res = tr.read_test_recording_slice(str(wav_file), len(payload) - 2, 2)
        assert res["success"] is True
        assert res["bytes_read"] == 2
        assert res["eof"] is True
        assert base64.b64decode(res["data_b64"]) == payload[-2:]


class TestChunkCapacityConservativeBound:
    """Chunk-deque capacity stays a conservative upper bound at any rate."""

    def test_capacity_covers_scaled_block_rates(self):
        from voice_typer.server._audio_constants import scaled_audio_blocksize
        from voice_typer.server.level_monitor import test_recording as tr

        state = tr._state
        old_duration, old_sr = state._test_duration, state._monitor_sample_rate
        old_chunks, old_raw, old_filtered = (
            state._test_chunks,
            state._test_raw_chunks,
            state._test_filtered_chunks,
        )
        try:
            state._test_duration = 30.0
            state._monitor_sample_rate = 48000
            tr._reset_test_chunks(locked=False)
            cap = state._test_raw_chunks.maxlen
            assert cap == int(30.0 * 48000 / 512) + 1
            # At 48 kHz the stream emits ~32 ms (1536-sample) chunks, far
            # fewer than the 512-sample math assumes — the bound must
            # cover the scaled rate with room to spare.
            needed = int(30.0 * 48000 / scaled_audio_blocksize(48000)) + 1
            assert cap is not None and cap >= needed
        finally:
            state._test_duration = old_duration
            state._monitor_sample_rate = old_sr
            state._test_chunks = old_chunks
            state._test_raw_chunks = old_raw
            state._test_filtered_chunks = old_filtered


class TestDurationLogSuffix:
    """C-LOG-2: mic-test lifecycle lines carry a ``format_duration`` suffix."""

    def test_start_and_stop_lines_use_duration_suffix(self, monitor_env, tmp_path, monkeypatch, caplog):
        import logging

        from voice_typer.server.duration import format_duration
        from voice_typer.server.level_monitor import test_recording as tr

        recordings = tmp_path / "recs"
        recordings.mkdir()
        monkeypatch.setattr(tr, "_test_recordings_dir", lambda: recordings)
        with caplog.at_level(logging.INFO, logger="voice_typer.server.level_monitor"):
            assert tr.start_test_recording(duration=5.0)["success"] is True
            tr._state._test_raw_chunks.append(np.ones((512, 1), dtype=np.float32) * 0.25)
            result = tr.stop_test_recording()

        assert result["success"] is True
        messages = [r.getMessage() for r in caplog.records]
        assert any(f"duration{format_duration(5.0)}" in m for m in messages), (
            f"start line must carry the duration suffix; got: {messages}"
        )
        expected_stop = format_duration(result["duration_ms"] / 1000).strip()
        assert any("Test stopped:" in m and expected_stop in m for m in messages), (
            f"stop line must carry the duration suffix; got: {messages}"
        )
