"""#13: Tray menu construction — extracted from tray.py.

Concern mixing in tray.py: pystray icon lifecycle (start/run/stop/
set_state/notify) was tangled with menu building (_build_menu /
_build_models_submenu / _display_hotkey / _wrap). This module owns
the menu-building side; tray.py owns the lifecycle.

The menu structure:
  - Open App (default/bold action)
  - Start Dictation
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

#  tray-scoped menu builders (build_menu_for_tray,
# build_microphones_submenu, build_models_submenu, invalidate_menu_cache,
# maybe_publish_tray_menu) need the i18n ``_`` function to localize
# labels. Imported here (not inlined) so the same locale state is
# shared with tray.py's re-export — ``set_tray_locale`` mutates the
# module-level locale in tray_i18n and both modules see the update.
from voice_typer.server.tray_i18n import _

pystray = lazy_module("pystray")

log = logging.getLogger("voice_typer.server.tray_menu")


def display_hotkey(hotkey: str, fallback: str = "<caps_lock>") -> str:
    """Return the configured hotkey in user-facing form.

    #13: extracted from TrayIcon._display_hotkey so the formatting
    logic is testable without a TrayIcon instance.

    The fallback mirrors ``config.DEFAULT_HOTKEY`` ("<caps_lock>") —
    the canonical platform-independent default. The legacy "<f2>"
    fallback would display "F2" in tray tooltips/menus while the app
    actually bound Caps Lock.
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

     (fix): previously we re-raised ``SystemExit`` so the
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
    # tray hardcoded ``default=True`` on "Start Dictation", so left-click
    # ALWAYS started recording regardless of the Settings page choice.
    # Now this parameter controls which menu item gets ``default=True``.
    left_click_action: str = "open_app",
    # localization function
    localize: Callable[[str], str] = lambda k: k,
) -> tuple:
    """Build the minimal tray menu with Models submenu.

    #13: extracted from TrayIcon._build_menu. Returns a tuple of
    pystray.MenuItem suitable for passing to pystray.Menu(*items).

    Menu structure:
      - Open App (default/bold)
      - Start Dictation
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
        the "Start Dictation" label.
    toggle_dictation, open_app, restart_app, quit_app : Callable
        No-arg callbacks for each menu action. They will be wrapped
        with wrap_callback() so pystray's (icon, item) invocation
        convention doesn't break them.
    force_cancel_transcription : Callable, optional
         Finding #3: force-cancel a stuck transcription.  Invokes
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
    # Start Dictation is second
    items.append(
        pystray.MenuItem(
            f"{localize('toggle_dictation')} ({hotkey_label})",
            wrap_callback(toggle_dictation),
            default=dictation_default,
        )
    )

    #  Finding #3: Force-cancel stuck transcription (manual escape hatch)
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
    force_cancel_transcription: Callable[[], None] | None = None,
    is_transcribing: Callable[[], bool] = lambda: False,
    is_recording: Callable[[], bool] = lambda: False,
    restart_app: Callable[[], None],
    quit_app: Callable[[], None],
    build_models_submenu: Callable[[], list] = lambda: [],
    left_click_action: str = "open_app",
    microphones: list[dict] | None = None,
    active_mic_id: str | None = None,
    on_select_mic: Callable[[str], None] | None = None,
    on_refresh_mics: Callable[[], None] | None = None,
    on_open_settings: Callable[[], None] | None = None,
    on_open_history: Callable[[], None] | None = None,
    on_open_help: Callable[[], None] | None = None,
    localize: Callable[[str], str] = lambda k: k,
) -> tuple[list[dict], dict[str, Callable]]:
    """Build the tray menu MODEL (dicts) for the Tauri/sidecar host.

    Returns ``(model, id_map)`` where ``model`` is a list of item dicts
    and ``id_map`` maps every actionable item id to its callback.

    Mirrors the structure of :func:`build_menu_for_tray` (the pystray
    builder) so both runtimes render the same item set — single source
    of truth for the menu structure. Per C-TRAY-1 in AGENTS.md,
    no "re-paste last transcription" item is emitted on either
    runtime; the controller's re-paste method remains available to
    the renderer's Undo button but is NOT surfaced in the tray menu.

    Per the ``force_cancel`` item is only included when
    ``is_transcribing()`` is true. Per the microphones render as a
    submenu with ``mic:<id>`` ids (the active one carries
    ``checked=True``) plus a ``refresh_mics`` entry. Settings/History/
    Help quick shortcuts are wired via the ``on_open_*`` callbacks and
    mirror the pystray-side shortcuts that open the Electron window on
    the corresponding route.
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

    # Toggle/Stop Dictation — the label switches to "Stop Dictation"
    # while the app is actively recording so the user can see at a
    # glance that the next click will stop, not start. The item id
    # stays ``toggle_dictation`` so the host's click dispatcher is
    # unchanged (the action is the same; only the label differs).
    # ``is_recording`` is a callable so the host can re-query state
    # on every menu rebuild without the caller having to thread the
    # state through to this function.
    hotkey_label = display_hotkey(hotkey)
    dictation_key = "stop_dictation" if is_recording() else "toggle_dictation"
    items.append(
        _item(
            "toggle_dictation",
            f"{localize(dictation_key)} ({hotkey_label})",
            callback=toggle_dictation,
        )
    )

    # force-cancel only while transcribing.
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

    # microphones submenu (only when a mic list is supplied).
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

    # Settings / History / Help quick shortcuts. Each opens the
    # Electron window on the corresponding route — mirrors the
    # pystray-side builder so both runtimes expose the same shortcuts.
    if on_open_settings is not None:
        items.append(_item("settings", localize("settings"), callback=on_open_settings))
    if on_open_history is not None:
        items.append(_item("history", localize("history"), callback=on_open_history))
    if on_open_help is not None:
        items.append(_item("help", localize("help"), callback=on_open_help))

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


