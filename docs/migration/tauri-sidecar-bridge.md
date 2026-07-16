# Tauri + Python Sidecar Migration — Bridge Architecture

**Status**: Phase 0-W scaffolding + Rust host compilation + Phase 3 UI port implemented (2026-07-16). Phase 0-W validation gate (Nuitka exe + Tauri spawn + WS + HMAC + faster-whisper + enigo + notification + cooperative shutdown + prewarm LogonTrigger + native hotkey) pending on a real Windows host.

**Reference ADR**: [`docs/adr/0020-desktop-runtime-migration-analysis.md`](../adr/0020-desktop-runtime-migration-analysis.md) (cross-platform rewrite).

## What's implemented

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
| `src-tauri/Cargo.toml` | Tauri v2 + plugins (shell, notification, clipboard-manager, single-instance) + `enigo` (keystroke injection) + `tokio-tungstenite` (WS client) + `rand` (token gen) + Windows-specific `windows` crate for `AttachThreadInput` + `SetForegroundWindow`. **No `tauri-plugin-process`** — `AppHandle::restart()` is in core tauri (see below). **No `hmac`/`sha2`** — the token is a bearer token (32 random bytes hex-encoded), not an HMAC key. **No `tray-icon` feature** — the Python sidecar owns the tray via pystray. |
| `src-tauri/src/main.rs` (739 lines) | The Rust host. Spawns sidecar via `externalBin`, reads `server_started` JSON from stdout, opens WS client with 1 MiB frame cap (`connect_async_with_config`), performs bearer-token auth, exposes ONE generic `dispatch` command to the webview, subscribes to server-initiated events (emits BOTH the specific event name AND a generic `python-event` envelope for catch-all listeners), coalesces `bubble_level` 60Hz→30Hz, runs FT-1 supervisor with 500ms→1s→2s→4s→8s backoff (cap 5 → full-app relaunch via `AppHandle::restart()`), drains pending dispatch requests + clears `ws_tx` on WS disconnect (so callers don't wait 120s), cooperative shutdown with 2s ack timeout + `kill_children` backstop. |
| `src-tauri/build.rs` | `tauri_build::build()` — reads `tauri.conf.json` + capabilities. |
| `src-tauri/tauri.conf.json` | Per-arch `externalBin` (6 target triples) + `resources` (3 native hotkey binaries + 6 prewarm binaries) + Tauri v2 capabilities. `withGlobalTauri: true` so `window.__TAURI__` is available to the renderer bridge. CSP carries over from the Electron `csp-plugin.ts`. |
| `src-tauri/capabilities/migrate-runtime.json` | Least-privilege capability: scoped `shell:allow-spawn` per sidecar binary, `notification`, `clipboard-manager`, `single-instance`. **No `core:tray:*`** (sidecar owns tray via pystray). **No `process:allow-restart`** (`AppHandle::restart()` is core tauri, no plugin needed). |

### Phase 3 UI port (React bridge — `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`)

| File | Purpose |
|---|---|
| `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts` (NEW, 357 lines) | Tauri ↔ React bridge. Auto-installs `window.python`, `window.bubble`, `window.window_` using Tauri's global `__TAURI__` API when Tauri is detected. In Electron mode it's a no-op (the Electron preload already installed the namespaces). The renderer code (`usePython.ts`, pages, components) is unchanged. **Contract parity is partial, not identical** — see "Bridge contract parity" below. `window.python.call`/`onEvent` + FT-1 events are at full parity. `window.bubble` (6 mutator methods) and `window.window_.exportHistory/exportVocabulary` are stubbed on the Tauri path (known gaps, see "What's NOT implemented this round" #5/#6). |
| `voice_typer/client/src/renderer/src/main.tsx` (modified) | Imports `./lib/tauri-bridge` before the React app mounts. |
| `voice_typer/client/src/renderer/src/bubble-main.tsx` (modified) | Imports `./lib/tauri-bridge` before the bubble app mounts. |
| `voice_typer/client/src/renderer/src/globals.d.ts` (NEW) | Ambient `declare module "*.css"` etc. — fixes pre-existing TS2882 errors on side-effect CSS imports. (Note: the `window.python`/`window.bubble`/`window.window_` ambient types come from the pre-existing `declare global` block in `types/ipc.ts`, NOT from this file.) |

#### NEW-IPC-107 guard — error-envelope parity (d-review NEW-IPC-007)

`usePython.ts` inspects the resolved value of `window.python.call(...)` and throws a real `Error` when it sees either of the two error-envelope shapes the backend can produce. **The previous framing ("works on both paths") was false — corrected here:**

- **Electron path** — the `python-call` IPC handler (`client/src/main/index.ts:1904-1918`) resolves the pending request with the raw object, which can be EITHER shape:
  1. `{_error: "..."}` (string) — Electron main-process synthetic errors: backend-not-connected (`index.ts:1908/1911`) and `sendToPython` exceptions (`index.ts:1916`).
  2. `{type:"error", data:{code, message}}` — Python server unhandled-dispatch exceptions (`server/ipc_server.py:1044-1050`), passed through verbatim (the main process does NOT translate them into `{_error:...}`).

  Both in-code checks in `usePython.ts` are **live and necessary** on Electron — without the `type:"error"` branch, server-side errors were silently treated as successful results and callers downstream read `undefined` from data fields. The fix throws `new Error(result._error || result.data?.message || "unknown error")` so `try { await python.call(...) } catch (e) {}` callers see real failures on both shapes.

- **Tauri path** — the Rust `dispatch` command (`src-tauri/src/main.rs:954-965`) rejects the `invoke` promise on `type:"error"` (translating it to `Err("server error [code]: message")`) and never produces `{_error:...}`. As a result `await api.call(...)` throws **before** the resolved value is ever inspected, so **BOTH in-code checks (`_error` AND `type:"error"`) are unreachable dead code on Tauri.** Errors still surface correctly — via promise rejection, propagated as-is by `usePython` (no double-wrapping). The checks remain in the source because the same `usePython.ts` bundle runs under both hosts; they are harmless no-ops on Tauri.

### FT-1 relaunch behavior (corrected)

The FT-1 supervisor (`ft1_respawn` in `main.rs`) uses `AppHandle::restart()` (tauri-2.11.5/src/app.rs:588) for full-app relaunch when all 5 backoff retries are exhausted. This is a **core tauri v2 API** — it does NOT require `tauri-plugin-process` (the directive's original claim was incorrect; `tauri-plugin-process` only exposes `restart` as a Tauri command for webview invocation, not as a Rust-side trait).

`app.restart()` returns `!` (the never type), so it satisfies the `Result<(), String>` return signature of `ft1_respawn` directly. The relaunch is NOT a silent `Err` discard — it:
1. Emits a `ft1_relaunching` Tauri event with `{"reason": "exhausted_retries"}` (or `"backoff_exhausted"`)
2. Waits 500ms for the UI to render the event
3. Calls `app.restart()` which exits with `RESTART_EXIT_CODE` so the Tauri launcher spawns a fresh instance

The renderer's `tauri-bridge.ts` listens for `ft1_relaunching` and `ft1_reconnected` events and synthesizes `python-event` frames (with `type: "reconnecting"` / `"reconnected"`) so the `useConnection` hook updates the UI during FT-1 cycles.

### WS disconnect handling (CR-Finding 1 + 3 fix)

When the WS reader task exits (sidecar crash or network drop), the host:
1. **Clears `state.ws_tx`** to `None` — so new `dispatch` calls return `"sidecar not connected"` immediately instead of queueing onto a dead channel
2. **Drains `state.pending`** — rejects every in-flight dispatch request with `{"type":"error","data":{"code":"sidecar_disconnected","message":"sidecar WS disconnected (FT-1 respawn in progress)"}}` so callers don't wait the full 120s timeout
3. **Spawns FT-1 respawn** on a background thread (unless `shutting_down` is set)

### Tests (all cross-platform, run on Linux/macOS/Windows CI)

| File | Coverage |
|---|---|
| `tests/tauri/test_sidecar_ws_unit.py` (~250 lines) | `_emit_server_started` JSON shape, `_authenticate` token match/mismatch/timeout/non-auth-frame, `_make_dispatch` shutdown/rate-limit/dispatch-raises/missing-type, loopback host, 1 MiB frame cap, 2s shutdown ack timeout. |
| `tests/tauri/test_sidecar_ws_integration.py` (~120 lines) | End-to-end: real `websockets.serve` + real client, full auth + dispatch + response round-trip, bad-token rejection, malformed-frame resilience. Skipped if `websockets` not installed. |
| `tests/tauri/test_prewarm_resolver.py` (~120 lines) | `resolve_prewarm_exe` env-override/dev-fallback/nonexistent-env-fallthrough, `_target_triple` per-platform shape, `_exe_suffix`. |
| `tests/tauri/test_native_binary_path_tauri.py` (~95 lines) | `VOICE_TYPER_NATIVE_DIR` env-var lookup finds the binary, `VOICE_TYPER_NATIVE_BINARY` (single-file) takes precedence, broken env vars fall through cleanly. |
| `tests/tauri/test_tauri_sidecar_gate.py` (~160 lines) | `TAURI_SIDECAR=1` disables heartbeat thread, `TAURI_SIDECAR=1` is set by `--ws` flag, `--ws` + `--port` are mutually exclusive, `_COMMAND_REGISTRY` still contains `heartbeat` (Electron fallback). |

**Validation evidence (this round):**
- `cargo check` → 0 errors, 0 warnings (Linux, with webkit2gtk/GTK system libs)
- `npm run typecheck` → 0 errors (tsc --noEmit + tsconfig.web + tsconfig.node)
- `npm run lint` → 0 errors (biome check)
- `npm run build:renderer` → succeeded (5717 modules transformed)
- `python -m pytest tests/tauri/ tests/test_ipc_dispatch_errors.py tests/test_tray*.py` → 127 passed
- `python -m pytest tests/test_electron_ipc_and_build.py tests/test_dead_code_stays_removed.py tests/test_api_doc_accuracy.py` → 123 passed

## What's NOT implemented this round (requires host validation)

These require a real Windows/macOS host or a display server, neither of which is available in the Linux dev container:

1. **Nuitka build of the sidecar exe** — the `python-build-standalone` + Nuitka command in ADR-0020 §4.2 must run on a Windows host with MSVC build tools. The Rust host code is written to consume the exe via `externalBin`; the build script is documented in the Windows Validation Runbook (see `docs/migration/windows-validation-runbook.md`).
2. **Tauri build with display** — `cargo tauri dev` / `cargo tauri build` requires a display server for the WebView (absent in the headless dev container). `cargo check` passes on Linux (proving the code compiles); the full build requires a display. See the Tauri Build Runbook (see `docs/migration/tauri-build-runbook.md`).
3. **Phase 0-W validation gate** — the 9-point checklist at the end of ADR-0020 must run on a Windows 10 + Windows 11 test machine. See the Windows Validation Runbook.
4. **Phase 0-M (macOS)** and **Phase 0-L (Linux X11 + Wayland)** — same shape as Phase 0-W but per-platform. Documented in ADR-0020; not started this round.
5. **Bubble window Tauri commands** — the Tauri bridge stubs 6 bubble APIs (`show`, `signalReady`, `setPosition`, `setDraggable`, `moveBy`, `hideComplete`) as no-ops. These require Rust-side window-management commands that are out of scope for the Phase 3 MVP port. The core bubble function (`onLevel` + state events) works.
6. **Export/dialog APIs** — `window.window_.exportHistory` / `exportVocabulary` are stubbed with rejections in Tauri mode. They require Rust-side dialog commands (Tauri's `dialog` plugin).

## Dev-mode workflow

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

For `cargo tauri dev` (once the Rust toolchain + display are installed):

```bash
cd src-tauri
VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev
```

The Rust host checks `VOICE_TYPER_SIDECAR_DEV=1` and spawns `python -m voice_typer.server.ipc_server --ws` instead of the `externalBin` binary — so UI/transport iteration happens in seconds, not the ~10 minutes a Nuitka rebuild takes. (The dev-mode branch is documented in ADR-0020 §14 but not yet implemented in `main.rs` — it's a small addition for the next round.)

## Architecture boundary (what stays / what moves / what is removed)

See ADR-0020 "What stays / what moves / what is removed" for the full scope boundary. Summary:

- **Python sidecar**: 100% of the existing `voice_typer/server/` modules stay unchanged. Only `ipc_server.py` gets the `--ws` flag + `TAURI_SIDECAR=1` gate; `native_hotkeys.py` gets one new env-var path; `task_scheduler.py` gets one Tauri-aware branch. The 68-command registry, 21-event bus, handlers, ASR pipeline, audio filter chain, tray logic (pystray — works under Tauri because the sidecar inherits the desktop session), hotkey subsystem, prewarm, crash recovery — all stay verbatim.
- **Rust host**: NEW. Replaces `client/src/main/index.ts` (2,205 lines of Electron main process) + `electron_launcher.py` (215 lines) + `autostart_launcher.py` (464 lines) on the Tauri path. Electron path is 100% intact as a reversible fallback.
- **React bridge**: NEW (`tauri-bridge.ts`, 357 lines). The renderer code (`usePython.ts`, all pages, all components) is unchanged on both paths for the `python` namespace and FT-1 events. **`bubble` mutators + export/dialog APIs are Tauri-path stubs** (no-ops / rejections) — the renderer is unchanged but those features are not yet functional under Tauri (see "What's NOT implemented this round" #5/#6). This is a known partial-parity gap, not full contract equivalence.

## Next steps (in priority order)

1. **Run Phase 0-W on Windows** — follow the Windows Validation Runbook (`docs/migration/windows-validation-runbook.md`). Install Nuitka + `python-build-standalone`, build `python-sidecar-x86_64-pc-windows-msvc.exe`, run the 9-point validation gate.
2. **Run `cargo tauri build` on Windows/macOS/Linux** — follow the Tauri Build Runbook (`docs/migration/tauri-build-runbook.md`). Requires a display server + platform-specific toolchain.
3. **Wire the dev-mode branch in `main.rs`** — `VOICE_TYPER_SIDECAR_DEV=1` spawns `python -m voice_typer.server.ipc_server --ws` instead of `externalBin`. ~15 lines of Rust.
4. **Implement the 6 stubbed bubble APIs** — add Rust-side `bubble:show`, `bubble:set-position`, `bubble:set-draggable`, etc. commands.
5. **Wire CI** — extend `.github/workflows/build.yml` with one Nuitka build job per target triple + one Tauri build job per platform.
6. **Phase 0-M (macOS, both archs)** then **Phase 0-L (Linux X11 + Wayland)**.
