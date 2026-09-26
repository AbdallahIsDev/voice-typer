"""``redact_pii`` home-directory path redaction tests."""

from __future__ import annotations

import pytest
from voice_typer.server.security import redact_pii  # noqa: E402


class TestRedactPiiRedactsHomePath:
    """``redact_pii`` must redact home-directory path prefixes."""

    def test_linux_home_path_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Linux-style home path must not leak the username."""
        monkeypatch.setenv("HOME", "/home/alice")
        result = redact_pii("/home/alice/.lausu/foo.log")
        assert isinstance(result, str)
        assert "alice" not in result, f"Linux OS username leaked via home path in redact_pii output: {result!r}"

    def test_windows_home_path_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Windows-style home path must not leak the username."""
        monkeypatch.setenv("HOME", "C:\\Users\\bob")
        result = redact_pii("C:\\Users\\bob\\.lausu\\foo.log")
        assert isinstance(result, str)
        assert "bob" not in result, f"Windows OS username leaked via home path in redact_pii output: {result!r}"

    def test_macos_home_path_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A macOS-style home path must not leak the username."""
        monkeypatch.setenv("HOME", "/Users/carol")
        result = redact_pii("/Users/carol/.lausu/foo.log")
        assert isinstance(result, str)
        assert "carol" not in result, f"macOS OS username leaked via home path in redact_pii output: {result!r}"

    def test_home_prefix_replaced_with_tilde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The home prefix is replaced with ``~`` (not removed entirely)."""
        monkeypatch.setenv("HOME", "/home/alice")
        result = redact_pii("/home/alice/.lausu/foo.log")
        assert "~" in result, f"Home prefix was not replaced with '~': {result!r}"

    def test_non_home_path_not_mangled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A path that is NOT under the home dir must pass through."""
        monkeypatch.setenv("HOME", "/home/alice")
        result = redact_pii("/var/log/lausu.log")
        assert "/var/log/lausu.log" in result, f"Non-home path was incorrectly redacted: {result!r}"


class TestControlCharEscapingInRedactedText:
    """HU-15: ``_redact_text`` (the ``PIIRedactionFilter`` path) must"""

    def test_newline_forged_line_escaped(self) -> None:
        """A raw newline in the payload becomes the literal two-char"""
        from voice_typer.server.security.redaction import _redact_text

        text = "Hello\n[CRITICAL] fake critical event"
        out = _redact_text(text)
        assert "\n" not in out, f"raw newline survived redaction: {out!r}"
        assert "\\n" in out, f"newline must be escaped to literal \\n: {out!r}"
        # The forged marker text is preserved but now lives on ONE line.
        assert "[CRITICAL]" in out
        assert out.count("\n") == 0

    def test_crlf_escaped(self) -> None:
        from voice_typer.server.security.redaction import _redact_text

        out = _redact_text("line1\r\nline2")
        assert "\r" not in out and "\n" not in out, f"CR/LF survived: {out!r}"
        assert "\\r\\n" in out, f"CRLF must be escaped: {out!r}"

    def test_tab_escaped(self) -> None:
        from voice_typer.server.security.redaction import _redact_text

        out = _redact_text("col1\tcol2")
        assert "\t" not in out, f"tab survived: {out!r}"
        assert "\\t" in out, f"tab must be escaped: {out!r}"

    def test_ansi_escape_escaped(self) -> None:
        """Raw ANSI escape sequences () are neutralised, an"""
        from voice_typer.server.security.redaction import _redact_text

        text = "prefix\x1b[31mred\x1b[0m"
        out = _redact_text(text)
        assert "\x1b" not in out, f"ANSI ESC survived: {out!r}"
        assert "\\x1b" in out, f"ANSI ESC must be escaped to literal \\x1b: {out!r}"

    def test_control_chars_without_pii_triggers_still_escaped(self) -> None:
        """A payload containing ONLY control chars (no email / phone /"""
        from voice_typer.server.security.redaction import _redact_text

        out = _redact_text("spam\r\n[ERROR] forged")
        assert "\r" not in out and "\n" not in out, f"control chars survived: {out!r}"
        assert "[ERROR]" in out

    def test_filter_escapes_control_chars_on_log_record(self) -> None:
        """End-to-end through ``PIIRedactionFilter``: the redacted"""
        import logging

        from voice_typer.server.security import PIIRedactionFilter

        record = logging.LogRecord(
            "test.logger",
            logging.INFO,
            __file__,
            1,
            "x\n[ERROR] forged",
            (),
            None,
        )
        PIIRedactionFilter().filter(record)
        assert "\n" not in record.msg, f"raw newline survived the filter: {record.msg!r}"
        assert "\\n" in record.msg, f"newline must be escaped by the filter: {record.msg!r}"

    def test_traceback_keeps_multiline_structure(self) -> None:
        """traceback path (``record.exc_text``) must KEEP its structural"""
        import logging
        import sys

        from voice_typer.server.security import PIIRedactionFilter

        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            "test.logger",
            logging.ERROR,
            __file__,
            1,
            "line with\nforged newline",
            (),
            exc_info,
        )
        PIIRedactionFilter().filter(record)
        # Message path: control chars escaped.
        assert "\n" not in record.msg, f"raw newline survived the filter: {record.msg!r}"
        assert "\\n" in record.msg, f"newline must be escaped by the filter: {record.msg!r}"
        # Traceback path: structural newlines preserved.
        assert record.exc_text is not None, "exc_text must be populated for exc_info records"
        assert "\n" in record.exc_text, f"traceback must keep its multi-line structure: {record.exc_text!r}"

    def test_pii_still_redacted_alongside_escaping(self) -> None:
        """Escaping must not regress the PII patterns, an email inside"""
        from voice_typer.server.security.redaction import _redact_text

        out = _redact_text("contact user@example.com\n[CRITICAL] hi")
        assert "user@example.com" not in out
        assert "[EMAIL]" in out, f"PII pattern must still fire alongside escaping: {out!r}"
        assert "\n" not in out

    def test_plain_text_unchanged(self) -> None:
        """Text with no control chars and no PII patterns passes through"""
        from voice_typer.server.security.redaction import _redact_text

        plain = "hello world, this is a normal log line"
        assert _redact_text(plain) == plain
