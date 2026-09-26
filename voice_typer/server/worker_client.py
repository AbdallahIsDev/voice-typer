"""Slim-core sidecar WS client to the runtime-pack worker (ADR-0024 Step 3).

One purpose only: own the worker hop (connect, auth, forward, route,
heartbeat, reconnect). Transcribe dispatch policy lives in
``voice_typer/server/ipc/lifecycle.py`` (Step 4 owns that body).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from typing import Any

from voice_typer.server import event_bus
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR, LOOPBACK_HOST
from voice_typer.server.duration import format_duration

log = logging.getLogger("voice_typer.server.worker_client")

# 1 MiB frame cap, matches the worker's ``_ws_server._MAX_FRAME_BYTES``
# (ADR-0020 §10). Local constant, not an import: the slim-core build
# must not depend on the ``voice_typer.worker`` package (Step 7 slims
# it out); parity pinned by ``tests/test_worker_client.py``.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

_HEARTBEAT_SECONDS = 15.0
_MAX_MISSED_HEARTBEATS = 3
_BACKOFF_FIRST_SECONDS = 0.5
_BACKOFF_CAP_SECONDS = 30.0
_OUTBOUND_QUEUE_MAX = 64


def build_auth_frame(token: str) -> str:
    """First-frame auth envelope the worker's ``_authenticate`` expects."""
    # C-WS-2: str, never bytes, so the frame leaves as a TEXT opcode.
    return json.dumps({"type": "auth", "token": token})


def build_transcribe_frame(audio_path: str, sample_rate: int | None, language: str | None, request_id: int) -> str:
    """Forward envelope for one offline transcription (event_bus §7.4 shape)."""
    # C-WS-2: numeric top-level id for response correlation; the worker
    # does not echo it yet, so results are routed by type (tolerated).
    return json.dumps(
        {
            "cmd": "transcribe_offline",
            "id": int(request_id),
            "data": {"audio_path": audio_path, "sample_rate": sample_rate, "language": language},
        }
    )


def build_heartbeat_frame() -> str:
    """Liveness ping; the worker answers ``heartbeat_ack`` (plan §7.2)."""
    return json.dumps({"type": "heartbeat"})


