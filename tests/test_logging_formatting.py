"""Regression tests for terminal log formatting.

These checks cover Windows console compatibility and topic color
coverage without requiring a real Windows console.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from voice_typer.server.app import _ColorFormatter


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
        assert _ColorFormatter._TOPIC_COLOR.get(topic) == color_code

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
