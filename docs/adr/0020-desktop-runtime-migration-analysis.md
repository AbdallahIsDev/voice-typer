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
- **Transport: WebSocket, single choice.** UI → Rust (`invoke`) → sidecar over a **localhost WebSocket**. No HTTP/JSON-RPC alternative. The sidecar is reached at an **ephemeral `127.0.0.1:0`** port it binds itself and reports to Rust via a `server_started` JSON line on stdout (not the hardcoded `9876`; see §1). Auth is the existing **HMAC session token** via env `VOICE_TYPER_IPC_TOKEN`.
- **Paste/keystroke injection: `enigo`.** The Rust bridge uses the **`enigo`** crate for keystroke/mouse injection of transcribed text into the foreground window (replaces Electron's `webContents.paste`/keyboard path). `enigo` is keyboard/mouse ONLY — it does NOT do toast notifications (see §6 of the Implementation Specification). Higher-level and unicode-safe vs the lower-level `rdev` alternative.
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
        UI → Tauri invoke → Rust → localhost WebSocket → Python sidecar
        (sidecar runs faster-whisper / CTranslate2 — same Python backend as today)
Plus: prewarm-x86_64-pc-windows-msvc.exe (separate boot helper, frozen per ADR-0009)
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
- Port is **ephemeral `127.0.0.1:0`**, chosen by the sidecar and reported to Rust over stdout (see §1); auth is the existing **HMAC token** via `VOICE_TYPER_IPC_TOKEN`. Reuse `ipc_server._validate_dict_payload` + error codes (`invalid_payload`, `missing_field`, `invalid_field`).
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

**Verified against the live `voice_typer/server/` tree (2026-07-15).** The table below lists **21 events** — 3 more than the earlier draft (`history_changed`, `recording_started`, `recording_stopped` were previously missed). Line numbers are a source snapshot; if a referenced file changes, re-locate via the **symbol**, which is the canonical anchor — `event_bus.publish("<name>")` for events, `def handle_<cmd>` in `handlers/*` for commands — the line number is convenience only. `ready` is emitted via `IPCServer.push` (not `event_bus.publish`) but flows through the same channel. Empty `data` is written as `{}` (never `null`) so the Rust subscriber can always do `event.data ?? {}`.

| Event `type` | Source (file) | `data` payload | Notes |
|---|---|---|---|
| `ready` | `ipc_server.py:1899` | `{}` | emitted on server start (Electron defers window creation) |
| `bubble_show` | `app.py:594` | `{}` | show waveform bubble |
| `bubble_hide` | `app.py:598` | `{}` | hide waveform bubble |
| `bubble_level` | `app.py:658` | `{rms:float, peak:float}` | ~60 Hz source → Rust coalesce ≤30 Hz (see §9) |
| `bubble_set_state` | `app.py:693` | `{state:str}` | |
| `transcription_final` | `dictation_pipeline.py:841` | `{text:str (≤200 chr)}` | UI preview / refresh |
| `vocabulary_suggestion` | `dictation_pipeline.py:743` | `{suggestions:[{original,corrected,confidence,context,timestamp}]}` | |
| `hotkey_capture_cancel` | `hotkey_dispatcher.py:243` | `{}` | |
| `config_changed` | `config_handlers.py:144`, `service.py:1369` | `{validated config updates}` | |
| `history_changed` | `history_handlers.py:188` | `{reason:str}` | **added** — missed in earlier draft |
| `microphone_test_complete` | `level_monitor.py:867` | `{duration:float}` | |
| `microphones_changed` | `startup_tasks.py:196` | `{count:int}` | |
| `audio_clip` | `recording.py:2149` | `{peak:float, count:int}` | |
| `recording_started` | `recording_controller.py:335` | `{}` | **added** — missed in earlier draft |
| `recording_stopped` | `recording_controller.py:362` | `{}` | **added** — missed in earlier draft |
| `download_progress` | `service.py:1654` | `{model, progress(0-100), status, +optional downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds, paused, resumed}` | |
| `electron_notification` | `system_handlers.py:282`, `startup_sequence.py:107` | `{title, message, duration_ms, critical}` | → **native Windows toast** under Tauri |
| `navigate` | `tray.py:628` | `{path:str}` | tray → UI route |
| `show_window` | `tray_window.py:123` | `{}` | |
| `quit_app` | `app.py:1270` | `{}` | sidecar requests app quit |
| `relaunch_electron` | `app.py:1348` | `{}` | → **Tauri app relaunch** under Tauri |

**Channel (1) — command/response envelope** (not in the table above): requests `{"type":<command>,"data":{...}}` → responses `{"type":"result"|"error","data":{...},"code"?:<error_code>}`. The `status_change` / `state_changed` messages in `ipc_server.py` are command responses on this channel, not `event_bus` publishes.

**Renames under Tauri (payloads unchanged):** `electron_notification` → native Windows toast via **`tauri-plugin-notification`** (WinRT); `relaunch_electron` → Tauri full-app relaunch; `quit_app` → Tauri quit. All other event names and payloads are preserved 1:1. `heartbeat` is **removed from both sides** — see §2.

---

## Implementation Specification (detailed)

Closes the gaps called out in review: port-bind direction, command table, token lifecycle, Nuitka command, prewarm packaging, toast/paste, Tauri config, paths, throttling, error handling, logging, single-instance, signing. All referenced against the real `voice_typer/server/` code (registry + handlers + event sites).

### 1. Ephemeral port — locked bind direction
- **Sidecar is the WebSocket SERVER; Rust is the WebSocket CLIENT.** No ambiguity.
- **Token:** Rust generates the HMAC token (`secrets.token_bytes(32)`, see §3) and passes it to the sidecar via env `VOICE_TYPER_IPC_TOKEN` at spawn. Rust does **not** choose the port.
- **Port is chosen by the OS at bind time — no TOCTOU race.** Rust spawns the sidecar (`externalBin`) as a child process and captures its `stdout` pipe. The sidecar binds `websockets.serve(...)` to **`127.0.0.1:0`**; the OS assigns a free ephemeral port. **Before** the accept loop starts, the sidecar writes exactly one structured line to `stdout`:
  `{"event":"server_started","port":<n>}`
  Every other sidecar log goes to **stderr** or the rotating file (see §11) — **never `stdout`** — so Rust's parser is unambiguous.
  - **Unbuffered stdout is mandatory (Phase 0 blocker).** When Tauri pipes the sidecar's `stdout`, CPython switches to **block buffering**, so the `server_started` JSON is held in the buffer and Rust hangs forever waiting. Force a flush at the very top of `ipc_server.py`: `sys.stdout.reconfigure(line_buffering=True)` (or have Rust spawn the sidecar with `PYTHONUNBUFFERED=1`). Without this the launch freezes with no error.
- **Rust connects:** Rust blocks reading `stdout` until it parses the `server_started` JSON, then opens the WS client to `ws://127.0.0.1:<port>` and performs the HMAC handshake (§3). There is **no** probe→close→rebind window, so there is no `EADDRINUSE` race and **no exit-code-10 respawn dance** is needed.
- **Loopback lock (hard rule):** the bind address must be `127.0.0.1` **only**. Binding `0.0.0.0`/`::` would (a) pop a Windows Defender Firewall dialog on first run and (b) expose the authed-but-localhost IPC to the LAN. Fail the launch if the configured bind is not loopback.
- On FT-1 respawn Rust generates a **new** token and respawns the sidecar (which binds a fresh `:0`); token rotation per §3.

### 2. Sidecar←UI Command Table (channel 1, extracted from `ipc_server._COMMAND_REGISTRY`)
68 commands registered (verified against `ipc_server.py:1347`); each maps to a `_handle_<cmd>` mixin in `handlers/*`. Dispatch is generic: `getattr(self, _COMMAND_REGISTRY[cmd])` → `(data, resp)`. Request `{"type":<cmd>,"data":{...}}`; response `{"type":"result"|"error","data":{...},"code"?}`. Exact `data` fields per command are defined inside each `_handle_*` and re-validated by `ipc_server._validate_dict_payload` — **that function is the source of truth for command-payload shape and must be ported 1:1, not redesigned or relaxed**; line numbers drift, so locate each handler by `def handle_<cmd>` in `handlers/*`.

| Command | Handler module (`handlers/`) | Purpose |
|---|---|---|
| `get_status` | status_handlers | app/engine status snapshot |
| `get_rms_level` | status_handlers | live RMS level |
| `get_volume_backend_status` | status_handlers | volume-duck backend state |
| `get_audio_status` | status_handlers | audio device state |
| `get_model_status` | status_handlers | model load state |
| `get_prewarm_status` | status_handlers | ADR-0009 cache status |
| `run_prewarm` | status_handlers | trigger prewarm run (detached) |
| `open_prewarm_log` | status_handlers | open prewarm log in editor |
| `toggle_dictation` | dictation_handlers | start/stop dictation |
| `undo_last` | dictation_handlers | undo last transcription |
| `force_cancel_transcription` | dictation_handlers | recover stuck transcription |
| `get_history` | history_handlers | paginated history |
| `get_today_stats` | history_handlers | today's stats |
| `delete_history` | history_handlers | delete record(s) |
| `restore_history` | history_handlers | restore deleted record |
| `clear_history` | history_handlers | erase all history |
| `toggle_favorite` | history_handlers | favorite toggle |
| `get_favorites` | history_handlers | list favorites |
| `search_history` | history_handlers | search records |
| `get_config` | config_handlers | current config |
| `get_defaults` | config_handlers | default config |
| `set_config` | config_handlers | update config (validated) |
| `get_vocabulary` | vocabulary_handlers | list vocabulary entries |
| `save_vocabulary` | vocabulary_handlers | save vocabulary entries |
| `get_vocabulary_suggestions` | vocabulary_automation_handlers | pending suggestions |
| `apply_vocabulary_suggestion` | vocabulary_automation_handlers | apply a suggestion |
| `dismiss_vocabulary_suggestion` | vocabulary_automation_handlers | dismiss a suggestion |
| `get_templates` | templates_handlers | list templates |
| `save_templates` | templates_handlers | save templates |
| `onboarding_is_first_run` | onboarding_handlers | first-run check |
| `onboarding_start` | onboarding_handlers | begin wizard |
| `onboarding_get_step` | onboarding_handlers | current step |
| `onboarding_next_step` | onboarding_handlers | advance |
| `onboarding_prev_step` | onboarding_handlers | back |
| `onboarding_set_microphone` | onboarding_handlers | set mic |
| `onboarding_set_hotkey` | onboarding_handlers | set hotkey |
| `onboarding_set_model` | onboarding_handlers | set model |
| `onboarding_skip` | onboarding_handlers | skip wizard |
| `onboarding_apply` | onboarding_handlers | apply selections |
| `onboarding_get_microphones` | onboarding_handlers | mic list |
| `onboarding_get_model_options` | onboarding_handlers | model options |
| `onboarding_get_hotkey_presets` | onboarding_handlers | hotkey presets |
| `get_microphones` | microphone_handlers | mic list |
| `refresh_microphones` | microphone_handlers | re-enumerate mics |
| `microphone_test_start` | microphone_test_handlers | start test (duration) |
| `microphone_test_stop` | microphone_test_handlers | stop test |
| `microphone_test_cancel` | microphone_test_handlers | cancel test |
| `microphone_test_status` | microphone_test_handlers | test state |
| `microphone_test_get_level` | microphone_test_handlers | test level |
| `level_monitor_start` | level_monitor_handlers | start level monitor |
| `level_monitor_stop` | level_monitor_handlers | stop level monitor |
| `level_monitor_status` | level_monitor_handlers | monitor state |
| `download_model` | model_handlers | download model |
| `cancel_model_download` | model_handlers | cancel download |
| `pause_model_download` | model_handlers | pause download |
| `resume_model_download` | model_handlers | resume download |
| `get_model_catalog` | model_handlers | full catalog metadata |
| `test_llm_connection` | model_handlers | test LLM endpoint |
| `import_model` | model_handlers | import local model |
| `delete_model` | model_handlers | delete model |
| `restart_app` | system_handlers | request relaunch (→ Tauri relaunch) |
| `quit_app` | system_handlers | request quit (→ Tauri quit) |
| `export_diagnostics` | system_handlers | redacted diag bundle |
| `check_accessibility` | system_handlers | macOS accessibility check |
| `set_tray_locale` | system_handlers | set tray locale |
| `set_esc_cancel_paused` | system_handlers | pause ESC-cancel hotkey |
| `show_electron_notification` | system_handlers | → **Tauri notification** (renamed) |
| `heartbeat` | ipc_server (RW-10) | liveness ping — **REMOVED** under Tauri (Rust owns liveness; see §10) |

> **`heartbeat` is removed from BOTH sides.** The registry currently contains **68 commands** (incl. `heartbeat` at `ipc_server.py:1441`, handler `_handle_heartbeat`). The current Electron UI *still sends* `heartbeat` every 5 s (`client/src/main/index.ts:1271,1275`). Under Tauri: (1) the Tauri UI port must **delete** the heartbeat interval (Rust is the supervisor — it detects death via WS-close / process exit, so no app→backend heartbeat is needed); (2) the Rust bridge must **not** forward `heartbeat` to Python (treat as no-op + debug log); (3) `_handle_heartbeat` stays in Python until the UI removal lands, so a stray `heartbeat` frame never hits `_handle_unknown_command` and returns `unknown_command`. Verification task: `grep -rn "heartbeat" voice_typer/client` → zero hits after the UI port. `show_electron_notification` renames to a Tauri notification emit. The 67 surviving commands keep their `data` schemas 1:1 — enumerate each `_handle_*` payload from `handlers/*` (do NOT redesign); `_validate_dict_payload` re-validates on the sidecar.

### 3. HMAC token lifecycle
- Generated by Rust at startup: `secrets.token_bytes(32)` → hex. **Not** reused from any file.
- Passed to the sidecar **only** via env `VOICE_TYPER_IPC_TOKEN` at spawn (not CLI, not a file).
- Visibility concern (env readable via WMI/Process Explorer): acceptable for a localhost single-user desktop app — the token only authorizes loopback WS connections; it is regenerated per launch and per respawn, the port is ephemeral + loopback-only (`127.0.0.1`), so a stolen token is useless after the process exits. If stronger isolation is later required, pass via a deleted temp file or a named-pipe handshake instead.
- WS handshake: client's first frame must be `{"type":"auth","token":"<token>"}`; sidecar compares with `hmac.compare_digest` (constant-time) against the env value; on mismatch it closes the socket. Subsequent frames skip re-auth (matches today's TCP handshake-once model).
- **Rotation:** on every FT-1 respawn Rust generates a new token + new port.
- **Never log the token.** Redact `VOICE_TYPER_IPC_TOKEN` from every sink (`tauri.log`, `sidecar.log`, `stdout`/`stderr`). Log at most `token_present=true` or a short hash. A token written to a log file defeats the per-launch rotation and is readable by any local user, so it must never appear verbatim.

