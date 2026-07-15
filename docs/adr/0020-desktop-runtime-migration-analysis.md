# ADR 0020: Desktop Runtime Migration to Tauri v2 + Python Sidecar

## Status

Accepted — migration in progress. Electron is retained intact as a reversible fallback until Tauri + Sidecar is proven and cut over.

## Date

2026-07-13 (decision) — 2026-07-14 (updated to Sidecar-only, actionable migration plan)

## Context

Voice Typer today is **Electron (React UI) + a separate Python backend + a separate prewarm helper** — effectively **three processes**:

1. Electron main process (hosts the React UI).
2. `python -m voice_typer.server.ipc_server --port 9876` — the Python backend, spawned by `electron_launcher.py:13`, reached over a local TCP socket. This process does audio capture + model inference.
3. `prewarm.py` — a standalone helper that warms the OS file cache at startup (kept intentionally separate; see ADR-0009).

Two pain points drive this migration:

- **(A) IPC middleware dislike.** The UI ↔ Python path is Electron `ipcMain`/`ipcRenderer` → TCP → Python handler mixins. We want to remove the hand-rolled launcher/relay.
- **(B) "Two things in Task Manager."** Electron + Python ship as two separate programs. We want **one application** the user launches (one icon/install), not two unrelated programs.

This ADR adopts **Tauri v2 + a Python Sidecar** as the replacement desktop runtime, and records the ordered migration plan. The Python backend and React UI are kept substantially as-is; only the shell and the transport change.

> **Plain English:** Today the app is three running programs (the window, the speech brain in Python, and a pre-load helper). We are moving the *window* from Electron to a smaller Tauri program, and bundling the speech brain *next to* it as a "sidecar" that Tauri starts and manages. The user still sees one app. We keep Electron working the whole time, so if the new version misbehaves we just ship Electron again — nothing is lost.

---

## Decision

**Adopt Tauri v2 + Python Sidecar as the desktop runtime, replacing Electron.** Keep the Python backend and React UI substantially as-is; only the shell + transport change.

**Rationale (why Sidecar, not embedding Python in the app):** embedding Python directly inside the Rust/Tauri process would put the speech engine in the *same* process as the UI. For a continuous realtime-audio app that reintroduces a Global Interpreter Lock (GIL) freeze risk on the audio path, adds fragile Windows native DLL/ABI linking, and prevents crash isolation (a speech-engine crash would kill the whole app). The sidecar pattern keeps the speech engine in its own managed process, so the UI never freezes, crashes are isolated, and Windows native loading stays standard.

**Migration is incremental and reversible.** Electron is NOT removed. We build Tauri + Sidecar *alongside* Electron, port the UI/components to Tauri's WebView2, implement the sidecar, then re-point the "wire" (UI → logic) from Electron→Python to Tauri→sidecar. At every phase the Electron app remains buildable, runnable, and shippable. Cutover is a packaging/default switch, not a destructive change.

Three mandatory architecture rules:

1. **Keep prewarm as a SEPARATE boot helper.** Do **not** merge it into the app. The main app becomes one Tauri app (Rust host + sidecar); prewarm remains a distinct, intentional boot-time process that warms the OS file cache. Net: **3 processes → 2 processes** (one app + one invisible boot helper). Preserves ADR-0009.
2. **Preserve the current streaming model.** Background chunking/streaming stays hidden from the user until dictation ends, then pastes at once. Unaffected by the runtime change.
3. **Migration must stay reversible.** Electron code is untouched; the Tauri build is additive. Ability to ship/switch back to Electron at any time, with zero loss.

> **Plain English (the three rules):** Rule 1 — the pre-load helper stays its own little program so the model stays ready in RAM; we only swap the *window* technology. Rule 2 — the way words are collected in the background and shown all at once does not change. Rule 3 — we never delete or break Electron; the new app is added next to it, and we can go back whenever we want.

### Locked implementation decisions (resolved in planning)

These choices were decided before the Phase 0 spike and are fixed for the build:

