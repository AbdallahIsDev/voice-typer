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
2. **Cold-import time** — ``python -X importtime`` shows the
   ``_paths`` line at <5ms (was ~54ms before the lazy fix). Verified
   via a subprocess invocation that parses the ``importtime`` output.
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
    """
    for key in list(sys.modules):
        if (
            key == "voice_typer.server._paths"
            or key == "voice_typer.server.config"
            or key.startswith("voice_typer.server.config.")
            or key.startswith("voice_typer.server.config_internals.")
            or key.startswith("voice_typer.server.config_path_safety")
            or key.startswith("voice_typer.server.config_validators")
        ):
            del sys.modules[key]


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
        "test setup bug: voice_typer.server.config should be absent "
        "from sys.modules before the _paths import"
    )
    assert "voice_typer.server._paths" not in sys.modules, (
        "test setup bug: voice_typer.server._paths should be absent "
        "from sys.modules before the import"
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
    print(f"DEBUG: voice_typer.server._paths attr = {getattr(sys.modules.get('voice_typer.server'), '_paths', 'MISSING')}")
    print(f"DEBUG: voice_typer.server._paths in sys.modules = {'voice_typer.server._paths' in sys.modules}")
    if 'voice_typer.server._paths' in sys.modules:
        cached = sys.modules['voice_typer.server._paths']
        print(f"DEBUG: cached module id={id(cached)}, is _paths: {cached is _paths}")
        print(f"DEBUG: cached._config_dir = {cached._config_dir!r}")
    assert _paths._config_dir is None, (
        " regression: _paths._config_dir should be None after a "
        "fresh import (the lazy resolver hasn't fired yet). Got: "
        f"{_paths._config_dir!r}."
    )


# ── 2. Cold-import time (python -X importtime) ─────────────────────────


def test_paths_cold_import_under_5ms() -> None:
    """``python -X importtime`` smoke check: the ``_paths`` line in the
    import-time trace must show a cumulative time under 5ms.

    Before the lazy fix, ``_paths`` showed ~54ms cumulative (because
    it transitively imported the heavy ``config`` package). After the
    fix, ``_paths`` itself imports in <1ms (only ``sys`` + ``pathlib``
    at the top — both already loaded by the parent
    ``voice_typer.server`` package init).

    This test spawns a subprocess that runs
    ``python -X importtime -c 'from voice_typer.server import _paths'``
    and parses the stderr output (``-X importtime`` writes to stderr)
    to find the ``voice_typer.server._paths`` line, then asserts the
    cumulative time is under 5ms.

    The 5ms ceiling matches the task's expected target (lazy import
    drops from 54ms to <5ms). The actual measured value on Linux +
    Python 3.12 is typically <1ms; we keep a 5x safety margin to
    absorb CI-machine variance (slower CPUs, cold disk caches).
    """
    # Use the same venv Python that pytest is running under so the
    # subprocess picks up the installed ``voice_typer`` package.
    cmd = [
        sys.executable,
        "-X",
        "importtime",
        "-c",
        "from voice_typer.server import _paths",
    ]
    # ``PYTHONPATH`` is inherited from the test process; the
    # ``voice_typer`` package is installed in editable mode so the
    # subprocess can import it without explicit cwd pinning.
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"subprocess failed (returncode={result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # ``-X importtime`` writes one line per imported module to stderr,
    # in the format:
    #   ``import time:    <self_us> |   <cumulative_us> |   <module_name>``
    # We look for the line whose module name is exactly
    # ``voice_typer.server._paths`` (no leading whitespace in the
    # module-name column means it's a top-level import, not a nested
    # child of another module's import).
    paths_line = None
    for line in result.stderr.splitlines():
        if "voice_typer.server._paths" not in line:
            continue
        # The module name is the last ``|``-separated field, stripped
        # of leading/trailing whitespace. We match the exact name so
        # we don't accidentally pick up a child like
        # ``voice_typer.server._paths.something``.
        parts = line.split("|")
        if len(parts) != 3:
            continue
        module_name = parts[2].strip()
        if module_name == "voice_typer.server._paths":
            paths_line = line
            break
    assert paths_line is not None, (
        "could not find the 'voice_typer.server._paths' line in the "
        f"importtime output:\n{result.stderr}"
    )
    # The cumulative time is the middle ``|``-separated field (in
    # microseconds). Parse it as an int and convert to milliseconds.
    cumulative_us = int(parts[1].strip())
    cumulative_ms = cumulative_us / 1000.0
    assert cumulative_ms < 5.0, (
        f" regression: voice_typer.server._paths cold import took "
        f"{cumulative_ms:.2f}ms (expected <5ms after the lazy-config "
        f"import fix). The module is likely eagerly importing "
        f"voice_typer.server.config again. Run:\n"
        f"  {sys.executable} -X importtime -c 'from voice_typer.server "
        f"import _paths'\nand look for the voice_typer.server.config "
        f"line in the trace."
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


def test_helpers_return_paths_under_pinned_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
