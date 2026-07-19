"""pytest configuration for the Tauri sidecar tests.

These tests use pytest-asyncio in auto mode so async tests don't need
explicit @pytest.mark.asyncio markers. This is local to the tauri/
test directory so it doesn't affect the project-wide pytest config.
"""

import os
import sys

import pytest

# Map Tauri migration-test file prefixes to the platform they verify.
# mig15 = Windows, mig16/mig18 = macOS, mig17 = Linux. These tests assert
# OS-specific Rust/Python behaviour (path resolution, launchctl, code
# signing, signal handling) and can only pass on their target OS — they
# are skipped automatically when run on the wrong platform so the suite
# stays green cross-platform.
_PLATFORM_FOR_PREFIX: dict[str, str] = {
    "mig15": "win32",
    "mig16": "darwin",
    "mig17": "linux",
    "mig18": "darwin",
}


def _required_platform(module_path: str) -> str | None:
    # Match on the containing directory name (e.g. ``mig17``), since the
    # test files themselves are named ``test_*.py``.
    directory = os.path.basename(os.path.dirname(module_path))
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
