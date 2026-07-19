"""Tests for the ADR-0020 §6.5 / §16 Tauri tray-menu glue.

Covers:
- ``build_tray_menu_model`` produces well-formed MenuItem dicts (no pystray
  import required for the model path — ``TAURI_SIDECAR`` guarded).
- ``publish_tray_menu`` emits a ``tray_menu`` event only under
  ``TAURI_SIDECAR=1`` (Electron/pystray path untouched).
- ``tray_click`` command dispatches to the correct action by id and returns
  ``unknown_tray_item`` for unknown ids.

These tests run without a display: the model builder never touches
pystray at runtime, and the ``tray_click`` dispatch goes through the
id→callback map, not the real pystray menu.
"""

from __future__ import annotations

from voice_typer.server import event_bus
from voice_typer.server.tray_menu import build_tray_menu_model


def _noop() -> None:
    pass


def _make_model(**overrides):
    """Build a tray menu model with sensible defaults."""
    kwargs = {
        "hotkey": "<f2>",
        "toggle_dictation": _noop,
        "open_app": _noop,
        "repaste_last": _noop,
        "force_cancel_transcription": _noop,
        "is_transcribing": lambda: False,
        "restart_app": _noop,
        "quit_app": _noop,
        "build_models_submenu": lambda: [],
        "left_click_action": "open_app",
        "microphones": None,
        "active_mic_id": None,
        "on_select_mic": None,
        "on_refresh_mics": None,
    }
    kwargs.update(overrides)
    return build_tray_menu_model(**kwargs)


def test_build_tray_menu_model_returns_well_formed_items():
    """Every item is a valid MenuItem dict; separators use the empty form."""
    model, id_map = _make_model()

    assert isinstance(model, list)
    assert len(model) > 0

    for item in model:
        assert set(item.keys()) == {
            "id",
            "label",
            "disabled",
            "separator",
            "checked",
            "submenu",
        }, item
        assert isinstance(item["id"], str)
        assert isinstance(item["label"], str)
        assert isinstance(item["disabled"], bool)
        assert isinstance(item["separator"], bool)
        # checked is Optional[bool]; submenu Optional[list].
        assert item["checked"] is None or isinstance(item["checked"], bool)
        assert item["submenu"] is None or isinstance(item["submenu"], list)
        if item["separator"]:
            assert item["id"] == ""
            assert item["label"] == ""


def test_build_tray_menu_model_top_level_ids_present():
    """Stable top-level ids the host relies on are present."""
    model, id_map = _make_model()

    ids = {item["id"] for item in model if not item["separator"]}
    for expected in ("open_app", "toggle_dictation", "repaste_last", "restart", "quit"):
        assert expected in ids, f"missing tray id {expected}"

    # id_map maps every actionable id to a callable.
    assert "open_app" in id_map
    assert callable(id_map["open_app"])
    assert "toggle_dictation" in id_map


def test_build_tray_menu_model_force_cancel_only_when_transcribing():
    """UX-3: the force-cancel item is gated by is_transcribing()."""
    hidden, _ = _make_model(is_transcribing=lambda: False)
    shown, _ = _make_model(is_transcribing=lambda: True)

    hidden_ids = {i["id"] for i in hidden if not i["separator"]}
    shown_ids = {i["id"] for i in shown if not i["separator"]}

    assert "force_cancel_transcription" not in hidden_ids
    assert "force_cancel_transcription" in shown_ids


def test_build_tray_menu_model_microphones_submenu():
    """UX-2: microphones render as a submenu with ``mic:<id>`` ids."""
    mics = [{"id": "0", "name": "Default"}, {"id": "1", "name": "USB Mic"}]
    model, id_map = _make_model(
        microphones=mics,
        active_mic_id="1",
        on_select_mic=_noop,
        on_refresh_mics=_noop,
    )

    mic_item = next(i for i in model if i["id"] == "microphones")
    assert mic_item["submenu"] is not None
    sub_ids = {i["id"] for i in mic_item["submenu"] if not i["separator"]}
    assert "mic:0" in sub_ids
    assert "mic:1" in sub_ids
    assert "refresh_mics" in sub_ids

    # The active mic carries checked=True; the other False.
    sub_by_id = {i["id"]: i for i in mic_item["submenu"]}
    assert sub_by_id["mic:1"]["checked"] is True
    assert sub_by_id["mic:0"]["checked"] is False
    assert "mic:1" in id_map


