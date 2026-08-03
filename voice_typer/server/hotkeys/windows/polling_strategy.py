"""GetAsyncKeyState polling-loop strategies for ``WindowsNativeHotkey``.

Extracted from the original ``windows_native.py`` god-class (EC-29
split). Contains:

- ``run_polling_loop`` — the main GetAsyncKeyState polling loop
  (used for non-modifier hotkeys when RegisterHotKey and the LL
  hook both fail / are skipped).
- ``run_modifier_only_polling_loop`` — a separate polling loop for
  modifier-only hotkeys (e.g. ``<alt>``) that fires on the
  modifier press/release itself.
- Small stateless key-state helpers (``modifiers_pressed``,
  ``other_modifiers_pressed``, ``is_altgr_pressed``,
  ``key_pressed``, ``any_non_modifier_key_pressed``,
  ``any_non_modifier_key_pressed_throttled``).

Each function takes ``self`` as its first parameter so it can be
assigned as a method on ``WindowsNativeHotkey`` (Python's descriptor
protocol passes the instance as ``self``). Source-inspection tests
that read ``inspect.getsource(WindowsNativeHotkey._run_polling_loop)``
will see the source of ``run_polling_loop`` defined here.
"""

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

# ---------------------------------------------------------------------------
# Key-state helpers (stateless — operate on backend._user32)
# ---------------------------------------------------------------------------


def key_pressed(self, vk: int) -> bool:
    """Return True if the given VK code is currently held down.

    Defensive guard: returns ``False`` when ``self._user32`` is
    ``None`` (non-Windows test host). Without this guard, a direct
    call would raise ``AttributeError: 'NoneType' object has no
    attribute 'GetAsyncKeyState'``.
    """
    # defensive guard — sibling methods (``_other_modifiers_pressed``,
    # ``_is_altgr_pressed``, and ``_modifiers_pressed`` above) already
    # early-return ``False`` when ``self._user32`` is None. Add the
    # same guard here so a direct call to ``_key_pressed(vk)`` from
    # any other call site doesn't raise ``AttributeError: 'NoneType'
    # object has no attribute 'GetAsyncKeyState'`` on a non-Windows
    # test host.
    if not self._user32:
        return False
    return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)


