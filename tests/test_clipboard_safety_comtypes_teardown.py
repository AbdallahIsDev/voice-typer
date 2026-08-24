"""TK-35: Win32 comtypes teardown coverage for ``_is_safe_paste_target_impl``.

``voice_typer/server/clipboard/safety.py:44`` hoists ``CoInitialize`` /
``CoUninitialize`` around the UIA password-field + contentEditable
checks (the paste-target safety gate). The PERF contract (recorded in
the function docstring) is:

* ``CoInitialize()`` is called at most once per paste attempt, and
* ``CoUninitialize()`` is ALWAYS called in the ``finally`` block when
  ``CoInitialize`` succeeded — even when the password-field check
  returns ``False`` early (paste blocked) or the contentEditable check
  raises.

A regression that drops the ``finally`` teardown leaks a COM
initialization on the calling thread for the lifetime of the process —
on the paste hot path this is a per-paste leak that accumulates.

Pre-fix this branch was entirely untested: the 110+ mocked Win32 tests
in ``tests/clipboard/win32/test_win32_target_safety.py`` cover ``_is_password_field``
/ ``_is_elevated_target`` etc. directly, but no test ever invoked
``_is_safe_paste_target_impl`` with the comtypes path active, so the
``CoInitialize``/``CoUninitialize`` pairing was dead code from the
tests' point of view.

These tests mock ``ctypes.windll`` (the ``fake_win32`` fixture pattern
from ``tests/clipboard/win32/ (split files)``) and inject a fake
``comtypes`` module into ``sys.modules`` so the Windows branch executes
on Linux. All clipboard helpers are patched at the PACKAGE level
(``voice_typer.server.clipboard.X``) per the safety.py design contract
— the implementation looks symbols up via ``_cb.X`` at call time.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard.safety import _is_safe_paste_target_impl  # noqa: E402


@pytest.fixture()
def fake_win32(monkeypatch):
    """Mock ``ctypes.windll`` + the clipboard package so the Win32
    ``_is_safe_paste_target_impl`` branch runs on Linux.

    Returns a dict of the mocks so individual tests can steer the
    password-field / contentEditable / comtypes outcomes.
    """
    mock_user32 = MagicMock(name="user32")
    mock_user32.GetForegroundWindow.return_value = 0x1234  # non-zero hwnd
    mock_windll = MagicMock(name="windll")
    mock_windll.user32 = mock_user32

    # ``_get_uia_focused_element`` is patched to a mock so the real
    # (comtypes.client-based) UIA fetch never runs — the comtypes
    # lifecycle we assert on is the one managed by
    # ``_is_safe_paste_target_impl`` itself.
    monkeypatch.setattr(clip_mod, "is_windows", lambda: True)
    monkeypatch.setattr(clip_mod, "is_macos", lambda: False)
    monkeypatch.setattr(clip_mod, "is_linux", lambda: False)
    monkeypatch.setattr(clip_mod, "_is_elevated_target", lambda hwnd: False)
    monkeypatch.setattr(clip_mod, "_get_uia_focused_element", MagicMock(name="focused"))
    monkeypatch.setattr(clip_mod, "log", MagicMock(name="log"))

    with patch("ctypes.windll", mock_windll, create=True):
        yield {"user32": mock_user32, "windll": mock_windll}


def _install_fake_comtypes() -> tuple[MagicMock, MagicMock]:
    """Inject a fake ``comtypes`` module into ``sys.modules`` and return
    ``(fake_comtypes, fake_client)``.

    ``_is_safe_paste_target_impl`` does a bare ``import comtypes`` inside
    the try block, so a ``sys.modules`` entry is sufficient — no
    ``builtins.__import__`` patching needed (the outer ``import ctypes``
    must keep working).
    """
    fake_comtypes = MagicMock(name="comtypes")
    fake_client = MagicMock(name="comtypes.client")
    fake_comtypes.client = fake_client
    return fake_comtypes, fake_client


class TestComtypesTeardown:
    """``CoUninitialize`` must run in the ``finally`` whenever
    ``CoInitialize`` succeeded — regardless of the paste outcome."""

    def test_password_field_blocks_paste_and_still_tears_down_com(self, fake_win32):
        """Password field focused → paste blocked (False) BUT the
        finally-block ``CoUninitialize`` still runs exactly once.

        This is the headline teardown contract: the early ``return
        False`` from the password-field branch must NOT skip the
        finally block. A regression that moved the return outside the
        ``try/finally`` would leak one ``CoInitialize`` per paste.
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
        """Safe target (no password field, no contentEditable) → True,
        and the CoInitialize/CoUninitialize pair is balanced (1:1)."""
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
        """The contentEditable probe is fail-open (logs, continues) and
        the finally teardown must still run when it raises."""
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
        """comtypes unavailable (ImportError) → no CoInitialize, no
        CoUninitialize; the function falls through to the
        password-field heuristic and still returns a sane result."""
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "_is_password_field", lambda focused, hwnd: False),
            patch.object(clip_mod, "_is_content_editable", lambda focused: False),
        ):
            result = _is_safe_paste_target_impl()

        assert result is True, "no comtypes → heuristic path must still allow paste"

    def test_coinitialize_raising_skips_teardown_but_proceeds(self, fake_win32):
        """``CoInitialize()`` raising (COM init failed) is logged and
        tolerated: ``com_initialized`` stays False so the finally does
        NOT call ``CoUninitialize`` (there is nothing to uninitialize),
        and the password-field check still runs (it retries
        CoInitialize idempotently)."""
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
