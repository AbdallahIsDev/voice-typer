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
from typing import Any, Callable, Optional
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux
from voice_typer.server.branding import APP_NAME

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

    RW-1 (Hotkey parser unification): this now delegates to the
    canonical :func:`voice_typer.server.hotkey_spec.parse_hotkey` for
    tokenisation and alias resolution. The pynput-specific concerns
    that remain in this function are:

    - Modifier ``Key`` collapsing: canonical ``win`` / ``super`` /
      ``cmd`` all map to ``Key.cmd`` (pynput does not distinguish —
      it has ``Key.cmd`` / ``Key.cmd_l`` / ``Key.cmd_r`` but no
      ``Key.win`` or ``Key.super``).
    - ``Key`` / ``KeyCode`` conversion: pynput's ``Key`` enum (for
      named keys like ``f2``, ``space``, ``enter``) and
      ``KeyCode.from_char`` / ``from_vk`` (for letters, digits, and
      function keys not in the enum).
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    def _to_pynput_key(name: str):
        """Convert a canonical key name to a pynput Key/KeyCode, or None."""
        if hasattr(Key, name):
            return getattr(Key, name)
        if name.startswith("f") and name[1:].isdigit():
            fnum = int(name[1:])
            if 1 <= fnum <= 24:
                return KeyCode.from_vk(0x6F + fnum)
        if len(name) == 1:
            return KeyCode.from_char(name)
        return None

    def _to_pynput_modifier(name: str):
        """Convert a canonical modifier name to a pynput Key, or None.

        pynput collapses win/super/cmd → Key.cmd (no Key.win or
        Key.super exists). alt_gr maps to Key.alt_gr when available
        (platform-dependent), otherwise Key.alt_r, otherwise None.
        fn maps to Key.fn when available (macOS only), otherwise None.
        """
        _CANONICAL_TO_PYNPUT = {
            "ctrl": "ctrl",
            "shift": "shift",
            "alt": "alt",
            # pynput collapses win/super → cmd (no Key.win / Key.super).
            "cmd": "cmd",
            "win": "cmd",
            "super": "cmd",
        }
        attr = _CANONICAL_TO_PYNPUT.get(name)
        if attr is not None and hasattr(Key, attr):
            return getattr(Key, attr)
        # alt_gr / fn: try the canonical name, fall back to None.
        if name == "alt_gr":
            for fallback in ("alt_gr", "alt_r"):
                if hasattr(Key, fallback):
                    return getattr(Key, fallback)
        if name == "fn" and hasattr(Key, "fn"):
            return getattr(Key, "fn")
        return None

    parsed = parse_hotkey(hotkey_str)
    if parsed.is_empty:
        return None

    # Single-modifier special case (preserves the prior behaviour where
    # a 1-part spec like ``<alt>`` returns ``Key.alt`` directly rather
    # than ``(modifiers, target)``). For multi-modifier specs with no
    # main key (e.g. ``<ctrl>+<shift>``), pynput cannot match without a
    # target key — return None, matching the previous behaviour.
    if not parsed.keys:
        if len(parsed.modifiers) == 1:
            mod_key = _to_pynput_modifier(parsed.modifiers[0])
            return mod_key  # may be None if pynput lacks the attribute
        return None

    target = _to_pynput_key(parsed.keys[0])
    if target is None:
        return None

    modifier_keys = []
    for mod in parsed.modifiers:
        mod_key = _to_pynput_modifier(mod)
        if mod_key is not None:
            modifier_keys.append(mod_key)

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

# FIX-HOTKEY-ARCHITECTURE: VK codes for the modifier keys themselves,
# used by the modifier-only polling loop (e.g. when the hotkey is just
# ``<alt>`` with no main key). VK_CAPITAL is the Caps Lock key, used
# for toggle suppression in the legacy polling backend.
_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_MENU = 0x12      # VK_MENU covers both LAlt (0xA4) and RAlt (0xA5)
_VK_LWIN = 0x5B
_VK_RWIN = 0x5C
_VK_CAPITAL = 0x14   # Caps Lock — also in _VK_MAP["caps_lock"]
_VK_RMENU = 0xA5     # Right Alt / AltGr
_KEYEVENTF_KEYUP = 0x0002

# Common virtual-key code mappings for function keys and printable keys.
#
# ISSUE-3 (Key-name maps): this table maps pynput-style lowercase names
# to Win32 VK codes. It is ONE OF THREE independent key-name tables:
#
#   Frontend: KEY_CODE_TO_PYNPUT (hotkey-utils.ts) — e.code → pynput name
#   Backend:  _VK_MAP (here) — pynput name → Win32 VK code
#   Native:   _normalize_key_name (native_hotkeys.py) — pynput name →
#             wire-protocol name (CapsLock, Space, MediaNext, etc.)
#
# All three must agree on the set of names ("f1", "space", "caps_lock",
# "page_up", etc.). _normalize_key_name is the canonical transformer.
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


