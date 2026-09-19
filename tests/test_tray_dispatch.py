"""
Tray click dispatch + cache-invalidation regression tests.
``repaste_last`` item, AGENTS.md C-TRAY-1 forbids that entry
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_menu import build_tray_menu_model  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_pystray_on_tray_modules(monkeypatch):
    """Ensure tray.py and tray_menu.py both see the pystray stub."""
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


class TestDispatchTrayActionMethodPresent:
    """``TrayIcon`` must expose ``dispatch_tray_action`` without any"""

    def test_hasattr_dispatch_tray_action_without_mock(self):
        """The production ``TrayIcon`` class owns ``dispatch_tray_action``."""
        tray = _make_tray()
        assert hasattr(tray, "dispatch_tray_action"), (
            "TrayIcon must expose dispatch_tray_action so the IPC layer's "
            "hasattr(tray, 'dispatch_tray_action') guard returns True under "
            "the Tauri runtime, otherwise every tray click silently fails "
            "as 'server.unknown_tray_item'."
        )
        # The attribute must be a bound method, not a non-callable field.
        assert callable(tray.dispatch_tray_action)

    def test_dispatch_tray_action_on_class_not_instance(self):
        """The method lives on the ``TrayIcon`` class itself, so"""
        assert hasattr(TrayIcon, "dispatch_tray_action")


class TestDispatchTrayActionRouting:
    """``dispatch_tray_action`` consults ``_tray_id_map`` and returns"""

    def test_unknown_id_returns_false(self):
        """Before any menu publish (or for an id the host never"""
        tray = _make_tray()
        # _tray_id_map defaults to {} in __init__, no publish yet.
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
        """A callback that raises must NOT crash the IPC server thread."""
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


class TestNoRepasteLastInTauriMenuModel:
    """
    C-TRAY-1 in AGENTS.md forbids a 'Repaste Last' tray item.
    This test pins the Tauri-side builder (``build_tray_menu_model``) so
    """

    def test_repaste_last_not_in_model_ids(self):
        """No top-level item id in the Tauri model is ``repaste_last``."""
        model, _id_map = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
        )
        ids = {item["id"] for item in model if not item["separator"]}
        assert "repaste_last" not in ids, (
            "C-TRAY-1 violation: 'repaste_last' must NOT appear in the "
            "Tauri tray menu model, AGENTS.md forbids a 'Repaste "
            "Last transcription' button on both runtimes."
        )

    def test_repaste_last_not_in_id_map(self):
        """``repaste_last`` entry, even if a stray callback were passed,"""
        _model, id_map = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
        )
        assert "repaste_last" not in id_map

    def test_repaste_last_not_in_any_submenu(self):
        """No submenu item id is ``repaste_last`` either (regression"""
        model, _id_map = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
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


class TestInvalidateMenuCacheLocked:
    """The lazy cache-invalidation helper clears ``_menu_cache_valid``"""

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
        """The lazy variant must NOT call ``self._icon._update_menu()``."""
        tray = _make_tray()
        # Install a mock icon so we can assert _update_menu is NOT called.
        mock_icon = MagicMock()
        tray._icon = mock_icon
        tray._menu_cache_valid = True

        tray._invalidate_menu_cache_locked()

        assert tray._menu_cache_valid is False
        mock_icon._update_menu.assert_not_called()

    def test_set_microphones_uses_locked_helper(self):
        """``set_microphones`` must use ``_invalidate_menu_cache_locked``"""
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


class TestMenuSpecParity:
    """Both builders (pystray ``build_menu_for_tray`` + Tauri"""

    def test_tauri_model_includes_settings_history_help(self):
        """The Tauri model must include Settings / History / Help —"""
        model, _ = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            on_open_settings=lambda: None,
            on_open_history=lambda: None,
            on_open_help=lambda: None,
        )
        ids = {item["id"] for item in model if not item["separator"]}
        assert "undo_last" not in ids, "C-TRAY-2: undo_last must NOT be in the Tauri tray menu"
        assert "settings" in ids, "Tauri menu must include Settings (parity with pystray)"
        assert "history" in ids, "Tauri menu must include History (parity with pystray)"
        assert "help" in ids, "Tauri menu must include Help (parity with pystray)"

    def test_core_top_level_ids_match_pystray(self):
        """Both builders emit the same core top-level ids (Open App,"""
        # Tauri side: build via build_tray_menu_model.
        tauri_model, _ = build_tray_menu_model(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
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

        # present (the OI-18 fix). ``undo_last`` is absent (C-TRAY-2).
        for expected in (
            "open_app",
            "toggle_dictation",
            "models",
            "microphones",
            "settings",
            "history",
            "help",
            "restart",
            "quit",
        ):
            assert expected in tauri_ids, (
                f"Tauri menu missing id {expected!r}, both builders must emit the same item set (OI-18 parity)."
            )
        # C-TRAY-1 guard: repaste_last MUST NOT be on either side.
        assert "repaste_last" not in tauri_ids
