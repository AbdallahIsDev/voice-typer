"""Win32 virtual-key code map and hotkey-string parsers.

Holds the ``_VK_MAP`` lookup table, the ``_MOD_*`` RegisterHotKey bit
constants, and the :func:`parse_hotkey_to_win32` /
:func:`parse_hotkey_to_vk` parsers.  Split out from the original
``hotkeys.py`` god-file in Phase 4.5 (ARCH-045).
"""

import threading

from voice_typer.server import hotkeys as _hotkeys_pkg

from .base import log


# See pynput_backend.py for the rationale: tests patch
# ``voice_typer.server.hotkeys.is_windows`` and expect the patch to
# affect calls made from this submodule.
def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


# ─── Windows native backend ──────────────────────────────────────────────────

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
_VK_MENU = 0x12  # VK_MENU covers both LAlt (0xA4) and RAlt (0xA5)
_VK_LWIN = 0x5B
_VK_RWIN = 0x5C
_VK_CAPITAL = 0x14  # Caps Lock — also in _VK_MAP["caps_lock"]
_VK_RMENU = 0xA5  # Right Alt / AltGr
_KEYEVENTF_KEYUP = 0x0002

# Common virtual-key code mappings for function keys and printable keys.
#
# ISSUE-3 (key-name maps): this table maps pynput-style lowercase names
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


def _win32_vk(vk_name: str) -> int | None:
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
        # ARCH-041: extend with numpad, media, browser, and special keys.
        # Without these, PTT bindings to e.g. Media_Next silently fail.
        # Numpad 0-9 (VK_NUMPAD0 = 0x60 .. VK_NUMPAD9 = 0x69)
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
        # On non-US keyboards, AltGr is used for characters like @, €, #.
        # VK_RMENU = 0xA5 is the right Alt key, which is the physical
        # AltGr key on most keyboards.
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
    _canonical_to_modbit = {
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
        bit = _canonical_to_modbit.get(mod, 0)
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
                extra,
                hotkey_str,
                key_name,
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
                user32.GetKeyboardLayout(0)
                # VkKeyScanW returns the VK code and shift state
                vk_scan = user32.VkKeyScanW(ord(key_name))
                if vk_scan != -1:
                    vk = vk_scan & 0xFF
            except Exception:
                pass
        if vk is None:
            return None
    return vk, modifiers
