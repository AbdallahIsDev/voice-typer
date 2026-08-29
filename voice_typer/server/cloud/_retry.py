"""Cloud retry-policy primitives.

Extracted from the ``cloud_engines.py`` monolith: ``_parse_retry_after``
(RFC 7231 §7.1.3 ``Retry-After`` parsing with a 60 s sleep cap) and
``_cloud_http_error_class`` (HTTP status → typed ``CloudEngineError``
subclass). ``voice_typer/server/cloud_engines.py`` re-exports both
names so existing importers keep resolving.

NOTE for tests that freeze time: ``_parse_retry_after`` resolves the
``datetime`` class from THIS module's namespace (its owning leaf), so
``monkeypatch`` must target ``voice_typer.server.cloud._retry`` —
patching ``datetime`` on the facade module has no effect on this
function.
"""

from __future__ import annotations

from datetime import datetime, timezone

from voice_typer.server.asr_errors import (
    CloudAuthError,
    CloudEngineError,
    CloudRateLimitError,
    CloudServerError,
)


# Map an HTTP status code from a cloud provider's HTTPError to
# the appropriate typed ``CloudEngineError`` subclass. Used by both the
# OpenAI-compatible path (``_send_openai_compatible``) and the Deepgram
# path (``_send_deepgram``). Mapping:
#   401, 403                  → CloudAuthError      (API key invalid/revoked)
#   429                       → CloudRateLimitError (after retry budget)
#   5xx (500-599)             → CloudServerError
#   any other HTTP status     → CloudEngineError    (generic cloud failure)
# Callers that want to surface a more specific message can still wrap
# the chosen exception via ``raise CloudAuthError("...") from exc``;
# the type is what the IPC layer switches on, not the message.
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

    RFC 7231 §7.1.3 allows ``Retry-After`` to be either:
          1. An integer number of seconds, OR
          2. An HTTP-date (e.g. ``Wed, 21 Oct 2015 07:28:00 GMT``).

        We cap the wait at 60 seconds so a hostile or misconfigured server
        cannot stall the dictation thread indefinitely. A negative or
        unparseable value falls back to a small default (2s) so we still
        honor the spirit of "wait briefly before retrying" without trusting
        the server blindly.

        Returns a float suitable for ``time.sleep``.
    """
    if not header_value:
        return 2.0
    # Case 1: integer seconds.
    try:
        seconds = float(header_value)
    except (TypeError, ValueError):
        # Case 2: HTTP-date. ``email.utils.parsedate_to_datetime``
        # returns a timezone-aware datetime (or None if unparseable).
        seconds = 2.0
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(header_value)
            if dt is not None:
                now = datetime.now(timezone.utc)
                # parsedate_to_datetime may return a naive datetime if
                # the date string has no tz; normalize to UTC.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                delta = (dt - now).total_seconds()
                if delta > 0:
                    seconds = delta
        except (TypeError, ValueError, OverflowError):
            pass
    # Cap at 60s; never sleep for a negative amount.
    return max(0.0, min(seconds, 60.0))
