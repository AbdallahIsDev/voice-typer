"""OS-level microphone device-change watcher.

PERF-MIC-001: invalidates the microphone list cache instantly when
a device is plugged/unplugged, instead of waiting up to 30s for
the TTL to expire.

Platform support
----------------
- **Windows**: ``WM_DEVICECHANGE`` via a hidden top-level window
  (created with ``WS_EX_TOOLWINDOW`` so it never appears in the
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
from typing import Any, Callable, Optional

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
    """

    def __init__(
        self,
        on_change: Callable[[], None],
        poll_interval: float = 1.0,
    ) -> None:
        self._on_change = on_change
        self._poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._platform = self._detect_platform()
        # Task 15: when on macOS and pyobjc is available, ``start()``
        # delegates to a ``CoreAudioMicrophoneWatcher`` (event-driven,
        # zero idle wakeups). Otherwise this stays ``None`` and the
        # polling thread is used. The selection is at runtime, not
        # import time, so importing this module never triggers a
        # pyobjc import.
        self._coreaudio_watcher: Optional[Any] = None

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
                "[MIC-WATCHER] Platform %s not supported, "
                "falling back to TTL polling",
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
                        "[MIC-WATCHER] CoreAudio watcher start failed, "
                        "falling back to sounddevice polling",
                        exc_info=True,
                    )
                    self._coreaudio_watcher = None
                else:
                    self._coreaudio_watcher = ca_watcher
                    log.info(
                        "[MIC-WATCHER] Using CoreAudio property-listener "
                        "watcher (event-driven)"
                    )
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

    def _try_create_coreaudio_watcher(self) -> Optional[Any]:
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
                "[MIC-WATCHER] microphone_watcher_coreaudio module "
                "unavailable, falling back to sounddevice polling"
            )
            return None
        try:
            return CoreAudioMicrophoneWatcher(
                self._on_change, poll_interval=self._poll_interval
            )
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
                "[MIC-WATCHER] Watcher thread did not exit within 2s "
                "(it is a daemon and will not block process exit)"
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
                "[MIC-WATCHER] Watcher thread crashed, "
                "falling back to TTL polling",
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
                    "[MIC-WATCHER] %s entries changed (%d -> %d), "
                    "invalidating cache",
                    snd_dir,
                    len(last_entries),
                    len(current),
                )
                last_entries = current
                self._invoke_callback()

    # ── macOS implementation ──────────────────────────────────────────

    def _run_macos(self) -> None:
        """Watch for CoreAudio device changes by polling ``sounddevice``.

        Uses ``sounddevice.query_devices()`` (which wraps PortAudio,
        which in turn talks to CoreAudio) to get the current device
        list every ``poll_interval`` seconds. When the device count
        changes, ``_invoke_callback()`` fires.

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
            log.debug(
                "[MIC-WATCHER] sounddevice not importable on macOS, "
                "falling back to TTL polling"
            )
            return

        # Capture the baseline device count. query_devices can raise
        # PortAudioError on a fresh boot before the audio HAL is
        # ready — treat that as "no baseline yet" so the first
        # successful poll doesn't spuriously fire a callback.
        try:
            last_count: Optional[int] = len(sd.query_devices())
        except Exception:
            last_count = None
            log.debug(
                "[MIC-WATCHER] initial sd.query_devices() failed, "
                "deferring baseline capture",
                exc_info=True,
            )

        log.debug(
            "[MIC-WATCHER] watching macOS device count (initial=%s)",
            last_count,
        )
        while not self._stop_event.wait(self._poll_interval):
            try:
                current_count = len(sd.query_devices())
            except Exception:
                # Transient PortAudio error — skip this cycle. The
                # TTL cache will refresh on the next list_microphones
                # call regardless.
                log.debug(
                    "[MIC-WATCHER] macOS poll failed, skipping cycle",
                    exc_info=True,
                )
                continue
            if last_count is not None and current_count != last_count:
                log.debug(
                    "[MIC-WATCHER] macOS device count changed (%d -> %d), "
                    "invalidating cache",
                    last_count,
                    current_count,
                )
                self._invoke_callback()
            last_count = current_count

    # ── Windows implementation ────────────────────────────────────────

    def _run_windows(self) -> None:
        """Watch for ``WM_DEVICECHANGE`` on a hidden top-level window.

        Uses ``ctypes`` to register a window class, create a hidden
        window (``WS_EX_TOOLWINDOW``, no ``WS_VISIBLE``), and pump
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
            log.warning("[MIC-WATCHER] Windows watcher crashed "
            "(Win32/ctypes fault), falling back to TTL polling", exc_info=True)
            return

    def _run_windows_impl(self) -> None:
        """Implementation of _run_windows — separated so _run_windows can catch exceptions."""
        try:
            import ctypes
            from ctypes import wintypes
        except (ImportError, AttributeError):
            log.debug(
                "[MIC-WATCHER] ctypes/wintypes unavailable, "
                "falling back to TTL polling"
            )
            return

        WM_DEVICECHANGE = 0x0219
        WM_QUIT = 0x0012
        PM_REMOVE = 1
        WS_EX_TOOLWINDOW = 0x00000080

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except AttributeError:
            log.debug(
                "[MIC-WATCHER] windll unavailable (not Windows?), "
                "falling back to TTL polling"
            )
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
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
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
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,  # LRESULT
            wintypes.HWND,     # hwnd
            wintypes.UINT,     # uMsg
            wintypes.WPARAM,   # wParam
            wintypes.LPARAM,   # lParam
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
            if msg == WM_DEVICECHANGE:
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
                "[MIC-WATCHER] RegisterClassExW failed (err=%d), "
                "falling back to TTL polling",
                ctypes.get_last_error(),
            )
            return

        hwnd = 0
        try:
            hwnd = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW,        # dwExStyle — no taskbar button
                class_name,              # lpClassName
                "VoiceTyper Mic Watcher",  # lpWindowName
                0,                       # dwStyle — not WS_VISIBLE
                0, 0, 0, 0,              # x, y, w, h
                None,                    # hWndParent — top-level (not HWND_MESSAGE)
                None,                    # hMenu
                h_instance,              # hInstance
                None,                    # lpParam
            )
            if not hwnd:
                log.warning(
                    "[MIC-WATCHER] CreateWindowExW failed (err=%d), "
                    "falling back to TTL polling",
                    ctypes.get_last_error(),
                )
                return

            log.info(
                "[MIC-WATCHER] Windows device-change watcher window created (hwnd=%d)",
                hwnd,
            )
            # Stash hwnd so stop() can post WM_QUIT to wake the pump.
            self._windows_hwnd = hwnd

            msg = wintypes.MSG()
            # PeekMessage pump: polls at ~10Hz so stop_event can
            # interrupt within ~100ms. PeekMessage with PM_REMOVE
            # is non-blocking, so the wait() between iterations is
            # what throttles the loop.
            while not self._stop_event.is_set():
                while user32.PeekMessageW(
                    ctypes.byref(msg), None, 0, 0, PM_REMOVE
                ):
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
                    log.debug(
                        "[MIC-WATCHER] DestroyWindow failed", exc_info=True
                    )
            try:
                user32.UnregisterClassW(class_name, h_instance)
            except Exception:
                log.debug(
                    "[MIC-WATCHER] UnregisterClassW failed", exc_info=True
                )

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

        WM_QUIT = 0x0012
        ctypes.windll.user32.PostMessageW(hwnd, WM_QUIT, 0, 0)

    # ── callback dispatch ─────────────────────────────────────────────

    def _invoke_callback(self) -> None:
        """Call ``_on_change`` and swallow exceptions.

        An exception in the invalidation callback must not kill the
        watcher thread — the next device change should still trigger
        an invalidation attempt. The 30s TTL cache is the ultimate
        backstop.
        """
        try:
            self._on_change()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Invalidation callback raised",
                exc_info=True,
            )
