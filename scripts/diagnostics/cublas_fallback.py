"""Runtime integration test: proves the cublas64_12.dll failure path is handled.

Simulates the exact scenario the user reported:
1. Model loaded on GPU (CUDA)
2. Recording starts (F2)
3. Recording stops (F2)
4. Transcription fails with "cublas64_12.dll is not found or cannot be loaded"
5. App must NOT stay stuck in busy=True / Transcribing...
6. Pressing F2 again must work

This test does NOT need a display or real GPU — it mocks the model but
exercises the real code paths.

BUILD-N06 / DOC-045: This script was written against the long-deleted
``voice_typer.app`` (Flet) module. The current codebase lives under
``voice_typer.server.app``. Running it as-is will fail at import time
with ``ModuleNotFoundError: No module named 'voice_typer.app'``.

The transcription fallback path it documents is now covered by the
pytest suite in ``tests/test_transcription.py::TestFallbackChain`` and
``tests/test_round9_e2e.py``. This file is kept for historical context
but is no longer runnable; the matching logic was ported to the
pytest tests above.
"""

import sys

# Fail fast with a clear message instead of an opaque ImportError.
if __name__ == "__main__":
    print(
        "[cublas_fallback.py] DEPRECATED: this script references the deleted "
        "`voice_typer.app` (Flet) module. The cuBLAS fallback path is now "
        "covered by tests/test_transcription.py::TestFallbackChain. "
        "Run `pytest tests/test_transcription.py -k FallbackChain` instead.",
        file=sys.stderr,
    )
    sys.exit(2)
