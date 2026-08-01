"""Regression test for CR-41: log.error(..., exc) must be replaced with log.exception(...) to capture tracebacks."""

from pathlib import Path

import pytest

# Source files to check (excluding recorder.py which is owned by )
SOURCE_FILES = [
    "voice_typer/server/parakeet_engine.py",
    "voice_typer/server/qwen_engine.py",
    "voice_typer/server/asr_registry.py",
    "voice_typer/server/transcription.py",
    "voice_typer/server/vocabulary.py",
    "voice_typer/server/templates.py",
    "voice_typer/server/onboarding.py",
    "voice_typer/server/task_scheduler.py",
    "voice_typer/server/autostart_launcher.py",
    "voice_typer/server/electron_launcher.py",
]


@pytest.mark.parametrize("rel_path", SOURCE_FILES)
def test_no_log_error_with_exc_arg(rel_path):
    """log.error(..., exc) pattern must be replaced with log.exception(...) to capture tracebacks."""
    repo_root = Path(__file__).parent.parent
    src = (repo_root / rel_path).read_text(encoding="utf-8")
    # The buggy pattern: log.error("...: %s", exc) or log.error("...", ..., exc)
    # We can't easily parse Python; use a heuristic grep.
    # Look for `log.error("` lines that end with `, exc)` (with optional whitespace).
    import re

    buggy_pattern = re.compile(r"log\.error\([^)]*,\s*exc(?:_info=\w+)?\)\s*$", re.MULTILINE)
    matches = buggy_pattern.findall(src)
    assert not matches, f"{rel_path} still has log.error(..., exc) pattern (should be log.exception(...)): {matches}"
