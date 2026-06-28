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

# Test command to run for each mutant.
# TEST-010: must cover ALL modules in MODULES_TO_MUTATE. Pre-fix this
# only ran 4 test files (test_text_cleanup, test_config, test_tray,
# test_tray_menu) but MODULES_TO_MUTATE has 7 modules — mutants in
# tray_icon.py, recording.py, and app.py would survive undetected,
# giving a misleading mutation score. Now all 7 modules have at
# least one corresponding test file in the command.
TEST_COMMAND = (
    "pytest -x -q "
    "tests/test_text_cleanup.py "
    "tests/test_config.py "
    "tests/test_tray.py "
    "tests/test_tray_menu.py "
    "tests/test_tray_icon.py "
    "tests/test_recording.py "
    "tests/test_app.py"
)


def pre_mutation(context):
    """Called before each mutation. Return False to skip."""
    # Skip mutations in string literals (too many false positives)
    if context.node.type == ast.Str if hasattr(ast, 'Str') else isinstance(context.node, ast.Constant):
        return False
    return True
