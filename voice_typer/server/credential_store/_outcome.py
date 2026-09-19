"""Thread-local outcome recording for ``store_secret``."""

from __future__ import annotations

import threading
from typing import Any

from ._redact import _redact_sensitive

#: Thread-local record of the most recent ``store_secret`` outcome.
_last_store_outcome = threading.local()


def _set_last_store_outcome(
    stored_in: str,
    reason: str | None,
    provider: str | None = None,
) -> None:
    """Record the outcome of the most recent ``store_secret`` call."""
    _last_store_outcome.outcome = {
        "stored_in": stored_in,
        "reason": _redact_sensitive(reason) if reason else None,
        "provider": provider,
    }


def last_store_outcome() -> dict[str, Any]:
    """Return the outcome of the most recent ``store_secret`` call.

    Returns
    """
    outcome = getattr(_last_store_outcome, "outcome", None)
    if outcome is None:
        return {"stored_in": "unknown", "reason": None, "provider": None}
    return dict(outcome)


__all__ = ["_last_store_outcome", "_set_last_store_outcome", "last_store_outcome"]
