"""Voice Typer test suite.

This package marker makes ``tests`` importable as a package so that
relative imports work in test files that need shared fixtures or
helpers.  The actual test discovery and shared fixtures live in
``conftest.py``.

Test categories:
- ``test_round8/9/10/11/12/13/16/17_*.py`` — E2E regression suites
  that exercise the full IPC → service → app stack.
- ``test_<module>.py`` — per-module unit tests.
- ``test_new_*.py`` — regression tests added in Rounds 2+3 for
  specific issue IDs (NEW-CQ-030, NEW-IPC-001, etc.).

Run: ``pytest tests/ -v`` from the repo root.
"""
