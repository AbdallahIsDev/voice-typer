"""Python-side guard that no blanket ``ignore::ResourceWarning``
filter is added to ``pyproject.toml``.

Mirrors the TS assertion at
``voice_typer/client/src/renderer/src/__tests__/behavior-rewrite/electron-ipc-build-behavior.test.tsx:1051``
(rewrite of TestNoBlanketResourceWarningFilter). The TS test runs
only under ``vitest``; this Python guard runs under ``pytest`` so a
contributor who only runs the Python suite still catches the regression.

A blanket ``ignore::ResourceWarning`` filter would hide real file-handle
/ socket leaks in the 24/7 long-running tray process. Targeted filters
(e.g. ``ignore::ResourceWarning:sounddevice``) are still allowed — only
the bare ``"ignore::ResourceWarning"`` form is rejected.

This file also guards two other pyproject.toml + pytest-config
invariants that have regressed in the past:

- ``norecursedirs`` MUST include ``.hypothesis`` (otherwise the
  hypothesis pytest plugin emits a ``UserWarning: Skipping collection
  of '.hypothesis' directory`` on every pytest run — see the
  ``test_norecursedirs_includes_hypothesis`` regression test below).
- ``filterwarnings`` MUST include ``"error::DeprecationWarning:voice_typer"``
  so voice_typer-originated DeprecationWarnings are ratcheted into
  errors (see ``test_filterwarnings_has_voice_typer_deprecation_ratchet``).
- The ``ci`` hypothesis profile MUST be registered with ``deadline=None``
  and loaded, so the 22 ``@settings``-decorated hypothesis tests don't
  flake with ``DeadlineExceeded`` on a loaded CI runner (see
  ``test_hypothesis_ci_profile_loaded_with_deadline_none``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def test_no_blanket_resource_warning_filter() -> None:
    """No line in ``pyproject.toml`` may start with ``"ignore::ResourceWarning"``.

    Reads the raw TOML text (not the parsed structure) so the guard fires
    even if a future contributor adds the filter outside the
    ``[tool.pytest.ini_options].filterwarnings`` array.
    """
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith('"ignore::ResourceWarning"'):
            msg = f"Blanket 'ignore::ResourceWarning' filter found at pyproject.toml:{lineno}: {stripped}"
            raise AssertionError(msg)


def test_norecursedirs_includes_hypothesis() -> None:
    """``norecursedirs`` in ``pyproject.toml`` MUST include ``.hypothesis``.

    Hypothesis writes its example database (``.hypothesis/examples/*``)
    under the repo root. When ``norecursedirs`` is set explicitly and does
    NOT include ``.hypothesis``, the hypothesis pytest plugin emits a
    ``UserWarning: Skipping collection of '.hypothesis' directory - this
    usually means you've explicitly set the norecursedirs pytest config
    option, replacing rather than extending the default ignores.`` on
    EVERY pytest run. The warning is harmless but noisy (it fires once
    per pytest invocation, including for unrelated test files like
    ``tests/test_text_cleanup.py``), and a contributor seeing it on every
    run will learn to ignore pytest warnings — defeating the
    ``filterwarnings`` ratchet that promotes real warnings to errors.

    Adding ``.hypothesis`` to ``norecursedirs`` silences the warning and
    also prevents pytest from attempting to collect the binary
    ``.hypothesis/examples/`` blobs as Python test modules (which would
    error with ``SyntaxError`` on import).

    Reads the raw TOML text so the guard fires even if a future
    contributor rewrites the ``norecursedirs`` line in a way that breaks
    the ``.hypothesis`` entry (e.g. reordering, quoting, or splitting
    across multiple lines).
    """
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Find the ``norecursedirs = [...]`` line. It's a single-line TOML
    # array in this project, but be defensive: scan for any line that
    # starts with ``norecursedirs`` (after stripping leading whitespace)
    # and contains the array literal.
    norecursedirs_lines = [(idx + 1, raw) for idx, raw in enumerate(lines) if raw.lstrip().startswith("norecursedirs")]
    assert norecursedirs_lines, "norecursedirs key missing from pyproject.toml"
    lineno, line = norecursedirs_lines[0]
    assert ".hypothesis" in line, (
        f"'.hypothesis' must be listed in norecursedirs (pyproject.toml:{lineno}) "
        f"to suppress the hypothesis pytest plugin's 'Skipping collection of "
        f".hypothesis' UserWarning. Current value: {line.strip()}"
    )


def test_filterwarnings_has_voice_typer_deprecation_ratchet() -> None:
    """``filterwarnings`` MUST include ``"error::DeprecationWarning:voice_typer"``.

    This is a ratchet: once a ``voice_typer``-originated deprecation is
    fixed, this filter ensures it can never silently regress — the
    DeprecationWarning is promoted to a hard test error. The filter is
    scoped to the ``voice_typer`` module (via the trailing ``:voice_typer``
    module-regex field) so third-party DeprecationWarnings (e.g. the
    ``torch.jit.load`` deprecation) are NOT promoted to errors.

    Without this filter, a regressed deprecation passes silently and is
    only caught much later when a downstream consumer upgrades Python
    and the deprecation becomes a hard error.
    """
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    assert '"error::DeprecationWarning:voice_typer"' in text, (
        "filterwarnings must include the literal string "
        "'error::DeprecationWarning:voice_typer' to ratchet "
        "voice_typer-originated DeprecationWarnings into errors. "
        "Without it, a regressed deprecation passes silently."
    )


def test_hypothesis_ci_profile_loaded_with_deadline_none() -> None:
    """The ``ci`` hypothesis profile MUST be registered with ``deadline=None``.

    Registered + loaded in ``tests/conftest.py:pytest_configure`` (not in
    ``pyproject.toml`` — hypothesis profiles are runtime-registered via
    ``hypothesis.settings.register_profile``). Without ``deadline=None``,
    the 22 ``@settings``-decorated hypothesis tests across
    ``tests/test_property_based.py``,
    ``tests/test_text_cleanup_hypothesis.py`` and
    ``tests/test_streaming_hypothesis.py`` can fail with
    ``FlakyFailure(DeadlineExceeded)`` on a loaded CI runner (hypothesis's
    default ``deadline`` is 200ms per test case).

    This test verifies three things at runtime (it runs UNDER pytest, so
    ``conftest.py:pytest_configure`` has already executed by the time the
    test body runs):

    1. The ``ci`` profile is registered (``settings._profiles`` contains
       ``"ci"``).
    2. The ``ci`` profile's ``deadline`` is ``None``.
    3. The ``ci`` profile is the currently-loaded profile
       (``settings._current_profile == "ci"``), so per-test ``@settings``
       decorators inherit ``deadline=None`` as their parent.

    Hypothesis ships with a built-in ``ci`` profile that already has
    ``deadline=None``, so this test would pass even without
    ``conftest.py``'s ``register_profile`` call — but the
    ``_current_profile == "ci"`` assertion verifies that
    ``conftest.py`` actually LOADS the profile (without ``load_profile``,
    hypothesis stays on the ``default`` profile with ``deadline=200ms``).
    """
    hypothesis = pytest.importorskip("hypothesis", reason="hypothesis not installed")
    settings = hypothesis.settings

    # (1) The ``ci`` profile is registered.
    registered_profiles = settings._profiles
    assert "ci" in registered_profiles, (
        "hypothesis 'ci' profile is not registered — "
        "tests/conftest.py:pytest_configure must call "
        "settings.register_profile('ci', deadline=None, ...). "
        f"Registered profiles: {sorted(registered_profiles)!r}"
    )

    # (2) The ``ci`` profile's deadline is None.
    ci_settings = settings.get_profile("ci")
    assert ci_settings.deadline is None, (
        f"hypothesis 'ci' profile must have deadline=None; "
        f"got deadline={ci_settings.deadline!r}. "
        "tests/conftest.py:pytest_configure must register the profile "
        "with deadline=None."
    )

    # (3) The ``ci`` profile is the currently-loaded profile. This is the
    # assertion that catches a future regression where someone removes the
    # ``settings.load_profile("ci")`` call from conftest.py — without
    # that call, hypothesis stays on the ``default`` profile (deadline=200ms)
    # even though the ``ci`` profile is registered.
    assert settings._current_profile == "ci", (
        f"hypothesis 'ci' profile is registered but not loaded — "
        f"current profile is {settings._current_profile!r}. "
        "tests/conftest.py:pytest_configure must call "
        "settings.load_profile('ci') after register_profile."
    )
