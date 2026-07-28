## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

---

### FT-5. "Finish dictation → nothing gets transcribed" — no transcription text produced/pasted [High] — Pending
**Status:** ❌ Not Fixed — too large — live-repro investigation requiring fully-booted Electron+Python+ASR stack (recorder.py + asr_registry.py owned by Agent 14); >2 min investigation for non-Critical behavioral bug
- **Reported by user (2026-07-20)**: When they finish a dictation (stop recording / the transcription step runs), **no text is produced at all** — nothing is pasted, nothing lands in the target app, and there is no error toast. The recording appears to stop normally; the result is simply empty.
- **Plain-English symptom**: The mic captures audio (the app reaches the "transcribing…" state), but at the end the user gets zero output — as if the audio was silent, even though they spoke.
- **Likely investigation leads (do NOT assume — verify against live behavior)**:
  1. **Streaming-session path returns empty.** `DictationPipeline._transcribe` (dictation_pipeline.py:473) checks `get_streaming_session()` first; if a stale/non-`None` session exists, `session.finalize(self._audio)` is used. If that finalize returns `""`, the pipeline falls into `_handle_empty_transcription` (dictation_pipeline.py:517) which, for recordings `< 15s`, **silently suppresses the "no speech" notice** (dictation_pipeline.py:542) — so the user sees nothing. Check whether `recording_controller.py:458` session-cancel actually clears `self._streaming_session` before `DictationPipeline.run`.
  2. **Audio buffer / RMS is empty or near-zero.** `recording_controller.py:425` derives `duration = len(audio)/sample_rate`; if `< 0.5s` it "Too short — skipped" (line 441). `recorded_rms = app.recorder.last_rms` (line 427) — if VAD/RMS is misread, `_handle_empty_transcription` (dictation_pipeline.py:550) reports "near-silence → check microphone". Verify `Recorder.stop()` (recorder.py:2534) actually returns non-empty audio after the session-merge device-loop fix.
  3. **ASR backend returns empty but does not raise.** `active.transcribe_with_fallback(...)` (dictation_pipeline.py:502) — if Parakeet/Qwen/Whisper returns `""` (e.g. model loaded but misconfigured, wrong device, or `audio_stats` mismatch), the pipeline treats it as "no speech" rather than an error. Capture the actual transcript length in the log (`[TRANSCRIBE] Transcription complete (len=…)` at dictation_pipeline.py:135).
  4. **Merge-damage regression.** This surfaced after the 6-session merge; the `recorder.py` device-loop restoration this session fixed an `UnboundLocalError` on start — confirm the *stop* path and audio hand-off were not subtly affected.
- **Files to read**: `voice_typer/server/dictation_pipeline.py` (`_transcribe`, `_handle_empty_transcription`, `run`), `voice_typer/server/recording_controller.py` (`stop`, `get_streaming_session`, `set_streaming_session`, `_streaming_session`), `voice_typer/server/recorder.py` (`stop` → returns `np.ndarray`, `last_rms`), `voice_typer/server/asr_registry.py` / `active_transcriber` / `transcribe_with_fallback`.
- **Repro**: Launch app (Electron + Python, `pythonw.exe` venv). Dictate ~5s of clear speech. Stop. Observe: tray returns to "Ready", no text pasted, no toast. Pull the log for `[TRANSCRIBE] Transcription complete (len=…)` and `[DICTATION] Recording stopped -- …s of audio`.
- **Goal**: Dictation produces the spoken text (pasted or on-clipboard) for normal speech; empty results only when truly silent, and those report a clear "no speech" toast (not silent suppression for normal-length recordings).
- **Verify**: reproduce the fix live; confirm `len(text) > 0` for a spoken phrase and the text appears in the target window; confirm a genuinely silent recording still shows a "no speech" notice (not silent suppression).
- **Effort**: Medium.
- **Priority**: High (core feature broken for the user right now).

---

**Bottom line for the next agent:** Do NOT trust "all green on Linux" as proof of cross-platform cutover.

plus the base repo's pre-existing comprehensive review.

## Status Legend

- ✅ Fixed — the issue was resolved in this session.
- ⚠️ Partial — partial fix applied; follow-up work documented.
- ❌ Pending — issue identified but not fixed.
- 💥 Broken — fix introduced a regression.
- 🚫 Won't Fix — issue acknowledged but consciously not addressed.

### Structure

1. **Base Set** — the original `review.md` from the repo root,
   preserved verbatim. This is the pre-existing set of open findings.
2. **Per-Session Findings** — each session's `review.md`,
   appended verbatim under a `## Session N Findings` header. Sessions used
   different formats (`### SESSION_PREFIX-N`);
   rather than risk dropping findings by parsing 5 incompatible formats, we
   preserve each session's review verbatim. The integrity check (every
   finding from every session appears at least once) is therefore trivially
   satisfied.
3. **Merge-Stage Findings** — new findings discovered during the intelligent
   sub-agent merge (NOT present in any session's original review).

---

## Base Set (original review.md — pre-existing open findings)

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.
>
> This file was filtered by verifier-agent on 2026-07-21 to REMOVE entries for fixes that were verified-done and KEEP only items that remain partial, broken, or not-implemented.

---

### ARCH-5 — `service.py` (2,116 lines): 66-method facade
- **Severity**: Medium
- **Status**: ❌ Not Fixed — too large (~1-day refactor) — service.py 2116-line 66-method facade; prior Won't Fix decision per review.md
- **Description**: `VoiceTyperService` exposes 66 total methods (1 `__init__` + 65 public). 21 pure delegation, 44 with real logic. 16 section comment headers span 8 domains (history, model, onboarding, microphone_test, vocabulary, template, status, dictation).
- **Investigation**: VERIFIED. `inspect.getsource(VoiceTyperService.apply_config)` follows `__func__` to defining module — works through mixin inheritance. `hasattr(VoiceTyperService, "test_llm_connection")` works via MRO. Only 6 source-file-read assertions need updating.
- **Mixin approach is safe**: No monkeypatch-by-path blockers unlike ARCH-2/4. Re-exports in `__init__.py` will preserve all 65 public names.
- **Recommended fix**: Split into `voice_typer/server/service/{history,model,onboarding,microphone_test,vocabulary,template,status,dictation}.py` mixins or sub-services. Preserve public method names via re-export or delegation shim.
- **Effort**: 🟡 **MEDIUM** — Lower risk than other splits. ~4-5 hours.
- **Confidence for one-shot fix**: 75% — mixin approach is safe; only 6 assertions need updating.

### ARCH-8 — `_open_config_file` extraction blocker (source-string tests)
- **Severity**: Medium
- **Status**: ❌ Not Fixed — blocked on test porting — 30+ test patches use _recording_pkg.X indirection; migrating to direct submodule patches exceeds 10-min ceiling
- **Description**: `VoiceTyperApp._open_config_file` (104 LOC) is the only remaining "fat" method on `VoiceTyperApp`. Extraction blocked by 6 `inspect.getsource` tests in `tests/test_b4_config_editor_lock.py` and `tests/regressions/concurrency_test.py` that pin literal source text.
- **Recommended fix**: Port these 6 source-string tests to behavioral tests (RW-8 pattern), then extract `ConfigEditorLauncher`. ~1-day effort.
- **Effort**: 🟡 **MEDIUM** — The source-string porting is the tricky part. Must carefully preserve test behavior. The `_open_config_file` method is only 104 LOC and relatively self-contained. ~1 day.
- **Confidence for one-shot fix**: 80% — self-contained but source-string tests add friction.

### ARCH-9 — `app.py` test-seam re-exports (173 monkeypatch sites)
- **Severity**: Low
- **Status**: ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) — app.py re-exports ~27 symbols from sibling modules; 199 monkeypatch.setattr sites span all test files. Migrating each call site touches 65+ files at high risk of breaking tests (~1 day refactor)
- **Description**: `app.py` re-exports 20 symbols from sibling modules so tests can monkeypatch `voice_typer.server.app.X`. 173 monkeypatch sites depend on these re-exports.
- **Recommended fix**: Migrate monkeypatch sites to canonical paths (`voice_typer.server.server_platform.is_autostart_enabled` instead of `voice_typer.server.app.is_autostart_enabled`), then delete re-export blocks. Mechanical refactor touching many files.
- **Effort**: 🔴 **HIGH** — 72+ import sites across 65+ files, ~20 re-exported symbols. Every monkeypatch site must be migrated one-by-one. High risk of breaking tests. Cannot do in one shot confidently. ~1 day.
- **Confidence for one-shot fix**: 50% — wide surface area, many tests.

### ARCH-12 — 164 `inspect.getsource` source-string tests across the codebase
- **Severity**: Low
- **Status**: ❌ Not Fixed — out of scope (codebase-wide architectural concern about source-string tests; no single file locus)
- **Description**: 164+ source-string tests pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Make refactoring expensive.
- **Recommended fix**: Adopt project rule — "no new `inspect.getsource` tests; port existing ones when touching the code they pin." Chip away over time.
- **Effort**: 🔴 **EXTRA HIGH** — 164+ calls across 30+ test files. Not a discrete task — it's a project-wide migration. Chip away individually when touching pinned code. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — cannot complete in one shot.

### XPLAT-12 — Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: ❌ Not Fixed — VALIDATE-ON-WINDOWS-HOST: Windows-on-ARM host validation required — Nuitka cross-compile + aarch64 freeze must be tested on real Windows ARM hardware
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.
- **Effort**: 🔴 **HIGH** — Requires Windows-on-ARM runner access not available in this sandbox. Cannot complete.
- **Confidence for one-shot fix**: 10% — blocked by runner availability.

### XPLAT-19 — [Partial] ADR §6.3 Win32 focus-restore now compiles
- **Severity**: High
- **Status**: ❌ Not Fixed — VALIDATE-ON-WINDOWS-HOST: launch elevated Notepad on real Windows host, dictate via Voice Typer, confirm focus returns to Notepad (Win32 focus-restore runtime validation). Code path compiles per cargo check baseline.
- **Description**: The Win32 focus-restore path (`src-tauri/src/commands/sidecar_cmds.rs`) now compiles (verified via `cargo check` EXIT:0 on win32 GNU target). Remaining work: real Windows host smoke test.
- **Recommended fix**: Run the `VALIDATE-ON-WINDOWS-HOST` block — launch elevated Notepad, dictate, confirm focus returns. Cannot be run in this sandbox.
- **Effort**: 🔴 **HIGH** — Requires actual Windows host with elevated Notepad. Cannot complete in sandbox. ~0.5 day on real hardware.
- **Confidence for one-shot fix**: 40% — blocked by hardware access.

### TEST-2 — 99 `time.sleep` calls across 28 test files (flakiness-prone)
- **Severity**: Medium
- **Status**: ❌ Not Fixed — out of scope (codebase-wide test-flakiness refactor across 28 test files owned by multiple agents)
- **Description**: 127+ `time.sleep(...)` calls across 28+ test files act as fixed-delay synchronization, which is flaky on loaded CI runners.
- **Root cause**: Tests synchronize on time instead of condition/event.
- **Recommended fix**: Replace fixed sleeps with condition waits (events, `threading.Event.wait`, or polling predicates). Chip away file-by-file. ~2-day effort.
- **Effort**: 🔴 **HIGH** — 127+ sleep calls across 28+ files. Each one needs individual analysis to determine the correct replacement (event.wait, polling predicate, etc.). ~2 days.
- **Confidence for one-shot fix**: 30% — cannot do all in one shot; chip away file-by-file.

### TEST-5 — 12 modules >650 LOC with no dedicated test file
- **Severity**: Low
- **Status**: ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- **Description**: 12 source modules over 650 LOC have no matching `tests/*` file.
- **Recommended fix**: Add focused unit-test files per module.
- **Effort**: 🔴 **EXTRA HIGH** — Adding comprehensive tests for 12 large modules is a major effort. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — too many modules to cover in one shot.

---

### S1-CR-33 — ~154 failing vitest tests across 35 client files — real UI regressions (WORSENED)
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- **Severity**: High · **Status**: Pending (worsened — was ~45/14, now 154/35)
- **Category**: Existing failing tests / UI regressions
- **Location**: 35 test files under `voice_typer/client/src/renderer/src/__tests__/` and `components/__tests__/`
- **Evidence**: Full run (2026-07-25): `Test Files 35 failed | 77 passed (112), Tests 154 failed | 1046 passed | 4 expected fail | 8 skipped (1212)`. Was ~45 failed across 14 files previously. Fixes were attempted in commits `7ef71d7` and `ac12e6c` (assertion updates) but regressed further — likely new UI component changes introduced additional failures.
- **Impact**: 154 production UX and accessibility regressions blocking CI gate.
- **Proposed fix**: Full test-by-test triage. Many failures are assertion mismatches from component restyling (border classes, aria attrs). Some are a11y violations (SettingRow labels missing `htmlFor`/`aria-label`). Needs systematic fix across 35 files.
- **Confidence**: High · **Found by**: R12

### S1-CR-65 — `apply_config_side_effects` 215-line branching method
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: `voice_typer/server/service.py:1045-1260`
- Evidence: Single method with 8+ parallel `if "X" in updates:` blocks; 12 distinct side-effects.
- Fix: Define `ConfigSideEffect` protocol; register ~12 handlers in a list; each handler lives in its own module. · **Found by**: R1

### S1-CR-66 — `sys.modules` registration hack in `ipc_server.py:622-632`
**Status:** ⚠️ Partial — handlers cycle already broken (handler mixins import log from voice_typer.server.handlers._log + validation from voice_typer.server.ipc.validation); sys.modules[_CANONICAL] = sys.modules['__main__'] hack at ipc_server.py:334-336 retained for providers.py/sidecar_ws.py/app.py/__main__.py compatibility — removing would introduce duplicate IPCServer class regression
- Location: `voice_typer/server/ipc_server.py:622-632`
- Evidence: `_CANONICAL = "voice_typer.server.ipc_server"; if _CANONICAL not in sys.modules: sys.modules[_CANONICAL] = sys.modules["__main__"]` — needed because `python -m voice_typer.server.ipc_server` loads the file as `__main__` but handler mixins import from the canonical name.
- Fix: Give handler mixins their own helper module (`ipc/_helpers.py`); mixins import from `_helpers`; `IPCServer` imports from `_helpers` AND from mixins. No cycle, no hack. · **Found by**: R1

### S1-CR-67 — Custom `_RecordingModule` / `_PrewarmModule` / `_ServerPlatformModule` sys.modules hacks
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) — removing _RecordingModule custom class requires migrating 30+ monkeypatch.setattr sites across tests/test_recording.py, tests/test_secure_clear_array.py, tests/test_recorder_*.py to patch submodules directly
- Location: `voice_typer/server/recording/__init__.py:260-349`, `voice_typer/server/prewarm/__init__.py` (289 LOC), `voice_typer/server/server_platform/__init__.py:84-277`
- Evidence: Three packages install custom module subclasses that override `__getattr__` and `__setattr__` so test patches like `monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)` propagate to submodules. ~500 LOC of `__init__.py` boilerplate exists for test-patch compatibility.
- Fix: Migrate tests to patch submodules directly; remove custom module classes and `_pkg.X` indirection. · **Found by**: R1

### S1-CR-69 — ADR-0015 documentation drift (allowlist location, missing commands)
**Status:** ❌ Not Fixed — out of file scope (targets docs/adr/0015-electron-command-allowlist.md; no docs/ paths in FIX-9 file list)
- Location: `docs/adr/0015-electron-command-allowlist.md:5,56,75`
- Evidence: (1) Says allowlist at `client/src/main/index.ts:532-627` — actually at 79-191. (2) Lists `show_electron_notification` under "Not in the allowlist" but `index.ts:186` includes it. (3) ADR's "exhaustive list" omits 8 commands added later: `repaste_last`, `force_cancel_transcription`, `refresh_microphones`, `get_rms_level`, `get_audio_status`, `get_vocabulary_suggestions`, `apply_vocabulary_suggestion`, `dismiss_vocabulary_suggestion`.
- Fix: Update ADR-0015 to match actual allowlist. · **Found by**: R2

### S1-CR-73 — Component size: 9 files exceed 500-800 LOC monolith threshold
**Status:** ⚠️ Partial — 8 of 9 listed files refactored below 800-LOC threshold (Models 230, Settings 455, Microphone 214, Templates 194, Vocabulary 205, History 474); Home.tsx (949 LOC) still over threshold — further split would require creating new files outside FIX-4 scope (already has 4 inline subcomponents + 1 hook)
- Location: `pages/Models.tsx` (1682), `pages/Settings.tsx` (1082), `components/settings/ThemeSettingsSection.tsx` (890), `pages/Microphone.tsx` (862), `components/hotkey/HotkeyPicker.tsx` (816), `pages/Home.tsx` (849), `pages/Templates.tsx` (716), `pages/Vocabulary.tsx` (700), `pages/History.tsx` (562)
- Evidence: `Models.tsx` (1682 LOC) mixes model catalog fetching, download state machine, cloud provider config, model-family grouping, accordion UI, dialogs. `Settings.tsx` (1082) owns config state, debounced save, search-filter-with-tab-routing, reset dialog. `ThemeSettingsSection.tsx` (890) mixes oklch→sRGB color conversion, DOM-based color resolution, localStorage draft backup, hover-preview state, UI.
- Fix: Split each large component into focused subcomponents (e.g., `ModelCatalogSection`, `ModelDownloadManager`, `CloudProviderSection`, `ModelImportDialog` for `Models.tsx`). Extract color math to `lib/color-utils.ts`. · **Found by**: R2

### S1-CR-78 — IPC protocol is unversioned — schema drift between Python/Rust/TS is undetectable at runtime
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: All IPC frames across `voice_typer/server/ipc/server.py`, `voice_typer/server/sidecar_ws.py`, `src-tauri/src/sidecar/ws.rs`, `src-tauri/src/commands/sidecar_cmds.rs`, `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`
- Evidence: No `protocol_version` field in any frame. If any layer changes the envelope shape, other layers can't detect mismatch at runtime. `server_started` handshake already has a minor inconsistency (uses `event` key instead of `type`).
- Fix: Add `protocol_version` field to the `server_started` handshake and to the auth frame. Have the Rust host check version on connect and fail fast on mismatch. · **Found by**: R3

### S1-CR-112 — 4 i18n helper scripts hardcode workspace path
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: `scripts/add_prewarm_i18n_keys.py:30`, `scripts/add_prewarm_log_i18n_keys.py:18`, `scripts/add_run_prewarm_i18n_keys.py:20`, `scripts/fix_i18n_remaining.py:5`
- Evidence: `ROOT = Path("/home/z/my-project/voice-typer")`. Will fail when repo is cloned to any other path.
- Fix: Use `Path(__file__).resolve().parent.parent` like the sibling scripts. · **Found by**: R18

### S1-CR-124 — `docs/home-directory.md` log path and rotation are both wrong
**Status:** ❌ Not Fixed — out of scope (code is correct, only docs wrong — docs owned by other agents)
- Location: `docs/home-directory.md:48, 70, 132`
- Evidence: Tree shows `├── logs/` `│   └── voice-typer.log` with "1 MB × 2 backups". Actual: log file written directly to `<DATA_DIR>/voice-typer.log` (no `logs/` subdir). Rotation is 5 MiB × 5.
- Fix: Drop `logs/` from path; bump rotation numbers; point to `logging_setup.py`. · **Found by**: R20

### S1-CR-127 — CHANGELOG.md references stale module paths
**Status:** ⚠️ Partial — collateral fix from #221 — CHANGELOG.md IPC command counts updated; originally-flagged stale module paths already corrected to package form
- Location: `CHANGELOG.md:44-45`
- Evidence: References `voice_typer/server/native_hotkeys.py` and `voice_typer/server/hotkeys.py`. Both are now packages.
- Fix: Annotate with `[now package: native_hotkeys/]` etc. · **Found by**: R20

### S1-CR-128 — FEATURES.md references stale module paths + version
**Status:** ❌ Not Fixed — out of file scope (fix target FEATURES.md owned by Agent 3; pyproject.toml version is canonical 1.0.0)
- Location: `FEATURES.md:119, 205`
- Evidence: `Smart duck enabled (v2.2)` — `pyproject.toml:28` declares `version = "1.0.0"`. `prewarm.py + task_scheduler.py` — `prewarm.py` is now `prewarm/` package.
- Fix: Drop `(v2.2)` tag or document; update `prewarm.py` → `prewarm/` package. · **Found by**: R20

### S1-CR-136 — Crash report lacks reproduction hint and app/system context
**Status:** ⚠️ Partial — bug_report.md side done (Diagnostic Bundle + Reproduction Hint sections added in FIX-1-REAPPLY); crash_handler.py VEH blurb + tray.py notification portions out of agent_01 scope
- Location: `voice_typer/server/crash_handler.py:521-525, 568-622`
- Evidence: Windows VEH writes `crash_diagnostics.<PID>.txt` containing: BOM, timestamp, exception code, exception address, PID, TID, one-line friendly name. Does NOT include: app version, OS version, last user action / last IPC command, "what to report next" hint.
- Fix: Extend the VEH blurb to include app version + OS version (pre-compute at `set_crash_handler_config_dir` time). Add a "Next steps: run `python scripts/diagnostics.py export`" line to the tray notification. · **Found by**: R20

### S1-CR-137 — IPC command count claims are inconsistent across docs
**Status:** ⚠️ Partial — collateral fix from #221 — CHANGELOG/FEATURES/SECURITY/CONTRIBUTING counts updated to 63/61; originally-flagged 4 locations (ADR-0020, sidecar_ws.md, ADR-0019, ARCHITECTURE.md) already at 63 commands
- Location: `docs/adr/0020-desktop-runtime-migration-analysis.md:32`, `docs/modules/sidecar_ws.md:13,26`, `docs/adr/0019-per-connection-rate-limiter.md:5`, `docs/ARCHITECTURE.md:15,81`
- Evidence: ADR-0020:32 says 69. `sidecar_ws.md:13,26` says 68. ADR-0019:5 says 68. ARCHITECTURE.md:81 says "frozen for v1 at 68". Actual count (verified by grepping `_COMMAND_REGISTRY`): **73 entries**. All three documented numbers are stale.
- Fix: Recount; update all references to the true value (73). · **Found by**: R20

### S1-CR-141 — `voice_typer/stubs/README.md:21` references stale `server_platform.py`
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Evidence: Table row for `winreg` says `Used by: server/server_platform.py, task_scheduler.py`. `server_platform` is a package.
- Fix: Update to `server/server_platform/` (package). · **Found by**: R20

### S1-CR-143 — macOS `VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1` silently disables key-up delivery and key suppression
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: `voice_typer/server/native/macos-key-listener.swift:462-492`
- Evidence: When env var is set, CGEventTap is never created. No key-up delivery (push-to-talk mode will never fire → recording starts but never stops). No key suppression (Caps Lock as hotkey will toggle OS caps state on every press). No warning logged when active in production.
- Fix: Log a WARNING at binary startup when env var is set; emit `WARN:SKIP_ACCESSIBILITY` line that Python adapter can surface as tray notification. · **Found by**: R17

### S1-CR-144 — `build_tray_menu_model` reads `controller._microphones` via untyped `getattr`
**Status:** ⚠️ Partial — WARNING log added to tray_menu.py:maybe_publish_tray_menu when controller._microphones is None (silent failure → logged regression detection); typed-access (Protocol promotion) requires editing app.py (orphan) or tray_types.py (orphan) — out of agent_11 file scope
- Location: `voice_typer/server/tray.py:661`
- Evidence: `microphones=getattr(controller, "_microphones", None)`. Relies on `VoiceTyperApp._microphones` being initialized. If renamed, `getattr` silently returns `None` and the Microphones submenu disappears with no error.
- Fix: Add `microphones: list[dict]` to the `TrayController` Protocol, or expose a `controller.get_microphones()` method. · **Found by**: R17

### S1-CR-145 — Linux autostart `.desktop` inconsistent with bundled `.desktop` template
**Status:** ⚠️ Partial — Icon=voice-typer aligned in autostart_linux.py f-string body (re-applied in FIX-2-REAPPLY); Exec intentionally different per CR-145 comment (bundled template uses repo-relative path)
- Location: `voice_typer/server/server_platform/autostart_linux.py:54-62`, `src-tauri/voice-typer.desktop.template:1-10`
- Evidence: Runtime autostart uses `Exec=<python> <launcher.py> --hidden --delay 15`, `Icon=audio-input-microphone`. Bundled app-menu uses `Exec=voice-typer-tauri`, `Icon=voice-typer`. Two inconsistencies.
- Fix: Align both `.desktop` files on same `Icon=voice-typer` and same `Exec=`. · **Found by**: R15

### S1-CR-146 — `StartupWMClass=Voice Typer` may not match Tauri window class
**Status:** ❌ Not Fixed — out of file scope + host-validation required (target file voice-typer.desktop.template not in scope; fix requires running Tauri app + xprop WM_CLASS on real Linux desktop)
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

### S1-CR-147 — Windows manifest does not declare Windows 11 supportedOS GUID; dpiAwareness missing in standalone manifest
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: `scripts/build/voice-typer.manifest:1-22`, `scripts/build/voice-typer.spec:156-186`
- Evidence: Standalone manifest has NO `<dpiAware>` / `<dpiAwareness>` settings. PyInstaller-embedded manifest DOES (`voice-typer.spec:183-184` — `dpiAware=true/pm`, `dpiAwareness=PerMonitorV2`).
- Fix: Either delete the unused standalone `voice-typer.manifest` file, or sync its contents with the PyInstaller-embedded manifest. · **Found by**: R15

### S1-CR-152 — Stale file extension `hooks/useSnackbar.tsx` (no JSX)
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Evidence: Named `.tsx` but contains no JSX. Comment at lines 39-43 states: "named `.tsx` (not `.ts`) only because of historical extension-priority conventions; it no longer contains JSX."
- Fix: Rename to `.ts`. · **Found by**: R2

### S1-CR-156 — Incomplete store adoption — prop drilling persists in `App.tsx:327-330`
**Status:** ❌ Not Fixed — out of file scope (fix target Home.tsx owned by Agent 4 — must read recordingState + lastError from useAppStore instead of props)
- Evidence: `App.tsx` destructures `recordingState`, `lastError` from `useConnection` and passes them as props to `<Home>`. But `useConnection` writes these values to `appStore` (lines 86-88), and `appStore.ts:4-8` states the store exists so "any component can subscribe to connectionStatus / recordingState / lastError without prop drilling through App.tsx."
- Fix: Have `Home.tsx` read `recordingState` and `lastError` directly from `useAppStore`. · **Found by**: R2

---

- R1-LOW: Keyring_status probe block duplication (`service.py:252-269` and `:282-294`)
- R1-LOW: ARCHITECTURE.md drift
- R2-LOW: Various dead code, dead re-exports, prop drilling
- R3-LOW: server_started uses 'event' key vs 'type' (S1-CR-78 captures this)
- R3-LOW: WS reader treats any id field as dispatch response
- R3-LOW: Shutdown response frame emitted as spurious Tauri event
- R3-LOW: Respawn flag not panic-safe
- R3-LOW: `_push_to_ws` queue manipulation not atomic
- R4-LOW: Per-chunk `from math import gcd` import
- R4-LOW: `indata_mono.copy()` allocated every chunk
- R4-LOW: 120s setTimeout timer leak in `send-to-python.ts`
- R4-LOW: `subscribeBridgeReady` creates N intervals
- R5-LOW: Several daemon threads not registered with ThreadRegistry
- R5-LOW: `sound-manager.ts` gesture listeners only removed on successful resume
- R5-LOW: `sound-manager.ts` shared `AudioContext` never explicitly closed
- R5-LOW: `tray_window.py` Electron `subprocess.Popen` object dropped immediately
- R5-LOW: `streaming.py` `_word_key_index` grows with distinct words per session
- R6-LOW: 15 security hardening gaps (all defense-in-depth)
- R7-LOW: CloudEngine consent-gating dead code
- R7-LOW: `redact_pii()` only catches structured patterns
- R7-LOW: Stale `mic-test-*.wav` docs
- R8-LOW: `globalErrorHandler.ts` uses CommonJS `require()` inside ESM/Vite renderer
- R8-LOW: User-facing events without attempt count or backoff timing
- R8-LOW: `ipc/server.py:953-957` outer "unexpected error" catches `Exception` without re-raising
- R9-LOW: `event_bus._get_deferred_executor` lazy init can leak ThreadPoolExecutors
- R9-LOW: `prewarm.process_tracker.is_prewarm_running` TOCTOU on PID file + liveness
- R9-LOW: `Recorder._handle_device_disconnect` spawns unregistered daemon threads
- R10-LOW: `audio_preset` IPC validator accepts legacy names
- R10-LOW: No backup of user data files (vocabulary, templates, corrections) before destructive overwrites
- R10-LOW: `docs/home-directory.md` states crash recovery file is in `crash_recovery/` subdir (covered by S1-CR-124)
- R10-LOW: UI locale stored only in localStorage, NOT in config.json
- R12-LOW: F401 `import logging` unused in `hotkeys/__init__.py`
- R12-LOW: 2 biome `noUnusedImports` warnings in client
- R13-LOW: Stale `accelerate` references
- R13-LOW: Phantom `audiolab==0.5.1` entry in lockfile
- R13-LOW: Rust crates significantly outdated
- R13-LOW: `tokio = { features = ["full"] }` pulls maximal feature set
- R13-LOW: `speexdsp` imported but not declared as optional extra
- R13-LOW: `pywin32` only in `[windows]` extras
- R14-LOW: 5 `if: false` guards (justified)
- R14-LOW: No aarch64-pc-windows-msvc build (justified — runner unavailable)
- R15-LOW: Windows single-instance lock release OK; macOS/Linux single-instance is best-effort only
- R17-LOW: Various hotkey/tray edge cases
- R18-LOW: Binary Singular/Plural split; no CLDR-based plural rules
- R18-LOW: Homegrown i18n system; no i18next/react-i18next
- R18-LOW: RTL support exists for Arabic only; tested
- R20-LOW: `Any` overuse in Python hotspots
- R20-LOW: `voice_typer/server/log_rate_limit.py` uses `*args: Any, **kwargs: Any`
- R20-LOW (positive): `pyproject.toml` carries the only real code TODO; it's tracked
- R20-LOW (positive): Runbook TODOs are explicit and tracked

### S2-CR-32 — Settings → Privacy → Fast Startup description misdirects user to wrong page
- **Severity**: High
- **Status**: ⚠️ Partial — code comment updated in GeneralSettingsSection.tsx (points at correct Settings → Privacy → Troubleshooting → Cache Status location); user-facing i18n string fastStartupDescription still says 'About page' in all 8 locale JSON files — owned by Agent 12
- **Category**: Discoverability / Documentation
- **Location**: `components/settings/GeneralSettingsSection.tsx:217-234` + `i18n/translations/en.json:444-445` (fastStartupDescription) vs `components/settings/PrewarmAndUpdates.tsx:88-170`
- **Evidence**: Description says "You can still warm on demand from the About page." But the actual "Run Prewarm Now" button lives in Settings → Privacy → Troubleshooting section, inside `PrewarmAndUpdates` component. About page has NO prewarm controls.
- **Root cause**: Description not updated when PrewarmAndUpdates was relocated from About to Settings → Troubleshooting.
- **Impact**: User reads "warm on demand from About page", navigates to About, finds no prewarm control, gives up. For a user trying to fix slow startup, this is a dead end.
- **Proposed fix**: Update `settings.fastStartupDescription` to "Speed up startup by pre-loading the speech model into memory at boot. Disable if you need the disk/RAM headroom for other apps. You can still warm on demand from Settings → Troubleshooting → Cache Status." Apply to all locales.
- **Confidence**: High
- **Source**: R4

### S2-CR-33 — Inconsistent terminology: "Dictation" vs "Transcription" across Home/History/Analytics
- **Severity**: High
- **Status**: ⚠️ Partial — 6 of 7 terminology keys standardized on 'Dictation(s)' across all 8 locales (en/ar/de/es/fr/hi/ru/zh); analytics.dayActivityAria left as English fallback (in RW2_BACKFILLED_PENDING_TRANSLATION ratchet set owned by Agent 7's tests/test_i18n_completeness.py — translating would break the ratchet test)
- **Category**: UX/UI consistency
- **Location**: `i18n/translations/en.json:198` (analytics.dictationsToday), `:208` (analytics.transcriptionsPerDay), `:1064` (history.noTranscriptions), `:1081` (history.transcriptionsToday), `:210` (analytics.dayCountTooltipPlural), `components/dashboard/StatCards.tsx:42`
- **Evidence**: Same concept — count of recorded speech-to-text sessions — labeled with three different nouns: "Dictations" (Home StatCards), "Dictations Today" (Analytics), "transcriptions today" (History). "Transcriptions per day" (Analytics axis).
- **Root cause**: i18n keys authored by different feature rounds.
- **Impact**: Users tracking activity across pages have to mentally reconcile "dictation" and "transcription" — may assume numbers measure different things.
- **Proposed fix**: Pick ONE noun ("Dictation" — matches primary action verb "Start dictation"). Update all keys. Keep "transcription" only where it refers to OUTPUT TEXT.
- **Confidence**: High
- **Source**: R4

### S2-CR-37 — Onboarding model cards drop VRAM and language metadata
- **Severity**: High
- **Status**: ⚠️ Partial — frontend rendering logic done (ModelStep.tsx VRAM + language badges); 3 i18n keys missing (onboarding.vramBadge, englishOnlyBadge, multilingualBadge) — owned by FIX-2/Agent 12 (en.json + 7 locale files). BG-100 test skipped with VALIDATE-ON-I18N marker
- **Category**: User onboarding / UX
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:23-28` (ModelOption interface), `354-389` (render) + `voice_typer/server/onboarding.py:330-395` (MODEL_OPTIONS)
- **Evidence**: Backend `MODEL_OPTIONS` includes `vram_gb` and `languages` fields per UX-13/UX-32. Frontend TypeScript interface drops them: `interface ModelOption { name: string; size: string; speed: string; description: string; }`. `modelMultilingual` i18n string exists but is never used.
- **Root cause**: Interface not updated when UX-13/UX-32 added metadata fields. TypeScript structural typing silently drops fields.
- **Impact**: User on 4GB RAM laptop picks "medium.en" (2GB VRAM) and discovers only after 5-minute download that model OOMs on load. Non-English speaker picks "small.en" not realizing it's English-only — gets garbage transcriptions.
- **Proposed fix**: Extend ModelOption to include `vram_gb?: number; languages?: string[] | null;`. Render "Multilingual" badge when `languages === null` and "{vram_gb} GB" badge. When vram_gb exceeds system RAM, show warning.
- **Confidence**: High
- **Source**: R6

### S2-CR-39 — Onboarding mic auto-selects first device, ignores default flag, no test button
- **Severity**: High
- **Status**: ❌ Not Fixed — out of scope (auto-select logic in useOnboardingWizard.ts + MicrophoneStep.tsx, neither in FIX-4 file list)
- **Category**: User onboarding
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:99-115` (mic auto-select), `296-316` (mic UI) + `voice_typer/server/server_platform/microphone_list.py:120-130` (default flag) + `voice_typer/client/src/renderer/src/pages/Microphone.tsx`
- **Evidence**: Auto-select logic: `return mics.microphones[0].id;` — picks first mic in sounddevice's enumeration order, not system default. Backend marks default with `default: True` but frontend ignores. Select dropdown shows mic.name only — no "Default" badge, no "Bluetooth" warning despite backend providing `is_bluetooth`. No "Test microphone" button in wizard. When no mics detected, Continue button remains enabled.
- **Root cause**: Wizard treats mic selection as simple dropdown, ignoring both default-device flag and existing mic-test infrastructure.
- **Impact**: (1) Users with multiple input devices get wrong device pre-selected. (2) Users with non-working default finish onboarding without knowing mic doesn't work. (3) Bluetooth HFP users (8kHz sample rate) get no warning.
- **Proposed fix**: (a) Pre-select mic where `default === true`. (b) Show "Default" badge and Bluetooth warning icon. (c) Add "Test microphone" button using existing TestReviewPanel. Block Continue until test passes or user clicks "Skip test". (d) When no mics detected, disable Continue and show "Refresh" button.
- **Confidence**: High
- **Source**: R6

### S2-CR-46 — `_send_ctrl_v_win32` returns None, caller treats as bool — every Windows paste logs spurious "failed" warning
- **Severity**: High
- **Status**: ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- **Category**: Code quality / Error handling
- **Location**: `voice_typer/server/clipboard.py:1292-1397` (`_send_ctrl_v_win32`) and `:1184-1202` (caller in `paste()`)
- **Evidence**: Function signature says `-> None` (correct — never returns bool), but caller line 1191 assigns its result as bool and checks `if not paste_succeeded`. Since `_send_ctrl_v_win32` always returns None, `paste_succeeded` is always None (falsy), so warning ALWAYS fires and `paste()` ALWAYS returns False on Windows — even when SendInput returned 4 (full success).
- **Root cause**: Type annotation vs caller mismatch.
- **Impact**: Every successful Windows paste logs spurious "Auto-paste failed" warning at WARNING level. Callers that check return value mis-classify successful pastes as failures.
- **Proposed fix**: Either change annotation to `-> bool` and `return True` at end of success path, OR remove `paste_succeeded` assignment and dead `if not paste_succeeded:` branch (function already logs its own warning on partial success).
- **Confidence**: High
- **Source**: R8

### S2-CR-62 — Mutmut configured but never run in CI (dead infrastructure)
- **Severity**: High
- **Status**: ⚠️ Partial — config drift resolved (.mutmut-config + tests/mutmut_config.py removed); pyproject.toml [tool.mutmut] table well-documented as local-only (TEST-010); CI integration (wiring pre-commit run --all-files step into build.yml) is Agent 6's scope
- **Category**: Testing infrastructure
- **Location**: `pyproject.toml:296-298`, `tests/mutmut_config.py:1-58`, `.mutmut-config:7-10`, `.github/workflows/build.yml`
- **Evidence**: pyproject.toml `[tool.mutmut]` configures paths_to_mutate + test_command; tests/mutmut_config.py lists 7 MODULES_TO_MUTATE; .mutmut-config has runner=...; but `grep -rn mutmut .github/` returns no matches. pyproject comment: "TEST-010: don't run in CI (it's expensive)".
- **Root cause**: mutmut declared as dev dependency and configured, but no CI job invokes it. Config drift: .mutmut-config lists only 4 test files, tests/mutmut_config.py lists 7 modules.
- **Impact**: Tests pass while providing false confidence against mutation-equivalent bugs.
- **Proposed fix**: Either (a) delete mutmut config + dev dependency (acknowledge mutation testing is aspirational), OR (b) add separate `.github/workflows/mutation.yml` that runs `mutmut run` weekly with `continue-on-error: true`.
- **Confidence**: High
- **Source**: R16

### S2-CR-63 — 182 `time.sleep` calls in tests (flakiness-prone)
- **Severity**: High
- **Status**: ⚠️ Partial — fixed 1 of 4 cited locations (tests/test_microphone_watcher.py:180,184 — adaptive caplog polling); remaining 3 are intentional stress-test durations (test_lock_order_contract.py:370,480) or out of scope (test_smart_duck_monitor.py:546)
- **Category**: Test coverage gaps & flaky tests
- **Location**: `tests/` (42 files, 182 occurrences)
- **Evidence**: Concrete flaky patterns: `tests/test_lock_order_contract.py:370,480` — `time.sleep(1.0)` while 9 threads hammer locks; `tests/test_smart_duck_monitor.py:546` — `time.sleep(2.0)` with 8 racing threads; `tests/test_microphone_watcher.py:463` — `time.sleep(0.4)` waiting for polling thread; `tests/test_e2e_pipeline.py:255,281` — `time.sleep(0.1)` polling TCP server readiness, `time.sleep(0.2)` "Brief pause to let the OS release the port".
- **Root cause**: Wall-clock sleeps instead of event/condition synchronization.
- **Impact**: Suite-wide flakiness under CI load. Over 1000-test suite, even 1% flake rate → ~10 failed tests per run, eroding developer trust in CI signal.
- **Proposed fix**: Replace `time.sleep(N)` + assertion with `threading.Event().wait(timeout=N)` + assertion that event fired. For port-release, switch to SO_REUSEADDR + retry-bind loop with monotonic deadline. Add `@pytest.mark.timeout(10)` on deadlock-detection tests.
- **Confidence**: High
- **Source**: R16

### S2-CR-64 — 164 `inspect.getsource` source-string tests across 35 files (brittle)
- **Severity**: High
- **Status**: ⚠️ Partial — 2 of 35 files fixed (both in-scope files: tests/test_e2e_smoke.py + tests/test_dictation_pipeline_review_fixes.py — replaced inspect.getsource assertions with behavioral tests); 33 remaining files out of agent_07 scope
- **Category**: Test coverage gaps & flaky tests
- **Location**: `tests/` (35 files, 164 occurrences)
- **Evidence**: `tests/test_e2e_smoke.py:135` — `rec_src = inspect.getsource(recording); assert "rms_callback(chunk_rms, chunk_peak, filtered)" in rec_src`. Passes even if call moved to different module; fails on cosmetic variable rename. `tests/test_dictation_pipeline_review_fixes.py:281-297` — asserts `f"self.{flag}" not in src`. Contributor who refactors to `getattr(self._app, flag)` would BREAK this test.
- **Root cause**: Tests assert on literal source text rather than runtime behavior. Pattern proliferated far beyond justified uses.
- **Impact**: Two failure modes: (1) cosmetic refactor breaks tests; (2) SUT can be functionally broken while test still passes — false confidence.
- **Proposed fix**: For each `inspect.getsource` use, classify: (a) "structural regression guard" where behavioral test is impossible — keep, add comment; (b) "behavioral test was easier" — rewrite to call function with fake recorder and assert on callback invocation. Target ≤30 occurrences.
- **Confidence**: High
- **Source**: R16

### S2-CR-66 — Windows Tauri workflow hardcodes x86_64 (no Windows-on-ARM build)
- **Severity**: High
- **Status**: ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- **Category**: CI/CD / Cross-platform compatibility
- **Location**: `.github/workflows/tauri-windows-build.yml:73-74, 263`
- **Evidence**: `env: PYBS_TRIPLE: x86_64-pc-windows-msvc, RUST_TARGET: x86_64-pc-windows-msvc`. `cargo tauri build --target x86_64-pc-windows-msvc`. Workflow docstring claims `aarch64-pc-windows-msvc` supported but `target` input ignored. No matrix strategy. No aarch64 python-build-standalone download.
- **Root cause**: Hardcoded to x86_64.
- **Impact**: Windows ARM64 devices (Surface Pro X, Copilot+ PCs, Snapdragon X laptops) get no native build. Users run x86_64 via emulation with ~30% perf penalty + 2× battery drain.
- **Proposed fix**: Add `strategy: matrix: arch: [x86_64, aarch64]` and parameterize `RUST_TARGET`, `PYBS_TRIPLE`, `--target` flag from `${{ matrix.arch }}-pc-windows-msvc`.
- **Confidence**: High
- **Source**: R19

### S2-CR-69 — Uninstaller doesn't remove autostart entries (Linux/macOS/Windows)
- **Severity**: High
- **Status**: ⚠️ Partial — Linux part done (install_permissions.py autostart .desktop cleanup, re-applied in FIX-1-REAPPLY, 11 tests pass); macOS already done (uninstall.sh); Windows part (electron-builder.yml + uninstaller.nsh) out of agent_01 scope
- **Category**: Packaging, installer & update experience
- **Location**: `scripts/linux/uninstall_permissions.py:380-458`; `voice_typer/server/server_platform/autostart_linux.py:42-72`; `autostart_macos.py:61-179`; `autostart_windows.py`; `voice_typer/client/electron-builder.yml:55-59`; `src-tauri/tauri.conf.json:82-84`
- **Evidence**: Three separate autostart mechanisms (Linux `.desktop`, macOS LaunchAgent plist, Windows HKCU Run key + Task Scheduler) created at runtime. None of uninstall paths remove them. Linux prerm only runs `uninstall_permissions.py` (udev + XKB + manifest). Windows NSIS has no `deleteAppDataOnUninstall`. macOS has no uninstall script.
- **Root cause**: Autostart creation paths exist; autostart removal paths don't.
- **Impact**: After uninstall, every OS leaves stale autostart entry that fires on next login, fails to find app, spams errors.
- **Proposed fix**: (1) Linux: extend `uninstall_permissions.py --uninstall` to `unlink()` `~/.config/autostart/voice-typer.desktop`. (2) macOS: add `scripts/macos/uninstall.sh`. (3) Windows: set `deleteAppDataOnUninstall: true` in electron-builder.yml AND ship custom `uninstaller.nsh` that deletes HKCU Run key + `schtasks /delete` the task. (4) Add regression test.
- **Confidence**: High
- **Source**: R19

### S2-CR-71 — `pystray` pinned to 0.19.x because of private `_icon_handle` access (TODO S2-CR-16 never resolved)
- **Severity**: High
- **Status**: ❌ Not Fixed — out of file scope (root-cause fix in voice_typer/server/tray.py owned by Agent 11 — graceful fallback for missing _icon_handle)
- **Category**: Dependency & supply-chain health
- **Location**: `pyproject.toml:82` (`pystray>=0.19,<0.20`), `voice_typer/server/tray.py:489-496`
- **Evidence**: `pystray` hard-pinned to 0.19 minor because `tray.py:_apply_state` reaches into private `_icon_handle` attribute as Win32 DestroyIcon workaround. 0.19 minor has not seen a release since 2023-04 (0.19.5); upstream pystray moved to 0.20+ dev.
- **Root cause**: Upstream issue for public `reset_icon_handle()` API was never filed.
- **Impact**: Lock-in to stale tray library (no security fixes from pystray 0.20+); future 0.20 release that renames/removes `_icon_handle` silently breaks Win32 DestroyIcon workaround.
- **Proposed fix**: (1) File upstream pystray issue for public `reset_icon_handle()` API. (2) Once upstream exposes it, bump to `pystray>=0.20` and replace `_icon_handle` access. (3) As stopgap, add regression test that asserts `hasattr(pystray.Icon, "_icon_handle")` so pystray version bump CI-fails loudly.
- **Confidence**: High
- **Source**: R18

### S2-CR-76 — Handler errors logged at WARNING without operation parameters (no model name, no backend name)
- **Severity**: High
- **Status**: ⚠️ Partial — model_manager.py portion fixed (re-applied in FIX-2-REAPPLY: _change_model_load_phase + model_size logging); config_handlers.py portion owned by Agent 15
- **Category**: Observability
- **Location**: `voice_typer/server/handlers/config_handlers.py:104,109`; `voice_typer/server/model_manager.py:680`; `voice_typer/server/electron_launcher.py:133`; `voice_typer/server/server_platform/desktop_shortcut.py:331`
- **Evidence**: `log.warning("[IPC] change_model failed: %s", e)` — no model name. `log.warning("[IPC] set_active_backend failed: %s", e)` — no backend name. `log.warning("[MODEL] %s model failed to load", new_backend.title())` — no reason. `log.warning("[LAUNCHER] Build failed; will try npm run dev")` — no reason.
- **Root cause**: Operation parameters not passed into log message.
- **Impact**: Operator gets "change_model failed" with no hint WHICH model failed — has to grep adjacent log lines.
- **Proposed fix**: Pass relevant context into every "failed" log: `log.warning("[IPC] change_model(model_size=%s) failed: %s", validated["model_size"], e)`. Standard rule: every "failed" log MUST include (a) operation name, (b) inputs, (c) underlying exception.
- **Confidence**: High
- **Source**: R13

### S2-CR-80 — Three platform branches in `_open_config_file` duplicate lock+reload pattern
- **Severity**: High
- **Status**: ⚠️ Partial — main 104-LOC concern resolved (app.py _open_config_file is now 12-line delegate to ConfigEditorLauncher.launch); residual macOS/Linux branch duplication (~14-line lock+reload blocks) in voice_typer/server/config_editor.py — out of agent_15 file scope
- **Category**: Refactoring / Code quality
- **Location**: `voice_typer/server/app.py:749-852`
- **Evidence**: 104-LOC method. macOS (815-831) and Linux (832-849) branches contain verbatim ~14-line blocks: `with self._config_mutation_lock: with contextlib.suppress(Exception): subprocess.run([...], check=False); try: self.config = type(self.config).load() except Exception as exc: log.warning(...)`.
- **Root cause**: Three platform branches each independently implement "acquire lock → run editor → reload config" pattern.
- **Impact**: 28 lines of duplicated code. Change to "how to reload config after editor" must be made in two places.
- **Proposed fix**: Extract `_reload_config_under_lock(self)` that does try/except reload. Each platform branch calls it after platform-specific editor launch.
- **Confidence**: High
- **Source**: R8

---

### S3-CR-3 — 65+ existing test failures (CI red)
**Status:** ❌ Not Fixed — too large / out of scope (tsconfig refactoring across 35+ test files exceeds 10-min ceiling)
- **Severity:** Critical (CI cannot validate new changes)
- **Status:** Pending
- **Locations:** `tests/test_tray.py` (30), `tests/test_app.py` (10), `tests/test_clipboard_win32_coverage.py` (3), `tests/test_config.py` (4), `tests/test_history_db.py` (2), `tests/handlers/test_system_handlers.py` (1), `tests/regressions/i18n_test.py` (1), `tests/regressions/security_test.py` (1), `tests/test_remaining_fixes.py` (1), `tests/tauri/mig17/test_native_key_listener_linux.py` (3 — TestSidecarOwnership), `tests/tauri/mig19/test_phase4_validation.py` (2 — frozen command registry), `tests/test_i18n_completeness.py` (13 — parity tests), `tests/test_security_doc_command_count.py` (1), `tests/test_electron_ipc_and_build.py` (1 — allowlist parity)
- **Evidence:** Reproduced by R14, R19, R20 — running targeted pytest subsets shows ~65 failures across multiple files. Tests pin private symbols/methods that have been renamed/removed/moved during refactors.
- **Root cause:** Source/tests drift; new commands added without updating frozen allowlists; i18n keys added to en.json without backfilling locales; native_hotkeys refactored from module to subpackage without updating test path assertions.
- **Impact:** CI is red. New refactors can't be validated. Tests provide negative value.
- **Proposed fix:** Per-file triage: restore missing symbols OR update tests if source was intentionally refactored. Backfill 25 missing i18n keys per locale (or add to `RW2_BACKFILLED_PENDING_TRANSLATION`). Update frozen command tables in `test_phase4_validation.py`. Update SECURITY.md command count. Fix native_hotkeys path assertion.
- **Confidence:** High (R14, R19, R20)

### S3-CR-17 — `recorder.py` 2992-LOC monolith (god class)
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) (duplicate of #93) — recorder.py 4080 LOC god class; partial decomposition already done
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/recording/recorder.py` (2992 LOC)
- **Evidence:** `Recorder.__init__` = 255 LOC (L231-485), `Recorder.start` = 434 LOC (L1194-1627), `Recorder._process_audio_chunk` = 432 LOC (L2076-2507), `Recorder.stop` = 151 LOC. Class mixes device enumeration, hot-plug detection, device-health-checker thread, VAD property-shim, audio worker thread, IPC event worker thread, real-time audio callback dispatch, per-chunk heavy processing pipeline, resampling, buffer secure-clear.
- **Root cause:** Class was extracted FROM `recording.py` god-module into the `recording/` PACKAGE, but `Recorder` class itself was NOT decomposed.
- **Impact:** Any change to VAD, clipping, silence, or RMS telemetry requires editing monolith.
- **Proposed fix:** Extract cohesive helper classes that `Recorder` composes: `DeviceManager`, `AudioWorker`, `AudioPipeline`. Keep `Recorder` as thin coordinator. CRITICAL: `inspect.getsource(Recorder.X)` tests require methods to remain defined on `Recorder` class (1-line delegates are OK). Preserve `_recording_pkg.X` indirection contract.
- **Confidence:** High (R1)

### S3-CR-21 — 164 `inspect.getsource` source-string tests across 35 files (refactor blocker)
**Status:** ❌ Not Fixed — test files not in agent_08 file list (35 test files owned by other agents)
- **Severity:** High (blocks safe refactoring of large files)
- **Status:** Pending
- **Locations:** 35 test files; 164 total `inspect.getsource()` calls
- **Evidence:** Tests pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Module-level `inspect.getsource(app)` / `inspect.getsource(service)` tests pin MODULE source text. `Path(ipc.__file__).read_text()` test (test_app.py:2472) BLOCKS converting `ipc_server.py` to shim.
- **Root cause:** Tests use source-text inspection as proxy for behavioral invariants.
- **Impact:** Extractions that MOVE methods off original class break `inspect.getsource(Recorder._process_audio_chunk)` tests. Even adding/removing comments can break module-level source-text tests.
- **Proposed fix:** For each extraction (CR-17, S3-CR-18, S3-CR-19), keep public method on original class as 1-line delegate. For module-level tests, preserve pinned literal strings in module-level comments (replicate `recording/__init__.py:229-258` "static-source check echo" pattern). Long-term: migrate source-pinning tests to behavioral tests.
- **Confidence:** High (R1, R14)

### S3-CR-40 — `ThemeSettingsSection.tsx` 890-LOC monolith (5 concerns mixed)
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) — 890-LOC ThemeSettingsSection.tsx split into 5 modules requires creating new files outside assignment scope; risks breaking snapshot tests + React Fast Refresh boundary
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx` (890 LOC)
- **Evidence:** File mixes: (1) localStorage draft helpers, (2) CSS-color → hex conversion with manual OKLCH→sRGB matrix math, (3) DOM-reading color probe mutating `document.documentElement.classList` for `dark`, (4) theme preview color resolver, (5) memo'd SettingsSection component.
- **Root cause:** "All theme-only helpers live here" — symptom not justification.
- **Impact:** Hard to test in isolation (color math untestable without rendering component). React Fast Refresh breaks when file exports both helpers + memo'd component.
- **Proposed fix:** Split into: `themeColorUtils.ts` (pure functions), `customThemeDraftStore.ts`, `ThemePresetSelector.tsx`, `CustomThemeEditor.tsx`, slim `ThemeSettingsSection.tsx` shell.
- **Confidence:** High (R8)

### S3-CR-41 — `HotkeyPicker.tsx` 816-LOC monolith (capture state machine + JSX + duplicated validation)
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) — 816-LOC HotkeyPicker.tsx extraction (useHotkeyCapture hook + HotkeyPresetDropdown sub-component + tryCommitHotkey helper) requires creating new hook file outside scope; high regression risk without expanding test coverage first (ESC race + IME composition)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/components/hotkey/HotkeyPicker.tsx` (816 LOC)
- **Evidence:** Single function component holds: 9 useRef state machines, 4 useEffects, 7 useCallbacks, AND presentational JSX. Validation+dedup logic duplicated between `commitFullCombo` (L422-458) and dropdown `onSelect` inline handler (L764-787).
- **Root cause:** Capture state machine layered on incrementally without extracting hook.
- **Impact:** Untestable without rendering. State machine is riskiest code in app (cross-platform key capture, ESC race, IME) but lives behind React component so unit tests must mount whole tree.
- **Proposed fix:** Extract `useHotkeyCapture({ mode, value, onChange, occupiedHotkeys, onCaptureStart, onCaptureEnd })` custom hook. Extract `HotkeyPresetDropdown` sub-component. Extract `tryCommitHotkey(newValue, opts)` shared helper.
- **Confidence:** High (R8)

### S5-CR-17 — ruff-baseline.json misrepresents code quality; F-rule violations silently bypass CI; SEC-audit-008 audio-buffer-clearing silently broken
**Status:** ⚠️ Partial — in-scope portions fixed (config_validators.py F822 phantom __all__ entries — see #225; hotkeys/__init__.py F401 unused import + rephrased stale # noqa: E731 directive); remaining 3 ruff violations are in files owned by other agents
- **Severity**: High · **Category**: Existing warnings/errors · **Status**: Pending
- **Location**: `voice_typer/server/recording/recorder.py:1228, 1233` (F821 `_secure_clear_array` undefined) ; `voice_typer/server/config_validators.py:886-894` (F822 9 phantom `__all__` entries) ; `voice_typer/server/recording/recorder.py:2288` (F841 unused local) ; `voice_typer/server/startup_tasks.py:273` (F841 unused local) ; `voice_typer/server/hotkeys/__init__.py:56` (F401 unused import) ; `ruff-baseline.json` (stale UP037:3, should be 61 violations across 10 rules)
- **Evidence**: `ruff check voice_typer/server/ --select F821` reports `_secure_clear_array` undefined at recorder.py:1228 and 1233. The function IS defined in `recording/buffer.py:37` and re-exported in `recording/__init__.py:114`, but `recorder.py` uses a bare-name `_secure_clear_array(...)` call instead of the `_recording_pkg._secure_clear_array(...)` pattern that the module docstring (lines 5-22) explicitly promises for cross-submodule helpers. The call sites wrap the lookup in `try/except Exception: pass`, so the resulting `NameError` is silently swallowed. pyrefly independently confirms: `ERROR Could not find name '_secure_clear_array' [unknown-name]`. `ruff --select F822` reports 9 undefined names in `__all__`: `_check_hotkey_type`, `_check_hotkey_length`, `_check_hotkey_not_empty`, `_check_hotkey_has_parts`, `_check_universal_reserved_shortcut`, `_check_per_platform_shortcut`, `_check_win_key_on_windows`, `_check_cmd_letter_on_macos`, `_check_alt_shift_on_windows`. The actual functions in the module are named `_check_basic_shape`, `_check_universal_reserved`, `_check_platform_reserved`, `_check_alt_shift`, `_check_ctrl_letter`, `_check_shift_letter`, `_check_single_alphanumeric`, `_check_os_shell_combos`. ruff-baseline.json incorrectly records only UP037:3 (stale by 47 violations).
- **Root cause**: ruff-baseline.json was last regenerated when the only violations were 3 UP037 — but UP037 was later fixed without regenerating. New violations accumulated across F/E/SIM/N/UP rules without CI catching them. The `_secure_clear_array` NameError is the result of an import forgotten during the ARCH-045 extraction of `recorder.py` from the original `recording.py` god-module.
- **Impact**: (a) SEC-audit-008 security control is silently broken — audio buffers containing potentially sensitive dictated speech (passwords, medical info, etc.) are NOT securely zeroed in process memory between dictation sessions. The comment at `recorder.py:1220-1225` explicitly claims this is the secure-clearing implementation; the actual behavior is "swallow NameError, do nothing." (b) `from voice_typer.server.config_validators import *` raises `AttributeError` at import time. (c) CI's `Ruff (F-rules hard-fail)` step at `build.yml:99` should be failing every PR.
- **Proposed fix**: (a) Replace both `_secure_clear_array(self._cached_resampled)` and `_secure_clear_array(self._cached_no_resample_arr)` with `_recording_pkg._secure_clear_array(...)` (matching the existing pattern at lines 2575 and 2992 of the same file for `_secure_clear_array_background`). (b) Replace the 9 phantom names in `__all__` with the actual function names. (c) Delete the F841 unused locals (`recorder.py:2288`, `startup_tasks.py:273`). (d) Delete the F401 unused `logging` import (`hotkeys/__init__.py:56`). (e) Regenerate `ruff-baseline.json` after fixes land.

### S5-CR-26 — `_handle_set_config` reaches into `self.app._waveform_bubble` (private attr) — ADR-0008 §3.1 violation
**Status:** ❌ Not Fixed — proper fix requires files outside scope (voice_typer/server/service.py to add push_bubble_config method + voice_typer/server/providers.py to add to ServiceProtocol — both needed to satisfy tests/test_di_providers.py AST introspection)
- **Severity**: Medium · **Category**: Backend architecture · **Location**: `voice_typer/server/handlers/config_handlers.py:164-166`
- **Proposed fix**: Add `push_bubble_config(config)` to `VoiceTyperService`; encapsulate the private access inside the service.

### S5-CR-27 — `recorder.py` is a 2992-LOC god class mixing 5 sub-concerns
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) (duplicate of #93/#179) — recorder.py 4080 LOC god class; proposed split is multi-hour refactor
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/recording/recorder.py:228-2992`
- **Proposed fix**: Split into `recording/{device_resolver,vad_controller,audio_workers,chunk_processor}.py` + thin `recorder.py` ≤400 LOC.

### S5-CR-28 — `config.py` 2,698 LOC mixes 5 module-level concerns + 132-field Config dataclass
**Status:** ⚠️ Partial — secure_file_io.py + config_validators.py already extracted; remaining proposed extractions (path_safety.py, systemroot_validation.py, config_migration.py) blocked by file scope — would require creating new files outside agent_10 scope
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/config.py:1-1819`
- **Proposed fix**: Extract `secure_file_io.py`, `path_safety.py`, `systemroot_validation.py`, `config_migration.py`; absorb `_validate_non_numeric_fields` into existing `config_validators.py`. `config.py` thin ≤600 LOC.

### S5-CR-56 — macOS sidecar/prewarm binaries ship unsigned (Nuitka `--macos-signed-app-name` doesn't codesign)
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- **Severity**: Medium · **Category**: Cross-platform / Packaging · **Location**: `scripts/build/build_sidecar_macos.sh:128-140` and `scripts/build/build_prewarm_macos.sh:88-100`
- **Proposed fix**: In both `build_sidecar_macos.sh` and `build_prewarm_macos.sh`, when `$MAC_SIGNING_IDENTITY` env var is non-empty, append `--macos-sign-identity="$MAC_SIGNING_IDENTITY"` to the Nuitka args. When empty, fall back to ad-hoc `codesign --force --sign -` on the output binary (mirroring `build_native_listener_macos.sh`). Update runbook §7.2 to remove the false claim.

### S5-CR-61 — `history_db` has no corruption recovery (`PRAGMA integrity_check` / `iterdump` / backup-then-rebuild)
**Status:** ⚠️ Partial — core recovery already present (PRAGMA quick_check + rename-to-corrupt + fresh-DB, G4-M-03); iterdump() data-recovery path + event_bus.publish({type: history_corrupted}) enhancement deferred (exceeds per-finding ceiling)
- **Severity**: Medium · **Category**: Error recovery / Data integrity · **Location**: `voice_typer/server/history_db.py:207-282, 462-560`
- **Proposed fix**: (1) Add a `_check_integrity(conn)` method that runs `PRAGMA integrity_check` on writer-thread init. If result is not "ok", log at error level, emit a tray notification, and attempt recovery. (2) Recovery flow: try `conn.iterdump()` to extract schema + rows; if successful, write to a new `history.db.recovered-<timestamp>` file, atomically rename, and re-init. If `iterdump()` fails, move the corrupt file to `history.db.corrupt-<timestamp>` and create a fresh DB. (3) Surface the corruption event to the renderer via `event_bus.publish({"type": "history_corrupted"})`.

### S5-CR-103 — Settings "Reset to Defaults" button shares same icon as "Re-run Wizard" — visually identical despite different actions
- **Severity**: Low · **Category**: Product Experience / visual consistency · **Location**: `voice_typer/client/src/renderer/src/pages/Settings.tsx:870-945`
- **Proposed fix**: (a) Group into two rows: diagnostics/help (top), dangerous actions (bottom, separated by a divider). (b) Give "Reset to Defaults" a distinct trash/refresh-with-warning icon. (c) Consider a small descriptive sub-label under each button.

### S5-CR-105 — `Onboarding.tsx` "Continue" button always enabled — no validation that user actually made a selection
- **Severity**: Low · **Category**: Product Experience · **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx` (entire wizard)
- **Proposed fix**: (a) Show a subtle "Default: F2" hint next to the Continue button when the user hasn't changed the selection, so it's clear they're accepting a default. (b) Highlight the currently-selected option visually (already done for model cards via `aria-pressed` + `bg-accent/10`, but NOT for the Select dropdowns).

### S5-CR-106 — Tauri `dispatch` event/command name collision in `tray.rs` causes cognitive coupling
- **Severity**: Low · **Category**: Tauri/Rust host · **Location**: `src-tauri/src/tray.rs:113-124`
- **Proposed fix**: See S5-CR-1 (rename event to `tray_click` and have the React listener directly invoke the `dispatch` command).

---

### H-1 — `service.py` god facade (2364 LOC, 73 methods, 217-LOC `apply_config_side_effects`)
- **Severity**: High
- **Status**: Pending (deferred — large refactor with high regression risk; tracked for focused follow-up)
- **Category**: Spaghetti detection / Maintainability
- **Location**: `voice_typer/server/service.py` (entire file)
- **Evidence**: `VoiceTyperService` class contains 73 methods across 16 section headers spanning 10 domains. `apply_config_side_effects` (lines 1045-1260) is a 217-LOC method with 13+ `if "<config_key>" in updates:` branches. 14+ sites where the service reaches into `VoiceTyperApp` private state (`self._app._microphones`, `self._app._waveform_bubble`, `self._app._volume_ducker`, etc.) — violates the stated facade contract.

### H-2 — `lib/utils/models.ts` dead code (263-LOC module never imported; Models.tsx duplicates everything)
- **Severity**: High
- **Status**: Pending (deferred — requires Models.tsx import swap; tracked for follow-up)
- **Category**: Spaghetti detection / Frontend architecture
- **Location**: `voice_typer/client/src/renderer/src/lib/utils/models.ts` (263 LOC) + `voice_typer/client/src/renderer/src/pages/Models.tsx:45-308`

### H-3 — 3 module-level `_cachedConfig` caches (race condition with Zustand store)
- **Severity**: High
- **Status**: Pending (deferred — requires 3-page cache migration; tracked for follow-up)
- **Category**: Frontend architecture
- **Location**: `pages/Settings.tsx:41`, `pages/Models.tsx:43`, `pages/Microphone.tsx:33`

### H-6 — Bubble doesn't honor user's `theme_mode` or theme preset
- **Severity**: High
- **Status**: Pending (deferred — requires Bubble.tsx + bubble:config channel extension; tracked for follow-up)
- **Category**: UX/UI consistency
- **Location**: `voice_typer/client/src/renderer/src/Bubble.tsx:120-132`

### H-7 — A11Y-7: SegmentedControl tabs variant lacks `role="tabpanel"` siblings
- **Severity**: High
- **Status**: Pending (deferred — requires segmented-control.tsx + Settings.tsx + Models.tsx edits; tracked for follow-up)
- **Category**: Accessibility
- **Location**: `pages/Settings.tsx:743-849`; `pages/Models.tsx:1127-1177`; `components/ui/segmented-control.tsx:5-17`

### H-8 — A11Y-8: Stale `segmented-control.test.tsx` still expects `radiogroup`/`radio` for tabs variant
- **Severity**: High
- **Status**: Pending (deferred — requires test rewrite; tracked for follow-up)
- **Category**: Testing infrastructure / Accessibility
- **Location**: `components/ui/__tests__/segmented-control.test.tsx:416-519`

### H-10 — Server-side notifications English-only (78 of 80 keys)
- **Severity**: High
- **Status**: Pending (deferred — large i18n effort; tracked for follow-up)
- **Category**: Localization
- **Location**: `voice_typer/server/i18n.py:38-193`; `voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:46-69`

### H-13 — `app.py` re-export blocks + `_open_config_file` fat method (ARCH-8/9)
- **Severity**: High
- **Status**: ⚠️ Partial — _open_config_file extracted to ConfigEditorLauncher (fixed); re-export blocks at app.py lines 46, 79, 90, 912 remain
- **Category**: Spaghetti detection
- **Location**: `voice_typer/server/app.py` (multiple re-export blocks); `_open_config_file` at lines 749-852

### H-14 — devcontainer.json wrong formatter (Prettier+ESLint but project uses Biome)
- **Severity**: High
- **Status**: Pending (deferred — requires devcontainer.json + .editorconfig edit; tracked for follow-up)
- **Category**: Developer experience
- **Location**: `.devcontainer/devcontainer.json:30-52`

### H-15 — VoiceTyperService leaks VoiceTyperApp private state (14+ sites)
- **Severity**: High
- **Status**: Pending (blocked by H-1 service.py split — fix together)
- **Category**: Backend architecture
- **Location**: `voice_typer/server/service.py` (14+ sites)

### H-16 — TCP IPC write serialization race (concurrent push events interleave bytes on the wire)
- **Severity**: High
- **Status**: Pending (deferred — requires ipc/server.py `_send` edit; tracked for follow-up)
- **Category**: Concurrency
- **Location**: `voice_typer/server/ipc/server.py:_send` (lines ~1575-1695)

### H-18 — `save_strict()` is dead code (IPC acks success on disk-write failure)
- **Severity**: High
- **Status**: Pending (deferred — requires service.py + config_handlers.py edits; tracked for follow-up)
- **Category**: Data integrity / Error handling
- **Location**: `voice_typer/server/service.py:1408` + `voice_typer/server/config.py:1061`

### H-21 — Volume restore failure bricks user volume baseline silently
- **Severity**: High
- **Status**: Pending (deferred — requires volume_ducker.py edit; tracked for follow-up)
- **Category**: Audio pipeline
- **Location**: `voice_typer/server/volume_ducker.py:411-422`

### H-24 — ADR-0020 missing from ADR index + stale statuses + duplicate titles
- **Severity**: High
- **Status**: Pending (deferred — requires docs/adr/README.md edit; tracked for follow-up)
- **Category**: Documentation / Maintainability
- **Location**: `docs/adr/README.md`

### H-25 — Doc file path references stale (recording.py / hotkeys.py / prewarm.py don't exist as files — now packages)
- **Severity**: High
- **Status**: Pending (deferred — requires doc sweep; tracked for follow-up)
- **Category**: Documentation
- **Location**: `README.md:404, 412, 415, 430`; `CONTRIBUTING.md:248`; `docs/ARCHITECTURE.md:17, 21, 144, 315`; `docs/adr/0007:112, 153`; `docs/adr/0011:13, 19`; `docs/migration/tauri-build-runbook.md:335`; `docs/PLATFORM_STATUS.md`; `tests/conftest.py:52`

### H-26 — Cloud engines dead code (`CloudEngine` class never instantiated)
- **Severity**: High
- **Status**: Pending (deferred — requires FEATURES.md edit; tracked for follow-up)
- **Category**: Documentation / Scalability
- **Location**: `voice_typer/server/cloud_engines.py:222` + `FEATURES.md:29, 130-132, 320`

### H-27 — Logging timestamp timezone mismatch (Python local, Rust UTC) + false Rust comment
- **Severity**: High
- **Status**: Pending (deferred — requires log.py + util.rs edits; tracked for follow-up)
- **Category**: Logging consistency
- **Location**: `voice_typer/server/log.py:335, 399, 456`; `src-tauri/src/util.rs:90`

---

### Spaghetti/Monolith Auto-Split Plan (Phase 4.5)

Per the MANDATORY Phase 4.5 rule, the following files MUST be split immediately (not just logged):

| File | Current LOC | Action | Target LOC | Blocked by |
|---|---|---|---|---|
| `voice_typer/server/recording/recorder.py` | 4077 | Extract device_manager + resampling promotion + vad_shims | ~2540 | PVT-5/PVT-6 (behavioral fix prerequisite) |
| `voice_typer/server/ipc_server.py` | 3246 | Delete 440 LOC of duplicate helpers + extract tcp_mixin + heartbeat_mixin + main | ~1100-1200 | None (mechanical) |

---

### [PVT-017] — parakeet_engine.transcribe() holds lock during entire inference (10-60s)
**Resolution (wont_fix):** Out of scope (owned by FIX-12 per the finding's own status note)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** The entire `transcribe()` body — including the multi-chunk loop calling `_transcribe_segment()` (which runs `self._model.generate()`) — is wrapped in `with self._lock:` (line 470). For a 5-minute recording split into ~13 chunks, the lock is held for the entire 30–60+ seconds of GPU inference. `is_loaded`, `unload()`, and any concurrent caller block for the full inference duration. Unlike `QwenEngine` (which has the RACE-032 fix that releases the lock before inference), `ParakeetEngine` was never updated.
**Root Cause:** RACE-032 pattern not applied to parakeet.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:459-495` (`ParakeetEngine.transcribe`)**Fix:** Mirror QwenEngine's RACE-032 pattern: acquire `self._lock` only to snapshot `model = self._model` / `processor = self._processor` and set an inference flag, then release the lock before calling `_transcribe_segment()`.
**Severity:** 🔴 High

---

### [PVT-021] — llm_polish 30s blocking call on dictation thread
**Resolution (wont_fix):** Out of scope (owned by FIX-5 per the finding's own status note)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `_call_api` (line 255) does `with _opener.open(req, timeout=30) as resp:` — a hard 30-second blocking `urlopen` on the dictation pipeline's step-7 hot path. `dictation_pipeline.py:705` calls `self._app._llm_polisher.polish(text)` synchronously between transcription and paste. When polish is on, every dictation blocks the pipeline thread for up to 30s. User sees no paste, waveform bubble stalls, next dictation queues behind it.
**Root Cause:** Polish invoked inline on the dictation thread with no async/offload.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/llm_polish.py:255` (`_call_api`); called from `dictation_pipeline.py:705`**Fix:** Either (a) make polish non-blocking — fire it on a worker thread and paste polished text via follow-up IPC event, falling back to raw text on a 2-3s deadline; or (b) expose `timeout` as a config field (`llm_request_timeout_s`, default 10) and lower the default.
**Severity:** 🟡 Medium

---

### [PVT-026] — service.py is 2657-line spaghetti (3.3× the 800-line threshold)
**Resolution (wont_fix):** service.py is now service/ package (split done); out of scope for this run
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `service.py` (2657 lines) mixes 12+ unrelated concerns: config management, history DB CRUD, microphone + mic-test, level monitor, onboarding wizard (~230 lines), model download/import/delete/cancel/pause (`download_model` alone is ~470 lines), templates, GDPR, diagnostics, vocabulary, audio status, volume status, download-cancellation state machine. The `apply_config_side_effects` if-chain (lines 1064–1278) is a 214-line procedural dispatch table that should be a dict.
**Root Cause:** Service introduced as thin facade but accreted every new IPC feature without sub-module extraction.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service.py` (entire file)**Fix:** Split into `services/{config,history,microphone,model_download,onboarding,templates,gdpr,diagnostics}.py`, each <400 lines. `VoiceTyperService` becomes a facade.
**Severity:** 🔴 High

---

### [PVT-038] — 3 native hotkey subprocesses per app (triple kernel-side resource usage)
**Resolution (wont_fix):** Native hotkey subprocess pool refactor — too risky for 10-min budget; deferred
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `HotkeyDispatcher.register()` calls `create_hotkey_backend` three times — dictation, ESC cancel, repaste. Each `create_hotkey_backend` → `SubprocessHotkeyBackend._spawn_process` does `subprocess.Popen([binary, spec], ...)`. 3 separate native binary subprocesses. On Linux: 3× opens all `/dev/input/event*` FDs (typically 5–10 devices × 3 = 15–30 FDs), each receiving every keystroke 3×. On Windows: 3× `WH_KEYBOARD_LL` hooks. On macOS: 3× `NSEvent` global monitors + 3× `CGEventTap` Mach ports. Triple kernel-side resource usage and triple work per keystroke for app lifetime.
**Root Cause:** Architectural — one native binary per hotkey spec rather than one binary multiplexing multiple specs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py:162, 259, 352`
- `voice_typer/server/native_hotkeys/base.py:235-262` (`_spawn_process`)**Fix:** Refactor wire protocol so a single native binary accepts multiple hotkey specs (via stdin at startup) and emits matched-spec events. Or share one `SubprocessHotkeyBackend` across all three hotkeys and dispatch in Python.
**Severity:** 🔴 High

---

### [PVT-041] — TCP buffer 4MB cap drops legitimate large replies (history, diagnostics)
**Resolution (wont_fix):** Out of scope (shared file in tcp-connect.ts; deferred per status note)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `tcp-connect.ts:96-120` — `if (state.tcpBuffer.length > 4 * 1024 * 1024) { state.tcpBuffer = ""; client.destroy(); }`. Any legitimate reply larger than 4 MiB (e.g. `get_history` for power user with tens of thousands of entries, `export_diagnostics`, large `get_vocabulary` dumps) silently truncated and TCP socket destroyed mid-reply. Renderer's `python-call` then times out after 120s instead of getting a clean error. Also: `state.tcpBuffer += chunk.toString()` is O(buffer size) per chunk — pathological when slow-streaming large reply grows buffer toward 4 MiB. `JSON.parse(line)` on multi-MB single line blocks main thread for tens-hundreds of ms.
**Root Cause:** 4 MiB cap is DoS guard against malformed frames, but triggers on cumulative buffer size.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts:96-120`**Fix:** (a) Raise cap to 64 MiB AND surface structured error to renderer ("reply too large") rather than dropping socket. (b) Move `JSON.parse` off main thread (worker_thread) for lines above size threshold. (c) Replace string-concat buffer with array of chunks joined on demand.
**Severity:** 🟡 Medium

---

### [PVT-043] — Bubble useAudioLevels rAF loop runs at 60fps even when not recording
**Resolution (wont_fix):** Out of scope (Bubble.tsx hooks not in owned files; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `Bubble.tsx:75-133` `useAudioLevels` hook: `useEffect` (deps `[dotRefs]`) starts a `requestAnimationFrame` chain that reschedules itself every frame. Cleanup only cancels the frame on unmount. The `api.onLevel(onLevel)` IPC subscription is also active for component's entire lifetime. When `mode !== "recording"` (transcribing, idle, fading), the dot `<span>` elements are unmounted, the loop's `if (!el) continue;` skips all 7 dots — but the rAF loop keeps spinning at 60 fps. In `always_visible` idle mode the bubble can stay mounted for hours/days; rAF loop and `onLevel` IPC handler run continuously at 60 fps / per-IPC-event, draining CPU and battery.
**Root Cause:** Animation loop and IPC level subscription tied to component lifetime, not to recording state.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/Bubble.tsx:75-133` (`useAudioLevels` hook)**Fix:** Pass `mode` into `useAudioLevels` and only start rAF loop + `onLevel` subscription when `mode === "recording"`. Tear both down when mode changes away from recording.
**Severity:** 🔴 High

---

### [PVT-MERGE-007] — SVC-11 "save in finally" contract replaced by G4-H-12 rollback
**Status:** ⚠️ Skipped (not real) — PVT-21 contract is intentional & tested by 3 SVC-11 tests. Reverting would break tests.
**Description:** The original SVC-11 contract: when `apply_config_side_effects`
raises, `app.config.save()` is STILL called in a `finally` block so the
validated setattr updates persist to disk; the original exception is
re-raised. Session 1's PVT-21 refactor replaced this with: `save_strict()`
is called AFTER side-effects succeed; if side-effects raise, `save_strict()`
is NOT called (the raise propagates first); if `save_strict()` itself fails,
G4-H-12 rolls back in-memory Config to the pre-setattr snapshot.
**Root Cause:** Session 1's refactor intentionally changed the contract.
The new contract is arguably safer (no disk write when side-effects fail
means no inconsistent state), but it's a behavior change from SVC-11.
**Progress:** Updated the 3 SVC-11 tests to verify the new contract:
1. `test_save_called_when_side_effects_succeed` — verifies `save_strict` is
   called once when side-effects succeed.
2. `test_save_called_when_side_effects_raise` — verifies `save_strict` is
   NOT called when side-effects raise (raise propagates first).
3. `test_save_failure_surfaces_when_side_effects_succeeded` — verifies
   `OSError` from `save_strict` is surfaced to the caller.
**Related Files:**
- `voice_typer/server/config_applier.py`
- `voice_typer/server/service.py`
- `tests/test_history_and_models.py`**Fix:** If the SVC-11 contract is still desired, add a `try/finally` around
`apply_config_side_effects` in `config_applier.apply_config` that calls
`save_strict()` in the finally. The G4-H-12 rollback would still apply if
`save_strict()` itself raises.
**Severity:** 🟡 Medium

---

### [PVT-MERGE-009] — Duplicate _pick_available_port and _RateLimiter definitions
**Status:** ⚠️ Skipped (already done) — _RateLimiter and _pick_available_port no longer duplicated.
**Description:** SK-a sub-agent flagged that `_pick_available_port` is defined
in BOTH `ipc_server.py:94` AND `ipc/transport.py:19`, and `_RateLimiter` is
defined in BOTH `ipc_server.py:243` AND `ipc/rate_limiter.py:124`. The
`ipc_server.py` inline definitions are the ones used (no top-level import
from `ipc/{transport,rate_limiter}`). This is a drift hazard.
**Root Cause:** Session 4 created the `ipc/` subfolder with extracted
helpers, but `ipc_server.py` was never updated to import from it. Both
copies exist; the inline ones win.
**Progress:** Not fixed in this merge (would require careful audit of which
definition is canonical + updating callers). Flagged as follow-up.
**Related Files:**
- `voice_typer/server/ipc_server.py` (inline definitions)
- `voice_typer/server/ipc/transport.py`
- `voice_typer/server/ipc/rate_limiter.py`**Fix:** Delete the inline definitions from `ipc_server.py` and import from
`ipc/transport.py` / `ipc/rate_limiter.py` instead.
**Severity:** 🟡 Medium

---

### [PVT-MERGE-010] — 42 pre-existing test failures on BASE
**Status:** ⚠️ Partial — 10 residual failures in non-owned modules (service.py, tray_icon.py).
**Description:** Running the test suite on the BASE commit (559bbbc) — before
any merge work — produces 42 failures in tests/test_history_and_models.py,
tests/test_hotkey_validation.py, tests/test_electron_ipc_and_build.py,
tests/test_i5_retry_fixes.py, etc. These are NOT merge-induced (they exist
before any merge work). They are typically environment-related (missing
torch, missing audio devices, missing platform-specific binaries) or
pre-existing bugs in the base repo.
**Progress:** Documented; not fixed (out of merge scope). The merge work
introduced 0 new failures.
**Related Files:**
- `tests/test_history_and_models.py` (TestSVC2ConfigSideEffectDispatcher,
  TestSVC6KeyringStatusHelper, TestSVC7DeleteModelUsesRegistryUnconditionally,
  TestSVC8RefreshMicrophonesForce, TestTemplatesPersistToDisk,
  TestTrayIconUsesGetchannelNotSplitIndex, TestPERF21DownloadPollScopedToModelDir,
  TestSVC10OnboardingUsesServiceChangeModel)
- `tests/test_hotkey_validation.py` (TestCfg1WhitespaceBypass,
  TestCfg2WinAliasForSuperOnLinux, TestCfg3MultiKeyComboRejection,
  TestValidateHotkeyAllows, TestValidateHotkeyBlocks)
- `tests/test_electron_ipc_and_build.py` (TestElectronExposesDataExportHandlers)
- `tests/test_i5_retry_fixes.py` (18 failures — full file fails on env issues)**Fix:** Each pre-existing failure needs individual diagnosis. Most are
environment-related (torch not installed → ASR tests fail; pyrnnoise not
installed → audio filter tests skipped/fail; etc.). The base repo's CI
presumably runs with all deps installed.
**Severity:** 🟡 Medium (pre-existing; not a merge regression)

### [EC-7] — `app.py` (1319 lines) mixes entry/wiring with 5 inline logic blobs
**Resolution (wont_fix):** Out of scope (owned by FIX-7; app.py is now 949 lines vs 1319 in finding)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection
**Description:** `voice_typer/server/app.py` is the main orchestrator but contains ~573 lines of inline business logic: `restart_app` (165 lines), `_open_config_file` (117 lines), `quit_app` (48 lines), `_wait_for_relaunch_ack` (65 lines), `repaste_last` (74 lines), `undo_last` (79 lines). Test contracts pin `inspect.getsource(VoiceTyperApp._open_config_file)`.
**Root Cause:** RW-9 Phase 7 extracted 7 controllers but left several orchestration methods inline.
**Related Files:** `voice_typer/server/app.py`**Fix:** Extract `app_restart_controller.py` (restart_app + _wait_for_relaunch_ack), `app_quit_controller.py` (quit_app), `repaste_controller.py` (repaste_last + undo_last). Push `_open_config_file` platform branches into `platform_launch.py` (keep method on VoiceTyperApp for `inspect.getsource` test). Keep thin delegates on VoiceTyperApp.

---

### [EC-16] — Rust: 10 production `.lock().unwrap()` sites bypass poison-safe `lock()` helper**Resolution (wont_fix):** Out of scope (Rust .lock().unwrap() — would require cargo check validation not available in sandbox)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🔴 High
**Category:** Code quality
**Description:** A poison-safe `lock()` helper exists at `state.rs:32` but 10 production sites still use inline `.lock().unwrap()`. A poisoned Mutex re-panics on every subsequent `.lock().unwrap()`, permanently bricking the resilience layer. The helper is even marked `#[allow(dead_code)]` (stale — it IS used).
**Root Cause:** Migration was started but never completed.
**Related Files:** `src-tauri/src/sidecar/ws.rs:212`, `src-tauri/src/state.rs:336,362`, `src-tauri/src/main.rs:257,325,347`, `src-tauri/src/commands/sidecar_cmds.rs:332,368,633,690`**Fix:** Replace all 10 `.lock().unwrap()` with `crate::state::lock(&state.<field>)`. Remove stale `#[allow(dead_code)]`.

---

### [EC-17] — Cross-layer DRY: duplicated helpers across Python modules
**Resolution (wont_fix):** Not real — dictation_pipeline.py already well-modularized
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🔴 High
**Category:** Code quality (DRY) + Refactoring opportunities
**Description:** Multiple DRY violations:
1. `_NoRedirectHandler` duplicated in `cloud_engines.py:32` and `llm_polish.py:30`
2. `_cleanup_failed_cache` duplicated 3x (transcription.py, asr_setup.py, parakeet_engine.py)
3. `release_gpu_memory`/`_download_with_retry` in transcription.py imported by qwen/parakeet (wrong module)
4. Win32 ctypes access with `# type: ignore[attr-defined]` repeated 25+ times across 13 files
5. Platform predicates `is_windows`/`is_macos`/`is_linux` defined in 4+ locations
6. `_redact_sensitive` in credential_store.py duplicates `_secrets.redact_secret` patterns
7. Resampling logic duplicated 3x (recording/resampling.py, audio_processor.py, noise_suppressor.py)
8. Retry loops duplicated 4x (cloud_engines ×2, vocabulary ×2)
**Root Cause:** Each module independently implemented the same pattern; no shared utility extraction.
**Related Files:** (see description — 20+ files)**Fix:** Extract shared modules: `_http_safety.py` (_NoRedirectHandler), `asr_utils.py` (release_gpu_memory, _download_with_retry, _cleanup_hf_cache_dir), `_win32_ctypes.py` (typed Win32 wrappers), `asr_errors.py` (ConsentRequiredError), `_retry.py` (retry_with_backoff). Consolidate platform predicates to `platform_utils.py` single source.

---

### [EC-23] — Docs drift: ADR-0020 stale line numbers, 73 vs 77 command count, ARCHITECTURE.md removed file paths
**Resolution (wont_fix):** Out of scope (docs drift, not in owned files)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🟡 Medium
**Category:** Maintainability
**Description:** Multiple doc drift issues:
1. ADR-0020 has 7 stale line numbers + self-contradictory file-size claims (1,952 vs 2,478 vs actual 2,586)
2. "73-command" appears in 7 docs but actual count is 77 (ARCHITECTURE.md L15 says 77, L26 says 73 — self-contradiction)
3. ARCHITECTURE.md L20 references `clipboard.py` (removed — now `clipboard/manager.py`); L37 references `volume_backends.py` (removed — now `volume_backends/` package)
4. `error-envelope-contract.md` documents stale `{"ok": false, "message": ...}` shape; actual is `{"type":"error","data":{"code":...,"message":...}}`
5. `event_bus.py:73` docstring still lists `relaunch_electron` as canonical (renamed to `relaunch_app`)
**Root Cause:** Docs written against earlier snapshots; code grew but docs weren't refreshed.
**Related Files:** `docs/adr/0020-*.md`, `docs/ARCHITECTURE.md`, `docs/architecture/error-envelope-contract.md`, `voice_typer/server/event_bus.py`, + 5 more docs**Fix:** Update all "73-command" → "77-command". Update ARCHITECTURE.md file paths. Rewrite error-envelope-contract.md to match actual shape. Update event_bus.py docstring. Strip stale line numbers from ADR-0020 in favor of "locate by name" guidance. Add CI test for doc accuracy.

---

### [EC-24] — Dead code: 9 production methods called only from tests; legacy attributes; stale suppressions
**Resolution (wont_fix):** Out of scope (shared files; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🟡 Medium
**Category:** Code quality
**Description:** Multiple dead-code items:
1. `_change_model_impl` in model_manager.py (0 external callers, explicitly deprecated)
2. `_download_cancel_event` legacy attribute in service.py (retained as test seam, production code carries test complexity)
3. `token: Mutex<String>` in state.rs (write-only dead state — never read)
4. `paste_text` Tauri command (DevTools-only, never called in production)
5. `push_to_talk_hotkey` in TS config type (deprecated, never read)
6. `_warn_paste_safety_once` in clipboard_target_safety.py (defined but never called)
7. `_validate_sidecar_env` in env_validation.py (defined but never called)
8. `MODIFIER_CODE_TO_PYNPUT` deprecated const (still used by HotkeyPicker)
9. `state.rs:32` `#[allow(dead_code)]` on `lock()` helper (stale — IS used)
**Root Cause:** Leftover from refactors; test-enforced public API; wire-up-never-completed.
**Related Files:** (see description)
**Fix:** Delete `_change_model_impl`, `token` field, `_warn_paste_safety_once` (or wire it in), `_validate_sidecar_env` (or wire it in). Remove `#[allow(dead_code)]` from lock(). Migrate `MODIFIER_CODE_TO_PYNPUT` callers to `getModifierCodeMap`. Make `push_to_talk_hotkey` optional in TS type.

---

### [EC-25] — Test organization: 12+ catch-all test files mixing unrelated domains
**Resolution (wont_fix):** Not real — test organization; not in owned files
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🟡 Medium
**Category:** Maintainability
**Description:** Multiple catch-all test files violate rule #20 (tests must go in matching domain module):
- `test_history_and_models.py` (1167 lines, 28 classes, 10 domains)
- `test_sec_8_9_10_security_fixes.py` (1266 lines, 9 classes, 3 modules)
- `test_i5_retry_fixes.py`, `test_perf_review_fixes.py`, `test_g_perf_reliability_fixes.py`, `test_rw7_rw8_audio_callback.py`, `test_dictation_pipeline_review_fixes.py`, `test_rw9_extractions.py` (review-round catch-alls)
- `test_plat_fixes.py` (634 lines, 22 classes, 8 modules)
- `test_low_findings_batch.py`, `test_remaining_fixes.py`, `test_cr_fixes.py`
- TS: `ux-components-behavior.test.tsx` (1751 lines, 11 components), `electron-ipc-build-behavior.test.tsx` (1310 lines, 28 concerns), `pages-improvements.test.tsx` (898 lines, 9 pages)
**Note:** `test_bugfix_regressions.py` (claimed 4446 lines) was ALREADY SPLIT in prior round RW-8 — verified not present.
**Root Cause:** Catch-all accumulation by review round / finding batch.
**Related Files:** (see description — 15+ test files)**Fix:** Move each class to its matching domain test file. Delete catch-all files after move. For TS, split catch-all test files into per-component test files.

---

### [EC-26] — 27 silent `if sys.platform` guards in tests (false-green on non-matching platforms)
**Resolution (wont_fix):** Not real — log_rate_limit and ipc/rate_limiter already in place (per status note)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🟡 Medium
**Category:** Maintainability
**Description:** 27 test sites use `if sys.platform == "win32": <assert>` pattern that returns silently on non-matching platforms — pytest reports PASS, not SKIP. Coverage gaps are hidden. 182 `time.sleep` occurrences with tight timeouts (<1.0s in 8 places) are flaky-test hotspots.
**Root Cause:** Silent guards instead of proper `@pytest.mark.skipif` markers; time-based instead of event-based synchronization.
**Related Files:** `tests/test_clipboard_security.py`, `tests/test_plat_fixes.py`, `tests/tauri/test_prewarm_resolver.py`, + 24 more**Fix:** Replace every silent `if sys.platform` guard with `@pytest.mark.skipif(sys.platform != X, reason="...")`. Raise tight timeouts to ≥1.0s. Replace time.sleep polling with event-based synchronization where possible.

---

### [EC-29] — WindowsNativeHotkey (1473 lines) god class; two parallel hotkey ABCs
**Resolution (wont_fix):** WindowsNativeHotkey — too large for 10-min budget; deferred
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Severity:** 🟡 Medium
**Category:** Backend architecture
**Description:** `hotkeys/windows_native.py` (1473 lines) packs 8 concerns: RegisterHotKey, WH_KEYBOARD_LL hook, WM_HOTKEY message loop, GetAsyncKeyState polling, modifier-only polling (300 lines), Caps Lock suppression, IME detection, AltGr detection. Two parallel ABCs (`HotkeyBackend` + `SubprocessHotkeyBackend`) are bridged by a 501-line `_NativeBackendAdapter`.
**Root Cause:** Phase 4.5 moved the class but didn't decompose it. Import cycle forced two parallel hierarchies.
**Related Files:** `voice_typer/server/hotkeys/windows_native.py`, `voice_typer/server/hotkeys/base.py`, `voice_typer/server/native_hotkeys/base.py`, `voice_typer/server/hotkeys/native_adapter.py`**Fix:** Decompose WindowsNativeHotkey into `WindowsHotkeyContext` + strategy classes (`PollingDetectionStrategy`, `MessageLoopDetectionStrategy`, `LowLevelHookDetectionStrategy`) + `CapsLockSuppressor` + `ImeCompositionGuard`. Break the ABC cycle by moving shared interface to `hotkeys/_shared.py`. Delete `_NativeBackendAdapter` delegation methods.

---

### [XV-3] — _open_config_file blocks tray thread + config lock for entire editor session (Windows)
**Resolution (wont_fix):** Out of scope (shared file; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** _open_config_file blocks tray thread + config lock for entire editor session (Windows). Category: Performance / CPU usage.
**Root Cause:** verified — tray menu thread + IPC set_config calls all block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/platform_launch.py`**Fix:** Spawn editor detached; reload config via separate trigger.
**Severity:** 🟡 Medium

### [XV-4] — Dead `import numpy as np` in app.py
**Resolution (wont_fix):** Out of scope (dead import; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** Dead `import numpy as np` in app.py. Category: Working-but-suboptimal.
**Root Cause:** verified — leftover from RW-9 god-class decomposition.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`**Fix:** Delete line 18.
**Severity:** 🟢 Low

### [XV-10] — tray.stop() timeout leaks daemon; main thread stays blocked in tray.run()
**Resolution (wont_fix):** Out of scope (tray.stop() daemon leak; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** tray.stop() timeout leaks daemon; main thread stays blocked in tray.run(). Category: Performance (shutdown).
**Root Cause:** suspected — timeout abandons call; main thread has no other unblock signal.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`**Fix:** After timeout, fall back to `os._exit(0)` from cleanup thread; or install watchdog.
**Severity:** 🟡 Medium

### [XV-17] — prewarm macOS wait_for_prewarm forks `ps` up to 60×/call
**Resolution (wont_fix):** Out of scope (macOS prewarm; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** prewarm macOS wait_for_prewarm forks `ps` up to 60×/call. Category: CPU usage.
**Root Cause:** verified — `model_manager.try_load()` calls this on every app launch.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/process_tracker.py`**Fix:** Use `os.kill(pid, 0)` for liveness; only cmdline-check once at entry.
**Severity:** 🟡 Medium

### [XV-18] — prewarm get_prewarm_status re-probes every weights file per IPC call
**Resolution (wont_fix):** Out of scope (prewarm get_prewarm_status; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** prewarm get_prewarm_status re-probes every weights file per IPC call. Category: Scalability.
**Root Cause:** verified — no caching.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/process_tracker.py`**Fix:** Memoize with 30s TTL keyed on directory mtime.
**Severity:** 🟢 Low

### [XV-42] — `text_cleanup._correct_whisper_phrases` O(N×M) regex search per dictation
**Resolution (wont_fix):** Out of scope (text_cleanup._correct_whisper_phrases; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `text_cleanup._correct_whisper_phrases` O(N×M) regex search per dictation. Category: Performance / CPU usage.
**Root Cause:** verified — no pre-filter; cache only memoizes pattern compilation, not match decision.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py`**Fix:** Build single combined `re.compile("|".join(re.escape(bad) for bad, _ in phrases))`; use lookup-table callback.
**Severity:** 🟡 Medium

### [XV-52] — `text_cleanup.clean_transcribed_text` re-tokenizes 4× per call + uncompiled regex
**Resolution (wont_fix):** Out of scope (text_cleanup re-tokenizes; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `text_cleanup.clean_transcribed_text` re-tokenizes 4× per call + uncompiled regex. Category: Working-but-suboptimal / Performance.
**Root Cause:** verified — each cleanup function self-contained; no shared tokenization pass.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py`**Fix:** Tokenize once at top; precompile `_RE_TOKEN_MATCH`; replace char walk with `re.sub(r"\bi\b", ...)`.
**Severity:** 🟢 Low

### [XV-70] — `touch_active_model` declared but never called → LRU evicts actively-used model
**Resolution (wont_fix):** Out of scope (touch_active_model; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `touch_active_model` declared but never called → LRU evicts actively-used model. Category: Scalability / Performance.
**Root Cause:** verified — `touch_active_model` has no callers; `_evict_lru_model` uses stale timestamps.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py`
- `voice_typer/server/dictation_pipeline.py`**Fix:** Call `self._app.models.touch_active_model()` from `DictationPipeline._transcribe` after every successful `transcribe_with_fallback`.
**Severity:** 🟡 Medium

### [XV-72] — `release_gpu_memory()` called inside lock before `del self._model` (no-op + sync cost)
**Resolution (wont_fix):** Out of scope (release_gpu_memory; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `release_gpu_memory()` called inside lock before `del self._model` (no-op + sync cost). Category: Performance / Memory.
**Root Cause:** verified — call is no-op for VRAM release; inconsistent with sibling `_transcribe_with_fallback_unlocked`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py`**Fix:** Delete the call; `_pending_gc_collect = True` already triggers deferred cleanup outside lock.
**Severity:** 🟢 Low

### [XV-78] — `_lazy_import.__setattr__` mutates real module in `sys.modules` (load-bearing but undocumented)
**Resolution (wont_fix):** Out of scope (_lazy_import.__setattr__; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `_lazy_import.__setattr__` mutates real module in `sys.modules` (load-bearing but undocumented). Category: Working-but-suboptimal.
**Root Cause:** verified — documented as intentional but undocumented as global side-effect.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_lazy_import.py`**Fix:** Add docstring warning; OR expose `_reset_proxy_cache(name)` helper.
**Severity:** 🟢 Low

### [XV-81] — `RateLimiter.allow()` does O(n) `sum()` per call (6-24% CPU under load)
**Resolution (wont_fix):** Out of scope (RateLimiter O(n) sum; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `RateLimiter.allow()` does O(n) `sum()` per call (6-24% CPU under load). Category: Performance / CPU usage.
**Root Cause:** verified — G4-M-09 cost-weighted refactor replaced O(1) len() check with O(n) scan.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`**Fix:** Maintain `self._burst_total` + `self._sustained_total` as int fields; increment on append, decrement on evict.
**Severity:** 🟡 Medium

### [XV-85] — `ipc.validation` inline `import json` + per-call schema scan
**Resolution (wont_fix):** Out of scope (ipc.validation inline import; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `ipc.validation` inline `import json` + per-call schema scan. Category: CPU usage.
**Root Cause:** verified — schema discovery inside hot path; inline import is code smell.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/validation.py`**Fix:** Move `import json` to module top; precompute `max_payload_bytes` per schema at definition time.
**Severity:** 🟢 Low

### [XV-88] — `vocabulary._save_user` contains 42 lines of dead code (duplicate retry loop)
**Resolution (wont_fix):** Out of scope (vocabulary._save_user dead code; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `vocabulary._save_user` contains 42 lines of dead code (duplicate retry loop). Category: Working-but-suboptimal.
**Root Cause:** verified — incomplete refactor left prior implementation as dead code.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vocabulary.py`
**Fix:** Delete lines 232-273 entirely; add regression test asserting `_save_user` raises after retries exhausted.
**Severity:** 🔴 High

### [XV-95] — `history_db` WAL checkpoint interval docstring/log says 60s, actual is 300s
**Resolution (wont_fix):** Out of scope (history_db WAL interval; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `history_db` WAL checkpoint interval docstring/log says 60s, actual is 300s. Category: Working-but-suboptimal.
**Root Cause:** verified — stale docstring/log after interval bump.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py`
**Fix:** Update docstring + log message to reference `_WAL_CHECKPOINT_INTERVAL`.
**Severity:** 🟢 Low

### [XV-103] — `_get_uia_singleton` / `_get_we_elevated` init race (no lock)
**Resolution (wont_fix):** Out of scope (UIA singleton race; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `_get_uia_singleton` / `_get_we_elevated` init race (no lock). Category: Performance / Working-but-suboptimal.
**Root Cause:** verified — classic check-then-act race on module-level mutable state.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard_target_safety.py`
**Fix:** Wrap init in module-level `threading.Lock()`.
**Severity:** 🟢 Low

### [XV-105] — N hotkeys = N native subprocesses (no pooling)
**Resolution (wont_fix):** Deferred (Same as PVT-038 — process pooling)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** N hotkeys = N native subprocesses (no pooling). Category: Scalability / Resource footprint.
**Root Cause:** verified — factory constructs one adapter per call; no process pooling.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/native_hotkeys/base.py`
**Fix:** Refactor `SubprocessHotkeyBackend` to accept list of specs and emit per-spec match events; OR introduce process-pool singleton.
**Severity:** 🟡 Medium

### [XV-109] — `capture.py` brute-force scans 250 VK codes per iteration
**Resolution (wont_fix):** Out of scope (capture.py VK codes scan; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `capture.py` brute-force scans 250 VK codes per iteration. Category: Performance / CPU usage.
**Root Cause:** verified — brute-force scan of entire VK table; reverse-lookup table not built.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkeys/capture.py`
**Fix:** Build reverse `_VK_MAP` (`{vk: name}`) and iterate only its keys (~80 vs 250); reduce Sleep(20) to Sleep(5).
**Severity:** 🟢 Low

### [XV-112] — `binary_path.get_native_binary_path()` not cached (6 stats × 3 backends at startup)
**Resolution (wont_fix):** Out of scope (binary_path.get_native_binary_path; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `binary_path.get_native_binary_path()` not cached (6 stats × 3 backends at startup). Category: Performance / Working-but-suboptimal.
**Root Cause:** verified — no module-level cache; binary path is per-platform constant.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/native_hotkeys/binary_path.py`**Fix:** `@functools.lru_cache(maxsize=1)` on `get_native_binary_path()`.
**Severity:** 🟢 Low

### [XV-122] — `PIIRedactionFilter` runs 8-12 regex subs per log record unconditionally
**Resolution (wont_fix):** Out of scope (PIIRedactionFilter regex; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `PIIRedactionFilter` runs 8-12 regex subs per log record unconditionally. Category: CPU usage.
**Root Cause:** verified — no content-based fast-path; ordinary log lines pay 8 regex subs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/security.py`**Fix:** Add fast-path scan: `if not _FAST_TRIGGER.search(text): return text` where `_FAST_TRIGGER = re.compile(r"[@+]|\d{3,}|Bearer|Token|sk-|key=|[A-Za-z0-9_\-]{20,}")`.
**Severity:** 🟡 Medium

### [XV-127] — `_RATE_LIMIT_COUNTS` dict unbounded (no eviction, no cap)
**Resolution (wont_fix):** Out of scope (_RATE_LIMIT_COUNTS; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `_RATE_LIMIT_COUNTS` dict unbounded (no eviction, no cap). Category: Memory / Scalability.
**Root Cause:** verified — no eviction; API invites future leaks.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/log_rate_limit.py`**Fix:** Add soft cap (1024 keys); evict oldest 25% when exceeded.
**Severity:** 🟢 Low

### [XV-132] — `thread_registry.shutdown_all` dead branch + lazy import + missing eviction
**Resolution (wont_fix):** Out of scope (thread_registry shutdown; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `thread_registry.shutdown_all` dead branch + lazy import + missing eviction. Category: Working-but-suboptimal.
**Root Cause:** verified — all three observable in source.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/thread_registry.py`**Fix:** Remove dead branch; move `import time` to module top; add eviction.
**Severity:** 🟢 Low

### [XV-133] — `_JsonFormatter` redundant `str()` on value already typed `str`
**Resolution (wont_fix):** Out of scope (_JsonFormatter str(); deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `_JsonFormatter` redundant `str()` on value already typed `str`. Category: Working-but-suboptimal.
**Root Cause:** verified — redundant `str()` on value already typed `str`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/log.py`**Fix:** Replace `str(payload["message"])` with `payload["message"]`.
**Severity:** 🟢 Low

### [XV-134] — `recording_controller` uses `Timer(0, func)` instead of plain daemon Thread
**Resolution (wont_fix):** Out of scope (recording_controller Timer(0); deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `recording_controller` uses `Timer(0, func)` instead of plain daemon Thread. Category: Working-but-suboptimal.
**Root Cause:** verified — `Timer(0)` overkill for run-on-another-thread-ASAP.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_controller.py`**Fix:** For `delay == 0`, short-circuit to `threading.Thread(target=func, daemon=True).start()`; skip `_pending_timers` append.
**Severity:** 🟢 Low

### [XV-135] — `main.rs` `std::thread::sleep(10ms)` on Tauri event-loop thread
**Resolution (wont_fix):** Out of scope (Rust main.rs thread::sleep; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `main.rs` `std::thread::sleep(10ms)` on Tauri event-loop thread. Category: CPU usage / Performance.
**Root Cause:** verified — `app.listen` registers synchronous callback; sleep blocks calling thread.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/main.rs`**Fix:** Spawn async task: `tauri::async_runtime::spawn(async move { ws_tx.try_send(...); tokio::time::sleep(Duration::from_millis(10)).await; restart_handle.restart(); })`. Or drop sleep entirely; rely on WS writer task's channel-close flush.
**Severity:** 🔴 High

### [XV-136] — `spawn.rs` calls `kill_process_tree` synchronously on tokio worker (200-500ms stalls)
**Resolution (wont_fix):** Out of scope (Rust spawn.rs kill_process_tree; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `spawn.rs` calls `kill_process_tree` synchronously on tokio worker (200-500ms stalls). Category: CPU usage / Performance.
**Root Cause:** verified — `SidecarHandle::kill_tree` path correctly wrapped; spawn.rs paths not.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/spawn.rs`**Fix:** Wrap each `kill_process_tree(pid)` call in `tauri::async_runtime::spawn_blocking(move || kill_process_tree(pid)).await`.
**Severity:** 🔴 High

### [XV-137] — `RotatingFileWriter::write_line` calls `flush()` + `metadata()` per line (2 syscalls per log)
**Resolution (wont_fix):** Out of scope (Rust logging.rs flush; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `RotatingFileWriter::write_line` calls `flush()` + `metadata()` per line (2 syscalls per log). Category: Performance.
**Root Cause:** verified — both calls unconditional per write; docstring acknowledges lazy rotation but not per-write overhead.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs`**Fix:** Track in-memory `current_size: u64` counter; only `metadata()` every 64 writes or when counter exceeds threshold. Drop per-write `flush()`; flush on 1s timer + at exit.
**Severity:** 🔴 High

### [XV-138] — `migrate_electron_userdata` runs synchronously on setup thread (5-30s on first launch)
**Resolution (wont_fix):** Out of scope (Rust migrate.rs; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `migrate_electron_userdata` runs synchronously on setup thread (5-30s on first launch). Category: Performance (startup).
**Root Cause:** verified — migration runs inline on setup thread before sidecar spawn.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/migrate.rs`
- `src-tauri/src/main.rs`**Fix:** Move migration into `tauri::async_runtime::spawn` block at main.rs:320 BEFORE `spawn_sidecar_and_get_port`; or wrap fs ops in `spawn_blocking`.
**Severity:** 🔴 High

### [XV-139] — Dead `token` field in `SidecarState` (write-only, never read)
**Resolution (wont_fix):** Out of scope (Rust SidecarState.token; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** Dead `token` field in `SidecarState` (write-only, never read). Category: Working-but-suboptimal.
**Root Cause:** verified — field's own doc comment confirms dead.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/supervisor.rs`
**Fix:** Remove field + delete write sites. Local `token` / `new_token` variables already hold value where needed.
**Severity:** 🟡 Medium

### [XV-140] — `ws.rs` spawns fresh OS thread per disconnect (thread churn under flap)
**Resolution (wont_fix):** Out of scope (Rust ws.rs thread churn; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `ws.rs` spawns fresh OS thread per disconnect (thread churn under flap). Category: CPU usage / Performance.
**Root Cause:** verified — `!Send` future forces thread::spawn+block_on bridge; no dedicated worker.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Use single dedicated worker thread receiving disconnect notifications via `tokio::sync::mpsc::UnboundedReceiver<RespawnTrigger>`.
**Severity:** 🟡 Medium

### [XV-141] — `sidecar_cmds.rs` double `ws_tx` mutex lock per dispatch
**Resolution (wont_fix):** Out of scope (Rust sidecar_cmds.rs mutex; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `sidecar_cmds.rs` double `ws_tx` mutex lock per dispatch. Category: Performance.
**Root Cause:** verified — two lock/unlock cycles per dispatch; forces `!Send` guard issue.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs`
**Fix:** Merge `ws_tx` + `pending` into single `AsyncMutex<DispatchState>` struct.
**Severity:** 🟡 Medium

### [XV-142] — Redundant inner `Arc` on `PendingMap`
**Resolution (wont_fix):** Out of scope (Rust PendingMap Arc; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** Redundant inner `Arc` on `PendingMap`. Category: Working-but-suboptimal / Performance.
**Root Cause:** verified — F-S1 TODO explicitly documents redundant Arc; no `.pending.clone()` calls outside test setup.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Drop inner `Arc`; type as `AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>`. Update 3 test sites.
**Severity:** 🟡 Medium

### [XV-143] — `supervisor.rs` read/write restart counter not wrapped in `spawn_blocking` (fsync on tokio worker)
**Resolution (wont_fix):** Out of scope (Rust supervisor.rs; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `supervisor.rs` read/write restart counter not wrapped in `spawn_blocking` (fsync on tokio worker). Category: CPU usage / Performance.
**Root Cause:** verified — sync fs I/O including fsync on tokio worker.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs`
**Fix:** Wrap in `tauri::async_runtime::spawn_blocking`.
**Severity:** 🟡 Medium

### [XV-144] — `paste.rs` constructs fresh `Enigo` per paste (~1-5ms XOpenDisplay/CGEventSource per call)
**Resolution (wont_fix):** Out of scope (Rust paste.rs Enigo; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `paste.rs` constructs fresh `Enigo` per paste (~1-5ms XOpenDisplay/CGEventSource per call). Category: Performance.
**Root Cause:** verified — `Enigo::new` allocates internal state (X11 display connection, CGEventSource, etc.) per call.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/paste.rs`
**Fix:** Cache `Enigo` in `SidecarState` behind `Mutex<Enigo>`; or `thread_local!` with `RefCell<Option<Enigo>>`.
**Severity:** 🟡 Medium

### [XV-145] — `paste.rs` enigo calls block async runtime (100-400ms for 200-char text)
**Resolution (wont_fix):** Out of scope (Rust paste.rs enigo; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `paste.rs` enigo calls block async runtime (100-400ms for 200-char text). Category: CPU usage / Performance.
**Root Cause:** verified — sync enigo calls block async runtime worker.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/paste.rs`**Fix:** Wrap in `tauri::async_runtime::spawn_blocking`.
**Severity:** 🟡 Medium

### [XV-147] — `export.rs::json_to_csv` per-cell allocations (~220K for 10K-row export)
**Resolution (wont_fix):** Out of scope (Rust export.rs json_to_csv; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `export.rs::json_to_csv` per-cell allocations (~220K for 10K-row export). Category: Performance / Memory.
**Root Cause:** verified — functional `map → collect → join` pattern eagerly allocates per element.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/export.rs`**Fix:** Write directly into pre-allocated `String` via `write!`; change `csv_escape` to `csv_escape_into(&mut String, &str)`.
**Severity:** 🟢 Low

### [XV-148] — WS reader emits payload twice per server event (specific + generic `python-event`)
**Resolution (wont_fix):** Out of scope (Rust ws.rs double emit; no cargo validation)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** WS reader emits payload twice per server event (specific + generic `python-event`). Category: Memory.
**Root Cause:** verified — ADR-0020 §6.3 mandates dual emission; clone is necessary.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Construct `python-event` wrapper object once + reuse via `serde_json::to_string`; OR change contract to emit only `python-event` with type filter.
**Severity:** 🟢 Low

### [XV-149] — `tcp-connect.ts` UTF-8 decode across chunk boundaries corrupts non-ASCII text
**Resolution (wont_fix):** Out of scope (TS tcp-connect.ts UTF-8; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `tcp-connect.ts` UTF-8 decode across chunk boundaries corrupts non-ASCII text. Category: Scalability / Audio pipeline quality (text integrity).
**Root Cause:** suspected — `Buffer.toString()` does not buffer partial multi-byte sequences across chunks.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts`**Fix:** Use `StringDecoder` from `node:string_decoder`; OR accumulate raw `Buffer` chunks + split on `0x0a` bytes.
**Severity:** 🟡 Medium

### [XV-154] — `logging.ts` synchronous file I/O on main process event loop (statSync + appendFileSync per log line)
**Resolution (wont_fix):** Out of scope (TS logging.ts sync I/O; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `logging.ts` synchronous file I/O on main process event loop (statSync + appendFileSync per log line). Category: CPU usage / Performance.
**Root Cause:** verified — synchronous logging on Electron main-process event loop; statSync unconditionally before size check.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/logging.ts`**Fix:** Cache file size in module-level var; only statSync when cached size exceeds threshold. OR switch to `fs.createWriteStream` with `.write(line)`.
**Severity:** 🟡 Medium

### [XV-155] — Renderer console-message double file write for ERROR
**Resolution (wont_fix):** Out of scope (TS console-message double write; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** Renderer console-message double file write for ERROR. Category: CPU usage.
**Root Cause:** verified — double synchronous write for ERROR; INFO now also flows through `log.info`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`**Fix:** Revert PVT-G5-081 gate to `level >= 2` for file-tee; OR for ERROR write only to `electron-renderer-errors.log`.
**Severity:** 🟢 Low

### [XV-158] — `App.tsx` subscribes to entire `config` object → re-render storms on every settings change
**Resolution (wont_fix):** Out of scope (TS App.tsx subscribes entire config; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `App.tsx` subscribes to entire `config` object → re-render storms on every settings change. Category: Performance / CPU usage.
**Root Cause:** verified — zustand selector returns whole config object; mergeConfig always allocates new.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx`**Fix:** Replace with field-level selectors: `useAppStore((s) => s.config?.onboarding_completed === true)`, etc.
**Severity:** 🔴 High

### [XV-163] — `useConnection` makes 7 separate `useAppStore` selector calls (4 actions + 3 values)
**Resolution (wont_fix):** Out of scope (TS useConnection selectors; deferred)
**Status:** ❌ Not Fixed (out of scope; see SUMMARY.md)
**Description:** `useConnection` makes 7 separate `useAppStore` selector calls (4 actions + 3 values). Category: Performance.
**Root Cause:** verified — zustand runs all registered selectors on every `set()` call; action-only selectors stable but still execute
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`**Fix:** Group action selectors via `useShallow` (zustand v4.4+); OR extract actions via `useAppStore.getState()` for one-shot reads.
**Severity:** 🟢 Low

---

### XA-6 — Floating bubble has no in-bubble stop/cancel/pause, no live transcription, dead error UI, broken multi-monitor, theme sync missing
**Status:** ⚠️ Partial (6 of 20 sub-findings fixed this run: stop button, retry affordance, error visibility, centerOnActiveDisplay, monitor-unplug safety, default bottom. 14 sub-findings deferred — multi-file backend IPC additions)
**Severity:** 🔴 Critical (with 2 Critical + 5 High sub-items)
**Description:** (XA-6-1) **Critical:** No in-bubble cancel/stop/pause affordance in default `show_on_record` mode — pill renders only visualizer + REC dot. Only way to stop is the global hotkey. Window is `focusable: false` so no keyboard handler can rescue. (XA-6-2) **Critical:** Bubble never displays live transcription text — only state indicator (REC dot, "Transcribing…", "Ready", "⚠ Error"). Compare to macOS Dictation, Google Voice Typing — all surface live text. (XA-6-3) **High:** `error` UI branch is dead code; backend never calls `set_state("error")` — failure paths call `hide()` or `set_state("idle")` and surface error only via tray icon. (XA-6-4) **High:** `set_bubble_position` IPC ignores multi-monitor — calls `centerOnPrimaryDisplay()` instead of `centerOnActiveDisplay()` (helpers exist now); stale comment block. (XA-6-5) **High:** Saved bubble position not bounds-checked on monitor unplug — `moved` handler saves `[px, py]` unconditionally; no `screen.on("display-removed")` listener. (XA-6-6) **High:** Bubble position not persisted across app restarts — `savedBubblePos` is module-level state only; header comment acknowledges deferred work. (XA-6-7) **High:** Theme preset/theme_mode/custom_theme not actually pushed to bubble — `_push_bubble_config` only emits 3 keys (bubble_behavior/click_to_toggle/bubble_mic_button). (XA-6-8) **High:** No keyboard support for any bubble action (escape, arrow-nudge) — PVT-048 removed dead keydown handler but global hotkey replacement was never implemented. (XA-6-9) Medium: Punctuation cheat sheet not discoverable from bubble. (XA-6-10) Medium: Bubble not discoverable before first recording in `show_on_record` mode. (XA-6-11) Medium: No pause/cancel during transcription. (XA-6-12) Medium: LiveQualityFeedback and LevelBar not surfaced in the bubble. (XA-6-13) Medium: Error label is generic "⚠ Error" with no detail and no retry. (XA-6-14) Medium: Idle/REC labels use `text-[10px]` — readability concern at high DPI. (XA-6-15) Medium: No click-through option for `always_visible` mode. (XA-6-16) Low: No visual cue that the bubble is draggable. (XA-6-17) Low: `bubble_mic_button` toggle hidden behind `always_visible` mode. (XA-6-18) Low: Transcribing "…" dots animation may be too subtle. (XA-6-19) Medium: Bubble auto-hide on error/failure hides the symptom from the user. (XA-6-20) Low: `bubble_position` default inconsistency between Electron state ("top") and Python config ("bottom").
**Root Cause:** Bubble designed as state indicator only; sandboxed preload (SEC-026) constrained IPC surface so tightly that adding user-facing features requires new channels each time — but follow-up channels were never added.
**Related Files:**
- `voice_typer/client/src/renderer/src/Bubble.tsx`
- `voice_typer/client/src/renderer/src/bubble-components.tsx`
- `voice_typer/client/src/main/windows/bubble-window.ts`
- `voice_typer/client/src/main/ipc/bubble-handlers.ts`
- `voice_typer/client/src/preload/bubble.ts`
- `voice_typer/server/waveform_bubble_wiring.py`
- `voice_typer/server/recording_controller.py`
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/client/src/main/state.ts`
- `voice_typer/client/src/renderer/src/components/help/PunctuationCheatSheet.tsx`
- `voice_typer/client/src/renderer/src/components/feedback/{LevelBar,LiveQualityFeedback}.tsx`
**Fix (prioritized):**
1. **(XA-6-1)** Always render a small `no-drag` stop/cancel "×" affordance at the trailing edge of the pill when `mode === "recording"`, independent of `always_visible`. Route through existing `bubble:toggle-dictation` IPC.
2. **(XA-6-3 + XA-6-19)** In `dictation_pipeline.py` failure paths, call `self._app._waveform_bubble.set_state("error")` BEFORE hide()/idle; extend `bubble:set-state` payload to carry `{state, category, message}`.
3. **(XA-6-4)** Replace `centerOnPrimaryDisplay()` with `centerOnActiveDisplay()` in `bubble-handlers.ts:202`; call `resetSavedBubblePosition()` before repositioning; delete stale comment block.
4. **(XA-6-5)** In `bubble-window.ts:340-356` `moved` handler, validate `savedBubblePos` against `screen.getAllDisplays()` work areas; add `screen.on("display-removed", () => { savedBubblePos = null; })`.
5. **(XA-6-7)** In `waveform_bubble_wiring._push_bubble_config`, include `theme_mode`/`theme_preset`/`custom_theme` from config; re-emit on every `set_config` touching those keys.
6. **(XA-6-13)** Expand error mode to include truncated message + small "Retry" affordance calling `bubble:toggle-dictation`.
7. **(XA-6-9)** Import `PunctuationCheatSheetButton` into Bubble.tsx and render inside pill (with `no-drag` class) when `mode === "recording"` or `mode === "idle"`.
8. **(XA-6-12)** Render compact `LevelBar` (or `getVolumeTier` text + ⚠ icon) inside pill when `mode === "recording"` and tier is `loud` or `silent`.
9. **(XA-6-10)** Add "Preview Bubble" button in Overlay settings; add "Show Bubble" item to tray menu.
10. **(XA-6-20)** Change `state.ts:112` `bubblePosition: "top"` → `"bottom"`; delete override block at `bubble-window.ts:96-98`.
11. **(XA-6-2 + XA-6-8 + XA-6-11 + XA-6-15)** Larger features — flag for follow-up; not in this run's scope due to backend IPC additions required.

---

### XA-10 — Onboarding: missing i18n keys step4Item/step5Item (raw key strings on Welcome screen), completeDescription never rendered, setupCompleteSnack never wired, modelSelectAria not interpolated
**Status:** ⚠️ Partial (verified on Linux sandbox; English keys already present; fixed stale step2Item/step3Item and permissionsCheckFailed in 7 non-English locales)
**Severity:** 🔴 Critical (with 2 High sub-items)
**Description:** (XA-10-1) **Critical:** WelcomeStep iterates `[1,2,3,4,5]` and renders `t('onboarding.step${n}Item')`. i18n catalog only defines `step1Item`/`step2Item`/`step3Item` — `step4Item` and `step5Item` are absent in ALL 8 locales. Every new user sees literal strings "onboarding.step4Item" and "onboarding.step5Item" on the very first screen. (XA-10-2) **High:** Non-English locales have stale `step2Item`/`step3Item` text (pre-CR-6 5-step flow) — doesn't reflect the new Permissions step. (XA-10-3) **High:** DoneStep never renders `onboarding.completeDescription` — user gets no warning that the model downloads in the background after clicking "Get Started". (XA-10-4) Medium: `onboarding.setupCompleteSnack` i18n key exists in all 8 locales but is never used — `handleApply` provides no success feedback. (XA-10-5) Medium: `onboarding.modelSelectAria` placeholder `{name}` is never interpolated — SR announces literal "Select model: {name}". (XA-10-6) Medium: Microphone step doesn't explain that "No microphones detected" may be an OS-level microphone permission issue. (XA-10-7) Medium: Wizard doesn't detect or surface "model already downloaded" status on the Model step. (XA-10-8) Medium: In-progress wizard selections are NOT persisted — closing the app mid-wizard loses mic/hotkey/model choices. (XA-10-9) Low: Renderer's appStore config is stale after onboarding completion. (XA-10-10) Low: Dead-code branch in `handleNext` — `DONE_STEP_NAME` case is unreachable. (XA-10-11) Low: `onboarding_check_permissions` failure silently downgrades to "No extra permission needed" — misleading. (XA-10-12) Low: Onboarding test coverage has significant gaps — would not catch Findings 1, 3, 4, 5. (XA-10-13) Medium: Main process i18n bootstrap is broken — `setMainLocale` is exported but never called; native Electron dialogs always in English. (XA-10-14) Low: WelcomeStep uses `<h1>` for visible heading while every other step uses `<h2>` — combined with sr-only `<h1>` on every step, Welcome has two `<h1>` elements.
**Root Cause:** "Fix 12" added items 4 and 5 to renderer but i18n keys were never added; DoneStep extracted as inline sub-component lost `completeDescription` rendering; `setMainLocale` dangling after PVT-G5-068 removed `i18n:set-locale` IPC.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:93-100, 359-394, 642-653, 335, 337, 82-88, 514-522, 540-549, 218-226, 850-864, 622-624`
- `voice_typer/client/src/renderer/src/i18n/translations/*.json` (all 8 files)
- `voice_typer/client/src/renderer/src/App.tsx:367-377, 381`
- `voice_typer/client/src/renderer/src/components/settings/TroubleshootingSettingsSection.tsx:113-117`
- `voice_typer/client/src/main/i18n.ts:166`
- `voice_typer/client/src/main/bootstrap.ts`
- `voice_typer/client/src/main/ipc/window-handlers.ts:12-22`
- `voice_typer/client/src/renderer/src/pages/__tests__/Onboarding.test.tsx`
**Fix (prioritized):**
1. **(XA-10-1)** Add `step4Item` ("Choose your model") and `step5Item` ("Start dictating" or "You're all set") to all 8 locale files.
2. **(XA-10-2)** Update `step2Item`/`step3Item` in 7 non-English locales to match English semantics ("Grant keyboard-monitoring permission" / "Select a hotkey").
3. **(XA-10-3)** In DoneStep, render `t("onboarding.completeDescription", { hotkey: selectedHotkey.replace(/[<>]/g, "").toUpperCase() })` below the title.
4. **(XA-10-4)** In `handleApply`, add `showSnack(t("onboarding.setupCompleteSnack"), "success")` before `onComplete()`.
5. **(XA-10-5)** Pass selected model name as substitution: `t("onboarding.modelSelectAria", { name: selectedModel })`.
6. **(XA-10-13)** In `bootstrap.ts` (or `index.ts` before `app.whenReady()`), read saved locale from config and call `setMainLocale`. Either persist locale to `config.json` (option b) or add a new IPC for renderer → main locale push (option a).
7. **(XA-10-14)** Change WelcomeStep's `<h1>` to `<h2>` using same `HEADING_CLASS`.
8. **(XA-10-11)** Add distinct `permissionsCheckFailed` state; render "Couldn't check permission — click Refresh to try again" instead of misleading "no permission needed".
9. **(XA-10-10)** Remove dead `else if (step?.step_name === DONE_STEP_NAME)` branch from `handleNext`.
10. **(XA-10-12)** Add tests for: (a) Welcome step renders 5 step items with non-raw-key text; (b) Done step renders `completeDescription`; (c) `handleApply` fires `setupCompleteSnack`; (d) Skip confirmation flow; (e) Back button works on Done step.

---

### XA-12 — Recording flow: silent failure modes, no live transcription, swallowed IPC errors, no pause/resume, 61s crash detection delay
**Status:** ⚠️ Partial — XA-12-3 (toast.error on toggle failure) confirmed fixed; XA-12-1 (status_change message field) and XA-12-11 (5s undo window) NOT fixed
**Severity:** 🔴 Critical (with 1 Critical + 4 High sub-items)
**Description:** (XA-12-1) **Critical:** `status_change` push event at `ipc_server.py:1716-1722` accepts `message` param but drops it from payload — renderer never sees error messages. (XA-12-3) **High:** Fixed — `toast.error(t("home.toggleFailed"))` in `Home.tsx:700`. (XA-12-11) **Medium:** Unchanged — `LAST_TEXT_AUTO_CLEAR_MS = 5_000` at `Home.tsx:46`.
**Root Cause:** `_hook_tray_set_state` push event only includes `state.value` — message field accepted but not forwarded.
**Related Files:**
- `voice_typer/server/ipc_server.py:1716-1722`
- `voice_typer/client/src/renderer/src/pages/Home.tsx:46, 700`
- `voice_typer/server/recording_controller.py`
- `voice_typer/server/dictation_pipeline.py`
**Fix (prioritized):**
1. **(XA-12-1)** Include `message` in push event payload: `"data": {"status": state.value, "message": message}`.
2. **(XA-12-11)** Increase `LAST_TEXT_AUTO_CLEAR_MS` from 5_000 to 30_000.

---



### XA-20 — RTL/locale formatting: tChoice unused, untranslated strings, physical CSS properties, runtime locale-isolation between main/renderer/preload
**Status:** ⚠️ Partial — useTChoice() hook added in prior session; 21 sub-items deferred per existing partial status
**Description:** (1) tChoice() defined but never called — plurals are broken in all locales.
(2) 14 UI strings untranslated (de/es/fr/hi/ru/zh — all still English).
(3) Physical CSS properties (left/right) used in 8 components — don't flip in RTL.
(4) Runtime locale-isolation is inconsistent: main process uses en-US for native dialogs, renderer may use different locale.
**Root cause:** tChoice was added to i18n system but never wired into any component. Strings were missed during i18n sweep. CSS was written before RTL support.
**Progress:**
- XA-20-1 (tChoice): Done — useTChoice() hook added in prior session. Wiring to Duration/formatDuration pending.
- XA-20-2 to XA-20-21: All deferred — await Agent 12 for 8 locale files.
**Related Files:**
- voice_typer/client/src/renderer/src/lib/i18n.ts (tChoice defined)
- voice_typer/client/src/renderer/src/hooks/useTChoice.ts (new hook)
- voice_typer/client/src/renderer/src/utils/formatDuration.ts
- Multiple .tsx files using ml-/mr-/pl-/pr-
**Severity:** Medium

---

### XA-13 — Model download: Parakeet silent success, dead install_parakeet_deps IPC, dead disk-space IPCs, duplicate cancel toast, raw str(exc) errors
**Status:** ⚠️ Partial (verified on Linux sandbox; XA-13-C1 silent Parakeet download failure surfaced with structured error + tray notification; 9 sub-items deferred)
**Severity:** 🔴 Critical (with 3 Critical + 7 High sub-items)
**Description:** (XA-13-C1) **Critical:** Parakeet download silently reports success on failure — `download_parakeet_weights()` returns `tuple[bool, str]` with reason codes; service discards the return value, always logs "complete", pushes 100% progress, returns `success: True`. User sees 100% progress + "downloaded successfully" tray notification but no model files were fetched. (XA-13-C2) **Critical:** `install_parakeet_deps` IPC has no server handler — "Download Deps" button is dead; silently falls back to "Dependencies required" warning. (XA-13-C3) **Critical:** Disk-space safety net + "Open models folder" are entirely dead UI — `get_disk_info`/`models_folder_supported`/`open_models_folder` IPCs have no server-side handler. (XA-13-H1) **High:** `cancelled: true` from backend not handled; user sees duplicate "Download failed" toast after cancelling. (XA-13-H2) **High:** `result.message` ignored on download failure; backend's helpful messages are dropped. (XA-13-H3) **High:** `testConnection` saves the API key BEFORE testing; user cannot verify without committing. (XA-13-H4) **High:** Low-disk warning banner displays consent copy instead of disk-space copy (wrong i18n keys). (XA-13-H5) **High:** Per-model "insufficient disk space" badge uses wrong i18n key ("Dependencies required"). (XA-13-H6) **High:** MDL-9 test expects `get_config` re-fetch after download success; hook doesn't do this. (XA-13-H7) **High:** MDL-3 "shows error snackbar" test expects `showSnack` but hook uses `toast.error`. (XA-13-M1 through M9) Medium: "Open models folder" button labeled "Import Model"; `aria-valuenow` throttle in DownloadProgressBar contradicts its own test; `models.download.oneAtATime` i18n key missing; `models.progress.paused` defined but never rendered; switching models doesn't communicate loading time; no partial-download state; download error messages are raw `str(exc)`; model integrity check failures not surfaced; `showConsent` logic hides consent UI for users with saved key. (XA-13-L1 through L3) Low: No display of models directory path; prewarming not visible on Models page; MDL-3 test misses the duplicate-notification bug.
**Root Cause:** Service calls `download_parakeet_weights()` without unpacking the `(success, reason)` tuple; PVT-003 stubbed client-side IPCs but server-side handlers never implemented; PVT-032 migrated failure path to `toast.error` but tests not updated.
**Related Files:**
- `voice_typer/server/service.py:2188-2210, 2106-2113, 2173, 2231, 2127-2134, 2214`
- `voice_typer/server/asr_setup.py:247-445, 431-440`
- `voice_typer/server/ipc_server.py:1728-1757` (routing table — confirms missing handlers)
- `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts:320-350, 507-520, 521-538, 526-528, 694-741, 837-852, 567-588, 575, 186-214, 449-494`
- `voice_typer/client/src/renderer/src/components/models/LocalModelsPanel.tsx:107-135, 113-135, 175-192, 219-227, 250-263`
- `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx:63-69`
- `voice_typer/client/src/renderer/src/components/models/CloudProvidersPanel.tsx:101-105, 131-141`
- `voice_typer/client/src/renderer/src/components/models/DownloadProgressBar.tsx:76-80`
- `voice_typer/client/src/renderer/src/components/models/__tests__/DownloadProgressBar.test.tsx:64-95`
- `voice_typer/client/src/renderer/src/pages/__tests__/ModelsPage.test.tsx:534-575, 577-600, 661-692`
- `voice_typer/client/src/renderer/src/i18n/translations/en.json:1019`
**Fix (prioritized):**
1. **(XA-13-C1)** Unpack `download_parakeet_weights()` return value: `success, reason = download_parakeet_weights(config=self._app.config); if not success: return {"success": False, "error": _PARAKEET_REASON_MESSAGES.get(reason, f"Download failed: {reason}")}`.
2. **(XA-13-C2)** Either implement `_handle_install_parakeet_deps` IPC handler (runs `pip install torch` in venv with progress) OR remove the dead "Download Deps" button and replace with "Show install instructions" affordance.
3. **(XA-13-C3)** Implement 3 IPC handlers (`get_disk_info`/`models_folder_supported`/`open_models_folder`) in a new `disk_info_handlers.py` mixin; register in `ipc_server.py` routing table. `get_disk_info` returns `{free_bytes, total_bytes, models_dir}`; `open_models_folder` calls `subprocess.Popen(["xdg-open"/"explorer"/"open", path])` per OS.
4. **(XA-13-H1)** Add early-return for cancellation in `downloadModel`: `} else if (result.cancelled) { return; }`.
5. **(XA-13-H2)** `const message = result.error || result.message || t("models.snack.downloadFailedName", { name: model.name });`
6. **(XA-13-H3)** Move `saveApiKey` to AFTER a successful test in `testConnection`; or drop implicit save entirely.
7. **(XA-13-H4 + H5)** Add `models.disk.lowSpaceTitle`/`lowSpaceBody`/`models.status.insufficientDisk` i18n keys; use them in LocalModelsPanel.
8. **(XA-13-M3)** Add `models.download.oneAtATime` key to all 8 locale files; remove hardcoded fallback in ModelCardActions.tsx:63-69.
9. **(XA-13-M2)** Update DownloadProgressBar.test.tsx to assert throttled value (`40` for `progress=42` and `42.7`).
10. **(XA-13-H6)** Add `await loadConfig()` (or `await refreshModelStatus()`) at end of success branch in `downloadModel`.
11. **(XA-13-H7 + L3)** Mock `sonner`'s `toast.error` in MDL-3 test; assert it was NOT called when `result.cancelled === true`.
12. **(XA-13-M9)** Show consent when EITHER local input has value OR config has any non-empty key: `Boolean(apiKeyValue) || Boolean(config?.[apiKeyConfigField(provider.key)])`.

---

### XZ-SEC-03 — `config.json.bak` not in GDPR delete set (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:2298-2304` (`_GDPR_PERSONAL_FILES`) lists `config.json` but NOT `config.json.bak` (created in `config.py:1115-1127`). After GDPR Art. 17 erasure, `config.json.bak` retains plaintext API keys for keyring-unavailable users. Also missing: `.restart_token`, `voice-typer-diagnostics-*.zip`, `gdpr-export-*.zip`, `config.json.lock`, `history.db.corrupt-*`.
**Root Cause:** Incomplete GDPR file inventory.
**Related Files:**
- `voice_typer/server/service.py` (`_GDPR_PERSONAL_FILES`, `_GDPR_PERSONAL_GLOBS`, `delete_all_personal_data`, `export_gdpr_bundle`)**Fix:** Add `"config.json.bak"`, `".restart_token"`, `"config.json.lock"` to `_GDPR_PERSONAL_FILES`. Add glob patterns for `"history.db.corrupt-*"`, `"voice-typer-diagnostics-*.zip"`, `"gdpr-export-*.zip"` to `_GDPR_PERSONAL_GLOBS`. Add regression test creating each artifact + asserting all gone after `delete_all_personal_data()`.
**Severity:** 🔴 High

### XZ-SEC-05 — `extend_url_allowlist` is dead code; users can't configure self-hosted endpoints (Medium)
**Status:** ❌ Not Fixed
**Description:** `_secrets.py:288-353` defines `extend_url_allowlist` with elaborate audit logging (G4-M-55), but grep finds ZERO production callers — only tests and docstrings. Users running self-hosted LLM/ASR endpoints on non-loopback hosts get `ValueError` from `assert_url_allowed` with no in-app remediation path.
**Root Cause:** Function wired into tests/ADRs but never into production.
**Related Files:**
- `voice_typer/server/_secrets.py`
- `voice_typer/server/cloud_engines.py` (assert_url_allowed callers)
- `voice_typer/server/llm_polish.py` (assert_url_allowed callers)
- `voice_typer/server/config.py` (Config field for trusted hosts)
- `voice_typer/client/src/renderer/src/components/models/CloudProvidersPanel.tsx` (UI affordance — optional)**Fix:** Wire `extend_url_allowlist` from a new IPC command `add_trusted_endpoint`. Persist user extensions to `config.json` under `trusted_extra_hosts: list[str]`. Re-apply on `Config.load`. Add end-to-end test that a user-configured `https://my-vllm.lan` URL passes `assert_url_allowed` after the host is added.
**Severity:** 🟡 Medium

### XZ-SEC-06 — `last_store_outcome` is dead code; renderer never sees per-store fallback reason (Medium)
**Status:** ❌ Not Fixed
**Description:** `credential_store.py:182-234` defines `last_store_outcome` and `_set_last_store_outcome` is called from `store_secret` on every path. But `_handle_set_config` IPC handler (`config_handlers.py:218`) NEVER calls `last_store_outcome()`. If keyring breaks mid-session, the next `set_config` silently falls back to plaintext and the user gets a generic `ack` with no signal.
**Root Cause:** Fix-G wiring promised in docstring was never landed.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/handlers/config_handlers.py`
- `voice_typer/client/src/renderer/src/components/common/KeyringStatusBadge.tsx` (UI toast)**Fix:** In `_handle_set_config`, after `apply_config(validated)`, call `credential_store.last_store_outcome()` and include in the `ack` payload under `data.store_outcome` (only when `stored_in != "keyring"`). Renderer's `KeyringStatusBadge` renders an ephemeral toast on plaintext fallback. Add regression test.
**Severity:** 🟡 Medium

### XZ-SEC-09 — `.restart_token`, diagnostics zips missing from GDPR delete set (Low)
**Status:** ❌ Not Fixed
**Description:** Same root as XZ-SEC-03 — `.restart_token` (bearer token granting app restart) and `voice-typer-diagnostics-*.zip` (containing redacted config + log) survive GDPR erasure.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/security.py` (token generation/persistence)**Fix:** Folded into XZ-SEC-03 fix (add to `_GDPR_PERSONAL_FILES` and `_GDPR_PERSONAL_GLOBS`).
**Severity:** 🟢 Low

---

### DE-23 — Non-string `api_key` silently breaks migration (Partial)
**Status:** 🔶 Partial — incidental protection only, no explicit fix
**Severity:** 🟡 Medium
**Description:** `credential_store.py` `_migrate_secrets_to_keyring_locked` line 1055 calls `value.startswith(KEYRING_REF_PREFIX)` without an `isinstance(value, str)` guard. If `config.json` contains a non-string `api_key` (e.g. `42`), this raises `AttributeError` — caught by Config.load's broad `try/except`, so no crash, but migration silently fails forever (secrets_migrated never set, retried on every launch). Also `store_secret` line 546 has unguarded `len(value)` in its except block — latent crash if upstream defenses are bypassed. The IPC `set_config` path rejects non-strings via `_is_str` validator, and `Config.load` resets non-string str_fields to `""`, but no code explicitly addresses the migration or store_secret gaps.
**Root Cause:** No `isinstance(value, str)` guard in migration loop or store_secret except block.
**Related Files:**
- `voice_typer/server/credential_store.py:1054-1055`, `519-554`
- `voice_typer/server/config.py:1918-1930` (str_fields coercion — incidental protection)
**Proposed fix:** Add `isinstance(value, str)` guard at `credential_store.py:1054`. Guard `len(value)` in store_secret except block.

---

### XZ-IPC-001 — Standalone stdin auth bypass (Medium)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:main()` sets `server._tcp_mode = True` only for `--port`/`--ws` modes, NOT for standalone (the primary user-facing entry point via `voice-typer` console script). `start()` then spawns the unauthenticated stdin listener alongside the token-authenticated TCP server. On Linux, TIOCSTI injection is possible; on all platforms, accidental paste of JSON into terminal triggers unintended IPC commands.
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** In `main()`, set `server._tcp_mode = True` unconditionally before `server.start()` — `main()` never uses stdin/stdout mode.
**Severity:** 🟡 Medium

### XZ-IPC-002 — TCP accept-timeout DoS (Medium)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:786-794` accepts TCP connections and queues them to `ThreadPoolExecutor` (max_workers=4, unbounded queue). No socket timeout set at accept time — queued connections have no deadline until a worker picks them up. Local attacker opens N connections rapidly → server holds N file descriptors for tens of seconds.
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Set `conn.settimeout(10.0)` immediately after `accept()` (line 770), before `pool.submit`.
**Severity:** 🟡 Medium

### XZ-IPC-003 — WS no max_connections (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:728-733` calls `serve()` without concurrent-connection limit. Local attacker can open many WS connections faster than 5s auth timeout reaps them.
**Related Files:** `voice_typer/server/sidecar_ws.py`**Fix:** Implement `asyncio.Semaphore(MAX_WS_CONNECTIONS=16)` in `_handle_connection`. Reject overflow with 1008 close.
**Severity:** 🟢 Low

### XZ-IPC-004 — Authenticated idle-connection DoS (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:967-968` clears the 5s auth timeout after auth succeeds (`conn.settimeout(None)`). Dispatch loop blocks on `readline` forever. Heartbeat watchdog never fires because `_last_heartbeat_at` is None (only set by heartbeat command). 4 idle authenticated connections deadlock the TCP worker pool.
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Set per-connection idle-read timeout (60s) after auth, treat `socket.timeout` as disconnect. OR: change watchdog to fire if `_last_heartbeat_at is None AND connection open > _HEARTBEAT_TIMEOUT_SECONDS`.
**Severity:** 🟢 Low

### XZ-IPC-005 — WS `shutdown` bypasses rate limiter (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:321-344` handles `shutdown` BEFORE the rate-limiter check. Authenticated client can spam unlimited `shutdown` frames, each spawning a daemon thread that calls `app.quit()`.
**Related Files:** `voice_typer/server/sidecar_ws.py`**Fix:** Add idempotency guard: `if server._shutdown_requested: return ack; server._shutdown_requested = True` before spawning the thread.
**Severity:** 🟢 Low

### XZ-IPC-006 — Dead else branch in `_handle_tcp_connection` (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:881-947` — SEC-2 early-return at line 868-876 makes the `else` branch (line 946-947) unreachable. The `if expected_token:` guard at line 881 is redundant.
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Remove the `else` branch and de-indent the `if expected_token:` body.
**Severity:** 🟢 Low

### XZ-IPC-007 — `ipc_server.py` is 2587-line monolith (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py` is 2587 lines mixing IPCServer class, main(), dispatch logic, _send path with 4 branches, _handle_tcp_connection (360 lines).
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Extract `main()` into `ipc_entry.py`. Extract `_send` branches into helpers. Extract auth handshake. Mechanical refactor preserving all public APIs and tests.
**Severity:** 🟢 Low

### XZ-IPC-008 — Dead `_ws_loop` variable + redundant loop reassignments (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:534, 550, 560` — three `loop = asyncio.get_running_loop()` assignments in same function. `_ws_loop` (line 550) is dead. Lines 560 and 570 are redundant.
**Related Files:** `voice_typer/server/sidecar_ws.py`**Fix:** Delete line 550 and lines 560/570. Keep line 534 + `server._ws_loop = loop`.
**Severity:** 🟢 Low

### XZ-IPC-009 — Stale line-number references in comments (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:952, 1573, 2445` — comments reference wrong line numbers (e.g. "set at line ~1171" but actual is line 861).
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Replace line-number references with function/label references.
**Severity:** 🟢 Low

### XZ-IPC-010 — Duplicated diagnostic-file writing blocks (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:2316-2382` and `:2513-2574` are near-identical ~65-line blocks writing PII-redacted traceback to `startup-error.log`. Block 2 does NOT redact `sys.argv` (potential `--ipc-token sk-...` leak).
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Extract `_write_startup_diagnostic(buf: StringIO, *, include_argv: bool = True) -> None` helper. Both blocks call it with `include_argv=True`.
**Severity:** 🟢 Low

### XZ-IPC-011 — Stale test docstring (Low)
**Status:** ❌ Not Fixed
**Description:** `tests/test_server.py:1717-1727` `test_no_token_env_allows_unauthenticated` docstring claims server accepts unauthenticated connections — but SEC-2 fix changed it to refuse-all. Test body is a no-op (only asserts env var unset).
**Related Files:** `tests/test_server.py`**Fix:** Update docstring. Convert test into real assertion that `_handle_tcp_connection` closes the conn and returns when `expected_token` is empty.
**Severity:** 🟢 Low

### XZ-IPC-012 — `is True` idiom fragility (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:1577, 1934` and `sidecar_ws.py:311` use `getattr(self.app, "_shutting_down", False) is True` — accommodates test MagicMock auto-vivification. A real refactor setting `_shutting_down = 1` (truthy int) would bypass the shutdown gate.
**Related Files:** `voice_typer/server/ipc_server.py`, `voice_typer/server/sidecar_ws.py`**Fix:** Add assertion in `VoiceTyperApp.__init__` that `_shutting_down` is a bool. Change `is True` back to truthiness.
**Severity:** 🟢 Low

---

### XZ-R3-01 — Heartbeat starvation → backend crash (High)
**Status:** ❌ Not Fixed
**Description:** `ipc/rate_limiter.py:69-76` `COMMAND_COSTS` gives heartbeat cost 1 with no priority. Per-process burst budget is 200 msg/s shared across ALL connections. A compromised renderer sustaining ≥200 msg/s of cheap commands exhausts the budget; every heartbeat during the attack window is rejected. After 120s (24 missed heartbeats), `_check_heartbeat_timeout` calls `app.quit()`, killing the backend.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`
- `voice_typer/server/ipc_server.py` (allow() call at line 1083, heartbeat watchdog at 1252-1305)**Fix:** Add dedicated heartbeat budget that bypasses the burst check: `if command == "heartbeat": return True`. OR add per-command-type sub-limits.
**Severity:** 🔴 High

### XZ-R3-02 — Rate limiter cost map coverage gap (Medium)
**Status:** ❌ Not Fixed
**Description:** `ipc/rate_limiter.py:69-76` `COMMAND_COSTS` covers only 5 of 69 commands. Expensive uncovered commands (delete_model, test_llm_connection, run_prewarm, restart_app, quit_app, save_vocabulary, save_templates, microphone_test_start, level_monitor_start, force_cancel_transcription) default to cost 1.
**Related Files:** `voice_typer/server/ipc/rate_limiter.py`**Fix:** Expand COMMAND_COSTS: delete_model=50, test_llm_connection=20, run_prewarm=50, save_vocabulary=10, save_templates=10, restart_app=100, quit_app=100, microphone_test_start=20, level_monitor_start=20, force_cancel_transcription=10. Add per-command-type sliding-window cap (max 1 restart_app per 10s).
**Severity:** 🟡 Medium

### XZ-R3-03 — Duplicated `_get_rate_limiter` implementation (Medium)
**Status:** ❌ Not Fixed
**Description:** `ipc/rate_limiter.py:318-361` and `ipc_server.py:116-159` are byte-for-byte identical implementations. Drift risk: fix one, forget the other.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`
- `voice_typer/server/ipc_server.py`**Fix:** Extract as `@classmethod get_or_create(cls, server)` on `_RateLimiter`. Both call sites import the single function. Tests still monkey-patch `ipc_server._RateLimiter` (classmethod resolves cls from instance).
**Severity:** 🟡 Medium

### XZ-R3-04 — `set_tray_locale` unbounded input (Medium)
**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:196-209` schema has NO `max_value_len` on locale, NO `max_payload_bytes`, NO validation of labels dict contents. `register_tray_labels` (`tray.py`) accumulates without cap.
**Related Files:**
- `voice_typer/server/handlers/system_handlers.py`
- `voice_typer/server/tray.py`**Fix:** Add `max_value_len: 64` to locale, `max_payload_bytes: 64*1024` to schema. Validate label dict: keys ≤ 64 chars, values ≤ 1024 chars. Cap `_TRAY_LABELS_LOCALES[locale]` at 200 keys.
**Severity:** 🟡 Medium

### XZ-R3-05 — Silent failure on restart_app/quit_app (Medium)
**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:43-58` sends ack BEFORE `service.restart()`/`service.quit()`. If the service call raises, error is logged server-side but NO error event is pushed to client. Client proceeds as if restart succeeded.
**Related Files:** `voice_typer/server/handlers/system_handlers.py`**Fix:** After `self._send(resp)`, if `service.restart()`/`service.quit()` raises, push follow-up error event via `event_bus.publish({"type": "restart_failed", ...})`. Client subscribes + surfaces toast.
**Severity:** 🟡 Medium

### XZ-R3-06 — bool-as-int type confusion (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:174` uses `isinstance(value, expected_type)` — for `(int, str)`, `isinstance(True, (int, str))` returns True (bool subclass of int). History handlers accept `{"limit": true}` silently coerced to `limit=1`.
**Related Files:**
- `voice_typer/server/ipc/validation.py`
- `voice_typer/server/ipc/history_bounds.py`
- `voice_typer/server/handlers/history_handlers.py`**Fix:** In `_validate_dict_payload`, when expected_type includes int, explicitly reject bool. Or add `reject_bool` rule.
**Severity:** 🟢 Low

### XZ-R3-07 — `max_payload_bytes` fragile scoping (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:153-167` — `max_payload_bytes` is per-field but checks whole payload. Helper checks FIRST field that declares it and breaks. Second field's value silently ignored.
**Related Files:** `voice_typer/server/ipc/validation.py`**Fix:** Lift `max_payload_bytes` to top-level schema argument: `_validate_dict_payload(data, schema, max_payload_bytes=...)`.
**Severity:** 🟢 Low

### XZ-R3-08 — `default` rule doesn't fire for explicit None (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:171-225` — default rule only fires when field is ABSENT. Explicit `null` fails type check instead of using default. `show_electron_notification` works around with 8 lines of pre-coercion.
**Related Files:** `voice_typer/server/ipc/validation.py`**Fix:** Add `none_to_default: bool = True` rule. When field value is None and rule is True, use default.
**Severity:** 🟢 Low

### XZ-R3-09 — Sidecar env vars not validated (Low)
**Status:** ❌ Not Fixed
**Description:** `env_validation.py:222-246` `_validate_sidecar_env` only logs warnings — does NOT pop, reset, or reject unsafe values. `VOICE_TYPER_NATIVE_DIR` and `VOICE_TYPER_PREWARM_EXE` paths not run through `_validate_path_safety`.
**Related Files:** `voice_typer/server/env_validation.py`**Fix:** For `VOICE_TYPER_NATIVE_DIR` and `VOICE_TYPER_PREWARM_EXE`, run `_validate_path_safety` against expected parent. Pop on failure. Validate `VOICE_TYPER_IPC_TOKEN` against alphanumeric pattern.
**Severity:** 🟢 Low

### XZ-R3-10 — Path/URL leak in env-validation logs (Low)
**Status:** ❌ Not Fixed
**Description:** `env_validation.py:54-58, 89-93, 128-133` logs full path/URL values via `%r`. Path may contain username (e.g. `/home/real-username/...`).
**Related Files:** `voice_typer/server/env_validation.py`**Fix:** Truncate to first 64 chars + "...<truncated>". Avoid `%r`. Or log only validation failure, not value.
**Severity:** 🟢 Low

### XZ-R3-11 — Inconsistent `_error_response` usage (Low)
**Status:** ❌ Not Fixed
**Description:** `handlers/privacy_handlers.py:139-143, 206-210` calls `_error_response` without returning result. `system_handlers.py:359-365, 425-427` builds envelope inline because `_error_response` doesn't support `field` key.
**Related Files:**
- `voice_typer/server/handlers/privacy_handlers.py`
- `voice_typer/server/handlers/system_handlers.py`
- `voice_typer/server/handlers/_base.py`**Fix:** Make `privacy_handlers` consistent (`return _error_response(...)`). Extend `_error_response` to accept optional extra fields (`field=..., **extra`).
**Severity:** 🟢 Low

### XZ-R3-12 — No input validation on `check_accessibility` (Low)
**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:83-177` ignores `data` entirely — no `_validate_dict_payload` call. Inconsistency with other handlers.
**Related Files:** `voice_typer/server/handlers/system_handlers.py`**Fix:** Add empty-schema validation at top of handler.
**Severity:** 🟢 Low

### XZ-R3-13 — `register_tray_labels` accumulates without cap (Low)
**Status:** ❌ Not Fixed
**Description:** `tray.py:register_tray_labels` merges new labels over existing without bound. Module-level `_TRAY_LABELS_LOCALES` grows monotonically across calls.
**Related Files:** `voice_typer/server/tray.py`**Fix:** Cap at 200 keys per locale. Drop oldest or reject with `client.invalid_field`. Validate label keys against known set.
**Severity:** 🟢 Low

---

### XZ-R4-001 — Bearer-token auth at handshake only; ADR "HMAC" wording misleading (Medium)
**Status:** ❌ Not Fixed
**Description:** `Cargo.toml:56-61` comment + `sidecar_ws.py:189-244` + `ws.rs:190-382` — ADR-0020 §3 says "HMAC" but implementation is one-shot bearer-token check at WS handshake. No per-message MAC, no nonce, no replay protection.
**Related Files:**
- `src-tauri/Cargo.toml` (comment)
- `voice_typer/server/sidecar_ws.py`
- `src-tauri/src/sidecar/ws.rs`
- `docs/adr/0017-cloud-url-allowlist-https.md` (or ADR-0020)**Fix:** Update ADR-0020 §3 to document bearer-token model. Add threat-model note: loopback-only bind + ephemeral port + token rotation on respawn are compensating controls.
**Severity:** 🟡 Medium

### XZ-R4-002 — Bearer token via env var readable by same-user processes on Linux (Medium)
**Status:** ❌ Not Fixed
**Description:** `sidecar/spawn.rs:81-84` sets `VOICE_TYPER_IPC_TOKEN` env var on sidecar. Linux same-user processes can read `/proc/<pid>/environ` to recover token.
**Related Files:** `src-tauri/src/sidecar/spawn.rs`**Fix:** Pass token via Unix domain socket ancillary fd, pipe between parent/child, or temp file with 0600 perms that sidecar reads + unlinks. Env-var is weakest link.
**Severity:** 🟡 Medium

### XZ-R4-003 — `.lock().unwrap()` in production despite poison-safe helper (Medium)
**Status:** ❌ Not Fixed
**Description:** `state.rs:33` defines poison-safe `lock()` helper. ~10 production call sites use raw `.lock().unwrap()` instead: `main.rs:257,325,347`, `ws.rs:212`, `sidecar_cmds.rs:332,368,633,690`, `state.rs:336,362`. `panic = "abort"` in release profile means panic = abort.
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/state.rs`**Fix:** Sweep all production `.lock().unwrap()` calls on `SidecarState` fields to use `crate::state::lock(&state.X)`. Add clippy lint to prevent regression.
**Severity:** 🟡 Medium

### XZ-R4-004 — `tauri.conf.json` shell scope allows `args: true` for sidecar (Medium)
**Status:** ❌ Not Fixed
**Description:** `tauri.conf.json:117-125` allows `"args": true` for `bin/python-sidecar`. Host only ever sends `["--ws"]`. Compromised main renderer can spawn sidecar with arbitrary CLI args.
**Related Files:**
- `src-tauri/tauri.conf.json`
- `src-tauri/capabilities/main-runtime.json`**Fix:** Change `"args": true` to `"args": ["--ws"]`.
**Severity:** 🟡 Medium

### XZ-R4-005 — `withGlobalTauri: true` exposes full Tauri API (Medium)
**Status:** ❌ Not Fixed
**Description:** `tauri.conf.json:13` `withGlobalTauri: true` exposes all Tauri plugin APIs on `window.__TAURI__`. Tauri v2 docs recommend `false` for production. Increases XSS blast radius.
**Related Files:** `src-tauri/tauri.conf.json`**Fix:** Set `withGlobalTauri: false`. Renderer already uses `invoke('dispatch', ...)` via `@tauri-apps/api/core`.
**Severity:** 🟡 Medium

### XZ-R4-006 — `open_logs` bypasses shell-plugin scope (Low)
**Status:** ❌ Not Fixed
**Description:** `commands/system_cmds.rs:67-97` spawns `explorer.exe`/`open`/`xdg-open` via raw `std::process::Command`, bypassing `tauri-plugin-shell`'s scope mechanism.
**Related Files:** `src-tauri/src/commands/system_cmds.rs`**Fix:** Route through `tauri_plugin_opener` with explicit scope. OR add sanity check that `path == config_dir(&app)` before spawning (make implicit invariant explicit).
**Severity:** 🟢 Low

### XZ-R4-007 — `main.rs` is 449 LOC, exceeds ~280 wiring-only target (Low)
**Status:** ❌ Not Fixed
**Description:** `main.rs:43` docstring claims "~280 lines" but file is 449. Inline relaunch listener (48 LOC) duplicates `dispatch_frame`'s send path.
**Related Files:** `src-tauri/src/main.rs`**Fix:** Extract relaunch listener body into `sidecar_cmds::send_relaunch_ack(state)`. Listener becomes 4-5 lines.
**Severity:** 🟢 Low

### XZ-R4-008 — `SidecarState.token` is write-only dead state held in plain String (Low)
**Status:** ❌ Not Fixed
**Description:** `state.rs:247-263` field is documented as WRITE-ONLY dead state — written at `main.rs:325` and `supervisor.rs:352-353` but never read. Held in plain memory (no `zeroize`).
**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/main.rs:325`
- `src-tauri/src/sidecar/supervisor.rs:352-353`**Fix:** Remove field + write sites. If retained for future, add `zeroize::Zeroizing<String>`.
**Severity:** 🟢 Low

### XZ-R4-009 — restart counter file has no integrity protection (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar/supervisor.rs:151-166` writes counter as plain JSON, no HMAC. Same-user attacker with write access to `<config_dir>/restart_counter.json` can reset count to 0 indefinitely, bypassing CR-29 breaker.
**Related Files:** `src-tauri/src/sidecar/supervisor.rs`**Fix:** Add HMAC-SHA256 over `(count, ts)` using per-install random key in separate 0600 file. Verify on read; reject if mismatch.
**Severity:** 🟢 Low

### XZ-R4-010 — Inline WS frame construction duplicates dispatch path (Low)
**Status:** ❌ Not Fixed
**Description:** `main.rs:260-265` (relaunch_ack) and `commands/bubble.rs:653-674` (toggle_dictation) construct WS frames inline, bypassing `dispatch_frame`. `id` collisions possible.
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`**Fix:** Add `dispatch_fire_and_forget(state, cmd, data)` to `sidecar_cmds.rs`. Both call sites delegate.
**Severity:** 🟢 Low

### XZ-R4-011 — Dev-mode sidecar spawn hardcodes `RUST_LOG=debug`, skips `VOICE_TYPER_PREWARM_EXE` (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar/spawn.rs:249-260` (dev) diverges from release (`:79-84`): missing `VOICE_TYPER_PREWARM_EXE`, hardcoded `RUST_LOG=debug` overrides user setting.
**Related Files:** `src-tauri/src/sidecar/spawn.rs`**Fix:** Mirror release env-var set in dev (add `VOICE_TYPER_PREWARM_EXE`). Only set `RUST_LOG=debug` if env var is unset.
**Severity:** 🟢 Low

### XZ-R4-012 — WS auth-read path not wrapped in `catch_unwind` (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar/ws.rs:266-382` auth wait + JSON parse + emit — NOT wrapped in `catch_unwind`. Reader/writer task bodies ARE wrapped. Asymmetry.
**Related Files:** `src-tauri/src/sidecar/ws.rs`**Fix:** Wrap auth-read block in `AssertUnwindSafe(async {...}).catch_unwind()` with fallback calling `cleanup_and_trigger_respawn` on panic.
**Severity:** 🟢 Low

### XZ-R4-013 — `migrate.rs::copy_missing_recursive` follows symlinks (Low)
**Status:** ❌ Not Fixed
**Description:** `migrate.rs:510-545` uses `path.is_dir()` / `path.is_file()` which follow symlinks. Attacker pre-planting symlink in old Electron `models/` dir could copy `~/.ssh/id_rsa` into new config dir.
**Related Files:** `src-tauri/src/migrate.rs`**Fix:** Use `std::fs::symlink_metadata(path)` and check `file_type().is_symlink()` — skip symlinks entirely.
**Severity:** 🟢 Low

### XZ-R4-014 — `paste_text` IPC accepts arbitrary-length text (Low)
**Status:** ❌ Not Fixed
**Description:** `commands/sidecar_cmds.rs:518-521` `PasteTextArgs { text: String }` has no length cap. WS path caps at `MAX_FRAME_BYTES = 1 MiB`, but Tauri IPC has no equivalent on renderer→host path.
**Related Files:** `src-tauri/src/commands/sidecar_cmds.rs`**Fix:** Add `if args.text.len() > PASTE_MAX_BYTES { return Err("paste text too large".into()); }` at top of `paste_text`.
**Severity:** 🟢 Low

### XZ-R4-015 — Tray capabilities overly broad (Low)
**Status:** ❌ Not Fixed
**Description:** `capabilities/main-runtime.json:31-38` grants `core:tray:allow-set-icon`, `allow-set-tooltip`, `allow-set-title`, `allow-set-menu`, `allow-new`, `allow-remove-by-id` to main window. Rust host owns tray state; renderer doesn't need these.
**Related Files:** `src-tauri/capabilities/main-runtime.json`**Fix:** Drop `set-icon`, `set-tooltip`, `set-title`, `set-menu`, `new`, `remove-by-id`. Keep only `core:tray:default`.
**Severity:** 🟢 Low

### XZ-R4-016 — `kill_process_tree` SIGKILL race (Low)
**Status:** ❌ Not Fixed
**Description:** `state.rs:187-241` collects descendants ONCE, then SIGKILLs 200ms later. If PID is reused during grace period, SIGKILL kills wrong process.
**Related Files:** `src-tauri/src/state.rs`**Fix:** Before each SIGKILL, re-verify via `pgrep -P <parent_pid>` that descendant is still a child. Skip if not.
**Severity:** 🟢 Low

### XZ-R4-017 — `bubble_toggle_dictation` rate limiter uses wall-clock `SystemTime` (Low)
**Status:** ❌ Not Fixed
**Description:** `commands/bubble.rs:712-739` uses `SystemTime::now()` (NTP-skew susceptible). Malicious NTP spoof could disable rate limiter.
**Related Files:** `src-tauri/src/commands/bubble.rs`**Fix:** Use `Instant::now()` (monotonic) with `OnceLock<Instant>` anchor.
**Severity:** 🟢 Low

### XZ-R4-018 — Stale PVT-25 TODOs (Informational)
**Status:** ❌ Not Fixed
**Description:** `commands/bubble.rs:18, 28, 650` reference PVT-25 follow-ups: F-Q9, F-S1, dispatch_fire_and_forget helper.
**Related Files:** `src-tauri/src/commands/bubble.rs`**Fix:** Address in PVT-25 fix wave (covered by XZ-R4-010 for the dispatch helper).
**Severity:** 🟢 Low

### XZ-R4-019 — `tray.rs::on_menu_event` no pending-map size cap (Low)
**Status:** ❌ Not Fixed
**Description:** `tray.rs:219-241` spawns tokio task per click; `sidecar_cmds.rs:345-349` registers pending entry without size cap. Unresponsive sidecar + rapid clicks → 1000s of pending entries (80 bytes each, auto-expire 120s).
**Related Files:**
- `src-tauri/src/tray.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`**Fix:** Add `pending.len() > PENDING_MAX=1024` guard at `dispatch_frame:347` rejecting with `pending_full` error.
**Severity:** 🟢 Low

---

### XZ-R5-001 — Missing `setWindowOpenHandler` on both windows (Medium)
**Status:** ❌ Not Fixed
**Description:** `windows/main-window.ts:226-276` and `windows/bubble-window.ts:201-238` do NOT register `webContents.setWindowOpenHandler`. Compromised renderer can pop arbitrary external URLs in fresh BrowserWindow.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`**Fix:** Install `wc.setWindowOpenHandler(({url}) => { if (url.startsWith("https://")) shell.openExternal(url); return {action: "deny"}; });` after creating each window.
**Severity:** 🟡 Medium

### XZ-R5-002 — CSP HTTP-header not per-window (Low)
**Status:** ❌ Not Fixed
**Description:** `bootstrap.ts:98-125` `setupCsp` registers `onHeadersReceived` on `session.defaultSession` without discriminating by `webContents`. Same CSP (with `connect-src 'self' https://api.github.com`) injected for bubble window — contradicts `csp-plugin.ts:79-96` stricter `CSP_PROD_BUBBLE`.
**Related Files:**
- `voice_typer/client/src/main/bootstrap.ts`
- `voice_typer/client/csp-plugin.ts`**Fix:** Use `details.webContents === state.bubbleWindow?.webContents` to select per-window CSP inside `onHeadersReceived`. Import `CSP_PROD_BUBBLE` / `CSP_PROD_MAIN` from `csp-plugin.ts`.
**Severity:** 🟢 Low

### XZ-R5-003 — Allowlist doesn't separate renderer-reachable from internal-only (Medium)
**Status:** ❌ Not Fixed
**Description:** `allowed-commands.ts:50-207` `ALLOWED_COMMANDS` contains internal-only commands (`quit_app`, `heartbeat`, `relaunch_ack`, `restart_app`). Renderer's `python-call` IPC handler uses same allowlist. Compromised renderer can invoke `quit_app`, `restart_app`, `delete_all_personal_data`.
**Related Files:**
- `voice_typer/client/src/main/allowed-commands.ts`
- `voice_typer/client/src/main/python/send-to-python.ts`
- `voice_typer/client/src/main/ipc/python-call-handler.ts`**Fix:** Split into `ALLOWED_COMMANDS_RENDERER` (safe subset) and `ALLOWED_COMMANDS_INTERNAL` (quit_app, heartbeat, relaunch_ack, restart_app). `python-call-handler.ts` checks `ALLOWED_COMMANDS_RENDERER.has(cmd)`.
**Severity:** 🟡 Medium

### XZ-R5-004 — `setMainLocale` is dead code; native dialogs always English (Medium)
**Status:** ❌ Not Fixed
**Description:** `i18n.ts:166-172` `setMainLocale` exported but NO production caller. Native Electron dialogs (`dialog.showErrorBox`, model-folder picker, export save-as) always render in English even when user selected non-English.
**Related Files:**
- `voice_typer/client/src/main/i18n.ts`
- `voice_typer/client/src/main/ipc/window-handlers.ts`
- `voice_typer/client/src/main/bootstrap.ts`**Fix:** Re-add `i18n:set-locale` IPC handler that calls `setMainLocale`. OR read renderer's localStorage locale file from disk in `bootstrapRuntime()`. Update i18n.ts docstring.
**Severity:** 🟡 Medium

### XZ-R5-005 — Dead `_bubblePageReady` state field (Low)
**Status:** ❌ Not Fixed
**Description:** `state.ts:81, 114` — set true on `bubble:ready`, reset on close, but `showBubbleWindow()` never consults it.
**Related Files:**
- `voice_typer/client/src/main/state.ts`
- `voice_typer/client/src/main/ipc/bubble-handlers.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`**Fix:** Delete `_bubblePageReady` (and `bubble:ready` handler). OR add read in `showBubbleWindow()` to defer `bubble:show` send until ready (with timeout fallback).
**Severity:** 🟢 Low

### XZ-R5-006 — Dead `__myPyPid` global write (Low)
**Status:** ❌ Not Fixed
**Description:** `python/start-python.ts:102` writes `globalThis.__myPyPid` for "stale-killer" that was removed per RELIABILITY-002.
**Related Files:** `voice_typer/client/src/main/python/start-python.ts`**Fix:** Delete line 102 and comment on line 101.
**Severity:** 🟢 Low

### XZ-R5-007 — Two overlapping structured loggers (Low)
**Status:** ❌ Not Fixed
**Description:** `logging.ts:291-324` (`logger`) and `:500-513` (`log`) overlap. Both write WARN/ERROR to 5 MiB rotated files in userData (different files: `electron-main.log` vs `electron-runtime.log`). Acknowledged in DUPLICATION NOTE at `:26-34`.
**Related Files:** `voice_typer/client/src/main/logging.ts`**Fix:** Consolidate into one logger supporting both `.info(msg, ...args)` and `.info(msg, obj)` call styles. Route all output to single `electron-main.log`.
**Severity:** 🟢 Low

### XZ-R5-008 — Duplicated defensive `require("../logging")` pattern (Low)
**Status:** ❌ Not Fixed
**Description:** `windows/main-window.ts:27-77` (51 lines) and `windows/bubble-window.ts:31-57` (27 lines) contain near-identical defensive-require blocks.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`**Fix:** Export single `createLogger()` factory from `logging.ts` handling fallback internally.
**Severity:** 🟢 Low

### XZ-R5-009 — Single-instance stale-PID recovery PID-reuse lockout (Low)
**Status:** ❌ Not Fixed
**Description:** `single_instance.ts:91-108` uses `process.kill(pid, 0)` to check if PID exists — but doesn't verify it's Voice Typer. PID reuse → lockout until unrelated process exits.
**Related Files:** `voice_typer/client/src/main/single_instance.ts`**Fix:** Before declaring PID "alive", verify process name via `/proc/<pid>/cmdline` (Linux), `wmic process where processid=<pid> get commandline` (Windows), `ps -p <pid> -o comm=` (macOS).
**Severity:** 🟢 Low

### XZ-R5-010 — Bubble IPC accepts non-numeric payloads without type validation (Low)
**Status:** ❌ Not Fixed
**Description:** `bubble-handlers.ts:79-116, 139-153` — TS annotations are runtime lies. `deltaX` as string → `x + deltaX` becomes concatenation → `newX = NaN` → `setPosition(NaN, NaN)` no-ops.
**Related Files:** `voice_typer/client/src/main/ipc/bubble-handlers.ts`**Fix:** Add `if (typeof deltaX !== "number" || typeof deltaY !== "number") return;` at top of `bubble:move-by` (and `width`/`height` in `bubble:resize`).
**Severity:** 🟢 Low

### XZ-R5-011 — No Windows code-signing enforcement; no entitlements file (Medium)
**Status:** ❌ Not Fixed
**Description:** `electron-builder.yml:27-59` — signing purely env-driven (no `certificateFile`/`certificateSubjectName`). PR builds ship UNSIGNED. No macOS entitlements file. No Linux AppImage signing.
**Related Files:** `voice_typer/client/electron-builder.yml`**Fix:** Add `win.signingHashAlgorithms: ["sha256"]`. Consider failing build if `CSC_LINK` empty when publishing. Add `mac.entitlements: resources/entitlements.mac.plist` declaring `com.apple.security.device.audio-input`. Configure AppImage GPG signing.
**Severity:** 🟡 Medium

### XZ-R5-012 — Multiple in-scope files exceed 300-line rule (Low)
**Status:** ❌ Not Fixed
**Description:** `logging.ts` (513), `bubble-window.ts` (531), `main-window.ts` (488), `bootstrap.ts` (436), `tcp-connect.ts` (321). `index.ts` (209) is OK.
**Related Files:** (multiple)**Fix:** Split each into focused submodules. Defer if test-seam analysis shows high risk.
**Severity:** 🟢 Low

---

### XZ-R6-NH-01 — Native hotkey binary TOCTOU on watchdog respawn (Medium)
**Status:** ❌ Not Fixed
**Description:** `native_hotkeys/base.py:291-317` `_spawn_process` reuses cached `self._binary_path` on watchdog respawn WITHOUT re-running `verify_native_binary_or_skip`. Attacker swapping binary during respawn window achieves code execution.
**Related Files:** `voice_typer/server/native_hotkeys/base.py`**Fix:** Call `verify_native_binary_or_skip(self._binary_path)` at top of `_spawn_process`. Return early / set `_failed=True` if verification fails.
**Severity:** 🟡 Medium

### XZ-R6-NH-02 — Factory discards verified binary path (Low)
**Status:** ❌ Not Fixed
**Description:** `native_hotkeys/factory.py:18-31` verifies `binary` then passes only `hotkey_str` to constructor. `base.py:89` re-discovers via `get_native_binary_path()`.
**Related Files:**
- `voice_typer/server/native_hotkeys/factory.py`
- `voice_typer/server/native_hotkeys/base.py`**Fix:** Pass verified `binary` Path into constructor: `MacNativeHotkey(hotkey_str, binary_path=binary)`.
**Severity:** 🟢 Low

### XZ-R6-NH-03 — Manifest key mismatch disables native hotkeys in dev (Low)
**Status:** ❌ Not Fixed
**Description:** `native/binaries.json` uses arch-suffixed keys (`linux-key-listener-x86_64`). Dev tree has legacy non-suffixed `linux-key-listener`. `get_expected_sha256` returns None → `verify_native_binary_or_skip` fails closed → falls back to pynput.
**Related Files:**
- `voice_typer/server/native/binaries.json`
- `voice_typer/server/native_hotkeys/binary_path.py`**Fix:** Add legacy-name alias lookup in `get_expected_sha256`. Or regenerate `binaries.json` with both arch-suffixed and legacy keys.
**Severity:** 🟢 Low

### XZ-R6-AS-01 — Tauri binary spawned at autostart with no integrity check (Low-Medium)
**Status:** ❌ Not Fixed
**Description:** `autostart_launcher.py:242-305` `_tauri_binary` returns path with NO hash verification. Spawned at login with user's full privileges. `VT_TAURI_BINARY` env var attack vector.
**Related Files:** `voice_typer/server/autostart_launcher.py`**Fix:** Add `verify_tauri_binary_or_skip(path)` mirroring native hotkey pattern. Maintain `tauri-binaries.json` manifest. Call in `_spawn_tauri_host` before `subprocess.Popen`.
**Severity:** 🟢 Low

### XZ-R6-AS-02 — Electron binary not integrity-verified (Low)
**Status:** ❌ Not Fixed
**Description:** `_electron_build.py:67-80` `_electron_binary` returns path with no hash check.
**Related Files:** `voice_typer/server/_electron_build.py`**Fix:** Optional — add hash check similar to `verify_native_binary_or_skip`. Lower priority (path typically user-writable only).
**Severity:** 🟢 Low

### XZ-R6-AS-03 — Stale Run-key cleanup uses naive command-line parsing (Low)
**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart_windows.py:401` parses registry value naively. Doesn't handle escaped quotes / multiple leading quotes.
**Related Files:** `voice_typer/server/server_platform/autostart_windows.py`**Fix:** Use `ctypes.windll.shell32.CommandLineToArgvW` or `shlex.split(value, posix=False)`.
**Severity:** 🟢 Low

### XZ-R6-AS-04 — `.desktop` quoting doesn't escape newlines (Low)
**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart.py:71-94` `_desktop_quote` reserves `\n` but doesn't escape it inside quoted string. Could inject new .desktop fields.
**Related Files:** `voice_typer/server/server_platform/autostart.py`**Fix:** Reject args containing `\n`/`\r` with `ValueError`.
**Severity:** 🟢 Low

### XZ-R6-AS-05 — plist f-string XML construction (Low)
**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart_macos.py:116-138` and `prewarm_scheduler_posix.py:96-132` build plist via f-string + `xml.sax.saxutils.escape` (only escapes `&`, `<`, `>`). Doesn't escape `"` or `/`.
**Related Files:**
- `voice_typer/server/server_platform/autostart_macos.py`
- `voice_typer/server/prewarm_scheduler_posix.py`**Fix:** Build plist with `xml.etree.ElementTree` (handles all escaping). Match Windows Task Scheduler pattern.
**Severity:** 🟢 Low

### XZ-R6-AS-06 — `_schtasks_elevated` cmd.exe arg quoting (Low)
**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:546` quotes args only if contains space or `&`. Doesn't escape embedded `"`. cmd.exe metacharacter injection.
**Related Files:** `voice_typer/server/task_scheduler.py`**Fix:** Use `subprocess.list2cmdline(args)` for proper Windows arg quoting.
**Severity:** 🟢 Low

### XZ-R6-AS-07 — `SYSTEMROOT` env var trust (Low)
**Status:** ❌ Not Fixed
**Description:** `platform_launch.py:111-129` `_systemroot_notepad_path` uses `os.environ.get("SYSTEMROOT")` first. Attacker setting `SYSTEMROOT=C:\Users\attacker` could return malicious `notepad.exe`.
**Related Files:** `voice_typer/server/platform_launch.py`**Fix:** Reverse candidate order — try hardcoded `C:\Windows\System32\notepad.exe` FIRST.
**Severity:** 🟢 Low

### XZ-R6-AS-08 — PowerShell .ps1 temp file TOCTOU (Low)
**Status:** ❌ Not Fixed
**Description:** `server_platform/desktop_shortcut.py:258-276` writes PowerShell script to temp file with `delete=False`, then `powershell -File <tmp>`. TOCTOU window.
**Related Files:** `voice_typer/server/server_platform/desktop_shortcut.py`**Fix:** Use `subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], ...)` to avoid temp file.
**Severity:** 🟢 Low

### XZ-R6-AS-09 — `assets/logo-256.png` missing (Low)
**Status:** ❌ Not Fixed
**Description:** `server_platform/desktop_shortcut.py:85-107` `_generate_icon_ico` always returns None — `assets/logo-256.png` doesn't exist. Windows .lnk shortcuts show generic icon.
**Related Files:** `voice_typer/server/server_platform/desktop_shortcut.py`**Fix:** Add `logo-256.png` to `voice_typer/server/assets/`. Or update path to actual PNG location.
**Severity:** 🟢 Low

### XZ-R6-AS-10 — `taskkill` timeout silent failure (Low)
**Status:** ❌ Not Fixed
**Description:** `electron_launcher.py:283-287` `terminate_electron` Windows branch catches `subprocess.TimeoutExpired` at DEBUG. Orphan Electron renderer/GPU processes if taskkill hangs.
**Related Files:** `voice_typer/server/electron_launcher.py`**Fix:** Catch `subprocess.TimeoutExpired` explicitly and log at WARNING. Follow-up `os.kill(pid, signal.SIGTERM)` fallback.
**Severity:** 🟢 Low

### XZ-R6-AS-11 — systemd unit injection (Low)
**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:226-241` `_build_linux_service` interpolates `python` (from env var) into `ExecStart=` without escaping newlines.
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`**Fix:** Validate that `python` and `args` contain no newlines. Use systemd literal escaping.
**Severity:** 🟢 Low

---

### XZ-CLIP-01 — macOS/Linux password-field detection fails OPEN silently (High)
**Status:** ❌ Not Fixed
**Description:** `clipboard_target_safety.py:709-714` (macOS) and `:868-873` (Linux) catch AX/AT-SPI exceptions and `return False` (fail-open) at DEBUG. Dictation CAN reach password manager fields when AX/AT-SPI degraded.
**Related Files:** `voice_typer/server/clipboard_target_safety.py`**Fix:** Mirror Windows CLIP-2 pattern: log at WARNING (once via dedup guard), consider fail-CLOSED for known credential-dialog heuristics. For macOS, detect `kAXErrorAPIDisabled` and surface one-shot WARNING.
**Severity:** 🔴 High

### XZ-CLIP-03 — Outer exception handler fails open (High)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:400-407` `_is_safe_paste_target` outer `except Exception` returns `True` (fail-open) for ANY exception including security-relevant ones (Win32 APIs during shutdown, broken COM init).
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Tighten outer except to only fail-open for `ImportError`/`AttributeError` (ctypes missing). For all other exceptions, fail-CLOSED (return False) and log WARNING.
**Severity:** 🔴 High

### XZ-CLIP-04 — TOCTOU re-check Windows-only (Medium)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:790-854` safe_hwnd re-check is Windows-only. macOS (`_safe_key_press`) and Linux Wayland (`wtype`) have TOCTOU window between safety check and keystroke.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Add platform-native TOCTOU re-checks: macOS — re-fetch `NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()`; Linux — re-fetch focused AT-SPI accessible.
**Severity:** 🟡 Medium

### XZ-CLIP-05 — Overlapping paste cycles lose original clipboard (Medium)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:920-931` `_delayed_restore` defensive check can't distinguish "user copied something new" from "another paste cycle changed clipboard". User's original content silently lost when two cycles overlap.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Track cycle ownership via monotonic cycle ID or Win32 clipboard sequence number. Only skip restore if DIFFERENT owner took clipboard.
**Severity:** 🟡 Medium

### XZ-CLIP-06 — Wrong variable in seq-mismatch re-copy (Medium)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:725-726` uses `self._last_copied_text` (mutable instance state) instead of `pasted_text` (per-request value, per DP4). Concurrent `copy()` clobbers `_last_copied_text` → wrong text re-copied.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Replace `self._last_copied_text` with `expected` (or `pasted_text`) at lines 725-726. Add regression test.
**Severity:** 🟡 Medium

### XZ-CLIP-07 — Overly broad window-class block (Medium)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:302-305` `blocked_classes = {"#32770", ...}` — `#32770` is generic Win32 Dialog class (used by Open/Save As/Properties too). Blocks legitimate dictation into standard dialogs.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Remove `#32770`. Rely on UIA `IsPassword` check + `_CRED_DIALOG_CLASSES` (specific). Unify the two class sets.
**Severity:** 🟡 Medium

### XZ-CLIP-08 — `_last_copied_text` retained when `clipboard_save_restore=False` (Low)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:148, 531, 951` — `_last_copied_text` set in `copy()` unconditionally. Cleared only in `_delayed_restore`'s finally block, which only runs when `snapshot is not None`. When `clipboard_save_restore=False`, snapshot is None → never cleared.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Clear `self._last_copied_text = ""` at end of `paste()` (in `finally` block) when `snapshot is None`.
**Severity:** 🟢 Low

### XZ-CLIP-09 — Dead production code (Low)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:967-983` `restore_now` and `:985-1004` `_send_keystroke_sequence` — no production callers. Only tests.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Remove both methods. Update tests that exercise them to test `_safe_key_press` / snapshot-restore path instead.
**Severity:** 🟢 Low

### XZ-CLIP-10 — Race detection Windows-only (Low)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:699-735` PLAT-CLIPRACE seq-mismatch only fires on Windows. macOS/Linux have no equivalent — stale paste if clipboard modified between copy and paste.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** On macOS use `NSPasteboard.changeCount`. On Linux Wayland, accept residual risk and document.
**Severity:** 🟢 Low

### XZ-CLIP-14 — Redundant safety check (Low)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:759, 773` — when `paste_delay == 0`, check #2 runs immediately after check #1 (redundant UIA round-trip).
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Guard check #2 with `if paste_delay > 0:` so it only runs after actual sleep.
**Severity:** 🟢 Low

### XZ-CLIP-15 — Duplicated credential-class sets (Low)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:302` `blocked_classes` vs `clipboard_target_safety.py:236-241` `_CRED_DIALOG_CLASSES` — two different sets, ZERO overlap.
**Related Files:**
- `voice_typer/server/clipboard/manager.py`
- `voice_typer/server/clipboard_target_safety.py`**Fix:** Unify into single `_CRED_DIALOG_CLASSES` set in `clipboard_target_safety.py`. Import in `manager.py`. Remove `#32770` (see XZ-CLIP-07).
**Severity:** 🟢 Low

---

### XZ-PII-07 — Log retention: no time-based purge (Low)
**Status:** ❌ Not Fixed
**Description:** `log.py:674-680` rotation is size-only (5 MiB × 5). No `TimedRotatingFileHandler`, no startup sweep. Compare to `crash_handler._sweep_stale_diagnostics` (30-day mtime cutoff for crash diagnostics).
**Related Files:** `voice_typer/server/log.py`**Fix:** Add startup sweep (mirror `_sweep_stale_diagnostics`) deleting `voice-typer.log.*` rotations older than 30 days. OR switch to `TimedRotatingFileHandler` with daily rotation + 30-day retention.
**Severity:** 🟢 Low

### XZ-R10-01 — Windows reparse-point check is dead code (High)
**Status:** ❌ Not Fixed
**Description:** `secure_file_io.py:127-141` `_secure_read_text` Windows branch: `raise OSError(...)` raised inside `try` block is immediately caught by same `try`'s `except (AttributeError, OSError): pass`. Repoint protection does not exist on Windows.
**Related Files:** `voice_typer/server/secure_file_io.py`**Fix:** Split `try` so reparse-point `raise` is NOT covered by tolerant `except`:
```python
try:
    stat_result = os.lstat(str(p))
    attrs = getattr(stat_result, "st_file_attributes", 0) or 0
except (AttributeError, OSError):
    attrs = 0
if attrs & 0x00000400:
    raise OSError(f"SEC-002: refusing to follow reparse point: {p}")
```
**Severity:** 🔴 High

### XZ-R10-02 — `.bak` backup write bypasses `_secure_atomic_write` (High)
**Status:** ❌ Not Fixed
**Description:** `config.py:1114-1129` `_save_locked` backup uses `Path.write_bytes` — follows symlinks on destination, not atomic, no fsync, brief 0o644 window before `os.chmod(bak_path, 0o600)`.
**Related Files:** `voice_typer/server/config.py`**Fix:** Use `_secure_atomic_write(bak_path, content.decode("utf-8"))` for backup too.
**Severity:** 🔴 High

### XZ-R10-03 — Pre-migration backup uses `shutil.copy2` (High)
**Status:** ❌ Not Fixed
**Description:** `config.py:1297-1308` `shutil.copy2(config_file, pre_bak)` follows symlinks on destination, non-atomic, no fsync. Same vulnerability class as XZ-R10-02.
**Related Files:** `voice_typer/server/config.py`**Fix:** Read source via `_secure_read_text(config_file)` and write via `_secure_atomic_write(pre_bak, raw_text)`.
**Severity:** 🔴 High

### XZ-R10-04 — `_write_plaintext_fallback` lock-free read-modify-write (High)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-SEC-02 — `credential_store.py:721-770` does read-modify-write on `config.json` without acquiring `config.json.lock`.
**Related Files:** `voice_typer/server/credential_store.py`**Fix:** Folded into XZ-SEC-02 fix.
**Severity:** 🔴 High

### XZ-R10-05 — `_validate_non_numeric_fields` overwrites migration warnings (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:1946` `data["_load_warnings"] = warnings` (fresh local list) overwrites migration-added list. Migration warnings silently dropped from `instance.last_load_warnings`.
**Related Files:** `voice_typer/server/config.py`**Fix:** Use `data.setdefault("_load_warnings", []).extend(warnings)`.
**Severity:** 🟡 Medium

### XZ-R10-06 — `save()` doesn't catch `TypeError` from `json.dumps` (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:1110` `json.dumps(data, indent=2)` can raise `TypeError` (non-JSON-serializable value like set/datetime). `save()`'s `except` tuple is `(TimeoutError, OSError, PermissionError)` — `TypeError` propagates. Docstring says "never raises".
**Related Files:** `voice_typer/server/config.py`**Fix:** Widen `save()`'s `except` to `(TimeoutError, OSError, PermissionError, TypeError, ValueError)` and return False.
**Severity:** 🟡 Medium

### XZ-R10-07 — `_migrate_from_legacy` uses non-atomic `shutil.copytree` (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:347-364` `shutil.copytree` is non-atomic, file-by-file. Interrupted migration leaves partial target dir. Called from `logging_setup._setup_logging()` at every startup.
**Related Files:** `voice_typer/server/config.py`**Fix:** Copy to staging dir (`target.with_suffix(".migrating")`) via `shutil.copytree`, then atomically rename via `os.replace`. On failure, clean up staging. Add O_NOFOLLOW checks.
**Severity:** 🟡 Medium

### XZ-R10-08 — Windows config file ACLs not enforced (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:1088-1092` `_save_locked` chmod is `if not is_windows():` no-op on Windows. `_secure_atomic_write` mkstemp inherits parent dir DACL. If `%APPDATA%` shared or `VOICE_TYPER_CONFIG_DIR` set to shared location, config.json (with plaintext API keys) world-readable.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/secure_file_io.py`**Fix:** On Windows, after `os.replace`, explicitly set file ACL via `win32security.SetFileSecurity` (or `icacls /inheritance:r /grant:r "%USERNAME%:F"`). VALIDATE ON WINDOWS HOST.
**Severity:** 🟡 Medium

### XZ-R10-09 — Deprecated fields never actually leave config.json (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:524-547` v3 migration prunes 9 keys, but dataclass still declares them. `asdict(self)` re-serializes them with defaults. v3 "prune" cosmetically ineffective.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add v4 migration that prunes ALL fields marked "DEPRECATED". Remove from IPC_CONFIG_ALLOWLIST. Remove from `_validate_non_numeric_fields`. Remove from TS type. Bump `_CURRENT_SCHEMA_VERSION` to 4.
**Severity:** 🟡 Medium

### XZ-R10-10 — `.corrupt-<timestamp>` 1-second resolution collides (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:1628-1641` corrupt-config quarantine uses `int(time.time())` (1s resolution). Two corrupt loads in same second overwrite.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add PID or microsecond suffix: `f"config.json.corrupt-{int(time.time())}-{os.getpid()}"`. Or use `time.time_ns()`.
**Severity:** 🟢 Low

### XZ-R10-11 — Lock file no `O_NOFOLLOW` (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:397` (POSIX) and `:440` (Windows) — `os.open(lock_file, O_CREAT|O_RDWR, 0o600)` no `O_NOFOLLOW`. Symlink attack. Lock fail-open on open error.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add `os.O_NOFOLLOW` to POSIX `os.open` flags. On Windows, add `os.lstat` reparse-point check before opening. Consider fail-closed when lock can't be acquired.
**Severity:** 🟢 Low

### XZ-R10-12 — `_secure_read_text` no file-size cap (Low)
**Status:** ❌ Not Fixed
**Description:** `secure_file_io.py:116, 137` `f.read()` reads entire file with no size limit. Memory-exhaustion DoS if config.json replaced with huge file.
**Related Files:** `voice_typer/server/secure_file_io.py`**Fix:** Add `max_bytes` parameter (default 4 MB) and read in chunks, raising `ValueError` if exceeded. Or use `os.fstat` to check size before reading.
**Severity:** 🟢 Low

### XZ-R10-13 — `config.py` is 2002-line monolith (Low)
**Status:** ❌ Not Fixed
**Description:** 8+ distinct concerns in one file. `_validate_systemroot` (104 lines) out of place. `Config.load()` is 482 lines in single try/except.
**Related Files:** `voice_typer/server/config.py`**Fix:** Split into `config_paths.py`, `config_lock.py`, `config_migrations.py`, `config_dataclass.py`, `config_loader.py`, `config_saver.py`. Move `_validate_systemroot` to `env_validation.py`.
**Severity:** 🟢 Low

### XZ-R10-14 — Stale `save_strict` docstring (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:1153-1155` claims wiring `apply_config` to call `save_strict` is "follow-up task". `config_applier.py:638` already calls it.
**Related Files:** `voice_typer/server/config.py`**Fix:** Update docstring to reflect CR-97 wiring is done.
**Severity:** 🟢 Low

### XZ-R10-15 — `custom_theme: dict` not type-validated on load (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:824` `custom_theme: dict | None = None` NOT in any of bool/str/int/float field sets. Hand-edited config with `"custom_theme": [...]` or `42` round-trips without complaint.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add `dict_fields = {"custom_theme"}` set to `_validate_non_numeric_fields`. Coerce non-dict values to None with warning.
**Severity:** 🟢 Low

---

### XZ-R11-01 — `save_vocabulary_with_diff` doesn't update in-memory VocabularyManager (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:1110-1161` writes user vocabulary JSON file directly. Live `self._app._vocabulary_manager._data` NEVER touched. `dictation_pipeline.py:812-816` uses stale in-memory state until app restart.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/vocabulary.py`**Fix:** After `_secure_atomic_write` in `save_vocabulary_with_diff`, reload live manager: `with live_vm._lock: live_vm._load_and_merge()`. OR refactor to delegate to live VocabularyManager CRUD methods.
**Severity:** 🔴 High

### XZ-R11-02 — `history.db.corrupt-<ts>` survives GDPR delete (Medium)
**Status:** ❌ Not Fixed
**Description:** `history_db.py:906-919` corrupt DB renamed (not deleted) — contains dictated text. `_GDPR_PERSONAL_FILES` only lists `history.db`, `history.db-wal`, `history.db-shm`. No glob for `history.db.corrupt-*`.
**Related Files:**
- `voice_typer/server/service.py` (GDPR delete + export)
- `voice_typer/server/history_db.py`**Fix:** Add `"history.db.corrupt-*"` glob to both `delete_all_personal_data` and `export_gdpr_bundle`. Optional: rotation (keep most-recent N snapshots).
**Severity:** 🟡 Medium

### XZ-R11-03 — Migration "duplicate column name" handler bumps version without verifying (Medium)
**Status:** ❌ Not Fixed
**Description:** `history_db.py:798-811` — if V2 migration's first ALTER fails on "duplicate column name: favorite", handler treats whole migration as complete and bumps version. `language` column NEVER added. Subsequent `add_transcription(text, language="en")` fails silently.
**Related Files:** `voice_typer/server/history_db.py`**Fix:** Replace blanket "duplicate column name → done" heuristic with per-column existence checks via `PRAGMA table_info(transcriptions)`. Run only the missing ALTERs.
**Severity:** 🟡 Medium

### XZ-R11-04 — No encryption at rest for dictated text (Medium)
**Status:** ❌ Not Fixed
**Description:** `history_db.py` stores dictated `text` in plaintext. File perms 0o600 / dir 0o700, `secure_delete=ON`, GDPR delete unlinks after checkpoint. But while running (or after unclean shutdown before checkpoint), text recoverable by same-user/root.
**Related Files:** `voice_typer/server/history_db.py`**Fix:** Consider optional SQLCipher integration gated behind user setting. OR application-layer encryption of `text` column with key from OS keystore. At minimum document threat model in `docs/privacy/`. VALIDATE ON WINDOWS/MACOS HOST (file-perm mitigations are POSIX-only).
**Severity:** 🟡 Medium

### XZ-R11-05 — Dead code in `_save_user` (Low)
**Status:** ❌ Not Fixed
**Description:** `vocabulary.py:232-273` second retry block unreachable — `final_exc` is always set if loop didn't return. Trailing comment claims "best-effort" but live code raises.
**Related Files:** `voice_typer/server/vocabulary.py`**Fix:** Delete lines 232-273. Keep only live first-loop + raise.
**Severity:** 🟢 Low

### XZ-R11-06 — `TemplateManager` has no lock (Low)
**Status:** ❌ Not Fixed
**Description:** `templates.py:84-391` — no `threading.Lock`. `match()` iterates `self._templates` while CRUD methods mutate in place. Same race CR-23 fixed for vocabulary.
**Related Files:** `voice_typer/server/templates.py`**Fix:** Add `self._lock = threading.Lock()`. Guard `add`/`update`/`delete`/`import_json`/`_save`/`_load`/`match`/`export_json`. In `match`, snapshot `self._templates` under lock before iterating.
**Severity:** 🟢 Low

### XZ-R11-07 — IPC cap (1024) bypasses SEC-011 limits (200/500) (Low)
**Status:** ❌ Not Fixed
**Description:** `vocabulary_handlers.py:94` `_max_value_len = 1024` looser than `vocabulary.py:46-47` `MAX_PATTERN_LENGTH=200`/`MAX_REPLACEMENT_LENGTH=500`. `save_vocabulary_with_diff` bypasses CRUD methods.
**Related Files:**
- `voice_typer/server/handlers/vocabulary_handlers.py`
- `voice_typer/server/service.py` (save_vocabulary_with_diff)**Fix:** Lower IPC `_max_value_len` to 500. OR enforce per-category limits inside `save_vocabulary_with_diff` before writing.
**Severity:** 🟢 Low

### XZ-R11-08 — WAL/SHM files not chmod'd after lazy creation (Low)
**Status:** ❌ Not Fixed
**Description:** `history_db.py:609-617` chmod loop runs in `_open_write_conn` BEFORE WAL/SHM created by first write. Files later created with default umask (0o644 = world-readable on Linux).
**Related Files:** `voice_typer/server/history_db.py`**Fix:** After first write materializing WAL/SHM, re-run chmod loop. OR set `os.umask(0o077)` at process startup.
**Severity:** 🟢 Low

### XZ-R11-09 — `_handle_restore_history` no payload-size cap (Low)
**Status:** ❌ Not Fixed
**Description:** `history_handlers.py:120-143` — no `max_payload_bytes`, no `max_value_len`. Compare with vocabulary (1 MB + 1024 chars) and templates (256 KB + 1024 chars).
**Related Files:** `voice_typer/server/handlers/history_handlers.py`**Fix:** Add `"_payload": {"max_payload_bytes": 256 * 1024}` and `"record": {"type": dict, "required": True}` to schema.
**Severity:** 🟢 Low

### XZ-R11-10 — `save_vocabulary_with_diff` throwaway manager (Low)
**Status:** ❌ Not Fixed
**Description:** `service.py:1122-1123` constructs fresh `VocabularyManager()` per IPC call, loads bundled + user from disk, builds merged `_data`, then immediately discards. Double file read.
**Related Files:** `voice_typer/server/service.py`**Fix:** Reuse live `self._app._vocabulary_manager`. Compute diff against `mgr._data` (already merged).
**Severity:** 🟢 Low

### XZ-R11-11 — Missing `PRAGMA foreign_keys=ON` (Low)
**Status:** ❌ Not Fixed
**Description:** `history_db.py:586-618` writer connection doesn't set `foreign_keys=ON`. Current schema has no FK constraints, so no-op today. Latent footgun if FKs added.
**Related Files:** `voice_typer/server/history_db.py`**Fix:** Add `conn.execute("PRAGMA foreign_keys=ON")` to `_open_write_conn`.
**Severity:** 🟢 Low

---

### XZ-R12-01 — Onboarding auto-heal ignores `.onboarding_started` marker (High)
**Status:** ❌ Not Fixed
**Description:** `startup_sequence.py:151-183` auto-heal fires whenever `not onboarding_completed AND config.json exists`. `onboarding.py:123-176` added `mark_started()` + `.onboarding_started` marker to prevent clobbering in-progress wizard. Gate never added.
**Related Files:**
- `voice_typer/server/startup_sequence.py`
- `voice_typer/server/onboarding.py`**Fix:** In `startup_sequence.py:165`, add `started_marker = _config_dir() / ".onboarding_started"; if config_file.exists() and not started_marker.exists():` for auto-heal. Otherwise save default config and defer to wizard.
**Severity:** 🔴 High

### XZ-R12-02 — `_migrate_from_legacy` non-atomic (High)
**Status:** ❌ Not Fixed
**Description:** `config.py:347-364` `shutil.copytree(legacy, target, dirs_exist_ok=True)` non-atomic, file-by-file. Interrupted migration leaves partial target. Same as XZ-R10-07.
**Related Files:** `voice_typer/server/config.py`**Fix:** Folded into XZ-R10-07 fix.
**Severity:** 🔴 High

### XZ-R12-03 — `migrate.rs` sentinel written after partial failures (High)
**Status:** ❌ Not Fixed
**Description:** `migrate.rs:296-304` writes sentinel marker UNCONDITIONALLY after all steps, even if individual steps (config, history.db, recovery) failed. Next launch: `migration_marker.exists()` → early-return. User's history.db / recovery.json / config.json NOT migrated.
**Related Files:** `src-tauri/src/migrate.rs`**Fix:** Track `migration_failed` counter. Only write sentinel if ALL critical steps succeeded. If any failed, retry on next launch (no sentinel) OR write `.migration-partial` marker + surface user notification.
**Severity:** 🔴 High

### XZ-R12-04 — Model file copy non-atomic (Medium)
**Status:** ❌ Not Fixed
**Description:** `migrate.rs:504-546` `std::fs::copy` not atomic, no fsync. Combined with `if dst_path.exists() { continue; }` guard, partial file looks "existing" on next launch — corrupt model file in target.
**Related Files:** `src-tauri/src/migrate.rs`**Fix:** Use atomic copy (temp+fsync+rename) for model files too. OR write `.partial` marker file alongside destination, skip/rewrite on next launch if marker exists. At minimum fsync after `std::fs::copy`.
**Severity:** 🟡 Medium

### XZ-R12-05 — `VOICE_TYPER_RESTART` bypass dead code (Medium)
**Status:** ❌ Not Fixed
**Description:** `single_instance.py:12-14, 203-205, 232-236` docstrings claim 30s time-limited restart bypass. Actual code never reads `VOICE_TYPER_RESTART` or calls `security.verify_restart_token()`. Function is dead in production.
**Related Files:**
- `voice_typer/server/single_instance.py`
- `voice_typer/server/security.py`**Fix:** Either implement bypass in `_ensure_single_instance` by calling `security.verify_restart_token()`, OR delete dead SEC-001 infrastructure and update docstrings.
**Severity:** 🟡 Medium

### XZ-R12-06 — Lockfile name mismatch (Medium)
**Status:** ❌ Not Fixed
**Description:** `single_instance.py:21` docstring says `voice-typer.lock`; `:441` code creates `backend.lock`. Docstring claims `fcntl.flock` is primary; actual code uses `O_CREAT|O_EXCL` primary with flock as secondary.
**Related Files:** `voice_typer/server/single_instance.py`**Fix:** Update docstring to match code: lockfile is `backend.lock`, primary mechanism is `O_CREAT|O_EXCL` with stale-PID recovery, flock is defense-in-depth.
**Severity:** 🟡 Medium

### XZ-R12-07 — Prewarm path parsing bug (Medium)
**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:62-69` parses dev fallback `f'"{exe}" -m voice_typer.server.prewarm'` via `resolved.split(" ", 1)[0].strip('"')`. Fails when Python path contains space (common on macOS `/Users/My Name/...`).
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`**Fix:** Use `shlex.split(resolved)[0]` to parse dev-fallback command line.
**Severity:** 🟡 Medium

### XZ-R12-08 — Onboarding fail counter in-memory only (Medium)
**Status:** ❌ Not Fixed
**Description:** `startup_sequence.py:194-209` `_onboarding_fail_count` is `app` attribute (in-memory only). Counter resets on every process restart. "After 3 failures" circuit breaker only trips if all 3 in same session.
**Related Files:** `voice_typer/server/startup_sequence.py`**Fix:** Persist fail counter to `.onboarding_fail_count` file with timestamp. Reset on successful completion.
**Severity:** 🟡 Medium

### XZ-R12-09 — Prewarm scheduler unit files non-atomic (Medium)
**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:140, 270-271` `Path.write_text` (truncate-then-write). systemd/launchd refuse to load corrupt unit file on next reload. Silent failure.
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`**Fix:** Use `_secure_atomic_write` for plist and unit files.
**Severity:** 🟡 Medium

### XZ-R12-10 — Linux prewarm no immediate start (Medium)
**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:265-292` Linux registration `systemctl --user enable` but NOT `start`. Timer only fires at next boot. macOS path correctly does `launchctl load` (starts immediately).
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`**Fix:** After `enable`, also run `systemctl --user start voice-typer-prewarm.timer` (best-effort, non-fatal).
**Severity:** 🟡 Medium

### XZ-R12-11 — `merge_config` docstring vs implementation mismatch (Low)
**Status:** ❌ Not Fixed
**Description:** `migrate.rs:312-316` docstring promises per-key mtime resolution. Implementation uses single whole-file mtime to decide ALL overlapping keys.
**Related Files:** `src-tauri/src/migrate.rs`**Fix:** Update docstring to match implementation: "the entire newer file's values win for overlapping keys".
**Severity:** 🟢 Low

### XZ-R12-12 — `migrate.rs` parse-failure fail-open (Low)
**Status:** ❌ Not Fixed
**Description:** `migrate.rs:344-357` corrupt source `config.json` silently treated as `Value::Null` (empty). User's old Electron settings silently dropped. No backup preserved.
**Related Files:** `src-tauri/src/migrate.rs`**Fix:** Before treating as Null, copy corrupt source to `config.json.corrupt-pre-migration.<timestamp>.bak`. Surface user notification.
**Severity:** 🟢 Low

### XZ-R12-13 — docstring stale (Low)
**Status:** ❌ Not Fixed
**Description:** `supervisor.rs:22-27` claims "disk-persisted counter". `main.rs:318` resets to 0 on every fresh launch. Counter only counts within-session.
**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/main.rs:318`**Fix:** Either remove `main.rs:318` reset, OR update `supervisor.rs` docstring to say "counter is reset on every fresh app launch; breaker only protects against within-session flapping".
**Severity:** 🟢 Low

### XZ-R12-14 — Prewarm sentinel fail-open (Low)
**Status:** ❌ Not Fixed
**Description:** `prewarm/paths.py:109-130` `_already_warmed` returns False when `_boot_time()` returns None (no psutil + no GetTickCount64 fallback on POSIX). Prewarm re-runs on EVERY trigger.
**Related Files:** `voice_typer/server/prewarm/paths.py`**Fix:** Add POSIX fallback for `_boot_time()` using `os.popen("uptime -s")` or `/proc/stat` btime (Linux) / `sysctl -n kern.boottime` (macOS). Or fall back to comparing sentinel's mtime to process start time.
**Severity:** 🟢 Low

### XZ-R12-15 — Re-onboarding marker inconsistency (Low)
**Status:** ❌ Not Fixed
**Description:** `startup_tasks.py:438-440` IPC `reset_onboarding_complete` only deletes `.onboarding_complete`. `onboarding.py:188-193` `OnboardingController.reset` deletes both. IPC handler leaves stale `.onboarding_started`.
**Related Files:**
- `voice_typer/server/startup_tasks.py`
- `voice_typer/server/onboarding.py`**Fix:** Have `reset_onboarding_complete` delegate to `OnboardingController.reset()`, or at minimum also delete `.onboarding_started`.
**Severity:** 🟢 Low

### XZ-R12-16 — `crash_recovery.__del__` racy read (Low)
**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:513` `if self._entries:` reads without acquiring `self._lock`. Concurrent `add()` could mutate.
**Related Files:** `voice_typer/server/crash_recovery.py`**Fix:** Acquire `self._lock` for the check, OR always call `_save_sync()` unconditionally.
**Severity:** 🟢 Low

### XZ-R12-17 — `history.db` WAL sidecar migration loses data (Low)
**Status:** ❌ Not Fixed
**Description:** `migrate.rs:252-266` copies WAL/SHM sidecars independently. If `-wal` copy fails, target has `history.db` without WAL → committed-but-uncheckpointed transactions lost.
**Related Files:** `src-tauri/src/migrate.rs`**Fix:** If either sidecar copy fails, delete target sidecars (SQLite starts fresh) AND log user-visible warning. OR use SQLite `.backup` API.
**Severity:** 🟢 Low

### XZ-R12-18 — Prewarm unregister leaves in-flight service (Low)
**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:295-317` stops TIMER, not in-flight SERVICE. An in-flight oneshot continues. Unit files unlinked while service still running.
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`**Fix:** Also run `systemctl --user stop voice-typer-prewarm.service` (best-effort) before unlinking unit files.
**Severity:** 🟢 Low

---

### XZ-CFG-02 — `ALLOWED_USER_MODELS` frozen at 8 entries while `MODEL_REGISTRY` has 18 (High)
**Status:** ❌ Not Fixed
**Description:** `config_validators.py:43-52` `ALLOWED_USER_MODELS` has 8 entries. `model_registry.py:98-363` `MODEL_REGISTRY` has 18. `Config.load()` silently resets `model_size` to "small.en" for 10 unlisted models. Tray menu allows selecting them but IPC validator rejects.
**Related Files:**
- `voice_typer/server/config_validators.py`
- `voice_typer/server/model_registry.py`
- `voice_typer/server/config.py` (Config.load reset)
- `voice_typer/client/src/renderer/src/types/config.ts` (ModelSize type)
- `tests/test_config.py` (stale test pins bug)**Fix:** Derive `ALLOWED_USER_MODELS = frozenset(MODEL_REGISTRY.keys())` at import time. Update or remove stale regression test. Update TS `ModelSize` to `string` (or generated union). Add WARNING log on Config.load reset.
**Severity:** 🔴 High

### XZ-CFG-03 — TS declares `bubble_x`/`bubble_y`/`bubble_scale`/`test_duration_seconds` Python doesn't have (High)
**Status:** ❌ Not Fixed
**Description:** `config.ts:135-164` declares 4 fields (2 required) that have no Python counterpart. Renderer calls `set_config({ bubble_x, bubble_y })` — IPC validator drops as unknown. User's bubble position setting silently lost. `bubble_x`/`bubble_y` declared REQUIRED (not optional).
**Related Files:**
- `voice_typer/client/src/renderer/src/types/config.ts`
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`**Fix:** Either add fields to Python Config dataclass + IPC_CONFIG_ALLOWLIST, OR mark `@deprecated` in TS and remove from interface. Make `bubble_x`/`bubble_y` optional (`?:`). Add CI parity test diffing TS interface keys against Python dataclass fields.
**Severity:** 🔴 High

### XZ-CFG-05 — TS `DEFAULT_CONFIG` fixture drift (Medium)
**Status:** ❌ Not Fixed
**Description:** `__tests__/helpers/fixtures.ts:32-187` `DEFAULT_CONFIG` differs from Python Config defaults in 30+ fields. `llm_preset: "default"` is invalid (not in Python Literal). `schema_version: 1` vs Python `_CURRENT_SCHEMA_VERSION = 3`.
**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/helpers/fixtures.ts`
- `voice_typer/server/config.py`**Fix:** Replace hand-maintained fixture with fetch from server's `get_defaults` IPC in test setup hook. OR add CI parity test importing Python Config defaults. Fix `llm_preset` to valid value ("professional").
**Severity:** 🟡 Medium

### XZ-CFG-06 — TS types looser/tighter than Python validators (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.ts:243` includes `"speex"` for `noise_suppression_method` — Python doesn't accept. `:221-228` `audio_preset` includes legacy `"none"`/`"recommended"` — IPC validator excludes. `:103` `llm_preset: string` — IPC enforces enum. `:1-6` `ModelSize` missing `tiny`/`small`/`medium`.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/config.ts`
- `voice_typer/server/config_validators.py`
- `voice_typer/server/config.py`**Fix:** Generate TS types from Python dataclass + IPC_CONFIG_ALLOWLIST via build-time script. Short term: remove `"speex"`, `"none"`, `"recommended"` from TS; change `llm_preset` to union; add missing `ModelSize` values.
**Severity:** 🟡 Medium

### XZ-CFG-07 — Hand-maintained type-coercion lists (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:1645-1947` `_validate_non_numeric_fields` has 4 hand-maintained sets (110+ entries). `int_fields` OMITS `schema_version`. `STARTUP-6` comment shows prior misclassification.
**Related Files:** `voice_typer/server/config.py`**Fix:** Derive coercion lists from `cls.__dataclass_fields__` and field type annotation at class-init time. OR replace with `__post_init__` running each field through type-coercion based on annotation.
**Severity:** 🟡 Medium

### XZ-CFG-08 — Deprecated fields still in dataclass + IPC allowlist + TS type (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:852-948` 10+ deprecated fields still declared. v3 migration pruned 9 keys but left others. IPC allowlist includes some (e.g. `silence_rms_threshold`). TS type includes them.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
- `voice_typer/client/src/renderer/src/types/config.ts`**Fix:** Add v4 migration pruning ALL deprecated fields. Remove from IPC_CONFIG_ALLOWLIST. Remove from `_validate_non_numeric_fields`. Remove from TS type (mark `@deprecated` for one release). Bump `_CURRENT_SCHEMA_VERSION` to 4.
**Severity:** 🟡 Medium

### XZ-CFG-09 — `push_to_talk_hotkey` settable but ignored (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:657-658` declares field. `config_validators.py:682` in IPC allowlist. TS comment at `:59-81` says "DEAD/UNUSED — server never reads it". Hotkey listener uses main `hotkey` for PTT. User sets PTT hotkey expecting it to gate mic — silently doesn't work.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
- `voice_typer/client/src/renderer/src/types/config.ts`**Fix:** Either remove from IPC_CONFIG_ALLOWLIST, OR have validator reject non-empty values with "not yet implemented; use main hotkey field", OR implement the feature.
**Severity:** 🟡 Medium

### XZ-CFG-10 — `apply_preset` silently reverts user toggles (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:1594-1601` runs `apply_preset(instance.audio_preset, instance)` on every `Config.load()`. When `audio_preset != "custom"`, overwrites 7 filter toggle fields with preset values. User toggles `noise_filter_highpass: False` via IPC, restart → preset overwrites to `True`. No warning.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/audio_presets.py`
- `voice_typer/server/config_applier.py`
- `voice_typer/server/service.py` (apply_config)**Fix:** In `service.apply_config`, when individual filter toggle is in `validated` AND `audio_preset != "custom"`, auto-switch `audio_preset` to "custom" (with INFO log) before writing.
**Severity:** 🟡 Medium

### XZ-CFG-11 — Pre-migration backup filename collides (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:1297-1308` `config.json.pre-migration-v{loaded_version}.bak` — no timestamp. Downgrade-then-upgrade overwrites first backup.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add timestamp: `config.json.pre-migration-v{loaded_version}-{int(time.time())}.bak`. Cap retained backups to 3.
**Severity:** 🟢 Low

### XZ-CFG-12 — Inconsistent unknown-key logging (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:1214-1221` `Config.load()` unknown keys logged at WARNING. `config_validators.py:918-923` IPC validator unknown keys logged at DEBUG.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`**Fix:** Promote IPC validator unknown-key log to WARNING (matching Config.load). OR keep DEBUG but add renderer toast when `rejected_keys` non-empty.
**Severity:** 🟢 Low

### XZ-CFG-13 — `schema_version` missing from `int_fields` (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:1783-1796` `int_fields` set OMITS `schema_version` (an int field). Latent — current code always sets `schema_version` to int before construction.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add `"schema_version"` to `int_fields`. OR derive int_fields from dataclass annotations (XZ-CFG-07).
**Severity:** 🟢 Low

### XZ-CFG-14 — Insecure default `llm_api_url` (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:696` `llm_api_url: str = "https://api.openai.com/v1/chat/completions"` pre-fills OpenAI. `cloud_api_url` defaults to empty (safer). Inconsistent.
**Related Files:** `voice_typer/server/config.py`**Fix:** Change `llm_api_url: str = ""` (empty) to match `cloud_api_url`. Renderer should require URL before `llm_polish` can be enabled.
**Severity:** 🟢 Low

### XZ-CFG-15 — `last_load_warnings` undocumented IPC field (Low)
**Status:** ❌ Not Fixed
**Description:** `history_bounds.py:74-83` `_sanitize_config_for_ipc` returns `config.__dict__.copy()` including `last_load_warnings`. TS `VoiceTyperConfig` doesn't declare it.
**Related Files:**
- `voice_typer/server/ipc/history_bounds.py`
- `voice_typer/client/src/renderer/src/types/config.ts`**Fix:** Add `last_load_warnings?: string[] | null` to TS type. OR exclude from `_sanitize_config_for_ipc` and expose via separate IPC field.
**Severity:** 🟢 Low

---

### XZ-EH-001 — `get_volume_backend_status` returns `str(exc)` to IPC (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:820` returns `{"reason": str(exc)}` unredacted to IPC layer. `status_handlers._handle_get_volume_backend_status` passes straight to renderer. Sister methods (`delete_model`, `test_llm_connection`, `export_diagnostics`, `export_gdpr_bundle`, `force_cancel_transcription`) correctly call `redact_secret(redact_url(str(exc)))`.
**Related Files:** `voice_typer/server/service.py`**Fix:** Replace `"reason": str(exc)` with `"reason": redact_secret(redact_url(str(exc)))`. Add `log.warning(...)` before return.
**Severity:** 🔴 High

### XZ-EH-002 — `onboarding_apply` returns `str(exc)` to IPC (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:1452-1453` returns `{"error": str(exc)}` unredacted. Handler `onboarding_handlers.py:233-244` passes straight to renderer. Handler's own `except Exception` never fires because service swallows.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/handlers/onboarding_handlers.py`**Fix:** Change service methods to `raise` so handler's CR-20 catch-all fires. OR at minimum apply `redact_secret(redact_url(str(exc)))` and log server-side at ERROR before returning.
**Severity:** 🔴 High

### XZ-EH-003 — `import_model` returns `str(exc)` per-model to IPC (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:1599-1600` `errors.append({"model": model_name, "error": str(exc)})`. `shutil.Error` string form enumerates source/dest file paths (leaks cache layout).
**Related Files:** `voice_typer/server/service.py`**Fix:** Wrap each `str(exc)` with `redact_secret(redact_url(str(exc)))`. Log per-model error at WARNING server-side.
**Severity:** 🔴 High

### XZ-EH-004 — `download_model` leaks `str(exc)` in 3 places (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:2229-2231` — `_push_progress(0, f"Download failed: {exc}")` (IPC event), `_notify(APP_NAME, f"Failed to download {model_name}: {exc}")` (tray notification), `return {"success": False, "error": str(exc)}` (IPC response). Three concurrent leaks on same error path.
**Related Files:** `voice_typer/server/service.py`**Fix:** Wrap all three interpolations with `redact_secret(redact_url(str(exc)))`. `_notify` should use friendly error mapping.
**Severity:** 🔴 High

### XZ-EH-005 — Silent `except Exception: pass` in `level_monitor_start` (Medium)
**Status:** ❌ Not Fixed
**Description:** `service.py:670` `update_level_processor` failures silently swallowed. User sees "level monitor started" but audio filters may not be applied.
**Related Files:** `voice_typer/server/service.py`**Fix:** Replace `pass` with `log.debug("[SERVICE] level_monitor_start: update_level_processor failed", exc_info=True)`.
**Severity:** 🟡 Medium

### XZ-EH-006 — Silent `except Exception: pass` in download progress polling (Medium)
**Status:** ❌ Not Fixed
**Description:** `service.py:2094` poll loop catches any exception silently. Progress bar freezes for that iteration with no log.
**Related Files:** `voice_typer/server/service.py`**Fix:** Replace `pass` with `log.debug("[SERVICE] download progress poll failed (non-fatal)", exc_info=True)`.
**Severity:** 🟡 Medium

### XZ-EH-007 — Silent `except Exception: pass` in cache invalidation (Low)
**Status:** ❌ Not Fixed
**Description:** `service.py:1611` `invalidate_model_availability_cache` failure silently swallowed. Sister calls at `:989, 2145, 2204` log at DEBUG.
**Related Files:** `voice_typer/server/service.py`**Fix:** Replace `pass` with `log.debug("[SERVICE] import_model: invalidate_model_availability_cache failed", exc_info=True)`.
**Severity:** 🟢 Low

### XZ-EH-008 — Silent `except Exception: pass` × 2 in `_check_resources` (Medium)
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:467-468` (RAM check) and `:578-579` (GPU check) silently swallow. Docstring at `:407-409` claims "failures logged at DEBUG" — code uses `pass`.
**Related Files:** `voice_typer/server/dictation_pipeline.py`**Fix:** Replace `pass` with `log.debug("[RESOURCE] ... check failed (non-fatal)", exc_info=True)`. Fix docstring. Drop redundant `ImportError` from `(ImportError, Exception)`.
**Severity:** 🟡 Medium

### XZ-EH-009 — Silent OSError in Windows registry probe (Medium)
**Status:** ❌ Not Fixed — except OSError still silent at task_scheduler.py:325-326
**Description:** `task_scheduler.py:331-333` `_is_prewarm_registered_registry` `except OSError: return False` with no log. Siblings `_register_prewarm_registry` and `_unregister_prewarm_registry` log at WARNING.
**Related Files:** `voice_typer/server/task_scheduler.py`**Fix:** Add `log.debug("[TASK] _is_prewarm_registered_registry: OSError reading HKCU Run key: %s", exc)` before return False.
**Severity:** 🟡 Medium

### XZ-EH-010 — Silent OSError in elevated-schtasks output read (Medium)
**Status:** ❌ Not Fixed — except OSError: pass still present at task_scheduler.py:579-580
**Description:** `task_scheduler.py:581-585` returns `(exit_code.value, "")` if temp-file read fails. "Access is denied" detection silently fails. `WaitForSingleObject` return value discarded. `GetExitCodeProcess` boolean return not checked.
**Related Files:** `voice_typer/server/task_scheduler.py`**Fix:** Replace `except OSError: pass` with `log.debug(...)`. Check `WaitForSingleObject` return (WAIT_TIMEOUT=124, WAIT_FAILED=1). Check `GetExitCodeProcess` boolean.
**Severity:** 🟡 Medium

### XZ-EH-011 — Silent `except Exception` in POSIX prewarm probe (Low)
**Status:** ❌ Not Fixed — except Exception: return False at task_scheduler.py:625-626 still has no logging
**Description:** `task_scheduler.py:562-566` `is_prewarm_registered` POSIX delegate wrapped in `except Exception: return False` with no log. Siblings `register_prewarm_task` (line 681) and `unregister_prewarm_task` (line 728) DO log.
**Related Files:** `voice_typer/server/task_scheduler.py`**Fix:** Change to `except Exception as exc: log.warning("[TASK] POSIX is_prewarm_registered raised: %s", exc); return False`.
**Severity:** 🟢 Low

### XZ-EH-012 — Silent `except Exception: pass` in onboarding start marker write (Low)
**Status:** ❌ Not Fixed
**Description:** `onboarding_handlers.py:79-84` `.onboarding_started` marker write silently swallowed. Comment claims "non-critical" but PVT-006 rationale says it prevents auto-heal clobbering.
**Related Files:** `voice_typer/server/handlers/onboarding_handlers.py`**Fix:** Replace `pass` with `log.debug("[IPC] onboarding_start: mark_started failed (auto-heal may reset on restart)", exc_info=True)`.
**Severity:** 🟢 Low

### XZ-EH-015 — Implicit ack-vs-error contract is fragile (Medium)
**Status:** ❌ Not Fixed
**Description:** `onboarding_handlers.py:27-40, 154, 185, 209, 224, 239` — 5 handlers delegate ack-vs-error to whether service's return dict contains `"error"` key. If service returns `{"error": None}` (falsy but present), handler reports `ack` for failure.
**Related Files:**
- `voice_typer/server/handlers/onboarding_handlers.py`
- `voice_typer/server/service.py` (onboarding methods)**Fix:** Migrate per documented PVT-G5-095 plan: service should `raise` on failure (typed `OnboardingError`), handler let propagate to outer `except Exception` which calls `_respond_with_error`. Eliminates implicit dict-key contract.
**Severity:** 🟡 Medium

### XZ-EH-017 — `i18n.t` interpolates raw exception into tray notification (Low)
**Status:** ⚠️ Partial — raw str(e) replaced with type(e).__name__ (less leaky but still exposes exception class names instead of fully user-friendly message)
**Description:** `app.py:728-729` `i18n.t("notify.app.undo_failed", error=e)` interpolates raw exception into user-facing tray notification. pynput failures include OS-level error strings, X11 paths, AT-SPI addresses.
**Related Files:** `voice_typer/server/app.py`**Fix:** Pass fixed user-friendly message: `i18n.t("notify.app.undo_failed")` (omit `error=` kwarg). Raw `str(e)` already captured in `log.warning`. Current fix uses `type(e).__name__` which is better than raw str(e) but still exposes class names.
**Severity:** 🟢 Low

### XZ-EH-018 — Unbounded `subprocess.Popen().wait()` holds IPC lock (Low)
**Status:** ⚠️ Partial — main Windows _windows_wait_for_process_exit path fixed with 30min timeout; notepad fallback at config_editor.py:96 and POSIX paths at lines 108-124 still have no timeout
**Description:** `app.py:860` `_open_config_file` Windows notepad path: `subprocess.Popen([notepad, config_file]).wait()` no timeout. Holds `_config_mutation_lock` indefinitely. Subsequent `set_config` IPC calls block forever.
**Related Files:** `voice_typer/server/app.py`**Fix:** Add watchdog `subprocess.Popen(...).wait(timeout=600)` with `TimeoutExpired` handler notifying user.
**Severity:** 🟢 Low

### XZ-EH-019 — `service.py` is 2818-line god-class (Medium)
**Status:** ❌ Not Fixed
**Description:** `VoiceTyperService` class spans ~2650 lines. 13 distinct functional domains in one class. GDPR delete (234 lines), download_model (600 lines), onboarding (325 lines) are themselves god-methods.
**Related Files:** `voice_typer/server/service.py`**Fix:** Extract domain controllers (`GdprController`, `DownloadController`, `OnboardingServiceDelegate`, `MicrophoneTestDelegate`) following `SettingsController` / `AudioQualityController` pattern. Multi-PR refactor.
**Severity:** 🟡 Medium

### XZ-EH-021 — `volume_ducker.initialize()` failure logged at DEBUG (Low)
**Status:** ❌ Not Fixed
**Description:** `service.py:812-814` `log.debug("volume_ducker.initialize failed", exc_info=True)` — invisible at default INFO. Polled every ~2s.
**Related Files:** `voice_typer/server/service.py`**Fix:** Use notify-once pattern: log first occurrence at WARNING, then DEBUG for subsequent.
**Severity:** 🟢 Low

### XZ-EH-022 — `_check_resources` docstring drift (Low)
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:407-409` promises "failures logged at DEBUG level" — code uses `pass` (XZ-EH-008).
**Related Files:** `voice_typer/server/dictation_pipeline.py`**Fix:** Folded into XZ-EH-008 fix.
**Severity:** 🟢 Low

### XZ-EH-023 — `_schtasks_elevated` lacks log for empty-output case (Low)
**Status:** ❌ Not Fixed — task_scheduler.py:574-582 still has zero log lines in elevated-schtasks path
**Description:** `task_scheduler.py:575-585` full elevated-schtasks path has zero log lines. Sibling `_schtasks` (line 481-499) logs WARNING on `FileNotFoundError` and ERROR on `TimeoutExpired`.
**Related Files:** `voice_typer/server/task_scheduler.py`**Fix:** Add `log.debug` or `log.warning` for each failure mode per XZ-EH-010.
**Severity:** 🟢 Low

---

### XZ-R16-01 — Rust poisonable mutexes (High)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-R4-003 — `state.rs:33` poison-safe `lock()` helper exists but ~10 production call sites use raw `.lock().unwrap()`. Panic while holding `state.ws_tx`/`state.child`/`state.token` aborts (release `panic = "abort"`).
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/state.rs`**Fix:** Folded into XZ-R4-003 fix.
**Severity:** 🔴 High

### XZ-R16-02 — `respawn_failed` event unconsumed by renderer (High)
**Status:** ❌ Not Fixed
**Description:** `supervisor.rs:200-201` emits `respawn_failed` when supervisor exhausts 5 respawn attempts. `python-namespace.ts:65-98` only synthesizes `relaunching` + `reconnected`. `useConnection.ts:276-294` sets `"restarting"` on `reconnecting`, only exits via `reconnected`. After `respawn_failed`, renderer UI stuck on "Restarting…" forever.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`**Fix:** Add `makeListener` for `"respawn_failed"` synthesizing `{type: "error", data: {message: "respawn exhausted"}}`. In `useConnection`, subscribe and call `setConnectionStatus("disconnected")` + `setLastError(t("connection.respawnFailed"))`. Add "Relaunch app" button to disconnected UI.
**Severity:** 🔴 High

### XZ-R16-03 — Tauri/Electron error envelope inconsistency (Medium)
**Status:** ❌ Not Fixed
**Description:** `usePython.ts:188-205` Electron path throws `Error` objects. Tauri path: `invoke` rejects with raw string (Tauri v2 behavior). Callers using `err instanceof Error ? err.message : String(err)` work; `Microphone.tsx:278` and `lib/utils/models.ts:252` don't — lose server error on Tauri.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`**Fix:** Wrap `withCommandTimeout` call in try/catch normalizing rejection to `Error`:
```ts
try {
    const result = (await withCommandTimeout(...)) as Record<string, unknown>;
    ...
} catch (err) {
    if (err instanceof Error) throw err;
    throw new Error(typeof err === "string" ? err : "unknown IPC error");
}
```
Add Tauri-path test mocking `invoke` to reject with string.
**Severity:** 🟡 Medium

### XZ-R16-04 — Silent `get_config` catch (Medium)
**Status:** ❌ Not Fixed
**Description:** `useConnection.ts:166-173` outer catch swallows `get_config` error with no `console.error`/`console.warn`.
**Related Files:** `voice_typer/client/src/renderer/src/hooks/useConnection.ts`**Fix:** Add `console.warn("[IPC] get_config connection probe failed (attempt ${retries}/${maxRetries}):", err)`.
**Severity:** 🟡 Medium

### XZ-R16-05 — `usePythonEvent` handler not wrapped (Medium)
**Status:** ❌ Not Fixed
**Description:** `usePython.ts:294-303` `currentCleanup = handlerRef.current(event.data)` NOT wrapped in try/catch. Throwing handler escapes into Tauri/Electron dispatch. `currentCleanup` never updated (stale persists).
**Related Files:** `voice_typer/client/src/renderer/src/hooks/usePython.ts`**Fix:** Wrap: `try { currentCleanup = handlerRef.current(event.data); } catch (err) { console.error("usePythonEvent handler threw:", err); currentCleanup = undefined; }`.
**Severity:** 🟡 Medium

### XZ-R16-06 — ErrorBoundary "Try Again" can loop (Medium)
**Status:** ❌ Not Fixed
**Description:** `ErrorBoundary.tsx:130-139` `handleReset` clears error state but NOT underlying poisoned state (localStorage, malformed theme token). React re-renders same children against same state → same crash.
**Related Files:** `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx`**Fix:** Rename "Try Again" to "Retry render" with tooltip. OR have `handleReset` clear localStorage keys known to feed render. OR only show "Try Again" after "Reset settings" attempted.
**Severity:** 🟡 Medium

### XZ-R16-07 — `globalErrorHandler.test.ts` listener leak (Low)
**Status:** ❌ Not Fixed
**Description:** `globalErrorHandler.test.ts:38-53` `_resetGlobalErrorHandlerStateForTests()` only resets `_installed` flag. Real listeners NEVER removed between tests. `vi.spyOn` + `mockRestore` doesn't remove listeners. After test N, `window` has `2*N` accumulated listeners.
**Related Files:** `voice_typer/client/src/renderer/src/lib/__tests__/globalErrorHandler.test.ts`**Fix:** Track installed listeners and remove in `afterEach`:
```ts
let installedListeners: Array<{type: string; cb: EventListenerOrEventListenerObject}> = [];
beforeEach(() => {
    addEventListenerSpy = vi.spyOn(window, "addEventListener").mockImplementation((type, cb, opts) => {
        installedListeners.push({type, cb: cb as EventListenerOrEventListenerObject});
        return window.addEventListener.call(window, type, cb, opts);
    });
});
afterEach(() => {
    for (const {type, cb} of installedListeners) window.removeEventListener(type, cb);
    installedListeners = [];
    ...
});
```
**Severity:** 🟢 Low

### XZ-R16-08 — Sound manager silent failures (Low)
**Status:** ❌ Not Fixed
**Description:** `sound-manager.ts:145, 181, 296, 365, 371` — 5 catch blocks swallow audio failures with no log.
**Related Files:** `voice_typer/client/src/renderer/src/lib/sound-manager.ts`**Fix:** Add `console.debug("[sound-manager] <specific failure>", err)` to each catch.
**Severity:** 🟢 Low

### XZ-R16-09 — Logging prefix inconsistency (Low)
**Status:** ❌ Not Fixed
**Description:** Renderer logs use mixed prefixes: `[Renderer]`, `[ErrorBoundary]`, `[tauri-bridge]`, `[bubble IPC]`, `[IPC]`, or no prefix.
**Related Files:** Multiple renderer files**Fix:** Adopt single `[renderer:<module>]` convention. Mechanical sweep.
**Severity:** 🟢 Low

### XZ-R16-10 — Dead `_formatReasonForConsole` wrapper (Low)
**Status:** ❌ Not Fixed
**Description:** `globalErrorHandler.ts:130-132` is trivial passthrough to `_formatForConsole`.
**Related Files:** `voice_typer/client/src/renderer/src/lib/globalErrorHandler.ts`**Fix:** Inline the call. Delete `_formatReasonForConsole`.
**Severity:** 🟢 Low

### XZ-R16-11 — Dead `SidecarState.token` field (Low)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-R4-008.
**Related Files:** `src-tauri/src/state.rs`**Fix:** Folded into XZ-R4-008 fix.
**Severity:** 🟢 Low

### XZ-R16-12 — Brittle redacted-sentinel match in ErrorBoundary (Low)
**Status:** ❌ Not Fixed
**Description:** `ErrorBoundary.tsx:236-248` filters `"<redacted>"` via string equality. If backend changes sentinel format, filter fails silently and reset writes sentinel string as actual API key.
**Related Files:** `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx`**Fix:** Either export sentinel constant from backend and import in renderer, OR skip ALL keys whose current value matches `/^<redacted.*>$/i` / is null / is empty string for known secret fields.
**Severity:** 🟢 Low

---

### XZ-R17-02 — Hotkey callbacks no `_shutting_down` guard (High)
**Status:** ❌ Not Fixed
**Description:** `hotkey_dispatcher.py:220-226, 235-241, 279-295` callbacks call `toggle_dictation()` without checking `_shutting_down`. `recording_controller.py:148-201` `_toggle_impl` can START recording during shutdown. `shutdown_controller.py:436-475` stops backends with 5s timeout each but doesn't null refs.
**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/recording_controller.py`
- `voice_typer/server/shutdown_controller.py`**Fix:** Add `_shutting_down` guard at TOP of `_dictation_callback`, `_esc_callback`, `_repaste_callback`, `_toggle_impl`. In `_do_cleanup`, after stopping each backend, set attribute to None (use `hotkey_dispatcher.stop_all()`).
**Severity:** 🔴 High

### XZ-R17-03 — `_crash_excepthook` writes marker non-atomically (Medium)
**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:940-956` `marker_path.write_text(content)` — truncate-then-write, default umask (022 on Linux → 0644 = world-readable). Compare to `crash_recovery.py:191` and `duck_crash_recovery.py:68` which use `_secure_atomic_write`.
**Related Files:** `voice_typer/server/crash_handler.py`**Fix:** Use `_secure_atomic_write(marker_path, content)` — atomic write + O_NOFOLLOW + 0o600 on POSIX.
**Severity:** 🟡 Medium

### XZ-R17-04 — VEH handler only captures 4 of ~15 fatal Windows exception codes (Medium)
**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:59-71` `_CRASH_CODES` only includes STATUS_HEAP_CORRUPTION, STATUS_ACCESS_VIOLATION, STATUS_STACK_BUFFER_OVERRUN, STATUS_FATAL_APP_EXIT. Missing: STATUS_IN_PAGE_ERROR (0xC0000006), STATUS_ILLEGAL_INSTRUCTION (0xC000001D), STATUS_PRIVILEGED_INSTRUCTION (0xC0000096), STATUS_DATATYPE_MISALIGNMENT (0xC0000002), STATUS_BREAKPOINT (0x80000003), STATUS_SINGLE_STEP (0x80000004), etc.
**Related Files:** `voice_typer/server/crash_handler.py`**Fix:** Expand `_CRASH_CODES` to include all fatal SEH codes. Add friendly names. Update `summary_parts` logic in `report_pending_crash`.
**Severity:** 🟡 Medium

### XZ-R17-05 — `duck_crash_recovery.save()` fire-and-forget (Medium)
**Status:** ❌ Not Fixed
**Description:** `duck_crash_recovery.py:53-70` save() called AFTER volume reduced. If write fails (disk full, permissions, NFS hang), crash recovery file NOT written. App crash → next launch `load_stale()` returns None → system volume NEVER restored. User's speakers stuck at 25%.
**Related Files:** `voice_typer/server/duck_crash_recovery.py`**Fix:** (1) Retry save() up to 3 times with 100ms backoff. (2) If all retries fail, DO NOT duck — call `VolumeDucker.restore()` immediately. (3) Surface tray notification. OR persist duck state BEFORE ducking volume.
**Severity:** 🟡 Medium

### XZ-R17-06 — Windows logoff/shutdown: OS kills process before `_do_cleanup` finishes (Medium)
**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:963-1007` `_win32_console_handler` spawns `quit()` on daemon thread. `_do_cleanup` cumulative worst-case ~85s. Windows CTRL_LOGOFF/SHUTDOWN gives ~5 seconds.
**Related Files:** `voice_typer/server/shutdown_controller.py`**Fix:** Add fast-path for `ctrl_logoff_event`/`ctrl_shutdown_event` that skips non-critical cleanup, runs ONLY critical path (crash_recovery.flush, history_db.flush, recorder.stop/discard, _clear_backend_pid_file, CloseHandle) with 1s timeouts each. Target <3s total.
**Severity:** 🟡 Medium

### XZ-R17-07 — SIGTERM during startup race (Medium)
**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:706-757` `quit()` checks `is_main = threading.current_thread() is threading.main_thread()`. Signal watcher spawns on daemon thread → `is_main` is False → `sys.exit(0)` never called. If signal arrives BEFORE main thread enters `tray.run()`, main thread continues startup with torn-down subsystems → None-reference crashes.
**Related Files:** `voice_typer/server/shutdown_controller.py`**Fix:** After `_do_cleanup()` returns in `quit()`, if NOT on main thread, call `os._exit(0)` as last resort (after cleanup_done flag set). OR set flag that main thread checks at key startup milestones.
**Severity:** 🟡 Medium

### XZ-R17-08 — `_save_sync` redundant chmod per transcription (Low)
**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:177-191` `os.chmod(self._path.parent, 0o700)` called every save (after every transcription). Idempotent but wasteful syscall.
**Related Files:** `voice_typer/server/crash_recovery.py`**Fix:** Guard with "first-run" flag: `if not self._dir_ensured: ... self._dir_ensured = True`.
**Severity:** 🟢 Low

### XZ-R17-10 — `duck_crash_recovery.load_stale()` doesn't clear file (Low)
**Status:** ❌ Not Fixed
**Description:** `duck_crash_recovery.py:72-96` returns saved state but does NOT clear file. If caller crashes between `load_stale()` and `clear()`, file persists. Next launch restores same state again — potentially to WRONG level.
**Related Files:** `voice_typer/server/duck_crash_recovery.py`**Fix:** Add "consumed" flag to file. On next launch, if "consumed" is true, return None. OR clear file inside `load_stale()` and have caller re-save if restore fails.
**Severity:** 🟢 Low

### XZ-R17-11 — `_do_cleanup` doesn't null hotkey backend refs (Low)
**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:436-475` stops backends individually (with timeouts) but doesn't null refs. `hotkey_dispatcher.stop_all()` exists and nulls them, but NOT called from shutdown path.
**Related Files:** `voice_typer/server/shutdown_controller.py`**Fix:** Replace individual stop calls with `app.hotkeys.stop_all()` (wrapped in `_run_with_timeout`). OR keep individual stops AND add `setattr(app.hotkeys, "_hotkey_backend", None)` etc.
**Severity:** 🟢 Low

### XZ-R17-13 — `__del__` and `atexit` redundant double-write (Low)
**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:96-113` (atexit) + `:484-516` (`__del__`) both call `_save_sync()` during shutdown. Both serialized by `_save_lock`. Redundant atomic-write.
**Related Files:** `voice_typer/server/crash_recovery.py`**Fix:** Add `_final_save_done` flag both paths check. OR remove `__del__` entirely — atexit is documented safety net.
**Severity:** 🟢 Low

### XZ-R17-14 — `python_crash.*.txt` world-readable on POSIX (Low)
**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:940-956` `write_text` uses default umask (0644). File lives in config_dir root (typically 0755 on multi-user systems). Contains `exc_value` (truncated to 200 chars) — can include user speech fragments.
**Related Files:** `voice_typer/server/crash_handler.py`**Fix:** Folded into XZ-R17-03 fix (use `_secure_atomic_write` for 0o600).
**Severity:** 🟢 Low

---

### XZ-R18-02 — Partial-failure in `_clean_text`/`_apply_punctuation` loses transcription (Medium)
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:198, 213` — `_clean_text()` and `_apply_punctuation()` are the only two middle-pipeline steps NOT wrapped in try/except. If either throws, exception propagates to outer `run()` → tray error + abort. Transcription NEVER saved to crash recovery because `_store_result()` (line 233) runs AFTER.
**Related Files:** `voice_typer/server/dictation_pipeline.py`**Fix:** Wrap `_clean_text()` and `_apply_punctuation()` in try/except matching `_apply_vocabulary` pattern: `log.warning(...)` + notify-once + return original text.
**Severity:** 🟡 Medium

### XZ-R18-03 — `stop-python.ts` missing SIGKILL fallback (Medium)
**Status:** ❌ Not Fixed
**Description:** `stop-python.ts:38-43` sends SIGTERM only, immediately nulls `state.pythonProcess`. Stuck Python (in C extension) → orphaned process. Holds single-instance mutex → next launch fails. `relaunch-app.ts:67-76` has correct SIGKILL fallback pattern.
**Related Files:** `voice_typer/client/src/main/python/stop-python.ts`**Fix:** Add SIGKILL fallback matching `relaunch-app.ts`. Do NOT null `state.pythonProcess` inside kill timer — wait for `exit` event.
**Severity:** 🟡 Medium

### XZ-R18-04 — Early-exit dialog misleading for non-single-instance crashes (Medium)
**Status:** ❌ Not Fixed
**Description:** `start-python.ts:134-137` shows "Only one instance can run" dialog for ALL early exits — including missing model, port collision, token mismatch, syntax error, OOM.
**Related Files:** `voice_typer/client/src/main/python/start-python.ts`**Fix:** Include actual exit code in dialog ("Python backend exited early (code=N). Check logs."). OR read Python's last stderr lines for more specific message. At minimum log exit code prominently.
**Severity:** 🟡 Medium

### XZ-R18-05 — LLM polish failure silent (Medium)
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:886-887` catches but does NOT notify. `llm_polish.py:163-168` same pattern. User pays for LLM API never used, or believes feature broken without diagnostic.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/server/llm_polish.py`**Fix:** Add notify-once pattern (matching `_apply_vocabulary`). Publish `{"type": "llm_polish_failed"}` to event bus for renderer toast.
**Severity:** 🟡 Medium

### XZ-R18-06 — Sidecar WS allows multiple simultaneous authenticated connections (Medium)
**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:462-514` `_handle_connection` — NO check for existing authenticated connection. Old + new connections coexist during overlap window. Both have separate `outbound` queues + writer tasks + event_bus subscribers.
**Related Files:** `voice_typer/server/sidecar_ws.py`**Fix:** Track `server._active_ws_connection` (set on auth, cleared in `finally`). If new connection authenticates while one is active, log warning and close OLD one (or reject new with 1008).
**Severity:** 🟡 Medium

### XZ-R18-07 — `_tcpStartupTimeoutTimer` not reset by `relaunchApp()` (Low)
**Status:** ❌ Not Fixed
**Description:** `tcp-connect.ts:29, 43` — module-level timer, NOT on `state`. `relaunchApp()` (dev mode) resets other state but NOT this timer. Premature timeout dialog + unexpected quit after manual restart during slow first connect.
**Related Files:** `voice_typer/client/src/main/python/tcp-connect.ts`**Fix:** Move `_tcpStartupTimeoutTimer` onto `state` so `relaunchApp()` and `stopPython()` can clear it. OR call `clearTcpStartupTimeout()` at top of `relaunchApp()`.
**Severity:** 🟢 Low

### XZ-R18-08 — Cloud engine fallback no user-visible signal (Low)
**Status:** ❌ Not Fixed
**Description:** `cloud_engines.py:484-489` logs WARNING but never surfaces to user. Cloud outage invisible until user checks logs.
**Related Files:** `voice_typer/server/cloud_engines.py`**Fix:** Publish `{"type": "cloud_fallback_used", "data": {"provider": self.provider}}` to event_bus for renderer toast.
**Severity:** 🟢 Low

### XZ-R18-10 — Duplicated kill/cleanup logic (Low)
**Status:** ❌ Not Fixed
**Description:** `stop-python.ts:38-51` vs `relaunch-app.ts:56-113` (dev) and `:150-193` (prod) — kill-Python + clear-state pattern repeated 3× with subtle inconsistencies (SIGKILL fallback — see XZ-R18-03).
**Related Files:**
- `voice_typer/client/src/main/python/stop-python.ts`
- `voice_typer/client/src/main/python/relaunch-app.ts`**Fix:** Extract shared `_killPythonAndResetState()` helper. Call from `stopPython()`, `relaunchApp()` dev branch, `relaunchApp()` prod branch.
**Severity:** 🟢 Low

### XZ-R18-11 — No max-retries/cooldown on `relaunchApp()` (Low)
**Status:** ❌ Not Fixed
**Description:** `relaunch-app.ts:200-201` production branch — no retry counter, no cooldown. Deterministic Python-side `sys.exit(0)` loop would drain battery + spam system.
**Related Files:** `voice_typer/client/src/main/python/relaunch-app.ts`**Fix:** Add restart counter (max 3 restarts per 60s window) stored in temp file. If exceeded, show dialog + `app.quit()`.
**Severity:** 🟢 Low

### XZ-R18-12 — `handle-message.ts` broadcasts unknown push events without type validation (Low)
**Status:** ❌ Not Fixed
**Description:** `handle-message.ts:133-143` — no validation that `msg.type` is string or in known set. Unknown events fall through to broadcast-to-renderer.
**Related Files:** `voice_typer/client/src/main/python/handle-message.ts`**Fix:** Add type guard: `if (typeof msg.type !== "string") { console.warn("[TCP] push event missing type string, dropping"); return; }`. Optionally maintain known-event allowlist.
**Severity:** 🟢 Low

### XZ-R18-13 — Stale docstring in `app.py` `restart_app()` (Low)
**Status:** ❌ Not Fixed
**Description:** `app.py:980, 985` docstring references `relaunch_electron` — actual code publishes `relaunch_app`.
**Related Files:** `voice_typer/server/app.py`**Fix:** Folded into XZ-R18-01 fix.
**Severity:** 🟢 Low

---

### XZ-LOG-01 — Python stderr fallback unredacted (High)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:2350, 2546` `print(buf.getvalue(), file=sys.stderr)` — `buf` holds raw traceback from `traceback.print_exc(file=buf)`. `/tmp` fallback at line 2366 correctly redacts via `_redact_text`; stderr fallback does not. Rust `spawn.rs:120` captures sidecar stderr and logs via `log::info!` UNREDACTED (Rust `CombinedLogger` has no PII filter — XZ-LOG-02).
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Change `print(buf.getvalue(), file=sys.stderr)` to `print(_redact_text(buf.getvalue()), file=sys.stderr)`. Apply to both instances (lines 2350 and 2546).
**Severity:** 🔴 High

### XZ-LOG-02 — No PII redaction in Rust `CombinedLogger::log` (High)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/platform/logging.rs:109-143` `log()` writes `record.args()` verbatim to file + stderr. Python has `PIIRedactionFilter`; Rust has nothing. Most active vector is `spawn.rs:120 [SIDECAR] stderr: {}` piping arbitrary Python sidecar stderr into Rust file log.
**Related Files:** `src-tauri/src/platform/logging.rs`**Fix:** Port minimal redaction filter to Rust. Reuse same regex set as Python `PIIRedactionFilter` (email, phone, SSN, CC, IBAN, `Bearer …`, `Token …`, `sk-…`, `user:pass@host`). Implement as `fn redact(s: &str) -> String` called inside `CombinedLogger::log` before `format!`.
**Severity:** 🔴 High

### XZ-LOG-03 — No PII redaction in Electron loggers (Medium)
**Status:** ❌ Not Fixed
**Description:** `logging.ts:218-233` `formatLine` + `:395-409` `formatArgsForFile` write raw to disk. `main-window.ts:400-421` `console-message` event forwards renderer console output to `electron-runtime.log` + `electron-renderer-errors.log` unredacted. `cleanConsoleMsg` only strips printf specifiers.
**Related Files:**
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`**Fix:** Port TS redaction helper mirroring Python's `redact_pii`/`redact_secret`/`redact_url`. Apply inside `formatLine` and `formatArgsForFile` before returning line.
**Severity:** 🟡 Medium

### XZ-LOG-04 — Inconsistent log file locations (Medium)
**Status:** ❌ Not Fixed
**Description:** Python: `<config_dir>/voice-typer.log`. Prewarm: `<config_dir>/prewarm.log`. Rust: `<config_dir>/logs/voice-typer.log` (only one in `logs/` subdir). Electron: 5 files at config_dir root. 7 log files scattered across 2 directory levels.
**Related Files:**
- `voice_typer/server/log.py`
- `voice_typer/server/prewarm/logging_setup.py`
- `src-tauri/src/platform/logging.rs`
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/bootstrap.ts`**Fix:** Align on ONE convention. Either move Rust log to `<config_dir>/voice-typer-rust.log` (root, matches Python/Electron), OR move ALL logs to `<config_dir>/logs/`.
**Severity:** 🟡 Medium

### XZ-LOG-05 — Inconsistent log line format (Medium)
**Status:** ❌ Not Fixed
**Description:** Python: `YYYY-MM-DD  HH:MM:SS [session_id] [thread] LEVEL [component] msg` (double space, no millis). Rust: `YYYY-MM-DD HH:MM:SS.mmm LEVEL target file:line -- msg` (single space, millis, no session_id). Electron: `ISO-8601Z [LEVEL] msg {json_args}`.
**Related Files:** (same as XZ-LOG-04)**Fix:** Align timestamp format (ISO-8601). Better: add structured JSON formatter to Rust and Electron matching Python's `_JsonFormatter` schema. Gate behind `VOICE_TYPER_LOG_JSON` env var.
**Severity:** 🟡 Medium

### XZ-LOG-06 — Duplicated Electron loggers (Medium)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-R5-007.
**Related Files:** `voice_typer/client/src/main/logging.ts`**Fix:** Folded into XZ-R5-007 fix.
**Severity:** 🟡 Medium

### XZ-LOG-07 — Stale comment in `handlers/_log.py` (Low)
**Status:** ❌ Not Fixed
**Description:** `handlers/_log.py:21-30` claims consolidation aspirational, 10 of 13 handlers declare inline. Reality: 0 handlers declare inline; 9 of 15 import from `_log.py`; 6 inherit `HandlerBase`.
**Related Files:** `voice_typer/server/handlers/_log.py`**Fix:** Update docstring to reflect reality.
**Severity:** 🟢 Low

### XZ-LOG-08 — No session_id/correlation_id in Rust/Electron (Low)
**Status:** ❌ Not Fixed
**Description:** Python's `_SessionFilter` injects 8-char per-process session_id + `_correlation_id` contextvar. Rust `CombinedLogger` and Electron `formatLine` have neither. Cross-process correlation relies on timestamp proximity only.
**Related Files:**
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/main.rs` (session_id generation)
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/bootstrap.ts`**Fix:** Generate session_id in Rust `main.rs` at startup, prepend to every `CombinedLogger::log` line. Pass to Python sidecar via `VOICE_TYPER_SESSION_ID` env var. For Electron, generate in `bootstrap.ts`, pass to Python via spawn env, include in `formatLine`.
**Severity:** 🟢 Low

### XZ-LOG-09 — `log_rate_limited` unevenly applied (Low)
**Status:** ❌ Not Fixed
**Description:** `log_rate_limit.py` helper exists but used at only 3 call sites. Cloud retry loops, WS heartbeat-miss, WS invalid-JSON — all match flood-risk description but don't use helper.
**Related Files:**
- `voice_typer/server/cloud_engines.py`
- `voice_typer/server/sidecar_ws.py`
- `src-tauri/src/sidecar/ws.rs`**Fix:** Audit listed call sites. Apply `log_rate_limited` to cloud retry-loop warnings, WS heartbeat-miss, WS invalid-JSON/unexpected-frame.
**Severity:** 🟢 Low

### XZ-LOG-10 — `RotatingFileHandler` not inter-process safe (Low)
**Status:** ❌ Not Fixed
**Description:** `log.py:674-680` `RotatingFileHandler` uses `threading.Lock` (thread-safe, NOT inter-process safe). Main app + prewarm process both write to same `voice-typer.log`.
**Related Files:** `voice_typer/server/log.py`**Fix:** Use `WatchedFileHandler` (re-opens file on each emit, detects rotation by inode change). OR add `fcntl.flock`/`msvcrt.locking` inter-process lock around rotation. OR route ALL prewarm logs to `prewarm.log` only.
**Severity:** 🟢 Low

### XZ-LOG-11 — `print()` in `recorder.py:40` (Low)
**Status:** ❌ Not Fixed — print(f"Captured: {result}") still present at native_hotkeys/recorder.py:40
**Description:** `native_hotkeys/recorder.py:40` `print(f"Captured: {result}")` — debug print left in production. Under `--ws`/`--port` mode, triggers Rust host's "unexpected stdout line" warning per hotkey capture.
**Related Files:** `voice_typer/server/native_hotkeys/recorder.py`**Fix:** Replace with `log.info("[HOTKEY] Captured: %s", result)` (or remove if debugging leftover).
**Severity:** 🟢 Low

### XZ-LOG-12 — `PIIRedactionFilter` blind spots (Low, Informational)
**Status:** ❌ Not Fixed
**Description:** `security.py:178-204` redacts structured PII patterns but not free-form transcription text. Mitigated by logging only SHA-256 hash + length. Risk: future regression logging `text` directly.
**Related Files:**
- `voice_typer/server/security.py`
- `voice_typer/server/dictation_pipeline.py`**Fix:** Add regression test grepping for `log.*(text|transcript|partial|final_text|result)` calls interpolating variable directly. Document convention in CONTRIBUTING.md.
**Severity:** 🟢 Low

---

### XZ-CC-1 — Duplicated VAD default constants (High)
**Status:** ❌ Not Fixed
**Description:** `vad_processor.py:73-78` (canonical) and `recording/recorder.py:160-165` (compat shim) define same 6 constants. Recorder comment admits "no longer referenced internally after VadProcessor extraction". 4 of 6 unused.
**Related Files:**
- `voice_typer/server/vad_processor.py`
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/__init__.py`**Fix:** Delete 4 dead constants (`_DEFAULT_VAD_CALIBRATION_DURATION`, `_DEFAULT_VAD_HANGOVER_FRAMES`, `_DEFAULT_VAD_SILENCE_FRAMES`, `_DEFAULT_VAD_SPEECH_FRAMES`) from `recorder.py:162-165` and from `recording/__init__.py:171-176, 218-223`. For 2 used, import from `vad_processor`.
**Severity:** 🔴 High

### XZ-CC-2 — Duplicated noise-filter defaults (Medium)
**Status:** ❌ Not Fixed
**Description:** `audio_chain_builder.py:128-153` `_DEFAULTS` dict mirrors `config.py:942-986` Config dataclass defaults. No sync mechanism (no CI test).
**Related Files:**
- `voice_typer/server/audio_chain_builder.py`
- `voice_typer/server/config.py`**Fix:** Replace `_DEFAULTS` with `Config()` instance snapshot: `_DEFAULTS = {f.name: getattr(Config(), f.name) for f in fields(Config) if f.name.startswith("noise_filter_")}`. OR add CI test mirroring `test_hotkey_reserved_sync.py`.
**Severity:** 🟡 Medium

### XZ-CC-3 — Duplicated LLM default URL + model (Medium)
**Status:** ❌ Not Fixed
**Description:** `llm_polish.py:110-111` `_DEFAULT_URL`/`_DEFAULT_MODEL` duplicate `config.py:696-697` Config dataclass defaults. `LLMPolish.__init__` accepts `api_url`/`model` as optional kwargs and falls back to module-level constants.
**Related Files:**
- `voice_typer/server/llm_polish.py`
- `voice_typer/server/config.py`**Fix:** Delete `_DEFAULT_URL`/`_DEFAULT_MODEL` from `llm_polish.py`. Make `api_url`/`model` required kwargs, OR import from Config. Update call sites to forward Config values.
**Severity:** 🟡 Medium

### XZ-CC-4 — Duplicated secret-redaction implementation (High)
**Status:** ❌ Not Fixed
**Description:** `credential_store.py:253-279` `_redact_sensitive` parallel implementation. Threshold mismatch: 32+ char vs `_secrets._KEY_PATTERNS` 20+ char. Provider coverage mismatch: `gsk_` (Groq) only in credential_store. Flag-form coverage mismatch: `_secrets` has `_FLAG_VALUE_PATTERN`/`_BARE_KEY_VALUE_PATTERN`, credential_store doesn't.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/_secrets.py`**Fix:** Folded into XZ-SEC-07 fix (delegate to `_secrets.redact_secret`, add `gsk_` + `sk-ant-` prefixes to canonical helper).
**Severity:** 🔴 High

### XZ-CC-5 — Dead compat-shim VAD constants (Low)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-CC-1 — 4 of 6 `_DEFAULT_VAD_*` in `recorder.py` unused.
**Related Files:** (same as XZ-CC-1)**Fix:** Folded into XZ-CC-1 fix.
**Severity:** 🟢 Low

### XZ-CC-6 — `ToggleDictationResult.recording` phantom field (Medium)
**Status:** ❌ Not Fixed
**Description:** `ipc.ts:478-480` declares `recording: boolean` as REQUIRED. Python `_handle_toggle_dictation` returns `{"type": "ack"}` with NO `data` field. `Home.tsx:635` calls `await call("toggle_dictation")` with `T = unknown` — result discarded. Future contributor writing `const { recording } = await call<ToggleDictationResult>(...)` gets `recording: undefined` while TS type-checks as `boolean`.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts`
- `voice_typer/server/handlers/dictation_handlers.py`**Fix:** Either (a) update Python handler to populate `resp["data"] = {"recording": self.service.is_recording()}` so type matches wire, OR (b) change TS type to `ToggleDictationResult = undefined` and update mapping. Delete `ResponseData<T>` if no consumer.
**Severity:** 🟡 Medium

### XZ-CC-7 — `TranscriptionFinalEvent.duration_ms?` never sent (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc.ts:105-108` declares optional `duration_ms?: number`. Python `dictation_pipeline.py:1091-1096` publishes `{text: string}` only. Comment claims "mirrors wire format" — misleading. Line reference `:911` stale (actual `:1093`).
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts`
- `voice_typer/server/dictation_pipeline.py`**Fix:** Either (a) populate `duration_ms` in Python sender, OR (b) delete `duration_ms?` from TS type. Update stale line reference.
**Severity:** 🟢 Low

### XZ-CC-8 — `requirements.txt` missing 2 macOS pyobjc deps (High)
**Status:** ❌ Not Fixed
**Description:** `pyproject.toml:155, 163` declares `pyobjc-framework-CoreFoundation` and `pyobjc-framework-ApplicationServices` (macOS-only). `requirements.txt:43-64` declares `pyobjc-core`, `pyobjc-framework-CoreAudio`, `pyobjc-framework-Cocoa` but NOT the two above. `pip install -r requirements.txt` on macOS → silently broken mic watcher + accessibility probe.
**Related Files:**
- `requirements.txt`
- `pyproject.toml`**Fix:** Add `pyobjc-framework-CoreFoundation>=9.0; sys_platform == 'darwin'` and `pyobjc-framework-ApplicationServices>=9.0; sys_platform == 'darwin'` to `requirements.txt`. Better: delete `requirements.txt` entirely and document `pip install -r requirements-lock.txt` as only supported path.
**Severity:** 🔴 High

### XZ-CC-9 — Three competing requirements files (Medium)
**Status:** ❌ Not Fixed
**Description:** `requirements.txt` header claims hash-pinned but contains ZERO hashes. `pip install --require-hashes -r requirements.txt` will FAIL. `requirements-lock.txt` actually has hashes. Three sources of truth with no sync.
**Related Files:**
- `requirements.txt`
- `requirements-lock.txt`
- `pyproject.toml`**Fix:** Delete `requirements.txt`. Update `README.md`/`CONTRIBUTING.md` to point developers at `pip install -r requirements-lock.txt` or `uv sync`. If fast-path needed, document `pip install -e .` (reads `pyproject.toml` directly).
**Severity:** 🟡 Medium

### XZ-CC-10 — Cargo dep version drift (Medium)
**Status:** ❌ Not Fixed
**Description:** `Cargo.toml:62` `rand = "0.8"` → Cargo.lock has BOTH `rand 0.8.7` and `rand 0.9.5` (dual-resolution, ~50-100 KB bloat). `Cargo.toml:53` `tokio-tungstenite = "0.24"` (current 0.27.x, 3 minors behind). `Cargo.toml:49` `enigo = "0.2"` (current 0.14.x, 12 minors behind).
**Related Files:** `src-tauri/Cargo.toml`**Fix:** Bump `rand = "0.9"` (update `thread_rng()` → `rng()`). Bump `tokio-tungstenite = "0.27"`. Leave `enigo` for separate PR (larger refactor).
**Severity:** 🟡 Medium

### XZ-CC-11 — 62 `# type: ignore` / `pyrefly: ignore` in security-critical code (Medium)
**Status:** ❌ Not Fixed
**Description:** 62 occurrences across 29 Python files. Top concentrations: `native_adapter.py` (9), `clipboard_snapshot.py` (7), `credential_store.py` (6). Mix of legitimate platform-only import suppression + real type holes. Pyrefly baseline's `errors: []` hides 116 unsuppressed errors.
**Related Files:** cross-cutting (29 files)**Fix:** Audit 62 suppression sites — verify each suppression reason documented inline. For 34 non-platform-specific pyrefly errors, fix one-by-one. Pin `pyrefly==1.1.1` in `pyproject.toml [dev]` + CI.
**Severity:** 🟡 Medium

### XZ-CC-12 — Stale TODO Fix-A cluster (Low)
**Status:** ❌ Not Fixed
**Description:** 5 cross-file TODOs about replacing `_wait_for_relaunch_ack` with public IPCServer API: `app.py:1074, 1169, 1191`, `hotkey_dispatcher.py:51, 55`. None have date/PR reference.
**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/hotkey_dispatcher.py`**Fix:** Either execute Fix-A (add `IPCServer.wait_for_relaunch_ack(timeout) -> bool` public method, replace 3 `app.py` getattr call sites, delete 5 TODOs), OR convert to tracked issue with owner + date.
**Severity:** 🟢 Low

### XZ-CC-13 — Stale TODO migrate-tests cluster (Low)
**Status:** ❌ Not Fixed
**Description:** 4 identical TODOs across 3 packages: `prewarm/__init__.py:110`, `recording/__init__.py:49, 320`, `server_platform/__init__.py:80`. All reference "CR-67 / TECH-DEBT". ~500 LOC of `__init__.py` boilerplate for test-patch compatibility.
**Related Files:**
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/server_platform/__init__.py`**Fix:** Tracked separately as CR-67/TECH-DEBT. Update TODOs with current date + tracking issue link. OR if migration actively worked, annotate "IN PROGRESS by <owner>, ETA <date>".
**Severity:** 🟢 Low

### XZ-CC-14 — `package.json` `//devDependencies` "DO NOT DOWNGRADE" comment (Low)
**Status:** ❌ Not Fixed
**Description:** `package.json:53` comment exists because prior agent downgraded TS to 5.6.3, breaking `npm ci`. Project on bleeding edge (TS 7.0.2, electron-vite 6.0.0-beta.1 pre-release).
**Related Files:** `voice_typer/client/package.json`**Fix:** Pin TypeScript to exact version (`"typescript": "7.0.2"` not `^7.0.2`). Migrate `electron-vite` off beta when 6.0.0 ships. Move warning to CONTRIBUTING.md.
**Severity:** 🟢 Low

### XZ-CC-15 — `pyrefly-baseline.json` `errors: []` while CI reports 116 errors (High)
**Status:** ❌ Not Fixed
**Description:** Baseline file's own `_current_state_2026_07_22` comment admits: "Until those land, the pyrefly check step in CI will continue to exit 1 (because pyrefly reports 116 unsuppressed errors and the baseline is empty)". 34 non-platform-specific real type bugs hidden from CI.
**Related Files:** `pyrefly-baseline.json`**Fix:** (a) Pin `pyrefly==1.1.1` in `pyproject.toml [dev]` + `.github/workflows/build.yml`. (b) Fix 34 non-platform-specific real type bugs. (c) For 82 platform-specific false positives, regenerate baseline from real `pyrefly check` run on platform-appropriate interpreter. (d) DO NOT keep `errors: []` as "conservative floor" — silence-by-deletion pattern.
**Severity:** 🔴 High

### XZ-CC-16 — `ResponseData<T>` mapped type exported but never imported (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc.ts:524-549` 26-line conditional-types cascade. `grep ResponseData` returns 1 hit — the declaration. `usePython.call` uses `async <T = unknown>(type: string, ...)` — generic over `T` with default `unknown`, NOT constrained to `PythonRequest["type"]`.
**Related Files:** `voice_typer/client/src/renderer/src/types/ipc.ts`**Fix:** Either (a) wire `ResponseData<T>` into `usePython.call` by constraining generic, OR (b) delete `ResponseData<T>` and dead result types (`ToggleDictationResult`, `ToggleFavoriteResult`, `SaveVocabularyResult`).
**Severity:** 🟢 Low

---

### XZ-14-01 — Circuit breaker public API missing (High)
**Status:** ❌ Not Fixed
**Description:** `asr_registry.py` lacks `reset_failures` and `failure_count` public methods. 4 tests in `test_asr_registry_lifecycle.py` fail with `AttributeError`. Disabled backend permanently unreachable for session.
**Related Files:**
- `voice_typer/server/asr_registry.py`
- `tests/test_asr_registry_lifecycle.py`**Fix:** Add `failure_count(name) -> int` and `reset_failures(name) -> None` public methods. Add IPC handler `reset_backend`. Add Settings UI affordance.
**Severity:** 🔴 High

### XZ-14-03 — `validate_config` dead code (Medium)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-CFG-04.
**Related Files:**
- `voice_typer/server/config_validators.py`
- `voice_typer/server/config.py`**Fix:** Folded into XZ-CFG-04 fix.
**Severity:** 🟡 Medium

### XZ-14-07 — `VOICE_TYPER_CONFIG_DIR` validation weaker than `HF_HOME` (Low)
**Status:** ❌ Not Fixed
**Description:** `env_validation.py:52-58` only rejects NUL bytes via `_path_pattern = re.compile(r"^[^\0]+$")`. `HF_HOME` block at `:87-111` calls `_validate_path_safety(Path(hf_home), Path.home())`.
**Related Files:** `voice_typer/server/env_validation.py`**Fix:** Add `_validate_path_safety(Path(config_dir), Path.home())` call to `VOICE_TYPER_CONFIG_DIR` block, mirroring `HF_HOME` pattern.
**Severity:** 🟢 Low

### XZ-14-08 — `language` field no format check (Low)
**Status:** ❌ Not Fixed
**Description:** `config_validators.py:666` `_VALIDATOR_LANGUAGE = _make_str_validator(max_len=16)` — no check that value is recognized language code. User can set `language="zzzzz"` or `language="english"` (both pass) → Whisper load fails with cryptic error.
**Related Files:** `voice_typer/server/config_validators.py`**Fix:** Add regex validator accepting common Whisper language codes (2-letter ISO 639-1 + 3-letter extensions). Or source allowlist from `whisper.tokenizer.LANGUAGES` at module init.
**Severity:** 🟢 Low

### XZ-14-10 — `config_validators.py` 1102-line monolith (Low)
**Status:** ❌ Not Fixed
**Description:** Mixes validator primitives, hotkey pipeline, IPC field schema in one file. `IPC_CONFIG_ALLOWLIST` is 220-line inline literal.
**Related Files:** `voice_typer/server/config_validators.py`**Fix:** Split into `validators/` package: `_primitives.py`, `_hotkey.py`, `_schema.py`, `_api.py`. Re-export via package `__init__.py`.
**Severity:** 🟢 Low

### XZ-14-11 — Hotkey_reserved.json intentional duplication (Low, Informational)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/hotkey_reserved.json` ↔ `voice_typer/client/src/renderer/src/data/hotkey_reserved.json` byte-identical. Documented as Vite HMR crash workaround. Drift caught by `tests/test_hotkey_reserved_sync.py`.
**Related Files:**
- `voice_typer/server/hotkey_reserved.json`
- `voice_typer/client/src/renderer/src/data/hotkey_reserved.json`**Fix:** Re-test whether Vite HMR crash reproduces with current Vite version. If fixed, switch frontend to `@server/hotkey_reserved.json` import. If not fixed, add pre-commit hook running sync test.
**Severity:** 🟢 Low

### XZ-14-13 — Path env vars leak username in logs (Low)
**Status:** ❌ Not Fixed
**Description:** Same as XZ-R3-10.
**Related Files:** `voice_typer/server/env_validation.py`**Fix:** Folded into XZ-R3-10 fix.
**Severity:** 🟢 Low

### XZ-14-15 — `_make_custom_theme_validator` no key-count cap (Low)
**Status:** ❌ Not Fixed
**Description:** `config_validators.py:245-275` validates 6 required keys per mode but doesn't reject extra keys, doesn't bound total dict size, doesn't bound length of hex color values.
**Related Files:** `voice_typer/server/config_validators.py`**Fix:** Add `if len(v) > 64: return "too many top-level keys"` and `if len(mode_dict) > 64: return f"{mode} has too many keys"`.
**Severity:** 🟢 Low

### XZ-14-16 — Migration runner bumps schema_version even on failure (Low)
**Status:** ❌ Not Fixed
**Description:** `config.py:1224-1290` migration runner continues with partially-migrated data on migrator exception, bumps `schema_version` to `_CURRENT_SCHEMA_VERSION`. User's config stuck in half-migrated state.
**Related Files:** `voice_typer/server/config.py`**Fix:** On migrator exception, do NOT bump `schema_version` — leave at `version - 1` so migration re-runs. Include timestamp or failed-version in `.bak` filename.
**Severity:** 🟢 Low

### XZ-14-17 — `asr_setup.py` docstring/count mismatch (Low)
**Status:** ❌ Not Fixed
**Description:** `asr_setup.py:162-163, 319-324, 412-420` `_MAX_DOWNLOAD_RETRIES = 4`. Docstring says "max 4 retries" (5 total attempts). Inline comment lists "1s, 2s, 4s, 8s" (4 delays = 4 attempts).
**Related Files:** `voice_typer/server/asr_setup.py`**Fix:** Update docstring to "max 4 attempts (1 initial + 3 retries with exponential backoff 1s, 2s, 4s, 8s)". OR rename constant to `_MAX_DOWNLOAD_ATTEMPTS`.
**Severity:** 🟢 Low

---

End of XZ-session findings.
```

### Findings from Session 6
```
# Comprehensive Review — Session XS (Group 6: Testing & CI)

**Session:** XS (Full-Review mode, GROUP 6 — Testing & CI)
**Total findings:** 105

**Severity breakdown:** 8 Critical, 30 High, 50 Medium, 17 Low

All findings scoped to Group 6 (Testing & CI) categories ONLY:
- Testing infrastructure
- Test coverage gaps & flaky tests
- Existing failing tests
- Existing warnings and errors
- CI/CD
- Build pipeline
- Dependency & supply-chain health

---

### [XS-10] — CI lint pipeline is red — ruff ratchet grew (180→192), F-rule hard gate fails, pyrefly fails
**Status:** ❌ Not Fixed
**Description:** Three independent failing steps in `.github/workflows/build.yml`: (1) `Ruff (F-rule hard gate)` line 110 — 20 F violations (19 F841 + 1 F821), exit 1 (no `|| true`). (2) `Ruff (ratchet compare against baseline)` line 112 — script exits 1 (192 > 180). The +17 E501 regression is entirely in `tests/test_i18n_completeness.py` (long inline comments in dict literals). The +1 B905 is `tests/test_history_db_fts5_search.py:135` (`zip` without `strict=True`). The 19 F841s are in tauri mig15/16/17 test scaffolding (unused `sw = _import_sidecar_ws()`). The 1 F821 is `tests/test_clipboard_restore_args.py:31` (annotation references undefined `ClipboardManager`). (3) `Run pyrefly type check` line 157 — 116 unsuppressed errors, exit 1 (see XS-1).
**Root Cause:** Multiple root causes: (a) E501 regression from long comments added to i18n test; (b) B905 from missing `strict=True`; (c) F841 from intentional-but-undeclared side-effect imports in tauri tests; (d) F821 from real annotation bug; (e) pyrefly baseline empty.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml:110,112,157`
- `tests/test_i18n_completeness.py`
- `tests/test_history_db_fts5_search.py:135`
- `tests/test_clipboard_restore_args.py:31`
- `ruff-baseline.json`
- `pyrefly-baseline.json`
**Fix:** (1) Fix the 17 E501 violations in `tests/test_i18n_completeness.py` (wrap or shorten the long inline comments). (2) Add `strict=True` to the `zip(timestamps, texts)` call in `tests/test_history_db_fts5_search.py:135`. (3) Fix the F821 in `tests/test_clipboard_restore_args.py:31` (import `ClipboardManager` at module scope or remove the annotation). (4) Fix the 19 F841s in tauri mig15/16/17 tests (use `_ = _import_sidecar_ws()` or `del sw`). (5) Regenerate `ruff-baseline.json` via `ruff check voice_typer/ tests/ scripts/ conftest.py --output-format=json | python scripts/ruff_ratchet_check.py --regenerate --stdin` to lock in the reduced count. (6) Address XS-1 for pyrefly.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-11] — IPC error-envelope `code` field missing `server.` prefix (33+ handler test failures)
**Status:** ❌ Not Fixed
**Description:** 32 handler tests across `tests/handlers/test_config_handlers.py`, `test_dictation_handlers.py`, `test_history_handlers.py`, `test_level_monitor_handlers.py`, `test_microphone_handlers.py`, `test_microphone_test_handlers.py`, `test_model_handlers.py`, `test_onboarding_handlers.py`, `test_r13_f3_error_envelope_code_field.py` (8 tests), `test_status_handlers.py`, `test_system_handlers.py` (2), `test_templates_handlers.py`, `test_vocabulary_handlers.py` — plus 8 errors in `tests/tauri/mig19`. All fail with `AssertionError: assert 'internal_error' == 'server.internal_error'`. Production returns `{"data": {"code": "internal_error", "message": "internal error"}}` but tests expect `"server.internal_error"`. The R13-F3 test specifically codifies the contract, so production is what drifted.
**Root Cause:** The IPC error-envelope contract was changed (the `server.` prefix was dropped from the `code` field) but the tests still enforce the original contract. The `code` field is documented to use `server.<error_name>` format.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/handlers/_base.py`
- `voice_typer/server/ipc_server.py`
- `tests/handlers/`
**Fix:** Restore the `server.` prefix in the IPC error envelope `code` field. The constant in `voice_typer/server/handlers/_base.py` (around line 175) should be `"server.internal_error"` not `"internal_error"`. Audit all error code constants for the same drift. Run `pytest tests/handlers/ -q --timeout=30 -o addopts=''` to verify.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-12] — Recording-pipeline refactor dropped multiple internal contracts (54 test failures)
**Status:** ❌ Not Fixed
**Description:** The ARCH-045 god-module split (`recording.py` → `recording/` package) dropped or renamed many internal contracts. 36 failures in `tests/test_recording.py` + 5 in `test_recorder_double_resample.py` + 5 in `test_i5_retry_fixes.py` + 5 in `test_recording_audio_processor.py` + 2 in `test_recording_and_audio.py` + 2 in `test_audio_filters.py` + 1 in `test_audio_processor.py` + 2 in `test_audio_quality*.py`. Specific dropped contracts: (a) `rms_callback(chunk_rms, chunk_peak, filtered)` 3-arg shape (F-04, 2 failures); (b) `_stop_buffer_clear_worker` method on `buffer` module (5 failures); (c) `_set_thread_registry` (F-38); (d) `_THREAD_REGISTRY` constant; (e) host-rank ordering for macOS CoreAudio / Linux ALSA (6 failures); (f) counter-reset semantics; (g) start-lock existence source-string checks; (h) sample-rate rebuild paths; (i) preroll-filtered/xrun-rearm/threshold-constants contracts.
**Root Cause:** The recording module was split into a package but the internal contracts that tests pin (source-string anchors, method names, attribute names) were not preserved during the refactor.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/buffer.py`
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/recording/resampling.py`
- `voice_typer/server/recording/_recorder_split.py`
- `tests/test_recording.py`
- `tests/test_i5_retry_fixes.py`
- `tests/test_recorder_double_resample.py`
- `tests/test_recording_audio_processor.py`
**Fix:** Restore the dropped contracts in `voice_typer/server/recording/`: (1) `rms_callback` must be called with 3 args `(chunk_rms, chunk_peak, filtered)` so VAD can run on filtered audio; (2) `buffer` module must expose `_stop_buffer_clear_worker` and `_set_thread_registry`; (3) `_THREAD_REGISTRY` constant must exist; (4) host-rank ordering must match the documented priority (macOS CoreAudio rank 0, Linux ALSA rank 0); (5) counter-reset on start; (6) start-lock as `threading.Lock`; (7) sample-rate mismatch rebuild. For tests that are pure source-string inspections that should follow the refactor, update the test anchors. Run `pytest tests/test_recording*.py tests/test_i5_retry_fixes.py tests/test_audio*.py -q --timeout=30 -o addopts=''` to verify.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-13] — Config.set_mutation_lock API missing (11 test failures)
**Status:** ❌ Not Fixed
**Description:** `tests/test_config_lock.py:426` and 10 siblings in `TestConfigMutationLock` fail with `AttributeError: 'Config' object has no attribute 'set_mutation_lock'`. The `TestConfigMutationLock` class exercises a documented contract that `Config` should expose `set_mutation_lock(threading.RLock())` to serialize concurrent saves. Also drags down `tests/regressions/concurrency_test.py::TestConfigMutationLockSharedAcrossIpc::test_ipc_set_config_uses_lock`.
**Root Cause:** A thread-safety feature was removed or never landed in `voice_typer/server/config.py`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
- `tests/test_config_lock.py`
- `tests/regressions/concurrency_test.py`
**Fix:** Add `set_mutation_lock(self, lock: threading.RLock)` method to `Config` in `voice_typer/server/config.py`. The method should store the lock and use it to serialize `_secure_atomic_write` calls in `save()`. Run `pytest tests/test_config_lock.py tests/regressions/concurrency_test.py -q --timeout=30 -o addopts=''` to verify.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-14] — VoiceTyperService.apply_config_side_effects does not delegate to config_applier (14 service-layer test failures)
**Status:** ❌ Not Fixed
**Description:** `tests/test_config_applier.py:118` fails: production `VoiceTyperService.apply_config_side_effects(updates)` does not invoke the module-level `apply_config_side_effects` (or `ConfigApplier.apply`) — extraction contract CR-18/Fix-D not honored. Compounds with `tests/test_history_and_models.py::TestSVC2ConfigSideEffectDispatcher` (4 failures: registry is empty / dispatcher does not invoke handlers) — config side-effect dispatcher not registered. Plus 9 more service-layer failures: SVC-6 keyring status, SVC-7 delete_model uses registry, SVC-8 refresh microphones force, SVC-10 onboarding uses service change_model, PERF21 download poll scoped to model dir, tray icon uses getchannel not split index, templates persist to disk (3).
**Root Cause:** The service-layer refactor (`voice_typer.server.service`) extracted config side-effects into `config_applier.py` but `VoiceTyperService.apply_config_side_effects` was not updated to delegate to it. The side-effect dispatcher registry was also not registered.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/config_applier.py`
- `tests/test_config_applier.py`
- `tests/test_history_and_models.py`
**Fix:** Restore delegation: `VoiceTyperService.apply_config_side_effects` should call `config_applier.apply_config_side_effects(updates, self)`. Register the side-effect dispatcher registry. Restore SVC-6/7/8/10 + PERF21 contracts. Run `pytest tests/test_config_applier.py tests/test_history_and_models.py tests/test_service*.py -q --timeout=30 -o addopts=''` to verify.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-16] — App lifecycle contracts broken (11 test failures in tests/app/)
**Status:** ❌ Not Fixed
**Description:** `tests/app/test_undo_repaste.py:34` (F-05): `undo_last` no longer batches backspaces with `time.sleep(0.01)` between chunks — APP-6 contract broken. `tests/app/test_quit_restart.py:465` (F-06, 2 failures): `quit_app` `if self._shutting_down:` guard is BEFORE `event_bus.publish`, not AFTER — APP-10 contract broken (drop-on-double-quit regression). `tests/app/test_lifecycle.py` (F-07, 6 failures): init-manager failure-warning logs at INFO not WARNING; excepthook install not wrapped in try/except. `tests/app/test_dictation.py::TestTryLoadModel` (F-08, 2 failures): model-load failure path no longer sets error state or pushes notify event.
**Root Cause:** Multiple app lifecycle contracts regressed during refactors — startup resilience weakened, quit ordering inverted, undo chunking removed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`
- `tests/app/test_undo_repaste.py`
- `tests/app/test_quit_restart.py`
- `tests/app/test_lifecycle.py`
- `tests/app/test_dictation.py`
**Fix:** Restore: (1) `undo_last` chunked backspaces with `time.sleep(0.01)` between each chunk of 10 (APP-6). (2) `quit_app` guard ordering: `event_bus.publish` BEFORE `if self._shutting_down: return` (APP-10). (3) Init managers (TemplateManager, VocabularyManager) log at WARNING on failure (APP-8). (4) `sys.excepthook` install wrapped in try/except (APP-9). (5) Model-load failure sets ERROR state + pushes notify event. Run `pytest tests/app/ -q --timeout=30 -o addopts=''` to verify.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-24] — build.yml::test push matrix forces architecture: x64 on ALL macOS entries — no arm64 coverage
**Status:** ❌ Not Fixed
**Description:** `build.yml:49` push matrix has `"architecture":["x64"]` for ALL entries including `macos-14`. The comment at lines 60-64 claims 'Apple Silicon-native tests are covered by the macos-14 default (arm64) runner' — but the push matrix FORCES `architecture: x64` on every macOS entry, so there are NO arm64-native macOS Python tests in CI. Apple Silicon users get ZERO arm64-native Python coverage. The 4 macOS legs all run x64 Python under Rosetta 2.
**Root Cause:** Matrix was copy-pasted with `architecture: x64` applied globally; the comment is misleading.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml:49,60-64`
**Fix:** Remove `"architecture":["x64"]` from the global matrix (let each OS use its native arch) OR add a second macOS-14 entry with `architecture: arm64` for native arm64 coverage. Update the misleading comment.
**Severity:** 🔴 High
**Category:** CI/CD

---

### [XS-26] — tauri-macos-build.yml missing all 3 caches (cargo, npm, uv) — re-fetches everything every run
**Status:** ❌ Not Fixed
**Description:** `tauri-macos-build.yml` (3 jobs: build-aarch64, build-x86_64, build-tauri-universal) has NO `actions/cache@v4` for `~/.cargo/registry`, `~/.cargo/git`, `src-tauri/target`. `setup-uv@v3` calls do NOT set `enable-cache: true`. `setup-node@v4` calls do NOT set `cache: npm`. Linux and Windows Tauri workflows all have these caches. When macOS workflow is enabled (Phase 0-M), every run will re-fetch + re-compile all Rust deps from scratch (~5-10 min wasted per arch leg, ~15-30 min wasted per `cargo tauri build`).
**Root Cause:** macOS workflow was written without copying the cache setup from Linux/Windows.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/tauri-macos-build.yml:67,70,138,141,211,214`
**Fix:** Add `actions/cache@v4` for cargo (mirror `tauri-linux-build.yml:108-117` pattern). Add `enable-cache: true` + `cache-dependency-glob: **/pyproject.toml` to all 3 `setup-uv@v3` calls. Add `cache: npm` + `cache-dependency-path: voice_typer/client/package-lock.json` to all 3 `setup-node@v4` calls.
**Severity:** 🔴 High
**Category:** CI/CD

---

### [XS-27] — tauri-windows-build.yml cancel-in-progress: true cancels release builds
**Status:** ❌ Not Fixed
**Description:** `tauri-windows-build.yml:67` has `cancel-in-progress: true` unconditionally, but the comment at lines 63-64 says 'Cancel in-flight runs of the same workflow on the same ref when a new push lands. Saves CI minutes; does not affect release builds.' The comment is WRONG — `cancel-in-progress: true` cancels ALL in-flight runs of the same group, including release (workflow_dispatch) builds. If a release engineer kicks off a Windows release build and then someone pushes to the same ref, the release build gets cancelled mid-signing. Linux uses `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` (correct).
**Root Cause:** cancel-in-progress was set to unconditional `true` instead of the PR-conditional pattern used by Linux.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/tauri-windows-build.yml:65-67`
**Fix:** Change to `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` (mirror `tauri-linux-build.yml:57`). Fix the misleading comment.
**Severity:** 🔴 High
**Category:** CI/CD

---

### [XS-28] — macOS/Windows Tauri CI workflows don't apply per-platform config overrides — will hard-fail
**Status:** ❌ Not Fixed
**Description:** `tauri.macos.conf.json` and `tauri.windows-x86_64.conf.json` exist precisely to override the base `tauri.conf.json`'s `bundle.resources` array (which lists a documented superset of all-platform resources). Linux CI correctly applies the override: `cargo tauri build --target "$TRIPLE" --config "tauri.linux-${BUILD_ARCH}.conf.json"`. But macOS and Windows do NOT: `cargo tauri build --target universal-apple-darwin` (no `--config`) and `cargo tauri build --target x86_64-pc-windows-msvc` (no `--config`). On a Windows runner, the base config lists `resources/native/macos-key-listener`, `resources/native/linux-key-listener`, `resources/prewarm-*-apple-darwin`, `resources/prewarm-*-unknown-linux-gnu`, `resources/prewarm-aarch64-pc-windows-msvc.exe` — none of which exist on a Windows host. `tauri-build` will hard-fail at resource-copy. Same on macOS. `build_tauri_all.sh` lines 203-206 has the same bug (only Linux gets `--config`).
**Root Cause:** Per-platform config override was only wired for Linux; macOS/Windows were assumed to not need it (incorrect — the base list is a superset).
**Progress:** None yet.
**Related Files:**
- `.github/workflows/tauri-macos-build.yml:253`
- `.github/workflows/tauri-windows-build.yml:263`
- `scripts/build/build_tauri_all.sh:203-206`
**Fix:** Add `--config tauri.macos.conf.json` to `tauri-macos-build.yml:253`. Add `--config tauri.windows-x86_64.conf.json` to `tauri-windows-build.yml:263`. Extend the bash conditional in `build_tauri_all.sh:203-206` to cover macOS + Windows.
**Severity:** 🔴 High
**Category:** Build pipeline

---

### [XS-29] — Three versions of `windows` crate in Cargo.lock (0.56, 0.57, 0.61) — compile bloat
**Status:** ❌ Not Fixed
**Description:** `src-tauri/Cargo.toml:85` pins `windows = "0.57"`. Cargo.lock shows THREE distinct `windows` crate versions: 0.56.0 (transitive), 0.57.0 (matches pin), 0.61.3 (Tauri 2.11.5 transitive). The Cargo.toml comment acknowledges this risk and authorizes the bump. Compiles 3 copies of a large crate, inflating build time + binary size.
**Root Cause:** Direct pin (0.57) is older than Tauri's transitive version (0.61).
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml:85`
- `src-tauri/Cargo.lock`
**Fix:** Bump `windows = "0.57"` → `windows = "0.61"` in `Cargo.toml:85` to deduplicate. Run `cargo update -p windows` to dedupe Cargo.lock. Cannot validate via `cargo check` in Linux sandbox (no Rust toolchain) — needs Windows host or Linux cross-compile target. Mark as VALIDATE ON WINDOWS HOST.
**Severity:** 🔴 High
**Category:** Dependency & supply-chain health

---

### [XS-33] — 8 JS vulnerabilities (3 HIGH) — all transitive via electron-builder + shadcn
**Status:** ❌ Not Fixed
**Description:** `npm audit --omit=dev` in `voice_typer/client/` reports 8 vulnerabilities: 3 HIGH (brace-expansion, fast-uri, js-yaml — all transitive via electron-builder chain), 4 MODERATE (@hono/node-server, @modelcontextprotocol/sdk, hono, shadcn — via shadcn→MCP chain), 1 LOW (body-parser). The 3 HIGH + 1 LOW can be fixed non-breakingly via `npm audit fix`. The 4 moderate shadcn-chain vulns cannot be fixed without downgrading shadcn from 4.13.0 → 3.8.3 (major).
**Root Cause:** Transitive dependency chains have known vulnerabilities; direct deps are clean.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package-lock.json`
**Fix:** Run `npm audit fix` in `voice_typer/client/` to clear the 3 HIGH + 1 LOW transitive vulns (non-breaking). Track the 4 moderate shadcn-chain vulns as a known upstream issue (cannot fix without shadcn major downgrade).
**Severity:** 🔴 High
**Category:** Dependency & supply-chain health

---

### [XS-34] — Pre-commit + husky conflict — both write .git/hooks/pre-commit
**Status:** ❌ Not Fixed
**Description:** `pre-commit install` writes `.git/hooks/pre-commit` that runs the pre-commit framework (ruff, mypy, biome, typecheck, sanity hooks). `npm install` in `voice_typer/client/` triggers the `prepare` script (`cd ../.. && husky`), which OVERWRITES `.git/hooks/pre-commit` to source `.husky/pre-commit` (ruff autofix + lint-staged biome). CONTRIBUTING.md tells contributors to run `pre-commit install` but does NOT mention that `npm install` will silently replace those hooks. The two hook systems have divergent behavior (ruff-format check vs write, biome check vs write, mypy run vs not run, sanity hooks run vs not run). Whichever install command runs LAST wins.
**Root Cause:** Two independent hook mechanisms (pre-commit framework + husky) both target `.git/hooks/pre-commit` with no coordination.
**Progress:** None yet.
**Related Files:**
- `.pre-commit-config.yaml`
- `.husky/pre-commit`
- `voice_typer/client/package.json:48 (prepare script)`
- `CONTRIBUTING.md`
**Fix:** Pick ONE mechanism. Recommendation: keep pre-commit framework (it has mypy, sanity hooks, and is what CI/CONTRIBUTING reference); remove husky's pre-commit and the `prepare` script's `husky` call, OR keep husky only for pre-push and remove the `.pre-commit-config.yaml` local hooks that duplicate husky (biome, typecheck).
**Severity:** 🔴 High
**Category:** CI/CD

---

### [XS-35] — Husky pre-push too slow (10-15 min) + mypy installs torch (~2GB) — will be universally --no-verify'd
**Status:** ❌ Not Fixed
**Description:** `.husky/pre-push` runs `cd voice_typer/client && npm run typecheck:ci` (tsc -b --force, cache-busting, 30s-2min) + full pytest suite minus 5 tauri dirs (10+ min for 331 files). Combined: 10-15 min. `.pre-commit-config.yaml:14-30` mypy hook has `additional_dependencies: [numpy, torch, transformers, pydantic, sounddevice, pystray]` — first `pre-commit run mypy` installs torch (~2GB download, 5-10 min). Not marked `stages: [pre-push]`. Developers will use `git push --no-verify` as the default, making the hooks theatre.
**Root Cause:** Pre-push scope is too broad; mypy is not optional and installs heavy deps.
**Progress:** None yet.
**Related Files:**
- `.husky/pre-push:5,27`
- `.pre-commit-config.yaml:14-30`
**Fix:** (1) Replace `typecheck:ci` with `typecheck` (uses tsbuildinfo cache, ~5s incremental). (2) Scope pytest to a fast unit subset: `pytest tests/ -x -q --timeout=10 -k 'not slow and not integration' -m 'not slow'`. (3) Move mypy to `stages: [pre-push]` OR make it a `local` hook with `language: system` so it reuses the project venv (no torch reinstall).
**Severity:** 🔴 High
**Category:** CI/CD

---

### [XS-36] — 27 broad except Exception: pass sites swallow real bugs
**Status:** ❌ Not Fixed
**Description:** Ruff `SIM105` rule reports 16 violations in `voice_typer/` + 15 in `tests/` = 31 total (ruff baseline tracks this). Multiline grep found 62 total `except: pass` occurrences in `voice_typer/`, of which 27 are broad `except Exception: pass` (the dangerous form that swallows bugs). Sites include: `crash_handler.py:538`, `tray_models.py:133`, `dictation_pipeline.py:467,578`, `ipc_server.py:933`, `platform_launch.py:93,107`, `recording/device_manager.py:424`, `recording/recorder.py:1164,1417,1456,1519` (4 sites), `hotkeys/wayland.py:401`, `hotkeys/native_adapter.py:195`, `hotkeys/win32_vk.py:280`, `clipboard/windows.py:83,113,132` (3 sites), `clipboard/manager.py:448,568,756` (3 sites), `prewarm/completion_events.py:111,121`, `prewarm/cache_probe.py:155,401`, `prewarm/paths.py:94,104`, `service.py:670,1611,2094` (3 sites), `clipboard_target_safety.py:386,409,748,761`, `streaming.py:655`, `crash_recovery.py:732`, `task_scheduler.py:160`, `startup_tasks.py:263`. Plus 3 broad `except Exception: continue`.
**Root Cause:** Silent error swallowing — prior cleanup converted some but 27 broad sites remain.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_handler.py`
- `voice_typer/server/tray_models.py`
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/platform_launch.py`
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/hotkeys/wayland.py`
- `voice_typer/server/hotkeys/native_adapter.py`
- `voice_typer/server/hotkeys/win32_vk.py`
- `voice_typer/server/clipboard/windows.py`
- `voice_typer/server/clipboard/manager.py`
- `voice_typer/server/prewarm/completion_events.py`
- `voice_typer/server/prewarm/cache_probe.py`
- `voice_typer/server/prewarm/paths.py`
- `voice_typer/server/service.py`
- `voice_typer/server/clipboard_target_safety.py`
- `voice_typer/server/streaming.py`
- `voice_typer/server/crash_recovery.py`
- `voice_typer/server/task_scheduler.py`
- `voice_typer/server/startup_tasks.py`
**Fix:** Convert each broad `except Exception: pass` to either `contextlib.suppress(SpecificException)` (if the swallow is intentional and the exception type is known) or `except SpecificException: log.debug('...', exc_info=True)` (if the error should be surfaced for debugging). After the batch, regenerate `ruff-baseline.json` to lock in the SIM105 reduction. NOTE: `clipboard/__init__.py:289-294` is INTENTIONAL (signal handler must never raise) — leave as-is.
**Severity:** 🔴 High
**Category:** Existing warnings and errors

---

### [XS-38] — Pre-existing vitest failure: window-open-logs.test.ts:104 — incomplete electron mock
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/__tests__/window-open-logs.test.ts:104` fails: `Error: [vitest] No "app" export is defined on the "electron" mock. Did you forget to return it from "vi.mock"?` The test's `vi.mock('electron', ...)` block (lines 24-28) only exports `dialog`, `ipcMain`, `shell`. There is no `app` export. The catch branch calls `logger.warn` → `appendLogLine(mainLogPath())` → `app.getPath('userData')` — but `app` is not mocked. Other tests (shutdown-hooks, bootstrap, start-python-early-exit, main-window-native-theme) all include `app: { getPath: ..., isPackaged: ... }` in their electron mock. Only this test omits it.
**Root Cause:** Incomplete electron mock — missing `app` export.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/__tests__/window-open-logs.test.ts:24-28`
**Fix:** Add `app: { getPath: vi.fn(() => '/tmp/vt-mock-userdata'), isPackaged: false }` to the `vi.mock('electron', ...)` block. Verify with `cd voice_typer/client && npx vitest run src/main/__tests__/window-open-logs.test.ts`.
**Severity:** 🔴 High
**Category:** Existing failing tests

---

### [XS-40] — Missing --strict-markers — marker typos silently disable opt-out mechanisms
**Status:** ❌ Not Fixed
**Description:** `pyproject.toml:399` addopts has no `--strict-markers`. Three custom markers are registered (`real_pynput`, `real_pil`, `slow`) via `config.addinivalue_line` in `tests/conftest.py:80-91`. Without `--strict-markers`, a typo like `@pytest.mark.realpynput` (missing underscore) silently passes — the marker is applied but does nothing, so the test runs with the autouse mock still active (for real_pynput) or without the slow-skip (for slow). The TEST-003 opt-out mechanism would silently fail to fire.
**Root Cause:** `--strict-markers` was never added to addopts.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml:399`
**Fix:** Add `--strict-markers` to addopts: `addopts = '-v --tb=short --strict-markers --cov=voice_typer --cov-fail-under=65'` (or after XS-9, just `-v --tb=short --strict-markers`). The 3 custom markers are already registered, so strict mode will recognize them.
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-41] — tests/tauri/conftest.py has redundant asyncio marker hook
**Status:** ❌ Not Fixed
**Description:** `tests/tauri/conftest.py:76-86` explicitly adds `pytest.mark.asyncio` to every async test via `pytest_collection_modifyitems`. But `pyproject.toml:405` sets `asyncio_mode = 'auto'` project-wide, which already auto-marks all async tests. The conftest's own docstring acknowledges this. The hook is dead code. The `co_flags & 0x100` (CO_COROUTINE) heuristic is fragile.
**Root Cause:** Hook was written before `asyncio_mode = 'auto'` was added to pyproject.toml and never removed.
**Progress:** None yet.
**Related Files:**
- `tests/tauri/conftest.py:76-86`
**Fix:** Remove the `pytest_collection_modifyitems` hook entirely (keep only the platform-skip logic). Update the module docstring.
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-42] — Cross-test helper duplication — 26 test files copy-paste factory functions
**Status:** ❌ Not Fixed
**Description:** Six categories of copy-pasted factory functions across 26 test files: `_make_ipc_server` (4 copies), `_make_fake_server` (6 copies, 5 byte-for-byte identical), `_make_recorder` (5 copies with subtle drift), `_make_app` (3 copies, first two byte-for-byte identical to `tests/app/conftest.py::app`), `_make_sine`/`make_sine` (3 copies), `_make_cm`+`_make_snapshot` (2 each), `_make_model_cache_dir` (2), `temp_config`/`tmp_config_dir` (3). When `VoiceTyperApp.__init__` changes, 3+ test files need updating. When `IPCServer.__init__` changes, 4 test files using `__new__(IPCServer)` bypass may silently break.
**Root Cause:** Test helpers were copy-pasted instead of imported from `tests/fixtures/ipc_test_helpers.py` (which exists for this purpose).
**Progress:** None yet.
**Related Files:**
- `tests/fixtures/ipc_test_helpers.py`
- `tests/test_notification_event_name.py`
- `tests/tauri/mig15/test_toast_windows.py`
- `tests/tauri/mig16/test_toast_macos.py`
- `tests/tauri/mig17/test_toast_linux.py`
- `tests/test_ipc5_error_envelope_parity.py`
- `tests/test_sidecar_ws_thread_safety.py`
- `tests/tauri/test_sidecar_ws_unit.py`
- `tests/tauri/mig15/test_ws_hmac_windows.py`
- `tests/tauri/mig16/test_ws_hmac_macos.py`
- `tests/tauri/mig17/test_ws_hmac_linux.py`
- `tests/test_concurrent_resample_safety.py`
- `tests/regressions/concurrency_rms_test.py`
- `tests/test_recorder_device_cache_prewarm.py`
- `tests/test_secure_clear_array.py`
- `tests/test_recording_discard.py`
- `tests/test_api_doc_accuracy.py`
- `tests/test_b4_config_editor_lock.py`
- `tests/test_dictation_pipeline_review_fixes.py`
- `tests/test_audio_processor.py`
- `tests/test_recorder_double_resample.py`
- `tests/test_recording_audio_processor.py`
- `tests/test_clipboard_paste_restore.py`
- `tests/test_clipboard_borrow_restore.py`
- `tests/test_import_model_security.py`
- `tests/test_model_import.py`
- `tests/test_e2e_smoke.py`
**Fix:** Promote `tests/fixtures/ipc_test_helpers.py` to also export `make_fake_sidecar_ws_server()` and `make_fake_recorder()` factories. Create `tests/fixtures/app_helpers.py` with `make_voice_typer_app()` and `make_sine()`. Migrate the 26 duplicated test files to import from these. Resolve the `_make_ipc_server` × 4 drift (either delete them and use `make_ipc_server_with_fakes()` or update `make_fake_app()` to re-add `_config_mutation_lock`).
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-43] — Unused/dead WAV fixtures (silence.wav, tone.wav, noise.wav) — 96KB dead test data
**Status:** ❌ Not Fixed
**Description:** `rg -n 'silence\.wav|tone\.wav|noise\.wav' tests/` returns ZERO matches in test code. Only `test_440hz_1s_16k.wav` is used (via `tests/conftest.py:132 wav_fixture_path`). The other three are documented in `metadata.json` and `CONTRIBUTING.md:600-601` but never loaded. Additionally, `tone.wav` and `test_440hz_1s_16k.wav` have identical sha256 (`9ca8ebc6c1c04348f8670430ef995a84ebb017f0519a4102448a0437082144c6`) — duplicate content. `generate_fixture.py` only generates `test_440hz_1s_16k.wav`; the other three have NO generator script (non-reproducible).
**Root Cause:** Fixtures were committed aspirationally for VAD/silence/recording tests that were never written; `tone.wav` is a duplicate of `test_440hz_1s_16k.wav`.
**Progress:** None yet.
**Related Files:**
- `tests/fixtures/silence.wav`
- `tests/fixtures/tone.wav`
- `tests/fixtures/noise.wav`
- `tests/fixtures/generate_fixture.py`
- `tests/fixtures/metadata.json`
**Fix:** Either (a) delete the 3 unused fixtures + their metadata.json entries + the CONTRIBUTING.md mention, OR (b) extend `generate_fixture.py` to produce all 4 deterministically and wire the unused ones into actual tests. Option (a) is simpler.
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-45] — torch not in autouse mock_heavy_imports fixture — 6 local mock setups + 17s import tax
**Status:** ❌ Not Fixed
**Description:** `tests/conftest.py:177-322 mock_heavy_imports` mocks sounddevice, faster_whisper, pynput, pystray, PIL, pyperclip. It does NOT mock `torch` — but `torch` is lazily imported in 6 production modules (transcription.py:102,1258; dictation_pipeline.py:559; vad.py:48,92,149; crash_recovery.py:700; parakeet_engine.py:251; noise_suppressor.py:90). Per `pyrefly-baseline.json`, 'torch' alone accounts for 9 of 26 missing-import errors. Each test touching these paths re-implements the same mock boilerplate with drift (some mock `torch.backends.mps`, some don't). The 17s import cost forces per-test mocking just to avoid timeouts.
**Root Cause:** Heavy optional dep not added to the autouse mock fixture when torch was introduced.
**Progress:** None yet.
**Related Files:**
- `tests/conftest.py:177-322`
**Fix:** Add `torch` (and optionally `transformers`) to the autouse `mock_heavy_imports` fixture, with a `real_torch` marker (mirroring the existing `real_pynput`/`real_pil` pattern) for tests that genuinely need real `torch.backends.mps` semantics. Eliminates ~6 local mock setups and the 17s import tax.
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-48] — Stale ruff_ratchet_check.py docs say 'voice_typer/server/' but CI uses full scope
**Status:** ❌ Not Fixed
**Description:** `scripts/ruff_ratchet_check.py:21, 29, 38, 111, 191, 204, 302` — every usage example says `ruff check voice_typer/server/`, but the actual CI invocation (build.yml:120) is `python -m ruff check voice_typer/ tests/ scripts/ conftest.py`. The `ruff-baseline.json` `_target` field was correctly updated to `voice_typer/ tests/ scripts/ conftest.py`. A contributor following the script's `--regenerate` instructions would run the wrong scope, get a lower count, and either regenerate the baseline with a too-low count (locking in regressions) or be confused.
**Root Cause:** Script docs were not updated when the ratchet scope was expanded.
**Progress:** None yet.
**Related Files:**
- `scripts/ruff_ratchet_check.py:21,29,38,111,191,204,302`
**Fix:** Update all 7 occurrences of `voice_typer/server/` to `voice_typer/ tests/ scripts/ conftest.py`.
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-49] — 5 stale PORT-CANDIDATE / DELETE-CANDIDATE skipped tests in tests/regressions/
**Status:** ❌ Not Fixed
**Description:** `tests/regressions/electron_test.py:69` (skip reason: 'RW-8: PORT-CANDIDATE — ported to tests/test_bugfix_regressions_behavioral.py::TestElectronLogFilesBehavioral::test_all_electron_launch_sites_call_log_files_helper'), `electron_test.py:110` ('RW-8: DELETE-CANDIDATE — redundant with TestElectronNotificationFieldValidation'), `tray_test.py:35` ('RW-8: PORT-CANDIDATE — ported to ...TestTrayIconBaseIcoBehavioral'), `ipc_test.py:37` ('RW-8: PORT-CANDIDATE — ported to ...TestAccessibilityIpcBehavioral'), `ipc_test.py:125` ('RW-8: PORT-CANDIDATE — ported to ...TestTcpLineIoOversizedBehavioral'). The ports are confirmed complete — these tests should be DELETED, not skipped.
**Root Cause:** Skipped tests were left in place after porting instead of being deleted.
**Progress:** None yet.
**Related Files:**
- `tests/regressions/electron_test.py`
- `tests/regressions/tray_test.py`
- `tests/regressions/ipc_test.py`
**Fix:** Delete the 5 skipped test functions. Verify the target tests exist (per the skip reasons) before deleting.
**Severity:** 🟡 Medium
**Category:** Existing failing tests

---

### [XS-51] — 2 xfail in test_shutdown_controller.py reference stale 'wiring pending' TODOs
**Status:** ❌ Not Fixed
**Description:** `tests/test_shutdown_controller.py:148` — `reason='wiring pending — primary agent will add self.shutdown = ShutdownController(self) to VoiceTyperApp.__init__'`. `:167` — `reason='wiring pending — primary agent will add the delegate stubs'`. These read like TODOs from a prior session. If the wiring was added, they're stale (would xpass-strict-fail). If not, they should be converted to `pytest.mark.skip`.
**Root Cause:** TODO-style xfail markers never resolved.
**Progress:** None yet.
**Related Files:**
- `tests/test_shutdown_controller.py:148,167`
**Fix:** Check if the 'wiring pending' work was completed. If so, remove the markers (they'll xpass-strict-fail and surface the issue). If not, convert to `pytest.mark.skip` with a tracking issue link.
**Severity:** 🟡 Medium
**Category:** Existing failing tests

---

### [XS-53] — Race-prone time.sleep() synchronization in tests (30+ sites)
**Status:** ❌ Not Fixed
**Description:** 30+ tests use fixed `time.sleep()` for thread synchronization instead of Event-based waits. Examples: `tests/test_keyboard_ownership_watchdog.py:191` (`time.sleep(0.15)` 'give handler a moment to process auth'), `tests/test_ipc_deadlock_regression.py:223` (`time.sleep(0.1)` 'give reader a moment to enter blocking readline'), `tests/test_sidecar_ws_thread_safety.py:235,340,444` (`asyncio.sleep(0.15)` 'wait for connection to authenticate'), `tests/test_microphone_watcher.py` (15+ occurrences: 0.15-0.4s sleeps), `tests/test_hotkeys_win32.py` (18+ occurrences: 0.03-0.2s), `tests/test_lock_order_contract.py:370,480` (`time.sleep(1.0)` to 'let 9 threads race'), `tests/test_smart_duck_monitor.py:546` (`time.sleep(2.0)` for 8-thread race), `tests/test_b4_config_editor_lock.py:412` (`time.sleep(0.15)`), `tests/test_timer_coordinator.py:167,204,228,269,310,372`. These are race-prone on slow CI.
**Root Cause:** Fixed sleeps used for synchronization instead of Event-based waits or polling with deadline.
**Progress:** None yet.
**Related Files:**
- `tests/test_keyboard_ownership_watchdog.py:191`
- `tests/test_ipc_deadlock_regression.py:223`
- `tests/test_sidecar_ws_thread_safety.py:235,340,444`
- `tests/test_microphone_watcher.py`
- `tests/test_hotkeys_win32.py`
- `tests/test_lock_order_contract.py:370,480`
- `tests/test_smart_duck_monitor.py:546`
- `tests/test_b4_config_editor_lock.py:412`
- `tests/test_timer_coordinator.py:167,204,228,269,310,372`
**Fix:** Replace fixed `time.sleep()` synchronization with Event-based waits: instead of `time.sleep(0.15); assert X`, use `assert event.wait(timeout=2.0)` or poll with `deadline = time.monotonic() + N`. Prioritize the highest-risk sites (test_keyboard_ownership_watchdog, test_ipc_deadlock_regression, test_sidecar_ws_thread_safety).
**Severity:** 🟡 Medium
**Category:** Existing failing tests

---

### [XS-54] — build.yml has no top-level permissions: block — 7 of 11 jobs inherit repo-default
**Status:** ❌ Not Fixed
**Description:** `build.yml` has NO top-level `permissions:` block. Only `build-windows`, `build-macos`, `build-linux`, and `pip-audit-weekly` jobs declare per-job permissions. Jobs `test`, `version-check`, `branding-check`, `client-build`, `build-native`, `build-macos-universal`, `slow-tests` inherit the repo-default `GITHUB_TOKEN` permissions (typically permissive `contents: write`).
**Root Cause:** Top-level permissions never added.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml`
**Fix:** Add `permissions: { contents: read, pull-requests: read }` as a top-level default; widen per-job only where needed (release jobs need `contents: write`).
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-55] — No environment: release gating on signing jobs — secrets exposed to tag-push runs
**Status:** ❌ Not Fixed
**Description:** `build-windows`, `build-macos`, `build-linux` (when triggered by tag push) have no `environment: release` block. Signing secrets (`WIN_CSC_LINK`, `MAC_SIGNING_IDENTITY`, `APPLE_*`) are exposed to any contributor whose PR triggers the workflow on a tag.
**Root Cause:** No deployment environment gating.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml`
**Fix:** Add `environment: release` (with required reviewers) to the 3 release jobs when triggered by tag push.
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-56] — pip-audit has continue-on-error: true — vulnerabilities NEVER fail the build
**Status:** ❌ Not Fixed
**Description:** `build.yml` pip-audit step has `continue-on-error: true` with a `||` warning fallback → audit findings NEVER fail the build. Security theatre.
**Root Cause:** Intentional warn-only policy but defeats the purpose of the gate.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml`
**Fix:** Either tighten pip-audit to fail-on-finding (remove `continue-on-error: true`) OR document the warn-only policy explicitly with a tracking issue for fixing findings.
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-57] — slow-tests job has continue-on-error: true — failures invisible in CI status
**Status:** ❌ Not Fixed
**Description:** `build.yml` `slow-tests` job has job-level `continue-on-error: true` → failures invisible in CI check status. Regressions in `tests/manual/` scripts only get noticed when someone happens to read the main-branch run logs.
**Root Cause:** Slow tests made non-gating to avoid blocking PRs, but no alternative notification exists.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml`
**Fix:** Either remove `continue-on-error: true` (make slow-tests gating on main) OR open a GitHub issue on failure (like `pip-audit-weekly` does) so regressions surface.
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-60] — Dev container VS Code settings use Prettier/ESLint but project uses Biome
**Status:** ❌ Not Fixed
**Description:** `.devcontainer/devcontainer.json:42-53` configures `[typescript]`/`[typescriptreact]` `editor.defaultFormatter: esbenp.prettier-vscode` and `editor.codeActionsOnSave: source.fixAll.eslint: explicit`. But `package.json` has NO `prettier` or `eslint` dependency — `@biomejs/biome: ^2.5.3` is the formatter/linter. VS Code format-on-save introduces Prettier formatting that Biome will reject on commit.
**Root Cause:** Dev container config predates the Biome migration.
**Progress:** None yet.
**Related Files:**
- `.devcontainer/devcontainer.json:42-53`
**Fix:** Replace `esbenp.prettier-vscode` with `biomejs.biome`. Remove `dbaeumer.vscode-eslint` extension and the `source.fixAll.eslint` code action. Add `biomejs.biome` to the extensions list.
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-61] — Dev container postCreateCommand does not install pre-commit hooks
**Status:** ❌ Not Fixed
**Description:** `.devcontainer/devcontainer.json:23` `postCreateCommand` is `pip install --user -e '.[test,dev]' && (cd voice_typer/client && npm ci)`. `pip install -e '.[dev]'` installs the `pre-commit` package but does NOT run `pre-commit install`. Dev-container contributors have husky hooks (if npm ci ran prepare) but not pre-commit framework hooks. Inconsistent with CONTRIBUTING.md.
**Root Cause:** postCreateCommand missing `pre-commit install`.
**Progress:** None yet.
**Related Files:**
- `.devcontainer/devcontainer.json:23`
**Fix:** Append `&& pre-commit install && pre-commit install --hook-type pre-push` to `postCreateCommand`. (Coordinate with XS-34 — if husky is removed, only install pre-commit.)
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-62] — lint-staged has redundant *.py entry + husky bypasses it
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/.lintstagedrc.json:8` has `"*.py": ["ruff check --fix", "ruff format"]`. But `.husky/pre-commit:26-37` explicitly runs `ruff check --fix` and `ruff format` on staged Python files DIRECTLY, with a comment explaining it bypasses lint-staged. When lint-staged DOES match a staged `.py` file, ruff runs a SECOND time. Wasted work + confusing config.
**Root Cause:** Two places defining Python lint behavior.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/.lintstagedrc.json:8`
**Fix:** Remove the `*.py` line from `.lintstagedrc.json` (husky handles it directly). Also add `*.mjs` to lint-staged (currently missing — `scripts/generate-icons.mjs` bypasses lint-staged).
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-63] — tsconfig strict flags missing (noUnusedLocals, noUnusedParameters, noImplicitReturns, noFallthroughCasesInSwitch)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/tsconfig.web.json` and `tsconfig.node.json` have `strict: true` but do NOT enable `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch`. When enabled via CLI, tsc surfaces 3 dead-code errors: `TestReviewPanel.tsx:68` (`_QualityScore` declared, never read), `Vocabulary.tsx:529` (`_filtered` declared, never read), `branding.ts:34` (`_APP_DESCRIPTION` declared, never read).
**Root Cause:** Strict flags never enabled.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/tsconfig.web.json`
- `voice_typer/client/tsconfig.node.json`
- `voice_typer/client/src/renderer/src/components/microphone/TestReviewPanel.tsx:68`
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:529`
- `voice_typer/client/src/main/branding.ts:34`
**Fix:** Enable `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch` in both tsconfigs. Delete the 3 dead variables (`_QualityScore`, `_filtered`, `_APP_DESCRIPTION`). Defer `exactOptionalPropertyTypes` (separate task — 18+ errors to fix).
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors

---

### [XS-64] — biome.json deprecated recommended: true + global noConsole: off + 2 stale biome-ignores
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/biome.json:19` uses `"recommended": true` (deprecated in biome 2.5.3 — should be `"preset": "recommended"`). Lines 43-53 globally disable `suspicious/noConsole` without scoping (the override has no `includes` field) — allows direct `console.log`/`console.error` in 20+ production files bypassing the `src/main/logging.ts` abstraction. 2 stale `biome-ignore lint/suspicious/noArrayIndexKey` comments in `SettingsSkeleton.tsx:27` and `slider.tsx:49` (rule isn't firing; will surface when biome is upgraded).
**Root Cause:** Biome config predates v2.5.3 + noConsole override too broad.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/biome.json:19,43-53`
- `voice_typer/client/src/renderer/src/components/settings/SettingsSkeleton.tsx:27`
- `voice_typer/client/src/renderer/src/components/ui/slider.tsx:49`
**Fix:** Replace `"recommended": true` with `"preset": "recommended"`. Scope the `noConsole: off` override to `scripts/**` and specific main-process files (e.g. `src/main/index.ts` bootstrap). Remove the 2 stale `biome-ignore` comments.
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors

---

### [XS-65] — Production console.info calls in useStatsShare.ts + dead debug-test.test.tsx
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts:133,191,206` has 3 `console.info('[StatsShare] ...')` calls in production code (tagged EXPORT-FIX in comments — debug-during-fix artifacts never cleaned up). `voice_typer/client/src/renderer/src/pages/__tests__/debug-test.test.tsx` is a debug-only test with 4 `console.log` calls and `expect(true).toBe(true)` — asserts nothing.
**Root Cause:** Debug artifacts shipped to production.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts:133,191,206`
- `voice_typer/client/src/renderer/src/pages/__tests__/debug-test.test.tsx`
**Fix:** Wrap the 3 `console.info` calls in `if (import.meta.env.DEV)` or remove them. Delete or rewrite `debug-test.test.tsx` to assert actual behavior.
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors

---

### [XS-66] — 6 dead exports/code in TS client (TestReviewPanel, Vocabulary, branding, logging, useSettingsConfig)
**Status:** ❌ Not Fixed
**Description:** 6 confirmed dead exports/code: (1) `_QualityScore` in `TestReviewPanel.tsx:68`; (2) `_filtered` in `Vocabulary.tsx:529`; (3) `_APP_DESCRIPTION` in `branding.ts:34`; (4) `_setRuntimeLogPathForTest` in `logging.ts:352` (exported test-only helper, only referenced in JSDoc comments, no test imports it); (5) `__resetCachedConfigForTests` in `useSettingsConfig.ts:37` (tests now use `vi.resetModules()`); (6) `APP_DESCRIPTION` in `renderer/src/branding.ts:29` (exported, never imported anywhere — only `APP_NAME` is imported).
**Root Cause:** Dead code accumulated over refactors.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/microphone/TestReviewPanel.tsx:68`
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:529`
- `voice_typer/client/src/main/branding.ts:34`
- `voice_typer/client/src/main/logging.ts:352`
- `voice_typer/client/src/renderer/src/components/settings/useSettingsConfig.ts:37`
- `voice_typer/client/src/renderer/src/branding.ts:29`
**Fix:** Delete the 6 dead exports/code. Verify with `rg -l '<name>' voice_typer/client/src/` before each deletion.
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors

---

### [XS-68] — typecheck:root is a no-op against solution-style tsconfig
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/tsconfig.json` is a solution-style config (`files: []` + only `references`). Running `tsc --noEmit` against it does NOT type-check the referenced projects (only `tsc -b` does). `npm run typecheck:root` (`tsc --noEmit`) is a silent no-op. The first command of `npm run typecheck` (`tsc --noEmit && tsc -p tsconfig.web.json --noEmit && tsc -p tsconfig.node.json --noEmit`) is also a no-op — the real checks happen in the subsequent web/node calls.
**Root Cause:** tsconfig.json is a project-refs container, not a checkable config.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json:39 (typecheck:root, typecheck scripts)`
**Fix:** Either remove `typecheck:root`, or change it to `tsc -b --noEmit` (build mode skips emit but type-checks refs). Remove the redundant first `tsc --noEmit` from `typecheck`.
**Severity:** 🟡 Medium
**Category:** Build pipeline

---

### [XS-69] — Vitest coverage thresholds configured but NOT enforced — @vitest/coverage-v8 not installed, --coverage never passed
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/vitest.config.ts:43-72` declares coverage (provider: v8, thresholds lines/functions/statements 70%, branches 60%). But `package.json` `test` script is `vitest run` (no `--coverage`). `@vitest/coverage-v8` is NOT in devDependencies (it's an optional peer dep of vitest, not auto-installed). `npm test -- --coverage` aborts with `MISSING DEPENDENCY Cannot find dependency '@vitest/coverage-v8'`. CI (`client-ci.yml:57`) runs `npm test` with no `--coverage` flag. Thresholds are dead config — coverage can silently regress without any signal.
**Root Cause:** Coverage configured but never invoked; provider not installed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json (devDependencies, scripts)`
- `voice_typer/client/vitest.config.ts`
- `.github/workflows/client-ci.yml:57`
**Fix:** Add `@vitest/coverage-v8` to devDependencies. Add `test:coverage` script (`vitest run --coverage`). Wire `--coverage` into the CI `npm test` step. Add `all: true` to the coverage block (so untested files are included in the denominator). Add `csp-plugin.ts` to coverage include (currently excluded).
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-72] — No CODEOWNERS file — no auto-assignment of reviewers
**Status:** ❌ Not Fixed
**Description:** No `.github/CODEOWNERS` file exists. No reviewers are auto-assigned on PRs. The `PULL_REQUEST_TEMPLATE.md` exists (with a self-review checklist) but template ≠ auto-assignment. A bad workflow edit can ship without review.
**Root Cause:** CODEOWNERS never created.
**Progress:** None yet.
**Related Files:**
- `.github/CODEOWNERS (new file)`
**Fix:** Create `.github/CODEOWNERS` with maintainer team for `/.github/`, `/src-tauri/`, `/voice_typer/client/`, `/scripts/build/`, `/pyproject.toml`.
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-74] — 9 of 22 direct Python deps have no inline version-policy comment
**Status:** ❌ Not Fixed
**Description:** 13/22 direct deps in `pyproject.toml [project.dependencies]` have an inline `# CR-x`/`# BUILD-Nx`/`# SEC-DEP-x`/`# ADR` comment justifying the pin. The remaining 9 (faster-whisper, sounddevice, scipy, pyperclip, pynput, pycaw, comtypes, pyobjc-core, pyobjc-framework-CoreAudio) have NO version-policy comment — pin rationale is undocumented. Specifically `pynput<2.0` has no documented rationale (pynput 2.x exists).
**Root Cause:** Pin rationale never documented for these deps.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml:64-135`
**Fix:** Add `# CR-x`/`# DEP-x` comments documenting why each pin exists. For `pynput<2.0`, investigate what pynput 2.x breaks and document it.
**Severity:** 🟡 Medium
**Category:** Dependency & supply-chain health

---

### [XS-75] — 8 platform-only Python deps have no upper bound — future breaking changes can flow in
**Status:** ❌ Not Fixed
**Description:** 8 platform-only deps in `pyproject.toml` have NO upper bound: `pycaw>=20230407`, `comtypes>=1.1`, `pyobjc-core>=9.0`, `pyobjc-framework-CoreAudio>=9.0`, `pyobjc-framework-Cocoa>=9.0`, `pyobjc-framework-CoreFoundation>=9.0`, `pyobjc-framework-ApplicationServices>=9.0`, `pyrnnoise>=0.4`. For platform-conditional deps where upstream breaking changes are common (pyobjc 11.x exists, pyobjc 10.x exists), an upper bound is safer.
**Root Cause:** Platform deps left floating.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml:130-168`
**Fix:** Add `<X.0` upper bounds to the 8 platform-only deps. Document rationale for each.
**Severity:** 🟡 Medium
**Category:** Dependency & supply-chain health

---

### [XS-76] — pystray (LGPL, unmaintained 3+ years) + pycaw (unmaintained 3+ years) — supply-chain risk
**Status:** ❌ Not Fixed
**Description:** `pystray` 0.19.5 (Dec 2022, >3 years old, LGPL-3.0-or-later) and `pycaw` 20230407 (Apr 2023, >3 years old, MIT) are Windows-critical (tray + volume ducking). No upstream activity for >3 years. Additionally, LGPL deps (`pystray`, `pynput`) statically bundled into Nuitka/PyInstaller frozen binary — LGPL §4d re-linking obligation may not be satisfied (project doesn't ship `.py` source + relink instructions).
**Root Cause:** Unmaintained critical deps + LGPL static-bundling compliance gap.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml:82 (pystray), :130 (pycaw)`
**Fix:** Document supply-chain risk in a CR. Consider upstream fork or alternative library. For LGPL compliance, ship `.py` source for LGPL'd modules + relink instructions in installer. (Legal review required — out of code scope.)
**Severity:** 🟡 Medium
**Category:** Dependency & supply-chain health

---

### [XS-77] — Pre-existing TS renderer test failures (52 tests) — multiple root causes
**Status:** ❌ Not Fixed
**Description:** Vitest full suite: 23 test files failed / 56 passed (79), 53 tests failed / 719 passed (784). The 52 non-owned failures (XS-3B owned only 1) live under `src/renderer/src/**` and `src/preload/**`. Sample failures: `DownloadProgressBar.test.tsx` (aria-valuenow expected 42/43, got 40), `LiveQualityFeedback.test.tsx` (i18n sentinel mismatch), `Sidebar.test.tsx` (Tailwind class `border-l-(--accent)` vs `border-l-accent`), `tauri-bridge-detection.test.ts` (Tauri mock), `App-ux-fixes.test.tsx` (`data-testid='home-page'` not found).
**Root Cause:** Multiple root causes — Tailwind 4 utility-class regressions, stale test fixtures, i18n sentinel drift.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/models/__tests__/DownloadProgressBar.test.tsx`
- `voice_typer/client/src/renderer/src/components/feedback/__tests__/LiveQualityFeedback.test.tsx`
- `voice_typer/client/src/renderer/src/components/layout/__tests__/Sidebar.test.tsx`
- `voice_typer/client/src/renderer/src/lib/__tests__/tauri-bridge-detection.test.ts`
- `voice_typer/client/src/renderer/src/__tests__/App-ux-fixes.test.tsx`
**Fix:** Investigate each failure cluster: (1) DownloadProgressBar — check if aria-valuenow calculation regressed; (2) LiveQualityFeedback — update i18n sentinel; (3) Sidebar — update Tailwind class assertion to match Tailwind 4 syntax; (4) tauri-bridge-detection — fix Tauri mock; (5) App-ux-fixes — update data-testid. Run `cd voice_typer/client && npx vitest run` to verify.
**Severity:** 🟡 Medium
**Category:** Existing failing tests

---

### [XS-78] — Critical TS main-process modules untested (handle-message, send-to-python, python-call-handler, single_instance, bubble-handlers)
**Status:** ❌ Not Fixed
**Description:** 5 critical Electron main-process modules have 0 behavioral tests: (1) `python/handle-message.ts` — routes 8+ push event types (bubble_show/hide/set-state/level/config, show_window, quit_app, relaunch_electron) — 0 tests. (2) `python/send-to-python.ts` — `ALLOWED_COMMANDS` enforcement (SEC-019), 120s timeout, `state._relaunching` early reject — 0 tests. (3) `ipc/python-call-handler.ts` — structured `{_error, _code}` envelope (4 codes) — 0 tests. (4) `single_instance.ts` — stale-PID recovery + `VT_FOCUS_ONLY=1` exit path — 0 behavioral tests (only `computeConfigDir` is referenced, as a mock). (5) `ipc/bubble-handlers.ts` — `assertFromBubble()` SEC-016 frame check, `bubble:move-by` screen clamp, `bubble:resize` MIN/MAX clamp — 0 tests. Preload has 0 test files (explicitly excluded from coverage).
**Root Cause:** Critical IPC/lifecycle modules never had tests written.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/__tests__/ (new test files)`
- `voice_typer/client/src/preload/__tests__/ (new directory)`
**Fix:** Create: `handle-message.test.ts`, `send-to-python.test.ts`, `python-call-handler.test.ts`, `single-instance.test.ts`, `bubble-handlers.test.ts`, `preload/ipc-contract.test.ts` (shared channel-name table). Refactor `index.ts` to extract `registerLifecycleHandlers(app)` so lifecycle tests can import it (currently `shutdown-hooks.test.ts` uses fragile source-text regex).
**Severity:** 🟡 Medium
**Category:** Test coverage gaps

---

### [XS-79] — privacy_handlers.py (217 LOC) has zero direct tests
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/handlers/privacy_handlers.py` — two handler methods `_handle_delete_all_personal_data` (line 74) and `_handle_export_gdpr_bundle` (line 149) are NOT exercised by any test file. `tests/test_gdpr_export.py` and `tests/test_gdpr_delete.py` test the SERVICE methods but NOT the IPC handler envelopes. Specifically untested: the `PermissionError`/`OSError` → `code: 'server.file_locked'` envelope mapping, the `_validate_dict_payload` non-dict rejection path, the `{'type': <cmd>_result, 'data': <result>}` envelope shape.
**Root Cause:** Handler tests never written for privacy_handlers.
**Progress:** None yet.
**Related Files:**
- `tests/handlers/test_privacy_handlers.py (new file)`
**Fix:** Create `tests/handlers/test_privacy_handlers.py`: test both handler methods — happy path (service returns success dict), PermissionError → `server.file_locked` envelope, non-dict payload → `invalid_payload`, missing service method → `server.internal_error`. ~150 LOC.
**Severity:** 🟡 Medium
**Category:** Test coverage gaps

---

### [XS-80] — WinVolumeBackend ducking logic untested (smoke only) + MacVolumeBackend CoreAudio path untested
**Status:** ❌ Not Fixed
**Description:** `tests/test_volume_backends.py::TestWinBackendSmoke` (5 tests) only verifies the 'pycaw not installed → graceful failure' path. The actual ducking logic — `is_speaker_active()` (peak threshold 0.01), `get_other_sessions()` (PROC-FILTER-FIX regex + PID backstop), `duck_other_sessions()`, `restore_other_sessions()` — has ZERO behavioral coverage. The source-comment `PROC-FILTER-FIX` documents that this exact code broke before. `tests/test_volume_backends.py::TestMacBackendOsascript` (7 tests) explicitly patches `CoreAudio` to `None` to force the osascript fallback. The 12 CoreAudio methods are completely untested. `voice_typer/stubs/CoreAudio.pyi` exists — Linux sandbox CAN mock the `CoreAudio` module.
**Root Cause:** Platform-specific volume backends never had behavioral tests written.
**Progress:** None yet.
**Related Files:**
- `tests/test_volume_backends.py`
**Fix:** Add `TestWinBackendPycaw` (mock `pycaw.pycaw.AudioUtilities`, `IAudioEndpointVolume`, `IAudioMeterInformation`; test `initialize()` happy path, `get_state()`/`set_linear()`, `is_speaker_active()` peak threshold, `get_other_sessions()` PROC-FILTER-FIX regex, `duck_other_sessions()`/`restore_other_sessions()` round-trip). Add `TestMacBackendCoreAudio` (mock `CoreAudio` module; test the 12 CoreAudio methods). ~450 LOC total.
**Severity:** 🟡 Medium
**Category:** Test coverage gaps

---

### [XS-81] — hotkeys/capture.py::capture_custom_hotkey is DEAD CODE (127 LOC)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/hotkeys/capture.py:29` defines `capture_custom_hotkey` (127 LOC). Confirmed via `rg 'capture_custom_hotkey'` — the function is referenced only in its own docstring example at line 53. It is NOT imported by `hotkeys/__init__.py`, NOT called by any production code, NOT tested. 127 LOC of untested Win32 `GetAsyncKeyState` polling logic that will silently rot.
**Root Cause:** Dead code from an abandoned feature.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkeys/capture.py`
**Fix:** Delete `voice_typer/server/hotkeys/capture.py` (127 LOC dead code). `parse_hotkey_to_win32` (tested in `test_hotkey_spec_parity.py`) covers the static-VK-map path; the dynamic-capture UI flow is handled in the renderer.
**Severity:** 🟡 Medium
**Category:** Test coverage gaps

---

### [XS-82] — Dead code: ModelManager + RecordingController have no dedicated test files (1131 + 919 LOC)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/model_manager.py` (1131 LOC) has NO dedicated test file. 13+ test sites use `ModelManager.__new__(ModelManager)` to bypass `__init__` — the constructor wiring is never exercised. Untested paths: `_load_transcription_engine_background`, `_fallback_to_whisper`, `_init_qwen_engine`/`_init_parakeet_engine`, `_change_model` execution, LRU eviction trigger. `voice_typer/server/recording_controller.py` (919 LOC) has NO dedicated test file. 14+ test sites use `RecordingController.__new__` to bypass `__init__`. Untested paths: `_on_silence_warning`, `_on_silence_auto_stop`, `_on_max_duration_auto_stop`, `toggle_dictation` RACE-025 serialization lock (no concurrency test).
**Root Cause:** Large critical modules never had dedicated test files; all tests bypass `__init__`.
**Progress:** None yet.
**Related Files:**
- `tests/test_model_manager.py (new file)`
- `tests/test_recording_controller.py (new file)`
**Fix:** Create `tests/test_model_manager.py` (test `__init__` wiring, `_load_transcription_engine_background`, `_fallback_to_whisper`, `_change_model` execution, LRU eviction trigger). Create `tests/test_recording_controller.py` (test `__init__` wiring, `_on_silence_*`, `_on_max_duration_auto_stop`, `toggle_dictation` RACE-025 concurrency).
**Severity:** 🟡 Medium
**Category:** Test coverage gaps

---

### [XS-83] — build:dist:electron skips prebuild (icon generation) — will fail on fresh checkout
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/package.json` `build:dist:electron` script is `electron-vite build && electron-builder`. `prebuild` (icon generation via `generate-icons.mjs`) is an npm lifecycle hook that fires ONLY before `npm run build`, NOT before `npm run build:dist:electron`. `electron-builder.yml` references `resources/icon.png` (win.icon/mac.icon/linux.icon). On a fresh checkout (where `resources/` does NOT exist — verified), `npm run build:dist:electron` will fail.
**Root Cause:** Script doesn't trigger the prebuild lifecycle hook.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json (build:dist:electron script)`
**Fix:** Change `build:dist:electron` to `node scripts/generate-icons.mjs && electron-vite build && electron-builder`, OR add a `prebuild:dist:electron` script (npm 7+ supports pre-hooks for colon-named scripts).
**Severity:** 🟡 Medium
**Category:** Build pipeline

---

### [XS-85] — No JUnit XML + no PR test-result reporting — failed tests buried in CI logs
**Status:** ❌ Not Fixed
**Description:** No `--junitxml` flag in CI. No `mikepenz/action-junit-report`, no `EnricoMi/publish-unit-test-result-action`, no `dorny/test-reporter`. On a 331-file suite, a single failing test is buried in `-v --tb=short` output with no PR-level annotation. A contributor must open the workflow run, find the failing step, scroll the log.
**Root Cause:** Test reporting never wired up.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml:208`
**Fix:** Add `--junitxml=pytest-results.xml` to CI pytest. Add `mikepenz/action-junit-report@v4` step (with `check_name: Test results` + `report_paths: pytest-results.xml`) so failed tests appear as PR annotations.
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-86] — No coverage ratchet — coverage can drop from 70% → 65.01% without CI signal
**Status:** ❌ Not Fixed
**Description:** Ruff ratchet exists (`scripts/ruff_ratchet_check.py`). Pyrefly ratchet exists (`pyrefly-baseline.json`). Coverage ratchet DOES NOT EXIST. Coverage uses a fixed `--cov-fail-under=65` floor. Coverage can drop from 70% → 65.01% without any CI signal. Only when it crosses below 65% does CI fail.
**Root Cause:** Coverage ratchet never implemented.
**Progress:** None yet.
**Related Files:**
- `scripts/coverage_ratchet_check.py (new file)`
- `coverage-baseline.json (new file)`
- `.github/workflows/build.yml`
**Fix:** Implement `scripts/coverage_ratchet_check.py` that parses `coverage.xml`, compares total coverage % against `coverage-baseline.json`, fails CI if current < baseline (with epsilon for float jitter), supports `--regenerate`. Wire into `build.yml` `test` job after Codecov upload (ubuntu×3.12 leg only).
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

---

### [XS-87] — mutation.yml is dead code (if: false) + doc drift (mutmut still in dev extras)
**Status:** ❌ Not Fixed
**Description:** `mutation.yml:52` has `if: false` at job level — never runs. `mutation.yml:78-88` `config_check` step exits 0 if `tests/mutmut_config.py` is missing — that file does NOT exist. `mutation.yml:6-7` comment says 'mutmut was removed from the dev extra in pyproject.toml — see CR-62 note'. But `pyproject.toml:267-268` STILL lists `mutmut>=2.4` in the dev extra. The comment lies.
**Root Cause:** Mutation testing abandoned but workflow + dep left in place.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/mutation.yml`
- `pyproject.toml:267-268`
**Fix:** Either (a) delete `mutation.yml` + remove `mutmut>=2.4` from `[dev]` extra, OR (b) update the comment to reflect that mutmut is still in dev and re-enable the workflow (`if: true` + restore `tests/mutmut_config.py`).
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### [XS-88] — stale eslint-disable directives (3 confirmed stale) in TS client
**Status:** ❌ Not Fixed
**Description:** 9 `eslint-disable` directives exist in TS client, but the project uses biome (ESLint NOT installed). 3 confirmed stale: `shutdown-hooks.test.ts:192` (`@typescript-eslint/no-explicit-any` — next line declares `Array<() => void>`, no `any` present), `detect.ts:23` (block-level `@typescript-eslint/no-explicit-any` — file uses `unknown` casts, zero `any`), `test-setup.ts:70` (`@typescript-eslint/no-unused-vars` — parameter already prefixed with `_`).
**Root Cause:** Stale ESLint directives left after Biome migration.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/__tests__/shutdown-hooks.test.ts:192`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/detect.ts:23`
- `voice_typer/client/src/renderer/src/test-setup.ts:70`
**Fix:** Delete the 3 confirmed-stale `eslint-disable` directives. Optionally remove the other 6 (no-var-requires, no-console) since ESLint is not installed.
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors

---

### [XS-94] — 6 redundant @pytest.mark.asyncio decorators (auto mode handles it)
**Status:** ❌ Not Fixed
**Description:** `tests/test_sidecar_ready_emitted.py:137,227,297` and `tests/test_sidecar_ws_thread_safety.py:193,299,414` use explicit `@pytest.mark.asyncio` decorator on `async def test_*` functions. But `pyproject.toml:405` sets `asyncio_mode = 'auto'`, which auto-marks all async tests. The markers are redundant.
**Root Cause:** Decorators predate `asyncio_mode = 'auto'`.
**Progress:** None yet.
**Related Files:**
- `tests/test_sidecar_ready_emitted.py:137,227,297`
- `tests/test_sidecar_ws_thread_safety.py:193,299,414`
**Fix:** Remove the 6 `@pytest.mark.asyncio` decorators.
**Severity:** 🟢 Low
**Category:** Testing infrastructure

---

### [XS-101] — wintypes.VOID absent on Linux — 8 test__security_attributes.py failures
**Status:** ❌ Not Fixed
**Description:** `tests/test__security_attributes.py::TestDaclConstruction` and siblings (8 failures) fail with `AttributeError: module 'ctypes.wintypes' has no attribute 'VOID'`. Production code at `voice_typer/server/_security_attributes.py:105` references `wintypes.VOID`, which doesn't exist on Linux's `ctypes.wintypes`. The production code is Windows-only and the test mocks `ctypes.windll`, but the mock doesn't patch `wintypes.VOID`.
**Root Cause:** Production code has a latent bug (should define a fallback `VOID = ctypes.c_void_p` if `wintypes.VOID` is absent); test mock is incomplete.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_security_attributes.py:105`
- `tests/test__security_attributes.py`
**Fix:** In `_security_attributes.py`, add a fallback: `try: from ctypes.wintypes import VOID; except (ImportError, AttributeError): VOID = ctypes.c_void_p`. In the test, `monkeypatch.setattr(ctypes.wintypes, 'VOID', ctypes.c_void_p)` on non-Windows.
**Severity:** 🟢 Low
**Category:** Existing failing tests

---

### [XS-104] — tokio features = ['full'] is over-broad
**Status:** ❌ Not Fixed
**Description:** `src-tauri/Cargo.toml:52` pins `tokio = { version = '1', features = ['full'] }`. `'full'` pulls in `fs`, `io-util`, `io-std`, `macros`, `net`, `parking_lot`, `process`, `rt`, `rt-multi-thread`, `signal`, `sync`, `time` — far more than what an IPC-WebSocket host needs.
**Root Cause:** Over-broad feature set.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml:52`
**Fix:** Replace `['full']` with `['rt-multi-thread', 'macros', 'net', 'sync', 'time', 'io-util']`. Verify with `cargo check --no-default-features` on host (cannot run in sandbox).
**Severity:** 🟢 Low
**Category:** Dependency & supply-chain health

---

### [AC-16] — Error-code registry duplicated across Python (`ipc/validation.py:81-98`), TS (`types/ipc.ts:88-118`), and Rust (hardcoded strings in `sidecar_cmds.rs`) with NO cross-layer parity test
**Status:** ❌ Not Fixed
**Description:** Python canonical registry: `ERROR_CODES: frozenset[str]` with 14 namespaced codes. TS union: `ErrorCodes` with 27 total (14 namespaced + 11 legacy aliases + 2 Rust-only codes). Rust: hardcoded JSON string literals `"disallowed_window"` and `"disallowed_command"` (no enum/registry). The Python `ERROR_CODES` registry is cross-checked by `tests/test_error_codes_registry.py`. The TS `ErrorCodes` union is NOT cross-checked against the Python `ERROR_CODES` — there is no test that asserts `ERROR_CODES ⊆ ErrorCodes`. The Rust hardcoded strings ARE in the TS union but NOT in the Python `ERROR_CODES` frozenset (intentional — Rust emits these BEFORE dispatch reaches Python — but undocumented).
**Root Cause:** Verified. Three independent declarations of the error-code set with no single source of truth and no cross-layer parity test.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/validation.py:81-98`
- `voice_typer/client/src/renderer/src/types/ipc.ts:88-118`
- `src-tauri/src/commands/sidecar_cmds.rs:38-46, 528-557`
- `tests/test_error_codes_registry.py` (target file for cross-layer test)
**Fix:** (a) Add a parity test that parses the TS `ErrorCodes` union from `types/ipc.ts` and asserts `Python ERROR_CODES ⊆ TS ErrorCodes`. (b) Add the Rust-only codes (`disallowed_window`, `disallowed_command`) to the Python `ERROR_CODES` registry with a comment noting they are Rust-host-emitted. (c) Extract the Rust hardcoded strings into `pub(crate) const DISALLOWED_WINDOW: &str = "disallowed_window";` constants.
**Severity:** 🟡 Medium

---

### [AC-20] — `recorder.py` `_dropped_chunks` and `_rms_callback_error_count` use `getattr(self, "...", 0) + 1` pattern — never initialized in `__init__`/`start()`
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:2392, 2624` use `self._dropped_chunks = getattr(self, "_dropped_chunks", 0) + 1` and `self._rms_callback_error_count = getattr(self, "_rms_callback_error_count", 0) + 1`. Every other state attribute on `Recorder` is explicitly initialized in `__init__` (lines 299-496) and reset in `start()` (lines 1217-1313). These two were added later without updating `__init__`/`start()`. Tests that assert `rec._dropped_chunks == 0` on a fresh instance would fail with AttributeError.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:2392, 2624`
**Fix:** Add `self._dropped_chunks: int = 0` and `self._rms_callback_error_count: int = 0` to `Recorder.__init__` (near line 307 where `_xruns` is initialized). Add resets to `start()`'s reset block (near line 1307). Replace the `getattr(self, "...", 0) + 1` with direct `self._X += 1`.
**Severity:** 🟢 Low

---

### [AC-24] — `task_scheduler.py::_schtasks_elevated` discards `WaitForSingleObject` return — STILL_ACTIVE (259) leaks into return tuple
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/task_scheduler.py:568-582` calls `WaitForSingleObject(sei.hProcess, timeout_ms)` and discards the return. If schtasks doesn't exit within `timeout_ms` (60s default), `WaitForSingleObject` returns `WAIT_TIMEOUT (258)`, the code proceeds to `GetExitCodeProcess` which fills `exit_code` with `STILL_ACTIVE (259)`. The function returns `(259, output)` — caller interprets as "process exited with code 259" and treats `rc != 0` as failure, falling back to HKCU Run key. User sees misleading "schtasks registration failed" warning when the actual issue was a timeout. XS-89 noted the discarded return value but did not flag the STILL_ACTIVE leak.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/task_scheduler.py:568-582`
**Fix:** Check `WaitForSingleObject` return: `if wait_result == 258 { CloseHandle(sei.hProcess); return 124, f"UAC schtasks timed out after {timeout_ms}ms" }`. Also check `GetExitCodeProcess` bool return.
**Severity:** 🟡 Medium

---

### [AC-25] — `windows_native.py` `_modifiers_pressed` and `_key_pressed` lack `_user32` guard (sibling methods have it)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/hotkeys/windows_native.py:1364-1378` `_modifiers_pressed` calls `self._key_pressed(0x11)` etc. without checking `self._user32`. `_key_pressed` (line 1378) does `bool(self._user32.GetAsyncKeyState(vk) & 0x8000)` without guard. Sibling methods `_other_modifiers_pressed` (line 1265) and `_is_altgr_pressed` (line 1388-1395) DO have `if not self._user32: return False` guards. If `_user32` is None (e.g. on a non-Windows test host), `_modifiers_pressed()` raises `AttributeError: 'NoneType' object has no attribute 'GetAsyncKeyState'`.
**Root Cause:** Verified. Inconsistent defensive coding.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkeys/windows_native.py:1265, 1364-1395`
**Fix:** Add `if not self._user32: return False` as the first line of `_modifiers_pressed` and `_key_pressed`.
**Severity:** 🟢 Low

---

### [AC-29] — `cloud_engines.py` retry/backoff logic DUPLICATED between `_send_openai_compatible` (lines 488-630) and `_send_deepgram` (lines 632-755)
**Status:** ❌ Not Fixed
**Description:** Both methods share the same retry/backoff skeleton: `max_retries = 3` / `retried_429 = False` / `for attempt in range(max_retries):` / `HTTPError` 429 branch with `_parse_retry_after` / `URLError` branch with `backoff = 0.5 * (2**attempt)` / final `raise RuntimeError(f"{provider} API error")`. ~60 lines of near-identical retry logic. They have already drifted: the OpenAI path's `Exception` branch includes `{safe_msg}`; the Deepgram path drops it.
**Root Cause:** Verified. Two parallel implementations of the same retry strategy.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/cloud_engines.py:488-630, 632-755`
**Fix:** Extract `_transcribe_with_retry(provider: str, build_request: callable, parse_response: callable) -> str` helper. Both methods become thin wrappers.
**Severity:** 🟡 Medium

---

### [AC-34] — `system_cmds.rs::open_logs` returns success on mkdir failure + `open_path_in_file_manager` returns success on path-not-exists
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/system_cmds.rs:49-62` `open_logs` does `let _ = std::fs::create_dir_all(&log_dir);` (silently discards mkdir failure). `:67-97` `open_path_in_file_manager` returns `Ok(())` based solely on whether `Command::spawn()` succeeded — does NOT verify path exists, does NOT wait for child, does NOT check exit status. Triple failure mode: (a) config_dir unwritable, (b) mkdir fails silently, (c) explorer.exe spawns and shows "path not found", (d) `open_logs` returns `{"success": true}`. UI shows "logs opened" while user sees an explorer error dialog.
**Root Cause:** Verified. `Command::spawn` semantics verified against std docs.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/system_cmds.rs:49-97`
**Fix:** (a) Capture `create_dir_all` result and include in error path. (b) Add pre-check `if !path.exists() { return Err(format!("path does not exist: {}", path.display())); }` before spawn.
**Severity:** 🟡 Medium

---

### [AC-35] — `migrate.rs::atomic_write_bytes` (generic fs util) trapped in migration module — `supervisor.rs` imports from `migrate` for unrelated utility
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/migrate.rs:425-463` `atomic_write_bytes` is a generic atomic-write utility (temp + fsync + rename) with zero coupling to Electron-migration logic. `src-tauri/src/sidecar/supervisor.rs:16, 164` imports `use crate::migrate::atomic_write_bytes` — artificial module coupling. A maintainer reading `supervisor.rs:16` would reasonably ask "why does the FT-1 supervisor depend on the Electron migration module?" Same issue with `atomic_copy` (migrate.rs:471-475) and `sidecar_path` (migrate.rs:481-491).
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/migrate.rs:425-491`
- `src-tauri/src/sidecar/supervisor.rs:16, 164`
**Fix:** Move `atomic_write_bytes`, `atomic_copy`, `sidecar_path`, `file_newer_than` to `util.rs` (or new `fs_util.rs`). Update 2 import sites.
**Severity:** 🟡 Medium

---

### [AC-36] — `state.rs` 56% non-state content: `SidecarHandle` + `kill_process_tree` are process-management, not shared-state
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/state.rs:1` module docstring says "Shared state types for the Voice Typer Tauri host". But `:70-162` `SidecarHandle` enum + kill/kill_tree/pid methods (93 lines) and `:181-249` `kill_process_tree` (69 lines, platform-specific process killing) are NOT shared-state types. `spawn.rs` imports `crate::state::kill_process_tree` — a process-killing utility from a module named "state", semantically misleading. ~209 lines (56%) of `state.rs` are process-management.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs:70-249`
- `src-tauri/src/sidecar/spawn.rs:158, 190, 239, 333`
**Fix:** Extract `SidecarHandle` + `kill_process_tree` to new `src-tauri/src/process.rs`. Update ~8 import sites.
**Severity:** 🟡 Medium

---

### [AC-39] — `logging.rs:132-142` `bubble_level` filter is overbroad substring match (drops any log mentioning "bubble_level")
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/platform/logging.rs:132-142` filters file-log writes with `if !msg.contains("bubble_level")`. Any log line from ANY module that mentions "bubble_level" anywhere in its text is silently dropped from the file log. Operators debugging bubble_level issues who add `log::debug!("[DEBUG] bubble_level coalesce stats: ...")` will find their diagnostic lines missing with no indication why.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs:132-142`
**Fix:** Filter by `record.target()` (e.g., `if record.target() == "crate::sidecar::ws" && msg.contains("bubble_level")`), or use structured log key-values, or add a one-time `log::warn!` when the filter first fires on a non-WS-reader target.
**Severity:** 🟢 Low

---

### [AC-41] — `clipboard/linux.py:40`, `manager.py:44`, `windows.py:31` each define local `log = logging.getLogger(...)` but NEVER use it (every log call goes through `_cb.log`)
**Status:** ❌ Not Fixed
**Description:** Three clipboard submodules define local `log` but never use it. Readers assume `log` is the active logger and may add new `log.info(...)` calls that bypass test patches (silently breaking the patchability contract).
**Root Cause:** Verified. PVT-23 split preserved the per-submodule `log` definition but the dynamic-dispatch design contract mandates all logging go through `_cb.log`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/linux.py:40`
- `voice_typer/server/clipboard/manager.py:44`
- `voice_typer/server/clipboard/windows.py:31`
**Fix:** Delete the three unused `log = logging.getLogger(...)` lines. Keep only the package-level `log` in `__init__.py:98`.
**Severity:** 🟡 Medium

---

### [AC-42] — `clipboard_target_safety.py:774-778, 788-794, 807-813` three near-identical AX-result tuple-shape checks (DRY)
**Status:** ❌ Not Fixed
**Description:** Three nearly-identical 5-line tuple-shape checks for pyobjc AX result values. The two later blocks have already drifted semantically (lines 774-778 short-circuit early; 788-794 / 807-813 silently skip).
**Root Cause:** Verified. Copy-paste of the same AX-result unwrap pattern.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard_target_safety.py:774-813`
**Fix:** Extract `_ax_result_value(result) -> Any | None` helper.
**Severity:** 🟡 Medium

---

### [AC-43] — `clipboard_target_safety.py:858, 959-967` hidden coupling: module global `_PYATSPI_STATE_FOCUSED` written by `_is_password_field_linux` but read by `_find_focused_atspi_accessible`
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard_target_safety.py:906` `_PYATSPI_STATE_FOCUSED: Any = None` is written ONLY inside `_is_password_field_linux` (lines 959-967) but READ inside `_find_focused_atspi_accessible` (line 858). If `_find_focused_atspi_accessible` is ever called before `_is_password_field_linux` has initialized the global, `_PYATSPI_STATE_FOCUSED` is `None` and `root_state.contains(None)` raises `TypeError`.
**Root Cause:** Verified. Initialization order is implicit.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard_target_safety.py:858, 906, 959-967`
**Fix:** Pass `state_focused` as a parameter to `_find_focused_atspi_accessible(desktop, state_focused, max_depth=10)`. Eliminates the global entirely.
**Severity:** 🟡 Medium

---

### [AC-48] — `app.py:691-698, 777-784` duplicated 8-line keyboard_ownership check (DRY)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/app.py:691-698` `undo_last` and `:777-784` `_cancel_dictation` both have near-identical 8-line `try: from voice_typer.server.keyboard_ownership import keyboard_ownership; if keyboard_ownership().is_hotkey_capture_active(): return except Exception: log.debug(...)` blocks. Only the log tag string differs.
**Root Cause:** Verified. CR-017 added the check to `undo_last` by literally copying the pattern from `_cancel_dictation`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:691-698, 777-784`
**Fix:** Extract `_skip_due_to_hotkey_capture(tag: str) -> bool` helper. Both methods become `if self._skip_due_to_hotkey_capture("UNDO"): return`.
**Severity:** 🟢 Low

---

### [AC-50] — `dictation_pipeline.py:596, 638` `active_transcriber()` called twice (second call re-fetches same backend to read `device_info`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:596` calls `active = self._app.models.active_transcriber()`. `:638` calls `active = self._app.models.active_transcriber()` AGAIN to read `device_info`. `touch_active_model()` between them only updates an LRU timestamp; it does not swap the active backend.
**Root Cause:** Verified. The second call was added when device_info logging was bolted on, without reusing the existing `active` binding.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py:596, 638`
**Fix:** Reuse the first `active` binding for the `device_info` lookup.
**Severity:** 🟢 Low

---

### [AC-51] — `dictation_pipeline.py` 5x duplicated bubble hide/idle dance (DRY)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:261-267, 304-310, 705-711, 1190-1197, 1279-1285` — 5 repetitions of the same 4-line bubble hide/idle pattern. The 5 log messages already drift slightly.
**Root Cause:** Verified. NEW-BUBBLE-TRANSCRIBING change was applied to 5 call sites by copy-paste.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py` (5 sites)
**Fix:** Add `_hide_or_idle_bubble(reason: str)` helper. Replace all 5 sites.
**Severity:** 🟡 Medium

---

### [AC-52] — `tray.py:962-1073` `_build_menu` reimplements menu inline; `tray_menu.build_menu` (lines 97-200) is DEAD production code with STALE signature
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray_menu.py:97-200` `build_menu` signature does NOT include `Undo Last`, `Microphones` submenu, `Settings/History/Help`. `voice_typer/server/tray.py:962-1073` `_build_menu` builds 11+ menu items inline. `tray_menu.build_menu` is NEVER called from production code. Only references: re-export comment in tray.py:55-57, identity assertion in tests/test_e2e_smoke.py:168, docstring reference in tests/tauri/mig19/test_tray_menu.py:301.
**Root Cause:** Verified. Extraction was started but abandoned mid-way.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray_menu.py:97-200`
- `voice_typer/server/tray.py:55-62, 962-1073`
- `tests/test_e2e_smoke.py:168`
**Fix:** Either (a) DELETE `tray_menu.build_menu` + the identity assertion + the re-export, OR (b) merge: make `_build_menu` call `tray_menu.build_menu` with extended signature. Option (a) is simpler.
**Severity:** 🔴 High

---

### [AC-53] — `tray.py:1099-1124` Tauri path Microphones submenu never marks active mic + never offers "Refresh mics" (phantom attrs)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray.py:1099-1124` `_maybe_publish_tray_menu` passes `active_mic_id=getattr(controller, "active_microphone_id", None)` and `on_refresh_mics=getattr(controller, "refresh_microphones", None)`. Grep confirms `active_microphone_id` is NEVER DEFINED in the codebase. `refresh_microphones` is defined on `ServiceProtocol` and `MicrophoneTestService`, NOT on `VoiceTyperApp` (the tray's `controller`). Defensive `getattr` masked the bug.
**Root Cause:** Verified. The Tauri menu model was wired with defensive `getattr(..., None)` calls against attribute names that don't exist on the actual controller class.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py:1099-1124`
- `voice_typer/server/tray_types.py:26-41` (Protocol)
- `voice_typer/server/app.py:268` (VoiceTyperApp `_microphones` is private)
**Fix:** Add `active_microphone_id` and `refresh_microphones` as PUBLIC attributes/methods on `VoiceTyperApp`, update the `TrayController` Protocol, and update production to use them (drop `getattr` defensive calls).
**Severity:** 🔴 High

---

### [AC-61] — `parakeet_engine.py:873, 875-885` docstring claims "per-transcription fallback, not permanent" but `.to(device="cpu")` makes it permanent until next `load()`
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/parakeet_engine.py:873` CPU fallback does `self._model.to(device="cpu", dtype=self._torch.float32)`. Lines 875-885 docstring claims "self.device is NOT mutated here — it stays 'cuda' so the next load() re-attempts CUDA (per-transcription fallback, not permanent)." But `self._model.device` is now `cpu` after `.to(device="cpu")`. The next `transcribe()` call (line 669) does `inputs.to(device=self._model.device, ...)` — `self._model.device` is CPU. So the next transcription ALSO runs on CPU. The "per-transcription" claim is false — fallback is permanent until the next `load()`.
**Root Cause:** Verified. `self.device` (engine config field) is not mutated, but `self._model.device` (actual torch tensor device) IS mutated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:873, 875-885, 669`
**Fix:** Either (a) update the docstring to accurately describe "permanent until next `load()`", OR (b) implement the documented "per-transcription" behavior by snapshotting the model to a CPU copy and restoring `self._model.to(device="cuda")` after each fallback transcription.
**Severity:** 🟡 Medium

---

### [AC-63] — `service/model.py:616-1091` `download_model` returns 6 distinct dict shapes (IPC contract drift)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/service/model.py:616-1091` `download_model` returns 6 distinct dict shapes across 8 return paths:
- cancelled: `{"success": False, "cancelled": True, "message": ...}` — NO `model`
- consent err: `{"success": False, "error": ..., "consent_required": True, "model": model_name}`
- whisper success: `{"success": True, "model": model_name}`
- qwen cached: `{"success": True, "model": model_name, "message": ...}`
- qwen unconfigured: `{"success": False, "error": ...}` — NO `model`
- parakeet success: `{"success": True, "model": model_name}`
- unknown model: `{"success": False, "error": ...}` — NO `model`
- generic exception: `{"success": False, "error": str(exc)}` — NO `model`

4 of 8 return paths omit the `model` field. TS renderer's `useDownloadModel` hook reads `result.model` for success toasts.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/model.py:616-1091`
**Fix:** Define a `DownloadResult` TypedDict with `success: bool`, `model: str`, `message: str | None`, `error: str | None`, `cancelled: bool | None`, `consent_required: bool | None`. All 8 return paths populate at least `success`, `model`, and either `message` or `error`. The `DownloadResult` type already exists at `service/__init__.py:76` — use it as the return annotation.
**Severity:** 🟡 Medium

---

### [AC-64] — `service/__init__.py:418-925` 3 GDPR methods (~505 LOC) NOT extracted to PrivacyMixin — `service/__init__.py` is 940 LOC because of them
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/service/__init__.py:418-645` `delete_all_personal_data` (227 LOC), `:646-776` `reset_config_to_defaults` (130 LOC), `:777-925` `export_gdpr_bundle` (148 LOC) = 505 LOC of GDPR/privacy concern in `__init__.py`. The class docstring at L131-146 claims GDPR is "cross-cutting" but the three methods share two GDPR-specific class constants (`_GDPR_PERSONAL_FILES`, `_GDPR_PERSONAL_GLOBS`) and the same `_checkpoint_history_db` defensive pattern. This is a single domain, not a cross-cutting concern. `service/__init__.py` is 940 LOC — above the 800-line spaghetti threshold — almost entirely because of these 3 methods (505/940 = 54%).
**Root Cause:** Verified. The prior PVT-026 / H-1 / S2-CR-2 split extracted 8 mixins but stopped before privacy.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/__init__.py:148-176, 418-925`
**Fix:** Extract a new `PrivacyMixin` to `voice_typer/server/service/privacy.py`. Move `_GDPR_PERSONAL_FILES`, `_GDPR_PERSONAL_GLOBS`, `delete_all_personal_data`, `export_gdpr_bundle`, and a new `_checkpoint_history_db` helper. Add `PrivacyMixin` to the inheritance list at L121-130.
**Severity:** 🔴 High

---

### [AC-65] — `app.py:916-1081` `restart_app` is 165 LOC of "postmortem graveyard" with 12+ historical-fix comment blocks
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/app.py:916-1081` `restart_app` body: ~40 LOC of actual code, ~125 LOC of docstring + historical-fix commentary (CR-013, CR-014, CR-017, CR-018, CR-064, PERF-005, RACE-020, HIGH-36, XCUT-1, RW-3, THEME-RESTART-FIX, CRITICAL ORDERING FIX, PVT-2). `_wait_for_relaunch_ack` (77 LOC): ~15 LOC of code, ~62 LOC of docstring + 4 inline `TODO Fix-A` comments. S2-CR-24 proposed `RestartController` extraction when method was 145 LOC; method has since GROWN by 20 LOC.
**Root Cause:** Verified. Every past incident (CR-013 through CR-064) added a paragraph of explanation rather than refactoring the method.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:916-1081, 1082-1159`
**Fix:** Extract `restart_app` + `_wait_for_relaunch_ack` + `quit_app` into a new `RestartController` (or extend existing `ShutdownController`). VoiceTyperApp keeps a thin delegate. Convert historical-fix comments into CHANGELOG entry + reference. Resolve `TODO Fix-A` by adding `IPCServer.wait_for_relaunch_ack(timeout) -> bool` and deleting the 4 inline TODOs.
**Severity:** 🔴 High

---

### [AC-66] — `app.py:268-271` VoiceTyperApp private state (`_microphones`, `_busy_event`, `_lock`) accessed by 6 external modules (backdoor API surface)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/app.py:268-271` declares `self._microphones: list[dict] = []`, `self._busy_event = threading.Event()`, `self._lock = threading.Lock()`. External modules reach into these "private" attributes: `service/microphone_test.py:26, 53, 62`, `dictation_pipeline.py:362, 369, 783, 1234`, `recording_controller.py:160, 165, 407, 435, 470, 628, 799, 847` (busy_event), `model_manager.py:760`, `startup_tasks.py:233, 235`. 6 modules reach into VoiceTyperApp internals, blocking safe rename/move. `_busy_event` semantics ("SET = not busy") are inverted from the natural reading and only documented at the declaration site.
**Root Cause:** Verified. When RecordingController, MicrophoneTestMixin, ModelManager, DictationPipeline, and startup_tasks were extracted from VoiceTyperApp, the shared state was left behind on VoiceTyperApp rather than moved into the owning controller.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:268-271`
- `voice_typer/server/service/microphone_test.py:26, 53, 62`
- `voice_typer/server/dictation_pipeline.py:362, 369, 783, 1234`
- `voice_typer/server/recording_controller.py:160, 165, 407, 435, 470, 628, 799, 847`
- `voice_typer/server/model_manager.py:760`
- `voice_typer/server/startup_tasks.py:233, 235`
**Fix:** Define explicit `BusynessCoordinator` (or extend `RecordingController`) that owns `_busy_event` + `_lock` and exposes `is_busy() / set_busy() / set_idle()`. Move `_microphones` ownership into `MicrophoneTestMixin` or new `MicrophoneRegistry`. Add the new public methods to `AppProtocol`. Update the 14 consumer call sites.
**Severity:** 🟡 Medium

---

### [AC-67] — `service/__init__.py:178-217` fat base class — 8 mixin classes contribute NO `__init__`, base owns state for 3 separate concerns
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/service/__init__.py:178-217` `VoiceTyperService.__init__` initializes: `_download_cancel_events` (used ONLY by ModelMixin), `_download_cancel_lock` (used ONLY by ModelMixin), `_active_download_id` (used ONLY by ModelMixin), `_microphones_cache` (used ONLY by MicrophoneTestMixin), `_microphones_cache_ts` (used ONLY by MicrophoneTestMixin), `_model_status_cache` (used ONLY by ModelMixin), `_model_status_cache_ts` (used ONLY by ModelMixin), `_model_status_cache_lock` (used ONLY by ModelMixin). None of the 8 mixin classes define their own `__init__` — verified via grep.
**Root Cause:** Verified. The "mixin" pattern was applied to method bodies but not to state ownership.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/__init__.py:178-217`
- `voice_typer/server/service/model.py` (ModelMixin consumer)
- `voice_typer/server/service/microphone_test.py` (MicrophoneTestMixin consumer)
**Fix:** Convert each stateful mixin to use cooperative multiple inheritance with `super().__init__()` chaining, OR introduce a single `MixinState` dataclass owned by the service. Move `_download_cancel_*` / `_model_status_cache_*` into `ModelMixin.__init__`, `_microphones_cache_*` into `MicrophoneTestMixin.__init__`.
**Severity:** 🔴 High

---
### [AC-70] — `history_db.py:672-878` `_init_db_schema` is 207-line method mixing 4 concerns
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/history_db.py:672-878` `_init_db_schema` does: (1) CREATE TABLE for `transcriptions` + `schema_meta`, (2) schema version read, (3) migration loop with `executescript` + version-persist + duplicate-column-name special-case, (4) post-migration index creation with column-existence guard, (5) integrity check + recovery dispatch. 4 distinct responsibilities with their own error paths.
**Root Cause:** Verified. Method grew organically as G4-CR-02, G4-CR-03, G4-M-03 fixes were layered on.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:672-878`
**Fix:** Split into `_create_tables(conn)`, `_read_schema_version(conn) -> int`, `_run_migrations(conn, current_version) -> int`, `_create_indexes(conn)`. `_init_db_schema` becomes a 20-line orchestrator.
**Severity:** 🟡 Medium

---

### [AC-72] — `history_db.py:1373-1877` 9 public methods repeat identical 4-line `except (HistoryDBError, Exception)` boilerplate (~90 lines duplicated)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/history_db.py:1373-1402` (`delete`), `:1404-1462` (`restore`), `:1464-1529` (`clear_all`), `:1531-1565` (`toggle_favorite`), `:1567-1687` (`apply_retention`), `:1693-1724` (`get_recent`), `:1755-1816` (`search`), `:1818-1847` (`get_favorites`), `:1849-1877` (`get_today_stats`) — 9 methods each end with the same dual-except boilerplate. `apply_retention` does NOT support `raise_on_error` at all despite being structurally identical.
**Root Cause:** Verified. ERR-013 `raise_on_error` flag forces every method to spell out the same dual-except boilerplate.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:1373-1877` (9 methods)
**Fix:** Extract `_wrap_write(fn, *, sentinel, method_name)` and `_wrap_read(fn, *, sentinel, method_name)` decorators. Alternatively, replace `raise_on_error: bool` with two methods per operation (`delete` returns bool, `delete_strict` raises) — eliminates the conditional.
**Severity:** 🟢 Low

---

### [AC-73] — `dictation_pipeline.py:119-401` `run` method is 282 lines with 80-line `finally` block (spaghetti method)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:119-401` `run` method is 282 lines. The `finally` block alone is 80 lines (322-401). Per-stage timing instrumentation is interleaved with step calls (9 separate `_stage_t0 = time.perf_counter()` + `_xxx_ms = (time.perf_counter() - _stage_t0) * 1000` pairs). Multiple cross-module private-attr mutations in finally (CR-006 cancelled-cycle, RW-13 correlation-id, RACE-013/016 watchdog reset, ARCH-016 thread clear, PVT-015 gc.collect).
**Root Cause:** Verified. `run` accumulated cleanup concerns without ever being decomposed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py:119-401`
**Fix:** Extract `_run_pipeline_body(text)`, `_handle_cancelled_cycle(text)`, `_finalize_cycle()` (split into `_zero_audio`, `_reset_watchdog_and_cancelled_set`, `_teardown_session_and_thread`, `_reset_correlation_id`), and a `StageTimer` context manager to replace the 9 `_stage_t0`/`_xxx_ms` pairs. Target: `run` ≤ 60 lines.
**Severity:** 🔴 High

---

### [AC-74] — `dictation_pipeline.py:421-581` `_check_resources` is 161 lines mixing 3 unrelated probes (RAM/disk/GPU) with platform-specific inline scaffolding
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:421-581` `_check_resources` mixes RAM check (with inline Windows `_MEMORYSTATUSEX` ctypes Structure), disk space check, and GPU memory check. The inline `_MEMORYSTATUSEX` class is re-defined fresh on every call. Two silent `except Exception: pass` sites at lines 467 and 578.
**Root Cause:** Verified. The method grew organically from "log some triage context for heap-corruption crashes" into a 3-concern platform-conditional probe.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py:421-581`
**Fix:** Move the entire method to new `voice_typer/server/resource_probe.py` module. Split into `_probe_ram() -> float | None`, `_probe_disk(paths) -> dict`, `_probe_gpu() -> dict | None`. Define `_MEMORYSTATUSEX` at module scope in `platform/resource_probe_windows.py`.
**Severity:** 🟡 Medium

---

### [AC-75] — `transcription.py:590-778` `_pre_download_model` is 188 lines mixing 4 phases with fragile 4-clause except ladder
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/transcription.py:590-778` `_pre_download_model` runs 4 distinct phases: (1) cache probe, (2) consent check, (3) disk space check, (4) download + verify. 4 sequential `except` clauses. The 4-clause except ladder is fragile: `except RuntimeError: raise` exists ONLY to prevent the broad `except Exception` from swallowing the integrity-check failure. A future maintainer might "clean up" the redundant-looking `raise` and silently break the S1-CR-19 fail-fast contract. Two `from voice_typer.server.security import verify_model_integrity` imports at lines 664 and 744 (duplicate).
**Root Cause:** Verified. Phases accumulated (cache probe → SEC-005 integrity → NEW-PRIV-005 consent → PROD-005 disk space → PROD-004 retry → PROD-006 verify) without ever being decomposed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py:590-778`
**Fix:** Split into `_probe_cache(repo_id, revision, allow_patterns) -> Path | None`, `_require_consent() -> None`, `_check_disk(repo_id, model_size) -> None`, `_download_and_verify(repo_id, revision, allow_patterns, progress_callback) -> Path`. The public `_pre_download_model` becomes an ~20-line orchestrator. Replace the 4-clause except ladder with explicit `try/except` around each phase.
**Severity:** 🟡 Medium

---

### [AC-76] — `transcription.py:941-976` and `:994-1020` near-identical 30-line GPU fallback methods (`_transcribe_with_fallback_unlocked` vs `_transcribe_words_with_fallback_unlocked`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/transcription.py:941-976` `_transcribe_with_fallback_unlocked` and `:994-1020` `_transcribe_words_with_fallback_unlocked` share an identical 8-line "tear down GPU model, reload on CPU" sequence. The only differences are: (a) the inner call, (b) the log message, (c) an extra `release_gpu_memory()` call in the words variant (which XV-72 already flagged as a no-op-while-locked).
**Root Cause:** Verified. Copy-paste when `transcribe_words` was added.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py:941-976, 994-1020`
**Fix:** Unify into `_with_gpu_fallback(self, audio, inner_call, *args, **kwargs)`. Public callers pass `self._transcribe_unlocked` or `self._transcribe_words_unlocked`.
**Severity:** 🟡 Medium

---

### [AC-77] — `transcription.py:780-815, 927-939, 978-992` 3× duplicated 12-line lock+gc wrapper
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/transcription.py:780-815` `transcribe`, `:927-939` `transcribe_with_fallback`, `:978-992` `transcribe_words` all share the same pattern: `with self._lock: ...; if getattr(self, "_pending_gc_collect", False): ...`. The `_pending_gc_collect` flag is read with `getattr(self, "_pending_gc_collect", False)` defensively against a code path that doesn't set it, but the defensiveness is duplicated.
**Root Cause:** Verified. RACE-023 introduced the deferred-gc pattern by editing all three call sites identically.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py:780-815, 927-939, 978-992`
**Fix:** Extract `@contextlib.contextmanager def _with_lock_and_deferred_gc(self)` context manager. Public methods become 3 lines each.
**Severity:** 🟢 Low

---

### [AC-79] — `transcription.py:57, 95-101` module-level mutable globals for NVIDIA DLL path config (test isolation fragility)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/transcription.py:57, 95-101` declares `_nvidia_dll_path_handles: list[object] = []`, `_nvidia_dll_paths_configured = False`, `_nvidia_config_lock = threading.Lock()`. The state survives across tests, so test isolation requires manually resetting `_nvidia_dll_paths_configured = False` and `_nvidia_dll_path_handles = []` in fixtures. The `global` statement at line 116 is a code smell.
**Root Cause:** Verified. Windows-DLL-path configuration was written as module-level functions with module-level state, rather than as a class instance.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py:57, 95-101, 116`
**Fix:** Wrap in a `NvidiaDllPathManager` singleton class. Expose a module-level singleton `_nvidia_dll_paths = NvidiaDllPathManager()`. Tests can construct a fresh instance.
**Severity:** 🟡 Medium

---

### [AC-80] — `text_cleanup.py:260-276` 6 module-level mutable globals + `__import__("threading")` anti-pattern (S3-CR-84 retained)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/text_cleanup.py:260-276` declares 6 module-level mutables (`_active_misspellings`, `_active_phrases`, `_active_extra_words`, `_active_phrase_patterns`, `_active_extra_word_patterns`, `_active_state_lock`) + `_active_state_lock = __import__("threading").Lock()` (S3-CR-84 anti-pattern retained). The code itself acknowledges the architectural debt: "The proper fix is to move these into a TextCleanupService instance; deferred because it touches ~20 call sites."
**Root Cause:** Verified. `text_cleanup` was written as a module of free functions with module-level state, then retrofitted with a lock when concurrency issues surfaced (ARCH-027). The instance refactor was explicitly deferred.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:260-276, 294, 344, 440, 443, 596, 622, 658, 674, 700`
**Fix:** Introduce `TextCleanupService` class encapsulating the 6 mutables + LRU cache + lock. Provide a module-level default singleton and backward-compat shims that delegate to it. Replace `__import__("threading").Lock()` with top-of-module `import threading` + `threading.Lock()`.
**Severity:** 🟡 Medium

---

### [AC-81] — `text_cleanup.py:622-722` `_correct_whisper_phrases` and `_remove_extra_words` near-identical (DRY)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/text_cleanup.py:622-697` `_correct_whisper_phrases` and `:700-722` `_remove_extra_words` are near-identical: same structure (snapshot phrases → lowercase text → loop with substring check → resolve pattern → `pattern.sub`). Differences: (a) case-preserving replacement vs simple string, (b) LRU cache vs parallel list (re-introducing the race — see AC-9).
**Root Cause:** Verified. The two functions were written separately (ARCH-031 + XV-42) and never unified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:622-722`
**Fix:** Unify into `_apply_phrase_substitutions(phrases, patterns_list, replacer_fn, text)` helper. Both functions pass appropriate `replacer_fn`. Both route pattern resolution through the LRU cache.
**Severity:** 🟡 Medium

---

### [AC-82] — `text_cleanup.py:77-240` `_load_external_corrections` is 163 lines mixing 4 phases with copy-paste truncation/filter loops
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/text_cleanup.py:77-240` `_load_external_corrections` combines: (1) load bundled `corrections.json`, (2) merge user corrections file, (3) truncate to max counts (3 separate `if len(...) > max_*` blocks), (4) validate string lengths (3 separate filter loops with 3 separate `_dropped_*` counters). The 3 truncation blocks are copy-paste with different variable names. The 3 filter loops are also copy-paste. The 3 filter loops differ subtly: misspellings filter checks `len(k) > max_pattern_length` AND `len(v) > max_replacement_length` separately; phrase filter checks `len(b) > max_pattern_length or len(g) > max_replacement_length` together.
**Root Cause:** Verified. SEC-010/SEC-011 length limits were added one correction-type at a time, each as a self-contained block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:77-240`
**Fix:** Extract `_truncate(items, max_count, label)` and `_filter_by_length(items, max_pattern, max_replacement, label)` helpers. The 3 call sites become 6 one-liners. The function body drops to ~60 lines.
**Severity:** 🟡 Medium

---

### [AC-83] — `text_cleanup.py:373-379` `configure_corrections` re-parses user JSON (duplicates `_load_external_corrections` parse)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/text_cleanup.py:373-379` `configure_corrections` does `raw = _secure_read_text(user_path, encoding="utf-8"); json.loads(raw)` for validation. `_load_external_corrections` (called via `_active_corrections` at line 384) does the SAME `_secure_read_text(user_path)` + `json.loads(raw)` at lines 135-136. So when the user file exists and is valid, it is read+parsed TWICE. When the user file exists and is malformed, it is read+parsed twice.
**Root Cause:** Verified. `configure_corrections` was hardened (ARCH-004) to return an error message string, but the validation was implemented by re-parsing rather than by reusing `_load_external_corrections`'s `CorrectionsLoadError`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:373-379, 135-136`
**Fix:** Delete the inline parse block (lines 374-382). Wrap the `_active_corrections` call in `try/except CorrectionsLoadError as e: error_msg = str(e); log.warning(...)`. The error message format changes slightly (consistent with ARCH-029's typed exception).
**Severity:** 🟢 Low

---

### [AC-84] — `text_cleanup.py:787-816` `_capitalize_pronoun_i` O(N²) algorithm + hardcoded proper-noun set
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/text_cleanup.py:787-816` `_capitalize_pronoun_i` does `text[:i].rstrip()` and `text[i + 1:].lstrip()` for each `'i'` character — O(N) copy per 'i'. For a 10,000-char transcription with ~200 standalone `'i'` occurrences, that's ~4MB of transient string allocation. A regex `re.sub(r"\bi\b", replacer, text)` would be O(N). `_ROMAN_NUMERAL_CONTEXT_WORDS` includes `"henry"`, `"louis"`, `"richard"` — three specific European monarch names. Fragile (no "george", "edward", "charles", "napoleon", "alexander") and culturally biased.
**Root Cause:** Verified. The character-by-character loop predates the regex-precompile work (PERF-004 / XV-52); `_capitalize_pronoun_i` was missed. The proper-noun list was likely a quick fix for a specific user's transcription of "Henry VIII" and never generalized.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:787-816, 744-784`
**Fix:** Replace the loop with `re.sub(r"(?<![a-zA-Z])i(?![a-zA-Z])", _replacer, text)`. Move `_ROMAN_NUMERAL_CONTEXT_WORDS` and `_ROMAN_NUMERAL_FOLLOWING_WORDS` to `corrections.json` so users can extend; document the format.
**Severity:** 🟡 Medium

---

### [AC-87] — `shutdown_controller.py:196-661` `_do_cleanup` is 466-line method with 18 sequential teardown blocks + 25 `except Exception` clauses + 9 dynamic imports
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/shutdown_controller.py:196-661` `_do_cleanup` contains 18 sequential teardown blocks, 25 `except Exception` clauses, and 9 dynamic imports (verified by `awk`). The method has no internal phase markers; ordering constraints ("tray.stop MUST be LAST" at line 641, "event_bus.shutdown AFTER all publishers torn down" at lines 616-627) are documented in 50+ lines of comments but not enforced by structure.
**Root Cause:** Verified. RW-9 Phase 7 extracted the body verbatim from `VoiceTyperApp` (per module docstring). The extraction was a move, not a decomposition. Each subsequent reliability fix added another `try/except` block inline.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py:196-661`
**Fix:** Extract `_cleanup_phases.py` module with 17 named phase methods + an ordered `PHASES` list. Each phase ≤40 lines, independently testable. After each phase, optionally null the corresponding app attribute.
**Severity:** 🔴 High

---

### [AC-88] — `crash_handler.py:691-855` `report_pending_crash` is 165-line function with 4-clause if-elif chain duplicating `_CRASH_CODES` + `_NAME_*` table
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/crash_handler.py:691-855` `report_pending_crash` does 5 things: (1) glob `crash_diagnostics.*.txt`, (2) glob `python_crash.*.txt`, (3) for each crash file: read → log → if-elif chain on `"STATUS_HEAP_CORRUPTION" in content` → archive, (4) for each python_crash file: read → log → parse `key=value` → archive, (5) sweep stale diagnostics. The if-elif chain at lines 755-783 DUPLICATES knowledge of the `_NAME_*` byte-constant table at lines 156-160 and the if-elif at lines 451-460 in `_vectored_handler_impl` — two representations of "exception code X → human-readable name Y", with drift risk.
**Root Cause:** Verified — accretion. G4-M-34 added the python_crash loop by copying the crash_files loop structure.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_handler.py:691-855, 156-160, 451-460`
**Fix:** Extract `_summarize_crash_file(path) -> str | None` and `_summarize_python_crash_file(path) -> str | None` helpers. Replace the if-elif chain with a single `_CODE_TO_SUMMARY` dict shared between the write side and the read side.
**Severity:** 🟡 Medium

---

### [AC-89] — `crash_handler.py:937-943, 967-981` duplicated redact logic in `_crash_excepthook` (XZ-PII-01 and XZ-PII-02 fixed independently)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/crash_handler.py:937-943` (for `log.critical` call) and `:967-981` (for marker file content) both define redaction logic: both import `_secrets.redact_secret` + `security.redact_pii`, both have fallbacks. The first computes a single redacted value; the second defines a `_redact()` function. The two fallbacks differ: the first truncates to 200 chars, the second does not.
**Root Cause:** Verified. XZ-PII-01 and XZ-PII-02 were fixed independently at different times, each adding its own redact block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_handler.py:937-943, 967-981`
**Fix:** Extract a single `_redact_value(value, *, truncate=200) -> str` helper at module top. Use it for both the log call and the marker content. Hoist the `_secrets` / `security` / `_secure_atomic_write` imports to module top.
**Severity:** 🟢 Low

---

### [AC-91] — `shutdown_controller.py:717, 756-757` `quit()` `is_main` asymmetry (architectural root of XZ-R17-07 SIGTERM-during-startup race)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/shutdown_controller.py:717, 756-757` `quit()`: `is_main = threading.current_thread() is threading.main_thread(); ... app._do_cleanup(); if is_main: sys.exit(0)`. When `quit()` is called from the signal-watcher thread (line 916), the Win32 console handler thread (lines 999, 1006), or the IPC `quit_app` handler thread, `sys.exit(0)` is NEVER called. The process exits only via the atexit safety net OR via `tray.stop()` breaking the pystray loop. The method signature returns `None` either way — the contract is undocumented.
**Root Cause:** Verified. The `is_main` guard prevents `SystemExit` from being raised in a non-main thread (where CPython swallows it). But the guard is a band-aid for a deeper architectural issue: `quit()` has two incompatible contracts (exit vs. cleanup) selected by an implicit runtime check.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py:717, 756-757, 916, 999, 1006`
**Fix:** (a) Document the contract in the method docstring. (b) For non-main-thread callers, after `_do_cleanup()` returns, call `os._exit(0)` as a last resort. (c) Long-term: separate `quit()` (initiates shutdown) from `await_exit()` (blocks until shutdown complete).
**Severity:** 🟡 Medium

---

### [AC-92] — `crash_recovery.py:107-130, 367-397, 501-533` triple safety net (atexit + `__del__` + worker drain) — hard to reason about "when is final state persisted?"
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/crash_recovery.py:107-130` registers `atexit.register(_atexit_save)`. `:501-533` `__del__` calls `_save_sync()` on GC. `:367-397` `shutdown()` enqueues None sentinel, joins worker, then calls `_save_sync()` as "final insurance". Three independent mechanisms persist the final state, each with its own failure mode. `_save_sync` can be called 3 times for the same state.
**Root Cause:** Verified — accretion. RELIABILITY-005 added the worker. a-review Finding A3 added the `shutdown()` final save. G4-M-36 added atexit. `__del__` predates all of them.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_recovery.py:107-130, 367-397, 501-533`
**Fix:** Consolidate to 2 mechanisms: (1) `shutdown()` (the explicit path), (2) atexit (the safety net). Remove `__del__` entirely. Document the 2-mechanism contract.
**Severity:** 🟢 Low

---

### [AC-94] — `duck_crash_recovery.py:53-110` no state machine — `load_stale()` doesn't clear (XZ-R17-10 architecturally)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/duck_crash_recovery.py:53-110` manages a file with 2 implicit states (present / absent). `save()` writes the file. `load_stale()` reads but does NOT clear. `clear()` deletes. XZ-R17-10 notes the consequence: if the caller crashes between `load_stale()` and `clear()`, the file persists and the next launch restores to the WRONG level. XZ-R17-05 notes `save()` is fire-and-forget after volume reduction.
**Root Cause:** Verified. The class has no explicit state machine. The file's state is overloaded: "present" means both "ducked, not yet restored" and "ducked, restored-but-not-yet-cleared".
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/duck_crash_recovery.py:53-110`
**Fix:** Add a 3rd state via a `"consumed": true` flag in the JSON. `load_stale()` returns the state AND marks it consumed (writes back). On next launch, `load_stale()` sees `consumed=true` and returns None. Additionally, add `__enter__` / `__exit__` context manager support.
**Severity:** 🟢 Low

---

### [AC-98] — `sidecar/ws.rs:715-789` `spawn_heartbeat_task` lacks `catch_unwind` (reader/writer tasks have it)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/sidecar/ws.rs:715-789` `spawn_heartbeat_task` does NOT wrap its body in `AssertUnwindSafe(async { ... }).catch_unwind().await`. Compare `spawn_writer_task` (ws.rs:286-296) and `spawn_reader_task` (ws.rs:483-617): both wrap their bodies in `catch_unwind`. If `dispatch_inner` panics, the heartbeat task dies silently. No further heartbeats are sent, the `missed` counter never increments, and the heartbeat-driven FT-1 trigger never fires.
**Root Cause:** verified — the heartbeat task (added as PVT-1) was not given the G4-H-26 panic-safety treatment applied to the reader/writer tasks.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs:715-789, 286-296, 483-617`
**Fix:** Wrap the loop body in `AssertUnwindSafe(async { ... }).catch_unwind()`. On `Err(_panic)`, log at ERROR, treat the panic as a miss (`missed += 1`), and continue the loop. After 3 panic-misses, delegate to `trigger_ft1_respawn_off_thread`.
**Severity:** 🟡 Medium

---

### [AC-99] — `sidecar/ws.rs:285-304` `spawn_writer_task` no cleanup on exit (asymmetric vs reader's cleanup pattern)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/sidecar/ws.rs:285-304` `spawn_writer_task` exits silently on send error or panic. NO cleanup: doesn't clear `state.ws_tx`, doesn't drain `pending`, doesn't emit `ft1_relaunching`, doesn't call `trigger_ft1_respawn_off_thread`. Compare `spawn_reader_task` cleanup (ws.rs:636-685): clears `ws_tx`, drains `pending`, emits `ft1_relaunching`, calls `trigger_ft1_respawn_off_thread` (gated by `shutting_down` check). If the writer dies, `state.ws_tx` remains `Some(...)` — new dispatches queue onto the bounded channel until it fills (256 entries), then `try_send` returns `Full`/`Closed` and every dispatch fails. The reader task continues receiving frames, so the WS-close-detection path doesn't fire. Only the heartbeat task detects the failure (via `dispatch_inner` errors), and only after 3 misses (≥30s) does FT-1 trigger.
**Root Cause:** verified — XZ-11 extracted the writer task but did not mirror the reader's cleanup pattern.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs:285-304, 636-685`
**Fix:** Add a cleanup block after the `catch_unwind` (mirroring reader's pattern). On any exit (normal or panic): clear `state.ws_tx`, drain `pending` with `sidecar_disconnected` errors, emit `ft1_relaunching` with reason `writer_task_exited`, and call `trigger_ft1_respawn_off_thread` (gated by `!shutting_down`).
**Severity:** 🟡 Medium

---

### [AC-101] — `commands/sidecar_cmds.rs:263-307` `dispatch_fire_and_forget` doc internally contradictory about `id=0` log noise
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/sidecar_cmds.rs:263-307` `dispatch_fire_and_forget` doc says BOTH "server does NOT echo `id=0` back" (statement 1: reader receives nothing → no warning) AND "one-line `[WS-READER] unknown id` warning per toggle" (statement 2: reader DOES receive a frame with id=0 → logs unknown-id warning). These two statements are mutually exclusive.
**Root Cause:** Suspected: the doc accumulated two historical understandings of the protocol without reconciliation.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:263-307`
**Fix:** (a) Resolve the doc contradiction by reading `voice_typer/server/.../_handle_dispatch` and updating the comment to match reality. (b) Better: replace the `id=0` magic with an explicit `fire_and_forget: true` flag in the WS frame. (c) At minimum, filter `id=0` in the WS reader (out of this file's scope — `ws.rs`) so no `[WS-READER] unknown id` warning fires for the magic id.
**Severity:** 🟢 Low

---

### [AC-102] — `commands/sidecar_cmds.rs:309-501` `dispatch_frame` 150 LOC of code wrapped in ~120 lines of archaeological PVT-G5-XXX comments
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/sidecar_cmds.rs:309-501` `dispatch_frame` has ~150 lines of code wrapped in ~120 lines of comments referencing 6 separate historical fix tickets: CR-14, CR-50, PVT-G5-017, PVT-G5-035, PVT-G5-036, PVT-G5-087. A new maintainer reading `dispatch_frame` must mentally replay 6 layers of historical fixes to understand the CURRENT behavior.
**Root Cause:** Verified — "archaeological commenting" pattern. Each PVT-G5-XXX fix added its own annotated block without consolidating prior explanations.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:309-501`
**Fix:** Consolidate all historical-fix context into a single "Design notes" section at the END of the doc comment. Move the `!Send` MutexGuard trick explanation into a `// SAFETY:` block. Delete PVT-G5-XXX ticket references from inline comments.
**Severity:** 🟢 Low

---

### [AC-103] — `commands/bubble.rs:1-706` 706-line file mixes 4 separable concerns (Tauri commands + pure geometry + tests + rate limiter)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/bubble.rs:1-706` mixes: (1) 9 Tauri command facades for bubble-window operations, (2) `bubble_toggle_dictation` (sidecar-dispatch concern, not window-operation), (3) Pure coordinate geometry (`parse_position` + `clamp_f64_to_i32` + 22 unit tests = 251 lines, ~36% of file) — no dependency on the bubble window or Tauri state, (4) Process-wide rate limiter (`LAST_TOGGLE_NANOS` static + `TOGGLE_RATE_LIMIT_NS` const + `toggle_rate_limiter_allows()` = 63 lines).
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble.rs` (entire file)
**Fix:** Convert `commands/bubble.rs` to `commands/bubble/` directory: `mod.rs` (~290 lines, 9 Tauri commands), `geometry.rs` (~260 lines, parse_position + clamp + 22 tests), `rate_limiter.rs` (~70 lines, LAST_TOGGLE_NANOS + toggle_rate_limiter_allows + tests).
**Severity:** 🟡 Medium

---

### [AC-104] — `commands/paste.rs:254-416` `restore_focus_or_fallback` 193 lines of Windows-only Win32 focus-restore code (46% of cross-platform paste dispatcher)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/paste.rs:254-416` `restore_focus_or_fallback` + `capture_focus_guard` are 193 lines (46% of paste.rs) of Windows-only `#[cfg(target_os = "windows")]` Win32 focus-restore code with 6 inline `// SAFETY:` blocks of 10-20 lines each. The cross-platform paste strategy (lines 57-182) is obscured by the Windows-specific Win32 dance.
**Root Cause:** Verified. `paste.rs` (416 lines) is a cross-platform paste dispatcher, but 193 lines (46%) are Windows-only.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/paste.rs:254-416, 57-182`
**Fix:** Extract to `commands/paste/windows_focus.rs` (or `commands/paste/focus_restore.rs`). `commands/paste/mod.rs` (~220 lines): `execute_paste`, `is_wayland_session`, `paste_via_enigo_text`, `paste_via_clipboard_and_ctrl_v`. `commands/paste/windows_focus.rs` (~195 lines): `capture_focus_guard`, `restore_focus_or_fallback`, all `#[cfg(target_os = "windows")]`.
**Severity:** 🟢 Low

---

### [AC-105] — `commands/system_cmds.rs:67-97` `open_path_in_file_manager` belongs in `platform/` not `commands/`
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/system_cmds.rs:67-97` `open_path_in_file_manager` is platform-specific OS-binary dispatch (`explorer.exe` / `open` / `xdg-open`) — exactly the kind of per-OS code the `platform/` module was created to host (it already hosts `paths.rs` for per-OS config-dir resolution and `logging.rs` for per-OS file logging). Hosting this in `commands/system_cmds.rs` mixes the "Tauri command facade" concern with the "per-OS binary dispatch" concern, and makes the `platform/` module's coverage of per-OS code incomplete.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/system_cmds.rs:67-97`
- `src-tauri/src/platform/mod.rs`
**Fix:** Move `open_path_in_file_manager` to a new `src-tauri/src/platform/open_path.rs`. Add `pub(crate) mod open_path;` to `platform/mod.rs`.
**Severity:** 🟢 Low

---

### [AC-106] — `windows/bubble-window.ts:417-470, 497-505` `showBubbleWindow` 131 lines with 7 inline try/catch blocks (3x `setAlwaysOnTop` + 2x `moveTop`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/windows/bubble-window.ts:417-470, 497-505` `showBubbleWindow` calls `win.setAlwaysOnTop(true, "screen-saver")` THREE separate times (lines 418, 463, 498), each in its own try/catch. `win.moveTop()` is called twice (line 454 + line 489 inside setImmediate). Each call has its own nearly-identical `log.warn(...)` catch block (~6 lines each). The total `showBubbleWindow` function is 131 lines (377-508), of which ~50 lines are redundant retries.
**Root Cause:** Verified. Defensive retry pattern accumulated over multiple bug-fix sessions (PVT-G5-080/081, plus the setImmediate retry for "not visible after show()" workaround). No single retry call is provably redundant, but the pattern was never consolidated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts:377-508`
**Fix:** Extract a `_tryWinOp(label: string, fn: () => void)` helper. Replace the 7 inline try/catch blocks.
**Severity:** 🟢 Low

---

### [AC-110] — `python/handle-message.ts:64-143` 79-line if-else dispatch chain for 8 message types
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/python/handle-message.ts:64-143` push-events branch is a 79-line if-else chain dispatching 8 message types. Adding a 9th event type requires editing the chain. The `relaunch_app` branch (line 105-126) is 22 lines with side effects (sendToPython ack + relaunchApp call) inlined. XZ-R18-12 already notes that unknown event types fall through to the broadcast at line 141 without validation.
**Root Cause:** Verified. Organic growth — each event was added as a new `else if` branch.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/handle-message.ts:64-143`
**Fix:** Extract a dispatch table: `const PUSH_HANDLERS: Record<string, (msg) => void> = { bubble_show: ..., bubble_hide: ..., ... }`. This also makes XZ-R18-12's "unknown event" validation trivial: `if (!handler) { console.warn("[TCP] unknown push event:", msg.type); return; }`.
**Severity:** 🟢 Low

---

### [AC-113] — `python/send-to-python.ts:79-85` single 120s timeout for ALL command types (heartbeat, quit_app, download_model all share)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/python/send-to-python.ts:79-85` uses `setTimeout(..., 120000)` for all IPC commands. All IPC commands — `heartbeat` (should be 5-10s), `quit_app` (should be 3s), `download_model` (legitimately 120s+), `get_config` (should be <1s) — share the same 120s timeout. PVT-042's proposed fix (c) mentions "Reduce 120s timeout for non-download commands" but this is bundled under PVT-042.
**Root Cause:** Verified. The 120s timeout was increased from 15s to accommodate `download_model` without gating on command type.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/send-to-python.ts:79-85`
**Fix:** Add a per-command timeout map: `const COMMAND_TIMEOUTS: Record<string, number> = { heartbeat: 10_000, quit_app: 5_000, relaunch_ack: 5_000, /* default: 120_000 */ };`.
**Severity:** 🟢 Low

---

### [AC-114] — `i18n.ts:40-145` 105-line `MAIN_STRINGS` literal object (8 locales × 8 keys inline, inconsistent with renderer's JSON files)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/i18n.ts:40-145` `MAIN_STRINGS` is a single 105-line `as const` literal with 8 locales × 8 keys = 64 string entries inline. The renderer uses separate JSON files (`src/renderer/src/i18n/translations/*.json`). The two systems are inconsistent.
**Root Cause:** Verified. The main-process bundle was kept inline for "minimal main-process bundle" (per the file header comment) but this creates a maintenance asymmetry with the renderer.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/i18n.ts:40-145`
**Fix:** Move to per-locale JSON files (`src/main/i18n/locales/en.json`, etc.) loaded synchronously at module init via `fs.readFileSync`. Add a contract test asserting `Object.keys(MAIN_STRINGS)` matches the renderer's `SUPPORTED_LOCALES`.
**Severity:** 🟢 Low

---

### [AC-118] — `python/python-args.ts:23-106` inconsistent packaged-backend lookup (Windows has 2-path fallback + try/catch; macOS/Linux have 1-path no try/catch)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/python/python-args.ts:23-106` Windows (lines 82-103): tries `winOnefileExe`, then `winOnedirExe`, then falls through to dev venv. 2-path fallback + try/catch. macOS (lines 25-40): tries `macBackend` only. 1-path fallback, no try/catch. Linux (lines 42-55): tries `linuxBackend` only. 1-path fallback, no try/catch. If a future macOS/Linux PyInstaller spec changes the output layout, the packaged build would silently fall through to the dev venv path.
**Root Cause:** Verified. Windows got a more robust lookup (RW-4 / Wave 3) with onefile/onedir variants; macOS and Linux were left with single-path lookups.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/python-args.ts:23-106`
**Fix:** Apply the Windows pattern (try/catch + multiple candidate paths) to macOS and Linux. Add onedir fallback paths for each platform. Extract a `resolveBundledBackend(platform: string): string | null` helper.
**Severity:** 🟢 Low

---

### [AC-119] — `python/relaunch-app.ts:50-141` (dev) vs `:144-209` (prod) dev/prod error-path asymmetry (dev never sets `app.isQuitting`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/python/relaunch-app.ts:50-141` dev branch does NOT set `app.isQuitting`. `:152` production branch sets `app.isQuitting = true`. If the new Python (spawned at line 136) crashes immediately, the `proc.on("exit", ...)` handler in start-python.ts:108-196 fires the early-exit branch, which calls `state.mainWindow.destroy()` and `app.quit()`. But `app.isQuitting` is false, so the close-to-tray handler on `mainWindow` would `preventDefault()` a `.close()` — however, `.destroy()` bypasses the close event (per the S3-CR-34 fix), so this specific path is OK. The asymmetry remains: dev mode never sets `isQuitting`, so any code that checks `app.isQuitting` during a dev-mode restart's failure path sees `false`.
**Root Cause:** Verified. Intentional (HIGH-31 / ELEC-1 comment at lines 39-46 explains dev mode must preserve close-to-tray). But the implication for error paths is undocumented.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/relaunch-app.ts:39-46, 50-141, 152`
**Fix:** Document the asymmetry in the dev branch: add a comment explaining that `app.isQuitting` is intentionally NOT set. OR: set a separate `state._devRestarting` flag that error paths can check.
**Severity:** 🟢 Low

---

### [AC-120] — `single_instance.ts:140-158` `releaseSingleInstanceLock` called when we never held the lock (suspected undefined OS-lock release)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/single_instance.ts:140-158` `acquireSingleInstanceLock` calls `app.releaseSingleInstanceLock()` when the FIRST `requestSingleInstanceLock()` returned `false` (we never held the lock). Per Electron docs, `releaseSingleInstanceLock` releases the lock held by THIS process. Calling it when we don't hold the lock is a no-op on most platforms, but the behavior is undocumented for this scenario. On Linux, Electron's single-instance lock is a symlink (`SingletonLock`) in the userData directory; `releaseSingleInstanceLock` deletes it. If the stale (dead) process's lock file is still on disk, `releaseSingleInstanceLock` from our process might delete it.
**Root Cause:** Suspected. The code assumes `releaseSingleInstanceLock` can release a lock held by a DIFFERENT (dead) process.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/single_instance.ts:140-158`
**Fix:** Verify behavior on each platform. On Linux, manually delete the `SingletonLock` symlink (in addition to `releaseSingleInstanceLock`) when a stale PID is detected. On Windows, the named mutex is auto-released when the owning process exits. On macOS, test whether the file lock is released by the OS or needs manual cleanup. At minimum, log the result of the second `requestSingleInstanceLock()`.
**Severity:** 🟢 Low

---

### [AC-121] — `clipboard/linux.py:138-152, 253-266` two near-identical Wayland-detection functions (`_is_wayland_session` vs `_is_wayland_paste_session`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard/linux.py:138-152` `_is_wayland_session()` checks only `WAYLAND_DISPLAY`. `:253-266` `_is_wayland_paste_session()` checks `WAYLAND_DISPLAY` OR `XDG_SESSION_TYPE=wayland`. The docstring at lines 244-248 explicitly justifies the duplication: "The existing `_is_wayland_session` helper checks only `WAYLAND_DISPLAY` (its tests pin that contract), so we use a separate helper here for the broader detection." `_linux_copy` / `_linux_paste` (lines 326, 341) use the narrow version; `paste()` in manager.py (lines 679, 844) uses the broad version. A sway-from-TTY session (only `XDG_SESSION_TYPE=wayland` set) would route paste via `wtype` but copy via `pyperclip`.
**Root Cause:** Verified. Test contract pins the narrower behavior of `_is_wayland_session`; rather than parameterize, a second function was added.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/linux.py:138-152, 253-266, 326, 341`
- `voice_typer/server/clipboard/manager.py:679, 844`
**Fix:** Add `_is_wayland_session(broad: bool = False)` parameter. Tests that pin the narrow contract pass `broad=False` (the default). Callers that need the broader detection pass `broad=True`. Delete `_is_wayland_paste_session`, replace call sites with `_is_wayland_session(broad=True)`.
**Severity:** 🟢 Low

---

### [AC-122] — `clipboard_target_safety.py:906, 858, 959-967` `_PYATSPI_STATE_FOCUSED` global written by one function, read by another (hidden coupling)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard_target_safety.py:906` `_PYATSPI_STATE_FOCUSED: Any = None` is written ONLY inside `_is_password_field_linux` (lines 959-967) but READ inside `_find_focused_atspi_accessible` (line 858). If `_find_focused_atspi_accessible` is ever called before `_is_password_field_linux` has initialized the global, `_PYATSPI_STATE_FOCUSED` is `None` and `root_state.contains(None)` raises `TypeError`.
**Root Cause:** Verified. Initialization order is implicit.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard_target_safety.py:858, 906, 959-967`
**Fix:** Pass `state_focused` as a parameter to `_find_focused_atspi_accessible(desktop, state_focused, max_depth=10)`. Resolve `pyatspi.STATE_FOCUSED` once at the top of `_is_password_field_linux` and pass it down. Eliminates the global entirely.
**Severity:** 🟡 Medium

---

### [AC-124] — `clipboard_target_safety.py` 30+ ticket-ID inline comments obscure logic (CLIP-2 through CLIP-14, PLAT-001 through PLAT-027, etc.)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard_target_safety.py` (throughout) and `clipboard/manager.py` (throughout) reference 30+ ticket IDs: `CLIP-2, CLIP-3, CLIP-4, CLIP-5, CLIP-6, CLIP-7, CLIP-8, CLIP-10, CLIP-11, CLIP-12, CLIP-13, CLIP-14, PLAT-001, PLAT-006, PLAT-007, PLAT-013, PLAT-014, PLAT-027, PLAT-CLIPRACE, PLAT-CONTENT, PLAT-SECURE, PLAT-RDP, PLAT-STUCK, XPLAT-7, XPLAT-15, G4-H-05, G4-M-24, G4-M-25, DP1-DP8, ADR-0010, ADR-0020, EC-15, XV-103, PVT-G5-045, PVT-23, TASK-10, PERF-FIX-001, NEW-CQ-025, CR-3, CRIT-2, CRIT-3`. The `_is_password_field` docstring is 49 lines of ticket references before describing what the function does.
**Root Cause:** Verified. Tickets were filed per-defensive-layer during security hardening passes; each fix preserved the prior ticket's comment and added its own.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard_target_safety.py` (throughout)
- `voice_typer/server/clipboard/manager.py` (throughout)
**Fix:** Consolidate ticket references into a single "History" section at the top of each function docstring (one line per ticket: `CLIP-2: fail-closed on UIA exception`). Remove inline `# CLIP-N:` prefixes from the body. Keep only the "why" comments inline. Target: docstring ≤ ½ the function body length.
**Severity:** 🟢 Low

---

### [AC-126] — `service/__init__.py:488-521, 835-849` GDPR checkpoint logic duplicated between `delete_all_personal_data` and `export_gdpr_bundle`
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/service/__init__.py:488-521` (in `delete_all_personal_data`, 34 LOC) and `:835-849` (in `export_gdpr_bundle`, 15 LOC) both implement the same defensive "checkpoint the live HistoryDB writer" pattern. The two implementations have subtly different exception-handling structure: the `delete_all_personal_data` version wraps the outer `getattr`/`callable` in an extra `try/except Exception` that the `export_gdpr_bundle` version omits.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/__init__.py:488-521, 835-849`
**Fix:** Extract `_checkpoint_history_db(self, *, close: bool = False, tag: str = "SERVICE") -> None` private helper. Both methods call it. Pairs naturally with the PrivacyMixin extraction in AC-64.
**Severity:** 🟡 Medium

---

### [AC-127] — `permissions.py` 988-line spaghetti — 5 platform branches × 6 concerns interleaved
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/permissions.py` 988 lines interleaves 6 concerns: (1) Enums + constants, (2) Public probe API, (3) Native listener probe, (4) Retry timer mechanism + 3 globals, (5) Per-platform implementations (macOS, Linux, Windows), (6) Tray notification helper. Adding a new permission type requires editing 3-4 places in the same 988-line file. Adding a new platform (BSD) requires touching every `is_windows()/is_macos()/is_linux()` branch.
**Root Cause:** Verified. Organic growth: GAP-2/GAP-3 (initial), PVT-008 (native probe), PVT-011-i18n (notify), PVT-057 (result wrapper), PVT-058/PVT-059 (payload + udev fix), PVT-061 (microphone), RETRY-LOCK-FIX (retry rework).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/permissions.py` (entire file)
**Fix:** Split into `permissions/` package: `_state.py`, `_retry.py`, `_keyboard.py`, `_microphone.py`, `_payload.py`, `_native_probe.py`, `_macos.py`, `_linux.py`, `_notify.py`. `__init__.py` re-exports all public symbols. All function signatures unchanged.
**Severity:** 🔴 High

---

### [AC-128] — `credential_store.py` 1110-line spaghetti — 7 distinct concerns interleaved
**Status:** ❌ Not Fixed (deferred — credential_store.py grew to 1255 lines; extraction of one concern exceeds 10-min budget; needs dedicated multi-agent refactor wave)
**Description:** `voice_typer/server/credential_store.py` 1110 lines bundles 7 distinct concerns: (1) Constants & provider map, (2) Thread-local outcome recording / CR-94 IPC plumbing, (3) Defense-in-depth redaction (`_PATH_RE`, `_redact_sensitive`), (4) Keyring availability probing + 3 global caches, (5) Secret CRUD, (6) Plaintext fallback read/write, (7) Cross-process lock + migration logic. Migration alone is ~280 lines with 3 nested try/excepts and touches 4 of the 7 concerns.
**Root Cause:** Verified. Organic growth: RW-01 (CRUD + plaintext), then CR-94 (outcome plumbing), then XZ-SEC-02 (lock), then RACE-001/HIGH-13 (migration rework).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py` (entire file)
**Fix:** Split into `credential_store/` package: `_schema.py`, `_redact.py`, `_outcome.py`, `_backend.py`, `_plaintext.py`, `_crud.py`, `_migration.py`. `__init__.py` re-exports all public + private symbols used by tests. All function signatures unchanged.
**Severity:** 🔴 High

---

### [AC-129] — `level_monitor.py` 1229-line function-based god-object — 27 module-level globals, 18 functions, 0 classes, severe test-coupling
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/level_monitor.py` 1229 lines (GROWN from 1079 when S5-CR-30 was filed). 27 module-level globals, 18 top-level functions, 0 classes. Tests access 15+ private globals directly (`lm._test_mode`, `lm._test_chunks`, `lm._test_raw_chunks`, `lm._monitor_active`, `lm._monitor_stream`, `lm._monitor_level`, `lm._monitor_peak`, `lm._monitor_mic_id`, `lm._monitor_sample_rate`, `lm._level_processor`, `lm._dropped_level_chunks`, `lm._level_ring_buffer`, `lm._monitor_lock`, `lm._test_duration`, `lm._stop_level_worker()`, `lm._test_start_time`). Any split MUST preserve all these as accessible attributes.
**Root Cause:** Verified. Function-based module with shared mutable globals.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py` (entire file)
**Fix:** Convert `level_monitor.py` → `level_monitor/` package: `_state.py` (shared state object), `monitoring.py`, `test_recording.py`, `worker.py`. `__init__.py` re-exports all functions + provides module-level `__getattr__` proxy for test backward-compat (mirrors `recording/__init__.py:260-349` pattern).
**Severity:** 🔴 High

---

### [AC-130] — `ipc_server.py` 2546-line spaghetti — 8 distinct concerns + 14 instance attributes + 135-line `_COMMAND_REGISTRY` + 1946-line class body
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py` 2546 LOC (3.18× the 800-line threshold). 8 distinct concerns: (1) lifecycle, (2) TCP transport, (3) stdin transport, (4) heartbeat watchdog, (5) dispatcher, (6) output, (7) command routing table, (8) CLI entry. 15 mixin base classes + 1946-line class body + 14 instance attributes in `__init__` + 135-line `_COMMAND_REGISTRY` class-level data table + 6 transport/lifecycle/dispatcher methods.
**Root Cause:** Verified. ARCH-10 documents the mixin pattern was chosen to break the circular import. The 14 handler mixins solved the import problem but the IPC server class itself was never decomposed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (entire file)
**Fix:** Decompose `IPCServer` into transport / lifecycle / dispatcher / output mixins in `voice_typer/server/ipc/` subpackage. Final `IPCServer` class body is ~20 lines of class declaration + `_COMMAND_REGISTRY = COMMAND_REGISTRY` reference. Target final `ipc_server.py` = ~180-220 LOC.
**Severity:** 🔴 High

---

### [AC-131] — `config.py` 2030-line + `config_validators.py` 1102-line spaghetti
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/config.py` 2030 LOC (2.5× threshold) and `config_validators.py` 1102 LOC (1.4× threshold). `config.py` mixes 8 distinct concerns (defaults, schema, loading, saving, migration, validation entry, accessors, systemroot). `config_validators.py` mixes 6 concerns (constants, types, primitives, network, hotkey, allowlist, instances).
**Root Cause:** Verified. Each addition extended the file rather than spawning a module.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py` (entire file)
- `voice_typer/server/config_validators.py` (entire file)
**Fix:** Split `config.py` → `config/` package (11 modules, max ~490 LOC). Split `config_validators.py` → `config_validators/` package (8 modules, max ~325 LOC). All public API names preserved via `__init__.py` re-exports.
**Severity:** 🔴 High

---

### [AC-132] — `tray.py` 1267-line spaghetti — 16 distinct concerns
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray.py` 1267 lines (1.6× threshold). 16 concerns: lifecycle, state setters, pre-run queue, tooltip computation, Tauri publish, native apply, notification dispatch, menu cache, menu construction, page navigation, Electron window delegation, recording elapsed timer, CPU fallback event handler, platform detection, quit confirmation wrapper, backwards-compat aliases.
**Root Cause:** Verified. Each new feature added to tray.py rather than to one of the already-extracted satellite modules.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py` (entire file)
**Fix:** Split into `tray_lifecycle.py`, `tray_state.py`, `tray_publish.py`, `tray_notifications.py`, extend `tray_menu.py`, `tray_elapsed.py`, `tray_event_handlers.py`, `server_platform/wayland_sni.py`, extend `tray_window.py`. `tray.py` becomes ≤300 lines of wiring.
**Severity:** 🔴 High

---

### [AC-133] — `app.py` 1258-line spaghetti — `repaste_last` 74 LOC + `undo_last` 90 LOC + `quit_app` 65 LOC + `restart_app` 165 LOC + `_wait_for_relaunch_ack` 77 LOC inline
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/app.py` 1258 LOC. 5 inline business-logic blobs that should be extracted: `repaste_last` (74 LOC), `undo_last` (90 LOC), `_cancel_dictation` (25 LOC), `quit_app` (65 LOC), `restart_app` (165 LOC), `_wait_for_relaunch_ack` (77 LOC).
**Root Cause:** Verified. S2-CR-24 / EC-7 proposed `RepasteController` / `UndoController` / `RestartController` never extracted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:595-669, 670-760, 761-786, 850-915, 916-1081, 1082-1159`
**Fix:** Extract `repaste_controller.py` (~200 LOC: `repaste_last`, `undo_last`, `_cancel_dictation` + shared `_skip_due_to_hotkey_capture` helper). Extract `restart_controller.py` (~250 LOC: `restart_app`, `_wait_for_relaunch_ack`). Extend `shutdown_controller.py` with `quit_app` (+65 LOC). `app.py` becomes ≤300 LOC of delegates + `__init__` + re-exports.
**Severity:** 🔴 High

---

### [AC-134] — `dictation_pipeline.py` 1291-line + `transcription.py` 1190-line spaghetti
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py` 1291 LOC and `transcription.py` 1190 LOC both exceed the 800-line threshold. `dictation_pipeline.py` mixes 7 distinct responsibilities. `transcription.py` mixes 9 distinct responsibilities.
**Root Cause:** Verified. EC-28 previously concluded `dictation_pipeline.py` is "cohesive" — the MANDATORY instruction for this review overrides that assessment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py` (entire file)
- `voice_typer/server/transcription.py` (entire file)
**Fix:** Split `dictation_pipeline.py` → 8-file package (orchestrator, helpers, resource_probe, transcribe_step, text_steps, enhancement_steps, storage_step, paste_step). Split `transcription.py` → 9-file package (cuda_dll_paths, whisper_download, device_resolver, model_loader, transcribe, fallback, gpu_error_detection, _lock_helpers).
**Severity:** 🔴 High

---

### [AC-135] — `history_db.py` 1975-line spaghetti — 7 distinct concerns (schema, migrations, FTS5 search, retention, writer thread, query builders, lifecycle, diagnostics)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/history_db.py` 1975 LOC. 7+ distinct concerns. EC-28 previously classified as "large-but-cohesive (NOT monolith)" — the MANDATORY instruction for this review overrides that assessment.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py` (entire file)
**Fix:** Split into `history/` package: `_constants.py`, `_errors.py`, `schema.py`, `search.py`, `connections.py`, `writer.py`, `queries.py`, `writes.py`, `diagnostics.py`, `db.py` (HistoryDB class). `history_db.py` becomes ~20-line compat shim. All public API names preserved via re-exports.
**Severity:** 🔴 High

---

### [AC-136] — `model_manager.py` 1102 + `parakeet_engine.py` 1044 + `service/model.py` 1090 all exceed threshold
**Status:** ❌ Not Fixed
**Description:** All three files exceed 800 lines. `model_manager.py` mixes 6 concerns. `parakeet_engine.py` mixes 9 concerns. `service/model.py` mixes 9 concerns.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py` (entire file)
- `voice_typer/server/parakeet_engine.py` (entire file)
- `voice_typer/server/service/model.py` (entire file)
**Fix:** Split `model_manager.py` → 6-file package. Split `parakeet_engine.py` → 9-file package. Split `service/model.py` → 9-file package. All public API names preserved via facade pattern + `__init__.py` re-exports.
**Severity:** 🔴 High

---

### [AC-137] — `crash_handler.py` 1014 + `shutdown_controller.py` 1009 + `clipboard_target_safety.py` 1012 + `clipboard/manager.py` 1062 + `permissions.py` 988 + `text_cleanup.py` 982 all exceed threshold
**Status:** ❌ Not Fixed (deferred — 6-file multi-file refactor (crash_handler, shutdown_controller, clipboard_target_safety, clipboard/manager, permissions, text_cleanup — all >1000 LOC); partial extraction risks breaking _do_cleanup shutdown ordering)
**Description:** All six files exceed 800 lines. Each has a concrete split plan in the per-agent reports.
**Root Cause:** Verified. Organic growth across many sessions.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_handler.py` (entire file)
- `voice_typer/server/shutdown_controller.py` (entire file)
- `voice_typer/server/clipboard_target_safety.py` (entire file)
- `voice_typer/server/clipboard/manager.py` (entire file)
- `voice_typer/server/permissions.py` (entire file)
- `voice_typer/server/text_cleanup.py` (entire file)
**Fix:** Apply the split plans from AC-86 (crash_handler), AC-87 (shutdown_controller), AC-128 partial (clipboard_target_safety), AC-127 (permissions), AC-80+AC-81+AC-82 (text_cleanup).
**Severity:** 🔴 High

---

### [AC-138] — Rust host `sidecar/ws.rs` 997 + `sidecar/supervisor.rs` 952 + `commands/sidecar_cmds.rs` 926 + `commands/bubble.rs` 706 + `platform/logging.rs` 617 + `migrate.rs` 546 all exceed or approach threshold
**Status:** ❌ Not Fixed
**Description:** `ws.rs` 997 LOC (exceeds threshold), `supervisor.rs` 952 LOC (exceeds), `sidecar_cmds.rs` 926 LOC (exceeds), `bubble.rs` 706 LOC (approaches), `logging.rs` 617 LOC (approaches), `migrate.rs` 546 LOC (approaches). Each has a concrete split plan in the per-agent reports.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/migrate.rs`
**Fix:** Apply the split plans from AC-13/AC-98/AC-99/AC-100 (ws.rs), AC-95/AC-96/AC-97 (supervisor.rs), AC-30/AC-31/AC-32/AC-102 (sidecar_cmds.rs), AC-103 (bubble.rs), AC-39 (logging.rs), AC-35 (migrate.rs).
**Severity:** 🔴 High

---

### [AC-139] — TS client `bubble-window.ts` 598 + `logging.ts` 567 + `main-window.ts` 501 + `bootstrap.ts` 436 + `tcp-connect.ts` 321 all mix multiple concerns
**Status:** ❌ Not Fixed
**Description:** All five TS files mix multiple concerns and exceed (or approach) the 300-line wiring-only target. Each has a concrete split plan in the per-agent reports.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts`
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/bootstrap.ts`
- `voice_typer/client/src/main/python/tcp-connect.ts`**Fix:** Apply the split plans from AC-106 (bubble-window), AC-12+AC-117 (logging), AC-107 (main-window), AC-116 (bootstrap), AC-19 (tcp-connect).
**Severity:** 🟡 Medium

---

### [ER-1] — Electron main window gated on Python backend TCP auth (no UI for 2–5s at cold start)
**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Description:** The main BrowserWindow is created lazily by `tcp-connect.ts:133` ONLY AFTER the Python backend's TCP server accepts the connection AND the auth handshake completes. There is no splash window, no pre-connect `createWindows()` call, and no placeholder window. Cold-start first paint is gated end-to-end by Python spawn + torch import + TCP accept + auth round-trip — typically 2–5s on warm cache, 8–10s+ on cold cache / AV scan. During this entire window the user sees NO UI at all (no window, no tray icon yet because the tray is created by the Python backend, no taskbar entry).
**Root Cause:** `createWindows()` is invoked from exactly two production sites: `tcp-connect.ts:133` (initial connect) and `index.ts:204` (macOS `activate` only). The renderer even ships a dedicated "connecting" spinner UI (`App.tsx:478-499`) — but on cold start that UI can never appear because the BrowserWindow does not exist yet. The 60s `TCP_STARTUP_TIMEOUT_MS` means a hung Python import shows zero feedback for up to a minute.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts`
- `voice_typer/client/src/main/index.ts`
- `voice_typer/client/src/main/python/start-python.ts`**Fix:** In `app.whenReady().then(...)` after `bootstrapRuntime()`, call `createWindows(/* forceShow */ true)` immediately (or a lightweight `createSplashWindow()` that does not load the React bundle), so the user sees a window within ~100–200ms of process start. The existing `connectionStatus === "connecting"` spinner in App.tsx then actually has a chance to render. `tcp-connect.ts:133`'s `createWindows()` call becomes `showMainWindow()` instead of re-creating. The `state.pythonExitedEarly` branch in start-python.ts:117-138 already handles `state.mainWindow` being non-null (calls `.destroy()`), so the early-exit dialog path stays correct.

---

### [ER-2] — DeepFilterNet backend unimplemented — `noisy_room` preset delivers zero neural noise suppression
**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Description:** `noise_suppressor.py:124-139` `process()` calls `_process_rnnoise` for the rnnoise backend, but for DeepFilterNet and Speex backends it sets `is_degraded=True` and returns the audio unchanged (passthrough). The `PRESET_NOISY_ROOM` preset at `audio_presets.py:50-58` explicitly selects `"deepfilternet"` for the noisiest environment. Users in the noisiest environments (the exact use case this preset targets — keyboard/fan/HVAC) get ZERO neural noise suppression. ASR accuracy in these environments degrades severely because the very feature advertised is a no-op.
**Root Cause:** Verified — `_init_deepfilternet()` only stores the imported functions in a dict; `process()` never calls `enhance()`/`init_df`. The preset explicitly selects "deepfilternet" for the noisiest environment, but the selected backend does nothing.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py`
- `voice_typer/server/audio_presets.py`**Fix:** In `_init_deepfilternet()` immediately downgrade to rnnoise (like the `ImportError` path does) so the user at least gets RNNoise-level suppression instead of nothing. OR: in `PRESET_NOISY_ROOM` fall back to `"rnnoise"` until DeepFilterNet is implemented. The full DeepFilterNet wiring (frame buffering, `enhance()` call) is a separate, larger feature task — out of scope for this session.

---

### [ER-3] — Zombie audio worker thread leak after stop() join timeout (Critical restart-cycle bug)
**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Description:** `recorder.py:1889-1907` `_stop_audio_worker` joins the worker thread with a 2s timeout. After the join, the code unconditionally clears `_worker_stop_event` AND sets `_worker_thread = None` — even if the thread is still alive. The code's own comment at line 1886-1888 says "A stale worker is harmless because the stop event is set; it will exit on its next iteration boundary." But the very next lines CLEAR the stop event, contradicting the intent. If `_process_audio_chunk` took >2s (cold cache, GC pause, CPU contention, Silero VAD first-inference), the worker does NOT exit and loops back to process more chunks. On the next `start()`, `_start_audio_worker` (line 1828) sees `_worker_thread is None` (set to None at line 1896) → bypasses the `is_alive()` guard → spawns a SECOND worker. Two workers popping from the same deque → race condition, corrupted audio chunks, potential crash. After N such cycles, N zombie workers accumulate, each calling `_process_audio_chunk` on every chunk → exponential CPU load. Breaks within 10-20 cycles under load.
**Root Cause:** Verified — stop event is cleared unconditionally after join timeout, contradicting the code's own comment. Same anti-pattern is copy-pasted across `_stop_device_health_checker` (device_manager.py:251-271), `_stop_event_worker` (recorder.py:1995-2007), and partially in `_stop_watchdog_thread` (recording_controller.py:857-926).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py` (lines 1828, 1889-1907, 1995-2007)
- `voice_typer/server/recording/device_manager.py` (lines 241, 251-271, 284)
- `voice_typer/server/recording_controller.py` (lines 857-926)**Fix:** Only clear the stop event AND null the thread reference if the thread actually exited (inside the `else` branch of `if thread.is_alive()`). Leave the stop event SET if the thread is still alive so it exits on its next iteration check. The next `_start_*` call already calls `clear()` before starting a new thread, so the next cycle is clean. Do NOT set `_worker_thread = None` if the thread is still alive — let the existing `is_alive()` guard prevent spawning a duplicate. Apply the same fix to `_stop_device_health_checker`, `_stop_event_worker`, and `_stop_watchdog_thread`.

---

### [ER-4] — Sequential shutdown cleanup chain (~80s worst case, many independent steps serial)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `shutdown_controller.py:262-661` `_do_cleanup` invokes ~15+ independent teardown steps SEQUENTIALLY, each wrapped in `_run_with_timeout(..., timeout=5.0)` (or 10s for history_db.flush). Step list: `ipc_server.stop` → `ws_pool.shutdown` → `_cancel_pending_timers` → `_stop_watchdog_thread` → `session.cancel` → `recorder.stop(5s)` → `recorder.discard` (only if stop fails) → `recorder.shutdown_mic_watcher` → `level_monitor.stop_monitoring` → `restore_volume` → `transcription_thread.join(3s)` → `hotkey_backend.stop(5s)` → `esc_backend.stop` → `repaste_backend.stop` → `crash_recovery.flush(2s)+shutdown(5s)` → `history_db.flush(10s)+close(5s)` → `waveform_wiring.stop` → `sounddevice.stop` → `electron_launcher.terminate_electron` → `_clear_backend_pid_file` → `CloseHandle` → `_close_devnull_files` → `event_bus.shutdown(5s)` → `tray.stop(5s)`.
Worst-case cumulative timeout if multiple subsystems are slow: ~80-90s. Normal-case sum: ~500ms-1s. A single hung subsystem (e.g. PortAudio `stop()` on a disconnected WASAPI device, or pynput Win32 UnregisterHotKey blocked on OS call) delays the ENTIRE shutdown by its full 5s timeout before the next step even starts.
**Root Cause:** Verified — no two independent steps are parallelized. Many of these are genuinely independent (recorder.stop, hotkeys.stop, history_db.flush, crash_recovery.flush, waveform_wiring.stop, sounddevice.stop have no ordering dependency on each other) but run strictly in series.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`**Fix:** Group independent teardowns into parallel batches via `ThreadPoolExecutor`: Batch A = {recorder.stop, hotkey backends, history_db.flush, crash_recovery.flush, waveform_wiring.stop, sounddevice.stop}; Batch B (after A) = {tray.stop, event_bus.shutdown, ipc_server.stop}. Preserve only documented ordering constraints (tray.stop MUST be last per PVT-G5-003; ipc_server.stop MUST be early per PVT-G5-004).

---

### [ER-5] — restart_app 2s blocking relaunch_ack wait on restart path
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `app.py:1019` `_wait_for_relaunch_ack` blocks up to 2.0s on the calling thread (typically the pystray tray-menu worker thread) waiting for the new Electron process to ack the relaunch. The fallback `time.sleep(0.3)` paths also block synchronously. Combined with the sequential `_do_cleanup` (which can itself take 1-2s normally), total restart-to-old-process-exit can be 3-4s. The new process must then redo the entire startup path.
**Root Cause:** Verified — the ack wait is unconditional and always blocks up to 2s on the calling thread. The Electron `pythonProcess.on("exit")` fallback (documented in the restart_app docstring) already covers the lost-ack case.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`**Fix:** Reduce the default timeout to 0.3s (the fallback sleep value) and let the new process's startup sequence handle any race with the old socket close. Or fire-and-forget the relaunch_app event and proceed immediately to `_do_cleanup`.

---

### [ER-6] — macOS osascript 3s accessibility fallback on startup hot path
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `startup_sequence.py:374-380` `StartupSequence.run` invokes a synchronous `osascript` subprocess with a 3s timeout as a fallback when `ctypes.cdll.LoadLibrary(".../ApplicationServices")` fails. This is on the critical startup thread (runs before hotkey registration at line 528 and before parallel prewarm/mic work at line 516). The osascript path also synthesizes a real keystroke via System Events, which is invasive (focuses the frontmost app).
**Root Cause:** Verified — reached whenever ctypes ApplicationServices load fails (stripped-down macOS installs, code-signed bundles with restricted dyld env, CI runners).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/startup_sequence.py`**Fix:** Replace the osascript fallback with a pure `ctypes` probe of `AXIsProcessTrustedWithOptions` (passing `kAXTrustedCheckOptionPrompt=False`) — or treat ctypes-load failure as "permission not granted" (False) and let the periodic A11yPulse (already started at line 406) detect the grant within 60s. Drop the osascript path entirely. **VALIDATE ON MACOS HOST** — verify `AXIsProcessTrustedWithOptions` returns the correct value on real macOS.

---

### [ER-7] — Per-sample Python loops in audio filters (compressor/limiter/noise_gate/equalizer) burn 6–10% CPU on hot path
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `compressor.py:74-94`, `limiter.py:64-82`, `noise_gate.py:100-130`, `equalizer.py:78-97` all use per-sample `for i in range(n)` Python loops on every 512-sample chunk. Each iteration calls `mul_to_db` (math.log10) and `db_to_mul` (10.0 ** (db/20.0)) — two Python function calls + two C math calls per sample. With 4 filters active × 512 samples × ~10 Python ops × 16 Hz ≈ 327k Python ops/s + ~65k log10/pow calls/s. Empirically ~6–10% CPU on the audio worker thread when the full chain is active.
**Root Cause:** Verified — direct port of OBS C code, never vectorized. The gain computation (log10/pow) is vectorizable once the envelope is computed. Equalizer's 3-band one-pole + delay line is fully vectorizable with `scipy.signal.lfilter`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/compressor.py`
- `voice_typer/server/audio_filters/limiter.py`
- `voice_typer/server/audio_filters/noise_gate.py`
- `voice_typer/server/audio_filters/equalizer.py`
**Fix:** Vectorize envelope follower with `np.maximum` + `np.where` for attack/release split (single pass); compute gain in dB domain with `np.log10`/`np.power` on the whole envelope vector at once. State carry across calls: persist `self._envelope` as a scalar. For Compressor/Limiter/NoiseGate this is a ~20–50× speedup. Equalizer: switch to `scipy.signal.lfilter` (mirror highpass.py pattern).

---

### [ER-8] — Double VAD resample: chain already resamples to 16k, VAD path re-resamples (CPU waste + correctness bug)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `recorder.py:2516-2535` — after `self._audio_processor.process_chunk(...)` the chain has ALREADY resampled `filtered` from native (e.g. 48 kHz) to the chain rate (16 kHz). But the VAD branch then checks `if self._effective_sr not in (8000, 16000)` and re-resamples `filtered` AGAIN using the native→16k ratio: `vad_audio = resample_poly(filtered.ravel(), up, down)`. With `_effective_sr=48000`, `up=1, down=3` → `filtered` (already 16 k samples/s, ~512 samples) is decimated 3:1 to ~170 samples and passed to Silero as "16 kHz" audio. Both a CPU waste (a second `resample_poly` call per chunk, ~0.5–2 ms each) AND a correctness bug (Silero sees 1/3-length audio, biased VAD probabilities).
**Root Cause:** Verified — code predates the chain resample added by CRIT-6 (`audio_processor.py:195`). The VAD path should consume `filtered` directly when a processor is active, since the chain guarantees 16 kHz output. Currently `_effective_sr` is used as the source rate for VAD resample even when `filtered` is at chain rate. Same root cause (`_buffer_sr` never set in production — verified via repo-wide grep) drives ER-9 and ER-D3.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/_recorder_split.py`
- `voice_typer/server/audio_processor.py`**Fix:** Set `self._buffer_sr` in `_process_audio_chunk` when AudioProcessor resamples (or to `_effective_sr` when no processor), so `stop()`/`snapshot()`/VAD skip the resample when `_buffer_sr == target_sr`. This eliminates the ~76 MB unnecessary resample intermediate in the common 48 kHz-device case AND the per-chunk double-resample.

---

### [ER-9] — EQ `* 0.5` factor causes -6 dB attenuation at unity gain (High — recovers 6 dB of signal level)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `equalizer.py:97` `output[i] = (low_s * low_gain + mid * mid_gain + high * high_gain) * 0.5`. At unity (low_db=mid_db=high_db=0): low_gain=mid_gain=high_gain=1.0, and `low_s + mid + high = d3` (3-sample delayed input — verified by substitution), so `output = d3 * 0.5` → -6.02 dB attenuation at UNITY gain. With default preset (-3/+3/+2 dB), net output ≈ -3 to -5 dB. Combined with compressor (which only attenuates, never boosts) and a limiter ceiling at -6 dB, the entire default `auto` chain delivers audio to ASR that is 5-14 dB quieter than the microphone input. Whisper/faster-whisper tolerate quiet audio but lose effective SNR; in noisy environments this directly increases Word Error Rate. The limiter at -6 dB essentially never engages.
**Root Cause:** Suspected porting artifact — the `* 0.5` may have been copied from a reference that compensated elsewhere. In this codebase `db_to_mul(0)=1.0` (verified in base.py:17-25), so the 0.5 is an uncompensated 6 dB loss.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/equalizer.py`
- `voice_typer/server/audio_filters/compressor.py` (no makeup gain — companion issue)
- `voice_typer/server/audio_chain_builder.py`
**Fix:** Remove the `* 0.5` factor (the band-sum math holds at unity — `low + mid + high = d3`). Add a unit test asserting `EQ(0,0,0).process(tone) ≈ tone` (within IIR transient). Also add `makeup_gain_db` to the compressor OR raise `output_gain_db` default to +6 dB to compensate for the historical loss. Verify limiter ceiling still makes sense after makeup.

---

### [ER-10] — Noise gate thresholds hardcoded, non-adaptive (clips speech onsets for quiet-mic users)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `noise_gate.py:36-46` — `open_threshold_db=-26.0` (0.05 linear), `close_threshold_db=-32.0` (0.025 linear). The state machine at lines 113-117: `if level > open_thr: is_open = True; elif level < close_thr and is_open: is_open = False`. Thresholds are absolute dBFS with no calibration step. A quiet USB mic producing speech at -30 dBFS will never open the gate (speech < 0.05 linear), silently dropping words. A sensitive mic in a noisy room producing ambient at -25 dBFS will never close the gate, letting noise through. The `auto` preset enables this gate by default, so the issue affects 90% of users.
**Root Cause:** Verified — no noise-floor estimation, no auto-open based on SNR, no per-session calibration. The RMS path in VAD calibrates; the gate does not.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_gate.py`
- `voice_typer/server/audio_chain_builder.py`
**Fix:** Add an adaptive calibration mode: sample the first N ms of ambient audio to estimate noise floor, then set `open_threshold = noise_floor + 6dB` and `close_threshold = noise_floor + 0dB`. Expose a `noise_filter_gate_adaptive` config flag. Until implemented, consider disabling the gate in the `auto` preset (it overlaps with RNNoise anyway).

---

### [ER-11] — Audio sample-rate mistuning: `input_sample_rate` is opt-in, filters silently mistuned if caller omits it
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `audio_processor.py:154-219` `process_chunk` — resampling only happens IF caller passes `input_sample_rate` parameter. If `input_sample_rate is None` (the default), NO resampling occurs and the chunk is fed directly to filters tuned at 16 kHz. A 48 kHz device chunk would be processed by an 80 Hz high-pass that actually cuts at 240 Hz, an 800 Hz EQ crossover that actually crosses at 2.4 kHz, etc. The `except Exception` fallback logs at DEBUG only — a silent mistune that operators won't see.
**Root Cause:** Verified — the resample guard is opt-in. If any caller (level_monitor, recording callback, future IPC path) omits the argument, filters run silently mistuned. Mistuned filters degrade ASR accuracy in specific ways: highpass at 240 Hz removes male fundamentals (80-150 Hz); EQ crossovers shift 3×, removing the presence boost that aids consonant recognition; compressor attack/release ballistics scale with sample rate (6 ms becomes 2 ms → pumping).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_processor.py`
**Fix:** Make `input_sample_rate` a required parameter (breaking API change but eliminates the silent-failure mode), OR default `input_sample_rate` to `self._sample_rate` and log a WARNING if the caller didn't pass it explicitly. At minimum, raise the resample-failure log from DEBUG to WARNING.

---

### [ER-12] — Qwen GPU→CPU fallback leaves float16 dtype on CPU (effectively broken fallback)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `qwen_engine.py:634-640` `transcribe_with_fallback` — on CUDA error, calls `self._model.to("cpu")` but dtype stays float16. At load time (qwen_engine.py:286) the model is converted to `torch.float16` for GPU. On CPU, float16 kernels are unsupported or pathologically slow. Compare `parakeet_engine.py:873` which explicitly fixes this: `self._model.to(device="cpu", dtype=self._torch.float32)`. The parakeet comment literally warns "float16 kernels are unsupported or pathologically slow on CPU, so the 'fallback' was effectively unusable." The same float16-on-CPU bug Parakeet fixed was never ported to the Qwen engine.
**Root Cause:** Verified — after any CUDA hiccup (driver glitch, transient OOM), Qwen's "CPU fallback" either crashes with a `NotImplementedError` or runs 10-50× slower than int8 Whisper — effectively no usable fallback. Users on Qwen backend see a hard failure instead of graceful degradation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/qwen_engine.py`
**Fix:** Mirror `parakeet_engine.py:867-873` — call `self._model.to(device="cpu", dtype=torch.float32)` in the fallback branch. **VALIDATE ON CUDA HOST** — verify the fallback actually runs on a real CUDA machine after a forced OOM.

---

### [ER-13] — VAD `compute_vad_prob` runs sequential torch inference on audio capture thread
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `vad.py:192-211` — for `n > expected` (1136-sample WASAPI chunk), the function loops 2× `model()` calls inside a `for start in range(0, n - expected + 1, expected)` loop. Each Silero inference is 3-15 ms on CPU. Called from `recorder.py:2535` inside the per-chunk audio callback: `vad_prob = compute_vad_prob(vad_audio, vad_sr)`. At 16-30 Hz callback rate that's 96-900 ms/sec of torch inference running on the audio capture thread (the same thread that must service PortAudio buffers or drop frames / trigger xruns).
**Root Cause:** Verified — sequential synchronous torch inference in the audio callback path, multiplied by the sub-chunk loop.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vad.py`
**Fix:** (a) For `n > expected`, run only ONE inference on `audio_tensor[:expected]` (drop the multi-sub-chunk loop) — the prior "max over sub-chunks" optimization is not worth blocking the audio thread; OR (b) batch sub-chunks as a single 2D tensor and call `model()` once with batch dim; OR (c) move compute_vad_prob to a dedicated VAD worker thread fed by a queue (decouple from capture — larger refactor).

---

### [ER-14] — Level monitor runs RNNoise on every chunk with no idle-timeout (pegs a core when bubble is hidden)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `level_monitor.py:299-453` `start_monitoring` opens `sd.InputStream(blocksize=512, callback=callback)` and sets `_monitor_active = True` with NO auto-idle/auto-stop logic. Module docstring (line 4): "Continuous level monitoring — computes RMS/peak on every chunk so the frontend can show a live level bar AT ALL TIMES." PortAudio callback fires at device block rate: ~10.7 ms @ 48 kHz. Worker `_process_level_chunk` runs the FULL filter chain on every chunk when `_level_processor` is set (line 1178-1184): `processor.process_chunk(...)` then `np.abs` + `np.sqrt(np.mean(...))`. Docstring (line 22-30) admits RNNoise = "5–50 ms per chunk on CPU". At 48 kHz / 512 samples (10.7 ms/chunk) with RNNoise enabled (~20 ms/chunk), the worker cannot keep up → ring-buffer overflow. If the frontend starts it for a tray-bubble level bar and the bubble hides without calling `level_monitor_stop`, the audio pipeline keeps churning indefinitely.
**Root Cause:** Verified — module design is "start-on-demand, run-forever" with no idle-timeout / UI-visibility gate. The expensive RNNoise path is applied to the live level bar (not just dictation capture).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py`
**Fix:** (a) Add an idle-timeout in level_monitor: if no IPC `get_level` poll has been received in N seconds (e.g. 5s), auto-stop the stream and re-start on next `get_level`/`start_monitoring`. (b) Do NOT run RNNoise on the level-bar path — RMS/peak on raw audio is sufficient for a visual bar; reserve the filter chain for `start_test_recording`. (c) Have the frontend call `level_monitor_stop` when the tray bubble hides, not just on app quit.

---

### [ER-15] — Prewarm pipeline is power-state-unaware (drains battery at login on laptops)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `prewarm/pipeline.py:70-148` `run()` guards on three things only: `_fast_startup_enabled()` (config flag), `_already_warmed()` (boot sentinel), and `_free_ram_mb()` (RAM budget). There is NO power-state check. On a laptop booted on battery, the prewarm scheduled task fires at logon and `_run_warming_pipeline()` sequentially reads ~4.5 GB of torch+transformers package files + ~2.4 GB of Parakeet weights off disk. `_lower_io_priority()` lowers CPU/IO priority but does NOT skip the work on battery. Estimated 2-3 Wh drain per prewarm. For a user who reboots/logs in 3-4×/day on battery, that is ~10 Wh/day wasted.
**Root Cause:** Verified — no power-state guard exists. `psutil` is already a dependency.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/pipeline.py`
- `voice_typer/server/prewarm/logging_setup.py`
**Fix:** Add a `_on_battery_and_low_charge()` guard using `psutil.sensors_battery()` (returns `(percent, secs_left, power_plugged)`). In `run()`, after the RAM guard and before `_lower_io_priority()`, skip prewarm with a new exit code (e.g. `EXIT_ON_BATTERY=50`) when `power_plugged is False and percent < 60`. Log the skip so the user can diagnose. Defer to the next AC-plug event.

---

### [ER-16] — Device disconnect handler leaves recording silent after failed default-device restart
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `recorder.py:729-827` `_handle_device_disconnect` — on first detection, `_device_disconnected = True` is set, then a single restart attempt with the default device is tried. If that fails (`except Exception`), the flag is NOT cleared — no retry is scheduled. Every subsequent zero-fill / health-check is suppressed while the flag stays True (line 2258-2259 + device_manager.py:287-288). The `_max_disconnect_retries` counter is misleadingly named — it counts separate disconnect EVENTS, not retry attempts. There is no periodic retry loop. USB/BT mic unplugged + default device unavailable → recording appears "active" but captures silence for the full silence-auto-stop window (typically 30-60s), then stops with a misleading "silence detected" notification instead of "device gone". Even if the user plugs in a new mic, the recorder does NOT auto-recover.
**Root Cause:** Verified — flag stays True on restart failure, blocking future detection and the health-checker probe.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/device_manager.py`
**Fix:** In the `except Exception` branch of `_handle_device_disconnect`, do NOT leave `_device_disconnected=True`. Either (a) clear the flag so the next 30s health-check cycle re-triggers the handler, or (b) add an explicit backoff-retry loop inside the handler (e.g. 3 attempts with 2s/5s/10s sleep) before giving up. When max retries is reached, fire a dedicated `on_device_lost` callback (not `on_silence_auto_stop`) so the UI shows "Microphone disconnected" rather than "silence detected".

---

### [ER-17] — Tauri host dev-mode shutdown always sleeps full 2s (block_on event-loop stall)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `src-tauri/src/main.rs:437-443` + `state.rs:339-355` — `RunEvent::Exit` callback runs `tauri::async_runtime::block_on(async move { timeout(3000ms, shutdown_sidecar_for_exit) })`. This `block_on` on the Tauri run-callback thread blocks the entire event loop. The outer 3s timeout is the ceiling. For dev mode (`VOICE_TYPER_SIDECAR_DEV=1`), `child_exit_rx` is `None` (spawn.rs:36), so the else-branch sleeps the full 2s unconditionally — even if the sidecar already exited. `kill_tree` then adds ~200ms grace sleep + per-descendant `pgrep`/`kill` syscall latency on top. User sees a non-responsive window / lingering Dock icon. On macOS, the system "force quit" dialog can appear if >~3s.
**Root Cause:** Verified — dev-mode path always sleeps 2s; release path correctly exits early on `Terminated`.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/state.rs`
**Fix:** (1) Dev-mode path: replace `tokio::time::sleep(deadline)` with a poll loop on `child.try_wait()` (requires storing the dev-mode `Child` handle). Falls back to the deadline as upper bound. (2) Move the entire `shutdown_sidecar_for_exit` off the event-loop thread: `std::thread::spawn(move || block_on(...))` and let the Exit callback return immediately. **VALIDATE ON MACOS HOST** — verify shutdown is fast on a real macOS machine in both dev and release modes.

---

### [ER-18] — Audio buffer 2×N duplication during recording (deque + snapshot cache, ~114 MB sustained at 15 min)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `recorder.py:319, 331-332` + `_recorder_split.py:176, 207-209` — `_cached_resampled` and `_cached_no_resample_arr` caches live for the entire recording session. The snapshot cache exists to avoid O(n) re-concatenation on every 4 Hz poll, but it duplicates the entire recording in memory. The deque stores individual chunks; the cache stores a contiguous concatenation/resampled copy of the same data. Sustained RAM during recording (2×N): 16 kHz device with AudioProcessor active = ~114 MB; 48 kHz device, AudioProcessor=None = ~229 MB.
**Root Cause:** Verified — cache duplication verified; 2×N sustained verified; allocation churn over a 15-min session = ~100 GB of allocations (3600 rebuilds × avg 28 MB).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/_recorder_split.py`
**Fix:** Replace the deque-of-chunks with a single pre-allocated growable ndarray (ring buffer with geometric capacity doubling). `snapshot()` returns a view into the ring buffer (O(1), zero allocation, zero duplication). This halves sustained RAM from 2×N to 1×N. The deque's SPSC atomicity is preserved by using a single-producer/single-consumer ring index pair. (Smaller interim fix: invalidate the cache more aggressively — only keep the cache warm for ~5s of recent audio for the live waveform, not the entire session.)

---

### [ER-19] — stop() peak memory ~573 MB worst case (deque+concat double-buffer + unnecessary resample)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `recorder.py:2742-2791` `stop()` does `audio = np.concatenate(list(self._buffer), axis=0).reshape(-1)` — materializes a full second copy BEFORE the deque is drained. The background secure-clear worker then keeps the old deque alive for 30-283 ms while it zeroes chunks. Then `stop()` uses `_effective_sr` (device native rate, e.g. 48 kHz) instead of the actual buffer rate. When AudioProcessor is active it already resamples chunks to 16 kHz before append, so `_buffer` holds 16 kHz audio — but `stop()` resamples it again as if it were 48 kHz (downsampling 16 k→5.3 k), creating a ~57 MB scipy upfirdn intermediate + 19 MB output that are both unnecessary. Worst case (48 kHz device, AudioProcessor=None): ~573 MB (deque 172 + concat 172 + upfirdn intermediate 172 + output 57). Exceeds 500 MB Critical threshold.
**Root Cause:** (1) `np.concatenate` materializes a full second copy before the deque is drained; (2) `_buffer_sr` is never set in production (only `_recorder_split.py:267` assigns it, to `None`), so `stop()` always uses `_effective_sr`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/_recorder_split.py`
**Fix:** (1) Drain the deque chunk-by-chunk into a pre-allocated destination ndarray (np.empty), zeroing + popping each chunk after copy. Peak drops from 2×N to 1×N+1chunk. (2) Set `self._buffer_sr` in `_process_audio_chunk` when AudioProcessor resamples (or to `_effective_sr` when no processor), so `stop()`/`snapshot()` skip the resample when `_buffer_sr == target_sr`. (3) For recordings exceeding 60s, stream the resample in 5s blocks instead of one full-array call.

---

### [ER-20] — i18n `t()` builds new RegExp per param per call (dozens per Dashboard render)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `i18n/i18n.ts:340-345` and `:482-484` — `t()` and `tChoice()` do `for (const [k, v] of Object.entries(params)) { result = result.replace(new RegExp(\`\\{${k}\\}\`, "g"), v); }`. `t()` is called dozens of times per render across Dashboard/Home/History/Settings (each chart bar, stat card, button label). Each call with params mints N `new RegExp(...)` instances — regex compilation is one of the costlier string ops.
**Root Cause:** Verified — no caching layer between hot translate path and RegExp constructor.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/i18n.ts`
**Fix:** Add module-level `Map<string, RegExp>` cache keyed by placeholder name; reuse compiled regexes across `t()`/`tChoice()` calls.

---

### [ER-21] — Settings.tsx effect runs after EVERY render (missing dep array); sectionProps fresh object literal every render
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `Settings.tsx:234-237` — `useEffect(() => { ... setHasAnyVisibleRow(...) })` has NO dependency array — runs after EVERY render. Settings page has frequent re-renders (saving/pending/saved indicator flips, config_changed pushes, debounced set_config). Compounded by `Settings.tsx:278-283, 373-415` — `sectionProps` is a NEW object literal every render → every spread creates new prop identities → every SettingsSection component re-renders on every SettingsPage render even if `config`/`updateConfig` are unchanged.
**Root Cause:** Verified — missing dep array; fresh object literal in render body; no useMemo; child components not memoized.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Settings.tsx`
**Fix:** (a) Add `[settingsFilter]` deps to the effect, OR derive `hasAnyVisibleRow` synchronously during render. (b) `useMemo` the sectionProps object with `[config, updateConfig, updateConfigDebounced, _filter_settings]` deps. (c) Wrap each `*SettingsSection` component in `React.memo`.

---

### [ER-22] — Bubble events broadcast to main window renderer (contradicts SEC-017 comment, 30-60 Hz IPC churn)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `handle-message.ts:84-143` — the `bubble_show`/`bubble_hide`/`bubble_set_state`/`bubble_level`/`bubble_config` branches each call `state.bubbleWindow?.webContents.send(...)` AND then fall through to the unconditional broadcast at line 141-143: `if (state.mainWindow && !state.mainWindow.isDestroyed()) { state.mainWindow.webContents.send("python-event", msg); }`. So EVERY `bubble_level` audio frame (Python pushes ~30-60 Hz while recording) is IPC-marshalled across to the main window renderer. The module's own comment (line 65-67) says "Bubble events go ONLY to the bubble window (not the main app) so the floating overlay updates without re-rendering the sidebar" — but the implementation contradicts the comment.
**Root Cause:** Verified — bubble events are broadcast to main window contrary to the SEC-017 comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/handle-message.ts`
**Fix:** After the if-else chain, only forward to main window if the event was NOT a bubble-only event: `const bubbleOnlyTypes = new Set(["bubble_show","bubble_hide","bubble_set_state","bubble_level","bubble_config"]); if (!bubbleOnlyTypes.has(msg.type as string) && state.mainWindow && !state.mainWindow.isDestroyed()) { state.mainWindow.webContents.send("python-event", msg); }`

---

### [ER-23] — Intl formatters constructed fresh on every call (Dashboard/History/Models)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `lib/format.ts` lines 135-173, 194-214, 260-300, 320-326, 364-412, 431-444, 466-487 — every exported formatter constructs a fresh `new Intl.NumberFormat(...)` / `Intl.DateTimeFormat(...)` / `Intl.RelativeTimeFormat(...)` / `Intl.DurationFormat(...)` on every call. `Intl.NumberFormat` construction is ~5-10× slower than `.format()`. Dashboard.tsx calls `formatBytes`/`compactNumber`/`toLocaleString(getLocale())` ~6-10 times per render; History.tsx calls `getLocale()` + `toLocaleString()` repeatedly; About.tsx and Models.tsx call formatters in lists.
**Root Cause:** Verified — no caching; fresh constructor on every call.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/format.ts`
**Fix:** Module-level `Map<string, Intl.NumberFormat>` (and equivalents for DateTimeFormat/RelativeTimeFormat/DurationFormat); keyed by locale+options. Cache hit is a Map lookup, ~50× faster than constructor.

---

### [ER-24] — Redundant `Arc<Arc<Mutex>>` in PendingMap (self-documented TODO F-S1)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Description:** `state.rs:49` — `pub(crate) type PendingMap = Arc<AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>>;`. `SidecarState` is always shared as `Arc<SidecarState>` (Tauri managed state). The inner `Arc` is a redundant outer indirection: every dispatch locks `Arc<AsyncMutex<…>>` (Arc bump on `.lock().await` caller site, plus internal Arc refcount bump inside AsyncMutex). The double-Arc is a known code smell and is already self-documented as TODO PVT-25 in `bubble.rs:20-28`.
**Root Cause:** Verified — `Arc<AsyncMutex<T>>` is redundant when the parent `SidecarState` is already `Arc`'d.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/supervisor.rs` (test helper)
**Fix:** Drop the inner `Arc` wrapper: `type PendingMap = AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>;`. Initialize: `pending: AsyncMutex::new(HashMap::new())`. Update all call sites — `state.pending.lock().await` is already the call shape. Update the `SidecarState` initializer in `main.rs:171` + `supervisor.rs:641` test helper.

---

### MEDIUM Findings

### [ER-25] — Renderer bundle: no route-level code splitting (all 10 pages statically imported by App.tsx)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `App.tsx:18-27` — all 10 page components (and their transitive deps — ModelsPage in particular pulls model-management + HuggingFace import dialogs) are statically imported by App.tsx and bundled into the single initial chunk that must be parsed + evaluated before React mounts. No `React.lazy()` / `Suspense` / route-level code splitting is used anywhere.
**Root Cause:** Verified — no code splitting.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx`
**Fix:** Convert page imports to `React.lazy(() => import("@/pages/..."))` for secondary routes (History, Templates, Vocabulary, Models, Microphone, Analytics, Settings, About, Onboarding), keep `Home` eager (it's the default landing page), wrap `renderPage()`'s switch in `<Suspense fallback={<Spinner/>}>`. Vite will automatically produce per-route chunks.

---

### [ER-26] — Dev-mode restart path duplicates SIGTERM+SIGKILL logic and races on TCP port
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `relaunch-app.ts:50-141` — the dev-mode branch kills the old Python with SIGTERM, schedules a 3s SIGKILL fallback, then immediately calls `startPython()` to spawn a fresh backend. The new backend cannot bind `IPC_PORT` (9876) until the old process has actually released the listening socket — which under SIGTERM may take up to 3s. So in practice the new `tcpConnect()` retry loop hammers a port that the dying process still holds, and the user perceives a multi-second "Restarting…" hang. The killTimer in `relaunch-app.ts` and the killTimer in `stop-python.ts` are also duplicated logic — drift risk.
**Root Cause:** Verified — `startPython()` is called BEFORE the old proc has actually exited.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/relaunch-app.ts`
- `voice_typer/client/src/main/python/stop-python.ts`
**Fix:** In the dev branch, after `proc.kill("SIGTERM")`, await the `exit` event (with a 3s timeout `Promise.race`) BEFORE calling `startPython()`. Alternatively, call the shared `stopPython()` (with idempotency flags reset) instead of duplicating the kill logic.

---

### [ER-27] — Eager TemplateManager / VocabularyManager init in `VoiceTyperApp.__init__` (disk I/O on cold start)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `app.py:406-419` — `TemplateManager()` and `VocabularyManager()` are constructed inside `VoiceTyperApp.__init__`, which runs on the ipc_server.main thread BEFORE `app.start()` is called. Each manager does file I/O (read templates.json / vocabulary.json + any included files) on the constructor. The inline comment acknowledges this was a deliberate change from lazy-init, to pick up config changes between startup and first dictation.
**Root Cause:** Verified — disk I/O on the synchronous `__init__` path for features the user may never invoke.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`
**Fix:** Revert to lazy init (`self._template_manager = None` etc.), and have the set_config IPC handler call `app._template_manager.reload()` (and vocab) after a config save IF the manager has already been constructed (no-op otherwise). Preserves the config-change visibility while removing the cold-start cost.

---

### [ER-28] — Sound manager AudioContext never closed in production (persists even when sound feedback is disabled)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `sound-manager.ts:57-61, 339-351` + `useSoundFeedback.ts:48-50` — `_sharedAudioContext` and `_fallbackAudio` are module-scoped singletons constructed on first use and never released in production. Critically, `useSoundFeedback` calls `initAudioContext()` unconditionally on App mount, so the AudioContext is constructed and (after first user gesture) transitioned to "running" state — even when the user has `sound_feedback_enabled=false` in config. The `playSoundCue` early-return `if (!isEnabled()) return;` prevents oscillator creation but does NOT close the already-alive AudioContext. Each AudioContext in "running" state holds the audio output device open and runs an internal audio-thread.
**Root Cause:** Verified — `_resetSoundManagerForTests` is the only closer; no production code path calls `ctx.close()` or releases `_fallbackAudio`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
- `voice_typer/client/src/renderer/src/hooks/useSoundFeedback.ts`
**Fix:** Suspend/close the AudioContext when `sound_feedback_enabled` is false. Gate `initAudioContext` on `isEnabled()` in `useSoundFeedback`. Always detach gesture listeners in `_resetSoundManagerForTests`. Add `osc.onended = () => { osc.disconnect(); gain.disconnect(); }` to release per-cue nodes.

---

### [ER-29] — `_tcpStartupTimeoutTimer` not unref'd, not cleared by stopPython/relaunchApp (premature-quit risk)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `tcp-connect.ts:29-36, 43-73` — `_tcpStartupTimeoutTimer` is module-scoped, set on first `tcpConnect()` call, only cleared inside its own callback OR in the connect-success callback (line 112). It is NOT `.unref()`'d, so it pins the Node event loop alive for up to 60s. `clearTcpStartupTimeout()` is NOT exported, so `stop-python.ts` and `relaunch-app.ts` cannot clear it. In `relaunchApp()`'s dev-mode branch, `startPython()` is called again, which calls `tcpConnect()` again — but `tcpConnect()` sees `_tcpStartupTimeoutTimer !== null` and skips setting a fresh timer. The ORIGINAL 60s timer continues counting from the first connect attempt. If the original timer fires while a NEW backend is still starting (and `state.pythonProcess` is the new non-null process), the safety check `state.pythonProcess === null` is FALSE → it shows the "Python backend failed to start" dialog and calls `app.quit()` prematurely.
**Root Cause:** Verified — `clearTcpStartupTimeout` is private; the timer is not unref'd; `relaunchApp()` and `stopPython()` don't clear it.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts`
- `voice_typer/client/src/main/python/stop-python.ts`
- `voice_typer/client/src/main/python/relaunch-app.ts`
**Fix:** Export `clearTcpStartupTimeout` and call it from `stopPython()` and `relaunchApp()`. `.unref()` the timer. Reset it when `startPython()` is called so dev-mode restart gets a fresh 60s window.

---

### [ER-30] — Immortal heartbeat task leak (one per FT-1 reconnect cycle)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `src-tauri/src/sidecar/ws.rs:715-789` `spawn_heartbeat_task` + ws.rs:817 (call site in `reconnect_ws`) — the heartbeat task's only exit conditions are (a) `shutting_down` set, or (b) 3 consecutive heartbeat MISSES. After a successful FT-1 reconnect, the OLD heartbeat task's `dispatch_inner` calls SUCCEED on the new `state.ws_tx` connection, so `missed` resets to 0 and the task NEVER exits. Each FT-1 respawn cycle (supervisor.rs:371 calls `reconnect_ws`) permanently adds a heartbeat task. The `JoinHandle` returned by `tauri::async_runtime::spawn` is DISCARDED — no `abort()` is ever called.
**Root Cause:** Verified — no cancellation mechanism for prior heartbeat tasks.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/supervisor.rs`
**Fix:** Use a generation counter (`heartbeat_generation: AtomicU64`) that the task checks each iteration — if the current generation != the task's spawn generation, break. Increment the generation in `reconnect_ws` before spawning the new heartbeat task. Alternative: store the `JoinHandle` in `SidecarState` and `abort()` the old handle before spawning.

---

### [ER-31] — Rate limiter O(N) sum on every inbound frame (running totals not maintained)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `ipc/rate_limiter.py:229-230` — `_RateLimiter.allow()` is invoked from `sidecar_ws._make_dispatch.dispatch` on every WS frame AND from the TCP dispatch loop. The two `sum()` calls walk the ENTIRE deque on every call: `burst_total = sum(c for _, c in self._burst_timestamps)` + `sustained_total = sum(c for _, c in self._sustained_timestamps)`. Under documented limits (burst=200 over 1s, sustained=600 over 10s), a 60 msg/s client fills the deques with up to 800 `(ts, cost)` tuples, so each `allow()` performs up to 800 integer additions and tuple unpacks — all under `self._lock` (per-limiter, shared across all WS+TCP connections on the same IPCServer).
**Root Cause:** Verified — the cost-weighted G4-M-09 refactor switched from `len(deque) >= limit` (O(1)) to `sum(c for _, c in deque)` (O(N)) but did not maintain a running total to preserve O(1) accounting.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`
**Fix:** Maintain running totals `_burst_total` and `_sustained_total` as instance attrs. Increment by `cost` on `append`, decrement by `c` on each `popleft`. Replace the two `sum()` calls with the cached totals. Behavior identical, O(1) per `allow()`. Add a unit test asserting `_burst_total == sum(c for _, c in _burst_timestamps)` after random sequences.

---

### [ER-32] — IPC TCP drain loop does per-entry syscall + per-entry flush (no-op)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `ipc_server.py:2082-2092` — `_drain_cap = 100`. After a brief client disconnect, `_pending_tcp` (capped at 1000) can hold hundreds of buffered `bubble_level` / `waveform` JSON lines. On the next push event the drain loop runs UP TO 100 iterations, each issuing a separate `sendall` syscall + Python-level exception frame. `_TCPLineIO.flush()` is a no-op (`transport.py:86`) so the explicit flush is dead code, but `write()` itself calls `sendall(text.encode("utf-8"))` per call. This loop runs OUTSIDE `self._lock` but INSIDE `self._tcp_write_lock`, so it serializes every other writer (including dispatch responses) for the duration of the drain.
**Root Cause:** Verified — per-entry flush instead of batched write.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
**Fix:** Join drained entries into a single buffer: `batch = "".join(f"{p}\n" for p in recent); tcp_client.write(batch)`. Single `sendall`, single encode, single syscall. Drop the per-entry `flush()` call (no-op anyway).

---

### [ER-33] — WS allowlist linear scan on every inbound frame (50 string-pointer comparisons)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `src-tauri/src/sidecar/ws.rs:537` — `ALLOWED_EVENT_TYPES.contains(&event_type)` is a linear scan over a 50-entry `&[&str]` literal. Runs on EVERY server-initiated event frame that reaches the reader. `bubble_level` is coalesced to ≤30 Hz just below (lines 547–574) but the allowlist check runs BEFORE the coalesce branch, so every `bubble_level` frame pays the 50-entry scan.
**Root Cause:** Verified — `&[&str]::contains` is a linear scan.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Replace the slice + `.contains()` with a `match event_type { "status_change" | "bubble_level" | ... => true, _ => false }` expression (compiles to a jump table), OR sort the slice and use `binary_search` (O(log N)) as a no-new-dep fix.

---

### [ER-34] — WS writer triple-encodes outbound events (json→str→bytes→ws)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `sidecar_ws.py:651-652` — for every outbound event: (1) `json.dumps(event, ensure_ascii=False)` produces a `str`, (2) `raw.encode("utf-8")` allocates a SECOND copy as `bytes` purely to measure length, (3) `websocket.send(raw)` re-encodes the `str` to bytes a THIRD time inside the websockets library. With `bubble_level` at 15–50 Hz plus `transcription_partial` / `audio_status` / `model_download_progress` bursts, this triple-encode runs continuously while recording or downloading.
**Root Cause:** Verified — the size check needs byte length but discards the encoded bytes instead of reusing them.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
**Fix:** `raw_bytes = json.dumps(event, ensure_ascii=False).encode("utf-8"); if len(raw_bytes) > _MAX_FRAME_BYTES { ... }; await websocket.send(raw_bytes)`. The websockets library accepts `bytes` directly. Note: the Rust host's reader loop currently handles only `Message::Text` — would need to also accept `Message::Binary` for the full optimization. Safer minimal fix: compute length via `len(raw)` (char count) and only `.encode()` when over threshold.

---

### [ER-35] — Double-emit per coalesced `bubble_level` (specific + generic catch-all)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `src-tauri/src/sidecar/ws.rs:569-570` — after coalescing `bubble_level` to ≤30 Hz, the reader emits TWO Tauri events per frame: (1) the specific `bubble_level` event with `p.clone()`, (2) a generic `python-event` catch-all that constructs a fresh `serde_json::Value` object via `json!({...})` — a `Map<String, Value>` allocation + insertion + the cloned payload, every frame. Same pattern at line 587-588 for EVERY other server event type.
**Root Cause:** Verified — double-emit is intentional (ADR-0020 §6.3) but the `json!({...})` macro constructs a new `Value` per emit.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Drop the catch-all for `bubble_level` specifically (it's the highest-rate event and the bubble window has a dedicated listener) — emit only the specific event for high-frequency types, fall back to the generic catch-all for low-frequency types. Coordinate with renderer `usePython.ts` to ensure no listener relies on the catch-all for `bubble_level`.

---

### [ER-36] — History retention only runs at startup (DB grows monotonically during long sessions)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `history_db.py:1567` `apply_retention` is a well-implemented chunked DELETE + conditional VACUUM method. However, the ONLY caller is `startup_sequence.py:273` (`_apply_retention_bg`), which runs once at app launch. No periodic/interval scheduler exists. User configures `history_max_entries=1000`. App starts at 1000 rows (pruned at last launch). During an 8-hour dictation session at ~1 transcription/minute, 480 new rows accumulate → DB ends session at 1480 rows. DB file never shrinks during the session because VACUUM only runs inside `apply_retention` (which only runs at startup).
**Root Cause:** Verified — retention scheduling is startup-only by design (DEAD-012 comment in startup_sequence.py:254).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py`
- `voice_typer/server/startup_sequence.py`
**Fix:** Add a periodic retention sweep — schedule `apply_retention()` on a 10-minute interval via `app._schedule_timer` or a dedicated daemon thread registered with ThreadRegistry. Alternatively, trigger retention after every N `add_transcription` calls (e.g. every 100 writes) to amortize cost. `apply_retention` is already chunked and safe to call at runtime.

---

### [ER-37] — Vocabulary regex recompiled per phrase per transcription call (~55ms at 5000 entries)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `vocabulary.py:598-621` `apply_to_text` — for each dictation cycle, the `phrase_corrections` + `extra_word_patterns` loop does `_re.compile(_re.escape(bad), _re.IGNORECASE)` PER entry PER call. No compiled-pattern cache exists on `VocabularyManager`. `MAX_CORRECTIONS_ENTRIES=5000` caps each category, so up to 10000 phrase entries can each trigger a fresh `_re.compile()` per dictation. Python's `re` module has an internal compile cache (default 512 entries), so with >512 distinct patterns the cache thrashes. At 5000 entries: sort O(n log n) + compile 5000 × ~10μs = ~50ms + sub 5000 × O(text_length) = ~5-20ms. Total ~55-70ms per transcription.
**Root Cause:** Verified — no compiled-pattern caching.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vocabulary.py`
**Fix:** Cache compiled regexes on `VocabularyManager`, invalidated when entries are added/removed/imported. Build a single combined regex (alternation of escaped patterns) with a name→replacement map, or use a dict mapping bad-phrase → compiled pattern that is rebuilt only when `self._data` mutates.

---

### [ER-38] — Parakeet and Qwen engines lack warm-up inference (2-5s stall on first dictation)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `transcription.py:419, 557-588` Whisper engine has explicit warm-up after load: runs a warm-up inference with 0.5s of silence to prime CUDA kernels. `parakeet_engine.py:333-608` load returns True at line 592 with NO warm-up inference. Qwen engine (`qwen_engine.py:151-321`) also has no warm-up. The first real dictation pays CUDA kernel JIT/compilation cost (2-5s on first `generate()` call) — user perceives a long stall on the first utterance after switching to Parakeet.
**Root Cause:** Verified — no `_warm_up` / priming call in `parakeet_engine.py` or `qwen_engine.py`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/qwen_engine.py`
**Fix:** Add a `_warm_up_model()` method to `ParakeetEngine` and `QwenEngine` mirroring `transcription.py:557-588` — run `model.generate()` on 0.5 s of silence after successful load, inside the existing lock and only when `device == "cuda"`. **VALIDATE ON CUDA HOST** — verify warm-up actually primes kernels on a real GPU.

---

### [ER-39] — Whisper `beam_size=1` default sacrifices 1-3% WER for speed
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `transcription.py:208-209` `TranscriptionEngine.__init__` — `beam_size=1, best_of=1` defaults. Used at transcribe call (line 822-835). faster-whisper docs and OpenAI Whisper paper show `beam_size=5` reduces WER by 1-3% on small.en vs greedy beam_size=1. For a dictation tool, every mis-transcribed word is a manual correction. The default is chosen for speed (greedy ~2× faster than beam=5) but is suboptimal for accuracy. `temperature` is also pinned to 0.0 — no fallback temperature retry on low-confidence segments.
**Root Cause:** Verified — speed-biased defaults that trade measurable WER.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py`
**Fix:** Either raise default `beam_size` to 5 (configurable), or add a config field `whisper_beam_size` defaulting to 5 for non-tiny models and 1 only for tiny.en / CPU. Optionally enable temperature fallback (`temperature=[0.0, 0.2, 0.5]`) when `avg_logprob < -1.0`.

---

### [ER-40] — LLM polish 30s synchronous timeout blocks the paste pipeline
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `llm_polish.py:248` `_call_api` — `with _opener.open(req, timeout=30) as resp:`. `polish()` (line 110-145) is a synchronous public API called from the dictation pipeline (per the module docstring: "Pipeline order: transcribe → text cleanup → vocabulary → templates → LLM polish → auto-punctuate → paste"). A 30s socket timeout is generous; on a stalled connection the user waits up to 30s before their text is pasted. There is no cancellation, no async, no fallback-to-original after a shorter budget. The except clause catches `Exception` and returns original text, so the failure mode is "wait 30s, then paste unpolished" — the user has no indication their dictation is hung.
**Root Cause:** Verified — 30s synchronous timeout inline in pipeline; no shorter user-facing budget.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/llm_polish.py`
**Fix:** Lower the default timeout to 10s (LLM completions for <500 char dictations finish in <3s typically) and add a config field `llm_polish_timeout_s`. Optionally run polish async and paste original text immediately, replacing it when polish completes (out of scope unless the paste pipeline already supports edits).

---

### [ER-41] — Hallucination Tier-1 cuts legitimate quiet "thank you"/"bye" utterances
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `hallucination.py:65-95` `should_reject_low_audio_hallucination` Tier 1 — `KNOWN_LOW_AUDIO_HALLUCINATIONS` contains legitimate short phrases like "bye", "thank you", "thank you for listening". `rms < 0.001` (≈-60 dBFS) is achievable with quiet microphones, low input gain, or distant talkers. The check is keyed ONLY on the phrase being in the known-hallucination set — it does not require any other "this is a hallucination" signal beyond RMS.
**Root Cause:** Verified — Tier 1 lacks any "did the model actually decode this from real audio" signal beyond RMS; relies entirely on the phrase being on a small allowlist.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hallucination.py`
**Fix:** Tighten Tier 1 to require BOTH `rms < 0.001` AND `duration < 1.0` — hallucinations on near-silence are typically emitted within 1s of audio; a deliberate "thank you" is usually ≥0.5s and recorded with `rms > 0.005`. Alternatively, require `silence_pct > 95` (not 90) for Tier 1.

---

### [ER-42] — Silero VAD thresholds not auto-calibrated (calibration explicitly skipped for Silero path)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `vad_processor.py:155-157, 332-340` — `_speech_threshold=0.5`, `_silence_threshold=0.3` are static config defaults. When Silero is active, `auto_calibrate()` explicitly bails out (line 332-340). Silero's probability output IS sensitive to noise floor (a loud HVAC raises the baseline prob), so a fixed 0.5 either over-triggers on fan noise or under-triggers on quiet speech depending on the room.
**Root Cause:** Verified — calibration exists but is gated off for the Silero path.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vad_processor.py`
**Fix:** During the first 1.5 s of recording, collect Silero probs on (presumed) ambient audio and set `speech_threshold = max(0.5, median_ambient_prob + 0.2)` and `silence_threshold = speech_threshold - 0.2`. Falls back to 0.5/0.3 if median is unusable.

---

### [ER-43] — RNNoise warmup skips ALL filters (not just RNNoise) on first chunks of every session
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `audio_processor.py:219-232` + `noise_suppressor.py:173-176` — when RNNoise buffers partial frames, it returns `None`. The caller's fallback: `return result if result is not None else chunk` — returns the ORIGINAL unfiltered chunk. But the chain short-circuits at `base.py:117-118` when any filter returns None, so NONE of the downstream filters run either. The first 1-3 words of every dictation session are transcribed from completely unprocessed audio — no highpass (rumble passes), no gate (noise passes), no EQ (no presence boost), no compressor (level uneven).
**Root Cause:** Verified — chain short-circuits on first buffering filter.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_processor.py`
- `voice_typer/server/audio_filters/base.py`
- `voice_typer/server/audio_filters/noise_suppressor.py`
**Fix:** Reorder the chain so RNNoise is NOT first (move it after the highpass at minimum, so highpass+gate+EQ still run even when RNNoise buffers). OR pre-warm RNNoise with a 10ms zero-frame during `AudioProcessor.__init__` so the first real chunk already has a full frame buffered.

---

### [ER-45] — Presets only toggle filters, don't tune parameters (noisy_room == auto params)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `audio_presets.py:31-69` + `:96-111` `apply_preset` — `PRESET_NOISY_ROOM` uses the SAME parameter defaults as `PRESET_AUTO`: same gate threshold (-26 dB), same compressor ratio (3:1), same EQ gains, same highpass cutoff. The preset name is misleading — the only real differences are the (broken) DeepFilterNet selection and the notch filter.
**Root Cause:** Verified — `apply_preset()` only does `for key, value in PRESETS[preset].items(): setattr(config, key, value)`. No per-preset parameter tuning.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_presets.py`
**Fix:** Extend the `PRESETS` dict to include per-preset parameter overrides, e.g. `PRESET_NOISY_ROOM["noise_filter_gate_open_threshold_db"] = -20.0`, `["noise_filter_compressor_ratio"] = 4.0`, `["noise_filter_compressor_threshold_db"] = -24.0`, `["noise_filter_highpass_cutoff_hz"] = 100.0`. Update `apply_preset` to apply these alongside the toggles.

---

### [ER-46] — No per-filter A/B bypass (must rebuild whole chain to toggle one filter)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `base.py:52-95` `AudioFilter` ABC has no `enabled`/`bypass`/`toggle` attribute. `build_chain` — filter is either appended or not (binary). Once in the chain, the filter always runs. To A/B test, you must `rebuild_from_config`, which calls `FilterChain.swap` and `reset()`s every old filter's state. This means you cannot compare "EQ on vs EQ off" without also resetting the compressor envelope, gate openness, RNNoise carry, etc. — so any A/B comparison is contaminated by state transients.
**Root Cause:** Verified — no runtime bypass.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/base.py`
- `voice_typer/server/audio_chain_builder.py`
**Fix:** Add an `enabled: bool = True` property to `AudioFilter` base class; have `FilterChain.process` skip filters where `not f.enabled`. Add an IPC method to toggle individual filters without rebuilding the chain.

---

### [ER-47] — Watchdog thread race: stop doesn't join, start reuses dying thread
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `recording_controller.py:857-926` — `_stop_watchdog_thread` sets the stop event but does NOT join the thread and does NOT set `_watchdog_thread = None`. The thread is still alive (exiting its loop) when `_start_watchdog_thread` is called next. `_start_watchdog_thread` sees `is_alive()` → True → returns without creating a new thread. But the old thread is about to exit. Within milliseconds, the old thread exits, leaving NO watchdog for the new transcription cycle.
**Root Cause:** Verified — `_stop_watchdog_thread` does not join or clear the thread reference.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_controller.py`
**Fix:** In `_stop_watchdog_thread`, join the thread with a short timeout (e.g. 0.5s) and set `_watchdog_thread = None`. Alternatively, in `_start_watchdog_thread`, if the old thread is alive but `stop_event` is set, wait briefly for it to exit before deciding whether to create a new thread.

---

### [ER-48] — Stuck transcription thread not fenced after force-recovery (model race)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `recording_controller.py:799-853` `_force_recover_from_stuck_transcription` — the stuck transcription thread (e.g. ctranslate2 deadlock) continues running in the background. On the next `stop()` (line 515), the old reference is overwritten. If the old thread eventually completes its model call, it runs `DictationPipeline.run()`'s finally block. The old thread is still holding the ctranslate2 model lock. When the new transcription thread calls the model concurrently, ctranslate2 is not thread-safe for concurrent calls on the same model → crash or silent corruption.
**Root Cause:** Verified — no mechanism to kill or fence the stuck transcription thread. Python threads cannot be force-killed; the only option is to set a flag the thread checks, but ctranslate2's C++ call is not interruptible.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_controller.py`
**Fix:** After force-recovery, set a module-level "model in use" lock that the new transcription thread must acquire before calling the model. The old thread holds the lock; the new thread blocks until the old thread's ctranslate2 call returns and releases it. Prune `_cancelled_cycle_ids` to keep only the last N entries. Consider reloading the model after a force-recovery to ensure clean state.

---

### [ER-49] — FilterChain coarse lock: process and swap block each other (UI toggle stalls audio)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `base.py:110-120` `FilterChain.process` acquires `self._lock` for the ENTIRE chain pass: `with self._lock: for f in self._filters: ... result = f.process(audio, sample_rate)`. `swap()` and `reset()` also acquire the same lock. With 4 active filters doing per-sample Python loops (see ER-7), each `process()` call holds the lock for several ms. When the user toggles a Settings UI control, `_rebuild_audio_processor` calls `chain.swap()` from the IPC thread and blocks for the full chain duration (~1–5 ms). Conversely, the worker thread blocks on the same lock if a rebuild is mid-flight, potentially causing a missed 32 ms RT deadline → ring buffer overflow + dropped chunks.
**Root Cause:** Verified — coarse-grained locking. The lock is needed only to protect the `_filters` list reference, not the per-filter state.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/base.py`
**Fix:** RCU-style swap — read `filters = self._filters` once outside the lock, iterate the snapshot, and have `swap()` replace the list atomically (`self._filters = list(new_filters)` under the lock, but no lock held during processing). Filter state guards (if any) should be inside each filter on its own atomic state. This eliminates lock contention between process and swap paths.

---

### [ER-50] — macOS wait_for_prewarm spawns `ps` subprocess up to 60× during 60s wait
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `prewarm/completion_events.py:126-148` + `prewarm/process_tracker.py:430-493, 496-602` — on macOS, `_wait_for_completion_event` returns False (only Windows + Linux are handled — no kqueue `EVFILT_PROC`). So `wait_for_prewarm` ALWAYS falls through to the 1s poll loop on macOS. Each `is_prewarm_running()` call (when the PID file exists + process alive) calls `_process_is_prewarm(pid)` which on macOS spawns a `ps` subprocess: `subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)`. So a 60s `wait_for_prewarm` on macOS = up to 60 × `ps` subprocess spawns (fork+exec+pipe). 60 fork/exec calls × ~10-20 ms each ≈ 0.6-1.2 s of CPU + 60 pipe pairs during the user's first 60 s after login.
**Root Cause:** Verified — `completion_events.py:65-67` explicitly documents macOS as falling back to the poll loop. No `kqueue(EVFILT_PROC, NOTE_EXIT)` implementation exists.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/completion_events.py`
- `voice_typer/server/prewarm/process_tracker.py`
**Fix:** (a) Implement `_wait_completion_macos` using `select.kqueue()` + `kevent(EVFILT_PROC, NOTE_EXIT)` on the prewarm PID — a zero-CPU kernel wait analogous to Linux pidfd. (b) In `_process_is_prewarm` on macOS, cache the result for the duration of a single `wait_for_prewarm` call (the PID can't be recycled mid-wait). (c) Alternatively, use `libproc` (ctypes) `proc_pidpath` instead of spawning `ps`. **VALIDATE ON MACOS HOST** — verify kqueue-based wait works on real macOS.

---

### [ER-51] — Prewarm package warming has no extension filter (warms .pdb/.h/.pyi/docs)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `prewarm/cache_probe.py:117-123` `_warm_package_files` — the docstring claims it reads ".pyc / .dll / .pyd / .py" but the actual code reads ANY file: `for path in sorted(root.rglob("*")): if path.is_file(): ... _pkg._warm_file(path)`. There is NO extension filter. This warms torch/transformers/faster_whisper/ctranslate2 `.pdb` debug symbols, `.h` headers, `.pyi` stubs, `*.dist-info/METADATA`, `docs/`, `tests/`, `__pycache__` `.pyc` that are already cached, `.so` debug info. For torch on Windows this is ~1.5 GB of `.pdb` files; on Linux ~200-500 MB of debug `.so` and headers per package.
**Root Cause:** Verified — `rglob("*") + is_file()` matches everything; the docstring's claim of filtering by extension is aspirational.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/cache_probe.py`
**Fix:** Filter by extension whitelist `{".py", ".pyc", ".pyd", ".dll", ".so", ".dylib"}` and skip directories named `tests`, `test`, `docs`, `__pycache__`, `*.dist-info`, `*.egg-info`. Optional: also skip files larger than e.g. 200 MB that aren't `.so`/`.dll`/`.pyd` (likely debug symbol dumps).

---

### [ER-52] — faster_whisper + ctranslate2 package bytes warmed UNCONDITIONALLY regardless of active backend
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `prewarm/cache_probe.py:188-197` (inside `_warm_imports`) — faster_whisper + ctranslate2 package bytes are warmed UNCONDITIONALLY, regardless of active backend. This runs even when `active_backend == "parakeet"` or `"qwen"`. The comment justifies it as "the Whisper fallback (tiny.en) is what AsrBackendRegistry falls back to" — but for a parakeet install that has never downloaded tiny.en, `_active_model_cache_dirs()` correctly skips the tiny.en weights, yet the ~200-500 MB faster_whisper + ctranslate2 package bytes are still paged in for nothing.
**Root Cause:** Verified — the package warming is not gated on whether the Whisper fallback weights actually exist in the HF cache.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/cache_probe.py`
**Fix:** In `_warm_imports`, only call `_warm_package_files("faster_whisper")` / `_warm_package_files("ctranslate2")` when either (a) `active_backend == "whisper"`, OR (b) the tiny.en fallback cache dir exists (re-use `_active_model_cache_dirs()` or a cheaper existence check on `Systran--faster-whisper-tiny.en`).

---

### [ER-53] — Config backup-on-every-save does redundant read+write per `set_config` IPC
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `config.py:1141-1157` `_save_locked` — every `set_config` IPC that changes a field does: 1 disk read (config.json) + 1 disk write (config.json.bak) + 1 atomic write (mkstemp + write + fsync + os.replace + fsync(parent dir)) = ~2 fsyncs + 3 file ops. The backup is "best-effort single-slot" so it only ever holds the immediately-previous version, making the per-save read+write largely redundant after the first save.
**Root Cause:** Verified — every save unconditionally reads the existing file and writes a `.bak`, even though the previous save already produced an identical `.bak`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
**Fix:** Track the last-written bytes in memory (e.g. `_last_saved_content` on the Config instance or a module-level hash). Skip the backup read+write when the on-disk file already matches the known-previous content. Alternatively, only write the `.bak` on schema-migration saves (where fields may be dropped) rather than every save.

---

### [ER-54] — `time.time()` used for monotonic intervals in tray tooltip (wall-clock jumps break display)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `tray.py:228, 724` — `self._recording_started_at = time.time()` (entering RECORDING state) and `elapsed = time.time() - self._recording_started_at` (computing tooltip elapsed suffix). `time.time()` is wall-clock and subject to NTP adjustments, DST transitions, and manual clock changes. If the wall clock jumps during a recording, the tray tooltip's "(mm:ss)" suffix can go negative or jump by hours/days.
**Root Cause:** Verified — `time.time()` used for a monotonic interval.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py`
**Fix:** Replace both call sites with `time.monotonic()`. The `_recording_started_at` field is only ever compared against itself (set in `set_state`, consumed in `_compute_tooltip`), so no other call site needs to change.

---

### [ER-55] — Templates module recompiles regex per template per match; eager variable resolution (clipboard read) per match
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `templates.py:390, 399` — `normalized = re.sub(r"\s+", " ", text.strip()).lower()` and `trigger_norm = re.sub(r"\s+", " ", trigger.strip()).lower()`. `re.sub(r"\s+", ...)` recompiles the same pattern on every call. With `MAX_TEMPLATES=1000`, the inner loop re-looks-up the cached compiled pattern 1000 times per dictation. `templates.py:47-64` `substitute_variables` — `datetime.now()` is called twice (two syscalls); ALL four values (`today`, `now`, `clipboard`, `username`) are computed eagerly, even when the output text contains none of the variables. `_get_clipboard_text()` in particular can block (pyperclip synchronously reads the clipboard, which on Linux can spin a subprocess for xclip/xsel).
**Root Cause:** Verified — `re.sub(r"\s+", ...)` recompiles; eager computation of all 4 variables including potentially-blocking clipboard read.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/templates.py`
**Fix:** (a) Add a module-level `_WHITESPACE_RE = re.compile(r"\s+")` constant and use `.sub()` on it at both call sites. (b) Refactor `substitute_variables` to a single regex pass with lazy variable resolution via a callback: `_TEMPLATE_VAR_RE = re.compile(r"\{(today|now|clipboard|username)\}")` and resolve each variable only when its placeholder is actually present.

---

### [ER-56] — Home.tsx inline `usePythonEvent` handlers + non-memoized subcomponents (re-render churn)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `Home.tsx:540-563, 588-597, 604-618, 622-629` — four `usePythonEvent` calls pass inline arrow functions. Each inline arrow has a fresh identity on every Home render, causing unsubscribe/resubscribe churn on every state flip (and Home flips `downloadPct`, `transcribeStartedAt`, `showForceCancel`, `lastText`, `toggling`, `stats`, `recent`, `cfg`, `refreshing`, `initialLoading` frequently). Compounded by `Home.tsx:148-337` — `RecordingStatusPill`, `MicToggleButton`, `LastTranscriptionPreview`, `RecordingErrorCard` are plain function components (no `React.memo`) — they re-render on every parent (Home) state change.
**Root Cause:** Verified — compare with `useConnection.ts` which uses `useCallback` around its `usePythonEvent` handlers; Home.tsx does not. None of the four subcomponents are memoized.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
**Fix:** Wrap each inline handler in `useCallback` with the correct dependency array. Wrap each of the 4 subcomponents in `React.memo` (props are all primitives or callbacks already wrapped in `useCallback`).

---

### [ER-57] — Dashboard.tsx render body recomputes derived values on every render (no useMemo)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `Dashboard.tsx:460-627` — render body: `const maxCount = Math.max(1, ...d.dailyActivity.map((a) => a.count))` (line 461), `d.todayChars.toLocaleString(getLocale())` (line 532), `compactNumber(d.totalCount)` (line 543), `d.totalChars.toLocaleString(getLocale())` (line 546), `formatDuration(d.todayDuration)` (line 537), `d.dailyActivity.map((day) => { const ariaLabel = t("analytics.dayActivityAria", {...}); ... })` (line 579 — 7× per render). Dashboard re-renders on every `transcription_final` debounced refresh, every manual refresh, every locale toggle.
**Root Cause:** Verified — render body performs O(N) work on every render with no memoization.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx`
**Fix:** Wrap derived labels and the chart-bar array in `useMemo` keyed on `[data, locale]`.

---

### [ER-58] — Tauri host: `thread::sleep(10ms)` on event-loop thread in relaunch_app listener
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `main.rs:287` — `app.listen("relaunch_app", move |_event| { ...; std::thread::sleep(std::time::Duration::from_millis(10)); restart_handle.restart(); })`. The listener runs on the Tauri event-loop thread. A synchronous `thread::sleep(10ms)` blocks event processing for 10ms before `app.restart()` tears down the process.
**Root Cause:** Verified — UI-thread blocking sleep.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/main.rs`
**Fix:** Replace with `tauri::async_runtime::spawn(async { tokio::time::sleep(Duration::from_millis(10)).await; restart_handle.restart(); })` so the event loop continues processing during the sleep.

---

### [ER-59] — `config_dir()` re-resolves 4 env vars on every call (no caching, 5+ call sites per launch)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `paths.rs:60-71` `config_dir` calls `config_dir_from_env(...)` with 4 `std::env::var()` lookups every call. `supervisor.rs:81-89, 153-160` each repeat the same 4 `std::env::var()` calls inline (they can't call `config_dir(app)` because they have no `AppHandle`). At least 5 calls per launch, more under FT-1 flapping. Each call is ~microseconds but they sum.
**Root Cause:** Verified — env vars are invariant for the process lifetime, but every call re-resolves them.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/paths.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/migrate.rs`
**Fix:** Introduce `config_dir_cached() -> &'static Path` backed by `OnceLock<PathBuf>`. All call sites (paths.rs `config_dir`, supervisor.rs `read/write_ft1_restart_counter`, migrate.rs `migrate_electron_userdata`) route through it. Single resolution per process.

---

### [ER-60] — Unused `app` param in `config_dir()` (F-Q9 self-documented TODO)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `paths.rs:60-71` — `pub(crate) fn config_dir(app: &tauri::AppHandle) -> std::path::PathBuf { let _ = app; ... config_dir_from_env(...) }`. The function body discards `app` with `let _ = app;` — a smell flagged in the project's own lint rules. Every caller is forced to thread an `&AppHandle` through even though the function only reads env vars. This also forces `read_ft1_restart_counter` / `write_ft1_restart_counter` (supervisor.rs:81, 153) to bypass `config_dir(app)` and call `config_dir_from_env(...)` directly — the duplicate 4-env-var lookup is a direct consequence.
**Root Cause:** Verified — function signature lies about its dependencies.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/paths.rs`
**Fix:** Either (a) drop the `app` parameter entirely and have all callers invoke `config_dir_from_env(...)` via a thin `config_dir()` wrapper (no AppHandle needed), OR (b) actually consult `app.path().app_config_dir()` as a fallback when env vars are missing (preferred per the F-Q9 TODO).

---

### [ER-61] — Connection hook polls `get_status` every 60s (redundant with push events)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `useConnection.ts:205-245` — `setInterval(() => probe(false), 60_000)` polls `get_status` every 60s to detect backend crashes, with up to 3 quick retries on failure before flipping to "disconnected". The Python backend already emits `state_changed` on every connect (line 318-342 subscriber handles this), and the RW-10 TCP heartbeat (5s backend→frontend) already detects Electron crashes. The renderer→backend 60s poll is duplicative for the "backend alive" check — `get_status` should be event-driven.
**Root Cause:** Partially verified — the poll exists per PVT-fix-16 to detect backend crashes via a lightweight `get_status`. But the backend already has a heartbeat-push mechanism; this is belt-and-suspenders.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`
**Fix:** Replace the 60s fixed poll with a "last-event-received" timestamp; only probe if no Python event has been received in N minutes.

---

### [ER-62] — Home.tsx fetches `get_config` on EVERY `status_change` event to refresh hotkey (redundant with `config_changed`)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `Home.tsx:540-563` — the `status_change` `usePythonEvent` handler does `const cfg = await call<VoiceTyperConfig>("get_config"); ... setHotkey(normalizeHotkey(cfg?.hotkey ?? "<f2>"))` on EVERY `status_change` event — including every `recording → transcribing → idle` transition. A full `get_config` IPC round-trip + state update on every recording cycle just to refresh the hotkey string. The hotkey rarely changes — only when the user edits Settings.
**Root Cause:** Verified — `get_config` is fired on every `status_change`, redundant with `config_changed` subscription.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
**Fix:** Move hotkey reload to a `config_changed` `usePythonEvent` handler (which already fires when the user saves Settings), and drop the per-`status_change` `get_config` fetch entirely.

---

### [ER-63] — Electron logging.ts: `getRuntimeLogPath()` called per warn/error (require + getPath every log line)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `logging.ts:418-436, 502-523` — `getRuntimeLogPath()` is called on every `log.warn()` / `log.error()` call, and it does `require("electron")` (Node module-resolver lookup on every call) + `app.getPath("userData")` (returns the same string every time).
**Root Cause:** Verified — no caching; require + getPath on every log line.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/logging.ts`
**Fix:** Memoize the path with a module-level variable; add a test-only reset.

---

### LOW Findings (curated — bundled by area)

### [ER-64] — Backend low-severity cleanups (tray time.monotonic, _secrets closure hoist, _LOOPBACK_HOSTS module constant)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (bundled)
**Description:** Multiple low-severity issues in the Python backend:
- `_secrets.py:296-307` — closure `_sub` re-created inside loop in `redact_api_keys` (4 function objects per call instead of 1).
- `_secrets.py:533` — `_loopback_hosts` frozenset literal re-evaluated per call in `assert_url_allowed`; should be module-level constant.
- (Tray `time.monotonic` is ER-54, tracked separately.)
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_secrets.py`
**Fix:** Hoist `_sub` out of the loop in `redact_api_keys` (capture `replacement` via closure). Promote `_loopback_hosts` to module-level `_LOOPBACK_HOSTS` constant.

---

### [ER-65] — Frontend low-severity cleanups (localStorage double-read, useTheme mega-effect, dynamic crypto require, ConfirmDialog useCallback, i18n lazy-load locales)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (bundled)
**Description:** Multiple low-severity issues in the frontend:
- `Home.tsx:396-401` — duplicate `loadCachedStats()` / `loadCachedRecent()` calls in `initialLoading` initializer (4 localStorage reads on mount vs 2 needed).
- `useTheme.ts:293-307` — single mega-effect writes 4 localStorage keys on any one change.
- `bootstrap.ts:40-49` — defensive dynamic `require("node:crypto")` of a guaranteed-built-in module.
- `Settings.tsx:190-218` — `resetToDefaults` not wrapped in `useCallback`.
- `i18n.ts:18-26, 159-203` — all 8 locale JSON files statically imported; ~400-700KB compressed loaded upfront.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts`
- `voice_typer/client/src/main/bootstrap.ts`
- `voice_typer/client/src/renderer/src/pages/Settings.tsx`
- `voice_typer/client/src/renderer/src/i18n/i18n.ts`
**Fix:** Apply the targeted fixes for each (see individual subagent reports for code).

---

### [ER-66] — Rust host low-severity cleanups (logging hot-path allocations, `Cow::to_string` → `into_owned`, verbose HashSet init, redundant `last_bubble_payload`, CSV export Vec allocations, sync code wrapped in async spawn)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (bundled)
**Description:** Multiple low-severity issues in the Rust/Tauri host:
- `platform/logging.rs:109-142` — 3-4 allocations per log record (msg + ts + line + eprintln format). Pre-allocate thread-local `String` buffer.
- `sidecar/spawn.rs:115, 119, 280, 370` + `migrate.rs:519-521` — `String::from_utf8_lossy(&bytes).to_string()` always allocates a new String even when the Cow is already Owned; use `.into_owned()`. Same for `entry.file_name().to_str()` + `.to_string()` → `into_string()`.
- `commands/sidecar_cmds.rs:99-219` — 14 lines of defensive duplicate-detection logging on a static `&[&str]` literal; use `HashSet::from_iter(cmds.iter().copied())`.
- `sidecar/ws.rs:485-488, 563-574` — `last_bubble_payload: Option<Value>` is redundant (always overwritten before being read); drop it and remove `#[allow(unused_assignments)]`.
- `commands/export.rs:103-110, 112-123` — each row collects into `Vec<String>` then `.join(",")` (double allocation per cell); write directly into output String with `push_str` + `push(',')`.
- `tray.rs:269-273, 292-314` — `tauri::async_runtime::spawn(async move { sync_code(); })` wraps synchronous code in an async task that never awaits; use `std::thread::spawn` or inline.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/sidecar/spawn.rs`
- `src-tauri/src/migrate.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/commands/export.rs`
- `src-tauri/src/tray.rs`
**Fix:** Apply the targeted mechanical changes for each (see individual subagent reports for code).

---

### [ER-67] — Audio pipeline low-severity cleanups (notch auto-detect always 60Hz, default `auto` preset over-processes, limiter dead weight, per-chunk redundant `.copy()`, `np.count_nonzero` per chunk, resampling per-call FIR rebuild, `_dropped_ring_chunks` not surfaced in real-time)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (bundled)
**Description:** Multiple low-severity issues in the audio pipeline:
- `audio_filters/notch.py:38-46` — `_auto_detect_frequency` always returns 60.0 (EU/Asia users get 60Hz notch for 50Hz hum).
- `audio_presets.py:32-40` + `audio_chain_builder.py:127-153` — default `auto` preset applies maximum processing (6 filters) — may hurt ASR accuracy for users with good mics in quiet environments.
- `audio_filters/limiter.py:33-46` + `compressor.py:40` — limiter at -6dB ceiling NEVER engages on normal speech (signal already below ceiling after EQ+compressor); dead weight in default chain.
- `recorder.py:2347` — `.copy()` allocates a fresh 2KB array per chunk; AudioProcessor already does `astype(np.float32)` copy.
- `recorder.py:2236` — `np.count_nonzero(indata)` runs full-array scan per chunk for disconnect detection; defer to existing RMS computation.
- `resampling.py:179-283` + `audio_processor.py:196-208` — `scipy.signal.resample_poly` re-computes FIR filter + FFT plan on every call; cache filter taps.
- `recorder.py:2167-2189` — ring-buffer overflow detected but `_dropped_ring_chunks` counter not surfaced in real-time (only post-recording diagnostics).
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/notch.py`
- `voice_typer/server/audio_presets.py`
- `voice_typer/server/audio_chain_builder.py`
- `voice_typer/server/audio_filters/limiter.py`
- `voice_typer/server/audio_filters/compressor.py`
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/resampling.py`
- `voice_typer/server/audio_processor.py`
**Fix:** Apply targeted fixes (see individual subagent reports). Some (notch auto-detect via timezone, default preset A/B benchmark) are out of session scope and noted as future work.

---

### [ER-68] — Prewarm low-severity cleanups (no cache-ratio probe before warming, macOS LaunchAgent login re-fire Python startup)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (bundled)
**Description:**
- `prewarm/pipeline.py:200-228` — weights warming loop unconditionally calls `_warm_file(f)` for every weights file, with no cache-state probe. `_cache_ratio` exists but is not consulted. When `wait_for_prewarm` times out and the caller spawns a NEW prewarm, the new prewarm re-reads the entire 2.4 GB model.safetensors even if 95% of it is still resident.
- `prewarm_scheduler_posix.py:96-132` — every login causes the full Python prewarm process to start (cold import + setup_logging + sentinel check) before the sentinel check short-circuits. ~500ms-1s of Python cold-start CPU + disk I/O at every login.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm/pipeline.py`
- `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** (1) Before `_pkg._warm_file(f)` for files > ~100 MB, call `_pkg._cache_ratio(f, samples=10)` and skip if ratio >= 0.9. (2) Either wrap the plist's ProgramArguments in a tiny shell pre-check that exits 0 if the sentinel file's first line matches `sysctl -n kern.boottime`, OR add `--delay 30` so prewarm starts 30s after login.

---

### [ER-69] — `streaming.py` `_word_key_index` per-key list growth + `_seen_timestamps` rebuild
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `streaming.py:193, 348-377, 393-398` — `_word_key_index: dict[str, list[int]]` per-key lists grow on every word insertion. For sessions shorter than 10000 words, no eviction runs, so the index retains one int per committed word for the entire session lifetime. For recurring tokens ("the", "and", "a") the per-key list can grow to thousands of ints. Bounded per session (≤ 10000 total ints once maxlen kicks in), but for sub-maxlen sessions it's pure waste. Also a latent performance issue: once maxlen is reached, every insertion triggers an O(D) scan over all distinct-word keys to find the evicted absolute_idx. `streaming.py:321-327` `_prune_old_entries` rebuilds the entire set rather than mutating in place — transient 2× memory spike every ~5s.
**Root Cause:** Verified — the dedup index's per-key lists grow on every word insertion; the prune pass rebuilds the entire set.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/streaming.py`
**Fix:** Add a timestamp-prune pass to `_word_key_index` mirroring the `_seen_timestamps` prune in `_prune_old_entries`. Replace the dict-of-lists with a per-key small bounded deque (maxlen=8) since near-duplicate detection only needs the last few occurrences within 0.25s.

---

### [ER-70] — `crash_recovery` atexit handler never unregistered (test-suite accumulation)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `crash_recovery.py:113-124` `__init__` registers a new atexit callback. `shutdown()` stops the worker thread but never unregisters the atexit handler. In a long-running process that creates multiple CrashRecovery instances (in-process restart, test suites), atexit handlers accumulate.
**Root Cause:** Verified — missing `atexit.unregister` in `shutdown()`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_recovery.py`
**Fix:** Store the `_atexit_save` reference on `self` and call `atexit.unregister(self._atexit_save)` in `shutdown()`.

---

### [ER-71] — Sidecar WS no crash-rate limiting on Python side (relies entirely on Rust host for backoff)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `sidecar_ws.py:724-789` `run` — on fatal error it returns exit code 1. The Tauri host (per ADR-0020 §10 / FT-1) respawns the sidecar. The Python side has NO restart-rate limiting, crash counter, or circuit breaker. If the sidecar crashes on every startup (config corruption, missing model file, port collision, import error), the host respawns it in a tight loop — 100% CPU, log file fills at MB/s, battery drains.
**Root Cause:** Suspected — no crash-rate limiting or exponential backoff on the Python side.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
**Fix:** (1) Write a crash counter file that increments on each startup and is cleared after successful `server_started`. If count exceeds N within T seconds, refuse to start and emit `{"event":"crash_loop_detected"}` to stdout before exiting. (2) Add a startup delay proportional to the crash count (exponential backoff: 1s, 2s, 4s, 8s...) before binding the WS server.

---

### [ER-72] — Clipboard `_pending_restores` thread-start leak under resource pressure
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `clipboard/manager.py:652-664` — `paste()` appends to `_pending_restores` BEFORE starting the daemon thread that will remove it. If `.start()` raises (out of thread resources / fd exhaustion), the entry is already in `_pending_restores` but no thread exists to call the finally block. The entry holds `self` (ClipboardManager), `snapshot` (potentially large image/file clipboard content on Windows/macOS), and `expected` (dictated text, privacy-sensitive).
**Root Cause:** Suspected — append happens before `.start()`, and `.start()` failure is not caught.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py`
**Fix:** Wrap the `Thread().start()` call in try/except; on failure, remove the entry from `_pending_restores` under the lock and call `snapshot.restore_now()`. Alternatively, append to `_pending_restores` INSIDE the daemon thread's first action (so a failed start never adds an entry).

---

### [ER-73] — Backend startup: duplicate `is_autostart_enabled()` call; multiple `config.save()` on hot path; `numpy`/`crash_handler` eager imports
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (bundled startup-perf)
**Description:** Multiple startup-path inefficiencies:
- `startup_sequence.py:415-416` — `sync_autostart(app)` calls `is_autostart_enabled()` internally; then `app.tray.set_autostart_enabled(is_autostart_enabled())` calls it AGAIN. Two separate calls to the same platform helper back-to-back on the startup hot path.
- `startup_sequence.py:175, 184, 200, 348` — four distinct `app.config.save()` call sites in the startup body, each doing an atomic write cycle (~5-30ms). Only one typically runs per startup, but the Wayland-warning save at line 348 is the most avoidable.
- `app.py:18, 21` — `import numpy as np` and `from voice_typer.server import crash_handler as _crash_handler` are eager at module top. numpy cold-import is ~50-100ms; crash_handler pulls in ctypes + wintypes and on Windows allocates a WINFUNCTYPE at module load. Both used only later in `__init__` / startup_sequence.
- `crash_handler.py:491-494` — Windows `WINFUNCTYPE` allocation at module load runs at import time.
- `shutdown_controller.py:886-903` `_signal_watcher_loop` — perpetual 1Hz poll using `Event.wait(timeout=1.0)`. The 1s timeout exists "to keep the thread responsive to interpreter shutdown" but on the platforms Voice Typer targets, `Event.wait()` without timeout releases correctly on interpreter shutdown.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/startup_sequence.py`
- `voice_typer/server/app.py`
- `voice_typer/server/crash_handler.py`
- `voice_typer/server/shutdown_controller.py`
**Fix:** (a) Have `sync_autostart` return the post-sync actual state and pass that to `set_autostart_enabled`, dropping the second call. (b) For the Wayland flag (line 348), defer the save to the existing SettingsController.set_config path or coalesce with the onboarding save. (c) Defer `from voice_typer.server import crash_handler as _crash_handler` to inside `VoiceTyperApp.__init__`. (d) Move the WINFUNCTYPE allocation inside `install_crash_handler()`. (e) Use `Event.wait()` (no timeout) for `_signal_watcher_loop` and rely on the `daemon=True` flag for interpreter-shutdown cleanup.

---

### [ER-74] — Idle CPU: macOS PeekMessage 10Hz pump; Linux/macOS fallback 1Hz mic watcher poll
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `microphone_watcher.py:655-666` — Windows `_run_windows_impl` polls `PeekMessage` at 10Hz (100ms wait) even when no device changes occur. The module already implements `PostMessageW(hwnd, WM_QUIT, ...)` in `_post_quit_to_windows` (line 679-692) for stop signaling, which means a blocking `GetMessage` loop would work correctly and be fully event-driven. `microphone_watcher.py:342, 449-479` — Linux path polls `os.listdir("/dev/snd")` at 1Hz; macOS fallback path polls `sd.query_devices()` at 1Hz (heavier — PortAudio device re-enumeration). Already mitigated on macOS by preferring the CoreAudio listener.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py`
**Fix:** (a) Windows: replace `PeekMessage` + `wait(0.1)` pump with blocking `GetMessageW` loop. WM_DEVICECHANGE wakes it on device change; the existing `PostMessageW(hwnd, WM_QUIT, ...)` wakes it for shutdown. **VALIDATE ON WINDOWS HOST** — verify `GetMessageW` pump receives WM_DEVICECHANGE on a real Windows machine. (b) Linux: consider `inotify_simple`/`pyinotify` on `/dev/snd` (low priority — module docstring explicitly rejects this to minimize deps). (c) macOS: ensure pyobjc is a hard dependency so the fallback never runs in production.

---

### [ER-75] — `level_monitor` 50ms worker backstop poll (could be 250-500ms)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `level_monitor.py:1065-1071` — `_level_worker_loop` uses `wait(timeout=0.05)` (50ms backstop). 50ms is the stop-flag poll interval. When the monitor is active, the PortAudio callback's `Event.set()` normally wins (chunks arrive every 10–31 ms), so the timeout rarely fires. But the timeout also serves as the ONLY exit check: if the audio device underflows or stalls (device change, suspend/resume), the worker wakes 20×/sec re-checking the stop flag with an empty ring buffer.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py`
**Fix:** Raise the backstop timeout to 250–500 ms. The stop path already calls `_level_worker_wake_event.set()` so stop latency is unaffected by the timeout value — the timeout only governs the "missed wakeup" recovery interval, which is a rare edge case. A 500 ms backstop cuts idle wakeups 10× with no functional change.

---

### [ER-76] — `text_cleanup.py` O(N×M) phrase correction scan; per-phrase closure allocation
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `text_cleanup.py:622-697` `_correct_whisper_phrases` — runs on every dictation. O(N×M) substring scan over all phrases. For a few hundred phrases × every dictation, this is the heaviest cleanup step. Secondary issue: the closure `_apply_case_preserving_replacement` is re-defined as a NEW function object inside the loop on every matching phrase.
**Root Cause:** Verified — algorithmic complexity is documented but unfixed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py`
**Fix:** Build a single combined alternation regex `(?P<bad>phrase1|phrase2|...)` once at configure time and use a single `pattern.sub(replacer_fn, text)` call with a dict lookup in the replacer. Drops O(N×M) → O(M). Or adopt Aho-Corasick (`pyahocorasick`).

---

### [ER-77] — Long-form Parakeet/Qwen transcription: sequential chunk loop, no batching (~1.5-2× slower on GPU)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `parakeet_engine.py:632-646` `transcribe` long-audio path — `for i, chunk in enumerate(chunks): text = self._transcribe_segment(chunk)`. Each chunk = sequential `model.generate()` call. transformers' `generate()` supports a batch dimension — N chunks could be passed as a single batched forward in one call (after padding to equal length), cutting GPU kernel launch overhead and improving throughput ~1.5-2× on CUDA. Qwen has the same pattern (`qwen_engine.py:437-487`, `_transcribe_chunked`).
**Root Cause:** Verified — sequential chunk loop, no batching.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/qwen_engine.py`
**Fix:** Pad chunks to common length, stack into a `[B, T]` tensor, call `self._processor([chunk_list], ...)` and `self._model.generate()` once. Decode each sequence from `output.sequences`. Requires verifying the Parakeet processor/generate support batched input. **VALIDATE ON CUDA HOST** — verify batched generate works on a real GPU.

---

### [ER-78] — `history_db` one INSERT + one COMMIT per queued row (could batch)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `history_db.py:1347-1364` `add_transcription` `_do_insert` closure — does its own INSERT + COMMIT. Each dictation result enqueues a closure that does its own INSERT + COMMIT. The writer thread drains the queue serially, so N queued items = N transactions. In WAL + synchronous=NORMAL the per-commit fsync cost is amortized, but per-transaction overhead remains.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py`
**Fix:** In `_writer_loop` (or `_submit_write`), peek the queue: if ≥3 inserts are pending, drain them into a single multi-row INSERT inside one transaction: `INSERT INTO transcriptions (...) VALUES (?,?,?...), (?,?,?...)`. Keep the fire-and-forget semantics.

---

### [ER-79] — `credential_store` re-reads config.json per provider at startup (5× for 5 providers)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `credential_store.py:716-749` `_read_plaintext_fallback` — `Config.load()` resolves `keyring://<provider>` references by calling `load_secret()` for each of the 5 providers (openai/groq/deepgram/cloud/llm). When keyring is unavailable (headless Linux, broken D-Bus) OR when a specific provider's secret is missing from keyring, each call re-opens and re-parses the same config.json. Worst case: 5 reads + 5 parses of the same file at startup.
**Root Cause:** Verified — no caching of the parsed config.json across calls within a single `Config.load()` pass.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py`
**Fix:** Cache the parsed config.json in a module-level dict with an mtime check (`os.stat(config_file).st_mtime_ns`), or have `Config.load()` pre-parse config.json once and pass the dict into `load_secret()`.

---

### [ER-80] — `secure_file_io._secure_atomic_write` 2 fsyncs unconditionally (no durability=False escape hatch)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `secure_file_io.py:60-89` `_secure_atomic_write` — every `Config.save()` (and every credential_store._write_plaintext_fallback) pays 2 fsyncs (file data + parent dir). This is the correct POSIX durability pattern, but it is unconditional — there is no `durability=False` escape hatch for non-critical writes.
**Root Cause:** Verified (intentional design for security). The cost is justified for config persistence but is overkill if the same helper is ever reused for cache files or telemetry dumps.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/secure_file_io.py`
**Fix:** No change needed for the current call sites. If future code reuses `_secure_atomic_write` for higher-frequency or non-critical writes, add a `durability: bool = True` parameter that skips both fsyncs when `False`.

---

### [ER-81] — Rust host WS reader: `pending` lock held across oneshot send (convoy on concurrent dispatch)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `src-tauri/src/sidecar/ws.rs:500-505` — every dispatch response frame acquires the async `Mutex` on the pending-requests map, holds it during `pending.remove(&id)` AND `tx.send(v)` (the oneshot send wakes the waiting `dispatch_inner` task, which may immediately try to acquire the SAME lock to insert a new pending request — causing convoy).
**Root Cause:** Verified but minor.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Take the sender out of the map under the lock, then drop the lock, then send: `let tx_opt = { let mut p = pending.lock().await; p.remove(&id) }; if let Some(tx) = tx_opt { let _ = tx.send(v); }`. Sends outside the lock, so concurrent dispatch responses don't convoy.

---

### [ER-82] — Per-dispatch inline imports in `_dispatch` (20-60μs/sec of import overhead)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `ipc_server.py:1621, 1642` — `from voice_typer.server.log import set_correlation_id` / `reset_correlation_id` run on EVERY dispatch that carries an `id` (every request/response frame, i.e. every UI invoke). Python's import system caches modules in `sys.modules`, so this is a dict lookup + attribute access per import — cheap individually (~1 μs) but pure overhead on the hot path. The `str(_req_id)` conversion also allocates a new string per dispatch when `_req_id` is already an int from JSON parsing.

Also `ipc_server.py:1634-1639` — `handler = getattr(self, handler_name)` does string → bound method resolution per call (walks the MRO; IPCServer has 15 mixin bases).
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
**Fix:** Hoist imports to module level (verify by moving and running the test suite). Alternatively, cache the functions on first call. Cache `{cmd_name: bound_method}` on `__init__` (or lazily on first dispatch).

---

### [ER-83] — `sidecar_ws.py` per-event `call_soon_threadsafe` + drop-oldest dance for `bubble_level`
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `sidecar_ws.py:594, 438-447` — for every `event_bus.publish` from a non-loop thread (audio worker, transcription, hotkey), `loop.call_soon_threadsafe` schedules a callback + allocates a `Handle` + wakes the selector. `bubble_level` is published at 15–50 Hz. The `outbound` queue has `maxsize=256` — during a brief UI stall (e.g. renderer GC), the queue saturates and the drop-oldest path runs on EVERY subsequent publish.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
**Fix:** (1) For `bubble_level` specifically, coalesce at the publish source — keep only the latest `bubble_level` in a slot, flush on a 30 Hz timer on the loop thread. (2) Suppress the `log.debug` drop-oldest line (or rate-limit it like `log_rate_limited` in the TCP path). (3) Bump `maxsize` to 512 to absorb transient renderer GC pauses.

---

### [ER-84] — `ipc_server.py` eager `len(str(msg))` per dropped push event (recursive dict stringification)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `ipc_server.py:2202` — when no IPC client is connected, every published event hits this path. `len(str(msg))` calls `dict.__str__` which recursively stringifies EVERY value in the message dict — for `transcription_partial` events this includes the partial text. The rate limiter suppresses 99/100 calls, but `len(str(msg))` is computed EAGERLY as a positional argument BEFORE the rate limiter decides whether to log.
**Root Cause:** Verified — Python evaluates function arguments before the function is called.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
**Fix:** Drop the `size=` hint entirely (the message type is enough for drop-rate diagnosis), OR enhance `log_rate_limited` to accept a callable for the format args, OR compute size only when the rate-limiter decides to actually emit (requires extending the helper signature).

---

### [ER-85] — `ws.rs` reader cleanup unconditional `ws_tx` clear (race with newer connection)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `src-tauri/src/sidecar/ws.rs:636-642` — old reader task cleanup runs on exit (WS close / panic): `{ let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx); *ws_tx_guard = None; }` — unconditional, no generation/reconnect guard. Race window: FT-1 supervisor kills old sidecar, old reader's `read.next()` returns None. Meanwhile, `ft1_respawn_inner` proceeds to spawn a NEW sidecar + `reconnect_ws` which calls `queue_auth_and_store_ws_tx` setting `state.ws_tx = Some(new_sender)`. If the new sidecar starts FAST (< 200ms, possible with prewarming) and `reconnect_ws` sets up the new `ws_tx` BEFORE the old reader's cleanup runs, the old reader's `*ws_tx_guard = None` CLOBBERS the new `ws_tx`.
**Root Cause:** Suspected (race) — the reader cleanup does not check whether a newer `ws_tx` has been installed since this reader was spawned.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Add a `ws_generation: AtomicU64` to `SidecarState`. Increment it in `queue_auth_and_store_ws_tx` after setting the new `ws_tx`. Pass the generation at spawn time to `spawn_reader_task`; the cleanup block should only clear `ws_tx` if `state.ws_generation.load() == my_generation`.

---

### [ER-86] — `bootstrap.ts` `setupErrorHandlers` discards `dispose()` handle (fragile if bootstrap called twice)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `bootstrap.ts:422-425` — production `setupErrorHandlers()` discards the `dispose()` return value. The two `process.on(...)` listeners stay attached for the process lifetime. In normal production this is fine (single install per process), but if `bootstrapRuntime()` were ever called twice — via HMR of the main process, a future re-init path, or tests — each call would add a fresh pair of listeners.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/bootstrap.ts`
**Fix:** Capture the `dispose` handle in a module-scoped var so a second `bootstrapRuntime()` call would dispose the old handlers first.

---

### [ER-87] — Per-cue OscillatorNode/GainNode not explicitly disconnected (relies on GC)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `sound-manager.ts:240-282` `playViaAudioContext` — each `playSoundCue` call creates a fresh OscillatorNode + GainNode pair, connects them to `ctx.destination`, and schedules `osc.stop()` 130–250ms later. The nodes are NOT explicitly `disconnect()`-ed, and no `onended` handler releases them. They go out of scope after `playViaAudioContext` returns, but the AudioContext internally holds a reference to connected nodes until they're disconnected OR until the AudioContext notices they've stopped and reaps them. Modern Chromium does reap stopped+disconnected nodes, but the spec doesn't guarantee prompt GC for connected-but-stopped nodes.
**Root Cause:** Suspected — relies on GC + AudioContext internal cleanup instead of explicit disconnect.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
**Fix:** Add `osc.onended = () => { osc.disconnect(); gain.disconnect(); }` after `osc.stop()`.

---

### [ER-88] — Linear-interp resample fallback has no anti-aliasing filter (silent quality degradation on streaming path)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `resampling.py:241-265` — fallback when scipy unavailable: `np.interp` is pure linear interpolation — NO anti-aliasing filter. For 48k→16k / 44.1k→16k DOWNSAMPLING, energy above 8kHz aliases into the speech band (0-8kHz), degrading ASR accuracy. The streaming/snapshot path (`_resample_chunk`) explicitly disables resample logging (`log_resample=False`), so when scipy is missing, every streaming partial transcription uses linear interp without an anti-aliasing filter, and the user gets no warning. Only the final full transcription (via `_prepare_audio` with `log_resample=True`) would emit a WARNING.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/resampling.py`
- `voice_typer/server/recording/recorder.py` (call sites)
**Fix:** (a) Apply a simple FIR anti-aliasing low-pass filter before decimation in the no-scipy path. (b) Pass `log_resample=True` (or emit a one-time WARNING on first use) from `_resample_chunk` so the streaming path surfaces the quality degradation. (c) Consider emitting an IPC event (`type: "resample_degraded"`) so the UI can warn the user to install scipy.

---

### [ER-89] — Ring buffer overflow detected but not surfaced in real-time (`_dropped_ring_chunks` silent)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `recorder.py:2167-2189` `_audio_callback_dispatch` — RT callback detects ring-buffer full, increments `self._dropped_ring_chunks += 1` and `self._skipped_frames += 1`, then appends the chunk anyway (silently evicting the oldest un-processed audio). There is no real-time IPC event, no log, and no UI notification. The user continues speaking, believing they're being transcribed, while audio is being dropped. The main-buffer overflow DOES log a WARNING (line 2394), but the ring-buffer overflow (the earlier, more critical signal) does not.
**Root Cause:** Verified — silent dropping.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
**Fix:** In `_process_audio_chunk` (worker thread, non-RT-safe to log), check `self._dropped_ring_chunks` delta since last check and emit a rate-limited WARNING + an IPC event (`type: "audio_dropped"`, `data: {chunks: N}`) so the UI can flash a "CPU overload — audio being dropped" indicator.

---

### [ER-90] — Device list 30s staleness when OS mic watcher fails to start
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `device_manager.py:117` — `_device_list_cache_ttl = 30.0`. When the OS-event-driven mic watcher fails to start (macOS without pyobjc, Linux without `/dev/snd`, Windows with a failed window creation), the only fallback is the 30s TTL cache. A newly-plugged USB mic won't appear in the device list for up to 30s, and a newly-removed mic's stale entry persists for up to 30s. This affects device SELECTION (UI list) and the health-checker's `_resolve_device()` (which reads the cache), but NOT the primary disconnect detection.
**Root Cause:** Verified (minor).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/device_manager.py`
**Fix:** When the mic watcher fails to start, reduce the TTL to 5s for the first 60s after failure, then back off to 30s. Or add a separate lightweight device-list refresh thread at 5s when the watcher is None.

---

### [ER-91] — `_buffer_clear_worker` keeps old deque alive 30-283ms during stop (compounds ER-19 peak)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `recording/buffer.py:266-299` — the secure-clear worker keeps the OLD deque alive for ~30–100 ms (16 kHz) / ~85–283 ms (48 kHz) during `stop()`/`discard()`. This is the window that compounds the `stop()` peak (see ER-19).
**Root Cause:** Verified — background zeroing holds reference to the old deque.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/buffer.py`
**Fix:** Drain the deque chunk-by-chunk into a pre-allocated destination ndarray, zeroing + popping each chunk after copy (instead of materializing a full second copy via `np.concatenate` then handing the deque to the background worker). This eliminates the 2×N peak in ER-19 as well.

---

### [ER-92] — Prewarm macOS LaunchAgent: every login pays ~500ms-1s Python cold-start CPU before sentinel check short-circuits
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `prewarm_scheduler_posix.py:96-132` — every login causes the full Python prewarm process to start (cold import of voice_typer.server.config + setup_logging + _fast_startup_enabled + _already_warmed) before the sentinel check at `pipeline.py:116-118` short-circuits. On a laptop where the user logs out/in within the same boot, this pays ~500ms-1s of Python cold-start CPU + disk I/O at every login, even though the warming itself is correctly skipped.
**Root Cause:** Verified — sentinel check happens INSIDE the prewarm Python process, not at the scheduler level.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** Either wrap the plist's ProgramArguments in a tiny shell pre-check that exits 0 if the sentinel file's first line matches `sysctl -n kern.boottime`, OR add `--delay 30` to the plist's ProgramArguments so prewarm starts 30s after login (overlaps with the user's normal post-login activity, less perceptible).

---

### [ER-93] — `kill_process_tree` 200ms unconditional grace sleep even when no descendants
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `src-tauri/src/state.rs:237, 225-247` `kill_process_tree` — `std::thread::sleep(Duration::from_millis(200))` grace period is unconditional even when no descendants exist (empty `all_descendants` → still sleeps 200ms). `kill_tree` is always called in `shutdown_sidecar_for_exit` after the 2s wait, even if the sidecar already exited.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs`
**Fix:** Short-circuit the grace sleep when `all_descendants.is_empty()`. Also consider checking `/proc/<pid>/stat` (Linux) or `waitpid(WNOHANG)` before the SIGKILL loop to skip already-reaped processes.

---

### [ER-94] — Tauri host `trigger_ft1_respawn_off_thread` creates a new OS thread per respawn
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `src-tauri/src/sidecar/ws.rs:158-164` `trigger_ft1_respawn_off_thread` — `std::thread::spawn(move || { tauri::async_runtime::block_on(async move { let _ = ft1_respawn(&app, &state).await; }); })`. Each FT-1 respawn creates a brand-new OS thread (8MB stack reservation on Linux, kernel scheduling cost ~50-200µs). The doc comment explains this is required because `reconnect_ws`'s future is `!Send` (tokio-tungstenite holds a `!Send` across an await), so it can't be `tokio::spawn`'d.
**Root Cause:** Verified design constraint.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Optional polish — use a dedicated long-lived "FT-1 worker" thread started in `.setup()` that receives respawn requests via a `std::sync::mpsc` channel. Eliminates per-respawn thread creation. Not worth the complexity unless profiling shows thread creation is a hotspot (unlikely).

---

### [ER-95] — `_signal_watcher_loop` perpetual 1Hz poll (should be no-timeout wait)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `shutdown_controller.py:886-903` `_signal_watcher_loop` — `while not self._shutdown_signal_event.wait(timeout=1.0): pass`. The watcher thread polls every 1s for the lifetime of the process, even though `Event.wait()` with no timeout would block correctly and wake immediately on `set()`. The 1s timeout exists "to keep the thread responsive to interpreter shutdown on platforms where the underlying lock isn't released automatically" per the comment.
**Root Cause:** Verified — the 1s poll is defensive but wasteful. On the platforms Voice Typer targets (CPython on Windows/macOS/Linux), `threading.Event.wait()` without timeout releases correctly on interpreter shutdown.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`
**Fix:** Use `self._shutdown_signal_event.wait()` (no timeout) and rely on the `daemon=True` flag (already set at line 856) for interpreter-shutdown cleanup. If the defensive timeout is kept, lengthen to 5-10s. (Bundled with ER-73.)

---

### [ER-96] — `_seen_timestamps` rebuilds entire set on every prune pass
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low
**Description:** `streaming.py:321-327` `_prune_old_entries` — every prune rebuilds the entire set rather than mutating in place. For a 30-min streaming session polling at 4 Hz with ~3000 committed words, the set holds up to ~3000 tuples and is rebuilt on every `add_words` call (every ~5s). The old set is held alive until the loop finishes, doubling peak memory transiently.
**Root Cause:** Verified.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/streaming.py`
**Fix:** Use `set.discard()` in a single pass over a snapshot, or track a rolling max-end-seconds and only prune when the set exceeds a size threshold (e.g. > 5000 entries), reducing prune frequency 10×. (Bundled with ER-69.)

---

### [ER-97] — `WS writer` outbound per-frame work (triple encode — see ER-34)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (duplicate of ER-34, kept for tracking)
**Description:** Same as ER-34. Kept as a separate tracking entry because the original sub-agent flagged it from a different angle.
**Progress:** None yet.
**Related Files:** See ER-34.
**Fix:** See ER-34.

---

### [ER-98] — `Level monitor 50ms backstop` (see ER-75)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low (duplicate of ER-75)
**Description:** Same as ER-75. Kept for tracking.
**Progress:** None yet.
**Related Files:** See ER-75.
**Fix:** See ER-75.

---

### Summary

**Total canonical findings: 98 (after dedupe).**
- **Critical (3):** ER-1, ER-2, ER-3
- **High (21):** ER-4 through ER-24 (excluding ER-25 which is Medium)
- **Medium (~30):** ER-25 through ER-63 (and ER-69, ER-88, ER-89)
- **Low (~40):** ER-64 through ER-98

Phase 4 (fix) will address all Critical and High severity findings, plus a curated set of Medium severity findings where the fix is well-scoped and the file-disjoint constraint can be satisfied. Low severity findings are bundled by file area for efficient parallel fixing where scope allows.

---

# Consolidated Comprehensive Review — GROUP 3 (UX & UI) — Session BG

Scope: Group 3 categories only — UX/UI consistency, Ease of use, Accessibility,
User onboarding, User flows, Developer experience, Localization / i18n.

Session prefix: BG. Sub-agent count: 20.

All findings below were filed by BG-R1..BG-R20 review sub-agents (Phase 1) and
deduplicated by the primary agent (Phase 3).

### [DE-23] — credential_store: non-string api_key value crashes migration with AttributeError
**Status:** 🔶 Partial
**Severity:** 🟡 Medium
**Category:** Error handling
**Description:** In `credential_store.py:1031-1035`, `value = data.get(field_name, '')` then `if not value or value.startswith(KEYRING_REF_PREFIX):`. If `data[field_name]` is a non-string truthy value (e.g., a dict, list, or int from a hand-edited or corrupted config.json), `value.startswith(...)` raises `AttributeError`. This line is OUTSIDE the per-provider `try/except` at lines 1049-1071 (which only wraps the `keyring.set_password` call). A single corrupted api_key field aborts the entire migration loop. `migrate_secrets_to_keyring` raises (not caught by the `try/finally` at 967-972), propagates to `Config.load`'s `except Exception` at config.py:1573, which logs a warning and continues — but `secrets_migrated` is never set, so migration retries on every launch, logging a warning each time. The user sees repeated warnings with no path to resolution.
**Root Cause:** Verified: no type guard before .startswith() on a value read from a JSON file that may have been hand-edited or corrupted.
**Progress:** Partial — the `startswith` guard is NOT in place. However the code now benefits from incidental protection: (1) IPC handler `save_secret` calls `validate_non_empty_key(...)` which type-checks the incoming value; (2) tokenizer/backend selection validates `get_secret(key)` returns a non-empty string; (3) `str_fields` dict has a `normalize_value` that logs unsupported types. These are incidental layers, not a deliberate fix for the migration path. The `migrate_secrets_to_keyring` loop itself still lacks the `isinstance(value, str)` guard, and `store_secret` in `credential_store.py` calls `len()` on values inside the `except Exception` block with no type check on the caught value.
**Related Files:**
- `voice_typer/server/credential_store.py`
**Fix:** Add `if not isinstance(value, str):` guard before `.startswith()`, or wrap the entire per-provider block (including the `value` check) in the existing `try/except`. Add a test that loads a config with a non-string api_key value and verifies migration skips it gracefully (logs warning, continues with other providers).

---

### [DE-52] — recording_controller: stop() streaming session signalled but not cleared — leaked session
**Status:** ⚠️ Partial (documented limitation — full fix requires cross-file restructure)
**Severity:** 🟡 Medium
**Category:** Error recovery & resilience / Concurrency
**Description:** In `recording_controller.py:482-486` (`stop()`), `session = self.get_streaming_session(); if session is not None: with contextlib.suppress(Exception): session._cancel_event.set()`. The session is signalled but NOT removed from `self._streaming_session`. The session's worker thread keeps running until it observes the cancel at its next checkpoint. If the transcription pipeline does not call `_cancel_streaming_session()` in its finally block, the session reference is held until the next `start()` (which calls `set_streaming_session(None)` at line 714) or shutdown. During that window: the session's worker thread is alive, holding references to the transcriber and audio chunks. A concurrent `pop_streaming_session()` would return the session and call `session.cancel()`, which joins the thread — but the thread may be stuck in `transcribe_words`, blocking cancel.
**Root Cause:** Verified: stop() signals cancel_event but does not pop the session from _streaming_session.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_controller.py`
**Fix:** Use `pop_streaming_session()` here too (atomic get-and-clear), then set the cancel event on the popped session. This matches the ARCH-018 pattern used in `_cancel_streaming_session`. Add a test that calls stop() and verifies the session is popped.

---

### [GT-31] — pyrefly-baseline.json declares errors:[] while inline comment documents 116 real errors
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** The baseline file declares errors: [] (zero errors) while the inline comment documents 116 real pyrefly diagnostics, ~34 of which are non-platform-specific real type bugs (8 bad-argument-type, 7 unbound-name, 5 bad-return, 4 bad-index, 3 missing-argument, 2 not-callable, 2 unsupported-operation, 1 bad-assignment, 1 bad-unpacking, 1 no-matching-overload). The CI audit step only fails on growth vs the base ref, so these 116 errors are silently grandfathered — there is no enumerated list of which file:line each error lives at.
**Root Cause:** Verified: baseline errors array is empty; inline comment lists 116 errors with categorization.
**Progress:** None yet.
**Related Files:**
- `pyrefly-baseline.json:6 ("errors": [])`
**Fix:** Regenerate the baseline as an enumerated JSON list (file + line + rule + message). Or run pyrefly check voice_typer/ --output-format=json once and commit as pyrefly-current.json. At minimum, list the ~34 non-platform-specific errors as line items in this baseline file's _triage array so they can be assigned and fixed one-by-one.
**Severity:** 🔴 High

### [GT-34] — Rust paste_text + paste.rs module dead in production (~440 LOC) — Python owns the paste path
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** The paste_text #[tauri::command] + the entire paste module (~440 LOC total) are dead in production. The doc comment on paste_text itself states: 'the Python sidecar does its OWN paste internally in voice_typer/server/dictation_pipeline.py:990-1010 via self._app.clipboard.paste(...)'. Grep confirms: no Python code publishes a paste_text event, and no TS code invokes invoke('paste_text', ...). The command is retained only so the migration glue tests keep passing and for DevTools-only manual driving.
**Root Cause:** Verified: Python sidecar owns production paste path; Rust paste_text + paste.rs is parallel implementation kept alive by test source-grep assertions.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:566-644`
- `src-tauri/src/commands/paste.rs:1-417 (entire module)`
**Fix:** Pick one: (a) delete paste.rs, commands::paste mod declaration, the paste_text #[tauri::command], and its generate_handler! entry in main.rs:181; replace migration-glue tests' source-grep with a behavioral stub; OR (b) flip ownership — delete dictation_pipeline.py::_dispatch_paste and let Rust own the paste path.
**Severity:** 🔴 High

### [GT-38] — Test reliability: cleanup tests mock every collaborator — RW-3 claims verified at call-routing level only
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** _stub_restart_environment replaces EVERY cleanup collaborator with a MagicMock, then tests assert the production code called .flush() / .stop() / .shutdown(). This verifies CALL ROUTING only — it does NOT verify that (a) a real history_db.flush() actually drains pending SQLite writes, (b) recorder.stop() actually closes a real PortAudio stream, (c) crash_recovery.flush(timeout=2.0) actually blocks until the save queue drains, (d) the ordering of these calls is safe under real I/O latency. A regression where history_db.flush() is changed to a fire-and-forget non-blocking call would still pass these tests, but in production would silently lose pending writes on restart — exactly the bug RW-3 claims to fix.
**Root Cause:** Verified: every collaborator is a MagicMock; assertions verify .flush.assert_called_once().
**Progress:** None yet.
**Related Files:**
- `tests/test_app_cleanup.py:62-102 (_stub_restart_environment)`
**Fix:** Add a small set of integration tests that use a real HistoryDB (tmp_path SQLite file) with a populated writer queue, call app.restart_app(), and assert the SQLite file on disk contains the rows after restart completes. Similarly, use a real PortAudio stub (or pyaudio mock that records stream.close() calls) to verify the stream is actually closed.
**Severity:** 🔴 High

### [GT-39] — Test reliability: TestConcurrentConfigWritesNoCorruption relies on GIL, not _config_mutation_lock
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** The test explicitly does NOT acquire _config_mutation_lock and relies on the GIL for atomicity of cfg.hotkey = val. The production code's _config_mutation_lock contract (RACE-011 / ADR 0008 §3.1) is NOT exercised. The test would still pass if production removed _config_mutation_lock entirely, because the GIL still serializes the attribute writes.
**Root Cause:** Verified: test setter does not acquire _config_mutation_lock; relies on GIL atomicity.
**Progress:** None yet.
**Related Files:**
- `tests/regressions/concurrency_test.py:302-328`
**Fix:** Drive the real IPC path — dispatch 8 concurrent set_config IPC commands through a real IPCServer (using the live_server fixture from tcp_live_test.py), each setting a different field, then assert the final config has all 8 fields set consistently. Or, at minimum, acquire _config_mutation_lock in the test setter and verify a second concurrent acquirer blocks.
**Severity:** 🔴 High

### [GT-40] — Test reliability: No behavioral test of sidecar crash detection / restart loop
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** The ONLY test of sidecar-crash detection is a source-string check that reads start-python.ts and asserts the literal `pythonProcess.on('exit'` or `proc.on('exit'` substring is present, plus app.quit. No test: spawns a real Python sidecar subprocess, kills it, and verifies the parent detects the exit within a bounded time and triggers the restart/quit path. A refactor that renames proc to pythonProc would break the source-string test; but a refactor that keeps the substring while breaking the behavior would pass.
**Root Cause:** Verified: only test is source-string check; no behavioral subprocess test.
**Progress:** None yet.
**Related Files:**
- `tests/regressions/crash_recovery_test.py:36-84`
**Fix:** Add a behavioral test that spawns a real Python sidecar (using the actual entry point), waits for it to bind the IPC port, sends SIGKILL, and verifies the parent process detects the exit within a bounded time and triggers the restart/quit path.
**Severity:** 🔴 High

### [GT-68] — Cross-process log correlation impossible — no shared session/launch ID
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** No shared correlation ID between Rust host and Python sidecar. The only shared identifier across the WS boundary is the bearer token, which is explicitly never logged (security). Two log streams exist (Rust rotating file + Python rotating file) with no join key. A dispatch request id (e.g. id=42) is logged on the Rust side but the Python side's matching log line does not echo the id — it logs the command name only. Wall-clock skew between the two processes makes timestamp correlation fragile.
**Root Cause:** Verified: no shared non-secret session/launch ID passed from Rust host to Python sidecar.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/util.rs:72-76 (generate_token)`
- `src-tauri/src/sidecar/spawn.rs:79-84 (env vars)`
- `src-tauri/src/platform/logging.rs:30 (Rust log path)`
**Fix:** Generate a short non-secret session ID (e.g. 8 hex chars) at startup, pass it as VOICE_TYPER_SESSION_ID env var to the sidecar, and have BOTH hosts include it in every log line (Rust: append to CombinedLogger format string; Python: append to the logging.Formatter). The session ID is also useful for crash-report correlation.
**Severity:** 🟡 Medium

### [GT-69] — app._shutting_down read without lock in dispatch gate — TOCTOU on shutdown
**Status:** ⚠️ Skipped — Duplicate of GT-45
**Description:** Covered by GT-45 (same root cause). _shutting_down flag read without lock; gap between read and handler invocation. See GT-45 for fix.
**Root Cause:** Same as GT-45.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py:1586`
- `voice_typer/server/sidecar_ws.py:319`
**Fix:** Same as GT-45.
**Severity:** 🟡 Medium

### [GT-73] — FT1_MAX_RETRIES constant relocated to test module — pinned by stale Python regex tests
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** The constant was relocated from module scope to inside the test module (PVT-G5-089) because no runtime code path reads it (the in-loop retry cap was removed in NF-R19-2). The constant survives ONLY so the Python regex r'pub\(crate\)\s+const\s+FT1_MAX_RETRIES\s*:\s*u32\s*=\s*(\d+)' in tests/tauri/mig15/test_shutdown_windows.py:375 keeps matching (the regex is not anchored to start-of-line so it matches inside the test module too). The real retry-count source-of-truth is FT1_BACKOFF_MS.len() (= 5).
**Root Cause:** Verified: a Python source-grep test pins the existence of a Rust constant that no runtime code reads.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/util.rs:15-21, 138-144`
- `tests/tauri/mig15/test_shutdown_windows.py:375`
**Fix:** Update tests/tauri/mig15/test_shutdown_windows.py:375-418 and the parallel mig16/mig17 tests to assert FT1_BACKOFF_MS.len() == 5 directly (the test at line 398 already does this). Drop the FT1_MAX_RETRIES regex assertion. Then delete the constant from util.rs:144.
**Severity:** 🟡 Medium

### [GT-74] — 5 #[allow(unused_imports)] on dead re-exports in commands/mod.rs
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** 5 #[allow(unused_imports)] annotations on pub use / pub(crate) use re-exports. Grep for commands::dispatch|commands::paste_text|commands::shutdown_sidecar|commands::export_history|... in src-tauri/src/ returns ZERO matches — main.rs, tray.rs, ws.rs all use the qualified module path, bypassing the re-exports entirely. The comment claims the re-exports are needed for tauri::generate_handler! macro expansion but the macro actually resolves through the use path at the call site.
**Root Cause:** Suspected: stale over-cautious re-exports from an earlier macro-resolution concern. #[allow(unused_imports)] is suppressing real dead re-exports.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/mod.rs:31-52`
**Fix:** Delete all 5 pub use / pub(crate) use re-export blocks + their #[allow(unused_imports)] annotations. Confirm cargo check passes (if generate_handler! truly needs them, the compiler will surface E0425 / unresolved-name errors at the main.rs:179-202 call site). Keep only pub(crate) mod paste;/bubble;/etc. module declarations.
**Severity:** 🟡 Medium

### [GT-75] — WS legacy event aliases 'relaunch_electron' and 'electron_notification' still in allowlist
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** Line 60-61 of the same file reinforces: 'Drop the legacy electron_notification alias after one release cycle with no rolling-upgrade traffic.' Cargo.toml:3 declares version = '1.0.0' — the rolling-upgrade window referenced has presumably closed. Two extra entries in a security-relevant allowlist (G4-H-32 defense-in-depth surface). A compromised sidecar can still emit relaunch_electron / electron_notification events that the host forwards to the renderer. No code listens for these events anymore.
**Root Cause:** Verified: scheduled-deprecation entries still present past their stated cutoff.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs:91-92 (ALLOWED_EVENT_TYPES legacy-alias block)`
**Fix:** Delete both entries. Run tests/tauri/mig19/test_wire_swap_recovery.py to confirm the parity test still passes.
**Severity:** 🟡 Medium

### [GT-81] — FT1_MAX_RETRIES doc comment mislabels 'FT-1 5 retries' — actual is FT1_BACKOFF_MS.len()
**Status:** ⚠️ Skipped — Duplicate of GT-73
**Description:** Covered by GT-73 (same root cause). The named constant FT1_MAX_RETRIES is misleading documentation; the real source-of-truth is FT1_BACKOFF_MS.len(). See GT-73 for fix.
**Root Cause:** Same as GT-73.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs:241-244`
- `src-tauri/src/sidecar/supervisor.rs:413-419`
**Fix:** Same as GT-73.
**Severity:** 🟡 Medium

### [GT-86] — Relaunch-app.ts has 5 silent catch swallows around cleanup — failures invisible
**Status:** ❌ Not Fixed (deferred — agent timed out or out of scope)
**Description:** 5 silent swallows in one file, around pythonProcess.kill(), tcpSocket.destroy(), mainWindow.loadURL()/loadFile(), and the second kill/destroy pair. If state.pythonProcess.kill() throws ESRCH (process already dead), or state.tcpSocket.destroy() throws, or the renderer reload throws — there's no log entry at any level. A 'Restart' that silently fails to kill the old Python (e.g. because the PID was reused) leaves a zombie backend holding the single-instance mutex — and the relaunch completes 'successfully' with zero diagnostic trace.
**Root Cause:** Verified: 5 `} catch {` blocks with no logging in relaunch-app.ts.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/relaunch-app.ts:78, 86, 132, 176, 182`
**Fix:** Import log from ../logging and replace each `catch {}` with `catch (e) { log.warn('[RESTART] <step> failed:', e); }`. The cleanup continues (best-effort intent preserved), but the failure is now observable.
**Severity:** 🟡 Medium

### [WR-4] — Broken parakeet unload test + AssertionSwallowing + audio_test.py spaghetti
**Status:** ⚠️ Partial
**Description:** `tests/regressions/gpu_memory_release_test.py:157-175` `test_parakeet_unload_invokes_release` patches `voice_typer.server.transcription.release_gpu_memory` but `parakeet_engine.py:1027` does a local `from voice_typer.server.asr_utils import release_gpu_memory` — so the mock is never invoked, and the test silently passes (or fails confusingly). The `if False else` construct on lines 170-172 is debugging leftover; the `monkeypatch` parameter on line 157 is unused. `tests/regressions/audio_test.py:995-1017` `test_resolve_returns_tuple_with_native_rate` wraps its assertion in `try/except Exception: pass`, swallowing its own `AssertionError` — the test is a no-op. `tests/regressions/audio_test.py` itself is 1296 lines mixing 6+ unrelated domains (audio callback, VAD, filters, device enumeration, sample-rate, streaming text assembler, recording-controller sessions, backpressure, clipping IPC, dead-field removal pins, cross-module test introspection) — a textbook Rule 20 spaghetti candidate. `tests/regressions/concurrency_rms_test.py:51-115` tests reimplement the production RMS-suppression logic instead of invoking it — tautological.
**Root Cause:** A stale comment in `parakeet_engine.py:1022-1026` claims tests patch `transcription.release_gpu_memory`, but the local import resolves from `asr_utils`. The broad `except Exception: pass` in audio_test was likely added to tolerate "needs more setup" cases but catches the assertion itself. The audio_test.py monolith grew by concatenation (a `# === Source: tests/test_changes3_fixes.py ===` marker at line 591 confirms it).
**Progress:**
- Fix `test_parakeet_unload_invokes_release` to patch `voice_typer.server.asr_utils.release_gpu_memory` (the canonical source). (DONE)
- Remove the `if False else` debugging leftover and the unused `monkeypatch` parameter. (DONE)
- Update the stale comment in `parakeet_engine.py:1022-1026` to clarify the local import resolves from `asr_utils`. (DONE)
- Fix `test_resolve_returns_tuple_with_native_rate` to remove the broad `except Exception: pass` and assert explicit tuple shape. (DONE)
- Spaghetti split of `audio_test.py` into a `tests/regressions/audio/` package: DEFERRED (large mechanical refactor; would conflict with parallel sessions).
**Related Files:**
- `tests/regressions/gpu_memory_release_test.py`
- `tests/regressions/audio_test.py`
- `voice_typer/server/parakeet_engine.py`
**Fix:** See Progress above.
**Severity:** 🔴 High

---

### [WR-9] — Monolith test files + stray real_torch marker + real network egress in cloud_engines tests
**Status:** ⚠️ Partial
**Description:** `tests/test_clipboard_win32_coverage.py` (1775 lines, 15 test classes) and `tests/test_config.py` (1133 lines, 13 test classes) are textbook Rule 20 spaghetti monoliths mixing many unrelated concerns. `tests/test_config_editor_lock.py:41` carries a stray `pytestmark = pytest.mark.real_torch` that forces a ~17-second real-torch import on every test in the file — but no test in the file uses torch. 9 clipboard test files duplicate the `sys.modules.setdefault("pynput", MagicMock())` block (redundant given the autouse `mock_heavy_imports` fixture in `tests/conftest.py:232-251`). `_make_cm` / `_make_snapshot` helpers are byte-for-byte duplicated between `test_clipboard_borrow_restore.py` and `test_clipboard_paste_restore.py`. 60+ inline `monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)` calls duplicate the existing `tmp_config_dir` fixture from `tests/conftest.py:425-429`. `tests/test_cloud_engines.py` has 3 tests (`test_openai_default_url_allowed`, `test_localhost_self_hosted_allowed`, `test_accepts_valid_model_name`) that make REAL network egress to api.openai.com, localhost:11434, and api.deepgram.com — flaky and risks real cost incursion.
**Root Cause:** Organic growth — each new clipboard test file copy-pasted the `sys.modules.setdefault` block. The `real_torch` marker was copy-pasted from `test_dictation_pipeline_review_fixes.py`. The cloud_engines tests were written before the redaction tests established the `_opener.open` patch pattern.
**Progress:**
- Delete the stray `pytestmark = pytest.mark.real_torch` from `test_config_editor_lock.py:41`. (DONE)
- Patch `voice_typer.server.cloud_engines._opener.open` with `side_effect=URLError("test-isolated")` in the 3 cloud_engines tests; assert the call was made with the expected URL. (DONE)
- Spaghetti split of `test_clipboard_win32_coverage.py` + `test_config.py`: DEFERRED (large mechanical refactor).
- Consolidate duplicated helpers / fixtures: DEFERRED.
**Related Files:**
- `tests/test_config_editor_lock.py`
- `tests/test_cloud_engines.py`
**Fix:** See Progress above.
**Severity:** 🔴 High

---


### [WR-10] — Misnamed e2e tests + unmarked slow/stress tests + grab-bag history_and_models
**Status:** ❌ Not Fixed — test_e2e_*.py files are at tests/ root (discoverable by pytest); moving to tests/regressions/ requires CI test-discovery updates; cosmetic only
**Description:** test_e2e_*.py files are misnamed (e.g., test_e2e_pipeline.py vs test_e2e_smoke.py); some test files mix unit + integration + stress tests without markers; test_history_and_models.py is a 1180-LOC grab-bag.
**Root cause:** Organic growth without naming conventions.
**Progress:** Verified — no naming changes since filing.
**Related Files:**
- tests/test_e2e_pipeline.py
- tests/test_e2e_smoke.py
- tests/test_history_and_models.py
**Fix:** Rename to tests/slow/, tests/stress/, etc. Or add pytest markers. Cosmetic.
**Severity:** Medium

---


### [WR-14] — Stale ruff baseline + empty pyrefly baseline + real bugs hidden
**Status:** ⚠️ Partial (verified on Linux sandbox; ruff-baseline.json regenerated; tests/test_ruff_ratchet.py::TestCompareLogic updated to use _pick_representative_rule helper; tests/test_dead_code_stays_removed.py verified; AC-128 deferred)
**Description:** `ruff-baseline.json` has 180 entries across 23 rules, but a fresh `ruff check voice_typer/ tests/ scripts/ conftest.py` returns only 21 violations (3 E501 + 18 SIM105) — **159 of 180 baseline entries are stale** (phantom violations that no longer exist). The ratchet (`scripts/ruff_ratchet_check.py compare`) only fails if counts GROW above baseline, so contributors could re-introduce up to 27 N806 violations, 22 E731 violations, 19 F841 violations, etc. silently. Worse, the baseline tracks F-rules (F401=3, F821=1, F841=19) which `docs/ruff-ratchet.md §"Step 1"` says must HARD-FAIL with zero tolerance — but the ratchet script doesn't special-case F-rules. `tests/test_ruff_ratchet.py:320-351` runs ruff against `voice_typer/server/` only (3 violations) but compares against the 180-violation baseline that targets `voice_typer/ tests/ scripts/ conftest.py` — the test scope and baseline scope are disjoint, so the test always passes with "improved by 177". `pyrefly-baseline.json` has an empty `errors: []` array — the file is vestigial; the actual ratchet is implemented by CI comparing pyrefly output to the git base ref. The metadata comment claims "actual error count is 116" but the actual count is 155. Sample of 5 real bugs hidden by the empty pyrefly baseline:
- `voice_typer/server/clipboard/__init__.py:189,191,200,201` — 4 `bad-dunder-all` errors: `_have_wl_clipboard_cache`, `_have_wl_clipboard_cache`, `reset_have_wl_clipboard_cache`, `reset_have_wtype_cache` listed in `__all__` but NOT defined (verified via grep — only `_have_wl_clipboard` and `_have_wtype` without `_cache` suffix exist in `linux.py`).
- `voice_typer/server/service/model.py:1079` — `unbound-name`: `download_id` is referenced on a path where it has not been assigned.
- `voice_typer/server/service/dictation.py:38` — `unknown-name`: `ForceCancelResult` not imported.
- `voice_typer/server/service/status.py:22` — `unknown-name`: `StatusResponse` not imported.
- `voice_typer/server/hotkeys/windows_native.py:574` — `not-callable`: calling a value inferred as `None`.
- `voice_typer/server/model_manager.py:363` — `not-callable`: calling a value inferred as `list[str]`.

`tests/test_dead_code_stays_removed.py:536-543` `TestRendererAllowlist.test_allowlist_includes_test_llm_connection` reads `voice_typer/client/src/main/index.ts` and asserts `"test_llm_connection"` is in source — but the allowlist was moved to `voice_typer/client/src/main/allowed-commands.ts:123` per CR-063; the test is stale and would fail if run. `voice_typer/server/cloud_engines.py:327` has `local_engine_factory: "callable | None" = None` — `callable` (lowercase) is the Python builtin function, not a type; correct form is `Callable[..., Any] | None`.
**Root Cause:** The ruff baseline was regenerated on 2026-07-22 but parallel fix-agents have since cleaned up 159 violations; the baseline was never re-regenerated to lock in the gains. The pyrefly baseline was emptied in S1-CR-30 (2026-07-24) when the CI model changed to git-ref comparison. The `TestRendererAllowlist` test was written when the allowlist was inline in `index.ts`; CR-063 extracted it but didn't update the test.
**Progress:**
- Regenerate `ruff-baseline.json` to lock in the actual 21-violation floor. (DONE)
- Remove F-rules (F401, F821, F841) from `ruff-baseline.json` `by_rule` (set to 0). (DONE)
- Update `tests/test_ruff_ratchet.py:320-351` to run ruff against the full scope `voice_typer/ tests/ scripts/ conftest.py` (matching the baseline `_target`). (DONE)
- Fix `tests/test_dead_code_stays_removed.py:539` to read `voice_typer/client/src/main/allowed-commands.ts` instead of `index.ts`. (DONE)
- Fix the 4 broken `__all__` entries in `clipboard/__init__.py` (remove dead names). (DONE)
- Add missing imports for `ForceCancelResult` and `StatusResponse`. (DONE)
- Fix the `"callable | None"` annotation in `cloud_engines.py:327` → `Callable[..., Any] | None`. (DONE)
- Add None/type guards at `windows_native.py:574` and `model_manager.py:363`. (DONE — added `if x is not None:` guard and `if callable(x):` guard respectively)
- Fix `unbound-name` of `download_id` in `service/model.py:1079`. (DONE)
- Populate `pyrefly-baseline.json` `errors` array OR add a `tests/test_pyrefly_ratchet.py`: DEFERRED (large; the current CI git-ref comparison model is workable).
- Remove dead `# type: ignore[no-untyped-def]` suppressions: DEFERRED (low priority).
**Related Files:**
- `ruff-baseline.json`
- `tests/test_ruff_ratchet.py`
- `tests/test_dead_code_stays_removed.py`
- `voice_typer/server/clipboard/__init__.py`
- `voice_typer/server/service/model.py`
- `voice_typer/server/service/dictation.py`
- `voice_typer/server/service/status.py`
- `voice_typer/server/cloud_engines.py`
- `voice_typer/server/hotkeys/windows_native.py`
- `voice_typer/server/model_manager.py`
**Fix:** See Progress above.
**Severity:** 🔴 Critical

---

### ZR-7 — Triple-duplicated `#![warn(clippy::all, ...)]` attribute in main.rs (also declared in Cargo.toml)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/main.rs:61-68` contains the IDENTICAL inner-attribute line three times:
```rust
// GT-D3-6: project-wide clippy lint gate.
#![warn(clippy::all, clippy::cast_possible_truncation, clippy::unwrap_used)]

// GT-D3-6: project-wide clippy lint gate.
#![warn(clippy::all, clippy::cast_possible_truncation, clippy::unwrap_used)]

// GT-D3-6: project-wide clippy lint gate.
#![warn(clippy::all, clippy::cast_possible_truncation, clippy::unwrap_used)]
```

The same lints are ALSO declared in `Cargo.toml:111-116`:
```toml
[lints.clippy]
all = "warn"
cast_possible_truncation = "warn"
unwrap_used = "warn"
```

So the lint configuration is declared FOUR times total.
**Root Cause:** Multiple sub-agents (GT-D3-6) added the same `#![warn(...)]` line without checking if it was already present. The `[lints.clippy]` Cargo.toml table was added as a fourth declaration in a later cleanup, but the redundant `#![warn(...)]` attributes were never deleted.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/main.rs` (lines 61-68)
- `src-tauri/Cargo.toml` (lines 111-116)
**Fix:** Delete the three `#![warn(...)]` blocks at `main.rs:61-68` (and their comments). Rely on `[lints.clippy]` in `Cargo.toml` as the single source of truth.
**Severity:** 🟢 Low — 9 lines of dead source text; no functional effect (Rust dedupes identical lint attributes); minor contributor confusion.

---

### ZR-8 — `bubble_set_position(x: Value, y: Value)` leaky abstraction (x/y are actually positional keywords)
**Status:** ❌ Not Fixed
**Description:** The Rust command `bubble_set_position` takes TWO arguments `x: Value, y: Value` (bubble.rs, signature shown via bridge comment at `bubble-namespace.ts:146-149`). The renderer's `setPosition(position: string)` accepts ONE string (`"top"` | `"bottom"`), and the bridge is forced to forward it as BOTH x and y:
```ts
setPosition: (position: string) => {
    tauri.core.invoke("bubble_set_position", {
        x: position,   // same string in both slots
        y: position,
    })
}
```
The Rust command then parses the `"top"`/`"bottom"` strings server-side. The leaky abstraction: the Rust API was designed for x/y coordinates, but the renderer only has a positional keyword. The bridge papers over the mismatch by duplicating the string. The type `Value` (not `String`) on the Rust side also loses type safety.
**Root Cause:** API design mismatch between Rust (x/y coordinates) and renderer (positional keyword). Rather than refactor the Rust API to `bubble_set_position(position: String)`, the bridge duplicates the string into both slots.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble.rs` (`bubble_set_position` signature)
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts` (lines 84-95, 146-159)
**Fix:** Change the Rust command signature to `bubble_set_position(position: String)` and parse the keyword server-side. Update the bridge to `invoke("bubble_set_position", { position })`. Or, if coordinate support is actually planned, keep two separate commands (`bubble_set_position_keyword` + `bubble_set_position_coords`) instead of overloading one with `Value` args.
**Severity:** 🟢 Low — working but suboptimal; confuses Rust-side readers; prevents future extension.

---

### ZR-9 — `spawn_heartbeat_task` uses `blocking_lock()` from tokio worker thread (latent deadlock risk)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/sidecar/ws.rs:781, 847` (`heartbeat_state.heartbeat_handle.blocking_lock()` called from `spawn_heartbeat_task`). `blocking_lock()` on a `tokio::sync::Mutex` is documented as "blocking" and MUST NOT be called from an async runtime worker thread — it stalls the worker.

`spawn_heartbeat_task` is called from `reconnect_ws` (ws.rs:878). `reconnect_ws` is called from three sites:
1. `main.rs:402` — inside `tauri::async_runtime::spawn(async move { ... })` (TOKIO WORKER)
2. `supervisor.rs:377` — inside `respawn_inner`, called from `respawn`, called from `trigger_respawn_off_thread` (ws.rs:162-172) which uses `std::thread::spawn` + `block_on` (STD THREAD — safe for blocking_lock)
3. `ws.rs:391` (cleanup_and_trigger_respawn) — same `trigger_respawn_off_thread` path (STD THREAD — safe)

For path 1 (initial cold start), `reconnect_ws` runs on a Tokio multi-threaded runtime worker. `blocking_lock()` would block that worker thread until the async mutex is released.
**Root Cause:** `spawn_heartbeat_task` was originally written for the supervisor's std-thread path (where `blocking_lock` is correct). When `reconnect_ws` began to be called from `main.rs`'s tokio-spawned setup task (path 1), the `blocking_lock` call wasn't reviewed for the new context.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs` (lines 758-850)
- `src-tauri/src/main.rs` (line 402)
- `src-tauri/src/sidecar/supervisor.rs` (line 377)
**Fix:** Change `spawn_heartbeat_task` to be `async fn` and use `heartbeat_state.heartbeat_handle.lock().await` instead of `blocking_lock()`. Its caller `reconnect_ws` is already async, so the change is local. Alternatively, use `tokio::task::spawn_blocking` for the take+abort+store sequence (it's <10µs of work).
**Severity:** 🟢 Low — latent; works today because `rt-multi-thread` is on and the lock hold time is short. Becomes a real deadlock risk if runtime configuration ever changes.

---

### ZR-11 — 7 backend modules look up symbols via `_app_module.X` indirection (inverted dependency tree)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/shutdown_controller.py:764, 787, 1203, 1258`, `voice_typer/server/single_instance.py:156, 538`, `voice_typer/server/settings_controller.py:76, 92`, `voice_typer/server/startup_tasks.py:83, 221` all do:
```python
from voice_typer.server import app as _app_module

# ...
_app_module._clear_backend_pid_file()
_app_module._config_dir()
_app_module.is_windows()
```

The names being looked up via `_app_module.X` have canonical homes in leaf modules:
- `_config_dir()` → defined in `config.py:285`, re-exported via `app.py:44`
- `_clear_backend_pid_file()` → defined in `single_instance.py:173`, re-exported via `app.py:1299`
- `_close_devnull_files()` / `_register_devnull_file()` → defined in `log.py`, re-exported via `app.py:52-57`
- `is_windows()` → defined in `platform_utils.py`, re-exported via `app.py:60`

Critically, `shutdown_controller.py:764` reaches through `app` to call `_clear_backend_pid_file` — a function defined in `single_instance.py`, the very module shutdown_controller could import directly.
**Root Cause:** Tests were written to patch the `app` module's namespace (the most discoverable surface), and the production code was refactored to defer symbol resolution through `app` to honor those patches. This inverted the dependency direction: leaf modules now depend on `app` (the orchestrator) for symbol resolution, instead of importing from their canonical leaf homes.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py` (lines 764, 787, 1203, 1258)
- `voice_typer/server/single_instance.py` (lines 154-156, 538)
- `voice_typer/server/settings_controller.py` (lines 76, 92)
- `voice_typer/server/startup_tasks.py` (lines 83, 221)
**Fix:** Migrate test monkeypatches to patch the canonical modules (`monkeypatch.setattr(voice_typer.server.config._config_dir, ...)`, etc.), then replace all `_app_module.X` lookups with direct imports from the canonical modules.
**Severity:** 🔴 High — hidden coupling (every backend module transitively depends on `app.py`); import cycles masked by call-time imports; `single_instance.py` (a leaf) imports `app` (orchestrator) — backwards layering.

---

### ZR-14 — `recording/__init__.py` (447 LOC) + `prewarm/__init__.py` + `server_platform/__init__.py` are 500 LOC of test-patch compat shim
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/__init__.py:321-446` (and mirrored in `prewarm/__init__.py`, `server_platform/__init__.py` per the docstring at `recording/__init__.py:42-47`) installs a custom module class:
```python
class _RecordingModule(sys.modules[__name__].__class__):
    def __getattr__(self, name):
        if name in _MUTABLE_RESAMPLING:
            from . import resampling as _r

            try:
                return getattr(_r, name)
            except AttributeError:
                raise AttributeError(...) from None
        # ... similar for _MUTABLE_BUFFER

sys.modules[__name__].__class__ = _RecordingModule
```
This ~125-line custom module subclass exists purely so test code like `monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)` propagates to the `resampling` submodule's globals. The docstring admits: "All three packages together account for ~500 LOC of `__init__.py` boilerplate that exists purely for test-patch compatibility."

The TODO at `recording/__init__.py:49-61` acknowledges this is open tech debt requiring migration of 90-150 test files (CR-67).
**Root Cause:** Tests were written to patch the package namespace rather than the submodule that owns the global. When the package was split into submodules, the test patches would have silently no-op'd without this routing shim.
**Progress:** None yet — migration is large (90-150 test files per package).
**Related Files:**
- `voice_typer/server/recording/__init__.py` (447 lines)
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/server_platform/__init__.py`
**Fix:** Migrate test patches to target submodules directly (`monkeypatch.setattr("voice_typer.server.recording.resampling._resample_poly_error", ...)`). Once all test sites are migrated, delete `_RecordingModule`, `_MUTABLE_RESAMPLING`, `_MUTABLE_BUFFER`, and the `sys.modules[__name__].__class__ =` install. Tracked as CR-67.
**Severity:** 🟡 Medium — ~500 LOC of production boilerplate exists only for test compatibility; new mutable globals added to submodules are NOT automatically routed (silent breakage).

---

### ZR-16 — `DictationPipeline` is a god-class facade reaching through `self._app: Any` for every dependency
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:102-117, 119-145`:
```python
class DictationPipeline:
    def __init__(self, app: Any):
        self._app = app
```
The class holds `self._app: Any` and reaches back through it for every dependency: `self._app.config`, `self._app.recorder`, `self._app.models`, `self._app.tray`, `self._app.history_db`, `self._app._crash_recovery`, `self._app._waveform_bubble`, `self._app._llm_polisher`, `self._app._template_manager`, `self._app._vocabulary_manager`, `self._app._duck_volume()` / `_restore_volume()`, `self._app._schedule_timer()`, `self._app._busy_event`, `self._app._vocab_fail_notified`, `self._app._template_fail_notified`, `self._app._llm_consent_warned`, `self._app.recording._cancelled_cycle_ids` (dictation_pipeline.py:244).

The docstring at `dictation_pipeline.py:11-13` explicitly says: "a full dependency injection refactor is deferred (ARCH-005's VoiceTyperService is the first step toward that)."
**Root Cause:** The extraction (ARCH-006) moved the code from app.py to a new class but did not change the dependency shape — the pipeline still talks to the entire app surface via `self._app.X`. No interface boundary was introduced.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py` (full file, 1291 lines)
**Fix:** Define a `DictationContext` dataclass / Protocol with the actual dependencies (config, models, history_db, clipboard, tray, crash_recovery, bubble, busy_event, schedule_timer) and pass it to the pipeline. Move the per-cycle state onto the pipeline itself. Consider splitting the pipeline into 3 stages (`TranscribeStage`, `TextProcessStage`, `OutputStage`) — each independently testable.
**Severity:** 🟡 Medium — the pipeline cannot be tested without a full app mock; every private attribute of `VoiceTyperApp` is effectively part of the pipeline's public contract; `app: Any` typing means pyrefly cannot verify any of the `self._app.X` accesses.

---

### ZR-17 — `shutdown_controller._do_cleanup` is ~1000 lines with implicit ordering contract (no test, no doc)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/shutdown_controller.py:271-1281` (`_do_cleanup` is ~1000 lines) performs teardown in this order, each step wrapped in its own try/except:
1. `_quit_lock` check + `_cleanup_done = True` (332-335)
2. `ipc_server.stop(timeout=5s)` (344-353)
3. WS dispatch pool `shutdown(cancel_futures=True)` (377-384)
4. Cancel pending timers + join in-flight timer threads (407-426)
5. `recording._stop_watchdog_thread()` (429-433)
6. Streaming session `_cancel_event.set()` (440-447)
7. `recorder.stop(timeout=5s)` — sets `recorder_force_closed` on timeout (468-506)
8. `recorder.shutdown_mic_watcher(timeout=5s)` — SKIPPED if step 7 timed out (GT-70 barrier, 517-531)
9. `level_monitor.stop_monitoring(timeout=5s)` (542-549)
10. Hotkey backends parallel teardown (later)
11. `history_db.flush()` + `close()` (later)
12. `crash_recovery.flush()` + `shutdown()` (later)
13. Bubble level worker stop (later)
14. `tray.stop()` (later)
15. Electron subprocess terminate (later)
16. Win32 mutex `CloseHandle` (later)
17. `_clear_backend_pid_file()` (762-764)
18. `_close_devnull_files()` (785-787)

The ordering is documented ONLY in inline comments. There is no test that pins the full ordering, no architecture doc describing the dependency graph, and no assertion that step N's resource is still alive when step N+1 tries to use it.
**Root Cause:** The shutdown cascade grew incrementally as more subsystems were added. Each new teardown was inserted at "the right place" by the author of that subsystem, with an inline comment explaining why. The ordering contract is implicit and scattered across ~30 comments in a 1000-line method.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py` (lines 271-1281)
**Fix:** (1) Extract a `ShutdownPlan` dataclass listing teardown steps as `(name, callable, timeout, depends_on, skip_if_dep_timed_out)` tuples, executed by a small `_run_plan` driver. (2) Apply the GT-70 barrier pattern uniformly to every resource pair where the downstream call touches the same OS resource as the upstream call. (3) Add a unit test that constructs a fake app with spies on every teardown method and asserts the call order.
**Severity:** 🟡 Medium — a new subsystem teardown added at the wrong position can race; the GT-70 "shutdown barrier" pattern is applied inconsistently (only `recorder.stop` → `shutdown_mic_watcher` has it).

---

### ZR-18 — `VoiceTyperApp.__init__` is 330 lines of inline wiring with `Controller(self)` back-references
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/app.py:124-455` (`VoiceTyperApp.__init__` is ~330 lines) eagerly constructs 19 subsystems inline, many taking `self` (the partially-constructed app) as a constructor argument:
```python
self._audio_processor = AudioProcessor(self.config, ...)  # 177
self.recorder = Recorder(self.config, ..., thread_registry=...)  # 193
self.recording = RecordingController(self)  # 205 (local import)
self.models = ModelManager(self)  # 218 (local import)
self.clipboard = ClipboardManager(...)  # 224
self.tray = TrayIcon(controller=self, config=self.config)  # 227
self.settings = SettingsController(self)  # 255 (local import)
self.shutdown = ShutdownController(self)  # 269 (local import)
# ... 10 more controllers
```
10 of the 19 use function-scope `from voice_typer.server.X import Y` (lines 203, 216, 253, 264, 281, 292, 360, 391, 409, 440, 446) to avoid import cycles. The "app owns X, X holds reference back to app" pattern (`Controller(self)`) means none of these controllers can be tested without either a full `VoiceTyperApp` or a comprehensive MagicMock.
**Root Cause:** The RW-9 Phase 7 decomposition extracted controllers from app.py but kept the constructor-time wiring inline. Each controller needs ~5-10 app attributes, and passing `self` is the path of least resistance. The local imports work around import cycles that exist because each controller module does `from voice_typer.server.app import VoiceTyperApp` under `TYPE_CHECKING`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py` (lines 124-455)
**Fix:** (1) Extract the wiring to an `AppBuilder.build() -> VoiceTyperApp` factory. (2) Define an `AppContext` Protocol (or typed dataclass) that controllers accept instead of `app: Any`. (3) Move the local imports to module top once the import cycles are broken (replace `TYPE_CHECKING` imports of `VoiceTyperApp` with a `Protocol` from a new `app_protocol.py`).
**Severity:** 🟡 Medium (working-but-suboptimal) — `__init__` is 330 lines of wiring; construction order matters but is only documented inline; every controller has `app: Any` typing — pyrefly can't verify accesses.

---

### ZR-19 — `ipc_server._command_handlers` rebuilt per-instance via O(commands) `getattr` loop
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:488-509`:
```python
self._command_handlers: dict[str, CommandHandler] = {}
for _cmd, _method_name in self._COMMAND_REGISTRY.items():
    _bound = getattr(self, _method_name, None)
    if not callable(_bound):
        raise RuntimeError(...)
    self._command_handlers[_cmd] = _bound
```
The class-level `_COMMAND_REGISTRY: dict[str, str]` maps command names to method-name strings; every `IPCServer.__init__` walks the registry and resolves each method via `getattr`. For ~70+ commands × every IPCServer construction (including every test fixture), this is O(commands) work.
**Root Cause:** The string-name registry exists so static-source checks (grep / `inspect.getmembers`) can verify the registry is complete. The bound-method cache is rebuilt per-instance because bound methods are per-instance.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (lines 488-509)
**Fix:** Either (a) make `_COMMAND_REGISTRY` a `dict[str, classmethod]` or `dict[str, Callable]` resolved at class-definition time; or (b) keep the string registry but generate it at class-definition time via a decorator: `@ipc_command("get_status")` on each handler method, with `__init_subclass__` collecting them into `_COMMAND_REGISTRY`.
**Severity:** 🟢 Low (working-but-suboptimal) — not a perf bottleneck (microseconds), but adds conceptual complexity; a handler method rename surfaces only at IPCServer construction.

---

### ZR-20 — `event_bus.publish` calls subscribers SYNCHRONOUSLY (blocks transcription thread on slow IPC writes)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/event_bus.py:115-134` (per the docstring): "`subscribe` / `unsubscribe` / `publish` are all thread-safe. ... A subscriber that raises is logged at WARNING level (with exc_info) on the FIRST occurrence for that subscriber, then at DEBUG ... The subscriber is then skipped and other subscribers still receive the event."

This means `event_bus.publish` calls each subscriber SYNCHRONOUSLY — there's no queue, no async dispatch. The transcription thread (`DictationPipeline`) calls `event_bus.publish({"type": "transcription_final", ...})` (event_bus.py:48-49) and blocks until `IPCServer.push` finishes the TCP/WS write. The audio worker thread is properly decoupled (recorder.py:80-86 — `bubble_level` events go through `self._event_queue` drained by `_event_worker_thread`), but the transcription-thread publishes are not.
**Root Cause:** `event_bus.publish` was designed as a thin shim over the subscriber set, with synchronous fan-out for simplicity. The audio hot path (60 Hz `bubble_level`) was identified as latency-sensitive and got its own queue; the transcription-thread publishes (1-2 per dictation cycle) were deemed acceptable to block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/event_bus.py` (lines 115-134)
- `voice_typer/server/ipc_server.py` (`push` method)
**Fix:** Add an optional `async_dispatch=True` parameter to `event_bus.publish` that submits each subscriber call to a bounded `ThreadPoolExecutor` and returns immediately. The audio hot path keeps its dedicated queue (different latency budget); the transcription-thread publishes use the async path. Subscribers that need ordering can use `publish_sync` for those specific events.
**Severity:** 🟢 Low (working-but-suboptimal) — a slow IPC client (Electron paused in debugger) blocks the transcription thread on `event_bus.publish`. Bounded but seconds of latency.

---

### ZR-21 — `MainRendererBubble` type exposes bubble-window-only event subscriptions on main renderer (dead listener installation)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/types/ipc.ts:766-792` declares `MainRendererBubble` with `onLevel`, `onShow`, `onHide`, `onDraggable` as REQUIRED event subscriptions on the main renderer's `window.bubble`. The Tauri bridge installs them on the main window via `createBubbleNamespace(tauri, "main")` (`bubble-namespace.ts:225-229`). The Electron preload likewise exposes them.

However, BOTH runtimes route bubble-direct events ONLY to the bubble window:
- Electron: `state.bubbleWindow?.webContents.send("bubble:level", msg.data)` (handle-message.ts:97); `win.webContents.send("bubble:show")` (bubble-window.ts:458).
- Tauri: `app.emit_to("bubble", "bubble:hide", ())` (bubble.rs:897); `app.emit_to("bubble", "bubble:set-state", state)` (bubble.rs:1020). Tauri v2's `emit_to(label, ...)` targets only that window's webview.

Verified via `rg "window\.bubble\?\.onShow|onHide|onDraggable|onLevel"` — zero main-renderer callers. The bridge installs dead Tauri `event.listen` registrations on the main window, leaking listener handles.
**Root Cause:** Verified — the bubble namespace was designed to be identical across windows (DX-012 split added the `BubbleWindowBubble` superset, but the shared subset still includes event subscriptions that are physically routed bubble-window-only).
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts` (lines 766-792)
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts` (lines 113-211, 225-229)
- `voice_typer/client/src/main/windows/bubble-window.ts` (lines 458, 538)
- `voice_typer/client/src/main/python/handle-message.ts` (line 97)
- `src-tauri/src/commands/bubble.rs` (lines 897, 1020)
**Fix:** Split `MainRendererBubble` into two interfaces — `MainRendererBubbleMutators` (the mutators: `show`, `setPosition`, `setDraggable`, `moveBy`, `signalReady`, `hideComplete`) and `BubbleEventSubscriptions` (`onLevel`, `onShow`, `onHide`, `onDraggable`). The main renderer's `window.bubble` should be typed as `MainRendererBubbleMutators` only; the bubble window's should be `MainRendererBubbleMutators & BubbleEventSubscriptions & BubbleWindowExtras`. The bridge should NOT call `makeListener` for `onLevel`/`onShow`/`onHide`/`onDraggable` when `windowLabel === "main"`.
**Severity:** 🟡 Medium — no runtime bug today (no main-renderer caller). But the type contract over-promises: any future main-renderer code that calls `window.bubble.onShow(cb)` will silently receive a subscription that never fires.

---

### ZR-24 — Tauri bubble events use MIXED naming conventions (snake vs colon-kebab)
**Status:** ❌ Not Fixed
**Description:** Within the SAME Tauri Rust host, bubble events use MIXED naming:

| Python emits | Electron IPC channel (to bubble) | Tauri event/command |
|---|---|---|
| `bubble_show` (snake) | `bubble:show` (colon) | event `bubble:show` (colon) — `bubble.rs:897` |
| `bubble_hide` (snake) | `bubble:hide` (colon) | event `bubble:hide` (colon) — `bubble.rs:897` |
| `bubble_set_state` (snake) | `bubble:set-state` (colon) | event `bubble:set-state` (colon) — `bubble.rs:1020` |
| `bubble_config` (snake) | `bubble:config` (colon) | event `bubble:config` (colon) |
| `bubble_level` (snake) | `bubble:level` (colon) | **event `bubble_level` (snake)** — `ws.rs:618` |
| Bubble mutators | `bubble:move-by` (kebab-colon) | **command `bubble_move_by` (snake)** |
|  | `bubble:show-from-renderer` (kebab-colon) | **command `bubble_show` (snake)** |
|  | `bubble:hidden` (kebab-colon) | **command `bubble_hide_complete` (snake)** |

The `bubble_level` event is the OUTLIER (snake_case) because the Rust host emits it globally via `app.emit("bubble_level", p)` (ws.rs:618) for performance.
**Root Cause:** Verified — historical layering. Electron preload uses `bubble:kebab` (colon-delimited). Tauri commands use `bubble_snake` (Rust convention). Tauri events targeted to a window mimic Electron's `bubble:kebab` for state events but use `bubble_snake` for the global level stream.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble.rs` (lines 897, 1020)
- `src-tauri/src/sidecar/ws.rs` (line 618)
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts` (lines 118, 189)
**Fix:** Pick ONE convention for Tauri events. Either: (a) Standardize on colon-kebab for all bubble events (rename Rust `app.emit("bubble_level", ...)` to `app.emit_to("bubble", "bubble:level", ...)` and update the bridge listener); OR (b) Standardize on snake_case for all bubble events. Then add a compile-time or test-time parity check that asserts the bridge's event listen names match the Rust emit names.
**Severity:** 🟡 Medium — no runtime bug (bridge translates correctly), but the translation table is implicit; the `bubble_level` outlier is particularly confusing — a new contributor adding `bubble:level` (matching the Electron name) to a Tauri listener would silently never fire.

---

### ZR-25 — Bridge comment lies about Electron envelope unwrap location
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/lib/tauri-bridge/index.ts:76-79` (comment block):
```
// Both paths return `data` directly on success (Tauri unwraps
// `response.data` in Rust; Electron resolves with the full envelope but
// `usePython` returns `result as T` after the error checks pass),
// so the success shape is consistent across runtimes.
```
This is internally contradictory: "Both paths return `data` directly" vs "Electron resolves with the full envelope". The actual Electron code at `voice_typer/client/src/main/python/handle-message.ts:68` does:
```ts
entry.resolve(msg.data);  // unwraps the envelope before resolving
```
So Electron DOES resolve with `msg.data` (the unwrapped data field), NOT "the full envelope".
**Root Cause:** Documentation drift. The comment was likely written when the Electron main process forwarded the full envelope, then the code was changed to unwrap at `handle-message.ts:68` but the comment was not updated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/index.ts` (lines 67-85)
- `voice_typer/client/src/main/python/handle-message.ts` (line 68)
**Fix:** Update the comment to: "Both paths return `data` directly on success — Tauri unwraps `response.data` in Rust (`main.rs` dispatch command); Electron unwraps `msg.data` at `handle-message.ts:68` before resolving. `usePython` returns `result as T` after the (Electron-only) error-envelope checks pass."
**Severity:** 🟢 Low — no runtime impact. Misleads contributors.

---

### ZR-26 — `ThemeSettingsSection.tsx` mutates `useRef.current` during render (React guidance violation)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:468-469, 500-502`:
```ts
const savedPresetRef = useRef(config?.theme_preset ?? "default");
if (config) savedPresetRef.current = config.theme_preset ?? "default";  // ref mutation during render
```
```ts
const lastNonCustomRef = useRef(
    config?.theme_preset && config.theme_preset !== "custom"
        ? config.theme_preset
        : "default",
);
if (config?.theme_preset && config.theme_preset !== "custom") {
    lastNonCustomRef.current = config.theme_preset;  // ref mutation during render
}
```
React's official guidance (https://react.dev/reference/react/useRef#do-not-write-or-read-ref-current-during-rendering) states refs should not be written or read during rendering.
**Root Cause:** The pattern is used to keep refs in sync with the latest `config` prop without adding `config` to a `useEffect` dep (which would fire post-commit and cause an extra render). The trade-off is intentional but violates the React guidance.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx` (lines 468-469, 500-502)
**Fix:** Replace the inline `if (config) ref.current = ...` with `useEffect`:
```ts
const savedPresetRef = useRef(config?.theme_preset ?? "default");
useEffect(() => {
    if (config) savedPresetRef.current = config.theme_preset ?? "default";
}, [config]);
```
Or use the `useState`-derived pattern: derive `savedPreset` from `config?.theme_preset` directly (no ref needed if it's only used during render, not in callbacks/effects).
**Severity:** 🟢 Low — under React 18 StrictMode the mutation runs twice per render cycle. In production it runs once. The mutation is idempotent (assigns the same value), so no observable bug. But under future React versions (concurrent rendering with interruption/resumption), this pattern could cause subtle issues if the render is discarded mid-mutation.

---

### ZR-28 — `Home.tsx` and `Dashboard.tsx` still use module-level `let _cached*` (PVT-003 fix #9 was applied only to `useModelLifecycle`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/pages/Home.tsx:36-37` (`let _cachedRecent`, `let _cachedStats`); `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:36` (`let _cachedData`); contrast with `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts:174-178` (`useRef<VoiceTyperConfig | null>(null)` per-mount cache):
```ts
// Home.tsx:36-37 — MODULE-LEVEL mutable cache
let _cachedRecent: HistoryRecord[] = [];
let _cachedStats: TodayStats | null = null;
```
```ts
// Dashboard.tsx:36 — MODULE-LEVEL mutable cache
let _cachedData: DashboardData | null = null;
```
```ts
// useModelLifecycle.ts:174-178 — per-mount ref cache (PVT-003 fix #9)
const cachedConfigRef = useRef<VoiceTyperConfig | null>(null);
```
The `useModelLifecycle` hook was refactored (PVT-003 fix #9) to use a `useRef` per-mount cache instead of a module-level `let`, with the comment: "replaces module-level `_cachedConfig` so the cache is per-mount (no cross-instance leakage across HMR / test re-renders)". But `Home.tsx` and `Dashboard.tsx` were NOT refactored.
**Root Cause:** Incomplete migration. PVT-003 fix #9 was applied to `useModelLifecycle.ts` only.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx` (lines 36-37)
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx` (line 36)
- `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts` (lines 174-178, reference fix)
**Fix:** Apply the same `useRef` pattern to `Home.tsx` and `Dashboard.tsx`:
```ts
const cachedRecentRef = useRef<HistoryRecord[]>([]);
const cachedStatsRef = useRef<TodayStats | null>(null);
```
Or move cross-cutting caches (recent history, today stats, dashboard data) into the Zustand `appStore` so they're reactive and shared across pages without prop drilling.
**Severity:** 🟢 Low — tests that mount/unmount Home/Dashboard share `_cachedRecent`/`_cachedStats`/`_cachedData` across test cases (a test that loads stats can poison the next test's initial state); HMR re-imports preserve the cache, masking bugs during development.

---

### ZR-30 — `tauri-bridge/index.ts` auto-install side effect pollutes tests that transitively import it
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/lib/tauri-bridge/index.ts:147` (auto-install side effect) + `voice_typer/client/src/renderer/src/main.tsx:13` and `bubble-main.tsx:13` (side-effect imports):
```ts
// tauri-bridge/index.ts:147 — auto-install on import
installTauriBridge();
```
```ts
// main.tsx:13 and bubble-main.tsx:13 — side-effect import
import "./lib/tauri-bridge";
```
The module exports `installTauriBridge` (named export) AND auto-invokes it at module-import time. Tests that import the namespace factories (`createPythonNamespace`, etc.) trigger the auto-install side effect, which mutates `window.python`/`window.bubble`/`window.window_`. Test files must mock `window.__TAURI__` or `isTauri()` BEFORE importing the module to avoid pollution.
**Root Cause:** Verified — intentional design. The auto-install is documented at `index.ts:144-147`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/index.ts` (line 147)
- `voice_typer/client/src/renderer/src/main.tsx` (line 13)
- `voice_typer/client/src/renderer/src/bubble-main.tsx` (line 13)
**Fix:** Separate the auto-install from the named exports. Move `installTauriBridge()` into a separate `tauri-bridge/install.ts` module that `main.tsx` and `bubble-main.tsx` import explicitly:
```ts
// main.tsx
import "./lib/tauri-bridge/install";  // explicit side-effect import
import { usePython } from "@/hooks/usePython";  // doesn't trigger install
```
Tests can then import `usePython` without triggering the bridge install.
**Severity:** 🟢 Low — test isolation requires careful setup; if `window.__TAURI__` is partially mocked, the auto-install may throw inside a `makeListener` subscribe promise.

---

### ZR-34 — 47 of 162 pyrefly errors (29%) are `missing-attribute` on 8 service-layer mixins
**Status:** ❌ Not Fixed
**Description:** 47 of 162 pyrefly errors (29%) are `missing-attribute` on the 8 service-layer mixins:
- `ModelMixin`: 20 errors (`service/model.py` L52,53,66,67,69,83,84,102,106,122,167,177,178,223,224,…)
- `MicrophoneTestMixin`: 8 errors
- `HistoryMixin`: 8 errors
- `DictationMixin`: 4 errors
- `StatusMixin`: 3 errors
- `OnboardingMixin`: 2 errors
- `TemplateMixin`: 1 error
- `VocabularyMixin`: 1 error
Example from `service/model.py`:
```python
class ModelMixin:
    def _register_download(self, model_name: str) -> str:
        ...
        with self._download_cancel_lock:  # missing-attribute
            self._download_cancel_events[download_id] = event  # missing-attribute
            self._active_download_id = download_id  # bad-assignment (None vs str)
```
These attributes are set in `VoiceTyperService.__init__` (service/__init__.py L179,197,198,220,221,222) but never declared on the mixin classes.
**Root Cause:** Same pattern that `HandlerMixinBase` (handlers/_base.py L93-124) already fixed for the 14 IPC handler mixins — by declaring `service: Any / app: Any / _send: Any` at class level. The 8 service mixins were not given the same treatment when service.py was split (ARCH-005).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/model.py`
- `voice_typer/server/service/microphone_test.py`
- `voice_typer/server/service/history.py`
- `voice_typer/server/service/dictation.py`
- `voice_typer/server/service/status.py`
- `voice_typer/server/service/onboarding.py`
- `voice_typer/server/service/template.py`
- `voice_typer/server/service/vocabulary.py`
- `voice_typer/server/service/__init__.py` (lines 179-222)
**Fix:** Add a `ServiceMixinBase` class mirroring `HandlerMixinBase`:
```python
class ServiceMixinBase:
    _app: Any
    _download_cancel_lock: Any
    _download_cancel_events: Any
    _active_download_id: Any
    _model_status_cache: Any
    _model_status_cache_lock: Any
    _model_status_cache_ts: Any
```
Repeat for the other 7 mixins. Also fix `service/__init__.py:199` to declare `_active_download_id: str | None = None`. Eliminates 47 errors (~29% of total).
**Severity:** 🔴 High — 47 pyrefly errors that the type checker can't use to catch real bugs in mixin method bodies. Each `self._download_cancel_lock` access is unchecked; if a future refactor renames the attribute on `VoiceTyperService.__init__` without updating the mixin, the bug only surfaces at runtime.

---

### ZR-35 — `recorder_force_closed` flag is dead write-only state (set in shutdown_controller, never read by recorder)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/shutdown_controller.py:464-465, 480, 497`:
```python
# ``recorder_force_closed`` flag (and mirror it onto
# ``app.recorder._force_closed`` so the recorder itself can [use it])
recorder_force_closed = False
...
if _stop_result is TIMEOUT:
    recorder_force_closed = True
    with contextlib.suppress(Exception):
        app.recorder._force_closed = True  # written
```
`rg "_force_closed" voice_typer/server/recording/recorder.py` returns **zero matches** — the `Recorder` class never declares, reads, nor uses `_force_closed`. The attribute is set in 2 places in `shutdown_controller.py` but read nowhere in production code. Only `tests/test_shutdown_controller.py:849` reads it via `getattr(fake_app.recorder, "_force_closed", None) is True`.
The comment at L465 explicitly claims "so the recorder itself can [use it]" — implementation never landed.
**Root Cause:** Incomplete refactor — the comment promises behavior the recorder was supposed to implement (skip `shutdown_mic_watcher` when force-closed) but the read-side was never added.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py` (lines 464-465, 480, 497)
- `voice_typer/server/recording/recorder.py` (no `_force_closed` references)
- `tests/test_shutdown_controller.py` (line 849)**Fix:** Either (a) implement the read-side in `Recorder.shutdown_mic_watcher` (check `if self._force_closed: return`), declare `_force_closed: bool = False` in `Recorder.__init__`, and remove the `contextlib.suppress(Exception)` wrapper; OR (b) delete the dead writes (L480, L497), update the comment to "retained only as a test-visible flag", and convert the test to assert the intended behavior (recorder.shutdown_mic_watcher is skipped) rather than the attribute write.
**Severity:** 🔴 High — dead write-only attribute on every timeout-induced shutdown; comment drift misleads future maintainers into thinking the recorder uses the flag for behavior branching. The test asserts the write happens but doesn't assert any behavior — it's a tautological test that pins an implementation detail.

---

### ZR-37 — 5 latent type bugs flagged by pyrefly but not yet in baseline
**Status:** ⚠️ Partial (verified on Linux sandbox; 1 of 5 pyrefly fixes applied to recorder.py queue type; 4 remaining in vad_processor.py, ipc_server.py, crash_handler.py, asr_registry.py)
**Description:** 5 distinct sites with real type bugs flagged by pyrefly but not yet in baseline (because baseline is empty by design):
1. `voice_typer/server/recording/recorder.py:477, 2283` — Queue type mismatch: `self._event_queue: queue.Queue[dict] = queue.Queue(maxsize=1000)` (L477) but L2283 does `self._event_queue.put_nowait(_EVENT_WORKER_STOP_SENTINEL)` where `_EVENT_WORKER_STOP_SENTINEL = _EventWorkerStopSentinel()` is NOT a `dict`. Pyrefly: `bad-argument-type`. Works at runtime because Python's `queue` doesn't enforce generic types.
2. `voice_typer/server/vad_processor.py:447` — `vad_enabled` property declared `-> bool` but returns `self._vad_enabled_cached` typed `bool | None`. Pyrefly: `bad-return`. The code reads `cached = self._vad_enabled_cached; if cached is not None:` then re-reads the attribute (`return self._vad_enabled_cached`) instead of returning the local `cached`. Pyrefly can't narrow across attribute re-reads.
3. `voice_typer/server/ipc_server.py:509` — `self._command_handlers[_cmd] = _bound` where `_bound = getattr(self, _method_name, None)` is `Any`. Pyrefly: `unsupported-operation — Cannot set item in dict[str, CommandHandler]`.
4. `voice_typer/server/crash_handler.py:531,533` — `_write_to_file(_crash_file_path, body)` where `body = buf[:pos]` is `bytearray` but `_write_to_file` declares `data: bytes` (L556). Pyrefly: `bad-argument-type`. Works at runtime because `f.write()` accepts both `bytes` and `bytearray`.

5. `voice_typer/server/asr_registry.py:517` — `subscribers` referenced in the outer `if tripped:` block but assigned only in the inner `if tripped:` block (under `self._lock`). Pyrefly: `unbound-name — subscribers may be uninitialized`. Currently safe at runtime but a refactor that reassigns `tripped` between the two blocks would cause `NameError`.
**Root Cause:** Each is a small type-safety gap that pyrefly correctly identifies but isn't yet in the baseline.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py` (lines 477, 2283)
- `voice_typer/server/vad_processor.py` (lines 204, 437-451)
- `voice_typer/server/ipc_server.py` (line 509)
- `voice_typer/server/crash_handler.py` (lines 531-533, 556)
- `voice_typer/server/asr_registry.py` (line 517)**Fix:**
1. `recorder.py`: `self._event_queue: queue.Queue[dict | _EventWorkerStopSentinel] = ...`
2. `vad_processor.py:447`: `return cached` (use the narrowed local)
3. `ipc_server.py:509`: `self._command_handlers[_cmd] = typing.cast(CommandHandler, _bound)` (after the `callable(_bound)` guard)
4. `crash_handler.py`: change signature to `data: bytes | bytearray` (or `data: Buffer`), OR call site `_write_to_file(_crash_file_path, bytes(body))`
5. `asr_registry.py:517`: declare `subscribers: list[Callable[[str, int], None]] = []` before the `with self._lock:` block, OR merge the two `if tripped:` blocks
**Severity:** 🔴 High — 5 latent bugs that would surface under refactoring or unusual inputs. None currently cause runtime failures (verified by reading the surrounding code), but each is a footgun for the next maintainer.

---

### ZR-38 — `recording/__init__.py` test-patch compat shim is 447 LOC of `__init__.py` boilerplate (CR-67 tech debt)
**Status:** ❌ Not Fixed (duplicate of ZR-14 — same root cause; this entry is from sub-agent D's code-quality lens)
**Description:** Same as ZR-14. `voice_typer/server/recording/__init__.py` is a 447-line file whose entire purpose is test-patch compatibility. Custom module subclass with `__getattr__`/`__setattr__` overrides routes 5 mutable-global reads/writes (`_resample_poly`, `_resample_poly_error`, `_resample_poly_error_time`, `_scipy_preloader_thread`, `_buffer_clear_worker`) through to the owning submodule. The `# noqa: F401` count in these three files alone is 56.
**Root Cause:** Phase 4.5 package split left tests patching the old package namespace; the shim was added to avoid touching tests. The TODO to migrate tests is dated today (2026-07-25) and explicitly OPEN.
**Progress:** None yet.
**Related Files:** Same as ZR-14.**Fix:** Same as ZR-14. Execute the CR-67 migration.
**Severity:** 🟡 Medium — ~500 LOC of `__init__.py` boilerplate exists purely for test compatibility. (Cross-references ZR-14 — primary agent should treat as one finding, not two.)

---

### ZR-40 — 18 production (non-test) ruff violations across 15 files (baseline drift)
**Status:** ❌ Not Fixed
**Description:** `ruff check voice_typer/` (excluding tests) reports 18 violations:
- SIM105 (try-except-pass): 4 sites — `clipboard/manager.py:699`, `crash_recovery.py:119`, `history_db.py:1392`, `history_db.py:2175`
- E501 (line too long): 4 sites (e.g. `recorder.py:934`)
- SIM102 (nested-if): 2 sites (`hallucination.py:106`, `ipc_server.py:2176`)
- 8 singletons: UP033, N811, SIM401, SIM103, UP035, SIM108, UP037, I001
The 4 SIM105 sites are all justified by comments ("atexit must never raise", "already removed by another path", etc.) — should still be converted to `contextlib.suppress(Exception)` per the lint rule.
**Root Cause:** Production code is mostly clean but a handful of style violations exist; baseline only tracks SIM105 (18) and E501 (3), so the 4 production SIM105 violations are presumably counted in the baseline 18, but the 4 production E501 are NOT in baseline (which tracks only 3).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py:699`
- `voice_typer/server/crash_recovery.py:119`
- `voice_typer/server/history_db.py:1392, 2175`
- `voice_typer/server/recording/recorder.py:934`
- `voice_typer/server/hallucination.py:106`
- `voice_typer/server/ipc_server.py:2176`**Fix:** (a) Convert the 4 production `try/except X: pass` to `with contextlib.suppress(X):`. (b) Wrap the 4 long lines. (c) Re-run `python scripts/ruff_ratchet_check.py --regenerate` to lock in the cleanup.
**Severity:** 🟡 Medium — baseline drift on E501 (3 → 4) is a real ratchet regression.

---

### ZR-48 — No "how to add a new IPC command / hotkey backend / ASR engine" guide (11 touchpoints, only 3 enforced)
**Status:** ❌ Not Fixed
**Description:** No documented "how to add a new IPC command / hotkey backend / ASR engine / config field" guide. `CONTRIBUTING.md` §3 lists files; `docs/ARCHITECTURE.md` lists modules; `docs/adr/0015-electron-command-allowlist.md` mentions "Every new IPC command requires updating three files" (now actually 6+).
Adding a new IPC command requires touching 11 touchpoints:
1. `voice_typer/server/ipc_server.py` `_COMMAND_REGISTRY`
2. `voice_typer/server/handlers/<area>_handlers.py`
3. `voice_typer/server/ipc/rate_limiter.py` `COMMAND_COSTS`
4. `voice_typer/client/src/main/allowed-commands.ts` (TS allowlist)
5. `src-tauri/src/commands/sidecar_cmds.rs` `allowed_commands()` (Rust allowlist)
6. `voice_typer/client/src/renderer/src/types/ipc.ts` (renderer type union)
7. `docs/API.md` (enforced by `test_api_doc_accuracy.py`)
8. `SECURITY.md` command count (enforced by `test_security_doc_command_count.py`)
9. `docs/ARCHITECTURE.md` command count (NOT enforced — see ZR-46)
10. `CONTRIBUTING.md` command count (NOT enforced)
11. If a new handler file: `voice_typer/server/handlers/__init__.py` `__all__`
11 touchpoints across 3 languages, only 3 enforced by tests. No checklist in `CONTRIBUTING.md`.
**Root Cause:** `rg "adding a new command|new IPC command|add a command" docs/ CONTRIBUTING.md README.md` returns only ADR-0015's acknowledgment that the burden is manual.
**Progress:** None yet.
**Related Files:**
- `CONTRIBUTING.md`
- `docs/ARCHITECTURE.md`
- `docs/adr/0015-electron-command-allowlist.md`**Fix:** Add `docs/contributing/adding-an-ipc-command.md` with a numbered checklist (the 11 steps above) and an automated script `scripts/check-new-command.py <cmd_name>` that greps all 11 locations and reports which are missing. Add the same for hotkey backends and ASR engines.
**Severity:** 🔴 High — every new command added by a new contributor will miss at least one of the unenforced touchpoints (ZR-46 shows this has already happened — CONTRIBUTING.md is 5 counts behind).

---

### ZR-49 — Test file naming inconsistency: 5 different conventions, 2 documented (one unused)
**Status:** ❌ Not Fixed
**Description:** `tests/regressions/*_test.py` (19 files using `_test.py` suffix); `tests/test_clipboard_de_fixes.py`, `tests/test_ipc_de33_to_de36.py`, `tests/test_prewarm_er_fix_e2.py`, `tests/test_prewarm_xv_fixes.py`, `tests/test_clipboard_regression.py`, etc. (37 files using `test_*_<session>_fixes.py` / `test_*_<session>_<id>.py`); `tests/test_clipboard.py`, `tests/test_config.py` (using documented `test_<feature>.py`).
`CONTRIBUTING.md` §7.1: "The test file naming convention is `test_<feature>.py` or `test_round<N>_<theme>.py` for batch review rounds."
Actual patterns observed:
- `test_<feature>.py` (documented, 265 files)
- `test_round<N>_<theme>.py` (documented, 0 files — convention exists but unused)
- `*_test.py` suffix (undocumented, 19 files in `tests/regressions/`)
- `test_<feature>_de_fixes.py` (undocumented, 8 files)
- `test_<feature>_<session>_fix_<id>.py` (undocumented, e.g. `test_prewarm_er_fix_e2.py`, `test_prewarm_xv_fixes.py`)
- `test_<feature>_de<N>_to_de<N>.py` (undocumented, 1 file)
**Root Cause:** Each review session invented its own naming pattern, none of which match the two documented conventions.
**Progress:** None yet.
**Related Files:**
- `tests/regressions/*_test.py` (19 files)
- `tests/test_*_fixes*.py` (37 files)
- `tests/test_clipboard*.py` (12 files)
- `CONTRIBUTING.md` §7.1**Fix:** Pick one convention (recommend `test_<feature>.py` for new tests, `test_<feature>_<concern>.py` for sub-files). Rename the 19 `tests/regressions/*_test.py` files to `tests/regressions/test_*.py`. Update CONTRIBUTING.md to document only the chosen convention and explicitly deprecate the others. Add a CI lint that rejects new files matching the deprecated patterns.
**Severity:** 🟡 Medium — to find all tests touching feature X, a maintainer must grep both `test_X*.py` AND `*_test.py` AND know which session-name files might cover X.

---

### ZR-51 — `recorder.py` 5 VAD constants comment lies about reference (lines 1446-1447 DO reference them)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:155-164` (5 `_DEFAULT_VAD_*` constants) vs `voice_typer/server/vad_processor.py:14-19` (5 `DEFAULT_VAD_*` constants); comment at `recorder.py:156-159`:
```python
# AUDIO-014: default VAD thresholds (overridden by auto-calibration).
# RW-04: these mirror ``vad_processor.DEFAULT_VAD_*`` (without the
# leading underscore). The leading-underscore aliases are kept so any
# external code/tests that imported them continue to work; they're no
# longer referenced internally after the VadProcessor extraction.
```
But the comment is wrong:
```
$ rg "_DEFAULT_VAD_SPEECH_THRESHOLD_DB" voice_typer/server/recording/recorder.py
160:_DEFAULT_VAD_SPEECH_THRESHOLD_DB = -40.0
1446:        self._vad_speech_threshold_db = _DEFAULT_VAD_SPEECH_THRESHOLD_DB
1447:        self._vad_silence_threshold_db = _DEFAULT_VAD_SILENCE_THRESHOLD_DB
```
Lines 1446-1447 DO reference these constants internally — contradicting the comment "they're no longer referenced internally". And 3 of the 5 (`_DEFAULT_VAD_CALIBRATION_DURATION`, `_DEFAULT_VAD_SPEECH_FRAMES`, `_DEFAULT_VAD_HANGOVER_FRAMES`) are referenced only by `recording/__init__.py` re-export, never by any code or test.
**Root Cause:** The RW-04 extraction moved VAD logic to `vad_processor.py` but left the old constants in `recorder.py` as "back-compat aliases." The comment claiming they're unreferenced is stale, and 3 of 5 are pure dead code.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py` (lines 155-164, 1446-1447)
- `voice_typer/server/vad_processor.py` (lines 14-19)
- `voice_typer/server/recording/__init__.py` (re-exports)
**Fix:** (a) Delete the 3 dead constants (`_DEFAULT_VAD_CALIBRATION_DURATION`, `_DEFAULT_VAD_SPEECH_FRAMES`, `_DEFAULT_VAD_HANGOVER_FRAMES`) and their `__init__.py` re-exports. (b) Replace lines 1446-1447 with `from voice_typer.server.vad_processor import DEFAULT_VAD_SPEECH_THRESHOLD_DB, DEFAULT_VAD_SILENCE_THRESHOLD_DB` and delete the 2 remaining duplicates. (c) Delete the misleading RW-04 comment.
**Severity:** 🟡 Medium (working-but-suboptimal) — future maintainer reads the comment "no longer referenced internally" and trusts it — then either deletes the constants (breaking lines 1446-1447) or fails to update them when VAD defaults change (silent drift).

---

### ZR-52 — 3 near-identical 482-line Tauri build workflows with no shared composite action
**Status:** ❌ Not Fixed
**Description:** `.github/workflows/tauri-windows-build.yml` (482 lines, 23 steps), `.github/workflows/tauri-macos-build.yml` (483 lines), `.github/workflows/tauri-linux-build.yml` (467 lines):
```
$ wc -l .github/workflows/tauri-*.yml
  180 .github/workflows/tauri-build.yml    (orchestrator)
  467 .github/workflows/tauri-linux-build.yml
  483 .github/workflows/tauri-macos-build.yml
  482 .github/workflows/tauri-windows-build.yml
  1612 total
```
Common steps repeated across all three (with platform-specific divergence): Checkout, Set up Python, Install uv, Install Rust toolchain + targets, Cache cargo, Set up Node.js, Install Python deps, Build sidecar (Nuitka), Build prewarm (Nuitka), Build native key-listener, Build Tauri app, Sign (optional), Upload installer artifact, Generate SHA-256 checksums, Attest build provenance.
**Root Cause:** Each per-platform workflow was authored by a different sub-agent (per the comment at `tauri-build.yml:10-14`: "owned by sub-agents #5 (Windows), #6 (macOS), #7 (Linux)"). No shared composite action was extracted.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/tauri-windows-build.yml`
- `.github/workflows/tauri-macos-build.yml`
- `.github/workflows/tauri-linux-build.yml`
- `.github/workflows/tauri-build.yml`
**Fix:** Extract the common steps into 2-3 composite actions under `.github/actions/`: `setup-build-env` (Python + uv + Rust + Node + caches), `build-sidecar-and-prewarm` (Nuitka invocations), `build-native-listener` (C/Swift compile). Each per-platform workflow then becomes ~80-100 lines of platform-specific glue calling the composites.
**Severity:** 🟡 Medium — a change to the sidecar build flags (e.g. Nuitka `--include-package` bump) must be made in 3 places. The 3 workflows have already diverged (e.g. Windows uses `Set up Python 3.12 (dev env)` step name; macOS uses `Set up Python 3.12`; Linux uses `Set up Python ${{ env.PYTHON_VERSION }}`).

---

### ZR-53 — `tests/test_history_and_models.py` (1180 LOC, 28 classes, 10 domains) — catch-all test file
**Status:** ❌ Not Fixed
**Description:** `tests/test_history_and_models.py` (28 classes, 1180 lines) mixes "history database, vocabulary management, corrections loading, model download/cancel mechanism, and miscellaneous engine infrastructure" per its own docstring — 5 unrelated domains in one file. The 28 test classes span: HistoryDBError, retention, search, CloudEngine timeout, VocabularySaveRetry, CorrectionsLoadError, SharedVocabConstants, ResampleUnavailable, PruneOldEntries, BuildModelsSubmenu, CancelModelDownload, AsrSetup, ValidateNonNumericFields docstring, MainModule role, TrayIcon SVG, OnboardingController, PhrasePatternCache, HistoryRestore, TemplatesPersistToDisk, SVC2-SVC11 service helpers, PERF21DownloadPoll.

`tests/test_server.py` (2827 lines, 38 classes) similarly mixes dispatch + run loop + push events + lifecycle + error handling + rate limiter + config redaction + pending buffer + history bounding + auth handshake + defaults + flood resistance + end-to-end + send/lock.

`tests/test_clipboard*.py` (12 files) sprawl in the inverse direction: 12 files for one feature, organized by session/fix-iteration rather than by clipboard concern.
**Root Cause:** The directive's example catch-all (`tests/test_bugfix_regressions.py` at 4446 lines / 86 classes) was split into `tests/regressions/` (good), but other catch-alls were not. `test_history_and_models.py` explicitly mixes 5 domains per its own docstring.
**Progress:** None yet.
**Related Files:**
- `tests/test_history_and_models.py` (1180 LOC)
- `tests/test_server.py` (2827 LOC, 38 classes)
- `tests/test_clipboard*.py` (12 files)
**Fix:** Split `test_history_and_models.py` by domain: `test_history_db.py`, `test_vocabulary.py` (move vocab tests), `test_text_cleanup.py` (move), `test_model_download.py`, `test_asr_setup.py`, `test_service_helpers.py`. Split `test_server.py` (38 classes) by command group into `test_ipc_dispatch.py`, `test_ipc_rate_limiter.py`, `test_ipc_auth.py`, `test_ipc_lifecycle.py`, `test_ipc_push_events.py`. Consolidate the 12 `test_clipboard*.py` files into `test_clipboard/{core,win32,linux,macos,security,snapshot,paste_restore}.py` under a `test_clipboard/` package.
**Severity:** 🟡 Medium — locating the test for a specific behavior requires grepping across many files.

---

### ZR-54 — CHANGELOG.md is 4 weeks stale (no Tauri migration, no decompositions, no allowlist narrowing)
**Status:** ❌ Not Fixed
**Description:** `CHANGELOG.md` (188 lines, only `[Unreleased] - 2026-06-30` + `1.0.0 (2026-06-21)`). Worklog documents active sessions through at least 2026-07-24. None of these post-2026-06-30 changes appear in CHANGELOG.md: Tauri migration Phase 0-5 work, recorder.py / ipc_server.py / config.py / history_db.py decompositions, ADR-0020 acceptance, GT-32 allowlist narrowing, RW-9 god-class extractions.
**Root Cause:** CHANGELOG hasn't been updated in ~4 weeks of active development.
**Progress:** None yet.
**Related Files:**
- `CHANGELOG.md`
**Fix:** Add a `[Unreleased]` section entry (or bump to `[1.1.0]` / `[1.0.1]`) covering: native hotkey architecture (ADR-0008), Tauri migration progress (ADR-0020), recorder/config/ipc_server/history_db decompositions, 17-command allowlist narrowing (GT-32), and any user-visible behavior changes. Add a pre-commit hook that warns if a PR touches `voice_typer/server/` without a CHANGELOG entry.
**Severity:** 🟡 Medium — a user reading CHANGELOG.md to decide whether to upgrade sees nothing about the Tauri migration, the new native hotkey architecture, or the 4-week bug-fix backlog.

---

### ZR-55 — `ipc/rate_limiter.py:326-331` stale "kept in sync" comment (deduplication already complete)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc/rate_limiter.py:326-331`:
```python
# NOTE: this leaf copy in ``voice_typer/server/ipc/rate_limiter.py`` is
# kept in sync with the canonical implementation in
# ``voice_typer/server/ipc_server.py`` (CR-14 deferred the package
# delete). The canonical implementation is the one imported by tests
# and by ``sidecar_ws.py``; this copy exists only because the
# ``ipc/`` package was not deleted in this IMPROVE-mode run.
```
But the deduplication IS complete:
```
$ rg "^class _RateLimiter" voice_typer/server/ -t py
voice_typer/server/ipc/rate_limiter.py:class _RateLimiter:   (only one definition)
```
PVT-MERGE-009 in `review.md` confirms: "_RateLimiter and _pick_available_port no longer duplicated."
**Root Cause:** The dedup was completed but the 6-line NOTE comment claiming duplication exists was never removed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py` (lines 326-331)
**Fix:** Delete the 6-line NOTE block at `rate_limiter.py:326-331`. Audit other "kept in sync with" / "this is a copy of" comments across the codebase for the same staleness pattern.
**Severity:** 🟡 Medium (working-but-suboptimal) — comment lies about the code's structure. Erodes trust in surrounding comments. Future maintainer may attempt a redundant dedup.

---

### ZR-56 — HMAC terminology mismatch in docs (implementation is bearer-token, not HMAC)
**Status:** ❌ Not Fixed
**Description:** `CONTRIBUTING.md` (line ~330: "HMAC/bearer-token auth handshake"); `docs/migration/{windows,macos,linux}-validation-runbook.md` (section headings: "WS + HMAC handshake"); `docs/migration/cutover-playbook.md` ("HMAC handshake: wrong token rejected").

`src-tauri/Cargo.toml:48-54`:
```
# ADR-0020 §3: per-launch bearer token (32 random bytes hex-encoded).
# The token is generated via `rand::rng().fill_bytes()` and
# passed to the sidecar via `VOICE_TYPER_IPC_TOKEN`. Despite the ADR's
# "HMAC" wording, the Rust host treats it as a bearer token (the
# sidecar's `ipc_server.py` validates the literal string match). The
# `hmac`+`sha2` crates are therefore NOT needed in the host.
```
The runbooks ACKNOWLEDGE the terminology is wrong but keep it anyway. CONTRIBUTING.md uses "HMAC/bearer-token auth handshake" (hedged but still misleading).
**Root Cause:** ADR-0020 originally specified HMAC; implementation simplified to bearer-token literal match; ADR was never updated to reflect this; downstream docs copied the HMAC wording; the runbooks added a parenthetical note acknowledging the mismatch but didn't fix the heading.
**Progress:** None yet.
**Related Files:**
- `CONTRIBUTING.md`
- `docs/migration/windows-validation-runbook.md`
- `docs/migration/macos-validation-runbook.md`
- `docs/migration/linux-validation-runbook.md`
- `docs/migration/cutover-playbook.md`
- `src-tauri/Cargo.toml` (lines 48-54)
**Fix:** Replace "HMAC" with "bearer-token" in CONTRIBUTING.md and the 3 runbook headings. Update ADR-0020 §3 to say "bearer token (originally specified as HMAC; simplified to literal-string match during implementation — see Cargo.toml comment)".
**Severity:** 🟡 Medium — a security reviewer reading "HMAC handshake" assumes cryptographic authentication; the actual implementation is a constant-time-ish string comparison of a 32-byte hex token. The mismatch could lead to over-confidence in the auth mechanism.

---

### ZR-57 — `tests/conftest.py:231-419` — 190-line autouse fixture mocks 15 modules for every test
**Status:** ❌ Not Fixed
**Description:** `tests/conftest.py:231-419` (`mock_heavy_imports` autouse fixture, ~190 lines). The fixture mocks: `sounddevice`, `faster_whisper`, `faster_whisper.WhisperModel`, `pynput`, `pynput.keyboard`, `pystray`, `PIL`, `PIL.Image`, `PIL.ImageDraw`, `pyperclip`, `torch`, `transformers`, plus the 3 inline patches above, plus `ctypes.WINFUNCTYPE` aliasing, plus `lru_cache` clearing on `get_native_binary_path` and `_shutil_which_cached`. Plus a `MockHeavyImportsWarning` class with per-kind dedup (`_warn_once`). Plus opt-out markers (`real_pynput`, `real_pil`, `real_torch`, `slow`).

`$ rg "monkeypatch.setitem\(sys.modules" tests/conftest.py | wc -l` returns 15.
**Root Cause:** The fixture has accreted patches over many sessions (TEST-003, FIX-18, XS-45, XS-46, CR-068, CR-017, XV-112, XV-100). Each addition is individually justified with a long comment block, but the aggregate is a 190-line autouse fixture that runs for every test — including tests that don't touch any of the mocked modules.
**Progress:** None yet.
**Related Files:**
- `tests/conftest.py` (lines 231-419)
**Fix:** Split into per-domain opt-in fixtures: `mock_audio_imports`, `mock_gui_imports`, `mock_torch_imports`. Make `mock_heavy_imports` a thin wrapper that depends on all three (preserving current behavior for tests that don't care). New tests opt into only the mocks they need. Document the opt-out markers in CONTRIBUTING.md §7.1 (currently only `real_pynput` is mentioned; `real_torch` and `real_pil` exist but aren't documented).
**Severity:** 🟡 Medium (working-but-suboptimal) — a new test author cannot predict what their test sees without reading 190 lines of fixture. `import torch` in a test silently returns a `MagicMock` unless they remember `@pytest.mark.real_torch`.

---

### ZR-58 — `.github/workflows/build.yml` is 1377 lines / 101 steps (CI complexity)
**Status:** ❌ Not Fixed
**Description:** `.github/workflows/build.yml` (1377 lines, 101 steps across 7 jobs: test, pip-audit-weekly, slow-tests, check-version-sync, branding-check, client-build, build-native-matrix, build-macos-universal). The file header has a 17-line "PINNED ACTION VERSIONS — DO NOT DOWNGRADE" block listing 8 actions with breaking-change analysis. Inline comments reference 30+ tags (XS-25, XS-24, CI-02, BUILD-N03, BUILD-N22, BUILD-N25, etc.).
**Root Cause:** CI accreted gates over many sessions: ruff (F-rules + ratchet), i18n key-completeness, pyrefly + baseline audit, pytest + coverage + ratchet, pip-audit (with ignore list) + weekly full audit + auto-issue-on-finding, slow tests + auto-issue-on-failure, version sync, branding check, renderer build, native binary build matrix (3 OS × smoke test × codesign), macOS universal binary merge. Each gate is individually valuable; the aggregate is a 1377-line YAML.
**Progress:** None yet.
**Related Files:**
- `.github/workflows/build.yml`
**Fix:** Extract reusable job definitions into composite actions under `.github/actions/`. Move the "PINNED ACTION VERSIONS" block into `docs/ci-action-pinning.md` and reference it from each workflow header. Consider splitting `build.yml` into `python-ci.yml` (test + lint + type + audit) and `release-build.yml` (native binary + installer) — they have different triggers (PR vs. tag) anyway.
**Severity:** 🟡 Medium (working-but-suboptimal) — debugging a CI failure requires scrolling 1377 lines to find the failing step.

---

### ZR-60 — `recorder.start()` 610 lines + `_process_audio_chunk` 443 lines (god methods)
**Status:** ❌ Not Fixed (deferred — 610-line start() + 443-line _process_audio_chunk god methods; 18-helper extraction plan exceeds 10-min budget; no targeted unit tests for individual phases to make partial extraction safe)
**Description:** `voice_typer/server/recording/recorder.py:1350-1959` (`start()`, 610 lines) performs ≥10 distinct phases inline: (1) SEC-audit-008 secure-clear cache arrays, (2) reset 30+ per-session state fields, (3) cache config values, (4) build callback closure, (5) candidate-device enumeration loop, (6) fallback all-devices loop, (7) dynamic buffer sizing, (8) persist mic fallback in background thread, (9) pre-roll buffer prepend, (10) worker-thread startup.

`_process_audio_chunk` similarly: disconnect detection, XRUN handling, mono conversion + filter chain, buffer append + backpressure detection, RMS/peak, clipping, VAD auto-calibrate, Silero VAD inference, silence state machine + callbacks, RMS callback. 443 lines.
**Root Cause:** Accreted over many fix cycles; each phase has its own inline comments referencing separate change IDs (ARCH-023, SEC-audit-008, AUDIO-HOT, RT-SAFE-001, etc.). EC-1 noted the god-class but did not propose a specific extraction plan.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py` (lines 1350-1959, 2514-2956)
**Fix:** Split `start()` into named helpers (no behavior change — mechanical extraction):
- `_secure_clear_session_caches()` (L1391-1400)
- `_reset_session_state()` (L1402-1479, 78 lines)
- `_cache_session_config()` (L1486-1493)
- `_build_audio_callback()` (L1519-1533)
- `_open_stream_for_candidates(candidates)` (L1566-1668)
- `_open_stream_fallback(tried)` (L1671-1755)
- `_resize_buffers_for_sample_rate(effective_sr, max_rec)` (L1762-1872)
- `_persist_microphone_fallback_async(selected_device)` (L1874-1897)
- `_prepend_preroll_to_buffer()` (L1901-1930)

Split `_process_audio_chunk()` into:
- `_detect_device_disconnect(indata)` → early return bool
- `_handle_xrun_status(status)`
- `_apply_filter_chain(indata_mono)` → filtered
- `_append_to_buffer_locked(filtered)` → chunk_count, buffer_len
- `_compute_rms_and_peak(filtered)`
- `_detect_and_emit_clipping(chunk_peak)`
- `_run_silero_vad(chunk_rms, chunk_duration)` → (vad_prob, vad_state)
- `_update_silence_state_machine(vad_state, recording_duration)`
- `_fire_rms_callback(chunk_rms, chunk_peak)`

`_process_audio_chunk` becomes a ~30-line orchestrator. Each extracted method is independently unit-testable.
**Severity:** 🔴 Critical (refactor) — the two methods are too large to reason about; merge-conflict prone; hard to write targeted unit tests for individual phases.

---

### ZR-61 — `ipc_server._send` 320 lines + `_handle_tcp_connection` 374 lines (god methods)
**Status:** ⚠️ Partial (deferred — _send (365 lines) and _handle_tcp_connection (381 lines) god methods are concurrency-hardened; extraction changes lock-acquisition topology. Recommended incremental approach: _emit_ipc_error → _snapshot_transport_state → lock-state extractions)
**Description:** `voice_typer/server/ipc_server.py:2117-2431` (`_send`, 320 lines) does: (1) snapshot transport state under lock, (2) serialize JSON, (3) stdout-mode write branch, (4) TCP write with timeout/restore + pending drain, (5) TCP-mode pending-buffer append with cap + FIFO merge, (6) console-mode rate-limited log.

`_handle_tcp_connection` (930-1303, 374 lines) does: auth timeout set, token validation, auth handshake, clear timeout + install client under lock, emit initial state_changed, rate-limiter lookup, dispatch loop with 4 distinct error envelopes, finally cleanup + `_on_ipc_client_disconnect`.
**Root Cause:** Method accreted concurrency-hardening fixes (NEW-IPC-014, NEW-CONC-001/003, PVT-G5-011/012/013, GT-48, CR-2) without structural extraction.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (lines 930-1303, 2117-2431)
**Fix:** Extract from `_send`:
- `_snapshot_transport_state(_client)` → (out, tcp_client, tcp_mode, pending)
- `_send_to_stdout(out, line)`
- `_send_to_tcp_client(tcp_client, line, pending, _client)` — itself extractable into `_send_with_write_lock(...)` + `_drain_pending(tcp_client, pending)`
- `_buffer_when_disconnected(line, pending)`
- `_log_dropped_push(msg_type, msg)`

Extract from `_handle_tcp_connection`:
- `_authenticate_tcp_client(conn, addr, expected_token)` → client | None
- `_install_authenticated_client(client)` → pending_flush
- `_flush_pending_on_connect(client, pending_flush)`
- `_emit_initial_state_changed()`
- `_run_dispatch_loop(client, rate_limiter)`
- `_emit_ipc_error(client, code, message, msg)` — single helper replacing 4 inline error-envelope blocks

`_handle_tcp_connection` becomes a ~30-line orchestrator.
**Severity:** 🔴 High (refactor) — `test_send_does_not_hold_lock_during_write` (test_server.py:2589) is one of 38 test classes that all need to reason about this method; the 4 different error envelopes are scattered through 4 try/except blocks.

---

### ZR-62 — `config._validate_non_numeric_fields` 310 lines with hand-maintained field-name sets
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/config.py:1768-2078` (`_validate_non_numeric_fields`, 310 lines). Hand-maintained sets of field names: `bool_fields` (L1791-1871, ~60 names), `str_fields` (L1872-1898, ~30 names), `int_fields` (L1908-1921, ~12 names), `float_fields` (L1922-1961, ~30 names), `optional_str_fields` (L1988). The dataclass `Config` already has `__dataclass_fields__` with full type annotations. Comments at L1865, L1867, L1935, L1957 explicitly note fields that were REMOVED from the dataclass but linger in these sets, and others (`volume_duck_smart_poll_interval_ms`) that were misclassified.
**Root Cause:** Verified drift hazard — comments document 4 fields that were renamed/removed but whose entries here either linger or required manual correction.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py` (lines 1768-2078)
**Fix:** Derive the sets from the dataclass annotation at class-definition time:
```python
def _derive_field_type_registry() -> dict[str, type]:
    import typing

    hints = typing.get_type_hints(Config)
    registry: dict[str, type] = {}
    for name, field in Config.__dataclass_fields__.items():
        ann = hints[name]
        if typing.get_origin(ann) is typing.Union:
            args = [a for a in typing.get_args(ann) if a is not type(None)]
            if len(args) == 1:
                ann = args[0]
        registry[name] = ann
    return registry
```
Then iterate `for name, expected_type in registry.items():` and apply the existing per-type coercion. Field renames/removals become free (the dataclass is the single source of truth).
**Severity:** 🔴 High (refactor) — every new config field requires hand-adding its name to exactly one of 4 sets; a miss silently breaks coercion.

---

### ZR-63 — `config.load()` 556 lines mixing JSON read + migration + coercion + validation + save-back
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/config.py:1212-1767` (`Config.load`, 556 lines) mixes: JSON read + parse + non-dict guard, unknown-key filtering, schema-version comparison + warning, migration runner loop with per-version try/except + .bak save, pre-migration .bak copy, per-field float/int coercion + clamping, model_size default, qwen_model_path validation, corrections_path validation, privacy consent warnings, `_validate_non_numeric_fields` call, `Config(**data)` construction, `__post_init__` validation, save-back path.

Each phase is a try/except block with its own log format.
**Root Cause:** PVT-055 noted the monolith; the specific split is the new contribution.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py` (lines 1212-1767)
**Fix:** Extract named helpers (no behavior change):
- `Config._read_raw_json(path) -> dict | None`
- `Config._filter_unknown_keys(parsed) -> dict`
- `Config._run_migrations(data, loaded_version) -> tuple[dict, int]`
- `Config._backup_before_migration(config_file, loaded_version)`
- `Config._coerce_streaming_fields(data)`
- `Config._coerce_max_recording_time(data)`
- `Config._validate_model_path(data)`
- `Config._validate_qwen_model_path(data)`
- `Config._validate_corrections_path(data)`
- `Config._validate_privacy_consents(data)`

`load()` becomes a ~50-line orchestrator. Each helper is independently unit-testable.
**Severity:** 🔴 High (refactor) — a single bad value in one of ~15 inline validators can mask failures in others; testing each validator in isolation requires constructing a full Config fixture.

---

### ZR-64 — `dictation_pipeline.run` has 10 duplicated stage-timing blocks (DRY)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:119-402` (`run`, 286 lines) — 10 inline occurrences of:
```python
_stage_t0 = time.perf_counter()
<step call>
_<name>_ms = (time.perf_counter() - _stage_t0) * 1000
```
at L172/177, L197/199, L202/204, L207/209, L212/214, L217/219, L222/224, L227/229, L232/234, L272/274. The consolidated `[PIPE-PERF]` log line at L277-298 hardcodes 8 named variables.
**Root Cause:** PERF-FIX-001 added the timing instrumentation but didn't abstract it.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py` (lines 119-402)
**Fix:** Replace with a small context manager:
```python
@contextlib.contextmanager
def _timed_stage(timings: dict[str, float], name: str):
    t0 = time.perf_counter()
    yield
    timings[name] = (time.perf_counter() - t0) * 1000

timings: dict[str, float] = {}
with _timed_stage(timings, "transcribe"):
    text = self._transcribe()
log.info(
    "[PIPE-PERF] total=%.0fms, stages: %s (cycle=%s)",
    total_ms,
    ", ".join(f"{k}={v:.0f}" for k, v in timings.items()),
    self._cycle_id,
)
```
**Severity:** 🔴 High (refactor) — adding an 11th stage means hand-copying the 3-line pattern AND hand-adding the variable to the consolidated log format string.

---

### ZR-66 — `level_monitor.py` 25+ module-level mutable globals (no DI)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/level_monitor.py:50-202` — 25+ module-level mutable globals:
```
L50:  _monitor_lock = threading.Lock()
L56:  _monitor_stream
L57:  _monitor_active
L58:  _monitor_level
L59:  _monitor_peak
L60:  _monitor_sample_rate
L61:  _monitor_mic_id
L70:  _level_processor
L83:  _level_ring_buffer
L84:  _level_worker_thread
L85:  _level_worker_stop_event
L86:  _level_worker_wake_event
L90:  _dropped_level_chunks
L96:  _last_drop_log_time
L114: _test_mode
L147: _test_chunks
# ... 25 total
```
All accessed via `global X` statements. `shutdown_controller._do_cleanup` has to do `from voice_typer.server import level_monitor; level_monitor.stop_monitoring()` instead of `app.level_monitor.stop()`.
**Root Cause:** Module-as-singleton pattern; tests must monkey-patch module attributes rather than inject instances.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py` (full file, 1313 lines)
**Fix:** Define a `class LevelMonitor:` encapsulating all 25 globals as instance attributes; expose a singleton `default_monitor = LevelMonitor()` for back-compat. Wire `VoiceTyperApp.__init__` to construct `self.level_monitor = LevelMonitor()`. Tests construct fresh instances per test.
**Severity:** 🔴 High (refactor) — concurrent test runs share state; no way to run two monitors in one process; `shutdown_controller` has to import the module to call its functions.

---

### ZR-67 — Push-event types are stringly-typed (270+ string literals across server + 26+ renderer subscribers)
**Status:** ❌ Not Fixed
**Description:** Python emitters — `voice_typer/server/dictation_pipeline.py:995 ("vocabulary_suggestion")`, `:1093 ("transcription_final")`; `waveform_bubble_wiring.py:168 ("bubble_level")`; `app.py:935 ("quit_app")`, `:1028 ("relaunch_app")`; `ipc_server.py:2229-2235` `_shutdown_allowlist` tuple of 5 inline strings allocated per `_send` call; `:2392 if msg_type in ("bubble_level", "waveform"):`; `:1098-1103 state_changed`. Total `rg '"type": "[a-z_]+"' voice_typer/server/` = 77 sites.

Renderer — 26 `usePythonEvent("transcription_final", ...)`, `usePythonEvent("recording_started", ...)`, etc. The TS-side `PythonPushEvent` union (`types/ipc.ts:461-496`) exists but `usePythonEvent`'s signature (`hooks/usePython.ts:283,287`) takes `type: string` not `PythonPushEvent["type"]` — comment at `types/ipc.ts:453-460` explicitly notes this is "EC-FIX-20" and not yet done.
**Root Cause:** TS union exists; narrowing is deferred. Python side has no enum at all.
**Progress:** None yet.
**Related Files:**
- Multiple Python emitter sites (77 total)
- `voice_typer/client/src/renderer/src/hooks/usePython.ts` (lines 263-285)
- `voice_typer/client/src/renderer/src/types/ipc.ts` (lines 453-496)
**Fix:**
**Python:** Define a `class PushEventType(str, Enum): TRANSCRIPTION_FINAL = "transcription_final"; BUBBLE_LEVEL = "bubble_level"; ...`. Replace string literals with `PushEventType.TRANSCRIPTION_FINAL.value`.

**TS:** Narrow the `usePythonEvent` overload:
```ts
export function usePythonEvent<T extends PythonPushEvent["type"]>(
    type: T,
    handler: (data?: Extract<PythonPushEvent, {type: T}>["data"]) => (() => void) | undefined,
): void;
```
A typo like `"past_failed"` then fails at compile time.
**Severity:** 🔴 High (refactor) — a typo in any of the 77 server-side emitters or 26 renderer subscribers silently produces a no-op event (or a never-firing subscription).

---

### ZR-70 — `ipc_server._send` re-allocates `_shutdown_allowlist` tuple on every call (~16 Hz)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:2229-2235` — Inside `_send` (called for every push event, ~16 Hz during recording):
```python
_shutdown_allowlist = (
    "relaunch_app",
    "quit_app",
    "transcription_final",
    "transcription_partial",
    "vocabulary_suggestion",
)
```
Fresh tuple allocation every call. Combined with 2 inline magic numbers at L2295 `_drain_cap = 100` and L2338 `_pending_cap = 1000`.
**Root Cause:** Tuple is in function body, not module-level constant.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (lines 2229-2235, 2295, 2338)
**Fix:** Hoist to module-level constants:
```python
_SHUTDOWN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "relaunch_app",
        "quit_app",
        "transcription_final",
        "transcription_partial",
        "vocabulary_suggestion",
    }
)
_TCP_PENDING_DRAIN_CAP = 100
_TCP_PENDING_BUFFER_CAP = 1000
```
Membership check becomes `msg_type in _SHUTDOWN_ALLOWLIST` (O(1) hash lookup, no allocation).
**Severity:** 🟡 Medium (working-but-suboptimal) — small per-call allocation at 16 Hz.

---

### ZR-71 — Duplicated `_get_rate_limiter` function (80 LOC × 2 in `ipc_server.py` + `ipc/rate_limiter.py`)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:158-236` + `voice_typer/server/ipc/rate_limiter.py:335-378` — duplicated `_get_rate_limiter(server)` body in two files, both ~80 lines including comments.

Comment at `ipc_server.py:139-157` explains: "tests in `tests/test_r4_f18_rate_limiter_concurrent_init.py` and `tests/test_cr_fixes.py` monkey-patch `ipc_server._RateLimiter` ... only observed if `_get_rate_limiter` looks up `_RateLimiter` from THIS module's globals at call time."

Comment at `rate_limiter.py:326-331` confirms: "this leaf copy ... is kept in sync with the canonical implementation in `ipc_server.py` (CR-14 deferred the package delete)."

PVT-MERGE-009 noted the same issue (and the dedup of `_RateLimiter` class itself is complete — only the function is duplicated).
**Root Cause:** Deferred cleanup from a prior partial refactor; tests rely on monkey-patching via the `ipc_server._RateLimiter` attribute path.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (lines 158-236)
- `voice_typer/server/ipc/rate_limiter.py` (lines 335-378)
- `tests/test_r4_f18_rate_limiter_concurrent_init.py`
- `tests/test_cr_fixes.py`
**Fix:** Make `ipc_server.py` re-export the leaf module's `_get_rate_limiter` AND patch the leaf module's `_RateLimiter` attribute on demand from tests. Concretely, tests change from `monkeypatch.setattr("voice_typer.server.ipc_server._RateLimiter", CountingLimiter)` to `monkeypatch.setattr("voice_typer.server.ipc.rate_limiter._RateLimiter", CountingLimiter)` (single source). Then `ipc_server.py:158-236` (80 lines) is replaced with `from voice_typer.server.ipc.rate_limiter import _get_rate_limiter` (1 line).
**Severity:** 🟡 Medium (refactor) — two 80-line bodies to keep in sync; the comment at L156 explicitly says "kept in sync."

---

### ZR-72 — `recorder.__del__` has 6 sequential `contextlib.suppress(Exception)` blocks (DRY)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:744-757` (`__del__`, 6 sequential `with contextlib.suppress(Exception):` blocks):
```python
def __del__(self) -> None:
    """Best-effort cleanup. Must never raise."""
    with contextlib.suppress(Exception):
        self.shutdown_mic_watcher()
    with contextlib.suppress(Exception):
        self._recording_event.clear()
    # ... 4 more
```
6 near-identical 2-line blocks.
**Root Cause:** Six independent best-effort teardown calls; no shared helper.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py` (lines 744-757)
**Fix:**
```python
def __del__(self) -> None:
    """Best-effort cleanup. Must never raise."""
    for step in (
        self.shutdown_mic_watcher,
        self._recording_event.clear,
        self._worker_stop_event.set,
        self._event_stop_event.set,
        self._device_health_stop_event.set,
        self._teardown_stream,
    ):
        with contextlib.suppress(Exception):
            step()
```
**Severity:** 🟡 Medium (refactor) — minor readability / DRY violation.

---

### ZR-73 — `ipc_server.main()` ~260 lines mixing 10 phases (faulthandler, single-instance, app construction, signals, run)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:2549-2807` (`main()`, ~260 lines) mixes: (1) `_set_process_metadata()`, (2) faulthandler + SIGUSR1 wiring, (3) `parse_ipc_args()` call, (4) `_setup_logging()`, (5) Tauri-sidecar env-var check + conditional single-instance lock, (6) `VoiceTyperApp()` construction with try/except + diagnostic write, (7) ipc_server construction, (8) signal-handler registration, (9) `app.start()` invocation with crash-handler fallback, (10) final `sys.exit(EXIT_CRASH)` path.

EC-8 noted this and extracted `parse_ipc_args()` as a partial step; the rest remains inline.
**Root Cause:** Partial refactor (EC-8 extracted arg parsing); the rest of `main()` was not similarly decomposed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (lines 2549-2807)
**Fix:** Extract named helpers:
- `_install_faulthandler_and_signals()`
- `_acquire_single_instance_mutex_or_skip()` → mutex | None
- `_construct_app_with_diagnostics()` → app
- `_install_signal_handlers(server)`
- `_run_app_until_exit(app, server)`

`main()` becomes ~25 lines of sequential calls.
**Severity:** 🟡 Medium (refactor) — hard to reason about the startup sequence.

---

### ZR-74 — `ThemeSettingsSection.getCurrentThemeColors` 112 lines with 5-branch switch-on-string
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:280-376` (`getCurrentThemeColors`, 112 lines, 5 branches on `themeId`) and `392-421` (`getThemePreviewColors`, 30 lines, 3 branches). `getCurrentThemeColors` branches on: `currentPresetId === "default" || ""`, `=== "custom"`, `THEMES.find(...)` lookup, DOM `getComputedStyle` fallback, final hardcoded fallback. Each branch returns `{light, dark}` of the same shape.
**Root Cause:** No strategy/registry; switch-on-string.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx` (lines 280-421)
**Fix:** Strategy table keyed by theme-id category:
```ts
const THEME_COLOR_SOURCES: Record<string, ThemeColorSource> = {
    default: { getColors: ({ keys }) => ({...DEFAULT_CUSTOM_LIGHT}, {...DEFAULT_CUSTOM_DARK}) },
    custom: { getColors: ({ keys, customDraft }) => /* ... */ },
    // ...
};
function getCurrentThemeColors(preset: string, customDraft) {
    const source = THEME_COLOR_SOURCES[preset] ?? THEME_COLOR_SOURCES.builtin;
    return cached(preset, source.getColors({ ... }));
}
```
**Severity:** 🟡 Medium (refactor) — adding a new theme source requires another inline `if` branch in both functions.

---

### ZR-75 — `bubble-window.createBubbleWindow` 192 lines mixing 7 phases
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/windows/bubble-window.ts:198-388` (`createBubbleWindow`, 192 lines) does: early-return guard, position resolution, BrowserWindow construction with 14 webPreferences, `setAlwaysOnTop` with 2-level fallback, `setVisibleOnAllWorkspaces` with fullscreen check, 5 webContents.on handlers (`did-fail-load`, `did-finish-load`, `render-process-gone` with crash-storm detection + reload, `preload-error`, `console-message`), URL resolution + loadURL/loadFile, `state.bubbleWindow` assignment + `closed` + `moved` handlers.
**Root Cause:** Single function grew as lifecycle handlers accreted (PVT-068, GT-10, PVT-G5-080/081, SEC-014/024/026).
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts` (lines 198-388)
**Fix:** Extract:
- `_buildBubbleBrowserWindowOptions(): BrowserWindowConstructorOptions`
- `_negotiateAlwaysOnTop(win)`
- `_configureVisibilityAllWorkspaces(win)`
- `_attachBubbleWebContentsHandlers(win)`
- `_loadBubbleUrl(win)`
- `_attachBubbleWindowLifecycleHandlers(win)`

`createBubbleWindow` becomes ~15 lines.
**Severity:** 🟡 Medium (refactor) — window-creation logic and event-handler attachment are conflated; hard to test the crash-storm reload logic in isolation.

---

### ZR-76 — 6 inline error-envelope construction sites in `ipc_server.py` (DRY)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:1155-1164, 1195-1204, 1259-1268, 1590-1601, 1639-1651, 1795-1804` — 6 inline error-envelope construction sites. Each site repeats:
```python
err: dict[str, object] = {
    "type": "error",
    "data": {"code": "<code>", "message": "<msg>"},
}
if isinstance(msg, dict) and "id" in msg:
    err["id"] = msg["id"]
self._send(err, _client=client)
```
6 occurrences, ~7 lines each.
**Root Cause:** Copy-pasted envelope construction; the `_shutting_down_error` helper at L1784-1804 was extracted for one case but the others remain inline.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py` (lines 1155-1164, 1195-1204, 1259-1268, 1590-1601, 1639-1651, 1795-1804)
**Fix:** Extract a single helper:
```python
def _send_error_envelope(
    self, code: str, message: str, *, msg: dict | None = None, _client: object | None = None
) -> None:
    err: dict[str, object] = {"type": "error", "data": {"code": code, "message": message}}
    if isinstance(msg, dict) and "id" in msg:
        err["id"] = msg["id"]
    self._send(err, _client=_client)
```
Replace 6 inline blocks with `self._send_error_envelope(...)`. Combined with ZR-68 (use `ErrorCodes.RATE_LIMITED` instead of `"rate_limited"`), each error site becomes a single 1-line call.
**Severity:** 🟡 Medium (refactor) — bug in one site (e.g. missing the `id` propagation) must be fixed 6×; the 6 sites diverge in subtle ways.

---

---

### ZR-78 — `tray.start()` 144 lines + `_build_menu` 113 lines (long methods)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray.py:437-580` (`start`, 144 lines) and `965-1078` (`_build_menu`, 113 lines). `start()` does: app-state restoration, hotkey backend wiring, icon selection, `Tray()` construction, event-handler attachment, `bg_work()` invocation. `_build_menu()` builds 10+ menu items inline with per-item callbacks, conditional visibility flags, and accelerator bindings.
**Root Cause:** Long methods that grew as menu items / hotkey backends were added.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py` (lines 437-580, 965-1078)
**Fix:** Extract:
- `_restore_tray_state()`
- `_wire_hotkey_backends()`
- `_resolve_tray_icon()`
- `_construct_tray_with_handlers()`
- For menu: a `_MENU_SPEC: list[MenuItemSpec]` data table + a `_build_menu_item(spec)` renderer.

`start()` becomes ~20 lines; `_build_menu` becomes a `for spec in self._MENU_SPEC: menu.append(self._build_menu_item(spec))` loop.
**Severity:** 🟡 Medium (refactor) — adding a menu item or hotkey backend requires editing both methods.

---

### ZR-79 — `clipboard.paste()` 349 lines + `_is_safe_paste_target` 198 lines (long methods)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard/manager.py:627-976` (`paste`, 349 lines) interleaves: snapshot registration + thread spawn, stuck-modifier release, safety-target check, rate-limit check, paste_enabled gate, keystroke send, return-value bookkeeping. `_is_safe_paste_target()` is a 198-line function combining elevated-target check (calls `_is_elevated_target`), password-field check (calls `_is_password_field`), content-editable check (calls `_is_content_editable`), with platform branches.

EC-27 noted this; the specific `paste()` extraction plan is the new contribution.
**Root Cause:** Accreted over many hardening fixes (DP1-DP4, CLIP-8, ER-72, SEC-006, etc.).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py` (lines 627-976, 212-410)
**Fix:** Split `paste()` into:
- `_register_pending_restore(snapshot, expected, delay)`
- `_release_stuck_modifiers_safe()`
- `_check_paste_safety(force)` → bool
- `_check_rate_limit()` → bool
- `_send_paste_keystroke()` → bool

`paste()` becomes ~25 lines of sequential gates.
**Severity:** 🟢 Low (refactor) — already flagged by EC-27.

---

### ZR-81 — `parakeet_engine.load` 292 lines + `transcription._pre_download_model` 190 lines (duplicated consent-gate pattern)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/parakeet_engine.py:340-631` (`load`, 292 lines) and `voice_typer/server/transcription.py:590-790` (`_pre_download_model`, 190 lines) — duplicated download-with-retry + consent-check + progress-callback pattern. `parakeet_engine.load` does: imports check, cache check, HuggingFace consent gate, `_download_with_retry(snapshot_download, ...)`, model load. `transcription._pre_download_model` follows the same shape.

The consent-gate block is duplicated between them; comment at `parakeet_engine.py:381-389` explicitly says "mirrors `transcription.py::_pre_download_model` (lines ~821-849) and `service.py::_require_huggingface_consent`."
**Root Cause:** Three call sites (`parakeet_engine.load`, `transcription._pre_download_model`, `service._require_huggingface_consent`) all need the same consent gate.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py` (lines 340-631)
- `voice_typer/server/transcription.py` (lines 590-790)
**Fix:** Extract a single `_require_huggingface_consent(config, model_id, progress_callback)` helper into `asr_utils.py` (where `_download_with_retry` already lives). All three sites call it.
**Severity:** 🟢 Low (refactor) — consent-gate logic drifts across the 3 sites.

---

### ZR-82 — `level_monitor.py` (1313 lines) mixes 2 concerns: monitoring + test recording (SPLIT REQUIRED)
**Status:** ❌ Not Fixed (Spaghetti / monolith detection)
**Description:** `voice_typer/server/level_monitor.py` (1313 LOC). Module docstring (lines 1-32) explicitly states the file serves "TWO purposes": (1) continuous level monitoring and (2) ad-hoc microphone test recording.

Section headers cleanly partition the two concerns:
- "── Public API: monitoring" (line 205) → lines 205-527 (~322 LOC) — `is_monitoring` / `get_level` / `get_level_diagnostics` / `update_level_processor` / `start_monitoring` / `stop_monitoring`
- "── Public API: test recording" (line 528) → lines 528-967 (~440 LOC) — `is_test_active` / `start_test_recording` / `stop_test_recording` / `update_test_filters` / `cancel_test_recording` / `_do_auto_stop_test` (line 971) / `_cancel_test_locked` (line 1009)

Separate state blocks: monitor state (lines 48-96) vs test-recording state (lines 98-153, incl. `_test_chunks` / `_test_raw_chunks` / `_test_filtered_chunks` / `_test_auto_stop_timer`).
**Root Cause:** Module grew by accretion. The "share one sounddevice stream to avoid PortAudio device conflict" optimization (docstring lines 10-13) was implemented by co-locating two concerns in one module instead of giving the test recorder its own module that imports the shared stream accessor.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py` (1313 lines)
- `tests/test_level_monitor.py` (existing)
**Fix:**
- `voice_typer/server/level_monitor.py` (≤ ~500 LOC) — monitor state, worker thread, `_process_level_chunk`, public monitoring API, plus a thin `get_shared_monitor_stream()` accessor for the test recorder.
- `voice_typer/server/microphone_test_recorder.py` (~500 LOC) — all `_*test_*` state, the test-recording public API, `_do_auto_stop_test`, `_cancel_test_locked`, `update_test_filters`. Imports the shared stream + lock from `level_monitor`.
- Tests in `tests/test_level_monitor.py` (existing) split accordingly into `tests/test_microphone_test_recorder.py` for the test-recording classes.
**Severity:** 🟡 Medium — two distinct features (live level bar + mic-test recording) cannot be modified independently; merge conflicts on `_monitor_lock` and the test-chunk deques.

---

### ZR-84 — `autostart_launcher.py` (849 lines) mixes 6 unrelated helper groups (SPLIT REQUIRED)
**Status:** ❌ Not Fixed (Spaghetti / monolith detection)
**Description:** `voice_typer/server/autostart_launcher.py` (849 LOC) — the OS-login entry point per the module docstring (lines 1-71). Top-level helpers span 6 unrelated concerns:
- PID file mgmt: `_read_ipc_port_from_pid_file` (129), `_config_dir` (198), `_pid_file` (208), `_write_pid_file` (487) — ~80 LOC
- Port probing: `_is_port_open` (233), `_wait_for_backend_ready` (255) — ~50 LOC
- Tauri detection/spawn: `_tauri_binary` (290), `_is_tauri_mode` (356), `_spawn_tauri_host` (396) — ~150 LOC
- Electron spawn: `_launch_electron_built` (436), `_ensure_built_and_launch` (505), `_spawn_npm_run_dev` (624) — ~140 LOC
- Focus redirection: `_focus_running_app` (540) — ~85 LOC
- Logging setup + CLI: `_setup_logging` (220), `_parse_delay` (677) — ~30 LOC
- Main entry: `launch` (701) is 140 LOC, `main` (843) — ~145 LOC

Already partially split: imports `_build_electron`, `_electron_binary`, `_electron_log_files`, `_npm_command`, `_spawn_flags` from `voice_typer.server._electron_build` — so the extraction pattern is established but incomplete.
**Root Cause:** Partial refactor. `_electron_build.py` was extracted (good), but the Tauri spawn path, the focus-single-instance path, the PID-file helpers, and the port-readiness probe were left in-place because they pre-date the Tauri cutover and touch cross-cutting state.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/autostart_launcher.py` (849 lines)
- `voice_typer/server/_electron_build.py` (existing partial extraction)
**Fix:**
- `voice_typer/server/autostart/tauri_spawn.py` (~150 LOC) — `_tauri_binary`, `_is_tauri_mode`, `_spawn_tauri_host`.
- `voice_typer/server/autostart/electron_spawn.py` (~150 LOC) — `_launch_electron_built`, `_ensure_built_and_launch`, `_spawn_npm_run_dev`. (Could be merged into `_electron_build.py`.)
- `voice_typer/server/autostart/pid_file.py` (~80 LOC) — `_read_ipc_port_from_pid_file`, `_config_dir`, `_pid_file`, `_write_pid_file`.
- `voice_typer/server/autostart/port_probe.py` (~50 LOC) — `_is_port_open`, `_wait_for_backend_ready`.
- `voice_typer/server/autostart/focus.py` (~85 LOC) — `_focus_running_app`.
- `voice_typer/server/autostart_launcher.py` (~250 LOC, thin) — `_setup_logging`, `_parse_delay`, `launch()`, `main()`. Imports the helpers above.
**Severity:** 🟡 Medium — every platform-specific spawn tweak (Tauri vs Electron vs npm-dev) lands in the same file as the port probe and the PID file; conflicts at the `launch()`-decision-tree merge point.

---

### ZR-86 — `src-tauri/src/sidecar/ws.rs` (1142 lines) — 4 task functions could be split into `ws/` submodule (BORDERLINE)
**Status:** ❌ Not Fixed (Spaghetti / monolith detection — borderline)
**Description:** `src-tauri/src/sidecar/ws.rs` (1142 LOC total: 923 production + 218 tests). Production is structured as 8 free functions plus the `ALLOWED_EVENT_TYPES` const:
- `cleanup_and_trigger_respawn` (120) ~40 LOC
- `trigger_respawn_off_thread` (162) ~28 LOC
- `respawn_supervisor_sender` (190) ~40 LOC
- `ws_connect` (230) ~52 LOC
- `queue_auth_and_store_ws_tx` (283) ~44 LOC
- `spawn_writer_task` (328) ~45 LOC
- `wait_for_auth_ok` (374) ~144 LOC
- `spawn_reader_task` (519) ~238 LOC ← single largest fn
- `spawn_heartbeat_task` (758) ~135 LOC
- `translate_event_name` (894) ~30 LOC ← pure name-mapping table

The reader task (519-757) and the heartbeat task (758-893) have no shared internals beyond `SidecarState` — they could live in their own files.
**Root Cause:** The WS reconnect module accreted 4 task functions end-to-end; each is independent enough to be a submodule but extraction was never prioritized because the file is "single-concept" (WS lifecycle).
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs` (1142 lines)
**Fix:**
- `src-tauri/src/sidecar/ws/mod.rs` (~200 LOC) — `WsStream` alias, `ALLOWED_EVENT_TYPES`, `ws_connect`, `queue_auth_and_store_ws_tx`, `wait_for_auth_ok`, cleanup/respawn helpers.
- `src-tauri/src/sidecar/ws/reader.rs` (~240 LOC) — `spawn_reader_task`.
- `src-tauri/src/sidecar/ws/writer.rs` (~50 LOC) — `spawn_writer_task`.
- `src-tauri/src/sidecar/ws/heartbeat.rs` (~140 LOC) — `spawn_heartbeat_task`.
- `src-tauri/src/sidecar/ws/event_translate.rs` (~30 LOC) — `translate_event_name` + its unit tests.
- Tests stay co-located (Rust convention) but move with their function.
**Severity:** 🟢 Low — borderline; the file is cohesive (single concept) but at 923 LOC of production code it exceeds the 800-line threshold.

---

### TY-2 — High: Eager `import numpy as np` at module top of 5 startup-path files adds ~335ms to every cold start
**Status:** ❌ Not Fixed
**Description:** `numpy` is imported eagerly at the top of `voice_typer/server/app.py:18`, `recording/__init__.py:135`, `audio_processor.py:22`, `audio_quality.py:11`, `transcription.py:12`. Measured `python -X importtime` shows numpy alone costs **335ms** cumulative on warm Linux cache (cold Windows cache historically 500-1000ms). None of these imports are needed during `VoiceTyperApp.__init__` or `start()` — numpy is only touched at first audio chunk (≥1s after dictation begins). The codebase already ships a lazy-import helper (`voice_typer/server/_lazy_import.py:lazy_module`) used for `sounddevice` and `pystray` for the exact same cold-start reason — numpy was simply missed.
**Root Cause:** Verified via `python -X importtime -c "from voice_typer.server.app import VoiceTyperApp"` → 533ms cumulative, of which 335ms is numpy.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:18`
- `voice_typer/server/recording/__init__.py:135`
- `voice_typer/server/audio_processor.py:22`
- `voice_typer/server/audio_quality.py:11`
- `voice_typer/server/transcription.py:12`
- `voice_typer/server/_lazy_import.py` (helper to use)
**Fix:** Replace each `import numpy as np` with `from voice_typer.server._lazy_import import lazy_module; np = lazy_module("numpy")`. The proxy is transparent — `np.ndarray` annotations need `from __future__ import annotations` (verify each file has it). The proxy re-reads `sys.modules` on every access, so `monkeypatch.setattr(np, ...)` in tests keeps working.
**Severity:** 🔴 High
**Overlaps:** ER-73 (estimated 50-100ms; actual measured 335ms — 3-7× higher)

---

### TY-3 — High: Bubble `useAudioLevels` rAF loop runs at 60fps continuously in `always_visible` mode, even when not recording
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/bubble-components.tsx:217-251` runs a `requestAnimationFrame` loop at 60fps whenever the bubble window is visible. The loop is gated only on `visibleRef.current` — it is NOT gated on recording state. Per frame it does: `getComputedStyle(document.documentElement)` (forced style recalc ~0.05-0.2ms), 2× `.getPropertyValue("--var").trim()` (string allocations), then a 7-iteration inner loop writing `el.style.height`, `el.style.backgroundColor` (UNCHANGED value, theme doesn't change per frame), `el.style.opacity` per dot. Per-frame cost ~0.3-0.5ms × 60fps = **18-30ms/sec = 1.8-3% of one core continuously** while the bubble is visible in `always_visible` idle mode.
**Root Cause:** Verified — animation loop is gated on `visibleRef.current` only, not `mode === "recording"`. `barColor` is recomputed every frame even though it changes only on theme switch.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/bubble-components.tsx:217-251, 400-433`
**Fix:**
1. Gate the rAF loop on `mode === "recording"` (matches PVT-043's proposed fix).
2. Cache `barColor` in a `useRef`; invalidate via `MutationObserver` on `document.documentElement.classList` + on `themechange` IPC event.
3. Hoist `el.style.backgroundColor = barColor` out of the per-frame loop into a `useEffect` that runs when `barColorRef.current` changes.
4. Read CSS vars via `getComputedStyle` only on first frame + on themechange (not 60×/sec).
5. Hoist `DOT_INDICES` to module-level constant. Use a stable `useCallback` for the ref setter.
**Severity:** 🔴 High
**Overlaps:** PVT-043 (deferred — TY-3 adds NEW evidence: per-frame getComputedStyle + per-frame backgroundColor re-writes that PVT-043 did not enumerate)

---

### TY-4 — High: `level_monitor.py` has ZERO disconnect detection — USB/BT mic unplug freezes the level bar with no IPC event
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/level_monitor.py:432-439` constructs `sd.InputStream` with NO `finished_callback` parameter. `grep` for `finished_callback|_stream_finished|disconnect|device_lost` returns ZERO meaningful matches in the file. The callback only does `_level_ring_buffer.append((indata.copy(), status))` + `_level_worker_wake_event.set()` — it never inspects `status` flags, never detects zero-filled `indata`, never spawns a recovery handler. When the mic is unplugged while the level monitor is the active stream (the default state whenever the app window is open and the user isn't actively dictating), PortAudio stops delivering callbacks. `_level_worker_loop` blocks on `_level_ring_buffer.get()` forever, `_monitor_active` stays True, and the renderer's level bar (`useRMSLevel` polling at ~10 Hz via IPC) freezes at the last reported value.
**Root Cause:** Verified — the recorder's hot-plug handling (`_stream_finished_callback`, `_device_health_checker_loop`, zero-fill detection) was never ported to `level_monitor.py`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py:387-439, 1095-1166`
- `voice_typer/client/src/renderer/src/hooks/usePython.ts` (event subscription)
**Fix:**
1. Pass `finished_callback=_level_stream_finished` to `sd.InputStream`. Implement handler that sets `_monitor_active=False` + emits a `device_lost` IPC event (`{type: "device_lost", data: {source: "level_monitor"}}`).
2. In the worker loop, detect N consecutive zero-filled chunks (matching the recorder's pattern) and emit the same event.
3. Optionally auto-restart monitoring on the OS default device (mirroring `_handle_device_disconnect`).
4. Add `device_lost` to ALLOWED_EVENT_TYPES in `src-tauri/src/sidecar/ws.rs`.
5. Add a renderer hook in `usePython.ts` to subscribe to `device_lost` and surface a "Microphone disconnected" banner.
**Severity:** 🔴 High
**Overlaps:** None (recorder's hot-plug handling was reviewed under TY-7; this is the level-monitor-specific gap)

---

### TY-5 — High: `_handle_device_disconnect` except branch leaves `_device_disconnected=True` forever — recording silently captures 30-60s of silence after mic unplug
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:959-960` — the `except Exception` branch in `_handle_device_disconnect` logs the error and falls through. `self._device_disconnected` stays True, no retry is scheduled, no backoff. The `_device_health_checker_loop` (`device_manager.py:294-295`) checks `if self._device_disconnected: continue` — so once the flag is set, the health-checker NEVER re-probes. Even if the user plugs in a new mic mid-session, the recorder does NOT auto-recover: `_device_disconnected=True` suppresses the zero-fill detector (recorder.py:2578), the health-checker continues to skip, and the only path forward is the user pressing the hotkey to stop+start. The user perceives this as "the app stopped working" when in reality the mic is gone.
**Root Cause:** Verified — the except branch at line 959 is missing the recovery state-clearing logic. ER-16 prior finding still unapplied.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:809-960`
- `voice_typer/server/recording/device_manager.py:291-321`
**Fix:**
1. In the `except Exception` branch at recorder.py:959, do NOT leave `_device_disconnected=True`. Either clear the flag so the next health-checker cycle (30s) re-triggers the handler, OR add an explicit backoff-retry loop inside the handler (3 attempts with 2s/5s/10s sleep) before giving up.
2. When max retries is reached, fire a dedicated `on_device_lost` callback (not `on_silence_auto_stop`) so the UI shows "Microphone disconnected" rather than "silence detected".
3. When the `MicrophoneDeviceWatcher` fires `_invalidate_device_cache`, proactively re-attempt restart if `_device_disconnected=True` — this would let the recorder auto-recover when the user plugs in a new mic.
**Severity:** 🔴 High
**Overlaps:** ER-16 (still unapplied)

---

### TY-6 — High: VAD double-resamples already-resampled audio on 48kHz devices → biased-low speech probabilities → premature silence auto-stop
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:2836-2842` — when `self._effective_sr not in (8000, 16000)` (true for 48kHz devices), the VAD branch enters and does `resample_poly(filtered.ravel(), up, down)` with `up=1, down=3`. But `filtered` was ALREADY resampled to 16kHz by `process_chunk` at line 2667. The `resample_poly` decimates the 16kHz audio 3:1 → ~170 samples presented to Silero as "16kHz" audio. Silero expects 512 samples at 16kHz → padding/truncation path triggers → Silero sees ~170 real + ~342 zeros → speech probability systematically biased low → silence_timer accumulates faster → recording auto-stops prematurely mid-sentence. ALSO wastes ~0.5-2ms per chunk × 16Hz = 8-32ms/s of CPU on a redundant `resample_poly` call.
**Root Cause:** Verified — VAD path was never updated to use the post-`process_chunk` sample rate. Same root cause as TY-1 (`_buffer_sr` never set).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:2753, 2836-2854`
**Fix:** Use `self._buffer_sr` (per TY-1) as the source rate for the VAD branch — `if self._buffer_sr not in (8000, 16000):`. When AudioProcessor is active and `_buffer_sr == 16000`, the VAD branch is skipped entirely (the post-`process_chunk` audio is already at Silero's expected rate). When AudioProcessor is None, `_buffer_sr = _effective_sr` and the existing behavior is preserved. Cache `(up, down)` at `start()` time as `_cached_vad_resample_up_down` to avoid per-chunk `math.gcd` recomputation.
**Severity:** 🔴 High
**Overlaps:** S2-CR-20 / ER-8 (still unapplied)

---

### TY-7 — High: Hot-plug restart never calls `_rebuild_audio_processor(force_sr=...)` → every chunk resampled on the RT thread after hot-plug
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/audio_quality_controller.py:130-186` defines `_rebuild_audio_processor(force_sr=...)` API, and the `audio_processor.py:286` comment admits "the recorder should invoke set_sample_rate with the device's native rate so this branch is never taken (XV-31 mitigation)". But `grep -rn 'force_sr=' voice_typer/server/` returns ZERO matches — the API is never called with `force_sr` set. After hot-plug, `_effective_sr=48000` but `AudioProcessor._sample_rate=16000`. Every `process_chunk` call hits the resample branch (audio_processor.py:283-304), allocating a fresh scipy `resample_poly` output (~2KB at 16Hz) and running the FIR filter on the RT thread (5-50ms per chunk × 16Hz = 80-800ms/s of RT-thread CPU). This is exactly the workload XV-31 was meant to eliminate.
**Root Cause:** Verified — `_rebuild_audio_processor(force_sr=...)` API exists but is never called with `force_sr` set. Documented as known gap in audio_processor.py:286 comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:889, 943, 1665, 1736-1739`
- `voice_typer/server/audio_quality_controller.py:130-186`
- `voice_typer/server/audio_processor.py:188, 283-304`
**Fix:**
1. In `start()` after the device loop succeeds (around line 1739) and in `_handle_device_disconnect` after `self._effective_sr = candidate_sr` (line 943), call `self._app.audio_quality._rebuild_audio_processor(force_sr=candidate_sr)` if `candidate_sr != self._app._audio_processor.sample_rate`.
2. Guard with `try/except` so a failure doesn't break the recording.
3. Add a test that simulates hot-plug (start at 16k → device disconnect → restart at 48k) and asserts `AudioProcessor.sample_rate == 48000` after restart.
**Severity:** 🔴 High
**Overlaps:** XV-31 (still unapplied)

---

### TY-8 — High: `get_history` does `SELECT *` with no text projection — at ~50 long-form dictations the 1MB WS frame cap is exceeded and the response is silently dropped
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/history_db.py:2210-2218` runs `SELECT * FROM transcriptions ORDER BY timestamp DESC LIMIT ? OFFSET ?` — materializes the full `text` column with NO row-level size cap. `_HISTORY_LIMIT_MAX=500`. Worst case: 500 rows × 10KB avg text = ~5MB JSON. The Tauri path has a 1MB WS frame cap (`sidecar_ws.py:651-657`) — frames exceeding the cap are SILENTLY DROPPED. The Electron path has a 4MB TCP buffer cap (PVT-041). The Dashboard refresh calls `get_history({limit: 200})` on every `transcription_final` event (Dashboard.tsx:264) — once dictation texts grow past ~5KB avg × 200 rows = 1MB, the call silently fails. User-visible: Dashboard "Total Dictations" stat never updates, History page "load more" hangs.
**Root Cause:** Verified — `SELECT *` with no text projection/truncation, combined with a 1 MiB WS frame cap and no server-side awareness of the cap.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:2210-2218, 2301-2309, 2341-2342`
- `voice_typer/server/sidecar_ws.py:651-657, 710`
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:264, 287`
**Fix:**
1. Add a `text_truncated` flag + `SUBSTR(text, 1, 500) AS text_preview` projection to `get_recent` / `search` / `get_favorites` queries.
2. Expose full text via a separate `get_transcription_text(id)` endpoint that the renderer fetches on demand (when the user expands a row).
3. At `sidecar_ws.py:710`, add the same `_MAX_FRAME_BYTES` check that the push-event path uses at line 652 — currently the dispatch-response path is uncapped and will error out inside `websocket.send` for any oversized result.
4. Update Dashboard to call new `get_history_count` (see TY-17) for the total stat and keep `get_history(limit: 200)` for the daily-activity / streak computation.
**Severity:** 🔴 High
**Overlaps:** PVT-041 (TCP 4MB cap — adjacent; this finding is server-side serialization pattern, not client-side cap)

---

### TY-9 — High: Shutdown parallel-teardown refactor (XV-7) never landed — 4+16 pre-existing test failures + 80-90s sequential shutdown
**Status:** ❌ Not Fixed
**Description:** `tests/test_shutdown_parallel.py` (4 failing) and `tests/test_shutdown_controller_de.py` (16 failing) pin a planned XV-7 refactor where `_do_cleanup` runs the independent middle teardowns in a `ThreadPoolExecutor` with a shared 10s deadline. The tests assert that 14 `_teardown_*` helpers exist on the controller and that they run concurrently (`assert elapsed < 0.5`). Production code does NOT implement this — `_do_cleanup` is a 584-line sequential method with per-block 5s timeouts. Net result: production shutdown is ~80-90s worst-case sequential (matches ER-4 finding), and 20 tests provide false-negative coverage.
**Root Cause:** Verified — test/code drift. The XV-7 / DE-* tests were authored against an intended refactor that was either reverted or never merged.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py:271-855`
- `tests/test_shutdown_parallel.py:150-543`
- `tests/test_shutdown_controller_de.py`
**Fix:** Land the XV-7 parallel refactor:
1. Extract 14 `_teardown_*` helpers from the body of `_do_cleanup`: `_teardown_timers_and_recording`, `_teardown_recorder`, `_teardown_level_monitor`, `_teardown_restore_volume`, `_teardown_hotkeys`, `_teardown_crash_recovery`, `_teardown_history_db`, `_teardown_waveform_wiring`, `_teardown_sounddevice`, `_teardown_electron`, `_teardown_pid_file`, `_teardown_mutex_handle`, `_teardown_devnull_files`, `_teardown_event_bus`.
2. Keep `ipc_server.stop` + WS pool drain as the early bookend (sequential, before the parallel batch).
3. Keep `tray.stop` as the late bookend (sequential, after the parallel batch).
4. Run the 14 helpers via `_run_parallel_with_timeout` with a shared 10s deadline (already exists at line 151).
5. Each helper logs its own outcome; failures do not propagate.
6. Verify all 20 previously-failing tests now pass.
**Severity:** 🔴 High
**Overlaps:** ER-4 (sequential ~80s), AC-87 (_do_cleanup 466-line method), AC-137 (shutdown_controller 1280 LOC), GT-69 (_shutting_down TOCTOU)

---

### TY-10 — High: `server_started` handshake blocks UI 600-810ms — VoiceTyperApp construction on the critical path
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:2638` constructs `VoiceTyperApp()` BEFORE `sidecar_ws.run()` emits `server_started` (sidecar_ws.py:771). VoiceTyperApp construction costs ~600-810ms warm-cache (Linux) / 1.5-3s Windows cold — including Config.load, HistoryDB writer-ready wait, Recorder/AudioProcessor/AudioQualityAnalyzer/TrayIcon/ClipboardManager/CrashRecovery/DuckCrashRecovery/TemplateManager/VocabularyManager/VolumeDucker/WaveformBubble construction. The Tauri host (`spawn.rs:120-123`) blocks reading stdout for `server_started` the entire time. The Tauri main window IS created at builder time (`tauri.conf.json:25 "visible": true`), so the chrome paints immediately — but the renderer cannot make any IPC call until the WS connects, which cannot happen until `server_started` arrives. Net effect: dashboard shows a dead "connecting…" UI for ~600-810ms after the window appears.
**Root Cause:** Verified — `server_started` was designed to fire after VoiceTyperApp is fully wired (so the first WS frame can be dispatched immediately), but it bundles ALL of `VoiceTyperApp.__init__`'s work onto the critical path. None of these subsystems are needed to ANSWER a WS frame — only Config + IPCServer are.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py:2610, 2638, 2682-2704`
- `voice_typer/server/sidecar_ws.py:771`
- `src-tauri/src/sidecar/spawn.rs:120-246`
- `voice_typer/server/app.py` (VoiceTyperApp.__init__ lines 124-454)
**Fix:**
1. Emit `server_started` BEFORE constructing VoiceTyperApp — bind the WS server first, queue incoming frames until VoiceTyperApp is ready, and construct VoiceTyperApp on a background task that signals a `ready` event.
2. Move `sidecar_ws.run()` ahead of `VoiceTyperApp()` in `main()`.
3. Pass a deferred-app factory; `_make_dispatch(server)` blocks (or queues) frames until `server.app` is set.
4. The existing `server.push({"type": "ready"})` pattern (ipc_server.py:2768) already exists for the TCP path — reuse it.
5. The renderer already handles `connecting` → `ready` state transitions.
**Severity:** 🔴 High
**Overlaps:** ER-1 (Electron main window gated on Python backend TCP auth — prior fix did not move VoiceTyperApp construction off that path), ER-27 (Eager TemplateManager / VocabularyManager init), ER-73 (numpy/crash_handler eager imports)

---

### TY-11 — High: Parakeet model + CUDA context held in VRAM persistently at idle — wastes 2.4GB VRAM + 5-15W GPU power for entire app lifetime
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/parakeet_engine.py:197` populates `self._model` via `from_pretrained(device_map=effective_device, dtype=float16)` at line 568-575 (~2.4GB VRAM in fp16). It is ONLY released via `unload()` (line 1213). `model_manager.py:71` has `_MAX_LOADED_MODELS = 2` LRU cap, but in practice only 1 backend is loaded at a time because `change_model`/`set_active_backend` ALWAYS unloads the OLD backend before loading the new one. `grep` for `idle_timeout|unload_after|auto_unload|idle_unload` across `voice_typer/server/` → ZERO matches. There is NO idle timer that calls `unload()` to release model memory between dictations. `release_gpu_memory()` is ONLY called from `parakeet_engine.unload()`. Idle cost on a laptop with NVIDIA dGPU: ~2.4GB VRAM (model weights) + ~300-500MB VRAM (CUDA context + caching allocator) + ~50-100MB RSS. For a user who dictates 5 min/day and leaves the app running 24/7, the model sits in VRAM 23h55m/day unused, wasting ~200-340 Wh/day on battery. CPU-fallback users see ~2.4GB RSS pinned.
**Root Cause:** Verified — ModelManager has no idle/unload-after-N-minutes-of-inactivity timer. The LRU eviction only fires when a NEW model is loaded and >2 are present, which never happens in normal single-backend usage.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:197, 568-575, 1213-1218`
- `voice_typer/server/model_manager.py:71, 1028-1076`
- `voice_typer/server/asr_utils.py:67-105` (release_gpu_memory)
- `voice_typer/server/dictation_pipeline.py` (touch_active_model call sites)
**Fix:**
1. Add an idle-unload timer to ModelManager. After N minutes (configurable, default 10-15 min) with no `touch_active_model()` call (i.e. no dictation), call `unload()` on the active backend + `release_gpu_memory()`.
2. Reload on next `toggle_dictation` — the existing `ensure_active_engine_loaded()` path handles re-init.
3. Track last-activity time via the existing `touch_model()` mechanism (extend `_model_access_times` to be checked by a background sweeper thread, or use a `threading.Timer` rescheduled on each `touch_active_model()`).
4. Add a config flag `model_idle_unload_minutes: int = 0` (0 = disabled, matching current behavior) so users with abundant VRAM can opt out.
5. **VALIDATE ON CUDA HOST** — verify reload latency on a real GPU (cold reload is ~5-15s; warm page-cache reload is ~2-5s; verify the user-facing "Loading model..." tray state transitions correctly).
**Severity:** 🔴 High
**Overlaps:** None (DE-13 is post-cancel audio bytes, not model weights)

---

### TY-12 — High: Audio ring buffer sized at 4.0s — when worker falls behind (RNNoise on slow CPU), silence auto-stop latency balloons to 9.0s
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:1830-1850` dynamically sizes the ring buffer to `int(sizing_sr / blocksize * 4.0)` — 4.0s of audio. The silence timer at line 2868-2869 uses `time.perf_counter()` (worker thread's "now"), NOT the `perf_ts` captured in the callback (line 2509). When the worker falls behind (RNNoise at 50ms/chunk on a 16kHz device where chunks arrive every 32ms), the ring buffer fills to 4s and stays there (worker throughput 20 chunks/s vs callback 31 chunks/s → backlog grows 11 chunks/s → ring fills in ~11s). Under this steady-state overload: steady-state silence auto-stop latency = silence_threshold (5s) + ring_backlog (4s) = **9.0s** from when user stopped speaking. Steady-state (no overload) latency = 5s + 1-2 chunks (32-64ms) = 5.03-5.06s. The 4s ring buffer was sized to "absorb VAD inference latency spikes" per the comment at line 1826, but Silero VAD is 1-5ms against a 32ms budget — the 4s headroom is ~1000× the actual spike absorption needed.
**Root Cause:** Verified — ring buffer capacity sized for worst-case VAD spike (overkill) without accounting for the silence-detection latency trade-off. Silence timer anchored to worker time, not chunk arrival time.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:1820-1850, 2505-2512, 2866-2896`
**Fix:**
1. Reduce ring buffer to ~1s headroom (`int(sr/blocksize * 1.0)` → 32 chunks at 16kHz, 94 at 48kHz). Limits worst-case silence latency to ~6s while still absorbing 1s spikes (GC pauses, etc.).
2. Better: move RMS-based silence detection into the callback itself (RMS via `np.dot` is ~1µs, RT-safe). The callback already copies `indata`; adding `rms = float(np.sqrt(np.dot(flat, flat) / flat.size))` and a lightweight silence timer keeps the callback under ~10µs while eliminating ring-buffer-backlog latency for silence detection. VAD-based silence detection can remain on the worker for accuracy; the callback-based timer serves as a low-latency fallback.
3. Either remove the dead `perf_ts` parameter (see TY-29) or actually use it for the silence-timer VALUE.
**Severity:** 🔴 High
**Overlaps:** None

---

### TY-14 — Medium: `kill_process_tree` runs 200ms unconditional sleep even when no descendants exist — on Tauri event-loop thread
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/state.rs:225-301` — the Unix branch of `kill_process_tree` runs `std::thread::sleep(Duration::from_millis(200))` UNCONDITIONALLY between the SIGTERM loop (lines 259-278) and the SIGKILL loop (lines 282-301), even when `all_descendants` is empty. The function does NOT short-circuit on `all_descendants.is_empty()`. `kill_tree` (state.rs:158-169) wraps this in `spawn_blocking` and is called from `shutdown_sidecar_for_exit` (state.rs:488), which is invoked on the `RunEvent::Exit` critical path via `tauri::async_runtime::block_on` (main.rs:490) — a thread that blocks the entire Tauri event loop. The 200ms is pure waste when the sidecar has no grandchildren.
**Root Cause:** Verified — `if all_descendants.is_empty() { return; }` short-circuit is absent. The 200ms is also a hardcoded constant rather than a polling loop on `waitpid(WNOHANG)`.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs:225-301, 488`
- `src-tauri/src/main.rs:476-496`**Fix:**
1. Short-circuit: `if all_descendants.is_empty() { log::debug!("[KILL-TREE] no descendants for pid {}", pid); return; }` at the top of the Unix branch.
2. Replace the fixed 200ms with a poll loop: `for _ in 0..20 { if all_reaped() { break; } std::thread::sleep(10ms); }` so the grace period ends as soon as descendants actually exit.
**Severity:** 🟡 Medium
**Overlaps:** XZ-R4-016 (covers the SIGKILL/PID-reuse race in this same function — this finding is about the unconditional sleep, which is a distinct performance issue)

---

### TY-15 — Medium: `event_bus.shutdown(wait=False)` + non-daemon worker thread = 5s `_run_with_timeout` provides false assurance
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/event_bus.py:607-619` — `event_bus.shutdown()` calls `executor.shutdown(wait=False)` — `wait=False` means the call returns immediately and does NOT block on already-running or queued tasks. But the worker thread is a NON-DAEMON (CPython `ThreadPoolExecutor` default), so it keeps the interpreter alive past the `shutdown()` call until all queued/in-flight tasks finish. `shutdown_controller.py:808-812` wraps `event_bus.shutdown` in `_run_with_timeout(..., timeout=5.0)` — but since `shutdown(wait=False)` is near-instant, the 5s wrapper bounds NOTHING. If a deferred `_deliver` task is stuck on a slow subscriber (e.g. `socket.sendall` to a dead Electron renderer), the worker thread lingers past the 5s "timeout" and the process can't exit cleanly until the subscriber unblocks or the OS force-kills it.
**Root Cause:** Verified — `wait=False` + non-daemon worker thread + `_run_with_timeout` wrapping a non-blocking call = misleading timeout bound.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/event_bus.py:607-619`
- `voice_typer/server/shutdown_controller.py:805-814`**Fix:** Change `event_bus.shutdown` to `executor.shutdown(wait=True, cancel_futures=True)` — this (a) cancels queued-but-not-started tasks (they're stale by definition on shutdown), (b) waits for the in-flight task to complete. The 5s `_run_with_timeout` wrapper then ACTUALLY bounds the wait. If the in-flight task exceeds 5s, `_run_with_timeout` returns TIMEOUT and the worker thread is leaked as a daemon (already what `_run_with_timeout` does for any timed-out cleanup). Alternative: spawn the executor's worker thread with `daemon=True` (requires subclassing `ThreadPoolExecutor`).
**Severity:** 🟡 Medium
**Overlaps:** None

---

### TY-16 — Medium: macOS A11yPulse 1Hz idle poll — same anti-pattern as ER-95 but in `startup_tasks.py` not `shutdown_controller.py`
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/startup_tasks.py:339-347` — `_pulse_loop` runs `for _ in range(60): if stop_event.wait(1.0): return` to slice a 60-second `AXIsProcessTrusted()` recheck into 1-second ticks. The 1s slicing was added in PERF-25 so that `shutdown_all()` signals via `stop_event` are picked up within ~1s. But the `stop_event` is the canonical shutdown signal (registered with ThreadRegistry). The redundant `app._shutting_down` check on every 1s tick is what keeps the thread waking every second; if the loop trusted `stop_event.wait()` with no timeout (or a much longer timeout like 60s), the thread would block in the kernel for the full 60s and only wake on `stop_event.set()` (immediate) or the timeout (60s, for the AXIsProcessTrusted() recheck). Idle cost on macOS: 60 kernel thread wakeups per minute = 1/sec for the lifetime of the app, even when no dictation is happening. On a laptop on battery, 1 wake/sec prevents some deeper C-states (typically C7→C6), increasing idle CPU power draw by ~0.1-0.5W. Over 24h that's ~2.4-12 Wh/day wasted.
**Root Cause:** Verified — the 1s slicing was added in PERF-25 so that `shutdown_all()` signals via `stop_event` are picked up within ~1s. But `stop_event.set()` from `shutdown_all()` already wakes the thread immediately.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/startup_tasks.py:322-379`
**Fix:** Replace the inner `for _ in range(60): if stop_event.wait(1.0): return` with a single `if stop_event.wait(timeout=60.0): return`. The `stop_event.set()` from `shutdown_all()` wakes the thread immediately on shutdown — the 60s timeout only governs the AXIsProcessTrusted() recheck interval, which is the actual purpose of the loop. If the defensive `app._shutting_down` check must be kept (for callers that don't go through the registry), lengthen the slice to 10-30s. **VALIDATE ON MACOS HOST** — verify `stop_event.set()` from `ThreadRegistry.shutdown_all()` reliably wakes the thread within 1s on real macOS.
**Severity:** 🟡 Medium
**Overlaps:** ER-95 (same 1-second poll pattern in `shutdown_controller.py` — different file, NOT a duplicate)

---

### TY-17 — Medium: `level_monitor._process_level_chunk` RMS/peak computation uses un-optimized numpy pattern — 32-280 KB/s wasted allocations
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/level_monitor.py:1248-1270` — current code uses `abs_flat = np.abs(flat_filtered)` (allocates 2KB intermediate), `rms = float(np.sqrt(np.mean(flat_filtered**2)))` (allocates squared array 2KB + mean + sqrt), and `raw_rms_for_quality = float(np.sqrt(np.mean(np.square(flat.astype(np.float32)))))` (triple-allocation: astype copy + square + mean). At 48kHz/512 = ~93.75 chunks/sec × 3-4 allocations × 2KB = ~24KB/sec extra GC. The AUDIO-NP optimization (use `np.dot(flat, flat)/flat.size` for RMS, use `max(flat.max(), -flat.min())` for peak) was applied to `recorder._process_audio_chunk` (lines 2740-2749, with explicit comments "AUDIO-NP: single-pass RMS using np.dot — avoids creating the intermediate abs_filtered**2 array" + "PERF-FIX-2: allocation-free peak") but NEVER ported to `level_monitor._process_level_chunk` even though the two computations are mathematically identical.
**Root Cause:** Verified — AUDIO-NP / PERF-FIX-2 optimization applied to recorder.py but never ported to level_monitor.py.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py:1248-1270`
- `voice_typer/server/recording/recorder.py:2740-2749` (reference impl)
**Fix:** Port the AUDIO-NP / PERF-FIX-2 pattern from recorder.py:
```python
flat = filtered.ravel() if filtered is not None else indata.ravel()
if flat.size:
    rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
    peak = max(float(flat.max()), -float(flat.min()))
```
And drop the `.astype(np.float32)` in L1269 (flat is already float32 from sounddevice's dtype=np.float32 InputStream at L435).
**Severity:** 🟡 Medium
**Overlaps:** M-17 (still applies), ER-14 (broader scope — RNNoise on every chunk)

---

### TY-18 — Medium: `useMicrophoneTest` polls Python backend at 10Hz via IPC instead of subscribing to a push event
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/pages/microphone/hooks/useMicrophoneTest.ts:177-206` runs `setInterval(async () => { ... await call("microphone_test_get_level"); ... setLevel(...); setPeak(...); setMicMonitoring(...); }, 100)`. Polls the Python backend via IPC at 10 Hz for a 3-key dict {level, peak, active}. Per-poll cost: full IPC round-trip = JSON serialize args → WS frame → Python `get_level()` handler (acquires `_monitor_lock`, returns dict) → JSON serialize response → WS frame → Rust/Electron dispatch → renderer Promise resolve → 3 setState calls → React re-render of Microphone page subtree. Each round-trip ≈ 1-2 ms on Tauri, 2-4 ms on Electron. At 10 Hz = 10-40 ms/sec of CPU across renderer+host+sidecar just for the level bar. The backend already has a push-based `bubble_level` pipeline at 30 Hz — the Microphone page ignores this stream and polls instead.
**Root Cause:** Verified — `microphone_test_get_level` IPC handler exists for backwards compat; the renderer was never migrated to subscribe to a `mic_level` push event.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/microphone/hooks/useMicrophoneTest.ts:167-219`
- `voice_typer/server/level_monitor.py` (publish path)
- `voice_typer/server/waveform_bubble_wiring.py:132-171` (reference push impl)
- `src-tauri/src/sidecar/ws.rs` (ALLOWED_EVENT_TYPES)
**Fix:**
1. Add a `mic_level` event to ALLOWED_EVENT_TYPES in ws.rs.
2. Have `level_monitor._process_level_chunk` publish `{type: "mic_level", data: {level, peak, active}}` via the same bounded-queue + worker pattern as `_push_bubble_level` (at ≤30 Hz coalesced).
3. Replace the `setInterval(100)` in `useMicrophoneTest` with `usePythonEvent("mic_level", ...)`.
4. Keep the 100ms poll as a one-shot fallback for the first read after `level_monitor_start`.
**Severity:** 🟡 Medium
**Overlaps:** TY-3 (both are frontend polling vs push), TY-19 (ActiveMicrophoneCard re-renders driven by this poll)

---

### TY-19 — Medium: `ActiveMicrophoneCard` (277 LOC, 25+ props) has no `React.memo` — re-renders 10×/sec driven by mic level poll
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/pages/microphone/components/ActiveMicrophoneCard.tsx:66-277` — `ActiveMicrophoneCard` is a 277-LOC presentational component with 25+ props including `level` and `peak`. When `useMicrophoneTest` polls at 10 Hz (TY-18), it calls `setLevel` + `setPeak` + `setMicMonitoring` on the Microphone page, which re-renders `ActiveMicrophoneCard` and ALL of its children: `LevelBar`, `LiveQualityFeedback`, `AudioPresetSelector`, `TestReviewPanel`, 4 `Button`s, `RangeSlider`, etc. Per-render cost: React reconciliation across ~30 components + i18n lookups + JSX allocation ≈ 1-2 ms. Frequency: 10 Hz while mic monitoring is active = 10-20 ms/sec of renderer main-thread CPU. Only `LevelBar` and `LiveQualityFeedback` actually need to re-render on level change.
**Root Cause:** Verified — no `React.memo` wrapping on `ActiveMicrophoneCard` or its children; no splitting between "level-driven" and "config-driven" subtrees.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/microphone/components/ActiveMicrophoneCard.tsx:66-277`
**Fix:**
1. Wrap `AudioPresetSelector` and `TestReviewPanel` in `React.memo` with a custom comparator on `config` + `onConfigChange`.
2. Move the level + peak state OUT of the Microphone page and into a dedicated `<LevelBarContainer>` that subscribes to the `mic_level` push event (TY-18's proposed fix) — so level updates re-render only `LevelBar` + `LiveQualityFeedback`, not the whole card.
**Severity:** 🟡 Medium
**Overlaps:** TY-18

---

### TY-20 — Medium: Dashboard "Total Dictations" stat caps at 200 forever — no `get_history_count` IPC endpoint exists
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:264` fetches 200-row sample: `call<HistoryRecord[]>("get_history", { limit: 200 })`. Line 287 treats the sample length as the all-time total: `totalCount: recs.length`. `grep` across `voice_typer/server/` for `get_history_count|get_total_count|history_count|all_time_stats|get_all_count` returns NO matches — there is no server endpoint that returns the all-time history count. At N>200 history entries, the Dashboard's "Total Dictations" stat caps at 200 forever. The "activeDays" / "currentStreak" / "maxStreak" computations (lines 270-271) are also based on the 200-row sample, so they're wrong for power users with longer history.
**Root Cause:** Verified — no `get_history_count` IPC endpoint exists; the Dashboard uses the bounded sample length as a proxy.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:264, 287, 469, 549`
- `voice_typer/server/history_db.py:2349` (get_today_stats)
- `voice_typer/server/handlers/history_handlers.py`
**Fix:**
1. Add `get_history_count` IPC handler that runs `SELECT COUNT(*) FROM transcriptions` (O(N) but ~5ms at 100k rows on SQLite; cached with the same TTL pattern as `get_model_status` at `service/model.py:101-109`).
2. Update Dashboard to call `get_history_count` for the total stat.
3. Keep `get_history(limit: 200)` for the daily-activity / streak computation (where the sample is acceptable for "last 200 dictations" visualization).
**Severity:** 🟡 Medium
**Overlaps:** TY-8 (history pagination — same area)

---

### TY-22 — Medium: PortAudio device IDs not stable across hot-swap on Windows MME — `_handle_device_disconnect` falls back to OS default, ignoring user's configured mic
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/device_manager.py:325-339` — `_resolve_device` does `return int(mic)` — `config.microphone` is stored as a string PortAudio device INDEX (e.g. "5"). On Windows MME, PortAudio renumbers device indices when a USB mic is unplugged or plugged: a mic that was index 5 may become index 4 after another device is unplugged. The next `sd.InputStream(device=5)` then opens a DIFFERENT physical device (or raises `PortAudioError` if index 5 no longer exists). Additionally, `_handle_device_disconnect` line 916 hardcodes `device=None` for the restart — the recorder always falls back to the OS default device, NOT the user's configured mic. If the user had explicitly selected a non-default mic (e.g. a USB headset) and it disconnects momentarily (BT reconnection), the recorder silently switches to the laptop built-in mic.
**Root Cause:** Verified — PortAudio device IDs are not stable across hot-swap on Windows MME. The recovery path ignores the user's configured device identity and uses the OS default.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/device_manager.py:325-339, 353-391`
- `voice_typer/server/recording/recorder.py:916, 1665, 1736`
**Fix:**
1. Store a stable device identifier in config — at minimum `{"index": int, "name": str, "hostapi": str}` instead of just the index string.
2. On `start()`/`_handle_device_disconnect`, use `_same_physical_microphone_candidates` to find the current PortAudio index that matches the stored name.
3. In `_handle_device_disconnect`, instead of hardcoding `device=None`, try `_same_physical_microphone_candidates(self._resolve_device())` first, and only fall back to `device=None` if no same-named device is found.
4. Persist the chosen mic name (not index) across restarts so the user's selection survives OS-level device renumbering.
**Severity:** 🟡 Medium
**Overlaps:** None

---

### TY-23 — Medium: Five thread spawns in the device-disconnect path are unregistered with `thread_registry` — risk of half-written config on shutdown
**Status:** ❌ Not Fixed
**Description:** Five `threading.Thread(...).start()` sites in the device-disconnect path are unregistered with `self._thread_registry`:
- `_stream_finished_callback` (recorder.py:802-807): `name="stream-finished-handler"`
- `_process_audio_chunk` zero-fill detector (recorder.py:2594-2599): `name="device-disconnect-handler"`
- `_device_health_checker_loop` one-shot spawn (device_manager.py:313-319): `name="device-disconnect-check"`
- `_prewarm_device_cache` (recorder.py:1310): `name="recorder-device-cache-prewarm"`
- `_persist_mic` (recorder.py:1893): `name="mic-fallback-save"`

The 3 `register()` call sites in recorder.py (lines 589, 2092, 2217) cover scipy-preloader, audio-worker, and event-worker only. During disconnect flapping (BT mic reconnecting repeatedly, or a failing USB hub), the zero-fill detector can spawn a new `device-disconnect-handler` thread on every zero-filled callback. During shutdown (`VoiceTyperApp.quit_app()` → `shutdown_all()`), these threads are not joined. The prewarm and mic-fallback-save threads may be mid-`sd.query_devices()` (50-200ms) or mid-`config.save()` (50-500ms disk write) when the process exits — risking a half-written config file.
**Root Cause:** Verified — five thread-spawn sites in the device-disconnect path are unregistered. M-23 / AC-19 / R9-LOW prior findings still unapplied.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:802, 1310, 1893, 2594`
- `voice_typer/server/recording/device_manager.py:313-319`
**Fix:**
1. Replace all five `threading.Thread(...).start()` sites with a helper `_spawn_device_thread(name, target, kwargs)` that calls `self._thread_registry.register(...)` when `_thread_registry` is non-None.
2. For the disconnect-handler spawns, also add a single-flight guard (e.g. a `threading.Lock` + `is_running` flag) so a second spawn while the first is still running is a no-op.
**Severity:** 🟡 Medium
**Overlaps:** AC-19 / M-23 / R9-LOW (still unapplied)

---

### TY-24 — Medium: `ipc_server._send` re-allocates `_shutdown_allowlist` tuple + does per-write `settimeout`/`restore` dance on every push event
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/ipc_server.py:2229-2235` — `_shutdown_allowlist = ("relaunch_app", "quit_app", "transcription_final", "transcription_partial", "vocabulary_suggestion")` is re-constructed as a tuple on EVERY `_send()` call when `tcp_client is not None`. At 15-50 Hz waveform-bubble push events, that's 15-50 tuple allocations/sec. Line 2218: `getattr(self.app, "_shutting_down", False) is True` runs on every `_send()` call — `getattr` with a default is ~2× slower than a direct attribute access. Lines 2278-2328: the entire `with self._tcp_write_lock:` block does `_prev_timeout = tcp_client.conn.gettimeout()` → `settimeout(_TCP_WRITE_TIMEOUT_SECONDS)` → `write` → `flush` → `settimeout(_prev_timeout)` on EVERY push event — 4 syscalls per write × 15-50 writes/sec = 60-200 syscalls/sec.
**Root Cause:** Verified — each `_send()` fix (NEW-CONC-003 write timeout, QUIT-CLEAN-001 shutdown suppress, PR-2-FIX-2 allowlist expansion) was added defensively inside the hot path without hoisting invariants out.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py:2218, 2229-2235, 2278-2328`
**Fix:**
1. Hoist `_shutdown_allowlist` to a module-level `frozenset` constant `_SHUTDOWN_ALLOWLIST` near `_READONLY_COMMANDS` (L129).
2. Cache `_shutting_down` on the IPCServer instance: refresh in `start()` (set to `False`) and in `stop()` (set to `True`). Replace `getattr(self.app, "_shutting_down", False) is True` with `self._cached_shutting_down`.
3. Set `_TCP_WRITE_TIMEOUT_SECONDS` once in `_handle_tcp_connection` after auth (combine with the existing `conn.settimeout(None)` at L1059-L1060). If a per-direction timeout is needed (read blocks, write times out), use `socket.setblocking(True)` + `select.select([conn], [], [], _TCP_WRITE_TIMEOUT_SECONDS)` before each write.
**Severity:** 🟡 Medium
**Overlaps:** None

---

### TY-25 — Medium: Tray pending states/notifications lists grow unbounded on tray-unavailable systems (Wayland without SNI, VOICE_TYPER_NO_TRAY=1)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray.py:600-614` — when `_tray_unavailable=True and _icon is None`, the `run()` method does a ONE-SHOT clear of `_pending_states` and `_pending_notifications`, then blocks on `_run_event.wait()`. After the initial clear, the main thread blocks. Meanwhile, every `set_state()` (line 235-237), `notify()` (line 684-686), and `notify_safety()` (line 908-910) call appends to the module-level lists because `self._icon is None`. These lists are NEVER drained again until `stop()` sets `_run_event` (line 654). The drain paths at lines 623/628 only run on the pystray-loop branch (which is skipped when `_tray_unavailable`). Growth rate: ~4-6 state changes per dictation cycle × ~150 bytes/entry = ~750-900 bytes/cycle. A user dictating 50×/hour for 24h = ~600KB.
**Root Cause:** Verified — the unavailable-path's clear-once-then-block design forgot that subscribers keep appending during the block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py:235-237, 600-614, 684-686, 908-910`
**Fix:** In the tray-unavailable branch, replace the one-shot clear + indefinite wait with a periodic drain loop (e.g., `while not self._run_event.wait(timeout=60): self._drain_pending()`) that drains the queues every 60s and drops them (since there's no icon to flush to). Alternatively, in `set_state`/`notify`/`notify_safety`, skip the append when `_tray_unavailable` is True (the state is already published to Tauri via `_publish_tray_state`, so the pystray queue is redundant on the unavailable path).
**Severity:** 🟡 Medium
**Overlaps:** None

---

### TY-26 — Medium: `vad.py:251, 263` `.float()` always clones data — should be `.to(torch.float32)` (no-op when already float32)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/vad.py:251` (`audio_tensor = torch.from_numpy(audio_chunk).float()`) and line 263 (`audio_tensor = torch.from_numpy(padded).float()`). When `audio_chunk` (or `padded`) is already float32 — which is always the case in production because `recorder._process_audio_chunk` passes `vad_audio` from `filtered.ravel()` (float32) or from `resample_poly(...).astype(np.float32)` — `.float()` returns a NEW tensor with a CLONED data buffer (~2KB copy per chunk). Per chunk cost: ~2KB tensor allocation + memcpy = ~5-20µs. Frequency: 16 Hz. Total: ~80-320µs/sec wasted + ~32KB/sec extra allocation.
**Root Cause:** Verified — `.float()` is unconditional. `torch.Tensor.to(torch.float32)` is a no-op (returns the same tensor) when dtype already matches; `.float()` is not.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vad.py:251, 263`
**Fix:** Replace `.float()` with `.to(torch.float32)`:
```python
audio_tensor = torch.from_numpy(audio_chunk).to(torch.float32)
```
Or guard explicitly: `if audio_tensor.dtype != torch.float32: audio_tensor = audio_tensor.float()`.
**Severity:** 🟡 Medium (Low compute cost but easy win)
**Overlaps:** ER-13 (VAD sequential torch inference — broader scope)

---

### TY-28 — Low: `recorder.py:2639` xrun threshold `==` instead of `%` — callback fires EXACTLY ONCE per session
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:2639` — `if self._xruns == self._xrun_threshold and self.on_xrun_threshold:` uses `==`, so the callback fires EXACTLY ONCE per session — when `_xruns` increments from 9 to 10. After that, `_xruns` is 11, 12, ... and `== 10` is never True again. A user with 100+ xruns in a session sees 1 UI notification (at xrun #10), then nothing — they may believe the xrun issue resolved when it actually worsened.
**Root Cause:** Verified — `==` instead of `%` or rolling-window check.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:2639`
**Fix:** Change to `if self._xruns % self._xrun_threshold == 0 and self.on_xrun_threshold:` (fires every 10 xruns), OR use the already-computed `recent_count >= _XRUN_ALERT_THRESHOLD` (line 2629) to trigger the callback on a rolling-window basis.
**Severity:** 🟢 Low
**Overlaps:** None

---

### TY-34 — Low: `src-tauri/src/platform/logging.rs` does 4 syscalls per log line — should use BufWriter + in-memory byte counter
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/platform/logging.rs:271-275`:
```rust
file.write_all(line.as_bytes())?;   // syscall #2 (file write — no BufWriter)
file.write_all(b"\n")?;             // syscall #3 (file write — separate newline write)
file.flush()?;                      // no-op for std::fs::File (doesn't fsync)
let len = file.metadata()?.len();   // syscall #4 (stat() to check rotation)
if len > ROTATE_MAX_BYTES { ... self.rotate()?; }
```
Plus `eprintln!("{}", line)` (line 131) is unconditional — wasted syscall when stderr is /dev/null. ~4 syscalls per Rust host log line vs the optimal 1.
**Root Cause:** Verified — no BufWriter wrapping the File; rotation-check via stat() instead of in-memory byte counter; eprintln! unconditional.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs:109-146, 271-275, 131`
**Fix:**
1. Wrap the File in `BufWriter<File>` (8 KB buffer) and call `buf.flush()` only on rotation-check threshold crossings or every N lines.
2. Track `current_size: AtomicU64` on `RotatingFileWriter`; increment by `line.len() + 1` after each successful write; rotate when `current_size > ROTATE_MAX_BYTES`. Eliminates the per-line stat().
3. Gate `eprintln!` on a `verbose: bool` field set from `cfg!(debug_assertions)` or a `RUST_LOG_STDERR=1` env var; in release builds, write only to the file.
4. Combine the two `write_all` calls into one: `let mut buf = line.as_bytes().to_vec(); buf.push(b'\n'); file.write_all(&buf)?;` — or rely on BufWriter to coalesce.
**Severity:** 🟢 Low
**Overlaps:** None

---

### TY-35 — Low: `send-to-python.ts:40` outer `Map<number, number[]>` keyed by `webContents.id` leaks entries for destroyed windows
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/main/python/send-to-python.ts:40`:
```typescript
const _rendererCallTimestamps: Map<number, number[]> = new Map();
```
Inner array (timestamps) self-prunes via sliding-window filter, BUT the outer Map is NEVER pruned of entries for destroyed windows. Each new BrowserWindow gets a fresh monotonic `webContents.id`; when the window is destroyed, its entry stays in the Map forever. The exported reset function `_resetIpcBackpressureForTests` has a docstring claiming production callers from `stopPython` / `relaunchApp`, but grep shows NO call sites in production code (only in tests). The claim is false.
**Root Cause:** Verified — outer Map lacks eviction; the reset function exists but is only wired to tests.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/send-to-python.ts:40, 48-58, 65-67`
**Fix:** Either (a) call `_resetIpcBackpressureForTests()` (rename to `_resetIpcBackpressure`) from `stopPython()` / `relaunchApp()` to honor the existing docstring, or (b) listen for `webContents.on('destroyed')` in the python-call handler and delete the Map entry for that senderId, or (c) add a periodic sweep that deletes entries whose inner array is empty.
**Severity:** 🟢 Low
**Overlaps:** None

---

### TY-37 — Low: `AudioFilterChain.tsx:218-337` ~80 `t()` calls per render with no `useMemo` — wasted 0.5-1 ms per Settings interaction
**Status:** ❌ Not Fixed
**Description:** `AudioFilterChain` component (860 LOC, ~20 SettingRow children) calls `t("settings.audioEnhancement.*Label")` + `t("settings.audioEnhancement.*InfoSearch")` at the top of the function body (L241-336, ~40 calls) + `t("...Info")` + `t("...Aria")` inline per SettingRow (~40 more calls) on every render. None are wrapped in `useMemo`. The `set` callback (L232-235) is not `useCallback`'d either. Per-render cost: ~80 i18n dictionary lookups + string allocations ≈ 0.5-1 ms. Frequency: 1-5 interactions/sec during active Settings use = 0.5-5 ms/sec.
**Root Cause:** Verified — labels are stable for a given locale; only need re-resolution when locale changes.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx:218-337`
**Fix:** Wrap all label constants in `useMemo` keyed on `[locale]` (or hoist to module-level cache keyed on locale, since the labels are static for a given locale). Wrap `set` in `useCallback` keyed on `[onConfigChange]`. Or split the component into a `<SettingRow>` per filter (each memoized) so a change to one filter doesn't re-render the labels of the other 19.
**Severity:** 🟢 Low
**Overlaps:** None

---

### TY-38 — Low: `recorder.py:2828` per-chunk VAD property triple-lookup + per-chunk `math.gcd` recomputation
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py:2828` — `if self._vad_enabled and self._use_silero_vad and self._silero_available:` runs on every audio chunk (16 Hz). Each of the three is a `property` that delegates to `self._vad.<attr>` via the `_make_vad_property` factory — i.e. 3 `getattr` calls + 3 `property.descriptor.__get__` dispatches per chunk × 16 Hz = 48 attribute lookups/sec. The values only change on `on_config_changed()`. Lines 2839-2842: when Silero VAD is active at a non-16 kHz native rate, `math.gcd(self._effective_sr, 16000)`, `up`, `down` are recomputed on EVERY chunk. The result is invariant within a session. Line 2556: `np.count_nonzero(indata) == 0` scans all 512 samples per chunk for disconnect detection.
**Root Cause:** Verified — per-chunk methods grew organically; each fix added its own per-chunk check without coordinating with the existing ones.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:2556, 2828, 2839-2842`
**Fix:**
1. In `start()` (after `_cached_target_sr = self.config.sample_rate`), cache `_cached_use_silero_vad`, `_cached_silero_available`, `_cached_vad_enabled`. Replace L2828 with the cached values. Refresh in `on_config_changed()`.
2. In `start()` (after `effective_sr` is finalized), cache `_cached_vad_resample_up_down = (up, down)` (or `None` if `effective_sr in (8000, 16000)`). Replace L2839-L2842 with a tuple unpack.
3. Move the zero-fill disconnect check to AFTER the RMS computation (L2744): replace `np.count_nonzero(indata) == 0` with `chunk_rms == 0.0` (which is already computed from `np.dot(flat, flat) / flat.size`).
**Severity:** 🟢 Low
**Overlaps:** TY-6 (caching the resample ratio is shared)

---

### TY-39 — Spaghetti / monolith detection: `crash_handler.py` 1255 LOC mixing 6 distinct concerns — AC-86 split plan never landed
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/crash_handler.py` (1255 LOC) bundles 6 distinct responsibilities, all interleaved: (a) Win32 VEH ctypes structures, (b) kernel32 function-pointer resolution, (c) byte-buffer writer primitives, (d) VEH callback impl (148 LOC of hand-rolled hex formatting), (e) kernel32 file I/O, (f) static crash header builder, (g) diagnostics archive management (164 LOC), (h) install/remove VEH, (i) Python excepthook (124 LOC). Linux import pulls in `ctypes.wintypes` references that only work because of the `sys.platform == "win32"` guard. File has GROWN from 1014 (AC-86 filing) to 1255 LOC (+241 LOC) since the finding was opened.
**Root Cause:** AC-86 documented this exactly: "accretion. Each new diagnostic feature was appended to the file rather than carved out." GT-4 (redacted traceback), GT-7 (static crash header), G4-M-32/33/34 (archive + retention + python_crash marker) were all bolted onto the same file.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_handler.py:1-1255`**Fix:** Execute the AC-86 split plan — convert `crash_handler.py` to a `crash_handler/` sub-package with:
- `_constants.py` (status codes, _CRASH_CODES, GENERIC_WRITE/FILE_SHARE_*/OPEN_ALWAYS constants)
- `_win32_structs.py` (_ExceptionRecord/_ExceptionPointers/_SYSTEMTIME)
- `_veh_kernel32.py` (_ensure_kernel32 + argtypes/restype setup)
- `_veh_callback.py` (_write_u32_hex/_write_u64_hex/_write_timestamp/_vectored_handler_impl/_write_to_file + _crash_msg_buf)
- `_diagnostics_archive.py` (_compute_crash_header/set_crash_handler_config_dir/_archive_crash_file/_enforce_archive_retention/_sweep_stale_diagnostics/report_pending_crash)
- `_python_excepthook.py` (_format_redacted_traceback/_get_active_asr_backend/_crash_excepthook/install_python_excepthook/install_crash_handler/remove_crash_handler)
- `__init__.py` is a facade (~40 lines of re-exports) preserving every public + test-referenced private name. All function signatures unchanged. Per-platform guard stays at module-load time inside `_veh_kernel32.py` so Linux imports remain cheap.
**Severity:** 🔴 High (file grew 241 LOC since AC-86; deferred too long)
**Overlaps:** AC-86 (still ❌ Not Fixed), AC-88, AC-89, AC-90

---

### TY-40 — Spaghetti / monolith detection: `tray.py` 1270 LOC mixing 5 concerns — EC-27 split plan partially landed (i18n only)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray.py` (1270 LOC) mixes 5 concerns inside the `TrayIcon` class: (a) pystray Icon lifecycle, (b) state queuing + apply, (c) Wayland SNI detection (68 LOC inline D-Bus probe via `dbus.SessionBus`), (d) elapsed-recording timer (daemon Timer rescheduling loop), (e) parakeet CPU fallback subscription, (f) Tauri tray-menu publish. Stale docstring at :12-13 claims "This module is ~670 lines" — file is 1270 LOC (1.9× the documented size). EC-27 called out the same 3 concerns (i18n + elapsed timer + Wayland SNI); only i18n was extracted (TRAY-008 to `tray_i18n.py`).
**Root Cause:** EC-27 documented: "Packs i18n localization, elapsed timer, Wayland SNI detection + TrayIcon class." TRAY-008 fixed the i18n extraction; the elapsed timer + Wayland SNI extraction was deferred.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py:1-1270`**Fix:** Execute the remaining EC-27 split plan:
1. Extract `tray_wayland_detect.py` (the `_is_linux_wayland_without_sni` static method + the `dbus.SessionBus` NameHasOwner probe — :306-374).
2. Extract `tray_elapsed_timer.py` (the `_format_elapsed`, `_start_elapsed_timer`, `_cancel_elapsed_timer` trio — :793-864 — refactored into a small `ElapsedTimer` helper class that the TrayIcon composes).
3. Update the stale docstring at :12-13.
4. Re-export `is_linux_wayland_without_sni` and `ElapsedTimer` from `tray.py` for test backward-compat.
**Severity:** 🟡 Medium
**Overlaps:** EC-27 (partially landed)

---

### TY-41 — Spaghetti / monolith detection: `recorder.py` 3286 LOC + `ipc_server.py` 2808 LOC — too large to safely split in this session
**Status:** ⚠️ Partial (deferred — too risky to land in this session without dedicated ADR)
**Description:** `voice_typer/server/recording/recorder.py` (3286 LOC, grew 294 LOC since S2-CR-3/S3-CR-17 were filed) and `voice_typer/server/ipc_server.py` (2808 LOC, grew 200 LOC since S3-CR-19). Both exceed the project's Rule-19/20 entry-file ≤~300 LOC target by 9-11×. Concrete split plans exist (see TY-REVIEW-9 in worklog). However, both files have active test-patch backward-compat shims (`recording/__init__.py` 446 LOC `_RecordingModule` custom module class for S1-CR-67; `ipc_server.py:275-277` `sys.modules[__main__]` registration for ARCH-10) that make a safe split require migrating 30-50 test files per package.
**Root Cause:** Phase 4.5 splits extracted leaf helpers but stopped before extracting the *behavioral* methods. Subsequent fixes landed inline because there was no natural home for them.
**Progress:** Deferred — needs a dedicated ADR + multi-session refactor (CR-67 test migration first, then split). Will document in Remaining Work.
**Related Files:**
- `voice_typer/server/recording/recorder.py` (3286 LOC)
- `voice_typer/server/ipc_server.py` (2808 LOC)
- `voice_typer/server/recording/__init__.py` (446 LOC S1-CR-67 shim)**Fix:** Deferred. Concrete plans recorded in worklog (TY-REVIEW-9 return). Pre-requisite: CR-67 test migration to submodule-qualified patch paths. Estimated scope: 30-50 test files per package.
**Severity:** 🔴 High (but deferred — see Remaining Work)
**Overlaps:** S2-CR-3, S3-CR-17, S3-CR-19, S1-CR-67, ARCH-10

---

### NH-34 — `docs/API.md` is a Python class API reference, not the IPC reference the tree comment claims
**Status:** ⚠️ Partial — docs/python-api.md and ipc-reference.md created but docs/API.md still exists with same content
**Description:** `CONTRIBUTING.md:333` declares `docs/API.md` as "IPC message reference", but `docs/API.md` (218 lines) is actually a Python class API reference (`VoiceTyperApp`, `Recorder`, etc.). A developer wanting to know "what IPC commands exist" must read three source files in parallel: `voice_typer/server/ipc_server.py:_COMMAND_REGISTRY` (lines 1820-1954), `voice_typer/client/src/main/allowed-commands.ts:ALLOWED_COMMANDS`, and `voice_typer/client/src/renderer/src/types/ipc.ts` (~500 lines covering ~30 of the 78 commands). The 78 commands and 24 events are documented inline across these files — there is no consolidated, human-readable doc.
**Root Cause:** The CONTRIBUTING.md annotation is stale. API.md was written as a Python API doc and never became the IPC reference the tree comment claims.
**Progress:** None yet.
**Related Files:**
- `docs/API.md`
- `CONTRIBUTING.md`
**Fix:** Rename `docs/API.md` to `docs/python-api.md` and create a new `docs/ipc-reference.md` auto-generated from `_COMMAND_REGISTRY` + `ALLOWED_COMMANDS` + `types/ipc.ts`. Update the CONTRIBUTING.md tree comment.
**Severity:** 🟡 Medium

---

### NH-43 — `BubbleDismissButton` is keyboard-inaccessible (bubble window is `focusable: false`)
**Status:** ⚠️ Won\'t Fix (this run — requires main-process global shortcut, deferred)
**Description:** `voice_typer/client/src/renderer/src/bubble-components.tsx:445-517, 539-568` — both `BubbleMicButton` and `BubbleDismissButton` are real `<button>` elements with `aria-label` and `title`, but the bubble BrowserWindow is created with `focusable: false`. Because the window is non-focusable, these real `<button>` elements are UNREACHABLE via Tab and cannot be activated via Enter/Space in the shipped app — effectively mouse-only. For `BubbleMicButton`, the global hotkey (Caps Lock) provides a keyboard alternative. But `BubbleDismissButton` (the '×' dismiss affordance) has NO keyboard alternative. The BG-31 comment explicitly accepts this trade-off but documents the recommended mitigation (main-process global hotkey, e.g. Ctrl+Shift+D) as a future fix.
**Root Cause:** The bubble is intentionally non-focusable to avoid stealing focus from the user's active text field. The recommended mitigation is not implemented.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/bubble-components.tsx`
- `voice_typer/client/src/main/` (main process global shortcut registration)
**Fix:** Wire a main-process global shortcut (e.g. `Ctrl+Shift+D`) that routes to the `bubble:dismiss` IPC handler. Document the shortcut in the HelpOverlay. This mirrors the BG-31 recommended solution. VALIDATE ON WINDOWS HOST + MACOS HOST: global shortcut registration behavior differs per OS.
**Severity:** 🟢 Low

---

### Findings from Session 5 (YJ — Type Safety / IPC contracts)

Session 5 contributed 66 new findings.

### YJ-4 — No MemoryHandler ring buffer attached to the VEH crash handler
**Status:** ❌ Not Fixed — deferred (complex cross-cutting change)
**Description:** The VEH callback `_vectored_handler_impl` (crash_handler.py:390-538) writes only: BOM + timestamp + "CRASH" + exception code/address/pid/tid + friendly name + the pre-computed static header. No log records are included. `log.py` defines `_FlushingStreamHandler`, `_FileFormatter`, `_JsonFormatter` but NO `logging.handlers.MemoryHandler` ring buffer is ever attached to the `voice_typer` root logger.
**Root Cause:** Verified — already documented as M-69 in prior review; remains unimplemented.
**Progress:** Deferred — implementation requires careful design for the heap-corruption case where Python calls are unsafe.
**Related Files:**
- `voice_typer/server/crash_handler.py`
- `voice_typer/server/log.py`
- `voice_typer/server/logging_setup.py`
**Fix:** Add a `MemoryHandler(capacity=200, target=None)` to the `voice_typer` root logger at INFO level, with target set to a dedicated `voice-typer-crash-buffer.log` RotatingFileHandler. In `_vectored_handler_impl`, after the crash body write, attempt a best-effort `memory_handler.flush()` wrapped in try/except. For STATUS_HEAP_CORRUPTION where Python calls are unsafe, accept that the buffer is lost (documented limitation).
**Severity:** 🔴 High

---

### YJ-7 — `ServiceProtocol` declares `mic_id`/`duration`/`filters` as `Any`
**Status:** ❌ Not Fixed
**Description:** `providers.py:315-317`: `def microphone_test_start(self, mic_id: Any = None, duration: Any = None, filters: Any = None) -> dict[str, object]: ...`. `providers.py:324`: `def level_monitor_start(self, mic_id: Any = None) -> dict[str, object]: ...`. `providers.py:349`: `def onboarding_set_microphone(self, mic_id: Any) -> dict[str, object]: ...`.
**Root Cause:** Verified — `mic_id` is `Any` because the renderer sends it as either a string (`"0"`) or an int (`0`); `duration` is `Any` because it can be `int` (seconds) or `None`; `filters` is `Any` because it's a `dict[str, object]` payload.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/providers.py`
**Fix:** Replace with concrete unions: `mic_id: str | int | None = None`, `duration: int | float | None = None`, `filters: dict[str, object] | None = None`. Update `VoiceTyperService` impl to match (already mostly does).
**Severity:** 🔴 High

---

### YJ-11 — ADR-0015 (Electron command allowlist) is stale on ~25% of entries
**Status:** ❌ Not Fixed
**Description:** `docs/adr/0015-electron-command-allowlist.md` claims: (a) Line 5: "implemented in `client/src/main/index.ts:532-627`" — but allowed-commands.ts header says the canonical declaration MOVED to `voice_typer/client/src/main/allowed-commands.ts`. (b) Lines 44-53 list `test_llm_connection`, `microphone_test_status`, `level_monitor_status`, `onboarding_get_step`, `export_diagnostics`, `check_accessibility` — all 6 were REMOVED from the actual TS allowlist by GT-32. (c) The list omits 12+ entries added later (`repaste_last`, `onboarding_check_permissions`, `onboarding_get_model_catalog`, `onboarding_request_keyboard_permission`, `onboarding_reset`, `delete_all_personal_data`, `export_gdpr_bundle`, `force_cancel_transcription`, `relaunch_ack`, `microphone_test_cancel`, `microphone_test_get_level`, `cancel_model_download`, `pause_model_download`, `resume_model_download`). (d) Line 77 references `voice_typer/client/src/types/ipc.ts` — actual path is `voice_typer/client/src/renderer/src/types/ipc.ts`.
**Root Cause:** Verified — ADR-0015 was written 2026-07-14 and never updated for R6-F10 (canonical move), GT-32 (17 removals), or subsequent additions.
**Progress:** None yet.
**Related Files:**
- `docs/adr/0015-electron-command-allowlist.md`
**Fix:** Replace the inline "Exhaustive list" section with: "See `voice_typer/client/src/main/allowed-commands.ts` for the canonical list (parity-enforced by `tests/test_security_doc_command_count.py`)". Fix the stale `index.ts:532-627` and `src/types/ipc.ts` paths.
**Severity:** 🔴 High

---

### YJ-13 — `shutdown_controller.py` is a 1280-LOC monolith (single `_do_cleanup` method is ~490 LOC)
**Status:** ❌ Not Fixed — deferred (large refactor, exceeds session budget)
**Description:** `_do_cleanup` is a single linear method with 30+ sequential try/except blocks covering: IPC server stop, WS dispatch pool drain, timer cancel + join, watchdog stop, streaming session cancel, recorder stop/discard, mic watcher shutdown, level_monitor stop, volume restore, transcription thread join, hotkey backend parallel stop, crash recovery flush, history DB flush+close, bubble worker stop, sounddevice stop, electron subprocess terminate, PID file clear, mutex handle close, devnull close, event_bus shutdown, tray stop. The class also owns signal handlers (POSIX + Win32 console), the watchdog thread, and atexit safety net. Mixes wiring (dynamic `from voice_typer.server import app as _app_module` lookups) with logic (cleanup sequence).
**Root Cause:** Verified — RW-9 god-class decomposition extracted shutdown into its own module but kept everything in one class.
**Progress:** Deferred — would require ~1-2 sessions to split safely.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`
**Fix:** Extract the parallel-cleanup orchestration into a `_CleanupPhase` dataclass + runner. Group cleanup steps into phases: (1) stop-inbound (IPC, WS pool, timers), (2) stop-capture (recorder, level_monitor, volume), (3) flush-persistence (crash_recovery, history_db), (4) stop-ipc-clients (electron, hotkeys), (5) release-locks (PID file, mutex, devnull), (6) stop-tray (last). Each phase as a separate method. Target ~300 LOC for `_do_cleanup` body.
**Severity:** 🟡 Medium

---

### YJ-15 — Tauri `VoiceTyperError` enum migration is incomplete (only 2 of ~40 commands)
**Status:** ❌ Not Fixed — deferred (large migration across 38 commands)
**Description:** `src-tauri/src/commands/errors.rs:14-16` documents: "only `bubble_show` + `bubble_signal_ready` are migrated in this session as a proof-of-concept. The remaining ~38 command sites still return `Result<T, String>`". The contract doc (line 79) states: "Rust host (`dispatch` Tauri command) — rejects the `invoke` promise on `type: "error"`, translating it to `Err("server error [<code>]: <message>")` so the renderer-side `await api.call(...)` throws before the resolved value is ever inspected. The renderer-side in-code checks are therefore unreachable dead code on the Tauri path".
**Root Cause:** Verified — incremental migration started but never completed.
**Progress:** Deferred — large mechanical migration across 38 commands.
**Related Files:**
- `src-tauri/src/commands/errors.rs`
- `src-tauri/src/commands/*.rs` (all command files)
- `docs/architecture/error-envelope-contract.md`
**Fix:** Migrate the remaining 38 Tauri commands from `Result<T, String>` to `Result<T, VoiceTyperError>`. At the WS→Rust boundary in `dispatch`, deserialize the WS error envelope into `VoiceTyperError` (mapping `code` → variant) before rejecting, instead of string-concatenating. Add a contract test asserting both transports surface the same variant for the same `code`.
**Severity:** 🟡 Medium

---

### YJ-16 — Two parallel Electron main loggers with overlapping semantics (`electron-main.log` vs `electron-runtime.log`)
**Status:** ❌ Not Fixed — deferred (large refactor across many call sites)
**Description:** `logging.ts` header explicitly states: "DUPLICATION NOTE: the two loggers overlap in functionality (both write WARN/ERROR lines to a 5 MiB-rotated file under userData). They are kept side-by-side because (a) their consumer files use disjoint APIs (message-first vs printf), (b) their file targets are different (`electron-main.log` vs `electron-runtime.log`), and (c) merging them into one would require touching every call site".
**Root Cause:** Verified — two parallel logging APIs grew independently: `logger` (G4-H-37, message-first) and `log` (PVT-G5-080, printf-style).
**Progress:** Deferred — would require touching every call site.
**Related Files:**
- `voice_typer/client/src/main/logging.ts`
**Fix:** Pick one API (recommend the message-first `logger` for structured fields) and migrate the 5 `log.*` callers. Have the surviving logger write to BOTH files during a deprecation window, then drop the second file.
**Severity:** 🟡 Medium

---

### YJ-17 — Electron log lines lack `session_id` / `component` / `correlation_id` fields
**Status:** ❌ Not Fixed — deferred (cross-process change)
**Description:** `logging.ts:274-289` (formatLine) produces `<ISO ts> [<LEVEL>] <msg> <json-args>`. There is NO `session_id` field (the Python sidecar generates an 8-char hex session_id via `log.setup_logging`), NO `component`/`source` field, NO `correlation_id` field. Prior review S2-CR-75 explicitly recommended "Propagate Python session_id into Electron log lines" — partially addressed (WARN/ERROR now persist) but session_id propagation was not done.
**Root Cause:** Verified — incomplete.
**Progress:** Deferred — requires coordinated cross-process change.
**Related Files:**
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/server/log.py`
**Fix:** When the Electron main process spawns the Python sidecar, capture the session_id from the sidecar's startup banner (or generate one Electron-side and pass it as `--session-id` to the sidecar). Add `session_id` as a top-level field in `formatLine`.
**Severity:** 🟡 Medium

---

### YJ-18 — `PIIRedactionFilter.filter` mutates `record.msg` and wipes `record.args`
**Status:** ❌ Not Fixed
**Description:** `security.py:249-271` — the filter mutates `record.msg` to the redacted string and wipes `record.args`. The original message + structured args are lost for any subsequent handler. This is fine for the current text/JSON formatters but forecloses future handlers that need the structured args (e.g. a metrics exporter, or a MemoryHandler ring buffer that re-emits to a structured backend).
**Root Cause:** Verified — design tradeoff that prevents downstream structured consumers.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/security.py`
**Fix:** Store the redacted message in `record.redacted_msg` and the redacted traceback in `record.exc_text` (already done for traceback). Have the text/JSON formatters consult `record.redacted_msg` if set, falling back to `record.getMessage()`. Leave `record.msg`/`record.args` intact so structured consumers downstream can introspect them.
**Severity:** 🟡 Medium

---

### YJ-32 — `clipboard_target_safety.py` is a 1012-LOC monolith mixing 3 platform branches
**Status:** ❌ Not Fixed — deferred (large refactor)
**Description:** Single file mixes: Win32 UIA helpers (`_get_uia_singleton:501`, `_get_uia_focused_element:555`, `_is_content_editable:575`, `_focused_window_is_credential_dialog:274`, `_is_elevated_target:168`, `_get_we_elevated:101`), Linux AT-SPI helpers (`_find_focused_atspi_accessible:829`, `_is_password_field_linux:909`), macOS helpers (`_is_password_field_macos:691`), and cross-cutting wiring (`_warn_paste_safety_once:75`, `reset_platform_unavailable_warnings:678`, `_is_password_field:313` dispatcher).
**Root Cause:** Suspected — extracted as a single 1012-LOC module before the per-platform split convention was applied to `clipboard/`.
**Progress:** Deferred — would require careful split.
**Related Files:**
- `voice_typer/server/clipboard_target_safety.py`
**Fix:** Split into `clipboard_target_safety/{__init__.py, windows.py, macos.py, linux.py, dispatcher.py}`. Preserve the `from voice_typer.server.clipboard_target_safety import _is_password_field` import path via re-exports.
**Severity:** 🟡 Medium

---

### YJ-34 — 3 Python push events are missing from TS `PythonPushEvent` union
**Status:** ❌ Not Fixed
**Description:** Python emits three push events that are NOT in the TS `PythonPushEvent` union (`ipc.ts:461-496`): (a) `{"type": "asr_backend_disabled", ...}` at `asr_registry.py:533`; (b) `{"type": "asr_last_resort_unloaded", ...}` at `asr_registry.py:332`; (c) `event_bus.publish({"type": "llm_polish_failed"})` at `dictation_pipeline.py:919`. Electron main `handleMessage` routes them via the catch-all `broadcastToMainWindow("python-event", msg)` so the events DO reach the renderer — but no renderer code can subscribe to them in a type-safe way.
**Root Cause:** Verified — Python emitters were added without a corresponding TS interface update.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts`
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/dictation_pipeline.py`**Fix:** Add `ASRBackendDisabledEvent`, `ASRLastResortUnloadedEvent`, and `LLMPolishFailedEvent` interfaces to `ipc.ts` and include them in the `PythonPushEvent` union. Add a parity test similar to `config-parity.test.ts` that grep-asserts every `event_bus.publish({"type": "..."})` literal in `voice_typer/server/` has a matching TS interface.
**Severity:** 🟡 Medium

---
### YJ-39 — 5 monolith files at the IPC/contract boundary exceed 800 LOC
**Status:** ❌ Not Fixed — deferred (large multi-file refactor)
**Description:** `src-tauri/src/commands/bubble.rs` (1176 LOC) + `voice_typer/server/ipc_server.py` (2808 LOC) + `voice_typer/server/config.py` (2131 LOC) + `voice_typer/server/config_validators.py` (1445 LOC) + `voice_typer/client/src/renderer/src/types/ipc.ts` (1032 LOC) all exceed 800 LOC and mix wiring with logic. `bubble.rs` mixes 9 `#[tauri::command]` handlers with 5 helper functions. `ipc_server.py` mixes the `IPCServer` class body, `_COMMAND_REGISTRY`, handler mixins, `main()`, plus the `sys.modules` registration hack. `config.py` + `config_validators.py` together are 3576 LOC of mixed schema definition + validation + migration logic.
**Root Cause:** Verified — incremental accretion without periodic splits.
**Progress:** Deferred — exceeds session budget.
**Related Files:**
- `src-tauri/src/commands/bubble.rs`
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
- `voice_typer/client/src/renderer/src/types/ipc.ts`
**Fix:** `bubble.rs` → split into `commands/bubble/{show.rs, position.rs, draggable.rs, resize.rs, toggle.rs}`. `ipc_server.py` → complete the planned shim conversion (S2-CR-71). `config.py` → split `Config` dataclass (schema) from `Config.load()` (migration) from `Config.save()` (IO). `ipc.ts` → split into `events.ts`, `requests.ts`, `responses.ts`.
**Severity:** 🟡 Medium

---

### YJ-46 — `handlers/_log.py` is aspirational: only 4 of 14 handler modules import from it
**Status:** ❌ Not Fixed
**Description:** `handlers/_log.py:21-30` module docstring states: "as of PVT-G5-058, 10 of 13 handler files still declare `log = logging.getLogger(...)` inline. Only `history_handlers`, `model_handlers`, `privacy_handlers`, and `_base.py` actually import from here."
**Root Cause:** Verified — aspirational consolidation incomplete.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/handlers/_log.py`
- `voice_typer/server/handlers/*.py` (10 files to migrate)
**Fix:** Mechanically migrate the remaining 10 handler modules to `from voice_typer.server.handlers._log import log`. Add a test that asserts no handler file under `voice_typer/server/handlers/` contains `logging.getLogger(` outside `_log.py`.
**Severity:** 🟢 Low

---

### YJ-50 — `level_monitor.py` `_test_auto_stop_timer` is mutated without lock in 2 of 3 sites
**Status:** ❌ Not Fixed
**Description:** `level_monitor.py:670-674` (stop_test_recording) and :951-958 (cancel_test_recording) mutate `_test_auto_stop_timer` without `_monitor_lock`. The third site (`_do_auto_stop_test` at :971-985) holds the lock.
**Root Cause:** Verified — inconsistent locking discipline.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py`
**Fix:** Move the timer cancel + clear block inside `with _monitor_lock:` in both `stop_test_recording` and `cancel_test_recording`.
**Severity:** 🟢 Low

---

### YJ-53 — 10 monolith files ≥800 LOC mixing transport/lifecycle/logic (cross-cutting)
**Status:** ❌ Not Fixed — deferred (covered by YJ-13, YJ-31, YJ-32, YJ-39 individually)
**Description:** `wc -l`: `ipc_server.py` 2808, `level_monitor.py` 1313, `dictation_pipeline.py` 1291, `shutdown_controller.py` 1280, `recording_controller.py` 1002, `crash_recovery.py` 960, `microphone_watcher.py` 881, `prewarm/process_tracker.py` 837, `event_bus.py` 811, `task_scheduler.py` 793 (borderline).
**Root Cause:** Verified — RW-9 god-class decomposition incomplete.
**Progress:** Deferred — covered by individual findings YJ-13, YJ-31, YJ-32, YJ-39.
**Related Files:**
- (see individual findings)
**Fix:** Continue the RW-9 god-class decomposition. Highest-value splits: (1) `ipc_server.py` → extract `_send` + `_pending_tcp` into `ipc/tcp_writer.py`; extract `_accept_tcp` + `_handle_tcp_connection` into `ipc/tcp_acceptor.py`. (2) `shutdown_controller.py` → extract `_do_cleanup` into a `CleanupOrchestrator` (see YJ-13). (3) `level_monitor.py` → split module globals into a `LevelMonitorSession` class.
**Severity:** 🟢 Low

---

### YJ-54 — `ipc.ts` `PythonRequest` union covers only 12 of 73 IPC commands
**Status:** ❌ Not Fixed — deferred (large type coverage expansion)
**Description:** `ipc.ts:609-621` — `PythonRequest` union covers 12 commands; 61 commands have no typed request envelope. The TS side relies on callers passing explicit type arguments: `call<VoiceTyperConfig>('get_config')`. A typo'd command name (`call('get_microhpones')`) is not caught at compile time.
**Root Cause:** Verified — `PythonRequest` was only populated for commands with structured request payloads.
**Progress:** Deferred — large type coverage expansion.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts`
**Fix:** (a) Widen `PythonRequest` to cover all 73 commands (even if most have `data?: Record<string, unknown>`). (b) Constrain `usePython.call`'s `type` param: `async call<T = unknown>(type: PythonRequest["type"] | string, data?: ...)`. (c) Long-term: codegen `PythonRequest` from the Python `_COMMAND_REGISTRY` keys.
**Severity:** 🟢 Low

---

### YJ-56 — `hotkeys/native_adapter.py` has 9 `# type: ignore` sites reaching into private attrs
**Status:** ❌ Not Fixed — deferred (requires adding public API to HotkeyBackend ABC)
**Description:** `hotkeys/native_adapter.py:154-159, 297, 355, 367, 445, 475, 480` (9 sites) — same root cause as AC-44 (`permissions.py:613` reaches into `backend._on_error_callback`). The `_NativeBackendAdapter` delegates to the native backend by writing directly to its private `_on_*_callback` attributes and `_tray` attribute.
**Root Cause:** Verified — the `HotkeyBackend` ABC doesn't expose these as public API.
**Progress:** Deferred — requires adding public methods to HotkeyBackend ABC + updating native backends.
**Related Files:**
- `voice_typer/server/hotkeys/native_adapter.py`
- `voice_typer/server/hotkeys/base.py`
- `voice_typer/server/native_hotkeys/base.py`
**Fix:** Add public methods to `HotkeyBackend` (or `SubprocessHotkeyBackend`): `set_error_callback(cb)`, `set_permanent_failure_callback(cb)`, `set_warn_callback(cb)`, `set_tray(tray)`. Update `_NativeBackendAdapter` to call the public API. Drop all 9 `# type: ignore`.
**Severity:** 🟢 Low

---

### YJ-58 — `electron_launcher.py:139 is_spawned_by_electron` is dead in production
**Status:** ❌ Not Fixed
**Description:** `electron_launcher.py:139` — `is_spawned_by_electron` predicate. `rg --no-ignore -n 'is_spawned_by_electron' .` returns 9 hits: 1 definition, 7 in `tests/test_electron_launcher.py`, 1 in `electron_launcher.py:155` (the impl reads `VOICE_TYPER_IPC_TOKEN` env). Zero production call sites.
**Root Cause:** Verified — `ipc_server.py:2700-2760` decides to call `launch_electron_frontend` based on `port is None and not ws_mode`, not by consulting `is_spawned_by_electron()`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/electron_launcher.py`
- `tests/test_electron_launcher.py`
**Fix:** Delete `is_spawned_by_electron` and the 3 tests in `test_electron_launcher.py` that only exercise it. OR wire it into `ipc_server.py`'s standalone-mode decision if there's a real use case.
**Severity:** 🟢 Low

---

### YJ-59 — `tests/test_ipc_server_shim.py` is an abandoned test file (all tests skipped)
**Status:** ❌ Not Fixed
**Description:** `tests/test_ipc_server_shim.py` module docstring (lines 25-29) states: "_SKIP_REASON = 'abandoned — CR-1/Fix-A shim extraction deferred. See review.md XS-50.'" All tests in the file are skipped. The CR-1/Fix-A direction (make `ipc_server.py` a shim re-exporting from `ipc/server.py`) was REVERSED.
**Root Cause:** Verified — abandoned refactor direction.
**Progress:** None yet.
**Related Files:**
- `tests/test_ipc_server_shim.py`
**Fix:** Delete `tests/test_ipc_server_shim.py`. The canonical direction is documented in `tests/test_dead_code_stays_removed.py::TestIpcDeadCodeStaysRemoved`.
**Severity:** 🟢 Low

---

### YJ-63 — CR-67 test-patch-compat `__init__.py` shims across 3 packages (~1300 LOC boilerplate) (reconfirmation)
**Status:** ❌ Not Fixed — reconfirmation of prior finding CR-67 / S3-CR-82
**Description:** `voice_typer/server/{prewarm,recording,server_platform}/__init__.py` (combined ~1300 LOC of shim boilerplate). Each file has an explicit `TODO (2026-07-25, CR-67 / TECH-DEBT — OPEN, awaiting migration)` header.
**Root Cause:** Verified — intentional test-patch-compatibility shim. S3-CR-82 specifically flags "9 unused stdlib re-binds in prewarm/__init__.py:116-127, 273-284" (still present).
**Progress:** None yet — reconfirmed.
**Related Files:**
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/server_platform/__init__.py`
**Fix:** Execute CR-67 migration in 3 phases (one per package): (1) Migrate `tests/**.py` `monkeypatch.setattr` calls to patch submodules directly. (2) Update submodules to do `from .<submodule> import X` at top. (3) Delete the `_pkg.X` indirection + stdlib re-binds.
**Severity:** 🟢 Low

---

### RT-6 — ASR source bugs: load_active, asr_errors, download_parakeet_weights, _validate_systemroot
**Status:** ❌ Not Fixed
**Description:** 4 ASR source bugs: load_active missing circuit-breaker+unload on exception (GPU memory leak), asr_errors missing ConsentRequiredError subclasses+IPC dispatch handler, download_parakeet_weights missing force=param+safe-default (GDPR violation), _validate_systemroot conditional reset that never fires on Linux.
**Root Cause:** Source bugs — refactors specified but never landed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/asr_errors.py`
- `voice_typer/server/asr_setup.py`
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/config.py`**Fix:** (1) Add _record_failure+unload in load_active except branch. (2) Add provider/scope attrs + HuggingFaceConsentRequiredError + CloudConsentRequiredError subclasses + IPC dispatch handler. (3) Add force= param + safe-default to download_parakeet_weights. (4) Drop is_dir() check in _validate_systemroot.
**Severity:** 🔴 High

---

### RT-8 — Tauri mig17 test failures: source-text drift, fixture issues, path drift
**Status:** ❌ Not Fixed
**Description:** 32 test failures across 4 mig17 test files: paste_text moved to paste.rs, ws.rs emit_name/supervisor.rs patterns changed, _dispatch_lock fixture missing, native binary fail-closed+stdin PIPE, XPLAT-2 review.md section missing, Rust shutdown_sidecar source-text drift, supervisor dead-code removed.
**Root Cause:** API drift — production code evolved but source-inspection tests were never updated.
**Progress:** None yet.
**Related Files:**
- `tests/tauri/mig17/test_enigo_paste_linux.py`
- `tests/tauri/mig17/test_native_key_listener_linux.py`
- `tests/tauri/mig17/test_shutdown_linux.py`
- `tests/tauri/mig17/test_toast_linux.py`**Fix:** Update file paths (paste.rs not sidecar_cmds.rs), update regexes for renamed patterns, add _dispatch_lock to fixtures, update stdin/native binary assertions, remove dead-code tests.
**Severity:** 🔴 High

---

### RT-11 — pyrefly CI audit is theatre; 163 type errors untracked; 38 likely-real type bugs
**Status:** ❌ Not Fixed
**Description:** pyrefly-baseline.json contains empty errors array. CI audit step reads committed baseline file (not live pyrefly output), so new type errors pass silently. 163 actual errors untracked. 5 confirmed real bugs: asr_registry:517 unbound-name, crash_handler:531 bytearray not assignable to bytes, ipc_server:576 None not assignable to Thread, templates_handlers:70/vocabulary_handlers:72 Cannot index into str, model_manager:374 no-matching-overload.
**Root Cause:** CI audit design flaw + type bugs never fixed.
**Progress:** None yet.
**Related Files:**
- `pyrefly-baseline.json`
- `.github/workflows/build.yml`
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/crash_handler.py`
- `voice_typer/server/handlers/templates_handlers.py`
- `voice_typer/server/handlers/vocabulary_handlers.py`
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/model_manager.py`**Fix:** Fix 5 confirmed type bugs. Fix CI audit to compare live output. Populate pyrefly-baseline.json.
**Severity:** 🔴 High

---

### RT-15 — Dependency health: transformers 4.x RCE CVEs, electron-vite beta, pystray unmaintained
**Status:** ⚠️ Partial — transformers 5.14.1, pystray 0.19.5, electron-vite 6.0.0-beta.1 (intentional) all current
**Description:** transformers 4.x has known RCE CVEs (CVE-2024-11375 via safetensors deserialization, CVE-2024-11376 via pickle.load on model weights); electron-vite is beta; pystray is unmaintained (last release 2023).
**Root cause:** transformers pinned to <5 avoids breaking API changes; electron-vite is intentional (no stable Electron v35 support); pystray has no maintained fork.
**Progress:** transformers upgraded to 5.14.1 in requirements-lock.txt; CVE surface reduced. electron-vite intentionally held at beta. pystray monitoring issue filed.
**Related Files:**
- requirements-lock.txt
- pyproject.toml:82 (pystray>=0.19,<0.20)
- voice_typer/client/package.json (electron-vite)
**Fix:** transformers no action (already mitigated). electron-vite no action (intentional).
- Monitor pystray for forks or replacements.
- Add safety-ci to GHA that runs pip-audit weekly.
**Severity:** Medium

---

### RT-16 — Testing infrastructure: dead fixtures, redundant markers, coverage baseline fictional
**Status:** ❌ Not Fixed
**Description:** coverage-baseline.json is hand-set to 65.23% (never measured). Dead fixtures (make_fake_sidecar_ws_server, make_fake_recorder, wav_fixture_path). Redundant --cov-fail-under in addopts. 35 redundant @pytest.mark.asyncio. tests/server/ empty. tests/regressions/ uses _test.py suffix. ruff_ratchet_check.py silent-pass hole.
**Root Cause:** Incomplete migrations, dead code, config redundancy.
**Progress:** None yet.
**Related Files:**
- `coverage-baseline.json`
- `tests/fixtures/app_helpers.py`
- `tests/fixtures/ipc_test_helpers.py`
- `tests/conftest.py`
- `tests/server/__init__.py`
- `pyproject.toml`
- `scripts/ruff_ratchet_check.py`**Fix:** Regenerate coverage baseline from real run. Delete dead fixtures. Remove --cov-fail-under from addopts. Fix ruff ratchet silent-pass hole. Delete tests/server/.
**Severity:** 🟡 Medium

---

### DT-21 — recorder.py (4012 lines) — Critical spaghetti monolith
**Status:** ❌ Not Fixed — recorder.py (4033 lines) NOT split; no DisconnectHandler or vad_helpers.py
**Description:** `voice_typer/server/recording/recorder.py` is 4012 lines. The single `Recorder` class (line 305) spans ~3700 lines and mixes 7 concerns: audio I/O lifecycle, PortAudio device management, device-disconnect recovery, VAD integration, audio worker + ring buffer, IPC event worker, resampling/format. `start()` is 754 lines (1825-2578). `_process_audio_chunk` is 510 lines (3133-3642). `_handle_device_disconnect` is 277 lines. `__init__` is 370 lines declaring 60+ instance attributes.
**Root Cause:** God-class grew by accretion; Phase 4.5 extracted helpers (device_manager.py, buffer.py, resampling.py) but kept `Recorder` itself monolithic.
**Impact:** Untestable in isolation; every change risks touching the audio hot path; 510-line `_process_audio_chunk` is unreviewable.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`**Fix:** Split `_process_audio_chunk` into named helper methods (each ≤80 lines): `_detect_disconnect`, `_handle_xrun`, `_apply_filter_chain`, `_append_to_buffer`, `_compute_rms_and_peak`, `_run_vad_state_machine`, `_fire_callbacks`. Extract `_handle_device_disconnect` body into a `DisconnectHandler` class. Extract VAD methods into `recording/vad_helpers.py`. Keep `Recorder` as a thin coordinator. Public API preserved via `recording/__init__.py` re-exports.
**Severity:** 🔴 Critical

### DT-24 — config.py (2270 lines) — Critical spaghetti monolith
**Status:** ❌ Not Fixed — config.py (2478 lines) NOT split into config/ package
**Description:** `voice_typer/server/config.py` is 2270 lines. `Config` dataclass (line 599) + 11 module-level helpers mixing path resolution + traversal safety, atomic save/mutation lock, schema migration v2/v3, load-time validation. `load()` is 265 lines. `_validate_non_numeric_fields` is 162 lines. The file's tail (lines 2240-2270) is a 30-entry `from .config_validators import (...)  # noqa: F401` backward-compat re-export.
**Root Cause:** config_validators.py was extracted but path safety, schema migration, and load-time validation were not.
**Impact:** `Config.load()` is the #1 cold-start path; mixing it with schema migration means a v3→v4 migration requires editing a 2270-line file.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py` (1673 lines)**Fix:** Extract `config/paths.py` (_config_dir, _validate_path_safety, _is_path_within, _validate_import_path, _validate_systemroot, _acquire_config_lock), `config/migrations.py` (_migrate_to_v2, _migrate_to_v3, _run_migrations), `config/loader.py` (Config.load orchestrator + _filter_unknown_keys). Keep `Config` dataclass + save/load entry points in config.py. Delete the 30-symbol re-export after migrating imports.
**Severity:** 🔴 Critical

### DT-28 — level_monitor.py (1524 lines) — two disjoint subsystems mixed
**Status:** ❌ Not Fixed — level_monitor.py (1586 lines) NOT converted to package; still module-level functions with 30 mutable globals
**Description:** `voice_typer/server/level_monitor.py` is 1524 lines with ZERO classes — 24 module-level functions + ~30 module-level mutable globals. Mixes TWO disjoint subsystems: (1) live mic-level monitoring for the IPC `get_level` command, (2) ad-hoc microphone test recording. `stop_test_recording` is 269 lines. `start_monitoring` is 176 lines. `_process_level_chunk` is 169 lines. Two separate worker pools (`_mic_level_worker` + `_level_worker`) share the same module namespace.
**Root Cause:** Two features share a `sounddevice.InputStream`; co-located in one module with shared module-level state.
**Impact:** Module-level mutable state makes testing fragile (~30 globals must be reset per test); 269-line `stop_test_recording` cannot be unit-tested.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py`**Fix:** Convert to `level_monitor/` package: `monitor.py` (LevelMonitor class for live monitoring), `test_recorder.py` (MicrophoneTestRecorder class), `_shared.py` (LevelStream singleton owning the InputStream). Keep `__init__.py` re-exporting `start_monitoring`/`stop_monitoring`/`start_test_recording`/`stop_test_recording` as module-level functions delegating to singletons.
**Severity:** 🔴 High

### DT-29 — permissions.py (1282 lines) — 3 platforms × 3 domains mixed
**Status:** ❌ Not Fixed — permissions.py (1282 lines) NOT split into package
**Description:** `voice_typer/server/permissions.py` is 1282 lines, ~25 module-level functions spanning keyboard permission, microphone permission, macOS Accessibility, Linux input access, native listener probe, tray notification. `probe_native_listener` is 141 lines with inline closures. `check_permissions_payload` is 109 lines.
**Root Cause:** All "permission" code landed in one file; platform-specific helpers accreted.
**Impact:** A Linux-input bug forces reading macOS Accessibility + keyboard retry code; platform-specific code can't be lazy-loaded.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/permissions.py`**Fix:** Convert to `permissions/` package: `keyboard.py`, `microphone.py`, `macos_accessibility.py`, `linux_input.py`, `native_probe.py`, `payload.py`. Re-export public API from `__init__.py`.
**Severity:** 🟡 Medium

### DT-30 — dictation_pipeline.py (1306 lines) — run() 301-line monolith
**Status:** ❌ Not Fixed — dictation_pipeline.py (1335 lines) NOT split; run() still 300+ lines
**Description:** `voice_typer/server/dictation_pipeline.py` is 1306 lines. `run()` is 301 lines (119-419) orchestrating 12 sequential text-processing steps + resource checks + error handling in ONE method. `_check_resources` is 163 lines. `_store_result` is 116 lines. `_copy_and_paste` is 156 lines.
**Root Cause:** Pipeline pattern with no step-abstraction — each step added inline.
**Impact:** Adding a new post-processing step requires editing the 301-line `run()`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py`**Fix:** Extract `dictation_pipeline/steps.py` (one function per step, pure: (text, ctx) → text). Extract `dictation_pipeline/resource_check.py` (_check_resources). `DictationPipeline.run` becomes a <80-line orchestrator.
**Severity:** 🟡 Medium

### DT-32 — ThemeSettingsSection.tsx (1258 lines) — 6 concerns mixed
**Status:** ❌ Not Fixed — ThemeSettingsSection.tsx (1258 lines) NOT split; no extracted sub-components
**Description:** `components/settings/ThemeSettingsSection.tsx` is 1258 lines mixing WCAG contrast-ratio math, localStorage draft backup, theme-color readers, 7-prop interface, 7 useRef + 6 useState + 6 useEffect state machine, and ~550 lines of JSX. The memo'd component body is 802 lines — 13× the 60-line TS threshold.
**Root Cause:** Extraction from Settings.tsx pulled the entire appearance subsystem into one file without further decomposition.
**Impact:** Editing the WCAG contrast warning requires scrolling past 280 lines of cache/LS scaffolding.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx`**Fix:** Extract `lib/theme-contrast.ts` (contrastRatio + WCAG threshold — pure functions). Extract `lib/theme-draft-storage.ts` (3 LS helpers). Extract `ColorRow.tsx` + `ThemePresetSelect.tsx` + `CustomThemeEditor.tsx` sub-components. Extract `useThemeSettings` hook. ThemeSettingsSection.tsx becomes ~250 lines.
**Severity:** 🔴 High

### DT-36 — main-window.ts (542) + bubble-window.ts (612) — mixed concerns
**Status:** ❌ Not Fixed — bubble-window.ts (612) + main-window.ts (542) NOT further split
**Description:** Both window files exceed the 500-line threshold. `bubble-window.ts` (612 lines) mixes multi-display positioning, exclusive-fullscreen detection, saved-position state, window construction, animated show/hide, crash recovery, console forwarding, moved-event persistence, AND a 22-line defensive-require logger block. `main-window.ts` (542 lines) mixes window construction, nativeTheme listener lifecycle, crash-storm tracking, renderer-error persistence, and 8 inline webContents event handlers.
**Root Cause:** Window files extracted from index.ts (REF-2) without further decomposition; crash-storm + theme + error persistence layered on.
**Impact:** Crash-storm policy changes require touching main-window.ts; `recordBubbleRenderCrash` import creates circular-ish coupling.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts`
- `voice_typer/client/src/main/windows/main-window.ts`**Fix:** Split `main-window.ts` into `windows/main-window.ts` (creation + show), `windows/theme-listener.ts`, `windows/crash-storm.ts` (shared by both), `windows/renderer-error-persistence.ts`. Split `bubble-window.ts` into `windows/bubble-window.ts` (creation + show/hide), `windows/bubble-positioning.ts`, `windows/fullscreen-detect.ts`.
**Severity:** 🟡 Medium

### DT-38 — CR-67 __init__.py indirection (3 packages, ~2000 LOC boilerplate)
**Status:** ❌ Not Fixed — _RecordingModule still indirection in recording/__init__.py; CR-67 not applied
**Description:** `recording/__init__.py` (457 lines), `prewarm/__init__.py` (334), `server_platform/__init__.py` (325) install custom `_RecordingModule`/`_pkg.X` indirection classes purely for test-patch compatibility. Each exports 24-30+ private `_`-prefixed symbols in `__all__`. The `_` prefix has been drained of meaning — it signals "test-patch target" rather than "internal". The docstrings explicitly tag this as "CR-67 / TECH-DEBT — OPEN, awaiting migration" with scope "90-150 test files total."
**Root Cause:** Package split (Phase 4.5) introduced submodules but left the test suite patching the package-level name.
**Impact:** ~2000 LOC of pure indirection; `_` prefix no longer communicates "private"; custom module subclasses break `inspect.getsource`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/server_platform/__init__.py`**Fix:** Execute CR-67 migration: update ~90-150 test monkeypatch sites to patch the submodule directly (`voice_typer.server.recording.resampling._resample_poly_error` instead of `voice_typer.server.recording._resample_poly_error`). Delete the custom module subclasses. Shrink each `__init__.py` to a single `from .submodule import PublicName` block. Drop every `_`-prefixed name from `__all__`.
**Severity:** 🟡 Medium

### DT-39 — config_validators.py (1673 lines) — _validate_language 294-line god-function
**Status:** ❌ Not Fixed — config_validators.py (1673 lines) NOT split into package
**Description:** `voice_typer/server/config_validators.py` is 1673 lines packing 4 disjoint validation subdomains: scalar validator factories (~270 lines), hotkey validation (~270 lines, 12 helpers), cross-field validation, language validation (`_validate_language` is a SINGLE 294-line function at lines 1086-1379), and top-level orchestrators.
**Root Cause:** Each new config domain grew its own helpers in the same file because `validate_config_update` is the single entrypoint.
**Impact:** Adding a new hotkey rule forces reading the 294-line language validator.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config_validators.py`**Fix:** Split into `config_validators/` package: `scalar.py`, `hotkey.py`, `language.py` (split `_validate_language` internally), `cross_field.py`, `__init__.py` (orchestrators + re-exports).
**Severity:** 🟡 Medium

### DT-40 — shutdown_controller.py (1488 lines) — _do_cleanup 235-line monolith
**Status:** ❌ Not Fixed — shutdown_controller.py (1488 lines) NOT split; _do_cleanup still 235 lines
**Description:** `voice_typer/server/shutdown_controller.py` is 1488 lines. `_do_cleanup` is 235 lines (304-539). The 13 `_teardown_X` methods are sequentially coupled through the same `app` handle. Module also has `_TimeoutSentinel`, `_run_with_timeout`, `_run_parallel_with_timeout` (155-197) — generic timeout utilities unrelated to shutdown.
**Root Cause:** Each subsystem teardown added as a `_teardown_X` method on the same controller; timeout helpers inlined.
**Impact:** Reordering teardown sequence requires editing the 235-line `_do_cleanup`; timeout utilities can't be reused.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`**Fix:** Extract `shutdown_controller/_timeout.py` (generic utilities). Extract `shutdown_controller/teardown_steps.py` (each `_teardown_X` as a standalone `async def teardown_X(app, timeout)`). `ShutdownController._do_cleanup` becomes a <80-line sequence of `await teardown_X(...)` calls.
**Severity:** 🟡 Medium

### DT-41 — ALLOWED_COMMANDS 3-layer duplication (76 entries × 3 files)
**Status:** ❌ Not Fixed — ALLOWED_COMMANDS still in 3 separate layers; no protocol/commands.json
**Description:** `ALLOWED_COMMANDS` is declared in 3 separate layers: TS (`allowed-commands.ts:70-206`, 76 entries), Rust (`sidecar_cmds.rs:133-215`, 76 entries), Python (`_COMMAND_REGISTRY` ~78 entries). Each layer hardcodes its list; parity enforced after-the-fact by `tests/test_security_doc_command_count.py` and `tests/test_electron_ipc_and_build.py`. Doc comments on both sides admit the duplication ("KEEP IN SYNC").
**Root Cause:** Pre-existing 3-layer architecture; each layer enforces its own allowlist as defense-in-depth; no shared contract file.
**Impact:** Every new command requires editing 3 files + manual coordination; 60+ renderer call sites pass names as bare string literals.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/allowed-commands.ts:70-206`
- `src-tauri/src/commands/sidecar_cmds.rs:133-215`
- `voice_typer/server/ipc_server.py:2016-2188`
**Fix:** Introduce a single source-of-truth `protocol/commands.json` at repo root. Codegen TS `ALLOWED_COMMANDS` Set + `type CommandName` union, Rust `static ALLOWED_COMMANDS: &[&str]`, Python `_COMMAND_REGISTRY` skeleton. Parity tests assert codegen output matches. (Full codegen is a larger effort; for this session, add a stricter parity test that asserts set equality.)
**Severity:** 🟡 Medium

### DT-43 — APP_NAME 4-way duplication (branding across 4 files)
**Status:** ❌ Not Fixed — APP_NAME still declared in 4 separate files; no protocol/branding.json
**Description:** `APP_NAME = "Voice Typer"` is declared in 4 files: `voice_typer/server/branding.py:31`, `voice_typer/client/src/main/branding.ts:51`, `voice_typer/client/src/renderer/src/branding.ts:52`, `src-tauri/src/branding.rs:41`. Each file's docstring explicitly documents the duplication with multi-paragraph ASCII-art warning boxes begging future agents not to inline the literal. Parity enforced by `branding-sync.test.ts` + `scripts/check_branding.py`.
**Root Cause:** TS main and renderer tsconfigs include disjoint directory trees; single shared TS module impossible. Rust mirror added when Tauri host landed.
**Impact:** A product rename requires editing 4 files in 3 languages; the warning boxes themselves are duplicated 3×.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/branding.py:31`
- `voice_typer/client/src/main/branding.ts:51`
- `voice_typer/client/src/renderer/src/branding.ts:52`
- `src-tauri/src/branding.rs:41`
**Fix:** Add `APP_NAME` (and `APP_DESCRIPTION`, `APP_URL`, `APP_REPO`) to shared `protocol/branding.json`. Codegen all 4 branding modules from it. As a transitional step, extend `check_branding.py` to also read `branding.rs` (it currently doesn't per the `branding.rs:21` comment).
**Severity:** 🟡 Medium

### DT-48 — ServiceMixinBase `Any` attributes (123 suppressed type errors)
**Status:** ❌ Not Fixed — ServiceMixinBase still uses Any attributes; no ClassVar with concrete types
**Description:** `service/_base.py:77-87` declares `_app`, `_download_cancel_lock`, etc. as `Any`. `pyrefly-baseline.json` has 123 `missing-attribute` suppressed errors, top file `service/model.py` (24). The `Any` annotation silences errors at the base level but multiple-inheritance composition means pyrefly still reports missing attributes at mixin scope.
**Root Cause:** ServiceMixinBase introduced to silence "attribute access before assignment" warnings; type safety became decorative.
**Impact:** A refactor renaming `_active_download_id` silently breaks 4 mixin references because the type system no longer enforces the contract.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/_base.py:44-87`
- `voice_typer/server/service/__init__.py:229-254`
- `voice_typer/server/service/model.py`
- `pyrefly-baseline.json` (123 missing-attribute entries)**Fix:** Declare these as `ClassVar` on ServiceMixinBase with concrete types (e.g. `_download_cancel_lock: ClassVar[threading.Lock]`) and bind in `__init__` via `self.__dict__[...]`. Remove the 123 entries from `pyrefly-baseline.json` as they're fixed.
**Severity:** 🟡 Medium

---

### FZ-8 — 280 `inspect.getsource` source-string tests across 69 Python test files (GREW from 164/30)
**Status:** ❌ Not Fixed — too large (project-wide migration; deferred to dedicated test-quality sprint)
**Description:** 280 occurrences of `inspect.getsource` across 69 Python test files (the directive cited "164+ across 30+ files" — the count has GROWN, not shrunk). Top offenders: `tests/regressions/audio_test.py` (22), `tests/test_electron_ipc_and_build.py` (13), `tests/test_recorder_worker_lifecycle.py` (12), `tests/test_platform_and_config.py` (11), `tests/test_recording_and_audio.py` (10), `tests/test_dead_code_stays_removed.py` (9). These tests assert on the literal source text of production functions rather than on observable behavior.
**Root Cause:** Bug-fix-driven tests assert on structural source text ("ensure this function still contains a try/except line") rather than behavior. Each fix added one or two `inspect.getsource(...)` + `assert "..." in src` lines, and nobody pruned them.
**Impact:** Refactoring any of the 69 production modules — renaming a variable, splitting a function, reformatting — breaks source-string tests in unrelated-looking test files. This is the single largest source of refactoring friction in the suite and the reason FZ-1 through FZ-5 are deferred.
**Progress:** None yet.
**Related Files:** 69 test files (see above)
**Fix:** Replace each `inspect.getsource(...)` + substring assertion with a behavioral test (call the function with a fixture input, assert on output/side effect). For the few cases where a structural guarantee is genuinely required (e.g. "no `eval` in this module"), use AST inspection (`ast.walk`) rather than raw source substring matching. Prioritize the top-10 offenders. Target: <30 occurrences suite-wide.
**Severity:** 🔴 Critical

---

### FZ-14 — IPC channel-name strings hardcoded as literals across 8+ TS files (no shared contract module)
**Status:** ❌ Not Fixed — too large (~50 literal sites to migrate; deferred to dedicated IPC-contract sprint)
**Description:** Every IPC channel name is a hand-typed string literal duplicated between preload (sender) and main process (receiver). 16 distinct channel prefixes, each duplicated 2-7× as inline literals. Examples: `"python-call"`, `"python-event"`, `"window:minimize"`, `"history:export"`, `"bubble:move-by"`, `"bubble:resize"`, `"bubble:draggable"` (7 non-test sites), `"bubble:hidden"` (7 non-test sites). No shared `IpcChannel` constants module exists.
**Root Cause:** Pre-existing pattern from the original monolithic `index.ts`; the REF-2 split into per-domain handler files preserved the inline literals rather than extracting a shared channel-name registry. The Tauri bridge added a third copy of each name (snake_case Rust commands).
**Impact:** A typo in any one of these literals silently breaks IPC at runtime with no compile-time error. Channel renames require manual find/replace across 2-3 sites with no safety net. The Tauri/Electron naming-convention split compounds the divergence risk.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/preload/{index.ts, _bubble-channels.ts}`
- `voice_typer/client/src/main/ipc/{bubble-handlers.ts, window-handlers.ts, export-handlers.ts, python-call-handler.ts}`
- `voice_typer/client/src/main/windows/{bubble-window.ts, main-window.ts}`
- `voice_typer/client/src/main/python/handle-message.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts`
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Introduce `src/shared/ipc-channels.ts` exporting `const IPC_CHANNEL = { PYTHON_CALL: "python-call", PYTHON_EVENT: "python-event", WINDOW_MINIMIZE: "window:minimize", BUBBLE_MOVE_BY: "bubble:move-by", ... } as const`. Replace every inline literal with the constant. Add a vitest test asserting every `ipcMain.handle/on` channel has a matching `ipcRenderer.invoke/send` constant.
**Severity:** 🔴 High

### FZ-23 — `shutdown_controller.py` (1488 LOC) is a god-module mixing 5 separable concerns
**Status:** ❌ Not Fixed — too large (~5 new files; deferred)
**Description:** Single `ShutdownController` class mixes: generic timeout helpers (115 LOC), watchdog (50 LOC), POSIX signal handling (95 LOC), Win32 console handling (90 LOC), 14 teardown step methods (520 LOC), core orchestration (300 LOC).
**Root Cause:** RW-9 god-class decomposition extracted shutdown from `VoiceTyperApp` but stopped at a single class — it should have produced 5-6 focused modules.
**Impact:** Every change to (e.g.) the Win32 console handler requires re-reading 600 LOC of unrelated teardown code. The 1488-LOC file is above most linters' maintainability thresholds.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`**Fix:** Split into `_timeout_utils.py`, `_shutdown_watchdog.py`, `_signal_handlers.py`, `_win32_console.py`, `teardown_steps.py` (or `_teardown/` package). `shutdown_controller.py` keeps `__init__`, `_do_cleanup`, `quit`, `_atexit_*` (~300 LOC) and delegates to the extracted modules.
**Severity:** 🔴 High

### FZ-24 — `ws.rs` (1187 LOC) mixes WS transport with supervisor scheduling and event-protocol concerns
**Status:** ❌ Not Fixed — too large (3 new files; deferred; FZ-9/FZ-10 address the most urgent ws.rs issues)
**Description:** Mixes supervisor-scheduler plumbing (85 LOC: `trigger_respawn_off_thread`, `respawn_supervisor_sender`, `RESPAWN_SUPERVISOR_TX`, `cleanup_and_trigger_respawn`), event-protocol tables (67 LOC: `ALLOWED_EVENT_TYPES` const + `translate_event_name`), heartbeat watchdog (110 LOC: `spawn_heartbeat_task`), WS transport proper (~600 LOC), plus tests (~220 LOC).
**Root Cause:** The original 585-line `reconnect_ws` god-function was partially split, but the split stopped at phase helpers. The supervisor-trigger plumbing and event-protocol tables were left in place.
**Impact:** Today's `ws.rs` has 5 distinct responsibilities and 1187 LOC — every WS-transport bugfix requires re-reading supervisor-scheduler code and the heartbeat state machine.
**Progress:** None yet (FZ-9 + FZ-10 address the urgent allowlist issues within this file).
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`**Fix:** Split into `respawn_scheduler.rs` (supervisor plumbing), `event_protocol.rs` (allowlist + translate), `heartbeat.rs`. `ws.rs` keeps `ws_connect`, `queue_auth_and_store_ws_tx`, `spawn_writer_task`, `wait_for_auth_ok`, `spawn_reader_task`, `reconnect_ws` (~600 LOC).
**Severity:** 🔴 High

### FZ-27 — `thiserror` declared in `Cargo.toml` but NEVER used; all 40+ Rust errors are `Result<T, String>`
**Status:** ❌ Not Fixed — too large (40+ site migration; deferred to dedicated error-handling sprint)
**Description:** `src-tauri/Cargo.toml:67` declares `thiserror = "2"` but it is never imported anywhere in `src-tauri/src/`. Zero `#[derive(... Error ...)]`, zero `impl std::error::Error`. Meanwhile every command handler + sidecar helper uses `Result<T, String>` (40+ sites confirmed by grep). Errors are constructed via `format!("...")`, `.map_err(|e| e.to_string())`, or `"...".to_string()`.
**Root Cause:** `thiserror` was added to `Cargo.toml` (presumably anticipating a proper error enum) but never actually wired up.
**Impact:** Callers cannot programmatically distinguish error variants (e.g. "sidecar not connected" vs "WS send failed" vs "dispatch timeout" vs "server error [code]"). Every consumer must do string-substring matching, which is brittle to log-message edits. Stack/source info from underlying `io::Error` / `serde_json::Error` is lost. The declared `thiserror` dep also bloats the release binary + compile time for no benefit.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml`
- All `src-tauri/src/commands/*.rs`
- `src-tauri/src/sidecar/*.rs`
- `src-tauri/src/platform/*.rs`
- `src-tauri/src/state.rs`**Fix:** Define a `HostError` enum in a new `src-tauri/src/error.rs` using `thiserror`. Add `impl Serialize for HostError` that emits the existing `{"type":"error","data":{"code":..., "message":...}}` shape (Tauri v2 supports `invoke` rejection with any serializable value). Migrate command handlers first (mechanical), then sidecar helpers.
**Severity:** 🔴 High

### FZ-28 — `16000` (Whisper sample rate) hardcoded as a literal across 30+ production sites
**Status:** ❌ Not Fixed — too large (30+ site migration across 7+ modules; deferred)
**Description:** Whisper's 16 kHz requirement is hardcoded as `16000` across 30+ sites: `config.py:803`, `transcription.py:74` (defines `_WHISPER_SAMPLE_RATE = 16000` but never imports it elsewhere), `transcription.py:590`, `level_monitor.py:120,561,563,950`, `qwen_engine.py:379,572`, `parakeet_engine.py:665,705,798,845,1122,1150`, `audio_chain_builder.py:23,129`, `audio_processor.py:139`, `microphone_test_recorder.py:300`, `service/status.py:129`, `cloud_engines.py:178`, `vad.py:216,255,292,354`, `recording/recorder.py:524,1523-1525,2185,3532,3564,3574`, all 7 `audio_filters/*.py` constructors, `server_platform/microphone_list.py:119`.
**Root Cause:** Whisper's 16 kHz requirement is universal domain knowledge that pre-dates the codebase; engineers inline `16000` rather than reaching for a shared constant. `transcription.py` even defines `_WHISPER_SAMPLE_RATE` but it is module-local and never imported elsewhere.
**Impact:** If Whisper ever accepts 8 kHz / 24 kHz, every site must be hunted down. A wrong literal in one site (e.g. parakeet's `len(audio) / 16000`) silently miscalculates duration. The `16000 / 44100 / 48000` triple in `recorder.py` and `level_monitor.py` is already a known source of bugs.
**Progress:** None yet.
**Related Files:** 30+ files (see above)**Fix:** Promote `voice_typer/server/transcription.py:_WHISPER_SAMPLE_RATE` (or a new `voice_typer/server/_audio_constants.py`) to export `WHISPER_SAMPLE_RATE = 16000`, `SILERO_VAD_SAMPLE_RATES = frozenset({8000, 16000})`, `RNNOISE_SAMPLE_RATE = 48000`, `NATIVE_MIC_RATES = frozenset({8000, 16000, 44100, 48000})`. Update all 30+ call sites and default-argument sites to import.
**Severity:** 🔴 High

### FZ-29 — IPC command allowlist (`ALLOWED_COMMANDS`) hardcoded THREE times across Rust / TS / Python (60+ entries each)
**Status:** ❌ Not Fixed — too large (requires contract-file codegen; deferred)
**Description:** Three independent language runtimes each need a static allowlist at module-load time: Rust (`src-tauri/src/commands/sidecar_cmds.rs:158-240` literal `&[&str]` array of 60+ commands), TS (`voice_typer/client/src/main/allowed-commands.ts:70-206` `Set<string>` of 60+ commands), Python (`voice_typer/server/ipc_server.py:2086-2200+` `_COMMAND_REGISTRY` dict[str, str]). There is no shared schema source — instead a parity test (`test_security_doc_command_count.py`) backstops the manual duplication. The Rust file's own comment at `sidecar_cmds.rs:153-157` admits: "all three layers now stay in sync. The parity test enforces count + exact-entry equality across all three."
**Root Cause:** Three independent language runtimes each need a static allowlist at module-load time. No shared schema source.
**Impact:** Adding a new command requires editing 3 files in lockstep. The comment trail documents at least 4 historical drift incidents (missing `quit_app`/`restart_app`, missing `onboarding_check_permissions`, stale entries that broke runtime). The parity test catches *count + equality* but not *intent* — adding the wrong command to all three passes the test.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs`
- `voice_typer/client/src/main/allowed-commands.ts`
- `voice_typer/server/ipc_server.py`**Fix:** Define the command set ONCE in a machine-readable contract file (e.g. `docs/contracts/commands.json`). Generate the Rust `&[&str]`, TS `Set<string>`, Python `_COMMAND_REGISTRY` keys via a build-time codegen step.
**Severity:** 🔴 High

### FZ-30 — `ALLOWED_EVENT_TYPES` allowlist duplicated between Rust host and Python publisher (drift already happened twice)
**Status:** ❌ Not Fixed — too large (requires shared event-catalogue + codegen; deferred; FZ-9 addresses the immediate symptom)
**Description:** The Python sidecar publishes events under snake_case names; the Rust host allowlists them defensively. There is no single contract file enumerating the valid event types — the Rust literal is the de-facto spec, and the Python `event_bus.publish` call sites are the de-facto producers. The comment at `event_bus.py:155` explicitly documents that drift has occurred twice historically. The Rust allowlist has TWO blocks (lines 74-79 "G4-H-32 spec list verbatim" and 80-108 "Additional known server-published events") — itself a two-tier duplication where the "spec" and "actual" lists disagree.
**Root Cause:** No shared contract artifact.
**Impact:** When Python adds a new event, the Rust allowlist must be updated in lockstep or the event is silently dropped at the WS reader. FZ-9 is a concrete instance of this drift (8 missing event types).
**Progress:** FZ-9 fixes the immediate symptom (added the 8 missing types). Long-term contract-file solution deferred.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
- `voice_typer/server/event_bus.py`**Fix:** Define the event-type registry ONCE in Python as `KNOWN_EVENT_TYPES: frozenset[str]`. Have the Python sidecar emit this list at WS handshake. The Rust host builds its `ALLOWED_EVENT_TYPES` from the handshake response + a static "extra-safe" superset. Add a parity test.
**Severity:** 🔴 High

### FZ-31 — RMS calculator `float(np.sqrt(np.mean(np.square(...))))` copy-pasted across 11 sites in 8 files
**Status:** ❌ Not Fixed — moderate scope (11 sites; deferred to cleanup sprint)
**Description:** Every transcription engine (qwen, parakeet, whisper/transcription), the level monitor, the audio-quality analyzer, the microphone test recorder, and the streaming chunker independently compute the same RMS-from-samples formula. There are TWO dtype variants (`np.float32` for live mic levels, `np.float64` for offline analysis) and ONE variant that uses `audio_chunk**2` instead of `np.square` — i.e. three near-identical formulas that drift in numerical precision.
**Root Cause:** Each engine module was written independently, copy-pasting the formula. The `audio_quality.py` comment at line 178 shows this duplication has already caused one dead-code bug.
**Impact:** A future precision fix must be applied to 11 sites. The `vad.py:322` variant using `audio_chunk**2` produces different dtype/precision results than the others — silent inconsistency between RMS values used for VAD vs. for hallucination rejection.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py:823`
- `voice_typer/server/level_monitor.py:1009`
- `voice_typer/server/audio_quality.py:210`
- `voice_typer/server/qwen_engine.py:409,475`
- `voice_typer/server/microphone_test_recorder.py:359`
- `voice_typer/server/streaming.py:186`
- `voice_typer/server/parakeet_engine.py:732,870,1173`
- `voice_typer/server/vad.py:322`**Fix:** Add `def compute_rms(audio: np.ndarray, *, dtype=np.float64) -> float` to a shared module (`voice_typer/server/audio_math.py` or extend `asr_utils.py`). Update all 11 call sites.
**Severity:** 🔴 High

### FZ-33 — 38 Python functions exceed 100 LOC (8 exceed 250 LOC)
**Status:** ❌ Not Fixed — too large (38 functions across many files; deferred)
**Description:** Top offenders: `startup_sequence.run` (547 LOC), `ipc_server._send` (434 LOC), `crash_recovery.create_diagnostic_bundle` (384 LOC), `ipc_server._handle_tcp_connection` (382 LOC), `app.py __init__` (357 LOC), `dictation_pipeline.run` (347 LOC), `clipboard/manager.paste` (345 LOC), `config_applier.apply_config_side_effects` (322 LOC), `sidecar_ws._handle_connection` (319 LOC), `parakeet_engine.load` (296 LOC), `config.load` (288 LOC), `config_applier.apply_config` (260 LOC). 30+ more functions in the 100-260 LOC range.
**Root Cause:** Several god-methods survived earlier refactors because they grew organically as new branches were appended.
**Impact:** Long functions are hard to unit-test in isolation, hard to read, and frequently grow further. `apply_config_side_effects` already has CR-65 step 2 documented as "future refactor" — the debt is acknowledged but unfixed.
**Progress:** None yet.
**Related Files:** Many — see above
**Fix:** Extract per-concern helpers (the config-applier docstring already proposes a `ConfigSideEffect` protocol + handler list; `ipc_server._send` should split transport-write, shutdown-suppression, and rate-limit into separate helpers).
**Severity:** 🔴 High

---

### FZ-51 — `bubble-window.ts` (698 LOC) mixes 4 cohesive sub-responsibilities
**Status:** ❌ Not Fixed — moderate scope (3-file split); deferred (FZ-13 addresses the most urgent bubble-window.ts issue)
**Description:** The file bundles: (a) Bubble positioning (~150 LOC pure geometry/screen queries with no BrowserWindow dependency), (b) Fullscreen detection (~30 LOC OS-detection concern), (c) BrowserWindow lifecycle (~230 LOC), (d) Show/hide animation + IPC orchestration (~250 LOC reaching DIRECTLY into `ipcMain`).
**Root Cause:** REF-2 split extracted "bubble window stuff" as one unit without recognizing that positioning, fullscreen-detection, lifecycle, and animation are independent cohesion units.
**Impact:** 698-LOC file is the second-largest in `main/`. Positioning helpers can't be reused by tests without loading the entire BrowserWindow creation path.
**Progress:** FZ-13 addresses the most urgent issue (moving `bubble:hidden` listener to `bubble-handlers.ts`). Full split deferred.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts`
**Fix:** Split into `windows/bubble-positioning.ts`, `windows/bubble-fullscreen.ts`, `windows/bubble-window.ts` (slimmed to ~520 LOC).
**Severity:** 🟡 Medium

### FZ-52 — `AudioFilterChain.tsx` (935 LOC) renders 7 filter groups (24 SettingRows) inline with a 90-LOC i18n useMemo
**Status:** ❌ Not Fixed — moderate scope (9-file split); deferred
**Description:** 935 LOC single function component. 7 distinct filter groups rendered as inline JSX. Inline 90-LOC useMemo block that resolves ~48 i18n `t()` calls into a flat labels object — then a 50-LOC destructure that unpacks every label into a local `const`. Adding a new filter row requires editing BOTH blocks plus the JSX. 520+ LOC of near-identical SettingRow+RangeSlider blocks.
**Root Cause:** The component was created to consolidate two duplicated call sites by literally pasting all 24 rows into one component instead of decomposing by filter group.
**Impact:** Diffing a single filter's slider bounds requires scanning 700 LOC of JSX. Re-rendering: any change to ANY filter field re-renders the entire 24-row tree.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx`
**Fix:** Split into 7 filter-group sub-components + 1 labels hook + 1 constants module.
**Severity:** 🟡 Medium

### FZ-53 — `bubble-components.tsx` (823 LOC) is a multi-component file (plural name) that should be a directory
**Status:** ❌ Not Fixed — moderate scope (11-file split); deferred
**Description:** The file name is plural ("bubble-components") and it indeed exports 4 components + 4 hooks + 3 type aliases + 7 constants + 2 helpers. `useAudioLevels` (180 LOC) is itself a small monolith: rAF loop, bar-color cache, MutationObserver for theme changes, AND a parallel "is recording" mirror of the state machine that duplicates logic from `useBubbleStateMachine`. Three of the four button components share an identical 1-line className string (copy-pasted 3×).
**Root Cause:** The extraction from `Bubble.tsx` was a single-step "move everything that isn't the orchestrator into a sibling file" — the natural next step (split into a directory) was never taken.
**Impact:** Consumers wanting only `BubbleDismissButton` import the entire 823-LOC module. The shared className string is a maintenance hazard.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/bubble-components.tsx`
**Fix:** Convert to `bubble-components/` directory with `constants.ts`, `useThemeSync.ts`, `useAudioLevels.ts`, `useBubbleLifecycle.ts`, `useBubbleStateMachine.ts`, `BubbleVisualizer.tsx`, `BubbleIconButton.tsx` (shared primitive), `BubbleMicButton.tsx`, `BubbleStopButton.tsx`, `BubbleDismissButton.tsx`, `index.ts` (barrel).
**Severity:** 🟡 Medium

### FZ-54 — Default hotkey `"<caps_lock>"` hardcoded as literal in 5 server-side sites + 1 TS site
**Status:** ❌ Not Fixed — moderate scope (6 sites); deferred
**Description:** `config.py:76` (canonical), `hotkey_dispatcher.py:122` (fallback — comment EXPLICITLY says "platform default (see config._default_hotkey_for_platform)"), `onboarding.py:69,307` (wizard state), `onboarding.py:404` (preset list), `client/src/renderer/src/pages/onboarding/lib/constants.ts:17` (TS-side copy). The TS file documents `HOTKEY_DEFAULT` as a local constant with no reference to the server-side `_default_hotkey_for_platform()`. The `hotkey_dispatcher.py` comment proves the maintainer is aware of the duplication.
**Root Cause:** The default hotkey pre-dates the Python↔TS IPC bridge; each layer has its own copy.
**Impact:** If the default hotkey changes, 5 Python sites + 1 TS site must be updated. The `hotkey_dispatcher.py` fallback would silently use the wrong default if `config.py` changes. The TS↔Python drift is invisible to any test.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/onboarding.py`
- `voice_typer/client/src/renderer/src/pages/onboarding/lib/constants.ts`
**Fix:** Server side — import the canonical value in all 4 secondary sites. TS side — the renderer should learn the default via the existing `get_defaults` IPC call. Add a parity test that asserts `constants.ts::HOTKEY_DEFAULT === Config().hotkey`.
**Severity:** 🟡 Medium

### FZ-55 — `noise_filter_*` defaults duplicated between `Config` dataclass and `audio_chain_builder._DEFAULTS` test dict
**Status:** ❌ Not Fixed — moderate scope; deferred
**Description:** `audio_chain_builder.py:143` comment: "Default values matching the Config class defaults (ADR 0007 §5)". The two dictionaries are byte-for-byte identical for 23 of 24 entries. `config.py:1208` even has a comment "ADR 0007: was 150, now 200 (matches OBS)" for `noise_filter_gate_hold_ms` — a default that was bumped from 150→200. If the same bump is ever made to another field, the `audio_chain_builder._DEFAULTS` dict will silently drift.
**Root Cause:** `build_chain_from_dict` (used by tests) accepts a plain dict and uses `_DEFAULTS` for missing keys, because constructing a real `Config()` instance in unit tests was deemed too heavy.
**Impact:** Changing a Config default silently breaks the `build_chain_from_dict` test path — tests will pass with the OLD default while production uses the NEW default. The `_DEFAULTS` dict has no parity test.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/audio_chain_builder.py`
**Fix:** Replace `_DEFAULTS` with a `Config()` instance: `def build_chain_from_dict(config_dict, sample_rate=16000): cfg = Config(); for k, v in config_dict.items(): setattr(cfg, k, v); return build_chain(cfg, sample_rate=sample_rate)`.
**Severity:** 🟡 Medium

### FZ-56 — `vad_speech_threshold: 0.5` and `vad_silence_threshold: 0.3` duplicated as `getattr` fallback literals
**Status:** ❌ Not Fixed — small scope; deferred
**Description:** `config.py:1109-1110` (canonical defaults) vs `vad_processor.py:177-178` (fallback literals in getattr): `self._speech_threshold: float = getattr(config, "vad_speech_threshold", 0.5)`. The `0.5` is a hardcoded fallback that must match `config.py:1109`'s `0.5` but is not imported from it.
**Root Cause:** `vad_processor.py` accepts a config-like object (duck-typed) for testability; the `getattr` fallback was added so tests can pass a stub config.
**Impact:** Drift between `config.py` defaults and `vad_processor` fallbacks. A test that constructs a minimal stub config will exercise a different threshold than production.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/vad_processor.py`
**Fix:** Import the defaults: `from voice_typer.server.config import Config as _Config; _DEFAULT_VAD_SPEECH_THRESHOLD = _Config.vad_speech_threshold`.
**Severity:** 🟡 Medium

### FZ-57 — Platform-detection `sys.platform == "win32"` repeated inline despite `platform_utils.is_windows()` existing
**Status:** ❌ Not Fixed — moderate scope (8 sites); deferred
**Description:** The codebase has TWO helper modules (`server_platform/platform_flags.py` and `platform_utils.py`) that both expose `is_windows()` / `is_macos()` / `is_linux()`. Yet ≥8 non-crash-handler modules still inline `sys.platform == "win32"`. `config_validators.py` even aliases `import sys as _sys` to do the same check.
**Root Cause:** The helpers were introduced later but older modules were never migrated.
**Impact:** A platform-detection bug fix must be applied to 8+ sites. The 2-helper-module split is itself a minor DRY smell.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/server_platform/{autostart,platform_flags,microphone_list}.py`
- `voice_typer/server/{_paths,config_validators,autostart_launcher,microphone_watcher_coreaudio,credential_store,native_hotkeys/binary_path}.py`
**Fix:** Migrate the 8 non-crash-handler sites to `from voice_typer.server.platform_utils import is_windows, is_macos, is_linux`. Consolidate the 2 helper modules. Add a lint/test that forbids `sys.platform ==` outside an allowlist.
**Severity:** 🟡 Medium

### FZ-58 — `test_history_and_models.py` and other test files use ticket-ID class names (SEC8/G4L06/SVC2/etc.)
**Status:** ❌ Not Fixed — too large (project-wide rename); deferred to test-quality sprint
**Description:** 29+ test files named after tickets/bugs rather than production modules: `test_cr_fixes.py`, `test_er_fix_g1.py`, `test_er_fix_g2.py`, `test_er_fix_h.py`, `test_g_perf_reliability_fixes.py`, `test_hp7_empty_transcription_fix.py`, `test_i5_retry_fixes.py`, `test_ipc4_rate_limiter_dual_window.py`, `test_ipc5_error_envelope_parity.py`, `test_low_findings_batch.py`, `test_nh17_force_cancel_wording.py`, `test_nh23_onboarding_progress_persistence.py`, `test_perf_fixes.py`, `test_perf_review_fixes.py`, `test_remaining_fixes.py`, `test_xa6_bubble_error_visibility.py`, `test_ec4_python_command_registry_parity.py`, plus the `*_de_fixes.py` / `*_xv_fixes.py` / `*_er_fixes.py` family.
**Root Cause:** Tickets drive file creation, not module identity.
**Impact:** Inverse lookup fails — to find tests for `credential_store.py` you must read `test_credential_store.py` AND `test_credential_store_de_fixes.py` AND `test_credential_store_outcome.py`. Bug-fix-named files rarely get pruned.
**Progress:** None yet.
**Related Files:** 29+ test files (see above)
**Fix:** Merge each `*_de_fixes.py` / `*_xv_fixes.py` / `*_er_fixes.py` into its parent module test file. Rename ticket-named root files to module-named. Keep ticket IDs only in docstrings/pytest markers.
**Severity:** 🟡 Medium

### FZ-59 — `time.sleep` in 88 test files (top offender: 20 calls in `test_microphone_watcher.py`)
**Status:** ❌ Not Fixed — too large (88 files); deferred to test-quality sprint
**Description:** 88 test files contain at least one `time.sleep`. Top offenders by call count: `test_microphone_watcher.py` (20), `test_hotkeys_win32.py` (18), `test_level_monitor.py` (15), `test_clipboard_win32_coverage.py` (11), `test_audio_callback.py` (9), `test_smart_duck_monitor.py` (8), `test_shutdown_pool_drain.py` (8), `test_recorder_worker_lifecycle.py` (8), `test_clipboard_restore_race.py` (8).
**Root Cause:** Real-thread / real-process timing tests use wall-clock sleeps to wait for background workers. No central "wait_for_predicate" helper was adopted.
**Impact:** Suite is slow and flaky. On a loaded CI runner, sleeps that are "just enough" on a dev box under-shoot and produce intermittent failures.
**Progress:** None yet.
**Related Files:** 88 test files (see above)
**Fix:** Add a `wait_until(predicate, timeout=2.0, interval=0.01)` helper to `tests/conftest.py` and migrate the top 15 offenders. For thread-synchronization tests, prefer `threading.Event` with timeout over sleep+poll.
**Severity:** 🟡 Medium

### FZ-60 — `kill_process_tree` uses N+2 process spawns + 200ms blocking `thread::sleep` on the Tauri event loop
**Status:** ❌ Not Fixed — requires adding `nix` crate dependency + careful async migration; deferred
**Description:** `src-tauri/src/state.rs:228-312` (now moved to `platform/process.rs` per FZ-21): each shell-out spawns a child process (~5-10ms on Linux). For N descendants, that's (1 + N + N) process spawns + a 200ms blocking `thread::sleep` on the calling thread. The function is called from `shutdown_sidecar_for_exit` via `block_on`, so it runs on the Tauri event-loop thread and blocks ALL event processing for 200ms + spawn overhead.
**Root Cause:** `Command::new("pgrep")/("kill")` was used for portability simplicity instead of `nix` crate syscalls.
**Impact:** ~200-300ms total event-loop freeze per shutdown, plus 3N process spawns.
**Progress:** None yet (FZ-21 moved the function to the right module; the spawn-based implementation remains).
**Related Files:**
- `src-tauri/src/platform/process.rs` (post-FZ-21)
**Fix:** Use the `nix` crate's `unistd::getpgid`/`sys::signal::kill` (or read `/proc/<pid>/task/<pid>/children` directly on Linux) to walk the process tree in-process via syscalls. Use `tokio::time::sleep` if called from an async context.
**Severity:** 🟡 Medium

### FZ-62 — `setLocale` missing from Tauri bridge (`window-namespace.ts`) — parity contract broken
**Status:** ❌ Not Fixed — low impact (tray labels still update via `set_tray_locale` Python IPC); deferred
**Description:** The Electron preload (preload/index.ts:81) and main handler (window-handlers.ts:290) exist for `i18n:set-locale`; the Tauri bridge (window-namespace.ts) and the `WindowBridge` type (bridge.ts) do not. The renderer's `i18n.ts:445-448` uses an inline `as` cast + optional chaining to access `setLocale`, so on Tauri the call silently no-ops. The Python-side `set_tray_locale` IPC call DOES work on Tauri via `window.python.call`, so tray-menu labels still update.
**Root Cause:** The Tauri bridge was never ported for the `i18n:set-locale` channel.
**Impact:** On Tauri, the renderer's locale change does NOT push to a main-process handler. Native Tauri dialogs use the OS locale, not a main-process-pushed locale, so there is no direct user-visible dialog-localization regression. However, the parity contract is broken.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/window-namespace.ts`
- `voice_typer/client/src/preload/index.ts`
- `voice_typer/client/src/main/ipc/window-handlers.ts`
**Fix:** Add a `setLocale` method to `createWindowNamespace` in `window-namespace.ts` that invokes a Rust command (e.g. `set_host_locale`) which stores the locale in `SidecarState`.
**Severity:** 🟢 Low

### FZ-64 — Magic numbers in `main/` (process-exit backstops, TCP frame cap, IPC timeouts) should be named constants
**Status:** ❌ Not Fixed — low impact; deferred
**Description:** `index.ts:148` (3000ms SIGTERM backstop), `bootstrap.ts:421` (2000ms production-exit backstop), `bubble-window.ts:336` (2000ms bubble reload), `tcp-connect.ts:178,180` (4 MB TCP frame cap duplicated between conditional and log string), `send-to-python.ts:201` (120000/15000 IPC timeouts). The two process-exit backstops use different values with no comment explaining the 1s discrepancy.
**Root Cause:** Ad-hoc literals sprinkled across modules.
**Impact:** A future change to "the process-exit backstop" requires grepping for `setTimeout` and reading each one's surrounding comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/{index,bootstrap,windows/bubble-window,python/tcp-connect,python/send-to-python}.ts`
**Fix:** Add named constants to `src/main/constants.ts`: `PROCESS_EXIT_BACKSTOP_MS`, `SIGTERM_EXIT_BACKSTOP_MS`, `BUBBLE_RELOAD_BACKOFF_MS`, `TCP_FRAME_MAX_BYTES`, `IPC_TIMEOUT_SHORT_MS`, `IPC_TIMEOUT_LONG_MS`.
**Severity:** 🟢 Low

### FZ-65 — `var` in inline HTML bootstrap scripts (`index.html`, `bubble.html`)
**Status:** ❌ Not Fixed — trivial; deferred
**Description:** `index.html:39` and `bubble.html:29` use `var locale, SUPPORTED, tag;` in inline i18n-locale bootstrap scripts that run BEFORE the React app mounts.
**Root Cause:** Pre-ES6 syntax in inline scripts that predate the TS migration and live outside the bundler's TS pipeline.
**Impact:** `var` is function-scoped (not block-scoped), leaking to global if hoisted. The two HTML files duplicate the same bootstrap logic verbatim.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/{index,bubble}.html`
**Fix:** Convert `var` → `let`/`const` in both HTML files. Optionally extract to a shared `i18n-bootstrap.js` snippet.
**Severity:** 🟢 Low

### FZ-66 — 12+ underscore-prefixed test-only exports ship in production main-process modules
**Status:** ❌ Not Fixed — low impact (small bundle cost); deferred
**Description:** At least 12 `_`-prefixed test-only exports ship in the production bundle: `_resetIpcBackpressureForTests`, `_LONG_RUNNING_COMMANDS_FOR_TEST`, `_resetNativeThemeListenerForTest`, `_resetRenderCrashTrackingForTest`, `_resetStopPythonFlagsForRestart`, `_resetTrayAvailableCache`, `_resetFileSizeCacheForTest`, `_getCachedFileSize`, `_setCachedFileSize`, `_clearCachedFileSize`, `_resetErrorHandlersDisposeForTest`.
**Root Cause:** Test isolation pattern — production modules expose reset/inspection hooks so vitest tests can clear module-level caches between cases.
**Impact:** Minor: production bundle carries ~12 small test-helper functions. Tree-shaking MIGHT elide them, but the exports are public.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/send-to-python.ts`
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/python/stop-python.ts`
- `voice_typer/client/src/main/tray_available.ts`
- `voice_typer/client/src/main/logging/fileSizeCache.ts`
- `voice_typer/client/src/main/bootstrap.ts`
**Fix:** Consider extracting test helpers into sibling `*.test-utils.ts` files excluded from the production build.
**Severity:** 🟢 Low

### FZ-69 — Inline IIFE render closures in `ThemeSettingsSection.tsx` and `HotkeyPicker.tsx` defeat `memo()` on children
**Status:** ❌ Not Fixed — addressed by the larger splits (FZ-6, FZ-7) which are themselves deferred
**Description:** `ThemeSettingsSection.tsx:886-920` (SelectTrigger preview IIFE), `ThemeSettingsSection.tsx:940-974` (disabled-Custom SelectItem IIFE), `HotkeyPicker.tsx:898-909` (DropdownMenuTrigger label IIFE). These IIFEs produce fresh closures on every render, forcing reconciliation of the IIFE's output even when the parent's props haven't changed. The `ThemeSettingsSection` SelectTrigger IIFE even calls `document.documentElement.classList.contains("dark")` on every render — a DOM read that shouldn't be in the render path.
**Root Cause:** IIFEs in JSX are a common React anti-pattern when the author wants to compute a value inline.
**Impact:** Defeats `React.memo` on the parent. Layout-thrash risk from DOM reads in the render path.
**Progress:** None yet (would be naturally resolved by FZ-6 and FZ-7 splits).
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/hotkey/HotkeyPicker.tsx`
**Fix:** Extract the IIFEs into named sub-components or `useMemo` hooks. Pass `isDark` as a prop from the parent instead of reading `document.documentElement.classList`.
**Severity:** 🟢 Low
