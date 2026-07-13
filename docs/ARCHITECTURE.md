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