def publish_tray_state(
    *,
    icon: str | None = None,
    tooltip: str | None = None,
) -> bool:
    """Emit the ``tray_state`` event for the Tauri/sidecar host.

    ADR-0020 §6.5: the icon name + tooltip are only pushed to the event
    bus when running under the Tauri sidecar (``TAURI_SIDECAR=1``). On
    the Electron/pystray runtime this is a no-op — the pystray ``Icon``
    object is updated directly by ``TrayIcon._apply_state`` so emitting
    a parallel event would double-publish.

    The Tauri Rust host registers a ``tray_state`` listener in
    ``src-tauri/src/tray.rs::create_tray`` that calls ``tray.set_icon``
    + ``tray.set_tooltip`` with the payload. Without this event, the
    Tauri tray icon and tooltip stay frozen at their startup values
    regardless of recording/transcribing/error state.

    Args:
        icon: Logical icon name (``"idle"``, ``"recording"``,
            ``"transcribing"``, ``"error"``). The Rust host's
            ``load_tray_icon`` whitelists these four names; any other
            value is logged + dropped. ``None`` means "don't change
            the icon".
        tooltip: New tooltip string. ``None`` means "don't change
            the tooltip".

    Returns ``True`` if the event was published, ``False`` otherwise
    (including when both fields are ``None`` — there's nothing to
    update).
    """
    import os

    from voice_typer.server import event_bus

    if os.environ.get("TAURI_SIDECAR") != "1":
        return False
    payload: dict = {}
    if icon is not None:
        payload["icon"] = icon
    if tooltip is not None:
        payload["tooltip"] = tooltip
    if not payload:
        return False
    event_bus.publish({"type": "tray_state", "data": payload})
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  (Phase 4.5 spaghetti split): tray-scoped menu builders.
#
# These functions were previously inlined on the ``TrayIcon`` class
# (``_build_menu``, ``_build_microphones_submenu``, ``_build_models_submenu``,
# ``invalidate_menu_cache``, ``_maybe_publish_tray_menu``). They are
# extracted here as module-level functions that take a ``tray`` parameter
# (the ``TrayIcon`` instance) so the menu-building concern is colocated
# with the existing :func:`build_menu` / :func:`build_tray_menu_model`
# / :func:`publish_tray_menu` helpers.
#
# The ``TrayIcon`` class keeps one-line delegate methods for each so:
#   - tests that do ``monkeypatch.setattr("voice_typer.server.tray.TrayIcon.X", ...)``
#     still work (the symbol remains on the class).
#   - source-grep tests that scan ``tray.py`` for the method signatures
#     (e.g. ``tests/tauri/mig19/test_tray_menu.py::test_tray_py_build_models_submenu_method_present``)
#     still pass — the delegate keeps the exact signature.
#
# The lambdas captured by :func:`build_menu_for_tray` consult
# ``tray._open_page`` / ``tray.open_electron_window`` / etc. via
# attribute lookup at CALL TIME (not at capture time), so tests that
# do ``monkeypatch.setattr(tray, "_open_page", fake)`` before invoking
# the menu callback keep working.
# -----------------------------------------------------------------------------


