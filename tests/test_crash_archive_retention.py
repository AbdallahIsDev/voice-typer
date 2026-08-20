"""Regression tests for VEH crash-diagnostics archive retention + truncate.

Covers two related disk-bounding fixes:

  - **AP-39** — ``_enforce_archive_retention`` was only invoked from
    ``_archive_crash_file()`` for ROOT-level crash files; archive-subdir
    files (where the VEH callback writes directly) were never bounded,
    so the archive grew unbounded across crashes. The fix:
      1. ``report_pending_crash`` now calls ``_enforce_archive_retention``
         on the archive subdir (with an ``if archive_dir.exists():`` guard
         for the first-run case).
      2. ``_sweep_stale_diagnostics`` now globs the archive subdir too
         (it previously only walked the config_dir root) so the 30-day
         mtime cutoff + keep-last-``_MAX_ACTIVE_FILES`` cap also applies
         to VEH-written archive files.

  - **AP-40** — ``_write_to_file`` opened the crash file with
    ``OPEN_ALWAYS`` + ``SetFilePointer(FILE_END)``, which APPENDED to any
    pre-existing file at the same path. When the OS recycled a PID and
    the recycled process also crashed, the new ~10 KiB crash record was
    concatenated onto the stale one. The fix replaces ``OPEN_ALWAYS``
    with ``CREATE_ALWAYS`` (truncates) and removes the seek-to-end call,
    matching the Python excepthook's ``O_WRONLY | O_CREAT | O_TRUNC``
    semantics.

These tests are Linux-runnable: the AP-40 tests mock the kernel32
function pointers (``_func_create_file_w`` etc.) on the
``crash_handler`` facade so the write path runs headless; the AP-39
tests use a real ``tmp_path`` directory.
"""

from __future__ import annotations

import logging
import os
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server import crash_handler
from voice_typer.server.crash_handler import _diagnostics_archive, _veh_callback
from voice_typer.server.crash_handler._constants import (
    _ARCHIVE_RETENTION_KEEP,
    _CRASH_DIAGNOSTICS_DIR,
    _MAX_ACTIVE_FILES,
    OPEN_ALWAYS,
)

# ─── Fixtures ────────────────────────────────────────────────────────────

_UNSET = object()


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state():
    """Reset module-level globals between tests.

    Mirrors the autouse fixture in ``tests/test_crash_handler.py`` so
    state leaks between tests don't cause flaky failures. Includes the
    kernel32 function pointers (``_func_*``) so the AP-40 mocked-write
    tests don't leak mocks into the next test.
    """
    keys = (
        "_crash_file_path",
        "_PID",
        "_handler_handle",
        "_kernel32",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
        "_func_create_file_w",
        "_func_write_file",
        "_func_set_file_pointer",
        "_func_close_handle",
        "_func_delete_file_w",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    yield
    for k, v in saved.items():
        if v is _UNSET:
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)


# ─── AP-39: archive retention ────────────────────────────────────────────


