"""Root conftest.py — makes --cov flags optional and non-failing on
subset runs.

ADR-0020 round-2 fix: ``pyproject.toml`` declares
``addopts = "-v --tb=short --cov=voice_typer --cov-fail-under=65"``.
Two problems arise from this:

1. When pytest-cov is NOT installed (e.g. a bare ``pip install .``
   without the [test] extra), a plain ``pytest`` invocation errors
   before collecting with::

       unrecognized arguments: --cov=voice_typer --cov-fail-under=65

2. When pytest-cov IS installed but the user runs a SUBSET of tests
   (e.g. ``pytest tests/tauri/``), the --cov-fail-under=65 threshold
   fails because partial test runs naturally have low coverage.

This conftest handles both:

- ``pytest_load_initial_conftests``: if pytest-cov is absent, strip
  all --cov flags from sys.argv so pytest doesn't error on unknown
  args.
- ``pytest_configure``: if this is a subset run (not the full suite),
  find the registered CovPlugin instance (``_cov``) and set its
  ``options.cov_fail_under = None``. The plugin reads this value at
  session end (``pytest_terminal_summary`` line 410 of plugin.py:
  ``if self.options.cov_fail_under is not None and > 0:``), so None
  skips enforcement. Coverage is still MEASURED, just not enforced.

The full-suite CI run (``pytest tests/`` with no path args) still
enforces the 65% threshold.

Why ``config.pluginmanager.getplugin('_cov')`` instead of
``config.option.cov_fail_under = None``?  The CovPlugin stores a
reference to ``early_config.known_args_namespace`` (not
``config.option``) at construction time, so modifying
``config.option`` does NOT propagate to the plugin. We must modify
the plugin's ``self.options`` directly.
"""

from __future__ import annotations

import sys


def _is_full_suite_run() -> bool:
    """Return True if the user is running the full test suite."""
    test_paths = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not test_paths:
        return True
    if len(test_paths) == 1 and test_paths[0].rstrip("/") in ("tests", "."):
        return True
    return False


def pytest_load_initial_conftests(early_config, parser):
    """Strip --cov flags from sys.argv when pytest-cov is not installed."""
    try:
        import pytest_cov  # noqa: F401
    except ImportError:
        new_argv = [sys.argv[0]]
        i = 1
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg.startswith("--cov"):
                if "=" not in arg and not arg.startswith("--cov-report"):
                    i += 2
                    continue
                i += 1
                continue
            new_argv.append(arg)
            i += 1
        sys.argv = new_argv


def pytest_configure(config):
    """Disable --cov-fail-under on subset runs by modifying the CovPlugin directly."""
    try:
        import pytest_cov  # noqa: F401
    except ImportError:
        return

    if _is_full_suite_run():
        return  # full suite — keep the threshold.

    # The CovPlugin is registered as '_cov'. Get it and set its
    # options.cov_fail_under to None so the plugin skips enforcement
    # at session end (plugin.py line 410: `if ... is not None and > 0`).
    try:
        plugin = config.pluginmanager.getplugin("_cov")
        if plugin is not None and hasattr(plugin, "options"):
            plugin.options.cov_fail_under = None
    except Exception:
        pass
