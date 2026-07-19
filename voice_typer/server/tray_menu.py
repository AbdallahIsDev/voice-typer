"""#13: Tray menu construction — extracted from tray.py.

Concern mixing in tray.py: pystray icon lifecycle (start/run/stop/
set_state/notify) was tangled with menu building (_build_menu /
_build_models_submenu / _display_hotkey / _wrap). This module owns
the menu-building side; tray.py owns the lifecycle.

The menu structure:
  - Open App (default/bold action)
  - Toggle Dictation
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
from collections.abc import Callable

# PERF-COLDSTART-001: lazy import — pystray's xorg backend calls
# Xlib.display.Display() at module import time, costing ~48 ms and
# failing without an X display (headless CI). pystray is only needed
# when build_menu() actually constructs menu items, so defer it. The
# proxy re-reads sys.modules on every access, so tests that inject a
# mock via monkeypatch.setitem(sys.modules, "pystray", ...) — or that
# assign tray_menu.pystray directly — keep working unchanged.
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.tray_hotkey import format_hotkey_label

pystray = lazy_module("pystray")

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

    ERR-QUIT-002 (fix): previously we re-raised ``SystemExit`` so the
    process could exit. But pystray's dispatcher catches the re-raised
    ``SystemExit`` and prints a full traceback ("An error occurred
    when calling message handler"), which is noisy and confusing.
    Since ``quit()`` and ``restart_app`` both call ``self.tray.stop()``
    before raising ``SystemExit``, the pystray event loop is already
    broken — we don't need to re-raise. Just suppress the ``SystemExit``
    and return normally; pystray sees a clean return and its loop
    exits because ``stop()`` was called.
    """

    def wrapper(icon, item):
        try:
            fn()
        except SystemExit as _se:
            # QUIT-CLEAN-001: this is the expected exit path for
            # ``quit_app`` and ``restart_app`` — ``tray.stop()`` was
            # already called inside the callback, so the pystray loop
            # is winding down.  Log at DEBUG so the user only sees
            # ``[QUIT] Quitting Voice Typer...`` and ``[SHUTDOWN]
            # Shutdown complete, exiting`` during a normal quit; the
            # ``SystemExit(...) suppressing`` line is internal
            # bookkeeping that previously polluted INFO-level output.
            log.debug("[TRAY] Quit handler completed, pystray loop will exit")
            # Do NOT re-raise — tray.stop() inside quit()/restart_app()
            # already broke the pystray event loop. Re-raising causes
            # pystray to print a confusing "error" traceback.

    return wrapper


