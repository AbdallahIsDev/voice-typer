# ruff: noqa: N802, N803
# PYREFLY-001: stub for the `comtypes` package (Windows COM).
# comtypes is declared in pyproject.toml with `sys_platform == 'win32'`,
# so it is never installed on the Linux/macOS CI runners.
#
# Import surface (from grep):
#   from comtypes import CLSCTX_ALL
#   comtypes.CoCreateInstance(clsid, interface=...) -> instance
#
# Runtime usage (from grep):
#   CLSCTX_ALL                    (int constant, passed to Activate)
#   comtypes.CoCreateInstance(...) -> COM interface instance
from typing import Any

# COM context flags (Windows constants).
CLSCTX_ALL: int
CLSCTX_INPROC_SERVER: int
CLSCTX_INPROC_HANDLER: int
CLSCTX_LOCAL_SERVER: int
CLSCTX_REMOTE_SERVER: int

# COM class-context GUID helpers.
GUID: Any

def CoCreateInstance(
    clsid: Any,
    interface: Any = ...,
    clsctx: int = ...,
    punkouter: Any = ...,
) -> Any: ...
def GetClassObject(
    clsid: Any,
    interface: Any = ...,
    clsctx: int = ...,
) -> Any: ...

# TASK-14: COM apartment threading initialization.  Used by
# ``clipboard.py`` to enter/leave the COM apartment before invoking
# UI-Automation calls.  Permissive ``Any`` return matches the rest of
# this stub.
def CoInitialize(reserved: Any = ...) -> None: ...
def CoInitializeEx(reserved: Any = ..., dwCoInit: int = ...) -> None: ...
def CoUninitialize() -> None: ...

# Submodules re-exported for `import comtypes.client` style access.
from comtypes import client as client  # noqa: E402

__all__: list[str]
