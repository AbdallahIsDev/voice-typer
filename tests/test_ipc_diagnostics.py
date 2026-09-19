"""Unit tests for :mod:`voice_typer.server.ipc_diagnostics`."""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Force-import the modules whose attributes we need to patch (mock.patch
import voice_typer.server.config  # noqa: F401
import voice_typer.server.security  # noqa: F401
from voice_typer.server.ipc_diagnostics import write_startup_diagnostic


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """``\"construction\"`` phase; keep it deterministic."""
    monkeypatch.setattr(sys, "argv", ["ipc_diagnostics_test"])


@pytest.fixture
def diag_dir(tmp_path: Path) -> Path:
    """Per-test config dir for the diagnostic file path."""
    return tmp_path / "config"


class TestGt14CriticalLevel:
    """diagnostic-write logs must use ``CRITICAL``, not ``ERROR``,"""

    def test_successful_write_logs_at_critical(self, diag_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
        """When ``_secure_atomic_write`` succeeds, the log line is"""
        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                return_value=None,
            ),
            caplog.at_level(logging.CRITICAL, logger="voice_typer.server.ipc_server"),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert critical_records, (
            f"expected at least one CRITICAL record; got levels={[r.levelname for r in caplog.records]}"
        )
        msg = critical_records[0].getMessage()
        assert "[FATAL]" not in msg, f"GT-14 regression: [FATAL] prefix still in message body: {msg!r}"
        assert "Diagnostic written to" in msg

    def test_tempfile_fallback_logs_at_critical(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ``_secure_atomic_write`` fails but the tempfile fallback"""
        # Redirect tempfile.gettempdir to tmp_path so the fallback file
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
            caplog.at_level(logging.CRITICAL, logger="voice_typer.server.ipc_server"),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert critical_records, (
            "expected a CRITICAL record for the tempfile-fallback path; "
            f"got levels={[r.levelname for r in caplog.records]}"
        )
        # The fallback-path message names both paths so the operator can
        msg = critical_records[0].getMessage()
        assert "[FATAL]" not in msg
        assert "wrote to" in msg
        # The fallback file must actually have been written.
        assert (tmp_path / "voice-typer-startup-error.log").exists()

    def test_all_fallbacks_fail_logs_at_critical(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ``_secure_atomic_write`` AND the tempfile fallback both"""
        nonexistent = tmp_path / "does-not-exist"
        assert not nonexistent.exists()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
            caplog.at_level(logging.CRITICAL, logger="voice_typer.server.ipc_server"),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert critical_records, (
            "expected a CRITICAL record for the all-fallbacks-failed path; "
            f"got levels={[r.levelname for r in caplog.records]}"
        )
        msg = critical_records[0].getMessage()
        assert "[FATAL]" not in msg
        assert "Could not write diagnostic anywhere" in msg

    def test_no_error_level_records_emitted(self, diag_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
        """the success path, the level was bumped to CRITICAL wholesale."""
        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                return_value=None,
            ),
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert not error_records, (
            f"GT-14 regression: ERROR-level records emitted: {[(r.levelname, r.getMessage()) for r in error_records]}"
        )


class TestStderrRedaction:
    """GT-B1-5: the ``print(buf.getvalue(), file=sys.stderr)`` third-tier"""

    def test_redact_for_export_called_on_stderr_payload(self, diag_dir: Path, tmp_path: Path, monkeypatch) -> None:
        """When the primary write fails, ``redact_for_export`` MUST be"""
        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        from voice_typer.server import _secrets as secrets_mod

        real_redact = secrets_mod.redact_for_export
        call_count = {"n": 0}
        captured_payloads: list[str] = []

        def _spy(text: str) -> str:
            call_count["n"] += 1
            captured_payloads.append(text)
            return real_redact(text)

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
            patch(
                "voice_typer.server._secrets.redact_for_export",
                side_effect=_spy,
            ),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        # We don't pin the exact count (future code might add more
        assert call_count["n"] >= 2, (
            f"expected redact_for_export to be called ≥2 times (primary write + stderr fallback); got {call_count['n']}"
        )
        assert any("Voice Typer startup failed at" in p for p in captured_payloads), (
            "expected at least one redact_for_export call to receive the "
            "diagnostic payload (with the 'Voice Typer startup failed at' "
            f"header); captured payloads: {captured_payloads!r}"
        )

    def test_secret_in_traceback_redacted_before_stderr(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End-to-end: a secret-bearing traceback is redacted by the time"""
        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        secret_key = "sk-" + "a" * 40  # 43-char bearer-style token
        exc = RuntimeError(f"failed to load model with key={secret_key}")

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
        ):
            write_startup_diagnostic("construction", exc=exc)

        captured = capsys.readouterr()
        stderr_text = captured.err
        # The traceback header survives, proves the print path fired.
        assert "Voice Typer startup failed at" in stderr_text
        # The secret must NOT survive.
        assert secret_key not in stderr_text, (
            f"GT-B1-5 regression: raw secret leaked to stderr; stderr was:\n{stderr_text}"
        )

    def test_redactor_failure_falls_back_to_redacted_marker(
        self,
        diag_dir: Path,
        tmp_path: Path,
        monkeypatch,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If ``redact_for_export`` itself raises on the stderr-fallback"""
        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        from voice_typer.server import _secrets as secrets_mod

        real_redact = secrets_mod.redact_for_export
        call_state = {"n": 0}

        def _partial_redactor(text: str) -> str:
            call_state["n"] += 1
            # The function makes 3 ``redact_for_export`` calls on this
            if call_state["n"] >= 3:
                raise RuntimeError("redactor broken on stderr fallback")
            return real_redact(text)

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
            patch(
                "voice_typer.server._secrets.redact_for_export",
                side_effect=_partial_redactor,
            ),
            caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        captured = capsys.readouterr()
        stderr_text = captured.err
        assert call_state["n"] >= 3, (
            f"expected redact_for_export to be called >=3 times (argv + "
            f"primary write + stderr fallback); got {call_state['n']}"
        )
        assert "Voice Typer startup failed at" not in stderr_text, (
            f"regression: raw buf content leaked to stderr when redact_for_export raised; stderr was:\n{stderr_text}"
        )
        # The traceback content of the caller-passed exception must
        assert "RuntimeError" not in stderr_text, (
            "regression: raw traceback content leaked to stderr when "
            "redact_for_export raised; stderr was:\n"
            f"{stderr_text}"
        )
        assert "boom" not in stderr_text, (
            "regression: raw exception message leaked to stderr when "
            "redact_for_export raised; stderr was:\n"
            f"{stderr_text}"
        )
        assert "[redaction failed, traceback suppressed to avoid PII leak]" in stderr_text, (
            f"expected the redaction-failed marker on stderr; got:\n{stderr_text}"
        )
        assert "OSError" in stderr_text, (
            "expected the outer write_exc type name (OSError) appended "
            f"to the redaction-failed marker; got:\n{stderr_text}"
        )
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warning_records, (
            "expected at least one WARNING record for the redactor-raised "
            f"fallback; got levels={[r.levelname for r in caplog.records]}"
        )
        warning_msgs = [r.getMessage() for r in warning_records]
        assert any("[LOG-SETUP] redact_for_export raised" in m for m in warning_msgs), (
            "expected a WARNING record with the '[LOG-SETUP] redact_for_export "
            f"raised' marker; got warning messages: {warning_msgs!r}"
        )
        assert any("RuntimeError" in m for m in warning_msgs), (
            f"expected the redactor exception type name (RuntimeError) in the WARNING message; got: {warning_msgs!r}"
        )

    def test_redactor_failure_marker_excludes_secret_in_buf(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """stderr-fallback call, the marker string printed to stderr must"""
        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        from voice_typer.server import _secrets as secrets_mod

        real_redact = secrets_mod.redact_for_export
        call_state = {"n": 0}

        def _partial_redactor(text: str) -> str:
            call_state["n"] += 1
            if call_state["n"] >= 3:
                raise RuntimeError("redactor broken on stderr fallback")
            return real_redact(text)

        secret_key = "sk-" + "a" * 40  # 43-char bearer-style token
        exc = RuntimeError(f"failed to load model with key={secret_key}")

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
            patch(
                "voice_typer.server._secrets.redact_for_export",
                side_effect=_partial_redactor,
            ),
        ):
            write_startup_diagnostic("construction", exc=exc)

        captured = capsys.readouterr()
        stderr_text = captured.err
        assert secret_key not in stderr_text, (
            f"regression: raw API key leaked to stderr when redact_for_export raised; stderr was:\n{stderr_text}"
        )
        # The marker MUST appear (proving we took the new fail-closed
        assert "[redaction failed, traceback suppressed to avoid PII leak]" in stderr_text, (
            f"expected the redaction-failed marker on stderr; got:\n{stderr_text}"
        )


class TestHeaderPreservation:
    """Pin the phase-header text so the pre-extraction diagnostic"""

    def test_construction_phase_header(self, diag_dir: Path, tmp_path: Path, monkeypatch) -> None:
        """``phase=\"construction\"`` writes the historical"""
        # Force a successful primary write so we can read the file.
        written_payloads: list[str] = []

        def _capture(_path, payload):
            written_payloads.append(payload)

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=_capture,
            ),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        assert written_payloads, "primary write did not capture any payload"
        assert "Voice Typer startup failed at" in written_payloads[0]
        assert "sys.executable:" in written_payloads[0]
        assert "sys.argv:" in written_payloads[0]

    def test_app_start_phase_header(self, diag_dir: Path) -> None:
        """``phase=\"app.start()\"`` writes the historical"""
        written_payloads: list[str] = []

        def _capture(_path, payload):
            written_payloads.append(payload)

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=_capture,
            ),
        ):
            write_startup_diagnostic("app.start()", exc=RuntimeError("boom"))

        assert written_payloads
        assert "--- app.start() failed at" in written_payloads[0]

    def test_unknown_phase_header(self, diag_dir: Path) -> None:
        """``\"\\n--- <phase> failed at <time> ---\\n\"``."""
        written_payloads: list[str] = []

        def _capture(_path, payload):
            written_payloads.append(payload)

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=_capture,
            ),
        ):
            write_startup_diagnostic("custom-phase", exc=RuntimeError("boom"))

        assert written_payloads
        assert "--- custom-phase failed at" in written_payloads[0]


