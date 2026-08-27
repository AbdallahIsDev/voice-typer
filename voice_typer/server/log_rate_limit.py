"""Rate-limited logging helper.

A single reusable function :func:`log_rate_limited` that wraps a
``logging.Logger`` and emits a record at the configured level only on the
**1st** occurrence of a given message and every **Nth** occurrence
thereafter.  All other occurrences are logged at ``DEBUG`` (without
``exc_info``) so they remain visible when debug-level logging is enabled
but do not spam the default log output.

Motivation
----------
Several hot paths in the server (notably the audio worker thread, which
runs at ~16 Hz) can hit a persistent error condition that would
otherwise flood the log at ``ERROR`` level — roughly 960 lines per
minute.  Using ``log.exception()`` for those paths captures valuable
diagnostic stack traces on the *first* occurrence but produces an
unreadable wall of noise if the error persists.  This helper preserves
the diagnostics (1st + every Nth keeps the stack trace) while keeping
the log readable.

Design
------
- **Option B** from the B-5 task brief: a standalone function with a
  module-level counter dict.  Chosen over a class wrapper (Option A)
  because there is no useful per-instance state to manage and the call
  sites can stay as a single function call.  Chosen over a context
  manager / decorator (Option C) because the rate-limit decision happens
  *after* the wrapped code raises — i.e. in the ``except`` branch — so
  the call form is more natural.
- Counters are keyed by ``(logger.name, key_or_msg)`` so distinct
  messages (or explicit ``key`` overrides) get independent counters.
- All counter access is guarded by a single module-level
  :class:`threading.Lock`; the lock is held only for the dict read +
  write, not for the actual log call (which can do expensive I/O).
- :func:`reset` mirrors :func:`voice_typer.server.log.reset` so tests
  can clear counters between cases.

Thread safety
-------------
The audio worker thread, the audio callback thread, the IPC thread and
the main thread can all hit the same error path.  The lock guarantees
that the counter is incremented atomically; the worst case under
contention is that two threads observe the same ``count`` value and both
log at the configured level.  That is acceptable for a logging utility
(a duplicate ``ERROR`` line is far less harmful than a missed one).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

__all__ = ["log_rate_limited", "reset"]


# ── Module-level state ────────────────────────────────────────────────
# A single dict + lock is simpler and faster than per-logger state.  The
# dict is bounded in practice because the keys are (logger_name, msg)
# pairs and there are only a handful of distinct rate-limited call sites;
# if a future caller uses an unbounded set of dynamic messages it should
# pass an explicit ``key=`` to bucket them.

_RATE_LIMIT_LOCK = threading.Lock()
"""Guards all access to :data:`_RATE_LIMIT_COUNTS` and the summary-state
 dicts below."""

_MAX_COUNTERS = 1024
"""GT-B1-12: hard cap on the number of distinct rate-limit counters.

The keys are ``(logger.name, key_or_msg)`` pairs.  In practice, a
handful of distinct rate-limited call sites means the dict stays
small; if a future caller uses an unbounded set of dynamic messages
without passing an explicit ``key=``, the dict would grow without
bound -- potentially exhausting memory in a long-running server.
This cap with LRU eviction bounds the worst case; the eviction
WARNING (see :func:`log_rate_limited`) surfaces caller misuse so the
bug gets noticed.
"""

_SUMMARY_INTERVAL_SECONDS = 60.0
"""Wall-clock seconds between INFO-level suppression summaries."""

_RATE_LIMIT_COUNTS: OrderedDict[tuple[str, str], int] = OrderedDict()
"""Map of ``(logger.name, key)`` -> number of times the path has fired.

Implemented as :class:`collections.OrderedDict` so :meth:`move_to_end`
gives O(1) LRU semantics without a separate access-ordered structure
(GT-B1-12).  Never read or written without holding
:data:`_RATE_LIMIT_LOCK`.
"""

_RATE_LIMIT_NEXT_SUMMARY_DEADLINE: dict[tuple[str, str], float] = {}
"""Per-key ``time.monotonic()`` deadline for the next INFO summary.

A key is inserted into this dict on the *first* suppressed occurrence
(so the first 60-second window starts ticking from the second call,
not from process boot -- otherwise a single suppressed occurrence at
process start + 61s of silence would emit an empty summary).  Never
read or written without holding :data:`_RATE_LIMIT_LOCK`.

The cadence is *deadline-based*, not "first call after threshold".
The deadline is computed as ``seed_time + 60s`` and on each fire it is
advanced by ``60s`` (NOT reset to ``now + 60s``).  This means the
deadline is anchored to the original seed time and advances on a fixed
60s grid regardless of when fires actually happen, so a fire at
``t=61`` produces a next deadline of ``t=120`` (not ``t=121``).  This
keeps the cadence stable across slow callers and avoids drift.
"""

_RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY: dict[tuple[str, str], int] = {}
"""Per-key count of suppressed occurrences since the last summary.

Reset to 0 whenever an INFO summary is emitted for the key.  Never
read or written without holding :data:`_RATE_LIMIT_LOCK`.
"""

_log = logging.getLogger(__name__)
"""Module logger used for the INFO summary and the eviction WARNING.

