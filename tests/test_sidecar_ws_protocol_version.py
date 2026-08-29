"""S1-CR-78 regression: IPC protocol-version negotiation on the auth frame.

The Python sidecar's ``_authenticate`` (``voice_typer/server/sidecar_ws.py``)
now inspects an optional ``protocol_version`` integer in the inbound auth
frame and logs a WARNING on mismatch with the sidecar's own
``PROTOCOL_VERSION``. The Rust host (``src-tauri/src/sidecar/ws.rs``)
includes the field in its auth frame.

The check is defense-in-depth — it MUST NOT reject the connection on
mismatch (the field is advisory, additive, and older hosts/sidecars
that don't send it must continue to function).

These tests pin:

1. Auth frame WITHOUT ``protocol_version`` → auth still succeeds
   (backward compat with older hosts).
2. Auth frame WITH matching ``protocol_version`` → auth succeeds, no
   warning logged.
3. Auth frame WITH mismatched ``protocol_version`` → auth STILL
   succeeds (advisory), but a warning is logged with both versions.
4. Auth frame with non-int ``protocol_version`` → auth still succeeds,
   a warning is logged about the bad type.

The tests call ``_authenticate`` directly with a mock websocket so they
don't need to exercise the full ``_handle_connection`` dispatch loop.
"""

from __future__ import annotations

import logging

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

from tests.fixtures.sidecar_ws_test_helpers import make_fake_websocket  # noqa: E402


@pytest.mark.asyncio
async def test_auth_without_protocol_version_still_succeeds(monkeypatch) -> None:
    """Backward compat: older hosts that omit ``protocol_version`` auth OK.

    S1-CR-78: the field is additive. Older Rust hosts (or manual test
    clients) that send only ``{"type":"auth","token":...}`` must
    continue to authenticate successfully.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = make_fake_websocket({"type": "auth", "token": "good-token"})

    result = await sidecar_ws._authenticate(ws)

    assert result is True, "auth must succeed when protocol_version is absent"


@pytest.mark.asyncio
async def test_auth_with_matching_protocol_version_succeeds(monkeypatch, caplog) -> None:
    """Matching ``protocol_version`` → auth succeeds, no skew warning."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = make_fake_websocket(
        {
            "type": "auth",
            "token": "good-token",
            "protocol_version": sidecar_ws.PROTOCOL_VERSION,
        }
    )

    with caplog.at_level(logging.WARNING, logger="voice_typer.server.sidecar_ws"):
        result = await sidecar_ws._authenticate(ws)

    assert result is True, "auth must succeed when protocol_version matches"
    skew_warnings = [r for r in caplog.records if "protocol version skew" in r.getMessage()]
    assert skew_warnings == [], (
        f"matching protocol_version must NOT log a skew warning; got: {[r.getMessage() for r in skew_warnings]}"
    )


@pytest.mark.asyncio
async def test_auth_with_mismatched_protocol_version_logs_warning_but_succeeds(monkeypatch, caplog) -> None:
    """Mismatched ``protocol_version`` → WARNING logged, auth STILL succeeds.

    S1-CR-78: the version negotiation is defense-in-depth, NOT a
    security gate. A misconfigured host should still be able to
    authenticate so the operator can read the warning in diagnostics.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    wrong_version = sidecar_ws.PROTOCOL_VERSION + 999
    ws = make_fake_websocket(
        {
            "type": "auth",
            "token": "good-token",
            "protocol_version": wrong_version,
        }
    )

    with caplog.at_level(logging.WARNING, logger="voice_typer.server.sidecar_ws"):
        result = await sidecar_ws._authenticate(ws)

    assert result is True, "auth must STILL succeed on protocol_version mismatch (field is advisory)"
    skew_warnings = [r for r in caplog.records if "protocol version skew" in r.getMessage()]
    assert len(skew_warnings) == 1, (
        f"expected exactly one skew warning, got {len(skew_warnings)}; "
        f"records={[r.getMessage() for r in caplog.records]}"
    )
    # Both the host and sidecar versions must appear in the warning so
    # the operator can see the actual skew magnitude.
    msg = skew_warnings[0].getMessage()
    assert f"host={wrong_version}" in msg, f"warning must include host version: {msg!r}"
    assert f"sidecar={sidecar_ws.PROTOCOL_VERSION}" in msg, f"warning must include sidecar version: {msg!r}"


@pytest.mark.asyncio
async def test_auth_with_non_int_protocol_version_logs_warning_but_succeeds(monkeypatch, caplog) -> None:
    """Non-int ``protocol_version`` → WARNING logged, auth STILL succeeds."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = make_fake_websocket(
        {
            "type": "auth",
            "token": "good-token",
            "protocol_version": "not-an-int",
        }
    )

    with caplog.at_level(logging.WARNING, logger="voice_typer.server.sidecar_ws"):
        result = await sidecar_ws._authenticate(ws)

    assert result is True, "auth must STILL succeed when protocol_version is a bad type"
    bad_type_warnings = [r for r in caplog.records if "protocol_version is not an int" in r.getMessage()]
    assert len(bad_type_warnings) == 1, (
        f"expected one bad-type warning, got {len(bad_type_warnings)}; "
        f"records={[r.getMessage() for r in caplog.records]}"
    )
