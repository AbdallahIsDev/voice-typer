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

import pytest
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
        "force_cancel_transcription": _noop,
        "is_transcribing": lambda: False,
        "restart_app": _noop,
        "quit_app": _noop,
        # Models submenu rows come from the DATA builder (list of
        # (name, downloaded, is_active, change_fn) tuples) — NOT from
        # pystray MenuItems. Empty default = "no downloaded models".
        "build_models_submenu_data": lambda: [],
        "on_open_models": _noop,
        "left_click_action": "open_app",
        "microphones": None,
        "active_mic_id": None,
        "on_select_mic": None,
        "on_refresh_mics": None,
        "on_open_microphones": None,
        "on_open_settings": _noop,
        "on_open_history": _noop,
        "on_open_help": _noop,
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
    """Stable top-level ids the host relies on are present.

    Per C-TRAY-1 in AGENTS.md, ``repaste_last`` MUST NOT appear
    in the tray menu model — the constraint forbids that item on both
    runtimes. This test now asserts its absence (regression guard).
    Per C-TRAY-2, ``undo_last`` is also forbidden in the tray menu.
    """
    model, id_map = _make_model()

    ids = {item["id"] for item in model if not item["separator"]}
    for expected in ("open_app", "toggle_dictation", "restart", "quit"):
        assert expected in ids, f"missing tray id {expected}"
    # C-TRAY-1 guard: repaste_last MUST NOT be in the model.
    assert "repaste_last" not in ids, (
        "C-TRAY-1 violation: repaste_last must NOT appear in the tray "
        "menu model — the constraint forbids a 'Repaste Last' button."
    )
    # C-TRAY-2 guard: undo_last MUST NOT be in the model.
    assert "undo_last" not in ids, (
        "C-TRAY-2 violation: undo_last must NOT appear in the tray "
        "menu model — the constraint forbids an 'Undo Last' button."
    )

    # id_map maps every actionable id to a callable.
    assert "open_app" in id_map
    assert callable(id_map["open_app"])
    assert "toggle_dictation" in id_map
    # repaste_last must NOT be in the id_map either.
    assert "repaste_last" not in id_map
    # undo_last must NOT be in the id_map either.
    assert "undo_last" not in id_map


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

            def undo_last(self):
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
        # error codes are now namespaced. Accept either the
        # namespaced ``server.unknown_tray_item`` form or the bare
        # legacy ``unknown_tray_item`` form for forward-compat.
        assert result["data"]["code"] in (
            "server.unknown_tray_item",
            "unknown_tray_item",
        )
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
    # error codes are now namespaced. Accept either the
    # namespaced form (``client.missing_field`` / ``client.invalid_payload``)
    # or the bare legacy form for forward-compat.
    assert result["data"]["code"] in (
        "missing_field",
        "invalid_payload",
        "client.missing_field",
        "client.invalid_payload",
    )


# tray_menu + tray_state publish wiring ──────────────────────


def _make_full_controller():
    """Build a minimal controller satisfying build_tray_menu_model's contract."""

    class _Ctrl:
        def toggle_dictation(self):
            pass

        def restart_app(self):
            pass

        def change_microphone(self, _mic):
            pass

        def change_model(self, _name):
            pass

        def undo_last(self):
            pass

        def refresh_microphones(self):
            pass

        _microphones = []

    return _Ctrl()


