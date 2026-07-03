# PYREFLY-001: stub for the `CoreFoundation` framework (pyobjc-framework-
# CoreFoundation, macOS only). Used by `voice_typer/server/permissions.py
# ::_check_macos_accessibility`.
#
# Import surface (from grep):
#   from CoreFoundation import CFDictionaryCreate, kCFBooleanTrue
#
# Runtime usage (from grep):
#   CFDictionaryCreate(None, [], [], 0, None, None) -> CFDictionaryRef
#   kCFBooleanTrue  (passed as a value in the dict)
from typing import Any

kCFBooleanTrue: Any
kCFBooleanFalse: Any
kCFAllocatorDefault: Any
kCFTypeDictionaryKeyCallBacks: Any
kCFTypeDictionaryValueCallBacks: Any

def CFDictionaryCreate(
    allocator: Any,
    keys: Any,
    values: Any,
    numValues: int,
    keyCallBacks: Any,
    valueCallBacks: Any,
) -> Any: ...
def CFDictionaryCreateMutable(
    allocator: Any,
    capacity: int,
    keyCallBacks: Any,
    valueCallBacks: Any,
) -> Any: ...
def CFRelease(cf: Any) -> None: ...
def CFRetain(cf: Any) -> Any: ...
def CFNumberCreate(
    allocator: Any,
    theType: int,
    valuePtr: Any,
) -> Any: ...

__all__: list[str]
