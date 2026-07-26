"""Win32 ctypes structures for reading exception info.

Per-platform guard: ``ctypes.wintypes`` is only imported on Windows.
On Linux/macOS, the struct classes are still defined (using
``ctypes.c_void_p`` / ``ctypes.c_ulonglong`` stand-ins where needed)
so that ``from voice_typer.server.crash_handler import _SYSTEMTIME``
works without an ``AttributeError`` — but the VEH callback that
dereferences these structs is never invoked on non-Windows (see
``_veh_callback._vectored_handler_impl``, which short-circuits when
``_ch._vectored_handler is None``).

Split out from the original monolithic ``crash_handler.py`` so the
structs can be imported independently of the kernel32 resolver and the
VEH callback.
"""

from __future__ import annotations

import ctypes
import sys

# Per-platform guard: ``ctypes.wintypes`` only exists on Windows.
# On Linux/macOS we use stand-in types so the struct classes can still
# be defined (the VEH callback is never invoked there). This keeps
# Linux imports cheap and avoids ``AttributeError`` at module-load time.
if sys.platform == "win32":
    from ctypes import wintypes

    _DWORD = wintypes.DWORD
    _WORD = wintypes.WORD
else:
    # Stand-ins for non-Windows: same ctypes widths so the struct
    # ``_fields_`` layout matches (in case any test inspects it).
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
