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
from typing import Any

__all__ = ["log_rate_limited", "reset"]


# ── Module-level state ────────────────────────────────────────────────
# A single dict + lock is simpler and faster than per-logger state.  The
# dict is bounded in practice because the keys are (logger_name, msg)
# pairs and there are only a handful of distinct rate-limited call sites;
# if a future caller uses an unbounded set of dynamic messages it should
# pass an explicit ``key=`` to bucket them.

_RATE_LIMIT_LOCK = threading.Lock()
"""Guards all access to :data:`_RATE_LIMIT_COUNTS`."""

_RATE_LIMIT_COUNTS: dict[tuple[str, str], int] = {}
"""Map of ``(logger.name, key)`` → number of times the path has fired.

Never read or written without holding :data:`_RATE_LIMIT_LOCK`.
"""


def reset() -> None:
    """Clear all rate-limit counters — called by tests for isolation."""
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_COUNTS.clear()


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

    # ``every_n <= 0`` means "never log on the Nth" (only the 1st logs
    # at the configured level).  ``every_n == 1`` means every call is an
    # Nth, so every call logs at *level* (no rate-limiting).  The
    # ``every_n >= 1`` guard also short-circuits the modulo, avoiding a
    # ZeroDivisionError when ``every_n == 0``.
    should_log_at_level = count == 1 or (every_n >= 1 and count % every_n == 0)
    if should_log_at_level:
        logger.log(level, msg, *args, exc_info=exc_info, **kwargs)
        return

    # Suppressed occurrence: log at DEBUG without exc_info.  Rendering
    # *msg* with *args* here (rather than passing them through to
    # ``logger.debug``) lets us append the suppressed-count suffix as
    # positional %-format args (so lazy formatting is preserved — the
    # logging framework only renders the string if DEBUG is enabled).
    rendered = msg % args if args else msg
    logger.debug("%s (suppressed occurrence %d)", rendered, count)
