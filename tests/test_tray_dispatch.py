"""Tray click dispatch + cache-invalidation regression tests.

Covers the compound tray fix that unblocks the Tauri tray runtime:

(a) ``TrayIcon`` exposes ``dispatch_tray_action`` without any mock
    injected by the caller — previously the method was missing entirely
    on the production ``TrayIcon`` class and the IPC layer's
    ``hasattr(tray, "dispatch_tray_action")`` guard returned False,
    silently dropping every Tauri tray click as ``unknown_tray_item``.

(b) ``dispatch_tray_action`` consults ``self._tray_id_map`` (populated
    by ``_maybe_publish_tray_menu``) and returns True for known ids /
    False for unknown ids (the IPC layer turns a False return into a
    ``server.unknown_tray_item`` error envelope).

(c) The Tauri-side ``build_tray_menu_model`` does NOT emit a
    ``repaste_last`` item — CONSTRAINTS.md C-TRAY-1 forbids that entry
    on both runtimes. The pystray-side ``build_menu_for_tray`` already
    omitted it; this test pins the parity so a future re-introduction
    on the Tauri path is caught at CI time.

(d) ``_invalidate_menu_cache_locked`` clears ``_menu_cache_valid``
    under ``_menu_lock`` WITHOUT calling ``self._icon._update_menu()``
    (the eager ``invalidate_menu_cache`` helper is reserved for
    explicit user-facing refresh actions; the lazy setters use the
    locked variant to avoid the Win32 ``DestroyMenu`` round-trip).

(e) Menu spec parity: both the pystray builder (``build_menu_for_tray``)
    and the Tauri builder (``build_tray_menu_model``) emit the same
    set of top-level item ids (single source of truth for the menu
    structure). Settings/History/Help/Undo Last were previously MISSING
    on the Tauri path — this test guards against regression.

These tests mock ``pystray`` at ``sys.modules`` (so the tray module can
be imported without an X display) and ``event_bus`` (so publish helpers
don't emit events to a real bus). No production tray code is mocked —
``dispatch_tray_action`` / ``_invalidate_menu_cache_locked`` /
``build_tray_menu_model`` are all invoked for real.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ─── Module-level pystray stub ──────────────────────────────────────────
# pystray's xorg backend calls Xlib.display.Display() at module import
# time, which fails headless. The lazy_module proxy in tray.py /
# tray_menu.py re-reads sys.modules on every access, so installing the
# stub here keeps the import side-effect-free.
_mock_pystray = MagicMock()
_mock_pystray.Menu = MagicMock
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_menu import build_tray_menu_model  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_pystray_on_tray_modules(monkeypatch):
    """Ensure tray.py and tray_menu.py both see the pystray stub.

    tray.py / tray_menu.py capture ``pystray = lazy_module("pystray")``
    at module load; the proxy re-reads sys.modules on every attribute
    access so the stub above is already in effect. We additionally
    mock PIL at sys.modules so tray_icon._make_icon (imported eagerly
    by tray.py) does not pull real PIL into the test process.
    """
    mock_pystray = MagicMock()
    mock_pystray.Menu = MagicMock
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = MagicMock
    mock_pystray.Icon = MagicMock
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    import voice_typer.server.tray as tray_mod
    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)
    monkeypatch.setattr(tray_menu_mod, "pystray", mock_pystray)

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=0: MagicMock())


class _MockController:
    """Minimal controller satisfying the TrayController protocol."""

    def toggle_dictation(self) -> None: ...

    def change_microphone(self, mic_id: str | None) -> None: ...

    def change_model(self, model_size: str) -> None: ...

    def quit_app(self) -> None: ...

    def restart_app(self) -> None: ...

    def undo_last(self) -> None: ...


def _make_tray() -> TrayIcon:
    """Build a real TrayIcon with a mock controller + config (no pystray)."""
    return TrayIcon(
        controller=_MockController(),
        config=SimpleNamespace(
            hotkey="<f2>",
            model_size="small.en",
            autostart=True,
            show_notifications=True,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
        ),
    )


# ─── (a) dispatch_tray_action exists on production TrayIcon ─────────────


class TestDispatchTrayActionMethodPresent:
    """``TrayIcon`` must expose ``dispatch_tray_action`` without any
    caller-injected mock — the production class owns the method."""

    def test_hasattr_dispatch_tray_action_without_mock(self):
        """The production ``TrayIcon`` class owns ``dispatch_tray_action``.

        Previously the method was missing and the IPC layer's
        ``hasattr(tray, "dispatch_tray_action")`` guard returned False,
        silently dropping every Tauri tray click. This test pins the
        method's presence on the class itself (not a subclass mock).
        """
        tray = _make_tray()
        assert hasattr(tray, "dispatch_tray_action"), (
            "TrayIcon must expose dispatch_tray_action so the IPC layer's "
            "hasattr(tray, 'dispatch_tray_action') guard returns True under "
            "the Tauri runtime — otherwise every tray click silently fails "
            "as 'server.unknown_tray_item'."
        )
        # The attribute must be a bound method, not a non-callable field.
        assert callable(tray.dispatch_tray_action)

    def test_dispatch_tray_action_on_class_not_instance(self):
        """The method lives on the ``TrayIcon`` class itself, so
        ``hasattr(TrayIcon, 'dispatch_tray_action')`` is True too —
        a subclass that doesn't override still inherits it."""
        assert hasattr(TrayIcon, "dispatch_tray_action")


