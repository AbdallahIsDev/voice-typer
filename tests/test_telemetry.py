"""Tests for S-1: atomic crash-report writes + secure directory perms + PII redaction.

Scope (per task S-1):
  - Atomic write: ``write_crash_report`` delegates to
    ``_secure_atomic_write`` (tmp + ``os.replace``); on failure the
    pre-existing report file is preserved.
  - Directory perms: on POSIX, the ``crash_reports/`` dir is created
    with ``0o700`` when newly created; pre-existing dirs are not
    silently chmod'd.
  - PII redaction: crash-report content is run through ``redact_pii``
    before being persisted (defence-in-depth — tracebacks and
    exception messages can carry user-supplied text).
  - Regression: crash-report content (timestamp, exception type,
    traceback, thread name, etc.) is preserved end-to-end.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import voice_typer.server.telemetry as telemetry_mod
from voice_typer.server.telemetry import write_crash_report

# ─── Helpers ───────────────────────────────────────────────────────────────


def _set_config_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect ``_crash_reports_dir()`` to ``tmp_path / crash_reports``."""
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )
    return tmp_path / "crash_reports"


def _make_exc(message: str = "boom") -> ValueError:
    """Return an exception with a populated traceback."""
    try:
        raise ValueError(message)
    except ValueError as exc:
        return exc


# ─── Regression: content preservation ──────────────────────────────────────


class TestCrashReportContent:
    """Regression: crash report content is preserved end-to-end."""

    def test_disabled_returns_none(self, monkeypatch, tmp_path):
        """When telemetry_enabled is False, nothing is written."""
        _set_config_dir(monkeypatch, tmp_path)
        result = write_crash_report(
            _make_exc(), telemetry_enabled=False
        )
        assert result is None
        assert not (tmp_path / "crash_reports").exists()

    def test_writes_file_with_expected_fields(self, monkeypatch, tmp_path):
        """Written report contains the expected header + exception info."""
        reports_dir = _set_config_dir(monkeypatch, tmp_path)
        exc = _make_exc("kaboom")
        path = write_crash_report(
            exc, thread_name="worker-1", telemetry_enabled=True
        )
        assert path is not None
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Voice Typer Crash Report" in content
        assert "Exception Type: ValueError" in content
        assert "Exception Message: kaboom" in content
        assert "Thread: worker-1" in content
        assert "Traceback:" in content
        # Traceback body should reference the raise site.
        assert "ValueError" in content
        # File is written inside the reports dir.
        assert path.parent == reports_dir

    def test_returns_path_under_reports_dir(self, monkeypatch, tmp_path):
        """Returned path is a crash_*.log file under crash_reports/."""
        reports_dir = _set_config_dir(monkeypatch, tmp_path)
        path = write_crash_report(_make_exc(), telemetry_enabled=True)
        assert path is not None
        assert path.parent == reports_dir
        assert path.name.startswith("crash_")
        assert path.suffix == ".log"


# ─── Atomic write ──────────────────────────────────────────────────────────


