"""``voice_typer/server/_paths.py``."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_purged_module(key: str) -> bool:
    """True for any module key that :func:`_purge_paths_and_config`"""
    return (
        key == "voice_typer.server._paths"
        or key == "voice_typer.server.config"
        or key.startswith("voice_typer.server.config.")
        or key.startswith("voice_typer.server.config_internals.")
        or key.startswith("voice_typer.server.config_validators")
    )


def _purge_paths_and_config() -> None:
    """``sys.modules`` so the next ``importlib.import_module`` re-executes"""
    for key in list(sys.modules):
        if _is_purged_module(key):
            del sys.modules[key]


@pytest.fixture(autouse=True)
def _restore_purged_modules():
    """Snapshot and restore the modules purged by this module's tests."""
    saved = {k: v for k, v in list(sys.modules.items()) if _is_purged_module(k)}
    # Snapshot parent-package attributes: re-importing a submodule
    saved_attrs = {}
    for key in saved:
        parent_name, _, attr = key.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, attr):
            saved_attrs[key] = (parent, attr, getattr(parent, attr))
    yield
    # Drop any freshly-imported objects created under these keys during
    for key in list(sys.modules):
        if _is_purged_module(key) and key not in saved:
            del sys.modules[key]
    sys.modules.update(saved)
    for _key, (parent, attr, value) in saved_attrs.items():
        if parent is not None:
            setattr(parent, attr, value)


def test_importing_paths_does_not_eagerly_pull_in_config() -> None:
    """Importing ``_paths`` must NOT pull"""
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


def test_paths_module_initial_config_dir_is_unresolved() -> None:
    """After a fresh import, ``_paths._config_dir`` is an unresolved holder."""
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    resolver = _paths._config_dir
    assert isinstance(resolver, _paths._ConfigDirResolver), (
        " regression: _paths._config_dir should be a _ConfigDirResolver "
        f"after a fresh import (MO-12 holder). Got: {resolver!r}."
    )
    assert resolver._cached is None and resolver._override is None, (
        " regression: the resolver must be unresolved after a fresh "
        "import (the lazy import hasn't fired yet). Got: "
        f"cached={resolver._cached!r} override={resolver._override!r}."
    )


test_paths_module_initial_config_dir_is_none = test_paths_module_initial_config_dir_is_unresolved


def test_paths_cold_import_does_not_pull_config_in_fresh_interpreter() -> None:
    """Cold import in a fresh interpreter must NOT pull the heavy"""
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


def test_first_helper_call_triggers_lazy_import(tmp_path: Path) -> None:
    """The first call to any helper (e.g. :func:`config_dir`) triggers"""
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    assert "voice_typer.server.config" not in sys.modules
    resolver = _paths._config_dir
    assert isinstance(resolver, _paths._ConfigDirResolver)
    assert resolver._cached is None and resolver._override is None

    # First call triggers the lazy import. We DON'T patch
    result = _paths.config_dir()

    # The returned path is the real platform config dir (we don't
    assert isinstance(result, Path)
    assert "voice_typer.server.config" in sys.modules, (
        " regression: the first helper call did NOT pull "
        "voice_typer.server.config into sys.modules. The lazy resolver "
        "may have been replaced with an inline implementation that "
        "doesn't cache the imported function."
    )
    assert isinstance(_paths._config_dir, _paths._ConfigDirResolver), (
        " regression: _paths._config_dir must stay the shared holder "
        "(MO-12: no module-attribute rebinding in the production path). "
        f"Got: {_paths._config_dir!r}."
    )
    assert _paths._config_dir._cached is not None, (
        " regression: the resolver cache is still empty after the "
        "first helper call, the lazy resolver didn't cache the "
        "imported function."
    )

    # Second call reuses the cached function (no re-import). We can't
    cached_fn = _paths._resolve_config_dir()
    _ = _paths.config_dir()
    assert _paths._config_dir._cached is cached_fn, (
        " regression: the cached resolver changed between calls, "
        "the lazy resolver is re-importing on every call instead of "
        "caching."
    )


def test_helpers_return_paths_under_pinned_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing ``monkeypatch.setattr(_paths, \"_config_dir\","""
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    monkeypatch.setattr(_paths, "_config_dir", lambda: tmp_path)

    assert _paths.config_dir() == tmp_path
    assert _paths.hf_cache_dir() == tmp_path / "huggingface"
    assert _paths.user_data_dir() == tmp_path
    assert _paths.prewarm_launchagent_log() == tmp_path / "prewarm-launchagent.log"
    assert _paths.autostart_log() == tmp_path / "autostart.log"
    assert "voice_typer.server.config" not in sys.modules, (
        " regression: the patched _paths._config_dir did NOT "
        "short-circuit the lazy resolver, voice_typer.server.config "
        "was imported even though the test fixture pinned the value. "
        "The lazy resolver must use the rebound module attribute "
        "instead of importing."
    )


def test_resolver_override_pins_without_rebinding(
    tmp_path: Path,
) -> None:
    """``_config_dir.override(fn)`` pins the resolver without rebinding."""
    _purge_paths_and_config()
    _paths = importlib.import_module("voice_typer.server._paths")
    holder = _paths._config_dir
    assert isinstance(holder, _paths._ConfigDirResolver)
    holder.override(lambda: tmp_path)
    try:
        assert _paths._config_dir is holder
        assert _paths.config_dir() == tmp_path
        assert _paths.hf_cache_dir() == tmp_path / "huggingface"
        assert "voice_typer.server.config" not in sys.modules
    finally:
        holder.override(None)
