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
    ws://127.0.0.1:<n>, sends the bearer-token auth frame, then forwards
    invoke('dispatch', {cmd, data}) envelopes over the WS.

    Auth model (ADR-0020 §3)
    -----------------------------------------------
    The handshake is a **one-shot bearer-token** check, NOT an HMAC
    scheme. The Rust host generates a 256-bit bearer token via
    ``secrets.token_bytes(32)`` and the Python sidecar compares it with
    :func:`hmac.compare_digest` (constant-time *comparison only* — no
    key derivation, no signing). There is no per-message MAC, no nonce,
    and no replay protection; subsequent frames skip re-auth (mirroring
    the TCP handshake-once model from ADR-0014).

    Compensating controls for the absence of per-message MAC:
      - **Loopback-only bind**: ``127.0.0.1:0`` — never exposed to the
        network.
      - **Ephemeral port**: chosen by the OS at sidecar startup and
        reported to the host over stdout; not predictable ahead of time.
      - **Per-launch / per-respawn token rotation**: a new token is
        generated on every sidecar spawn, so a stolen token is useless
        after the process exits (ADR-0020 §3 rotation).
    The historical "HMAC" wording was carried over from ADR-0014's
    original design; ADR-0020 §3 has been reconciled and this
    module's docstrings mirror the corrected wording.

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
RELIABILITY-006-) gets ``{"type":"error","code":
"rate_limited","data":{"message":"rate limit exceeded; backing
off"}}`` and the connection stays open.

the limiter is shared across ALL WS connections to this server
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

Module layout
-------------
This module is the CANONICAL home of the WS transport: the entrypoint
(:func:`run`), the auth handshake (``_authenticate``), the dispatch
factory (``_make_dispatch``), the outbound send path (``_safe_send`` +
``_encode_ws_frame`` + ``_emit_server_started``), the connection
orchestrators (``_handle_connection`` / ``_handle_connection_inner``,
``_read_loop``, ``_start_writer``) and every module-level constant.
Focused helper concerns live in the
:mod:`voice_typer.server.sidecar_ws_internals` leaf package and are
re-exported here (see the "Split leaves" comment near the imports for
the pin map):

- ``encode_pool`` — WS frame-encode ThreadPoolExecutor lifecycle
  (``_get_ws_encode_pool``, ``shutdown_encode_pool``).
- ``graceful_shutdown`` — ``_attach_ws_graceful_shutdown`` /
  ``_graceful_close_all_conns`` (close(1001) pass + loop stop).
- ``stdout_banner`` — ``_force_line_buffered_stdout`` (stdout line
  buffering for the handshake JSON).
- ``connection`` — per-connection helpers (duplicate-auth invariant,
  connection semaphore, browser-origin rejection, drop-oldest enqueue,
  ready emit, event-bus subscriber, initial state snapshot).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from typing import TYPE_CHECKING

# Shared TCP/WS auth-handshake helpers: frame-shape validation
# + token extraction (``extract_auth_token``) and the constant-time
# token comparison (``tokens_equal``, wrapping ``hmac.compare_digest``)
# live in :mod:`voice_typer.server.ipc.auth` so the two transports
# cannot silently drift — see the DEDUP note in ``_authenticate``.
from voice_typer.server.ipc.auth import extract_auth_token, tokens_equal

# Namespaced error code constants. Imported here so the bare-string
# literals used elsewhere in this module (e.g. ``"max_connections_reached"``,
# ``"duplicate_connection"``) can be replaced with ``ErrorCodes.X`` at
# import time — which keeps the static-structural test
# ``test_error_codes_registry`` happy (it scans for emitted code
# literals and requires each to either match a registered code or a
# declared legacy alias). The constants themselves are defined in
# :mod:`voice_typer.server.ipc.validation`.
from voice_typer.server.ipc.validation import ErrorCodes

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

# import the canonical loopback host constant from _paths.py
# (was a local `_LOOPBACK_HOST = "127.0.0.1"` literal duplicated across
# _http_safety.py, _secrets.py, and this module). Aliased to the
# underscore-prefixed name so existing call sites (e.g. `serve(...,
# _LOOPBACK_HOST, ...)`) keep working unchanged.
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR, LOOPBACK_HOST as _LOOPBACK_HOST

# ── Split leaves ─────────────────────────────────────────────────────
# The once-monolithic sidecar_ws.py (2081 LOC, 8+ concerns) was split
# into focused leaf modules under
# ``voice_typer/server/sidecar_ws_internals/`` (the history_db.py /
# history_db_internals/ precedent — deliberately NOT a sidecar_ws/
# package, which would move this file and break the ~14 test files
# that pin the literal voice_typer/server/sidecar_ws.py path).
#
# This module stays CANONICAL: every module-level constant plus every
# file-text-pinned function (_safe_send, _encode_ws_frame,
# _emit_server_started, _authenticate, _make_dispatch, _read_loop,
# _start_writer, _handle_connection_inner, run) lives here, and the
# moved symbols are re-exported below so every existing import path AND
# monkeypatch target keeps working: a
# ``monkeypatch.setattr(sidecar_ws, "X", ...)`` rebinds THIS module's
# global, which is exactly what the canonical observers (run(),
# _handle_connection_inner, _safe_send) resolve via bare-name lookup.
#
# Pin map for the re-exported names:
# - _get_ws_encode_pool / shutdown_encode_pool — referenced by name in
#   ``_safe_send`` (canonical; whole-module getsource pin in
#   tests/test_ipc_server.py::TestWriterEncodesOnce). The
#   ``_ws_encode_pool_singleton`` global is NOT re-exported (a value
#   re-export would go stale the moment the leaf's accessors rebind
#   it); it lives in sidecar_ws_internals/encode_pool.py with its
#   accessors.
# - _graceful_close_all_conns / _attach_ws_graceful_shutdown — driven
#   via ``sidecar_ws._attach_ws_graceful_shutdown(server)`` by
#   tests/test_sidecar_ws.py.
# - _force_line_buffered_stdout — PATCHED by the mig15/mig16/mig17
#   ws_hmac suites; observed by run() (canonical).
# - _check_duplicate_auth / _emit_ready_if_first / _install_subscriber
#   / _emit_initial_state_snapshot — signatures pinned by
#   tests/test_sidecar_ws_handle_connection_split.py; source pins in
#   tests/test_sidecar_ws_thread_safety.py;
#   _install_subscriber + _emit_ready_if_first are PATCHED by
#   tests/test_sidecar_ws_ready_ordering.py and observed by
#   _handle_connection_inner (canonical, C-WS-1 ordering site).
# - _enqueue_safe / _get_ws_connection_semaphore — source + direct-call
#   pins in tests/test_sidecar_ws_thread_safety.py and
#   tests/test_sidecar_ws_connection_cap.py.
# - _reject_browser_origins — direct-call + process_request identity
#   pins in tests/test_sidecar_ws_origin_check.py; passed by run().
from voice_typer.server.sidecar_ws_internals.connection import (  # noqa: F401
    _check_duplicate_auth,
    _emit_initial_state_snapshot,
    _emit_ready_if_first,
    _enqueue_safe,
    _get_ws_connection_semaphore,
    _install_subscriber,
    _reject_browser_origins,
)
from voice_typer.server.sidecar_ws_internals.encode_pool import (  # noqa: F401
    _get_ws_encode_pool,
    shutdown_encode_pool,
)
from voice_typer.server.sidecar_ws_internals.graceful_shutdown import (  # noqa: F401
    _attach_ws_graceful_shutdown,
    _graceful_close_all_conns,
)
from voice_typer.server.sidecar_ws_internals.stdout_banner import (  # noqa: F401
    _force_line_buffered_stdout,
)

log = logging.getLogger("voice_typer.server.sidecar_ws")

# ADR-0020 round-2 fix: the `ready` event is emitted only once per
# IPCServer instance, on the first authenticated WS connection.
# Previously ipc_server.py:main() called `server.push({"type": "ready"})`
# BEFORE sidecar_ws.run() started the WS server — so the event was
# dropped (no subscriber yet). Now we emit `ready` via event_bus.publish
# AFTER the first client authenticates, so the Tauri host receives it
# over the WS and can hydrate the UI.
#
# `ready` is emitted AFTER `_install_subscriber` registers the WS
# subscriber (`_push_to_ws`) on `event_bus`. Pre- the emit ran
# BEFORE the subscriber was registered, so the event was published to
# an empty subscriber set (modulo other transports) and the WS writer
# task never received it — the Tauri host never got `ready` over the WS
# on first connection and the UI stayed un-hydrated. See
# `_handle_connection_inner` for the ordered call sites.
#
# this flag USED to be a module-level global (`_ready_emitted`).
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
# ``_LOOPBACK_HOST`` is now imported from ``_paths.py`` (see
# the import statement near the top of this module) so the same
# constant is shared with ``_http_safety.py`` and ``_secrets.py``.

# ADR-0020 §10: 1 MiB WS frame cap. download_progress and
# vocabulary_suggestion can carry large payloads; without a cap a
# malformed/huge frame can OOM the client.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Auth frame timeout (seconds). A client that connects but never
# sends the auth frame must not hold the connection indefinitely —
# matches the TCP path's 5-second auth timeout ().
#
# DEDUP TRACKING: the TCP path has a local
# ``_tcp_auth_timeout_seconds = 5.0`` in
# ``ipc/transport_tcp.py::_handle_tcp_connection`` (line ~278).
# The two transports MUST agree on the auth-deadline budget — if one
# is changed, the other MUST be updated to match.  A future refactor
# could extract this constant to a shared ``ipc/auth.py`` module
# (see ).
_AUTH_TIMEOUT_SECONDS = 5.0

# concurrent-connection limit (DoS protection).
_MAX_WS_CONNECTIONS = 16

# Heartbeat fast-path rate cap.
#
# The Rust host sends one ``heartbeat`` command every 10s (ADR-0018 /
# ADR-0020 §10) — i.e. the legitimate steady-state rate is 1 per 10s.
# The heartbeat fast-path in :func:`_read_loop` deliberately bypasses
# the dispatch pool (and therefore the ADR-0019 per-frame
# :class:`_RateLimiter` that lives inside ``_make_dispatch``) so the
# ack latency stays at the WS round-trip (~1 ms loopback) instead of
# the dispatch-pool queue depth — a slow ``download_model`` /
# ``transcribe`` running on the pool must not delay the ack and trip
# the host's "3 consecutive misses → respawn" liveness probe.
#
# But that bypass means a hostile or buggy client could spam
# ``{"type":"heartbeat"}`` at line rate (tens of thousands per
# second) and the read loop would ``await websocket.send(ack)`` for
# every one of them — starving every other connection's reads, since
# the read loop is single-threaded per connection and the event loop
# is shared across all connections.
#
# This cap is a CHEAP sliding-window (a ``deque`` of timestamps,
# popped from the left when older than the window). 100 per 10s is
# ~10x the legitimate rate — generous enough that a slightly
# over-eager host retry loop won't trip it, tight enough that a
# flood is dropped at the read loop instead of fanning out acks.
#
# The window is PER-CONNECTION (not shared like the ADR-0019
# limiter) because a heartbeat flood is a per-connection
# misbehaviour — sharing the budget would let one flapping client
# starve heartbeats from a well-behaved second connection. Each
# connection's read loop is single-threaded, so the deque is
# accessed without a lock from inside that coroutine.
_HEARTBEAT_RATE_WINDOW_SECONDS = 10.0
_HEARTBEAT_RATE_MAX_PER_WINDOW = 100

# Outbound ``websocket.send`` timeout (seconds). A send that has not
# completed within this window is treated as a stuck peer (TCP send
# buffer full, slow consumer, half-open socket) — the connection is
# closed so the host's reconnect path can take over instead of
# letting the WS writer task block the event loop indefinitely on a
# single ``await websocket.send``. 5s is generous for a 1 MiB frame
# over loopback (sub-millisecond RTT, multi-GiB/s throughput) but
# bounded enough that a wedged peer doesn't tie up the writer task
# (and the asyncio loop thread) forever.
_WS_SEND_TIMEOUT_SECONDS = 5.0

# Graceful WS shutdown budget (seconds). ``ws_graceful_shutdown`` sends
# ``close(code=1001, reason="going away")`` to every authenticated
# connection, then sleeps for this long so the WS close handshake has
# time to complete on the wire BEFORE the asyncio loop is stopped.
# Without this sleep, ``loop.stop()`` can fire before the peer
# receives the close frame — the TCP socket is torn down mid-handshake
# and the host sees a TCP RST instead of a clean WS close, triggering
# the respawn path as if the sidecar had crashed (the exact failure
# mode the graceful-shutdown path was added to prevent). 500 ms is
# ~50x the loopback RTT, which is generous for the close-frame
# round-trip even under load.
_WS_GRACEFUL_CLOSE_HANDSHAKE_SECONDS = 0.5

# Bounded-wait budget (seconds) for in-flight dispatch futures during
# ``ws_graceful_shutdown``. Each future registered on
# ``server._ws_dispatch_futures`` gets its own ``.result(timeout=...)``
# call — a single stuck handler cannot block the shutdown indefinitely.
# 2.0 s matches the Rust host's ``SHUTDOWN_ACK_TIMEOUT_MS = 2000`` (in
# ``src-tauri/src/util.rs``): if a handler has not completed by the
# time the host's hard-timeout fires, the host force-kills the process
# tree anyway, so waiting longer in Python would be wasted time.
_WS_DISPATCH_DRAIN_TIMEOUT_SECONDS = 2.0


def _encode_ws_frame(event: dict) -> bytes:
    """Serialize ``event`` to a WS TEXT-frame payload (UTF-8 bytes).

    Runs ``json.dumps`` + ``.encode`` together so the whole O(n)
    encode cost stays OFF the asyncio loop thread — the writer task
    in :func:`_start_writer` calls this via
    ``loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)``.
    For near-cap frames (~1 MiB) the in-line encode was 50-100 ms of
    pure CPU on the loop thread, stalling every other connection's
    reads + the heartbeat fast-path.

    ``ensure_ascii=False`` keeps multi-byte UTF-8 (e.g. CJK / emoji
    dictation) as-is on the wire instead of escaping to ``\\uXXXX``
    (the default ``ensure_ascii=True`` would bloat CJK payloads ~3x).
    """
    return json.dumps(event, ensure_ascii=False).encode("utf-8")


async def _safe_send(websocket, event: dict) -> str:
    """Encode + size-cap + timeout-protected send for one outbound WS frame.

    Shared by the dispatch-response path in :func:`_read_loop` and the
    writer-task path in :func:`_start_writer._writer`. Both paths MUST
    apply the same three defenses against an oversized / wedged-peer
    DoS — pre-fix the dispatch-response path applied NONE of them,
    so a handler returning a multi-MiB response (e.g. ``get_history``
    / ``list_models`` / ``get_vocabulary`` for a user with thousands
    of entries) would (1) block the asyncio loop thread with
    synchronous ``json.dumps`` (50-100 ms per MiB — stalls every
    other connection's reads + the heartbeat fast-path), (2) block
    forever if the peer's TCP send buffer fills (no timeout), and
    (3) exceed the 1 MiB ``_MAX_FRAME_BYTES`` cap that ADR-0020 §10
    mandates (the websockets library's ``max_size`` is enforced on
    INBOUND frames only — the OUTBOUND side had no cap).

    The three defenses:

    (1) Offload ``json.dumps`` + UTF-8 encode to a worker thread via
        :func:`loop.run_in_executor`. For near-cap frames (~1 MiB) the
        in-line encode was 50-100 ms of pure CPU on the asyncio loop
        thread, stalling every other connection's reads + the heartbeat
        fast-path.
    (2) Drop the frame pre-emptively with an ERROR log if
        ``len(raw_bytes) > _MAX_FRAME_BYTES`` — otherwise the Rust
        host's tungstenite ``max_size`` receive enforcement would close
        the connection on its end (with a 1009) and surface a
        misleading transport error. Measuring the actual UTF-8 byte
        count (not the char count) catches multi-byte-heavy frames
        (CJK / emoji dictation) the pre-fix char-count check missed.
    (3) Wrap ``websocket.send`` in :func:`asyncio.wait_for` with
        ``_WS_SEND_TIMEOUT_SECONDS`` so a wedged peer (full TCP send
        buffer, half-open socket) cannot tie up the calling coroutine
        (and the asyncio loop thread) indefinitely. On timeout the
        connection is closed with code 1011 so the host's reconnect
        path takes over.

    Returns one of three status strings so the caller can distinguish
    "drop and keep going" (oversized) from "drop and bail out"
    (timeout / send error):

    - ``"sent"`` — the frame was sent successfully.
    - ``"dropped"`` — the frame exceeded ``_MAX_FRAME_BYTES`` and was
      dropped with an ERROR log. The connection is still healthy.
    - ``"failed"`` — the send timed out (connection closed with 1011)
      OR raised an unexpected exception. The connection is unreliable
      or already closing.

    The caller MUST NOT re-attempt the send on ``"failed"`` — the
    timeout path already initiated a WS close, and a re-attempt would
    race the close handshake.
    """
    loop = asyncio.get_running_loop()
    raw_bytes = await loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)
    if len(raw_bytes) > _MAX_FRAME_BYTES:
        log.error(
            "[SIDECAR-WS] outbound frame exceeds %d bytes — dropping",
            _MAX_FRAME_BYTES,
        )
        return "dropped"
    try:
        # WIRE CONTRACT (do not "optimize" this decode away): dispatch
        # responses MUST go out as WS **TEXT** frames carrying a numeric
        # top-level ``id``. The Rust host's reader parses
        # ``Message::Text`` only and silently ignores ``Message::Binary``
        # — sending the encoded bytes directly produces BINARY frames,
        # every dispatch response vanishes inside the host, and ALL
        # renderer commands time out while heartbeat acks (sent inline
        # as ``str``) keep flowing ("Lost connection to Python backend",
        # first Windows host run 2026-08-21). Binding rule:
        # AGENTS.md constraint C-WS-2.
        await asyncio.wait_for(
            websocket.send(raw_bytes.decode("utf-8")),
            timeout=_WS_SEND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.warning(
            "[SIDECAR-WS] send timed out after %.1fs — closing connection",
            _WS_SEND_TIMEOUT_SECONDS,
        )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="send timeout")
        return "failed"
    except Exception:
        log.warning("[SIDECAR-WS] send failed", exc_info=True)
        return "failed"
    return "sent"


# Cooperative shutdown hard timeout (ADR-0020 §10). When the host
# sends {"type":"shutdown"} the sidecar must release the mic, ack,
# and exit within this window; if it doesn't, the host force-kills
# the process tree via kill_children.
#
# the previous ``_SHUTDOWN_ACK_TIMEOUT_SECONDS = 2.0`` constant
# was dead code — referenced nowhere in this module and misleadingly
# suggested Python enforces the timeout. The Rust host's
# ``SHUTDOWN_ACK_TIMEOUT_MS = 2000`` (in ``src-tauri/src/util.rs``)
# is the single source of truth for the cooperative-shutdown hard
# timeout; Python does NOT mirror it. See
# ``src-tauri/src/util.rs::SHUTDOWN_ACK_TIMEOUT_MS`` for the canonical
# value.

# Tauri sidecar handshake protocol-version constant.
#
# The sidecar emits ``"protocol": PROTOCOL_VERSION`` in its
# ``server_started`` JSON line so the Rust host can detect version
# skew at handshake time (before any command dispatch) and surface a
# clear ``protocol_mismatch`` error rather than producing confusing
# partial failures (some commands work, others return
# ``unknown_command``, push events have unexpected ``type`` values).
#
# The Rust host's ``EXPECTED_PROTOCOL_VERSION`` constant in
# ``src-tauri/src/sidecar/ws.rs`` MUST match this value. Bump this
# integer whenever the ``_COMMAND_REGISTRY`` (in
# ``voice_typer/server/ipc_server.py``) adds/removes/renames a
# command OR the push-event ``type`` vocabulary changes — both are
# observable contracts the host depends on. The version is monotonic
# and never reused.
#
# History:
#   - 1 (, this run): initial protocol-version negotiation. The
#     pre-negotiation sidecar emitted only ``{"event":"server_started",
#     "port":<n>}``; old hosts that don't yet parse the ``protocol``
#     field continue to function (the field is additive).
#
# Canonical source of truth: ``voice_typer/server/ipc/protocol_version.py``.
# Importing (rather than redefining) prevents drift between the WS and
# TCP transports — see ``tests/test_protocol_version_consolidated.py``.
from voice_typer.server.ipc.protocol_version import PROTOCOL_VERSION  # noqa: E402


def _emit_server_started(port: int, protocol: int | None = None) -> None:
    """Write the one structured stdout line the host is parsing for.

    Per ADR-0020 §1, this is the ONLY thing that ever goes to stdout
    from the sidecar. Every other log goes to stderr / the rotating
    file log. The host blocks reading stdout until it parses this
    JSON, then opens a WS client to ws://127.0.0.1:<port>.

    when ``protocol`` is not ``None``, the payload
    additionally includes ``"protocol": <int>`` so the Rust host can
    detect version skew at handshake time. The Rust host's
    ``EXPECTED_PROTOCOL_VERSION`` constant in
    ``src-tauri/src/sidecar/ws.rs`` MUST match
    :data:`PROTOCOL_VERSION`; on mismatch, the host logs a clear
    ``protocol_mismatch`` error and refuses to spawn. Callers in this
    module always pass :data:`PROTOCOL_VERSION`; the ``None`` default
    is preserved for backward compatibility with pre-negotiation tests
    that assert the exact two-field payload shape and with any
    hypothetical external caller of this helper (none exist in the
    codebase today, but the default keeps the function safe to call
    without forcing the caller to know the current protocol integer).
    """
    if protocol is not None:
        print(
            json.dumps({"event": "server_started", "port": int(port), "protocol": int(protocol)}),
            flush=True,
        )
    else:
        print(json.dumps({"event": "server_started", "port": int(port)}), flush=True)


async def _authenticate(websocket) -> bool:
    """Read the first WS frame and validate the bearer token.

    Per ADR-0020 §3, the client's first frame
    must be::

        {"type": "auth", "token": "<token>"}

    The token is compared with :func:`hmac.compare_digest` (constant
    time) against the ``VOICE_TYPER_IPC_TOKEN`` env var set by the
    Rust host at spawn. On mismatch, the socket is closed immediately
    and the connection is rejected (the host treats this as a crash
    → respawn with a fresh token, ADR-0020 §10).

    This is a **one-shot bearer-token** check, NOT an HMAC scheme:
    :func:`hmac.compare_digest` is used purely as a constant-time
    *comparison* helper — there is no key derivation, no signing, no
    per-message MAC, and no nonce/replay protection. Subsequent frames
    after the handshake skip re-auth (mirroring the TCP handshake-once
    model from ADR-0014). Compensating controls for the absence of
    per-message MAC are documented in this module's top-level docstring
    (loopback-only bind + ephemeral port + per-respawn token rotation).

    Returns ``True`` if authenticated, ``False`` if rejected.

    DEDUP ()
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
    ``conn.settimeout``).  Bug fixes to the validation contract are
    applied in ONE place: the shared helpers in
    :mod:`voice_typer.server.ipc.auth` (``extract_auth_token`` +
    ``tokens_equal``) are used by BOTH transports, so a fix to the
    frame-validation / constant-time comparison contract lands in a
    single module (extracted 2026-08-11; previously this note
    read "must be applied to BOTH call sites").
    """
    expected_token = os.environ.get(IPC_TOKEN_ENV_VAR, "")
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

    # Shared with the TCP transport: ``extract_auth_token``
    # validates the frame shape + extracts the token; ``tokens_equal``
    # performs the constant-time ``hmac.compare_digest`` comparison
    # (see ``voice_typer.server.ipc.auth`` — a bug fix to either
    # concern lands in ONE module used by both transports).
    provided = extract_auth_token(first)
    if provided is None:
        log.warning("[SIDECAR-WS] auth frame missing token")
        return False

    if not tokens_equal(provided, expected_token):
        log.warning("[SIDECAR-WS] auth token mismatch — rejecting")
        return False

    # detect host/sidecar protocol-version skew at handshake
    # time. The Rust host (src-tauri/src/sidecar/ws.rs) now includes a
    # `protocol_version` integer in its auth frame. The field is
    # additive — older hosts that don't yet send it continue to function
    # (we just skip the check). When present and mismatched, log a
    # prominent WARNING so the mismatch is observable in diagnostics
    # before confusing partial-failure symptoms appear. We do NOT reject
    # the connection on mismatch because a misconfigured host should
    # still be able to authenticate (the version negotiation is
    # defense-in-depth, not a security gate). The TCP transport's
    # parallel check lives in ipc/transport_tcp.py ().
    host_protocol = first.get("protocol_version")
    if host_protocol is not None:
        try:
            host_protocol_int = int(host_protocol)
        except (TypeError, ValueError):
            log.warning(
                "[SIDECAR-WS] auth frame protocol_version is not an int: %r",
                host_protocol,
            )
        else:
            if host_protocol_int != PROTOCOL_VERSION:
                log.warning(
                    "[SIDECAR-WS] protocol version skew: host=%d sidecar=%d (continuing — field is advisory)",
                    host_protocol_int,
                    PROTOCOL_VERSION,
                )

    log.info("[SIDECAR-WS] auth accepted")
    return True


def _make_dispatch(server: IPCServer):
    """Build a coroutine that dispatches a single WS frame.

    Reuses ``server._dispatch`` (the same path the TCP loop uses),
    so the 61-command registry + _validate_dict_payload + every
    handler mixin is exercised unchanged (ADR-0020 §2).
    """
    # ADR-0019 + : per-process rate limiter. Reuse the same private
    # _RateLimiter class the TCP path uses (ipc_server.py:215) so the
    # burst/sustained semantics are identical — 200 burst, 600 sustained
    # over a 10s window (RELIABILITY-006-).
    #
    # the limiter is looked up lazily via _get_rate_limiter(server)
    # so it is shared across ALL WS connections to this server process.
    # A local attacker can no longer reset the 200-message burst budget
    # by dropping the WS and reconnecting — the 10s sliding window
    # continues to evict old timestamps across reconnects.
    # dedicated ThreadPoolExecutor for WS dispatch so
    # ``_do_cleanup`` can drain / cancel in-flight dispatch requests
    # BEFORE tearing down the recorder / history DB / crash-recovery
    # writer. Previously ``loop.run_in_executor(None, server._dispatch,
    # msg)`` used the asyncio loop's default executor, which has no
    # handle the shutdown path can reach — a long-running handler
    # (e.g. ``download_model``) would race teardown, half-flush the
    # history DB, and leak a partially-written crash-recovery snapshot.
    #
    # DEDUP (): the rate-limiter import is intentionally from
    # ``ipc_server`` (NOT from the leaf ``voice_typer.server.ipc.rate_limiter``).
    # ``_get_rate_limiter`` is defined LOCALLY in ``ipc_server.py`` (not
    # just re-exported) so it resolves ``_RateLimiter`` against
    # ``ipc_server``'s module globals at call time.  Tests that
    # monkey-patch ``ipc_server._RateLimiter`` observe the patched class
    # through this import (see
    # ``tests/test_ipc_rate_limiter_concurrent_init.py``).  Changing the
    # import to the leaf
    # module would BREAK the test monkey-patch contract.  The TCP path
    # (``ipc/transport_tcp.py``) also imports from ``ipc_server`` for
    # the same reason.
    #
    # Stored on the server instance (not the closure) so
    # ``ShutdownController._do_cleanup`` can reach it via
    # ``app._ipc_server._ws_dispatch_pool``. The pool / drained-event /
    # inflight-lock/count are PRE-CONSTRUCTED in ``IPCServer.__init__``
    # (the creation logic is pure constructor work with no WS-loop
    # dependency, so the lazy-init branch per dispatch was dead
    # weight). The MagicMock-compat ``getattr`` reads are kept so
    # test doubles that bypass ``__init__`` still work.
    from voice_typer.server.ipc_server import _get_rate_limiter

    # Resolve the rate limiter ONCE in the closure body so
    # ``dispatch()`` doesn't call ``_get_rate_limiter(server)`` per
    # frame. Per-frame resolution costs a module-globals traversal
    # + a dict-style getattr on every WS frame; resolved-once
    # captures the limiter in a local closure cell. The limiter
    # is still shared across all WS connections to this server
    # (it's the same ``_RateLimiter`` instance stored on the
    # server's ``_rate_limiter_instance`` slot, just resolved at
    # handler-creation time rather than per frame).
    rate_limiter = _get_rate_limiter(server)

    ws_dispatch_pool = getattr(server, "_ws_dispatch_pool", None)
    if ws_dispatch_pool is None:
        # Only reachable on test doubles that bypass ``__init__``.
        from concurrent.futures import ThreadPoolExecutor

        ws_dispatch_pool = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="sidecar-ws-dispatch",
        )
        server._ws_dispatch_pool = ws_dispatch_pool

    # explicit ``threading.Event`` coordination between the WS
    # dispatch path and ``ShutdownController._do_cleanup``. The pool's
    # ``shutdown(wait=True)`` only guarantees that the
    # ``ThreadPoolExecutor``'s worker queue has drained — it does NOT
    # guarantee that the per-dispatch coroutine body has finished its DB
    # write (the Future resolves on ``server._dispatch`` return, but the
    # WS ``dispatch`` coroutine may still be in its ``await
    # loop.run_in_executor`` unwind / result-serialisation tail when the
    # pool reports drained). That tail can race
    # ``_teardown_history_db`` / ``_teardown_crash_recovery`` in
    # ``_do_cleanup``, silently losing the user's final
    # transcription_final DB write.
    #
    # ``_ws_drained_event`` is SET when no dispatch is in-flight (the
    # initial state — no dispatch has started yet, so the drain is
    # trivially complete). ``_ws_inflight_count`` is the number of
    # dispatches currently between the entry point and the exit of the
    # ``dispatch`` coroutine body. ``_ws_inflight_lock`` guards the
    # count + Event mutation pair so two concurrent dispatches cannot
    # race the count into a wrong value or miss the Event-set on the
    # last exit.
    #
    # Pre-constructed in ``IPCServer.__init__`` — plain reads here (the
    # ``getattr`` fallbacks only fire on test doubles that bypass
    # ``__init__``).
    ws_drained_event = getattr(server, "_ws_drained_event", None)
    ws_inflight_lock = getattr(server, "_ws_inflight_lock", None)
    if ws_drained_event is None or ws_inflight_lock is None:
        import threading as _threading

        if ws_drained_event is None:
            ws_drained_event = _threading.Event()
            ws_drained_event.set()  # initially drained — count is 0
            server._ws_drained_event = ws_drained_event
        if ws_inflight_lock is None:
            ws_inflight_lock = _threading.Lock()
            server._ws_inflight_lock = ws_inflight_lock
        if getattr(server, "_ws_inflight_count", None) is None:
            server._ws_inflight_count = 0

    async def dispatch(msg: dict, websocket) -> dict | None:
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical) — see
                    # ``voice_typer/server/ipc/validation.py`` for the
                    # migration contract.
                    "code": "client.invalid_payload",
                    "message": "missing 'type'",
                },
            }

        # cooperative shutdown gate. Once ``app._shutting_down``
        # is True (set by ``ShutdownController.quit()`` before
        # ``_do_cleanup()`` runs), reject every new dispatch request
        # with a structured ``server.shutting_down`` error code so the
        # host can re-queue / surface a graceful "backend is exiting"
        # message instead of starting a long-running handler (e.g.
        # ``download_model``) that would race teardown. The
        # ``shutdown`` message itself is exempt — the host sends it to
        # TRIGGER shutdown, and it is now handled by the shared
        # ``_COMMAND_REGISTRY`` entry ``"shutdown": "_handle_shutdown"``
        # (registered in ipc_server.py by ) which delegates to
        # ``service.quit()`` — the SAME path the TCP ``quit_app``
        # command uses. Pre- the WS path special-cased
        # ``shutdown`` here and called ``server.app.quit()`` directly,
        # bypassing the service layer (so any future shutdown
        # side-effect added to ``service.quit()`` silently wouldn't run
        # on Tauri). The special-case is now removed; ``shutdown``
        # flows through ``server._dispatch`` like every other command.
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
            log.debug("[SIDECAR-WS] rejecting %s — server shutting down", msg_type)
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # ADR-0019 +  rate limit check. Look up the shared limiter
        # on every call (cheap — dict-style getattr) so all WS frames to
        # this server share the same sliding-window budget. _RateLimiter
        # .allow() returns a bool (no retry-after); the host backs off
        # via backoff on repeated rate-limit hits.
        #
        # pass ``command=msg_type`` so the per-command cost map
        # (``COMMAND_COSTS``) is applied — e.g. ``download_model``
        # consumes 50 of the 200 burst units, so a buggy client can fire
        # at most 4 expensive commands per second before the 5th is
        # rejected. Cheap commands (``heartbeat``, ``get_status``) keep
        # the pre- cost-1 behavior. The legacy
        # ``rate_limiter.allow()`` form (no ``command`` kwarg) is still
        # supported and treats the call as cost 1.
        #
        # ``shutdown`` is a CONTROL frame, not a dispatch frame — it
        # must bypass the rate limiter so a sidecar being spammed with
        # frames (over the 200-burst budget) can still shut down
        # cleanly (ADR-0020 §10). The TCP path's read loop applies the
        # same exemption (``shutdown`` skips its rate-limit gate); the
        # WS path must stay in parity.
        if msg_type != "shutdown" and not rate_limiter.allow(command=msg_type):
            # allow() already increments _rejected atomically when
            # it returns False — the separate .reject() call was removed
            # to eliminate the benign race where two threads could both
            # observe the same deque state, both decide to reject, and
            # double-count the rejection. This keeps WS-path rejected_count
            # consistent with the TCP path (both count via allow()).
            return {
                "type": "error",
                "data": {
                    # Namespaced form (canonical).
                    "code": "client.rate_limited",
                    "message": "rate limit exceeded; backing off",
                },
            }

        # TOCTOU re-check: the early ``_shutting_down`` gate
        # above was read BEFORE the rate-limiter call. The flag can
        # flip in the gap between that read and the actual
        # ``pool.submit`` — e.g. ``ShutdownController.quit()`` runs
        # concurrently between the early gate and here, OR the
        # rate-limiter itself blocks long enough for the shutdown
        # sequence to start. Re-check immediately before the in-flight
        # count increment (so a TOCTOU-rejected dispatch does NOT
        # touch the count — net-zero) and short-circuit with the SAME
        # ``server.shutting_down`` error envelope as the early gate.
        # This shrinks (does NOT eliminate) the TOCTOU window: the
        # flag can still flip DURING the handler's execution, but that
        # residual race is owned by the handler's own
        # shutdown-awareness (e.g. ``download_model`` checks
        # ``_shutting_down`` between chunks). Placing the re-check
        # BEFORE the in-flight count increment (rather than
        # immediately before ``loop.run_in_executor``) avoids
        # incrementing then decrementing the count for a rejected
        # dispatch — the count is only touched for dispatches that
        # actually reach the executor.
        if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
            log.debug(
                "[SIDECAR-WS] TOCTOU re-check rejecting %s — server shutting down",
                msg_type,
            )
            return {
                "type": "error",
                "data": {
                    "code": "server.shutting_down",
                    "message": "server is shutting down; please retry later",
                },
            }

        # mark this dispatch as in-flight + clear the drain Event
        # so ``ShutdownController._do_cleanup`` knows to wait for us
        # before tearing down the DB / recorder / crash-recovery
        # subsystems. The increment-then-clear pair is under
        # ``_ws_inflight_lock`` so two concurrent dispatches cannot
        # interleave as ``inc → inc → clear → clear`` (both would clear
        # the Event, then the first exit would set it prematurely while
        # the second dispatch is still running — a TOCTOU on the count).
        # The lock is held for the minimum work needed (increment +
        # Event.clear); the dispatch body itself runs without the lock.
        with ws_inflight_lock:
            server._ws_inflight_count = server._ws_inflight_count + 1
            ws_drained_event.clear()

        # Dispatch on the worker thread pool so a slow handler
        # (e.g. download_model) doesn't block the WS reader.
        loop = asyncio.get_running_loop()
        # Pre-bind ``result`` to None so the ``return result`` line
        # below has a defined value to return even when
        # ``loop.run_in_executor`` raises (in which case
        # ``return_error`` is set to a non-None dict and we return
        # early at ``if return_error is not None:`` — but pyrefly
        # cannot track that early-return control flow).
        result: dict | None = None
        try:
            # Pre-executor TOCTOU re-check: the early ``_shutting_down``
            # gate above and the in-flight-count re-check both run BEFORE
            # the count increment. The flag can flip in the window
            # between that re-check and this point — e.g. during the
            # count increment + ``ws_drained_event.clear()`` under
            # ``ws_inflight_lock``, the ``asyncio.get_running_loop()``
            # call, or the ``try`` entry. Re-checking immediately before
            # ``loop.run_in_executor`` shrinks the TOCTOU window to just
            # the ``run_in_executor`` await itself (the residual race
            # during the handler's execution is owned by the handler's
            # own shutdown-awareness — e.g. ``download_model`` checks
            # ``_shutting_down`` between chunks). On rejection, the
            # ``finally`` block below decrements the in-flight count
            # (net-zero — the count was incremented above) and re-sets
            # the drain Event when the count drops to zero, so
            # ``_do_cleanup`` is not blocked on a dispatch that never
            # reached the executor.
            if msg_type != "shutdown" and getattr(server.app, "_shutting_down", False):
                log.debug(
                    "[SIDECAR-WS] pre-executor TOCTOU re-check rejecting %s — server shutting down",
                    msg_type,
                )
                return {
                    "type": "error",
                    "data": {
                        "code": "server.shutting_down",
                        "message": "server is shutting down; please retry later",
                    },
                }
            # use the dedicated ``_ws_dispatch_pool`` (not the
            # asyncio default executor) so ``ShutdownController._do_cleanup``
            # can ``pool.shutdown(wait=False, cancel_futures=True)`` to
            # drain / cancel in-flight handlers before recorder / history
            # DB / crash-recovery teardown.
            result = await loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg)
        except Exception:
            log.exception("[SIDECAR-WS] _dispatch raised")
            #  (2026-07-18): the error envelope now matches the
            # TCP path (``ipc_server._handle_tcp_connection``'s
            #  block) verbatim — same ``code`` AND same
            # ``message`` ("internal error"). Pre- the WS path
            # used the message "dispatch raised" while TCP used
            # "internal error"; both messages were generic (neither
            # leaked ``str(exception)``) but the divergence meant a
            # client could not use the message text to confirm parity.
            # The contract: ``{"type":"error","data":{"code":
            # "server.internal_error","message":"internal error"}}``.
            #
            #  (): the ``code`` was migrated from the
            # legacy ``"internal_error"`` to the namespaced
            # ``"server.internal_error"`` form (matching the
            # ``ERROR_CODES`` registry in ``ipc/validation.py``). The
            # renderer accepts both forms (legacy treated as alias),
            # so this is a backward-compatible migration.
            # applies the same migration to the TCP path's
            # ``internal_error`` emissions.
            return_error = {
                "type": "error",
                "data": {"code": "server.internal_error", "message": "internal error"},
            }
        else:
            return_error = None
        finally:
            # decrement the in-flight count and re-set the drain
            # Event when the count drops to zero. The ``finally`` block
            # guarantees the Event is set even if ``run_in_executor``
            # raised (the in-flight count MUST be consistent with the
            # actual dispatch state, otherwise ``_do_cleanup`` would
            # wait on an Event that never fires — a deadlock).
            with ws_inflight_lock:
                server._ws_inflight_count = server._ws_inflight_count - 1
                if server._ws_inflight_count <= 0:
                    server._ws_inflight_count = 0
                    ws_drained_event.set()

        if return_error is not None:
            return return_error

        # _dispatch returns None for fire-and-forget commands (e.g.
        # restart_app, which sends its own response). Don't send a
        # frame in that case.
        return result

    return dispatch