def build_menu_for_tray(tray) -> tuple:
    """Build the tray menu with Models + Microphones submenus and quick shortcuts.

     extracted from ``TrayIcon._build_menu`` (which
    was a 117-line method). The body is unchanged — only the receiver
    changed from ``self`` to the ``tray`` parameter.

    Menu structure (///):
      - Open App (default/bold when ``tray_left_click_action == "open_app"``)
      - Start Dictation (default/bold when action == "toggle_dictation")
      - Undo Last                                  ()
      - Force Cancel Stuck Transcription           (, only when state == TRANSCRIBING)
      - --- separator ---
      - Models ▸
      - Microphones ▸                              ()
      - --- separator ---
      - Settings                                ()
      - History                                 ()
      - Help                                    ()
      - --- separator ---
      - Restart
      - Quit

    The menu is cached on the TrayIcon instance (``tray._cached_menu``)
    and only rebuilt when ``tray._menu_cache_valid`` is False (set by
    ``set_microphones`` / ``set_hotkey`` / ``refresh_config`` /
    ``invalidate_menu_cache`` and on TRANSCRIBING state transitions
    via ``set_state``).

    About, Diagnostics, and Show Last Notification have been removed
    from the tray menu (they remain available in the Electron app).
    """
    # serialize the check-then-build-then-cache sequence against
    # concurrent invalidate_menu_cache() calls (which set the flag False
    # and call tray._icon._update_menu()). Without the lock, a concurrent
    # invalidate can fire _update_menu() — DestroyMenu / CreatePopupMenu
    # on Windows — while this build is mid-flight, racing the HMENU
    # teardown against the tuple that _update_menu's caller is about to
    # walk. The lock also prevents two concurrent builds from both
    # writing tray._cached_menu.
    with tray._menu_lock:
        if tray._menu_cache_valid and tray._cached_menu is not None:
            return tray._cached_menu

        hotkey_str = tray._hotkey or getattr(tray._config, "hotkey", "<caps_lock>") or "<caps_lock>"
        hotkey_label = display_hotkey(hotkey_str)
        left_click = getattr(tray._config, "tray_left_click_action", "open_app") or "open_app"
        dictation_default = left_click == "toggle_dictation"
        open_app_default = left_click == "open_app"

        items: list = []

        # Open App (first; default/bold depends on left_click_action).
        items.append(
            pystray.MenuItem(
                _("open_app"),
                wrap_callback(tray.open_electron_window),
                default=open_app_default,
            )
        )
        # Toggle/Stop Dictation — the label switches to "Stop Dictation"
        # while the app is actively recording so the user can see at a
        # glance that the next click will stop, not start. The action
        # (controller.toggle_dictation) is unchanged; only the label
        # differs. ``tray._state`` is the canonical AppState enum from
        # tray_types; compared by identity to AppState.RECORDING.
        # Imported here once at the top of this block so the later
        # ``tray._state == AppState.TRANSCRIBING`` check below reuses
        # the same binding without a second local import.
        from voice_typer.server.tray_types import AppState

        dictation_key = "stop_dictation" if tray._state == AppState.RECORDING else "toggle_dictation"
        items.append(
            pystray.MenuItem(
                f"{_(dictation_key)} ({hotkey_label})",
                wrap_callback(tray._controller.toggle_dictation),
                default=dictation_default,
            )
        )
        # Force Cancel Stuck Transcription — only rendered while
        # transcribing so the menu isn't cluttered when nothing is stuck.
        # The lambda is created (closure over tray._controller.recording)
        # but NOT invoked during menu building, so a mock controller
        # without a ``recording`` attribute is safe.
        # Uses the canonical ``force_cancel_transcription`` key (single
        # canonical label across tray + renderer); the legacy
        # ``force_cancel_stuck_transcription`` key was removed from
        # ``tray_i18n.py``.
        # STATE-IMPORT: tray._state is the canonical AppState enum
        # from tray_types; compared by identity to AppState.TRANSCRIBING.
        # The import was hoisted to the Toggle/Stop Dictation block
        # above so this block reuses the same binding.
        if tray._state == AppState.TRANSCRIBING:
            items.append(
                pystray.MenuItem(
                    _("force_cancel_transcription"),
                    wrap_callback(
                        lambda: tray._controller.recording._force_recover_from_stuck_transcription(force=True)
                    ),
                )
            )

        items.append(pystray.Menu.SEPARATOR)

        # Models submenu — built by tray_models.build_models_menu_items
        # (invoked via tray._build_models_submenu delegate).
        models_sub = tray._build_models_submenu()
        items.append(pystray.MenuItem(_("models"), pystray.Menu(*models_sub)))
        # Microphones submenu — mirrors the Models submenu.
        mic_sub = tray._build_microphones_submenu()
        items.append(pystray.MenuItem(_("microphones"), pystray.Menu(*mic_sub)))

        items.append(pystray.Menu.SEPARATOR)

        # Settings / History / Help quick shortcuts. Each opens
        # the Electron window on the corresponding route via tray._open_page
        # (delegate to tray_window.open_page).
        for label_key, path in (
            ("settings", "/settings"),
            ("history", "/history"),
            ("help", "/about"),
        ):
            items.append(
                pystray.MenuItem(
                    _(label_key),
                    wrap_callback(lambda p=path: tray._open_page(p)),
                )
            )

        items.append(pystray.Menu.SEPARATOR)

        # Restart + Quit.
        items.append(pystray.MenuItem(_("restart"), wrap_callback(tray._controller.restart_app)))
        items.append(pystray.MenuItem(_("quit"), wrap_callback(tray._confirm_quit_while_recording)))

        result = tuple(items)
        tray._cached_menu = result
        tray._menu_cache_valid = True
        return result


