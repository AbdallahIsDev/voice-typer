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
