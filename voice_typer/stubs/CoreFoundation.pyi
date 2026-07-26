# ruff: noqa: A001, A002, N802, N803, N816
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

# TASK-14: CFRunLoop functions used by ``microphone_watcher_coreaudio``
# to drive the CoreAudio property-listener thread.  Declared with
# permissive ``Any`` parameter / return types to match the rest of this
# stub.
def CFRunLoopGetCurrent() -> Any: ...
def CFRunLoopGetMain() -> Any: ...
def CFRunLoopRun() -> None: ...
def CFRunLoopStop(rl: Any) -> None: ...
def CFRunLoopAddSource(rl: Any, source: Any, mode: Any) -> None: ...
def CFRunLoopRemoveSource(rl: Any, source: Any, mode: Any) -> None: ...

__all__: list[str]
