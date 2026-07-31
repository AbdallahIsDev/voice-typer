"""DR-21 (S1-CR-78): cross-language parity for the IPC protocol version.

The IPC wire protocol version is a single integer that MUST be kept in
lockstep across three language surfaces:

  - Python (TCP receiver): ``voice_typer/server/ipc/transport_tcp.py``
    defines ``IPC_PROTOCOL_VERSION = 1`` and rejects auth frames whose
    explicit ``protocol_version`` field does not match.
  - Python (WS receiver): ``voice_typer/server/sidecar_ws.py`` defines
    ``PROTOCOL_VERSION = 1`` and logs a WARNING on mismatch (advisory
    only — does not reject).
  - Rust (host sender): ``src-tauri/src/sidecar/ws.rs`` defines
    ``const EXPECTED_PROTOCOL_VERSION: u64 = 1`` and sends it in its
    auth frame.
  - TypeScript (renderer contract): ``voice_typer/client/src/renderer/
    src/types/ipc/push_events.ts`` exports ``IPC_PROTOCOL_VERSION`` so
    any future renderer-side auth-frame construction (e.g. an Electron
    fallback path) can reference the same constant.

A drift between any two of these would either:
  - cause a stale client to be rejected with an opaque ``auth_failed``
    (if the receiver is ahead of the sender), OR
  - let an incompatible frame through to the dispatch layer where it
    fails with a confusing ``unknown_command`` (if the sender is ahead
    of the receiver).

This file is the regression guard: if any of the four constants drifts
out of sync, this test fails before the change can be merged. Bumping
the protocol version is a deliberate, multi-file change — never an
accidental one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from voice_typer.server.ipc.transport_tcp import IPC_PROTOCOL_VERSION

# ────────────────────────────────────────────────────────────────────────────
# Paths
# ────────────────────────────────────────────────────────────────────────────

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

RUST_WS_PATH = REPO_ROOT / "src-tauri" / "src" / "sidecar" / "ws.rs"
PYTHON_WS_PATH = REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
TS_PUSH_EVENTS_PATH = (
    REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc" / "push_events.ts"
)


# ────────────────────────────────────────────────────────────────────────────
# Regexes (one per language surface — the constant is declared with
# different syntax in each file)
# ────────────────────────────────────────────────────────────────────────────

# Python (transport_tcp.py): ``IPC_PROTOCOL_VERSION: int = 1``
_PYTHON_TCP_RE = re.compile(
    r"^IPC_PROTOCOL_VERSION\s*:\s*int\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)

# Python (sidecar_ws.py): ``PROTOCOL_VERSION: int = 1``
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
    """Find the first match of *pattern* in *text* and return its int capture.

    Fails with a clear message if the pattern doesn't match — the
    constant may have been renamed, moved, or had its declaration
    syntax changed (e.g. ``const`` → ``let`` in Rust). The parity test
    is only useful if it can actually find the constant in each file.
    """
    match = pattern.search(text)
    if match is None:
        pytest.fail(
            f"Could not find the protocol-version constant in {source_name} "
            f"using pattern {pattern.pattern!r}. The declaration may have "
            "been renamed, moved, or had its syntax changed — update the "
            "regex in this test to match the new form."
        )
    return int(match.group(1))


# ────────────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────────────


def test_python_tcp_protocol_version_constant_exists() -> None:
    """Sanity: the Python TCP receiver's ``IPC_PROTOCOL_VERSION`` is a
    positive int (imported at the top of this file). A future bump that
    accidentally sets it to ``0`` or a non-int would silently accept all
    auth frames (``0 == 0``) or raise a TypeError during the comparison.
    """
    assert isinstance(IPC_PROTOCOL_VERSION, int)
    assert IPC_PROTOCOL_VERSION > 0


def test_python_ws_protocol_version_matches_tcp() -> None:
    """The Python WS receiver's ``PROTOCOL_VERSION`` (sidecar_ws.py)
    MUST equal the TCP receiver's ``IPC_PROTOCOL_VERSION``
    (transport_tcp.py). Both surfaces implement the same auth-frame
    contract — a drift would mean the same client is accepted on one
    transport but rejected (or warned) on the other.
    """
    assert PYTHON_WS_PATH.is_file(), (
        f"sidecar_ws.py not found at {PYTHON_WS_PATH} — the file may have "
        "been renamed or moved; update the path in this test."
    )
    text = PYTHON_WS_PATH.read_text(encoding="utf-8")
    ws_version = _extract_int(_PYTHON_WS_RE, text, str(PYTHON_WS_PATH))
    assert ws_version == IPC_PROTOCOL_VERSION, (
        f"sidecar_ws.py:PROTOCOL_VERSION={ws_version} does not match "
        f"transport_tcp.py:IPC_PROTOCOL_VERSION={IPC_PROTOCOL_VERSION}. "
        "Both Python receivers MUST agree on the protocol version."
    )


def test_rust_host_protocol_version_matches_python() -> None:
    """The Rust host's ``EXPECTED_PROTOCOL_VERSION`` (ws.rs) MUST equal
    the Python TCP receiver's ``IPC_PROTOCOL_VERSION``. The Rust host
    constructs the auth frame with this integer; the Python receiver
    validates it. A drift would either reject a valid host (if Python
    is ahead) or let an incompatible frame through (if Rust is ahead).
    """
    assert RUST_WS_PATH.is_file(), (
        f"ws.rs not found at {RUST_WS_PATH} — the file may have been renamed or moved; update the path in this test."
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
    """The TS ``IPC_PROTOCOL_VERSION`` constant in push_events.ts MUST
    equal the Python ``IPC_PROTOCOL_VERSION``. The TS constant is the
    renderer's compile-time reference for the auth-frame contract; any
    future Electron-side auth-frame construction (e.g. the Electron
    fallback path) references this constant instead of bare-coding the
    integer.
    """
    assert TS_PUSH_EVENTS_PATH.is_file(), (
        f"push_events.ts not found at {TS_PUSH_EVENTS_PATH} — the file "
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
    """Belt-and-suspenders: all four language surfaces agree on the
    same integer. This is the core DR-21 cross-language parity guard —
    if any future bump touches only one file, this test fails before
    the change can be merged.

    Bumping the protocol version is a deliberate, multi-file change:
      1. ``voice_typer/server/ipc/transport_tcp.py:IPC_PROTOCOL_VERSION``
      2. ``voice_typer/server/sidecar_ws.py:PROTOCOL_VERSION``
      3. ``src-tauri/src/sidecar/ws.rs:EXPECTED_PROTOCOL_VERSION``
      4. ``voice_typer/client/src/renderer/src/types/ipc/push_events.ts:
         IPC_PROTOCOL_VERSION``
    """
    python_tcp = IPC_PROTOCOL_VERSION

    ws_text = PYTHON_WS_PATH.read_text(encoding="utf-8")
    python_ws = _extract_int(_PYTHON_WS_RE, ws_text, str(PYTHON_WS_PATH))

    rust_text = RUST_WS_PATH.read_text(encoding="utf-8")
    rust = _extract_int(_RUST_RE, rust_text, str(RUST_WS_PATH))

    ts_text = TS_PUSH_EVENTS_PATH.read_text(encoding="utf-8")
    ts = _extract_int(_TS_RE, ts_text, str(TS_PUSH_EVENTS_PATH))

    versions = {
        "python_tcp (transport_tcp.py:IPC_PROTOCOL_VERSION)": python_tcp,
        "python_ws (sidecar_ws.py:PROTOCOL_VERSION)": python_ws,
        "rust (ws.rs:EXPECTED_PROTOCOL_VERSION)": rust,
        "ts (push_events.ts:IPC_PROTOCOL_VERSION)": ts,
    }
    distinct = set(versions.values())
    assert len(distinct) == 1, (
        "Protocol version constants have drifted across languages. "
        f"Current values: {versions}. Bumping the protocol version "
        "requires updating ALL FOUR constants in lockstep — see the "
        "docstring at the top of this test file."
    )


def test_auth_frame_interface_declares_optional_protocol_version() -> None:
    """The TS ``AuthFrame`` interface in push_events.ts MUST declare
    ``protocol_version?: number`` so renderer code that constructs or
    parses auth frames has compile-time help. The field is OPTIONAL
    (legacy senders may omit it; the receiver's validate-if-present
    check skips to the token check).
    """
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
    """DR-21: the ``server.protocol_version_mismatch`` error code MUST
    be registered in the central :class:`ErrorCodes` registry in
    ``voice_typer/server/ipc/validation.py``. The transport_tcp.py
    module references this via ``ErrorCodes.PROTOCOL_VERSION_MISMATCH``
    (and keeps a ``PROTOCOL_VERSION_MISMATCH_CODE`` alias for
    backward-compat with tests that import the constant directly).
    """
    from voice_typer.server.ipc.validation import ERROR_CODES, ErrorCodes

    assert ErrorCodes.PROTOCOL_VERSION_MISMATCH == "server.protocol_version_mismatch"
    assert "server.protocol_version_mismatch" in ERROR_CODES, (
        "server.protocol_version_mismatch must be in ERROR_CODES so the "
        "renderer's TS ErrorCodes union and the cross-language parity "
        "audit can verify all language surfaces agree on the code."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
