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
- **macOS**: not implemented (falls back to the 30s TTL polling in
  ``recording.py``). A future iteration can wire up CoreAudio's
  ``kAudioDevicePropertyListenerProc`` via ``pyobjc``.

The watcher runs in a daemon thread and calls the registered
invalidation callback when a device change is detected. The
existing 30s TTL cache in ``recording.py`` remains as a fallback —
if the watcher thread crashes or the platform is unsupported, the
TTL still refreshes the list every 30s.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

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
        """
        if self._thread is not None:
            return
        if self._platform not in ("windows", "linux"):
            log.debug(
                "[MIC-WATCHER] Platform %s not supported, "
                "falling back to TTL polling",
                self._platform,
            )
            return
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

    def stop(self) -> None:
        """Signal the watcher thread to stop and join it (timeout 2s).

        Idempotent: calling ``stop()`` twice is safe. After ``stop()``
        returns, the watcher can be ``start()``-ed again (the internal
        thread reference is cleared).
        """
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

        # Keep a reference to the WNDPROC so the GC doesn't free it
        # while the window is alive (which would crash the message
        # pump on the next dispatch).
        wnd_proc_ref = WNDPROC(_wnd_proc)
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
