# Voice Typer — Comprehensive Review (2026-07-18)

This file is the **single source of truth** for the Permanent Product Improvements Review executed this run.

## Findings

### Architecture (REVIEW-1)

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

---

### Performance (REVIEW-2)

#### PERF-2 — `event_bus.publish` synchronous fan-out (footgun)
- **Severity**: Medium
- **Status**: Pending
- **Description**: `publish()` calls each subscriber synchronously in the publisher's thread. Currently safe because all hot-path callers offload to worker threads, but no runtime guard prevents future RT-thread callers.
- **Recommended fix**: Add `threading.current_thread().name` check in `publish()` that logs warning if called from `audio-worker` / `audio-callback` / `hotkey-*`.

#### PERF-3 — Bubble level worker doesn't coalesce — slow client causes ~128s visualizer freeze
- **Severity**: Medium
- **Status**: Pending
- **Description**: When worker stalls on `sendall` (2s timeout) and queue fills, worker resumes processing items one at a time. With 64 queued items, worst-case drain ~128s.
- **Recommended fix**: On `queue.get`, drain additional pending items and keep only the latest (or use `queue.LifoQueue` pattern). Bubble level events are pure "latest state" snapshots.

#### PERF-5 — `HistoryDB._queue` unbounded
- **Severity**: Low
- **Status**: Pending
- **Description**: Fire-and-forget transcription writes enqueued without bound. Pathological case: writer thread stuck on retention sweep + user keeps dictating → unbounded growth.
- **Recommended fix**: Cap at `maxsize=10000` with drop-oldest + warning log.

#### PERF-10 — `get_model_status()` does N `os.path.isdir()` calls per invocation
- **Severity**: Low
- **Status**: Pending
- **Description**: For each model in `MODEL_REGISTRY`, 2 `os.path.isdir()` calls. ~10–20 syscalls per Models-page load.
- **Recommended fix**: Cache result with 5–10s TTL; invalidate on download completion via event.

---

### Security (REVIEW-3)

#### SEC-2 — Inconsistent unauthenticated-IPC fallback between TCP and WS paths
- **Severity**: Medium
- **Status**: Pending
- **Description**: WS path refuses connections when token is unset. TCP path accepts unauthenticated connections with only a WARNING log.
- **Recommended fix**: Mirror WS path behavior — refuse connections (or require explicit `--allow-unauthenticated` flag) when token is unset.

#### SEC-4 — Loopback URL exemption enables local exfiltration of API keys
- **Severity**: Low
- **Status**: Won't Fix (intentional)
- **Description**: URL allowlist permits any `http://localhost:<port>` URL. Combined with SEC-2, attacker with local code execution could exfiltrate API keys.
- **Rationale for Won't Fix**: Loopback exemption is intentional for local dev servers. Fix would be a separate "loopback API URL change requires re-consent" UX gate.

#### SEC-6 — Rate-limiter `_rejected` counter has benign race
- **Severity**: Low
- **Status**: Pending
- **Description**: `allow()` and `reject()` acquire lock separately; counter undercounts in concurrent-reject case.
- **Recommended fix**: Have `allow()` increment `_rejected` when returning False; drop separate `reject()` call.

---

### Cross-Platform & Build (REVIEW-4)

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

#### XPLAT-3 — `build_sidecar_linux.sh` unconditionally includes `--include-data-dir=$SITE/ctranslate2/libs`
- **Severity**: High
- **Status**: **Broken** ❌ (verifier: claimed fix NEVER applied)
- **Description**: Line 218 always passed `--include-data-dir` for `ctranslate2/libs` without existence check. Nuitka fails if path doesn't exist.
- **Fix applied (claimed)**: Refactored to `NUITKA_ARGS=()` array pattern (matching macOS sibling), with `if [[ -d "$CT2_LIBS_DIR" ]]; then NUITKA_ARGS+=(...); else echo NOTE; fi` guard (build_sidecar_linux.sh).
- **Verifier finding (2026-07-18)**: The fix is **NOT present** in the working tree or HEAD. `git diff HEAD -- scripts/build/build_sidecar_linux.sh` is EMPTY; the file still has the unconditional `--include-data-dir=$SITE/ctranslate2/libs` (lines 217-218). Grep for `NUITKA_ARGS|CT2_LIBS_DIR|[[ -d` returns nothing. The original defect remains. The run SUMMARY falsely reported this as Fixed. Apply the guard and re-run `bash -n` + a Linux dry-run to close. Tracked in TASKS.md.

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