### 4. Nuitka build (actionable)
- Base interpreter: `python-build-standalone` `cpython-3.12.x+x86_64-pc-windows-msvc` (matches `faster-whisper`/CTranslate2 wheels).
- Command (single exe):
  ```bat
  :: Pin the build interpreter to python-build-standalone cpython-3.12.x
  :: (matches faster-whisper / ctranslate2 wheel tags — do NOT use 3.13+ yet).
  set PYBS=C:\path\to\python-build-standalone\cpython-3.12.x+x86_64-pc-windows-msvc
  set SITE=%PYBS%\Lib\site-packages
  python -m nuitka --standalone --onefile ^
    --assume-yes-for-downloads ^
    --enable-plugin=numpy ^
    --include-package=faster_whisper --include-package=ctranslate2 ^
    --include-package=voice_typer --include-package=websockets ^
    --include-data-dir=%SITE%\ctranslate2\lib=%SITE%\ctranslate2\lib ^
    --include-dll=%SITE%\ctranslate2\lib\ctranslate2.dll ^
    --include-dll=%SITE%\ctranslate2\lib\cublas64_*.dll ^
    --include-dll=%SITE%\ctranslate2\lib\cudnn64_*.dll ^
    --include-dll=%SITE%\ctranslate2\lib\cuda_runtime64_*.dll ^
    --windows-disable-console ^
    --output-filename=python-sidecar-x86_64-pc-windows-msvc.exe ^
    voice_typer/server/ipc_server.py
  ```
  - **Nuitka does NOT expand globs** like `*.dll` in `--include-dll` — those `*.dll` lines above will NOT work as written; use explicit paths or `--include-data-dir` (which copies a whole folder verbatim). The reliable pattern is `--include-data-dir=%SITE%\ctranslate2\lib=%SITE%\ctranslate2\lib` plus an explicit `--include-dll` for `ctranslate2.dll` itself. `ctranslate2/lib` holds `ctranslate2.dll` + optional CUDA DLLs (`cublas64_*`, `cudnn64_*`, `cuda_runtime64_*`) — include the CUDA ones only if the frozen env has CUDA enabled; otherwise CTranslate2 runs CPU-only and those files are absent.
  - `--include-package=websockets` is **required** (added to `requirements.txt` — see §14); the sidecar is a WS *server* and the stdlib has no WS implementation. `--enable-plugin=numpy` pulls numpy's hidden imports; if Nuitka warns about missing `numpy.*` submodules, add `--include-package=numpy`.
  - **Discover the exact DLL set at build time**, do not guess: `dir "%SITE%\ctranslate2\lib\*.dll"` and list every file; re-run after any `faster-whisper`/`ctranslate2` version bump.
  - **CPU inference runtimes (easy to miss, instant crash if absent):** `ctranslate2` links Intel MKL / OpenMP for fast x86 CPU inference even with no GPU. If `libiomp5md.dll` (OpenMP) or the MKL redistributables are missing, the frozen exe **builds fine but crashes instantly on `import ctranslate2`** at launch. Verify with `python -c "import ctranslate2"` in the build env, then enumerate loaded companion DLLs (`listdlls`, Sysinternals Process Explorer, or `tasklist /m`) and copy every runtime next to `ctranslate2.dll` via `--include-data-dir` (or an explicit `--include-dll`). At minimum include `libiomp5md.dll`; add any `libiomp*.dll` / `mkl*.dll` / `libgomp*.dll` present. Nuitka does **not** auto-collect these.
  - **Do NOT** bundle model weights — models live in `%LOCALAPPDATA%/voice-typer/models` (see §8), loaded at runtime. Include only code + native DLLs.
  - **`--onefile` temp-dir bloat:** Nuitka `--onefile` extracts to `%TEMP%\onefile_*` on every launch; frequent launches/crashes accumulate gigabytes. Pin a deterministic extract dir with `--onefile-tempdir-spec=%LOCALAPPDATA%\voice-typer\onefile-tmp` and have the installer/uninstaller purge that dir (match by the Voice Typer binary signature) so stale extracts are cleaned.
