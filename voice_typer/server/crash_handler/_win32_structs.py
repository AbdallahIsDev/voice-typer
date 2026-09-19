"""Win32 ctypes structures for reading exception info."""

from __future__ import annotations

import ctypes
import sys

# Per-platform guard: ``ctypes.wintypes`` only exists on Windows.
if sys.platform == "win32":
    from ctypes import wintypes

    _DWORD = wintypes.DWORD
    _WORD = wintypes.WORD
else:
    # Stand-ins for non-Windows: same ctypes widths so the struct
    _DWORD = ctypes.c_uint32
    _WORD = ctypes.c_uint16


class _ExceptionRecord(ctypes.Structure):
    """Win32 ``EXCEPTION_RECORD`` struct (subset of fields we read)."""

    _fields_ = [
        ("ExceptionCode", _DWORD),
        ("ExceptionFlags", _DWORD),
        ("ExceptionRecord", ctypes.c_void_p),
        ("ExceptionAddress", ctypes.c_void_p),
        ("NumberParameters", _DWORD),
        ("ExceptionInformation", ctypes.c_ulonglong * 15),
    ]


class _ExceptionPointers(ctypes.Structure):
    """Win32 ``EXCEPTION_POINTERS`` struct."""

    _fields_ = [
        ("ExceptionRecord", ctypes.POINTER(_ExceptionRecord)),
        ("ContextRecord", ctypes.c_void_p),
    ]


class _SYSTEMTIME(ctypes.Structure):
    """Win32 ``SYSTEMTIME`` struct (used for timestamp formatting)."""

    _fields_ = [
        ("wYear", _WORD),
        ("wMonth", _WORD),
        ("wDayOfWeek", _WORD),
        ("wDay", _WORD),
        ("wHour", _WORD),
        ("wMinute", _WORD),
        ("wSecond", _WORD),
        ("wMilliseconds", _WORD),
    ]
