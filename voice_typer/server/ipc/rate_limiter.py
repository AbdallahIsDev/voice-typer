# extracted from the original
"""Per-connection rate limiter (RELIABILITY-006 + ).

A crash-looping or buggy predecessor client can flood the IPC socket with
thousands of malformed messages per second, exhausting file descriptors
and starving the tray thread.  :class:`_RateLimiter` is a sliding-window
per-connection limiter: each connection gets a bounded number of
messages per window.  Over-budget messages are dropped (with an error
response) rather than dispatched.

The limits are intentionally generous, a well-behaved predecessor client
sends maybe 1-5 msg/s.

RELIABILITY-006-: ``burst`` (200) is the hard per-second cap; a
client that sends >200 messages in any 1-second window is throttled.
``sustained`` (600) is measured over a 10-second window (60 msg/s
average) so short bursts within 1s (up to 200) are NOT throttled by the
sustained limit.  Previously both used a 1s window with sustained=60 <
burst=200, making burst completely unreachable.

fix (2026-07-18): the prior  comment claimed "burst is the
hard per-second cap" but the implementation used a SINGLE deque for
both checks, with the same ``window`` (10s).  With burst=200 and
sustained=600 over the same 10s deque, the burst check (>= 200) ALWAYS
fired first, making the sustained check (>= 600) unreachable dead code.
The fix: TWO independent deques, ``_burst_timestamps`` (1-second
window) and ``_sustained_timestamps`` (10-second window), so burst
catches fast-burst attacks (201 msgs in any 1s) and sustained catches
slow-drip attacks (601 msgs in any 10s = 60.1 msg/s average, never
tripping the 200/s burst).  The two checks are now genuinely
independent, not redundant.

Heartbeat bypass (explicit security decision)
---------------------------------------------
``heartbeat`` returns ``True`` unconditionally from
:meth:`_RateLimiter.allow`, skipping both the burst and sustained
budgets.  This is deliberate and MUST NOT be removed without a
redesign that proves heartbeat liveness under flood:

* The heartbeat is the only keep-alive that prevents the Python
  backend from outliving a crashed/force-killed predecessor host (the
  ``_heartbeat_loop`` daemon calls ``app.quit()`` after
  ``_HEARTBEAT_TIMEOUT_SECONDS`` of missed heartbeats).  If a
  compromised or buggy renderer floods cheap commands at ≥200 msg/s,
  a cost-1 heartbeat share of the SAME burst budget would be starved
  during the attack window and the watchdog would kill a healthy
  backend 45s later.
* The compensating control is that the watchdog tracks only the LAST
  heartbeat timestamp, not the count.  A flood of fake heartbeats
  from an already-compromised renderer is not a new attack vector
  (the renderer already has IPC); flooding OTHER commands must not be
  able to starve the legitimate keep-alive.
* ``heartbeat`` remains listed in ``COMMAND_COSTS`` at cost 1 so
  future ``DEFAULT_COST`` changes cannot silently alter its
  characteristics if the bypass is ever redesigned.
"""

import threading
import time
from collections import deque
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.ipc_server import IPCServer

_RATE_LIMIT_WINDOW_SECONDS = 10.0
_RATE_LIMIT_BURST_WINDOW_SECONDS = 1.0
_RATE_LIMIT_BURST = 200
_RATE_LIMIT_SUSTAINED = 600  # 60 msg/s average over 10s window

