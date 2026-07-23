# Voice Typer — Architecture

## Dual Runtime Stacks: Electron + Tauri (migration in progress)

Voice Typer is migrating from Electron to **Tauri v2 + Python sidecar** per [ADR-0020](adr/0020-desktop-runtime-migration-analysis.md). Both stacks ship from the same source tree; the **Electron stack is the current default**, and the **Tauri stack is additive** (the Electron code is untouched and remains a reversible fallback). Cutover is per-platform (Windows first → macOS → Linux) per the [cutover playbook](migration/cutover-playbook.md). The renderer code (`usePython.ts`, pages, components) is **byte-identical on both paths** — the runtime difference is absorbed entirely by the bridge.

The diagram in the next section ("High-level overview") shows the **Electron** stack (the default shipping app). The Tauri stack is described below it.

### Backend (shared by both stacks)

The Python backend lives in `voice_typer/server/` and is unchanged between the two stacks:

| Module | Purpose |
|---|---|
| `voice_typer/server/ipc_server.py` | JSON-lines IPC server (TCP `127.0.0.1:9876` on Electron; localhost WebSocket on Tauri). SEC-018 session-token auth, RELIABILITY-006 rate limiter, 77-command `_COMMAND_REGISTRY`, 24-event bus. Under `TAURI_SIDECAR=1`: heartbeat watchdog (ADR-0018) is NOT started; Win32 single-instance mutex is NOT acquired. |
| `voice_typer/server/app.py` | `VoiceTyperApp` orchestrator — startup, state machine, thread safety. |
| `voice_typer/server/recording/` + `recording_controller.py` | PortAudio capture, silence detection, session lifecycle, streaming. (Recording was decomposed into a package — see `docs/rw04-recording-decomposition.md`.) |
| `voice_typer/server/transcription.py` + `asr_registry.py` + `qwen_engine.py` + `parakeet_engine.py` | ASR pipeline (Whisper / Qwen3-ASR / Parakeet, with GPU→CPU fallback). |
| `voice_typer/server/text_cleanup.py` + `vocabulary.py` + `templates.py` + `llm_polish.py` | Post-transcription cleanup, user corrections, snippets, optional LLM polish (PRIVACY-001 consent gate). |
| `voice_typer/server/clipboard.py` | Clipboard copy + safe auto-paste (Win32 focus detection). |
| `voice_typer/server/hotkeys/` + `hotkey_dispatcher.py` + `native_hotkeys/` | 3 hotkey backends (dictation / ESC / repaste) + native binary lookup (`VOICE_TYPER_NATIVE_DIR` env var for Tauri resource path). |
| `voice_typer/server/tray.py` + `tray_menu.py` | pystray tray icon (works under Tauri because the sidecar inherits the desktop session). |
| `voice_typer/server/prewarm_scheduler_posix.py` + `prewarm_resolver.py` + `task_scheduler.py` | Prewarm scheduling (Windows Task Scheduler / macOS LaunchAgent / Linux systemd user timer). `resolve_prewarm_exe()` finds the frozen `prewarm-<triple>[.exe]` for the Tauri path. |
| `voice_typer/server/crash_recovery.py` | Crash-recovery buffer (RELIABILITY-005 async flush). |
| `voice_typer/server/history_db.py` | SQLite WAL history DB (SEC-007 `0o600` perms). |
| `voice_typer/server/sidecar_ws.py` (NEW for Tauri) | WebSocket server side of the Tauri bridge. Binds `127.0.0.1:0`, emits `{"event":"server_started","port":N}` to stdout, performs bearer-token auth, dispatches WS frames via `IPCServer._dispatch` (reuses the 73-command registry unchanged). |
| `voice_typer/server/shutdown_controller.py` (RW-9 extraction) | `ShutdownController` — extracted from `VoiceTyperApp`. Owns the entire shutdown / cleanup lifecycle: the shared idempotent `_do_cleanup` body invoked by `quit()`, `restart_app()`, and `_atexit_cleanup()`. Releases every subsystem (recorder, hotkeys, history DB, crash recovery, bubble level worker, Win32 mutex, Electron subprocess, devnull FDs). |
| `voice_typer/server/audio_quality_controller.py` (RW-9 extraction) | `AudioQualityController` — extracted from `VoiceTyperApp`. Owns per-chunk audio-quality accumulation, on-the-fly filter-chain rebuilds on config change, and the final post-recording quality report. |
| `voice_typer/server/crash_handler.py` (Windows SEH) | Vectored Exception Handler (`AddVectoredExceptionHandler`) that captures silent Windows process crashes (STATUS_HEAP_CORRUPTION, STATUS_ACCESS_VIOLATION) which terminate the process before Python's traceback machinery runs. Writes a minimal diagnostic blurb to the recovery file before the OS kills the process. |
| `voice_typer/server/duck_crash_recovery.py` | Persists the pre-duck system volume to a small JSON file on `VolumeDucker.duck()` and deletes it on `restore()`. On the next app launch, `VolumeDucker.initialize` checks for a stale file and restores the saved volume — preventing the system from being stuck at the ducked level (e.g. 25%) indefinitely after a crash. |
| `voice_typer/server/keyboard_ownership.py` (ARCH-ESC-001) | Centralized keyboard ownership manager. Solves the "Escape key routing" problem with a clean ownership model instead of scattered boolean flags across the frontend HotkeyPicker, native hotkey backend, and tray. Single owner at any time; clean handoff protocol. |
| `voice_typer/server/level_monitor.py` | Continuous microphone level monitoring + ad-hoc test recording. Opens ONE `sounddevice.InputStream` for both purposes (continuous RMS/peak for the live level bar + test-recording buffer) — eliminates the PortAudio device contention that two separate streams caused. |
| `voice_typer/server/log_rate_limit.py` | `log_rate_limited(log, level, msg, every=N)` — emits a record on the 1st occurrence and every Nth occurrence thereafter; other occurrences fall through to DEBUG so default logs aren't spammed by tight-loop warnings (audio callback, mic watcher). |
| `voice_typer/server/thread_registry.py` | Central registry for daemon threads. Each registered thread carries a name, `stop_event`, and `join_timeout`. `shutdown_all()` cancels every stop event and joins every thread in registration order — gives the app a single chokepoint for graceful daemon teardown instead of ad-hoc `Thread.join()` calls scattered across modules. |
| `voice_typer/server/timer_coordinator.py` (RW-9 extraction) | `TimerCoordinator` — extracted from `VoiceTyperApp`. Owns the lifecycle of fire-and-forget `threading.Timer` instances. A *generation guard* prevents stale callbacks (scheduled before a cancel) from firing after `_cancel_pending_timers` has bumped the generation counter. |
| `voice_typer/server/volume_backend_base.py` | Abstract `VolumeBackend` interface. Platform-agnostic contract for reading/setting system volume in perceptual-linear scale `[0.0, 1.0]` (0.0 = silent, 1.0 = max, 0.25 = default duck level). All concrete backends (`WinVolumeBackend`, `MacVolumeBackend`, `LinuxVolumeBackend`) implement this interface. |
| `voice_typer/server/volume_backends.py` | Concrete volume backends for Windows (pycaw), macOS (CoreAudio via pyobjc), and Linux (pactl/wpctl/amixer). All platform-specific imports are guarded so the module imports cleanly on any OS — `initialize()` returns `False` if the native library is unavailable. `get_volume_backend()` selects the first backend whose `initialize()` succeeds. |
| `voice_typer/server/volume_controller.py` (RW-9 extraction) | `VolumeController` — extracted from `VoiceTyperApp`. Owns the system-volume side effects of the dictation lifecycle: `_on_volume_crash_restore` (tray notification fired by `VolumeDucker` when it discovers a stale duck state on startup) and `_duck_volume` (smart-duck + master-volume duck at the start of dictation). |
| `voice_typer/server/volume_ducker.py` | `VolumeDucker` — orchestrates system audio volume ducking during dictation. On dictation start, reduces volume to the configured duck level (default 25%). On stop, restores the original volume — including mute state — with a short fade ramp. Persists duck state via `duck_crash_recovery.py` so a crash mid-dictation doesn't leave the system muted. |
| `voice_typer/server/clipboard_snapshot.py` (ADR-0010 §4) | Multi-format clipboard snapshot/restore. Captures and restores ALL clipboard formats (text, HTML, image, RTF, files on Windows) — `pyperclip` is text-only and was insufficient for the borrow/restore protocol. Platform-dispatched; snapshots passed as values, not stored as instance state (DP4). Every borrow is paired with a restore (DP1). |
| `voice_typer/server/vocabulary_automation.py` | Confidence-score-based correction suggestions. Analyzes transcribed text alongside its per-token confidence scores and proposes vocabulary corrections for low-confidence words. Suggestions can be auto-applied (high-confidence case) or queued for user review via `vocabulary_automation_handlers.py`. Opt-in — the dictation pipeline only invokes it when the config flag is set. |

