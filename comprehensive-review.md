# Comprehensive Review — Open Findings (verified-fixed items removed)

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.

---

## Architecture (all Pending)


#### ARCH-1 — `hotkeys.py` (2,938 lines): god-file with 5 backends
- **Severity**: High
- **Status**: Pending
- **Description**: 5 unrelated hotkey-backend classes in one file (`HotkeyBackend` ABC, `PynputHotkey`, `WindowsNativeHotkey` ~1,420 LOC itself a god-class, `_NativeBackendAdapter`, `WaylandHotkey`) plus 7 module-level helpers.
- **Root cause**: Hotkey backends grouped by interface rather than platform responsibility. Windows-native path accumulated IME, low-level hooks, modifier-only polling, Caps Lock suppression in place.
- **Recommended fix**: Convert to `voice_typer/server/hotkeys/` package: `base.py`, `pynput_backend.py`, `windows_native.py`, `native_adapter.py`, `wayland.py`, `_parse.py`. ~21 test files import these names — `__init__.py` re-exports preserve them. ~1-day effort.

#### ARCH-2 — `recording.py` (3,213 lines): single `Recorder` class with 47 methods
- **Severity**: High
- **Status**: Pending
- **Description**: `Recorder` class spans ~2,750 LOC bundling 7 responsibilities (device management, audio callback pipeline, event worker, VAD shims, lifecycle, resampling, buffer management).
- **Root cause**: Real-time audio recording naturally couples many concerns; file accumulated features without extraction.
- **Recommended fix**: Split to `voice_typer/server/recording/` package: `recorder.py` (lifecycle), `device_manager.py`, `audio_pipeline.py`, `buffer.py`, `resampling.py`, `vad_shims.py`. Requires porting 22 `inspect.getsource` tests in `tests/regressions/audio_test.py` to behavioral tests. ~3-day effort.

#### ARCH-3 — `ipc_server.py` (2,297 lines): transport + lifecycle + dispatcher + entry point
- **Severity**: Medium
- **Status**: Pending
- **Description**: `IPCServer` class 1,500 LOC plus 200 LOC module helpers and 250 LOC `main()`. 5 concerns: transport, dispatch, push-event publishing, heartbeat, CLI entry.
- **Recommended fix**: Split to `voice_typer/server/ipc/` package: `server.py`, `tcp_transport.py`, `heartbeat.py`, `helpers.py`, `main.py`. ~2-day effort.

#### ARCH-4 — `prewarm.py` (2,162 lines): 6 functional groups in one file
- **Severity**: Medium
- **Status**: Pending
- **Description**: 39 module-level functions spanning setup/logging, cache discovery, warming pipeline, process tracking, completion events, CLI.
- **Recommended fix**: Split to `voice_typer/server/prewarm/` package: `pipeline.py`, `process_tracker.py`, `cache_probe.py`, `completion_events.py`, `cli.py`. ~1-day effort.

#### ARCH-5 — `service.py` (2,096 lines): 70-method facade
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperService` exposes 70 delegating methods. Class IS a facade (mostly delegation), so cost is readability, not coupling.
- **Recommended fix**: Either keep as single facade OR split per-domain into mixins (lower priority than ARCH-1/2/3).

#### ARCH-6 — `server_platform.py` (1,169 lines): "everything platform" grab-bag with duplicates
- **Severity**: Medium
- **Status**: Pending
- **Description**: Bundles microphone listing, autostart, desktop shortcut, volume backend factory, remote-session detection, AND duplicate `is_windows/is_macos/is_linux` (duplicates of `platform_utils.py`).
- **Recommended fix**: Split into `microphone_list.py`, `autostart.py` (consolidate with `autostart_launcher.py`), `desktop_shortcut.py`. Delete duplicate `is_windows/is_macos/is_linux`. Keep app.py re-exports as test seams.

#### ARCH-7 — `native_hotkeys.py` (1,188 lines): 4 backends + recorder in one file
- **Severity**: Medium
- **Status**: Pending
- **Description**: Mirrors ARCH-1 problem — 4 backend classes (`MacNativeHotkey`, `WindowsHookHotkey`, `LinuxEvdevHotkey`) + `NativeHotkeyRecorder` + helpers.
- **Recommended fix**: Convert to `voice_typer/server/native_hotkeys/` package: `base.py`, `macos.py`, `windows.py`, `linux.py`, `recorder.py`. ~0.5-day effort.

#### ARCH-8 — `_open_config_file` extraction blocker (source-string tests)
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperApp._open_config_file` (104 LOC) is the only remaining "fat" method on `VoiceTyperApp`. Extraction blocked by 6 `inspect.getsource` tests in `tests/test_b4_config_editor_lock.py` and `tests/regressions/concurrency_test.py` that pin literal source text.
- **Recommended fix**: Port these 6 source-string tests to behavioral tests (RW-8 pattern), then extract `ConfigEditorLauncher`. ~1-day effort.

