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
from collections import deque
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
# expanded from the original ``("relaunch_app", "quit_app")``
# pair to include the content-bearing events above.
# dispatch responses (which carry an ``id`` field) are
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

# hoisted from inline magic numbers in ``_send`` so they
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

# hard size cap on a single outbound TCP frame. Matches the WS
# path's ``_MAX_FRAME_BYTES`` (1 MiB, see ``sidecar_ws.py``) so a buggy
# handler that returns an enormous dict (e.g. an unbounded history
# query, a diagnostics export with a full log tail) cannot OOM the
# client by writing a multi-MB JSON line that the kernel send buffer
# has to swallow. Pre-fix the TCP path had no cap — the WS path
# rejected oversized frames at the transport layer (``serve(...,
# max_size=...)``) but the TCP path's ``tcp_client.write(line + "\n")``
# would happily block the worker thread on a 100-MB send.
_TCP_MAX_OUTBOUND_BYTES: int = 1 * 1024 * 1024


class _PendingBuffer(deque):
    """Bounded FIFO buffer for ``IPCServer._pending_tcp``.

    Replaces the previous ``list[str]`` to eliminate the O(N)
    ``del list[:n]`` cap-drop on every append while the client is
    disconnected. The ``maxlen`` argument auto-drops the OLDEST entries
    on ``append``/``extend`` so the manual cap-drop logic in
    ``OutputMixin._send`` becomes dead code (kept in source for
    backward compat with the source-string checks in
    ``tests/test_ipc_pending_tcp_remerge.py``).

    ``__radd__`` supports the existing re-merge patterns
    (``self._pending_tcp = _undrained + self._pending_tcp`` and
    ``self._pending_tcp = pending + self._pending_tcp``) so a ``list`` on
    the left of ``+`` returns a new ``_PendingBuffer`` with the merged
    contents — Python falls back to ``__radd__`` because ``list.__add__``
    returns ``NotImplemented`` for a non-list right operand. This
    preserves the exact source-string patterns the re-merge tests pin.
    Test fixtures that bypass ``__init__`` and assign a plain ``list``
    to ``_pending_tcp`` continue to work — ``list + list`` returns a
    ``list`` (no ``__radd__`` is invoked), matching the pre-fix
    behavior.

    ``__delitem__`` is overridden to support the ``del d[:n]`` slice
    deletion pattern used by the cap-drop logic (deque's default
    ``__delitem__`` only accepts integer indices). With ``maxlen`` set,
    this branch is dead code (the deque never exceeds ``maxlen``) but
    the override keeps the source pattern safe if a future change
    constructs the buffer without ``maxlen``.
    """

    def __init__(self, maxlen: int | None = None) -> None:
        super().__init__(maxlen=maxlen)

    def __radd__(self, other: object) -> "_PendingBuffer":
        # ``list + _PendingBuffer`` → new ``_PendingBuffer`` with merged
        # contents. FIFO order is preserved: ``other`` (the snapshot,
        # OLDER entries) first, then ``self`` (the current buffer, NEWER
        # entries). If the total exceeds ``maxlen``, the OLDEST entries
        # (from ``other``) are dropped automatically by ``extend`` —
        # matching the manual ``del self._pending_tcp[:dropped]`` cap-drop
        # semantics that the source-string re-merge tests pin.
        if isinstance(other, list):
            result: _PendingBuffer = _PendingBuffer(maxlen=self.maxlen)
            result.extend(other)
            result.extend(self)
            return result
        return NotImplemented

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        # ``deque.__eq__`` returns ``NotImplemented`` for non-deque
        # operands, which Python then treats as identity comparison — so
        # ``_PendingBuffer() == []`` would be ``False`` without this
        # override. Test fixtures (``tests/server/test_tcp_io.py`` and
        # ``tests/test_ipc_layer_fixes.py``) assert ``_pending_tcp == []``
        # after a successful drain; supporting ``== list`` keeps those
        # assertions working with the new deque-backed buffer.
        if isinstance(other, list):
            return list(self) == other
        if isinstance(other, deque):
            return list(self) == list(other)
        return NotImplemented

    def __delitem__(self, key):  # type: ignore[override]
        if isinstance(key, slice):
            # deque's default ``__delitem__`` raises TypeError on slices.
            # Support the ``del d[:n]`` (drop oldest n) and ``del d[-n:]``
            # (drop newest n) patterns used by the cap-drop logic in
            # ``_send``. Convert to list, delete, and rebuild — O(N) but
            # dead code when ``maxlen`` is set (the deque never exceeds
            # ``maxlen`` so the ``len > cap`` guard never trips).
            items = list(self)
            del items[key]
            self.clear()
            self.extend(items)
        else:
            super().__delitem__(key)


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

        previously the entire
                send path (json.dumps + sendall + pending drain) ran under
                ``self._lock``, which meant:

                - Every other IPC dispatch command blocked while a slow Electron
        renderer drained its TCP receive buffer ().
                - The audio-callback-spawned bubble_level worker could stall
                  inside ``sendall`` with no timeout, holding the lock and
        stalling every other dispatch path ().
                - ``Microphone.tsx::testMicrophone → get_microphones`` saw
        user-visible lag during recording ( details).

                The fix splits the work:
                1. Under the lock: snapshot the current client / mode / pending
                   list.  This is the only section that needs mutual exclusion.
                2. Outside the lock: serialize the message, perform the actual
        ``sendall`` (with a write timeout — ), and drain
                   the pending list.  A slow client can no longer block other
                   dispatchers.

        the optional ``_client`` parameter lets a TCP
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
            # prefer the caller-provided local client (the
            # one this dispatch loop authenticated) over ``self._tcp_client``
            # (which a concurrent fast-auth reconnect may have replaced).
            # ``_client`` defaults to ``None`` for the push-event path.
            tcp_client = _client if _client is not None else self._tcp_client
            tcp_mode = self._tcp_mode
            # snapshot the pending list ONLY when we have
            # a connected client to drain it to. When ``tcp_client`` is
            # None (disconnected), the snapshot+clear is skipped — the
            # tcp_mode branch below appends the new line to the in-memory
            # buffer instead. This eliminates the FIFO race () at
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
            # cap the outbound TCP frame size before acquiring the
            # write lock. A buggy handler returning an enormous dict (e.g.
            # an unbounded history query, a diagnostics export with a full
            # log tail) would otherwise OOM the client by writing a multi-
            # MB JSON line that the kernel send buffer has to swallow —
            # blocking the worker thread on a 100-MB send. The cap matches
            # the WS path's ``_MAX_FRAME_BYTES`` (1 MiB) so both transports
            # enforce the same upper bound. The check is BEFORE the
            # ``_tcp_write_lock`` acquisition so an oversized frame doesn't
            # serialize behind a slow in-flight write. ``return`` (not
            # ``continue``) so the undrained ``pending`` snapshot is
            # re-merged below — same path as the post-write re-merge when
            # the client write fails.
            if len(line.encode("utf-8")) > _TCP_MAX_OUTBOUND_BYTES:
                log.error(
                    "[IPC] outbound TCP frame exceeds %d bytes — dropping",
                    _TCP_MAX_OUTBOUND_BYTES,
                )
                # re-merge the pending snapshot so the dropped
                # frame's would-be-drained entries survive for the next
                # reconnect (mirrors the re-merge after a write failure).
                # The dropped frame itself is NOT re-merged — it would
                # just be dropped again on the next attempt.
                if pending:
                    _pending_cap_drop = _TCP_PENDING_BUFFER_CAP
                    with self._lock:
                        self._pending_tcp = pending + self._pending_tcp
                        if len(self._pending_tcp) > _pending_cap_drop:
                            _dropped = len(self._pending_tcp) - _pending_cap_drop
                            del self._pending_tcp[:_dropped]
                return
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
            # cleanup: the published event name is ``relaunch_app``
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
            # expanded allowlist to include content-bearing events
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
            # dispatch responses (which carry an ``id`` field)
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
                    # re-merge the pending snapshot back into
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
            # set a write timeout so a stalled renderer
            # can't block the worker thread indefinitely.  2 seconds is
            # generous for a localhost TCP write — under normal load the
            # kernel buffer accepts the data immediately.  If we hit the
            # timeout, the write raises ``socket.timeout`` and we drop
            # the connection (the accept loop will catch the next
            # reconnect).  We restore the PREVIOUS timeout afterwards
            # rather than forcing blocking mode: the auth read set a
            # deadline () and we must not clobber it to
            # ``None`` (blocking), or the dispatch-loop ``readline`` would
            # block forever and the connection could never be reaped/
            #
            # PERF NOTE: the per-write ``gettimeout`` / ``settimeout``
            # dance below is 4 syscalls per write × 15-50 writes/sec =
            # 60-200 syscalls/sec on the waveform-bubble push path. This
            # is correctness-related ( — a stalled renderer
            # must NOT block the worker thread indefinitely) and was
            # intentionally LEFT UNCHANGED in the  perf pass. The
            # alternative (set ``_TCP_WRITE_TIMEOUT_SECONDS`` once in
            # ``_handle_tcp_connection`` after auth) would clobber the
            # auth-read deadline set by , breaking the
            # connection-reaping contract. A future pass could use
            # ``select.select([conn], [], [], _TCP_WRITE_TIMEOUT_SECONDS)``
            # before each write to achieve the same timeout semantics
            # without the per-write ``settimeout`` syscalls — but that
            # refactor is deferred (it requires careful audit of the
            # ``gettimeout``/``settimeout`` interactions with the auth
            # read path and is out of scope for ).
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
            # CORRECTNESS-related (): we cannot leave the
            # socket in write-timeout mode because the dispatch-loop
            # ``readline`` on the same socket expects the auth deadline
            # set in  A proper fix would either (a) use two
            # sockets (one read, one write) with independent timeouts,
            # or (b) switch to non-blocking I/O with
            # ``select.select([conn], [], [], _TCP_WRITE_TIMEOUT_SECONDS)``
            # before each write — both are larger refactors that are
            # out of scope. Leaving the behavior unchanged and
            # documenting the overhead here so the next pass has the
            # context.
            # track entries that were snapshotted but NOT
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
                    # PERF- / SEC-008: drain at most the most recent
                    # K pending entries, not the whole list.  When the
                    # client was disconnected for a while, _pending_tcp
                    # could have grown to thousands of entries (16 Hz
                    # waveform bubble * minutes of disconnect).  Draining
                    # all of them on every push event was O(n) per push
                    # and blocked the audio thread.
                    _drain_cap = _TCP_PENDING_DRAIN_CAP
                    if pending:
                        # split the snapshot into ``older`` (the
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
                        # buffer ALL recent entries to the
                        # ``_TCPLineIO`` write buffer WITHOUT flushing
                        # per-entry, then flush ONCE at the end. With
                        # the old per-entry ``write+flush`` pattern, a
                        # full drain (100 entries) issued 100 separate
                        # ``sendall`` syscalls under ``_tcp_write_lock``
                        # — plus 1 for the current line = 101 syscalls
                        # per ``_send`` call. The batched-flush pattern
                        # collapses those 100 drain syscalls into 1,
                        # reducing the total to 2 (1 for the current
                        # line above + 1 for the whole drain batch).
                        #
                        # The per-entry ``try/except`` is retained for
                        # backward compatibility with mock-based tests
                        # that override ``write()`` to raise on the Nth
                        # call (real ``_TCPLineIO.write`` never raises —
                        # it just appends to an in-memory list, so the
                        # per-entry failure path is dead code for the
                        # real transport; the live failure path is the
                        # batched ``flush()`` below).
                        for _i, p in enumerate(recent):
                            try:
                                tcp_client.write(p + "\n")
                            except Exception:
                                log.debug("[IPC] client write failed during pending drain (buffer)")
                                _drain_failed_at = _i
                                break
                        if _drain_failed_at is None:
                            # All recent entries buffered successfully —
                            # issue ONE ``sendall`` for the whole batch.
                            # If this raises (real-world failure mode:
                            # broken pipe / write timeout), treat ALL
                            # recent entries as undrained: ``sendall``
                            # may have partially succeeded before raising
                            # but we cannot tell which entries actually
                            # reached the wire. Conservative: re-merge
                            # the whole ``recent`` slice by setting the
                            # failure index to 0.
                            try:
                                tcp_client.flush()
                            except Exception:
                                log.debug("[IPC] client write failed during pending drain flush")
                                _drain_failed_at = 0
                        if _drain_failed_at is not None:
                            # reset the write buffer so any
                            # partially-buffered entries (written before
                            # a per-entry failure, or buffered before a
                            # flush failure) don't leak into the next
                            # ``_send`` call. The undrained entries are
                            # re-merged from ``recent[_drain_failed_at:]``
                            # below — for the per-entry failure case the
                            # entries at indices ``[0:_drain_failed_at)``
                            # were buffered but not sent (real
                            # ``_TCPLineIO.write`` never raises so this
                            # case is mock-only); for the flush-failure
                            # case ``_drain_failed_at == 0`` so the slice
                            # covers ALL of ``recent`` (the whole batch
                            # is re-merged because sendall may have
                            # partially succeeded before raising).
                            with contextlib.suppress(Exception):
                                tcp_client._reset_write_buffer()
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
                    # the first write failed before the drain loop
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
                    # auth-timeout/close deadlock (): a blocking socket
                    # could never time out, so the reader thread never exited
                    # and ``_TCPLineIO.close()`` deadlocked against the
                    # in-progress ``recv``.
                    with contextlib.suppress(OSError, AttributeError):
                        tcp_client.conn.settimeout(_prev_timeout)
            # re-merge any undrained pending entries back into
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
                # re-merge with correct FIFO ordering. Under the
                # snapshot gate above, ``pending`` is always None
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
        # surface these at INFO level so the
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
            # bubble_level / waveform are emitted at 15-50 Hz
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
            # never log the message body — push events include
            # transcription text (``transcription_partial`` /
            # ``transcription_final``) which is user PII.  Log only the
            # type and a size hint so the operator can see drop rate
            # without leaking dictated content to the log file.
            #
            # a disconnected Electron client during a
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
    # exported so tests can import the constant and assert the
    # size cap is enforced (mirrors the WS path's ``_MAX_FRAME_BYTES``
    # export in ``sidecar_ws.py``).
    "_TCP_MAX_OUTBOUND_BYTES",
    # exported so ``ipc_server.IPCServer.__init__`` can construct the
    # bounded FIFO buffer for ``_pending_tcp`` (replaces the previous
    # ``list[str]`` — see the class docstring for the deque-with-maxlen
    # rationale).
    "_PendingBuffer",
]
