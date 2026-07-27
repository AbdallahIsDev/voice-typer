"""SA-6 (S2-CR-72): Tauri sidecar handshake protocol-version tests.

Pins the Python side of the protocol-version negotiation contract:

    voice_typer/server/sidecar_ws.py::PROTOCOL_VERSION
        MUST match
    src-tauri/src/sidecar/spawn.rs::EXPECTED_PROTOCOL

The Rust-side parity test is
``src-tauri/src/sidecar/spawn.rs::tests::test_expected_protocol_matches_python_sidecar_default``.
This file is the Python-side counterpart.

Why a separate file (not appended to ``tests/tauri/test_sidecar_ws_unit.py``)?
That file is owned by another sub-agent's scope. ``tests/test_app*.py`` is the
SA-6 file scope, and this filename (``test_app_sidecar_protocol.py``) matches
the ``test_app*.py`` glob, so it stays inside SA-6's owned files.

S2-CR-72 (SA-6) background: the sidecar's ``server_started`` JSON line now
includes a ``"protocol": <int>`` field so the Rust host can detect version
skew at handshake time (before any command dispatch). Pre-negotiation
sidecars emitted only ``{"event":"server_started","port":<n>}``; old hosts
that don't yet parse the ``protocol`` field continue to function (the field
is additive on the Python side — see ``_emit_server_started``'s ``None``
default).
"""

from __future__ import annotations

import json

from voice_typer.server import sidecar_ws

# ─── PROTOCOL_VERSION constant ──────────────────────────────────────────


class TestProtocolVersionConstant:
    """S2-CR-72 (SA-6): the ``PROTOCOL_VERSION`` constant contract."""

    def test_protocol_version_constant_exists(self):
        """The module MUST expose a ``PROTOCOL_VERSION`` integer constant.

        Without it, the Rust host's ``EXPECTED_PROTOCOL`` has nothing to
        compare against — the protocol-version negotiation is impossible.
        """
        assert hasattr(sidecar_ws, "PROTOCOL_VERSION"), (
            "S2-CR-72: sidecar_ws.PROTOCOL_VERSION must exist — the Rust "
            "host's EXPECTED_PROTOCOL constant compares against it at "
            "handshake time"
        )

    def test_protocol_version_is_int(self):
        """The constant MUST be an ``int`` (not a string, not None).

        The Rust host parses it via ``serde_json::Value::as_u64`` then
        ``u32::try_from`` — a non-int JSON value degrades to ``None`` on
        the Rust side and is treated as a mismatch. An int is the only
        type that survives the round-trip.
        """
        assert isinstance(sidecar_ws.PROTOCOL_VERSION, int), (
            "S2-CR-72: PROTOCOL_VERSION must be int (Rust host parses via "
            "as_u64 + u32::try_from); got " + type(sidecar_ws.PROTOCOL_VERSION).__name__
        )

    def test_protocol_version_is_positive(self):
        """The constant MUST be > 0.

        0 is reserved as the "absent / pre-negotiation" sentinel on the
        Rust side (``parse_server_started`` returns ``None`` when the
        field is absent, and the host's mismatch check treats ``None``
        as a mismatch against ``EXPECTED_PROTOCOL``). A protocol version
        of 0 would be ambiguous with "field absent" — using positive
        integers only keeps the contract unambiguous.
        """
        assert sidecar_ws.PROTOCOL_VERSION > 0, (
            "S2-CR-72: PROTOCOL_VERSION must be > 0 (0 is reserved as the "
            "absent / pre-negotiation sentinel); got " + repr(sidecar_ws.PROTOCOL_VERSION)
        )

    def test_protocol_version_is_currently_one(self):
        """Pins the current value to 1.

        This test is the Python-side parity counterpart of the Rust-side
        ``test_expected_protocol_matches_python_sidecar_default`` test in
        ``src-tauri/src/sidecar/spawn.rs``. When this value is bumped,
        BOTH tests must be updated in lockstep:

          - voice_typer/server/sidecar_ws.py::PROTOCOL_VERSION
          - src-tauri/src/sidecar/spawn.rs::EXPECTED_PROTOCOL
          - src-tauri/src/sidecar/spawn.rs::tests::test_expected_protocol_matches_python_sidecar_default
          - tests/test_app_sidecar_protocol.py (this test)

        The bump is a coordinated 2-sided change — see the S2-CR-72
        finding for the rationale.
        """
        assert sidecar_ws.PROTOCOL_VERSION == 1, (
            "S2-CR-72: PROTOCOL_VERSION is currently 1 — if you're bumping "
            "this value, also update src-tauri/src/sidecar/spawn.rs::"
            "EXPECTED_PROTOCOL and its parity test "
            "(test_expected_protocol_matches_python_sidecar_default). "
            "Got: " + repr(sidecar_ws.PROTOCOL_VERSION)
        )


