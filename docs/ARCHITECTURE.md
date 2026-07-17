# Voice Typer — Architecture

## Dual Runtime Stacks: Electron + Tauri (migration in progress)

Voice Typer is migrating from Electron to **Tauri v2 + Python sidecar** per [ADR-0020](adr/0020-desktop-runtime-migration-analysis.md). Both stacks ship from the same source tree; the **Electron stack is the current default**, and the **Tauri stack is additive** (the Electron code is untouched and remains a reversible fallback). Cutover is per-platform (Windows first → macOS → Linux) per the [cutover playbook](migration/cutover-playbook.md). The renderer code (`usePython.ts`, pages, components) is **byte-identical on both paths** — the runtime difference is absorbed entirely by the bridge.

The diagram in the next section ("High-level overview") shows the **Electron** stack (the default shipping app). The Tauri stack is described below it.

### Backend (shared by both stacks)

The Python backend lives in `voice_typer/server/` and is unchanged between the two stacks:

| Module | Purpose |
|---|---|
| `voice_typer/server/ipc_server.py` | JSON-lines IPC server (TCP `127.0.0.1:9876` on Electron; localhost WebSocket on Tauri). SEC-018 session-token auth, RELIABILITY-006 rate limiter, 68-command `_COMMAND_REGISTRY`, 21-event bus. Under `TAURI_SIDECAR=1`: heartbeat watchdog (ADR-0018) is NOT started; Win32 single-instance mutex is NOT acquired. |
| `voice_typer/server/app.py` | `VoiceTyperApp` orchestrator — startup, state machine, thread safety. |
| `voice_typer/server/recording.py` + `recording_controller.py` | PortAudio capture, silence detection, session lifecycle, streaming. |
| `voice_typer/server/transcription.py` + `asr_registry.py` + `qwen_engine.py` + `parakeet_engine.py` | ASR pipeline (Whisper / Qwen3-ASR / Parakeet, with GPU→CPU fallback). |
| `voice_typer/server/text_cleanup.py` + `vocabulary.py` + `templates.py` + `llm_polish.py` | Post-transcription cleanup, user corrections, snippets, optional LLM polish (PRIVACY-001 consent gate). |
| `voice_typer/server/clipboard.py` | Clipboard copy + safe auto-paste (Win32 focus detection). |
| `voice_typer/server/hotkeys.py` + `hotkey_dispatcher.py` + `native_hotkeys.py` | 3 hotkey backends (dictation / ESC / repaste) + native binary lookup (`VOICE_TYPER_NATIVE_DIR` env var for Tauri resource path). |
| `voice_typer/server/tray.py` + `tray_menu.py` | pystray tray icon (works under Tauri because the sidecar inherits the desktop session). |
| `voice_typer/server/prewarm_scheduler_posix.py` + `prewarm_resolver.py` + `task_scheduler.py` | Prewarm scheduling (Windows Task Scheduler / macOS LaunchAgent / Linux systemd user timer). `resolve_prewarm_exe()` finds the frozen `prewarm-<triple>[.exe]` for the Tauri path. |
| `voice_typer/server/crash_recovery.py` | Crash-recovery buffer (RELIABILITY-005 async flush). |
| `voice_typer/server/history_db.py` | SQLite WAL history DB (SEC-007 `0o600` perms). |
| `voice_typer/server/sidecar_ws.py` (NEW for Tauri) | WebSocket server side of the Tauri bridge. Binds `127.0.0.1:0`, emits `{"event":"server_started","port":N}` to stdout, performs bearer-token auth, dispatches WS frames via `IPCServer._dispatch` (reuses the 68-command registry unchanged). |

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
| Electron main process | `voice_typer/client/src/main/index.ts` (2,205 lines) | Generates 32-byte `IPC_TOKEN`, spawns Python backend as a child process, bridges `ipcMain`/`ipcRenderer` ↔ TCP `127.0.0.1:9876`, manages main + bubble windows, owns ALLOWED_COMMANDS allowlist (SEC-002 lateral boundary). |
| Preload bridges | `voice_typer/client/src/preload/index.ts`, `voice_typer/client/src/preload/bubble.ts` | `contextBridge.exposeInMainWorld` installs `window.python`, `window.bubble`, `window.window_`. SEC-014 `contextIsolation: true` + `sandbox: true`; SEC-016 `assertFromBubble(event)` on bubble-scoped handlers. |
| Electron launcher | `voice_typer/server/electron_launcher.py` (215 lines) | Inverse path (Python-as-parent) — also exists for the standalone Python install. |

