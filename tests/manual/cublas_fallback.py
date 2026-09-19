"""Runtime integration test: proves the cublas64_12.dll failure path is handled."""

import sys

DEPRECATION_MESSAGE = (
    "[cublas_fallback.py] DEPRECATED: this script references the deleted "
    "`voice_typer.app` (the predecessor) module. The cuBLAS fallback path is now "
    "covered by tests/test_transcription.py::TestFallbackChain. "
    "Run `pytest tests/test_transcription.py -k FallbackChain` instead."
)


def run() -> int:
    """Print the deprecation notice and return the exit code (2)."""
    print(DEPRECATION_MESSAGE, file=sys.stderr)
    return 2


# Fail fast with a clear message instead of an opaque ImportError.
if __name__ == "__main__":
    sys.exit(run())
