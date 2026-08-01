"""FR-13 regression: STATUS_GUARD_PAGE_VIOLATION is excluded from ``_CRASH_CODES``.

Pre-FR-13, ``STATUS_GUARD_PAGE_VIOLATION`` (0x80000001, severity=warning,
NON-FATAL) was included in ``_CRASH_CODES``. The VEH callback at
``_veh_callback.py:225-226`` checks the rate-limit flag at callback entry;
``:345`` sets ``_ch._crash_written = True`` after the first crash-record
write (NEVER reset within the process). A single non-fatal
STATUS_GUARD_PAGE_VIOLATION event permanently silenced the VEH for the
rest of the process — real crashes during the same session left no
diagnostic record, breaking the ``report_pending_crash`` -> user
notification loop on next startup.

Post-FR-13, the code is removed from ``_CRASH_CODES``. The constant
itself (``STATUS_GUARD_PAGE_VIOLATION``) and the friendly-name lookup
(``_NAME_GUARD_PAGE``) are RETAINED for back-compat — the VEH callback's
elif branch remains as a defensive no-op (the ``_CRASH_CODES`` gate at
callback entry already filters the code out).
"""

from __future__ import annotations

from voice_typer.server import crash_handler
from voice_typer.server.crash_handler import _constants

# STATUS_GUARD_PAGE_VIOLATION excluded from _CRASH_CODES ────


class TestGuardPageExcluded:
    """``STATUS_GUARD_PAGE_VIOLATION`` is NOT in ``_CRASH_CODES``.

    This is the core FR-13 invariant — without it, a single non-fatal
    guard-page event would set ``_crash_written = True`` and permanently
    silence the VEH for the rest of the process.
    """

    def test_guard_page_violation_not_in_crash_codes(self):
        """FR-13: STATUS_GUARD_PAGE_VIOLATION is NOT in ``_CRASH_CODES``."""
        assert crash_handler.STATUS_GUARD_PAGE_VIOLATION not in crash_handler._CRASH_CODES, (
            "FR-13: STATUS_GUARD_PAGE_VIOLATION (0x80000001) must NOT be in "
            "_CRASH_CODES — it is a warning-level code (stack growth / probe), "
            "not a fatal crash. Including it caused the VEH rate-limit flag to "
            "permanently silence the VEH after a single non-fatal event."
        )

    def test_guard_page_violation_value_unchanged(self):
        """FR-13 back-compat: the constant's VALUE is unchanged.

        The constant is retained so the VEH callback's elif branch
        remains a defensive no-op (the ``_CRASH_CODES`` gate already
        filters it out). External callers and the docstring still
        reference ``0x80000001``.
        """
        assert crash_handler.STATUS_GUARD_PAGE_VIOLATION == 0x80000001

    def test_guard_page_violation_is_warning_severity(self):
        """FR-13 rationale: the high bit (0x80000000) of the NTSTATUS
        layout indicates ``severity=warning`` (0x1 = success, 0x2 = info,
        0x3 = warning, 0xC = error). STATUS_GUARD_PAGE_VIOLATION has
        severity=1 (the high 2 bits are ``0b10`` = 0x8... but the
        Windows NTSTATUS severity field is bits 30-31; 0x80000001 has
        severity=2 = WARNING per the Microsoft NTSTATUS layout), not
        severity=3 (ERROR) like the other ``0xC...`` codes in
        ``_CRASH_CODES``. It does NOT terminate the process — the OS
        uses it for stack-growth probe pages and C-extension guard-page
        probes.
        """
        code = crash_handler.STATUS_GUARD_PAGE_VIOLATION
        # Extract the severity field (bits 30-31).
        severity = (code >> 30) & 0x3
        # 0x80000001 -> severity=2 (WARNING). 0xC... codes -> severity=3 (ERROR).
        assert severity != 0x3, (
            f"FR-13: STATUS_GUARD_PAGE_VIOLATION (0x{code:08X}) has severity "
            f"{severity}, NOT 3 (ERROR) — it is a warning-level code, not a "
            "fatal crash. The 0xC... codes in _CRASH_CODES all have severity=3."
        )

    def test_all_other_codes_still_in_crash_codes(self):
        """FR-13 non-regression: the OTHER 8 YJ-42 extended codes remain
        in ``_CRASH_CODES``. Only STATUS_GUARD_PAGE_VIOLATION was
        removed — the fix is surgical, not a blanket rollback of YJ-42.
        """
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
        """FR-13 non-regression: the original 4 fatal codes are still
        in ``_CRASH_CODES`` (HEAP_CORRUPTION, ACCESS_VIOLATION,
        STACK_BUFFER_OVERRUN, FATAL_APP_EXIT)."""
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
        """FR-13 back-compat: the ``_NAME_GUARD_PAGE`` pre-encoded
        friendly-name byte string is RETAINED so the VEH callback's
        elif branch remains a defensive no-op (the ``_CRASH_CODES``
        gate at callback entry already filters the code out before the
        elif chain is reached). Removing the constant would break the
        ``test_crash_handler_split.py`` back-compat surface check that
        asserts every re-exported name still exists on the facade.
        """
        assert crash_handler._NAME_GUARD_PAGE, (
            "FR-13: _NAME_GUARD_PAGE constant must be RETAINED (the VEH "
            "callback's elif branch references it; removing would break "
            "the back-compat surface test)."
        )
        assert isinstance(crash_handler._NAME_GUARD_PAGE, bytes)
        assert b"STATUS_GUARD_PAGE_VIOLATION" in crash_handler._NAME_GUARD_PAGE


# ─── _constants module surface ────────────────────────────────────────


class TestConstantsModuleSurface:
    """Direct assertions against ``_constants`` (no facade indirection)
    so a future refactor that moves the constant between modules is
    caught."""

    def test_constants_module_excludes_guard_page_from_crash_codes(self):
        assert _constants.STATUS_GUARD_PAGE_VIOLATION not in _constants._CRASH_CODES

    def test_constants_module_retains_guard_page_constant(self):
        assert _constants.STATUS_GUARD_PAGE_VIOLATION == 0x80000001
        assert _constants._NAME_GUARD_PAGE.startswith(b"STATUS_GUARD_PAGE_VIOLATION")
