"""Tests for ``WaylandHotkey``'s no-client grace-period warning."""

from __future__ import annotations

import logging
import os
import socket as _socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.hotkeys.wayland import WaylandHotkey

_AF_UNIX_PATH_TOO_LONG = pytest.mark.skipif(
    sys.platform != "linux" or len(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) > 90,
    reason=("AF_UNIX socket tests are Linux-only (Wayland), VALIDATE ON HOST with short XDG_RUNTIME_DIR"),
)


def _make_tmp_xdg(tmp_path: Path) -> str:
    """Return a SHORT tmp dir suitable for ``$XDG_RUNTIME_DIR``."""
    import tempfile

    xdg = Path(tempfile.gettempdir()) / f"vt-xdg-{os.getpid()}"
    xdg.mkdir(mode=0o700, exist_ok=True)
    return str(xdg)


@pytest.fixture
def xdg_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``$XDG_RUNTIME_DIR`` to a per-test tmp dir."""
    xdg = _make_tmp_xdg(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", xdg)
    return xdg


def test_no_client_grace_seconds_is_30() -> None:
    """
    The grace period is 30s (matches the pynput-fallback timeout).
    This pins the contract: if anyone changes the constant, this test
    """
    assert WaylandHotkey.NO_CLIENT_GRACE_SECONDS == 30.0


@_AF_UNIX_PATH_TOO_LONG
def test_start_schedules_no_client_timer(xdg_runtime: str) -> None:
    """``start()`` must schedule the no-client grace timer."""
    backend = WaylandHotkey("<f8>")
    backend.start(lambda: None)
    try:
        assert backend._no_client_timer is not None, (
            "start() must schedule _no_client_timer so the no-client warning can fire after the grace period."
        )
        assert backend._no_client_timer.is_alive(), "the no-client grace timer must be running after start()."
    finally:
        backend.stop()


@_AF_UNIX_PATH_TOO_LONG
def test_no_client_warning_fires_after_grace(xdg_runtime: str, caplog: pytest.LogCaptureFixture) -> None:
    """When no client connects within the grace period, an actionable"""
    backend = WaylandHotkey("<f8>")
    # Shorten the grace period so the test is fast.
    backend.NO_CLIENT_GRACE_SECONDS = 0.05  # type: ignore[misc]
    with caplog.at_level(logging.WARNING, logger="voice_typer.server.hotkeys"):
        backend.start(lambda: None)
        # Wait for the timer to fire (50ms + buffer).
        deadline = time.monotonic() + 2.0
        while backend._no_client_timer is not None and time.monotonic() < deadline:
            time.sleep(0.02)
    try:
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Wayland Hotkey Idle" in r.getMessage() for r in warnings), (
            "no-client warning must be logged with the 'Wayland Hotkey Idle' "
            f"title; got warnings: {[r.getMessage() for r in warnings]}"
        )
        assert any("voice-typer-hotkey.sock" in r.getMessage() for r in warnings), (
            "no-client warning must mention the socket path so the user knows where to send commands."
        )
        assert any("linux-key-listener" in r.getMessage() for r in warnings), (
            "no-client warning must mention the install hint (linux-key-listener) so the user knows how to fix it."
        )
        # The flag must still be False (no client ever connected).
        assert not backend._client_ever_connected.is_set(), (
            "_client_ever_connected must remain False when no IPC client ever connected during the grace period."
        )
    finally:
        backend.stop()


@_AF_UNIX_PATH_TOO_LONG
def test_no_client_callback_invoked(xdg_runtime: str) -> None:
    """When a callback is registered via ``set_no_client_callback``,"""
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 0.05  # type: ignore[misc]
    captured: list[tuple[str, str]] = []
    backend.set_no_client_callback(lambda title, message: captured.append((title, message)))
    backend.start(lambda: None)
    # Wait for the timer to fire.
    deadline = time.monotonic() + 2.0
    while backend._no_client_timer is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    backend.stop()
    assert len(captured) == 1, f"no-client callback must be invoked exactly once; got {captured}"
    title, message = captured[0]
    assert "Wayland Hotkey Idle" in title
    assert "voice-typer-hotkey.sock" in message
    assert "linux-key-listener" in message


@_AF_UNIX_PATH_TOO_LONG
def test_no_client_timer_canceled_on_client_connect(xdg_runtime: str) -> None:
    """When an IPC client connects, the no-client grace timer is canceled"""
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 5.0  # type: ignore[misc], long grace; we'll connect immediately
    backend.start(lambda: None)
    try:
        socket_path = backend.SOCKET_PATH
        assert socket_path is not None, "XDG_RUNTIME_DIR must be set for this test"
        # Connect a real client and send "ping".
        client = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(socket_path)
        client.sendall(b"ping")
        response = client.recv(1024)
        client.close()
        assert response == WaylandHotkey.PING_RESPONSE, (
            f"ping must be answered with {WaylandHotkey.PING_RESPONSE!r}; got {response!r}"
        )
        # Give the accept loop a moment to process the connection +
        deadline = time.monotonic() + 2.0
        while backend._no_client_timer is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert backend._no_client_timer is None, "no-client timer must be canceled after the first client connects."
        assert backend._client_ever_connected.is_set(), "_client_ever_connected must be True after a client connected."
    finally:
        backend.stop()


@_AF_UNIX_PATH_TOO_LONG
def test_stop_cancels_no_client_timer(xdg_runtime: str) -> None:
    """``stop()`` must cancel the no-client grace timer so app shutdown"""
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 5.0  # type: ignore[misc], long grace; we'll stop before it fires
    backend.start(lambda: None)
    assert backend._no_client_timer is not None
    backend.stop()
    assert backend._no_client_timer is None, (
        "stop() must clear _no_client_timer so the warning can't fire after teardown."
    )


@_AF_UNIX_PATH_TOO_LONG
def test_stop_prevents_warning_from_firing(xdg_runtime: str, caplog: pytest.LogCaptureFixture) -> None:
    """If ``stop()`` is called during the grace period, the timer's"""
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 1.0  # type: ignore[misc], long enough that stop() wins the race
    with caplog.at_level(logging.WARNING, logger="voice_typer.server.hotkeys"):
        backend.start(lambda: None)
        # Stop immediately, well before the 1.0s grace elapses.
        backend.stop()
        # Wait long enough that the timer WOULD have fired (1.0s grace
        time.sleep(1.5)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Wayland Hotkey Idle" in r.getMessage()]
    assert not warnings, (
        "stop() during the grace period must prevent the no-client "
        f"warning from firing; got: {[r.getMessage() for r in warnings]}"
    )