class TestAp39ArchiveRetention:
    """AP-39: the archive subdir must be bounded so VEH-written crash
    files don't accumulate unbounded across crashes.
    """

    def test_enforce_archive_retention_keeps_last_5(self, tmp_path):
        """``_enforce_archive_retention`` deletes the oldest files beyond
        the ``_ARCHIVE_RETENTION_KEEP`` cap (5). Direct unit test on the
        retention function.
        """
        archive_dir = tmp_path / _CRASH_DIAGNOSTICS_DIR
        archive_dir.mkdir()
        # Create 10 crash files with strictly increasing mtimes so the
        # sort order is deterministic.
        for i in range(10):
            f = archive_dir / f"crash_diagnostics.{1000 + i}.txt"
            f.write_text(f"crash {i}\n", encoding="utf-8")
            # Set mtime to i seconds past the epoch + a fixed base so
            # the sort order is strictly increasing.
            mtime = 1_000_000_000 + i
            os.utime(f, (mtime, mtime))

        _diagnostics_archive._enforce_archive_retention(archive_dir)

        remaining = sorted(archive_dir.glob("crash_diagnostics.*.txt"))
        assert len(remaining) == _ARCHIVE_RETENTION_KEEP, (
            f"AP-39: _enforce_archive_retention must keep exactly {_ARCHIVE_RETENTION_KEEP} files; got {len(remaining)}"
        )
        # The NEWEST 5 must survive (files 5..9 with PIDs 1005..1009).
        surviving_pids = {int(p.stem.split(".")[1]) for p in remaining}
        assert surviving_pids == {1005, 1006, 1007, 1008, 1009}, (
            f"AP-39: retention must keep the NEWEST 5 files by mtime; got PIDs {surviving_pids}"
        )

    def test_report_pending_crash_bounds_archive_subdir(self, tmp_path):
        """AP-39: ``report_pending_crash`` calls
        ``_enforce_archive_retention`` on the archive subdir so
        VEH-written crash files (which land directly in the archive
        subdir) are bounded. Pre-fix, the retention call only ran for
        ROOT-level files moved via ``_archive_crash_file`` —
        archive-subdir files grew unbounded.
        """
        archive_dir = tmp_path / _CRASH_DIAGNOSTICS_DIR
        archive_dir.mkdir()
        # Create 10 unreported crash files in the archive subdir (the
        # new VEH write path). Each has a distinct PID and an
        # INCREASING mtime within the last 30 days (so the
        # ``_sweep_stale_diagnostics`` mtime cutoff does NOT delete
        # them — we want to test the retention cap, not the sweep).
        base = time.time() - 100
        for i in range(10):
            f = archive_dir / f"crash_diagnostics.{2000 + i}.txt"
            f.write_text(
                "STATUS_ACCESS_VIOLATION: the process tried to access invalid memory.\r\n",
                encoding="utf-8",
            )
            mtime = base + i
            os.utime(f, (mtime, mtime))

        result = crash_handler.report_pending_crash(tmp_path)

        # The function should surface the crashes (non-None summary).
        assert result is not None, "AP-39: report_pending_crash must surface archive-subdir crash files"
        # After processing, the archive subdir's .txt files must be
        # bounded to _ARCHIVE_RETENTION_KEEP. Pre-fix the archive would
        # have 10 .txt files; post-fix it has at most 5.
        # (``.reported`` sidecars may also exist alongside the surviving
        # .txt files — they're NOT counted toward the cap.)
        txt_files = list(archive_dir.glob("crash_diagnostics.*.txt"))
        assert len(txt_files) <= _ARCHIVE_RETENTION_KEEP, (
            f"AP-39: archive subdir .txt files must be bounded to "
            f"{_ARCHIVE_RETENTION_KEEP}; got {len(txt_files)}: "
            f"{[p.name for p in txt_files]}"
        )
        # The NEWEST 5 must survive (files 5..9 with PIDs 2005..2009).
        surviving_pids = {int(p.stem.split(".")[1]) for p in txt_files}
        assert surviving_pids == {2005, 2006, 2007, 2008, 2009}, (
            f"AP-39: retention must keep the NEWEST 5 .txt files by mtime; got PIDs {surviving_pids}"
        )
        # No orphan sidecars — every remaining sidecar must have a
        # corresponding .txt file (retention cleans up sidecars when
        # deleting .txt files).
        sidecars = list(archive_dir.glob("*.reported"))
        for sidecar in sidecars:
            txt_counterpart = archive_dir / sidecar.name[: -len(".reported")]
            assert txt_counterpart.exists(), (
                f"AP-39: orphan sidecar {sidecar.name} has no corresponding "
                f".txt file — retention must clean up sidecars when deleting "
                f".txt files"
            )

    def test_report_pending_crash_handles_missing_archive_subdir(self, tmp_path):
        """AP-39: the retention call is guarded by
        ``if archive_dir.exists():`` so the first-run case (no archive
        subdir yet, no crashes recorded) does NOT raise.
        """
        # No archive subdir exists — first run.
        assert not (tmp_path / _CRASH_DIAGNOSTICS_DIR).exists()

        # Should return None (nothing to surface) and not raise.
        result = crash_handler.report_pending_crash(tmp_path)
        assert result is None, "AP-39: report_pending_crash must return None when no crash files exist"
        # The archive subdir must still NOT exist (we didn't create it).
        assert not (tmp_path / _CRASH_DIAGNOSTICS_DIR).exists(), (
            "AP-39: first-run with no crash files must not create the archive subdir"
        )

    def test_sweep_stale_diagnostics_walks_archive_subdir(self, tmp_path):
        """AP-39: ``_sweep_stale_diagnostics`` now globs the archive
        subdir too. Pre-fix, it only walked the config_dir root, so
        VEH-written crash files in the archive subdir were never swept
        by the 30-day mtime cutoff. This test creates an OLD crash file
        in the archive subdir (mtime > 30 days ago) and asserts the
        sweep deletes it.
        """
        archive_dir = tmp_path / _CRASH_DIAGNOSTICS_DIR
        archive_dir.mkdir()
        # Create a stale file (mtime = 31 days ago).
        stale_file = archive_dir / "crash_diagnostics.3000.txt"
        stale_file.write_text("stale crash\r\n", encoding="utf-8")
        stale_mtime = time.time() - (31 * 24 * 60 * 60)
        os.utime(stale_file, (stale_mtime, stale_mtime))
        # Create a fresh file (mtime = now) — must survive the sweep.
        fresh_file = archive_dir / "crash_diagnostics.3001.txt"
        fresh_file.write_text("fresh crash\r\n", encoding="utf-8")

        _diagnostics_archive._sweep_stale_diagnostics(tmp_path)

        assert not stale_file.exists(), (
            "AP-39: _sweep_stale_diagnostics must delete stale (>30 day) files in the archive subdir"
        )
        assert fresh_file.exists(), "AP-39: _sweep_stale_diagnostics must NOT delete fresh files in the archive subdir"

    def test_sweep_stale_diagnostics_bounds_archive_to_max_active(self, tmp_path):
        """AP-39: ``_sweep_stale_diagnostics`` enforces the
        ``_MAX_ACTIVE_FILES`` cap on the archive subdir (in addition to
        the 30-day mtime cutoff). If more than ``_MAX_ACTIVE_FILES``
        fresh files accumulate, the oldest beyond the cap are deleted.
        """
        archive_dir = tmp_path / _CRASH_DIAGNOSTICS_DIR
        archive_dir.mkdir()
        # Create more files than _MAX_ACTIVE_FILES, all fresh (mtime=now).
        n_files = _MAX_ACTIVE_FILES + 5
        for i in range(n_files):
            f = archive_dir / f"crash_diagnostics.{4000 + i}.txt"
            f.write_text("crash\r\n", encoding="utf-8")
            mtime = time.time() - (n_files - i)  # strictly increasing
            os.utime(f, (mtime, mtime))

        _diagnostics_archive._sweep_stale_diagnostics(tmp_path)

        remaining = list(archive_dir.glob("crash_diagnostics.*.txt"))
        assert len(remaining) <= _MAX_ACTIVE_FILES, (
            f"AP-39: _sweep_stale_diagnostics must cap archive subdir "
            f"to {_MAX_ACTIVE_FILES} files; got {len(remaining)}"
        )


