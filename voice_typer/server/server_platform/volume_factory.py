"""Volume-backend factory.

Phase 4.5 / ARCH-045 — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Contains the
single :func:`get_volume_backend` factory that selects the appropriate
:class:`voice_typer.server.volume_backend_base.VolumeBackend` for the
current platform.

Patch-path compatibility
------------------------
Tests do not patch ``get_volume_backend`` via the dotted
``voice_typer.server.server_platform.get_volume_backend`` path on this
module's local binding — instead, ``tests/test_volume_ducker.py`` does
``plat.get_volume_backend = lambda: None`` (assignment on the package's
``__dict__``).  Consumers (``volume_ducker.py``) import the function
lazily inside a method, so they pick up the patched package binding at
call time.  No ``_pkg`` indirection is required inside this module.

``inspect.getsource`` compatibility
-----------------------------------
``get_volume_backend`` is genuinely defined here, so
``inspect.getsource(get_volume_backend)`` continues to read from this
file.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.volume_backend_base import VolumeBackend

# Patch-path bridge: route lookups of ``SYSTEM`` through the package
# namespace so test patches of the form
# ``monkeypatch.setattr("voice_typer.server.server_platform.SYSTEM", "linux")``
# keep affecting production code defined here.
from voice_typer.server import server_platform as _pkg

log = logging.getLogger(__name__)


# ─── Volume backend factory ────────────────────────────────────────────


def get_volume_backend() -> VolumeBackend | None:
    """Return the appropriate :class:`VolumeBackend` for this platform.

    Returns ``None`` if the platform is not supported (no backend class
    exists).  The returned backend is **not yet initialised** — the
    caller must call ``initialize()`` to verify that native libraries
    are available.

    RW-6 (pyrefly): return type tightened from ``Optional[object]`` to
    ``Optional[VolumeBackend]``. All three concrete backends
    (``WinVolumeBackend``, ``MacVolumeBackend``, ``LinuxVolumeBackend``)
    inherit from :class:`voice_typer.server.volume_backend_base.VolumeBackend`,
    so the looser ``object`` annotation was both inaccurate and the
    root cause of the ``bad-assignment`` downstream in
    :mod:`voice_typer.server.volume_ducker` (``self._backend`` is typed
    ``VolumeBackend | None``). ``VolumeBackend`` is imported under
    ``TYPE_CHECKING`` to avoid a circular import at runtime — the
    concrete backend classes already import it themselves.

    Selection:
      - ``win32``  → :class:`WinVolumeBackend` (pycaw)
      - ``darwin`` → :class:`MacVolumeBackend` (CoreAudio / osascript)
      - ``linux``  → :class:`LinuxVolumeBackend` (pactl → wpctl → amixer)
    """
    try:
        if _pkg.SYSTEM == "win32":
            from voice_typer.server.volume_backends import WinVolumeBackend

            return WinVolumeBackend()
        elif _pkg.SYSTEM == "darwin":
            from voice_typer.server.volume_backends import MacVolumeBackend

            return MacVolumeBackend()
        elif _pkg.SYSTEM == "linux":
            from voice_typer.server.volume_backends import LinuxVolumeBackend

            return LinuxVolumeBackend()
        else:
            log.debug("[VOLUME] Unsupported platform: %s", _pkg.SYSTEM)
            return None
    except Exception as exc:
        log.warning("[VOLUME] Failed to create backend for %s: %s", _pkg.SYSTEM, exc)
        return None
