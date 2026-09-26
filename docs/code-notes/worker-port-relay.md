# Worker port relay (ADR-0024 Step 2)

The host spawns the worker and is the only process that sees its
ephemeral port (stdout `worker_started` handshake). It relays the bind
to the slim-core sidecar so Step 3's WS client can connect. Frozen
before Steps 1/3 build on it; do not rename the event, do not add a
second event.

## Wire shape

Host→sidecar, existing host↔sidecar WS hop, TEXT frame, numeric `id`
(fire-and-forget, no pending entry, no response):

```json
{"type": "worker_started", "data": {"pid": 1234, "version": "v1", "port": 54321}, "id": 7}
```

- `type`: literal `worker_started` (already in every allowlist, no churn).
- `data.pid`: host worker child pid (u32 → JSON int).
- `data.version`: pack version (`platform::worker_path::pack_version`).
- `data.port`: worker WS port, u16 both sides (E9), always 1..=65535.
- `id`: numeric request id (C-WS-2); the sidecar sends no reply.

Sidecar→renderer re-publish on the event bus keeps the same shape.
`port` is additive: `{pid, version}`-only readers keep working.

## Validation (sidecar ingest)

Accept ⟺ `data` is an object AND `pid` is int ≥ 0 AND `version` is
str AND `port` is int 1..=65535 (`type() is int`: JSON true/false must
not pass as 1/0). Anything else: warn log, keep the prior port, no
publish, never raise, never overwrite a valid port with garbage.

## File map

- Host emit: `src-tauri/src/sidecar/spawn.rs` (`initialize_worker`
  Ok branch, pid captured before the child move) → `#host-emit`
  `src-tauri/src/sidecar/spawn/worker.rs`
  (`worker_started_relay_frame` + `relay_worker_started_to_sidecar`;
  immediate send, bounded 20 × 500 ms retry for the cold-start race
  where the sidecar link is not up yet).
- Sidecar ingest: `voice_typer/server/sidecar_ws_internals/read_loop.py`
  (intercept before dispatch: the name is an event, not a
  `_COMMAND_REGISTRY` command) → `#sidecar-ingest`
  `voice_typer/server/worker_relay.py` (single port store,
  `get_worker_port()` is the only accessor for Step 3).
- Catalogue: `voice_typer/server/event_bus.py` docstring (`port?`
  additive; `EVENT_TYPES` stays 52: relay publishes via the
  variable-form helper like the other pack/worker events).

## Anchors

- `#host-emit`: relay call site in `spawn.rs`, frame + retry in `worker.rs`.
- `#sidecar-ingest`: intercept in `read_loop.py`, store in `worker_relay.py`.
- `#wire-shape`: the JSON above (E9 pin: Rust u16 ↔ Python 1..=65535,
  covered by the round-trip tests on both sides).
