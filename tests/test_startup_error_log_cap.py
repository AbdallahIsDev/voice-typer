"""regression tests: ``startup-error.log`` is overwritten, not appended."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

# Module-wide diagnostic-helper call count for the REAL entrypoint module
from voice_typer.server.ipc import entrypoint as _entrypoint_module

_ENTRYPOINT_PATH = Path(_entrypoint_module.__file__).resolve()
_ENTRYPOINT_MODULE_DIAGNOSTIC_CALLS = _ENTRYPOINT_PATH.read_text(encoding="utf-8").count("write_startup_diagnostic(")


def _simulate_app_start_failure(diag_path: Path, message: str) -> None:
    """Reproduce the diagnostic-write block from ``main()``'s"""
    import io
    import traceback

    from voice_typer.server.config import _secure_atomic_write

    buf = io.StringIO()
    buf.write("\n--- app.start() failed at 2026-07-19 04:00:00 ---\n")
    buf.write(message)
    traceback.print_exc(file=buf)
    # OVERWRITE (not append).  The previous implementation
    _secure_atomic_write(diag_path, buf.getvalue())


class TestStartupErrorLogOverwrite:
    """``startup-error.log`` must be overwritten, not appended."""

    def test_main_source_overwrites_not_appends(self):
        """The live ``app.start()`` failure path must not read existing content."""
        # ``app.start()`` runs on the ws startup thread (main() hands the tray
        # loop to it), so the app.start()-failure diagnostic lives there.
        src = inspect.getsource(_entrypoint_module._ws_startup_thread_main)
        assert 'write_startup_diagnostic("ws app.start()")' in src, (
            "the ws startup thread must route the app.start()-failure diagnostic through "
            'write_startup_diagnostic("ws app.start()") (EC-8 shared helper, overwrite, not append).'
        )
        assert "existing = diag_path.read_text" not in src, (
            "CR-10 regression: the app.start() failure path reads existing "
            "startup-error.log content to append.  The fix overwrites, cap the file at one entry."
        )
        assert "existing + buf.getvalue()" not in src, (
            "CR-10 regression: the app.start() failure path appends to startup-error.log.  "
            "The fix overwrites, cap the file at one entry."
        )

    def test_repeated_failures_do_not_grow_file(self, tmp_path, monkeypatch):
        """``startup-error.log`` file must contain ONE traceback after"""
        diag_path = tmp_path / "startup-error.log"

        # Simulate first failure.
        try:
            raise RuntimeError("first failure")
        except RuntimeError:
            _simulate_app_start_failure(diag_path, "first")

        assert diag_path.exists()
        first_size = diag_path.stat().st_size
        first_content = diag_path.read_text(encoding="utf-8")
        assert "first failure" in first_content

        # Simulate second failure (same crash on relaunch).
        try:
            raise RuntimeError("second failure")
        except RuntimeError:
            _simulate_app_start_failure(diag_path, "second")

        second_size = diag_path.stat().st_size
        second_content = diag_path.read_text(encoding="utf-8")

        # the file must NOT have grown by ~2× (which would
        assert second_size < first_size * 1.5, (
            f"CR-10 regression: startup-error.log grew from {first_size} "
            f"to {second_size} bytes after a second failure, the file is "
            "being appended to instead of overwritten.  Repeated relaunch "
            "crashes would grow this file without bound."
        )
        # The second traceback must be present.
        assert "second failure" in second_content
        # The first traceback must NOT be present (overwrite, not append).
        assert "first failure" not in second_content, (
            "CR-10 regression: the first failure's traceback is still in "
            "startup-error.log after the second failure, the file is "
            "being appended to instead of overwritten."
        )

    def test_overwrite_matches_construction_failure_path(self):
        """The ``app.start()`` failure path must use the SAME diagnostic helper."""
        ws_src = inspect.getsource(_entrypoint_module._ws_startup_thread_main)
        assert 'write_startup_diagnostic("ws app.start()")' in ws_src, (
            'the ws app.start()-failure path must call write_startup_diagnostic("ws app.start()") (EC-8 shared helper).'
        )
        main_src = inspect.getsource(_entrypoint_module.main)
        assert "_construct_app_with_diagnostics()" in main_src, (
            "main() must delegate VoiceTyperApp construction to the shared "
            "_construct_app_with_diagnostics helper (construction-failure "
            "diagnostics live there, EC-8 single source of truth, shared by "
            "both launch orders)."
        )
        assert _ENTRYPOINT_MODULE_DIAGNOSTIC_CALLS >= 2, (
            "The entrypoint module must route ALL startup-failure "
            "diagnostics through write_startup_diagnostic(...) (EC-8): "
            "one construction call (the shared helper) + one for the "
            "ws-startup thread's app.start()-failure site. "
            f"Found {_ENTRYPOINT_MODULE_DIAGNOSTIC_CALLS} occurrence(s)."
        )


class TestStartupErrorLogConstructionFailureAlsoOverwrites:
    """Sanity check: the construction-failure path (which already"""

    def test_construction_failure_path_uses_secure_atomic_write(self):
        """The ``except Exception`` clause around ``VoiceTyperApp()``"""
        src = inspect.getsource(_entrypoint_module._construct_app_with_diagnostics)
        assert "write_startup_diagnostic(" in src, (
            "The construction-failure path must call "
            "write_startup_diagnostic(...) (EC-8 shared helper), "
            "overwrite, not append."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
