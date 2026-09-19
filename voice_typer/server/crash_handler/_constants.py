"""Crash-handler constants."""

from __future__ import annotations

# Archive + retention constants for crash diagnostics.
_CRASH_DIAGNOSTICS_DIR = "crash_diagnostics"
_LEGACY_CRASH_DIAGNOSTICS_DIR = "crash_diagnostics_archive"
_ARCHIVE_RETENTION_KEEP = 5
# sidecar marker suffix used by ``report_pending_crash`` to
_REPORTED_SIDECAR_SUFFIX = ".reported"
# Sweep policy for stale ``crash_diagnostics.*.txt`` files left
_MAX_ACTIVE_FILES = 10
_MAX_AGE_DAYS = 30
_MAX_AGE_SECONDS = _MAX_AGE_DAYS * 24 * 60 * 60


STATUS_HEAP_CORRUPTION = 0xC0000374
STATUS_ACCESS_VIOLATION = 0xC0000005
STATUS_STACK_BUFFER_OVERRUN = 0xC0000409
STATUS_FATAL_APP_EXIT = 0x40000015

# extended coverage for additional fatal Windows exception codes.
STATUS_ILLEGAL_INSTRUCTION = 0xC000001D
STATUS_INT_DIVIDE_BY_ZERO = 0xC0000094
STATUS_PRIVILEGED_INSTRUCTION = 0xC0000096
STATUS_IN_PAGE_ERROR = 0xC0000006
STATUS_STACK_OVERFLOW = 0xC00000FD
STATUS_NONCONTINUABLE_EXCEPTION = 0xC0000025
STATUS_INVALID_HANDLE = 0xC0000008
STATUS_DATATYPE_MISALIGNMENT = 0xC0000002
STATUS_GUARD_PAGE_VIOLATION = 0x80000001

# STATUS_GUARD_PAGE_VIOLATION (0x80000001) is deliberately
_CRASH_CODES = frozenset(
    {
        STATUS_HEAP_CORRUPTION,
        STATUS_ACCESS_VIOLATION,
        STATUS_STACK_BUFFER_OVERRUN,
        STATUS_FATAL_APP_EXIT,
        # extended fatal codes. See _NAME_* constants below for
        STATUS_ILLEGAL_INSTRUCTION,
        STATUS_INT_DIVIDE_BY_ZERO,
        STATUS_PRIVILEGED_INSTRUCTION,
        STATUS_IN_PAGE_ERROR,
        STATUS_STACK_OVERFLOW,
        STATUS_NONCONTINUABLE_EXCEPTION,
        STATUS_INVALID_HANDLE,
        STATUS_DATATYPE_MISALIGNMENT,
        # STATUS_GUARD_PAGE_VIOLATION intentionally NOT listed —
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
_HEADER_MAX_MODULES = 100

# The VEH buffer layout is data-driven. Each entry is a (label, width)
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
_CRASH_MSG_BUF_SIZE = sum(width for _, width in _CRASH_MSG_LAYOUT) + 256

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
# friendly names for the extended fatal exception codes. Each
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

# unified exception-code → (name_bytes, summary_str) lookup table.
_CODE_TO_INFO: dict[int, tuple[bytes, str]] = {
    STATUS_HEAP_CORRUPTION: (_NAME_HEAP, "Process heap corrupted"),
    STATUS_ACCESS_VIOLATION: (_NAME_ACCESS, "Invalid memory access"),
    STATUS_STACK_BUFFER_OVERRUN: (_NAME_STACK, "Stack buffer overrun detected"),
    STATUS_FATAL_APP_EXIT: (_NAME_FATAL, "Application requested fatal exit"),
    STATUS_ILLEGAL_INSTRUCTION: (_NAME_ILLEGAL_INSTRUCTION, "CPU invalid opcode"),
    STATUS_INT_DIVIDE_BY_ZERO: (_NAME_INT_DIVIDE_BY_ZERO, "Integer division by zero"),
    STATUS_PRIVILEGED_INSTRUCTION: (_NAME_PRIVILEGED_INSTRUCTION, "Privileged instruction in user mode"),
    STATUS_IN_PAGE_ERROR: (_NAME_IN_PAGE_ERROR, "Memory page I/O error"),
    STATUS_STACK_OVERFLOW: (_NAME_STACK_OVERFLOW, "Thread stack exhausted"),
    STATUS_NONCONTINUABLE_EXCEPTION: (_NAME_NONCONTINUABLE, "Non-continuable exception"),
    STATUS_INVALID_HANDLE: (_NAME_INVALID_HANDLE, "Invalid kernel handle"),
    STATUS_DATATYPE_MISALIGNMENT: (_NAME_MISALIGNMENT, "Misaligned memory access"),
    STATUS_GUARD_PAGE_VIOLATION: (_NAME_GUARD_PAGE, "Guard page touched (stack growth)"),
}

# Lookup table for hex digit encoding (pre-computed)
_HEX_CHARS = b"0123456789ABCDEF"

# user-facing summary strings keyed by NTSTATUS code.
_CODE_TO_USER_SUMMARY: dict[int, str] = {
    STATUS_HEAP_CORRUPTION: (
        "Heap corruption (0xC0000374). Likely cause: low memory (RAM), low disk space, or a C extension bug."
    ),
    STATUS_ACCESS_VIOLATION: ("Access violation (0xC0000005). Likely cause: low memory or a C extension bug."),
    STATUS_STACK_BUFFER_OVERRUN: (
        "Stack overrun (0xC0000409). Likely cause: low memory, stack overflow, or a C extension bug."
    ),
    STATUS_FATAL_APP_EXIT: (
        "Fatal exit (0x40000015). The process detected a critical "
        "error and terminated itself. Likely cause: low memory "
        "or a C extension bug."
    ),
    STATUS_ILLEGAL_INSTRUCTION: (
        "Illegal instruction (0xC000001D). The CPU executed an invalid opcode, "
        "likely a C extension ABI mismatch or a corrupted code page."
    ),
    STATUS_INT_DIVIDE_BY_ZERO: (
        "Integer divide by zero (0xC0000094). A C extension performed an unprotected integer division by zero."
    ),
    STATUS_PRIVILEGED_INSTRUCTION: (
        "Privileged instruction (0xC0000096). User-mode code executed a "
        "kernel-only CPU instruction, likely a C extension bug."
    ),
    STATUS_IN_PAGE_ERROR: (
        "In-page error (0xC0000006). The OS could not load a memory page, "
        "likely low disk space, disk failure, or quota exhaustion."
    ),
    STATUS_STACK_OVERFLOW: (
        "Stack overflow (0xC00000FD). The thread exhausted its stack, likely unbounded recursion or a C extension bug."
    ),
    STATUS_NONCONTINUABLE_EXCEPTION: (
        "Non-continuable exception (0xC0000025). The process attempted to "
        "continue after a non-continuable exception, likely a C extension bug."
    ),
    STATUS_INVALID_HANDLE: (
        "Invalid handle (0xC0000008). A kernel API received an invalid handle, "
        "likely a C extension bug or a race during shutdown."
    ),
    STATUS_DATATYPE_MISALIGNMENT: (
        "Datatype misalignment (0xC0000002). A misaligned memory access occurred, "
        "likely a C extension bug on an aligned-memory ABI."
    ),
    STATUS_GUARD_PAGE_VIOLATION: (
        "Guard page violation (0x80000001). A guard page was touched, "
        "likely stack growth or a probe from a C extension."
    ),
}
