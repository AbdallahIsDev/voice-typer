"""Pure constants for the crash_handler package.

Status codes, the ``_CRASH_CODES`` frozenset, Win32 file-I/O constants
(``GENERIC_WRITE`` / ``FILE_SHARE_*`` / ``OPEN_ALWAYS``), archive +
retention constants, the VEH message-buffer layout, and the
pre-allocated ``_crash_msg_buf`` bytearray.

No mutable state lives here — only constants and the pre-allocated
``_crash_msg_buf`` (which is mutated in place by ``_veh_callback`` but
never reassigned, so the module-level reference is effectively
constant).

Split out from the original monolithic ``crash_handler.py`` so the
constants can be imported by the VEH callback, the kernel32 resolver,
and the diagnostics archive without pulling in Windows-only ctypes or
the Python excepthook.
"""

from __future__ import annotations

# Archive + retention constants for crash diagnostics.
_CRASH_DIAGNOSTICS_ARCHIVE = "crash_diagnostics_archive"
_ARCHIVE_RETENTION_KEEP = 5
# YJ-47: sidecar marker suffix used by ``report_pending_crash`` to
# track which archive-subdir files have already been surfaced to the
# user. Pre-fix, VEH wrote to the config_dir root and
# ``report_pending_crash`` MOVED the file to the archive (so the next
# scan found nothing). Post-fix, VEH writes directly to the archive
# subdir — without a sidecar marker, the same file would be re-surfaced
# on every startup. The sidecar is an empty file named
# ``<crash_file>.reported`` sitting next to the crash file.
_REPORTED_SIDECAR_SUFFIX = ".reported"
# Sweep policy for stale ``crash_diagnostics.*.txt`` files left
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

# YJ-42: extended coverage for additional fatal Windows exception codes.
# The original ``_CRASH_CODES`` set (added in XZ-R17-04) only covered 4
# codes — ~11 other fatal exception codes were silently bypassed by the
# VEH handler, producing no crash_diagnostics.<PID>.txt file for triage.
# The codes below extend the set to cover the most common fatal SEH
# exceptions that should trigger crash-diagnostics capture. Debugger
# signals (STATUS_BREAKPOINT=0x80000003, STATUS_SINGLE_STEP=0x80000004)
# are deliberately omitted so an attached debugger doesn't trigger a
# crash record on every breakpoint / single-step trap.
STATUS_ILLEGAL_INSTRUCTION = 0xC000001D
STATUS_INT_DIVIDE_BY_ZERO = 0xC0000094
STATUS_PRIVILEGED_INSTRUCTION = 0xC0000096
STATUS_IN_PAGE_ERROR = 0xC0000006
STATUS_STACK_OVERFLOW = 0xC00000FD
STATUS_NONCONTINUABLE_EXCEPTION = 0xC0000025
STATUS_INVALID_HANDLE = 0xC0000008
STATUS_DATATYPE_MISALIGNMENT = 0xC0000002
STATUS_GUARD_PAGE_VIOLATION = 0x80000001

_CRASH_CODES = frozenset(
    {
        STATUS_HEAP_CORRUPTION,
        STATUS_ACCESS_VIOLATION,
        STATUS_STACK_BUFFER_OVERRUN,
        STATUS_FATAL_APP_EXIT,
        # YJ-42: extended fatal codes. See _NAME_* constants below for
        # the human-readable diagnostic messages mapped to each code.
        STATUS_ILLEGAL_INSTRUCTION,
        STATUS_INT_DIVIDE_BY_ZERO,
        STATUS_PRIVILEGED_INSTRUCTION,
        STATUS_IN_PAGE_ERROR,
        STATUS_STACK_OVERFLOW,
        STATUS_NONCONTINUABLE_EXCEPTION,
        STATUS_INVALID_HANDLE,
        STATUS_DATATYPE_MISALIGNMENT,
        STATUS_GUARD_PAGE_VIOLATION,
    }
)

EXCEPTION_CONTINUE_SEARCH = 0x0

# CreateFileW / WriteFile constants
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x00000080

