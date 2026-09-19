# Respawn supervisor (Rust host)

Inline source: `src-tauri/src/sidecar/ws/respawn_scheduler.rs` (comments kept short there).

## Contracts

- **C-WS-3**: every respawn request carries `expected_generation: Option<u64>`. The supervisor (and the one-shot fallback) re-checks `state.ws_generation` at dequeue/run time. A stale generation must not kill a newer healthy WS connection (that produced an infinite kill/restart ping-pong). Only heartbeat-liveness and auth-failure paths pass `None`.
- **C-TOKIO-1**: `reconnect_ws` is `!Send` (tokio-tungstenite holds `!Send` across await). Respawn cannot use `tokio::spawn`; it runs on a dedicated `std::thread` + `tauri::async_runtime::block_on`.
- Supervisor is one long-lived thread, lazily started via `OnceLock<Mutex<Option<SyncSender>>>`. The Mutex lets a failed spawn be retried; the queue is `sync_channel(8)`. Full queue → drop the request (in-flight respawn already covers the outage).
- On supervisor disconnect (thread panicked), clear the cached sender and fall back to a one-shot thread.
- Auth-failure cleanup (`cleanup_and_trigger_respawn`) clears `ws_tx`, drains `pending` with `sidecar_disconnected` (dispatches can be queued during the auth window and would hang the 120s timeout), then triggers respawn with `None` generation.

## Related

- ADR-0020 §1 + §10
- Parent module: `src-tauri/src/sidecar/ws.rs`
- Python side: `voice_typer/server/sidecar_ws.py` (C-WS-1/2)
