"""Regression tests for terminal log formatting.

These checks cover Windows console compatibility and topic color
coverage without requiring a real Windows console.
"""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Mock heavy imports that require a display / hardware so the test can
# import _ColorFormatter from app.py on headless CI.
#
# NOTE: PIL is deliberately NOT mocked here. app.py transitively imports
# tray.py, which imports pystray (mocked below), but tray.py never
# imports PIL at module load time — tray_icon.py uses lazy imports for
# PIL inside its drawing functions. Mocking PIL at module level here
# would permanently pollute ``sys.modules`` and break later tests that
# need real PIL (e.g. tests/test_tray_icon.py with @pytest.mark.real_pil).
for _mod in (
    "sounddevice",
    "pynput",
    "pynput.keyboard",
    "pystray",
    "pyperclip",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from voice_typer.server.log import _TOPIC_COLOR, _ColorFormatter  # noqa: E402

_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}


def _log_format_literals(module_path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    literals: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOG_METHODS or not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            literals.append((first_arg.lineno, first_arg.value))
    return literals


def test_server_log_format_strings_are_cp1252_safe() -> None:
    """Windows cp1252 consoles must not drop server log lines."""
    server_dir = Path(__file__).parents[1] / "voice_typer" / "server"
    offenders: list[str] = []
    for module_path in server_dir.rglob("*.py"):
        for lineno, literal in _log_format_literals(module_path):
            try:
                literal.encode("cp1252")
            except UnicodeEncodeError as exc:
                offenders.append(f"{module_path.relative_to(server_dir)}:{lineno}: {exc}")

    assert offenders == []


def test_audio_log_topics_have_color_entries() -> None:
    """Known audio-path topics should render with explicit topic color."""
    expected = {
        "AUDIO_QUALITY": "38;5;215",
        "VOLUME": "38;5;111",
        "VAD": "38;5;245",
    }

    for topic, color_code in expected.items():
        assert _TOPIC_COLOR.get(topic) == color_code

        record = logging.LogRecord(
            name="voice_typer",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=f"[{topic}] sample",
            args=(),
            exc_info=None,
        )
        formatted = _ColorFormatter().format(record)
        assert f"\033[{color_code}m" in formatted
