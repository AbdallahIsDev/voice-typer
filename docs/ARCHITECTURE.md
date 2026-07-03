# Voice Typer — Architecture

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

During the Round 9 extraction (Task #2), `VoiceTyperApp` (in
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

