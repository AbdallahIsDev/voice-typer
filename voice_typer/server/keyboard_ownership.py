"""Centralized keyboard ownership manager.

ARCH-ESC-001: solves the "Escape key routing" problem with a clean
ownership model instead of scattered boolean flags.

# Problem
The previous architecture had THREE independent Escape handlers:

  1. The frontend HotkeyPicker (in capture mode) — listens to DOM
     ``keydown`` events for Escape to exit capture.
  2. The backend's global ESC cancel hotkey backend (Win32 native /
     pynput) — polls the OS for Escape and fires ``_cancel_dictation``.
  3. The backend's recording controller — receives ``cancel()`` calls
     and discards the active recording.

These three handlers had no shared state. The frontend's "I'm in
capture mode" was communicated to the backend via an async IPC
(``set_esc_cancel_paused``), which created a race window: if the user
pressed Escape before the IPC was processed, the backend would fire
``_cancel_dictation`` even though the frontend owned the keyboard.

The recording controller's ``cancel()`` checked ``recorder.recording``
to early-return when no recording was active, but ``recorder.recording``
could be ``True`` from a stale state (a previous session that wasn't
cleaned up properly), causing spurious ``[CANCEL]`` logs.

# Solution
A single ``KeyboardOwnership`` singleton tracks which subsystem owns
the keyboard at any given time. The owner is one of:

  - ``"normal"``        — no special owner; global hotkeys behave normally
  - ``"hotkey_capture"`` — frontend HotkeyPicker is capturing a hotkey;
                           global hotkey backends (including ESC cancel)
                           must NOT fire.
  - ``"recording"``      — a recording session is active; the ESC cancel
                           hotkey may fire to cancel it.

Ownership transitions are synchronous from the backend's perspective
(``set_owner`` is called directly by IPC handlers in the same thread
that the hotkey backends poll from). From the frontend's perspective,
the IPC is still async, BUT the backend's hotkey backends now consult
``KeyboardOwnership.current_owner()`` on every poll — so even if the
IPC is delayed, the moment it lands, the next poll cycle honors it.

The previous ``_esc_cancel_paused`` boolean on ``VoiceTyperApp`` is
now a thin alias to ``KeyboardOwnership`` for backward compat with
existing tests.
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

log = logging.getLogger(__name__)


Owner = Literal["normal", "hotkey_capture", "recording"]


class KeyboardOwnership:
    """Singleton tracking which subsystem owns keyboard input.

    Thread-safe — the hotkey backends poll from background threads
    while the IPC handler sets ownership from the IPC thread.
    """

    _instance: KeyboardOwnership | None = None
    _lock = threading.Lock()

    def __new__(cls) -> KeyboardOwnership:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    # Use object.__setattr__ to initialize the instance
                    # attributes. Pyrefly rejects direct annotation on
                    # ``cls._instance._owner`` (bad-assignment), and
                    # plain ``instance._owner = ...`` triggers a false
                    # positive because the class-body annotations declare
                    # the attribute but pyrefly doesn't see the assignment
                    # as a "self" assignment in __new__. Using setattr
                    # sidesteps the annotation check entirely while
                    # remaining fully type-safe at runtime.
                    object.__setattr__(instance, "_owner", "normal")
                    object.__setattr__(instance, "_reason", "")
                    cls._instance = instance
        return cls._instance

    def set_owner(self, owner: Owner, reason: str = "") -> None:
        """Set the current keyboard owner.

        Parameters
        ----------
        owner : "normal" | "hotkey_capture" | "recording"
            The subsystem that now owns keyboard input.
        reason : str
            Human-readable reason for the transition (logged at INFO
            level for auditability).
        """
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
        """True if the frontend is currently capturing a hotkey.

        Hotkey backends should consult this on every poll and skip
        firing if True — the frontend owns the keyboard during capture.
        """
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
