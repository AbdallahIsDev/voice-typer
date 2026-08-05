"""Shared fixtures and mocks for the split IPC server test suite.

This conftest.py centralizes the Mock* helper classes that were previously
inlined at the top of the (now-deleted) monolithic ``tests/test_server.py``.

Exports (as plain Python classes — tests can instantiate them directly):

- :class:`MockConfig`     — minimal Config mock with __dict__ + save()
- :class:`MockHistoryDB`  — minimal history-db mock
- :class:`MockTray`       — minimal tray mock tracking set_state calls
- :class:`MockApp`        — minimal VoiceTyperApp mock for IPC server tests

Pytest fixtures provided:

- :func:`mock_app`                  — fresh MockApp()
- :func:`server`                    — IPCServer(mock_app)
- :func:`server_with_mock_app`      — IPCServer backed by a MagicMock app
- :func:`server_with_mock_app_for_push_events` — same, scoped for push-event / ack-shape tests
- :func:`server_with_mock_app_for_tcp_io`     — same, scoped for TCP send-lock-split / write-timeout tests
- :func:`clean_registry`            — snapshot/clear/restore the push-event
                                       registry around a test

Note: ``tmp_config_dir`` is intentionally NOT defined here — the
canonical fixture in ``tests/conftest.py`` (which patches BOTH
``voice_typer.server.config._config_dir`` AND
``voice_typer.server.app._config_dir``) is picked up automatically.
A previous local shadow patched only ``config._config_dir`` and
silently let ``app.py`` paths write to the real user config dir.
"""

import dataclasses
import sys
import threading
from unittest.mock import MagicMock

import pytest

# ── Module-level pystray mock ───────────────────────────────────────────
#
# This module-level ``sys.modules.setdefault("pystray", ...)`` is a
# deliberate, documented exception to the parent ``tests/conftest.py``'s
# warning against collection-time ``sys.modules`` mutations (see the
# ``real_pil`` eviction block in ``mock_heavy_imports`` for why PIL
# collection-time setdefaults are harmful). It is kept here for two
# reasons:
#
# 1. The ``from voice_typer.server import event_bus, ipc_server`` import
#    below is intentionally module-level because ``event_bus`` and
#    ``ipc_server`` are re-exported via ``__all__`` — sibling test
#    modules (e.g. ``tests/server/test_push_events.py``) import them
#    back out as ``from tests.server.conftest import event_bus,
#    ipc_server``. Deferring the import into fixtures would break that
#    re-export contract, so the deferred-import alternative is not
#    viable here.
#
# 2. ``voice_typer.server.tray`` currently uses ``lazy_module("pystray")``
#    (PERF-COLDSTART-001), so the import chain below does NOT eagerly
#    pull in ``pystray`` today — meaning the setdefault is technically
#    dead code right now. It is retained as a defensive safety net:
#    (a) the same ``_mock_pystray`` idiom is used by 17+ sibling test
#    modules (``tests/test_tray.py``, ``tests/test_e2e_pipeline.py``, …)
#    so removing it here would be inconsistent with the project-wide
#    convention without removing it everywhere;
#    (b) if a future refactor re-eagerifies the pystray import inside
#    ``tray.py`` or ``ipc_server.py``, this setdefault prevents a
#    headless-CI crash (pystray's xorg backend tries to connect to an
#    X display at import time on Linux) before any fixture can run.
#
# PIL is NOT mocked at module level here. tray.py and ipc_server.py use
# lazy imports for PIL (via tray_icon._get_pil_image), so PIL is never
# imported at module load time. Mocking it here would permanently
# pollute ``sys.modules`` and break later tests that need real PIL
# (e.g. tests/test_tray_icon.py with @pytest.mark.real_pil).
#
# The autouse session-scoped ``mock_heavy_imports_session`` fixture in
# ``tests/conftest.py`` also installs a ``MagicMock()`` for ``pystray``
# once the first test runs (after collection), so the ``_mock_pystray``
# below only bridges the collection-time window before that fixture fires.
_mock_pystray = MagicMock()
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

# ── Server imports (must run after the pystray setdefault above) ─────────
# noqa: E402 -- intentional late import after sys.modules patching
from voice_typer.server import event_bus, ipc_server  # noqa: E402
from voice_typer.server.ipc_server import (  # noqa: E402
    _TCP_WRITE_TIMEOUT_SECONDS,
    IPCServer,
)
from voice_typer.server.tray import AppState  # noqa: E402

