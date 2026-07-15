"""pytest configuration for the Tauri sidecar tests.

These tests use pytest-asyncio in auto mode so async tests don't need
explicit @pytest.mark.asyncio markers. This is local to the tauri/
test directory so it doesn't affect the project-wide pytest config.
"""

import pytest


# Auto mode: every `async def test_*` is treated as an asyncio test
# without needing @pytest.mark.asyncio. This matches the pattern in
# the project's existing async tests (e.g. test_cloud_engines.py).
def pytest_collection_modifyitems(config, items):
    for item in items:
        if hasattr(item, "function") and getattr(item.function, "__code__", None):
            if item.function.__code__.co_flags & 0x100:  # CO_COROUTINE
                item.add_marker(pytest.mark.asyncio)
