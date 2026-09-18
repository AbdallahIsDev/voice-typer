"""Autostart sync is a true no-op when already in sync."""

from __future__ import annotations

import logging
from types import SimpleNamespace


def _app(autostart):
    return SimpleNamespace(config=SimpleNamespace(autostart=autostart))


def test_in_sync_does_not_call_enable_or_disable(monkeypatch, caplog):
    from voice_typer.server import startup_tasks
    from voice_typer.server.server_platform import autostart as _autostart

    monkeypatch.setattr(_autostart, "is_autostart_enabled", lambda: True)
    called = {}

    def _boom():
        called["hit"] = True
        return True

    monkeypatch.setattr(_autostart, "enable_autostart", _boom)
    monkeypatch.setattr(_autostart, "disable_autostart", _boom)

    with caplog.at_level(logging.INFO, logger="voice_typer.server.startup_tasks"):
        result = startup_tasks.sync_autostart(_app(True))
    assert called == {}
    assert result["actual_post_sync"] is True
    assert any("already in sync" in r.getMessage() for r in caplog.records)


def test_in_sync_false_does_not_call_either(monkeypatch):
    from voice_typer.server import startup_tasks
    from voice_typer.server.server_platform import autostart as _autostart

    monkeypatch.setattr(_autostart, "is_autostart_enabled", lambda: False)
    called = {}
    monkeypatch.setattr(_autostart, "enable_autostart", lambda: called.setdefault("e", True) or True)
    monkeypatch.setattr(_autostart, "disable_autostart", lambda: called.setdefault("d", True) or True)
    result = startup_tasks.sync_autostart(_app(False))
    assert called == {}
    assert result["actual_post_sync"] is False


def test_task_check_log_reads_as_read_only(caplog):
    import inspect

    from voice_typer.server.server_platform import autostart_windows as _aw

    src = inspect.getsource(_aw._is_app_autostart_task_registered)
    assert "read-only, no write" in src
