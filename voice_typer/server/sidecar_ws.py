"""Tauri sidecar WebSocket transport — server side.

ADR-0020 §1 + §2: this module turns the existing :class:`IPCServer`
dispatch layer into a localhost WebSocket server so the Tauri Rust
host can connect to it as a WS client.

Architecture
------------
::

    Tauri host (Rust)
        │  spawns sidecar via externalBin
        │  passes VOICE_TYPER_IPC_TOKEN env
        ▼
    sidecar_main.py (this module's run() entrypoint)
        │  binds websockets.serve on 127.0.0.1:0
        │  OS assigns an ephemeral port
        │  writes ONE structured line to stdout:
        │     {"event":"server_started","port":<n>}
        ▼
    Rust reads stdout, parses the JSON, opens a WS client to
    ws://127.0.0.1:<n>, sends the HMAC auth frame, then forwards
    invoke('dispatch', {cmd, data}) envelopes over the WS.

Why a separate module (not a flag on ipc_server.py)?
----------------------------------------------------
- The TCP path (ipc_server._accept_tcp) and the WS path share the
  same _COMMAND_REGISTRY + _dispatch + _validate_dict_payload, but
  the listen/accept loops are completely different (raw socket vs
  asyncio websockets). Putting them in the same function would be a
  parallel-systems hazard.
- This module is additive: the TCP path stays intact for the
  Electron fallback, the WS path is opt-in via ``--ws`` on
  ``ipc_server.py`` (which delegates here).
- The dispatch + handler mixins are 100% reused — only the transport
  changes (per ADR-0020 §2).

Cross-platform
--------------
- ``websockets.serve`` binds on 127.0.0.1:0 on all three platforms
  (Windows/macOS/Linux). The OS assigns the port.
- stdout is force-set to line-buffered at the top of :func:`run` so
  the ``server_started`` JSON is flushed immediately — the host's
  pipe would otherwise block-buffer it and the host would hang
  waiting for the line (ADR-0020 §1 Phase-0 blocker).
- All non-handshake logs go to **stderr** (or the rotating file log
  via ``log.py``), never stdout — keeps the stdout JSON protocol
  unambiguous.

Rate limiting
-------------
ADR-0019's rate limiter
(:class:`voice_typer.server.ipc_server._RateLimiter`) is applied on
every incoming WS frame, mirroring the TCP path. A client that
exceeds 200 burst / 600 sustained (10s window, per
RELIABILITY-006-FIX-10) gets ``{"type":"error","code":
"rate_limited","data":{"message":"rate limit exceeded; backing
off"}}`` and the connection stays open.

CR-11: the limiter is shared across ALL WS connections to this server
process (looked up via ``_get_rate_limiter(server)``), so a local
attacker can no longer reset the 200-message burst budget by dropping
the WS and reconnecting.

Heartbeat
---------
ADR-0018's Python-side heartbeat watchdog is DISABLED on the Tauri
path via the ``TAURI_SIDECAR=1`` env var (see ``ipc_server.py``).
Liveness is instead owned by the Rust host, which combines two
detection mechanisms:

1. **Transport-level**: WS-close or process exit triggers supervisor
   respawn (ADR-0020 §10).
2. **Application-level**: the Rust host dispatches a ``heartbeat``
   command every 10s (handled here in Python by
   ``_handle_heartbeat``, registered in ``_COMMAND_REGISTRY``). On
   3 consecutive misses (≥30s of unresponsiveness — socket open but
   no response, e.g. GIL contention / infinite loop / blocking C
   call), the Rust host triggers respawn. This catches sidecar
   hangs that keep the TCP/WS socket open but don't respond to
   dispatches — a scenario the WS-close-only detection misses.

Together these replace the Electron path's 120-second-heartbeat-
timeout watchdog with a faster, more accurate liveness probe.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

# websockets is a hard new dep under ADR-0020 §14. Import lazily
# inside run() so the module imports cleanly without the dep
# installed (e.g. on the Electron-only build path); the runtime
# check produces a clean ImportError with an actionable message.
#
# The typed close-exception classes
# (``ConnectionClosedOK`` / ``ConnectionClosedError``) are imported
# lazily at the top of ``_handle_connection`` (NOT at module top) so
# this module still imports cleanly on the Electron-only build path.
# ``_handle_connection`` is only called by ``serve()`` which is set
# up by ``run()`` AFTER the lazy websockets import there has already
# succeeded, so the inner import is guaranteed to succeed at runtime.

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

# DT-11: import the canonical loopback host constant from _paths.py
# (was a local `_LOOPBACK_HOST = "127.0.0.1"` literal duplicated across
# _http_safety.py, _secrets.py, and this module). Aliased to the
# underscore-prefixed name so existing call sites (e.g. `serve(...,
# _LOOPBACK_HOST, ...)`) keep working unchanged.
from voice_typer.server._paths import LOOPBACK_HOST as _LOOPBACK_HOST

log = logging.getLogger("voice_typer.server.sidecar_ws")

# ADR-0020 round-2 fix: the `ready` event is emitted only once per
# IPCServer instance, on the first authenticated WS connection.
# Previously ipc_server.py:main() called `server.push({"type": "ready"})`
# BEFORE sidecar_ws.run() started the WS server — so the event was
# dropped (no subscriber yet). Now we emit `ready` via event_bus.publish
# AFTER the first client authenticates, so the Tauri host receives it
# over the WS and can hydrate the UI.
#
# CR-4: this flag USED to be a module-level global (`_ready_emitted`).
# That was correct for production (one ready event per process), but
# never reset between test runs that import the module once and call
# `run()` multiple times with different IPCServer instances — so the
# second `run()` would not emit `ready` even with a fresh server. The
# flag is now a per-instance attribute on the IPCServer
# (`server._ready_emitted`, initialized to False in
# `IPCServer.__init__`); see `_handle_connection` for the read/write
# site and `IPCServer._reset_ready_emitted()` for the test-only helper.

# Hard loopback-only bind (ADR-0020 §1). Binding 0.0.0.0 / :: would
# (a) pop a Windows Defender Firewall prompt, (b) trigger an macOS
# Application Firewall prompt, (c) expose the authed-but-localhost
# IPC to the LAN. Fail the launch if the configured bind is not
# loopback.
#
# DT-11: ``_LOOPBACK_HOST`` is now imported from ``_paths.py`` (see
# the import statement near the top of this module) so the same
# constant is shared with ``_http_safety.py`` and ``_secrets.py``.

# ADR-0020 §10: 1 MiB WS frame cap. download_progress and
# vocabulary_suggestion can carry large payloads; without a cap a
# malformed/huge frame can OOM the client.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Auth frame timeout (seconds). A client that connects but never
# sends the auth frame must not hold the connection indefinitely —
# matches the TCP path's 5-second auth timeout (PR-3-FIX-1).
#
# DEDUP TRACKING: the TCP path has a local
# ``_tcp_auth_timeout_seconds = 5.0`` in
# ``ipc/transport_tcp.py::_handle_tcp_connection`` (line ~278).
# The two transports MUST agree on the auth-deadline budget — if one
# is changed, the other MUST be updated to match.  A future refactor
# could extract this constant to a shared ``ipc/auth.py`` module
# (see S2-CR-1).
_AUTH_TIMEOUT_SECONDS = 5.0

# XZ-IPC-003: concurrent-connection limit (DoS protection).
_MAX_WS_CONNECTIONS = 16

# Cooperative shutdown hard timeout (ADR-0020 §10). When the host
# sends {"type":"shutdown"} the sidecar must release the mic, ack,
# and exit within this window; if it doesn't, the host force-kills
# the process tree via kill_children.
#
# DT-54: the previous ``_SHUTDOWN_ACK_TIMEOUT_SECONDS = 2.0`` constant
# was dead code — referenced nowhere in this module and misleadingly
# suggested Python enforces the timeout. The Rust host's
# ``SHUTDOWN_ACK_TIMEOUT_MS = 2000`` (in ``src-tauri/src/util.rs``)
# is the single source of truth for the cooperative-shutdown hard
# timeout; Python does NOT mirror it. See
# ``src-tauri/src/util.rs::SHUTDOWN_ACK_TIMEOUT_MS`` for the canonical
# value.

# S2-CR-72 (SA-6): Tauri sidecar handshake protocol-version constant.
#
# The sidecar emits ``"protocol": PROTOCOL_VERSION`` in its
# ``server_started`` JSON line so the Rust host can detect version
# skew at handshake time (before any command dispatch) and surface a
# clear ``protocol_mismatch`` error rather than producing confusing
# partial failures (some commands work, others return
# ``unknown_command``, push events have unexpected ``type`` values).
#
# The Rust host's ``EXPECTED_PROTOCOL`` constant in
# ``src-tauri/src/sidecar/spawn.rs`` MUST match this value. Bump this
# integer whenever the ``_COMMAND_REGISTRY`` (in
# ``voice_typer/server/ipc_server.py``) adds/removes/renames a
# command OR the push-event ``type`` vocabulary changes — both are
# observable contracts the host depends on. The version is monotonic
# and never reused.
#
# History:
#   - 1 (SA-6, this run): initial protocol-version negotiation. The
#     pre-negotiation sidecar emitted only ``{"event":"server_started",
#     "port":<n>}``; old hosts that don't yet parse the ``protocol``
#     field continue to function (the field is additive).
PROTOCOL_VERSION: int = 1


def _force_line_buffered_stdout() -> None:
    """Force stdout to line buffering (ADR-0020 §1 Phase-0 blocker).

    When the Tauri host pipes the sidecar's stdout, CPython switches
    to block buffering, so the ``server_started`` JSON is held in
    the buffer and the host hangs forever waiting. ``reconfigure``
    flips the stream back to line buffering so each ``\\n`` flushes.

    Python 3.7+ supports ``sys.stdout.reconfigure``; we guard for
    older interpreters (the project floor is 3.10 per pyproject.toml
    so this is always available, but the guard is defensive).
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        # Fallback: reopen stdout with buffering=1 (line-buffered).
        # This loses the original fd's write-only-on-flush semantics
        # but is the standard pattern for unbuffered stdio.
        with contextlib.suppress(Exception):
            sys.stdout = open(  # noqa: SIM115 - intentional reopen
                sys.stdout.fileno(),
                "w",
                buffering=1,
                encoding="utf-8",
                closefd=False,
            )


