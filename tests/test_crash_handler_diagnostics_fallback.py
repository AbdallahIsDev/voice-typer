"""FR-7: ``set_crash_handler_config_dir`` must not silently disable the
SEH/VEH crash diagnostics when the archive dir cannot be created.

Pre-fix, ``set_crash_handler_config_dir`` wrapped
``archive_dir.mkdir(...)`` in ``contextlib.suppress(Exception)`` and
ALWAYS set ``_crash_file_path`` to the archive-subdir path regardless
of whether mkdir succeeded. On a read-only config_dir the mkdir fails
silently, and the VEH callback's ``_write_to_file`` gets
``CreateFileW`` -> ``INVALID_HANDLE_VALUE`` -> silent return — so the
SEH crash handler was dead even though the Python excepthook (which
writes to the config_dir root) kept working.

Fix: after the mkdir attempt, verify the archive dir actually exists;
if it does not, fall back to writing the crash file to the config_dir
root (the same root the Python excepthook uses) so VEH diagnostics are
never lost, and log a WARNING naming the failure + the fallback. The
failure stays non-fatal (never raises from the config-dir setter).

These tests cover:
  - mkdir fails -> fallback path set + WARNING logged
  - mkdir succeeds -> archive-subdir path used
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from voice_typer.server import crash_handler

_UNSET = object()


@pytest.fixture(autouse=True)
def _reset_crash_handler_module_state() -> None:
    """Reset the facade module globals so the cached path doesn't leak
    between tests (mirrors the autouse fixture in ``test_crash_handler.py``)."""
    keys = (
        "_crash_file_path",
        "_PID",
        "_crash_written",
        "_python_crash_dir",
        "_crash_header_bytes",
    )
    saved = {k: getattr(crash_handler, k, _UNSET) for k in keys}
    crash_handler._crash_file_path = ""
    crash_handler._PID = 0
    crash_handler._crash_written = False
    crash_handler._python_crash_dir = None
    crash_handler._crash_header_bytes = b""
    yield
    for k, v in saved.items():
        if v is _UNSET:
            if hasattr(crash_handler, k):
                delattr(crash_handler, k)
        else:
            setattr(crash_handler, k, v)


def _failing_archive_mkdir(self: Path, *args, **kwargs):
    """``Path.mkdir`` stub that fails ONLY for the crash_diagnostics dir.

    ``set_crash_handler_config_dir`` creates the archive dir with
    ``mkdir(parents=True, exist_ok=True)``. Raising for that exact path
    simulates a read-only config_dir; every other ``Path.mkdir`` call
    (e.g. by the memory-buffer install) passes through unchanged.
    """
    if self.name == "crash_diagnostics":
        raise PermissionError("read-only filesystem")
    return Path.mkdir(self, *args, **kwargs)


def test_mkdir_failure_falls_back_to_config_root(tmp_path, monkeypatch, caplog) -> None:
    """FR-7: when the archive-dir mkdir fails, ``_crash_file_path`` falls
    back to the config_dir root (so VEH diagnostics are not lost) and a
    WARNING names the failure + the fallback."""
    monkeypatch.setattr(Path, "mkdir", _failing_archive_mkdir)

    with caplog.at_level(logging.WARNING, logger="voice_typer.server.crash_handler._diagnostics_archive"):
        # Must NOT raise — the config-dir setter stays non-fatal.
        crash_handler.set_crash_handler_config_dir(tmp_path)

    config_root = tmp_path.resolve()
    archive_dir = tmp_path / "crash_diagnostics"
    assert not archive_dir.exists(), "FR-7: the archive dir must NOT exist when mkdir failed"
    assert crash_handler._python_crash_dir == config_root

    path_no_nul = crash_handler._crash_file_path.rstrip("\0")
    assert path_no_nul, "FR-7: _crash_file_path must be non-empty on the fallback path"
    assert Path(path_no_nul).parent == config_root, (
        f"FR-7: fallback crash file path must sit in the config root "
        f"(got parent {Path(path_no_nul).parent!r}, expected {config_root!r})"
    )
    assert str(Path(path_no_nul).parent) != str(archive_dir), (
        "FR-7: the fallback must NOT point into the (uncreatable) archive dir"
    )

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("falling back to the config root" in m for m in warning_msgs), (
        f"FR-7: a WARNING naming the fallback must be logged; got: {warning_msgs}"
    )
    assert any("read-only filesystem" in m for m in warning_msgs), (
        f"FR-7: the WARNING must name the mkdir failure; got: {warning_msgs}"
    )


def test_mkdir_success_uses_archive_path(tmp_path) -> None:
    """FR-7: when mkdir succeeds, the archive-subdir path is used (no
    fallback, no WARNING) — preserving the existing behavior."""
    crash_handler.set_crash_handler_config_dir(tmp_path)

    archive_dir = tmp_path / "crash_diagnostics"
    assert archive_dir.is_dir(), "archive dir must be pre-created when mkdir succeeds"

    path_no_nul = crash_handler._crash_file_path.rstrip("\0")
    assert path_no_nul, "_crash_file_path must be non-empty"
    assert Path(path_no_nul).parent == archive_dir.resolve(), (
        f"VEH crash file path must be inside crash_diagnostics/ (was {path_no_nul!r})"
    )
