"""Windows-native hotkey backend (Win32 RegisterHotKey + GetAsyncKeyState polling).

This module is the slim class shell. The strategy implementations
live in the :mod:`voice_typer.server.hotkeys.windows` subpackage
(split out during the package split). Each strategy function takes ``self`` as its
first parameter so it can be assigned as a method on
:class:`WindowsNativeHotkey` — Python's descriptor protocol then
passes the instance as ``self``, and ``inspect.getsource`` follows
the function's ``__code__.co_filename`` back to the strategy module
(so source-inspection regression tests still pin the polling-loop
implementation in ``polling_strategy.py``).
"""

import contextlib
import queue
import threading
from collections.abc import Callable
from typing import Any

from voice_typer.server import hotkeys as _hotkeys_pkg

from .base import HotkeyBackend, log
from .win32_vk import (
    _MOD_NOREPEAT,
    _VK_CAPITAL,
    _WM_QUIT,
    parse_hotkey_to_win32,
)
from .windows.caps_lock_suppressor import ensure_caps_lock_off, suppress_caps_lock_toggle
from .windows.context import compute_modifier_vks, setup_main_argtypes
from .windows.ime_guard import is_ime_composing, is_ime_composing_throttled
from .windows.ll_hook_strategy import (
    enqueue_hook_callback,
    install_low_level_hook,
    start_hook_callback_worker,
)
from .windows.message_loop_strategy import run_message_loop
from .windows.polling_strategy import (
    any_non_modifier_key_pressed,
    any_non_modifier_key_pressed_throttled,
    is_altgr_pressed,
    key_pressed,
    modifiers_pressed,
    other_modifiers_pressed,
    run_modifier_only_polling_loop,
    run_polling_loop,
)


