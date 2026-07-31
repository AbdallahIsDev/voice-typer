"""Wayland-without-SNI detection extracted from ``tray.py``.

detects Linux Wayland compositors that don't implement
the StatusNotifierItem D-Bus interface (Sway, Hyprland, dwl, river,
bare wlroots). On these compositors, pystray's GTK backend hangs
forever on ``icon.run()`` because there's no tray to attach to.

Previously this lived inline as a 68-LOC static method on
``TrayIcon`` (``_is_linux_wayland_without_sni``). Extracted here as a
standalone module-level function so the detection logic is testable
without instantiating a ``TrayIcon`` and so ``tray.py`` shrinks toward
the project's Rule-19/20 entry-file size target.

``tray.py`` re-exports ``is_linux_wayland_without_sni`` and keeps a
static-method delegator on ``TrayIcon`` for backward compatibility
with tests that call ``TrayIcon._is_linux_wayland_without_sni()``.
"""

from __future__ import annotations

import logging
import os

from voice_typer.server.platform_utils import is_linux

log = logging.getLogger(__name__)


def is_linux_wayland_without_sni() -> bool:
    """Detect Linux Wayland without StatusNotifierItem.

    Returns True if ALL of the following are true:
      1. We're on Linux (sys.platform starts with "linux").
      2. The session is Wayland (XDG_SESSION_TYPE=wayland).
      3. No StatusNotifierItem watcher is registered on the D-Bus
         session bus.

    Detection of (3) is best-effort: we try to call the
    ``org.kde.StatusNotifierWatcher`` service via D-Bus.  If the
    call fails (service unknown, bus unavailable, dbus module
    missing), we assume SNI is not available — which matches the
    user's complaint that "the tray silently fails" on Sway/Hyprland.

    We DON'T try to detect specific compositors by name (Sway,
    Hyprland, etc.) because new compositors appear regularly and
    the SNI-availability check is the actual contract that
    matters.
    """
    if not is_linux():
        return False
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return False
    # Try to detect the StatusNotifierItem watcher service on D-Bus.
    try:
        import dbus  # type: ignore[import-untyped]
    except ImportError:
        # No dbus module — we can't detect SNI programmatically.
        # Conservative: assume SNI is NOT available (matches the
        # user's complaint of "silent failure" on minimal Wayland
        # setups that typically don't have python-dbus installed).
        log.debug(
            "[TRAY] Wayland session detected but python-dbus not installed; assuming StatusNotifierItem is unavailable."
        )
        return True
    try:
        bus = dbus.SessionBus()
        # The SNI watcher is the well-known name registered by
        # the compositor's tray (e.g. waybar, swaync, KDE's
        # plasma-workspace).  If it's not registered, the
        # NameHasOwner call returns False.
        proxy = bus.get_object(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
        )
        has_owner = bool(
            proxy.NameHasOwner(
                "org.kde.StatusNotifierWatcher",
                dbus_interface="org.freedesktop.DBus",
            )
        )
        if not has_owner:
            log.info(
                "[TRAY] Wayland session detected and org.kde.StatusNotifierWatcher "
                "is NOT registered on the D-Bus session bus. Tray will be skipped."
            )
            return True
        return False
    except Exception as exc:
        log.debug(
            "[TRAY] D-Bus check for StatusNotifierItem failed: %s — assuming SNI is unavailable.",
            exc,
        )
        return True
