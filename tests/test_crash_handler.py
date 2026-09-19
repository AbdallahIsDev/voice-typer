"""Tests for ``voice_typer.server.crash_handler`` (CR-78)."""

from __future__ import annotations

import logging
import os
import sys
import textwrap
from pathlib import Path

import pytest
from voice_typer.server import crash_handler


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state():
    """Reset module-level globals between tests."""
    keys = (
        "_crash_file_path",
        "_PID",
        "_handler_handle",
        "_kernel32",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    crash_handler._crash_file_path = ""
    crash_handler._PID = 0
    crash_handler._handler_handle = None
    crash_handler._kernel32 = None
    crash_handler._crash_written = False
    crash_handler._python_crash_dir = None
    crash_handler._crash_header_bytes = b""
    yield
    for k, v in saved.items():
        if v is _UNSET:
            # Attribute didn't exist before the test, delete if created.
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)


# Sentinel used by the autouse fixture to distinguish "attribute was
_UNSET = object()


@pytest.fixture
def restore_excepthook():
    """Snapshot ``sys.excepthook`` so a test can restore it."""
    saved = sys.excepthook
    saved_orig_attr = crash_handler._original_excepthook
    # Setup: reset to a clean state so install_python_excepthook has
    sys.excepthook = sys.__excepthook__
    crash_handler._original_excepthook = None
    yield
    sys.excepthook = saved
    crash_handler._original_excepthook = saved_orig_attr


class TestCrashHandlerConfigDir:
    """``set_crash_handler_config_dir`` caches the config-dir path."""

    def test_set_config_dir_stores_path(self, tmp_path):
        """After ``set_crash_handler_config_dir``, the cached crash-file"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        # The cached path is a Python str ending with a NUL terminator
        assert crash_handler._crash_file_path
        assert "crash_diagnostics" in crash_handler._crash_file_path
        assert str(os.getpid()) in crash_handler._crash_file_path
        assert os.getpid() == crash_handler._PID

    def test_set_config_dir_path_uses_os_path_join(self, tmp_path):
        """G4-L-12: the cached crash-file path uses ``os.path.join`` so"""
        import os.path as _osp

        crash_handler.set_crash_handler_config_dir(tmp_path)
        expected_dir = str(tmp_path.resolve())
        # The cached path is ``<config_dir>/crash_diagnostics.<PID>.txt\0``
        assert crash_handler._crash_file_path.startswith(expected_dir + _osp.sep), (
            f"G4-L-12: cached path should start with config_dir + os.sep, got: {crash_handler._crash_file_path!r}"
        )
        # Trailing NUL terminator preserved for CreateFileW.
        assert crash_handler._crash_file_path.endswith("\0")

    def test_set_config_dir_caches_python_crash_dir(self, tmp_path):
        """config dir as ``_python_crash_dir`` so the Python excepthook"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        assert crash_handler._python_crash_dir is not None
        assert crash_handler._python_crash_dir == tmp_path.resolve()

    def test_set_config_dir_resets_crash_written_flag(self, tmp_path):
        """G4-L-14: ``set_crash_handler_config_dir`` resets the VEH"""
        crash_handler._crash_written = True
        crash_handler.set_crash_handler_config_dir(tmp_path)
        assert crash_handler._crash_written is False

    def test_set_config_dir_idempotent(self, tmp_path):
        """Calling twice with the same dir produces the same cached path."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        first = crash_handler._crash_file_path
        crash_handler.set_crash_handler_config_dir(tmp_path)
        second = crash_handler._crash_file_path
        assert first == second

    def test_set_config_dir_handles_invalid_path(self, monkeypatch):
        """A resolve() failure must NOT crash, fall back to empty path."""

        # Force Path.resolve() to raise.
        class BadPath:
            def resolve(self):
                raise OSError("permission denied")

        # The function catches Exception broadly.
        crash_handler.set_crash_handler_config_dir(BadPath())  # type: ignore[arg-type]
        assert crash_handler._crash_file_path == ""

    def test_yj47_crash_file_path_in_archive_subdir(self, tmp_path):
        """YJ-47: the VEH crash file path is INSIDE"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        path = crash_handler._crash_file_path
        assert path, "YJ-47: _crash_file_path must be non-empty after set_crash_handler_config_dir"
        # Strip the trailing NUL terminator for the path checks.
        path_no_nul = path.rstrip("\0")
        assert "crash_diagnostics" in path_no_nul, (
            f"YJ-47: VEH crash file path must be inside crash_diagnostics/ (was: {path_no_nul!r})"
        )
        # The archive subdir must have been pre-created so the VEH
        archive_dir = tmp_path / "crash_diagnostics"
        assert archive_dir.is_dir(), "YJ-47: set_crash_handler_config_dir must pre-create the archive dir"

    def test_yj47_header_max_modules_is_100(self):
        """YJ-47: ``_HEADER_MAX_MODULES`` is capped at 100 (was 500)."""
        assert crash_handler._HEADER_MAX_MODULES == 100, (
            f"YJ-47: _HEADER_MAX_MODULES must be 100 (was 500); got {crash_handler._HEADER_MAX_MODULES}"
        )

    def test_yj47_report_pending_crash_surfaces_archive_subdir_file(self, tmp_path):
        """surfaces a VEH-written crash file (no longer in the root)."""
        archive_dir = tmp_path / "crash_diagnostics"
        archive_dir.mkdir(parents=True)
        crash_file = archive_dir / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None, "YJ-47: report_pending_crash must surface archive-subdir files"
        assert "Access violation" in result

        # The crash file must still be in the archive (NOT moved out).
        assert crash_file.exists(), "YJ-47: archive-subdir crash file must remain in place after surfacing"
        # A sidecar ``.reported`` marker must be created so the next
        sidecar = archive_dir / "crash_diagnostics.1234.txt.reported"
        assert sidecar.exists(), "YJ-47: report_pending_crash must create a .reported sidecar marker"

    def test_yj47_report_pending_crash_skips_files_with_sidecar(self, tmp_path):
        """YJ-47: ``report_pending_crash`` does NOT re-surface crash"""
        archive_dir = tmp_path / "crash_diagnostics"
        archive_dir.mkdir(parents=True)
        crash_file = archive_dir / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )
        # Pre-create the sidecar marker, the file was already surfaced.
        sidecar = archive_dir / "crash_diagnostics.1234.txt.reported"
        sidecar.touch()

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is None, "YJ-47: report_pending_crash must NOT re-surface files with a .reported sidecar marker"


