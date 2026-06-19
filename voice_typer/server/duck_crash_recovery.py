"""Crash recovery for volume ducking.

If the application crashes while system volume is ducked (reduced to
e.g. 25%), the system would be left at that reduced level indefinitely.
This module persists the pre-duck volume state to a small JSON file on
``duck()`` and deletes it on ``restore()``.  On the next application
launch, :meth:`VolumeDucker.initialize` checks for a stale file and
restores the saved volume before any new ducking occurs.

The file lives in the voice-typer config directory (``~/.voice-typer/``)
and contains only the volume level and mute flag — no sensitive data.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from voice_typer.server.volume_backend import VolumeState

log = logging.getLogger(__name__)

_DEFAULT_FILENAME = "duck_crash_recovery.json"


class DuckCrashRecovery:
    """Persists ducked volume state for crash recovery.

    The file is written atomically (temp file + rename) so that a crash
    mid-write cannot corrupt it.  On POSIX, permissions are tightened
    to 0o600 to prevent other users from reading or tampering with the
    file (though it contains no secrets, defense in depth is cheap).
    """

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        if config_dir is None:
            config_dir = Path.home() / ".voice-typer"
        self._path = config_dir / _DEFAULT_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def save(self, state: VolumeState) -> None:
        """Persist the pre-duck volume state.

        Called by ``VolumeDucker.duck()`` after the volume has been
        successfully reduced.  If writing fails, a warning is logged but
        no exception is raised — crash recovery is best-effort.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"linear": state.linear, "muted": state.muted}
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            import os
            import sys

            if sys.platform != "win32":
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
            tmp.replace(self._path)
        except Exception as exc:
            log.warning("[VOLUME-CRASH] Failed to persist duck state: %s", exc)

    def load_stale(self) -> Optional[VolumeState]:
        """Check for a stale duck state file from a crashed session.

        Returns the saved :class:`VolumeState` if a stale file exists,
        or ``None`` if no file is present or it cannot be parsed.
        Does **not** delete the file — the caller is responsible for
        calling :meth:`clear` after successfully restoring.
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
            return VolumeState(
                linear=float(data["linear"]),
                muted=bool(data["muted"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("[VOLUME-CRASH] Failed to parse stale state: %s", exc)
            self.clear()  # delete corrupt file
            return None

    def clear(self) -> None:
        """Delete the duck state file.

        Called by ``VolumeDucker.restore()`` after volume has been
        successfully restored, and by :meth:`load_stale` if the file is
        corrupt.
        """
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError as exc:
            log.debug("[VOLUME-CRASH] Could not delete stale file: %s", exc)
