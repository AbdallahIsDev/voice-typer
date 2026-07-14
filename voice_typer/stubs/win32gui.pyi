# ruff: noqa: A001, A002, N802, N803, N816
# RW-6 (pyrefly): stub for the `win32gui` module (Windows-only, shipped
# via the `pywin32` distribution).
#
# `win32gui` is never installed on the Linux CI runner, so pyrefly
# reports `missing-import` for the lazy `import win32gui` inside
# `voice_typer/server/hotkeys.py::_run_win32_message_loop`. The runtime
# code wraps the import in `try/except ImportError`, so this stub only
# needs to declare the small surface actually used (the message-pump
# API). All symbols are typed `Any` because the surrounding code is
# platform-guarded and we do not need pyrefly to verify call sites.
from typing import Any

# Message-pump APIs used by hotkeys.py.
def PumpWaitingMessages() -> int: ...
def PeekMessage(
    lpMsg: Any,
    hwnd: Any = ...,
    wMsgFilterMin: int = ...,
    wMsgFilterMax: int = ...,
    wRemoveMsg: int = ...,
) -> int: ...
def GetMessage(
    lpMsg: Any,
    hwnd: Any = ...,
    wMsgFilterMin: int = ...,
    wMsgFilterMax: int = ...,
) -> int: ...
def TranslateMessage(lpMsg: Any) -> int: ...
def DispatchMessage(lpMsg: Any) -> int: ...

# Window / hotkey helpers used by the broader hotkey machinery.
def RegisterHotKey(
    hwnd: Any,
    id: int,
    modifiers: int,
    vk: int,
) -> int: ...
def UnregisterHotKey(hwnd: Any, id: int) -> int: ...
def PostMessage(hwnd: Any, msg: int, wParam: Any, lParam: Any) -> int: ...
def SendMessage(hwnd: Any, msg: int, wParam: Any, lParam: Any) -> int: ...
def FindWindow(className: Any, windowName: Any) -> Any: ...
def GetForegroundWindow() -> Any: ...
def SetForegroundWindow(hwnd: Any) -> int: ...
def GetWindowText(hwnd: Any) -> str: ...
def IsWindowVisible(hwnd: Any) -> int: ...
def IsWindow(hwnd: Any) -> int: ...

# Virtual-key / message constants referenced as module attributes.
WM_HOTKEY: int
WM_KEYDOWN: int
WM_KEYUP: int
WM_SYSKEYDOWN: int
WM_SYSKEYUP: int
WM_QUIT: int
PM_REMOVE: int
PM_NOREMOVE: int

# Modifier bit flags for RegisterHotKey.
MOD_ALT: int
MOD_CONTROL: int
MOD_SHIFT: int
MOD_WIN: int
MOD_NOREPEAT: int

__all__: list[str]