### Frontend (shared by both stacks)

The React renderer lives in `voice_typer/client/src/renderer/` and is **the same bundle on both paths**:

| Module | Purpose |
|---|---|
| `voice_typer/client/src/renderer/src/App.tsx` + `main.tsx` + `bubble-main.tsx` | React root, routing, bubble window root. `main.tsx` and `bubble-main.tsx` both `import "./lib/tauri-bridge"` BEFORE the React app mounts so the `window.python`/`window.bubble`/`window.window_` namespaces are ready when `usePython` and other hooks initialize. |
| `voice_typer/client/src/renderer/src/hooks/usePython.ts` | Shared IPC hook — reconnects, request/response correlation, event subscription, NEW-IPC-107 error-envelope parity. |
| `voice_typer/client/src/renderer/src/pages/` + `components/` | Home, Settings, History, Models, Vocabulary, Templates, Microphone, Dashboard, Onboarding, About + shadcn/ui components. |

### Electron host (default shipping app)

| Component | Source | Purpose |
|---|---|---|
| Electron main process | `voice_typer/client/src/main/index.ts` (209 lines — wiring-only; logic in `./state/`, `./python/`, `./ipc/`, `./windows/`, `./bootstrap`) | Generates 32-byte `IPC_TOKEN`, spawns Python backend as a child process, bridges `ipcMain`/`ipcRenderer` ↔ TCP `127.0.0.1:9876`, manages main + bubble windows, owns ALLOWED_COMMANDS allowlist (SEC-002 lateral boundary). |
| Preload bridges | `voice_typer/client/src/preload/index.ts`, `voice_typer/client/src/preload/bubble.ts` | `contextBridge.exposeInMainWorld` installs `window.python`, `window.bubble`, `window.window_`. SEC-014 `contextIsolation: true` + `sandbox: true`; SEC-016 `assertFromBubble(event)` on bubble-scoped handlers. |
| Electron launcher | `voice_typer/server/electron_launcher.py` (215 lines) | Inverse path (Python-as-parent) — also exists for the standalone Python install. |

