"""Tests for the event_bus transport-liveness probe registry.

``event_bus.publish`` returns True when ANY in-process subscriber
accepted the event — which is NOT proof the event reached the host UI:
the IPC transport's push() swallows write failures (it buffers to
``_pending_tcp`` and marks the client dead instead of raising), the
no-client path buffers silently, and unrelated subscribers (e.g. the
tray's parakeet-cpu-fallback listener) accept every event.

The probe registry (``register_transport_probe`` /
``unregister_transport_probe`` / ``has_live_transport``) gives callers
such as ``tray_window.open_electron_window`` a truthful delivery
signal: ``has_live_transport()`` is True only when a registered probe
reports a live host client. The TCP transport (``IPCServer.start_tcp``)
registers one reporting ``self._tcp_client is not None`` and
``IPCServer.stop`` unregisters it — verified in
``TestTcpServerProbeWiring`` below.
"""

from __future__ import annotations

from voice_typer.server import event_bus
from voice_typer.server.event_bus import (
    has_live_transport,
    register_transport_probe,
    unregister_transport_probe,
)


class TestProbeRegistry:
    """Pure registry semantics: register / unregister / has_live_transport."""

    def test_no_probes_defaults_to_live(self):
        """With no probes registered, has_live_transport() returns True
        (console mode / non-TCP transports keep publish-and-return
        behavior)."""
        assert has_live_transport() is True

    def test_register_none_is_noop(self):
        register_transport_probe(None)
        assert event_bus._transport_probes == []
        assert has_live_transport() is True

    def test_unregister_unknown_or_none_is_noop(self):
        unregister_transport_probe(lambda: True)  # never registered
        unregister_transport_probe(None)
        assert has_live_transport() is True

    def test_true_probe_reports_live(self):
        def probe() -> bool:
            return True

        register_transport_probe(probe)
        try:
            assert has_live_transport() is True
        finally:
            unregister_transport_probe(probe)
        # Unregistered → back to the no-probe default.
        assert has_live_transport() is True

    def test_false_probe_reports_no_live_client(self):
        def probe() -> bool:
            return False

        register_transport_probe(probe)
        try:
            assert has_live_transport() is False
        finally:
            unregister_transport_probe(probe)

    def test_any_true_wins_among_multiple_probes(self):
        def p1() -> bool:
            return False

        def p2() -> bool:
            return True

        register_transport_probe(p1)
        register_transport_probe(p2)
        try:
            assert has_live_transport() is True
        finally:
            unregister_transport_probe(p1)
            unregister_transport_probe(p2)

    def test_probe_reflects_live_state_dynamically(self):
        """The probe is a callable evaluated at query time — mutating the
        transport state flips the answer without re-registering."""
        state = {"connected": False}

        def probe() -> bool:
            return state["connected"]

        register_transport_probe(probe)
        try:
            assert has_live_transport() is False
            state["connected"] = True
            assert has_live_transport() is True
        finally:
            unregister_transport_probe(probe)


class TestTcpServerProbeWiring:
    """``IPCServer.start_tcp`` registers a probe reporting the live
    client state and ``IPCServer.stop`` unregisters it."""

    def test_start_tcp_registers_and_stop_unregisters(self, monkeypatch):
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, _app, _service = make_ipc_server_with_fakes()
        # Don't bind a real socket: replace the accept loop (spawned in
        # a daemon thread by start_tcp) with a no-op.
        monkeypatch.setattr(server, "_accept_tcp", lambda port: None)

        server.start_tcp(9999)
        try:
            assert server._transport_live_probe is not None
            # No client connected yet → probe reports False (and the
            # registry reports no live transport).
            server._tcp_client = None
            assert server._transport_live_probe() is False
            assert has_live_transport() is False
            # A connected client flips the probe live.
            server._tcp_client = object()
            assert server._transport_live_probe() is True
            assert has_live_transport() is True
        finally:
            server._tcp_client = None
            server.stop()
        # stop() unregistered the probe → back to the no-probe default.
        assert has_live_transport() is True

    def test_stop_without_start_tcp_is_noop(self):
        """stop() on a server whose TCP transport never started must not
        raise (the WS-sidecar path never calls start_tcp)."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, _app, _service = make_ipc_server_with_fakes()
        server.stop()  # must not raise; unregister(None) is a no-op
        assert has_live_transport() is True
