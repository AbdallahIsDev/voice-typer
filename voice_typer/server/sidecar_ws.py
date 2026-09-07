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
(:func:`run`) and the connection orchestrators
(``_handle_connection`` / ``_handle_connection_inner``).
Focused helper concerns live in the
:mod:`voice_typer.server.sidecar_ws_internals` leaf package and are
re-exported here (see the "Split leaves" comment near the imports for
the pin map):

- ``connection`` — per-connection helpers (duplicate-auth invariant,
  connection semaphore, browser-origin rejection, drop-oldest enqueue,
  ready emit, event-bus subscriber, initial state snapshot).
- ``dispatch`` — the WS dispatch factory (``_make_dispatch``): the
  ADR-0019 per-frame rate-limit gate, the cooperative-shutdown
  gates, the dedicated dispatch thread pool, and the in-flight
  drain coordination consumed by ``ShutdownController._do_cleanup``.
- ``encode_pool`` — WS frame-encode ThreadPoolExecutor lifecycle
  (``_get_ws_encode_pool``, ``shutdown_encode_pool``).
- ``graceful_shutdown`` — ``_attach_ws_graceful_shutdown`` /
  ``_graceful_close_all_conns`` (close(1001) pass + loop stop).
- ``handshake`` — the one-shot bearer-token auth handshake
  (``_authenticate``); its auth-read deadline resolves this module's
  ``_AUTH_TIMEOUT_SECONDS`` alias at call time.
- ``outbound`` — the outbound frame path (``_encode_ws_frame``,
  ``_safe_send``, ``_start_writer``, the 1 MiB frame cap and the
  send timeout): the C-WS-2 TEXT-frame wire contract's enforcement
  site.
- ``read_loop`` — the inbound read/dispatch loop (``_read_loop``
  + ``_dispatch_and_respond``) with the heartbeat fast-path and its
  per-connection sliding-window rate cap.
- ``stdout_banner`` — ``_emit_server_started`` +
  ``_force_line_buffered_stdout`` (the stdout ``server_started`` JSON
  handshake + line buffering for it).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING

# Shared TCP/WS auth-handshake helpers: frame-shape validation
# + token extraction (``extract_auth_token``) and the constant-time
# token comparison (``tokens_equal``, wrapping ``hmac.compare_digest``)
# live in :mod:`voice_typer.server.ipc.auth` so the two transports
# cannot silently drift — the WS consumer is
# :mod:`voice_typer.server.sidecar_ws_internals.handshake`
# (``_authenticate``; see its DEDUP note). That module also owns the
# shared auth-read deadline (``AUTH_READ_TIMEOUT_SECONDS``), imported
# below so the WS and TCP auth-timeout budgets are single-sourced
# instead of manually synced (they used to be two 5.0 literals with a
# keep-in-sync comment).
from voice_typer.server.ipc.auth import AUTH_READ_TIMEOUT_SECONDS

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
from voice_typer.server._paths import LOOPBACK_HOST as _LOOPBACK_HOST

# Module objects of the split leaves — the canonical observers resolve
# the moved functions through THESE attribute reads at call time
# (C-ARCH-2 canonical form) so owning-submodule patches are observed
# by production: run() → dispatch._make_dispatch /
# stdout_banner._emit_server_started,
# _dispatch_and_respond → outbound._safe_send,
# _handle_connection_inner → handshake._authenticate /
# read_loop._read_loop / outbound._start_writer.
from voice_typer.server.sidecar_ws_internals import (  # C-ARCH-2 canonical call-time observers
    dispatch as _dispatch_mod,
    handshake as _handshake_mod,
    outbound as _outbound_mod,
    read_loop as _read_loop_mod,
    stdout_banner as _stdout_banner_mod,
)

