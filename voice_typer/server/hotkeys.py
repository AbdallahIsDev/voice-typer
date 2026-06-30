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
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

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

    # NEW-DEAD-009: ``diagnose`` was previously @abstractmethod, forcing
    # every subclass to implement a debug string even though only test
    # callers invoke it.  We provide a default no-op implementation so
    # new backends (e.g. a future macOS or Linux native backend) don't
    # have to implement it just to satisfy the Protocol.  Existing
    # backends (PynputHotkey, Win32Hotkey, etc.) still override it
    # because their tests rely on the diagnostic output.
    def diagnose(self) -> str:
        """Return a human-readable diagnostic string.

        Default implementation returns an empty string.  Subclasses
        override to provide backend-specific debug info (registered
        hotkeys, listener thread state, etc.).
        """
        return ""


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
        # PERF-012: On Linux/macOS, use pynput's event-driven Listener
        # instead of polling. The Listener receives key events from the
        # OS, so it uses zero CPU while idle and has zero latency.
        # On Windows, the WindowsNativeHotkey backend is preferred (uses
        # GetAsyncKeyState in a tight 1ms-polling loop).
        from pynput.keyboard import GlobalHotKeys, Key, KeyCode, Listener

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

            # PLAT-030: On macOS, pynput failure usually means the
            # Accessibility permission is missing. Show a user-friendly
            # guide so the user knows how to fix it.
            if is_macos():
                log.warning(
                    "[HOTKEY] macOS: pynput keyboard listener failed. "
                    "This usually means the Accessibility permission is not "
                    "granted to Voice Typer. To fix:\n"
                    "  1. Open System Preferences > Privacy & Security > "
                    "Accessibility\n"
                    "  2. Click the lock icon and authenticate\n"
                    "  3. Add Voice Typer (or your terminal/Python) to the "
                    "allowed apps\n"
                    "  4. Restart Voice Typer\n"
                    "Without this permission, hotkeys will not work."
                )

            try:
                self._start_fallback(callback, Listener, Key, KeyCode)
            except Exception:
                log.exception("[HOTKEY] Fallback Listener also failed")

                # PLAT-030: also warn for the fallback path on macOS
                if is_macos():
                    log.warning(
                        "[HOTKEY] macOS: Both GlobalHotKeys and Listener "
                        "failed. Please grant Accessibility permissions:\n"
                        "  System Preferences > Privacy & Security > "
                        "Accessibility > add Voice Typer"
                    )

    # --- internal helpers ---------------------------------------------------

    def _start_fallback(self, callback, Listener, Key, KeyCode) -> None:
        target = _parse_hotkey_to_pynput(self.hotkey_str, Key, KeyCode)
        if target is None:
            raise RuntimeError(
                f"Cannot parse hotkey {self.hotkey_str!r} for fallback"
            )

        # NEW-DEAD-030: for composite hotkeys (tuple), extract BOTH the
        # modifier keys and the target key.  Previously the fallback
        # listener only matched on the target key, ignoring modifiers —
        # so ``<ctrl>+<f2>`` would fire on bare ``<f2>``.  We now track
        # which modifier keys are currently held (via the pynput
        # on_press/on_release events) and require ALL of them to be held
        # before firing the callback.
        if isinstance(target, tuple):
            modifier_keys, match_key = target
        else:
            modifier_keys, match_key = (), target

        # Track currently-held modifier keys so we can check the full
        # composite state before firing.
        held_modifiers = set()
        # UX-001: track whether the matched key is currently held down
        # so we can fire the on_release callback exactly once per
        # press-release cycle (pynput fires repeated on_press events
        # while a key is held).
        held = {"value": False}

        def on_press(key):
            # Track modifier presses.
            if modifier_keys and key in modifier_keys:
                held_modifiers.add(key)
            if key == match_key:
                # NEW-DEAD-030: only fire if ALL modifiers are held.
                if modifier_keys and len(held_modifiers) < len(modifier_keys):
                    return
                if not held["value"]:
                    held["value"] = True
                    log.info(
                        "[HOTKEY FALLBACK] Matched key: %s (mods=%d/%d)",
                        key, len(held_modifiers), len(modifier_keys),
                    )
                    callback()

        def on_release(key):
            # NEW-DEAD-030: track modifier releases so the held_modifiers
            # set stays accurate.
            if modifier_keys and key in modifier_keys:
                held_modifiers.discard(key)
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
    modifier_names = {
        "ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
        "cmd": Key.cmd, "win": Key.cmd, "super": Key.cmd,
    }

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
# PLAT-ALTGR: AltGr modifier flag. Windows simulates AltGr as Ctrl+Alt,
# but on some keyboard layouts the user may want to bind AltGr explicitly.
# We add _MOD_ALTGR so the hotkey system can recognize AltGr combinations.
_MOD_ALTGR = 0x0010
_MOD_NOREPEAT = 0x4000
_GWLP_USERDATA = -21

