"""TCP transport mixin for the IPC server (Phase 4.5 split).

Extracted from the original ``voice_typer/server/ipc_server.py``
god-module. Contains the ``TCPTransportMixin`` class — the TCP transport
methods (``start_tcp``, ``_accept_tcp``, ``_run_tcp_handler_safely``,
``_handle_tcp_connection``, ``_on_ipc_client_disconnect``) that are
mixed into :class:`IPCServer` via multiple inheritance.

The mixin accesses instance state (``self._lock``, ``self._tcp_client``,
``self._tcp_write_lock``, ``self._tcp_worker_pool`` etc.) which is
declared on :class:`IPCServer` itself — the mixin provides only the
method bodies.
"""

import contextlib
import hmac
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from voice_typer.server.handlers._log import log
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR
from voice_typer.server.ipc.rate_limiter import (
    _HEARTBEAT_INTERVAL_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
    _get_rate_limiter,
)
from voice_typer.server.ipc.transport import _TCPLineIO
from voice_typer.server.ipc.validation import ErrorCodes
from voice_typer.server.keyboard_ownership import keyboard_ownership

# ─── IPC wire protocol versioning ──────────────────
#
# Single source of truth for the IPC wire protocol version. Bump on any
# wire-incompatible change to the auth frame shape or any command's
# request/response schema. The auth handshake validates this BEFORE the
# token check so a stale client gets a structured
# ``server.protocol_version_mismatch`` error instead of an opaque
# ``auth_failed``. See ADR-0004 (IPC protocol ADR) for the versioning
# contract.
#
# Validate-if-present semantics: a missing ``protocol_version`` field is
# accepted (legacy senders continue to the token check), only an
# explicit mismatch is rejected. This makes the change fully backward
# compatible — new senders opt in by sending ``"protocol_version": 1``
# and get a structured rejection on mismatch.
IPC_PROTOCOL_VERSION: int = 1
# Error code emitted on the version-mismatch path. Registered in
# the central ``ErrorCodes`` registry (``ipc/validation.py``) so the
# renderer's TS ``ErrorCodes`` union and the cross-language parity test
# can verify all three language constants agree. The module-level alias
# is kept for backward-compat with existing tests that import
# ``PROTOCOL_VERSION_MISMATCH_CODE`` directly (see
# ``tests/test_dr21_protocol_version.py``); new code should reference
# ``ErrorCodes.PROTOCOL_VERSION_MISMATCH`` instead.
PROTOCOL_VERSION_MISMATCH_CODE = ErrorCodes.PROTOCOL_VERSION_MISMATCH


