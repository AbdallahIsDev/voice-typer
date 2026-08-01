# ADR 0014: TCP IPC Session Token Authentication (SEC-018)

## Status

Accepted — implemented in `ipc_server.py:_accept_tcp` / `_handle_tcp_connection`, in the Electron host at `voice_typer/client/src/main/python/tcp-connect.ts`, and in the Tauri host at `src-tauri/src/util.rs::generate_token` + `src-tauri/src/sidecar/ws.rs`.

## Date

2026-07-14 (originally), updated 2026-07-25 for ADR-0020 Tauri migration + CR-20 error-envelope unification (YJ-36).

## Context

Voice Typer's Python backend exposes a TCP JSON-lines IPC server on `127.0.0.1:0` (OS-assigned port) and emits `{"event":"server_started","port":N}` JSON on stdout, which the host (Electron `voice_typer/client/src/main/python/tcp-connect.ts` OR Tauri `src-tauri/src/sidecar/ws.rs`) parses to discover the bound port. The fixed-port binding referenced in earlier drafts of this ADR is DEAD — the OS-assigned port + `server_started` handshake was introduced by ADR-0020 §4.1 to eliminate the port-collision class of startup failures. The host (Electron or Tauri) spawns the Python backend as a subprocess and communicates over this loopback TCP socket (or, in the Tauri WS path, over a loopback WebSocket).

**Threat model:** any local process — malware, a browser extension, an IDE plugin, a debugger, or another Electron app — can connect to the OS-assigned loopback port and send IPC commands. Without authentication, a malicious local process could:

- Invoke `quit_app` to kill the backend.
- Invoke `set_config` to change the API key endpoint to an attacker-controlled server (SEC-002).
- Invoke `get_config` to read API keys (though SEC-003 redacts them from the IPC response).
- Invoke `toggle_dictation` to start/stop recording without the user's knowledge.
- Invoke `delete_history` to destroy transcription history.

The IPC socket is designed as loopback-only (`127.0.0.1`), but loopback does not imply trust — any code running as the same user can connect to it. (The OS-assigned port is discoverable via `/proc/<pid>/net/tcp` on Linux, `netstat` on Windows, etc. — port randomization is a defense-in-depth nicety, not the primary gate.)

**Alternatives considered:**

1. **Unix domain socket with peer-credential authentication (SO_PEERCRED).** Peer-cred auth is POSIX-only; Windows has no equivalent. Voice Typer is primarily a Windows app. Rejected.

2. **mTLS (mutual TLS) between host and Python.** This would require certificate management (generation, rotation, secure storage of private keys) on both sides. The added complexity is disproportionate to the threat (local-only, same-user attacker). Rejected.

3. **Ephemeral per-launch random token.** Generate a cryptographically random token on the host side, pass it to Python via environment variable, and require it as the first message on every TCP/WS connection. This is simple, cryptographically sound (256-bit token), and works on all platforms. **Chosen.**

## Decision

Implement **per-launch session token authentication** for the TCP/WS IPC channel:

1. **Token generation (host side):** On each launch, the host generates a 256-bit random token.
   - Electron host: `voice_typer/client/src/main/python/tcp-connect.ts` uses `crypto.randomBytes(32).toString("hex")`.
   - Tauri host: `src-tauri/src/util.rs::generate_token` uses the `rand` crate's `OsRng` to produce a 32-byte cryptographically random value, hex-encoded.
   Both hosts then set the `VOICE_TYPER_IPC_TOKEN` environment variable on the Python subprocess.

2. **Token delivery (host → Python):** The token is passed to the Python subprocess via the `VOICE_TYPER_IPC_TOKEN` environment variable. Both the Electron `spawn()` call, the Tauri `Command::sidecar` invocation, and the standalone launcher (`electron_launcher.py`) set this variable.

3. **Auth handshake (TCP/WS connect):** The first JSON line the host client sends after connecting must be `{"type": "auth", "token": "<token>"}`. The Python server reads exactly one line before processing any other commands.

