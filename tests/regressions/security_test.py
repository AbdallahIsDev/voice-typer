"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestTranscriptionLoggingRedactsPii:
    """SEC-009.

    Pre-fix: ``redact_pii()`` was dead code — declared in security.py
    but never called from production. The original SEC-009 fix wired
    ``redact_pii()`` into the ``log_transcriptions=True`` path of
    ``DictationPipeline._store_result`` so PII patterns were masked
    before hitting the log file.

    G4-H-08 (superseded SEC-009): ``redact_pii()`` only masked four
    patterns (email / US-phone / SSN / credit-card-like) — medical
    dictation, financial narratives, addresses, and names passed
    through verbatim. For a voice-typing tool this is the primary
    PII surface, so masking just four patterns was insufficient.
    The path now logs a non-reversible 12-char SHA-256 prefix of the
    transcription text instead, preserving log-line correlation
    (the same transcription produces the same hash) without leaking
    ANY content. ``len(text)`` is also logged so operators can spot
    suspiciously short / long transcriptions.
    """

    def test_store_result_logs_sha256_hash_not_raw_text(self, caplog):
        """SEC-009 / G4-H-08: when ``log_transcriptions`` is enabled,
        ``DictationPipeline._store_result`` must log a non-reversible
        SHA-256 hash prefix of the transcription text — NOT the raw
        text and NOT a four-pattern ``redact_pii()`` masked version
        (which leaked medical/financial/address/name PII). The raw
        text must NEVER appear in the log output.

        The test is behavioral: it patches ``hashlib.sha256`` so we
        can assert the hash path is invoked, AND it captures the log
        output to verify the raw transcription text does not leak.
        """
        import hashlib
        import logging

        from voice_typer.server.dictation_pipeline import DictationPipeline

        # Build a minimal app mock. ``_store_result`` touches:
        # ``history_db.add_transcription`` / ``flush``,
        # ``config.crash_recovery_enabled`` (False → skip crash recovery),
        # ``config.log_transcriptions`` (True → trigger the hash-log path),
        # ``config.model_size`` / ``config.device`` (passed to add_transcription),
        # ``_last_transcription`` (assigned), ``tray.notify`` (only on errors),
        # and ``event_bus.publish`` (transcription_final push event — the
        # MagicMock makes this a no-op).
        app = MagicMock()
        app.config.log_transcriptions = True
        app.config.crash_recovery_enabled = False
        app.config.model_size = "tiny.en"
        app.config.device = "cpu"

        pipeline = DictationPipeline(app)

        raw_text = "Contact john.doe@example.com for the biopsy results."

        # Patch ``hashlib.sha256`` so we can assert the hash path is
        # invoked (the source does ``hashlib.sha256(text.encode(...))``).
        # We delegate to the real implementation so the log line gets a
        # real 12-char hex prefix (which we then verify is NOT the raw
        # text).
        real_sha256 = hashlib.sha256
        sha256_calls: list[bytes] = []

        def _tracking_sha256(payload: bytes):
            sha256_calls.append(payload)
            return real_sha256(payload)

        with (
            patch("hashlib.sha256", side_effect=_tracking_sha256),
            caplog.at_level(logging.INFO, logger="voice_typer.server.dictation_pipeline"),
        ):
            pipeline._store_result(raw_text)

        # ``hashlib.sha256`` MUST have been invoked with the raw text
        # bytes (the G4-H-08 contract). If this fails, the source has
        # regressed to either logging raw text or to ``redact_pii()``
        # (the pre-G4-H-08 four-pattern masker).
        assert sha256_calls, (
            "SEC-009 / G4-H-08: _store_result must call hashlib.sha256 on "
            "the transcription text when log_transcriptions is True."
        )
        assert any(raw_text.encode("utf-8") in call_args for call_args in sha256_calls), (
            "SEC-009 / G4-H-08: hashlib.sha256 must be called with the raw "
            "transcription text as its argument."
        )

        # The raw transcription text must NOT appear in any log line —
        # only the 12-char hash prefix + length are logged. This is the
        # core SEC-009 invariant: PII never leaks to the log file.
        log_text = caplog.text
        assert raw_text not in log_text, (
            "SEC-009 / G4-H-08: the raw transcription text must NOT appear "
            "in the log output — only a non-reversible SHA-256 hash prefix "
            "should be logged. The raw text was found in the log."
        )
        # Belt-and-suspenders: verify the hash-prefix form is present
        # (``hash=<12 hex chars>`` per dictation_pipeline.py:1131).
        import re

        assert re.search(r"hash=[0-9a-f]{12}", log_text), (
            "SEC-009 / G4-H-08: the log line should contain "
            "'hash=<12-char hex prefix>' when log_transcriptions is True."
        )

    def test_redact_pii_masks_email_phone_ssn_cc(self):
        """``redact_pii`` must mask the four documented PII patterns."""
        from voice_typer.server.security import redact_pii

        # Email
        assert "[EMAIL]" in redact_pii("contact me at john.doe@example.com")
        # Phone (US-style)
        assert "[PHONE]" in redact_pii("call me at 555-123-4567")
        # SSN
        assert "[SSN]" in redact_pii("my ssn is 123-45-6789")
        # Credit card
        assert "[CC]" in redact_pii("card 4111-1111-1111-1111")

    def test_redact_pii_preserves_non_pii_text(self):
        from voice_typer.server.security import redact_pii

        text = "Hello world, this is a test transcription."
        assert redact_pii(text) == text