class TCPTransportMixin:
    """TCP transport methods for :class:`IPCServer`.

    Provides ``start_tcp`` / ``_accept_tcp`` / ``_run_tcp_handler_safely``
    / ``_handle_tcp_connection`` / ``_on_ipc_client_disconnect``. The
    mixin assumes the host class declares the TCP-related instance
    attributes (``_tcp_mode``, ``_tcp_client``, ``_tcp_worker_pool``,
    ``_tcp_server_socket``, ``_pending_tcp``, ``_lock``, etc.).
    """

    def start_tcp(self, port) -> None:
        """Start a TCP server that accepts one Electron connection.

         fix: ``port`` may be either:

        - an ``int`` (legacy / backward-compatible) — this method will
          create and bind its own socket to ``127.0.0.1:port``.  There
          is an inherent race window between this call and the bind
          (another local process could grab the port).
        - a ``(port_int, bound_socket)`` tuple (gold-standard — no race
          window).  The caller has already bound the socket (typically
          via :func:`_pick_available_port`); this method simply calls
          ``listen()`` on it and starts accepting connections.  The
          kernel guarantees no other process can claim the port between
          the probe and the listen.
        """
        self._tcp_mode = True
        # lazily create the worker pools. Two pools are used so that
        # long-lived blocking connection read-loops cannot starve
        # short-lived dispatch submissions:
        #
        # - ``_tcp_worker_pool`` (max_workers=8) accepts connections
        #   off the accept-loop thread and runs each connection's auth
        #   handshake + dispatch read-loop. A worker is held for the
        #   ENTIRE connection lifetime (the read-loop blocks on
        #   ``readline`` until the client disconnects). 8 workers
        #   headroom-accounts for reconnect storms and multi-window
        #   Electron (production has 1 client).
        # - ``_tcp_dispatch_pool`` (max_workers=4) runs
        #   ``_tcp_dispatch_and_respond`` submissions off the read
        #   loop so a slow handler (e.g. ``download_model``, up to
        #   120s) does not head-of-line block subsequent commands on
        #   the same connection.
        #
        # Pre-split both work types shared ``_tcp_worker_pool`` with
        # ``max_workers=4``: with 4 concurrent authenticated
        # connections, all 4 workers were stuck on read-loops and
        # dispatch submissions queued indefinitely (full starvation).
        # Reusing the pools across start_tcp() calls is fine; they're
        # only torn down by stop().
        if self._tcp_worker_pool is None:
            self._tcp_worker_pool = ThreadPoolExecutor(
                max_workers=8,
                thread_name_prefix="tcp-worker",
            )
        if self._tcp_dispatch_pool is None:
            self._tcp_dispatch_pool = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix="tcp-dispatch",
            )
        t = threading.Thread(
            target=self._accept_tcp,
            args=(port,),
            daemon=True,
        )
        t.start()

    def _accept_tcp(self, port) -> None:
        """Accept one connection, then run the TCP IPC loop.

        the first line from the client must be a JSON object
        with ``{"type": "auth", "token": "<session-token>"}`` matching
        the ``VOICE_TYPER_IPC_TOKEN`` env var set by the Electron
        parent.  If the token is missing or doesn't match, the
        connection is dropped immediately.  This prevents any local
        process from connecting to 127.0.0.1:9876 and sending
        ``quit_app`` / ``set_config`` / etc.

        The token is generated by the Electron main process (see
        ``client/src/main/index.ts:startPython``) and passed to the
        Python subprocess via the ``VOICE_TYPER_IPC_TOKEN`` env var.
        Both sides see the same random per-launch value; no other
        process can know it.

        ``port`` may be either an ``int`` (legacy) or a
        ``(port_int, bound_socket)`` tuple (gold-standard — eliminates
        the probe-then-bind race window).  See :meth:`start_tcp`.
        """
        # Read the expected token from the env var set by Electron.
        expected_token = os.environ.get(IPC_TOKEN_ENV_VAR, "")
        if not expected_token:
            # mirror the WS path (sidecar_ws._authenticate) — refuse
            # ALL connections when the token is unset. The host must always
            # set this env var; an unset token means any local process
            # could otherwise connect to 127.0.0.1:9876 and dispatch
            # arbitrary IPC commands (quit_app, set_config, etc.).
            #
            # We still bind+listen (so stop()/socket cleanup semantics work
            # — see test_accept_loop_can_be_stopped), but every accepted
            # connection is immediately closed in _handle_tcp_connection
            # below before any auth or dispatch runs.
            log.error(
                "[TCP] VOICE_TYPER_IPC_TOKEN not set — refusing ALL connections "
                "(the host must always set this env var)."
            )

        # unpack the (port, bound_socket) tuple if provided — the
        # socket is already bound, so we skip the bind() call entirely
        # and go straight to listen().  This eliminates the race window
        # where another local process could grab the port between the
        # probe close() and the real bind().
        if isinstance(port, tuple):
            port_num, bound_sock = port
            server = bound_sock
            try:
                server.listen(1)
                log.info(
                    "[TCP] listening on 127.0.0.1:%d (pre-bound socket — no race window)",
                    port_num,
                )
            except Exception:
                log.exception("[TCP] failed to listen on pre-bound socket port %d", port_num)
                # Make sure we don't leak the socket on listen failure.
                with contextlib.suppress(OSError):
                    server.close()
                return
        else:
            port_num = port
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind(("127.0.0.1", port_num))
                server.listen(1)
                log.info("[TCP] listening on 127.0.0.1:%d", port_num)
            except Exception:
                log.exception("[TCP] failed to bind on port %d", port_num)
                # Make sure we don't leak the socket on bind failure.
                with contextlib.suppress(OSError):
                    server.close()
                return

        # store the listening socket on the instance so
        # stop() can close it to unblock the accept() call below.
        self._tcp_server_socket = server

        # accept connections in a loop so that a network
        # blip, sleep/resume, or Electron crash+restart doesn't brick
        # the Python IPC forever. Previously `server.close()` was
        # called after the first accept, so a single disconnect meant
        # no reconnection was possible until the Python process was
        # manually restarted.
        #
        # We check `self._running` (the canonical lifecycle flag set to
        # False by stop()) instead of the legacy getattr(self, "_stopped",
        # False) — that flag was never set anywhere, so the loop only
        # exited via OSError when stop() closed the socket (which it
        # couldn't do, because the socket was a local variable).
        while self._running:
            try:
                conn, addr = server.accept()
                log.info("[TCP] client connected from %s:%d", *addr)
                # SEC: set a defensive blocking budget on the accepted
                # socket BEFORE submitting it to the worker pool, so a
                # backlog of queued connections (worker pool saturated
                # by a flood-connect attack) cannot hold worker threads
                # indefinitely once they do start processing. The
                # connection handler re-applies the auth + idle-read
                # timeouts after it takes over.
                with contextlib.suppress(OSError, AttributeError):
                    conn.settimeout(10.0)  # socket may be a mock in tests
            except OSError:
                # Server socket closed during shutdown (stop() called
                # server_sock.close()).
                break
            # hand the connection off to a worker thread
            # IMMEDIATELY so a slow/malicious client cannot block the
            # accept loop. Previously _handle_tcp_connection was called
            # inline here, so a client that opened a connection and sent
            # nothing would stall the accept loop for the full 5-second
            # auth timeout (soft DoS) — any other client that connected
            # during that window would be queued in the kernel backlog
            # and not picked up until the stalled auth timed out. The
            # auth handshake (and its timeout) now runs on a worker
            # thread, leaving the accept loop free to accept the next
            # connection right away.
            pool = self._tcp_worker_pool
            if pool is None:
                # Defensive: pool was never created (shouldn't happen
                # since start_tcp creates it before starting the accept
                # thread). Close the connection and keep looping.
                with contextlib.suppress(OSError):
                    conn.close()
                continue
            # wrap pool.submit in try/except RuntimeError so a
            # race between accept-loop read of _tcp_worker_pool and stop()'s
            # pool.shutdown(wait=False, cancel_futures=True) does not kill
            # the accept thread with "cannot schedule new futures after
            # shutdown". The just-accepted conn socket must be closed to
            # avoid leak; the loop then breaks because the pool is gone.
            try:
                pool.submit(self._run_tcp_handler_safely, conn, addr, expected_token)
            except RuntimeError:
                with contextlib.suppress(OSError):
                    conn.close()
                break
            # Loop back to accept the next connection

        # shut down the worker pools now that no new connections
        # will arrive. cancel_futures=True drops queued (not-yet-started)
        # submissions; in-flight workers are responsible for their own
        # teardown (the auth timeout + dispatch loop's OSError handling
        # ensure they exit promptly when their socket is closed).
        # The dispatch pool is shut down first so its in-flight
        # dispatches can finish writing their responses before the
        # connection handlers' sockets are torn down.
        dispatch_pool = self._tcp_dispatch_pool
        if dispatch_pool is not None:
            dispatch_pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_dispatch_pool = None
            dispatch_join = threading.Thread(target=dispatch_pool.shutdown, kwargs={"wait": True}, daemon=True)
            dispatch_join.start()
            dispatch_join.join(timeout=5.0)
            if dispatch_join.is_alive():
                log.warning("[SHUTDOWN] tcp_dispatch_pool did not drain in 5s — proceeding anyway")
        pool = self._tcp_worker_pool
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._tcp_worker_pool = None
            # Bound the in-flight handler drain so teardown doesn't
            # race with running handlers. ``shutdown(wait=False)`` only
            # cancels queued futures; in-flight handlers keep running on the
            # pool's worker threads. We drain them with a hard 5s deadline
            # on a daemon thread so the accept loop's exit never blocks
            # indefinitely.
            join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
            join_thread.start()
            join_thread.join(timeout=5.0)
            if join_thread.is_alive():
                log.warning("[SHUTDOWN] tcp_worker_pool did not drain in 5s — proceeding anyway")

        with contextlib.suppress(OSError):
            server.close()
        # Clear the instance reference so a subsequent start_tcp() can
        # store a fresh socket without confusion.
        if self._tcp_server_socket is server:
            self._tcp_server_socket = None

    def _run_tcp_handler_safely(self, conn, addr, expected_token: str) -> None:
        """run ``_handle_tcp_connection`` on a worker thread.

        Wraps the handler with the same exception handling that the
        accept loop used to apply inline, so an unexpected exception
        in one handler doesn't silently kill the worker thread (which
        would otherwise be reported only via the ThreadPoolExecutor's
        internal error handler and easy to miss in production logs).
        """
        try:
            self._handle_tcp_connection(conn, addr, expected_token)
        except OSError:
            # Routine: the handler already logged the disconnect at
            # DEBUG/INFO and ran its teardown. Nothing to surface.
            log.debug("[TCP] connection handler completed")
        except Exception:
            # Anything else escaping the handler (e.g. teardown in
            # _on_ipc_client_disconnect raising) is unexpected and
            # must be visible in production logs.
            log.warning(
                "[TCP] connection handler terminated unexpectedly",
                exc_info=True,
            )

    def _handle_tcp_connection(self, conn, addr, expected_token: str) -> None:
        """Handle a single TCP client connection (auth + dispatch loop).

        Extracted from _accept_tcp by  so the accept loop
        can handle reconnections.

        The auth handshake is now performed OUTSIDE
        ``self._lock`` to prevent a single-connection DoS. Previously,
        a client that opened a TCP connection and sent nothing would
        block the dispatcher thread AND hold ``self._lock``
        indefinitely, freezing the entire IPC server (every ``push()``
        event from any thread would block on the lock). Now the auth
        read has a 5-second timeout and the lock is only acquired
        after auth succeeds, to install the client and flush pending
        events. The token comparison uses ``hmac.compare_digest`` for
        constant-time comparison so a timing side-channel cannot recover
        the token byte-by-byte.
        """
        # Set a read timeout BEFORE the auth readline so a
        # malicious client that connects but sends nothing can't hold
        # the thread indefinitely.
        _tcp_auth_timeout_seconds = 5.0
        with contextlib.suppress(OSError, AttributeError):
            conn.settimeout(_tcp_auth_timeout_seconds)  # socket may be a mock in tests

        # enable TCP_NODELAY on the accepted server-side socket so
        # small push events (bubble_level at 15-50 Hz, heartbeat_ack) are
        # not delayed by Nagle's algorithm coalescing them into larger
        # segments. Nagle defaults to up to 40ms of coalescing delay on
        # loopback, which directly inflates waveform-bubble end-to-end
        # latency. The matching client-side setNoDelay(true) lives in
        # tcp-connect.ts (set immediately after ``new net.Socket()``).
        # ``IPPROTO_TCP`` may be unavailable on non-TCP mock sockets in
        # tests, so the setsockopt is wrapped in suppress(OSError,
        # AttributeError) — same defensive pattern as the settimeout
        # above.
        with contextlib.suppress(OSError, AttributeError):
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # mirror the WS path — if the token is unset, refuse the
        # connection immediately. We log ERROR once at bind time (above)
        # and once per connection so a misconfigured launch surfaces in
        # either spot. Closing the conn unblocks the accept loop so the
        # server stays responsive to stop().
        if not expected_token:
            log.error(
                "[TCP] refusing connection from %s:%d — VOICE_TYPER_IPC_TOKEN not set",
                addr[0],
                addr[1],
            )
            with contextlib.suppress(OSError):
                conn.close()
            return

        # Perform the auth handshake OUTSIDE self._lock so
        # a stalled auth read doesn't block push() events from other
        # threads. The ``not expected_token`` guard above returns
        # early, so ``expected_token`` is always truthy here — the old
        # ``if expected_token: ... else: auth_client = _TCPLineIO(conn)``
        # wrapper's else branch was dead code. The auth body runs
        # unconditionally (de-indented).
        auth_client = _TCPLineIO(conn)
        try:
            auth_line = auth_client.readline()
            if not auth_line:
                log.warning("[TCP] client disconnected before sending auth")
                auth_client.close()
                return
            auth_msg = json.loads(auth_line.strip())
            # Validate-if-present the IPC wire
            # protocol version BEFORE the token check. A stale client
            # that explicitly sends ``protocol_version`` with a value
            # that doesn't equal :data:`IPC_PROTOCOL_VERSION` gets a
            # structured ``server.protocol_version_mismatch`` error
            # envelope instead of an opaque ``auth_failed``. Clients
            # that omit the field (legacy senders) continue to the
            # token check unchanged — backward compatible.
            if (
                isinstance(auth_msg, dict)
                and "protocol_version" in auth_msg
                and auth_msg.get("protocol_version") != IPC_PROTOCOL_VERSION
            ):
                log.warning(
                    "[TCP] auth rejected — protocol_version mismatch (client sent %r, server expects %r)",
                    auth_msg.get("protocol_version"),
                    IPC_PROTOCOL_VERSION,
                )
                try:
                    auth_client.write(
                        json.dumps(
                            {
                                "type": "error",
                                "data": {
                                    "code": ErrorCodes.PROTOCOL_VERSION_MISMATCH,
                                    "message": (
                                        f"protocol version mismatch: client sent "
                                        f"{auth_msg.get('protocol_version')!r}, "
                                        f"server requires {IPC_PROTOCOL_VERSION}"
                                    ),
                                    "client_protocol_version": auth_msg.get("protocol_version"),
                                    "server_protocol_version": IPC_PROTOCOL_VERSION,
                                },
                            }
                        )
                        + "\n"
                    )
                    auth_client.flush()
                except Exception as exc:  # noqa: BLE001 — best-effort error frame to a client that's about to be closed
                    log.debug(
                        "[TCP] failed to send protocol_version_mismatch error frame: %s",
                        type(exc).__name__,
                        exc_info=True,
                    )
                auth_client.close()
                return
            # Use hmac.compare_digest for constant-time
            # token comparison so a timing side-channel cannot
            # recover the token byte-by-byte.
            # Check isinstance FIRST so .get() doesn't raise on
            # non-dict JSON values (e.g. 42, [1,2,3], "hi").
            token_valid = (
                isinstance(auth_msg, dict)
                and auth_msg.get("type") == "auth"
                and isinstance(auth_msg.get("token", ""), str)
                and hmac.compare_digest(auth_msg.get("token", ""), expected_token)
            )
            if not token_valid:
                log.warning("[TCP] auth failed — invalid token")
                try:
                    auth_client.write(
                        json.dumps(
                            {
                                "type": "error",
                                "data": {
                                    # Add
                                    # ``code: "auth_failed"`` for
                                    # envelope consistency with
                                    # the other TCP/WS error
                                    # paths (rate_limited,
                                    # invalid_payload,
                                    # internal_error, etc.). The
                                    # WS path closes the socket
                                    # with code 1008 instead of
                                    # emitting an error frame, so
                                    # there is no WS-side code to
                                    # match — but a client reading
                                    # the TCP error frame can now
                                    # distinguish auth failure
                                    # from other errors without
                                    # substring-matching the
                                    # message.
                                    "code": "auth_failed",
                                    "message": "authentication failed",
                                },
                            }
                        )
                        + "\n"
                    )
                    auth_client.flush()
                except Exception as exc:  # noqa: BLE001 — best-effort auth_failed error frame to a client that's about to be closed
                    log.debug(
                        "[TCP] failed to send auth_failed error frame: %s",
                        type(exc).__name__,
                        exc_info=True,
                    )
                auth_client.close()
                return
            log.info("[TCP] auth OK")
        except json.JSONDecodeError:
            log.warning("[TCP] auth failed — invalid JSON on first line")
            auth_client.close()
            return
        except Exception:
            log.warning("[TCP] auth handshake raised", exc_info=True)
            auth_client.close()
            return

        # Set the idle-read timeout on the dispatch-loop socket AFTER
        # auth succeeds, sized to ``_HEARTBEAT_TIMEOUT_SECONDS +
        # _HEARTBEAT_INTERVAL_SECONDS`` so it tracks the watchdog
        # contract (a healthy Electron client heartbeats every
        # ``_HEARTBEAT_INTERVAL_SECONDS``, so a real client never trips
        # this; an authenticated-but-silent client is disconnected
        # instead of holding a worker thread + FD forever —
        # authenticated-idle DoS mitigation).
        #
        # The auth phase keeps its own short 5s timeout (reject stalled
        # clients); this idle-read timeout is applied AFTER auth for the
        # long-lived dispatch loop. Pre-fix the dispatch loop cleared the
        # timeout entirely (``settimeout(None)``), which let a silent
        # authenticated client hold the connection open indefinitely.
        _idle_read_timeout_seconds = _HEARTBEAT_TIMEOUT_SECONDS + _HEARTBEAT_INTERVAL_SECONDS
        with contextlib.suppress(OSError, AttributeError):
            conn.settimeout(_idle_read_timeout_seconds)  # socket may be a mock in tests

        # Now acquire the lock ONLY for the post-auth setup
        # (installing the client + snapshotting pending events). This is
        # a short critical section that can't block on unbounded I/O.
        # the pending-event flush is performed OUTSIDE the lock
        # (mirrors the ``_send`` snapshot-then-flush pattern at the
        # ``pending = list(self._pending_tcp); self._pending_tcp.clear()``
        # block). Pre-fix the lock was held across every
        # ``tcp_client.write`` + ``flush`` of the backlog, so a slow
        # client on first connect could stall all dispatchers.
        with self._lock:
            self._tcp_client = auth_client
            # Snapshot + clear pending under the lock (fast, no I/O).
            pending_flush = list(self._pending_tcp) if self._pending_tcp else None
            if pending_flush:
                self._pending_tcp.clear()

        # Flush the snapshot OUTSIDE the lock — a slow Electron
        # renderer can stall here without blocking other dispatchers.
        if pending_flush:
            for p in pending_flush:
                try:
                    auth_client.write(p + "\n")
                    auth_client.flush()
                except Exception:
                    log.debug("[TCP] pending flush write failed on connect")
                    break

        # emit a state_changed event on connect so the
        # renderer immediately knows the current app state (was
        # previously left stale until the next state transition).
        try:
            current_state = getattr(self.app.tray, "_state", None)
            current_msg = getattr(self.app.tray, "_message", "")
            if current_state is not None:
                self.push(
                    {
                        "type": "state_changed",
                        "data": {
                            "status": getattr(current_state, "value", str(current_state)),
                            "message": current_msg,
                        },
                    }
                )
        except Exception:
            log.debug("[TCP] failed to emit initial state_changed on connect")

        # RELIABILITY-006 + : per-process rate limiter. A buggy or
        # malicious Electron client that flood-dispatches commands would
        # otherwise starve the tray thread. : the limiter is shared
        # across all TCP connections to this server (looked up via
        # ``_get_rate_limiter(self)``) so a local attacker can no longer
        # reset the 200-message burst budget by disconnecting and
        # reconnecting — the 10s sliding window continues to evict old
        # timestamps across reconnects.
        rate_limiter = _get_rate_limiter(self)

        # capture a LOCAL reference to the authenticated client.
        # With the worker-pool fix, multiple handlers can run
        # concurrently (e.g. a slow-auth client still in its 5s auth
        # window while a fast-auth client connects and authenticates).
        # If a second client authenticates, ``self._tcp_client`` is
        # reassigned to the new client; iterating ``self._tcp_client``
        # directly would then read from the WRONG socket. Capturing the
        # local reference here ensures this handler's dispatch loop
        # always reads from the client it authenticated.
        client = auth_client

        try:
            for line in client:
                line = line.strip()
                if not line:
                    continue
                # parse JSON BEFORE the rate-limit check so
                # the request ``id`` (when present) is available for the
                # rate-limit error response — clients using id-based
                # JSON-RPC-style correlation can then match the rejection
                # back to the originating request. Previously the check
                # fired on the raw line BEFORE ``json.loads``, so the
                # rate-limit error response carried no ``id``.
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # The error envelope now
                    # includes a ``code: "invalid_payload"`` field to
                    # match the WS path so the client can distinguish
                    # invalid JSON from rate-limit and dispatch errors.
                    # pass the LOCAL ``client`` (captured at
                    # the top of the dispatch loop) so a concurrent
                    # fast-auth reconnect that reassigns ``self._tcp_client``
                    # doesn't redirect this error to the wrong socket.
                    # ``id`` is unavailable here (json.loads
                    # failed before we could parse it) — match the WS path
                    # which also omits ``id`` on invalid_payload.
                    self._send(
                        {
                            "type": "error",
                            "data": {
                                # Namespaced form (canonical) — see
                                # ``voice_typer/server/ipc/validation.py``
                                # for the migration contract.
                                "code": "client.invalid_payload",
                                "message": "invalid JSON",
                            },
                        },
                        _client=client,
                    )
                    continue
                # Pass ``command=msg_type`` so the per-command
                # cost map (``COMMAND_COSTS``) is applied — e.g.
                # ``download_model`` consumes 50 of the 200 burst units,
                # so a buggy client can fire at most 4 expensive commands
                # per second before the 5th is rejected. Cheap commands
                # (``heartbeat``, ``get_status``) keep the cost-1 behavior.
                # The legacy ``rate_limiter.allow()`` form (no ``command``
                # kwarg) is still supported and treats the call as cost 1.
                msg_type = msg.get("type") if isinstance(msg, dict) else ""
                # Heartbeat fast-path. Handle heartbeat INLINE in
                # the read loop BEFORE ``self._dispatch(msg)`` so the
                # heartbeat-ack is not delayed by an in-flight long
                # dispatch (e.g. ``download_model``,
                # ``transcribe_final``) — Electron's main-process
                # heartbeat watchdog (see ``client/src/main/index.ts``)
                # would otherwise fire spuriously during a legitimate
                # long-running command, restarting the IPC connection
                # mid-transcription. The inline path mirrors
                # ``_handle_heartbeat`` exactly: update
                # ``_last_heartbeat_at`` and write the ``heartbeat_ack``
                # envelope. Bypassing the rate limiter is safe —
                # heartbeats are 1 every 5 s (Electron) / 10 s (Tauri),
                # far below the 200-burst budget.
                if msg_type == "heartbeat":
                    self._last_heartbeat_at = time.monotonic()
                    ack: dict[str, object] = {"type": "heartbeat_ack"}
                    if isinstance(msg, dict) and "id" in msg:
                        ack["id"] = msg["id"]
                    self._send(ack, _client=client)
                    continue
                if not rate_limiter.allow(command=msg_type):
                    # ``allow()`` increments the rejected counter
                    # atomically when it returns False — no separate
                    # ``reject()`` call needed (and calling it would
                    # double-count under the new atomic semantics).
                    #
                    # The error envelope now
                    # includes a ``code: "rate_limited"`` field to
                    # match the WS path (``sidecar_ws._make_dispatch``)
                    # so a client can distinguish rate-limit rejections
                    # from invalid-JSON and dispatch-exception errors
                    # without substring-matching the message text. The
                    # ADR-0020 §2 envelope contract is
                    # ``{"type":"error","data":{"code":<str>,"message":<str>}}``.
                    # pass the LOCAL ``client`` so the error
                    # reaches the originating socket, not a concurrently-
                    # reconnected ``self._tcp_client``.
                    # include ``id`` (when present in the
                    # parsed msg) so the client can correlate the
                    # rejection to the originating request.
                    # ZR-76: route through ``_send_error_envelope`` so
                    # the envelope shape (``type`` / ``data{code,message}``
                    # / optional ``id``) is defined in one place shared
                    # with the dispatch-exception path below.
                    self._send_error_envelope(
                        "client.rate_limited",
                        "rate limit exceeded; backing off",
                        msg=msg,
                        _client=client,
                    )
                    log.warning(
                        "[TCP] rate limit hit (%d rejected)",
                        rate_limiter.rejected_count,
                    )
                    continue
                # Offload ``_dispatch`` to the worker pool so a
                # slow handler (e.g. ``download_model``, up to 120s) does
                # NOT block the read loop from reading subsequent commands
                # from the same Electron client (head-of-line blocking).
                # Mirrors the WS path's ``run_in_executor`` pattern at
                # ``sidecar_ws.py:572`` — but TCP is thread-based (not
                # asyncio), so we use ``ThreadPoolExecutor.submit`` instead
                # of ``loop.run_in_executor``. The Future is discarded
                # because the response is sent from within
                # ``_tcp_dispatch_and_respond`` itself (not returned) —
                # this preserves the existing dispatch + ``_send`` pairing
                # that non-``run_in_executor`` callers depend on (some
                # handlers return ``None`` and send their own response
                # internally, e.g. ``restart_app`` / ``quit_app``; others
                # return a dict that the caller sends).
                # The Electron client already correlates responses to
                # requests out-of-order via the ``id`` field
                # (``sendToPython`` → ``pendingRequests.get(id)`` in
                # ``client/src/main/ipc/tcp-connect.ts``), so dispatching
                # concurrently is safe even though responses may arrive in
                # a different order than requests.
                # heartbeats bypass this entirely (handled inline
                # above) so the heartbeat-ack is never delayed by an
                # in-flight long dispatch.
                self._tcp_dispatch_pool.submit(self._tcp_dispatch_and_respond, msg, client)
        except socket.timeout:  # noqa: UP041 — the idle-read tests pin this exact text in source
            # Idle-read timeout fired — the authenticated client sent
            # nothing within the heartbeat window (authenticated-idle
            # DoS mitigation). Log at WARNING (not the routine disconnect
            # DEBUG) and let the finally block close the connection.
            log.warning("[TCP] idle-read timeout fired for authenticated client — disconnecting")
        except OSError:
            # Routine socket close / EOF: the client disconnected.
            log.debug("[TCP] client connection closed")
        except Exception:
            # Anything else escaping the dispatch loop is unexpected
            # (e.g. rate-limiter state corruption, partial-frame bugs)
            # and was previously swallowed at DEBUG. Surface it.
            log.warning("[TCP] unexpected error in connection loop", exc_info=True)
        finally:
            # close the LOCAL client reference (not
            # ``self._tcp_client``) and only clear the instance field
            # if it still points to us. Another handler may have
            # already replaced ``self._tcp_client`` with a newer
            # authenticated client; blindly closing it would terminate
            # the wrong connection.
            client.close()
            with self._lock:
                if self._tcp_client is client:
                    self._tcp_client = None
            log.info("[TCP] client disconnected")
            # if the frontend crashed mid-capture (before
            # sending ``set_esc_cancel_paused: false``), the backend
            # would otherwise be stuck in ``"hotkey_capture"`` state
            # forever, suppressing all hotkey interactions until
            # restart. Reset keyboard ownership so the next client
            # reconnect starts clean. Skipped during server shutdown
            # so an active recording isn't interrupted by teardown.
            self._on_ipc_client_disconnect("IPC client disconnected")

    def _tcp_dispatch_and_respond(self, msg, client) -> None:
        """Run ``_dispatch`` on the worker pool and send the response.

        This method is submitted to ``_tcp_dispatch_pool`` by
        the TCP read loop (see ``_handle_tcp_connection``) so a
        long-running handler (e.g. ``download_model``, up to 120s) does
        NOT block the read loop from reading subsequent commands from
        the same Electron client. This mirrors the WS path's
        ``run_in_executor(ws_dispatch_pool, server._dispatch, msg)``
        pattern at ``sidecar_ws.py:572`` — adapted for the thread-based
        TCP transport by wrapping the dispatch + response-send in a
        single callable so the read loop can discard the Future.

        The response is sent from WITHIN this method (not returned) so
        the read loop can submit-and-forget. The Electron client
        correlates responses to requests out-of-order via the ``id``
        field (``sendToPython`` → ``pendingRequests.get(id)``), so
        concurrent dispatch is safe even though responses may arrive in
        a different order than requests.

        isolate handler exceptions from socket I/O errors.
        Previously, ANY exception raised by ``self._dispatch(msg)``
        bubbled up to the outer ``except Exception:`` clause in the
        read loop, which logged "client connection closed" at DEBUG and
        disconnected the client. Handler bugs were therefore silently
        swallowed and a single bad handler killed the entire IPC
        session. We now log the exception at ERROR with ``exc_info``
        and send a structured error response so the client gets a clear
        signal and the connection survives. The outer ``except
        Exception:`` in the read loop is now reserved for genuine
        socket I/O errors.
        """
        try:
            result = self._dispatch(msg)
        except Exception as dispatch_exc:
            log.error(
                "[TCP] unhandled exception in dispatch for message type %r: %s",
                msg.get("type") if isinstance(msg, dict) else None,
                dispatch_exc,
                exc_info=True,
            )
            # B-6: preserve the request ``id`` on the error response so
            # clients using ``id``-based request/response correlation
            # (the standard JSON-RPC-like pattern) can match the error
            # back to the originating request. Without this, a buggy
            # handler effectively orphaned every pending request — the
            # client received an ``{"type": "error"}`` with no ``id``
            # and could not tell which request failed. The message
            # stays the generic ``"internal error"`` (we deliberately
            # do NOT leak ``str(dispatch_exc)`` to avoid exposing
            # server internals over IPC).
            # ADR-0020 round-2 fix: add ``code: "internal_error"`` for
            # consistency with other error envelopes (invalid_payload,
            # rate_limited, etc. all carry a ``code`` field). The
            #  fix in usePython.ts and the Rust dispatch()
            # command both read ``code`` with a ``"unknown"`` fallback,
            # so this is backward-compatible but now consistent.
            # pass the LOCAL ``client`` so the error
            # reaches the originating socket, not a concurrently-
            # reconnected ``self._tcp_client``.
            #  align to the namespaced
            # ``server.internal_error`` form so the renderer's
            # ``ErrorEvent.code`` narrowing switches on a single
            # canonical prefix (``server.*``) across the TCP / stdin /
            # WS transports.
            # ZR-76: route through ``_send_error_envelope`` so the
            # envelope shape matches the rate-limit path above (single
            # source of truth for ``type`` / ``data{code,message}`` /
            # optional ``id`` propagation). Sites with extra fields
            # (``protocol_version_mismatch`` carries
            # ``client_protocol_version`` / ``server_protocol_version``;
            # ``auth_failed`` is sent before the dispatch loop captures
            # ``msg``) keep their inline construction.
            self._send_error_envelope(
                "server.internal_error",
                "internal error",
                msg=msg if isinstance(msg, dict) else None,
                _client=client,
            )
            return
        if result is not None:
            # route the dispatch response to the LOCAL
            # client that issued the request, not the (possibly
            # reassigned) ``self._tcp_client``.
            self._send(result, _client=client)

    def _on_ipc_client_disconnect(self, reason: str) -> None:
        """Reset keyboard ownership when the IPC client disconnects.

         (backend ownership watchdog): if the frontend
        crashes mid-capture (before sending
        ``set_esc_cancel_paused: false``), the backend would
        otherwise be stuck in ``"hotkey_capture"`` state forever,
        suppressing all hotkey interactions until restart. Resetting
        ownership here ensures the next client reconnect starts
        clean.

        Skipped during server shutdown (``self._running == False``)
        so an active recording isn't interrupted by the teardown
        sequence — we only want to fire on an *unexpected* client
        disconnect, not on a planned stop().

        The reset is idempotent: calling it when ownership is
        already ``"normal"`` is a no-op. Safe to call from multiple
        disconnect paths (TCP + stdin EOF) — the second call is a
        no-op.
        """
        if not self._running:
            # Server is shutting down (stop() was called). Don't
            # reset ownership — a recording might be in progress
            # and the teardown sequence will handle cleanup.
            log.debug("[IPC] client disconnect during shutdown; skipping keyboard ownership reset")
            return
        keyboard_ownership().reset()
        # Also clear the ESC-pending-capture-exit Event
        # on the hotkey dispatcher. If the frontend crashed mid-capture
        # (ESC pressed but not yet released), the flag would remain set
        # and cause a spurious ``hotkey_capture_cancel`` event on the
        # next ESC press after reconnect. A prior refactor replaced the plain bool
        # with a ``threading.Event`` so the 3 threads that touch this
        # flag (ESC listener / ESC release handler / this IPC worker)
        # cannot race on the read-modify-write cycle. ``.clear()`` is
        # atomic.
        _hotkeys = getattr(self.app, "hotkeys", None)
        if _hotkeys is not None:
            with contextlib.suppress(AttributeError):
                _hotkeys._esc_pending_capture_exit_event.clear()
        log.info("[IPC] keyboard ownership reset to normal (%s)", reason)


__all__ = ["TCPTransportMixin"]
