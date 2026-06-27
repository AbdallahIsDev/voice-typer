"""Tests for IME composition state and hotkey interaction.

TEST-018: Mock-based test that simulates IME composition state and
verifies hotkeys are ignored during composition.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestIMEHotkeySuppression:
    """Test that hotkey events are suppressed during IME composition."""

    def test_hotkey_ignored_during_ime_composition(self, monkeypatch):
        """When IME composition is active, hotkey should be ignored."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        except ImportError:
            pytest.skip("hotkey_dispatcher module not available")

        app = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        fired = []

        def on_hotkey():
            fired.append(True)

        dispatcher.register("<f2>", on_hotkey)

        # Simulate IME composing state
        dispatcher._ime_composing = True
        dispatcher._on_hotkey("<f2>")

        # Should NOT have fired
        assert len(fired) == 0

    def test_hotkey_fires_after_ime_composition_ends(self, monkeypatch):
        """After IME composition ends, hotkey should fire normally."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        except ImportError:
            pytest.skip("hotkey_dispatcher module not available")

        app = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        fired = []

        def on_hotkey():
            fired.append(True)

        dispatcher.register("<f2>", on_hotkey)

        # Start composing, fire (should be ignored)
        dispatcher._ime_composing = True
        dispatcher._on_hotkey("<f2>")
        assert len(fired) == 0

        # Stop composing, fire (should work)
        dispatcher._ime_composing = False
        dispatcher._on_hotkey("<f2>")
        assert len(fired) == 1

    def test_ime_composing_defaults_to_false(self):
        """IME composing state should default to False."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        except ImportError:
            pytest.skip("hotkey_dispatcher module not available")

        dispatcher = HotkeyDispatcher(MagicMock())
        assert not getattr(dispatcher, "_ime_composing", False)

    def test_multiple_hotkeys_during_ime_all_suppressed(self):
        """Multiple different hotkeys during IME composition should all be suppressed."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        except ImportError:
            pytest.skip("hotkey_dispatcher module not available")

        app = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        f2_fired = []
        f9_fired = []

        dispatcher.register("<f2>", lambda: f2_fired.append(True))
        dispatcher.register("<f9>", lambda: f9_fired.append(True))

        dispatcher._ime_composing = True
        dispatcher._on_hotkey("<f2>")
        dispatcher._on_hotkey("<f9>")

        assert len(f2_fired) == 0
        assert len(f9_fired) == 0

        # After IME ends, both should work
        dispatcher._ime_composing = False
        dispatcher._on_hotkey("<f2>")
        dispatcher._on_hotkey("<f9>")
        assert len(f2_fired) == 1
        assert len(f9_fired) == 1

    def test_ime_toggle_does_not_fire_callback(self):
        """Toggling _ime_composing should not fire any registered callbacks."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        except ImportError:
            pytest.skip("hotkey_dispatcher module not available")

        app = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        fired = []

        dispatcher.register("<f2>", lambda: fired.append(True))

        # Toggle IME state multiple times without firing hotkey
        dispatcher._ime_composing = True
        dispatcher._ime_composing = False
        dispatcher._ime_composing = True
        dispatcher._ime_composing = False

        # No callback should have fired
        assert len(fired) == 0

    def test_rapid_ime_toggle_and_hotkey(self):
        """Rapid toggling of IME state with hotkey fires should be consistent."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        except ImportError:
            pytest.skip("hotkey_dispatcher module not available")

        app = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        fired = []

        dispatcher.register("<f2>", lambda: fired.append(True))

        # Rapid toggle: composing → fire → not composing → fire
        dispatcher._ime_composing = True
        dispatcher._on_hotkey("<f2>")  # suppressed
        dispatcher._ime_composing = False
        dispatcher._on_hotkey("<f2>")  # fires

        assert len(fired) == 1  # Only the non-IME hotkey fired
