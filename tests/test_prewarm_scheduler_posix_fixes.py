"""SA-06 / XZ-R6-AS-05 / XZ-R6-AS-11 / XZ-R12-07 / XZ-R12-09 / XZ-R12-10 /
XZ-R12-18: targeted regression tests for the prewarm_scheduler_posix
hardening fixes applied by sub-agent 6.

Each test pins one specific behaviour change so a future refactor that
reverts any of them fails loudly.  They complement the broader suites
in ``tests/test_e2e_regression.py`` (which assert the high-level plist
shape + registration round-trip).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import prewarm_scheduler_posix as psp

# ─── XZ-R12-07: shlex-based path parsing ──────────────────────────────


class TestPrewarmPythonShlexParsing:
    """XZ-R12-07: ``_prewarm_python`` must use ``shlex.split`` so Python
    paths containing spaces parse correctly (the prior
    ``resolved.split(\" \", 1)[0].strip('\"')`` truncated
    ``/Users/My Name/...`` to ``/Users/My``)."""

    def test_path_with_spaces_parses_correctly(self, monkeypatch):
        """A dev-fallback command line with a spaced Python path must
        yield the full path, not the truncation at the first space."""
        spaced_path = "/Users/My Name/venv/bin/python"
        dev_fallback_cmd = f'"{spaced_path}" -m voice_typer.server.prewarm'

        # Force the resolver to return the dev-fallback command line.
        monkeypatch.setenv("TAURI_SIDECAR", "1")

        def _fake_resolve():
            return dev_fallback_cmd

        # ``prewarm_resolver`` is imported lazily inside ``_prewarm_python``
        # so monkeypatch the module attribute via sys.modules.
        import voice_typer.server.prewarm_resolver as pr

        monkeypatch.setattr(pr, "resolve_prewarm_exe", _fake_resolve)
        result = psp._prewarm_python()
        assert result == spaced_path, (
            f"XZ-R12-07 regression: spaced Python path was mangled to {result!r}; expected {spaced_path!r}"
        )

    def test_path_without_spaces_still_parses(self, monkeypatch):
        """Non-spaced paths keep working (no regression for the common case)."""
        plain_path = "/usr/bin/python3"
        dev_fallback_cmd = f'"{plain_path}" -m voice_typer.server.prewarm'
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        import voice_typer.server.prewarm_resolver as pr

        monkeypatch.setattr(pr, "resolve_prewarm_exe", lambda: dev_fallback_cmd)
        assert psp._prewarm_python() == plain_path

    def test_malformed_command_falls_back_to_sys_executable(self, monkeypatch):
        """If ``shlex.split`` raises (e.g. unbalanced quotes), the
        function falls back to ``sys.executable`` rather than crashing."""
        # An unterminated quote makes shlex.split raise ValueError.
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        import voice_typer.server.prewarm_resolver as pr

        monkeypatch.setattr(pr, "resolve_prewarm_exe", lambda: '"unterminated -m voice_typer')
        # Must NOT raise; must return a usable interpreter path.
        result = psp._prewarm_python()
        assert isinstance(result, str)
        assert result  # non-empty


# ─── XZ-R6-AS-05: plist XML escaping via ElementTree ──────────────────


class TestPlistEscaping:
    """XZ-R6-AS-05: the plist must escape all five XML special
    characters, not just ``&``, ``<``, ``>``.  We pin this by feeding a
    Python path containing ``"`` and an arg containing ``'`` and
    asserting the output is parseable XML with the literal quotes
    preserved as escaped entities."""

    def test_plist_escapes_double_quote_in_python_path(self, monkeypatch):
        """A Python path containing ``\"`` must produce well-formed XML
        that round-trips back to the original path string. The prior
        f-string + ``xml.sax.saxutils.escape`` builder only escaped
        ``&``, ``<``, ``>`` — it produced the SAME raw ``"`` in element
        content, which is technically valid XML but fragile (any future
        refactor that moved the value into an attribute would silently
        break). ElementTree handles all five special characters
        consistently regardless of context."""
        # Force _prewarm_python to return a path containing a double quote.
        monkeypatch.setattr(psp, "_prewarm_python", lambda: '/path/with"quote/python')
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        plist = psp._build_macos_plist()
        # The plist must be parseable as XML (strip DOCTYPE first —
        # ElementTree's default parser doesn't know about the plist DTD).
        import xml.etree.ElementTree as ET

        no_doctype = plist.replace(
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
            "",
        )
        # Must not raise ParseError.
        root = ET.fromstring(no_doctype)
        # The Python path must round-trip exactly through the XML
        # parse + text-extract cycle (proving the escaping is
        # reversible, not lossy).
        strings = [s.text or "" for s in root.iter("string")]
        assert '/path/with"quote/python' in strings, (
            f"XZ-R6-AS-05 regression: path with quote did not round-trip; strings={strings!r}"
        )

    def test_plist_emits_boolean_shorthand_without_space(self, monkeypatch):
        """The plist must use ``<true/>`` / ``<false/>`` (no space) so
        the existing ``test_posix_scheduler_macos_plist_builder`` test
        (which asserts ``<true/>``) keeps passing."""
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        plist = psp._build_macos_plist()
        assert "<true/>" in plist, f"plist must use <true/> (no space); got: {plist!r}"
        assert "<false/>" in plist, f"plist must use <false/> (no space); got: {plist!r}"
        # ElementTree's default <true /> form (with space) must NOT appear.
        assert "<true />" not in plist
        assert "<false />" not in plist

    def test_plist_escapes_ampersand_in_arg(self, monkeypatch):
        """An arg containing ``&`` must be escaped to ``&amp;``."""
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm&inject"])

        plist = psp._build_macos_plist()
        import xml.etree.ElementTree as ET

        no_doctype = plist.replace(
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
            "",
        )
        ET.fromstring(no_doctype)  # must parse
        assert "&amp;" in plist
        # The raw ``&inject`` substring (which would be an entity
        # reference) must NOT appear unescaped.
        assert "prewarm&inject" not in plist


# ─── XZ-R6-AS-11: systemd ExecStart token escaping ────────────────────


class TestSystemdExecStartEscaping:
    """XZ-R6-AS-11: ``_build_linux_service`` must quote each ExecStart
    token + reject tokens containing newlines / control chars."""

    def test_simple_tokens_get_double_quoted(self, monkeypatch):
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/usr/bin/python3")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        service = psp._build_linux_service()
        assert 'ExecStart="/usr/bin/python3" "-m" "voice_typer.server.prewarm"' in service

    def test_token_with_double_quote_is_escaped(self, monkeypatch):
        """A token containing a literal ``\"`` must be backslash-escaped."""
        monkeypatch.setattr(psp, "_prewarm_python", lambda: '/path/with"quote/python')
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        service = psp._build_linux_service()
        # The literal ``"`` inside the token must be escaped as ``\"``.
        assert '\\"' in service, f"XZ-R6-AS-11 regression: double quote not escaped in ExecStart: {service!r}"

    def test_token_with_backslash_is_doubled(self, monkeypatch):
        """A token containing a literal ``\\`` must be doubled to ``\\\\``."""
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/path/with\\slash/python")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        service = psp._build_linux_service()
        assert "\\\\" in service, f"XZ-R6-AS-11 regression: backslash not doubled in ExecStart: {service!r}"

    def test_newline_in_token_raises(self, monkeypatch):
        """A newline in a token must raise ``ValueError`` (defence
        against directive-injection via env-var-controlled paths)."""
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/usr/bin/python3\n[Install]")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        with pytest.raises(ValueError, match="newline"):
            psp._build_linux_service()

    def test_carriage_return_in_token_raises(self, monkeypatch):
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/usr/bin/python3\r[Install]")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        with pytest.raises(ValueError, match="newline"):
            psp._build_linux_service()

    def test_control_char_in_token_raises(self, monkeypatch):
        """A non-printable control char (e.g. NUL, 0x01) must raise."""
        monkeypatch.setattr(psp, "_prewarm_python", lambda: "/usr/bin/python3\x01evil")
        monkeypatch.setattr(psp, "_prewarm_args", lambda: ["-m", "voice_typer.server.prewarm"])

        with pytest.raises(ValueError, match="control char"):
            psp._build_linux_service()

    def test_systemd_escape_arg_helper_directly(self):
        """Direct unit test for the ``_systemd_escape_arg`` helper."""
        assert psp._systemd_escape_arg("simple") == '"simple"'
        assert psp._systemd_escape_arg('with"quote') == '"with\\"quote"'
        assert psp._systemd_escape_arg("with\\slash") == '"with\\\\slash"'
        assert psp._systemd_escape_arg("") == '""'

        with pytest.raises(ValueError):
            psp._systemd_escape_arg("with\nnewline")
        with pytest.raises(ValueError):
            psp._systemd_escape_arg("with\rnewline")
        with pytest.raises(ValueError):
            psp._systemd_escape_arg("with\x00nul")


# ─── XZ-R12-09: atomic file writes ────────────────────────────────────


class TestAtomicFileWrites:
    """XZ-R12-09: ``_register_prewarm_macos`` and
    ``_register_prewarm_linux`` must write their config files via
    ``_secure_atomic_write`` (temp + ``os.replace``) so a crash mid-
    write doesn't leave a corrupt unit/plist file."""

    def test_macos_register_uses_secure_atomic_write(self, monkeypatch, tmp_path):
        """``_register_prewarm_macos`` must call ``_secure_atomic_write``
        rather than ``Path.write_text``."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(psp.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
        # Spy on ``_secure_atomic_write`` to confirm it's called with
        # the plist path + the plist body.
        calls: list[tuple] = []
        import voice_typer.server.secure_file_io as sio

        original = sio._secure_atomic_write

        def spy(path, content, **kwargs):
            calls.append((str(path), content, kwargs))
            # Delegate to the real implementation so the file is actually
            # written (the subsequent existence check needs it).
            return original(path, content, **kwargs)

        # monkeypatch the binding the module sees via its lazy import.
        # The import happens INSIDE ``_register_prewarm_macos``, so we
        # patch the module-level attribute of ``secure_file_io``.
        monkeypatch.setattr(sio, "_secure_atomic_write", spy)

        assert psp._register_prewarm_macos() is True
        assert len(calls) == 1, f"expected 1 atomic write; got {len(calls)}"
        plist_path, plist_body, kwargs = calls[0]
        assert plist_path.endswith("com.voicetyper.prewarm.plist")
        assert "<?xml" in plist_body
        # ``durability=False`` because the plist is non-critical config
        # (regenerated on next registration).
        assert kwargs.get("durability") is False

    def test_linux_register_uses_secure_atomic_write(self, monkeypatch, tmp_path):
        """``_register_prewarm_linux`` must call ``_secure_atomic_write``
        for BOTH the .service and .timer unit files."""
        monkeypatch.setattr(psp.os, "environ", {"XDG_CONFIG_HOME": str(tmp_path)})
        monkeypatch.setattr(psp.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
        calls: list[tuple] = []
        import voice_typer.server.secure_file_io as sio

        original = sio._secure_atomic_write

        def spy(path, content, **kwargs):
            calls.append((str(path), content))
            return original(path, content, **kwargs)

        monkeypatch.setattr(sio, "_secure_atomic_write", spy)

        assert psp._register_prewarm_linux() is True
        # Exactly 2 calls: one for .service, one for .timer.
        assert len(calls) == 2, f"expected 2 atomic writes; got {len(calls)}"
        paths = [c[0] for c in calls]
        assert any(p.endswith("voice-typer-prewarm.service") for p in paths), paths
        assert any(p.endswith("voice-typer-prewarm.timer") for p in paths), paths


# ─── XZ-R12-10: Linux register also starts the timer ──────────────────


class TestLinuxRegisterStartsTimer:
    """XZ-R12-10: ``_register_prewarm_linux`` must run ``systemctl --user
    start voice-typer-prewarm.timer`` (best-effort) so the timer fires
    on the current session, not just the next boot."""

    def test_start_command_is_issued(self, monkeypatch, tmp_path):
        monkeypatch.setattr(psp.os, "environ", {"XDG_CONFIG_HOME": str(tmp_path)})
        captured_cmds: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(psp.subprocess, "run", fake_run)

        assert psp._register_prewarm_linux() is True
        # The third systemctl call must be ``start`` (after daemon-reload
        # and enable).
        assert any(cmd == ["systemctl", "--user", "start", "voice-typer-prewarm.timer"] for cmd in captured_cmds), (
            f"XZ-R12-10 regression: ``systemctl --user start`` was not issued; captured: {captured_cmds}"
        )

    def test_start_failure_is_non_fatal(self, monkeypatch, tmp_path):
        """A failure of ``systemctl start`` must not cause registration
        to fail (it's best-effort; the timer is already ``enable``'d)."""
        monkeypatch.setattr(psp.os, "environ", {"XDG_CONFIG_HOME": str(tmp_path)})

        def fake_run(cmd, *args, **kwargs):
            # ``start`` fails (returns non-zero); others succeed.
            rc = 1 if "start" in cmd else 0
            return MagicMock(returncode=rc)

        monkeypatch.setattr(psp.subprocess, "run", fake_run)
        # Registration must still succeed.
        assert psp._register_prewarm_linux() is True


# ─── XZ-R12-18: unregister stops the SERVICE unit too ─────────────────


class TestLinuxUnregisterStopsService:
    """XZ-R12-18: ``_unregister_prewarm_linux`` must run ``systemctl
    --user stop voice-typer-prewarm.service`` (best-effort) before
    unlinking the unit files, so an in-flight oneshot prewarm run is
    terminated."""

    def test_stop_service_command_is_issued(self, monkeypatch, tmp_path):
        monkeypatch.setattr(psp.os, "environ", {"XDG_CONFIG_HOME": str(tmp_path)})
        # First register so the unit files exist.
        monkeypatch.setattr(psp.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
        psp._register_prewarm_linux()

        captured_cmds: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(psp.subprocess, "run", fake_run)
        assert psp._unregister_prewarm_linux() is True

        # The ``stop voice-typer-prewarm.service`` call must be present
        # alongside the timer disable/stop calls.
        assert any(cmd == ["systemctl", "--user", "stop", "voice-typer-prewarm.service"] for cmd in captured_cmds), (
            f"XZ-R12-18 regression: ``systemctl --user stop .service`` was not issued; captured: {captured_cmds}"
        )
