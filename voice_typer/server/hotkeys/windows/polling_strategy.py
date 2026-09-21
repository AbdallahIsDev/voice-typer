"""Windows hotkey polling strategy backend."""

from __future__ import annotations

import contextlib
import ctypes
import time

from ..base import log
from ..win32_vk import (
    _MOD_ALT,
    _MOD_CONTROL,
    _MOD_SHIFT,
    _MOD_WIN,
    _VK_CAPITAL,
    _VK_CONTROL,
    _VK_LWIN,
    _VK_MENU,
    _VK_RWIN,
    _VK_SHIFT,
)

# Key-state helpers (stateless, operate on backend._user32)


def key_pressed(self, vk: int) -> bool:
    """Return True if the given VK code is currently held down."""
    # defensive guard, sibling methods (``_other_modifiers_pressed``,
    if not self._user32:
        return False
    return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)


def is_altgr_pressed(self) -> bool:
    """Detect if AltGr is currently pressed."""
    if not self._user32:
        return False
    try:
        right_alt = bool(self._user32.GetAsyncKeyState(0xA5) & 0x8000)
        ctrl = bool(self._user32.GetAsyncKeyState(0x11) & 0x8000)
        return right_alt and ctrl
    except Exception:
        return False


def modifiers_pressed(self) -> bool:
    """Return True if ALL configured modifiers are currently held."""
    # defensive guard, sibling methods (``_other_modifiers_pressed``
    if not self._user32:
        return False
    # PLAT-ALTGR: Detect AltGr (Right Alt + Ctrl simulated by Windows).
    if self._is_altgr_pressed():
        return False

    if self._modifiers & _MOD_CONTROL and not self._key_pressed(0x11):
        return False
    if self._modifiers & _MOD_SHIFT and not self._key_pressed(0x10):
        return False
    if self._modifiers & _MOD_ALT and not self._key_pressed(0x12):
        return False
    return not (self._modifiers & _MOD_WIN and not (self._key_pressed(91) or self._key_pressed(92)))


def other_modifiers_pressed(self) -> bool:
    """Return True if any modifier OTHER than the configured one is held."""
    if not self._user32:
        return False
    # Iterate over all modifier VKs, skipping any that correspond to
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
    return bool(not self._modifiers & _MOD_ALT and self._is_altgr_pressed())


