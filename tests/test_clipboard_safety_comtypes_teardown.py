"""TK-35: Win32 comtypes teardown coverage for ``_is_safe_paste_target_impl``."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard.safety import _is_safe_paste_target_impl  # noqa: E402


@pytest.fixture()
def fake_win32(monkeypatch):
    """Mock ``ctypes.windll`` + the clipboard package so the Win32"""
    mock_user32 = MagicMock(name="user32")
    mock_user32.GetForegroundWindow.return_value = 0x1234  # non-zero hwnd
    mock_windll = MagicMock(name="windll")
    mock_windll.user32 = mock_user32

    # ``_get_uia_focused_element`` is patched to a mock so the real
    monkeypatch.setattr(clip_mod, "is_windows", lambda: True)
    monkeypatch.setattr(clip_mod, "is_macos", lambda: False)
    monkeypatch.setattr(clip_mod, "is_linux", lambda: False)
    monkeypatch.setattr(clip_mod, "_is_elevated_target", lambda hwnd: False)
    monkeypatch.setattr(clip_mod, "_get_uia_focused_element", MagicMock(name="focused"))
    monkeypatch.setattr(clip_mod, "log", MagicMock(name="log"))

    with patch("ctypes.windll", mock_windll, create=True):
        yield {"user32": mock_user32, "windll": mock_windll}


def _install_fake_comtypes() -> tuple[MagicMock, MagicMock]:
    """Inject a fake ``comtypes`` module into ``sys.modules`` and return"""
    fake_comtypes = MagicMock(name="comtypes")
    fake_client = MagicMock(name="comtypes.client")
    fake_comtypes.client = fake_client
    return fake_comtypes, fake_client


class TestComtypesTeardown:
    """``CoUninitialize`` must run in the ``finally`` whenever"""

    def test_password_field_blocks_paste_and_still_tears_down_com(self, fake_win32):
        """
        finally-block ``CoUninitialize`` still runs exactly once.
        This is the headline teardown contract: the early ``return
        """
        fake_comtypes, _fake_client = _install_fake_comtypes()
        password_field = MagicMock(return_value=True)
        content_editable = MagicMock(return_value=False)

        with (
            patch.dict(sys.modules, {"comtypes": fake_comtypes, "comtypes.client": _fake_client}),
            patch.object(clip_mod, "_is_password_field", password_field),
            patch.object(clip_mod, "_is_content_editable", content_editable),
        ):
            result = _is_safe_paste_target_impl()

        assert result is False, "password field must block paste"
        fake_comtypes.CoInitialize.assert_called_once_with()
        fake_comtypes.CoUninitialize.assert_called_once_with()
        # Early return means the contentEditable probe never ran.
        content_editable.assert_not_called()

    def test_safe_target_tears_down_com(self, fake_win32):
        """Safe target (no password field, no contentEditable) → True,"""
        fake_comtypes, _fake_client = _install_fake_comtypes()

        with (
            patch.dict(sys.modules, {"comtypes": fake_comtypes, "comtypes.client": _fake_client}),
            patch.object(clip_mod, "_is_password_field", lambda focused, hwnd: False),
            patch.object(clip_mod, "_is_content_editable", lambda focused: False),
        ):
            result = _is_safe_paste_target_impl()

        assert result is True
        fake_comtypes.CoInitialize.assert_called_once_with()
        fake_comtypes.CoUninitialize.assert_called_once_with()

    def test_content_editable_probe_raising_still_tears_down_com(self, fake_win32):
        """The contentEditable probe is fail-open (logs, continues) and"""
        fake_comtypes, _fake_client = _install_fake_comtypes()

        def _boom(_focused):
            raise RuntimeError("UIA contentEditable probe failed")

        with (
            patch.dict(sys.modules, {"comtypes": fake_comtypes, "comtypes.client": _fake_client}),
            patch.object(clip_mod, "_is_password_field", lambda focused, hwnd: False),
            patch.object(clip_mod, "_is_content_editable", _boom),
        ):
            result = _is_safe_paste_target_impl()

        assert result is True, "contentEditable failure must fail open"
        fake_comtypes.CoInitialize.assert_called_once_with()
        fake_comtypes.CoUninitialize.assert_called_once_with()

    def test_comtypes_import_error_skips_teardown(self, fake_win32):
        """comtypes unavailable (ImportError) → no CoInitialize, no"""
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "_is_password_field", lambda focused, hwnd: False),
            patch.object(clip_mod, "_is_content_editable", lambda focused: False),
        ):
            result = _is_safe_paste_target_impl()

        assert result is True, "no comtypes → heuristic path must still allow paste"

    def test_coinitialize_raising_skips_teardown_but_proceeds(self, fake_win32):
        """tolerated: ``com_initialized`` stays False so the finally does"""
        fake_comtypes, _fake_client = _install_fake_comtypes()
        fake_comtypes.CoInitialize.side_effect = OSError("COM init failed")

        with (
            patch.dict(sys.modules, {"comtypes": fake_comtypes, "comtypes.client": _fake_client}),
            patch.object(clip_mod, "_is_password_field", lambda focused, hwnd: False),
            patch.object(clip_mod, "_is_content_editable", lambda focused: False),
        ):
            result = _is_safe_paste_target_impl()

        assert result is True
        fake_comtypes.CoInitialize.assert_called_once_with()
        fake_comtypes.CoUninitialize.assert_not_called()
