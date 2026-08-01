"""Trimmed per CR-92 — trivial existence-check meta-tests removed.

Previously this file was 738 LOC of pure existence-check meta-tests
(e.g., ``assert "TestFoo" in test_bar.read_text()``) that pinned
whether a test class/file existed somewhere in the suite. These
meta-tests were brittle (broke on every test-file rename) and provided
no behavioral value — they asserted on test infrastructure rather
than product behavior.

The classes that pinned trivial existence (file/class/string
presence) have been DELETED. The meaningful invariants KEPT here are:

- ``TestVkLookupBenchmarkExists`` (PLAT-002): perf threshold
  (``_init_vk_map`` < 100ms) + correctness (VK_F2 == 113) +
  implementation contract (dict.get O(1) lookup, not linear scan).
- ``TestParametrizeUsageCountAboveThirty`` (TEST-032): count
  threshold (≥ 30 ``@pytest.mark.parametrize`` uses across the suite
  — catches a regression where parametrized tests get replaced with
  copy-paste variants).
- ``TestNoImportMockInTests`` (TEST-033): behavioral scan — actually
  walks every test file and fails if any uses the deprecated
  ``import mock`` form (vs the canonical ``from unittest.mock
  import``).

The deleted trivial existence checks should be replaced (if at all)
by a single static-check CI job that runs ``ruff`` / ``pyright`` on
the test suite, not by per-class Python existence pins.
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest


class TestVkLookupBenchmarkExists:
    """PLAT-002.

    The finding: VK lookup performance not benchmarked. Fix: add a
    pytest-benchmark test for the VK map initialization and lookup.

    The three tests below pin the meaningful invariants: the perf
    threshold, the O(1) implementation contract (dict.get, not a
    linear scan), and the correctness of a known VK code (F2 = 113).
    """

    def test_vk_map_initialization_is_fast(self):
        """VK map initialization must complete in under 100ms."""
        from voice_typer.server.hotkeys import _init_vk_map

        t0 = time.perf_counter()
        _init_vk_map()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 100, f"PLAT-002: VK map init took {elapsed_ms:.1f}ms (target < 100ms)"

    def test_vk_lookup_is_o1_dict_get(self):
        # KEEP — pins  (VK lookup uses dict.get, O(1)).
        # The sibling test_vk_map_initialization_is_fast and
        # test_vk_lookup_returns_correct_code_for_f2 test the speed and
        # correctness, but don't catch a regression where the lookup
        # switches to a linear scan that happens to be fast for small
        # maps. Source-string check catches the implementation choice.
        from voice_typer.server import hotkeys

        src = inspect.getsource(hotkeys)
        # The lookup uses _VK_MAP.get(key_name)
        assert "_VK_MAP.get" in src or "_VK_MAP[" in src, "PLAT-002: VK lookup must use dict.get (O(1))"

    def test_vk_lookup_returns_correct_code_for_f2(self):
        """VK_F2 = 0x71 (113)."""
        from voice_typer.server.hotkeys import _VK_MAP, _init_vk_map

        _init_vk_map()
        # F2 should map to VK_F2 = 113
        assert _VK_MAP.get("f2") == 113 or _VK_MAP.get("F2") == 113, (
            f"PLAT-002: VK lookup for 'f2' must return 113, got {_VK_MAP.get('f2')}"
        )


class TestParametrizeUsageCountAboveThirty:
    """TEST-032.

    The finding: only 6 @pytest.mark.parametrize uses. Investigation:
    41 uses now exist across 7 files. This test pins that state.
    """

    def test_parametrize_count_is_above_30(self):
        """At least 30 @pytest.mark.parametrize uses must exist.

        Uses Python's pathlib + grep instead of the Unix `grep` command
        so it works on Windows too.

        RW-8: KEEP — pins TEST-032 (>= 30 @pytest.mark.parametrize uses).
        A behavioral test would need to count parametrize uses at runtime,
        which is the same operation; the file-content check is the most
        direct way to catch a regression where parametrize uses drop.
        """
        tests_dir = Path(__file__).resolve().parent.parent
        count = 0
        for py_file in tests_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                count += content.count("@pytest.mark.parametrize")
            except Exception:
                pass
        assert count >= 30, f"TEST-032: expected at least 30 @pytest.mark.parametrize uses, found {count}"


class TestNoImportMockInTests:
    """TEST-033.

    The finding: `import mock` and `from unittest.mock import` coexist.
    Investigation: 0 `import mock` instances; convention documented in
    CONTRIBUTING.md. This test pins that state by actually walking
    every test file and failing on violations.
    """

    def test_no_import_mock_in_tests(self):
        """No test file must use `import mock` (use `from unittest.mock import` instead).

        Uses Python's pathlib instead of the Unix `grep` command so it
        works on Windows too.
        """
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
            f"TEST-033: found `import mock` usage in tests:\n{chr(10).join(violations)}\n"
            "Use `from unittest.mock import MagicMock, patch` instead."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