class TestReadCappedAbortsOnOverflow:
    """SEC-030.

    Pre-fix: the ``total > max_bytes`` abort path in ``_read_capped``
    was untested. A malformed server sending >50MB could timeout
    instead of cleanly aborting. Fix: add a test that supplies chunks
    summing >50MB and asserts ``RuntimeError`` is raised.
    """

    def test_read_capped_aborts_on_overflow(self):
        from voice_typer.server.cloud_engines import _read_capped

        # Mock response that yields 100 chunks of 1 MB each = 100 MB > 50 MB cap
        chunk_size = 1024 * 1024  # 1 MB
        chunks_yielded = [0]

        class FakeResp:
            def read(self, n):
                # Yield 1 MB chunks until 100 have been emitted.
                if chunks_yielded[0] >= 100:
                    return b""
                chunks_yielded[0] += 1
                return b"x" * chunk_size

        with pytest.raises(RuntimeError, match="exceeded.*aborting to prevent OOM"):
            _read_capped(FakeResp(), max_bytes=50 * 1024 * 1024)

    def test_read_capped_returns_body_when_under_cap(self):
        from voice_typer.server.cloud_engines import _read_capped

        body = b"hello world" * 100  # ~1.1 KB

        class FakeResp:
            def __init__(self, body):
                self._buf = io.BytesIO(body)

            def read(self, n):
                return self._buf.read(n)

        result = _read_capped(FakeResp(body), max_bytes=50 * 1024 * 1024)
        assert result == body

    def test_read_capped_handles_empty_response(self):
        from voice_typer.server.cloud_engines import _read_capped

        class FakeResp:
            def read(self, n):
                return b""

        assert _read_capped(FakeResp(), max_bytes=1024) == b""

    def test_read_capped_aborts_exactly_at_boundary(self):
        """One byte over the cap must trigger the abort."""
        from voice_typer.server.cloud_engines import _read_capped

        class FakeResp:
            def __init__(self):
                self._calls = 0

            def read(self, n):
                self._calls += 1
                if self._calls == 1:
                    return b"x" * 100  # exactly 100 bytes
                if self._calls == 2:
                    return b"y"  # 1 more byte → total 101 > cap 100
                return b""

        with pytest.raises(RuntimeError):
            _read_capped(FakeResp(), max_bytes=100)


