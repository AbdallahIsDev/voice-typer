"""Win32 virtual-key code map and hotkey-string parsers."""

import threading

from voice_typer.server import hotkeys as _hotkeys_pkg

from .base import log


# See pynput_backend.py for the rationale: tests patch
def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


# Win32 constants
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_KEYUP = 0x0101
_WM_SYSKEYUP = 0x0105
_WHC_KEYBOARD_LL = 13
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
# PLAT-ALTGR: AltGr modifier flag. Windows simulates AltGr as Ctrl+Alt,
_MOD_ALTGR = 0x0010
_MOD_NOREPEAT = 0x4000
_GWLP_USERDATA = -21

# VK codes for the modifier keys themselves,
_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_MENU = 0x12  # VK_MENU covers both LAlt (0xA4) and RAlt (0xA5)
_VK_LWIN = 0x5B
_VK_RWIN = 0x5C
_VK_CAPITAL = 0x14  # Caps Lock, also in _VK_MAP["caps_lock"]
_VK_RMENU = 0xA5  # Right Alt / AltGr
_KEYEVENTF_KEYUP = 0x0002

# Common virtual-key code mappings for function keys and printable keys.
_VK_MAP = {}
# guard _VK_MAP init so two threads racing on the first call
_VK_MAP_LOCK = threading.Lock()


def _win32_vk(vk_name: str) -> int | None:
    """Look up a VK code by name, initializing the map lazily."""
    _init_vk_map()
    return _VK_MAP.get(vk_name)


def _init_vk_map():
    """Populate _VK_MAP lazily to avoid issues at import on non-Windows."""
    if _VK_MAP:
        return
    with _VK_MAP_LOCK:
        # Double-checked locking: another thread may have populated
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
        _VK_MAP["esc"] = 0x1B  # VK_ESCAPE
        _VK_MAP["escape"] = 0x1B
        _VK_MAP["space"] = 0x20  # VK_SPACE
        _VK_MAP["enter"] = 0x0D  # VK_RETURN
        _VK_MAP["return"] = 0x0D
        _VK_MAP["tab"] = 0x09  # VK_TAB
        _VK_MAP["backspace"] = 0x08  # VK_BACK
        _VK_MAP["del"] = 0x2E  # VK_DELETE
        _VK_MAP["delete"] = 0x2E
        _VK_MAP["insert"] = 0x2D  # VK_INSERT
        _VK_MAP["home"] = 0x24  # VK_HOME
        _VK_MAP["end"] = 0x23  # VK_END
        _VK_MAP["pageup"] = 0x21  # VK_PRIOR
        _VK_MAP["pagedown"] = 0x22  # VK_NEXT
        _VK_MAP["up"] = 0x26  # VK_UP
        _VK_MAP["down"] = 0x28  # VK_DOWN
        _VK_MAP["left"] = 0x25  # VK_LEFT
        _VK_MAP["right"] = 0x27  # VK_RIGHT
        # extend with numpad, media, browser, and special keys.
        for i in range(10):
            _VK_MAP[f"num_{i}"] = 0x60 + i
            _VK_MAP[f"numpad_{i}"] = 0x60 + i
        _VK_MAP["num_decimal"] = 0x6E  # VK_DECIMAL
        _VK_MAP["num_enter"] = 0x6C  # VK_RETURN (numpad)
        _VK_MAP["num_add"] = 0x6B  # VK_ADD
        _VK_MAP["num_subtract"] = 0x6D  # VK_SUBTRACT
        _VK_MAP["num_multiply"] = 0x6A  # VK_MULTIPLY
        _VK_MAP["num_divide"] = 0x6F  # VK_DIVIDE
        # Media keys
        _VK_MAP["media_next"] = 0xB0  # VK_MEDIA_NEXT_TRACK
        _VK_MAP["media_prev"] = 0xB1  # VK_MEDIA_PREV_TRACK
        _VK_MAP["media_play_pause"] = 0xB3  # VK_MEDIA_PLAY_PAUSE
        _VK_MAP["media_stop"] = 0xB2  # VK_MEDIA_STOP
        # Browser keys
        _VK_MAP["browser_back"] = 0xA6
        _VK_MAP["browser_forward"] = 0xA7
        _VK_MAP["browser_refresh"] = 0xA8
        _VK_MAP["browser_home"] = 0xAC
        # Special keys
        _VK_MAP["capslock"] = 0x14  # VK_CAPITAL
        _VK_MAP["caps_lock"] = 0x14
        _VK_MAP["numlock"] = 0x90  # VK_NUMLOCK
        _VK_MAP["num_lock"] = 0x90
        _VK_MAP["scrolllock"] = 0x91  # VK_SCROLL
        _VK_MAP["scroll_lock"] = 0x91
        _VK_MAP["printscreen"] = 0x2C  # VK_SNAPSHOT
        _VK_MAP["print_screen"] = 0x2C
        _VK_MAP["pause"] = 0x13  # VK_PAUSE
        # PLAT-ALTGR: Right Alt (AltGr) virtual-key code.
        _VK_MAP["altgr"] = 0xA5  # VK_RMENU (Right Alt / AltGr)
        _VK_MAP["right_alt"] = 0xA5  # VK_RMENU
        _VK_MAP["ralt"] = 0xA5  # VK_RMENU


