"""Worker ``transcribe_offline`` tests (master plan §7.4).

The worker's core contract: the slim-core sidecar forwards
``{audio_path, sample_rate, language}`` over the worker's WS hop and
the worker transcribes the file, pushing the result back via the
``transcribe_offline_result`` event.

These tests cover:

1. **Dispatch** — a ``transcribe_offline`` frame through the real
   ``_handle_connection`` dispatch loop produces a
   ``transcribe_offline_result`` push event (mocked transcriber, real
   auth + frame loop). Mirrors the mocked-connection pattern from
   ``test_worker_startup.py``.
2. **Audio loading** — :func:`_load_wav_float32` decodes mono + stereo
   WAVs into float32 [-1, 1] with the native sample rate.
3. **Resampling** — :func:`_resample_to_16k` is a no-op at 16 kHz and
   delegates to the shared resampler otherwise (48 kHz → 16 kHz).
4. **Error paths** — missing path / missing file / decode failure /
   engine failure each produce a structured result payload (never a
   raised exception, never a dropped result event).

The engine is always mocked (``unittest.mock``) — no real model
download / GPU / audio hardware (E6: external deps mocked).
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.worker import __main__ as worker_main  # noqa: E402
from voice_typer.worker._transcribe import (  # noqa: E402
    WorkerTranscriber,
    _load_wav_float32,
    _resample_to_16k,
)

_TEST_TOKEN = "test-worker-token-12345"


def _make_wav_bytes(samples: np.ndarray, sample_rate: int, channels: int = 1) -> bytes:
    """Encode a float32 array as int16 WAV bytes."""
    buf = io.BytesIO()
    int16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        if channels == 1:
            wf.writeframes(int16.tobytes())
        else:
            wf.writeframes(np.repeat(int16[:, None], channels, axis=1).tobytes())
    return buf.getvalue()


def _make_fake_websocket(frames: list[dict]) -> MagicMock:
    """Mock websocket with an auth recv() + a frame __aiter__ (dispatch loop)."""
    auth_frame = json.dumps({"type": "auth", "token": _TEST_TOKEN}).encode()
    recv_calls: list[bytes] = [auth_frame]

    async def _fake_recv() -> bytes:
        if recv_calls:
            return recv_calls.pop(0)
        raise AssertionError("recv() called after auth frame — dispatch should use __aiter__")

    class _FrameAsyncIter:
        _remaining: list[bytes] = []

        def __aiter__(self) -> _FrameAsyncIter:
            return self

        async def __anext__(self) -> bytes:
            if _FrameAsyncIter._remaining:
                return _FrameAsyncIter._remaining.pop(0)
            raise StopAsyncIteration

    _FrameAsyncIter._remaining = [json.dumps(f).encode() for f in frames]

    ws = MagicMock()
    ws.recv = _fake_recv
    ws.__aiter__ = lambda self: _FrameAsyncIter()  # noqa: E731
    ws.remote_address = ("127.0.0.1", 12345)
    ws.origin = ""

    sent_frames: list[str] = []
    closed_with: list[tuple[tuple, dict]] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _track_close(*args, **kwargs):
        closed_with.append((args, kwargs))

    ws._sent_frames = sent_frames
    ws._closed_with = closed_with
    ws.send = _track_send
    ws.close = _track_close
    return ws


# ─── 1. Dispatch ────────────────────────────────────────────────────────


async def test_transcribe_offline_dispatch_emits_result_event(monkeypatch) -> None:
    """A transcribe_offline frame → transcribe_offline_result push event."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _TEST_TOKEN)

    fake = MagicMock()
    fake.transcribe_file.return_value = {"text": "hello world", "error": None, "latency_ms": 12}
    with patch("voice_typer.worker._transcribe.get_transcriber", return_value=fake):
        ws = _make_fake_websocket(
            [
                {
                    "cmd": "transcribe_offline",
                    "data": {"audio_path": "/tmp/test.wav", "sample_rate": 16000, "language": None},
                },
                {"cmd": "shutdown"},
            ]
        )
        stop_event = asyncio.Event()
        shutdown_timer = worker_main._ShutdownTimer()
        await worker_main._handle_connection(
            ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer
        )

    # The result event must have been pushed BEFORE the shutdown ack.
    frames = [json.loads(f) for f in ws._sent_frames]
    result_frames = [f for f in frames if f.get("type") == "transcribe_offline_result"]
    assert len(result_frames) == 1, f"expected one result event, got {frames}"
    assert result_frames[0]["data"] == {"text": "hello world", "error": None, "latency_ms": 12}
    assert any(f.get("type") == "shutdown_ack" for f in frames)
    assert stop_event.is_set()

    # The transcriber must have received the parsed payload.
    fake.transcribe_file.assert_called_once_with("/tmp/test.wav", 16000, None)


