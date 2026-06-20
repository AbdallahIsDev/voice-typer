"""Extracted hotkey formatting logic from tray.py.

ARCH-007: _format_hotkey_label was previously an inline static method
in TrayIcon.  It is now a standalone function so other modules (e.g.
tray_window.py) can import it without creating a TrayIcon instance.
"""

import logging

log = logging.getLogger(__name__)


def format_hotkey_label(hotkey: str) -> str:
    """Format a hotkey string like '<ctrl>+<shift>+f2' into 'Ctrl+Shift+F2'.

    Handles pynput-style angle-bracket notation and normalizes
    modifier names to user-friendly display form.
    """
    parts = []
    for part in hotkey.split("+"):
        clean = part.strip().strip("<>").lower()
        if clean == "ctrl":
            parts.append("Ctrl")
        elif clean == "alt":
            parts.append("Alt")
        elif clean == "shift":
            parts.append("Shift")
        elif clean in {"cmd", "win", "super"}:
            parts.append("Win")
        else:
            parts.append(clean.upper())
    return "+".join(parts)
