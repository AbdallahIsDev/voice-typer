"""Focused tests for the ``AppRecordingInit`` mixin"""

from __future__ import annotations

import logging
import sys
import threading
import types
from unittest.mock import MagicMock

from voice_typer.server.app_recording_init import AppRecordingInit


class _FakeThreadRegistry:
    """Deterministic stand-in for ``ThreadRegistry``: records spawns."""

    def __init__(self) -> None:
        self.spawned: list[tuple[str, object, bool, float]] = []

    def spawn_and_register(self, name, target, *, daemon, join_timeout):
        self.spawned.append((name, target, daemon, join_timeout))


class _Host(AppRecordingInit):
    """Minimal host exercising the mixin without VoiceTyperApp."""

    def __init__(self, registry: _FakeThreadRegistry) -> None:
        self.config = MagicMock()
        self._audio_processor = MagicMock()
        self._thread_registry = registry


def _spawned(registry: _FakeThreadRegistry, name: str):
    for spawned_name, target, daemon, join_timeout in registry.spawned:
        if spawned_name == name:
            return target, daemon, join_timeout
    raise AssertionError(f"thread {name!r} was never spawned; got {[s[0] for s in registry.spawned]}")


class TestInitRecording:
    def test_declares_sentinel_backings_and_event(self):
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._init_recording()

        from voice_typer.server.app_lazy_hub import _RECORDER_MISSING

        assert host._recorder_backing is _RECORDER_MISSING
        assert host._recording_backing is _RECORDER_MISSING
        assert host._recorder_build_error is None
        assert isinstance(host._recorder_build_ready, threading.Event)
        assert not host._recorder_build_ready.is_set()

    def test_spawns_recorder_init_thread_with_historical_flags(self):
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._init_recording()

        _, daemon, join_timeout = _spawned(registry, "recorder-init")
        assert daemon is True
        assert join_timeout == 10.0

    def test_setter_race_guard_never_clobbers_injected_recorder(self):
        """A recorder injected while the background build is in flight"""
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._init_recording()
        # Simulate the setter racing the (not yet run) background build.
        injected = object()
        host._recorder_backing = injected
        target, _, _ = _spawned(registry, "recorder-init")
        target()

        from voice_typer.server.app_lazy_hub import _RECORDER_MISSING

        assert host._recorder_backing is injected
        assert host._recording_backing is _RECORDER_MISSING
        assert host._recorder_build_error is None
        assert host._recorder_build_ready.is_set()

    def test_build_failure_records_error_and_sets_ready_event(self, monkeypatch, caplog):
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        def _boom(*args, **kwargs):
            raise RuntimeError("recording package exploded")

        monkeypatch.setitem(
            sys.modules,
            "voice_typer.server.recording",
            types.SimpleNamespace(Recorder=_boom),
        )

        host._init_recording()
        target, _, _ = _spawned(registry, "recorder-init")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            target()

        assert isinstance(host._recorder_build_error, RuntimeError)
        assert host._recorder_build_ready.is_set()
        assert any(
            "background recorder construction failed" in record.message
            for record in caplog.records
            if record.name == "voice_typer.server.app"
        )


class TestPreloadVadModel:
    """The eager Silero VAD preload is owned by ``StartupSequence``"""

    def test_init_recording_spawns_no_vad_preload(self, monkeypatch):
        """``_init_recording`` no longer arms a duplicate VAD preload —"""
        import voice_typer.server.vad as vad_module

        monkeypatch.setattr(vad_module, "preload", lambda: None)
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._init_recording()

        names = [spawned[0] for spawned in registry.spawned]
        assert "recorder-init" in names
        assert "vad-preload" not in names, (
            "recording-init must not spawn its own vad-preload worker; "
            "the startup sequence phase-1 site owns the single preload"
        )

    def test_vad_preload_helper_is_gone(self):
        """The removed helper must stay removed, a construction-time"""
        assert not hasattr(_Host, "_preload_vad_model")
