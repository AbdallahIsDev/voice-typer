"""pytest configuration for the Tauri sidecar tests.

XS-41: the previous ``pytest_collection_modifyitems`` hook that
explicitly added ``pytest.mark.asyncio`` to every async test was
removed — ``pyproject.toml`` sets ``asyncio_mode = "auto"``
project-wide, which already auto-marks all async tests. The hook was
dead code that predated the project-wide setting and the
``co_flags & 0x100`` (CO_COROUTINE) heuristic was fragile.

What remains is the platform-skip logic: Tauri migration-test
directories (``mig15`` / ``mig16`` / ``mig17``) are platform-specific
and skipped automatically when run on the wrong OS — UNLESS the test
file name does NOT carry a platform token, in which case it is a
cross-platform structural / source-grep test that runs everywhere.

WR-6: the previous directory-level mapping for mig15/mig16 caused
~16,300 LOC of structural source-grep tests (e.g. "tauri.conf.json
declares externalBin: ['bin/python-sidecar']") to be skipped on Linux
CI — the only CI sandbox — even though the mig15/mig16 test file
docstrings explicitly state "These tests run in the Linux sandbox" and
"These tests run on any platform (Linux sandbox included)". This was
the EXACT same over-skip defect that the conftest author already
diagnosed and fixed for mig18 (see comment below); the fix is now
extended to mig15/mig16 by routing them through the same file-name-
based detection path.
"""

import os
import sys

import pytest

# WR-6: mig15 / mig16 / mig17 / mig18 ALL now use file-name-based
# platform detection. The previous directory-level mapping for
# mig15/mig16/mig17 caused structural source-grep tests (which read
# `tauri.conf.json` and `*.rs` files as text and assert on their
# contents) to be skipped on the Linux CI sandbox — even though those
# tests' own docstrings explicitly state "These tests run in the Linux
# sandbox". The 9-point ADR-0020 behavioral gate still requires real
# Windows/macOS/Linux host execution (see review.md
# HP-1/HP-2/HP-3), but the structural source-grep scaffolding runs on
# every platform so regressions in tauri.conf.json / spawn.rs / build
# scripts are caught in CI, not just on real-host validation.
#
# Tests that DO require a specific platform (e.g. test_ime_composition_
# check_returns_false_on_non_windows) are still correctly skipped on
# the wrong OS because their file name carries the platform token
# (windows / macos / linux).
_PLATFORM_FOR_FILE_TOKEN: list[tuple[str, str]] = [
    ("linux", "linux"),
    ("macos", "darwin"),
    ("windows", "win32"),
]

# Directories that use file-name-based platform detection. WR-6: now
# includes mig15/mig16/mig17 in addition to mig18 — all four Tauri
# migration-test directories contain a mix of platform-specific tests
# (whose file name carries the platform token) and cross-platform
# structural source-grep tests (whose file name does NOT carry a token
# and which run on every platform).
_MIXED_PLATFORM_PREFIXES: frozenset[str] = frozenset(
    {
        "mig15",
        "mig16",
        "mig17",
        "mig18",
    }
)


def _required_platform(module_path: str) -> str | None:
    # Match on the containing directory name (e.g. ``mig17``), since the
    # test files themselves are named ``test_*.py``.
    directory = os.path.basename(os.path.dirname(module_path))
    # All Tauri migration-test directories use file-name-based detection
    # so e.g. ``test_linux_signing.py`` is treated as Linux-specific
    # while ``test_openmp_runtimes.py`` (no platform token) runs on
    # every platform. This catches structural source-grep regressions
    # on Linux CI without forcing every test to wait for real-host
    # validation.
    if any(directory.startswith(prefix) for prefix in _MIXED_PLATFORM_PREFIXES):
        filename = os.path.basename(module_path).lower()
        for token, plat in _PLATFORM_FOR_FILE_TOKEN:
            if token in filename:
                return plat
        return None  # no platform token → runs on all platforms
    return None


def pytest_collection_modifyitems(config, items):
    """Skip platform-specific Tauri migration tests on the wrong OS.

    XS-41: the previous asyncio-marker logic was removed because
    ``asyncio_mode = "auto"`` in ``pyproject.toml`` already handles
    auto-marking. Only the platform-skip behavior remains.

    WR-6: file-name-based detection extended to mig15/mig16/mig17
    (previously only mig18 used it). Structural source-grep tests
    (file name without a platform token) now run on every platform.
    """
    for item in items:
        required = _required_platform(str(item.module.__file__))
        if required is not None and sys.platform != required:
            item.add_marker(
                pytest.mark.skip(reason=f"platform-specific Tauri test (target={required}); running on {sys.platform}")
            )
