"""regression: IPC protocol-version negotiation on the auth frame."""

from __future__ import annotations

import logging

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

from tests.fixtures.sidecar_ws_test_helpers import make_fake_websocket  # noqa: E402


@pytest.mark.asyncio
async def test_auth_without_protocol_version_still_succeeds(monkeypatch) -> None:
    """Backward compat: older hosts that omit ``protocol_version`` auth OK."""
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
    """Mismatched ``protocol_version`` → WARNING logged, auth STILL succeeds."""
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
