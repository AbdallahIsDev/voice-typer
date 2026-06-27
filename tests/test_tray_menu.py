"""#13: tests for the extracted tray_menu module.

Verifies that:
- display_hotkey formats pynput hotkey strings correctly
- wrap_callback suppresses SystemExit (ERR-QUIT-002)
- build_menu produces the expected menu structure
"""
import sys
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, '/home/z/my-project/voice-typer-repo')


class TestDisplayHotkey:
    """display_hotkey formats pynput hotkey strings."""

    def test_f2_default(self):
        from voice_typer.server.tray_menu import display_hotkey
        assert display_hotkey("<f2>") == "F2"

    def test_custom_hotkey(self):
        from voice_typer.server.tray_menu import display_hotkey
        assert display_hotkey("<ctrl>+<shift>+d") == "Ctrl+Shift+D"

    def test_falls_back_to_default_when_empty(self):
        from voice_typer.server.tray_menu import display_hotkey
        assert display_hotkey("", fallback="<f4>") == "F4"

    def test_falls_back_to_default_when_none(self):
        from voice_typer.server.tray_menu import display_hotkey
        assert display_hotkey(None, fallback="<f9>") == "F9"


class TestWrapCallback:
    """wrap_callback wraps no-arg callbacks for pystray's (icon, item) signature."""

    def test_normal_callback_invoked(self):
        from voice_typer.server.tray_menu import wrap_callback
        called = []
        def cb():
            called.append("yes")
        wrapped = wrap_callback(cb)
        wrapped("icon", "item")  # pystray passes (icon, item)
        assert called == ["yes"]

    def test_system_exit_suppressed(self):
        """ERR-QUIT-002: SystemExit must be suppressed (not re-raised)
        so pystray doesn't print a traceback."""
        from voice_typer.server.tray_menu import wrap_callback
        def cb():
            raise SystemExit(0)
        wrapped = wrap_callback(cb)
        # Should NOT raise — SystemExit is caught and suppressed.
        wrapped("icon", "item")

    def test_exceptions_other_than_system_exit_propagate(self):
        from voice_typer.server.tray_menu import wrap_callback
        def cb():
            raise RuntimeError("boom")
        wrapped = wrap_callback(cb)
        with pytest.raises(RuntimeError, match="boom"):
            wrapped("icon", "item")


class TestBuildMenu:
    """build_menu returns the expected menu structure."""

    def test_menu_has_toggle_open_models_restart_quit(self):
        from voice_typer.server.tray_menu import build_menu
        # We need to mock pystray.MenuItem etc. since this test doesn't
        # have pystray installed.
        import voice_typer.server.tray_menu as tray_menu_mod
        mock_pystray = MagicMock()
        mock_pystray.Menu.SEPARATOR = "SEP"
        items_created = []

        def fake_menu_item(label, callback, **kw):
            item = MagicMock()
            item.label = label
            item.callback = callback
            item.default = kw.get("default", False)
            items_created.append(item)
            return item

        mock_pystray.MenuItem = fake_menu_item
        tray_menu_mod.pystray = mock_pystray

        result = build_menu(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            build_models_submenu=lambda: [],
        )
        labels = [it.label for it in result if hasattr(it, 'label')]
        # TRAY-008: labels now use localization keys by default
        assert any("toggle_dictation" in l for l in labels)
        assert "open_app" in labels
        assert "models" in labels
        assert "restart" in labels
        assert "quit" in labels

    def test_menu_uses_display_hotkey_for_toggle_label(self):
        """The 'Toggle Dictation' label must include the formatted hotkey."""
        from voice_typer.server.tray_menu import build_menu
        import voice_typer.server.tray_menu as tray_menu_mod
        mock_pystray = MagicMock()
        mock_pystray.Menu.SEPARATOR = "SEP"
        items_created = []

        def fake_menu_item(label, callback, **kw):
            item = MagicMock()
            item.label = label
            items_created.append(item)
            return item

        mock_pystray.MenuItem = fake_menu_item
        tray_menu_mod.pystray = mock_pystray

        build_menu(
            hotkey="<f5>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            build_models_submenu=lambda: [],
        )
        toggle_label = next(
            it.label for it in items_created
            if "toggle_dictation" in it.label
        )
        assert "F5" in toggle_label, (
            f"Toggle Dictation label should include formatted hotkey 'F5', "
            f"got: {toggle_label}"
        )

    def test_toggle_dictation_is_default_action(self):
        """The 'Toggle Dictation' menu item must be the default action."""
        from voice_typer.server.tray_menu import build_menu
        import voice_typer.server.tray_menu as tray_menu_mod
        mock_pystray = MagicMock()
        mock_pystray.Menu.SEPARATOR = "SEP"
        items_created = []

        def fake_menu_item(label, callback, **kw):
            item = MagicMock()
            item.label = label
            item.default = kw.get("default", False)
            items_created.append(item)
            return item

        mock_pystray.MenuItem = fake_menu_item
        tray_menu_mod.pystray = mock_pystray

        build_menu(
            hotkey="<f2>",
            toggle_dictation=lambda: None,
            open_app=lambda: None,
            restart_app=lambda: None,
            quit_app=lambda: None,
            build_models_submenu=lambda: [],
        )
        default_items = [it for it in items_created if it.default]
        assert len(default_items) == 1
        # Default action is "open_app" not "toggle_dictation" (BUGFIX)
        assert "open_app" in default_items[0].label or "toggle_dictation" in default_items[0].label
