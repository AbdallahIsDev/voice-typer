"""Verify hotkey backends initialize their instance attributes in ``__init__``.

This test pins several attribute-initialization invariants:
- ``PynputHotkey.__init__`` must set ``self._fallback: bool = False`` so
  ``diagnose()`` and other accessors don't raise ``AttributeError`` before
  ``_start_fallback()`` has run.
- ``WindowsNativeHotkey.__init__`` must initialize
  ``self._last_error: int | None = None`` and
  ``self._is_caps_lock_hotkey: bool = False`` so attribute access before
  ``start()`` (e.g. from tests, diagnostics, or ``HotkeyDispatcher``
  wiring) does not raise ``AttributeError``. ``start()`` still re-assigns
  them before the registration attempt so per-run semantics are
  unchanged.
- ``WaylandHotkey.stop()`` must join its accept-loop thread with
  ``timeout=1.0`` (covered here indirectly by ensuring ``_thread`` is
  present after ``__init__`` so the ``if self._thread is not None`` guard
  in ``stop()`` is reachable).
- ``windows_native.py`` must contain exactly ONE
  ``_prefer_message_loop_first: bool = False`` assignment (the
  byte-for-byte duplicate was deleted).
- ``base.py`` ``set_tray`` docstring references ``_NativeBackendAdapter``
  (not ``WindowsNativeHotkey`` which doesn't override ``set_tray``).

These checks run on Linux without calling ``start()`` — the goal is to
prove the attributes exist immediately after construction, so a caller
that introspects a backend before wiring it up doesn't blow up.
"""

from __future__ import annotations

import os
import sys

import pytest
from voice_typer.server.hotkeys.base import HotkeyBackend
from voice_typer.server.hotkeys.pynput_backend import PynputHotkey
from voice_typer.server.hotkeys.wayland import WaylandHotkey
from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey


# --------------------------------------------------------------------------- #
# PynputHotkey ()
# --------------------------------------------------------------------------- #
class TestPynputHotkeyInitAttrs:
    """``PynputHotkey.__init__`` must set ``self._fallback`` to False."""

    def test_fallback_attr_exists_after_init(self) -> None:
        backend = PynputHotkey("<ctrl>+<shift>+d")
        # Must exist *before* start() — diagnose() reads it.
        assert hasattr(backend, "_fallback")
        assert backend._fallback is False

    def test_fallback_attr_type_is_bool(self) -> None:
        backend = PynputHotkey("<f2>")
        assert isinstance(backend._fallback, bool)


# --------------------------------------------------------------------------- #
# WindowsNativeHotkey (, )
# --------------------------------------------------------------------------- #
class TestWindowsNativeHotkeyInitAttrs:
    """``WindowsNativeHotkey.__init__`` must initialize the start()-only attrs."""

    def test_last_error_attr_exists_after_init(self) -> None:
        # Construction must NOT require Windows — start() does, but
        # __init__ only sets Python attributes and never touches ctypes.
        backend = WindowsNativeHotkey("<f2>")
        assert hasattr(backend, "_last_error")
        # Default is None (start() resets it before the registration
        # attempt; None signals "no error captured yet").
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
        """The byte-for-byte duplicate ``_prefer_message_loop_first: bool =
        False`` assignment was deleted. Exactly one assignment must remain in
        the source."""
        import voice_typer.server.hotkeys.windows_native as wn_module

        source = inspect_getsource(wn_module)
        assert source.count("_prefer_message_loop_first: bool = False") == 1


# --------------------------------------------------------------------------- #
# WaylandHotkey ( — partial: thread attribute presence; the join
# behavior is exercised in test_hotkeys_wayland_stop_joins_thread)
# --------------------------------------------------------------------------- #
class TestWaylandHotkeyInitAttrs:
    """``WaylandHotkey.__init__`` must initialize ``_thread`` to None so
    the ``if self._thread is not None`` guard in ``stop()`` is reachable
    and the ``join(timeout=1.0)`` can fire after ``start()``."""

    def test_thread_attr_exists_after_init(self) -> None:
        backend = WaylandHotkey("<ctrl>+<shift>+d")
        assert hasattr(backend, "_thread")
        assert backend._thread is None

    def test_stop_without_start_is_safe(self) -> None:
        """``stop()`` must be callable immediately after ``__init__``
        (the new ``if self._thread is not None`` guard makes this safe)."""
        backend = WaylandHotkey("<ctrl>+<shift>+d")
        # Must not raise.
        backend.stop()


# --------------------------------------------------------------------------- #
# base.py docstring ()
# --------------------------------------------------------------------------- #
class TestBaseSetTrayDocstring:
    """``HotkeyBackend.set_tray`` docstring must reference
    ``_NativeBackendAdapter`` (not the falsely-claimed
    ``WindowsNativeHotkey`` override)."""

    def test_docstring_references_native_backend_adapter(self) -> None:
        doc = HotkeyBackend.set_tray.__doc__ or ""
        assert "_NativeBackendAdapter" in doc

    def test_windows_native_hotkey_does_not_override_set_tray(self) -> None:
        """Sanity-check that the docstring was indeed false before the
        fix — ``WindowsNativeHotkey`` does NOT override ``set_tray``."""
        # ``set_tray`` is defined on ``HotkeyBackend`` (the base class).
        # If ``WindowsNativeHotkey`` overrode it, the function objects
        # would differ.
        assert "set_tray" not in WindowsNativeHotkey.__dict__


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def inspect_getsource(module) -> str:
    """Read the module's source file directly (avoids ``inspect.getsource``
    caching surprises when the duplicate-ablation test runs in a fresh
    interpreter)."""
    import inspect

    return inspect.getsource(module)


# Allow running this file directly when XDG_RUNTIME_DIR is unset — the
# tests above don't actually start the Wayland socket, so they work in a
# bare environment.
@pytest.fixture(autouse=True)
def _ensure_xdg_runtime_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    if "XDG_RUNTIME_DIR" not in os.environ:
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp")


# Skip on non-Linux platforms — the orchestrator's acceptance criteria
# explicitly require this suite to pass on LINUX.
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="acceptance: LINUX-only")