# ─── (b) dispatch_tray_action routes by id, returns True/False ──────────


class TestDispatchTrayActionRouting:
    """``dispatch_tray_action`` consults ``_tray_id_map`` and returns
    True for known ids / False for unknown ids."""

    def test_unknown_id_returns_false(self):
        """Before any menu publish (or for an id the host never
        registered), ``_tray_id_map`` is empty / misses the id →
        return False so the IPC layer emits ``unknown_tray_item``."""
        tray = _make_tray()
        # _tray_id_map defaults to {} in __init__ — no publish yet.
        assert tray._tray_id_map == {}
        assert tray.dispatch_tray_action("does_not_exist") is False

    def test_known_id_invokes_callback_and_returns_true(self):
        """A registered id invokes its callback and returns True."""
        tray = _make_tray()
        invoked = []

        def _callback():
            invoked.append(True)

        tray._tray_id_map["open_app"] = _callback
        result = tray.dispatch_tray_action("open_app")
        assert result is True
        assert invoked == [True]

    def test_known_id_with_failing_callback_still_returns_true(self):
        """A callback that raises must NOT crash the IPC server thread.

        The return value stays True — the click was *dispatched*; the
        callback's failure is the renderer's concern (surfaced via
        toasts). This guards against a single broken callback taking
        down the entire Tauri tray IPC path.
        """
        tray = _make_tray()

        def _boom():
            raise RuntimeError("callback failed")

        tray._tray_id_map["open_app"] = _boom
        # Must NOT raise.
        result = tray.dispatch_tray_action("open_app")
        assert result is True

    def test_dispatch_does_not_touch_other_ids(self):
        """Dispatching id A does NOT invoke id B's callback."""
        tray = _make_tray()
        a_hits = []
        b_hits = []
        tray._tray_id_map = {
            "a": lambda: a_hits.append(True),
            "b": lambda: b_hits.append(True),
        }
        tray.dispatch_tray_action("a")
        assert a_hits == [True]
        assert b_hits == []


# ─── (c) repaste_last MUST NOT be in the Tauri menu model (C-TRAY-1) ────


