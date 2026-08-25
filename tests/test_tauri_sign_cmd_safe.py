"""Security contracts for Tauri's Windows Authenticode signing path.

``tauri-sign.cmd`` is intentionally a minimal adapter: Tauri invokes it
through ``bundle.windows.signCommand`` with the binary path as ``%1``. The
adapter must never expand an arbitrary signing command. It delegates to one
PowerShell helper so Tauri, inner binaries, and release installers all use the
same validated arguments, branding lookup, timestamp retry policy, and
signature verification.

The Linux sandbox cannot invoke ``cmd.exe`` or ``signtool``. These tests pin
the security-critical static contract; the Windows Tauri workflow is the
platform-specific execution check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CMD_PATH = PROJECT_ROOT / "scripts" / "tauri-sign.cmd"
HELPER_PATH = PROJECT_ROOT / "scripts" / "windows" / "sign-authenticode.ps1"


@pytest.fixture(scope="module")
def cmd_text() -> str:
    assert CMD_PATH.is_file(), f"tauri-sign.cmd not found at {CMD_PATH}"
    return CMD_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def helper_text() -> str:
    return HELPER_PATH.read_text(encoding="utf-8") if HELPER_PATH.is_file() else ""


def _non_comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().lower().startswith(("rem", "::"))]


class TestTauriSignCmdSafe:
    def test_wrapper_is_present_and_nonempty(self, cmd_text: str) -> None:
        assert cmd_text.strip(), "tauri-sign.cmd is empty"

    def test_wrapper_rejects_dynamic_sign_command(self, cmd_text: str) -> None:
        offenders = [line for line in _non_comment_lines(cmd_text) if "%WIN_SIGN_COMMAND%" in line]
        assert not offenders, f"unsafe WIN_SIGN_COMMAND expansion: {offenders}"

    def test_wrapper_is_a_guarded_helper_adapter(self, cmd_text: str) -> None:
        executable = "\n".join(_non_comment_lines(cmd_text))
        assert re.search(
            r'if\s+"%WIN_CSC_LINK%"\s*==\s*""\s+exit\s+/b\s+0',
            executable,
            re.IGNORECASE,
        )
        assert re.search(
            r'if\s+"%WIN_CSC_KEY_PASSWORD%"\s*==\s*""\s+exit\s+/b\s+0',
            executable,
            re.IGNORECASE,
        )
        assert "windows\\sign-authenticode.ps1" in executable
        assert "powershell.exe" in executable.lower()
        assert "signtool sign" not in executable.lower()


class TestAuthenticodeSigningHelper:
    def test_helper_is_present(self, helper_text: str) -> None:
        assert HELPER_PATH.is_file() and helper_text.strip(), f"Windows signing helper not found at {HELPER_PATH}"

    def test_helper_uses_branding_source_of_truth(self, helper_text: str) -> None:
        assert "voice_typer/server/branding.py" in helper_text
        assert '"Voice Typer"' not in helper_text

    def test_helper_uses_fixed_signtool_arguments(self, helper_text: str) -> None:
        assert "signtool.exe" in helper_text
        for flag in ("/fd", "SHA256", "/tr", "/td", "/d", "/du"):
            assert flag in helper_text
        assert "WIN_CSC_KEY_PASSWORD" in helper_text
        assert "Invoke-Expression" not in helper_text

    def test_helper_retries_timestamp_servers_and_verifies(self, helper_text: str) -> None:
        for server in (
            "http://timestamp.digicert.com",
            "http://timestamp.sectigo.com",
            "http://timestamp.globalsign.com/scripts/timestamp.dll",
            "http://ts.ssl.com",
        ):
            assert server in helper_text
        assert re.search(r"\$maxAttempts\s*=\s*3", helper_text)
        assert "Start-Sleep -Seconds 30" in helper_text
        assert "verify" in helper_text

    def test_helper_removes_materialized_certificate(self, helper_text: str) -> None:
        assert "FromBase64String" in helper_text
        assert "finally" in helper_text
        assert "Remove-Item" in helper_text
