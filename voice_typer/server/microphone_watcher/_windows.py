"""Microphone watcher — Windows WM_DEVICECHANGE implementation.

Provides :class:`_WindowsMixin` (mixed into ``MicrophoneDeviceWatcher``)
with the hidden-window ``GetMessageW`` pump and ``WM_QUIT`` posting.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class _WindowsMixin:
    def _run_windows(self) -> None:
        try:
            self._run_windows_impl()
        except Exception:
            log.warning(
                "[MIC-WATCHER] Windows watcher crashed (Win32/ctypes fault), falling back to TTL polling", exc_info=True
            )

    def _run_windows_impl(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
        except (ImportError, AttributeError):
            log.debug("[MIC-WATCHER] ctypes/wintypes unavailable, falling back to TTL polling")
            return

        wm_devicechange = 0x0219
        ws_ex_toolwindow = 0x00000080

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except AttributeError:
            log.debug("[MIC-WATCHER] windll unavailable (not Windows?), falling back to TTL polling")
            return

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
        user32.GetMessageW.restype = ctypes.c_ssize_t
        user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
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

        WNDPROC = ctypes.WINFUNCTYPE(  # noqa: N806
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
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
                ws_ex_toolwindow,
                class_name,
                "VoiceTyper Mic Watcher",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                h_instance,
                None,
            )
            if not hwnd:
                log.warning(
                    "[MIC-WATCHER] CreateWindowExW failed (err=%d), falling back to TTL polling",
                    ctypes.get_last_error(),
                )
                return

            log.info(
                "[MIC-WATCHER] Windows device-change watcher running (hwnd=%d)",
                hwnd,
            )
            self._windows_hwnd = hwnd

            msg = wintypes.MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    if ret == -1:
                        log.debug(
                            "[MIC-WATCHER] GetMessageW returned -1 (err=%d), exiting pump",
                            ctypes.get_last_error(),
                        )
                    return
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
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
        hwnd = getattr(self, "_windows_hwnd", None)
        if not hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes
        except (ImportError, AttributeError):
            log.debug("[MIC-WATCHER] ctypes/wintypes unavailable, cannot post WM_QUIT")
            return

        wm_quit = 0x0012
        try:
            user32 = ctypes.windll.user32
        except AttributeError:
            log.debug("[MIC-WATCHER] windll unavailable (not Windows?), cannot post WM_QUIT")
            return

        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        posted = user32.PostMessageW(hwnd, wm_quit, 0, 0)
        if not posted:
            log.debug(
                "[MIC-WATCHER] PostMessageW(WM_QUIT) returned 0 (hwnd=%d, err=%d) — pump may have already exited",
                int(hwnd) if hwnd else 0,
                ctypes.get_last_error(),
            )