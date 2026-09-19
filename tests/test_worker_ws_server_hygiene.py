"""Hygiene pins for ``voice_typer.worker._ws_server``."""

from __future__ import annotations

import inspect

import voice_typer.worker._ws_server as ws_server


class TestNoDeadConnectionCap:
    def test_dead_max_ws_connections_constant_is_gone(self):
        """The worker module must not define a connection-cap constant:"""
        assert not hasattr(ws_server, "_MAX_WS_CONNECTIONS"), (
            "regression: the dead '_MAX_WS_CONNECTIONS' DoS-protection "
            "constant is back in worker._ws_server, it was never "
            "referenced and never enforced"
        )

    def test_no_dos_protection_claim_in_source(self):
        """No comment or docstring may claim a concurrent-connection"""
        src = inspect.getsource(ws_server)
        assert "DoS protection" not in src, (
            "worker._ws_server claims 'DoS protection' again, either implement the control or drop the claim"
        )
        assert "Concurrent-connection limit" not in src

    def test_design_note_names_the_real_controls(self):
        """The design note adjacent to ``serve()`` must name the real"""
        src = inspect.getsource(ws_server)
        for control in ("auth gate", "loopback-only", "ephemeral port"):
            assert control in src, (
                f"worker._ws_server design note must name the real access controls, missing: {control!r}"
            )

    def test_frame_cap_constant_survives(self):
        """The 1 MiB frame cap (ADR-0020 §10) is REAL and must stay."""
        assert ws_server._MAX_FRAME_BYTES == 1 * 1024 * 1024
