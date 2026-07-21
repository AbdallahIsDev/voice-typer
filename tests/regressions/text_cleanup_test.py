"""CR-069: split from tests/test_feature_hardening_regressions.py (L1195-1221).

Source marker: ``tests/test_new_ts_004_006_012_015.py``.

This section of the original monolith contained only the module
docstring for the NEW-TS-004 / NEW-TS-006 / NEW-TS-012 / NEW-TS-015
regression cluster — the actual static-source checks for these
TypeScript-side fixes live in their own dedicated test modules
(``tests/test_electron_ipc_and_build.py`` and friends).  The
``if __name__ == "__main__"`` block is preserved verbatim so that
the file remains runnable as a standalone pytest invocation (per
the original file's pattern).
"""

# === Source: tests/test_new_ts_004_006_012_015.py ===

"""Regression tests for NEW-TS-004, NEW-TS-006, NEW-TS-012, NEW-TS-015.

These are TypeScript-side fixes verified via static source inspection
(the renderer doesn't have a JS test runner wired up for component
tests; we verify the source structure instead).

NEW-TS-004: Settings.tsx and Microphone.tsx re-implemented the snackbar
pattern inline instead of using the shared useSnackbar hook.

NEW-TS-006: Home.tsx registered two separate usePythonEvent listeners
for 'transcription_final' — consolidated into one.

NEW-TS-012: App.tsx had an ``as RecordingState`` cast that was never
removed despite a comment claiming it was.  Replaced with a runtime
validator.

NEW-TS-015: usePython().isReady was always true (the preload installs
window.python before React mounts), making every ``if (!isReady)
return`` guard dead code.  Removed the misleading flag.
"""


import pytest

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
