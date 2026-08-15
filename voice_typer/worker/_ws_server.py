"""WebSocket server + connection handling for the worker (master plan §7.3).

This module is an intentional extraction from ``voice_typer/worker/__main__.py``
per E3 (no spaghetti entry files). It owns:

- The WS server lifecycle: :func:`run_worker_server` binds
  ``127.0.0.1:0`` via ``websockets.asyncio.server.serve``, emits the
  ``worker_started`` stdout event, and blocks on ``stop_event`` until
  graceful shutdown.
- The connection handler :func:`_handle_connection`: authenticates the
  slim-core sidecar, acknowledges heartbeats, dispatches the
  ``shutdown`` command (which sets ``stop_event`` so ``run_worker_server``
  unblocks and the worker exits cleanly), and dispatches
  ``transcribe_offline`` (real ASR via
  :func:`voice_typer.worker._transcribe.get_transcriber`, result pushed
  back as ``transcribe_offline_result`` — master plan §7.4).
- The SIGTERM handler :func:`_install_sigterm_handler` (POSIX) — also
  sets ``stop_event`` on signal.
- The :class:`_ShutdownTimer` that measures the wall-clock duration of
  graceful shutdown for the ``[SHUTDOWN] worker shutdown complete_<duration>``
  log line per C-LOG-2.
- Stdout helpers (:func:`_force_line_buffered_stdout`,
  :func:`_emit_worker_started`) and the prewarm phase
  (:func:`_run_prewarm_phase`).

The shutdown command path is the authoritative graceful-shutdown
mechanism. When the sidecar sends ``{"cmd":"shutdown"}``, the worker
sends ``shutdown_ack``, calls ``stop_event.set()``, marks the shutdown
timer's start, and closes the socket — so ``run_worker_server``'s
``await stop_event.wait()`` unblocks, ``async with serve()`` exits
cleanly, and ``run()``'s ``finally: lock_handle.release()`` runs.

NOTE: a sidecar that closes the WS WITHOUT sending ``shutdown`` does
NOT trigger worker exit (intentional — the respawn scheduler may
briefly disconnect and reconnect). Use the ``shutdown`` command for
graceful exit; SIGTERM (POSIX) / taskkill (Windows) is the forceful
backstop.
"""

from __future__ import annotations

import contextlib
import json
import logging
import signal
import sys
import time
from types import FrameType
from typing import TYPE_CHECKING

from voice_typer.server._paths import LOOPBACK_HOST
from voice_typer.server.duration import format_duration
from voice_typer.worker._auth import _authenticate, _send_auth_failed_and_close

if TYPE_CHECKING:
    import asyncio

log = logging.getLogger("voice_typer.worker")

# ADR-0020 §10: 1 MiB WS frame cap. Matches the slim-core sidecar's
# ``sidecar_ws._MAX_FRAME_BYTES`` so the two transports agree on the
# maximum envelope size.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Concurrent-connection limit (DoS protection). The worker should only
# ever have ONE authenticated client (the slim-core sidecar); the limit
# is set to a small number to allow a brief overlap during the
# sidecar's respawn window (master plan §7.2 "Respawn scheduler").
_MAX_WS_CONNECTIONS = 4

# Protocol version (mirrors ``sidecar_ws.PROTOCOL_VERSION``). The
# slim-core sidecar's WS client checks this on the ``worker_started``
# line so a version-skewed worker is rejected at handshake time rather
# than failing on the first ``transcribe_offline`` request.
PROTOCOL_VERSION: int = 1

# Stdout event name. Distinct from the slim-core sidecar's
# ``server_started`` (which the host already listens for) so the host's
# stdout parser can route the worker's bind info to the worker-spawn
# code path (not the sidecar-spawn code path). See master plan §7.3.
_WORKER_STARTED_EVENT = "worker_started"


# ─── Prewarm phase (master plan §6.2 P-1) ──────────────────────────────