# Previously the rate limiter treated every dispatched command as a
COMMAND_COSTS: dict[str, int] = {
    # Heavy I/O or subprocess (cost 10+).
    "download_model": 50,
    "import_model": 20,
    "delete_model": 50,  # was 10, model delete spawns subprocess + fs writes
    "transcribe_offline": 10,  # forwards audio to worker for ASR inference
    "check_offline_pack_update": 1,  # auto-update pack check, rare, network-bound
    "restart_app": 100,  # was 10, full process restart
    "quit_app": 100,  # was 5, full process teardown
    "resume_model_download": 10,
    "clear_history": 10,
    # Moderate (cost 20).
    "microphone_test_start": 20,  # was 5, opens a PortAudio stream
    "level_monitor_start": 20,  # was 3, opens a PortAudio stream + spawns thread
    "shutdown": 5,
    "onboarding_apply": 5,
    # Small file writes / single-row mutations (cost 10).
    "save_vocabulary": 10,  # was 2, writes vocabulary file
    "save_templates": 10,  # was 2, writes templates file
    "force_cancel_transcription": 10,  # was 2, interrupts ASR pipeline
    "delete_history": 2,
    "restore_history": 2,
    "pause_model_download": 2,
    "cancel_model_download": 2,
    # Reads / cheap ops (cost 1), explicitly listed so future DEFAULT_COST
    "check_accessibility": 1,
    "heartbeat": 1,
    "get_config": 1,
    "get_defaults": 1,
    "get_download_queue": 1,  # cheap read, pending-download queue snapshot (renderer mount hydration)
    "get_favorites": 1,
    "get_history": 1,
    "get_history_count": 1,
    "get_microphones": 1,
    "get_model_catalog": 1,
    "get_model_status": 1,
    "get_prewarm_status": 1,  # RESTORED 2026-08-14 (About-page Cache Status card. See plan §6.3)
    "get_status": 1,
    "get_templates": 1,
    "get_today_stats": 1,
    "get_correction_usage": 1,  # cheap read, per-correction usage snapshot
    "get_transcription_text": 1,
    "get_vocabulary": 1,
    "get_volume_backend_status": 1,
    "level_monitor_stop": 1,
    "microphone_test_cancel": 1,
    "microphone_test_get_level": 1,
    "microphone_test_stop": 1,
    # Chunked WAV-slice reads: each call is a CHEAP bounded disk read
    "microphone_test_read_audio": 1,
    "onboarding_check_permissions": 1,
    "onboarding_get_hotkey_presets": 1,
    "onboarding_get_microphones": 1,
    "onboarding_get_model_options": 1,
    "onboarding_is_first_run": 1,
    "onboarding_next_step": 1,
    "onboarding_prev_step": 1,
    "onboarding_reset": 1,
    "onboarding_set_backend": 1,
    "onboarding_set_hotkey": 1,
    "onboarding_set_microphone": 1,
    "onboarding_set_model": 1,
    "onboarding_skip": 1,
    "onboarding_start": 1,
    "open_prewarm_log": 1,  # RESTORED 2026-08-14 (About-page Cache Status card. See plan §6.3); launches OS editor
    "run_prewarm": 10,  # RESTORED 2026-08-14 (§6.3 addendum 2nd half); warm pass reads ~200 MB
    "relaunch_ack": 1,
    "repaste_last": 1,
    "search_history": 1,
    "set_config": 2,  # writes config file
    "set_esc_cancel_paused": 1,
    "set_tray_locale": 1,
    "test_vocabulary_correction": 3,  # compute, runs the correction engine pass on user text
    "toggle_dictation": 1,
    "toggle_favorite": 2,  # writes to db
    "tray_click": 1,
    "undo_last": 2,  # deletes last history row
    # Commands added to _COMMAND_REGISTRY after the cost map was last audited.
    "add_trusted_endpoint": 2,
    "test_cloud_connection": 10,
    "reset_macos_accessibility": 2,
    "reset_linux_permissions": 2,
}
DEFAULT_COST = 1

# write timeout for TCP sendall.  A stalled predecessor
_TCP_WRITE_TIMEOUT_SECONDS = 2.0

# If predecessor crashes or is force-killed, the Python backend keeps
_HEARTBEAT_INTERVAL_SECONDS = 15.0
_HEARTBEAT_TIMEOUT_SECONDS = 45.0  # 3 missed heartbeats @ 15s, same detection window as prior 9@5s
# grace period (seconds) the heartbeat watchdog's force-exit
_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS = 10.0


