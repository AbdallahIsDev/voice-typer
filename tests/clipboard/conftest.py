"""Shared fixtures and collection-time mocks for the clipboard test suite.

TK-47 (WR-9): the collection-time ``sys.modules.setdefault`` block for
``pynput`` / ``pynput.keyboard`` / ``pyperclip`` that previously lived
here is now centralized in the PARENT ``tests/conftest.py`` — pytest
imports parent conftests before child conftests, so the parent's
module-level ``setdefault`` already bridges the window between
pytest's initial collection pass and the autouse
``mock_heavy_imports`` fixture in ``tests/conftest.py`` (which installs
fresh per-test mocks at function scope, after every test module's
top-level ``from voice_typer.server.clipboard import …`` line has
already executed).

History (why this file once existed): the setdefault block was
originally duplicated (in slightly varying forms — either
``MagicMock()`` directly or via local ``mock_pynput`` /
``mock_pynput_kb`` variables) across 17 clipboard test files at
``tests/`` top level. It was centralized HERE as an intermediate step;
the final TK-47 step moved it to the parent conftest so no conftest or
test file needs its own block.

The old argument for keeping the block scoped to ``tests/clipboard/``
("No pollution of sibling test suites" — hotkey tests that genuinely
need real pynput, e.g. ``tests/test_hotkeys.py`` with
``@pytest.mark.real_pynput``, are not affected) is now addressed in
the parent conftest itself: the per-test ``mock_heavy_imports``
fixture's ``real_pynput`` branch EVICTS the collection-time mock (by
``__spec__`` detection, mirroring the ``real_pil`` eviction pattern)
so a real pynput import loads from disk for marked tests.

Note: ``PIL`` is intentionally NOT mocked at collection time anywhere
(same rationale as the ``tests/server/conftest.py`` comment block) —
tests that need real PIL (e.g. ``tests/test_tray_icon.py`` with
``@pytest.mark.real_pil``) would break if we polluted ``sys.modules``
at collection time.
"""

# This conftest intentionally has no code of its own — it exists so
# `tests/clipboard/` remains a proper pytest package root for future
# clipboard fixtures (see the migration note in the docstring).
