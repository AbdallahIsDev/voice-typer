"""Security regression test for ``scripts/tauri-sign.cmd``.

Domain: the Tauri Windows bundler's ``bundle.windows.signCommand``
wrapper must NOT pass the ``WIN_SIGN_COMMAND`` env var through unquoted
to ``cmd.exe``. The previous implementation did:

    if "%WIN_SIGN_COMMAND%"=="" exit /b 0
    %WIN_SIGN_COMMAND% "%1"
    exit /b %errorlevel%

which is a classic cmd.exe injection vector. If a future CI step or a
compromised dependency set ``WIN_SIGN_COMMAND`` to e.g.
``signtool sign /f cert.pfx /p pass & evil.exe``, the ``&`` would cause
``evil.exe`` to run as a separate command during ``cargo tauri build`` —
arbitrary code execution on the signing machine.

The rewrite ( fix) eliminates the env-var pass-through entirely
and invokes ``signtool`` DIRECTLY with only ``WIN_CSC_LINK`` (PFX path)
and ``WIN_CSC_KEY_PASSWORD`` (PFX password), both quoted:

    signtool sign /f "%WIN_CSC_LINK%" /p "%WIN_CSC_KEY_PASSWORD%" ^
        /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "%1"

These tests assert:
  1. The ``WIN_SIGN_COMMAND`` env var is NO LONGER READ by the script
     (the literal string ``WIN_SIGN_COMMAND`` must not appear outside
     of comments / historical context).
  2. The script invokes ``signtool`` DIRECTLY (the literal token
     ``signtool sign`` must appear on a non-comment, non-REM line).
  3. Both ``WIN_CSC_LINK`` and ``WIN_CSC_KEY_PASSWORD`` are referenced.
  4. The script is a no-op (``exit /b 0``) when either env var is
     unset — preserves the local-build-without-signing-material path.
  5. The ``/fd SHA256`` + ``/tr http://timestamp.digicert.com`` +
     ``/td SHA256`` flags are present (RFC-3161 timestamping, per
     ADR-0020 §13.1 + docs/migration/signing-guide.md).

The Linux sandbox CANNOT run a real Windows ``cmd.exe`` (no Wine
dependency). These tests therefore operate on the SCRIPT TEXT
(string-level assertions), not on a real execution. A separate
VALIDATE-ON-WINDOWS step (see the docstring of
``scripts/tauri-sign.cmd``) is required to confirm signtool actually
runs end-to-end on a Windows host with a real PFX.

VALIDATE ON WINDOWS HOST:
    1. set WIN_CSC_LINK=path\\to\\voice-typer.pfx
    2. set WIN_CSC_KEY_PASSWORD=<password>
    3. cd src-tauri
    4. cargo tauri build --target x86_64-pc-windows-msvc
    5. signtool verify /pa /v target\\...\\release\\bundle\\nsis\\*-setup.exe
    Expected: "Successfully verified"
    6. unset WIN_CSC_LINK, rerun cargo tauri build — the wrapper
       must exit 0 (no-op) so local builds without signing material
       still succeed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The .cmd file is at <repo-root>/scripts/tauri-sign.cmd.
# This test file is at <repo-root>/tests/test_tauri_sign_cmd_safe.py.
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "tauri-sign.cmd"


@pytest.fixture(scope="module")
def cmd_text() -> str:
    """Read the .cmd file as text (LF or CRLF agnostic)."""
    assert _SCRIPT_PATH.is_file(), f"tauri-sign.cmd not found at {_SCRIPT_PATH}"
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def _non_comment_lines(text: str) -> list[str]:
    """Return only the lines that are NOT cmd.exe comments (REM or ::)."""
    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith("REM") or stripped.startswith("rem") or stripped.startswith("::"):
            continue
        out.append(raw)
    return out


class TestTauriSignCmdSafe:
    """``scripts/tauri-sign.cmd`` must NOT pass env vars through
    unquoted. The script must invoke ``signtool`` directly with only
    ``WIN_CSC_LINK`` + ``WIN_CSC_KEY_PASSWORD`` (both quoted).
    """

    def test_file_exists(self, cmd_text: str) -> None:
        """Sanity check — the script file is present and non-empty."""
        assert cmd_text.strip(), "tauri-sign.cmd is empty"

    def test_no_unquoted_win_sign_command_passthrough(self, cmd_text: str) -> None:
        """The literal ``%WIN_SIGN_COMMAND%`` token must NOT appear on any
        non-comment line. The previous implementation had
        ``%WIN_SIGN_COMMAND% "%1"`` on line 8 — a cmd.exe injection
        vector. The rewrite removes the env-var pass-through entirely.

        We allow the literal string ``WIN_SIGN_COMMAND`` to appear inside
        REM comments (historical context explaining the  fix), but
        the EXECUTABLE token ``%WIN_SIGN_COMMAND%`` (with surrounding
        percent signs) must NEVER be expanded by the script — i.e. it
        must not appear on a non-comment line.
        """
        non_comment = _non_comment_lines(cmd_text)
        offenders = [line for line in non_comment if "%WIN_SIGN_COMMAND%" in line]
        assert not offenders, (
            "tauri-sign.cmd still references %WIN_SIGN_COMMAND% on non-comment "
            f"line(s): {offenders}.  requires the env-var pass-through "
            "to be REMOVED — the script must invoke signtool directly with "
            "WIN_CSC_LINK + WIN_CSC_KEY_PASSWORD."
        )

    def test_invokes_signtool_directly(self, cmd_text: str) -> None:
        """The script must invoke ``signtool sign`` directly (not via an
        env-var pass-through). The literal token ``signtool sign`` must
        appear on a non-comment, non-REM line.
        """
        non_comment = _non_comment_lines(cmd_text)
        has_signtool = any("signtool sign" in line for line in non_comment)
        assert has_signtool, (
            "tauri-sign.cmd must invoke `signtool sign` directly on a "
            "non-comment line ( fix — replaces the env-var pass-through)."
        )

    def test_uses_win_csc_link(self, cmd_text: str) -> None:
        """The script must reference ``WIN_CSC_LINK`` (the Authenticode
        PFX cert path env var). Per ADR-0020 §13.1 +
        docs/migration/signing-guide.md, this is the canonical env var
        for the PFX path.
        """
        assert "WIN_CSC_LINK" in cmd_text, (
            "tauri-sign.cmd must reference WIN_CSC_LINK (the PFX cert path "
            "env var, per ADR-0020 §13.1 + docs/migration/signing-guide.md)."
        )

    def test_uses_win_csc_key_password(self, cmd_text: str) -> None:
        """The script must reference ``WIN_CSC_KEY_PASSWORD`` (the PFX
        password env var).
        """
        assert "WIN_CSC_KEY_PASSWORD" in cmd_text, (
            "tauri-sign.cmd must reference WIN_CSC_KEY_PASSWORD (the PFX "
            "password env var, per ADR-0020 §13.1)."
        )

    def test_noop_when_env_vars_unset(self, cmd_text: str) -> None:
        """The script MUST ``exit /b 0`` when either env var is unset —
        preserves the local-build-without-signing-material path. We
        assert that both ``WIN_CSC_LINK`` and ``WIN_CSC_KEY_PASSWORD``
        have an associated ``exit /b 0`` guard BEFORE the signtool
        invocation.

        Specifically, the script must contain (in order):
          1. ``if "%WIN_CSC_LINK%"=="" exit /b 0``
          2. ``if "%WIN_CSC_KEY_PASSWORD%"=="" exit /b 0``
          3. ``signtool sign ...``

        We check the two guards exist as non-comment lines; ordering is
        verified by checking they appear before the first ``signtool sign``
        line.
        """
        non_comment = _non_comment_lines(cmd_text)
        # Find the first signtool invocation line index.
        signtool_idx = next(
            (i for i, line in enumerate(non_comment) if "signtool sign" in line),
            None,
        )
        assert signtool_idx is not None, "No `signtool sign` line found (checked by other tests)."
        # All guard lines must appear BEFORE the signtool invocation.
        prefix = non_comment[:signtool_idx]
        guard_link = any(
            re.search(r'if\s+"%WIN_CSC_LINK%"\s*==\s*""\s+exit\s+/b\s+0', line, re.IGNORECASE)
            for line in prefix
        )
        guard_pass = any(
            re.search(r'if\s+"%WIN_CSC_KEY_PASSWORD%"\s*==\s*""\s+exit\s+/b\s+0', line, re.IGNORECASE)
            for line in prefix
        )
        assert guard_link, (
            "tauri-sign.cmd must guard `if \"%WIN_CSC_LINK%\"==\"\" exit /b 0` "
            "BEFORE the signtool invocation — preserves no-op behavior on "
            "local builds without signing material."
        )
        assert guard_pass, (
            "tauri-sign.cmd must guard `if \"%WIN_CSC_KEY_PASSWORD%\"==\"\" "
            "exit /b 0` BEFORE the signtool invocation — preserves no-op "
            "behavior on local builds without signing material."
        )

    def test_uses_sha256_file_digest(self, cmd_text: str) -> None:
        """The signtool invocation must include ``/fd SHA256`` (the file
        digest algorithm). Per ADR-0020 §13.1 + signing-guide.md.
        """
        non_comment = _non_comment_lines(cmd_text)
        has_fd = any("/fd SHA256" in line or "/fd" in line and "SHA256" in line for line in non_comment)
        assert has_fd, (
            "tauri-sign.cmd must include `/fd SHA256` in the signtool "
            "invocation (file digest algorithm, per ADR-0020 §13.1)."
        )

    def test_uses_rfc_3161_timestamp(self, cmd_text: str) -> None:
        """The signtool invocation must include an RFC-3161 timestamp
        server (``/tr http://timestamp.digicert.com``) AND ``/td SHA256``
        (timestamp digest). Per ADR-0020 §13.1 + signing-guide.md.
        """
        non_comment = _non_comment_lines(cmd_text)
        joined = "\n".join(non_comment)
        assert "http://timestamp.digicert.com" in joined, (
            "tauri-sign.cmd must include the DigiCert RFC-3161 timestamp "
            "server URL (per ADR-0020 §13.1 + signing-guide.md)."
        )
        assert "/tr" in joined, "tauri-sign.cmd must include the `/tr` flag (RFC-3161 timestamp server)."
        assert "/td SHA256" in joined, "tauri-sign.cmd must include `/td SHA256` (timestamp digest algorithm)."

    def test_first_arg_quoted(self, cmd_text: str) -> None:
        """The ``WIN_CSC_LINK`` value must be quoted in the signtool
        invocation (``/f "%WIN_CSC_LINK%"``) — paths with spaces would
        otherwise break the signtool call.
        """
        non_comment = _non_comment_lines(cmd_text)
        # Look for the signtool invocation line(s).
        signtool_lines = [line for line in non_comment if "signtool sign" in line]
        assert signtool_lines, "No `signtool sign` line found."
        # The /f flag must be followed by "%WIN_CSC_LINK%" (quoted).
        joined = "\n".join(signtool_lines)
        assert '/f "%WIN_CSC_LINK%"' in joined, (
            "tauri-sign.cmd must quote the WIN_CSC_LINK value: "
            "`/f \"%WIN_CSC_LINK%\"`. Unquoted paths with spaces would "
            "break the signtool call."
        )
        assert '/p "%WIN_CSC_KEY_PASSWORD%"' in joined, (
            "tauri-sign.cmd must quote the WIN_CSC_KEY_PASSWORD value: "
            "`/p \"%WIN_CSC_KEY_PASSWORD%\"`."
        )

    def test_no_eval_call_or_call_command(self, cmd_text: str) -> None:
        """Defensive: the script must NOT use ``call``, ``eval``,
        ``for /f``, or other cmd.exe constructs that could re-evaluate
        a string as a command. The  fix is to invoke signtool
        DIRECTLY — no indirection.
        """
        non_comment = _non_comment_lines(cmd_text)
        # ``call`` is allowed ONLY as a method call inside the .cmd
        # itself (none expected here). We ban the standalone ``call``
        # statement that would re-evaluate its argument.
        for line in non_comment:
            stripped = line.lstrip()
            if re.match(r"call\s", stripped, re.IGNORECASE):
                pytest.fail(f"tauri-sign.cmd uses `call` on line: {line!r} —  forbids indirection.")
            if stripped.startswith("for /f") or stripped.startswith("for /F"):
                pytest.fail(f"tauri-sign.cmd uses `for /f` on line: {line!r} —  forbids indirection.")
