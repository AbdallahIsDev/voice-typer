"""SA-6 (S2-CR-72): Tauri sidecar handshake protocol-version tests."""

from __future__ import annotations

import json

from voice_typer.server import sidecar_ws


class TestProtocolVersionConstant:
    """(SA-6): the ``PROTOCOL_VERSION`` constant contract."""

    def test_protocol_version_constant_exists(self):
        """The module MUST expose a ``PROTOCOL_VERSION`` integer constant."""
        assert hasattr(sidecar_ws, "PROTOCOL_VERSION"), (
            "S2-CR-72: sidecar_ws.PROTOCOL_VERSION must exist, the Rust "
            "host's EXPECTED_PROTOCOL constant compares against it at "
            "handshake time"
        )

    def test_protocol_version_is_int(self):
        """The constant MUST be an ``int`` (not a string, not None)."""
        assert isinstance(sidecar_ws.PROTOCOL_VERSION, int), (
            "S2-CR-72: PROTOCOL_VERSION must be int (Rust host parses via "
            "as_u64 + u32::try_from); got " + type(sidecar_ws.PROTOCOL_VERSION).__name__
        )

    def test_protocol_version_is_positive(self):
        """The constant MUST be > 0."""
        assert sidecar_ws.PROTOCOL_VERSION > 0, (
            "S2-CR-72: PROTOCOL_VERSION must be > 0 (0 is reserved as the "
            "absent / pre-negotiation sentinel); got " + repr(sidecar_ws.PROTOCOL_VERSION)
        )

    def test_protocol_version_is_currently_one(self):
        """Pins the current value to 1."""
        assert sidecar_ws.PROTOCOL_VERSION == 1, (
            "S2-CR-72: PROTOCOL_VERSION is currently 1, if you're bumping "
            "this value, also update src-tauri/src/sidecar/spawn.rs::"
            "EXPECTED_PROTOCOL and its parity test "
            "(test_expected_protocol_matches_python_sidecar_default). "
            "Got: " + repr(sidecar_ws.PROTOCOL_VERSION)
        )


class TestEmitServerStartedPayload:
    """(SA-6): the ``_emit_server_started`` payload contract."""

    def test_emit_with_protocol_includes_protocol_field(self, capsys):
        """When ``protocol`` is passed, the payload includes the field."""
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
        """When ``protocol`` is ``None`` (default), the field is absent."""
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
            "when the protocol arg is None, pre-negotiation tests assert "
            "the exact two-field payload shape. Got: " + repr(payload)
        )

    def test_emit_with_explicit_none_omits_protocol_field(self, capsys):
        """Passing ``protocol=None`` explicitly is the same as the default."""
        sidecar_ws._emit_server_started(54321, None)
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert "protocol" not in payload

    def test_emit_protocol_is_int_in_json(self, capsys):
        """The protocol field MUST be a JSON int (not a string)."""
        sidecar_ws._emit_server_started(54321, 1)
        payload = json.loads(capsys.readouterr().out.strip())
        assert isinstance(payload.get("protocol"), int), (
            "S2-CR-72: 'protocol' field must serialize as a JSON int (Rust "
            "parser uses Value::as_u64); got: " + repr(payload.get("protocol"))
        )

    def test_emit_coerces_protocol_to_int(self, capsys):
        """float is normalized to an int in the JSON output."""
        sidecar_ws._emit_server_started(54321, True)  # bool is a subtype of int
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["protocol"] == 1
        assert isinstance(payload["protocol"], int)
        # Specifically NOT a bool in the JSON output (json.dumps would
        assert payload["protocol"] is not True

    def test_emit_with_production_protocol_value(self, capsys):
        """
        The production ``run()`` caller passes ``PROTOCOL_VERSION`` —
        ``PROTOCOL_VERSION``. This pins the wiring between the constant
        """
        sidecar_ws._emit_server_started(54321, sidecar_ws.PROTOCOL_VERSION)
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["protocol"] == sidecar_ws.PROTOCOL_VERSION


class TestRunCallSiteWiring:
    """(SA-6): the production ``run()`` caller passes"""

    def test_run_calls_emit_with_protocol_constant(self):
        """``run()`` MUST call ``_emit_server_started(port, PROTOCOL_VERSION)``"""
        import inspect

        src = inspect.getsource(sidecar_ws.run)
        # The call site is ``_emit_server_started(port, PROTOCOL_VERSION)``.
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
        for line in src.splitlines():
            if "_emit_server_started(" in line:
                assert "PROTOCOL_VERSION" in line, (
                    "S2-CR-72: the _emit_server_started call site in run() "
                    "must pass PROTOCOL_VERSION as the second arg. Got line: " + repr(line.strip())
                )
                break
