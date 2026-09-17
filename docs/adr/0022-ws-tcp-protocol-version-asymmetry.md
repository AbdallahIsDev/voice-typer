# ADR 0022: Sidecar WS Protocol-Version Check Stays Advisory While TCP Rejects

## Status

Accepted (deliberate asymmetry; doc-only, no transport behavior change).

## Date

2026-09-16

## Context

Both IPC transports validate the `protocol_version` handshake field against the
single source of truth `PROTOCOL_VERSION`
(`voice_typer/server/ipc/protocol_version.py`; cross-language parity with the
Rust `EXPECTED_PROTOCOL_VERSION` and the TypeScript `IPC_PROTOCOL_VERSION` is
pinned by `tests/test_ipc_protocol_cross_language_parity.py`), but they react
differently to skew:

- **Sidecar WebSocket** (`voice_typer/server/sidecar_ws_internals/handshake.py`):
  logs a prominent WARNING and **continues** the connection (advisory-only;
  "defense-in-depth, not a security gate").
- **TCP** (`voice_typer/server/ipc/transport_tcp.py`): sends a structured
  `server.protocol_version_mismatch` error envelope and **closes** the socket.

The WS check was added as advisory; the TCP check was added later as a hard
rejection. The two were never reconciled, so a Tauri host/sidecar version skew
authenticates over WS and can surface as confusing partial-failure symptoms
instead of a clean rejection.

## Decision

Keep the asymmetry deliberately until the single-transport Tauri world
(ADR-0020 cutover):

1. The WS handshake stays warn-and-continue. A blind WS reject would turn
   version skew into a total disconnect: the Rust supervisor treats a refused
   connection as a dead sidecar and enters its respawn loop, replacing a
   diagnosable degraded session with an endless kill/restart cycle.
2. The TCP handshake keeps rejecting with `server.protocol_version_mismatch`,
   so the Electron path surfaces skew as a structured error the renderer can
   display.
3. Skew stays observable on the WS path via the WARNING log line naming both
   versions; no silent behavior.

## Consequences

### Easier

- Version skew on the Tauri path degrades to a logged, diagnosable state
  instead of a disconnect loop.
- Each transport keeps the failure mode appropriate to its supervisor (Rust
  respawn loop vs Electron renderer error surface).

### More difficult / risks

- Tauri skew can still present as partial dispatch failures; operators must
  check the log WARNING rather than receiving a structured error.

## Revisit condition

Reconcile when the Electron/TCP path is removed at the ADR-0020 cutover:
either make the WS handshake reject (safe once no second transport exists to
compare against) or keep it advisory and add renderer-visible surfacing for the
skew warning. Do not change either side's behavior before then.

## References

- `voice_typer/server/sidecar_ws_internals/handshake.py` (advisory check)
- `voice_typer/server/ipc/transport_tcp.py` (rejecting check)
- `voice_typer/server/ipc/protocol_version.py` (single source of truth)
- `tests/test_sidecar_ws_protocol_version.py`, `tests/test_ipc_protocol_versioning.py`
- ADR-0020 (single-transport Tauri world)