class TestPi12TmpFallbackOverwrite:
    """create, refuses to clobber an existing file). With ``O_EXCL``, if"""

    def test_second_consecutive_crash_dump_overwrites_first(self, diag_dir: Path, tmp_path: Path, monkeypatch) -> None:
        """PI-12: two consecutive calls to ``write_startup_diagnostic``"""
        # Redirect tempfile.gettempdir to tmp_path so the fallback file
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),
        ):
            # First crash dump.
            write_startup_diagnostic("construction", exc=RuntimeError("first crash"))
            # Second crash dump, must NOT raise. Pre-, this would
            write_startup_diagnostic("construction", exc=RuntimeError("second crash"))

        tmp_file = tmp_path / "voice-typer-startup-error.log"
        # The fallback file must still exist (not deleted by the second
        assert tmp_file.exists(), (
            "PI-12 regression: the /tmp fallback file should still exist after the second consecutive crash dump"
        )
        # The file content must be the SECOND crash's diagnostic (i.e.
        content = tmp_file.read_text(encoding="utf-8")
        assert "second crash" in content, (
            "PI-12 regression: the /tmp fallback file should contain the "
            "SECOND crash's diagnostic (the first was overwritten). "
            f"Got content:\n{content}"
        )
        assert "first crash" not in content, (
            "PI-12 regression: the /tmp fallback file should NOT contain "
            "the first crash's diagnostic (it should have been overwritten "
            f"by the second). Got content:\n{content}"
        )
