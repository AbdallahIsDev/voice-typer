"""Win32 hotkey registration, polling loops, and lifecycle — mixin
extracted from the original ``WindowsNativeHotkey`` god-class.

Holds the methods that:
- start / stop the hotkey listener thread (``start``, ``stop``,
  ``is_alive``, ``diagnose``);
- register / unregister the Win32 hotkey (``RegisterHotKey`` +
  ``UnregisterHotKey``);
- install / uninstall the low-level ``WH_KEYBOARD_LL`` hook;
- run the message-pump / polling loops that deliver hotkey triggers
  (``_run_message_loop``, ``_run_polling_loop``,
  ``_run_modifier_only_polling_loop``);
- manage the dedicated worker thread that drains the LL hook callback
  queue (``_start_hook_callback_worker``, ``_enqueue_hook_callback``);
- compute the per-modifier VK list (``_compute_modifier_vks``).

These are the "register a hotkey + deliver triggers" concerns — distinct
from the key-state queries / keystroke injection in
:mod:`.win32_keystroke` and the ctypes-ABI declarations in
:mod:`.win32_bindings`.

The methods are defined as a Mixin so :class:`.windows_native.WindowsNativeHotkey`
can compose them with :class:`.win32_keystroke.Win32KeystrokeMixin` and
the :class:`.base.HotkeyBackend` ABC without copying any code. Every
method references ``self.`` exactly as before the split, so the public
API (``backend.start(cb)``, ``backend.stop()``, etc.) is preserved
byte-for-byte. ``inspect.getsource(WindowsNativeHotkey._run_polling_loop)``
returns the source defined HERE (Python's introspection follows the
method's ``__code__.co_filename``), so the existing source-inspection
regression tests keep passing without modification.
"""

from __future__ import annotations

import contextlib
import ctypes
import queue
import threading
from collections.abc import Callable

