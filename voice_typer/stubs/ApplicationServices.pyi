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
from typing import Any

def AXIsProcessTrustedWithOptions(options: Any) -> bool: ...
def AXIsProcessTrusted() -> bool: ...
def AXMakeProcessTrusted(*args: Any, **kwargs: Any) -> bool: ...

__all__: list[str]
