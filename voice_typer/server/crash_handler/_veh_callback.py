"""VEH callback implementation + byte-buffer writer primitives.

``_write_u32_hex`` / ``_write_u64_hex`` / ``_write_timestamp`` are
hand-rolled hex formatters that write directly into a pre-allocated
``bytearray`` (no heap allocations — safe inside the VEH callback
during heap corruption).

``_vectored_handler_impl`` is the VEH callback body: reads the
exception code/address from the ``EXCEPTION_POINTERS`` struct, builds
a crash-diagnostics blurb in ``_crash_msg_buf``, and writes it to
``crash_diagnostics.<PID>.txt`` via ``_write_to_file`` (raw kernel32
``CreateFileW`` + ``WriteFile``).

``_write_to_file`` wraps the kernel32 file-I/O sequence (open, seek to
end, write, close; delete on failure so 0-byte diagnostic files don't
accumulate).

At module-load time on Windows, ``_vectored_handler_impl`` is wrapped
in a ``WINFUNCTYPE`` and cached on the facade as ``_vectored_handler``.
On non-Windows, ``_vectored_handler`` stays ``None`` and the callback
is never invoked.

Split out from the original monolithic ``crash_handler.py``.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys

from voice_typer.server.crash_handler._constants import (
    _ADDR_LABEL,
    _BOM,
    _CODE_LABEL,
    _CODE_TO_INFO,
    _CRASH_CODES,
    _CRASH_LABEL,
    _HEX_CHARS,
    _NAME_UNKNOWN,
    _NL,
    _PID_LABEL,
    _SEP,
    _TID_LABEL,
    EXCEPTION_CONTINUE_SEARCH,
    FILE_ATTRIBUTE_NORMAL,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_WRITE,
    OPEN_ALWAYS,
)


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

    The kernel32 function pointers (``_func_get_system_time_as_file_time``,
    ``_func_file_time_to_system_time``) and the pre-allocated structs
    (``_ft``, ``_st``) live on the ``crash_handler`` facade module so
    test mutations propagate. Accessed via ``_ch.<name>``.
    """
    from voice_typer.server import crash_handler as _ch

    _ch._func_get_system_time_as_file_time(ctypes.byref(_ch._ft))
    _ch._func_file_time_to_system_time(ctypes.byref(_ch._ft), ctypes.byref(_ch._st))

    _st = _ch._st
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

    A module-level ``_crash_written`` flag rate-limits the callback so
    cascading exceptions (e.g. an access violation triggered while
    handling an earlier access violation) cannot write multiple crash
    records — the second invocation returns early.

    Mutable state (``_crash_written``, ``_crash_file_path``,
    ``_crash_header_bytes``, the ``_func_*`` pointers) lives on the
    ``crash_handler`` facade module so test mutations propagate.
    Accessed via ``_ch.<name>``.
    """
    from voice_typer.server import crash_handler as _ch

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

    # Rate-limit — don't write multiple crash records under cascading
    # exceptions.  Once we've written one record, subsequent VEH
    # callbacks (which the OS may deliver as the exception dispatcher
    # unwinds) return early so we don't corrupt or duplicate the file.
    #
    # UE-2-F2: the rate-limit check + write + flag-set is now wrapped
    # in a compare-and-set via ``_ch._crash_write_lock``. Pre-fix,
    # two concurrent VEH callbacks (e.g. cascading access violations on
    # two threads) could BOTH pass the ``_crash_written`` check before
    # either set the flag, producing two crash records (and potentially
    # corrupting the file via concurrent ``WriteFile`` calls to the
    # same path). The lock is acquired non-blocking: if another thread
    # is mid-write, this callback returns early so it doesn't block the
    # OS exception dispatcher.
    #
    # Residual gap (documented): the VEH callback is NOT
    # async-signal-safe in Python (it allocates ctypes wrappers, calls
    # kernel32 functions, etc.). During STATUS_HEAP_CORRUPTION the
    # heap is corrupted and the ``Lock.acquire`` call itself may fail
    # or deadlock. This is acceptable because (a) the alternative (no
    # lock) is worse (duplicate records, possible file corruption from
    # concurrent writes), and (b) for access violations / stack
    # overruns (the common case) the lock works reliably. The OS will
    # terminate the process shortly regardless.
    if not _ch._crash_write_lock.acquire(blocking=False):
        # Another thread is mid-crash-write — return early so we don't
        # write a duplicate record or block the exception dispatcher.
        return EXCEPTION_CONTINUE_SEARCH
    try:
        if _ch._crash_written:
            return EXCEPTION_CONTINUE_SEARCH

        # UE-2-F8: wrap kernel32 resolution in try/except — a failure
        # here (e.g. kernel32 function not found, or a non-Windows test
        # mock that didn't wire up all the function pointers) must not
        # propagate out of the VEH callback. Pre-fix, an exception
        # here would unwind through ctypes and force-kill the process
        # without writing a crash record.
        try:
            _ch._ensure_kernel32()
        except Exception:
            return EXCEPTION_CONTINUE_SEARCH

        pid = _ch._func_get_current_process_id()
        tid = _ch._func_get_current_thread_id()

        # Build the crash message in the pre-allocated buffer using ONLY
        # bytearray slice assignment (no heap allocations).
        # UE-2-F9: ``_crash_msg_buf`` now lives on the facade (alongside
        # the other mutable runtime state) — access via ``_ch.`` so
        # test mutations on ``crash_handler._crash_msg_buf`` propagate.
        buf = _ch._crash_msg_buf
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
        # AC-88: replaced the 13-clause ``if exc_code == STATUS_X: name =
        # _NAME_X`` ``elif`` chain with a single dict lookup into
        # ``_CODE_TO_INFO`` (the unified code → (name_bytes, summary_str)
        # table in ``_constants.py``). Drift eliminated: adding a new code
        # now requires editing ONE place (``_CODE_TO_INFO``) rather than
        # three (constants + VEH if-elif + read-side if-elif). The
        # ``_NAME_UNKNOWN`` fallback mirrors the original ``else`` branch.
        _info = _CODE_TO_INFO.get(exc_code)
        name = _info[0] if _info is not None else _NAME_UNKNOWN

        n_name = len(name)
        buf[pos : pos + n_name] = name
        pos += n_name
        buf[pos : pos + 2] = _NL
        pos += 2

        # Write the crash diagnostics file
        # NOTE: buf[:pos] creates a new bytes object (heap allocation).
        # This is an acceptable limitation — see the module docstring.
        if _ch._crash_file_path:
            # Prepend the pre-computed static header (app/python/OS
            # version + loaded-module snapshot) so the crash_diagnostics
            # file carries enough context for a support engineer to triage
            # the crash.  ``_crash_header_bytes`` is built once at
            # ``set_crash_handler_config_dir()`` time, so this concatenation
            # is the only extra heap allocation relative to the pre-fix
            # behavior — acceptable per the module docstring's heap-alloc
            # caveat.  If the header is empty (e.g. config_dir was never
            # set, or header computation failed), we fall back to writing
            # only the crash body, preserving the pre-fix behavior.
            body = buf[:pos]
            if _ch._crash_header_bytes:
                _write_to_file(_ch._crash_file_path, _ch._crash_header_bytes + body)
            else:
                _write_to_file(_ch._crash_file_path, body)
            # Mark as written so cascading VEH callbacks don't write a
            # second record. Set AFTER the successful write so a
            # transient kernel32 failure (e.g. disk full) doesn't
            # permanently silence the VEH — the next crash callback can
            # retry.
            _ch._crash_written = True

        return EXCEPTION_CONTINUE_SEARCH
    finally:
        _ch._crash_write_lock.release()


# Wrap the VEH callback implementation in ``WINFUNCTYPE`` ONLY on Windows.
# ``ctypes.WINFUNCTYPE`` only exists on Windows — referencing it on Linux/macOS
# raises ``AttributeError`` at module-load time, which breaks every test that
# imports ``voice_typer.server.app`` (which transitively imports crash_handler).
# On non-Windows, SEH exceptions don't exist, so ``_vectored_handler`` is None
# and ``install_crash_handler()`` short-circuits on ``sys.platform != "win32"``
# — it never dereferences ``_vectored_handler``.
# This replaces the previous ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` band-aid
# that lived in ``tests/conftest.py`` (now removed).
#
# ``_vectored_handler`` is stored on the ``crash_handler`` facade module
# (not on this submodule) so tests that read ``crash_handler._vectored_handler``
# see the same reference. On Windows the facade is partially initialized at
# this point (state vars defined, submodules being imported), but
# ``_vectored_handler`` is already initialized to ``None`` so the assignment
# succeeds.
if sys.platform == "win32":
    from ctypes import wintypes

    from voice_typer.server import crash_handler as _ch
    from voice_typer.server.crash_handler._win32_structs import _ExceptionPointers

    _ch._vectored_handler = ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.POINTER(_ExceptionPointers))(
        _vectored_handler_impl
    )


def _write_to_file(path_str: str, data: bytes | bytearray) -> None:
    """Write data to a file at *path_str* using only kernel32 APIs.

    Uses CreateFileW with the correct LPCWSTR (c_wchar_p) marshalling
    for the path, and WriteFile for the data.  The path is a Python str
    (stored internally as UTF-16 LE on Windows), so c_wchar_p correctly
    marshals it as LPCWSTR.

    If the write fails (e.g. due to heap corruption during
    STATUS_HEAP_CORRUPTION), the empty file is deleted so it doesn't
    accumulate as a 0-byte diagnostic file.

    *path_str* must end with a null terminator (\\0) for CreateFileW.

    *data* accepts ``bytes | bytearray`` (rather than just ``bytes``)
    because the VEH callback slices ``_crash_msg_buf[:pos]`` (a
    ``bytearray``) and passes the slice directly to this writer when
    there is no header to prepend.  ctypes' ``WriteFile`` accepts both
    buffer types interchangeably, and ``len(data)`` works on both, so
    no conversion is needed at the call site.  The previous
    ``bytes``-only annotation caused static type-checkers to flag the
    bytearray call site as a bad-argument-type error.

    The kernel32 function pointers (``_func_create_file_w``, etc.) live
    on the ``crash_handler`` facade module. Accessed via ``_ch.<name>``.
    """
    from ctypes import wintypes

    from voice_typer.server import crash_handler as _ch

    handle = _ch._func_create_file_w(
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
        _ch._func_set_file_pointer(handle, 0, None, 2)

        written = wintypes.DWORD(0)
        if _ch._func_write_file(
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
            _ch._func_close_handle(handle)
        # If the write failed (heap corruption, disk full, etc.),
        # delete the empty file to prevent accumulation of 0-byte
        # crash diagnostic files.
        if not write_ok:
            with contextlib.suppress(Exception):
                _ch._func_delete_file_w(ctypes.c_wchar_p(path_str))
