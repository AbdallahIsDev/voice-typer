"""XZ-PII-07: tests for ``_sweep_stale_log_rotations``.

The size-based ``RotatingFileHandler`` (5 MiB × 5) bounds the count of
in-use rotation files but does NOT delete rotation files left over from
long-idle periods (user on vacation, app not launched). Dictated text
often appears in transcribed preview snippets inside log records, so
lingering rotation files are a PII hygiene concern.

``_sweep_stale_log_rotations`` mirrors
``crash_handler._diagnostics_archive._sweep_stale_diagnostics``: it
deletes ``voice-typer.log.*`` rotation files older than 30 days at the
start of ``setup_logging``. These tests pin:

1. Files older than the cutoff are deleted.
2. Files newer than the cutoff are kept.
3. The active ``voice-typer.log`` is NEVER deleted (the glob requires
   a trailing ``.<x>`` segment).
4. The inter-process rotation lock file
   (``voice-typer.log.rotate.lock``) is NEVER deleted.
5. ``setup_logging`` invokes the sweep (integration check).
6. A missing/empty config dir is a no-op (no crash).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from voice_typer.server import log as vt_log


def _logs_dir(tmp_path: Path) -> Path:
    """Create (and return) the ``logs/`` subdir under ``tmp_path`` (O1)."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def _set_mtime_days_ago(path: Path, days: float) -> None:
    """Set a file's mtime to ``days`` days in the past."""
    target = time.time() - days * 24 * 60 * 60
    os.utime(path, (target, target))


def test_sweeps_old_rotation_files(tmp_path: Path) -> None:
    """Rotation files older than 30 days are deleted."""
    old_rotation = _logs_dir(tmp_path) / "voice-typer.log.1"
    old_rotation.write_text("ancient log", encoding="utf-8")
    _set_mtime_days_ago(old_rotation, days=45)

    vt_log._sweep_stale_log_rotations(tmp_path)

    assert not old_rotation.exists(), "old rotation file should be swept"


def test_keeps_recent_rotation_files(tmp_path: Path) -> None:
    """Rotation files newer than 30 days are kept."""
    recent_rotation = _logs_dir(tmp_path) / "voice-typer.log.1"
    recent_rotation.write_text("recent log", encoding="utf-8")
    _set_mtime_days_ago(recent_rotation, days=5)

    vt_log._sweep_stale_log_rotations(tmp_path)

    assert recent_rotation.exists(), "recent rotation file should be kept"
    assert recent_rotation.read_text(encoding="utf-8") == "recent log"


def test_does_not_delete_active_log(tmp_path: Path) -> None:
    """The active ``voice-typer.log`` (no .N suffix) is never deleted."""
    active = _logs_dir(tmp_path) / "voice-typer.log"
    active.write_text("active log", encoding="utf-8")
    _set_mtime_days_ago(active, days=365)  # very old — should still survive

    vt_log._sweep_stale_log_rotations(tmp_path)

    assert active.exists(), "active log file must never be swept"
    assert active.read_text(encoding="utf-8") == "active log"


def test_does_not_delete_rotation_lock_file(tmp_path: Path) -> None:
    """The inter-process rotation lock file is never deleted."""
    lock_file = _logs_dir(tmp_path) / "voice-typer.log.rotate.lock"
    lock_file.write_text("", encoding="utf-8")
    _set_mtime_days_ago(lock_file, days=365)  # very old — should still survive

    vt_log._sweep_stale_log_rotations(tmp_path)

    assert lock_file.exists(), "rotation lock file must never be swept"


def test_mixed_ages_only_deletes_old(tmp_path: Path) -> None:
    """A mix of old and recent rotation files: only old ones are swept."""
    old1 = _logs_dir(tmp_path) / "voice-typer.log.1"
    old1.write_text("old1", encoding="utf-8")
    _set_mtime_days_ago(old1, days=60)

    recent2 = _logs_dir(tmp_path) / "voice-typer.log.2"
    recent2.write_text("recent2", encoding="utf-8")
    _set_mtime_days_ago(recent2, days=10)

    old3 = _logs_dir(tmp_path) / "voice-typer.log.3"
    old3.write_text("old3", encoding="utf-8")
    _set_mtime_days_ago(old3, days=31)

    vt_log._sweep_stale_log_rotations(tmp_path)

    assert not old1.exists()
    assert not old3.exists()
    assert recent2.exists()
    assert recent2.read_text(encoding="utf-8") == "recent2"


def test_missing_dir_is_noop(tmp_path: Path) -> None:
    """A non-existent config dir is a no-op (no crash)."""
    missing = tmp_path / "does-not-exist"
    # Must not raise.
    vt_log._sweep_stale_log_rotations(missing)


def test_empty_dir_is_noop(tmp_path: Path) -> None:
    """An empty config dir is a no-op (no crash)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    # Must not raise and must not delete the dir itself.
    vt_log._sweep_stale_log_rotations(empty)
    assert empty.is_dir()


def test_setup_logging_invokes_sweep(tmp_path: Path, monkeypatch) -> None:
    """``setup_logging`` calls ``_sweep_stale_log_rotations`` once at startup."""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()

    swept_paths: list[Path] = []
    original = vt_log._sweep_stale_log_rotations

    def _spy(config_dir: Path) -> None:
        swept_paths.append(config_dir)
        original(config_dir)

    # Plant an old rotation file to verify the sweep runs through setup_logging.
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "logs").mkdir()
    old_rotation = config_dir / "logs" / "voice-typer.log.1"
    old_rotation.write_text("ancient", encoding="utf-8")
    _set_mtime_days_ago(old_rotation, days=45)

    monkeypatch.setattr(vt_log, "_sweep_stale_log_rotations", _spy)

    try:
        vt_log.setup_logging(config_dir)
        assert len(swept_paths) == 1
        assert swept_paths[0] == config_dir
        assert not old_rotation.exists(), "setup_logging should have swept the old rotation"
    finally:
        vt_log.reset()
