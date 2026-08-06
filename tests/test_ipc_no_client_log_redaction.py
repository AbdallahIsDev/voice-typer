"""CR-8 regression tests: no-client push path must not log transcription text.

The bug
-------
``IPCServer._send`` (in ``voice_typer/server/ipc_server.py``) has a
"no client connected" code path that logs every dropped push event at
INFO level with the full message body::

    log.info("[IPC] no client; dropping %s event: %s", msg_type, msg)

Push events include ``transcription_partial`` and
``transcription_final`` which carry the dictated text — i.e. user PII.
Logging them to the file handler leaks dictated content (passwords,
medical dictation, private correspondence) to the log file.

The fix
-------
Replace the body-logging call with a size-only hint::

    log.info(
        "[IPC] no client; dropping %s event (size=%d)",
        msg_type, len(str(msg)),
    )

The operator still sees the drop rate (type + size) but the dictated
text never reaches the log.

These tests verify the no-client path does NOT include the message
body in the log record, while still emitting the type and a size
hint.  Each test FAILS if the old ``%s`` body format is restored.
"""

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
        """The source of ``_send`` must NOT format ``msg`` into the
        no-client log line.  The old format string was
        ``"... dropping %s event: %s", msg_type, msg`` — the new format
        is ``"... dropping %s event (size=%d)", msg_type, len(str(msg))``.
        """
        src = inspect.getsource(IPCServer._send)
        # The old format string included "event: %s" with msg as the
        # second arg.  The new format uses "event (size=%d)" with
        # len(str(msg)) as the second arg.
        assert "event: %s" not in src, (
            "_send must NOT log the message body via 'event: %s' format "
            "(CR-8 PII leak). Push events carry transcription text."
        )
        assert "event (size=%d)" in src, (
            "_send must use the redacted 'event (size=%d)' format that logs only the message type and a size hint."
        )
        # The body must NOT be passed as a log arg (only msg_type and
        # the size).  Look at the log.info call args in the source.
        assert "len(str(msg))" in src, (
            "_send must pass len(str(msg)) — not msg itself — as the size hint so the dictated text isn't interpolated."
        )

    def test_no_client_log_does_not_include_transcription_text(self, server, caplog):
        """When a ``transcription_final`` event is dropped (no client
        connected), the log record MUST NOT contain the dictated text.
        """
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
        """The log record must include a ``size=N`` hint that matches
        ``len(str(msg))`` for the dropped event.
        """
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
        """``transcription_partial`` events also carry dictated text
        and must be redacted.
        """
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
        """``vocabulary_suggestion`` events carry user-typed vocabulary
        and must also be redacted (they're in the shutdown allowlist
        but still go through the no-client path when no client is
        connected).
        """
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
        """``bubble_level`` and ``waveform`` events must STILL be logged
        at DEBUG (not INFO) — CR-8 only redacts the body of the INFO
        log; the high-freq DEBUG log must remain so the operator can
        filter it out if needed.
        """
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