async def test_transcribe_offline_dispatch_string_sample_rate(monkeypatch) -> None:
    """String sample_rate in the frame is coerced to int before the transcriber."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _TEST_TOKEN)

    fake = MagicMock()
    fake.transcribe_file.return_value = {"text": "ok", "error": None, "latency_ms": 1}
    with patch("voice_typer.worker._transcribe.get_transcriber", return_value=fake):
        ws = _make_fake_websocket(
            [
                {
                    "cmd": "transcribe_offline",
                    "data": {"audio_path": "/tmp/t.wav", "sample_rate": "48000", "language": "en"},
                },
                {"cmd": "shutdown"},
            ]
        )
        stop_event = asyncio.Event()
        shutdown_timer = worker_main._ShutdownTimer()
        await worker_main._handle_connection(
            ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer
        )

    fake.transcribe_file.assert_called_once_with("/tmp/t.wav", 48000, "en")


async def test_transcribe_offline_dispatch_engine_error_still_emits_result(monkeypatch) -> None:
    """An engine exception must NOT drop the result event (no hang)."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _TEST_TOKEN)

    fake = MagicMock()
    fake.transcribe_file.side_effect = RuntimeError("boom")
    with patch("voice_typer.worker._transcribe.get_transcriber", return_value=fake):
        ws = _make_fake_websocket(
            [{"cmd": "transcribe_offline", "data": {"audio_path": "/tmp/x.wav"}}],
        )
        stop_event = asyncio.Event()
        shutdown_timer = worker_main._ShutdownTimer()
        await worker_main._handle_connection(
            ws, prewarm_ran=True, stop_event=stop_event, shutdown_timer=shutdown_timer
        )

    frames = [json.loads(f) for f in ws._sent_frames]
    result_frames = [f for f in frames if f.get("type") == "transcribe_offline_result"]
    assert len(result_frames) == 1, f"expected a result event even on error, got {frames}"
    assert result_frames[0]["data"]["text"] == ""
    assert "boom" in result_frames[0]["data"]["error"]


# ─── 2. Audio loading ───────────────────────────────────────────────────


def test_load_wav_float32_mono() -> None:
    tmp = Path("/tmp")
    path = tmp / f"vt-wav-mono-{np.random.randint(0, 1_000_000)}.wav"
    try:
        samples = np.array([0.5, -0.25, 0.0, 0.125, -1.0, 1.0], dtype=np.float32)
        path.write_bytes(_make_wav_bytes(samples, 48000, channels=1))
        audio, sr = _load_wav_float32(path)
        assert sr == 48000
        assert audio.shape == (6,)
        assert audio.dtype == np.float32
        np.testing.assert_allclose(audio, samples, atol=1.5e-4)
    finally:
        path.unlink(missing_ok=True)


def test_load_wav_float32_stereo_downmixed() -> None:
    tmp = Path("/tmp")
    path = tmp / f"vt-wav-stereo-{np.random.randint(0, 1_000_000)}.wav"
    try:
        samples = np.array([0.5, -0.5, 0.25], dtype=np.float32)
        path.write_bytes(_make_wav_bytes(samples, 16000, channels=2))
        audio, sr = _load_wav_float32(path)
        assert sr == 16000
        assert audio.shape == (3,)
        np.testing.assert_allclose(audio, samples, atol=1.5e-4)
    finally:
        path.unlink(missing_ok=True)