def build_microphones_submenu(tray) -> list:
    """Build the Microphones ▸ submenu ().

     extracted from ``TrayIcon._build_microphones_submenu``.

    Renders one MenuItem per cached microphone (``tray._microphones``),
    marking the active device (matching ``tray._config.microphone``)
    with a native checkmark via pystray's ``checked=True`` parameter
    (Win32 MF_CHECKED / macOS NSControlStateValueOn / GTK radio
    active). A trailing ``More microphones...`` item opens the
    Settings page (where the user can pick a device or refresh the
    list).

    Returns an empty list only if ``tray._microphones`` is empty AND
    the ``More microphones...`` shortcut is somehow suppressed — in
    practice the shortcut is always appended so the submenu is never
    empty (the user can always reach the Settings page).
    """
    active_mic_id = str(getattr(tray._config, "microphone", None) or "")
    items: list = []
    for mic in tray._microphones:
        mic_id = str(mic.get("id", ""))
        mic_name = str(mic.get("name", mic_id)) or mic_id
        # Native checkmark via ``checked=callable``: previously the
        # active mic was prefixed with "• " (and non-active with ""),
        # which bypassed the platform checkmark, broke screen-reader
        # semantics, and misaligned with the Models submenu (which
        # also uses ``checked=``). pystray's MenuItem ``checked``
        # parameter renders the platform-standard checkmark, but it
        # MUST be a callable — pystray wraps it via
        # ``_assert_callable(checked, lambda _: None)`` and invokes it
        # as ``checked(item)`` at render time; a raw bool raises
        # ``ValueError`` at MenuItem construction (crashes the tray
        # at startup). The menu is rebuilt on every right-click via
        # invalidate_menu_cache, so the captured bool is fresh at
        # display time.
        is_active = mic_id == active_mic_id
        # Default-arg capture so each iteration's mic_id is bound
        # at lambda creation time (not lazily at call time).
        items.append(
            pystray.MenuItem(
                mic_name,
                wrap_callback(lambda _id=mic_id: tray._controller.change_microphone(_id)),
                checked=(lambda _item, _active=is_active: _active),
            )
        )
    if tray._microphones:
        items.append(pystray.Menu.SEPARATOR)
    items.append(
        pystray.MenuItem(
            _("more_microphones"),
            wrap_callback(lambda: tray._open_page("/settings")),
        )
    )
    return items


def build_models_submenu(tray) -> list:
    """Build a list of model MenuItems — only cached models + More models link.

     extracted from ``TrayIcon._build_models_submenu``.
    Delegates to :func:`tray_models.build_models_menu_items` for the
    actual item construction.

    previously the menu builder re-parsed config.json from
    disk, which is stale under rapid config updates. We now pass the
    in-memory Config object via a config_provider callable so the menu
    always reflects the live state.
    """
    from voice_typer.server.config import _config_dir
    from voice_typer.server.tray_models import build_models_menu_items

    # pass a config provider that returns the live Config
    # instance, so the menu doesn't read stale config.json from disk.
    config_provider = getattr(tray, "_config", None)
    return build_models_menu_items(
        _config_dir,
        tray._controller.change_model,
        wrap_callback,  # use the shared wrapper from tray_menu
        tray._open_models_page,  # use models-page callback (opens + navigates)
        config_provider=config_provider,
    )


