"""Tests for the event_bus transport-liveness probe registry."""

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
        """With no probes registered, has_live_transport() returns True"""
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
        """The probe is a callable evaluated at query time, mutating the"""
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
    """``IPCServer`` the same way; this class pins that the deleted"""

    def test_start_tcp_surface_is_removed(self):
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, _app, _service = make_ipc_server_with_fakes()
        assert not hasattr(server, "_accept_tcp")
        assert not hasattr(type(server), "start_tcp") or not callable(getattr(type(server), "start_tcp", None))
        assert has_live_transport() is True

    def test_stop_without_start_tcp_is_noop(self):
        """stop() on a server whose TCP transport never started must not"""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        server, _app, _service = make_ipc_server_with_fakes()
        server.stop()  # must not raise; unregister(None) is a no-op
        assert has_live_transport() is True