def _make_tauri_tray(*, controller=None, icon=None, hotkey="<f2>"):
    """Build a TrayIcon-like object that bypasses pystray entirely.

    Mirrors the shape that ``_maybe_publish_tray_menu`` and
    ``_publish_tray_state`` read off ``self``. ``icon`` defaults to
    ``None`` to model the Tauri runtime (where the native tray is owned
    by the Rust host and the pystray ``Icon`` is never created).
    """
    import threading

    import voice_typer.server.tray as tray_mod
    from voice_typer.server.tray_types import AppState

    class _FakeTray(tray_mod.TrayIcon):
        def __init__(self):
            self._config = None
            self._controller = controller
            self._state = AppState.IDLE
            self._message = ""
            self._icon = icon
            self._menu_cache_valid = False
            self._tray_id_map = {}
            self._hotkey = hotkey
            self._cpu_fallback_active = False
            self._recording_started_at = None
            self._microphones = []
            self._elapsed_timer = None
            # ``_publish_tray_state`` consults ``_last_published`` for
            # dedup; bypassing the real __init__ means we must set it
            # manually so set_state/refresh_config don't AttributeError.
            self._last_published = None
            # ``_invalidate_menu_cache_locked`` acquires ``_menu_lock``
            # to clear ``_menu_cache_valid`` atomically; bypassing the
            # real __init__ means we must set it manually so the lazy
            # setters (set_microphones/set_hotkey/refresh_config) don't
            # AttributeError.
            self._menu_lock = threading.Lock()
            # ``set_state`` queues pre-run state under ``_queue_lock`` when
            # ``_icon`` is None — provide a real lock so the with-block works.
            self._queue_lock = threading.Lock()
            self._pending_states = []
            self._pending_notifications = []
            # ``_publish_tray_state`` acquires ``_publish_lock`` to
            # serialize the check-then-publish-then-cache sequence;
            # bypassing the real __init__ means we must set it manually
            # so set_state/refresh_config/set_hotkey don't AttributeError.
            self._publish_lock = threading.Lock()

    return _FakeTray()


