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

# Submodules re-exported for `import comtypes.client` style access.
from comtypes import client as client

__all__: list[str]