def build_menu(
    *,
    hotkey: str,
    toggle_dictation: Callable[[], None],
    open_app: Callable[[], None],
    force_cancel_transcription: Callable[[], None] | None = None,
    restart_app: Callable[[], None],
    quit_app: Callable[[], None],
    build_models_submenu: Callable[[], list],
    # BUGFIX: tray_left_click_action was never read from config — the
    # tray hardcoded ``default=True`` on "Toggle Dictation", so left-click
    # ALWAYS started recording regardless of the Settings page choice.
    # Now this parameter controls which menu item gets ``default=True``.
    left_click_action: str = "open_app",
    # TRAY-008: localization function
    localize: Callable[[str], str] = lambda k: k,
) -> tuple:
    """Build the minimal tray menu with Models submenu.

    #13: extracted from TrayIcon._build_menu. Returns a tuple of
    pystray.MenuItem suitable for passing to pystray.Menu(*items).

    Menu structure:
      - Open App (default/bold)
      - Toggle Dictation
      - --- separator ---
      - Models ▸
      - --- separator ---
      - Restart
      - Quit

    About and Diagnostics are no longer in the tray menu; they remain
    available inside the main application (Electron window).

    Parameters
    ----------
    hotkey : str
        The hotkey string in pynput format (e.g. '<f2>'), used for
        the "Toggle Dictation" label.
    toggle_dictation, open_app, restart_app, quit_app : Callable
        No-arg callbacks for each menu action. They will be wrapped
        with wrap_callback() so pystray's (icon, item) invocation
        convention doesn't break them.
    force_cancel_transcription : Callable, optional
        PR-2 Finding #3: force-cancel a stuck transcription.  Invokes
        ``_force_recover_from_stuck_transcription(force=True)`` to
        reset busy flag and tray state immediately.  Safe to call
        when transcription is not stuck (no-op).
    build_models_submenu : Callable[[], list]
        Returns the list of MenuItem for the Models submenu. Delegated
        to the caller because it depends on TrayIcon's controller +
        open_electron_window methods (kept in tray.py).
    left_click_action : str
        Controls which menu item gets ``default=True`` (the left-click
        action). "open_app" (default) opens/focuses the Electron window;
        "toggle_dictation" starts/stops recording.
    """
    items = []
    hotkey_label = display_hotkey(hotkey)

    dictation_default = left_click_action == "toggle_dictation"
    open_app_default = left_click_action == "open_app"

    # Open App is first (default/bold action)
    items.append(
        pystray.MenuItem(
            localize("open_app"),
            wrap_callback(open_app),
            default=open_app_default,
        )
    )
    # Toggle Dictation is second
    items.append(
        pystray.MenuItem(
            f"{localize('toggle_dictation')} ({hotkey_label})",
            wrap_callback(toggle_dictation),
            default=dictation_default,
        )
    )

    # PR-2 Finding #3: Force-cancel stuck transcription (manual escape hatch)
    if force_cancel_transcription is not None:
        items.append(
            pystray.MenuItem(
                localize("force_cancel_transcription"),
                wrap_callback(force_cancel_transcription),
            )
        )

    items.append(pystray.Menu.SEPARATOR)

    # Models submenu — only show downloaded models
    models_sub = build_models_submenu()
    items.append(pystray.MenuItem(localize("models"), pystray.Menu(*models_sub)))

    items.append(pystray.Menu.SEPARATOR)

    # Restart
    items.append(pystray.MenuItem(localize("restart"), wrap_callback(restart_app)))

    # Quit
    items.append(pystray.MenuItem(localize("quit"), wrap_callback(quit_app)))

    return tuple(items)


# ─────────────────────────────────────────────────────────────────────────────
# ADR-0020 §6.5 / §16: Tauri tray-menu MODEL builder.
#
# This is the Tauri/sidecar counterpart to ``build_menu`` above. Instead of
# pystray ``MenuItem`` objects (which require a display), it returns plain
# dicts that the Tauri host can render directly, plus an ``id`` → callback
# map used to dispatch a click back to the right action.  It never imports
# or touches pystray, so it is safe to call headless (e.g. in tests or on
# the Tauri runtime).
#
# Each model item dict has exactly the keys the host expects:
#     {id, label, disabled, separator, checked, submenu}
# Separators use ``id=""`` and ``label=""``.  ``checked``/``submenu`` are
# Optional (``None`` when absent).
# -----------------------------------------------------------------------------


