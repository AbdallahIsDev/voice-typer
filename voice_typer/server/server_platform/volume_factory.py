"""Volume-backend factory."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    from voice_typer.server.volume_backend_base import VolumeBackend

# Patch-path bridge: read ``SYSTEM`` through the owning
from voice_typer.server.server_platform import platform_flags as _platform_flags

log = logging.getLogger(__name__)


def get_volume_backend() -> VolumeBackend | None:
    """Return the appropriate :class:`VolumeBackend` for this platform.

    Returns ``None`` if the platform is not supported (no backend class
    """
    try:
        if _platform_flags.SYSTEM == "win32":
            from voice_typer.server.volume_backends import WinVolumeBackend

            return WinVolumeBackend()
        elif _platform_flags.SYSTEM == "darwin":
            from voice_typer.server.volume_backends import MacVolumeBackend

            return MacVolumeBackend()
        elif _platform_flags.SYSTEM == "linux":
            from voice_typer.server.volume_backends import LinuxVolumeBackend

            return LinuxVolumeBackend()
        else:
            log.debug("[VOLUME] Unsupported platform: %s", _platform_flags.SYSTEM)
            return None
    except Exception as exc:
        log.warning("[VOLUME] Failed to create backend for %s: %s", _platform_flags.SYSTEM, exc)
        return None