### Tauri host (in migration, not yet the default)

| Component | Source | Purpose |
|---|---|---|
| Rust host | `src-tauri/src/main.rs` (1,866 lines) | Spawns the Python sidecar via Tauri's `externalBin` (one binary per target triple), reads `{"event":"server_started","port":N}` JSON from stdout, opens a localhost WebSocket client with 1 MiB frame cap, performs bearer-token auth, exposes ONE generic `dispatch` Tauri command to the webview, subscribes to server-initiated events (emits BOTH the specific event name AND a generic `python-event` envelope), coalesces `bubble_level` 60Hz→30Hz, runs FT-1 supervisor with 500ms→1s→2s→4s→8s backoff (cap 5 → full-app relaunch via `AppHandle::restart()`), drains pending dispatch requests + clears `ws_tx` on WS disconnect, cooperative shutdown with 2s ack timeout + `kill_children` backstop. |
| Tauri config | `src-tauri/tauri.conf.json` | Per-arch `externalBin` (6 target triples) + `resources` (3 native hotkey binaries + 6 prewarm binaries) + Tauri v2 capabilities. `withGlobalTauri: true` so `window.__TAURI__` is available to the renderer bridge. CSP carries over from the Electron `csp-plugin.ts`. |
| Capabilities | `src-tauri/capabilities/migrate-runtime.json` | Least-privilege: scoped `shell:allow-spawn` per sidecar binary, `notification`, `clipboard-manager`, `single-instance`, `dialog`. **No `core:tray:*`** (sidecar owns tray via pystray). |
| Cargo manifest | `src-tauri/Cargo.toml` | Tauri v2 + plugins (`shell`, `notification`, `clipboard-manager`, `single-instance`, `dialog`) + `enigo` (keystroke injection) + `tokio-tungstenite` (WS client) + `rand` (token gen) + Windows `windows` crate for `AttachThreadInput`/`SetForegroundWindow`. |

### Bridge: `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`

The Phase 3 UI port is the architectural keystone of the migration: the **renderer code is identical on both paths**. The runtime difference is absorbed entirely by the bridge, which auto-detects the host at startup and installs the right namespace:

- **Electron path** — `client/src/preload/index.ts` runs in the preload world and uses `contextBridge.exposeInMainWorld` to install `window.python`, `window.bubble`, `window.window_`. The bridge module's `installTauriBridge()` detects the absence of `window.__TAURI__` and **early-returns** — it does NOT touch the preload-installed namespaces (referential identity preserved, verified by `tauri-bridge-commands.test.ts`).
- **Tauri path** — `tauri.conf.json` sets `withGlobalTauri: true`, so the Tauri runtime injects `window.__TAURI__` (with `core.invoke`, `event.listen`, `window.getCurrentWindow`) before the renderer JS executes. The bridge module's auto-install side effect (last line of `tauri-bridge.ts`) calls `installTauriBridge()`, which sees `__TAURI__` and installs `window.python`/`window.bubble`/`window.window_` using Tauri's global API.

Both `main.tsx` (main window) and `bubble-main.tsx` (bubble window) import `./lib/tauri-bridge` BEFORE the React app mounts, so the namespaces are ready when `usePython` and other hooks initialize.

### IPC contract

The IPC surface is **frozen for v1** at **68 commands / 21 events** (see ADR-0020 §16). The same `_COMMAND_REGISTRY` in `voice_typer/server/ipc_server.py` dispatches both the Electron TCP path and the Tauri WebSocket path. The Electron main-process `ALLOWED_COMMANDS` allowlist (`client/src/main/index.ts`) is the lateral security boundary on the Electron path; the Tauri `dispatch` command's `externalBin`-scoped capability (`src-tauri/capabilities/migrate-runtime.json`) is the lateral boundary on the Tauri path.

