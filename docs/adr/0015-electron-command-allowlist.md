# ADR 0015: Electron-Side Command Allowlist (SEC-019)

## Status

Accepted — implemented in `client/src/main/index.ts:532-627`.

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

1. **`ALLOWED_COMMANDS` Set:** A `Set<string>` in `client/src/main/index.ts` enumerates every IPC command the renderer is allowed to send. Commands are matched by their `type` field in the IPC message.

2. **Gate location:** The check is in `sendToPython()` (lines 624-627), immediately before the message is serialized and written to the TCP socket. If the command is not in the allowlist, the promise is rejected with `new Error("Disallowed IPC command: ${cmd}")`.

3. **Synchronization rule (CONTRIBUTING.md §6.4):** Any new command added to `_COMMAND_REGISTRY` in the Python backend MUST also be added to `ALLOWED_COMMANDS` in the Electron main process, AND to the renderer's type-safe `call()` wrapper in `types/ipc.ts`. A bidirectional parity test (`test_electron_ipc_and_build.py`) enforces this.

4. **Exhaustive list:** The allowlist includes every command from `_COMMAND_REGISTRY` that the renderer should be able to invoke:
   - Read-only: `get_status`, `get_config`, `get_defaults`, `get_history`, `search_history`, `get_today_stats`, `get_favorites`, `get_microphones`, `get_templates`, `get_vocabulary`, `get_prewarm_status`, `get_model_status`, `get_model_catalog`, `get_volume_backend_status`
   - Mutation: `set_config`, `toggle_dictation`, `undo_last`, `delete_history`, `restore_history`, `clear_history`, `toggle_favorite`, `save_templates`, `save_vocabulary`
   - Lifecycle: `restart_app`, `quit_app`
   - Model management: `download_model`, `cancel_model_download`, `pause_model_download`, `resume_model_download`, `delete_model`, `import_model`, `test_llm_connection`
   - Onboarding: `onboarding_is_first_run`, `onboarding_start`, `onboarding_get_step`, `onboarding_next_step`, `onboarding_prev_step`, `onboarding_set_microphone`, `onboarding_set_hotkey`, `onboarding_set_model`, `onboarding_skip`, `onboarding_apply`, `onboarding_get_microphones`, `onboarding_get_model_options`, `onboarding_get_hotkey_presets`
   - Microphone test: `microphone_test_start`, `microphone_test_stop`, `microphone_test_cancel`, `microphone_test_status`, `microphone_test_get_level`
   - Level monitor: `level_monitor_start`, `level_monitor_stop`, `level_monitor_status`
   - Prewarm: `run_prewarm`, `open_prewarm_log`
   - Misc: `set_esc_cancel_paused`, `set_tray_locale`, `export_diagnostics`, `check_accessibility`, `heartbeat`

5. **Not in the allowlist (intentionally):** Commands that should never originate from the renderer:
   - `show_electron_notification` — pushed from Python, not invoked by renderer.
   - Internal dispatch commands that are server-side only.

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

- `voice_typer/client/src/main/index.ts` lines 532-627 — `ALLOWED_COMMANDS` definition and gate.
- `voice_typer/server/ipc_server.py` — `_COMMAND_REGISTRY` definition.
- `voice_typer/client/src/types/ipc.ts` — renderer type-safe `call()` wrapper.
- `tests/test_electron_ipc_and_build.py` — parity test that `ALLOWED_COMMANDS` matches `_COMMAND_REGISTRY`.
- `CONTRIBUTING.md` §6.4 — synchronization rule documentation.
- SECURITY.md — SEC-019 documentation.

*End of document.*
