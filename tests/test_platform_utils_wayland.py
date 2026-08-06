"""Focused tests for :func:`voice_typer.server.platform_utils.is_wayland_session`.

Covers the env-var + platform heuristic with monkeypatched ``XDG_SESSION_TYPE``
and ``WAYLAND_DISPLAY`` (and ``sys.platform`` for the platform gate). These
tests pin the contract that the four Wayland-detection call sites
(``clipboard/linux.py`` broad mode, ``hotkeys/factory.py``,
``hotkeys/native_adapter.py``, ``startup_sequence.py``) rely on.
"""

from __future__ import annotations

import pytest
from voice_typer.server.platform_utils import is_wayland_session

# Path to the ``sys`` module bound in ``platform_utils``'s namespace —
# patching ``sys.platform`` there propagates to ``is_wayland_session``.
_PU_SYS = "voice_typer.server.platform_utils.sys.platform"


class TestIsWaylandSession:
    """Verify :func:`is_wayland_session` across platform + env-var matrices."""

    @pytest.mark.parametrize(
        "session_type",
        ["wayland", "Wayland", "WAYLAND"],
    )
    def test_xdg_session_type_wayland_on_linux(self, monkeypatch, session_type):
        """Linux + XDG_SESSION_TYPE=wayland (any case) → True."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", session_type)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert is_wayland_session() is True

    def test_wayland_display_set_on_linux(self, monkeypatch):
        """Linux + WAYLAND_DISPLAY set (no XDG_SESSION_TYPE) → True."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert is_wayland_session() is True

    def test_both_env_vars_set_on_linux(self, monkeypatch):
        """Linux + both env vars set → True (XDG check short-circuits)."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert is_wayland_session() is True

    def test_xdg_x11_on_linux_without_wayland_display(self, monkeypatch):
        """Linux + XDG_SESSION_TYPE=x11 + no WAYLAND_DISPLAY → False."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert is_wayland_session() is False

    def test_no_env_vars_on_linux(self, monkeypatch):
        """Linux + no env vars → False."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert is_wayland_session() is False

    def test_wayland_display_on_macos(self, monkeypatch):
        """macOS + WAYLAND_DISPLAY set → False (Wayland is Linux-only)."""
        monkeypatch.setattr(_PU_SYS, "darwin")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert is_wayland_session() is False

    def test_xdg_wayland_on_macos(self, monkeypatch):
        """macOS + XDG_SESSION_TYPE=wayland → False (Wayland is Linux-only)."""
        monkeypatch.setattr(_PU_SYS, "darwin")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert is_wayland_session() is False

    def test_windows_always_false(self, monkeypatch):
        """Windows + any env vars → False (Windows can't be Wayland)."""
        monkeypatch.setattr(_PU_SYS, "win32")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert is_wayland_session() is False

    def test_windows_no_env_vars(self, monkeypatch):
        """Windows + no env vars → False."""
        monkeypatch.setattr(_PU_SYS, "win32")
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert is_wayland_session() is False

    def test_empty_xdg_session_type_on_linux(self, monkeypatch):
        """Linux + XDG_SESSION_TYPE="" (empty) + no WAYLAND_DISPLAY → False."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.setenv("XDG_SESSION_TYPE", "")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert is_wayland_session() is False

    def test_returns_bool_not_truthy_value(self, monkeypatch):
        """Result must be a real ``bool`` (not e.g. a str from os.environ.get)."""
        monkeypatch.setattr(_PU_SYS, "linux")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        result = is_wayland_session()
        assert result is True
        assert isinstance(result, bool)