def parse_hotkey_to_win32(hotkey_str: str) -> Optional[tuple[Optional[int], int]]:
    """Convert a hotkey string to ``(virtual_key, RegisterHotKey modifiers)``.

    NEW-CQ-022: previously this returned the LAST non-modifier key found
    in the ``+``-separated string. For ``f2+ctrl+1+3``, it would set
    key_name to ``"3"``, missing ``"1"`` and ``"f2"``. The fix uses
    FIRST-match-wins: the first non-modifier token is the primary key,
    and any subsequent non-modifier tokens are ignored (with a warning).
    This matches user expectation: ``<f2>`` is the hotkey, ``ctrl`` is
    the modifier; writing ``f2+ctrl`` vs ``ctrl+f2`` should produce the
    same result.

    FIX-HOTKEY-ARCHITECTURE: for modifier-only specs (e.g. ``<alt>``,
    ``<ctrl>+<shift>``) where no main key is present, this now returns
    ``(None, modifiers)`` instead of ``None``. Callers can detect the
    modifier-only case by checking ``vk is None and modifiers != 0``.
    ``parse_hotkey_to_vk`` still returns ``None`` for these specs (it
    returns ``parsed[0]`` which is ``None``), preserving the existing
    contract for callers that only care about the VK code.

    RW-1 (Hotkey parser unification): this now delegates to the
    canonical :func:`voice_typer.server.hotkey_spec.parse_hotkey` for
    tokenisation and alias resolution. The Win32-specific concerns
    that remain in this function are:

    - Modifier-bit collapsing: canonical ``win`` / ``super`` / ``cmd``
      all map to ``_MOD_WIN`` (Windows does not distinguish between
      them — ``RegisterHotKey`` uses a single bit). Canonical
      ``alt_gr`` maps to ``_MOD_ALTGR``.
    - VK-code lookup: ``_VK_MAP`` (and the ``MapVirtualKey`` fallback
      for non-US layouts) translate the canonical key name to a
      Windows virtual-key code.
    """
    from voice_typer.server.hotkey_spec import parse_hotkey

    _init_vk_map()
    parsed = parse_hotkey(hotkey_str)

    # Map canonical modifier names to Win32 RegisterHotKey modifier bits.
    # Win32 collapses win/super/cmd → _MOD_WIN (single bit).
    _CANONICAL_TO_MODBIT = {
        "ctrl": _MOD_CONTROL,
        "shift": _MOD_SHIFT,
        "alt": _MOD_ALT,
        "alt_gr": _MOD_ALTGR,
        "cmd": _MOD_WIN,
        "win": _MOD_WIN,
        "super": _MOD_WIN,
        # 'fn' has no Win32 RegisterHotKey equivalent (firmware-only).
        # It is silently ignored here — callers that care about Fn
        # use the native_hotkeys backend instead.
    }

    modifiers = 0
    for mod in parsed.modifiers:
        bit = _CANONICAL_TO_MODBIT.get(mod, 0)
        modifiers |= bit

    key_name = parsed.main_key
    # NEW-CQ-022: first non-modifier key wins. Subsequent non-modifier
    # tokens are ignored (they're likely a typo or a multi-key combo
    # that Win32 RegisterHotKey doesn't support). Emit a warning for
    # each extra key, preserving the previous diagnostic behaviour.
    if len(parsed.keys) > 1:
        for extra in parsed.keys[1:]:
            log.warning(
                "[HOTKEY] Ignoring extra key %r in hotkey %r (already have %r)",
                extra, hotkey_str, key_name,
            )

    if key_name is None:
        # FIX-HOTKEY-ARCHITECTURE: modifier-only spec (e.g. <alt>,
        # <ctrl>+<shift>). Return (None, modifiers) so callers can
        # detect the modifier-only case and use a polling loop that
        # detects modifier press/release without requiring a main key.
        # If there are also no modifiers, the spec is genuinely invalid
        # (e.g. empty string, "<>"), so return None.
        if modifiers:
            return (None, modifiers)
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

    FIX-HOTKEY-ARCHITECTURE:
    - Modifier-only hotkeys (``<alt>``, ``<ctrl>``, ``<shift>``,
      ``<win>``) are now supported via a dedicated polling loop that
      detects modifier press/release WITHOUT requiring a non-modifier
      main key. Previously these specs were rejected at start() time
      with ``ValueError("Cannot parse...")``.
    - When the hotkey is Caps Lock (``<caps_lock>``), the polling loop
      suppresses the OS-level caps-state toggle by sending a synthetic
      Caps Lock keypress via ``keybd_event`` immediately after the
      physical press is detected. This mirrors the suppression the
      native ``windows-key-listener.exe`` binary performs via its
      ``WH_KEYBOARD_LL`` hook (see ``should_suppress_keydown`` in
      ``native/windows-key-listener.c``).
    """

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()  # signalled when registration completes
        self._hotkey_id = 1  # arbitrary ID for RegisterHotKey
        self._registered = False
        # TASK-10: typed as Any — these are populated inside _register()
        # via ctypes.windll (Windows-only). They remain None on non-Windows
        # platforms, but the methods that touch them (the message-pump
        # loop, _unregister) are only invoked from Windows-only code paths.
        self._user32: Any = None
        self._kernel32: Any = None
        self._success = False
        self._vk: Optional[int] = None
        self._modifiers = 0
        self._using_polling = False  # True if falling back to GetAsyncKeyState
        # FIX-HOTKEY-ARCHITECTURE: True when the hotkey is a modifier-only
        # spec (e.g. ``<alt>``). The polling loop uses a different code
        # path for these — see ``_run_modifier_only_polling_loop``.
        self._is_modifier_only: bool = False
        # FIX-HOTKEY-ARCHITECTURE: brief flag set while we're sending a
        # synthetic Caps Lock keypress to undo the OS-level toggle.
        # The polling loop skips processing while this is set so the
        # synthetic events don't re-trigger the callback.
        self._caps_lock_suppressing: bool = False
        # PERF-FIX-1: throttled IME composition check. The underlying
        # ``_is_ime_composing()`` staticmethod makes 5 syscalls per call
        # (GetForegroundWindow, ImmGetContext, ImmGetOpenStatus,
        # ImmGetCompositionStringW, ImmReleaseContext). At the polling
        # loop's 1ms cadence that's ~5000 syscalls/sec even when no key
        # is pressed. The throttled wrapper
        # ``_is_ime_composing_throttled()`` re-queries at most every
        # 50ms (20 Hz) — IME state changes at human typing speed so
        # 50ms latency is invisible to the user.
        self._last_ime_check_time: float = 0.0
        self._last_ime_composing: bool = False
        # PERF-FIX-1: throttled non-modifier key scan.
        # ``_any_non_modifier_key_pressed()`` calls GetAsyncKeyState for
        # each VK in 0x08-0xFF (248 codes) — O(248) per iteration. The
        # throttled wrapper re-scans at most every 50ms, reducing the
        # idle-state syscall rate from ~248k/sec to ~5k/sec.
        self._last_nonmod_check_time: float = 0.0
        self._last_nonmod_pressed: bool = False

    def start(self, callback: Callable[[], None]) -> None:
        import ctypes
        import ctypes.wintypes

        parsed = parse_hotkey_to_win32(self.hotkey_str)
        if parsed is None:
            raise ValueError(
                f"Cannot parse hotkey {self.hotkey_str!r} to a VK code"
            )
        self._vk, self._modifiers = parsed

        # FIX-HOTKEY-ARCHITECTURE: detect modifier-only specs (e.g.
        # ``<alt>``). For these, ``vk`` is None but ``modifiers`` is
        # non-zero. RegisterHotKey can't be used (no main VK to
        # register), so we skip it and rely on the polling loop's
        # modifier-only detection path.
        self._is_modifier_only = (self._vk is None and self._modifiers != 0)
        if self._vk is None and not self._is_modifier_only:
            raise ValueError(
                f"Cannot parse hotkey {self.hotkey_str!r} to a VK code"
            )

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
                # FIX-HOTKEY-ARCHITECTURE: skip RegisterHotKey for
                # modifier-only hotkeys (``<alt>``, ``<ctrl>``, etc.).
                # RegisterHotKey requires a main VK code and won't
                # accept a bare modifier — calling it with vk=0 fails
                # with ERROR_INVALID_PARAMETER (87). The polling loop's
                # modifier-only detection path handles these specs
                # directly via GetAsyncKeyState on the modifier VK.
                if self._is_modifier_only:
                    log.info(
                        "[HOTKEY] Modifier-only hotkey (mods=0x%X) — skipping "
                        "RegisterHotKey, using polling-only detection",
                        self._modifiers,
                    )
                else:
                    # Register the hotkey.  Pass NULL (0) as hWnd.
                    # RegisterHotKey(NULL, ...) binds the hotkey to the calling
                    # thread so WM_HOTKEY is posted to the thread message queue.

                    result = self._user32.RegisterHotKey(
                        0, self._hotkey_id, _MOD_NOREPEAT | self._modifiers, self._vk
                    )
                    if not result:

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

                    self._user32.UnregisterHotKey(0, self._hotkey_id)
                    self._registered = False
                    log.debug("[HOTKEY] Unregistered %s", self.hotkey_str)

        # Also set GetAsyncKeyState argtypes for the polling fallback
        self._user32.GetAsyncKeyState.argtypes = [INT]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short

        # Set Sleep argtypes
        self._kernel32.Sleep.argtypes = [DWORD]
        self._kernel32.Sleep.restype = None

        # FIX-HOTKEY-ARCHITECTURE: set argtypes for keybd_event and
        # GetKeyState, used by _suppress_caps_lock_toggle() to undo the
        # OS-level caps-lock toggle when the hotkey is <caps_lock>.
        # VOID keybd_event(BYTE bVk, BYTE bScan, DWORD dwFlags, ULONG_PTR dwExtraInfo)
        # ULONG_PTR isn't exposed by ctypes.wintypes on non-Windows; use
        # WPARAM (which is pointer-sized on both 32- and 64-bit Windows)
        # as a portable stand-in.
        self._user32.keybd_event.argtypes = [
            ctypes.wintypes.BYTE, ctypes.wintypes.BYTE, DWORD, WPARAM,
        ]
        self._user32.keybd_event.restype = None
        # SHORT GetKeyState(int nVirtKey) — returns toggle/pressed state
        self._user32.GetKeyState.argtypes = [INT]
        self._user32.GetKeyState.restype = ctypes.c_short

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

    def _is_ime_composing_throttled(self) -> bool:
        """PERF-FIX-1: throttled wrapper around ``_is_ime_composing()``.

        The underlying staticmethod makes 5 syscalls per call (see
        ``__init__`` for the rationale). The polling loop runs at 1ms
        cadence, so calling it every iteration would be ~5000
        syscalls/sec. This wrapper re-queries at most every 50ms (20 Hz)
        and returns the cached result between queries.

        50ms latency is invisible to the user because IME state changes
        at human typing speed (each key press is ~50-150ms apart).
        """
        now = time.monotonic()
        if now - self._last_ime_check_time < 0.05:
            return self._last_ime_composing
        self._last_ime_composing = self._is_ime_composing()
        self._last_ime_check_time = now
        return self._last_ime_composing

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

        FIX-HOTKEY-ARCHITECTURE: dispatches to
        ``_run_modifier_only_polling_loop`` for modifier-only hotkeys
        (e.g. ``<alt>``) — those need a different detection logic that
        fires on the modifier press itself, not on a subsequent
        non-modifier keypress. Also suppresses the OS-level caps-lock
        toggle when the hotkey is ``<caps_lock>`` (see
        ``_suppress_caps_lock_toggle``).
        """
        # FIX-HOTKEY-ARCHITECTURE: modifier-only hotkeys (e.g. <alt>)
        # use a separate polling loop that fires on the modifier press
        # itself, not on a subsequent non-modifier keypress.
        if self._is_modifier_only:
            self._run_modifier_only_polling_loop(callback)
            return

        vk = self._vk
        # HOTKEY-DEFER-001 (Task 2.4): seed was_pressed from the current
        # key state at registration time. If the hotkey's main key is
        # currently held (e.g. the user just released it after capture
        # and the IPC set_config reached the backend before the keyUP),
        # the polling loop would otherwise see the still-held key as a
        # fresh press on the first iteration and immediately fire the
        # callback — starting recording without the user intending it.
        # Seeding was_pressed=True when the key is already held makes
        # the first iteration skip the "is_pressed and not was_pressed"
        # branch, requiring a genuine release+repress cycle before the
        # callback fires. This is defense-in-depth behind the frontend's
        # deferred-assignment fix (HotkeyPicker.tsx candidateRef).
        try:
            _seed_state = self._user32.GetAsyncKeyState(vk)
            _seed_mods = self._modifiers_pressed()
            was_pressed = bool(_seed_state & 0x8000) and _seed_mods
            if was_pressed:
                log.info(
                    "[HOTKEY] Backend registered while key VK=0x%X already held "
                    "— suppressing first keydown to avoid capture-triggers-recording race",
                    vk,
                )
        except Exception:
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
        # FIX-HOTKEY-ARCHITECTURE: detect Caps Lock hotkeys so we can
        # suppress the OS-level toggle. VK_CAPITAL = 0x14.
        is_caps_lock_hotkey = (vk == _VK_CAPITAL)

        # CAPS-LOCK-FIX: at registration time, if the hotkey is Caps Lock,
        # force caps lock OFF to prevent the user from typing in ALL CAPS.
        # This proactively handles the case where caps lock was ON before
        # the app started or when the hotkey configuration changes.
        if is_caps_lock_hotkey:
            self._ensure_caps_lock_off()

        # Iteration counter for periodic caps lock state checks (~200ms cadence).
        _caps_check_iter = 0

        while not self._stop_event.is_set():

            # CAPS-LOCK-FIX: periodically ensure caps lock stays OFF.
            # The reactive suppression on key press can fail due to timing
            # (OS toggles caps before we can undo it). A periodic ~200ms
            # check catches any missed toggles and re-silences caps lock.
            _caps_check_iter += 1
            if is_caps_lock_hotkey and _caps_check_iter % 200 == 0:
                if not self._caps_lock_suppressing:
                    self._ensure_caps_lock_off()
            # PLAT-020: suppress hotkey triggers during IME composition.
            # PERF-FIX-1: use the throttled wrapper so we don't make 5
            # syscalls per 1ms iteration.
            if self._is_ime_composing_throttled():
                was_pressed = False
                if _pump_messages is not None:
                    try:
                        _pump_messages()
                    except Exception:
                        pass
                self._kernel32.Sleep(50)
                continue

            # FIX-HOTKEY-ARCHITECTURE: if we're sending a synthetic
            # Caps Lock keypress to undo the OS toggle, skip processing
            # so the synthetic events don't re-trigger the callback or
            # prematurely fire on_release. The suppression flag is
            # cleared by _suppress_caps_lock_toggle() itself.
            if self._caps_lock_suppressing:
                if _pump_messages is not None:
                    try:
                        _pump_messages()
                    except Exception:
                        pass
                self._kernel32.Sleep(1)
                continue


            state = self._user32.GetAsyncKeyState(vk)
            is_pressed = bool(state & 0x8000) and self._modifiers_pressed() and not self._other_modifiers_pressed()
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
                # FIX-HOTKEY-ARCHITECTURE: suppress the OS-level
                # caps-lock toggle when the hotkey is <caps_lock>.
                # The OS toggles caps state as part of processing the
                # keyDown; we undo the toggle by sending a synthetic
                # Caps Lock keypress via keybd_event. This mirrors the
                # suppression the native windows-key-listener.exe
                # binary performs via WH_KEYBOARD_LL.
                if is_caps_lock_hotkey:
                    self._suppress_caps_lock_toggle()
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

            self._kernel32.Sleep(1)

    def _run_modifier_only_polling_loop(self, callback):
        """Polling loop for modifier-only hotkeys (e.g. ``<alt>``).

        FIX-HOTKEY-ARCHITECTURE: detects press/release of a single
        modifier key WITHOUT any other modifiers held. The previous
        polling loop required a non-modifier "main key" to be pressed,
        which made modifier-only hotkeys (like just Alt) non-functional
        — selecting Alt in the dropdown did nothing because there was no
        main key for GetAsyncKeyState to detect.

        FIX-HOTKEY-AND-NOTIFICATION: the loop was overhauled to fix two
        annoying misfire scenarios:

        a) **Alt+C (or any modifier+key combo) used to fire the dictation
           because Alt was pressed.** The fix: track whether ANY
           non-modifier key was pressed between the modifier press and
           release. If so, suppress the fire — the user was using a
           combo like Alt+C for copy, not invoking the dictation hotkey.

        b) **Press-and-hold used to fire the callback repeatedly.** The
           fix: fire the press callback exactly ONCE on the
           not-held → held transition (for push-to-talk mode) or ONCE
           on the held → not-held transition (for toggle mode). The
           callback is never re-fired while the modifier stays held.

        Per-mode behavior:

        **Toggle mode** (``_on_release_callback is None``):
        - On press: nothing (defer).
        - While held: monitor for non-modifier key presses; set
          ``_other_key_pressed`` if any are detected.
        - On release: if ``_other_key_pressed`` is False AND no other
          modifiers are currently held, fire the press callback (which
          is ``toggle_dictation``). Otherwise, do NOT fire — the user
          was using a combo.

        **Push-to-talk mode** (``_on_release_callback is not None``):
        - On press: if no other modifiers are held at the moment of
          press, fire the press callback immediately (start recording)
          and set ``press_fired = True``. (We can't predict future
          non-modifier key presses at the moment of press.)
        - While held: monitor for non-modifier key presses; set
          ``_other_key_pressed`` if any are detected.
        - On release: if ``press_fired`` is True, fire the
          ``on_release`` callback (stop recording) — this fires
          regardless of ``_other_key_pressed`` to prevent the recording
          from running forever. If ``press_fired`` is False (other
          modifiers were held at moment of press), do NOT fire
          ``on_release`` (nothing was started).
        """
        # Map the configured _MOD_* flags to the VK codes we need to poll.
        # VK_MENU (0x12) covers both LAlt (0xA4) and RAlt (0xA5).
        # VK_CONTROL (0x11) covers both LCtrl (0xA2) and RCtrl (0xA3).
        # VK_SHIFT (0x10) covers both LShift (0xA0) and RShift (0xA1).
        # VK_LWIN (0x5B) and VK_RWIN (0x5C) must both be polled.
        modifier_vks: list[int] = []
        if self._modifiers & _MOD_ALT:
            modifier_vks.append(_VK_MENU)
        if self._modifiers & _MOD_CONTROL:
            modifier_vks.append(_VK_CONTROL)
        if self._modifiers & _MOD_SHIFT:
            modifier_vks.append(_VK_SHIFT)
        if self._modifiers & _MOD_WIN:
            modifier_vks.append(_VK_LWIN)
            modifier_vks.append(_VK_RWIN)

        # FIX-HOTKEY-AND-NOTIFICATION: VK codes that count as "modifiers"
        # for the purposes of the non-modifier key scan. These are
        # excluded from the ``_any_non_modifier_key_pressed`` check
        # because holding another modifier (e.g. Ctrl while Alt is the
        # configured hotkey) is handled separately by
        # ``_other_modifiers_pressed`` — it shouldn't itself suppress
        # the fire (the user might press Ctrl+Alt intending both, but
        # that's a separate hotkey spec).
        all_modifier_vks = frozenset({
            _VK_SHIFT,       # 0x10
            _VK_CONTROL,     # 0x11
            _VK_MENU,        # 0x12 (Alt)
            _VK_CAPITAL,     # 0x14 (Caps Lock — handled separately)
            _VK_LWIN,        # 0x5B
            _VK_RWIN,        # 0x5C
            0xA0,            # VK_LSHIFT
            0xA1,            # VK_RSHIFT
            0xA2,            # VK_LCONTROL
            0xA3,            # VK_RCONTROL
            0xA4,            # VK_LMENU
            0xA5,            # VK_RMENU
        })

        log.info(
            "[HOTKEY] Modifier-only polling loop started (mods=0x%X, vks=%s)",
            self._modifiers, [f"0x{v:02X}" for v in modifier_vks],
        )

        # Per-press-cycle state flags (described in the docstring above).
        # FIX-HOTKEY-AND-NOTIFICATION: the old code used ``callback_fired``
        # to suppress repeat fires during press-and-hold. The new code
        # uses three flags:
        # - modifier_was_pressed: True if the configured modifier is
        #   currently in a "held" state (since the last release).
        # - other_key_pressed: True if ANY non-modifier key was pressed
        #   at any iteration since the modifier was pressed. Used to
        #   suppress the fire on release when the user was actually
        #   doing a combo like Alt+C.
        # - press_fired: (PTT only) True if the press callback already
        #   fired for this cycle. Used to decide whether on_release
        #   should fire on release.
        modifier_was_pressed = False
        other_key_pressed = False
        press_fired = False

        # PTT mode is detected by the presence of an on_release callback.
        # Toggle mode has _on_release_callback == None.
        is_ptt = self._on_release_callback is not None

        while not self._stop_event.is_set():
            # PLAT-020: suppress hotkey triggers during IME composition.
            # Reset all per-cycle state so a stray IME composition doesn't
            # leak into the next press cycle.
            # PERF-FIX-1: use the throttled wrapper so we don't make 5
            # syscalls per 1ms iteration.
            if self._is_ime_composing_throttled():
                modifier_was_pressed = False
                other_key_pressed = False
                press_fired = False
                self._kernel32.Sleep(50)
                continue

            # FIX-MULTI-MOD: require ALL configured modifiers to be held
            # simultaneously for multi-modifier combos like ``<ctrl>+<alt>``.
            # Previously used ``any()``, which meant pressing EITHER Ctrl
            # OR Alt alone would fire the hotkey — instead of requiring
            # BOTH to be pressed together.
            is_held = all(self._key_pressed(vk) for vk in modifier_vks)

            # ── Transition: not held → held (start of a new press cycle) ──
            if is_held and not modifier_was_pressed:
                modifier_was_pressed = True
                other_key_pressed = False
                press_fired = False
                # FIX-HOTKEY-AND-NOTIFICATION (b): press-and-hold must
                # NOT fire repeatedly. We fire the press callback at
                # most once per cycle, only on this not-held → held
                # transition. For toggle mode we don't fire on press
                # at all (we defer to release so we can verify the
                # modifier was released alone).
                if is_ptt:
                    # PTT mode: fire the press callback immediately IF
                    # no other modifiers are held at the moment of
                    # press. We can't predict future non-modifier key
                    # presses, so the "alone" check at press time only
                    # covers other modifiers.
                    if not self._other_modifiers_pressed():
                        log.info(
                            "[HOTKEY FIRED] Modifier-only press detected "
                            "(PTT, mods=0x%X)",
                            self._modifiers,
                        )
                        try:
                            callback()
                        except Exception:
                            log.exception(
                                "[HOTKEY] Callback raised in modifier-only "
                                "polling loop; hotkey still armed for next press"
                            )
                        press_fired = True
                    else:
                        log.debug(
                            "[HOTKEY] Modifier pressed but other modifiers "
                            "also held (mods=0x%X) — suppressing PTT press fire",
                            self._modifiers,
                        )

            # ── While held: monitor for non-modifier key presses ──
            # FIX-HOTKEY-AND-NOTIFICATION (a): this is the key fix for
            # the "Alt+C fires the dictation" problem. If the user
            # pressed any non-modifier key while holding our modifier,
            # they were using a combo (e.g. Alt+C for copy) — we'll
            # suppress the fire on release.
            #
            # PERF-FIX-1: the scan is O(248) per iteration
            # (GetAsyncKeyState for every VK in 0x08-0xFF). At the
            # polling loop's 1ms cadence that's up to ~248k syscalls/sec
            # while the modifier is held. The throttled wrapper
            # ``_any_non_modifier_key_pressed_throttled()`` re-scans at
            # most every 50ms (20 Hz), reducing the syscall rate to
            # ~5k/sec. The throttle is safe because:
            #   - the ``not other_key_pressed`` guard already ensures
            #     the scan stops once a non-modifier key is detected;
            #   - True results are NOT cached across releases (the
            #     wrapper only caches False), so the next press cycle
            #     always re-scans fresh;
            #   - 50ms detection latency for non-modifier keys is
            #     acceptable — typists press keys ≥50ms apart, and the
            #     polling loop's 1ms cadence still gives ~1ms modifier
            #     press/release latency (the scan throttle only affects
            #     combo detection, not the hotkey fire itself).
            # This scan is intentionally called every iteration while
            # held (NOT just on the not-held→held transition) because
            # the user can press a non-modifier key at any point during
            # the hold, and we need to detect it before the release
            # transition fires the callback.
            if is_held and not other_key_pressed:
                if self._any_non_modifier_key_pressed_throttled(all_modifier_vks):
                    other_key_pressed = True
                    log.debug(
                        "[HOTKEY] Non-modifier key pressed during modifier "
                        "hold (mods=0x%X) — will suppress fire on release "
                        "(user was doing a combo like Alt+C)",
                        self._modifiers,
                    )

            # ── Transition: held → not held (modifier itself released) ──
            if not is_held and modifier_was_pressed:
                if not other_key_pressed:
                    # Modifier was pressed and released without any
                    # non-modifier key in between. This is the "alone"
                    # case — fire the appropriate callback.
                    if is_ptt:
                        # PTT mode: fire on_release (stop recording) if
                        # we fired the press callback. If we didn't
                        # fire press (because other modifiers were held
                        # at the moment of press), don't fire on_release
                        # either (nothing was started).
                        if press_fired and self._on_release_callback is not None:
                            log.info(
                                "[HOTKEY] Modifier released alone "
                                "(PTT on_release, mods=0x%X)",
                                self._modifiers,
                            )
                            try:
                                self._on_release_callback()
                            except Exception:
                                log.exception(
                                    "[HOTKEY] on_release raised in "
                                    "modifier-only polling loop"
                                )
                    else:
                        # Toggle mode: fire the press callback
                        # (toggle_dictation). Double-check no other
                        # modifiers are currently held at release time
                        # — if the user is still holding Ctrl when they
                        # release Alt, that's a combo, not the hotkey.
                        if not self._other_modifiers_pressed():
                            log.info(
                                "[HOTKEY FIRED] Modifier-only press-and-release "
                                "alone (toggle, mods=0x%X)",
                                self._modifiers,
                            )
                            try:
                                callback()
                            except Exception:
                                log.exception(
                                    "[HOTKEY] Callback raised in modifier-only "
                                    "polling loop; hotkey still armed for next press"
                                )
                        else:
                            log.debug(
                                "[HOTKEY] Modifier released alone but other "
                                "modifiers still held (mods=0x%X) — suppressing "
                                "toggle fire (combo)",
                                self._modifiers,
                            )
                else:
                    # other_key_pressed is True — user was doing a combo
                    # like Alt+C. Per spec, do NOT fire the press callback.
                    # FIX-HOTKEY-AND-NOTIFICATION: for PTT mode, if we
                    # already fired the press callback (and thus started
                    # a recording), we MUST fire on_release to stop the
                    # recording — otherwise it would run forever. This
                    # is a safety net; the recording will be very short
                    # and the user will hear the brief dictation chime,
                    # but it's better than a stuck recording.
                    if is_ptt and press_fired and self._on_release_callback is not None:
                        log.info(
                            "[HOTKEY] Modifier released after combo "
                            "(PTT on_release safety, mods=0x%X) — stopping "
                            "recording started by the press fire",
                            self._modifiers,
                        )
                        try:
                            self._on_release_callback()
                        except Exception:
                            log.exception(
                                "[HOTKEY] on_release (safety) raised in "
                                "modifier-only polling loop"
                            )
                # Reset per-cycle state for the next press.
                modifier_was_pressed = False
                other_key_pressed = False
                press_fired = False

            # PERF-012: 1ms sleep gives near-instant hotkey response
            # while still yielding CPU to other threads.

            self._kernel32.Sleep(1)

    def _any_non_modifier_key_pressed(
        self, modifier_vks: "frozenset[int]"
    ) -> bool:
        """Return True if any non-modifier key is currently held down.

        FIX-HOTKEY-AND-NOTIFICATION: scans the Win32 virtual-key code
        space (0x08-0xFF) for any key that is currently held down,
        excluding the modifier VKs passed in ``modifier_vks``. Used by
        the modifier-only polling loop to detect when the user has
        pressed a non-modifier key (e.g. ``C``) while holding the
        configured modifier (e.g. ``Alt``) — that pattern indicates
        the user was doing a combo like Alt+C, not invoking the bare
        modifier hotkey, so the fire is suppressed on release.

        The scan covers the full VK range:
        - 0x08 (VK_BACK) through 0xFF (VK_OEM_CLEAR)
        - Excludes 0x10/0x11/0x12 (Shift/Ctrl/Menu) and 0x14 (Caps Lock)
        - Excludes 0x5B/0x5C (LWin/RWin)
        - Excludes 0xA0-0xA5 (LShift/RShift/LCtrl/RCtrl/LAlt/RAlt)

        Returns False on non-Windows or if no non-modifier key is held.

        PERF-FIX-1: this scan is O(248) per iteration (one
        ``GetAsyncKeyState`` per VK code). The modifier-only polling
        loop runs at 1ms cadence, so calling this every iteration while
        the modifier is held would be up to ~248k syscalls/sec. The
        loop wraps this call in
        ``_any_non_modifier_key_pressed_throttled()`` (see below) to
        re-scan at most every 50ms. The scan itself is NOT moved to
        the not-held→held transition because the user can press a
        non-modifier key at any point during the hold, and we need to
        detect it before the release transition fires the callback —
        only the throttle (50ms re-scan cadence) is applied.
        """
        if not self._user32:
            return False
        # Scan VK codes 0x08-0xFF inclusive. The +1 is because range()
        # is exclusive on the upper bound.
        for vk in range(0x08, 0x100):
            if vk in modifier_vks:
                continue
            try:

                if self._user32.GetAsyncKeyState(vk) & 0x8000:
                    return True
            except Exception:
                # If GetAsyncKeyState fails (e.g. on a non-Windows
                # test host with a partial mock), treat it as "no key
                # pressed" rather than crashing the polling loop.
                return False
        return False

    def _any_non_modifier_key_pressed_throttled(
        self, modifier_vks: "frozenset[int]"
    ) -> bool:
        """PERF-FIX-1: throttled wrapper around
        ``_any_non_modifier_key_pressed()``.

        The underlying scan is O(248) per call (see the docstring on
        ``_any_non_modifier_key_pressed`` for the rationale). The
        modifier-only polling loop runs at 1ms cadence, so calling it
        every iteration while the modifier is held would be up to
        ~248k syscalls/sec. This wrapper re-scans at most every 50ms
        (20 Hz), reducing the syscall rate to ~5k/sec.

        Cache semantics:

        - **False results are cached** for 50ms. Between scans the
          wrapper returns the cached False without touching
          ``GetAsyncKeyState``.
        - **True results are NOT cached across releases.** The polling
          loop stops calling this method once True is returned
          (``other_key_pressed`` becomes True), then resets
          ``other_key_pressed`` to False on modifier release. If we
          cached True across that boundary, the next press cycle would
          immediately see True (cache hit) and wrongly suppress the
          fire. So when the underlying scan returns True, we update
          the timestamp (so the next call within 50ms re-scans fresh)
          but the cache check explicitly skips when the last result
          was True.

        50ms detection latency for non-modifier keys is acceptable:
        typists press keys ≥50ms apart, and the polling loop's 1ms
        cadence still gives ~1ms modifier press/release latency (the
        scan throttle only affects combo detection, not the hotkey
        fire itself).
        """
        now = time.monotonic()
        # Only consult the cache when the last result was False. A
        # cached True would leak into the next press cycle (see the
        # docstring) — when the last result was True, always re-scan.
        if (
            not self._last_nonmod_pressed
            and now - self._last_nonmod_check_time < 0.005
        ):
            return False
        result = self._any_non_modifier_key_pressed(modifier_vks)
        self._last_nonmod_pressed = result
        self._last_nonmod_check_time = now
        return result

    def _other_modifiers_pressed(self) -> bool:
        """Return True if any modifier OTHER than the configured one is held.

        FIX-HOTKEY-ARCHITECTURE: used by the modifier-only polling loop
        to ensure the user is pressing ONLY the configured modifier (e.g.
        just Alt, not Alt+Ctrl). If another modifier is held, the press
        callback is suppressed — the user's intent is probably a
        multi-key combo, not the bare modifier.
        """
        if not self._user32:
            return False
        # Iterate over all modifier VKs, skipping any that correspond to
        # the configured modifier. _MOD_WIN maps to two VKs (LWin+RWin);
        # both are skipped when Win is the configured modifier.
        all_mods = [
            (_VK_CONTROL, _MOD_CONTROL),
            (_VK_SHIFT, _MOD_SHIFT),
            (_VK_MENU, _MOD_ALT),
            (_VK_LWIN, _MOD_WIN),
            (_VK_RWIN, _MOD_WIN),
        ]
        for vk, mod_flag in all_mods:
            if mod_flag & self._modifiers:
                continue  # This VK belongs to the configured modifier
            if self._key_pressed(vk):
                return True
        # Also detect AltGr (Right Alt + Ctrl simulated by Windows).
        # If AltGr is pressed and our configured modifier is NOT Alt,
        # treat it as "another modifier held" — it's a real key press
        # that the user likely didn't intend as the hotkey.
        if not (self._modifiers & _MOD_ALT) and self._is_altgr_pressed():
            return True
        return False

    def _suppress_caps_lock_toggle(self) -> None:
        """Undo the OS-level caps-lock toggle when the hotkey is Caps Lock.

        FIX-HOTKEY-ARCHITECTURE: Windows toggles the caps-lock state as
        part of processing the VK_CAPITAL keyDown, before the foreground
        app sees it. The native ``windows-key-listener.exe`` binary
        suppresses this via its ``WH_KEYBOARD_LL`` hook (see
        ``should_suppress_keydown`` in
        ``voice_typer/server/native/windows-key-listener.c``). The
        legacy polling backend can't install a low-level hook from
        Python without significant complexity, so we use a different
        approach: read the current toggle state via ``GetKeyState`` and,
        if the key is now toggled ON, send a synthetic Caps Lock
        keypress via ``keybd_event`` to toggle it back OFF.

        The ``_caps_lock_suppressing`` flag is set while the synthetic
        keypress is in flight so the polling loop skips processing —
        otherwise the synthetic events would re-trigger the callback
        or prematurely fire on_release.
        """
        if not self._user32 or not self._kernel32:
            return
        try:
            self._caps_lock_suppressing = True
            try:
                # GetKeyState returns a short where bit 0 (0x1) is the
                # toggle state. If 1, Caps Lock was just toggled ON by
                # the physical press — undo it with a synthetic press.

                toggle_state = self._user32.GetKeyState(_VK_CAPITAL) & 0x1
                if toggle_state:
                    # Synthetic keydown + keyup toggles the state back.

                    self._user32.keybd_event(_VK_CAPITAL, 0x45, 0, 0)
                    self._user32.keybd_event(
                        _VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP, 0
                    )
                    log.debug(
                        "[HOTKEY] Suppressed Caps Lock toggle (toggled back off)"
                    )
            finally:
                # Brief sleep to let the OS process the synthetic events
                # before clearing the flag. Without this, the next
                # iteration of the polling loop might see the synthetic
                # keyup and prematurely fire on_release. 5ms is enough
                # for the OS to dispatch the events but short enough
                # that the user doesn't notice a delay.

                self._kernel32.Sleep(5)
                self._caps_lock_suppressing = False
        except Exception:
            log.exception("[HOTKEY] Failed to suppress Caps Lock toggle")
            self._caps_lock_suppressing = False

    def _ensure_caps_lock_off(self) -> None:
        """Proactively ensure Caps Lock is OFF (not toggled).

        CAPS-LOCK-FIX: unlike _suppress_caps_lock_toggle() which reacts to a
        key press event, this method proactively checks the current caps lock
        state and toggles it OFF if it is ON. It is called:
        - At registration time (when the hotkey starts)
        - Periodically every ~200ms while the polling loop runs

        This is defense-in-depth against the caps lock toggle race where the
        OS toggles caps ON before the reactive suppression can undo it.
        The _caps_lock_suppressing flag is NOT set here because this method
        is called outside of a key-press event context (no risk of feedback
        loop with the polling loop).
        """
        if not self._user32:
            return
        try:
            toggle_state = self._user32.GetKeyState(_VK_CAPITAL) & 0x1
            if toggle_state:
                self._user32.keybd_event(_VK_CAPITAL, 0x45, 0, 0)
                self._user32.keybd_event(
                    _VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP, 0
                )
                log.info(
                    "[HOTKEY] Proactive caps lock toggle-off (was ON, forced OFF)"
                )
        except Exception:
            log.exception("[HOTKEY] Failed to force caps lock off")

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
        log.debug("[HOTKEY] Stopping %s listener", self.hotkey_str)
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
        # FIX-HOTKEY-ARCHITECTURE: handle modifier-only hotkeys where
        # ``self._vk`` is None (e.g. <alt>, <ctrl>). The previous format
        # string would crash with ``TypeError`` on ``None:X``.
        if self._vk is not None:
            vk_str = f"0x{self._vk:X} ({self._vk})"
        else:
            vk_str = "(modifier-only, no main VK)"
        return (
            "WindowsNativeHotkey\n"
            f"Hotkey: {self.hotkey_str}\n"
            f"VK: {vk_str}\n"
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

    FIX-HOTKEY-ARCHITECTURE: when the native binary is NOT built (e.g.
    running from a source checkout without invoking
    ``scripts/build/compile_native.{sh,ps1}``, or on a platform where
    the binary isn't bundled), the factory falls back to the legacy
    backends. On Windows this means ``WindowsNativeHotkey`` uses
    ``GetAsyncKeyState`` polling at 1kHz. This is expected behavior —
    NOT a bug. The polling backend now also supports modifier-only
    hotkeys (``<alt>``, ``<ctrl>``, ``<shift>``, ``<win>``) via
    ``_run_modifier_only_polling_loop``, and suppresses the Caps Lock
    toggle for ``<caps_lock>`` via ``_suppress_caps_lock_toggle``.
    Users who want the full feature set (lower CPU, sub-ms latency,
    native key suppression) should build the native binary.
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
        # FIX-HOTKEY-ARCHITECTURE: this is the polling fallback. It's
        # expected when the native windows-key-listener.exe binary isn't
        # built. See the class docstring for the feature differences.
        log.info(
            "[HOTKEY] Platform is win32 -> using WindowsNativeHotkey (legacy "
            "polling, native binary not built or unavailable)"
        )
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

    GAP-4 (runtime fallback chain): when the native backend permanently
    fails (5 retries exhausted), the adapter transparently swaps to a
    legacy backend (``PynputHotkey`` / ``WindowsNativeHotkey`` /
    ``WaylandHotkey``) with the same callbacks. A 5-minute retry timer
    periodically attempts to swap back to the native backend; on
    success, the adapter swaps back and notifies the user.

    GAP-2 (macOS Accessibility onboarding): when the native backend
    emits an ``ERROR:`` line classified as a permission issue, the
    adapter shows a tray notification and (on macOS) opens System
    Settings → Accessibility. A 60-second permission retry timer
    polls for the permission being granted and, on success, restarts
    the native backend.

    State machine (5 states, 3 callback slots, 2 async timers):

        States: NATIVE, FALLING_BACK, FALLBACK, FAILED, STOPPED
        Callback slots (set on the native backend in __init__):
            - native._on_error_callback            -> _on_native_error
            - native._on_permanent_failure_callback-> _on_native_permanent_failure
            - _on_release_callback                 -> set via set_on_release
        Async timers:
            - 300s native-retry timer  (_native_retry_timer, this class)
            - 60s  permission-retry    (voice_typer.server.permissions,
                                         max 5 attempts)

        Quick diagram (omits self-loops, FAILED->STOPPED, and the
        permission-grant recovery path; see the full table below):

            NATIVE → FALLING_BACK → FALLBACK → (NATIVE on recovery, or FAILED)
            Any state → STOPPED on stop()

    State Transition Table:
    ┌──────────────┬──────────────────────────────────────┬──────────────────┬───────────────────────────────────────┐
    │ From         │ Event                                │ To               │ Side Effects                          │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ (init)       │ __init__                             │ NATIVE           │ Wire _on_error_callback &             │
    │              │                                      │                  │ _on_permanent_failure_callback        │
    │              │                                      │                  │ on native.                            │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ NATIVE       │ start() succeeds                     │ NATIVE           │ (self-loop; confirms state            │
    │              │                                      │                  │ under swap_lock)                      │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ NATIVE       │ start() raises OR                    │ FALLING_BACK     │ Via _swap_to_legacy(): fire           │
    │              │ _on_native_permanent_failure         │                  │ _on_release_callback, create &        │
    │              │ (native's 5 retries exhausted)       │                  │ start legacy backend.                 │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ FALLING_BACK │ legacy backend starts                │ FALLBACK         │ Assign _legacy, show fallback         │
    │              │ successfully                         │                  │ notification, schedule 300s           │
    │              │                                      │                  │ native retry timer.                   │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ FALLING_BACK │ legacy create/start raises           │ FAILED           │ Log error, show failure               │
    │              │                                      │                  │ notification.                         │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ FALLING_BACK │ stop() called during swap            │ STOPPED          │ Stop the just-created legacy          │
    │              │                                      │                  │ backend, return.                      │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ FALLBACK     │ 300s native retry timer fires,       │ NATIVE           │ Stop legacy, stop+start native,       │
    │              │ native restart succeeds              │                  │ set _on_release, show recovery        │
    │              │                                      │                  │ notification, reset perm flag.        │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ FALLBACK     │ 300s timer fires, native fails,      │ FALLBACK         │ (self-loop) Stop old legacy,          │
    │              │ legacy restarts                      │                  │ restart native (fails), new           │
    │              │                                      │                  │ legacy, schedule 300s retry.          │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ FALLBACK     │ 300s timer fires, both native        │ FAILED           │ Log "both backends failed",           │
    │              │ & legacy restart fail                │                  │ show failure notification.            │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ NATIVE,      │ 60s permission retry timer           │ NATIVE           │ Stop+start native, set                │
    │ FALLBACK, or │ fires, native restart succeeds       │                  │ _on_release, reset perm flag.         │
    │ FAILED       │                                      │                  │ NOTE: legacy NOT stopped when         │
    │              │                                      │                  │ transitioning from FALLBACK.          │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ NATIVE,      │ stop() called                        │ STOPPED          │ Cancel 300s timer & 60s perm          │
    │ FALLING_BACK,│                                      │                  │ retry, reset flag, stop legacy        │
    │ FALLBACK, or │                                      │                  │ & native. Idempotent no-op if         │
    │ FAILED       │                                      │                  │ already STOPPED.                      │
    ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
    │ STOPPED      │ (none - terminal state)              │ STOPPED          │ No transitions out; stop() is         │
    │              │                                      │                  │ a no-op.                              │
    └──────────────┴──────────────────────────────────────┴──────────────────┴───────────────────────────────────────┘

    Key: STOPPED is terminal. FAILED is terminal except for stop() and the
    60s permission-grant recovery path (which restarts native directly).
    FALLING_BACK is a transient state held only while _swap_to_legacy() is
    between acquiring the swap_lock to set the state and re-acquiring it to
    install the legacy backend.
    """

    # State constants
    _STATE_NATIVE = "NATIVE"
    _STATE_FALLING_BACK = "FALLING_BACK"
    _STATE_FALLBACK = "FALLBACK"
    _STATE_FAILED = "FAILED"
    _STATE_STOPPED = "STOPPED"

    # GAP-4: retry interval for swapping back to native (5 minutes)
    _NATIVE_RETRY_INTERVAL_SECONDS = 300.0

    def __init__(self, native_backend):
        # Don't call super().__init__ because we delegate hotkey_str
        # to the wrapped backend.
        self._native = native_backend
        self.hotkey_str = native_backend.hotkey_str
        self._on_release_callback: Optional[Callable[[], None]] = None
        self._callback: Optional[Callable[[], None]] = None
        self._legacy: Optional[HotkeyBackend] = None
        self._state = self._STATE_NATIVE
        self._swap_lock = threading.Lock()
        self._native_retry_timer: Optional[threading.Timer] = None
        self._permission_notification_shown = False
        # Wire up the native backend's error and permanent-failure
        # callbacks so we know when to (a) show a permission prompt
        # and (b) swap to the legacy backend.
        native_backend._on_error_callback = self._on_native_error  # type: ignore[assignment]
        native_backend._on_permanent_failure_callback = (  # type: ignore[assignment]
            self._on_native_permanent_failure
        )

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        try:
            self._native.start(callback)
            with self._swap_lock:
                if self._state != self._STATE_STOPPED:
                    self._state = self._STATE_NATIVE
        except Exception as exc:
            log.warning(
                "[HOTKEY] Native backend failed to start: %s — trying legacy", exc
            )
            self._swap_to_legacy()

    def set_on_release(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_release_callback = callback
        self._native.set_on_release(callback)
        if self._legacy is not None:
            self._legacy.set_on_release(callback)

    def stop(self) -> None:
        with self._swap_lock:
            if self._state == self._STATE_STOPPED:
                return
            self._state = self._STATE_STOPPED
            # Cancel the native retry timer
            if self._native_retry_timer is not None:
                self._native_retry_timer.cancel()
                self._native_retry_timer = None
            # Cancel any pending permission retry
            try:
                from voice_typer.server.permissions import cancel_permission_retry
                cancel_permission_retry()
            except Exception:
                pass
            # Reset the permission notification flag so a restart
            # can show it again.
            self._permission_notification_shown = False
            # Stop both backends — the inactive one is a no-op.
            # Stop the legacy first (it's the active one if swapping),
            # then the native.
            if self._legacy is not None:
                try:
                    self._legacy.stop()
                except Exception:
                    log.debug("[HOTKEY] Failed to stop legacy backend", exc_info=True)
                self._legacy = None
            try:
                self._native.stop()
            except Exception:
                log.debug("[HOTKEY] Failed to stop native backend", exc_info=True)

    def is_alive(self) -> bool:
        with self._swap_lock:
            state = self._state
        if state == self._STATE_NATIVE:
            return self._native.is_alive()
        if state == self._STATE_FALLBACK:
            return self._legacy is not None and self._legacy.is_alive()
        return False  # FAILED or STOPPED

    def diagnose(self) -> str:
        with self._swap_lock:
            state = self._state
        active = (
            "native" if state == self._STATE_NATIVE
            else "legacy" if state == self._STATE_FALLBACK
            else "none"
        )
        native_diag = self._native.diagnose()
        legacy_diag = self._legacy.diagnose() if self._legacy else "not started"
        return (
            f"_NativeBackendAdapter (state={state}, active={active})\n"
            f"Native backend:\n{native_diag}\n"
            f"Legacy backend:\n{legacy_diag}"
        )

    # ── GAP-2: permission error handling ────────────────────────────────

    def _on_native_error(self, error_message: str) -> None:
        """Called by the native backend when it emits an ERROR: line.

        If the error is a permission issue (Accessibility on macOS,
        /dev/input on Linux), show a tray notification and open the OS
        permission UI. Other errors are handled by the startup fallback
        chain — no notification needed.
        """
        try:
            from voice_typer.server.permissions import (
                permission_error_is_permission_denied,
                request_keyboard_permission,
                show_permission_notification,
            )
        except ImportError:
            log.debug("[HOTKEY] permissions module not available")
            return

        if not permission_error_is_permission_denied(error_message):
            return

        # Show the notification at most once per session
        if self._permission_notification_shown:
            return
        self._permission_notification_shown = True

        # Get the tray from the app (best-effort — the adapter may be
        # used in tests without an app)
        tray = self._get_tray()
        show_permission_notification(tray, error_message)

        # Open the OS permission UI (macOS System Settings / Linux pkexec)
        # and schedule a retry timer that restarts the native backend
        # once permission is granted.
        request_keyboard_permission(on_granted=self._on_permission_granted)

    def _on_permission_granted(self) -> None:
        """Called when the permission retry timer detects the permission
        has been granted. Attempts to restart the native backend.
        """
        log.info("[HOTKEY] Permission granted — restarting native backend")
        try:
            self._native.stop()
        except Exception:
            pass
        try:
            self._native.start(self._callback)  # type: ignore[arg-type]
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state != self._STATE_STOPPED:
                        self._state = self._STATE_NATIVE
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info("[HOTKEY] Native backend restarted after permission grant")
                self._permission_notification_shown = False
                return
        except Exception:
            log.exception("[HOTKEY] Native restart after permission grant failed")

    def _get_tray(self):
        """Best-effort: get the app's tray object for notifications.

        The adapter doesn't hold a reference to the app (to avoid a
        circular import). We look it up via the HotkeyDispatcher's
        ``_app`` attribute if the adapter was created by one. Returns
        None if no tray is available (e.g. in tests).
        """
        # The HotkeyDispatcher stores itself on the adapter? No — but
        # the adapter is stored on the dispatcher. We can't easily go
        # back up. For now, return None — the notification is still
        # logged, and the HotkeyDispatcher can override this by setting
        # ``adapter._tray = app.tray`` after construction.
        return getattr(self, "_tray", None)

    # ── GAP-4: runtime fallback chain ───────────────────────────────────

    def _on_native_permanent_failure(self) -> None:
        """Called when the native backend exhausts its 5 retries."""
        log.warning(
            "[HOTKEY] Native backend permanently failed — swapping to legacy"
        )
        self._swap_to_legacy()

    def _swap_to_legacy(self) -> None:
        """Replace the native backend with a legacy one.

        Idempotent: if we've already swapped or stopped, do nothing.
        If the legacy backend also fails, set state to FAILED and show
        a tray notification.
        """
        with self._swap_lock:
            if self._state in (self._STATE_FALLBACK, self._STATE_FAILED,
                               self._STATE_STOPPED):
                return  # Already swapped, given up, or stopped
            self._state = self._STATE_FALLING_BACK

        # If a recording is in progress (push-to-talk), fire the release
        # callback so it doesn't get stuck. The native backend (which
        # detected the press) is dead and can't detect the release.
        if self._on_release_callback is not None:
            try:
                self._on_release_callback()
            except Exception:
                log.exception("[HOTKEY] on_release during swap raised")

        try:
            legacy = self._create_legacy_backend()
            legacy.start(self._callback)  # type: ignore[arg-type]
            if self._on_release_callback is not None:
                legacy.set_on_release(self._on_release_callback)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    # stop() was called during the swap — clean up
                    try:
                        legacy.stop()
                    except Exception:
                        pass
                    return
                self._legacy = legacy
                self._state = self._STATE_FALLBACK
            log.info("[HOTKEY] Successfully swapped to legacy backend")
            self._show_fallback_notification()
            # Schedule a periodic retry of the native backend
            self._schedule_native_retry()
        except Exception as exc:
            log.error("[HOTKEY] Legacy backend also failed: %s — giving up", exc)
            with self._swap_lock:
                self._state = self._STATE_FAILED
            self._show_failure_notification(exc)

    def _create_legacy_backend(self) -> HotkeyBackend:
        """Instantiate the appropriate legacy backend for this platform.

        Mirrors the fallback logic in ``create_hotkey_backend()`` for
        when the native binary is missing.
        """
        if is_windows():
            return WindowsNativeHotkey(self.hotkey_str)
        if is_linux():
            wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
            xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
            if wayland_display or xdg_session == "wayland":
                return WaylandHotkey(self.hotkey_str)
        return PynputHotkey(self.hotkey_str)

    def _schedule_native_retry(self) -> None:
        """Schedule a periodic attempt to swap back to the native backend."""
        with self._swap_lock:
            if self._state != self._STATE_FALLBACK:
                return  # Already recovered, failed, or stopped

        # Cancel any existing timer
        if self._native_retry_timer is not None:
            self._native_retry_timer.cancel()

        timer = threading.Timer(
            self._NATIVE_RETRY_INTERVAL_SECONDS,
            self._retry_native,
        )
        timer.daemon = True
        timer.start()
        self._native_retry_timer = timer

    def _retry_native(self) -> None:
        """Attempt to swap back to the native backend.

        If the native backend restarts successfully, swap back and notify
        the user. If it fails, stay on legacy and schedule another retry.
        """
        with self._swap_lock:
            if self._state != self._STATE_FALLBACK:
                return  # Already recovered, failed, or stopped

        log.info("[HOTKEY] Retrying native backend...")
        try:
            # Stop the legacy backend first to free up any registered
            # hotkeys (e.g. RegisterHotKey on Windows).
            if self._legacy is not None:
                try:
                    self._legacy.stop()
                except Exception:
                    pass
                self._legacy = None
            self._native.stop()
            self._native.start(self._callback)  # type: ignore[arg-type]
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state == self._STATE_STOPPED:
                        # stop() was called during retry — clean up
                        self._native.stop()
                        return
                    self._state = self._STATE_NATIVE
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info(
                    "[HOTKEY] Native backend recovered — swapped back from legacy"
                )
                self._show_recovery_notification()
                self._permission_notification_shown = False
                return
        except Exception as exc:
            log.warning(
                "[HOTKEY] Native retry failed: %s — staying on legacy", exc
            )

        # Retry failed — restart the legacy backend and schedule another retry
        try:
            self._legacy = self._create_legacy_backend()
            self._legacy.start(self._callback)  # type: ignore[arg-type]
            if self._on_release_callback is not None:
                self._legacy.set_on_release(self._on_release_callback)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    self._legacy.stop()
                    return
                self._state = self._STATE_FALLBACK
            self._schedule_native_retry()
        except Exception:
            with self._swap_lock:
                self._state = self._STATE_FAILED
            log.error(
                "[HOTKEY] Both native and legacy backends failed — hotkey dead"
            )
            self._show_failure_notification(None)

    # ── Notifications ───────────────────────────────────────────────────

    def _show_fallback_notification(self) -> None:
        """Notify the user that the hotkey is running in compatibility mode."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    f"{APP_NAME}: Compatibility mode",
                    "Hotkey is running in compatibility mode (reduced features). "
                    "Restart the app for full functionality.",
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for fallback notification")

    def _show_recovery_notification(self) -> None:
        """Notify the user that the native backend has recovered."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    f"{APP_NAME}: Full mode restored",
                    "Hotkey is running in full mode.",
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for recovery notification")

    def _show_failure_notification(self, exc: Optional[Exception]) -> None:
        """Notify the user that the hotkey is not working at all."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    f"{APP_NAME}: Hotkey error",
                    "Hotkey is not working. Click to troubleshoot.",
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for failure notification")


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
        # TASK-10: typed as Any — socket is created lazily inside start()
        # and remains None if the socket bind fails. _accept_loop checks
        # self._alive before touching this socket, but pyrefly cannot
        # prove the narrowing across the thread boundary.
        self._server_socket: Any = None
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
        # TASK-10: _callback may be None if start() was never called with
        # one — guard before forwarding to PynputHotkey.start(), which
        # has a non-Optional callback contract.
        if self._callback is None:
            log.warning("[HOTKEY-WAYLAND] Cannot start pynput fallback — no callback registered")
            return
        try:
            self._pynput_fallback = PynputHotkey(self._hotkey_str)
            self._pynput_fallback.start(self._callback)
            log.info("[HOTKEY-WAYLAND] Pynput fallback started (direct)")
        except Exception as exc:
            log.warning("[HOTKEY-WAYLAND] Pynput fallback also failed: %s", exc)

    def _start_pynput_fallback_with_timeout(self) -> None:
        """Start pynput with a timeout — kill it if it doesn't respond."""
        # TASK-10: same callback guard as _start_pynput_fallback.
        if self._callback is None:
            log.warning("[HOTKEY-WAYLAND] Cannot start pynput fallback — no callback registered")
            return
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

