# SidecarWS

**File**: `voice_typer/server/sidecar_ws.py` (see the file directly)

## Responsibility

The `SidecarWS` module implements the WebSocket server side of the Tauri↔Python bridge (ADR-0020). It replaces the TCP‑based IPC transport when running as a Tauri sidecar, allowing the Rust host to communicate with the Python server over WebSocket.

Key responsibilities:
- Bind a WebSocket server on `127.0.0.1:0` (random port)
- Emit `{"event":"server_started","port":N}` to stdout for the Rust host to discover
- Perform one-shot bearer-token auth (constant-time `hmac.compare_digest` comparison; NOT an HMAC scheme — no per-message MAC, no nonce, no key derivation, no replay protection). Compensating controls are loopback-only bind + ephemeral port + per-respawn token rotation.
- Dispatch incoming WS frames through `IPCServer._dispatch` (reusing the 63-command registry unchanged)
- Reuse the ADR-0019 per-connection rate limiter
- Cap frames at 1 MiB
- Handle `{"type":"shutdown"}` cooperative shutdown

## Entry Points

- **`run(server: IPCServer) -> int`** — the main entry point. Called by `ipc_server.py` when the `--ws` CLI flag is set (which gates on `TAURI_SIDECAR=1`). Binds the localhost WebSocket server on an ephemeral port, emits `server_started` to stdout, then enters the asyncio accept loop. Returns the bound port; blocks until the loop is cancelled (e.g. SIGTERM from the host's `kill_children` backstop).
- **`_emit_server_started(port: int, protocol: int | None = None)`** — writes the single structured `{"event":"server_started","port":N}` (and, when `protocol` is not `None`, the companion `"protocol"` field) JSON line to stdout so the Rust host can read the port number and detect protocol-version skew at handshake time.
- **`_authenticate(websocket) -> bool`** — performs the one-shot bearer-token auth handshake. Reads the first WS frame, validates `type == "auth"` + the `token` string, compares it against the `VOICE_TYPER_IPC_TOKEN` env var using `hmac.compare_digest` (constant-time), and returns `True` on match / `False` on rejection (the host treats a rejection as a crash → respawn with a fresh token).

## IPC Surface

The `sidecar_ws` module **reuses** the existing 63-command IPC registry from `IPCServer._COMMAND_REGISTRY`. It does NOT add any new commands. The WS transport replaces the TCP transport end-to-end:
- Inbound dispatch frames → `IPCServer._dispatch()` (same as TCP)
- Outbound push events → `IPCServer._publish_push_event()` (same as TCP)
- Rate limiting → same `_RateLimiter` from `ipc_server.py`

The WS bridge adds one synthetic frame type:
- `{"type":"shutdown"}` — triggers cooperative shutdown of the WS server loop.
