"""shared IPC server helpers split out of ``ipc_server.py``."""

from __future__ import annotations

import logging

from voice_typer.server import event_bus
from voice_typer.server.ipc.registry import _READONLY_COMMANDS  # noqa: F401

# the unauthenticated stdin/stdout IPC listener is gated
_STDIN_IPC_ENV_VAR: str = "VOICE_TYPER_ALLOW_STDIN_IPC"

# The IPC server's process-wide logger.  ``logging.getLogger`` returns
log = logging.getLogger("voice_typer.server.ipc_server")


def _push_event_now(msg: dict) -> bool:
    """Push a raw event to ALL active IPC servers, if any are wired.

    Returns True if at least one server accepted the event, False if
    """
    return event_bus.publish(msg)


__all__ = [
    "_READONLY_COMMANDS",
    "_STDIN_IPC_ENV_VAR",
    "_push_event_now",
    "log",
]
