"""Config-directory patching helpers shared across test files.

Single authoritative place that knows WHICH module references must be
patched to redirect the app's config directory in tests. Every test
that needs a fake config dir should go through
:func:`patch_config_dir_refs` (directly or via the ``tmp_config_dir``
fixture in ``tests/conftest.py``) instead of re-listing the patch
targets inline.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["patch_config_dir_refs"]


def patch_config_dir_refs(monkeypatch, path: Path) -> None:
    """Redirect every ``_config_dir`` binding to *path* for one test.

    Patches all three known bindings:

    - ``voice_typer.server.config._config_dir`` — the canonical accessor;
      app.py routes its internal calls through ``_resolve_config_dir()``
      (call-time indirection), so this patch intercepts every app path;
    - ``voice_typer.server.app._config_dir`` — belt-and-suspenders for
      consumers that deliberately resolve via the app module at call
      time (``single_instance`` reads ``_app_module._config_dir()``);
    - ``voice_typer.server._paths._config_dir`` — the lazy resolver's
      memoized callable (once a previous test has triggered resolution,
      this attribute pins the REAL function and silently ignores the
      canonical-name patch).

    Works with both ``monkeypatch`` fixtures and manual
    ``pytest.MonkeyPatch`` instances.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: path)
    import voice_typer.server._paths as _paths_mod

    monkeypatch.setattr(_paths_mod, "_config_dir", lambda: path)