def any_non_modifier_key_pressed(self, modifier_vks: frozenset[int]) -> bool:
    """Return True if any non-modifier key is currently held down.

    scans the Win32 virtual-key code
    space (0x08-0xFF) for any key that is currently held down,
    excluding the modifier VKs passed in ``modifier_vks``. Used by
    the modifier-only polling loop to detect when the user has
    pressed a non-modifier key (e.g. ``C``) while holding the
    configured modifier (e.g. ``Alt``), that pattern indicates
    the user was doing a combo like Alt+C, not invoking the bare
    modifier hotkey, so the fire is suppressed on release.

    The scan covers the full VK range:
    - 0x08 (VK_BACK) through 0xFF (VK_OEM_CLEAR)
    - Excludes 0x10/0x11/0x12 (Shift/Ctrl/Menu) and 0x14 (Caps Lock)
    - Excludes 0x5B/0x5C (LWin/RWin)
    - Excludes 0xA0-0xA5 (LShift/RShift/LCtrl/RCtrl/LAlt/RAlt)

    Returns False on non-Windows or if no non-modifier key is held.

    PERF- this scan is O(248) per iteration (one
    ``GetAsyncKeyState`` per VK code). The modifier-only polling
    loop runs at 8ms cadence (~125 Hz), so calling this every
    iteration while the modifier is held would be up to ~31k
    syscalls/sec. The loop wraps this call in
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
    for vk in range(0x08, 0x100):
        if vk in modifier_vks:
            continue
        try:
            if self._user32.GetAsyncKeyState(vk) & 0x8000:
                return True
        except Exception:
            # If GetAsyncKeyState fails (e.g. on a non-Windows
            return False
    return False


def any_non_modifier_key_pressed_throttled(self, modifier_vks: frozenset[int]) -> bool:
    """PERF- throttled wrapper around ``_any_non_modifier_key_pressed()``.

    The underlying scan is O(248) per call (see the docstring on
    ``_any_non_modifier_key_pressed`` for the rationale). The
    modifier-only polling loop runs at 8ms cadence (~125 Hz), so
    calling it every iteration while the modifier is held would
    be up to ~31k syscalls/sec. This wrapper re-scans at most
    every 50ms (20 Hz), reducing the syscall rate to ~5k/sec.

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
    typists press keys ≥50ms apart, and the polling loop's 8ms
    cadence (~125 Hz) still gives ~8ms modifier press/release
    latency (the scan throttle only affects combo detection, not
    the hotkey fire itself).
    """
    now = time.monotonic()
    # Only consult the cache when the last result was False. A
    if not self._last_nonmod_pressed and now - self._last_nonmod_check_time < 0.05:
        return False
    result = self._any_non_modifier_key_pressed(modifier_vks)
    self._last_nonmod_pressed = result
    self._last_nonmod_check_time = now
    return result


# Polling loops


def run_polling_loop(self, callback):
    """GetAsyncKeyState polling fallback for hotkey detection.

    PERF-012 / PERF-003 / PERF-01: on Windows this uses
    GetAsyncKeyState in a tight loop with an 8ms sleep. The Windows
    timer resolution is bumped to 8ms via ``timeBeginPeriod(8)``
    before the loop so ``Sleep(8)`` actually sleeps ~8ms instead of
    the default ~15.6ms. This is still technically polling but at a
    much lower cost than the previous 100ms (10Hz) approach, the key
    is checked every 8ms (~125 Hz), giving sub-perceptible response
    while the kernel Sleep(8) yields the CPU between checks. On
    Linux/macOS, pynput's event-driven Listener is used instead of
    polling.

    The previous 10Hz polling (100ms sleep) introduced up to 100ms
    latency on hotkey detection. The 8ms polling reduces this to
    up to ~8ms while still being CPU-efficient (the thread spends
    >99.9% of its time sleeping in the kernel).

    It dispatches to
    ``_run_modifier_only_polling_loop`` for modifier-only hotkeys
    (e.g. ``<alt>``), those need a different detection logic that
    fires on the modifier press itself, not on a subsequent
    non-modifier keypress. Also suppresses the OS-level caps-lock
    toggle when the hotkey is ``<caps_lock>`` (see
    ``_suppress_caps_lock_toggle``).
    """
    # Modifier-only hotkeys (e.g. <alt>)
    if self._is_modifier_only:
        self._run_modifier_only_polling_loop(callback)
        return

    vk = self._vk
    # Seed was_pressed from the current
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
    # Pre-fix this ran ``import win32gui`` on every 8ms iteration,
    _pump_messages = None
    try:
        import win32gui

        _pump_messages = win32gui.PumpWaitingMessages
    except ImportError:
        pass
    # detect Caps Lock hotkeys so we can
    is_caps_lock_hotkey = vk == _VK_CAPITAL

    # CAPS-LOCK-FIX: at registration time, if the hotkey is Caps Lock,
    if is_caps_lock_hotkey:
        self._ensure_caps_lock_off()

    # The loop body sleeps ~8ms per iteration (PERF-01), so
    _caps_check_iter = 0

    # PERF-01 (c-review): set the Windows timer resolution to 8ms
    _winmm = None
    try:
        _winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
        _winmm.timeBeginPeriod(8)
    except (AttributeError, OSError):
        pass

    try:
        while not self._stop_event.is_set():
            # (25 iterations × 8ms sleep = 200ms). Previously ``% 200``
            _caps_check_iter += 1
            if is_caps_lock_hotkey and _caps_check_iter % 25 == 0 and not self._caps_lock_suppressing:
                self._ensure_caps_lock_off()
            # syscalls per 8ms iteration.
            if self._is_ime_composing_throttled():
                was_pressed = False
                if _pump_messages is not None:
                    with contextlib.suppress(Exception):
                        _pump_messages()
                self._kernel32.Sleep(50)
                continue

            # if we're sending a synthetic
            if self._caps_lock_suppressing:
                if _pump_messages is not None:
                    with contextlib.suppress(Exception):
                        _pump_messages()
                # Caps Lock suppression: brief transient, needs <8ms latency
                self._kernel32.Sleep(1)
                continue

            state = self._user32.GetAsyncKeyState(vk)
            is_pressed = bool(state & 0x8000) and self._modifiers_pressed() and not self._other_modifiers_pressed()
            is_ptt = self._on_release_callback is not None
            toggle_on_keyup = getattr(self, "_toggle_on_keyup", False)
            if is_pressed and not was_pressed:
                log.info("[HOTKEY FIRED] GetAsyncKeyState detected key-down")
                if is_caps_lock_hotkey:
                    self._suppress_caps_lock_toggle()
                if is_ptt or not toggle_on_keyup:
                    try:
                        callback()
                    except Exception:
                        log.exception("[HOTKEY] Callback raised in polling loop; hotkey still armed for next press")
            if not is_pressed and was_pressed:
                if is_ptt:
                    log.info("[HOTKEY] Key released (PTT on_release)")
                    # pyrefly not-callable, ``_on_release_callback``
                    if self._on_release_callback is not None:
                        try:
                            self._on_release_callback()
                        except Exception:
                            log.exception("[HOTKEY] on_release callback raised in polling loop")
                elif toggle_on_keyup:
                    log.info("[HOTKEY FIRED] GetAsyncKeyState detected key-up (toggle)")
                    try:
                        callback()
                    except Exception:
                        log.exception("[HOTKEY] Callback raised in polling loop; hotkey still armed for next press")
            was_pressed = is_pressed
            if _pump_messages is not None:
                with contextlib.suppress(Exception):
                    _pump_messages()
            self._kernel32.Sleep(8)
    finally:
        if _winmm is not None:
            with contextlib.suppress(Exception):
                _winmm.timeEndPeriod(8)


def run_modifier_only_polling_loop(self, callback):
    """Polling loop for modifier-only hotkeys (e.g. ``<alt>``)."""
    # Map the configured _MOD_* flags to the VK codes we need to poll.
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

    # VK codes that count as "modifiers"
    all_modifier_vks = frozenset(
        {
            _VK_SHIFT,  # 0x10
            _VK_CONTROL,  # 0x11
            _VK_MENU,  # 0x12 (Alt)
            _VK_CAPITAL,  # 0x14 (Caps Lock, handled separately)
            _VK_LWIN,  # 0x5B
            _VK_RWIN,  # 0x5C
            0xA0,  # VK_LSHIFT
            0xA1,  # VK_RSHIFT
            0xA2,  # VK_LCONTROL
            0xA3,  # VK_RCONTROL
            0xA4,  # VK_LMENU
            0xA5,  # VK_RMENU
        }
    )

    log.info(
        "[HOTKEY] Modifier-only polling loop started (mods=0x%X, vks=%s)",
        self._modifiers,
        [f"0x{v:02X}" for v in modifier_vks],
    )

    # Per-press-cycle state flags (described in the docstring above).
    modifier_was_pressed = False
    other_key_pressed = False
    press_fired = False

    # PTT mode is detected by the presence of an on_release callback.
    is_ptt = self._on_release_callback is not None

    # PERF-01 (c-review): set accurate timer resolution for the
    _winmm = None
    try:
        _winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
        _winmm.timeBeginPeriod(8)
    except (AttributeError, OSError):
        pass

    try:
        while not self._stop_event.is_set():
            # syscalls per 8ms iteration.
            if self._is_ime_composing_throttled():
                modifier_was_pressed = False
                other_key_pressed = False
                press_fired = False
                self._kernel32.Sleep(50)
                continue

            # FIX-MULTI-MOD: require ALL configured modifiers to be held
            is_held = all(self._key_pressed(vk) for vk in modifier_vks)

            if is_held and not modifier_was_pressed:
                modifier_was_pressed = True
                other_key_pressed = False
                press_fired = False
                # NOTIFICATION (b): press-and-hold must
                if is_ptt:
                    # PTT mode: fire the press callback immediately IF
                    if not self._other_modifiers_pressed():
                        log.info(
                            "[HOTKEY FIRED] Modifier-only press detected (PTT, mods=0x%X)",
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
                            "also held (mods=0x%X), suppressing PTT press fire",
                            self._modifiers,
                        )

            # Polling loop's 8ms cadence (~125 Hz, see PERF-01)
            if is_held and not other_key_pressed and self._any_non_modifier_key_pressed_throttled(all_modifier_vks):
                other_key_pressed = True
                log.debug(
                    "[HOTKEY] Non-modifier key pressed during modifier "
                    "hold (mods=0x%X), will suppress fire on release "
                    "(user was doing a combo like Alt+C)",
                    self._modifiers,
                )

            if not is_held and modifier_was_pressed:
                if not other_key_pressed:
                    # Modifier was pressed and released without any
                    if is_ptt:
                        # PTT mode: fire on_release (stop recording) if
                        if press_fired and self._on_release_callback is not None:
                            log.info(
                                "[HOTKEY] Modifier released alone (PTT on_release, mods=0x%X)",
                                self._modifiers,
                            )
                            try:
                                self._on_release_callback()
                            except Exception:
                                log.exception("[HOTKEY] on_release raised in modifier-only polling loop")
                    else:
                        # Toggle mode: fire the press callback
                        if not self._other_modifiers_pressed():
                            log.info(
                                "[HOTKEY FIRED] Modifier-only press-and-release alone (toggle, mods=0x%X)",
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
                                "modifiers still held (mods=0x%X), suppressing "
                                "toggle fire (combo)",
                                self._modifiers,
                            )
                else:
                    # other_key_pressed is True, user was doing a combo
                    if is_ptt and press_fired and self._on_release_callback is not None:
                        log.info(
                            "[HOTKEY] Modifier released after combo "
                            "(PTT on_release safety, mods=0x%X), stopping "
                            "recording started by the press fire",
                            self._modifiers,
                        )
                        try:
                            self._on_release_callback()
                        except Exception:
                            log.exception("[HOTKEY] on_release (safety) raised in modifier-only polling loop")
                # Reset per-cycle state for the next press.
                modifier_was_pressed = False
                other_key_pressed = False
                press_fired = False

            # 8ms sleep (~125 Hz) with timeBeginPeriod(8) ensures

            self._kernel32.Sleep(8)

    finally:
        if _winmm is not None:
            with contextlib.suppress(Exception):
                _winmm.timeEndPeriod(8)
