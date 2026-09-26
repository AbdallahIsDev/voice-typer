"""``voice_typer/server/log.py``."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

import pytest
from voice_typer.server.log import (
    _BubbleLevelExclusionFilter,
    _ColorFormatter,
    _FileFormatter,
    _FlushingStreamHandler,
    _JsonFormatter,
    _module_level_overrides,
    get_module_levels,
    reset,
    set_module_level,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore the ``voice_typer`` logger + override registry."""
    vt_root = logging.getLogger("voice_typer")
    saved_vt_handlers = list(vt_root.handlers)
    saved_vt_filters = list(vt_root.filters)
    saved_vt_level = vt_root.level
    saved_overrides = dict(_module_level_overrides)

    yield

    vt_root.handlers = saved_vt_handlers
    vt_root.filters = saved_vt_filters
    vt_root.setLevel(saved_vt_level)
    _module_level_overrides.clear()
    _module_level_overrides.update(saved_overrides)


def _make_record_with_exc(msg: str = "boom") -> logging.LogRecord:
    """Build a LogRecord carrying a real ``exc_info`` tuple."""
    try:
        raise RuntimeError("intentional test exception")
    except RuntimeError:
        import sys as _sys

        exc_info = _sys.exc_info()
    record = logging.LogRecord(
        name="voice_typer.server.fake",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    record.session_id = "a3f1b2c4"
    record.component = "voice_typer.server.fake"
    return record


def test_file_formatter_appends_traceback() -> None:
    """message body so ``log.exception(...)`` records keep their stack"""
    line = _FileFormatter().format(_make_record_with_exc("outer boom"))
    assert "Traceback (most recent call last)" in line, (
        f"GT-2 regression: traceback missing from file-format output:\n{line!r}"
    )
    assert "RuntimeError: intentional test exception" in line
    assert "outer boom" in line


def test_color_formatter_appends_traceback() -> None:
    """``_ColorFormatter.format`` appends the traceback (plain"""
    line = _ColorFormatter().format(_make_record_with_exc("color boom"))
    assert "Traceback (most recent call last)" in line
    assert "RuntimeError: intentional test exception" in line
    assert "color boom" in line


def test_json_formatter_includes_traceback_field() -> None:
    """``_JsonFormatter.format`` adds a ``traceback`` field so"""
    out = _JsonFormatter().format(_make_record_with_exc("json boom"))
    parsed = json.loads(out)
    assert "traceback" in parsed, f"GT-2 regression: no 'traceback' key in JSON payload: {parsed}"
    assert "Traceback (most recent call last)" in parsed["traceback"]
    assert "RuntimeError: intentional test exception" in parsed["traceback"]


def test_file_formatter_no_traceback_when_no_exc_info() -> None:
    """A record without ``exc_info`` must not produce a traceback block"""
    record = logging.LogRecord(
        name="voice_typer.server.fake",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="nothing to see here",
        args=(),
        exc_info=None,
    )
    record.session_id = "deadbeef"
    line = _FileFormatter().format(record)
    assert "Traceback" not in line
    assert "nothing to see here" in line


def test_exception_log_reaches_file_on_disk(tmp_path: Path) -> None:
    """end-to-end: ``log.exception(...)`` writes a line containing"""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)
    try:
        log = logging.getLogger("voice_typer.server.fake_module")
        try:
            raise ValueError("end-to-end boom")
        except ValueError:
            log.exception("caught during handling")
        for h in logging.getLogger("voice_typer").handlers:
            with __import__("contextlib").suppress(Exception):
                h.flush()
        content = (config_dir / "logs" / "lausu.log").read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" in content, (
            f"GT-2 end-to-end regression: no traceback in log file:\n{content}"
        )
        assert "ValueError: end-to-end boom" in content
    finally:
        reset()


