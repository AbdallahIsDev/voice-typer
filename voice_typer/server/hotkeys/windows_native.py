"""Windows-native hotkey backend (Win32 RegisterHotKey + GetAsyncKeyState polling).

The big one — ~1,420 LOC god-class in the original ``hotkeys.py``.
Split out in Phase 4.5 (ARCH-045) without any semantic changes.
"""

import contextlib
import ctypes
import threading
import time
from collections.abc import Callable
from typing import Any

from voice_typer.server import hotkeys as _hotkeys_pkg

from .base import HotkeyBackend, log
from .win32_vk import (
    _KEYEVENTF_KEYUP,
    _MOD_ALT,
    _MOD_CONTROL,
    _MOD_NOREPEAT,
    _MOD_SHIFT,
    _MOD_WIN,
    _VK_CAPITAL,
    _VK_CONTROL,
    _VK_LWIN,
    _VK_MENU,
    _VK_RWIN,
    _VK_SHIFT,
    _WHC_KEYBOARD_LL,
    _WM_HOTKEY,
    _WM_KEYDOWN,
    _WM_KEYUP,
    _WM_QUIT,
    _WM_SYSKEYDOWN,
    _WM_SYSKEYUP,
    parse_hotkey_to_win32,
)


# PLAT-020 / patch-target: tests patch
# ``voice_typer.server.hotkeys.is_windows`` and expect the patch to take
# effect on ``WindowsNativeHotkey._is_ime_composing()`` (a static method
# on the class defined here).  The wrapper delegates to the package's
# binding at call time so the patch propagates.
def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


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
        self._thread: threading.Thread | None = None
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
        self._vk: int | None = None
        self._modifiers = 0
        self._using_polling = False  # True if falling back to GetAsyncKeyState
        # ESC-CANCEL-DELIVERY: handle for the WH_KEYBOARD_LL low-level hook
        # (when installed). Must be UnhookWindowsHookEx'd on stop; kept alive
        # here so the hook proc (a ctypes CFUNCTYPE) isn't garbage-collected
        # while the hook is active (a dangling callback would crash the
        # thread).
        self._hook_handle: Any = None
        self._hook_proc: Any = None
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
        # loop's 8ms cadence (~125 Hz, see PERF-01/CPU-01) that's ~625
        # syscalls/sec even when no key is pressed. The throttled wrapper
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
            raise ValueError(f"Cannot parse hotkey {self.hotkey_str!r} to a VK code")
        self._vk, self._modifiers = parsed

        # FIX-HOTKEY-ARCHITECTURE: detect modifier-only specs (e.g.
        # ``<alt>``). For these, ``vk`` is None but ``modifiers`` is
        # non-zero. RegisterHotKey can't be used (no main VK to
        # register), so we skip it and rely on the polling loop's
        # modifier-only detection path.
        self._is_modifier_only = self._vk is None and self._modifiers != 0
        if self._vk is None and not self._is_modifier_only:
            raise ValueError(f"Cannot parse hotkey {self.hotkey_str!r} to a VK code")

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

                    result = self._user32.RegisterHotKey(0, self._hotkey_id, _MOD_NOREPEAT | self._modifiers, self._vk)
                    if not result:
                        err = self._kernel32.GetLastError()
                        self._last_error = err
                        log.warning(
                            "RegisterHotKey failed for VK=0x%X, GetLastError=%d (0x%X) — polling fallback still works",
                            self._vk,
                            err,
                            err,
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
                # ESC-CANCEL-DELIVERY (regression fix): ESC is a *system key*
                # (VK_ESCAPE) that arrives as WM_SYSKEYDOWN. In the real world
                # it is routinely intercepted by the foreground window (the
                # Electron renderer, which uses ESC to close dialogs/overlays)
                # and/or already claimed by another process via RegisterHotKey
                # (ERROR_HOTKEY_ALREADY_REGISTERED / 1409). In both cases the
                # GetAsyncKeyState async-key state is NEVER set for ESC, so a
                # polling-only listener silently misses every press — exactly
                # the reported "Escape does nothing" symptom (and F2 — a plain
                # function key — keeps working, because nothing steals it).
                #
                # The robust, focus-independent fix is a WH_KEYBOARD_LL
                # low-level keyboard hook: Windows calls our hook procedure for
                # EVERY keystroke system-wide, BEFORE any app sees it and
                # REGARDLESS of RegisterHotKey ownership or foreground focus.
                # This is the same mechanism the native windows-key-listener.exe
                # binary uses, so behavior matches across both backends.
                #
                # We therefore PREFER the low-level hook for simple (non-PTT,
                # non-caps-lock) hotkeys such as <esc>. If the hook can't be
                # installed (rare), we fall back to RegisterHotKey + WM_HOTKEY
                # message loop, then to GetAsyncKeyState polling.
                #
                # We keep polling (not the hook/message loop) when a release
                # callback is installed (push-to-talk mode): PTT needs key-UP
                # detection, and the low-level hook would also deliver that,
                # but the polling loop is the proven PTT path and the hook adds
                # nothing for PTT. Caps Lock (VK_CAPITAL) NOW uses the
                # low-level hook when available: the hook sees raw key-up
                # scancodes and can swallow the keydown (preventing the OS from
                # toggling caps state), which makes reliable "toggle on release"
                # detection possible. (Previously Caps Lock was forced onto the
                # polling loop, where the synthetic caps-suppression key-up
                # corrupts the async key state and breaks key-up detection.)
                is_caps_lock_hotkey = self._vk == _VK_CAPITAL
                # Store on the instance so the LL hook proc can suppress
                # Caps Lock by swallowing the keydown (instead of the
                # reactive polling-loop suppression).
                self._is_caps_lock_hotkey = is_caps_lock_hotkey
                # Proactively force Caps Lock OFF (mirrors the polling-loop
                # behavior) so we don't start in ALL-CAPS if the OS state
                # was already ON. Done here (not just in the polling loop)
                # so the LL-hook path is also covered.
                if is_caps_lock_hotkey:
                    self._ensure_caps_lock_off()
                # Caps Lock now uses the low-level hook (when available): the
                # hook sees raw key-up scancodes and can swallow the keydown,
                # which makes reliable "toggle on release" detection possible
                # (the polling loop's synthetic caps suppression corrupts the
                # async key state and breaks key-up detection). Other simple
                # non-PTT hotkeys also prefer the hook for robust delivery.
                simple_key = self._on_release_callback is None and not self._is_modifier_only
                if simple_key and self._install_low_level_hook(callback):
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WH_KEYBOARD_LL "
                        "low-level hook (vk=0x%X) — robust ESC/system-key delivery",
                        self._vk,
                    )
                    self._using_polling = False
                    self._run_message_loop(callback, low_level_hook=True)
                elif self._registered and not is_caps_lock_hotkey:
                    # RegisterHotKey succeeded (and not caps-lock) → WM_HOTKEY
                    # message loop (focus-independent, event-driven, ~0% CPU).
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WM_HOTKEY message loop (registered VK=0x%X, id=%d)",
                        self._vk,
                        self._hotkey_id,
                    )
                    self._using_polling = False
                    self._run_message_loop(callback, low_level_hook=False)
                else:
                    # Use GetAsyncKeyState polling for reliable hotkey detection.
                    # RegisterHotKey + GetMessageW does not reliably deliver WM_HOTKEY
                    # on all Windows configurations.  PERF-012 / PERF-01 / CPU-01: the
                    # polling loop in _run_polling_loop() uses Sleep(8) with
                    # timeBeginPeriod(8) (~125 Hz effective check rate), which gives
                    # up to ~8 ms hotkey-detection latency while still yielding the
                    # CPU between checks — the thread spends >99.9% of its time
                    # sleeping in the kernel.  See _run_polling_loop() for the
                    # rationale and the regression test that pins this invariant.
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
            ctypes.wintypes.BYTE,
            ctypes.wintypes.BYTE,
            DWORD,
            WPARAM,
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
            raise RuntimeError(f"Timed out waiting for hotkey registration of {self.hotkey_str!r}")
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
                return comp_len > 0
            finally:
                imm32.ImmReleaseContext(hwnd, himc)
        except Exception:
            return False

    def _is_ime_composing_throttled(self) -> bool:
        """PERF-FIX-1: throttled wrapper around ``_is_ime_composing()``.

        The underlying staticmethod makes 5 syscalls per call (see
        ``__init__`` for the rationale). The polling loop runs at 8ms
        cadence (~125 Hz, see PERF-01/CPU-01), so calling it every
        iteration would be ~625 syscalls/sec. This wrapper re-queries
        at most every 50ms (20 Hz) and returns the cached result
        between queries.

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
                _caps_check_iter += 1
                if is_caps_lock_hotkey and _caps_check_iter % 200 == 0 and not self._caps_lock_suppressing:
                    self._ensure_caps_lock_off()
                # PLAT-020: suppress hotkey triggers during IME composition.
                # PERF-FIX-1: use the throttled wrapper so we don't make 5
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

    def _run_message_loop(self, callback, low_level_hook=False):
        """Message-pump hotkey detection (event-driven, ~0% CPU while idle).

        Two modes, selected by *low_level_hook*:

        * ``low_level_hook=True`` — a WH_KEYBOARD_LL hook is already installed
          (see ``_install_low_level_hook``). The hook procedure fires
          *callback* directly on the matching key-down (it sees EVERY
          keystroke system-wide, including ESC as WM_SYSKEYDOWN, before any
          app and regardless of RegisterHotKey ownership / foreground focus).
          The message pump here just keeps the thread alive so the hook's
          CallNextHookEx chain and the OS can deliver events; it does NOT
          need to inspect messages itself.

        * ``low_level_hook=False`` — RegisterHotKey succeeded, so WM_HOTKEY
          messages are posted to this thread. We detect them here.

        ESC-CANCEL-DELIVERY (regression fix): this is the reliable path for
        keys that GetAsyncKeyState polling misses. For the registered case,
        ``RegisterHotKey(NULL, id, MOD, vk)`` posts ``WM_HOTKEY`` to THIS
        thread's message queue regardless of foreground focus. For the
        low-level-hook case, the hook catches the key even when another
        process owns it via RegisterHotKey (the 1409 "already registered"
        failure), which is the exact real-world ESC failure we fix.
        """
        if not self._user32:
            # No win32 — should never happen on this path, but fall back to
            # polling defensively.
            log.warning("[HOTKEY] No user32 on message-loop path — falling back to polling")
            self._using_polling = True
            self._run_polling_loop(callback)
            return

        # Set argtypes/restype for the message-pump calls we use here.
        try:
            self._user32.GetMessageW.argtypes = [
                ctypes.POINTER(ctypes.wintypes.MSG),
                ctypes.wintypes.HWND,
                ctypes.wintypes.UINT,
                ctypes.wintypes.UINT,
            ]
            self._user32.GetMessageW.restype = ctypes.c_long
            self._user32.TranslateMessage.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
            self._user32.TranslateMessage.restype = ctypes.c_int
            self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
            self._user32.DispatchMessageW.restype = ctypes.c_long
        except Exception:
            log.exception("[HOTKEY] Failed to set message-pump argtypes — falling back to polling")
            self._using_polling = True
            self._run_polling_loop(callback)
            return

        if low_level_hook:
            log.info(
                "[HOTKEY] Message loop running with WH_KEYBOARD_LL hook (vk=0x%X, mods=0x%X)",
                self._vk,
                self._modifiers,
            )
        else:
            log.info(
                "[HOTKEY] WM_HOTKEY message loop running (vk=0x%X, id=%d, mods=0x%X)",
                self._vk,
                self._hotkey_id,
                self._modifiers,
            )
        msg = ctypes.wintypes.MSG()
        while not self._stop_event.is_set():
            # PM_REMOVE = 0x0001: remove the message from the queue.
            ret = self._user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if ret == 0:  # WM_QUIT
                log.info("[HOTKEY] WM_QUIT received — exiting message loop")
                break
            if ret == -1:  # error
                log.warning("[HOTKEY] GetMessageW returned -1 (error) — exiting message loop")
                break
            if not low_level_hook and msg.message == _WM_HOTKEY and msg.wParam == self._hotkey_id:
                log.info("[HOTKEY FIRED] WM_HOTKEY received for %s", self.hotkey_str)
                try:
                    callback()
                except Exception:
                    # ERR-020: shield the callback so a single failure
                    # doesn't kill the message loop (mirrors polling loop).
                    log.exception("[HOTKEY] Callback raised in WM_HOTKEY loop; hotkey still armed for next press")
            # Always translate/dispatch so any other messages (timers, etc.)
            # are processed normally — required for the hook to function.
            try:
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
            except Exception:
                log.debug("[HOTKEY] Translate/Dispatch failed for a message", exc_info=True)

    def _install_low_level_hook(self, callback) -> bool:
        """Install a WH_KEYBOARD_LL low-level keyboard hook for this hotkey.

        ESC-CANCEL-DELIVERY (regression fix): returns True if the hook was
        installed successfully. The hook procedure runs in THIS thread (the
        one pumping GetMessageW) and fires *callback* when the configured key
        (VK + modifiers) goes down — catching ESC/system keys that
        GetAsyncKeyState polling and even a failed RegisterHotKey miss.

        Returns False if the hook cannot be installed (e.g. SetWindowsHookEx
        is unavailable / fails), so the caller can fall back to the
        RegisterHotKey message loop or polling.

        The hook proc is stored on ``self._hook_proc`` to keep it alive
        (ctypes callbacks are collected if only referenced by the hook), and
        ``self._hook_handle`` is uninstalled in ``stop()``.
        """
        if not self._user32 or not self._kernel32:
            return False
        try:
            # KeyboardProc signature: (nCode, wParam, lParam) -> LRESULT.
            # lParam is a pointer to KBDLLHOOKSTRUCT.
            hook_proc = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_int,
                ctypes.wintypes.WPARAM,
                ctypes.wintypes.LPARAM,
            )

            # KBDLLHOOKSTRUCT: vkCode, scanCode, flags, time, dwExtraInfo
            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", ctypes.c_ulong),
                    ("scanCode", ctypes.c_ulong),
                    ("flags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            backend = self  # closure reference

            def _hook_proc(n_code, w_param, l_param):
                try:
                    if n_code == 0:  # HC_ACTION
                        ks = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        vk = ks.vkCode
                        is_caps = backend._is_caps_lock_hotkey
                        # Key-down path
                        if w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN) and (
                            vk == backend._vk and backend._modifiers_pressed()
                        ):
                            if is_caps:
                                # Caps Lock: swallow the keydown so the OS
                                # never toggles caps state. The toggle fires
                                # on the physical key-up (see below). This is
                                # the same suppression the native
                                # windows-key-listener.exe binary performs.
                                log.info("[HOTKEY] Swallowed Caps Lock keydown (suppress OS toggle)")
                                return 1  # swallow — do not call CallNextHookEx
                            if backend._on_release_callback is not None:
                                # Push-to-talk: start recording on press.
                                log.info(
                                    "[HOTKEY FIRED] WH_KEYBOARD_LL caught vk=0x%X (PTT)",
                                    vk,
                                )
                                try:
                                    callback()
                                except Exception:
                                    log.exception("[HOTKEY] Callback raised in LL hook; hotkey still armed")
                            elif getattr(backend, "_toggle_on_keyup", False):
                                # Toggle mode (user requested): defer the
                                # toggle to key-up so holding the key cannot
                                # start-then-stop recording. Do nothing here.
                                pass
                            else:
                                # Legacy toggle (e.g. ESC cancel): fire on
                                # key-down as before.
                                log.info(
                                    "[HOTKEY FIRED] WH_KEYBOARD_LL caught vk=0x%X (%s)",
                                    vk,
                                    backend.hotkey_str,
                                )
                                try:
                                    callback()
                                except Exception:
                                    log.exception("[HOTKEY] Callback raised in LL hook; hotkey still armed")
                        # Key-up path
                        elif w_param in (_WM_KEYUP, _WM_SYSKEYUP) and (vk == backend._vk):
                            if is_caps:
                                # Caps Lock: fire the toggle exactly once on
                                # the physical key-up, and swallow the keyup
                                # so the OS sees no orphan key-up.
                                log.info("[HOTKEY FIRED] WH_KEYBOARD_LL Caps Lock key-up (toggle)")
                                try:
                                    callback()
                                except Exception:
                                    log.exception("[HOTKEY] Callback raised in LL hook; hotkey still armed")
                                return 1  # swallow keyup
                            if backend._on_release_callback is not None:
                                # Push-to-talk: stop recording on release.
                                log.info(
                                    "[HOTKEY] Key released via WH_KEYBOARD_LL hook (vk=0x%X)",
                                    vk,
                                )
                                try:
                                    backend._on_release_callback()
                                except Exception:
                                    log.exception("[HOTKEY] on_release callback raised in LL hook")
                            elif getattr(backend, "_toggle_on_keyup", False):
                                # Toggle mode: fire the toggle on key-up.
                                # Holding the key (no key-up) never toggles,
                                # so a press-and-hold cannot start-then-stop
                                # recording. This is the user-requested
                                # behavior.
                                log.info(
                                    "[HOTKEY FIRED] WH_KEYBOARD_LL key-up (toggle, vk=0x%X)",
                                    vk,
                                )
                                try:
                                    callback()
                                except Exception:
                                    log.exception("[HOTKEY] Callback raised in LL hook; hotkey still armed")
                except Exception:
                    log.debug("[HOTKEY] LL hook proc error", exc_info=True)
                # Pass to the next hook so we don't break other hooks.
                return backend._user32.CallNextHookEx(backend._hook_handle or 0, n_code, w_param, l_param)

            self._hook_proc = hook_proc(_hook_proc)
            # Set argtypes for SetWindowsHookExW / UnhookWindowsHookEx.
            self._user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                hook_proc,
                ctypes.wintypes.HINSTANCE,
                ctypes.wintypes.DWORD,
            ]
            self._user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
            self._user32.CallNextHookEx.argtypes = [
                ctypes.wintypes.HHOOK,
                ctypes.c_int,
                ctypes.wintypes.WPARAM,
                ctypes.wintypes.LPARAM,
            ]
            self._user32.CallNextHookEx.restype = ctypes.c_long
            self._user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
            self._user32.UnhookWindowsHookEx.restype = ctypes.c_int

            # hMod must be NULL for a low-level hook (it runs in this process).
            handle = self._user32.SetWindowsHookExW(_WHC_KEYBOARD_LL, self._hook_proc, 0, 0)
            if not handle:
                # PVT-G5-043: previously this log line claimed "(GetLastError)"
                # but never fetched the error code. Mirror the RegisterHotKey
                # pattern at line 198-205 — fetch immediately and include both
                # decimal and hex so the user can look up the Win32 error.
                err = self._kernel32.GetLastError()
                self._last_error = err
                log.warning(
                    "[HOTKEY] SetWindowsHookExW(WH_KEYBOARD_LL) failed, "
                    "GetLastError=%d (0x%X) — falling back to RegisterHotKey/polling",
                    err,
                    err,
                )
                self._hook_proc = None
                return False
            self._hook_handle = handle
            log.info("[HOTKEY] WH_KEYBOARD_LL hook installed (vk=0x%X)", self._vk)
            return True
        except Exception:
            log.exception("[HOTKEY] Failed to install WH_KEYBOARD_LL hook")
            self._hook_proc = None
            self._hook_handle = None
            return False

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
                # PLAT-020: suppress hotkey triggers during IME composition.
                # Reset all per-cycle state so a stray IME composition doesn't
                # leak into the next press cycle.
                # PERF-FIX-1: use the throttled wrapper so we don't make 5
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
                # PERF-FIX-1: the scan is O(248) per iteration
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

    def _any_non_modifier_key_pressed(self, modifier_vks: "frozenset[int]") -> bool:
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
        loop runs at 8ms cadence (~125 Hz, see PERF-01/CPU-01), so
        calling this every iteration while the modifier is held would
        be up to ~31k syscalls/sec. The loop wraps this call in
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

    def _any_non_modifier_key_pressed_throttled(self, modifier_vks: "frozenset[int]") -> bool:
        """PERF-FIX-1: throttled wrapper around
        ``_any_non_modifier_key_pressed()``.

        The underlying scan is O(248) per call (see the docstring on
        ``_any_non_modifier_key_pressed`` for the rationale). The
        modifier-only polling loop runs at 8ms cadence (~125 Hz, see
        PERF-01/CPU-01), so calling it every iteration while the
        modifier is held would be up to ~31k syscalls/sec. This
        wrapper re-scans at most every 50ms (20 Hz), reducing the
        syscall rate to ~5k/sec.

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
        cadence (~125 Hz, see PERF-01/CPU-01) still gives ~8ms
        modifier press/release latency (the scan throttle only affects
        combo detection, not the hotkey fire itself).
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
        return bool(not self._modifiers & _MOD_ALT and self._is_altgr_pressed())

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
                    self._user32.keybd_event(_VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP, 0)
                    log.debug("[HOTKEY] Suppressed Caps Lock toggle (toggled back off)")
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
                self._user32.keybd_event(_VK_CAPITAL, 0x45, _KEYEVENTF_KEYUP, 0)
                log.info("[HOTKEY] Proactive caps lock toggle-off (was ON, forced OFF)")
        except Exception:
            log.exception("[HOTKEY] Failed to force caps lock off")

    def _modifiers_pressed(self) -> bool:
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

        ESC-CANCEL-DELIVERY: when the backend is on the WM_HOTKEY
        message-loop path (RegisterHotKey succeeded), ``GetMessageW``
        BLOCKS until a message arrives — so the ``_stop_event`` check
        alone would never wake it and ``stop()`` would hang for the full
        join timeout. We therefore post ``WM_QUIT`` to the hotkey thread
        via ``PostThreadMessageW`` on that path, which unblocks
        GetMessageW (it returns 0) and lets the loop exit promptly. The
        polling-only path ignores WM_QUIT (it has no message loop) and
        relies on the ``_stop_event`` poll as before.

        ERR-QUIT-001 (fix): early-return if already stopped so the
        duplicate log lines don't appear when quit_app and quit()
        both call stop().
        """
        if self._stop_event.is_set():
            return  # Already stopped — idempotent
        log.debug("[HOTKEY] Stopping %s listener", self.hotkey_str)
        self._stop_event.set()
        # ESC-CANCEL-DELIVERY: unblock a blocked GetMessageW on the
        # message-loop paths (WH_KEYBOARD_LL hook OR RegisterHotKey succeeded).
        # ``GetMessageW`` BLOCKS until a message arrives, so the
        # ``_stop_event`` check alone would never wake it and ``stop()``
        # would hang for the full join timeout. Post ``WM_QUIT`` to the
        # hotkey thread via ``PostThreadMessageW``, which unblocks
        # GetMessageW (it returns 0) and lets the loop exit promptly. The
        # polling-only path ignores WM_QUIT (it has no message loop) and
        # relies on the ``_stop_event`` poll as before.
        on_message_loop = (
            (self._hook_handle is not None or self._registered) and self._thread is not None and self._thread.is_alive()
        )
        if on_message_loop:
            try:
                self._user32.PostThreadMessageW(self._thread.ident, _WM_QUIT, 0, 0)
            except Exception:
                log.debug("[HOTKEY] PostThreadMessageW(WM_QUIT) failed", exc_info=True)
        # Uninstall the low-level hook (if installed) so it stops
        # intercepting keystrokes. Do this after setting _stop_event so the
        # hook proc sees the stop and the thread can exit.
        if self._hook_handle is not None:
            try:
                self._user32.UnhookWindowsHookEx(self._hook_handle)
            except Exception:
                log.debug("[HOTKEY] UnhookWindowsHookEx failed", exc_info=True)
            self._hook_handle = None
            self._hook_proc = None
        # PERF-NEW-016: skip the useless PostThreadMessageW call on the
        # polling path — the polling loop checks _stop_event.is_set()
        # every 100ms.
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
        vk_str = f"0x{self._vk:X} ({self._vk})" if self._vk is not None else "(modifier-only, no main VK)"
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
