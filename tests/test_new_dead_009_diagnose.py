"""Regression tests for NEW-DEAD-009: HotkeyBackend.diagnose no longer @abstractmethod.

Previously ``diagnose`` was declared ``@abstractmethod``, forcing every
subclass to implement a debug string even though only test callers
invoke it.  The fix provides a default no-op implementation so new
backends don't have to implement it just to satisfy the Protocol.
"""
from __future__ import annotations

import inspect

import pytest

from voice_typer.server.hotkeys import HotkeyBackend


class TestDiagnoseNotAbstract:
    """NEW-DEAD-009: diagnose must not be @abstractmethod."""

    def test_diagnose_has_default_implementation(self):
        """The HotkeyBackend base class must provide a default
        ``diagnose`` implementation that returns an empty string.
        """
        assert hasattr(HotkeyBackend, "diagnose"), (
            "HotkeyBackend must have a diagnose method"
        )
        source = inspect.getsource(HotkeyBackend.diagnose)
        assert 'return ""' in source, (
            "HotkeyBackend.diagnose must have a default implementation "
            "that returns an empty string"
        )

    def test_diagnose_not_marked_abstract(self):
        """The method must not be decorated with @abstractmethod."""
        source = inspect.getsource(HotkeyBackend)
        diag_start = source.find("def diagnose")
        assert diag_start != -1, "diagnose method not found in HotkeyBackend source"
        # Look at the 5 lines before the def to check for @abstractmethod.
        lines_before = source[:diag_start].splitlines()[-5:]
        for line in lines_before:
            assert "@abstractmethod" not in line, (
                "HotkeyBackend.diagnose must not be decorated with @abstractmethod "
                "(NEW-DEAD-009: should have a default implementation so new "
                "backends don't have to implement it)"
            )

    def test_subclasses_can_skip_diagnose_override(self):
        """A new subclass that doesn't override diagnose must be
        instantiable (with the other abstract methods implemented).
        """
        class MinimalBackend(HotkeyBackend):
            def start(self, callback):
                pass

            def register(self, hotkey, callback, on_release=None):
                pass

            def unregister(self, hotkey):
                pass

            def stop(self):
                pass

            def is_alive(self):
                return False

            # Note: NO diagnose override.

        backend = MinimalBackend("<f2>")
        assert backend.diagnose() == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
