"""FR-13 regression: STATUS_GUARD_PAGE_VIOLATION is excluded from ``_CRASH_CODES``."""

from __future__ import annotations

from voice_typer.server import crash_handler
from voice_typer.server.crash_handler import _constants

# STATUS_GUARD_PAGE_VIOLATION excluded from _CRASH_CODES ────


class TestGuardPageExcluded:
    """``STATUS_GUARD_PAGE_VIOLATION`` is NOT in ``_CRASH_CODES``."""

    def test_guard_page_violation_not_in_crash_codes(self):
        """FR-13: STATUS_GUARD_PAGE_VIOLATION is NOT in ``_CRASH_CODES``."""
        assert crash_handler.STATUS_GUARD_PAGE_VIOLATION not in crash_handler._CRASH_CODES, (
            "FR-13: STATUS_GUARD_PAGE_VIOLATION (0x80000001) must NOT be in "
            "_CRASH_CODES, it is a warning-level code (stack growth / probe), "
            "not a fatal crash. Including it caused the VEH rate-limit flag to "
            "permanently silence the VEH after a single non-fatal event."
        )

    def test_guard_page_violation_value_unchanged(self):
        """FR-13 back-compat: the constant's VALUE is unchanged."""
        assert crash_handler.STATUS_GUARD_PAGE_VIOLATION == 0x80000001

    def test_guard_page_violation_is_warning_severity(self):
        """FR-13 rationale: the high bit (0x80000000) of the NTSTATUS"""
        code = crash_handler.STATUS_GUARD_PAGE_VIOLATION
        # Extract the severity field (bits 30-31).
        severity = (code >> 30) & 0x3
        # 0x80000001 -> severity=2 (WARNING). 0xC... codes -> severity=3 (ERROR).
        assert severity != 0x3, (
            f"FR-13: STATUS_GUARD_PAGE_VIOLATION (0x{code:08X}) has severity "
            f"{severity}, NOT 3 (ERROR), it is a warning-level code, not a "
            "fatal crash. The 0xC... codes in _CRASH_CODES all have severity=3."
        )

    def test_all_other_codes_still_in_crash_codes(self):
        """FR-13 non-regression: the OTHER 8 YJ-42 extended codes remain"""
        remaining_extended = frozenset(
            {
                crash_handler.STATUS_ILLEGAL_INSTRUCTION,
                crash_handler.STATUS_INT_DIVIDE_BY_ZERO,
                crash_handler.STATUS_PRIVILEGED_INSTRUCTION,
                crash_handler.STATUS_IN_PAGE_ERROR,
                crash_handler.STATUS_STACK_OVERFLOW,
                crash_handler.STATUS_NONCONTINUABLE_EXCEPTION,
                crash_handler.STATUS_INVALID_HANDLE,
                crash_handler.STATUS_DATATYPE_MISALIGNMENT,
            }
        )
        assert remaining_extended <= crash_handler._CRASH_CODES, (
            "FR-13: the 8 fatal YJ-42 codes MUST remain in _CRASH_CODES. Only STATUS_GUARD_PAGE_VIOLATION was removed."
        )

    def test_original_four_codes_still_in_crash_codes(self):
        """FR-13 non-regression: the original 4 fatal codes are still"""
        original_four = frozenset(
            {
                crash_handler.STATUS_HEAP_CORRUPTION,
                crash_handler.STATUS_ACCESS_VIOLATION,
                crash_handler.STATUS_STACK_BUFFER_OVERRUN,
                crash_handler.STATUS_FATAL_APP_EXIT,
            }
        )
        assert original_four <= crash_handler._CRASH_CODES

    def test_guard_page_friendly_name_retained(self):
        """FR-13 back-compat: the ``_NAME_GUARD_PAGE`` pre-encoded"""
        assert crash_handler._NAME_GUARD_PAGE, (
            "FR-13: _NAME_GUARD_PAGE constant must be RETAINED (the VEH "
            "callback's elif branch references it; removing would break "
            "the back-compat surface test)."
        )
        assert isinstance(crash_handler._NAME_GUARD_PAGE, bytes)
        assert b"STATUS_GUARD_PAGE_VIOLATION" in crash_handler._NAME_GUARD_PAGE


class TestConstantsModuleSurface:
    """Direct assertions against ``_constants`` (no facade indirection)"""

    def test_constants_module_excludes_guard_page_from_crash_codes(self):
        assert _constants.STATUS_GUARD_PAGE_VIOLATION not in _constants._CRASH_CODES

    def test_constants_module_retains_guard_page_constant(self):
        assert _constants.STATUS_GUARD_PAGE_VIOLATION == 0x80000001
        assert _constants._NAME_GUARD_PAGE.startswith(b"STATUS_GUARD_PAGE_VIOLATION")