# ─── _emit_server_started payload shape ─────────────────────────────────


class TestEmitServerStartedPayload:
    """S2-CR-72 (SA-6): the ``_emit_server_started`` payload contract.

    The Rust host's ``parse_server_started`` parser extracts both ``port``
    and ``protocol`` from the JSON line. These tests pin the Python-side
    payload shape so a future refactor of ``_emit_server_started`` can't
    silently break the Rust parser.
    """

    def test_emit_with_protocol_includes_protocol_field(self, capsys):
        """When ``protocol`` is passed, the payload includes the field.

        This is the production path — ``run()`` calls
        ``_emit_server_started(port, PROTOCOL_VERSION)``.
        """
        sidecar_ws._emit_server_started(54321, 1)
        captured = capsys.readouterr()
        assert captured.err == ""
        line = captured.out.strip()
        payload = json.loads(line)
        assert payload == {
            "event": "server_started",
            "port": 54321,
            "protocol": 1,
        }, (
            "S2-CR-72: when protocol is passed, the payload MUST include "
            "'protocol': <int> so the Rust host can extract it via "
            "parse_server_started. Got: " + repr(payload)
        )

    def test_emit_without_protocol_omits_protocol_field(self, capsys):
        """When ``protocol`` is ``None`` (default), the field is absent.

        This is the backward-compatible path — pre-negotiation tests
        that assert the exact two-field payload shape (e.g.
        ``tests/tauri/test_sidecar_ws_unit.py::test_emit_server_started_writes_valid_json_to_stdout``)
        continue to pass unchanged.
        """
        sidecar_ws._emit_server_started(54321)
        captured = capsys.readouterr()
        line = captured.out.strip()
        payload = json.loads(line)
        assert payload == {"event": "server_started", "port": 54321}, (
            "S2-CR-72: when protocol is None (default), the payload MUST "
            "omit the 'protocol' field (backward compat with pre-negotiation "
            "tests). Got: " + repr(payload)
        )
        # Explicit: the field must NOT be present (not present-with-None).
        assert "protocol" not in payload, (
            "S2-CR-72: 'protocol' key must be ABSENT (not present-with-None) "
            "when the protocol arg is None — pre-negotiation tests assert "
            "the exact two-field payload shape. Got: " + repr(payload)
        )

    def test_emit_with_explicit_none_omits_protocol_field(self, capsys):
        """Passing ``protocol=None`` explicitly is the same as the default.

        Defensive: ensures the helper treats ``None`` (the default) and
        an explicit ``None`` identically — no subtle branching on
        "was the arg provided?".
        """
        sidecar_ws._emit_server_started(54321, None)
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert "protocol" not in payload

    def test_emit_protocol_is_int_in_json(self, capsys):
        """The protocol field MUST be a JSON int (not a string).

        The Rust parser uses ``serde_json::Value::as_u64`` which returns
        ``None`` for string values — a string protocol would be silently
        treated as absent on the Rust side, defeating the negotiation.
        """
        sidecar_ws._emit_server_started(54321, 1)
        payload = json.loads(capsys.readouterr().out.strip())
        assert isinstance(payload.get("protocol"), int), (
            "S2-CR-72: 'protocol' field must serialize as a JSON int (Rust "
            "parser uses Value::as_u64); got: " + repr(payload.get("protocol"))
        )

    def test_emit_coerces_protocol_to_int(self, capsys):
        """The helper coerces ``protocol`` via ``int(...)`` so a bool or
        float is normalized to an int in the JSON output.

        Defensive: ``int(True) == 1`` and ``int(1.0) == 1`` — both are
        valid inputs that should produce ``"protocol": 1`` in the JSON,
        not ``"protocol": true`` or ``"protocol": 1.0`` (which the Rust
        parser would reject via ``as_u64``).
        """
        sidecar_ws._emit_server_started(54321, True)  # bool is a subtype of int
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["protocol"] == 1
        assert isinstance(payload["protocol"], int)
        # Specifically NOT a bool in the JSON output (json.dumps would
        # emit ``true`` for an uncoerced bool — ``int(True)`` produces 1).
        assert payload["protocol"] is not True

    def test_emit_with_production_protocol_value(self, capsys):
        """The production ``run()`` caller passes ``PROTOCOL_VERSION`` —
        the resulting payload's ``protocol`` field MUST equal
        ``PROTOCOL_VERSION``. This pins the wiring between the constant
        and the call site.
        """
        sidecar_ws._emit_server_started(54321, sidecar_ws.PROTOCOL_VERSION)
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["protocol"] == sidecar_ws.PROTOCOL_VERSION


