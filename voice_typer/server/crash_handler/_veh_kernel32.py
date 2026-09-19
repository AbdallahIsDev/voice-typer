"""Kernel32 function-pointer resolver for the VEH callback."""

from __future__ import annotations

import ctypes


def _ensure_kernel32() -> None:
    """Resolve kernel32 function pointers once. Idempotent."""
    from voice_typer.server import crash_handler as _ch

    if _ch._kernel32 is not None:
        return

    # Per-platform guard: ``ctypes.windll`` and ``ctypes.wintypes`` only
    from ctypes import wintypes

    from voice_typer.server.crash_handler._win32_structs import _SYSTEMTIME

    _kernel32 = ctypes.windll.kernel32
    _ch._kernel32 = _kernel32

    _func_get_current_process_id = _kernel32.GetCurrentProcessId
    _func_get_current_process_id.argtypes = []
    _func_get_current_process_id.restype = wintypes.DWORD
    _ch._func_get_current_process_id = _func_get_current_process_id

    _func_get_current_thread_id = _kernel32.GetCurrentThreadId
    _func_get_current_thread_id.argtypes = []
    _func_get_current_thread_id.restype = wintypes.DWORD
    _ch._func_get_current_thread_id = _func_get_current_thread_id

    _func_get_system_time_as_file_time = _kernel32.GetSystemTimeAsFileTime
    _func_get_system_time_as_file_time.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    _func_get_system_time_as_file_time.restype = None
    _ch._func_get_system_time_as_file_time = _func_get_system_time_as_file_time

    _func_file_time_to_system_time = _kernel32.FileTimeToSystemTime
    _func_file_time_to_system_time.argtypes = [
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(_SYSTEMTIME),
    ]
    _func_file_time_to_system_time.restype = wintypes.BOOL
    _ch._func_file_time_to_system_time = _func_file_time_to_system_time

    _func_create_file_w = _kernel32.CreateFileW
    _func_create_file_w.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _func_create_file_w.restype = wintypes.HANDLE
    _ch._func_create_file_w = _func_create_file_w

    _func_write_file = _kernel32.WriteFile
    _func_write_file.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _func_write_file.restype = wintypes.BOOL
    _ch._func_write_file = _func_write_file

    _func_set_file_pointer = _kernel32.SetFilePointer
    _func_set_file_pointer.argtypes = [
        wintypes.HANDLE,
        wintypes.LONG,
        wintypes.DWORD,
    ]
    _func_set_file_pointer.restype = wintypes.DWORD
    _ch._func_set_file_pointer = _func_set_file_pointer

    _func_close_handle = _kernel32.CloseHandle
    _func_close_handle.argtypes = [wintypes.HANDLE]
    _func_close_handle.restype = wintypes.BOOL
    _ch._func_close_handle = _func_close_handle

    _func_delete_file_w = _kernel32.DeleteFileW
    _func_delete_file_w.argtypes = [wintypes.LPCWSTR]
    _func_delete_file_w.restype = wintypes.BOOL
    _ch._func_delete_file_w = _func_delete_file_w
