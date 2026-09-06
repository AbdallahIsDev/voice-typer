"""Base class for hotkey backends.

Provides the abstract :class:`HotkeyBackend` interface that every backend
implements.  Split out from the original ``hotkeys.py`` god-file in
Phase 4.5 () — see ``hotkeys/__init__.py`` for the package-level
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

    # Declared cross-cutting hook attributes. The ``HotkeyDispatcher``
    # (and the ``_NativeBackendAdapter``) set these on ANY backend object
    # they manage — previously the assignments were raw ``setattr`` sites
    # guarded by ``contextlib.suppress(AttributeError)`` +
    # ``# type: ignore[attr-defined]`` because the base class did not
    # declare them. Declaring them here (with safe defaults) means the
    # dispatcher's assignments are statically checkable and non-Windows
    # backends simply inherit the no-op defaults. The windows-native
    # backend overrides the value in its own ``__init__``.
    _tray: object | None = None
    _on_state_change_callback: Callable[[str], None] | None = None
    _delegated: bool = False
    _prefer_message_loop_first: bool = False

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

    # Public setter for the tray reference. Previously the
    # ``_NativeBackendAdapter`` reached into the legacy backend's private
    # ``_tray`` attribute directly (``legacy._tray = self._tray`` with a
    # ``# type: ignore[attr-defined]``) because :class:`HotkeyBackend`
    # didn't expose a tray setter. This default no-op lets backends that
    # don't care about tray notifications ignore the call; subclasses
    # that DO emit tray notifications override it to store the reference.
    def set_tray(self, tray: object | None) -> None:  # noqa: B027 - intentional optional override
        """Store a reference to the system-tray object for notifications.

        Default no-op — backends that don't emit tray notifications
        (e.g. :class:`PynputHotkey`, :class:`WaylandHotkey`) silently
        ignore the call. Backends that do (e.g.
        :class:`WindowsNativeHotkey`, mediated on the dispatcher side by
        :class:`_NativeBackendAdapter`) override it (or have the adapter
        propagate the reference via ``set_tray``) to store ``tray`` on
        ``self._tray`` for later ``tray.notify(...)`` calls.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release resources."""

    @abstractmethod
    def is_alive(self) -> bool:
        """Return True if the listener thread is running."""

    # ``diagnose`` was previously @abstractmethod, forcing
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
