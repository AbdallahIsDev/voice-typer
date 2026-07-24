"""Regression tests for GT-FIX-01 (Group 5 — Reliability & Observability) in
``voice_typer/server/log.py``.

Covers six findings from the comprehensive review:

* **GT-2 (Critical)** — custom formatters must append ``exc_info`` /
  tracebacks.  ``log.exception(...)`` / ``log.error(..., exc_info=True)``
  used to silently lose their stack trace because none of the three
  custom :class:`logging.Formatter` subclasses called
  ``super().format()`` or appended ``record.exc_text``.
* **GT-13 (High)** — a stderr :class:`_FlushingStreamHandler` must be
  attached even when stderr is not a TTY (Tauri sidecar / piped) so
  early startup failures remain visible when the rotating-file write
  silently fails (disk full, read-only config dir, bad perms).
* **GT-65 (Medium)** — :func:`_apply_per_module_log_levels` must log
  a WARNING for each skipped ``VOICE_TYPER_LOG_LEVEL_MODULES`` entry
  so a typo no longer silently disables DEBUG output.
* **GT-61 (Medium)** — timestamps must include milliseconds and a
  timezone offset (ISO 8601) so two log lines within the same second
  are distinguishable and cross-timezone tickets don't need manual
  timezone inference.
* **GT-62 (Medium)** — :class:`_BubbleLevelExclusionFilter` must keep
  WARNING+ records unconditionally (cheap path) so a legitimate
  ``"bubble_level handler crashed"`` error is never dropped from the
  file.
* **GT-64 (Medium)** — :func:`set_module_level` and
  :func:`get_module_levels` provide a runtime API for changing a
  subsystem's log level without restarting the sidecar.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
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


# ─── Test isolation ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Snapshot and restore the ``voice_typer`` logger + override registry.

    Tests that call :func:`setup_logging` mutate the global
    ``voice_typer`` logger (handlers, filters, level) and the module-level
    :data:`_module_level_overrides` dict.  Without snapshot/restore the
    state would leak across tests and break isolation — especially
    relevant because :func:`set_module_level` (GT-64) mutates the
    registry.
    """
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


# ─── GT-2: formatters must append exc_info ─────────────────────────────────


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
    """GT-2: ``_FileFormatter.format`` appends the traceback after the
    message body so ``log.exception(...)`` records keep their stack
    trace in the file."""
    line = _FileFormatter().format(_make_record_with_exc("outer boom"))
    assert "Traceback (most recent call last)" in line, (
        f"GT-2 regression: traceback missing from file-format output:\n{line!r}"
    )
    assert "RuntimeError: intentional test exception" in line
    assert "outer boom" in line


def test_color_formatter_appends_traceback() -> None:
    """GT-2: ``_ColorFormatter.format`` appends the traceback (plain
    text, no ANSI) so terminal output of ``log.exception(...)`` is
    diagnostic."""
    line = _ColorFormatter().format(_make_record_with_exc("color boom"))
    assert "Traceback (most recent call last)" in line
    assert "RuntimeError: intentional test exception" in line
    assert "color boom" in line


def test_json_formatter_includes_traceback_field() -> None:
    """GT-2: ``_JsonFormatter.format`` adds a ``traceback`` field so
    JSON aggregators can index / alert on stack traces."""
    out = _JsonFormatter().format(_make_record_with_exc("json boom"))
    parsed = json.loads(out)
    assert "traceback" in parsed, f"GT-2 regression: no 'traceback' key in JSON payload: {parsed}"
    assert "Traceback (most recent call last)" in parsed["traceback"]
    assert "RuntimeError: intentional test exception" in parsed["traceback"]


def test_file_formatter_no_traceback_when_no_exc_info() -> None:
    """A record without ``exc_info`` must not produce a traceback block
    (regression guard that GT-2 doesn't add noise to normal records)."""
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
    """GT-2 end-to-end: ``log.exception(...)`` writes a line containing
    ``'Traceback (most recent call last)'`` to ``voice-typer.log`` on disk."""
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
        content = (config_dir / "voice-typer.log").read_text(encoding="utf-8")
        assert "Traceback (most recent call last)" in content, (
            f"GT-2 end-to-end regression: no traceback in log file:\n{content}"
        )
        assert "ValueError: end-to-end boom" in content
    finally:
        reset()