# ─── AP-40: VEH write truncates (no append) ──────────────────────────────


class TestAp40VehWriteTruncates:
    """AP-40: ``_write_to_file`` opens with ``CREATE_ALWAYS`` (truncates)
    and does NOT seek to ``FILE_END``. Pre-fix, ``OPEN_ALWAYS`` +
    seek-to-end appended to any pre-existing file at the same path —
    when the OS recycled a PID and the recycled process also crashed,
    the new crash record was concatenated onto the stale one.
    """

    def test_write_to_file_uses_create_always_not_open_always(self, monkeypatch):
        """AP-40: the ``CreateFileW`` creation disposition is
        ``CREATE_ALWAYS`` (2), not ``OPEN_ALWAYS`` (4). ``CREATE_ALWAYS``
        truncates any pre-existing file at the path; ``OPEN_ALWAYS``
        opens without truncating (and the old code then seeked to end,
        causing append).
        """
        create_file_w = MagicMock()
        # Return a non-NULL handle so the write proceeds.
        create_file_w.return_value.value = 42
        write_file = MagicMock(return_value=True)
        set_file_pointer = MagicMock()
        close_handle = MagicMock()
        delete_file_w = MagicMock()

        monkeypatch.setattr(crash_handler, "_func_create_file_w", create_file_w)
        monkeypatch.setattr(crash_handler, "_func_write_file", write_file)
        monkeypatch.setattr(crash_handler, "_func_set_file_pointer", set_file_pointer)
        monkeypatch.setattr(crash_handler, "_func_close_handle", close_handle)
        monkeypatch.setattr(crash_handler, "_func_delete_file_w", delete_file_w)

        _veh_callback._write_to_file("C:\\fake\\path\0", b"new crash data")

        create_file_w.assert_called_once()
        # The 5th positional arg (index 4) is the creation disposition.
        args = create_file_w.call_args.args
        assert args[4] == 2, (
            f"AP-40: CreateFileW must be called with CREATE_ALWAYS (2), "
            f"not OPEN_ALWAYS ({OPEN_ALWAYS}); got creation={args[4]}"
        )

    def test_write_to_file_does_not_seek_to_end(self, monkeypatch):
        """AP-40: ``SetFilePointer(handle, 0, None, FILE_END)`` is NOT
        called. Pre-fix, the seek-to-end paired with ``OPEN_ALWAYS``
        caused the write to append. With ``CREATE_ALWAYS`` the file is
        already empty (truncated), so no seek is needed.
        """
        create_file_w = MagicMock()
        create_file_w.return_value.value = 42
        write_file = MagicMock(return_value=True)
        set_file_pointer = MagicMock()
        close_handle = MagicMock()
        delete_file_w = MagicMock()

        monkeypatch.setattr(crash_handler, "_func_create_file_w", create_file_w)
        monkeypatch.setattr(crash_handler, "_func_write_file", write_file)
        monkeypatch.setattr(crash_handler, "_func_set_file_pointer", set_file_pointer)
        monkeypatch.setattr(crash_handler, "_func_close_handle", close_handle)
        monkeypatch.setattr(crash_handler, "_func_delete_file_w", delete_file_w)

        _veh_callback._write_to_file("C:\\fake\\path\0", b"new crash data")

        (
            set_file_pointer.assert_not_called(),
            (
                "AP-40: SetFilePointer must NOT be called — CREATE_ALWAYS "
                "already truncated the file so the pointer is at offset 0"
            ),
        )

    def test_write_to_file_truncates_existing_content(self, monkeypatch, tmp_path):
        """AP-40: end-to-end truncation check. A file with stale content
        is overwritten (not appended to) when ``_write_to_file`` is
        called with the same path. The mock ``_func_create_file_w``
        inspects the creation disposition: if it's ``CREATE_ALWAYS``
        (2), the file is opened in ``wb`` mode (truncate); if it's
        ``OPEN_ALWAYS`` (4) — the old behavior — the file is opened in
        ``ab`` mode (append). The test asserts the final file content
        is EXACTLY the new data (no leftover stale content), which only
        passes if the production code passed ``CREATE_ALWAYS``.
        """
        crash_file = tmp_path / "crash_diagnostics.5000.txt"
        stale_content = b"OLD STALE CONTENT FROM A PREVIOUS CRASH"
        crash_file.write_bytes(stale_content)

        # Map of handle-id -> open binary file object.
        open_files: dict[int, object] = {}

        def mock_create_file_w(path, access, share, security, creation, flags, template):
            # ``path`` is a ctypes.c_wchar_p; the underlying str is in
            # ``path.value`` (strip the trailing NUL).
            path_str = path.value if hasattr(path, "value") else path
            path_str = path_str.rstrip("\0")
            # Simulate the Win32 creation disposition:
            #   CREATE_ALWAYS (2) -> 'wb' (truncate)
            #   OPEN_ALWAYS  (4) -> 'ab' (append, the OLD buggy behavior)
            if creation == 2:
                mode = "wb"
            elif creation == 4:
                mode = "ab"
            else:
                mode = "wb"
            f = open(path_str, mode)  # noqa: SIM115 — mock CreateFileW keeps the handle open until CloseHandle
            handle = MagicMock()
            handle.value = id(f)
            open_files[handle.value] = f
            return handle

        def mock_write_file(handle, data, length, written_ptr, overlapped):
            f = open_files[handle.value]
            f.write(bytes(data))
            # Set written.value via the byref object so the production
            # code's ``written.value == len(data)`` check passes.
            written_ptr._obj.value = length
            return True

        def mock_close_handle(handle):
            f = open_files.pop(handle.value, None)
            if f is not None:
                f.close()
            return True

        monkeypatch.setattr(crash_handler, "_func_create_file_w", mock_create_file_w)
        monkeypatch.setattr(crash_handler, "_func_write_file", mock_write_file)
        monkeypatch.setattr(crash_handler, "_func_close_handle", mock_close_handle)
        monkeypatch.setattr(crash_handler, "_func_set_file_pointer", MagicMock())
        monkeypatch.setattr(crash_handler, "_func_delete_file_w", MagicMock())

        new_data = b"NEW CRASH DATA (should replace the stale content)"
        _veh_callback._write_to_file(str(crash_file) + "\0", new_data)

        final_content = crash_file.read_bytes()
        assert final_content == new_data, (
            f"AP-40: existing file must be TRUNCATED and replaced with "
            f"the new crash record (not appended to). Expected exactly "
            f"{new_data!r}; got {final_content!r}"
        )
        # Sanity: the stale content must NOT be present anywhere in the
        # final file (it would be if the write had appended).
        assert stale_content not in final_content, (
            "AP-40: stale content leaked into the final file — the write appended instead of truncating"
        )

    def test_write_to_file_deletes_empty_file_on_write_failure(self, monkeypatch, tmp_path):
        """AP-40 (non-regression): the ``CREATE_ALWAYS`` change must not
        break the existing failure path — if ``WriteFile`` fails (e.g.
        heap corruption), the empty file is deleted so 0-byte diagnostic
        files don't accumulate. This invariant predates AP-40 but is
        sensitive to the open-mode change, so we re-assert it here.
        """
        crash_file = tmp_path / "crash_diagnostics.6000.txt"
        crash_file.write_bytes(b"pre-existing content")

        open_files: dict[int, object] = {}

        def mock_create_file_w(path, access, share, security, creation, flags, template):
            path_str = (path.value if hasattr(path, "value") else path).rstrip("\0")
            # CREATE_ALWAYS truncates; simulate that.
            f = open(path_str, "wb")  # noqa: SIM115 — mock CreateFileW keeps the handle open until CloseHandle
            handle = MagicMock()
            handle.value = id(f)
            open_files[handle.value] = f
            return handle

        def mock_write_file(handle, data, length, written_ptr, overlapped):
            # Simulate a write failure (e.g. heap corruption mid-write).
            return False

        def mock_close_handle(handle):
            f = open_files.pop(handle.value, None)
            if f is not None:
                f.close()
            return True

        delete_file_w = MagicMock(side_effect=lambda path: crash_file.unlink(missing_ok=True))

        monkeypatch.setattr(crash_handler, "_func_create_file_w", mock_create_file_w)
        monkeypatch.setattr(crash_handler, "_func_write_file", mock_write_file)
        monkeypatch.setattr(crash_handler, "_func_close_handle", mock_close_handle)
        monkeypatch.setattr(crash_handler, "_func_set_file_pointer", MagicMock())
        monkeypatch.setattr(crash_handler, "_func_delete_file_w", delete_file_w)

        _veh_callback._write_to_file(str(crash_file) + "\0", b"new data")

        (
            delete_file_w.assert_called_once(),
            ("AP-40 (non-regression): on write failure the empty file must be deleted via _func_delete_file_w"),
        )
        assert not crash_file.exists(), (
            "AP-40 (non-regression): the crash file must be deleted on "
            "write failure so 0-byte diagnostic files don't accumulate"
        )


