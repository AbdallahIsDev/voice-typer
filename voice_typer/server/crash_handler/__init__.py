"""Windows Vectored Exception Handler, captures silent process crashes."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# These module-level variables are the canonical storage for the

# Kernel32 function pointers (Windows-only; None on Linux).
_kernel32: ctypes.WinDLL | None = None  # type: ignore[valid-type]
_func_get_current_process_id: ctypes._FuncPtr | None = None
_func_get_current_thread_id: ctypes._FuncPtr | None = None
_func_get_system_time_as_file_time: ctypes._FuncPtr | None = None
_func_file_time_to_system_time: ctypes._FuncPtr | None = None
_func_create_file_w: ctypes._FuncPtr | None = None
_func_write_file: ctypes._FuncPtr | None = None
_func_set_file_pointer: ctypes._FuncPtr | None = None
_func_close_handle: ctypes._FuncPtr | None = None
_func_delete_file_w: ctypes._FuncPtr | None = None

# Pre-allocated struct instances (no heap allocation in callback).
if sys.platform == "win32":
    from ctypes import wintypes

    from voice_typer.server.crash_handler._win32_structs import _SYSTEMTIME

    _ft = wintypes.FILETIME()
    _st = _SYSTEMTIME()
else:
    _ft = None
    _st = None

# Pre-allocated crash-message buffer. Mutated in place by
_crash_msg_buf: bytearray = bytearray(0)

# rate-limit lock for the VEH callback's compare-and-set on
_crash_write_lock: threading.Lock = threading.Lock()

# Pre-computed file path (Python str, built at install time)
_crash_file_path: str = ""
_PID: int = 0

# Pre-computed static header written as a preamble to every
_crash_header_bytes: bytes = b""

# config_dir used by ``_crash_excepthook`` to write the
_python_crash_dir: Path | None = None

# Rate-limit flag for the VEH callback.  Set to True after a
_crash_written: bool = False

# VEH handle returned by AddVectoredExceptionHandler (Windows-only).
_handler_handle: int | None = None

# Original sys.excepthook saved by ``install_python_excepthook`` so
_original_excepthook = sys.excepthook

# original ``threading.excepthook`` saved by
_original_threading_excepthook: Any = None

# ``_vectored_handler`` is the WINFUNCTYPE-wrapped VEH callback
_vectored_handler: Any = None

# In-memory log ring buffer (MemoryHandler) + its target
_memory_handler: logging.handlers.MemoryHandler | None = None
_crash_buffer_handler: logging.Handler | None = None

# cached active ASR backend name. Populated at install time by
_cached_active_backend: str | None = None


# Constants and struct/class references are imported here so they're

from voice_typer.server.crash_handler._constants import (  # noqa: F401,E402
    _ADDR_LABEL,
    _ARCHIVE_RETENTION_KEEP,
    _BOM,
    _CODE_LABEL,
    _CODE_TO_INFO,
    _CODE_TO_USER_SUMMARY,
    _CRASH_CODES,
    _CRASH_DIAGNOSTICS_DIR,
    _CRASH_LABEL,
    _CRASH_MSG_BUF_SIZE,
    _CRASH_MSG_LAYOUT,
    _HEADER_MAX_MODULES,
    _HEX_CHARS,
    _MAX_ACTIVE_FILES,
    _MAX_AGE_DAYS,
    _MAX_AGE_SECONDS,
    _NAME_ACCESS,
    _NAME_FATAL,
    _NAME_GUARD_PAGE,
    _NAME_HEAP,
    _NAME_ILLEGAL_INSTRUCTION,
    _NAME_IN_PAGE_ERROR,
    _NAME_INT_DIVIDE_BY_ZERO,
    _NAME_INVALID_HANDLE,
    _NAME_MISALIGNMENT,
    _NAME_NONCONTINUABLE,
    _NAME_PRIVILEGED_INSTRUCTION,
    _NAME_STACK,
    _NAME_STACK_OVERFLOW,
    _NAME_UNKNOWN,
    _NL,
    _PID_LABEL,
    _REPORTED_SIDECAR_SUFFIX,
    _SEP,
    _TID_LABEL,
    EXCEPTION_CONTINUE_SEARCH,
    FILE_ATTRIBUTE_NORMAL,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_WRITE,
    OPEN_ALWAYS,
    STATUS_ACCESS_VIOLATION,
    STATUS_DATATYPE_MISALIGNMENT,
    STATUS_FATAL_APP_EXIT,
    STATUS_GUARD_PAGE_VIOLATION,
    STATUS_HEAP_CORRUPTION,
    STATUS_ILLEGAL_INSTRUCTION,
    STATUS_IN_PAGE_ERROR,
    STATUS_INT_DIVIDE_BY_ZERO,
    STATUS_INVALID_HANDLE,
    STATUS_NONCONTINUABLE_EXCEPTION,
    STATUS_PRIVILEGED_INSTRUCTION,
    STATUS_STACK_BUFFER_OVERRUN,
    STATUS_STACK_OVERFLOW,
)

# allocate the mutable crash-message buffer HERE (after the
_crash_msg_buf = bytearray(_CRASH_MSG_BUF_SIZE)
from voice_typer.server.crash_handler._diagnostics_archive import (  # noqa: F401,E402
    _archive_crash_file,
    _compute_crash_header,
    _enforce_archive_retention,
    _sweep_stale_diagnostics,
    report_pending_crash,
    set_crash_handler_config_dir,
)
from voice_typer.server.crash_handler._memory_buffer import (  # noqa: F401,E402
    flush_memory_handler,
    install_memory_buffer,
    uninstall_memory_buffer,
)
from voice_typer.server.crash_handler._python_excepthook import (  # noqa: F401,E402
    _crash_excepthook,
    _format_redacted_traceback,
    _get_active_asr_backend,
    _get_cached_asr_backend,
    _get_secure_atomic_write,
    _redact_exc_value,
    _refresh_cached_asr_backend,
    _safe_redact_fallback,
    _sanitize_thread_name_for_filename,
    _thread_crash_excepthook,
    _write_crash_marker,
    install_crash_handler,
    install_python_excepthook,
    install_threading_excepthook,
    remove_crash_handler,
    remove_python_excepthook,
    remove_threading_excepthook,
)
from voice_typer.server.crash_handler._veh_callback import (  # noqa: F401,E402
    _vectored_handler_impl,
    _write_timestamp,
    _write_to_file,
    _write_u32_hex,
    _write_u64_hex,
)
from voice_typer.server.crash_handler._veh_kernel32 import _ensure_kernel32  # noqa: F401,E402
from voice_typer.server.crash_handler._win32_structs import (  # noqa: F401,E402
    _SYSTEMTIME,
    _ExceptionPointers,
    _ExceptionRecord,
)