class TestCrashHandlerReportPending:
    """``report_pending_crash(config_dir)`` scans for leftover diagnostics."""

    def test_returns_none_when_no_file(self, tmp_path):
        """No crash file → return None, no crash logged."""
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path):
        """A non-existent config dir → return None (no raise)."""
        missing = tmp_path / "does-not-exist"
        result = crash_handler.report_pending_crash(missing)
        assert result is None

    def test_reads_file_and_returns_summary(self, tmp_path):
        """A crash file with STATUS_HEAP_CORRUPTION returns the heap summary."""
        crash_file = tmp_path / f"crash_diagnostics.{os.getpid()}.txt"
        crash_file.write_text(
            textwrap.dedent(
                """\
                \ufeff2026-07-14 23:09:48.123  CRASH  code=0xC0000374, addr=0x00007FFA12345678, pid=0x1234, tid=0x5678
                STATUS_HEAP_CORRUPTION: the process heap has been corrupted.
                """
            ),
            encoding="utf-8",
        )

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Heap corruption" in result
        assert "0xC0000374" in result

    def test_reads_file_with_access_violation(self, tmp_path):
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "code=0xC0000005 STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Access violation" in result
        assert "0xC0000005" in result

    def test_reads_file_with_stack_overrun(self, tmp_path):
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_STACK_BUFFER_OVERRUN: a stack buffer overrun was detected.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Stack overrun" in result

    def test_reads_file_with_fatal_app_exit(self, tmp_path):
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_FATAL_APP_EXIT: the application requested termination.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Fatal exit" in result

    def test_yj42_reads_file_with_illegal_instruction(self, tmp_path):
        """YJ-42: ``report_pending_crash`` recognises STATUS_ILLEGAL_INSTRUCTION"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ILLEGAL_INSTRUCTION: the CPU tried to execute an invalid opcode.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Illegal instruction" in result
        assert "0xC000001D" in result

    def test_yj42_reads_file_with_stack_overflow(self, tmp_path):
        """YJ-42: ``report_pending_crash`` recognises STATUS_STACK_OVERFLOW."""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_STACK_OVERFLOW: the thread exhausted its stack.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Stack overflow" in result
        assert "0xC00000FD" in result

    def test_yj42_reads_file_with_in_page_error(self, tmp_path):
        """YJ-42: ``report_pending_crash`` recognises STATUS_IN_PAGE_ERROR."""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_IN_PAGE_ERROR: a memory page could not be loaded.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "In-page error" in result
        assert "0xC0000006" in result

    def test_unknown_crash_code_extracts_code_line(self, tmp_path):
        """An unknown crash code → summary includes the ``code=0x…`` line."""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "CRASH code=0xDEADBEEF, addr=0x00000000, pid=0x1, tid=0x2\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "crash" in result.lower() or "0x" in result.lower()

    def test_empty_crash_file_skipped(self, tmp_path):
        """An empty diagnostics file is silently cleaned up (not reported)."""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text("", encoding="utf-8")

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is None
        # The empty file must still be deleted to prevent re-reporting.
        assert not crash_file.exists()

    def test_deletes_crash_file_after_reading(self, tmp_path):
        """config_dir root (no duplicates on next scan)."""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )

        crash_handler.report_pending_crash(tmp_path)
        assert not crash_file.exists(), "crash file must be moved out of config_dir root after reporting"

        assert crash_handler.report_pending_crash(tmp_path) is None

    def test_crash_diagnosticsd_not_deleted(self, tmp_path):
        """``crash_diagnostics/`` subdirectory rather than unlinked,"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )

        crash_handler.report_pending_crash(tmp_path)

        # Original location is empty, file was moved, not copied.
        assert not crash_file.exists(), "G4-M-33: crash_diagnostics file must be moved out of the config_dir root"

        # Archive directory exists.
        archive_dir = tmp_path / "crash_diagnostics"
        assert archive_dir.is_dir(), "G4-M-33: crash_diagnostics/ directory must exist after processing"

        # Exactly one archived file with the original name and contents.
        archived_files = list(archive_dir.glob("crash_diagnostics.*.txt"))
        assert len(archived_files) == 1, (
            f"expected 1 archived crash_diagnostics file, got {len(archived_files)}: {[f.name for f in archived_files]}"
        )
        assert archived_files[0].name == "crash_diagnostics.1234.txt"
        archived_content = archived_files[0].read_text(encoding="utf-8").strip()
        assert "STATUS_ACCESS_VIOLATION" in archived_content, (
            "G4-M-33: archived file must preserve the original crash record content"
        )

    def test_python_crash_marker_archived_and_surfaced(self, tmp_path):
        """the Python excepthook is surfaced in the startup notification"""
        marker = tmp_path / "python_crash.4321.txt"
        marker.write_text(
            "exc_type=ValueError\nexc_value=test python crash\nthread=MainThread\ntimestamp=2026-07-22T12:34:56.789\n",
            encoding="utf-8",
        )

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Python crash" in result
        assert "ValueError" in result
        # ``exc_value`` is dropped from the user-facing summary
        assert "test python crash" not in result, (
            "XE-7-2: exc_value must NOT be surfaced in the user-facing "
            "crash summary (the value can embed dictated speech / PII). "
            f"Got summary: {result!r}"
        )

        # Original marker is moved out of config_dir root.
        assert not marker.exists()
        # Archived alongside crash_diagnostics.
        archive_dir = tmp_path / "crash_diagnostics"
        archived = list(archive_dir.glob("python_crash.*.txt"))
        assert len(archived) == 1
        archived_content = archived[0].read_text(encoding="utf-8")
        assert "ValueError" in archived_content
        assert "test python crash" in archived_content, (
            "XE-7-2: the on-disk marker file must retain the (redacted) "
            "exc_value even though it's omitted from the user-facing summary."
        )

    def test_archive_retention_keeps_last_five(self, tmp_path):
        """G4-M-33: the archive enforces a keep-last-5 retention policy"""
        archive_dir = tmp_path / "crash_diagnostics"
        archive_dir.mkdir(parents=True)
        # Pre-populate the archive with 8 files (all older than "now").
        import time as _time

        for i in range(8):
            f = archive_dir / f"crash_diagnostics.{1000 + i}.txt"
            f.write_text(f"STATUS_ACCESS_VIOLATION #{i}\r\n", encoding="utf-8")
            # Stagger mtimes so retention can pick the oldest to delete.
            ts = _time.time() - (8 - i)
            os.utime(f, (ts, ts))

        # Now process one more crash file, should trigger retention,
        new_crash = tmp_path / "crash_diagnostics.9999.txt"
        new_crash.write_text("STATUS_ACCESS_VIOLATION: fresh crash\r\n", encoding="utf-8")
        crash_handler.report_pending_crash(tmp_path)

        archived = list(archive_dir.glob("crash_diagnostics.*.txt"))
        assert len(archived) <= 5, f"G4-M-33: archive must keep at most 5 files; got {len(archived)}"
        # The newest crash (just archived) must be one of the survivors.
        names = [f.name for f in archived]
        assert "crash_diagnostics.9999.txt" in names

    def test_sweep_deletes_old_diagnostics_files(self, tmp_path):
        """G4-M-32: crash_diagnostics files older than 30 days are"""
        import time as _time

        # Create an old file (40 days ago).
        old_file = tmp_path / "crash_diagnostics.5555.txt"
        old_file.write_text("STATUS_ACCESS_VIOLATION: ancient\r\n", encoding="utf-8")
        old_ts = _time.time() - (40 * 24 * 60 * 60)
        os.utime(old_file, (old_ts, old_ts))

        # The sweep helper directly deletes files older than 30 days.
        crash_handler._sweep_stale_diagnostics(tmp_path)

        assert not old_file.exists()

    def test_multiple_crash_files_all_reported(self, tmp_path):
        """Multiple crash files (from several crashed sessions) are all read."""
        (tmp_path / "crash_diagnostics.1111.txt").write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )
        (tmp_path / "crash_diagnostics.2222.txt").write_text(
            "STATUS_HEAP_CORRUPTION: the process heap has been corrupted.\r\n",
            encoding="utf-8",
        )

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Access violation" in result
        assert "Heap corruption" in result

    def test_non_crash_file_ignored(self, tmp_path):
        """Files NOT matching ``crash_diagnostics.*.txt`` are ignored."""
        # Wrong prefix.
        (tmp_path / "not_a_crash_file.txt").write_text("hello")
        # Right prefix, wrong extension.
        (tmp_path / "crash_diagnostics.1234.log").write_text("hello")
        # Right pattern, but empty (skipped + deleted).
        (tmp_path / "crash_diagnostics.1234.txt").write_text("")

        result = crash_handler.report_pending_crash(tmp_path)
        assert result is None

    def test_handles_unreadable_file_gracefully(self, tmp_path):
        """A file that raises during read is logged + deleted, no raise."""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text("STATUS_ACCESS_VIOLATION\n", encoding="utf-8")

        # Patch Path.read_text to raise on this specific file.
        original_read_text = Path.read_text

        def boom(self, *args, **kwargs):
            if self == crash_file:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "read_text", boom)
            # Must not raise.
            result = crash_handler.report_pending_crash(tmp_path)
        assert result is None or isinstance(result, str)
        # File must still be deleted (cleanup runs in finally).
        assert not crash_file.exists()


