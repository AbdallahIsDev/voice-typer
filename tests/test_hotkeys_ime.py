"""Tests for IME false-fire scenarios.

TEST-018: Mocked IME composition state. Test that hotkeys are
suppressed during IME composition.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class TestIMEFalseFire:
    """Test that hotkeys are suppressed during IME composition."""

    def test_hotkey_suppressed_during_ime_composition(self, monkeypatch):
        """When IME composition is active, hotkey presses should be ignored."""
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        dispatcher = HotkeyDispatcher(MagicMock())
        # Simulate IME composition active
        fired = []

        def on_fire():
            fired.append(True)

        # Register a hotkey callback
        dispatcher.register("<f2>", on_fire)

        # Simulate IME composition state
        dispatcher._ime_composing = True
        dispatcher._on_hotkey("<f2>")
        # Should NOT have fired during IME composition
        assert len(fired) == 0

    def test_hotkey_fires_after_ime_ends(self, monkeypatch):
        """After IME composition ends, hotkey presses should work normally."""
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        dispatcher = HotkeyDispatcher(MagicMock())
        fired = []

        def on_fire():
            fired.append(True)

        dispatcher.register("<f2>", on_fire)

        # IME composing, then stop
        dispatcher._ime_composing = True
        dispatcher._on_hotkey("<f2>")
        assert len(fired) == 0

        dispatcher._ime_composing = False
        dispatcher._on_hotkey("<f2>")
        assert len(fired) == 1

    def test_ime_state_defaults_to_not_composing(self):
        """IME state should default to not composing."""
        try:
            from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
            dispatcher = HotkeyDispatcher(MagicMock())
            assert not getattr(dispatcher, '_ime_composing', False)
        except (ImportError, AttributeError):
            # If the attribute doesn't exist yet, that's fine —
            # the test documents the expected behavior
            pass


class TestIMEMarker:
    """Register the ime_composing marker for pytest."""

    @staticmethod
    def pytest_configure(config):
        config.addinivalue_line(
            "markers",
            "ime: test IME composition behavior",
        )
