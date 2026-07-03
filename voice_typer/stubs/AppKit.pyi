# PYREFLY-001: stub for the `AppKit` framework (pyobjc-framework-Cocoa,
# macOS only). Pystray uses AppKit's `NSStatusBar` / `NSStatusItem` to
# render the tray icon on macOS. Voice-typer itself does not import
# AppKit directly, but pyrefly follows the transitive imports through
# pystray's backend selection.
from typing import Any

NSApplication: Any
NSStatusBar: Any
NSStatusItem: Any
NSImage: Any
NSMenu: Any
NSMenuItem: Any
NSEvent: Any
NSWindow: Any
NSView: Any
NSResponder: Any
NSTimer: Any

# Constants.
NSVariableStatusItemLength: float
NSSquareStatusItemLength: float

__all__: list[str]