def test_setup_logging_attaches_stream_handler_without_tty(tmp_path: Path, monkeypatch) -> None:
    """even when stderr is NOT a TTY and --port mode is NOT"""

    # Stub isatty() so the code path under test is exercised, pytest
    class _FakeNonTtyStderr:
        def isatty(self) -> bool:
            return False

        def reconfigure(self, **_kwargs) -> None:  # pragma: no cover - best-effort
            pass

    monkeypatch.setattr(sys, "stderr", _FakeNonTtyStderr())
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)
    try:
        root = logging.getLogger("voice_typer")
        stream_handlers = [h for h in root.handlers if isinstance(h, _FlushingStreamHandler)]
        assert stream_handlers, (
            "GT-13 regression: no _FlushingStreamHandler attached when stderr is not a TTY, "
            "Tauri sidecar startup failures would be invisible if the file write failed."
        )
    finally:
        reset()


def test_port_mode_with_redirected_stderr_uses_plain_formatter(tmp_path: Path, monkeypatch) -> None:
    """A ``--port`` run with a NON-TTY stderr (the launcher"""

    class _FakeNonTtyStderr:
        def isatty(self) -> bool:
            return False

        def reconfigure(self, **_kwargs) -> None:  # pragma: no cover - best-effort
            pass

    monkeypatch.setattr(sys, "stderr", _FakeNonTtyStderr())
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir, port_mode=True)
    try:
        root = logging.getLogger("voice_typer")
        stream = next(h for h in root.handlers if isinstance(h, _FlushingStreamHandler))
        assert isinstance(stream.formatter, _FileFormatter), (
            f"port mode + non-TTY stderr must use _FileFormatter (no ANSI), got {type(stream.formatter).__name__}"
        )
    finally:
        reset()


def test_port_mode_with_tty_stderr_keeps_color_formatter(tmp_path: Path, monkeypatch) -> None:
    """A real terminal (TTY stderr) keeps the coloured"""

    class _FakeTtyStderr:
        def isatty(self) -> bool:
            return True

        def reconfigure(self, **_kwargs) -> None:  # pragma: no cover - best-effort
            pass

    monkeypatch.setattr(sys, "stderr", _FakeTtyStderr())
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir, port_mode=True)
    try:
        root = logging.getLogger("voice_typer")
        stream = next(h for h in root.handlers if isinstance(h, _FlushingStreamHandler))
        assert isinstance(stream.formatter, _ColorFormatter), (
            f"TTY stderr must keep _ColorFormatter, got {type(stream.formatter).__name__}"
        )
    finally:
        reset()


def test_setup_logging_no_duplicate_stream_handlers_when_reinvoked(tmp_path: Path, monkeypatch) -> None:
    """must not break idempotency, repeated ``setup_logging``"""
    monkeypatch.delenv("VOICE_TYPER_LOG_JSON", raising=False)
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    try:
        setup_logging(config_dir)
        n1 = sum(1 for h in logging.getLogger("voice_typer").handlers if isinstance(h, _FlushingStreamHandler))
        setup_logging(config_dir)
        n2 = sum(1 for h in logging.getLogger("voice_typer").handlers if isinstance(h, _FlushingStreamHandler))
        assert n1 == 1, f"expected exactly 1 _FlushingStreamHandler after first setup, got {n1}"
        assert n2 == 1, f"GT-13 idempotency broken: {n1} -> {n2} _FlushingStreamHandlers"
    finally:
        reset()


def test_winerror1_on_write_is_benign_and_silent(monkeypatch) -> None:
    """On a Windows console, ``write()`` itself can raise WinError 1"""
    import errno

    written: list[str] = []

    class _WinConsoleStderr:
        def isatty(self) -> bool:
            return True

        def reconfigure(self, **_kwargs) -> None:  # pragma: no cover - best-effort
            pass

        def write(self, s: str) -> None:
            written.append(s)
            exc = OSError(errno.EINVAL, "Incorrect function")
            exc.winerror = 1
            raise exc

        def flush(self) -> None:
            exc = OSError(errno.EINVAL, "Incorrect function")
            exc.winerror = 1
            raise exc

    handler = _FlushingStreamHandler(stream=_WinConsoleStderr())
    handler.setFormatter(logging.Formatter("%(message)s"))

    def _record(msg: str) -> logging.LogRecord:
        return logging.LogRecord(
            "voice_typer.server.log_formatting_test",
            logging.INFO,
            __file__,
            1,
            msg,
            None,
            None,
        )

    # Benign path: os.name == "nt" + winerror == 1 → silent, handler alive.
    with monkeypatch.context() as m:
        m.setattr(os, "name", "nt")
        handler.emit(_record("first line"))

    assert not handler._flushed_once, "WinError 1 on write must NOT trip the broken-stream diagnostic"
    assert any("first line" in w for w in written), "line must reach the stream"

    # Second record: still works, still silent.
    with monkeypatch.context() as m:
        m.setattr(os, "name", "nt")
        handler.emit(_record("second line"))
    assert not handler._flushed_once
    assert any("second line" in w for w in written)


