"""Regression tests for centralized logging infrastructure."""

from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path

import voice_typer.server.log as log_module
from voice_typer.server.log import (
    _ColorFormatter,
    _FileFormatter,
    get_log_file_path,
    reset,
    setup_logging,
)


def test_file_log_line_is_clean_end_to_end(tmp_path: Path) -> None:
    """End-to-end: ``setup_logging`` → log line → file on disk."""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    session_id = setup_logging(config_dir)
    assert isinstance(session_id, str)
    assert len(session_id) == 8

    try:
        log = logging.getLogger("voice_typer.server.fake_module")
        log.info("[HOTKEY] RegisterHotKey succeeded")
        # Force the RotatingFileHandler to flush to disk before reading.
        for h in logging.getLogger("voice_typer").handlers:
            with __import__("contextlib").suppress(Exception):
                h.flush()

        log_file = config_dir / "logs" / "voice-typer.log"
        content = log_file.read_text(encoding="utf-8")
        # The message and level must be present…
        assert "INFO" in content
        assert "[HOTKEY] RegisterHotKey succeeded" in content
        # …but the per-line correlation/thread/component clutter is gone.
        assert f"[{session_id}]" not in content, (
            f"session_id bracket [{session_id}] must NOT appear on file log lines:\n{content}"
        )
        assert "[MainThread]" not in content
        assert "[fake_module]" not in content
        # Clean space-separated timestamp (no T separator, no tz offset).
        assert "T" not in content.split()[0], f"timestamp must be space-separated:\n{content}"
    finally:
        reset()


def test_file_formatter_omits_session_thread_component() -> None:
    """``_FileFormatter.format`` must NOT render the session_id bracket,"""
    record = logging.LogRecord(
        name="voice_typer",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[HOTKEY] RegisterHotKey succeeded",
        args=(),
        exc_info=None,
    )
    record.session_id = "a3f1b2c4"
    line = _FileFormatter().format(record)
    assert "[a3f1b2c4]" not in line
    assert "MainThread" not in line
    # The module-path component label is gone (message only carries its
    assert "[voice_typer.server.log]" not in line
    assert "\033[" not in line  # file output is plain text, no ANSI
    # Timestamp + level label + message all still present.
    assert "INFO" in line
    assert "[HOTKEY] RegisterHotKey succeeded" in line
    # Clean timestamp: `YYYY-MM-DD  HH:MM:SS` (two spaces, no millis,
    parts = line.split()
    assert len(parts) >= 3
    assert "-" in parts[0] and ":" in parts[1], f"clean ts expected, got: {line!r}"
    assert "T" not in parts[0]
    assert "+0300" not in line and "+0200" not in line  # tz offset gone


_EXACT_FILE_LINE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}  \d{2}:\d{2}:\d{2}  "  # clean ts + 2 spaces
    r"(?:DEBUG|INFO|WARN|ERROR|CRITICAL) {1,2}"  # level label + aligned spacing
    r".+"  # message (unmodified)
)


def test_file_formatter_exact_line_shape_is_timestamp_level_message() -> None:
    """Pin the EXACT file line shape to ``<ts>  <LEVEL>  <msg>``."""
    record = logging.LogRecord(
        name="voice_typer.server.app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Voice Typer starting -- model=small.en",
        args=(),
        exc_info=None,
    )
    record.session_id = "2ae8edcc"
    record.component = "voice_typer.server.app"
    record.threadName = "MainThread"
    record.funcName = "main"

    line = _FileFormatter().format(record)

    # 1) The whole line must match `ts  LEVEL  msg` and nothing else.
    assert _EXACT_FILE_LINE_RE.fullmatch(line), (
        f"regression: file line shape is not exactly '<ts>  <LEVEL>  <msg>':\n{line!r}"
    )
    # 2) None of the removed fields may appear anywhere in the line.
    assert "2ae8edcc" not in line
    assert "MainThread" not in line
    assert "voice_typer.server.app" not in line
    assert "main" not in line
    # 3) The message text is preserved verbatim at the end of the line.
    assert line.endswith("Voice Typer starting -- model=small.en")
    # 4) Clean timestamp: space-separated, no T separator, no tz offset.
    ts, _, _ = line.partition("  ")
    assert "T" not in ts and "+" not in ts and not ts.endswith("Z")


