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
import os
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
            # PERF-NEW-017: was 0.5s — the listener thread reaches
            # "alive" state within a few ms. 50ms is enough on the
            # slowest machines. With 3 hotkeys (toggle, PTT, repaste)
            # this saves ~1.4s of startup time.
            time.sleep(0.05)
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
        # PERF-NEW-017: was 0.5s — reduced to 50ms for the same reason.
        time.sleep(0.05)
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
# ARCH-019: guard _VK_MAP init so two threads racing on the first call
# don't each insert half the keys. The dict mutation itself is atomic
# in CPython, but the check-then-fill sequence is not.
_VK_MAP_LOCK = threading.Lock()


def _win32_vk(vk_name: str) -> Optional[int]:
    """Look up a VK code by name, initializing the map lazily."""
    _init_vk_map()
    return _VK_MAP.get(vk_name)


def _init_vk_map():
    """Populate _VK_MAP lazily to avoid issues at import on non-Windows.

    ARCH-019: previously the check-then-populate sequence was racy —
    two threads could both observe ``_VK_MAP`` as empty and each
    insert half the keys, with one set overwriting the other. We now
    guard the init with a module-level lock. The fast-path (map already
    populated) skips the lock to avoid contention on every hotkey press.
    """
    if _VK_MAP:
        return
    with _VK_MAP_LOCK:
        # Double-checked locking: another thread may have populated
        # the map while we were waiting on the lock.
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
        # ARCH-041: extend with numpad, media, browser, and special keys.
        # Without these, PTT bindings to e.g. Media_Next silently fail.
        # Numpad 0-9 (VK_NUMPAD0 = 0x60 .. VK_NUMPAD9 = 0x69)
        for i in range(10):
            _VK_MAP[f"num_{i}"] = 0x60 + i
            _VK_MAP[f"numpad_{i}"] = 0x60 + i
        _VK_MAP["num_decimal"] = 0x6E  # VK_DECIMAL
        _VK_MAP["num_enter"] = 0x6C    # VK_RETURN (numpad)
        _VK_MAP["num_add"] = 0x6B      # VK_ADD
        _VK_MAP["num_subtract"] = 0x6D # VK_SUBTRACT
        _VK_MAP["num_multiply"] = 0x6A # VK_MULTIPLY
        _VK_MAP["num_divide"] = 0x6F   # VK_DIVIDE
        # Media keys
        _VK_MAP["media_next"] = 0xB0    # VK_MEDIA_NEXT_TRACK
        _VK_MAP["media_prev"] = 0xB1    # VK_MEDIA_PREV_TRACK
        _VK_MAP["media_play_pause"] = 0xB3  # VK_MEDIA_PLAY_PAUSE
        _VK_MAP["media_stop"] = 0xB2    # VK_MEDIA_STOP
        # Browser keys
        _VK_MAP["browser_back"] = 0xA6
        _VK_MAP["browser_forward"] = 0xA7
        _VK_MAP["browser_refresh"] = 0xA8
        _VK_MAP["browser_home"] = 0xAC
        # Special keys
        _VK_MAP["capslock"] = 0x14  # VK_CAPITAL
        _VK_MAP["caps_lock"] = 0x14
        _VK_MAP["numlock"] = 0x90   # VK_NUMLOCK
        _VK_MAP["num_lock"] = 0x90
        _VK_MAP["scrolllock"] = 0x91  # VK_SCROLL
        _VK_MAP["scroll_lock"] = 0x91
        _VK_MAP["printscreen"] = 0x2C  # VK_SNAPSHOT
        _VK_MAP["print_screen"] = 0x2C
        _VK_MAP["pause"] = 0x13    # VK_PAUSE


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

        PERF-003: polls the key state every 100ms (~10Hz).  Detects
        key-down transitions by checking the high bit of GetAsyncKeyState.
        Previously polled at 50ms (20Hz) which caused ~3-5% battery
        drain per hour on laptops.  10Hz is still responsive enough
        for a push-to-talk hotkey (max 100ms latency) while halving
        the wakeups.
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
                try:
                    callback()
                except Exception:
                    # ERR-020: log full traceback but keep polling so
                    # the next hotkey press still works. Previously
                    # the bare callback() call would propagate the
                    # exception up the polling thread, killing it.
                    log.exception(
                        "[HOTKEY] Callback raised in polling loop; "
                        "hotkey still armed for next press"
                    )
            was_pressed = is_pressed
            # PERF-003: 100ms = 10Hz (was 50ms = 20Hz)
            # pyrefly: ignore [missing-attribute]
            self._kernel32.Sleep(100)

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
        """Stop the hotkey listener.

        PERF-NEW-016: previously posted WM_QUIT to the polling thread
        via PostThreadMessageW, but the thread uses GetAsyncKeyState
        polling (not a message loop) so it never reads WM_QUIT.  The
        join(timeout=3.0) waited 3 seconds for nothing.  Now we just
        set the stop event and join with a shorter timeout — the
        polling loop checks _stop_event every 100ms.
        """
        log.info("[HOTKEY] Stopping Windows native hotkey listener")
        self._stop_event.set()
        # PERF-NEW-016: skip the useless PostThreadMessageW call —
        # the polling loop checks _stop_event.is_set() every 100ms.
        if self._thread is not None:
            self._thread.join(timeout=0.5)  # was 3.0; 100ms poll = 500ms is plenty
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
    - On Linux/Wayland: returns ``WaylandHotkey`` (Unix socket fallback).
    - On Linux/X11: returns ``PynputHotkey``.
    """
    if sys.platform == "win32":
        log.info("[HOTKEY] Platform is win32 -> using WindowsNativeHotkey")
        return WindowsNativeHotkey(hotkey_str)

    # #4 PLAT-WAYLAND: detect Wayland and use Unix socket fallback
    if sys.platform.startswith("linux"):
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
        if wayland_display or xdg_session == "wayland":
            log.info("[HOTKEY] Wayland detected -> using WaylandHotkey (Unix socket)")
            return WaylandHotkey(hotkey_str)

    log.info("[HOTKEY] Platform is %s -> using PynputHotkey", sys.platform)
    return PynputHotkey(hotkey_str)


# ─── Wayland hotkey backend (#4 PLAT-WAYLAND) ──────────────────────────────


class WaylandHotkey(HotkeyBackend):
    """Wayland-compatible hotkey backend using a Unix domain socket.

    On Wayland compositors, pynput's X11-based keyboard listener doesn't
    work. This backend listens on a Unix domain socket at
    ``/tmp/voice-typer-hotkey.sock`` for commands like ``toggle`` and
    ``ping``. External tools (systemd, shell scripts, wlr-which-key)
    can send these commands to trigger dictation.

    Falls back to pynput if the socket fails, with a timer-based
    safety net that stops the pynput listener if it doesn't respond
    within a timeout (it silently fails on Wayland).
    """

    SOCKET_PATH = "/tmp/voice-typer-hotkey.sock"
    PING_RESPONSE = b"pong\n"
    TOGGLE_RESPONSE = b"toggled\n"

    def __init__(self, hotkey_str: str):
        self._hotkey_str = hotkey_str
        self._callback: Optional[Callable[[], None]] = None
        self._server_socket = None
        self._thread: Optional[threading.Thread] = None
        self._alive = False
        self._pynput_fallback: Optional[PynputHotkey] = None
        self._pynput_timer: Optional[threading.Timer] = None

    def start(self, callback: Callable[[], None]) -> None:
        """Start the Unix socket listener with pynput fallback."""
        self._callback = callback
        self._alive = True

        # Try Unix socket first
        try:
            self._start_socket_server()
            log.info("[HOTKEY-WAYLAND] Unix socket server started at %s", self.SOCKET_PATH)
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Failed to start socket server: %s", exc)
            self._start_pynput_fallback()
            return

        # Also start pynput as a fallback — on some Wayland setups,
        # XWayland or xdotool may make it partially work. Kill it
        # after a timeout if it doesn't fire.
        self._start_pynput_fallback_with_timeout()

    def _start_socket_server(self) -> None:
        """Create and bind the Unix domain socket."""
        import socket as _socket
        import stat

        # Clean up stale socket
        if os.path.exists(self.SOCKET_PATH):
            os.unlink(self.SOCKET_PATH)

        self._server_socket = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        self._server_socket.bind(self.SOCKET_PATH)
        # Make socket accessible to all users on the system
        os.chmod(self.SOCKET_PATH, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)

        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        """Accept connections and handle commands."""
        import socket as _socket
        while self._alive:
            try:
                conn, _ = self._server_socket.accept()
                try:
                    data = conn.recv(1024).decode("utf-8").strip()
                    if data == "toggle" and self._callback:
                        log.info("[HOTKEY-WAYLAND] Received toggle command")
                        self._callback()
                        conn.sendall(self.TOGGLE_RESPONSE)
                    elif data == "ping":
                        conn.sendall(self.PING_RESPONSE)
                    else:
                        conn.sendall(b"unknown command\n")
                finally:
                    conn.close()
            except _socket.timeout:
                continue
            except OSError:
                if self._alive:
                    log.warning("[HOTKEY-WAYLAND] Socket accept error")
                break

    def _start_pynput_fallback(self) -> None:
        """Start pynput as a direct fallback (no socket)."""
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (direct)")
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Pynput fallback also failed: %s", exc)

    def _start_pynput_fallback_with_timeout(self) -> None:
        """Start pynput with a timeout — kill it if it doesn't respond."""
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (with timeout)")

            # Set a timer to stop pynput if it doesn't fire within 30s
            # On Wayland, pynput usually silently fails — the timer
            # cleans it up so it doesn't waste resources.
            self._pynput_timer = threading.Timer(30.0, self._stop_pynput_fallback)
            self._pynput_timer.daemon = True
            self._pynput_timer.start()
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Pynput fallback failed: %s", exc)

    def _stop_pynput_fallback(self) -> None:
        """Stop the pynput fallback if it's still running."""
        if self._pynput_fallback and self._pynput_fallback.is_alive():
            try:
                self._pynput_fallback.stop()
                log.info("[HOTKEY-WAYLAND] Pynput fallback stopped (timeout)")
            except Exception:
                pass
        self._pynput_fallback = None

    def stop(self) -> None:
        """Stop the socket server and any pynput fallback."""
        self._alive = False
        if self._pynput_timer:
            self._pynput_timer.cancel()
            self._pynput_timer = None
        if self._pynput_fallback:
            self._stop_pynput_fallback()
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
        if os.path.exists(self.SOCKET_PATH):
            try:
                os.unlink(self.SOCKET_PATH)
            except Exception:
                pass
        log.info("[HOTKEY-WAYLAND] Stopped")

    def is_alive(self) -> bool:
        """Return True if the socket server thread is running."""
        return self._alive and (self._thread is not None and self._thread.is_alive())

    def diagnose(self) -> str:
        """Return diagnostic information about the Wayland hotkey backend."""
        socket_ok = os.path.exists(self.SOCKET_PATH)
        thread_alive = self._thread is not None and self._thread.is_alive()
        pynput_alive = self._pynput_fallback is not None and self._pynput_fallback.is_alive()
        return (
            f"WaylandHotkey: socket={self.SOCKET_PATH} (exists={socket_ok}), "
            f"thread_alive={thread_alive}, pynput_fallback={pynput_alive}"
        )