class TestNoRepasteLastInTauriMenuModel:
    """C-TRAY-1 in CONSTRAINTS.md forbids a 'Repaste Last' tray item.

    The pystray-side builder (``build_menu_for_tray``) already omits it.
    This test pins the Tauri-side builder (``build_tray_menu_model``) so
    a future re-introduction is caught at CI time.
    """

    def test_repaste_last_not_in_model_ids(self):
        """No top-level item id in the Tauri model is ``repaste_last``."""
        model, _id_map = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            undo_last=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
        )
        ids = {item["id"] for item in model if not item["separator"]}
        assert "repaste_last" not in ids, (
            "C-TRAY-1 violation: 'repaste_last' must NOT appear in the "
            "Tauri tray menu model — CONSTRAINTS.md forbids a 'Repaste "
            "Last transcription' button on both runtimes."
        )

    def test_repaste_last_not_in_id_map(self):
        """The ``id_map`` (callback dispatch table) must NOT have a
        ``repaste_last`` entry — even if a stray callback were passed,
        the builder must not register it under that id."""
        _model, id_map = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            undo_last=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
        )
        assert "repaste_last" not in id_map

    def test_repaste_last_not_in_any_submenu(self):
        """No submenu item id is ``repaste_last`` either (regression
        guard against a future re-introduction as a nested entry)."""
        model, _id_map = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            undo_last=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            microphones=[{"id": "0", "name": "Default"}],
            on_select_mic=lambda _id: None,
            on_refresh_mics=lambda: None,
            on_open_settings=lambda: None,
            on_open_history=lambda: None,
            on_open_help=lambda: None,
        )

        def _walk(items):
            for item in items:
                assert item["id"] != "repaste_last", (
                    "C-TRAY-1 violation: 'repaste_last' must NOT appear as a submenu item id either."
                )
                if item.get("submenu"):
                    _walk(item["submenu"])

        _walk(model)


# ─── (d) _invalidate_menu_cache_locked clears flag, no _update_menu ────


class TestInvalidateMenuCacheLocked:
    """The lazy cache-invalidation helper clears ``_menu_cache_valid``
    under ``_menu_lock`` WITHOUT calling ``_icon._update_menu()``."""

    def test_helper_exists(self):
        """``_invalidate_menu_cache_locked`` is defined on TrayIcon."""
        tray = _make_tray()
        assert hasattr(tray, "_invalidate_menu_cache_locked")
        assert callable(tray._invalidate_menu_cache_locked)

    def test_clears_menu_cache_valid_flag(self):
        """After the call, ``_menu_cache_valid`` is False."""
        tray = _make_tray()
        tray._menu_cache_valid = True
        tray._cached_menu = ("sentinel",)
        tray._invalidate_menu_cache_locked()
        assert tray._menu_cache_valid is False

    def test_does_not_call_icon_update_menu(self):
        """The lazy variant must NOT call ``self._icon._update_menu()``.

        The eager ``invalidate_menu_cache`` (with ``_update_menu``) is
        reserved for explicit user-facing refresh actions because the
        Win32 DestroyMenu / CreatePopupMenu round-trip is unnecessary
        when the Tauri host owns the native tray (``self._icon`` is
        None) and the next pystray right-click rebuilds lazily.
        """
        tray = _make_tray()
        # Install a mock icon so we can assert _update_menu is NOT called.
        mock_icon = MagicMock()
        tray._icon = mock_icon
        tray._menu_cache_valid = True

        tray._invalidate_menu_cache_locked()

        assert tray._menu_cache_valid is False
        mock_icon._update_menu.assert_not_called()

    def test_set_microphones_uses_locked_helper(self):
        """``set_microphones`` must use ``_invalidate_menu_cache_locked``
        (not the eager ``invalidate_menu_cache``) so the Win32 menu
        handle isn't rebuilt on every mic-list update."""
        tray = _make_tray()
        mock_icon = MagicMock()
        tray._icon = mock_icon
        tray._menu_cache_valid = True

        # Stub the Tauri publish so the test doesn't emit events.
        tray._maybe_publish_tray_menu = lambda: False

        tray.set_microphones([{"id": "0", "name": "Default"}])

        assert tray._microphones == [{"id": "0", "name": "Default"}]
        assert tray._menu_cache_valid is False
        # The lazy variant must NOT trigger _update_menu.
        mock_icon._update_menu.assert_not_called()

    def test_set_hotkey_uses_locked_helper(self):
        """``set_hotkey`` must use ``_invalidate_menu_cache_locked``."""
        tray = _make_tray()
        mock_icon = MagicMock()
        tray._icon = mock_icon
        tray._menu_cache_valid = True
        tray._maybe_publish_tray_menu = lambda: False
        tray._publish_tray_state = lambda: None

        tray.set_hotkey("<f9>")

        assert tray._hotkey == "<f9>"
        assert tray._menu_cache_valid is False
        mock_icon._update_menu.assert_not_called()

    def test_refresh_config_uses_locked_helper(self):
        """``refresh_config`` must use ``_invalidate_menu_cache_locked``."""
        tray = _make_tray()
        mock_icon = MagicMock()
        tray._icon = mock_icon
        tray._menu_cache_valid = True
        tray._maybe_publish_tray_menu = lambda: False
        tray._publish_tray_state = lambda: None

        tray.refresh_config(SimpleNamespace(hotkey="<f5>", model_size="medium"))

        assert tray._menu_cache_valid is False
        mock_icon._update_menu.assert_not_called()