# ─── AP-40: constant value sanity ────────────────────────────────────────


class TestAp40CreateAlwaysConstant:
    """AP-40: ``CREATE_ALWAYS`` is defined locally in ``_veh_callback``
    (value 2 — the Win32 ``CreateFileW`` creation disposition for
    "truncate existing or create new"). ``OPEN_ALWAYS`` (4) is still
    re-exported from ``_constants`` for backward compatibility with
    tests that import it, but is no longer used in the write path.
    """

    def test_create_always_is_defined_in_veh_callback(self):
        """``CREATE_ALWAYS`` is a module-level constant in
        ``_veh_callback`` with value 2 (the Win32 creation disposition
        that truncates any existing file at the path).
        """
        assert hasattr(_veh_callback, "CREATE_ALWAYS"), (
            "AP-40: _veh_callback must define CREATE_ALWAYS as a module-level constant"
        )
        assert _veh_callback.CREATE_ALWAYS == 2, (
            f"AP-40: CREATE_ALWAYS must be 2 (Win32 truncates-or-creates "
            f"disposition); got {_veh_callback.CREATE_ALWAYS}"
        )

    def test_open_always_still_re_exported_for_backward_compat(self):
        """``OPEN_ALWAYS`` remains on the ``crash_handler`` facade
        (re-exported from ``_constants``) so existing tests that import
        it (e.g. ``tests/test_crash_handler_split.py``) still pass. The
        AP-40 fix removed its USE in ``_write_to_file`` but not its
        definition (other code or future tests may still reference it).
        """
        assert hasattr(crash_handler, "OPEN_ALWAYS"), (
            "AP-40: OPEN_ALWAYS must remain re-exported on the crash_handler facade for backward compatibility"
        )
        assert crash_handler.OPEN_ALWAYS == OPEN_ALWAYS == 4