class TestMutexHardenedWithSecurityDescriptor:
    r"""PLAT-040.

    The finding: CreateMutexW with NULL security descriptor and bare
    name. Investigation: the mutex now has ``Local\`` prefix,
    a fixed name (no install-path hash), and a restrictive DACL.
    This test pins that state.
    """

    def test_mutex_name_has_local_prefix(self):
        r"""The mutex name must have Local\ prefix (no install hash).

        RW-8: KEEP — pins PLAT-040 (mutex name has Local\ prefix,
        no install-path hash). A behavioral test would need to spawn
        two processes and observe the mutex collision, which is heavy;
        the source-string check catches reintroduction of the install
        hash directly.
        """
        import inspect

        from voice_typer.server import app

        src = inspect.getsource(app)
        # Check for the mutex name substring (no backslash counting)
        assert "VoiceTyperSingleInstance" in src, "PLAT-040: mutex name must contain VoiceTyperSingleInstance."
        assert "install_hash" not in src, "PLAT-040-FIXED: no install-path hash in app module."

    def test_mutex_uses_restrictive_security_attributes(self):
        # RW-8: KEEP — pins PLAT-040 (mutex uses restrictive DACL via
        # _create_restrictive_security_attributes). A behavioral test
        # would need to inspect the mutex's security descriptor via
        # Windows APIs (heavy, Windows-only); the source-string check
        # catches removal of the helper call directly.
        from voice_typer.server import app

        src = inspect.getsource(app)
        assert "_create_restrictive_security_attributes" in src, (
            "PLAT-040: mutex must use _create_restrictive_security_attributes for a non-NULL DACL."
        )


class TestClipboardRetryNarrowedException:
    """PLAT-007.

    The finding: retry loop caught broad ``Exception``, masking
    permanent failures. Fix: narrow to ``OSError`` with
    ``winerror == 5`` (ERROR_ACCESS_DENIED) check.
    """

    def test_retry_catches_oserror_not_broad_exception(self):
        # RW-8: KEEP — pins PLAT-007 (clipboard retry narrowed to OSError
        # with winerror == 5). A behavioral test would need to trigger
        # ERROR_ACCESS_DENIED on the clipboard, which is Windows-specific
        # and flaky; the source-string check catches reintroduction of
        # the broad Exception catch directly.
        from voice_typer.server import clipboard

        src = inspect.getsource(clipboard)
        # Must use `except OSError as copy_err` (narrowed)
        assert "except OSError as copy_err" in src, "PLAT-007: clipboard retry must catch OSError, not broad Exception"
        # Must check winerror == 5
        assert "winerror == 5" in src, "PLAT-007: clipboard retry must check winerror == 5 (ERROR_ACCESS_DENIED)"

    def test_broad_exception_catch_removed(self):
        """The pre-fix ``except Exception as copy_err`` must NOT be
        present in the retry block.

        RW-8: KEEP — pins the negative half of PLAT-007. Same rationale
        as test_retry_catches_oserror_not_broad_exception.
        """
        from voice_typer.server import clipboard

        src = inspect.getsource(clipboard)
        # The pre-fix pattern was: except Exception as copy_err
        # (inside the PLAT-007 retry block). It must be gone.
        # We check the copy() method source specifically.
        copy_methods = [line for line in src.split("\n") if "except Exception as copy_err" in line]
        assert len(copy_methods) == 0, (
            "PLAT-007: 'except Exception as copy_err' must be removed from "
            "clipboard retry block (use 'except OSError as copy_err' instead)"
        )