# ── Split leaves ─────────────────────────────────────────────────────
# The once-monolithic sidecar_ws.py (2081 LOC, 8+ concerns) was split
# into focused leaf modules under
# ``voice_typer/server/sidecar_ws_internals/`` (the history_db.py /
# history_db_internals/ precedent — deliberately NOT a sidecar_ws/
# package, which would move this file and break the ~14 test files
# that pin the literal voice_typer/server/sidecar_ws.py path).
#
# This module stays CANONICAL: the file-text-pinned functions that
# remain here are _handle_connection_inner, run, and
# _is_graceful_loop_stop, plus the module-level constants whose
# source greps read this file. The moved symbols are re-exported
# below so every existing import path AND monkeypatch target keeps
# working.
#
# C-ARCH-2 PATCH-PATH NOTE: for every name extracted to a leaf, the
# OWNING-SUBMODULE form is the canonical patch path — production
# observers resolve them through sibling MODULE-OBJECT reads at call
# time, so a ``monkeypatch.setattr(<owning submodule>, "X", ...)`` is
# observed. Patching the RE-EXPORT on this module is
# legacy-compatible only for names whose observer still lives here
# and reads the bare name (see the per-leaf pin notes below).
#
# Pin map for the re-exported names:
# - _get_ws_encode_pool / shutdown_encode_pool — referenced by name
#   in ``_safe_send`` (sidecar_ws_internals/outbound.py; the
#   whole-module getsource pin in
#   tests/test_ipc_server.py::TestWriterEncodesOnce reads that leaf).
#   The ``_ws_encode_pool_singleton`` global is NOT re-exported (a
#   value re-export would go stale the moment the leaf's accessors
#   rebind it); it lives in sidecar_ws_internals/encode_pool.py with
#   its accessors.
# - _encode_ws_frame / _safe_send / _start_writer /
#   _WS_SEND_TIMEOUT_SECONDS / _MAX_FRAME_BYTES — OWNED by
#   sidecar_ws_internals/outbound.py. Direct calls
#   (``sidecar_ws._safe_send(ws, event)`` etc.) and getsource pins
#   follow the re-exported function objects; the canonical observers
#   (read_loop._dispatch_and_respond → _safe_send,
#   _handle_connection_inner → _start_writer,
#   run → serve(max_size=)) resolve via module-object
#   reads / the value alias, so tests lower the send timeout by
#   patching sidecar_ws_internals.outbound._WS_SEND_TIMEOUT_SECONDS
#   (tests/test_sidecar_ws_permissions_fixes.py). The C-WS-2
#   TEXT-frame wire contract's enforcement site moved WITH them.
# - _read_loop / _dispatch_and_respond / _HEARTBEAT_RATE_WINDOW_SECONDS
#   / _HEARTBEAT_RATE_MAX_PER_WINDOW — OWNED by
#   sidecar_ws_internals/read_loop.py. Direct calls
#   (``sidecar_ws._read_loop(ws, server, dispatch)`` —
#   tests/test_sidecar_ws.py, tests/test_sidecar_ws_permissions_fixes.py,
#   tests/test_sidecar_ws_relaunch_ack_fastpath.py) and the
#   ``inspect.getsource(sidecar_ws._read_loop)`` pins follow the
#   re-exported function object (the getsource assertions require
#   the rate-cap constant names in the body, so the constants moved
#   with the loop; THIS module keeps pure value aliases for the
#   read/assert surface — nothing rebinds them, so they cannot go
#   stale). The canonical observer (_handle_connection_inner) resolves
#   the loop via the ``_read_loop_mod`` module-object read, and
#   ``_dispatch_and_respond`` resolves ``_safe_send`` through the
#   ``_outbound_mod`` module-object read (both C-ARCH-2 canonical).
# - _graceful_close_all_conns / _attach_ws_graceful_shutdown — driven
#   via ``sidecar_ws._attach_ws_graceful_shutdown(server)`` by
#   tests/test_sidecar_ws.py.
# - _authenticate — OWNED by sidecar_ws_internals/handshake.py. Direct
#   calls (``sidecar_ws._authenticate(ws)`` — the mig15-17 ws_hmac
#   suites, tests/tauri/test_sidecar_ws_unit.py,
#   tests/test_sidecar_ws_protocol_version.py) and the getsource pins
#   (tests/test_sidecar_ws_bearer_token_doc.py) follow the re-exported
#   function object; the token-security source greps in the mig15-17
#   ws_hmac suites read the handshake leaf concatenated after this
#   file. The canonical observer (_handle_connection_inner) resolves
#   the handshake via the ``_handshake_mod`` module-object read
#   (C-ARCH-2 canonical), and the function resolves its auth-read
#   deadline from THIS module's ``_AUTH_TIMEOUT_SECONDS`` alias at
#   call time so the mig15-17 / unit-suite patch surface keeps
#   working (see the alias block above).
# - _force_line_buffered_stdout — PATCHED by the mig15/mig16/mig17
#   ws_hmac suites; observed by run() (canonical).
# - _emit_server_started — OWNED by
#   sidecar_ws_internals/stdout_banner.py. Direct calls
#   (``sidecar_ws._emit_server_started(port, protocol)`` — the
#   mig15-17 ws_hmac / unit suites, tests/test_app_sidecar_protocol.py,
#   tests/tauri/mig19/test_phase4_validation.py) follow the re-exported
#   function object; the ``"def _emit_server_started"`` + payload-shape
#   greps in the mig15-17 externalbin_spawn suites read the
#   stdout_banner leaf concatenated after this file (their
#   ``sidecar_ws_source`` fixture). run() (canonical) resolves the emit
#   via the ``_stdout_banner_mod`` module-object read at call time and
#   passes PROTOCOL_VERSION (see tests/test_app_sidecar_protocol.py's
#   run()-call-site source pin).
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
# - _make_dispatch — OWNED by sidecar_ws_internals/dispatch.py. Direct
#   calls (``sidecar_ws._make_dispatch(server)`` — mig15-17 ws_hmac
#   suites, tests/test_ipc_server.py, rate-limiter chokepoint tests)
#   keep working via the re-export; ``inspect.getsource`` pins follow
#   the function object; run() (canonical) resolves the factory via
#   the ``_dispatch_mod`` module-object read at call time, so tests
#   stub the factory by patching
#   ``voice_typer.server.sidecar_ws_internals.dispatch._make_dispatch``
#   (tests/test_sidecar_ws_origin_check.py). Source-grep pins on the
#   factory body (tests/test_shutdown_ws_db_race.py, mig19
#   test_wire_swap_recovery.py) read the owning leaf file.
from voice_typer.server.sidecar_ws_internals.connection import (  # noqa: F401
    _check_duplicate_auth,
    _emit_initial_state_snapshot,
    _emit_ready_if_first,
    _enqueue_safe,
    _get_ws_connection_semaphore,
    _install_subscriber,
    _reject_browser_origins,
)
from voice_typer.server.sidecar_ws_internals.dispatch import _make_dispatch  # noqa: F401
from voice_typer.server.sidecar_ws_internals.encode_pool import (  # noqa: F401
    _get_ws_encode_pool,
    shutdown_encode_pool,
)
from voice_typer.server.sidecar_ws_internals.graceful_shutdown import (  # noqa: F401
    _attach_ws_graceful_shutdown,
    _graceful_close_all_conns,
)
from voice_typer.server.sidecar_ws_internals.handshake import _authenticate  # noqa: F401
from voice_typer.server.sidecar_ws_internals.outbound import (  # noqa: F401
    _WS_SEND_TIMEOUT_SECONDS,
    _encode_ws_frame,
    _safe_send,
    _start_writer,
)
from voice_typer.server.sidecar_ws_internals.read_loop import (  # noqa: F401
    _dispatch_and_respond,
    _read_loop,
)
from voice_typer.server.sidecar_ws_internals.stdout_banner import (  # noqa: F401
    _emit_server_started,
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

# ADR-0020 §10: 1 MiB WS frame cap — OWNED by
# :mod:`voice_typer.server.sidecar_ws_internals.outbound` (where
# ``_safe_send`` enforces the outbound half; the docstring context on
# the constant lives there: download_progress and
# vocabulary_suggestion can carry large payloads; without a cap a
# malformed/huge frame can OOM the client). The value alias below is
# read by ``run()``'s ``serve(..., max_size=_MAX_FRAME_BYTES)`` call —
# the exact ``max_size=_MAX_FRAME_BYTES`` literal is source-grepped by
# tests/tauri/mig19/test_wire_swap_recovery.py — and by the value
# assertions in the mig15-17 / unit suites. Nothing rebinds the
# constant in production and no test patches it, so the alias cannot
# go stale; if the cap ever needs test control, patch the OWNING
# module (sidecar_ws_internals.outbound).
_MAX_FRAME_BYTES: int = _outbound_mod._MAX_FRAME_BYTES

# Auth frame timeout (seconds). A client that connects but never
# sends the auth frame must not hold the connection indefinitely —
# the budget is single-sourced as ``AUTH_READ_TIMEOUT_SECONDS`` in
# :mod:`voice_typer.server.ipc.auth` and imported by BOTH transports
# (the WS path — via the handshake leaf — and the TCP path in
# ``ipc/transport_tcp.py::_handle_tcp_connection``), so the two
# handshakes cannot drift apart (previously each transport carried
# its own 5.0 literal with a comment requiring manual sync — that
# duplication is what this single-sourcing removed).
#
# The module-level alias below preserves the historical patch
# surface: tests read/patch ``sidecar_ws._AUTH_TIMEOUT_SECONDS``
# (tests/tauri/mig15-17 ``ws_hmac`` suites, tests/tauri/
# test_sidecar_ws_unit.py) and ``_authenticate`` — now owned by
# :mod:`voice_typer.server.sidecar_ws_internals.handshake` —
# resolves the deadline from THIS module's attribute at call time
# (module-object read, C-ARCH-2 canonical form), so the patch is
# observed exactly as it was when the 5.0 literal lived here.
_AUTH_TIMEOUT_SECONDS = AUTH_READ_TIMEOUT_SECONDS

# concurrent-connection limit (DoS protection).
_MAX_WS_CONNECTIONS = 16

# Heartbeat fast-path rate cap — OWNED by
# :mod:`voice_typer.server.sidecar_ws_internals.read_loop` (moved with
# ``_read_loop`` whose heartbeat fast-path enforces the cap and whose
# body references the constant NAMES; the full design comment lives
# there). Value aliases below preserve the historical read/assert
# surface on THIS module: tests read
# ``sidecar_ws._HEARTBEAT_RATE_MAX_PER_WINDOW`` /
# ``_HEARTBEAT_RATE_WINDOW_SECONDS`` as plain values
# (tests/test_sidecar_ws_permissions_fixes.py). Nothing rebinds the
# constants in production and no test patches them, so the aliases
# cannot go stale; if the cap ever needs test control, patch the
# OWNING module (sidecar_ws_internals.read_loop).
_HEARTBEAT_RATE_WINDOW_SECONDS: float = _read_loop_mod._HEARTBEAT_RATE_WINDOW_SECONDS
_HEARTBEAT_RATE_MAX_PER_WINDOW: int = _read_loop_mod._HEARTBEAT_RATE_MAX_PER_WINDOW

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


async def _handle_connection_inner(websocket, server: IPCServer, dispatch, peer) -> None:
    """Auth + read/dispatch loop body ( extraction).

    refactored from a ~375-line monolith into a short
    coordinator that delegates to named helpers (:func:`_check_duplicate_auth`,
    :func:`_emit_ready_if_first`, :func:`_install_subscriber`,
    :func:`_start_writer`, :func:`_read_loop`). Each helper owns one
    concern; the orchestrator only sequences them + owns the
    connection-lifecycle ``try/except/finally`` that guarantees
    subscriber unsubscribe + writer-task cancel + active-connection
    slot clear on every exit path.
    """
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

    if not await _handshake_mod._authenticate(websocket):
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
    writer_task = _outbound_mod._start_writer(websocket, outbound)

    from voice_typer.server import event_bus

    try:
        await _read_loop_mod._read_loop(websocket, server, dispatch)
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

    # Resolve the dispatch factory through the owning leaf's module
    # object at CALL time (C-ARCH-2 canonical patch form): a test that
    # patches ``sidecar_ws_internals.dispatch._make_dispatch`` is
    # observed here, and the re-export on this module stays a pure
    # compatibility surface.
    dispatch = _dispatch_mod._make_dispatch(server)

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
            _stdout_banner_mod._emit_server_started(port, PROTOCOL_VERSION)
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
