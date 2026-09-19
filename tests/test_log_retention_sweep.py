"""Tests for ``_sweep_stale_logs``: the three-tier log cleanup design."""

from __future__ import annotations

import os
import time
from pathlib import Path

from voice_typer.server import log as vt_log
from voice_typer.server._log_constants import (
    LOG_MAX_BYTES,
    LOG_SIZE_FALLBACK_BYTES,
)


def _logs_dir(tmp_path: Path) -> Path:
    """Create (and return) the ``logs/`` subdir under ``tmp_path`` (O1)."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def _set_mtime_days_ago(path: Path, days: float) -> None:
    """Set a file's mtime to ``days`` days in the past."""
    target = time.time() - days * 24 * 60 * 60
    os.utime(path, (target, target))


def test_sweeps_old_log_files(tmp_path: Path) -> None:
    """Log files older than the 7-day retention are deleted."""
    old_rotation = _logs_dir(tmp_path) / "voice-typer.log.1"
    old_rotation.write_text("ancient log", encoding="utf-8")
    _set_mtime_days_ago(old_rotation, days=8)

    vt_log._sweep_stale_logs(tmp_path)

    assert not old_rotation.exists(), "old log file should be swept"


def test_keeps_recent_log_files(tmp_path: Path) -> None:
    """Log files newer than the retention are kept."""
    recent_rotation = _logs_dir(tmp_path) / "voice-typer.log.1"
    recent_rotation.write_text("recent log", encoding="utf-8")
    _set_mtime_days_ago(recent_rotation, days=5)

    vt_log._sweep_stale_logs(tmp_path)

    assert recent_rotation.exists(), "recent log file should be kept"
    assert recent_rotation.read_text(encoding="utf-8") == "recent log"


def test_deletes_stale_active_log(tmp_path: Path) -> None:
    """retention, the sweep runs before the handler opens it, so a stale"""
    active = _logs_dir(tmp_path) / "voice-typer.log"
    active.write_text("stale session", encoding="utf-8")
    _set_mtime_days_ago(active, days=8)

    vt_log._sweep_stale_logs(tmp_path)

    assert not active.exists(), "stale active log must be swept (starts fresh)"


def test_keeps_fresh_active_log(tmp_path: Path) -> None:
    """A recently-written active log survives the sweep."""
    active = _logs_dir(tmp_path) / "voice-typer.log"
    active.write_text("current session", encoding="utf-8")
    _set_mtime_days_ago(active, days=1)

    vt_log._sweep_stale_logs(tmp_path)

    assert active.exists()
    assert active.read_text(encoding="utf-8") == "current session"


def test_size_fallback_deletes_oversized_fresh_file(tmp_path: Path) -> None:
    """Tier 2: a freshly-written file larger than the size fallback is"""
    oversized = _logs_dir(tmp_path) / "voice-typer.log"
    oversized.write_bytes(b"x" * (LOG_SIZE_FALLBACK_BYTES + 1))

    vt_log._sweep_stale_logs(tmp_path)

    assert not oversized.exists(), "oversized file must be deleted by the Tier-2 size fallback even when fresh"


def test_size_fallback_keeps_file_under_cap(tmp_path: Path) -> None:
    """A file between the fallback and the ceiling but under the"""
    under = _logs_dir(tmp_path) / "voice-typer.log"
    under.write_bytes(b"x" * (LOG_SIZE_FALLBACK_BYTES - 1024))

    vt_log._sweep_stale_logs(tmp_path)

    assert under.exists(), "file under the size fallback must be kept"


def test_ceiling_above_fallback_invariant() -> None:
    """Ordering invariant: the mid-session hard ceiling (40 MB) must be"""
    assert LOG_MAX_BYTES > LOG_SIZE_FALLBACK_BYTES, "Tier-3 ceiling must exceed the Tier-2 fallback"


def test_does_not_delete_lock_file(tmp_path: Path) -> None:
    """The inter-process truncation lock file is never deleted."""
    lock_file = _logs_dir(tmp_path) / "voice-typer.log.lock"
    lock_file.write_text("", encoding="utf-8")
    _set_mtime_days_ago(lock_file, days=365)  # very old, should still survive

    vt_log._sweep_stale_logs(tmp_path)

    assert lock_file.exists(), "lock file must never be swept"


def test_mixed_ages_only_deletes_old(tmp_path: Path) -> None:
    """A mix of old and recent log files: only old ones are swept."""
    old1 = _logs_dir(tmp_path) / "voice-typer.log.1"
    old1.write_text("old1", encoding="utf-8")
    _set_mtime_days_ago(old1, days=30)

    recent2 = _logs_dir(tmp_path) / "voice-typer.log.2"
    recent2.write_text("recent2", encoding="utf-8")
    _set_mtime_days_ago(recent2, days=3)

    old3 = _logs_dir(tmp_path) / "legacy-host.log"
    old3.write_text("old3", encoding="utf-8")
    _set_mtime_days_ago(old3, days=8)

    vt_log._sweep_stale_logs(tmp_path)

    assert not old1.exists()
    assert not old3.exists()
    assert recent2.exists()
    assert recent2.read_text(encoding="utf-8") == "recent2"


def test_missing_dir_is_noop(tmp_path: Path) -> None:
    """A non-existent config dir is a no-op (no crash)."""
    missing = tmp_path / "does-not-exist"
    # Must not raise.
    vt_log._sweep_stale_logs(missing)


def test_empty_dir_is_noop(tmp_path: Path) -> None:
    """An empty config dir is a no-op (no crash)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    # Must not raise and must not delete the dir itself.
    vt_log._sweep_stale_logs(empty)
    assert empty.is_dir()


def test_setup_logging_invokes_sweep(tmp_path: Path, monkeypatch) -> None:
    """``setup_logging`` calls ``_sweep_stale_logs`` once at startup,"""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()

    swept_paths: list[Path] = []
    original = vt_log._sweep_stale_logs

    def _spy(config_dir: Path) -> None:
        swept_paths.append(config_dir)
        original(config_dir)

    # Plant an old log file to verify the sweep runs through setup_logging.
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "logs").mkdir()
    old_log = config_dir / "logs" / "voice-typer.log.1"
    old_log.write_text("ancient", encoding="utf-8")
    _set_mtime_days_ago(old_log, days=8)

    monkeypatch.setattr(vt_log, "_sweep_stale_logs", _spy)

    try:
        vt_log.setup_logging(config_dir)
        assert len(swept_paths) == 1
        assert swept_paths[0] == config_dir
        assert not old_log.exists(), "setup_logging should have swept the old log"
    finally:
        vt_log.reset()


def test_sweep_runs_before_handler_opens_log(tmp_path: Path, monkeypatch) -> None:
    """The sweep must run BEFORE the rotating file handler opens"""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    vt_log.reset()

    events: list[str] = []
    original_sweep = vt_log._sweep_stale_logs
    original_handler = vt_log._SecureTruncatingFileHandler

    def _spy_sweep(config_dir: Path) -> None:
        events.append("sweep")
        original_sweep(config_dir)

    class _SpyHandler(original_handler):
        def __init__(self, *args, **kwargs):
            events.append("handler")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(vt_log, "_sweep_stale_logs", _spy_sweep)
    monkeypatch.setattr(vt_log, "_SecureTruncatingFileHandler", _SpyHandler)

    config_dir = tmp_path / "cfg"
    try:
        vt_log.setup_logging(config_dir)
        assert events == ["sweep", "handler"], f"sweep must run before the handler opens the log file; got {events}"
    finally:
        vt_log.reset()