def test_broken_console_stream_silently_swallowed(tmp_path: Path, monkeypatch) -> None:
    """
    A Windows console flush that raises WinError 1 (ERROR_INVALID_FUNCTION)
    NOT emit a diagnostic, must NOT detach, and must keep writing.
    """
    written: list[str] = []
    flushed = 0

    class _FlakyConsoleStderr:
        def isatty(self) -> bool:
            return True

        def reconfigure(self, **_kwargs) -> None:  # pragma: no cover - best-effort
            pass

        def write(self, s: str) -> None:
            written.append(s)

        def flush(self) -> None:
            nonlocal flushed
            flushed += 1
            raise OSError(1, "Incorrect function")

    broken = _FlakyConsoleStderr()
    monkeypatch.setattr(sys, "stderr", broken)
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    try:
        setup_logging(config_dir)
        root = logging.getLogger("voice_typer")
        stream = next(h for h in root.handlers if isinstance(h, _FlushingStreamHandler))
        assert not stream._flushed_once

        logger = logging.getLogger("voice_typer.server.log_formatting_test")
        logger.info("first line")

        # The benign flush error produces NO diagnostic (write suceeded,
        assert not stream._flushed_once, "benign flush error must NOT trip the broken-stream diagnostic"
        assert stream in root.handlers, "handler must stay attached"
        assert any("first line" in w for w in written), "line must reach the stream"

        # Second record: still works, no diagnostic.
        logger.info("second line")
        assert stream in root.handlers
        assert any("second line" in w for w in written)
    finally:
        reset()


def test_apply_per_module_log_levels_warns_on_unknown_level(tmp_path: Path, monkeypatch, caplog) -> None:
    """an invalid level name logs a WARNING so the operator can"""
    monkeypatch.setenv(
        "VOICE_TYPER_LOG_LEVEL_MODULES",
        "voice_typer.server.typo_module=BOGUS,voice_typer.server.real_module=INFO",
    )
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    with caplog.at_level(logging.WARNING, logger="voice_typer.server.log"):
        setup_logging(config_dir)
    try:
        skipped = [r for r in caplog.records if "skipping invalid" in r.getMessage()]
        assert skipped, "GT-65 regression: no WARNING emitted for the invalid VOICE_TYPER_LOG_LEVEL_MODULES entry"
        # The skipped entry's raw text is included so the operator can
        assert any("BOGUS" in r.getMessage() for r in skipped)
        # The valid entry still applied.
        assert logging.getLogger("voice_typer.server.real_module").level == logging.INFO
    finally:
        reset()


def test_apply_per_module_log_levels_warns_on_missing_equals(tmp_path: Path, monkeypatch, caplog) -> None:
    """an entry missing the ``=`` separator logs a WARNING."""
    monkeypatch.setenv(
        "VOICE_TYPER_LOG_LEVEL_MODULES",
        "voice_typer.server.no_separator,voice_typer.server.ok=DEBUG",
    )
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    with caplog.at_level(logging.WARNING, logger="voice_typer.server.log"):
        setup_logging(config_dir)
    try:
        skipped = [r for r in caplog.records if "missing '='" in r.getMessage()]
        assert skipped, "GT-65 regression: no WARNING emitted for entry missing '='"
        assert logging.getLogger("voice_typer.server.ok").level == logging.DEBUG
    finally:
        reset()