from .base import log
from .win32_bindings import (
    KBDLLHOOKSTRUCT,
    make_hook_proc_type,
    set_async_key_state_argtypes,
    set_low_level_hook_argtypes,
    set_message_pump_argtypes,
    set_register_hotkey_argtypes,
)
from .win32_vk import (
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


class Win32HotkeyRegisterMixin:
    """Hotkey registration + polling + hook lifecycle for
    :class:`.windows_native.WindowsNativeHotkey`.

    The mixin assumes the host class also mixes in
    :class:`.win32_keystroke.Win32KeystrokeMixin` (for
    ``_modifiers_pressed``, ``_is_ime_composing_throttled``,
    ``_suppress_caps_lock_toggle``, ``_ensure_caps_lock_off``,
    ``_other_modifiers_pressed``, ``_any_non_modifier_key_pressed_throttled``,
    ``_key_pressed``) and inherits :class:`.base.HotkeyBackend`
    (for ``hotkey_str``, ``_on_release_callback``, ``_toggle_on_keyup``,
    ``set_on_release``, ``set_toggle_on_keyup``).
    """

    @staticmethod
    def _compute_modifier_vks(modifiers: int) -> list[int]:
        """Return VK codes for the modifier flags."""
        modifier_vks: list[int] = []
        if modifiers & _MOD_ALT:
            modifier_vks.append(_VK_MENU)
        if modifiers & _MOD_CONTROL:
            modifier_vks.append(_VK_CONTROL)
        if modifiers & _MOD_SHIFT:
            modifier_vks.append(_VK_SHIFT)
        if modifiers & _MOD_WIN:
            modifier_vks.append(_VK_LWIN)
            modifier_vks.append(_VK_RWIN)
        return modifier_vks

    def _start_hook_callback_worker(self) -> None:
        """Start the dedicated worker thread for LL hook callbacks."""
        if self._hook_callback_thread is not None and self._hook_callback_thread.is_alive():
            return

        def _worker() -> None:
            while True:
                try:
                    fn = self._hook_callback_queue.get(timeout=0.5)
                except queue.Empty:
                    if self._stop_event.is_set():
                        return
                    continue
                if fn is None:
                    return
                try:
                    fn()
                except Exception:
                    log.exception(
                        "[HOTKEY] Hook callback raised in worker thread — "
                        "callback dropped, hotkey still armed for next press"
                    )

        self._hook_callback_thread = threading.Thread(target=_worker, daemon=True, name="WinHookCb")
        self._hook_callback_thread.start()

    def _enqueue_hook_callback(self, fn: Callable[[], None] | None) -> None:
        """Enqueue a callback for the worker thread. Non-blocking."""
        if fn is None:
            return
        try:
            self._hook_callback_queue.put_nowait(fn)
        except queue.Full:
            log.warning(
                "[HOTKEY] LL hook callback queue full (size=%d) — dropping callback.",
                self._hook_callback_queue.maxsize,
            )

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
        # Delegated to win32_bindings to avoid duplicating the Win32 ABI
        # declarations across start() / _run_message_loop() / _install_low_level_hook().
        set_register_hotkey_argtypes(self._user32, self._kernel32)

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
                # NOTE: ``_ready_event.set()`` is deferred until AFTER the
                # detection-mode decision below so callers that block on
                # ``start()`` (which joins on ``_ready_event``) observe a
                # consistent post-detection-setup state — specifically
                # ``_using_polling`` and ``_hook_handle`` are already
                # assigned by the time ``start()`` returns. Without this
                # ordering, tests like
                # ``test_fallback_on_register_failure`` (which assert
                # ``_hook_handle is not None`` immediately after
                # ``start()`` returns) race the worker thread and
                # intermittently fail. (Set ``_success`` here so a
                # mid-decision exception still surfaces success=False
                # via the finally block below.)

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
                # Drop the ``not self._is_modifier_only`` guard so
                # modifier-only specs (e.g. ``<alt>``) ALSO use the LL hook
                # when available — they were previously forced onto the
                # 125Hz polling loop, burning CPU even when idle.
                simple_key = self._on_release_callback is None
                # Populate the per-instance modifier VK list so the
                # LL hook proc closure can match modifier VKs when _vk is None.
                self._modifier_vks_for_hook = self._compute_modifier_vks(self._modifiers)
                # when ``_prefer_message_loop_first`` is set (ESC and
                # repaste backends), prefer the event-driven WM_HOTKEY
                # message loop over the per-keystroke LL hook. This reduces
                # the number of system-wide WH_KEYBOARD_LL hooks from 3
                # (one per backend) to typically 1 (main dictation only).
                # We still fall back to the LL hook if RegisterHotKey failed
                # (some keys are reserved / already claimed), so the worst
                # case is 2 hooks instead of 3 — still an improvement.
                prefer_message_loop = self._prefer_message_loop_first and self._registered and not is_caps_lock_hotkey
                if prefer_message_loop:
                    # WM_HOTKEY message loop — event-driven, ~0% CPU
                    # while idle (no per-keystroke hook proc).
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WM_HOTKEY message loop "
                        "(prefer_message_loop=True, vk=0x%X, id=%d) — skips LL hook",
                        self._vk,
                        self._hotkey_id,
                    )
                    self._using_polling = False
                    self._ready_event.set()
                    self._run_message_loop(callback, low_level_hook=False)
                elif simple_key and self._install_low_level_hook(callback):
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WH_KEYBOARD_LL "
                        "low-level hook (vk=0x%X) — robust ESC/system-key delivery",
                        self._vk,
                    )
                    self._using_polling = False
                    self._ready_event.set()
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
                    self._ready_event.set()
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
                    self._ready_event.set()
                    self._run_polling_loop(callback)

            except Exception:
                log.exception("[HOTKEY] Windows hotkey thread error")
                # Ensure ``start()`` doesn't time out waiting on
                # ``_ready_event`` if an exception aborted the detection
                # setup. ``_success`` retains its current value (True if
                # RegisterHotKey itself succeeded but detection-mode setup
                # failed, False otherwise) so ``start()``'s post-wait
                # ``_success`` check still surfaces the failure correctly.
                self._ready_event.set()
            finally:
                # Cleanup
                if self._registered:
                    self._user32.UnregisterHotKey(0, self._hotkey_id)
                    self._registered = False
                    log.debug("[HOTKEY] Unregistered %s", self.hotkey_str)

        # Also set GetAsyncKeyState argtypes for the polling fallback
        # (and keybd_event / GetKeyState used by the Caps Lock suppression).
        # Consolidated in win32_bindings alongside the register-hotkey ABI.
        set_async_key_state_argtypes(self._user32, self._kernel32)

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
        # Delegated to win32_bindings — same ABI declarations, no duplication.
        try:
            set_message_pump_argtypes(self._user32)
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
                    # shield the callback so a single failure
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
            # Start the worker thread BEFORE installing the hook
            # so the queue is being drained the moment the first key
            # event arrives.
            self._start_hook_callback_worker()

            # KeyboardProc signature: (nCode, wParam, lParam) -> LRESULT.
            # lParam is a pointer to KBDLLHOOKSTRUCT.
            # Both the WINFUNCTYPE factory and the KBDLLHOOKSTRUCT layout
            # are imported from win32_bindings (no inline ctypes.Structure
            # declaration here — keeps the ABI spec in one place).
            hook_proc = make_hook_proc_type()

            backend = self  # closure reference

            def _hook_proc(n_code, w_param, l_param):
                try:
                    if n_code == 0:  # HC_ACTION
                        ks = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        vk = ks.vkCode
                        is_caps = backend._is_caps_lock_hotkey
                        # Match the configured key. For main-key
                        # hotkeys (``backend._vk is not None``) this is
                        # ``vk == backend._vk`` AND modifiers held. For
                        # modifier-only hotkeys (``backend._vk is None``)
                        # this is ``vk in backend._modifier_vks_for_hook``.
                        if backend._vk is not None:
                            vk_matches = vk == backend._vk and backend._modifiers_pressed()
                        else:
                            vk_matches = vk in backend._modifier_vks_for_hook
                        # Key-down path
                        if w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN) and vk_matches:
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
                                # Dispatch to worker thread.
                                backend._enqueue_hook_callback(callback)
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
                                # Dispatch to worker thread.
                                backend._enqueue_hook_callback(callback)
                        # Key-up path
                        elif w_param in (_WM_KEYUP, _WM_SYSKEYUP) and vk_matches:
                            if is_caps:
                                # Caps Lock: fire the toggle exactly once on
                                # the physical key-up, and swallow the keyup
                                # so the OS sees no orphan key-up.
                                log.info("[HOTKEY FIRED] WH_KEYBOARD_LL Caps Lock key-up (toggle)")
                                # Dispatch to worker thread.
                                backend._enqueue_hook_callback(callback)
                                return 1  # swallow keyup
                            if backend._on_release_callback is not None:
                                # Push-to-talk: stop recording on release.
                                log.info(
                                    "[HOTKEY] Key released via WH_KEYBOARD_LL hook (vk=0x%X)",
                                    vk,
                                )
                                # Dispatch to worker thread.
                                backend._enqueue_hook_callback(backend._on_release_callback)
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
                                # Dispatch to worker thread.
                                backend._enqueue_hook_callback(callback)
                except Exception:
                    log.debug("[HOTKEY] LL hook proc error", exc_info=True)
                # Pass to the next hook so we don't break other hooks.
                return backend._user32.CallNextHookEx(backend._hook_handle or 0, n_code, w_param, l_param)

            self._hook_proc = hook_proc(_hook_proc)
            # Set argtypes for SetWindowsHookExW / UnhookWindowsHookEx /
            # CallNextHookEx — ABI declared in win32_bindings.
            set_low_level_hook_argtypes(self._user32, hook_proc)

            # hMod must be NULL for a low-level hook (it runs in this process).
            handle = self._user32.SetWindowsHookExW(_WHC_KEYBOARD_LL, self._hook_proc, 0, 0)
            if not handle:
                # previously this log line claimed "(GetLastError)"
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
           the release. If so, suppress the fire — the user was using a
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

    def stop(self) -> None:
        """Stop the hotkey listener.

        PERF- previously posted WM_QUIT to the polling thread
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

         (fix): early-return if already stopped so the
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
        # PERF- skip the useless PostThreadMessageW call on the
        # polling path — the polling loop checks _stop_event.is_set()
        # every 100ms.
        if self._thread is not None:
            self._thread.join(timeout=0.5)  # was 3.0; 100ms poll = 500ms is plenty
            self._thread = None
        # Tear down the LL hook callback worker thread. Push a
        # ``None`` sentinel so the worker exits cleanly.
        if self._hook_callback_thread is not None:
            with contextlib.suppress(queue.Full):
                self._hook_callback_queue.put_nowait(None)
            try:
                self._hook_callback_thread.join(timeout=1.0)
            except Exception:
                log.debug("[HOTKEY] Hook callback worker join failed", exc_info=True)
            self._hook_callback_thread = None

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