def parse_hotkey_to_vk(hotkey_str: str) -> int | None:
    """Convert a hotkey string like '<f2>' to a Win32 virtual-key code.

    Returns None if the key cannot be parsed.
    """
    parsed = parse_hotkey_to_win32(hotkey_str)
    if parsed is None:
        return None
    return parsed[0]


def parse_hotkey_to_win32(hotkey_str: str) -> tuple[int | None, int] | None:
    """Convert a hotkey string to ``(virtual_key, RegisterHotKey modifiers)``."""
    from voice_typer.server.hotkey_spec import parse_hotkey

    _init_vk_map()
    parsed = parse_hotkey(hotkey_str)

    # Map canonical modifier names to Win32 RegisterHotKey modifier bits.
    _canonical_to_modbit = {
        "ctrl": _MOD_CONTROL,
        "shift": _MOD_SHIFT,
        "alt": _MOD_ALT,
        "alt_gr": _MOD_ALTGR,
        "cmd": _MOD_WIN,
        "win": _MOD_WIN,
        "super": _MOD_WIN,
        # 'fn' has no Win32 RegisterHotKey equivalent (firmware-only).
    }

    modifiers = 0
    for mod in parsed.modifiers:
        bit = _canonical_to_modbit.get(mod, 0)
        modifiers |= bit

    key_name = parsed.main_key
    # first non-modifier key wins. Subsequent non-modifier
    if len(parsed.keys) > 1:
        for extra in parsed.keys[1:]:
            log.warning(
                "[HOTKEY] Ignoring extra key %r in hotkey %r (already have %r)",
                extra,
                hotkey_str,
                key_name,
            )

    if key_name is None:
        # modifier-only spec (e.g. <alt>,
        if modifiers:
            return (None, modifiers)
        return None

    vk = _VK_MAP.get(key_name)
    if vk is None:
        # PLAT-VKMAP: try MapVirtualKey with the current keyboard layout
        if is_windows() and len(key_name) == 1 and key_name.isalpha():
            try:
                import ctypes

                user32 = ctypes.windll.user32
                # Get the current keyboard layout
                user32.GetKeyboardLayout(0)
                # VkKeyScanW returns the VK code and shift state
                vk_scan = user32.VkKeyScanW(ord(key_name))
                if vk_scan != -1:
                    vk = vk_scan & 0xFF
            except (OSError, AttributeError, ValueError):
                # ``user32`` is None on non-Windows hosts (AttributeError);
                pass
        if vk is None:
            return None
    return vk, modifiers
