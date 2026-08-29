"""regression tests for the lazy ``_config_dir`` resolver in
``voice_typer/server/_paths.py``.

Previously this module eagerly did ``from voice_typer.server.config
import _config_dir`` at module load (line 86), which pulled in the
heavy ``voice_typer.server.config`` package (validators,
secure_file_io, volume_ducker, duck_crash_recovery, etc.) — measured
cold-start cost: ~54ms. The eager import has been replaced by
:func:`_resolve_config_dir`, which imports ``_config_dir`` from the
``config`` package on first call and caches the function reference on
the module's ``_config_dir`` attribute.

These tests pin:

1. **Eager-import suppression** — importing ``_paths`` does NOT pull
   ``voice_typer.server.config`` into ``sys.modules`` (the heavy
   ``config`` package stays unloaded until a helper is actually
   called). This is the load-bearing assertion: a regression that
   re-introduces a top-level ``from voice_typer.server.config import
   _config_dir`` makes this test fail immediately.
2. **Cold-import in a fresh interpreter** — a subprocess imports
   ``_paths`` and asserts the heavy ``voice_typer.server.config``
   package is NOT pulled into ``sys.modules`` (the regression the eager
   import caused). This used to be a ``python -X importtime`` wall-clock
   bound (<5ms cumulative); wall-clock import timing is
   machine-dependent (Windows CI runners with cold disk caches /
   antivirus scanning can take 10ms+ for any .py import), so the check
   is now deterministic.
3. **Lazy resolution still works** — calling any helper (e.g.
   :func:`config_dir`) triggers the lazy import on first use and
   caches the resolved function on ``_paths._config_dir``.
4. **Test-patch compatibility** — the existing
   ``monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)``
   pattern (used by ``tests/test_paths.py`` and
   ``tests/test_app_cleanup.py``) short-circuits the lazy resolver,
   so tests don't pay the heavy-import cost or touch the real
   filesystem.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_purged_module(key: str) -> bool:
    """True for any module key that :func:`_purge_paths_and_config`
    removes from ``sys.modules``.

    Shared with the :func:`_restore_purged_modules` autouse fixture so
    the purge set and the restore set can never drift apart.
    """
    return (
        key == "voice_typer.server._paths"
        or key == "voice_typer.server.config"
        or key.startswith("voice_typer.server.config.")
        or key.startswith("voice_typer.server.config_internals.")
        or key.startswith("voice_typer.server.config_validators")
    )


def _purge_paths_and_config() -> None:
    """Remove ``_paths``, ``config``, and related modules from
    ``sys.modules`` so the next ``importlib.import_module`` re-executes
    the module body (otherwise the cached module from a prior test
    would short-circuit and we wouldn't observe a fresh import).

    Purges both the leaf modules AND the parent packages so a re-import
    triggers the full ``__init__.py`` body again. Without this, a
    previously-imported ``voice_typer.server`` package would have
    ``_paths`` already in its namespace and the
    ``from voice_typer.server import _paths`` would not re-execute the
    module body.

    NOTE: the deletion is process-global. The autouse
    :func:`_restore_purged_modules` fixture snapshots the affected
    entries BEFORE each test runs and puts the ORIGINAL module objects
    back afterwards, so the purge cannot leak into the rest of the
    suite (modules imported earlier, e.g.
    ``voice_typer.server.task_scheduler``'s ``_paths`` binding, keep
    referencing the original objects whose ``_config_dir`` lru_cache
    still holds the real config dir).
    """
    for key in list(sys.modules):
        if _is_purged_module(key):
            del sys.modules[key]


@pytest.fixture(autouse=True)
def _restore_purged_modules():
    """Snapshot and restore the modules purged by this module's tests.

    Each test deliberately deletes ``_paths`` / ``config`` /
    ``config_internals.*`` from ``sys.modules`` so it observes a cold
    import. But the deletion is visible process-wide: any module that
    was imported earlier in the suite keeps a direct reference to the
    ORIGINAL module object (e.g. ``voice_typer.server.task_scheduler``
    does ``from voice_typer.server import _paths`` at import time).
    If the deleted entries are never put back, later tests resolve
    through STALE objects — a ``Path.home`` / ``_config_dir``
    monkeypatch lands on the fresh object in ``sys.modules`` while the
    stale reference still returns the real config dir (order-dependent
    failures in ``test_task_scheduler::TestPrewarmCommand`` and
    ``test_perf_data_store_save_write::TestConfigSaveBackupSkip``).

    This fixture snapshots the affected ``sys.modules`` entries (and
    the parent-package attributes that a re-import rebinds) before each
    test, then restores them afterwards.
    """
    saved = {k: v for k, v in list(sys.modules.items()) if _is_purged_module(k)}
    # Snapshot parent-package attributes: re-importing a submodule
    # (e.g. ``voice_typer.server._paths``) rebinds the attribute on the
    # parent ``voice_typer.server`` package, which ``from ... import``
    # statements observe. Restore those too.
    saved_attrs = {}
    for key in saved:
        parent_name, _, attr = key.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, attr):
            saved_attrs[key] = (parent, attr, getattr(parent, attr))
    yield
    # Drop any freshly-imported objects created under these keys during
    # the test (the test re-imported a NEW ``_paths`` / ``config``), then
    # put the ORIGINAL objects back so later tests see the same module
    # graph that existed before this file ran.
    for key in list(sys.modules):
        if _is_purged_module(key) and key not in saved:
            del sys.modules[key]
    sys.modules.update(saved)
    for _key, (parent, attr, value) in saved_attrs.items():
        if parent is not None:
            setattr(parent, attr, value)


# ── 1. Eager-import suppression ────────────────────────────────────────


def test_importing_paths_does_not_eagerly_pull_in_config() -> None:
    """Importing ``_paths`` must NOT pull
    ``voice_typer.server.config`` into ``sys.modules``.

    The lazy :func:`_resolve_config_dir` defers the heavy ``config``
    package import to first call of any helper. If a regression
    re-introduces a top-level ``from voice_typer.server.config import
    _config_dir`` (or any other top-level import from the ``config``
    package), this test fails immediately.
    """
    _purge_paths_and_config()
    assert "voice_typer.server.config" not in sys.modules, (
        "test setup bug: voice_typer.server.config should be absent from sys.modules before the _paths import"
    )
    assert "voice_typer.server._paths" not in sys.modules, (
        "test setup bug: voice_typer.server._paths should be absent from sys.modules before the import"
    )
    importlib.import_module("voice_typer.server._paths")
    assert "voice_typer.server.config" not in sys.modules, (
        " regression: importing voice_typer.server._paths pulled "
        "voice_typer.server.config into sys.modules. The module likely "
        "has an eager ``from voice_typer.server.config import _config_dir`` "
        "at the top again. Run "
        "``python -X importtime -c 'from voice_typer.server import _paths'`` "
        "to find the offender."
    )


def test_paths_module_initial_config_dir_is_none() -> None:
    """After a fresh import, ``_paths._config_dir`` is ``None`` (the
    sentinel for "lazy resolver hasn't fired yet").

    This pins the resolver's contract: the heavy ``config`` import
    hasn't happened until the first helper call. If a regression
    eagerly assigns ``_config_dir`` at module load (e.g. by
    re-introducing the top-level ``from voice_typer.server.config
    import _config_dir`` line), this assertion fails.
    """
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    # Debug: print state for diagnosis
    import sys

    print(f"\nDEBUG: _paths id={id(_paths)}, _config_dir={_paths._config_dir!r}")
    print(
        f"DEBUG: voice_typer.server._paths attr = {getattr(sys.modules.get('voice_typer.server'), '_paths', 'MISSING')}"
    )
    print(f"DEBUG: voice_typer.server._paths in sys.modules = {'voice_typer.server._paths' in sys.modules}")
    if "voice_typer.server._paths" in sys.modules:
        cached = sys.modules["voice_typer.server._paths"]
        print(f"DEBUG: cached module id={id(cached)}, is _paths: {cached is _paths}")
        print(f"DEBUG: cached._config_dir = {cached._config_dir!r}")
    assert _paths._config_dir is None, (
        " regression: _paths._config_dir should be None after a "
        "fresh import (the lazy resolver hasn't fired yet). Got: "
        f"{_paths._config_dir!r}."
    )


# ── 2. Cold-import time (python -X importtime) ─────────────────────────


def test_paths_cold_import_does_not_pull_config_in_fresh_interpreter() -> None:
    """Cold import in a fresh interpreter must NOT pull the heavy
    ``voice_typer.server.config`` package into ``sys.modules``.

    Replaces an earlier ``python -X importtime`` wall-clock bound
    (<5ms cumulative). Wall-clock import timing is machine-dependent
    (Windows CI runners with cold disk caches / antivirus scanning can
    take 10ms+ for any .py import, inflating the cumulative figure even
    when the module body is tiny), which made the bound flaky. The
    load-bearing property — a regression that re-introduces an eager
    ``from voice_typer.server.config import _config_dir`` makes the
    import pull the whole config package — is asserted directly in a
    fresh subprocess, deterministically.
    """
    script = (
        "import sys;"
        "from voice_typer.server import _paths;"
        "pulled = 'voice_typer.server.config' in sys.modules;"
        "print('config_pulled:', pulled);"
        "sys.exit(1 if pulled else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        " regression: cold import of voice_typer.server._paths pulled "
        "voice_typer.server.config into sys.modules.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── 3. Lazy resolution still works ─────────────────────────────────────


def test_first_helper_call_triggers_lazy_import(tmp_path: Path) -> None:
    """The first call to any helper (e.g. :func:`config_dir`) triggers
    the lazy ``config`` import and caches the resolved function on
    ``_paths._config_dir``.

    After the first call:
    - ``voice_typer.server.config`` is in ``sys.modules``.
    - ``_paths._config_dir`` is no longer ``None`` (it's the cached
      ``functools._lru_cache_wrapper`` from
      ``config_internals.paths._config_dir``).
    - Subsequent helper calls reuse the cached function (no re-import).
    """
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    assert "voice_typer.server.config" not in sys.modules
    assert _paths._config_dir is None

    # First call triggers the lazy import. We DON'T patch
    # ``_paths._config_dir`` here because we want to exercise the
    # lazy-resolver path itself (the test_paths.py / test_app_cleanup.py
    # suites already cover the patched path).
    result = _paths.config_dir()

    # The returned path is the real platform config dir (we don't
    # assert on its exact value — that depends on the host's
    # ``$XDG_DATA_HOME`` / ``$HOME``). We just assert it's a Path.
    assert isinstance(result, Path)
    assert "voice_typer.server.config" in sys.modules, (
        " regression: the first helper call did NOT pull "
        "voice_typer.server.config into sys.modules. The lazy resolver "
        "may have been replaced with an inline implementation that "
        "doesn't cache the imported function."
    )
    assert _paths._config_dir is not None, (
        " regression: _paths._config_dir is still None after the "
        "first helper call — the lazy resolver didn't cache the "
        "imported function."
    )

    # Second call reuses the cached function (no re-import). We can't
    # directly assert "no re-import happened", but we can assert the
    # cached function reference is stable across calls.
    cached_fn = _paths._config_dir
    _ = _paths.config_dir()
    assert _paths._config_dir is cached_fn, (
        " regression: _paths._config_dir changed between calls — "
        "the lazy resolver is re-importing on every call instead of "
        "caching."
    )


def test_helpers_return_paths_under_pinned_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing ``monkeypatch.setattr(_paths, "_config_dir",
    lambda: tmp_path)`` pattern short-circuits the lazy resolver.

    When the test fixture patches ``_paths._config_dir`` to a custom
    callable, the lazy resolver sees a non-None value and returns it
    immediately — the heavy ``config`` import never fires, and the
    helpers return paths under the pinned tmp_path.

    This mirrors the autouse fixture in ``tests/test_paths.py`` and
    ``tests/test_app_cleanup.py``. It's the load-bearing test-patch
    pattern: if the lazy resolver ignored the patched value (e.g. by
    always re-importing), every existing _paths test would touch the
    real filesystem.
    """
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)

    # The patched value must short-circuit the lazy resolver — the
    # heavy ``config`` package should NOT be imported.
    assert _paths.config_dir() == tmp_path
    assert _paths.hf_cache_dir() == tmp_path / "huggingface"
    assert _paths.user_data_dir() == tmp_path
    assert _paths.prewarm_launchagent_log() == tmp_path / "prewarm-launchagent.log"
    assert _paths.autostart_log() == tmp_path / "autostart.log"
    assert "voice_typer.server.config" not in sys.modules, (
        " regression: the patched _paths._config_dir did NOT "
        "short-circuit the lazy resolver — voice_typer.server.config "
        "was imported even though the test fixture pinned the value. "
        "The lazy resolver must check `if _config_dir is None` before "
        "importing."
    )
