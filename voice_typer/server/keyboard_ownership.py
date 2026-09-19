"""Keyboard ownership handoff helpers."""

from __future__ import annotations

import logging
import threading
from typing import Literal

log = logging.getLogger(__name__)


Owner = Literal["normal", "hotkey_capture", "recording"]


class KeyboardOwnership:
    """Singleton tracking which subsystem owns keyboard input."""

    _instance: KeyboardOwnership | None = None
    _lock = threading.Lock()

    def __new__(cls) -> KeyboardOwnership:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    # Use object.__setattr__ to initialize the instance
                    object.__setattr__(instance, "_owner", "normal")
                    object.__setattr__(instance, "_reason", "")
                    cls._instance = instance
        return cls._instance

    def set_owner(self, owner: Owner, reason: str = "") -> None:
        """Set the current keyboard owner."""
        with self._lock:
            prev = self._owner
            self._owner = owner
            self._reason = reason
        if prev != owner:
            log.info(
                "[KEYBOARD_OWNERSHIP] %s -> %s (reason=%s)",
                prev,
                owner,
                reason or "(none)",
            )

    def current_owner(self) -> Owner:
        """Return the current keyboard owner."""
        with self._lock:
            return self._owner

    def is_hotkey_capture_active(self) -> bool:
        """True if the frontend is currently capturing a hotkey."""
        with self._lock:
            return self._owner == "hotkey_capture"

    def is_recording_active(self) -> bool:
        """True if a recording session is currently active.

        The ESC cancel hotkey should only fire when this is True.
        """
        with self._lock:
            return self._owner == "recording"

    def reset(self) -> None:
        """Reset to "normal" owner. Used in tests and on shutdown."""
        with self._lock:
            self._owner = "normal"
            self._reason = ""


# Module-level singleton accessor
def keyboard_ownership() -> KeyboardOwnership:
    """Return the global KeyboardOwnership singleton."""
    return KeyboardOwnership()