# Text output (file): clean space-separated local timestamp
_TS_RE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}  \d{2}:\d{2}:\d{2}")
# Terminal output: time only `HH:MM:SS` (no date, no millis).
_TS_RE_TERM = re.compile(r"\d{2}:\d{2}:\d{2}")
# JSON output: UTC ISO 8601 with Z suffix (for log aggregators).
_ISO_RE_JSON = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


def test_file_formatter_clean_timestamp_no_millis() -> None:
    """seconds-only precision: ``YYYY-MM-DD  HH:MM:SS``, TWO spaces"""
    record = logging.LogRecord(
        name="voice_typer.server.fake",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ts check",
        args=(),
        exc_info=None,
    )
    record.session_id = "deadbeef"
    line = _FileFormatter().format(record)
    assert _TS_RE_TEXT.search(line), f"regression: text timestamp not clean with two-space date/time sep:\n{line!r}"
    # No T separator between date and time, no tz offset suffix.
    first = line.split()[0]
    assert "T" not in first, f"no T separator expected: {line!r}"
    assert "+" not in first and not first.endswith("Z"), f"no tz offset expected: {line!r}"
    # No millisecond fraction (seconds-only precision).
    assert "." not in first
    assert not re.search(r"\d{2}\.\d{3}", line), f"no millis expected: {line!r}"


def test_color_formatter_clean_timestamp_time_only() -> None:
    """``_ColorFormatter`` (terminal) emits a TIME-ONLY timestamp"""
    record = logging.LogRecord(
        name="voice_typer.server.fake",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ts check",
        args=(),
        exc_info=None,
    )
    record.session_id = "deadbeef"
    line = _ColorFormatter().format(record)
    # Strip ANSI escapes before matching so colour codes don't interfere.
    plain = re.sub(r"\033\[[0-9;]*m", "", line)
    assert _TS_RE_TERM.search(plain), f"regression: colour formatter timestamp not time-only HH:MM:SS:\n{plain!r}"
    # The date must NOT appear in terminal output.
    assert not re.search(r"\d{4}-\d{2}-\d{2}", plain), f"date must not appear on terminal lines: {plain!r}"
    # No millis / tz on the terminal either.
    assert not re.search(r"\.\d{3}", plain), f"no millis expected on terminal: {plain!r}"


def test_json_formatter_iso_timestamp_utc_z_suffix() -> None:
    """``_JsonFormatter`` emits an ISO 8601 UTC timestamp with"""
    record = logging.LogRecord(
        name="voice_typer.server.fake",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ts check",
        args=(),
        exc_info=None,
    )
    record.session_id = "deadbeef"
    parsed = json.loads(_JsonFormatter().format(record))
    assert _ISO_RE_JSON.match(parsed["ts"]), (
        f"GT-61 regression: JSON 'ts' not UTC ISO 8601 with Z suffix: {parsed['ts']!r}"
    )


def test_bubble_filter_keeps_warning_records_mentioning_marker() -> None:
    """
    a WARNING (or higher) record whose message mentions
    ``bubble_level`` must NOT be dropped, it's the most diagnostic
    """
    filt = _BubbleLevelExclusionFilter()
    for level in (logging.WARNING, logging.ERROR, logging.CRITICAL):
        rec = logging.LogRecord(
            name="voice_typer.server.bubble_worker",
            level=level,
            pathname="x.py",
            lineno=1,
            msg="bubble_level handler crashed: queue full",
            args=None,
            exc_info=None,
        )
        assert filt.filter(rec) is True, (
            f"GT-62 regression: {logging.getLevelName(level)} record mentioning "
            "'bubble_level' was dropped, diagnostic errors must reach the file."
        )


def test_bubble_filter_still_drops_debug_bubble_records() -> None:
    """``IPCServer._send`` at ~60 Hz) must still be dropped from the file"""
    filt = _BubbleLevelExclusionFilter()
    rec = logging.LogRecord(
        name="voice_typer.server.ipc",
        level=logging.DEBUG,
        pathname="x.py",
        lineno=1,
        msg="no client; dropping high-freq bubble_level event",
        args=None,
        exc_info=None,
    )
    assert filt.filter(rec) is False, (
        "GT-62 regression: DEBUG bubble_level record not dropped, high-frequency "
        "noise would dominate the rotating file."
    )