# ─── HU-9: secure (symlink-refusing) crash-file read ─────────────────────


class TestHu9SecureCrashFileRead:
    """HU-9: crash-diagnostics / python_crash files are read through
    ``_secure_read_text`` (POSIX ``O_NOFOLLOW``, Windows reparse-point
    check) — the same helper the recovery-file load path uses. A symlink
    planted at a crash-file path is REFUSED and the file is treated as
    empty (fail-closed): its content can never reach the log, the user
    summary, or the archive.
    """

    def test_crash_diagnostics_read_refusal_fails_closed(self, tmp_path, caplog, monkeypatch):
        crash_file = tmp_path / "crash_diagnostics.7000.txt"
        crash_file.write_text("STATUS_ACCESS_VIOLATION: fake crash payload\r\n", encoding="utf-8")

        def _refuse(_path, *args, **kwargs):
            raise OSError("SEC-002: refusing to follow symlink")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", _refuse)

        with caplog.at_level(logging.DEBUG):
            result = crash_handler.report_pending_crash(tmp_path)

        # Fail-closed: nothing surfaced, nothing logged.
        assert result is None, "HU-9: refused read must not surface a summary"
        assert not any("fake crash payload" in r.getMessage() for r in caplog.records)
        # The refusal is a WARNING so operators see the attack attempt.
        assert any("Refusing to read diagnostics file" in r.getMessage() for r in caplog.records)
        # The file is still archived + marked reported (finally block),
        # and the next scan does not re-surface it.
        archive_dir = tmp_path / _CRASH_DIAGNOSTICS_DIR
        archived = list(archive_dir.glob("crash_diagnostics.*.txt"))
        assert len(archived) == 1, "HU-9: refused file must still be archived (finally block)"
        assert crash_handler.report_pending_crash(tmp_path) is None

    def test_python_crash_read_refusal_fails_closed(self, tmp_path, caplog, monkeypatch):
        py_crash = tmp_path / "python_crash.7001.txt"
        py_crash.write_text("exc_type=ValueError\r\nexc_value=TOP-SECRET-EXC\r\n", encoding="utf-8")

        def _refuse(_path, *args, **kwargs):
            raise OSError("SEC-002: refusing to follow symlink")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", _refuse)

        with caplog.at_level(logging.DEBUG):
            result = crash_handler.report_pending_crash(tmp_path)

        assert result is None
        assert not any("TOP-SECRET-EXC" in r.getMessage() for r in caplog.records)
        assert any("Refusing to read python_crash file" in r.getMessage() for r in caplog.records)
        archive_dir = tmp_path / _CRASH_DIAGNOSTICS_DIR
        assert len(list(archive_dir.glob("python_crash.*.txt"))) == 1

    @pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink semantics")
    def test_real_symlink_crash_file_never_logged(self, tmp_path, caplog):
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-REAL-SYMLINK-CONTENT", encoding="utf-8")
        os.symlink(secret, tmp_path / "crash_diagnostics.7002.txt")

        with caplog.at_level(logging.DEBUG):
            result = crash_handler.report_pending_crash(tmp_path)

        assert result is None
        assert not any("TOP-SECRET-REAL-SYMLINK-CONTENT" in r.getMessage() for r in caplog.records)
        assert any("Refusing to read diagnostics file" in r.getMessage() for r in caplog.records)