class _RateLimiter:
    """Sliding-window per-connection rate limiter.

        Each IPC connection gets its own ``_RateLimiter`` instance.  The
        limiter tracks the timestamp of each accepted message in TWO
        deques:

        * ``_burst_timestamps``: a 1-second sliding window. If the deque
          reaches ``burst`` entries (default 200), the next message is
          rejected. This catches fast-burst attacks (201+ msgs in any 1s).
        * ``_sustained_timestamps``: a ``window``-second sliding window
          (default 10s). If the deque reaches ``sustained`` entries
          (default 600 = 60 msg/s avg), the next message is rejected.
          This catches slow-drip attacks (601+ msgs in any 10s = 60.1
          msg/s avg) that never trip the per-second burst.

    fix (2026-07-18): prior to this fix, both checks shared a
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
        # TWO independent deques. The burst deque uses a 1s
        self._burst_timestamps: deque[tuple[float, int]] = deque()
        self._sustained_timestamps: deque[tuple[float, int]] = deque()
        # running totals maintained incrementally on append/popleft
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
        keep the pre- cost-1 behavior.
                now : float, optional
                    Current monotonic time.  If omitted, ``time.monotonic()``
                    is used.  Passing ``now`` explicitly makes the limiter
                    trivially testable.

                SEC-6: ``_rejected`` is incremented atomically with the
                rejection decision inside the same lock acquisition as the
                deque check. Previously ``allow()`` returned False and the
                caller separately called ``reject()`` (acquiring the lock
                again), a benign race where two threads could both observe
                the same deque state, both decide to reject, and double-count
                the rejection. Now ``allow()`` is the single source of truth
                for both the decision and the counter.

        the burst and sustained checks are now INDEPENDENT.
                A client can trip burst (201 msgs in 1s) without tripping
                sustained (601 msgs in 10s), and vice versa. Both deques are
                evicted and checked under the same lock acquisition so the
                decision is atomic.

        the cost-weighted check is
                ``current_window_total + cost > limit``: equivalent to the
        pre- ``len(deque) >= limit`` check when ``cost == 1``
                (because each entry contributes 1 to the total). With
                ``cost == 50`` (e.g. ``download_model``), the limit is
                reached after 4 calls instead of 200.

        the running totals (``_burst_total``
                ``_sustained_total``) are maintained incrementally on
                append/popleft, so this method is O(1) per call instead of
                O(N) ``sum()`` over the deque. The totals are mutated only
                under ``self._lock``, so the incremental bookkeeping stays
                consistent with the deque contents.
        """
        ts = now if now is not None else time.monotonic()
        cost = COMMAND_COSTS.get(command, DEFAULT_COST)
        # Defensive: a misconfigured COMMAND_COSTS entry or a future
        if cost < 1:
            cost = 1
        # Explicit security decision: heartbeat bypasses the burst +
        if command == "heartbeat":
            return True
        burst_cutoff = ts - self._burst_window
        sustained_cutoff = ts - self._window
        with self._lock:
            # Evict expired timestamps from both deques. : maintain
            while self._burst_timestamps and self._burst_timestamps[0][0] < burst_cutoff:
                _old_ts, _old_cost = self._burst_timestamps.popleft()
                # Clamp the running total at 0. The
                self._burst_total = max(0, self._burst_total - _old_cost)
            while self._sustained_timestamps and self._sustained_timestamps[0][0] < sustained_cutoff:
                _old_ts, _old_cost = self._sustained_timestamps.popleft()
                # See burst-total clamp above.
                self._sustained_total = max(0, self._sustained_total - _old_cost)
            # the cost-weighted check uses the running totals
            burst_total = self._burst_total
            sustained_total = self._sustained_total
            # burst check (1s window, hard per-second cap).
            if burst_total + cost > self._burst:
                self._rejected += 1
                return False
            # sustained check (10s window, avg-rate cap).
            if sustained_total + cost > self._sustained:
                self._rejected += 1
                return False
            self._burst_timestamps.append((ts, cost))
            self._sustained_timestamps.append((ts, cost))
            # increment running totals on append.
            self._burst_total += cost
            self._sustained_total += cost
            return True

    @property
    def rejected_count(self) -> int:
        """Total messages rejected since this limiter was created.

        Not currently exposed via IPC, but useful for tests.
        """
        return self._rejected


# Previously, both the TCP path (``_handle_tcp_connection``) and the WS
_RATE_LIMITER_INIT_LOCK = threading.Lock()


def _get_rate_limiter(server: "object", _cls: "type[_RateLimiter] | None" = None) -> _RateLimiter:
    """Return the per-process ``_RateLimiter`` for ``server`` ().

        Lazily creates and stores the limiter on the server instance so
        reconnects within the same process share the same sliding-window
        budget. A local attacker can no longer reset the budget by
        disconnecting and reconnecting.

    the get-or-create sequence is now atomic across threads
        thanks to ``_RATE_LIMITER_INIT_LOCK``. The lock is module-level
        (shared across all server instances), that's correct because the
        critical section is "check this specific ``server._rate_limiter_instance``
        and, if missing, create+store". Different server instances have
        different ``_rate_limiter_instance`` attributes, so the lock
        serializes only the get-or-create on the SAME server (which is
        the only race that matters); different servers can init in
        parallel without contention. The lock is held for microseconds
        at most (no I/O, no ``allow()`` call), so contention is negligible.

    ``_cls`` is an optional override for the ``_RateLimiter``
        class. ``ipc_server._get_rate_limiter`` delegates here with
        ``_cls=ipc_server._RateLimiter`` so tests that monkey-patch
    ``ipc_server._RateLimiter`` to widen the race window (
    ) still observe the patched class, the canonical
        implementation is now single-sourced in this leaf module.
    """
    if _cls is None:
        _cls = _RateLimiter
    # Fast path: limiter already exists on the server instance, return
    limiter = getattr(server, "_rate_limiter_instance", None)
    if isinstance(limiter, _cls):
        return limiter

    # Slow path: limiter is None or a non-_RateLimiter (e.g. an
    with _RATE_LIMITER_INIT_LOCK:
        limiter = getattr(server, "_rate_limiter_instance", None)
        if not isinstance(limiter, _cls):
            limiter = _cls()
            # ``IPCServer`` declares this slot; cast so the checker verifies
            # the assignment instead of silencing it.
            cast("IPCServer", server)._rate_limiter_instance = limiter
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