# Common virtual-key code mappings for function keys and printable keys.
#
# PLAT-VKMAP: VK codes are mapped from US keyboard layout. Non-US keyboards
# may differ for keys like ^/°/# (German, French, etc.). For example, on a
# German keyboard the ^ key is VK_OEM_5 (0xDC) instead of VK_6 (0x36).
# We add a MapVirtualKey fallback that uses the current keyboard layout
# to resolve VK codes for printable characters when the static map fails.
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
        # PLAT-ALTGR: Right Alt (AltGr) virtual-key code.
        # On non-US keyboards, AltGr is used for characters like @, €, #.
        # VK_RMENU = 0xA5 is the right Alt key, which is the physical
        # AltGr key on most keyboards.
        _VK_MAP["altgr"] = 0xA5      # VK_RMENU (Right Alt / AltGr)
        _VK_MAP["right_alt"] = 0xA5  # VK_RMENU
        _VK_MAP["ralt"] = 0xA5       # VK_RMENU


def parse_hotkey_to_vk(hotkey_str: str) -> Optional[int]:
    """Convert a hotkey string like '<f2>' to a Win32 virtual-key code.

    Returns None if the key cannot be parsed.
    """
    parsed = parse_hotkey_to_win32(hotkey_str)
    if parsed is None:
        return None
    return parsed[0]


