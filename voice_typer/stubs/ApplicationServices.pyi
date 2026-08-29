# ruff: noqa: A001, A002, N802, N803, N816
# PYREFLY-001: stub for the `ApplicationServices` framework (pyobjc-
# framework-ApplicationServices, macOS only). Used by
# `voice_typer/server/permissions.py::_check_macos_accessibility`.
#
# Import surface (from grep):
#   from ApplicationServices import AXIsProcessTrustedWithOptions
#
# Runtime usage (from grep):
#   AXIsProcessTrustedWithOptions(options_dict) -> bool
#
# `AXUIElementCreateApplication` and `AXUIElementCopyAttributeValue`
# were added because `voice_typer/server/clipboard_target_safety.py`
# calls them at runtime (AX API for focused-element introspection on
# macOS). They use the same permissive `Any` typing + `int` return
# convention (matching AX API's `AXError` enum) as the existing stubs.
from typing import Any

def AXIsProcessTrustedWithOptions(options: Any) -> bool: ...
def AXIsProcessTrusted() -> bool: ...
def AXMakeProcessTrusted(*args: Any, **kwargs: Any) -> bool: ...

# clipboard_target_safety.py uses these to introspect the
# currently-focused UI element (e.g. to detect terminal / rich-editor
# paste targets that need special keystroke handling).
def AXUIElementCreateApplication(pid: Any) -> Any: ...
def AXUIElementCopyAttributeValue(element: Any, attribute: Any, value: Any) -> int: ...

__all__: list[str]
