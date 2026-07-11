# ruff: noqa: N802, N803
# PYREFLY-001: stub for `comtypes.client` — the high-level COM helper
# module used by `voice_typer/server/clipboard.py` for UI Automation.
#
# Import surface (from grep):
#   import comtypes.client
#   comtypes.client.GetModule("UIAutomationCore.dll")
#
# Runtime usage (from grep):
#   comtypes.client.GetModule(dll_name) -> module with CUIAutomation, IUIAutomation
#   module.CUIAutomation._reg_clsid_     -> GUID
#   module.IUIAutomation                 -> interface type
from typing import Any

def GetModule(tlib: Any) -> Any: ...
def GetEvents(*args: Any, **kwargs: Any) -> Any: ...
def CreateObject(
    obj: Any,
    interface: Any = ...,
    clsctx: int = ...,
    dynamic: bool = ...,
) -> Any: ...
def CoCreateInstance(
    clsid: Any,
    interface: Any = ...,
    clsctx: int = ...,
    punkouter: Any = ...,
) -> Any: ...

__all__: list[str]
