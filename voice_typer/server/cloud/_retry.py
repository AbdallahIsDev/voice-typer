"""Cloud retry/backoff helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from voice_typer.server.asr_errors import (
    CloudAuthError,
    CloudEngineError,
    CloudRateLimitError,
    CloudServerError,
)


# Map an HTTP status code from a cloud provider's HTTPError to
def _cloud_http_error_class(code: int) -> type[CloudEngineError]:
    """Return the typed ``CloudEngineError`` subclass for an HTTP status."""
    if code in (401, 403):
        return CloudAuthError
    if code == 429:
        return CloudRateLimitError
    if 500 <= code < 600:
        return CloudServerError
    return CloudEngineError


def _parse_retry_after(header_value: str | None) -> float:
    """Parse a ``Retry-After`` header into a sleep duration in seconds.

    Returns a float suitable for ``time.sleep``.
    """
    if not header_value:
        return 2.0
    # Case 1: integer seconds.
    try:
        seconds = float(header_value)
    except (TypeError, ValueError):
        # Case 2: HTTP-date. ``email.utils.parsedate_to_datetime``
        seconds = 2.0
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(header_value)
            if dt is not None:
                now = datetime.now(timezone.utc)
                # parsedate_to_datetime may return a naive datetime if
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = (dt - now).total_seconds()
                if delta > 0:
                    seconds = delta
        except (TypeError, ValueError, OverflowError):
            pass
    # Cap at 60s; never sleep for a negative amount.
    return max(0.0, min(seconds, 60.0))