def test_file_formatter_level_column_alignment() -> None:
    """4-char level labels (``INFO``/``WARN``) get TWO spaces after,"""
    for level, label, expected_sep in [
        (logging.INFO, "INFO", "  "),
        (logging.WARNING, "WARN", "  "),
        (logging.ERROR, "ERROR", " "),
        (logging.DEBUG, "DEBUG", " "),
        (logging.CRITICAL, "CRITICAL", " "),
    ]:
        record = logging.LogRecord(
            name="test",
            level=level,
            pathname=__file__,
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        line = _FileFormatter().format(record)
        # Extract the label + its trailing separator from the exact
        m = re.match(
            r"\d{4}-\d{2}-\d{2}  \d{2}:\d{2}:\d{2}  ([A-Z]+)( +)",
            line,
        )
        assert m is not None, f"line shape unexpected: {line!r}"
        assert m.group(1) == label, f"expected label {label!r}, got {m.group(1)!r}"
        assert m.group(2) == expected_sep, f"expected {expected_sep!r} after {label} (got {m.group(2)!r}):\n{line!r}"


def test_color_formatter_warning_level_column_alignment() -> None:
    """Terminal WARN/ERROR/CRITICAL lines also align the level column:"""
    for level, sym, expected_sep in [
        (logging.WARNING, "WARN", "  "),
        (logging.ERROR, "ERROR", " "),
        (logging.CRITICAL, "CRITICAL", " "),
    ]:
        record = logging.LogRecord(
            name="test",
            level=level,
            pathname=__file__,
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        line = _ColorFormatter().format(record)
        # Strip ANSI escapes before checking spacing.
        plain = re.sub(r"\033\[[0-9;]*m", "", line)
        # Extract the sym + its trailing separator from the terminal
        m = re.match(r"\d{2}:\d{2}:\d{2}  ([A-Z]+)( +)", plain)
        assert m is not None, f"line shape unexpected: {plain!r}"
        assert m.group(1) == sym, f"expected sym {sym!r}, got {m.group(1)!r}"
        assert m.group(2) == expected_sep, f"expected {expected_sep!r} after {sym} (got {m.group(2)!r}):\n{line!r}"


def test_color_formatter_omits_session_id_bracket() -> None:
    """``_ColorFormatter.format`` must NOT render the session_id bracket"""
    record = logging.LogRecord(
        name="voice_typer",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[HOTKEY] RegisterHotKey succeeded",
        args=(),
        exc_info=None,
    )
    record.session_id = "a3f1b2c4"
    line = _ColorFormatter().format(record)
    assert "[a3f1b2c4]" not in line
    assert "MainThread" not in line
    # Message still present and coloured by topic.
    assert "[HOTKEY] RegisterHotKey succeeded" in line
    assert "\033[" in line


def test_color_formatter_warning_lines_omit_session_id() -> None:
    """WARN/ERR/FATAL lines (full-line coloured) must also omit the"""
    record = logging.LogRecord(
        name="voice_typer",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="[ENV] Invalid value for hotkey",
        args=(),
        exc_info=None,
    )
    record.session_id = "deadbeef"
    line = _ColorFormatter().format(record)
    assert "[deadbeef]" not in line
    # Short level label `WARN` (not the long-form `WARNING`).
    assert "WARN" in line
    # Full-line WARN colour (pure yellow #FFFF00) still applied.
    assert "\033[38;5;226m" in line


def test_formatters_do_not_require_session_attribute() -> None:
    """Records constructed without going through ``_SessionFilter``"""
    record = logging.LogRecord(
        name="transformers",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="loading model",
        args=(),
        exc_info=None,
    )
    # Deliberately do NOT set record.session_id, simulates a record
    assert not hasattr(record, "session_id")

    file_line = _FileFormatter().format(record)
    assert "[--------]" not in file_line
    assert "loading model" in file_line

    color_line = _ColorFormatter().format(record)
    assert "[--------]" not in color_line
    assert "loading model" in color_line


def test_formatter_empty_session_id_renders_no_bracket() -> None:
    """When ``_SessionFilter`` runs before ``setup_logging`` has assigned"""
    record = logging.LogRecord(
        name="voice_typer",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="early startup line",
        args=(),
        exc_info=None,
    )
    record.session_id = ""  # Simulates filter applied before setup_logging().
    file_line = _FileFormatter().format(record)
    assert "[" not in file_line
    assert "early startup line" in file_line


def test_get_logger_is_not_exported() -> None:
    """the canonical logger factory but used by zero call-sites (every"""
    assert not hasattr(log_module, "get_logger"), (
        "voice_typer.server.log.get_logger was removed as dead code "
        "(a-review Finding 6).  Do not re-add it, modules should use "
        "logging.getLogger(__name__) directly."
    )


def test_module_docstring_does_not_advertise_get_logger() -> None:
    """function that doesn't exist."""
    assert "get_logger" not in (log_module.__doc__ or "")


def test_worker_log_file_is_separate_from_sidecar(tmp_path: Path) -> None:
    """shared ``voice-typer.log``."""
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()

    worker_path = get_log_file_path(config_dir, process_name="worker")
    sidecar_path = get_log_file_path(config_dir, process_name="voice-typer")
    default_path = get_log_file_path(config_dir)
    main_path = get_log_file_path(config_dir, process_name="main")

    # 1) Worker gets its OWN file, not the shared sidecar file.
    assert worker_path == config_dir / "logs" / "worker.log", (
        f"regression: process_name='worker' must route to worker.log, got {worker_path}"
    )
    assert sidecar_path == config_dir / "logs" / "voice-typer.log", (
        f"regression: process_name='voice-typer' must route to voice-typer.log, got {sidecar_path}"
    )
    assert default_path == config_dir / "logs" / "voice-typer.log"
    assert main_path == config_dir / "logs" / "voice-typer.log"
    # 3) The race-elimination invariant: the two paths MUST differ.
    assert worker_path != sidecar_path, (
        "rotation-race regression: worker and sidecar must NOT share a log file "
        f"(both resolved to {worker_path}), the _SecureTruncatingFileHandler "
        "rotation race would re-emerge."
    )


def test_worker_setup_logging_writes_to_worker_log_file(tmp_path: Path) -> None:
    """End-to-end: ``setup_logging(config_dir, process_name=\"worker\")``"""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    try:
        setup_logging(config_dir, process_name="worker")
        log = logging.getLogger("voice_typer.server.wave3_worker_routing_test")
        log.info("[WORKER] rotation-race regression test line")

        # Flush so the record reaches disk before we read the file.
        for h in logging.getLogger("voice_typer").handlers:
            with contextlib.suppress(Exception):
                h.flush()

        worker_log = config_dir / "logs" / "worker.log"
        sidecar_log = config_dir / "logs" / "voice-typer.log"

        assert worker_log.exists(), (
            "regression: worker.log was NOT created, process_name='worker' is not routing to worker.log"
        )
        assert not sidecar_log.exists(), (
            "regression: voice-typer.log WAS created, process_name='worker' "
            "is racing the slim-core sidecar on the shared file (the exact "
            "race this routing was added to eliminate)."
        )
        content = worker_log.read_text(encoding="utf-8")
        assert "[WORKER] rotation-race regression test line" in content, (
            f"regression: worker log line missing from worker.log:\n{content}"
        )
    finally:
        reset()
