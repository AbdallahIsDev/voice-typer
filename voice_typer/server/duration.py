"""format_duration() for C-LOG-2 space-separated duration suffixes."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Format *seconds* as `` 2.3s`` (sub-minute) or `` 1m 2.3s``."""
    seconds = max(0.0, seconds)
    rounded = round(seconds, 1)
    if rounded < 60:
        return f" {rounded:.1f}s"
    minutes, secs = divmod(rounded, 60)
    return f" {int(minutes)}m {secs:.1f}s"
