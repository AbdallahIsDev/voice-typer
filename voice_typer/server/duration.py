"""Duration formatting for log lines.

Project-wide log convention (``AGENTS.md`` C-LOG-2): every
lifecycle-completion log line ends with a ``_<duration>`` suffix so
performance is measurable at a glance — ``_2.3s`` for sub-minute
durations, ``_1m 2.3s`` for anything longer.

This module is intentionally dependency-free so any module in the
server can import it without import-order or circular-import risk.
"""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Format *seconds* as ``_2.3s`` (sub-minute) or ``_1m 2.3s``.

    ``seconds`` is clamped at 0 so a negative clock delta can never
    render as a nonsense duration. The value is rounded to 0.1s BEFORE
    the minute/split decision, so 59.96s renders as ``_1m 0.0s``
    (consistent with 60.0s) instead of the misleading ``_60.0s``.
    """
    seconds = max(0.0, seconds)
    rounded = round(seconds, 1)
    if rounded < 60:
        return f"_{rounded:.1f}s"
    minutes, secs = divmod(rounded, 60)
    return f"_{int(minutes)}m {secs:.1f}s"
