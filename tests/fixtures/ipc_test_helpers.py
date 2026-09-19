"""Test helpers for IPC server DI (ARCH-REFAC-004)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def make_fake_app() -> MagicMock:
    """Return a ``MagicMock`` configured to satisfy ``AppProtocol``."""
    app = MagicMock(name="fake_app")

    # Public domain objects, pre-create them so callers can configure
    app.config = MagicMock(name="fake_app.config")
    app.history_db = MagicMock(name="fake_app.history_db")
    app.models = MagicMock(name="fake_app.models")
    app.recording = MagicMock(name="fake_app.recording")
    app.hotkeys = MagicMock(name="fake_app.hotkeys")
    app.recorder = MagicMock(name="fake_app.recorder")
    app.tray = MagicMock(name="fake_app.tray")
    # The lifecycle tray-state hook (``_hook_tray_set_state``) checks
    app.tray.set_state._vt_wrapped = False
    # Per-correction usage tracker (``correction_usage.py``), read by
    app.correction_usage = MagicMock(name="fake_app.correction_usage")

    # Private attributes still accessed by ipc_server / handlers.
    app._ipc_server = None
    # IPC _send shutdown short-circuit logic gates correctly.
    app._shutting_down = False
    app._esc_cancel_paused = False

    return app


def make_fake_service() -> MagicMock:
    """Return a ``MagicMock`` configured to satisfy ``ServiceProtocol``."""
    service = MagicMock(name="fake_service")
    # Pre-populate common return values so basic dispatch tests work
    service.get_status.return_value = {
        "status": "idle",
        "xruns_since_start": 0,
        "loaded_via": "",
    }
    service.get_config.return_value = {"hotkey": "<f2>", "model_size": "small.en"}
    service.get_defaults.return_value = {"hotkey": "<f2>", "model_size": "small.en"}
    service.set_config.return_value = ({}, [])  # (validated, errors)
    service.get_history.return_value = []
    service.get_favorites.return_value = []
    service.search_history.return_value = []
    service.get_today_stats.return_value = {"count": 0, "chars": 0}
    service.get_microphones.return_value = []
    service.refresh_microphones.return_value = []
    service.get_rms_level.return_value = {"rms": 0.0, "peak": 0.0}
    service.get_volume_backend_status.return_value = {
        "is_available": False,
        "backend_name": "fake (test)",
        "supports_per_session": False,
    }
    service.get_model_status.return_value = {}
    service.get_audio_status.return_value = {
        "filter_chain": [],
        "degraded": False,
        "degraded_reasons": [],
        "latency_ms": 0.0,
        "vad_backend": "rms",
        "sample_rate": 16000,
    }
    service.force_cancel_transcription.return_value = {
        "success": True,
        "message": "Transcription cancelled.",
    }
    service.get_vocabulary.return_value = {"entries": []}
    service.save_vocabulary.return_value = {"ok": True}
    service.save_vocabulary_with_diff.return_value = {"ok": True, "added": 0, "removed": 0}
    service.get_templates.return_value = []
    service.save_templates.return_value = True
    return service


# Sentinel for ``make_ipc_server_with_fakes(thread_registry=...)``:
_UNSET_THREAD_REGISTRY = object()


def make_ipc_server_with_fakes(*, thread_registry: Any = _UNSET_THREAD_REGISTRY) -> tuple[Any, MagicMock, MagicMock]:
    """Construct an ``IPCServer`` with a fake app and fake service."""
    # Imported here (not at module top) so importing this fixtures
    from voice_typer.server.ipc_server import IPCServer

    fake_app = make_fake_app()
    fake_service = make_fake_service()
    if thread_registry is not _UNSET_THREAD_REGISTRY:
        fake_app._thread_registry = thread_registry
    server = IPCServer(fake_app, service=fake_service)
    return server, fake_app, fake_service


def make_bare_ipc_server(
    app: MagicMock | None = None,
    service: MagicMock | None = None,
    *,
    send_path: bool = False,
) -> Any:
    """Build a bare ``IPCServer`` via the ``__new__`` bypass."""
    import threading

    from voice_typer.server.ipc.sender import _TCP_PENDING_BUFFER_CAP, _PendingBuffer
    from voice_typer.server.ipc_server import IPCServer

    if app is None:
        app = MagicMock(name="bare_app")
    if not isinstance(getattr(app, "_config_mutation_lock", None), type(threading.RLock())):
        app._config_mutation_lock = threading.RLock()
    if service is None:
        service = MagicMock(name="bare_service")
    server = IPCServer.__new__(IPCServer)
    server.app = app
    server.service = service
    # ``__new__`` skips ``__init__``; ``_dispatch`` acquires this lock.
    server._dispatch_lock = threading.RLock()
    if send_path:
        # Sender-path fixture state, exactly what ``OutputMixin._send``
        app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._pending_tcp = _PendingBuffer(maxlen=_TCP_PENDING_BUFFER_CAP)
        server._tcp_mode = True
        server._cached_shutting_down = False
        server._tcp_client = None
    return server


def make_buffered_mock_tcp_client() -> MagicMock:
    """Mock tcp_client simulating ``_TCPLineIO`` buffer-then-flush."""
    tcp_client = MagicMock()
    tcp_client.conn = MagicMock()
    write_buffer: list[bytes] = []

    def mock_write(text: str | bytes) -> None:
        write_buffer.append(text.encode("utf-8") if isinstance(text, str) else text)

    def mock_flush() -> None:
        if write_buffer:
            tcp_client.conn.sendall(b"".join(write_buffer))
            write_buffer.clear()

    def mock_reset() -> None:
        write_buffer.clear()

    tcp_client.write.side_effect = mock_write
    tcp_client.flush.side_effect = mock_flush
    tcp_client._reset_write_buffer.side_effect = mock_reset
    return tcp_client


def make_fake_sidecar_ws_server(**overrides: Any) -> Any:
    """Return the canonical fake sidecar-WS server for WS transport tests."""
    from tests.fixtures.sidecar_ws_test_helpers import _make_fake_server

    server = _make_fake_server()
    for name, value in overrides.items():
        setattr(server, name, value)
    return server


def make_fake_recorder(**config_overrides: Any) -> Any:
    """Return the canonical minimal ``Recorder`` for secure-clear tests."""
    from tests.fixtures.recorder_test_helpers import make_recorder as _mk

    return _mk(**config_overrides)


__all__ = [
    "make_fake_app",
    "make_fake_service",
    "make_fake_recorder",
    "make_fake_sidecar_ws_server",
    "make_bare_ipc_server",
    "make_buffered_mock_tcp_client",
    "make_ipc_server_with_fakes",
]
