# ruff: noqa: A001, A002, N802, N803, N816
# PYREFLY-001: stub for the `Foundation` framework (pyobjc-framework-
# Cocoa, macOS only). Pystray uses Cocoa/AppKit internally; the
# voice-typer code does not import Foundation directly today, but
# pyrefly follows transitive imports and may report `missing-import`
# on `Foundation` when resolving the pystray backend.
#
# This stub declares the commonly-imported Foundation symbols so
# pyrefly can resolve them without the pyobjc wheel installed.
from typing import Any

NSObject: Any
NSString: Any
NSMutableArray: Any
NSMutableDictionary: Any
NSNumber: Any
NSData: Any
NSNotification: Any
NSNotificationCenter: Any
NSTimer: Any
NSRunLoop: Any
NSAutoreleasePool: Any
NSBundle: Any
NSProcessInfo: Any

# Constants / sentinels.
kCFStringEncodingUTF8: int
NSDefaultRunLoopMode: Any

def NSLog(format: Any, *args: Any) -> None: ...

__all__: list[str]