- **Sidecar freeze tool: Nuitka.** The Python backend is compiled to a native `python-sidecar-x86_64-pc-windows-msvc.exe` via **Nuitka** (not PyInstaller `--onedir`). Rationale: smaller/faster-start binary, no PyInstaller bootloader PID/antivirus quirks, better fit for a Tauri `externalBin` sidecar. Build uses a clean **`python-build-standalone`** interpreter as the Nuitka target. `externalBin` requires a single `.exe` file (not a folder), so `--onedir` output must be flattened — Nuitka avoids that step.
- **Transport: WebSocket, single choice.** UI → Rust (`invoke`) → sidecar over a **localhost WebSocket**. No HTTP/JSON-RPC alternative. The sidecar is reached at an **ephemeral `127.0.0.1:0`** port (not the hardcoded `9876`); Tauri passes the chosen port via env `VOICE_TYPER_IPC_PORT`. Auth is the existing **HMAC session token** via env `VOICE_TYPER_IPC_TOKEN`.
- **Paste/keystroke injection: `enigo`.** The Rust bridge uses the **`enigo`** crate to inject transcribed text into the foreground window (replaces Electron's `webContents.paste`/keyboard path). Higher-level and unicode-safe vs the lower-level `rdev` alternative.
- **Cooperative shutdown over the WebSocket**, not stdin/stdout. The Rust supervisor sends `{"type":"shutdown"}`; the sidecar releases the mic, acks, and exits. `kill_children` is the backstop only.
- **Crash isolation (FT-1)** is a hard requirement before cutover: Rust respawns the sidecar only, shows "reconnecting…", with backoff 500 ms → 1 s → 2 s (cap 5 retries) then full-app relaunch.

> **Honest process-model note:** post-migration the OS still runs **multiple processes** — Tauri host + Python sidecar + prewarm (3 → 2 net: one app + one boot helper). The user sees **one app** (one icon/install/start menu entry), but Task Manager will show more than one entry. This migration resolves complaint (B) as "one app to launch", NOT "one OS process". Embedding Python (PyO3) was rejected precisely because it *would* yield one process but reintroduces GIL-freeze risk and kills crash isolation.

---

## Target Architecture (post-migration)

```
One Tauri app (ONE icon / install — one app to launch; multiple OS processes under the hood):
  ├─ WebView2 (React UI)          ← ported from Electron, same React components
  ├─ Rust shell (Tauri v2)
  └─ python-sidecar.exe           ← bundled externalBin, spawned & managed by Tauri
        UI → Tauri invoke / HTTP → Rust → localhost WebSocket → Python sidecar
        (sidecar runs faster-whisper / CTranslate2 — same Python backend as today)
Plus: prewarm.py (separate boot helper, kept per ADR-0009)
```

The sidecar is a **normal Python program** (your existing `ipc_server` / `handlers/*` logic), compiled/bundled and launched by Tauri — no Python embedded in the Rust binary.

---

## Migration Plan (ordered)

### Phase 0 — Spike (prove before building)
- Freeze a working Python backend with **Nuitka** against a `python-build-standalone` interpreter, producing a single `python-sidecar-x86_64-pc-windows-msvc.exe`, and bundle it as a Tauri `externalBin`.
- Confirm on the user's Windows machine: sidecar spawns on app launch, **localhost WebSocket** comms work over an **ephemeral `127.0.0.1:0` port** + **HMAC token**, sidecar auto-stops with the app, `kill_children` cleans the tree.
- Confirm `faster-whisper` / `CTranslate2` loads and transcribes inside the sidecar.
- Confirm `enigo` injects transcribed text into a foreground window (no Electron).
- **Gate:** do not start Phase 1 until this passes.

### Phase 1 — Sidecar packaging
- Freeze the Python backend with **Nuitka** (target: `python-build-standalone`, `x86_64-pc-windows-msvc`) into a single `python-sidecar-x86_64-pc-windows-msvc.exe`.
- Code-sign the sidecar exe separately (unsigned sidecar triggers SmartScreen); run with hidden console.
- Implement **cooperative shutdown over the WebSocket** (`{"type":"shutdown"}` → sidecar releases mic, acks, exits); `kill_children` is the backstop, not the primary path.

### Phase 2 — Transport bridge
- Replace Electron's TCP IPC (`ipc_server --port 9876` + `electron_launcher` spawn) with a **localhost WebSocket** between Tauri (Rust) and the sidecar. Rust is the only bridge: UI `invoke('dispatch',{cmd,data})` → Rust → WebSocket `{"type":cmd,"data":...}` → sidecar `_COMMAND_REGISTRY` (`getattr(self,"_handle_<cmd>")`). The WebView never talks to Python directly.
- Port is **ephemeral `127.0.0.1:0`** (passed via `VOICE_TYPER_IPC_PORT`); auth is the existing **HMAC token** via `VOICE_TYPER_IPC_TOKEN`. Reuse `ipc_server._validate_dict_payload` + error codes (`invalid_payload`, `missing_field`, `invalid_field`).
- JSON shapes (carried from `ipc_server.py`): request `{"type":<command>,"data":{...}}`; response `{"type":"result"|"error","data":{...},"code"?:<error_code>}`; sidecar→UI events flow over the same socket → Rust `app.emit(name,payload)`.
- Map the existing handler registry (`handlers/*`) to sidecar commands; keep **one generic dispatch** to minimize Python changes.
- Map Tauri events ↔ the current `_push_event_now` / `event_bus.publish` event flow (see **Sidecar→UI Event Table** below) so UI updates behave unchanged. Rename Electron-specific events (`electron_notification` → native Windows toast, `relaunch_electron` → Tauri app relaunch) without changing payloads.

### Phase 3 — UI port to Tauri WebView2
- Move the React UI from the Electron renderer to the Tauri webview; replace `ipcMain`/`contextBridge` calls with Tauri `invoke`.
- Port tray, global hotkey, settings, and autostart UX to Tauri plugins (`tray`, `global-shortcut`, `autostart`, `single-instance`).
- Keep the same React components — only the shell bridge changes.

### Phase 4 — Wire swap + recovery
- Re-point the "wire" (UI → logic) from Electron→Python to Tauri→sidecar. Keep the Electron build path intact and runnable in parallel.
- Implement **crash isolation** (tracked as FT-1 in `.workspace/TASKS.md`): a Rust supervisor respawns the sidecar on unexpected exit, shows a "reconnecting…" state, and falls back to full-app relaunch if respawn fails repeatedly.
- Enable the `single-instance` plugin so only one app instance runs.

### Phase 5 — Validation & cutover
- Verify: one icon/install; UI never freezes (sidecar owns its own GIL); crash isolation works; prewarm still warms the cache; streaming unchanged; global hotkey + tray work.
- Keep the Electron code path intact until satisfied; then make Tauri the default shipping app. Revert at any time by shipping the Electron build.

---

## Sidecar→UI Event Table (extracted from current code)

The sidecar pushes UI events through `event_bus.publish(event)` (the modern successor to `ipc_server._push_event_now`); the Rust bridge subscribes and re-emits each as a Tauri event. This is **channel (2)** — server-initiated events, distinct from the command/response envelope (channel 1). Every event below is delivered as `{"type":<name>,"data":{...}}`. Payloads are carried unchanged from today's code so the React UI needs no reshaping.

| Event `type` | Source (file) | `data` payload | Notes |
|---|---|---|---|
| `ready` | `ipc_server.py:1847` | — | emitted on server start |
| `bubble_show` | `app.py:588` | — | show waveform bubble |
| `bubble_hide` | `app.py:592` | — | hide waveform bubble |
| `bubble_level` | `app.py:625` | `{rms:float, peak:float}` | ~60 Hz, throttled |
| `bubble_set_state` | `app.py:689` | `{state:str}` | |
| `transcription_final` | `dictation_pipeline.py:803` | `{text:str (≤200 chr)}` | UI preview / refresh |
| `vocabulary_suggestion` | `dictation_pipeline.py:709` | `{suggestions:[{original,corrected,confidence,context,timestamp}]}` | |
| `hotkey_capture_cancel` | `hotkey_dispatcher.py:232` | — | |
| `config_changed` | `config_handlers.py:145`, `service.py:1370` | `{validated config updates}` | |
| `microphone_test_complete` | `level_monitor.py:741` | `{duration:float}` | |
| `microphones_changed` | `startup_tasks.py:197` | `{count:int}` | |
| `audio_clip` | `recording.py:2404` | `{peak:float, count:int}` | |
| `download_progress` | `service.py:1653` | `{model, progress(0-100), status, +optional downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds, paused, resumed}` | |
| `electron_notification` | `system_handlers.py:283`, `startup_sequence.py:109` | `{title, message, duration_ms, critical}` | → **native Windows toast** under Tauri |
| `navigate` | `tray.py:628` | `{path:str}` | tray → UI route |
| `show_window` | `tray_window.py:123` | — | |
| `quit_app` | `app.py:1236` | — | sidecar requests app quit |
| `relaunch_electron` | `app.py:1314` | — | → **Tauri app relaunch** under Tauri |

**Channel (1) — command/response envelope** (not in the table above): requests `{"type":<command>,"data":{...}}` → responses `{"type":"result"|"error","data":{...},"code"?:<error_code>}`. The `status_change` / `state_changed` messages in `ipc_server.py` are command responses on this channel, not `event_bus` publishes.

**Renames under Tauri (payloads unchanged):** `electron_notification` → native Windows toast via `enigo`/WinRT; `relaunch_electron` → Tauri full-app relaunch; `quit_app` → Tauri quit. All other event names and payloads are preserved 1:1.

---

### Wins (keep)
- **One app / one icon.** Tauri host + sidecar bundle into one app, installed/launched/stopped together. The user launches **one app** — directly addresses complaint (B) as "one thing to open", not "one OS process". Process count: today's 3 (Electron + Python + prewarm) → 2 (Tauri app + prewarm).
- **No hand-rolled launcher.** Tauri owns the Python lifecycle; the `electron_launcher.py` relay behind complaint (A) is removed. (Note: the Rust↔sidecar bridge is still a thin IPC layer — complaint (A) is addressed by removing Electron's `ipcMain`/`contextBridge` middleware, replaced by a single Tauri `invoke`→WebSocket path.)
- **No UI freeze.** The sidecar owns its own GIL, so continuous mic capture + inference never block the UI — matches today's smooth behavior.
- **Crash isolation possible (FT-1).** A speech-engine crash can be recovered without killing the whole app — an upgrade over today's whole-app restart.
- **Smaller shell.** Tauri exe ~2–10 MB using system WebView2, vs Electron's ~100 MB+ bundled Chromium.
- **Python stays Python.** No ML rewrite; the existing backend is bundled as a sidecar (Nuitka-compiled).

### Costs (documented, with mitigations)
- **Installer size:** Python + CTranslate2 + model adds ~400 MB–1 GB. Mitigation: this is model/data weight, comparable to what the app already ships; far less than Electron + Chromium overhead overall.
- **Startup latency:** 2–5 s cold sidecar start. Mitigation: prewarm file-cache warming + background load.
- **Multiple processes in Task Manager** (app + sidecar + prewarm): this is expected and honest — the migration does NOT yield a single OS process. Mitigated by Tauri-managed lifecycle + `single-instance`; users perceive one app. Do not promise "one process" to the user.
- **Lifecycle/PID bugs** (the child Python process must close cleanly or it lingers as a zombie / blocks reinstall): mitigated by four concrete measures, all to be applied:
   1. **Nuitka single-exe** (not PyInstaller `--onedir`/`--onefile`) — a clean native `python-sidecar-*.exe` built from `python-build-standalone` → no PyInstaller bootloader-child confusion, simpler process tree, fewer antivirus false positives.
   2. **`python-build-standalone`** — a clean prebuilt Python as the Nuitka target → standard Windows native loading, no embedded-PE contradiction.
   3. **`kill_children`** — Tauri recursively kills the whole child process tree on exit → no zombies left behind.
   4. **Cooperative shutdown over WebSocket** — Rust sends `{"type":"shutdown"}`; sidecar releases the mic and exits gracefully, rather than being force-killed. `kill_children` is backstop only.
- **Webview consistency:** WebView2 vs Chromium — minor CSS/API guardrails if macOS/Linux is later added.

### Reversibility
Electron code is untouched throughout the migration. The Tauri + Sidecar build is strictly additive. At any phase the Electron app remains the shippable fallback; cutover is a packaging/default switch. No data, config, or model loss on revert.

---

## Risks / Open Questions

1. **Spike must pass on the user's Windows machine** (Phase 0) before any full build — the make-or-break step.
2. **UI port effort (Phase 3)** is the largest unknown; mitigated by keeping React components shell-agnostic.
3. **Transport bridge (Phase 2)** must preserve all current handler behaviors and the event flow exactly — the **Sidecar→UI Event Table** above enumerates every event so nothing is missed.
4. **Recovery supervisor (FT-1)** must be implemented before cutover so a sidecar crash does not strand the user.

### Resolved in planning (no longer blocking)
- **Sidecar freeze tool:** Nuitka (not PyInstaller `--onedir`). Single `.exe` via `python-build-standalone`.
- **Paste/keystroke injection crate:** `enigo` (not `rdev`).
- **Transport:** WebSocket only (not WebSocket/HTTP ambiguity); ephemeral port + HMAC token.
- **Cooperative shutdown:** over WebSocket (not stdin/stdout).
- **Sidecar→UI event table:** extracted above from `event_bus.publish` / `_push_event_now` call sites — 18 events mapped, payloads carried 1:1.

## References

- ADR-0001 (Electron + Python Architecture, Accepted) — current architecture, retained as the reversible fallback.
- ADR-0009 (Prewarm & Autostart Architecture) — prewarm / BootTrigger design preserved by this ADR.
- Tauri v2 sidecar guide (v2.tauri.app/develop/sidecar) — first-class `externalBin` sidecar feature.
- Tauri discussion #1645 (github.com/tauri-apps/tauri/discussions/1645) — sidecar trade-offs.
- `python-build-standalone` (by Gregory Szorc) — clean pre-built Python for sidecar bundling.
- Tauri `kill_children` + `single-instance` plugin — lifecycle/cleanup correctness.
- FT-1 in `.workspace/TASKS.md` — crash isolation (restart backend only, keep UI alive).
- `electron_launcher.py:13` — current spawn of `python -m voice_typer.server.ipc_server --port 9876` (replaced by sidecar in Phase 2).
- `voice_typer/server/ipc_server.py`, `voice_typer/server/handlers/*` — TCP IPC + handler registry (bridged to sidecar WebSocket in Phase 2).
- `voice_typer/server/prewarm.py`, `voice_typer/server/task_scheduler.py` — prewarm + BootTrigger (kept).

*End of document.*
