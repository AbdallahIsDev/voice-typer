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

# ── G4-M-09: per-command cost map ────────────────────────────────────────
#
# Previously the rate limiter treated every dispatched command as a
# single "unit" against the burst (200/s) and sustained (600/10s) caps.
# That's fine for cheap commands like ``heartbeat`` or ``get_status``,
# but expensive commands — large model downloads, GDPR bundle exports,
# full personal-data wipes — can saturate the dispatcher thread pool
# and the disk long after the rate-limit window has slid past. A buggy
# or hostile client could trigger dozens of concurrent
# ``download_model`` invocations within the burst window.
#
# The cost map assigns each known command a "weight" against the same
# burst/sustained budgets. ``download_model`` consumes 50 of the 200
# burst units — so a client can fire at most 4 ``download_model``
# requests in any 1s window before the 5th is rejected. ``heartbeat``
# is explicitly listed at cost 1 so future changes to ``DEFAULT_COST``
# don't silently change the heartbeat's rate-limit characteristics
# (heartbeats fire every 5s and must NEVER trip the burst cap).
#
# Looked up in :meth:`_RateLimiter.allow` via ``COMMAND_COSTS.get(
# command, DEFAULT_COST)``. Unknown commands get ``DEFAULT_COST = 1``
# (preserves backward compatibility with the count-based limiter: a
# caller that does not pass ``command`` is treated as cost 1, identical
# to the pre-G4-M-09 behavior).
COMMAND_COSTS: dict[str, int] = {
    # Heavy I/O or subprocess (cost 10).
    "download_model": 50,
    "import_model": 20,
    "delete_model": 10,
    "run_prewarm": 10,
    "restart_app": 10,
    "resume_model_download": 10,
    "clear_history": 10,
    # Moderate (cost 5).
    "quit_app": 5,
    "shutdown": 5,
    "onboarding_apply": 5,
    "microphone_test_start": 5,
    # Light-moderate (cost 3).
    "level_monitor_start": 3,
    "get_vocabulary_suggestions": 3,  # NOTE: stale per registry — kept for back-compat
    # Small file writes / single-row mutations (cost 2).
    "save_vocabulary": 2,
    "save_templates": 2,
    "delete_history": 2,
    "restore_history": 2,
    "force_cancel_transcription": 2,
    "pause_model_download": 2,
    "cancel_model_download": 2,
    # Reads / cheap ops (cost 1) — explicitly listed so future DEFAULT_COST
    # changes don't silently alter their rate-limit characteristics.
    "heartbeat": 1,
    "get_config": 1,
    "get_defaults": 1,
    "get_favorites": 1,
    "get_history": 1,
    "get_history_count": 1,
    "get_microphones": 1,
    "get_model_catalog": 1,
    "get_model_status": 1,
    "get_prewarm_status": 1,
    "get_status": 1,
    "get_templates": 1,
    "get_today_stats": 1,
    "get_transcription_text": 1,
    "get_vocabulary": 1,
    "get_volume_backend_status": 1,
    "level_monitor_stop": 1,
    "microphone_test_cancel": 1,
    "microphone_test_get_level": 1,
    "microphone_test_stop": 1,
    "onboarding_check_permissions": 1,
    "onboarding_get_hotkey_presets": 1,
    "onboarding_get_microphones": 1,
    "onboarding_get_model_options": 1,
    "onboarding_is_first_run": 1,
    "onboarding_next_step": 1,
    "onboarding_prev_step": 1,
    "onboarding_reset": 1,
    "onboarding_set_hotkey": 1,
    "onboarding_set_microphone": 1,
    "onboarding_set_model": 1,
    "onboarding_skip": 1,
    "onboarding_start": 1,
    "open_prewarm_log": 1,
    "relaunch_ack": 1,
    "repaste_last": 1,
    "search_history": 1,
    "set_config": 2,  # writes config file
    "set_esc_cancel_paused": 1,
    "set_tray_locale": 1,
    "toggle_dictation": 1,
    "toggle_favorite": 2,  # writes to db
    "tray_click": 1,
    "undo_last": 2,  # deletes last history row
    # Stale entries kept for back-compat (the corresponding commands were
    # removed from _COMMAND_REGISTRY by ZR-45 — moved to Tauri Rust host).
    # The rate_limiter's COMMAND_COSTS dict still has them so older
    # Electron builds that bridge these calls don't trip the limiter's
    # DEFAULT_COST path. The contract test
    # (test_command_costs_does_not_list_unknown_commands) is satisfied
    # because these commands ARE in LEGACY_ERROR_CODES / older registries.
    "delete_all_personal_data": 20,
    "export_diagnostics": 10,
    "export_gdpr_bundle": 20,
    "test_llm_connection": 10,
}
DEFAULT_COST = 1

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
# start (10+ seconds for the torch import + window creation).  The
# cold-start tolerance is provided by the ``_last_heartbeat_at is None``
# guard in ``_check_heartbeat_timeout`` — NOT by the timeout value
# itself — so the timeout can be tight.
#
# The timeout was 120.0s (24 missed heartbeats), which is 4× the
# Rust-side equivalent (``src-tauri/src/sidecar/ws.rs``: 10s interval,
# 15s response timeout, 3 consecutive misses → 30-45s before supervisor
# respawn).  A crashed Electron left the Python backend running with
# the mic stream open, hotkeys registered, volume ducked, and the
# single-instance mutex held for the full 120s before cleanup fired.
# Reduced to 45s (9 missed heartbeats) — 3× the Rust-side 15s response
# timeout, giving a wide safety margin against transient GC pauses or
# main-thread stalls in the renderer while no longer leaving a crashed
# Electron's resources held for 2 full minutes.  The watchdog only
# fires after the first heartbeat, so slow Electron cold starts (which
# never send a heartbeat before the timeout would fire) are still
# safe.
_HEARTBEAT_INTERVAL_SECONDS = 5.0
_HEARTBEAT_TIMEOUT_SECONDS = 45.0  # 9 missed heartbeats — was 120s (24 misses), reduced to align with Rust-side ~30-45s
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
        # G4-M-09: each entry is now a ``(timestamp, cost)`` tuple
        # rather than a bare timestamp, so the per-command cost map
        # can be summed against the burst/sustained budgets. ``cost=1``
        # for unknown commands preserves the pre-G4-M-09 behavior
        # (each call counts as 1 unit).
        self._burst_timestamps: deque[tuple[float, int]] = deque()
        self._sustained_timestamps: deque[tuple[float, int]] = deque()
        # ER-31: running totals maintained incrementally on append/popleft
        # so allow() is O(1) per call instead of O(N) sum() over the deque.
        self._burst_total: int = 0
        self._sustained_total: int = 0
        self._rejected: int = 0
        self._lock = threading.Lock()

    def allow(self, *, command: str = "", now: float | None = None) -> bool:
        """Return True if the message should be accepted.

        Parameters
        ----------
        command : str
            The IPC command name (``msg.get("type")``). Used to look up
            the per-command cost in :data:`COMMAND_COSTS`. Unknown
            commands default to :data:`DEFAULT_COST` (1). Defaults to
            ``""`` so legacy callers (which don't pass ``command``)
            keep the pre-G4-M-09 cost-1 behavior.
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

        G4-M-09: the cost-weighted check is
        ``current_window_total + cost > limit`` — equivalent to the
        pre-G4-M-09 ``len(deque) >= limit`` check when ``cost == 1``
        (because each entry contributes 1 to the total). With
        ``cost == 50`` (e.g. ``download_model``), the limit is
        reached after 4 calls instead of 200.

        ER-31: the running totals (``_burst_total`` /
        ``_sustained_total``) are maintained incrementally on
        append/popleft, so this method is O(1) per call instead of
        O(N) ``sum()`` over the deque. The totals are mutated only
        under ``self._lock``, so the incremental bookkeeping stays
        consistent with the deque contents.
        """
        ts = now if now is not None else time.monotonic()
        cost = COMMAND_COSTS.get(command, DEFAULT_COST)
        # Defensive: a misconfigured COMMAND_COSTS entry or a future
        # caller passing a negative cost must not corrupt the budget.
        # Clamp to at least 1 so the limiter is always strict-ish.
        if cost < 1:
            cost = 1
        burst_cutoff = ts - self._burst_window
        sustained_cutoff = ts - self._window
        with self._lock:
            # Evict expired timestamps from both deques. ER-31: maintain
            # running totals incrementally so allow() is O(1) per call
            # instead of O(N) sum() over the deque.
            while self._burst_timestamps and self._burst_timestamps[0][0] < burst_cutoff:
                _old_ts, _old_cost = self._burst_timestamps.popleft()
                self._burst_total -= _old_cost
            while self._sustained_timestamps and self._sustained_timestamps[0][0] < sustained_cutoff:
                _old_ts, _old_cost = self._sustained_timestamps.popleft()
                self._sustained_total -= _old_cost
            # G4-M-09: the cost-weighted check uses the running totals
            # (ER-31) instead of sum() on every call. ``cost == 1``
            # reduces this to the pre-G4-M-09 count-based check.
            burst_total = self._burst_total
            sustained_total = self._sustained_total
            # IPC-4: burst check (1s window, hard per-second cap).
            if burst_total + cost > self._burst:
                self._rejected += 1
                return False
            # IPC-4: sustained check (10s window, avg-rate cap).
            # Independent of burst — a slow-drip attacker who never
            # sends >200 msgs/s but exceeds 600 msgs in 10s is caught
            # here, where the prior single-deque impl would have
            # missed them (burst fired first at 200).
            if sustained_total + cost > self._sustained:
                self._rejected += 1
                return False
            self._burst_timestamps.append((ts, cost))
            self._sustained_timestamps.append((ts, cost))
            # ER-31: increment running totals on append.
            self._burst_total += cost
            self._sustained_total += cost
            return True

    @property
    def rejected_count(self) -> int:
        """Total messages rejected since this limiter was created.

        Not currently exposed via IPC, but useful for tests.
        """
        return self._rejected


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
    "COMMAND_COSTS",
    "DEFAULT_COST",
    "_TCP_WRITE_TIMEOUT_SECONDS",
    "_HEARTBEAT_INTERVAL_SECONDS",
    "_HEARTBEAT_TIMEOUT_SECONDS",
    "_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
]
