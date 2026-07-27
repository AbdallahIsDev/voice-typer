"""Output / push mixin for the IPC server (Phase 4.5 split).

Extracted from the original ``voice_typer/server/ipc_server.py``
god-module. Contains:

- :data:`_SHUTDOWN_ALLOWLIST` — push-event types that bypass the
  shutdown suppress.
- :data:`_TCP_PENDING_DRAIN_CAP` / :data:`_TCP_PENDING_BUFFER_CAP` —
  pending-buffer tuning constants hoisted from inline magic numbers in
  ``_send``.
- :class:`OutputMixin` — the ``push`` and ``_send`` methods mixed into
  :class:`IPCServer`.

The mixin accesses instance state (``self._lock``, ``self._tcp_client``,
``self._tcp_write_lock``, ``self._pending_tcp``,
``self._cached_shutting_down`` etc.) which is declared on
:class:`IPCServer` itself.
"""

import contextlib
import json
import logging
from typing import TextIO

from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.rate_limiter import _TCP_WRITE_TIMEOUT_SECONDS
from voice_typer.server.ipc.transport import _TCPLineIO
from voice_typer.server.log_rate_limit import log_rate_limited

# Module-level frozenset of push-event ``type`` values that MUST be
# delivered to the host even when ``_cached_shutting_down`` is True. The
# set is intentionally small — only events whose loss the user would
# perceive as data loss or a stuck restart:
#
# - ``relaunch_app``: the restart signal from ``restart_app()``. If this
#   is suppressed, the host never relaunches and the user's "Restart" tray
#   click silently does nothing (CRITICAL — see the comment in ``_send``
#   for the full chain).
# - ``quit_app``: the quit signal from ``quit()``. Same reasoning — the
#   host needs this to tear down its Python-side state cleanly.
# - ``transcription_final``: the final transcription text. If suppressed,
#   the user sees no result on the Home page (data IS in history_db but
#   the UI never updates — perceived as data loss).
# - ``transcription_partial``: same as above for partial results during
#   streaming dictation.
# - ``vocabulary_suggestion``: vocabulary suggestion the user is waiting
#   for.
#
# Previously this was a 5-element tuple re-allocated on EVERY ``_send``
# call (15-50 Hz waveform-bubble push rate -> 15-50 tuple
# allocations/sec). Hoisting to a module-level frozenset eliminates the
# per-call allocation entirely.
#
# PR-2-FIX-2: expanded from the original ``("relaunch_app", "quit_app")``
# pair to include the content-bearing events above.
# PVT-G5-013: dispatch responses (which carry an ``id`` field) are
# exempted from the shutdown suppress by a separate ``"id" not in msg``
# check in ``_send`` — they are NOT in this allowlist because the allowlist
# is for PUSH events only (no ``id``).
_SHUTDOWN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "relaunch_app",
        "quit_app",
        "transcription_final",
        "transcription_partial",
        "vocabulary_suggestion",
    }
)

# ZR-43 / ZR-70: hoisted from inline magic numbers in ``_send`` so they
# are visible at the module top alongside the other TCP tuning knobs
# (``_TCP_WRITE_TIMEOUT_SECONDS`` in ``ipc/rate_limiter.py``). Both
# constants trade memory for catch-up latency on client reconnect:
#
# * ``_TCP_PENDING_DRAIN_CAP`` — max number of buffered push events we
#   attempt to flush in a single ``_send`` call after a client
#   reconnect.  Larger = slower reconnect (each push is one
#   ``write+flush`` syscall on the audio thread); smaller = more
#   events silently dropped.  100 ~= ~6 s of waveform-bubble level
#   events at 16 Hz, which is the upper bound of what the renderer
#   can plausibly catch up on without jank.
# * ``_TCP_PENDING_BUFFER_CAP`` — hard cap on ``_pending_tcp`` size
#   while the client is disconnected.  When the cap is hit we drop the
#   OLDEST entries (they are stale waveform-bubble events; the
#   authoritative transcription-final events are persisted to
#   ``history_db`` so dropping here is safe).  1000 ~= ~1 minute of
#   disconnect at 16 Hz before we start shedding, which gives the
#   accept loop ample time to pick up a reconnect.
_TCP_PENDING_DRAIN_CAP: int = 100
_TCP_PENDING_BUFFER_CAP: int = 1000