class TestAtomicWrite:
    """S-1: crash reports use ``_secure_atomic_write`` (tmp + os.replace)."""

    def test_uses_secure_atomic_write(self, monkeypatch, tmp_path):
        """write_crash_report delegates to _secure_atomic_write."""
        _set_config_dir(monkeypatch, tmp_path)
        captured: dict[str, object] = {}

        def fake_write(path: Path, content: str) -> None:
            captured["path"] = path
            captured["content"] = content
            # Simulate the real helper for downstream assertions.
            path.write_text(content, encoding="utf-8")

        with patch(
            "voice_typer.server.config._secure_atomic_write",
            side_effect=fake_write,
        ) as mock:
            result = write_crash_report(
                _make_exc("xyz"), telemetry_enabled=True
            )

        assert mock.called
        assert result is not None
        assert captured["path"] == result
        assert "Exception Type: ValueError" in str(captured["content"])

    def test_preserves_existing_report_when_write_fails(
        self, monkeypatch, tmp_path
    ):
        """If the atomic write raises, any pre-existing report is preserved.

        Mocks ``_secure_atomic_write`` to raise OSError so the test
        verifies the original file (if any) is left untouched. Because
        ``write_crash_report`` swallows exceptions and returns None,
        the previously written report must still be on disk and
        byte-identical to its pre-failure state.
        """
        reports_dir = _set_config_dir(monkeypatch, tmp_path)
        reports_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create a "previous" report at the path the next call
        # would target. The timestamp is computed at call time, so we
        # instead create an unrelated pre-existing report file and
        # then assert it survives a failed write call (the failed call
        # returns None without touching any sibling files).
        existing_path = reports_dir / "crash_PREV.log"
        existing_path.write_text("PREVIOUS-CONTENT", encoding="utf-8")

        with patch(
            "voice_typer.server.config._secure_atomic_write",
            side_effect=OSError("disk full"),
        ):
            result = write_crash_report(
                _make_exc("fail"), telemetry_enabled=True
            )

        assert result is None
        # Pre-existing report survives intact.
        assert existing_path.read_text(encoding="utf-8") == "PREVIOUS-CONTENT"
        # No new crash report was created.
        assert len(list(reports_dir.glob("crash_*.log"))) == 1

    def test_no_stale_tmp_file_after_successful_write(
        self, monkeypatch, tmp_path
    ):
        """After a successful write, no ``.tmp`` file lingers.

        ``_secure_atomic_write`` writes to ``<path>.tmp`` then
        ``os.replace``s into place; on success the tmp file must be
        gone. This is a regression test for the atomic-write contract.
        """
        _set_config_dir(monkeypatch, tmp_path)
        path = write_crash_report(_make_exc(), telemetry_enabled=True)
        assert path is not None
        assert path.exists()
        assert not path.with_suffix(path.suffix + ".tmp").exists()


# ─── Directory permissions ─────────────────────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only — Windows inherits ACLs from parent",
)
class TestDirectoryPermissions:
    """S-1: crash_reports/ dir is created with 0o700 on POSIX."""

    def test_newly_created_dir_has_0o700(self, monkeypatch, tmp_path):
        reports_dir = _set_config_dir(monkeypatch, tmp_path)
        write_crash_report(_make_exc(), telemetry_enabled=True)
        assert reports_dir.exists()
        mode = reports_dir.stat().st_mode
        assert (mode & 0o777) == 0o700, (
            f"Expected 0o700, got {oct(mode & 0o777)}"
        )

    def test_does_not_chmod_pre_existing_dir(self, monkeypatch, tmp_path):
        """A pre-existing dir with non-0o700 perms is NOT chmod'd.

        Honours the task-brief requirement: chmod only when newly
        created, so user-configured perms on an existing dir are
        preserved.
        """
        reports_dir = _set_config_dir(monkeypatch, tmp_path)
        # Pre-create the dir with looser perms (0o750).
        reports_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(reports_dir, 0o750)
        try:
            write_crash_report(_make_exc(), telemetry_enabled=True)
            mode = reports_dir.stat().st_mode & 0o777
            assert mode == 0o750, (
                f"Pre-existing dir was chmod'd to {oct(mode)}; "
                "expected 0o750 (user-configured perms preserved)"
            )
        finally:
            # Restore permissive perms so pytest's tmp_path cleanup
            # can delete the directory.
            os.chmod(reports_dir, 0o700)

    def test_chmod_failure_is_logged_not_raised(self, monkeypatch, tmp_path, caplog):
        """If os.chmod raises OSError, the report is still written."""
        _set_config_dir(monkeypatch, tmp_path)

        with patch(
            "voice_typer.server.telemetry.os.chmod",
            side_effect=OSError("permission denied"),
        ), caplog.at_level(
            "WARNING", logger="voice_typer.server.telemetry"
        ):
            path = write_crash_report(
                _make_exc(), telemetry_enabled=True
            )

        assert path is not None
        assert path.exists()
        assert any(
            "Failed to chmod reports dir" in r.message for r in caplog.records
        )


