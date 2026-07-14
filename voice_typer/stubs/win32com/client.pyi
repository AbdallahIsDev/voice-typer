# ruff: noqa: A001, A002, N802, N803, N816
# RW-6 (pyrefly): stub for `win32com.client` (Windows-only, shipped via
# the `pywin32` distribution).
#
# The runtime code only uses `Dispatch` to obtain a COM object for
# `WScript.Shell` (used to create Windows shortcut files). All symbols
# are `Any` because the surrounding code is wrapped in
# `try/except ImportError` and is Windows-only.
from typing import Any

def Dispatch(progid: Any) -> Any: ...
def DispatchEx(progid: Any, machine: Any = ..., server: Any = ...) -> Any: ...
def GetObject(pathmoniker: Any = ..., progid: Any = ...) -> Any: ...
def CoInitialize() -> None: ...
def CoUninitialize() -> None: ...

__all__: list[str]
