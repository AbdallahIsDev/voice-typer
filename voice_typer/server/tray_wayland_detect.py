"""Wayland session detection for tray backends."""

from __future__ import annotations

import logging
import os

from voice_typer.server.platform_utils import is_linux

log = logging.getLogger(__name__)


def is_linux_wayland_without_sni() -> bool:
    """Detect Linux Wayland without StatusNotifierItem.

    Returns True if ALL of the following are true:
    """
    if not is_linux():
        return False
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":
        return False
    # Try to detect the StatusNotifierItem watcher service on D-Bus.
    try:
        import dbus  # type: ignore[import-untyped]
    except ImportError:
        # No dbus module, we can't detect SNI programmatically.
        log.debug(
            "[TRAY] Wayland session detected but python-dbus not installed; assuming StatusNotifierItem is unavailable."
        )
        return True
    try:
        bus = dbus.SessionBus()
        # The SNI watcher is the well-known name registered by
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
            "[TRAY] D-Bus check for StatusNotifierItem failed: %s, assuming SNI is unavailable.",
            exc,
        )
        return True
