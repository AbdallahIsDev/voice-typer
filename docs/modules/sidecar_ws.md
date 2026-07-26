# SidecarWS

**File**: `voice_typer/server/sidecar_ws.py` (see the file directly)

## Responsibility

The `SidecarWS` module implements the WebSocket server side of the Tauri↔Python bridge (ADR-0020). It replaces the TCP‑based IPC transport when running as a Tauri sidecar, allowing the Rust host to communicate with the Python server over WebSocket.

Key responsibilities:
- Bind a WebSocket server on `127.0.0.1:0` (random port)
- Emit `{"event":"server_started","port":N}` to stdout for the Rust host to discover
- Perform HMAC auth handshake with the bearer token
- Dispatch incoming WS frames through `IPCServer._dispatch` (reusing the 63-command registry unchanged)
- Reuse the ADR-0019 per-connection rate limiter
- Cap frames at 1 MiB
- Handle `{"type":"shutdown"}` cooperative shutdown

## Entry Points

- **`run()`** — the main entry point. Called by `ipc_server.py` when the `--ws` CLI flag is set (which gates on `TAURI_SIDECAR=1`). Starts the WebSocket server and enters the event loop.
- **`_emit_server_started(port)`** — writes the startup JSON to stdout so the Rust host can read the port number.
- **`_authenticate(websocket, expected_token)`** — performs the bearer-token auth handshake.

## IPC Surface

The `sidecar_ws` module **reuses** the existing 63-command IPC registry from `IPCServer._COMMAND_REGISTRY`. It does NOT add any new commands. The WS transport replaces the TCP transport end-to-end:
- Inbound dispatch frames → `IPCServer._dispatch()` (same as TCP)
- Outbound push events → `IPCServer._publish_push_event()` (same as TCP)
- Rate limiting → same `_RateLimiter` from `ipc_server.py`

The WS bridge adds one synthetic frame type:
- `{"type":"shutdown"}` — triggers cooperative shutdown of the WS server loop.
