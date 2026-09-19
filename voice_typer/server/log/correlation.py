"""Correlation-id context propagation for the logging framework."""

from __future__ import annotations

import contextvars

# A per-context correlation id that flows through IPC dispatch and
_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("voice_typer_correlation_id", default="")


def set_correlation_id(correlation_id: str) -> object:
    """Set the active correlation id for the current execution context."""
    return _correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> str:
    """Return the active correlation id (``""`` if none in scope)."""
    return _correlation_id_ctx.get()


def reset_correlation_id(token) -> None:
    """Restore a correlation id previously captured via :func:`set_correlation_id`."""
    _correlation_id_ctx.reset(token)


class _correlation_id:  # noqa: N801, lowercase-by-design context manager
    """Context manager that sets a correlation id for its block."""

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
