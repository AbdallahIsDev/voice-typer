"""The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import inspect
import io
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestTranscriptionLoggingRedactsPii:
    """but never called from production. The original SEC-009 fix wired"""

    def test_store_result_logs_sha256_hash_not_raw_text(self, caplog):
        """when ``log_transcriptions`` is enabled,"""
        import hashlib
        import logging

        from voice_typer.server.dictation_pipeline import DictationPipeline

        # Build a minimal app mock. ``_store_result`` touches:
        app = MagicMock()
        app.config.log_transcriptions = True
        app.config.crash_recovery_enabled = False
        app.config.model_size = "tiny.en"
        app.config.device = "cpu"

        pipeline = DictationPipeline(app)

        raw_text = "Contact john.doe@example.com for the biopsy results."

        # Patch ``hashlib.sha256`` so we can assert the hash path is
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

        assert sha256_calls, (
            "_store_result must call hashlib.sha256 on the transcription text when log_transcriptions is True."
        )
        assert any(raw_text.encode("utf-8") in call_args for call_args in sha256_calls), (
            "hashlib.sha256 must be called with the raw transcription text as its argument."
        )

        # The raw transcription text must NOT appear in any log line —
        # core SEC-009 invariant: PII never leaks to the log file.
        log_text = caplog.text
        assert raw_text not in log_text, (
            "the raw transcription text must NOT appear "
            "in the log output, only a non-reversible SHA-256 hash prefix "
            "should be logged. The raw text was found in the log."
        )
        # Belt-and-suspenders: verify the hash-prefix form is present
        import re

        assert re.search(r"hash=[0-9a-f]{12}", log_text), (
            "the log line should contain 'hash=<12-char hex prefix>' when log_transcriptions is True."
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

    def test_transcribe_segment_log_redacts_pii_when_log_transcriptions_true(self, caplog):
        """``TranscriptionEngine._transcribe_unlocked``"""
        import logging
        import threading
        import types
        from unittest.mock import MagicMock

        import numpy as np
        from voice_typer.server.config import Config
        from voice_typer.server.transcription import TranscriptionEngine

        # Build a minimal Config with log_transcriptions=True.
        cfg = Config()
        cfg.log_transcriptions = True

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine.config = cfg
        engine.beam_size = 1
        engine.best_of = 1
        engine.language = "en"
        engine.condition_on_previous_text = False
        engine._lock = threading.Lock()
        # ``_transcribe_unlocked`` checks ``self._abort_event.is_set()``
        engine._abort_event = threading.Event()

        # Segment stub: provides .start, .end, .text, .avg_logprob,
        seg = types.SimpleNamespace(
            start=0.0,
            end=1.0,
            text="Contact john.doe@example.com for details.",
            avg_logprob=-0.5,
            no_speech_prob=0.01,
        )
        info = types.SimpleNamespace(
            language="en",
            language_probability=1.0,
        )

        # Patch the model's transcribe to return our segment + info.
        engine._model = MagicMock()
        engine._model.transcribe.return_value = (iter([seg]), info)

        # Stub out the hallucination rejection so it doesn't interfere.
        engine._should_reject_low_audio_hallucination = lambda **kwargs: False

        audio = np.zeros(16000, dtype=np.float32)
        audio[1000:1100] = 0.1  # non-silence so RMS > 0.001

        raw_email = "john.doe@example.com"

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            engine._transcribe_unlocked(audio)

        log_text = caplog.text
        assert raw_email not in log_text, (
            "raw PII (email) must NOT appear in the "
            "transcription segment DEBUG log when log_transcriptions is "
            "True. redact_pii should have masked it as [EMAIL]."
        )
        assert "[EMAIL]" in log_text, (
            "when log_transcriptions is True, segment "
            "text should be passed through redact_pii before logging, "
            "producing the [EMAIL] token for an email pattern."
        )

    def test_transcribe_segment_log_emits_no_text_when_log_transcriptions_false(self, caplog):
        """/ SEC-009 (negative case): when ``log_transcriptions``"""
        import logging
        import threading
        import types
        from unittest.mock import MagicMock

        import numpy as np
        from voice_typer.server.config import Config
        from voice_typer.server.transcription import TranscriptionEngine

        cfg = Config()
        cfg.log_transcriptions = False  # default, no transcription logging

        engine = TranscriptionEngine.__new__(TranscriptionEngine)
        engine._model = MagicMock()
        engine.config = cfg
        engine.beam_size = 1
        engine.best_of = 1
        engine.language = "en"
        engine.condition_on_previous_text = False
        engine._lock = threading.Lock()
        # ``_transcribe_unlocked`` checks ``self._abort_event.is_set()``
        engine._abort_event = threading.Event()

        seg_text = "My SSN is 123-45-6789 and card 4111-1111-1111-1111."
        seg = types.SimpleNamespace(
            start=0.0,
            end=1.0,
            text=seg_text,
            avg_logprob=-0.5,
            no_speech_prob=0.01,
        )
        info = types.SimpleNamespace(language="en", language_probability=1.0)
        engine._model.transcribe.return_value = (iter([seg]), info)
        engine._should_reject_low_audio_hallucination = lambda **kwargs: False

        audio = np.zeros(16000, dtype=np.float32)
        audio[1000:1100] = 0.1

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.transcription"):
            engine._transcribe_unlocked(audio)

        log_text = caplog.text
        # The raw segment text (containing SSN + CC patterns) MUST NOT
        assert seg_text not in log_text, (
            "when log_transcriptions is False, the "
            "segment DEBUG log must NOT contain raw segment text, only "
            "the char count + timestamps."
        )
        # And the redaction tokens should NOT appear either (because
        assert "[SSN]" not in log_text, (
            "when log_transcriptions is False, the "
            "segment DEBUG log must not emit any text content, not "
            "even redacted text."
        )
        assert "[CC]" not in log_text


class TestReadCappedAbortsOnOverflow:
    """Pre-fix: the ``total > max_bytes`` abort path in ``_read_capped``"""

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
    """
    The finding: CreateMutexW with NULL security descriptor and bare
    This test pins that state.
    """

    def test_mutex_name_has_local_prefix(self):
        r"""
        The mutex name must have Local\ prefix (no install hash).
        KEEP, pins PLAT-040 (mutex name has Local\ prefix,
        """
        import inspect

        from voice_typer.server import app

        src = inspect.getsource(app)
        # Check for the mutex name substring (no backslash counting)
        assert "LausuSingleInstance" in src, "PLAT-040: mutex name must contain LausuSingleInstance."
        assert "install_hash" not in src, "PLAT-040-FIXED: no install-path hash in app module."

    def test_mutex_uses_restrictive_security_attributes(self):
        # KEEP, pins  (mutex uses restrictive DACL via
        from voice_typer.server import app

        src = inspect.getsource(app)
        assert "_create_restrictive_security_attributes" in src, (
            "PLAT-040: mutex must use _create_restrictive_security_attributes for a non-NULL DACL."
        )


class TestClipboardRetryNarrowedException:
    """The finding: retry loop caught broad ``Exception``, masking"""

    def test_retry_catches_oserror_not_broad_exception(self):
        # KEEP, pins  (clipboard retry narrowed to OSError
        from voice_typer.server import clipboard

        src = inspect.getsource(clipboard)
        # Must use `except OSError as copy_err` (narrowed)
        assert "except OSError as copy_err" in src, "PLAT-007: clipboard retry must catch OSError, not broad Exception"
        # Must check winerror == 5
        assert "winerror == 5" in src, "PLAT-007: clipboard retry must check winerror == 5 (ERROR_ACCESS_DENIED)"

    def test_broad_exception_catch_removed(self):
        """
        The pre-fix ``except Exception as copy_err`` must NOT be
        RW-8: KEEP, pins the negative half of PLAT-007. Same rationale
        """
        from voice_typer.server import clipboard

        src = inspect.getsource(clipboard)
        # The pre-fix pattern was: except Exception as copy_err
        copy_methods = [line for line in src.split("\n") if "except Exception as copy_err" in line]
        assert len(copy_methods) == 0, (
            "PLAT-007: 'except Exception as copy_err' must be removed from "
            "clipboard retry block (use 'except OSError as copy_err' instead)"
        )


class TestComtypesFallbackFailsClosed:
    """fallback, and log a WARNING (not INFO) so operators notice."""

    def test_cred_dialog_classes_constant_exists(self):
        from voice_typer.server import clipboard

        assert hasattr(clipboard, "_CRED_DIALOG_CLASSES"), (
            "_CRED_DIALOG_CLASSES constant must exist for the comtypes-absence fallback."
        )
        assert isinstance(clipboard._CRED_DIALOG_CLASSES, set)
        assert len(clipboard._CRED_DIALOG_CLASSES) > 0

    def test_focused_window_is_credential_dialog_exists(self):
        from voice_typer.server import clipboard

        assert hasattr(clipboard, "_focused_window_is_credential_dialog"), (
            "_focused_window_is_credential_dialog helper must exist."
        )
        assert callable(clipboard._focused_window_is_credential_dialog)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: _focused_window_is_credential_dialog short-circuits to False when Win32 FindWindowEx unavailable",  # noqa: E501
    )
    def test_focused_window_returns_false_on_non_windows(self):
        """On non-Windows platforms, the helper must return False"""
        from voice_typer.server.clipboard import _focused_window_is_credential_dialog

        assert _focused_window_is_credential_dialog() is False

    def test_comtypes_absence_logs_warning_not_info(self):
        """The ImportError handler must log at WARNING level (not INFO)"""
        import re

        from voice_typer.server import clipboard

        src = inspect.getsource(clipboard._is_password_field)
        # Accept both ``log.warning(...)`` and ``_log().warning(...)``
        assert re.search(r"\b(?:log|_log\(\))\.warning\b", src), "comtypes-absence must log at WARNING level (not INFO)"
        # Must call the credential-dialog fallback
        assert "_focused_window_is_credential_dialog" in src, (
            "comtypes-absence path must call _focused_window_is_credential_dialog"
        )


class TestMutexAcquisitionHasRetryAndTimeout:
    """The finding: no retry/timeout for mutex acquisition. Investigation:"""

    def test_ensure_single_instance_exits_on_already_exists(self):
        # KEEP, pins  (immediate-exit on ERROR_ALREADY_EXISTS,
        from voice_typer.server import app as app_mod

        src = inspect.getsource(app_mod._ensure_windows_single_instance)
        # Must check ERROR_ALREADY_EXISTS and exit. The implementation
        assert "ERROR_ALREADY_EXISTS" in src or "error_already_exists" in src or "183" in src, (
            "_ensure_single_instance must check ERROR_ALREADY_EXISTS"
        )
        # The immediate-exit behavior is intentional, no retry loop
        assert "for attempt" not in src or "retry" not in src.lower(), (
            "_ensure_single_instance intentionally does NOT retry. "
            "Adding retry would delay the 'already running' message to the user."
        )


class TestSystemRootValidationFunctional:
    """The finding: only existence tests for _validate_systemroot, no"""

    def test_validate_systemroot_rejects_traversal(self, monkeypatch):
        """A SystemRoot containing '..' must be rejected (REG-3 / CR-19)."""
        from voice_typer.server import config
        from voice_typer.server.config import _validate_systemroot

        # Force the Windows-only code path to execute on the Linux
        monkeypatch.setattr(config, "is_windows", lambda: True)

        # Set SystemRoot to a path with traversal. Use the all-caps
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows\..\..\attacker")

        with pytest.raises(SystemExit) as exc_info:
            _validate_systemroot()
        assert exc_info.value.code == 1, (
            "a malicious SystemRoot containing '..' must sys.exit(1) (fail-closed), not log+reset (fail-open)."
        )

        # The malicious value must NOT be silently reset, the user
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows\..\..\attacker"

    def test_validate_systemroot_rejects_nonexistent_dir(self, monkeypatch):
        """A SystemRoot pointing to a nonexistent directory must be"""
        from voice_typer.server import config
        from voice_typer.server.config import _validate_systemroot

        # Patch is_windows so the function doesn't early-return on Linux.
        monkeypatch.setattr(config, "is_windows", lambda: True)
        # Use all-caps SYSTEMROOT (case-sensitive on Linux; the production
        user_root = r"C:\Nonexistent\Path\12345"
        monkeypatch.setenv("SYSTEMROOT", user_root)

        # Mock Path so the user-supplied path reports is_dir()=False
        default_root = r"C:\Windows"

        class _FakePath:
            def __init__(self, s):
                self._s = str(s)

            def __truediv__(self, other):
                return _FakePath(self._s + "\\" + str(other))

            def is_dir(self):
                return self._s == default_root

            def exists(self):
                return True

            def __str__(self):
                return self._s

        monkeypatch.setattr(config, "Path", _FakePath)

        _validate_systemroot()
        # The function must reset the malicious value to the safe default
        assert os.environ.get("SYSTEMROOT") == r"C:\Windows", (
            "PLAT-016/REG-3: _validate_systemroot() must reset a "
            "nonexistent-dir SYSTEMROOT to 'C:\\\\Windows' (the safe "
            "default). Got: "
            f"{os.environ.get('SYSTEMROOT')!r}"
        )

    def test_validate_systemroot_function_exists_and_is_callable(self):
        from voice_typer.server.config import _validate_systemroot

        assert callable(_validate_systemroot)
