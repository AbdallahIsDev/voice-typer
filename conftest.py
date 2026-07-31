"""Root conftest.py — makes --cov flags optional and non-failing on
subset runs.

 fix (this file): the previous implementation reached into the
pytest-cov plugin's internal ``options.cov_fail_under`` attribute and
mutated it to ``None`` on subset runs. That coupling was fragile
(plugin-internal attribute names can change between pytest-cov
releases) and surprising (a conftest mutating plugin state is
non-obvious). It has been removed.

Coverage threshold location: ``--cov-fail-under=65`` is NOT in
``[tool.pytest.ini_options].addopts`` in ``pyproject.toml`` (it was
removed from there so subset runs don't trip the gate). The threshold
lives only in ``[tool.coverage.report].fail_under`` and is passed
explicitly by the CI pytest step in ``build.yml``
(``pytest tests/ --cov-fail-under=65``). As a result, plain
``pytest tests/test_foo.py`` works fine without ``--no-cov`` or
``-o addopts=""``; it will not fail on coverage locally.

The ``pytest_load_initial_conftests`` shim below is still needed for
the "bare ``pip install .`` without the [test] extra" case where
pytest-cov is absent: it strips ``--cov*`` flags from ``sys.argv`` so
pytest does not error on unknown args before collecting. When
pytest-cov IS installed, the shim is a no-op and the real coverage
behavior is governed by ``pyproject.toml`` and CI.

If you want to enforce coverage locally, run::

    pytest tests/ --cov-fail-under=65
"""

from __future__ import annotations

import sys


def pytest_load_initial_conftests(early_config, parser):
    """Strip --cov flags from sys.argv when pytest-cov is not installed.

    This handles the "bare ``pip install .`` without the [test] extra"
    case where pytest-cov is absent: ``--cov=voice_typer`` and
    ``--cov-fail-under=65`` would be unrecognized arguments and pytest
    would error before collecting.

    When pytest-cov IS installed, we no longer mutate the plugin's
    internal ``options.cov_fail_under`` (: that hack was fragile
    and coupled to plugin internals). Subset runs that want to bypass
    the threshold should pass ``-o addopts=""`` or ``--no-cov``.

    Rationale for the sys.argv mutation:
      ``--cov`` / ``--cov-fail-under`` are *positional* in pytest's
      argv parser when pytest-cov is absent — they are not registered
      as known options, so pytest's ``Config.parseuptools`` would
      raise ``UsageError: unrecognized arguments`` and abort collection
      before any test runs. Mutating ``sys.argv`` here (rather than
      registering a no-op ``--cov`` flag via ``parser.addoption``) is
      the lowest-friction fix: it preserves the exact ``addopts`` from
      ``pyproject.toml`` for the CI path (where pytest-cov IS
      installed and the ``--cov`` flag is honored), while letting a
      minimal-install dev environment run ``pytest`` without
      errors. The mutation runs ONLY in the ``ImportError`` branch —
      when pytest-cov is installed, ``sys.argv`` is untouched.
    """
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
