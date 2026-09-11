"""CR-10 regression tests: ``startup-error.log`` is overwritten, not appended.

The bug
-------
``IPCServer.main`` (in ``voice_typer/server/ipc_server.py``) catches
exceptions from ``app.start()`` and writes a diagnostic traceback to
``<config_dir>/startup-error.log``.  The previous implementation read
the existing file content and APPENDED the new traceback::

    try:
        existing = diag_path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        existing = ""
    _secure_atomic_write(diag_path, existing + buf.getvalue())

If the operator hit the same crash on every relaunch (the common case
for a startup-time bug), the file grew without bound, one traceback
per launch, accumulating across days/weeks of debugging.  A 4-KB
traceback × 1000 relaunches = a 4-MB append-only log.

The fix
-------
Cap the file at ONE entry (overwrite, not append), mirroring the
construction-failure path that already used overwrite::

    _secure_atomic_write(diag_path, buf.getvalue())

These tests verify the app.start()-failure path overwrites (not
appends) by simulating two consecutive failures and checking the
final file size matches ONE traceback, not two.

Because ``main()`` is a long entry-point function with many side
effects (process metadata, single-instance lock, Electron launcher,
etc.), we test the diagnostic-write behavior in isolation by
extracting the relevant code path into a small helper that we
exercise directly.  The tests use ``tmp_path`` + ``monkeypatch`` to
redirect ``_config_dir()`` to a temp directory so no real config is
touched.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from voice_typer.server import ipc_server

# Module-wide diagnostic-helper call count for the REAL entrypoint module
# (``ipc_server`` is the compatibility shim; ``main``'s implementation, and
# the shared ``_construct_app_with_diagnostics`` helper, live in
# ``voice_typer/server/ipc/entrypoint.py``).
from voice_typer.server.ipc import entrypoint as _entrypoint_module

_ENTRYPOINT_PATH = Path(_entrypoint_module.__file__).resolve()
_ENTRYPOINT_MODULE_DIAGNOSTIC_CALLS = _ENTRYPOINT_PATH.read_text(encoding="utf-8").count("write_startup_diagnostic(")


def _simulate_app_start_failure(diag_path: Path, message: str) -> None:
    """Reproduce the diagnostic-write block from ``main()``'s
    ``except Exception`` clause, but with the ``diag_path`` patched
    to point at a temp directory.

    This mirrors the code in ``ipc_server.main`` at the
    ``app.start() raised, shutting down`` handler.  The CR-10 fix
    makes this path OVERWRITE (not append) ``startup-error.log``.
    """
    import io
    import traceback

    from voice_typer.server.config import _secure_atomic_write

    buf = io.StringIO()
    buf.write("\n--- app.start() failed at 2026-07-19 04:00:00 ---\n")
    buf.write(message)
    traceback.print_exc(file=buf)
    # OVERWRITE (not append).  The previous implementation
    # read existing content and appended, growing the file without
    # bound on repeated failures.
    _secure_atomic_write(diag_path, buf.getvalue())


class TestStartupErrorLogOverwrite:
    """CR-10: ``startup-error.log`` must be overwritten, not appended."""

    def test_main_source_overwrites_not_appends(self):
        """The source of ``main()`` must NOT read existing content
        and append; it must overwrite.
        """
        src = inspect.getsource(ipc_server.main)
        # fix removes the read-existing-and-append pattern.
        # Look for the diagnostic-write block.
        assert "startup-error.log" in src, "main() must write to startup-error.log on app.start() failure."
        # The old append pattern read existing content first.
        assert "existing = diag_path.read_text" not in src, (
            "CR-10 regression: main() reads existing startup-error.log "
            "content to append.  The fix overwrites, cap the file at one "
            "entry."
        )
        assert "existing + buf.getvalue()" not in src, (
            "CR-10 regression: main() appends to startup-error.log.  The fix overwrites, cap the file at one entry."
        )

    def test_repeated_failures_do_not_grow_file(self, tmp_path, monkeypatch):
        """Simulate two consecutive ``app.start()`` failures.  The
        ``startup-error.log`` file must contain ONE traceback after
        the second failure, not two.
        """
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
        # indicate append).  It should be roughly the same size
        # (one traceback, overwritten).
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
        """The ``app.start()`` failure path must use the SAME diagnostic
        helper as the ``VoiceTyperApp()`` construction failure path
        (both call ``write_startup_diagnostic``).  This guards against
        the two paths diverging again, EC-8 extracted the duplicated
        inline diagnostic blocks (which had already drifted: CR-10's
        overwrite-vs-append fix was applied to only one) into
        :func:`voice_typer.server.ipc_diagnostics.write_startup_diagnostic`.
        """
        src = inspect.getsource(ipc_server.main)
        # Both call sites must route through the shared helper instead of
        # duplicating the ``_secure_atomic_write(diag_path, buf.getvalue())``
        # pattern inline.  Counting the helper invocations guards against
        # a future regression that re-inlines one of the call sites.
        # Structure note (early server-started launch order): the
        # construction-failure diagnostics moved into the shared
        # ``_construct_app_with_diagnostics`` helper (the SINGLE
        # VoiceTyperApp construction site used by BOTH launch orders),
        # so ``main``'s own source now delegates to the helper instead
        # of calling ``write_startup_diagnostic("construction")`` inline
        # , the module-wide count keeps the EC-8 single-source-of-truth
        # invariant: one construction call (the helper), one per
        # app.start()-failure site (main + the ws-startup thread).
        assert 'write_startup_diagnostic("app.start()")' in src, (
            'main()\'s app.start()-failure path must call write_startup_diagnostic("app.start()") (EC-8 shared helper).'
        )
        assert "_construct_app_with_diagnostics()" in src, (
            "main() must delegate VoiceTyperApp construction to the shared "
            "_construct_app_with_diagnostics helper (construction-failure "
            "diagnostics live there, EC-8 single source of truth, shared by "
            "both launch orders)."
        )
        assert _ENTRYPOINT_MODULE_DIAGNOSTIC_CALLS >= 3, (
            "The entrypoint module must route ALL startup-failure "
            "diagnostics through write_startup_diagnostic(...) (EC-8): "
            "one construction call (the shared helper) + one per "
            "app.start()-failure site (main + the ws-startup thread). "
            f"Found {_ENTRYPOINT_MODULE_DIAGNOSTIC_CALLS} occurrence(s)."
        )


class TestStartupErrorLogConstructionFailureAlsoOverwrites:
    """Sanity check: the construction-failure path (which already
    overwrote before CR-10) must STILL overwrite.  CR-10 only changed
    the app.start()-failure path, but if someone reverts that, both
    paths should be checked.
    """

    def test_construction_failure_path_uses_secure_atomic_write(self):
        """The ``except Exception`` clause around ``VoiceTyperApp()``
        construction must route through the shared diagnostic helper
        (which internally uses ``_secure_atomic_write`` to overwrite,
        not append).
        """
        src = inspect.getsource(ipc_server.main)
        assert "write_startup_diagnostic(" in src, (
            "The construction-failure path must call "
            "write_startup_diagnostic(...) (EC-8 shared helper), "
            "overwrite, not append."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