class TestReportPendingCrashNextStepsHint:
    """``report_pending_crash`` appends a ``Next steps: run"""

    def test_summary_includes_next_steps_hint(self, tmp_path):
        """The returned summary contains the ``Next steps:`` line"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Next steps:" in result, f"crash summary must include a 'Next steps:' hint; got:\n{result}"
        assert "python scripts/diagnostics.py export" in result, (
            f"'Next steps:' hint must mention the diagnostics-export CLI command; got:\n{result}"
        )

    def test_summary_next_steps_appears_exactly_once_for_single_crash(self, tmp_path):
        """The ``Next steps`` hint appears EXACTLY ONCE in the summary,"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        occurrences = result.count("Next steps:")
        assert occurrences == 1, f"'Next steps:' must appear exactly once in the summary; got {occurrences}:\n{result}"

    def test_summary_next_steps_appears_exactly_once_for_multiple_crashes(self, tmp_path):
        """the final joined ``summary``, NOT per-file in ``summary_parts``)."""
        (tmp_path / "crash_diagnostics.1111.txt").write_text(
            "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
            encoding="utf-8",
        )
        (tmp_path / "crash_diagnostics.2222.txt").write_text(
            "STATUS_HEAP_CORRUPTION: the process heap has been corrupted.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        # Both crash summaries must still be present.
        assert "Access violation" in result
        assert "Heap corruption" in result
        # And the Next steps hint must appear exactly once.
        occurrences = result.count("Next steps:")
        assert occurrences == 1, (
            f"'Next steps:' must appear exactly once even with multiple crash files; got {occurrences}:\n{result}"
        )

    def test_summary_next_steps_hint_includes_python_command(self, tmp_path):
        """The hint specifically references the ``python`` interpreter"""
        crash_file = tmp_path / "crash_diagnostics.1234.txt"
        crash_file.write_text(
            "STATUS_HEAP_CORRUPTION: the process heap has been corrupted.\r\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "python scripts/diagnostics.py export" in result, (
            f"hint must use the literal 'python scripts/diagnostics.py export' command; got:\n{result}"
        )

    def test_summary_returns_none_still_omits_hint(self, tmp_path):
        """When no crash files exist, ``report_pending_crash`` returns"""
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is None, f"report_pending_crash must return None when no crash files exist; got: {result!r}"

    def test_summary_next_steps_hint_for_python_crash_marker(self, tmp_path):
        """``python_crash.<PID>.txt`` marker files too (not just VEH"""
        marker = tmp_path / "python_crash.4321.txt"
        marker.write_text(
            "exc_type=ValueError\nexc_value=test python crash\nthread=MainThread\ntimestamp=2026-07-22T12:34:56.789\n",
            encoding="utf-8",
        )
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is not None
        assert "Python crash" in result
        assert "Next steps:" in result
        assert "python scripts/diagnostics.py export" in result


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only test")
class TestCrashHandlerPosix:
    """On POSIX, ``install_crash_handler`` is a no-op (VEH is Windows-only)."""

    def test_install_crash_handler_returns_false_on_posix(self):
        """``install_crash_handler`` must return False on POSIX without raising."""
        result = crash_handler.install_crash_handler()
        assert result is False
        # No handle was registered.
        assert crash_handler._handler_handle is None

    def test_install_crash_handler_idempotent_on_posix(self):
        """Calling twice on POSIX still returns False (no state change)."""
        assert crash_handler.install_crash_handler() is False
        assert crash_handler.install_crash_handler() is False

    def test_remove_crash_handler_is_noop_on_posix(self):
        """``remove_crash_handler`` must not raise on POSIX (even when nothing"""
        # Should not raise.
        crash_handler.remove_crash_handler()
        assert crash_handler._handler_handle is None

    def test_remove_crash_handler_after_install_attempt_on_posix(self):
        """Even after a failed install, remove is safe to call."""
        crash_handler.install_crash_handler()
        crash_handler.remove_crash_handler()
        assert crash_handler._handler_handle is None


class TestCrashHandlerConstants:
    """Verify the Windows exception code constants are stable."""

    def test_status_heap_corruption_value(self):
        assert crash_handler.STATUS_HEAP_CORRUPTION == 0xC0000374

    def test_status_access_violation_value(self):
        assert crash_handler.STATUS_ACCESS_VIOLATION == 0xC0000005

    def test_status_stack_buffer_overrun_value(self):
        assert crash_handler.STATUS_STACK_BUFFER_OVERRUN == 0xC0000409

    def test_status_fatal_app_exit_value(self):
        assert crash_handler.STATUS_FATAL_APP_EXIT == 0x40000015

    def test_crash_codes_set_contains_all_four(self):
        """The original four fatal SEH codes are present in ``_CRASH_CODES``."""
        original_four = frozenset(
            {
                crash_handler.STATUS_HEAP_CORRUPTION,
                crash_handler.STATUS_ACCESS_VIOLATION,
                crash_handler.STATUS_STACK_BUFFER_OVERRUN,
                crash_handler.STATUS_FATAL_APP_EXIT,
            }
        )
        assert original_four <= crash_handler._CRASH_CODES, (
            "YJ-42: the original four fatal codes MUST remain in _CRASH_CODES after the extension (superset check)."
        )

    def test_crash_codes_set_contains_extended_codes(self):
        """YJ-42: ``_CRASH_CODES`` covers 8 additional fatal Windows"""
        extended_codes = frozenset(
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
        assert extended_codes <= crash_handler._CRASH_CODES, (
            "YJ-42: the 8 extended fatal codes MUST be in _CRASH_CODES."
        )

    def test_fr13_guard_page_violation_excluded_from_crash_codes(self):
        """FR-13: STATUS_GUARD_PAGE_VIOLATION (0x80000001) is deliberately"""
        assert crash_handler.STATUS_GUARD_PAGE_VIOLATION not in crash_handler._CRASH_CODES, (
            "FR-13: STATUS_GUARD_PAGE_VIOLATION must NOT be in _CRASH_CODES, "
            "it is a warning-level code, not a fatal crash."
        )

    def test_crash_codes_excludes_breakpoint_and_single_step(self):
        """YJ-42: STATUS_BREAKPOINT (0x80000003) and STATUS_SINGLE_STEP"""
        assert 0x80000003 not in crash_handler._CRASH_CODES
        assert 0x80000004 not in crash_handler._CRASH_CODES

    def test_exception_continue_search_is_zero(self):
        """VEH callbacks return ``EXCEPTION_CONTINUE_SEARCH`` (=0) to let"""
        assert crash_handler.EXCEPTION_CONTINUE_SEARCH == 0x0

    def test_yj42_extended_codes_have_friendly_names(self):
        """corresponding pre-encoded ``_NAME_*`` byte string for the VEH"""
        # Map each extended STATUS_* code to its expected _NAME_* bytes.
        yj42_mapping = {
            crash_handler.STATUS_ILLEGAL_INSTRUCTION: crash_handler._NAME_ILLEGAL_INSTRUCTION,
            crash_handler.STATUS_INT_DIVIDE_BY_ZERO: crash_handler._NAME_INT_DIVIDE_BY_ZERO,
            crash_handler.STATUS_PRIVILEGED_INSTRUCTION: crash_handler._NAME_PRIVILEGED_INSTRUCTION,
            crash_handler.STATUS_IN_PAGE_ERROR: crash_handler._NAME_IN_PAGE_ERROR,
            crash_handler.STATUS_STACK_OVERFLOW: crash_handler._NAME_STACK_OVERFLOW,
            crash_handler.STATUS_NONCONTINUABLE_EXCEPTION: crash_handler._NAME_NONCONTINUABLE,
            crash_handler.STATUS_INVALID_HANDLE: crash_handler._NAME_INVALID_HANDLE,
            crash_handler.STATUS_DATATYPE_MISALIGNMENT: crash_handler._NAME_MISALIGNMENT,
        }
        for code, name in yj42_mapping.items():
            assert isinstance(name, bytes), (
                f"YJ-42: _NAME_* for code {code:#x} must be pre-encoded bytes "
                f"(VEH callback cannot allocate during heap corruption)"
            )
            assert code in crash_handler._CRASH_CODES, f"YJ-42: STATUS_* code {code:#x} must be in _CRASH_CODES"
            assert len(name) <= 80, (
                f"YJ-42: _NAME_* for code {code:#x} is {len(name)} bytes "
                f"(exceeds 80-byte name slot in _CRASH_MSG_LAYOUT)"
            )


class TestHexEncoders:
    """``_write_u32_hex`` + ``_write_u64_hex`` produce fixed-width hex."""

    def test_u32_hex_writes_8_digits(self):
        buf = bytearray(16)
        n = crash_handler._write_u32_hex(0xDEADBEEF, buf, 0)
        assert n == 8
        assert bytes(buf[:8]) == b"DEADBEEF"

    def test_u32_hex_zero(self):
        buf = bytearray(16)
        crash_handler._write_u32_hex(0, buf, 0)
        assert bytes(buf[:8]) == b"00000000"

    def test_u32_hex_max(self):
        buf = bytearray(16)
        crash_handler._write_u32_hex(0xFFFFFFFF, buf, 0)
        assert bytes(buf[:8]) == b"FFFFFFFF"

    def test_u64_hex_writes_16_digits(self):
        buf = bytearray(32)
        n = crash_handler._write_u64_hex(0x123456789ABCDEF0, buf, 0)
        assert n == 16
        assert bytes(buf[:16]) == b"123456789ABCDEF0"

    def test_u64_hex_zero(self):
        buf = bytearray(32)
        crash_handler._write_u64_hex(0, buf, 0)
        assert bytes(buf[:16]) == b"0000000000000000"

    def test_u64_hex_max(self):
        buf = bytearray(32)
        crash_handler._write_u64_hex(0xFFFFFFFFFFFFFFFF, buf, 0)
        assert bytes(buf[:16]) == b"FFFFFFFFFFFFFFFF"

    def test_hex_writes_at_offset(self):
        """Both encoders must respect the ``offset`` argument."""
        buf = bytearray(32)
        crash_handler._write_u32_hex(0xABCD1234, buf, 4)
        # Bytes 0-3 untouched, 4-11 hold the hex, 12+ untouched.
        assert bytes(buf[:4]) == b"\x00\x00\x00\x00"
        assert bytes(buf[4:12]) == b"ABCD1234"


class TestPythonExcepthook:
    """``install_python_excepthook`` swaps ``sys.excepthook``."""

    def test_install_sets_custom_excepthook(self, restore_excepthook):
        """After install, ``sys.excepthook`` is the crash_handler's hook."""
        original = sys.excepthook
        crash_handler.install_python_excepthook()
        assert sys.excepthook is crash_handler._crash_excepthook
        assert sys.excepthook is not original

    def test_install_is_idempotent(self, restore_excepthook):
        """Calling install twice does NOT re-save the original (the second"""
        crash_handler.install_python_excepthook()
        saved_once = crash_handler._original_excepthook
        crash_handler.install_python_excepthook()
        saved_twice = crash_handler._original_excepthook
        assert saved_once is saved_twice

    def test_crash_excepthook_logs_and_chains(self, restore_excepthook, caplog):
        """The custom hook logs the exception then chains to the original."""
        original_called: list[bool] = []

        def fake_original(*args, **kwargs):
            original_called.append(True)

        sys.excepthook = fake_original  # type: ignore[assignment]
        crash_handler.install_python_excepthook()
        # Sanity: the crash hook was installed.
        assert sys.excepthook is crash_handler._crash_excepthook

        try:
            raise ValueError("test crash")
        except ValueError as exc:
            with caplog.at_level(logging.CRITICAL, logger="voice_typer"):
                sys.excepthook(type(exc), exc, exc.__traceback__)

        # The hook must have logged at CRITICAL level.
        assert any("Unhandled Python exception" in r.message for r in caplog.records)
        # The original hook must have been chained to.
        assert original_called == [True]

    def test_crash_excepthook_swallows_errors_in_original(self, restore_excepthook):
        """If the original hook raises, the crash hook must not propagate."""

        def boom(*args, **kwargs):
            raise RuntimeError("original hook broken")

        # See test_crash_excepthook_logs_and_chains: we must set
        sys.excepthook = boom  # type: ignore[assignment]
        crash_handler.install_python_excepthook()

        try:
            raise ValueError("test")
        except ValueError as exc:
            # Must not raise, the original-hook failure is suppressed.
            sys.excepthook(type(exc), exc, exc.__traceback__)

    def test_remove_restores_original(self, restore_excepthook):
        """``remove_python_excepthook`` restores ``sys.excepthook``"""
        original = sys.excepthook
        crash_handler.install_python_excepthook()
        assert sys.excepthook is crash_handler._crash_excepthook
        crash_handler.remove_python_excepthook()
        assert sys.excepthook is original

    def test_remove_is_idempotent(self, restore_excepthook):
        """
        ``_original_excepthook`` slot was never set).
        This is the test contract that proves the new function does
        """
        crash_handler.remove_python_excepthook()
        assert sys.excepthook is not None

    def test_remove_then_reinstall_roundtrip(self, restore_excepthook):
        """the full install→remove→install roundtrip works."""
        original = sys.excepthook
        crash_handler.install_python_excepthook()
        crash_handler.remove_python_excepthook()
        assert sys.excepthook is original
        crash_handler.install_python_excepthook()
        assert sys.excepthook is crash_handler._crash_excepthook
        # The second install must NOT save the crash hook as the
        assert crash_handler._original_excepthook is original


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only, handler is None")
class TestVectoredHandlerPosix:
    """On POSIX the VEH callback wrapper is None (no SEH exceptions)."""

    def test_vectored_handler_is_none_on_posix(self):
        """``_vectored_handler`` is None on POSIX: see WP-1 comment in"""
        assert crash_handler._vectored_handler is None

    def test_vectored_handler_impl_continues_search_on_posix(self):
        """Calling the impl directly returns EXCEPTION_CONTINUE_SEARCH."""
        # Build a fake EXCEPTION_POINTERS that yields a benign code.
        result = crash_handler._vectored_handler_impl(None)
        assert result == crash_handler.EXCEPTION_CONTINUE_SEARCH


class TestPythonCrashMarkerTraceback:
    """``_crash_excepthook`` writes a redacted traceback and static"""

    def test_marker_contains_traceback_line(self, restore_excepthook, tmp_path):
        """The marker file contains a line starting with"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            raise ValueError("boom")
        except ValueError as exc:
            crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        assert marker.exists(), "GT-4: python_crash marker must be written"
        content = marker.read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" in content, (
            f"GT-4: marker must include the traceback header; got:\n{content}"
        )

    def test_marker_traceback_uses_basename_only(self, restore_excepthook, tmp_path):
        """traceback frames use only the file basename."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            raise ValueError("boom")
        except ValueError as exc:
            crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        content = marker.read_text(encoding="utf-8")
        tb_section = content.split("\n\n", 1)[1] if "\n\n" in content else ""
        assert tb_section, "GT-4: marker must contain a traceback section"
        import re as _re

        frame_lines = [line for line in tb_section.splitlines() if line.startswith('  File "')]
        assert frame_lines, f"GT-4: traceback must contain at least one frame line; got:\n{tb_section}"
        for line in frame_lines:
            m = _re.match(r'  File "([^"]+)", line (\d+), in (\S+)', line)
            assert m is not None, f"GT-4: malformed frame line: {line!r}"
            file_path = m.group(1)
            assert "/" not in file_path and "\\" not in file_path, (
                f"GT-4: frame file must be basename only; got: {file_path!r}"
            )

    def test_marker_includes_app_python_os_version_and_asr_backend(self, restore_excepthook, tmp_path):
        """the marker carries app_version, python_version,"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            raise ValueError("boom")
        except ValueError as exc:
            crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        content = marker.read_text(encoding="utf-8")
        for key in ("app_version=", "python_version=", "os_version=", "asr_backend="):
            assert key in content, f"GT-4: marker must include '{key}' line; got:\n{content}"
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in content, (
            "GT-4: python_version value must include the running Python major.minor"
        )

    def test_marker_traceback_excludes_source_line(self, restore_excepthook, tmp_path):
        """traceback frames must NOT include the source code line."""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        try:
            x = "secret-user-data"  # noqa: F841
            raise ValueError(x)
        except ValueError as exc:
            crash_handler._crash_excepthook(type(exc), exc, exc.__traceback__)
        marker = tmp_path / f"python_crash.{os.getpid()}.txt"
        content = marker.read_text(encoding="utf-8")
        # must NOT appear in the marker.  `x = "secret-user-data"`
        assert 'x = "secret-user-data"' not in content, (
            "GT-4: traceback must NOT include the source line (which "
            "carries argument values / user data); got:\n" + content
        )

    def test_format_redacted_traceback_returns_empty_for_none(self):
        """``_format_redacted_traceback(None)`` returns ``\"\"``."""
        assert crash_handler._format_redacted_traceback(None) == ""

    def test_format_redacted_traceback_includes_frame_func(self):
        """a real traceback yields at least one frame line with the function name."""

        def _outer_frame():
            raise RuntimeError("test")

        try:
            _outer_frame()
        except RuntimeError as exc:
            tb_text = crash_handler._format_redacted_traceback(exc.__traceback__)
        assert "Traceback (most recent call last)" in tb_text
        assert "_outer_frame" in tb_text


class TestCrashDiagnosticsHeader:
    """at ``set_crash_handler_config_dir()`` time, a static header"""

    def test_set_config_dir_precomputes_header(self, tmp_path):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        assert crash_handler._crash_header_bytes, (
            "GT-7: _crash_header_bytes must be non-empty after set_crash_handler_config_dir()"
        )

    def test_header_includes_app_version(self, tmp_path):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        assert "App version:" in header, f"GT-7: header must include 'App version:' line; got:\n{header}"

    def test_header_includes_python_version(self, tmp_path):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        assert "Python:" in header
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in header, (
            "GT-7: header Python version must match the running interpreter"
        )

    def test_header_includes_os_version(self, tmp_path):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        assert "OS:" in header, f"GT-7: header must include 'OS:' line; got:\n{header}"

    def test_header_includes_loaded_modules_snapshot(self, tmp_path):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        assert "Loaded modules" in header, f"GT-7: header must include 'Loaded modules' section; got:\n{header}"
        assert "voice_typer" in header, "GT-7: header module snapshot must include 'voice_typer'"

    def test_header_is_recomputed_on_each_set_config_dir(self, tmp_path):
        crash_handler.set_crash_handler_config_dir(tmp_path)
        first = crash_handler._crash_header_bytes
        crash_handler.set_crash_handler_config_dir(tmp_path)
        second = crash_handler._crash_header_bytes
        assert first and second
        assert first == second, "GT-7: header should be deterministic for the same module set"

    def test_compute_crash_header_returns_bytes(self):
        header = crash_handler._compute_crash_header()
        assert isinstance(header, bytes), f"GT-7: _compute_crash_header must return bytes; got {type(header).__name__}"
        assert header.decode("utf-8", errors="replace")

    def test_header_includes_reproduction_hint(self, tmp_path):
        """``Reproduction hint`` line telling the user / support engineer"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        assert "Reproduction hint:" in header, f"header must include a 'Reproduction hint:' line; got:\n{header}"
        assert "python scripts/diagnostics.py export" in header, (
            f"reproduction hint must mention the diagnostics-export CLI command; got:\n{header}"
        )

    def test_header_reproduction_hint_appears_before_end_marker(self, tmp_path):
        """end marker doesn't miss it.  Layout regression guard against"""
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        hint_idx = header.find("Reproduction hint:")
        end_idx = header.find("=== END HEADER ===")
        assert hint_idx != -1 and end_idx != -1, (
            f"header must contain both 'Reproduction hint:' and '=== END HEADER ==='; got:\n{header}"
        )
        assert hint_idx < end_idx, (
            f"'Reproduction hint:' must appear BEFORE '=== END HEADER ==='; got hint_idx={hint_idx}, end_idx={end_idx}"
        )

    def test_header_includes_windows_version_on_windows(self, monkeypatch, tmp_path):
        """on Windows, the header includes a ``Windows version:``"""
        # Simulate the Windows-only ``sys.getwindowsversion`` attribute.
        fake_ver = (10, 0, 22621, 2, "SP0", 0, 0, 256, 1)

        def _fake_getwindowsversion():
            return fake_ver

        monkeypatch.setattr(sys, "getwindowsversion", _fake_getwindowsversion, raising=False)
        try:
            crash_handler.set_crash_handler_config_dir(tmp_path)
            header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
            assert "Windows version:" in header, (
                f"header must include 'Windows version:' line when sys.getwindowsversion is available; got:\n{header}"
            )
            assert "22621" in header, (
                f"'Windows version:' line must include the build number from sys.getwindowsversion(); got:\n{header}"
            )
        finally:
            # ``monkeypatch`` restores ``sys.getwindowsversion`` automatically
            if hasattr(sys, "getwindowsversion") and not callable(getattr(sys, "getwindowsversion", None)):
                # Defensive, should never trigger; left as a safety net.
                delattr(sys, "getwindowsversion")

    def test_header_omits_windows_version_on_posix(self, tmp_path):
        """on POSIX (where ``sys.getwindowsversion`` does not"""
        if hasattr(sys, "getwindowsversion"):
            pytest.skip("Windows host, sys.getwindowsversion exists; this test is POSIX-only")
        crash_handler.set_crash_handler_config_dir(tmp_path)
        header = crash_handler._crash_header_bytes.decode("utf-8", errors="replace")
        assert "Windows version:" not in header, (
            f"header must NOT include 'Windows version:' line on POSIX (no sys.getwindowsversion); got:\n{header}"
        )


class TestCrashBufferLayout:
    """``(label, width)`` tuples and ``_CRASH_MSG_BUF_SIZE`` is auto-computed."""

    def test_layout_is_list_of_label_width_tuples(self):
        layout = crash_handler._CRASH_MSG_LAYOUT
        assert isinstance(layout, list) and layout, "layout must be a non-empty list"
        for entry in layout:
            assert isinstance(entry, tuple) and len(entry) == 2, f"each layout entry must be a 2-tuple; got {entry!r}"
            label, width = entry
            assert isinstance(label, str) and label, f"layout label must be a non-empty str; got {label!r}"
            assert isinstance(width, int) and width > 0, f"layout width must be a positive int; got {width!r}"

    def test_buffer_size_matches_layout_sum_plus_margin(self):
        layout_sum = sum(width for _, width in crash_handler._CRASH_MSG_LAYOUT)
        assert layout_sum < crash_handler._CRASH_MSG_BUF_SIZE, (
            f"GT-B2-14: buffer size ({crash_handler._CRASH_MSG_BUF_SIZE}) "
            f"must exceed layout sum ({layout_sum}) to provide headroom"
        )

    def test_buffer_size_accommodates_full_layout(self):
        layout_sum = sum(width for _, width in crash_handler._CRASH_MSG_LAYOUT)
        assert len(crash_handler._crash_msg_buf) >= layout_sum, (
            f"GT-B2-14: _crash_msg_buf ({len(crash_handler._crash_msg_buf)} bytes) "
            f"must be >= layout sum ({layout_sum} bytes)"
        )

    def test_layout_includes_all_required_segments(self):
        labels = {label for label, _ in crash_handler._CRASH_MSG_LAYOUT}
        for required in ("bom", "timestamp", "crash_label", "code", "addr", "pid", "tid", "name"):
            assert required in labels, f"GT-B2-14: layout must include '{required}' segment; got {sorted(labels)}"


class TestCrashBufferUsesSecureRotatingFileHandler:
    """secure variant from ``voice_typer.server.log``, never a stock"""

    @pytest.fixture(autouse=True)
    def _reset_memory_buffer(self):
        """Remove any MemoryHandler installed by a previous test so"""
        from voice_typer.server.crash_handler._memory_buffer import (
            uninstall_memory_buffer,
        )

        uninstall_memory_buffer()
        yield
        uninstall_memory_buffer()

    def test_target_handler_is_secure_rotating_file_handler(self, tmp_path: Path):
        """When the ``log`` package is importable (the normal case),"""
        from voice_typer.server.crash_handler._memory_buffer import (
            install_memory_buffer,
        )
        from voice_typer.server.log import _SecureTruncatingFileHandler

        install_memory_buffer(tmp_path)

        assert crash_handler._crash_buffer_handler is not None, "target RotatingFileHandler must be installed"
        assert isinstance(crash_handler._crash_buffer_handler, _SecureTruncatingFileHandler), (
            "crash-buffer target must be _SecureTruncatingFileHandler "
            f"(got {type(crash_handler._crash_buffer_handler).__name__}); "
            "the insecure stock RotatingFileHandler fallback must NOT be used"
        )

    def test_target_handler_is_not_stock_rotating_file_handler(self, tmp_path: Path):
        """Defense-in-depth: even though ``_SecureTruncatingFileHandler``"""
        from voice_typer.server.crash_handler._memory_buffer import (
            install_memory_buffer,
        )
        from voice_typer.server.log import _SecureTruncatingFileHandler

        install_memory_buffer(tmp_path)

        assert crash_handler._crash_buffer_handler is not None
        # The concrete type must be the secure subclass (or a subclass
        assert type(crash_handler._crash_buffer_handler) is not logging.handlers.RotatingFileHandler, (
            "crash-buffer target must not be a stock RotatingFileHandler; "
            "it must be _SecureTruncatingFileHandler (or a subclass)"
        )
        assert isinstance(crash_handler._crash_buffer_handler, _SecureTruncatingFileHandler)

    def test_no_insecure_fallback_when_secure_handler_unavailable(self, tmp_path: Path, monkeypatch):
        """If the secure handler import fails, ``install_memory_buffer``"""
        import voice_typer.server.log as _log_pkg
        from voice_typer.server.crash_handler._memory_buffer import (
            install_memory_buffer,
        )

        # Hide the secure handler from the package so the hoisted
        monkeypatch.delattr(_log_pkg, "_SecureTruncatingFileHandler", raising=False)

        with pytest.raises(ImportError):
            install_memory_buffer(tmp_path)

        # The insecure fallback must NOT have been installed.
        assert crash_handler._crash_buffer_handler is None, (
            "crash-buffer target must NOT be installed when the secure "
            "handler import fails, no insecure stock RotatingFileHandler "
            "fallback is permitted"
        )
