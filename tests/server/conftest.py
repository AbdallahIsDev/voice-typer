"""Shared fixtures and mocks for the split IPC server test suite."""

import dataclasses
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server import event_bus, ipc_server  # noqa: E402
from voice_typer.server.ipc_server import (  # noqa: E402
    _TCP_WRITE_TIMEOUT_SECONDS,
    IPCServer,
)
from voice_typer.server.tray import AppState  # noqa: E402


@dataclasses.dataclass
class MockConfig:
    """Minimal config mock with __dict__ and save()."""

    hotkey: str = "<f2>"
    model_size: str = "tiny"
    device: str = "cuda"
    language: str = "en"
    # Secret/credential + path fields the sanitizer redacts, declared
    cloud_api_key: str | None = None
    cloud_api_url: str | None = None
    cloud_model: str | None = None
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    deepgram_api_key: str | None = None
    llm_api_key: str | None = None
    qwen_model_path: str | None = None
    parakeet_model_path: str | None = None
    corrections_path: str | None = None
    _saved: bool = False

    def save(self):
        self._saved = True

    def save_strict(self):
        self._saved = True

    def __getattr__(self, name):
        return None


class MockHistoryDB:
    """Minimal history db mock."""

    def get_recent(
        self,
        limit=50,
        offset=0,
        *,
        raise_on_error=False,
        before_timestamp=None,
        before_id=None,
    ):
        return [
            {"id": 1, "text": "hello world", "timestamp": "2025-01-01"},
        ]

    def get_today_stats(self, *, raise_on_error=False):
        return {"count": 5, "chars": 240}


class MockTray:
    """Minimal tray mock that tracks set_state calls."""

    def __init__(self):
        self.state = AppState.IDLE
        self.set_state_calls = []

    def set_state(self, state, message=""):
        self.set_state_calls.append((state, message))
        self.state = state


class MockApp:
    """Minimal LausuApp mock for IPC server tests."""

    def __init__(self):
        self.tray = MockTray()
        self.config = MockConfig()
        self.history_db = MockHistoryDB()
        self._microphones = [
            {"id": "0", "name": "Microphone (Realtek Audio)"},
            {"id": "1", "name": "Microphone (USB Camera)"},
        ]
        self.toggle_called = False
        self.restart_called = False
        self.quit_called = False
        # Volume ducker mock, the get_volume_backend_status IPC
        self._volume_ducker = MagicMock()
        self._volume_ducker.is_available = True
        self._volume_ducker.backend_name = "fake (test)"
        self._volume_ducker.supports_per_session = False
        self._volume_ducker.initialize = MagicMock(return_value=True)
        # RACE-011: the IPC set_config handler acquires this lock to
        self._config_mutation_lock = threading.RLock()
        self.hotkeys = MagicMock()

    def toggle_dictation(self):
        self.toggle_called = True

    def restart_app(self):
        self.restart_called = True

    def quit_app(self):
        self.quit_called = True

    def change_model(self, model_size: str) -> None:
        self.config.model_size = model_size

    def set_active_backend(self, backend: str) -> None:
        self.config.asr_backend = backend


@pytest.fixture
def mock_app():
    return MockApp()


@pytest.fixture
def server(mock_app):
    return IPCServer(mock_app)


@pytest.fixture
def server_with_mock_app():
    """Construct an IPCServer with a mocked app (no real LausuApp)."""
    app = MagicMock()
    # Avoid the service.py import side-effects on real LausuApp.
    srv = IPCServer(app)
    return srv


@pytest.fixture
def server_with_mock_app_for_push_events():
    """
    IPCServer with a mocked app, scoped to push-event / ack-shape tests.
    C-STYLE-1 (no ticket numbers in source identifiers). Same
    """
    app = MagicMock()
    return IPCServer(app)


@pytest.fixture
def server_with_mock_app_for_tcp_io():
    """
    IPCServer with a mocked app, scoped to TCP send-lock-split /
    C-STYLE-1 (no ticket numbers in source identifiers). Same
    """
    app = MagicMock()
    return IPCServer(app)


@pytest.fixture
def clean_registry():
    """Snapshot and clear the push-event registry for the test, restore after."""
    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


__all__ = [
    "AppState",
    "IPCServer",
    "MockApp",
    "MockConfig",
    "MockHistoryDB",
    "MockTray",
    "_TCP_WRITE_TIMEOUT_SECONDS",
    "event_bus",
    "ipc_server",
    "mock_app",
    "server",
    "server_with_mock_app",
    "server_with_mock_app_for_push_events",
    "server_with_mock_app_for_tcp_io",
    "clean_registry",
]
