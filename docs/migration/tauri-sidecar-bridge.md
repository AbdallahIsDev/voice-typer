# Tauri + Python Sidecar Migration — Bridge Architecture

**Status**: Phase 0-W scaffolding + Rust host compilation + Phase 3 UI port implemented (2026-07-16). Phase 0-W validation gate (Nuitka exe + Tauri spawn + WS + HMAC + faster-whisper + enigo + notification + cooperative shutdown + prewarm LogonTrigger + native hotkey) pending on a real Windows host.

**Reference ADR**: [`docs/adr/0020-desktop-runtime-migration-analysis.md`](../adr/0020-desktop-runtime-migration-analysis.md) (cross-platform rewrite).

## What's implemented

### Python side

| File | Change | Purpose |
|---|---|---|
| `voice_typer/server/sidecar_ws.py` (NEW, see file) | New module | WebSocket server side of the Tauri↔Python bridge. Binds `127.0.0.1:0`, emits `{"event":"server_started","port":N}` to stdout, performs HMAC auth handshake, dispatches WS frames via `IPCServer._dispatch` (reuses the 77-command registry unchanged), reuses the ADR-0019 rate limiter, handles `{"type":"shutdown"}` cooperative shutdown, caps frames at 1 MiB. |
| `voice_typer/server/prewarm_resolver.py` (NEW, ~213 lines) | New module | `resolve_prewarm_exe()` shared by Windows Task Scheduler + macOS LaunchAgent + Linux systemd user timer. Resolves the frozen `prewarm-<triple>[.exe]` via env var, Tauri resource dir, PyInstaller paths, or dev fallback. |
| `voice_typer/server/ipc_server.py` (modified) | `--ws` CLI flag + `TAURI_SIDECAR=1` env gate | `--ws` sets `TAURI_SIDECAR=1` and delegates to `sidecar_ws.run()`. Under `TAURI_SIDECAR=1`: (a) `_heartbeat_loop` thread is NOT started (FT-1 supervisor replaces ADR-0018); (b) `VoiceTyperSingleInstance` Win32 mutex is NOT acquired (Tauri's `single-instance` plugin replaces it). Electron path unchanged. |
| `voice_typer/server/native_hotkeys.py` (modified) | `VOICE_TYPER_NATIVE_DIR` env-var path | New lookup path between `VOICE_TYPER_NATIVE_BINARY` (single-file override) and the dev source-tree path. Tauri host sets this to `resourceDir/native/` so the Nuitka-frozen sidecar finds the native binaries in production. |
| `voice_typer/server/task_scheduler.py` (modified) | Tauri-aware `_prewarm_command()` | Under `TAURI_SIDECAR=1` or `VOICE_TYPER_PREWARM_EXE` env, delegates to `resolve_prewarm_exe()`. When the resolver returns a frozen exe path, the Task Scheduler XML is built without `<Arguments>` (the exe takes no module args). Dev fallback unchanged. |
| `pyproject.toml` + `requirements.txt` (modified) | `websockets>=12.0,<14.0` added | New hard dep for the Tauri sidecar path. Lazy-imported in `sidecar_ws.py` so the Electron-only path doesn't pay the import cost. |

### Rust side (Tauri v2 host — `src-tauri/`)

| File | Purpose |
|---|---|
| `src-tauri/Cargo.toml` | Tauri v2 + plugins (shell, notification, clipboard-manager, single-instance) + `enigo` (keystroke injection) + `tokio-tungstenite` (WS client) + `rand` (token gen) + Windows-specific `windows` crate for `AttachThreadInput` + `SetForegroundWindow`. **No `tauri-plugin-process`** — `AppHandle::restart()` is in core tauri (see below). **No `hmac`/`sha2`** — the token is a bearer token (32 random bytes hex-encoded), not an HMAC key. **No `tray-icon` feature** — the Python sidecar owns the tray via pystray. |
| `src-tauri/src/main.rs` (see file) | The Rust host entry point — module-level `main()` that invokes the dispatcher in `commands/sidecar_cmds.rs`. The bulk of the spawn / WS / FT-1 / cooperative-shutdown logic was extracted to focused sub-modules (`src-tauri/src/sidecar/`, `src-tauri/src/commands/`, `src-tauri/src/platform/`, `src-tauri/src/state.rs`) so the entry-point file stays thin. `main.rs` calls `tauri::Builder` with the sidecar spawn plugin, registers the dispatch + bubble + export commands, and runs the FT-1 supervisor via `state::spawn_ft1_supervisor()`. See `src-tauri/src/commands/sidecar_cmds.rs` (locate by `#[tauri::command] async fn dispatch`) for the canonical `dispatch` command implementation that translates a Tauri `invoke('dispatch', {type, data, id})` envelope into a WS frame and awaits the response with the 120s timeout + drain-on-disconnect semantics. |
| `src-tauri/build.rs` | `tauri_build::build()` — reads `tauri.conf.json` + capabilities. |
| `src-tauri/tauri.conf.json` | Per-arch `externalBin` (6 target triples) + `resources` (3 native hotkey binaries + 6 prewarm binaries) + Tauri v2 capabilities. `withGlobalTauri: true` so `window.__TAURI__` is available to the renderer bridge. CSP carries over from the Electron `csp-plugin.ts`. |
| `src-tauri/capabilities/migrate-runtime.json` | Least-privilege capability: scoped `shell:allow-spawn` per sidecar binary, `notification`, `clipboard-manager`, `single-instance`. **No `core:tray:*`** (sidecar owns tray via pystray). **No `process:allow-restart`** (`AppHandle::restart()` is core tauri, no plugin needed). |

### Phase 3 UI port (React bridge — `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`)

The Phase 3 UI port is the architectural keystone of the migration: the **renderer code is identical on both paths** (Electron + Tauri). There is one React bundle, one `usePython.ts`, one set of pages and components. The runtime difference is absorbed entirely by the bridge, which auto-detects the host at startup and installs the right namespace:

- **Electron path** — `client/src/preload/index.ts` runs in the preload world and uses `contextBridge.exposeInMainWorld` to install `window.python`, `window.bubble`, `window.window_`. The bridge module's `installTauriBridge()` detects the absence of `window.__TAURI__` and **early-returns** — it does NOT touch the preload-installed namespaces (referential identity preserved, verified by `tauri-bridge-commands.test.ts`).
- **Tauri path** — `tauri.conf.json` sets `withGlobalTauri: true`, so the Tauri runtime injects `window.__TAURI__` (with `core.invoke`, `event.listen`, `window.getCurrentWindow`) before the renderer JS executes. The bridge module's auto-install side effect (last line of `tauri-bridge.ts`) calls `installTauriBridge()`, which sees `__TAURI__` and installs `window.python`/`window.bubble`/`window.window_` using Tauri's global API.

Both `main.tsx` (main window) and `bubble-main.tsx` (bubble window) import `./lib/tauri-bridge` BEFORE the React app mounts, so the namespaces are ready when `usePython` and other hooks initialize. The order matters: `usePython.ts` reads `window.python` at first render, and the bubble's first render calls `window.bubble?.signalReady?.()`.

| File | Purpose |
|---|---|
| `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts` | Tauri ↔ React bridge. Auto-installs `window.python`, `window.bubble`, `window.window_` using Tauri's global `__TAURI__` API when Tauri is detected. In Electron mode it's a no-op (the Electron preload already installed the namespaces). The renderer code (`usePython.ts`, pages, components) is unchanged. **Contract parity is identical across both paths for all 8 MIG-1.1 + MIG-1.2 commands** — see "MIG-1.1 + MIG-1.2 wiring" below. `window.python.call`/`onEvent` + FT-1 events at full parity (round 1). `window.bubble` (6 mutator methods) + `window.window_.exportHistory/exportVocabulary` wired in round 2 (this round). See file directly for current size. |
| `voice_typer/client/src/renderer/src/main.tsx` (modified) | Imports `./lib/tauri-bridge` before the React app mounts. |
| `voice_typer/client/src/renderer/src/bubble-main.tsx` (modified) | Imports `./lib/tauri-bridge` before the bubble app mounts. |
| `voice_typer/client/src/renderer/src/vite-env.d.ts` (NEW) | `/// <reference types="vite/client" />` — pulls in Vite's ambient `*.css` / `*.svg` / `*.png` module declarations so TypeScript doesn't emit TS2882 on side-effect CSS imports (`import "./index.css"` in `main.tsx` + `bubble-main.tsx`). Works identically under `electron-vite` (Electron path) and `vite` (Tauri path) because both use the same Vite pipeline. (Note: the `window.python`/`window.bubble`/`window.window_` ambient types come from the pre-existing `declare global` block in `types/ipc.ts`, NOT from this file.) |
| `voice_typer/client/src/renderer/src/lib/__tests__/tauri-bridge-commands.test.ts` (NEW, see file) | Vitest coverage for the 8 MIG-1.1 + MIG-1.2 commands. Mocks `window.__TAURI__.core.invoke` (the bridge deliberately avoids importing `@tauri-apps/api/core` as a dep — `withGlobalTauri: true` exposes the same API on `window.__TAURI__`). Asserts each bridge method invokes the correct Rust command name + argument envelope. Also asserts the Electron-mode no-op invariant (referential identity of `window.python`/`bubble`/`window_` preserved when `__TAURI__` is absent). |

#### MIG-1.1 + MIG-1.2 wiring (this round — TS bridge side)

The 8 Rust host commands added by Sub-agent A (`export_history`, `export_vocabulary`, `bubble_show`, `bubble_signal_ready`, `bubble_set_position`, `bubble_set_draggable`, `bubble_move_by`, `bubble_hide_complete`) are wired to their TS bridge counterparts. The return shapes match the Electron preload **exactly** so the renderer code (History.tsx, Vocabulary.tsx, useConnection.ts, Bubble.tsx, GeneralSettingsSection.tsx) is byte-identical on both paths:

| Bridge method | Tauri invoke | Electron preload equivalent | Return shape (both paths) |
|---|---|---|---|
| `window.window_.exportHistory(data, format)` | `invoke('export_history', { data, format })` | `ipcRenderer.invoke('history:export', { data, format })` | `{success: true, path: string}` \| `{success: false}` (cancel) \| `{success: false, error: string}` |
| `window.window_.exportVocabulary(data, format)` | `invoke('export_vocabulary', { data, format })` | `ipcRenderer.invoke('vocabulary:export', { data, format })` | same as above |
| `window.bubble.show()` | `invoke('bubble_show')` | `ipcRenderer.send('bubble:show-from-renderer')` | `void` |
| `window.bubble.signalReady()` | `invoke('bubble_signal_ready')` | `ipcRenderer.send('bubble:ready')` | `void` |
| `window.bubble.setPosition(x, y)` | `invoke('bubble_set_position', { x, y })` | `ipcRenderer.send('set_bubble_position', pos)` | `void` |
| `window.bubble.setDraggable(draggable)` | `invoke('bubble_set_draggable', { draggable })` | `ipcRenderer.send('bubble:draggable', draggable)` | `void` |
| `window.bubble.moveBy(dx, dy)` | `invoke('bubble_move_by', { dx, dy })` | `ipcRenderer.send('bubble:move-by', { deltaX, deltaY })` | `void` |
| `window.bubble.hideComplete()` | `invoke('bubble_hide_complete')` | `ipcRenderer.send('bubble:hidden')` | `void` |

**`setPosition` arg-shape note (XPLAT-6):** the bridge's `setPosition` signature is `(position: string) => void` — accepting a `"top" | "bottom"` string enum (matching the renderer's existing `MainRendererBubble` type and the `useConnection.ts` / `GeneralSettingsSection.tsx` call sites). On the Electron path this string is forwarded verbatim to `ipcRenderer.send('set_bubble_position', position)`. On the Tauri path the string is forwarded to `invoke('bubble_set_position', { position })`, and the Rust `bubble_set_position` command resolves `"top"`/`"bottom"` to concrete screen coordinates server-side (XPLAT-6). The renderer is unchanged on both paths.

