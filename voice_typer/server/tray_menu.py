"""#13: Tray menu construction — extracted from tray.py.

Concern mixing in tray.py: pystray icon lifecycle (start/run/stop/
set_state/notify) was tangled with menu building (_build_menu /
_build_models_submenu / _display_hotkey / _wrap). This module owns
the menu-building side; tray.py owns the lifecycle.

The menu structure:
  - Toggle Dictation (default action)
  - Open App
  - --- separator ---
  - Models ▸ (submenu built by tray_models.build_models_menu_items)
  - --- separator ---
  - Restart
  - Quit

Menu items are cached on the TrayIcon instance (via the controller
protocol's invalidate_menu_cache()) so we don't rebuild on every
right-click. The cache is invalidated only when the menu structure
actually changes (microphone list, autostart toggle, hotkey, etc.).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import pystray

from voice_typer.server.tray_hotkey import format_hotkey_label

log = logging.getLogger("voice_typer.server.tray_menu")


def display_hotkey(hotkey: str, fallback: str = "<f2>") -> str:
    """Return the configured hotkey in user-facing form.

    #13: extracted from TrayIcon._display_hotkey so the formatting
    logic is testable without a TrayIcon instance.
    """
    h = hotkey or fallback
    return format_hotkey_label(h)


def wrap_callback(fn: Callable[[], None]) -> Callable:
    """Wrap a no-arg callback so pystray doesn't break on extra args.

    #13: extracted from TrayIcon._wrap so the wrapper logic is testable
    without a TrayIcon instance.

    RELIABILITY-001: previously this wrapper silently swallowed
    ``SystemExit``, which forced ``quit_app`` and ``restart_app``
    to use ``os._exit(0)`` to actually terminate the process.
    That bypassed Python cleanup (atexit, ``__del__``, ``finally``)
    and leaked the Win32 mutex, PortAudio handles, and
    ``RegisterHotKey`` registrations until the OS reaped them.

    We now log and re-raise ``SystemExit`` so the process can exit
    cleanly via the normal ``sys.exit(0)`` path.  ``self.quit()``
    (called by ``quit_app``) and ``restart_app`` both call
    ``self.tray.stop()`` before raising ``SystemExit``, which
    breaks the pystray event loop so ``_icon.run()`` returns and
    the main thread can exit.
    """
    def wrapper(icon, item):
        try:
            fn()
        except SystemExit as _se:
            log.info(
                "[TRAY] callback %r raised SystemExit(%s); re-raising",
                getattr(fn, "__name__", "<lambda>"), _se.code,
            )
            raise
    return wrapper


def build_menu(
    *,
    hotkey: str,
    toggle_dictation: Callable[[], None],
    open_app: Callable[[], None],
    restart_app: Callable[[], None],
    quit_app: Callable[[], None],
    build_models_submenu: Callable[[], list],
) -> tuple:
    """Build the Phase 2 minimal tray menu with Models submenu.

    #13: extracted from TrayIcon._build_menu. Returns a tuple of
    pystray.MenuItem suitable for passing to pystray.Menu(*items).

    Parameters
    ----------
    hotkey : str
        The hotkey string in pynput format (e.g. '<f2>'), used for
        the "Toggle Dictation" label.
    toggle_dictation, open_app, restart_app, quit_app : Callable
        No-arg callbacks for each menu action. They will be wrapped
        with wrap_callback() so pystray's (icon, item) invocation
        convention doesn't break them.
    build_models_submenu : Callable[[], list]
        Returns the list of MenuItem for the Models submenu. Delegated
        to the caller because it depends on TrayIcon's controller +
        open_electron_window methods (kept in tray.py).
    """
    items = []
    hotkey_label = display_hotkey(hotkey)

    items.append(
        pystray.MenuItem(
            f"Toggle Dictation ({hotkey_label})",
            wrap_callback(toggle_dictation),
            default=True,
        )
    )
    items.append(
        pystray.MenuItem(
            "Open App",
            wrap_callback(open_app),
        )
    )

    items.append(pystray.Menu.SEPARATOR)

    # Models submenu — only show downloaded models
    models_sub = build_models_submenu()
    items.append(pystray.MenuItem("Models", pystray.Menu(*models_sub)))

    items.append(pystray.Menu.SEPARATOR)

    # Restart
    items.append(pystray.MenuItem("Restart", wrap_callback(restart_app)))

    # Quit
    items.append(pystray.MenuItem("Quit", wrap_callback(quit_app)))

    return tuple(items)