def test_publish_tray_state_guarded_by_tauri_sidecar(monkeypatch):
    """The ``tray_state`` event is emitted ONLY under TAURI_SIDECAR=1."""
    from voice_typer.server.tray_menu import publish_tray_state

    captured = []

    def _on_event(event):
        if event.get("type") == "tray_state":
            captured.append(event)

    event_bus.subscribe(_on_event)
    try:
        # 1. Without the env var: no event.
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        assert publish_tray_state(icon="recording", tooltip="x") is False
        assert captured == [], "tray_state must NOT publish without TAURI_SIDECAR"

        # 2. With the env var: event emitted with both fields.
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        assert publish_tray_state(icon="recording", tooltip="Voice Typer — Recording") is True
        assert len(captured) == 1
        ev = captured[0]
        assert ev["type"] == "tray_state"
        assert ev["data"]["icon"] == "recording"
        assert ev["data"]["tooltip"] == "Voice Typer — Recording"

        # 3. With only icon field: payload only has icon.
        assert publish_tray_state(icon="idle") is True
        ev2 = captured[-1]
        assert ev2["data"] == {"icon": "idle"}

        # 4. With no fields: returns False (nothing to publish).
        assert publish_tray_state() is False
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_maybe_publish_tray_menu_works_without_pystray_icon(monkeypatch):
    """Under Tauri the pystray Icon is never created (Rust owns the tray).

    The publish helper must NOT short-circuit on ``self._icon is None``
    — otherwise the tray_menu event never reaches the Rust host and the
    tray stays frozen at the empty placeholder for the whole session.
    """
    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)
    assert tray._icon is None, "test setup: Tauri runtime has no pystray Icon"

    captured = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            captured.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        published = tray._maybe_publish_tray_menu()
        assert published is True, (
            "_maybe_publish_tray_menu must publish under Tauri even when "
            "self._icon is None — the Rust host owns the native tray"
        )
        assert len(captured) == 1
        assert captured[0]["type"] == "tray_menu"
        assert isinstance(captured[0]["data"]["items"], list)
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_set_state_publishes_tray_state_under_tauri(monkeypatch):
    """``set_state`` emits a tray_state event so the Tauri host updates icon+tooltip."""
    from voice_typer.server.tray_types import AppState

    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)

    captured = []

    def _on_event(event):
        if event.get("type") == "tray_state":
            captured.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        tray.set_state(AppState.RECORDING, "Recording…")
        assert len(captured) >= 1, "set_state must emit tray_state under Tauri"
        ev = captured[-1]
        assert ev["data"]["icon"] == "recording"
        assert "Recording" in ev["data"]["tooltip"]
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_set_state_publishes_tray_menu_only_on_transcribing_change(monkeypatch):
    """Menu publishes fire on {RECORDING, TRANSCRIBING} membership changes
    (the ``record_or_transcribe_changed`` predicate in ``set_state``) —
    the "Stop Dictation" label flips on entry/exit — but NOT on
    within-set transitions (RECORDING → TRANSCRIBING) or message-only
    changes. Mirrors the per-transition matrix in
    ``test_tray_state_transitions.py::TestRecordingTransitionInvalidatesMenuCache``.
    """
    from voice_typer.server.tray_types import AppState

    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)

    menu_events = []
    state_events = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            menu_events.append(event)
        elif event.get("type") == "tray_state":
            state_events.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")

        # IDLE → RECORDING: membership change — menu publish (the
        # "Stop Dictation" label flips on the host's tray).
        tray.set_state(AppState.RECORDING)
        menu_after_recording = len(menu_events)
        assert menu_after_recording == 1, "IDLE→RECORDING must publish tray_menu (Stop Dictation label flips)"
        assert len(state_events) == 1

        # RECORDING → TRANSCRIBING: stays inside {RECORDING, TRANSCRIBING}
        # — no menu publish (membership unchanged).
        tray.set_state(AppState.TRANSCRIBING)
        assert len(menu_events) == 1, "RECORDING→TRANSCRIBING must NOT publish tray_menu (stays in membership set)"

        # TRANSCRIBING → TRANSCRIBING: message-only change — no menu publish.
        tray.set_state(AppState.TRANSCRIBING, "still transcribing")
        assert len(menu_events) == 1, "TRANSCRIBING→TRANSCRIBING (msg change) must NOT publish tray_menu"

        # TRANSCRIBING → IDLE: membership change — menu publish (label
        # flips back to "Start Dictation").
        tray.set_state(AppState.IDLE)
        assert len(menu_events) == 2, "TRANSCRIBING→IDLE must publish tray_menu (membership change)"
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_set_hotkey_publishes_tray_menu_and_state(monkeypatch):
    """``set_hotkey`` pushes the new hotkey label in the menu + tooltip."""
    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)

    menu_events = []
    state_events = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            menu_events.append(event)
        elif event.get("type") == "tray_state":
            state_events.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        tray.set_hotkey("<f9>")
        assert len(menu_events) == 1, "set_hotkey must publish tray_menu (label changes)"
        assert len(state_events) == 1, "set_hotkey must publish tray_state (tooltip changes)"
        # The new hotkey should appear in the menu model's toggle_dictation label.
        items = menu_events[0]["data"]["items"]
        toggle = next(i for i in items if i["id"] == "toggle_dictation")
        assert "F9" in toggle["label"], "menu label should reflect new hotkey"
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_set_microphones_publishes_tray_menu(monkeypatch):
    """``set_microphones`` pushes the new Microphones submenu to the host."""
    ctrl = _make_full_controller()
    tray = _make_tauri_tray(controller=ctrl, icon=None)

    menu_events = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            menu_events.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        mics = [{"id": "0", "name": "Built-in"}, {"id": "1", "name": "USB"}]
        # Mirror production ordering: the caller (e.g. startup_tasks.py)
        # updates ``controller._microphones`` BEFORE calling
        # ``tray.set_microphones`` — ``_maybe_publish_tray_menu`` reads
        # ``controller._microphones`` (not ``self._microphones``).
        ctrl._microphones = mics
        tray.set_microphones(mics)
        assert len(menu_events) == 1, "set_microphones must publish tray_menu"
        items = menu_events[0]["data"]["items"]
        mic_item = next((i for i in items if i["id"] == "microphones"), None)
        assert mic_item is not None, "menu model should include a microphones submenu"
        assert mic_item["submenu"] is not None
        sub_ids = {i["id"] for i in mic_item["submenu"] if not i["separator"]}
        assert "mic:0" in sub_ids
        assert "mic:1" in sub_ids
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_refresh_config_publishes_tray_menu_and_state(monkeypatch):
    """``refresh_config`` pushes the rebuilt menu + tooltip to the host."""
    from types import SimpleNamespace

    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)

    menu_events = []
    state_events = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            menu_events.append(event)
        elif event.get("type") == "tray_state":
            state_events.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        tray.refresh_config(SimpleNamespace(hotkey="<f5>", model_size="medium", tray_left_click_action="open_app"))
        assert len(menu_events) == 1, "refresh_config must publish tray_menu"
        assert len(state_events) == 1, "refresh_config must publish tray_state"
        items = menu_events[0]["data"]["items"]
        toggle = next(i for i in items if i["id"] == "toggle_dictation")
        assert "F5" in toggle["label"], "menu should reflect new hotkey from config"
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_ws_reader_allowlist_includes_tray_state():
    """The Rust WS reader's ALLOWED_EVENT_TYPES must include ``tray_state``.

    Without this, the WS reader silently drops the ``tray_state`` event
    at the protocol boundary — the Rust tray.rs listener never fires,
    so the Tauri tray icon stays frozen at the startup placeholder
    regardless of recording/transcribing/error state.
    """
    # Parse the ALLOWED_EVENT_TYPES array out of the Rust source. We
    # grep the source rather than calling Rust because the sandbox has
    # no cargo. The constant moved to ws/event_protocol.rs during the
    # ws.rs module split (review.md FZ-24 / ZR-86); search both the new
    # home and the legacy ws.rs for resilience.
    from pathlib import Path

    src = ""
    for rel in ("src-tauri/src/sidecar/ws/event_protocol.rs", "src-tauri/src/sidecar/ws.rs"):
        p = Path(__file__).resolve().parents[2] / rel
        if p.exists():
            src += p.read_text(encoding="utf-8") + "\n"
    assert src, "neither ws/event_protocol.rs nor ws.rs found"
    # The array is a single const declaration; find its bounds and
    # verify ``"tray_state"`` (with quotes) appears inside.
    assert '"tray_state"' in src, (
        'ws/event_protocol.rs ALLOWED_EVENT_TYPES must include "tray_state" '
        "so the Rust WS reader forwards the event to the tray_state "
        "listener registered in tray.rs::create_tray"
    )
    # Also verify tray_menu is still there (regression guard — we
    # edited the same line that lists tray_menu).
    assert '"tray_menu"' in src


