"""Base class for hotkey backends.

Provides the abstract :class:`HotkeyBackend` interface that every backend
implements.  Split out from the original ``hotkeys.py`` god-file in
Phase 4.5 (ARCH-045) — see ``hotkeys/__init__.py`` for the package-level
re-export surface that preserves the legacy ``voice_typer.server.hotkeys``
import path.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

# Preserve the original logger name (``voice_typer.server.hotkeys``) so
# log records emitted from any submodule land under the same logger as
# before the split.  Submodules import this `log` instead of creating
# their own.
log = logging.getLogger("voice_typer.server.hotkeys")


class HotkeyBackend(ABC):
    """Abstract base for hotkey backends."""

    def __init__(self, hotkey_str: str):
        self.hotkey_str = hotkey_str
        self._on_release_callback: Callable[[], None] | None = None
        # Toggle-mode flag: when True (set by HotkeyDispatcher for the main
        # dictation hotkey in toggle mode), the toggle fires on key-UP
        # (release) instead of key-down. This prevents a press-and-hold from
        # starting and then immediately stopping recording. Ignored in
        # push-to-talk mode (which has _on_release_callback set).
        self._toggle_on_keyup: bool = False

    @abstractmethod
    def start(self, callback: Callable[[], None]) -> None:
        """Start listening for the hotkey. Calls *callback* when pressed."""

    def set_on_release(self, callback: Callable[[], None] | None) -> None:
        """Set a callback for key release (used by push-to-talk mode)."""
        self._on_release_callback = callback

    def set_toggle_on_keyup(self, value: bool) -> None:
        """In toggle mode, fire the toggle on key-up (release) instead of
        key-down. Set True by HotkeyDispatcher for the main dictation
        hotkey so a press-and-hold cannot start-then-stop recording.
        """
        self._toggle_on_keyup = value

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release resources."""

    @abstractmethod
    def is_alive(self) -> bool:
        """Return True if the listener thread is running."""

    # NEW-DEAD-009: ``diagnose`` was previously @abstractmethod, forcing
    # every subclass to implement a debug string even though only test
    # callers invoke it.  We provide a default no-op implementation so
    # new backends (e.g. a future macOS or Linux native backend) don't
    # have to implement it just to satisfy the Protocol.  Existing
    # backends (PynputHotkey, Win32Hotkey, etc.) still override it
    # because their tests rely on the diagnostic output.
    def diagnose(self) -> str:
        """Return a human-readable diagnostic string.

        Default implementation returns an empty string.  Subclasses
        override to provide backend-specific debug info (registered
        hotkeys, listener thread state, etc.).
        """
        return ""
