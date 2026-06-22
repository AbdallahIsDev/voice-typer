"""Round 17 regression tests for ERR-ERR-004 and ERR-ERR-006.

ERR-ERR-004: 12 unnecessary `bool()` wrappers + `# pyrefly: ignore` comments
              in settings.py / streaming.py / recording_controller.py.
              The signatures already typed the args as `bool`, so `bool()`
              was a no-op; we removed both the wrapper and the suppression.

ERR-ERR-006: pyproject.toml blanket-disabled E501 and missing-import checks
              project-wide. We removed the blanket ignores, set an explicit
              line-length, and listed the genuinely un-stubbed modules.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = REPO_ROOT / "voice_typer" / "server"


# ---------------------------------------------------------------------------
# ERR-ERR-004: no `# pyrefly: ignore [unnecessary-type-conversion]` left
# ---------------------------------------------------------------------------


def _scan_ignores(path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    if not path.exists():
        return hits
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "pyrefly: ignore" in line and "unnecessary-type-conversion" in line:
            hits.append((i, line.strip()))
    return hits


@pytest.mark.parametrize(
    "filename",
    [
        "settings.py",
        "streaming.py",
        "recording_controller.py",
    ],
)
def test_no_unnecessary_type_conversion_ignores(filename: str) -> None:
    """ERR-ERR-004: every location that suppressed unnecessary-type-conversion
    must be replaced with proper type narrowing, not a suppression comment."""
    hits = _scan_ignores(SERVER_DIR / filename)
    assert hits == [], f"unexpected unnecessary-type-conversion ignores in {filename}: {hits}"


def test_settings_apply_uses_typed_bools_directly() -> None:
    """ERR-ERR-004: SettingsController.apply must assign the bool args directly
    without wrapping them in bool(). The function signature already types them
    as bool, so bool() is a no-op that previously required a suppression."""
    src = (SERVER_DIR / "settings.py").read_text(encoding="utf-8")
    # Find the apply() method body and ensure no `bool(autostart)` or
    # `bool(show_notifications)` calls remain.
    apply_match = re.search(
        r"def apply\([^)]*\)[^:]*:(.*?)(?=\n    def |\nclass )",
        src,
        re.DOTALL,
    )
    assert apply_match is not None, "SettingsController.apply not found"
    body = apply_match.group(1)
    assert "bool(autostart)" not in body, "apply() still wraps autostart in bool()"
    assert "bool(show_notifications)" not in body, (
        "apply() still wraps show_notifications in bool()"
    )


def test_recording_controller_streaming_enabled_no_bool_wrap() -> None:
    """ERR-ERR-004: _streaming_enabled must return the config flag directly;
    the config field is typed `bool`, so bool() was a no-op."""
    src = (SERVER_DIR / "recording_controller.py").read_text(encoding="utf-8")
    m = re.search(r"def _streaming_enabled\(self\)[^:]*:(.*?)(?=\n    def |\nclass )", src, re.DOTALL)
    assert m is not None
    body = m.group(1)
    assert "bool(self._app.config.streaming_transcription)" not in body


# ---------------------------------------------------------------------------
# ERR-ERR-006: pyproject config does not blanket-disable E501 / missing imports
# ---------------------------------------------------------------------------


def _read_pyproject() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_ruff_does_not_blanket_ignore_e501() -> None:
    """ERR-ERR-006: ruff must not blanket-disable E501. The line-length is set
    explicitly above; lines that exceed it must be wrapped, not ignored."""
    src = _read_pyproject()
    # The ignore list may be empty `[]` or contain rules other than E501.
    ignore_match = re.search(
        r"\[tool\.ruff\.lint\]\s*\n(.*?)(?=\n\[|\Z)",
        src,
        re.DOTALL,
    )
    assert ignore_match is not None, "[tool.ruff.lint] section not found"
    ignore_body = ignore_match.group(1)
    assert '"E501"' not in ignore_body, (
        "E501 is blanket-disabled in ruff config — wrap offending lines instead"
    )


def test_ruff_line_length_is_set_explicitly() -> None:
    """ERR-ERR-006: an explicit line-length must be set (not the default 88)."""
    src = _read_pyproject()
    ll_match = re.search(r"^\s*line-length\s*=\s*(\d+)", src, re.MULTILINE)
    assert ll_match is not None, "no line-length set in [tool.ruff]"
    assert 100 <= int(ll_match.group(1)) <= 120, (
        f"line-length {ll_match.group(1)} is outside the documented 100-120 range"
    )


def test_mypy_does_not_blanket_ignore_missing_imports() -> None:
    """ERR-ERR-006: mypy must not blanket-ignore missing imports project-wide.
    Per-module overrides must be used for the few libraries without stubs."""
    src = _read_pyproject()
    # The top-level [tool.mypy] section must NOT contain
    # `ignore_missing_imports = true`. (false is fine; absence is fine.)
    mypy_section = re.search(r"\[tool\.mypy\][^\[]*", src, re.DOTALL)
    assert mypy_section is not None, "[tool.mypy] section not found"
    top = mypy_section.group(0)
    # Only check the top-level (not the [[tool.mypy.overrides]] block).
    top_only = top.split("[[tool.mypy.overrides]]")[0]
    assert "ignore_missing_imports = true" not in top_only, (
        "mypy blanket-ignores missing imports — use per-module overrides"
    )


def test_mypy_has_per_module_overrides() -> None:
    """ERR-ERR-006: per-module overrides must exist for known un-stubbed libs."""
    src = _read_pyproject()
    assert "[[tool.mypy.overrides]]" in src, "no mypy overrides block found"
    # Each of these libraries is known to lack py.typed marker as of 2026.
    for module in ["sounddevice", "faster_whisper", "pystray", "pynput"]:
        assert module in src, f"{module} not in mypy overrides list"


# ---------------------------------------------------------------------------
# Smoke: ruff actually passes on the modified server tree
# ---------------------------------------------------------------------------


def test_ruff_e501_passes_on_server_tree() -> None:
    """ERR-ERR-006: after removing the E501 blanket-ignore, ruff must pass on
    the server tree with no E501 errors."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "E501", str(SERVER_DIR)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"ruff E501 still fails on server tree:\n{result.stdout}\n{result.stderr}"
    )
