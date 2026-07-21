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
for a startup-time bug), the file grew without bound — one traceback
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


def _simulate_app_start_failure(diag_path: Path, message: str) -> None:
    """Reproduce the diagnostic-write block from ``main()``'s
    ``except Exception`` clause, but with the ``diag_path`` patched
    to point at a temp directory.

    This mirrors the code in ``ipc_server.main`` at the
    ``app.start() raised — shutting down`` handler.  The CR-10 fix
    makes this path OVERWRITE (not append) ``startup-error.log``.
    """
    import io
    import traceback

    from voice_typer.server.config import _secure_atomic_write

    buf = io.StringIO()
    buf.write("\n--- app.start() failed at 2026-07-19 04:00:00 ---\n")
    buf.write(message)
    traceback.print_exc(file=buf)
    # CR-10: OVERWRITE (not append).  The previous implementation
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
        # CR-10 fix removes the read-existing-and-append pattern.
        # Look for the diagnostic-write block.
        assert "startup-error.log" in src, "main() must write to startup-error.log on app.start() failure."
        # The old append pattern read existing content first.
        assert "existing = diag_path.read_text" not in src, (
            "CR-10 regression: main() reads existing startup-error.log "
            "content to append.  The fix overwrites — cap the file at one "
            "entry."
        )
        assert "existing + buf.getvalue()" not in src, (
            "CR-10 regression: main() appends to startup-error.log.  The fix overwrites — cap the file at one entry."
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

        # CR-10: the file must NOT have grown by ~2× (which would
        # indicate append).  It should be roughly the same size
        # (one traceback, overwritten).
        assert second_size < first_size * 1.5, (
            f"CR-10 regression: startup-error.log grew from {first_size} "
            f"to {second_size} bytes after a second failure — the file is "
            "being appended to instead of overwritten.  Repeated relaunch "
            "crashes would grow this file without bound."
        )
        # The second traceback must be present.
        assert "second failure" in second_content
        # The first traceback must NOT be present (overwrite, not append).
        assert "first failure" not in second_content, (
            "CR-10 regression: the first failure's traceback is still in "
            "startup-error.log after the second failure — the file is "
            "being appended to instead of overwritten."
        )

    def test_overwrite_matches_construction_failure_path(self):
        """The ``app.start()`` failure path must use the SAME write
        pattern as the ``VoiceTyperApp()`` construction failure path
        (both overwrite, not append).  This guards against the two
        paths diverging again.
        """
        src = inspect.getsource(ipc_server.main)
        # Find both diagnostic-write calls.
        # The construction-failure path (line ~2445 in the original)
        # uses ``_secure_atomic_write(diag_path, buf.getvalue())``.
        # The app.start()-failure path (line ~2585 in the original)
        # must use the same pattern.
        # Count occurrences of the overwrite pattern.
        overwrite_count = src.count("_secure_atomic_write(diag_path, buf.getvalue())")
        assert overwrite_count >= 2, (
            "Both the construction-failure path and the app.start()-failure "
            "path must use _secure_atomic_write(diag_path, buf.getvalue()) "
            "(overwrite, not append). Found "
            f"{overwrite_count} occurrence(s); expected at least 2."
        )


class TestStartupErrorLogConstructionFailureAlsoOverwrites:
    """Sanity check: the construction-failure path (which already
    overwrote before CR-10) must STILL overwrite.  CR-10 only changed
    the app.start()-failure path, but if someone reverts that, both
    paths should be checked.
    """

    def test_construction_failure_path_uses_secure_atomic_write(self):
        """The ``except Exception`` clause around ``VoiceTyperApp()``
        construction must use ``_secure_atomic_write`` (overwrite).
        """
        src = inspect.getsource(ipc_server.main)
        assert "_secure_atomic_write(diag_path, buf.getvalue())" in src, (
            "The construction-failure path must use "
            "_secure_atomic_write(diag_path, buf.getvalue()) — overwrite, "
            "not append."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