# Maximum number of top-level module names included in the crash
# diagnostics header.  Picked to bound the file size at ~10 KiB while
# still capturing the long tail of C-extension / package names that
# are most likely to be implicated in a silent crash.
#
# YJ-47: previously 500. The 500-module fingerprint was a PII /
# install-fingerprint exposure in the config_dir root during the
# window between crash (T0) and next startup (T1). Capped at 100
# to bound the exposure while still capturing the long-tail packages.
# Most user-implicating C extensions (whisper, torch, numpy, etc.)
# appear in the first 50 top-level names after sorting alphabetically.
_HEADER_MAX_MODULES = 100

# The VEH buffer layout is data-driven. Each entry is a (label, width)
# tuple where ``width`` is the maximum byte count that segment can
# occupy in the buffer. ``_CRASH_MSG_BUF_SIZE`` is auto-computed as
# the sum of all widths plus a small safety margin so adding/removing
# a field only requires editing this list.
#
# Layout breakdown (matches the byte-by-byte construction in
# ``_vectored_handler_impl``):
#   bom          : 3   (UTF-8 BOM)
#   timestamp    : 23  ("YYYY-MM-DD HH:MM:SS.mmm")
#   crash_label  : 9   ("  CRASH  " — sep+label+sep)
#   code         : 13  ("code=0x" + 8 hex digits)
#   addr         : 25  (", addr=0x" + 16 hex digits)
#   pid          : 17  (", pid=0x" + 8 hex digits)
#   tid          : 17  (", tid=0x" + 8 hex digits)
#   nl1          : 2   ("\r\n")
#   name         : 80  (friendly exception name — longest is
#                       ``_NAME_STACK`` at 60 bytes; 80 leaves headroom)
#   nl2          : 2   ("\r\n")
_CRASH_MSG_LAYOUT: list[tuple[str, int]] = [
    ("bom", 3),
    ("timestamp", 23),
    ("crash_label", 9),
    ("code", 13),
    ("addr", 25),
    ("pid", 17),
    ("tid", 17),
    ("nl1", 2),
    ("name", 80),
    ("nl2", 2),
]
# Auto-compute the crash-message body size from the layout, then add
# headroom so a future field extension doesn't silently truncate the
# write. The original constant was 1024; the layout sum is ~191, so
# the headroom is ample.
_CRASH_MSG_BUF_SIZE = sum(width for _, width in _CRASH_MSG_LAYOUT) + 256

# Pre-allocated crash message buffer. Mutated in place by
# ``_vectored_handler_impl`` (bytearray slice assignment — no heap
# allocation in the VEH callback). The reference itself is never
# reassigned, so importing it as a module-level constant is safe.
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
# YJ-42: friendly names for the extended fatal exception codes. Each
# message is pre-encoded ASCII bytes (no heap allocations in the VEH
# callback). The ``name`` slot in ``_CRASH_MSG_LAYOUT`` is 80 bytes;
# each message below is ≤ 80 bytes to respect that constraint.
_NAME_ILLEGAL_INSTRUCTION = b"STATUS_ILLEGAL_INSTRUCTION: the CPU tried to execute an invalid opcode."
_NAME_INT_DIVIDE_BY_ZERO = b"STATUS_INT_DIVIDE_BY_ZERO: an integer division by zero was attempted."
_NAME_PRIVILEGED_INSTRUCTION = b"STATUS_PRIVILEGED_INSTRUCTION: privileged CPU instruction in user mode."
_NAME_IN_PAGE_ERROR = b"STATUS_IN_PAGE_ERROR: a memory page could not be loaded (disk I/O or quota)."
_NAME_STACK_OVERFLOW = b"STATUS_STACK_OVERFLOW: the thread exhausted its stack."
_NAME_NONCONTINUABLE = b"STATUS_NONCONTINUABLE_EXCEPTION: a non-continuable exception occurred."
_NAME_INVALID_HANDLE = b"STATUS_INVALID_HANDLE: an invalid handle was passed to a kernel API."
_NAME_MISALIGNMENT = b"STATUS_DATATYPE_MISALIGNMENT: a misaligned memory access occurred."
_NAME_GUARD_PAGE = b"STATUS_GUARD_PAGE_VIOLATION: a guard page was touched (stack growth or probe)."
_NAME_UNKNOWN = b"Unknown fatal exception."

# Lookup table for hex digit encoding (pre-computed)
_HEX_CHARS = b"0123456789ABCDEF"