def test_publish_tray_menu_guarded_by_tauri_sidecar(monkeypatch):
    """The ``tray_menu`` event is emitted ONLY under TAURI_SIDECAR=1."""
    import voice_typer.server.tray as tray_mod

    # Build a minimal TrayIcon-like object with the publish hook.
    captured = []

    class _FakeTray(tray_mod.TrayIcon):
        def __init__(self):
            # Bypass the real __init__ (which touches pystray-free state only,
            # but we stub anyway to stay display-free).
            self._config = None
            self._controller = None
            self._state = None
            self._icon = object()  # non-None so publish proceeds
            self._menu_cache_valid = False
            self._tray_id_map = {}
            self._hotkey = "<f2>"

        def _maybe_publish_tray_menu(self):
            tray_mod.TrayIcon._maybe_publish_tray_menu(self)

    # Subscribe to event_bus and capture tray_menu publishes.
    def _on_event(event):
        if event.get("type") == "tray_menu":
            captured.append(event)

    event_bus.subscribe(_on_event)
    try:
        # 1. Without the env var: no event.
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        fake = _FakeTray()
        fake._maybe_publish_tray_menu()
        assert captured == [], "tray_menu must NOT publish without TAURI_SIDECAR"

        # 2. With the env var + a real controller: event emitted.
        monkeypatch.setenv("TAURI_SIDECAR", "1")

        class _Ctrl:
            def toggle_dictation(self):
                pass

            def restart_app(self):
                pass

            def change_microphone(self, _mic):
                pass

            def change_model(self, _name):
                pass

            def repaste_last(self):
                pass

            def refresh_microphones(self):
                pass

            _microphones = []

        class _FakeTray2(_FakeTray):
            def __init__(self):
                super().__init__()
                self._controller = _Ctrl()

        fake2 = _FakeTray2()
        fake2._maybe_publish_tray_menu()
        assert len(captured) == 1
        ev = captured[0]
        assert ev["type"] == "tray_menu"
        assert isinstance(ev["data"]["items"], list)
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_tray_click_dispatches_to_action(monkeypatch):
    """The ``tray_click`` command invokes the right id→action callback."""
    from voice_typer.server.ipc_server import IPCServer

    monkeypatch.setenv("TAURI_SIDECAR", "1")
    try:
        hits = []

        def _record(item_id):
            hits.append(item_id)
            return True

        class _Tray:
            def dispatch_tray_action(self, item_id):
                return _record(item_id)

        class _App:
            tray = _Tray()

        server = IPCServer(_App())
        result = server._dispatch({"type": "tray_click", "data": {"id": "open_app"}})

        assert result["type"] == "result"
        assert result["data"] == {"ok": True}
        assert hits == ["open_app"]
    finally:
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_tray_click_unknown_id_returns_error(monkeypatch):
    """Unknown tray ids return ``unknown_tray_item`` (not unknown_command)."""
    from voice_typer.server.ipc_server import IPCServer

    monkeypatch.setenv("TAURI_SIDECAR", "1")
    try:

        class _Tray:
            def dispatch_tray_action(self, item_id):
                return False  # unknown

        class _App:
            tray = _Tray()

        server = IPCServer(_App())
        result = server._dispatch({"type": "tray_click", "data": {"id": "nope"}})

        assert result["type"] == "error"
        assert result["data"]["code"] == "unknown_tray_item"
        assert result["data"]["id"] == "nope"
    finally:
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_tray_click_missing_id_returns_error():
    """``id`` is required (reuses _validate_dict_payload)."""
    from voice_typer.server.ipc_server import IPCServer

    class _App:
        tray = None

    server = IPCServer(_App())
    result = server._dispatch({"type": "tray_click", "data": {}})

    assert result["type"] == "error"
    assert result["data"]["code"] in ("missing_field", "invalid_payload")
