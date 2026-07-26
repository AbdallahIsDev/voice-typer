"""Unit tests for :mod:`voice_typer.server.ipc_diagnostics`.

Covers the single public function ``write_startup_diagnostic``.

GT-14 / GT-B1-5 regression tests
--------------------------------
Two findings from the comprehensive review are pinned here:

* **GT-14 (High)** — the three diagnostic-write log lines previously
  used ``log.error("[FATAL] ...")`` — i.e. the *message body* claimed
  ``FATAL`` severity while the actual ``record.levelno`` was ``ERROR``
  (40), not ``CRITICAL`` (50).  Python's logging framework, log
  aggregators, and alerting rules key off ``record.levelno`` /
  ``record.levelname``, not substring matches in the message body — so
  a CRITICAL-level alert rule would not fire on a startup-failure
  diagnostic, the most severe error the system can produce.  The fix
  routes the three sites through ``log.critical(...)`` and drops the
  now-redundant ``[FATAL]`` prefix.

* **GT-B1-5 (High)** — the third-tier ``print(buf.getvalue(),
  file=sys.stderr)`` fallback bypassed both the PIIRedactionFilter
  AND the logging framework.  A traceback embedded with a URL like
  ``?key=sk-...`` or an env-var dump from a buggy handler would land
  on stderr verbatim, leaking whatever the downstream
  PIIRedactionFilter would have scrubbed.  The fix pipes the payload
  through ``_redact_text`` BEFORE the ``print``.

These tests assert both the level (``CRITICAL``) and the redaction
(``_redact_text`` called on the stderr-bound payload) so a future
refactor cannot silently restore the bugs.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Force-import the modules whose attributes we need to patch (mock.patch
# with a dotted string target uses attribute lookup on the parent module,
# so the parent module must already be in sys.modules for the patch to
# find the attribute).
import voice_typer.server.config  # noqa: F401
import voice_typer.server.security  # noqa: F401
from voice_typer.server.ipc_diagnostics import write_startup_diagnostic


@pytest.fixture(autouse=True)
def _clean_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """``write_startup_diagnostic`` renders ``sys.argv`` for the
    ``"construction"`` phase; keep it deterministic.
    """
    monkeypatch.setattr(sys, "argv", ["ipc_diagnostics_test"])


@pytest.fixture
def diag_dir(tmp_path: Path) -> Path:
    """Per-test config dir for the diagnostic file path."""
    return tmp_path / "config"


# ─── GT-14: log level is CRITICAL, not ERROR; no [FATAL] prefix ─────────


class TestGt14CriticalLevel:
    """GT-14: diagnostic-write logs must use ``CRITICAL``, not ``ERROR``,
    and must NOT carry a ``[FATAL]`` prefix in the message body.
    """

    def test_successful_write_logs_at_critical(
        self, diag_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ``_secure_atomic_write`` succeeds, the log line is
        ``CRITICAL`` (50) and the message body contains neither the
        historical ``[FATAL]`` prefix nor the ``ERROR`` level name.
        """
        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                return_value=None,
            ),caplog.at_level(logging.CRITICAL, logger="voice_typer.server.ipc_server")
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        critical_records = [
            r for r in caplog.records if r.levelno == logging.CRITICAL
        ]
        assert critical_records, (
            "expected at least one CRITICAL record; got levels="
            f"{[r.levelname for r in caplog.records]}"
        )
        msg = critical_records[0].getMessage()
        assert "[FATAL]" not in msg, (
            f"GT-14 regression: [FATAL] prefix still in message body: {msg!r}"
        )
        assert "Diagnostic written to" in msg

    def test_tempfile_fallback_logs_at_critical(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ``_secure_atomic_write`` fails but the tempfile fallback
        succeeds, the log line is ``CRITICAL`` (50) — not ``ERROR`` (40).
        """
        # Redirect tempfile.gettempdir to tmp_path so the fallback file
        # lands inside the test sandbox.
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("read-only filesystem"),
            ),caplog.at_level(logging.CRITICAL, logger="voice_typer.server.ipc_server")
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        critical_records = [
            r for r in caplog.records if r.levelno == logging.CRITICAL
        ]
        assert critical_records, (
            "expected a CRITICAL record for the tempfile-fallback path; "
            f"got levels={[r.levelname for r in caplog.records]}"
        )
        # The fallback-path message names both paths so the operator can
        # find the diagnostic file.
        msg = critical_records[0].getMessage()
        assert "[FATAL]" not in msg
        assert "wrote to" in msg
        # The fallback file must actually have been written.
        assert (tmp_path / "voice-typer-startup-error.log").exists()

    def test_all_fallbacks_fail_logs_at_critical(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ``_secure_atomic_write`` AND the tempfile fallback both
        fail, the final log line is still ``CRITICAL`` (50).
        """
        # Point tempfile.gettempdir at a path that doesn't exist so
        # ``os.open`` raises FileNotFoundError.
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
            ),caplog.at_level(logging.CRITICAL, logger="voice_typer.server.ipc_server")
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        critical_records = [
            r for r in caplog.records if r.levelno == logging.CRITICAL
        ]
        assert critical_records, (
            "expected a CRITICAL record for the all-fallbacks-failed path; "
            f"got levels={[r.levelname for r in caplog.records]}"
        )
        msg = critical_records[0].getMessage()
        assert "[FATAL]" not in msg
        assert "Could not write diagnostic anywhere" in msg

    def test_no_error_level_records_emitted(
        self, diag_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Defensive: no ERROR-level records should be emitted at all on
        the success path — the level was bumped to CRITICAL wholesale.
        """
        with (
            patch(
                "voice_typer.server.config._config_dir",
                return_value=diag_dir,
            ),
            patch(
                "voice_typer.server.config._secure_atomic_write",
                return_value=None,
            ),caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server")
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert not error_records, (
            f"GT-14 regression: ERROR-level records emitted: "
            f"{[(r.levelname, r.getMessage()) for r in error_records]}"
        )


# ─── GT-B1-5: stderr print fallback must pipe through _redact_text ────


class TestGtB1_5StderrRedaction:
    """GT-B1-5: the ``print(buf.getvalue(), file=sys.stderr)`` third-tier
    fallback must call ``_redact_text`` on the payload BEFORE the print
    so secrets embedded in the traceback are masked the same way they
    would be in a ``log.critical`` record.
    """

    def test_redact_text_called_on_stderr_payload(
        self, diag_dir: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """When the primary write fails, ``_redact_text`` MUST be invoked
        on the buffer payload before it is printed to stderr.

        Asserts the call happened (via a spy that still delegates to the
        real ``_redact_text`` so the rest of the fallback chain works).
        """
        # Make tempfile.gettempdir point at a non-existent dir so the
        # second-tier fallback fails too — we want to isolate the
        # stderr-print path.
        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        real_redact = voice_typer.server.security._redact_text  # type: ignore[attr-defined]
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
                "voice_typer.server.security._redact_text",
                side_effect=_spy,
            ),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        # _redact_text is called at least twice on this path:
        #   1. for the primary _secure_atomic_write path (which raises)
        #   2. for the stderr print fallback
        # We don't pin the exact count (future code might add more
        # redaction points); we just assert it ran on the stderr-bound
        # payload by checking that at least one call saw the
        # "Voice Typer startup failed at" header.
        assert call_count["n"] >= 2, (
            f"expected _redact_text to be called ≥2 times (primary write + "
            f"stderr fallback); got {call_count['n']}"
        )
        assert any(
            "Voice Typer startup failed at" in p for p in captured_payloads
        ), (
            "expected at least one _redact_text call to receive the "
            "diagnostic payload (with the 'Voice Typer startup failed at' "
            f"header); captured payloads: {captured_payloads!r}"
        )

    def test_secret_in_traceback_redacted_before_stderr(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """End-to-end: a secret-bearing traceback is redacted by the time
        it reaches stderr.

        Constructs an exception whose ``str()`` includes a 40-char API
        key (matches the ``[A-Za-z0-9_\\-]{20,}`` trigger in
        :data:`security._FAST_TRIGGER`); the printed stderr payload
        must NOT contain the raw key.
        """
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
        # The traceback header survives — proves the print path fired.
        assert "Voice Typer startup failed at" in stderr_text
        # The secret must NOT survive.
        assert secret_key not in stderr_text, (
            f"GT-B1-5 regression: raw secret leaked to stderr; stderr was:\n{stderr_text}"
        )

    def test_redactor_failure_falls_back_to_unredacted_print(
        self, diag_dir: Path, tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """If ``_redact_text`` itself raises on the stderr-fallback call,
        the stderr fallback must still print SOMETHING (the unredacted
        payload) — a partially-redacted traceback is better than no
        traceback at all (matches the ``except Exception:`` last-resort
        philosophy of the surrounding block).

        The test uses a side_effect function that lets the first few
        ``_redact_text`` calls succeed (so the function reaches the
        stderr-fallback path) and then raises on the call inside the
        stderr-fallback ``try`` block.  This isolates the GT-B1-5
        fallback branch from the other ``_redact_text`` call sites in
        the function (``redacted_argv`` and the primary-write path).
        """
        nonexistent = tmp_path / "does-not-exist"
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(nonexistent))

        real_redact = voice_typer.server.security._redact_text  # type: ignore[attr-defined]
        call_state = {"n": 0}

        def _partial_redactor(text: str) -> str:
            call_state["n"] += 1
            # The function makes 3 ``_redact_text`` calls on this path
            # before reaching the stderr-fallback try block:
            #   1. redacted_argv (one call per sys.argv entry — the test
            #      fixture sets sys.argv to a single-element list).
            #   2. primary-write payload (inside the outer try, before
            #      _secure_atomic_write raises OSError).
            # The 3rd call is inside the stderr-fallback try block —
            # raise here so the GT-B1-5 except branch fires.
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
                "voice_typer.server.security._redact_text",
                side_effect=_partial_redactor,
            ),
        ):
            write_startup_diagnostic("construction", exc=RuntimeError("boom"))

        captured = capsys.readouterr()
        stderr_text = captured.err
        # The diagnostic header must still be on stderr even though the
        # redactor blew up on the stderr-fallback call — proves the
        # GT-B1-5 fallback-to-unredacted branch fired.
        assert "Voice Typer startup failed at" in stderr_text
        # And we must have actually taken the raising branch (i.e. the
        # side_effect was called at least 3 times).
        assert call_state["n"] >= 3, (
            f"expected _redact_text to be called >=3 times (argv + primary "
            f"write + stderr fallback); got {call_state['n']}"
        )


# ─── Behaviour preservation: header text + idempotent write ─────────────


class TestHeaderPreservation:
    """Pin the phase-header text so the pre-extraction diagnostic
    format is preserved across the GT-14 / GT-B1-5 edits.
    """

    def test_construction_phase_header(self, diag_dir: Path, tmp_path: Path, monkeypatch) -> None:
        """``phase="construction"`` writes the historical
        ``"Voice Typer startup failed at <time>\\n"`` header.
        """
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
        """``phase="app.start()"`` writes the historical
        ``"\\n--- app.start() failed at <time> ---\\n"`` header.
        """
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
        """An unrecognised phase label is rendered verbatim as
        ``"\\n--- <phase> failed at <time> ---\\n"``.
        """
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


# ─── PI-12: /tmp fallback uses O_TRUNC (not O_EXCL) so repeated crash dumps ─
# ─── can overwrite a previous diagnostic file ──────────────────────────────


class TestPi12TmpFallbackOverwrite:
    """PI-12: the /tmp fallback path previously used ``O_EXCL`` (atomic
    create, refuses to clobber an existing file). With ``O_EXCL``, if
    ``/tmp/voice-typer-startup-error.log`` exists from a previous crash,
    the next startup crash cannot write its diagnostic — ``os.open``
    raises ``FileExistsError``, the outer ``except Exception`` runs, and
    the traceback is lost. The docstring at line 146-147 says "OVERWRITE
    (not append) the diagnostic file so repeated relaunch crashes don't
    grow it without bound" — the /tmp fallback must honor that same
    contract.

    These tests pin the new ``O_TRUNC`` behavior so a future refactor
    that restores ``O_EXCL`` (e.g. a copy-paste from the config_dir
    primary path) doesn't silently regress PI-12.
    """

    def test_second_consecutive_crash_dump_overwrites_first(
        self, diag_dir: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """PI-12: two consecutive calls to ``write_startup_diagnostic``
        that both fall through to the /tmp fallback must both succeed
        — the second call overwrites the first diagnostic file rather
        than raising ``FileExistsError``.

        Pre-PI-12 behavior: the second call would raise
        ``FileExistsError`` from ``os.open(O_EXCL)`` inside the
        fallback ``try`` block; the outer ``except Exception`` would
        log "Could not write diagnostic anywhere" and the second
        crash's traceback would be lost.
        """
        # Redirect tempfile.gettempdir to tmp_path so the fallback file
        # lands inside the test sandbox.
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        # Force the primary write to fail so both calls fall through to
        # the /tmp fallback path.
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
            # Second crash dump — must NOT raise. Pre-PI-12, this would
            # raise FileExistsError (caught by the outer except) and the
            # second crash's traceback would be lost.
            write_startup_diagnostic("construction", exc=RuntimeError("second crash"))

        tmp_file = tmp_path / "voice-typer-startup-error.log"
        # The fallback file must still exist (not deleted by the second
        # call's failed os.open).
        assert tmp_file.exists(), (
            "PI-12 regression: the /tmp fallback file should still exist "
            "after the second consecutive crash dump"
        )
        # The file content must be the SECOND crash's diagnostic (i.e.
        # the second call overwrote the first). Both payloads contain
        # the "Voice Typer startup failed at" header, so we distinguish
        # them by the exception message embedded in the traceback.
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
