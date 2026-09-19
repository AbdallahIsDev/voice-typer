"""Trimmed: trivial existence-check meta-tests removed."""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest


class TestVkLookupBenchmarkExists:
    """
    pytest-benchmark test for the VK map initialization and lookup.
    The three tests below pin the meaningful invariants: the perf
    """

    def test_vk_map_initialization_is_fast(self):
        """VK map initialization must complete in under 100ms."""
        from voice_typer.server.hotkeys import _init_vk_map

        t0 = time.perf_counter()
        _init_vk_map()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"VK map init took {elapsed_ms:.1f}ms (target < 100ms)"

    def test_vk_lookup_is_o1_dict_get(self):
        # KEEP, pins  (VK lookup uses dict.get, O(1)).
        from voice_typer.server import hotkeys

        src = inspect.getsource(hotkeys)
        # The lookup uses _VK_MAP.get(key_name)
        assert "_VK_MAP.get" in src or "_VK_MAP[" in src, "VK lookup must use dict.get (O(1))"

    def test_vk_lookup_returns_correct_code_for_f2(self):
        """VK_F2 = 0x71 (113)."""
        from voice_typer.server.hotkeys import _VK_MAP, _init_vk_map

        _init_vk_map()
        # F2 should map to VK_F2 = 113
        assert _VK_MAP.get("f2") == 113 or _VK_MAP.get("F2") == 113, (
            f"VK lookup for 'f2' must return 113, got {_VK_MAP.get('f2')}"
        )


class TestParametrizeUsageCountAboveThirty:
    """The finding: only 6 @pytest.mark.parametrize uses. Investigation:"""

    def test_parametrize_count_is_above_30(self):
        """At least 30 @pytest.mark.parametrize uses must exist."""
        tests_dir = Path(__file__).resolve().parent.parent
        count = 0
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                count += content.count("@pytest.mark.parametrize")
            except Exception:
                pass
        assert count >= 30, f"expected at least 30 @pytest.mark.parametrize uses, found {count}"


class TestNoImportMockInTests:
    """
    The finding: `import mock` and `from unittest.mock import` coexist.
    CONTRIBUTING.md. This test pins that state by actually walking
    """

    def test_no_import_mock_in_tests(self):
        """No test file must use `import mock` (use `from unittest.mock import` instead)."""
        tests_dir = Path(__file__).resolve().parent.parent
        violations = []
        for py_file in tests_dir.rglob("*.py"):
            try:
                for line_num, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip() == "import mock":
                        violations.append(f"{py_file}:{line_num}")
            except Exception:
                pass
        assert not violations, (
            f"found `import mock` usage in tests:\n{chr(10).join(violations)}\n"
            "Use `from unittest.mock import MagicMock, patch` instead."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
