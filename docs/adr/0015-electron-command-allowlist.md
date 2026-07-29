# ADR 0015: Electron-Side Command Allowlist (SEC-019)

## Status

Accepted — implemented in `voice_typer/client/src/main/allowed-commands.ts` (canonical declaration moved from `index.ts` per R6-F10 — see `allowed-commands.ts` file header for the circular-dependency rationale).

## Date

2026-07-14

## Context

Voice Typer's IPC architecture has three layers:

1. **Renderer (React UI)** — communicates with the Electron main process via `ipcMain.handle("python-call", ...)`.
2. **Electron main process** — forwards commands to the Python backend over TCP.
3. **Python backend** — dispatches commands via `_COMMAND_REGISTRY`.

SEC-018 (ADR-0014) authenticates the TCP connection itself. But authentication alone does not prevent a **compromised renderer** from sending arbitrary IPC commands. If an XSS vulnerability in the React UI allows an attacker to call `window.electronAPI.python({type: "set_config", data: ...})`, the Electron main process will faithfully relay that command to the authenticated Python backend.

**Threat model:**
- A compromised renderer (XSS, malicious dependency, dev tools injection) can invoke any IPC command that the preload script exposes.
- The preload script exposes `window.electronAPI.python(cmd)` which calls `ipcMain.handle("python-call")`, which calls `sendToPython()`.
- Without a server-side allowlist, commands like `quit_app`, `set_config`, `delete_history`, or `restart_app` could be triggered from the renderer.

**Alternatives considered:**

1. **Server-side command namespacing (Python enforces capabilities).** The Python backend would categorize commands into roles (e.g., "read-only", "admin") and reject disallowed commands per connection. This duplicates the allowlist logic across two codebases and introduces a second failure mode. Rejected as over-engineering for a single-client architecture.

2. **Capability-based auth (token scopes).** Generate capability tokens for specific command subsets. This adds significant complexity (token creation, validation, rotation) with little benefit — there is only one authenticated client. Rejected.

3. **Client-side allowlist in the Electron main process.** The main process maintains a `Set<string>` of allowed command names. Before forwarding any IPC to the Python backend, it checks the command against this set. This is simple, auditable, and provides defense-in-depth alongside SEC-018. **Chosen.**

## Decision

Implement a **client-side command allowlist** in the Electron main process:

1. **`ALLOWED_COMMANDS` Set:** A `Set<string>` in `voice_typer/client/src/main/allowed-commands.ts` enumerates every IPC command the renderer is allowed to send. Commands are matched by their `type` field in the IPC message.

2. **Gate location:** The check is in `sendToPython()` (`voice_typer/client/src/main/python/send-to-python.ts`), immediately before the message is serialized and written to the TCP socket. If the command is not in the allowlist, the promise is rejected with `new Error("Disallowed IPC command: ${cmd}")`.

3. **Synchronization rule (CONTRIBUTING.md §6.4):** Any new command added to `_COMMAND_REGISTRY` in the Python backend MUST also be added to `ALLOWED_COMMANDS` in `voice_typer/client/src/main/allowed-commands.ts`, AND to the renderer's type-safe `call()` wrapper (the `ipc/` package under `voice_typer/client/src/renderer/src/types/ipc/` (specifically `requests.ts` for the Request union and `push_events.ts` for the PushEvent union)). A bidirectional parity test (`tests/test_electron_ipc_and_build.py`) enforces this.

4. **Exhaustive list:** See `voice_typer/client/src/main/allowed-commands.ts` for the canonical list. Parity between the TS allowlist, the Rust `allowed_commands()` in `src-tauri/src/commands/sidecar_cmds.rs`, and the Python `_COMMAND_REGISTRY` in `voice_typer/server/ipc_server.py` is enforced by `tests/test_security_doc_command_count.py`.

5. **Not in the renderer allowlist (intentionally):** Commands that should never originate from the renderer:
   - `tray_click` — Rust-only; routed via `dispatch_inner` from `tray.rs::on_menu_event`. The renderer never dispatches it.
   - `shutdown` — Rust-only cooperative shutdown via `shutdown_sidecar`. The renderer never dispatches it.
   - `heartbeat` — TS-only; the Rust WS-reader dispatches this directly (RW-10 watchdog tick). The renderer never dispatches it.
   - `relaunch_ack` — TS-only; the `relaunch_app` Tauri command dispatches this directly (PERF-005 relaunch ack). The renderer never dispatches it.
   - Canonical enumerations: `_HOST_ONLY_COMMANDS` (`tray_click`, `shutdown` — host-only, present in Python `_COMMAND_REGISTRY` but absent from both the TS `ALLOWED_COMMANDS` and the Rust `allowed_commands()`) and `_TS_ONLY_EXCEPTIONS` (`heartbeat`, `relaunch_ack` — present in the TS `ALLOWED_COMMANDS` but absent from the Rust `allowed_commands()`) in `tests/test_security_doc_command_count.py`.

## Consequences

### Easier
- **Defense-in-depth:** Even if SEC-018 (TCP auth) is bypassed OR the renderer is compromised, the allowlist prevents arbitrary command execution.
- **Auditability:** The full list of allowed commands is visible in one place, making it easy to review during code review.
- **Reject-fast:** Invalid commands are rejected in the Electron process without consuming a TCP round-trip or Python dispatch CPU.

### More difficult
- **Synchronization burden:** Every new IPC command requires updating three files (Python registry, Electron allowlist, renderer types). The parity test and CONTRIBUTING.md rule mitigate this, but it is still a manual step.
- **False sense of security:** The allowlist does not prevent a compromised renderer from calling allowed commands with malicious parameters (e.g., `set_config` with a malicious API URL). That is gated by server-side validation (RELIABILITY-004, SEC-002).

### Risks
- **Version skew:** If the Python backend is updated independently of the Electron frontend (e.g., a rolling release), a command that exists in `_COMMAND_REGISTRY` but not in `ALLOWED_COMMANDS` fails silently (rejected with "Disallowed IPC command"). This is a safe failure mode — the feature simply appears broken in the UI, which is better than executing an unintended command.

## References

- `voice_typer/client/src/main/allowed-commands.ts` — `ALLOWED_COMMANDS` canonical declaration (moved from `index.ts` per R6-F10).
- `voice_typer/client/src/main/python/send-to-python.ts` — `sendToPython()` gate implementation.
- `voice_typer/server/ipc_server.py` — `_COMMAND_REGISTRY` definition.
- The renderer's `ipc/` package (under `voice_typer/client/src/renderer/src/types/ipc/`, specifically `requests.ts` for the Request union and `push_events.ts` for the PushEvent union) — type-safe `call()` wrapper.
- `tests/test_electron_ipc_and_build.py` — parity test that `ALLOWED_COMMANDS` matches `_COMMAND_REGISTRY`.
- `tests/test_security_doc_command_count.py` — three-way parity test (TS ↔ Rust ↔ Python) command counts and entries.
- `CONTRIBUTING.md` §6.4 — synchronization rule documentation.
- SECURITY.md — SEC-019 documentation.

*End of document.*
