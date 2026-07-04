"""Test helpers for IPC server DI (ARCH-REFAC-004).

This module provides ready-made fakes that satisfy
:class:`voice_typer.server.providers.AppProtocol` and
:class:`voice_typer.server.providers.ServiceProtocol`.  Tests that want
to exercise :class:`voice_typer.server.ipc_server.IPCServer` in
isolation (without coupling to the real ``VoiceTyperApp`` /
``VoiceTyperService``) can use these helpers instead of building a
``MagicMock`` from scratch each time.

Why a separate module?
----------------------

Before ARCH-REFAC-004, every test that touched the IPC layer built its
own ``MockApp`` / ``MagicMock`` fixture inline (see
``tests/test_server.py:MockApp`` for the canonical example).  The
fixtures drifted: some included ``_volume_ducker``, some didn't; some
stuffed values into ``config.__dict__``, some used ``MockConfig``.
The drift made it hard to add a new ``self.app.X`` access in a handler
without breaking half the IPC tests.

These helpers provide a single, canonical fake that mirrors
``AppProtocol`` exactly.  When a handler starts reading a new
``self.app.X`` field, the introspection regression test in
``tests/test_di_providers.py`` fails — and ``make_fake_app`` is the
single place to update so every test gets the new attribute.

Usage
-----

::

    from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

    def test_something():
        server, fake_app, fake_service = make_ipc_server_with_fakes()
        result = server._dispatch({"type": "get_status"})
        assert result["type"] == "status"
        fake_service.get_status.assert_called_once()

The fakes are plain ``MagicMock`` instances, so assertions use the
standard mock API (``assert_called_once``, ``return_value``, etc.).
"""

from __future__ import annotations

import threading
from typing import Any, Tuple
from unittest.mock import MagicMock


def make_fake_app() -> MagicMock:
    """Return a ``MagicMock`` configured to satisfy ``AppProtocol``.

    The returned mock has every attribute named in
    :class:`voice_typer.server.providers.AppProtocol` pre-populated
    with a sensible child mock so attribute access (e.g.
    ``fake_app.config.hotkey``) doesn't AttributeError and behaves
    like a real attribute (not an auto-created child mock that would
    make ``assert_called_once`` flaky).

    Specifically, the following attributes are explicitly configured:

    - ``config``, ``history_db``, ``models``, ``recording``,
      ``hotkeys``, ``recorder``, ``tray``: child ``MagicMock()``.
    - ``_audio_processor``, ``_volume_ducker``: child ``MagicMock()``.
    - ``_ipc_server``: ``None`` (matches the real app's state before
      ``IPCServer.start()`` sets the back-reference).
    - ``_config_mutation_lock``: a real ``threading.RLock()`` — the
      IPC ``set_config`` handler uses ``with self.app._config_mutation_lock:``
      and a MagicMock's ``__enter__`` / ``__exit__`` would work but a
      real RLock better exercises the lock semantics (a test that
      tries to acquire it re-entrantly from the same thread should
      succeed, which RLock does and a MagicMock wouldn't naturally
      validate).
    - ``_shutting_down``: ``False`` (matches the real app's running
      state — the IPC ``_send`` path checks
      ``getattr(self.app, '_shutting_down', False) is True`` and a
      child mock would be truthy but not ``is True``, so the shutdown
      short-circuit must be tested by setting ``_shutting_down = True``
      explicitly).

    The methods declared on ``AppProtocol`` (``change_model``,
    ``toggle_dictation``, ``undo_last``, ``repaste_last``,
    ``restart_app``, ``quit_app``, ``start``) are auto-stubbed by
    ``MagicMock`` — calling them returns a child mock and records the
    call for later assertion.  No explicit configuration is needed.

    Returns
    -------
    MagicMock
        A mock that satisfies ``AppProtocol`` structurally.  Caller
        is free to override any attribute (e.g.
        ``fake_app.config.model_size = "tiny"``) before passing the
        mock to ``IPCServer(app, service=fake_service)``.
    """
    app = MagicMock(name="fake_app")

    # Public domain objects — pre-create them so callers can configure
    # them (e.g. ``fake_app.config.hotkey = "<f3>"``) without fighting
    # MagicMock's auto-child behavior.
    app.config = MagicMock(name="fake_app.config")
    app.history_db = MagicMock(name="fake_app.history_db")
    app.models = MagicMock(name="fake_app.models")
    app.recording = MagicMock(name="fake_app.recording")
    app.hotkeys = MagicMock(name="fake_app.hotkeys")
    app.recorder = MagicMock(name="fake_app.recorder")
    app.tray = MagicMock(name="fake_app.tray")

    # Private attributes accessed by handlers / IPC server.
    app._audio_processor = MagicMock(name="fake_app._audio_processor")
    app._volume_ducker = MagicMock(name="fake_app._volume_ducker")
    # _ipc_server is set by IPCServer.start(); pre-None to match real app.
    app._ipc_server = None
    # Real RLock so `with` semantics are exercised correctly.
    app._config_mutation_lock = threading.RLock()
    # _shutting_down must be `False` (not a truthy child mock) so the
    # IPC _send shutdown short-circuit logic gates correctly.
    app._shutting_down = False
    # _esc_cancel_paused must be `False` so the ESC cancel handler
    # doesn't skip cancel when the frontend isn't in capture mode.
    app._esc_cancel_paused = False

    return app