- Path resolution inside the compiled exe: `os.path.dirname(sys.argv[0])` for the exe dir; `os.environ["LOCALAPPDATA"]` for config/models/logs. Tauri passes `VOICE_TYPER_IPC_TOKEN` (+ optionally `appConfigDir`/`appLogDir`) via env; the port is self-selected by the sidecar (`:0`) and reported via stdout (see §1). Dev mode (§14) instead passes `VOICE_TYPER_IPC_PORT` to the plain-Python server, which still reads it from env.
- **Verify step (Phase 0 gate):** run `python-sidecar.exe` with a one-shot command that loads `faster_whisper` (`WhisperModel("tiny")`), transcribes a 3-second WAV, prints the text, exits 0. This proves CTranslate2 + DLLs + model load all work inside Nuitka.

### 5. Prewarm packaging
- `prewarm.py` is frozen the **same Nuitka way** into `prewarm-x86_64-pc-windows-msvc.exe` (kept separate per Rule 1 — not via the sidecar's Python).
- **Resource, NOT `externalBin`.** Prewarm is launched by Windows Task Scheduler (`schtasks`), not spawned by Tauri as a managed child — so it must be a `bundle.resource` extracted to `resourceDir`, **not** an `externalBin` (externalBin is only for the Tauri-spawned sidecar). Consequence: `shell:allow-spawn` does **not** apply to prewarm. Tauri extracts the resource next to the app; first launch registers the scheduled task (see `resolve_prewarm_exe` below).
- **Path resolution after install:** today `task_scheduler._prewarm_command()` returns `pythonw.exe -m voice_typer.server.prewarm`. Post-migration it must point at the frozen exe. Add one resolver used by `task_scheduler.py` and the `run_prewarm` / `get_prewarm_status` handlers:
  ```python
  def resolve_prewarm_exe() -> str | None:
      # 1) Rust passes the extracted resource path (preferred).
      if (env := os.environ.get("VOICE_TYPER_PREWARM_EXE")) and Path(env).exists():
          return env
      # 2) Tauri resource dir (tauri::api::path::resource_dir); 3) app install dir.
      # 4) Dev fallback: plain python module (works without a frozen exe).
      return f"{sys.executable} -m voice_typer.server.prewarm"
  ```
  `task_scheduler._prewarm_command()` returns `resolve_prewarm_exe()` (wrapped as a command) when the sidecar build is active; `run_prewarm` / `get_prewarm_status` shell out via the same resolver. Rust sets `VOICE_TYPER_PREWARM_EXE` to `resourceDir/prewarm-x86_64-pc-windows-msvc.exe` at startup.
  - The sidecar does **not** spawn prewarm; prewarm remains an independent boot helper (ADR-0009). `get_prewarm_status` / `run_prewarm` still work via `resolve_prewarm_exe()`.
  - **Uninstall cleanup:** the MSI/installer must **deregister** the `prewarm` LogonTrigger task (`schtasks /delete /tn voice-typer-prewarm /f`) on uninstall/upgrade. Otherwise an orphaned scheduled task will try to launch a deleted exe after the app is removed, spamming Task Scheduler failures.

### 6. Toast + paste (corrected)
- **Toast:** `electron_notification` → **`tauri-plugin-notification`** (WinRT backend), NOT `enigo`. `enigo` is keystroke/mouse injection only. Add `notification:allow-notify` to capabilities.
- **Paste strategy (`enigo`):** for transcribed text:
  - Short text (< ~300 chars): inject via **`enigo.text()`** (IME-safe Unicode string injection). Do **NOT** simulate discrete `key_down`/`key_up` virtual-key events — that breaks non-English layouts, dead keys, and punctuation. The only discrete keys simulated are `Ctrl+V` in the long-text path below.
  - Long text / text-field target: copy via `tauri-plugin-clipboard-manager` then send `Ctrl+V` (`enigo` `Key::Control`+`Key::V`), then optionally restore the previous clipboard. Matches today's "paste on stop".
  - **Focus restore (Win32):** before injecting, capture the current foreground window with `GetForegroundWindow()` + its thread id (`GetWindowThreadProcessId`); after `SendInput`, re-attach via `AttachThreadInput(our_thread, target_thread, TRUE)` then `SetForegroundWindow(hwnd)` + `AttachThreadInput(..., FALSE)`. This is the standard `win32` focus-steal dance (see `clipboard.py`, which already does foreground-attachment for paste) so the user's window is not permanently stolen.
  - **Elevated / focus-attach failure (UIPI):** `SendInput` and the focus-restore dance are blocked by UIPI when the target runs as Administrator (or at a higher integrity level than Voice Typer). The restore path calls `AttachThreadInput(our_thread, target_thread, TRUE)`; **if it returns `0`, do NOT retry the window-switch** — fall back immediately: write the text to the system clipboard, push it to crash-recovery (`_crash_recovery.add`), and surface a **toast** "Could not paste — text copied to clipboard" (via `tauri-plugin-notification`). The same fallback applies if `SetForegroundWindow` silently fails. This matches today's no-data-loss guarantee and removes the ambiguity an implementer would otherwise hit.
  - **Global hotkeys + UIPI:** the dictation toggle is registered via `tauri-plugin-global-shortcut`. On Windows, UIPI blocks a standard-user process from receiving global keyboard hooks while an **elevated (Administrator)** window has focus — the hotkey silently will not fire. This is an OS limitation, not a bug: log it and (optionally) surface a one-time toast "Hotkeys unavailable while an admin window is focused" so an implementer does not waste hours "debugging" a working hook.

### 7. Tauri config + capabilities (example)
`tauri.conf.json` essentials:
```json
{
  "bundle": {
    "externalBin": ["bin/python-sidecar-x86_64-pc-windows-msvc.exe"],
    "resources": ["prewarm-x86_64-pc-windows-msvc.exe"],
    "windows": { "signCommand": "..." }
  },
  "plugins": {
    "tray": {}, "global-shortcut": {}, "autostart": {"targets": []},
    "notification": {}, "clipboard-manager": {}, "single-instance": {}
  },
  "app": { "security": { "capabilities": ["migrate-runtime"] } }
}
```
> **Tauri v2 capabilities are mandatory (not optional).** Unlike v1, Tauri v2 ships zero IPC/shell/plugin permissions by default — every `invoke`, `shell:spawn`, `notification:notify`, `clipboard` read/write, and `global-shortcut` must be explicitly whitelisted in `src-tauri/capabilities/*.json` or Tauri **silently blocks it at runtime** (no compile error). The implementer must confirm each capability below is present, or paste/clipboard/notification will mysteriously no-op.

`migrate-runtime.capability` (least privilege):
- `core:default`, `core:event:default`, `core:window:allow-show`/`hide`/`set-focus`
- `shell:allow-spawn` **scoped to the sidecar binary** — Tauri v2 rejects an unconstrained spawn, so the capability must name the exact `externalBin`:
  ```json
  { "identifier": "shell:allow-spawn",
    "allow": [{ "name": "bin/python-sidecar-x86_64-pc-windows-msvc" }] }
  ```
  `shell:allow-kill-children` for the FT-1 force-kill backstop (§10).
- `global-shortcut:allow-register` / `unregister`
- `clipboard-manager:allow-read-text` / `write-text` / `clear`
- `notification:allow-notify`
- `single-instance:default`
- **Exactly ONE generic Rust command** bridges the webview to the sidecar — do **not** write a per-command `tauri::command` for each of the 67 commands. The webview calls `invoke('dispatch',{cmd,data})`; Rust forwards the envelope over WS, awaits the response keyed by a per-request id, and returns it:

  ```rust
  #[tauri::command]
  async fn dispatch(cmd: String, data: serde_json::Value) -> Result<serde_json::Value, String> {
      // assign a request id; send {"type": cmd, "data": data, "id": id} over the WS client;
      // await the matching {"id": id, ...} response; return it.
  }
  ```

  Because there is a single command, no per-command `ipc:` capability entry is needed — Rust maps `dispatch` to the WS connection.

### 8. Path resolution
- Config: `%LOCALAPPDATA%/voice-typer/config.json` (same as today's `config.py` `APPNAME` dir). Sidecar reads it from `os.environ["LOCALAPPDATA"]` (CWD-independent, unchanged).
- Models: `%LOCALAPPDATA%/voice-typer/models` (HF_HOME redirected here). `prewarm` warms this path.
- Logs: `%LOCALAPPDATA%/voice-typer/logs` (see §11).
- **Electron `userData` migration:** on first Tauri launch, if `%LOCALAPPDATA%/voice-typer` is absent but the old Electron `userData/voice-typer` exists, copy it once (config + models + history) — one-time, idempotent. Off by default until validated.
  - **Both exist (merge rule):** if both `%LOCALAPPDATA%/voice-typer` and the old Electron `userData/voice-typer` exist and differ, do **not** blindly overwrite: (a) `config.json` — merge key-by-key, **newest mtime wins** per key; (b) `models/` — copy only files **absent** from the target (never clobber a newer download); (c) `history.db` — **append**, never replace (history is append-only and irreplaceable); (d) log a summary of what was merged. Prevents silently destroying user data on a revert-then-relaunch.
  - **Ordering (write-conflict trap):** run the migration/merge **before** the sidecar starts. If the sidecar boots first it initializes a fresh empty `config.json` / `history.db`; the later merge then hits a file lock / write conflict or silently ignores the old data. Migrate → then spawn.

### 9. `bubble_level` throttling
- Sidecar emits `bubble_level` at source ~60 Hz. **Rust coalesces**: keep only the latest `{rms,peak}` and emit a Tauri event at ≤ 30 Hz (or on `requestAnimationFrame`). Prevents WebView jank. The ~60 Hz source throttle in `app.py` stays; Rust adds a second coalescing throttle.

### 10. WebSocket disconnect / error handling
- **Clean shutdown:** Rust sends `{"type":"shutdown"}`; sidecar releases mic, acks `{"type":"result"}`, exits 0. **Hard timeout:** if the sidecar has not exited within **2.0 s** of the ack (e.g. it is stuck inside a native CTranslate2 call and cannot service the WS message), Rust force-kills the process tree via Tauri's `kill_children` handle. Never wait indefinitely on a blocked Python thread.
- **Sidecar crash / WS close without shutdown:** Rust treats it as a crash → FT-1 respawn (backoff 500→1000→2000 ms, cap 5). In-flight chunk discarded.
- **Token validation failure:** sidecar closes the socket immediately; Rust logs + shows "connection rejected", retries with a fresh token (counts toward FT-1 backoff).
  - **Transient loopback blip:** Rust attempts one immediate reconnect; on failure, falls into FT-1 backoff.
  - **Frame-size limit:** cap WS messages at **1 MiB** (`tokio-tungstenite` `max_frame_size` on the Rust client; `websockets` `max_size` on the server). `download_progress` and `vocabulary_suggestion` can carry large payloads; without a limit a malformed/huge frame can OOM the client. Reject oversized frames with a clean error rather than buffering unbounded.
  - **Malformed frames:** a WS frame that is not valid JSON (or fails `_validate_dict_payload`) must yield `{"type":"error","code":"invalid_payload","data":{...}}` and leave the connection open — the sidecar must **never** crash on a bad frame. The Rust client treats a non-`result`/`error` response as a protocol error, not a crash.

### 11. Logging / diagnostics
- Sidecar runs `--windows-disable-console`; Rust reads the sidecar `stdout` pipe for the `server_started` JSON (see §1) and **tees both streams** to `%LOCALAPPDATA%/voice-typer/logs/sidecar.log` (Tauri `appLogDir`). Rust also writes `tauri.log`.
- **Rotation:** `log.py` must use a `RotatingFileHandler` (e.g. 5 MB × 5 files ≈ 25 MB cap) instead of an unbounded `FileHandler`, or `sidecar.log` grows without limit across sessions.
- **Exclude `bubble_level` from the file log.** At ~60 Hz it would fill disk fast even with rotation. Ensure `bubble_level` publishes are logged at `DEBUG` only (or suppressed in `log.py` / the `event_bus`→file forwarder) so file logs capture events/errors, not the level stream. Rust already coalesces the event to ≤30 Hz for the UI (§9); the file path must drop it entirely.
- Keep the Python `logging` config (`log.py`) otherwise unchanged; it writes to the file resolved from env. Do NOT rely on console output post-migration.

### 12. Single-instance behavior
- `single-instance` plugin enabled. Second launch → existing instance focused (`show` + `setFocus` on main window) and a `second-instance` event emitted so tray/UI can surface. No second sidecar spawned. Matches "one app" expectation.
- **Ordering is critical:** the `single-instance` duplicate check must run at the **absolute entry point of `main.rs` — before any sidecar initialization** (token gen, `stdout` port handshake, `shell:spawn`). If a second launch reaches the spawn code before the duplicate is detected, you get a **zombie sidecar** (and a competing mic holder) on every double-click of the desktop shortcut. Detect the duplicate first; only the surviving instance starts the sidecar.
- **CLI args / deep links:** forward the second instance's argv to the running instance via the `single-instance` `args` payload → re-emit as a Tauri event (or internal Rust message). The current `app.py` mutex (`VoiceTyperSingleInstance`) only blocks duplicates; under Tauri the args must be delivered so deep links / `voice-typer:` URIs still open the right view.

### 13. Code-signing order
1. Nuitka-produced `python-sidecar-*.exe` and `prewarm-*.exe` are **Authenticode-signed** immediately after build (before bundling) — unsigned sidecars trigger SmartScreen / AV.
2. Tauri builds the MSI/EXE; the bundler signs the main executable + installer.
3. Optionally re-sign the final bundle. Keep cert + timestamp server configured in CI.

**Signing specifics:**
- Tool: `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a <exe>`. Use an RFC-3161 timestamp server (DigiCert shown) so the signature survives cert expiry.
  - **`--onefile` self-extraction caveat:** Nuitka `--onefile` bundles an inner exe that extracts to a temp dir at runtime. **Only the outer `.exe` is signed** — the extracted inner exe is transient and not separately signed, which is fine; do not attempt to sign the inner payload. AV may briefly flag the temp extraction; that is expected and benign.
  - **Antivirus / SmartScreen QA note:** during `--onefile` self-extraction the inner exe briefly appears in a temp dir *unsigned*; procmon / AV consoles will show an "unsigned" child process. That is the expected transient stage, **not** a packaging bug — do not flag it in QA. The outer `.exe` is what is Authenticode-signed and what SmartScreen validates.
- Sign both `python-sidecar-*` and `prewarm-*` exes before they enter the Tauri bundle; the bundler then signs the host + MSI.

### 14. Dev workflow + WebSocket dependency
- **WebSocket library:** the current IPC is raw TCP — there is **no** WS library in `requirements.txt`. Add **`websockets`** (server-capable) and pin it; Nuitka must `--include-package=websockets` (§4). The sidecar is the WS *server* (`websockets.serve`); Rust is the WS *client*.
- **`cargo tauri dev` without recompiling Nuitka:** add a dev mode where the sidecar runs as a plain Python process instead of the frozen exe. Rust reads `VOICE_TYPER_SIDECAR_DEV=1`; when set it spawns `python -m voice_typer.server.ipc_server` (with the same `VOICE_TYPER_IPC_PORT` / `VOICE_TYPER_IPC_TOKEN` env) instead of the `externalBin`. UI/transport iterate in seconds, not minutes; the frozen exe is still used in `release`/bundled builds.

### Phase 0 validation gate (concrete)
- [ ] Nuitka exe builds from `python-build-standalone`.
- [ ] `externalBin` sidecar spawns via Tauri; Rust reads `server_started` JSON from sidecar `stdout`, connects WS to the reported port.
- [ ] HMAC handshake: wrong token rejected; correct token accepted.
- [ ] **`faster-whisper` `WhisperModel("tiny")` loads + transcribes a WAV inside the Nuitka exe** (proves CTranslate2/DLLs/models).
- [ ] `enigo` types text into Notepad; clipboard+Ctrl+V path verified.
- [ ] `tauri-plugin-notification` shows a toast.
- [ ] Cooperative `{"type":"shutdown"}` exits cleanly; `kill_children` cleans on hard kill.
- [ ] Prewarm exe registered as a LogonTrigger scheduled task (via `resolve_prewarm_exe`) and warms cache.

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
- **Sidecar→UI event table:** extracted above from `event_bus.publish` / `_push_event_now` call sites — 21 events mapped, payloads carried 1:1.

## References

- ADR-0001 (Electron + Python Architecture, Accepted) — current architecture, retained as the reversible fallback.
- ADR-0009 (Prewarm & Autostart Architecture) — prewarm / LogonTrigger scheduled-task design preserved by this ADR.
- Tauri v2 sidecar guide (v2.tauri.app/develop/sidecar) — first-class `externalBin` sidecar feature.
- Tauri discussion #1645 (github.com/tauri-apps/tauri/discussions/1645) — sidecar trade-offs.
- `python-build-standalone` (by Gregory Szorc) — clean pre-built Python for sidecar bundling.
- Tauri `kill_children` + `single-instance` plugin — lifecycle/cleanup correctness.
- FT-1 in `.workspace/TASKS.md` — crash isolation (restart backend only, keep UI alive).
- `electron_launcher.py:13` — current spawn of `python -m voice_typer.server.ipc_server --port 9876` (replaced by sidecar in Phase 2).
- `voice_typer/server/ipc_server.py`, `voice_typer/server/handlers/*` — TCP IPC + handler registry (bridged to sidecar WebSocket in Phase 2).
- `voice_typer/server/prewarm.py`, `voice_typer/server/task_scheduler.py` — prewarm + LogonTrigger scheduled task (kept).

*End of document.*
