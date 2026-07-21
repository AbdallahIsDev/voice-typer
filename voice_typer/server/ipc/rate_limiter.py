# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""Per-connection rate limiter (RELIABILITY-006 + CR-11).

A crash-looping or buggy Electron client can flood the IPC socket with
thousands of malformed messages per second, exhausting file descriptors
and starving the tray thread.  :class:`_RateLimiter` is a sliding-window
per-connection limiter: each connection gets a bounded number of
messages per window.  Over-budget messages are dropped (with an error
response) rather than dispatched.

The limits are intentionally generous — a well-behaved Electron client
sends maybe 1-5 msg/s.

RELIABILITY-006-FIX-10: ``burst`` (200) is the hard per-second cap; a
client that sends >200 messages in any 1-second window is throttled.
``sustained`` (600) is measured over a 10-second window (60 msg/s
average) so short bursts within 1s (up to 200) are NOT throttled by the
sustained limit.  Previously both used a 1s window with sustained=60 <
burst=200, making burst completely unreachable.

IPC-4 fix (2026-07-18): the prior FIX-10 comment claimed "burst is the
hard per-second cap" but the implementation used a SINGLE deque for
both checks, with the same ``window`` (10s).  With burst=200 and
sustained=600 over the same 10s deque, the burst check (>= 200) ALWAYS
fired first, making the sustained check (>= 600) unreachable dead code.
The fix: TWO independent deques — ``_burst_timestamps`` (1-second
window) and ``_sustained_timestamps`` (10-second window) — so burst
catches fast-burst attacks (201 msgs in any 1s) and sustained catches
slow-drip attacks (601 msgs in any 10s = 60.1 msg/s average, never
tripping the 200/s burst).  The two checks are now genuinely
independent, not redundant.
"""

import threading
import time
from collections import deque

# ── RELIABILITY-006: per-connection rate limiter ─────────────────────────
_RATE_LIMIT_WINDOW_SECONDS = 10.0
_RATE_LIMIT_BURST_WINDOW_SECONDS = 1.0
_RATE_LIMIT_BURST = 200
_RATE_LIMIT_SUSTAINED = 600  # 60 msg/s average over 10s window

# NEW-CONC-003: write timeout for TCP sendall.  A stalled Electron
# renderer (e.g. GC pause, dev-tools inspection, or a busy main thread)
# can stop draining its TCP receive buffer.  Without a timeout, sendall
# blocks indefinitely, holding the IPC lock (pre-NEW-IPC-014) or
# blocking the bubble_level worker thread (post-NEW-IPC-014).  2
# seconds is generous for a localhost write — under normal load the
# kernel buffer accepts data in microseconds.  When the timeout fires,
# we drop the client connection so the accept loop can pick up the
# next reconnect.
_TCP_WRITE_TIMEOUT_SECONDS = 2.0

# ── RW-10: Electron-alive heartbeat ─────────────────────────────────────
#
# If Electron crashes or is force-killed, the Python backend keeps
# running with the mic stream open, hotkeys registered, volume ducked,
# and the single-instance mutex held.  The next launch hits
# ``ERROR_ALREADY_EXISTS`` and surfaces "Only one instance can run",
# forcing the user to manually kill ``python.exe``.
#
# The heartbeat mechanism works as follows:
#   1. Electron connects via TCP and starts sending ``heartbeat`` IPC
#      commands every 5 seconds (see ``client/src/main/index.ts``).
#   2. The ``_handle_heartbeat`` handler updates
#      ``self._last_heartbeat_at = time.monotonic()``.
#   3. The ``_heartbeat_loop`` daemon thread wakes every 5 seconds and
#      checks if more than 120 seconds (24 missed heartbeats) have
#      elapsed since the last heartbeat.  If so, it calls
#      ``self.app.quit()`` — which runs the shared ``_do_cleanup()``
#      path from RW-3 (restores volume, flushes recovery, releases the
#      mutex, closes PortAudio).
#
# The watchdog only fires AFTER the first heartbeat has been received,
# so the backend doesn't exit prematurely during a slow Electron cold
# start (10+ seconds for the torch import + window creation).
_HEARTBEAT_INTERVAL_SECONDS = 5.0
_HEARTBEAT_TIMEOUT_SECONDS = 120.0  # 24 missed heartbeats — increased from 15s
# CR-9: grace period (seconds) the heartbeat watchdog's force-exit
# daemon thread waits before calling ``os._exit(1)``. 10s is longer
# than the slowest legitimate ``app.quit()`` path (PortAudio stream
# teardown + history DB flush + mutex release ≈ 2-3s in the worst
# observed case), giving graceful shutdown room to complete while
# still bounding the worst-case hang to 10s. Extracted as a constant
# so tests can patch it down to ~50ms to avoid waiting real seconds.
_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS = 10.0


class _RateLimiter:
    """Sliding-window per-connection rate limiter.

    Each IPC connection gets its own ``_RateLimiter`` instance.  The
    limiter tracks the timestamp of each accepted message in TWO
    deques:

    * ``_burst_timestamps`` — a 1-second sliding window. If the deque
      reaches ``burst`` entries (default 200), the next message is
      rejected. This catches fast-burst attacks (201+ msgs in any 1s).
    * ``_sustained_timestamps`` — a ``window``-second sliding window
      (default 10s). If the deque reaches ``sustained`` entries
      (default 600 = 60 msg/s avg), the next message is rejected.
      This catches slow-drip attacks (601+ msgs in any 10s = 60.1
      msg/s avg) that never trip the per-second burst.

    IPC-4 fix (2026-07-18): prior to this fix, both checks shared a
    SINGLE deque (the ``window``-second one), so the burst check
    (>= 200) always fired first and the sustained check (>= 600) was
    unreachable dead code. The two checks are now genuinely
    independent.
    """

    def __init__(
        self,
        *,
        burst: int = _RATE_LIMIT_BURST,
        sustained_per_sec: int = _RATE_LIMIT_SUSTAINED,
        window: float = _RATE_LIMIT_WINDOW_SECONDS,
        burst_window: float = _RATE_LIMIT_BURST_WINDOW_SECONDS,
    ) -> None:
        self._burst = burst
        self._sustained = sustained_per_sec
        self._window = window
        self._burst_window = burst_window
        # IPC-4: TWO independent deques. The burst deque uses a 1s
        # window (configurable via ``burst_window``); the sustained
        # deque uses the ``window`` parameter (default 10s).
        self._burst_timestamps: deque[float] = deque()
        self._sustained_timestamps: deque[float] = deque()
        self._rejected: int = 0
        self._lock = threading.Lock()

    def allow(self, *, now: float | None = None) -> bool:
        """Return True if the message should be accepted.

        Parameters
        ----------
        now : float, optional
            Current monotonic time.  If omitted, ``time.monotonic()``
            is used.  Passing ``now`` explicitly makes the limiter
            trivially testable.

        SEC-6: ``_rejected`` is incremented atomically with the
        rejection decision inside the same lock acquisition as the
        deque check. Previously ``allow()`` returned False and the
        caller separately called ``reject()`` (acquiring the lock
        again) — a benign race where two threads could both observe
        the same deque state, both decide to reject, and double-count
        the rejection. Now ``allow()`` is the single source of truth
        for both the decision and the counter.

        IPC-4: the burst and sustained checks are now INDEPENDENT.
        A client can trip burst (201 msgs in 1s) without tripping
        sustained (601 msgs in 10s), and vice versa. Both deques are
        evicted and checked under the same lock acquisition so the
        decision is atomic.
        """
        ts = now if now is not None else time.monotonic()
        burst_cutoff = ts - self._burst_window
        sustained_cutoff = ts - self._window
        with self._lock:
            # Evict expired timestamps from both deques.
            while self._burst_timestamps and self._burst_timestamps[0] < burst_cutoff:
                self._burst_timestamps.popleft()
            while self._sustained_timestamps and self._sustained_timestamps[0] < sustained_cutoff:
                self._sustained_timestamps.popleft()
            # IPC-4: burst check (1s window, hard per-second cap).
            if len(self._burst_timestamps) >= self._burst:
                self._rejected += 1
                return False
            # IPC-4: sustained check (10s window, avg-rate cap).
            # Independent of burst — a slow-drip attacker who never
            # sends >200 msgs/s but exceeds 600 msgs in 10s is caught
            # here, where the prior single-deque impl would have
            # missed them (burst fired first at 200).
            if len(self._sustained_timestamps) >= self._sustained:
                self._rejected += 1
                return False
            self._burst_timestamps.append(ts)
            self._sustained_timestamps.append(ts)
            return True

    @property
    def rejected_count(self) -> int:
        """Total messages rejected since this limiter was created.

        Not currently exposed via IPC, but useful for tests.
        """
        return self._rejected

    def reject(self) -> None:
        """No-op kept for backward compatibility.

        SEC-6: the counter is now incremented atomically inside
        :meth:`allow` when it returns ``False``. The separate
        ``reject()`` call from the caller was dropped to eliminate the
        benign race where two threads could both observe the same
        deque state, both decide to reject, and double-count the
        rejection. This method is retained (as a no-op) so existing
        callers (and the WS path's source-level string check in
        ``test_sidecar_ws_calls_rate_limiter_allow_per_frame``) don't
        have to change in lockstep.
        """
        return None


# ── CR-11: per-process rate limiter ──────────────────────────────────────
#
# Previously, both the TCP path (``_handle_tcp_connection``) and the WS
# path (``sidecar_ws._make_dispatch``) instantiated a FRESH
# ``_RateLimiter`` per connection. A local attacker could burst the
# 200-message budget, disconnect, reconnect, and burst again — bypassing
# the sustained cap entirely.
#
# The fix: ONE ``_RateLimiter`` per ``IPCServer`` instance, lazily
# created and stored on the instance via ``_get_rate_limiter(server)``.
# All connections (TCP reconnects, WS reconnects) within the same server
# process share the same sliding-window deque, so the 10s sustained
# budget continues to evict old timestamps across reconnects.
#
# Stored on the instance (not module-level) so:
#   - Production: one limiter per server process (CR-11 fix).
#   - Tests: each fresh IPCServer (or MagicMock test double) gets its
#     own limiter, preserving test isolation without needing a reset
#     hook. ``getattr(server, "_rate_limiter_instance", None)`` returns
#     None for a real IPCServer (attribute not set) and a child
#     MagicMock for a test double — the ``isinstance`` check filters
#     both, creating+storing a real ``_RateLimiter`` on first access.
#
# R4-F18 (IMPROVE-mode run, 2026-07-19): the lazy get-or-create is now
# guarded by a module-level ``threading.Lock`` so two threads
# simultaneously hitting ``_get_rate_limiter(server)`` on a fresh
# server instance cannot race past the ``isinstance`` check and each
# construct a competing ``_RateLimiter`` instance. The race window was
# tiny (a few microseconds between the ``getattr`` and the
# ``setattr``), but the consequence was severe: the orphaned limiter's
# accepted timestamps would NOT count toward the canonical budget, so
# a slow-drip attacker could effectively double the rate-limit budget
# for the brief overlap window (or worse, N× with N racing threads).
# The init lock is held only for the brief get-or-create window, NOT
# for the subsequent ``allow()`` call — the per-instance lock inside
# ``_RateLimiter.allow()`` already serializes deque mutation, so this
# outer lock does not serialize dispatch.
#
# NOTE: this leaf copy in ``voice_typer/server/ipc/rate_limiter.py`` is
# kept in sync with the canonical implementation in
# ``voice_typer/server/ipc_server.py`` (CR-14 deferred the package
# delete). The canonical implementation is the one imported by tests
# and by ``sidecar_ws.py``; this copy exists only because the
# ``ipc/`` package was not deleted in this IMPROVE-mode run.
_RATE_LIMITER_INIT_LOCK = threading.Lock()


def _get_rate_limiter(server: "object") -> _RateLimiter:
    """Return the per-process ``_RateLimiter`` for ``server`` (CR-11).

    Lazily creates and stores the limiter on the server instance so
    reconnects within the same process share the same sliding-window
    budget. A local attacker can no longer reset the budget by
    disconnecting and reconnecting.

    R4-F18: the get-or-create sequence is now atomic across threads
    thanks to ``_RATE_LIMITER_INIT_LOCK``. The lock is module-level
    (shared across all server instances) — that's correct because the
    critical section is "check this specific ``server._rate_limiter_instance``
    and, if missing, create+store". Different server instances have
    different ``_rate_limiter_instance`` attributes, so the lock
    serializes only the get-or-create on the SAME server (which is
    the only race that matters); different servers can init in
    parallel without contention. The lock is held for microseconds
    at most (no I/O, no ``allow()`` call), so contention is negligible.
    """
    # Fast path: limiter already exists on the server instance — return
    # it WITHOUT acquiring the init lock. This is the common case after
    # the first dispatch on each server; the lock is only needed for
    # the brief first-call race. The fast path is safe because
    # ``server._rate_limiter_instance`` is set atomically by the
    # ``setattr`` below (CPython's GIL makes single-attribute writes
    # atomic) and the ``_RateLimiter`` instance itself is fully
    # thread-safe (its own ``self._lock`` guards deque mutation).
    limiter = getattr(server, "_rate_limiter_instance", None)
    if isinstance(limiter, _RateLimiter):
        return limiter

    # Slow path: limiter is None or a non-_RateLimiter (e.g. an
    # auto-vivified MagicMock child). Acquire the init lock and
    # RE-CHECK — another thread may have created+stored the limiter
    # between our fast-path check and the lock acquisition (classic
    # double-checked locking pattern).
    with _RATE_LIMITER_INIT_LOCK:
        limiter = getattr(server, "_rate_limiter_instance", None)
        if not isinstance(limiter, _RateLimiter):
            limiter = _RateLimiter()
            # ``setattr`` on a MagicMock overrides the auto-vivified child
            # attribute; on a real IPCServer it just sets the attribute.
            server._rate_limiter_instance = limiter  # type: ignore[attr-defined]
        return limiter


__all__ = [
    "_RateLimiter",
    "_get_rate_limiter",
    "_RATE_LIMITER_INIT_LOCK",
    "_RATE_LIMIT_WINDOW_SECONDS",
    "_RATE_LIMIT_BURST_WINDOW_SECONDS",
    "_RATE_LIMIT_BURST",
    "_RATE_LIMIT_SUSTAINED",
    "_TCP_WRITE_TIMEOUT_SECONDS",
    "_HEARTBEAT_INTERVAL_SECONDS",
    "_HEARTBEAT_TIMEOUT_SECONDS",
    "_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
]