# ── Mock helper classes ─────────────────────────────────────────────────


@dataclasses.dataclass
class MockConfig:
    """Minimal config mock with __dict__ and save().

    A real ``@dataclass`` (not a plain class) because the IPC
    ``get_config`` path sanitizes via ``dataclasses.asdict()``
    (config_sanitizer.sanitize_config_for_ipc) — a plain-class double
    raises ``TypeError: asdict() should be called on dataclass
    instances``. Extra attributes the production Config has (API keys,
    paths, ...) resolve to None via ``__getattr__``.
    """

    hotkey: str = "<f2>"
    model_size: str = "small.en"
    device: str = "cuda"
    language: str = "en"
    # Secret/credential + path fields the sanitizer redacts — declared
    # as real fields so ``dataclasses.asdict`` includes them (the
    # redaction tests assert the redacted sentinel appears in the
    # get_config payload).
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
    """Minimal VoiceTyperApp mock for IPC server tests."""

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
        # Volume ducker mock — the get_volume_backend_status IPC
        # handler reads `app._volume_ducker` to populate the response.
        # Fields: is_available, backend_name, supports_per_session, initialize().
        self._volume_ducker = MagicMock()
        self._volume_ducker.is_available = True
        self._volume_ducker.backend_name = "fake (test)"
        self._volume_ducker.supports_per_session = False
        self._volume_ducker.initialize = MagicMock(return_value=True)
        # RACE-011: the IPC set_config handler acquires this lock to
        # serialize Config mutations. VoiceTyperApp initializes it in
        # __init__; MockApp must do the same so the IPC handler doesn't
        # AttributeError.
        self._config_mutation_lock = threading.RLock()
        # Phase 2: service.apply_config_side_effects now calls
        # `app.hotkeys.register_esc()` / `unregister_esc()` /
        # `register_repaste()` directly (instead of going through the
        # `app._register_*_hotkey` delegates). Mock the dispatcher so
        # the IPC handler doesn't AttributeError.
        self.hotkeys = MagicMock()

    def toggle_dictation(self):
        self.toggle_called = True

    def restart_app(self):
        self.restart_called = True

    def quit_app(self):
        self.quit_called = True

    # _handle_set_config routes model_size + asr_backend
    # changes through ``app.change_model()`` / ``app.set_active_backend()``
    # (via the service layer) so the active engine hot-swaps without an
    # app restart. MockApp previously lacked these methods, so any
    # set_config payload containing model_size or asr_backend raised
    # AttributeError, the handler caught it, dropped the key, and the
    # test assertion ``mock_app.config.model_size == "medium.en"``
    # failed. Add minimal stubs that just update self.config — the
    # dispatch tests don't exercise the real engine-swap path.
    def change_model(self, model_size: str) -> None:
        self.config.model_size = model_size

    def set_active_backend(self, backend: str) -> None:
        self.config.asr_backend = backend


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_app():
    return MockApp()


@pytest.fixture
def server(mock_app):
    return IPCServer(mock_app)


@pytest.fixture
def server_with_mock_app():
    """Construct an IPCServer with a mocked app (no real VoiceTyperApp)."""
    app = MagicMock()
    # Avoid the service.py import side-effects on real VoiceTyperApp.
    # The IPCServer constructor only needs `app` to attach to .service.
    srv = IPCServer(app)
    return srv


@pytest.fixture
def server_with_mock_app_for_push_events():
    """IPCServer with a mocked app — scoped to push-event / ack-shape tests.

    Renamed from the previous task-ID-suffixed form to comply with
    C-STYLE-1 (no ticket numbers in source identifiers). Same
    implementation as :func:`server_with_mock_app` — the domain-suffix
    is purely for test-suite readability.
    """
    app = MagicMock()
    return IPCServer(app)


@pytest.fixture
def server_with_mock_app_for_tcp_io():
    """IPCServer with a mocked app — scoped to TCP send-lock-split /
    write-timeout tests.

    Renamed from the previous task-ID-suffixed form to comply with
    C-STYLE-1 (no ticket numbers in source identifiers). Same
    implementation as :func:`server_with_mock_app` — the domain-suffix
    is purely for test-suite readability.
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


# ── Re-exports for tests that need direct access ────────────────────────
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
