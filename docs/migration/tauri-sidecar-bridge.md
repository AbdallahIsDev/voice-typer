# Tauri + Python Sidecar Migration — Bridge Architecture

**Status**: Phase 0-W scaffolding implemented (2026-07-16). Phase 0-W validation gate (Nuitka exe + Tauri spawn + WS + HMAC + faster-whisper + enigo + notification + cooperative shutdown + prewarm LogonTrigger + native hotkey) pending on a real Windows host.

**Reference ADR**: [`docs/adr/0020-desktop-runtime-migration-analysis.md`](../adr/0020-desktop-runtime-migration-analysis.md) (cross-platform rewrite, 1099 lines).

## What's implemented (this round)

### Python side

| File | Change | Purpose |
|---|---|---|
| `voice_typer/server/sidecar_ws.py` (NEW, ~370 lines) | New module | WebSocket server side of the Tauri↔Python bridge. Binds `127.0.0.1:0`, emits `{"event":"server_started","port":N}` to stdout, performs HMAC auth handshake, dispatches WS frames via `IPCServer._dispatch` (reuses the 68-command registry unchanged), reuses the ADR-0019 rate limiter, handles `{"type":"shutdown"}` cooperative shutdown, caps frames at 1 MiB. |
| `voice_typer/server/prewarm_resolver.py` (NEW, ~165 lines) | New module | `resolve_prewarm_exe()` shared by Windows Task Scheduler + macOS LaunchAgent + Linux systemd user timer. Resolves the frozen `prewarm-<triple>[.exe]` via env var, Tauri resource dir, PyInstaller paths, or dev fallback. |
| `voice_typer/server/ipc_server.py` (modified) | `--ws` CLI flag + `TAURI_SIDECAR=1` env gate | `--ws` sets `TAURI_SIDECAR=1` and delegates to `sidecar_ws.run()`. Under `TAURI_SIDECAR=1`: (a) `_heartbeat_loop` thread is NOT started (FT-1 supervisor replaces ADR-0018); (b) `VoiceTyperSingleInstance` Win32 mutex is NOT acquired (Tauri's `single-instance` plugin replaces it). Electron path unchanged. |
| `voice_typer/server/native_hotkeys.py` (modified) | `VOICE_TYPER_NATIVE_DIR` env-var path | New lookup path between `VOICE_TYPER_NATIVE_BINARY` (single-file override) and the dev source-tree path. Tauri host sets this to `resourceDir/native/` so the Nuitka-frozen sidecar finds the native binaries in production. |
| `voice_typer/server/task_scheduler.py` (modified) | Tauri-aware `_prewarm_command()` | Under `TAURI_SIDECAR=1` or `VOICE_TYPER_PREWARM_EXE` env, delegates to `resolve_prewarm_exe()`. When the resolver returns a frozen exe path, the Task Scheduler XML is built without `<Arguments>` (the exe takes no module args). Dev fallback unchanged. |
| `pyproject.toml` + `requirements.txt` (modified) | `websockets>=12.0,<14.0` added | New hard dep for the Tauri sidecar path. Lazy-imported in `sidecar_ws.py` so the Electron-only path doesn't pay the import cost. |

### Rust side (Tauri v2 host — `src-tauri/`)

| File | Purpose |
|---|---|
| `src-tauri/Cargo.toml` | Tauri v2 + plugins (shell, notification, clipboard-manager, single-instance, tray) + `enigo` (keystroke injection) + `tokio-tungstenite` (WS client) + `hmac`/`sha2`/`rand` (token gen) + Windows-specific `windows` crate for `AttachThreadInput` + `SetForegroundWindow`. |
| `src-tauri/src/main.rs` (~470 lines) | The Rust host. Spawns sidecar via `externalBin`, reads `server_started` JSON from stdout, opens WS client, performs HMAC auth, exposes ONE generic `dispatch` command to the webview, subscribes to server-initiated events, coalesces `bubble_level` 60Hz→30Hz, runs FT-1 supervisor with 500ms→1s→2s→4s→8s backoff (cap 5 → full-app relaunch), cooperative shutdown with 2s ack timeout + `kill_children` backstop. |
| `src-tauri/build.rs` | `tauri_build::build()` — reads `tauri.conf.json` + capabilities. |
| `src-tauri/tauri.conf.json` | Per-arch `externalBin` (6 target triples) + `resources` (3 native hotkey binaries + 6 prewarm binaries) + Tauri v2 capabilities. CSP carries over from the Electron `csp-plugin.ts`. |
| `src-tauri/capabilities/migrate-runtime.json` | Least-privilege capability: scoped `shell:allow-spawn` per sidecar binary, `notification`, `clipboard-manager`, `single-instance`, `tray`. |

### Tests (all cross-platform, run on Linux/macOS/Windows CI)

| File | Coverage |
|---|---|
| `tests/tauri/test_sidecar_ws_unit.py` (~250 lines) | `_emit_server_started` JSON shape, `_authenticate` token match/mismatch/timeout/non-auth-frame, `_make_dispatch` shutdown/rate-limit/dispatch-raises/missing-type, loopback host, 1 MiB frame cap, 2s shutdown ack timeout. |
| `tests/tauri/test_sidecar_ws_integration.py` (~120 lines) | End-to-end: real `websockets.serve` + real client, full auth + dispatch + response round-trip, bad-token rejection, malformed-frame resilience. Skipped if `websockets` not installed. |
| `tests/tauri/test_prewarm_resolver.py` (~120 lines) | `resolve_prewarm_exe` env-override/dev-fallback/nonexistent-env-fallthrough, `_target_triple` per-platform shape, `_exe_suffix`. |
| `tests/tauri/test_native_binary_path_tauri.py` (~95 lines) | `VOICE_TYPER_NATIVE_DIR` env-var lookup finds the binary, `VOICE_TYPER_NATIVE_BINARY` (single-file) takes precedence, broken env vars fall through cleanly. |
| `tests/tauri/test_tauri_sidecar_gate.py` (~160 lines) | `TAURI_SIDECAR=1` disables heartbeat thread, `TAURI_SIDECAR=1` is set by `--ws` flag, `--ws` + `--port` are mutually exclusive, `_COMMAND_REGISTRY` still contains `heartbeat` (Electron fallback). |

## What's NOT implemented this round (deferred to Phase 0-W validation)

These require a real Windows host or a Tauri Rust toolchain + display, neither of which is available in the dev container:

1. **Nuitka build of the sidecar exe** — the `python-build-standalone` + Nuitka command in ADR-0020 §4.2 must run on a Windows host with MSVC build tools. The Rust host code is written to consume the exe via `externalBin`; the build script is documented in ADR-0020 but not yet wired into CI.
2. **Tauri build compilation** — `cargo tauri dev` / `cargo tauri build` requires a Rust toolchain + a display server (for the WebView). The Rust code compiles on paper (Cargo.toml + main.rs + build.rs + tauri.conf.json + capabilities are all syntactically valid), but is not compiled in this round. CI wiring is the next step.
3. **Phase 0-W validation gate** — the 9-point checklist at the end of ADR-0020 (Nuitka exe builds, externalBin spawns, WS+HMAC connect, `faster-whisper` transcribes inside Nuitka, `enigo` paste, notification toast, cooperative shutdown, prewarm LogonTrigger, native `windows-key-listener` toggles dictation) must run on a Windows 10 + Windows 11 test machine. The scaffolding here is the implementation; the validation is the gate.
4. **Phase 0-M (macOS)** and **Phase 0-L (Linux X11 + Wayland)** — same shape as Phase 0-W but per-platform. Documented in ADR-0020; not started this round.
5. **UI port to Tauri WebView** — the React UI still runs under Electron. The Phase 3 port (replace `ipcMain`/`contextBridge` with Tauri `invoke`, port the tray, audit for webkit2gtk quirks) is the largest single chunk of work in the migration and is not started.

## Dev-mode workflow (for the next implementer)

Run the Python sidecar in WS mode without Tauri or Nuitka:

```bash
# Terminal 1 — start the sidecar (binds 127.0.0.1:0, prints server_started JSON)
VOICE_TYPER_IPC_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
python -m voice_typer.server.ipc_server --ws

# stdout shows: {"event": "server_started", "port": <N>}

# Terminal 2 — connect a WS client (e.g. websocat) and send the auth frame
websocat ws://127.0.0.1:<N>
> {"type": "auth", "token": "<TOKEN>"}
> {"type": "get_status", "data": {}, "id": 1}
< {"type": "result", "data": {"status": "idle"}, "id": 1}
```

For `cargo tauri dev` (once the Rust toolchain is installed):

```bash
cd src-tauri
VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev
```

The Rust host checks `VOICE_TYPER_SIDECAR_DEV=1` and spawns `python -m voice_typer.server.ipc_server --ws` instead of the `externalBin` binary — so UI/transport iteration happens in seconds, not the ~10 minutes a Nuitka rebuild takes. (The dev-mode branch is not yet implemented in `main.rs` — it's a small addition once the Rust compiles.)

## Architecture boundary (what stays / what moves / what is removed)

See ADR-0020 "What stays / what moves / what is removed" for the full scope boundary. Summary:

- **Python sidecar**: 100% of the existing `voice_typer/server/` modules stay unchanged. Only `ipc_server.py` gets the `--ws` flag + `TAURI_SIDECAR=1` gate; `native_hotkeys.py` gets one new env-var path; `task_scheduler.py` gets one Tauri-aware branch. The 68-command registry, 21-event bus, handlers, ASR pipeline, audio filter chain, tray logic, hotkey subsystem, prewarm, crash recovery — all stay verbatim.
- **Rust host**: NEW. Replaces `client/src/main/index.ts` (2,204 lines of Electron main process) + `electron_launcher.py` (215 lines) + `autostart_launcher.py` (464 lines).
- **Electron fallback**: 100% intact. The `--ws` flag and `TAURI_SIDECAR=1` gate are additive — `--port 9876` (Electron) still works exactly as before.

## Next steps (in priority order)

1. **Install Rust + Tauri CLI on a dev machine**, compile `src-tauri/`, fix any compile errors in `main.rs`. The code is written to compile but is not yet validated.
2. **Wire the dev-mode branch in `main.rs`** — `VOICE_TYPER_SIDECAR_DEV=1` spawns `python -m voice_typer.server.ipc_server --ws` instead of `externalBin`. ~15 lines of Rust.
3. **Run Phase 0-W on Windows** — install Nuitka + `python-build-standalone`, build `python-sidecar-x86_64-pc-windows-msvc.exe`, run the 9-point validation gate at the end of ADR-0020.
4. **Port the React UI to Tauri WebView** (Phase 3) — replace `ipcMain`/`contextBridge` with `invoke('dispatch', ...)`, port the tray, audit for webkit2gtk quirks.
5. **Wire CI** — extend `.github/workflows/build.yml` with one Nuitka build job per target triple + one Tauri build job per platform.
6. **Phase 0-M (macOS, both archs)** then **Phase 0-L (Linux X11 + Wayland)**.