def _emit_server_started(port: int, protocol: int | None = None) -> None:
    """Write the one structured stdout line the host is parsing for.

    Per ADR-0020 §1, this is the ONLY thing that ever goes to stdout
    from the sidecar. Every other log goes to stderr / the rotating
    file log. The host blocks reading stdout until it parses this
    JSON, then opens a WS client to ws://127.0.0.1:<port>.

    S2-CR-72 (SA-6): when ``protocol`` is not ``None``, the payload
    additionally includes ``"protocol": <int>`` so the Rust host can
    detect version skew at handshake time. The Rust host's
    ``EXPECTED_PROTOCOL`` constant in
    ``src-tauri/src/sidecar/spawn.rs`` MUST match
    :data:`PROTOCOL_VERSION`; on mismatch, the host logs a clear
    ``protocol_mismatch`` error and refuses to spawn. Callers in this
    module always pass :data:`PROTOCOL_VERSION`; the ``None`` default
    is preserved for backward compatibility with pre-negotiation tests
    that assert the exact two-field payload shape and with any
    hypothetical external caller of this helper (none exist in the
    codebase today, but the default keeps the function safe to call
    without forcing the caller to know the current protocol integer).
    """
    payload: dict[str, int | str] = {"event": "server_started", "port": int(port)}
    if protocol is not None:
        payload["protocol"] = int(protocol)
    print(json.dumps(payload), flush=True)


