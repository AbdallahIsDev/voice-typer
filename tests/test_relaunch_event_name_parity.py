"""Regression tests: ``relaunch_app`` event name parity (Python ↔ Tauri)."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _event_bus_source() -> str:
    import voice_typer.server.event_bus as event_bus

    return inspect.getsource(event_bus)


def _tauri_rust_sources() -> str:
    """Concatenate Rust sources that may listen for the relaunch event."""
    src_root = _repo_root() / "src-tauri" / "src"
    parts: list[str] = []
    for path in src_root.rglob("*.rs"):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


class TestEventBusCatalogueListsRelaunchApp:
    """``event_bus.py`` canonical event catalogue lists ``relaunch_app``."""

    def test_catalogue_lists_relaunch_app(self):
        src = _event_bus_source()
        assert "relaunch_app" in src, (
            "event_bus.py must reference 'relaunch_app' (the canonical "
            "event name published by app.py and ipc_server.py)."
        )


class TestTauriHostListensForRelaunchApp:
    """The Tauri Rust host listens for the same wire name."""

    def test_tauri_source_contains_relaunch_app(self):
        src = _tauri_rust_sources()
        assert "relaunch_app" in src, (
            "Tauri host sources must reference 'relaunch_app' (matching the Python publish call)."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