def invalidate_menu_cache(tray) -> None:
    """Mark the menu cache as stale so it rebuilds on next right-click.

     extracted from ``TrayIcon.invalidate_menu_cache``.

    on Windows, pystray's ``_on_notify`` displays the menu via
    ``TrackPopupMenuEx`` with the STORED ``HMENU`` handle — it does NOT
    re-call the ``_build_menu`` callback on subsequent right-clicks because
    ``_update_menu()`` is only called during icon creation.  We must force
    pystray to rebuild its Win32 menu handle by calling
    ``_icon._update_menu()`` here, which triggers the ``_build_menu``
    callback and reads the latest config values.

    Thread safety: ``_update_menu()`` calls ``DestroyMenu`` /
    ``CreatePopupMenu`` / ``InsertMenuItem`` — Win32 API calls that are
    NOT guaranteed thread-safe when invoked concurrently with a
    ``build_menu_for_tray`` rebuild on another thread.  We hold
    ``tray._menu_lock`` across the ``_menu_cache_valid = False`` +
    ``_update_menu()`` pair so the rebuild is serialized against any
    concurrent ``build_menu_for_tray`` (which acquires the same lock
    around its check-then-build-then-cache sequence).  A concurrent
    right-click during the brief rebuild window simply shows the
    previous menu or nothing — the user can right-click again.
    ``maybe_publish_tray_menu`` is called OUTSIDE the lock because it
    builds an independent Tauri-side model (no shared mutable state
    with the cache) and should not block the menu lock on IPC I/O.
    """
    # serialize the flag-clear + _update_menu() pair against
    # concurrent build_menu_for_tray() (same lock).
    with tray._menu_lock:
        tray._menu_cache_valid = False
        # force pystray to rebuild its Win32 menu handle so the
        # next right-click reflects the current config state.
        if tray._icon is not None:
            try:
                tray._icon._update_menu()
            except Exception:
                log.debug("[TRAY] _icon._update_menu() failed", exc_info=True)
    # ADR-0020 §6.5: push serialized menu to Tauri sidecar host.
    maybe_publish_tray_menu(tray)