#### ARCH-9 — `app.py` test-seam re-exports (173 monkeypatch sites)
- **Severity**: Low
- **Status**: Pending
- **Description**: `app.py` re-exports 20 symbols from sibling modules so tests can monkeypatch `voice_typer.server.app.X`. 173 monkeypatch sites depend on these re-exports.
- **Recommended fix**: Migrate monkeypatch sites to canonical paths (`voice_typer.server.server_platform.is_autostart_enabled` instead of `voice_typer.server.app.is_autostart_enabled`), then delete re-export blocks. Mechanical refactor touching many files.

#### ARCH-10 — Circular import between `ipc_server.py` and `handlers/*.py`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 handler mixins import `log` and `_validate_dict_payload` from `ipc_server.py`; `ipc_server.py` imports the mixins back. Cycle is broken by ordering (helpers defined before handler imports).
- **Rationale for Won't Fix**: Pattern is stable and documented. Moving helpers to `ipc_helpers.py` would be cleaner but provides no runtime benefit.

#### ARCH-11 — `clipboard.py` (1,369 lines): UIA focus/pwd detection tangled with clipboard I/O
- **Severity**: Low
- **Status**: Pending
- **Description**: ~330 LOC of Win32 UI Automation focus/password-field detection mixed with clipboard I/O helpers.
- **Recommended fix**: Extract to `voice_typer/server/clipboard_target_safety.py`. ~0.5-day effort.

#### ARCH-12 — 162 `inspect.getsource` source-string tests across the codebase
- **Severity**: Low
- **Status**: Pending (ongoing)
- **Description**: 162 source-string tests pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Make refactoring expensive.
- **Recommended fix**: Adopt project rule — "no new `inspect.getsource` tests; port existing ones when touching the code they pin." Chip away over time.

#### ARCH-13 — TYPE_CHECKING back-references from controllers to `VoiceTyperApp`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 modules use `if TYPE_CHECKING:` to import `VoiceTyperApp` for type annotations. `VoiceTyperApp` IS the service locator.
- **Rationale for Won't Fix**: Runtime cycle is already broken via lazy imports. Annotating against `AppProtocol` (already defined in `providers.py`) would be cleaner but provides no runtime benefit.

#### ARCH-14 — `config_validators.py::_validate_hotkey` is 336 LOC
- **Severity**: Low
- **Status**: Pending
- **Description**: One function spanning 7+ validation stages.
- **Recommended fix**: Extract each stage to a small `_check_*` helper. Cosmetic; low ROI.
- **ARCH-15** — `service.py` (2116 LOC): 50-method god facade spanning 8 domains. **Status**: Pending. **Fix**: Split into `service/{history,model,onboarding,microphone_test,vocabulary,template,status,dictation}.py`.

- **ARCH-16** — `recording.py` (3224 LOC): single `Recorder` class with 6 concerns (VAD, device, resampler, buffer, workers, xrun). **Status**: Pending. **Fix**: Split into `recorder/{core,vad,device,resampler,buffer,workers}.py`.

- **ARCH-17** — `hotkeys.py` (2938 LOC): 5 backend classes in one file. **Status**: Pending. **Fix**: Split per backend into `hotkeys/` package.

- **ARCH-18** — `ipc_server.py` (2297 LOC): handlers still inline (dispatch already extracted). **Status**: Pending. **Fix**: Extract `_handle_*` to per-domain mixins.

- **ARCH-19** — `prewarm.py` (2162 LOC): 7 sections in one file. **Status**: Pending. **Fix**: Split along existing section comments.

- **ARCH-20** — `Models.tsx` (1682 LOC): single-file page with 9 helpers + inline sections. **Status**: Pending. **Fix**: Extract utils + sub-components.


## Performance (all Pending)