async def _authenticate(websocket) -> bool:
    """Read the first WS frame and validate the HMAC token.

    Per ADR-0020 §3, the client's first frame must be::

        {"type": "auth", "token": "<token>"}

    The token is compared with :func:`hmac.compare_digest` (constant
    time) against the ``VOICE_TYPER_IPC_TOKEN`` env var set by the
    Rust host at spawn. On mismatch, the socket is closed immediately
    and the connection is rejected (the host treats this as a crash
    → respawn with a fresh token, ADR-0020 §10).

    Returns ``True`` if authenticated, ``False`` if rejected.

    DEDUP (S2-CR-1)
    ----------------
    This function mirrors the TCP auth handshake in
    ``ipc/transport_tcp.py::_handle_tcp_connection`` (the
    ``if expected_token:`` block at ~L300-365).  BOTH transports
    implement the same contract:

    - Read the first frame/line.
    - Parse JSON.
    - Validate ``type == "auth"`` + ``isinstance(token, str)``.
    - ``hmac.compare_digest(token, expected_token)`` (constant-time).
    - Emit ``{"code":"auth_failed","message":"authentication failed"}``
      envelope on mismatch.
    - 5-second auth deadline.

    Differences are transport-primitive only (``websocket.recv()`` +
    ``asyncio.wait_for`` vs ``_TCPLineIO.readline()`` +
    ``conn.settimeout``).  Bug fixes to the validation contract MUST
    be applied to BOTH call sites.  A future extraction to a shared
    ``ipc/auth.py`` helper is tracked under S2-CR-1.
    """
    expected_token = os.environ.get("VOICE_TYPER_IPC_TOKEN", "")
    if not expected_token:
        log.error(
            "[SIDECAR-WS] VOICE_TYPER_IPC_TOKEN not set — refusing to "
            "accept connections (the host must always set this env var)."
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=_AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning("[SIDECAR-WS] auth frame timeout — closing connection")
        return False
    except Exception:
        log.warning("[SIDECAR-WS] auth frame read failed", exc_info=True)
        return False

    try:
        if isinstance(first_raw, bytes):
            first_raw = first_raw.decode("utf-8")
        first = json.loads(first_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("[SIDECAR-WS] auth frame is not valid JSON")
        return False

    if not isinstance(first, dict) or first.get("type") != "auth":
        log.warning("[SIDECAR-WS] first frame is not an auth frame")
        return False

    provided = first.get("token", "")
    if not isinstance(provided, str) or not provided:
        log.warning("[SIDECAR-WS] auth frame missing token")
        return False

    if not hmac.compare_digest(provided, expected_token):
        log.warning("[SIDECAR-WS] auth token mismatch — rejecting")
        return False

    log.info("[SIDECAR-WS] auth accepted")
    return True


def _make_dispatch(server: IPCServer):
    """Build a coroutine that dispatches a single WS frame.

    Reuses ``server._dispatch`` (the same path the TCP loop uses),
    so the 61-command registry + _validate_dict_payload + every
    handler mixin is exercised unchanged (ADR-0020 §2).
    """
    # ADR-0019 + CR-11: per-process rate limiter. Reuse the same private
    # _RateLimiter class the TCP path uses (ipc_server.py:215) so the
    # burst/sustained semantics are identical — 200 burst, 600 sustained
    # over a 10s window (RELIABILITY-006-FIX-10).
    #
    # CR-11: the limiter is looked up lazily via _get_rate_limiter(server)
    # so it is shared across ALL WS connections to this server process.
    # A local attacker can no longer reset the 200-message burst budget
    # by dropping the WS and reconnecting — the 10s sliding window
    # continues to evict old timestamps across reconnects.
    # G4-H-30: dedicated ThreadPoolExecutor for WS dispatch so
    # ``_do_cleanup`` can drain / cancel in-flight dispatch requests
    # BEFORE tearing down the recorder / history DB / crash-recovery
    # writer. Previously ``loop.run_in_executor(None, server._dispatch,
    # msg)`` used the asyncio loop's default executor, which has no
    # handle the shutdown path can reach — a long-running handler
    # (e.g. ``download_model``) would race teardown, half-flush the
    # history DB, and leak a partially-written crash-recovery snapshot.
    #
    # DEDUP (S2-CR-1): the rate-limiter import is intentionally from
    # ``ipc_server`` (NOT from the leaf ``voice_typer.server.ipc.rate_limiter``).
    # ``_get_rate_limiter`` is defined LOCALLY in ``ipc_server.py`` (not
    # just re-exported) so it resolves ``_RateLimiter`` against
    # ``ipc_server``'s module globals at call time.  Tests that
    # monkey-patch ``ipc_server._RateLimiter`` observe the patched class
    # through this import (see ``tests/test_r4_f18_rate_limiter_concurrent_init.py``
    # and ``tests/test_cr_fixes.py``).  Changing the import to the leaf
    # module would BREAK the test monkey-patch contract.  The TCP path
    # (``ipc/transport_tcp.py``) also imports from ``ipc_server`` for
    # the same reason.
    #
    # Stored on the server instance (not the closure) so
    # ``ShutdownController._do_cleanup`` can reach it via
    # ``app._ipc_server._ws_dispatch_pool``. Lazily created on first
    # dispatch (the WS path may never be entered if the server runs in
    # TCP / standalone mode). Idempotent: the second call to
    # ``_get_ws_dispatch_pool`` returns the existing pool.
    from concurrent.futures import ThreadPoolExecutor

    from voice_typer.server.ipc_server import _get_rate_limiter

    ws_dispatch_pool = getattr(server, "_ws_dispatch_pool", None)
    if ws_dispatch_pool is None:
        ws_dispatch_pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-dispatch",
        )
        # ``setattr`` on a real IPCServer stores the attribute; on a
        # MagicMock test double it overrides the auto-vivified child.
        server._ws_dispatch_pool = ws_dispatch_pool  # type: ignore[attr-defined]

    async def dispatch(msg: dict, websocket) -> dict | None:
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical) + legacy alias
                    # (one-release compat) — see
                    # ``voice_typer/server/ipc/validation.py`` for the
                    # migration contract.
                    "code": "client.invalid_payload",
                    "legacy_code": "invalid_payload",
                    "message": "missing 'type'",
                },
            }

        # G4-H-30: cooperative shutdown gate. Once ``app._shutting_down``
        # is True (set by ``ShutdownController.quit()`` before
        # ``_do_cleanup()`` runs), reject every new dispatch request
        # with a structured ``server.shutting_down`` error code so the
        # host can re-queue / surface a graceful "backend is exiting"
        # message instead of starting a long-running handler (e.g.
        # ``download_model``) that would race teardown. The
        # ``shutdown`` message itself is exempt — the host sends it to
        # TRIGGER shutdown, and it is now handled by the shared
        # ``_COMMAND_REGISTRY`` entry ``"shutdown": "_handle_shutdown"``
        # (registered in ipc_server.py by EC-FIX-2) which delegates to
        # ``service.quit()`` — the SAME path the TCP ``quit_app``
        # command uses. Pre-EC-FIX-3 the WS path special-cased
        # ``shutdown`` here and called ``server.app.quit()`` directly,
        # bypassing the service layer (so any future shutdown
        # side-effect added to ``service.quit()`` silently wouldn't run
        # on Tauri). The special-case is now removed; ``shutdown``
        # flows through ``server._dispatch`` like every other command.
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False) is True:
            log.debug("[SIDECAR-WS] rejecting %s — server shutting down", msg_type)
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # ADR-0019 + CR-11 rate limit check. Look up the shared limiter
        # on every call (cheap — dict-style getattr) so all WS frames to
        # this server share the same sliding-window budget. _RateLimiter
        # .allow() returns a bool (no retry-after); the host backs off
        # via backoff on repeated rate-limit hits.
        rate_limiter = _get_rate_limiter(server)
        #
        # G4-M-09: pass ``command=msg_type`` so the per-command cost map
        # (``COMMAND_COSTS``) is applied — e.g. ``download_model``
        # consumes 50 of the 200 burst units, so a buggy client can fire
        # at most 4 expensive commands per second before the 5th is
        # rejected. Cheap commands (``heartbeat``, ``get_status``) keep
        # the pre-G4-M-09 cost-1 behavior. The legacy
        # ``rate_limiter.allow()`` form (no ``command`` kwarg) is still
        # supported and treats the call as cost 1.
        if not rate_limiter.allow(command=msg_type):
            # SEC-6: allow() already increments _rejected atomically when
            # it returns False — the separate .reject() call was removed
            # to eliminate the benign race where two threads could both
            # observe the same deque state, both decide to reject, and
            # double-count the rejection. This keeps WS-path rejected_count
            # consistent with the TCP path (both count via allow()).
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical) + legacy alias.
                    "code": "client.rate_limited",
                    "legacy_code": "rate_limited",
                    "message": "rate limit exceeded; backing off",
                },
            }

        # Dispatch on the worker thread pool so a slow handler
        # (e.g. download_model) doesn't block the WS reader.
        loop = asyncio.get_running_loop()
        try:
            # G4-H-30: use the dedicated ``_ws_dispatch_pool`` (not the
            # asyncio default executor) so ``ShutdownController._do_cleanup``
            # can ``pool.shutdown(wait=False, cancel_futures=True)`` to
            # drain / cancel in-flight handlers before recorder / history
            # DB / crash-recovery teardown.
            result = await loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg)
        except Exception:
            log.exception("[SIDECAR-WS] _dispatch raised")
            # IPC-5 (2026-07-18): the error envelope now matches the
            # TCP path (``ipc_server._handle_tcp_connection``'s
            # ERR-018 block) verbatim — same ``code`` AND same
            # ``message`` ("internal error"). Pre-IPC-5 the WS path
            # used the message "dispatch raised" while TCP used
            # "internal error"; both messages were generic (neither
            # leaked ``str(exception)``) but the divergence meant a
            # client could not use the message text to confirm parity.
            # The contract: ``{"type":"error","data":{"code":
            # "server.internal_error","message":"internal error"}}``.
            #
            # EC-FIX-3 (EC-10): the ``code`` was migrated from the
            # legacy ``"internal_error"`` to the namespaced
            # ``"server.internal_error"`` form (matching the
            # ``ERROR_CODES`` registry in ``ipc/validation.py``). The
            # renderer accepts both forms (legacy treated as alias),
            # so this is a backward-compatible migration. EC-FIX-2
            # applies the same migration to the TCP path's
            # ``internal_error`` emissions.
            return {
                "type": "error",
                "data": {"code": "server.internal_error", "message": "internal error"},
            }

        # _dispatch returns None for fire-and-forget commands (e.g.
        # restart_app, which sends its own response). Don't send a
        # frame in that case.
        return result

    return dispatch