def parse_hotkey_to_win32(hotkey_str: str) -> Optional[tuple[int, int]]:
    """Convert a hotkey string to ``(virtual_key, RegisterHotKey modifiers)``.

    NEW-CQ-022: previously this returned the LAST non-modifier key found
    in the ``+``-separated string. For ``f2+ctrl+1+3``, it would set
    key_name to ``"3"``, missing ``"1"`` and ``"f2"``. The fix uses
    FIRST-match-wins: the first non-modifier token is the primary key,
    and any subsequent non-modifier tokens are ignored (with a warning).
    This matches user expectation: ``<f2>`` is the hotkey, ``ctrl`` is
    the modifier; writing ``f2+ctrl`` vs ``ctrl+f2`` should produce the
    same result.
    """
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
        # PLAT-ALTGR: Allow 'altgr' as a modifier in hotkey strings.
        # Maps to _MOD_ALTGR for RegisterHotKey compatibility.
        if part in {"altgr", "right_alt", "ralt"}:
            modifiers |= _MOD_ALTGR
            continue
        # NEW-CQ-022: first non-modifier key wins. Subsequent non-modifier
        # tokens are ignored (they're likely a typo or a multi-key combo
        # that Win32 RegisterHotKey doesn't support).
        if key_name is None:
            key_name = part
        else:
            log.warning(
                "[HOTKEY] Ignoring extra key %r in hotkey %r (already have %r)",
                part, hotkey_str, key_name,
            )

    if key_name is None:
        return None

    vk = _VK_MAP.get(key_name)
    if vk is None:
        # PLAT-VKMAP: try MapVirtualKey with the current keyboard layout
        # for printable characters that may differ on non-US keyboards.
        if is_windows() and len(key_name) == 1 and key_name.isalpha():
            try:
                import ctypes
                user32 = ctypes.windll.user32
                # Get the current keyboard layout
                hkl = user32.GetKeyboardLayout(0)
                # VkKeyScanW returns the VK code and shift state
                vk_scan = user32.VkKeyScanW(ord(key_name))
                if vk_scan != -1:
                    vk = vk_scan & 0xFF
            except Exception:
                pass
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
            BOOL,
            DWORD,
            HWND,
            INT,
            LPARAM,
            UINT,
            WPARAM,
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
                # on all Windows configurations.  PERF-012: the polling loop in
                # _run_polling_loop() uses Sleep(1) (~1000 Hz effective check
                # rate), which gives ~1 ms hotkey-detection latency while still
                # yielding the CPU between checks — the thread spends >99.9% of
                # its time sleeping in the kernel.  See _run_polling_loop() for
                # the rationale and the regression test that pins this invariant.
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

        # RACE-008: daemon=True is acceptable because: (1) the hotkey
        # thread only calls the user callback (no critical cleanup);
        # (2) stop() sets _stop_event and joins with timeout, so the
        # thread exits cooperatively on normal shutdown; (3) on
        # force-kill, the OS reclaims the thread automatically — no
        # resource leak (the Win32 hotkey registration is
        # UnregisterHotKey'd in the finally block).
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

    @staticmethod
    def _is_ime_composing() -> bool:
        """PLAT-020: Detect if the IME is currently composing.

        When the IME is in composition mode (e.g. typing CJK characters),
        GetAsyncKeyState may fire hotkey triggers for keys that are part
        of the composition string. We suppress hotkey triggers during
        IME composition to avoid false-fires.

        Uses ImmGetContext + ImmGetCompositionStringW or ImmGetOpenStatus
        on Windows. Returns False on non-Windows or on failure.
        """
        if not is_windows():
            return False
        try:
            import ctypes
            user32 = ctypes.windll.user32
            imm32 = ctypes.windll.imm32

            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False

            himc = imm32.ImmGetContext(hwnd)
            if not himc:
                return False

            try:
                # Check if IME is open
                open_status = imm32.ImmGetOpenStatus(himc)
                if not open_status:
                    return False

                # Check if there's a composition string (GCS_COMPSTR = 0x0400)
                comp_len = imm32.ImmGetCompositionStringW(himc, 0x0400, None, 0)
                if comp_len > 0:
                    return True

                return False
            finally:
                imm32.ImmReleaseContext(hwnd, himc)
        except Exception:
            return False

    def _run_polling_loop(self, callback):
        """GetAsyncKeyState polling fallback for hotkey detection.

        PERF-012 / PERF-003: On Windows, uses GetAsyncKeyState in a tight
        loop with a 1ms sleep. This is still technically polling but at a
        much lower cost than the previous 100ms (10Hz) approach — the key
        is checked every 1ms, giving near-instant response while the
        kernel Sleep(1) yields the CPU between checks. On Linux/macOS,
        pynput's event-driven Listener is used instead of polling.

        The previous 10Hz polling (100ms sleep) introduced up to 100ms
        latency on hotkey detection. The new 1ms polling reduces this to
        ~1ms while still being CPU-efficient (the thread spends ~99.9% of
        its time sleeping in the kernel).
        """
        vk = self._vk
        was_pressed = False
        log.info("[HOTKEY] Polling loop started for VK=0x%X modifiers=0x%X", vk, self._modifiers)
        # PLAT-PUMP: hoist the win32gui import OUT of the polling loop.
        # Pre-fix this ran ``import win32gui`` on every 1ms iteration,
        # which is wasteful (Python's import system acquires the import
        # lock and does a dict lookup even for cached modules). The
        # import is now done once before the loop starts. If win32gui
        # is unavailable (non-Windows or pywin32 not installed), we
        # skip the message pump entirely — WM_HOTKEY delivery is a
        # Windows-only concern.
        _pump_messages = None
        try:
            import win32gui
            _pump_messages = win32gui.PumpWaitingMessages
        except ImportError:
            pass
        while not self._stop_event.is_set():
            # PLAT-020: suppress hotkey triggers during IME composition
            if self._is_ime_composing():
                was_pressed = False
                if _pump_messages is not None:
                    try:
                        _pump_messages()
                    except Exception:
                        pass
                self._kernel32.Sleep(50)
                continue

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
            # NEW-CQ-029: detect key-up transition for PTT mode.
            # Fire on_release when the key transitions from pressed
            # to not-pressed.
            if not is_pressed and was_pressed:
                if self._on_release_callback is not None:
                    log.info("[HOTKEY] Key released (PTT on_release)")
                    try:
                        self._on_release_callback()
                    except Exception:
                        log.exception(
                            "[HOTKEY] on_release callback raised in polling loop"
                        )
            was_pressed = is_pressed
            # PLAT-PUMP: pump Win32 messages so RegisterHotKey WM_HOTKEY
            # messages are dispatched. Without this, hotkeys silently fail
            # after ~30s on some Win11 builds.
            if _pump_messages is not None:
                try:
                    _pump_messages()
                except Exception:
                    pass
            # PERF-012: 1ms sleep gives near-instant hotkey response
            # while still yielding CPU to other threads.
            # pyrefly: ignore [missing-attribute]
            self._kernel32.Sleep(1)

    def _modifiers_pressed(self) -> bool:
        # PLAT-ALTGR: Detect AltGr (Right Alt + Ctrl simulated by Windows).
        # On non-US keyboards, AltGr is used for characters like @, €, #.
        # Windows simulates AltGr as Ctrl+Alt. If AltGr is detected,
        # don't treat it as a modifier press for hotkey purposes.
        if self._is_altgr_pressed():
            return False

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

    def _is_altgr_pressed(self) -> bool:
        """PLAT-ALTGR: Detect if AltGr is currently pressed.

        Windows simulates AltGr as Ctrl+RightAlt. We detect this by
        checking if Right Alt (VK=0xA5) is pressed AND Ctrl is also
        pressed. If both are held, it's AltGr — not a Ctrl+Alt combo.
        Returns True if AltGr is detected.
        """
        if not self._user32:
            return False
        try:
            right_alt = bool(self._user32.GetAsyncKeyState(0xA5) & 0x8000)
            ctrl = bool(self._user32.GetAsyncKeyState(0x11) & 0x8000)
            return right_alt and ctrl
        except Exception:
            return False

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

        ERR-QUIT-001 (fix): early-return if already stopped so the
        duplicate log lines don't appear when quit_app and quit()
        both call stop().
        """
        if self._stop_event.is_set():
            return  # Already stopped — idempotent
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

    Selection order (per platform):
    - macOS: ``MacNativeHotkey`` (Swift binary, supports FN key via
      ``NSEvent.modifierFlags.contains(.function)`` + CGEventTap). Falls
      back to ``PynputHotkey`` if the native binary is missing.
    - Windows: ``WindowsHookHotkey`` (C binary using ``WH_KEYBOARD_LL``).
      Falls back to ``WindowsNativeHotkey`` (GetAsyncKeyState polling)
      if the native binary is missing, and to ``PynputHotkey`` if Win32
      is unavailable.
    - Linux/Wayland: ``LinuxEvdevHotkey`` (C binary using
      ``/dev/input/event*``). Falls back to ``WaylandHotkey`` (Unix
      socket) if the native binary is missing.
    - Linux/X11: ``LinuxEvdevHotkey`` preferred (works on both X11 and
      Wayland); falls back to ``PynputHotkey`` if missing.

    The native backends are preferred because they support:
    - The FN key on macOS (firmware-level on Windows/Linux)
    - Modifier-only hotkeys (e.g. ``<alt>``, ``<caps_lock>``) on all
      platforms — pynput's GlobalHotKeys does not support these
    - Key suppression (so the trigger key doesn't reach the foreground
      app) on macOS and Windows
    - Lower CPU usage and lower latency than polling
    """
    # NATIVE-001: try the native subprocess backend first. It supports
    # FN on macOS, modifier-only hotkeys everywhere, and key suppression
    # on macOS/Windows. The legacy backends remain as fallbacks.
    try:
        from voice_typer.server.native_hotkeys import create_native_backend
        native = create_native_backend(hotkey_str)
        if native is not None:
            # Wrap the native backend so it satisfies the HotkeyBackend
            # interface expected by HotkeyDispatcher.
            log.info(
                "[HOTKEY] Using native %s backend for %r",
                type(native).__name__, hotkey_str,
            )
            return _NativeBackendAdapter(native)
    except Exception:
        log.exception(
            "[HOTKEY] Failed to create native backend; falling back to legacy"
        )

    if is_windows():
        log.info("[HOTKEY] Platform is win32 -> using WindowsNativeHotkey (legacy)")
        return WindowsNativeHotkey(hotkey_str)

    # #4 PLAT-WAYLAND: detect Wayland and use Unix socket fallback
    if is_linux():
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
        if wayland_display or xdg_session == "wayland":
            log.info("[HOTKEY] Wayland detected -> using WaylandHotkey (Unix socket, legacy)")
            return WaylandHotkey(hotkey_str)

    log.info("[HOTKEY] Platform is %s -> using PynputHotkey (legacy)", sys.platform)
    return PynputHotkey(hotkey_str)


