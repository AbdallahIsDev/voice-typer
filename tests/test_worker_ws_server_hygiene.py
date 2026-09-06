"""Hygiene pins for ``voice_typer.worker._ws_server``.

The worker WS server used to define ``_MAX_WS_CONNECTIONS = 4`` with a
comment calling it a "concurrent-connection limit (DoS protection)".
Nothing referenced it (the sidecar's same-named constant is a different
module with a real semaphore behind it), ``serve()`` accepts no
``max_connections`` kwarg, and no semaphore exists — a false security
signal for readers and security reviews. The constant and its claim are
deleted; the adjacent design note now names the REAL access controls
(per-launch bearer-token auth, loopback-only bind, OS-assigned ephemeral
port).
"""

from __future__ import annotations

import inspect

import voice_typer.worker._ws_server as ws_server


class TestNoDeadConnectionCap:
    def test_dead_max_ws_connections_constant_is_gone(self):
        """The worker module must not define a connection-cap constant —
        the sidecar's ``_MAX_WS_CONNECTIONS`` (a different module, backed
        by a real semaphore) is the only legitimate bearer of that name."""
        assert not hasattr(ws_server, "_MAX_WS_CONNECTIONS"), (
            "regression: the dead '_MAX_WS_CONNECTIONS' DoS-protection "
            "constant is back in worker._ws_server — it was never "
            "referenced and never enforced"
        )

    def test_no_dos_protection_claim_in_source(self):
        """No comment or docstring may claim a concurrent-connection
        limit / DoS protection that the code does not implement."""
        src = inspect.getsource(ws_server)
        assert "DoS protection" not in src, (
            "worker._ws_server claims 'DoS protection' again — either implement the control or drop the claim"
        )
        assert "Concurrent-connection limit" not in src

    def test_design_note_names_the_real_controls(self):
        """The design note adjacent to ``serve()`` must name the real
        access controls: the auth gate, the loopback-only bind, and the
        OS-assigned ephemeral port."""
        src = inspect.getsource(ws_server)
        for control in ("auth gate", "loopback-only", "ephemeral port"):
            assert control in src, (
                f"worker._ws_server design note must name the real access controls — missing: {control!r}"
            )

    def test_frame_cap_constant_survives(self):
        """The 1 MiB frame cap (ADR-0020 §10) is REAL and must stay."""
        assert ws_server._MAX_FRAME_BYTES == 1 * 1024 * 1024
