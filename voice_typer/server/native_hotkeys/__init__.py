"""Native subprocess hotkey backends.

Provides out-of-process hotkey detection via small native binaries that
emit line-delimited events on stdout. This module implements the
"Freestyle architecture" for Voice Typer:

- macOS: ``macos-key-listener`` (Swift) — supports the FN key via
  ``NSEvent.modifierFlags.contains(.function)`` plus a ``CGEvent.tap``
  for key-up reliability and hotkey suppression.
- Windows: ``windows-key-listener.exe`` (C) — uses ``WH_KEYBOARD_LL``
  (event-driven) instead of ``GetAsyncKeyState`` polling. Lower CPU,
  sub-millisecond latency, supports key suppression and modifier-only
  detection.
- Linux: ``linux-key-listener`` (C) — uses ``/dev/input/event*``
  (evdev). Works on both X11 and Wayland (unlike pynput which is X11
  only). Read-only — no key suppression on Linux.

Wire protocol (line-delimited stdout, same for all three binaries):

    READY                  # emitted once after init succeeds
    FN_DOWN                # macOS only — Fn/Globe pressed (edge-detected)
    FN_UP                  # macOS only — Fn/Globe released (edge-detected)
    KEY_DOWN:<Name>        # non-modifier key pressed
    KEY_UP:<Name>          # non-modifier key released
    MOD_DOWN:<Name>        # modifier pressed (Ctrl, Shift, Alt, Cmd/Win/Super)
    MOD_UP:<Name>          # modifier released
    ERROR:<message>        # fatal error, binary will exit(1)

Key names are normalized across platforms:
- Letters: A..Z
- Digits: 0..9
- Function keys: F1..F24
- Special: Space, Enter, Tab, Esc, Backspace, Insert, Delete, Home,
  End, PageUp, PageDown, CapsLock, NumLock, ScrollLock, PrintScreen, Pause
- Arrows: Up, Down, Left, Right
- Numpad: Num0..Num9, NumDecimal, NumAdd, NumSubtract, NumMultiply,
  NumDivide, NumEnter
- Media: MediaPlay, MediaStop, MediaNext, MediaPrev
- Modifiers: Ctrl, Shift, Alt, Cmd (macOS), Win (Windows), Super (Linux)

All three backends share ``SubprocessHotkeyBackend`` which handles:

- Binary discovery (dev mode + PyInstaller bundle)
- subprocess.Popen with the hotkey spec as argv[1]
- Reader thread parsing stdout lines
- Hotkey matching against the parsed spec
- Restart-on-crash with exponential backoff (max 5 attempts)
- Clean shutdown via SIGTERM (POSIX) / terminate() (Windows)

Phase 4.5 / ARCH-045 — this file was previously a 1,188-line god-module
(``voice_typer/server/native_hotkeys.py``); it has been split into a
package with one module per concern.  This ``__init__.py`` re-exports
every public name that the original module exposed so existing imports
of the form ``from voice_typer.server.native_hotkeys import X`` keep
working without modification.

Patch-path compatibility: tests do
``monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)``
(and is_windows / is_linux) and
``monkeypatch.setattr(sys, "platform", "linux")``.  For the patches to
affect production code defined in submodules, the submodules look up
``is_windows`` / ``is_linux`` / ``is_macos`` via thin wrapper lambdas
that delegate to *this* package's binding at call time (rather than
capturing the function object at import time).  The ``sys`` module is
imported here so ``voice_typer.server.native_hotkeys.sys`` resolves to
the real ``sys`` module and ``monkeypatch.setattr`` on ``.sys.platform``
propagates to all callers.
"""

# `sys` is imported (and re-exported) so that tests using
# `monkeypatch.setattr(sys, "platform", "linux")` continue to work —
# `sys` is referenced by `binary_path.get_native_binary_path`,
# `mac_backend.MacNativeHotkey._validate_platform`, etc. via the real
# `sys` module (not the package binding), so patching
# `sys.platform` directly affects them.
# `subprocess` is imported (and re-exported) so that tests using
# `monkeypatch.setattr(native_hotkeys.subprocess, "Popen", fake_popen)`
# continue to work after the Phase 4.5 split.  Production code in
# `base.SubprocessHotkeyBackend._spawn_process` does `import subprocess`
# directly (same module object), so the patch propagates to the actual
# `subprocess.Popen` call site.
import subprocess  # noqa: F401 — re-exported for monkeypatch.setattr targets
import sys  # noqa: F401 — re-exported for monkeypatch.setattr targets

from voice_typer.server.platform_utils import (
    is_linux,
    is_macos,
    is_windows,
)

# ─── Public API re-exports ──────────────────────────────────────────────────
# Order matters: each submodule imports from earlier ones
# (spec_parser → binary_path → modifiers → base → mac/windows/linux_backend
# → factory → recorder).  All submodules also do
# ``from voice_typer.server import native_hotkeys as _native_hotkeys_pkg``
# to defer ``is_windows`` / ``is_linux`` / ``is_macos`` lookups to call
# time, so the package's bindings for those names must be in place
# before the submodules' lambdas ever fire — which they are, because
# the lambdas only execute when the wrapped functions are called at
# runtime, long after this ``__init__.py`` has finished loading.
from .base import (
    MAX_RESTART_ATTEMPTS,
    READY_TIMEOUT_SECONDS,
    RESTART_DELAY_BASE_SECONDS,
    SubprocessHotkeyBackend,
)
from .binary_path import _BINARY_NAMES, get_native_binary_path
from .factory import create_native_backend, is_native_backend_available
from .linux_backend import LinuxEvdevHotkey
from .mac_backend import MacNativeHotkey
from .modifiers import (
    _MOD_CANONICAL_MAP,
    _canonical_modifier,
    _canonical_modifier_name_for_token,
    _key_name_to_token,
    _modifier_to_token,
)
from .recorder import NativeHotkeyRecorder
from .spec_parser import _normalize_key_name, log, parse_hotkey_spec
from .windows_backend import WindowsHookHotkey

__all__ = [
    # spec_parser
    "parse_hotkey_spec",
    "_normalize_key_name",
    "log",
    # binary_path
    "_BINARY_NAMES",
    "get_native_binary_path",
    # modifiers
    "_MOD_CANONICAL_MAP",
    "_canonical_modifier",
    "_canonical_modifier_name_for_token",
    "_modifier_to_token",
    "_key_name_to_token",
    # base
    "MAX_RESTART_ATTEMPTS",
    "RESTART_DELAY_BASE_SECONDS",
    "READY_TIMEOUT_SECONDS",
    "SubprocessHotkeyBackend",
    # mac / windows / linux backends
    "MacNativeHotkey",
    "WindowsHookHotkey",
    "LinuxEvdevHotkey",
    # factory
    "create_native_backend",
    "is_native_backend_available",
    # recorder
    "NativeHotkeyRecorder",
    # platform_utils (re-exported for patch compatibility)
    "is_linux",
    "is_macos",
    "is_windows",
    # sys (re-exported for monkeypatch.setattr compatibility)
    "sys",
    # subprocess (re-exported for monkeypatch.setattr compatibility)
    "subprocess",
]