def _fast_startup_enabled() -> bool:
    """Read the ``fast_startup`` config toggle (Settings → General).

    The toggle is the user's start/stop switch for prewarm: when
    disabled, the worker skips its warm phase entirely. Read from the
    config file directly — the worker is a separate process spawned
    by the host, so there is no live ``app.config`` instance to
    consult. Defaults to ENABLED on any read failure (the historical
    default; a config hiccup must not silently stop warming).
    """
    try:
        from voice_typer.server.config import Config

        return bool(getattr(Config.load(), "fast_startup", True))
    except Exception:
        log.debug("[WORKER] fast_startup config read failed — defaulting to enabled", exc_info=True)
        return True


def _run_prewarm_phase() -> float:
    """Run the prewarm phase ONCE at worker startup.

    Calls :func:`voice_typer.server.prewarm.warm_imports_for_worker`,
    which pages the runtime-pack libraries' files into the OS standby
    cache (``onnxruntime`` + ``ctranslate2`` + ``numpy`` + ``scipy`` +
    ``faster_whisper``) WITHOUT importing them. The worker still has
    to execute each library's code once, in its own process — that is
    unavoidable — but the cold-disk read is paid here, in the
    background, BEFORE the first transcription request.

    Skips warming entirely when the ``fast_startup`` config toggle is
    disabled (the user's start/stop control — RESTORED 2026-08-14,
    see plan §6.3 addendum). Either way, the warm-run timing is
    persisted via :func:`write_prewarm_status_file` so the About-page
    Cache Status card can show "last run + seconds".

    Returns the elapsed wall-clock seconds (used for the
    ``[STARTUP]`` log line's ``_<duration>`` suffix per C-LOG-2;
    ``0.0`` when prewarm was skipped).
    """
    from datetime import datetime

    from voice_typer.server.prewarm.status import write_prewarm_status_file

    t0 = time.perf_counter()
    if not _fast_startup_enabled():
        log.info("[STARTUP] worker prewarm phase SKIPPED — fast_startup disabled in config")
        write_prewarm_status_file(last_run=None, elapsed_s=0.0)
        return 0.0
    try:
        from voice_typer.server.prewarm import warm_imports_for_worker

        warm_imports_for_worker()
    except Exception:
        # Prewarm is best-effort: a failure here MUST NOT crash the
        # worker (the cold cache only costs latency, never correctness).
        log.debug("[WORKER] prewarm phase failed — continuing with cold cache", exc_info=True)
    elapsed = time.perf_counter() - t0
    write_prewarm_status_file(
        last_run=datetime.now().isoformat(timespec="seconds"),
        elapsed_s=round(elapsed, 1),
    )
    log.info("[STARTUP] worker prewarm phase complete%s", format_duration(elapsed))
    return elapsed


# ─── Stdout protocol ──────────────────────────────────────────────────


