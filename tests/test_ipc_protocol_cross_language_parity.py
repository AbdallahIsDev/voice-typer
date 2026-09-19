"""Cross-language parity for the IPC protocol version."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from voice_typer.server.ipc.protocol_version import PROTOCOL_VERSION as IPC_PROTOCOL_VERSION

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

RUST_WS_PATH = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws.rs"
# After the consolidation, the Python WS receiver's ``PROTOCOL_VERSION``
PYTHON_WS_PATH = REPO_ROOT / "voice_typer" / "server" / "ipc" / "protocol_version.py"
TS_PUSH_EVENTS_PATH = (
    REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "push_events.ts"
)


# Python (transport_tcp.py): ``IPC_PROTOCOL_VERSION: int = 1``
_PYTHON_TCP_RE = re.compile(
    r"^IPC_PROTOCOL_VERSION\s*:\s*int\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)

# Python (canonical source of truth: ``ipc/protocol_version.py``):
_PYTHON_WS_RE = re.compile(
    r"^PROTOCOL_VERSION\s*:\s*int\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)

# Rust (ws.rs): ``const EXPECTED_PROTOCOL_VERSION: u64 = 1;``
_RUST_RE = re.compile(
    r"const\s+EXPECTED_PROTOCOL_VERSION\s*:\s*u64\s*=\s*(\d+)\s*;",
)

# TypeScript (push_events.ts): ``export const IPC_PROTOCOL_VERSION = 1;``
_TS_RE = re.compile(
    r"export\s+const\s+IPC_PROTOCOL_VERSION\s*=\s*(\d+)\s*;",
)


def _extract_int(pattern: re.Pattern[str], text: str, source_name: str) -> int:
    """Find the first match of *pattern* in *text* and return its int capture."""
    match = pattern.search(text)
    if match is None:
        pytest.fail(
            f"Could not find the protocol-version constant in {source_name} "
            f"using pattern {pattern.pattern!r}. The declaration may have "
            "been renamed, moved, or had its syntax changed, update the "
            "regex in this test to match the new form."
        )
    return int(match.group(1))


def test_python_tcp_protocol_version_constant_exists() -> None:
    """accidentally sets it to ``0`` or a non-int would silently accept all"""
    assert isinstance(IPC_PROTOCOL_VERSION, int)
    assert IPC_PROTOCOL_VERSION > 0


def test_python_ws_protocol_version_matches_tcp() -> None:
    """The canonical Python WS-receiver ``PROTOCOL_VERSION``"""
    assert PYTHON_WS_PATH.is_file(), (
        f"protocol_version.py not found at {PYTHON_WS_PATH}, the file may have "
        "been renamed or moved; update the path in this test."
    )
    text = PYTHON_WS_PATH.read_text(encoding="utf-8")
    ws_version = _extract_int(_PYTHON_WS_RE, text, str(PYTHON_WS_PATH))
    assert ws_version == IPC_PROTOCOL_VERSION, (
        f"protocol_version.py:PROTOCOL_VERSION={ws_version} does not match "
        f"transport_tcp.py:IPC_PROTOCOL_VERSION={IPC_PROTOCOL_VERSION}. "
        "Both Python receivers MUST agree on the protocol version."
    )


def test_rust_host_protocol_version_matches_python() -> None:
    """The Rust host's ``EXPECTED_PROTOCOL_VERSION`` (ws.rs) MUST equal"""
    assert RUST_WS_PATH.is_file(), (
        f"ws.rs not found at {RUST_WS_PATH}, the file may have been renamed or moved; update the path in this test."
    )
    text = RUST_WS_PATH.read_text(encoding="utf-8")
    rust_version = _extract_int(_RUST_RE, text, str(RUST_WS_PATH))
    assert rust_version == IPC_PROTOCOL_VERSION, (
        f"ws.rs:EXPECTED_PROTOCOL_VERSION={rust_version} does not match "
        f"transport_tcp.py:IPC_PROTOCOL_VERSION={IPC_PROTOCOL_VERSION}. "
        "The Rust host and Python receiver MUST agree on the protocol "
        "version."
    )


