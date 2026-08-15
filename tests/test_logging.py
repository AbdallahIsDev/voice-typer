"""Regression tests for centralized logging infrastructure.

Covers three findings from a-review:

* Finding 5 (C2) — clean log-line rendering.
  The text formatters (``_FileFormatter`` for the file, ``_ColorFormatter``
  for the terminal) render a CLEAN line: timestamp + level + message.
  The per-line ``[session_id]`` bracket, ``[threadName]``, and
  ``[component]`` labels were removed — they added noise to every line
  without helping the user read the log.  Correlation metadata stays
  available in JSON mode (``VOICE_TYPER_LOG_JSON=1``).  These tests pin
  the clean format so the clutter is not reintroduced.

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

# ─── C2: clean line format (no session/thread/component clutter) ───────


def test_file_log_line_is_clean_end_to_end(tmp_path: Path) -> None:
    """End-to-end: ``setup_logging`` → log line → file on disk.

    The rendered line must be CLEAN: timestamp + level + message.  The
    8-char session ID, thread name, and module path must NOT appear on
    the line — they added noise to every line without helping the user
    read the log.
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
    """``_FileFormatter.format`` must NOT render the session_id bracket,
    thread name, or component label — the line is timestamp + level +
    message only."""
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
    # own [TOPIC] prefix).
    assert "[voice_typer.server.log]" not in line
    assert "\033[" not in line  # file output is plain text, no ANSI
    # Timestamp + level label + message all still present.
    assert "INFO" in line
    assert "[HOTKEY] RegisterHotKey succeeded" in line
    # Clean timestamp: `YYYY-MM-DD  HH:MM:SS` (two spaces, no millis,
    # no T separator, no tz).
    parts = line.split()
    assert len(parts) >= 3
    assert "-" in parts[0] and ":" in parts[1], f"clean ts expected, got: {line!r}"
    assert "T" not in parts[0]
    assert "+0300" not in line and "+0200" not in line  # tz offset gone


# Exact line shape: `<ts>  <LEVEL>  <msg>` — a full-line anchored match,
# stronger than the substring checks above.  A session id, thread name,
# or module path inserted ANYWHERE (before the ts, between fields, or
# appended at the end) breaks the match.
#
# Timestamp is `YYYY-MM-DD  HH:MM:SS` — TWO spaces between the date
# and the time (so the time column aligns in the file), seconds-only
# precision (no millisecond fraction).  Level label is the short form
# (`WARN`, not `WARNING`).
_EXACT_FILE_LINE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}  \d{2}:\d{2}:\d{2}  "  # clean ts + 2 spaces
    r"(?:DEBUG|INFO|WARN|ERROR|CRITICAL)  "  # level label + 2 spaces
    r".+"  # message (unmodified)
)


def test_file_formatter_exact_line_shape_is_timestamp_level_message() -> None:
    """Pin the EXACT file line shape to ``<ts>  <LEVEL>  <msg>``.

    The record carries every field the old format printed on every line
    (session id, component / module path, thread name, function name) so
    a reintroduction anywhere in the rendering pipeline is caught — not
    just the specific bracket styles the substring tests guard against.
    """
    record = logging.LogRecord(
        name="voice_typer.server.app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Voice Typer starting -- model=small.en",
        args=(),
        exc_info=None,
    )
    # Full clutter: everything the old format rendered on every line.
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