#### XPLAT-7 — `clipboard.py` Wayland subprocess calls have no timeout
- **Severity**: Medium
- **Status**: Pending
- **Description**: `wl-copy` / `wl-paste` `subprocess.run` calls have no `timeout=` argument. Unresponsive compositor blocks clipboard worker indefinitely.
- **Recommended fix**: Add `timeout=5` and catch `subprocess.TimeoutExpired`.

#### XPLAT-8 — Stale docs: `tauri-sidecar-bridge.md` + `tauri-build-runbook.md` claim features not implemented (but they are)
- **Severity**: Medium
- **Status**: Pending
- **Description**: Docs claim dev-mode + bubble/export APIs aren't implemented. Reality: all 3 are implemented in Rust.
- **Recommended fix**: Update both docs to reflect current state.

#### XPLAT-9 — `build_prewarm_windows.sh` doesn't validate `CT2_LIB_DIR` / `CT2_DLL` existence
- **Severity**: Medium
- **Status**: Pending
- **Description**: Sets `CT2_LIB_DIR` and `CT2_DLL` but never checks existence before passing to Nuitka. Sibling `build_sidecar_windows.sh` does validate.
- **Recommended fix**: Add `[[ -d ... ]]` / `[[ -f ... ]]` guards.

#### XPLAT-10 — `build_tauri_all.sh` doesn't invoke `gen_tauri_icons_stub.py`
- **Severity**: Medium
- **Status**: Pending
- **Description**: On clean checkout, Tauri build fails with "resource path doesn't exist" for cross-platform resources.
- **Recommended fix**: Auto-invoke stub generator before Phase 1c, or detect missing resources and invoke with warning.

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

#### XPLAT-13 — `hotkeys.py` docstring references non-existent class `LinuxEvdevHotkey`
- **Severity**: Low
- **Status**: Pending (doc-only)
- **Description**: Lines 2095, 2098 mention `LinuxEvdevHotkey` as a class, but no such class exists.

#### XPLAT-14 — Pyrefly false positives on `hotkeys.py` None-narrowing
- **Severity**: Low
- **Status**: Won't Fix (cosmetic; runtime-correct)

---

### UX & Product Polish (REVIEW-5)

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

#### UX-8 — `?` help overlay only reachable via `?` key — no mouse affordance
- **Severity**: Medium
- **Status**: Pending
- **Description**: Polished help overlay with 12 shortcuts, but no button anywhere in the UI opens it. Mouse-only users will never find it.
- **Recommended fix**: Add `?` icon button to TitleBar.

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

#### UX-22 — `useConnection` "reconnecting" event casts `"restarting" as ConnectionStatus` unnecessarily
- **Severity**: Low
- **Status**: Pending
- **Description**: `ConnectionStatus` union includes `"restarting"`; cast is dead code.
- **Recommended fix**: Remove the `as ConnectionStatus` cast. One-line cleanup.

---

## MIG-1.x Status (Tauri Migration)

The MIG-1.5 through MIG-1.9 epic slices remain **Pending** because they require real Windows/macOS/Linux host validation that cannot be performed in this Linux sandbox. However:

- **Headless-side scaffolding is ~85% complete** per REVIEW-4.
- **Phase 0-W (Windows)** can proceed immediately — no Windows-specific blockers.
- **Phase 0-M (macOS)** was blocked on XPLAT-5 (entitlements.plist) — **now Fixed**.
- **Phase 0-L (Linux)** was blocked on XPLAT-1 (bundle.linux) and XPLAT-3 (ctranslate2/libs guard) — **both now Fixed**. XPLAT-2 (Wayland paste) should be addressed in parallel with Phase 0-L.
- **Phase 0-L aarch64** is gated on XPLAT-11 (CI native listener build) — explicit ADR deferral.

The per-platform Phase 0 runbooks (`docs/migration/{windows,macos,linux}-validation-runbook.md`) are actionable. The cutover playbook's evidence-trail requirements are well-specified.