def test_ts_push_events_protocol_version_matches_python() -> None:
    """The TS ``IPC_PROTOCOL_VERSION`` constant in push_events.ts MUST"""
    assert TS_PUSH_EVENTS_PATH.is_file(), (
        f"push_events.ts not found at {TS_PUSH_EVENTS_PATH}, the file "
        "may have been renamed or moved; update the path in this test."
    )
    text = TS_PUSH_EVENTS_PATH.read_text(encoding="utf-8")
    ts_version = _extract_int(_TS_RE, text, str(TS_PUSH_EVENTS_PATH))
    assert ts_version == IPC_PROTOCOL_VERSION, (
        f"push_events.ts:IPC_PROTOCOL_VERSION={ts_version} does not match "
        f"transport_tcp.py:IPC_PROTOCOL_VERSION={IPC_PROTOCOL_VERSION}. "
        "The renderer's TS constant and Python receiver MUST agree on "
        "the protocol version."
    )


def test_all_four_constants_agree() -> None:
    """same integer. This is the core cross-language parity guard —"""
    python_tcp = IPC_PROTOCOL_VERSION

    ws_text = PYTHON_WS_PATH.read_text(encoding="utf-8")
    python_ws = _extract_int(_PYTHON_WS_RE, ws_text, str(PYTHON_WS_PATH))

    rust_text = RUST_WS_PATH.read_text(encoding="utf-8")
    rust = _extract_int(_RUST_RE, rust_text, str(RUST_WS_PATH))

    ts_text = TS_PUSH_EVENTS_PATH.read_text(encoding="utf-8")
    ts = _extract_int(_TS_RE, ts_text, str(TS_PUSH_EVENTS_PATH))

    versions = {
        "python_canonical (protocol_version.py:PROTOCOL_VERSION)": python_ws,
        "python_tcp (transport_tcp.py:IPC_PROTOCOL_VERSION)": python_tcp,
        "rust (ws.rs:EXPECTED_PROTOCOL_VERSION)": rust,
        "ts (push_events.ts:IPC_PROTOCOL_VERSION)": ts,
    }
    distinct = set(versions.values())
    assert len(distinct) == 1, (
        "Protocol version constants have drifted across languages. "
        f"Current values: {versions}. Bumping the protocol version "
        "requires updating ALL constants in lockstep, see the "
        "docstring at the top of this test file."
    )


def test_auth_frame_interface_declares_optional_protocol_version() -> None:
    """The TS ``AuthFrame`` interface in push_events.ts MUST declare"""
    text = TS_PUSH_EVENTS_PATH.read_text(encoding="utf-8")
    # Match `export interface AuthFrame { ... }` block.
    match = re.search(
        r"export\s+interface\s+AuthFrame\s*\{(?P<body>[^}]*)\}",
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "AuthFrame interface not found in push_events.ts. The interface "
        "declares the auth-frame wire shape and should be exported so "
        "future renderer code can type-annotate auth-frame construction."
    )
    body = match.group("body")
    assert "protocol_version" in body, (
        "AuthFrame interface must declare `protocol_version?: number` "
        "so the auth-frame wire shape is type-safe on the TS side. "
        f"Interface body: {body!r}"
    )
    assert "number" in body, "AuthFrame.protocol_version must be typed as `number` (the wire form is a JSON integer)."


def test_protocol_version_mismatch_registered_in_error_codes() -> None:
    """The ``server.protocol_version_mismatch`` error code MUST"""
    from voice_typer.server.ipc.validation import ERROR_CODES, ErrorCodes

    assert ErrorCodes.PROTOCOL_VERSION_MISMATCH == "server.protocol_version_mismatch"
    assert "server.protocol_version_mismatch" in ERROR_CODES, (
        "server.protocol_version_mismatch must be in ERROR_CODES so the "
        "renderer's TS ErrorCodes union and the cross-language parity "
        "audit can verify all language surfaces agree on the code."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
