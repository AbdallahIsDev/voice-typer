"""Full diagnostic test: traces the complete F2 -> recording -> transcription path."""

import sys

DEPRECATION_MESSAGE = (
    "[diagnose_f2.py] DEPRECATED: this script references the deleted "
    "`voice_typer.app` predecessor module. The F2 path is now covered by "
    "tests/test_e2e_smoke.py and tests/test_e2e_regression.py. "
    "Run `pytest tests/test_e2e_smoke.py tests/test_e2e_regression.py` instead."
)


def run() -> int:
    """Print the deprecation notice and return the exit code (2)."""
    print(DEPRECATION_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(run())