def test_bubble_filter_keeps_unrelated_info_records() -> None:
    """INFO records that do NOT mention ``bubble_level`` are kept"""
    filt = _BubbleLevelExclusionFilter()
    rec = logging.LogRecord(
        name="voice_typer.server.ipc",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="state_changed pushed to client",
        args=None,
        exc_info=None,
    )
    assert filt.filter(rec) is True


def test_set_module_level_changes_logger_level() -> None:
    """func:`set_module_level` immediately applies the new level"""
    log = logging.getLogger("voice_typer.server.gt64_target")
    log.setLevel(logging.INFO)
    set_module_level("voice_typer.server.gt64_target", "DEBUG")
    assert log.level == logging.DEBUG


def test_set_module_level_case_insensitive() -> None:
    """level name is case-insensitive (``\"debug\"`` == ``\"DEBUG\"``)."""
    log = logging.getLogger("voice_typer.server.gt64_case")
    log.setLevel(logging.WARNING)
    set_module_level("voice_typer.server.gt64_case", "warning")
    assert log.level == logging.WARNING


def test_set_module_level_invalid_raises_value_error() -> None:
    """an unknown level name raises :class:`ValueError` (rather"""
    with pytest.raises(ValueError):
        set_module_level("voice_typer.server.gt64_bad", "NOPE")


def test_set_module_level_empty_name_raises_value_error() -> None:
    """an empty module name raises :class:`ValueError`."""
    with pytest.raises(ValueError):
        set_module_level("", "DEBUG")


def test_get_module_levels_returns_set_overrides() -> None:
    """func:`get_module_levels` returns the dict of explicitly-set"""
    set_module_level("voice_typer.server.gt64_a", "DEBUG")
    set_module_level("voice_typer.server.gt64_b", "WARNING")
    overrides = get_module_levels()
    assert overrides.get("voice_typer.server.gt64_a") == "DEBUG"
    assert overrides.get("voice_typer.server.gt64_b") == "WARNING"


def test_get_module_levels_returns_fresh_dict() -> None:
    """mutating the returned dict does not affect internal state."""
    set_module_level("voice_typer.server.gt64_iso", "INFO")
    snap = get_module_levels()
    snap["voice_typer.server.gt64_iso"] = "DEBUG"  # mutate the snapshot
    snap["injected"] = "ERROR"
    # Internal state is unchanged.
    assert get_module_levels().get("voice_typer.server.gt64_iso") == "INFO"
    assert "injected" not in get_module_levels()


def test_set_module_level_emits_info_audit_log(tmp_path: Path, caplog) -> None:
    """func:`set_module_level` emits an INFO log so the change"""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)
    try:
        with caplog.at_level(logging.INFO, logger="voice_typer.server.log"):
            set_module_level("voice_typer.server.gt64_audit", "DEBUG")
        audit = [r for r in caplog.records if "runtime override" in r.getMessage()]
        assert audit, "GT-64 regression: set_module_level did not emit an INFO audit log"
        assert "gt64_audit" in audit[0].getMessage()
        assert "DEBUG" in audit[0].getMessage()
    finally:
        reset()


def test_get_module_levels_includes_env_var_overrides(tmp_path: Path, monkeypatch) -> None:
    """overrides applied via ``VOICE_TYPER_LOG_LEVEL_MODULES`` at"""
    monkeypatch.setenv(
        "VOICE_TYPER_LOG_LEVEL_MODULES",
        "voice_typer.server.gt64_env_a=DEBUG,voice_typer.server.gt64_env_b=WARNING",
    )
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    try:
        setup_logging(config_dir)
        overrides = get_module_levels()
        assert overrides.get("voice_typer.server.gt64_env_a") == "DEBUG"
        assert overrides.get("voice_typer.server.gt64_env_b") == "WARNING"
    finally:
        reset()