def test_bg_work_wrapper_publishes_initial_menu(monkeypatch):
    """``start(bg_work=…)`` wraps bg_work so the initial menu is published.

    Under Tauri, the native tray is built by the Rust host with an
    empty placeholder menu. Without an explicit publish after bg_work
    completes (model preload + hotkey registration), the user sees only
    the placeholder until they change a setting that triggers
    ``invalidate_menu_cache``.
    """
    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)

    menu_events = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            menu_events.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")

        # The wrapper is built by _wrap_bg_work; invoke it directly so
        # we don't have to spin up a real bg thread.
        ran = []
        wrapped = tray._wrap_bg_work(lambda: ran.append(True))
        assert wrapped is not None
        wrapped()
        assert ran == [True], "wrapper must call the original bg_work"
        assert len(menu_events) == 1, "wrapper must publish tray_menu after bg_work completes"
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


def test_bg_work_wrapper_returns_none_for_none_input():
    """``_wrap_bg_work(None)`` returns ``None`` (preserves start() guards)."""
    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)
    assert tray._wrap_bg_work(None) is None


def test_bg_work_wrapper_publishes_even_when_bg_work_raises(monkeypatch):
    """If bg_work raises, the wrapper still publishes the menu (try/finally)."""
    tray = _make_tauri_tray(controller=_make_full_controller(), icon=None)

    menu_events = []

    def _on_event(event):
        if event.get("type") == "tray_menu":
            menu_events.append(event)

    event_bus.subscribe(_on_event)
    try:
        monkeypatch.setenv("TAURI_SIDECAR", "1")

        def _boom():
            raise RuntimeError("preload failed")

        wrapped = tray._wrap_bg_work(_boom)
        with pytest.raises(RuntimeError):
            wrapped()
        assert len(menu_events) == 1, "wrapper must publish tray_menu even when bg_work raises"
    finally:
        event_bus.unsubscribe(_on_event)
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)


# ─── Tauri Models submenu: dispatchable rows + no dash item ──────────────
# Regression tests for the user-reported Tauri tray defects:
#   * the Models submenu showed a leading dash item (pystray's
#     SEPARATOR flattened to a text row) and "More models..." clicks
#     died as ``server.unknown_tray_item`` because no callback was
#     registered; model rows were equally dead.
#   * the Microphones parent vanished entirely when the device list
#     was momentarily empty.
# The dict path must consume the shared DATA layer
# (``tray_models.build_models_submenu_data`` tuples) directly — never a
# pystray round-trip.


def _models_row(name, downloaded, is_active, change_fn):
    """One (name, downloaded, is_active, change_fn) data tuple."""
    return (name, downloaded, is_active, change_fn)


