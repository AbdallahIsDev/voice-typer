# Changelog

All notable changes to Voice Typer are documented here.
This project follows [Keep a Changelog](https://keepachangelog.com/) format.

## User-Facing Changes

Changes that affect end users (new features, bug fixes, UX improvements).

### 1.0.0 (2026-06-21)

- **Dual ASR backends**: Whisper (faster-whisper, default) and optional Qwen3-ASR-0.6B
- **Parakeet backend** (optional, NVIDIA Parakeet TDT v3) — auto-downloads from HuggingFace on first use
- **Electron + React UI** with tray icon for background operation (no Flet)
- **Hidden streaming transcription** with overlapping audio windows and batch fallback
- **Text cleanup pipeline**: duplicate removal, hallucination cleanup, misspelling correction, phrase substitution, sentence capitalization
- **System tray icon** with minimal menu: Toggle Dictation, Open App, Models submenu, Restart, Quit
- **Global hotkey** support: `<ctrl>+<alt>+f2`, `<ctrl>+1` through `<ctrl>+5`, and F1-F12
- **Auto-paste** via Ctrl+V (terminal detection was removed; the docs/README claim has been corrected)
- **Microphone fallback chain**: same-name candidates across host APIs, ranked by reliability
- **4-level GPU→CPU fallback** for model loading
- **External corrections JSON** override file for custom misspelling/phrase corrections
- **Push-to-talk mode** (configured via Settings; release currently issues `pass` — see FEATURES.md)
- **ESC cancel** at any stage of dictation
- **Repaste last transcription** hotkey
- **Auto-punctuation** (optional, runs after template matching)
- **LLM text polishing** with 4 presets (professional, casual, email, code) — requires explicit user consent
- **Crash recovery**: stores last 10 transcriptions, prompts on restart if unpasted
- **History database** with search, favorites, and retention policy
- **Waveform bubble** overlay (optional) with real-time audio level visualization
- **Onboarding flow** — first-run wizard rendered by the React UI
- **Theme support**: system/light/dark
- **High-contrast mode** and adjustable text size (accessibility)
- **Fast startup** via prewarm (keeps model weights in OS file cache)

### Security & Privacy Improvements (rounds 1-7)

- **API keys redacted** in `get_config` IPC responses — no longer echoed in cleartext
- **LLM polish requires explicit consent** — separate `llm_polish_consent` flag
- **Cloud/LLM URL allowlist** — prevents endpoint-swap attacks from exfiltrating data
- **File permissions hardened** — config, history DB, and recovery files are 0o600 on POSIX
- **IPC session token auth** — prevents unauthorized local processes from sending commands
- **CSP headers** added to both Electron HTMLs
- **CSV export formula-injection defense** — cells starting with `=`, `+`, `-`, `@` are escaped
- **DevTools disabled in production builds**

### Reliability Improvements (rounds 1-7)

- **Clean shutdown** — replaced `os._exit(0)` with `sys.exit(0)` so Python cleanup runs (releases mutex, closes mic, unregisters hotkeys)
- **All hotkey backends stopped on quit/restart** — no more "hotkey busy" after restart
- **Cloud API timeouts** — 30s timeout on all HTTP requests (was unbounded)
- **Crash recovery async writes** — background thread prevents main-thread blocking
- **IPC rate limiting** — 200 burst / 60 sustained msg/s per connection
- **Removed stale Python reaper** — no more `taskkill /T /F` killing legitimate autostart sessions

### Performance Improvements (rounds 1-7)

- **Eager scipy preload** — first recording no longer blocks 200-800ms on import
- **SQLite 20 MB cache** — history reads stay in memory
- **Bubble level pushes off audio thread** — background queue + 30 Hz throttle prevents xruns
- **Recorder snapshot O(1)** — `itertools.islice` replaces full-deque copy
- **Xrun log rate-limited** — was 16 disk writes/sec, now once per 5 seconds

### UX Improvements (rounds 1-7)

- **Hotkey conflict notification** names the hotkey and suggests rebinding
- **"View Logs" button** actually opens the log folder (was a fake handler)
- **Settings inputs debounced** — typing "gpt-4o-mini" fires 1 IPC call, not 11
- **Label associations** on all settings inputs (screen reader support)
- **"Reset to Defaults"** fetches from backend (no silent drift from hardcoded defaults)
- **Honest "not implemented" messages** on fake buttons (model download, benchmark, mic test)

---

## Developer-Facing Changes

Changes that affect contributors (architecture, dead code removal, test coverage, docs).

### Architecture

- **`set_config` allowlist** — 53 user-tunable fields with type/range/enum/URL validation; trusted-path fields (`corrections_path`, `qwen_model_path`, etc.) excluded
- **Corrections deduplication** — `clean_transcribed_text(skip_corrections=True)` when VocabularyManager is enabled; single source of truth
- **Generic ASR engine init** — `_init_asr_engine()` dispatcher consolidates qwen/parakeet init
- **psutil replaces wmic** — `_another_voice_typer_alive` deleted (zero decision power); `killStalePython` deleted (mutex handles single-instance)
- **Corrections load errors surfaced** — `configure_corrections()` returns error string; tray notification on malformed JSON

### Dead Code Removal

- Removed `pip_install()` and `download_weights()` from `asr_setup.py` (archived to `archive/`)
- Removed dead shadcn/ui components (`dialog.tsx`, `sheet.tsx`, `popover.tsx`)
- Removed `StatusBar.tsx` (imported but rendered as a comment)
- Removed 6 dead `TrayController` protocol methods (`toggle_autostart`, `create_desktop_shortcut`, `set_notifications`, `set_silence_warning_seconds`, `set_silence_auto_stop_seconds`, `set_max_recording_seconds`)
- Removed unused `AudioQualityAnalyzer` instantiation
- Replaced fake `setTimeout` buttons with honest "not implemented" messages

### Testing

- **1072 tests passing** (up from ~400 at project start), 9 skipped (platform-specific)
- Test files cover every module: round8/9/10/11 E2E suites, per-module unit tests, regression tests for SEC/RELIABILITY/ERR/ARCH items. See `pytest --collect-only -q | wc -l` for the current count.
- New test files: `test_secrets.py`, `test_round11_regression.py`
- New test classes: `TestDispatchSetConfigAllowlist`, `TestGetConfigRedactsSecrets`, `TestSec006TrustedPathFieldsBlockedStandalone`, `TestSec008PendingTcpCap`, `TestSec010HistoryLimitBounding`, `TestGetDefaultsIpc`, `TestSec018TcpAuth`, `TestArch004CorrectionsLoadError`, `TestRateLimiter`, `TestWrapSystemExitHandling`, `TestQuitAppCleanShutdown`, `TestRestartAppCleanShutdown`, `TestPushToTalkOnRelease`, `TestCloudEngineUrlAllowlist`, `TestCloudEngineKeyRedaction`, `TestDeepgramUrlParameterInjection`, `TestSec007ConfigFilePermissions`, `TestCrashRecoveryAsyncWrites`, `TestCrashRecoveryIntegration`, `TestSetConfigRejectsSensitiveAttrs`, `TestSearchHistoryEdgeCases`, `TestCloudEngineUlopenTimeout`, `TestRestartAppStopsBackends`, `TestXrunThresholdCounter`, `TestResampleError`, `TestWatchdogForceRecover`, `TestPendingModelChange`, `TestFriendlyTranscriptionError`, `TestStoreResultFailurePromotion`, `TestParakeetBackendError`, `TestQwenFallback`, `TestUnknownIPCCommandCode`, `TestVKMapInitLockGuarded`, `TestPendingTimersLockGuarded`, `TestAudioCallbackPreStartGuard`, `TestPhrasePatternCache`, `TestResampleCacheInvalidation`, `TestVocabularySaveRetry`

### Documentation

- `docs/ARCHITECTURE.md` — ASCII diagram + security boundary table
- `docs/PLATFORM_STATUS.md` — feature × OS matrix
- `archive/deleted_files.txt` — tracks files removed for manual cleanup

### Build

- **npm pins fixed** — `typescript@^5.6.0`, `vite@^6.0.0`, `@types/node@^22.0.0` (were non-existent versions)
- **gitignore** — `out/`, `dist/`, `*.tsbuildinfo` excluded from commits
