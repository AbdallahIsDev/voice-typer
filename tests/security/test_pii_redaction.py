"""``redact_pii`` home-directory path redaction tests.

Migrated from ``tests/test_security_fixes.py`` (EO-25: the legacy
1292-LOC catch-all was split into per-domain files under
``tests/security/``). This file owns the PII-redaction domain:
``redact_pii`` must redact home-directory path prefixes so the OS
username never leaks into logs, exports, or LLM-polish text.
"""

from __future__ import annotations

import pytest
from voice_typer.server.security import redact_pii  # noqa: E402


class TestRedactPiiRedactsHomePath:
    """``redact_pii`` must redact home-directory path prefixes.

    Pre-fix, ``redact_pii`` applied PII patterns + ``redact_secret`` +
    ``redact_url`` but did NOT call ``_redact_home_path_in_text`` (only
    ``_redact_text`` — used by ``PIIRedactionFilter`` — did). Call sites
    that pass user-visible text through ``redact_pii`` — the cloud-LLM
    polish path (``llm_polish.py``), the hallucination filter, the config
    sanitizer, and ``redact_for_export`` (diagnostic bundles) — therefore
    leaked the OS username whenever a filesystem path was embedded in
    the text.

    The fix adds ``text = _redact_home_path_in_text(text)`` as the FIRST
    line of ``redact_pii``'s body, mirroring ``_redact_text``. These
    regression tests exercise the three OS-specific path layouts that
    ``_redact_home_path_in_text`` must handle (Linux ``/home/…``,
    Windows ``C:\\Users\\…``, macOS ``/Users/…``).
    """

    def test_linux_home_path_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Linux-style home path must not leak the username.

        ``os.path.expanduser("~")`` honours ``HOME`` on POSIX, so setting
        ``HOME=/home/alice`` makes ``_redact_home_path_in_text`` treat
        ``/home/alice`` as the home prefix and replace it with ``~``.
        """
        monkeypatch.setenv("HOME", "/home/alice")
        result = redact_pii("/home/alice/.voice-typer/foo.log")
        assert isinstance(result, str)
        assert "alice" not in result, f"Linux OS username leaked via home path in redact_pii output: {result!r}"

    def test_windows_home_path_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Windows-style home path must not leak the username.

        On POSIX, ``os.path.expanduser("~")`` honours ``HOME``; setting it
        to ``C:\\Users\\bob`` simulates the Windows home layout. The
        redaction is string-prefix driven, so this exercises the same
        code path that runs natively on Windows. On Windows itself,
        ``HOME`` is also consulted by ``expanduser`` (alongside
        ``USERPROFILE``), so the test is portable.
        """
        monkeypatch.setenv("HOME", "C:\\Users\\bob")
        result = redact_pii("C:\\Users\\bob\\.voice-typer\\foo.log")
        assert isinstance(result, str)
        assert "bob" not in result, f"Windows OS username leaked via home path in redact_pii output: {result!r}"

    def test_macos_home_path_redacted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A macOS-style home path must not leak the username."""
        monkeypatch.setenv("HOME", "/Users/carol")
        result = redact_pii("/Users/carol/.voice-typer/foo.log")
        assert isinstance(result, str)
        assert "carol" not in result, f"macOS OS username leaked via home path in redact_pii output: {result!r}"

    def test_home_prefix_replaced_with_tilde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The home prefix is replaced with ``~`` (not removed entirely).

        Guards against an implementation that strips the username but
        leaves the path structure intact — support engineers still need
        to see ``~/.voice-typer/foo.log`` to diagnose path issues.
        """
        monkeypatch.setenv("HOME", "/home/alice")
        result = redact_pii("/home/alice/.voice-typer/foo.log")
        assert "~" in result, f"Home prefix was not replaced with '~': {result!r}"

    def test_non_home_path_not_mangled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A path that is NOT under the home dir must pass through.

        Guards against an over-broad implementation that redacts any
        path-like string. ``/var/log/voice-typer.log`` does not start
        with the home prefix, so ``_redact_home_path_in_text`` must
        return it verbatim.
        """
        monkeypatch.setenv("HOME", "/home/alice")
        result = redact_pii("/var/log/voice-typer.log")
        assert "/var/log/voice-typer.log" in result, f"Non-home path was incorrectly redacted: {result!r}"


class TestControlCharEscapingInRedactedText:
    """HU-15: ``_redact_text`` (the ``PIIRedactionFilter`` path) must
    escape C0 control characters so dictated text (or any
    user-influenced log payload) cannot forge extra log lines.

    Pre-fix, ``_redact_text`` ran 8-12 regex substitutions for PII /
    API-key / URL-credential / home-path patterns but never stripped or
    escaped ``\n`` / ``\r`` / ANSI escapes. A dictated phrase like
    ``"Hello\n[CRITICAL] fake critical event"`` therefore produced a
    second disk line that visually appears as a forged ``[CRITICAL]``
    record (log injection). The fix adds ``_escape_control_chars`` to
    ``_redact_text`` so EVERY log record passing through the filter is
    scrubbed — not just the transcription-text call sites (which are
    gated by the ``config.log_transcriptions`` opt-in).

    ``_redact_text`` is intentionally kept PRIVATE; these tests import
    it from the ``redaction`` submodule directly (same convention as
    the other private helpers the package re-exports for tests).
    """

    def test_newline_forged_line_escaped(self) -> None:
        """A raw newline in the payload becomes the literal two-char
        sequence ``\n`` — the forged ``[CRITICAL]`` stays on the same
        disk line instead of becoming a second log record."""
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
        """Raw ANSI escape sequences (\x1b) are neutralised — an
        attacker cannot paint arbitrary terminal colours or hide text."""
        from voice_typer.server.security.redaction import _redact_text

        text = "prefix\x1b[31mred\x1b[0m"
        out = _redact_text(text)
        assert "\x1b" not in out, f"ANSI ESC survived: {out!r}"
        assert "\\x1b" in out, f"ANSI ESC must be escaped to literal \\x1b: {out!r}"

    def test_control_chars_without_pii_triggers_still_escaped(self) -> None:
        """A payload containing ONLY control chars (no email / phone /
        secret / URL pattern) must still be escaped — this exercises the
        fast-path gate, which must NOT early-return on control-char
        lines (HU-15 added the C0 class to ``_FAST_TRIGGER``)."""
        from voice_typer.server.security.redaction import _redact_text

        out = _redact_text("spam\r\n[ERROR] forged")
        assert "\r" not in out and "\n" not in out, f"control chars survived: {out!r}"
        assert "[ERROR]" in out

    def test_filter_escapes_control_chars_on_log_record(self) -> None:
        """End-to-end through ``PIIRedactionFilter``: the redacted
        message stored on the record has no raw control chars."""
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
        """HU-15 guard: the message path escapes control chars, but the
        traceback path (``record.exc_text``) must KEEP its structural
        newlines — tracebacks are generated by Python's formatter from
        exception objects, not user payloads, so collapsing them would
        be a diagnosability regression with no forgery protection gain."""
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
        assert "\n" in record.exc_text, (
            f"traceback must keep its multi-line structure: {record.exc_text!r}"
        )

    def test_pii_still_redacted_alongside_escaping(self) -> None:
        """Escaping must not regress the PII patterns — an email inside
        the payload is still masked to ``[EMAIL]``."""
        from voice_typer.server.security.redaction import _redact_text

        out = _redact_text("contact user@example.com\n[CRITICAL] hi")
        assert "user@example.com" not in out
        assert "[EMAIL]" in out, f"PII pattern must still fire alongside escaping: {out!r}"
        assert "\n" not in out

    def test_plain_text_unchanged(self) -> None:
        """Text with no control chars and no PII patterns passes through
        byte-for-byte (fast-path short-circuit)."""
        from voice_typer.server.security.redaction import _redact_text

        plain = "hello world, this is a normal log line"
        assert _redact_text(plain) == plain