def test_models_submenu_zero_models_has_no_leading_separator():
    """Zero downloaded models → submenu is exactly [more_models].

    The user reported a first item that is "just a dash" — that was
    pystray's ``Menu.SEPARATOR`` (text ``'- - - -'``) flattened into a
    normal label row by the old pystray-item round-trip. With no model
    rows there must be NO separator at all.
    """
    model, _id_map = _make_model(build_models_submenu_data=lambda: [])
    models_item = next(i for i in model if i["id"] == "models")
    sub = models_item["submenu"]

    assert sub is not None
    separators = [i for i in sub if i["separator"]]
    assert separators == [], "no separator may render when there are no model rows"
    ids = [i["id"] for i in sub]
    assert ids == ["more_models"], f"expected exactly the more_models row, got {ids}"
    # No row may carry a separator-ish label (the old dash defect).
    for i in sub:
        assert "-" not in i["label"]


def test_models_submenu_more_models_registered_and_opens_models_page():
    """``more_models`` must be in the id_map and invoke the open callback.

    Clicking "More models..." previously dispatched an unknown id (the
    user's log: ``server.unknown_tray_item``) — the item had no callback.
    """
    opened = []
    model, id_map = _make_model(
        build_models_submenu_data=lambda: [],
        on_open_models=lambda: opened.append("/models"),
    )

    assert "more_models" in id_map, "more_models must be dispatchable"
    assert callable(id_map["more_models"])
    models_item = next(i for i in model if i["id"] == "models")
    more = next(i for i in models_item["submenu"] if i["id"] == "more_models")
    assert more["label"] == "More models..."

    id_map["more_models"]()
    assert opened == ["/models"], "the callback must open the Models page"


def test_models_submenu_rows_have_callbacks_and_checked_state():
    """Downloaded models render as dispatchable rows with checkmarks.

    Only downloaded models get rows; the active one carries
    ``checked=True`` (native CheckMenuItem on the Rust host); every row
    registers its change-model callback in the id_map; a separator sits
    ONLY between the model rows and the trailing deep-link.
    """
    changes = []
    data = [
        _models_row("tiny", True, True, lambda n="tiny": changes.append(n)),
        _models_row("large-v3", True, False, lambda n="large-v3": changes.append(n)),
        _models_row("qwen", False, False, lambda n="qwen": changes.append(n)),
    ]
    model, id_map = _make_model(build_models_submenu_data=lambda: data)

    models_item = next(i for i in model if i["id"] == "models")
    sub = models_item["submenu"]
    by_id = {i["id"]: i for i in sub}

    # Only downloaded models get rows.
    assert "model:tiny" in by_id
    assert "model:large-v3" in by_id
    assert "model:qwen" not in by_id, "not-downloaded models must not render"

    # Native checkmark state: active True, others False.
    assert by_id["model:tiny"]["checked"] is True
    assert by_id["model:large-v3"]["checked"] is False

    # The label keeps the family glyph (mirrors the pystray path).
    assert by_id["model:tiny"]["label"] == "✱ tiny"

    # Exactly one separator, and it is BETWEEN rows and the deep-link.
    sep_positions = [idx for idx, i in enumerate(sub) if i["separator"]]
    assert sep_positions == [2], f"expected one separator after the 2 rows, got {sep_positions}"
    assert sub[-1]["id"] == "more_models"

    # Rows are dispatchable: the id_map callback switches the model.
    assert "model:tiny" in id_map
    id_map["model:tiny"]()
    assert changes == ["tiny"]


def test_microphones_item_present_with_empty_list():
    """The Microphones parent must render even with zero devices.

    The old ``if microphones:`` gate dropped the whole item whenever the
    list was momentarily empty (ws-mode startup). The submenu must stay
    useful: refresh action + deep-link, and NO leading separator/dash.
    """
    refreshed = []
    opened_mics = []
    model, id_map = _make_model(
        microphones=[],
        on_refresh_mics=lambda: refreshed.append(True),
        on_open_microphones=lambda: opened_mics.append("/microphone"),
    )

    mic_item = next(i for i in model if i["id"] == "microphones")
    sub = mic_item["submenu"]
    ids = [i["id"] for i in sub]

    # No device rows, no separator rows — just the useful actions.
    assert ids == ["refresh_mics", "more_microphones"], f"got {ids}"
    assert all(not i["separator"] for i in sub)

    # Both actions dispatch.
    id_map["refresh_mics"]()
    assert refreshed == [True]
    id_map["more_microphones"]()
    assert opened_mics == ["/microphone"]


