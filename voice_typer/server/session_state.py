"""Session state model."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

#: Marker filename inside the config dir. Kept separate from
SESSION_MARKER_FILENAME = "session_active"


def session_marker_path(config_dir: Path) -> Path:
    """Path to the session marker under the O3 ``run/`` subdir."""
    from voice_typer.server._paths import RUN_SUBDIR

    return Path(config_dir) / RUN_SUBDIR / SESSION_MARKER_FILENAME


def was_previous_session_abnormal(config_dir: Path) -> bool:
    """True iff the previous session did NOT shut down cleanly."""
    try:
        return session_marker_path(config_dir).exists()
    except OSError:
        log.debug("[SESSION] could not check session marker", exc_info=True)
        return False


def mark_session_active(config_dir: Path) -> None:
    """Write the session-active marker (best-effort)."""
    try:
        from voice_typer.server.config import _secure_atomic_write

        marker = session_marker_path(config_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        content = f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n"
        # durability=False, recreated every launch; atomic rename still
        _secure_atomic_write(marker, content, durability=False)
    except Exception as exc:
        log.debug("[SESSION] could not write session marker: %s", exc)


def clear_session_marker(config_dir: Path) -> None:
    """Remove the session-active marker (best-effort, idempotent)."""
    try:
        marker = session_marker_path(config_dir)
        if marker.exists():
            marker.unlink()
    except OSError as exc:
        log.debug("[SESSION] could not remove session marker: %s", exc)
    except Exception:
        log.debug("[SESSION] could not remove session marker", exc_info=True)


__all__ = [
    "SESSION_MARKER_FILENAME",
    "clear_session_marker",
    "mark_session_active",
    "session_marker_path",
    "was_previous_session_abnormal",
]