def _enqueue_safe(outbound: asyncio.Queue, event: dict) -> None:
    """Drop-oldest enqueue — MUST run on the event-loop thread.

    CR-4: ``_push_to_ws`` is an ``event_bus`` subscriber, so it is
    invoked from whatever thread called ``event_bus.publish()``. The
    publishers are non-event-loop threads: transcription, hotkey, tray,
    IPC dispatch workers (``_dispatch`` runs via
    ``loop.run_in_executor``), and the audio-worker deferred path.
    ``asyncio.Queue`` is explicitly NOT thread-safe — mutating its
    internal deque + ``_getawaiter`` / ``_putawaiter`` futures from a
    non-loop thread corrupts state. Symptoms seen pre-fix: silently
    dropped events (transcription_final never reached the Tauri host),
    a deadlocked writer task (``await outbound.get()`` never wakes
    after a cross-thread ``put_nowait``), or a hard asyncio loop
    crash killing the sidecar (→ respawn loop).

    This helper does the drop-oldest dance (``full`` / ``get_nowait``
    / ``put_nowait``). It is marshaled onto the event-loop thread via
    ``loop.call_soon_threadsafe`` from ``_push_to_ws``, so every
    queue mutation happens on the loop thread and the asyncio
    invariants are preserved.

    Notes
    -----
    - ``call_soon_threadsafe`` is the documented asyncio API for
      cross-thread wakeup. It schedules the callback on the loop's
      ready queue and wakes the loop's selector if it is blocked in
      ``select()``. This is the same primitive ``asyncio.run_coroutine_threadsafe``
      is built on.
    - Drop-oldest (not drop-newest) is preserved so a slow host
      receives the most RECENT state snapshots (bubble_level,
      transcription_final) rather than stale ones buffered behind
      the drop. The host recovers consistency via state_changed
      re-snapshots on reconnect.
    """
    if outbound.full():
        try:
            outbound.get_nowait()
            log.debug("[SIDECAR-WS] outbound queue full — dropped oldest event")
        except asyncio.QueueEmpty:
            pass
    try:
        outbound.put_nowait(event)
    except asyncio.QueueFull:
        log.warning("[SIDECAR-WS] outbound queue still full — dropping event")


