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
#### ARCH-15 — `service.py` (2,116 LOC): 50-method god facade spanning 8 domains
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperService` exposes ~50 delegating methods grouped across 8 domains (history, model, onboarding, microphone_test, vocabulary, template, status, dictation). Class is currently a facade, so cost is readability/maintainability, not runtime coupling.
- **Root cause**: New service domains were added as methods on the single facade over time without extracting per-domain modules.
- **Recommended fix**: Split into `voice_typer/server/service/{history,model,onboarding,microphone_test,vocabulary,template,status,dictation}.py` mixins or sub-services. Preserve the public method names (tests + IPC handlers call them) via re-export or delegation shim. ~2-day effort.

#### ARCH-16 — `recording.py` (3,224 LOC): single `Recorder` class with 6 concerns
- **Severity**: High
- **Status**: Pending
- **Description**: `Recorder` spans ~2,750 LOC bundling 6 concerns (VAD, device management, resampler, buffer, event workers, xrun handling). Same root problem as ARCH-2 but enumerated at the method-concern level.
- **Root cause**: Real-time audio recording naturally couples many concerns; file accumulated features without extraction.
- **Recommended fix**: Split into `voice_typer/server/recording/{core,vad,device,resampler,buffer,workers}.py`. Requires porting the 22 `inspect.getsource` tests in `tests/regressions/audio_test.py` to behavioral tests first (see ARCH-12). ~3-day effort.

#### ARCH-17 — `hotkeys.py` (2,938 LOC): 5 backend classes in one file
- **Severity**: High
- **Status**: Pending
- **Description**: 5 unrelated hotkey-backend classes in one file (`HotkeyBackend` ABC, `PynputHotkey`, `WindowsNativeHotkey` ~1,420 LOC, `_NativeBackendAdapter`, `WaylandHotkey`) plus 7 module-level helpers. Same as ARCH-1, enumerated as a dedicated line item.
- **Root cause**: Hotkey backends grouped by interface rather than platform responsibility.
- **Recommended fix**: Convert to `voice_typer/server/hotkeys/` package: `base.py`, `pynput_backend.py`, `windows_native.py`, `native_adapter.py`, `wayland.py`, `_parse.py`. Re-export names in `__init__.py` (~21 test files import them). ~1-day effort.

#### ARCH-18 — `ipc_server.py` (2,297 LOC): handlers still inline
- **Severity**: Medium
- **Status**: Pending
- **Description**: Dispatch was extracted (see ARCH-3) but the `_handle_*` methods remain inline on the server class, keeping the file at ~2,297 LOC and mixing transport concerns with per-command logic.
- **Root cause**: Incremental extraction stopped at the dispatch layer; per-command handlers were left in place.
- **Recommended fix**: Extract each `_handle_*` group into per-domain mixins under `voice_typer/server/ipc/handlers/`. Preserve entry points used by existing IPC tests. ~2-day effort.

#### ARCH-19 — `prewarm.py` (2,162 LOC): 7 sections in one file
- **Severity**: Medium
- **Status**: Pending
- **Description**: 39 module-level functions spanning 7 functional groups (setup/logging, cache discovery, warming pipeline, process tracking, completion events, CLI, plus one more) in a single module.
- **Root cause**: Prewarm logic grew section-by-section without module boundaries.
- **Recommended fix**: Split along existing section comments into `voice_typer/server/prewarm/{pipeline,process_tracker,cache_probe,completion_events,cli}.py`. ~1-day effort.

#### ARCH-20 — `Models.tsx` (1,682 LOC): single-file page with 9 helpers + inline sections
- **Severity**: Low
- **Status**: Pending
- **Description**: Renderer `Models.tsx` page holds 9 helper functions and multiple inline sections (model list, install progress, VRAM badges, settings) in one 1,682-LOC file.
- **Root cause**: Page grew by appending sections rather than extracting components.
- **Recommended fix**: Extract utils (`formatVram`, `modelStatusBadge`, etc.) into `utils/models.ts` and split inline sections into sub-components under `components/models/`. Keep page as a thin composition root. Low ROI (renderer, not runtime-critical).


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

### Performance (all Pending)

#### PERF-2 — `event_bus.publish` synchronous fan-out (RT-thread footgun)
- **Severity**: High
- **Status**: Pending
- **Description**: `event_bus.publish` calls every subscriber synchronously on the calling thread. When invoked from the audio real-time thread, a slow subscriber blocks the RT loop and can glitch capture.
- **Root cause**: Pub/sub designed for main-thread use but also called from the recording worker.
- **Recommended fix**: Add a thread-name guard (or offload to a worker thread) so publish from the RT thread is deferred/non-blocking. ~0.5-day effort.

#### PERF-3 — Bubble level worker doesn't coalesce (~128s freeze on slow client)
- **Severity**: High
- **Status**: Pending
- **Description**: The bubble level worker processes every queued item individually; under a slow renderer it backs up and can freeze for ~128s.
- **Root cause**: Queue drained one-item-at-a-time with no coalescing of stale levels.
- **Recommended fix**: Drain the queue and keep only the latest level (e.g. `LifoQueue` or drain-then-coalesce). ~0.5-day effort.

#### PERF-5 — `HistoryDB._queue` unbounded
- **Severity**: Medium
- **Status**: Pending
- **Description**: The history write queue has no bound; a stall in the writer thread lets the in-memory queue grow without limit.
- **Root cause**: Queue created without `maxsize`.
- **Recommended fix**: Set `maxsize=10000` with drop-oldest + a warning log when trimmed. ~0.25-day effort.

#### PERF-10 — `get_model_status()` N `os.path.isdir()` per call
- **Severity**: Low
- **Status**: Pending
- **Description**: Each `get_model_status()` call stats N model directories via `os.path.isdir()` with no caching; called repeatedly on hot paths.
- **Root cause**: No memoization of filesystem probes.
- **Recommended fix**: Add a short-TTL cache (e.g. `functools.lru_cache` with TTL or a manual cache). ~0.25-day effort.

#### PERF-15 — `waveform_bubble_wiring.py`: `getattr` with defaults for always-set attributes (micro)
- **Severity**: Low
- **Status**: Pending
- **Description**: Several `getattr(obj, "attr", default)` calls read attributes that are always set on the object; the default branch is dead code and a micro smell.
- **Root cause**: Defensive `getattr` carried over from uncertain wiring.
- **Recommended fix**: Replace with direct attribute access (or `.get` on a real mapping) where the attribute is guaranteed present. Cosmetic; low ROI.

## Cross-Platform (all Pending)

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

#### XPLAT-17 — Linux aarch64 CI job will fail at `cargo tauri build` (missing `linux-key-listener` resource)
- **Severity**: Medium
- **Status**: Pending
- **Description**: `.github/workflows/tauri-linux-build.yml` builds `linux-key-listener` only for x86_64, but `tauri.conf.json` lists it as a required resource for ALL platforms. The aarch64 CI job therefore fails at `cargo tauri build` because the resource file is absent.
- **Root cause**: Per-arch resource list not implemented; deferral per ADR-0020 (aarch64 Linux follow-up).
- **Recommended fix**: Make the `linux-key-listener` resource platform-conditional (only required for x86_64), or add an aarch64 build step / stub, or document the manual `compile_native.sh` requirement. ~0.5-day effort.

#### XPLAT-19 — [Partial] ADR §6.3 Win32 focus-restore now compiles
- **Severity**: High
- **Status**: Partial
- **Description**: The Win32 focus-restore path (ADR §6.3) in `src-tauri/src/commands/sidecar_cmds.rs` now compiles after the `AttachThreadInput` import fix and the `hwnd.is_invalid()` → `(hwnd.0 as usize) == 0` fix (verified via `cargo check` EXIT:0 on win32 GNU target, 2026-07-18).
- **Remaining work**: The code is not runtime-validated. Needs a real Windows host smoke test of the UIPI-fallback (elevated-target focus steal) and focus-restore behavior, plus the actual focus-restore integration test. Cannot be run in this sandbox.
- **Recommended fix**: Run the `VALIDATE-ON-WINDOWS-HOST` block for `MIG-1.5-sub1-paste-focus-restore` — launch an elevated Notepad, dictate, confirm focus returns to the originating window; verify UIPI fallback path executes without error.


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

#### UX-23 — `repaste_last` not in IPC allowlist; only callable via hotkey
- **Severity**: Medium
- **Status**: Pending
- **Description**: The backend `repaste_last` handler exists but is not registered in the IPC allowlist, so renderer components cannot call it — it is only reachable through the configured hotkey.
- **Root cause**: Allowlist registration omitted when the command was added.
- **Recommended fix**: Add `repaste_last` to the IPC command allowlist (and expose a tray/menu affordance if desired). Verify with a renderer call. ~0.25-day effort.

#### UX-24 — `?` help overlay shortcut labels hardcoded; lie about user's actual hotkeys
- **Severity**: Medium
- **Status**: Pending
- **Description**: The `?` help overlay hardcodes shortcut key labels (e.g. "F2") instead of reading the user's configured hotkeys, so the overlay contradicts the actual bindings.
- **Root cause**: Labels are static strings, not sourced from the resolved config.
- **Recommended fix**: Pull displayed shortcuts from the resolved hotkey config (same source the hotkey backend uses). ~0.5-day effort.

#### UX-25 — `?` keyboard listener skips `isContentEditable` check
- **Severity**: Medium
- **Status**: Pending
- **Description**: The global `?` overlay listener fires even when focus is in a contentEditable field, so pressing `?` while typing in a text box pops the overlay unexpectedly.
- **Root cause**: Listener lacks the `isContentEditable` guard that other global shortcuts use.
- **Recommended fix**: Add the same `isContentEditable` / input-focus guard used by other global hotkeys before toggling the overlay. ~0.25-day effort.

#### UX-26 — Onboarding step 1 (Mic) has no live level meter
- **Severity**: Low
- **Status**: Pending
- **Description**: The onboarding Mic step asks the user to pick a microphone but shows no live input level, so users can't confirm the device works before continuing.
- **Root cause**: Step renders device list only, no level callback wired.
- **Recommended fix**: Subscribe to the mic level IPC event and render a live meter next to the selected device. ~0.5-day effort.

#### UX-27 — Onboarding missing Linux `input` group / udev rule permission step
- **Severity**: High
- **Status**: Pending
- **Description**: On Linux, native hotkeys / listener need the user in the `input` group and a udev rule; the onboarding wizard never checks or instructs this, so first-run Linux users complete setup and hotkeys silently fail.
- **Root cause**: Permission step only covers macOS Accessibility (UX-4); Linux input-group/udev not covered.
- **Recommended fix**: Add a platform-conditional Linux step that checks `input` group membership and presents the udev rule + setup command, mirroring the macOS step. ~0.5-day effort.

#### UX-28 — Onboarding step 2 (Hotkey) has no test step
- **Severity**: Medium
- **Status**: Pending
- **Description**: The Hotkey onboarding step lets the user record a hotkey but offers no "press it now to test" confirmation, so misconfigured hotkeys pass silently.
- **Root cause**: Step captures the binding but never validates it triggers.
- **Recommended fix**: Add a "Test" sub-step that listens for the recorded combo and shows success/failure before continue. ~0.5-day effort.

#### UX-29 — Onboarding "Continue" button never disabled; can advance with no mic
- **Severity**: Medium
- **Status**: Pending
- **Description**: The onboarding "Continue" button is always enabled, so a user can advance past the Mic step without selecting a working microphone, leading to a broken first-run.
- **Root cause**: No guard tying button-disabled state to step validity.
- **Recommended fix**: Disable "Continue" until a valid mic is selected (and, for the Hotkey step, until a binding is captured/tested). ~0.25-day effort.

#### UX-30 — Home mic button not disabled during `loading` state
- **Severity**: Low
- **Status**: Pending
- **Description**: On Home, the mic button remains clickable while `recordingState === "loading"` (model download/prewarm), so clicking it queues an action against a not-ready backend.
- **Root cause**: Button disabled logic covers `transcribing`/`recording` but not `loading`.
- **Recommended fix**: Also disable the mic button when `recordingState === "loading"`. ~0.25-day effort.


## Test Infrastructure (all Pending)

#### TEST-2 — 99 `time.sleep` calls across 28 test files (flakiness-prone)
- **Severity**: Medium
- **Status**: Pending
- **Description**: 99 `time.sleep(...)` calls across 28 test files act as fixed-delay synchronization, which is flaky on loaded CI runners (too short → race; too long → slow suite).
- **Root cause**: Tests synchronize on time instead of condition/event.
- **Recommended fix**: Replace fixed sleeps with condition waits (events, `threading.Event.wait`, or polling predicates). Chip away file-by-file. ~2-day effort.

#### TEST-3 — 159 `inspect.getsource` source-inspection tests (brittle)
- **Severity**: Low
- **Status**: Pending
- **Description**: 159 tests pin implementation structure (variable names, call-site spellings, call counts) via `inspect.getsource`, making refactors (ARCH-1..19) expensive and error-prone. Overlaps ARCH-12.
- **Root cause**: Tests written against source text rather than behavior.
- **Recommended fix**: Adopt rule "no new `inspect.getsource` tests; port existing when touching pinned code." Port highest-friction ones alongside ARCH refactors.

#### TEST-4 — `test_server.py` (2,799 LOC) + `test_app.py` (2,484 LOC) are spaghetti test files
- **Severity**: Low
- **Status**: Pending
- **Description**: The two largest test files bundle many unrelated test classes with shared heavy fixtures, making them slow to run and hard to navigate.
- **Root cause**: Tests accumulated in the earliest files without topical split.
- **Recommended fix**: Split by domain into `tests/server/` submodules; share fixtures via `conftest.py`. Mechanical but large.

#### TEST-5 — 12 modules >650 LOC with no dedicated test file
- **Severity**: Low
- **Status**: Pending
- **Description**: 12 source modules over 650 LOC have no matching `tests/*` file, leaving them covered only incidentally by integration tests.
- **Root cause**: Modules added without unit-test companions.
- **Recommended fix**: Add focused unit-test files per module (prioritize the god-files in ARCH-1..19).

## Documentation (all Pending)

#### DOC-4 — `tauri-sidecar-bridge.md` stale line counts + wrong file paths
- **Severity**: Low
- **Status**: Pending
- **Description**: The sidecar-bridge doc cites line counts and file paths that no longer match after the Tauri v2 + Python sidecar migration (ADR-0020), so readers are pointed at wrong locations.
- **Root cause**: Doc not updated alongside the migration.
- **Recommended fix**: Regenerate line counts/paths from current tree; cross-check each referenced file exists.

#### DOC-6 — Missing docs for new modules (shutdown_controller, audio_quality_controller, etc.)
- **Severity**: Low
- **Status**: Pending
- **Description**: Modules introduced during the migration (e.g. `shutdown_controller`, `audio_quality_controller`) have no doc page, leaving behavior undocumented for new agents.
- **Root cause**: Modules added without accompanying docs.
- **Recommended fix**: Add a short doc per new module covering responsibility, entry points, and IPC surface.

## CI/CD (all Pending)

#### CI-1 — 5 `if: false` guards across 3 Tauri workflows (intentional, pre-Phase-0)
- **Severity**: Low
- **Status**: Pending (by design)
- **Description**: Five `if: false` guards disable jobs across 3 Tauri workflows; intentional pre-Phase-0 scaffolding, not a bug.
- **Root cause**: Workflows scaffolded before migration phases were ready.
- **Recommended fix**: Remove guards progressively as each MIG phase lands; no action until then.

#### CI-2 — Windows workflow x86_64-only (no aarch64 Windows-on-ARM)
- **Severity**: Low
- **Status**: Pending
- **Description**: The Windows CI workflow builds only x86_64; Windows-on-ARM (XPLAT-12) has no build/validate job.
- **Root cause**: aarch64 Windows runner not yet generally available; deferral per ADR §4.1.
- **Recommended fix**: Add an aarch64 Windows job once a runner is available, or document the gap.

#### CI-3 — `.rpm` not uploaded as CI artifact on Linux
- **Severity**: Low
- **Status**: Pending
- **Description**: The Linux workflow builds an `.rpm` but does not upload it as a CI artifact, so it is discarded after the run.
- **Root cause**: Upload step only covers `.deb`/AppImage.
- **Recommended fix**: Add the `.rpm` to the artifact upload step.

#### CI-4 — macOS signing order wrong (`.app` not signed before notarization)
- **Severity**: Medium
- **Status**: Pending
- **Description**: The macOS workflow invokes notarization before the `.app` bundle is signed, so notarization receives an unsigned artifact (or fails).
- **Root cause**: Signing and notarization steps ordered incorrectly.
- **Recommended fix**: Sign the `.app` (and helper binaries) first, then submit to notarytool. Verify with a local re-run.

#### CI-5 — macOS/Linux workflows missing dependency caching (10+ min rebuilds)
- **Severity**: Low
- **Status**: Pending
- **Description**: macOS/Linux workflows rebuild all dependencies from scratch each run (no `cache` step for cargo/pip), adding 10+ minutes per run.
- **Root cause**: Cache steps only on Windows workflow (or absent).
- **Recommended fix**: Add `actions/cache` for `~/.cargo` and the Python venv keyed on lockfiles.

#### CI-7 — Aggregator artifact-name mismatch (silent no-op downloads)
- **Severity**: Medium
- **Status**: Pending
- **Description**: The aggregator job requests artifacts by a name that no build job produces, so downloads silently no-op and the aggregate release is missing platforms — with no error surfaced.
- **Root cause**: Artifact name constant drifted from the upload step.
- **Recommended fix**: Align artifact names between upload and download; fail the job if expected artifacts are absent.

## Dependencies (all Pending)

#### DEP-2 — `torch` undeclared but imported in 6+ source files
- **Severity**: Medium
- **Status**: Pending
- **Description**: `torch` is imported across 6+ source files but is not declared in the project's dependency manifest, so install/lockfile is incomplete and environments can break.
- **Root cause**: Transitive import relied on a side-installed package rather than an explicit dep.
- **Recommended fix**: Add `torch` (pinned) to the dependency manifest, or gate the imports behind an optional extra if truly optional.

## Accessibility (all Pending)

#### A11Y-5 — `LiveQualityFeedback` hardcoded English + no `aria-live`
- **Severity**: Medium
- **Status**: Pending
- **Description**: The `LiveQualityFeedback` component renders hardcoded English strings and has no `aria-live` region, so screen readers neither translate nor announce quality changes.
- **Root cause**: Component built without i18n keys or ARIA semantics.
- **Recommended fix**: Move strings to i18n keys and wrap the changing region in `aria-live="polite"`.

#### A11Y-6 — Settings tabs use `radiogroup` pattern, not `tablist`
- **Severity**: Low
- **Status**: Pending
- **Description**: Settings navigation uses a `radiogroup`/`radio` ARIA pattern for what are visually tabs, breaking expected tab keyboard semantics (arrow vs. Tab) for AT users.
- **Root cause**: Wrong ARIA role chosen at implementation.
- **Recommended fix**: Switch to `tablist`/`tab`/`tabpanel` with proper `aria-selected` and roving focus.

#### A11Y-7 — `ExportFormatMenu` custom dropdown missing keyboard nav
- **Severity**: Medium
- **Status**: Pending
- **Description**: The custom `ExportFormatMenu` dropdown is mouse-only — no arrow-key navigation, no `role="listbox"`/`option`, no Escape-to-close, failing keyboard-only operation.
- **Root cause**: Custom widget built without keyboard handling.
- **Recommended fix**: Add `listbox`/`option` roles, arrow navigation, type-ahead, and Escape close; or replace with an accessible primitive.

## i18n (all Pending)

#### I18N-2 — Tray i18n only supports en+es (renderer has 8 locales)
- **Severity**: Medium
- **Status**: Pending
- **Description**: Tray labels (`_TRAY_LABELS_LOCALES`) cover only `en` and `es`, while the renderer ships 8 locales — non-en/es users see English tray text. Duplicate of UX-6.
- **Root cause**: Tray labels maintained separately from renderer i18n.
- **Recommended fix**: Move tray labels into renderer `i18n/translations/*.json`; push the full dict via an extended `set_tray_locale` IPC.

#### I18N-3 — No renderer test for RTL (Arabic) `dir` attribute
- **Severity**: Low
- **Status**: Pending
- **Description**: There is no renderer test asserting the `dir="rtl"` attribute is applied for RTL locales (e.g. Arabic), so RTL regressions would go unnoticed.
- **Root cause**: RTL path untested.
- **Recommended fix**: Add a render test that mounts with an RTL locale and asserts `dir="rtl"` on the root container.

## Audio Pipeline (all Pending)

#### AUDIO-4 — VAD auto-calibration is silently a no-op when Silero VAD is active
- **Severity**: Medium
- **Status**: Pending
- **Description**: When Silero VAD is the active backend, the VAD auto-calibration routine early-returns without effect, yet no log/UI signals this — users think calibration ran.
- **Root cause**: Calibration branch short-circuits for Silero and swallows the no-op.
- **Recommended fix**: Either skip calibration explicitly with a visible "not needed for Silero" status, or implement a Silero-appropriate calibration path.

#### AUDIO-5 — Grey-zone state preservation can starve silence timer during soft speech
- **Severity**: Medium
- **Status**: Pending
- **Description**: The grey-zone (uncertain VAD) state-preservation logic holds audio in a way that can delay/starve the silence timer, cutting off soft-spoken phrase endings.
- **Root cause**: Grey-zone buffering prioritized over silence detection latency.
- **Recommended fix**: Bound grey-zone hold time and ensure the silence timer still advances/triggers on soft-speech tails; add a regression test for soft-speech endings.

---

## MIG-1.1–1.9 — Desktop Runtime Migration (Tauri v2 + Python sidecar, ADR-0020)

> Migration spec: `docs/adr/0020-desktop-runtime-migration-analysis.md`. Phase map also tracked in `.workspace/TASKS.md` (untracked / gitignored).

- **MIG-1.1 (Boot + sidecar spawn)**: PARTIAL. `src-tauri` Rust host compiles clean on win32 GNU target (`cargo check` EXIT:0, 2026-07-18). Sidecar spawn + `Ready` handshake scaffolded. **Not validated** against a real Nuitka-frozen Python sidecar on a live host.
- **MIG-1.2 (IPC bridge)**: PARTIAL. 190 IPC tests pass. **IPC-3 regression fixed this session** (`set_esc_cancel_paused` now accepts `null` → `{}` and resumes; 29 IPC handler tests green). Still mock/doc-level, not real-host round-trip.
- **MIG-1.3 (Config mirror)**: PARTIAL. Config read/write scaffolded; not validated against live app config on each OS.
- **MIG-1.4 (Tray + windowing)**: PARTIAL. Tray commands compiled; real tray/menu behavior unverified on macOS/Linux Wayland.
- **MIG-1.5–1.9 (Real Host Validation)**: **NOT IMPLEMENTED (Partial)**. Test scaffolds exist (50 files). **Windows (win32) collection: 1,410 collected, 0 errors.** But the actual host validation — real Nuitka freeze, code-sign, real paste/toast, native key-listener on Windows/macOS/Linux — is **NOT implemented**. The MIG runtime tests are mostly `MagicMock`/`AsyncMock` (one module `sidecar_ws.py` has no platform branch yet is tested ×3 per platform) and many are doc/Rust-source text-presence checks.
  - **Cloud agent claim "2,095 passed / 0 failed"**: **Linux-only, UNVERIFIED on Windows (win32)**. Treat as unverified here.
  - **Linux test scaffold VERIFIED (2026-07-18)**: `pytest tests/tauri/mig15 tests/tauri/mig16 tests/tauri/mig17 tests/tauri/mig18 tests/tauri/mig19 -q --no-cov` → **1428 passed, 4 xfailed in 6.70s** (1432 collected). All 5 MIG suites green on Linux. Gap: tests are mock/doc-presence checks, not real-host behavioral asserts. (mig15=256, mig16=300, mig17=331, mig18=263, mig19=280, +4 xfailed.)
  - **XPLAT-1 config blocker FIXED this session** (`tauri.conf.json`: `postInstall`→`postInstallScript`, `preRemove`→`preRemoveScript`) so a Linux `cargo tauri build --bundles deb` can proceed — but the deb bundle build itself is still **unrun**.
  - **XPLAT-19 Win32 focus-restore now COMPILES** (verified via `cargo check`), but real-host UIPI-fallback + focus-restore smoke test is still **unrun** (see XPLAT-19 above).
  - **Do**: run the real gates on each host; convert mock-only / doc-presence tests to behavioral asserts where feasible. **Real-host validation (Windows + macOS + Linux Wayland + Linux aarch64) remains the hand-off — not done in this sandbox (Linux x86_64 only).**

---

**Bottom line for the next agent:** Do NOT trust "all green on Linux" as proof of cross-platform cutover. **Linux test scaffold is now verified green** (1428 MIG tests pass + 38 container-detect tests pass). However: (1) the Windows (win32) runner **MIG runtime pass count is still UNVERIFIED** (collection clean at 1,410; runtime not reproduced on win32 here); (2) there is 1 pre-existing test failure (`tests/test_recording_discard.py::test_discard_closes_stream_and_clears_buffer` — `deque([]) == []` is `False`, needs `list(r._buffer) == []` or `len(r._buffer) == 0`); (3) **DOWNGRADE #2 is now CLOSED** (fixed 2026-07-18); (4) this session also CLOSED/advanced: XPLAT-1 config keys fixed, XPLAT-19 compiles, IPC-3 `set_esc_cancel_paused` None-guard fixed. The remaining hand-off is **real-host validation on Windows + macOS + Linux Wayland + Linux aarch64** (run the `VALIDATE-ON-<OS>-HOST` blocks in `worklog.md` for `MIG-1.5-sub1-paste-focus-restore` — esp. `cargo check`/build on Windows for the new `windows = "0.57"` crate dep, the deb bundle build on Linux, and the UIPI-fallback smoke test on Windows 11 with elevated Notepad).
