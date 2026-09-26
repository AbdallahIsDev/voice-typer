"""Cloud retry/backoff helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from voice_typer.server.asr_errors import (
    CloudAuthError,
    CloudEngineError,
    CloudRateLimitError,
    CloudServerError,
)
from voice_typer.server.retry import parse_retry_after as _shared_parse_retry_after


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
    # Missing/unparsable headers fall back to 2s (shared parser default).
    # ``now`` stays module-local so tests can freeze ``_retry.datetime``.
    return _shared_parse_retry_after(header_value, default=2.0, now=datetime.now(timezone.utc))