# ─── GT-13: always attach a stderr StreamHandler ───────────────────────────


def test_setup_logging_attaches_stream_handler_without_tty(tmp_path: Path, monkeypatch) -> None:
    """GT-13: even when stderr is NOT a TTY and --port mode is NOT
    active (the Tauri sidecar case), ``setup_logging`` attaches a
    :class:`_FlushingStreamHandler` to the ``voice_typer`` logger so
    early startup failures remain visible when the rotating-file write
    silently fails (disk full / read-only config dir / bad perms)."""
    # Stub isatty() so the code path under test is exercised — pytest
    # captures stderr via a non-TTY wrapper, but to make this test
    # resilient against a future pytest that does provide a TTY we
    # force the non-TTY answer explicitly.
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
            "GT-13 regression: no _FlushingStreamHandler attached when stderr is not a TTY — "
            "Tauri sidecar startup failures would be invisible if the file write failed."
        )
    finally:
        reset()


def test_setup_logging_no_duplicate_stream_handlers_when_reinvoked(tmp_path: Path, monkeypatch) -> None:
    """GT-13 must not break idempotency — repeated ``setup_logging``
    calls do not duplicate the stderr stream handler."""
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
        assert n2 == 1, f"GT-13 idempotency broken: {n1} -> {n2} _FlushingStreamHandler(s)"
    finally:
        reset()


# ─── GT-65: warn on skipped per-module entries ────────────────────────────


def test_apply_per_module_log_levels_warns_on_unknown_level(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """GT-65: an invalid level name logs a WARNING so the operator can
    see *which* entry was skipped (previously a silent trap — a typo
    silently disabled DEBUG output)."""
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
        assert skipped, (
            "GT-65 regression: no WARNING emitted for the invalid VOICE_TYPER_LOG_LEVEL_MODULES entry"
        )
        # The skipped entry's raw text is included so the operator can
        # see which entry was ignored.
        assert any("BOGUS" in r.getMessage() for r in skipped)
        # The valid entry still applied.
        assert logging.getLogger("voice_typer.server.real_module").level == logging.INFO
    finally:
        reset()


def test_apply_per_module_log_levels_warns_on_missing_equals(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """GT-65: an entry missing the ``=`` separator logs a WARNING."""
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
        assert skipped, (
            "GT-65 regression: no WARNING emitted for entry missing '='"
        )
        assert logging.getLogger("voice_typer.server.ok").level == logging.DEBUG
    finally:
        reset()


# ─── GT-61: ISO 8601 timestamps with millis + tz ───────────────────────────


_ISO_RE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{4}")
_ISO_RE_JSON = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


def test_file_formatter_iso_timestamp_with_millis_and_tz() -> None:
    """GT-61: ``_FileFormatter`` emits an ISO 8601 timestamp with
    milliseconds and a timezone offset (``+HHMM``)."""
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
    assert _ISO_RE_TEXT.search(line), (
        f"GT-61 regression: text timestamp not ISO 8601 with millis+tz:\n{line!r}"
    )


def test_color_formatter_iso_timestamp_with_millis_and_tz() -> None:
    """GT-61: ``_ColorFormatter`` emits an ISO 8601 timestamp with
    milliseconds and a timezone offset."""
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
    assert _ISO_RE_TEXT.search(plain), (
        f"GT-61 regression: colour formatter timestamp not ISO 8601 with millis+tz:\n{plain!r}"
    )


def test_json_formatter_iso_timestamp_utc_z_suffix() -> None:
    """GT-61: ``_JsonFormatter`` emits an ISO 8601 UTC timestamp with
    milliseconds and a ``Z`` suffix (the format log aggregators expect)."""
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


# ─── GT-62: cheap pre-filter for _BubbleLevelExclusionFilter ───────────────


def test_bubble_filter_keeps_warning_records_mentioning_marker() -> None:
    """GT-62: a WARNING (or higher) record whose message mentions
    ``bubble_level`` must NOT be dropped — it's the most diagnostic
    record in a bubble-related failure and must reach the file."""
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
            "'bubble_level' was dropped — diagnostic errors must reach the file."
        )


def test_bubble_filter_still_drops_debug_bubble_records() -> None:
    """GT-62: the high-frequency DEBUG ``bubble_level`` push (emitted by
    ``IPCServer._send`` at ~60 Hz) must still be dropped from the file
    to preserve the original noise-suppression behaviour."""
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
        "GT-62 regression: DEBUG bubble_level record not dropped — high-frequency "
        "noise would dominate the rotating file."
    )


