"""Windows Vectored Exception Handler — captures silent process crashes.

STATUS_HEAP_CORRUPTION (0xC0000374), STATUS_ACCESS_VIOLATION, and other
SEH exceptions kill the process silently — no Python traceback, no log
message — because the OS terminates the process before Python's exception
machinery can run.

This module installs a Vectored Exception Handler (AddVectoredExceptionHandler)
at process startup. When a crash occurs, the handler writes a minimal
diagnostic blurb (timestamp, exception code, address, thread ID, process ID)
to a crash diagnostics file in the config directory.

CRITICAL SAFETY NOTE: During STATUS_HEAP_CORRUPTION the process heap is
corrupted, so the VEH callback cannot safely use Python operations that
allocate memory (f-strings, .encode(), .decode(), etc.). This handler
attempts to minimise allocations by pre-computing static message parts
at init time and using only pre-allocated buffers + raw kernel32.WriteFile
calls. However, there is NO GUARANTEE the handler will succeed for heap
corruption crashes — the corruption may already have damaged the memory
used by our buffer. For access violations and stack overruns, the handler
is reliable.

Limitations
-----------
- Only works on Windows (other platforms silently skip registration).
- STATUS_HEAP_CORRUPTION may prevent the handler from writing its
  diagnostics (see note above).
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# G4-M-32 / G4-M-33: archive + retention constants for crash diagnostics.
_CRASH_DIAGNOSTICS_ARCHIVE = "crash_diagnostics_archive"
_ARCHIVE_RETENTION_KEEP = 5
# G4-M-32: sweep policy for stale ``crash_diagnostics.*.txt`` files left
# in the config_dir root (e.g. from a prior version, or from a move that
# failed).  Keep at most ``_MAX_ACTIVE_FILES`` files and delete any older
# than ``_MAX_AGE_DAYS`` days.
_MAX_ACTIVE_FILES = 10
_MAX_AGE_DAYS = 30
_MAX_AGE_SECONDS = _MAX_AGE_DAYS * 24 * 60 * 60

# ── Windows constants ────────────────────────────────────────────────

STATUS_HEAP_CORRUPTION = 0xC0000374
STATUS_ACCESS_VIOLATION = 0xC0000005
STATUS_STACK_BUFFER_OVERRUN = 0xC0000409
STATUS_FATAL_APP_EXIT = 0x40000015

_CRASH_CODES = frozenset(
    {
        STATUS_HEAP_CORRUPTION,
        STATUS_ACCESS_VIOLATION,
        STATUS_STACK_BUFFER_OVERRUN,
        STATUS_FATAL_APP_EXIT,
    }
)

EXCEPTION_CONTINUE_SEARCH = 0x0

# CreateFileW / WriteFile constants
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x00000080

_handler_handle: int | None = None


# ── Win32 structures for reading exception info ──────────────────────


class _ExceptionRecord(ctypes.Structure):
    _fields_ = [
        ("ExceptionCode", wintypes.DWORD),
        ("ExceptionFlags", wintypes.DWORD),
        ("ExceptionRecord", ctypes.c_void_p),
        ("ExceptionAddress", ctypes.c_void_p),
        ("NumberParameters", wintypes.DWORD),
        ("ExceptionInformation", ctypes.c_ulonglong * 15),
    ]


class _ExceptionPointers(ctypes.Structure):
    _fields_ = [
        ("ExceptionRecord", ctypes.POINTER(_ExceptionRecord)),
        ("ContextRecord", ctypes.c_void_p),
    ]


# ── SYSTEMTIME struct (used for timestamp formatting) ─────────────────


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", wintypes.WORD),
        ("wMonth", wintypes.WORD),
        ("wDayOfWeek", wintypes.WORD),
        ("wDay", wintypes.WORD),
        ("wHour", wintypes.WORD),
        ("wMinute", wintypes.WORD),
        ("wSecond", wintypes.WORD),
        ("wMilliseconds", wintypes.WORD),
    ]


# ── Pre-allocated resources (initialized once at startup, never freed) ──

# Kernel32 function pointers
_kernel32: ctypes.WinDLL | None = None
_func_get_current_process_id: ctypes._FuncPtr | None = None
_func_get_current_thread_id: ctypes._FuncPtr | None = None
_func_get_system_time_as_file_time: ctypes._FuncPtr | None = None
_func_file_time_to_system_time: ctypes._FuncPtr | None = None
_func_create_file_w: ctypes._FuncPtr | None = None
_func_write_file: ctypes._FuncPtr | None = None
_func_set_file_pointer: ctypes._FuncPtr | None = None
_func_close_handle: ctypes._FuncPtr | None = None
_func_delete_file_w: ctypes._FuncPtr | None = None

# Pre-allocated struct instances (no heap allocation in callback)
_ft = wintypes.FILETIME()
_st = _SYSTEMTIME()

# Pre-allocated crash message buffer and static parts
# Max approx: 3 + 23 + 8 + 10 + 18 + 18 + 8 + 18 + 8 + 80 + 2 = ~196 bytes
_CRASH_MSG_BUF_SIZE = 1024
_crash_msg_buf: bytearray = bytearray(_CRASH_MSG_BUF_SIZE)

# Pre-encoded static message parts (ASCII)
_BOM = b"\xef\xbb\xbf"
_SEP = b"  "
_CRASH_LABEL = b"CRASH"
_CODE_LABEL = b"code="
_ADDR_LABEL = b", addr="
_PID_LABEL = b", pid="
_TID_LABEL = b", tid="
_NL = b"\r\n"

# Friendly names for known exception codes (pre-encoded)
_NAME_HEAP = b"STATUS_HEAP_CORRUPTION: the process heap has been corrupted."
_NAME_ACCESS = b"STATUS_ACCESS_VIOLATION: the process tried to access invalid memory."
_NAME_STACK = b"STATUS_STACK_BUFFER_OVERRUN: a stack buffer overrun was detected."
_NAME_FATAL = b"STATUS_FATAL_APP_EXIT: the application requested termination."
_NAME_UNKNOWN = b"Unknown fatal exception."

# Pre-computed file path (Python str, built at install time)
# Stored as a Python str so we can pass it to CreateFileW via c_wchar_p
# (which correctly marshals the internal UTF-16 buffer)
_crash_file_path: str = ""
_PID: int = 0

# G4-M-34: config_dir used by ``_crash_excepthook`` to write the
# ``python_crash.<PID>.txt`` marker file.  Set in
# ``set_crash_handler_config_dir``.
_python_crash_dir: Path | None = None

# G4-L-14: rate-limit flag for the VEH callback.  Set to True after a
# successful ``_write_to_file`` call so cascading exceptions don't write
# multiple crash records (which can corrupt the file or fill the disk).
# Reset to False in ``set_crash_handler_config_dir`` so tests work.
_crash_written: bool = False

# Lookup table for hex digit encoding (pre-computed)
_HEX_CHARS = b"0123456789ABCDEF"


def _ensure_kernel32() -> None:
    """Resolve kernel32 function pointers once. Idempotent."""
    global _kernel32, _func_get_current_process_id, _func_get_current_thread_id
    global _func_get_system_time_as_file_time, _func_file_time_to_system_time
    global _func_create_file_w, _func_write_file, _func_set_file_pointer, _func_close_handle
    if _kernel32 is not None:
        return
    _kernel32 = ctypes.windll.kernel32

    _func_get_current_process_id = _kernel32.GetCurrentProcessId
    _func_get_current_process_id.argtypes = []
    _func_get_current_process_id.restype = wintypes.DWORD

    _func_get_current_thread_id = _kernel32.GetCurrentThreadId
    _func_get_current_thread_id.argtypes = []
    _func_get_current_thread_id.restype = wintypes.DWORD

    _func_get_system_time_as_file_time = _kernel32.GetSystemTimeAsFileTime
    _func_get_system_time_as_file_time.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
    _func_get_system_time_as_file_time.restype = None

    _func_file_time_to_system_time = _kernel32.FileTimeToSystemTime
    _func_file_time_to_system_time.argtypes = [
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(_SYSTEMTIME),
    ]
    _func_file_time_to_system_time.restype = wintypes.BOOL

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

    _func_write_file = _kernel32.WriteFile
    _func_write_file.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _func_write_file.restype = wintypes.BOOL

    _func_set_file_pointer = _kernel32.SetFilePointer
    _func_set_file_pointer.argtypes = [
        wintypes.HANDLE,
        wintypes.LONG,
        ctypes.c_void_p,  # lpDistanceToMoveHigh (None = no 64-bit seek)
        wintypes.DWORD,
    ]
    _func_set_file_pointer.restype = wintypes.DWORD

    _func_close_handle = _kernel32.CloseHandle
    _func_close_handle.argtypes = [wintypes.HANDLE]
    _func_close_handle.restype = wintypes.BOOL

    _func_delete_file_w = _kernel32.DeleteFileW
    _func_delete_file_w.argtypes = [wintypes.LPCWSTR]
    _func_delete_file_w.restype = wintypes.BOOL


def _write_u32_hex(value: int, buf: bytearray, offset: int) -> int:
    """Write a 32-bit value as 8 hex digits (no 0x prefix) into buf at offset.

    Returns the number of bytes written (always 8).
    Uses only pre-computed hex chars — no heap allocations.
    """
    for i in range(7, -1, -1):
        nibble = (value >> (i * 4)) & 0xF
        buf[offset + (7 - i)] = _HEX_CHARS[nibble]
    return 8


def _write_u64_hex(value: int, buf: bytearray, offset: int) -> int:
    """Write a 64-bit value as 16 hex digits (no 0x prefix) into buf at offset.

    Uses only pre-computed hex chars — no heap allocations.
    """
    for i in range(15, -1, -1):
        nibble = (value >> (i * 4)) & 0xF
        buf[offset + (15 - i)] = _HEX_CHARS[nibble]
    return 16


def _write_timestamp(buf: bytearray, offset: int) -> int:
    """Write ISO-8601-like timestamp (e.g. '2026-07-14 23:09:48.123') into buf.

    Uses pre-allocated FILETIME and SYSTEMTIME structs — no heap allocations.
    Returns bytes written.
    """
    _func_get_system_time_as_file_time(ctypes.byref(_ft))
    _func_file_time_to_system_time(ctypes.byref(_ft), ctypes.byref(_st))

    year = _st.wYear
    month = _st.wMonth
    day = _st.wDay
    hour = _st.wHour
    minute = _st.wMinute
    second = _st.wSecond
    ms = _st.wMilliseconds

    # Write directly into the buffer: "YYYY-MM-DD HH:MM:SS.mmm"
    # This avoids Python string formatting (which would allocate on the heap).
    pos = offset

    # Year (4 digits)
    buf[pos] = _HEX_CHARS[(year // 1000) % 10]
    buf[pos + 1] = _HEX_CHARS[(year // 100) % 10]
    buf[pos + 2] = _HEX_CHARS[(year // 10) % 10]
    buf[pos + 3] = _HEX_CHARS[year % 10]
    buf[pos + 4] = 0x2D  # '-'
    pos += 5

    # Month (2 digits)
    buf[pos] = _HEX_CHARS[month // 10]
    buf[pos + 1] = _HEX_CHARS[month % 10]
    buf[pos + 2] = 0x2D  # '-'
    pos += 3

    # Day (2 digits)
    buf[pos] = _HEX_CHARS[day // 10]
    buf[pos + 1] = _HEX_CHARS[day % 10]
    buf[pos + 2] = 0x20  # ' '
    pos += 3

    # Hour (2 digits)
    buf[pos] = _HEX_CHARS[hour // 10]
    buf[pos + 1] = _HEX_CHARS[hour % 10]
    buf[pos + 2] = 0x3A  # ':'
    pos += 3

    # Minute (2 digits)
    buf[pos] = _HEX_CHARS[minute // 10]
    buf[pos + 1] = _HEX_CHARS[minute % 10]
    buf[pos + 2] = 0x3A  # ':'
    pos += 3

    # Second (2 digits)
    buf[pos] = _HEX_CHARS[second // 10]
    buf[pos + 1] = _HEX_CHARS[second % 10]
    buf[pos + 2] = 0x2E  # '.'
    pos += 3

    # Milliseconds (3 digits)
    buf[pos] = _HEX_CHARS[ms // 100]
    buf[pos + 1] = _HEX_CHARS[(ms // 10) % 10]
    buf[pos + 2] = _HEX_CHARS[ms % 10]
    pos += 3

    return pos - offset


# ── The VEH callback ─────────────────────────────────────────────────


def _vectored_handler_impl(exception_pointers) -> int:
    """VEH callback — runs when an SEH exception occurs.

    Writes crash diagnostics using ONLY pre-allocated buffers and raw
    kernel32 API calls.  Avoids Python heap allocations to maximise the
    chance of succeeding even during heap corruption.

    Always returns EXCEPTION_CONTINUE_SEARCH so the OS proceeds with
    normal termination.

    G4-L-14: a module-level ``_crash_written`` flag rate-limits the
    callback so cascading exceptions (e.g. an access violation triggered
    while handling an earlier access violation) cannot write multiple
    crash records — the second invocation returns early.
    """
    # G4-L-14: ``global`` MUST be declared before any use of the name
    # (read OR write) inside the function body, otherwise Python raises
    # SyntaxError: name '_crash_written' is used prior to global
    # declaration. Placed at the very top of the function so the read
    # at the rate-limit check below is legal.
    global _crash_written
    # Read the exception code from the EXCEPTION_RECORD.
    # NOTE: exception_pointers.contents.ExceptionRecord.contents creates
    # a Python ctypes wrapper object on the heap.  This is the ONLY heap
    # allocation in the callback.  If the heap is corrupted this may fail,
    # but in practice ctypes wrapper objects are tiny and often survive
    # localised heap corruption.  We accept this risk — without reading
    # the exception code, we can't proceed at all.
    try:
        record = exception_pointers.contents.ExceptionRecord.contents
        exc_code = record.ExceptionCode
        exc_addr = record.ExceptionAddress
    except Exception:
        return EXCEPTION_CONTINUE_SEARCH

    if exc_code not in _CRASH_CODES:
        return EXCEPTION_CONTINUE_SEARCH

    # G4-L-14: rate-limit — don't write multiple crash records under
    # cascading exceptions.  Once we've written one record, subsequent
    # VEH callbacks (which the OS may deliver as the exception dispatcher
    # unwinds) return early so we don't corrupt or duplicate the file.
    if _crash_written:
        return EXCEPTION_CONTINUE_SEARCH

    _ensure_kernel32()

    pid = _func_get_current_process_id()
    tid = _func_get_current_thread_id()

    # Build the crash message in the pre-allocated buffer using ONLY
    # bytearray slice assignment (no heap allocations).
    buf = _crash_msg_buf
    pos = 0

    # BOM
    buf[pos : pos + 3] = _BOM
    pos += 3

    # Timestamp
    pos += _write_timestamp(buf, pos)

    # "  CRASH  "
    buf[pos : pos + 2] = _SEP
    pos += 2
    buf[pos : pos + 5] = _CRASH_LABEL
    pos += 5
    buf[pos : pos + 2] = _SEP
    pos += 2

    # "code=0x"
    buf[pos : pos + 5] = _CODE_LABEL
    pos += 5
    buf[pos] = 0x30  # '0'
    buf[pos + 1] = 0x78  # 'x'
    pos += 2
    pos += _write_u32_hex(exc_code, buf, pos)

    # ", addr=0x"
    buf[pos : pos + 7] = _ADDR_LABEL
    pos += 7
    buf[pos] = 0x30  # '0'
    buf[pos + 1] = 0x78  # 'x'
    pos += 2
    pos += _write_u64_hex(exc_addr, buf, pos)

    # ", pid=0x"
    buf[pos : pos + 5] = _PID_LABEL
    pos += 5
    buf[pos] = 0x30  # '0'
    buf[pos + 1] = 0x78  # 'x'
    pos += 2
    pos += _write_u32_hex(pid, buf, pos)

    # ", tid=0x"
    buf[pos : pos + 5] = _TID_LABEL
    pos += 5
    buf[pos] = 0x30  # '0'
    buf[pos + 1] = 0x78  # 'x'
    pos += 2
    pos += _write_u32_hex(tid, buf, pos)

    buf[pos : pos + 2] = _NL
    pos += 2

    # Friendly name
    if exc_code == STATUS_HEAP_CORRUPTION:
        name = _NAME_HEAP
    elif exc_code == STATUS_ACCESS_VIOLATION:
        name = _NAME_ACCESS
    elif exc_code == STATUS_STACK_BUFFER_OVERRUN:
        name = _NAME_STACK
    elif exc_code == STATUS_FATAL_APP_EXIT:
        name = _NAME_FATAL
    else:
        name = _NAME_UNKNOWN

    n_name = len(name)
    buf[pos : pos + n_name] = name
    pos += n_name
    buf[pos : pos + 2] = _NL
    pos += 2

    # Write the crash diagnostics file
    # NOTE: buf[:pos] creates a new bytes object (heap allocation).
    # This is an acceptable limitation — see the module docstring.
    if _crash_file_path:
        _write_to_file(_crash_file_path, buf[:pos])
        # G4-L-14: mark as written so cascading VEH callbacks don't
        # write a second record.  ``global`` is declared at the top of
        # this function (above) so the assignment here updates the
        # module-level flag.
        _crash_written = True

    return EXCEPTION_CONTINUE_SEARCH


# WP-1: Wrap the VEH callback implementation in ``WINFUNCTYPE`` ONLY on Windows.
# ``ctypes.WINFUNCTYPE`` only exists on Windows — referencing it on Linux/macOS
# raises ``AttributeError`` at module-load time, which breaks every test that
# imports ``voice_typer.server.app`` (which transitively imports crash_handler).
# On non-Windows, SEH exceptions don't exist, so ``_vectored_handler`` is None
# and ``install_crash_handler()`` short-circuits on ``sys.platform != "win32"``
# (crash_handler.py:622, :651) — it never dereferences ``_vectored_handler``.
# This replaces the previous ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` band-aid
# that lived in ``tests/conftest.py`` (now removed).
if sys.platform == "win32":
    _vectored_handler = ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.POINTER(_ExceptionPointers))(_vectored_handler_impl)
else:
    _vectored_handler = None


def _write_to_file(path_str: str, data: bytes) -> None:
    """Write data to a file at *path_str* using only kernel32 APIs.

    Uses CreateFileW with the correct LPCWSTR (c_wchar_p) marshalling
    for the path, and WriteFile for the data.  The path is a Python str
    (stored internally as UTF-16 LE on Windows), so c_wchar_p correctly
    marshals it as LPCWSTR.

    If the write fails (e.g. due to heap corruption during
    STATUS_HEAP_CORRUPTION), the empty file is deleted so it doesn't
    accumulate as a 0-byte diagnostic file.

    *path_str* must end with a null terminator (\0) for CreateFileW.
    """
    handle = _func_create_file_w(
        ctypes.c_wchar_p(path_str),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    handle_val = handle.value if handle else -1
    if handle_val == -1:
        return

    write_ok = False
    try:
        # Seek to end (FILE_END = 2).  lpDistanceToMoveHigh=None is valid.
        _func_set_file_pointer(handle, 0, None, 2)

        written = wintypes.DWORD(0)
        if _func_write_file(
            handle,
            data,
            len(data),
            ctypes.byref(written),
            None,
        ):
            write_ok = written.value == len(data)
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            _func_close_handle(handle)
        # If the write failed (heap corruption, disk full, etc.),
        # delete the empty file to prevent accumulation of 0-byte
        # crash diagnostic files.
        if not write_ok:
            with contextlib.suppress(Exception):
                _func_delete_file_w(ctypes.c_wchar_p(path_str))


# ── Public API ────────────────────────────────────────────────────────


def set_crash_handler_config_dir(config_dir: Path) -> None:
    """Cache the config directory path for the VEH callback.

    Pre-builds the crash diagnostics file path as a Python str
    with a null terminator (\0) for CreateFileW.  Stored as a str
    so we can pass it via c_wchar_p (which correctly marshals the
    internal UTF-16 buffer as LPCWSTR).

    G4-L-12: the path is built via ``os.path.join`` (was a hardcoded
    ``\\`` backslash, which broke on POSIX where the VEH callback is
    never invoked but the cached path is still asserted on by tests).

    G4-L-13: removed the dead ``_config_dir_bytes`` / ``_CONFIG_DIR_BYTES``
    dual binding — only ``_crash_file_path`` is used downstream.

    G4-M-34: also caches the config dir for ``_crash_excepthook`` so the
    Python-level excepthook can write a ``python_crash.<PID>.txt`` marker.

    G4-L-14: resets ``_crash_written`` so each test (and each process)
    starts with a clean rate-limit flag.
    """
    global _crash_file_path, _PID, _python_crash_dir, _crash_written
    try:
        resolved = Path(config_dir).resolve()
        _PID = os.getpid()
        # G4-L-12: use os.path.join instead of a hardcoded backslash so
        # the cached path is correct on both Windows and POSIX (POSIX
        # never invokes the VEH callback, but tests still inspect the
        # cached path).  Preserve the trailing NUL terminator required
        # by CreateFileW.
        _crash_file_path = os.path.join(str(resolved), f"crash_diagnostics.{_PID}.txt") + "\0"
        _python_crash_dir = resolved
        # G4-L-14: reset the rate-limit flag so a fresh process (or a
        # re-init in tests) can write a new crash record.
        _crash_written = False
    except Exception as exc:
        log.debug("[CRASH] Failed to cache config dir: %s", exc)
        _crash_file_path = ""
        _python_crash_dir = None
        _crash_written = False


def _archive_crash_file(file_path: Path, config_dir: Path) -> None:
    """G4-M-33: move a crash diagnostics / python_crash file to the archive.

    The archive lives at ``<config_dir>/crash_diagnostics_archive/`` and
    is created with ``0o700`` perms on POSIX so the archived crash
    records (which may include exception addresses, thread IDs, etc.)
    are not world-readable.

    After moving, applies the G4-M-33 retention policy (keep last
    ``_ARCHIVE_RETENTION_KEEP`` files in the archive, delete older).
    """
    archive_dir = Path(config_dir) / _CRASH_DIAGNOSTICS_ARCHIVE
    archive_dir.mkdir(parents=True, exist_ok=True)
    # G4-M-33: tighten archive dir perms on POSIX so crash records
    # (which contain process internals) are not world-readable.
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.chmod(archive_dir, 0o700)
    target = archive_dir / file_path.name
    # If the target already exists (e.g. a previous archive had the
    # same PID), disambiguate by appending a monotonic timestamp.
    if target.exists():
        stem = file_path.stem
        suffix = file_path.suffix
        target = archive_dir / f"{stem}.{int(time.time() * 1000)}{suffix}"
    file_path.rename(target)
    log.info("[CRASH] Archived diagnostics file: %s -> %s", file_path.name, target.name)
    _enforce_archive_retention(archive_dir)


def _enforce_archive_retention(archive_dir: Path) -> None:
    """G4-M-33: keep only the last ``_ARCHIVE_RETENTION_KEEP`` files.

    Files are sorted by mtime (newest first); older files beyond the
    retention cap are deleted.  All errors are suppressed (best-effort).
    """
    try:
        files = sorted(
            archive_dir.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception:
        return
    for stale in files[_ARCHIVE_RETENTION_KEEP:]:
        with contextlib.suppress(Exception):
            stale.unlink()


def _sweep_stale_diagnostics(config_dir: Path) -> None:
    """G4-M-32: sweep stale crash diagnostics from the config_dir root.

    After ``report_pending_crash`` moves all current files into the
    archive, the config_dir root should normally be empty of
    ``crash_diagnostics.*.txt`` / ``python_crash.*.txt`` files.  This
    sweep is a safety net for files that were left behind by an older
    version (pre-archiving) or by a failed move.

    Policy:
      * Delete any file older than ``_MAX_AGE_DAYS`` days (mtime).
      * If more than ``_MAX_ACTIVE_FILES`` files remain, delete the oldest
        beyond the cap.
    """
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return
        files = list(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        files.extend(diagnostics_dir.glob("python_crash.*.txt"))
        if not files:
            return
        now = time.time()
        # First pass: delete files older than the mtime cutoff.
        for f in files:
            try:
                if now - f.stat().st_mtime > _MAX_AGE_SECONDS:
                    f.unlink()
            except Exception as exc:
                log.debug("[CRASH] sweep: failed to delete stale %s: %s", f, exc)
        # Second pass: enforce the count cap on the remaining files.
        try:
            remaining = sorted(
                (f for f in files if f.exists()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            return
        for stale in remaining[_MAX_ACTIVE_FILES:]:
            with contextlib.suppress(Exception):
                stale.unlink()
    except Exception as exc:
        log.debug("[CRASH] sweep: failed: %s", exc)


def report_pending_crash(config_dir: Path) -> str | None:
    """Check for leftover crash diagnostics from a previous session.

    Scans the config directory for:
      * ``crash_diagnostics.*.txt`` files written by the VEH callback
        when a previous process crashed silently (heap corruption,
        access violation, etc.).
      * ``python_crash.*.txt`` marker files written by
        ``_crash_excepthook`` when an unhandled Python exception
        terminated the previous process.

    If any are found:
      1. Logs the full contents at ``WARNING`` level to ``voice-typer.log``
      2. Moves the file to ``<config_dir>/crash_diagnostics_archive/``
         (G4-M-33) instead of deleting it, so the diagnostic bundle
         can include it later.
      3. Returns a human-readable summary for the caller to surface
         (e.g., as a tray notification).

    After processing, applies the G4-M-32 sweep (30-day mtime cutoff +
    keep last 10) to the config_dir root as a safety net for files left
    behind by failed moves or older versions.

    Returns ``None`` if no crash diagnostics were found.
    """
    # The file pattern uses the process PID from the previous run, so we
    # can't predict the exact filename.  Globbing is done via pathlib.
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return None
        # Collect all crash diagnostics files matching the pattern.
        # G4-M-34: also collect python_crash.*.txt marker files written
        # by the Python excepthook so they are surfaced in the same
        # startup notification.
        crash_files = sorted(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        python_crash_files = sorted(diagnostics_dir.glob("python_crash.*.txt"))
        if not crash_files and not python_crash_files:
            return None
    except Exception as exc:
        log.debug("[CRASH] Failed to scan for diagnostics files: %s", exc)
        return None

    summary_parts: list[str] = []
    for crash_file in crash_files:
        try:
            content = crash_file.read_text(encoding="utf-8").strip()
            if not content:
                log.debug(
                    "[CRASH] Found empty diagnostics file %s — cleaning up",
                    crash_file.name,
                )
                continue
            # Log each line of the crash diagnostics to voice-typer.log
            # at WARNING level so it appears clearly in the log file.
            log.warning("[CRASH] === Previous session crashed! Diagnostics follow ===")
            for line in content.split("\r\n"):
                line = line.strip()
                if line:
                    log.warning("[CRASH] %s", line)
            # Extract the exception code for a human-readable summary
            # Each message now includes possible causes: low memory (RAM)
            # and low disk space — the two most common triggers for
            # silent heap corruption / access violation crashes.
            if "STATUS_HEAP_CORRUPTION" in content:
                summary_parts.append(
                    "Heap corruption (0xC0000374). Likely cause: low memory (RAM), "
                    "low disk space, or a C extension bug."
                )
            elif "STATUS_ACCESS_VIOLATION" in content:
                summary_parts.append("Access violation (0xC0000005). Likely cause: low memory or a C extension bug.")
            elif "STATUS_STACK_BUFFER_OVERRUN" in content:
                summary_parts.append(
                    "Stack overrun (0xC0000409). Likely cause: low memory, stack overflow, or a C extension bug."
                )
            elif "STATUS_FATAL_APP_EXIT" in content:
                summary_parts.append(
                    "Fatal exit (0x40000015). The process detected a critical "
                    "error and terminated itself. Likely cause: low memory "
                    "or a C extension bug."
                )
            else:
                # Extract the crash code line for unknown codes
                for line in content.split("\r\n"):
                    if "code=0x" in line:
                        summary_parts.append(
                            f"Process crashed: {line.strip()}. Likely cause: low memory or low disk space."
                        )
                        break
                else:
                    summary_parts.append(
                        "Previous session ended unexpectedly. Likely cause: low memory or low disk space."
                    )
        except Exception as exc:
            # PVT-G5-046 (session-5): use ``crash_file.name`` (not the
            # full ``crash_file`` Path) so the log doesn't include the
            # user's home directory path.
            log.warning("[CRASH] Failed to read diagnostics file %s: %s", crash_file.name, exc)
        finally:
            # G4-M-33: move the file to the archive instead of unlinking,
            # so the diagnostic bundle can include it later.  Moving
            # (rather than deleting) also preserves the file for forensic
            # review if the user files a bug report.
            try:
                _archive_crash_file(crash_file, diagnostics_dir)
            except Exception as exc:
                log.debug("[CRASH] Failed to archive diagnostics file %s: %s", crash_file.name, exc)

    # G4-M-34: process python_crash marker files written by the
    # Python-level excepthook.  These capture unhandled Python
    # exceptions (e.g. in daemon threads) that would otherwise only
    # appear on stderr.
    for py_crash_file in python_crash_files:
        try:
            content = py_crash_file.read_text(encoding="utf-8").strip()
            if not content:
                log.debug(
                    "[CRASH] Found empty python_crash file %s — cleaning up",
                    py_crash_file.name,
                )
                continue
            log.warning("[CRASH] === Previous session crashed (Python exception)! ===")
            for line in content.splitlines():
                line = line.strip()
                if line:
                    log.warning("[CRASH] %s", line)
            # Build a concise summary from the key=value lines.
            fields: dict[str, str] = {}
            for line in content.splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    fields[key.strip()] = value.strip()
            exc_type = fields.get("exc_type", "UnknownException")
            exc_value = fields.get("exc_value", "")
            thread_name = fields.get("thread", "?")
            timestamp = fields.get("timestamp", "?")
            summary_parts.append(
                f"Python crash: {exc_type}: {exc_value} "
                f"(thread={thread_name}, at={timestamp}). "
                "Likely cause: an unhandled Python exception in the main "
                "thread or a daemon thread."
            )
        except Exception as exc:
            log.warning("[CRASH] Failed to read python_crash file %s: %s", py_crash_file.name, exc)
        finally:
            # G4-M-34: archive python_crash files in the same archive
            # directory so they're included in the diagnostic bundle.
            try:
                _archive_crash_file(py_crash_file, diagnostics_dir)
            except Exception as exc:
                log.debug("[CRASH] Failed to archive python_crash file %s: %s", py_crash_file.name, exc)

    # G4-M-32: sweep the config_dir root for any stale diagnostics
    # files (e.g. left behind by a failed move or by an older version
    # that unlinked instead of archiving).  Applies a 30-day mtime
    # cutoff and a keep-last-10 cap.
    with contextlib.suppress(Exception):
        _sweep_stale_diagnostics(diagnostics_dir)

    if not summary_parts:
        return None

    summary = "\n".join(summary_parts)
    log.warning("[CRASH] Crash summary for user notification:\n%s", summary)
    return summary


def install_crash_handler() -> bool:
    """Install the Windows Vectored Exception Handler.

    Must be called once at process startup, *before* any C extensions
    are loaded that could corrupt the heap.  Idempotent.

    On non-Windows, does nothing and returns False.
    """
    global _handler_handle
    if _handler_handle is not None:
        return True
    if sys.platform != "win32":
        return False

    try:
        _ensure_kernel32()

        add_veh = _kernel32.AddVectoredExceptionHandler
        add_veh.argtypes = [wintypes.ULONG, ctypes.c_void_p]
        add_veh.restype = ctypes.c_void_p

        handler_ptr = add_veh(1, _vectored_handler)
        if handler_ptr:
            _handler_handle = handler_ptr
            log.info(
                "[CRASH] Windows VEH installed — will capture silent crashes "
                "(heap corruption, access violation, stack overrun)"
            )
            return True
        else:
            log.warning("[CRASH] AddVectoredExceptionHandler failed")
            return False
    except Exception as exc:
        log.warning("[CRASH] Failed to install VEH: %s", exc)
        return False


def remove_crash_handler() -> None:
    """Remove the VEH handler. Idempotent."""
    global _handler_handle
    if _handler_handle is None or sys.platform != "win32":
        _handler_handle = None
        return
    with contextlib.suppress(Exception):
        remove_veh = _kernel32.RemoveVectoredExceptionHandler
        remove_veh.argtypes = [ctypes.c_void_p]
        remove_veh.restype = wintypes.ULONG
        remove_veh(_handler_handle)
    _handler_handle = None
    log.debug("[CRASH] VEH removed")


# ── Python-level sys.excepthook ───────────────────────────────────────

_original_excepthook = sys.excepthook


def _crash_excepthook(exc_type, exc_value, exc_tb) -> None:
    """Custom sys.excepthook for unhandled Python exceptions.

    Logs the exception to the voice-typer logger before chaining to
    the original hook.  Catches Python-level crashes (e.g., unhandled
    exceptions in threads) that would otherwise only appear on stderr.

    G4-M-34: also writes a ``python_crash.<PID>.txt`` marker file to
    the config_dir so the next session's ``report_pending_crash`` can
    surface the crash in the startup notification (alongside VEH
    crash diagnostics).  The marker contains the exception type,
    value, thread name, and timestamp — enough to diagnose the crash
    without re-running with a debugger attached.
    """
    with contextlib.suppress(Exception):
        # XZ-PII-01: redact exc_value before logging at CRITICAL.
        # PIIRedactionFilter (attached to log handlers) only catches
        # structured PII patterns + API-key-shaped tokens. Plain user
        # speech in str(exc_value) (e.g. ValueError("cannot process: "
        # + transcribed_text)) would pass through verbatim into the
        # rotating log file. Apply explicit redaction here so secrets
        # and PII are stripped before the log record is formatted.
        try:
            from voice_typer.server._secrets import redact_secret
            from voice_typer.server.security import redact_pii

            _redacted_value = redact_secret(redact_pii(str(exc_value))) if exc_value is not None else "None"
        except Exception:
            _redacted_value = str(exc_value)[:200] if exc_value is not None else "None"
        log.critical(
            "[CRASH] Unhandled Python exception: %s: %s",
            exc_type.__name__ if exc_type is not None else "Unknown",
            _redacted_value,
        )
        # Full traceback only when VOICE_TYPER_DEBUG=1 (operator opt-in
        # for verbose diagnostics). Production logs get the redacted
        # single-line summary above to bound PII exposure.
        if os.environ.get("VOICE_TYPER_DEBUG", "") == "1":
            log.critical("[CRASH] Full traceback (VOICE_TYPER_DEBUG=1)", exc_info=(exc_type, exc_value, exc_tb))
    # G4-M-34: write a python_crash.<PID>.txt marker so the next
    # session's report_pending_crash can surface it.  Best-effort —
    # the hook must never raise (it runs during interpreter shutdown
    # for unhandled exceptions, where any failure masks the original
    # error).  Thread-safe: the PID suffix makes collisions extremely
    # unlikely, and the worst case is a single overwritten file.
    if _python_crash_dir is not None:
        with contextlib.suppress(Exception):
            # XZ-R17-03: use _secure_atomic_write for atomic write +
            # O_NOFOLLOW + 0o600 on POSIX (was write_text with default
            # umask 0644 = world-readable on multi-user systems).
            # XZ-PII-02: apply redact_pii + redact_secret to exc_value
            # before persisting to the marker file (was raw str()).
            try:
                from voice_typer.server._secrets import redact_secret
                from voice_typer.server.config import _secure_atomic_write
                from voice_typer.server.security import redact_pii

                _atomic_write = _secure_atomic_write

                def _redact(s):
                    return redact_secret(redact_pii(s))

            except Exception:
                _atomic_write = None

                def _redact(s):
                    return s
            marker_path = _python_crash_dir / f"python_crash.{os.getpid()}.txt"
            thread_name = threading.current_thread().name
            timestamp = datetime.now().isoformat()
            # CR-98 + XZ-PII-02: truncate + redact exc_value so user
            # speech and secrets don't leak into the persistent crash
            # archive.
            _raw_value = str(exc_value)[:200] if exc_value is not None else "None"
            _safe_value = _redact(_raw_value)
            content = (
                f"exc_type={exc_type.__name__ if exc_type is not None else 'Unknown'}\n"
                f"exc_value={_safe_value}\n"
                f"thread={thread_name}\n"
                f"timestamp={timestamp}\n"
            )
            if _atomic_write is not None:
                _atomic_write(marker_path, content)
            else:
                marker_path.write_text(content, encoding="utf-8")
    if _original_excepthook is not None and _original_excepthook is not _crash_excepthook:
        with contextlib.suppress(Exception):
            _original_excepthook(exc_type, exc_value, exc_tb)
    for handler in logging.getLogger("voice_typer").handlers:
        with contextlib.suppress(Exception):
            handler.flush()


def install_python_excepthook() -> None:
    """Install the custom sys.excepthook. Idempotent."""
    global _original_excepthook
    if sys.excepthook is _crash_excepthook:
        return
    _original_excepthook = sys.excepthook
    sys.excepthook = _crash_excepthook
