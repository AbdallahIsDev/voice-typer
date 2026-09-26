"""Shared retry-with-backoff primitives (single consolidation point)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

log = logging.getLogger(__name__)

MAX_RETRY_AFTER_S = 60.0


class RetryAbortedError(RuntimeError):
    """Raised when an abort event fires before or during a retry wait."""


def parse_retry_after(
    value: str | None,
    *,
    default: float = 0.0,
    cap: float = MAX_RETRY_AFTER_S,
    now: datetime | None = None,
) -> float:
    """Parse a ``Retry-After`` header into seconds, clamped to ``[0, cap]``."""
    if not value:
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = default
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(value)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                current = now if now is not None else datetime.now(timezone.utc)
                delta = (dt - current).total_seconds()
                if delta > 0:
                    seconds = delta
        except (TypeError, ValueError, OverflowError):
            pass
    return max(0.0, min(seconds, cap))


def delay_for_attempt(delays: tuple[float, ...], attempt: int) -> float:
    """Return the backoff for zero-based *attempt*, reusing the last entry past the end."""
    if not delays:
        return 0.0
    return delays[attempt] if attempt < len(delays) else delays[-1]


def sleep_interruptible(
    delay: float,
    *,
    abort_event=None,
    gate_check: Callable[[], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
) -> bool:
    """Sleep *delay* seconds; return ``True`` when *abort_event* fired mid-wait."""
    if delay <= 0:
        if gate_check is not None:
            gate_check()
        return bool(abort_event is not None and abort_event.is_set())
    if abort_event is not None:
        if gate_check is not None:
            gate_check()
        return bool(abort_event.wait(timeout=delay))
    if gate_check is None:
        if sleep_fn is None:
            sleep_fn = time.sleep
        sleep_fn(delay)
        return False
    deadline = time.monotonic() + delay
    while True:
        gate_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if sleep_fn is None:
            time.sleep(min(0.2, remaining))
        else:
            sleep_fn(min(0.2, remaining))


def run_with_retry(
    func: Callable,
    *,
    max_attempts: int = 3,
    delays: tuple[float, ...] = (1.0,),
    should_retry: Callable[[Exception, int], bool] | None = None,
    on_retry: Callable[[Exception, int, float], None] | None = None,
    on_give_up: Callable[[BaseException, int], None] | None = None,
    abort_event=None,
    gate_check: Callable[[], None] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    retry_after_fn: Callable[[Exception], float | None] | None = None,
):
    """Run ``func()`` with backoff; only ``Exception`` is retried, ``BaseException`` propagates."""
    last_exc: BaseException = RuntimeError("no attempts made")
    for attempt in range(max_attempts):
        if abort_event is not None and abort_event.is_set():
            raise RetryAbortedError(f"retry aborted before attempt {attempt + 1}/{max_attempts}")
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if should_retry is not None and not should_retry(exc, attempt):
                raise
            if attempt >= max_attempts - 1:
                break
            delay = delay_for_attempt(delays, attempt)
            if retry_after_fn is not None:
                override = retry_after_fn(exc)
                if override is not None and override > 0:
                    delay = override
            if on_retry is not None:
                on_retry(exc, attempt, delay)
            if sleep_interruptible(delay, abort_event=abort_event, gate_check=gate_check, sleep_fn=sleep_fn):
                raise RetryAbortedError(f"retry aborted during backoff after attempt {attempt + 1}") from exc
    if on_give_up is not None:
        on_give_up(last_exc, max_attempts)
    raise last_exc


__all__ = [
    "MAX_RETRY_AFTER_S",
    "RetryAbortedError",
    "delay_for_attempt",
    "parse_retry_after",
    "run_with_retry",
    "sleep_interruptible",
]
