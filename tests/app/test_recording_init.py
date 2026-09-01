"""Focused tests for the ``AppRecordingInit`` mixin
(``voice_typer/server/app_recording_init.py``) — the deferred
recorder-subsystem construction slice extracted from ``VoiceTyperApp``.

Covers the mixin's public API on a minimal host class (no real
``Recorder`` / ``RecordingController`` / PortAudio / torch — external
dependencies stubbed), mirroring how ``tests/app/test_dictation.py``
exercises the ``AppDictation`` mixin surface:

- ``_init_recording`` declares the ``_RECORDER_MISSING`` sentinels, the
  build-error slot and the build-ready event, and spawns the
  ``recorder-init`` background thread through the thread registry with
  the historical flags (daemon, join_timeout).
- the setter-race guard: a recorder injected while the background build
  is in flight is never clobbered (early return, no recording import).
- the build-failure path records the exception and still sets the
  build-ready event (first-access surfacing contract).
- ``_preload_vad_model`` spawns the ``vad-preload`` thread with the
  historical flags, and a failing ``vad.preload()`` is swallowed
  (best-effort preload, logged at DEBUG).
- failure logs route to the ``voice_typer.server.app`` logger (the
  sibling-module convention that keeps caplog captures working).
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from unittest.mock import MagicMock

from voice_typer.server.app_recording_init import AppRecordingInit

# Imported lazily inside the tests that need it: importing the real
# vad module pins the ``from voice_typer.server import vad`` binding
# deterministically (see TestPreloadVadModel).


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
        """A recorder injected while the background build is in flight
        wins: the build target returns before importing the recording
        package (no heavy import, no backing overwrite)."""
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
    def test_spawns_vad_preload_thread_with_historical_flags(self, monkeypatch):
        # Canonical patch target: the OWNING submodule's attribute (the
        # worker resolves ``vad.preload`` through the module object at
        # call time). Importing the real module here also pins the
        # ``from voice_typer.server import vad`` binding deterministically
        # regardless of whether an earlier test already imported it.
        import voice_typer.server.vad as vad_module

        monkeypatch.setattr(vad_module, "preload", lambda: None)
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._preload_vad_model()

        _, daemon, join_timeout = _spawned(registry, "vad-preload")
        assert daemon is True
        assert join_timeout == 2.0

    def test_failing_preload_is_best_effort(self, monkeypatch, caplog):
        def _boom():
            raise RuntimeError("torch missing")

        import voice_typer.server.vad as vad_module

        monkeypatch.setattr(vad_module, "preload", _boom)
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._preload_vad_model()
        target, _, _ = _spawned(registry, "vad-preload")
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.app"):
            target()  # must not raise — the worker swallows the failure

        assert any(
            "vad.preload() failed" in record.message
            for record in caplog.records
            if record.name == "voice_typer.server.app"
        )

    def test_init_recording_preloads_vad(self, monkeypatch):
        """``_init_recording`` ends with the VAD preload (the historical
        call order pinned by the docstring)."""
        import voice_typer.server.vad as vad_module

        monkeypatch.setattr(vad_module, "preload", lambda: None)
        registry = _FakeThreadRegistry()
        host = _Host(registry)

        host._init_recording()

        names = [spawned[0] for spawned in registry.spawned]
        assert "recorder-init" in names
        assert "vad-preload" in names
