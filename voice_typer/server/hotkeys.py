"""Hotkey backend abstraction.

Provides platform-aware hotkey listening with two implementations:

- PynputHotkey: Uses pynput.keyboard.GlobalHotKeys (cross-platform).
- WindowsNativeHotkey: Uses Win32 RegisterHotKey via ctypes (Windows only).

The factory function ``create_hotkey_backend`` picks the best available backend.

All backends share a common interface:
    - start(callback) -> None
    - stop() -> None
    - is_alive() -> bool
    - diagnose() -> str
"""

import logging
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

log = logging.getLogger("voice_typer")


# ─── Base class ──────────────────────────────────────────────────────────────


class HotkeyBackend(ABC):
    """Abstract base for hotkey backends."""

    def __init__(self, hotkey_str: str):
        self.hotkey_str = hotkey_str
        self._on_release_callback: Optional[Callable[[], None]] = None

    @abstractmethod
    def start(self, callback: Callable[[], None]) -> None:
        """Start listening for the hotkey. Calls *callback* when pressed."""

    def set_on_release(self, callback: Optional[Callable[[], None]]) -> None:
        """Set a callback for key release (used by push-to-talk mode)."""
        self._on_release_callback = callback

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release resources."""

    @abstractmethod
    def is_alive(self) -> bool:
        """Return True if the listener thread is running."""

    @abstractmethod
    def diagnose(self) -> str:
        """Return a human-readable diagnostic string."""


# ─── Pynput backend ──────────────────────────────────────────────────────────


class PynputHotkey(HotkeyBackend):
    """Hotkey backend using pynput.keyboard.GlobalHotKeys.

    Falls back to a regular ``Listener`` with manual key matching if
    ``GlobalHotKeys`` fails (common on some Windows / WSL setups).
    """

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._listener = None
        self._fallback = False

    def start(self, callback: Callable[[], None]) -> None:
        from pynput.keyboard import GlobalHotKeys, Listener, Key, KeyCode

        log.info(
            "Registering hotkey via pynput: %r -> callback", self.hotkey_str
        )

        try:
            self._listener = GlobalHotKeys(
                {self.hotkey_str: callback}
            )
            self._listener.start()
            time.sleep(0.5)
            alive = self._listener.is_alive()
            log.info(
                "Pynput GlobalHotKeys started (alive=%s, daemon=%s)",
                alive,
                getattr(self._listener, "daemon", "?"),
            )
            if not alive:
                log.error(
                    "GlobalHotKeys thread died immediately; "
                    "falling back to manual Listener"
                )
                self._stop_listener()
                self._start_fallback(callback, Listener, Key, KeyCode)
        except Exception:
            log.exception("[HOTKEY] GlobalHotKeys failed; trying fallback Listener")
            try:
                self._start_fallback(callback, Listener, Key, KeyCode)
            except Exception:
                log.exception("[HOTKEY] Fallback Listener also failed")

    # --- internal helpers ---------------------------------------------------

    def _start_fallback(self, callback, Listener, Key, KeyCode) -> None:
        target = _parse_hotkey_to_pynput(self.hotkey_str, Key, KeyCode)
        if target is None:
            raise RuntimeError(
                f"Cannot parse hotkey {self.hotkey_str!r} for fallback"
            )

        # For composite hotkeys (tuple), extract the target key only
        match_key = target[1] if isinstance(target, tuple) else target

        # UX-001: track whether the matched key is currently held down
        # so we can fire the on_release callback exactly once per
        # press-release cycle (pynput fires repeated on_press events
        # while a key is held).
        held = {"value": False}

        def on_press(key):
            if key == match_key:
                if not held["value"]:
                    held["value"] = True
                    log.info("[HOTKEY FALLBACK] Matched key: %s", key)
                    callback()

        def on_release(key):
            # UX-001: invoke the on_release callback (used by
            # push-to-talk mode) when the matched key is released.
            # The check ``held["value"]`` ensures we only fire on the
            # transition from held -> released, not on every spurious
            # release event pynput may emit.
            if key == match_key and held["value"]:
                held["value"] = False
                log.info("[HOTKEY FALLBACK] Key released: %s", key)
                if self._on_release_callback is not None:
                    try:
                        self._on_release_callback()
                    except Exception:
                        log.exception("[HOTKEY FALLBACK] on_release callback raised")

        self._listener = Listener(on_press=on_press, on_release=on_release)
        self._listener.start()
        time.sleep(0.5)
        self._fallback = True
        log.info(
            "[HOTKEY] Fallback listener started, watching for %s (alive=%s)",
            match_key,
            self._listener.is_alive(),
        )

    def _stop_listener(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    # --- public interface ---------------------------------------------------

    def stop(self) -> None:
        if self._listener is not None:
            log.info("[HOTKEY] Stopping pynput hotkey listener")
            self._stop_listener()

    def is_alive(self) -> bool:
        return self._listener is not None and self._listener.is_alive()

    def diagnose(self) -> str:
        if self._listener is None:
            return "PynputHotkey: no listener registered"
        alive = self._listener.is_alive()
        daemon = getattr(self._listener, "daemon", "?")
        name = getattr(self._listener, "name", "?")
        mode = "fallback" if self._fallback else "GlobalHotKeys"
        return (
            f"PynputHotkey ({mode})\n"
            f"Hotkey: {self.hotkey_str}\n"
            f"Thread name: {name}\n"
            f"Thread alive: {alive}\n"
            f"Thread daemon: {daemon}"
        )


def _parse_hotkey_to_pynput(hotkey_str, Key, KeyCode):
    """Parse '<f2>' or '<ctrl>+1' -> pynput Key/KeyCode for fallback matching.

    Handles composite hotkeys with modifiers (ctrl, alt, shift, cmd/win).
    Returns a tuple of (modifier_keys, target_key) for composite hotkeys,
    or a single Key/KeyCode for simple hotkeys.
    """
    parts = hotkey_str.strip("<>").split("+")
    parts = [p.strip().strip("<>") for p in parts]

    if len(parts) == 1:
        clean = parts[0].lower()
        if hasattr(Key, clean):
            return getattr(Key, clean)
        if clean.startswith("f") and clean[1:].isdigit():
            fnum = int(clean[1:])
            if 1 <= fnum <= 24:
                return KeyCode.from_vk(0x6F + fnum)
        if len(clean) == 1:
            return KeyCode.from_char(clean)
        return None

    # Composite hotkey: return tuple of (modifiers_tuple, target_key)
    modifier_keys = []
    target = None
    modifier_names = {"ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift, "cmd": Key.cmd, "win": Key.cmd, "super": Key.cmd}

    for part in parts:
        clean = part.lower()
        if clean in modifier_names:
            modifier_keys.append(modifier_names[clean])
        elif target is None:
            if hasattr(Key, clean):
                target = getattr(Key, clean)
            elif clean.startswith("f") and clean[1:].isdigit():
                fnum = int(clean[1:])
                if 1 <= fnum <= 24:
                    target = KeyCode.from_vk(0x6F + fnum)
            elif len(clean) == 1:
                target = KeyCode.from_char(clean)

    if target is None:
        return None

    if modifier_keys:
        return (tuple(modifier_keys), target)
    return target


# ─── Windows native backend ──────────────────────────────────────────────────

# Win32 constants
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
_GWLP_USERDATA = -21

# Common virtual-key code mappings for function keys and printable keys.
_VK_MAP = {}


def _win32_vk(vk_name: str) -> Optional[int]:
    """Look up a VK code by name, initializing the map lazily."""
    _init_vk_map()
    return _VK_MAP.get(vk_name)


def _init_vk_map():
    """Populate _VK_MAP lazily to avoid issues at import on non-Windows."""
    if _VK_MAP:
        return
    # F1-F24
    for i in range(1, 25):
        _VK_MAP[f"f{i}"] = 0x70 + (i - 1)  # F1=0x70, F2=0x71, ...
    # Digits 0-9
    for c in "0123456789":
        _VK_MAP[c] = ord(c)
    # Letters a-z
    for c in "abcdefghijklmnopqrstuvwxyz":
        _VK_MAP[c] = ord(c.upper())
    # Common special keys
    _VK_MAP["esc"] = 0x1B      # VK_ESCAPE
    _VK_MAP["escape"] = 0x1B
    _VK_MAP["space"] = 0x20    # VK_SPACE
    _VK_MAP["enter"] = 0x0D    # VK_RETURN
    _VK_MAP["return"] = 0x0D
    _VK_MAP["tab"] = 0x09      # VK_TAB
    _VK_MAP["backspace"] = 0x08  # VK_BACK
    _VK_MAP["del"] = 0x2E      # VK_DELETE
    _VK_MAP["delete"] = 0x2E
    _VK_MAP["insert"] = 0x2D   # VK_INSERT
    _VK_MAP["home"] = 0x24     # VK_HOME
    _VK_MAP["end"] = 0x23      # VK_END
    _VK_MAP["pageup"] = 0x21   # VK_PRIOR
    _VK_MAP["pagedown"] = 0x22 # VK_NEXT
    _VK_MAP["up"] = 0x26       # VK_UP
    _VK_MAP["down"] = 0x28     # VK_DOWN
    _VK_MAP["left"] = 0x25     # VK_LEFT
    _VK_MAP["right"] = 0x27    # VK_RIGHT


def parse_hotkey_to_vk(hotkey_str: str) -> Optional[int]:
    """Convert a hotkey string like '<f2>' to a Win32 virtual-key code.

    Returns None if the key cannot be parsed.
    """
    parsed = parse_hotkey_to_win32(hotkey_str)
    if parsed is None:
        return None
    return parsed[0]


def parse_hotkey_to_win32(hotkey_str: str) -> Optional[tuple[int, int]]:
    """Convert a hotkey string to ``(virtual_key, RegisterHotKey modifiers)``."""
    _init_vk_map()
    modifiers = 0
    key_name = None

    for raw_part in hotkey_str.split("+"):
        part = raw_part.strip().strip("<>").lower()
        if not part:
            continue
        if part in {"ctrl", "control"}:
            modifiers |= _MOD_CONTROL
            continue
        if part == "alt":
            modifiers |= _MOD_ALT
            continue
        if part == "shift":
            modifiers |= _MOD_SHIFT
            continue
        if part in {"cmd", "win", "super"}:
            modifiers |= _MOD_WIN
            continue
        key_name = part

    if key_name is None:
        return None

    vk = _VK_MAP.get(key_name)
    if vk is None:
        return None
    return vk, modifiers


class WindowsNativeHotkey(HotkeyBackend):
    """Hotkey backend using Win32 RegisterHotKey via ctypes.

    Uses GetAsyncKeyState polling in a daemon thread for reliable
    hotkey detection.  RegisterHotKey is still called so that other
    applications cannot register the same hotkey.
    """

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()  # signalled when registration completes
        self._hotkey_id = 1  # arbitrary ID for RegisterHotKey
        self._registered = False
        self._user32 = None
        self._kernel32 = None
        self._success = False
        self._vk: Optional[int] = None
        self._modifiers = 0
        self._using_polling = False  # True if falling back to GetAsyncKeyState

    def start(self, callback: Callable[[], None]) -> None:
        import ctypes
        import ctypes.wintypes

        parsed = parse_hotkey_to_win32(self.hotkey_str)
        if parsed is None:
            raise ValueError(
                f"Cannot parse hotkey {self.hotkey_str!r} to a VK code"
            )
        self._vk, self._modifiers = parsed

        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._stop_event.clear()
        self._ready_event.clear()
        self._success = False
        self._last_error = None  # captured GetLastError() on failure

        # ── Set proper argtypes BEFORE any Win32 call ──
        # Without these, ctypes defaults to c_int which truncates 64-bit pointers.
        from ctypes.wintypes import (
            BOOL, DWORD, HWND, INT, LPARAM, UINT, WPARAM,
        )

        # BOOL RegisterHotKey(HWND, int, UINT, UINT)
        self._user32.RegisterHotKey.argtypes = [HWND, INT, UINT, UINT]
        self._user32.RegisterHotKey.restype = BOOL

        # BOOL UnregisterHotKey(HWND, int)
        self._user32.UnregisterHotKey.argtypes = [HWND, INT]
        self._user32.UnregisterHotKey.restype = BOOL

        # BOOL PostThreadMessageW(DWORD threadId, UINT msg, WPARAM, LPARAM)
        self._user32.PostThreadMessageW.argtypes = [DWORD, UINT, WPARAM, LPARAM]
        self._user32.PostThreadMessageW.restype = BOOL

        # DWORD GetLastError(void)
        self._kernel32.GetLastError.argtypes = []
        self._kernel32.GetLastError.restype = DWORD

        def run():
            """Hotkey thread: registers hotkey, runs polling loop."""
            try:
                # Register the hotkey.  Pass NULL (0) as hWnd.
                # RegisterHotKey(NULL, ...) binds the hotkey to the calling
                # thread so WM_HOTKEY is posted to the thread message queue.
                # pyrefly: ignore [missing-attribute]
                result = self._user32.RegisterHotKey(
                    0, self._hotkey_id, _MOD_NOREPEAT | self._modifiers, self._vk
                )
                if not result:
                    # pyrefly: ignore [missing-attribute]
                    err = self._kernel32.GetLastError()
                    self._last_error = err
                    log.warning(
                        "RegisterHotKey failed for VK=0x%X, GetLastError=%d (0x%X) "
                        "— polling fallback still works",
                        self._vk,
                        err, err,
                    )
                else:
                    self._registered = True
                    log.info(
                        "[HOTKEY] RegisterHotKey succeeded: hotkey=%s vk=0x%X id=%d",
                        self.hotkey_str,
                        self._vk,
                        self._hotkey_id,
                    )

                self._success = True
                self._ready_event.set()

                # ── Hotkey detection ──
                # Use GetAsyncKeyState polling for reliable hotkey detection.
                # RegisterHotKey + GetMessageW does not reliably deliver WM_HOTKEY
                # on all Windows configurations.  Polling at 20Hz uses negligible
                # CPU and works universally.
                log.info("[HOTKEY] Starting hotkey detection via GetAsyncKeyState polling")
                self._using_polling = True
                self._run_polling_loop(callback)

            except Exception:
                log.exception("[HOTKEY] Windows hotkey thread error")
            finally:
                # Cleanup
                if self._registered:
                    # pyrefly: ignore [missing-attribute]
                    self._user32.UnregisterHotKey(0, self._hotkey_id)
                    self._registered = False
                    log.info("[HOTKEY] UnregisterHotKey done")

        # Also set GetAsyncKeyState argtypes for the polling fallback
        self._user32.GetAsyncKeyState.argtypes = [INT]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short

        # Set Sleep argtypes
        self._kernel32.Sleep.argtypes = [DWORD]
        self._kernel32.Sleep.restype = None

        self._thread = threading.Thread(target=run, daemon=True, name="WinHotkey")
        self._thread.start()

        # Wait for the registration thread to signal readiness (or timeout)
        if not self._ready_event.wait(timeout=5.0):
            self._last_error = -1
            raise RuntimeError(
                f"Timed out waiting for hotkey registration of {self.hotkey_str!r}"
            )
        if not self._success:
            err = self._last_error
            raise RuntimeError(
                f"Failed to register hotkey {self.hotkey_str!r} "
                f"(Win32 error {err}, 0x{(err if err and err >= 0 else 0):X})"
            )

    def _run_polling_loop(self, callback):
        """GetAsyncKeyState polling fallback for hotkey detection.

        Polls the key state every 33ms (~30Hz).  Detects key-down
        transitions by checking the high bit of GetAsyncKeyState.
        """
        import ctypes
        vk = self._vk
        was_pressed = False
        log.info("[HOTKEY] Polling loop started for VK=0x%X modifiers=0x%X", vk, self._modifiers)
        while not self._stop_event.is_set():
            # pyrefly: ignore [missing-attribute]
            state = self._user32.GetAsyncKeyState(vk)
            is_pressed = bool(state & 0x8000) and self._modifiers_pressed()
            if is_pressed and not was_pressed:
                log.info("[HOTKEY FIRED] GetAsyncKeyState detected key-down")
                callback()
            was_pressed = is_pressed
            # pyrefly: ignore [missing-attribute]
            self._kernel32.Sleep(50)  # ~20Hz polling rate

    def _modifiers_pressed(self) -> bool:
        if self._modifiers & _MOD_CONTROL:
            if not self._key_pressed(0x11):
                return False
        if self._modifiers & _MOD_SHIFT:
            if not self._key_pressed(0x10):
                return False
        if self._modifiers & _MOD_ALT:
            if not self._key_pressed(0x12):
                return False
        if self._modifiers & _MOD_WIN:
            if not (self._key_pressed(0x5B) or self._key_pressed(0x5C)):
                return False
        return True

    def _key_pressed(self, vk: int) -> bool:
        # pyrefly: ignore [missing-attribute]
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def stop(self) -> None:
        log.info("[HOTKEY] Stopping Windows native hotkey listener")
        self._stop_event.set()
        if self._user32 is not None and self._thread is not None:
            thread_id = self._thread.ident
            if thread_id is not None:
                self._user32.PostThreadMessageW(thread_id, _WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def diagnose(self) -> str:
        if self._thread is None:
            return "WindowsNativeHotkey: no thread started"
        mode = "polling" if self._using_polling else "message-loop"
        return (
            "WindowsNativeHotkey\n"
            f"Hotkey: {self.hotkey_str}\n"
            f"VK: 0x{self._vk:X} ({self._vk})\n"
            f"Modifiers: 0x{self._modifiers:X}\n"
            f"Mode: {mode}\n"
            f"Thread name: {self._thread.name}\n"
            f"Thread alive: {self._thread.is_alive()}\n"
            f"Registered: {self._registered}"
        )


# ─── Factory ─────────────────────────────────────────────────────────────────


def create_hotkey_backend(hotkey_str: str) -> HotkeyBackend:
    """Create the best hotkey backend for the current platform.

    - On Windows: returns ``WindowsNativeHotkey``.
    - Elsewhere: returns ``PynputHotkey``.
    """
    if sys.platform == "win32":
        log.info("[HOTKEY] Platform is win32 -> using WindowsNativeHotkey")
        return WindowsNativeHotkey(hotkey_str)

    log.info("[HOTKEY] Platform is %s -> using PynputHotkey", sys.platform)
    return PynputHotkey(hotkey_str)