# ─── (e) Menu spec parity: pystray vs Tauri builders ────────────────────


class TestMenuSpecParity:
    """Both builders (pystray ``build_menu_for_tray`` + Tauri
    ``build_tray_menu_model``) emit the same set of top-level item ids.

    Previously the Tauri path was MISSING Undo Last / Settings / History
    / Help and ILLEGALLY had Repaste Last (C-TRAY-1). This test pins
    parity so the two runtimes stay in sync.
    """

    def test_tauri_model_includes_undo_last_settings_history_help(self):
        """The Tauri model must include Undo Last / Settings / History /
        Help — previously these were MISSING on Tauri, leaving the
        routes unreachable from the tray."""
        model, _ = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            undo_last=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            on_open_settings=lambda: None,
            on_open_history=lambda: None,
            on_open_help=lambda: None,
        )
        ids = {item["id"] for item in model if not item["separator"]}
        assert "undo_last" in ids, "Tauri menu must include Undo Last (parity with pystray)"
        assert "settings" in ids, "Tauri menu must include Settings (parity with pystray)"
        assert "history" in ids, "Tauri menu must include History (parity with pystray)"
        assert "help" in ids, "Tauri menu must include Help (parity with pystray)"

    def test_core_top_level_ids_match_pystray(self):
        """Both builders emit the same core top-level ids (Open App,
        Toggle Dictation, Models, Microphones, Restart, Quit).

        This is a parity smoke-test focused on the Tauri side (the side
        that was missing items per OI-18). The pystray-side builder is
        already covered by ``tests/test_tray.py``'s ``_menu_labels``
        assertions (Toggle/Open App/Models/Microphones/Restart/Quit).
        """
        # Tauri side: build via build_tray_menu_model.
        tauri_model, _ = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            undo_last=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            microphones=[{"id": "0", "name": "Default"}],
            on_select_mic=lambda _id: None,
            on_refresh_mics=lambda: None,
            on_open_settings=lambda: None,
            on_open_history=lambda: None,
            on_open_help=lambda: None,
        )
        tauri_ids = {i["id"] for i in tauri_model if not i["separator"]}

        # The Tauri side emits the same canonical ids the pystray side
        # emits (verified by tests/test_tray.py's _menu_labels helper).
        # The previously-missing Undo Last / Settings / History / Help
        # are now present (the OI-18 fix).
        for expected in (
            "open_app",
            "toggle_dictation",
            "undo_last",
            "models",
            "microphones",
            "settings",
            "history",
            "help",
            "restart",
            "quit",
        ):
            assert expected in tauri_ids, (
                f"Tauri menu missing id {expected!r} — both builders must emit the same item set (OI-18 parity)."
            )
        # C-TRAY-1 guard: repaste_last MUST NOT be on either side.
        assert "repaste_last" not in tauri_ids
