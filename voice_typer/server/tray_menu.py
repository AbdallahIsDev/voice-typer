"""Tray menu construction and action handlers."""

from __future__ import annotations

import logging
from collections.abc import Callable

# Xlib.display.Display() at module import time, costing ~48 ms and
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.tray_hotkey import format_hotkey_label

#  tray-scoped menu builders (build_menu_for_tray,
from voice_typer.server.tray_i18n import _

# Shared Models-submenu label helpers (tray_models has no heavy imports
from voice_typer.server.tray_models import _menu_label, more_models_label

pystray = lazy_module("pystray")

log = logging.getLogger("voice_typer.server.tray_menu")


def display_hotkey(hotkey: str, fallback: str = "<caps_lock>") -> str:
    """Return the configured hotkey in user-facing form."""
    h = hotkey or fallback
    return format_hotkey_label(h)


def wrap_callback(fn: Callable[[], None]) -> Callable:
    """Wrap a no-arg callback so pystray doesn't break on extra args."""

    def wrapper(icon, item):
        try:
            fn()
        except SystemExit as _se:
            # The quit path is the expected exit route from the tray:
            log.debug("[TRAY] Quit handler completed, pystray loop will exit")
            # Do NOT re-raise, tray.stop() inside quit()/restart_app()

    return wrapper


# ADR-0020 §6.5 / §16: Tauri tray-menu MODEL builder.


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
    # Models submenu DATA provider: returns a list of
    build_models_submenu_data: Callable[[], list] = lambda: [],
    on_open_models: Callable[[], None] | None = None,
    left_click_action: str = "open_app",
    microphones: list[dict] | None = None,
    active_mic_id: str | None = None,
    on_select_mic: Callable[[str], None] | None = None,
    on_refresh_mics: Callable[[], None] | None = None,
    on_open_microphones: Callable[[], None] | None = None,
    on_open_settings: Callable[[], None] | None = None,
    on_open_history: Callable[[], None] | None = None,
    on_open_help: Callable[[], None] | None = None,
    localize: Callable[[str], str] = lambda k: k,
) -> tuple[list[dict], dict[str, Callable]]:
    """of truth for the menu structure. Per C-TRAY-1 in AGENTS.md,"""
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
        accelerator: str | None = None,
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
            "accelerator": accelerator,
        }

    def _sep() -> dict:
        return {
            "id": "",
            "label": "",
            "disabled": False,
            "separator": True,
            "checked": None,
            "submenu": None,
            "accelerator": None,
        }

    # Open App (default/bold action depends on left_click_action).
    items.append(_item("open_app", localize("open_app"), callback=open_app))

    # Toggle/Stop Dictation, the label switches to "Stop Dictation"
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

    # Models submenu, built from the DATA layer
    models_sub: list[dict] = []
    for row in build_models_submenu_data():
        name, downloaded, is_active, change_fn = row
        if not downloaded:
            continue
        # ``checked`` mirrors the pystray path: active row True, other
        models_sub.append(
            _item(
                f"model:{name}",
                _menu_label(name),
                callback=change_fn,
                checked=is_active,
            )
        )
    # Separator ONLY between real model rows and the trailing
    if models_sub:
        models_sub.append(_sep())
    if on_open_models is not None:
        models_sub.append(_item("more_models", more_models_label(localize), callback=on_open_models))
    items.append(_item("models", localize("models"), submenu=models_sub))

    # Microphones submenu, the parent is ALWAYS rendered:
    mic_sub: list[dict] = []
    for mic in microphones or []:
        mic_id = str(mic.get("id", ""))
        # Same empty-name fallback as the pystray path below (line ~707):
        mic_name = str(mic.get("name", mic_id)) or mic_id
        mic_sub.append(
            _item(
                f"mic:{mic_id}",
                mic_name,
                callback=(lambda _id=mic_id: on_select_mic(_id)) if on_select_mic else None,
                checked=(active_mic_id is not None and mic_id == str(active_mic_id)),
            )
        )
    # Separator between device rows and the trailing actions, never
    if mic_sub:
        mic_sub.append(_sep())
    if on_refresh_mics is not None:
        mic_sub.append(_item("refresh_mics", localize("refresh_mics"), callback=on_refresh_mics))
    if on_open_microphones is not None:
        mic_sub.append(_item("more_microphones", localize("more_microphones"), callback=on_open_microphones))
    if mic_sub:
        items.append(_item("microphones", localize("microphones"), submenu=mic_sub))

    items.append(_sep())

    # Settings / History / Help quick shortcuts. Each opens the
    if on_open_settings is not None:
        items.append(_item("settings", localize("settings"), callback=on_open_settings))
    if on_open_history is not None:
        items.append(_item("history", localize("history"), callback=on_open_history))
    if on_open_help is not None:
        items.append(_item("help", localize("help"), callback=on_open_help))

    items.append(_sep())

    # Restart + Quit. Quit carries the conventional CmdOrCtrl+Q key
    # equivalent so the OS renders the hint (Tauri grammar, display-only;
    # not global wiring — see MenuItemData.accelerator docs in menu.rs).
    items.append(_item("restart", localize("restart"), callback=restart_app))
    items.append(_item("quit", localize("quit"), callback=quit_app, accelerator="CmdOrCtrl+Q"))

    return items, id_map


