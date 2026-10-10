"""Duplicate-suppression for mic-permission refusal OS notifications.

A blocked microphone refuses every hotkey press terminally; the first
refusal informs the user, the retry presses that follow are the same
information. This module tracks the last published refusal notification
key so repeat presses stay silent at the OS-notification layer (the
bubble / in-app snackbar / tray state keep refreshing idempotently).
"""

from __future__ import annotations

import threading
import time

#: Same key: at most one OS notification this often. A retry burst
#: (hotkey pressed several times in a row) must not stack toasts.
REFUSAL_NOTIFY_SUPPRESS_S = 60.0

_lock = threading.Lock()
_last_notified: tuple[tuple[str, str], float] | None = None


def should_notify_refusal(title: str, message: str) -> bool:
    """True when this refusal notification may be published now.

    Keyed on the exact (title, message) the user would see: an
    identical notification inside the window is a duplicate, a
    different one is fresh information and publishes immediately.
    """
    global _last_notified
    key = (title, message)
    now = time.monotonic()
    with _lock:
        if _last_notified is not None:
            last_key, last_at = _last_notified
            if last_key == key and (now - last_at) < REFUSAL_NOTIFY_SUPPRESS_S:
                return False
        _last_notified = (key, now)
        return True


def mark_microphone_granted() -> None:
    """Positive probe result: the next refusal is fresh news again."""
    global _last_notified
    with _lock:
        _last_notified = None
