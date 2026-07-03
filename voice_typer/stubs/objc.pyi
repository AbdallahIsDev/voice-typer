# PYREFLY-001: stub for the `objc` module (pyobjc-core, macOS only).
# This is the pyobjc runtime that loads framework bindings. Voice-typer
# itself rarely imports `objc` directly, but pyrefly may resolve it
# transitively through pystray / pyobjc-framework-* imports.
from typing import Any

def selector(
    callback: Any,
    selector: Any = ...,
    signature: Any = ...,
    isClassMethod: bool = ...,
) -> Any: ...
def IBOutlet(name: Any = ...) -> Any: ...
def IBAction(callback: Any) -> Any: ...
def ivar(type: Any = ..., name: str = ...) -> Any: ...
def property(*args: Any, **kwargs: Any) -> Any: ...
def synthesize(*args: Any, **kwargs: Any) -> Any: ...
def lookUpClass(name: str) -> Any: ...
def loadBundle(
    framework: Any = ...,
    module_name: Any = ...,
    bundle_path: Any = ...,
    *args: Any,
    **kwds: Any,
) -> Any: ...
def loadBundleFunctions(
    bundle: Any,
    module_identifier: Any,
    function_info: Any,
    *args: Any,
    **kwds: Any,
) -> Any: ...

# pyobjc runtime sentinel classes used as type hints in framework stubs.
NSObject: Any
pyobjc_id: Any

__all__: list[str]
