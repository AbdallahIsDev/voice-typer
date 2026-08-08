"""macOS + Windows installer-script LF / byte-parity tests.

The macOS (``install.sh`` / ``uninstall.sh``) and Windows
(``sign-authenticode.ps1`` / ``uninstall.bat`` / ``uninstaller.nsh`` /
``uninstall_permissions.py``) installer scripts run at install /
uninstall time. CRLF would break the POSIX shell scripts outright, and
the Windows maintainer files are byte-pinned to LF by
``.gitattributes`` (``scripts/macos/* text eol=lf`` and
``scripts/windows/* text eol=lf``).

Unlike the Linux scripts (which have a bundled copy in
``src-tauri/resources/linux-scripts/`` to byte-compare against), the
macOS / Windows scripts are referenced by the Tauri config directly —
there is no second on-disk copy. The canonical byte form is therefore
the committed git blob. These tests close the gap that a plain CR scan
leaves open by asserting three invariants per file:

  1. **LF-only**: the working-tree file contains no CR byte (i.e. it is
     byte-identical to its canonical LF-normalized form).
  2. **Blob parity** (committed-state snapshot): the working-tree bytes
     equal the committed git blob (``git show HEAD:<path>``). In CI the
     checkout is clean so this is always true; locally it catches any
     uncommitted drift in a file that ships in the installer. Note that
     with ``eol=lf`` in effect git never converts at checkout, so the
     CRLF regressions are caught by (1) + (3) — this test is about
     working-tree drift from the committed canonical bytes.
  3. **Attribute resolution**: ``git check-attr text eol`` reports
     ``text: set`` + ``eol: lf`` — i.e. the ``.gitattributes`` rule is
     actually in effect for the file, so a deleted / edited rule fails
     here before any CR byte ever appears.

Together these make it impossible for the ``.gitattributes`` rule to
silently regress: removing the rule trips (3); a CRLF working tree
trips (1) and (2); a CRLF blob committed as-is trips (1).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# tests/test_script_resources_lf.py → repo root in 1 parent.
_REPO_ROOT = Path(__file__).resolve().parents[1]

_MACOS_DIR = _REPO_ROOT / "scripts" / "macos"
_WINDOWS_DIR = _REPO_ROOT / "scripts" / "windows"


def _iter_script_resources() -> list[Path]:
    """All regular files under the macOS / Windows script dirs, skipping __pycache__."""
    files: list[Path] = []
    for directory in (_MACOS_DIR, _WINDOWS_DIR):
        if directory.is_dir():
            files.extend(
                path
                for path in sorted(directory.rglob("*"))
                if path.is_file()
                and "__pycache__" not in path.parts
                and not path.name.startswith(".")
            )
    return files


_SCRIPT_FILES = _iter_script_resources()


def _ids(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _git_blob_bytes(path: Path) -> bytes:
    """Return the committed blob bytes for *path* (``git show HEAD:<rel>``)."""
    rel = path.relative_to(_REPO_ROOT).as_posix()
    result = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "show", f"HEAD:{rel}"],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"`git show HEAD:{rel}` failed (exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace').strip()!r} — the file must "
            "be committed (it ships in the installer bundle) and git must be "
            "available in the test environment."
        )
    return result.stdout


class TestScriptResourceDirs:
    """The macOS / Windows script dirs exist (so the parametrization isn't empty)."""

    def test_macos_and_windows_script_dirs_exist(self):
        for directory in (_MACOS_DIR, _WINDOWS_DIR):
            assert directory.is_dir(), (
                f"{directory} missing — the macOS / Windows installer scripts "
                "must live here so they can be referenced by the Tauri bundle."
            )


class TestScriptResourceLf:
    """The checked-out macOS / Windows scripts must stay LF + match the committed blob."""

    @pytest.mark.parametrize("path", _SCRIPT_FILES, ids=_ids)
    def test_script_is_lf(self, path):
        """No CR byte in the working-tree file (byte-compare vs canonical LF)."""
        data = path.read_bytes()
        assert b"\r" not in data, (
            f"{path.relative_to(_REPO_ROOT)} contains CR bytes (CRLF line "
            "endings). The .gitattributes `text eol=lf` rule was bypassed — "
            "normalize to LF (`dos2unix` or a CRLF-stripping editor) and "
            "commit. On macOS the POSIX shell scripts would break with "
            "`\\r: command not found`."
        )

    @pytest.mark.parametrize("path", _SCRIPT_FILES, ids=_ids)
    def test_script_matches_committed_blob(self, path):
        """Working-tree bytes byte-compare equal to the committed git blob."""
        tree = path.read_bytes()
        blob = _git_blob_bytes(path)
        assert tree == blob, (
            f"{path.relative_to(_REPO_ROOT)} differs from its committed blob "
            "(git show HEAD). Either the file has uncommitted edits, or a "
            "line-ending conversion (CRLF) happened at checkout — both must "
            "be resolved so the shipped script is byte-identical to the "
            "committed LF form."
        )

    @pytest.mark.parametrize("path", _SCRIPT_FILES, ids=_ids)
    def test_gitattributes_enforces_text_eol_lf(self, path):
        """`git check-attr text eol` resolves to `text: set` + `eol: lf`."""
        rel = path.relative_to(_REPO_ROOT).as_posix()
        result = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "check-attr", "text", "eol", "--", rel],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"`git check-attr text eol -- {rel}` failed: {result.stderr}"
        )
        output = result.stdout
        assert "text: set" in output and "eol: lf" in output, (
            f"{rel}: .gitattributes must resolve `text eol=lf` for this file, "
            f"but git reports: {output.strip()!r}. The rule was removed or "
            "edited — restore `scripts/macos/* text eol=lf` / "
            "`scripts/windows/* text eol=lf` in .gitattributes."
        )
