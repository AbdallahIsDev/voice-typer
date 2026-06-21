"""Full diagnostic test: traces the complete F2 -> recording -> transcription path.

This test mocked ONLY the hardware (sounddevice, pystray, pynput) and exercised
every real code path from startup through F2 press to transcription completion.

BUILD-N06 / DOC-045: This script was written against the long-deleted
``voice_typer.app`` (Flet) module. The current codebase lives under
``voice_typer.server.app`` with the F2 path tested by:

  - tests/test_round8_e2e.py
  - tests/test_round9_e2e.py
  - tests/test_round10_bugfixes.py

This file is kept for historical context but is no longer runnable; the
matching logic was ported to the pytest tests above.
"""

import sys

if __name__ == "__main__":
    print(
        "[diagnose_f2.py] DEPRECATED: this script references the deleted "
        "`voice_typer.app` (Flet) module. The F2 path is now covered by "
        "tests/test_round8_e2e.py and tests/test_round9_e2e.py. "
        "Run `pytest tests/test_round8_e2e.py tests/test_round9_e2e.py` instead.",
        file=sys.stderr,
    )
    sys.exit(2)