def parse_incoming(raw: object) -> dict | None:
    """Decode one inbound frame; ``None`` means ignorable (never raises)."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        frame = json.loads(raw)  # type: ignore[arg-type]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return frame if isinstance(frame, dict) else None


def route_frame(frame: dict, publish: Any) -> str:
    """Route one decoded frame; returns the action taken for liveness."""
    ftype = frame.get("type") or frame.get("cmd")
    if ftype == "transcribe_offline_result":
        data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
        result = {"text": str(data.get("text") or ""), "latency_ms": int(data.get("latency_ms") or 0)}
        publish({"type": "transcribe_offline_result", "data": result})
        return "result"
    if ftype == "heartbeat_ack":
        return "heartbeat_ack"
    if ftype == "error":
        log.warning("[WORKER] worker error frame: %s", frame.get("data"))
        return "error"
    log.debug("[WORKER] unknown frame type %r: ignoring", ftype)
    return "ignored"


def backoff_delay(attempt: int) -> float:
    """Exponential reconnect backoff, capped (attempt counts failed tries)."""
    return min(_BACKOFF_FIRST_SECONDS * (2.0 ** max(0, attempt)), _BACKOFF_CAP_SECONDS)


def port_from_worker_started(payload: object) -> int | None:
    """Extract the worker port from a Step-2 ``worker_started`` payload."""
    # E8: None is the only no-value; invalid ports degrade to it.
    if not isinstance(payload, dict):
        return None
    port = payload.get("port")
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    return port if 1 <= port <= 65535 else None


class _WorkerUnresponsiveError(Exception):
    """Heartbeat budget exhausted; the session must reconnect."""


class WorkerClient:
    """Long-lived WS client to the worker (§7.3); generation-stamped (C-WS-3)."""

    def __init__(self, publish: Any | None = None) -> None:
        self._publish = publish if publish is not None else event_bus.publish
        self._lock = threading.Lock()
        self._port: int | None = None
        self._generation = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._outbound: queue.Queue[str] = queue.Queue(maxsize=_OUTBOUND_QUEUE_MAX)
        self._request_seq = 0
        self._last_request_t0: float | None = None

    @property
    def port(self) -> int | None:
        """Current worker port; ``None`` means unknown (E8)."""
        with self._lock:
            return self._port

    def update_from_worker_started(self, payload: object) -> bool:
        """Consume the Step-2 relay; invalid payloads are ignored, never stored."""
        port = port_from_worker_started(payload)
        if port is None:
            log.debug("[WORKER] worker_started relay without a valid port: ignoring")
            return False
        self.set_port(port)
        return True

    def set_port(self, port: int) -> None:
        """Point the client at a worker; bumps the generation (C-WS-3)."""
        with self._lock:
            self._port = int(port)
            self._generation += 1
            generation = self._generation
            self._stop_event.clear()
            if self._thread is None or not self._thread.is_alive():
                # RACE-008: daemon so a wedged worker never blocks exit.
                self._thread = threading.Thread(target=self._run, args=(generation,), name="worker-client", daemon=True)
                self._thread.start()

    def close(self) -> None:
        """Stop reconnecting; the bumped generation orphans stale loops (C-WS-3)."""
        with self._lock:
            self._generation += 1
            self._port = None
        self._stop_event.set()

    def send_transcribe(
        self, audio_path: str, sample_rate: int | None = None, language: str | None = None
    ) -> int | None:
        """Queue one forward; ``None`` when there is nowhere to send it (E8)."""
        with self._lock:
            port = self._port
            self._request_seq += 1
            request_id = self._request_seq
        if port is None:
            return None
        try:
            self._outbound.put_nowait(build_transcribe_frame(audio_path, sample_rate, language, request_id))
        except queue.Full:
            log.warning("[WORKER] outbound queue full: dropping transcribe request")
            return None
        self._last_request_t0 = time.perf_counter()
        return request_id

    def _run(self, generation: int) -> None:
        """Thread entry: own an asyncio loop for this generation's lifetime."""
        try:
            asyncio.run(self._connect_loop(generation))
        except Exception:
            log.debug("[WORKER] client loop exited", exc_info=True)

    def _current_url(self) -> str | None:
        with self._lock:
            return f"ws://{LOOPBACK_HOST}:{self._port}" if self._port is not None else None

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation and not self._stop_event.is_set()

    async def _connect_loop(self, generation: int) -> None:
        """Reconnect with backoff until superseded (generation) or closed."""
        attempt = 0
        while self._is_current(generation):
            url = self._current_url()
            if url is None:
                return
            t0 = time.perf_counter()
            try:
                # websockets imported lazily: the module stays importable
                # without the optional WS dep (mirrors _ws_server).
                from websockets.asyncio.client import connect

                async with connect(url, max_size=_MAX_FRAME_BYTES) as ws:
                    if not self._is_current(generation):
                        return
                    elapsed = format_duration(time.perf_counter() - t0)
                    log.info("[WORKER] connected to worker at %s:%s%s", LOOPBACK_HOST, self.port, elapsed)
                    attempt = 0
                    await self._run_session(ws, generation)
            except Exception:
                if not self._is_current(generation):
                    return
                delay = backoff_delay(attempt)
                log.warning("[WORKER] worker connection lost: retrying%s", format_duration(delay))
                attempt += 1
                await asyncio.sleep(delay)

    async def _run_session(self, ws: Any, generation: int) -> None:
        """Auth, then pump outbound frames and route inbound ones."""
        # ADR-0020 §3: token rides the first frame; the value itself is
        # never logged (only the fact it was sent).
        await ws.send(build_auth_frame(os.environ.get(IPC_TOKEN_ENV_VAR, "")))
        log.debug("[WORKER] auth frame sent (port=%s)", self.port)
        missed = 0
        while self._is_current(generation):
            await self._drain_outbound(ws)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_HEARTBEAT_SECONDS)
            except (asyncio.TimeoutError, TimeoutError):
                # No traffic at all for a full interval: probe liveness.
                await ws.send(build_heartbeat_frame())
                missed += 1
                if missed > _MAX_MISSED_HEARTBEATS:
                    raise _WorkerUnresponsiveError(f"{missed} missed heartbeats") from None
                continue
            frame = parse_incoming(raw)
            if frame is None:
                log.warning("[WORKER] non-JSON frame from worker: ignoring")
                continue
            missed = 0
            if route_frame(frame, self._publish) == "result":
                data = frame.get("data")
                text_len = len(str(data.get("text") or "")) if isinstance(data, dict) else 0
                t0 = self._last_request_t0
                suffix = format_duration(time.perf_counter() - t0) if t0 is not None else ""
                # C-LOG-2: space-separated duration suffix via format_duration.
                log.info("[WORKER] transcribe_offline_result (len=%d chars)%s", text_len, suffix)

    async def _drain_outbound(self, ws: Any) -> None:
        """Send every queued frame in order; returns when the queue is empty."""
        # C-WS-2: every outbound frame is str (TEXT opcode, never bytes).
        while True:
            try:
                frame = self._outbound.get_nowait()
            except queue.Empty:
                return
            await ws.send(frame)


_lock = threading.Lock()
_shared_client: WorkerClient | None = None


def get_shared_client() -> WorkerClient:
    """The single sidecar-process worker client (C-CONF-1: no second port store)."""
    global _shared_client
    if _shared_client is None:
        with _lock:
            if _shared_client is None:
                _shared_client = WorkerClient()
    return _shared_client
