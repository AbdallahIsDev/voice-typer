"""Deduplicated config warnings + atomic log-file writes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from voice_typer.server.config import Config


@pytest.fixture
def isolated_config_dir(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at an empty tmp_path for the test."""
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    return tmp_config_dir


def _reset_validate_config_dedupe() -> set[str]:
    """Clear the ``validate_config`` log-dedupe set; return a snapshot to restore."""
    from voice_typer.server.config import loader as loader_mod

    snapshot = set(loader_mod._validate_config_warnings)
    loader_mod._validate_config_warnings.clear()
    return snapshot


def _restore_validate_config_dedupe(snapshot: set[str]) -> None:
    from voice_typer.server.config import loader as loader_mod

    loader_mod._validate_config_warnings.clear()
    loader_mod._validate_config_warnings.update(snapshot)


class TestValidateConfigWarningDedupe:
    """Repeated ``Config.load()`` logs each distinct error once, keeps surfacing it."""

    def test_repeated_loads_log_each_error_once(
        self, isolated_config_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        snapshot = _reset_validate_config_dedupe()
        try:
            config_file = isolated_config_dir / "config.json"
            config_file.write_text(
                json.dumps({"schema_version": 3, "asr_backend": "whisper", "language": "invalid_lang_xx"}),
                encoding="utf-8",
            )
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
                first = Config.load()
                second = Config.load()
                third = Config.load()
            logged = [r.getMessage() for r in caplog.records if "validate_config:" in r.getMessage()]
            assert logged, "expected at least one validate_config WARNING to be logged"
            assert len(logged) == len(set(logged)), f"duplicate validate_config lines across loads: {logged!r}"
            # Every load still surfaces the warning to the renderer.
            for instance in (first, second, third):
                warnings = [w for w in (instance.last_load_warnings or []) if "validate_config:" in w]
                assert warnings, "each load must populate last_load_warnings even when the log line is deduped"
        finally:
            _restore_validate_config_dedupe(snapshot)


def _make_handler(log_file: Path, *, max_bytes: int = 1024 * 1024):  # -> _SecureTruncatingFileHandler
    from voice_typer.server import log as log_pkg

    handler = log_pkg._SecureTruncatingFileHandler(str(log_file), maxBytes=max_bytes, backupCount=0)
    handler.setFormatter(log_pkg._FileFormatter())
    return handler


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.emit",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestEmitInterProcessLock:
    """The file handler writes whole lines, even under contention or re-entry."""

    def test_emit_writes_full_templated_line(self, tmp_path: Path) -> None:
        from voice_typer.server import log as log_pkg  # noqa: F401 (keeps import shape uniform)

        log_file = tmp_path / "emit.log"
        handler = _make_handler(log_file)
        try:
            handler.emit(_make_record("[TEST] atomic line probe"))
            handler.flush()
        finally:
            handler.close()
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, f"expected exactly one line, got: {lines!r}"
        assert lines[0].endswith("[TEST] atomic line probe")

    def test_emit_fail_open_when_lock_contended(self, tmp_path: Path) -> None:
        """A lock held by another writer must not lose or corrupt the record."""
        log_file = tmp_path / "contended.log"
        handler = _make_handler(log_file)
        held = handler._try_acquire_emit_lock()
        assert held is not None, "could not acquire the emit lock for the contention probe"
        try:
            handler.emit(_make_record("[TEST] contended write probe"))
            handler.flush()
        finally:
            handler._release_rotation_lock(held)
            handler.close()
        content = log_file.read_text(encoding="utf-8")
        assert "[TEST] contended write probe" in content
        # Every line must carry the full C-LOG-1 template (timestamp +
        import re

        template = re.compile(r"^\d{4}-\d{2}-\d{2}  \d{2}:\d{2}:\d{2}  (DEBUG|INFO|WARN |ERROR) ")
        for line in content.splitlines():
            assert template.match(line), f"log line lost its template (partial write?): {line!r}"

    def test_emit_reentrant_logging_does_not_deadlock(self, tmp_path: Path) -> None:
        """Logging from inside the emit path writes fail-open without hanging."""
        from voice_typer.server import log as log_pkg

        log_file = tmp_path / "reentrant.log"
        handler = _make_handler(log_file)
        log_pkg._emit_reentrancy.active = True
        try:
            handler.emit(_make_record("[TEST] re-entrant probe"))
            handler.flush()
        finally:
            log_pkg._emit_reentrancy.active = False
            handler.close()
        assert "[TEST] re-entrant probe" in log_file.read_text(encoding="utf-8")

    def test_rotation_still_truncates_in_place(self, tmp_path: Path) -> None:
        """The emit-time lock must not disable the single-file truncation policy."""
        log_file = tmp_path / "rotate.log"
        handler = _make_handler(log_file, max_bytes=256)
        try:
            for i in range(30):
                handler.emit(_make_record(f"[TEST] rotation filler line {i:02d} " + "x" * 60))
            handler.flush()
        finally:
            handler.close()
        content = log_file.read_text(encoding="utf-8")
        assert len(content) < 30 * 80, "rotation never fired; file grew unbounded"
        assert "[TEST] rotation filler line 29" in content, "latest record must survive rotation"