def _force_line_buffered_stdout() -> None:
    """Force stdout to line buffering (ADR-0020 §1 Phase-0 blocker).

    When the Tauri host pipes the worker's stdout, CPython switches to
    block buffering, so the ``worker_started`` JSON is held in the
    buffer and the host hangs forever waiting. ``reconfigure`` flips
    the stream back to line buffering so each ``\\n`` flushes.

    Mirrors :func:`voice_typer.server.sidecar_ws._force_line_buffered_stdout`.
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        with contextlib.suppress(Exception):
            sys.stdout = open(  # noqa: SIM115 - intentional reopen
                sys.stdout.fileno(),
                "w",
                buffering=1,
                encoding="utf-8",
                closefd=False,
            )


def _emit_worker_started(port: int, protocol: int = PROTOCOL_VERSION) -> None:
    """Write the ONE structured stdout line the host is parsing for.

    Per master plan §7.3, this is the ONLY thing that ever goes to
    stdout from the worker. Every other log goes to stderr / the
    rotating file log. The host blocks reading stdout until it parses
    this JSON, then opens a WS client to ``ws://127.0.0.1:<port>``.

    The ``protocol`` field lets the host detect version skew at
    handshake time (mirrors :func:`sidecar_ws._emit_server_started`).
    """
    print(
        json.dumps({"event": _WORKER_STARTED_EVENT, "port": int(port), "protocol": int(protocol)}),
        flush=True,
    )


# ─── Shutdown timer (C-LOG-2 duration suffix on the SHUTDOWN log line) ─


class _ShutdownTimer:
    """Tracks the wall-clock start time of graceful shutdown.

    Used to compute the ``_<duration>`` suffix on the
    ``[SHUTDOWN] worker shutdown complete_<duration>`` log line per
    C-LOG-2. The timer is started ONCE — the first call to
    :meth:`start` wins, so concurrent shutdown triggers (SIGTERM +
    shutdown command arriving simultaneously) do not reset the
    measurement.

    The measurement source is :func:`time.perf_counter` (monotonic)
    per C-LOG-2.
    """

    __slots__ = ("_t0",)

    def __init__(self) -> None:
        self._t0: float | None = None

    def start(self) -> None:
        """Mark the start of graceful shutdown (idempotent).

        Safe to call from a signal handler context (POSIX
        ``add_signal_handler`` runs in the loop thread, not interrupt
        context) and from inside an async connection handler. The
        first call wins; subsequent calls are no-ops.
        """
        if self._t0 is None:
            self._t0 = time.perf_counter()

    def elapsed(self) -> float:
        """Return seconds since :meth:`start` was first called.

        Returns ``0.0`` if :meth:`start` was never called (e.g. the
        worker exited before any shutdown trigger fired — covered by
        the ``max(0.0, ...)`` clamp inside :func:`format_duration`).
        """
        if self._t0 is None:
            return 0.0
        return time.perf_counter() - self._t0


# ─── SIGTERM handler (POSIX) ───────────────────────────────────────────


def _install_sigterm_handler(stop_event: asyncio.Event, shutdown_timer: _ShutdownTimer) -> None:
    """Install a SIGTERM handler that initiates graceful shutdown (POSIX only).

    Uses :meth:`asyncio.AbstractEventLoop.add_signal_handler` (the
    asyncio-idiomatic way) so the handler runs INSIDE the event loop
    thread, NOT in the signal-handler interrupt context. This matters
    because ``asyncio.Event.set()`` is not safe to call from a signal
    handler directly (it doesn't acquire the loop's internal lock).

    On SIGTERM: ``shutdown_timer.start()`` captures the shutdown
    wall-clock t0, then ``stop_event.set()`` unblocks
    :func:`run_worker_server`'s ``await stop_event.wait()`` so the
    worker exits cleanly and ``run()``'s ``finally`` block runs
    ``lock_handle.release()`` + emits the SHUTDOWN log line with the
    measured duration.

    On Windows the Tauri host uses ``taskkill`` (no SIGTERM equivalent);
    the asyncio loop is cancelled via the ``shutdown`` command instead.
    The handler is best-effort: if the OS does not deliver the signal
    (e.g. the process is in a C extension call), the host's
    kill-children backstop still terminates the process.
    """
    import asyncio

    if not hasattr(signal, "SIGTERM"):
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — called outside ``asyncio.run``. Fall back
        # to the signal-handler-in-interrupt-context path. This is
        # safe-ish because ``stop_event.set()`` is the only thing the
        # handler does, and on CPython ``asyncio.Event.set()`` is
        # implemented as ``self._value = True`` + waking waiters via
        # ``self._loop.call_soon(...)``. The ``call_soon`` IS thread-
        # safe (it acquires the loop's internal lock), so calling it
        # from a signal handler is technically OK but produces a
        # DeprecationWarning on Python 3.10+. The fallback is here
        # for defensive reasons (e.g. a future test that calls
        # ``run()`` outside ``asyncio.run``); the production path
        # always uses the loop-aware branch above.
        def _on_sigterm_fallback(_signum: int, _frame: FrameType | None) -> None:
            log.info("[WORKER] SIGTERM received — initiating graceful shutdown (fallback)")
            shutdown_timer.start()
            with contextlib.suppress(Exception):
                stop_event.set()

        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGTERM, _on_sigterm_fallback)
        return

    def _on_sigterm() -> None:
        log.info("[WORKER] SIGTERM received — initiating graceful shutdown")
        shutdown_timer.start()
        stop_event.set()

    with contextlib.suppress(NotImplementedError, RuntimeError):
        # ``add_signal_handler`` raises NotImplementedError on Windows
        # (ProactorEventLoop does not support it) — the
        # shutdown-command path is the worker's shutdown mechanism
        # there.
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)


# ─── Connection handler ───────────────────────────────────────────────


async def _handle_connection(  # noqa: ANN001 - websockets type is imported lazily
    websocket,
    *,
    prewarm_ran: bool,
    stop_event: asyncio.Event,
    shutdown_timer: _ShutdownTimer,
) -> None:
    """Handle one WS connection from the slim-core sidecar.

    Authenticate, acknowledge heartbeats, dispatch ``shutdown``, and
    dispatch ``transcribe_offline`` (real ASR — the inference runs in
    a thread via :func:`get_transcriber`, the result is pushed back as
    ``transcribe_offline_result``). Unknown commands get an
    ``error`` envelope with ``code: "unknown_command"``.

    On ``shutdown``: send ``shutdown_ack``, mark the shutdown timer's
    start, set ``stop_event`` (so :func:`run_worker_server`'s
    ``await stop_event.wait()`` unblocks), then close the socket. The
    ``stop_event.set()`` MUST be called BEFORE ``websocket.close()`` so
    the worker does not hang forever waiting for a WS-close event the
    asyncio loop never delivers (regression: the previous code skipped
    ``stop_event.set()`` and the worker hung after every shutdown
    command — see ``test_shutdown_command_exits_worker``).
    """

    # Reject browser origins (defense-in-depth; the worker should only
    # ever be connected to by the slim-core sidecar's WS client, never
    # by a browser tab).
    origin = getattr(websocket, "origin", None) or ""
    if origin and origin not in ("", "null"):
        log.warning("[WORKER] rejecting connection with origin=%r (browser origins not allowed)", origin)
        await websocket.close(code=1008)
        return

    if not await _authenticate(websocket):
        await _send_auth_failed_and_close(websocket)
        return

    peer = getattr(websocket, "remote_address", None) or ("?", 0)
    log.info("[WORKER] slim-core sidecar connected from %s:%s (prewarm_ran=%s)", peer[0], peer[1], prewarm_ran)

    try:
        async for raw in websocket:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                frame = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                log.warning("[WORKER] non-JSON frame from slim-core sidecar — ignoring")
                continue
            if not isinstance(frame, dict):
                continue
            cmd = frame.get("cmd") or frame.get("type")
            if cmd == "heartbeat":
                # Acknowledge heartbeats so the slim-core sidecar's
                # liveness probe sees a live worker (master plan §7.2).
                with contextlib.suppress(Exception):
                    await websocket.send(json.dumps({"type": "heartbeat_ack"}))
                continue
            if cmd == "shutdown":
                log.info("[WORKER] shutdown command received — exiting gracefully")
                with contextlib.suppress(Exception):
                    await websocket.send(json.dumps({"type": "shutdown_ack"}))
                # Mark the shutdown timer BEFORE stop_event.set() so
                # the duration covers the full shutdown sequence
                # (ack send + socket close + asyncio loop drain +
                # lock release). C-LOG-2.
                shutdown_timer.start()
                # Unblock run_worker_server's await stop_event.wait()
                # BEFORE closing the socket so the worker does not
                # hang waiting for a WS-close event the asyncio loop
                # never delivers.
                stop_event.set()
                # Trigger loop cancellation by closing the socket.
                with contextlib.suppress(Exception):
                    await websocket.close()
                return
            if cmd == "transcribe_offline":
                # Master plan §7.4 — real offline ASR in the worker.
                # The slim-core sidecar forwards ``{audio_path,
                # sample_rate, language}``; the worker transcribes and
                # pushes the result back via the
                # ``transcribe_offline_result`` event. The inference is
                # blocking C-level work — run it in a thread so
                # heartbeats + shutdown stay responsive mid-inference.
                data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
                audio_path = str(data.get("audio_path") or "")
                sample_rate = data.get("sample_rate")
                language = data.get("language")
                log.info(
                    "[WORKER] transcribe_offline request (path=%s, sr=%s, lang=%s) — running in thread",
                    audio_path, sample_rate, language,
                )
                import asyncio as _asyncio

                # Bind the loop variables into the closure's defaults so
                # the thread function does not capture the loop variables
                # by reference (B023 — the connection handler's frame
                # loop mutates them on each iteration).
                def _run(
                    _path: str = audio_path,
                    _sr: object = sample_rate,
                    _lang: object = language,
                ) -> dict:
                    from voice_typer.worker._transcribe import get_transcriber

                    return get_transcriber().transcribe_file(
                        _path,
                        int(_sr) if isinstance(_sr, (int, str)) and _sr not in (None, "") else None,
                        str(_lang) if _lang is not None else None,
                    )

                try:
                    result = await _asyncio.to_thread(_run)
                except Exception as exc:  # noqa: BLE001 — never drop a result event
                    log.exception("[WORKER] transcribe_offline thread raised: %s", exc)
                    result = {"text": "", "error": f"internal error: {exc}"}
                with contextlib.suppress(Exception):
                    await websocket.send(
                        json.dumps({"type": "transcribe_offline_result", "data": result})
                    )
                continue
            # Unknown command.
            log.debug("[WORKER] unknown command %r", cmd)
            with contextlib.suppress(Exception):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {
                                "code": "unknown_command",
                                "message": f"unknown command: {cmd!r}",
                            },
                        }
                    )
                )
    except Exception:
        log.debug("[WORKER] connection handler exited with exception", exc_info=True)


# ─── Worker run loop ──────────────────────────────────────────────────


async def run_worker_server(  # noqa: ANN001 - websockets type is imported lazily
    *,
    prewarm_elapsed: float,
    prewarm_ran: bool,
    stop_event: asyncio.Event,
    shutdown_timer: _ShutdownTimer,
) -> bool:
    """Bind the WS server on an ephemeral port and run until ``stop_event`` is set.

    Returns ``True`` on clean shutdown (``stop_event`` was set by the
    shutdown command / SIGTERM / KeyboardInterrupt), ``False`` if the
    WS server failed to bind any socket.

    Sequence (master plan §7.3):

    1. Install the SIGTERM handler (POSIX) — sets ``stop_event`` on signal.
    2. Bind ``127.0.0.1:0`` (loopback-only, ADR-0020 §1) via
       ``websockets.asyncio.server.serve`` with the 1 MiB frame cap.
    3. Print ``{"event":"worker_started","port":N,"protocol":P}`` to stdout.
    4. Block on ``await stop_event.wait()`` until graceful shutdown.
    5. ``async with serve()`` exits cleanly (websockets' default
       close_timeout drains in-flight handlers).

    Mirrors :func:`voice_typer.server.sidecar_ws.run`'s shape so the
    two entry points read identically.
    """

    from websockets.asyncio.server import serve

    _install_sigterm_handler(stop_event, shutdown_timer)

    # bind on 127.0.0.1:0 → OS assigns an ephemeral port. max_size
    # enforces the 1 MiB frame cap (ADR-0020 §10). The handler is a
    # closure so it can carry the ``prewarm_ran`` flag, ``stop_event``,
    # and ``shutdown_timer`` without globals.
    #
    # NOTE: ``websockets.asyncio.server.serve`` does NOT accept a
    # ``max_connections`` kwarg (unlike the legacy
    # ``websockets.server.serve``). Connection-limiting is done inside
    # ``_handle_connection`` via the auth gate — the worker should
    # only ever have ONE authenticated client (the slim-core sidecar),
    # so a semaphore is unnecessary; a second client attempting auth
    # with the same token is rejected at the auth step (the slim-core
    # sidecar's respawn scheduler guarantees at most one sidecar is
    # alive at a time).
    async def _handler(websocket) -> None:  # noqa: ANN001
        await _handle_connection(
            websocket,
            prewarm_ran=prewarm_ran,
            stop_event=stop_event,
            shutdown_timer=shutdown_timer,
        )

    async with serve(
        _handler,
        LOOPBACK_HOST,
        0,
        max_size=_MAX_FRAME_BYTES,
    ) as ws_server:
        socks = ws_server.sockets
        first_sock = next(iter(socks), None)
        if first_sock is None:
            log.error("[WORKER] no sockets bound — aborting")
            return False
        port = first_sock.getsockname()[1]
        _emit_worker_started(port, PROTOCOL_VERSION)
        log.info(
            "[WORKER] listening on %s:%d (prewarm ran in %s)",
            LOOPBACK_HOST,
            port,
            format_duration(prewarm_elapsed),
        )

        # Run until SIGTERM (stop_event) or the asyncio loop is
        # cancelled by the shutdown command (which calls stop_event.set()).
        await stop_event.wait()

    return True