def test_microphones_submenu_rows_keep_separator_before_actions():
    """With devices present, a separator divides rows from the actions.

    Never a LEADING separator (the dash-defect class) — the first row
    must be a ``mic:`` row.
    """
    mics = [{"id": "0", "name": "Default"}, {"id": "1", "name": "USB Mic"}]
    model, _id_map = _make_model(
        microphones=mics,
        active_mic_id="1",
        on_select_mic=_noop,
        on_refresh_mics=_noop,
        on_open_microphones=_noop,
    )
    mic_item = next(i for i in model if i["id"] == "microphones")
    sub = mic_item["submenu"]
    ids = [i["id"] for i in sub]

    assert ids == ["mic:0", "mic:1", "", "refresh_mics", "more_microphones"]
    # First row is a mic row (no leading separator), one separator after
    # the device rows (separator ids are "" per the wire contract).
    assert ids[0] == "mic:0"
    seps = [idx for idx, i in enumerate(sub) if i["separator"]]
    assert seps == [2], f"expected one separator after the mic rows, got {seps}"


def test_maybe_publish_registers_models_and_mic_dispatch(monkeypatch):
    """Integration: the published Tauri menu carries dispatchable ids.

    ``maybe_publish_tray_menu`` must build the models submenu from the
    shared data layer and register ``more_models`` / ``model:<name>``
    / ``refresh_mics`` / ``more_microphones`` in ``tray._tray_id_map``
    so ``tray_click`` dispatch actually reaches the actions.
    """
    changes = []
    opened = []

    class _RecordingCtrl:
        def toggle_dictation(self):
            pass

        def restart_app(self):
            pass

        def change_microphone(self, _mic):
            pass

        def change_model(self, name):
            changes.append(name)

        def undo_last(self):
            pass

        def refresh_microphones(self):
            pass

        _microphones = []

    tray = _make_tauri_tray(controller=_RecordingCtrl(), icon=None)
    tray._open_models_page = lambda: opened.append("/models")
    tray._open_microphones_page = lambda: opened.append("/microphone")

    def _fake_data(config_dir_fn, change_model_fn, config_provider=None):
        return [
            ("tiny", True, True, lambda n="tiny": change_model_fn(n)),
            ("qwen", False, False, lambda n="qwen": change_model_fn(n)),
        ]

    monkeypatch.setattr("voice_typer.server.tray_models.build_models_submenu_data", _fake_data)
    monkeypatch.setenv("TAURI_SIDECAR", "1")

    published = tray._maybe_publish_tray_menu()
    assert published is True

    id_map = tray._tray_id_map
    for expected in ("more_models", "model:tiny", "refresh_mics", "more_microphones"):
        assert expected in id_map, f"{expected} must be dispatchable after publish"
    assert "model:qwen" not in id_map, "not-downloaded models must not dispatch"

    # Click dispatch reaches the real actions.
    assert tray.dispatch_tray_action("more_models") is True
    assert opened == ["/models"]
    assert tray.dispatch_tray_action("model:tiny") is True
    assert changes == ["tiny"]


def test_pystray_models_submenu_contract_unchanged(monkeypatch):
    """Regression guard: the pystray (Electron) path is untouched.

    ``build_models_menu_items`` still emits [model rows…] + separator +
    "More models..." with ``checked`` callables — the exact behavior the
    Electron runtime has today.
    """
    from voice_typer.server import tray_models

    monkeypatch.setattr(
        tray_models,
        "build_models_submenu_data",
        lambda *a, **k: [("tiny", True, True, lambda: None)],
    )

    class _FakeItem:
        def __init__(self, text, action, checked=None):
            self.text = text
            self.action = action
            self.checked = checked

    created = []
    sep = object()

    def _item_cls(text, action, checked=None):
        item = _FakeItem(text, action, checked)
        created.append(item)
        return item

    items = tray_models.build_models_menu_items(
        lambda: None,  # config_dir_fn (unused — data is stubbed)
        lambda name: None,
        lambda fn: fn,  # wrap_fn: identity
        lambda: None,  # open_electron_window_fn
        menu_item_class=_item_cls,
        menu_separator=sep,
    )

    # [model row, separator, More models...]
    assert items[0] is created[0]
    assert created[0].text == "✱ tiny"
    assert callable(created[0].checked)
    assert created[0].checked(None) is True
    assert items[1] is sep, "the pystray path keeps its separator item"
    assert created[1].text == "More models..."
    assert items[2] is created[1]