def test_bubble_filter_keeps_unrelated_info_records() -> None:
    """GT-62: INFO records that do NOT mention ``bubble_level`` are kept
    (regression guard)."""
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


# ─── GT-64: runtime log-level change API ───────────────────────────────────


def test_set_module_level_changes_logger_level() -> None:
    """GT-64: :func:`set_module_level` immediately applies the new level
    to the named logger so a subsystem's DEBUG output can be enabled
    without restarting the sidecar."""
    log = logging.getLogger("voice_typer.server.gt64_target")
    log.setLevel(logging.INFO)
    set_module_level("voice_typer.server.gt64_target", "DEBUG")
    assert log.level == logging.DEBUG


def test_set_module_level_case_insensitive() -> None:
    """GT-64: level name is case-insensitive (``"debug"`` == ``"DEBUG"``)."""
    log = logging.getLogger("voice_typer.server.gt64_case")
    log.setLevel(logging.WARNING)
    set_module_level("voice_typer.server.gt64_case", "warning")
    assert log.level == logging.WARNING


def test_set_module_level_invalid_raises_value_error() -> None:
    """GT-64: an unknown level name raises :class:`ValueError` (rather
    than silently no-op'ing)."""
    with pytest.raises(ValueError):
        set_module_level("voice_typer.server.gt64_bad", "NOPE")


def test_set_module_level_empty_name_raises_value_error() -> None:
    """GT-64: an empty module name raises :class:`ValueError`."""
    with pytest.raises(ValueError):
        set_module_level("", "DEBUG")


def test_get_module_levels_returns_set_overrides() -> None:
    """GT-64: :func:`get_module_levels` returns the dict of explicitly-set
    overrides, with level *names* as values (JSON-serialisable for IPC)."""
    set_module_level("voice_typer.server.gt64_a", "DEBUG")
    set_module_level("voice_typer.server.gt64_b", "WARNING")
    overrides = get_module_levels()
    assert overrides.get("voice_typer.server.gt64_a") == "DEBUG"
    assert overrides.get("voice_typer.server.gt64_b") == "WARNING"


def test_get_module_levels_returns_fresh_dict() -> None:
    """GT-64: mutating the returned dict does not affect internal state."""
    set_module_level("voice_typer.server.gt64_iso", "INFO")
    snap = get_module_levels()
    snap["voice_typer.server.gt64_iso"] = "DEBUG"  # mutate the snapshot
    snap["injected"] = "ERROR"
    # Internal state is unchanged.
    assert get_module_levels().get("voice_typer.server.gt64_iso") == "INFO"
    assert "injected" not in get_module_levels()


def test_set_module_level_emits_info_audit_log(tmp_path: Path, caplog) -> None:
    """GT-64: :func:`set_module_level` emits an INFO log so the change
    is visible in the rotating file (audit trail)."""
    reset()
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    setup_logging(config_dir)
    try:
        with caplog.at_level(logging.INFO, logger="voice_typer.server.log"):
            set_module_level("voice_typer.server.gt64_audit", "DEBUG")
        audit = [r for r in caplog.records if "runtime override" in r.getMessage()]
        assert audit, (
            "GT-64 regression: set_module_level did not emit an INFO audit log"
        )
        assert "gt64_audit" in audit[0].getMessage()
        assert "DEBUG" in audit[0].getMessage()
    finally:
        reset()


def test_get_module_levels_includes_env_var_overrides(tmp_path: Path, monkeypatch) -> None:
    """GT-64: overrides applied via ``VOICE_TYPER_LOG_LEVEL_MODULES`` at
    startup are also visible via :func:`get_module_levels`."""
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