class TestComtypesFallbackFailsClosed:
    """PLAT-014.

    The finding: comtypes absence → fail-open (returns True = safe to
    paste). Fix: add credential-dialog window-class heuristic as a
    fallback, and log a WARNING (not INFO) so operators notice.
    """

    def test_cred_dialog_classes_constant_exists(self):
        from voice_typer.server import clipboard

        assert hasattr(clipboard, "_CRED_DIALOG_CLASSES"), (
            "PLAT-014: _CRED_DIALOG_CLASSES constant must exist for the comtypes-absence fallback."
        )
        assert isinstance(clipboard._CRED_DIALOG_CLASSES, set)
        assert len(clipboard._CRED_DIALOG_CLASSES) > 0

    def test_focused_window_is_credential_dialog_exists(self):
        from voice_typer.server import clipboard

        assert hasattr(clipboard, "_focused_window_is_credential_dialog"), (
            "PLAT-014: _focused_window_is_credential_dialog helper must exist."
        )
        assert callable(clipboard._focused_window_is_credential_dialog)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _focused_window_is_credential_dialog short-circuits to False when Win32 FindWindowEx unavailable",
    )
    def test_focused_window_returns_false_on_non_windows(self):
        """On non-Windows platforms, the helper must return False
        (no credential dialogs to detect).
        """
        from voice_typer.server.clipboard import _focused_window_is_credential_dialog

        assert _focused_window_is_credential_dialog() is False

    def test_comtypes_absence_logs_warning_not_info(self):
        """The ImportError handler must log at WARNING level (not INFO)
        so operators notice at default log levels.

        RW-8: KEEP — pins PLAT-014 (comtypes-absence path logs WARNING
        and calls the credential-dialog fallback). A behavioral test
        would need to uninstall comtypes and capture log output, which
        is heavy; the source-string check catches removal of the
        WARNING level or the fallback call directly.

        The source uses the module-level ``_log()`` helper (a
        lazy-init logger getter) rather than a bare ``log`` symbol,
        so the assertion uses a regex that accepts both ``log.warning``
        and ``_log().warning`` forms.
        """
        import re

        from voice_typer.server import clipboard

        src = inspect.getsource(clipboard._is_password_field)
        # Accept both ``log.warning(...)`` and ``_log().warning(...)``
        # forms (the clipboard module uses the lazy ``_log()`` helper).
        assert re.search(r"\b(?:log|_log\(\))\.warning\b", src), (
            "PLAT-014: comtypes-absence must log at WARNING level (not INFO)"
        )
        # Must call the credential-dialog fallback
        assert "_focused_window_is_credential_dialog" in src, (
            "PLAT-014: comtypes-absence path must call _focused_window_is_credential_dialog"
        )


class TestMutexAcquisitionHasRetryAndTimeout:
    """PLAT-011.

    The finding: no retry/timeout for mutex acquisition. Investigation:
    the immediate-exit-on-ERROR_ALREADY_EXISTS is intentional — if
    another instance holds the mutex, it IS running. This test pins
    that behavior so a future "let's add retry" change is caught.
    """

    def test_ensure_single_instance_exits_on_already_exists(self):
        # RW-8: KEEP — pins PLAT-011 (immediate-exit on ERROR_ALREADY_EXISTS,
        # no retry). A behavioral test would need to spawn two processes
        # and observe the exit, which is heavy; the source-string check
        # catches reintroduction of a retry loop directly.
        from voice_typer.server import app as app_mod

        # CR-11: _ensure_single_instance is now a thin dispatcher; the
        # Windows mutex logic (which checks error_already_exists) lives
        # in _ensure_windows_single_instance. Inspect that function so
        # the PLAT-011 invariant is still pinned.
        src = inspect.getsource(app_mod._ensure_windows_single_instance)
        # Must check ERROR_ALREADY_EXISTS and exit. The implementation
        # may use either the symbolic name "ERROR_ALREADY_EXISTS" or the
        # numeric value 183 assigned to a lowercase variable
        # ``error_already_exists`` — both are valid representations of
        # the Windows system error code.
        assert "ERROR_ALREADY_EXISTS" in src or "error_already_exists" in src or "183" in src, (
            "PLAT-011: _ensure_single_instance must check ERROR_ALREADY_EXISTS"
        )
        # The immediate-exit behavior is intentional — no retry loop
        # should be added without explicit design discussion.
        assert "for attempt" not in src or "retry" not in src.lower(), (
            "PLAT-011: _ensure_single_instance intentionally does NOT retry. "
            "Adding retry would delay the 'already running' message to the user."
        )