def test_load_wav_float32_missing_file_raises() -> None:
    with pytest.raises((FileNotFoundError, wave.Error, OSError)):
        _load_wav_float32("/nonexistent/definitely-missing.wav")


# ─── 3. Resampling ──────────────────────────────────────────────────────


def test_resample_to_16k_noop_at_16k() -> None:
    audio = np.zeros(1600, dtype=np.float32)
    out = _resample_to_16k(audio, 16000)
    assert out is audio  # no copy when already at target rate


def test_resample_to_16k_downscales_48k() -> None:
    t = np.linspace(0, 1, 48000, endpoint=False)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = _resample_to_16k(audio, 48000)
    assert out.shape == (16000,)
    assert out.dtype == np.float32


# ─── 4. Error paths (WorkerTranscriber) ─────────────────────────────────


def test_transcribe_file_missing_path() -> None:
    t = WorkerTranscriber()
    result = t.transcribe_file("", None, None)
    assert result["text"] == ""
    assert "missing audio_path" in result["error"]
    assert "latency_ms" in result


def test_transcribe_file_missing_file() -> None:
    t = WorkerTranscriber()
    result = t.transcribe_file("/nonexistent/definitely-missing.wav", 16000, None)
    assert result["text"] == ""
    assert "not found" in result["error"]


def test_transcribe_file_decode_failure() -> None:
    tmp = Path("/tmp")
    path = tmp / f"vt-bad-{np.random.randint(0, 1_000_000)}.wav"
    try:
        path.write_bytes(b"this is not a wav file at all")
        t = WorkerTranscriber()
        result = t.transcribe_file(str(path), 16000, None)
        assert result["text"] == ""
        assert "failed to decode" in result["error"]
    finally:
        path.unlink(missing_ok=True)


def test_transcribe_file_engine_failure() -> None:
    tmp = Path("/tmp")
    path = tmp / f"vt-ok-{np.random.randint(0, 1_000_000)}.wav"
    try:
        path.write_bytes(_make_wav_bytes(np.zeros(1600, dtype=np.float32), 16000))
        t = WorkerTranscriber()
        with patch.object(t, "_ensure_engine", side_effect=RuntimeError("no model")):
            result = t.transcribe_file(str(path), 16000, None)
        assert result["text"] == ""
        assert "no model" in result["error"]
    finally:
        path.unlink(missing_ok=True)


def test_transcribe_file_happy_path_mocked_engine() -> None:
    tmp = Path("/tmp")
    path = tmp / f"vt-happy-{np.random.randint(0, 1_000_000)}.wav"
    try:
        path.write_bytes(_make_wav_bytes(np.zeros(16000, dtype=np.float32), 16000))
        fake_engine = MagicMock()
        fake_engine.transcribe_with_fallback.return_value = "  hello world  "
        t = WorkerTranscriber()
        with patch.object(t, "_ensure_engine", return_value=fake_engine):
            result = t.transcribe_file(str(path), 16000, "en")
        assert result["error"] is None
        assert result["text"] == "hello world"
        assert result["latency_ms"] >= 0
    finally:
        path.unlink(missing_ok=True)


def test_transcribe_file_resamples_before_inference() -> None:
    """48 kHz input is resampled to 16 kHz before the engine sees it."""
    tmp = Path("/tmp")
    path = tmp / f"vt-resample-{np.random.randint(0, 1_000_000)}.wav"
    try:
        path.write_bytes(_make_wav_bytes(np.zeros(48000, dtype=np.float32), 48000))
        fake_engine = MagicMock()
        fake_engine.transcribe_with_fallback.return_value = "resampled ok"
        t = WorkerTranscriber()
        with patch.object(t, "_ensure_engine", return_value=fake_engine):
            result = t.transcribe_file(str(path), 48000, None)
        assert result["error"] is None
        assert result["text"] == "resampled ok"
        # The engine receives a 16 kHz array (16000 samples of 48k → 16000).
        audio_arg = fake_engine.transcribe_with_fallback.call_args[0][0]
        assert len(audio_arg) == 16000
    finally:
        path.unlink(missing_ok=True)