def test_diagnose_reports_client_ever_connected(xdg_runtime: str) -> None:
    """onboarding flow + diagnostics can tell apart \"socket listening +"""
    backend = WaylandHotkey("<f8>")
    diag = backend.diagnose()
    assert "client_ever_connected=False" in diag, (
        f"diagnose() must report client_ever_connected=False before any client connects; got: {diag}"
    )


@_AF_UNIX_PATH_TOO_LONG
def test_diagnose_reports_true_after_client_connects(xdg_runtime: str) -> None:
    """After a client connects, ``diagnose()`` must report"""
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 5.0  # type: ignore[misc]
    backend.start(lambda: None)
    try:
        socket_path = backend.SOCKET_PATH
        assert socket_path is not None
        client = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(socket_path)
        client.sendall(b"ping")
        client.recv(1024)
        client.close()
        # Wait for the accept loop to set the flag.
        deadline = time.monotonic() + 2.0
        while not backend._client_ever_connected.is_set() and time.monotonic() < deadline:
            time.sleep(0.02)
        diag = backend.diagnose()
        assert "client_ever_connected=True" in diag, (
            f"diagnose() must report client_ever_connected=True after a client connected; got: {diag}"
        )
    finally:
        backend.stop()


@_AF_UNIX_PATH_TOO_LONG
def test_callback_exception_does_not_crash_timer(xdg_runtime: str, caplog: pytest.LogCaptureFixture) -> None:
    """If the registered callback raises, the timer thread must NOT"""
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 0.05  # type: ignore[misc]

    def raising_callback(title: str, message: str) -> None:
        raise RuntimeError("simulated tray.notify_safety failure")

    backend.set_no_client_callback(raising_callback)
    with caplog.at_level(logging.WARNING, logger="voice_typer.server.hotkeys"):
        backend.start(lambda: None)
        deadline = time.monotonic() + 2.0
        while backend._no_client_timer is not None and time.monotonic() < deadline:
            time.sleep(0.02)
    backend.stop()
    # The original warning must still be logged.
    warnings = [r for r in caplog.records if "Wayland Hotkey Idle" in r.getMessage()]
    assert warnings, "the no-client warning must be logged even if the callback raises"
    callback_failures = [r for r in caplog.records if "no-client callback raised" in r.getMessage()]
    assert callback_failures, "callback failures must be logged so the operator knows the tray notification was lost."


def test_set_no_client_callback_signature() -> None:
    """``set_no_client_callback`` must accept a (title, message)"""
    backend = WaylandHotkey("<f8>")
    mock_tray = MagicMock()
    backend.set_no_client_callback(mock_tray.notify_safety)
    assert backend._on_no_client is mock_tray.notify_safety