def _get_ws_connection_semaphore(server: IPCServer) -> asyncio.Semaphore:
    """XZ-IPC-003: lazily create / return the per-server connection semaphore."""
    sem = getattr(server, "_ws_connection_semaphore", None)
    if not isinstance(sem, asyncio.Semaphore):
        sem = asyncio.Semaphore(_MAX_WS_CONNECTIONS)
        with contextlib.suppress(Exception):
            server._ws_connection_semaphore = sem  # type: ignore[attr-defined]
    return sem


async def _handle_connection(websocket, server: IPCServer, dispatch) -> None:
    """Per-connection WS handler: auth + read/dispatch loop.

    XZ-IPC-003: enforces a concurrent-connection cap via a per-server
    asyncio.Semaphore BEFORE delegating to _handle_connection_inner.
    """
    peer = websocket.remote_address
    log.info("[SIDECAR-WS] client connected from %s", peer)

    sem = _get_ws_connection_semaphore(server)
    if sem.locked():
        log.warning(
            "[SIDECAR-WS] max_connections (%d) reached — rejecting %s "
            "with 1008 (XZ-IPC-003)",
            _MAX_WS_CONNECTIONS,
            peer,
        )
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": "max_connections_reached",
                            "message": "server at max simultaneous connections",
                        },
                    }
                )
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="max connections reached")
        return
    await sem.acquire()
    try:
        await _handle_connection_inner(websocket, server, dispatch, peer)
    finally:
        sem.release()


