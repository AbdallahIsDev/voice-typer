"""Root conftest.py — makes the optional pytest plugins (pytest-cov,
pytest-timeout, pytest-asyncio) non-fatal when they are not installed.

The repo's ``[tool.pytest.ini_options].addopts`` in ``pyproject.toml``
carries plugin-owned flags (``--timeout=60 --timeout-method=thread
--cov=voice_typer``). Those flags are only understood when the owning
plugin is installed. CI jobs that install ONLY ``pytest`` — e.g. the
product-namespace drift guard in ``build.yml``, which deliberately
avoids the project's [test] extras to keep the job ~1 min — would
otherwise abort with ``unrecognized arguments`` before any test
collects (pytest merges ini ``addopts`` into the argv it parses, and an
unregistered option is a hard error).

Fix: this conftest registers NO-OP stand-in options for each missing
plugin via ``pytest_addoption``, so the ini ``addopts`` parse cleanly on
a bare install. When the real plugin IS installed, the stand-ins are
skipped (the plugin registers the real option) and the flag keeps its
real behavior (coverage enforcement / test timeout).

Note: an earlier implementation stripped ``--cov*`` flags from
``sys.argv`` inside ``pytest_load_initial_conftests``. That was
ineffective: ``pytest`` captures ``sys.argv`` up front in
``_prepareconfig`` (before conftests load) and merges the ini
``addopts`` afterwards, so the shim could neither see the CLI flags it
tried to strip nor reach the ini-sourced ones (the CI error listed
``--cov=voice_typer`` even with the shim present). Option registration
via ``pytest_addoption`` is the correct hook — it runs before the
ini-addopts argv is parsed, so both CLI-passed and ini-sourced flags
are accepted.

Coverage threshold location: ``--cov-fail-under=65`` is NOT in
``[tool.pytest.ini_options].addopts`` in ``pyproject.toml`` (it was
removed from there so subset runs don't trip the gate). The threshold
lives only in ``[tool.coverage.report].fail_under`` and is passed
explicitly by the CI pytest step in ``build.yml``
(``pytest tests/ --cov-fail-under=65``). As a result, plain
``pytest tests/test_foo.py`` works fine without ``--no-cov`` or
``-o addopts=""``; it will not fail on coverage locally.

If you want to enforce coverage locally, run::

    pytest tests/ --cov-fail-under=65
"""

from __future__ import annotations

import importlib


def _plugin_available(module_name: str) -> bool:
    """True if the given plugin module is importable in this environment."""
    try:
        importlib.import_module(module_name)
    except ImportError:
        return False
    return True


def pytest_addoption(parser: object) -> None:
    """Register no-op stand-ins for optional-plugin flags + ini keys.

    Mirrors the options pytest-cov / pytest-timeout register (and the
    ini keys pytest-asyncio registers), so the ``addopts`` / ini in
    pyproject.toml parse even when those plugins are not installed (bare
    ``pip install pytest`` CI jobs). The stand-ins are inert: they
    accept the flag / key and store the value, but nothing reads it.
    When the real plugin is present, the corresponding block is skipped
    so there is no conflicting-option error.
    """
    if not _plugin_available("pytest_cov"):
        parser.addoption(
            "--cov",
            action="store",
            default=None,
            help="(no-op stand-in: pytest-cov not installed)",
        )
        parser.addoption(
            "--cov-report",
            action="append",
            default=[],
            help="(no-op stand-in: pytest-cov not installed)",
        )
        parser.addoption(
            "--cov-config",
            action="store",
            default=None,
            help="(no-op stand-in: pytest-cov not installed)",
        )
        parser.addoption(
            "--cov-branch",
            action="store_true",
            default=False,
            help="(no-op stand-in: pytest-cov not installed)",
        )
        parser.addoption(
            "--cov-append",
            action="store_true",
            default=False,
            help="(no-op stand-in: pytest-cov not installed)",
        )
        parser.addoption(
            "--no-cov",
            action="store_true",
            default=False,
            help="(no-op stand-in: pytest-cov not installed)",
        )
        parser.addoption(
            "--cov-fail-under",
            action="store",
            default=None,
            help="(no-op stand-in: pytest-cov not installed)",
        )
    if not _plugin_available("pytest_timeout"):
        parser.addoption(
            "--timeout",
            action="store",
            default=None,
            help="(no-op stand-in: pytest-timeout not installed)",
        )
        parser.addoption(
            "--timeout-method",
            action="store",
            default=None,
            help="(no-op stand-in: pytest-timeout not installed)",
        )
    if not _plugin_available("pytest_asyncio"):
        # --strict-config (in addopts) turns unknown ini keys into a hard
        # error, so the asyncio keys must be registered when pytest-asyncio
        # is absent too. Inert without the plugin.
        parser.addini(
            "asyncio_mode",
            "(no-op stand-in: pytest-asyncio not installed)",
            type="string",
            default="auto",
        )
        parser.addini(
            "asyncio_default_fixture_loop_scope",
            "(no-op stand-in: pytest-asyncio not installed)",
            type="string",
            default="function",
        )
    # No stand-ins for pytest-xdist's `-n` / `--dist` / `--numprocesses`:
    # the only job that passes xdist flags (build.yml full suite, `-n auto
    # --dist=loadgroup`) installs the [test] extras, which include
    # pytest-xdist. Every bare-pytest job passes only the options covered
    # above — keep this boundary explicit so a future job that moves an
    # xdist invocation to a bare install fails loudly instead of silently
    # being accepted here and erroring later.
