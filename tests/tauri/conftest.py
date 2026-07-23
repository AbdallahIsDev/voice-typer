"""pytest configuration for the Tauri sidecar tests.

These tests use pytest-asyncio in auto mode so async tests don't need
explicit @pytest.mark.asyncio markers. This is local to the tauri/
test directory so it doesn't affect the project-wide pytest config.
"""

import os
import sys

import pytest

# Map Tauri migration-test directory prefixes to the platform they verify.
# mig15 = Windows, mig16 = macOS, mig17 = Linux. These directories contain
# ONLY platform-specific tests (every file's name carries the platform
# token, e.g. ``test_toast_linux.py``), so a directory-level mapping is
# sufficient and they are skipped automatically when run on the wrong OS.
#
# mig18 is intentionally ABSENT from this map: it is a MIXED-platform
# directory (``test_linux_signing.py``, ``test_macos_signing.py``,
# ``test_windows_signing.py``, plus cross-platform build-script structure
# tests like ``test_openmp_runtimes.py``). The mig18 tests all validate
# STATIC files (``tauri.conf.json``, CI workflow YAML, shell scripts,
# build orchestrators) and explicitly state in their module docstrings
# that they "run on any platform (Linux sandbox included)". A directory-
# level mapping of ``mig18 -> darwin`` caused every mig18 test — including
# the Linux signing tests — to be skipped on Linux CI, hiding regressions
# that the tests were designed to catch. mig18 now uses file-name-based
# detection via :data:`_PLATFORM_FOR_FILE_TOKEN` below.
_PLATFORM_FOR_PREFIX: dict[str, str] = {
    "mig15": "win32",
    "mig16": "darwin",
    "mig17": "linux",
}

# File-name-token → platform mapping for mixed-platform migration
# directories (currently only mig18). A test file whose name contains the
# token (case-insensitive) is treated as platform-specific and skipped on
# other platforms. Files without any token run on every platform (they
# validate cross-platform build-script structure and read only static
# files that exist in the repo on every OS).
_PLATFORM_FOR_FILE_TOKEN: list[tuple[str, str]] = [
    ("linux", "linux"),
    ("macos", "darwin"),
    ("windows", "win32"),
]

# Directories that use file-name-based platform detection instead of the
# coarse directory-level mapping (because they contain a mix of
# platform-specific and cross-platform tests).
_MIXED_PLATFORM_PREFIXES: frozenset[str] = frozenset({"mig18"})


def _required_platform(module_path: str) -> str | None:
    # Match on the containing directory name (e.g. ``mig17``), since the
    # test files themselves are named ``test_*.py``.
    directory = os.path.basename(os.path.dirname(module_path))
    # Mixed-platform directories (mig18): use file-name-based detection
    # so e.g. ``test_linux_signing.py`` is treated as Linux-specific
    # while ``test_openmp_runtimes.py`` runs on every platform.
    if any(directory.startswith(prefix) for prefix in _MIXED_PLATFORM_PREFIXES):
        filename = os.path.basename(module_path).lower()
        for token, plat in _PLATFORM_FOR_FILE_TOKEN:
            if token in filename:
                return plat
        return None  # no platform token → runs on all platforms
    for prefix, plat in _PLATFORM_FOR_PREFIX.items():
        if directory.startswith(prefix):
            return plat
    return None


# Auto mode: every `async def test_*` is treated as an asyncio test
# without needing @pytest.mark.asyncio. This matches the pattern in
# the project's existing async tests (e.g. test_cloud_engines.py).
def pytest_collection_modifyitems(config, items):
    for item in items:
        if hasattr(item, "function") and getattr(item.function, "__code__", None):
            if item.function.__code__.co_flags & 0x100:  # CO_COROUTINE
                item.add_marker(pytest.mark.asyncio)
        # Skip platform-specific migration tests on the wrong OS.
        required = _required_platform(str(item.module.__file__))
        if required is not None and sys.platform != required:
            item.add_marker(
                pytest.mark.skip(reason=f"platform-specific Tauri test (target={required}); running on {sys.platform}")
            )