class TestSystemRootValidationFunctional:
    """PLAT-016.

    The finding: only existence tests for _validate_systemroot, no
    functional test that verifies a malicious SystemRoot is rejected.
    Fix: add a test that sets SystemRoot to an attacker-controlled path
    and verifies the function rejects it.
    """

    def test_validate_systemroot_rejects_traversal(self, monkeypatch):
        """A SystemRoot containing '..' must be rejected (REG-3 / CR-19).

        Original behavior: the function logged a warning and continued
        (fail-open). CR-19 changed this to fail-closed: a path-traversal
        sequence in SystemRoot is a classic DLL-injection vector, so
        ``_validate_systemroot`` now calls ``sys.exit(1)`` instead of
        silently resetting the env var. This test pins the fail-closed
        behavior by patching ``is_windows`` to True (otherwise the
        Linux test runner would short-circuit at the top of the
        function) and asserting that ``SystemExit`` is raised with
        exit code 1.

        Note: ``SYSTEMROOT`` (all-caps) is used here because the
        function reads it via ``os.environ.get("SYSTEMROOT", "")``.
        On Windows env-var names are case-insensitive, but on the
        Linux CI runner ``SystemRoot`` and ``SYSTEMROOT`` are distinct
        — the all-caps form is what the function actually consults.
        """
        from voice_typer.server import config
        from voice_typer.server.config import _validate_systemroot

        # Force the Windows-only code path to execute on the Linux
        # CI runner so the traversal check actually runs.
        monkeypatch.setattr(config, "is_windows", lambda: True)

        # Set SystemRoot to a path with traversal. Use the all-caps
        # form (see docstring above for rationale).
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows\..\..\attacker")

        # CR-19: must abort startup (fail-closed) — not silently reset.
        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1, (
            "REG-3 / CR-19: a malicious SystemRoot containing '..' must "
            "sys.exit(1) (fail-closed), not log+reset (fail-open)."
        )

        # The malicious value must NOT be silently reset — the user
        # must see the startup abort and investigate. (Silently
        # resetting would hide the attack.)
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows\..\..\attacker"

    def test_validate_systemroot_rejects_nonexistent_dir(self, monkeypatch):
        """A SystemRoot pointing to a nonexistent directory must be
        rejected (reset to the safe default ``C:\\Windows``).

        WR-5: previously this test was a complete no-op on Linux CI:
        (1) it set ``SystemRoot`` (mixed-case) but the production
        function reads ``SYSTEMROOT`` (all-caps, case-sensitive on
        Linux); (2) it never patched ``is_windows`` so the function
        early-returned at config.py:204-205 (``if not is_windows():
        return``); (3) the assertion ``"SystemRoot" in os.environ``
        was trivially True because the test itself just set it. The
        "rejects nonexistent dir" branch (config.py:246-262) was
        entirely uncovered. Now mirrors the sibling
        ``test_validate_systemroot_rejects_traversal`` pattern: patch
        ``is_windows`` to True, set all-caps ``SYSTEMROOT``, and
        assert the function resets it to the safe default.
        """
        from voice_typer.server import config
        from voice_typer.server.config import _validate_systemroot

        # Patch is_windows so the function doesn't early-return on Linux.
        monkeypatch.setattr(config, "is_windows", lambda: True)
        # Use all-caps SYSTEMROOT (case-sensitive on Linux; the production
        # function reads os.environ.get("SYSTEMROOT", "")).
        monkeypatch.setenv("SYSTEMROOT", r"C:\Nonexistent\Path\12345")
        _validate_systemroot()
        # The function must reset the malicious value to the safe default
        # (``C:\Windows``) — see config.py:246-262.
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows", (
            "PLAT-016/REG-3: _validate_systemroot() must reset a "
            "nonexistent-dir SYSTEMROOT to 'C:\\\\Windows' (the safe "
            "default). Got: "
            f"{os.environ.get('SYSTEMROOT')!r}"
        )

    def test_validate_systemroot_function_exists_and_is_callable(self):
        from voice_typer.server.config import _validate_systemroot

        assert callable(_validate_systemroot)
