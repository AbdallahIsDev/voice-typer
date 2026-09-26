"""Unit tests for ``voice_typer.server.worker_client`` (ADR-0024 Step 3)."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import queue

import pytest
from voice_typer.server import worker_client
from voice_typer.server.worker_client import (
    WorkerClient,
    backoff_delay,
    build_auth_frame,
    build_heartbeat_frame,
    build_transcribe_frame,
    get_shared_client,
    parse_incoming,
    port_from_worker_started,
    route_frame,
)


class _EndOfScriptError(Exception):
    """FakeWS raises this when its scripted inbound frames run out."""


class _FakeWS:
    def __init__(self, incoming=()) -> None:
        self.sent: list = []
        self._incoming = collections.deque(incoming)
        self.closed = False

    async def send(self, frame) -> None:
        self.sent.append(frame)

    async def recv(self):
        if not self._incoming:
            raise _EndOfScriptError
        item = self._incoming.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self, code=None) -> None:
        self.closed = True


def _client_with_recorder(**kwargs):
    published = []
    client = WorkerClient(publish=published.append, **kwargs)
    return client, published


class TestAuthFrame:
    def test_shape_matches_worker_handshake(self):
        frame = json.loads(build_auth_frame("tok"))
        assert frame == {"type": "auth", "token": "tok"}

    def test_is_str_never_bytes(self):
        assert isinstance(build_auth_frame("tok"), str)


class TestTranscribeFrame:
    def test_request_shape(self):
        frame = json.loads(build_transcribe_frame("a.wav", 16000, "en", 7))
        assert frame["cmd"] == "transcribe_offline"
        assert frame["id"] == 7
        assert frame["data"] == {"audio_path": "a.wav", "sample_rate": 16000, "language": "en"}

    def test_id_is_numeric_and_str_frame(self):
        raw = build_transcribe_frame("a.wav", None, None, 1)
        assert isinstance(raw, str)
        assert isinstance(json.loads(raw)["id"], int)


class TestParseIncoming:
    def test_valid_str(self):
        assert parse_incoming('{"type": "heartbeat_ack"}') == {"type": "heartbeat_ack"}

    def test_bytes_decoded(self):
        assert parse_incoming(b'{"type": "heartbeat_ack"}') == {"type": "heartbeat_ack"}

    def test_malformed_ignored(self):
        assert parse_incoming("{not json") is None
        assert parse_incoming("[1, 2]") is None
        assert parse_incoming(None) is None
        assert parse_incoming(42) is None


class TestRouteFrame:
    def test_result_publishes_bus_shape(self):
        published = []
        frame = {"type": "transcribe_offline_result", "data": {"text": "hi", "latency_ms": 12, "error": None}}
        assert route_frame(frame, published.append) == "result"
        assert published == [{"type": "transcribe_offline_result", "data": {"text": "hi", "latency_ms": 12}}]

    def test_result_missing_data_defaults(self):
        published = []
        assert route_frame({"type": "transcribe_offline_result"}, published.append) == "result"
        assert published[0]["data"] == {"text": "", "latency_ms": 0}

    def test_heartbeat_ack_and_unknown_silent(self):
        published = []
        assert route_frame({"type": "heartbeat_ack"}, published.append) == "heartbeat_ack"
        assert route_frame({"type": "whatever"}, published.append) == "ignored"
        assert published == []

    def test_error_frame_no_publish(self):
        published = []
        assert route_frame({"type": "error", "data": {"code": "x"}}, published.append) == "error"
        assert published == []


class TestBackoff:
    def test_exponential_then_capped(self):
        assert backoff_delay(0) == pytest.approx(0.5)
        assert backoff_delay(1) == pytest.approx(1.0)
        assert backoff_delay(3) == pytest.approx(4.0)
        assert backoff_delay(100) == pytest.approx(30.0)


class TestPortRelay:
    def test_valid_port(self):
        assert port_from_worker_started({"pid": 1, "version": "x", "port": 5123}) == 5123

    def test_invalid_is_none(self):
        assert port_from_worker_started(None) is None
        assert port_from_worker_started({"port": 0}) is None
        assert port_from_worker_started({"port": 99999}) is None
        assert port_from_worker_started({"port": "5123"}) is None
        assert port_from_worker_started({"port": True}) is None
        assert port_from_worker_started({}) is None

    def test_update_stores_valid_ignores_invalid(self):
        client, _ = _client_with_recorder()
        try:
            assert client.update_from_worker_started({"pid": 1, "version": "x", "port": 5123}) is True
            assert client.port == 5123
            assert client.update_from_worker_started({"port": "nope"}) is False
            assert client.port == 5123
        finally:
            client.close()

    def test_singleton_is_single_store(self):
        assert get_shared_client() is get_shared_client()


class TestNoPortNoSend:
    def test_send_without_port_returns_none(self):
        client, _ = _client_with_recorder()
        assert client.send_transcribe("a.wav") is None


class TestSession:
    async def test_auth_first_and_all_frames_str(self, monkeypatch):
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "s3cret")
        client, published = _client_with_recorder()
        ws = _FakeWS(
            [
                json.dumps({"type": "heartbeat_ack"}),
                json.dumps({"type": "transcribe_offline_result", "data": {"text": "hello", "latency_ms": 9}}),
            ]
        )
        client._port = 5123
        with pytest.raises(_EndOfScriptError):
            await client._run_session(ws, client._generation)
        assert ws.sent
        assert all(isinstance(f, str) for f in ws.sent)
        first = json.loads(ws.sent[0])
        assert first == {"type": "auth", "token": "s3cret"}
        assert published == [{"type": "transcribe_offline_result", "data": {"text": "hello", "latency_ms": 9}}]

    async def test_queued_transcribe_sent_as_str(self, monkeypatch):
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "t")
        client, _ = _client_with_recorder()
        client._port = 5123
        request_id = client.send_transcribe("a.wav", 16000, "en")
        assert request_id == 1
        ws = _FakeWS([])
        with pytest.raises(_EndOfScriptError):
            await client._run_session(ws, client._generation)
        bodies = [json.loads(f) for f in ws.sent]
        assert bodies[0]["type"] == "auth"
        assert bodies[1]["cmd"] == "transcribe_offline"
        assert bodies[1]["id"] == 1

    async def test_stale_generation_returns_without_connect(self, monkeypatch):
        client, _ = _client_with_recorder()
        client._generation = 5
        called = []

        async def _fail(url, **kwargs):
            called.append(url)
            raise AssertionError("stale loop must not connect")

        monkeypatch.setattr("websockets.asyncio.client.connect", _fail)
        client._port = 5123
        await client._connect_loop(4)
        assert called == []

    async def test_heartbeat_probe_after_idle(self, monkeypatch):
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "t")
        client, _ = _client_with_recorder()
        client._port = 5123
        monkeypatch.setattr(worker_client, "_HEARTBEAT_SECONDS", 0.01)
        ws = _FakeWS([asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError(), asyncio.TimeoutError()])
        with pytest.raises(worker_client._WorkerUnresponsiveError):
            await client._run_session(ws, client._generation)
        heartbeats = [json.loads(f) for f in ws.sent if "heartbeat" in f]
        assert len(heartbeats) == 4
        assert json.loads(build_heartbeat_frame()) == {"type": "heartbeat"}

    def test_token_never_logged(self, monkeypatch, caplog):
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "super-secret-token")
        client, _ = _client_with_recorder()
        ws = _FakeWS([])
        client._port = 5123
        logger_name = "voice_typer.server.worker_client"
        with caplog.at_level(logging.DEBUG, logger=logger_name), pytest.raises(_EndOfScriptError):
            asyncio.run(client._run_session(ws, client._generation))
        assert "super-secret-token" not in caplog.text


class TestQueueFull:
    def test_full_queue_returns_none(self):
        client, _ = _client_with_recorder()
        client._port = 5123
        client._outbound = queue.Queue(maxsize=1)
        assert client.send_transcribe("a.wav") == 1
        assert client.send_transcribe("b.wav") is None


class TestFrameCapParity:
    def test_matches_worker_cap(self):
        from voice_typer.worker import _ws_server

        assert worker_client._MAX_FRAME_BYTES == _ws_server._MAX_FRAME_BYTES == 1 * 1024 * 1024