def build_tray_menu_model(
    *,
    hotkey: str,
    toggle_dictation: Callable[[], None],
    open_app: Callable[[], None],
    repaste_last: Callable[[], None],
    force_cancel_transcription: Callable[[], None] | None = None,
    is_transcribing: Callable[[], bool] = lambda: False,
    restart_app: Callable[[], None],
    quit_app: Callable[[], None],
    build_models_submenu: Callable[[], list] = lambda: [],
    left_click_action: str = "open_app",
    microphones: list[dict] | None = None,
    active_mic_id: str | None = None,
    on_select_mic: Callable[[str], None] | None = None,
    on_refresh_mics: Callable[[], None] | None = None,
    localize: Callable[[str], str] = lambda k: k,
) -> tuple[list[dict], dict[str, Callable]]:
    """Build the tray menu MODEL (dicts) for the Tauri/sidecar host.

    Returns ``(model, id_map)`` where ``model`` is a list of item dicts
    and ``id_map`` maps every actionable item id to its callback.

    Mirrors the structure of :func:`build_menu` but produces serialisable
    dicts instead of pystray objects.  Per UX-3 the ``force_cancel`` item
    is only included when ``is_transcribing()`` is true.  Per UX-2 the
    microphones render as a submenu with ``mic:<id>`` ids (the active one
    carries ``checked=True``) plus a ``refresh_mics`` entry.
    """
    id_map: dict[str, Callable] = {}
    items: list[dict] = []

    def _item(
        item_id: str,
        label: str,
        *,
        callback: Callable[[], None] | None = None,
        disabled: bool = False,
        checked: bool | None = None,
        submenu: list[dict] | None = None,
    ) -> dict:
        if callback is not None:
            id_map[item_id] = callback
        return {
            "id": item_id,
            "label": label,
            "disabled": disabled,
            "separator": False,
            "checked": checked,
            "submenu": submenu,
        }

    def _sep() -> dict:
        return {
            "id": "",
            "label": "",
            "disabled": False,
            "separator": True,
            "checked": None,
            "submenu": None,
        }

    # Open App (default/bold action depends on left_click_action).
    items.append(_item("open_app", localize("open_app"), callback=open_app))

    # Toggle Dictation (with hotkey hint in the label).
    hotkey_label = display_hotkey(hotkey)
    items.append(
        _item(
            "toggle_dictation",
            f"{localize('toggle_dictation')} ({hotkey_label})",
            callback=toggle_dictation,
        )
    )

    # Repaste last transcription.
    items.append(_item("repaste_last", localize("repaste_last"), callback=repaste_last))

    # UX-3: force-cancel only while transcribing.
    if force_cancel_transcription is not None and is_transcribing():
        items.append(
            _item(
                "force_cancel_transcription",
                localize("force_cancel_transcription"),
                callback=force_cancel_transcription,
            )
        )

    items.append(_sep())

    # Models submenu.
    models_sub: list[dict] = []
    for m in build_models_submenu():
        # build_models_submenu returns pystray MenuItems in the pystray path;
        # for the model path we rebuild lightweight dicts from the same data
        # by calling the submenu builder's textual form.  To keep this path
        # self-contained and display-free, we derive dicts from the returned
        # pystray items' text + a stable id.
        text = getattr(m, "text", None)
        label = text() if callable(text) else str(m)
        models_sub.append(_item(f"model:{label}", label))
    items.append(_item("models", localize("models"), submenu=models_sub))

    # UX-2: microphones submenu (only when a mic list is supplied).
    if microphones:
        mic_sub: list[dict] = []
        for mic in microphones:
            mic_id = str(mic.get("id", ""))
            mic_name = str(mic.get("name", mic_id))
            mic_sub.append(
                _item(
                    f"mic:{mic_id}",
                    mic_name,
                    callback=(lambda _id=mic_id: on_select_mic(_id)) if on_select_mic else None,
                    checked=(active_mic_id is not None and mic_id == str(active_mic_id)),
                )
            )
        if on_refresh_mics is not None:
            mic_sub.append(_item("refresh_mics", localize("refresh_mics"), callback=on_refresh_mics))
        items.append(_item("microphones", localize("microphones"), submenu=mic_sub))

    items.append(_sep())

    # Restart + Quit.
    items.append(_item("restart", localize("restart"), callback=restart_app))
    items.append(_item("quit", localize("quit"), callback=quit_app))

    return items, id_map


def publish_tray_menu(model: list[dict]) -> bool:
    """Emit the ``tray_menu`` event for the Tauri/sidecar host.

    ADR-0020 §6.5 / §16: the serialized menu model is only pushed to the
    event bus when running under the Tauri sidecar (``TAURI_SIDECAR=1``).
    On the Electron/pystray runtime this is a no-op so the native pystray
    menu (built by :func:`build_menu`) remains the single source of truth
    and we never double-publish.

    Returns ``True`` if the event was published, ``False`` otherwise.
    """
    import os

    from voice_typer.server import event_bus

    if os.environ.get("TAURI_SIDECAR") != "1":
        return False
    event_bus.publish({"type": "tray_menu", "data": {"items": model}})
    return True
