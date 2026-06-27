"""Mutmut configuration for mutation testing.

TEST-010: Specifies which modules to mutate. Don't run mutmut in CI
(it's expensive) — just set up the infrastructure so developers can
run it locally with: mutmut run --config-file=tests/mutmut_config.py
"""

import ast
from pathlib import Path

# Project root (two levels up from this file)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Modules to mutate (relative to PROJECT_ROOT)
MODULES_TO_MUTATE = [
    "voice_typer/server/text_cleanup.py",
    "voice_typer/server/config.py",
    "voice_typer/server/tray.py",
    "voice_typer/server/tray_menu.py",
    "voice_typer/server/tray_icon.py",
    "voice_typer/server/recording.py",
    "voice_typer/server/app.py",
]

# Modules to exclude from mutation
MODULES_TO_EXCLUDE = [
    "tests/",
    "scripts/",
    "archive/",
]

# Timeout for each mutant (seconds)
TIMEOUT = 10.0

# Test command to run for each mutant
TEST_COMMAND = "pytest -x -q tests/test_text_cleanup.py tests/test_config.py tests/test_tray.py tests/test_tray_menu.py"


def pre_mutation(context):
    """Called before each mutation. Return False to skip."""
    # Skip mutations in string literals (too many false positives)
    if context.node.type == ast.Str if hasattr(ast, 'Str') else isinstance(context.node, ast.Constant):
        return False
    return True
