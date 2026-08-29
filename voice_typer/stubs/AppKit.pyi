# PYREFLY-001: stub for the `AppKit` framework (pyobjc-framework-Cocoa,
# macOS only). Pystray uses AppKit's `NSStatusBar` / `NSStatusItem` to
# render the tray icon on macOS. Voice-typer itself does not import
# AppKit directly, but pyrefly follows the transitive imports through
# pystray's backend selection.
#
# `NSPasteboard`, `NSPasteboardItem`, and `NSWorkspace` were
# added because `voice_typer/server/clipboard_snapshot.py` and
# `voice_typer/server/clipboard_target_safety.py` access them at
# runtime (macOS pasteboard + workspace introspection). They use the
# same permissive `Any` typing as the existing stub entries.
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
# Clipboard code reads these via `AppKit.NSPasteboard` etc.
NSPasteboard: Any
NSPasteboardItem: Any
NSWorkspace: Any

# Constants.
NSVariableStatusItemLength: float
NSSquareStatusItemLength: float

__all__: list[str]