**`moveBy` arg-name rename:** the renderer calls `moveBy(deltaX, deltaY)` (per `MainRendererBubble`), but the Rust command takes `{dx, dy}` (snake_case convention). The bridge renames in the invoke envelope: `{ dx: deltaX, dy: deltaY }`. The renderer is unchanged. (Line numbers in call sites drift; locate the rename in `tauri-bridge.ts` by the `moveBy` method.)

**`exportHistory`/`exportVocabulary` return-shape mapping:** the Rust commands return `{success: bool, path: string}` on success, `{canceled: true}` on user-dismissed save dialog, or throw on error. The bridge maps:
- `{success: true, path}` → `{success: true, path}` (pass-through)
- `{canceled: true}` → `{success: false}` (no path, no error — matches Electron's cancel shape)
- throw → `{success: false, error: <message>}` (matches Electron's catch shape)

This mapping is verified by `tauri-bridge-commands.test.ts` — the renderer's `result.success` / `result.path` / `result.error` reads work identically on both paths.

#### NEW-IPC-107 guard — error-envelope parity (d-review NEW-IPC-007)

`usePython.ts` inspects the resolved value of `window.python.call(...)` and throws a real `Error` when it sees either of the two error-envelope shapes the backend can produce. **The previous framing ("works on both paths") was false — corrected here:**

- **Electron path** — the `python-call` IPC handler resolves the pending request with the raw object, which can be EITHER shape:
  1. `{_error: "..."}` (string) — Electron main-process synthetic errors: backend-not-connected, and `sendToPython` exceptions.
  2. `{type:"error", data:{code, message}}` — Python server unhandled-dispatch exceptions (locate by the `{"type": "error", "data": {...}}` envelope in `server/ipc_server.py`'s `_handle_tcp_connection`), passed through verbatim (the main process does NOT translate them into `{_error:...}`).

  Both in-code checks in `usePython.ts` are **live and necessary** on Electron — without the `type:"error"` branch, server-side errors were silently treated as successful results and callers downstream read `undefined` from data fields. The fix throws `new Error(result._error || result.data?.message || "unknown error")` so `try { await python.call(...) } catch (e) {}` callers see real failures on both shapes.

- **Tauri path** — the Rust `dispatch` command (locate by `#[tauri::command] async fn dispatch` in `src-tauri/src/commands/sidecar_cmds.rs`) rejects the `invoke` promise on `type:"error"` (translating it to `Err("server error [code]: message")`) and never produces `{_error:...}`. As a result `await api.call(...)` throws **before** the resolved value is ever inspected, so **BOTH in-code checks (`_error` AND `type:"error"`) are unreachable dead code on Tauri.** Errors still surface correctly — via promise rejection, propagated as-is by `usePython` (no double-wrapping). The checks remain in the source because the same `usePython.ts` bundle runs under both hosts; they are harmless no-ops on Tauri.

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

### Event rename: `electron_notification` → `notification` (CR-8)

**Status**: Implemented (CR-8 fixed). The Python-side event name is now platform-agnostic.

**Before**: The Python sidecar published the event as `electron_notification` (a leftover from the Electron-only era). The Tauri Rust host renamed it to `notification` via a single `match` arm with no fallback — so the canonical UI-facing name was `notification`, but the wire name still carried the `electron_` prefix.

**After (CR-8 fix)**:
- **Python side** (`voice_typer/server/handlers/system_handlers.py` + `voice_typer/server/startup_sequence.py`) now publishes the event directly as `notification` — the `electron_` prefix is gone from the wire protocol.
- **Rust side** (`src-tauri/src/main.rs`) — the `electron_notification` → `notification` rename `match` arm was REMOVED. The event now passes through unchanged via the `other => other` arm. The `relaunch_electron` → `relaunch_app` rename is preserved (it remains a Tauri-specific translation, not a Python event name).
- **Renderer** — no subscription changes needed: the renderer consumes notifications via the generic `python-event` envelope (the `usePythonEvent` catch-all in `usePython.ts`), not by direct event-name subscription. (No `usePythonEvent("electron_notification", ...)` or `usePythonEvent("notification", ...)` call sites exist in the renderer.)

**Backward-compat shim (Rust side, rolling upgrade safety)**:

To support a rolling upgrade where an **old Python sidecar** (still emitting `electron_notification`) is paired with a **new Tauri host** (expecting `notification`), the Rust host keeps a small alias in the WS reader task (`main.rs`, immediately after the generic `emit` calls):

```rust
// CR-8 backward-compat alias: if an older Python sidecar still emits
// the legacy `electron_notification` event name (rolling upgrade),
// also emit it under the new canonical `notification` name so new UI
// code subscribing to `notification` keeps working.
if event_type == "electron_notification" {
    let _ = app_for_reader.emit("notification", payload.clone());
}
```

When the WS reader sees the legacy name, it emits **BOTH** `electron_notification` (via the `other => other` pass-through — keeps any old direct listeners working) AND `notification` (via the alias above — keeps new UI code working). New Python sidecars emit only `notification`, which passes through unchanged (no double-emit).

**Lifecycle**: This alias is intended to live for **one release cycle** after the Python-side rename ships. Once all deployed sidecars are upgraded to emit `notification` directly, drop the alias (the `if event_type == "electron_notification"` block in `main.rs`) and remove the legacy `electron_notification` mentions from the ADR-0020 event table.

**Tests**: `tests/test_notification_event_name.py` (new) asserts the Python handler publishes under `notification` (not `electron_notification`) and that the legacy name is absent from the published event payload.

### Tests (all cross-platform, run on Linux/macOS/Windows CI)

| File | Coverage |
|---|---|
| `tests/tauri/test_sidecar_ws_unit.py` (~304 lines) | `_emit_server_started` JSON shape, `_authenticate` token match/mismatch/timeout/non-auth-frame, `_make_dispatch` shutdown/rate-limit/dispatch-raises/missing-type, loopback host, 1 MiB frame cap, 2s shutdown ack timeout. |
| `tests/tauri/test_sidecar_ws_integration.py` (~150 lines) | End-to-end: real `websockets.serve` + real client, full auth + dispatch + response round-trip, bad-token rejection, malformed-frame resilience. Skipped if `websockets` not installed. |
| `tests/tauri/test_prewarm_resolver.py` (~238 lines) | `resolve_prewarm_exe` env-override/dev-fallback/nonexistent-env-fallthrough, `_target_triple` per-platform shape, `_exe_suffix`. |
| `tests/tauri/test_native_binary_path_tauri.py` (~87 lines) | `VOICE_TYPER_NATIVE_DIR` env-var lookup finds the binary, `VOICE_TYPER_NATIVE_BINARY` (single-file) takes precedence, broken env vars fall through cleanly. |
| `tests/tauri/test_tauri_sidecar_gate.py` (~152 lines) | `TAURI_SIDECAR=1` disables heartbeat thread, `TAURI_SIDECAR=1` is set by `--ws` flag, `--ws` + `--port` are mutually exclusive, `_COMMAND_REGISTRY` still contains `heartbeat` (Electron fallback). |

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
5. **Bubble window Tauri commands** — the 6 bubble APIs (`show`, `signalReady`, `setPosition`, `setDraggable`, `moveBy`, `hideComplete`) are **now implemented** in `src-tauri/src/commands/bubble.rs` (Tauri v2 `WebviewWindow` show/hide + `set_position` + `bubble:ready`/`bubble:draggable`/`bubble:hide_complete` emits). The TS bridge in `tauri-bridge.ts` is wired to them (see MIG-1.2 wiring table above). What still requires host validation is the end-to-end bubble UX (cursor-follow positioning, drag throttling) on a real display.
6. **Export/dialog APIs** — `window.window_.exportHistory` / `exportVocabulary` are **now implemented** in `src-tauri/src/commands/export.rs` via `tauri-plugin-dialog`'s save-file dialog (with JSON + CSV encoding). The bridge return-shape mapping (`{success, path}` / `{success: false}` cancel / `{success: false, error}` throw) is verified by `tauri-bridge-commands.test.ts`. What still requires host validation is the native save dialog rendering on each platform.

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

The Rust host checks `VOICE_TYPER_SIDECAR_DEV=1` and spawns `python -m voice_typer.server.ipc_server --ws` instead of the `externalBin` binary — so UI/transport iteration happens in seconds, not the ~10 minutes a Nuitka rebuild takes. (The dev-mode branch is implemented in `src-tauri/src/sidecar/spawn.rs` per ADR-0020 §14 — see `is_dev_mode()` + `spawn_sidecar_dev_mode()`.)

## Architecture boundary (what stays / what moves / what is removed)

See ADR-0020 "What stays / what moves / what is removed" for the full scope boundary. Summary:

- **Python sidecar**: 100% of the existing `voice_typer/server/` modules stay unchanged. Only `ipc_server.py` gets the `--ws` flag + `TAURI_SIDECAR=1` gate; `native_hotkeys.py` gets one new env-var path; `task_scheduler.py` gets one Tauri-aware branch. The 77-command registry, 21-event bus, handlers, ASR pipeline, audio filter chain, tray logic (pystray — works under Tauri because the sidecar inherits the desktop session), hotkey subsystem, prewarm, crash recovery — all stay verbatim.
- **Rust host**: NEW. Replaces the Electron main process (`client/src/main/` — 3,145 lines across ~25 files including `index.ts` at 310 lines, `ipc/` handler modules, `python/` integration, and `windows/` management) + `electron_launcher.py` (~228 lines) + `autostart_launcher.py` (~561 lines) on the Tauri path. Electron path is 100% intact as a reversible fallback.
- **React bridge**: NEW (`tauri-bridge.ts`, see file). The renderer code (`usePython.ts`, all pages, all components) is unchanged on both paths for the `python` namespace and FT-1 events. **`bubble` mutators + export/dialog APIs are implemented** in `src-tauri/src/commands/bubble.rs` (see file) + `export.rs` (see file) (see MIG-1.1 + MIG-1.2 wiring tables above) — the renderer is unchanged and those features are functional under Tauri subject to host-validation of the native window/dialog rendering.

## Next steps (in priority order)

1. **Run Phase 0-W on Windows** — follow the Windows Validation Runbook (`docs/migration/windows-validation-runbook.md`). Install Nuitka + `python-build-standalone`, build `python-sidecar-x86_64-pc-windows-msvc.exe`, run the 9-point validation gate.
2. **Run `cargo tauri build` on Windows/macOS/Linux** — follow the Tauri Build Runbook (`docs/migration/tauri-build-runbook.md`). Requires a display server + platform-specific toolchain.
3. **Validate the dev-mode + bubble + export implementations on a real host** — `VOICE_TYPER_SIDECAR_DEV=1` dev-mode spawn (`src-tauri/src/sidecar/spawn.rs`), the 6 bubble commands (`src-tauri/src/commands/bubble.rs`), and the 2 export commands (`src-tauri/src/commands/export.rs`) are implemented in Rust but require a display server to verify end-to-end (window show/hide positioning, native save-dialog rendering).
4. **Wire CI** — extend `.github/workflows/build.yml` with one Nuitka build job per target triple + one Tauri build job per platform.
5. **Phase 0-M (macOS, both archs)** then **Phase 0-L (Linux X11 + Wayland)**.
