"""Tests for :mod:`voice_typer.server.startup_timeline`.

The Electron main process stamps ``VOICE_TYPER_BOOT_EPOCH_MS`` (at
main-bundle eval) and ``VOICE_TYPER_SPAWN_EPOCH_MS`` (right before
spawning the Python backend) into the backend's environment.
``log_launch_timeline`` merges them into ONE startup line attributing
the spawn→first-log gap (electron boot vs backend interpreter +
imports). These tests pin:

1. Both markers present → single INFO line with both segments.
2. No markers (standalone / Tauri-WS launch) → nothing logged.
3. Partial markers → only the present segment is logged.
4. Garbage marker values are skipped without raising.
5. Negative deltas (clock skew) clamp to `` 0.0s``.
"""

from __future__ import annotations

import logging
import time

import pytest
from voice_typer.server import startup_timeline as st
from voice_typer.server.duration import format_duration


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(st.BOOT_EPOCH_ENV, raising=False)
    monkeypatch.delenv(st.SPAWN_EPOCH_ENV, raising=False)


def _records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "Launch timeline" in r.getMessage()]


def test_both_markers_emit_single_line(caplog, monkeypatch):
    now_ms = int(time.time() * 1000)
    monkeypatch.setenv(st.BOOT_EPOCH_ENV, str(now_ms - 1800))
    monkeypatch.setenv(st.SPAWN_EPOCH_ENV, str(now_ms - 600))
    with caplog.at_level(logging.INFO, logger="test.timeline"):
        st.log_launch_timeline(logging.getLogger("test.timeline"))
    recs = _records(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert "[STARTUP] Launch timeline:" in msg
    assert f"electron boot{format_duration(1.8)}" in msg
    assert f"backend init{format_duration(0.6)}" in msg


def test_no_markers_is_a_noop(caplog):
    with caplog.at_level(logging.INFO, logger="test.timeline"):
        st.log_launch_timeline(logging.getLogger("test.timeline"))
    assert _records(caplog) == []


def test_partial_marker_logs_only_that_segment(caplog, monkeypatch):
    monkeypatch.setenv(st.SPAWN_EPOCH_ENV, str(int(time.time() * 1000) - 250))
    with caplog.at_level(logging.INFO, logger="test.timeline"):
        st.log_launch_timeline(logging.getLogger("test.timeline"))
    recs = _records(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert "backend init" in msg
    assert "electron boot" not in msg


def test_garbage_marker_is_skipped(caplog, monkeypatch):
    monkeypatch.setenv(st.BOOT_EPOCH_ENV, "not-a-number")
    monkeypatch.setenv(st.SPAWN_EPOCH_ENV, str(int(time.time() * 1000) - 100))
    with caplog.at_level(logging.INFO, logger="test.timeline"):
        st.log_launch_timeline(logging.getLogger("test.timeline"))
    recs = _records(caplog)
    assert len(recs) == 1
    assert "electron boot" not in recs[0].getMessage()


def test_negative_delta_clamps_to_zero(caplog, monkeypatch):
    monkeypatch.setenv(st.SPAWN_EPOCH_ENV, str(int(time.time() * 1000) + 60_000))
    with caplog.at_level(logging.INFO, logger="test.timeline"):
        st.log_launch_timeline(logging.getLogger("test.timeline"))
    msg = _records(caplog)[0].getMessage()
    assert "backend init 0.0s" in msg