#### ARCH-2 — `recording.py` (3,213 lines): single `Recorder` class with 47 methods
- **Severity**: High
- **Status**: Pending
- **Description**: `Recorder` class spans ~2,750 LOC bundling 7 responsibilities (device management, audio callback pipeline, event worker, VAD shims, lifecycle, resampling, buffer management).
- **Root cause**: Real-time audio recording naturally couples many concerns; file accumulated features without extraction.
- **Recommended fix**: Split to `voice_typer/server/recording/` package: `recorder.py` (lifecycle), `device_manager.py`, `audio_pipeline.py`, `buffer.py`, `resampling.py`, `vad_shims.py`. Requires porting 22 `inspect.getsource` tests in `tests/regressions/audio_test.py` to behavioral tests. ~3-day effort.

#### ARCH-3 — `ipc_server.py` (2,297 lines): transport + lifecycle + dispatcher + entry point
- **Severity**: Medium
- **Status**: Pending
- **Description**: `IPCServer` class 1,500 LOC plus 200 LOC module helpers and 250 LOC `main()`. 5 concerns: transport, dispatch, push-event publishing, heartbeat, CLI entry.
- **Recommended fix**: Split to `voice_typer/server/ipc/` package: `server.py`, `tcp_transport.py`, `heartbeat.py`, `helpers.py`, `main.py`. ~2-day effort.

