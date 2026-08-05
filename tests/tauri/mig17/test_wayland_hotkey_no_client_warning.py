"""Tests for ``WaylandHotkey``'s no-client grace-period warning.

When the Wayland hotkey backend is selected (Linux + Wayland session,
native evdev binary unavailable — e.g. aarch64 Linux per XPLAT-11),
``WaylandHotkey`` opens a Unix socket and waits for external tools
(systemd, wlr-which-key, shell wrappers) to send ``toggle``/``ping``
commands. If no client ever connects, the dictation hotkey silently
does nothing — the socket is alive but nobody writes to it, and the
pynput fallback silently no-ops on Wayland.

These tests pin the no-client detection contract:

1. ``start()`` schedules a grace timer (``NO_CLIENT_GRACE_SECONDS``).
2. If no IPC client connects within the grace period, an actionable
   WARNING is logged AND the optional ``_on_no_client`` callback is
   invoked (so callers can surface a tray notification).
3. The timer is canceled as soon as the first client connects (so a
   late-connecting client doesn't trigger a spurious warning).
4. ``stop()`` cancels the timer (so app shutdown during the grace
   period doesn't fire a spurious warning).
5. ``diagnose()`` reports ``client_ever_connected`` so the onboarding
   flow + diagnostics can tell apart "socket listening + clients
   active" from "socket listening but nobody sending commands".

These tests run on any platform — they construct ``WaylandHotkey``
directly and manipulate ``$XDG_RUNTIME_DIR`` + the grace-period
constant to keep the test fast (no real 30s wait).
"""

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

# AF_UNIX sun_path is limited to 108 bytes on Linux. On sandboxes with a
# long TMPDIR / XDG_RUNTIME_DIR the socket path
# ($XDG_RUNTIME_DIR/voice-typer-hotkey.sock) overflows this limit and
# bind() raises OSError("AF_UNIX path too long"). The tests below
# construct a real WaylandHotkey and call backend.start() which opens
# the socket — they must be skipped on such sandboxes and re-validated
# on a real Linux host with a short XDG_RUNTIME_DIR.
#
# The ``pytest tmp_path`` fixture on Windows lives under a long
# ``%TEMP%`` path (e.g. ``C:\Users\...\AppData\Local\Temp\pytest-of-...``)
# which, combined with the per-test ``xdg-runtime`` suffix, exceeds the
# 108-byte AF_UNIX sun_path limit — so the socket bind fails and
# ``start()`` falls back to pynput, never scheduling the timer under
# test. The Wayland socket backend is Linux-only anyway, so skip the
# socket-binding tests on non-Linux hosts.
_AF_UNIX_PATH_TOO_LONG = pytest.mark.skipif(
    sys.platform != "linux" or len(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) > 90,
    reason=("AF_UNIX socket tests are Linux-only (Wayland) — VALIDATE ON HOST with short XDG_RUNTIME_DIR"),
)


def _make_tmp_xdg(tmp_path: Path) -> str:
    """Return a tmp dir suitable for ``$XDG_RUNTIME_DIR``."""
    xdg = tmp_path / "xdg-runtime"
    xdg.mkdir(mode=0o700, exist_ok=True)
    return str(xdg)