### Tauri host (in migration, not yet the default)

| Component | Source | Purpose |
|---|---|---|
| Rust host | `src-tauri/src/main.rs` (449 lines — wiring-only; logic in mod `sidecar/`, `commands/`, `platform/`, `tray`) | Spawns the Python sidecar via Tauri's `externalBin` (one binary per target triple), reads `{"event":"server_started","port":N}` JSON from stdout, opens a localhost WebSocket client with 1 MiB frame cap, performs bearer-token auth, exposes ONE generic `dispatch` Tauri command to the webview, subscribes to server-initiated events (emits BOTH the specific event name AND a generic `python-event` envelope), coalesces `bubble_level` 60Hz→30Hz, runs FT-1 supervisor with 500ms→1s→2s→4s→8s backoff (cap 5 → full-app relaunch via `AppHandle::restart()`), drains pending dispatch requests + clears `ws_tx` on WS disconnect, cooperative shutdown with 2s ack timeout + `kill_children` backstop. |
| Tauri config | `src-tauri/tauri.conf.json` | Per-arch `externalBin` (6 target triples) + `resources` (3 native hotkey binaries + 6 prewarm binaries) + Tauri v2 capabilities. `withGlobalTauri: true` so `window.__TAURI__` is available to the renderer bridge. CSP carries over from the Electron `csp-plugin.ts`. |
| Capabilities | `src-tauri/capabilities/main-runtime.json` + `bubble-runtime.json` | CR-5 / SEC-026 split: `main-runtime` grants the privileged main window scoped `shell:allow-spawn` per sidecar binary, `notification`, `clipboard-manager`, `dialog`, and `core:tray:default` + 7 tray perms (`allow-set-icon` / `allow-set-menu` / `allow-set-tooltip` / `allow-set-title` / `allow-get-by-id` / `allow-remove-by-id` / `allow-new`) — Rust host owns the tray; pystray is the Electron-fallback path only. `bubble-runtime` is minimal (`core:event:default` + `core:window:allow-start-dragging`) so a compromised bubble renderer cannot spawn, write clipboard, or touch the tray. |
| Cargo manifest | `src-tauri/Cargo.toml` | Tauri v2 + plugins (`shell`, `notification`, `clipboard-manager`, `single-instance`, `dialog`) + `enigo` (keystroke injection) + `tokio-tungstenite` (WS client) + `rand` (token gen) + Windows `windows` crate for `AttachThreadInput`/`SetForegroundWindow`. |

