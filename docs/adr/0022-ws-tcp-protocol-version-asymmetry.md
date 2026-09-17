# ADR 0022: Sidecar WS Protocol-Version Check Stays Advisory (TCP Reject Path Retired)

## Status

Accepted → **partially superseded by the Electron/TCP removal (ADR-0020
cutover, 2026-09)**. The deliberate WS-vs-TCP asymmetry no longer
exists as a live dual-transport concern: TCP is gone. The WS handshake
remains warn-and-continue by original design (a blind reject would turn
version skew into a Rust supervisor respawn loop). Revisit only if
renderer-visible surfacing of the skew WARNING is later requested.

## Date

2026-09-16
(2026-09: TCP path retired; status text updated for the single-transport world)

## Context

Both IPC transports validated the `protocol_version` handshake field against the
single source of truth `PROTOCOL_VERSION`
(`voice_typer/server/ipc/protocol_version.py`; cross-language parity with the
Rust `EXPECTED_PROTOCOL_VERSION` and the TypeScript `IPC_PROTOCOL_VERSION` is
pinned by `tests/test_ipc_protocol_cross_language_parity.py`), but they reacted
differently to skew:

- **Sidecar WebSocket** (`voice_typer/server/sidecar_ws_internals/handshake.py`):
  logs a prominent WARNING and **continues** the connection (advisory-only;
  "defense-in-depth, not a security gate").
- **TCP** (`voice_typer/server/ipc/transport_tcp.py`): sent a structured
  `server.protocol_version_mismatch` error envelope and **closed** the socket.
  **(retired with Electron/TCP removal — Lane B)**

The WS check was added as advisory; the TCP check was added later as a hard
rejection. The two were never reconciled while both transports lived.

## Decision

Keep the WS handshake warn-and-continue. The TCP reject path is retired
with the transport:

1. The WS handshake stays warn-and-continue. A blind WS reject would turn
   version skew into a total disconnect: the Rust supervisor treats a refused
   connection as a dead sidecar and enters its respawn loop, replacing a
   diagnosable degraded session with an endless kill/restart cycle.
2. Skew stays observable on the WS path via the WARNING log line naming both
   versions; no silent behavior.
3. Do not reintroduce a hard reject without a plan for supervisor-loop
   interaction (see Revisit condition).

## Consequences

### Easier

- One transport, one protocol-version policy (advisory WARNING).
- Version skew on the Tauri path degrades to a logged, diagnosable state
  instead of a disconnect loop.

### More difficult / risks

- Skew can still present as partial dispatch failures; operators must
  check the log WARNING rather than receiving a structured error.

## Revisit condition

Add renderer-visible surfacing of the skew WARNING (or a bounded reject
that the supervisor treats as non-fatal) if operators report that the log
line is insufficient. Do not make the handshake hard-reject while the
Rust supervisor's refused-connection path is a full respawn loop.

## References

- `voice_typer/server/sidecar_ws_internals/handshake.py` (advisory check)
- `voice_typer/server/ipc/protocol_version.py` (single source of truth)
- `tests/test_sidecar_ws_protocol_version.py`
- ADR-0020 (single-transport Tauri world; Electron/TCP removal)
