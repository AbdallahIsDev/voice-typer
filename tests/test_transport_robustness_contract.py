"""Cross-host transport-robustness contract pins (MO-122).

The Electron main process and the Tauri host must enforce the SAME
pending-map threshold and the SAME machine-readable backpressure code.
A drift here means a renderer branching on `_code === "pending_full"`
sees a different value per host.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"missing source file: {rel}"
    return path.read_text(encoding="utf-8")


def test_pending_threshold_matches_across_hosts() -> None:
    """Electron `MAX_PENDING_REQUESTS` must equal Rust `PENDING_MAX`."""
    electron = _read("voice_typer/client/src/main/state.ts")
    rust = _read("src-tauri/src/commands/sidecar_cmds/allowlist.rs")

    m_e = re.search(r"export const MAX_PENDING_REQUESTS\s*=\s*(\d+)", electron)
    assert m_e is not None, "MAX_PENDING_REQUESTS not found in state.ts"
    m_r = re.search(r"pub\(crate\) const PENDING_MAX:\s*usize\s*=\s*(\d+)", rust)
    assert m_r is not None, "PENDING_MAX not found in allowlist.rs"

    electron_n = int(m_e.group(1))
    rust_n = int(m_r.group(1))
    assert electron_n == rust_n, (
        f"pending-map threshold drifted: Electron MAX_PENDING_REQUESTS={electron_n} "
        f"vs Rust PENDING_MAX={rust_n}. Align both to the same value (MO-122)."
    )
    # Canonical value: 1024 (sized for genuine concurrent IPC; see the
    # doc comments at both declaration sites).
    assert electron_n == 1024


def test_pending_full_code_matches_across_hosts() -> None:
    """Electron pending-cap reject must use the same code as Rust."""
    electron_codes = _read("voice_typer/client/src/shared/python-call-error-code.ts")
    electron_send = _read("voice_typer/client/src/main/python/send-to-python.ts")
    rust = _read("src-tauri/src/commands/sidecar_cmds/allowlist.rs")

    assert '"pending_full"' in electron_codes, "PYTHON_CALL_ERROR_CODES must include 'pending_full' (MO-122)"
    assert "PENDING_FULL_CODE" in rust
    m = re.search(r'PENDING_FULL_CODE:\s*&str\s*=\s*"([^"]+)"', rust)
    assert m is not None
    assert m.group(1) == "pending_full"

    # The Electron pending-cap reject site must stamp the same code.
    cap_block = electron_send[electron_send.index("pendingRequests.size >= MAX_PENDING_REQUESTS") :]
    assert '"pending_full"' in cap_block or "'pending_full'" in cap_block, (
        "send-to-python.ts pending-cap rejection must use PythonIpcError('pending_full', ...)"
    )


def test_duplicate_connection_code_is_shared() -> None:
    """Both transports reject a second live client with the same code."""
    validation = _read("voice_typer/server/ipc/validation.py")
    tcp = _read("voice_typer/server/ipc/transport_tcp.py")
    ws = _read("voice_typer/server/sidecar_ws_internals/connection.py")

    m = re.search(r'DUPLICATE_CONNECTION\s*=\s*"([^"]+)"', validation)
    assert m is not None
    code = m.group(1)
    assert code == "server.duplicate_connection"

    assert "DUPLICATE_CONNECTION" in tcp, (
        "TCP transport must reject duplicate auth with ErrorCodes.DUPLICATE_CONNECTION"
    )
    assert "DUPLICATE_CONNECTION" in ws, "WS transport must reject duplicate auth with ErrorCodes.DUPLICATE_CONNECTION"
