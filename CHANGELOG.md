# Changelog

All notable changes to Voice Typer are documented here.
This project follows [Keep a Changelog](https://keepachangelog.com/) format.

## [Unreleased] - 2026-06-30

### Added — Zero-Command Hotkey Architecture (ADR 0008)

- **Gap 1: Cross-platform CI build pipeline** — GitHub Actions `build.yml` now
  compiles all 3 native binaries (Swift on macOS, C on Windows, C on Linux)
  on their native platforms and bundles them into per-platform installers
  (.exe / .dmg / .deb / .rpm / .AppImage).
  
- **Gap 2: macOS Accessibility onboarding** — When the native binary detects
  missing Accessibility permission, Voice Typer shows a tray notification and
  deep-links to System Settings → Privacy & Security → Accessibility. A 60s
  retry timer auto-restarts the native backend once permission is granted.

- **Gap 3: Linux zero-command setup** — `.deb`/`.rpm` packages now include
  postinst scripts that automatically install the udev rule, add the user to
  the `input` group, and configure Caps Lock neutralization. AppImage users
  get a `pkexec` GUI prompt on first launch. The user only types their sudo
  password once (prompted by the OS, not by Voice Typer).

- **Gap 4: Runtime fallback chain** — If the native binary dies permanently
  (antivirus, OOM killer, code-signing expiry), Voice Typer transparently
  swaps to the legacy backend (pynput/polling) with the same hotkey. A 5-min
  retry timer auto-recovers the native backend when it comes back.

### New files
- `voice_typer/server/permissions.py` — OS permission detection + onboarding
- `scripts/linux/install_permissions.py` — Linux udev/group/Caps Lock installer
- `scripts/linux/uninstall_permissions.py` — Linux uninstaller
- `scripts/linux/99-voice-typer.rules` — udev rule
- `scripts/linux/00-voice-typer-capslock.conf` — XKB Caps Lock config
- `scripts/linux/postinst` / `prerm` — Debian package scripts
- `scripts/linux/postinst.rpm` / `prerm.rpm` — RPM package scripts
- `scripts/linux/voice-typer.polkit` — polkit policy for pkexec
- `tests/test_runtime_fallback.py` — 28 tests for Gap 4
- `tests/test_permissions.py` — 31 tests for Gap 2 + Gap 3

### Modified files
- `voice_typer/server/native_hotkeys.py` — added _on_error_callback, _on_permanent_failure_callback
- `voice_typer/server/hotkeys.py` — rewrote _NativeBackendAdapter as 4-state machine
- `voice_typer/server/hotkey_dispatcher.py` — wires tray reference to adapter
- `voice_typer/client/electron-builder.yml` — added rpm target + afterInstall/afterRemove hooks
- `scripts/build/voice-typer.spec` — bundles Linux scripts + permissions module
- `.github/workflows/build.yml` — added build-native matrix + build-macos + build-linux jobs

## [Unreleased - earlier native-hotkey work] - 2026-06-30

The entries below describe the earlier NATIVE-001 work that the
Zero-Command Hotkey Architecture (ADR 0008) section above builds on.
Both roll up into the same unreleased release; the section heading is
distinct to avoid duplicating the `## [Unreleased]` header.

### Added
- Cross-platform native hotkey architecture (NATIVE-001)
  - macOS: native Swift binary supports the Fn key via NSEvent.modifierFlags.function
  - Windows: native C binary uses WH_KEYBOARD_LL (lower CPU, supports key suppression)
  - Linux: native C binary uses evdev (/dev/input/event*) — works on both X11 and Wayland
- Platform-aware default hotkey: Fn (macOS), Caps Lock (Windows/Linux)
- Settings UI: dropdown trimmed to universally-present keys (Caps Lock, Alt, Ctrl, Shift, Win/Cmd, Fn on macOS)
- Modifier-only hotkeys (Alt, Ctrl, Shift, Win/Cmd, Fn) now supported as single-key triggers
- FN key support on macOS (firmware-only on Windows/Linux — rejected at validation)

### Changed
- create_hotkey_backend() now prefers native backends; falls back to legacy PynputHotkey/WindowsNativeHotkey/WaylandHotkey when native binary is missing
- Config default hotkey changed from `<f2>` to platform-aware default via _default_hotkey_for_platform()
- HotkeyPicker now accepts modifier-only releases as single-key hotkeys (e.g. press Alt alone, release → <alt>)
- PyInstaller spec bundles native binaries from voice_typer/server/native/

### Migration notes
- Existing users with `<f2>` in their config will keep `<f2>` (no forced migration)
- New installs get the platform-aware default
- To build native binaries: `bash scripts/build/compile_native.sh` (or .ps1 on Windows)
- macOS users granting Accessibility for the first time may need to re-grant after macOS updates
- Linux users may need `sudo usermod -aG input $USER` then log out and back in

## User-Facing Changes

Changes that affect end users (new features, bug fixes, UX improvements).

### 1.0.0 (2026-06-21)

- **Dual ASR backends**: Whisper (faster-whisper, default) and optional Qwen3-ASR-0.6B
- **Parakeet backend** (optional, NVIDIA Parakeet TDT v3) — auto-downloads from HuggingFace on first use
- **Electron + React UI** with tray icon for background operation
- **Hidden streaming transcription** with overlapping audio windows and batch fallback
- **Text cleanup pipeline**: duplicate removal, hallucination cleanup, misspelling correction, phrase substitution, sentence capitalization
- **System tray icon** with minimal menu: Toggle Dictation, Open App, Models submenu, Restart, Quit
- **Global hotkey** support: `<ctrl>+<alt>+f2`, `<ctrl>+1` through `<ctrl>+5`, and F1-F12
- **Auto-paste** via Ctrl+V (terminal detection was removed; the docs/README claim has been corrected)
- **Microphone fallback chain**: same-name candidates across host APIs, ranked by reliability
- **4-level GPU→CPU fallback** for model loading
- **External corrections JSON** override file for custom misspelling/phrase corrections
- **Push-to-talk mode** (configured via Settings; press-and-hold starts recording, release stops it — see FEATURES.md)
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
- **Honest "not implemented" messages** on fake buttons (model download, benchmark)
  - Note: the microphone test is **real** — it opens a live `sounddevice.InputStream` via `level_monitor.start_test_recording()` and returns captured audio. Only model download progress and the model benchmark are simulated.

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
- (Correction to an earlier draft of this changelog: `AudioQualityAnalyzer` was **not** removed. It is still instantiated at `app.py:208` and used by `recording_controller.py:403` for per-chunk quality analysis. Only the user-facing tray notification that surfaced its report was suppressed — default `audio_quality_warnings=False`, with an early-return in `app.py:_finalize_audio_quality_report` that prevents the notification from ever firing.)
- Replaced fake `setTimeout` buttons with honest "not implemented" messages

### Testing

- **2822+ tests across 107 files** (up from ~400 at project start), 9 skipped (platform-specific). Run `pytest --co -q | tail -1` for the current count.
- Test files cover every module: round8/9/10/11/12/13 E2E suites, per-module unit tests, regression tests for SEC/RELIABILITY/ERR/ARCH/IPC items. See `pytest --collect-only -q | wc -l` for the current count.
- New test files: `test_secrets.py`, and 8 domain-named files (`test_consent_and_privacy.py`, `test_platform_and_config.py`, `test_hotkeys.py`, `test_ux_components.py`, `test_electron_ipc_and_build.py`, `test_notifications.py`, `test_recording_and_audio.py`, `test_history_and_models.py`) that replaced 5 round-numbered test files
- New test classes: `TestDispatchSetConfigAllowlist`, `TestGetConfigRedactsSecrets`, `TestSec006TrustedPathFieldsBlockedStandalone`, `TestSec008PendingTcpCap`, `TestSec010HistoryLimitBounding`, `TestGetDefaultsIpc`, `TestSec018TcpAuth`, `TestArch004CorrectionsLoadError`, `TestRateLimiter`, `TestWrapSystemExitHandling`, `TestQuitAppCleanShutdown`, `TestRestartAppCleanShutdown`, `TestPushToTalkOnRelease`, `TestCloudEngineUrlAllowlist`, `TestCloudEngineKeyRedaction`, `TestDeepgramUrlParameterInjection`, `TestSec007ConfigFilePermissions`, `TestCrashRecoveryAsyncWrites`, `TestCrashRecoveryIntegration`, `TestSetConfigRejectsSensitiveAttrs`, `TestSearchHistoryEdgeCases`, `TestCloudEngineUlopenTimeout`, `TestRestartAppStopsBackends`, `TestXrunThresholdCounter`, `TestResampleError`, `TestWatchdogForceRecover`, `TestPendingModelChange`, `TestFriendlyTranscriptionError`, `TestStoreResultFailurePromotion`, `TestParakeetBackendError`, `TestQwenFallback`, `TestUnknownIPCCommandCode`, `TestVKMapInitLockGuarded`, `TestPendingTimersLockGuarded`, `TestAudioCallbackPreStartGuard`, `TestPhrasePatternCache`, `TestResampleCacheInvalidation`, `TestVocabularySaveRetry`

### Documentation

- `docs/ARCHITECTURE.md` — ASCII diagram + security boundary table
- `docs/PLATFORM_STATUS.md` — feature × OS matrix
- `archive/deleted_files.txt` — tracks files removed for manual cleanup

### Build

- **npm pins fixed** — `typescript@^5.6.0`, `vite@^6.0.0`, `@types/node@^22.0.0` (were non-existent versions)
- **gitignore** — `out/`, `dist/`, `*.tsbuildinfo` excluded from commits