### Bridge: `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`

The Phase 3 UI port is the architectural keystone of the migration: the **renderer code is identical on both paths**. The runtime difference is absorbed entirely by the bridge, which auto-detects the host at startup and installs the right namespace:

- **Electron path** — `client/src/preload/index.ts` runs in the preload world and uses `contextBridge.exposeInMainWorld` to install `window.python`, `window.bubble`, `window.window_`. The bridge module's `installTauriBridge()` detects the absence of `window.__TAURI__` and **early-returns** — it does NOT touch the preload-installed namespaces (referential identity preserved, verified by `tauri-bridge-commands.test.ts`).
- **Tauri path** — `tauri.conf.json` sets `withGlobalTauri: true`, so the Tauri runtime injects `window.__TAURI__` (with `core.invoke`, `event.listen`, `window.getCurrentWindow`) before the renderer JS executes. The bridge module's auto-install side effect (last line of `tauri-bridge.ts`) calls `installTauriBridge()`, which sees `__TAURI__` and installs `window.python`/`window.bubble`/`window.window_` using Tauri's global API.

Both `main.tsx` (main window) and `bubble-main.tsx` (bubble window) import `./lib/tauri-bridge` BEFORE the React app mounts, so the namespaces are ready when `usePython` and other hooks initialize.

### IPC contract

The IPC surface is **frozen for v1** at **77 commands / 24 events** (CR-18 reconciliation 2026-07-19; see ADR-0020 §16). The same `_COMMAND_REGISTRY` in `voice_typer/server/ipc_server.py` dispatches both the Electron TCP path and the Tauri WebSocket path. The Electron main-process `ALLOWED_COMMANDS` allowlist (`client/src/main/index.ts`) is the lateral security boundary on the Electron path; the Tauri `dispatch` command's window-label check + `externalBin`-scoped capability (`src-tauri/capabilities/main-runtime.json` + `bubble-runtime.json`) is the lateral boundary on the Tauri path (CR-5 split).