class OutputMixin:
    """Output / push methods for :class:`IPCServer`.

    Provides ``push`` and ``_send``. The mixin assumes the host class
    declares the TCP / stdout transport instance attributes
    (``_lock``, ``_tcp_client``, ``_tcp_write_lock``, ``_pending_tcp``,
    ``_tcp_mode``, ``_cached_shutting_down``).
    """

    def push(self, msg: dict) -> None:
        """Send an unsolicited event (no ``id`` field)."""
        self._send(msg)

    def _send(
        self,
        msg: dict | None,
        _out: TextIO | None = None,
        _client: _TCPLineIO | None = None,
    ) -> None:
        """Serialize *msg* and write it to the active transport.

        NEW-IPC-014 / NEW-CONC-001 / NEW-CONC-003: previously the entire
        send path (json.dumps + sendall + pending drain) ran under
        ``self._lock``, which meant:

        - Every other IPC dispatch command blocked while a slow Electron
          renderer drained its TCP receive buffer (NEW-CONC-001).
        - The audio-callback-spawned bubble_level worker could stall
          inside ``sendall`` with no timeout, holding the lock and
          stalling every other dispatch path (NEW-CONC-003).
        - ``Microphone.tsx::testMicrophone → get_microphones`` saw
          user-visible lag during recording (NEW-CONC-001 details).

        The fix splits the work:
        1. Under the lock: snapshot the current client / mode / pending
           list.  This is the only section that needs mutual exclusion.
        2. Outside the lock: serialize the message, perform the actual
           ``sendall`` (with a write timeout — NEW-CONC-003), and drain
           the pending list.  A slow client can no longer block other
           dispatchers.

        PVT-G5-011: the optional ``_client`` parameter lets a TCP
        dispatch loop write its response to the LOCAL client it
        authenticated (captured at the top of the loop) rather than
        ``self._tcp_client`` — which may have been reassigned to a
        newer connection by a concurrent fast-auth client (SEC-8 race).
        Defaults to ``None`` (fall back to ``self._tcp_client``) so the
        push-event path (``server.push()``) and existing call sites are
        backward-compatible.
        """
        if msg is None:
            return

        # Step 1: snapshot transport state under the lock.  This is fast
        # (no I/O) and is the only section that needs mutual exclusion.
        with self._lock:
            out = _out
            # PVT-G5-011: prefer the caller-provided local client (the
            # one this dispatch loop authenticated) over ``self._tcp_client``
            # (which a concurrent fast-auth reconnect may have replaced).
            # ``_client`` defaults to ``None`` for the push-event path.
            tcp_client = _client if _client is not None else self._tcp_client
            tcp_mode = self._tcp_mode
            # XV-82 / GT-48: snapshot the pending list ONLY when we have
            # a connected client to drain it to. When ``tcp_client`` is
            # None (disconnected), the snapshot+clear is skipped — the
            # tcp_mode branch below appends the new line to the in-memory
            # buffer instead. This eliminates the FIFO race (GT-48) at
            # its root: with no snapshot+clear, no other thread can
            # observe an empty ``_pending_tcp`` mid-snapshot and append
            # a NEW event that the snapshot's re-merge would mis-order.
            pending: list[str] | None = None
            if tcp_client is not None and self._pending_tcp:
                pending = list(self._pending_tcp)
                self._pending_tcp.clear()

        # Step 2: serialize + write OUTSIDE the lock.  A slow client can
        # stall here without blocking other dispatchers.
        line = json.dumps(msg)

        if out is not None:
            # Stdin/stdout mode — used in tests and the legacy console
            # script.  Writes to a TextIO are typically fast (pipe to
            # Electron parent), but still don't need the lock.
            out.write(line + "\n")
            out.flush()
            return

        if tcp_client is not None:
            # QUIT-CLEAN-001: if the app is shutting down, skip the TCP
            # write for non-critical events.  Electron closes its end of
            # the socket as soon as it receives the ``quit_app`` event;
            # any subsequent push from the cleanup path (waveform bubble
            # worker, state-changed hooks, hotkey-backend teardown) would
            # hit a half-closed socket and raise ``[WinError 10053]```.
            #
            # CRITICAL-CRITICAL: the ``relaunch_app`` event is the
            # EXCEPTION.  This event MUST be delivered even during
            # shutdown because it's the signal from restart_app() that
            # tells the host (Tauri ``app.restart()`` / Electron
            # ``app.relaunch() + app.exit(0)``) to relaunch before
            # the Python process exits.  Without it, the restart hangs.
            # PVT-2 cleanup: the published event name is ``relaunch_app``
            # (no longer ``relaunch_electron``); the Rust WS bridge no
            # longer renames it.
            #
            # ``is True`` (rather than a truthiness check) is intentional:
            # the real ``VoiceTyperApp`` sets ``_shutting_down = True``
            # literally, and MagicMock-based test fixtures expose
            # ``_shutting_down`` as a child mock (which is truthy but
            # not ``is True``).  Using ``is True`` keeps the test path
            # exercising the write logic instead of the shutdown short-
            # circuit.
            #
            # Performance: previously this was
            # ``getattr(self.app, "_shutting_down", False) is True`` —
            # a per-call ``getattr`` on a different object (``self.app``
            # is a ``VoiceTyperApp`` instance with a complex MRO) that
            # always invokes ``__getattribute__`` even on hit (~2×
            # slower than a direct attribute access). We now read the
            # cached snapshot ``self._cached_shutting_down`` (refreshed
            # in ``start()`` → False and ``stop()`` → True). The
            # defensive ``getattr(self, ..., False)`` (NOT
            # ``getattr(self.app, ...)``) keeps test fixtures that
            # bypass ``__init__`` (e.g.
            # ``tests/test_ipc_layer_fixes.py::test_send_does_not_snapshot_when_no_client``
            # constructs ``IPCServer.__new__(IPCServer)`` and sets
            # ``server.app._shutting_down = False`` but never sets
            # ``_cached_shutting_down``) working without modification —
            # they get the ``False`` default, matching the previous
            # ``getattr(self.app, "_shutting_down", False)`` behaviour.
            _is_shutting_down = getattr(self, "_cached_shutting_down", False) is True
            msg_type = msg.get("type", "")
            # Allow critical shutdown events through; suppress others.
            # PR-2-FIX-2: expanded allowlist to include content-bearing events
            # that the user is waiting for. transcription_final carries the
            # final transcription text — if it's suppressed during shutdown,
            # the user sees no result on the Home page and perceives data loss
            # (the data IS saved to history_db, but the UI never updates).
            # transcription_partial and vocabulary_suggestion are similarly
            # content-bearing. High-frequency events (bubble_level, audio_level)
            # are still suppressed.
            #
            # The allowlist was previously a 5-element tuple re-allocated on
            # EVERY ``_send`` call (15-50 Hz waveform-bubble push rate →
            # 15-50 tuple allocations/sec). Hoisted to the module-level
            # ``_SHUTDOWN_ALLOWLIST`` frozenset constant near
            # ``_READONLY_COMMANDS`` — eliminates the per-call allocation
            # entirely. ``frozenset`` membership test is O(1) (same as
            # ``tuple.__contains__`` for short tuples, but no allocation
            # overhead).
            _shutdown_allowlist = _SHUTDOWN_ALLOWLIST
            # PVT-G5-013: dispatch responses (which carry an ``id`` field)
            # MUST be exempted from the shutdown suppress — otherwise the
            # client waits forever for a response to an in-flight request
            # that the server has already processed. Only push events
            # (no ``id``) are suppressed; they are replayed via state
            # snapshots on reconnect.
            if _is_shutting_down and "id" not in msg and msg_type not in _shutdown_allowlist:
                with self._lock:
                    if self._tcp_client is tcp_client:
                        with contextlib.suppress(Exception):
                            self._tcp_client.close()
                        self._tcp_client = None
                    # CR-79: re-merge the pending snapshot back into
                    # ``_pending_tcp`` so events queued for this (now-
                    # closed) client are NOT silently lost during the
                    # shutdown short-circuit. The snapshot+clear at the
                    # top of ``_send`` removed them from ``_pending_tcp``;
                    # without this re-merge they would be dropped even
                    # though a fresh reconnect (e.g. Electron restarting
                    # during shutdown) could still drain them. The
                    # critical shutdown events in ``_SHUTDOWN_ALLOWLIST``
                    # bypass this branch entirely and are written below.
                    if pending:
                        # FIFO order: snapshot events (oldest) first,
                        # then any concurrent appends. The 1000-entry
                        # cap from the ``tcp_mode`` branch is enforced
                        # defensively here too so a long-disconnected
                        # client doesn't grow the buffer unboundedly
                        # during shutdown.
                        self._pending_tcp = pending + self._pending_tcp
                        _pending_cap_shutdown = 1000
                        if len(self._pending_tcp) > _pending_cap_shutdown:
                            _dropped = len(self._pending_tcp) - _pending_cap_shutdown
                            del self._pending_tcp[:_dropped]
                return
            # NEW-CONC-003: set a write timeout so a stalled renderer
            # can't block the worker thread indefinitely.  2 seconds is
            # generous for a localhost TCP write — under normal load the
            # kernel buffer accepts the data immediately.  If we hit the
            # timeout, the write raises ``socket.timeout`` and we drop
            # the connection (the accept loop will catch the next
            # reconnect).  We restore the PREVIOUS timeout afterwards
            # rather than forcing blocking mode: the auth read set a
            # deadline (PR-3-FIX-1) and we must not clobber it to
            # ``None`` (blocking), or the dispatch-loop ``readline`` would
            # block forever and the connection could never be reaped/
            #
            # TY-24 PERF NOTE: the per-write ``gettimeout`` / ``settimeout``
            # dance below is 4 syscalls per write × 15-50 writes/sec =
            # 60-200 syscalls/sec on the waveform-bubble push path. This
            # is correctness-related (NEW-CONC-003 — a stalled renderer
            # must NOT block the worker thread indefinitely) and was
            # intentionally LEFT UNCHANGED in the TY-24 perf pass. The
            # alternative (set ``_TCP_WRITE_TIMEOUT_SECONDS`` once in
            # ``_handle_tcp_connection`` after auth) would clobber the
            # auth-read deadline set by PR-3-FIX-1, breaking the
            # connection-reaping contract. A future pass could use
            # ``select.select([conn], [], [], _TCP_WRITE_TIMEOUT_SECONDS)``
            # before each write to achieve the same timeout semantics
            # without the per-write ``settimeout`` syscalls — but that
            # refactor is deferred (it requires careful audit of the
            # ``gettimeout``/``settimeout`` interactions with the auth
            # read path and is out of scope for TY-24).
            # closed on cleanup (SEC-018 auth-timeout/close path).
            #
            # Write-serialization: the entire settimeout → write →
            # flush → drain → restore-timeout block runs under
            # ``self._tcp_write_lock``. ``socket.sendall`` releases
            # the GIL between ``send()`` syscalls (when the kernel
            # send buffer is full), so two concurrent ``sendall``
            # calls on the same socket CAN interleave their bytes at
            # the kernel send-buffer level, corrupting the JSON-lines
            # protocol. The dedicated write lock (separate from
            # ``self._lock``, which guards only the snapshot phase)
            # serializes ONLY writers — a slow client blocks other
            # writers, but not other dispatchers' snapshots or the
            # read path. The 2s write timeout bounds the stall.
            # Holding the lock across settimeout/restore also
            # prevents a race where two threads clobber each other's
            # timeout (one restores ``None`` while another is
            # mid-write, blocking the writer forever).
            #
            # PERF NOTE (no behavior change): this per-write
            # ``gettimeout`` → ``settimeout`` → write → ``settimeout``
            # dance is 4 syscalls per push event (2 ``getsockopt`` /
            # ``setsockopt`` calls + 2 socket writes). At 15-50 Hz
            # waveform-bubble push rate, that's 60-200 syscalls/sec
            # just for timeout bookkeeping. The dance is
            # CORRECTNESS-related (NEW-CONC-003): we cannot leave the
            # socket in write-timeout mode because the dispatch-loop
            # ``readline`` on the same socket expects the auth deadline
            # set in PR-3-FIX-1. A proper fix would either (a) use two
            # sockets (one read, one write) with independent timeouts,
            # or (b) switch to non-blocking I/O with
            # ``select.select([conn], [], [], _TCP_WRITE_TIMEOUT_SECONDS)``
            # before each write — both are larger refactors that are
            # out of scope. Leaving the behavior unchanged and
            # documenting the overhead here so the next pass has the
            # context.
            # CR-79: track entries that were snapshotted but NOT
            # written to the client (either because they exceeded the
            # drain cap or because the drain failed mid-way). They are
            # re-merged into ``_pending_tcp`` after the write block so
            # the next reconnect's drain can pick them up — previously
            # up to 900 of 1000 pending events could be silently lost
            # per ``_send`` call when the drain cap was hit, and the
            # ENTIRE pending snapshot was lost when the first write
            # failed (dead client).
            _undrained: list[str] = []
            with self._tcp_write_lock:
                _prev_timeout = tcp_client.conn.gettimeout()
                with contextlib.suppress(OSError, AttributeError):
                    tcp_client.conn.settimeout(_TCP_WRITE_TIMEOUT_SECONDS)
                # settimeout can fail if the socket is already closed;
                # that's fine — the write below will also fail and we'll
                # drop the connection cleanly.
                try:
                    tcp_client.write(line + "\n")
                    tcp_client.flush()
                    # PERF-NEW-014 / SEC-008: drain at most the most recent
                    # K pending entries, not the whole list.  When the
                    # client was disconnected for a while, _pending_tcp
                    # could have grown to thousands of entries (16 Hz
                    # waveform bubble * minutes of disconnect).  Draining
                    # all of them on every push event was O(n) per push
                    # and blocked the audio thread.
                    _drain_cap = _TCP_PENDING_DRAIN_CAP
                    if pending:
                        # CR-79: split the snapshot into ``older`` (the
                        # entries that exceed the drain cap — these are
                        # NEVER attempted) and ``recent`` (the last
                        # ``_drain_cap`` entries that we'll try to write).
                        # If the drain fails mid-``recent``, the
                        # not-yet-written suffix is added to ``_undrained``
                        # along with ``older`` so the next reconnect can
                        # pick them up.
                        if len(pending) > _drain_cap:
                            older = list(pending[:-_drain_cap])
                            recent = list(pending[-_drain_cap:])
                        else:
                            older = []
                            recent = list(pending)
                        _drain_failed_at: int | None = None
                        for _i, p in enumerate(recent):
                            try:
                                tcp_client.write(p + "\n")
                                tcp_client.flush()
                            except Exception:
                                log.debug("[IPC] client write failed during pending drain")
                                _drain_failed_at = _i
                                break
                        if _drain_failed_at is not None:
                            # The entries at/after the failure index were
                            # never written; ``older`` was never attempted
                            # either. Re-merge both so they survive.
                            _undrained = older + recent[_drain_failed_at:]
                        elif older:
                            # Drain succeeded for ``recent``; ``older``
                            # (entries that exceeded the cap) still need
                            # to be re-merged.
                            _undrained = older
                except (TimeoutError, OSError) as exc:
                    log.debug("[IPC] client write failed: %s", exc)
                    # CR-79: the first write failed before the drain loop
                    # could run, so the ENTIRE ``pending`` snapshot is
                    # undrained. Re-merge it so the next reconnect's drain
                    # can pick it up (previously the whole snapshot was
                    # silently dropped here — up to 1000 queued push
                    # events lost per write failure).
                    if pending:
                        _undrained = list(pending)
                    # Mark the client as dead so the accept loop will pick
                    # up the next reconnect.  We do this under the lock to
                    # avoid a race with a concurrent _send that just
                    # snapshotted the (now-dead) client.
                    with self._lock:
                        if self._tcp_client is tcp_client:
                            with contextlib.suppress(Exception):
                                self._tcp_client.close()
                            self._tcp_client = None
                finally:
                    # Restore the previous timeout (NOT blocking ``None``) so
                    # the dispatch-loop ``readline`` keeps its auth deadline
                    # and the worker can exit/be reaped on cleanup.  Setting
                    # ``None`` here was the root cause of the
                    # auth-timeout/close deadlock (CR-2): a blocking socket
                    # could never time out, so the reader thread never exited
                    # and ``_TCPLineIO.close()`` deadlocked against the
                    # in-progress ``recv``.
                    with contextlib.suppress(OSError, AttributeError):
                        tcp_client.conn.settimeout(_prev_timeout)
            # CR-79: re-merge any undrained pending entries back into
            # ``_pending_tcp`` so they survive for the next reconnect's
            # drain. FIFO order is preserved: snapshot events (oldest)
            # first, then any events a concurrent thread appended
            # between our snapshot+clear and this re-acquire. The
            # 1000-entry cap from the ``tcp_mode`` branch is enforced
            # so we don't grow the buffer unboundedly.
            if _undrained:
                _pending_cap_remerge = 1000
                with self._lock:
                    self._pending_tcp = _undrained + self._pending_tcp
                    if len(self._pending_tcp) > _pending_cap_remerge:
                        _dropped = len(self._pending_tcp) - _pending_cap_remerge
                        del self._pending_tcp[:_dropped]
                log.debug(
                    "[IPC] re-merged %d undrained pending entries",
                    len(_undrained),
                )
            return

        if tcp_mode:
            # SEC-008: cap _pending_tcp to prevent unbounded
            # memory growth while the client is disconnected.
            # When the cap is hit, drop the OLDEST entries
            # (waveform bubble level events are stale by the
            # time the client reconnects; transcription-complete
            # events are also in history_db).
            _pending_cap = _TCP_PENDING_BUFFER_CAP
            with self._lock:
                # GT-48: re-merge with correct FIFO ordering. Under the
                # XV-82 snapshot gate above, ``pending`` is always None
                # in this tcp_mode branch (the snapshot only runs when
                # ``tcp_client is not None``, which short-circuits to
                # the earlier write-and-drain path). The re-merge is kept
                # DEFENSIVELY — if a future change re-introduces an
                # unconditional snapshot, the FIFO order is preserved:
                # snapshot events (oldest) first, then any events a
                # concurrent thread appended between our snapshot+clear
                # and this re-acquire, then the new line (newest). The
                # previous buggy sequence (extend-then-append) placed OLD
                # snapshot events AFTER the concurrent thread's NEW event
                # — violating FIFO publish order.
                if pending:
                    self._pending_tcp = pending + self._pending_tcp + [line]
                else:
                    self._pending_tcp.append(line)
                if len(self._pending_tcp) > _pending_cap:
                    dropped = len(self._pending_tcp) - _pending_cap
                    del self._pending_tcp[:dropped]
                    cap_dropped = dropped
                else:
                    cap_dropped = 0
            if cap_dropped:
                log.warning(
                    "[IPC] _pending_tcp cap exceeded; dropped %d old entries",
                    cap_dropped,
                )
            return

        # No IPC client connected.  Two scenarios:
        #
        # 1. Console mode: the user ran ``voice-typer`` (or
        #    ``python -m voice_typer.server.ipc_server`` without
        #    ``--port``) for diagnosis.  Previously push events
        #    were silently dropped here at DEBUG level, making
        #    the console session useless for observing state
        #    changes / errors / background-audio events.
        #    NEW-IPC-008: surface these at INFO level so the
        #    user can actually see what the app is doing.
        #
        # 2. Brief disconnect during normal Electron use: the
        #    client is reconnecting.  INFO-level logging here
        #    is mildly noisy but bounded — the rate of push
        #    events is dominated by waveform bubbles which are
        #    already capped by the audio callback.  Acceptable
        #    trade-off vs. diagnostic value.
        msg_type = msg.get("type", "unknown")
        # Waveform bubble level events are very high frequency
        # (15-50 Hz).  Keep them at DEBUG to avoid flooding the
        # log; everything else goes to INFO so the user can
        # see state changes, errors, etc.
        if msg_type in ("bubble_level", "waveform"):
            # G4-M-28: bubble_level / waveform are emitted at 15-50 Hz
            # by the audio worker; even DEBUG-level flooding here can
            # saturate a slow disk's log buffer. Rate-limit to every
            # 100th occurrence (matches the ipc-no-client-drop INFO
            # gate below) so a sustained no-client condition during
            # recording doesn't drown the log.
            log_rate_limited(
                log,
                logging.DEBUG,
                "[IPC] no client; dropping high-freq %s event",
                msg_type,
                key="ipc-no-client-drop-high-freq",
                every_n=100,
            )
        else:
            # CR-8: never log the message body — push events include
            # transcription text (``transcription_partial`` /
            # ``transcription_final``) which is user PII.  Log only the
            # type and a size hint so the operator can see drop rate
            # without leaking dictated content to the log file.
            #
            # G4-M-28: a disconnected Electron client during a
            # transcription (mic still recording, hotkeys still firing)
            # produces a steady stream of push events. The previous
            # unconditional ``log.info`` per drop could emit thousands
            # of lines per minute — saturating the rotating log handler
            # and obscuring genuine errors. Rate-limit to the 1st and
            # every 100th occurrence; suppressed occurrences go to
            # DEBUG with a "(suppressed occurrence N)" suffix so they
            # remain visible when debug-level logging is enabled.
            log_rate_limited(
                log,
                logging.INFO,
                "[IPC] no client; dropping %s event (size=%d)",
                msg_type,
                len(str(msg)),
                key="ipc-no-client-drop",
                every_n=100,
            )


__all__ = [
    "OutputMixin",
    "_SHUTDOWN_ALLOWLIST",
    "_TCP_PENDING_DRAIN_CAP",
    "_TCP_PENDING_BUFFER_CAP",
]