async def _handle_connection(websocket, server: IPCServer, dispatch) -> None:
    """Per-connection WS handler: auth + read/dispatch loop.

    enforces a concurrent-connection cap via a per-server
    asyncio.Semaphore BEFORE delegating to _handle_connection_inner.
    """
    peer = websocket.remote_address
    log.info("[SIDECAR-WS] client connected from %s", peer)

    sem = _get_ws_connection_semaphore(server)
    if sem.locked():
        log.warning(
            "[SIDECAR-WS] max_connections (%d) reached — rejecting %s with 1008",
            _MAX_WS_CONNECTIONS,
            peer,
        )
        with contextlib.suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            "code": ErrorCodes.MAX_CONNECTIONS_REACHED,
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


def _start_writer(websocket, outbound: asyncio.Queue) -> asyncio.Task:
    """Create the per-connection writer task that drains the outbound
    queue and writes each event as a WS frame.

    The TCP path installs a single ``_tcp_client`` and writes to it
    under ``self._lock``; the WS path uses a per-connection asyncio
    Queue + a writer task so we don't block the dispatch loop.
    """

    async def _writer() -> None:
        """Drain the outbound queue and write each event as a WS frame.

        Delegates each send to :func:`_safe_send`, which applies the
        three safeguards that previously lived inline in this
        coroutine: offloads ``json.dumps`` to ``loop.run_in_executor``,
        wraps ``websocket.send`` in ``asyncio.wait_for(timeout=
        _WS_SEND_TIMEOUT_SECONDS)``, and on ``TimeoutError`` calls
        ``websocket.close(code=1011, reason="send timeout")``. The
        shared helper is also used by :func:`_read_loop`'s
        dispatch-response path so the two outbound paths cannot
        diverge on the DoS defenses again (regression guard for
        the finding where the dispatch response bypassed the
        writer's size cap + timeout + off-loop encode).
        """
        try:
            while True:
                event = await outbound.get()
                if event is None:
                    return
                send_status = await _safe_send(websocket, event)
                if send_status == "failed":
                    # Timeout or send error — ``_safe_send`` already
                    # logged + initiated the close (timeout path) or
                    # the connection is otherwise unreliable. Return
                    # so the orchestrator's finally block cleans up
                    # (subscriber unsubscribe + writer-task cancel +
                    # active-connection slot clear).
                    return
                # ``"sent"`` → keep draining the queue.
                # ``"dropped"`` → the frame exceeded
                # ``_MAX_FRAME_BYTES`` and was logged + skipped by
                # ``_safe_send``. Preserve the pre-fix ``continue``
                # behaviour so one pathological event does not kill
                # the whole outbound stream (subsequent events on the
                # queue are independent and may be perfectly
                # sendable).
        except asyncio.CancelledError:
            return

    return asyncio.create_task(_writer(), name="sidecar-ws-writer")