def maybe_publish_tray_menu(tray) -> bool:
    """ADR-0020 §6.5 / §16: push the serialized tray menu to the Tauri
    sidecar host (no-op on the Electron/pystray runtime).

     extracted from ``TrayIcon._maybe_publish_tray_menu``.

    Builds the model via :func:`build_tray_menu_model` (using the same
    controller callbacks as :func:`build_menu_for_tray`) and emits it
    through :func:`publish_tray_menu`, which guards on ``TAURI_SIDECAR``.
    Returns ``True`` if published.  Safe to call headless — never
    touches pystray.

    Note: under the Tauri runtime the pystray ``Icon`` is never created
    (the native tray is owned by the Rust host), so ``tray._icon`` is
    ``None``. The earlier ``if tray._icon is None: return False`` guard
    therefore short-circuited EVERY publish under Tauri — the
    ``tray_menu`` event never reached the Rust host and the tray menu
    stayed frozen at the empty placeholder. The guard is now removed;
    ``publish_tray_menu`` itself guards on ``TAURI_SIDECAR=1`` so the
    Electron runtime (where ``_icon`` IS set) is unaffected — the
    publish is a no-op there anyway.
    """
    controller = tray._controller
    if controller is None:
        return False

    hotkey = tray._hotkey or getattr(tray._config, "hotkey", "<caps_lock>") or "<caps_lock>"
    left_click = getattr(tray._config, "tray_left_click_action", "open_app") or "open_app"

    # detect attribute drift on the TrayController Protocol.
    # VoiceTyperApp._microphones is initialised to an empty list at
    # construction (app.py:338), so a None return from getattr here
    # means the attribute was renamed/removed — previously the
    # Microphones submenu would silently disappear with no log line.
    # The TrayController Protocol (tray_types.py) declares the typed
    # ``microphones`` attribute; until VoiceTyperApp exposes a public
    # ``microphones`` property (out of scope for this module), we keep
    # reading the private ``_microphones`` attribute but log a warning
    # when it's missing so the regression is visible in operator logs.
    controller_mics = getattr(controller, "_microphones", None)
    if controller_mics is None:
        log.warning(
            "controller has no _microphones attribute — microphones submenu "
            "disabled (class=%s). Update TrayController Protocol or %s to "
            "restore the submenu.",
            type(controller).__name__,
            type(controller).__name__,
        )

    model, _id_map = build_tray_menu_model(
        hotkey=hotkey,
        toggle_dictation=controller.toggle_dictation,
        open_app=tray.open_electron_window,
        force_cancel_transcription=lambda: controller.recording._force_recover_from_stuck_transcription(force=True),
        is_transcribing=lambda: (
            getattr(tray._state, "name", "") == "TRANSCRIBING" or getattr(tray._state, "value", "") == "TRANSCRIBING"
        ),
        # ``is_recording`` mirrors ``is_transcribing``: a callable so
        # the host can re-query state on every menu rebuild. The label
        # switches to "Stop Dictation" when the app is actively
        # recording. ``tray._state`` is an AppState enum; we accept
        # either the enum name or value for robustness against test
        # mocks that use a plain string instead of the enum.
        is_recording=lambda: (
            getattr(tray._state, "name", "") == "RECORDING" or getattr(tray._state, "value", "") == "RECORDING"
        ),
        restart_app=controller.restart_app,
        quit_app=tray._confirm_quit_while_recording,
        build_models_submenu=tray._build_models_submenu,
        left_click_action=left_click,
        microphones=controller_mics,
        # VoiceTyperApp now exposes ``active_microphone_id``
        # (property) and ``refresh_microphones`` (method) on the public
        # TrayController Protocol. We keep the defensive ``getattr(..., None)``
        # call sites ONLY for backward-compat with legacy test mocks in
        # tests/test_tray.py::_MockController that pre-date the Protocol
        # update (those test files are owned by a different agent batch
        # and cannot be updated here). In production, ``controller`` is
        # the live ``VoiceTyperApp`` instance and pyrefly verifies the
        # attribute via the ``TrayController`` Protocol — so the getattr
        # is a no-op on the production path and only falls back to None
        # on the legacy-mock test path.
        active_mic_id=getattr(controller, "active_microphone_id", None),
        on_select_mic=getattr(controller, "change_microphone", None),
        on_refresh_mics=getattr(controller, "refresh_microphones", None),
        # Settings/History/Help shortcuts — mirror the pystray-side
        # build_menu_for_tray wiring so both runtimes expose the same
        # quick shortcuts (previously MISSING on Tauri, leaving these
        # routes unreachable from the tray).
        on_open_settings=lambda: tray._open_page("/settings"),
        on_open_history=lambda: tray._open_page("/history"),
        on_open_help=lambda: tray._open_page("/about"),
        # Pass the i18n ``_`` function so the Tauri host receives
        # LOCALIZED labels (e.g. "Salir", "Beenden") instead of the
        # raw i18n keys (e.g. "quit", "restart"). Previously the
        # default ``localize=lambda k: k`` left the menu showing raw
        # keys to non-English users.
        localize=_,
    )
    tray._tray_id_map = _id_map
    return publish_tray_menu(model)


def dispatch_tray_action(tray, item_id: str) -> bool:
    """Dispatch a Tauri tray-click IPC to the registered callback.

    ADR-0020 §6.5 / §16: the Tauri Rust host emits a ``tray_click``
    IPC for every native menu item click; ``ipc_server.py`` calls
    this method with the item's ``id``. The ``id → callback`` map is
    populated by :func:`maybe_publish_tray_menu` on every menu
    publish, so this function simply looks up the id and invokes the
    registered callback.

    Returns ``True`` if the id was found and the callback was
    invoked, ``False`` if the id is unknown (the IPC layer turns a
    False return into a ``server.unknown_tray_item`` error envelope).
    Before the first menu publish, ``tray._tray_id_map`` is ``{}``
    (initialised in ``TrayIcon.__init__``, which also documents why
    the default is empty) so every click returns False — the Tauri
    host should publish the initial menu via ``_wrap_bg_work`` before
    any click can land.

    Callback exceptions are caught and logged so a single broken
    callback (e.g. a controller method that raises) doesn't take
    down the IPC server thread. The return value is still True on a
    known id — the click was *dispatched*, the callback's success is
    a separate concern (the renderer surfaces errors via toasts).
    """
    callback = tray._tray_id_map.get(item_id)
    if callback is None:
        return False
    try:
        callback()
    except Exception:
        log.warning(
            "[TRAY] dispatch_tray_action callback raised for item_id=%r",
            item_id,
            exc_info=True,
        )
    return True
