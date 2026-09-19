"""Pytest fixtures for IPC handler mixin unit tests (CR-12)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.fixtures.ipc_test_helpers import (
    make_fake_app,
    make_fake_service,
)


@pytest.fixture
def fake_app() -> MagicMock:
    """Fresh ``MagicMock`` satisfying ``AppProtocol`` per test."""
    return make_fake_app()


@pytest.fixture
def fake_service() -> MagicMock:
    """Fresh ``MagicMock`` satisfying ``ServiceProtocol`` per test."""
    return make_fake_service()


@pytest.fixture
def ipc_server(fake_app: MagicMock, fake_service: MagicMock):
    """Fresh ``IPCServer`` wired to ``fake_app`` + ``fake_service``."""
    from voice_typer.server.ipc_server import IPCServer

    server = IPCServer(fake_app, service=fake_service)
    fake_app._ipc_server = server
    return server