class TestLegacyArchiveDirMigration:
    """O4: legacy ``crash_diagnostics_archive/`` is renamed to ``crash_diagnostics/`` once."""

    def test_legacy_dir_is_migrated(self, tmp_path):
        from voice_typer.server.crash_handler._diagnostics_archive import _migrate_legacy_archive_dir

        legacy = tmp_path / "crash_diagnostics_archive"
        legacy.mkdir()
        (legacy / "crash_diagnostics.1234.txt").write_text("old crash", encoding="utf-8")

        _migrate_legacy_archive_dir(tmp_path)

        assert not legacy.exists(), "legacy dir must be renamed away"
        canonical = tmp_path / "crash_diagnostics"
        assert canonical.is_dir()
        assert (canonical / "crash_diagnostics.1234.txt").read_text(encoding="utf-8") == "old crash"

    def test_noop_when_legacy_missing(self, tmp_path):
        from voice_typer.server.crash_handler._diagnostics_archive import _migrate_legacy_archive_dir

        _migrate_legacy_archive_dir(tmp_path)  # must not raise
        assert not (tmp_path / "crash_diagnostics").exists()

    def test_noop_when_canonical_already_exists(self, tmp_path):
        from voice_typer.server.crash_handler._diagnostics_archive import _migrate_legacy_archive_dir

        legacy = tmp_path / "crash_diagnostics_archive"
        legacy.mkdir()
        (legacy / "old.txt").write_text("old", encoding="utf-8")
        canonical = tmp_path / "crash_diagnostics"
        canonical.mkdir()
        (canonical / "new.txt").write_text("new", encoding="utf-8")

        _migrate_legacy_archive_dir(tmp_path)

        assert legacy.exists(), "legacy dir must be left alone when canonical exists"
        assert (canonical / "new.txt").read_text(encoding="utf-8") == "new"
