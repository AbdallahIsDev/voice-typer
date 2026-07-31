"""Concrete volume backends for Windows, macOS, and Linux.

This package was extracted from the original ``voice_typer/server/volume_backends.py``
monolith (1055 LOC) per   Each platform's backend lives in its own
module:

- :mod:`voice_typer.server.volume_backends.windows` — ``WinVolumeBackend``
  (pycaw / WASAPI)
- :mod:`voice_typer.server.volume_backends.macos` — ``MacVolumeBackend``
  (CoreAudio via pyobjc, with osascript fallback)
- :mod:`voice_typer.server.volume_backends.linux` — ``LinuxVolumeBackend``
  (pactl → wpctl → amixer)

All three backends implement
:class:`voice_typer.server.volume_backend_base.VolumeBackend`.

Import order matters: ``get_volume_backend()`` in
:mod:`voice_typer.server.server_platform.volume_factory` selects the first
backend whose :meth:`initialize` succeeds for the current platform.  All
imports of platform-specific libraries (pycaw, pyobjc, subprocess CLI
tools) are guarded so that the package imports cleanly on any OS — the
backend simply returns ``False`` from :meth:`initialize` if its native
library is unavailable.

Backwards-compatibility re-exports
----------------------------------
The original ``volume_backends.py`` module exposed ``WinVolumeBackend``,
``MacVolumeBackend``, and ``LinuxVolumeBackend`` at the top level.  This
``__init__.py`` re-exports all three so existing
``from voice_typer.server.volume_backends import X`` statements continue
to resolve unchanged.

``Path`` is also re-exported so that tests which do
``monkeypatch.setattr(volume_backends, "Path", fake_path)`` (see
``tests/test_smart_duck.py::TestLinuxIsSpeakerActive``) keep working —
``LinuxVolumeBackend._alsa_is_playing`` looks up ``Path`` via this
package's namespace specifically so those patches continue to intercept
``Path("/proc/asound")`` lookups after the split.
"""

from __future__ import annotations

from pathlib import Path  # re-exported for test monkeypatch compatibility

from .linux import LinuxVolumeBackend
from .macos import MacVolumeBackend
from .windows import WinVolumeBackend

__all__ = [
    "WinVolumeBackend",
    "MacVolumeBackend",
    "LinuxVolumeBackend",
    "Path",
]
