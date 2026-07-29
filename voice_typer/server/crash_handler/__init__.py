"""Windows Vectored Exception Handler — captures silent process crashes.

STATUS_HEAP_CORRUPTION (0xC0000374), STATUS_ACCESS_VIOLATION, and other
SEH exceptions kill the process silently — no Python traceback, no log
message — because the OS terminates the process before Python's exception
machinery can run.

This package installs a Vectored Exception Handler (AddVectoredExceptionHandler)
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

Architecture note: this module is a facade that re-exports the public API
from submodules. All mutable state (``_crash_file_path``, ``_PID``,
``_handler_handle``, ``_kernel32``, the ``_func_*`` pointers,
``_crash_written``, ``_python_crash_dir``, ``_crash_header_bytes``,
``_original_excepthook``, ``_vectored_handler``, ``_ft``, ``_st``)
lives HERE on the facade module so that test mutations on
``voice_typer.server.crash_handler.<name>`` propagate to the submodule
functions that read/write the same state (the submodules access state
via ``from voice_typer.server import crash_handler as _ch;
_ch.<name>``). This preserves the pre-split behavior where tests could
reset module-level globals directly.

Submodules:
- ``_constants`` — pure constants (status codes, ``_CRASH_CODES``,
  Win32 file-I/O constants, archive/retention constants, VEH buffer
  layout, pre-encoded static byte parts, ``_HEX_CHARS``).
- ``_win32_structs`` — ``_ExceptionRecord`` / ``_ExceptionPointers`` /
  ``_SYSTEMTIME`` ctypes structures (per-platform guard).
- ``_veh_kernel32`` — ``_ensure_kernel32`` (kernel32 function-pointer
  resolver).
- ``_veh_callback`` — ``_write_u32_hex`` / ``_write_u64_hex`` /
  ``_write_timestamp`` / ``_vectored_handler_impl`` / ``_write_to_file``
  + module-load-time ``_vectored_handler`` wrapping on Windows.
- ``_diagnostics_archive`` — ``_compute_crash_header`` /
  ``set_crash_handler_config_dir`` / ``_archive_crash_file`` /
  ``_enforce_archive_retention`` / ``_sweep_stale_diagnostics`` /
  ``report_pending_crash``.
- ``_python_excepthook`` — ``_format_redacted_traceback`` /
  ``_get_active_asr_backend`` / ``_crash_excepthook`` /
  ``install_python_excepthook`` / ``install_crash_handler`` /
  ``remove_crash_handler``.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

# ── Mutable state (lives HERE so test mutations propagate) ───────────
#
# These module-level variables are the canonical storage for the
# crash_handler package's mutable state. Submodule functions access
# them via ``from voice_typer.server import crash_handler as _ch;
# _ch.<name>`` (NOT via ``global``) so a test that does
# ``crash_handler._kernel32 = None`` is observed by the submodule
# function on its next read.

# Kernel32 function pointers (Windows-only; None on Linux).
# Resolved once by ``_veh_kernel32._ensure_kernel32``.
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
# On Windows these are ``wintypes.FILETIME()`` / ``_SYSTEMTIME()``;
# on Linux they stay ``None`` (the VEH callback is never invoked).
if sys.platform == "win32":
    from ctypes import wintypes

    from voice_typer.server.crash_handler._win32_structs import _SYSTEMTIME

    _ft = wintypes.FILETIME()
    _st = _SYSTEMTIME()
else:
    _ft = None
    _st = None

# Pre-computed file path (Python str, built at install time)
# Stored as a Python str so we can pass it to CreateFileW via c_wchar_p
# (which correctly marshals the internal UTF-16 buffer)
_crash_file_path: str = ""
_PID: int = 0

# Pre-computed static header written as a preamble to every
# ``crash_diagnostics.<PID>.txt`` file.  Built once at
# ``set_crash_handler_config_dir()`` time so the VEH callback can write
# it without any heap allocations (the bytes are already encoded).
_crash_header_bytes: bytes = b""

# config_dir used by ``_crash_excepthook`` to write the
# ``python_crash.<PID>.txt`` marker file.  Set in
# ``set_crash_handler_config_dir``.
_python_crash_dir = None  # type: ignore[assignment]

# Rate-limit flag for the VEH callback.  Set to True after a
# successful ``_write_to_file`` call so cascading exceptions don't write
# multiple crash records (which can corrupt the file or fill the disk).
# Reset to False in ``set_crash_handler_config_dir`` so tests work.
_crash_written: bool = False

# VEH handle returned by AddVectoredExceptionHandler (Windows-only).
# None on Linux or before ``install_crash_handler`` is called.
_handler_handle: int | None = None

# Original sys.excepthook saved by ``install_python_excepthook`` so
# ``_crash_excepthook`` can chain to it after logging the crash.
_original_excepthook = sys.excepthook

# FR-14: original ``threading.excepthook`` saved by
# ``install_threading_excepthook`` so ``_thread_crash_excepthook`` can
# chain to it after logging the daemon-thread crash. Set on first
# ``install_threading_excepthook`` call; ``None`` until then.
_original_threading_excepthook = None

# ``_vectored_handler`` is the WINFUNCTYPE-wrapped VEH callback
# (Windows-only). None on Linux/macOS — ``install_crash_handler``
# short-circuits on ``sys.platform != "win32"`` and never dereferences
# it. Set at module-load time by ``_veh_callback`` (on Windows).
_vectored_handler = None


# ── Re-exports (constants, structs, functions) ───────────────────────
#
# Constants and struct/class references are imported here so they're
# accessible as ``crash_handler.<name>`` (tests read these directly).
# Function references are imported here so they're callable as
# ``crash_handler.<name>(...)``. The function bodies access mutable
# state via ``_ch.<name>`` (above), NOT via ``global`` — so test
# mutations on the facade propagate.

from voice_typer.server.crash_handler._constants import (  # noqa: F401,E402
    _ADDR_LABEL,
    _ARCHIVE_RETENTION_KEEP,
    _BOM,
    _CODE_LABEL,
    _CRASH_CODES,
    _CRASH_DIAGNOSTICS_ARCHIVE,
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
    _crash_msg_buf,
)
from voice_typer.server.crash_handler._diagnostics_archive import (  # noqa: F401,E402
    _archive_crash_file,
    _compute_crash_header,
    _enforce_archive_retention,
    _sweep_stale_diagnostics,
    report_pending_crash,
    set_crash_handler_config_dir,
)
from voice_typer.server.crash_handler._python_excepthook import (  # noqa: F401,E402
    _crash_excepthook,
    _format_redacted_traceback,
    _get_active_asr_backend,
    _sanitize_thread_name_for_filename,
    _thread_crash_excepthook,
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
