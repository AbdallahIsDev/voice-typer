"""keeps resolving exactly as before (C-ARCH-2).
This package splits the former 1240-LOC ``microphone_watcher.py`` monolith
pattern (same as ``RecorderInitMixin``, ``_RecoveryIO``, etc.).
Shared lifecycle     :mod:`._core`: ``MicrophoneDeviceWatcher``
Linux polling        :mod:`._linux`: ``_LinuxMixin``
macOS polling        :mod:`._macos`: ``_MacOSMixin``
Windows WM_DEVICECHANGE  :mod:`._windows`: ``_WindowsMixin``
Patch-target contract: ``voice_typer.server.microphone_watcher.X``
"""

from voice_typer.server.microphone_watcher._core import MicrophoneDeviceWatcher as MicrophoneDeviceWatcher

__all__ = ["MicrophoneDeviceWatcher"]