def test_color_formatter_omits_session_id_bracket() -> None:
    """``_ColorFormatter.format`` must NOT render the session_id bracket
    on the terminal."""
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
    """WARN/ERR/FATAL lines (full-line coloured) must also omit the
    session_id bracket."""
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
    """Records constructed without going through ``_SessionFilter``
    (e.g. third-party library logs, or unit-test records built by hand)
    must not raise ``AttributeError``.

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
    assert "[--------]" not in file_line
    assert "loading model" in file_line

    color_line = _ColorFormatter().format(record)
    assert "[--------]" not in color_line
    assert "loading model" in color_line


def test_formatter_empty_session_id_renders_no_bracket() -> None:
    """When ``_SessionFilter`` runs before ``setup_logging`` has assigned
    a session_id, it sets ``record.session_id = ""`` (the module-level
    default).  The formatters must render no bracket at all (no
    ``[]``, no ``[--------]``)."""
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


# ─── Worker log rotation race (Wave 3 Sub-agent 7) ───────────────────────
#
# The runtime-pack WebSocket worker (``voice_typer/worker/__main__.py``)
# runs as a SEPARATE process alongside the slim-core sidecar.  Both used
# to write to ``voice-typer.log`` — a multi-process race on the
# ``_SecureTruncatingFileHandler``'s in-place truncation rotation
# (maxBytes=5 MiB, backupCount=0) that could lose data when both
# processes rotated at once.  The fix routes the worker to its own
# ``worker.log`` via ``process_name="worker"`` (mirroring the prewarm
# case → ``prewarm.log``).  These tests pin the routing so a revert
# re-introduces the race.


def test_worker_log_file_is_separate_from_sidecar(tmp_path: Path) -> None:
    """``process_name="worker"`` routes to ``worker.log``, NOT the
    shared ``voice-typer.log``.

    This is the core race-elimination invariant — if the worker and
    the slim-core sidecar share ``voice-typer.log``, the
    ``_SecureTruncatingFileHandler``'s in-place truncation rotation
    can race (both processes stat >5 MiB, both truncate, data is
    lost).  Routing the worker to its own file eliminates the race
    because the two processes never share a file descriptor.

    Reverting the ``"worker"`` case in :func:`get_log_file_path` makes
    this test FAIL — the worker path would equal the sidecar path
    (``voice-typer.log``) instead of ``worker.log``.
    """
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()

    worker_path = get_log_file_path(config_dir, process_name="worker")
    sidecar_path = get_log_file_path(config_dir, process_name="voice-typer")
    default_path = get_log_file_path(config_dir)
    main_path = get_log_file_path(config_dir, process_name="main")

    # 1) Worker gets its OWN file — not the shared sidecar file.
    assert worker_path == config_dir / "worker.log", (
        f"regression: process_name='worker' must route to worker.log, got {worker_path}"
    )
    # 2) The sidecar (explicit "voice-typer" or default "main") still
    #    routes to voice-typer.log — the routing fix must not break the
    #    existing sidecar path.
    assert sidecar_path == config_dir / "voice-typer.log", (
        f"regression: process_name='voice-typer' must route to voice-typer.log, got {sidecar_path}"
    )
    assert default_path == config_dir / "voice-typer.log"
    assert main_path == config_dir / "voice-typer.log"
    # 3) The race-elimination invariant: the two paths MUST differ.
    assert worker_path != sidecar_path, (
        "rotation-race regression: worker and sidecar must NOT share a log file "
        f"(both resolved to {worker_path}) — the _SecureTruncatingFileHandler "
        "rotation race would re-emerge."
    )


def test_worker_setup_logging_writes_to_worker_log_file(tmp_path: Path) -> None:
    """End-to-end: ``setup_logging(config_dir, process_name="worker")``
    actually writes log records to ``worker.log`` on disk — and does
    NOT touch the shared ``voice-typer.log``.

    Pins the full pipeline (``setup_logging`` → ``get_log_file_path``
    → ``_SecureTruncatingFileHandler`` → file on disk) so a revert
    that breaks the routing at any layer is caught.  A revert that
    removes the ``"worker"`` case from ``get_log_file_path`` would
    cause this test to FAIL: the worker's log line would land in
    ``voice-typer.log`` instead of ``worker.log``.
    """
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

        worker_log = config_dir / "worker.log"
        sidecar_log = config_dir / "voice-typer.log"

        assert worker_log.exists(), (
            "regression: worker.log was NOT created — process_name='worker' "
            "is not routing to worker.log"
        )
        assert not sidecar_log.exists(), (
            "regression: voice-typer.log WAS created — process_name='worker' "
            "is racing the slim-core sidecar on the shared file (the exact "
            "race this routing was added to eliminate)."
        )
        content = worker_log.read_text(encoding="utf-8")
        assert "[WORKER] rotation-race regression test line" in content, (
            f"regression: worker log line missing from worker.log:\n{content}"
        )
    finally:
        reset()
