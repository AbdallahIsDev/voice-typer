"""Pytest fixtures for IPC handler mixin unit tests (CR-12).

Exposes the plain-function helpers in
``tests/fixtures/ipc_test_helpers.py`` as pytest fixtures so each
test gets a fresh ``(IPCServer, fake_app, fake_service)`` triple
without having to call the factory inline.

The ``IPCServer`` is constructed via the ARCH-REFAC-004 DI seam
(``IPCServer(app, service=fake)``) — the injected ``service`` mock
is stored verbatim on ``server.service``, so per-test mutations to
``fake_service.X.return_value`` after this fixture yields are
visible to the server's handlers.

Why not reuse ``make_ipc_server_with_fakes`` directly?
------------------------------------------------------

The factory returns a fresh ``(server, app, service)`` triple per
call, but as a plain function it can't participate in pytest's
fixture dependency graph.  Wrapping it as a fixture lets tests
depend on ``ipc_server`` (and ``fake_app`` / ``fake_service``
separately) and lets other fixtures in this directory consume the
same triple.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.fixtures.ipc_test_helpers import (
    make_fake_app,
    make_fake_service,
)


@pytest.fixture
def fake_app() -> MagicMock:
    """Fresh ``MagicMock`` satisfying ``AppProtocol`` per test.

    Pre-populated with the canonical attribute set declared on
    :class:`voice_typer.server.providers.AppProtocol` so handler
    attribute access (e.g. ``self.app.config.hotkey``) doesn't raise.
    """
    return make_fake_app()


@pytest.fixture
def fake_service() -> MagicMock:
    """Fresh ``MagicMock`` satisfying ``ServiceProtocol`` per test.

    Pre-populated with sensible return values for the common
    read-only service methods (``get_status``, ``get_config``,
    ``get_history`` …) so basic dispatch tests work without per-test
    configuration.  Override any return value after the fixture
    yields (e.g. ``fake_service.get_status.return_value = {...}``).
    """
    return make_fake_service()


@pytest.fixture
def ipc_server(fake_app: MagicMock, fake_service: MagicMock):
    """Fresh ``IPCServer`` wired to ``fake_app`` + ``fake_service``.

    The DI seam in ``IPCServer.__init__`` stores the injected
    ``service`` verbatim (no ``VoiceTyperService`` is constructed),
    so per-test mutations to ``fake_service`` after this fixture
    yields are visible to the server's handlers.

    The heavy-imports mock fixture (``mock_heavy_imports`` in
    ``tests/conftest.py``, autouse) has already run by the time this
    fixture body executes, so the lazy import of
    ``voice_typer.server.ipc_server`` below succeeds even on a
    headless Linux container without ``pystray`` / ``sounddevice``
    installed.
    """
    from voice_typer.server.ipc_server import IPCServer

    server = IPCServer(fake_app, service=fake_service)
    # Mirror what IPCServer.start() does: set the back-reference so
    # handlers that read ``self.app._ipc_server`` (e.g. push-event
    # helpers) don't see None.
    fake_app._ipc_server = server
    return server