@pytest.fixture
def xdg_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Set ``$XDG_RUNTIME_DIR`` to a per-test tmp dir."""
    xdg = _make_tmp_xdg(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", xdg)
    return xdg


def test_no_client_grace_seconds_is_30() -> None:
    """The grace period is 30s (matches the pynput-fallback timeout).

    This pins the contract: if anyone changes the constant, this test
    catches it and forces them to update the onboarding docs that
    quote "30s" to Wayland users.
    """
    assert WaylandHotkey.NO_CLIENT_GRACE_SECONDS == 30.0


@_AF_UNIX_PATH_TOO_LONG
def test_start_schedules_no_client_timer(xdg_runtime: str) -> None:
    """``start()`` must schedule the no-client grace timer.

    The timer reference is stored on ``_no_client_timer`` so ``stop()``
    can cancel it. Without this, the warning would never fire (or
    would fire after the backend has already been stopped).
    """
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
    """When no client connects within the grace period, an actionable
    WARNING is logged with the socket path + install instructions.

    The grace period is shortened to 0.05s via monkeypatching the
    class constant so the test doesn't wait 30s. The warning must
    mention the socket path (so the user knows where to send
    commands) and the install hint (so the user knows how to fix it).
    """
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
    """When a callback is registered via ``set_no_client_callback``,
    it is invoked with (title, message) after the grace period elapses.

    This lets the app wire the no-client warning to
    ``tray.notify_safety`` so it surfaces as a desktop notification
    in addition to the log line.
    """
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
    """When an IPC client connects, the no-client grace timer is canceled
    so a late-connecting client doesn't trigger a spurious warning.

    Simulates a real client connecting by sending ``ping`` to the
    socket and verifies the timer is canceled + the
    ``_client_ever_connected`` flag is set.
    """
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 5.0  # type: ignore[misc] — long grace; we'll connect immediately
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
        # cancel the timer.
        deadline = time.monotonic() + 2.0
        while backend._no_client_timer is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert backend._no_client_timer is None, "no-client timer must be canceled after the first client connects."
        assert backend._client_ever_connected.is_set(), "_client_ever_connected must be True after a client connected."
    finally:
        backend.stop()


@_AF_UNIX_PATH_TOO_LONG
def test_stop_cancels_no_client_timer(xdg_runtime: str) -> None:
    """``stop()`` must cancel the no-client grace timer so app shutdown
    during the grace period doesn't fire a spurious warning.

    Without this, a user who quits Voice Typer within 30s of startup
    would see a "Wayland Hotkey Idle" warning after the app is already
    gone — confusing and not actionable.
    """
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 5.0  # type: ignore[misc] — long grace; we'll stop before it fires
    backend.start(lambda: None)
    assert backend._no_client_timer is not None
    backend.stop()
    assert backend._no_client_timer is None, (
        "stop() must clear _no_client_timer so the warning can't fire after teardown."
    )


@_AF_UNIX_PATH_TOO_LONG
def test_stop_prevents_warning_from_firing(xdg_runtime: str, caplog: pytest.LogCaptureFixture) -> None:
    """If ``stop()`` is called during the grace period, the timer's
    callback must NOT log the warning (even if the timer has already
    fired but the callback hasn't run yet).

    The callback re-checks ``self._alive`` so a stop-during-grace
    race doesn't produce a spurious warning.

    Uses a 1.0s grace period (long enough that ``stop()`` is
    guaranteed to run before the timer fires) and then waits 1.5s to
    confirm the timer was canceled rather than just slow to fire.
    """
    backend = WaylandHotkey("<f8>")
    backend.NO_CLIENT_GRACE_SECONDS = 1.0  # type: ignore[misc] — long enough that stop() wins the race
    with caplog.at_level(logging.WARNING, logger="voice_typer.server.hotkeys"):
        backend.start(lambda: None)
        # Stop immediately — well before the 1.0s grace elapses.
        backend.stop()
        # Wait long enough that the timer WOULD have fired (1.0s grace
        # + 0.5s buffer).
        time.sleep(1.5)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Wayland Hotkey Idle" in r.getMessage()]
    assert not warnings, (
        "stop() during the grace period must prevent the no-client "
        f"warning from firing; got: {[r.getMessage() for r in warnings]}"
    )


def test_diagnose_reports_client_ever_connected(xdg_runtime: str) -> None:
    """``diagnose()`` must include ``client_ever_connected`` so the
    onboarding flow + diagnostics can tell apart "socket listening +
    clients active" from "socket listening but nobody sending commands".
    """
    backend = WaylandHotkey("<f8>")
    diag = backend.diagnose()
    assert "client_ever_connected=False" in diag, (
        f"diagnose() must report client_ever_connected=False before any client connects; got: {diag}"
    )


@_AF_UNIX_PATH_TOO_LONG
def test_diagnose_reports_true_after_client_connects(xdg_runtime: str) -> None:
    """After a client connects, ``diagnose()`` must report
    ``client_ever_connected=True``."""
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
    """If the registered callback raises, the timer thread must NOT
    crash — the warning was already logged, and the callback is a
    best-effort tray notification. A crash would silently lose the
    no-client signal forever (the timer thread dies and never fires
    again on restart).
    """
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
    # The callback-failure guard must log its own warning so the
    # operator can see the tray notification was lost.
    callback_failures = [r for r in caplog.records if "no-client callback raised" in r.getMessage()]
    assert callback_failures, "callback failures must be logged so the operator knows the tray notification was lost."


def test_set_no_client_callback_signature() -> None:
    """``set_no_client_callback`` must accept a (title, message)
    callable — matching ``tray.notify_safety(title, message)``'s
    signature so callers can wire them directly.
    """
    backend = WaylandHotkey("<f8>")
    mock_tray = MagicMock()
    backend.set_no_client_callback(mock_tray.notify_safety)
    assert backend._on_no_client is mock_tray.notify_safety