def is_altgr_pressed(self) -> bool:
    """Detect if AltGr is currently pressed.

    PLAT-ALTGR: Windows simulates AltGr as Ctrl+RightAlt. We detect
    this by checking if Right Alt (VK=0xA5) is pressed AND Ctrl is
    also pressed. If both are held, it's AltGr — not a Ctrl+Alt combo.
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


def modifiers_pressed(self) -> bool:
    """Return True if ALL configured modifiers are currently held.

    PLAT-ALTGR: Detect AltGr (Right Alt + Ctrl simulated by Windows).
    On non-US keyboards, AltGr is used for characters like @, €, #.
    Windows simulates AltGr as Ctrl+Alt. If AltGr is detected, don't
    treat it as a modifier press for hotkey purposes.
    """
    # defensive guard — sibling methods (``_other_modifiers_pressed``
    # and ``_is_altgr_pressed``) already early-return ``False`` when
    # ``self._user32`` is None (non-Windows test host). Without this
    # guard, ``_key_pressed`` (below) would raise
    # ``AttributeError: 'NoneType' object has no attribute
    # 'GetAsyncKeyState'`` when this method is invoked from a
    # non-Windows test host (the hotkey listener is constructed
    # lazily on Windows only, but tests exercise the polling path
    # with ``_user32=None`` to verify the fallback).
    if not self._user32:
        return False
    # PLAT-ALTGR: Detect AltGr (Right Alt + Ctrl simulated by Windows).
    # On non-US keyboards, AltGr is used for characters like @, €, #.
    # Windows simulates AltGr as Ctrl+Alt. If AltGr is detected,
    # don't treat it as a modifier press for hotkey purposes.
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
    return bool(not self._modifiers & _MOD_ALT and self._is_altgr_pressed())


def any_non_modifier_key_pressed(self, modifier_vks: frozenset[int]) -> bool:
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
    # cached True would leak into the next press cycle (see the
    # docstring) — when the last result was True, always re-scan.
    if not self._last_nonmod_pressed and now - self._last_nonmod_check_time < 0.05:
        return False
    result = self._any_non_modifier_key_pressed(modifier_vks)
    self._last_nonmod_pressed = result
    self._last_nonmod_check_time = now
    return result


# ---------------------------------------------------------------------------
# Polling loops
# ---------------------------------------------------------------------------


def run_polling_loop(self, callback):
    """GetAsyncKeyState polling fallback for hotkey detection.

    PERF-012 / PERF-003 / PERF-01 / CPU-01: On Windows, uses
    GetAsyncKeyState in a tight loop with an 8ms sleep. The Windows
    timer resolution is bumped to 8ms via ``timeBeginPeriod(8)``
    before the loop so ``Sleep(8)`` actually sleeps ~8ms instead of
    the default ~15.6ms. This is still technically polling but at a
    much lower cost than the previous 100ms (10Hz) approach — the key
    is checked every 8ms (~125 Hz), giving sub-perceptible response
    while the kernel Sleep(8) yields the CPU between checks. On
    Linux/macOS, pynput's event-driven Listener is used instead of
    polling.

    The previous 10Hz polling (100ms sleep) introduced up to 100ms
    latency on hotkey detection. The 8ms polling reduces this to
    up to ~8ms while still being CPU-efficient (the thread spends
    >99.9% of its time sleeping in the kernel).

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
    # Pre-fix this ran ``import win32gui`` on every 8ms iteration,
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
    is_caps_lock_hotkey = vk == _VK_CAPITAL

    # CAPS-LOCK-FIX: at registration time, if the hotkey is Caps Lock,
    # force caps lock OFF to prevent the user from typing in ALL CAPS.
    # This proactively handles the case where caps lock was ON before
    # the app started or when the hotkey configuration changes.
    if is_caps_lock_hotkey:
        self._ensure_caps_lock_off()

    # Iteration counter for periodic caps lock state checks (~200ms cadence).
    # the loop body sleeps ~8ms per iteration (PERF-01/CPU-01), so
    # a check every 25 iterations fires every 25 × 8ms = 200ms — matching
    # the documented cadence. Previously this was ``% 200``, which (with
    # the 8ms sleep) gave 200 × 8ms = 1600ms — an 8× discrepancy with the
    # comments. The modulus was likely chosen when the sleep was 1ms; the
    # sleep was later increased to 8ms without updating the modulus.
    _caps_check_iter = 0

    # PERF-01 / CPU-01 (c-review): set the Windows timer resolution to 8ms
    # before the loop so Sleep(8) below sleeps accurately (~125 Hz) instead
    # of the default ~15.6ms (64 Hz) or potentially 1000 Hz if another
    # process set 1ms resolution. Restored in the finally block so the
    # timer is cleaned up on both normal exit and exception.
    _winmm = None
    try:
        _winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
        _winmm.timeBeginPeriod(8)
    except (AttributeError, OSError):
        pass

    try:
        while not self._stop_event.is_set():
            # CAPS-LOCK-FIX: periodically ensure caps lock stays OFF.
            # The reactive suppression on key press can fail due to timing
            # (OS toggles caps before we can undo it). A periodic ~200ms
            # check catches any missed toggles and re-silences caps lock.
            # ``% 25`` matches the documented 200ms cadence
            # (25 iterations × 8ms sleep = 200ms). Previously ``% 200``
            # delivered 1.6s — see the comment at the ``_caps_check_iter``
            # declaration above for the root-cause analysis.
            _caps_check_iter += 1
            if is_caps_lock_hotkey and _caps_check_iter % 25 == 0 and not self._caps_lock_suppressing:
                self._ensure_caps_lock_off()
            # suppress hotkey triggers during IME composition.
            # PERF- use the throttled wrapper so we don't make 5
            # syscalls per 8ms iteration.
            if self._is_ime_composing_throttled():
                was_pressed = False
                if _pump_messages is not None:
                    with contextlib.suppress(Exception):
                        _pump_messages()
                self._kernel32.Sleep(50)
                continue

            # FIX-HOTKEY-ARCHITECTURE: if we're sending a synthetic
            # Caps Lock keypress to undo the OS toggle, skip processing
            # so the synthetic events don't re-trigger the callback or
            # prematurely fire on_release. The suppression flag is
            # cleared by _suppress_caps_lock_toggle() itself.
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
                    # pyrefly not-callable — ``_on_release_callback``
                    # is typed as ``Callable[[], None] | None`` and pyrefly
                    # can't propagate the narrowing from ``is_ptt =
                    # self._on_release_callback is not None`` (line 559)
                    # into this branch. Explicit None guard makes the
                    # narrowing local and silences the false positive.
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
    all_modifier_vks = frozenset(
        {
            _VK_SHIFT,  # 0x10
            _VK_CONTROL,  # 0x11
            _VK_MENU,  # 0x12 (Alt)
            _VK_CAPITAL,  # 0x14 (Caps Lock — handled separately)
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

    # PERF-01 / CPU-01 (c-review): set accurate timer resolution for the
    # modifier-only polling fallback (same rationale as _run_polling_loop).
    _winmm = None
    try:
        _winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
        _winmm.timeBeginPeriod(8)
    except (AttributeError, OSError):
        pass

    try:
        while not self._stop_event.is_set():
            # suppress hotkey triggers during IME composition.
            # Reset all per-cycle state so a stray IME composition doesn't
            # leak into the next press cycle.
            # PERF- use the throttled wrapper so we don't make 5
            # syscalls per 8ms iteration.
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
            # PERF- the scan is O(248) per iteration
            # (GetAsyncKeyState for every VK in 0x08-0xFF). At the
            # polling loop's 8ms cadence (~125 Hz, see PERF-01/CPU-01)
            # that's up to ~31k syscalls/sec while the modifier is held.
            # The throttled wrapper
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
            #     polling loop's 8ms cadence still gives ~8ms modifier
            #     press/release latency (the scan throttle only affects
            #     combo detection, not the hotkey fire itself).
            # This scan is intentionally called every iteration while
            # held (NOT just on the not-held→held transition) because
            # the user can press a non-modifier key at any point during
            # the hold, and we need to detect it before the release
            # transition fires the callback.
            if is_held and not other_key_pressed and self._any_non_modifier_key_pressed_throttled(all_modifier_vks):
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
                                "[HOTKEY] Modifier released alone (PTT on_release, mods=0x%X)",
                                self._modifiers,
                            )
                            try:
                                self._on_release_callback()
                            except Exception:
                                log.exception("[HOTKEY] on_release raised in modifier-only polling loop")
                    else:
                        # Toggle mode: fire the press callback
                        # (toggle_dictation). Double-check no other
                        # modifiers are currently held at release time
                        # — if the user is still holding Ctrl when they
                        # release Alt, that's a combo, not the hotkey.
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
                            log.exception("[HOTKEY] on_release (safety) raised in modifier-only polling loop")
                # Reset per-cycle state for the next press.
                modifier_was_pressed = False
                other_key_pressed = False
                press_fired = False

            # PERF-012: 8ms sleep (~125 Hz) with timeBeginPeriod(8) ensures
            # accurate sleep duration for the fallback polling loop.

            self._kernel32.Sleep(8)

    finally:
        if _winmm is not None:
            with contextlib.suppress(Exception):
                _winmm.timeEndPeriod(8)