4. **Auth validation (Python side):** The server validates the token using `hmac.compare_digest()` for constant-time comparison (preventing timing side-channel attacks). If the token is missing, wrong, or malformed, the server sends the error-envelope response `{"type": "error", "data": {"code": "auth_failed", "message": "authentication failed"}}` (TCP: `voice_typer/server/ipc_server.py:1033-1058`; WS: `voice_typer/server/sidecar_ws.py:478-488`) and closes the connection immediately. (Earlier drafts of this ADR documented the response without the `code` field — that was a CR-20 / YJ-36 fix; the error envelope REQUIRES `code` so the renderer's `ErrorEvent` type narrowing works.)

   **Note (YJ-FIX-D2):** the canonical `ERROR_CODES` registry in `voice_typer/server/ipc/validation.py` lists `client.auth_failed` as the target namespaced code; the emitter currently uses the legacy `auth_failed` alias per the `LEGACY_ERROR_CODES` backward-compat mapping (`validation.py:129-148`). The `id` field is omitted because auth-fail happens BEFORE request dispatch (no request id exists to correlate).

5. **Auth timeout (PR-3-FIX-1):** A 5-second `settimeout` is applied to the socket before the auth read to prevent a "connect-and-stall" DoS attack (where a malicious client opens a TCP connection but never sends the auth line, holding the dispatcher thread indefinitely).

6. **Lock-free auth:** The auth handshake is performed **outside** `self._lock` (the IPC server's main lock). This prevents a stalled auth read from blocking `push()` events and other IPC dispatch threads (PR-3-FIX-1).

7. **Fallback mode:** When `VOICE_TYPER_IPC_TOKEN` is not set (e.g., running `python -m voice_typer.server.ipc_server` from a terminal for debugging), the server emits a warning and accepts unauthenticated connections. This preserves the developer workflow without breaking security for production use.

## Consequences

### Easier
- **Defense-in-depth:** The token check is the primary gate; SEC-019 (client-side command allowlist) is the secondary gate. Both must be bypassed for a successful attack.
- **Minimal overhead:** Token generation + comparison adds ~200 µs to connection setup, negligible against the ~5-10 second Python import time.
- **No external dependencies:** Uses only `secrets` / `crypto.randomBytes` / `rand::OsRng` and `hmac.compare_digest`, all in the standard library.

### More difficult
- **Standalone mode auth:** When the host spawns Python (the norm), the token is passed via environment variable. But when Python spawns the Electron frontend (standalone mode, `python -m voice_typer.server.ipc_server`), the token must be passed the other way — from Python to Electron. This is handled by `electron_launcher.generate_session_token()` setting `VOICE_TYPER_IPC_TOKEN` before spawning Electron.
- **Token lifetime:** The token lives for the entire process lifetime. A long-running backend (hours) uses the same token. Rotating the token would require reconnecting all clients, which is not currently supported.
- **Tauri ↔ Electron parity:** The token-generation + auth-frame-send logic is duplicated across the Electron (`tcp-connect.ts`) and Tauri (`util.rs` + `ws.rs`) hosts. A future refactor could extract a shared spec, but the two implementations are small enough (~50 LOC each) that the duplication is acceptable during the ADR-0020 mixed-mode period.

### Risks
- **Environment variable leakage:** The token is passed via `VOICE_TYPER_IPC_TOKEN` which is visible in `/proc/<pid>/environ` on Linux and `GetEnvironmentStrings` on Windows. A local attacker with process-inspection capabilities can read it. Mitigation: `hmac.compare_digest` prevents token reuse beyond the single connection, but the attacker could authenticate directly. Acceptable risk — the IPC socket is loopback-only, and any local process with debug privileges can already interact with the app in other ways.
- **Race condition in standalone mode:** The token env var must be set BEFORE `start_tcp()` spawns the accept thread; otherwise, the thread reads an empty token and falls into unauthenticated mode. This is handled by ordering the calls (set env → start_tcp) in `main()`.

## References

- `voice_typer/server/ipc_server.py:_accept_tcp` (lines 796-898) — TCP accept + auth handshake.
- `voice_typer/server/ipc_server.py:_handle_tcp_connection` (lines 870-898) — auth validation.
- `voice_typer/client/src/main/python/tcp-connect.ts` — Electron host token generation (`IPC_TOKEN`) and auth line send.
- `src-tauri/src/util.rs::generate_token` — Tauri host token generation.
- `src-tauri/src/sidecar/ws.rs` — Tauri host auth frame send + port discovery.
- `voice_typer/server/electron_launcher.py` — standalone mode token generation.
- `docs/architecture/error-envelope-contract.md` — canonical `{type, data: {code, message}, id}` error-envelope contract.
- `tests/test_e2e_pipeline.py` — `TestAuthEnforcement` tests (wrong token, no auth line, stalled timeout).
- SECURITY.md — SEC-018 documentation.

## Supersedes

This ADR's port + token-path + error-envelope sections are SUPERSEDED by:
- **ADR-0020 §3** (canonical error-envelope contract) — for the `{type, data: {code, message}, id}` shape of the auth-fail response. The earlier fixed-port reference in this ADR is dead; ADR-0020 §4.1 is canonical for the OS-assigned-port + `server_started` handshake.
- **ADR-0020 §4.1** — for the OS-assigned port + `server_started` stdout handshake that replaces the previous fixed-port binding.
- The Tauri host paths (`src-tauri/src/util.rs`, `src-tauri/src/sidecar/ws.rs`) are NEW (added during the ADR-0020 Tauri migration) and are not in the original 2026-07-14 draft of this ADR.

*End of document.*