# ─── run() call-site wiring ─────────────────────────────────────────────


class TestRunCallSiteWiring:
    """S2-CR-72 (SA-6): the production ``run()`` caller passes
    ``PROTOCOL_VERSION``.

    Source-level invariant test (mirrors the pattern in
    ``tests/app/test_quit_restart.py::TestAppRestartLogMessage``) —
    inspects the source of ``run()`` to verify the call site wires the
    protocol constant. A runtime test would require binding a port +
    starting the asyncio loop, which is integration-test territory
    (``tests/tauri/test_sidecar_ws_integration.py``) and out of scope
    for this unit test.
    """

    def test_run_calls_emit_with_protocol_constant(self):
        """``run()`` MUST call ``_emit_server_started(port, PROTOCOL_VERSION)``
        — not ``_emit_server_started(port)`` (which would emit the
        pre-negotiation two-field payload and trigger a protocol-mismatch
        on the Rust side).
        """
        import inspect

        src = inspect.getsource(sidecar_ws.run)
        # The call site is ``_emit_server_started(port, PROTOCOL_VERSION)``.
        # Look for the literal string ``PROTOCOL_VERSION`` as the second
        # argument to ``_emit_server_started``.
        assert "_emit_server_started(" in src, (
            "S2-CR-72: run() must call _emit_server_started (couldn't find the call site in run()'s source)"
        )
        assert "PROTOCOL_VERSION" in src, (
            "S2-CR-72: run() must pass PROTOCOL_VERSION to "
            "_emit_server_started so the Rust host can detect version skew. "
            "Found _emit_server_started call but no PROTOCOL_VERSION arg in "
            "run()'s source."
        )
        # Specifically: the call site must NOT be the pre-negotiation
        # form ``_emit_server_started(port)`` (single arg).
        # We check that the line containing ``_emit_server_started(``
        # also contains ``PROTOCOL_VERSION``.
        for line in src.splitlines():
            if "_emit_server_started(" in line:
                assert "PROTOCOL_VERSION" in line, (
                    "S2-CR-72: the _emit_server_started call site in run() "
                    "must pass PROTOCOL_VERSION as the second arg. Got line: " + repr(line.strip())
                )
                break
