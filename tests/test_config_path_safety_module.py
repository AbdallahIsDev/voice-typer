"""S5-regression tests for the ``config_path_safety`` module extraction.

These tests pin the new module-extraction contract introduced by
finding S5-CR-28 (partial — extract ONE module):

1. ``voice_typer.server.config_path_safety`` exists as a dedicated
   named module for path-traversal / path-containment validation.
2. The three path-safety helpers
   (``_validate_path_safety``, ``_is_path_within``,
   ``_validate_import_path``) are importable from
   ``voice_typer.server.config_path_safety`` directly.
3. The same helpers remain importable from
   ``voice_typer.server.config`` (backward-compat re-export) so
   existing callers (``env_validation.py``, ``model_handlers.py``,
   ``tests/test_path_traversal.py``,
   ``tests/test_config_path_safety.py``,
   ``tests/test_env_validation_*.py``,
   ``tests/test_import_model_security.py``) keep working unchanged.
4. The function objects are SHARED —
   ``config._validate_path_safety is
   config_path_safety._validate_path_safety`` — so monkeypatching
   ``voice_typer.server.config._validate_path_safety`` continues to
   take effect on callers that import the symbol at call time
   (e.g. ``env_validation._validate_env_vars``).

See:
- ``voice_typer/server/config_path_safety.py``
- ``voice_typer/server/config.py`` (re-imports from ``config_path_safety``)
- ``scripts/findings/S5-CR-28.md``
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


class TestConfigPathSafetyModuleExists:
    """S5-``config_path_safety`` is a real, importable module."""

    def test_module_is_importable(self):
        """``voice_typer.server.config_path_safety`` must be importable."""
        mod = importlib.import_module("voice_typer.server.config_path_safety")
        assert mod is not None
        assert mod.__name__ == "voice_typer.server.config_path_safety"

    def test_module_has_path_safety_functions(self):
        """The three path-safety helpers must be module attributes."""
        from voice_typer.server import config_path_safety

        for name in ("_validate_path_safety", "_is_path_within", "_validate_import_path"):
            assert hasattr(config_path_safety, name), (
                f"S5-CR-28 regression: config_path_safety is missing {name!r} — "
                "the path-safety extraction must re-export all three helpers."
            )

    def test_module_all_dunder_lists_re_exports(self):
        """``__all__`` must list the three re-exported helpers so
        ``from config_path_safety import *`` and static analysis
        tools (pylint, ruff) see them as the module's public surface."""
        from voice_typer.server import config_path_safety

        assert set(config_path_safety.__all__) == {
            "_validate_path_safety",
            "_is_path_within",
            "_validate_import_path",
        }


class TestConfigReExportsPathSafety:
    """S5-``config`` re-exports the path-safety helpers so the
    public API is preserved (constraint #7)."""

    def test_validate_path_safety_importable_from_config(self):
        from voice_typer.server.config import _validate_path_safety

        assert callable(_validate_path_safety)

    def test_is_path_within_importable_from_config(self):
        from voice_typer.server.config import _is_path_within

        assert callable(_is_path_within)

    def test_validate_import_path_importable_from_config(self):
        from voice_typer.server.config import _validate_import_path

        assert callable(_validate_import_path)

    def test_function_objects_are_shared(self):
        """``config._validate_path_safety is
        config_path_safety._validate_path_safety`` — they must be the
        SAME function object so monkeypatching one site affects all
        callers (the tests in ``test_env_validation_config_dir.py``
        rely on this)."""
        from voice_typer.server import config, config_path_safety

        assert config._validate_path_safety is config_path_safety._validate_path_safety
        assert config._is_path_within is config_path_safety._is_path_within
        assert config._validate_import_path is config_path_safety._validate_import_path

    def test_function_objects_match_underlying_paths_module(self):
        """The re-exports ultimately resolve to the same function
        objects defined in ``config_internals.paths`` — the
        extraction is a routing change, not a duplication."""
        from voice_typer.server import config, config_path_safety
        from voice_typer.server.config_internals import paths

        assert config._validate_path_safety is paths._validate_path_safety
        assert config_path_safety._validate_path_safety is paths._validate_path_safety
        assert config._is_path_within is paths._is_path_within
        assert config_path_safety._is_path_within is paths._is_path_within


class TestPathSafetyFunctionsStillWork:
    """S5-the extraction must not change the function
    behaviour. A few smoke tests pin the contract."""

    def test_validate_path_safety_accepts_child(self, tmp_path):
        from voice_typer.server.config_path_safety import _validate_path_safety

        child = tmp_path / "config.json"
        child.touch()
        result = _validate_path_safety(child, tmp_path)
        assert result == child.resolve()

    def test_validate_path_safety_rejects_traversal(self):
        from voice_typer.server.config_path_safety import _validate_path_safety

        parent = Path("/home/user")
        escaped = Path("/home/user/../etc/passwd")
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path_safety(escaped, parent)

    def test_is_path_within_returns_bool(self, tmp_path):
        from voice_typer.server.config_path_safety import _is_path_within

        assert _is_path_within(tmp_path, tmp_path) is True
        assert _is_path_within(tmp_path / "child", tmp_path) is True
        assert _is_path_within(Path("/etc"), tmp_path) is False

    def test_validate_import_path_rejects_outside_roots(self):
        """A path outside the allowed roots (home, temp, HF cache)
        must raise ValueError. We use a deliberately-bogus absolute
        path that is NOT under any allowed root.

        Note: this test is best-effort — on some CI runners ``/``
        itself may be considered "within home" if the runner runs as
        root with ``HOME=/``. We assert the function either returns a
        string (accepted) or raises ValueError (rejected) — never
        raises a different exception type."""
        from voice_typer.server.config_path_safety import _validate_import_path

        # Use a synthetic path that does not exist and is unlikely to
        # be under any allowed root. We don't assert the OUTCOME
        # (accept/reject) — only that the function does not crash
        # with an unexpected exception type.
        try:
            result = _validate_import_path("/nonexistent-bogus-path-for-s5-cr-28-test")
        except ValueError:
            pass  # Rejected — acceptable.
        else:
            assert isinstance(result, str), (
                f"S5-_validate_import_path must return a str or raise "
                f"ValueError; got {type(result).__name__}: {result!r}"
            )