async def _handle_connection_inner(
    websocket, server: IPCServer, dispatch, peer
) -> None:
    """Auth + read/dispatch loop body (XZ-IPC-003 extraction)."""
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
    if not await _authenticate(websocket):
        # EC-11 (cross-transport parity): mirror the TCP path's
        # ``auth_failed`` error frame (ipc_server.py:~L925) BEFORE
        # closing the WS with code 1008. Pre-EC-FIX-3 the WS path
        # closed with 1008 and sent NO error frame, so the Rust host
        # (src-tauri/src/sidecar/ws.rs) and the Electron client had to
        # treat the close as an opaque auth failure with no envelope
        # detail. Now both transports emit the same
        # ``{"type":"error","data":{"code":"auth_failed",...}}`` frame
        # before closing — clients can branch on the error code without
        # sniffing the close reason. Wrapped in ``contextlib.suppress``
        # because the socket may already be half-closed (e.g. the
        # client RST'd after sending the bad token); the close call
        # below is the authoritative teardown.
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": "auth_failed",
                            "message": "authentication failed",
                        },
                    }
                )
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="auth failed")
        return

    # XZ-R18-06: enforce single-authenticated-connection invariant.
    # The host (Rust / Electron) uses respawn rather than reconnect —
    # a second authenticated WS implies a protocol bug (stale socket
    # in the host's connect loop, a race between supervisor respawn
    # and the old sidecar's accept loop, etc.). Both connections
    # would have separate outbound queues + writer tasks + event_bus
    # subscribers, causing duplicate event delivery (every
    # ``event_bus.publish`` reaches both writers). The cleaner fix is
    # to REJECT the new connection with 1008 ("Policy Violation") so
    # the existing authenticated connection continues uninterrupted.
    # The host's reconnect logic treats 1008 as a fatal-sidecar signal
    # and respawns, which is the correct response to a duplicate-auth
    # protocol bug. The previous connection is cleared from
    # ``server._active_ws_connection`` in the ``finally`` block below
    # (only if it still points at THIS socket — a race-safe compare).
    #
    # Defensive: ``server`` may be a ``MagicMock`` in tests, where
    # ``getattr(server, "_active_ws_connection", None)`` auto-vivifies
    # a child MagicMock (which would falsely trip the duplicate
    # check). The ``is_closed`` probe below treats a non-bool
    # ``.closed`` attribute (or any error reading it) as "closed" so
    # the invariant is enforced only against a REAL open websocket.
    with server._lock:
        existing = getattr(server, "_active_ws_connection", None)
    is_existing_open = False
    if existing is not None and existing is not websocket:
        try:
            is_existing_open = not bool(getattr(existing, "closed", True))
        except Exception:
            is_existing_open = False
    if is_existing_open:
        log.warning(
            "[SIDECAR-WS] duplicate authenticated connection from %s — "
            "an existing authenticated connection is already active; "
            "rejecting new connection with 1008 to preserve single-"
            "connection invariant (XZ-R18-06)",
            peer,
        )
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": "duplicate_connection",
                            "message": "another authenticated connection is already active",
                        },
                    }
                )
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="duplicate connection")
        return
    # Mark this as the active connection. Cleared in the ``finally``
    # block below (only if it still points at THIS socket — a concurrent
    # rejection path may have already swapped it).
    with server._lock:
        server._active_ws_connection = websocket

    # ADR-0020 round-2 fix: emit `ready` on the first authenticated
    # connection. The Tauri host waits for this event before hydrating
    # the UI (mirrors the Electron path's `ready` push at
    # ipc_server.py:1899). Using event_bus.publish (not server.push)
    # because the WS writer task subscribes to event_bus — server.push
    # would go to the TCP path's _tcp_client which is None in WS mode.
    #
    # CR-4: the flag is per-instance (``server._ready_emitted``), not
    # module-level, so each fresh ``IPCServer`` starts with the flag
    # False and emits `ready` on its first connection. This was
    # previously a module-level global which leaked state between test
    # runs that reused the same module. The flag is set under
    # ``server._lock``-free atomic read-then-write — single WS server
    # task, so the race is theoretical, but the worst case is a
    # duplicate `ready` event which the host tolerates.
    #
    # CR-83: the read-then-write is now guarded by ``server._lock``
    # (an RLock defined on IPCServer at __init__). Two concurrent
    # first-time authentications would otherwise both see
    # ``_ready_emitted == False`` and both publish ``ready``. The host
    # tolerates duplicates, but the duplicate broadcast is wasted work
    # and a minor protocol smell. The lock is also used elsewhere on
    # the server (e.g. _send / push), so this re-uses an existing
    # primitive rather than adding a new one.
    with server._lock:
        if getattr(server, "_ready_emitted", False):
            already_emitted = True
        else:
            already_emitted = False
            server._ready_emitted = True
    if not already_emitted:
        from voice_typer.server import event_bus

        log.info("[SIDECAR-WS] first authenticated connection — emitting `ready` event")
        event_bus.publish({"type": "ready"})

    # CR-79 / CR-37 / CR-30 / CR-4: ``_push_to_ws`` is registered as an
    # event_bus subscriber (sync API). It is invoked from WHATEVER
    # thread ``event_bus.publish`` runs on — typically a domain thread
    # (tray, transcription, dictation_pipeline, audio-worker, IPC
    # dispatch workers) that is NOT the asyncio loop thread.
    # ``asyncio.Queue`` is documented as NOT thread-safe; the GIL makes
    # immediate deque ops atomic but the ``_getters``/``_putters``
    # future-scheduling path can miss wakeups. Capture the running loop
    # ONCE here (EC-FIX-3 cleanup: previously re-captured three times
    # at L550/L560/L570 with a dead ``_ws_loop`` local — all redundant
    # since the loop is identical for the lifetime of this connection)
    # and close over it in ``_push_to_ws`` so the sync subscriber can
    # route all queue mutations through ``loop.call_soon_threadsafe``
    # (which is the documented way to bridge a sync caller to an
    # asyncio primitive from a non-loop thread).
    #
    # The previous ``server._ws_loop = loop`` write was removed — it
    # had zero production readers (the per-connection closure-captured
    # ``loop`` below is the only source of truth used by
    # ``_push_to_ws``), and writing it without ``server._lock``
    # created a race-on-write hazard for any future diagnostic reader.
    # The WS path runs ONE accept loop on ONE asyncio event loop, so
    # all connections share the same loop; if a future refactor
    # permits multiple loops, the closure-captured ``loop`` remains
    # the per-connection source of truth.
    loop = asyncio.get_running_loop()

    # Subscribe server.push (which forwards event_bus.publish) to
    # this WS so server-initiated events flow back to the host.
    # The TCP path installs a single _tcp_client and writes to it
    # under self._lock; the WS path uses a per-connection asyncio
    # Queue + a writer task so we don't block the dispatch loop.
    outbound: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)

    def _push_to_ws(event: dict) -> None:
        """Subscriber for event_bus.publish — enqueues for the writer task.

        CR-4 / PVT-G5-002: this subscriber is invoked synchronously in the
        publisher's thread (``event_bus._deliver`` calls ``fn(event)``
        directly, modulo the RT-thread deferred path). Because the
        publisher is typically a non-event-loop thread, we MUST NOT
        touch ``outbound`` (an ``asyncio.Queue``) here — ``asyncio.Queue``
        is documented as NOT thread-safe and direct mutation from a
        non-loop thread corrupts the queue's internal deque + Future
        state. Instead we schedule ``_enqueue_safe`` on the loop thread
        via ``call_soon_threadsafe`` — the only documented thread-safe
        way to hand work to an asyncio loop from outside it. The
        drop-oldest dance (``full`` / ``get_nowait`` / ``put_nowait``)
        lives in ``_enqueue_safe`` and runs entirely on the loop thread.

        PVT-028: removed the pre-marshaling queue-overflow check and
        the ``put_nowait`` fallback. They touched the asyncio.Queue from
        the publisher's thread — exactly the corruption ``_enqueue_safe``
        was created to prevent. ``_enqueue_safe`` already does the
        drop-oldest dance ON the loop thread. Also removed the dead
        ``except asyncio.QueueFull`` clause (``call_soon_threadsafe``
        never raises QueueFull).

        ``RuntimeError`` is raised by ``call_soon_threadsafe`` when
        the loop has been closed (process shutdown / respawn).
        The writer task has already been cancelled by the connection
        ``finally`` block, so there is no consumer for the event —
        drop silently at DEBUG level. This is the documented
        shutdown contract; we do NOT want a traceback per published
        event during teardown.
        """
        try:
            loop.call_soon_threadsafe(_enqueue_safe, outbound, event)
        except RuntimeError:
            log.debug("[SIDECAR-WS] event dropped during shutdown — event loop closed")

    from voice_typer.server import event_bus

    event_bus.subscribe(_push_to_ws)

    # EC-FIX-3 (EC-11 / ERR-017 parity): emit a ``state_changed``
    # snapshot on EVERY authenticated connection (not just the first
    # ``ready``). This mirrors the TCP path's connect-time snapshot at
    # ``ipc_server.py:_handle_tcp_connection`` (~L1003-1017) so a WS
    # reconnect after a transient drop immediately re-hydrates the
    # renderer's tray state badge instead of leaving it stale until
    # the next state transition.
    #
    # Placement: this is published AFTER ``_push_to_ws`` is registered
    # as an event_bus subscriber (L624 above) so the event flows
    # through the WS writer task's outbound queue to the host. The
    # ``ready`` emit at L527 is intentionally published BEFORE
    # ``_push_to_ws`` is registered (per ADR-0020 round-2 — see the
    # comment block above) and is delivered via ``server.push`` →
    # ``_pending_tcp`` flush; ``state_changed`` is published here so
    # it is GUARANTEED to reach the WS client on every auth.
    #
    # Defensive: the tray may not be initialized yet on the very first
    # connection (the app boots the IPC server before the tray icon is
    # constructed). ``getattr(..., None)`` + the ``is not None`` guard
    # skip the emit in that case — the host will pick up the next
    # state transition via the normal ``status_change`` hook.
    try:
        current_state = getattr(server.app.tray, "_state", None)
        current_msg = getattr(server.app.tray, "_message", "")
        if current_state is not None:
            event_bus.publish(
                {
                    "type": "state_changed",
                    "data": {
                        "status": getattr(current_state, "value", str(current_state)),
                        "message": current_msg,
                    },
                }
            )
    except Exception:
        log.debug(
            "[SIDECAR-WS] failed to emit initial state_changed on connect",
            exc_info=True,
        )

    async def _writer() -> None:
        """Drain the outbound queue and write each event as a WS frame."""
        try:
            while True:
                event = await outbound.get()
                if event is None:
                    return
                try:
                    raw = json.dumps(event, ensure_ascii=False)
                    if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:
                        log.error(
                            "[SIDECAR-WS] outbound frame exceeds %d bytes — dropping",
                            _MAX_FRAME_BYTES,
                        )
                        continue
                    await websocket.send(raw)
                except Exception:
                    log.warning("[SIDECAR-WS] send failed", exc_info=True)
                    return
        except asyncio.CancelledError:
            return

    writer_task = asyncio.create_task(_writer(), name="sidecar-ws-writer")

    try:
        async for raw in websocket:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            # NOTE: the inbound frame-size cap is enforced by the
            # `websockets` library itself via `serve(..., max_size=...)`
            # in run() below — the library rejects oversized frames at
            # the transport layer with a 1009 close. We do NOT re-check
            # here (it would be dead code; the frame never arrives if
            # it exceeds max_size).
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {
                                # Namespaced form (canonical) +
                                # legacy alias.
                                "code": "client.invalid_payload",
                                "legacy_code": "invalid_payload",
                                "message": "invalid JSON",
                            },
                        }
                    )
                )
                continue

            if not isinstance(msg, dict):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {
                                # Namespaced form (canonical) +
                                # legacy alias.
                                "code": "client.invalid_payload",
                                "legacy_code": "invalid_payload",
                                "message": "frame must be an object",
                            },
                        }
                    )
                )
                continue

            # The frame may carry an optional "id" for request/response
            # correlation (ADR-0020 §7 — the host's dispatch() command
            # assigns a per-request id). Echo it back on the response.
            request_id = msg.get("id")
            result = await dispatch(msg, websocket)
            if result is not None:
                if request_id is not None and isinstance(result, dict):
                    result = {**result, "id": request_id}
                try:
                    await websocket.send(json.dumps(result, ensure_ascii=False))
                except Exception:
                    log.warning("[SIDECAR-WS] response send failed", exc_info=True)
                    break
    except ConnectionClosedOK:
        # Clean WebSocket close (1000/1001 normal close) — log
        # at DEBUG. Previously the broad ``except Exception:`` below
        # caught this and logged at INFO with ``exc_info=True``,
        # polluting the rotating log with tracebacks for the NORMAL
        # disconnect path (every client that closes cleanly produced
        # an INFO-level traceback entry).
        log.debug("[SIDECAR-WS] client disconnected cleanly")
    except ConnectionClosedError as exc:
        # Abnormal WebSocket close (1006 protocol error, 1011
        # server error, etc.) — log at DEBUG. This is still an
        # expected close path (the host may close with a non-1000
        # code during shutdown); the WARNING-level catch-all below is
        # reserved for genuinely UNEXPECTED errors.
        log.debug("[SIDECAR-WS] connection closed with error: %s", exc)
    except Exception:
        # Genuinely unexpected error from the WS dispatch
        # loop. Log at WARNING (not INFO) with ``exc_info=True`` so
        # the traceback lands in the log for diagnosis, but reserve
        # INFO for the ``finally:`` "connection closed" message below
        # so the rotating log stays readable (clean disconnects no
        # longer produce traceback-laden INFO records).
        log.warning("[SIDECAR-WS] connection ended unexpectedly", exc_info=True)
    finally:
        event_bus.unsubscribe(_push_to_ws)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer_task
        # XZ-R18-06: clear the active-connection slot ONLY if it still
        # points at THIS socket. A subsequent auth may have already
        # replaced it (e.g. test scenarios that re-enter
        # ``_handle_connection`` on the same server instance) — clearing
        # unconditionally would clobber the new connection's marker and
        # re-open the duplicate-auth window the slot exists to prevent.
        # The compare-and-clear runs under ``server._lock`` so a
        # concurrent auth in another asyncio task can't race the read +
        # write (single event loop = no true parallelism, but the lock
        # documents the invariant and future-proofs against a
        # multi-loop refactor).
        with server._lock:
            if getattr(server, "_active_ws_connection", None) is websocket:
                server._active_ws_connection = None
        # INFO reserved for this single "connection closed"
        # message so the rotating log shows one line per WS
        # connection lifecycle (clean OR abnormal), making it easy
        # to grep for connection counts.
        log.info("[SIDECAR-WS] connection closed (peer=%s)", peer)