def make_fake_service() -> MagicMock:
    """Return a ``MagicMock`` configured to satisfy ``ServiceProtocol``.

    The returned mock auto-stubs every method declared on
    :class:`voice_typer.server.providers.ServiceProtocol`.  By
    default, calling any of those methods returns a child ``MagicMock``
    (which is truthy and iterable, satisfying most assertion patterns).

    Tests that need a specific return value (e.g.
    ``fake_service.get_status.return_value = {"status": "recording"}``)
    can set it after construction.

    Returns
    -------
    MagicMock
        A mock that satisfies ``ServiceProtocol`` structurally.
    """
    service = MagicMock(name="fake_service")
    # Pre-populate common return values so basic dispatch tests work
    # without per-test configuration.  Tests that need different
    # values can override these after construction.
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
    service.get_vocabulary.return_value = {"entries": []}
    service.save_vocabulary.return_value = {"ok": True}
    service.save_vocabulary_with_diff.return_value = {"ok": True, "added": 0, "removed": 0}
    service.get_templates.return_value = []
    service.save_templates.return_value = True
    service.export_diagnostics.return_value = {"path": "<fake>"}
    return service


def make_ipc_server_with_fakes() -> Tuple[Any, MagicMock, MagicMock]:
    """Construct an ``IPCServer`` with a fake app and fake service.

    This is the canonical DI-mode construction for tests that want to
    exercise the IPC dispatch layer (``_dispatch``, ``_send``, the
    ``_handle_*`` mixins) without coupling to ``VoiceTyperApp`` /
    ``VoiceTyperService`` internals.

    The returned server has:

    - ``server.app`` — the fake app from :func:`make_fake_app`
    - ``server.service`` — the fake service from :func:`make_fake_service`
      (NOT a real ``VoiceTyperService``; the DI seam in
      ``IPCServer.__init__`` stored it verbatim).

    Returns
    -------
    tuple
        ``(server, fake_app, fake_service)`` — the server is ready to
        ``start()`` (or just to call ``_dispatch`` on directly, which
        is the typical test pattern).  The fake app and service are
        returned so the test can configure return values and assert
        on calls.
    """
    # Imported here (not at module top) so importing this fixtures
    # module doesn't transitively import the entire server stack
    # (which would slow down test collection and could fail if e.g.
    # pystray isn't mocked yet).  The conftest.py autouse fixture
    # mocks heavy imports before any test runs, so by the time this
    # function is called, ``voice_typer.server.ipc_server`` is safe
    # to import.
    from voice_typer.server.ipc_server import IPCServer

    fake_app = make_fake_app()
    fake_service = make_fake_service()
    server = IPCServer(fake_app, service=fake_service)
    return server, fake_app, fake_service


__all__ = [
    "make_fake_app",
    "make_fake_service",
    "make_ipc_server_with_fakes",
]