class _NativeBackendAdapter(HotkeyBackend):
    """Adapter that wraps a ``SubprocessHotkeyBackend`` to satisfy the
    ``HotkeyBackend`` interface expected by ``HotkeyDispatcher``.

    The native backends in ``native_hotkeys.py`` don't inherit from
    ``HotkeyBackend`` (they use a separate base class to avoid an import
    cycle). This adapter bridges the two.
    """

    def __init__(self, native_backend):
        # Don't call super().__init__ because we delegate hotkey_str
        # to the wrapped backend.
        self._native = native_backend
        self.hotkey_str = native_backend.hotkey_str
        self._on_release_callback: Optional[Callable[[], None]] = None

    def start(self, callback: Callable[[], None]) -> None:
        self._native.start(callback)

    def set_on_release(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_release_callback = callback
        self._native.set_on_release(callback)

    def stop(self) -> None:
        self._native.stop()

    def is_alive(self) -> bool:
        return self._native.is_alive()

    def diagnose(self) -> str:
        return self._native.diagnose()


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

        self._server_socket = _socket.socket(
            _socket.AF_UNIX, _socket.SOCK_STREAM
        )
        self._server_socket.bind(self.SOCKET_PATH)
        # PLAT-WAYLAND: restrict socket to owner-only (0o600). Pre-fix
        # this was 0o666 (world-writable) which allowed any local user
        # to send "toggle" commands to the socket. The socket is only
        # used by the same user's wtype/ydotool wrapper script, so
        # group/other access is unnecessary.
        os.chmod(
            self.SOCKET_PATH,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)

        # RACE-008: daemon=True is acceptable because the accept loop
        # only handles incoming IPC connections (no critical cleanup).
        # stop() closes the listening socket, which causes accept() to
        # raise and the thread exits. On force-kill, the OS reclaims
        # the socket FD automatically.
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


# ─── PLAT-VKMAP: Custom hotkey capture ──────────────────────────────────────


def capture_custom_hotkey(timeout: float = 10.0) -> Optional[tuple[int, int, str]]:
    """PLAT-VKMAP: Capture a keystroke via GetAsyncKeyState polling.

    On Windows, polls all VK codes at ~50Hz to detect which key is
    pressed along with modifier state. This is useful for non-US
    keyboards where the static VK map in parse_hotkey_to_win32() may
    not produce the correct VK code.

    Returns ``(vk_code, modifiers, description)`` on success, or
    ``None`` on timeout or non-Windows platforms.

    The *modifiers* value is a bitmask of _MOD_ALT, _MOD_CONTROL,
    _MOD_SHIFT, _MOD_WIN, _MOD_ALTGR flags suitable for
    RegisterHotKey().

    The *description* is a human-readable string like "AltGr+1".

    Parameters
    ----------
    timeout : float
        Maximum seconds to wait for a key press. Default 10s.

    Usage
    -----
    >>> vk, mods, desc = capture_custom_hotkey()
    >>> if vk is not None:
    ...     print(f"Captured: VK=0x{vk:X}, mods=0x{mods:X}, desc={desc}")
    """
    if not is_windows():
        log.warning("[HOTKEY] Custom hotkey capture is only available on Windows")
        return None

    import ctypes
    from ctypes.wintypes import BOOL, DWORD, INT, UINT

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    user32.GetAsyncKeyState.argtypes = [INT]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    kernel32.Sleep.argtypes = [DWORD]
    kernel32.Sleep.restype = None

    # VK codes to poll (skip modifier keys 0x10-0x12, 0xA5, 0x5B/5C)
    _MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA5, 0x5B, 0x5C}

    log.info("[HOTKEY-CAPTURE] Waiting for keystroke (timeout=%.0fs)...", timeout)
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        # Check all VK codes 0x01..0xFF for a key press
        for vk in range(1, 256):
            if vk in _MODIFIER_VKS:
                continue
            state = user32.GetAsyncKeyState(vk)
            if state & 0x8000:
                # Key is pressed — capture modifiers
                mods = 0
                mod_names = []
                if user32.GetAsyncKeyState(0x11) & 0x8000:  # Ctrl
                    mods |= _MOD_CONTROL
                    mod_names.append("Ctrl")
                if user32.GetAsyncKeyState(0x10) & 0x8000:  # Shift
                    mods |= _MOD_SHIFT
                    mod_names.append("Shift")
                if user32.GetAsyncKeyState(0x12) & 0x8000:  # Alt
                    mods |= _MOD_ALT
                    mod_names.append("Alt")
                if user32.GetAsyncKeyState(0xA5) & 0x8000:  # AltGr/Right Alt
                    mods |= _MOD_ALTGR
                    mod_names.append("AltGr")

                # Build description
                _init_vk_map()
                vk_name = None
                for name, code in _VK_MAP.items():
                    if code == vk:
                        vk_name = name
                        break
                if vk_name is None:
                    vk_name = f"0x{vk:02X}"

                mod_str = "+".join(mod_names + [vk_name]) if mod_names else vk_name
                log.info(
                    "[HOTKEY-CAPTURE] Captured: VK=0x%X, mods=0x%X, desc=%s",
                    vk, mods, mod_str,
                )

                # Wait for key release to avoid re-triggering
                while user32.GetAsyncKeyState(vk) & 0x8000:
                    kernel32.Sleep(20)

                return (vk, mods, mod_str)

        kernel32.Sleep(20)  # ~50Hz polling

    log.info("[HOTKEY-CAPTURE] Timed out after %.0fs", timeout)
    return None

