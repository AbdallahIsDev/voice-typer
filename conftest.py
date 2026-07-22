"""Root conftest.py — makes --cov flags optional and non-failing on
subset runs.

CR-94 fix (this file): the previous implementation reached into the
pytest-cov plugin's internal ``options.cov_fail_under`` attribute and
mutated it to ``None`` on subset runs. That coupling was fragile
(plugin-internal attribute names can change between pytest-cov
releases) and surprising (a conftest mutating plugin state is
non-obvious). It has been removed.

Coverage-fail-under is now enforced ONLY by an explicit CI step that
opts in (``pytest tests/ --cov-fail-under=65``). Local subset runs
(``pytest tests/test_foo.py``) no longer see the threshold.

FIX-18 status (2026-07-22, sub-agent 18 "test infra & config"):
``pyproject.toml`` is OUT OF SCOPE for the FIX-18 sub-agent's file
ownership, so the ``--cov-fail-under=65`` flag has NOT been removed
from ``[tool.pytest.ini_options].addopts`` in ``pyproject.toml``.
This means plain ``pytest tests/`` invocations will STILL fail on
coverage (because ``addopts`` injects ``--cov-fail-under=65``
unconditionally). Until a follow-up sub-agent with ``pyproject.toml``
scope lands that removal, the ``pytest_load_initial_conftests`` shim
below still strips ``--cov*`` flags when pytest-cov is NOT installed
(so a bare ``pip install .`` without the [test] extra doesn't error on
unknown args), but we no longer attempt to disable the threshold at
runtime on subset runs — instead, users running subsets should pass
``-p no:cacheprovider --no-cov`` (or ``-o addopts=""``) to bypass the
``addopts`` threshold.

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
    internal ``options.cov_fail_under`` (CR-94: that hack was fragile
    and coupled to plugin internals). Subset runs that want to bypass
    the threshold should pass ``-o addopts=""`` or ``--no-cov``.
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