These meta-logs are emitted through the *module* logger
(``voice_typer.server.log_rate_limit``) -- NOT through the caller's
``logger`` argument -- so they are always visible at the file handler's
INFO level regardless of the caller's logger level (a caller may have
raised their level via ``VOICE_TYPER_LOG_LEVEL_MODULES``; the summary
should still surface to the operator).
"""


def reset() -> None:
    """Clear all rate-limit counters -- called by tests for isolation."""
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_COUNTS.clear()
        _RATE_LIMIT_NEXT_SUMMARY_DEADLINE.clear()
        _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY.clear()


def log_rate_limited(
    logger: logging.Logger,
    level: int,
    msg: str,
    *args: Any,
    every_n: int = 100,
    exc_info: bool = False,
    key: str | None = None,
    **kwargs: Any,
) -> None:
    """Log *msg* at *level* on the 1st and every *every_n*-th occurrence.

    All other occurrences are logged at ``DEBUG`` (without ``exc_info``)
    so suppressed occurrences remain visible when debug-level logging is
    enabled but do not spam the default log output.

    Parameters
    ----------
    logger:
        The :class:`logging.Logger` to emit through.
    level:
        The configured level for the 1st + every Nth occurrence (e.g.
        ``logging.ERROR``).
    msg, *args:
        The log message and optional %-format positional arguments, as
        accepted by :meth:`logging.Logger.log`.
    every_n:
        Log at *level* on the 1st occurrence and every *every_n*-th
        occurrence thereafter.  Default ``100``.  ``every_n == 1``
        disables rate-limiting (every call logs at *level*).
        ``every_n <= 0`` means "only the 1st logs at *level*" (all
        subsequent calls go to DEBUG).
    exc_info:
        Forwarded to :meth:`logging.Logger.log` for the *level* calls
        only.  The DEBUG calls never include ``exc_info`` (capturing and
        formatting a traceback on every occurrence would re-introduce
        the cost this helper exists to avoid).
    key:
        Optional explicit counter key.  Defaults to *msg* — meaning two
        call sites with the same *msg* share a counter.  Pass an
        explicit ``key`` when the message text is dynamic (e.g. contains
        interpolated values) so each logical error class gets its own
        counter.  The full key is ``(logger.name, key or msg)``.
    **kwargs:
        Extra keyword arguments forwarded to
        :meth:`logging.Logger.log` for the *level* calls only.

    Examples
    --------
    Replacing ``log.exception()`` in a hot path::

        from voice_typer.server.log_rate_limit import log_rate_limited

        try:
            do_thing()
        except Exception:
            log_rate_limited(
                log,
                logging.ERROR,
                "[RECORDING] Audio worker thread error processing chunk",
                exc_info=True,
            )

    The first call logs at ERROR with the full traceback; calls 2-99
    log at DEBUG with a ``(suppressed occurrence N)`` suffix; call 100
    logs at ERROR with the traceback again; and so on.
    """
    counter_key = (logger.name, key or msg)
    with _RATE_LIMIT_LOCK:
        count = _RATE_LIMIT_COUNTS.get(counter_key, 0) + 1
        _RATE_LIMIT_COUNTS[counter_key] = count
        # mark this key as most-recently-used so the LRU
        # eviction policy evicts the LEAST-recently-used key when the
        # dict hits the cap.  ``OrderedDict.__setitem__`` does NOT move
        # an existing key to the end automatically, so this explicit
        # call is what makes the LRU semantics work.
        _RATE_LIMIT_COUNTS.move_to_end(counter_key)
        # cap the dict size.  Eviction signals caller misuse
        # (dynamic messages without an explicit ``key=``); we count
        # the evictions here and log a WARNING after releasing the
        # lock so the I/O doesn't block other callers.
        # the two  summary dicts are keyed by the same
        # ``counter_key`` tuple — prune their entries for the evicted
        # key here too, otherwise a caller that drives >1024 distinct
        # dynamic messages would leak summary state forever (the
        # summary dicts were never bounded).  ``popitem(last=False)``
        # returns the (key, value) pair so we can clean up the
        # correlated dicts in O(1) per eviction.
        evicted_count = 0
        while len(_RATE_LIMIT_COUNTS) > _MAX_COUNTERS:
            evicted_key, _ = _RATE_LIMIT_COUNTS.popitem(last=False)
            _RATE_LIMIT_NEXT_SUMMARY_DEADLINE.pop(evicted_key, None)
            _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY.pop(evicted_key, None)
            evicted_count += 1

    if evicted_count:
        _log.warning(
            "[rate-limit] counter dict exceeded %d entries; evicted %d "
            "LRU keys -- caller should pass an explicit key= to bucket "
            "dynamic messages",
            _MAX_COUNTERS,
            evicted_count,
        )

    # ``every_n <= 0`` means "never log on the Nth" (only the 1st logs
    # at the configured level).  ``every_n == 1`` means every call is an
    # Nth, so every call logs at *level* (no rate-limiting).  The
    # ``every_n >= 1`` guard also short-circuits the modulo, avoiding a
    # ZeroDivisionError when ``every_n == 0``.
    should_log_at_level = count == 1 or (every_n >= 1 and count % every_n == 0)
    if should_log_at_level:
        logger.log(level, msg, *args, exc_info=exc_info, **kwargs)
        return

    # Suppressed occurrence: log at DEBUG without exc_info.  :
    # the previous implementation did ``rendered = msg % args`` eagerly
    # before the ``logger.debug`` call — defeating the lazy-formatting
    # guarantee that ``logging`` provides (the framework only renders
    # the format string when the level is enabled).  On hot paths where
    # DEBUG is disabled (the default), the eager ``msg % args`` was
    # pure waste — at high suppression counts (audio worker at ~16 Hz,
    # ~960/min) it showed up as measurable CPU.  We now build a single
    # format string and pass ``*args, count`` as positional %-format
    # args.  The logging framework defers the actual ``%`` substitution
    # until it has confirmed DEBUG is enabled, so the cost is zero when
    # DEBUG is off.
    #
    # Two branches preserve the pre-fix behaviour for callers that pass
    # a literal ``%`` in *msg* without any *args* (e.g. ``"100% done"``):
    # the no-args path uses ``"%s (suppressed occurrence %d)"`` with
    # *msg* as a literal ``%s`` substitution, so a literal ``%`` in
    # *msg* is NOT re-interpreted as a format spec.  The with-args
    # path concatenates *msg* with the suffix and relies on *msg*
    # already being a valid format string (the pre-fix ``msg % args``
    # required the same).
    if args:
        logger.debug(msg + " (suppressed occurrence %d)", *args, count)
    else:
        logger.debug("%s (suppressed occurrence %d)", msg, count)

    # periodic INFO summary so chronic suppressed-occurrence
    # conditions surface at INFO level (the file-handler default) — not
    # just at DEBUG (which is only visible when VOICE_TYPER_DEBUG=1).
    # Tracked per ``counter_key`` so each error class gets its own
    # summary cadence.  The first suppressed occurrence seeds the
    # deadline (``_RATE_LIMIT_NEXT_SUMMARY_DEADLINE`` is set to
    # ``now + 60s``); once ``now >= next_summary_deadline`` AND at
    # least one occurrence has fired since the last summary, emit an
    # INFO line through the module logger and reset the per-key delta.
    #
    # the deadline advances by ``_SUMMARY_INTERVAL_SECONDS`` from
    # the PREVIOUS deadline on each fire (NOT reset to ``now + 60s``).
    # This anchors the cadence to a fixed 60s grid rooted at the seed
    # time, so a fire at ``t=61`` (deadline was 60) advances the next
    # deadline to ``t=120`` (not ``t=121``).  Without this, a slow
    # caller that only checks in once per minute would have its deadline
    # drift forward by up to a minute per fire, eventually skipping
    # windows entirely.
    now = time.monotonic()
    summary_delta = 0
    summary_key: str | None = None
    with _RATE_LIMIT_LOCK:
        delta = _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY.get(counter_key, 0) + 1
        _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY[counter_key] = delta
        next_deadline = _RATE_LIMIT_NEXT_SUMMARY_DEADLINE.get(counter_key)
        if next_deadline is None:
            # Seed the timer on the first suppressed occurrence so the
            # first 60-second window starts ticking from now.
            _RATE_LIMIT_NEXT_SUMMARY_DEADLINE[counter_key] = now + _SUMMARY_INTERVAL_SECONDS
        elif now >= next_deadline and delta > 0:
            summary_delta = delta
            summary_key = counter_key[1]
            # Advance the deadline by 60s from the PREVIOUS deadline
            # (NOT ``now + 60s``) so the cadence stays anchored to the
            # original seed-time grid and doesn't drift.
            _RATE_LIMIT_NEXT_SUMMARY_DEADLINE[counter_key] = next_deadline + _SUMMARY_INTERVAL_SECONDS
            _RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY[counter_key] = 0

    if summary_key is not None:
        # Log outside the lock to avoid holding it during I/O.  Route
        # through the module logger so the summary is always visible
        # regardless of the caller's logger level.  Use %s (not %r) so
        # the summary_key is not repr()'d into inner quotes -- makes the
        # line grep-friendly.
        #
        # the summary severity tracks the caller's configured
        # ``level`` (clamped to >= INFO so the summary always surfaces
        # at the file handler's default level).  Pre- the summary
        # was hardcoded at INFO, so an ERROR-rate-limited path that
        # fired 1000x in 60s surfaced an INFO summary -- losing the
        # severity signal that operators' alerting rules key on
        # (``level>=ERROR``).  ``max(logging.INFO, level)`` preserves
        # the historical INFO baseline for callers that rate-limit
        # DEBUG/INFO messages while escalating the summary to the
        # caller's severity for WARNING/ERROR/CRITICAL paths.
        summary_level = max(logging.INFO, level)
        _log.log(
            summary_level,
            "[rate-limit] %d suppressed occurrences of %s in last 60s",
            summary_delta,
            summary_key,
        )
