"""WS + HMAC handshake (macOS path)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.fixtures.sidecar_ws_test_helpers import _make_fake_server

# Path to the Python source under test, used by the source-grep tests
_SIDECAR_WS_PATH = Path(__file__).resolve().parents[3] / "voice_typer" / "server" / "sidecar_ws.py"
assert _SIDECAR_WS_PATH.exists(), f"sidecar_ws.py not found at {_SIDECAR_WS_PATH}"
_SIDECAR_HANDSHAKE_PATH = (
    Path(__file__).resolve().parents[3] / "voice_typer" / "server" / "sidecar_ws_internals" / "handshake.py"
)
assert _SIDECAR_HANDSHAKE_PATH.exists(), f"handshake.py not found at {_SIDECAR_HANDSHAKE_PATH}"

# Path to the Rust WS client source, used by the arch-agnostic test
_WS_RS_PATH = Path(__file__).resolve().parents[3] / "src-tauri" / "src" / "sidecar" / "ws.rs"
assert _WS_RS_PATH.exists(), f"ws.rs not found at {_WS_RS_PATH}"

# Path to the Rust spawn.rs, used to document the externalBin target-triple
_SPAWN_RS_PATH = Path(__file__).resolve().parents[3] / "src-tauri" / "src" / "sidecar" / "spawn.rs"
assert _SPAWN_RS_PATH.exists(), f"spawn.rs not found at {_SPAWN_RS_PATH}"


def _import_sidecar_ws():
    """Import sidecar_ws lazily."""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


def _read_sidecar_ws_source() -> str:
    """string (for source-grep tests)."""
    return _SIDECAR_WS_PATH.read_text(encoding="utf-8") + "\n" + _SIDECAR_HANDSHAKE_PATH.read_text(encoding="utf-8")


def _read_ws_rs_source() -> str:
    """Read the Rust ws.rs source as a string (for the arch-agnostic test)."""
    return _WS_RS_PATH.read_text(encoding="utf-8")


def _read_spawn_rs_source() -> str:
    """Read the spawn module sources (spawn.rs + spawn/*.rs, EO-33 split)."""
    files = [_SPAWN_RS_PATH] + sorted(_SPAWN_RS_PATH.parent.joinpath("spawn").glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


# A realistic 64-char hex token (32 bytes × 2 hex chars), matching
_GOOD_TOKEN = "deadbeef" * 8  # 64 hex chars


async def test_authenticate_refuses_when_ipc_token_env_unset(monkeypatch):
    """REVIEW-3 SEC-2: if VOICE_TYPER_IPC_TOKEN is unset, the sidecar"""
    sw = _import_sidecar_ws()
    monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)

    ws = MagicMock()
    ws.recv = AsyncMock()

    accepted = await sw._authenticate(ws)

    assert accepted is False, "must reject when VOICE_TYPER_IPC_TOKEN is unset"
    # Critical: the sidecar must NOT read a frame off the wire when the
    ws.recv.assert_not_awaited()


async def test_authenticate_refuses_when_ipc_token_env_empty_string(monkeypatch):
    """An empty-string token is treated the same as unset (defense in depth)."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "")

    ws = MagicMock()
    ws.recv = AsyncMock()

    assert await sw._authenticate(ws) is False
    ws.recv.assert_not_awaited()


async def test_auth_frame_format_is_type_auth_token_string(monkeypatch):
    """The auth frame must be ``{\"type\":\"auth\",\"token\":\"<string>\"}``."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": _GOOD_TOKEN}).encode()
    ws.recv = AsyncMock(return_value=auth_frame)

    assert await sw._authenticate(ws) is True


async def test_auth_frame_must_be_first_frame_non_auth_rejected(monkeypatch):
    """The auth frame is the FIRST frame, a non-auth first frame is rejected."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    # First (and only) frame is a get_status, not auth.
    bad_frame = json.dumps({"type": "get_status", "data": {}}).encode()
    ws.recv = AsyncMock(return_value=bad_frame)

    assert await sw._authenticate(ws) is False
    assert ws.recv.await_count == 1


async def test_auth_frame_missing_token_field_rejected(monkeypatch):
    """A frame with type=auth but no token field is rejected."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"type": "auth"}).encode()  # no token
    )

    assert await sw._authenticate(ws) is False


async def test_auth_frame_empty_token_rejected(monkeypatch):
    """A frame with token=\"\" is rejected (no silent accept on empty token)."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": ""}).encode())

    assert await sw._authenticate(ws) is False


async def test_auth_frame_non_string_token_rejected(monkeypatch):
    """A frame with token=42 (non-string) is rejected before comparison."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": 42}).encode())

    assert await sw._authenticate(ws) is False


def test_authenticate_uses_hmac_compare_digest():
    """The token comparison must use ``hmac.compare_digest`` (constant-time)."""
    # The constant-time comparison lives in the shared auth module
    auth_py = Path(__file__).resolve().parents[3] / "voice_typer" / "server" / "ipc" / "auth.py"
    auth_source = auth_py.read_text(encoding="utf-8")
    source = _read_sidecar_ws_source()

    # The shared helper must use hmac.compare_digest (constant-time),
    assert "hmac.compare_digest" in auth_source, (
        "ipc/auth.py must use hmac.compare_digest for token comparison "
        "(constant-time). Found neither, possible timing side-channel "
        "regression."
    )
    assert "tokens_equal" in source, (
        "sidecar_ws.py must route its token comparison through ipc.auth.tokens_equal (the shared constant-time helper)."
    )
    # And the shared helper must compare the provided + expected tokens
    pattern = r"hmac\.compare_digest\s*\(\s*provided\s*,\s*expected\s*\)"
    assert re.search(pattern, auth_source), (
        "tokens_equal must call hmac.compare_digest(provided, expected), "
        "found a different call shape which may indicate the comparison "
        "is not actually between the user-supplied + env-var tokens."
    )


async def test_authenticate_compare_digest_is_actually_invoked(monkeypatch):
    """Runtime check: hmac.compare_digest is called during auth (not just"""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    # The constant-time comparison now lives in ipc/auth.py , spy
    from voice_typer.server.ipc import auth as _auth

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": _GOOD_TOKEN}).encode())

    # Spy on hmac.compare_digest without changing its behavior.
    real_compare = _auth.hmac.compare_digest
    spy = MagicMock(side_effect=real_compare)
    monkeypatch.setattr(_auth.hmac, "compare_digest", spy)

    assert await sw._authenticate(ws) is True
    spy.assert_called_once_with(_GOOD_TOKEN, _GOOD_TOKEN)


def test_token_value_never_appears_in_any_log_call():
    """The raw token value must NEVER appear in any log line."""
    source = _read_sidecar_ws_source()

    # The token-bearing identifiers in _authenticate are:
    token_bearing_vars = ("expected_token", "provided", "first_raw")
    # `first` is excluded from the bare-name check because it appears

    log_call_pattern = re.compile(r"log\.(info|debug|warning|error|critical)\s*\(")

    for lineno, line in enumerate(source.splitlines(), start=1):
        if not log_call_pattern.search(line):
            continue
        # Skip comment lines that mention log. (e.g. docstring references)
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        for var in token_bearing_vars:
            word_pattern = re.compile(r"\b" + re.escape(var) + r"\b")
            if word_pattern.search(line):
                pytest.fail(
                    f"Potential token leak: line {lineno} contains a log call "
                    f"and references token-bearing variable `{var}`:\n  {line}\n"
                    f"Token values must never be interpolated into log messages."
                )

    # Also assert the literal _GOOD_TOKEN test value doesn't appear
    assert "deadbeef" not in source.lower(), "sidecar_ws.py contains a hardcoded 'deadbeef' token, remove it."


def test_log_lines_use_static_strings_not_token_interpolation():
    """Every log call in _authenticate uses a static string (no %s for token)."""
    source = _read_sidecar_ws_source()

    # Extract the _authenticate function body.
    match = re.search(
        r"async def _authenticate\(.*?\n((?:.|\n)*?)(?=\n(?:async )?def |\Z)",
        source,
    )
    assert match, "_authenticate function not found in sidecar_ws.py"
    auth_body = match.group(1)

    log_call_re = re.compile(r"log\.(info|debug|warning|error|critical)\(\s*(f[\"'])")
    offenders = log_call_re.findall(auth_body)
    assert not offenders, (
        f"_authenticate uses f-string log calls (could leak token): {offenders}. "
        f"Use static strings only, token values must never be interpolated."
    )


def test_loopback_host_constant_is_127_0_0_1():
    """ADR-0020 §1: bind host must be exactly 127.0.0.1."""
    sw = _import_sidecar_ws()
    assert sw._LOOPBACK_HOST == "127.0.0.1"


def test_run_binds_to_loopback_ephemeral_port(monkeypatch):
    """``run()`` calls ``serve(handler, \"127.0.0.1\", 0, max_size=...)``."""
    sw = _import_sidecar_ws()

    # Mock the websockets module + websockets.asyncio.server.serve.
    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
    mock_ws_server = MagicMock()
    mock_ws_server.sockets = [mock_socket]
    mock_ws_server.__aenter__ = AsyncMock(return_value=mock_ws_server)
    mock_ws_server.__aexit__ = AsyncMock(return_value=None)

    mock_serve = MagicMock(return_value=mock_ws_server)
    mock_websockets = MagicMock()
    mock_websockets_asyncio_server = MagicMock()
    mock_websockets_asyncio_server.serve = mock_serve
    monkeypatch.setitem(sys.modules, "websockets", mock_websockets)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", mock_websockets_asyncio_server)

    monkeypatch.setattr(sw, "_force_line_buffered_stdout", lambda: None)

    def _raise_immediately():
        raise RuntimeError("stop after server_started")

    monkeypatch.setattr(asyncio, "Future", _raise_immediately)

    server = _make_fake_server()
    rc = sw.run(server)

    assert rc == 1

    mock_serve.assert_called_once()
    call = mock_serve.call_args
    assert call.args[1] == "127.0.0.1", "must bind to loopback"
    assert call.args[2] == 0, "must bind to ephemeral port (0 = OS-assigned)"
    assert "max_size" in call.kwargs, "must set max_size on serve()"
    assert call.kwargs["max_size"] == 1024 * 1024


def test_emit_server_started_reports_port_as_json(capsys):
    """The host blocks reading the sidecar's stdout until it parses::"""
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    assert captured.err == "", "stderr must be empty, only stdout carries the JSON"
    payload = json.loads(captured.out.strip())
    assert payload == {"event": "server_started", "port": 54321}


def test_emit_server_started_port_is_int_not_string(capsys):
    """The host's JSON parser expects port as an int, not a string."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(0)
    payload = json.loads(capsys.readouterr().out.strip())
    assert isinstance(payload["port"], int)
    assert not isinstance(payload["port"], bool), "port must not be a bool"


def test_server_started_json_does_not_leak_token(capsys):
    """SECURITY: the server_started JSON must NOT contain the token."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert "token" not in payload, (
        "server_started JSON must NOT contain the token, stdout is not "
        "a secure channel. The token is passed via env var at spawn."
    )
    raw_lower = captured.out.lower()
    assert "voice_typer_ipc_token" not in raw_lower, "stdout must not mention VOICE_TYPER_IPC_TOKEN (env-var name leak)"
    # Also assert the raw stdout doesn't contain the literal test token
    assert _GOOD_TOKEN not in captured.out, "raw token value leaked to stdout"


def test_server_started_json_has_no_arch_field(capsys):
    """macOS-specific: the ``server_started`` JSON must NOT carry an ``arch`` field."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())

    # Exactly two keys, no arch, no platform, no triple.
    assert set(payload.keys()) == {"event", "port"}, (
        "server_started JSON must have exactly {event, port} keys, "
        f"got {set(payload.keys())}. The arch is implicit in the "
        f"externalBin binary name; do not add it to the JSON."
    )
    assert "arch" not in payload, "server_started JSON must NOT contain 'arch'"
    assert "platform" not in payload, "server_started JSON must NOT contain 'platform'"
    assert "triple" not in payload, "server_started JSON must NOT contain 'triple'"


def test_max_frame_bytes_constant_is_exactly_1_mib():
    """ADR-0020 §10: the WS frame cap is 1 MiB (1048576 bytes)."""
    sw = _import_sidecar_ws()
    assert sw._MAX_FRAME_BYTES == 1024 * 1024
    assert sw._MAX_FRAME_BYTES == 1_048_576


def test_run_passes_max_size_to_serve(monkeypatch):
    """``run()`` passes ``max_size=_MAX_FRAME_BYTES`` to ``serve()``."""
    sw = _import_sidecar_ws()

    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
    mock_ws_server = MagicMock()
    mock_ws_server.sockets = [mock_socket]
    mock_ws_server.__aenter__ = AsyncMock(return_value=mock_ws_server)
    mock_ws_server.__aexit__ = AsyncMock(return_value=None)

    mock_serve = MagicMock(return_value=mock_ws_server)
    mock_websockets = MagicMock()
    mock_websockets_asyncio_server = MagicMock()
    mock_websockets_asyncio_server.serve = mock_serve
    monkeypatch.setitem(sys.modules, "websockets", mock_websockets)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", mock_websockets_asyncio_server)
    monkeypatch.setattr(sw, "_force_line_buffered_stdout", lambda: None)
    monkeypatch.setattr(asyncio, "Future", lambda: (_ for _ in ()).throw(RuntimeError("stop")))

    sw.run(_make_fake_server())

    mock_serve.assert_called_once()
    assert mock_serve.call_args.kwargs["max_size"] == sw._MAX_FRAME_BYTES


# 8. Rate limiter applied (ADR-0019 + ) ─────────────────────────


async def test_rate_limiter_applied_to_ws_frames():
    """ADR-0019: every inbound WS frame goes through the rate limiter."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {}})
    dispatch = sw._make_dispatch(server)

    rejected = 0
    for _ in range(201):
        result = await dispatch({"type": "ping", "data": {}}, MagicMock())
        if (
            isinstance(result, dict)
            and result.get("type") == "error"
            and result.get("data", {}).get("code") in ("client.rate_limited", "rate_limited")
        ):
            rejected += 1

    assert rejected >= 1, (
        "expected at least one rate_limited response after 201 frames in "
        "the burst window, ADR-0019 limiter not applied to WS path"
    )


async def test_rate_limiter_is_shared_across_connections():
    """the rate limiter is per-PROCESS (shared), not per-connection."""
    _import_sidecar_ws()
    server = _make_fake_server()

    from voice_typer.server.ipc_server import _get_rate_limiter

    limiter_1 = _get_rate_limiter(server)
    limiter_2 = _get_rate_limiter(server)

    assert limiter_1 is limiter_2, (
        "CR-11 regression: _get_rate_limiter returned different instances "
        "for the same server, the limiter must be shared across all WS "
        "connections to prevent burst-budget reset via reconnect."
    )


async def test_rate_limiter_rejects_with_structured_error():
    """A rate-limited frame returns ``{\"type\":\"error\",\"data\":{\"code\":\"rate_limited\",...}}``."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    # Exhaust the burst budget.
    for _ in range(200):
        await dispatch({"type": "ping", "data": {}}, MagicMock())

    # Next frame must be rate_limited.
    result = await dispatch({"type": "ping", "data": {}}, MagicMock())
    assert result["type"] == "error"
    # Accept the canonical namespaced form (``client.rate_limited``)
    assert result["data"]["code"] in ("client.rate_limited", "rate_limited")
    assert "message" in result["data"], "rate_limited error must include a message"


def test_no_platform_branch_in_auth_path():
    """The WS auth path must be 100% cross-platform, no ``sys.platform``,"""
    source = _read_sidecar_ws_source()

    # Forbidden patterns: any platform-conditional that could branch
    forbidden_patterns = [
        (r"\bsys\.platform\b", "sys.platform check"),
        (r"\bplatform\.system\s*\(", "platform.system() call"),
        (r"\bplatform\.platform\s*\(", "platform.platform() call"),
        (r"\bos\.name\b", "os.name check"),
        (r"\bwin32\b", "win32 literal (use sys.platform check elsewhere)"),
        (r"\bWin32\b", "Win32 literal"),
        (r"\bdarwin\b", "darwin literal (sys.platform check)"),
    ]

    for pattern, description in forbidden_patterns:
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if re.search(pattern, line):
                pytest.fail(
                    f"Platform branch detected in sidecar_ws.py line {lineno}: "
                    f"{description}.\n  Line: {line.rstrip()}\n"
                    f"The WS auth path must be cross-platform, macOS must "
                    f"behave identically to Linux/Windows. Move any platform-"
                    f"specific logic out of sidecar_ws.py."
                )


def test_no_arch_branch_in_auth_path():
    """macOS-specific: the WS auth path must have NO arch branch."""
    source = _read_sidecar_ws_source()

    # Forbidden arch-detection patterns. These would indicate the auth
    # "one protocol, two arches" contract.
    forbidden_patterns = [
        (r"\bplatform\.machine\s*\(", "platform.machine() call"),
        (r"\bos\.uname\s*\(", "os.uname() call"),
        (r"\bstruct\.calcsize\s*\(", "struct.calcsize() call (pointer-size probe)"),
        (r"\bctypes\.sizeof\s*\(", "ctypes.sizeof() call (pointer-size probe)"),
        (r"\bsys\.maxsize\b", "sys.maxsize check (could branch on 64-bit)"),
        (r"\baarch64\b", "aarch64 literal in code"),
        (r"\bx86_64\b", "x86_64 literal in code"),
        (r"\barm64\b", "arm64 literal in code"),
        (r"\buniversal\b", "universal literal in code (universal binary branch)"),
    ]

    for pattern, description in forbidden_patterns:
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if re.search(pattern, line):
                pytest.fail(
                    f"Arch branch detected in sidecar_ws.py line {lineno}: "
                    f"{description}.\n  Line: {line.rstrip()}\n"
                    f"The WS auth path must be arch-agnostic, Apple Silicon "
                    f"and Intel must run identical auth code. Arch selection "
                    f"happens at the Tauri externalBin spawn layer, not in "
                    f"the Python sidecar."
                )


def test_auth_uses_only_standard_library_plus_websockets():
    """The auth path must use only stdlib (asyncio, hmac, json, os, logging)"""
    source = _read_sidecar_ws_source()

    # Top-level imports (the ones loaded at module import time, not
    top_level_imports = re.findall(r"^import (\S+)$", source, re.MULTILINE)
    allowed_prefixes = (
        "asyncio",
        "contextlib",
        "hmac",
        "json",
        "logging",
        "os",
        "sys",
        "time",
        "typing",
    )
    for imp in top_level_imports:
        # Allow `from __future__ import annotations`
        if imp == "__future__":
            continue
        top = imp.split(".")[0]
        assert top in allowed_prefixes, (
            f"Non-stdlib top-level import in sidecar_ws.py: {imp}. "
            f"The auth path must use only stdlib + lazy-imported websockets."
        )


def test_rust_auth_frame_has_no_arch_branch():
    """macOS-specific: the Rust WS client's auth-frame construction must"""
    source = _read_ws_rs_source()

    # The auth frame is constructed at ws.rs:36:
    assert '"type": "auth"' in source, 'ws.rs must construct the auth frame with "type": "auth", got a different shape.'
    assert '"token": token' in source, 'ws.rs must construct the auth frame with "token": token, got a different shape.'
    assert "protocol_version" in source, "ws.rs must include the additive protocol_version field in the auth frame."

    # Scan for cfg(target_arch) anywhere in ws.rs, the WS client
    cfg_arch_re = re.compile(r"cfg\s*\(\s*target_arch\s*=")
    cfg_os_re = re.compile(r"cfg\s*\(\s*target_os\s*=")
    offenders_arch = cfg_arch_re.findall(source)
    offenders_os = cfg_os_re.findall(source)
    assert not offenders_arch, (
        f"ws.rs has cfg(target_arch=...) branches ({len(offenders_arch)}): "
        f"the WS auth path must be arch-agnostic on macOS. Move arch-"
        f"specific logic to spawn.rs (externalBin triple resolution)."
    )
    assert not offenders_os, (
        f"ws.rs has cfg(target_os=...) branches ({len(offenders_os)}): "
        f"the WS auth path must be cross-platform. Move OS-specific "
        f"logic out of ws.rs."
    )


def test_externalbin_triple_resolves_macos_arches():
    """macOS-specific: Tauri's ``externalBin`` resolves the per-arch"""
    source = _read_spawn_rs_source()

    assert '"aarch64", "macos"' in source and '"aarch64-apple-darwin"' in source, (
        'spawn.rs must map ("aarch64", "macos") → "aarch64-apple-darwin" '
        "for Tauri's externalBin to resolve the Apple Silicon sidecar."
    )
    assert '"x86_64", "macos"' in source and '"x86_64-apple-darwin"' in source, (
        'spawn.rs must map ("x86_64", "macos") → "x86_64-apple-darwin" '
        "for Tauri's externalBin to resolve the Intel sidecar."
    )


async def test_auth_protocol_identical_regardless_of_arch_env(monkeypatch):
    """macOS-specific: the auth protocol does NOT read any arch env var."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    # The constant-time comparison now lives in ipc/auth.py , spy
    from voice_typer.server.ipc import auth as _auth

    # Set a bunch of arch-related env vars that a regression MIGHT
    monkeypatch.setenv("VOICE_TYPER_ARCH", "aarch64")
    monkeypatch.setenv("VOICE_TYPER_TARGET_TRIPLE", "aarch64-apple-darwin")
    monkeypatch.setenv("HW_MACHINE", "arm64e")
    monkeypatch.setenv("ARCH", "arm64")

    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": _GOOD_TOKEN}).encode()
    ws.recv = AsyncMock(return_value=auth_frame)

    # Spy on hmac.compare_digest.
    real_compare = _auth.hmac.compare_digest
    spy = MagicMock(side_effect=real_compare)
    monkeypatch.setattr(_auth.hmac, "compare_digest", spy)

    # Must accept (token matches), arch env vars must not change this.
    assert await sw._authenticate(ws) is True
    spy.assert_called_once_with(_GOOD_TOKEN, _GOOD_TOKEN)

    # Now flip the arch env vars to x86_64 and re-run, the decision
    monkeypatch.setenv("VOICE_TYPER_ARCH", "x86_64")
    monkeypatch.setenv("VOICE_TYPER_TARGET_TRIPLE", "x86_64-apple-darwin")
    monkeypatch.setenv("HW_MACHINE", "x86_64")
    monkeypatch.setenv("ARCH", "x86_64")

    ws2 = MagicMock()
    ws2.recv = AsyncMock(return_value=auth_frame)
    spy2 = MagicMock(side_effect=real_compare)
    monkeypatch.setattr(_auth.hmac, "compare_digest", spy2)

    assert await sw._authenticate(ws2) is True
    spy2.assert_called_once_with(_GOOD_TOKEN, _GOOD_TOKEN)


# The two tests below explicitly document the mocking strategy used


def test_websockets_serve_is_mocked_in_run_path(monkeypatch):
    """Sanity check: when run() is called, no real websockets.serve fires."""
    sw = _import_sidecar_ws()

    real_serve_id = None
    try:
        from websockets.asyncio.server import serve as _real_serve

        real_serve_id = id(_real_serve)
    except Exception:
        pass  # websockets not installed, that's fine, the mock wins

    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
    mock_ws_server = MagicMock()
    mock_ws_server.sockets = [mock_socket]
    mock_ws_server.__aenter__ = AsyncMock(return_value=mock_ws_server)
    mock_ws_server.__aexit__ = AsyncMock(return_value=None)
    mock_serve = MagicMock(return_value=mock_ws_server)
    mock_websockets = MagicMock()
    mock_websockets_asyncio_server = MagicMock()
    mock_websockets_asyncio_server.serve = mock_serve
    monkeypatch.setitem(sys.modules, "websockets", mock_websockets)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", mock_websockets_asyncio_server)
    monkeypatch.setattr(sw, "_force_line_buffered_stdout", lambda: None)
    monkeypatch.setattr(asyncio, "Future", lambda: (_ for _ in ()).throw(RuntimeError("stop")))

    sw.run(_make_fake_server())

    assert mock_serve.called, "mocked serve() must be called, mocking setup is broken"
    if real_serve_id is not None:
        assert id(mock_serve) != real_serve_id, "mock_serve must not be the real websockets.serve"


async def test_os_environ_manipulation_does_not_leak_between_tests(monkeypatch):
    """monkeypatch.setenv/delenv auto-undoes after each test, verify."""
    _import_sidecar_ws()  # imports cleanly (side effect asserted)
    # Set a token, verify it's visible.
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-a")
    assert os.environ.get("VOICE_TYPER_IPC_TOKEN") == "test-a"

    # Re-set to a different value (simulating a second test).
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-b")
    assert os.environ.get("VOICE_TYPER_IPC_TOKEN") == "test-b"


async def test_auth_frame_timeout_is_5_seconds():
    """ADR-0020 §3: a client that connects but never sends the auth frame"""
    sw = _import_sidecar_ws()
    assert sw._AUTH_TIMEOUT_SECONDS == 5.0


async def test_auth_timeout_rejects_silent_client(monkeypatch):
    """A client that connects but never sends the auth frame is rejected."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    fut: asyncio.Future = asyncio.Future()

    async def _never_resolves():
        return await fut

    ws.recv = AsyncMock(side_effect=_never_resolves)
    # Patch the timeout down so the test doesn't wait 5s.
    monkeypatch.setattr(sw, "_AUTH_TIMEOUT_SECONDS", 0.1)

    assert await sw._authenticate(ws) is False
