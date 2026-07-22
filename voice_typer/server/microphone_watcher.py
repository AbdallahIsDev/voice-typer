"""OS-level microphone device-change watcher.

PERF-MIC-001: invalidates the microphone list cache instantly when
a device is plugged/unplugged, instead of waiting up to 30s for
the TTL to expire.

Platform support
----------------
- **Windows**: ``WM_DEVICECHANGE`` via a hidden top-level window
  (created with ``ws_ex_toolwindow`` so it never appears in the
  taskbar). A daemon thread runs a ``PeekMessage`` pump so the
  ``stop_event`` can interrupt it.
- **Linux**: polls ``/dev/snd`` directory listings at a configurable
  interval (default 1s) — lighter than PortAudio's 30s cache and
  doesn't require ``pyinotify`` as a dependency.
- **macOS**: prefers the event-driven CoreAudio property listener
  in :mod:`voice_typer.server.microphone_watcher_coreaudio`
  (``AudioObjectAddPropertyListener`` on
  ``kAudioHardwarePropertyDevices`` with a CFRunLoop on the watcher
  thread). Falls back to polling ``sounddevice.query_devices()``
  (which wraps CoreAudio) at the configured interval if
  ``pyobjc-framework-CoreAudio`` is not installed. The polling
  approach degrades to the 30s TTL cache if
  ``sounddevice``/PortAudio is unavailable.

The watcher runs in a daemon thread and calls the registered
invalidation callback when a device change is detected. The
existing 30s TTL cache in ``recording.py`` remains as a fallback —
if the watcher thread crashes or the platform is unsupported, the
TTL still refreshes the list every 30s.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


class MicrophoneDeviceWatcher:
    """Watches for microphone device changes and invalidates the cache.

    The watcher is intentionally best-effort: if the platform is
    unsupported, the thread fails to start, or the watcher thread
    crashes, the caller's 30s TTL cache (in ``recording.py``) still
    refreshes the device list. This class never raises from
    ``start()``/``stop()`` so a watcher failure cannot take down the
    recorder.

    Parameters
    ----------
    on_change:
        Zero-argument callback invoked (from the watcher thread) when
        a device change is detected. The callback is wrapped in a
        try/except so an exception in the callback does not kill the
        watcher thread.
    poll_interval:
        Seconds between ``/dev/snd`` directory polls on Linux.
        Defaults to 1.0s. Exposed as a parameter so tests can pass a
        smaller value for fast, deterministic verification.

    G4-M-41 — active-mic-lost detection
    -----------------------------------
    The watcher also exposes an OPTIONAL active-mic-lost hook so
    ``RecordingController`` can be notified when the microphone backing
    an in-flight recording is unplugged.  Three methods register the
    hook (all default to no-op if unset, preserving backward
    compatibility):

    - :meth:`set_active_mic_id` — set/clear the currently-active mic id
      (call with the mic id when a recording starts, ``None`` when it
      stops).
    - :meth:`set_on_active_mic_lost` — register the zero-arg callback
      to fire when the active mic disappears from the device list.
      The controller's implementation should cancel the recording and
      emit a tray notification.
    - :meth:`set_device_id_provider` — register a callable returning
      the current list of available mic ids.  Used by the watcher to
      detect "active mic gone" after a device-change event.

    The check runs inside :meth:`_invoke_callback` AFTER the cache-
    invalidation callback, so the provider sees a fresh device list.
    """

    def __init__(
        self,
        on_change: Callable[[], None],
        poll_interval: float = 1.0,
    ) -> None:
        self._on_change = on_change
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._platform = self._detect_platform()
        # Task 15: when on macOS and pyobjc is available, ``start()``
        # delegates to a ``CoreAudioMicrophoneWatcher`` (event-driven,
        # zero idle wakeups). Otherwise this stays ``None`` and the
        # polling thread is used. The selection is at runtime, not
        # import time, so importing this module never triggers a
        # pyobjc import.
        self._coreaudio_watcher: Any | None = None
        # G4-M-41: active-mic-lost detection.  ``RecordingController``
        # registers an ``_on_active_mic_lost`` callback (and the current
        # ``_active_mic_id`` plus a ``_device_id_provider`` callable)
        # during setup so that, when a device-change event fires AND the
        # active mic is no longer in the freshly-queried device list, the
        # watcher can tell the controller to cancel the in-flight
        # recording (instead of letting it stall on a dead input).
        # All three default to ``None`` — the watcher is fully
        # backward-compatible if no caller registers them.
        self._active_mic_id: Any | None = None
        self._on_active_mic_lost: Callable[[], None] | None = None
        self._device_id_provider: Callable[[], list[Any]] | None = None

    # ── platform detection ────────────────────────────────────────────

    def _detect_platform(self) -> str:
        """Return the current platform as one of ``windows``/``macos``/``linux``/``unknown``.

        Defers to ``voice_typer.server.platform_utils`` so all
        platform-detection logic lives in one place (CQ-029).
        """
        # Late import — platform_utils is cheap but late import keeps
        # the module importable in isolation for unit tests.
        from voice_typer.server.platform_utils import (
            is_linux,
            is_macos,
            is_windows,
        )

        if is_windows():
            return "windows"
        if is_macos():
            return "macos"
        if is_linux():
            return "linux"
        return "unknown"

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watcher thread (no-op on unsupported platforms).

        Idempotent: calling ``start()`` twice does not spawn a second
        thread.

        Task 15: on macOS, prefers the event-driven
        ``CoreAudioMicrophoneWatcher`` (zero idle wakeups) and falls
        back to the polling thread if pyobjc is unavailable. The
        CoreAudio import happens here (not at module load) so this
        module stays importable on non-macOS without pyobjc.
        """
        if self._thread is not None or self._coreaudio_watcher is not None:
            return
        if self._platform not in ("windows", "linux", "macos"):
            log.debug(
                "[MIC-WATCHER] Platform %s not supported, falling back to TTL polling",
                self._platform,
            )
            return

        # Task 15: macOS — try the native CoreAudio watcher first.
        if self._platform == "macos":
            ca_watcher = self._try_create_coreaudio_watcher()
            if ca_watcher is not None:
                try:
                    ca_watcher.start()
                except Exception:
                    # The CoreAudio watcher started but failed to
                    # register its listener — fall back to polling.
                    log.warning(
                        "[MIC-WATCHER] CoreAudio watcher start failed, falling back to sounddevice polling",
                        exc_info=True,
                    )
                    self._coreaudio_watcher = None
                else:
                    self._coreaudio_watcher = ca_watcher
                    log.info("[MIC-WATCHER] Using CoreAudio property-listener watcher (event-driven)")
                    return
            # else: pyobjc unavailable — fall through to polling.

        # Fallback: start the polling thread.
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="mic-device-watcher",
        )
        self._thread.start()
        log.info(
            "[MIC-WATCHER] Started device-change watcher for %s",
            self._platform,
        )

    def _try_create_coreaudio_watcher(self) -> Any | None:
        """Attempt to construct a ``CoreAudioMicrophoneWatcher``.

        Returns ``None`` if pyobjc is unavailable (so the caller falls
        back to polling). Returns the watcher instance (not yet
        started) on success. Any non-ImportError exception is logged
        and treated as "unavailable" — the polling watcher is the
        safe default.
        """
        # Late import — runtime selection so this module never imports
        # pyobjc at module load time (keeps Linux/Windows imports clean).
        try:
            from voice_typer.server.microphone_watcher_coreaudio import (
                CoreAudioMicrophoneWatcher,
            )
        except ImportError:
            log.debug(
                "[MIC-WATCHER] microphone_watcher_coreaudio module unavailable, falling back to sounddevice polling"
            )
            return None
        try:
            return CoreAudioMicrophoneWatcher(self._on_change, poll_interval=self._poll_interval)
        except ImportError:
            # _try_import_coreaudio raises ImportError on non-macOS or
            # when pyobjc-framework-CoreAudio is missing — this is the
            # expected "fall back to polling" signal.
            log.debug(
                "[MIC-WATCHER] CoreAudioMicrophoneWatcher unavailable "
                "(pyobjc not installed or not on macOS), falling back "
                "to sounddevice polling"
            )
            return None
        except Exception:
            log.warning(
                "[MIC-WATCHER] CoreAudioMicrophoneWatcher construction "
                "raised unexpectedly, falling back to sounddevice polling",
                exc_info=True,
            )
            return None

    def stop(self) -> None:
        """Signal the watcher thread to stop and join it (timeout 2s).

        Idempotent: calling ``stop()`` twice is safe. After ``stop()``
        returns, the watcher can be ``start()``-ed again (the internal
        thread reference is cleared).

        Task 15: if the CoreAudio watcher is active (macOS + pyobjc),
        delegates to its ``stop()`` instead of the polling thread's
        stop logic.
        """
        # Task 15: stop the CoreAudio watcher first if it's active.
        ca_watcher = self._coreaudio_watcher
        if ca_watcher is not None:
            try:
                ca_watcher.stop()
            except Exception:
                log.warning(
                    "[MIC-WATCHER] CoreAudio watcher stop failed",
                    exc_info=True,
                )
            self._coreaudio_watcher = None
            log.info("[MIC-WATCHER] Stopped CoreAudio watcher")
            return

        if self._thread is None:
            return
        self._stop_event.set()
        # Post a WM_QUIT to wake up a Windows message pump that might
        # be blocked in PeekMessage. On Linux this is a harmless no-op
        # because the pump uses _stop_event.wait(timeout).
        if self._platform == "windows":
            try:
                self._post_quit_to_windows()
            except Exception:
                # Best-effort — the 2s join timeout below is the
                # real backstop.
                log.debug("[MIC-WATCHER] WM_QUIT post failed", exc_info=True)
        self._thread.join(timeout=2.0)
        if self._thread.is_alive():
            log.warning(
                "[MIC-WATCHER] Watcher thread did not exit within 2s (it is a daemon and will not block process exit)"
            )
        self._thread = None
        log.info("[MIC-WATCHER] Stopped device-change watcher")

    # ── thread entry point ────────────────────────────────────────────

    def _run(self) -> None:
        """Thread target — dispatches to the platform-specific runner.

        Catches any exception so a watcher crash never propagates to
        the caller. The 30s TTL cache in ``recording.py`` covers the
        case where the watcher thread dies.
        """
        try:
            if self._platform == "windows":
                self._run_windows()
            elif self._platform == "linux":
                self._run_linux()
            elif self._platform == "macos":
                self._run_macos()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Watcher thread crashed, falling back to TTL polling",
                exc_info=True,
            )

    # ── Linux implementation ──────────────────────────────────────────

    def _run_linux(self) -> None:
        """Watch ``/dev/snd`` for changes by polling directory listing.

        Uses ``os.listdir`` + ``frozenset`` comparison at the
        configured poll interval. This deliberately avoids
        ``pyinotify``/``inotify_simple`` to keep the dependency
        surface minimal — PortAudio's 30s cache is the ultimate
        fallback if this loop misses an event.
        """
        import os

        snd_dir = "/dev/snd"
        if not os.path.isdir(snd_dir):
            log.debug(
                "[MIC-WATCHER] %s not found, falling back to TTL polling",
                snd_dir,
            )
            return

        try:
            last_entries = frozenset(os.listdir(snd_dir))
        except OSError as e:
            log.debug(
                "[MIC-WATCHER] cannot list %s (%s), falling back to TTL polling",
                snd_dir,
                e,
            )
            return

        log.debug(
            "[MIC-WATCHER] watching %s (%d entries)",
            snd_dir,
            len(last_entries),
        )
        while not self._stop_event.wait(self._poll_interval):
            try:
                current = frozenset(os.listdir(snd_dir))
            except OSError:
                # /dev/snd disappeared or became unreadable between
                # the isdir check and now — skip this cycle. The TTL
                # cache will refresh on the next list_microphones
                # call regardless.
                continue
            if current != last_entries:
                log.debug(
                    "[MIC-WATCHER] %s entries changed (%d -> %d), invalidating cache",
                    snd_dir,
                    len(last_entries),
                    len(current),
                )
                last_entries = current
                self._invoke_callback()

    # ── macOS implementation ────────────────────────────────────────

    @staticmethod
    def _device_signature(dev: Any) -> tuple:
        """MED-V / XPLAT-MAC-POLL-1: build a hashable signature for a
        sounddevice device entry.

        Comparing only ``len(sd.query_devices())`` misses same-count device
        swaps — e.g. a USB mic unplugged at the same moment a Bluetooth
        headset is plugged in (count stays at 3 → 3). The signature
        includes ``name``, ``hostapi``, and ``default_samplerate`` so any
        such swap is detected even when the count is unchanged.

        Uses ``dict.get`` so partial device entries (e.g. those returned
        by the unit-test mocks that only populate ``name``) do not raise
        ``KeyError``. ``None`` values are valid set members and compare
        equal only to themselves, so stable-but-partial entries still
        produce a stable signature across polls.
        """
        if not isinstance(dev, dict):
            # sounddevice normally returns dicts; fall back to a
            # sentinel that uniquely identifies each non-dict entry so
            # the set still reflects "something changed here".
            return (id(dev),)
        return (
            dev.get("name"),
            dev.get("hostapi"),
            dev.get("default_samplerate"),
        )

    def _run_macos(self) -> None:
        """Watch for CoreAudio device changes by polling ``sounddevice``.

        Uses ``sounddevice.query_devices()`` (which wraps PortAudio,
        which in turn talks to CoreAudio) to get the current device
        list every ``poll_interval`` seconds. When the device count
        changes, ``_invoke_callback()`` fires.

        MED-V / XPLAT-MAC-POLL-1: previously this method compared only
        the device COUNT. A same-count device swap (USB mic unplugged
        while a BT headset is plugged in) was missed and the cache was
        not invalidated. Now we also diff the
        ``(name, hostapi, default_samplerate)`` signature set so any
        same-count swap fires the callback.

        A property-listener approach via
        ``AudioObjectAddPropertyListener`` on
        ``kAudioHardwarePropertyDevices`` would be more elegant
        (event-driven, no polling), but it requires a running
        ``CFRunLoop`` on the watcher thread — which is awkward to
        integrate with the ``_stop_event`` shutdown pattern used by
        the Linux/Windows runners and hard to unit-test on non-mac
        CI. The polling approach is consistent with ``_run_linux``
        and degrades gracefully if ``sounddevice`` or PortAudio is
        unavailable.

        Falls back silently to the 30s TTL cache in ``recording.py``
        if ``sounddevice`` cannot be imported (e.g. PortAudio not
        installed) or if ``query_devices`` raises persistently —
        matching the Linux watcher's behavior when ``/dev/snd`` is
        missing.
        """
        try:
            import sounddevice as sd
        except ImportError:
            log.debug("[MIC-WATCHER] sounddevice not importable on macOS, falling back to TTL polling")
            return

        # Capture the baseline device count AND signature. query_devices
        # can raise PortAudioError on a fresh boot before the audio HAL
        # is ready — treat that as "no baseline yet" so the first
        # successful poll doesn't spuriously fire a callback.
        try:
            initial_devices = sd.query_devices()
            last_count: int | None = len(initial_devices)
            last_sig: set | None = {self._device_signature(d) for d in initial_devices}
        except Exception:
            last_count = None
            last_sig = None
            log.debug(
                "[MIC-WATCHER] initial sd.query_devices() failed, deferring baseline capture",
                exc_info=True,
            )

        log.debug(
            "[MIC-WATCHER] watching macOS device count (initial=%s)",
            last_count,
        )
        while not self._stop_event.wait(self._poll_interval):
            try:
                current_devices = sd.query_devices()
                current_count = len(current_devices)
                current_sig = {self._device_signature(d) for d in current_devices}
            except Exception:
                # Transient PortAudio error — skip this cycle. The
                # TTL cache will refresh on the next list_microphones
                # call regardless.
                log.debug(
                    "[MIC-WATCHER] macOS poll failed, skipping cycle",
                    exc_info=True,
                )
                continue
            # MED-V / XPLAT-MAC-POLL-1: fire on count OR signature
            # change. The signature check catches same-count device
            # swaps (e.g. USB mic unplugged + BT headset plugged in
            # simultaneously) that the count-only comparison missed.
            if (
                last_count is not None
                and last_sig is not None
                and (current_count != last_count or current_sig != last_sig)
            ):
                log.debug(
                    "[MIC-WATCHER] macOS device set changed (count %d -> %d), invalidating cache",
                    last_count,
                    current_count,
                )
                self._invoke_callback()
            last_count = current_count
            last_sig = current_sig

    # ── Windows implementation ────────────────────────────────────────

    def _run_windows(self) -> None:
        """Watch for ``WM_DEVICECHANGE`` on a hidden top-level window.

        Uses ``ctypes`` to register a window class, create a hidden
        window (``ws_ex_toolwindow``, no ``WS_VISIBLE``), and pump
        messages. A ``PeekMessage`` loop polls at 10Hz so the
        ``stop_event`` can interrupt the pump within ~100ms.

        Note: message-only windows (``HWND_MESSAGE`` parent) do NOT
        receive broadcast messages like ``WM_DEVICECHANGE`` per the
        Win32 docs, so we use a regular hidden top-level window
        instead.
        """
        # MIC-WATCHER-WIN32: Wrap the entire Windows path in a broad
        # try/except so an access violation (or any ctypes fault) logs
        # a clean warning and falls back to TTL polling instead of
        # crashing the process.
        try:
            self._run_windows_impl()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Windows watcher crashed (Win32/ctypes fault), falling back to TTL polling", exc_info=True
            )
            return

    def _run_windows_impl(self) -> None:
        """Implementation of _run_windows — separated so _run_windows can catch exceptions."""
        try:
            import ctypes
            from ctypes import wintypes
        except (ImportError, AttributeError):
            log.debug("[MIC-WATCHER] ctypes/wintypes unavailable, falling back to TTL polling")
            return

        wm_devicechange = 0x0219
        pm_remove = 1
        ws_ex_toolwindow = 0x00000080

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except AttributeError:
            log.debug("[MIC-WATCHER] windll unavailable (not Windows?), falling back to TTL polling")
            return

        # MIC-WATCHER-WIN32: Explicit argtypes/restype for 64-bit safety.
        # Without these, ctypes defaults to c_int restype which truncates
        # 64-bit HMODULE/HWND/LRESULT handles on 64-bit Windows — a primary
        # cause of access violations in ctypes code.
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        # LRESULT is LONG_PTR — c_ssize_t matches pointer width on
        # both 32-bit and 64-bit Windows.
        WNDPROC = ctypes.WINFUNCTYPE(  # noqa: N806
            ctypes.c_ssize_t,  # LRESULT
            wintypes.HWND,  # hwnd
            wintypes.UINT,  # uMsg
            wintypes.WPARAM,  # wParam
            wintypes.LPARAM,  # lParam
        )

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.DWORD),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == wm_devicechange:
                log.debug(
                    "[MIC-WATCHER] WM_DEVICECHANGE received (wparam=0x%x)",
                    wparam,
                )
                self._invoke_callback()
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        # MIC-WATCHER-WIN32: Keep a reference to the WNDPROC as an INSTANCE
        # attribute so the GC doesn't free it while the window is alive.
        # A local variable could be GC'd after the function returns, causing
        # Win32 to dispatch into freed memory → access violation.
        self._wnd_proc_ref = WNDPROC(_wnd_proc)
        wnd_proc_ref = self._wnd_proc_ref
        class_name = "VoiceTyperMicWatcherWnd"
        h_instance = kernel32.GetModuleHandleW(None)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = wnd_proc_ref
        wc.hInstance = h_instance
        wc.lpszClassName = class_name

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            log.warning(
                "[MIC-WATCHER] RegisterClassExW failed (err=%d), falling back to TTL polling",
                ctypes.get_last_error(),
            )
            return

        hwnd = 0
        try:
            hwnd = user32.CreateWindowExW(
                ws_ex_toolwindow,  # dwExStyle — no taskbar button
                class_name,  # lpClassName
                "VoiceTyper Mic Watcher",  # lpWindowName
                0,  # dwStyle — not WS_VISIBLE
                0,
                0,
                0,
                0,  # x, y, w, h
                None,  # hWndParent — top-level (not HWND_MESSAGE)
                None,  # hMenu
                h_instance,  # hInstance
                None,  # lpParam
            )
            if not hwnd:
                log.warning(
                    "[MIC-WATCHER] CreateWindowExW failed (err=%d), falling back to TTL polling",
                    ctypes.get_last_error(),
                )
                return

            log.info(
                "[MIC-WATCHER] Windows device-change watcher window created (hwnd=%d)",
                hwnd,
            )
            WM_QUIT = 0x0012  # noqa: N806
            # Stash hwnd so stop() can post WM_QUIT to wake the pump.
            self._windows_hwnd = hwnd

            msg = wintypes.MSG()
            # PeekMessage pump: polls at ~10Hz so stop_event can
            # interrupt within ~100ms. PeekMessage with pm_remove
            # is non-blocking, so the wait() between iterations is
            # what throttles the loop.
            while not self._stop_event.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, pm_remove):
                    if msg.message == WM_QUIT:
                        return
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                self._stop_event.wait(0.1)
        finally:
            self._windows_hwnd = None
            if hwnd:
                try:
                    user32.DestroyWindow(hwnd)
                except Exception:
                    log.debug("[MIC-WATCHER] DestroyWindow failed", exc_info=True)
            try:
                user32.UnregisterClassW(class_name, h_instance)
            except Exception:
                log.debug("[MIC-WATCHER] UnregisterClassW failed", exc_info=True)

    def _post_quit_to_windows(self) -> None:
        """Post ``WM_QUIT`` to the watcher window to wake the message pump.

        Called from ``stop()`` so the pump exits immediately instead
        of waiting up to 100ms for the next ``_stop_event.wait()``
        timeout. No-op if the window hasn't been created yet.
        """
        hwnd = getattr(self, "_windows_hwnd", None)
        if not hwnd:
            return
        import ctypes

        wm_quit = 0x0012
        ctypes.windll.user32.PostMessageW(hwnd, wm_quit, 0, 0)

    # ── callback dispatch ─────────────────────────────────────────────

    # MIC-WATCHER-DEDUP: debounce window.  Duplicate WM_DEVICECHANGE
    # messages (especially DBT_DEVNODES_CHANGED, which fires 18+ times
    # when a device driver re-enumerates) are suppressed within this
    # window so the device cache is invalidated at most once per burst.
    _DEBOUNCE_SECONDS = 0.5

    def _invoke_callback(self) -> None:
        """Call ``_on_change`` and swallow exceptions.

        An exception in the invalidation callback must not kill the
        watcher thread — the next device change should still trigger
        an invalidation attempt. The 30s TTL cache is the ultimate
        backstop.

        MIC-WATCHER-DEDUP: suppresses duplicate invocations within
        ``_DEBOUNCE_SECONDS`` (0.5s) of the last callback.  This
        prevents a burst of WM_DEVICECHANGE messages (e.g. 18
        duplicates of DBT_DEVNODES_CHANGED in 1 second) from
        invalidating the device cache 18 times in rapid succession.

        G4-M-41: after the cache-invalidation callback runs, the watcher
        also checks whether the active recording's mic_id is still
        present in the current device list (queried via the
        ``_device_id_provider`` registered by ``RecordingController``).
        If the active mic is gone, the ``_on_active_mic_lost`` callback
        fires so the controller can cancel the recording and emit a
        tray notification.  This check runs even if ``_on_change``
        raised — the recording must still be cancelled if its mic
        disappeared.  All three of ``_active_mic_id``,
        ``_on_active_mic_lost``, and ``_device_id_provider`` must be
        set for the check to run; otherwise it is a no-op.
        """
        import time

        now = time.monotonic()
        last = getattr(self, "_last_callback_time", 0.0)
        if now - last < self._DEBOUNCE_SECONDS:
            log.debug(
                "[MIC-WATCHER] Skipping duplicate invalidation (%.0fms since last)",
                (now - last) * 1000,
            )
            return
        self._last_callback_time = now

        try:
            self._on_change()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Invalidation callback raised",
                exc_info=True,
            )
        # G4-M-41: even if _on_change raised, the device list may have
        # changed in a way that removed the active mic — check anyway.
        self._check_active_mic_lost()

    # ── active-mic-lost detection (G4-M-41) ──────────────────────────

    def set_active_mic_id(self, mic_id: Any) -> None:
        """Set the mic_id of the currently-active recording (or clear it).

        Called by ``RecordingController`` when a recording starts (with
        the mic_id it selected) and again with ``None`` when the
        recording stops / is cancelled.  When the watcher detects a
        device-list change and the registered ``_device_id_provider``
        no longer returns ``mic_id``, the ``_on_active_mic_lost``
        callback fires.

        Passing ``None`` disables the check until the next recording
        starts — the watcher never fires ``_on_active_mic_lost`` while
        no recording is active.
        """
        self._active_mic_id = mic_id

    def set_on_active_mic_lost(self, callback: Callable[[], None]) -> None:
        """Register the callback to invoke when the active mic disappears.

        ``RecordingController`` should call this during setup::

            watcher.set_on_active_mic_lost(self._on_mic_lost)

        The callback receives no arguments; its implementation should
        call ``self.cancel()`` (or ``self.stop()`` with a
        "mic disconnected" message) and emit a tray notification.
        The callback is invoked from the watcher thread, so it must be
        thread-safe.  Exceptions raised by the callback are logged and
        swallowed (they must not kill the watcher thread).
        """
        self._on_active_mic_lost = callback

    def set_device_id_provider(self, provider: Callable[[], list[Any]]) -> None:
        """Register a callable that returns the current list of mic IDs.

        ``RecordingController`` should call this during setup::

            watcher.set_device_id_provider(
                lambda: [m["id"] for m in self._recorder.list_microphones()]
            )

        The provider is invoked once per device-change event (after the
        cache-invalidation callback runs) so the watcher can decide
        whether the active mic is still in the freshly-queried device
        list.  It must return a list/iterable of IDs in the same format
        as the ``mic_id`` passed to :meth:`set_active_mic_id` — the
        watcher uses ``in`` for membership, so IDs must be hashable.
        Exceptions raised by the provider are logged and swallowed.
        """
        self._device_id_provider = provider

    def _check_active_mic_lost(self) -> None:
        """Fire ``_on_active_mic_lost`` if the active mic is gone.

        No-op unless all three of ``_active_mic_id``,
        ``_on_active_mic_lost``, and ``_device_id_provider`` are set.
        This is intentional: the watcher is fully backward-compatible
        with callers that never register the active-mic-lost hooks
        (e.g. tests that only exercise the device-cache invalidation
        path).
        """
        if self._active_mic_id is None or self._on_active_mic_lost is None or self._device_id_provider is None:
            return
        try:
            current_ids = list(self._device_id_provider())
        except Exception:
            log.warning(
                "[MIC-WATCHER] device_id_provider raised; skipping active-mic-lost check this cycle",
                exc_info=True,
            )
            return
        if self._active_mic_id not in current_ids:
            log.info(
                "[MIC-WATCHER] Active mic %r no longer in device list "
                "(%d devices available) — firing on_active_mic_lost",
                self._active_mic_id,
                len(current_ids),
            )
            try:
                self._on_active_mic_lost()
            except Exception:
                log.warning(
                    "[MIC-WATCHER] on_active_mic_lost callback raised",
                    exc_info=True,
                )
