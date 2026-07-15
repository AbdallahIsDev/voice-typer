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
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

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
_CONFIG_DIR_BYTES: bytes = b""
_PID: int = 0

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


@ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.POINTER(_ExceptionPointers))
def _vectored_handler(exception_pointers) -> int:
    """VEH callback — runs when an SEH exception occurs.

    Writes crash diagnostics using ONLY pre-allocated buffers and raw
    kernel32 API calls.  Avoids Python heap allocations to maximise the
    chance of succeeding even during heap corruption.

    Always returns EXCEPTION_CONTINUE_SEARCH so the OS proceeds with
    normal termination.
    """
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

    return EXCEPTION_CONTINUE_SEARCH


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
    """
    global _config_dir_bytes, _crash_file_path, _PID
    try:
        _config_dir_bytes = str(config_dir.resolve()).encode("utf-8")
        _PID = os.getpid()
        dir_str = _config_dir_bytes.decode("utf-8")
        # Store as Python str with null terminator for CreateFileW
        _crash_file_path = f"{dir_str}\\crash_diagnostics.{_PID}.txt\0"
    except Exception as exc:
        log.debug("[CRASH] Failed to cache config dir: %s", exc)
        _crash_file_path = ""


def report_pending_crash(config_dir: Path) -> str | None:
    """Check for leftover crash diagnostics from a previous session.

    Scans the config directory for ``crash_diagnostics.*.txt`` files
    written by the VEH callback when a previous process crashed silently
    (heap corruption, access violation, etc.).

    If any are found:
      1. Logs the full contents at ``WARNING`` level to ``voice-typer.log``
      2. Deletes the diagnostics file
      3. Returns a human-readable summary for the caller to surface
         (e.g., as a tray notification)

    Returns ``None`` if no crash diagnostics were found.
    """
    # The file pattern uses the process PID from the previous run, so we
    # can't predict the exact filename.  Globbing is done via pathlib.
    try:
        diagnostics_dir = Path(config_dir).resolve()
        if not diagnostics_dir.is_dir():
            return None
        # Collect all crash diagnostics files matching the pattern
        crash_files = sorted(diagnostics_dir.glob("crash_diagnostics.*.txt"))
        if not crash_files:
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
            log.warning("[CRASH] Failed to read diagnostics file %s: %s", crash_file, exc)
        finally:
            # Always delete the diagnostics file after processing,
            # even if reading failed, to prevent duplicate reporting.
            try:
                crash_file.unlink()
                log.info("[CRASH] Deleted diagnostics file: %s", crash_file.name)
            except Exception as exc:
                log.debug("[CRASH] Failed to delete diagnostics file %s: %s", crash_file, exc)

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
    """
    with contextlib.suppress(Exception):
        log.critical(
            "[CRASH] Unhandled Python exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
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