#  patch-target: tests patch
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

    (UAC / Secure Desktop limitation): global hotkeys registered
    via ``RegisterHotKey`` or a ``WH_KEYBOARD_LL`` hook do NOT fire
    while the active desktop is the Secure Desktop (UAC prompt, logon
    screen, or any desktop created by ``CreateDesktop``). The hook is
    desktop-relative: it only sees keystrokes delivered to the desktop
    that owns the hooking thread. When the user elevates via UAC, Windows
    switches to the Secure Desktop, our hooks stop receiving events, and
    the user has no way to trigger dictation (or cancel it) until they
    return. Detecting the switch via ``SetWinEventHook`` with
    ``EVENT_SYSTEM_DESKTOPSWITCH`` would let us notify the user, but the
    callback runs on a dedicated thread and integrating it into the
    existing message-loop architecture is non-trivial.

    TODOinstall a ``SetWinEventHook(EVENT_SYSTEM_DESKTOPSWITCH)``
    listener on a dedicated thread and, when the user returns to the
    interactive desktop, emit a tray notification: "Hotkey paused during
    UAC elevation". For now, this is a documented limitation — the user
    simply re-presses the hotkey after the UAC prompt closes. See
    ``docs/native-hotkey-architecture-plan.md`` for the full plan.
    """

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()  # signalled when registration completes
        self._hotkey_id = 1  # arbitrary ID for RegisterHotKey
        self._registered = False
        # typed as Any — these are populated inside _register()
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
        # True when the hotkey is a modifier only
        # spec (e.g. ``<alt>``). The polling loop uses a different code
        # path for these — see ``_run_modifier_only_polling_loop``.
        self._is_modifier_only: bool = False
        # brief flag set while we're sending a
        # synthetic Caps Lock keypress to undo the OS-level toggle.
        # The polling loop skips processing while this is set so the
        # synthetic events don't re-trigger the callback.
        self._caps_lock_suppressing: bool = False
        # PERF- throttled IME composition check. The underlying
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
        # PERF- throttled non-modifier key scan.
        # ``_any_non_modifier_key_pressed()`` calls GetAsyncKeyState for
        # each VK in 0x08-0xFF (248 codes) — O(248) per iteration. The
        # throttled wrapper re-scans at most every 50ms, reducing the
        # idle-state syscall rate from ~248k/sec to ~5k/sec.
        self._last_nonmod_check_time: float = 0.0
        self._last_nonmod_pressed: bool = False
        # when True, ``start()`` prefers the event-driven WM_HOTKEY
        # message loop over the per-keystroke WH_KEYBOARD_LL hook. Set by
        # ``HotkeyDispatcher`` on the ESC and repaste backends so the
        # main dictation hotkey is the ONLY backend installing a system-
        # wide LL hook (reduces per-keystroke system-wide CPU from 3× to
        # 1×). If RegisterHotKey fails for the ESC/repaste key (some keys
        # are reserved by the OS or already claimed), the backend falls
        # back to the LL hook for that single backend — 2 hooks instead
        # of 3, still an improvement. The main dictation hotkey leaves
        # this False (default) so it keeps the robust LL-hook-first path.
        self._prefer_message_loop_first: bool = False
        # Initialize here (in __init__) rather than only inside
        # ``start()`` so attribute access before ``start()`` (e.g. from
        # tests, diagnostics, or ``HotkeyDispatcher`` wiring) does not
        # raise ``AttributeError``. ``start()`` still resets these before
        # the registration attempt so the per-run semantics are unchanged.
        self._last_error: int | None = None
        self._is_caps_lock_hotkey: bool = False
        # True when RegisterHotKey failed AND the low-level hook
        # had to step in to keep the hotkey functional. Set in ``start()``
        # on the registration thread; readable via the
        # ``_registration_degraded`` property so ``_NativeBackendAdapter``
        # (and other callers) can surface a tray notification without
        # reaching into private attrs. The hook keeps the hotkey working,
        # but the OS-level exclusive claim failed — usually because another
        # app (Snipping Tool, GeForce Overlay, etc.) already claimed it.
        # The adapter owns the tray surface; this class only records the
        # state so it can be polled later (Fix-9 owns this file;
        # native_adapter.py and is left untouched).
        self._degraded_registration: bool = False
        # Dedicated worker thread + queue for LL hook callbacks.
        # The hook proc MUST return within ~1ms or Windows marks it
        # unresponsive and bypasses it. Previously callback() ran inline
        # (10-100ms of recorder/IPC work). Now the hook proc enqueues
        # and returns immediately; the worker drains the queue.
        self._hook_callback_queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=256)
        self._hook_callback_thread: threading.Thread | None = None
        # VK codes the LL hook matches when _vk is None
        # (modifier-only hotkeys). Populated in start().
        self._modifier_vks_for_hook: list[int] = []

    # ------------------------------------------------------------------ #
    # Strategy method bindings (implementations live in the
    # ``windows/`` subpackage; assigned here as class attributes so
    # Python's descriptor protocol passes the instance as ``self``).
    # ------------------------------------------------------------------ #
    _compute_modifier_vks = staticmethod(compute_modifier_vks)
    _is_ime_composing = staticmethod(is_ime_composing)
    _is_ime_composing_throttled = is_ime_composing_throttled
    _run_polling_loop = run_polling_loop
    _run_modifier_only_polling_loop = run_modifier_only_polling_loop
    _run_message_loop = run_message_loop
    _install_low_level_hook = install_low_level_hook
    _start_hook_callback_worker = start_hook_callback_worker
    _enqueue_hook_callback = enqueue_hook_callback
    _suppress_caps_lock_toggle = suppress_caps_lock_toggle
    _ensure_caps_lock_off = ensure_caps_lock_off
    _any_non_modifier_key_pressed = any_non_modifier_key_pressed
    _any_non_modifier_key_pressed_throttled = any_non_modifier_key_pressed_throttled
    _modifiers_pressed = modifiers_pressed
    _is_altgr_pressed = is_altgr_pressed
    _key_pressed = key_pressed
    _other_modifiers_pressed = other_modifiers_pressed

    def start(self, callback: Callable[[], None]) -> None:
        import ctypes
        import ctypes.wintypes

        parsed = parse_hotkey_to_win32(self.hotkey_str)
        if parsed is None:
            raise ValueError(f"Cannot parse hotkey {self.hotkey_str!r} to a VK code")
        self._vk, self._modifiers = parsed

        # detect modifier-only specs (e.g.
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
        self._degraded_registration = False
        self._last_error = None  # captured GetLastError() on failure

        # ── Set proper argtypes BEFORE any Win32 call ──
        # Without these, ctypes defaults to c_int which truncates 64-bit pointers.
        setup_main_argtypes(self._user32, self._kernel32)

        def run():
            """Hotkey thread: registers hotkey, runs polling loop."""
            try:
                # skip RegisterHotKey for
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
                        # ERROR_HOTKEY_ALREADY_REGISTERED (1409) is
                        # by far the most common cause of RegisterHotKey
                        # failure on a multi-app desktop. Log it explicitly
                        # so the user can identify the conflicting app
                        # (Snipping Tool, GeForce Overlay, AutoHotkey, etc.)
                        # and either close it or rebind to a different hotkey
                        # in Settings.
                        if err == 1409:
                            log.warning(
                                "[HOTKEY] RegisterHotKey FAILED for VK=0x%X — "
                                "ERROR_HOTKEY_ALREADY_REGISTERED (1409). Another "
                                "app has claimed this hotkey. Check for: Snipping "
                                "Tool (Win+Shift+S), GeForce Overlay, AutoHotkey, "
                                "or other global-hotkey apps, OR rebind to a "
                                "different hotkey in Settings.",
                                self._vk,
                            )
                        else:
                            log.warning(
                                "RegisterHotKey failed for VK=0x%X, GetLastError=%d (0x%X) — "
                                "polling fallback still works",
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

                # ``_success`` is set to True here as a transient
                # because the main thread checks it immediately after
                # ``_ready_event.wait()`` returns, BEFORE the detection
                # branch below has a chance to install the LL hook. The
                # polling fallback (the ``else`` branch below) ALWAYS
                # engages as the last resort, so reaching this point means
                # at least one detection path will run. Each detection
                # branch below sets the authoritative
                # ``_degraded_registration`` flag based on the actual path
                # engaged. The ``_registration_degraded`` property is the
                # authoritative signal for the adapter.
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
                    self._run_message_loop(callback, low_level_hook=False)
                elif simple_key and self._install_low_level_hook(callback):
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WH_KEYBOARD_LL "
                        "low-level hook (vk=0x%X) — robust ESC/system-key delivery",
                        self._vk,
                    )
                    self._using_polling = False
                    # re-evaluate ``_success`` now that the LL hook
                    # is installed. The hook is a fully functional delivery
                    # path, so ``start()`` must NOT raise even if
                    # RegisterHotKey failed. Also flip
                    # ``_degraded_registration`` when RegisterHotKey failed
                    # but the LL hook stepped in to keep the hotkey working
                    # — the adapter reads this via the property below and
                    # surfaces a tray notification.
                    self._success = True
                    if not self._registered:
                        self._degraded_registration = True
                        log.warning(
                            "[HOTKEY] Operating in degraded mode: RegisterHotKey "
                            "failed but WH_KEYBOARD_LL hook is keeping the hotkey "
                            "(vk=0x%X) functional. ``_NativeBackendAdapter`` should "
                            "surface a tray safety notification.",
                            self._vk,
                        )
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
                    # polling is also a valid delivery path, so
                    # ``_success`` is True here too — but flag degraded
                    # mode if RegisterHotKey failed AND the LL hook also
                    # couldn't be installed (worst-case fallback).
                    self._success = True
                    if not self._registered and not self._is_modifier_only:
                        self._degraded_registration = True
                        log.warning(
                            "[HOTKEY] Operating in degraded mode: RegisterHotKey "
                            "failed and WH_KEYBOARD_LL hook unavailable — relying on "
                            "GetAsyncKeyState polling (vk=0x%X).",
                            self._vk if self._vk is not None else -1,
                        )
                    self._run_polling_loop(callback)

            except Exception:
                log.exception("[HOTKEY] Windows hotkey thread error")
            finally:
                # Cleanup
                if self._registered:
                    self._user32.UnregisterHotKey(0, self._hotkey_id)
                    self._registered = False
                    log.debug("[HOTKEY] Unregistered %s", self.hotkey_str)

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

    @property
    def _registration_degraded(self) -> bool:
        """True when RegisterHotKey failed but the backend kept the
        hotkey functional via a fallback path (low-level hook or polling).

        Read by ``_NativeBackendAdapter`` (in native_adapter.py, so this file
        only exposes the property) so the adapter can surface a tray safety
        notification. The adapter is responsible for the user-facing surface;
        this class only records the state.
        """
        return self._degraded_registration

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
        # handle modifier-only hotkeys where
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