def publish_tray_menu(model: list[dict]) -> bool:
    """Emit the ``tray_menu`` event for the Tauri/sidecar host."""
    from voice_typer.server import event_bus
    from voice_typer.server.tray_types import is_tauri_sidecar

    if not is_tauri_sidecar():
        return False
    event_bus.publish({"type": "tray_menu", "data": {"items": model}})
    return True


def publish_tray_state(
    *,
    icon: str | None = None,
    tooltip: str | None = None,
) -> bool:
    """Emit the ``tray_state`` event for the Tauri/sidecar host.
    The Tauri Rust host registers a ``tray_state`` listener in
    """
    from voice_typer.server import event_bus
    from voice_typer.server.tray_types import is_tauri_sidecar

    if not is_tauri_sidecar():
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


# (``_build_menu``, ``_build_microphones_submenu``, ``_build_models_submenu``,


def build_menu_for_tray(tray) -> tuple:
    """Build the tray menu with Models + Microphones submenus and quick shortcuts."""
    # serialize the check-then-build-then-cache sequence against
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
                wrap_callback(tray.open_app_window),
                default=open_app_default,
            )
        )
        # Toggle/Stop Dictation, the label switches to "Stop Dictation"
        from voice_typer.server.tray_types import AppState

        dictation_key = "stop_dictation" if tray._state == AppState.RECORDING else "toggle_dictation"
        items.append(
            pystray.MenuItem(
                f"{_(dictation_key)} ({hotkey_label})",
                wrap_callback(tray._controller.toggle_dictation),
                default=dictation_default,
            )
        )
        # Force Cancel Stuck Transcription, only rendered while
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

        # Models submenu, built by tray_models.build_models_menu_items
        models_sub = tray._build_models_submenu()
        items.append(pystray.MenuItem(_("models"), pystray.Menu(*models_sub)))
        # Microphones submenu, mirrors the Models submenu.
        mic_sub = tray._build_microphones_submenu()
        items.append(pystray.MenuItem(_("microphones"), pystray.Menu(*mic_sub)))

        items.append(pystray.Menu.SEPARATOR)

        # Settings / History / Help quick shortcuts. Each opens
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

    Returns an empty list only if ``tray._microphones`` is empty AND
    """
    active_mic_id = str(getattr(tray._config, "microphone", None) or "")
    items: list = []
    for mic in tray._microphones:
        mic_id = str(mic.get("id", ""))
        mic_name = str(mic.get("name", mic_id)) or mic_id
        # Native checkmark via ``checked=callable``: previously the
        is_active = mic_id == active_mic_id
        # Default-arg capture so each iteration's mic_id is bound
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
    """Build a list of model MenuItems, only cached models + More models link."""
    from voice_typer.server.config import _config_dir
    from voice_typer.server.tray_models import build_models_menu_items

    # pass a config provider that returns the live Config
    config_provider = getattr(tray, "_config", None)
    return build_models_menu_items(
        _config_dir,
        tray._controller.change_model,
        wrap_callback,
        tray._open_models_page,
        config_provider=config_provider,
    )


def invalidate_menu_cache(tray) -> None:
    """Mark the menu cache as stale so it rebuilds on next right-click."""
    # serialize the flag-clear + _update_menu() pair against
    with tray._menu_lock:
        tray._menu_cache_valid = False
        # force pystray to rebuild its Win32 menu handle so the
        if tray._icon is not None:
            try:
                tray._icon._update_menu()
            except Exception:
                log.debug("[TRAY] _icon._update_menu() failed", exc_info=True)
    # ADR-0020 §6.5: push serialized menu to Tauri sidecar host.
    maybe_publish_tray_menu(tray)


def _models_submenu_data(tray, controller) -> list:
    """Return the Models-submenu DATA rows for the Tauri dict path."""
    from voice_typer.server.config import _config_dir
    from voice_typer.server.tray_models import build_models_submenu_data

    # Prefer the in-memory Config (falls back to a disk read when the
    config_provider = getattr(tray, "_config", None)
    return build_models_submenu_data(
        _config_dir,
        controller.change_model,
        config_provider=config_provider,
    )


def maybe_publish_tray_menu(tray) -> bool:
    """``tray_menu`` event never reached the Rust host and the tray menu"""
    controller = tray._controller
    if controller is None:
        return False

    hotkey = tray._hotkey or getattr(tray._config, "hotkey", "<caps_lock>") or "<caps_lock>"
    left_click = getattr(tray._config, "tray_left_click_action", "open_app") or "open_app"

    # detect attribute drift on the TrayController Protocol.
    controller_mics = getattr(controller, "_microphones", None)
    if controller_mics is None:
        log.warning(
            "controller has no _microphones attribute, microphones submenu "
            "disabled (class=%s). Update TrayController Protocol or %s to "
            "restore the submenu.",
            type(controller).__name__,
            type(controller).__name__,
        )

    model, _id_map = build_tray_menu_model(
        hotkey=hotkey,
        toggle_dictation=controller.toggle_dictation,
        open_app=tray.open_app_window,
        force_cancel_transcription=lambda: controller.recording._force_recover_from_stuck_transcription(force=True),
        is_transcribing=lambda: (
            getattr(tray._state, "name", "") == "TRANSCRIBING" or getattr(tray._state, "value", "") == "TRANSCRIBING"
        ),
        # ``is_recording`` mirrors ``is_transcribing``: a callable so
        is_recording=lambda: (
            getattr(tray._state, "name", "") == "RECORDING" or getattr(tray._state, "value", "") == "RECORDING"
        ),
        restart_app=controller.restart_app,
        quit_app=tray._confirm_quit_while_recording,
        # Models submenu: consume the shared DATA layer directly, the
        build_models_submenu_data=lambda: _models_submenu_data(tray, controller),
        # "More models..." opens the app window AND navigates to the
        on_open_models=tray._open_models_page,
        left_click_action=left_click,
        microphones=controller_mics,
        # TrayController Protocol. We keep the defensive ``getattr(..., None)``
        active_mic_id=getattr(controller, "active_microphone_id", None),
        on_select_mic=getattr(controller, "change_microphone", None),
        on_refresh_mics=getattr(controller, "refresh_microphones", None),
        # "More microphones..." opens the app window AND navigates to
        on_open_microphones=tray._open_microphones_page,
        # Settings/History/Help shortcuts, mirror the pystray-side
        on_open_settings=lambda: tray._open_page("/settings"),
        on_open_history=lambda: tray._open_page("/history"),
        on_open_help=lambda: tray._open_page("/about"),
        # Pass the i18n ``_`` function so the Tauri host receives
        localize=_,
    )
    tray._tray_id_map = _id_map
    return publish_tray_menu(model)


def dispatch_tray_action(tray, item_id: str) -> bool:
    """Dispatch a Tauri tray-click IPC to the registered callback.

    Returns ``True`` if the id was found and the callback was
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
