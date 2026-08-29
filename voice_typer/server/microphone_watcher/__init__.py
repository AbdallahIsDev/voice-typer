"""Microphone device-change watcher — package facade.

This package splits the former 1240-LOC ``microphone_watcher.py`` monolith
(see GQ-L15) into focused modules using the repo's established mixin
pattern (same as ``RecorderInitMixin``, ``_RecoveryIO``, etc.).

Concern              Module
-------------------- ------------------------------------------
Shared lifecycle     :mod:`._core` — ``MicrophoneDeviceWatcher``
                     (init, start, stop, _run, _invoke_callback,
                     _DEBOUNCE_SECONDS, active-mic-lost hooks)
Linux polling        :mod:`._linux` — ``_LinuxMixin``
macOS polling        :mod:`._macos` — ``_MacOSMixin``
Windows WM_DEVICECHANGE  :mod:`._windows` — ``_WindowsMixin``

Patch-target contract: ``voice_typer.server.microphone_watcher.X``
keeps resolving exactly as before (C-ARCH-2).
"""
from voice_typer.server.microphone_watcher._core import MicrophoneDeviceWatcher as MicrophoneDeviceWatcher

__all__ = ["MicrophoneDeviceWatcher"]