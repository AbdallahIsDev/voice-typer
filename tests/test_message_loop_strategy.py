"""Focused behavior tests for the WM_HOTKEY message-loop strategy."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
from types import SimpleNamespace

from voice_typer.server.hotkeys.win32_vk import _WM_HOTKEY
from voice_typer.server.hotkeys.windows.message_loop_strategy import run_message_loop

_OTHER_MSG = 0x0042
_HOTKEY_ID = 7


class _NoArgtypesCallable:
    """Callable that rejects attribute assignment, simulating ctypes setup failure."""

    __slots__ = ()

    def __call__(self, *args):
        return 0


class _FakeUser32:
    """Scripted GetMessageW: each entry is (ret, message, wparam)."""

    def __init__(self, script: list[tuple[int, int, int]], harness: SimpleNamespace) -> None:
        self._script = list(script)
        self._harness = harness
        self.translate_calls = 0
        self.dispatch_calls = 0

        def get_message(msg_ptr, _hwnd, _lo, _hi) -> int:
            if not self._script:
                self._harness._stop_event.set()
                return 0
            ret, message, wparam = self._script.pop(0)
            msg = ctypes.cast(msg_ptr, ctypes.POINTER(ctypes.wintypes.MSG)).contents
            msg.message = message
            msg.wParam = wparam
            if not self._script:
                self._harness._stop_event.set()
            return ret

        def translate_message(_msg_ptr) -> int:
            self.translate_calls += 1
            return 1

        def dispatch_message(_msg_ptr) -> int:
            self.dispatch_calls += 1
            return 0

        # Plain-function attributes (not bound methods): assigning
        # ``.argtypes``/``.restype`` works, mirroring ctypes func pointers.
        self.GetMessageW = get_message
        self.TranslateMessage = translate_message
        self.DispatchMessageW = dispatch_message


def _make_harness(script: list[tuple[int, int, int]], **overrides) -> SimpleNamespace:
    harness = SimpleNamespace(
        _user32=None,
        _stop_event=threading.Event(),
        _vk=0x42,
        _modifiers=0x02,
        _hotkey_id=_HOTKEY_ID,
        hotkey_str="ctrl+b",
        _using_polling=False,
        _run_polling_loop=None,
        **overrides,
    )
    harness._user32 = _FakeUser32(script, harness)
    return harness


class TestPollingFallbacks:
    def test_missing_user32_falls_back_to_polling(self) -> None:
        calls: list = []
        harness = SimpleNamespace(
            _user32=None,
            _using_polling=False,
            _run_polling_loop=lambda callback: calls.append(callback),
        )
        callback = lambda: None  # noqa: E731
        run_message_loop(harness, callback)
        assert harness._using_polling is True
        assert calls == [callback]

    def test_argtype_setup_failure_falls_back_to_polling(self) -> None:
        calls: list = []
        fake = SimpleNamespace(GetMessageW=_NoArgtypesCallable())
        harness = SimpleNamespace(
            _user32=fake,
            _using_polling=False,
            _run_polling_loop=lambda callback: calls.append(callback),
        )
        callback = lambda: None  # noqa: E731
        run_message_loop(harness, callback)
        assert harness._using_polling is True
        assert len(calls) == 1


class TestLoopExits:
    def test_wm_quit_breaks_without_firing(self) -> None:
        fired: list = []
        harness = _make_harness([(0, 0, 0)])
        run_message_loop(harness, lambda: fired.append(True))
        assert fired == []

    def test_getmessage_error_breaks_without_firing(self) -> None:
        fired: list = []
        harness = _make_harness([(-1, 0, 0)])
        run_message_loop(harness, lambda: fired.append(True))
        assert fired == []


class TestHotkeyDispatch:
    def test_matching_hotkey_fires_callback(self) -> None:
        fired: list = []
        harness = _make_harness([(1, _WM_HOTKEY, _HOTKEY_ID)])
        run_message_loop(harness, lambda: fired.append(True))
        assert fired == [True]
        assert harness._user32.translate_calls == 1
        assert harness._user32.dispatch_calls == 1

    def test_wrong_id_does_not_fire(self) -> None:
        fired: list = []
        harness = _make_harness([(1, _WM_HOTKEY, _HOTKEY_ID + 1)])
        run_message_loop(harness, lambda: fired.append(True))
        assert fired == []

    def test_other_message_does_not_fire(self) -> None:
        fired: list = []
        harness = _make_harness([(1, _OTHER_MSG, 0)])
        run_message_loop(harness, lambda: fired.append(True))
        assert fired == []

    def test_callback_exception_is_shielded_and_loop_continues(self) -> None:
        calls: list = []

        def flaky() -> None:
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("boom")

        harness = _make_harness(
            [
                (1, _WM_HOTKEY, _HOTKEY_ID),
                (1, _WM_HOTKEY, _HOTKEY_ID),
            ]
        )
        run_message_loop(harness, flaky)
        assert calls == [True, True]

    def test_low_level_hook_mode_ignores_wm_hotkey(self) -> None:
        fired: list = []
        harness = _make_harness([(1, _WM_HOTKEY, _HOTKEY_ID)])
        run_message_loop(harness, lambda: fired.append(True), low_level_hook=True)
        assert fired == []
