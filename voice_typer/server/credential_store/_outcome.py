"""Thread-local outcome recording for ``store_secret``.

Owns the "thread-local outcome recording" concern. The IPC ``set_config``
handler needs to surface *why* a ``store_secret`` call fell back to
plaintext without changing ``store_secret``'s ``bool`` return type, so
we record the outcome in a :class:`threading.local` and expose it via
:func:`last_store_outcome`. The state is thread-local because the IPC
server is multi-threaded and the call to ``store_secret`` and the
subsequent call to ``last_store_outcome`` always happen on the same
IPC handler thread (no inter-thread hand-off).
"""

from __future__ import annotations

import threading
from typing import Any

from ._redact import _redact_sensitive

#: Thread-local record of the most recent ``store_secret`` outcome.
#: Used by :func:`last_store_outcome` so the IPC handler can surface
#: ``{"stored_in": "keyring"|"plaintext"|"failed"|"deleted"|"unknown",
#: "provider": "...", "reason": "..."}`` in the ``set_config`` ack payload
#: WITHOUT changing ``store_secret``'s return type. ``threading.local``
#: is used directly (rather than a ``threading.Lock`` + dict[thread_id, dict])
#: so we don't accumulate stale entries for threads that have exited.
_last_store_outcome = threading.local()


def _set_last_store_outcome(
    stored_in: str,
    reason: str | None,
    provider: str | None = None,
) -> None:
    """Record the outcome of the most recent ``store_secret`` call.

    Parameters
    ----------
    stored_in : str
        ``"keyring"`` if the secret was stored in the OS keychain, or
        ``"plaintext"`` if it was written to ``config.json`` as a
        fallback. ``"deleted"`` is used when an empty value triggered
        :func:`delete_secret`. ``"failed"`` is used when keyring
        failed AND the plaintext fallback also failed — the secret was
        NOT saved anywhere.
    reason : str | None
        Short, redacted reason string when ``stored_in`` is
        ``"plaintext"`` (the keyring exception message passed through
        :func:`_redact_sensitive`). ``None`` for the keyring-success
        and delete paths. For the ``"failed"`` path, a short summary
        of both failures.
    provider : str | None
        The provider name passed to ``store_secret``. ``None`` only on
        the ``unknown`` (no-store-yet) path.
    """
    _last_store_outcome.outcome = {
        "stored_in": stored_in,
        "reason": _redact_sensitive(reason) if reason else None,
        "provider": provider,
    }


def last_store_outcome() -> dict[str, Any]:
    """Return the outcome of the most recent ``store_secret`` call.

    Returns
    -------
    dict with keys ``stored_in``, ``reason``, ``provider`` (see
    :func:`_set_last_store_outcome` for the values). The returned dict
    is a shallow copy so callers can't mutate our thread-local state.
    """
    outcome = getattr(_last_store_outcome, "outcome", None)
    if outcome is None:
        return {"stored_in": "unknown", "reason": None, "provider": None}
    return dict(outcome)


__all__ = ["_last_store_outcome", "_set_last_store_outcome", "last_store_outcome"]