#### ARCH-5 — `service.py` (2,096 lines): 70-method facade
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperService` exposes 70 delegating methods. Class IS a facade (mostly delegation), so cost is readability, not coupling.
- **Recommended fix**: Either keep as single facade OR split per-domain into mixins (lower priority than ARCH-1/2/3).

#### ARCH-10 — Circular import between `ipc_server.py` and `handlers/*.py`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 handler mixins import `log` and `_validate_dict_payload` from `ipc_server.py`; `ipc_server.py` imports the mixins back. Cycle is broken by ordering (helpers defined before handler imports).
- **Rationale for Won't Fix**: Pattern is stable and documented. Moving helpers to `ipc_helpers.py` would be cleaner but provides no runtime benefit.

- **PERF-15** — `waveform_bubble_wiring.py`: `getattr` with defaults for always-set attributes (micro). **Status**: Pending.

## Security (all Pending)

- **SEC-8** — TCP accept loop runs auth handler inline (soft DoS, 5s stall). **Status**: Pending.

- **SEC-9** — `redact_secret` regex gap for `-`-delimited tokens. **Status**: Pending (informational).

- **SEC-10** — PowerShell script generation only escapes `"` (defense-in-depth). **Status**: Pending.


## Cross-Platform (all Pending)
#### XPLAT-1 — `tauri.conf.json` missing `bundle.linux` section (CRITICAL)
- **Severity**: Critical
- **Status**: **Partial** ⚠️ (verifier: dangling `desktopTemplate` reference)
- **Description**: ADR-0020 §7 + §13.3 mandate `bundle.linux.deb.postInstall` and `preRemove` for `scripts/linux/postinst` and `prerm`. Without this, `apt install voice-typer.deb` won't run postinst → no udev rule, no `input` group, no Caps Lock config → native hotkey binary fails on `/dev/input/event*`.
- **Fix applied**: Added `bundle.linux.{deb,rpm}` section to `src-tauri/tauri.conf.json` with `depends`, `postInstall`, `preRemove`, and `desktopTemplate` per ADR spec. The block is valid JSON and the `postInstall`/`preRemove` paths (`scripts/linux/postinst`, `scripts/linux/prerm`, plus `.rpm` variants) all exist.
- **Verifier gap (2026-07-18)**: `"desktopTemplate": "voice-typer.desktop.template"` (line 72) points to a file that **does not exist anywhere in the repo** (confirmed via recursive search — zero `*.desktop*` files; ADR-0020 §13.3 references it too, so the ADR assumes it exists). Tauri `tauri build --bundles deb` will FAIL reading this template. **Not fully fixed** — create `src-tauri/voice-typer.desktop.template` (or remove the key) and build the deb on Linux to close. Tracked in TASKS.md.

#### XPLAT-2 — Rust `paste_text` doesn't handle Wayland
- **Severity**: High
- **Status**: Pending
- **Description**: `src-tauri/src/commands/sidecar_cmds.rs::paste_text` uses `enigo.text()` and `enigo.key(Control, v)` — both X11-only. No `wtype`/`ydotool` fallback in Rust.
- **Impact**: On Wayland Linux, transcription completes but text never gets typed into the foreground app.
- **Recommended fix**: Detect Wayland (`WAYLAND_DISPLAY` or `XDG_SESSION_TYPE=wayland`) and shell out to `wtype -d 50 -- "<text>"` (short) or `wtype -k ctrl+v` (long) on the Wayland branch.
- **Note**: Requires Wayland host validation.

#### XPLAT-4 — `tauri.conf.json` missing `bundle.windows` and `bundle.macOS` signing config
- **Severity**: High
- **Status**: Pending (CI handles it; local-build is the gap)
- **Description**: CI workflows run `signtool` / `codesign` + `notarytool` as post-build steps, but local `cargo tauri build` produces unsigned bundles.
- **Recommended fix**: Either populate `bundle.windows.signCommand` and `bundle.macOS.signingIdentity` + `entitlements`, OR document that signing is CI-only.

#### XPLAT-6 — `bubble_set_position` Rust command takes `i32` but renderer passes `"top" | "bottom"` strings
- **Severity**: High
- **Status**: Pending
- **Description**: `src-tauri/src/commands/bubble.rs::bubble_set_position(x: i32, y: i32)` expects integers, but renderer calls `window.bubble.setPosition("top")` / `setPosition("bottom")`. Serde deserialization fails on Tauri path.
- **Recommended fix**: Either widen Rust signature to `x: String` and parse server-side, or migrate renderer to send numeric `(x, y)`.

#### XPLAT-11 — Linux aarch64 native listener not built by CI
- **Severity**: Medium
- **Status**: Pending (ADR deferral)
- **Description**: `.github/workflows/tauri-linux-build.yml` only builds `linux-key-listener` for x86_64. `tauri.conf.json` lists it as required for ALL platforms.
- **Recommended fix**: Per-arch resource list, OR generate stub, OR document manual `compile_native.sh` requirement.
- **Note**: ADR-0020 explicitly defers aarch64 Linux to a follow-up.

#### XPLAT-12 — Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: Pending (host validation required)
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.

- **XPLAT-17** — Linux aarch64 CI job will fail at `cargo tauri build` (missing `linux-key-listener` resource). **Status**: Pending.

## UX (all Pending)

#### UX-1 — `undo_last` IPC command wired but unreachable from any UI
- **Severity**: High
- **Status**: Pending
- **Description**: Backend handler + i18n keys exist, but no component ever calls `call("undo_last")`. Feature is invisible.
- **Recommended fix**: Surface "Undo" button on Home's `lastText` preview block. Add tray menu item.

#### UX-2 — Tray menu missing Mic switcher
- **Severity**: High
- **Status**: Pending
- **Description**: Tray exposes only Open App, Toggle Dictation, Cancel Transcription, Models, Restart, Quit. No Mic submenu.
- **Recommended fix**: Add Mic submenu after "Models".

#### UX-3 — "Cancel Transcription" tray item confusingly named and always visible
- **Severity**: Medium
- **Status**: Pending
- **Description**: Reads like normal cancel but is actually force-recover escape hatch. Clutters menu when nothing is stuck.
- **Recommended fix**: Rename to "Cancel Stuck Transcription"; only show when `state == TRANSCRIBING`.

#### UX-4 — Onboarding wizard has no macOS Accessibility / Wayland `wtype` permission step
- **Severity**: High
- **Status**: Pending
- **Description**: First-run macOS user without Accessibility permission completes wizard, presses F2, nothing happens.
- **Recommended fix**: Insert platform-conditional step between Mic and Hotkey.

#### UX-5 — Inconsistent terminology in tray notification bodies
- **Severity**: Medium
- **Status**: Pending
- **Description**: Ad-hoc string literals scattered. Inconsistent punctuation, leaky abstractions (`pynput`, `rate-limited`, `clipboard lock`).
- **Recommended fix**: Extend `_TRAY_LABELS_*` to cover notification bodies. Standardize punctuation and remove internal module names.

#### UX-6 — Tray i18n only supports 2 of 8 renderer locales
- **Severity**: Medium
- **Status**: Pending
- **Description**: `_TRAY_LABELS_LOCALES` has only `en` and `es`. Renderer ships 8 locales.
- **Recommended fix**: Move tray labels into renderer's `i18n/translations/*.json`; push full dict via extended `set_tray_locale` IPC.

#### UX-7 — `force_cancel_transcription` IPC has no renderer affordance
- **Severity**: Medium
- **Status**: Pending
- **Description**: Wired only to tray. If tray unavailable (Wayland without SNI) and transcription hangs, user has zero UI to recover.
- **Recommended fix**: Show subtle "Taking too long? Force cancel" link on Home when `recordingState === "transcribing"` for >60s.

#### UX-9 — Home "LOADING" state has no progress indicator
- **Severity**: Low
- **Status**: Pending
- **Description**: When `recordingState === "loading"`, Home shows only an orange "LOADING" pill. Backend emits `download_progress` events but Home doesn't subscribe.
- **Recommended fix**: Subscribe to `download_progress`; show progress bar under status pill.

#### UX-10 — Bubble (always_visible mode) is non-interactive
- **Severity**: Medium
- **Status**: Pending
- **Description**: Clicking the always-visible bubble does nothing.
- **Recommended fix**: In `always_visible` mode only, add a button next to the waveform visualizer that has a mic `onClick` that calls `call("toggle_dictation")` Also in the settings, we should a setting to enable this or disable it..

#### UX-11 — Tray tooltip doesn't show elapsed recording time
- **Severity**: Low
- **Status**: Pending
- **Description**: Tooltip says "Voice Typer — recording [small.en] (F2)" but no elapsed time.
- **Recommended fix**: Track `_recording_started_at`; 1s `threading.Timer` updates tooltip with `mm:ss`.

#### UX-12 — Onboarding "Skip" doesn't warn that mic/hotkey may not work
- **Severity**: Medium
- **Status**: Pending
- **Recommended fix**: Wrap Skip button in `ConfirmDialog` (component already exists).

#### UX-13 — Onboarding model step doesn't show VRAM / language requirements
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Switch wizard's model step to `get_model_catalog` IPC; render VRAM/language badges.

#### UX-14 — Onboarding has no keyboard shortcuts
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Add `Enter` → Continue, `Esc` → Back, `1`-`9` → pick Nth option.

#### UX-16 — Sidebar active nav item has weak visual hierarchy
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Add 2px left accent border using `--accent`.

#### UX-17 — Sidebar collapse button tooltip doesn't show Ctrl+B shortcut
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Change tooltip to include `(Ctrl+B)`.

#### UX-18 — Settings search has no empty state
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Add empty-state banner when no settings match query.

#### UX-19 — App.tsx "Page not found" fallback has no recovery action
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Add "Go to Home" button.

#### UX-20 — About page is a 727-line catch-all
- **Severity**: Low
- **Status**: Pending
- **Recommended fix**: Split — keep About focused on Version + Privacy + Diagnostics + Resources. Move Cache Status to Settings → General. Move Updates to Settings. Remove Help section (replaced by `?` button per UX-8).

- **UX-23** — `repaste_last` not in IPC allowlist; only callable via hotkey. **Status**: Pending.
- **UX-24** — `?` help overlay shortcut labels hardcoded; lie about user's actual hotkeys. **Status**: Pending.
- **UX-25** — `?` keyboard listener skips `isContentEditable` check. **Status**: Pending.
- **UX-26** — Onboarding step 1 (Mic) has no live level meter. **Status**: Pending.
- **UX-27** — Onboarding missing Linux `input` group / udev rule permission step. **Status**: Pending.
- **UX-28** — Onboarding step 2 (Hotkey) has no test step. **Status**: Pending.
- **UX-29** — Onboarding "Continue" button never disabled; can advance with no mic. **Status**: Pending.
- **UX-30** — Home mic button not disabled during `loading` state. **Status**: Pending.


## Test Infrastructure (all Pending)

- **TEST-2** — 99 `time.sleep` calls across 28 test files (flakiness-prone). **Status**: Pending.
- **TEST-3** — 159 `inspect.getsource` source-inspection tests (brittle). **Status**: Pending.
- **TEST-4** — `test_server.py` (2799 LOC) + `test_app.py` (2484 LOC) are spaghetti test files. **Status**: Pending.
- **TEST-5** — 12 modules >650 LOC with no dedicated test file. **Status**: Pending.


## Documentation (all Pending)

- **DOC-4** — tauri-sidecar-bridge.md stale line counts + wrong file paths. **Status**: Pending.
- **DOC-6** — Missing docs for new modules (shutdown_controller, audio_quality_controller, etc.). **Status**: Pending.

## CI/CD (all Pending)

- **CI-1** — 5 `if: false` guards across 3 Tauri workflows (intentional, pre-Phase-0). **Status**: Pending (by design).
- **CI-2** — Windows workflow x86_64-only (no aarch64 Windows-on-ARM). **Status**: Pending.
- **CI-3** — `.rpm` not uploaded as CI artifact on Linux. **Status**: Pending.
- **CI-4** — macOS signing order wrong (`.app` not signed before notarization). **Status**: Pending.
- **CI-5** — macOS/Linux workflows missing dependency caching (10+ min rebuilds). **Status**: Pending.
- **CI-7** — Aggregator artifact-name mismatch (silent no-op downloads). **Status**: Pending.

## Dependencies (all Pending)

- **DEP-2** — `torch` undeclared but imported in 6+ source files. **Status**: Pending.

## Accessibility (all Pending)

- **A11Y-5** — `LiveQualityFeedback` hardcoded English + no aria-live. **Status**: Pending.
- **A11Y-6** — Settings tabs use `radiogroup` pattern, not `tablist`. **Status**: Pending.
- **A11Y-7** — `ExportFormatMenu` custom dropdown missing keyboard nav. **Status**: Pending.

## i18n (all Pending)

- **I18N-2** — Tray i18n only supports en+es (renderer has 8 locales). **Status**: Pending.
- **I18N-3** — No renderer test for RTL (Arabic) `dir` attribute. **Status**: Pending.

## IPC Protocol (all Pending)

- **IPC-1** — 68-command contract is actually 69 (`relaunch_ack` extra). **Status**: Pending.
- **IPC-2** — 3 undocumented events (`paste_failed`, `state_changed`, `status_change`). **Status**: Pending.
- **IPC-3** — `_validate_dict_payload` coverage is 8/69 handlers (ADR §2 claim unmet). **Status**: Pending.
- **IPC-4** — Rate limiter `sustained=600` is dead code (burst always fires first). **Status**: Pending.
- **IPC-5** — Error-envelope inconsistency between TCP and WS paths (missing `code` field on TCP). **Status**: Pending.

## Audio Pipeline (all Pending)

- **AUDIO-4** — VAD auto-calibration is silently a no-op when Silero VAD is active. **Status**: Pending.
- **AUDIO-5** — Grey-zone state preservation can starve silence timer during soft speech. **Status**: Pending.

---

## MIG-1.5–1.9 — Real Host Validation NOT IMPLEMENTED (Partial)

- **Status**: PARTIAL. Test scaffolds exist (50 files). **Windows (win32) collection: 1,410 collected, 0 errors** (collection crash fixed). But the actual host validation — real Nuitka freeze, code-sign, real paste/toast, native key-listener on Windows/macOS/Linux — is **NOT implemented**. The MIG runtime tests are mostly `MagicMock`/`AsyncMock` (one module `sidecar_ws.py` has no platform branch yet is tested ×3 per platform) and many are doc/Rust-source text-presence checks.
- **Cloud agent claim "2,095 passed / 0 failed"**: **Linux-only, UNVERIFIED on Windows (win32)**. Treat as unverified here.
- **Do**: run the real gates on each host; convert mock-only / doc-presence tests to behavioral asserts where feasible.

---

## Windows (win32) Runner — Actual Test Results (reproduced 2026-07-18)

| Test / area | Windows (win32) result | Evidence | Required Windows-specific fix |
|---|---|---|---|
| `tests/tauri/mig15..mig19 --collect-only` | **1,410 collected, 0 errors** ✅ | `pytest tests/tauri/mig15 mig16 mig17 mig18 mig19 --collect-only` | None — collection crash fixed |
| MIG-1.5–1.9 runtime pass count | **UNVERIFIED on Windows** ⚠️ | — | Cloud agent "2,095 passed / 0 failed" is Linux-only; not reproduced here |
| `tests/test_recording_discard.py::test_discard_closes_stream_and_clears_buffer` | **1 FAILED** ❌ (pre-existing) | `deque([]) == []` is `False` | assert `list(r._buffer) == []` or `len(r._buffer) == 0` |
| `bash -n` (5 build scripts) | **all pass** ✅ | `build_sidecar_linux/windows.sh`, `build_prewarm_linux/windows.sh`, `build_tauri_all.sh` | None |
| `biome check` (8 TS/TSX) | **clean** ✅ | `npx biome check` | None |
| `npm ci` (client) | **FIXED** ✅ (was broken) | commit `fa42fd5` — typescript lock synced to **7.0.2 (latest stable release)**; unused `postcss`/`autoprefixer`/`next-themes` kept REMOVED (not imported by any source file) | None (confirm on clean CI runner) |
| DOWNGRADE #2 (WS `reject()` no-op) | **STILL OPEN** ❌ | `sidecar_ws.py:293` + `ipc_server.py` SEC-6 | remove `.reject()` on WS path |

---

## Removed (verified FIXED — audit trail only, do not redo)

The following were claimed Fixed by the cloud agent and **independently re-verified as genuinely present** on the Windows (win32) runner (read-only lead verifier + 6 parallel sub-agents). Their full findings entries were **deleted** from this file. Listed here only so the next agent knows they are DONE:

- **XPLAT-3** (was BROKEN twice): `build_sidecar_linux.sh:263` ctranslate2/libs `if [[ -d ... ]]` guard (`bash -n` passes).
- **BUILD-2** (parity): `build_sidecar_windows.sh:144` same guard.
- **SEC-2**: TCP refuses when `VOICE_TYPER_IPC_TOKEN` unset (security upgrade).
- **SEC-6**: `_rejected` atomic inside `allow()`.
- **EH-2 / EH-3 / EH-4 / EH-5**: silent `except`/`pass` now `log.debug(exc_info=True)`.
- **XPLAT-7**: `wl-copy`/`wl-paste` `timeout=5`. **XPLAT-15**: Wayland `wtype` paste fallback (pynput preserved).
- **AUDIO-1 / AUDIO-2 / AUDIO-3**: RT warning removed; device probe removed (health thread covers it); `_recent_rms_values` dead write fixed.
- **PERF-11 / PERF-12 / PERF-13 / PERF-14**: deque fix; `.copy()` removed; flush moved outside lock; `apply_retention` backgrounded.
- **BUILD-1 / BUILD-3 / BUILD-4 / BUILD-5**: `--check` mode; `faster_whisper` hiddenimports; icon-stub invocation; artifact verification.
- **XPLAT-9 / XPLAT-18**: `CT2_LIB_DIR`/`CT2_DLL` existence guards in prewarm scripts.
- **XPLAT-13 / DOC-1 / DOC-2 / DOC-3 / DOC-5 / DOC-7 / DOC-8 / XPLAT-8**: doc fixes (hotkeys.py docstring, README link, ADR-0013 superseded, rw9 doc, runbook log strings, ADR-0020 line counts, build-script guards).
- **DEP-3 / DEP-4 / DEP-5 / DEP-6**: unused Node deps removed (`cmdk`/`std-env`/`expect-type`/`es-module-lexer`/`postcss`/`autoprefixer`/`next-themes` — none imported by `src/`); `windows` Rust crate removed; requirements.txt bounds aligned. **Note:** `postcss`/`autoprefixer`/`next-themes` are intentionally REMOVED, not restored — they are unused. Do not re-add them.
- **8 gap-assertion MIG tests**: flipped from "assert gap" to "assert fix present".
- **DOWNGRADE #1** (typescript lock mismatch): FIXED in commit `fa42fd5`. **`typescript` MUST stay at `^7.0.2` — that is the latest stable release (`npm view typescript version` → 7.0.2). A prior agent wrongly assumed 7.x was unstable and pinned the lockfile to 5.6.3, breaking `npm ci`. Do NOT downgrade it.**
- **I18N-1**: 19 keys × 7 locales translated.
- **UX-8 / UX-22 / UX-30**: help button; dead `as ConnectionStatus` cast removed; Home mic disabled during loading.
- **A11Y-1 / A11Y-2 / A11Y-3 / A11Y-4 / A11Y-8**: focus rings; progressbar ARIA; `--text-muted` contrast.
- **8 gap-assertion MIG tests**: flipped from "assert gap" to "assert fix present".
- **DOWNGRADE #1** (typescript lock mismatch): FIXED in commit `8119f45`.

**Bottom line for the next agent:** Do NOT trust "all green on Linux." On the Windows (win32) runner the MIG collection is clean (1,410) but the runtime pass count is UNVERIFIED, there is 1 pre-existing test failure (`test_recording_discard`), and 1 open downgrade (WS `reject()` metric divergence — DOWNGRADE #2). Fix DOWNGRADE #2 before merging Session-3 work.
