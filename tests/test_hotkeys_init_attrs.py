"""Verify hotkey backends initialize their instance attributes in ``__init__``."""

from __future__ import annotations

import os

import pytest
from voice_typer.server.hotkeys.base import HotkeyBackend
from voice_typer.server.hotkeys.pynput_backend import PynputHotkey
from voice_typer.server.hotkeys.wayland import WaylandHotkey
from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey


# --------------------------------------------------------------------------- #
class TestPynputHotkeyInitAttrs:
    """``PynputHotkey.__init__`` must set ``self._fallback`` to False."""

    def test_fallback_attr_exists_after_init(self) -> None:
        backend = PynputHotkey("<ctrl>+<shift>+d")
        # Must exist *before* start(), diagnose() reads it.
        assert hasattr(backend, "_fallback")
        assert backend._fallback is False

    def test_fallback_attr_type_is_bool(self) -> None:
        backend = PynputHotkey("<f2>")
        assert isinstance(backend._fallback, bool)


# --------------------------------------------------------------------------- #
class TestWindowsNativeHotkeyInitAttrs:
    """``WindowsNativeHotkey.__init__`` must initialize the start()-only attrs."""

    def test_last_error_attr_exists_after_init(self) -> None:
        # Construction must NOT require Windows, start() does, but
        backend = WindowsNativeHotkey("<f2>")
        assert hasattr(backend, "_last_error")
        # Default is None (start() resets it before the registration
        assert backend._last_error is None

    def test_is_caps_lock_hotkey_attr_exists_after_init(self) -> None:
        backend = WindowsNativeHotkey("<caps_lock>")
        assert hasattr(backend, "_is_caps_lock_hotkey")
        assert backend._is_caps_lock_hotkey is False

    def test_prefer_message_loop_first_default_false(self) -> None:
        backend = WindowsNativeHotkey("<esc>")
        assert hasattr(backend, "_prefer_message_loop_first")
        assert backend._prefer_message_loop_first is False

    def test_prefer_message_loop_first_assignment_not_duplicated(self) -> None:
        """The byte-for-byte duplicate ``_prefer_message_loop_first: bool ="""
        import voice_typer.server.hotkeys.windows_native as wn_module

        source = inspect_getsource(wn_module)
        assert source.count("_prefer_message_loop_first: bool = False") == 1


# --------------------------------------------------------------------------- #
class TestWaylandHotkeyInitAttrs:
    """``WaylandHotkey.__init__`` must initialize ``_thread`` to None so"""

    def test_thread_attr_exists_after_init(self) -> None:
        backend = WaylandHotkey("<ctrl>+<shift>+d")
        assert hasattr(backend, "_thread")
        assert backend._thread is None

    def test_stop_without_start_is_safe(self) -> None:
        """``stop()`` must be callable immediately after ``__init__``"""
        backend = WaylandHotkey("<ctrl>+<shift>+d")
        # Must not raise.
        backend.stop()


# --------------------------------------------------------------------------- #
class TestBaseSetTrayDocstring:
    """``HotkeyBackend.set_tray`` docstring must reference"""

    def test_docstring_references_native_backend_adapter(self) -> None:
        doc = HotkeyBackend.set_tray.__doc__ or ""
        assert "_NativeBackendAdapter" in doc

    def test_windows_native_hotkey_does_not_override_set_tray(self) -> None:
        """Sanity-check that the docstring was indeed false before the"""
        # ``set_tray`` is defined on ``HotkeyBackend`` (the base class).
        assert "set_tray" not in WindowsNativeHotkey.__dict__


# --------------------------------------------------------------------------- #
def inspect_getsource(module) -> str:
    """Read the module's source file directly (avoids ``inspect.getsource``"""
    import inspect

    return inspect.getsource(module)


@pytest.fixture(autouse=True)
def _ensure_xdg_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    if "XDG_RUNTIME_DIR" not in os.environ:
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp")
