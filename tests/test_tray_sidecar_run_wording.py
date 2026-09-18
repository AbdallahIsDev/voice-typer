"""Sidecar tray run line names the host owner."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace


def _make_tray():
    from voice_typer.server import tray_lifecycle

    tray = SimpleNamespace()
    tray._tray_unavailable = True
    tray._icon = None
    tray._run_event = threading.Event()
    tray._run_event.set()
    tray._drain_pending = lambda: None  # noqa: E731
    return tray, tray_lifecycle


def test_sidecar_run_line_names_host_owner(caplog, monkeypatch):
    tray, mod = _make_tray()
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    with caplog.at_level(logging.INFO, logger="voice_typer.server.tray"):
        mod.run(tray)
    texts = [r.getMessage() for r in caplog.records]
    assert any("TAURI_SIDECAR=1" in t and "Rust host" in t for t in texts)
    assert not any(t.startswith("[TRAY] Tray unavailable") for t in texts)


def test_standalone_run_line_keeps_unavailable_wording(caplog, monkeypatch):
    tray, mod = _make_tray()
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)
    with caplog.at_level(logging.INFO, logger="voice_typer.server.tray"):
        mod.run(tray)
    texts = [r.getMessage() for r in caplog.records]
    assert any(t.startswith("[TRAY] Tray unavailable") for t in texts)
