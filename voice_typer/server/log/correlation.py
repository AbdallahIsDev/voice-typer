"""Correlation-id context propagation for the logging framework.

Provides a per-context correlation id that flows through IPC dispatch
and the dictation pipeline so all log lines belonging to one user
request / transcription cycle carry the same ``correlation_id``.

Stored in a :class:`contextvars.ContextVar` so concurrent async/threaded
work (multiple overlapping IPC requests, the transcription thread) each
see their own value without explicit parameter threading on every call
site.  ``""`` means "no correlation id in scope" — formatters render it
as an absent key (JSON) / nothing (text).

Extracted from the original monolithic ``log.py`` (logging-package
split). Kept dependency-free so the formatters module can import it
without creating a circular dependency on the parent ``log`` package.
"""

from __future__ import annotations

import contextvars

# A per-context correlation id that flows through IPC dispatch and
# the dictation pipeline so that all log lines belonging to one user
# request / transcription cycle carry the same ``correlation_id``.
_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("voice_typer_correlation_id", default="")


def set_correlation_id(correlation_id: str) -> object:
    """Set the active correlation id for the current execution context.

    Call from IPC dispatch (request id) or the dictation pipeline
    (cycle id) so downstream logs auto-carry it.  Returns the token
    captured by :meth:`contextvars.ContextVar.set` — pass it to
    :func:`reset_correlation_id` (or use the :func:`correlation_id`
    context manager) to restore the previous value.
    """
    return _correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> str:
    """Return the active correlation id (``""`` if none in scope)."""
    return _correlation_id_ctx.get()


def reset_correlation_id(token) -> None:
    """Restore a correlation id previously captured via :func:`set_correlation_id`."""
    _correlation_id_ctx.reset(token)


class _correlation_id:  # noqa: N801 — lowercase-by-design context manager
    """Context manager that sets a correlation id for its block.

    Usage::

        with log._correlation_id(cycle_id):
            ...  # all logs here carry correlation_id=cycle_id
    """

    def __init__(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id
        self._token = None

    def __enter__(self) -> _correlation_id:
        if self._correlation_id:
            self._token = _correlation_id_ctx.set(self._correlation_id)
        return self

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            _correlation_id_ctx.reset(self._token)
            self._token = None


__all__ = [
    "_correlation_id",
    "_correlation_id_ctx",
    "get_correlation_id",
    "reset_correlation_id",
    "set_correlation_id",
]
