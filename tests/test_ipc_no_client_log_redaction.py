"""regression tests: no-client push path must not log transcription text."""

from __future__ import annotations

import inspect
import logging
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer


@pytest.fixture
def server():
    """Build a minimal IPCServer for unit-testing ``_send``."""
    server = IPCServer.__new__(IPCServer)
    server.app = MagicMock()
    server.app._shutting_down = False
    server._lock = threading.RLock()
    server._pending_tcp = []
    server._tcp_mode = False  # no client, no TCP mode → "no client" log path
    server._tcp_client = None
    return server


class TestNoClientLogRedaction:
    """the no-client push log must not include the message body."""

    def test_send_source_does_not_log_msg_body(self):
        """``\"... dropping %s event: %s\", msg_type, msg``, the new format"""
        src = inspect.getsource(IPCServer._send)
        assert "event: %s" not in src, (
            "_send must NOT log the message body via 'event: %s' format "
            "(CR-8 PII leak). Push events carry transcription text."
        )
        assert "event (size=%d)" in src, (
            "_send must use the redacted 'event (size=%d)' format that logs only the message type and a size hint."
        )
        # The body must NOT be passed as a log arg (only msg_type and
        assert "len(str(msg))" in src, (
            "_send must pass len(str(msg)) (not msg itself) as the size hint so the dictated text isn't interpolated."
        )

    def test_no_client_log_does_not_include_transcription_text(self, server, caplog):
        """When a ``transcription_final`` event is dropped (no client"""
        secret_text = "super-secret-dictated-password-12345"
        msg = {"type": "transcription_final", "text": secret_text}

        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            server._send(msg)

        # The drop must have been logged (at INFO).
        drop_records = [r for r in caplog.records if "no client; dropping" in r.getMessage()]
        assert drop_records, (
            "Expected an INFO log record about the dropped event. "
            "If no record was emitted, the no-client path may have changed."
        )
        # the dictated text must NOT appear in the log.
        for record in drop_records:
            assert secret_text not in record.getMessage(), (
                "CR-8 PII leak: the no-client drop log includes the dictated "
                f"text {secret_text!r}. Only the type and a size hint may be "
                "logged."
            )
            # The type MUST be logged (operator needs to see drop rate).
            assert "transcription_final" in record.getMessage(), (
                "The event type must be logged so the operator can see drop rate."
            )
            # A size hint MUST be logged.
            assert "size=" in record.getMessage(), (
                "The size hint (size=N) must be logged so the operator can tell a 100-byte event from a 100-KB one."
            )

    def test_no_client_log_includes_size_hint(self, server, caplog):
        """The log record must include a ``size=N`` hint that matches"""
        msg = {"type": "state_changed", "value": "idle"}
        expected_size = len(str(msg))

        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            server._send(msg)

        drop_records = [r for r in caplog.records if "no client; dropping" in r.getMessage()]
        assert drop_records
        record = drop_records[-1]
        assert f"size={expected_size}" in record.getMessage(), (
            f"Expected 'size={expected_size}' in log message, got: {record.getMessage()!r}"
        )

    def test_no_client_log_redacts_partial_transcription_too(self, server, caplog):
        """``transcription_partial`` events also carry dictated text"""
        secret_partial = "my credit card number is "
        msg = {"type": "transcription_partial", "text": secret_partial}

        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            server._send(msg)

        for record in caplog.records:
            if "no client; dropping" in record.getMessage():
                assert secret_partial not in record.getMessage(), (
                    "CR-8 PII leak: transcription_partial text reached the log file."
                )

    def test_no_client_log_redacts_vocabulary_suggestion(self, server, caplog):
        """``vocabulary_suggestion`` events carry user-typed vocabulary"""
        secret_vocab = "my-private-medical-term"
        msg = {"type": "vocabulary_suggestion", "word": secret_vocab}

        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            server._send(msg)

        for record in caplog.records:
            if "no client; dropping" in record.getMessage():
                assert secret_vocab not in record.getMessage(), (
                    "CR-8 PII leak: vocabulary_suggestion text reached the log file."
                )

    def test_high_freq_bubble_level_still_uses_debug(self, server, caplog):
        """``bubble_level`` and ``waveform`` events must STILL be logged"""
        msg = {"type": "bubble_level", "level": 0.42}

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"):
            server._send(msg)

        debug_records = [r for r in caplog.records if "no client; dropping high-freq" in r.getMessage()]
        assert debug_records, (
            "bubble_level events must still be logged at DEBUG (not INFO) so they don't flood the INFO log."
        )
        assert all(r.levelno == logging.DEBUG for r in debug_records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
