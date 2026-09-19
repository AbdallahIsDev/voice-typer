"""Domain: SEC-10, generated PowerShell .lnk-creation scripts must wrap"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestPsSingleQuote:
    """SEC-10: ``_ps_single_quote`` must produce a PowerShell"""

    @pytest.mark.parametrize(
        "dangerous_char,description",
        [
            ("$", "dollar, variable expansion"),
            ("`", "backtick, escape sequences (e.g. `n, `t)"),
            (";", "semicolon, statement chaining"),
            ("|", "pipe, pipeline operator"),
            ("&", "ampersand, call operator"),
            ("(", "open-paren, grouping"),
            (")", "close-paren, grouping"),
            ("<", "less-than, input redirection"),
            (">", "greater-than, output redirection"),
            ('"', "double-quote, was the only char escaped pre-SEC-10"),
            ("\n", "newline, statement separator"),
            ("\t", "tab, whitespace in commands"),
        ],
        ids=[
            "dollar",
            "backtick",
            "semicolon",
            "pipe",
            "ampersand",
            "open-paren",
            "close-paren",
            "less-than",
            "greater-than",
            "double-quote",
            "newline",
            "tab",
        ],
    )
    def test_dangerous_character_preserved_literal(self, dangerous_char, description):
        """single-quoted string, no expansion, no escape-sequence"""
        from voice_typer.server.server_platform import _ps_single_quote

        value = f"prefix{dangerous_char}suffix"
        quoted = _ps_single_quote(value)
        # The result must be wrapped in single quotes.
        assert quoted.startswith("'"), f"expected leading single quote, got {quoted!r}"
        assert quoted.endswith("'"), f"expected trailing single quote, got {quoted!r}"
        inner = quoted[1:-1]
        assert dangerous_char in inner, (
            f"dangerous char {dangerous_char!r} ({description}) must "
            f"appear literally inside the single-quoted string; "
            f"inner content was {inner!r}."
        )

    def test_single_quote_doubled(self):
        """Embedded single quotes must be doubled (``'`` → ``''``)."""
        from voice_typer.server.server_platform import _ps_single_quote

        assert _ps_single_quote("a'b") == "'a''b'"
        assert _ps_single_quote("'") == "''''"
        assert _ps_single_quote("''") == "''''''"

    def test_empty_string(self):
        """An empty value produces an empty single-quoted string ``''``."""
        from voice_typer.server.server_platform import _ps_single_quote

        assert _ps_single_quote("") == "''"

    def test_non_string_input_stringified(self):
        """Non-string inputs are stringified via ``str()`` before quoting."""
        from voice_typer.server.server_platform import _ps_single_quote

        assert _ps_single_quote(42) == "'42'"
        assert _ps_single_quote(3.14) == "'3.14'"
        assert _ps_single_quote(None) == "'None'"

    def test_no_double_quote_escaping(self):
        """SEC-10 regression: double quotes must NOT be doubled."""
        from voice_typer.server.server_platform import _ps_single_quote

        # Double quote must appear ONCE, not doubled.
        assert _ps_single_quote('a"b') == "'a\"b'"
        assert '""' not in _ps_single_quote('a"b')


class TestBuildPowershellLnkScript:
    """SEC-10: the generated .lnk-creation PowerShell script must wrap"""

    def _build(self, **overrides):
        """Helper: build a script with sensible defaults + overrides."""
        from voice_typer.server.server_platform import _build_powershell_lnk_script

        defaults = dict(
            lnk_path=Path("C:\\Users\\test\\Desktop\\Voice Typer.lnk"),
            target="C:\\Python311\\pythonw.exe",
            arguments='"C:\\app\\autostart_launcher.py"',
            icon_ico=None,
            description="Voice Typer, voice-to-text dictation",
        )
        defaults.update(overrides)
        return _build_powershell_lnk_script(**defaults)

    def test_uses_single_quoted_strings_not_double(self):
        """The generated script must wrap values in single-quoted"""
        script = self._build()
        # The CreateShortcut call must use a single-quoted argument.
        assert "$s.CreateShortcut('" in script, (
            f"CreateShortcut must use a single-quoted argument; script was:\n{script}"
        )
        # The pre-SEC-10 form `CreateShortcut("` must NOT appear.
        assert '$s.CreateShortcut("' not in script, (
            f"CreateShortcut must NOT use a double-quoted argument (pre-SEC-10 pattern); script was:\n{script}"
        )
        # Same for the other property assignments.
        assert "$l.TargetPath = '" in script
        assert "$l.Arguments = '" in script
        assert "$l.Description = '" in script
        assert "$l.WorkingDirectory = '" in script
        # The pre-SEC-10 forms must NOT appear.
        assert '$l.TargetPath = "' not in script
        assert '$l.Arguments = "' not in script
        assert '$l.Description = "' not in script
        assert '$l.WorkingDirectory = "' not in script

    def test_icon_location_when_provided(self):
        """When ``icon_ico`` is provided, the IconLocation line uses"""
        script = self._build(icon_ico=Path("C:\\app\\icon.ico"))
        assert "$l.IconLocation = '" in script, (
            f"IconLocation must use a single-quoted argument when icon_ico is provided; script was:\n{script}"
        )
        assert '$l.IconLocation = "' not in script

    def test_no_icon_location_when_absent(self):
        """When ``icon_ico`` is None, the IconLocation line is absent."""
        script = self._build(icon_ico=None)
        assert "IconLocation" not in script

    def test_injection_in_description_neutralized(self):
        """A malicious description like ``'; Remove-Item C:\\ -Recurse; '``"""
        malicious = "'; Remove-Item C:\\ -Recurse; '"
        script = self._build(description=malicious)
        # The malicious description must appear with each `'` doubled.
        expected_escaped = malicious.replace("'", "''")
        assert expected_escaped in script, (
            f"malicious description must be escaped via doubling of "
            f"single quotes; expected escaped form {expected_escaped!r} "
            f"in script:\n{script}"
        )
        # The raw un-escaped malicious payload must NOT appear as the
        # description value. Pre-SEC-10 the description was wrapped in
        # as a separate statement (the injection). Post-SEC-10 the
        assert "$l.Description = '';" not in script, (
            f"raw un-escaped payload `= '';` must not appear in "
            f"script (would indicate the embedded `'` was NOT doubled "
            f"and could break out of the single-quoted context):\n{script}"
        )
        # Sanity: the escaped form (three single quotes after ``=``)
        assert "$l.Description = '''" in script, f"escaped form `= '''` must appear in script:\n{script}"

    @pytest.mark.parametrize(
        "dangerous_char",
        ["$", "`", ";", "|", "&", "(", ")", "<", ">", '"', "\n"],
    )
    def test_dangerous_char_in_target_path_is_literal(self, dangerous_char):
        """A target path containing a dangerous character must have"""
        target = f"C:\\path with {dangerous_char} char\\pythonw.exe"
        script = self._build(target=target)
        # The dangerous character must appear inside the single-quoted
        assert dangerous_char in script, (
            f"dangerous char {dangerous_char!r} must appear literally "
            f"in the generated script (inside a single-quoted string); "
            f"script was:\n{script}"
        )
        # The TargetPath line must be single-quoted.
        assert "$l.TargetPath = '" in script

    @pytest.mark.parametrize(
        "dangerous_char",
        ["$", "`", ";", "|", "&", "(", ")", "<", ">", '"', "\n"],
    )
    def test_dangerous_char_in_arguments_is_literal(self, dangerous_char):
        """Arguments containing a dangerous character must preserve"""
        arguments = f'"C:\\launcher.py" --opt {dangerous_char}value'
        script = self._build(arguments=arguments)
        assert dangerous_char in script, (
            f"dangerous char {dangerous_char!r} must appear literally "
            f"in the generated script (inside a single-quoted string); "
            f"script was:\n{script}"
        )
        assert "$l.Arguments = '" in script

    @pytest.mark.parametrize(
        "dangerous_char",
        ["$", "`", ";", "|", "&", "(", ")", "<", ">", '"', "\n"],
    )
    def test_dangerous_char_in_description_is_literal(self, dangerous_char):
        """Description containing a dangerous character must preserve"""
        description = f"Voice Typer {dangerous_char} dictation"
        script = self._build(description=description)
        assert dangerous_char in script, (
            f"dangerous char {dangerous_char!r} must appear literally "
            f"in the generated script (inside a single-quoted string); "
            f"script was:\n{script}"
        )
        assert "$l.Description = '" in script

    def test_script_ends_with_save(self):
        """The script must end with ``$l.Save()``, the actual"""
        script = self._build()
        assert script.rstrip().endswith("$l.Save()"), f"script must end with $l.Save(); got tail: {script[-60:]!r}"

    def test_script_starts_with_com_object_creation(self):
        """The script must start by creating the WScript.Shell COM"""
        script = self._build()
        assert script.startswith("$s = New-Object -ComObject WScript.Shell"), (
            f"script must start with $s = New-Object -ComObject WScript.Shell; got head: {script[:80]!r}"
        )


class TestCreateLnkShortcutIntegration:
    """win32com path is unavailable. We mock subprocess.run so no real"""

    def test_command_invocation_uses_single_quoted_strings(self, tmp_path, monkeypatch):
        """The PowerShell script passed via ``-Command`` must contain"""
        # Force the win32com ImportError path so the PowerShell
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "win32com.client" or name.startswith("win32com.client."):
                raise ImportError("mocked: win32com not available")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        # Capture the cmd list passed to subprocess.run so we can
        captured: dict = {}

        class _FakeCompletedProcess:
            returncode = 0
            stdout = b""
            stderr = b""

        def _fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            # ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            captured["script"] = cmd[-1]
            return _FakeCompletedProcess()

        monkeypatch.setattr("voice_typer.server.server_platform.subprocess.run", _fake_run)

        from voice_typer.server.server_platform import _create_lnk_shortcut

        # Use a description containing a dangerous character to verify
        result = _create_lnk_shortcut(
            lnk_path=Path("C:\\test\\Voice Typer.lnk"),
            target="C:\\Python311\\pythonw.exe",
            arguments='"C:\\app\\launcher.py"',
            icon_ico=None,
            description="Voice Typer; dictation",
        )

        assert result is True, "shortcut creation should have succeeded"
        assert "script" in captured, "subprocess.run was not invoked"
        cmd = captured["cmd"]
        assert "-Command" in cmd, f"must use -Command (XZ-R6-AS-08); got cmd: {cmd}"
        assert "-File" not in cmd, f"must NOT use -File (XZ-R6-AS-08 TOCTOU); got cmd: {cmd}"
        content = captured["script"]

        # The script must use single-quoted strings.
        assert "$s.CreateShortcut('" in content, f"script must use single-quoted strings; was:\n{content}"
        assert '$s.CreateShortcut("' not in content
        assert "Voice Typer; dictation" in content, (
            f"semicolon in description must appear literally inside single-quoted string; script was:\n{content}"
        )


def test_sec10_helper_functions_exist():
    """SEC-10: ``server_platform`` module must expose the new"""
    from voice_typer.server import server_platform

    assert hasattr(server_platform, "_ps_single_quote")
    assert hasattr(server_platform, "_build_powershell_lnk_script")
    assert callable(server_platform._ps_single_quote)
    assert callable(server_platform._build_powershell_lnk_script)