async def _read_loop(websocket, server: IPCServer, dispatch) -> None:
    """Read/dispatch loop body ( extraction from
    ``_handle_connection_inner``).

    Reads inbound WS frames, validates JSON, and dispatches each frame
    via the ``dispatch`` coroutine. Fast-paths handled INLINE (so they
    cannot be starved by an in-flight long dispatch): heartbeat-ack and
    ``relaunch_ack``. Everything else is dispatched as a PIPELINED task
    (see the task-creation comment) so the loop keeps reading while a
    long handler runs on the dispatch pool; in-flight tasks are drained
    (responses flushed) before this function returns.

    NOTE: the inbound frame-size cap is enforced by the ``websockets``
    library itself via ``serve(..., max_size=...)`` in :func:`run` —
    the library rejects oversized frames at the transport layer with a
    1009 close. We do NOT re-check here (it would be dead code; the
    frame never arrives if it exceeds ``max_size``).

    Raises ``ConnectionClosedOK`` / ``ConnectionClosedError`` on WS
    close (the caller distinguishes clean vs abnormal close for
    log-level selection). Any other exception is propagated to the
    caller's catch-all.
    """
    # Per-connection heartbeat sliding-window rate cap. The fast-path
    # bypasses the dispatch-pool / ADR-0019 limiter, so a hostile or
    # buggy client could flood ``{"type":"heartbeat"}`` at line rate
    # and starve the event loop with ack sends. This deque holds the
    # timestamps of the last ``_HEARTBEAT_RATE_MAX_PER_WINDOW``
    # heartbeats; old entries are popleft when older than the window.
    # Single-threaded access from this coroutine — no lock needed.
    heartbeat_window: deque[float] = deque()
    # In-flight pipelined dispatch tasks (see the creation site). The
    # set is drained before return so every response is flushed.
    dispatch_tasks: set[asyncio.Task] = set()
    async for raw in websocket:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "data": {
                            # Namespaced form (canonical).
                            "code": "client.invalid_payload",
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
                            # Namespaced form (canonical).
                            "code": "client.invalid_payload",
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
        # DEBUG wire-trace (C-TAURI-3 diagnosis aid): one line per
        # inbound frame so a host↔sidecar frame loss can be bisected
        # end-to-end against the Rust-side [dispatch] id= lines.
        log.debug(
            "[SIDECAR-WS] RX frame type=%s id=%s",
            msg.get("type"),
            request_id,
        )
        # heartbeat fast-path. Handle heartbeat INLINE in
        # the read loop BEFORE awaiting ``dispatch()`` so the
        # heartbeat-ack is not delayed by an in-flight long
        # dispatch (e.g. ``download_model``, ``transcribe``) running
        # on the dispatch pool. The Rust host's liveness probe
        # (3 consecutive misses ≥30s → respawn, see ADR-0018 /
        # ADR-0020 §10) would otherwise fire spuriously during a
        # legitimate long-running command — restarting the sidecar
        # mid-download and forcing the user to retry. Bypassing the
        # dispatch pool keeps the heartbeat-ack latency at the
        # ``websocket.send()`` round-trip (~1 ms loopback) instead
        # of the dispatch-pool queue depth.
        if msg.get("type") == "heartbeat":
            # Cheap heartbeat-specific rate cap. The fast-path bypasses
            # the dispatch-pool / ADR-0019 limiter, so without this cap
            # a flood of ``{"type":"heartbeat"}`` frames would be acked
            # at line rate, starving the event loop. Allow at most
            # ``_HEARTBEAT_RATE_MAX_PER_WINDOW`` per
            # ``_HEARTBEAT_RATE_WINDOW_SECONDS``; drop the rest WITHOUT
            # acking (a well-behaved host sending 1/10s will never
            # trip this — even a 10x-over-eager retry loop has room).
            now = time.monotonic()
            window_edge = now - _HEARTBEAT_RATE_WINDOW_SECONDS
            while heartbeat_window and heartbeat_window[0] < window_edge:
                heartbeat_window.popleft()
            if len(heartbeat_window) >= _HEARTBEAT_RATE_MAX_PER_WINDOW:
                log.warning(
                    "[SIDECAR-WS] heartbeat rate cap exceeded (%d in %.0fs) — dropping (no ack)",
                    _HEARTBEAT_RATE_MAX_PER_WINDOW,
                    _HEARTBEAT_RATE_WINDOW_SECONDS,
                )
                continue
            heartbeat_window.append(now)
            # Mirror ``_handle_heartbeat``'s update of
            # ``_last_heartbeat_at`` so the Python-side heartbeat
            # watchdog (if installed) sees fresh liveness.
            with contextlib.suppress(AttributeError):
                server._last_heartbeat_at = now
            ack: dict[str, object] = {"type": "heartbeat_ack"}
            if request_id is not None:
                ack["id"] = request_id
            try:
                await websocket.send(json.dumps(ack))
            except Exception:
                log.warning("[SIDECAR-WS] heartbeat ack send failed", exc_info=True)
                break
            continue
        # PERF-005 fast-path (2026-08-30 tray-Restart postmortem): the
        # host's ``relaunch_ack`` must set ``_relaunch_ack_event`` with
        # ~1 ms latency. Routing it through the dispatch closure
        # (rate-limiter + ``ws_dispatch_pool`` executor round-trip)
        # raced the sidecar's 0.5 s ``wait_for_relaunch_ack`` timeout —
        # observed as "relaunch_ack timed out after 0.500s" WHILE the
        # host log showed "relaunch_ack frame sent", producing a
        # double-restart. Handle INLINE like heartbeat: set the event,
        # charge no rate-limit cost, send nothing (fire-and-forget).
        if msg.get("type") == "relaunch_ack":
            with contextlib.suppress(AttributeError):
                server._relaunch_ack_event.set()
            continue
        # PIPELINED DISPATCH (2026-08-30 tray-Restart postmortem): the
        # read loop previously ``await``ed each dispatch INLINE, so a
        # long-running handler (``restart_app``'s 4.6 s teardown) BLOCKED
        # frame processing for its whole duration — the host's
        # ``relaunch_ack`` starved behind it and the sidecar timed out.
        # Handlers already run concurrently on the ``ws_dispatch_pool``
        # (4 workers); only the response send was serialized. Dispatch
        # each frame as its own task so the loop keeps reading; the task
        # sends the response itself. The host correlates by id, so
        # response ORDER across concurrent dispatches is irrelevant.
        # ``dispatch_tasks`` is drained before this function returns so
        # callers (and the tests) observe every response deterministically.
        task = asyncio.create_task(_dispatch_and_respond(msg, request_id, websocket, dispatch))
        dispatch_tasks.add(task)
        task.add_done_callback(dispatch_tasks.discard)

    # Drain in-flight dispatch tasks on every exit path (normal stream
    # end, heartbeat/`break` paths, connection close) so responses are
    # flushed before the connection teardown cancels the writer. A task
    # whose ``_safe_send`` failed closes the websocket itself; the
    # already-ended read loop is unaffected.
    if dispatch_tasks:
        results = await asyncio.gather(*dispatch_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                log.debug(
                    "[SIDECAR-WS] dispatch task raised during drain",
                    exc_info=(type(r), r, r.__traceback__),
                )


async def _dispatch_and_respond(msg: dict, request_id, websocket, dispatch) -> None:
    """Run one pipelined dispatch + send its response (read-loop task body).

    Extracted from ``_read_loop``'s inline tail when dispatches became
    pipelined tasks (see the comment at the task-creation site). The
    response keeps every ``_safe_send`` defense (off-loop encode, 1 MiB
    cap, send timeout); a non-``"sent"`` status closes the websocket —
    the task-based equivalent of the old inline ``break`` — because a
    dropped/failed response means the peer is waiting for an id that
    will never resolve, and a wedged connection must hand control back
    to the host's reconnect path immediately.
    """
    result = await dispatch(msg, websocket)
    if result is not None:
        if request_id is not None and isinstance(result, dict):
            result = {**result, "id": request_id}
        # Route the dispatch response through ``_safe_send`` so it
        # gets the SAME three DoS defenses the writer task applies
        # to outbound events: off-loop ``json.dumps``, the
        # ``_MAX_FRAME_BYTES`` 1 MiB cap, and the
        # ``_WS_SEND_TIMEOUT_SECONDS`` send timeout. Pre-fix the
        # dispatch response called ``websocket.send(json.dumps(...))``
        # directly, bypassing all three — a handler returning a
        # multi-MiB response (e.g. ``get_history`` /
        # ``get_vocabulary`` for a user with thousands of entries)
        # would (1) block the asyncio loop thread with synchronous
        # ``json.dumps``, (2) block forever on a wedged peer, and
        # (3) exceed the 1 MiB cap that ADR-0020 §10 mandates.
        send_status = await _safe_send(websocket, result)
        log.debug(
            "[SIDECAR-WS] TX response id=%s status=%s",
            request_id,
            send_status,
        )
        if send_status != "sent":
            # ``"dropped"`` (oversized) or ``"failed"`` (timeout /
            # send error). For ``"dropped"`` the host is expecting
            # a response with this ``request_id`` and would hang
            # until its own timeout; the resulting WS close lets the
            # host's reconnect path take over immediately instead of
            # silently dropping the response. For ``"failed"`` the
            # connection is already unreliable or closing.
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="dispatch response not sent")


async def _handle_connection_inner(websocket, server: IPCServer, dispatch, peer) -> None:
    """Auth + read/dispatch loop body ( extraction).

    refactored from a ~375-line monolith into a ~30-line
    coordinator that delegates to named helpers (:func:`_check_duplicate_auth`,
    :func:`_emit_ready_if_first`, :func:`_install_subscriber`,
    :func:`_start_writer`, :func:`_read_loop`). Each helper owns one
    concern; the orchestrator only sequences them + owns the
    connection-lifecycle ``try/except/finally`` that guarantees
    subscriber unsubscribe + writer-task cancel + active-connection
    slot clear on every exit path.
    """
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

    if not await _authenticate(websocket):
        # mirror the TCP path's
        # ``auth_failed`` error frame BEFORE closing the WS with 1008.
        # Wrapped in ``contextlib.suppress`` because the socket may
        # already be half-closed; the close call is authoritative.
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

    if not await _check_duplicate_auth(websocket, server, peer):
        return

    loop = asyncio.get_running_loop()
    # Store the loop reference on the server so
    # ``ws_graceful_shutdown`` (invoked from a non-loop thread by the
    # ``server.stop`` wrapper installed in ``_attach_ws_graceful_shutdown``)
    # can schedule the close coroutine + ``loop.stop``. All WS
    # connections share the same loop (the one running ``run._main``),
    # so this per-connection write is idempotent in production. The
    # ``contextlib.suppress`` guards against a test double where
    # attribute-write may be restricted.
    with contextlib.suppress(Exception):
        server._ws_loop = loop

    # Register the authenticated websocket on
    # ``server._ws_authenticated_conns`` so ``ws_graceful_shutdown``
    # can send ``close(1001)`` to it during graceful shutdown. The
    # websocket is removed in the ``finally`` block below (only if it
    # is still in the set — a concurrent shutdown may have already
    # snapshotted and cleared the set). ``discard`` is used (not
    # ``remove``) so the cleanup is idempotent if the websocket was
    # already removed. The ``getattr(..., None)`` guard skips the
    # registration when the graceful-shutdown hooks were never
    # installed (e.g. the server runs in TCP-only mode, or a test
    # MagicMock without the hooks).
    authed_conns = getattr(server, "_ws_authenticated_conns", None)
    if authed_conns is not None:
        with contextlib.suppress(Exception):
            authed_conns.add(websocket)

    outbound: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    # ``_install_subscriber`` MUST run BEFORE ``_emit_ready_if_first``
    # so the WS subscriber (``_push_to_ws``) is registered on ``event_bus``
    # before the ``ready`` event is published. Pre- the reversed order
    # published to a subscriber set without ``_push_to_ws``, so the WS
    # outbound queue never received ``ready`` and the Tauri host never got
    # it over the WS on first connection (UI stayed un-hydrated).
    #
    # The initial ``state_changed`` snapshot MUST come AFTER ``ready``:
    # the Tauri host's ``wait_for_auth_ok`` accepts ONLY ``auth_ok`` /
    # ``ready`` as the first post-auth frame and treats anything else as
    # a protocol violation (supervisor respawn loop). The snapshot used
    # to live inside ``_install_subscriber`` and raced in ahead of
    # ``ready``, killing every Tauri handshake with
    # "WS auth unexpected frame type: state_changed".
    _push_to_ws = _install_subscriber(server, loop, outbound)
    _emit_ready_if_first(server)
    _emit_initial_state_snapshot(server)
    writer_task = _start_writer(websocket, outbound)

    from voice_typer.server import event_bus

    try:
        await _read_loop(websocket, server, dispatch)
    except ConnectionClosedOK:
        # Clean WebSocket close (1000/1001 normal close) — log at DEBUG.
        log.debug("[SIDECAR-WS] client disconnected cleanly")
    except ConnectionClosedError as exc:
        # Abnormal WebSocket close (1006 / 1011, etc.) — log at DEBUG.
        log.debug("[SIDECAR-WS] connection closed with error: %s", exc)
    except Exception:
        # Genuinely unexpected error — log at WARNING with traceback.
        log.warning("[SIDECAR-WS] connection ended unexpectedly", exc_info=True)
    finally:
        event_bus.unsubscribe(_push_to_ws)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer_task
        # Remove the websocket from the authenticated-conns set so
        # ``ws_graceful_shutdown`` does not send ``close(1001)`` to a
        # websocket that has already closed. ``discard`` is idempotent
        # (no error if the websocket was already removed).
        authed_conns = getattr(server, "_ws_authenticated_conns", None)
        if authed_conns is not None:
            with contextlib.suppress(Exception):
                authed_conns.discard(websocket)
        # clear the active-connection slot ONLY if it still
        # points at THIS socket — a concurrent auth may have already
        # replaced it. Compare-and-clear under ``server._lock``.
        with server._lock:
            if getattr(server, "_active_ws_connection", None) is websocket:
                server._active_ws_connection = None
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
        log.exception(
            "[SIDECAR-WS] the `websockets` package is required for the "
            "Tauri sidecar path. Install with: uv pip install websockets. "
            "Original error: %s",
            exc,
        )
        return 2

    dispatch = _make_dispatch(server)

    # Install the graceful-shutdown hooks BEFORE the loop starts so the
    # ``server.stop`` wrapper (which calls ``ws_graceful_shutdown``) is
    # in place before any connection arrives. ``_attach_ws_graceful_shutdown``
    # is idempotent, so a double-call (e.g. the test harness calls it
    # first, then ``run`` calls it again) is a no-op.
    _attach_ws_graceful_shutdown(server)

    async def _main() -> int:
        # Store the loop reference on the server so
        # ``ws_graceful_shutdown`` (invoked from a non-loop thread by
        # the ``server.stop`` wrapper) can schedule the close coroutine
        # + ``loop.stop``. This is set here (in ``_main``) so it is
        # available even if no WS connection has been established yet
        # (e.g. the host sends ``shutdown`` before the first
        # connection). ``_handle_connection_inner`` ALSO sets this per
        # connection (idempotently — same loop, shared across all
        # connections).
        with contextlib.suppress(Exception):
            server._ws_loop = asyncio.get_running_loop()

        # bind on 127.0.0.1:0 → OS assigns an ephemeral port.
        # max_size enforces the 1 MiB frame cap (ADR-0020 §10).
        async with serve(
            _handler,
            _LOOPBACK_HOST,
            0,
            max_size=_MAX_FRAME_BYTES,
            process_request=_reject_browser_origins,
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
    except RuntimeError as exc:
        # 2026-08-30: ``ws_graceful_shutdown`` stops the loop via
        # ``loop.call_soon_threadsafe(loop.stop)`` while ``_main``'s
        # ``await asyncio.Future()`` is still pending — asyncio.run then
        # raises "Event loop stopped before Future completed". That is
        # the DESIGNED stop path (tray Restart / Quit), not a fault:
        # log it at INFO with exit code 0 instead of a spurious ERROR
        # traceback + exit 1. Any other RuntimeError (or a stop that
        # was NOT requested by the graceful path) still lands in the
        # generic handler below.
        if _is_graceful_loop_stop(server, exc):
            log.info("[SIDECAR-WS] loop stopped by graceful shutdown — clean WS stop")
            return 0
        log.exception("[SIDECAR-WS] fatal error in run()")
        return 1
    except Exception:
        log.exception("[SIDECAR-WS] fatal error in run()")
        return 1


def _is_graceful_loop_stop(server: IPCServer, exc: Exception) -> bool:
    """True when ``exc`` is the designed loop.stop() from
    ``ws_graceful_shutdown`` (flag set by the graceful path + the
    canonical asyncio message). Extracted for direct unit testing.
    """
    return bool(getattr(server, "_ws_graceful_stop_requested", False)) and ("Event loop stopped" in str(exc))