# ─── PII redaction ─────────────────────────────────────────────────────────


class TestPIIRedaction:
    """S-1: crash report content is run through ``redact_pii``."""

    def test_email_in_exception_message_is_redacted(
        self, monkeypatch, tmp_path
    ):
        """An email in ``str(exc)`` is replaced with ``[EMAIL]``."""
        _set_config_dir(monkeypatch, tmp_path)
        exc = _make_exc("failed for user leaker@example.com on input")
        path = write_crash_report(exc, telemetry_enabled=True)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert "leaker@example.com" not in content
        assert "[EMAIL]" in content

    def test_phone_in_exception_message_is_redacted(
        self, monkeypatch, tmp_path
    ):
        """A phone number in ``str(exc)`` is replaced with ``[PHONE]``."""
        _set_config_dir(monkeypatch, tmp_path)
        exc = _make_exc("callback failed for 555-123-4567")
        path = write_crash_report(exc, telemetry_enabled=True)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert "555-123-4567" not in content
        assert "[PHONE]" in content

    def test_ssn_in_exception_message_is_redacted(self, monkeypatch, tmp_path):
        """An SSN-like pattern in ``str(exc)`` is replaced with ``[SSN]``."""
        _set_config_dir(monkeypatch, tmp_path)
        exc = _make_exc("identity lookup failed for 123-45-6789")
        path = write_crash_report(exc, telemetry_enabled=True)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert "123-45-6789" not in content
        assert "[SSN]" in content

    def test_redaction_failure_falls_back_to_unredacted(
        self, monkeypatch, tmp_path
    ):
        """If ``redact_pii`` import raises, the report is still written.

        Defence-in-depth: a failure in the redaction pipeline must not
        prevent the crash report from being persisted. The unredacted
        content is still written (better a leaked report than no
        report).
        """
        _set_config_dir(monkeypatch, tmp_path)

        with patch(
            "voice_typer.server.security.redact_pii",
            side_effect=RuntimeError("redaction exploded"),
        ):
            path = write_crash_report(
                _make_exc("plain message"), telemetry_enabled=True
            )

        assert path is not None
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Exception Type: ValueError" in content


# ─── Source-level audit (defence in depth) ─────────────────────────────────


class TestSourceLevelAudit:
    """Meta-tests: source-level guarantees for S-1."""

    def test_telemetry_uses_secure_atomic_write(self):
        """telemetry.py imports and calls ``_secure_atomic_write``."""
        source = Path(telemetry_mod.__file__).read_text(encoding="utf-8")
        assert "_secure_atomic_write" in source
        assert "from voice_typer.server.config import _secure_atomic_write" in source

    def test_telemetry_does_not_use_write_text_for_reports(self):
        """No bare ``report_path.write_text(...)`` remains for crash writes.

        The non-atomic ``report_path.write_text(...)`` call was the
        original S-1 defect. After the fix, ``_secure_atomic_write``
        is the only crash-report write path.
        """
        source = Path(telemetry_mod.__file__).read_text(encoding="utf-8")
        # The literal pattern from the original defect must not appear.
        assert "report_path.write_text(" not in source, (
            "Non-atomic ``report_path.write_text()`` is still in use; "
            "crash reports must use ``_secure_atomic_write``."
        )

    def test_telemetry_uses_redact_pii(self):
        """telemetry.py runs crash-report content through ``redact_pii``."""
        source = Path(telemetry_mod.__file__).read_text(encoding="utf-8")
        assert "redact_pii" in source
        assert "from voice_typer.server.security import redact_pii" in source

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only — chmod is a no-op on Windows",
    )
    def test_telemetry_uses_chmod_0o700_on_new_dir(self):
        """telemetry.py chmods the reports dir to 0o700 on POSIX."""
        source = Path(telemetry_mod.__file__).read_text(encoding="utf-8")
        assert "os.chmod(reports_dir, 0o700)" in source
        assert "was_missing" in source  # only-chmod-if-newly-created guard
