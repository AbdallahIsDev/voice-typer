"""regression test for pystray private ``_icon_handle``."""

from __future__ import annotations

import importlib
import sys

import pytest
from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_types import AppState  # noqa: E402


def _load_real_pystray():
    """Load the *real* ``pystray`` package, bypassing the autouse mock."""
    saved = sys.modules.get("pystray")
    keys_to_evict = [k for k in list(sys.modules.keys()) if k == "pystray" or k.startswith("pystray.")]
    for k in keys_to_evict:
        del sys.modules[k]
    try:
        return importlib.import_module("pystray")
    except Exception:
        # ImportError, pystray not installed.
        return None
    finally:
        # Restore the autouse-mock so subsequent tests in this
        if saved is not None:
            sys.modules["pystray"] = saved


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "pystray sets the private `_icon_handle` instance attribute only in "
        "its Win32 backend (pystray/_win32.py); the darwin/Xorg backends "
        "never have it. The DestroyIcon workaround this test guards is a "
        "Win32-only bug workaround (tray.py:_apply_state), so the attribute "
        "can only be verified on Windows."
    ),
)
def test_pystray_icon_class_exposes_icon_handle():
    """Stopgap regression: assert ``pystray.Icon._icon_handle`` still exists."""
    pystray = _load_real_pystray()
    if pystray is None:
        # Real pystray isn't installed in this sandbox (e.g. Linux
        pytest.skip("real pystray not installed in this environment, cannot introspect pystray.Icon._icon_handle")

    # The DestroyIcon workaround in ``tray.py:_apply_state`` writes
    try:
        probe_icon = pystray.Icon("probe")
    except Exception:
        pytest.skip("cannot construct pystray.Icon in this environment, cannot verify _icon_handle")
    assert hasattr(probe_icon, "_icon_handle"), (
        "pystray.Icon instances no longer expose the private `_icon_handle` "
        "attribute. The DestroyIcon workaround in "
        "voice_typer/server/tray.py:_apply_state is broken. See "
        "S2- / TODO S2-replace the private attribute "
        "access with a public `reset_icon_handle()` API and bump "
        "pystray to the release that exposes it."
    )


class _FakeIcon:
    """Plain-Python stand-in for ``pystray.Icon`` used by the fallback tests."""

    def __init__(self, *, has_icon_handle: bool) -> None:
        self._has_icon_handle = has_icon_handle
        if has_icon_handle:
            self._icon_handle_value: object = "sentinel-handle-value"
        self.title: object = None

    # --- icon property ------------------------------------------------
    @property
    def icon(self) -> object:
        raise OSError("simulated WinError 1402 (icon getter)")

    @icon.setter
    def icon(self, value: object) -> None:
        raise OSError("simulated WinError 1402 (icon setter)")

    # --- _icon_handle property ---------------------------------------
    @property
    def _icon_handle(self) -> object:
        if not self._has_icon_handle:
            raise AttributeError("_icon_handle")
        return self._icon_handle_value

    @_icon_handle.setter
    def _icon_handle(self, value: object) -> None:
        if not self._has_icon_handle:
            raise AttributeError("_icon_handle")
        self._icon_handle_value = value


def _make_minimal_tray_with_icon(icon: object) -> TrayIcon:
    """Build a ``TrayIcon`` whose ``self._icon`` is ``icon``."""
    import threading

    tray = TrayIcon.__new__(TrayIcon)
    tray._icon = icon
    tray._state = AppState.IDLE
    tray._recording_started_at = None
    tray._cpu_fallback_active = False  # SK-b
    tray._config = None  # model name source
    tray._hotkey = None  # falls through to "<f2>" default
    # _apply_state now acquires _icon_lock around the icon-write pair.
    tray._icon_lock = threading.RLock()
    # _apply_state reads _last_applied_state for the cache-skip.
    tray._last_applied_state = None
    return tray


def test_apply_state_warns_when_icon_handle_missing(caplog):
    """Graceful fallback: ``_apply_state`` warns when workaround can't fire."""
    icon = _FakeIcon(has_icon_handle=False)
    # Sanity check: the fake icon should report no ``_icon_handle``
    assert hasattr(icon, "_icon_handle") is False, (
        "test fixture broken: _FakeIcon(has_icon_handle=False) should make hasattr(icon, '_icon_handle') return False"
    )

    tray = _make_minimal_tray_with_icon(icon)

    # Must not raise, the OSError is caught and the missing
    tray._apply_state(AppState.RECORDING, "recording")

    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("_icon_handle" in r.getMessage() for r in warning_records), (
        "Expected a WARNING log mentioning `_icon_handle` when the "
        "DestroyIcon workaround can't fire (attribute missing). "
        "Got records: " + repr([(r.levelname, r.getMessage()) for r in caplog.records])
    )


def test_apply_state_clears_icon_handle_when_present(caplog):
    """Belt-and-braces: workaround fires when ``_icon_handle`` IS present."""
    icon = _FakeIcon(has_icon_handle=True)
    assert hasattr(icon, "_icon_handle") is True, (
        "test fixture broken: _FakeIcon(has_icon_handle=True) should make hasattr(icon, '_icon_handle') return True"
    )

    tray = _make_minimal_tray_with_icon(icon)
    tray._apply_state(AppState.TRANSCRIBING, "transcribing")

    # The workaround must have cleared the handle.
    assert icon._icon_handle is None, (
        "Expected `_icon_handle` to be set to None after OSError "
        "( / GT-E1-8 workaround), but it is: " + repr(icon._icon_handle)
    )
    # And NO warning should have been logged (the workaround
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert not any("_icon_handle" in r.getMessage() for r in warning_records), (
        "Did not expect a WARNING log when the workaround fired "
        "normally (the warning is reserved for the missing-attribute "
        "fallback). Got warnings: " + repr([(r.levelname, r.getMessage()) for r in warning_records])
    )
