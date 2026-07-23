"""Full diagnostic test: traces the complete F2 -> recording -> transcription path.

This test mocked ONLY the hardware (sounddevice, pystray, pynput) and exercised
every real code path from startup through F2 press to transcription completion.

BUILD-N06 / DOC-045: This script was written against the long-deleted
``voice_typer.app`` The current codebase lives under
``voice_typer.server.app`` with the F2 path tested by:

  - tests/test_e2e_smoke.py
  - tests/test_e2e_regression.py
  - tests/test_transcription.py::TestFallbackChain

This file is kept for historical context but is no longer runnable; the
matching logic was ported to the pytest tests above.

TASK-013: ``run()`` is exposed so ``tests/test_manual_slow.py`` can wrap
this script as a ``@pytest.mark.slow`` test that verifies the deprecation
contract (the script must always exit with code 2 and a clear message
pointing at the modern replacement tests).
"""

import sys

DEPRECATION_MESSAGE = (
    "[diagnose_f2.py] DEPRECATED: this script references the deleted "
    "`voice_typer.app` Electron module. The F2 path is now covered by "
    "tests/test_e2e_smoke.py and tests/test_e2e_regression.py. "
    "Run `pytest tests/test_e2e_smoke.py tests/test_e2e_regression.py` instead."
)


def run() -> int:
    """Print the deprecation notice and return the exit code (2).

    TASK-013: extracted from the ``__main__`` block so the slow test in
    ``tests/test_manual_slow.py`` can call it directly without spawning
    a subprocess.
    """
    print(DEPRECATION_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(run())