def run(server: IPCServer) -> int:
    """Bind a localhost WS server on an ephemeral port and run forever.

    Returns the bound port (also emitted to stdout via
    :func:`_emit_server_started` before the accept loop starts).

    Per ADR-0020 §1, this is the canonical Tauri-sidecar entrypoint.
    The host (Rust) reads the ``server_started`` JSON from the
    sidecar's stdout, then opens a WS client to the reported port.

    The function blocks until the asyncio loop is cancelled (e.g.
    SIGTERM from the host's kill_children backstop).
    """
    _force_line_buffered_stdout()

    # Local import so the module imports cleanly without `websockets`
    # installed (the Electron-only build path doesn't need it).
    try:
        import websockets  # noqa: F401 — imported for availability probe
        from websockets.asyncio.server import serve
    except ImportError as exc:
        log.error(
            "[SIDECAR-WS] the `websockets` package is required for the "
            "Tauri sidecar path. Install with: uv pip install websockets. "
            "Original error: %s",
            exc,
        )
        return 2

    dispatch = _make_dispatch(server)

    async def _main() -> int:
        # bind on 127.0.0.1:0 → OS assigns an ephemeral port.
        # max_size enforces the 1 MiB frame cap (ADR-0020 §10).
        async with serve(
            _handler,
            _LOOPBACK_HOST,
            0,
            max_size=_MAX_FRAME_BYTES,
        ) as ws_server:
            # Read back the OS-assigned port. websockets.asyncio.server
            # exposes the underlying socket via .sockets.
            socks = ws_server.sockets
            if not socks:
                log.error("[SIDECAR-WS] no sockets bound — aborting")
                return 3
            port = socks[0].getsockname()[1]
            _emit_server_started(port, PROTOCOL_VERSION)
            log.info("[SIDECAR-WS] listening on %s:%d", _LOOPBACK_HOST, port)

            # Run until cancelled.
            await asyncio.Future()

        return 0

    async def _handler(websocket) -> None:
        await _handle_connection(websocket, server, dispatch)

    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("[SIDECAR-WS] interrupted — shutting down")
        return 0
    except Exception:
        log.exception("[SIDECAR-WS] fatal error in run()")
        return 1
