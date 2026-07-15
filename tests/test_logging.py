"""Regression tests for centralized logging infrastructure.

Covers three findings from a-review:

* Finding 5 (C2) — ``session_id`` rendering in log output.
  ``_SessionFilter`` was injecting ``session_id`` into every record but
  neither ``_FileFormatter`` nor ``_ColorFormatter`` rendered it, so
  the 8-char per-process ID never appeared in ``voice-typer.log`` and
  operators could not correlate lines across process restarts or
  distinguish interleaved lines from concurrent backends.

* Finding 6 (C3) — ``get_logger`` is dead code.
  The factory was documented as the canonical logger entry point but
  zero call-sites used it (every module did
  ``logging.getLogger(__name__)`` directly).  These tests pin the
  removal so the dead factory is not reintroduced.

These tests intentionally exercise both the end-to-end pipeline
(``setup_logging`` → file on disk) and the formatters in isolation
so a regression in either layer is caught.
"""

from __future__ import annotations

import logging
from pathlib import Path

import voice_typer.server.log as log_module
from voice_typer.server.log import (
    _ColorFormatter,
    _FileFormatter,
    reset,
    setup_logging,
)

# ─── C2: session_id rendering ──────────────────────────────────────────


def test_session_id_appears_in_file_log(tmp_path: Path) -> None:
    """End-to-end: ``setup_logging`` → log line → file on disk.

    The 8-char session ID returned by ``setup_logging`` must appear
    inside square brackets on every file log line so operators can
    correlate log lines across process restarts and disambiguate
    interleaved lines from concurrent processes.
    """
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

        log_file = config_dir / "voice-typer.log"
        content = log_file.read_text(encoding="utf-8")
        assert f"[{session_id}]" in content, f"session_id bracket [{session_id}] not found in log file:\n{content}"
        # Existing format pieces must still be present (regression guard
        # that the session_id bracket is appended, not replacing anything).
        assert "INFO" in content
        assert "[HOTKEY] RegisterHotKey succeeded" in content
    finally:
        reset()


def test_file_formatter_renders_session_id_bracket() -> None:
    """``_FileFormatter.format`` emits a ``[session_id]`` bracket before
    the message when the record carries a session_id attribute."""
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
    assert "[a3f1b2c4]" in line
    # Timestamp + level label + message all still present.
    assert "INFO" in line
    assert "[HOTKEY] RegisterHotKey succeeded" in line


def test_color_formatter_renders_dimmed_session_id_bracket() -> None:
    """``_ColorFormatter.format`` emits the session_id bracket dimmed
    (SGR 2) so it doesn't compete with the level colour or message
    body on the terminal."""
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
    assert "[a3f1b2c4]" in line
    # SGR 2 = dim/faint attribute (per ECMA-48 / ANSI X3.64).
    assert "\033[2m" in line, f"dim SGR (\\033[2m) missing from: {line!r}"


def test_color_formatter_renders_session_id_for_warning_lines() -> None:
    """WARN/ERR/FATAL lines (full-line coloured) must also include the
    session_id bracket — otherwise error logs would lose correlation."""
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
    assert "[deadbeef]" in line
    assert "WARN" in line
    # Full-line WARN colour (pure yellow #FFFF00) still applied.
    assert "\033[38;5;226m" in line


def test_session_id_defaults_to_dashes_when_filter_not_applied() -> None:
    """Records constructed without going through ``_SessionFilter``
    (e.g. third-party library logs, or unit-test records built by hand)
    must not raise ``AttributeError`` and must render a ``[--------]``
    placeholder so the line still has a well-formed bracket.

    Also covers the early-startup case where ``setup_logging`` has not
    yet been called (``_session_id`` is the empty string ``""``).
    """
    record = logging.LogRecord(
        name="transformers",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="loading model",
        args=(),
        exc_info=None,
    )
    # Deliberately do NOT set record.session_id — simulates a record
    # that bypassed the _SessionFilter (no attribute on the LogRecord).
    assert not hasattr(record, "session_id")

    file_line = _FileFormatter().format(record)
    assert "[--------]" in file_line

    color_line = _ColorFormatter().format(record)
    assert "[--------]" in color_line


def test_session_id_defaults_to_dashes_when_filter_set_empty_string() -> None:
    """When ``_SessionFilter`` runs before ``setup_logging`` has assigned
    a session_id, it sets ``record.session_id = ""`` (the module-level
    default).  The formatters must render ``[--------]`` in that case
    rather than ``[]`` (empty brackets look like a bug to operators)."""
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
    assert "[--------]" in file_line
    assert "[]" not in file_line


# ─── C3: get_logger removal (regression guard) ─────────────────────────


def test_get_logger_is_not_exported() -> None:
    """a-review Finding 6: ``get_logger`` was dead code — documented as
    the canonical logger factory but used by zero call-sites (every
    module does ``logging.getLogger(__name__)`` directly).  This test
    pins the removal so the dead factory is not reintroduced."""
    assert not hasattr(log_module, "get_logger"), (
        "voice_typer.server.log.get_logger was removed as dead code "
        "(a-review Finding 6).  Do not re-add it — modules should use "
        "logging.getLogger(__name__) directly."
    )


def test_module_docstring_does_not_advertise_get_logger() -> None:
    """The module docstring must not advertise ``get_logger`` as the
    canonical entry point — that would mislead readers into using a
    function that doesn't exist."""
    assert "get_logger" not in (log_module.__doc__ or "")