### Dev mode

`VOICE_TYPER_SIDECAR_DEV=1 cargo tauri dev` (in `src-tauri/`) runs the Rust host against a `python -m voice_typer.server.ipc_server --ws` subprocess for fast iteration — no Nuitka rebuild needed. See [CONTRIBUTING.md § Tauri Development](../CONTRIBUTING.md#tauri-development-migration-in-progress) and the [bridge architecture doc](migration/tauri-sidecar-bridge.md).

### Migration status

| Platform | Status | Runbook |
|---|---|---|
| Windows | Phase 0-W pending real-host validation (Nuitka exe + Tauri spawn + WS + HMAC + faster-whisper + enigo + notification + cooperative shutdown + prewarm LogonTrigger + native hotkey). | [`migration/windows-validation-runbook.md`](migration/windows-validation-runbook.md) |
| macOS | Phase 0-M not started. | [`migration/macos-validation-runbook.md`](migration/macos-validation-runbook.md) |
| Linux (X11 + Wayland) | Phase 0-L not started. | [`migration/linux-validation-runbook.md`](migration/linux-validation-runbook.md) |
| Cutover | Per-platform; Electron remains the default until each platform's Tauri build is proven. | [`migration/cutover-playbook.md`](migration/cutover-playbook.md) |
| Build | Nuitka freeze + Tauri bundle per target triple. | [`migration/tauri-build-runbook.md`](migration/tauri-build-runbook.md) |
| Bridge | Phase 3 UI port complete (renderer shared between both stacks). | [`migration/tauri-sidecar-bridge.md`](migration/tauri-sidecar-bridge.md) |

For the full migration contract (architecture boundaries, what stays / what moves / what is removed, per-platform Nuitka + signing + paste + autostart + prewarm + path resolution), see [ADR-0020](adr/0020-desktop-runtime-migration-analysis.md).

---

## High-level overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Electron Main Process                       │
│  (client/src/main/index.ts)                                     │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ Main Window │  │ Bubble Win  │  │ Python subprocess       │ │
│  │ (renderer)  │  │ (renderer)  │  │ (spawned via child_proc)│ │
│  │ React+Vite  │  │ React+Vite  │  │ voice_typer.server.     │ │
│  │             │  │ always-on-top│ │ ipc_server               │ │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘ │
│         │                │                     │               │
│         │ ipcRenderer    │ ipcRenderer         │ TCP 127.0.0.1 │
│         │ (preload bridge)│ (preload bridge)   │ :9876         │
│         │                │                     │ + auth token  │
└─────────┼────────────────┼─────────────────────┼───────────────┘
          │                │                     │
          ▼                ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Python Backend (server/)                      │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ IPCServer (ipc_server.py)                                │  │
│  │  • JSON-lines over TCP (loopback only)                   │  │
│  │  • SEC-018: session token auth on first frame           │  │
│  │  • RELIABILITY-006: per-connection rate limiter          │  │
│  │  • SEC-009: 1 MB line cap  • SEC-010: limit bounding     │  │
│  │  • SEC-003: get_config redacts API keys                 │  │
│  │  • SEC-002: set_config allowlist (53 fields)            │  │
│  └──────────────────────┬───────────────────────────────────┘  │
│                         │ dispatch                              │
│  ┌──────────────────────▼───────────────────────────────────┐  │
│  │ VoiceTyperApp (app.py)                                    │  │
│  │  • Tray icon (pystray)  • Hotkey backends (3)            │  │
│  │  • Recorder (PortAudio)  • Transcription pipeline        │  │
│  │  • Crash recovery  • History DB  • Waveform bubble       │  │
│  └──────┬──────────┬───────────┬────────────┬───────────────┘  │
│         │          │           │            │                   │
│  ┌──────▼───┐ ┌────▼────┐ ┌────▼─────┐ ┌────▼──────────────┐   │
│  │ Config   │ │ Tray    │ │ Hotkeys  │ │ Recorder          │   │
│  │ (config  │ │ (tray   │ │ (hotkeys │ │ (recording.py)    │   │
│  │  .py)    │ │  .py)   │ │  .py)    │ │  PortAudio→deque  │   │
│  │          │ │          │ │          │ │  +scipy resample  │   │
│  │ SEC-002  │ │ ARCH-003 │ │ UX-001   │ │ PERF-001 preload  │   │
│  │ allowlist│ │ (todo)   │ │ PTT fix  │ │ PERF-002 WAL      │   │
│  └──────────┘ └──────────┘ └──────────┘ └────────┬──────────┘   │
│                                                   │              │
│  ┌────────────────────────────────────────────────▼──────────┐  │
│  │ Transcription Pipeline (app.py transcribe_thread)         │  │
│  │                                                            │  │
│  │  audio → [TranscriptionEngine / Qwen / Parakeet]           │  │
│  │       → text_cleanup (ARCH-009: skip if vocab enabled)     │  │
│  │       → VocabularyManager (single source for corrections)  │  │
│  │       → TemplateManager                                    │  │
│  │       → LLM polish (PRIVACY-001: consent gate)             │  │
│  │       → auto-punctuation                                   │  │
│  │       → clipboard paste                                    │  │
│  │       → history DB  +  crash recovery (async writes)       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ HistoryDB   │  │ CrashRecovery│  │ CloudEngines / LLMPolish│  │
│  │ (history_db │  │ (crash_recov │  │ (cloud_engines.py,      │  │
│  │  .py)       │  │  .py)        │  │  llm_polish.py)         │  │
│  │             │  │              │  │                         │  │
│  │ SQLite WAL  │  │ BG thread    │  │ RELIABILITY-004:        │  │
│  │ SEC-007:    │  │ RELIABILITY- │  │  URL allowlist +        │  │
│  │ 0o600 perms │  │  005: async  │  │  key redaction          │  │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

                    Filesystem layout (~/.voice-typer/)
                    ┌────────────────────────────────┐
                    │ config.json        (0o600)     │
                    │ history.db         (0o600)     │
                    │ history.db-wal     (0o600)     │
                    │ history.db-shm     (0o600)     │
                    │ voice-typer-recovery.json (0o600)│
                    │ corrections.json   (user-edit) │
                    │ voice-typer-corrections.json   │
                    │ huggingface/       (model cache)│
                    └────────────────────────────────┘
```

## Process lifecycle

1. **Electron main** starts → generates `IPC_TOKEN` (crypto.randomBytes)
2. Spawns Python subprocess with `VOICE_TYPER_IPC_TOKEN` env var
3. Python `ipc_server.main()` starts:
   - `_ensure_single_instance()` — Win32 named mutex (ARCH-012: psutil scan)
   - `VoiceTyperApp()` — creates Config, HistoryDB, CrashRecovery, Tray, HotkeyBackend
   - `IPCServer.start()` + `start_tcp(port)` — binds 127.0.0.1:9876
4. Electron connects via TCP, sends `{"type":"auth","token":...}` (SEC-018)
5. Python validates token → connection accepted
6. Electron creates main window + bubble window
7. User presses hotkey → `toggle_dictation()` → Recorder starts → audio callback at 16 Hz
8. PERF-NEW-001: bubble_level pushes go to background queue (not audio thread)
9. User releases hotkey → `stop_dictation()` → transcribe_thread runs pipeline
10. Result pasted to active window; history + crash recovery saved

## Security boundaries

| Boundary              | Protection                                              |
|-----------------------|---------------------------------------------------------|
| TCP IPC (loopback)    | SEC-018 session token auth                              |
| set_config            | SEC-002 allowlist (53 fields) + type/range/enum/URL val |
| get_config            | SEC-003 API keys redacted to `<redacted>`               |
| Cloud/LLM HTTP        | RELIABILITY-004 URL allowlist + key redaction in logs   |
| File permissions      | SEC-007: 0o600 files, 0o700 dirs on POSIX              |
| IPC rate              | RELIABILITY-006: 200 burst / 60 sustained msg/s         |
| IPC line size         | SEC-009: 1 MB cap                                       |
| History limit         | SEC-010: clamped to [1, 500]                            |
| Electron CSP          | SEC-012: default-src 'self' in both HTMLs              |
| DevTools              | SEC-013: guarded with `!app.isPackaged`                 |
| webPreferences        | SEC-014: contextIsolation + sandbox + webSecurity       |
| Bubble IPC scope      | SEC-016: `assertFromBubble(event)` on all handlers     |
| Broadcast             | SEC-017: filtered to mainWindow only                   |

## Key design decisions

- **RELIABILITY-001**: `tray._wrap` re-raises `SystemExit` (was swallowing it, forcing `os._exit(0)`). Now `quit()`/`restart_app()` use clean `sys.exit(0)`.
- **RELIABILITY-005**: crash recovery writes go to a background daemon thread with a bounded queue; `quit()` calls `flush()` before exit.
- **PERF-NEW-001**: bubble_level pushes are decoupled from the audio thread via a bounded queue + 30 Hz throttle.
- **ARCH-009**: `clean_transcribed_text(skip_corrections=True)` when VocabularyManager is enabled, avoiding double-application of corrections.
- **ARCH-013**: `_init_qwen_engine` and `_init_parakeet_engine` delegate to a generic `_init_asr_engine` dispatcher.

## ARCH-REFAC-003: VoiceTyperApp @property delegates removed

During the extraction (Task #2), `VoiceTyperApp` (in
`voice_typer/server/app.py`) grew ~12 `@property` delegates that
mirrored fields owned by the extracted modules:

- `ModelManager` (engine fields + lifecycle state):
  `transcriber`, `_qwen_engine`, `_parakeet_engine`, `_asr_registry`,
  `_model_load_thread`, `_model_load_attempted`, `_pending_dictation`
- `RecordingController`: `_transcription_thread`, `_streaming_session`
- `HotkeyDispatcher`: `_hotkey_backend`, `_esc_backend`, `_repaste_backend`

These delegates were added as a **transition strategy** so existing
callers (and tests) that read `app.transcriber` / `app._hotkey_backend`
/ etc. would keep working without modification while the extracted
modules were wired up. ARCH-REFAC-003 removes them now that the
extraction is complete and stable.

### Migration guide for callers

| Before (legacy delegate)            | After (direct module access)                                  |
|-------------------------------------|---------------------------------------------------------------|
| `app.transcriber`                   | `app.models.transcriber`                                       |
| `app._qwen_engine`                  | `app.models._qwen_engine`                                      |
| `app._parakeet_engine`              | `app.models._parakeet_engine`                                  |
| `app._asr_registry` (read)          | `app.models.registry`                                          |
| `app._asr_registry = X` (write)     | `app.models._registry = X`                                     |
| `app._model_load_thread`            | `app.models._model_load_thread`                                |
| `app._model_load_attempted`         | `app.models._model_load_attempted`                             |
| `app._pending_dictation`            | `app.models._pending_dictation`                                |
| `app._transcription_thread`         | `app.recording._transcription_thread`                          |
| `app._streaming_session`            | `app.recording._streaming_session`                             |
| `app._hotkey_backend`               | `app.hotkeys._hotkey_backend`                                  |
| `app._esc_backend`                  | `app.hotkeys._esc_backend`                                     |
| `app._repaste_backend`              | `app.hotkeys._repaste_backend`                                 |

### Important: registry sync is no longer automatic

The `transcriber`, `_qwen_engine`, and `_parakeet_engine` property
delegates had setters that called `ModelManager._sync_registry_from_fields()`
after assignment, keeping the `AsrBackendRegistry` consistent with
the legacy fields. **This auto-sync no longer happens** when callers
assign directly to `app.models.<field>`. Callers that mutate these
fields must invoke `app.models._sync_registry_from_fields()` explicitly
to keep the registry in sync. In normal operation the registry is the
source of truth (mutated via `ModelManager._ensure_engine` /
`ModelManager.load_background` / etc., which call
`_sync_legacy_fields` themselves), so this only matters for tests
that swap a mock in directly, e.g.:

```python
# Before (auto-synced):
app.transcriber = MagicMock()
app.transcriber.is_loaded = True

# After (explicit sync):
app.models.transcriber = MagicMock()
app.models.transcriber.is_loaded = True
app.models._sync_registry_from_fields()
```

`ModelManager.active_transcriber()` still calls
`_sync_registry_from_fields()` lazily before returning, so call sites
that go through `app._get_active_transcriber()` are unaffected.



## PERF-MIC-001: OS-event-driven microphone cache invalidation

The microphone device list (`Recorder._device_list_cache`) was
previously refreshed only by a 30-second TTL in
`Recorder._refresh_device_list()`. When a user plugged or unplugged
a USB/BT microphone mid-session, the UI could show stale devices
for up to 30 seconds.

PERF-MIC-001 adds a best-effort OS-event watcher that invalidates
the cache **instantly** when the OS reports a device change. The
30-second TTL polling remains as a fallback for platforms where the
watcher can't start (macOS) or for the case where the watcher
thread crashes.

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Recorder.__init__  (recording.py)                           │
│    ├─ _device_list_cache / _device_list_cache_time           │
│    ├─ _device_list_cache_ttl = 30.0   ← fallback             │
│    └─ MicrophoneDeviceWatcher(on_change=_invalidate_device_  │
│                               cache).start()                 │
└─────────────────────┬────────────────────────────────────────┘
                      │ daemon thread
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  MicrophoneDeviceWatcher  (microphone_watcher.py)            │
│                                                              │
│  ┌─────────────────────┐  ┌────────────────────────────────┐ │
│  │  Windows            │  │  Linux                         │ │
│  │  _run_windows()     │  │  _run_linux()                  │ │
│  │                     │  │                                │ │
│  │  ctypes hidden      │  │  polls /dev/snd every          │ │
│  │  top-level window   │  │  1s via os.listdir + frozenset │ │
│  │  receives           │  │  comparison                    │ │
│  │  WM_DEVICECHANGE    │  │                                │ │
│  │  (0x0219)           │  │  no extra deps (no pyinotify)  │ │
│  │  PeekMessage pump   │  │                                │ │
│  │  at 10Hz            │  │                                │ │
│  └──────────┬──────────┘  └───────────────┬────────────────┘ │
│             │ WM_DEVICECHANGE              │ entries changed  │
│             ▼                              ▼                  │
│           _invoke_callback() → self._on_change()             │
│           (= Recorder._invalidate_device_cache)              │
└──────────────────────────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│  Recorder._invalidate_device_cache()                         │
│    self._device_list_cache = None                            │
│    self._device_list_cache_time = 0.0                        │
│    → next _refresh_device_list() call re-queries PortAudio   │
└──────────────────────────────────────────────────────────────┘
```

### Platform support

| Platform | Mechanism                              | Status      |
|----------|----------------------------------------|-------------|
| Windows  | `WM_DEVICECHANGE` via hidden window    | Implemented |
| Linux    | `/dev/snd` directory polling (1s)      | Implemented |
| macOS    | CoreAudio device-change callback       | Not implemented (TTL fallback) |

### Why both watcher + TTL?

The watcher is **best-effort**:

- If the platform is unsupported (macOS), `start()` is a no-op.
- If the watcher thread crashes, `_run()` catches the exception,
  logs a warning, and the thread exits. The 30s TTL cache still
  refreshes the list.
- If the invalidation callback raises, `_invoke_callback()` catches
  it and logs a warning — the thread continues running so the next
  device change still triggers an invalidation attempt.

This means a watcher failure can never break the recorder. The
30s TTL is the ultimate backstop.

### Thread safety

`_invalidate_device_cache()` performs two simple attribute writes
(`_device_list_cache = None`, `_device_list_cache_time = 0.0`).
These are atomic under Python's GIL. A concurrent reader in
`_refresh_device_list()` may see either the old or new value — both
are correct (the reader either returns the stale cache for one more
call, or re-queries immediately). No lock is needed.

### Lifecycle

- **Created** in `Recorder.__init__` (lazy import of
  `MicrophoneDeviceWatcher`).
- **Started** immediately after creation.
- **Stopped** by `Recorder.shutdown_mic_watcher()`, which is called
  from `VoiceTyperApp.quit_app()` during shutdown and defensively
  from `Recorder.__del__()`.
- The watcher thread is a daemon, so it will not block process exit
  even if `stop()` is never called.

### Testing

The watcher is tested in `tests/test_microphone_watcher.py`:

- `test_watcher_calls_callback_on_linux_dev_snd_change` — mocks
  `os.listdir` to simulate a `/dev/snd` change and verifies the
  callback fires.
- `test_watcher_does_not_crash_when_dev_snd_missing` — verifies
  graceful degradation when `/dev/snd` doesn't exist.
- `test_watcher_stop_joins_thread` — verifies `stop()` joins the
  thread.
- `test_watcher_skips_unsupported_platform` — verifies macOS doesn't
  spawn a thread.
- `test_watcher_logs_warning_on_callback_exception` — verifies a
  raising callback doesn't kill the thread.
- `test_watcher_logs_warning_when_run_method_crashes` — verifies
  `_run()` catches platform-runner exceptions.
- `test_recorder_*` — verify `Recorder` creates, starts, and stops
  the watcher correctly, and survives watcher import failure.

The Windows `WM_DEVICECHANGE` path is not mocked in tests (it
requires a real Windows message pump). It's exercised only on
Windows CI runners. The Linux path is fully tested via `os.listdir`
mocking.

## ARCH-REFAC-004: Dependency Injection Boundary

Round 2 introduced a lightweight dependency-injection (DI) seam so
`IPCServer` can be constructed without a real `VoiceTyperApp`, while
preserving 100% backward compatibility with all existing call sites
and tests.

### Motivation

Before this change, `IPCServer.__init__(app)` took a concrete
`VoiceTyperApp` and immediately constructed
`VoiceTyperService(app)`. This tight coupling forced every test that
exercised the IPC layer to either:

1. Spin up a real `VoiceTyperApp` (heavy — imports `pystray`,
   `sounddevice`, `faster_whisper`, etc.), or
2. Pass a `MagicMock` app and let the server construct a real
   `VoiceTyperService` over it — meaning service-layer bugs surfaced
   as IPC test failures, and tests could not isolate the IPC dispatch
   path from the service implementation.

The DI seam lets a test substitute a fake service for the real one,
exercising the IPC dispatch layer (the `_handle_*` mixins, `_dispatch`,
`_send`) in isolation.

### Approach: Protocol-based DI with backward-compatible constructor

Two `typing.Protocol` classes are defined in
`voice_typer/server/providers.py`:

- **`AppProtocol`** — describes the surface that `IPCServer` and its
  handler mixins actually need from `app`. Members include the public
  domain objects (`config`, `history_db`, `models`, `recording`,
  `hotkeys`, `recorder`, `tray`) and the private attributes the
  handlers / IPC server reach into (`_audio_processor`,
  `_volume_ducker`, `_ipc_server`, `_config_mutation_lock`,
  `_shutting_down`), plus the methods the service layer delegates to
  the app (`change_model`, `toggle_dictation`, `undo_last`,
  `repaste_last`, `restart_app`, `quit_app`, `start`).

- **`ServiceProtocol`** — describes the surface that the IPC handler
  mixins call on `self.service`. This is the full
  `VoiceTyperService` public method surface (status, dictation,
  config, history, microphone, models, vocabulary, templates,
  onboarding, system).

Both protocols are `@runtime_checkable` and use `typing.Any` for
member types so the protocol module does not import every concrete
dependency (avoiding import cycles and a heavy import surface). Test
doubles (`MagicMock`, custom fakes) trivially satisfy the protocols
via structural typing — no inheritance required.

### Constructor change

`IPCServer.__init__` now accepts an optional `service` parameter:

```python
def __init__(self, app, service: Optional[Any] = None) -> None:
    self.app = app
    if service is not None:
        self.service = service           # DI mode: caller-provided fake
    else:
        from voice_typer.server.service import VoiceTyperService
        self.service = VoiceTyperService(app)  # Backward compat
```

- `IPCServer(app)` — **backward-compatible path**, used by all 20+
  existing test files and the production entry point. Constructs a
  real `VoiceTyperService(app)` exactly as before. No call site needs
  to change.
- `IPCServer(app, service=fake_service)` — **DI path**, used by tests
  that want to exercise the IPC dispatch layer in isolation. The
  injected `service` is stored verbatim on `self.service`; no
  `VoiceTyperService` is constructed.

`app` is typed as `Any` (not `AppProtocol`) so existing
`MagicMock`-based test fixtures keep working without importing the
protocol module. `AppProtocol` is a structural type — a `MagicMock`
satisfies it — but annotating the parameter with `AppProtocol` would
force every test file that constructs `IPCServer(app)` to import the
protocol, which is an unnecessary migration burden.

### Composition root: `providers.build_ipc_server(app)`

`voice_typer/server/providers.py` exports a factory function:

```python
def build_ipc_server(app: AppProtocol) -> IPCServer:
    from voice_typer.server.ipc_server import IPCServer
    return IPCServer(app)
```

This is the **canonical composition root** for production code. The
production entry point (`voice_typer/server/ipc_server.py:main`) now
calls `build_ipc_server(app)` instead of `IPCServer(app)` directly.

Behavior today is identical to `IPCServer(app)`: a real
`VoiceTyperService` is constructed over `app`. The factory exists so
that future wiring changes (logging, metrics, feature flags, an
alternate service implementation) live in one place rather than being
threaded through every call site.

Tests that want to inject a fake service should call
`IPCServer(app, service=fake)` directly rather than this factory —
`build_ipc_server` is the production path and intentionally does not
accept a `service` parameter.

### Test helpers: `tests/fixtures/ipc_test_helpers.py`

A new test-fixture module provides ready-made fakes that satisfy the
protocols:

- `make_fake_app()` — returns a `MagicMock` configured with every
  attribute `AppProtocol` requires (config, history_db, models,
  recording, hotkeys, recorder, tray, _audio_processor,
  _volume_ducker, _ipc_server=None, _config_mutation_lock=RLock(),
  _shutting_down=False).
- `make_fake_service()` — returns a `MagicMock` that satisfies
  `ServiceProtocol`, with sensible default return values for the
  most-called methods.
- `make_ipc_server_with_fakes()` — returns
  `(server, fake_app, fake_service)` for tests that want to exercise
  IPCServer in isolation.

Usage:

```python
from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

def test_something():
    server, fake_app, fake_service = make_ipc_server_with_fakes()
    fake_service.get_status.return_value = {"status": "recording", ...}
    result = server._dispatch({"type": "get_status"})
    fake_service.get_status.assert_called_once()
```

### Protocol drift detection

`tests/test_di_providers.py` includes a regression test that walks
the AST of every handler under `voice_typer/server/handlers/` (and
`ipc_server.py` itself) and collects every `self.app.<name>` and
`self.service.<name>` access. It then verifies that:

- Every `self.app.X` access is declared on `AppProtocol` (either as
  an annotated data attribute or as a method).
- Every public `self.service.X` access (excluding private `_app`)
  is declared on `ServiceProtocol`.

If a future handler starts reading `self.app.new_field` without
`new_field` being declared on `AppProtocol`, the introspection test
fails — forcing an explicit decision about whether to widen the
protocol (accepted surface growth) or refactor the handler to go
through the service layer (preferred — the protocol surface should
stay small).

### Migration guide

| Use case                                       | Pattern                                           |
|------------------------------------------------|---------------------------------------------------|
| Production (Electron subprocess)               | `build_ipc_server(app)` (via `ipc_server.main()`) |
| Existing test with hand-rolled MockApp         | `IPCServer(app)` — unchanged                      |
| New test exercising IPC dispatch in isolation  | `make_ipc_server_with_fakes()`                    |
| New test with a custom fake service            | `IPCServer(app, service=my_fake)`                 |

**No existing call site needs to change.** The DI seam is purely
additive.

