# ADR 0014: TCP IPC Session Token Authentication (SEC-018)

## Status

Accepted — implemented in `ipc_server.py:_accept_tcp` / `_handle_tcp_connection` and `client/src/main/index.ts`.

## Date

2026-07-14

## Context

Voice Typer's Python backend exposes a TCP JSON-lines IPC server on `127.0.0.1:9876` (the default port). The Electron main process spawns the Python backend as a subprocess and communicates over this loopback TCP socket.

**Threat model:** any local process — malware, a browser extension, an IDE plugin, a debugger, or another Electron app — can connect to `127.0.0.1:9876` and send IPC commands. Without authentication, a malicious local process could:

- Invoke `quit_app` to kill the backend.
- Invoke `set_config` to change the API key endpoint to an attacker-controlled server (SEC-002).
- Invoke `get_config` to read API keys (though SEC-003 redacts them from the IPC response).
- Invoke `toggle_dictation` to start/stop recording without the user's knowledge.
- Invoke `delete_history` to destroy transcription history.

The IPC socket was designed as loopback-only (`127.0.0.1`), but loopback does not imply trust — any code running as the same user can connect to it.

**Alternatives considered:**

1. **Unix domain socket with peer-credential authentication (SO_PEERCRED).** Peer-cred auth is POSIX-only; Windows has no equivalent. Voice Typer is primarily a Windows app. Rejected.

2. **mTLS (mutual TLS) between Electron and Python.** This would require certificate management (generation, rotation, secure storage of private keys) on both sides. The added complexity is disproportionate to the threat (local-only, same-user attacker). Rejected.

3. **Ephemeral per-launch random token.** Generate a cryptographically random token on the Electron side, pass it to Python via environment variable, and require it as the first message on every TCP connection. This is simple, cryptographically sound (256-bit token), and works on all platforms. **Chosen.**

## Decision

Implement **per-launch session token authentication** for the TCP IPC channel:

1. **Token generation (Electron side):** On each launch, the Electron main process generates a 256-bit random token via `crypto.randomBytes(32).toString("hex")`. This is stored in `IPC_TOKEN` at module level in `client/src/main/index.ts`.

2. **Token delivery (Electron → Python):** The token is passed to the Python subprocess via the `VOICE_TYPER_IPC_TOKEN` environment variable. Both Electron's `spawn()` call and the standalone launcher (`electron_launcher.py`) set this variable.

3. **Auth handshake (TCP connect):** The first JSON line the Electron client sends after connecting must be `{"type": "auth", "token": "<token>"}`. The Python server reads exactly one line before processing any other commands.

4. **Auth validation (Python side):** The server validates the token using `hmac.compare_digest()` for constant-time comparison (preventing timing side-channel attacks). If the token is missing, wrong, or malformed, the server sends an `{"type": "error", "data": {"message": "authentication failed"}}` response and closes the connection immediately.

5. **Auth timeout (PR-3-FIX-1):** A 5-second `settimeout` is applied to the socket before the auth read to prevent a "connect-and-stall" DoS attack (where a malicious client opens a TCP connection but never sends the auth line, holding the dispatcher thread indefinitely).

6. **Lock-free auth:** The auth handshake is performed **outside** `self._lock` (the IPC server's main lock). This prevents a stalled auth read from blocking `push()` events and other IPC dispatch threads (PR-3-FIX-1).

7. **Fallback mode:** When `VOICE_TYPER_IPC_TOKEN` is not set (e.g., running `python -m voice_typer.server.ipc_server` from a terminal for debugging), the server emits a warning and accepts unauthenticated connections. This preserves the developer workflow without breaking security for production use.

## Consequences

### Easier
- **Defense-in-depth:** The token check is the primary gate; SEC-019 (client-side command allowlist) is the secondary gate. Both must be bypassed for a successful attack.
- **Minimal overhead:** Token generation + comparison adds ~200 µs to connection setup, negligible against the ~5-10 second Python import time.
- **No external dependencies:** Uses only `secrets` / `crypto.randomBytes` and `hmac.compare_digest`, both in the standard library.

### More difficult
- **Standalone mode auth:** When Electron spawns Python (the norm), the token is passed via environment variable. But when Python spawns Electron (standalone mode, `python -m voice_typer.server.ipc_server`), the token must be passed the other way — from Python to Electron. This is handled by `electron_launcher.generate_session_token()` setting `VOICE_TYPER_IPC_TOKEN` before spawning Electron.
- **Token lifetime:** The token lives for the entire process lifetime. A long-running backend (hours) uses the same token. Rotating the token would require reconnecting all clients, which is not currently supported.

### Risks
- **Environment variable leakage:** The token is passed via `VOICE_TYPER_IPC_TOKEN` which is visible in `/proc/<pid>/environ` on Linux and `GetEnvironmentStrings` on Windows. A local attacker with process-inspection capabilities can read it. Mitigation: `hmac.compare_digest` prevents token reuse beyond the single connection, but the attacker could authenticate directly. Acceptable risk — the IPC socket is loopback-only, and any local process with debug privileges can already interact with the app in other ways.
- **Race condition in standalone mode:** The token env var must be set BEFORE `start_tcp()` spawns the accept thread; otherwise, the thread reads an empty token and falls into unauthenticated mode. This is handled by ordering the calls (set env → start_tcp) in `main()`.

## References

- `voice_typer/server/ipc_server.py:_accept_tcp` (lines 796-898) — TCP accept + auth handshake.
- `voice_typer/server/ipc_server.py:_handle_tcp_connection` (lines 870-898) — auth validation.
- `voice_typer/client/src/main/index.ts` — token generation (`IPC_TOKEN`) and auth line send.
- `voice_typer/server/electron_launcher.py` — standalone mode token generation.
- `tests/test_e2e_pipeline.py` — `TestE2EAuthEnforcement` tests (wrong token, no auth line, stalled timeout).
- SECURITY.md — SEC-018 documentation.

*End of document.*
