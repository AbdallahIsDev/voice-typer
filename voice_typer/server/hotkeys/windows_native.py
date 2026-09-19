"""Windows native hotkey backend helpers."""

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
def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


class WindowsNativeHotkey(HotkeyBackend):
    """Hotkey backend using Win32 RegisterHotKey via ctypes."""

    def __init__(self, hotkey_str: str):
        super().__init__(hotkey_str)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()  # signalled when registration completes
        self._hotkey_id = 1  # arbitrary ID for RegisterHotKey
        self._registered = False
        # typed as Any, these are populated inside _register()
        self._user32: Any = None
        self._kernel32: Any = None
        self._success = False
        self._vk: int | None = None
        self._modifiers = 0
        self._using_polling = False  # True if falling back to GetAsyncKeyState
        # ESC-CANCEL-DELIVERY: handle for the WH_KEYBOARD_LL low-level hook
        self._hook_handle: Any = None
        self._hook_proc: Any = None
        # True when the hotkey is a modifier only
        self._is_modifier_only: bool = False
        # brief flag set while we're sending a
        self._caps_lock_suppressing: bool = False
        # loop's 8ms cadence (~125 Hz, see PERF-01/CPU-01) that's ~625
        self._last_ime_check_time: float = 0.0
        self._last_ime_composing: bool = False
        # PERF- throttled non-modifier key scan.
        self._last_nonmod_check_time: float = 0.0
        self._last_nonmod_pressed: bool = False
        # when True, ``start()`` prefers the event-driven WM_HOTKEY
        self._prefer_message_loop_first: bool = False
        # Initialize here (in __init__) rather than only inside
        self._last_error: int | None = None
        self._is_caps_lock_hotkey: bool = False
        # True when RegisterHotKey failed AND the low-level hook
        self._degraded_registration: bool = False
        # Dedicated worker thread + queue for LL hook callbacks.
        self._hook_callback_queue: queue.Queue[Callable[[], None] | None] = queue.Queue(maxsize=256)
        self._hook_callback_thread: threading.Thread | None = None
        # VK codes the LL hook matches when _vk is None
        self._modifier_vks_for_hook: list[int] = []

    # Strategy method bindings (implementations live in the
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

        # Without these, ctypes defaults to c_int which truncates 64-bit pointers.
        setup_main_argtypes(self._user32, self._kernel32)

        def run():
            """Hotkey thread: registers hotkey, runs polling loop."""
            try:
                # skip RegisterHotKey for
                if self._is_modifier_only:
                    log.info(
                        "[HOTKEY] Modifier-only hotkey (mods=0x%X), skipping "
                        "RegisterHotKey, using polling-only detection",
                        self._modifiers,
                    )
                else:
                    # Register the hotkey.  Pass NULL (0) as hWnd.

                    result = self._user32.RegisterHotKey(0, self._hotkey_id, _MOD_NOREPEAT | self._modifiers, self._vk)
                    if not result:
                        err = self._kernel32.GetLastError()
                        self._last_error = err
                        # ERROR_HOTKEY_ALREADY_REGISTERED (1409) is
                        if err == 1409:
                            log.warning(
                                "[HOTKEY] RegisterHotKey FAILED for VK=0x%X, "
                                "ERROR_HOTKEY_ALREADY_REGISTERED (1409). Another "
                                "app has claimed this hotkey. Check for: Snipping "
                                "Tool (Win+Shift+S), GeForce Overlay, AutoHotkey, "
                                "or other global-hotkey apps, OR rebind to a "
                                "different hotkey in Settings.",
                                self._vk,
                            )
                        else:
                            log.warning(
                                "RegisterHotKey failed for VK=0x%X, GetLastError=%d (0x%X), "
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
                self._success = True
                self._ready_event.set()

                # ESC-CANCEL-DELIVERY (regression fix): ESC is a *system key*
                is_caps_lock_hotkey = self._vk == _VK_CAPITAL
                # Store on the instance so the LL hook proc can suppress
                self._is_caps_lock_hotkey = is_caps_lock_hotkey
                # Proactively force Caps Lock OFF (mirrors the polling-loop
                if is_caps_lock_hotkey:
                    self._ensure_caps_lock_off()
                # 125Hz polling loop, burning CPU even when idle.
                simple_key = self._on_release_callback is None
                # Populate the per-instance modifier VK list so the
                self._modifier_vks_for_hook = self._compute_modifier_vks(self._modifiers)
                # when ``_prefer_message_loop_first`` is set (ESC and
                prefer_message_loop = self._prefer_message_loop_first and self._registered and not is_caps_lock_hotkey
                if prefer_message_loop:
                    # WM_HOTKEY message loop, event-driven, ~0% CPU
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WM_HOTKEY message loop "
                        "(prefer_message_loop=True, vk=0x%X, id=%d), skips LL hook",
                        self._vk,
                        self._hotkey_id,
                    )
                    self._using_polling = False
                    self._run_message_loop(callback, low_level_hook=False)
                elif simple_key and self._install_low_level_hook(callback):
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WH_KEYBOARD_LL "
                        "low-level hook (vk=0x%X), robust ESC/system-key delivery",
                        self._vk,
                    )
                    self._using_polling = False
                    # re-evaluate ``_success`` now that the LL hook
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
                    log.info(
                        "[HOTKEY] Starting hotkey detection via WM_HOTKEY message loop (registered VK=0x%X, id=%d)",
                        self._vk,
                        self._hotkey_id,
                    )
                    self._using_polling = False
                    self._run_message_loop(callback, low_level_hook=False)
                else:
                    # on all Windows configurations.  PERF-012 / PERF-01 / CPU-01: the
                    log.info("[HOTKEY] Starting hotkey detection via GetAsyncKeyState polling")
                    self._using_polling = True
                    # polling is also a valid delivery path, so
                    self._success = True
                    if not self._registered and not self._is_modifier_only:
                        self._degraded_registration = True
                        log.warning(
                            "[HOTKEY] Operating in degraded mode: RegisterHotKey "
                            "failed and WH_KEYBOARD_LL hook unavailable, relying on "
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
        """True when RegisterHotKey failed but the backend kept the"""
        return self._degraded_registration

    def stop(self) -> None:
        """Stop the hotkey listener."""
        if self._stop_event.is_set():
            return  # Already stopped, idempotent
        log.debug("[HOTKEY] Stopping %s listener", self.hotkey_str)
        self._stop_event.set()
        # ESC-CANCEL-DELIVERY: unblock a blocked GetMessageW on the
        on_message_loop = (
            (self._hook_handle is not None or self._registered) and self._thread is not None and self._thread.is_alive()
        )
        if on_message_loop:
            try:
                self._user32.PostThreadMessageW(self._thread.ident, _WM_QUIT, 0, 0)
            except Exception:
                log.debug("[HOTKEY] PostThreadMessageW(WM_QUIT) failed", exc_info=True)
        # Uninstall the low-level hook (if installed) so it stops
        if self._hook_handle is not None:
            try:
                self._user32.UnhookWindowsHookEx(self._hook_handle)
            except Exception:
                log.debug("[HOTKEY] UnhookWindowsHookEx failed", exc_info=True)
            self._hook_handle = None
            self._hook_proc = None
        # PERF- skip the useless PostThreadMessageW call on the
        if self._thread is not None:
            self._thread.join(timeout=0.5)  # was 3.0; 100ms poll = 500ms is plenty
            self._thread = None
        # Tear down the LL hook callback worker thread. Push a
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
