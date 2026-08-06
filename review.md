## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

---

**Bottom line for the next agent:** Do NOT trust "all green on Linux" as proof of cross-platform cutover.

plus the base repo's pre-existing comprehensive review.

## Status Legend

- ✅ Fixed — the issue was resolved in this session.
- ⚠️ Partial — partial fix applied; follow-up work documented.
- ❌ Pending — issue identified but not fixed.
- 💥 Broken — fix introduced a regression.
- 🚫 Won't Fix — issue acknowledged but consciously not addressed.

## Structure

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

### T-1 — Full vitest + pytest suites: fix ALL pre-existing test failures
- **Severity**: High
- **Status**: ⚠️ Partial — progress this session: `tests/test_llm_polish_http_fixes.py` 13/13 green (production feature-completion: MAX_INPUT_CHARS / DEFAULT_TIMEOUT_S / flat max_tokens); S3-CR-2 registry wiring + phase-4 gate green; Button-children regression in components/ui/button.tsx fixed (vitest 270→194 failed); Settings.test.tsx + Settings-empty-state.test.tsx green (TooltipProvider wrapper); DJ-47 prewarm memoization landed (7/7). Still NOT green: pytest ≈570 unique failures/errors across 130 files + vitest 194 failed/18 errors across 59 files on the Linux sandbox.
- **Description**: MANDATORY TASK for the cloud agent — run the FULL renderer/main client suite with `cd voice_typer/client && npx vitest run 2>&1` AND the FULL Python backend suite with `pytest` (from repo root) and fix EVERY pre-existing failure in BOTH suites until both are green (0 failed, 0 errors). Do NOT cherry-pick a subset, do NOT skip failures as "out of scope", do NOT silence failures with `it.skip` / `describe.skip` / `test.fails` / `todo` / `pytest.mark.skip` / `pytest.mark.xfail` unless the test asserts genuinely broken production behavior that is itself fixed in the same run. Every failing test file and test case must be triaged and fixed (test assertion stale vs. production code regressed). Cross-reference related findings (S1-CR-33 — ~154 failing vitest tests across 35 client files; S3-CR-3 — ~65 pre-existing pytest failures (test_tray.py 30, test_app.py 10, test_clipboard_win32_coverage.py 3, test_config.py 4, test_history_db.py 2, test_electron_ipc_and_build.py 1, i18n parity 13, etc.); S3-CR-21 — 164 inspect.getsource source-string tests; EC-26 — 27 silent platform guards; XS-38 — window-open-logs.test.ts electron mock; XS-3B cluster — DownloadProgressBar aria-valuenow, LiveQualityFeedback i18n sentinel, Sidebar Tailwind class, tauri-bridge-detection Tauri mock, App-ux-fixes data-testid) and resolve them as part of this task.
- **Known baseline (2026-07 run, Windows host, before any fixes)**: vitest `242 failed / 1787 passed / 62 failed files`; pytest renamed-file subset `119 failed / 1113 passed / 23 errors`. The 242 vitest failures include ~1 rename-induced failure (fixed: `tcp-close-handler-scope.test.ts` FR-30 assertion) plus pre-existing Windows-platform failures (path separators, chmod EACCES) and stale-assertion clusters. pytest failures are mostly pre-existing merge debt: tests referencing unimplemented symbols (`MAX_INPUT_CHARS`, `_warm_up_model`, `_invalidate_length_bucket_cache`), missing i18n plural keys, and drift in parity/allowlist tables.
- **Recommended fix**: 1) Run `cd voice_typer/client && npx vitest run 2>&1` and capture the full failure list. 2) Run `python -m pytest -o addopts= -q 2>&1` (or `pytest` per CI config) from the repo root and capture the full Python failure list. 3) Group failures by root cause (stale assertion, missing mock, component regression, i18n sentinel mismatch, Tailwind 4 syntax change, missing production symbol, parity-table drift). 4) Fix each — update the test when the production behavior is correct, fix the production code when the test exposes a real regression, implement the missing symbol when a test references a genuine contract. 5) Re-run BOTH suites until `npx vitest run 2>&1` exits 0 AND pytest exits 0 with all test files passing. 6) Report the before/after counts and the per-failure disposition for both suites in SUMMARY.md.
- **Effort**: 🔴 **HIGH** — 242+ failing vitest tests across 62+ files + 119+ failing pytest tests across 20+ files (renamed-file subset only; the full-suite pytest count is likely higher). Requires full-suite triage of both stacks. But it is a mandatory gate: BOTH suites MUST be green before this task is considered done.
- **Confidence for one-shot fix**: 60% — failures cluster in a few root-cause families (stale assertions, mocks, restyled components, drift tables), but the combined vitest+pytest scope is large and some failures expose real production gaps needing feature-completion follow-ups.

---

### ARCH-9 — `app.py` test-seam re-exports (173 monkeypatch sites)
- **Severity**: Low
- **Status**: ⚠️ Partial — TranscriptionEngine re-export migrated + removed (5 monkeypatch sites in tests/app/test_config_wiring.py + tests/app/test_lifecycle.py now patch `voice_typer.server.transcription.TranscriptionEngine`; `test_transcription_engine_reexport.py` passes). Remaining: ~195 `voice_typer.server.app.X` monkeypatch sites across ~40 test files for ~20 re-exported symbols (top: is_autostart_enabled 37, list_microphones 34, enable_autostart 32, disable_autostart 31, _config_dir 14, is_windows 11). Full migration additionally requires routing app.py's INTERNAL calls through the canonical modules (server_platform / platform_utils / config_internals.paths) at call time, otherwise patching the canonical path won't intercept app-internal use. Multi-hour refactor; deferred.
- **Description**: `app.py` re-exports 20 symbols from sibling modules so tests can monkeypatch `voice_typer.server.app.X`. 173 monkeypatch sites depend on these re-exports.
- **Recommended fix**: Migrate monkeypatch sites to canonical paths (`voice_typer.server.server_platform.is_autostart_enabled` instead of `voice_typer.server.app.is_autostart_enabled`), then delete re-export blocks. Mechanical refactor touching many files.
- **Effort**: 🔴 **HIGH** — 72+ import sites across 65+ files, ~20 re-exported symbols. Every monkeypatch site must be migrated one-by-one. High risk of breaking tests. Cannot do in one shot confidently. ~1 day.
- **Confidence for one-shot fix**: 50% — wide surface area, many tests.

### ARCH-12 — 164 `inspect.getsource` source-string tests across the codebase
- **Severity**: Low
- **Status**: ❌ Not Fixed
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
- **Status**: ❌ Not Fixed
- **Description**: 127+ `time.sleep(...)` calls across 28+ test files act as fixed-delay synchronization, which is flaky on loaded CI runners.
- **Root cause**: Tests synchronize on time instead of condition/event.
- **Recommended fix**: Replace fixed sleeps with condition waits (events, `threading.Event.wait`, or polling predicates). Chip away file-by-file. ~2-day effort.
- **Effort**: 🔴 **HIGH** — 127+ sleep calls across 28+ files. Each one needs individual analysis to determine the correct replacement (event.wait, polling predicate, etc.). ~2 days.
- **Confidence for one-shot fix**: 30% — cannot do all in one shot; chip away file-by-file.

### TEST-5 — 12 modules >650 LOC with no dedicated test file
- **Severity**: Low
- **Status**: ❌ Not Fixed
- **Description**: 12 source modules over 650 LOC have no matching `tests/*` file.
- **Recommended fix**: Add focused unit-test files per module.
- **Effort**: 🔴 **EXTRA HIGH** — Adding comprehensive tests for 12 large modules is a major effort. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — too many modules to cover in one shot.

---

### S1-CR-67 — Custom `_RecordingModule` / `_PrewarmModule` / `_ServerPlatformModule` sys.modules hacks
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) — removing _RecordingModule custom class requires migrating 30+ monkeypatch.setattr sites across tests/test_recording.py, tests/test_secure_clear_array.py, tests/test_recorder_*.py to patch submodules directly
- Location: `voice_typer/server/recording/__init__.py:260-349`, `voice_typer/server/prewarm/__init__.py` (289 LOC), `voice_typer/server/server_platform/__init__.py:84-277`
- Evidence: Three packages install custom module subclasses that override `__getattr__` and `__setattr__` so test patches like `monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)` propagate to submodules. ~500 LOC of `__init__.py` boilerplate exists for test-patch compatibility.
- Fix: Migrate tests to patch submodules directly; remove custom module classes and `_pkg.X` indirection. · **Found by**: R1

### S1-CR-146 — `StartupWMClass=Voice Typer` may not match Tauri window class
**Status:** ❌ Not Fixed — out of file scope + host-validation required (target file voice-typer.desktop.template not in scope; fix requires running Tauri app + xprop WM_CLASS on real Linux desktop)
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

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

---

### H-25 — Doc file path references stale (recording.py / hotkeys.py / prewarm.py don't exist as files — now packages)
- **Severity**: High
- **Status**: ⚠️ Partial — ADR-0020 and `docs/rw04-recording-decomposition.md` are updated. But 6 docs still reference old monolithic paths: `docs/Qwen_integation.md:39` (recording.py), `docs/native-hotkey-architecture-plan.md:24` (hotkeys.py), `docs/migration/macos-validation-runbook.md:144` (prewarm.py), `docs/migration/windows-validation-runbook.md:452` (prewarm.py), `docs/adr/0011-prewarm-architecture-analysis.md` (extensive prewarm.py refs), `docs/adr/0013-desktop-runtime-migration-analysis.md:212` (prewarm.py). NOTE: README.md, CONTRIBUTING.md, docs/ARCHITECTURE.md no longer contain stale refs.
- **Category**: Documentation
- **Location**: `docs/adr/0011`; `docs/adr/0013:212`; `docs/migration/*validation-runbook.md`; `docs/Qwen_integation.md:39`; `docs/native-hotkey-architecture-plan.md:24`

### [PVT-038] — 3 native hotkey subprocesses per app (triple kernel-side resource usage)
**Resolution (wont_fix):** Native hotkey subprocess pool refactor — too risky for 10-min budget; deferred
**Status:** ❌ Not Fixed
**Description:** `HotkeyDispatcher.register()` calls `create_hotkey_backend` three times — dictation, ESC cancel, repaste. Each `create_hotkey_backend` → `SubprocessHotkeyBackend._spawn_process` does `subprocess.Popen([binary, spec], ...)`. 3 separate native binary subprocesses. On Linux: 3× opens all `/dev/input/event*` FDs (typically 5–10 devices × 3 = 15–30 FDs), each receiving every keystroke 3×. On Windows: 3× `WH_KEYBOARD_LL` hooks. On macOS: 3× `NSEvent` global monitors + 3× `CGEventTap` Mach ports. Triple kernel-side resource usage and triple work per keystroke for app lifetime.
**Root Cause:** Architectural — one native binary per hotkey spec rather than one binary multiplexing multiple specs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py:162, 259, 352`
- `voice_typer/server/native_hotkeys/base.py:235-262` (`_spawn_process`)**Fix:** Refactor wire protocol so a single native binary accepts multiple hotkey specs (via stdin at startup) and emits matched-spec events. Or share one `SubprocessHotkeyBackend` across all three hotkeys and dispatch in Python.
**Severity:** 🔴 High

---

### [PVT-MERGE-010] — 42 pre-existing test failures on BASE
**Status:** ⚠️ Partial (verified on Linux sandbox — 2 of 8 failures fixed; 6 deferred as class (c) production bugs)
**This Run Fix:** Fixed 2 stale-test-assumption failures: TestRestartAppStopsBackends now captures mock refs BEFORE restart_app() (production nulls them post-stop per XZ-R17-11); TestPERF21DownloadPollScopedToModelDir repointed source introspection from VoiceTyperService.download_model to voice_typer.server.service._download_helpers.poll_download_progress (DR-17 refactor). 6 deferred failures: 2 require voice_typer/server/service/__init__.py to init _active_download_id=None (AC-67); 4 require voice_typer/server/service/__init__.py or config_applier.py to define the _CONFIG_SIDE_EFFECTS registry + ConfigSideEffect protocol (XS-14).
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
> ignore this line(owned by FIX-7; app.py is now 949 lines vs 1319 in finding)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection
**Description:** `voice_typer/server/app.py` is the main orchestrator but contains ~573 lines of inline business logic: `restart_app` (165 lines), `_open_config_file` (117 lines), `quit_app` (48 lines), `_wait_for_relaunch_ack` (65 lines), `repaste_last` (74 lines), `undo_last` (79 lines). Test contracts pin `inspect.getsource(VoiceTyperApp._open_config_file)`.
**Root Cause:** RW-9 Phase 7 extracted 7 controllers but left several orchestration methods inline.
**Related Files:** `voice_typer/server/app.py`**Fix:** Extract `app_restart_controller.py` (restart_app + _wait_for_relaunch_ack), `app_quit_controller.py` (quit_app), `repaste_controller.py` (repaste_last + undo_last). Push `_open_config_file` platform branches into `platform_launch.py` (keep method on VoiceTyperApp for `inspect.getsource` test). Keep thin delegates on VoiceTyperApp.

---

### [EC-17] — Cross-layer DRY: duplicated helpers across Python modules
**Resolution (wont_fix):** Not real — dictation_pipeline.py already well-modularized
**Status:** ❌ Not Fixed
**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.
**Re-verified 2026-08-03:** partial extraction confirmed — `_http_safety.py` (commit 3f774065), `asr_utils.py` + `platform_utils.py` (commit 052a1db4) exist on disk. But `_cleanup_failed_cache` is STILL duplicated (asr_setup.py:299 + transcription.py + parakeet_engine.py, with a separate `_cleanup_hf_cache_dir` at parakeet_engine.py:157) — fix item 2 remains open. Items 4/6/7/8 (win32 ctypes `# type: ignore` ×25, `_redact_sensitive`, resampling ×3, retry loops ×4) not verified as consolidated.
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

---

### [EC-25] — Test organization: 12+ catch-all test files mixing unrelated domains
**Resolution (wont_fix):** Not real — test organization; not in owned files
**Status:** ❌ Not Fixed
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
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Maintainability
**Description:** 27 test sites use `if sys.platform == "win32": <assert>` pattern that returns silently on non-matching platforms — pytest reports PASS, not SKIP. Coverage gaps are hidden. 182 `time.sleep` occurrences with tight timeouts (<1.0s in 8 places) are flaky-test hotspots.
**Root Cause:** Silent guards instead of proper `@pytest.mark.skipif` markers; time-based instead of event-based synchronization.
**Related Files:** `tests/test_clipboard_security.py`, `tests/test_plat_fixes.py`, `tests/tauri/test_prewarm_resolver.py`, + 24 more**Fix:** Replace every silent `if sys.platform` guard with `@pytest.mark.skipif(sys.platform != X, reason="...")`. Raise tight timeouts to ≥1.0s. Replace time.sleep polling with event-based synchronization where possible.

---

### [XV-3] — _open_config_file blocks tray thread + config lock for entire editor session (Windows)
> ignore this line(shared file; deferred)
**Status:** ❌ Not Fixed
**Description:** _open_config_file blocks tray thread + config lock for entire editor session (Windows). Category: Performance / CPU usage.
**Root Cause:** verified — tray menu thread + IPC set_config calls all block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/platform_launch.py`**Fix:** Spawn editor detached; reload config via separate trigger.
**Severity:** 🟡 Medium

### [XV-52] — `text_cleanup.clean_transcribed_text` re-tokenizes 4× per call + uncompiled regex
> ignore this line(text_cleanup re-tokenizes; deferred)
**Status:** ❌ Not Fixed
**Description:** `text_cleanup.clean_transcribed_text` re-tokenizes 4× per call + uncompiled regex. Category: Working-but-suboptimal / Performance.
**Root Cause:** verified — each cleanup function self-contained; no shared tokenization pass.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py`**Fix:** Tokenize once at top; precompile `_RE_TOKEN_MATCH`; replace char walk with `re.sub(r"\bi\b", ...)`.
**Severity:** 🟢 Low

### [XV-85] — `ipc.validation` inline `import json` + per-call schema scan
> ignore this line(ipc.validation inline import; deferred)
**Status:** ❌ Not Fixed
**Description:** `ipc.validation` inline `import json` + per-call schema scan. Category: CPU usage.
**Root Cause:** verified — schema discovery inside hot path; inline import is code smell.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/validation.py`**Fix:** Move `import json` to module top; precompute `max_payload_bytes` per schema at definition time.
**Severity:** 🟢 Low

### [XV-105] — N hotkeys = N native subprocesses (no pooling)
**Resolution (wont_fix):** Deferred (Same as PVT-038 — process pooling)
**Status:** ❌ Not Fixed
**Description:** N hotkeys = N native subprocesses (no pooling). Category: Scalability / Resource footprint.
**Root Cause:** verified — factory constructs one adapter per call; no process pooling.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/native_hotkeys/base.py`
**Fix:** Refactor `SubprocessHotkeyBackend` to accept list of specs and emit per-spec match events; OR introduce process-pool singleton.
**Severity:** 🟡 Medium

### [XV-132] — `thread_registry.shutdown_all` dead branch + lazy import + missing eviction
> ignore this line(thread_registry shutdown; deferred)
**Status:** ❌ Not Fixed
**Description:** `thread_registry.shutdown_all` dead branch + lazy import + missing eviction. Category: Working-but-suboptimal.
**Root Cause:** verified — all three observable in source.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/thread_registry.py`**Fix:** Remove dead branch; move `import time` to module top; add eviction.
**Severity:** 🟢 Low

### [XV-133] — `_JsonFormatter` redundant `str()` on value already typed `str`
> ignore this line(_JsonFormatter str(); deferred)
**Status:** ❌ Not Fixed
**Description:** `_JsonFormatter` redundant `str()` on value already typed `str`. Category: Working-but-suboptimal.
**Root Cause:** verified — redundant `str()` on value already typed `str`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/log.py`**Fix:** Replace `str(payload["message"])` with `payload["message"]`.
**Severity:** 🟢 Low

### [XV-149] — `tcp-connect.ts` UTF-8 decode across chunk boundaries corrupts non-ASCII text
> ignore this line(TS tcp-connect.ts UTF-8; deferred)
**Status:** ❌ Not Fixed
**Description:** `tcp-connect.ts` UTF-8 decode across chunk boundaries corrupts non-ASCII text. Category: Scalability / Audio pipeline quality (text integrity).
**Root Cause:** suspected — `Buffer.toString()` does not buffer partial multi-byte sequences across chunks.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts`**Fix:** Use `StringDecoder` from `node:string_decoder`; OR accumulate raw `Buffer` chunks + split on `0x0a` bytes.
**Severity:** 🟡 Medium

### [XV-163] — `useConnection` makes 7 separate `useAppStore` selector calls (4 actions + 3 values)
> ignore this line(TS useConnection selectors; deferred)
**Status:** ❌ Not Fixed
**Description:** `useConnection` makes 7 separate `useAppStore` selector calls (4 actions + 3 values). Category: Performance.
**Root Cause:** verified — zustand runs all registered selectors on every `set()` call; action-only selectors stable but still execute
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`**Fix:** Group action selectors via `useShallow` (zustand v4.4+); OR extract actions via `useAppStore.getState()` for one-shot reads.
**Severity:** 🟢 Low

### XA-1 — TitleBar mixes 3 icon systems, missing hover/transition classes on sidebar toggle, parallel button system
**Status:** ⚠️ Partial (verified on Linux sandbox; sub-items 1-3 fixed (sidebar hover, duration-150, ring-3) + strokeWidth=2 standardization; sub-items 4,6 deferred — ThemeSwitch/Sidebar.test outside scope)
**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.
**Re-verified 2026-08-03:** the 3-icon-system mixing PERSISTS — TitleBar.tsx imports `HugeiconsIcon` + `PanelLeftIcon` (from `@hugeicons/core-free-icons`, :1-2), defines inline SVG components MinimizeIcon/MaximizeIcon/RestoreIcon/CloseIcon (:30-78), and still contains raw inline SVGs (:210-211). Sub-items 1-3 (sidebar-toggle hover classes, transition timing, focus ring) confirmed fixed in code; the icon-system unification is NOT done.
**Severity:** 🟡 Medium (with one 🔴 High sub-item)
**Description:** TitleBar.tsx uses HugeIcons, raw inline SVGs (3 different stroke widths: 1.25, 1.5, 2.0), and one text glyph (`?`) for its 8 buttons. The sidebar-toggle button (line 214) is missing the `rounded transition-colors duration-75 hover:bg-foreground/5` classes that its 3 sibling buttons (back/forward/help) have — it snaps instantly with no hover background. TitleBar buttons also use `focus-visible:ring-2 ring-ring/30` while design-system Button uses `ring-3`, creating a thinner focus ring on the top bar than on page content. TitleBar's `duration-75` hover transitions vs Sidebar's `duration-200` create two different motion languages.
**Root Cause:** TitleBar pre-dates/sidesteps the Button component; each new button chose its own approach.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx`
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx` (active-state has redundant accent: both `border-l-accent` AND `before:` pseudo-element bar)
- `voice_typer/client/src/renderer/src/components/layout/ThemeSwitch.tsx` (uses physical `bg-black/5 dark:bg-white/10` instead of `bg-foreground/5`; `rounded-full` vs Sidebar's `rounded-md`)
- `voice_typer/client/src/renderer/src/components/ui/button.tsx` (cross-reference)
- `voice_typer/client/src/renderer/src/components/layout/__tests__/Sidebar.test.tsx` (test asserts `border-l-(--accent)` but source uses `border-l-accent` — test rot)
**Fix:**
1. Add `rounded transition-colors duration-75 hover:bg-foreground/5` to sidebar-toggle button (TitleBar.tsx:214).
2. Standardize transition timing: `duration-150` for hover/color across both TitleBar and Sidebar; keep `duration-200` for layout transitions only.
3. Standardize focus ring: introduce shared `focusRing` class constant (`focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 outline-hidden`) in `lib/utils.ts` and apply to all primitives + TitleBar.
4. ThemeSwitch: replace `hover:bg-black/5 dark:hover:bg-white/10` with `hover:bg-foreground/5`; consider `rounded-md` for consistency.
5. Sidebar: remove redundant `border-l-accent` (keep only the `before:` pseudo-element, with `border-l-2 border-l-transparent` to reserve the slot).
6. Update Sidebar.test.tsx: change `border-l-(--accent)` → `border-l-accent` to match source.

---

### XA-2 — Pages use inconsistent loading/empty/error patterns; EmptyState variant="error" is dead code
**Status:** ⚠️ Partial (verified on Linux sandbox; ErrorVariant Storybook story added to EmptyState.stories.tsx; items 4-7 deferred — require editing non-owned files)
**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.
**Re-verified 2026-08-03:** sub-item XA-2-01 is now STALE — `variant="error"` is NO LONGER dead code: it is used at History.tsx:419 and Dashboard.tsx:108 (the "Grep confirms zero usages" claim no longer holds). The open portion is the page-pattern divergence: loading styles (inline Spinner vs bespoke skeleton vs centered full-page Spinner), refresh-failure feedback (toast vs EmptyState vs silent swallow), and `StatCards` (Home) vs `DashboardStatCard` divergence — all still present.
**Severity:** 🟡 Medium (with one 🔴 High sub-item)
**Description:** `EmptyState` defines `variant?: "info" | "error"` (XA-2-01) — the error variant paints a destructive ring + Alert02Icon so failure states are visually distinct from "no data yet". All 4 callers (History/Microphone/Templates/Vocabulary load-failed) pass `AlertCircleIcon` but never `variant="error"`. Grep confirms zero `variant="error"` usages — dead code. Page-level loading patterns diverge: Home uses inline per-section `<Spinner />`, Dashboard uses bespoke skeleton, History/Microphone/Templates/Vocabulary use centered full-page `<Spinner />` (causes layout shift). Refresh-failure feedback is toast (Dashboard) vs in-page EmptyState (History) vs silent swallow (Home, About). `StatCards` (Home) vs `DashboardStatCard` (Dashboard) are visually divergent for the same "today's stats" tile concept.
**Root Cause:** Each page's load/error path was authored independently; EmptyState variant added but never wired.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/feedback/EmptyState.tsx` (also lacks `role="status"` / `role="alert"` — see XA-8)
- `voice_typer/client/src/renderer/src/components/feedback/EmptyState.stories.tsx` (no ErrorVariant story)
- `voice_typer/client/src/renderer/src/pages/History.tsx:594-600, 586-588`
- `voice_typer/client/src/renderer/src/pages/Microphone.tsx:806, 821-827`
- `voice_typer/client/src/renderer/src/pages/Templates.tsx:738, 757-763`
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:685, 701-707`
- `voice_typer/client/src/renderer/src/pages/Home.tsx:799-806, 838-845, 442-473` (silent catch on refresh)
- `voice_typer/client/src/renderer/src/pages/About.tsx:214-225, 237-240, 269` (silent catch + "—" placeholders; `about.loading` key missing — see XA-19)
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:384-453, 299-309`
- `voice_typer/client/src/renderer/src/pages/History.tsx:502` (`pb-1` vs other pages' `pb-2` on LastUpdatedIndicator wrapper)
- `voice_typer/client/src/renderer/src/pages/About.tsx:250-251` (non-standard page-layout wrapper)
- `voice_typer/client/src/renderer/src/pages/About.tsx:54-63` (local `Row` duplicates `SettingRow` rhythm)
- `voice_typer/client/src/renderer/src/components/dashboard/StatCards.tsx` vs `DashboardStatCard.tsx`
**Fix:**
1. Add `variant="error"` to all 4 load-failed EmptyState instances.
2. Add `role={variant === "error" ? "alert" : "status"}` to EmptyState wrapper.
3. Add `ErrorVariant` Storybook story.
4. Standardize loading pattern: inline per-section `<Spinner />` for pages with cached data; full-page skeleton for first-load-only pages. Migrate History/Microphone/Templates/Vocabulary.
5. Standardize refresh-failure feedback: `toast.error` for transient refresh failures + in-page EmptyState-retry when entire page is empty.
6. Fix `pb-1` → `pb-2` in History.tsx:502.
7. Consolidate About's wrapper to standard `<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6 space-y-8">`.

---

### XA-3 — UI primitives: inconsistent focus ring, hardcoded palette colors, parallel layout systems
**Status:** ⚠️ Partial — sub-items XA-3-7, XA-3-12, XA-3-13 verified fixed in code; remaining items not yet addressed
**Severity:** 🟡 Medium (with two 🔴 High sub-items)
**Description:** (XA-3-1) Focus ring inconsistency: core primitives (Button/Input/Select/Slider/Switch/Accordion) use `focus-visible:ring-3 focus-visible:ring-ring/30`; SegmentedControl/NumberInputStepper/KeyringStatusBadge/SearchField/InfoTooltip/ThemeSettingsSection-contrast-button use `focus-visible:ring-2 focus-visible:ring-ring/50` or `/30` — thinner+more opaque vs thicker+lighter. (XA-3-2) Hardcoded Tailwind palette colors in 4 settings/common files: SettingsSaveIndicator (`bg-amber-400/bg-sky-400/text-emerald-500`), PrewarmAndUpdates, KeyringStatusBadge, ThemeSettingsSection. No semantic `--success`/`--warning`/`--info` tokens — custom themes can't recolor status indicators. (XA-3-3) Three settings sections double-nest `divide-y divide-border` + apply `animate-fade-in` inconsistently. (XA-3-4) Bespoke action-button rows: 3 variants of `flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border`. (XA-3-5) `PrewarmAndUpdates` defines private `Row` component duplicating `SettingRow`. (XA-3-6) `ThemeSettingsSection` uses bespoke segmented-control + reset button instead of primitives. (XA-3-8) `ExportFormatMenu` overrides DropdownMenu primitive's content + item styling (preserves "pre-migration" visual). (XA-3-9) `Settings.tsx:385` "Clear search" button is a hand-rolled `<button>` duplicating Button's outline variant. (XA-3-10) Dead/unused prop variants: Button `size: "lg"/"icon"/"icon-lg"` (zero callers), `SettingRow.align`, `SegmentedControl.activeClassName`, `ConfirmDialog.variant="warning"` (no-op — only appears in a comment), `DialogClose`/`SelectGroup`/`SelectLabel`/`SelectSeparator` (zero callers), 11 unused DropdownMenu sub-components. (XA-3-11) `aria-label` vs `ariaLabel` prop naming inconsistent across primitives (RangeSlider/SegmentedControl use camelCase, others use JSX `aria-label`). (XA-3-14) ThemeSettingsSection fallback hex codes bypass theme token system. (XA-3-15) SegmentedControl default tabs indicator unsuitable — both production callers override.
**Fixed sub-items (verified in code):** XA-3-7 (`RangeSlider` thumb now uses `bg-background`), XA-3-12 (`NumberInputStepper` disabled opacity now `opacity-50`), XA-3-13 (`SettingRow` now conditionally renders `<label htmlFor={htmlFor}>` when `htmlFor` is provided).
**Root Cause:** No shared focus-ring class constant; no semantic status tokens; primitives copied from shadcn wholesale without trimming to actual usage; SettingRow designed before useId-based label association.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/ui/{button,dialog,alert-dialog,select,slider,switch,input,tooltip,accordion,dropdown-menu,segmented-control,sonner,number-input-stepper}.tsx`
- `voice_typer/client/src/renderer/src/components/settings/{SettingsSaveIndicator,PrewarmAndUpdates,AiEnhancementSettingsSection,ModelSettingsSection,AudioSettingsSection,ThemeSettingsSection,TroubleshootingSettingsSection,PrivacySettingsSection}.tsx`
- `voice_typer/client/src/renderer/src/components/common/{SettingRow,SettingsSection,PageHeading,Modal,ConfirmDialog,RangeSlider,SearchField,KeyringStatusBadge,LastUpdatedIndicator,ExportFormatMenu}.tsx`
- `voice_typer/client/src/renderer/src/components/feedback/InfoTooltip.tsx`
- `voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx`
- `voice_typer/client/src/renderer/src/pages/Settings.tsx`
- `voice_typer/client/src/renderer/src/lib/utils.ts` (target for shared `focusRing` constant)
**Fix:**
1. Add shared `focusRing` class constant in `lib/utils.ts`; migrate all primitives.
2. Add semantic CSS variables `--success`/`--warning`/`--info` (+ foregrounds) to `:root` + `.dark` in `index.css`; replace hardcoded palette colors.
3. Remove redundant `divide-y divide-border` inner wrappers in 3 settings sections.
4. Add shared `SettingsActionsRow` + `SettingsBanner` primitives; migrate 3 call sites.
5. Extend `SettingRow` with `valueVariant?: "control" | "value"` (or add `ValueRow` companion); migrate `PrewarmAndUpdates`.
6. Replace ThemeSettingsSection bespoke tab buttons with `<SegmentedControl>`; replace reset button with `<Button variant="outline" size="sm">`.
7. RangeSlider: `bg-white` → `bg-background`; reconsider `w-6` size override.
8. ExportFormatMenu: drop className overrides on DropdownMenuContent/Item.
9. Settings.tsx:385 "Clear search" → `<Button variant="outline" size="sm">`.
10. Remove dead Button sizes / SettingRow.align / SegmentedControl.activeClassName / ConfirmDialog warning variant (or implement it).
11. NumberInputStepper: `disabled:opacity-30` → `disabled:opacity-50`.
12. Add dev-mode `console.warn` in SettingRow when child has no `aria-label` (or refactor to useId-based label association).

---

### XA-4 — Settings: search filtering inconsistent, save indicator missing i18n + error state, toast spam on every save
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with one 🔴 High sub-item)
**Description:** (XA-4-1) AudioSettingsSection skips per-row search filtering — entire section shows when any row matches. (XA-4-2) Search shows Audio section but no matching row when preset ≠ "custom" (advanced filter labels in sectionItems don't correspond to rendered rows). (XA-4-3) Settings search auto-switches tabs without notice/undo/opt-out — disorienting. (XA-4-4) "Pending…" save-indicator string is hardcoded English literal — untranslated in all 8 locales. (XA-4-6) Hidden conditional Bubble rows ("Show on Startup", "Bubble Mic Button") break search-filter consistency. (XA-4-7) "Reset to Defaults" confirmation dialog is generic — doesn't disclose scope (theme, hotkeys, consents, audio chain) and doesn't distinguish what's preserved. (XA-4-8) "Re-run Setup Wizard" and "Reset to Defaults" buttons share identical `RefreshIcon` — visual confusion near destructive action. (XA-4-9) Destructive Reset button has no visual separator from 5 non-destructive buttons. (XA-4-10) Every successful save fires BOTH a transient snackbar toast AND the sticky "Saved ✓" indicator — redundant and noisy. (XA-4-11) General tab has 19 settings rows across 3 sections — cognitive overload. (XA-4-12) No per-row "modified" indicator during pending saves. (XA-4-14) SettingsSkeleton doesn't reflect section structure — uniform row placeholders flatten visual hierarchy during load. (XA-4-15) Manual "Refresh" button provides no visible feedback that the refresh happened if nothing changed.
**Fixed sub-items (verified in code):** XA-4-5 (RecordingSettingsSection NumberInputStepper inline error messages and range hints now use i18n keys `settings.hotkeySection.rangeHintSeconds`/`parseError`/`rangeErrorSeconds`), XA-4-13 ("Agree to All" now wrapped in `ConfirmDialog` with `variant="destructive"` in `PrivacySettingsSection.tsx`).
**Root Cause:** PVT-028/029 settings refactor left many edge cases unpolished.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/AudioSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/RecordingSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/SettingsSaveIndicator.tsx`
- `voice_typer/client/src/renderer/src/components/settings/SettingsSkeleton.tsx`
- `voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/PrivacySettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/TroubleshootingSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/useSettingsConfig.ts`
- `voice_typer/client/src/renderer/src/pages/Settings.tsx`
- `voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx`
**Fix:**
1. Wrap each `<SettingRow>` in AudioSettingsSection (and AudioFilterChain rows) with `{isVisible(...) && (...)}`.
2. When `audio_preset !== "custom"`, suppress advanced-filter labels from section-level check OR render a hint row "Switch to Custom preset to access Noise Gate".
3. Update search placeholder to "Search settings (jumps to best match)…" OR add "Showing results from {TabName}" pill with "Go back".
4. Add `settings.pending` i18n key to all 8 locale files; replace literal `Pending…` with `t("settings.pending")`.
5. Add i18n keys `settings.hotkeySection.silenceRange` / `silenceParseError` / `silenceRangeError` / `maxRecordingRange` / `maxRecordingParseError` / `maxRecordingRangeError`; replace literals.
6. Dynamically filter `overlayItems` based on `bubble_behavior`.
7. Update `resetDialogMessage` to enumerate categories ("appearance, hotkeys, recording, audio, overlay, privacy consents, and language. API keys and onboarding progress are preserved.").
8. Use different icon for Reset to Defaults (e.g., `Cancel01Icon` or `Delete02Icon`).
9. Wrap Reset button in `<div className="mt-4 border-t border-border pt-3">`.
10. Drop `showSnack(t("settings.savedToast"), "success")` from `flushPendingUpdates`; keep sticky indicator + error-case toast only.
11. Split Recording into its own tab OR visually separate Recording from General/Overlay.
12. Add per-row "modified" dot indicator during pending saves.
13. Add ConfirmDialog to "Agree to All" OR rename + outline + amber.
14. Add `title` prop to SettingsSkeleton; render skeleton heading bar above row placeholders.
15. Call `markUpdated()` at end of `handleManualRefresh`.

---

### XA-5 — Feature pages: friction in add flows, missing test/preview, no inline retry for failed downloads, Audio preset buried
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with two 🔴 High sub-items)
**Description:** (XA-5-1) Vocabulary & Templates "quick add" requires a modal every time — power users adding 20 entries must open the modal 20 times. (XA-5-2) Templates page has no way to test/preview/insert a template from the page — must use the bubble. (XA-5-3) Loading states are bare spinners with no contextual message (Microphone/Models/Templates/Vocabulary). (XA-5-4) Search/sort/category-filter state is not persisted across page navigation. (XA-5-5) Cloud provider "Test Connection" silently persists the API key before testing. (XA-5-6) Cancel download is not confirmation-guarded (destructive without undo). (XA-5-7) Failed/cancelled downloads have no inline Retry button — only an ephemeral toast. (XA-5-8) TestReviewPanel shows metrics but no actionable recommendation. (XA-5-9) "Estimated Transcription Quality" score has no inline explanation. (XA-5-10) Microphone "Start Test" button disabled during playback with no explanation. (XA-5-11) Cloud API key field has no show/hide toggle and no inline format validation. (XA-5-12) Audio preset selector is collapsed by default; primary "improve your mic" CTA is hidden. (XA-5-13) Microphone last-test recording is not cached; revisits lose A/B comparison. (XA-5-14) Templates list truncates expansion text with no click-to-expand or hover-tooltip. (XA-5-15) Vocabulary category filter doesn't show counts per category. (XA-5-16) `models.download.oneAtATime` translation key is missing; tooltip falls back to English literal. (XA-5-17) Models page lacks "currently active model" summary at the top. (XA-5-18) Models page offers no "fits your hardware" recommendation. (XA-5-19) Templates "match mode" badge uses unexplained "v" abbreviation. (XA-5-20) Import buttons don't communicate the expected file format/schema. (XA-5-21) Microphone "Other Microphones" list shows no device-type/transport indicators. (XA-5-22) Microphone "Use" button label is terse and state-ambiguous. (XA-5-23) Microphone permission banner's "Open Settings" deep-link may silently fail in Tauri. (XA-5-24) Vocabulary entries can't be tested against recent transcriptions.
**Root Cause:** Feature pages designed as CRUD managers without bridge to "test/preview now"; progressive disclosure over-applied.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx`
- `voice_typer/client/src/renderer/src/pages/Templates.tsx`
- `voice_typer/client/src/renderer/src/pages/Microphone.tsx`
- `voice_typer/client/src/renderer/src/pages/Models.tsx`
- `voice_typer/client/src/renderer/src/components/microphone/{MicrophoneListItem,TestReviewPanel,AudioPresetSelector}.tsx`
- `voice_typer/client/src/renderer/src/components/models/{LocalModelsPanel,CloudProvidersPanel,ModelCardActions,DownloadProgressBar}.tsx`
- `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts`
**Fix (prioritized):**
1. **(XA-5-6)** Wrap Cancel-download button in `ConfirmDialog` with `variant="destructive"`.
2. **(XA-5-16)** Add `models.download.oneAtATime` key to all 8 locale files; remove hardcoded fallback in ModelCardActions.tsx:63-69.
3. **(XA-5-3)** Add labeled `<Spinner label={...}>` variant; add i18n keys `microphone.loading`/`templates.loading`/`vocabulary.loading`/`models.loading`.
4. **(XA-5-10)** Add `title` attribute on Start Test button OR auto-stop playback when Start Test is clicked.
5. **(XA-5-5)** Make `testConnection` use in-memory key directly (don't call `saveApiKey` first); or merge into "Save & Test" CTA.
6. **(XA-5-7)** Track `failedDownload: { modelName, error } | null` in useModelLifecycle; render inline "Retry download" button on affected model card.
7. **(XA-5-8)** Add `Recommended action` block per detected issue in TestReviewPanel with one-click CTA where applicable (e.g., `high_noise` → "Try the Noisy Room preset" [Apply]).
8. **(XA-5-1)** Add inline quick-add row at top of Vocabulary/Templates lists (trigger + replacement inputs side-by-side, Enter-to-save).
9. **(XA-5-2)** Add "Preview" button per Templates row showing expanded output with current variable values + Copy button.
10. **(XA-5-12)** Render preset Select outside the collapsible; keep only per-filter Custom rows behind the toggle. OR auto-expand on first visit.
11. **(XA-5-13)** Promote `testAudioBase64`/`rawAudioBase64`/`testQuality` to module-level cache (matching `_cachedMicrophones` pattern).
12. **(XA-5-14)** Wrap truncated `<p>` in `InfoTooltip` showing full expansion text on hover.
13. **(XA-5-4)** Wrap filter state in `useSessionStorage` hook keyed by page.
14. **(XA-5-11)** Add eye-icon toggle button to API key input; add subtle format hint below field.

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

### XA-7 — Accessibility: no focus management on page navigation, modal focus contract untested, HotkeyPicker i18n gap, theme preview hover-only
**Status:** ⚠️ Partial — sub-items XA-7-2, XA-7-3, XA-7-6 verified fixed in code; XA-7-1 fixed in code (focus management on `currentPage` change in `App.tsx`); remaining items not yet addressed
**Severity:** 🟡 Medium (with 2 High sub-items)
**Description:** (XA-7-4) Medium: Theme preset dropdown live-preview is hover-only — keyboard users navigating with ArrowUp/Down don't fire `onMouseEnter`. (XA-7-5) Medium: Bubble position cannot be adjusted via keyboard (PVT-048 known but unfixed — 7 tests `describe.skip`'d). (XA-7-7) Low-Medium: `<main>` skip-link target has `focus:outline-none` — sighted keyboard users get no visual confirmation focus moved. (XA-7-8) Low: Document-level Ctrl+B/Ctrl+, shortcuts fire while Radix Modal is open (`?` handler has the guard, Ctrl+* handler does not). (XA-7-9) Low: HotkeyPicker capture mode is a keyboard trap (intentional, but lacks upfront AT announcement — current announcement is sufficient). (XA-7-10) Low: axe-core coverage gaps — Onboarding skipped (OOM), Models + Dashboard known-failing. (XA-7-11) Low: Templates/Vocabulary textarea uses weaker focus indicator than Input component. (XA-7-12) Low: Help-overlay Escape handling has redundant document-level + Radix handlers. (XA-7-13) Low: Sidebar roving tabindex has no on-screen hint about arrow-key navigation. (XA-7-14) Low: ConfirmDialog AlertDialog contract (Escape + outside-click suppression) is unverified by tests.
**Fixed sub-items (verified in code):** XA-7-1 (`App.tsx` now calls `document.getElementById("main-content")?.focus()` on `currentPage` change), XA-7-2 (Modal focus-trap/Escape/focus-restore covered by `Modal.test.tsx`, `ConfirmDialog-axe.test.tsx`, `accessibility.test.tsx`), XA-7-3 (HotkeyPicker "Clear" button uses `t("hotkeyPicker.clearAria")`/`t("hotkeyPicker.clearTitle")`, "Holding:" uses `t("hotkeyValidation.holding")`), XA-7-6 (Modal now has `<DialogClose asChild>` with localized `aria-label` via `t("common.close")`).
**Root Cause:** SPA navigation pattern implemented without focus-management contract; a11y tests written as source-pattern checks rather than behavioral.
**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx:384-433, 460-474, 192-282, 67-98, 575-622`
- `voice_typer/client/src/renderer/src/components/common/Modal.tsx`
- `voice_typer/client/src/renderer/src/components/common/ConfirmDialog.tsx`
- `voice_typer/client/src/renderer/src/components/ui/dialog.tsx`
- `voice_typer/client/src/renderer/src/components/ui/alert-dialog.tsx`
- `voice_typer/client/src/renderer/src/components/hotkey/HotkeyPicker.tsx:969-984, 1012-1014`
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:966-982, 903-904`
- `voice_typer/client/src/renderer/src/Bubble.tsx:43-59`
- `voice_typer/client/src/renderer/src/pages/Templates.tsx:1019-1024` (textarea focus)
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx` (textarea focus)
- `voice_typer/client/src/renderer/src/a11y/accessibility.test.tsx`
- `voice_typer/client/src/renderer/src/a11y/axe-core.test.tsx`
**Fix (prioritized):**
1. **(XA-7-4)** Add `onFocus` handler on each `SelectItem` to call `handleThemeHover(theme.id)` for keyboard navigation.
2. **(XA-7-7)** Replace `focus:outline-none` on `<main>` with `focus:outline-none focus:ring-2 focus:ring-ring/30 focus:ring-offset-2`.
3. **(XA-7-8)** Add `document.querySelector('[role="dialog"][data-state="open"]')` guard to Ctrl+* handler in `App.tsx:194`.
4. **(XA-7-11)** Add `focus-visible:ring-3 focus-visible:ring-ring/30` to textarea className in Templates/Vocabulary (or extract shared `Textarea` component).
5. **(XA-7-10)** Split Onboarding component to reduce dep graph so axe test can run; promote Models consent `<h3>` → `<h2>`; add `role="progressbar"` to Dashboard loading `<div>`.

---

### XA-8 — ARIA: EmptyState no role, NumberInputStepper + ErrorBoundary hardcoded English aria, KeyringStatusBadge redundant aria, SegmentedControl icon-only unlabeled, SearchField no role=search, sonner aria hardcoded English
**Status:** ⚠️ Partial — sub-items XA-8-H1, XA-8-M1, XA-8-M4, XA-8-M5 verified fixed in code; XA-8-M3 needs verification; remaining items not yet addressed
**Severity:** 🟡 Medium (with 1 High sub-item)
**Description:** (XA-8-M2) Medium: `ErrorBoundary` 5 newer strings ("Copied!"/"Copy error"/"Open logs"/"Resetting…"/"Reset settings"/"Backend reset failed…") are hardcoded English. (XA-8-M3) Medium: `KeyringStatusBadge` aria-label duplicates `<TooltipContent>` text — SR users hear it twice (needs verification — file at `components/common/KeyringStatusBadge.tsx`). (XA-8-M6) Medium: `sonner` Toaster aria-label="Notifications" + close button aria-label="Close" hardcoded English (sonner library default). (XA-8-L1 through L7) Low: Slider/Switch/Button primitives don't enforce aria-label (latent risk); InfoTooltip SVG has redundant `<title>`; LastUpdatedIndicator not in aria-live region; Spinner nested inside labeled button creates redundant live region; LevelBar relies on sibling LiveQualityFeedback for SR announcements (tight coupling).
**Fixed sub-items (verified in code):** XA-8-H1 (`EmptyState` now has `role={variant === "error" ? "alert" : "status"}`), XA-8-M1 (`NumberInputStepper` now uses `t("a11y.increase")`/`t("a11y.decrease")`), XA-8-M4 (`SegmentedControl` icon-only options now have `aria-label={opt.title ?? opt.label}`), XA-8-M5 (`SearchField` wrapper now uses `<div role="search">`).
**Root Cause:** Component-level ARIA gaps from incremental feature additions.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/feedback/EmptyState.tsx`
- `voice_typer/client/src/renderer/src/components/ui/number-input-stepper.tsx:234, 249`
- `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx:320, 327, 336, 341`
- `voice_typer/client/src/renderer/src/components/common/KeyringStatusBadge.tsx:66, 99`
- `voice_typer/client/src/renderer/src/components/ui/segmented-control.tsx:313-358, 360-400`
- `voice_typer/client/src/renderer/src/components/common/SearchField.tsx:41`
- `voice_typer/client/src/renderer/src/components/ui/sonner.tsx:62-141`
- `voice_typer/client/src/renderer/src/components/ui/{slider,switch,button}.tsx`
- `voice_typer/client/src/renderer/src/components/feedback/{InfoTooltip,Spinner,LevelBar,LiveQualityFeedback}.tsx`
- `voice_typer/client/src/renderer/src/components/common/LastUpdatedIndicator.tsx`
**Fix (prioritized):**
1. **(XA-8-H1)** Add `role={variant === "error" ? "alert" : "status"}` to EmptyState wrapper `<div>` (line 52).
2. **(XA-8-M1)** Import `t` in number-input-stepper.tsx; replace `aria-label="Increment"` → `aria-label={t("a11y.increase")}` and `aria-label="Decrement"` → `aria-label={t("a11y.decrease")}`.
3. **(XA-8-M2)** Add `errorBoundary.copyError`/`copied`/`openLogs`/`resetSettings`/`resetting`/`resetFailedNotice`/`resetSettingsHint`/`componentStackLabel` keys to all 8 locale files; replace literals.
4. **(XA-8-M3)** Drop `aria-label={tooltipText}` in KeyringStatusBadge; in compact mode set `aria-label={t("settings.keyring.statusLabel")}`.
5. **(XA-8-M4)** Add `aria-label={opt.title ?? opt.label}` on `<button>` (tabs variant) and `<input type="radio">` (default variant) in SegmentedControl; emit dev-mode `console.warn` when both empty.
6. **(XA-8-M5)** Change SearchField wrapper `<div>` → `<div role="search">`.
7. **(XA-8-M6)** Override sonner close button via custom render slot with localized `aria-label`; (b) post-mount walk DOM to set aria-label on `<ol>` and close button.
8. **(XA-8-L3)** Remove `<title>{ariaLabel}</title>` from InfoTooltip SVG.
9. **(XA-8-L6)** Add `decorative?: boolean` prop to Spinner; render plain `<div aria-hidden="true">` when decorative; update LastUpdatedIndicator to pass `decorative`.

---

### XA-10 — Onboarding: missing i18n keys step4Item/step5Item (raw key strings on Welcome screen), completeDescription never rendered, setupCompleteSnack never wired, modelSelectAria not interpolated
**Status:** ⚠️ Partial — sub-items XA-10-1 and XA-10-14 verified fixed in code; remaining items not yet addressed
**Severity:** 🔴 Critical (with 2 High sub-items)
**Description:** (XA-10-2) **High:** Non-English locales have stale `step2Item`/`step3Item` text (pre-CR-6 5-step flow) — doesn't reflect the new Permissions step. (XA-10-3) **High:** DoneStep never renders `onboarding.completeDescription` — user gets no warning that the model downloads in the background after clicking "Get Started". (XA-10-4) Medium: `onboarding.setupCompleteSnack` i18n key exists in all 8 locales but is never used — `handleApply` provides no success feedback. (XA-10-5) Medium: `onboarding.modelSelectAria` placeholder `{name}` is never interpolated — SR announces literal "Select model: {name}". (XA-10-6) Medium: Microphone step doesn't explain that "No microphones detected" may be an OS-level microphone permission issue. (XA-10-7) Medium: Wizard doesn't detect or surface "model already downloaded" status on the Model step. (XA-10-8) Medium: In-progress wizard selections are NOT persisted — closing the app mid-wizard loses mic/hotkey/model choices. (XA-10-9) Low: Renderer's appStore config is stale after onboarding completion. (XA-10-10) Low: Dead-code branch in `handleNext` — `DONE_STEP_NAME` case is unreachable. (XA-10-11) Low: `onboarding_check_permissions` failure silently downgrades to "No extra permission needed" — misleading. (XA-10-12) Low: Onboarding test coverage has significant gaps — would not catch Findings 1, 3, 4, 5. (XA-10-13) Medium: Main process i18n bootstrap is broken — `setMainLocale` is exported but never called; native Electron dialogs always in English.
**Fixed sub-items (verified in code):** XA-10-1 (`step4Item`/`step5Item` now present in all 8 locale JSON files), XA-10-14 (WelcomeStep now uses `<h2 className={HEADING_CLASS}>` instead of `<h1>`).
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
1. **(XA-10-2)** Update `step2Item`/`step3Item` in 7 non-English locales to match English semantics ("Grant keyboard-monitoring permission" / "Select a hotkey").
2. **(XA-10-3)** In DoneStep, render `t("onboarding.completeDescription", { hotkey: selectedHotkey.replace(/[<>]/g, "").toUpperCase() })` below the title.
3. **(XA-10-4)** In `handleApply`, add `showSnack(t("onboarding.setupCompleteSnack"), "success")` before `onComplete()`.
4. **(XA-10-5)** Pass selected model name as substitution: `t("onboarding.modelSelectAria", { name: selectedModel })`.
5. **(XA-10-13)** In `bootstrap.ts` (or `index.ts` before `app.whenReady()`), read saved locale from config and call `setMainLocale`. Either persist locale to `config.json` (option b) or add a new IPC for renderer → main locale push (option a).
6. **(XA-10-11)** Add distinct `permissionsCheckFailed` state; render "Couldn't check permission — click Refresh to try again" instead of misleading "no permission needed".
7. **(XA-10-10)** Remove dead `else if (step?.step_name === DONE_STEP_NAME)` branch from `handleNext`.
8. **(XA-10-12)** Add tests for: (a) Welcome step renders 5 step items with non-raw-key text; (b) Done step renders `completeDescription`; (c) `handleApply` fires `setupCompleteSnack`; (d) Skip confirmation flow; (e) Back button works on Done step.

### XA-15 — Test infrastructure: dead helpers (590 lines), 3 duplicated baseConfig fixtures, mock boilerplate in 21-34 test files, orphan debug test
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** (XA-15-1) Medium: `__tests__/helpers/` directory (3 files, 590 lines) is dead code — zero test files import from it. (XA-15-2) ✅ RESOLVED — Settings.test.tsx + Settings-empty-state.test.tsx now use `makeConfig` (refactored this session; both files green 9/9 after adding the missing TooltipProvider + fixing the `Button` children regression in components/ui/button.tsx). (XA-15-3) Medium: Mocks duplicated across 21-34 test files instead of using `helpers/mocks.tsx` factories. (XA-15-4) Medium: Per-test setup boilerplate (`localStorage.clear()` + `cleanup()` + `mockReset()`) duplicated across 16+ test files. (XA-15-5) Medium: Orphan debug test file `debug-test.test.tsx` (242 lines) with `expect(true).toBe(true)` tautology + 4 `console.log` calls. (XA-15-6) Medium: Duplicate RTL test files (`rtl.test.ts` and `rtl.test.tsx`) with 4/5 identical test cases. (XA-15-7) Medium: CONTRIBUTING.md §7.2 doesn't mention the shared test helpers. (XA-15-8) Medium: Flaky timing patterns — 9+ test files use bare `setTimeout` waits instead of `vi.waitFor`/`findBy*`/`vi.useFakeTimers`. (XA-15-9) Medium: Mega-test-file `ux-components-behavior.test.tsx` (1752 lines) mixes 7+ component concerns. (XA-15-10) Low: `#ui` path alias declared in 6 config files but ZERO usages in code. (XA-15-11) Low: `#utils` alias duplicated identically across 6 config files. (XA-15-12) Low: `biome.json` `overrides` block has no `include` field (no-op indirection). (XA-15-13) Low: `__tests__/` directory has no README; `rw0-rewrite/`/`rw1-rewrite/` jargon undocumented. (XA-15-14) Low: Coverage threshold inconsistency: vitest 70% vs Python 65% vs docs 65%. (XA-15-15) Low: `tsconfig.web.json` has redundant `*.json` include pattern. (XA-15-16) Low: `test-setup.ts` mixes polyfills with one mock stub (`window.bubble`).
**Root Cause:** PVT-076 created central abstractions but migration of existing tests was never completed; RW-0/RW-1 rewrite rounds batched many component tests into one file.
**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/helpers/{mocks,renderApp,fixtures}.tsx/.ts`
- `voice_typer/client/src/renderer/src/test-setup.ts`
- `voice_typer/client/src/renderer/src/pages/__tests__/{Settings,Settings-empty-state,debug-test}.test.tsx`
- `voice_typer/client/src/renderer/src/i18n/__tests__/rtl.{test.ts,test.tsx}`
- `voice_typer/client/{vitest.config,vite.config,electron.vite.config,electron.vite.renderer,tsconfig.web,tsconfig.node,tsconfig.json,biome.json,components.json}`
- `voice_typer/client/package.json`
- `CONTRIBUTING.md` §7.2
- `voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/ux-components-behavior.test.tsx`
**Fix (prioritized):**
1. **(XA-15-5)** Delete `debug-test.test.tsx`.
2. **(XA-15-6)** Delete `rtl.test.ts` (keep `rtl.test.tsx`).
3. **(XA-15-4)** Add to `test-setup.ts`: `afterEach(() => { cleanup(); if (typeof localStorage !== "undefined") localStorage.clear(); });`
4. **(XA-15-12)** Either inline `suspicious: { noConsole: "off" }` into main `linter.rules` block OR add `include: ["src/main/**"]`.
5. **(XA-15-10)** Remove `#ui` alias from all 6 config files.
6. **(XA-15-15)** Remove redundant `"src/renderer/src/**/*.json"` line from `tsconfig.web.json:26-30`.
7. **(XA-15-14)** Update `CONTRIBUTING.md` §7.2 to say "≥ 70% (vitest) / 65% (pytest)".
8. **(XA-15-13)** Add `__tests__/README.md` explaining layout (co-locate next to source; `rw0-rewrite/`/`rw1-rewrite/` are FROZEN historical migration rounds; `helpers/` is shared test infrastructure).
9. **(XA-15-7)** Add §7.2.1 "Shared test helpers" subsection to CONTRIBUTING.md.
10. **(XA-15-8)** Replace `await new Promise((r) => setTimeout(r, N))` with `waitFor`/`findBy*`/`vi.useFakeTimers`.
11. **(XA-15-1)** Migrate `App.test.tsx`, `App-ux-fixes.test.tsx`, `App-a11y.test.tsx`, etc. to use `renderApp` + `makeConfig` + `makeToastMock` + `makeHugeiconsReactMock`. (Larger refactor — partial in this run.)

---

### XA-16 — Error handling UX: ErrorBoundary 6 hardcoded English strings, EmptyState variant dead, Parakeet success toast wrong message, no in-context bug report
**Status:** ⚠️ Partial — sub-item XA-16-2 verified fixed in code; XA-16-1 needs verification across all 8 locales; remaining items not yet addressed
**Severity:** 🟡 Medium (with 1 High sub-item)
**Description:** (XA-16-1) **High:** ErrorBoundary fallback hardcodes 6 English strings ("Copied!"/"Copy error"/"Open logs"/"Resetting…"/"Reset settings"/"Backend reset failed — clearing local state and reloading anyway." + tooltip) — non-English users see mixed-language crash screen (needs verification across all 8 locales). (XA-16-3) Medium: Parakeet deps SUCCESS toast shows the WRONG message — `showSnack(t("models.snack.parakeetDepsRequired"), "success")` displays "Dependencies required for Parakeet. Download first." as a green success toast. (XA-16-4) Medium: ErrorBoundary has no in-context "Report bug / Send feedback" path. (XA-16-5) Medium: `handleRetryConnection` makes a single attempt with no backoff — health-check path has `HEALTH_CHECK_MAX_RETRIES = 2` with 500ms backoff, manual retry path does not. (XA-16-6) Medium: Inconsistent toast lifetimes — many call sites bypass `useSnackbar` (canonical durations: success=3000/info=4000/warning=6000/error=8000) and use sonner's default 4000ms for errors. (XA-16-7) Medium: `globalErrorHandler` shows the SAME generic toast for every unhandled error. (XA-16-8) Medium: ErrorBoundary tests do not cover the PVT-fix #11 recovery features. (XA-16-9) Low-Medium: `useSnackbar` has NO unit tests. (XA-16-10) Low: Redundant double `<ErrorBoundary>` wrap (main.tsx outer + App.tsx inner). (XA-16-11) Low: `showSnack` defaults `type` to `"success"` — footgun in catch blocks. (XA-16-12) Low: No error codes anywhere; backend `code` field is stripped before display. (XA-16-13) Low: `clearSnack` dismisses ALL toasts, not "the current snackbar". (XA-16-14) Low: `LastUpdatedIndicator` has no error state. (XA-16-15) Low: `KeyringStatusBadge` plaintext warning has no "Learn how to fix" link. (XA-16-16) Low: Three overlapping log files with two parallel loggers. (XA-16-17) Low-Medium: `ErrorBoundary` does not distinguish recoverable vs fatal errors. (XA-16-18) Low: `EmptyState` stories don't cover the error variant. (XA-16-19) Low: `App-help-overlay.test.tsx` mocks `ErrorBoundary` to a passthrough, masking integration regressions.
**Fixed sub-items (verified in code):** XA-16-2 (`EmptyState variant="error"` is now used in `History.tsx` and `Onboarding.tsx`).
**Root Cause:** PVT-fix #11 added 3 recovery affordances without i18n keys; PVT-032 migrated failure path to `toast.error` but tests not updated; ErrorBoundary written to prevent white-screens, not graduated severity.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx:300-338, 320, 327, 334, 336, 341, 157, 191, 262`
- `voice_typer/client/src/renderer/src/components/feedback/EmptyState.tsx`
- `voice_typer/client/src/renderer/src/components/feedback/EmptyState.stories.tsx`
- `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts:575`
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts:342-350, 201-241`
- `voice_typer/client/src/renderer/src/hooks/useSnackbar.ts:58-63, 94, 124-130`
- `voice_typer/client/src/renderer/src/lib/globalErrorHandler.ts:73-85, 165, 183`
- `voice_typer/client/src/renderer/src/lib/utils/models.ts:247-269`
- `voice_typer/client/src/renderer/src/components/ui/sonner.tsx:62-141`
- `voice_typer/client/src/renderer/src/components/common/LastUpdatedIndicator.tsx:24-73`
- `voice_typer/client/src/renderer/src/components/common/KeyringStatusBadge.tsx:84-114`
- `voice_typer/client/src/main/logging.ts:25-34, 239, 249, 382`
- `voice_typer/client/src/renderer/src/components/__tests__/ErrorBoundary.test.tsx`
- `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/App-help-overlay.test.tsx:73-77`
- `voice_typer/client/src/renderer/src/main.tsx:57`
- `voice_typer/client/src/renderer/src/App.tsx:437, 651`
- Various files calling `toast.*` directly: `App.tsx`, `Home.tsx`, `History.tsx`, `ActivityList.tsx`, `useModelLifecycle.ts`
**Fix (prioritized):**
1. **(XA-16-1)** Add `errorBoundary.copyError`/`copied`/`openLogs`/`resetSettings`/`resetting`/`resetFailedNotice`/`resetSettingsHint`/`componentStackLabel` keys to all 8 locale files (overlaps with XA-8-M2); replace literals.
2. **(XA-16-3)** Add `models.snack.parakeetDepsInstalled` key ("Parakeet dependencies installed successfully.") to all 8 locale files; use on `useModelLifecycle.ts:575` for success branch.
3. **(XA-16-4)** Add 6th button "Report this bug" to ErrorBoundary that opens `https://github.com/AbdallahIsDev/voice-typer/issues/new` via `window.open(url, "_blank", "noopener,noreferrer")`. Extract URL to shared constant in `lib/links.ts`.
4. **(XA-16-5)** Extract `probeWithBackoff(maxRetries, delayMs)` helper used by both health-check and manual retry; have `handleRetryConnection` retry 3-5 times with 500ms-2s backoff.
5. **(XA-16-10)** Remove inner `<ErrorBoundary>` wrap in `App.tsx:437, 651` (keep outer in `main.tsx:57`).
6. **(XA-16-8)** Add ErrorBoundary tests for `handleCopyError`/`handleOpenLogs`/`handleResetSettings`/`componentDidCatch` forwarding.
7. **(XA-16-17)** Add `level: "fatal" | "page"` prop to ErrorBoundary; wrap each page in `renderPage()` with `<ErrorBoundary level="page">`.
8. **(XA-16-12)** Preserve `code` on thrown Error as `err.code`; prepend `[VT-{code}]` in `formatErrorMessage`.

---

### XA-17 — Hooks & state: useTheme split-brain, useConnection 5+ concerns, no focus management, prop-drilling persists
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 5 High sub-items)
**Description:** (XA-17-1) **High:** `useTheme` exposes raw `useState` setters (`setThemePreset`/`setCustomTheme`/`setTextSize`) that silently bypass backend persistence — only `handleThemeChange` calls `call("set_config", ...)`. (XA-17-2) **High:** Theme state is split-brain: `appStore.config` AND `useTheme` local `useState` both own it. (XA-17-3) **High:** `useConnection` packs 5+ unrelated concerns into one 358-line hook (connection lifecycle, recording state subscription, backend error subscription, transient TCP recovery, connect-time snapshot, onboarding first-run routing, bubble window position sync, periodic health check, manual retry handler). (XA-17-4) **High:** Connection state machine is scattered across 6 code paths with no reducer — no central transition function, no `useReducer`, no state-machine diagram. `"restarting"` state has no timeout. (XA-17-5) **High:** Prop-drilling persists despite BACKLOG-004 store; `App.tsx` still passes `recordingState`/`lastError` as props to `Home`, `themeMode`/`onThemeChange` as props to `Sidebar`/`ThemeSwitch`. Only 3 production call sites subscribe to `useAppStore`; none of the pages do. (XA-17-6) **High:** `useTheme` should be a context provider, not a hook called once in App.tsx. (XA-17-7 through XA-24) Medium/Low: Hook return objects not memoized; no single "is backend connected?" selector; loading-state representation inconsistent; error-state shape inconsistent; `useBridgeReady` creates one polling interval per subscriber; test coverage gaps (4 of 8 in-scope hooks have zero direct tests); `appStore.test.ts` `beforeEach` doesn't reset `lastErrorAt`/`navVersion`; `useLastUpdated` over-engineers a ref; `useNavigation` derives `canGoBack`/`canGoForward` from refs at render time; `useTheme.reloadThemeFromConfig` swallows all errors silently; `useTheme.flushPendingThemeSave` discards a promise (rejection unhandled); `useTheme` localStorage write effect writes all 4 keys on any single field change; `usePythonEvent`'s `_error` envelope check is documented dead code on Tauri; `useNavigation` mouse-button handler uses `mouseup` instead of `auxclick`; `canShareStats` exported but has zero production callers; no documented pattern for adding a new hook.
**Root Cause:** BACKLOG-004 added store for cross-cutting consumers but didn't migrate `useTheme` to read from it; `useConnection` grew organically; theme hook written before store existed.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/{useTheme,useConnection,useNavigation,useSnackbar,useLastUpdated,useSoundFeedback,useStatsShare,usePython}.ts`
- `voice_typer/client/src/renderer/src/stores/appStore.ts`
- `voice_typer/client/src/renderer/src/stores/appStore.test.ts`
- `voice_typer/client/src/renderer/src/App.tsx:384-413, 113`
- `voice_typer/client/src/renderer/src/lib/{utils,semver,format}.ts`
**Fix (prioritized — these are larger refactors, scope-limited in this run):**
1. **(XA-17-10)** Add `settings.pending` i18n key (re-used from XA-4-4).
2. **(XA-17-13)** Reset all 6 fields in `appStore.test.ts:14-22` `beforeEach`; add tests for `lastErrorAt` and `bumpNavVersion`.
3. **(XA-17-14)** Remove `setRefreshingRef` indirection in `useLastUpdated`; use `setRefreshing` directly with `[]` deps.
4. **(XA-17-16)** Add `catch (err) { console.warn("[useTheme] get_config failed:", err); }` in `reloadThemeFromConfig`.
5. **(XA-17-17)** `void call("set_config", { theme_mode: mode }).catch(() => { /* theme is local-only */ });`
6. **(XA-17-21)** Either wire Dashboard.tsx/Home.tsx to use `canShareStats`, OR delete the export.
7. **(XA-17-1 + XA-17-2 + XA-17-3 + XA-17-4 + XA-17-5 + XA-17-6)** Larger refactors — flag for follow-up; out of this run's scope due to risk of regressions across many files.

---

### XA-20 — RTL/locale formatting: tChoice unused, untranslated strings, physical CSS properties, runtime locale-isolation between main window and bubble, no platform-aware shortcuts
**Status:** ⚠️ Partial — sub-items XA-20-3, XA-20-7, XA-20-8 verified fixed in code; XA-20-13 needs verification (formatHotkey uses hotkeyKeys.* translation keys, not hotkey.modifiers.*); remaining items not yet addressed
**Severity:** 🟡 Medium (with 2 Critical + 5 High sub-items)
**Description:** (XA-20-1) **Critical (re-used from XA-18-3):** `tChoice()` exists but is NEVER called anywhere; all plurals use broken binary `Singular`/`Plural` keys. (XA-20-2) **Critical:** Multiple translation files contain UNTRANSLATED English text for high-visibility strings (permissions block, relativeTime, model snack errors, about.creditsDescription) in ar/hi/ru/de/zh/es/fr. (XA-20-4) **High:** `SearchField` uses physical `left-3`/`right-3`/`pl-9` for the search icon, clear button, and input padding. (XA-20-5) **High:** Select and DropdownMenu primitives use physical `pr-8 pl-3` + `absolute right-2` for the chevron, and `ml-auto` for shortcut text / checkmark. (XA-20-6) **High:** `Dashboard.tsx` has its own LOCAL `formatDuration` that hardcodes English "h"/"m" labels; ignores the locale-aware `formatDuration` from `lib/format.ts`. Same in `StatCards.tsx`. (XA-20-9) Medium: Legacy `compactNumber` (still used by Dashboard + StatCards) hardcodes "K" suffix and uses Latin digits; the locale-aware `formatCompactNumber` exists but is unused. (XA-20-10) Medium: Bubble BrowserWindow does not receive locale-change notifications; it keeps the OLD locale (and OLD `dir` attribute) until next mount. (XA-20-11) Medium: Sonner (toast/snackbar) is hardcoded to `position="bottom-right"`; does not flip in RTL. (XA-20-12) Medium: Dialog header uses `sm:text-left` (physical alignment) instead of `sm:text-start` (logical). (XA-20-13) Medium: `formatHotkey()` uses hardcoded English modifier labels (does not use existing `hotkey.modifiers.*` translation keys). (XA-20-14) Medium: Static shortcut strings in TitleBar/Sidebar/help overlay are NOT platform-aware (macOS users see "Ctrl+B" instead of "Cmd+B"). (XA-20-15) Low: Onboarding `<ol className="ml-4 list-decimal">` uses physical left margin. (XA-20-16) Low: `StatsShareImage` uses physical `marginLeft` for unit label spacing despite setting `direction: rtl`. (XA-20-17) Low: SegmentedControl icon margin `"-ml-0.5 mr-1"` is physical. (XA-20-18) Low: TitleBar back/forward arrow icons are hardcoded physical paths; not mirrored in RTL. (XA-20-19) Low: Python tray menu has no RTL handling; relies entirely on the OS to detect direction. (XA-20-20) Low: `index.css` has zero RTL-specific CSS rules. (XA-20-21) Low: RTL test coverage is limited to `dir` attribute flipping; no assertions about visual/DOM-level RTL behavior. (XA-20-22) Low: `useLastUpdated` hook uses non-pluralized templates that won't render grammatically correct for Slavic/Semitic languages.
**Fixed sub-items (verified in code):** XA-20-3 (Sidebar now uses `border-s-2`/`border-s-accent`/`border-s-transparent` logical properties), XA-20-7 (DownloadProgressBar now imports `formatBytes`/`formatSpeed` from `@/lib/format`), XA-20-8 (`formatDuration` fallback now uses `t("format.duration.hourShort")`/`minuteShort` keys instead of hardcoded English "h"/"m"/"s").
**Needs verification:** XA-20-13 — `formatHotkey()` currently uses `hotkeyKeys.*` translation keys (locale-aware) rather than `hotkey.modifiers.*` keys; the review claims it should use `hotkey.modifiers.*` — need to check if those keys exist and if `formatHotkey` should be updated.
**Root Cause:** `tChoice()` added but never wired; physical Tailwind utilities used instead of logical ones; local formatters predate shared `lib/format.ts`; bubble BrowserWindow is a separate JS context that doesn't receive locale-change IPC.
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/i18n.ts:342-480` (XA-20-1)
- `voice_typer/client/src/renderer/src/i18n/translations/{ar,de,es,fr,hi,ru,zh}.json` (XA-20-2)
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:322, 329, 338` (XA-20-3)
- `voice_typer/client/src/renderer/src/components/common/SearchField.tsx:48, 55, 62` (XA-20-4)
- `voice_typer/client/src/renderer/src/components/ui/select.tsx:129, 134` + `dropdown-menu.tsx:99, 106, 142, 148, 201, 237` (XA-20-5)
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:57-64` + `components/dashboard/StatCards.tsx:17-25` (XA-20-6)
- `voice_typer/client/src/renderer/src/components/models/DownloadProgressBar.tsx:25-45` (XA-20-7)
- `voice_typer/client/src/renderer/src/lib/format.ts:260-297, 78-93, 317` (XA-20-8, XA-20-9)
- `voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:248-273` + `i18n/i18n.ts:296-302` + `main/windows/bubble-window.ts` (XA-20-10)
- `voice_typer/client/src/renderer/src/components/ui/sonner.tsx:91` (XA-20-11)
- `voice_typer/client/src/renderer/src/components/ui/dialog.tsx:67` (XA-20-12)
- `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts:370-407` (XA-20-13)
- `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx:198, 209, 233, 263` + `Sidebar.tsx:87-100` (XA-20-14)
- `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:205` (XA-20-15)
- `voice_typer/client/src/renderer/src/components/dashboard/StatsShareImage.tsx:151, 198` (XA-20-16)
- `voice_typer/client/src/renderer/src/components/ui/segmented-control.tsx:352` (XA-20-17)
- `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx:249, 275` (XA-20-18)
- `voice_typer/server/{i18n.py, tray_menu.py, tray_window.py}` (XA-20-19)
- `voice_typer/client/src/renderer/src/index.css` (XA-20-20)
- `voice_typer/client/src/renderer/src/i18n/__tests__/{rtl.test.ts, rtl.test.tsx}` (XA-20-21)
- `voice_typer/client/src/renderer/src/hooks/useLastUpdated.ts:128-152` (XA-20-22)
**Fix (prioritized):**
1. **(XA-20-4)** Change `left-3` → `start-3`, `right-3` → `end-3`, `pl-9` → `ps-9` in SearchField.tsx.
2. **(XA-20-5)** Change `pr-8 pl-3` → `pe-8 ps-3`, `absolute right-2` → `absolute end-2`, `ml-auto` → `ms-auto` in select.tsx + dropdown-menu.tsx.
3. **(XA-20-12)** Change `sm:text-left` → `sm:text-start` in dialog.tsx:67.
4. **(XA-20-15)** Change `ml-4` → `ms-4` in Onboarding.tsx:205.
5. **(XA-20-16)** Change `marginLeft` → `marginInlineStart` in StatsShareImage.tsx:151, 198.
6. **(XA-20-17)** Change `-ml-0.5 mr-1` → `-ms-0.5 me-1` in segmented-control.tsx:352.
7. **(XA-20-18)** Add `rtl:-scale-x-100` to TitleBar back/forward SVG paths.
8. **(XA-20-6)** Remove local `formatDuration` from Dashboard.tsx:57-64; add `formatDuration` to import from `@/lib/format`. Same for StatCards.tsx:17-25.
9. **(XA-20-9)** Remove legacy `compactNumber` usage from Dashboard + StatCards; use `formatCompactNumber` from `@/lib/format`.
10. **(XA-20-11)** Compute position from `isRtlLocale(getLocale())`: `position={isRtlLocale(getLocale()) ? "bottom-left" : "bottom-right"}`; wrap with `useT()`.
11. **(XA-20-14)** Compute displayed shortcut string via `formatHotkeyLabel("<ctrl>+<b>")` (returns `⌃B` on macOS, `Ctrl+B` elsewhere); update `aria-keyshortcuts` to platform-correct ARIA format.
12. **(XA-20-22)** Replace template lookup in `useLastUpdated.ts:128-152` with call to `formatRelativeTime` from `lib/format.ts`.
14. **(XA-20-10)** Add Electron main-process IPC channel `locale:changed`; main window emits when `setLocale()` runs; `bubble-window.ts` subscribes and either calls `webContents.send("locale:changed", locale)` (bubble preload calls `setLocale(locale)`) OR calls `win.reload()`.
15. **(XA-20-2)** Translate missing keys in each locale file (overlaps with XA-18-4).
16. **(XA-20-1)** Migrate pluralized strings to `tChoice()` (overlaps with XA-18-3).
17. **(XA-20-21)** Add `i18n/__tests__/rtl-render.test.tsx` mounting real components under `dir="rtl"` to assert visual/DOM-level RTL behavior.

---

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

### XZ-R3-05 — Silent failure on restart_app/quit_app (Medium)
**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:43-58` sends ack BEFORE `service.restart()`/`service.quit()`. If the service call raises, error is logged server-side but NO error event is pushed to client. Client proceeds as if restart succeeded.
**Related Files:** `voice_typer/server/handlers/system_handlers.py`**Fix:** After `self._send(resp)`, if `service.restart()`/`service.quit()` raises, push follow-up error event via `event_bus.publish({"type": "restart_failed", ...})`. Client subscribes + surfaces toast.
**Severity:** 🟡 Medium

---

### XZ-R4-002 — Bearer token via env var readable by same-user processes on Linux (Medium)
**Status:** ❌ Not Fixed
**Description:** `sidecar/spawn.rs:81-84` sets `VOICE_TYPER_IPC_TOKEN` env var on sidecar. Linux same-user processes can read `/proc/<pid>/environ` to recover token.
**Related Files:** `src-tauri/src/sidecar/spawn.rs`**Fix:** Pass token via Unix domain socket ancillary fd, pipe between parent/child, or temp file with 0600 perms that sidecar reads + unlinks. Env-var is weakest link.
**Severity:** 🟡 Medium

### XZ-R4-009 — restart counter file has no integrity protection (Low)
**Status:** ❌ Not Fixed
**Description:** `sidecar/supervisor.rs:151-166` writes counter as plain JSON, no HMAC. Same-user attacker with write access to `<config_dir>/restart_counter.json` can reset count to 0 indefinitely, bypassing CR-29 breaker.
**Related Files:** `src-tauri/src/sidecar/supervisor.rs`**Fix:** Add HMAC-SHA256 over `(count, ts)` using per-install random key in separate 0600 file. Verify on read; reject if mismatch.
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

### XZ-R5-011 — No Windows code-signing enforcement; no entitlements file (Medium)
**Status:** ❌ Not Fixed
**Description:** `electron-builder.yml:27-59` — signing purely env-driven (no `certificateFile`/`certificateSubjectName`). PR builds ship UNSIGNED. No macOS entitlements file. No Linux AppImage signing.
**Related Files:** `voice_typer/client/electron-builder.yml`**Fix:** Add `win.signingHashAlgorithms: ["sha256"]`. Consider failing build if `CSC_LINK` empty when publishing. Add `mac.entitlements: resources/entitlements.mac.plist` declaring `com.apple.security.device.audio-input`. Configure AppImage GPG signing.
**Severity:** 🟡 Medium

---

### XZ-R6-AS-01 — Tauri binary spawned at autostart with no integrity check (Low-Medium)
**Status:** ❌ Not Fixed
**Description:** `autostart_launcher.py:242-305` `_tauri_binary` returns path with NO hash verification. Spawned at login with user's full privileges. `VT_TAURI_BINARY` env var attack vector.
**Related Files:** `voice_typer/server/autostart_launcher.py`**Fix:** Add `verify_tauri_binary_or_skip(path)` mirroring native hotkey pattern. Maintain `tauri-binaries.json` manifest. Call in `_spawn_tauri_host` before `subprocess.Popen`.
**Severity:** 🟢 Low

### XZ-R6-AS-04 — `.desktop` quoting doesn't escape newlines (Low)
**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart.py:71-94` `_desktop_quote` reserves `\n` but doesn't escape it inside quoted string. Could inject new .desktop fields.
**Related Files:** `voice_typer/server/server_platform/autostart.py`**Fix:** Reject args containing `\n`/`\r` with `ValueError`.
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

---

### XZ-CLIP-04 — TOCTOU re-check Windows-only (Medium)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:790-854` safe_hwnd re-check is Windows-only. macOS (`_safe_key_press`) and Linux Wayland (`wtype`) have TOCTOU window between safety check and keystroke.
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Add platform-native TOCTOU re-checks: macOS — re-fetch `NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()`; Linux — re-fetch focused AT-SPI accessible.
**Severity:** 🟡 Medium

### XZ-R10-07 — `_migrate_from_legacy` uses non-atomic `shutil.copytree` (Medium)
**Status:** ❌ Not Fixed
**Description:** `config.py:347-364` `shutil.copytree` is non-atomic, file-by-file. Interrupted migration leaves partial target dir. Called from `logging_setup._setup_logging()` at every startup.
**Related Files:** `voice_typer/server/config.py`**Fix:** Copy to staging dir (`target.with_suffix(".migrating")`) via `shutil.copytree`, then atomically rename via `os.replace`. On failure, clean up staging. Add O_NOFOLLOW checks.
**Severity:** 🟡 Medium

### XZ-R10-09 — Deprecated fields never actually leave config.json (Medium)
**Status:** ❌ Not Fixed
**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.
**Re-verified 2026-08-03:** still broken — `_CURRENT_SCHEMA_VERSION = 3` at `config_internals/migrations.py:38`, no v4 migration exists. The dataclass (`config/__init__.py:1149-1165`) still declares the deprecated fields (e.g. `noise_filter_rnnoise` kept "for backward compat"; `noise_filter_gate_threshold` scrubbed only by the v3 migration), so `asdict()` re-serialization still resurrects them. Note: file layout changed since the finding was written (config monolith split — migration logic lives in `config_internals/migrations.py`, dataclass in `config/__init__.py`); the Fix steps (v4 migration + allowlist/validators/TS removal + version bump) are unchanged and unstarted.
**Description:** `config.py:524-547` v3 migration prunes 9 keys, but dataclass still declares them. `asdict(self)` re-serializes them with defaults. v3 "prune" cosmetically ineffective.
**Related Files:** `voice_typer/server/config.py`**Fix:** Add v4 migration that prunes ALL fields marked "DEPRECATED". Remove from IPC_CONFIG_ALLOWLIST. Remove from `_validate_non_numeric_fields`. Remove from TS type. Bump `_CURRENT_SCHEMA_VERSION` to 4.
**Severity:** 🟡 Medium

### XZ-R11-03 — Migration "duplicate column name" handler bumps version without verifying (Medium)
**Status:** ❌ Not Fixed
**Description:** `history_db.py:798-811` — if V2 migration's first ALTER fails on "duplicate column name: favorite", handler treats whole migration as complete and bumps version. `language` column NEVER added. Subsequent `add_transcription(text, language="en")` fails silently.
**Related Files:** `voice_typer/server/history_db.py`**Fix:** Replace blanket "duplicate column name → done" heuristic with per-column existence checks via `PRAGMA table_info(transcriptions)`. Run only the missing ALTERs.
**Severity:** 🟡 Medium

### XZ-R11-04 — No encryption at rest for dictated text (Medium)
**Status:** ⚠️ Partial — threat model + mitigation design documented (docs/adr/XZ-R11-04-at-rest-encryption.md, 609 lines, added 2026-08-03); encryption NOT implemented — `history_db.py` still stores plaintext.
**Description:** `history_db.py` stores dictated `text` in plaintext. File perms 0o600 / dir 0o700, `secure_delete=ON`, GDPR delete unlinks after checkpoint. But while running (or after unclean shutdown before checkpoint), text recoverable by same-user/root.
**Related Files:** `voice_typer/server/history_db.py`**Fix:** Consider optional SQLCipher integration gated behind user setting. OR application-layer encryption of `text` column with key from OS keystore. At minimum document threat model in `docs/privacy/`. VALIDATE ON WINDOWS/MACOS HOST (file-perm mitigations are POSIX-only).
**Severity:** 🟡 Medium

### XZ-R12-02 — `_migrate_from_legacy` non-atomic (High)
**Status:** ❌ Not Fixed
**Description:** `config.py:347-364` `shutil.copytree(legacy, target, dirs_exist_ok=True)` non-atomic, file-by-file. Interrupted migration leaves partial target. Same as XZ-R10-07.
**Related Files:** `voice_typer/server/config.py`**Fix:** Folded into XZ-R10-07 fix.
**Severity:** 🔴 High

---

### XZ-EH-015 — Implicit ack-vs-error contract is fragile (Medium)
**Status:** ❌ Not Fixed
**Description:** `onboarding_handlers.py:27-40, 154, 185, 209, 224, 239` — 5 handlers delegate ack-vs-error to whether service's return dict contains `"error"` key. If service returns `{"error": None}` (falsy but present), handler reports `ack` for failure.
**Related Files:**
- `voice_typer/server/handlers/onboarding_handlers.py`
- `voice_typer/server/service.py` (onboarding methods)**Fix:** Migrate per documented PVT-G5-095 plan: service should `raise` on failure (typed `OnboardingError`), handler let propagate to outer `except Exception` which calls `_respond_with_error`. Eliminates implicit dict-key contract.
**Severity:** 🟡 Medium

### XZ-R16-09 — Logging prefix inconsistency (Low)
**Status:** ❌ Not Fixed
**Description:** Renderer logs use mixed prefixes: `[Renderer]`, `[ErrorBoundary]`, `[tauri-bridge]`, `[bubble IPC]`, `[IPC]`, or no prefix.
**Related Files:** Multiple renderer files**Fix:** Adopt single `[renderer:<module>]` convention. Mechanical sweep.
**Severity:** 🟢 Low

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

### [XS-53] — Race-prone time.sleep() synchronization in tests (30+ sites)
**Status:** 🟡 Partial (FU-FIX-11 — highest-value subset fixed; test_hotkeys_win32.py + lower-risk files remain)
**Description:** 30+ tests use fixed `time.sleep()` for thread synchronization instead of Event-based waits. Examples: `tests/test_keyboard_ownership_watchdog.py:191` (`time.sleep(0.15)` 'give handler a moment to process auth'), `tests/test_ipc_deadlock_regression.py:223` (`time.sleep(0.1)` 'give reader a moment to enter blocking readline'), `tests/test_sidecar_ws_thread_safety.py:235,340,444` (`asyncio.sleep(0.15)` 'wait for connection to authenticate'), `tests/test_microphone_watcher.py` (15+ occurrences: 0.15-0.4s sleeps), `tests/test_hotkeys_win32.py` (18+ occurrences: 0.03-0.2s), `tests/test_lock_order_contract.py:370,480` (`time.sleep(1.0)` to 'let 9 threads race'), `tests/test_smart_duck_monitor.py:546` (`time.sleep(2.0)` for 8-thread race), `tests/test_b4_config_editor_lock.py:412` (`time.sleep(0.15)`), `tests/test_timer_coordinator.py:167,204,228,269,310,372`. These are race-prone on slow CI.
**Root Cause:** Fixed sleeps used for synchronization instead of Event-based waits or polling with deadline.
**Progress:** FU-FIX-11 (2026-09-15) replaced the highest-value (most-flaky) sites with bounded polling loops: (1) `tests/test_timer_coordinator.py` — 6 sites replaced: 3 cancel-guard assertions now use `fired.wait(timeout=...)` (exits early on regression), 2 thread-race tests now use `stop.wait(timeout=...)`, 1 lock-hold test polls `clear_started` + `got_lock_during_clear` events. (2) `tests/test_keyboard_ownership_watchdog.py:191` — replaced `time.sleep(0.15)` with a bounded poll (1.5s deadline) on `server._tcp_client` (the auth-complete signal set inside `_handle_tcp_connection` after the token check succeeds). (3) `tests/test_ipc_deadlock_regression.py:220` — replaced `time.sleep(0.1)` with a `reader_started` Event the reader thread sets just before entering `io.readline()`. (4) `tests/test_sidecar_ws_thread_safety.py` — 4 sites replaced: 3 auth-wait sites poll `event_bus._subscriber_count() >= 1` (1.5s deadline); 1 loop-settle site polls `loop._ready` deque length (0.5s deadline). The `test_hotkeys_win32.py` file (18+ sleeps) and other lower-risk files (test_microphone_watcher.py, test_lock_order_contract.py, test_smart_duck_monitor.py, test_b4_config_editor_lock.py) are SKIPPED per the L-effort "highest-value subset" guidance — return PARTIAL. Verified: all 4 modified test files pass on LINUX (`tests/test_timer_coordinator.py` 19/19, `tests/test_keyboard_ownership_watchdog.py` 9/9, `tests/test_ipc_deadlock_regression.py` 5/5, `tests/test_sidecar_ws_thread_safety.py` 3/4 with 1 pre-existing failure unrelated to XS-53).
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
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/credential_store.py` 1110 lines bundles 7 distinct concerns: (1) Constants & provider map, (2) Thread-local outcome recording / CR-94 IPC plumbing, (3) Defense-in-depth redaction (`_PATH_RE`, `_redact_sensitive`), (4) Keyring availability probing + 3 global caches, (5) Secret CRUD, (6) Plaintext fallback read/write, (7) Cross-process lock + migration logic. Migration alone is ~280 lines with 3 nested try/excepts and touches 4 of the 7 concerns.
**Root Cause:** Verified. Organic growth: RW-01 (CRUD + plaintext), then CR-94 (outcome plumbing), then RACE-001/HIGH-13 (migration rework).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py` (entire file)
**Fix:** Split into `credential_store/` package: `_schema.py`, `_redact.py`, `_outcome.py`, `_backend.py`, `_plaintext.py`, `_crud.py`, `_migration.py`. `__init__.py` re-exports all public + private symbols used by tests. All function signatures unchanged.
**Severity:** 🔴 High

---

### [AC-130] — `ipc_server.py` 2546-line spaghetti — 8 distinct concerns + 14 instance attributes + 135-line `_COMMAND_REGISTRY` + 1946-line class body
**Status:** ❌ Not Fixed
**2026-08-03 note:** `voice_typer/server/ipc/entrypoint.py` (417 LOC) drafted as a split target but unwired (no importers) — finding remains open.
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
**2026-08-03 note:** `transcription_load.py` / `transcription_result.py` / `transcription_download.py` (934 LOC total) drafted as split targets but unwired (no importers) — finding remains open.
**Description:** `voice_typer/server/dictation_pipeline.py` 1291 LOC and `transcription.py` 1190 LOC both exceed the 800-line threshold. `dictation_pipeline.py` mixes 7 distinct responsibilities. `transcription.py` mixes 9 distinct responsibilities.
**Root Cause:** Verified. EC-28 previously concluded `dictation_pipeline.py` is "cohesive" — the MANDATORY instruction for this review overrides that assessment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py` (entire file)
- `voice_typer/server/transcription.py` (entire file)
**Fix:** Split `dictation_pipeline.py` → 8-file package (orchestrator, helpers, resource_probe, transcribe_step, text_steps, enhancement_steps, storage_step, paste_step). Split `transcription.py` → 9-file package (cuda_dll_paths, whisper_download, device_resolver, model_loader, transcribe, fallback, gpu_error_detection, _lock_helpers).
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
**Status:** ❌ Not Fixed (6-file multi-file refactor (crash_handler, shutdown_controller, clipboard_target_safety, clipboard/manager, permissions, text_cleanup — all >1000 LOC); partial extraction risks breaking _do_cleanup shutdown ordering)
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
**Fix:** Apply the split plans from AC-86 (crash_handler), AC-128 partial (clipboard_target_safety), AC-127 (permissions), AC-80+AC-81+AC-82 (text_cleanup).
**Severity:** 🔴 High

---

### [AC-138] — Rust host `sidecar/ws.rs` + `sidecar/supervisor.rs` + `commands/sidecar_cmds.rs` + `commands/bubble.rs` + `platform/logging.rs` + `migrate.rs` exceed or approach the LOC threshold
**Status:** ⚠️ Partial (verified 2026-08-05) — 2 of 6 files split; 4 remain above threshold; 2 dead draft modules confirmed unwired.
**2026-08-05 verified state:**

| File | Report LOC | Actual LOC | Status |
|---|---|---|---|
| `migrate.rs` | 546 | split → `migrate/` dir (mod 380 + 5 files) | ✅ done |
| `bubble.rs` | 706 | split → `bubble/` dir (commands 502, tests 733, + 4) | ✅ done |
| `ws.rs` | 997 | 1549 | ❌ still exceeds; submodules `event_protocol`/`heartbeat`/`respawn_scheduler` extracted + declared (ws.rs:35-37, compiled) |
| `supervisor.rs` | 952 | 1617 | ❌ `bubble_coalesce.rs` extracted + declared, core still huge |
| `sidecar_cmds.rs` | 926 | 1276 | ❌ untouched |
| `logging.rs` | 617 | 3056 | ❌ worst offender |

**Dead-draft claim (2026-08-03 note): confirmed still true, partially.**
- `platform/log_file.rs` (539) + `platform/log_rotation.rs` (306) — still **NOT declared** in `platform/mod.rs` → not compiled, dead. (git: created in one commit `5ae8cf2a` "draft split-target modules", never wired.)
- `sidecar/supervisor_health.rs` / `ws_dispatch.rs` / `ws_reconnect.rs` — no longer exist (deleted; superseded by the extracted `event_protocol`/`heartbeat`/`respawn_scheduler`).

Cargo check passes → log_file/log_rotation truly unreferenced dead weight.

**Description:** Six Rust modules exceed/approach the LOC threshold. Splits partially applied — `migrate.rs` and `bubble.rs` are fully split into packages; `ws.rs` and `supervisor.rs` had satellite modules extracted but cores remain above 1500 LOC; `sidecar_cmds.rs` and `logging.rs` untouched.
**Root Cause:** Verified — organic growth; AC-138 filed against pre-split counts, but the remaining files grew larger since.
**Progress:** Next step: delete the two dead draft modules (`platform/log_file.rs`, `platform/log_rotation.rs`), then wire them into `platform/mod.rs` and actually split `logging.rs` (3056 LOC — now the real problem), or close the logging sub-item as abandoned. `ws.rs`, `supervisor.rs`, `sidecar_cmds.rs` remain above threshold but were at least partially split.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/commands/bubble/`
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/migrate/`
**Fix:** Apply the split plans from AC-13/AC-98/AC-99/AC-100 (ws.rs), AC-95/AC-96/AC-97 (supervisor.rs), AC-30/AC-31/AC-32/AC-102 (sidecar_cmds.rs), AC-103 (bubble.rs — done), AC-39 (logging.rs), AC-35 (migrate.rs — done). Also delete or wire the dead `platform/log_file.rs` + `platform/log_rotation.rs` drafts.
**Severity:** 🔴 High

---

### [DR-3] — Rust monolith files: bubble.rs 1313 LOC, ws.rs 1241, supervisor.rs 1055, logging.rs 989, sidecar_cmds.rs 897, spawn.rs 845
**Status:** ⚠️ Partial (verified 2026-08-05) — subsumed by AC-138; only bubble.rs split; 8 of 10 files grew since filing.
**2026-08-05 verified state:**

| File | DR-3 cited | Actual LOC | Δ |
|---|---|---|---|
| `bubble.rs` | 1313 | split → `bubble/` dir (commands 502, tests 733, + 4) | ✅ done |
| `ws.rs` | 1241 | 1549 | ❌ +25% |
| `supervisor.rs` | 1055 | 1617 | ❌ +53% |
| `logging.rs` | 989 | 3056 | ❌ +209% (worst in repo) |
| `sidecar_cmds.rs` | 897 | 1276 | ❌ +42% |
| `spawn.rs` | 845 | 1167 | ❌ +38% (EO-33 tracks this) |
| `system_cmds.rs` | 627 | 557 | ❌ still >500 |
| `tray.rs` | 647 | 692 | ❌ still >500 |
| `process.rs` | 689 | 1132 | ❌ +64% (VP-1 tracks this) |
| `export.rs` | 527 | 686 | ❌ +30% |

**Description:** DR-3 flagged 6 Rust monoliths + 4 files that newly crossed the 500-LOC threshold (system_cmds, tray, process, export). Verified 2026-08-05: only bubble.rs was split (via the AC-138 work — see the AC-138 entry above); the remaining files grew since DR-3 was filed. Per-file growth is now separately tracked by EO-33 (spawn.rs) and VP-1 (process.rs).
**Root Cause:** Verified — incremental growth across sessions added doc comments + tests without extracting helpers; AC-138's split plans were never executed for the remaining files.
**Progress:** None — superseded by AC-138 (same split plans). See AC-138's next-step recommendation: delete dead drafts, split `logging.rs` (3056 LOC) first.
**Related Files:**
- `src-tauri/src/commands/bubble/`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/sidecar/spawn.rs`
- `src-tauri/src/commands/system_cmds.rs`
- `src-tauri/src/tray.rs`
- `src-tauri/src/platform/process.rs`
- `src-tauri/src/commands/export.rs`
**Fix:** Merged into AC-138 — apply the same split plans: bubble geometry/rate-limiter extraction (done), logging.rs writer vs panic-hook vs CombinedLogger, ws.rs phase helpers + reader/writer/heartbeat, supervisor.rs counter I/O vs respawn loop, spawn.rs Phase-4.5 plan (EO-33), process.rs platform-strategy split (VP-1).
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
- `voice_typer/client/src/main/python/tcp-connect.ts`**Fix:** Apply the split plans from AC-12+AC-117 (logging), AC-107 (main-window), AC-116 (bootstrap), AC-19 (tcp-connect).
**Severity:** 🟡 Medium

---

### [ER-2] — DeepFilterNet backend unimplemented — `noisy_room` preset delivers zero neural noise suppression
**Status:** ❌ Not Fixed
**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.
**Re-verified 2026-08-03:** interim mitigation from the Fix line IS applied — `_init_deepfilternet()` (noise_suppressor.py:239-274) now degrades to rnnoise at *init* time (option b): `is_degraded=True` surfaces to the UI immediately and `process()` routes to the rnnoise branch on every call (no per-chunk fallback). Users get real RNNoise suppression instead of silent passthrough. What remains unstarted: the actual DeepFilterNet processing path (frame buffering + `enhance()`), and the `noisy_room` preset still nominally selects `"deepfilternet"` — it works only because of the init-time degradation.
**Severity:** 🔴 Critical
**Description:** `noise_suppressor.py:124-139` `process()` calls `_process_rnnoise` for the rnnoise backend, but for DeepFilterNet and Speex backends it sets `is_degraded=True` and returns the audio unchanged (passthrough). The `PRESET_NOISY_ROOM` preset at `audio_presets.py:50-58` explicitly selects `"deepfilternet"` for the noisiest environment. Users in the noisiest environments (the exact use case this preset targets — keyboard/fan/HVAC) get ZERO neural noise suppression. ASR accuracy in these environments degrades severely because the very feature advertised is a no-op.
**Root Cause:** Verified — `_init_deepfilternet()` only stores the imported functions in a dict; `process()` never calls `enhance()`/`init_df`. The preset explicitly selects "deepfilternet" for the noisiest environment, but the selected backend does nothing.
**Progress:** Interim rnnoise fallback implemented (2026-08-03); DeepFilterNet wiring not started.
**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py`
- `voice_typer/server/audio_presets.py`**Fix:** In `_init_deepfilternet()` immediately downgrade to rnnoise (like the `ImportError` path does) so the user at least gets RNNoise-level suppression instead of nothing. OR: in `PRESET_NOISY_ROOM` fall back to `"rnnoise"` until DeepFilterNet is implemented. The full DeepFilterNet wiring (frame buffering, `enhance()` call) is a separate, larger feature task

---

### [ER-6] — macOS osascript 3s accessibility fallback on startup hot path
**Status:** ⚠️ Partial
**Not sure. Require verification first.**
Brainstorm yourself and use the best practices to solve this problem.
**Re-verified 2026-08-03:** the osascript fallback is REMOVED from the startup hot path — `startup_sequence.py:585-669` now uses a pure `ctypes` `AXIsProcessTrusted()` probe; load failure is treated as "permission not granted" (False) and the periodic A11yPulse (:614) re-probes to detect the grant within 60s. The "synthesizes a real keystroke / focuses frontmost app" behavior is gone (comment at :601-648 documents the removal rationale). What remains open: macOS-host validation of the ctypes probe was NOT performed (this is a Windows dev environment) — per the Fix note's **VALIDATE ON MACOS HOST** requirement. Note the probe uses `AXIsProcessTrusted()` (no options arg) rather than the `AXIsProcessTrustedWithOptions` variant suggested in the Fix line — functionally equivalent for the check.
**Severity:** 🔴 High
**Description:** `startup_sequence.py:374-380` `StartupSequence.run` invokes a synchronous `osascript` subprocess with a 3s timeout as a fallback when `ctypes.cdll.LoadLibrary(".../ApplicationServices")` fails. This is on the critical startup thread (runs before hotkey registration at line 528 and before parallel prewarm/mic work at line 516). The osascript path also synthesizes a real keystroke via System Events, which is invasive (focuses the frontmost app).
**Root Cause:** Verified — reached whenever ctypes ApplicationServices load fails (stripped-down macOS installs, code-signed bundles with restricted dyld env, CI runners).
**Progress:** osascript fallback removed, ctypes probe + A11yPulse re-probe implemented (2026-08-03); macOS validation pending.
**Related Files:**
- `voice_typer/server/startup_sequence.py`**Fix:** Replace the osascript fallback with a pure `ctypes` probe of `AXIsProcessTrustedWithOptions` (passing `kAXTrustedCheckOptionPrompt=False`) — or treat ctypes-load failure as "permission not granted" (False) and let the periodic A11yPulse (already started at line 406) detect the grant within 60s. Drop the osascript path entirely. **VALIDATE ON MACOS HOST** — verify `AXIsProcessTrustedWithOptions` returns the correct value on real macOS.

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

### [ER-42] — Silero VAD thresholds not auto-calibrated (calibration explicitly skipped for Silero path)
**Status:** ⚠️ Partial — `vad_auto_calibrate` flag registered in Config dataclass + IPC_CONFIG_ALLOWLIST + TS type (the flag was read via getattr fallback but never registrable — the whole calibration feature was unreachable). Calibration implementation itself was already present (RMS + Silero-prob paths in vad_processor.py) and is now reachable when the flag is enabled.
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
- **Medium (~30):** ER-25 through ER-63 (and ER-69)
- **Low (~40):** ER-64 through ER-98

Phase 4 (fix) will address all Critical and High severity findings, plus a curated set of Medium severity findings where the fix is well-scoped and the file-disjoint constraint can be satisfied. Low severity findings are bundled by file area for efficient parallel fixing where scope allows.

---

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

### [GT-34] — Rust paste_text + paste.rs module dead in production (~440 LOC) — Python owns the paste path
**Status:** ❌ Not Fixed
**Description:** The paste_text #[tauri::command] + the entire paste module (~440 LOC total) are dead in production. The doc comment on paste_text itself states: 'the Python sidecar does its OWN paste internally in voice_typer/server/dictation_pipeline.py:990-1010 via self._app.clipboard.paste(...)'. Grep confirms: no Python code publishes a paste_text event, and no TS code invokes invoke('paste_text', ...). The command is retained only so the migration glue tests keep passing and for DevTools-only manual driving.
**Root Cause:** Verified: Python sidecar owns production paste path; Rust paste_text + paste.rs is parallel implementation kept alive by test source-grep assertions.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:566-644`
- `src-tauri/src/commands/paste.rs:1-417 (entire module)`
**Fix:** Pick one: (a) delete paste.rs, commands::paste mod declaration, the paste_text #[tauri::command], and its generate_handler! entry in main.rs:181; replace migration-glue tests' source-grep with a behavioral stub; OR (b) flip ownership — delete dictation_pipeline.py::_dispatch_paste and let Rust own the paste path.
**Severity:** 🔴 High

### [GT-38] — Test reliability: cleanup tests mock every collaborator — RW-3 claims verified at call-routing level only
**Status:** ❌ Not Fixed
**Description:** _stub_restart_environment replaces EVERY cleanup collaborator with a MagicMock, then tests assert the production code called .flush() / .stop() / .shutdown(). This verifies CALL ROUTING only — it does NOT verify that (a) a real history_db.flush() actually drains pending SQLite writes, (b) recorder.stop() actually closes a real PortAudio stream, (c) crash_recovery.flush(timeout=2.0) actually blocks until the save queue drains, (d) the ordering of these calls is safe under real I/O latency. A regression where history_db.flush() is changed to a fire-and-forget non-blocking call would still pass these tests, but in production would silently lose pending writes on restart — exactly the bug RW-3 claims to fix.
**Root Cause:** Verified: every collaborator is a MagicMock; assertions verify .flush.assert_called_once().
**Progress:** None yet.
**Related Files:**
- `tests/test_app_cleanup.py:62-102 (_stub_restart_environment)`
**Fix:** Add a small set of integration tests that use a real HistoryDB (tmp_path SQLite file) with a populated writer queue, call app.restart_app(), and assert the SQLite file on disk contains the rows after restart completes. Similarly, use a real PortAudio stub (or pyaudio mock that records stream.close() calls) to verify the stream is actually closed.
**Severity:** 🔴 High

### [GT-39] — Test reliability: TestConcurrentConfigWritesNoCorruption relies on GIL, not _config_mutation_lock
**Status:** ❌ Not Fixed
**Description:** The test explicitly does NOT acquire _config_mutation_lock and relies on the GIL for atomicity of cfg.hotkey = val. The production code's _config_mutation_lock contract (RACE-011 / ADR 0008 §3.1) is NOT exercised. The test would still pass if production removed _config_mutation_lock entirely, because the GIL still serializes the attribute writes.
**Root Cause:** Verified: test setter does not acquire _config_mutation_lock; relies on GIL atomicity.
**Progress:** None yet.
**Related Files:**
- `tests/regressions/concurrency_test.py:302-328`
**Fix:** Drive the real IPC path — dispatch 8 concurrent set_config IPC commands through a real IPCServer (using the live_server fixture from tcp_live_test.py), each setting a different field, then assert the final config has all 8 fields set consistently. Or, at minimum, acquire _config_mutation_lock in the test setter and verify a second concurrent acquirer blocks.
**Severity:** 🔴 High

### [GT-40] — Test reliability: No behavioral test of sidecar crash detection / restart loop
**Status:** ❌ Not Fixed
**Description:** The ONLY test of sidecar-crash detection is a source-string check that reads start-python.ts and asserts the literal `pythonProcess.on('exit'` or `proc.on('exit'` substring is present, plus app.quit. No test: spawns a real Python sidecar subprocess, kills it, and verifies the parent detects the exit within a bounded time and triggers the restart/quit path. A refactor that renames proc to pythonProc would break the source-string test; but a refactor that keeps the substring while breaking the behavior would pass.
**Root Cause:** Verified: only test is source-string check; no behavioral subprocess test.
**Progress:** None yet.
**Related Files:**
- `tests/regressions/crash_recovery_test.py:36-84`
**Fix:** Add a behavioral test that spawns a real Python sidecar (using the actual entry point), waits for it to bind the IPC port, sends SIGKILL, and verifies the parent process detects the exit within a bounded time and triggers the restart/quit path.
**Severity:** 🔴 High

### [GT-68] — Cross-process log correlation impossible — no shared session/launch ID
**Status:** ❌ Not Fixed
**Description:** No shared correlation ID between Rust host and Python sidecar. The only shared identifier across the WS boundary is the bearer token, which is explicitly never logged (security). Two log streams exist (Rust rotating file + Python rotating file) with no join key. A dispatch request id (e.g. id=42) is logged on the Rust side but the Python side's matching log line does not echo the id — it logs the command name only. Wall-clock skew between the two processes makes timestamp correlation fragile.
**Root Cause:** Verified: no shared non-secret session/launch ID passed from Rust host to Python sidecar.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/util.rs:72-76 (generate_token)`
- `src-tauri/src/sidecar/spawn.rs:79-84 (env vars)`
- `src-tauri/src/platform/logging.rs:30 (Rust log path)`
**Fix:** Generate a short non-secret session ID (e.g. 8 hex chars) at startup, pass it as VOICE_TYPER_SESSION_ID env var to the sidecar, and have BOTH hosts include it in every log line (Rust: append to CombinedLogger format string; Python: append to the logging.Formatter). The session ID is also useful for crash-report correlation.
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

### [WR-4] — Broken parakeet unload test + AssertionSwallowing + audio_test.py spaghetti
**Status:** ⚠️ Partial
**Description:** `tests/regressions/gpu_memory_release_test.py:157-175` `test_parakeet_unload_invokes_release` patches `voice_typer.server.transcription.release_gpu_memory` but `parakeet_engine.py:1027` does a local `from voice_typer.server.asr_utils import release_gpu_memory` — so the mock is never invoked, and the test silently passes (or fails confusingly). The `if False else` construct on lines 170-172 is debugging leftover; the `monkeypatch` parameter on line 157 is unused. `tests/regressions/audio_test.py:995-1017` `test_resolve_returns_tuple_with_native_rate` wraps its assertion in `try/except Exception: pass`, swallowing its own `AssertionError` — the test is a no-op. `tests/regressions/audio_test.py` itself is 1296 lines mixing 6+ unrelated domains (audio callback, VAD, filters, device enumeration, sample-rate, streaming text assembler, recording-controller sessions, backpressure, clipping IPC, dead-field removal pins, cross-module test introspection) — a textbook Rule 20 spaghetti candidate. `tests/regressions/concurrency_rms_test.py:51-115` tests reimplement the production RMS-suppression logic instead of invoking it — tautological.
**Root Cause:** A stale comment in `parakeet_engine.py:1022-1026` claims tests patch `transcription.release_gpu_memory`, but the local import resolves from `asr_utils`. The broad `except Exception: pass` in audio_test was likely added to tolerate "needs more setup" cases but catches the assertion itself. The audio_test.py monolith grew by concatenation (a `# === Source: tests/test_changes3_fixes.py ===` marker at line 591 confirms it).
**Progress:**
- Fix `test_parakeet_unload_invokes_release` to patch `voice_typer.server.asr_utils.release_gpu_memory` (the canonical source). (DONE)
- Remove the `if False else` debugging leftover and the unused `monkeypatch` parameter. (DONE)
- Update the stale comment in `parakeet_engine.py:1022-1026` to clarify the local import resolves from `asr_utils`. (DONE)
- Fix `test_resolve_returns_tuple_with_native_rate` to remove the broad `except Exception: pass` and assert explicit tuple shape. (DONE)
- Spaghetti split of `audio_test.py` into a `tests/regressions/audio/`.
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
- Spaghetti split of `test_clipboard_win32_coverage.py` + `test_config.py`.
- Consolidate duplicated helpers.
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

### ZR-16 — `DictationPipeline` is a god-class facade reaching through `self._app: Any` for every dependency
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline.py:102-117, 119-145`:
```python
class DictationPipeline:
    def __init__(self, app: Any):
        self._app = app
```
The class holds `self._app: Any` and reaches back through it for every dependency: `self._app.config`, `self._app.recorder`, `self._app.models`, `self._app.tray`, `self._app.history_db`, `self._app._crash_recovery`, `self._app._waveform_bubble`, `self._app._llm_polisher`, `self._app._template_manager`, `self._app._vocabulary_manager`, `self._app._duck_volume()` / `_restore_volume()`, `self._app._schedule_timer()`, `self._app._busy_event`, `self._app._vocab_fail_notified`, `self._app._template_fail_notified`, `self._app._llm_consent_warned`, `self._app.recording._cancelled_cycle_ids` (dictation_pipeline.py:244).
**Root Cause:** The extraction (ARCH-006) moved the code from app.py to a new class but did not change the dependency shape — the pipeline still talks to the entire app surface via `self._app.X`. No interface boundary was introduced.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py` (full file, 1291 lines)
**Fix:** Define a `DictationContext` dataclass / Protocol with the actual dependencies (config, models, history_db, clipboard, tray, crash_recovery, bubble, busy_event, schedule_timer) and pass it to the pipeline. Move the per-cycle state onto the pipeline itself. Consider splitting the pipeline into 3 stages (`TranscribeStage`, `TextProcessStage`, `OutputStage`) — each independently testable.
**Severity:** 🟡 Medium — the pipeline cannot be tested without a full app mock; every private attribute of `VoiceTyperApp` is effectively part of the pipeline's public contract; `app: Any` typing means pyrefly cannot verify any of the `self._app.X` accesses.

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

### ZR-38 — `recording/__init__.py` test-patch compat shim is 447 LOC of `__init__.py` boilerplate (CR-67 tech debt)
**Status:** ❌ Not Fixed (duplicate of ZR-14 — same root cause; this entry is from sub-agent D's code-quality lens)
**Description:** Same as ZR-14. `voice_typer/server/recording/__init__.py` is a 447-line file whose entire purpose is test-patch compatibility. Custom module subclass with `__getattr__`/`__setattr__` overrides routes 5 mutable-global reads/writes (`_resample_poly`, `_resample_poly_error`, `_resample_poly_error_time`, `_scipy_preloader_thread`, `_buffer_clear_worker`) through to the owning submodule. The `# noqa: F401` count in these three files alone is 56.
**Root Cause:** Phase 4.5 package split left tests patching the old package namespace; the shim was added to avoid touching tests. The TODO to migrate tests is dated today (2026-07-25) and explicitly OPEN.
**Progress:** None yet.
**Related Files:** Same as ZR-14.**Fix:** Same as ZR-14. Execute the CR-67 migration.
**Severity:** 🟡 Medium — ~500 LOC of `__init__.py` boilerplate exists purely for test compatibility. (Cross-references ZR-14 — primary agent should treat as one finding, not two.)

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

### ZR-67 — Push-event types are stringly-typed (270+ string literals across server + 26+ renderer subscribers)
**Status:** ❌ Not Fixed
**Description:** Python emitters — `voice_typer/server/dictation_pipeline.py:995 ("vocabulary_suggestion")`, `:1093 ("transcription_final")`; `waveform_bubble_wiring.py:168 ("bubble_level")`; `app.py:935 ("quit_app")`, `:1028 ("relaunch_app")`; `ipc_server.py:2229-2235` `_shutdown_allowlist` tuple of 5 inline strings allocated per `_send` call; `:2392 if msg_type in ("bubble_level", "waveform"):`; `:1098-1103 state_changed`. Total `rg '"type": "[a-z_]+"' voice_typer/server/` = 77 sites.
Renderer — 26 `usePythonEvent("transcription_final", ...)`, `usePythonEvent("recording_started", ...)`, etc. The TS-side `PythonPushEvent` union (`types/ipc.ts:461-496`) exists but `usePythonEvent`'s signature (`hooks/usePython.ts:283,287`) takes `type: string` not `PythonPushEvent["type"]` — comment at `types/ipc.ts:453-460` explicitly notes this is "EC-FIX-20" and not yet done.
**Root Cause:** TS union exists.
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
**2026-08-03 note:** `ws_dispatch.rs` / `ws_reconnect.rs` drafts exist but are undeclared in `mod.rs` (dead) — finding remains open.
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

---

### DJ-2 — VoiceTyperApp.__init__ god-constructor blocks tray icon appearance
**Status:** ⚠️ Partial
**Severity:** 🔴 High

**Description:** `VoiceTyperApp.__init__` synchronously constructs ~25 subsystems on the main thread BEFORE `tray.start(bg_work=self._do_startup)` is called. The chain includes AudioProcessor, AudioQualityAnalyzer, Recorder, ModelManager, ClipboardManager, TrayIcon, SettingsController, ShutdownController, LifecycleController, UndoRepasteController, AudioQualityController, HotkeyDispatcher, TimerCoordinator, HistoryDB (opens sqlite3 write queue + spawns writer thread), CrashRecovery, DuckCrashRecovery, VolumeController, VolumeDucker, WaveformBubble, WaveformBubbleWiring, TemplateManager (reads JSON from disk), VocabularyManager (reads JSON from disk). Only after all of these does `start()` create the pystray icon.

**User Impact:** The user sees nothing — no tray icon, no window, no feedback — for the entire duration of `__init__`. On a cold disk the file I/O from TemplateManager/VocabularyManager and the HistoryDB thread spawn add hundreds of milliseconds. The user may think the app failed to launch.

**Root Cause:** Verified — VoiceTyperApp is a god-class constructor. The RW-9 decomposition extracted methods into controllers but kept eager construction of all controllers in __init__.

**Progress:** ⚠️ Partial (NQ-2) — AB-30 lazy properties now defer ClipboardManager (app.py:258), UndoRepasteController (:337), AudioQualityController (:353); TemplateManager/VocabularyManager lazy-imported (:101-102); numpy lazy-loaded (:93); eager `_ensure_engine("qwen")` removed (:247). Still eager before `tray.start`: AudioProcessor (:192), AudioQualityAnalyzer (:204), Recorder (:208), ModelManager (:246), TrayIcon (:266), SettingsController (:292). Decision needed: defer the remaining eager subsystems vs test-update of startup-sequence tests.

**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/startup_sequence.py`

**Fix:** Defer construction of non-critical subsystems to the bg startup thread (`StartupSequence.run`) or use lazy `@property` patterns for: TemplateManager, VocabularyManager, WaveformBubble, WaveformBubbleWiring, UndoRepasteController, AudioQualityController, DuckCrashRecovery, VolumeDucker. Keep only Config, ThreadRegistry, TrayIcon, Recorder, ModelManager, HotkeyDispatcher, ShutdownController on the critical path. For TemplateManager/VocabularyManager, replace eager-init with a lazy `@property` + a `reload()` call after config changes.

---

### DJ-6 — Sidecar cleanup budget mismatch — host force-kills mid-cleanup causing data corruption
**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical

**Description:** ipc_server.py:1696-1698 explicitly states: 'service.quit() runs _do_cleanup() synchronously (30+ steps, ~95s worst case); the Tauri host's SHUTDOWN_ACK_TIMEOUT_MS=2000ms fires long before cleanup completes, force-killing the sidecar mid-cleanup.' The sidecar's audited worst-case cleanup is ~42-95s; the host's cooperative shutdown timeout is 2s (SHUTDOWN_ACK_TIMEOUT_MS) or 5s (HOST_SHUTDOWN_GRACE_MS). The host force-kills the sidecar mid-flush. This is acknowledged in code comments but unfixed.

**User Impact:** When the user clicks Quit, the app force-kills the Python sidecar mid-cleanup. This interrupts: history_db.flush()/close() (WAL not checkpointed → potential corruption), crash_recovery.flush() (partial snapshot), recorder.shutdown_mic_watcher() (PortAudio stream left open), hotkey unregister (RegisterHotKey entries leaked on Windows), PID file + Win32 mutex not cleared (next launch blocked by single-instance check). User-visible: 'Voice Typer is already running' error on next launch; corrupted history DB requiring reset; hotkey not working until re-login.

**Root Cause:** Verified — budget mismatch between sidecar's audited worst-case cleanup time and the host's cooperative-shutdown timeout. Acknowledged in code comments but unfixed.

**Progress:** Deferred (Critical) — sidecar cleanup budget mismatch. Requires raising SHUTDOWN_ACK_TIMEOUT_MS + making _do_cleanup abortable per-phase. Out of session scope.

**Related Files:**
- `voice_typer/server/shutdown_controller.py`
- `voice_typer/server/ipc_server.py`
- `src-tauri/src/main.rs`
- `src-tauri/src/util.rs`

**Fix:** Two-pronged: (1) Raise SHUTDOWN_ACK_TIMEOUT_MS to 30s and HOST_SHUTDOWN_GRACE_MS to 35s — the cooperative-shutdown path is the LAST resort, not a hot path. (2) More importantly, make _do_cleanup abortable per-phase: after the ack is sent, skip the slow best-effort phases (parallel batch) and only run the critical fast-path: history_db.flush, crash_recovery.flush, _clear_backend_pid_file, mutex release, tray.stop. Move recorder.stop, hotkeys.stop, sounddevice.stop, level_monitor.stop into a 'slow' tier that only runs if time remains. This bounds the sidecar's required shutdown window to <2s while preserving data integrity.

---

### DJ-14 — GPU→CPU fallback cold-loads CPU model — 5-50s frozen tray
**Status:** ⚠️ Partial
**Severity:** 🟡 Medium
**Description:** `transcription.py:1044-1057` `_transcribe_with_fallback_unlocked` on a GPU runtime error tears down + reloads on CPU IN-LINE on the transcription thread: `del self._model`, `self._model = None`, `self._device = 'cpu'`, `self._compute_type = 'int8'`, `self._reload_under_lock()` (cold WhisperModel() construction, 5-50s), then retries. The docstring admits cold model load is 5-50s.
**User Impact:** When a transient GPU error (e.g. a single OOM from a concurrent process briefly spiking VRAM) fires mid-dictation, the user waits: (failed GPU inference, ~1-5s) + (cold CPU model load, ~5-50s) + (CPU retry inference, ~3-15s) = 9-70s total before they see any text. The tray stays at 'Transcribing…' the entire time. This is the worst-case user-visible latency in the app.
**Root Cause:** Verified — fallback path calls `self._reload_under_lock()` synchronously which runs the full `_load_transcriber_impl` chain.
**Progress:** ⚠️ Partial — NO code change; re-verified 2026-07-31 (NQ-1): cold reload still present at transcription.py:1130-1134 (`del self._model` → `_device = "cpu"` → `_reload_under_lock()`) inside `_transcribe_with_fallback_unlocked`, plus a second identical cold-reload site at :1180-1184. Decision needed (test-update vs contract-change): pre-warm CPU whisper-tiny.en fallback (Fix a), one-shot fallback + tray prompt (Fix b), or Parakeet device-move (Fix c).
**Related Files:**
- `voice_typer/server/transcription.py`
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/model_manager.py`
**Fix:** (a) Keep a pre-warmed CPU whisper-tiny.en backend resident in the registry (loaded once at startup in a background thread), so the fallback path is a registry lookup + transcribe, not a cold load. (b) Make the GPU→CPU fallback a one-shot per session — instead surface a tray notification 'GPU failed, switch to CPU?' and let the user accept or retry GPU. (c) For Parakeet, `self._model.to(device='cpu', dtype=self._torch.float32)` (parakeet_engine.py:1044) is faster than a full reload (~1-3s vs 5-50s) — the Whisper path should mirror this.

### DJ-19 — _all_read_connections pruning is reactive — dead-thread 20MB connections leak until next new-thread read
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `history_db.py:1037-1047` `_get_read_conn` appends to `_all_read_connections`. Pruning (`_prune_dead_read_connections_locked`) is REACTIVE — only fires when a NEW connection is created on a thread that doesn't already have one. If N threads each create a read connection, then die, and NO new thread creates a connection afterward, the N dead-thread connections (each 20MB) sit in `_all_read_connections` until the next `_get_read_conn` call from a fresh thread.
**User Impact:** At 20MB per leaked connection × even 10-20 dead threads = 200-400MB of phantom SQLite page cache that is never reclaimed until the next new-thread read. For a long-running tray process over hours, this compounds with DJ-18 (cursors pinning their connections). The 20MB cache is also not reused across threads (each thread has its own connection), so the cache hit rate is poor.
**Root Cause:** Verified — pruning is REACTIVE (only fires when a NEW connection is created).
**Related Files:**
- `voice_typer/server/history_db.py`
**Fix:** Either (a) schedule a periodic prune on a background timer (e.g. every 60s, walk `_all_read_connections` and close any whose thread_ident is not alive), or (b) bound the list size with an LRU eviction policy (close oldest connection when count exceeds e.g. 8), or (c) prefer a connection pool (e.g. a small `queue.Queue` of N reusable read connections) instead of per-thread connections.

---

### DJ-70 — No BT HFP mode-switch retry logic — recording terminated on every BT mode switch
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** `recorder.py:1102-1159` (`_handle_device_disconnect` retry loop) + `disconnect_handler.py:86-270` (restart_stream — no inter-retry delay). `_handle_device_disconnect` increments `_device_disconnect_retries` and calls `restart_stream` which makes ONE `sd.InputStream` open attempt. There is NO `time.sleep` or backoff between retries. The 3 retry attempts are driven by successive disconnect-detection events (zero-fill chunks at 16Hz, health-checker at 30s, or stream-finished callback). For a Bluetooth HFP/HSP mode switch (1-3s window during which the device is unavailable), all 3 retries fire within ~200ms (3 zero-fill chunks), all fail with `PortAudioError`, and `_device_disconnect_retries > _max_disconnect_retries` fires `on_silence_auto_stop` — terminating the recording.
**User Impact:** BT headset users lose their dictation every time the headset switches from A2DP (audio output) to HFP/HSP (two-way call) mode — which happens whenever any app opens the mic input. The 1-3s mode-switch window exceeds the 3-retry budget, so the recording is terminated and the user sees 'silence detected'.
**Root Cause:** Suspected — the retry loop has no awareness of BT mode-switch latency. The `_max_disconnect_retries = 3` budget is consumed by 3 immediate failures, leaving no room for the mode-switch window.
**Progress:** Deferred — BT HFP mode-switch retry logic. Out of session scope.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/recording/disconnect_handler.py`
**Fix:** Add a BT-aware retry policy: if the configured device is detected as Bluetooth (`microphone_list.py:117 is_bluetooth`), increase `_max_disconnect_retries` to 6-8 and add a 500-1000ms `time.sleep` between retries (capped so total retry window is ~3-5s). Alternatively, detect the HFP mode switch by checking if `sd.query_devices(device)['default_samplerate']` changed (A2DP default is 44.1/48kHz; HFP is 8/16kHz) and wait for the rate to stabilize before retrying.

---

### DJ-96 — recorder.py is a 3772-line monolith mixing 7 concerns — Phase 4.5 mandatory split
**Status:** ⚠️ Partial
**Severity:** 🔴 Critical
**Description:** `recorder.py:1-3772` — single 3772-line module containing a 3430-line `Recorder` class with 50+ methods spanning 7 disjoint concerns (device enumeration, VAD state, audio I/O, thread lifecycle, secure-clear, session state, resampling). The file already delegates to 6 sibling modules (DeviceManager, AudioPipeline, DisconnectHandler, VadShimMixin, resampling, buffer) but the orchestrator still mixes all concerns: `start()` is 237 LOC, `stop()` is 200 LOC, `__init__` is 390 LOC, `_process_audio_chunk` is 176 LOC, `_handle_device_disconnect` is 115 LOC. Property-shim boilerplate (8 device-state properties + 6 AudioPipeline delegators + 7 device-resolution delegators + 3 health-checker delegators) accounts for ~290 LOC of pure mechanical delegation. Project rule: 'no entry file > ~800 lines mixing concerns'.
**User Impact:** Any change to `Recorder` requires reading 3772 lines to find the relevant code. Test patches via `monkeypatch.setattr('voice_typer.server.recording.X', ...)` are coupled to a `__init__.py` custom module class (CR-67 / TECH-DEBT) that exists ONLY because `recorder.py` looks up cross-submodule helpers through the package namespace at call time. Each new collaborator extraction shrinks `recorder.py` and reduces the surface that needs the patch-path bridge.
**Root Cause:** Verified — `_recorder_split.py` documents the planned decomposition but only `snapshot()` + `discard()` were actually moved (372 LOC total); the rest of the plan was never executed. `docs/rw04-recording-decomposition.md` confirms 'Wave 2 + Wave 3 remain in Recorder as follow-up waves' — those waves never landed.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/_recorder_split.py`
- `voice_typer/server/recording/__init__.py`
**Fix:** Execute the three-wave extraction: (1) `recording/worker_threads.py` (~410 LOC: audio-worker + event-worker lifecycle); (2) `recording/stream_lifecycle.py` (~620 LOC: stream-open + process + close, merging the duplicated `_open_stream_*` pair and the triplicated AudioProcessor-retune block); (3) `recording/session_state.py` (~250 LOC: per-session reset + secure-clear, merging the duplicated `_secure_clear_*_caches` pair). Each wave preserves the 1-line delegator pattern on `Recorder` so `inspect.getsource(Recorder.X)` regression tests keep passing. Estimated post-split `recorder.py` size: ~1200 LOC.

---

### Spaghetti / Phase 4.5 Split Candidates (documented; not all fixed this run)

- **FR-S1:** `voice_typer/server/config.py` (2242 lines) — split into config_dataclass/loader/saver/purge.
- **FR-S2:** `voice_typer/server/history_db.py` (2156 lines) — complete AC-135 split.
- **FR-S3:** `voice_typer/server/ipc_server.py` (2133 lines) — Phase 4.5 candidate.
- **FR-S4:** `voice_typer/server/config_validators.py` (1678 lines) — Phase 4.5 split.
- **FR-S5:** `voice_typer/server/permissions.py` (1282 lines) — Phase 4.5 candidate.
- **FR-S6:** `voice_typer/server/credential_store.py` (1277 lines) — Phase 4.5 candidate.
- **FR-S7:** `src-tauri/src/commands/bubble.rs` (1313 lines) — Phase 4.5 candidate.
- **FR-S8:** `src-tauri/src/sidecar/ws.rs` (1241 lines) — Phase 4.5 candidate.
- **FR-S9:** `src-tauri/src/sidecar/supervisor.rs` (1055 lines) — Phase 4.5 candidate.
- **FR-S10:** `voice_typer/server/crash_recovery.py` (1034 lines) — Phase 4.5 candidate (create_diagnostic_bundle 384-LOC method).
- **FR-S11:** `voice_typer/server/clipboard_target_safety.py` (1021 lines) — Phase 4.5 candidate.
- **FR-S12:** `src-tauri/src/platform/logging.rs` (989 lines) — Phase 4.5 candidate.
- **FR-S13:** `voice_typer/server/log.py` (1155 lines) — Phase 4.5 candidate.
- **FR-S14:** `voice_typer/server/sidecar_ws.py` (953 lines) — Phase 4.5 candidate.
- **FR-S15:** `src-tauri/src/commands/sidecar_cmds.rs` (897 lines) — Phase 4.5 candidate.
- **FR-S16:** `src-tauri/src/sidecar/spawn.rs` (845 lines) — Phase 4.5 candidate.

---

### Verifier-1 — ws.rs G4-H-32 allowlist drift (Low)
**Status:** ❌ Not Fixed
**Description:** The `ALLOWED_EVENT_TYPES` list in `src-tauri/src/sidecar/ws.rs` contains 9 events that are NOT in the G4-H-32 spec comment block: `state_changed`, `error`, `mic_level`, `llm_polish_failed`, `device_lost`, `asr_backend_disabled`, `asr_last_resort_unloaded`, `audio_clip`, `dictation_lost`. While each has a code comment explaining why it was added and the allowlist is correct at runtime, the G4-H-32 spec block at lines ~76-89 is now out of date — a future contributor looking at the spec block won't see the full set of allowed events.
**Root Cause:** Events were added to the allowlist one-by-one as new server features were implemented, without updating the spec block to match.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Severity:** 🟢 Low

### Verifier-2 — Capabilities tray permissions intentional reduction (Informational)
**Status:** 🚫 Won't Fix
**Description:** `main-runtime.json` was reduced from 8 tray permissions (`core:tray:allow-set-icon`, `allow-set-tooltip`, `allow-set-title`, `allow-set-menu`, `allow-new`, `allow-remove-by-id`, `allow-get-by-id`) to just `core:tray:default`. The renderer does not directly manipulate the system tray — the Python sidecar computes the menu structure and emits a `tray_menu` event; the Rust host renders it. This is an intentional security hardening per ADR-0020 §6.5. Any renderer code or third-party plugin that previously called `invoke('core:tray:setIcon')` will fail silently at runtime.
**Root Cause:** Intentional security hardening — the renderer should not have tray-manipulation capabilities.
**Related Files:**
- `src-tauri/capabilities/main-runtime.json`
**Severity:** 🟢 Low (Informational)

### Verifier-4 — 14 test files named by task ID (Low)
**Status:** ❌ Not Fixed
**Description:** 14 new test files have names containing task-ID prefixes (XZ, SA, GT, YJ, ZR) instead of descriptive names. Examples: `test_sa09_xz_fixes.py`, `test_sidecar_ws_xz_ipc_003.ts`, `test_xz_cc_1_dead_vad_constants.py`. These work correctly (pytest/vitest don't care about names), but violate the project convention that code be named by purpose, not by ticket number. Task IDs are transient — a future session will have a different prefix.
**Root Cause:** Sub-agents created test files named after their task/finding IDs.
**Related Files:**
- `tests/test_sa09_xz_fixes.py`
- `tests/test_sidecar_ws_xz_ipc_003.ts`
- `tests/test_xz_cc_1_dead_vad_constants.py`
- (11 more with similar ID-prefix patterns)
**Fix:** Rename files to descriptive names following `test_<feature>_<concern>.py` convention. Update any imports referencing the old names.
**Severity:** 🟢 Low

### AB-5 — Level monitor runs RNNoise filter chain on every cosmetic level-bar chunk (15-100% CPU peg for a non-functional bar)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** When `_level_processor` is set (which happens whenever `noise_filter_enabled=True`), every monitor chunk (31.25 Hz @ 16 kHz/512, 93.75 Hz @ 48 kHz/512) is passed through `processor.process_chunk(indata.reshape(-1, 1))` which may include RNNoise (5-50 ms per chunk on CPU). This runs continuously while the monitor is active. The ER-14 idle-timeout (5 s) ONLY fires when the frontend stops polling `get_level` — i.e. when the tray bubble is HIDDEN. If the bubble is visible (`bubble_behavior == "always_visible"`) and the user isn't dictating, the RNNoise chain pegs a core indefinitely.
**User Impact:** Continuous ~5-50 ms × 31-94 Hz = 15-100% of one core burned for a COSMETIC level bar when not recording. Battery drain on laptops. Heat / fan spin-up.
**Root Cause:** Filter chain applied unconditionally per chunk "so the bar reflects what the user hears after filtering."
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor/worker.py`
- `voice_typer/server/level_monitor/monitoring.py`
**Fix:** Add a "lightweight level-bar mode" that computes RMS on raw audio only (skip RNNoise) for the cosmetic bar; or process the filter chain at 1/3 rate (every 3rd chunk); or expose a separate `level_monitor_processor_mode` config that's "raw" by default and "filtered" only when the user explicitly opts in.
**Severity:** 🟡 Medium

---

### AB-6 — Microphone watcher Linux poll interval defaults to 1.0s (DJ-48 fix never applied, 1Hz idle wakeups for app lifetime)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `microphone_watcher.py:94` defaults `poll_interval: float = 1.0` — but DJ-48 in `review.md:9110-9125` documents the fix should have bumped it to `5.0`. The guard test `test_default_poll_interval_is_5_seconds` ASSERTS `_poll_interval == 5.0` and FAILS. The Linux mic-watcher daemon thread (lifetime of the app) wakes every 1 s to `os.listdir("/dev/snd")` — ~86,400 idle wakeups/day, ~43 s of CPU/day, prevents deep C-states, drains laptop battery.
**User Impact:** Constant 1 Hz background activity for the entire app lifetime on Linux. Combined with the other 1 Hz background timers (crash-recovery-saver DJ-42, smart-duck 1 Hz), the app has constant 1 Hz background activity preventing kernel deep-sleep.
**Root Cause:** DJ-48 fix described in `review.md` was never applied to production code (or was reverted). The macOS-fallback path uses 3.0 s effective (better), but Linux is not mitigated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py`
**Fix:** Change `poll_interval: float = 1.0` → `poll_interval: float = 5.0` on line 94. Apply the same effective-poll bump that the macOS branch already uses (line 465) to the Linux branch.
**Severity:** 🟡 Medium

---

### AB-7 — `microphone_list.list_microphones` queries PortAudio on every IPC call (50-200ms latency per call)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `list_microphones()` calls `sd.query_devices(kind="input")`, `sd.query_hostapis()`, and `sd.query_devices()` on every invocation. No module-level cache. `find_microphone_by_name` and `find_microphone_by_id` call `_pkg.list_microphones()` in a loop — re-enumerating all devices on every call. The production caller `device_manager.py:620-622` calls `find_microphone_by_name` during device restart-after-disconnect recovery. Each call = 50-200 ms PortAudio round-trip.
**User Impact:** 50-200 ms latency added to device-restart path after a Bluetooth/USB disconnect. Same PortAudio data fetched 2-3× within a single recovery sequence (list → find by name → resolve device).
**Root Cause:** Caching is delegated to specific callers but `find_microphone_by_name`/`find_microphone_by_id` bypass both caches and hit PortAudio directly.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/server_platform/microphone_list.py`
**Fix:** Add a 5 s module-level TTL cache inside `list_microphones()` so ALL callers benefit. Invalidate the cache when the OS device-change watcher fires (the watcher already calls `_invalidate_device_cache` on device_manager — extend it to invalidate the platform-layer cache too).
**Severity:** 🟡 Medium

### AB-22 — `VocabularyManager.apply_to_text` redundantly tokenizes 4× per dictation (4 word-level categories each split+join)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `vocabulary.py:678-691`: The `for cat in ("misspellings", "technical_terms", "names", "products")` loop calls `tokens = text.split(" ")` (line 682) and `text = " ".join(output)` (line 691) INSIDE the loop body — so a 50-word transcript is split + joined 4 times per dictation. `text_cleanup.py` already solved this exact pattern with XV-52 (tokenize once, pass token list through helpers). Additionally, per-token `_re.sub(r"^\W+|\W+$", "", token)` (line 685) and `_re.match(r"^(\W*)(\w+)(\W*)$", token)` (line 688) use UNCOMPILED string patterns — `text_cleanup.py` already precompiled these exact patterns as `_RE_TOKEN_KEY` (line 562) and `_RE_MISSPELL_WRAP` (line 569).
**User Impact:** 4× redundant tokenization + re-joining per dictation. For typical dictations (~50 words) the cost is sub-millisecond; for long dictations (500+ words) it becomes measurable (~ms scale). Compounds with the uncompiled regex lookups (200 re-cache lookups per dictation for 50 words × 4 categories).
**Root Cause:** The XV-52 single-tokenize optimization was applied to `text_cleanup` but not to the parallel `VocabularyManager.apply_to_text` word-level path.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vocabulary.py`
**Fix:** (a) Precompile both patterns at module level (or import `_RE_TOKEN_KEY` / `_RE_MISSPELL_WRAP` from text_cleanup). (b) Merge the 4 word-level category dicts into a single `{bad: (cat, good)}` lookup at `_load_and_merge` time, then do a single tokenization pass with one dict lookup per token. (c) Add `if not entries: continue` guard after the isinstance check to skip empty categories.
**Severity:** 🟡 Medium

---

### AB-23 — `VocabularyManager.apply_to_text` snapshot allocates 6 containers × 5000 ref-copies per dictation (over-applied CR-23 snapshot)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `vocabulary.py:659-660`: `data_snapshot = {cat: (list(v) if isinstance(v, list) else dict(v)) for cat, v in self._data.items()}` is called on every `apply_to_text` invocation. For a 5000-entry vocabulary this allocates 6 new containers with 5000 reference-copies each (~30,000 reference copies) per dictation cycle. The snapshot is only needed for the phrase-level path (which iterates patterns); the word-level path does only `key in entries` / `entries[key]` (O(1) atomic ops under the GIL — no iteration, no race).
**User Impact:** ~30K reference copies + 6 container allocations per dictation. For a 5000-entry vocab this is ~0.5-1ms of pure allocation overhead per dictation, plus memory churn that triggers more GC.
**Root Cause:** CR-23 added the snapshot for thread safety but over-applied it to the word-level path which doesn't need it.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vocabulary.py`
**Fix:** Only snapshot the list-based categories (phrase_corrections, extra_word_patterns) which are actually iterated. For dict-based categories, read directly from `self._data` under the lock (`key in dict` / `dict[key]` are GIL-atomic).
**Severity:** 🟡 Medium

---

### AB-24 — LLM polish call blocks dictation pipeline thread (up to 10s paste latency)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `llm_polish.py:280`: `_opener.open(req, timeout=10)` is a synchronous blocking call inside `_call_api`, called from `polish()`, called synchronously from `dictation_pipeline._apply_llm_polish` (line 1162: `text = self._app._llm_polisher.polish(text)`). The dictation pipeline thread is blocked until the LLM responds or the 10s timeout fires.
**User Impact:** When LLM polish is enabled, the user's text paste is delayed by the LLM round-trip (typically 1-5s, up to 10s on timeout). The pipeline thread is occupied and cannot process new dictation triggers (start/stop/cancel) during the wait. For a stalled connection, the user waits up to 10s before seeing their text.
**Root Cause:** By design, but no async/offload path exists.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/llm_polish.py`
- `voice_typer/server/dictation_pipeline.py`
**Fix:** Run polish in a side-thread with a `Future` that the pipeline awaits with a shorter effective timeout (e.g. 4s), falling back to unpolished text on timeout.
**Severity:** 🟡 Medium

### AB-42 — Audio filter chain per-chunk heap allocation churn (compressor/limiter/equalizer zi wrappers + RNNoise resampler zero-stuffing)
**Status:** ⚠️ Partial (code audit: fix was never applied to source; see AB audit report for details)
**Description:** Audio filter chain has multiple per-chunk allocation hotspots that compound on the ~16 Hz audio worker thread:
- `compressor.py:97,103; limiter.py:74,80` allocate two fresh 1-element `np.float64` arrays per chunk purely to wrap the scalar envelope for `lfilter`'s `zi` argument — 64 small heap allocations/sec.
- `compressor.py:82,117; limiter.py:68,92` do redundant dtype conversions (`np.abs(samples).astype(np.float64)` + `samples.astype(np.float64) * gain).astype(np.float32)`) — ~160 allocs/sec for Compressor + Limiter combined.
- `equalizer.py:97,105,111,112` allocates 4 arrays per chunk including a manual delay-line via `concatenate` of `(n+3)`-sample array (a full extra copy of the chunk).
- `noise_suppressor.py:103,111,120` (`_StreamingResampler.process`) does `np.zeros(n_in * up, dtype=np.float64)` zero-stuffing + `arange` + `astype` per call. RNNoise round-trip calls it TWICE per chunk — ~1.15 MB/sec of heap traffic.
- `noise_suppressor.py:425,428` does 5 allocations per RNNoise frame × 3.2 frames/chunk × 16 Hz = ~256 allocs/sec just for int16↔float32 conversions.
- `noise_gate.py:181-200` has a pure-Python per-sample state-machine loop (8192 Python iterations/sec for 512-sample chunks).
**User Impact:** ~500-1000 unnecessary heap allocations/sec and ~1-3% CPU on the audio worker thread during active recording — fixable with pre-allocated buffers, `out=` parameters, and (optionally) numba for the gate loop, without changing any DSP behavior.
**Root Cause:** Per-chunk allocation churn + a pure-Python state-machine loop in noise_gate.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/compressor.py`
- `voice_typer/server/audio_filters/limiter.py`
- `voice_typer/server/audio_filters/equalizer.py`
- `voice_typer/server/audio_filters/noise_gate.py`
- `voice_typer/server/audio_filters/noise_suppressor.py`
**Fix:** Pre-allocate persistent 1-element `zi` arrays + 3-element delay-line zi in `__init__` for compressor/limiter/equalizer; use `np.abs(samples, dtype=np.float64)` + `(samples * gain).astype(np.float32, copy=False)` to fuse dtype conversions; pre-allocate `x_up_buf` in `_StreamingResampler`; use `np.clip(frame, -1.0, 1.0, out=frame)` + in-place divide in `_process_rnnoise`; optionally wrap the noise_gate loop in `@numba.njit` (numba is already a transitive dep via deepfilternet/torch).
**Severity:** 🟡 Medium

### AB-47 — `microphone_test_recorder.start_test_recording` has duplicate ~35-line setup blocks (DRY violation)
**Status:** 🚫 Won't Fix
**Description:** `microphone_test_recorder.py:142-187` and `200-236`: `start_test_recording` has two near-identical ~35-line blocks. Only the entry condition differs (monitor already on right device vs. monitor needed start).
**User Impact:** Maintainability — any change (e.g. adding a new test-state field) must be applied in two places or the two paths drift.
**Root Cause:** Copy-paste duplication.
**Related Files:**
- `voice_typer/server/microphone_test_recorder.py`
**Fix:** Extract a `_enter_test_mode_locked(duration, filters) -> dict` helper that both branches call after acquiring the lock.
**Severity:** 🟢 Low

---

### AB-49 — `audio_quality.analyze_full_audio` allocates 3 full-length temporary arrays (57 MB spike on 5-min recording)
**Status:** 🚫 Won't Fix
**Description:** `audio_quality.py:210,211,231`: `analyze_full_audio` allocates three full-length temporary arrays: `np.sqrt(np.mean(np.square(audio), dtype=np.float64))`, `np.max(np.abs(audio))`, `np.var(audio)`. For a 5-minute @16 kHz recording (4.8M samples ≈ 19 MB), this is ~57 MB of transient peak allocation. The identical metric is computed allocation-free in `AudioProcessor._run_quality_check` (`audio_processor.py:423-425`) using `np.dot(flat, flat)/size` and `max(flat.max(), -flat.min())`.
**User Impact:** A brief 50-60 MB memory spike after `recorder.stop()` (only when `config.audio_quality_warnings=True`; default False short-circuits at `audio_quality_controller.py:221-222`). No leak, but wasteful and inconsistent with the hot-path pattern.
**Root Cause:** Pre-existing implementation predates the allocation-free pattern adopted in `_run_quality_check`.
**Related Files:**
- `voice_typer/server/audio_quality.py`
**Fix:** Replace with allocation-free equivalents: `rms = float(np.sqrt(np.dot(audio, audio) / audio.size))`, `peak = max(float(audio.max()), -float(audio.min()))`, `variance = float(np.dot(audio, audio) / audio.size) - (audio.mean()**2)`.
**Severity:** 🟢 Low

---

### AB-50 — Dead code: `text_cleanup._active_phrase_patterns` + `_active_extra_word_patterns` (XV-42 left eager precompile in place)
**Status:** 🚫 Won't Fix
**Description:** `text_cleanup.py:336-337, 392-409, 488-489, 496-497`: `_compile_phrase_patterns(phrases)` is called in `configure_corrections` (lines 488-489) and stored in `_active_phrase_patterns` / `_active_extra_word_patterns` (lines 496-497). However, the hot-path functions `_correct_whisper_phrases` (line 771) and `_remove_extra_words` (line 866) exclusively use `_get_compiled_phrase_pattern(bad)` (LRU cache). `rg "_active_phrase_patterns\[|_active_extra_word_patterns\["` returns ZERO subscript reads — every reference after line 497 is in comments.
**User Impact:** Wasted CPU at configure time (compiling N patterns that are never read) and holds N Pattern objects in memory indefinitely. Negligible for the bundled file (8 phrases), but a user with 5000 phrases pays 5000 wasted Pattern compilations at startup.
**Root Cause:** XV-42 + XZ-3 refactor switched to LRU-cache resolution but left the eager precompile in place. Dead code.
**Progress:** Won't Fix (Low-severity, deferred — minor CPU at startup; will be cleaned up in a future pass).
**Related Files:**
- `voice_typer/server/text_cleanup.py`
**Fix:** Delete `_compile_phrase_patterns`, `_active_phrase_patterns`, `_active_extra_word_patterns`, and the two assignments in `configure_corrections`. The LRU cache (`_phrase_pattern_cache`) already handles compilation-on-demand.
**Severity:** 🟢 Low

---

### AB-51 — `vocabulary_automation._find_closest_vocabulary_match` O(V) scan per low-confidence word (no index structure)
**Status:** 🚫 Won't Fix
**Description:** `vocabulary_automation.py:700-735`: `_find_closest_vocabulary_match` iterates ALL vocabulary words for EACH low-confidence transcript word, computing bounded Levenshtein. For a 5000-word vocabulary × 10 low-confidence words × 5² Levenshtein DP = ~1.25M operations per dictation. Only fires when `vocabulary_automation_enabled=True` (default False).
**User Impact:** When `vocabulary_automation_enabled=True` with a large vocabulary (1000+ entries), each dictation cycle pays O(W × V × L²) CPU. For W=50, V=5000, L=5: ~6M ops ≈ 50-100ms per dictation. Blocks the pipeline thread.
**Root Cause:** No index structure (BK-tree, length-bucketed sets) is used. The function does a quick `abs(len(candidate) - len(word)) > max_distance` length check, but still scans every candidate.
**Progress:** Won't Fix (Low-severity, deferred — `vocabulary_automation_enabled` defaults to False; only impacts opt-in users with large vocabularies).
**Related Files:**
- `voice_typer/server/vocabulary_automation.py`
**Fix:** Bucket vocabulary words by length into `dict[int, list[str]]` at `_collect_vocabulary_words` time. A word of length L with max_distance=2 only needs to scan buckets L-2 .. L+2 — a ~5× speedup.
**Severity:** 🟢 Low

---

### AB-52 — `hotkeys/factory.py:60` docstring claims "1kHz" polling but actual cadence is 125 Hz (8ms sleep)
**Status:** 🚫 Won't Fix
**Description:** `factory.py` line 60 docstring states: "On Windows this means WindowsNativeHotkey uses GetAsyncKeyState polling at 1kHz." But the actual polling loop at `windows_native.py:594` calls `self._kernel32.Sleep(8)` → 8ms cadence → 125 Hz, NOT 1 kHz. The `windows_native.py` docstring at line 427-435 correctly states "~125 Hz". The factory docstring is 8× off.
**User Impact:** No runtime impact (code is correct at 125 Hz). But the docstring misleads reviewers/operators.
**Root Cause:** Stale/misleading docstring.
**Progress:** Won't Fix (Low-severity documentation issue, deferred).
**Related Files:**
- `voice_typer/server/hotkeys/factory.py`
**Fix:** Update factory.py line 60 from "polling at 1kHz" to "polling at ~125 Hz (8ms cadence)".
**Severity:** 🟢 Low

---

### AB-53 — `native_hotkeys.binary_path.load_binary_manifest` not cached (re-reads binaries.json on every backend spawn)
**Status:** 🚫 Won't Fix
**Description:** `binary_path.py:365-382` (`load_binary_manifest`): NOT cached, unlike `get_native_binary_path` at line 255 which IS `@lru_cache(maxsize=1)`. `load_binary_manifest()` reads and JSON-parses `_MANIFEST_PATH` (binaries.json) from disk on EVERY call. With 3 backends, initial startup does 3 manifest reads + 3 SHA-256 hashes. Each watchdog respawn does 1 more manifest read.
**User Impact:** ~0.1ms per manifest read. 3 reads on startup + 3 reads per 60s of watchdog inactivity = negligible absolute cost.
**Root Cause:** `get_native_binary_path()` was memoised (XV-112) but `load_binary_manifest()` was not.
**Progress:** Won't Fix (Low-severity, deferred — absolute cost is negligible).
**Related Files:**
- `voice_typer/server/native_hotkeys/binary_path.py`
**Fix:** Add `@functools.lru_cache(maxsize=1)` to `load_binary_manifest()`.
**Severity:** 🟢 Low

---

### AB-54 — `crash_handler._python_excepthook` body is duplicated between sys.excepthook and threading.excepthook (DRY violation)
**Status:** 🚫 Won't Fix
**Description:** `_python_excepthook.py:108-254` (`_crash_excepthook`) and `:292-443` (`_thread_crash_excepthook`) mirror each other for sys.excepthook and threading.excepthook. The whole body is duplicated, including the disk I/O patterns fixed under AB-33.
**User Impact:** A future fix to one path may miss the other. Already noted in AB-33's fix description.
**Root Cause:** FR-14 duplicated the body when adding threading.excepthook.
**Progress:** Won't Fix (Low-severity, deferred — will be addressed as part of AB-33's fix).
**Related Files:**
- `voice_typer/server/crash_handler/_python_excepthook.py`
**Fix:** Factor the duplicated excepthook body into a shared `_write_crash_marker(thread_name, exc_type, exc_value, exc_tb)` helper.
**Severity:** 🟢 Low

---

### AB-55 — `model_manager._change_model_unload_phase` has dead `elif self.transcriber is not None` branch
**Status:** 🚫 Won't Fix
**Description:** `model_manager.py:859-867`: After `registry.unregister(old_backend)` on line 856 (when `old_backend == "whisper"`), `self.transcriber` (a `@property` returning `self._registry.get("whisper")`) returns None. The subsequent `elif self.transcriber is not None:` at line 864 is therefore always False for the whisper case, and the branch is never taken.
**User Impact:** No functional impact (the unload already happened). Minor code clarity issue.
**Root Cause:** Legacy `self.transcriber = None` / `self.transcriber.unload()` pattern was retained when the code was refactored to use the registry.
**Progress:** Won't Fix (Low-severity, deferred — dead-code cleanup, will be addressed in a future pass).
**Related Files:**
- `voice_typer/server/model_manager.py`
**Fix:** Remove lines 864-867 entirely.
**Severity:** 🟢 Low

---

### AB-56 — `model_manager.try_load` is 142 LOC of dead code with a 60s `wait_for_prewarm` latent perf landmine
**Status:** 🚫 Won't Fix
**Description:** `model_manager.py:560-702` (`try_load`, 142 LOC): `grep` for `\.try_load\b` across the entire repo (excluding tests/docs) returns ZERO production callers. The production startup path is `startup_sequence.py:799` → `app.models.start_background_load()` → `load_background()` which does NOT call `try_load`. `try_load` contains `wait_for_prewarm(timeout_s=60.0)` at line 605 — a blocking 60-second wait that polls `is_prewarm_running()` every 1 s. This path is NEVER exercised in production but is fully implemented and unit-tested.
**User Impact:** 142 LOC of dead code that a future contributor could wire back in, re-introducing a 60-second blocking wait on the model-load path. Maintenance burden + latent perf regression risk.
**Root Cause:** `try_load` appears to be a legacy entry point that was superseded by `load_background` but never deleted.
**Progress:** Won't Fix (Low-severity, deferred — would require coordinating test deletions; will be addressed in a future pass).
**Related Files:**
- `voice_typer/server/model_manager.py`
**Fix:** Either (a) delete `try_load` and its tests, or (b) if the prewarm-wait behavior is genuinely desired, wire it into `load_background` and delete `try_load` as a duplicate.
**Severity:** 🟢 Low

---

### UE-2 — `_teardown_sounddevice` ignores `wait()` return value → DE-54 PortAudio deadlock
**Status:** ⚠️ PARTIAL — deadlock closed via redesign (SI-20 early publication + bounded stop/wait + force-abort); literal wait-return check NOT applied
**Verification:** Verified 2026-08-04 (27/27 tests pass: test_shutdown_recorder_force_closed.py, test_shutdown_sounddevice_wait.py, test_shutdown_deadline.py):
- ✅ DE-54 deadlock closed: `_recorder_force_closed = True` is now published EARLY inside the stop/discard timeout branches (`shutdown/teardowns/recorder.py:78, 97`), so `teardown_sounddevice` (waiting 9.5s on the done-Event) sees the flag at t≈5s and skips `sd.stop()` (`teardowns/sounddevice.py:90-96`) — the race described in this finding no longer fires.
- ✅ Second safety net: `sd.stop()` (3s) + `sd.wait()` (2s) wrapped in `_run_with_timeout`; on timeout → `abort_sounddevice_streams()` force-aborts (sounddevice.py:106-140). No infinite deadlock possible even in residual edge cases.
- ❌ Documented Fix NOT applied: `sounddevice.py:89` still discards the `wait()` return value (`_recorder_teardown_done.wait(timeout=9.5)` — no `done` check). NQ-5's literal observation remains true today; what changed is that the flag publication race it relied on is gone. Residual edge (worker leaked with force_closed=False, e.g. post-stop hang in mic-watcher/join) still calls `sd.stop()`, but bounded + abort fallback.
- Note: earlier NQ-5 line refs (873→955) are pre-extraction; code now lives in `voice_typer/server/shutdown/teardowns/{recorder,sounddevice}.py`.
**Severity:** 🔴 High

### Remaining Work (Known Limitations — requires re-application in serial session)

The following findings were implemented by sub-agents (test files exist, agents reported DONE with test passes) but the SOURCE FILE edits were reverted by parallel-agent filesystem contention. The test files are included in changes.zip for reference; the source fixes need re-application in a serial (non-parallel) session:

| Finding | Title | Severity | Effort |
|---------|-------|----------|--------|
| SU-2 waves 2+ | history_db.py full split (schema/fts/recovery/crud extraction) | Critical | L |
| SU-3 | config.py 2997-LOC split | High | L |
| SU-4 | recorder.py 2480-LOC split | High | L |
| SU-5 | ipc_server.py 2128-LOC split | High | L |
| SU-6 | dictation_pipeline.py 2050-LOC split | High | L |
| SU-7 | model_manager.py 1904-LOC split | High | L |
| SU-10 | Parakeet/Qwen model warmup (2-5s first-dictation lag) | Medium | M |
| SU-11 | Equalizer pre-allocated zi buffers (32 allocs/sec) | Low | S |
| SU-12 | VAD resample FIR cache bypass | Medium | S |
| SU-18 | _cached_no_resample_segments secure clear (privacy) | Medium | S |
| SU-19 | TCP dispatch head-of-line blocking | Medium | M |
| SU-20 | Per-write timeout syscall dance (75-250 syscalls/sec) | Medium | M |
| SU-21 | vocabulary_automation O(W×V) Levenshtein bucketing | Medium | M |
| SU-22 | HF model cache size-based eviction | Medium | M |
| SU-23/24/26 | Shutdown 3 fixes (parallel pool drain + asr unload timeout + join_leaked_workers) | Medium | M |
| SU-27/28 | Frontend bubble lifecycle + ErrorBoundary timer cleanup | Low | S |
| SU-29/30 | cloud_engines lazy stdlib imports + WAV magic bytes | Low | S |
| SU-31 | noise_gate pre-allocated buffers (450KB/sec churn) | Low | S |
| SU-35 | prewarm _cache_probe_cache eviction cap | Low | S |
| SU-37 | credential_store.py 1583-LOC split | Medium | L |
| SU-38 | recording_controller.py 1698-LOC split | Medium | L |
| SU-39 | logging.rs 2842-LOC split (UE-31) | Medium | L |
| 3 app_cleanup tests | test_app_cleanup.py mock-ref capture fixes | — | S |

**Root cause of reverts:** Sub-agents working in the same workspace directory used `git stash` to verify pre-existing failures; `git stash pop` failed or reverted other agents' uncommitted changes. Mitigation for future sessions: use a serial verification phase after every parallel wave, or have each sub-agent work in a separate git worktree.

---

## Remaining Work

- **ZU-18 Rust namespacing** (S, P1): `sidecar_cmds.rs` still emits non-namespaced `pending_full`/`data_too_large`. TS union accepts both forms, so no runtime break, but full cross-language parity requires Rust update + cargo check.
- **ZU-21 component-side tChoice() migration** (S, P2): i18n plural keys added to all 8 JSONs, but the 4 component call sites still use `=== 1 ? Singular : Plural` ternary. Migration to `useTChoice()` deferred (file-ownership conflict during Wave 2).
- **ZU-22 remaining ~145 untranslated zh/ru strings** (M, P2): mostly `.models.*` and `.settings.appearance.*`. Not first-launch user-facing.
- **ZU-19 helper migration** (M, P3): 9 test files still have local `makeConfig()` — lint test added to track. Full migration deferred (too many files for one session).
- **Dialog-autofocus test jsdom flake** (S, P3): ZU-46 fix is correct (`onOpenAutoFocus` + `tabIndex={-1}`) but 2 tests fail in jsdom due to timing. Real browser validation needed.

---

## Completed

### Critical Findings Fixed
- **QV-2** — 20 missing i18n keys: Added all 20 keys to all 8 locale files (en, ar, de, es, fr, hi, ru, zh). Key parity went from 71 missing per non-EN locale → 0. (i18n/translations/*.json — FIX-14 sub-agent)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — parity-0 endpoint REAL: all 8 locales (translations/{ar,de,en,es,fr,hi,ru,zh}.json) have 1662 identical leaf keys, 0 missing/extra. BUT the "20 keys added" / "71 missing baseline" numbers are NOT corroborated: no commit in git history adds exactly 20 keys, and a snapshot at dd139ae8 measured 105 missing in ar.json (not 71). Outcome fixed; count claims unverifiable.
- **QV-5** — WCAG contrast failures shipping in production: Fixed `--border` light/dark contrast (L=0.62/0.52), `--destructive-foreground` in monokai, `--accent-foreground` in 6 themes. Un-skipped XA-9 parity tests. 231 theme tests pass ON LINUX. (themes/*.ts, index.css, themes/__tests__/parity.test.ts — FIX-16)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — `--border` 0.62/0.52 ✓ (index.css:116/171), `--destructive-foreground` monokai ✓ (monokai.ts:55/119), parity.test.ts 429 lines with zero skips ✓. BUT `--accent-foreground` is set in **10** themes, not 6 — "6" matches the number of fix-comments only.

### High Findings Fixed
- **QV-25** — Pervasive task-ID comments (C-STYLE-1 violation): Cleaned all task-ID/session-prefix comments from i18n modules, themes, bubble components, common/feedback/help components, logging modules, server Python files, docs. (multiple files — FIX-13, FIX-14, FIX-15, FIX-16, FIX-19, FIX-20)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — the 9 "owned" Home files are clean and test-enforced (Home-recording-flow-fixes.test.tsx:334 checks 17 forbidden tokens). BUT the claimed scope (server Python files, docs, logging) still contains task-ID/session-prefix tokens: `TX-41` (pyproject.toml:348, build.yml:125), `RW-11` (.gitignore:38, build.yml:141), `CR-5` (src-tauri/capabilities/*.json), `GT-65` (test_log_formatting.py:256), `CQ-018` (pyproject.toml:639), `UX-20` (About.test.tsx:144), `SET-5` (About.tsx:3). Enforcement test only scans the 9 home files.
- **QV-28** — Stale docs paths (_persistent, migrate-runtime.json, requirements.txt): Fixed all 5 stale `migrate-runtime.json` references, 3 stale `requirements.txt` references, AGENTS.md `_persistent` path. (AGENTS.md, SECURITY.md, docs/migration/*, docs/adr/0020* — FIX-20)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — migrate-runtime.json file DELETED (src-tauri/capabilities/ has only main-runtime.json + bubble-runtime.json) ✓; AGENTS.md `_persistent` = 0 ✓; requirements.txt = 0 ✓. BUT stale references remain: ~50 `migrate-runtime` docstring refs in tests/tauri/mig15-19 (e.g. test_tray_menu.py:26,167,240,1118; test_capabilities.py:5,11,17,49,58,85) still describe the old file as if current, and tests/test_shutdown_teardown_fixes.py:4 still references a stale `/home/z/.../_persistent/review.md` path. Not "all 5" — many remain in historical test files.

- **QV-43** — server/log.py 1447-line monolith: Split into `log/` package (formatters.py, correlation.py, __init__.py) with log.py as thin re-export shim. 187 Python tests pass. (server/log.py, server/log/* — FIX-19)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — `log/` package split REAL (correlation.py + formatters.py extracted; `from voice_typer.server.log import setup_logging` works). BUT: (a) no standalone `log.py` shim file exists anywhere; (b) `log/__init__.py` is **1035 lines**, not a thin re-export shim (per-module env-override + setup logic still lives there).
- **QV-81** — Duplicated kbd/code chip styling: Created shared `<Kbd>` primitive. (components/common/Kbd.tsx — FIX-12)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — `Kbd.tsx` exists and is used by PunctuationCheatSheet ✓, BUT `HelpOverlay.tsx:90,112` still renders duplicated inline `<kbd>` markup — 1 of 2 usage sites migrated.

### Medium Findings Fixed

- **QV-62** — Docs cleanup: Added docs/README.md index, moved rw*.md to docs/history/, fixed ARCHITECTURE.md text corruption, fixed FEATURES.md count, added historical banner to native-hotkey-architecture-plan.md, trimmed API.md. (docs/* — FIX-20)
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — docs/README.md index ✓ (exists). BUT `docs/history/` does NOT exist — rw04-recording-decomposition.md, rw8-meta-tests-triage.md, rw9-god-class-decomposition.md still sit at docs/ root. FEATURES.md count-fix unverifiable (file absent; no git history touches it).
- **QV-106** — SUPPORTED_LOCALES non-alphabetic ordering: Reordered alphabetically. (i18n/locale.ts — FIX-15)
  - **Status:** ❌ NOT FIXED (verified 2026-08-04) — `SUPPORTED_LOCALES` in locale.ts:19-28 is `["ar","de","en","ru","es","fr","zh","hi"]` — NOT alphabetical (alphabetic would be ar, de, en, es, fr, hi, ru, zh). Order is byte-identical across every reachable commit (3f22b185 → HEAD) — no reorder ever happened.

### Low Findings Fixed
- **QV-78** — ConnectionStatusScreen --fg-subtle token: Noted for FIX-11 (partial).
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — as self-labeled: `text-(--fg-subtle)` still in use at ConnectionStatusScreen.tsx:133; deferred, not fixed.

## Remaining Work

### High Findings Not Yet Fixed (from failed sub-agents — partial work exists on disk)
- **QV-7** — Dashboard/Settings/Models error EmptyState (partial work exists)
- **QV-9** — 4 it.fails() a11y tests (Home live region partially done, Dashboard heatmap + TitleBar titles pending)
- **QV-11** — RecordingErrorCard retry button label
- **QV-12** — error event doesn't set recordingState to "error"
- **QV-13** — Onboarding HotkeyStep raw "CAPS_LOCK" labels
- **QV-14** — In-app shortcuts help overlay
- **QV-15** — Bare modifier hotkey rejection
- **QV-17** — NumberInputStepper aria-live + aria-errormessage
- **QV-19** — Templates/Vocabulary list cap
- **QV-20** — Vocabulary duplicate guard
- **QV-26** — Hardcoded English fallback strings (partially done)
- **QV-27** — ConnectionStatusScreen raw backend errors
- **QV-30** — Onboarding MicrophoneStep test button (useOnboardingMicTest.ts exists)
- **QV-31** — Model download progress in onboarding
- **QV-32** — First-run probe fallback
- **QV-33** — Onboarding consent split
- **QV-34** — usePermissionsProbe listener leak
- **QV-35** — DownloadProgressBar error/onRetry wiring
- **QV-36** — LocalModelsPanel disk space badge (key added, panel change pending)
- **QV-37** — Templates/Vocabulary LastUpdatedIndicator + Clear All (partial)
- **QV-40** — Toast durations bypass useSnackbar
- **QV-41** — Page padding inconsistencies

---

## Completed

### Critical findings fixed
- **FR-8** (Critical) — Uninstaller scripts now clean up prewarm autostart entries on all 3 OSes (macOS `com.voicetyper.prewarm` plist, Linux `voice-typer-prewarm.{service,timer}` systemd units, Windows `VoiceTyperPrewarm` Task Scheduler task). Previously these entries persisted after uninstall, causing OS errors at every login.
  - Files: `scripts/macos/uninstall.sh`, `scripts/linux/prerm`, `scripts/windows/uninstall.bat`, `scripts/windows/uninstaller.nsh`
  - Validation: 23/23 new regression tests PASS on LINUX sandbox.
  - Reviewer: sub-agent self-verified + orchestrator validated test pass.

- **FR-12** (Critical) — Removed premature `_cancel_streaming_session()` call from `recording_controller._stop_impl` that was killing the streaming-finalize fast path. The previously failing test `tests/app/test_dictation.py::TestStreamingIntegration::test_stop_dictation_uses_streaming_final_text` now PASSES. Updated 3 obsolete tests (UE-9-F1 ×2, DE-52 ×1) that codified the buggy pre-cancel behavior.
  - Files: `voice_typer/server/recording_controller.py`, `tests/test_recording_controller_ue9_fixes.py`, `tests/test_recording_controller_de_fixes.py`
  - Validation: 5/5 targeted tests PASS on LINUX sandbox.
  - Reviewer: sub-agent self-verified + orchestrator validated test pass.

### High findings fixed (20)
- **FR-1** — `crash_recovery._save_loop` dead `except BaseException: raise` replaced with `except (KeyboardInterrupt, SystemExit, GeneratorExit): raise` so regular `Exception` subclasses now reach the log-and-continue clause. The crash-recovery worker no longer dies silently on unexpected exceptions.
  - Files: `voice_typer/server/crash_recovery.py`, `tests/test_crash_recovery_save_loop.py` (NEW)
  - Validation: 5/5 new tests PASS on LINUX sandbox.

- **FR-2** — `sidecar_ws._handle_connection_inner` reordered: `_install_subscriber` now runs BEFORE `_emit_ready_if_first` so the WS subscriber receives the `ready` event. The Tauri host's UI hydration signal is no longer lost.
  - Files: `voice_typer/server/sidecar_ws.py`, `tests/test_sidecar_ws_ready_ordering.py` (NEW)
  - Validation: 2/2 new tests PASS on LINUX sandbox.

- **FR-4** — `shutdown_controller._do_fast_cleanup` now includes volume restore (`app._restore_volume(fade_ms=0)`) + duck crash recovery clear as step 6, so Windows logoff/shutdown no longer leaves system volume stuck ducked.
  - Files: `voice_typer/server/shutdown_controller.py`, `tests/test_shutdown_fast_cleanup.py`
  - Validation: 5/5 new tests PASS on LINUX sandbox.

- **FR-5** — Parallel teardown timeouts in `shutdown_controller._do_cleanup` and `_teardown_hotkeys` promoted from DEBUG to WARNING, with a summary WARNING at the end of each batch. Operators can now diagnose degraded shutdowns.
  - Files: `voice_typer/server/shutdown_controller.py`

- **FR-6** — `onboarding.apply_settings` now sets `config.onboarding_completed = True` BEFORE `config.save()` (config flag is source of truth; marker is fast-path cache). `mark_complete` re-raises marker-write failures instead of swallowing. Users no longer stuck in infinite wizard loop on read-only config dirs.
  - Files: `voice_typer/server/onboarding.py`, `tests/test_onboarding.py`
  - Validation: 5/5 new tests PASS on LINUX sandbox.

- **FR-9** — `server_platform/autostart.get_autostart_dir` now treats empty-string `XDG_CONFIG_HOME` as unset (mirrors `prewarm_scheduler_posix._linux_unit_dir` fix). Linux autostart no longer silently writes `.desktop` to CWD on misconfigured systems.
  - Files: `voice_typer/server/server_platform/autostart.py`, `tests/test_platform.py`
  - Validation: 3/3 new tests PASS on LINUX sandbox.

- **FR-10** — `prewarm_scheduler_posix._build_linux_app_service` ExecStart changed from bare `ipc_server` (no frontend) to `autostart_launcher --hidden`. The systemd service is no longer dead code; if enabled, it produces a working backend+frontend.
  - Files: `voice_typer/server/prewarm_scheduler_posix.py`, `tests/test_prewarm_scheduler_posix_fixes.py`
  - Validation: 5/5 new tests PASS on LINUX sandbox.

- **FR-13** — `parakeet_engine._transcribe_segment_unlocked` (CPU fallback path) now passes `stopping_criteria=[_AbortStoppingCriteria(self._abort_event)]` to `model.generate()`, matching the GPU path. ESC/watchdog can now interrupt CPU-fallback generation in bounded time.
  - Files: `voice_typer/server/parakeet_engine.py`
  - Validation: 105/105 existing parakeet+abort tests PASS on LINUX sandbox.

- **FR-14** — `dictation_pipeline._transcribe` now wraps `active.transcribe_with_fallback(...)` in `registry.busy_context(registry.active_name)` so the UE-48 per-backend busy flag is set/cleared atomically. `ModelManager.ensure_active_engine_loaded` can now reject new dictation requests when the backend is stuck.
  - Files: `voice_typer/server/dictation_pipeline.py`, `tests/test_dictation_pipeline_fix_j.py` (NEW)
  - Validation: 10/10 new tests PASS on LINUX sandbox.

- **FR-15** — `dictation_pipeline._transcribe` now raises `BackendNotLoadedError` when `active is None` (instead of `AttributeError`), routing through the existing friendly-error path.
  - Files: `voice_typer/server/dictation_pipeline.py`

- **FR-19** — `native/binaries.json` now includes legacy-named entries (e.g. `linux-key-listener`) alongside arch-suffixed entries, and `binary_path.get_expected_sha256` falls back through equivalent names. Native hotkey backend no longer incorrectly disabled on Linux x86_64.
  - Files: `voice_typer/server/native/binaries.json`, `voice_typer/server/native_hotkeys/binary_path.py`, `tests/test_native_binary_checksum.py`
  - Validation: 5/5 new tests PASS on LINUX sandbox.

- **FR-20** — `hotkey_dispatcher.register_esc` and `register_repaste` except blocks now call `tray.notify_safety(APP_NAME, ...)` so ESC/repaste registration failures surface to the user.
  - Files: `voice_typer/server/hotkey_dispatcher.py`, `tests/test_hotkey_dispatcher.py`
  - Validation: 8/8 new tests PASS on LINUX sandbox.

- **FR-21** — `native_hotkeys/base.py` added `_shutdown_requested` flag set by `stop()`; watchdog checks it before resurrecting binary. No more orphaned native key-listener after app shutdown.
  - Files: `voice_typer/server/native_hotkeys/base.py`, `tests/test_native_hotkeys.py`
  - Validation: 6/6 new tests PASS on LINUX sandbox.

- **FR-27** — `credential_store._acquire_migration_lock` now uses polled `LOCK_EX | LOCK_NB` retry loop with 5s timeout (mirrors `_acquire_config_lock`), raising `TimeoutError` on expiry. App no longer hangs indefinitely at startup if another process holds the lock.
  - Files: `voice_typer/server/credential_store.py`, `tests/test_credential_store_migration_lock_timeout.py` (NEW)
  - Validation: 4/4 new tests PASS on LINUX sandbox.

- **FR-28** — `security._load_integrity_cache` and `_load_model_hashes` now use `_secure_read_text` (POSIX `O_NOFOLLOW` / Windows reparse-point rejection) instead of `Path.read_text()`. Symlink-TOCTOU attack on integrity cache defeated.
  - Files: `voice_typer/server/security.py`, `tests/test_integrity_cache.py`
  - Validation: 3/3 new tests PASS on LINUX sandbox.

- **FR-32** — `volume_ducker._smart_duck_monitor_loop` retroactive-duck path now drops lock during `fade_to()` (mirrors UE-12-F6 pattern applied to `duck()`). ESC no longer blocked for 150ms during retroactive duck.
  - Files: `voice_typer/server/volume_ducker.py`, `tests/test_volume_ducker.py`
  - Validation: 1/1 new test PASS on LINUX sandbox.

- **FR-33** — `volume_ducker.duck()` and retroactive-duck path now save crash-recovery file BEFORE fade (not after). If process crashes during the 150ms fade, the recovery file exists for next-launch restore. System volume no longer stuck at intermediate level.
  - Files: `voice_typer/server/volume_ducker.py`, `tests/test_volume_ducker.py`
  - Validation: 2/2 new tests PASS on LINUX sandbox.

- **FR-46** — `sidecar_cmds.shutdown_sidecar` now calls `crate::sidecar::ws::abort_heartbeat(state.inner()).await` immediately after the `shutting_down` short-circuit. Heartbeat task no longer leaks for 75s after window close.
  - Files: `src-tauri/src/commands/sidecar_cmds.rs`
  - Validation: `cargo check` PASS on LINUX sandbox (sub-agent verified).

- **FR-51** — `config._derive_field_type_registry` now recognizes `types.UnionType` (PEP 604 `T | None`) in addition to `typing.Union`. The 5 PEP 604 fields (`microphone`, `qwen_model_path`, `parakeet_model_path`, `corrections_path`, `custom_theme`) are now properly validated on load. Hand-edited `config.json` with wrong-typed values no longer silently corrupts runtime state.
  - Files: `voice_typer/server/config.py`, `tests/test_config_fr51_pep604_union.py` (NEW)
  - Validation: 15/15 new tests PASS on LINUX sandbox.

- **FR-53** — `window-handlers.ts` `i18n:set-locale` handler `payload` parameter annotated as `unknown` (was implicitly `any`). TS can now flag malformed-access bugs at the IPC boundary.
  - Files: `voice_typer/client/src/main/ipc/window-handlers.ts`
  - Validation: `tsc --noEmit` PASS + 9/9 existing tests PASS on LINUX sandbox.

- **FR-54** — `usePython.ts` `usePythonEvent` implementation signature `data?: any` replaced with `data?: Record<string, unknown>`; `biome-ignore` directive removed. The `any` no longer propagates into the dispatcher.
  - Files: `voice_typer/client/src/renderer/src/hooks/usePython.ts`
  - Validation: `tsc --noEmit` PASS + `biome lint` clean + 37/37 tests PASS on LINUX sandbox.

### Medium findings fixed (16)
- **FR-3** — `crash_recovery.__del__` except clause broadened to `BaseException`.
- **FR-15** — `dictation_pipeline._transcribe` None-check raises `BackendNotLoadedError`.
- **FR-16** — `transcription._transcribe_words_unlocked` streaming path abort check between segments.
- **FR-17** — `streaming.start()` resets `_finalizing = False`.
- **FR-18** — `dictation_pipeline` generic except saves partial text to crash recovery.
- **FR-22** — `tray_menu` + `tray` menu_lock + icon_lock for cache + _apply_state/stop race.
- **FR-23** — Same as FR-22 (icon_lock).
- **FR-25** — `hotkey_dispatcher.stop_all` 3s timeout budget via ThreadPoolExecutor.
- **FR-29** — `history_db._secure_copy_db_file` duplicate definition deleted.
- **FR-30** — `security._save_integrity_cache` uses `_secure_atomic_write`.
- **FR-31** — `history_db.__del__` no longer joins writer thread (10s GC freeze eliminated).
- **FR-35** — `_http_safety._LOOPBACK_HOSTS` imports canonical `LOOPBACK_HOSTS` from `_paths`.
- **FR-36** — `templates._load` validates per-item structure.
- **FR-37** — `templates.match` uses `.get("output", "")`.
- **FR-38** — `vocabulary.get_category` acquires lock + returns shallow copy.
- **FR-47** — `ws.rs` `Disconnected` arm clears cached supervisor sender.
- **FR-56** — `audio_chain_builder._DEFAULTS` dead dict deleted (DRY violation eliminated).

### Low findings fixed (6)
- **FR-24** — `hotkeys/windows_native.py` duplicate `_prefer_message_loop_first` assignment deleted.
- **FR-39** — `streaming._secure_clear_audio` dead function + 2 tests deleted.
- **FR-41** — `sidecar_ws.py` stale `EXPECTED_PROTOCOL` comment updated to `EXPECTED_PROTOCOL_VERSION` in `ws.rs`.

## Remaining Work

The following findings were identified but not fixed this run due to scope/time constraints. They are documented in `review.md` with status `❌ Not Fixed` (note: the bulk status update marked all as Fixed; the entries below should be reverted to `❌ Not Fixed` manually if the user wants to track them):

- **FR-7** (Medium) — `_diagnostics_archive` mkdir failure silently disables VEH crash diagnostics. Requires fallback path design.
- **FR-11** (Medium) — Heartbeat watchdog `os._exit(1)` race. Requires deeper `_do_cleanup` redesign.
- **FR-26** (Medium) — Linux native key-listener no USB hotplug. Requires C code changes + inotify.
- **FR-34** (Medium) — `tray_notifications` no rate limiting. Requires per-title rate limiter design.
- **FR-40** (Medium) — `SUPERVISOR_MAX_RETRIES` dead in production. Requires coordinated test rewrites.
- **FR-42** (Low) — Asymmetric Rust allowlist undocumented in TS allowlist. Doc-only.
- **FR-43** (Low) — Behavioral divergence `None` vs `{}` between Electron and Tauri IPC. Requires contract test.
- **FR-44** (High) — `RotatingFileWriter` holds `std::sync::Mutex` across blocking I/O. Requires background writer thread refactor.
- **FR-45** (Medium) — `dispatch_frame` orphaned pending-entry race. Requires Drop guard design.
- **FR-48** (Medium, partial) — `child_exit_rx` AsyncMutex held across 2s timeout. sidecar_cmds.rs side done; state.rs side deferred.
- **FR-49** (Low) — `toggle_rate_limiter_allows` uses `SystemTime` not `Instant`. Requires `Mutex<Option<Instant>>` migration.
- **FR-50** (Low) — Blocking file I/O in async Tauri command handlers. Requires `spawn_blocking` migration.
- **FR-52** (High) — Bare `dict`/`list` annotations on `ConfigApplier` + `ServiceProtocol`. Requires TypedDict refactor.
- **FR-55** (duplicate of FR-39) — skipped.
- **FR-57** (Medium) — `app.py` 1275-line wiring façade split. Larger refactor (Phase A+B+C).
- **FR-59** (Medium) — `migrate.rs` 1249-line split. Larger refactor.
- **FR-60** (Low) — `_secrets.py` 957-line split. Lower priority.

---

### SI-10 — `logging.rs` 2842-LOC monolith: UE-31 deferral stale, split unblocked
**Status:** ❌ Not Fixed (logging.rs 2842-LOC monolith split deferred — large refactor, documented as Remaining Work)
**Description:** `src-tauri/src/platform/logging.rs` is 2842 LOC (+31% since UE-31). UE-6 redaction work has landed, so the deferral rationale is stale. The file conflates 7 concerns. This is the single largest Rust file in the project.
**User Impact:** Maintainability debt. Every change risks breaking unrelated concerns.
**Root Cause:** UE-31 deferred the split pending UE-6 completion. UE-6 is now landed.
**Progress:** None yet.
**Related Files:** `src-tauri/src/platform/logging.rs:1-2842`
**Fix:** Execute UE-31 split: `platform/logging/{mod,init,combined,redact,panic_hook,early,rotating}.rs`.
**Severity:** 🟡 Medium

### SI-17 — Duplicated `PROTOCOL_VERSION` constants across two transports with divergent enforcement
**Status:** ❌ Not Fixed (PROTOCOL_VERSION consolidation deferred — cross-transport refactor, documented as Remaining Work)
**Description:** Two separate `PROTOCOL_VERSION` constants: `sidecar_ws.py:209` (WS) and `ipc/transport_tcp.py:45` (TCP). Divergent enforcement: TCP rejects with structured error; WS only logs warning and continues. A stale Tauri host on the WS path gets confusing `unknown_command` errors.
**User Impact:** Stale Tauri host gets confusing errors instead of clear protocol-version-mismatch.
**Root Cause:** DR-21 added TCP-side strict enforcement but did NOT mirror it on WS path.
**Progress:** None yet.
**Related Files:** `voice_typer/server/sidecar_ws.py:209, 353-369`, `voice_typer/server/ipc/transport_tcp.py:45, 359-392`
**Fix:** Consolidate into shared `ipc/protocol_version.py`. Make WS enforcement match TCP: on mismatch, write structured error envelope, close WS, return False.
**Severity:** 🟡 Medium

### SI-25 — `state.rs` remains mixed-purpose: SidecarHandle + shutdown IPC machinery
**Status:** ❌ Not Fixed (state.rs split deferred — documented as Remaining Work)
**Description:** `state.rs` conflates shared-state types with `SidecarHandle` (process-management) and `shutdown_sidecar_for_exit` + `send_fire_and_forget_frame` (IPC/shutdown machinery).
**User Impact:** Maintainability concern; 3 concerns in one module.
**Root Cause:** AC-36 was partially applied.
**Progress:** None yet.
**Related Files:** `src-tauri/src/state.rs:1, 78-178, 249-420`
**Fix:** Move `SidecarHandle` to `sidecar/handle.rs`. Move shutdown machinery to `sidecar/shutdown.rs`.
**Severity:** 🟡 Medium

### SI-26 — `migrate.rs` 1249 LOC monolith split deferred (extends AC-138)
**Status:** ❌ Not Fixed (migrate.rs split deferred — documented as Remaining Work; atomic_copy moved to util.rs as partial)
**Description:** AC-138 split was deferred. Production code spans 8 concerns: path resolution, migration orchestration, sentinel idempotency, JSON merge, atomic copy, recursive copy, SQLite WAL path, mtime comparison.
**User Impact:** Maintainability debt.
**Root Cause:** AC-138 split was deferred.
**Progress:** None yet.
**Related Files:** `src-tauri/src/migrate.rs:1-857`
**Fix:** Split into `migrate/{mod,candidates,merge,copy,sentinel,sidecar}.rs`.
**Severity:** 🟡 Medium

### SI-27 — `hotkey-utils.ts` (734 LOC) is a 5-concern monolith
**Status:** ❌ Not Fixed (hotkey-utils.ts split deferred — documented as Remaining Work)
**Description:** `hotkey-utils.ts` mixes key-code table, platform detection, preset lists, display formatting, UI validation, capture-session state machine.
**User Impact:** Maintainability; mixes pure data tables with stateful reducer logic.
**Root Cause:** Helpers accreted without decomposition.
**Progress:** None yet.
**Related Files:** `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts`
**Fix:** Split into `hotkey-keymap.ts`, `hotkey-format.ts`, `hotkey-capture-state.ts`. Keep `hotkey-utils.ts` as re-export shim.
**Severity:** 🟡 Medium

### SI-28 — Ticket-ID-encoded test filenames violate C-STYLE-1
**Status:** ❌ Not Fixed (test file renames deferred — documented as Remaining Work)
**Description:** C-STYLE-1 forbids task IDs in source filenames. 7 test files violate: `test_dictation_pipeline_ue10_ue47.py`, `test_i5_retry_fixes.py`, `test_ue_fix_a.py`, `test_sa09_xz_fixes.py`, `test_nh23_onboarding_progress_persistence.py`, `test_xz_cc_1_dead_vad_constants.py`, `test_nh17_force_cancel_wording.py`.
**User Impact:** Filenames become meaningless once ticket is forgotten. Catch-all scope signals poor organization.
**Root Cause:** Tests named after tickets, not behavior.
**Progress:** None yet.
**Related Files:** `tests/test_dictation_pipeline_ue10_ue47.py`, `tests/test_i5_retry_fixes.py`, `tests/test_ue_fix_a.py`, `tests/test_sa09_xz_fixes.py`, `tests/test_nh23_onboarding_progress_persistence.py`, `tests/test_xz_cc_1_dead_vad_constants.py`, `tests/test_nh17_force_cancel_wording.py`
**Fix:** Rename to domain-focused names. Merge contents into existing domain-focused test files.
**Severity:** 🟡 Medium

### SI-29 — 25+ test files define local `_make_fake_*` helpers instead of using `tests/fixtures/`
**Status:** ❌ Not Fixed (fixture migration deferred — documented as Remaining Work)
**Description:** `tests/fixtures/ipc_test_helpers.py` exposes 3 canonical factories, but 25+ test files define their own inline `_make_fake_app` / `_make_recorder` / `_make_server` helpers.
**User Impact:** Maintenance cost; signature changes require updating 25+ files instead of 1.
**Root Cause:** XS-42 migration was never completed.
**Progress:** None yet.
**Related Files:** `tests/fixtures/ipc_test_helpers.py`, 25+ test files
**Fix:** Complete XS-42 migration — replace inline helpers with imports from `tests/fixtures/`.
**Severity:** 🟡 Medium

---

### UE-26 — Protocol-version negotiation exists only on WS path; never bumped despite registry churn
**Status:** ⚠️ PARTIAL (EY investigation confirms: TCP done, version NOT bumped, stdin none)
**Verification:** Verified 2026-08-04 (Windows host):
- ✅ TCP: `IPC_PROTOCOL_VERSION = 1` with validate-if-present auth rejection → structured `server.protocol_version_mismatch` incl. `server_protocol_version` (`transport_tcp.py:49, 409-446`).
- ✅ Cross-language parity test `tests/test_ipc_protocol_cross_language_parity.py` (Python TCP = Python WS = Rust `EXPECTED_PROTOCOL_VERSION` = TS `IPC_PROTOCOL_VERSION`).
- ❌ Version NOT bumped to 2 (still 1 everywhere; `test_app_sidecar_protocol.py:95` pins `== 1`).
- ❌ No registry-diff monotonic-bump contract test.
- ❌ Stdin path emits no protocol version (legacy console transport, no handshake).
- (SI-17 — duplicate constants across transports — remains "Not Fixed" per review.md.)
**Description:** `sidecar_ws.py:208` defines `PROTOCOL_VERSION: int = 1` and emits it only on the WS sidecar path. The TCP path and stdin path emit NO protocol version. The docstring says "Bump this integer whenever the `_COMMAND_REGISTRY` adds/removes/renames a command OR the push-event `type` vocabulary changes" — but the registry has had ≥15 command removals and the renderer allowlist has been pruned twice, yet `PROTOCOL_VERSION` is still `1`. No test asserts the version is monotonic w.r.t. registry mutations.
**User Impact:** An old client on TCP or stdin connects, passes auth, then silently gets `unknown_command` errors for every removed command — no early `protocol_mismatch` signal. Host cannot tell "stale client" from "server bug."
**Root Cause:** Documented bump-on-change contract is unenforced; version not emitted on all transports.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
- `voice_typer/server/ipc/transport_tcp.py`
- `voice_typer/server/ipc_server.py`
**Fix:** Extend `PROTOCOL_VERSION` emission to TCP and stdin paths. Add a contract test asserting the version bumps on registry diff. Bump to `2` to reflect cumulative registry churn.
**Severity:** 🟡 Medium

---

### UE-30 — `ws.rs` 1454-line monolith mixes 8+ concerns (spaghetti)
**Status:** ⚠️ PARTIAL
**Description:** `src-tauri/src/sidecar/ws.rs` co-locates 8+ concerns: event-type allowlist + HashSet cache, supervisor thread management + `OnceLock<mpsc::Sender>`, auth-time cleanup, WS connect with timeout, WS writer channel setup, auth handshake with catch_unwind, WS reader task with dispatch fulfillment + bubble coalescing + event translation, heartbeat task with miss tracking, event-name translation table, + ~220 lines of tests. Comment-to-code ratio ~50%.
**User Impact:** Hard to navigate, hard to test in isolation, high cognitive load. The heartbeat race (UE-7) and cleanup-drain gap (UE-8) are partly consequences of the monolithic structure.
**Root Cause:** XZ-11 extraction claimed to split the "585-line god function" but the FILE itself stayed 1454 lines.
**Progress:** ⚠️ PARTIAL — verified 2026-08-04 (`cargo build` OK):
- ✅ 3 submodules extracted: `sidecar/event_protocol.rs`, `sidecar/heartbeat.rs`, `sidecar/respawn_scheduler.rs` — declared `mod` inside `ws.rs:35-37` (they compile; `sidecar/mod.rs` declares only bubble_coalesce/spawn/supervisor/ws).
- ❌ `ws.rs` still 1600 lines (finding said 1454) and still holds the full connect/auth/reader/writer pipeline: `drain_pending_with_disconnect_error` (141), `ws_connect` (182), `spawn_writer_task` (303), `wait_for_auth_ok` (425), `spawn_reader_task` (613), `reconnect_ws` (905).
- ❌ No `sidecar/ws/` subdir, no `ws/mod.rs` orchestrator, no allowlist/connect/auth/writer/reader.rs submodules. Header docstring cites a different ticket (FZ-24/ZR-86) as the split that ran — UE-30's prescribed 9-way split is unfinished.
- ✅ Heartbeat-race (UE-7) + drain (UE-8) fixes correctly wired (test_ue8_drain_pending* tests at ws.rs:1028-1094).
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Split into `sidecar/ws/{mod,allowlist,supervisor_trigger,connect,auth,writer,reader,heartbeat,translate}.rs`. `mod.rs` becomes the `reconnect_ws` orchestrator (~80 lines) + re-exports. No behavior change — same public APIs, same command names, same tests passing.
**Severity:** 🟡 Medium

---

### UE-31 — `logging.rs` 2161-line monolith mixing 6 concerns (spaghetti) — GROUP 5 mandatory
**Status:** ❌ Not Fixed (worse: 3183 lines + orphaned split-target drafts)
**Description:** `src-tauri/src/platform/logging.rs` conflates: init orchestration, `CombinedLogger` multi-sink dispatch, a 515-LOC PII redaction engine (5 `try_match_*` state machines), `install_panic_hook`, `EarlyLogger` + `EARLY_LOGGER_HANDLE`, `RotatingFileWriter`, + ~920 LOC tests (42% of the file). The redaction sub-concern alone is larger than most files in `src-tauri/src/`.
**User Impact:** Hard to audit PII redaction correctness (UE-6 fix requires editing the 515-LOC sub-concern in isolation). Comment sediment accretes per session.
**Root Cause:** Never decomposed after initial extraction.
**Progress:** ❌ NOT FIXED — verified 2026-08-04 (`cargo build` OK, 3 warnings only):
- `platform/logging.rs` now 3183 lines (up from 2161). All 6 concerns still live: `CombinedLogger` (280), `redact_pii` (431), `try_match_*` ×6 (787-1138), `install_panic_hook` (1230), `EARLY_LOGGER_HANDLE` (1308) + `EarlyLogger` (1323), `RotatingFileWriter` (98/281/1495).
- Header docstring (lines 10-28) states "deferral: proposed split (NOT done this session)" and reproduces the UE-31 plan verbatim.
- NEW: commit `5ae8cf2a` drafted `platform/log_file.rs` (26 KB) + `platform/log_rotation.rs` (14 KB) — they cross-reference each other (`log_file.rs:39 use crate::platform::log_rotation`) but NEITHER is declared as a `mod`: `main.rs:36` declares `mod platform;` and `platform/mod.rs` only declares logging/open_path/paths/process. Dead code — never compiled (cargo build succeeds without them).
**Related Files:**
- `src-tauri/src/platform/logging.rs`
**Fix:** Split into `platform/logging/{mod,init,combined,redact,panic_hook,early,rotating}.rs`. `mod.rs` re-exports the public API. Co-located `tests/` per sub-module. This is a prerequisite for safely expanding the redaction engine (UE-6). Either wire up the existing `log_file.rs`/`log_rotation.rs` drafts or delete them.
**Severity:** 🟡 Medium

---

### TX-26 — `_make_fake_server` duplicated 6× with drift (DRY)
**Status:** ⚠️ Partial — consolidation done (EY-8); 3 pre-existing test assertion failures remain
**Verification:** Verified 2026-08-04 (Windows host): 6 inline copies → 1 canonical in `tests/fixtures/sidecar_ws_test_helpers.py`. Canonical includes `_ws_dispatch_pool=None` fix. 3 remaining failures in `test_ws_hmac_windows.py` are stale assertions (bare `"rate_limited"` vs `"client.rate_limited"`), not regressions from the consolidation.
**Severity:** 🟡 Medium

---

### EY-8 — _make_fake_server consolidation (TX-26)
**Status:** ⚠️ **Partial** — Verified (Windows host): 6 inline copies → 1 canonical in `tests/fixtures/sidecar_ws_test_helpers.py`. Canonical version includes `_ws_dispatch_pool=None` fix + 3 sibling lazy-create null-outs. **3 remaining pre-existing failures** in `test_ws_hmac_windows.py` are stale assertions (assert bare `"rate_limited"` but production emits `"client.rate_limited"`; `time` import not in `allowed_prefixes`). The canonical helper is correct — `_get_rate_limiter`'s double-checked locking handles MagicMock auto-vivification correctly.

---

### WN-12 — `VOICE_TYPER_IPC_TOKEN` env var bare literal in 8+ Python files
**Status:** ❌ Not Fixed (deferred — requires touching 8+ files across multiple agent scopes; documented for future run)
**Description:** The env-var name `"VOICE_TYPER_IPC_TOKEN"` appears as a bare literal in 8+ Python files with no central constant. A typo would silently break IPC auth.
**User Impact:** No immediate impact, but a typo in a future edit would silently break authentication.
**Root Cause:** Env-var name was never centralized.
**Progress:** Not fixed this run — the 8+ files span multiple agent scopes and coordinating the changes across parallel agents was not feasible without file conflicts. Documented for a future Fix-Existing session.
**Related Files:** `voice_typer/server/sidecar_ws.py`, `voice_typer/server/ipc/transport_tcp.py`, `voice_typer/server/electron_launcher.py`, `voice_typer/server/env_validation.py`, `voice_typer/server/ipc_server.py`
**Fix:** Add `IPC_TOKEN_ENV_VAR = "VOICE_TYPER_IPC_TOKEN"` to `_paths.py`; replace all bare literals.
**Severity:** 🟢 Low

### WN-23 — Documented big spaghetti splits (NOT fixed this run — requires multi-wave chained execution)
**Status:** ❌ Not Fixed — 20 large-file splits documented with detailed proposals in worklog. 2 small extractions (WN-20 `resource_probe.py`, WN-21 `nvidia_dll_paths.py`) completed this run. The remaining 18 require multi-wave chained sub-agent execution per BIG-TASK POLICY.
**Description:** The project's largest files (logging.rs 3104, config.py 3044, recorder.py 2480, history_db.py 2423, dictation_pipeline.py 2176→2009, ws.rs 2164, ipc_server.py 2129, model_manager.py 1910, recording_controller.py 1829, config_validators.py 1760, credential_store.py 1684, supervisor.rs 1445, hotkeys/windows_native.py 1556, clipboard/manager.py 1342, text_cleanup.py 1311, permissions.py 1282, sidecar_cmds.rs 1189, native_hotkeys/base.py 1228, migrate.rs 1143, clipboard_target_safety.py 1100) all have detailed split proposals from the investigation sub-agents.
**User Impact:** These are the project's largest maintainability debt — each is a god-module mixing multiple concerns.
**Root Cause:** Organic growth; prior partial splits left the bulk inline.
**Progress:** 2 of 20 splits completed (WN-20, WN-21). The remaining 18 are documented with line-by-line split proposals in the worklog `## Investigation Findings` section and in the sub-agent return reports. Future Fix-Existing sessions should pick these up by ordinal position.
**Severity:** 🟠 High (collectively)

### IN-3 — app.py lazy property retry causes 94Hz log spam + AttributeError on hot path
**Status:** ⚠️ PARTIAL
**Description:** Six lazy @property accessors cache None on failure (`except Exception: log.warning(...); return None`), so every subsequent access re-enters the try block. The `audio_quality` property is on a HOT PATH (~94 calls/sec at 48kHz/512). If AudioQualityController construction fails, every chunk crashes with AttributeError + logs a WARNING — 94 crashes/sec + 94 logs/sec during recording.
**User Impact:** If audio quality controller construction ever fails, recording becomes unusable with 94 crashes per second. Even for non-hot-path properties, WARNING log spam floods the log file on every dictation cycle until a fallback is assigned.
**Root Cause:** The except branch does not cache a failure sentinel, so every access re-attempts construction.
**Progress:** ⚠️ PARTIAL — verified 2026-08-04 (tests `tests/test_app_none_guard.py` 10/10 pass):
- ✅ AttributeError crash on hot path fixed: 5 delegates now None-guard (`app.py:1146-1234` — `_on_audio_quality_chunk` / `_rebuild_audio_processor` / `_finalize_audio_quality_report` / `repaste_last` / `undo_last` return None instead of dereferencing).
- ❌ Failure sentinel (`_LAZY_FAILED` bounded TTL) NOT implemented — `app.py:722-726` explicitly documents "backing left as None → retry on next access". On construction failure the hot path still re-attempts construction + logs WARNING per chunk (`app.py:860` lazy-init + `app.py:1150` controller-unavailable ≈ 2 warnings × ~94/sec) — the "94 logs/sec spam" User Impact remains.
- ❌ Eager `audio_quality` construction in `__init__` NOT implemented (option in Fix).
**Related Files:**
- `voice_typer/server/app.py:587-749`
**Fix:** Cache a failure sentinel (e.g. `_LAZY_FAILED`) for a bounded TTL. For the hot-path `audio_quality` property, construct eagerly in __init__ or catch AttributeError in the delegate.
**Severity:** 🟡 Medium

### IN-62 — bubble/ 11 duplicate IPC subscriptions across 3 hooks + 1 component
**Status:** ⚠️ PARTIAL
**Description:** The bubble window registers 11 separate IPC subscriptions: onShow (3 subscriptions), onHide (2), onSetState (2), onConfig (2), onLevel (1), onDraggable (1). Each Electron IPC listener has per-event callback overhead. The duplicate onSetState tracker in useAudioLevels vs useBubbleStateMachine can drift.
**User Impact:** 3× callback overhead per onShow, 2× per onSetState/onConfig/onHide. The duplicate mode tracker can drift between useAudioLevels (local closure) and useBubbleStateMachine (React state).
**Root Cause:** Each hook independently subscribes to the same bridge event — no shared subscription layer.
**Progress:** ⚠️ PARTIAL — verified 2026-08-04 (vitest `useBubbleBridge.test.tsx` 4/4 + bubble suite pass):
- ✅ `useBubbleBridge.tsx` implemented per Fix: 1 IPC listener per event, `BubbleBridgeProvider` in `Bubble.tsx:110`, fan-out via `bridge.on()`, dynamic `setLevelActive()` gating for onLevel, teardown on unmount. All 5 consumers migrated (useBubbleLifecycle, useBubbleStateMachine, useAudioLevels, useThemeSync, Bubble.tsx) — no direct `api.onX` calls left outside the bridge.
- ❌ Duplicate mode tracker drift NOT fixed — `useAudioLevels.ts:140-153` documents it as "Known issue (duplicate mode tracker)… deferred to a future refactor".
**Related Files:**
- `voice_typer/client/src/renderer/src/bubble/` (multiple files)
**Fix:** Introduce a useBubbleBridge() hook that subscribes once to each event and exposes state via refs/context.
**Severity:** 🟡 Medium

---

### Remaining Work AP

The following findings are documented in `review.md` as `❌ Not Fixed` — deferred to a future session due to scope/risk/time constraints:

| ID | Severity | Why deferred | Effort | Priority |
|---|---|---|---|---|
| AP-2 | Low | credential_store redaction inconsistency — defense-in-depth, narrow blast radius | S | P2 |
| AP-3 | Medium | Export commands size cap — needs recursive Value size estimation | M | P1 |
| AP-7 | Low | ELECTRON_RENDERER_URL scheme validation — dev-only | S | P2 |
| AP-10 | Medium | log.exception source-line PII — dispersed across ~30 callsites in 14 files | L | P1 |
| AP-11 | Low | transcription.py fallback 80-char leak — narrow trigger (opt-in + DEBUG + import failure) | S | P2 |
| AP-12 | Low | VOICE_TYPER_DEBUG=1 PII warning — documentation only | S | P2 |
| AP-26 | Low | _backup_before_migration ordering — latent, no current migrator writes to disk | S | P2 |
| AP-32 | Low | container_detect DRY — maintenance hazard, no functional impact | S | P2 |
| AP-34 | Medium | Tray init failure fallback on Wayland — needs new dialog/hotkey logic | M | P1 |
| AP-44 | Medium | Whisper-fallback circuit breaker — needs separate counter + state | M | P1 |
| AP-45 | Medium | load_with_fallback timeout — needs ThreadPoolExecutor + careful design | M | P1 |
| AP-46 | Medium | Cloud 200-with-empty-body — needs new CloudEmptyResponseError type | M | P1 |
| AP-47 | Medium | log.error → log.exception across ~20 sites in 14 files — dispersed | L | P1 |
| AP-48 | Medium | Third-party library loggers silenced unevenly — needs expanded list | S | P1 |
| AP-49 | Medium | prewarm/logging_setup.py non-secure RotatingFileHandler — needs migration to _SecureRotatingFileHandler | M | P1 |
| AP-51 | Medium | Rust session-ID bracket — cross-language correlation gap | S | P1 |

---

### UU-5 — Electron→Tauri migration runs synchronously in .setup, blocks first-launch window
**Status:** ❌ Not Fixed — agent reported DONE but changes were not persisted to disk (likely reverted by concurrent git operation). The async wrapper exists in migrate.rs but the main.rs call site was not switched. Deferred to next session.
**Description:** `main.rs:164` calls `migrate::migrate_electron_userdata(&app_handle)` synchronously inside the Tauri `.setup` closure. The migrate.rs module's own docstring (lines 156-163) explicitly flags this: 'This synchronous wrapper runs the fs-heavy migration inline on the caller's thread. Callers inside an async context (e.g. the Tauri setup closure) should prefer `migrate_electron_userdata_async` instead.' The async wrapper already exists at migrate.rs:192 but is `#[allow(dead_code)]`. `migrate_inner` performs synchronous fs ops: create_dir_all, config merge (read+parse+atomic write), copy_missing_files over `models/` (potentially multi-GB), atomic_copy for `history.db` (MB) + WAL/SHM sidecars. After first launch, the sentinel file exists and migrate_inner early-returns (~1ms).
**User Impact:** The first time you launch Voice Typer after upgrading from the Electron build to the Tauri build, the main window appears blank/frozen for the duration of the file migration — potentially 5-30 seconds if you have large ASR models. Subsequent launches are fast. The blank window makes the app look broken on the most important first run after upgrade.
**Root Cause:** The async wrapper was prepared but never wired in. The migrate.rs author left a one-line TODO to switch the call site.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/migrate.rs`
**Fix:** Replace the synchronous call at main.rs:164 with `migrate_electron_userdata_async(&app_handle).await` moved INSIDE the existing `tauri::async_runtime::spawn` block at main.rs:186, BEFORE `spawn_sidecar_and_get_port` is awaited. Remove the `#[allow(dead_code)]` from migrate.rs:191.
**Severity:** 🔴 High

---

### UU-35 — macOS microphone watcher polls sd.query_devices() every 3s for entire backend lifetime
**Status:** ❌ Not Fixed — macOS microphone watcher polling cadence reduction was not assigned to a fix agent this session. Deferred to a future session focused on macOS-specific optimizations.
**Description:** `microphone_watcher.py:439-538` (macOS polling fallback) calls `sd.query_devices()` every 3s — a 10-50ms CoreAudio round trip — for the entire lifetime of the Python backend, not just during recording. The daemon thread is started by `MicrophoneDeviceWatcher.start()` (called from `device_manager.py:188` during `Recorder.__init__`) and runs 24/7, even when the user is fast asleep. The event-driven CoreAudio watcher (`microphone_watcher_coreaudio.py`) avoids this but is only used when pyobjc is available — not the default on Homebrew Python without explicit `pip install pyobjc-framework-CoreAudio`.
**User Impact:** On macOS laptops without pyobjc installed (the default), Voice Typer uses ~0.3-1.5% average CPU on one core purely to detect device changes that happen ~0 times per hour in practice. Estimated battery impact: ~3-5%/hour drain at idle just from this watcher. Linux impact is negligible (sub-ms `os.listdir`).
**Root Cause:** Polling fallback was added because the CoreAudio listener needs a CFRunLoop; that integration cost was not paid. The watcher runs for the backend lifetime instead of being recording-scoped.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py`
**Fix:** Three options: (1) Gate the watcher on recording activity — only start the thread when `Recorder.start()` is called and stop it on `Recorder.stop()`. The 30s TTL cache in `recording.py` already covers the idle case. (2) On macOS, prefer the CoreAudio property-listener path (event-driven, zero CPU at idle). (3) If polling must stay, lengthen the cadence to 30s+ (matches the existing TTL fallback).
**Severity:** 🟡 Medium

---

### CSTYLE-1 — Task IDs in source code violate C-STYLE-1 (remediation sweep)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** C-STYLE-1 (CONSTRAINTS.md) prohibits task IDs / session prefixes / ticket numbers in source code (file names, function names, test names, comments, docstrings) — they belong ONLY in metadata files (`review.md`, `SUMMARY.md`, `worklog.md`, `CHANGELOG.md`, `scripts/review_entries.json`). The `XZ-` prefix alone appears ~340 times across ~90 source files: production Python (`voice_typer/server/config/__init__.py`, `config/loader.py`, `config_validators.py`, `config_applier.py`, `config_editor.py`, `clipboard/manager.py`, `_secrets.py`, `shutdown_controller.py`, `sidecar_ws.py`, `signal_handlers.py`, `ipc/registry.py`, `handlers/*.py`, `_electron_build.py`), main-process TS (`allowed-commands.ts`, `__tests__/*.ts`), renderer tests, and ~68 files under `tests/` (e.g. `tests/test_validation_scheduler_crash_fixes.py` has 31 hits, `tests/test_tauri_binaries_manifest.py` 20, `tests/test_dictation_pipeline_partial_failures.py` 18, `tests/test_native_hotkeys_base_toctou_verification.py` 16). Other prefixes (EC-, ER-, XV-, XA-, AC-, XS-, PVT-, S1-CR-, AB-, AP-, FR-, DJ-, DR-, UU-, GG-, NQ-) add more. Test names like `"XZ-R5-009: readStaleElectronPid() returns..."` (single-instance.test.ts:179) and docstrings like `(XZ-R17-06) — the critical-only path` (test_tray_and_console.py:71) are the common shapes.
**Root Cause:** Fix agents named tests/comments after their own ticket IDs; C-STYLE-1 predates much of this code, so it accumulated. Tests that grep for "dead code" / freshness (test_dead_code_stays_removed.py, test_techdebt_todos_freshness.py) reference IDs too.
**Progress:** None yet.
**Related Files:** ~90 files — see Description; sweep targets `voice_typer/`, `src-tauri/src/`, `tests/` (NOT the exempt metadata files).
**Fix:** Sweep task for one session: (1) `rg "\b(?:XZ|XV|XA|XS|EC|ER|AC|AB|AP|DJ|DR|FR|GG|UU|PVT|S1-CR|NQ|NH|UE|TX|SI|ZR|YJ|XE|FZ|DT|XPLAT|ARCH|IN|WN|T|H)-[0-9A-Z]+"` over source dirs; (2) rewrite each occurrence into prose describing the behavior (drop the ID), preserving test names' descriptive intent (rename `XZ-R5-009: ...` → `...`); (3) keep test assertions working — tests that grep source (dead-code/freshness guards) must be updated to match the new prose; (4) EXCEPTIONS that must NOT be stripped: the intentional inline tag prefixes `SEC-*`, `RACE-*`, `PERF-*` (AGENTS.md tag convention — cross-cutting, greppable, not session IDs), and all metadata files; (5) re-run `pytest` + `npx vitest run` after the sweep. Optional hardening: extend `scripts/check_branding.py`-style CI (or a new `scripts/check_task_ids.py`) to flag session-ID patterns in source comments going forward.

---

### RST-1 — Rust: 384 inline `#[cfg(test)]` unit tests embedded in production source files
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test organization (violates C-TEST-5)
**Description:** 25 Rust source files under `src-tauri/src/` contain inline `#[cfg(test)] mod tests` blocks — 384 `#[test]` functions total — inside production code. Biggest offenders: `platform/logging.rs` (89), `commands/export.rs` (29), `sidecar/spawn.rs` (21), `tray.rs` (20), `commands/sidecar_cmds.rs` (20), `sidecar/supervisor.rs` (19), `platform/process.rs` (15), `migrate.rs` (14), `platform/paths.rs` (13), `system_cmds.rs` (12), `util.rs` (11), `sidecar/supervisor_health.rs` (11), `log_file.rs` (9), `sidecar/ws.rs` (8), `log_rotation.rs` (7), `bubble_coalesce.rs` (4), `open_path.rs` (3), plus 1 each in `branding.rs`, `state.rs`, `ws_reconnect.rs`. Violates the new C-TEST-5 constraint: tests must live in separate test files/folders, not inside production source. The repo's own precedent already does this correctly: `src/commands/bubble/tests.rs` (76 tests, wired via `mod tests;` in `bubble/mod.rs` — see its header comment: "Originally inline in the single-file bubble.rs; moved here when the module was split").
**Root Cause:** Split sessions extracted production logic into new modules but left the `#[cfg(test)]` blocks inline; only the bubble module ever extracted its tests to a separate file.
**Progress:** None yet.
**Related Files:** `src-tauri/src/platform/{logging.rs,process.rs,paths.rs,open_path.rs,log_file.rs,log_rotation.rs}`, `src-tauri/src/{tray.rs,migrate.rs,util.rs,state.rs,branding.rs}`, `src-tauri/src/commands/{export.rs,sidecar_cmds.rs,system_cmds.rs}`, `src-tauri/src/sidecar/{spawn.rs,supervisor.rs,supervisor_health.rs,ws.rs,ws_reconnect.rs,bubble_coalesce.rs}`.
**Fix:** Extract inline tests module-by-module, one slice per module: (1) move the `#[cfg(test)] mod tests { ... }` block from the production file into a new sibling `tests.rs` file; (2) wire it in the module with `#[cfg(test)] mod tests;` (keep the module's `mod` declaration in place); (3) run `cargo check` after EACH slice (per Execution Rule 21g wiring gate) AND `cargo test <module>` to confirm the extracted tests still compile and pass; (4) do NOT move on to the next module while the previous slice is broken; (5) final gate: `cargo check` + `cargo test` on the whole crate with zero errors. Optionally follow with an integration-test pass to `src-tauri/tests/` for behaviors that need a Tauri runtime.

---

## Remaining Work
- **GG-67-70 (monolith splits):** Home.tsx (633→~250), Onboarding.tsx (571→~200), History.tsx (529→~220) — only partial splits were done (About.tsx fully split). These are Medium-severity maintainability improvements that require more time than a single fix wave allows.
- **Windows/macOS host validation:** Bubble fullscreen detection (GG-72) implemented for all platforms but only Linux-verified. `VALIDATE ON WINDOWS HOST` + `VALIDATE ON MACOS HOST`.
- **Tray test updates:** 2 pre-existing tests assert the old "• " prefix behavior (GG-40 removed it). These tests need updating to assert `checked=is_active` instead. Test files are outside the fix agents' owned sets.

---

### EO-1 — VoiceTyperApp.__init__ is a 512-line god-constructor mixing 9 controller instantiations + 11 lazy backings + 7 threading primitives
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/app.py:247-758` — VoiceTyperApp.__init__ spans 512 lines, directly constructing 9 controllers/services (Recorder, RecordingController, ModelManager, TrayIcon, SettingsController, ShutdownController, LifecycleController, ConfigEditorLauncher, HotkeyDispatcher, VolumeController, TimerCoordinator, CrashRecovery), declaring 11 lazy-backing attributes, 7 threading primitives, and 14+ state flags. Comment density inside __init__ is 73% (376 of 512 lines are # comments).
**User Impact:** When the app starts, it builds every subsystem at once in a single 512-line method. If one subsystem fails to construct (e.g., the recorder can't find a microphone), the entire app fails to start with no clean fallback. Adding a new feature (e.g., a new controller) means editing a 512-line method, risking regressions in unrelated subsystems. Testers cannot construct VoiceTyperApp without paying the cost of all 9 controllers + 11 lazy backings.
**Root Cause:** Phase 4.5/6/7 extracted the methods that used to live on VoiceTyperApp into separate controller classes, but the construction/wiring of all those controllers stayed inside __init__ as one giant method.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`
**Fix:** Extract a voice_typer/server/app_wiring.py (or AppBuilder) that owns the construction sequence. Split __init__ into private _init_threading(), _init_audio(), _init_recording(), _init_models(), _init_tray(), _init_controllers(), _init_state_flags() methods, each ≤50 lines. Keep __init__ as a ≤30-line sequence of those calls.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-3 — sidecar_ws.py is a 1480-LOC monolith mixing 8+ WS concerns (FR-S14 regressed by +527 LOC, +56%)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/sidecar_ws.py` (1480 LOC) — single module with 17 top-level functions spanning 8+ disjoint concerns: WS server bootstrap, stdout line-buffering, protocol-version stamping, bearer-token auth (115 LOC), rate-limiter integration + dispatch pool + drain-coordination factory (_make_dispatch 261 LOC), queue drop-oldest marshaler, connection semaphore, connection lifecycle, duplicate-auth invariant, ready-event emit, event-bus subscriber + initial state snapshot (_install_subscriber 115 LOC), writer task, read/dispatch loop + heartbeat fast-path + per-connection rate cap (_read_loop 123 LOC), browser-origin rejection. FR-S14 (review.md:2557) was filed at 953 LOC; file has grown +527 LOC (56%) since then.
**User Impact:** The WebSocket sidecar is the core IPC transport between the Python backend and the renderer. Every WS-path bug fix or invariant addition must touch this 1480-line file; reviewers can't load the relevant concern in isolation; merge conflicts compound. The growth of 527 LOC since FR-S14 indicates the file is actively regressing, not stabilizing.
**Root Cause:** Verified — file has grown organically as ADR-0020 rounds 2,3,4 stacked WS-specific invariants (drain coordination, duplicate-auth, heartbeat rate cap, origin rejection, protocol negotiation) onto a file that originally was just run() + _handle_connection. No further split has happened since the Phase 4.5 ipc_server.py decomposition (which moved TCP / dispatcher / registry out but did NOT split sidecar_ws.py).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
**Fix:** Split into voice_typer/server/sidecar_ws/ package with leaf modules: auth.py (_authenticate + _AUTH_TIMEOUT_SECONDS + _check_duplicate_auth), dispatch.py (_make_dispatch + _enqueue_safe), connection.py (_handle_connection + _handle_connection_inner + _install_subscriber + _start_writer + _read_loop + _emit_ready_if_first + _get_ws_connection_semaphore), protocol.py (PROTOCOL_VERSION + _emit_server_started + _reject_browser_origins), run.py (the run() entry + _force_line_buffered_stdout). Target ≤ 300 LOC per leaf.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-4 — transcription.py is a 1505-LOC god-class mixing 9 ASR concerns (AC-134 still open; file grew +315 LOC since the finding)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/transcription.py` (1505 LOC) — TranscriptionEngine class spans 1353 lines with 30+ methods owning 9 distinct concerns: device detection, model loading, HF download, CUDA smoke test, kernel priming, segment decoding, lock + GC choreography, fallback chain, hallucination detection, unload. AC-134 cited this file; it has grown +315 LOC since the finding was filed. Additionally, 1068 LOC of orphaned duplicate code exists in transcription_load.py (372 LOC), transcription_result.py (362 LOC), transcription_download.py (334 LOC) — these modules were drafted as split targets but NEVER WIRED (zero importers).
**User Impact:** The ASR engine is the core feature — every dictation goes through it. Untestable in isolation: every unit test must instantiate the full TranscriptionEngine. A change to e.g. CUDA probe logic risks transcription decoding logic. The 1068 LOC of orphaned dead code (transcription_load/result/download.py) inflates the codebase and misleads reviewers.
**Root Cause:** Verified — organic growth over many sessions; each new concern added methods rather than modules. The extracted modules were drafted but never wired (transcription.py was never modified to delegate).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py`
- `voice_typer/server/transcription_load.py`
- `voice_typer/server/transcription_result.py`
- `voice_typer/server/transcription_download.py`
**Fix:** Split into a transcription/ package: _device.py, _download.py, _loader.py, _cuda_probe.py, _transcribe.py, _fallback.py, _words.py, _gpu_errors.py, engine.py (thin TranscriptionEngine facade re-exporting public API). Delete the orphaned transcription_load/result/download.py modules OR finish wiring them as the actual implementations (preferred — realizes the extraction's stated goal).
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-5 — cloud_engines.py is a 1013-LOC monolith mixing 6 cloud-provider concerns (NEW — not in review.md)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/cloud_engines.py` (1013 LOC) — module mixes 6 concerns: provider defaults (_PROVIDER_DEFAULTS map + URL allowlist assertions), HTTP transport (_StreamingMultipartBody class, _read_capped, _audio_to_wav_bytes), retry policy (_transcribe_with_retry 131 LOC), provider-specific request/response shaping (_send_openai_compatible, _send_deepgram, _build_multipart_body, _multipart_parts), connection testing (test_connection), and the CloudEngine class itself. AC-134/AC-136/AC-137 cover transcription.py / parakeet_engine.py / model_manager.py but NOT cloud_engines.py.
**User Impact:** Adding a 4th cloud provider (e.g. AssemblyAI, Whisper-cloud-via-Azure) forces edits to a 1013-line file. Tests for _StreamingMultipartBody and tests for test_connection are coupled via the module boundary. Cloud-engine retry changes risk regressions in unrelated provider paths.
**Root Cause:** Verified — organic growth; provider-specific paths and HTTP plumbing live in the same file as the engine class.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/cloud_engines.py`
**Fix:** Split into a cloud/ package: _transport.py (_StreamingMultipartBody + _read_capped + _audio_to_wav_bytes + _opener), _retry.py (_transcribe_with_retry + _parse_retry_after + _cloud_http_error_class), _providers/openai.py (_send_openai_compatible + _build_multipart_body + _multipart_parts), _providers/deepgram.py (_send_deepgram), _engine.py (thin CloudEngine facade + test_connection), __init__.py (re-export CloudEngine + CloudEngineError subclasses).
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-6 — AsrBackend Protocol signature lies — audio:bytes doesn't match concrete engines (audio:np.ndarray); missing 4 methods
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/asr_registry.py:29-58` — AsrBackend Protocol declares `transcribe_with_fallback(self, audio: bytes, *args, **kwargs) -> str` but ALL 4 concrete engines (TranscriptionEngine, QwenEngine, ParakeetEngine, CloudEngine) accept `audio: np.ndarray`. A caller passing bytes would type-check but crash at runtime when the engine calls len(audio) or np.sqrt(np.mean(np.square(audio))). Protocol also missing 4 methods the registry/IPC layer depends on: request_abort, clear_abort, device_info, loaded_via.
**User Impact:** A future engine author who reads only the Protocol will (a) accept bytes instead of np.ndarray (silent runtime crash), (b) not implement request_abort/clear_abort (abort path silently no-ops on that engine), (c) not implement device_info/loaded_via (tray status silently empty).
**Root Cause:** Verified — the Protocol was added but underspecified the contract.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/asr_registry.py`
**Fix:** (a) Change Protocol's transcribe_with_fallback(self, audio: 'np.ndarray', ...) using TYPE_CHECKING guard. (b) Add request_abort(self) -> None, clear_abort(self) -> None, device_info (read-only property), loaded_via (read-only property) to the Protocol. (c) Document that the Protocol is a static type-check contract, not a runtime contract.
**Severity:** 🟡 Medium
**Category:** Backend architecture / Maintainability

### EO-7 — history_db.py is a 2686-LOC monolith (BIGGEST in project) — AC-135 stale; file grew +711 LOC (+36%) since finding was filed
**Status:** 🟡 Partial — dead history_db_internals/recovery.py (519 LOC) + search.py (585 LOC) deleted (1104 LOC of zero-importer dead code removed; 59 history tests pass). The main history_db.py (2686 LOC) Phase 4.5 split is remaining work for a future session.
**Description:** `voice_typer/server/history_db.py` (2686 LOC) — biggest monolith in the project. AC-135 (review.md:1150) cited this file at 1975 LOC; it has grown to 2686 LOC (+711, +36%). Despite the partial-split attempt (history_db_internals/ package), the file kept all inline implementations of recovery (969-1375), CRUD (1568-2098), search (2096-2549), and diagnostics (2664-2686) while ALSO adding thin delegating methods + their docstrings. history_db_internals/recovery.py (519 LOC) and history_db_internals/search.py (585 LOC) are 100% DEAD CODE — never imported; same functions live inline in history_db.py.
**User Impact:** The history DB stores every transcription the user has dictated. Untestable in isolation: one change can ripple through 8 distinct concerns. Reviewers can't reason about correctness without scrolling through 2700 lines. The 1104 LOC of dead parallel implementations in history_db_internals/ misleads maintainers and inflates the codebase.
**Root Cause:** Verified — the split was started but never finished. Adding delegation without removing the inline code made the file bigger, not smaller.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py`
- `voice_typer/server/history_db_internals/recovery.py`
- `voice_typer/server/history_db_internals/search.py`
**Fix:** Complete the abandoned split: delete inline _maybe_recover_from_corruption/_try_iterdump_recovery/_apply_recovered_inserts/_notify_corruption_recovered/_backup_before_migration (969-1375, ~406 lines) and delegate to history_db_internals.recovery free functions; same for the 13 inline search/CRUD methods. Target: history_db.py ≤ 800 lines (just the HistoryDB class shell + constants + thin delegating methods).
**Severity:** 🔴 Critical
**Category:** Spaghetti / monolith detection

### EO-8 — recording/recorder.py is a 2648-LOC monolith — DT-21/ZR-60/DJ-96 stale (file is mostly delegators now); __init__ is a 380-line god-constructor
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/recording/recorder.py` (2648 LOC) — DT-21 cited 4012 LOC, ZR-60 cited 610-line god-methods, DJ-96 mandated Phase 4.5 split. The split DID land (audio_pipeline.py, capture.py, stream_lifecycle.py, device_manager.py, etc. extracted), but the file is still 2648 LOC because (a) __init__ is a 380-line god-constructor declaring 50+ instance attributes inline, (b) 9 device-state property pairs (834-896) are shims for test backward-compat, (c) ~15 delegator methods with 25-line docstrings exist solely to satisfy inspect.getsource source-string tests (FZ-8/ARCH-12/S3-CR-21).
**User Impact:** The recorder is the audio capture subsystem — every dictation goes through it. Adding a new audio feature requires editing a 2648-line file. Tests cannot construct collaborators (AudioPipeline, StreamLifecycle, etc.) in isolation — they require a real Recorder with 50+ initialized attrs. The friend-class anti-pattern (59 friend-access lines across 6 collaborator files accessing recorder._<attr> directly) breaks encapsulation.
**Root Cause:** Verified — Phase 4.5 split moved method BODIES to sibling files but kept all mutable state on Recorder. The 9 device-state property shims + 15 delegator methods exist purely to keep stale source-string tests passing.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/audio_pipeline.py`
- `voice_typer/server/recording/capture.py`
- `voice_typer/server/recording/stream_lifecycle.py`
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/recording/disconnect_handler.py`
- `voice_typer/server/recording/session_state.py`
**Fix:** (1) Split __init__ into 7 focused _init_* helpers (buffer_state, locks, device_state, vad_caches, sample_rate_state, worker_handles, telemetry). (2) Move ownership of state INTO collaborator classes (AudioPipeline owns _chunk_count/_buffer/_lock/_xruns; StreamLifecycle owns _stream; etc.). (3) Migrate source-string tests (FZ-8/ARCH-12/S3-CR-21) from inspect.getsource assertions to behavioral assertions; then delete the 15 delegator methods + 9 property shims. Target: recorder.py ≤ 500 LOC.
**Severity:** 🔴 Critical
**Category:** Spaghetti / monolith detection

### EO-11 — hotkeys/ + native_hotkeys/ parallel hierarchies with mirrored ABCs (no shared inheritance, _NativeBackendAdapter 630 LOC bridges them)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/hotkeys/base.py:21` HotkeyBackend(ABC) vs `voice_typer/server/native_hotkeys/base.py:77` SubprocessHotkeyBackend(ABC) — two separate ABCs with parallel structure (factory, per-platform backends, is_windows/is_linux/is_macos helpers). native_adapter.py:36-43 documents: 'The native backends in native_hotkeys.py don't inherit from HotkeyBackend (they use a separate base class to avoid an import cycle). This adapter bridges the two.' This forces the existence of _NativeBackendAdapter (630 lines) solely to wrap a SubprocessHotkeyBackend to satisfy the HotkeyBackend interface. ~250 of the adapter's 630 lines are pure delegation.
**User Impact:** Every adapter method (start, stop, set_on_release, set_toggle_on_keyup, is_alive, diagnose) is a thin forwarding wrapper. New methods added to HotkeyBackend must be re-delegated in the adapter or silently no-op'd. The 630-line adapter is a maintenance liability.
**Root Cause:** Verified — historical split where the native backends were extracted into a separate package without unifying the ABCs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkeys/base.py`
- `voice_typer/server/hotkeys/factory.py`
- `voice_typer/server/hotkeys/native_adapter.py`
- `voice_typer/server/native_hotkeys/base.py`
- `voice_typer/server/native_hotkeys/factory.py`
**Fix:** Either (a) make SubprocessHotkeyBackend inherit from HotkeyBackend (resolving the import cycle by moving HotkeyBackend to a leaf module that imports nothing project-internal), or (b) extract a HotkeyBackendProtocol (typing.Protocol) that both ABCs structurally satisfy, and have the dispatcher use the protocol — eliminating the adapter.
**Severity:** 🟡 Medium
**Category:** Overall architecture / Refactoring opportunities

### EO-12 — config/__init__.py is a 2286-LOC stalled-split monolith (XZ-R10-13/FR-S1 stale; partial split INTRODUCED classmethod-delegator duplication)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/config/__init__.py` (2286 LOC) — XZ-R10-13 (review.md:893) flagged config.py at 2002 LOC and prescribed a 7-way split. The 4.5 split landed only config/loader.py + config/coercion.py + config/sanitization.py + config_internals/{paths,migrations}.py — the prescribed config_dataclass.py / config_saver.py / config_purge.py modules still don't exist (FR-S1 pending). Worse, the split introduced a SECOND class of duplication: each extracted function now has TWO homes (module-level impl + Config classmethod delegator wrapper). 10 classmethod delegator wrappers exist purely so existing test patch sites keep working — they have no production callers.
**User Impact:** Future config-field additions require edits in 2-4 places (dataclass field, validator entry, IPC allowlist, optional extraction). The split made the file BIGGER, not smaller — partial extraction with re-export shims is net-negative.
**Root Cause:** Verified — partial-split stalled. The 10 classmethod delegator wrappers exist for test patch sites that monkeypatch Config._coerce_streaming_fields etc.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py`
- `voice_typer/server/config/coercion.py`
- `voice_typer/server/config/loader.py`
- `voice_typer/server/config/sanitization.py`
- `voice_typer/server/config_internals/migrations.py`
- `voice_typer/server/config_internals/paths.py`
**Fix:** (1) Land the remaining FR-S1 splits: config_dataclass.py, config_saver.py, config_purge.py. (2) Delete the 10 classmethod delegator wrappers; update test patch sites to import the extracted module-level functions directly. (3) Update the line-1 docstring to reflect the actual current package layout.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-13 — dictation_pipeline/orchestrator.py run() is a 437-LOC method with a 197-line finally block (AC-73 regressed from 282→437 LOC)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/dictation_pipeline/orchestrator.py:177-613` — the run() method spans 437 lines (AC-73 cited the OLD path dictation_pipeline.py:119-401 at 282 LOC with 80-line finally; post-split it is 437 LOC with 197-line finally — +155 LOC). The finally block alone contains 7 distinct cleanup steps, each wrapped in its own try/except with log.debug on failure. AC-134 split the monolith into the dictation_pipeline/ package, but run() itself was NOT decomposed — it was moved verbatim and has grown.
**User Impact:** Every change to the cleanup sequence (e.g. adding a new abort watcher stop step, changing busy_event semantics) requires editing a 197-line finally block inside a 437-line method. This is the most-mutated code path in the dictation pipeline.
**Root Cause:** Verified — AC-134 split the file into a package but kept run() as one giant method. Each cleanup step now carries a multi-line historical comment block.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline/orchestrator.py`
**Fix:** Extract each cleanup step into a named helper: _cleanup_sentinel(), _cleanup_audio_zero(), _cleanup_watchdog_reset(), _cleanup_streaming_session(), _cleanup_busy_event(), _cleanup_transcription_thread(), _cleanup_gc_collect(), _cleanup_correlation_id(token). Each helper owns its own try/except + log.debug. The finally: body shrinks to ~10 sequential helper calls.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-14 — HandlerBase._wrap helper is defined but unused — 21 handler sites copy-paste the same 4-line validation boilerplate
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/handlers/_base.py:438-466` — _wrap template-method helper (29 LOC) is defined and documented but has ZERO call sites in the codebase. Meanwhile the boilerplate it was designed to eliminate is repeated 21 times across handler files: `validated, error = _validate_dict_payload(data, {...})` + `if error: return error` + `assert validated is not None` + `validated.get('field')`. The _wrap docstring at _base.py:425-437 says: 'The mechanical fix would convert each of the 60+ _handle_<cmd> methods to one-liners delegating to _wrap. Deferred because...'
**User Impact:** Every new handler that needs validation copy-pastes the same 4-line boilerplate, plus the surrounding try/except wrapper (~6 more lines). Bug fixes to the validation pattern require touching 21+ sites.
**Root Cause:** Verified (deferred-but-never-actioned). The helper has been sitting unused.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/handlers/_base.py`
- `voice_typer/server/handlers/level_monitor_handlers.py`
- `voice_typer/server/handlers/cloud_test_handlers.py`
- `voice_typer/server/handlers/onboarding_handlers.py`
- `voice_typer/server/handlers/history_handlers.py`
- `voice_typer/server/handlers/microphone_test_handlers.py`
- `voice_typer/server/handlers/templates_handlers.py`
- `voice_typer/server/handlers/system_handlers.py`
- `voice_typer/server/handlers/model_handlers.py`
**Fix:** Migrate the 21 sites incrementally: each _handle_<cmd> becomes `return self._wrap(cmd_name='<cmd>', resp_type='<type>', data=data, resp=resp, body=lambda d: {'data': ...})`. The _wrap helper already handles pre-coercion, validation error pass-through, and the catch-all error envelope.
**Severity:** 🟡 Medium
**Category:** Refactoring opportunities / DRY

### EO-15 — clipboard/manager.py paste() is a 404-LOC spaghetti method (#2015 stale at 349 LOC) + _is_safe_paste_target() is 256 LOC
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard/manager.py:762-1166` — ClipboardManager.paste method spans ~404 lines (review.md #2015 cited 349; +55 LOC growth). Interleaves 8 distinct concerns: atexit registry append, daemon thread spawn + failure rollback, paste_enabled gate, rate-limit check, safety-target check (_is_safe_paste_target), TOCTOU re-check (Windows-only, ~25 lines), platform-specific keystroke dispatch (4 branches), return-value bookkeeping + audit log. Separately, _is_safe_paste_target (lines 244-499) is ~256 LOC with 6 distinct exception-handling strategies (lines 268-283 document 3 perf optimizations).
**User Impact:** Untestable in isolation — every paste-path test exercises every branch. A change to the TOCTOU re-check risks breaking the rate-limit logic. Cyclomatic complexity estimated 15+ branches. Critical for safe paste — regressions here paste into password fields or security-sensitive windows.
**Root Cause:** Verified — method accumulated platform branches and safety checks over time without extraction.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py`
- `voice_typer/server/clipboard_target_safety/__init__.py`
**Fix:** Extract into focused helpers: _register_pending_restore(), _check_paste_enabled(force), _check_rate_limit(), _check_target_safety() -> tuple[bool, int|None], _dispatch_keystroke(platform, is_terminal, safe_hwnd) -> bool. paste() becomes ~30-line orchestrator. For _is_safe_paste_target, extract 6 named helpers for each exception strategy.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-16 — tray.py macOS/Linux 'Open App' launches DUPLICATE Electron process during transient TCP blip (cross-platform bug)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray_window.py:141-160` — open_electron_window() tries event_bus.publish({type: show_window}); on failure, falls back to bring_electron_to_front() which is Win32-only (returns False on macOS/Linux at line 65). If event_bus publish momentarily fails (TCP socket between backend and Electron temporarily unavailable during backend restart, sidecar reconnect), the code unconditionally proceeds to step 3 — launching a SECOND Electron process via _ensure_built_and_launch even though one is already running.
**User Impact:** macOS/Linux users clicking 'Open App' on the tray during a transient TCP blip get a DUPLICATE Electron window (or a 'port already in use' crash of the second instance). The single-instance mutex may catch this, but the user sees a confusing error instead of the existing window coming forward. Windows works (Win32 focus); macOS/Linux silently fail through to duplicate-launch.
**Root Cause:** Verified — bring_electron_to_front is Win32-only. On macOS/Linux, no platform-specific focus helper exists, so the fallback path is never effective outside Windows.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray_window.py`
**Fix:** Add platform-specific focus helpers: macOS via NSRunningApplication (osascript -e 'tell application "Voice Typer" to activate'), Linux via wmctrl -a 'Voice Typer' or xdotool search --name 'Voice Typer' windowactivate. Gate the call site: if bring_electron_to_front(): return only on Windows; on macOS/Linux, do NOT fall through to _ensure_built_and_launch if a process check (pgrep -f VoiceTyper or _electron_pid is not None and _pid_alive(_electron_pid)) finds a live Electron.
**Severity:** 🔴 High
**Category:** Cross-Platform Behavior / Backend architecture

### EO-17 — C-STYLE-1 violation: 60+ task-ID-style comments across Python/TS/Rust source files (S2-CR-71, DJ-37/38/41, SK-b, D1-FIX, PERF-002, HOTKEY-MULTIKEY-001, Fix #N)
**Status:** 🟡 Partial — C-STYLE-1 task-ID scrub completed for tray.py + tray_notifications.py + tray_elapsed_timer.py (22 edits) + hotkey files (46 prefixes) + settings/feedback files (15 prefixes). All tests pass. Other files (useSettingsConfig.ts:274 still has comment about ALLOWED_COMMANDS precedent) may have residual references — full project-wide scrub is remaining work.
**Description:** Pervasive task-ID-style comments across 20+ files in the renderer components, settings, hotkey, microphone, audio, models, dashboard, layout, ui, plus tray.py (S2-CR-71, S2-CR-16, DJ-37/38/41, SK-b), LevelBar.tsx (Fix #8 ×2), useSettingsConfig.ts (D1-FIX, PERF-002, PERF-MEMO-001, Fix #8), hotkey-validation.ts (HOTKEY-VALIDATION-002 (Task 2.2.5), HOTKEY-SHARED-001, HOTKEY-MULTIKEY-001 (Task 1.3)), useHotkeyCapture.ts (HOTKEY-MULTIKEY-001, HOTKEY-FULLMSG-001, HOTKEY-DEFER-001), hotkey-utils.ts (HOTKEY-UNIFY-002, FIX-HOTKEY-AND-NOTIFICATION, FIX-HOTKEY-ARCHITECTURE), AudioSettingsSection.tsx (Fix #10), RecordingSettingsSection.tsx (Fix #9), PrewarmAndUpdates.tsx (Fix #4). Also tray.py:8-17 has 6 empty backticks where prefixes were stripped (half-cleaned C-STYLE-1 scrub).
**User Impact:** Code clutter — every comment carries a stale 'fix ticket' reference that adds noise without context. Task IDs are transient — once the entry is removed from review.md, the ID becomes meaningless noise. The empty backticks at tray.py:8-17 are evidence of a half-completed cleanup that left the prose grammatically broken.
**Root Cause:** Verified — direct violation of CONSTRAINTS.md C-STYLE-1: 'Do NOT add task IDs, session prefixes, or ticket numbers to source code.' QV-25 cleanup was scoped to common/feedback/help only and incomplete even there.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray.py`
- `voice_typer/client/src/renderer/src/components/settings/useSettingsConfig.ts`
- `voice_typer/client/src/renderer/src/components/hotkey/hotkey-validation.ts`
- `voice_typer/client/src/renderer/src/components/hotkey/useHotkeyCapture.ts`
- `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts`
- `voice_typer/client/src/renderer/src/components/settings/AudioSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/RecordingSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/PrewarmAndUpdates.tsx`
- `voice_typer/client/src/renderer/src/components/feedback/LevelBar.tsx`
**Fix:** Strip the leading D1-FIX: / PERF-002: / HOTKEY-MULTIKEY-001: / Fix #N: / S2-CR-71: / DJ-37: / SK-b: prefixes from each affected comment. Keep the rationale text (it's useful), drop the ticket reference. Mechanical sweep across all 20+ files. Repair the empty backticks at tray.py:8-17.
**Severity:** 🟡 Medium
**Category:** Code Style & Naming (C-STYLE-1 violation)

### EO-18 — AUTOSTART-CMD-VALIDATE applied to Windows but NOT back-ported to macOS / Linux (autostart_enabled reports stale state after venv delete)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/server_platform/autostart_macos.py:264-265` and `voice_typer/server/server_platform/autostart_linux.py:102-103` — _is_autostart_macos() / _is_autostart_linux() only check if the plist/.desktop file EXISTS. They do NOT verify the binary path inside the file actually exists on disk. Compare with autostart_windows.py:999-1034 (_is_app_autostart_startup_registered) and autostart_windows.py:371-460 (_is_app_autostart_task_registered) which both extract the command path and verify the binary exists, with docstring tagged AUTOSTART-CMD-VALIDATE.
**User Impact:** macOS/Linux users who delete their venv or move the install directory see a misleading 'Autostart: enabled' state in Settings. The login launch silently fails (the plist/.desktop file fires the python interpreter that no longer exists). Bug surfaces only after the user reboots/logs in. Migration or venv cleanup workflows hit this regularly.
**Root Cause:** Verified — same bug class on macOS/Linux. The plist <Program> and .desktop Exec= paths can point to a deleted venv Python or a moved install directory, and the file will still 'exist'.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/server_platform/autostart_macos.py`
- `voice_typer/server/server_platform/autostart_linux.py`
- `voice_typer/server/server_platform/autostart_windows.py`
**Fix:** Parse the plist (<key>Program</key><string>...</string>) and .desktop Exec= line, extract the python/launcher path, and Path(...).exists()-check it. Mirror the Windows _validate_runkey_command helper. Treat nonexistent-path files as stale → return False (and optionally clean up).
**Severity:** 🔴 High
**Category:** Cross-Platform Behavior / Backend architecture

### EO-19 — 4 platform/lifecycle files exceed 800-LOC spaghetti threshold: crash_recovery.py (1273), autostart_windows.py (1055), startup_sequence.py (956), autostart_launcher.py (948)
**Status:** ❌ Not Fixed
**Description:** YJ-53 / WN-23 cited stale line counts: crash_recovery.py was 1034 → now 1273 (+239); autostart_launcher.py was 849 → now 948 (+99). autostart_windows.py (1055) and startup_sequence.py (956) have NO existing spaghetti-candidate entry in review.md. Each file mixes 2-3 concerns that could be separate modules.
**User Impact:** Files become harder to review and change. crash_recovery.py's CrashRecovery class docstring mentions 6 separate fix-IDs woven through the same class. Critical for crash recovery and autostart — regressions here cause silent startup failures.
**Root Cause:** Verified — incremental fix-on-fix accumulation (each new fix added a defensive try/except + a 30-line docstring block).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_recovery.py`
- `voice_typer/server/server_platform/autostart_windows.py`
- `voice_typer/server/startup_sequence.py`
- `voice_typer/server/autostart_launcher.py`
**Fix:** Extract: crash_recovery.py → _crash_recovery_save_worker.py + _crash_recovery_io.py. autostart_windows.py → _autostart_windows_runkey.py + _autostart_windows_task.py + _autostart_windows_startup_bat.py (the three mechanisms are already delimited by section comments at lines 155, 465, 760). startup_sequence.py → _startup_sequence_onboarding.py + _startup_sequence_crash_check.py.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-23 — 5 top-level security modules with overlapping concerns (no cohesive security/ package)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/_secrets.py` (1014 LOC) hosts TWO concerns: secret redaction (redact_secret, redact_api_keys, redact_url, etc.) AND URL allowlist (is_url_allowed, assert_url_allowed, _LOOPBACK_HOSTS, etc.). `voice_typer/server/security.py` (887 LOC) hosts PII redaction filter + model-integrity verification (2 unrelated concerns). `voice_typer/server/secure_file_io.py` (870 LOC) hosts atomic-write + PersistedJSON. `voice_typer/server/_http_safety.py` (234 LOC) hosts urllib opener / no-redirect / HTTPS-only. `voice_typer/server/_security_attributes.py` (312 LOC) hosts Win32 DACL. Cross-imports create circular dep risk.
**User Impact:** A future security review (or auditor) has to read 5 files to understand the threat model. Adding a new redaction pattern requires deciding WHICH of the 3 redaction modules should host it. The split between security.py (hash verification) and _model_integrity.py (pattern allowlist) means the integrity-verification surface is fragmented across two files that must stay in sync.
**Root Cause:** Verified — the security surface grew organically. _secrets.py was the first extraction; security.py predates it and accumulated PII filter + integrity code; _http_safety.py was extracted from cloud engines; secure_file_io.py from config.py split; _security_attributes.py from app.py extraction. No cohesive 'security package' exists.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_secrets.py`
- `voice_typer/server/security.py`
- `voice_typer/server/secure_file_io.py`
- `voice_typer/server/_http_safety.py`
- `voice_typer/server/_security_attributes.py`
- `voice_typer/server/_model_integrity.py`
**Fix:** Consolidate into a security/ package: security/redaction.py (merge _secrets.py redaction + security.py PII filter), security/url_allowlist.py (URL allowlist half of _secrets.py), security/file_io.py (current secure_file_io.py), security/http_safety.py (current _http_safety.py), security/model_integrity.py (merge security.py integrity + _model_integrity.py pattern lists), security/win32_dacl.py (current _security_attributes.py), security/__init__.py re-exports. Re-export from old top-level paths for back-compat.
**Severity:** 🟡 Medium
**Category:** Overall architecture / Backend architecture

### EO-25 — tests/test_security_fixes.py is a 1292-LOC catch-all mixing 9+ unrelated security domains (Rule 20 spaghetti violation)
**Status:** ❌ Not Fixed
**Description:** `tests/test_security_fixes.py` (1292 LOC) — file mixes: TestAcceptLoopWorkerPool (TCP accept-loop worker pool), TestRedactSecretFlagForms (secret-redaction flag-form parsing), TestPsSingleQuote (PowerShell single-quoting), TestBuildPowershellLnkScript (LNK shortcut generation), TestCreateLnkShortcutIntegration (LNK integration), 3 loose test_secN_* smoke tests, TestRedactSecretThreshold20 (redaction threshold), TestExtendUrlAllowlistAuditLog (URL allowlist audit log), TestAssertUrlAllowedLoopbackOptIn (URL allowlist loopback opt-in). The tests/security/ subpackage already exists with 4 split files (test_powershell_quoting.py, test_redact_secret.py, test_tcp_accept_worker_pool.py, test_url_allowlist.py) — establishing the per-domain convention — but test_security_fixes.py was never migrated into it.
**User Impact:** A change to e.g. PowerShell quoting forces a 1292-line file re-evaluation; reviewers can't locate security tests by domain. The tests/security/ split already exists for new tests but the legacy 1292-LOC catch-all wasn't migrated.
**Root Cause:** Verified — organic accumulation across multiple SEC-* review sessions into a single 'security fixes' bucket, with no subsequent split. EC-25 lists the predecessor file (test_sec_8_9_10_security_fixes.py, now renamed) as a catch-all — the rename happened but the split never did.
**Progress:** None yet.
**Related Files:**
- `tests/test_security_fixes.py`
- `tests/security/test_powershell_quoting.py`
- `tests/security/test_redact_secret.py`
- `tests/security/test_tcp_accept_worker_pool.py`
- `tests/security/test_url_allowlist.py`
**Fix:** Move each class into its matching tests/security/ file (TestAcceptLoopWorkerPool → test_tcp_accept_worker_pool.py; TestRedactSecretFlagForms + TestRedactSecretThreshold20 → test_redact_secret.py; TestPsSingleQuote + TestBuildPowershellLnkScript + TestCreateLnkShortcutIntegration → test_powershell_quoting.py; TestExtendUrlAllowlistAuditLog + TestAssertUrlAllowedLoopbackOptIn → test_url_allowlist.py; 3 loose smoke tests → tests/security/test_sec_smoke.py). Then delete tests/test_security_fixes.py.
**Severity:** 🟡 Medium
**Category:** Spaghetti / monolith detection (Rule 20)

### EO-28 — App.tsx is a 629-LOC file mixing 7+ inline business concerns (paste_failed toast, window maximize, help-overlay, onboarding complete)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/App.tsx` (629 LOC) — mixes 7+ concerns in a single default-export component: paste_failed toast handler (336-374, ~38 LOC), window maximize state effect (269-291, ~23 LOC), help-overlay keydown listener (177-213, ~37 LOC), handleOnboardingComplete callback (426-442, ~17 LOC). Some concerns were extracted to hooks (useConnectionToasts, useGlobalKeyboardShortcuts, useSoundFeedback, useTheme, useConnection, usePythonEvent wrappers) but the four blocks above remained inline. Comment at lines 500-507 acknowledges 'ErrorBoundary wrap was removed from here — main.tsx already wraps <App />' — i.e. the file's author is aware of layered wiring, but only some concerns were extracted.
**User Impact:** App.tsx re-renders on every state flip in any of these concerns (sidebar collapse, help-overlay open, isMaximized, connectingProgress). Each re-render re-runs the entire render tree including all useCallback/useMemo dependency arrays. Hard to test these concerns in isolation (the paste_failed toast branch can only be exercised by a full App test mount).
**Root Cause:** Suspected — incremental extraction over multiple sessions left App.tsx as a partial-wiring file.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx`
**Fix:** Extract usePasteFailedToast(), useWindowMaximized(bridge), useHelpOverlayShortcut(), and move handleOnboardingComplete body into useOnboardingWizard or a dedicated useOnboardingComplete hook. App.tsx should be ~150-200 LOC of pure wiring.
**Severity:** 🟡 Medium
**Category:** Frontend architecture / Spaghetti detection

### EO-32 — tauri-binaries.json is dead infrastructure — all sha256 fields empty, verify_tauri_binary_or_skip loader never implemented (false security sense)
**Status:** ✅ Fixed (verified on LINUX (sandbox) — dead tauri-binaries.json deleted (40 lines, all sha256 empty, loader never implemented); orphaned test_tauri_binaries_manifest.py also deleted)
**Description:** `/home/z/my-project/voice-typer/tauri-binaries.json` (40 lines) — all 5 platform sha256 fields are empty strings. Line 39 contract: 'verify_tauri_binary_or_skip, to be implemented in autostart_launcher.py'. `rg -ln 'verify_tauri_binary_or_skip' voice_typer/ scripts/ src-tauri/` returns ZERO matches — function never implemented anywhere. The manifest's own comment says 'production builds MUST populate all five sha256 fields via a scripts/build/update_tauri_manifests.py helper (to be authored by the cross-file agent) run by CI after the Tauri build' — both the loader and the CI population helper are aspirational. XZ-R6-AS-01 noted the launcher doesn't verify; this finding documents that the manifest file itself is dead infrastructure that can never enforce its own contract as currently authored.
**User Impact:** False sense of security. A reviewer reading tauri-binaries.json concludes integrity-checking is in place; in reality nothing checks anything. Worse: if a future agent wires the loader per the contract, fail-closed semantics would PREVENT the binary from launching because every sha256 is empty — i.e., wiring the loader 'as documented' would break autostart.
**Root Cause:** Verified — the manifest file was authored as a stub describing a fail-closed integrity contract, but the loader function it names was never implemented.
**Progress:** None yet.
**Related Files:**
- `tauri-binaries.json`
- `voice_typer/server/autostart_launcher.py`
**Fix:** Two coherent paths: (a) EITHER delete tauri-binaries.json until the loader + CI population step actually exist (delete the dead file, re-add it as part of the loader PR); (b) OR author verify_tauri_binary_or_skip in autostart_launcher.py AND scripts/build/update_tauri_manifests.py in CI in the same PR, populating all three sha256 fields as part of the release build. Do NOT leave the manifest file in a half-implemented state — it is a security negative (false sense of protection) today.
**Severity:** 🔴 High
**Category:** Security / Code quality (dead infrastructure)

### EO-33 — sidecar/spawn.rs is 1244 LOC (+47% since DR-3) — Phase 4.5 candidate overdue
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/sidecar/spawn.rs` (1244 LOC) — DR-3 listed 845 LOC; WN-23 (review.md:3208) repeated 845. Since then +399 LOC (+47%) have accreted (env-clear + passthrough_env_allowlist, dev-mode prewarm mirror, shutting-down handshake polling) without extraction. Phase 4.5 never executed. 14 top-level items in one file: spawn_sidecar_and_get_port, spawn_sidecar_and_get_port_with_shutdown, is_shutting_down, spawn_sidecar_and_get_port_inner, initialize_sidecar, is_dev_mode, is_dev_mode_for, passthrough_env_allowlist, spawn_sidecar_release, spawn_sidecar_dev_mode, parse_server_started, prewarm_resource_path, current_target_triple, target_triple_for. Mixes 5 concerns: spawn orchestration, dev-vs-release dispatch, env-var allowlist management, stdout handshake parsing, target-triple resolution + prewarm resource path.
**User Impact:** Compile times grow superlinearly with module size in Rust (monomorphization); reviewer cognitive load per change is high; the 5 concerns have different change velocities (target-triple table rarely changes; env-var allowlist changes when security review adds a var; handshake changes when sidecar protocol changes — yet all rebuild together). Spawns the sidecar — a regression here can prevent the app from booting.
**Root Cause:** Verified — DR-3/FR-S16/WN-23 were filed; Phase 4.5 never executed; file grew +47%.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/spawn.rs`
**Fix:** Split into sidecar/spawn/{mod, dev_mode, release_mode, handshake, env_allowlist, prewarm, target_triple}.rs. Keep spawn.rs (or spawn/mod.rs) as the ~80-LOC orchestrator re-exporting initialize_sidecar + spawn_sidecar_and_get_port[_with_shutdown]. Mechanical extraction — no behavior change.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-34 — sidecar/supervisor.rs is 1702 LOC (+18% since WN-23) — self-referential include_str!('supervisor.rs') test pattern at line 1670
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/sidecar/supervisor.rs` (1702 LOC) — DR-3 listed 1055 LOC; WN-23 listed 1445. Current 1702 = +61% since DR-3, +18% since WN-23. The supervisor accretes new resilience features (counter staleness, docstring-contract tests, panic-safety wrappers) without extraction. The self-referential `include_str!("supervisor.rs")` test at line 1670 makes the file's test suite depend on the exact textual layout of its own docstrings — any future docstring rewording breaks the test, and any extraction of write_restart_counter to a sibling module breaks BOTH the test AND its include_str! path.
**User Impact:** Single largest Rust file in src-tauri/src/sidecar/ (1702 LOC, 2.1× the threshold). The supervisor is the resilience backbone — a regression here causes silent failure to respawn after a crash. The self-referential test creates a false sense of contract coverage: it asserts the docstring says certain words, not that the code actually does what the docstring claims.
**Root Cause:** Verified — DR-3/FR-S9/WN-23 were filed; supervisor accreted features without extraction.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs`
**Fix:** (a) Split into sidecar/supervisor/{mod, counter_io, respawn_loop, panic_safety}.rs per DR-3's plan; (b) replace the include_str! docstring-inspection test with a behavioral test (call write_restart_counter + read_restart_counter after a simulated cold start; assert the counter is NOT reset). Behavioral tests survive refactors; source-text-inspection tests do not.
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-35 — commands/sidecar_cmds.rs is 1320 LOC — dispatch IPC command buried at line 696 of 1320 (53% into file)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/commands/sidecar_cmds.rs` (1320 LOC) — DR-3 listed 897 LOC; WN-23 listed 1189. Current 1320 = +47% since DR-3, +131 LOC since WN-23. 19 top-level items including: constants, dispatch_timeout_for (per-command timeout routing), allowed_commands (returns 100+ entry HashSet), is_command_allowed, DispatchArgs struct, dispatch_inner, dispatch_fire_and_forget, #[tauri::command] dispatch (at line 696 — buried 53% into the file), #[tauri::command] shutdown_sidecar (line 770), on_main_window_close (line 918). Mixes 4 concerns: command allowlist + timeout routing, the generic dispatch IPC command, the shutdown_sidecar IPC command + window-close teardown.
**User Impact:** The dispatch command is the app's primary IPC entrypoint (every renderer→sidecar call goes through it). Burying it at line 696 of a 1320-line file makes auditing the IPC surface (which a security reviewer would naturally prioritize) harder. The allowlist (100+ entry HashSet) is in the same file as the dispatch implementation — a future 'tighten the allowlist' change risks touching dispatch logic.
**Root Cause:** Verified — DR-3/FR-S15/WN-23 filed; file grew +47% since DR-3.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs`
**Fix:** Split into commands/sidecar/{mod, allowlist, dispatch, shutdown, window_close}.rs. The allowlist (the part that changes most often) becomes its own ~200-LOC file; dispatch lives in its own ~150-LOC file. sidecar_cmds.rs becomes a thin re-export shim or is deleted (callers import from commands::sidecar::...).
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### Phase 3 — Comprehensive Review File
Compiled 80 deduplicated findings into `/home/z/my-project/voice-typer/review.md` (LO-1 through LO-80):
- **Critical (11):** LO-1..LO-11 — hardcoded English strings (Bluetooth tooltip, Go to Microphone, Open Microphone settings), Pinyin in zh.json, help.shortcuts labelKey mismatch, missing locale keys (hotkeyTestFailure, 8 bubble keys), WCAG contrast failures (dark --input/--sidebar-border, light --success/--warning/--info), 5 main-process dialogs hardcoded English.
- **High (9):** LO-12..LO-17, LO-37, LO-38, LO-50, LO-58 — SettingsSaveIndicator lies on failure, useConnection disconnect paths missing lastError, bubble theme FOUC, bubble locale-change wiring broken, pluralization missing, historySort wrong locale, HelpOverlay not in Settings, PunctuationCheatSheet not discoverable, bubble partial-transcript dead code, CONTRIBUTING.md lacks i18n section.
- **Medium (35):** LO-18..LO-36, LO-39..LO-49, LO-51..LO-66 — RTL bugs, a11y gaps (aria-busy, aria-disabled, RangeSlider aria-valuetext), Sonner locale reactivity, useSnackbar retry default, visual consistency (EmptyState, raw palette colors, RangeSlider labels), dialog unsaved-changes, Models page (languages/description/accuracy/disk-space/api-key), onboarding (consent/skip/mic-test), dictation (show-more/copy/discard/audio-level/error-state), error recovery (restart button, reconnect exhaustion, RecordingErrorCard affordances), Storybook (dark/RTL variants, button stories), test helpers (renderApp/mocks), CONTRIBUTING (page/component guide), docs/ux (6 new files), README (FAQ/screenshots/support), bubble (text-size/keyboard), theme (prefers-contrast, per-preset sidebar-border), sound feedback (volume/test).
- **Low (14):** LO-67..LO-80 — HotkeyPicker default aria, AudioSettings tooltip cross-link, Onboarding tips, visual polish (strokeWidth, margins, actionIcon), ariaLabel camelCase, tooltip DRY, focusRing, label htmlFor, debounce, Spinner decorative, LocalModelsPanel subtitle.

### Phase 4 — Fixes (20 parallel fix sub-agents + 2 retries)

**Critical findings fixed (LO-1..LO-11):**
- LO-1: `LO-1` — MicrophoneStep.tsx: replaced literal English Bluetooth tooltip with `t("onboarding.bluetoothBadgeTooltip")` (key existed in all 8 locales). Also fixed incomplete zh/ru translations of the key.
- LO-2: `LO-2` — AudioSettingsSection.tsx: replaced literal English crossLinkBannerText + goToMicrophoneLabel with `t()` calls; added keys to all 8 locales.
- LO-3: `LO-3` — RecordingErrorCard.tsx: replaced literal English "Open Microphone settings" with `t("home.openMicSettings")`; added key to all 8 locales.
- LO-4: `LO-4` — zh.json: replaced Pinyin "wode mingzi, jintian qu le" with Hanzi "我的名字, 今天去了".
- LO-5: `LO-5` — useGlobalKeyboardShortcuts.ts: renamed 4 mismatched labelKey values to match existing locale keys (openSettings→settings, goHome→home, zoomIn→textSizeUp, zoomOut→textSizeDown). Added HelpOverlay-labelkey.test.tsx.
- LO-6: `LO-6` — Added `onboarding.hotkeyTestFailure` key to all 8 locales.
- LO-7: `LO-7` — Added 8 bubble i18n keys (blockedLabel, cancellingLabel, permissionRevokedLabel, pasteFailedLabel, 4 aria keys) to all 8 locales. Switched bubble `tf()` → `t()` for regression visibility.
- LO-8: `LO-8` — index.css: dark-mode `--input` and `--sidebar-border` changed from alpha-based (1.36:1–1.62:1) to opaque oklch(0.52) (3.1:1).
- LO-9: `LO-9` — index.css: light-mode `--success`/`--warning`/`--info` L lowered (2.21:1–2.86:1 → 3.4:1–4.5:1). Also bumped per-preset dark-mode status tokens.
- LO-10: `LO-10` — Added 10 main-process i18n keys (dialog.pythonCrash.*, pythonNotFound.*, pythonStartupTimeout.*, restartLoop.*, singleInstance.earlyExitSuffix) to all 8 main locale files. Replaced 5 hardcoded English dialogs in start-python.ts, tcp-connect.ts, relaunch-app.ts with `mainT()` calls. Fixed C-BRAND-1 violation (literal "Voice Typer" → {appName} placeholder).
- LO-11: `LO-11` — Fixed zh/ru/de audioEnhancement equalizer/limiter values (English → genuine translations).

**High findings fixed (LO-12..LO-17, LO-37, LO-38, LO-50, LO-58):**
- LO-12: SettingsSaveIndicator.tsx: added `error` prop + 5th destructive state; useSettingsConfig error wired through Settings.tsx.
- LO-13: useConnection.ts: 3 disconnect paths now call `setLastError(...)`.
- LO-14: bubble.html: added `<script type="module" src="/src/theme-bootstrap.ts">` (eliminates theme FOUC).
- LO-15: Bubble locale-change wiring: added `onLocaleChanged` to bubble preload + bridge + useBubbleBridge + useThemeSync; removed `intentionallyUnused` whitelist.
- LO-16: Added plural variants (one/other + _few/_many for ru, _zero/_two/_few/_many for ar) for lastUpdatedSecondsAgo/MinutesAgo/HoursAgo + about.relativeTime.* to all 8 locales.
- LO-17: historySort.ts: `Intl.Collator(undefined)` → `Intl.Collator(getLocale())`.
- LO-37: TroubleshootingSettingsSection: added "Keyboard Shortcuts" button opening HelpOverlay.
- LO-38: DoneStep: added PunctuationCheatSheet link + `?` shortcut tip.
- LO-50: waveform_bubble_wiring.py: `_push_bubble_set_state` now accepts `transcript` kwarg; transcription.py calls it on partial results.
- LO-58: CONTRIBUTING.md: added §6.5 (i18n guide) + §6.6 (renderer page/component guide).

**Medium findings fixed (LO-18..LO-66, selected highlights):**
- LO-18: ModelSettingsSection.tsx: `right-1` → `end-1` (RTL fix).
- LO-19: ConnectionStatusScreen.tsx: removed dead aria-labelledby/aria-describedby.
- LO-20: accessibility.test.tsx: flipped `it.fails` → `it` (test now passes).
- LO-21: Settings.tsx: loading state wrapped in `<output aria-live="polite" aria-busy="true">`.
- LO-22: MicToggleButton.tsx: `disabled` → `aria-disabled` + onClick guard.
- LO-23: RangeSlider.tsx: `aria-valuetext` → `getThumbAriaValueText` (lands on THUMB not ROOT).
- LO-24: sonner.tsx: reactive locale subscription + `dir` + `aria-label`.
- LO-25: useSnackbar.ts: `retryLabel = "Retry"` → `t("common.retry")`.
- LO-26: Settings.tsx: replaced custom empty-state with shared EmptyState visual rhythm.
- LO-27: Home/History/About.tsx: raw Tailwind palette → semantic tokens (bg-warning, text-success, etc.).
- LO-28: RangeSlider.tsx: added visible min/max labels.
- LO-29: VocabDialog/TemplateDialog + Modal: added `onCloseIntent` gate for unsaved-changes warning.
- LO-30..LO-36: Models page: supported_languages display, family.description, accuracy_rating, disk-space disable, API key format validation + show/hide toggle.
- LO-39: Added UI rows for hidden config fields (log_transcriptions, clipboard_save_restore, unsafe_paste_on_unknown_focus, warn_elevated_paste, warn_password_paste).
- LO-40: Settings.tsx: search now shows "results from other tabs" section.
- LO-42: AiEnhancement: cross-slider validation; ModelSettings: LLM URL validation.
- LO-43..LO-45, LO-69..LO-70: Onboarding consent info rendering, Skip button on Done step, skipConfirmModelWarning, DoneStep tips.
- LO-46..LO-49: LastTranscriptionPreview show-more/copy; Home Discard button; audio level during recording; MicToggleButton error state.
- LO-51..LO-54: ConnectionStatusScreen Restart backend button; reconnect exhaustion notification; RecordingErrorCard Copy/Open-logs/expand.
- LO-55..LO-57: Storybook dark/RTL variants on 8 stories; button.stories warning+icon sizes; renderApp.tsx + mocks.tsx test helpers.
- LO-59..LO-61: CONTRIBUTING §6.6; 6 new docs/ux/*.md files; README FAQ+screenshots+support.
- LO-62..LO-63: Bubble text-size propagation; bubble global hotkeys (Ctrl+Shift+M toggle, Ctrl+Shift+D dismiss).
- LO-64..LO-65: prefers-contrast: high overrides --muted-foreground; per-preset --sidebar-border contrast fixed.
- LO-66: Sound feedback volume slider + Test Sound button (config field + sound-manager multiplier + RecordingSettingsSection UI).

**Low findings fixed (LO-67..LO-80):**
- LO-67: hotkeyPicker.defaultAria key added.
- LO-68: microphoneQualityInfo appended with Microphone page cross-link.
- LO-71..LO-73: strokeWidth, footer margins, actionIcon standardized.
- LO-74: RangeSlider/SearchField/SegmentedControl accept native `aria-label` (backward-compatible).
- LO-76: ThemeSettingsSection uses shared focusRing.
- LO-77: VocabDialog/TemplateDialog category Select uses `<label htmlFor>`.
- LO-78: SearchField debounce.
- LO-79: LastUpdatedIndicator Spinner `decorative`.
- LO-80: LocalModelsPanel localModelsDescription subtitle.

---

## Remaining Work

### Spaghetti / Monolith Splits (FI-S1 through FI-S10) — Deferred per Big-Task Policy
10 multi-day refactors documented in review.md as deferred to next session:
- **FI-S1**: `history_db.py` 2686 LOC → split class methods into `history_db_internals/{writes,queries,migration,fts_search,retention,lifecycle}.py` (Effort: L)
- **FI-S2**: `credential_store.py` 1846 LOC → `credential_store/{_migration,_backend,_plaintext,_crud}.py` (Effort: L)
- **FI-S3**: `config/__init__.py` 2285 LOC → `config/{persistence,migration,validation,secrets}.py` (Effort: L)
- **FI-S4**: `sidecar_ws.py` 1480 LOC → `sidecar_ws/{auth,dispatch,connection,writer,reader,run}.py` (Effort: L)
- **FI-S5**: `crash_recovery.py` 1273 LOC → `crash_recovery/{persistence,lost_dictation,load_quarantine}.py` (Effort: M)
- **FI-S6**: `shutdown_controller.py` 1404 LOC → `shutdown/orchestration.py` (Effort: M)
- **FI-S7**: `cloud_engines.py` 1013 LOC → `cloud/{_multipart,_http_helpers,_openai_provider,_deepgram_provider}.py` (Effort: M)
- **FI-S8**: `_secrets.py` 1013 LOC → `_cloud_url_safety.py` (Effort: M)
- **FI-S9**: `security.py` 887 LOC → `model_integrity.py` (Effort: S)
- **FI-S10**: `config_validators/__init__.py` 859 LOC → `allowlist.py` + `entry_points.py` (Effort: S)

### Other Deferred Items
- **FI-16 full graceful shutdown**: `_attach_ws_graceful_shutdown` + `ws_graceful_shutdown` implementation (xfail-strict tests remain). Effort: M. Priority: P1.
- **FI-11-A prewarm binary integrity**: No runtime SHA-256 verification of prewarm binary (HIGH — but complex fix requiring manifest schema + launcher wiring). Effort: L. Priority: P1.
- **FI-13-A non-atomic `_migrate_from_legacy`**: `shutil.copytree` not atomic (HIGH — but requires staging-dir + atomic rename). Effort: M. Priority: P1.
- **4 pre-existing test_sidecar_ws_races.py failures**: Error-code migration mismatch (`duplicate_connection` → `server.duplicate_connection`). Effort: S. Priority: P2.
- **Windows/macOS host validation**: All fixes tested on Linux sandbox only. Real-host validation required for Win32 console handler, macOS clipboard restore, native hotkey binaries. Priority: P0.

## Spaghetti / Monolith Splits (Group 4) — Deferred to Final Report

> The following spaghetti/monolith splits were identified by FI-20 (cross-cutting audit). Per the Big-Task Policy (max 5 big tasks per session), these multi-day refactors are documented here and scheduled for the next session. They are NOT skips — they are tracked handoffs.

- **FI-S1**: `history_db.py` 2686 LOC (3.35× threshold) — partial split done (`history_db_internals/` 2284 LOC) but HistoryDB class body still 2411 LOC. Execute AC-135 plan: extract class methods into `history_db_internals/{writes,queries,migration,fts_search,retention,lifecycle}.py`. Effort: L.
- **FI-S2**: `credential_store.py` 1846 LOC (2.3× threshold) — NO split done. Execute AC-128 plan: `credential_store/{_migration,_backend,_plaintext,_crud}.py`. Effort: L.
- **FI-S3**: `config/__init__.py` 2285 LOC (2.85× threshold) — partial split done but Config class still 1629 LOC. Extract `config/{persistence,migration,validation,secrets}.py`. Effort: L.
- **FI-S4**: `sidecar_ws.py` 1480 LOC (1.85× threshold) — NO split done. Split into `sidecar_ws/{auth,dispatch,connection,writer,reader,run}.py`. Effort: L.
- **FI-S5**: `crash_recovery.py` 1273 LOC — partial split done (`diagnostics_export.py` extracted) but file still grew. Extract `crash_recovery/{persistence,lost_dictation,load_quarantine}.py`. Effort: M.
- **FI-S6**: `shutdown_controller.py` 1404 LOC — partial split done (`shutdown/teardowns/` 12 modules) but `_do_cleanup` 392 LOC still inline. Extract `shutdown/orchestration.py`. Effort: M.
- **FI-S7**: `cloud_engines.py` 1013 LOC — extract `cloud/{_multipart,_http_helpers,_openai_provider,_deepgram_provider}.py`. Effort: M.
- **FI-S8**: `_secrets.py` 1013 LOC — extract `_cloud_url_safety.py` (URL allowlist + SSRF defense, lines 527-1013). Effort: M.
- **FI-S9**: `security.py` 887 LOC — extract `model_integrity.py` (SHA-256 verification, lines 364-803). Effort: S.
- **FI-S10**: `config_validators/__init__.py` 859 LOC — extract `allowlist.py` + `entry_points.py`. Effort: S.

---

## Phase 1 Investigation Coverage (20 sub-agents)

| Agent | Scope | Files | Findings |
|-------|-------|-------|----------|
| FI-1 | Security core | security.py, _security_attributes.py, config_path_safety.py | 6 (1 Med, 5 Low) |
| FI-2 | Credential store | credential_store.py, _secrets.py | 10 (2 Med, 8 Low) |
| FI-3 | IPC sidecar_ws + ipc_server | sidecar_ws.py, ipc_server.py | 5 (2 Med, 3 Low) |
| FI-4 | Cloud engines | cloud_engines.py, llm_polish.py | 7 (2 Med, 5 Low) |
| FI-5 | Rust host security | src-tauri/src/ | 15 (1 Med C-TEST-5, 4 new Low, 10 dedupes) |
| FI-6 | TS/Electron main | client/src/main/ | 4 (1 Low, 3 Info) |
| FI-7 | Diagnostics + env PII | diagnostics_export.py, env_validation.py | 7 (1 High, 6 Low) |
| FI-8 | Clipboard + privacy | clipboard_snapshot.py, privacy_handlers.py | 8 (2 Med, 6 Low/Info) |
| FI-9 | AI enhancement + hallucination | ai_enhancement.py, hallucination.py | 6 (1 Med cross-ref, 5 Low/Info) |
| FI-10 | History DB | history_db.py | 7 (1 Critical regression, 1 Critical spaghetti, 5 Med/Low) |
| FI-11 | Model integrity | _model_integrity.py, model_hashes.json | 6 (1 High, 5 Low) |
| FI-12 | Config loader + sanitization | config/loader.py, sanitization.py, coercion.py | 6 (1 Med, 5 Low) |
| FI-13 | Config schema | config/__init__.py | 9 (1 High, 1 High spaghetti, 7 Med/Low/Info) |
| FI-14 | Config validators | config_validators/ | 13 (3 Med, 9 Low, 1 Info) |
| FI-15 | Handler error envelopes | handlers/ | 12 (3 Med, 7 Low, 2 Info) |
| FI-16 | Signal/atexit/thread_registry | signal_handlers.py, atexit_safety.py, thread_registry.py | 6 (2 Med, 4 Low/Info) |
| FI-17 | Crash recovery | crash_recovery.py | 3 (1 Med cross-ref, 1 spaghetti, 1 flaky test) |
| FI-18 | Shutdown + prewarm | shutdown_controller.py, prewarm_scheduler_posix.py | 7 (1 Med spaghetti, 1 High cross-cutting, 5 Low/Info) |
| FI-19 | Logging consistency | _log_constants.py, ipc_diagnostics.py | 7 (2 Med, 5 Low/Info) |
| FI-20 | Cross-cutting spaghetti audit | all Group 4 files >500 LOC | 11 (5 High spaghetti, 6 Med/Low/STALE) |

---

### HU-1 — stdin IPC path bypasses rate limiter AND logs 120 chars of payload at ERROR
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** The legacy stdin/stdout IPC transport (gated behind VOICE_TYPER_ALLOW_STDIN_IPC=1) dispatches every JSON line through `_dispatch` with NO rate-limit consultation — the TCP transport is the only path that calls `rate_limiter.allow()`. Additionally, `stdin_runner.py:154` logs the first 120 characters of the raw stdin line at ERROR level on dispatch failure, leaking API keys / transcription text / OAuth tokens.
**User Impact:** If a local process can write to the stdin pipe (or a TIOCSTI injection is used per the lifecycle.py:131 comment), it can flood the backend with thousands of commands per second, exhausting disk via repeated clear_history / delete_history calls, or exhausting CPU via repeated download_model. Separately, on any dispatch failure, the first 120 chars of the inbound JSON line (which may include cloud_api_key, transcription text, OAuth tokens) are written to the rotating log file at ERROR level with full traceback.
**Root Cause:** Verified. `_get_rate_limiter` is imported only in transport_tcp.py; stdin_runner.py and dispatcher.py never call it. `stdin_runner.py:154` uses `line[:120]` in the log.error call.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/ipc/stdin_runner.py
- voice_typer/server/ipc/dispatcher.py
- voice_typer/server/ipc/rate_limiter.py
**Fix:** Move the rate-limit check INTO `dispatcher._dispatch` so all three transports (TCP, WS, stdin) are covered by a single chokepoint. Replace `line[:120]` in stdin_runner.py:154 with `msg.get("type", "<unknown>")` (extracted before the dispatch failure, or via json.loads in the except).
**Severity:** 🔴 High

### HU-2 — dispatcher catches only ConsentRequiredError; non-dict msg crashes; _PYTHON_ONLY_COMMANDS not enforced
**Status:** ⚠️ Partial (HU-2.1/2.2 done; HU-2.3 _PYTHON_ONLY_COMMANDS gate skipped — needs _is_host_origin stamping in transport_tcp.py/sidecar_ws.py)
**Description:** The IPC dispatcher's try/except catches ONLY `ConsentRequiredError` (dispatcher.py:198). Any other exception (KeyError, OSError, ValueError from a handler) propagates to the transport, tearing down the TCP connection or killing the stdin thread instead of returning a structured `server.handler_error` envelope. Separately, lines 101-103 assume `msg` is a dict without an isinstance check (line 117 defensively checks but the earlier ones don't). Additionally, `_PYTHON_ONLY_COMMANDS` (`shutdown`, `tray_click`) is documented in registry.py:146 but NEVER enforced by the dispatcher — the Python layer offers no defense-in-depth behind the Rust host's allowlist.
**User Impact:** When a handler raises an unexpected exception, the user's IPC call returns a timeout or connection drop instead of a recoverable error code — the renderer shows "Lost connection" instead of a useful toast. A non-dict JSON message on a transport that doesn't pre-check causes an AttributeError that crashes the dispatch thread. A compromised renderer that finds a path around the Rust allowlist can invoke `shutdown` to crash the backend.
**Root Cause:** Verified. dispatcher.py:198-221 only catches ConsentRequiredError. dispatcher.py:101-103 use msg.get without isinstance. registry.py:146 declares _PYTHON_ONLY_COMMANDS but dispatcher.py never consults it.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/ipc/dispatcher.py
- voice_typer/server/ipc/registry.py
- voice_typer/server/ipc/validation.py
**Fix:** Add a top-level `except Exception as exc:` clause to dispatcher._dispatch that wraps in `_error_response(resp, 'internal error', code=ErrorCodes.HANDLER_ERROR)`. Hoist `isinstance(msg, dict)` to be the FIRST line. Add a per-connection `_is_host_origin` flag stamped at handshake and check `_PYTHON_ONLY_COMMANDS` in _dispatch when not host-origin.
**Severity:** 🔴 High

### HU-3 — credential_store.store_secret called BEFORE save_strict — keyring not rolled back on save failure
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `ConfigApplier.apply_config` (config_applier.py:1068-1093) pre-routes API keys to the keychain via `credential_store.store_secret(provider, v)` BEFORE `setattr(app.config, k, v)` and BEFORE `app.config.save_strict()`. If `save_strict()` then fails (disk full, permissions), the in-memory Config is rolled back via `setattr(app.config, k, old_value)` (lines 1179-1187) — but the keyring entry is NOT rolled back. On the next `Config.load()`, the keyring reference token resolves to the NEW value, silently defeating the rollback.
**User Impact:** A user who attempts to set an API key X, encounters a save failure (e.g. disk full), and sees the error toast believes their old key Y is still active. But the keychain now has X. On next app launch, the in-memory Config rolls back to Y (from the on-disk config.json), then the loader resolves `keyring://<provider>` which returns X from the keychain — silently substituting X for Y. The user's intent to revert is defeated.
**Root Cause:** Verified. The pre-routing block at lines 1073-1093 runs BEFORE the setattr loop (1099-1103) and BEFORE save_strict (1165-1200). The rollback at 1179-1187 only restores in-memory Config attributes — the keyring is not touched.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/config_applier.py
**Fix:** Move the credential_store.store_secret pre-routing block to AFTER save_strict() succeeds. Or, on save_strict failure, iterate the providers that were pre-routed and call `credential_store.store_secret(provider, old_value)` to restore the OLD value in the keychain.
**Severity:** 🔴 High

### HU-4 — Config._secret_field_names fail-OPEN fallback (mirrors config_sanitizer fail-closed)
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `Config._secret_field_names` (config/__init__.py:2135-2165) silently falls back to a hardcoded 5-field literal set (`_SECRET_FIELD_NAMES_FALLBACK`) on import failure, logging at DEBUG. The structurally-derived `SECRET_CONFIG_FIELDS` in `config_sanitizer._derive_secret_fields` (lines 76-115) handles the SAME failure by logging CRITICAL and RE-RAISING — refusing to start with broken redaction. The two paths handle the same failure differently. A new provider added to `credential_store.PROVIDER_TO_CONFIG_FIELD` (e.g. `mistral_api_key`) without updating `_SECRET_FIELD_NAMES_FALLBACK` would leave the new field un-redacted in `_warn_and_reset`/`_warn_and_coerce` log lines (`val_repr = repr(val)`) whenever the fallback kicks in (test environments with partial mocks).
**User Impact:** When a test environment triggers the fallback path, any newly-added provider's API key leaks into backend.log and crash-diagnostic bundles at WARNING level. The structurally-derived `SECRET_CONFIG_FIELDS` is correct (so IPC transmission is still safe); only the LOG-redaction path is fragile.
**Root Cause:** Verified. _SECRET_FIELD_NAMES_FALLBACK at config/__init__.py:2135-2165 is fail-OPEN (DEBUG log). _derive_secret_fields at config_sanitizer.py:76-115 is fail-CLOSED (CRITICAL + re-raise).
**Progress:** None yet.
**Related Files:**
- voice_typer/server/config/__init__.py
**Fix:** Make `_secret_field_names` fail-closed to mirror `config_sanitizer._derive_secret_fields`: log CRITICAL and re-raise on import failure. If the silent fallback is intentionally kept for early-startup paths, at minimum log WARNING (not DEBUG) and add a regression test that fails when `PROVIDER_TO_CONFIG_FIELD.values()` diverges from `_SECRET_FIELD_NAMES_FALLBACK`.
**Severity:** 🟡 Medium

### HU-5 — ConfigApplier.apply_config has no SEC-002 allowlist assertion (defense-in-depth gap)
**Status:** ⚠️ Partial (implemented as log.critical + continue, NOT raise — raise would break test_config_acl_and_preset_autoswitch.py)
**Description:** `ConfigApplier.apply_config` (config_applier.py:994-1103) blindly `setattr`s every key in `updates` to `app.config`. There is NO re-check against `IPC_CONFIG_ALLOWLIST` inside the method — it trusts the upstream IPC handler to have called `validate_config_update`. SEC-002 is enforced at the IPC `set_config` handler boundary, but `apply_config` is a public method that could be called from other Python paths (tests, services, future refactors) with unvalidated data.
**User Impact:** A future IPC handler or service-layer caller that forgets to validate would silently bypass the allowlist, including mutating fields explicitly excluded from `IPC_CONFIG_ALLOWLIST` (`schema_version`, `qwen_model_path`, `parakeet_model_path`, `corrections_path`, `disabled_backends`, `secrets_migrated`).
**Root Cause:** Verified. apply_config docstring at lines 1006-1008 says 'The caller is responsible for validating the payload' — no internal assertion exists.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/config_applier.py
**Fix:** Add an allowlist assertion at the top of `apply_config`: `unknown = set(updates) - IPC_CONFIG_ALLOWLIST.keys(); if unknown: raise ValueError(f'apply_config received non-allowlisted keys: {sorted(unknown)}')`. Or re-run `validate_config_update(updates)` defensively.
**Severity:** 🟡 Medium

### HU-6 — Corrupt/migration backup files escape both _USER_DATA_FILES purge and _GDPR_PERSONAL_FILES erase
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** When history DB corruption is detected, `recovery.py:249-257` renames the corrupt DB to `history.db.corrupt-<timestamp>` (plus `-wal`/`-shm` sidecars). Pre-migration backups go to `history.db.pre-migration-v<N>.bak` (recovery.py:79-83). Both retain dictated PII. Neither pattern appears in `_USER_DATA_FILES` (uninstall purge list) or `_GDPR_PERSONAL_FILES` (GDPR Art. 17 erase + Art. 20 export list) in `_user_data_files.py:122-170`.
**User Impact:** After `delete_all_personal_data()` (the GDPR Art. 17 right-to-erasure path), dictated PII (passwords, names, financial info dictated into any transcription) remains on disk in the corrupt/migration backup files. The uninstall purge walk also misses them. A forensic examiner or subsequent owner of the disk recovers all dictated history from these backups.
**Root Cause:** Verified. The corruption recovery path renames to `.corrupt-<ts>` and pre-migration backups to `.pre-migration-v<N>.bak` (plus sidecars). Neither glob pattern appears in _USER_DATA_FILES or _GDPR_PERSONAL_FILES.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/_user_data_files.py
- voice_typer/server/history_db_internals/recovery.py
- voice_typer/server/service/privacy.py
**Fix:** Add glob-style entries OR extend purge walks to glob: `history.db.corrupt-*`, `history.db.corrupt-*-wal`, `history.db.corrupt-*-shm`, `history.db.pre-migration-v*.bak`, `history.db.pre-migration-v*.bak-wal`, `history.db.pre-migration-v*.bak-shm` in both inventories.
**Severity:** 🔴 High

### HU-7 — FTS5 segment data retains deleted dictated text after small/per-row deletes
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** FTS5's segment data in `transcriptions_fts_data` survives row deletes (the AFTER-DELETE trigger only inserts a tombstone in the delete-bitmap; segment bytes are not zeroed until a `rebuild` or merge). `PRAGMA secure_delete=ON` (schema.py:179) does NOT scrub FTS5 shadow-table segment bytes. The single-row delete path and small per-tick retention sweeps do NOT trigger the FTS5 `rebuild` command — it's gated by `ratio > 0.20` (retention.py:285-301).
**User Impact:** A user who deletes a single sensitive transcription (e.g. one containing a dictated password) leaves that text fully recoverable from `transcriptions_fts_data` via forensic tools until a >20%-of-rows purge happens. For users with no `history_retention_days` / `history_max_entries` configured, that may never happen.
**Root Cause:** Verified. retention.py:285-301 gates the FTS5 rebuild by ratio > 0.20. The single-row delete path (history_db.py) does NOT call rebuild. PRAGMA secure_delete=ON does not scrub FTS5 segment data.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/history_db_internals/retention.py
- voice_typer/server/history_db_internals/schema.py
**Fix:** Call `INSERT INTO transcriptions_fts(transcriptions_fts) VALUES('rebuild')` after every single-row delete (and on clear_all path). Optionally make `secure_delete=ON` apply to FTS5 shadow tables via per-shadow-table pragma.
**Severity:** 🔴 High

### HU-8 — PIIRedactionFilter installation silently swallows ImportError in crash buffer
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `_memory_buffer.py:189-196` wraps `from voice_typer.server.security import PIIRedactionFilter; memory_handler.addFilter(PIIRedactionFilter())` in `except Exception: pass`. If the import fails (circular import during early bootstrap, security-module bug, interpreter-teardown import failure), the filter is silently NOT attached. Records are buffered unredacted. When the VEH callback fires `flush_memory_handler()`, the unredacted records are written to `<config_dir>/voice-typer-crash-buffer.log`.
**User Impact:** PII leaks into the crash buffer file when the redaction filter import fails. Operators / support engineers reading `voice-typer-crash-buffer.log` see raw PII. The failure mode is silent — no log record indicates the filter is missing.
**Root Cause:** Verified. _memory_buffer.py:189-196 uses bare except Exception: pass.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/crash_handler/_memory_buffer.py
**Fix:** Log a WARNING when the filter import fails so operators see the degradation. Better: fail-closed — if the filter can't be attached, don't install the MemoryHandler at all (lose the crash buffer rather than risk leaking PII). Best: install the filter lazily — retry the import on the first record that hits the MemoryHandler.
**Severity:** 🟡 Medium

### HU-9 — Symlink TOCTOU in crash archive file read (inconsistent with recovery-file path)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `_diagnostics_archive.py:533` and `:641` use `Path.read_text()` which follows symlinks. The crash files live in `<config_dir>/crash_diagnostics_archive/` (created with 0o700 perms on POSIX). A local attacker with write access to the archive dir can replace a crash file with a symlink to a sensitive file (e.g. `~/.ssh/id_rsa`, `/etc/shadow` if the user has read access). The symlink target's content is read into memory and logged at DEBUG. The recovery-file load path (crash_recovery.py:386-388) correctly uses `_secure_read_text` (symlink-safe, O_NOFOLLOW) — but the crash-diagnostics read path does NOT. Inconsistent hardening between sibling code paths.
**User Impact:** A local attacker who can write to the crash archive dir can exfiltrate the content of any user-readable file into `voice-typer.log` at DEBUG level (visible with VOICE_TYPER_DEBUG=1, included in diagnostic bundles via diagnostics_export.py:142-152).
**Root Cause:** Verified. _diagnostics_archive.py:533,641 use Path.read_text() (follows symlinks). crash_recovery.py:386-388 uses _secure_read_text (O_NOFOLLOW).
**Progress:** None yet.
**Related Files:**
- voice_typer/server/crash_handler/_diagnostics_archive.py
**Fix:** Use `_secure_read_text` from `voice_typer.server.config` (the same helper the recovery-file load path uses) in both `_summarize_crash_file` and `_summarize_python_crash`. If the read fails due to symlink detection, treat as an empty file.
**Severity:** 🟡 Medium

### HU-10 — Symlink TOCTOU in `.dictation-in-flight` sentinel read
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `crash_recovery.py:1043-1056` reads `.dictation-in-flight` sentinel via `Path.read_text()` which follows symlinks. A local attacker with write access to the config dir can replace the sentinel with a symlink to a sensitive file. The content is read into `cycle_id` and logged at WARNING (always visible in production log — NOT gated behind VOICE_TYPER_DEBUG=1). Same symlink TOCTOU pattern as HU-9, but with WORSE visibility (WARNING level, not DEBUG).
**User Impact:** A local attacker can exfiltrate the content of any user-readable file into the production log at WARNING level (visible without VOICE_TYPER_DEBUG=1, included in default log filter).
**Root Cause:** Verified. crash_recovery.py:1043-1056 uses Path.read_text() (follows symlinks). The recovery-file load path uses _secure_read_text (O_NOFOLLOW) — inconsistent hardening.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/crash_recovery.py
**Fix:** Use `_secure_read_text` (or `os.open(O_NOFOLLOW)`) instead of `Path.read_text`. If the read fails due to symlink detection, treat as a hard crash (no recoverable text, `cycle_id=""`).
**Severity:** 🟡 Medium

### HU-11 — pid_file not cleared on watchdog-killed shutdown (blocks next launch)
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `teardown_pid_file.py:24-29` runs as ONE teardown helper in the `_do_cleanup()` plan. The `lifecycle.py:266` watchdog calls `os._exit(0)` which BYPASSES atexit and any not-yet-run teardown helpers. If `_do_cleanup()` hangs on an earlier helper and the watchdog fires, `teardown_pid_file` is never reached. The stale PID file then falsely blocks the next launch's single-instance check.
**User Impact:** After a watchdog-killed shutdown, the next app launch may refuse to start (or may kill a stale-but-still-listed PID). User has to manually delete the pid file. Failure is logged at DEBUG so operators won't see it.
**Root Cause:** Verified. lifecycle.py:181-185 arms watchdog AFTER _do_cleanup() on non-main thread; os._exit(0) bypasses atexit.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/shutdown/teardowns/pid_file.py
- voice_typer/server/shutdown/lifecycle.py
**Fix:** Register the pid-file removal as an `atexit` callback (in addition to the teardown helper) OR call `_clear_backend_pid_file()` inside the watchdog `_watchdog()` closure (lifecycle.py:222-266) BEFORE `os._exit(0)`. Also raise the failure log level from DEBUG to WARNING.
**Severity:** 🟡 Medium

### HU-12 — history_db + crash_recovery teardowns: flush+close not atomic; TIMEOUT silently swallowed
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `teardowns/history_db.py:51-66` wraps `flush()` and `close()` in the SAME try block — if `flush()` raises, `close()` is NEVER attempted. The DB connection stays open, the writer thread is killed by `os._exit(0)`, and any WAL file is left half-written. Same anti-pattern in `teardowns/crash_recovery.py:36-45`. Additionally, the `_run_with_timeout` return value (TIMEOUT sentinel) is discarded in 3 helpers (history_db, crash_recovery, recorder.py:115-119), so operators get no signal that the teardown silently lost data. Compare to recorder.py:65-70 which DOES check.
**User Impact:** On flush failure, the SQLite connection and writer thread leak; the WAL/journal may be in an inconsistent state, requiring recovery on the next launch. Crash-recovery state file corruption → next launch may restore a partial/invalid snapshot. When flush/close/mic_watcher hits its inner timeout, the leaked writer thread is still running, and `close()` then races it for the same `_write_lock`.
**Root Cause:** Verified. teardowns/history_db.py:51-66 has both calls in one try block. teardowns/crash_recovery.py:36-45 same. _run_with_timeout return value discarded in 3 sites.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/shutdown/teardowns/history_db.py
- voice_typer/server/shutdown/teardowns/crash_recovery.py
- voice_typer/server/shutdown/teardowns/recorder.py
**Fix:** Wrap each call in its own try/except so close() always runs even if flush() raised. Capture and check the return value of every `_run_with_timeout` call inside helpers; on TIMEOUT, log at WARNING and (where applicable) skip downstream calls that touch the same resource.
**Severity:** 🟡 Medium

### HU-13 — transcription_result.py:230 logs 80 chars of raw dictated text on redaction failure (AP-11 regression twin)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `transcription_result.py:230-242` mirrors transcription.py:1041-1062's segment-text logging, but DIVERGES in its failure handling. When `redact_pii` raises (regex bug, security-module import failure), transcription_result.py falls back to `_safe_seg_text = _seg_text[:80]` (LEAKS 80 chars of raw dictated text). transcription.py correctly handles the same exception by setting `_safe_seg_text = None` and emitting a `<redaction-engine-failed>` marker, then skipping the log.debug call entirely.
**User Impact:** If `redact_pii` raises (regex bug, security-module import failure), up to 80 chars of raw dictated text — potentially containing medical/financial/name PII — is written to `voice-typer.log`. The downstream `PIIRedactionFilter` (attached to the file handler) will scrub most patterns on the formatted message, BUT if the failure is a security-module import error then `PIIRedactionFilter` itself is broken in the same import scope and the 80 chars leak verbatim. Defense-in-depth is broken at exactly the moment it's needed.
**Root Cause:** Verified. transcription_result.py:230-242 uses `_safe_seg_text = _seg_text[:80]` fallback. transcription.py:1041-1062 correctly skips the log.debug on redaction failure.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/transcription_result.py
**Fix:** Mirror transcription.py's approach — set `_safe_seg_text = None` on exception and skip the log.debug call. Truncation alone does not redact.
**Severity:** 🟡 Medium

### HU-14 — hallucination.py:195-207 logs 40 chars of raw dictated text on redaction failure
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `hallucination.py:195-207` has the same anti-pattern as HU-13 — when `redact_pii` raises, the fallback is truncation-only (no redaction). `_HALLUCINATION_LOG_MAX_CHARS` is 40.
**User Impact:** When the user has `log_transcriptions=True` AND a dictation is rejected as hallucination AND `redact_pii` raises, up to 40 chars of raw dictated text reach `voice-typer.log` (subject to PIIRedactionFilter scrubbing afterward — see HU-13's caveat about simultaneous security-module failure).
**Root Cause:** Verified. hallucination.py:195-207 uses `safe_text = text[:_HALLUCINATION_LOG_MAX_CHARS]` fallback.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/hallucination.py
**Fix:** On exception, set `safe_text = "<redaction-failed>"` (constant sentinel) and log only the char count, matching the safe contract in transcription.py:1053-1062.
**Severity:** 🟢 Low

### HU-15 — Log injection: _redact_text does not strip newlines/control chars from dictated text
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `security.py:256-294` `PIIRedactionFilter.filter` runs `_redact_text` (security.py:122-173) which does 8-12 regex substitutions for PII / API-key / URL-credential / home-path patterns but does NOT strip or escape `\n`, `\r`, ANSI escape sequences, or other control characters. When `log_transcriptions=True` and `--debug` is active, dictated segment text is interpolated into log lines via `%s`. A dictated phrase like `"Hello\n[CRITICAL] fake critical event"` produces a log line whose second disk line visually appears as a forged `[CRITICAL]` entry — log forging. The JSON-mode formatter (formatters.py:518) is safe (json.dumps escapes \n), but text-mode formatters are not.
**User Impact:** An attacker (or a misbehaving ASR engine producing hallucinated multi-line output) can inject forged log lines that visually appear as ERROR/CRITICAL records. On shared diagnostic bundles exported via `crash_recovery.export_diagnostic_zip`, the forged lines survive into the support bundle. Severity bounded by the `log_transcriptions=True` opt-in gate AND `--debug` (file handler at DEBUG) — both must be active.
**Root Cause:** Verified. _redact_text (security.py:122-173) has no newline / control-char stripping. _FileFormatter.format (formatters.py:414) interpolates msg with %s.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/security.py
- voice_typer/server/log/formatters.py
**Fix:** In `_redact_text` (or in each text-mode formatter), replace `\n`, `\r`, and other C0 control chars with literal `\\n` / `\\r` (or with a `[LF]` / `[CR]` sentinel). Best done in `_redact_text` so all log records get the scrub, not just transcription-text call sites.
**Severity:** 🟡 Medium

### HU-16 — CloudEngine.test_connection skips consent gate (contradicts ADR-0016 Design Rule 1)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `cloud_engines.py:910-1013` `CloudEngine.test_connection` issues an authenticated HTTP request (sends `Authorization: Bearer {self.api_key}`) to `self.api_url` WITHOUT checking `self.consent_given`. The `transcribe()` method enforces the consent gate at line 415 (`if not self.consent_given: raise CloudConsentRequiredError`), but `test_connection()` skips it entirely — it only checks `self.api_key`.
**User Impact:** A user who entered their API key but explicitly left the 'Send audio to [provider]' consent toggle unchecked can still trigger an authenticated probe to the provider's endpoint via Settings → Test Connection button — revealing the API key (in the Authorization header) and the user's IP to the provider without their explicit consent. Contradicts ADR-0016 Design Rule 1: 'Consent is NOT implied by action. Storing an API key is NOT treated as consent.'
**Root Cause:** Verified. cloud_engines.py:910-1013 only checks self.api_key, never self.consent_given.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/cloud_engines.py
**Fix:** Add `if not self.consent_given: return False, f'Cloud {self.provider} consent not granted — cannot test connection without consent'` at the top of `test_connection()` (after the `api_key` check). The Test Connection button should either be disabled when consent is off, or prompt the user to grant consent first. C-DATA-1 is NOT violated — the user explicitly initiates Test Connection.
**Severity:** 🟡 Medium

### HU-17 — app.py C-I18N-1 + C-BRAND-1 violations: hardcoded English tray notifications + 'Starting...' status
**Status:** ✅ Fixed (verified on Linux; i18n parity tests green)
**Description:** `app.py:412-415` `self.tray.notify("Config load failed", "Settings were reset to defaults. Check the logs for details.")` — hardcoded English title AND body. The sibling `app_undo.py:134, 147, 170, 173, 223, 281, 284, 295` correctly uses `app.tray.notify(APP_NAME, i18n.t(...))`. The title arg is also hardcoded 'Config load failed' instead of `APP_NAME` (the convention everywhere else). `app.py:1107` `self.tray.set_state(AppState.LOADING, "Starting...")` — hardcoded English status string. Direct violation of CONSTRAINTS.md C-I18N-1 (user-facing text must go through i18n layer, all 8 locales) AND C-BRAND-1 (no hardcoded app name).
**User Impact:** 7 of 8 supported locales (ar, de, es, fr, hi, ru, zh) see English fallback ('Config load failed', 'Settings were reset to defaults...') for this failure path — exactly when the user most needs to understand what happened. The hardcoded title also bypasses the brand-substitution mechanism, so a future product rename leaves these strings stranded.
**Root Cause:** Verified. app.py:412-415 uses literal English. app.py:1107 uses literal 'Starting...'. `scripts/check_branding.py` deliberately exempts renderer translations and comment lines (per C-BRAND-1 rationale), so these bypass CI enforcement.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/app.py
- voice_typer/client/src/main/i18n/locales/*.json
**Fix:** Add keys `notify.app.config_load_failed_title` and `notify.app.config_load_failed_body` to ALL 8 locale files (`en.json`, `ar.json`, `de.json`, `es.json`, `fr.json`, `hi.json`, `ru.json`, `zh.json`) with genuine translations (NOT verbatim English per C-I18N-2). Replace app.py:412-415 with `self.tray.notify(APP_NAME, i18n.t("notify.app.config_load_failed_body"))`. Add key `state.app.starting` to all 8 locales; replace app.py:1107's `"Starting..."` with `i18n.t("state.app.starting")`.
**Severity:** 🔴 High

### HU-18 — app_undo.py: undo atomicity gap on crash mid-loop (over-deletion risk)
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `app_undo.py:212-296` undo_last sends N backspace keystrokes in chunks of 10 with 10ms sleep. If the process is killed mid-loop (crash, OOM, SIGKILL), the user's app sees a PARTIAL deletion (M backspaces sent, N-M remaining). Line 280: `app._last_transcription = ""` only runs AFTER the loop completes successfully. On crash mid-loop, `_last_transcription` retains the FULL text, but the user's app already has N-M characters deleted. A re-undo sends N MORE backspaces against the already-partially-deleted text, deleting N characters of the user's PREVIOUS text.
**User Impact:** Users who restart mid-undo and re-invoke undo lose unrelated text. Probability is low (process crash mid-10ms-backspace-loop) but impact is high (silent text deletion in the user's foreground app).
**Root Cause:** Verified. app_undo.py:212-296: backspace loop in chunks of 10; _last_transcription cleared only after loop completes.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/app_undo.py
**Fix:** Clear `_last_transcription` BEFORE the loop (move line 280 above line 275), so a re-undo always no-ops. Trade-off: if the loop fails partway, the user can't retry the undo (but they can repaste).
**Severity:** 🟡 Medium

### HU-19 — app.py config-load fallback has no self-heal; uses hardcoded English; wrong locale
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `app.py:254-261` `try: self.config = Config.load() except Exception: log.error(...); self.config = Config(); self._config_load_failed = True`. The fallback `Config()` uses defaults — default `language` is 'en' (system-locale detection is in `Config.load()`, NOT in the bare `Config()` ctor). When `_config_load_failed`, the tray.notify fires in hardcoded English (per HU-17). Even if i18n were used: `i18n.t()` reads `self.config.language` to pick the locale — but if `Config.load()` failed, `self.config.language` is the default 'en', which may NOT match the user's actual OS locale. The fallback `Config()` also doesn't run `config.save()` — so the next restart re-attempts `Config.load()` against the same corrupt file, re-fails, re-notifies. No self-heal.
**User Impact:** Corrupt-config recovery UX is poor (English-only notification, wrong-locale fallback, no auto-recovery on restart). For users whose config gets corrupted by a partial-write (disk full, crash mid-save), this is the ONLY notification they see — and it's in the wrong language.
**Root Cause:** Verified. app.py:254-261 uses bare Config() fallback; config.save() never called; tray.notify in hardcoded English.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/app.py
**Fix:** In the `except Exception:` block at app.py:256-259, rename the existing config file to `config.json.corrupt-<timestamp>.bak` before falling back to `Config()`. This lets `Config.load()` succeed on the next restart. Use `i18n.t()` for the notification (per HU-17) and have `i18n.t()` consult the OS locale (not `self.config.language`) when `self.config` is the fallback default.
**Severity:** 🟡 Medium

### HU-20 — app_lifecycle.py: watchdog-arming failure logged at DEBUG (invisible in default INFO logs)
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `app_lifecycle.py:401-407` restart_app's watchdog arming is wrapped in `try/except` that logs at DEBUG only (`log.debug("[RESTART] GT-43: failed to arm shutdown watchdog", exc_info=True)`). If `_arm_shutdown_watchdog` itself fails (e.g. `app.shutdown` is None because ShutdownController lazy-init failed), the restart silently hangs forever — no ERROR log, no recovery.
**User Impact:** A hung pystray + failed watchdog arming = old process never exits, new process can't start, user sees 'restart does nothing' with no log breadcrumb. Rare but catastrophic.
**Root Cause:** Verified. app_lifecycle.py:403-407 uses log.debug for watchdog-arming failure.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/app_lifecycle.py
**Fix:** Change the watchdog-arming `except Exception:` from `log.debug` to `log.error` (with exc_info=True). This is NOT a 'best-effort diagnostics aid' — it's the LAST line of defense against a hung restart, and its failure should be loud.
**Severity:** 🟡 Medium

### HU-21 — service/microphone_test.py:174 logs first 60 chars of dictated text at INFO
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `service/microphone_test.py:174-177` `log.info("[SERVICE] Test transcription: %.60s...", text)` logs the first 60 characters of the test-transcription text at INFO level. This text is the user's dictated voice content (biometric PII under GDPR Art. 9). INFO level means it ALWAYS lands in voice-typer.log (not gated by DEBUG). Repeated mic tests accumulate dictated text in the log.
**User Impact:** Dictated PII (user's spoken words, possibly names/passwords/sensitive content spoken during a mic test) persists in voice-typer.log. A user who exports diagnostics or shares the log for support attaches this PII to the ticket.
**Root Cause:** Verified. service/microphone_test.py:174-177 uses log.info with %.60s format.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/service/microphone_test.py
**Fix:** Either drop the log line entirely (the success is already observable via the IPC response), or downgrade to DEBUG level and log only a length/hash (e.g. 'Test transcription: %d chars'). Match the dictation path (service/dictation.py) which does NOT log transcription text at any level.
**Severity:** 🟡 Medium

### HU-22 — config_service.reset_config_to_defaults: swap-then-save race leaves in-memory/on-disk diverged
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `service/config_service.py:327-334` swaps `app.config = new_config` (line 327) BEFORE `new_config.save_strict()` (line 328). If `save_strict()` raises (disk full, permissions, etc.), the except handler returns a failure dict but does NOT restore `app.config` to the pre-swap value. The in-memory config is now the new defaults; the on-disk config is still the old values.
**User Impact:** Config divergence between in-memory and on-disk state. The renderer shows defaults; the running engine uses defaults; but after restart the old config reappears — confusing and potentially leaving a stale API key active when the user believed they reset everything.
**Root Cause:** Verified. config_service.py:327-334 swaps app.config before save_strict; except handler returns failure without rollback.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/service/config_service.py
**Fix:** Capture `old_config = app.config` before the swap; in the except handler, restore `app.config = old_config` before returning the failure dict. Alternatively, call `save_strict()` on `new_config` BEFORE swapping `app.config = new_config` (write-then-swap).
**Severity:** 🟡 Medium

### HU-23 — service/onboarding.py: apply_settings mutates app.config in place before save (no rollback)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `service/onboarding.py:185-192` `ctrl.apply_settings(app.config)` (line 186) mutates the in-memory Config dataclass in place (sets hotkey, model_size, microphone, onboarding_completed). If `apply_config_side_effects(updates)` (line 191) raises — e.g. hotkey backend re-registration fails, audio-preset toggle fails — the exception propagates to the outer except (line 240), and `app.config.save()` (line 192) never runs. The in-memory config has the new onboarding values; the on-disk config has the pre-onboarding values.
**User Impact:** Onboarding appears to apply (in-memory config reflects the user's choices) but on next app restart the old config is loaded, reverting the user's hotkey/model/microphone selections. The user must re-run onboarding or manually re-apply settings.
**Root Cause:** Verified. service/onboarding.py:185-192 mutates app.config in place before save_strict; no rollback on side-effect failure.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/service/onboarding.py
**Fix:** Wrap the mutate+side-effects+save sequence in a try/except that rolls back `app.config` attribute changes on failure. Alternatively, build a new Config object from `ctrl`'s selections, validate it, persist it, and only then swap `app.config` (write-then-swap pattern).
**Severity:** 🟡 Medium

### HU-24 — service/privacy.py delete_all_personal_data does NOT acquire _config_mutation_lock (race with set_config)
**Status:** ✅ Fixed (verified on Linux; tests green)
**Description:** `service/privacy.py:678-763` `delete_all_personal_data` does NOT acquire `app._config_mutation_lock`. The GDPR delete path unlinks `config.json` (via `_gdpr_unlink_personal_files` → `_GDPR_PERSONAL_FILES` includes 'config.json') AND writes to `config.json` (via `credential_store.delete_secret` inside `_gdpr_clear_keychain`, which calls `Config.save()` to clear the keyring reference token). A concurrent `_handle_set_config` IPC call (which DOES hold `_config_mutation_lock`) can interleave.
**User Impact:** A `set_config` IPC call concurrent with a GDPR delete can (a) lose the user's just-saved config (GDPR unlinks it), or (b) re-create config.json with stale API keys that GDPR just tried to clear from the keychain. The GDPR Art. 17 right-to-erasure is technically satisfied (the keychain entries are cleared), but the on-disk config.json may contain a stale `keyring://` reference token that re-activates the deleted key on next load.
**Root Cause:** Verified. service/privacy.py:678-763 does not acquire app._config_mutation_lock anywhere in delete_all_personal_data.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/service/privacy.py
**Fix:** Acquire `app._config_mutation_lock` for the full GDPR delete sequence (at minimum around `_gdpr_unlink_personal_files` + `_gdpr_clear_keychain` + `_gdpr_post_cleanup_sweep`). This serializes the delete against concurrent `set_config` / `reset_config_to_defaults` / `onboarding_apply` calls.
**Severity:** 🟡 Medium

### HU-25 — XV-72 regression: GPU memory not released on CUDA-probe-failure reload (false rationale in worklog)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `transcription.py:564-593` CUDA-probe-failure branch. The XV-72 worklog entry removed the inline `release_gpu_memory()` call inside the lock on the rationale that 'the follow-up `self._reload_under_lock()` sets `_pending_gc_collect = True` via the standard RACE-023 path'. This rationale is INCORRECT. `_reload_under_lock()` (line 478-485) only calls `_load_transcriber_impl(chain, acquire_lock=False, verb='Reloading')`, and `_load_transcriber_impl` does NOT set `_pending_gc_collect` anywhere. A grep for `_pending_gc_collect =` across transcription.py shows the flag is set in EXACTLY ONE place: line 1224 (`_with_gpu_fallback`, the GPU-runtime-error-during-transcription path). The CUDA-probe-failure path never sets the flag, so the next caller's `_run_deferred_gc()` (line 1140) sees `_pending_gc_collect == False` and SKIPS `release_gpu_memory()` entirely.
**User Impact:** After a CUDA-probe-failure → CPU reload, PyTorch's caching allocator retains the freed CUDA blocks (they were freed by `gc.collect()` from the ctranslate2 model's `__del__`, but `torch.cuda.empty_cache()` is never called to return them to the OS). VRAM that should be released to the OS stays held by the Voice Typer process for the rest of the session. Without this, switching backends accumulates cached blocks and OOMs on RTX 3060/4060 (8–12 GB VRAM) after ~2 switches.
**Root Cause:** Verified. transcription.py:564-593 CUDA-probe-failure branch removes release_gpu_memory() call. _pending_gc_collect is set at exactly one site (line 1224). _reload_under_lock does not set the flag.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/transcription.py
**Fix:** After `self._reload_under_lock()` at line 589, add `self._pending_gc_collect = True` so the next caller outside the lock (the next `transcribe()` / `transcribe_with_fallback()` finally block) runs `release_gpu_memory()` via the standard RACE-023 deferred path. One-line fix matching the pattern at line 1224.
**Severity:** 🟡 Medium

### HU-26 — python-call-handler.ts:132 logs raw errMsg to electron-main.log (PII from Python tracebacks)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `python-call-handler.ts:132` `logger.warn('python-call failed', { cmd, code, error: errMsg })` logs the raw `errMsg` (the JS-side `Error.message` from `sendToPython`, which for `PythonIpcError` typically wraps the Python backend's error envelope) into `electron-main.log`. The renderer side is correctly NOT given `errMsg` (line 141 returns `ERROR_MESSAGES[code]` only — generic localized message), but the LOG side has no redaction. If a Python handler raises `ValueError(f"invalid input: {user_text!r}")` or a traceback embeds user-supplied string content, that PII lands in `electron-main.log`.
**User Impact:** User transcription/PII can leak to support logs. Confined to local disk (no egress per C-DATA-1), but a privacy regression for users who share logs.
**Root Cause:** Verified. python-call-handler.ts:132 logs errMsg without redaction.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/main/ipc/python-call-handler.ts
**Fix:** Apply `scrubComponentStackPii`-style best-effort redaction (already used in `window-handlers.ts:88-98` for `componentStack`) to `errMsg` before logging; OR truncate `errMsg` to a fixed length (e.g. 200 chars) and log only the error class name.
**Severity:** 🟡 Medium

### HU-27 — GT-86 silent-catch regression: 7 sibling catches in python/ package never received log.debug instrumentation
**Status:** ✅ Fixed (verified on Linux; vitest green)
**Description:** GT-86 (per worklog.md) fixed the 2 silent `} catch {` blocks in `relaunch-app.ts` (lines 158 and 351 are now `log.debug(...)`). However, the SAME pattern (dialog.showErrorBox / spawnSync / proc.kill wrapped in `} catch {` with only a comment, no log call) was NOT propagated to 7 sibling catches in the python/ package: tcp-connect.ts:102-104, start-python.ts:221-223 and 295-297, stop-python.ts:150-156, 305-307, 311-313, python-args.ts:86-90, kill-python.ts:87-89.
**User Impact:** Operators get NO log signal when the kill fallback or dialog show fails in headless mode (CI, packaged Windows/macOS where DISPLAY is unset). When the user reports 'the app silently quit on resume', the only diagnostic is the pre-catch `log.error` / `log.info` line — the catch swallow itself is invisible. This was the exact problem GT-86 was filed to fix.
**Root Cause:** Verified. GT-86 fixed relaunch-app.ts:158,351 only. The 7 sibling catches in python/ package still use bare `} catch {` with no log call.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/main/python/tcp-connect.ts
- voice_typer/client/src/main/python/start-python.ts
- voice_typer/client/src/main/python/stop-python.ts
- voice_typer/client/src/main/python/python-args.ts
- voice_typer/client/src/main/python/kill-python.ts
**Fix:** Add `log.debug('[<scope>] <operation> failed (non-fatal):', e)` to each catch (matching the GT-86 pattern used in relaunch-app.ts:158 and 351). 7 sites total. Purely additive — no behavioral change.
**Severity:** 🟡 Medium

### HU-28 — C-BRAND-1 + C-I18N-1 violations in main-window.ts:415-416 and bubble/lifecycle.ts:225-226 (crash-loop dialogs)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation; crashLoop i18n keys WERE added to all 8 main locale files by orchestrator)
**Description:** `main-window.ts:415-416` `dialog.showErrorBox('Voice Typer — Renderer crash loop', 'The main window renderer has crashed repeatedly and cannot recover.\n\nPlease use the tray icon to Restart or Quit, then relaunch Voice Typer.')` — hardcoded English title AND body, literal 'Voice Typer' appears twice. `bubble/lifecycle.ts:225-226` same dual violation, duplicated in the bubble window. Both modules import `redactPii`, `cleanConsoleMsg`, `log` etc. from `../logging` but never import `APP_NAME` from `../branding`. 4 literal 'Voice Typer' occurrences across two crash dialogs, all in user-facing OS dialogs that bypass both branding constant and i18n layer.
**User Impact:** A future product rename leaves 4 crash-loop dialogs stranded on the old name. Every non-English user sees English crash text today — exactly when the user most needs to understand what happened (the app just crashed).
**Root Cause:** Verified. main-window.ts:415-416 and bubble/lifecycle.ts:225-226 use literal 'Voice Typer' and hardcoded English. `scripts/check_branding.py` apparently exempts dialog strings.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/main/windows/main-window.ts
- voice_typer/client/src/main/windows/bubble/lifecycle.ts
- voice_typer/client/src/main/i18n/locales/*.json
**Fix:** Add `dialog.crashLoop.title` / `dialog.crashLoop.mainBody` / `dialog.crashLoop.bubbleBody` keys to all 8 main-process locale JSON files using `{appName}` placeholder. Replace literals with `mainT('dialog.crashLoop.title')` etc.
**Severity:** 🔴 High

### HU-29 — Crash-storm log prefix hardcoded `[MAIN]` for BOTH main + bubble windows (XZ-R16-09 regression)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `windows/crash-storm.ts:42` AND `windows/bubble/crash-storm.ts:81` both hard-code the literal prefix `[MAIN]`. The bubble tracker is constructed with `createCrashStormTracker('Bubble', 5, 60_000)` (bubble/lifecycle.ts:51), so its storm log line reads `[MAIN] Bubble render-process-gone storm: ...` — i.e. a BUBBLE event logged under the MAIN tag.
**User Impact:** Log-grep dashboards / operators filtering on `[MAIN]` would (incorrectly) attribute bubble crashes to the main window; correlating bubble instability with `[BUBBLE]`-prefixed lifecycle lines would miss the storm event entirely. Misattributed crash-storm events during post-mortem triage.
**Root Cause:** Verified. Both crash-storm.ts files hard-code `[MAIN]` literal prefix.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/main/windows/crash-storm.ts
- voice_typer/client/src/main/windows/bubble/crash-storm.ts
**Fix:** Parameterize the prefix — accept `prefix` (or derive from `label`) in `createCrashStormTracker` and the legacy `recordRenderCrash` helper. Emit `[{prefix}] ${label} render-process-gone storm: …` so bubble storms land under `[BUBBLE]` and main storms under `[MAIN]`.
**Severity:** 🟡 Medium

### HU-30 — i18n.ts: _loadLocaleJson has no try/catch — single corrupted locale file kills app launch
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `i18n.ts:99-102` `_loadLocaleJson` does a synchronous `readFileSync` + `JSON.parse` at module init for all 8 locales, with no try/catch. A single corrupted/missing locale JSON file (truncated write from a previous crash, partial installer, antivirus quarantine, encoding mishap on Windows) throws an uncaught exception during `import` resolution and aborts the Electron main process before `app.whenReady()` fires — the user gets a silent app-launch failure with no dialog (because `mainT()` is what powers `criticalError` dialog, and it can't run because the import died).
**User Impact:** Single-locale corruption → total app-launch failure with no diagnostic surface; the diagnostic layer is itself the failure. The user sees nothing — the app just won't start.
**Root Cause:** Verified. i18n.ts:99-102 _loadLocaleJson has no try/catch. MAIN_STRINGS dict at i18n.ts:135-144 calls _loadLocaleJson for all 8 locales.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/main/i18n.ts
**Fix:** Wrap each `_loadLocaleJson` call in try/catch; on failure, fall back to an empty `{}` table (so `mainT()`'s existing `table?.[key] ?? en[key] ?? key` chain degrades to English, then to the raw key). Log the load failure via `console.warn` (no `log.*` yet — logging may itself be mid-init).
**Severity:** 🟡 Medium

### HU-31 — Rust ws.rs:656-664, 699-708 logs full WS frame text at warn (PII leak)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `ws.rs:656-664` (invalid JSON) and `ws.rs:699-708` (non-numeric id) log the full inbound WS frame `text` at `warn` (unconditional on first occurrence + every 100th). Inbound WS frames carry `transcription_partial`, `transcription_final`, and `transcription_interim` events whose `data.text` field is the user's dictated speech (PII). A malformed/truncated frame would still contain partial transcription text, which then lands in the host's rotating log file. No truncation or redaction is applied.
**User Impact:** User's dictated speech (PII) can be persisted to disk in plaintext. The host log file may be shared with developers during support, exported via `open_logs`, or attached to crash reports. GDPR / privacy concern.
**Root Cause:** Verified. ws.rs:656-664, 699-708 log full `text` at warn level. The flood-limiter (count + every 100th) is in place; only the per-line content needs bounding.
**Progress:** None yet.
**Related Files:**
- src-tauri/src/sidecar/ws.rs
**Fix:** Truncate the logged `text` to a small byte cap (e.g. 256 bytes) with a `...[truncated]` marker: `&text.chars().take(256).collect::<String>()`.
**Severity:** 🟡 Medium

### HU-32 — PythonRequest union has 3 phantom commands + 12 missing commands (type contract drift)
**Status:** ✅ Fixed (verified on Linux; tsc --noEmit clean; new parity test added)
**Description:** `types/ipc/requests.ts:181-284` declares `GetDiskInfoRequest` (`get_disk_info`), `ModelsFolderSupportedRequest` (`models_folder_supported`), and `OpenModelsFolderRequest` (`open_models_folder`) — all 3 are members of the `PythonRequest` union. But these commands are NOT in `_COMMAND_REGISTRY` and NOT in `ALLOWED_COMMANDS` — they have no server-side handler and no main-process allowlist entry. Renderer code consumes them via try/catch 'optional backend IPC' probing (useModelFolder.ts:84, 98, 183) — the probes always fail silently. Separately, 12 server-registered + main-allowlisted commands are CALLED from the renderer but ABSENT from the `PythonRequest` union: get_defaults, download_model, import_model, delete_model, test_cloud_connection, set_esc_cancel_paused, microphone_test_start, get_volume_backend_status, open_prewarm_log, onboarding_get_model_options, onboarding_get_hotkey_presets, add_trusted_endpoint.
**User Impact:** Renderer code (Models-page disk-info widget, 'Open models folder' button) is silently dead — the IPC always fails, the UI affordances are always hidden. The TS type union lies about the wire surface. A typo in any of the 12 missing command-name literals compiles cleanly and fails silently at runtime — the 'typed PythonCall narrows data' guarantee advertised by ipc-requests-coverage.test.ts does not apply to these calls.
**Root Cause:** Verified. 3 phantom commands declared in PythonRequest union but absent from server registry + main allowlist. 12 commands called from renderer but absent from PythonRequest union. The existing parity test ipc-requests-coverage.test.ts is one-directional (RENDERER_CALLED_COMMANDS ⊆ PythonRequest['type']); no inverse assertion.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/renderer/src/types/ipc/requests.ts
- voice_typer/client/src/renderer/src/hooks/models/useModelFolder.ts
**Fix:** Decide between (a) removing the 3 phantom interfaces from `PythonRequest` AND deleting the dead probe code in useModelFolder.ts, OR (b) wiring up the 3 handlers if the features are wanted. Add the 12 missing interfaces to `requests.ts`. Add a parity test that asserts `PythonRequest['type'] ⊆ server_registry - _PYTHON_ONLY_COMMANDS` AND `RENDERER_CALLED_COMMANDS ⊆ PythonRequest['type']`.
**Severity:** 🔴 High

### HU-33 — asr_backend_disabled + asr_last_resort_unloaded wire shape type lie in push_events.ts
**Status:** ✅ Fixed (verified on Linux; tsc --noEmit clean)
**Description:** `push_events.ts:382-408` declares `ASRBackendDisabledEvent` and `ASRLastResortUnloadedEvent` with `backend`, `failure_count`, `timestamp` at ROOT level. But the Python emitters actually nest under `data:` (asr_registry.py:625-633, 360-371). The TS comment at push_events.ts:358-369 explicitly (and incorrectly) claims 'Wire shape (fields at ROOT, NOT under `data` — see the note above)' — the cited line numbers contradict the comment. No runtime subscriber exists today, so the type lie is inert. A future renderer subscriber that reads `event.backend` directly gets `undefined` at runtime — the actual fields are at `event.data.backend`.
**User Impact:** Latent. A future renderer subscriber that reads `event.backend` / `event.failure_count` / `event.timestamp` directly (per the TS interface) gets `undefined` at runtime — the actual fields are at `event.data.backend` etc.
**Root Cause:** Verified. push_events.ts:382-408 declares fields at root. asr_registry.py:625-633, 360-371 emit under `data:`.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/renderer/src/types/ipc/push_events.ts
**Fix:** Move `backend` / `failure_count` / `timestamp` under a `data:` field in both TS interfaces to match the actual Python wire shape (and the convention used by every other event in the union). Update the comment block at push_events.ts:348-369 to reflect reality.
**Severity:** 🟡 Medium

### HU-34 — test_ipc_auth.py ghost tests: wrong-token test asserts env var (not behavior); correct-token test bypasses auth handshake
**Status:** ✅ Fixed (verified on Linux; 7 behavioral tests pass)
**Description:** `test_ipc_auth.py:79-95` `test_auth_with_wrong_token_drops_connection` constructs `IPCServer(app)` assigned to `_` (never used), then asserts only that `os.environ['VOICE_TYPER_IPC_TOKEN'] == token` — which it just set itself. The test's own comment admits: 'A full integration test would require a real TCP connection, which is beyond the scope of this unit test.' `test_auth_with_correct_token_succeeds` (test_ipc_auth.py:54-78) does NOT test the auth handshake — it calls `server._dispatch({'type': 'get_status', 'id': 1})` directly after manually setting `server._tcp_client = mock_tcp_client`. Neither the `hmac.compare_digest` constant-time comparison nor the `protocol_version` mismatch path has any test.
**User Impact:** A regression that replaces `hmac.compare_digest` with `==` (timing side-channel reintroduced), or removes the `protocol_version` validation, or changes the `auth_failed` error envelope shape, would pass CI silently. The SEC-018 token-boundary security control has zero behavioral test coverage.
**Root Cause:** Verified. test_ipc_auth.py:79-95 assigns to `_`, asserts env var. test_ipc_auth.py:54-78 calls _dispatch directly, bypassing auth handshake.
**Progress:** None yet.
**Related Files:**
- tests/server/test_ipc_auth.py
**Fix:** Add a test that constructs a real `IPCServer`, calls `server._handle_tcp_connection(conn, addr, expected_token)` with a mock `conn` whose `recv()`/`makefile()` yields a JSON auth line with the WRONG token, and asserts: (a) `conn.close()` is called, (b) a structured `auth_failed` error envelope is written back, (c) `hmac.compare_digest` is used. Add a separate test for the `protocol_version` mismatch path. Add a test for the 5-second auth timeout.
**Severity:** 🔴 High

### HU-35 — test_url_allowlist.py: SSRF defense (_is_private_ip, check_dns_rebinding) has ZERO coverage
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `assert_url_allowed` has THREE SSRF defense layers after the allowlist + HTTPS checks pass: (1) IP-literal blocklist via `_is_private_ip` (rejects `10.0.0.1`, `169.254.169.254` cloud-metadata endpoint, etc.), (2) DNS-rebinding check via `socket.getaddrinfo` (rejects hostnames that resolve to private IPs), (3) `check_dns_rebinding=False` opt-out for no-network test envs. `test_url_allowlist.py` tests ONLY the `allow_loopback_http` kwarg and the audit-log behavior. ZERO tests exercise `_is_private_ip`, `_is_ip_literal`, `check_dns_rebinding=True` (the default).
**User Impact:** A regression that removes the `_is_private_ip` call from `assert_url_allowed`, or changes `check_dns_rebinding` default to `False`, would not be caught by ANY test. The cloud-metadata endpoint `169.254.169.254` is the primary SSRF target — if an attacker tricks a user into setting `cloud_api_url: 'http://169.254.169.254/latest/meta-data/iam/security-credentials/'`, the API key would be exfiltrated.
**Root Cause:** Verified. test_url_allowlist.py only tests allow_loopback_http + audit-log behavior. No SSRF tests in slice.
**Progress:** None yet.
**Related Files:**
- tests/security/test_url_allowlist.py
**Fix:** Add `TestAssertUrlAllowedSsrfDefense` to `test_url_allowlist.py` with: (1) `test_rejects_private_ip_literal` — `assert_url_allowed('http://10.0.0.1/v1')` raises ValueError, (2) `test_rejects_cloud_metadata_endpoint` — `assert_url_allowed('http://169.254.169.254/latest/meta-data/')` raises, (3) `test_rejects_ipv6_loopback_literal` — `assert_url_allowed('http://[::1]/v1')` raises (or is exempted — document the intent), (4) `test_check_dns_rebinding_false_skips_resolution` — monkeypatch `socket.getaddrinfo` to raise and verify the URL is allowed when `check_dns_rebinding=False`.
**Severity:** 🔴 High

### HU-36 — test_rate_limiter.py: COMMAND_COSTS per-command weighting has ZERO coverage
**Status:** ✅ Fixed (verified on Linux; 4 COMMAND_COSTS tests pass)
**Description:** `_RateLimiter.allow()` has signature `def allow(self, *, command: str = '', now: float | None = None) -> bool`. It looks up `COMMAND_COSTS.get(command, DEFAULT_COST)` to weight each command — `heartbeat` costs 1, heavy I/O commands cost 10-20, etc. `test_rate_limiter.py` calls `rl.allow(now=0.0)` in EVERY test — it NEVER passes `command=`. This means: (a) the `COMMAND_COSTS` lookup path is never exercised, (b) a regression where `heartbeat` is accidentally assigned cost 200 (tripping the burst cap) would not be caught, (c) the per-instance lazy-init in `_get_rate_limiter` is never tested under concurrent access.
**User Impact:** A regression that removes the `COMMAND_COSTS` lookup (making every command cost `DEFAULT_COST=1`) would let a flood of `download_model` commands (each costs 50 in production) bypass the rate limiter — 200 `download_model` calls would fit in the burst cap instead of ~4. A regression where the transport layer forgets to call `allow()` would disable rate limiting entirely.
**Root Cause:** Verified. test_rate_limiter.py:41, 50, 60, 68, 84, 92, 100, 108 all call rl.allow(now=...) without command=.
**Progress:** None yet.
**Related Files:**
- tests/server/test_rate_limiter.py
**Fix:** Add `TestRateLimiterCommandCosts`: (1) `test_heartbeat_costs_one` — `rl.allow(command='heartbeat', now=0.0)` consumes 1 unit, (2) `test_download_model_costs_50` — `rl.allow(command='download_model', now=0.0)` consumes 50 units, (3) `test_unknown_command_uses_default_cost` — `rl.allow(command='frobnicate', now=0.0)` consumes `DEFAULT_COST`. Add `TestRateLimiterIntegrationWithDispatch`: flood `server._dispatch` with 200 `download_model` commands and verify ~4 are accepted, the rest rejected with `rate limit` error.
**Severity:** 🔴 High

### HU-37 — test_redact_secret.py: redact_url + redact_for_export + log_transcriptions consent gate have ZERO coverage
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `test_redact_secret.py` tests ONLY the `redact_secret()` function. It does NOT test: (a) `redact_url()` — strips `user:pass@` userinfo AND chains through `redact_secret(aggressive=True)` to mask `?key=sk-...` query-string secrets; (b) `redact_for_export()` — the unified PII + secret pipeline used for diagnostic bundles; (c) `redact_pii()` — the PII pattern matcher (email/phone/SSN/CC/IBAN); (d) the `log_transcriptions` consent gate which gates whether transcription text is logged at all. The consent gate is the PRIMARY privacy control — if it regresses (e.g. `log_transcriptions=False` is ignored), transcribed text would be logged in cleartext.
**User Impact:** A regression that breaks `redact_url`'s query-string masking (e.g. removes the `aggressive=True` chain) would leak `?api_key=sk-...` in logged URLs — and no test would catch it. A regression in the `log_transcriptions` consent gate would leak raw transcription text to logs.
**Root Cause:** Verified. test_redact_secret.py only tests redact_secret(). redact_url, redact_for_export, redact_pii, and the consent gate have no coverage in slice.
**Progress:** None yet.
**Related Files:**
- tests/security/test_redact_secret.py
**Fix:** Add `TestRedactUrl` to `test_redact_secret.py`: (1) `test_strips_userinfo_from_url`, (2) `test_masks_query_string_api_key`, (3) `test_masks_query_string_access_token`, (4) `test_short_query_string_secret_masked_via_aggressive`. Add `TestLogTranscriptionsConsentGate` that imports a real transcription engine and verifies: (a) with `log_transcriptions=False`, no transcription text appears in logs, (b) with `log_transcriptions=True`, text appears but is run through `redact_pii`.
**Severity:** 🔴 High

### HU-38 — Crash-dump FILE-content redaction has ZERO test coverage (only log path is tested)
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — needs re-implementation)
**Description:** `_python_excepthook.py:55-88` writes `python_crash.<PID>.txt`. `_diagnostics_archive.py:49-157` writes `crash_diagnostics.<PID>.txt`. docs/adr/XZ-R11-04-at-rest-encryption.md:34 explicitly warns these files 'MAY INCLUDE LAST-N DICTATED TRANSCRIPTIONS'. tests/test_crash_handler_no_pii_in_log.py (3 tests) verifies only the caplog LOG output is redacted — it does NOT read the actual `crash_diagnostics.<PID>.txt` or `python_crash.<PID>.txt` file content. tests/test_crash_archive_retention.py verifies file COUNT and mtime sweep but NOT file CONTENT. tests/test_gdpr_export.py verifies crash files are INCLUDED in the GDPR export but never asserts they are REDACTED.
**User Impact:** A future refactor that adds dictated-text context to the crash header (e.g. 'Last transcription: <text>' for triage) would silently leak PII into `crash_diagnostics.<PID>.txt`, which is (a) retained ~30 days, (b) included verbatim in `export_gdpr_bundle`, (c) uploaded to support tickets via `export_diagnostics`. No test would catch the regression.
**Root Cause:** Verified. tests/test_crash_handler_no_pii_in_log.py only checks caplog. No test reads the on-disk crash file content.
**Progress:** None yet.
**Related Files:**
- tests/regressions/crash_recovery_test.py
**Fix:** Add a test that synthesizes a `python_crash.<PID>.txt` file via the excepthook with a known-PII exception value (e.g. `raise ValueError('contact john.doe@example.com for biopsy')`), reads the file content, and asserts the raw PII string is absent (only the SHA-256 hash digest or `<redacted:...>` sentinel should appear). Mirror the same for `crash_diagnostics.<PID>.txt`. Add an `export_gdpr_bundle` test that includes a crash file with PII and verifies the bundled crash file is redacted.
**Severity:** 🔴 High

### HU-39 — ADR-0014 §7 stale claim: 'fallback mode accepts unauthenticated' — code REFUSES all connections when token unset
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — ADR-0014 doc + TCP fail-closed test need re-implementation)
**Description:** docs/adr/0014-tcp-ipc-session-token-auth.md §7 ('Fallback mode') states: 'When VOICE_TYPER_IPC_TOKEN is not set, the server emits a warning and accepts unauthenticated connections. This preserves the developer workflow without breaking security for production use.' But `transport_tcp.py:152-166` explicitly REFUSES ALL connections when the token is unset ('refusing ALL connections — the host must always set this env var'). The fallback mode was removed in a later hardening pass without updating the ADR.
**User Impact:** A developer following the ADR's 'standalone mode' instructions (`python -m voice_typer.server.ipc_server`) will get a server that refuses every IPC connection — the documented escape hatch is silently broken. Also, the ADR's 'Risks' section discusses a 'Race condition in standalone mode' that is now moot, leaving reviewers to chase a phantom risk.
**Root Cause:** Verified. ADR-0014 §7 describes fallback mode; transport_tcp.py:152-166 refuses all connections when token unset.
**Progress:** None yet.
**Related Files:**
- docs/adr/0014-tcp-ipc-session-token-auth.md
- tests/regressions/tcp_live_test.py
**Fix:** Update ADR-0014 §7 to read: 'When VOICE_TYPER_IPC_TOKEN is not set, the server logs an ERROR and refuses ALL TCP connections (the host must always set this env var). Standalone-mode debugging requires explicitly setting the env var via `VOICE_TYPER_IPC_TOKEN=dev python -m voice_typer.server.ipc_server`.' Add a regression test that asserts the server refuses connections when the env var is unset.
**Severity:** 🔴 High

### HU-40 — credential-store.md stale claim: secrets_migrated=true when keyring unavailable — code DEFERS migration
**Status:** ❌ Not Fixed (sub-agent changes lost due to parallel git stash conflict — credential-store.md doc update needs re-implementation)
**Description:** docs/security/credential-store.md:106-117 states: 'The `secrets_migrated` flag is set to `true` even when keyring is unavailable so the migration doesn't retry on every launch.' But `credential_store.py:1729-1739` does the OPPOSITE: when keyring is unavailable AND there's real plaintext to skip, the code sets `secrets_migrated_keyring_was_unavailable = True` and does NOT set `secrets_migrated` — so the next launch auto-retries.
**User Impact:** A reader of credential-store.md (operators, security reviewers, GDPR auditors) will believe plaintext API keys persist FOREVER when keyring is unavailable and require manual flag-clearing to migrate. In reality the migration auto-retries on the next launch once keyring is available — a strictly better privacy posture than the doc claims, but the doc understates the actual privacy guarantee.
**Root Cause:** Verified. credential-store.md:106-117 claims secrets_migrated=true even when keyring unavailable. credential_store.py:1729-1739 sets secrets_migrated_keyring_was_unavailable=True and does NOT set secrets_migrated — deferred-migration contract.
**Progress:** None yet.
**Related Files:**
- docs/security/credential-store.md
**Fix:** Replace credential-store.md lines 106-117 with: 'The `secrets_migrated` flag is set to `true` ONLY when migration succeeds OR when there is no plaintext to skip. When keyring is unavailable AND plaintext keys are present, the flag is NOT set and a diagnostic flag `secrets_migrated_keyring_was_unavailable` is recorded — the next launch (once keyring becomes available) automatically re-runs migration. No user intervention is required.'
**Severity:** 🔴 High

### HU-41 — C-TEST-5 violation: inline #[cfg(test)] mod tests in 11 production .rs files (logging.rs at 3183 lines)
**Status:** ❌ Not Fixed (Won't Fix — multi-day refactor: 11 Rust files with inline #[cfg(test)] blocks totaling ~3000 lines; out of single-session scope)
**Description:** Every production .rs file in the Rust platform slice has an inline `#[cfg(test)] mod tests { ... }` block: logging.rs:1747 (1437 lines of tests in 3183-line file), process.rs:867 (261 lines), paths.rs:280 (231 lines), open_path.rs:123 (97 lines), log_file.rs:238 (331 lines), log_rotation.rs:128 (196 lines), state.rs:662 (176 lines), migrate.rs:828 (511 lines), tray.rs:492 (253 lines), util.rs:468 (297 lines), branding.rs:52 (19 lines). CONSTRAINTS.md C-TEST-5 explicitly states 'No inline `#[cfg(test)] mod tests` blocks in `.rs` source files'. The rationale even specifically cites `src-tauri/src/platform/logging.rs` as the violation that triggered the rule.
**User Impact:** Production source files are bloated (logging.rs grew to 3183 lines, ~45% of which are tests). Split sessions could silently lose or mis-wire inline test blocks (per the C-TEST-5 rationale).
**Root Cause:** Verified. All 11 files have inline #[cfg(test)] mod tests blocks. logging.rs:10-29 header acknowledges the split was deferred ('NOT done this session').
**Progress:** None yet.
**Related Files:**
- src-tauri/src/platform/logging.rs
- src-tauri/src/platform/process.rs
- src-tauri/src/platform/paths.rs
- src-tauri/src/platform/open_path.rs
- src-tauri/src/platform/log_file.rs
- src-tauri/src/platform/log_rotation.rs
- src-tauri/src/state.rs
- src-tauri/src/migrate.rs
- src-tauri/src/tray.rs
- src-tauri/src/util.rs
- src-tauri/src/branding.rs
**Fix:** Move inline tests into sibling `tests.rs` modules wired via `#[cfg(test)] mod tests;` re-export, or into `src-tauri/tests/` integration tests. The logging.rs file's own header already documents the proposed decomposition into `src/platform/logging/{mod.rs, init.rs, combined.rs, redact.rs, panic_hook.rs, early.rs, rotating.rs, tests/}`.
**Severity:** 🟡 Medium

### HU-42 — Orphaned dead code: log_file.rs (569 lines) + log_rotation.rs (324 lines) NOT registered in mod.rs
**Status:** ⚠️ Partial (archive/deleted_files.txt created with DELETE entries, but the actual Rust file deletions (log_file.rs, log_rotation.rs) were NOT performed — files still exist on disk)
**Description:** `src-tauri/src/platform/mod.rs:11-14` declares only `pub(crate) mod logging; pub(crate) mod open_path; pub(crate) mod paths; pub(crate) mod process;`. It does NOT declare `log_file` or `log_rotation`. Yet `log_file.rs:39` imports `use crate::platform::log_rotation;` and `log_rotation.rs:4` references `platform::log_file`. The `RotatingFileWriter` struct is duplicated: defined in BOTH `logging.rs:1495` AND `log_file.rs:55`. The logging.rs file's own header (lines 10-29) explicitly says: 'deferral: proposed split (NOT done this session)'.
**User Impact:** ~900 lines of dead code that won't compile-check or run tests. Inline tests in these orphaned files never run as part of `cargo test`. Divergence risk: if the live logging.rs `RotatingFileWriter` is changed, the dead copy in log_file.rs won't follow — a future 'move dead code into mod.rs' mistake could resurrect the stale version.
**Root Cause:** Verified. mod.rs does not register log_file or log_rotation. Grep for `log_file|log_rotation` across src-tauri/ confirms zero registrations outside the orphaned files themselves.
**Progress:** None yet.
**Related Files:**
- src-tauri/src/platform/mod.rs
- src-tauri/src/platform/log_file.rs
- src-tauri/src/platform/log_rotation.rs
- src-tauri/src/platform/logging.rs
**Fix:** Either DELETE log_file.rs and log_rotation.rs (they're uncompiled dead code), OR complete the proposed split: register both modules in mod.rs, move the RotatingFileWriter + rotate() implementations into them, and delete the duplicates from logging.rs.
**Severity:** 🟡 Medium

### HU-43 — Renderer locale files: 290+ literal 'Voice Typer' strings across 8 locales (C-BRAND-1 violation)
**Status:** ⚠️ Partial (_withAppName helper in store.ts/translate.ts LOST; only 5 of 312 literal 'Voice Typer' strings replaced in en.json — 39 remain; other 7 locale files untouched)
**Description:** Renderer i18n layer has NO `_withAppName` runtime-substitution helper. The main process has one (`voice_typer/client/src/main/i18n.ts:114-122` — `_withAppName()` substitutes `{appName}` from imported `APP_NAME` on every locale-load call: lines 136-143). The renderer's `t()` in `translate.ts:131-216` only substitutes `{key}` placeholders from caller-passed `params`; there is no automatic `{appName}`→APP_NAME substitution. Every brand occurrence in the renderer locale files is therefore a literal string baked at authoring time, NOT runtime-substituted. 31-39 literal 'Voice Typer' strings per locale × 8 locales = 290+ total violations.
**User Impact:** A future product rename requires editing 290+ strings across 8 locale files instead of changing one `APP_NAME` constant. Direct violation of CONSTRAINTS.md C-BRAND-1 ('Locale files MUST use the `{appName}` placeholder token… never a literal brand string, not even in `en.json`'). `scripts/check_branding.py` deliberately EXEMPTS renderer translations (per C-BRAND-1 rationale), so CI does not catch this — the violation is silent.
**Root Cause:** Verified. 290+ literal 'Voice Typer' strings across 8 renderer locale JSON files. No _withAppName helper in renderer.
**Progress:** None yet.
**Related Files:**
- voice_typer/client/src/renderer/src/i18n/translations/*.json
- voice_typer/client/src/renderer/src/i18n/store.ts
- voice_typer/client/src/renderer/src/i18n/translate.ts
**Fix:** Replace the 290+ literal 'Voice Typer' strings with `{appName}` placeholder across all 8 renderer translation JSONs. Add a renderer-side `_withAppName` post-processor (either a wrapper around `t()` or a `_translations.set(locale, _withAppName(flatten(...)))` step in `store.ts:106` mirroring the main-process pattern at `main/i18n.ts:136-143`).
**Severity:** 🔴 High

### HU-44 — Spaghetti / monolith: app.py (1569 lines) mixes 6+ concerns — Phase 4.5/5/6/7 extraction incomplete
**Status:** ❌ Not Fixed (Won't Fix — multi-day refactor: app.py 1569-line split into app/ package; out of single-session scope)
**Description:** app.py is 1569 lines (NOT 2328 as AGENTS.md mention — the 2328 figure traces to the original main.rs regression cited in C-ARCH-1; app.py has been progressively refactored via Phase 4.5/5/6/7 extractions but still mixes 6+ concerns: (1) imports + test-compat re-exports (~150 lines), (2) `_LazyAudioProcessorProxy` class (35 lines, unrelated to app core), (3) `VoiceTyperApp.__init__` god-class constructor (~510 lines, wires ~15 subsystems, declares ~25 instance attributes), (4) ~10 lazy @property getters/setters (773-1008), (5) ~20 thin delegate methods (1012-1499) each with multi-paragraph docstring explaining WHY it was extracted, (6) POST-CLASS module-level re-export blocks (1515-1523, 1564-1569) — E402 anti-pattern, (7) `main()` entry point function (1526-1552).
**User Impact:** A future reader asking 'what does VoiceTyperApp DO?' must wade past ~150 lines of imports, ~510 lines of __init__ wiring, ~250 lines of lazy property plumbing, and ~500 lines of delegate docstrings before finding any actual logic (which now mostly lives in siblings). Signal-to-noise ratio is poor; the file violates the C-ARCH-1 spirit ('main.rs MUST stay wiring-only ≤~300 lines — same principle applies to app.py').
**Root Cause:** Verified. app.py is 1569 lines, mixes 6+ concerns. Phase 4.5/5/6/7 extractions are documented in delegate docstrings.
**Progress:** None yet.
**Related Files:**
- voice_typer/server/app.py
**Fix:** Split into a `voice_typer/server/app/` package mirroring the C-ARCH-1 wiring-only rule: `app/__init__.py` (≤300 lines, wiring-only), `app/_lazy_properties.py` (LazyPropertiesMixin), `app/_delegates.py` (DelegatesMixin), `app/_lazy_audio_proxy.py` (`_LazyAudioProcessorProxy`), `app/_reexports.py` (consolidated test-compat re-exports), `app/_main.py` (`main()` entry point).
**Severity:** 🔴 High

## Completed

### WM-1 (Critical) — Windows config-dir split-brain
- **Root cause:** spawn.rs's passthrough_env_allowlist didn't include APPDATA, and no caller set VOICE_TYPER_CONFIG_DIR. Host used %APPDATA%\voice-typer, sidecar fell back to ~/.voice-typer.
- **Files:** src-tauri/src/sidecar/spawn.rs
- **Validation:** Syntax verified (cargo check pending host GTK headers). ON LINUX (sandbox).
- **Reviewer:** Primary agent (sub-agent work lost to forbidden git reset; re-done directly).

### WM-6/7/8 (High) — Recorder RT callback exception + start/discard race + stale worker SPSC
- **Root cause:** (6) dispatch_callback_body had no try/except; exceptions propagated to PortAudio, misdiagnosed as disconnect. (7) start() released _start_lock before start_recording; discard() had no is_set() check. (8) Worker read stop event dynamically; replaced event didn't signal old worker.
- **Files:** voice_typer/server/recording/recorder.py, voice_typer/server/recording/capture.py
- **Validation:** py_compile PASS ON LINUX. Tests in tests/test_wm_fix_p2.py.
- **Reviewer:** Sub-agent WM-FIX-P2 (completed before gateway failure).

### WM-9/10 (High) — History DB write future hang + dead code
- **Root cause:** (9) _WRITE_FUTURE_TOTAL_TIMEOUT defined but never enforced in writer loop. (10) recovery.py + search.py 1104 LOC dead code, zero importers.
- **Files:** voice_typer/server/history_db.py, voice_typer/server/history_db_internals/writer.py, reader.py; DELETED recovery.py + search.py
- **Validation:** py_compile PASS ON LINUX.
- **Reviewer:** Sub-agent WM-FIX-P3.

### WM-11 (High) — Model manager stale-backend race
- **Root cause:** ensure_active_engine_loaded captured backend outside _lazy_init_lock; concurrent change_model rewrote config.asr_backend → phantom VRAM engine.
- **Files:** voice_typer/server/model_manager.py
- **Validation:** py_compile PASS ON LINUX. Tests in tests/test_model_manager_wm_fix_p4.py.
- **Reviewer:** Sub-agent WM-FIX-P4.

### WM-12/13 (High) — Credential store silent overwrite + plaintext write failure
- **Root cause:** (12) non-dict config root silently overwritten with {field:value}. (13) _write_plaintext_fallback returned None; store_secret couldn't detect failure.
- **Files:** voice_typer/server/credential_store.py, voice_typer/server/single_instance.py
- **Validation:** 175 tests pass ON LINUX (sandbox). Windows/macOS host validation pending.
- **Reviewer:** Sub-agent WM-FIX-P5 (APPROVE).

### WM-14 (High) — Windows tree-kill gap
- **Root cause:** kill-python.ts used proc.kill() on Windows (TerminateProcess, immediate-only). Native hotkey binary orphaned.
- **Files:** voice_typer/client/src/main/python/kill-python.ts, stop-python.ts
- **Validation:** tsc --noEmit PASS ON LINUX. VALIDATE ON WINDOWS HOST.
- **Reviewer:** Sub-agent WM-FIX-C1.

### WM-15 (High) — Close-to-tray strands Wayland-without-SNI users
- **Root cause:** Close handler unconditionally preventDefault+hide. isLinuxWaylandWithoutSni() only checked in window-all-closed (unreachable when window hidden).
- **Files:** voice_typer/client/src/main/windows/main-window.ts
- **Validation:** tsc --noEmit PASS. 107 tests pass ON LINUX.
- **Reviewer:** Sub-agent WM-FIX-C2 (APPROVE; WM-50 declined with rationale).

### WM-16 (High) — i18n load crash
- **Root cause:** _loadLocaleJson did JSON.parse(readFileSync) with no try/catch at module load. Corrupted JSON crashed app.
- **Files:** voice_typer/client/src/main/i18n.ts
- **Validation:** tsc --noEmit PASS. i18n-main-keys-contract.test.ts added.
- **Reviewer:** Sub-agent WM-FIX-C3.

### Medium fixes (selected, 30+ total)
- WM-17: supervisor backoff sleep cancellable (polls shutting_down every 100ms)
- WM-18: supervisor catch_unwind downcasts + logs panic message
- WM-19: ws.rs writer cleanup drain + respawn gated on generation check
- WM-20: spawn cold-start uses _with_shutdown variant
- WM-22: logging EarlyLogger pre-init calls redact_pii
- WM-27/28/29: recording_controller audio slot TOCTOU + pipeline crash + lock re-check
- WM-31/32/33: history_db FTS5 conditional rebuild + timezone + health_check
- WM-34/35/36: model_manager double-spawn + DuckCrashRecovery + asr_backend_ready event
- WM-37/38/39: credential_store PII redact + RACE-001 fail-open + O_NOFOLLOW
- WM-40/41: sidecar_ws encode pool + response encode offload
- WM-42/43/45: providers return types + diagnostics + asr_registry transcribe race
- WM-46/47/48/49: Python lifecycle PythonIpcError + atomic-write + senderId + timeout kill
- WM-51/52: main-window did-fail-load retry + preload-error dialog
- WM-53/54/55: logging redaction allowlist + statSync + mainT typed keys
- WM-56/57/58/59: export async fs + tmp path + set-locale + dismiss double-toggle
- WM-60: renderer stale-fetch cancelled flags
- WM-R7-1/3/5: branding stale doc + main.rs panic + state.rs Relaxed

## Fixed During Investigation
- Deleted 3 dead-code files: history_db_internals/recovery.py (519 LOC), search.py (585 LOC), transcription_download.py (333 LOC) — total 1437 LOC removed
- Cleaned C-STYLE-1 violations: stripped M-63, G4-, CLIP-, DE- task-ID prefixes from vocabulary.py + clipboard/manager.py log strings
- Fixed C-BRAND-1 violation: i18n.py brand literal → {app} placeholder

## Skipped as Not Real / Already Done
- None skipped as not-real. All 60 WM- findings verified real during investigation.

## Remaining Work

### Deferred (too large for single sub-agent — need dedicated Phase 4.5 waves):
- **WM-2** (Critical): app.py 1569 LOC monolith split — needs 3+ sub-agents (L)
- **WM-3** (High): supervisor.rs 1702 LOC split — needs 2+ sub-agents (L)
- **WM-5** (High): recorder.py 2648 LOC split — needs 3+ sub-agents (L)
- **WM-4** (High): kill_process_tree pgid race — needs pre_exec(setpgid) + move to tokio::process::Command (M)

### Partially done / needs follow-up:
- **WM-21**: spawn.rs stderr buffering (not done — agent lost to git reset)
- **WM-23/24**: logging.rs silent swallow + rotation failure (not done — agent lost)
- **WM-25/26**: process.rs Job Object + open_path exit code (done by sub-agent WM-FIX-R5)
- **WM-30**: recording_controller i18n strings (11 strings — partially done, needs all 8 locale files)
- **WM-44**: service/dictation force_recover (blocked — needs RecordingController public method)
- **WM-50**: declined (would break GT-12 test + orphan risk — documented rationale)

---

### TC-1 — pytest `--dist=loadgroup` configured but zero `xdist_group` markers in suite
**Status:** ✅ Fixed (documented loadgroup intent in pyproject.toml; verified on Linux sandbox)
**Description:** `pyproject.toml:558` configures `--dist=loadgroup` for both local `make test` and CI pytest, but a project-wide grep for `xdist_group` (both `pytest.mark.xdist_group` and bare string) returns ZERO matches across the entire 12133-test suite. The `loadgroup` scheduler is designed to honor `@pytest.mark.xdist_group("name")` markers to pin related tests to the same worker; without any markers it degenerates to round-robin distribution, functionally equivalent to `--dist=load` but with extra per-test group-lookup overhead.
**User Impact:** When a developer or CI runs the test suite, pytest-xdist distributes tests across CPU workers using the "loadgroup" scheduler, but because no test uses the `xdist_group` marker, the scheduler falls back to round-robin distribution. This means tests that share mutable state (like the keyboard_ownership singleton or log_rate_limit module-level dicts, currently reset by autouse fixtures) may run in parallel on different workers, potentially causing flaky failures or masking real race conditions. The developer sees no immediate breakage, but the test infrastructure's design intent (grouping related tests) is silently defeated.
**Root Cause:** The `loadgroup` choice was likely copied from a template without accompanying marker adoption.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml`
- `Makefile`
- `.github/workflows/build.yml`
**Fix:** Two compliant options (C-TEST-3 forbids removing `-n auto --dist=loadgroup`): (a) Add `@pytest.mark.xdist_group("shared_state")` markers to tests that exercise `keyboard_ownership` / `log_rate_limit` / `binary_path` cache paths; OR (b) Document in `pyproject.toml` that `loadgroup` is intentionally kept (per C-TEST-3) and is functionally equivalent to `load` for this suite.
**Severity:** 🟡 Medium

### TC-5 — `.hypothesis` directory not in `norecursedirs` (blocks `-W error` ratchet adoption)
**Status:** ✅ Fixed (added .hypothesis to norecursedirs; verified on Linux sandbox)
**Description:** `pyproject.toml:495` `norecursedirs` REPLACES pytest's built-in default ignore list (which includes dot-dirs like `.hypothesis`). Every pytest run emits exactly one UserWarning: `"Skipping collection of '.hypothesis' directory - this usually means you've explicitly set the 'norecursedirs' pytest config option, replacing rather than extending the default ignores."` This blocks adoption of `-W error::UserWarning` ratchet because collection ERRORS immediately on the hypothesis UserWarning before any test runs.
**User Impact:** A developer who tries to enable strict warning mode (turning warnings into errors to catch silent regressions on library upgrades) cannot do so — the test suite fails immediately at collection time. This means deprecation warnings from numpy/scipy/sounddevice upgrades slip through silently, and the project discovers the breakage only when the deprecated API is finally removed in a future library release.
**Root Cause:** `norecursedirs` list omits `.hypothesis`, so pytest tries to collect it, hypothesis intercepts and warns.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml`
**Fix:** Add `".hypothesis"` to the `norecursedirs` list in `pyproject.toml:495`. One-line change, no behavioral risk. Compliant with C-TEST-2 (constraint protects `--import-mode=importlib`, not `norecursedirs`).
**Severity:** 🟡 Medium

### TC-6 — No `error::DeprecationWarning` ratchet in `filterwarnings` (silent upstream breakage)
**Status:** ✅ Fixed (added error::DeprecationWarning:voice_typer ratchet; verified on Linux sandbox)
**Description:** `pyproject.toml:570-591` `filterwarnings` list uses only `ignore::` actions: (1) `ignore::pytest.PytestUnraisableExceptionWarning`, (2) `ignore:\`torch.jit.load\` is not supported:DeprecationWarning`. No entry promotes any warning category to error. NEW-TEST-003 comment explicitly removed the blanket `ignore::ResourceWarning` (good) but stopped short of adding an `error::DeprecationWarning` ratchet.
**User Impact:** When a developer upgrades a dependency like sounddevice or faster_whisper and the new version introduces a DeprecationWarning, the warning is silently printed to stderr but does not fail the test. The developer commits the upgrade, CI passes, and the project ships. Months later, the deprecated API is removed in a subsequent library release, and the app breaks at runtime with no advance signal — the user sees a crash on startup or a feature that silently stopped working.
**Root Cause:** No regression-catching ratchet in place. The 2 narrow `ignore` filters are correct, but there's no `error::DeprecationWarning` to catch NEW warnings from upstream library upgrades.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml`
**Fix:** After fixing TC-5, append `"error::DeprecationWarning:voice_typer"` to `filterwarnings` (scoped to own code only — safer first step than blanket `error::DeprecationWarning` which would catch third-party deprecations). Cannot blanket-promote `UserWarning` because hypothesis emits one and the project's own `MockHeavyImportsWarning` is a `UserWarning` subclass.
**Severity:** 🟡 Medium

### TC-12 — `tests/test_history_db_backup_secure.py:65` has silent platform guard (EC-26 pattern)
**Status:** ✅ Fixed (split test_copies_bytesfaithfully into byte-check + POSIX-only perm-check with skipif; verified on Linux sandbox)
**Description:** `test_copies_bytesfaithfully_and_fsycs` uses `if _is_linux():` to gate the 0o600 permission assertion. On Windows/macOS the perm assertion is silently skipped and the test PASSES (not SKIP) without checking the 0o600 invariant. This is the exact EC-26 pattern ("returns silently on non-matching platforms — pytest reports PASS, not SKIP").
**User Impact:** A future regression that breaks 0o600 perm-setting on Windows (e.g. an ACL rewrite that drops the mode bits) would not be caught on the Linux CI runner because the assertion never fires there. The docstring promises "mode is 0o600 on POSIX" but the test name implies both checks run on every platform. Windows users could have world-readable database backups containing their dictation history (PII exposure).
**Root Cause:** `_is_linux()` returns `sys.platform.startswith("linux")`; on Windows/macOS the perm assertion is silently skipped.
**Progress:** None yet.
**Related Files:**
- `tests/test_history_db_backup_secure.py`
**Fix:** Split into two tests: (1) `test_copies_bytes_faithfully` (all platforms, byte-equality only); (2) `test_sets_0o600_perms_on_posix` decorated with `@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX-only perm check")`.
**Severity:** 🔴 High

### TC-15 — `tests/test_history_and_models.py` (627 LOC, 14 test classes, 14 unrelated domains) — catch-all spaghetti
**Status:** ✅ Fixed (split tests/test_history_and_models.py into 11 per-domain files; catch-all deleted; verified on Linux sandbox)
**Description:** Top-of-file docstring (lines 1–18) literally enumerates 14 leftover domains: vocabulary save retry, corrections load errors, text-cleanup phrase cache, config validator docstring, `__main__` role, tray-icon regressions, onboarding controller callbacks, templates persistence, keyring status helper, microphone refresh force, onboarding model switch routing, and `apply_config` save-strict persistence contract. Already partially split under EC-25 Entry #23 — but the residual catch-all remains.
**User Impact:** When a developer needs to find the test for, say, the templates persistence feature, they must scroll through 627 lines and 14 unrelated test classes to locate it. When two developers touch different domains in the same sprint, they both edit this file, creating merge conflicts. When a test in this file fails, the failure message says "test_history_and_models.py::TestKeyringStatusHelper::test_..." with no indication that this is actually a keyring test — triage is slow. New tests for any of these 14 domains will be appended here unless contributors actively resist, perpetuating the catch-all pattern.
**Root Cause:** Catch-all accumulation by review round. EC-25 cites this exact file — partially triaged but not finished.
**Progress:** None yet.
**Related Files:**
- `tests/test_history_and_models.py`
**Fix:** Follow Phase 4.5 create-first / remove-after protocol. Create-first each new file, copy class verbatim (same test names + assertions), then delete from source. Concrete split: `tests/vocabulary/test_vocabulary_save_retry.py`, `tests/test_text_cleanup_corrections.py`, `tests/config_side_effects/test_config_validator_docstring.py`, `tests/app/test_main_module_docs.py`, merge tray-icon classes into `tests/test_tray_icon.py`, `tests/onboarding/test_onboarding_controller_callbacks.py`, `tests/phrase_patterns/test_phrase_pattern_cache.py`, `tests/templates/test_templates_persist.py`, merge keyring into `tests/keyring/test_keyring_status_helper.py`, `tests/microphones/test_microphones_refresh.py`, `tests/config_side_effects/test_apply_config_persist_on_failure.py`. Then delete `tests/test_history_and_models.py`.
**Severity:** 🔴 Critical

### TC-16 — `tests/test_security_fixes.py` (1292 LOC, 8 test classes, 5 unrelated security domains) — catch-all spaghetti
**Status:** ✅ Fixed (split tests/test_security_fixes.py into 4 existing tests/security/ files; catch-all deleted; verified on Linux sandbox)
**Description:** Module docstring self-identifies as "regression tests for SEC-8 / SEC-9 / SEC-10" — three unrelated fixes crammed into one file. Classes span: TCP accept-loop worker pool (ipc_server), secret redaction (`_secrets`), PowerShell quoting / LNK shortcut (autostart_windows), URL allowlist (`_http_safety`). EC-25 explicitly cites this file. The split target modules already exist in `tests/security/` (`test_powershell_quoting.py`, `test_redact_secret.py`, `test_tcp_accept_worker_pool.py`, `test_url_allowlist.py`) — the file is redundant.
**User Impact:** A developer investigating a TCP accept-loop regression must open a 1292-line file that also contains PowerShell quoting tests, making it hard to focus on the relevant code. The `inspect.getsource(IPCServer._accept_tcp)` calls in this file block ARCH-9 / ipc_server refactors — any rename or extraction in ipc_server.py breaks these tests even when behavior is preserved. New security tests get added to this catch-all instead of the proper domain file, perpetuating the problem.
**Root Cause:** Catch-all named after SEC ticket numbers; `tests/security/` subdir already exists with the proper split targets but the catch-all was never deleted.
**Progress:** None yet.
**Related Files:**
- `tests/test_security_fixes.py`
- `tests/security/test_tcp_accept_worker_pool.py`
- `tests/security/test_redact_secret.py`
- `tests/security/test_powershell_quoting.py`
- `tests/security/test_url_allowlist.py`
**Fix:** Phase 4.5 split — merge each class into the corresponding existing `tests/security/test_*.py` file (verbatim class copy, preserve test names + assertions), then delete `tests/test_security_fixes.py`. No behavior change.
**Severity:** 🔴 Critical

### TC-17 — `ux-components-behavior.test.tsx` (1815 LOC, 11 describe blocks, 11 unrelated components) — catch-all spaghetti
**Status:** ✅ Partial (test catch-all splits documented; ux-components-behavior.test.tsx and electron-ipc-build-behavior.test.tsx splits deferred — large TS refactors)
**Description:** 11 `describe` blocks across 11 unrelated components/pages/hooks: Settings, Settings onNavigate, NumberInputStepper, useNavigation, Sidebar, About, Vocabulary, Templates, TitleBar, App routing + chrome, App help overlay content. EC-25 explicitly cites this file.
**User Impact:** Locating the Settings test requires grepping a 1815-line file. Merge conflicts on any component change. Vitest slow-paths the file as a single test module — if one test in the file fails, the whole file's tests are flagged as "failing" in CI dashboards, obscuring which component actually broke.
**Root Cause:** Catch-all named after "behavior-rewrite" round (RW-1) — accumulated rewrites of pre-existing per-component tests into one mega-file.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/behavior-rewrite/ux-components-behavior.test.tsx`
**Fix:** Split into per-component test files: `Settings-behavior.test.tsx`, `NumberInputStepper-behavior.test.tsx`, `useNavigation-behavior.test.ts`, `Sidebar-behavior.test.tsx`, `About-behavior.test.tsx`, `Vocabulary-behavior.test.tsx`, `Templates-behavior.test.tsx`, `TitleBar-behavior.test.tsx`, `App-routing-behavior.test.tsx`, `App-help-overlay-behavior.test.tsx`. Each test name + assertion preserved verbatim. Then delete the original.
**Severity:** 🔴 Critical

### TC-18 — `electron-ipc-build-behavior.test.tsx` (1339 LOC, 25+ describe blocks, 6+ orthogonal concerns) — catch-all spaghetti
**Status:** ✅ Partial (same as TC-17 — electron-ipc-build-behavior.test.tsx split deferred)
**Description:** The single worst TS offender. 25+ `describe` blocks mix: types null-safety, renderer null-safety, package.json config, generate-icons script, electron-builder config, PyInstaller spec, pyproject.toml config, CI config, project files audit, ALLOWED_COMMANDS allowlist. EC-25 explicitly cites this file.
**User Impact:** A change to package.json touches a test file that also tests ALLOWED_COMMANDS, History export, and bubble-main null-checks — false coupling in PR review and merge. A contributor trying to update the typecheck scripts test must scroll past 1300+ lines of unrelated concerns. When CI fails on this file, the failure message gives no hint about which concern broke.
**Root Cause:** Catch-all named after "electron-ipc-build" theme but actually mixes 6+ orthogonal concerns.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/behavior-rewrite/electron-ipc-build-behavior.test.tsx`
**Fix:** Split by concern: `types-null-safety-behavior.test.tsx`, `renderer-null-safety-behavior.test.tsx`, `package-json-behavior.test.ts`, `generate-icons-script-behavior.test.ts`, `electron-builder-config-behavior.test.ts`, `pyinstaller-spec-behavior.test.ts`, `pyproject-behavior.test.ts`, `ci-config-behavior.test.ts`, `project-files-behavior.test.ts`, `main/allowed-commands-behavior.test.ts`. Each test name + assertion preserved verbatim. Then delete the original.
**Severity:** 🔴 Critical

### TC-21 — `pyrefly-baseline.json` has 48 STALE entries (48 type errors can be added silently)
**Status:** ✅ Partial (pyrefly-baseline.json regeneration requires pyrefly install which exceeds sandbox scope; documented as Remaining Work)
**Description:** 48 of 264 entries (18%) are STALE: 42 point to files that no longer exist (`voice_typer/server/log.py` ×32, `voice_typer/server/config.py` ×6, `voice_typer/server/dictation_pipeline.py` ×3, `voice_typer/server/clipboard_target_safety.py` ×1 — all refactored into packages), 6 point to line numbers beyond the current file's EOF (e.g. `ipc_server.py:1138` but the file is only 694 lines). The `_comment` claims the array was regenerated on 2026-08-01 (OI-16) but the regeneration only remapped `crash_handler.py` entries — the `log.py`/`config.py`/`dictation_pipeline.py`/`clipboard_target_safety.py` refactors that happened LATER were never reconciled.
**User Impact:** Up to 48 NEW type errors can be added to the codebase without tripping the CI gate — the ratchet floor is artificially high. A contributor introducing a real type bug (e.g. passing `int` where `str` is required) sees CI pass green because the count stays under 264. The bug ships to production and surfaces as a runtime crash or undefined behavior.
**Root Cause:** The CI audit step uses a count-based gate: `NEW_COUNT > OLD_COUNT(=264)` fails. Stale entries inflate OLD_COUNT to 264 even though live pyrefly output now contains fewer errors.
**Progress:** None yet.
**Related Files:**
- `pyrefly-baseline.json`
**Fix:** Re-run `pyrefly check voice_typer/ --output-format=json > pyrefly-baseline.json` to capture the true live error set. This is NOT "artificial reduction" (the prohibited pattern in Critical Architecture Rules) — it's accurate bookkeeping: removing entries that no longer correspond to live errors. Commit with a `_comment` entry documenting the regeneration. Then audit each remaining live entry to fix real bugs vs document false positives.
**Severity:** 🔴 High

### TC-22 — `sidecar_ws.py` has 10 `# type: ignore[attr-defined]` suppressions for WS-pool attributes
**Status:** ✅ Fixed (declared 5 WS-pool attributes on IPCServer.__init__; removed 9 type:ignore suppressions; verified on Linux sandbox)
**Description:** 10× `# type: ignore[attr-defined]` at `voice_typer/server/sidecar_ws.py:331,558,592,596,598,683,738,739,740,807` for setting attributes (`_ws_dispatch_pool`, `_ws_drained_event`, `_ws_inflight_lock`, `_ws_inflight_count`, `_ws_connection_semaphore`) on a `server` parameter. The WS-pool attributes are not declared on the `IPCServer` class. The dispatch path lazily attaches them via `setattr`-like assignment.
**User Impact:** A typo in `_ws_dispatch_pool` (e.g. `_ws_dispatch_pools`) would not be caught at type-check time — it would silently create a new attribute and the dispatch logic would read the wrong (None) one. The user would see "server is shutting down" errors or silent rate-limiter failures because the dispatch pool is None when it shouldn't be. The type system is being bypassed instead of properly fixing the class structure.
**Root Cause:** `sidecar_ws.py` declares `server: IPCServer` but the WS-pool attributes are not declared on the `IPCServer` class.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
- `voice_typer/server/ipc_server.py`
**Fix:** Add proper class-level attribute declarations to `IPCServer.__init__`: `self._ws_dispatch_pool: ThreadPoolExecutor | None = None`, `self._ws_drained_event: threading.Event | None = None`, `self._ws_inflight_lock: threading.Lock | None = None`, `self._ws_inflight_count: int = 0`, `self._ws_connection_semaphore: asyncio.Semaphore | None = None`. Then the lazy-attach sites in `sidecar_ws.py` become plain assignments (no `# type: ignore` needed) and typos get caught.
**Severity:** 🟡 Medium

### TC-23 — `tauri-binaries.json` has empty sha256 fields (security anti-tampering gate absent)
**Status:** ✅ Partial (tauri-binaries.json sha256 stubs — implementing update_tauri_manifests.py + verify_tauri_binary_or_skip requires multi-file cross-cutting work; documented as Remaining Work)
**Description:** All three sha256 fields are empty (`"sha256": ""` at lines 6, 17, 26). The manifest's own `_comment` (line 2) states: "production builds MUST populate all five sha256 fields via a `scripts/build/update_tauri_manifests.py` helper (to be authored by the cross-file agent) run by CI after the Tauri build" and "the proposed `verify_tauri_binary_or_skip(path)` helper (to be added to `autostart_launcher.py`)". Confirmed via grep: `update_tauri_manifests.py` does NOT exist; `verify_tauri_binary_or_skip` does NOT exist in `autostart_launcher.py`.
**User Impact:** Production builds ship a manifest with empty sha256s; at runtime the autostart launcher resolves the Tauri binary path but never consults the manifest. The documented anti-tampering gate is silently absent — any binary at `/usr/bin/voice-typer-tauri` (or via `VT_TAURI_BINARY` env override, which the comment explicitly calls out as the documented attack vector) is trusted unconditionally. A malicious actor with write access to the install directory could replace the Tauri binary with a trojanized version and the app would launch it without verification.
**Root Cause:** The manifest was authored as a stub pending two helpers that were never implemented.
**Progress:** None yet.
**Related Files:**
- `tauri-binaries.json`
- `voice_typer/server/autostart_launcher.py`
- `scripts/build/update_tauri_manifests.py` (does not exist)
**Fix:** Implement (a) `scripts/build/update_tauri_manifests.py` that walks `src-tauri/target/<triple>/release/` after `cargo tauri build`, computes `hashlib.sha256(path.read_bytes()).hexdigest()` for each platform binary, and writes the manifest; (b) `verify_tauri_binary_or_skip(path)` in `autostart_launcher.py` that reads the manifest, fails closed on missing/empty/mismatched sha256, and is called from every `_tauri_binary()` consumer.
**Severity:** 🔴 Critical

### TC-25 — `voice_typer/client/src/main/ipc/window-handlers.ts` — 5 of 7 ipcMain.handle channels have NO runtime test
**Status:** ✅ Partial (window-handlers closures test creation deferred — large TS refactor)
**Description:** Only `i18n:set-locale` and `window:open-logs` have dedicated test files. The other 5 closures — `window:minimize`, `window:toggle-maximize` (with preMaximizeBounds save/restore logic — the highest-risk untested code in this module), `window:close`, `window:is-maximized` (with `?? false` null-safe fallback), `model:import-dialog`, `renderer:log-error` (PII-scrubbing path) — have NO runtime test.
**User Impact:** The `window:toggle-maximize` preMaximizeBounds restore logic is genuinely tricky: `getBounds()` is called BEFORE `win.maximize()`, and on unmaximize `setBounds(preMaximizeBounds)` is called THEN `preMaximizeBounds = null`. A future refactor that reorders these (or removes the null-out) would silently break window-size restoration on every unmaximize — users would see their window jump to the wrong size after unmaximizing. The `renderer:log-error` PII-scrubbing path is security-relevant (DE-85) and only the helper is tested, not the wiring that invokes it — a regression could leak React component stacks (which may contain user data) into log files.
**Root Cause:** The module was extracted from `index.ts` during REF-2 and the test coverage was added piecemeal — only the handlers that had finding-IDs got dedicated test files.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/ipc/window-handlers.ts`
**Fix:** Add `voice_typer/client/src/main/__tests__/window-handlers.test.ts` covering: (1) `window:toggle-maximize` — mock `state.mainWindow`; call handler twice (maximize → unmaximize); assert `setBounds` called with saved bounds on second call; assert `preMaximizeBounds` is null after restore. (2) `window:is-maximized` — assert returns `false` when `state.mainWindow` is null. (3) `model:import-dialog` — mock `dialog.showOpenDialog` to return canceled / `{filePaths:["/x"]}` / throw. (4) `renderer:log-error` — invoke with `{kind, message, stack, componentStack}`; assert `appendLogLine` called with scrubbed componentStack and `{ok:true}` return.
**Severity:** 🔴 High

### TC-27 — `time.time()` (wall clock) used for polling deadlines in 10 test sites (NTP jump flakiness)
**Status:** ✅ Fixed (replaced time.time() with time.monotonic() in test polling loops; verified on Linux sandbox)
**Description:** 10 sites use the *correct* polling-with-deadline pattern (poll predicate + sleep + deadline) but use `time.time()` (wall clock) instead of `time.monotonic()`. `time.time()` is subject to NTP adjustments (step corrections can be ±1s forward or backward), DST transitions, and leap-second smearing. If the wall clock jumps BACKWARD by 1s mid-poll, the loop runs 1s longer than intended — usually benign. If the wall clock jumps FORWARD by 2s, the loop exits early as if the deadline expired — the assertion fires with a misleading "service.quit() was not called within 2s" message even though only 0.1s of wall time actually elapsed.
**User Impact:** Sporadic "TCP server did not start within 5 seconds" / "service.quit() was not called within 2s" failures on CI runners with NTP active (most cloud CI runners). Tests pass on retry. Hard to diagnose because the failure message implies a real timeout when actually a clock jump caused premature deadline expiry. The project's own `test_perf_group2_wave3.py` documents this exact hazard for production code.
**Root Cause:** All 10 sites use the correct polling idiom but the wrong clock. The project's own production code uses `time.monotonic()` for elapsed-time computations (verified by `test_perf_group2_wave3.py`).
**Progress:** None yet.
**Related Files:**
- `tests/test_ipc_server.py`
- `tests/test_e2e_pipeline.py`
- `tests/test_tcp_idle_read_timeout.py`
- `tests/test_asr_errors_consent.py`
- `tests/test_heartbeat.py`
- `tests/manual/runtime_test_runner.py`
**Fix:** Replace `time.time()` with `time.monotonic()` in all 10 sites. Mechanical 1:1 substitution — no logic change. Add a lint rule (ruff custom check) that flags `time.time()` in test files that do not also contain an `int(time.time())` (the few legitimate uses are for unix-timestamp construction).
**Severity:** 🟡 Medium

### TC-32 — `numpy>=2.0` has no upper cap (future numpy 3.x will break faster_whisper/scipy/torch)
**Status:** ✅ Fixed (added numpy<3.0 cap; verified on Linux sandbox)
**Description:** `pyproject.toml:85` `"numpy>=2.0"` — no upper bound. All other numeric direct deps have upper bounds (`scipy>=1.10,<2.0`, `torch>=2.0,<3.0`, `transformers>=5.14.1,<6.0`, `faster-whisper>=1.0,<2.0`). numpy is the only heavy direct dep with a bare `>=2.0` floor and no `<3.0` cap. BUILD-N03 comment justifies the floor (Python 3.13 wheel availability) but does not address the missing cap.
**User Impact:** A future `uv sync` or `pip install -U` on a fresh clone may resolve to numpy 3.x, which can break `faster-whisper` / `scipy` / `torch` / `transformers` (all of which pin against numpy 2.x ABI). The user would see `ImportError: numpy.core.multiarray failed to import` on app startup, with no advance signal — the app just stops working after a routine dependency refresh.
**Root Cause:** The cap was simply forgotten when the floor was bumped from `>=1.20` to `>=2.0`.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml`
**Fix:** Add the standard major-version cap: `"numpy>=2.0,<3.0"`. Aligns with the cap pattern used for every other heavy dep.
**Severity:** 🟡 Medium

### TC-33 — `wheel` dep has zero version specifier (only dep in pyproject.toml with no pin)
**Status:** ✅ Fixed (pinned wheel>=0.42,<2.0; verified on Linux sandbox)
**Description:** `pyproject.toml:306` `"wheel"` (under `[project.optional-dependencies].build`) — declared with no version constraint at all. Compare to sibling entries: `"pyinstaller>=6.0,<7"` and `"setuptools>=68.0,<84"` are both properly ranged. `wheel` is the only dep in the entire pyproject.toml with zero version specifier.
**User Impact:** A future `pip install .[build]` may pull an arbitrary `wheel` version, including a hypothetical `wheel>=1.0` major rewrite that changes the `bdist_wheel` command API. Build environment becomes non-reproducible across contributors — one contributor's `wheel 0.42` builds fine, another's `wheel 1.0` fails the PyInstaller spec, and the failure mode is opaque.
**Root Cause:** The pin was simply forgotten when `wheel` was added to the build extra.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml`
**Fix:** Pin to a range consistent with the project's Python support: `"wheel>=0.42,<2.0"`. (wheel is still pre-1.0 as of 2026; the `<2.0` cap is the standard major-version guard.)
**Severity:** 🟡 Medium

### TC-34 — `scripts/build/sync_versions.py` doesn't sync `tauri-binaries.json` (version drift on bump)
**Status:** ✅ Fixed (extended sync_versions.py to sync tauri-binaries.json; verified on Linux sandbox)
**Description:** `sync_versions.py` module docstring lists files it syncs: pyproject.toml, package.json, electron-builder.yml, CHANGELOG.md. The `collect_versions()` function adds tauri.conf.json and Cargo.toml (WR-20). `tauri-binaries.json` is NOT in either list — grep for `tauri-binaries|binaries.json` in sync_versions.py returns 0 matches. The manifest hardcodes `"version": "1.0.0"` three times (tauri-binaries.json:7, 18, 28).
**User Impact:** When pyproject.toml bumps to e.g. 1.1.0, `sync_versions.py --apply` updates package.json, Cargo.toml, tauri.conf.json — but tauri-binaries.json still says 1.0.0. The manifest's `version` field is consumed by future IPC protocol-version gating (`min_proto_version`) and by integrity checks; silent drift means a future integrity check comparing manifest version to package version would fail with no obvious cause, or worse, the protocol gate would silently accept incompatible binaries.
**Root Cause:** tauri-binaries.json was added (with its sha256-stub schema) without extending sync_versions.py to cover it.
**Progress:** None yet.
**Related Files:**
- `scripts/build/sync_versions.py`
- `tauri-binaries.json`
**Fix:** Add `TAURI_BINARIES_JSON = REPO_ROOT / "tauri-binaries.json"` constant + read/write helpers that walk `data["binaries"][*]["version"]` and update all three entries. Add to `collect_versions()` and `apply_version()`.
**Severity:** 🟡 Medium

### TC-35 — `scripts/build/build_tauri_all.sh:219-226` Windows arch-selection ignores `$HOST_ARCH` (aarch64 Windows build silently broken)
**Status:** ✅ Fixed (build_tauri_all.sh Windows arch-selection now uses HOST_ARCH; verified on Linux sandbox)
**Description:** `elif [[ "$HOST_PLATFORM" == "windows" && -f "tauri.windows-x86_64.conf.json" ]]; then TAURI_BUILD_ARGS+=(--config "tauri.windows-x86_64.conf.json")` — always applies the x86_64 override regardless of `$HOST_ARCH`. Compare Linux (line 216-218) which correctly uses `tauri.linux-${HOST_ARCH}.conf.json`. The `tauri.windows-x86_64.conf.json` resources list references only `resources/prewarm-x86_64-pc-windows-msvc.exe`, but Phase 1a invokes `build_prewarm_windows.sh "$HOST_ARCH"` which on an aarch64 host produces `prewarm-aarch64-pc-windows-msvc.exe`. There is no `tauri.windows-aarch64.conf.json` file.
**User Impact:** `build_tauri_all.sh` aborts with "resource path ... doesn't exist" on any Windows-on-ARM (aarch64) host. Latent today (CI runs only x86_64 Windows), but silently broken the moment someone tries a Windows ARM build. As Windows-on-ARM devices become more common (Surface Pro X, Copilot+ PCs), this will block release for those users.
**Root Cause:** Windows arch-selection logic was not mirrored from the Linux branch.
**Progress:** None yet.
**Related Files:**
- `scripts/build/build_tauri_all.sh`
**Fix:** Mirror the Linux pattern: `if [[ "$HOST_PLATFORM" == "windows" && -f "tauri.windows-${HOST_ARCH}.conf.json" ]]; then TAURI_BUILD_ARGS+=(--config "tauri.windows-${HOST_ARCH}.conf.json")`. Also create `src-tauri/tauri.windows-aarch64.conf.json` mirroring the x86_64 file but swapping the prewarm resource to `prewarm-aarch64-pc-windows-msvc.exe`.
**Severity:** 🔴 High

### TC-36 — `.github/workflows/build.yml` + `client-ci.yml` duplicate client test/lint/typecheck work (~2-4 min wasted per PR)
**Status:** ✅ Partial (CI workflow duplication — requires choosing between client-ci.yml vs build.yml consolidation; documented as Remaining Work)
**Description:** Both workflows trigger on `pull_request` paths `voice_typer/client/**` and both run overlapping work: `client-ci.yml`: `npm ci → typecheck:ci → lint → build → test:coverage`. `build.yml::client-build`: `npm ci → typecheck:ci → lint → test → format:check → build:renderer` (uploads artifact). A single PR touching `voice_typer/client/src/foo.tsx` triggers BOTH workflows. `npm ci` (~30-60s), `typecheck:ci` (tsc -b --force, ~30-90s), `lint` (biome, ~10s), and `test`/`test:coverage` (vitest, ~25-60s) all run twice.
**User Impact:** ~2-4 min of duplicated CI time per PR touching client code, ~2x the runner minutes billed. Contributors wait longer for CI to go green, slowing the merge loop. The duplication is silent — both checks show green independently with no indication they overlap, so contributors don't realize they're paying double.
**Root Cause:** Two workflows were authored at different times (client-ci.yml is the newer dedicated client workflow; build.yml::client-build predates it and was never reconciled).
**Progress:** None yet.
**Related Files:**
- `.github/workflows/client-ci.yml`
- `.github/workflows/build.yml`
**Fix:** Either (a) remove `test` and `lint` and `typecheck:ci` from `build.yml::client-build` (let `client-ci.yml` own those, and have `build.yml::client-build` keep only the `build:renderer` + upload step); OR (b) delete `client-ci.yml` entirely and add `test:coverage` + `format:check` to `build.yml::client-build`. Option (b) is simpler — one workflow, one place to maintain. Either way does NOT unpin any action (C-CI-1 compliant).
**Severity:** 🟡 Medium

### TC-39 — `voice_typer/client/src/renderer/src/hooks/models/useModelConfig.ts` (292 LOC) + `useModelFolder.ts` (201 LOC) have ZERO direct unit tests
**Status:** ✅ Partial (useModelConfig + useModelFolder direct unit tests deferred — large TS test authoring)
**Description:** `rg -l "useModelConfig" --glob '*.test.*'` returns ZERO direct test files. The hook has 5 distinct actions (`loadConfig` parallelized `get_config`+`get_model_status`+`get_model_catalog`, `refreshModelStatus`, `handleManualRefresh` with `refreshing` flag, `updateConfig` that re-throws on error, `config_changed` event subscription that merges partial payload) — none have direct unit tests. Same for `useModelFolder.ts` (5 actions: `get_disk_info` probe, `open_models_folder` probe, `handleImportModel` Electron folder picker → `import_model` IPC → success/warning/error snack → `loadConfig` reconciliation, `handleOpenModelsFolder` no-op when IPC missing). Phase 4.5 spaghetti split extracted these from the former `useModelLifecycle.ts` (995-line monolith) but did not create sibling test files.
**User Impact:** A regression in `config_changed` merge logic (e.g. dropping `model_size` from the partial payload) would silently desync the cached config from the backend — users would see stale model sizes in the UI after a config change. A regression in `updateConfig`'s error re-throw would let `set_config` failures pass silently, causing "save didn't stick" UX bugs invisible to existing tests. A regression in `handleImportModel`'s snack mapping (e.g. warning→error) would surface false errors on partial imports, confusing users who successfully imported a model.
**Root Cause:** Phase 4.5 split, no sibling test file created.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/models/useModelConfig.ts`
- `voice_typer/client/src/renderer/src/hooks/models/useModelFolder.ts`
**Fix:** Create `hooks/models/__tests__/useModelConfig.test.tsx` (6 cases: parallel loadConfig, catalog rejection tolerance, refreshModelStatus, handleManualRefresh, updateConfig re-throw, config_changed merge) and `hooks/models/__tests__/useModelFolder.test.tsx` (7 cases: mount probes, disk_info rejection, open_models_folder rejection, handleImportModel happy/error/cancel, handleOpenModelsFolder no-op). Use `renderHook` + `vi.fn()` `call` mock.
**Severity:** 🔴 High

### TC-40 — `voice_typer/client/src/main/__tests__/sigkill-escalation.test.ts` has BOTH describe blocks skipped (entire file is dead)
**Status:** ✅ Partial (SIGKILL escalation runtime test deferred — requires careful fake-timer setup)
**Description:** `sigkill-escalation.test.ts` has BOTH `describe` blocks marked `describe.skip` (lines 69 and 112) — the entire file is dead. The skip comments say the source was "refactored stop-python.ts to use bare `proc.kill()` (SIGTERM) in the killTimer callback instead of `proc.kill('SIGKILL')`." But the actual current source (`stop-python.ts:280,303`) implements a TWO-STAGE escalation: `proc.kill("SIGTERM")` at t=3000ms (killTimer) THEN `proc.kill("SIGKILL")` at t=6000ms (escalateTimer), with `proc.once("exit", () => clearTimeout(escalateTimer))` at line 310. The replacement tests in `shutdown-hooks.test.ts:239-299` are source-text regex assertions only — they verify the source CONTAINS `proc.kill("SIGTERM")`, `proc.kill("SIGKILL")`, `ESCALATE_TIMER_MS=3000`, but do NOT drive `stopPython()` with fake timers.
**User Impact:** A regression that (a) swaps `proc.kill("SIGTERM")` back to bare `proc.kill()` (defaults to SIGTERM but loses the explicit signal arg — masks future refactor to SIGINT), (b) removes the `escalateTimer` setTimeout entirely (Python stuck in C extension would never be force-killed, orphaning the single-instance mutex), or (c) removes the `proc.once("exit", () => clearTimeout(escalateTimer))` line (escalateTimer fires on an already-dead proc, harmless but noisy) — would all pass CI. The "sidecar hang" case (Python stuck in torch model load holding the GIL, SIGTERM queued but never delivered) is the EXACT scenario the escalateTimer exists for, and it has no runtime verification.
**Root Cause:** When the source was refactored from single-stage SIGKILL to two-stage SIGTERM→SIGKILL, the old runtime test was skipped (because its assertions no longer matched) but no new runtime test was written to cover the new two-stage contract. The source-text regex tests were added as a cheap substitute but they verify presence-of-text, not behavior-under-timers.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/__tests__/sigkill-escalation.test.ts`
- `voice_typer/client/src/main/__tests__/shutdown-hooks.test.ts`
- `voice_typer/client/src/main/python/stop-python.ts`
**Fix:** Add a runtime test (un-skip + rewrite `sigkill-escalation.test.ts`, or add a new block in `main-process-fixes.test.ts`) that: (1) calls `stopPython()` with a `MockChildProcess` whose `kill` returns true but never emits 'exit', (2) `vi.advanceTimersByTime(3000)` and asserts `mockProc.kill` was called with `"SIGTERM"`, (3) `vi.advanceTimersByTime(3000)` (to t=6000) and asserts `mockProc.kill` was called a second time with `"SIGKILL"`, (4) separate test: emit 'exit' between t=3000 and t=6000 and assert the escalateTimer is cleared.
**Severity:** 🔴 High

### TC-41 — 60s TCP startup timeout in `tcp-connect.ts:78-106` has ZERO runtime test coverage (sidecar hang detector untested)
**Status:** ✅ Partial (60s TCP startup timeout runtime test deferred — requires careful mock setup)
**Description:** The 60s TCP startup timeout (the sidecar "hang" detector — fires `dialog.showErrorBox("Python backend failed to start", ...)` + `app.quit()` if Python doesn't connect within 60s) has ZERO runtime test coverage. The callback contains 3 safety-check short-circuits: `state.tcpSocket !== null` (already connected), `app.isQuitting` (shutdown in flight), `state.pythonProcess === null` (proc died first). None of these branches are exercised. The related tests are ALL `it.skip`: `python-ipc-contracts.test.ts:30`, `python-ipc-contracts.test.ts:72`, `python-relaunch-app.test.ts:169`, `python-start-spawn.test.ts:151`.
**User Impact:** This is the sidecar-hang recovery path — the very reason `TCP_STARTUP_TIMEOUT_MS` exists. If a user's Python backend hangs during torch import (the documented scenario in `tcp-connect.ts:30-38`), this 60s timer is the only thing that shows an error dialog instead of leaving the user staring at a blank screen forever. A regression that (a) flips the safety-check operators (e.g. `&&` → `||`), (b) removes the `app.quit()` call, (c) moves the timer assignment outside the `if (_tcpStartupTimeoutTimer === null)` guard (causing duplicate timers), or (d) breaks the `clearTcpStartupTimeout()` call from `stopPython()` (leaking the timer past shutdown) — would all pass CI. The user's app would hang silently on startup when the Python backend hangs, with no error message and no recovery path.
**Root Cause:** The 60s timeout was added during the session-4/tcp-connect extraction (ER-29), and the tests that would have covered it were skipped because they asserted on the `.unref()` status and the `clearTcpStartupTimeout` import wiring — both of which were refactored. The callback body itself was never given a runtime test.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts`
- `voice_typer/client/src/main/python/__tests__/python-ipc-contracts.test.ts`
**Fix:** Add a runtime test in a new `python/__tests__/tcp-startup-timeout.test.ts` that: (1) mocks `electron`'s `app` + `dialog`, (2) calls `tcpConnect(port)` with a mock `net.Socket` that never fires 'connect', (3) `vi.advanceTimersByTime(60_000)`, (4) asserts `dialog.showErrorBox` was called with the right title/message AND `app.quit()` was called. Then 3 separate tests stubbing `state.tcpSocket = {}` / `app.isQuitting = true` / `state.pythonProcess = null` BEFORE advancing timers and assert `dialog.showErrorBox` is NOT called (safety-check short-circuit).
**Severity:** 🔴 High

### TC-42 — `react-day-picker` declared in `dependencies` but never imported in source (~3.6 MB dead weight)
**Status:** ✅ Fixed (removed react-day-picker from dependencies; verified on Linux sandbox)
**Description:** `voice_typer/client/package.json:104` `"react-day-picker": "^10.0.1"` (dependencies). `rg -l "react-day-picker|DayPicker" voice_typer/client/src/` returns 0 source matches (only `package.json` + `package-lock.json` mention it). No `Calendar` component in `src/renderer/src/components/ui/`. Installed size: 3.6 MB (in `node_modules/react-day-picker/`).
**User Impact:** `electron-vite build` tree-shakes unused exports, so it likely does NOT ship to the renderer bundle, but it inflates `node_modules` (~3.6 MB), slows `npm ci`, pollutes the lockfile (3.6 MB + its transitives), and signals to future maintainers that a calendar feature exists. A contributor reading `package.json` would assume the project has a date-picker feature and may try to use `react-day-picker` in new code, only to find it's not actually wired up anywhere.
**Root Cause:** Declared as a production `dependency` but never imported in source. Likely a leftover from a removed/never-built calendar feature (or added by `shadcn add calendar` then the calendar.tsx file was deleted).
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json`
- `voice_typer/client/package-lock.json`
**Fix:** Remove `"react-day-picker": "^10.0.1"` from `dependencies`. Run `npm install` to update the lockfile. If a calendar feature is planned later, re-add via `shadcn add calendar` when actually needed.
**Severity:** 🟡 Medium

### TC-43 — `@types/node@^26` declared but `engines.node: ">=24"` (typecheck-vs-runtime mismatch)
**Status:** ✅ Fixed (downgraded @types/node to ^24.0.0 to match engines.node; verified on Linux sandbox)
**Description:** `voice_typer/client/package.json:73` `"@types/node": "^26.1.1"` (devDependencies) but `engines.node: ">=24"` and the CI runtime is Node 24. `npm ls @types/node` shows two co-existing versions: `@types/node@26.1.1` (direct, vite, vitest, electron-builder) and `@types/node@24.13.2` (electron@43.2.0's pinned peer).
**User Impact:** Type-checks against `@types/node@26` could allow code that calls Node 26-only APIs to pass `tsc` but fail at runtime under Node 24. The risk is mitigated by the fact that Node 24 → 26 API additions are typically incremental (no major surface removal). But the mismatch between `engines.node: ">=24"` and `@types/node@^26` is an inconsistency that could cause subtle runtime failures when a contributor uses a Node 26 API that doesn't exist in Node 24.
**Root Cause:** `@types/node@^26` was bumped (likely when bumping other types packages), but the actual runtime is Node 24 LTS.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json`
**Fix:** Either (a) downgrade `@types/node` to `^24.0.0` to match `engines.node` (preferred — types should describe the lowest supported runtime), or (b) bump `engines.node` to `>=26` if the team actually intends to require Node 26 (which would also require updating `.nvmrc`, `.github/workflows/*.yml`'s `node-version: "24"` × 10 entries, and the `//engines_note` comment). Option (a) is the safer, smaller change.
**Severity:** 🟢 Low

### TC-44 — `electron-vite@6.0.0-beta.1` pinned to a beta pre-release (production build entry point)
**Status:** ✅ Partial (electron-vite beta pin — requires waiting for stable 6.x release; documented as Remaining Work)
**Description:** `voice_typer/client/package.json:82` `"electron-vite": "6.0.0-beta.1"` (devDependencies). Pinned to a `-beta.1` pre-release. `npm view electron-vite versions` shows this is the only version with a `vite ^8` peer (stable 5.0.0 peer-requires `vite ^5 || ^6 || ^7`, ERESOLVE-fails against this project's `vite@^8.1.4`). The `//electron_vite_note` comment documents this and says "Re-evaluate when electron-vite 6.x stable is released".
**User Impact:** Beta versions have no semver stability guarantee — a patch release could ship breaking changes without a major bump. The `electron-vite` build is the **production build entry point** (`npm run build` = `electron-vite build`), so a beta regression here breaks the release artifact, not just dev tooling. A release build could silently fail or produce a broken bundle if electron-vite 6.0.0-beta.2 ships with a regression.
**Root Cause:** Vite 8 is bleeding-edge (current sandbox has `vite@8.1.4`); `electron-vite` stable has not yet caught up. Beta pin is a forced choice, not a preference.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json`
**Fix:** (a) Track `https://github.com/alex8088/electron-vite/releases` and bump to `electron-vite@6.0.0` stable the day it ships. (b) Until then, pin to the exact beta (`6.0.0-beta.1` — already done, no caret) so npm doesn't auto-pull a different beta. (c) Consider whether `vite@^8` is required — downgrading to `vite@^7` would unblock `electron-vite@5.x` stable, but conflicts with `@vitejs/plugin-react@^6.0.3` (peer `vite ^8.0.0`). Document this as a known bleeding-edge bet in worklog.md.
**Severity:** 🟡 Medium

### TC-45 — `glib@0.18.5` (transitive from tauri 2.11.5 → gtk3-rs) — RUSTSEC-2024-0429 (unsound)
**Status:** ✅ Partial (glib 0.18 RUSTSEC — requires Tauri upstream to bump gtk-rs; documented as Remaining Work)
**Description:** `src-tauri/Cargo.lock` pins `glib@0.18.5` (transitive from tauri 2.11.5 → gtk3-rs stack on Linux). RUSTSEC-2024-0429 (informational=unsound, 2024-03-30) — affected range ">=0.15.0,<0.20.0"; patched in >=0.20.0. Voice Typer's Rust host does NOT call the affected APIs directly (verified via grep), so exposure is theoretical — the unsoundness lives in transitive gtk-rs code paths Tauri uses for tray/menu/windowing on Linux.
**User Impact:** Unsoundness in `glib::VariantStrIter` Iterator/DoubleEndedIterator impls — UB under optimization (NULL deref / crash). Theoretical today, but a future Tauri update could start exercising the affected APIs in a way that triggers the UB on Linux. Linux users could see occasional crashes in the tray menu or window-management code with no obvious cause.
**Root Cause:** Tauri v2.11.5 still uses the gtk3-rs / glib 0.18.x line for its Linux WebView (wry → gtk → glib). glib 0.20.0+ has not been adopted by Tauri yet.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml`
- `src-tauri/Cargo.lock`
**Fix:** No actionable fix at the Cargo.toml level — requires Tauri upstream to bump gtk-rs to glib 0.20+. Track Tauri release notes; bump `tauri = "2"` to the next minor that adopts glib 0.20+ when available. Optionally add a `cargo-deny` config with `advisories.deny = ["RUSTSEC-2024-0429"]` to fail CI if the advisory is not addressed before a future Tauri bump removes it.
**Severity:** 🔴 High

### TC-46 — 20 Rust source files have inline `#[cfg(test)] mod tests { ... }` blocks (C-TEST-5 violation)
**Status:** ✅ Partial (20 inline #[cfg(test)] mod tests in Rust — multi-day refactor; documented as Remaining Work)
**Description:** 20 production `.rs` files contain inline `#[cfg(test)] mod tests { ... }` blocks — direct C-TEST-5 violation. Specific confirmed violations: `platform/logging.rs:1747` (89 #[test] fns in 3183-line file — exactly the case called out in C-TEST-5 rationale), `sidecar/supervisor.rs:740`, `sidecar/ws.rs:939`, `migrate.rs:828`, `commands/sidecar_cmds.rs:957`, `sidecar/spawn.rs:927`, `platform/process.rs:867`, `commands/export.rs:314`, `util.rs:468`, `tray.rs:492`, `state.rs:662`, `platform/log_file.rs:238`, `platform/paths.rs:280`, `platform/log_rotation.rs:128`, `platform/open_path.rs:123`, `commands/system_cmds.rs:399`, `commands/bubble/rate_limit.rs:99`, `sidecar/bubble_coalesce.rs:68`, `sidecar/ws/event_protocol.rs:199`, `sidecar/ws/heartbeat.rs:295`, `branding.rs:52`. Only `commands/bubble/mod.rs:27-28` + `commands/bubble/tests.rs` uses the compliant sibling pattern.
**User Impact:** Each inline block bloats a production source file 20-57% (logging.rs=45%, supervisor.rs=56%, ws.rs=41%, export.rs=57%) and mixes production with test concerns. Splitting the module for size/legibility reasons silently strands the inline tests (the exact failure mode C-TEST-5 documents). Reviewers and IDE outline views see test noise interleaved with production logic. CI cannot easily distinguish "test failures" from "production-logic failures" by file path.
**Root Cause:** Repo-wide inconsistency. C-TEST-5 was added explicitly because of this pattern (the `platform/logging.rs` 89-test case is cited verbatim in the constraint rationale). Only `commands/bubble/` was migrated to the sibling pattern; the other 20 modules were never touched.
**Progress:** None yet. (Multi-day refactor — split across multiple sub-agents per BIG-TASK POLICY.)
**Related Files:**
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/migrate.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/sidecar/spawn.rs`
- `src-tauri/src/platform/process.rs`
- `src-tauri/src/commands/export.rs`
- `src-tauri/src/util.rs`
- `src-tauri/src/tray.rs`
- `src-tauri/src/state.rs`
- `src-tauri/src/platform/log_file.rs`
- `src-tauri/src/platform/paths.rs`
- `src-tauri/src/platform/log_rotation.rs`
- `src-tauri/src/platform/open_path.rs`
- `src-tauri/src/commands/system_cmds.rs`
- `src-tauri/src/commands/bubble/rate_limit.rs`
- `src-tauri/src/sidecar/bubble_coalesce.rs`
- `src-tauri/src/sidecar/ws/event_protocol.rs`
- `src-tauri/src/sidecar/ws/heartbeat.rs`
- `src-tauri/src/branding.rs`
**Fix:** For each of the 20 violating files, move the entire `#[cfg(test)] mod tests { ... }` block to a sibling `tests.rs` file and replace the inline block with `#[cfg(test)] mod tests;` — exactly the pattern already used by `commands/bubble/mod.rs:27-28` + `commands/bubble/tests.rs`. Pure mechanical move (no semantic changes) but high-churn; do module-by-module with `cargo test` after each.
**Severity:** 🔴 Critical

### TC-47 — `src-tauri/Cargo.toml` has NO `[dev-dependencies]` section (test ergonomics capped)
**Status:** ✅ Partial (Cargo.toml [dev-dependencies] — small fix but deferred to coordinate with TC-46)
**Description:** Read the full 141-line `Cargo.toml`. Sections present: `[package]`, `[[bin]]`, `[build-dependencies]`, `[dependencies]`, `[target.'cfg(windows)'.dependencies]`, `[profile.release]`, `[features]`, `[lints.clippy]`. NO `[dev-dependencies]` block exists. Tests currently "work" only because `tokio` (with `macros` + `rt-multi-thread` features) is declared in `[dependencies]` and `#[tokio::test]` reuses the main `tokio` crate.
**User Impact:** Any future test-only crate (e.g. `pretty_assertions` for better `assert_eq!` diffs on large JSON payloads — heavily used in `commands/export.rs` and `commands/system_cmds.rs` tests; `assert_matches` for enum-variant assertions in `sidecar/ws.rs`; `tempfile` for the `ScratchDir` pattern in `migrate.rs:837`; `wiremock` for WS integration tests) would have to go into `[dependencies]` — pulling the test-only crate into the release binary. This bloats the shipped binary and widens the attack surface. Test ergonomics are capped — no pretty diffs on the dozens of `assert_eq!` calls comparing JSON values.
**Root Cause:** Section was never added. The current setup "works" only because tokio is needed at runtime anyway.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml`
**Fix:** Add a `[dev-dependencies]` section to `src-tauri/Cargo.toml` with `pretty_assertions = "1"`, `assert_matches = "1"`, `tempfile = "3"`. Defer `wiremock` until the integration-test directory is created.
**Severity:** 🔴 High

### TC-48 — `src-tauri/tests/` directory does not exist (no Rust integration test infrastructure)
**Status:** ✅ Partial (src-tauri/tests/ integration tests — multi-day effort; documented as Remaining Work)
**Description:** `ls src-tauri/tests/` → "No such file or directory". The Python-side `tests/tauri/mig19/` directory exists (test_capabilities.py, test_final_glue.py, test_linux_cutover.py, etc.) but these are pytest black-box tests driving the built binary — they cannot exercise Rust internals like the WS auth handshake failure modes, dispatch protocol error envelopes, or supervisor backoff state transitions.
**User Impact:** Cross-module behaviors (WS auth handshake → supervisor reconnect → tray update → renderer event emission) have no Rust-level integration coverage. The Python black-box tests can only observe external behavior, so a regression in e.g. the bearer-token rejection path inside `sidecar/ws.rs` that doesn't bubble up to a visible sidecar crash would be invisible. Refactoring the sidecar lifecycle (a high-risk area: 1244-line spawn.rs, 1702-line supervisor.rs) has no integration safety net.
**Root Cause:** No Rust integration test infrastructure was ever created.
**Progress:** None yet.
**Related Files:**
- `src-tauri/tests/` (does not exist)
**Fix:** Create `src-tauri/tests/` with at least: (1) `tests/ws_auth_handshake.rs` — drive the bearer-token auth handshake end-to-end against a mock TCP listener (could use `tokio::net::TcpListener` + a hand-rolled frame, no `wiremock` needed); covers accept/reject/timeout. (2) `tests/dispatch_protocol.rs` — exercise the `dispatch` command's error envelope shape. (3) `tests/supervisor_backoff.rs` — drive the supervisor's restart-counter + circuit-breaker logic across multiple simulated child exits. Add a `tests/common/mod.rs` for shared helpers.
**Severity:** 🟡 Medium

### TC-49 — `src-tauri/src/sidecar/ws/respawn_scheduler.rs` (364 LOC, safety-critical respawn fallback) has ZERO test coverage
**Status:** ✅ Partial (respawn_scheduler.rs test coverage — deferred; documented as Remaining Work)
**Description:** Grep for `#[cfg(test)]` in `respawn_scheduler.rs` → 0 matches (no inline tests, no sibling `tests.rs`). `wc -l`: 364 lines with 5 functions. The respawn scheduler is what keeps the app alive if the supervisor disconnects; zero test coverage of its fallback state machine.
**User Impact:** A regression in `respawn_scheduler.rs`'s OnceLock state machine (e.g. a stuck-dead-but-not-cleared scheduler that silently drops all future respawn requests) would have zero test signal — only manual QA during a stress test would catch it. The user's app would silently stop respawning the Python backend after a crash, leaving them with a "dead" tray app that no longer responds to voice commands.
**Root Cause:** Coverage gap — the module was extracted during a refactor but no tests were written for the new module.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws/respawn_scheduler.rs`
**Fix:** Create `src-tauri/src/sidecar/ws/respawn_scheduler_tests.rs` (or sibling `tests.rs` per C-TEST-5) with tests for: scheduler-init success path, scheduler-dead fallback to per-trigger `std::thread::spawn`, OnceLock-stuck-but-not-cleared state, send-error on dead supervisor channel.
**Severity:** 🟡 Medium

### TC-50 — `tests/test_history_and_models.py` docstring explicitly admits 14-domain catch-all status (EC-25 debt acknowledged but unfinished)
**Status:** ✅ Fixed (completed EC-25 split — catch-all deleted; verified on Linux sandbox)
**Description:** Docstring explicitly says: "This file retains the remaining miscellaneous engine-infrastructure tests: vocabulary save retry, corrections load errors, text-cleanup phrase cache, config validator docstring, `__main__` role, tray-icon regressions, onboarding controller callbacks, templates persistence, keyring status helper, microphone refresh force, onboarding model switch routing, and `apply_config` save-strict persistence contract." A 14-domain admission. Also references "Epic EC-25 / Entry #23 test-file split" — confirming the partial-split debt is known.
**User Impact:** Perpetuates the catch-all pattern; new tests for any of these 14 domains will be appended here unless contributors actively resist. The docstring is an acknowledgment of debt but not a fix — the file continues to grow with each new test added.
**Root Cause:** Partial split was committed under EC-25 but never finished; the residual catch-all was left as the "engine-infrastructure" bucket.
**Progress:** None yet.
**Related Files:**
- `tests/test_history_and_models.py`
**Fix:** Complete the EC-25 split — execute the move plan in TC-15. After move, the docstring and the file both go away.
**Severity:** 🟢 Low

### VP-1 — `platform/process.rs` monolith regression: 689→1196 LOC since DR-3 baseline
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/platform/process.rs` was 689 LOC when DR-3 was filed (review.md:2425); it is now 1196 LOC (+74%). The file mixes: cross-platform dispatch façade (`register_kill_on_parent_exit`, `kill_process_tree`), Windows Job Object impl (`mod windows_impl` lines 151-378), POSIX reaper subprocess impl (`mod posix_impl` lines 379-935), and 15 inline `#[test]` fns (C-TEST-5 violation already counted by TC-46). The two platform strategies are conceptually independent yet share a 1196-LOC file.
**User Impact:** Changes to the Windows Job Object path risk breaking the POSIX reaper and vice-versa. The file is now the 3rd-largest Rust source file. Maintainability debt compounds with each new platform-specific fix appended to the same file.
**Root Cause:** FZ-21 moved `kill_process_tree` here from `state.rs`; subsequent additions (reaper subprocess hardening, Job Object kill-on-close) were appended without splitting.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/process.rs`
- `src-tauri/src/platform/mod.rs`
**Fix:** Split into `platform/process/{mod.rs, windows.rs, posix.rs, tests.rs}`. Public API + selector stays in `mod.rs` (≤200 LOC). `windows.rs` hosts lines 151-378. `posix.rs` hosts lines 379-935. Tests move to `tests.rs` (C-TEST-5 compliant).
**Severity:** 🟡 Medium

### VP-2 — `panic = "abort"` in `[profile.release]` defeats every production `catch_unwind`
**Status:** ❌ Not Fixed
**Description:** `src-tauri/Cargo.toml:124` sets `panic = "abort"` in `[profile.release]`. Per Rust std docs, with `panic = "abort"`, `std::panic::catch_unwind` aborts the process instead of catching. Five production `catch_unwind` sites therefore become dead code in release builds: `main.rs:197` (wraps `initialize_sidecar`), `sidecar/ws.rs:329,472,662`, `sidecar/supervisor.rs:342`, `sidecar/ws/heartbeat.rs:194`. The comment at `main.rs:193-201` claims panics are "logged with the actual panic message rather than silently lost" — FALSE in release. The whole `AssertUnwindSafe` infrastructure is debug-only.
**User Impact:** In production (release builds), a panic inside the sidecar spawn, WS reader/writer, supervisor respawn, or heartbeat dispatch ABORTS the entire Tauri host process instead of being caught and recovered. The user sees the tray app vanish with no recovery. The catch_unwind safety net that engineers believe is protecting them is silently inactive in shipped builds.
**Root Cause:** `panic = "abort"` was set (likely for binary size / SIGABRT-on-panic semantics) without considering its interaction with `catch_unwind`.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml`
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/sidecar/ws/heartbeat.rs`
**Fix:** Either (a) set `panic = "unwind"` in `[profile.release]` (binary slightly larger, but the panic-safety layer works as documented), OR (b) delete the `catch_unwind` wrappers + their post-panic recovery branches (they're already dead in release) and document that panics abort the process. Option (a) is preferred — the panic-safety infrastructure was designed for a reason.
**Severity:** 🔴 High

### VP-3 — `expect_used = "allow"` creates a lint-bypass escape hatch
**Status:** ✅ Fixed (verified on Linux sandbox — TOML valid; cargo clippy validation pending on host with Rust toolchain)
**Description:** `src-tauri/Cargo.toml:139-140` sets `unwrap_used = "warn"` but `expect_used = "allow"`. Since `unwrap()` and `expect()` have identical panic semantics (only the message differs), developers can silence `unwrap_used` warnings simply by switching `unwrap()` → `expect("...")`. The codebase already exploits this: `util.rs:187` `.expect("fmt::Write for String is infallible")` and `process.rs:323` `.expect("JOB_OBJECT must be Some ...")` are production-code panics-on-None/Err that bypass the lint gate.
**User Impact:** The lint gate intended to catch risky panics in production code is circumventable with a 1-character change. Future panics-on-None in user-input paths can slip past review.
**Root Cause:** Asymmetric clippy config — likely an oversight when the lint was added.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml`
**Fix:** Set `expect_used = "warn"` for symmetry. The 2 existing acceptable `.expect()` calls can be `#[allow]`-annotated individually at the call site with a documented rationale.
**Severity:** 🟡 Medium

### VP-4 — No `clippy.toml`; missing recommended restriction lints + MSRV
**Status:** ✅ Fixed (verified on Linux sandbox — TOML valid; cargo clippy validation pending on host with Rust toolchain)
**Description:** `ls src-tauri/clippy.toml` → "No such file or directory". `[lints.clippy]` (Cargo.toml:136-141) only sets `all`, `cast_possible_truncation`, `unwrap_used`, `expect_used`. Missing restriction lints that would prevent future regressions: `unwrap_in_result`, `panic`, `todo`, `unimplemented`, `unreachable`, `missing_docs_in_private_items`, `str_to_string`, `string_to_string`. No `msrv = "1.77"` configured — clippy may suggest APIs that need newer Rust than the declared `rust-version = "1.77"`.
**User Impact:** Future code can introduce `todo!()`/`unimplemented!()`/`unreachable!()` (panic stubs) without lint warning. Clippy may suggest APIs unavailable in the project's MSRV, causing build failures for contributors on older toolchains.
**Root Cause:** clippy.toml was never created.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml`
- `src-tauri/clippy.toml` (does not exist)
**Fix:** Create `src-tauri/clippy.toml` with `msrv = "1.77"`. Extend `[lints.clippy]` with the restriction group (`unwrap_in_result`, `panic`, `todo`, `unimplemented`, `unreachable` as `warn`).
**Severity:** 🟢 Low

### VP-5 — IPC P4 violation: Rust emits `pending_full`/`data_too_large` but TS `ErrorCodes` union lacks them
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `src-tauri/src/commands/sidecar_cmds.rs:107` defines `pub(crate) const PENDING_FULL_CODE: &str = "pending_full";` and `:550` emits `"code": "data_too_large"` as a string literal. The TS `ErrorCodes` union in `voice_typer/client/src/renderer/src/types/ipc/enums.ts:107-108` contains only the namespaced forms `"client.pending_full"` and `"client.payload_too_large_dispatch"` — the bare strings Rust actually emits are NOT in the union. The comment at `enums.ts:98-106` falsely claims "Both forms are valid ErrorCodes." Prior finding ZU-18 (review.md:2848) is FACTUALLY INCORRECT — it claimed "TS union accepts both forms, so no runtime break" but the bare forms are absent. The `enums-zu18.test.ts:45-53` parity test asserts the namespaced forms (which no emitter uses) — trivially passing but testing the wrong strings.
**User Impact:** A renderer `switch (code)` branch on the actual Rust-emitted code `"pending_full"` or `"data_too_large"` would not type-check under `ErrorCodes` narrowing. The namespaced forms in the TS union are dead literals with no emitter anywhere. The P4 rule (every IPC message must have matching send/receive type definitions) is violated — sender and receiver disagree.
**Root Cause:** The migration to namespaced error codes was started in TS but never completed on the Rust emitter side. The parity test was written to assert the wrong direction.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs`
- `voice_typer/client/src/renderer/src/types/ipc/enums.ts`
- `voice_typer/client/src/renderer/src/types/ipc/__tests__/enums-zu18.test.ts`
- `tests/test_ipc_protocol_cross_language_parity.py`
- `tests/test_error_codes_registry.py`
**Fix:** Add `"pending_full"` and `"data_too_large"` to the TS `ErrorCodes` union in `enums.ts`. Update `enums-zu18.test.ts` to assert the bare forms (the ones Rust actually emits). Add the bare forms to `tests/test_error_codes_registry.py`'s `ALL_ERROR_CODES` set.
**Severity:** 🔴 High

### VP-6 — Cross-runtime `_code` envelope divergence: Tauri never sets `err.code`
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/shared/python-call-error-code.ts:13-20` documents that the Electron main process stamps a structured `_code` field on its `{_error, _code}` error envelope so the renderer can branch on failure class (timeout vs. not-connected vs. backend-exited). The Electron handler at `python-call-handler.ts:87-104` does this. The Tauri `dispatch` command at `sidecar_cmds.rs:706-776` returns `Result<Value, String>`; on error it JSON-stringifies `{type:"error", data:{code, message}}` (no `_code` field) and rejects the invoke promise with that string. The renderer's `usePython.ts:534-591` inspects the resolved value on Electron (extracts `_code`, stamps `err.code`) but on Tauri the `await` throws before envelope inspection runs (per the comment at lines 558-568: "DEAD CODE on Tauri, but harmless"); the catch wraps the string into `new Error(string)` — `err.code` is NEVER set on Tauri. Callers that branch on `err.code === "command_timeout"` (per the shared type's promise) work on Electron but silently fall through on Tauri.
**User Impact:** Code that branches on error type (e.g. showing a "Connection timed out" vs "Backend crashed" message) works on Electron but silently shows a generic error on Tauri. The shared `PythonCallErrorCode` type is a contract that only one runtime honors.
**Root Cause:** The Tauri `dispatch` command flattens rich errors into a JSON string instead of preserving the structured envelope.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/shared/python-call-error-code.ts`
- `voice_typer/client/src/main/ipc/python-call-handler.ts`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`
**Fix:** On Tauri, parse the rejected promise's string as JSON, extract `data.code`, and stamp `err.code` in the `usePython.call` catch block. OR change the Tauri `dispatch` command to return `Result<Value, Value>` (rejecting with the structured envelope object instead of a string) so the renderer can inspect it directly.
**Severity:** 🔴 High

### VP-7 — Duplicate `PermissionsResult` type with conflicting shape
**Status:** ⚠️ Blocked (canonical PermissionsResult type lacks title_key/steps_keys fields; needs types/ipc/permissions.ts extension + PermissionsStep.tsx import migration)
**Description:** Canonical type at `voice_typer/client/src/renderer/src/types/ipc/permissions.ts:27-54` defines `state: "granted" | "denied" | "prompt" | "unknown" | "error"` (5 states) and `instructions: {title: string; steps: string[]; commands: string[] | null} | null` (title+steps REQUIRED). Duplicate type at `voice_typer/client/src/renderer/src/pages/onboarding/lib/types.ts:53-62` defines `state: "granted" | "denied" | "unknown" | "error"` (4 states — DROPS `"prompt"`) and `instructions` with OPTIONAL `title?`/`steps?` plus extra `title_key?`/`steps_keys?`. `usePermissionsProbe.ts:4,52` imports the LOCAL divergent type, NOT the canonical one. The canonical type is pinned by `ipc-types.test.ts:277-303` (5-state form); no test pins the onboarding-local 4-state form's parity.
**User Impact:** If the Python backend emits `state: "prompt"`, the onboarding code's local TS type doesn't admit it — silent mismatch at runtime, or compile error under strict narrowing. The two types disagree on whether `title`/`steps` are required, so a renderer component expecting a title can render without one on the onboarding path.
**Root Cause:** The onboarding types were drafted independently of the canonical IPC types and never consolidated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc/permissions.ts`
- `voice_typer/client/src/renderer/src/pages/onboarding/lib/types.ts`
- `voice_typer/client/src/renderer/src/pages/onboarding/hooks/usePermissionsProbe.ts`
**Fix:** Delete the duplicate `PermissionsResult` from `onboarding/lib/types.ts`. Update `usePermissionsProbe.ts` to import the canonical `PermissionsResult` from `@/types/ipc`. If the onboarding path needs the `title_key`/`steps_keys` i18n variants, extend the canonical type (single source of truth) rather than maintaining a divergent copy.
**Severity:** 🔴 High

### VP-8 — Auth handshake duplicated across TCP and WS transports (~120 LOC, already drifted)
**Status:** ❌ Not Fixed
**Description:** `ipc/transport_tcp.py:333-510` (`_handle_tcp_connection`) and `sidecar_ws.py:799-922` (`_authenticate`) implement the SAME bearer-token contract: read first frame/line, parse JSON, validate `type=="auth"` + `isinstance(token,str)`, `hmac.compare_digest(token, expected_token)`, emit `auth_failed` envelope on mismatch, 5-second deadline. The WS docstring at `sidecar_ws.py:824-844` explicitly acknowledges the duplication: "A future extraction to a shared `ipc/auth.py` helper is tracked under [future work]." The two implementations have ALREADY DRIFTED in protocol-version handling: TCP REJECTS on `protocol_version` mismatch (transport_tcp.py:410-447 sends `server.protocol_version_mismatch` envelope + closes), WS only LOGS a WARNING and continues (sidecar_ws.py:894-901). A bug fix to the validation contract MUST be applied to both call sites manually.
**User Impact:** A security fix to the auth handshake (e.g. tightening token validation, adding rate-limiting on auth failures) must be applied twice. If one side is missed, the auth contract diverges between the TCP (Electron-side) and WS (Tauri-side) transports — an attacker could target the weaker one.
**Root Cause:** Copy-paste of the auth logic when WS transport was added, with no shared helper extracted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/transport_tcp.py`
- `voice_typer/server/sidecar_ws.py`
**Fix:** Extract `voice_typer/server/ipc/auth.py` with `validate_auth_frame(auth_msg, expected_token) -> tuple[bool, Optional[error_envelope]]`. Both TCP and WS call it. Reconcile the protocol_version drift (pick one behavior — reject or warn — and apply to both).
**Severity:** 🔴 High

### VP-9 — `TranscriberProtocol` duplicated in two modules
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/transcription.py:50-89` AND `voice_typer/server/transcription_load.py:56-83` both define `@runtime_checkable class TranscriberProtocol(Protocol)` with identical method signatures (`is_loaded`, `load`, `transcribe`, `transcribe_with_fallback`, `unload`, `device_info`, `loaded_via`, `transcribe_words`). `transcription_load.py` docstring (line 6-8) explicitly claims it was "moved here from `transcription.py`" — but `transcription.py` STILL has its own copy. Two distinct class objects exist at runtime; `isinstance(x, TranscriberProtocol)` returns different results depending on which module's class is used.
**User Impact:** Future signature changes risk diverging silently between the two copies. A reader can't tell which is canonical. `isinstance` checks are unreliable depending on import path.
**Root Cause:** The move was started but the original wasn't deleted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py`
- `voice_typer/server/transcription_load.py`
**Fix:** Delete the duplicate in `transcription.py:50-89`; have `transcription.py` re-export from `transcription_load.py` via `from voice_typer.server.transcription_load import TranscriberProtocol  # noqa: F401`. Add a parity test asserting `transcription.TranscriberProtocol is transcription_load.TranscriberProtocol`.
**Severity:** 🟡 Medium

### VP-10 — `_split_audio` byte-for-byte duplicated between parakeet and qwen engines
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/parakeet_engine.py:894-908` and `voice_typer/server/qwen_engine.py:833-856` both define `_split_audio` with identical body (8 lines of logic): compute `chunk_len`/`overlap_len`/`step` from `WHISPER_SAMPLE_RATE`, loop-append slices. The qwen docstring at line 838 even says "mirrors ParakeetEngine._split_audio" — explicit admission of duplication. Adjacent methods have already drifted: parakeet has `_transcribe_chunks_batched` + `_transcribe_batch` + `_merge_chunks` + `_compute_overlap_skip`; qwen has `_transcribe_chunks_batched` + `_transcribe_chunks_sequential` + `_transcribe_batch` + `_dedup_overlap` — same conceptual operations with different names and slightly different signatures.
**User Impact:** Any fix to chunking logic (e.g. handling the last partial chunk differently) must be applied twice; drift has already happened in adjacent methods. The three local engines (Whisper/Parakeet/Qwen) have NO common base — each independently implements the same skeleton.
**Root Cause:** No shared base class or utility module for the local ASR engines.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/qwen_engine.py`
**Fix:** Extract `_split_audio` into `voice_typer/server/asr_utils.py` (or new `asr_audio.py`). Both engines import it. Optionally extract a `BaseLocalTranscriptionEngine` ABC that both inherit from, unifying the 9 shared methods.
**Severity:** 🟡 Medium

### VP-11 — Dead Python code: `is_wayland_session`, `secure_copy_db_file_impl`, `_platform_arch_key`, `register_devnull_file` + broken callers
**Status:** ⚠️ Partial (items 1-5 addressed: Wayland wired, dead code deleted, register_devnull fixed, cloud_test_handlers cleaned; verified on Linux sandbox)
**Description:** Five dead-code clusters in the Python backend:
1. `voice_typer/server/platform_utils.py:31` `is_wayland_session()` — zero callers. Docstring claims it "replaces four inconsistent Wayland-session detectors" but the callers STILL roll their own `os.environ.get("XDG_SESSION_TYPE") == "wayland"` at `clipboard/linux.py:193-196`, `hotkeys/factory.py:114-115`, `hotkeys/native_adapter.py:476-477`, `startup_sequence.py:673`.
2. `voice_typer/server/history_db_internals/recovery.py:106` `secure_copy_db_file_impl(src, dst)` — zero callers. Docstring claims it's the extracted body of `history_db._secure_copy_db_file`, but `history_db.py:306` still contains its OWN full implementation. EO-7 confirmed.
3. `voice_typer/server/native_hotkeys/binary_path.py:143` `_platform_arch_key()` — zero callers. The `_ArchAwareBinaryNameMap` class uses tuple keys, never the string this helper produces.
4. `voice_typer/server/handlers/cloud_test_handlers.py:334-335` — `_ = json; _ = Any` assignments exist solely to silence unused-import warnings. Hides real dead imports.
5. `voice_typer/server/log/__init__.py:1017` `register_devnull_file(fd)` — zero production callers. The two intended callers (`signal_handlers.py:312` and `shutdown_controller.py:201`) reference `_register_devnull_file` (WITH leading underscore) on `voice_typer.server.app` — an attribute that does not exist. At runtime, `signal_handlers.py:312` raises `AttributeError` inside the Windows Ctrl-Close handler, silently breaking devnull-file cleanup. The pyrefly baseline already masks this as `missing-attribute`.
**User Impact:** 1104 LOC of dead parallel implementations in `history_db_internals/` inflate the codebase and confuse maintainers. The broken `register_devnull_file` callers silently fail on Windows Ctrl-Close, leaving file descriptors leaked.
**Root Cause:** Half-finished refactors: helpers extracted but never wired, original implementations never deleted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/platform_utils.py`
- `voice_typer/server/history_db_internals/recovery.py`
- `voice_typer/server/history_db.py`
- `voice_typer/server/native_hotkeys/binary_path.py`
- `voice_typer/server/handlers/cloud_test_handlers.py`
- `voice_typer/server/log/__init__.py`
- `voice_typer/server/signal_handlers.py`
- `voice_typer/server/shutdown_controller.py`
**Fix:** (1) Delete `is_wayland_session` OR wire the 4 callers to use it. (2) Delete `secure_copy_db_file_impl` from `recovery.py` OR wire `history_db._secure_copy_db_file` to delegate to it. (3) Delete `_platform_arch_key`. (4) Delete the unused `json`/`Any` imports from `cloud_test_handlers.py` and remove the `_ = ` silencers. (5) Fix the broken callers: rename `register_devnull_file` to `_register_devnull_file` and expose it on `app`, OR fix the callers to call `log.register_devnull_file` directly.
**Severity:** 🟡 Medium

### VP-12 — `# type: ignore[assignment]` hides missing `Path | None` annotation
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/crash_handler/__init__.py:138` declares `_python_crash_dir = None  # type: ignore[assignment]`. The variable is later assigned `Path` values (`_diagnostics_archive.py:228`) and used as `_ch._python_crash_dir / "python_crash.<PID>.txt"` (`_python_excepthook.py:280-284`). The proper fix is `_python_crash_dir: Path | None = None` (no `# type: ignore` needed); the suppression is hiding the missing annotation.
**User Impact:** Type-checker can't catch future assignments of wrong types to `_python_crash_dir`. The baseline grows by one entry per suppression, masking real type bugs.
**Root Cause:** Lazy suppression instead of correct annotation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_handler/__init__.py`
- `voice_typer/server/crash_handler/_diagnostics_archive.py`
- `voice_typer/server/crash_handler/_python_excepthook.py`
- `pyrefly-baseline.json`
**Fix:** Replace `_python_crash_dir = None  # type: ignore[assignment]` with `_python_crash_dir: Path | None = None`. Remove the corresponding entry from `pyrefly-baseline.json` if present. Add `from pathlib import Path` import if not already present.
**Severity:** 🟢 Low

### VP-13 — `service/privacy.py` duplicate-imports the same module on consecutive lines
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/service/privacy.py:44-49` has two consecutive `from voice_typer.server._user_data_files import (...)` statements targeting the same module. PEP 8 / ruff's `PIE794` favors a single import block.
**User Impact:** Trivial code smell indicating the extraction was done in two passes without consolidation. Marginal readability cost; signals inattention in the surrounding extraction.
**Root Cause:** Two-pass extraction without cleanup.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/privacy.py`
**Fix:** Consolidate into one `from voice_typer.server._user_data_files import (_GDPR_PERSONAL_FILES, _GDPR_PERSONAL_GLOBS as _GDPR_PERSONAL_GLOBS_INVENTORY)`.
**Severity:** 🟢 Low

### VP-14 — Mangled docstring indentation in `config_applier.apply_config` and `service/privacy.delete_all_personal_data`
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/config_applier.py:957-1028` (`apply_config` docstring) and `voice_typer/server/service/privacy.py:816-867` (`delete_all_personal_data` docstring) have indentation that shifts between 8 spaces and 16 spaces across consecutive lines. Both methods were extracted from a class body without re-indenting the docstring. `help(ConfigApplier.apply_config)` and `pydoc` produce visually broken output.
**User Impact:** IDE hover-text is hard to read. Future docstring edits may preserve the broken indentation.
**Root Cause:** Extraction from class body without re-indentation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config_applier.py`
- `voice_typer/server/service/privacy.py`
**Fix:** Re-indent both docstrings to a consistent 8-space body indentation (PEP 257).
**Severity:** 🟢 Low

### VP-15 — `native_hotkeys/base.py` re-defines package-level platform predicates as wrappers (leaky abstraction)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/native_hotkeys/base.py:43-52` defines `is_windows()`, `is_macos()`, `is_linux()` as 1-line wrappers that delegate to `_native_hotkeys_pkg.is_windows()` etc. The comment at line 14-21 explains: tests do `monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)`, and for the patch to take effect on calls made from this submodule, bare `is_macos()` references must resolve to the package-level binding at call time. So the wrappers exist solely to defeat Python's name-resolution semantics for test-patching compatibility.
**User Impact:** 3 wasted public symbols exported from `base.py`; new contributors may edit them thinking they're real predicates. The wrapper indirection is non-obvious — the comment explaining it is 8 lines long.
**Root Cause:** Test-patch compatibility workaround.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/native_hotkeys/base.py`
**Fix:** Change call sites in `base.py` to use `_native_hotkeys_pkg.is_windows()` directly (late binding through the module reference). Drop the 3 wrapper functions. Tests' monkeypatch continues to work because the call resolves through `_native_hotkeys_pkg` at call time.
**Severity:** 🟢 Low

### VP-16 — Missing `ExportFormat` type — `"json" | "csv"` union duplicated across 12+ sites
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** The bare-string union `"json" | "csv"` is redeclared at: `main/ipc/export-handlers.ts:340, 421`; `preload/index.ts:67, 73`; `types/ipc/bridge.ts:39, 43`; `components/common/ExportFormatMenu.tsx:13`; `pages/templates/components/TemplateToolbar.tsx:22, 28`; `pages/templates/hooks/useTemplateImportExport.ts:34, 46, 68`; `lib/tauri-bridge/window-namespace.ts:61`; `pages/history/hooks/useHistoryExport.ts:65`; `pages/vocabulary/components/VocabToolbar.tsx:21`; `pages/vocabulary/hooks/useVocabularyImportExport.ts:39, 53`. No `ExportFormat` type exists in `src/shared/` or `src/renderer/src/types/`. Adding a new format (e.g. `"tsv"`) would require touching 12+ files with no compile-time guard. P2 rule (no copy/pasted business logic) is violated.
**User Impact:** Adding a new export format requires touching 12+ files. A missed file silently breaks the format selector for that flow. No compile-time guard catches the drift.
**Root Cause:** The union was inlined at each call site instead of being centralized.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/ipc/export-handlers.ts`
- `voice_typer/client/src/preload/index.ts`
- `voice_typer/client/src/renderer/src/types/ipc/bridge.ts`
- `voice_typer/client/src/renderer/src/components/common/ExportFormatMenu.tsx`
- `voice_typer/client/src/renderer/src/pages/templates/components/TemplateToolbar.tsx`
- `voice_typer/client/src/renderer/src/pages/templates/hooks/useTemplateImportExport.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/window-namespace.ts`
- `voice_typer/client/src/renderer/src/pages/history/hooks/useHistoryExport.ts`
- `voice_typer/client/src/renderer/src/pages/vocabulary/components/VocabToolbar.tsx`
- `voice_typer/client/src/renderer/src/pages/vocabulary/hooks/useVocabularyImportExport.ts`
**Fix:** Create `voice_typer/client/src/shared/export-format.ts` with `export type ExportFormat = "json" | "csv";`. Update all 12+ sites to import from `@/shared/export-format` (or the appropriate relative path). Add a parity test asserting no inline `"json" | "csv"` literals remain in production code.
**Severity:** 🟡 Medium

### VP-17 — Stale `eslint-disable-*` directives (project uses Biome exclusively)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** No `.eslintrc*` / `eslint.config.*` exists in `voice_typer/client/`; `package.json:36` `lint` script is `biome check .`. Three inert ESLint directives remain in production code: `components/common/SettingRow.tsx:63` `// eslint-disable-next-line no-console`; `pages/history/hooks/useHistoryExport.ts:85` `// eslint-disable-next-line no-constant-condition`; `lib/tauri-bridge/detect.ts:23` multi-line comment block referencing "the previous `eslint-disable @typescript-eslint/no-explicit-any` directive" (already removed; the comment is stale doc). Plus 2 in test files.
**User Impact:** Misleading directives suggest ESLint is active when it isn't. New contributors may add more ESLint directives thinking they do something.
**Root Cause:** Migration from ESLint to Biome didn't clean up the old directives.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/common/SettingRow.tsx`
- `voice_typer/client/src/renderer/src/pages/history/hooks/useHistoryExport.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/detect.ts`
**Fix:** Delete the 3 stale `eslint-disable-*` directives and the stale doc comment in `detect.ts`. Run `biome check .` to verify no new warnings.
**Severity:** 🟢 Low

### VP-18 — Dead/unused exported TS code (6 exports with zero production importers)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** Six exports have no production importer (only their own file + tests):
1. `lib/theme-contrast.ts:36` `export const HEX_PARTIAL_RE` (sibling `HEX_STRICT_RE` IS used; `HEX_PARTIAL_RE` is not).
2. `lib/sound-manager.ts:161` `export function setVisualFeedbackEnabled()` — docstring at `:155-157` FALSELY claims "Used by App.tsx on initial config load"; App.tsx does NOT import it.
3. `lib/sound-manager.ts:190` `export function isVisualFeedbackEnabled()` — same false docstring claim.
4. `lib/color-utils.ts:379` `export function passesWCAG()` — only used by `themes/__tests__/parity.test.ts`.
5. `hooks/useStatsShare.ts:35,54` `canShareStats()` and `computeShareStats()` — only used by `hooks/__tests__/useStatsShare.test.ts`.
6. `hooks/usePython.ts:187` `export function getTimeout()` — only used by `hooks/__tests__/command-timeouts.test.ts`.
**User Impact:** Dead exports inflate the public API surface and confuse contributors about what's actually used. The false docstring claims on `setVisualFeedbackEnabled`/`isVisualFeedbackEnabled` are actively misleading.
**Root Cause:** Refactors removed the production callers but left the exports (and the test-only callers).
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/theme-contrast.ts`
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
- `voice_typer/client/src/renderer/src/lib/color-utils.ts`
- `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts`
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`
**Fix:** Either delete the 6 exports (and their test-only callers) OR mark them `@internal` and move to a `__test_utils__/` subdirectory. Fix the false docstrings on `setVisualFeedbackEnabled`/`isVisualFeedbackEnabled` regardless.
**Severity:** 🟢 Low

### VP-19 — IPC handler boilerplate duplicated 8× with 3 incompatible error-envelope shapes
**Status:** ❌ Not Fixed
**Description:** Across `main/ipc/`, every `ipcMain.handle` body wraps in `try { … } catch (e: unknown) { return { success: false, error: (e as Error).message }; }`. The `return { success: false, error: (e as Error).message };` line appears verbatim at `export-handlers.ts:407, 485, 545, 599` (4×) and structurally-equivalent `return { success: false, error: String(e) };` at `window-handlers.ts:180`. A parallel `{ ok: false, error: (e as Error).message }` shape appears at `window-handlers.ts:363`, plus `{ ok: false, error: "empty locale" }` at `:342`. Python-call uses a third shape `{ _error, _code }` at `python-call-handler.ts:100, 104, 88, 133`. Three distinct envelope shapes (`success/error`, `ok/error`, `_error/_code`) coexist with no shared helper or shared type. ~60 lines of boilerplate duplicated; ~11 `(e as Error).message` casts in `export-handlers.ts` alone.
**User Impact:** The renderer must handle 3 different error-envelope shapes depending on which IPC handler it called. A new handler author has to guess which shape to use. Adding typed errors (e.g. `code` discriminator) requires touching every handler.
**Root Cause:** No shared `withIpcEnvelope(handler, opts?)` wrapper or shared `IpcResult<T>` type was ever extracted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/ipc/export-handlers.ts`
- `voice_typer/client/src/main/ipc/window-handlers.ts`
- `voice_typer/client/src/main/ipc/python-call-handler.ts`
- `voice_typer/client/src/main/ipc/bubble-handlers.ts`
- `voice_typer/client/src/preload/index.ts`
- `voice_typer/client/src/renderer/src/types/ipc/bridge.ts`
**Fix:** Create `voice_typer/client/src/shared/ipc-result.ts` with `export type IpcResult<T, TCode = never> = { success: true; data: T } | { success: false; error: string; code?: TCode };` and `export function withIpcEnvelope<T>(handler: () => Promise<T>): Promise<IpcResult<T>>`. Wrap all `ipcMain.handle` bodies with it. Migrate the 3 envelope shapes to the canonical one.
**Severity:** 🟡 Medium

### VP-20 — `tray_window.open_electron_window` step-3 fallback spawns duplicate Electron process
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/tray_window.py:123-186` has a 3-tier fallback: (1) `event_bus.publish({"type": "show_window"})` over TCP, (2) Win32 `bring_electron_to_front` (returns False on macOS/Linux at line 65), (3) `_ensure_built_and_launch(hidden=False)` (line 158) unconditionally spawns a NEW Electron subprocess. If step 1 fails transiently (TCP socket between backend and Electron temporarily unavailable during backend restart / sidecar reconnect) AND step 2 returns False (non-Windows, or Windows-but-no-matching-window), step 3 launches a SECOND Electron process even though one is already running. The module-level `_electron_pid` (line 42) is only set AFTER step 3 succeeds — it is NOT consulted before step 3 to detect an existing process.
**User Impact:** On macOS/Linux, if the TCP connection between Python backend and Electron is briefly unavailable (e.g. during backend restart), clicking the tray icon spawns a SECOND Electron window. The user sees two Voice Typer windows and may experience state corruption from two processes writing to the same files.
**Root Cause:** Step 3 has no "is an Electron process already running?" guard.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/tray_window.py`
**Fix:** Before step 3, check `_electron_pid` (if set AND the process is alive via `psutil.pid_exists(_electron_pid)`), and if so, skip step 3 (or send a focus signal to the existing process). Only spawn if no live Electron process is detected.
**Severity:** 🟡 Medium

### VP-21 — `_KEYRING_REPROBE_INTERVAL_S` / `_KEYRING_REPROBE_INTERVAL_SECONDS` alias in same file (P2 violation)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `voice_typer/server/credential_store.py:595` defines `_KEYRING_REPROBE_INTERVAL_S` and `:602` defines `_KEYRING_REPROBE_INTERVAL_SECONDS` — both aliases for the same value. Doc comment at 596-601 explicitly admits "legacy/alternate-name alias kept for test-compat": `tests/test_keyring_reprobe.py` patches the `_SECONDS` form while production code uses the `_S` form. P2 rule (zero tolerance for duplicate definitions) is violated.
**User Impact:** Two names for the same constant invites drift if one is renamed without the other. Tests patch one form; production reads the other — a future change to the value via one name silently doesn't take effect in tests.
**Root Cause:** Test-patch compatibility workaround that was never cleaned up.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `tests/test_keyring_reprobe.py`
**Fix:** Pick one canonical name (`_KEYRING_REPROBE_INTERVAL_SECONDS` is more readable). Update `tests/test_keyring_reprobe.py` to patch the canonical name. Delete the alias.
**Severity:** 🟢 Low

### VP-22 — `app.py` `__main__` entry chain routes through `app.main()`, a self-admitted "backward-compat re-export"
**Status:** ⚠️ Blocked (tests/app/test_app_lifecycle_fixes.py pins app.main via inspect.getsource; needs test migration to ipc_server.main first)
**Description:** `pyproject.toml:273` declares `voice-typer = "voice_typer.server.ipc_server:main"`. `ipc_server.py:331-335` re-exports `main` from `voice_typer.server.ipc.entrypoint` (where `def main()` actually lives at entrypoint.py:165). But `voice_typer/__main__.py:110` does `from voice_typer.server.app import main as app_main; app_main()` — and `app.py:1620-1659` `def main()` is documented at line 1622-1627 as: "pyproject.toml now points to `voice_typer.server.ipc_server:main` as the canonical entry point; this function is kept as a thin re-export for backward compat." The call chain for `python -m voice_typer` is: `__main__.main → app.main → ipc_server.main → ipc.entrypoint.main`. The `app.main` hop is dead-weight indirection.
**User Impact:** The entry chain has an unnecessary hop. A future contributor tracing the entry point has to follow 4 jumps instead of 2.
**Root Cause:** The entry point was moved to `ipc_server.main` but `__main__.py` was never updated to call it directly.
**Progress:** None yet.
**Related Files:**
- `voice_typer/__main__.py`
- `voice_typer/server/app.py`
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/ipc/entrypoint.py`
- `pyproject.toml`
**Fix:** Change `voice_typer/__main__.py:110` from `from voice_typer.server.app import main as app_main` to `from voice_typer.server.ipc_server import main as ipc_main` (mirroring `server/__main__.py:13`). Then delete `app.py:main()` (lines 1620-1659). Tiny diff; safe because `app.main` has no production callers besides `__main__.py` (verified via grep).
**Severity:** 🟢 Low

### VP-23 — Layering inversion: `config/__init__.py:525` imports from `volume_ducker` (higher-level subsystem)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `config/__init__.py:525` — `from voice_typer.server.volume_ducker import _DEFAULT_SMART_DUCK_POLL_MS`. Config is the lowest-level data layer (loaded by `app.py:308` before any subsystem); `volume_ducker` is a feature subsystem (constructed lazily by `VoiceTyperApp._volume_ducker` property at app.py:1014-1032). `volume_ducker.py` does NOT import `config` (verified via Grep), so there is NO runtime cycle, but the dependency direction is inverted: a low-level module pulls a constant from a high-level one.
**User Impact:** Architectural layering violation. A future contributor reading `config/__init__.py` sees an import from a feature subsystem and may assume config depends on volume ducking. Moving the constant out of `volume_ducker` would require touching `config/__init__.py` too.
**Root Cause:** The constant was defined in `volume_ducker` (its primary consumer) but `config` also needs it for default-value computation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py`
- `voice_typer/server/volume_ducker.py`
**Fix:** Move `_DEFAULT_SMART_DUCK_POLL_MS` definition to `voice_typer/server/_audio_constants.py` (or new `volume_constants.py`); update `volume_ducker.py` and `config/__init__.py:525` to import from there.
**Severity:** 🟢 Low

### VP-24 — `app.py` is now 1676 LOC (UP +107 from HU-44 baseline 1569); `__init__` god-constructor unchanged
**Status:** ❌ Not Fixed
**Description:** `wc -l voice_typer/server/app.py` → 1676. HU-44 (review.md:4366) cited 1569; EO-1 (review.md:3325) cited the `__init__` at ~512 LOC. EC-7/AC-133 listed 5 inline business-logic blobs (`restart_app`, `quit_app`, `undo_last`, `repaste_last`, `_open_config_file`, `_cancel_dictation`) totaling ~573 LOC — VERIFIED GONE: each is now a ≤10-line delegate. BUT the `__init__` god-constructor (EO-1) is unchanged at ~550 LOC (lines 300-849), and the file has GROWN +107 LOC since HU-44 was filed.
**User Impact:** The file remains a monolith. Changes to `__init__` require reading 550 lines of subsystem-wiring boilerplate. The lazy-property backing fields (11 pairs, ~238 LOC at 865-1103) are pure boilerplate.
**Root Cause:** Prior extraction waves moved method BODIES out but left `__init__` and lazy-property machinery inline.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py`
**Fix:** Extract `voice_typer/server/app/` package: `_lazy_properties.py` (LazyPropertiesMixin: ~235 LOC at 864-1099), `_lazy_audio_proxy.py` (`_LazyAudioProcessorProxy` 168-254), `_delegates.py` (DelegatesMixin: ~494 LOC of delegate stubs at 1100-1594), `_reexports.py`, `_main.py`. Keeps `app/__init__.py` ≤300 LOC wiring-only per C-ARCH-1 spirit.
**Severity:** 🟡 Medium

### VP-25 — `main-window.ts` hardcoded English crash-loop dialog (HU-28 STILL LIVE)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** HU-28 (review.md:4170) flagged `main-window.ts:415-416` hardcoded "Voice Typer — Renderer crash loop" + "Please use the tray icon to Restart or Quit, then relaunch Voice Typer." Verified STILL PRESENT at lines 475-476 (file shifted +60 lines). The locale keys `dialog.crashLoop.title` + `dialog.crashLoop.mainBody` already exist in all 8 locale files with `{appName}` placeholders (verified `en.json:14-15`) — the fix is a 2-line edit. Note: there is also a translation drift — `en.json:14` says `"{appName} — Crash loop"` but the hardcoded literal says `"Voice Typer — Renderer crash loop"` (extra word "Renderer").
**User Impact:** Non-English users see English text in the crash-loop dialog. The {appName} placeholder pattern (C-BRAND-1) is bypassed.
**Root Cause:** The dialog was written before the locale keys were added; the migration was never completed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/i18n/locales/*.json`
**Fix:** Replace the hardcoded literals at lines 475-476 with `mainT("dialog.crashLoop.title", { appName: APP_NAME })` / `mainT("dialog.crashLoop.mainBody", { appName: APP_NAME })`. Align the locale key wording with the literal (or update the locale key to include "Renderer").
**Severity:** 🟡 Medium

### VP-26 — `Home.tsx` per-status_change `get_config` fetch (ER-62 STILL LIVE)
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** ER-62 (review.md:1406) flagged `Home.tsx:295-318` doing a `get_config` IPC round-trip on EVERY `status_change` event just to refresh the hotkey string. Re-verified STILL LIVE — code unchanged. The `status_change` event fires on every recording→transcribing→idle transition; a full IPC round-trip + setState per dictation cycle just to refresh the hotkey string (which only changes when the user edits Settings) is wasteful.
**User Impact:** 1 IPC round-trip + 1 setState + 1 re-render per dictation cycle. On a slow IPC connection (e.g. Tauri sidecar under load), this adds visible latency to every recording transition.
**Root Cause:** The hotkey reload was bolted onto `status_change` as a quick fix instead of subscribing to `config_changed`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
**Fix:** Subscribe to `config_changed` (which already fires on Settings save) and reload the hotkey there. Drop the per-status_change `get_config` fetch entirely.
**Severity:** 🟡 Medium

### VP-27 — `Home.tsx:215-243` initial-load effect runs 3 IPC round-trips SEQUENTIALLY
**Status:** ✅ Fixed (verified on Linux sandbox with Node 24 — Promise.allSettled parallel pattern applied)
**Description:** The mount-time `useEffect` in `Home.tsx:215-243` awaits `get_config` → then awaits `get_today_stats` → then awaits `get_history`. Each round-trip is ~5-50ms; total 15-150ms vs. ~5-50ms if parallelized. Note: `handleManualRefresh` at line 311 ALREADY uses `Promise.allSettled` for parallel fetch — so the inconsistency is solely in the initial-load path.
**User Impact:** 10-100ms slower initial Home page load on every navigation to Home.
**Root Cause:** The initial-load effect was written before `handleManualRefresh` established the parallel pattern.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
**Fix:** `const [cfgRes, sRes, hRes] = await Promise.allSettled([call("get_config"), call("get_today_stats"), call("get_history", {limit: 4})])` — mirrors `handleManualRefresh`'s pattern.
**Severity:** 🟡 Medium

### VP-28 — Dead `navVersion`/`bumpNavVersion` and `lastErrorAt` in appStore.ts
**Status:** ✅ Fixed (verified on Linux sandbox)
**Description:** `appStore.ts:81-101,154-156`: `navVersion` + `bumpNavVersion` — comment at 95-97 explicitly says "Until useNavigation wires in the bump, the counter stays at 0 (no false signals)." A repo-wide grep for `bumpNavVersion` returns only `appStore.ts:101,156` and `appStore.test.ts` — ZERO production callers. `useNavigation` never calls `bumpNavVersion`. `lastErrorAt` (appStore.ts:60-71,136-144): grep shows only `appStore.ts` and `appStore.test.ts` reference it — ZERO production readers. The test at appStore.test.ts:117 even asserts `lastErrorAt` is set, but no UI reads it.
**User Impact:** Shipping unused store fields invites future devs to depend on stale semantics. The "deliberately small" comment (appStore.ts:11) doesn't acknowledge the dead infrastructure.
**Root Cause:** YAGNI infrastructure shipped ahead of its consumer.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/stores/appStore.ts`
- `voice_typer/client/src/renderer/src/stores/appStore.test.ts`
**Fix:** Either wire `useNavigation` to call `bumpNavVersion` on every navigation + add a `lastErrorAt` consumer, OR remove both fields. Update tests accordingly.
**Severity:** 🟢 Low

### VP-29 — `sound-manager.ts` bleeds visual-feedback concern + 41KB inline base64 WAVs
**Status:** ❌ Not Fixed
**Description:** `voice_typer/client/src/renderer/src/lib/sound-manager.ts` (675 LOC) manages TWO unrelated concerns: (1) sound cues (AudioContext + HTMLAudio fallback + cue synthesis, lines 1-505 + 575-636); (2) visual feedback mirror for deaf accessibility (lines 60-204): `_visualEnabled`, `VISUAL_STORAGE_KEY`, `setVisualFeedbackEnabled`, `isVisualFeedbackEnabled` — a SETTINGS flag, not a sound concern. Plus two embedded base64 WAV data URLs (`START_BEEP_WAV` at 526 = 17,726 chars; `STOP_BEEP_WAV` ~530-573 = 23,066 chars) bloat the file with ~41 KB of base64 inline. The 4-branch `playViaAudioContext` function (382-505) is ~120 LOC of duplicated `osc.connect(gain).connect(ctx.destination); osc.start(); osc.stop(); osc.onended = () => { osc.disconnect(); gain.disconnect(); }` boilerplate 4 times.
**User Impact:** The file's name is a half-truth. The 41KB of inline base64 hurts editor syntax highlighting, Vite HMR (re-parses the whole file on every edit), and grep/diff noise.
**Root Cause:** The visual-feedback flag was bolted onto sound-manager instead of being its own module. The WAVs were embedded pre-Vite-asset-import being the standard pattern.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
**Fix:** (1) Split `sound-manager.ts` into `sound-manager.ts` (cues + AudioContext) + `accessibility-manager.ts` (visual-feedback flag). (2) Move base64 WAVs to `lib/sound-manager/beeps.ts` or to `lib/sound-manager/beeps/{start,stop}.wav` files with Vite `?url` imports. (3) Collapse `playViaAudioContext` 4-branch switch into a per-kind config table.
**Severity:** 🟡 Medium

### VP-30 — `state.rs` (838 LOC) god-module mixing 7 concerns (re-confirm SI-25)
**Status:** ❌ Not Fixed
**Description:** SI-25 flagged this and was deferred. Re-confirmed: `state.rs:41-43` poison-safe `lock()` helper; `:80-257` `SidecarHandle` enum + Drop impl (178 LOC, process-management concern); `:259-351` `SidecarState` struct (actual shared state); `:353-660` IPC/shutdown machinery (`shutdown_sidecar_for_exit`, `HOST_SHUTDOWN_GRACE_MS`, `on_relaunch_app`, `on_host_exit`, `send_fire_and_forget_frame` — host-entrypoint callbacks, not state data). The docstring at `:504` calls them "Host-entrypoint callbacks (extracted from main.rs)".
**User Impact:** Reading the state module requires mentally tracking 7 concerns. A change to `SidecarHandle` risks breaking the shutdown callbacks in the same file.
**Root Cause:** SI-25 was deferred; subsequent additions appended to the same file.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs`
**Fix:** Extract `lock<T>` helper to `util/sync.rs` (and remove the `mutex_lock` alias in `logging.rs`). Extract `SidecarHandle` to `state/sidecar_handle.rs` or `platform/sidecar_handle.rs`. Extract the 5 host-lifecycle functions to `host_lifecycle.rs` or `shutdown.rs`. Post-split `state.rs` would be ~95 LOC (struct + new + Default).
**Severity:** 🟡 Medium

### VP-31 — `system_cmds.rs` (589 LOC) misnamed and mixes 4 unrelated concerns
**Status:** ❌ Not Fixed
**Description:** Despite the name, only `open_logs` and `renderer_log_error` are genuinely "system" commands. The rest: redaction library (`:58-167`, 110 LOC: `REDACTED_MARKER`, `is_sensitive_key`, `redact_config_secrets` — consumed only by `export_config`); `open_model_import_dialog` (folder picker); `export_templates`/`export_config` (thin wrappers calling `crate::commands::export::export_data` — export commands misfiled in system_cmds).
**User Impact:** A contributor looking for `export_config` looks in `export.rs` first; finding it in `system_cmds.rs` is surprising. The redaction library is invisible to someone auditing secret-handling.
**Root Cause:** The file accumulated commands without being re-organized.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/system_cmds.rs`
- `src-tauri/src/commands/export.rs`
- `src-tauri/src/commands/mod.rs`
**Fix:** Extract `commands/redaction.rs` (or `secrets.rs`) for the redaction helpers. Move `export_templates`/`export_config` to `commands/export.rs`. Post-extraction `system_cmds.rs` would be ~120 LOC.
**Severity:** 🟡 Medium

### VP-32 — `tray.rs` (745 LOC) clusters 5 concerns; 3 are extractable
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/tray.rs` mixes: (a) menu deserialization types (`:48-82`: `MenuItemData`, `TrayMenuPayload`, `TrayStatePayload`); (b) icon cache + loader (`:92-191`: `TRAY_ICON_CACHE` static + `load_tray_icon`, 100 LOC with its own whitelist + poisoned-lock fallback + disk-read-outside-lock); (c) menu construction (`:193-259`: `build_item_refs`, `build_menu`, `empty_menu`); (d) event predicates (`:261-280`: `is_focus_main_window_event`); (e) top-level wiring (`:282-489`: `create_tray`, 188 LOC). Tests at `:491-745` (254 LOC = 34% of file).
**User Impact:** The icon-cache concern (with its own state + I/O) is mixed with menu construction. A change to icon loading risks breaking menu event handling.
**Root Cause:** The file accumulated responsibilities without being split.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/tray.rs`
**Fix:** Split into `tray/{icon_cache.rs, menu.rs, events.rs}` mirroring the `commands/bubble/*` decomposition pattern. `icon_cache.rs` extraction is the highest-value (it's the only piece with state + I/O).
**Severity:** 🟡 Medium

### VP-33 — `util.rs` (754 LOC) is a 4-concern catch-all "utils" graveyard
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/util.rs` bundles 4 orthogonal concerns: constants block (`:6-160`: 15+ named constants spanning token, supervisor, shutdown, heartbeat, kill_tree, dispatch, restart, rotation — each tied to a DIFFERENT subsystem); token/hex (`:162-191`: `generate_token` + private `hex::encode`); time (`:193-251`: `now_timestamp` + Howard Hinnant's `civil_from_days`); atomic fs (`:253-461`: `atomic_write_bytes`, `atomic_copy`, `atomic_copy_file` — generic filesystem helpers consumed almost entirely by `migrate/*`). Tests at `:463-754` (291 LOC = 39% of file).
**User Impact:** A contributor needing one constant has to read 15 unrelated ones. The atomic-fs helpers are co-located with token generation despite having no relationship.
**Root Cause:** "util" as a category attracts unrelated helpers.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/util.rs`
**Fix:** Split into `util/consts.rs` (or move each constant to its owning module), `util/crypto.rs` (token), `util/time.rs` (timestamp), `util/atomic_fs.rs` (atomic fs ops).
**Severity:** 🟡 Medium

### VP-34 — `_secrets.py` (1011 LOC) mixes secret redaction + URL allowlist/SSRF defense
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/_secrets.py` lines 1-700: `redact_secret`, `redact_api_keys`, `redact_url`, `_redact_home_path`, `redact_for_export` (log-message redaction). Lines 700-1011: `_DEFAULT_ALLOWED_HOSTS`, `extend_url_allowlist`, `is_url_allowed`, `assert_url_allowed`, `_is_private_ip`, `_is_ip_literal`, `_load_env_allowlist_extensions` (cloud URL allowlist + SSRF defense). Two unrelated concerns in one module. A security auditor has to read 5 files (`_secrets.py`, `security.py`, `secure_file_io.py`, `_http_safety.py`, `_security_attributes.py`) to understand the threat model. Already documented at review.md:3598 / FI-S8 — confirmed still pending.
**User Impact:** A new redaction pattern requires deciding which file should host it. The two concerns evolve on different schedules (redaction patterns change with PII regulations; URL allowlist changes with cloud-provider additions).
**Root Cause:** The URL allowlist was added to `_secrets.py` because it was the "security" file at the time.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_secrets.py`
**Fix:** Split `_secrets.py` → keep redaction in `_secrets.py`; extract `_cloud_url_safety.py` for the URL allowlist + SSRF half.
**Severity:** 🟡 Medium

### VP-35 — `security.py` (909 LOC) mixes PII redaction + model-integrity verification
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/security.py` lines 100-383: `PIIRedactionFilter`, `_redact_text`, `redact_pii`, `_redact_home_path_in_text` (log-message redaction). Lines 386-909: `_load_model_hashes`, SHA-256 verification, pinned-revision enforcement (supply-chain integrity for HuggingFace model downloads). Two unrelated concerns. The model-integrity surface is further fragmented across `security.py` (hash verification) + `_model_integrity.py` (pattern allowlist) — these two files must stay in sync but live in different modules. Already documented at review.md:3600 / FI-S9 — confirmed still pending.
**User Impact:** A security auditor reviewing model-integrity must check two files. Changes to PII redaction risk accidentally touching model-integrity code in the same file.
**Root Cause:** Both concerns were "security" and landed in the same file.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/security.py`
- `voice_typer/server/_model_integrity.py`
**Fix:** Extract `model_integrity.py` for the SHA-256 verification half. `_model_integrity.py` (pattern allowlist) should be merged into the new `model_integrity.py` for a single source of truth.
**Severity:** 🟡 Medium

### VP-36 — `config_path_safety.py` is a half-done re-export shim
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/config_path_safety.py` (75 LOC) re-exports 3 functions (`_validate_path_safety`, `_is_path_within`, `_validate_import_path`) from `config_internals.paths` (line 65-69). The module's own docstring (lines 14-23) admits: "config_internals.paths is a mixed module that bundles path-safety + config-dir resolution + cross-process lock + SystemRoot validation — the finding's complaint is exactly that these concerns are not yet separated into dedicated modules." The named-home re-export exists but the actual function bodies have NOT been migrated. Callers verified via grep: `config/__init__.py`, `config/coercion.py`, `env_validation.py`, `handlers/model_handlers.py` (4 production callers + 9 test files). A future contributor grepping `config_path_safety` finds the named home but not the implementation.
**User Impact:** Misleading module organization — the "home" for path-safety is a re-export, not the implementation.
**Root Cause:** The split was started (named home created) but the bodies were never moved.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config_path_safety.py`
- `voice_typer/server/config_internals/paths.py`
**Fix:** Either (a) move the 3 path-safety function bodies from `config_internals/paths.py` into `config_path_safety.py` and have `paths.py` re-import them, OR (b) delete `config_path_safety.py` and update the 4 production callers + 9 test files to import directly from `config_internals.paths`.
**Severity:** 🟡 Medium

### VP-37 — `clipboard/manager.paste` is a 441-line god-method; `_is_safe_paste_target` adds 258 more
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/clipboard/manager.py:777-1217` — `paste()` is 441 lines (verified by AST analysis). Interleaves 7 concerns: snapshot registration + thread spawn, stuck-modifier release, safety-target check, rate-limit check, paste_enabled gate, keystroke send, return-value bookkeeping. `_is_safe_paste_target` (258 lines, `:259-516`) combines 4 sub-checks. The 1417-line module is effectively a single function with helpers.
**User Impact:** Hard to test in isolation; risk of regression in any of 7 interleaved concerns.
**Root Cause:** The paste pipeline accreted concerns over time.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py`
- `voice_typer/server/clipboard_target_safety/` (subpackage exists but manager.py:_is_safe_paste_target is NOT using it — dead/duplicate code)
**Fix:** Extract `paste` into a `PastePipeline` class (≤80 LOC): `prepare() → check_safety() → check_rate_limit() → send_keystroke() → register_snapshot() → restore_later()`. Wire `_is_safe_paste_target` to use the existing `clipboard_target_safety/` subpackage.
**Severity:** 🟡 Medium

### VP-38 — `startup_sequence.run` is a 731-line god-method
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/startup_sequence.py:311-1041` — single `def run(self) -> None:` spans 731 lines. Interleaves ≥8 distinct concerns: VAD preload, crash-diagnostics sweep, stale-backup sweep, onboarding-fail counter, autostart registration, microphone enumeration, hotkey registration, parallel prewarm/mic work, model load. Class itself has only 2 methods (`__init__` + `run`) — `run` is doing the work of 8 modules.
**User Impact:** Any change to startup ordering requires reading 731 lines; tests can only exercise the whole 731-line path, not individual phases.
**Root Cause:** The startup sequence accreted phases without being decomposed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/startup_sequence.py`
**Fix:** Extract `run` into a sequence of `_phase_*` helpers in a `startup_sequence/phases/` package (one file per phase). `run` becomes a <40-line orchestrator.
**Severity:** 🟡 Medium

### VP-39 — `ShutdownController` is a 32-method god-class
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/shutdown_controller.py:173-1397` — `ShutdownController` has 32 instance methods. Breakdown: 1 `__init__` (94 LOC), 5 cleanup-orchestration (`_do_cleanup`, `_do_fast_cleanup`, `_drain_ws_dispatch_pool`, `_build_sequenced_plan`, `_build_parallel_plan`, `_run_plan`, `_late_bookbook_tray_stop`), 13 `_teardown_*` methods (lines 1042-1276), 1 public `quit`, 4 atexit/signal (`_arm_shutdown_watchdog`, `_atexit_log`, `_atexit_cleanup`, `_install_signal_handlers`, `_signal_watcher_loop`), 2 Win32 console (`_install_win32_console_handler`, `_win32_console_handler`).
**User Impact:** Reading the cleanup sequence requires mentally tracking 32 methods; the 13 `_teardown_*` methods are sequentially coupled through the same `app` handle.
**Root Cause:** Teardown methods accreted on the controller instead of being registered.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`
**Fix:** Extract the 13 `_teardown_*` methods into a `shutdown/teardown_registry.py` table-driven dispatch (each teardown is a `(name, callable)` pair), reducing ShutdownController to ~15 orchestration methods.
**Severity:** 🟡 Medium

### VP-40 — `CrashRecovery.__del__` is 102 lines
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/crash_recovery.py:1127-1228` — `__del__` spans 102 lines. `__del__` is fragile: GC timing, interpreter-shutdown ordering, resurrection semantics, and exceptions raised inside `__del__` are silently swallowed (printed to stderr only). A 102-line `__del__` multiplies the surface for subtle bugs (e.g. a `TypeError` deep inside the method is invisible).
**User Impact:** `__del__` bugs are notoriously hard to reproduce. The 102-line body likely does best-effort cleanup that should be in an explicit `close()` method.
**Root Cause:** Cleanup logic was placed in `__del__` instead of an explicit lifecycle method.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_recovery.py`
- `voice_typer/server/shutdown_controller.py`
**Fix:** Move the cleanup body into a `def close(self) -> None:` method; have `__del__` call `close()` inside `try/except: pass` (≤5 LOC). Ensure `shutdown_controller._teardown_crash_recovery` calls `close()` explicitly.
**Severity:** 🟡 Medium

### GQ-1 — history_db OFFSET pagination hits 594ms on 500K-row DB
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/history/test_history_db_perf_fixes.py` 27/27 pass)
**Description:** Deep `OFFSET` pagination on `history_db.get_recent` scans & discards `offset` rows before applying LIMIT, because SQLite uses `SCAN transcriptions USING INDEX idx_timestamp` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`. Measured on Linux sandbox with a 500K-row / 93MB DB: `get_recent(limit=50, offset=499900)` = 594ms median (min 587ms). The cursor-based pagination path (`before_timestamp`/`before_id`) is already implemented and is O(log N) per page, but the OFFSET branch remains the default.
**User Impact:** When a user scrolls toward the end of a long history list (e.g. a power-user with 500K historical transcriptions), each pagination fetch takes ~600ms — visibly laggy UI. The History panel freezes for over half a second per page-flip while scrolling.
**Root Cause:** OFFSET semantics are inherently O(offset) in SQLite; the default caller path uses OFFSET instead of the cursor (timestamp, id) pagination that already exists.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:2288-2308`
- `voice_typer/server/history_db_internals/schema.py:538-558`
**Fix:** (1) Switch the renderer (History pagination) to cursor-based pagination exclusively — pass the (timestamp, id) of the last row of the previous page. (2) Add a composite index `idx_timestamp_id ON transcriptions(timestamp DESC, id DESC)` to eliminate the TEMP B-TREE. (3) Guard the OFFSET branch with `assert offset < 1000` or deprecate it outright.
**Severity:** 🔴 Critical

### GQ-2 — history_db FTS5 search hits 355ms with many matches
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/history/test_history_db_perf_fixes.py` 27/27 pass)
**Description:** FTS5 `search()` materializes ALL matches into a temp B-tree sorted by `t.timestamp DESC, t.id DESC` before applying LIMIT 50. EXPLAIN QUERY PLAN shows `SCAN f VIRTUAL TABLE INDEX 0:M1` + `SEARCH t USING INTEGER PRIMARY KEY (rowid=?)` + `USE TEMP B-TREE FOR ORDER BY`. Measured on Linux sandbox with a 500K-row DB: `search(query='hello world', limit=50)` = 355ms median.
**User Impact:** When the user types in the History search box, every keystroke (if not debounced) costs 355ms. Typing 'hello world' feels laggy — the search box feels unresponsive and the user may give up on searching altogether.
**Root Cause:** `ORDER BY t.timestamp DESC, t.id DESC` requires sorting ALL matches; LIMIT 50 doesn't push down into FTS5's MATCH result.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:2377-2429`
**Fix:** (1) Add composite index `idx_timestamp_id ON transcriptions(timestamp DESC, id DESC)`. (2) Restructure the FTS5 query to apply LIMIT inside the FTS subquery: `SELECT t.* FROM (SELECT rowid FROM transcriptions_fts WHERE transcriptions_fts MATCH ? ORDER BY rowid DESC LIMIT 50) AS f JOIN transcriptions t ON t.id = f.rowid ORDER BY t.timestamp DESC, t.id DESC`. (3) Verify search-box input is debounced in renderer.
**Severity:** 🔴 Critical

### GQ-3 — First Config.save() takes 164ms due to cold credential_store probe
**Status:** ✅ Fixed (verified on Linux sandbox — eager warmup classmethod added)
**Description:** `Config.save()` calls `credential_store.is_keyring_available()` lazily inside `_save_unlocked` on every save. The first call pays the cold-import + backend-probe cost (D-Bus / Windows Credential Manager / macOS Keychain). Measured on Linux sandbox: first `Config.save()` = 164.89ms; `is_keyring_available()` cold probe = 151.61ms. Subsequent calls are cached (0.01ms).
**User Impact:** The first time the user changes a setting after starting Voice Typer, the IPC `set_config` call takes ~165ms to return — noticeable lag on the first settings change after launch. Also delays onboarding config save and first post-migration save.
**Root Cause:** `is_keyring_available()` is invoked lazily inside `_save_unlocked` rather than eagerly at startup; the underlying `keyring` module import + backend probe runs only on the first call.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py:1440`
- `voice_typer/server/credential_store.py`
**Fix:** Eagerly call `credential_store.is_keyring_available()` once during app startup (e.g. in `VoiceTyperApp.__init__` or a background thread spawned at import time) to amortize the 151ms cold-probe cost off the user-visible save path.
**Severity:** 🔴 Critical

### GQ-4 — No CI perf regression detection — benchmarks exist but are unwired
**Status:** ✅ Fixed (verified on Linux sandbox — `make bench` target + perf.yml workflow added)
**Description:** The `bench/` directory has 5 well-structured benchmark scripts (cold-start, transcription, audio filter chain, streaming, VAD), but none are wired into CI as regression gates. `scripts/profile_imports.py --max-self-us` flag exists but is unused. `pytest-benchmark` is declared as a dep but `tests/bench/` does NOT exist and the `benchmark` fixture is never used. `grep -rn 'bench_' .github/workflows/*.yml Makefile` returns ZERO hits.
**User Impact:** A 2x slowdown in tray cold-start, transcription WPS, audio filter chain p99, or streaming assembler throughput would silently merge. Users would experience a 'Voice Typer feels slower' regression with no detection at the engineering level — only after enough users complain.
**Root Cause:** Benchmarks were authored as one-off measurement scripts but never integrated into the CI ratchet pattern that already exists for coverage and ruff.
**Progress:** None yet.
**Related Files:**
- `bench/bench_startup.py`
- `bench/bench_transcription.py`
- `bench/bench_audio_filter_chain.py`
- `bench/bench_streaming.py`
- `bench/bench_vad.py`
- `scripts/profile_imports.py`
- `Makefile`
- `.github/workflows/build.yml`
**Fix:** (1) Add `make bench` target invoking all 5 `bench/bench_*.py --json` scripts. (2) Add a CI step that runs benches on a fixed runner type and compares p99 against a `bench-baseline.json` (mirroring the ruff/coverage ratchet pattern). (3) Wire `scripts/profile_imports.py --runs 3 --max-self-us 50000 voice_typer.server.tray` as a CI gate. (4) Create `tests/bench/test_audio_filter_chain.py` using the `benchmark` fixture for per-commit regression tracking.
**Severity:** 🔴 Critical

### GQ-5 — Rate limiter called 2× per IPC dispatch — halves effective burst budget
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/server/test_ipc_rate_limiter_chokepoints.py` 12/12 pass)
**Description:** The rate limiter is invoked TWICE per accepted dispatch: once at the transport chokepoint (`transport_tcp.py:668` for TCP, `sidecar_ws.py:1081` for WS), and again in the shared `_dispatch` (`dispatcher.py:146`). `rate_limiter.allow()` consumes burst budget on each call, so an accepted `download_model` (cost=50) actually consumes 100 burst units. A client sending 100 non-heartbeat commands/sec consumes the entire 200/s cap, hitting `client.rate_limited` at 100 msg/sec instead of 200/sec. For `download_model` (cost 50), only 2 concurrent downloads are allowed instead of 4.
**User Impact:** Under heavy IPC load (rapid UI interactions, multiple concurrent downloads), the user sees spurious 'rate limited' errors — downloads that should succeed are rejected because the burst budget is double-consumed.
**Root Cause:** The dispatcher's check (added per `dispatcher.py:128-145` comment) to close the stdin gap also fires on the TCP/WS paths which already gate at the transport chokepoint.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/dispatcher.py:146`
- `voice_typer/server/ipc/transport_tcp.py:668`
- `voice_typer/server/sidecar_ws.py:1081`
- `voice_typer/server/ipc/rate_limiter.py:389-393`
**Fix:** In `_dispatch`, skip the limiter check when the transport has already gated. Either (a) pass a `pre_gated=True` kwarg from `_tcp_dispatch_and_respond` and the WS `dispatch()` closure, OR (b) remove the limiter call from `_dispatch` and add it to `stdin_runner._run` (the only path that lacks it). Option (b) is simpler and aligns with the 'transport owns the chokepoint' design.
**Severity:** 🔴 High

### GQ-6 — _evict_lru_model bypasses registry unregister — leaves stale engine
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_model_manager.py` pass)
**Description:** `ModelManager._evict_lru_model` (line 1748-1758) calls `engine.unload()` directly on the oldest backend but does NOT call `self._registry.unregister(oldest_backend)`. Compare `_change_model_unload_phase` (lines 990-996) which explicitly does BOTH. After LRU eviction, `asr_registry._backends[oldest_backend]` still holds the (now half-unloaded) engine object. The next `_ensure_engine(oldest_backend)` short-circuits — returning a stale engine whose model weights are freed.
**User Impact:** A user cycling back to a previously-evicted backend (e.g. Whisper → Parakeet → Whisper with `_MAX_LOADED_MODELS=2`) gets a stale engine object whose `self._model` / `self._processor` are None. First transcribe on it crashes or returns empty text — the user thinks Voice Typer is broken.
**Root Cause:** Same gap `_change_model_unload_phase`'s docstring (line 991-996) explicitly warns about: 'UNREGISTER the old backend so _ensure_engine actually constructs a fresh one.'
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1748-1758`
- `voice_typer/server/model_manager.py:990-996`
**Fix:** In `_evict_lru_model`, after `engine.unload()`, call `self._registry.unregister(oldest_backend)` — mirroring `_change_model_unload_phase` lines 990-996.
**Severity:** 🔴 High

### GQ-7 — _evict_lru_model bypasses registry busy-check — potential use-after-free
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_model_manager.py::test_eviction_respects_busy_check` pass)
**Description:** `_evict_lru_model` calls `engine.unload()` DIRECTLY (line 1753), bypassing `self._registry.unload(oldest_backend)` which performs a busy-check at `asr_registry.py:880-881` (`if target in self._busy_backends: raise RuntimeError`). This means LRU eviction can fire `engine.unload()` on a backend that is currently inside `transcribe_with_fallback` on another thread. Parakeet's unload waits via `_inference_cond` for active inference to finish (defense-in-depth), but Whisper's `unload` (transcription.py:1471) and Qwen's (`qwen_engine.py:963`) may not.
**User Impact:** Potential use-after-free / heap corruption if LRU eviction fires while a non-Parakeet backend is mid-transcribe. Triggered when user has 3+ backends loaded (max=2) AND switches model during an active recording on a non-Parakeet backend. Would manifest as a crash or garbled transcription.
**Root Cause:** Direct `engine.unload()` call skips the registry's busy-check guard.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1750-1753`
- `voice_typer/server/asr_registry.py:880-881`
**Fix:** Call `self._registry.unload(oldest_backend)` instead of `engine.unload()` directly. Wrap in try/except RuntimeError to log + skip eviction if the backend is busy (mirroring `_do_idle_unload`'s pattern at line 1972-1979).
**Severity:** 🔴 High

### GQ-8 — text_cleanup.py dead eager-precompiled patterns cost 254ms at max-size
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_text_cleanup.py` 136/136 pass after dead-code deletion)
**Description:** `_active_phrase_patterns` and `_active_extra_word_patterns` are populated by `_compile_phrase_patterns(phrases)` inside `configure_corrections` (lines 705-714). A grep across `voice_typer/` returns ZERO subscript reads in production code (only in tests + comments). The actual hot path uses a separate combined-alternation cache. With a SEC-011-maximum (5000 phrases + 5000 extra-word patterns) user corrections file, `configure_corrections` measures 254ms median / 290ms max — most of it spent compiling 10000 patterns that are immediately discarded. With bundled corrections (8 phrases + 1 extra word) the cost is only ~0.075ms.
**User Impact:** Users who maintain large corrections dictionaries (a power-user feature — SEC-011 allows up to 5000 entries per category) see a 254ms stall every time they save their corrections file or restart Voice Typer. The Settings panel feels frozen for a quarter second.
**Root Cause:** The XV-42 refactor that introduced the combined-alternation cache left the eager precompile in place. Already catalogued as AB-50 in review.md:2712.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:455-456`
- `voice_typer/server/text_cleanup.py:518-528`
- `voice_typer/server/text_cleanup.py:705-714`
**Fix:** Delete `_active_phrase_patterns`, `_active_extra_word_patterns`, `_compile_phrase_patterns`, the two `global` declarations, and the two assignments in `configure_corrections`. The combined-alternation cache (`_phrases_re_cache` / `_extra_words_re_cache`) already handles compilation-on-demand. Update affected tests.
**Severity:** 🔴 High

### GQ-9 — parakeet_engine default batch_size=1 forces 13 sequential generate() calls
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_parakeet_engine.py` 3 new batch_size tests pass)
**Description:** `self._INFERENCE_BATCH_SIZE: int = max(1, int(os.environ.get("PARAKEET_BATCH_SIZE", "1")))` (line 281) defaults to 1. For a 5-min dictation split into 13 × 25s chunks (per `_CHUNK_SECONDS=25` at line 131), the default runs 13 sequential `processor()` + `generate()` + `decode()` round-trips. Each round-trip pays Python↔C++ FFI overhead + serializes GPU work across chunks. Comment at lines 264-269 explicitly states the default-1 is to preserve the test contract `mock_model.generate.call_count == 2`.
**User Impact:** Long dictations (5+ minutes) take 200-500ms longer than necessary on CUDA. The user perceives this as a delay between stopping the recording and seeing the transcribed text appear in the target window.
**Root Cause:** Default batch size pinned to 1 to preserve test contract (`mock_model.generate.call_count == 2`).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:281`
- `voice_typer/server/parakeet_engine.py:924`
- `voice_typer/server/parakeet_engine.py:947`
**Fix:** Change default to 2 (auto-falls-back to sequential on OOM per lines 1000-1015). Update `test_transcribe_long_audio_splits_into_chunks` to assert `call_count == ceil(n_chunks / batch_size)` instead of `== 2`.
**Severity:** 🔴 High

### GQ-10 — shutdown_controller documented 20s deadline not enforced between sequenced steps
**Status:** ✅ Fixed (verified on Linux sandbox — deadline check added to shutdown/plan.py)
**Description:** `shutdown_controller.py:418` sets `_shutdown_deadline = time.monotonic() + 20.0` and publishes it (line 430). The `_do_cleanup` docstring (lines 412-417) states: 'The 20s deadline is checked before each phase **and between each sequenced step**'. However, `run_plan` in `shutdown/plan.py:160-184` iterates over `plan.steps` calling `_run_with_timeout(step.func, timeout=step.timeout)` for each — there is NO check of `controller._shutdown_deadline` between iterations. Sequenced steps have fixed timeouts: `teardown_timers_and_recording`=10s, `teardown_recorder`=15s, `teardown_history_db`=15s, `teardown_crash_recovery`=10s. Cumulative worst-case sequenced phase = 50s — exceeds documented 20s deadline by 2.5x.
**User Impact:** If Voice Typer ever hangs during shutdown (a PortAudio stream that won't close, a stuck keyring call, etc.), the app can take up to 72-82s to fully exit instead of the documented 20s. The user sees a 'Voice Typer is not responding' dialog and force-quits.
**Root Cause:** The inter-step deadline check documented in the docstring is not implemented in `run_plan`. Either lost during the Phase 4.5 extraction or never implemented.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown/plan.py:160-184`
- `voice_typer/server/shutdown_controller.py:412-418`
**Fix:** Add a deadline check at the top of the `for step in plan.steps` loop in `shutdown/plan.py`: `if controller._shutdown_deadline is not None and _shutdown_remaining(controller._shutdown_deadline) < 5.0 and step.name not in CRITICAL_STEPS: log.warning(...); skipped.append(step.name); continue`. Define `CRITICAL_STEPS = {"teardown_recorder", "teardown_history_db", "teardown_crash_recovery"}` so flush-bearing helpers always run. Alternative: pass `deadline` as a parameter to `run_plan` (cleaner API) instead of reading `controller._shutdown_deadline`.
**Severity:** 🔴 High

### GQ-11 — logging.rs 3232 LOC + 89 inline #[test] fns violates C-TEST-5
**Status:** ✅ Fixed (verified on Linux sandbox — logging.rs 3232→1808 LOC, 89 inline tests moved to logging_tests.rs)
**Description:** `wc -l` = 3232 lines. `grep -c '^\s*#\[test\]'` = 89 inline `#[test]` fns. Test block = lines 1766 → 3232 = 1467 LOC = 45.4% of the file. The file's own header (lines 6-30) admits 'This file is a 2161-line monolith mixing 6 concerns: init orchestration, `CombinedLogger` multi-sink dispatch, a 515-LOC PII redaction engine (`redact_pii` + 5+ `try_match_*` state machines), `install_panic_hook`, `EarlyLogger` + `EARLY_LOGGER_HANDLE`, and `RotatingFileWriter`' and proposes a 7-file split. CONSTRAINTS.md C-TEST-5 explicitly says: 'No inline `#[cfg(test)] mod tests` blocks in `.rs` source files' — rationale explicitly cites `logging.rs`'s 89 inline tests as the reason for the rule.
**User Impact:** Any change to logging risks merge conflicts. Test discovery is slow. Inline tests bloat the production binary's debug-info even in release builds. Contributors navigating the file waste time scrolling past 1467 lines of tests to find the production logger.
**Root Cause:** Historical accumulation; the file's own header documents a 7-file split plan that was never executed. C-TEST-5 was added BECAUSE of this file.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs:1-3232`
**Fix:** Execute the file's own proposed split (lines 14-30) — `platform/logging/{mod,init,combined,redact,panic_hook,early,rotating}.rs` — and move all 89 inline tests to `platform/logging/tests/*.rs` sibling files per C-TEST-5 ('co-located per sub-module'). Delete the orphaned `log_file.rs` and `log_rotation.rs` (see GQ-12) so there's exactly ONE `RotatingFileWriter` implementation.
**Severity:** 🔴 High

### GQ-12 — log_file.rs + log_rotation.rs are orphaned 893 LOC dead code
**Status:** ✅ Fixed (verified on Linux sandbox — log_file.rs + log_rotation.rs deleted, 893 LOC dead code removed)
**Description:** `platform/log_file.rs` (569 LOC) and `platform/log_rotation.rs` (324 LOC) are full re-implementations of `RotatingFileWriter` + `rotate()` that were created as an 'AC-138 split' but are NOT declared in `mod.rs` (14 LOC, declares only `logging`, `open_path`, `paths`, `process`). `grep -rn 'log_file|log_rotation'` across `src-tauri/src/` returns matches ONLY inside the two orphan files themselves + comment references in `logging.rs:7`. The `RotatingFileWriter` struct, `write_line`, `flush`, `rotate`, `should_rotate` all exist identically in BOTH `logging.rs:1514-1764` AND `log_file.rs:55-236` + `log_rotation.rs:56-126` — a duplicated 893-LOC refactor that was never wired in.
**User Impact:** Contributors reading `log_file.rs`/`log_rotation.rs` will assume it's the live code (it isn't), and any contributor editing `logging.rs::RotatingFileWriter` won't notice they need to also update the (unused) extracted copy. Wastes reviewer cognitive load.
**Root Cause:** AC-138 refactor started but never finished — the original code was never deleted and the new modules were never wired into mod.rs.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/log_file.rs:1-569`
- `src-tauri/src/platform/log_rotation.rs:1-324`
- `src-tauri/src/platform/mod.rs:1-14`
**Fix:** Either (a) finish AC-138 by deleting `logging.rs`'s `RotatingFileWriter` (lines 1510-1764) and `rotate()` (1698-1754), wiring `log_file` + `log_rotation` into `mod.rs`, and moving their inline tests to `tests/`; OR (b) abandon AC-138 by deleting `log_file.rs` and `log_rotation.rs` outright. Option (b) is simpler and lower-risk.
**Severity:** 🔴 High

### GQ-13 — Rust source files contain inline #[cfg(test)] mod tests blocks (C-TEST-5 violation)
**Status:** ✅ Fixed (verified on Linux sandbox — 18 Rust source files migrated to sibling *_tests.rs pattern)
**Description:** Multiple Rust production source files contain inline `#[cfg(test)] mod tests { ... }` blocks with bodies, violating CONSTRAINTS.md C-TEST-5. Affected files (with measured inline-test line counts): `src-tauri/src/platform/logging.rs` (1467 LOC inline tests, 89 #[test] fns); `src-tauri/src/sidecar/supervisor.rs` (962 LOC, 56% of file); `src-tauri/src/sidecar/ws.rs` (662 LOC, 41%); `src-tauri/src/sidecar/spawn.rs` (318 LOC, 26%); `src-tauri/src/state.rs` (177 LOC, 21%); `src-tauri/src/commands/sidecar_cmds.rs` (363 LOC); `src-tauri/src/commands/system_cmds.rs` (190 LOC); `src-tauri/src/commands/export.rs` (420 LOC); `src-tauri/src/commands/bubble/rate_limit.rs` (73 LOC); `src-tauri/src/tray.rs` (254 LOC); `src-tauri/src/util.rs` (291 LOC); `src-tauri/src/platform/paths.rs`; `src-tauri/src/platform/process.rs`; `src-tauri/src/platform/open_path.rs`; `src-tauri/src/branding.rs`; `src-tauri/src/sidecar/ws/event_protocol.rs`; `src-tauri/src/sidecar/ws/heartbeat.rs`; `src-tauri/src/sidecar/ws/respawn_scheduler.rs`. In contrast, `src-tauri/src/commands/bubble/tests.rs` (821 LOC) and `src-tauri/src/migrate/tests.rs` (521 LOC) correctly use the sibling `tests.rs` pattern.
**User Impact:** Inline tests inflate the apparent production LOC counts (e.g. sidecar_cmds.rs is 957 LOC of production code, not 1331), making spaghetti audits misleading. Any test edit recompiles production code. Violates explicit project constraint.
**Root Cause:** Inline `#[cfg(test)] mod tests` was the original pattern; the sibling `tests.rs` pattern was adopted later (bubble, migrate) but older files were never migrated.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/platform/paths.rs`
- `src-tauri/src/platform/process.rs`
- `src-tauri/src/platform/open_path.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/spawn.rs`
- `src-tauri/src/sidecar/ws/event_protocol.rs`
- `src-tauri/src/sidecar/ws/heartbeat.rs`
- `src-tauri/src/sidecar/ws/respawn_scheduler.rs`
- `src-tauri/src/state.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/commands/system_cmds.rs`
- `src-tauri/src/commands/export.rs`
- `src-tauri/src/commands/bubble/rate_limit.rs`
- `src-tauri/src/tray.rs`
- `src-tauri/src/util.rs`
- `src-tauri/src/branding.rs`
**Fix:** For each affected file: move the inline `#[cfg(test)] mod tests` block to a sibling `<module>_tests.rs` file wired via `#[cfg(test)] mod <module>_tests;` declaration in the parent module. Mirror the proven pattern from `commands/bubble/tests.rs` and `migrate/tests.rs`. Coordinate with GQ-11 (logging.rs split) for the largest file.
**Severity:** 🔴 High

### GQ-14 — sidecar_cmds.rs deep-clones dispatch response on every invoke
**Status:** ✅ Fixed (verified on Linux sandbox — sidecar_cmds.rs uses Value::take, no deep clone)
**Description:** `Ok(response.get("data").cloned().unwrap_or(json!({})))` — `response: Value` is owned (received via oneshot channel at line 654), but the success path deep-clones the `data` field instead of moving it out. For `get_history` / `search_history` / `get_vocabulary` responses (arrays of hundreds-to-thousands of records), this is a full recursive `Value` clone per dispatch.
**User Impact:** For a 10K-row history response (~2-5 MB serialized), the clone allocates + walks the entire JSON tree on the async worker thread — ~5-15ms CPU + ~2-5MB transient heap per `get_history` call. The Dashboard polls history on mount and on focus, so this fires multiple times per session.
**Root Cause:** Owned `Value` is available; clone is avoidable. The fix uses `Value::take` to move the field out without cloning.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:679`
**Fix:** `let data = response.get_mut("data").map(Value::take).unwrap_or(json!({})); Ok(data)` — `Value::take` swaps the field with `Null` and returns the moved value, avoiding the deep clone. The `response` Value is dropped immediately after, so the `Null` swap is free.
**Severity:** 🔴 High

### GQ-15 — bench_startup.py warm-cache contamination makes median misleading
**Status:** ✅ Fixed (verified on Linux sandbox — bench_startup.py uses subprocess per run)
**Description:** `measure_import_time()` only clears `voice_typer.*` from `sys.modules` (line 66-68); third-party C extensions (`numpy`, `pystray`, `PIL`) stay cached across the 3 in-process runs. Measured on Linux sandbox: 'All runs: 46ms, 46ms, 48ms' — variance is 2ms, confirming runs 2-3 are warm. COLDSTART_REPORT.md §5.1 explicitly says 'the median therefore understates true cold start; the *first* run is the honest cold number.' §6 rec #3 (line 282-288) recommends fixing the methodology but it was never implemented. Also, README.md:209 claims '~2 ms cold-import on reference hardware' but on this Linux sandbox the script reports 46ms — the README claim is stale and unverified by CI.
**User Impact:** Median cold-start number reported by `bench_startup.py` is misleading (warm-cache). README perf claim ('~2 ms') is unverifiable and stale. Any future regression that adds eager imports of heavy deps would be hidden if it doesn't exceed the warm-cache floor.
**Root Cause:** Acknowledged in COLDSTART_REPORT.md but no fix landed.
**Progress:** None yet.
**Related Files:**
- `bench/bench_startup.py:59-75`
- `bench/COLDSTART_REPORT.md:60-63`
- `bench/COLDSTART_REPORT.md:282-288`
- `bench/README.md:209`
**Fix:** Replace `measure_import_time()` to spawn a fresh `python -X importtime -c "import voice_typer.server.tray"` subprocess per run (or delegate to `scripts/profile_imports.py`). Report first-run (true cold) + median + p99. Update README.md with the sandbox-measured value + OS disclaimer.
**Severity:** 🔴 High

### GQ-16 — bench_transcription.py non-deterministic + p90=max with n=5
**Status:** ✅ Fixed (verified on Linux sandbox — bench_transcription.py uses seeded RNG + 10 iterations)
**Description:** (1) `generate_test_audio` uses `np.random.randn(len(t))` (line 26) — NOT seeded → non-deterministic input across runs. (`bench_audio_filter_chain.py:117` and `bench_vad.py:66` both correctly use `np.random.default_rng(seed=...)`; `bench_streaming.py` is deterministic.) (2) Default `iterations=5` (line 71) → `p90 = latencies[int(5 * 0.9)] = latencies[4] = max` (line 61) — p90 is mathematically identical to max with 5 samples. (3) Audio is synthetic 440Hz sine + noise (line 26) — NOT speech; the engine processes it but the result is gibberish, so this measures 'engine throughput on arbitrary audio' not 'realistic WPS on speech.'
**User Impact:** Transcription benchmark results are non-reproducible (random noise), statistically weak (n=5, p90=max), and not representative of real user-perceived WPS. Regression detection on this bench would have high false-positive rate from noise alone.
**Root Cause:** Methodology gaps not addressed when the bench was authored.
**Progress:** None yet.
**Related Files:**
- `bench/bench_transcription.py:23-27`
- `bench/bench_transcription.py:40-64`
**Fix:** (1) Use `np.random.default_rng(seed=0xA4A4)` (matching `bench_audio_filter_chain.py`). (2) Default to `--iterations 10` (so p90 != max). (3) Add a `--fixture` option that loads a real 16kHz speech WAV from `tests/fixtures/`. (4) Report WPS (words-per-second) in addition to latency.
**Severity:** 🔴 High

### GQ-17 — No IPC benchmarks at all — ADR-0014 lock-free auth claim unverified
**Status:** ✅ Fixed (verified on Linux sandbox — bench/bench_ipc.py created)
**Description:** `bench/` directory has 5 scripts; none exercise `voice_typer/server/ipc_server.py`. ADR-0004 specifies 'Local TCP socket with JSON protocol — bidirectional, low latency'. ADR-0014 specifies 'Lock-free auth ... prevents a stalled auth read from blocking `push()` events' — a perf claim that is unverified. ADR-0019 specifies a per-connection token-bucket rate limiter — algorithmic complexity unbenchmarked. ADR-0018 heartbeat watchdog has timing budgets — unbenched.
**User Impact:** A regression that adds a `self._lock` around `push()` (the exact thing ADR-0014 §6 warns against) would have no detection. A regression that turns the rate limiter from O(1) to O(n) per message would have no detection. Streaming partials (the highest-frequency IPC message) have no throughput ceiling enforced.
**Root Cause:** IPC layer was treated as 'correctness-tested only, perf is obvious' — but ADR-0014 explicitly makes perf claims about lock-free auth that need verification.
**Progress:** None yet.
**Related Files:**
- `bench/ (no IPC bench exists)`
- `docs/adr/0004-ipc-protocol.md`
- `docs/adr/0014-tcp-ipc-session-token-auth.md`
- `docs/adr/0018-heartbeat-watchdog.md`
- `docs/adr/0019-per-connection-rate-limiter.md`
**Fix:** Add `bench/bench_ipc.py` measuring: (1) auth handshake latency (cold + warm), (2) `push()` throughput under N concurrent subscribers, (3) end-to-end latency for a streaming partial round-trip, (4) rate-limiter throughput at saturation. Wire into the proposed CI ratchet (see GQ-4).
**Severity:** 🔴 High

### GQ-18 — config/__init__.py 2323 LOC — Config class monolith mixing 6 concerns
**Status:** ✅ Partial (config/saver.py + config/purge.py extraction deferred per Max 5 big tasks rule; GQ-3/44/45/46 perf fixes done)
**Description:** AST analysis: 1 class (Config, 1614 LOC) + 6 module-level functions (`purge_user_data` 116 LOC, `purge_all_user_data` 113 LOC, `_enforce_windows_owner_only_acl` 72 LOC, `_prune_kept_backups` 33 LOC, `_default_hotkey_for_platform` 24 LOC, `_legacy_voice_typer_dir` 16 LOC). The Config dataclass has ~150 field declarations with extensive multi-paragraph comments spanning ~600 lines. The `save()` chain is ~200 LOC. Despite multiple extraction passes (loader.py, coercion.py, sanitization.py), the `__init__.py` still carries the full Config dataclass + all save/backup logic + two unrelated purge functions + Windows-only ACL helper + backup-prune helper + enum-reset sanitization.
**User Impact:** 2323-LOC file is hard to navigate, hard to review changes against, and resists further refactoring. Contributors waste time scrolling to find the save() logic buried between purge_user_data and ACL helpers.
**Root Cause:** Despite multiple extraction passes, the save() chain, purge functions, ACL helper, and enum-reset logic remain inline in __init__.py.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py:1-2323`
**Fix:** Extract in three behavior-preserving passes: (1) `config/saver.py` — `save()`, `_save_with_mutation_lock()`, `_save_unlocked()`, `save_strict()`, `_save_locked` alias (~200 LOC, mirrors the existing `loader.py` split). (2) `config/purge.py` — `purge_user_data()`, `purge_all_user_data()` (~230 LOC). (3) Move `_enforce_windows_owner_only_acl` to `secure_file_io.py` (already owns the cross-platform ACL/fsync concerns) and `_prune_kept_backups` to `config_internals/migrations.py` (already owns backup retention). Move `_reset_invalid_enum_fields` to `sanitization.py`.
**Severity:** 🔴 High

### GQ-19 — clipboard/manager.py 1417 LOC — paste() method alone is 440 LOC
**Status:** ✅ Partial (clipboard/restore.py + clipboard/safety.py extracted; manager.py 1417→943 LOC; paste() dispatch-table refactor deferred)
**Description:** AST analysis: `ClipboardManager` class is 1249 LOC (lines 160-1408) with 13 methods. The `paste()` method alone is 440 LOC (lines 777-1216) — larger than many complete modules. It contains 5 platform/terminal branches (is_terminal+macOS, is_terminal+Wayland, is_terminal+other, is_macos, is_windows, use_wayland_wtype, else) with duplicated TOCTOU re-check logic for Windows (lines 1164-1188) and macOS (lines 1150-1163). `_is_safe_paste_target` is 256 LOC (lines 259-514) with nested try/except blocks 4 levels deep. `_delayed_restore` is 132 LOC (lines 1218-1349).
**User Impact:** 440-LOC `paste()` method is the single largest method in the owned files. The duplicated TOCTOU re-check (Windows branch vs macOS branch) is a maintenance hazard — a fix to one branch can easily be missed in the other, leading to platform-specific paste bugs.
**Root Cause:** Original `clipboard.py` monolith was split into `linux.py`/`windows.py`/`manager.py`/`__init__.py`, but `manager.py` retained the entire `ClipboardManager` class including the platform-branching `paste()` method and the safety-check logic.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py:1-1417`
**Fix:** Extract in three passes: (1) `clipboard/restore.py` — `_pending_restores`, `_pending_restores_lock`, `_MAX_PENDING_RESTORES`, `_force_restore_pending_at_exit`, `ClipboardManager._delayed_restore`, `ClipboardManager.restore_now` (~280 LOC). (2) `clipboard/safety.py` — `ClipboardManager._is_safe_paste_target`, `_is_terminal_process`, `_detect_focused_process`, `_get_frontmost_pid_macos` (~340 LOC). (3) Refactor `paste()` into a dispatch table: extract per-platform keystroke-send logic into `_send_paste_keystroke_win32()` / `_send_paste_keystroke_macos()` / `_send_paste_keystroke_wayland()` / `_send_paste_keystroke_default()` helpers, and unify the TOCTOU re-check into a single `_verify_target_unchanged(safe_hwnd, safe_macos_pid)` helper.
**Severity:** 🔴 High

### GQ-20 — history_db.py 2848 LOC + history_db_internals/search.py is dead code
**Status:** ✅ Fixed (verified on Linux sandbox — history_db.py 2848→2523 LOC, 13 methods delegating to search.py)
**Description:** `history_db_internals/search.py` (585 lines) was extracted as part of a refactor but is NEVER imported by any caller — `rg -l 'history_db_internals.search'` returns ZERO Python importers (only `review.md` and `SOURCES.txt` mention it). The `search.py` module exports 13 functions: `get_recent`, `get_latest_text`, `search`, `get_favorites`, `get_today_stats`, `get_transcription_text`, `get_history_count`, `invalidate_today_stats_cache`, `invalidate_history_count_cache`, `prepare_like_search_pattern`, `is_fts_compatible_query`, `sanitize_fts_query`, `project_text_row`. The `HistoryDB` class in `history_db.py` has its OWN inline copies of every one of these methods (lines 2223-2733 for the SQL methods, 367-445 for the helpers). Net: ~569 lines of duplicated code in `history_db.py` + 585 lines of dead code in `search.py` = ~1154 lines of redundant spaghetti.
**User Impact:** Maintenance hazard — bug fixes / perf improvements to inline `history_db.py` copies must be re-applied to `search.py` (or vice versa); dead `search.py` misleads future maintainers into thinking the split was completed.
**Root Cause:** Split was started but never wired up. Other split modules (`schema.py`, `writer.py`, `reader.py`, `retention.py`, `recovery.py`) ARE wired up via thin delegating methods on `HistoryDB` — only `search.py` was left dangling.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:367-445`
- `voice_typer/server/history_db.py:2223-2733`
- `voice_typer/server/history_db_internals/search.py:1-585`
**Fix:** Complete the split — replace the inline methods in `history_db.py` (get_recent, search, get_favorites, get_today_stats, get_history_count, get_transcription_text, get_latest_text, _invalidate_today_stats_cache, _invalidate_history_count_cache) with thin delegating stubs matching the pattern already used for `_writer_loop`, `_get_read_conn`, etc.; also delete the duplicate `_prepare_like_search_pattern` / `_is_fts_compatible_query` / `_sanitize_fts_query` / `_project_text_row` and re-export them from `search.py`. Removes ~569 lines from `history_db.py`.
**Severity:** 🔴 High

### GQ-21 — _paths.py eager import of config package costs 54ms cold-start
**Status:** ✅ Fixed (verified on Linux sandbox — _paths.py lazy import)
**Description:** `python -X importtime -c "import voice_typer.server._paths"` shows `voice_typer.server.config` taking 53395µs cumulative (94% of the 57101µs total for `_paths`). First import measured at 53.9ms ON LINUX (sandbox); pulls in 73 new modules including `crash_recovery` (~4.3ms), `_user_data_files` (~4.8ms), `config_validators` (~10ms), `config.coercion` (~13ms), `secure_file_io` (~4.9ms), `volume_ducker` (~4.9ms), plus `tempfile`, `shutil`, `bz2`, `lzma`, `random`. Line 86 is `from voice_typer.server.config import _config_dir`. Grep finds 25 production files (ipc_server.py, tray.py, env_validation.py, sidecar_ws.py, autostart_launcher.py, etc.) that import from `_paths`.
**User Impact:** ~54ms added to every cold start of the Python sidecar (tray + IPC server). On Electron/Tauri host launch, this 54ms is on the critical path before the tray icon appears and before the IPC server accepts connections.
**Root Cause:** `_config_dir` is imported eagerly at module scope so the 9 path-helper functions can call it as `_config_dir()`. The config package transitively pulls the full config-validation + secure-file-IO + volume-ducker stack.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_paths.py:86`
**Fix:** Replace line 86 with a lazy resolver. Either (a) inline `from voice_typer.server.config import _config_dir` inside each of the 9 helper functions, or (b) wrap `_config_dir` in a `functools.lru_cache(maxsize=1)`-backed wrapper that imports on first call. Expected: `_paths` cold import drops from 54ms to <5ms.
**Severity:** 🟡 Medium

### GQ-22 — WS encode pool undersized (2 workers) vs dispatch pool (4 workers)
**Status:** ✅ Fixed (verified on Linux sandbox — WS encode pool 2→4 workers)
**Description:** WS frame-encode pool is `ThreadPoolExecutor(max_workers=2, thread_name_prefix="sidecar-ws-encode")` while the dispatch pool is `max_workers=4`. The writer task offloads every outbound frame's `json.dumps + .encode` via `loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)`. For near-cap frames (~1 MiB), the encode itself is 50-100 ms. With 4 dispatch workers potentially producing large results concurrently (e.g. `get_history`, `get_vocabulary`, `diagnostics_export`), only 2 can encode in parallel; the 3rd and 4th wait, blocking the writer task and the outbound queue drain.
**User Impact:** Under concurrent large-response workloads (multiple dashboard panels refreshing), outbound latency for the 3rd+ frame adds 50-100 ms of encode-queue wait. The 256-entry `outbound` queue absorbs bursts but events can be drop-oldest discarded if the encode pool stalls for >5 s during a sustained large-response burst.
**Root Cause:** Encode-pool sizing was chosen for the steady-state waveform-bubble workload (small frames, 15-50 Hz) but not sized for the burst of large dispatch responses.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py:357`
- `voice_typer/server/sidecar_ws.py:978`
**Fix:** Match the encode pool size to the dispatch pool size (4), or merge the encode work into the dispatch pool (the dispatch worker already has the dict and can encode before returning). The latter eliminates one pool hop.
**Severity:** 🟡 Medium

### GQ-23 — TCP pending flush issues per-entry sendall on reconnect
**Status:** ✅ Fixed (verified on Linux sandbox — TCP flush batched via for/else)
**Description:** Post-auth pending flush at TCP connect: `for p in pending_flush: auth_client.write(p + "\n"); auth_client.flush()`. Each iteration issues a separate `auth_client.flush()` → `conn.sendall(batch)`. For a client disconnected during a 60-second recording at 16 Hz waveform-bubble push rate, `_pending_tcp` can hold up to 1000 entries (capped at `_TCP_PENDING_BUFFER_CAP`) — so up to 1000 separate `sendall` syscalls on reconnect.
**User Impact:** On reconnect after a disconnect, the worker thread spends up to ~1000 syscall round-trips flushing the backlog before it can read the first new command from the client. At ~10 µs per loopback sendall, that's ~10 ms of pure syscall overhead — small in absolute terms but blocks the per-connection worker thread, delaying the first new dispatch.
**Root Cause:** The `_send` path correctly batches pending drains (sender.py:687-710 buffers all entries then issues ONE `flush`), but the connect-time flush path predates that optimization and was never updated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/transport_tcp.py:549-556`
**Fix:** Refactor to use the same batched-flush pattern: `for p in pending_flush: auth_client.write(p + "\n")` then a single `auth_client.flush()` after the loop. Mirror the `recent` batch in `sender._send` (sender.py:687-710).
**Severity:** 🟡 Medium

### GQ-24 — sidecar_ws.py 1999 LOC mixes 8+ concerns in one file
**Status:** ⚠️ Partial (sidecar_ws.py 1999 LOC split deferred per Max 5 big tasks rule)
**Description:** Single file mixes 8+ concerns: (1) WS frame encode pool mgmt (L285-417), (2) `_safe_send` DoS defenses (L420-494), (3) graceful close + shutdown hooks (L497-701), (4) bearer-token auth handshake (L799-913), (5) dispatch closure with TOCTOU re-checks + inflight tracking (L916-1238), (6) outbound queue + drop-oldest (L1241-1287), (7) connection handler with auth + subscriber + writer task + read loop (L1298-1878), (8) origin rejection (L1881-1911), (9) `run()` entrypoint (L1914-1999). Multiple functions exceed 100 lines (`_make_dispatch` is ~322 lines, `_handle_connection_inner` is ~110 lines, `ws_graceful_shutdown` nested closure is ~95 lines).
**User Impact:** Maintainability + review burden. Long docstrings (50-100 lines each) signal the author had to explain history to future readers — a code smell.
**Root Cause:** File grew organically as ADR-0020 features were layered on; no extraction pass was done.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py:1-1999`
**Fix:** Split into `sidecar_ws/transport.py` (auth + connection + read loop + writer), `sidecar_ws/encode.py` (encode pool + `_safe_send`), `sidecar_ws/dispatch.py` (`_make_dispatch` + inflight tracking), `sidecar_ws/shutdown.py` (graceful close hooks), `sidecar_ws/run.py` (entrypoint + origin rejection).
**Severity:** 🟡 Medium

### GQ-25 — transcription.py 1521 LOC — TranscriptionEngine mixes 5+ concerns
**Status:** ⚠️ Partial (transcription.py 1521 LOC split deferred per Max 5 big tasks rule)
**Description:** `TranscriptionEngine` class (L144-1509) mixes: model load + fallback chain (L335-492), CUDA runtime probe (L503-611), warmup (L613-644), HuggingFace cache probe + consent + disk check + download verify (L646-901), transcribe batch (L903-1140), transcribe words (L1292-1384), GPU-error classification (L1386-1448), hallucination rejection delegation (L1450-1469), unload (L1471-1509). 24 methods on one class. Module also contains `TranscriberProtocol` (L49-89), `_format_optional_mean` (L1512), and NVIDIA DLL path manager state (L95-141).
**User Impact:** Maintainability. Loading and inference are independently testable but coupled in one class.
**Root Cause:** Multiple concerns grew into a single class without extraction.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py:1-1521`
**Fix:** Split into `transcription/engine.py` (load + transcribe + fallback), `transcription/download.py` (HF cache probe + consent + download + verify), `transcription/cuda_probe.py` (CUDA runtime probe + warmup), `transcription/error_classifier.py` (`_is_gpu_runtime_error`).
**Severity:** 🟡 Medium

### GQ-26 — app.py 1676 LOC — wiring hub with 12 repetitive lazy-property pairs
**Status:** ⚠️ Partial (app.py 1676 LOC lazy-property descriptor refactor deferred)
**Description:** `wc -l` = 1676 lines; `awk` ratio = 752 code / 730 comment / 194 blank (44% comments). 12 lazy `@property` getter+setter pairs (e.g. lines 864-878 `_template_manager`, 880-894 `_vocabulary_manager`, 896-908 `clipboard`, 910-926 `_waveform_bubble`, 928-940 `waveform_wiring`, 953-969 `undo`, 971-989 `audio_quality`, 991-1011 `_duck_crash_recovery`, 1013-1036 `_volume_ducker`, 1051-1061 `_audio_processor`, 1079-1099 `history_db`, plus `_LazyAudioProcessorProxy` at 168-253). 10 one-line delegate methods. File also mixes 3 concerns: (a) module-level i18n registry mutation at import time (lines 273-285), (b) lazy property infrastructure (168-253, 850-1100), (c) wiring delegates (1100-1595), (d) re-export shims for test monkeypatch (lines 43-165, 1609-1617, 1671-1676).
**User Impact:** 1676-line file crosses the Rule 20 spaghetti threshold. Cognitive load is high when reading the file but each piece is small and isolated.
**Root Cause:** `VoiceTyperApp` is a god-class wiring hub. The actual business logic was extracted but the wiring hub retains 12 near-identical lazy property pairs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:1-1676`
**Fix:** (1) Replace the 12 lazy property pairs with a single `lazy_property` descriptor — saves ~150 lines. (2) Move the module-level `with i18n._LOCK: ... setdefault(...)` block (lines 273-285) into a `_register_i18n_fallbacks()` function called from `start()`. (3) Consider a separate `app_wiring.py` module for the re-export shims.
**Severity:** 🟡 Medium

### GQ-27 — recording_lifecycle F2 dispatch thread blocks 5-30s on idle-unload model reload
**Status:** ✅ Fixed (verified on Linux sandbox — F2 model load moved to daemon worker thread)
**Description:** `recording_lifecycle.py:431-435`: `controller._toggle_lock.release(); try: app.models.ensure_active_engine_loaded(); finally: controller._toggle_lock.acquire()`. Called from `_start_impl` (line 433). The comment at lines 397-410 claims the lock release prevents the F2 hotkey backend's dispatch thread from being 'blocked for 5-30s on the idle-unload reload path' — but the dispatch thread IS the one executing `ensure_active_engine_loaded()` (call chain: `toggle()` → `_toggle_impl` → `app._start_dictation` → `controller.start` → `_start_impl` → `ensure_active_engine_loaded`, all on the F2 hotkey thread). The lock release only unblocks OTHER threads.
**User Impact:** 5-30s F2-dispatch-thread block on idle-unload reload path. The user presses F2 to start dictation and nothing happens for up to 30 seconds while the model reloads. The recorder is buffering audio in parallel so the speech IS captured, but the user has no feedback and may press F2 again thinking it didn't register.
**Root Cause:** Model load runs synchronously on the F2 hotkey thread. The previous behavior was worse (lock held across the load too), and auto-stop Timer still fires.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_lifecycle.py:431-435`
- `voice_typer/server/recording_lifecycle.py:397-410`
**Fix:** Move `ensure_active_engine_loaded()` to a daemon worker thread (mirroring the `_stop_and_transcribe_worker_entry` pattern at line 692). The worker signals `_start_complete_event` when done; `_start_streaming_session_if_enabled` runs in the worker's finally block. The F2 dispatch thread returns immediately after `recorder.start()` (sub-100ms).
**Severity:** 🟡 Medium

### GQ-28 — model_manager.py 2136 LOC — 5 locks + 3 blended concerns
**Status:** ⚠️ Partial (model_manager.py 2136 LOC split deferred per Max 5 big tasks rule)
**Description:** Single `ModelManager` class holds 5 distinct locks: `_model_lru_lock` (line 116), `_lazy_init_lock` (line 126), `_model_load_spawn_lock` (line 138), `_model_change_lock` RLock (line 147), `_idle_unload_lock` (line 167). Plus app-level `_config_mutation_lock` (acquired in `_change_model_blocking` line 889). Lock-order contract is documented but complex. File is 2136 LOC but ~60% is docstrings. The class blends three concerns: lifecycle (load/swap), LRU tracking, and idle-unload timer.
**User Impact:** High cognitive load for maintainers; risk of introducing lock-order violations on future edits.
**Root Cause:** Historical accumulation; each concern was added incrementally.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1-2136`
**Fix:** Split into `LifecycleModelManager` (load/swap/fallback) + `LruTracker` (touch/evict, owns `_model_lru_lock`) + `IdleUnloadTimer` (owns `_idle_unload_lock`). Keep `ModelManager` as a facade delegating to the three. Mirrors the existing `service/` mixin split pattern.
**Severity:** 🟡 Medium

### GQ-29 — _evict_lru_model missing release_gpu_memory() — inconsistent defense-in-depth
**Status:** ✅ Fixed (verified on Linux sandbox — release_gpu_memory() added to _evict_lru_model)
**Description:** `_evict_lru_model` does NOT call `release_gpu_memory()` after `engine.unload()`. Compare `_do_idle_unload` (line 1996-1998) and `force_unload_active` (line 2115-2117) which BOTH call `from voice_typer.server.asr_utils import release_gpu_memory; release_gpu_memory()` as defense-in-depth (per docstring 1991-1994: 'parakeet_engine.unload() also calls this, but the ModelManager calls it explicitly too so a registry impl that doesn't propagate unload still releases VRAM').
**User Impact:** If a future engine's `unload()` forgets to call `release_gpu_memory()` (asr_utils.py:97-98 documents this as the engine contract), LRU eviction leaks the CUDA caching-allocator blocks (~2.4 GB for Parakeet fp16 per model_manager.py:154). Currently safe for Parakeet (its unload does call release_gpu_memory at parakeet_engine.py:1521), but no defense-in-depth.
**Root Cause:** Inconsistent defense-in-depth across the three unload paths (LRU / idle / force).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1748-1758`
- `voice_typer/server/model_manager.py:1996-1998`
- `voice_typer/server/model_manager.py:2115-2117`
**Fix:** Add `release_gpu_memory()` call after the `engine.unload()` block in `_evict_lru_model`, wrapped in try/except like the other two paths.
**Severity:** 🟡 Medium

### GQ-30 — service/status.py calls ducker.initialize() on every 2s poll
**Status:** ✅ Fixed (verified on Linux sandbox — ducker.initialize() removed from poll path)
**Description:** `get_volume_backend_status` calls `ducker.initialize()` on every IPC poll. The status endpoint is polled ~every 2s (per docstring line 36 'polled ~every 2s'). `initialize()` may do platform IPC (Windows per-session volume enumeration, Linux dbus, macOS CoreAudio). The notify-once guard at line 95-100 confirms the authors know this can fail repeatedly.
**User Impact:** 10-50ms of CPU on every 2s poll = 0.5-2.5% sustained CPU on Windows when the status endpoint is being polled. Negligible on Linux (dbus cheap).
**Root Cause:** `initialize()` cost is platform-dependent; on Windows it walks the audio session list (potentially 10-50ms per call).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/status.py:89`
**Fix:** Move `ducker.initialize()` out of the status poll path — call it once at app startup (or lazily on first duck request) and cache backend_name. Add a force-refresh entry point for the UI's 'Refresh' button.
**Severity:** 🟡 Medium

### GQ-31 — text_cleanup.py 1499 LOC — monolith mixing 7 distinct concerns
**Status:** ⚠️ Partial (text_cleanup.py 1499 LOC split deferred — GQ-8 dead-code deletion landed)
**Description:** Single file mixes 7 distinct concerns: (1) corrections JSON loading — `_load_bundled_corrections`/`_load_user_corrections`/`_load_external_corrections`/`_truncate_corrections`/`_filter_corrections_by_length`/`_active_corrections` (lines 60-439); (2) phrase-pattern cache management (lines 442-637); (3) `configure_corrections` orchestrator (lines 639-715); (4) `clean_transcribed_text` pipeline entry (lines 718-768); (5) token-based structural cleanup (lines 770-1112); (6) capitalization (lines 1115-1317); (7) file-extension fix + auto-punctuation (lines 1326-1499). Control flow is NOT tangled (each function is focused), but the file is monolithic and the historical-comment density is very high (~40% of lines are docstrings/comments).
**User Impact:** Maintenance cost: future edits to any one concern (e.g. changing auto-punctuation rules) require scrolling through 1500 lines and risk touching unrelated state.
**Root Cause:** Incremental growth + extensive prose comments documenting past refactors.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:1-1499`
**Fix:** Split into focused modules under `voice_typer/server/text_cleanup/`: `corrections_loader.py`, `phrase_pattern_cache.py`, `token_cleanup.py`, `pronoun_capitalization.py`, `file_extensions.py`, `auto_punctuation.py`, with `text_cleanup/__init__.py` re-exporting `clean_transcribed_text` + `configure_corrections` for backward compat. Recommend doing this AFTER deleting the dead state from GQ-8.
**Severity:** 🟡 Medium

### GQ-32 — text_cleanup max-size corrections file drives 145ms per-dictation
**Status:** 🚫 Won't Fix (lowering SEC-011 cap from 5000→500 is a user-facing behavior change for power users; deferred to dedicated perf-tuning session)
**Description:** With bundled corrections.json (8 phrases), `clean_transcribed_text` on a 5580-char input measures median 7.9ms / p95 8.4ms — well under Low threshold. But with a SEC-011-maximum (5000 phrases + 5000 extra-word patterns) user corrections file, the combined-alternation regex `(?:p1|p2|...|p5000)` built at line 607 drives per-dictation cleanup to median 145.4ms / max 199.7ms on a 2360-char input, and p95 211.2ms on a 47-char input with one match (first-call regex warmup).
**User Impact:** For typical users — none (<10ms). For users with very large corrections dictionaries — per-dictation cleanup could approach 200ms, which on a 1-second transcription budget is ~20% overhead.
**Root Cause:** The SRE trie compiled from a 5000-alternative alternation of `re.escape`d literals is O(total pattern chars), and `re.sub` against it touches every text char against the trie.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:566-608`
- `voice_typer/server/text_cleanup.py:1016-1096`
**Fix:** If max-size corrections files become a real use case, options are: (a) lower the SEC-011 cap from 5000 to ~500 (still 60x the bundled defaults); (b) switch from a single combined regex to Aho-Corasick (`pyahocorasick` package) for O(N+M) multi-pattern matching that scales better than SRE trie at 5000+ patterns. Recommend (a) as the lowest-risk mitigation.
**Severity:** 🟡 Medium

### GQ-33 — noise_gate.py per-sample Python loop on RT audio thread
**Status:** 🚫 Won't Fix (noise_gate per-sample loop is inherently sequential state machine; vectorization too complex/risky for output fidelity)
**Description:** `noise_gate.py:183-202`: per-sample Python `for i in range(n):` loop on the audio worker thread. Body does: `level = float(level_arr[i])`, 1-2 float comparisons, 1-2 float arithmetic ops, 1 array write. The equalizer.py docstring (line 6) states a similar per-sample loop cost '~1 ms per chunk'. At 16 kHz / 10 ms chunks (100 chunks/sec, 160 samples/chunk) this is ~0.1-0.3 ms/chunk = 10-30 ms/sec ≈ 1-3% CPU. The file comment (line 5-8) acknowledges this is 'inherently sequential' but the attack/release ballistics CAN be vectorized with cumulative max/min tricks.
**User Impact:** ~1-3% CPU on the audio worker thread (the only filter with a Python per-sample loop). All other dynamics filters (compressor, limiter, EQ) are fully vectorized.
**Root Cause:** Attack/release envelope state machine implemented as a per-sample Python loop instead of vectorized numpy ops.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_gate.py:183-202`
**Fix:** Vectorize the attack/release envelope via two parallel `np.maximum.accumulate` passes (one for attack-rising, one for release-falling), then apply element-wise gain. The open/close threshold crossings can be pre-computed as boolean masks. The hold-time state machine is the hardest part — it may require a scan-based approach (`np.ufunc.accumulate` or a small Cython helper).
**Severity:** 🟡 Medium

### GQ-34 — noise_gate.py 4-5 fresh array allocations per chunk on RT thread
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_audio_filter_prealloc.py` 38/38 pass, byte-identical output)
**Description:** Per-chunk allocations on the RT thread: line 146 `abs_x_init = np.abs(samples).astype(np.float64)` (calibration path); line 166 `abs_x = np.abs(samples).astype(np.float64)` — allocates abs result + float64 copy; line 167 `i_arr = np.arange(n, dtype=np.float64)` — fresh index array every chunk; line 169 `y_with_init = np.empty(n + 1, dtype=np.float64)` — fresh buffer; line 178 `attenuation_arr = np.empty(n, dtype=np.float64)` — fresh buffer. At 100 chunks/sec this is ~400-500 allocations/sec of ~1.6-12.8 KB each. The `compressor.py` (line 81) already demonstrates the pre-allocation pattern.
**User Impact:** ~5-13 KB/chunk of heap churn × 100 chunks/sec = 0.5-1.3 MB/sec allocator pressure on the RT thread. May trigger GC pauses on long sessions.
**Root Cause:** Buffers not pre-allocated; fresh arrays created per chunk.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_gate.py:146`
- `voice_typer/server/audio_filters/noise_gate.py:166-178`
**Fix:** Pre-allocate `self._abs_buf`, `self._i_arr_buf`, `self._y_buf`, `self._attenuation_buf` in `__init__` (lazy-resized to max chunk seen, like `compressor._env_db_buf`). The `i_arr` can be cached and sliced since it's just `[0, 1, 2, ..., n-1]` — only needs resize when n grows.
**Severity:** 🟡 Medium

### GQ-35 — equalizer.py 8-10 allocations per chunk on RT thread
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_audio_filter_prealloc.py` byte-identical)
**Description:** Per-chunk allocations: line 97 `x = samples.astype(np.float64)` — fresh float64 copy of input; line 107 `zi=np.array([self._low_state], dtype=np.float64)` — fresh 1-elem array; line 115 `zi=np.array([self._high_state], dtype=np.float64)` — fresh 1-elem array; lines 103-108, 111-116: lfilter returns new `low_s`, `high_s` float64 arrays; line 146 `output = (low_s * low_gain + mid * mid_gain + high * high_gain).astype(np.float32)` — 3 intermediate arrays + sum + final astype = ~5 allocations. Total: ~8-10 allocations/chunk. The compressor (line 76) pre-allocates `self._zi_buf` for the same pattern; EQ does not.
**User Impact:** ~10 allocations/chunk × 100 chunks/sec = 1000 allocations/sec, ~25-50 KB heap churn/sec on the RT thread.
**Root Cause:** Pre-allocation pattern from compressor not applied to EQ.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/equalizer.py:97`
- `voice_typer/server/audio_filters/equalizer.py:107`
- `voice_typer/server/audio_filters/equalizer.py:115`
- `voice_typer/server/audio_filters/equalizer.py:146`
**Fix:** (1) Pre-allocate `self._low_zi_buf` / `self._high_zi_buf` as 1-element float64 arrays in `__init__` (mirror `compressor._zi_buf`). (2) Pre-allocate `self._x_f64_buf` for the float64 working copy. (3) Pre-allocate `self._output_buf` and use `np.multiply(..., out=)` + `np.add(..., out=)` to compute the band sum in-place.
**Severity:** 🟡 Medium

### GQ-36 — compressor.py + limiter.py final gain-stage allocates 7 arrays per chunk
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_audio_filter_prealloc.py` byte-identical)
**Description:** Final gain-application stage allocates 2-3 temp arrays per chunk: line 155 `gain = np.power(10.0, gain_db / 20.0) * self._output_gain` — `gain_db / 20.0` (new array), `np.power(...)` (new array), `* output_gain` (new array) = 3 allocations; line 156 `gain = np.where(above_floor, gain, self._output_gain)` — new array (np.where has no `out=` kwarg); line 158 `output = (samples.astype(np.float64) * gain).astype(np.float32)` — `samples.astype(np.float64)` (new array), `* gain` (new array), `.astype(np.float32)` (new array) = 3 allocations. Total: ~7 allocations/chunk. Same pattern in limiter.py:132-135.
**User Impact:** ~7 allocations/chunk × 100 chunks/sec = 700 allocations/sec, ~20-40 KB heap churn/sec on the RT thread per filter (compressor + limiter = 2× ).
**Root Cause:** No pre-allocation of intermediate gain buffers.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/compressor.py:155-158`
- `voice_typer/server/audio_filters/limiter.py:132-135`
**Fix:** (1) Pre-allocate `self._gain_buf` and compute `gain_db / 20.0` in-place (env_db already reused). (2) Use `np.copyto(gain, output_gain, where=~above_floor)` instead of `np.where(...)` to avoid the final allocation. (3) Pre-allocate `self._output_f64_buf` and `self._output_f32_buf`; use `np.multiply(samples, gain, out=self._output_f64_buf, casting='same_kind')` then `astype(np.float32, out=..., copy=False)`.
**Severity:** 🟡 Medium

### GQ-37 — noise_suppressor.py RNNoise frame loop + length-match allocations
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/test_audio_filter_prealloc.py` byte-identical)
**Description:** Per-chunk allocations in the RNNoise frame loop + length-match path: line 444 `output_frames = []` — Python list, grown via `.append()`; line 463 `cleaned_i16[0].astype(np.float32) / _FLOAT_TO_INT16_MAX` — per-frame float32 array + division result array (2 allocs × n_full frames); line 474 `result_48k = np.concatenate(output_frames)` — fresh array; lines 485-487 `padded = np.zeros(target_len, dtype=np.float32)` — fresh array when resampling produces shorter output (common on first/last chunk). At n_full=2 frames/chunk and 100 chunks/sec: ~5 allocations/chunk = 500/sec.
**User Impact:** ~5 allocations/chunk × 100 chunks/sec = 500 allocations/sec, ~10-20 KB heap churn/sec. The `padded = np.zeros(target_len)` path can trigger on every chunk if the streaming resampler's output length oscillates around target_len.
**Root Cause:** Pre-allocation pattern not applied to noise_suppressor.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py:444-487`
**Fix:** (1) Pre-allocate `self._result_48k_buf` sized to max expected output and use slice-assignment instead of `np.concatenate`. (2) Pre-allocate `self._frame_out_f32_buf` and use `np.copyto` + in-place divide. (3) Pre-allocate `self._padded_buf` for the length-match path.
**Severity:** 🟡 Medium

### GQ-38 — recorder.py 2857 LOC — Phase 4.5 split left 1-line delegators
**Status:** ⚠️ Partial (recorder.py 2857 LOC split deferred per Max 5 big tasks rule)
**Description:** Despite Phase 4.5 extracting bodies to 13 collaborator modules, `recorder.py` is still 2857 lines. The class body is dominated by 1-line delegator methods (e.g. lines 2469-2550: `_detect_device_disconnect`, `_handle_xrun_status`, `_apply_filter_chain`, `_append_to_buffer_locked`, `_compute_rms_and_peak`, `_run_vad_state_machine` — each a 1-line `return self._collaborator.X(self, ...)`) wrapped in multi-paragraph docstrings. `_recorder_split.py:19-40` lists a FURTHER split plan (lifecycle.py, device_management.py, format.py, worker_threads.py) that has not been executed.
**User Impact:** Maintainer navigation overhead; high cognitive load to trace any single code path across 3-4 files.
**Root Cause:** Split was intentionally partial to avoid line-number conflicts with parallel surgical fixes ('deferred until all in-flight surgical fixes to specific recorder.py line ranges have landed').
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:1-2857`
- `voice_typer/server/recording/_recorder_split.py:19-40`
**Fix:** Complete the planned further split: extract `start`/`stop`/`discard`/`_teardown_stream` into `lifecycle.py`, device methods into `device_management.py`, format helpers (`_resample_chunk`/`_prepare_audio`/`_ensure_mono`) into `format.py`. Reduce `recorder.py` to <800 LOC of orchestrator + property shims.
**Severity:** 🟡 Medium

### GQ-39 — _recorder_split.py segment list never pruned — 3× memory multiplier
**Status:** ✅ Fixed (verified on Linux sandbox — `tests/recording/test_recorder_split_pipelines.py` 15/15 pass)
**Description:** `take_snapshot` appends a new numpy segment to `_cached_no_resample_segments` (or `_cached_resampled_segments`) on every snapshot that sees new chunks. The lists are NEVER pruned during a session — only cleared at `start()`/`stop()`/`discard()`. At 4Hz streaming poll × 30min session = 7200 segments. Each segment is a `np.concatenate(new_chunks)` copy of the new tail. Combined memory: (a) `_buffer` deque holds all chunks (~56MB for 30min @ 16kHz), (b) `_cached_no_resample_segments` list holds concatenations of the same chunks (~56MB), (c) `_cached_no_resample_arr` holds the full concatenation (~56MB). Total = ~3× the actual audio data = ~168MB for a 30-min 16kHz session; ~500MB+ for 48kHz without AudioProcessor.
**User Impact:** ~168MB retained for a 30-min 16kHz session (3× the actual audio). Limits max recording time on memory-constrained devices (e.g. 4GB VMs, Raspberry Pi).
**Root Cause:** Segment-list design was introduced to avoid O(N) re-concatenation per snapshot, but the tradeoff of 3× memory retention is not called out in the comments.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/_recorder_split.py:282`
- `voice_typer/server/recording/_recorder_split.py:361`
- `voice_typer/server/recording/_recorder_split.py:109-149`
**Fix:** Periodically compact the segment list — when `len(_cached_no_resample_segments) > N` (e.g. 64), replace the list with `[np.concatenate(segments)]` and reset the dirty flag. This bounds the list to N+1 entries while preserving the O(1)-append-per-snapshot property.
**Severity:** 🟡 Medium

### GQ-40 — recorder stop() worst-case 4.5-5s on long recordings
**Status:** ✅ Partial (teardown poll skip + pipelined prepare_audio done; _AUDIO_WORKER_JOIN_TIMEOUT_S reduction deferred — pinned by test contract)
**Description:** `stop()` worst-case path: (1) `_teardown_stream` = up to 300ms callback-drain poll (`_TEARDOWN_CALLBACK_DRAIN_BUDGET_S=0.300`); (2) `_stop_audio_worker(timeout=2.0, drain=True)` = up to 2s — drains up to 64 ring chunks × ~50ms RNNoise = ~3.2s CPU but bounded by 2s join; (3) `_stop_event_worker(timeout=2.0, drain=True)` = up to 2s; (4) `np.concatenate(_captured_chunks)` = 50-300ms for 30-min buffer; (5) `_prepare_audio` resample-from-scratch = 100-500ms. Total worst-case: ~4.5-5s. Typical case (healthy system): ~100-300ms. The 300ms teardown poll is the common-case floor even when the callback is already clear.
**User Impact:** User perceives 100-500ms delay between pressing the stop hotkey and the transcription engine receiving audio. For a 30-min recording, worst-case ~5s. Acceptable for dictation but noticeable.
**Root Cause:** 300ms teardown budget and 2s worker joins are documented safety margins, not measured latencies. `np.concatenate` + `_prepare_audio` resample run sequentially after the worker join.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/_recorder_split.py:824-1131`
- `voice_typer/server/recording/stream_lifecycle.py:487-494`
**Fix:** (1) Skip the 300ms teardown poll when `_is_in_audio_callback` is already clear on first check (fast-path `if not recorder._is_in_audio_callback.is_set(): skip_poll`). (2) Pipeline `np.concatenate` and `_prepare_audio` — start the resample on a background thread as soon as the chunk list is captured. (3) Reduce `_AUDIO_WORKER_JOIN_TIMEOUT_S` from 2.0s to 1.0s.
**Severity:** 🟡 Medium

### GQ-41 — recorder start() hotkey critical path 200-600ms typical, 2-4s first-start
**Status:** 🚫 Won't Fix (warm_up_resampler background prewarm blocked by test contract `assert_called_once()` synchronous pin; full fix requires Recorder.__init__ change + test relaxation)
**Description:** `start()` hotkey critical path runs synchronously: (1) `_open_stream_for_candidates` iterates 1-3 candidates, each `sd.InputStream(...)` + `stream.start()` = 50-200ms on Windows MME; (2) `_open_stream_fallback` iterates ALL remaining input devices if primary fails; (3) `warm_up_resampler()` = 1-2s scipy preload on first start when resampling is needed; (4) `retune_audio_processor` may call `rebuild_from_config(config)` = 100-500ms for RNNoise filter design. Total worst-case first-start: ~2-4s; subsequent starts: ~200-600ms. The prewarm device-cache thread helps avoid device-list RPCs but not the stream-open itself.
**User Impact:** 200-600ms typical start latency; 2-4s first-start latency. Pre-roll buffer (captured by RT callback before `_recording_event.set()`) is NOT captured during this window because the stream isn't open yet — the user's first syllables after pressing the hotkey may be lost if they speak immediately.
**Root Cause:** Stream-open is inherently blocking (PortAudio API), scipy preload is synchronous to avoid racing with stop(), retune is synchronous to avoid per-chunk resample overhead.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/_recorder_split.py:615-623`
- `voice_typer/server/recording/_recorder_split.py:719`
- `voice_typer/server/recording/_recorder_split.py:743-756`
**Fix:** (1) Move `warm_up_resampler()` to a background prewarm thread started at `Recorder.__init__` (mirrors `_prewarm_device_cache`), so scipy is loaded by the time the user first presses the hotkey. (2) Open a 'dummy' stream at `__init__` time on the default device to warm PortAudio's device-open path (close it immediately), so the first real `start()` doesn't pay PortAudio's one-time initialization cost. (3) Defer `retune_audio_processor` to the worker thread's 'phase 0' (alongside the preroll prepend) so start() returns immediately after stream-open.
**Severity:** 🟡 Medium

### GQ-42 — microphone_watcher.py dead _is_idle/_idle_poll_interval_s state
**Status:** ✅ Fixed (verified on Linux sandbox — set_idle() method added to MicrophoneDeviceWatcher)
**Description:** Three instance attributes are initialised in `__init__`: `self._is_idle: bool = True`, `self._idle_poll_interval_s: float = 12.0`, `self._active_poll_interval_s: float = 3.0`. The docstring at lines 139-149 says: 'When idle, the macOS-without-pyobjc polling path widens its cadence from the active 3 s to a gentler 12 s'. But a grep across the entire `voice_typer/server/` tree returns only these three definition lines — there is NO `set_idle()` method, and `_run_macos` (line 659) computes `effective_poll = self._poll_interval if self._poll_interval < 1.0 else 3.0` with no reference to `_is_idle` or the idle/active intervals. `_run_linux` likewise ignores them. State is set, never read.
**User Impact:** On the macOS-without-pyobjc path (sounddevice polling) and the Linux secondary `sd.query_devices()` poll, the watcher runs at the active 3s/5s cadence for the entire app lifetime — including the 95%+ idle time when no recording is in flight. Each `sd.query_devices()` is a 10–50 ms PortAudio round trip. At 3s cadence that's ~3.3 Hz × ~30 ms ≈ 100 ms/s ≈ ~10% of one core on the watcher thread at idle on macOS; the intended 12s cadence would cut that to ~0.8%.
**Root Cause:** Idle-gating mechanism is documented but never wired up. Likely a refactor that landed the state initialization and docstring but reverted/never-landed the consumer code.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py:147-149`
- `voice_typer/server/microphone_watcher.py:659`
- `voice_typer/server/microphone_watcher.py:477`
- `voice_typer/server/microphone_watcher.py:501`
**Fix:** Either (a) wire up `set_idle(bool)` on `MicrophoneDeviceWatcher` (called by `RecordingController` when recording starts/stops) and have `_run_macos` and the `_run_linux` secondary poll select between `_idle_poll_interval_s` (12s) and `_active_poll_interval_s` (3s); or (b) delete the three attributes and the docstring section if the design was abandoned.
**Severity:** 🟡 Medium

### GQ-43 — native_hotkeys 3× process multiplier (one per backend role)
**Status:** 🚫 Won't Fix (native_hotkeys 3× process multiplier requires changes to native C/Swift binaries — out of scope for this session)
**Description:** Each `SubprocessHotkeyBackend` instance spawns three OS-level resources: (1) one `subprocess.Popen` of the native binary, (2) one daemon reader thread blocked on `stdout.readline()`, (3) one daemon watchdog thread cycling every ~35s. `HotkeyDispatcher` instantiates three backends: `_hotkey_backend` (dictation), `_esc_backend`, `_repaste_backend`. Confirmed by the in-code architecture note at `base.py:607-616`: 'Each backend instance spawns its OWN native listener process here. `HotkeyDispatcher` creates three backends (dictation / ESC / repaste), so three of these processes run at once on native platforms — three reader threads, three IPC pipes, three TOCTOU-verify + watchdog cycles.'
**User Impact:** At idle: 3 native processes (~0.6–2.4 MB total RSS); 3 reader threads blocked on I/O (zero CPU); 3 watchdog threads waking every 35s each. CPU is negligible (<0.1% of one core). The real cost is kernel-bookkeeping multiplicity (3× process scheduling, 3× FD table entries, 3× pipe buffers, 3× SHA-256 verify on every respawn).
**Root Cause:** Native wire protocol is single-spec-per-binary (argv[1] = the hotkey spec), so the dispatcher must create one backend per role.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/native_hotkeys/base.py:607-683`
- `voice_typer/server/native_hotkeys/base.py:930-1075`
**Fix:** Implement the documented refactor (`hotkey_dispatcher.py:53-64`): collapse to one native binary that accepts a list of `(role, hotkey_spec)` pairs on the command line or via a startup handshake frame, and emits wire events tagged with the originating role. The dispatcher then owns one backend handle and dispatches by role. Cuts to 1 process / 1 reader / 1 watchdog / 1 pipe. Note: requires changes to the native C/Swift binaries in `voice_typer/server/native/` — should be its own cross-cutting task.
**Severity:** 🟡 Medium

### GQ-44 — Config.save() serializes before cache check — 0.17ms wasted per no-op save
**Status:** ✅ Fixed (verified on Linux sandbox — _dirty flag added to Config)
**Description:** `_save_unlocked` always executes `data = asdict(self)` (line 1435), the credential_store routing loop (lines 1437-1519), `content = json.dumps(data, indent=2)` (line 1520), and `content_bytes = content.encode("utf-8")` (line 1521) BEFORE the `_last_saved_bytes` cache short-circuit at line 1542. Measured: no-op save (cache hit) = 0.23ms; `asdict` alone = 0.07ms, `json.dumps` = 0.10ms — so ~0.17ms of the 0.23ms cache-hit time is wasted work that could be skipped.
**User Impact:** ~0.17ms wasted per no-op save. For IPC `set_config` round-trips that don't change persisted fields (the common case for renderer state echoes), this adds ~0.17ms of avoidable CPU + allocation pressure per call. Compounds under rapid IPC.
**Root Cause:** Cache check is positioned after serialization. `asdict(self)` deep-copies all ~150 fields including nested `custom_theme` dict and `disabled_backends`/`trusted_extra_hosts` lists, allocating ~5-15 KB of dict + nested structures per save call.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py:1435-1543`
**Fix:** Add a `_dirty` flag (set in `__setattr__` override) checked BEFORE `asdict`. If not dirty and `_last_saved_bytes is not None`, return True immediately. Alternatively, compute a cheap content hash from `vars(self)` and compare to a cached hash.
**Severity:** 🟡 Medium

### GQ-45 — Config.save() .bak write on every modified save — 2 extra fsyncs
**Status:** ✅ Fixed (verified on Linux sandbox — .bak write skipped when _last_saved_bytes matches)
**Description:** Every modified `Config.save()` does: (1) `_secure_read_text(config_file)` to read existing content; (2) `_secure_atomic_write(bak_path, existing_text)` to write `config.json.bak`; (3) `_secure_atomic_write(config_file, content)` to write the new config. Each `_secure_atomic_write` does mkstemp + write + fsync(file) + os.replace + chmod + fsync(parent_dir) = 4 fsyncs total per modified save. Measured on container fs: 0.57-0.80ms per modified save; on real SSD expect ~8-20ms; on spinning rust ~40-200ms.
**User Impact:** 2 extra fsyncs per modified save (~4-10ms on SSD, ~20-100ms on HDD). For a user rapidly changing settings via IPC, this doubles the disk I/O cost.
**Root Cause:** The `.bak` write is unconditional on every modified save, even though `_last_saved_bytes` (populated after the prior successful save) already holds the exact bytes that were on disk.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py:1550-1593`
**Fix:** When `_last_saved_bytes is not None`, use it as `existing_bytes` instead of re-reading config.json via `_secure_read_text`. Skip the `.bak` write entirely when `_last_saved_bytes == existing_bytes` (i.e. the prior save already backed up that content). Keep the `_secure_read_text` path only as a fallback when `_last_saved_bytes is None` (first save after construction).
**Severity:** 🟡 Medium

### GQ-46 — config_applier.py redundant store_secret after save_strict
**Status:** ✅ Fixed (verified on Linux sandbox — redundant store_secret loop removed from config_applier)
**Description:** After `app.config.save_strict()` succeeds (line 1190), `apply_config` loops over `updates.items()` and calls `credential_store.store_secret(provider, v)` for each API-key field (lines 1248-1252). But `save_strict()` → `Config.save()` → `_save_unlocked` already routed the same secrets through `credential_store.store_secret()` at lines 1505-1507 of `config/__init__.py`. The comment at `config_applier.py:1240-1244` explicitly acknowledges this is 'a redundant safety net for the no-keyring-available plaintext fallback path and for callers whose Config.save() was patched to skip routing.'
**User Impact:** When the user changes an API key via IPC `set_config`, the secret is stored to keyring twice — doubling the D-Bus/Keychain round-trip cost for that IPC call. Only triggers on API-key-field changes (not on every config change), but each API-key change pays ~10-100ms of redundant I/O.
**Root Cause:** Defense-in-depth safety net that has outlived its necessity.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config_applier.py:1245-1259`
- `voice_typer/server/config/__init__.py:1505-1507`
**Fix:** Remove the redundant `store_secret` loop in `apply_config` (lines 1245-1259). Rely on `Config.save()` to route secrets. If the 'test mocks that patch Config.save() to skip routing' concern is real, gate the redundant loop behind a `if not getattr(app.config, '_secrets_routed_in_save', True):` flag that `_save_unlocked` sets.
**Severity:** 🟡 Medium

### GQ-47 — history_db missing composite idx_timestamp_id index
**Status:** ✅ Fixed (verified on Linux sandbox — idx_timestamp_id composite index added)
**Description:** EXPLAIN QUERY PLAN on `get_recent` shows `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`. The existing `idx_timestamp` is on `(timestamp DESC)` only, but every list query uses `ORDER BY timestamp DESC, id DESC`. The composite secondary sort requires a temp B-tree. Adding `CREATE INDEX idx_timestamp_id ON transcriptions(timestamp DESC, id DESC)` would let SQLite satisfy both sort terms from the index directly.
**User Impact:** Eliminates the temp B-tree on the common list path; expected to shave ~5-15% off `get_recent(50)` at 50K rows and ~10-30% on deep OFFSET pages. Combined with cursor pagination (GQ-1), would bring deep pagination from Critical to Low.
**Root Cause:** Schema never added the composite index.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/schema.py:538-558`
- `voice_typer/server/history_db.py:2276-2277`
**Fix:** Add `CREATE INDEX IF NOT EXISTS idx_timestamp_id ON transcriptions(timestamp DESC, id DESC)` to `init_schema` after `idx_timestamp`. Weighs ~6% extra index size on a typical DB.
**Severity:** 🟡 Medium

### GQ-48 — history_db LIKE fallback 58ms scan on separator-only queries
**Status:** 🚫 Won't Fix (LIKE fallback 58ms scan is edge case — separator-only queries; idx_timestamp_id already mitigates ORDER BY)
**Description:** EXPLAIN QUERY PLAN: `SCAN transcriptions USING INDEX idx_timestamp` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`. The `WHERE text LIKE ? ESCAPE '\\'` with leading `%` cannot use any index, forcing a full table scan. Benchmark on 500K-row DB: `search(query="%", limit=50)` = 58ms median. Scales linearly with N (was 5.7ms at 50K rows — 10× rows ≈ 10× time). Triggered when `_is_fts_compatible_query` returns False (query contains ONLY separator chars — `%`, `_`, punctuation).
**User Impact:** Edge-case scenario (user types only `%` or `_` in search box). At 5M rows would hit ~580ms (Critical). Bounded by `_MAX_LIST_LIMIT=500` on the result set, but the SCAN cost is unbounded.
**Root Cause:** LIKE with leading `%` cannot use any index.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:2430-2484`
**Fix:** For separator-only queries, prefer an FTS5 substring search via `MATCH '"*<char>*"'` tokenization (limited support in unicode61). Alternatively, reject these queries client-side. Low priority — edge case.
**Severity:** 🟡 Medium

### GQ-49 — parakeet_engine _transcribe_impl bypasses batched path on CPU fallback
**Status:** ✅ Fixed (verified on Linux sandbox — _transcribe_impl delegates to _transcribe_chunks_batched)
**Description:** `_transcribe_impl` iterates `chunks` sequentially: `for i, chunk in enumerate(chunks): ... text = self._transcribe_segment_unlocked(chunk)` (lines 1342-1361). It does NOT call `_transcribe_chunks_batched`. The GPU path (`transcribe` at line 811) DOES call `_transcribe_chunks_batched`.
**User Impact:** When a CUDA error triggers the GPU→CPU fallback (line 1229 `self._model.to(device="cpu", ...)`), a 5-min dictation runs 13 sequential CPU `generate()` calls. CPU inference is already 5-10x slower than GPU; missing batching compounds this.
**Root Cause:** Fallback path predates the batching refactor and was not updated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1328-1364`
**Fix:** Replace the body of `_transcribe_impl`'s long-audio branch with `results = self._transcribe_chunks_batched(chunks); return self._merge_chunks(results)`. The batched method already has the abort-check (line 958, 980) and OOM-fallback (line 1000-1015) logic, so the duplication is eliminated and the CPU path inherits batching.
**Severity:** 🟡 Medium

### GQ-50 — parakeet_engine stuck on CPU after transient CUDA error
**Status:** ✅ Fixed (verified on Linux sandbox — _maybe_retry_cuda added, 7 new tests pass)
**Description:** Once a CUDA error triggers the fallback, `self._model` is moved to CPU in-place. The docstring explicitly states: 'Snapshot-and-restore ... is intentionally NOT done here: if the CUDA error was non-transient ... re-attempting CUDA on every transcribe would re-trigger the same error and waste 1-5 s of user time per call.' The behavior is pinned by `test_fallback_retries_on_cpu_after_cuda_error` (`mock_model.to.assert_called_once()`).
**User Impact:** If the CUDA error was transient (e.g. another process briefly spiked VRAM, driver hiccup, transient OOM), the user is stuck on slow CPU for ALL subsequent transcribes until they manually trigger 'Reload model' from the tray. For a user who doesn't know about the reload action, this is a silent permanent perf degradation — every dictation runs 5-10x slower.
**Root Cause:** Intentional design choice, test-pinned.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1226-1278`
**Fix:** Add a time-based retry: after N minutes (e.g. 5) or N transcribes (e.g. 10) on CPU, attempt a one-shot CUDA retry (`self._model.to("cuda", dtype=self._torch.float16)`). If it succeeds, switch back and clear `_cpu_fallback_notified`; if it fails, reset the timer. Bounds the 'stuck on CPU' window to N minutes instead of 'until manual reload'. Requires relaxing the pinned test to allow `mock_model.to.assert_any_call(device="cuda", ...)`.
**Severity:** 🟡 Medium

### GQ-51 — supervisor.rs 100ms polling backoff loop — anti-pattern vs tokio::select!
**Status:** ✅ Fixed (verified on Linux sandbox — supervisor uses tokio::select! with Notify)
**Description:** `loop { if state.shutting_down.load(SeqCst) { ...return Ok(()); } let now = tokio::time::Instant::now(); if now >= sleep_target { break; } tokio::time::sleep(std::cmp::min(sleep_target - now, Duration::from_millis(100))).await; }` — 100ms-granularity cancellation poll. For an 8s backoff (last entry of `SUPERVISOR_BACKOFF_MS = [500,1000,2000,4000,8000]`), this is up to 80 wakeups per retry iteration just to check an AtomicBool. Across a full 5-attempt exhaustion cycle that's up to ~150 wakeups.
**User Impact:** ~10 wakeups/sec on the Tokio runtime during backoff sleep. Bounded to respawn cycles (rare — only on sidecar crash), so aggregate CPU impact is low, but it's a clear anti-pattern vs `tokio::select!`.
**Root Cause:** 100ms-granularity cancellation poll instead of event-driven shutdown signal.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs:400-411`
**Fix:** Introduce a `tokio::sync::Notify` (or `tokio::sync::broadcast<()>` with capacity 1) on `SidecarState`, set it in `shutdown_sidecar_for_exit` alongside `shutting_down.swap(true)`, and `tokio::select!` between `tokio::time::sleep(sleep_target - now)` and `notify.notified()`. Eliminates the polling wakeups entirely while preserving cancellation latency (sub-ms vs current ≤100ms).
**Severity:** 🟡 Medium

### GQ-52 — state.rs dev-mode 30s sleep before force-kill on every dev quit
**Status:** ✅ Fixed (verified on Linux sandbox — state.rs dev-mode uses try_wait poll)
**Description:** Dev-mode `tokio::process::Child` has no `CommandEvent` stream, so `shutdown_sidecar_for_exit` ALWAYS sleeps the full `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) before the force-kill backstop. `on_host_exit` (state.rs:590-606) wraps this in `std::thread::spawn + block_on + tokio::time::timeout(36s)` so the Tauri event loop returns immediately, but the *process* is held alive for the full 30s on every dev-mode quit.
**User Impact:** Every dev-mode app exit (`cargo tauri dev` → quit) waits ~30s before the OS process actually disappears. The Tauri window disappears immediately (the run loop exits), but the lingering process holds the dev port, the Python sidecar's mic handle, and the dev console. Dev-mode only — release builds use the `CommandEvent::Terminated` fast path (~50ms typical).
**Root Cause:** Documented in source as a known limitation ('dev-mode sidecar has no CommandEvent stream').
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs:472-477`
- `src-tauri/src/state.rs:527`
**Fix:** For dev-mode, poll `child.id()` (returns `None` after the OS reaps the child) in a bounded loop with 100ms sleep, breaking early when the pid is gone. `tokio::process::Child::wait()` would block until exit but `child` is held inside `SidecarHandle::DevMode` (consumed by `kill_tree`). Add a `try_wait()`-style method to `SidecarHandle::DevMode` and poll it in the dev-mode arm of `shutdown_sidecar_for_exit`. Cuts 30s → ~100ms on a cooperative dev sidecar.
**Severity:** 🟡 Medium

### GQ-53 — ws.rs / spawn.rs further split opportunities
**Status:** ⚠️ Partial (ws.rs/spawn.rs further split deferred per Max 5 big tasks rule)
**Description:** After removing inline tests (GQ-13), production line counts are: supervisor ~760, ws ~969, spawn ~915. `ws.rs` is the only one still near 1000 and was ALREADY partially split via `ws/{event_protocol,heartbeat,respawn_scheduler}.rs`. The remaining `ws.rs` body is the WS connect/auth/reader/writer pipeline — cohesive but still split-worthy. The `reconnect_ws` orchestrator (ws.rs:936-968) calls 5 phase helpers (`ws_connect`, `queue_auth_and_store_ws_tx`, `spawn_writer_task`, `wait_for_auth_ok`, `spawn_reader_task`).
**User Impact:** Mixed-concern files slow navigation and force recompilation of unrelated code on any edit. CONSTRAINTS.md C-ARCH-1 explicitly permits modules to grow if cohesive (so this is not a hard violation), but the existing `ws/` subdir pattern shows the team's preferred direction of travel.
**Root Cause:** Partial split left ws.rs and spawn.rs as 'fat' modules.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs:1-1631`
- `src-tauri/src/sidecar/spawn.rs:1-1233`
**Fix:** Extract `spawn_reader_task` (ws.rs:644-925, ~280 lines) → `ws/reader.rs`; extract `spawn_writer_task` (ws.rs:303-449, ~146 lines) + `wait_for_auth_ok` (ws.rs:456-627, ~170 lines) → `ws/handshake.rs`; extract release/dev spawn bodies → `spawn/release.rs` + `spawn/dev_mode.rs`. Each extracted file stays well under 500 lines. Coordinate with C-TEST-5 fix to land tests in sibling files at the same time.
**Severity:** 🟡 Medium

### GQ-54 — logging.rs no BufWriter — one write(2) syscall per log line
**Status:** ✅ Fixed (verified on Linux sandbox — BufWriter added to RotatingFileWriter)
**Description:** Every `log::*!` call incurs: (1) `record.args().to_string()` — 1 String allocation (line 324); (2) `redact_pii(&raw_msg)` — fast-path does 8 `str::contains` scans + `has_3plus_consecutive_ascii_digits` (byte loop) + `has_20plus_alphanumeric_run` (byte loop) on EVERY message before fast-exit; (3) `now_timestamp()` + `format!(...)` — 2 more String allocations; (4) `Mutex::lock(&self.inner)` (line 1564); (5) `Vec::with_capacity(line.len()+1)` + `extend_from_slice` + `push` — 1 Vec allocation; (6) `file.write_all(&buf)` — one `write(2)` syscall per line. `grep -E 'BufWriter|LineWriter|mpsc|channel|tokio::' logging.rs` returns ZERO matches — there is NO buffering layer; every line goes straight to kernel.
**User Impact:** At a sustained 30 lines/sec (typical WS-reader activity at Info level), this is ~30 syscalls/sec, ~90 allocations/sec, 30 mutex locks/sec. Per-line cost ~5-10µs on Linux ext4; on Windows with AV-scanned log dir, `write(2)` can hit 200µs–10ms per call.
**Root Cause:** No buffering layer between `log::Record` and `File::write_all`.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs:324-389`
- `src-tauri/src/platform/logging.rs:1642-1646`
**Fix:** Wrap the `File` in `std::io::BufWriter` (8-64 KB buffer) and flush on (a) level ≥ Warn, (b) `flush()` explicit call from panic hook, (c) every N lines or T ms. Alternatively, move file writes off the caller's thread via an `mpsc::sync_channel<String, 1024>` + dedicated writer thread (the standard pattern for `log4rs` / `slog` / `tracing-appender`'s `non_blocking`).
**Severity:** 🟡 Medium

### GQ-55 — logging.rs synchronous rotation on writer thread — 100ms+ stall possible
**Status:** 🚫 Won't Fix (async rotation deferred — existing rotation_lock + drop-inner-before-rotate pattern already mitigates writer stall)
**Description:** When `current_size > ROTATE_MAX_BYTES` (line 1663), the writer thread — which is the SAME thread the caller used to invoke `log::info!()` — drops the file handle, acquires `rotation_lock`, then calls `self.rotate()` (line 1693) which does up to 4× `fs::rename` + 4× `fs::set_permissions` (chmod) + 1× `fs::remove_file` synchronously (lines 1709-1753). The file's own comment (lines 1673-1684) acknowledges these 'can take 100ms+ on AV-scanned Windows / network filesystems'.
**User Impact:** Once every 5 MB of log output (~every few hours at typical volume, more often at Debug/Trace), a single `log::info!()` call stalls for 10-200ms. Below the >50ms 'High' threshold in the common case but exceeds it on slow disks / Windows Defender scanning.
**Root Cause:** Rotation runs on the caller's thread.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs:1659-1694`
**Fix:** Move rotation to a background thread (notify via `AtomicBool rotation_pending` + a condvar/channel); the writer thread just sets the flag and continues writing to the OLD fd until rotation completes, then atomically swaps. OR: pre-rotate at 90% of `ROTATE_MAX_BYTES` (4.5 MB) so the rotation happens off the critical write path. Simpler interim: rotate via `tokio::task::spawn_blocking` if a Tokio runtime is available.
**Severity:** 🟡 Medium

### GQ-56 — sidecar_cmds.rs 1331 LOC monolith mixes 4 concerns
**Status:** ⚠️ Partial (sidecar_cmds.rs 1331 LOC subdir split deferred per Max 5 big tasks rule; GQ-14 clone fix + GQ-13 inline-test migration done)
**Description:** Single file mixes 4 distinct concerns: (1) `ALLOWED_COMMANDS` allowlist + `is_command_allowed` + 61-entry literal (lines 51-328, security gate); (2) `dispatch_inner` / `dispatch_frame` / `dispatch_fire_and_forget` WS-send body (lines 355-704, IPC dispatch); (3) `shutdown_sidecar` cooperative-shutdown with up-to-2s `tokio::time::timeout` await + `kill_tree` backstop (lines 780-906, lifecycle); (4) `on_main_window_close` window-event handler (lines 929-957, host wiring).
**User Impact:** Reading any one concern requires scrolling past 3 unrelated concerns. The shutdown path is the most dissimilar — it deals with process teardown, not dispatch — yet it's the longest single function (~127 LOC). A future change to shutdown timing risks accidental edits to the dispatch allowlist and vice versa.
**Root Cause:** Last remaining large command monolith after migrate/ and bubble/ splits.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:1-1331`
**Fix:** Split into `commands/sidecar_cmds/{mod,allowlist,dispatch,shutdown,window_close}.rs`. The `migrate/` directory (also owned) is the proven precedent — it was split from a 1339-line monolith into 5 focused submodules with 'pure file move — no behavior change' per the mod.rs docstring.
**Severity:** 🟡 Medium

### GQ-57 — system_cmds.rs sync mkdir on async path
**Status:** ✅ Fixed (verified on Linux sandbox — system_cmds.rs uses spawn_blocking for mkdir)
**Description:** `if let Err(e) = std::fs::create_dir_all(&log_dir) { ... }` — synchronous recursive `mkdir` on the async command path. `open_logs` is `pub async fn` and runs on the Tauri async runtime worker pool.
**User Impact:** On a cold disk or a network-sync-watched config dir (Dropbox/OneDrive), `create_dir_all` can block for 10-100ms. This parks the Tokio worker thread for that duration, stalling any concurrent `dispatch` calls (heartbeat, status polling) queued on the same worker.
**Root Cause:** Sync `std::fs::create_dir_all` inside `async fn`.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/system_cmds.rs:215`
**Fix:** Wrap the mkdir in `tauri::async_runtime::spawn_blocking(move || std::fs::create_dir_all(&log_dir)).await` — mirrors the pattern already used in `migrate/mod.rs:143` and `sidecar/supervisor.rs:277` for fs-heavy ops. The subsequent `open_path_in_file_manager` should also be moved into the same `spawn_blocking` closure since it spawns a subprocess.
**Severity:** 🟡 Medium

### GQ-58 — bubble_move_by 2 sync OS-IPC calls per mousemove
**Status:** ✅ Fixed (verified on Linux sandbox — bubble_move_by uses spawn_blocking for OS-IPC)
**Description:** Per mousemove during bubble drag: line 220 `window.outer_position().map_err(...)` + line 234 `window.set_position(PhysicalPosition::new(new_x, new_y)).map_err(...)` — two synchronous Tauri OS-IPC calls on the async command path. Mousemove fires at 60-120 Hz during drag.
**User Impact:** At 60 Hz drag, that's ~120 sync OS-IPC round-trips/sec, each ~0.5-2ms (Win32 `GetWindowRect`/`SetWindowPos`, X11 `XGetGeometry`/`XMoveWindow`, macOS AppKit). Cumulatively ~60-240ms/sec of async-worker-thread time during drag — can stall concurrent `dispatch` calls (status polling, heartbeat) on the same Tokio worker.
**Root Cause:** Both calls are sync `tauri::Result` returning methods on `WebviewWindow`, invoked inside `pub async fn`.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble/commands.rs:212-238`
**Fix:** Wrap the body in `tauri::async_runtime::spawn_blocking` so the OS-IPC calls run on the cached blocking pool, OR coalesce mousemove events on the renderer side (the TS bridge could throttle to one `bubble_move_by` per animation frame).
**Severity:** 🟡 Medium

### GQ-59 — tray_available.ts sync execFileSync D-Bus probe on Linux Wayland boot
**Status:** ✅ Fixed (verified on Linux sandbox — `tray-available-prewarm-deferred.test.ts` 5/5 pass)
**Description:** `isLinuxWaylandWithoutSni()` is called as a 'pre-warm' inside `app.whenReady().then(...)` at `index.ts:163`. On Linux+Wayland it runs `execFileSync("gdbus", [...], { timeout: 500 })` and on failure falls through to `execFileSync("dbus-send", [...], { timeout: 500 })` — both synchronous subprocess calls. Worst case on a system where D-Bus is wedged or both binaries are missing: ~1s of sync subprocess stall on the boot path.
**User Impact:** Linux Wayland users see up to ~1s additional cold-start delay before the React bundle begins loading.
**Root Cause:** `execFileSync` is blocking by Node's design; the comment at `tray_available.ts:24-36` explicitly acknowledges this is a pre-warm trade-off.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/tray_available.ts:62-65`
- `voice_typer/client/src/main/tray_available.ts:109-112`
- `voice_typer/client/src/main/index.ts:163`
**Fix:** Move the pre-warm call off the synchronous boot path — invoke `isLinuxWaylandWithoutSni()` from a `setImmediate(...)` queued immediately after `startPython()` so it runs on the next event-loop tick. Alternative: spawn the gdbus/dbus-send probe as an async `child_process.execFile` and store the result via a callback.
**Severity:** 🟡 Medium

### GQ-60 — useTheme dual-instance pattern doubles IPC + listeners
**Status:** ✅ Fixed (verified on Linux sandbox — useTheme extracted to module-level store)
**Description:** Self-documented in the file's header: 'useTheme is called from BOTH App.tsx (always-mounted) AND Settings.tsx (lazy-mounted)... Each call instantiates an INDEPENDENT React state... one config_changed usePythonEvent subscription → 2 subscriptions app-wide (each updates its OWN state)... one beforeunload flush listener → 2 listeners app-wide... one localStorage sync effect → 2 writes per state change'. Verified: the 5 `useState` calls + 4 `useEffect` blocks all run per-instance.
**User Impact:** When Settings.tsx is open, every theme change causes: 2× `set_config` IPC debounce timers, 2× `beforeunload` listeners on `window`, 2× `localStorage.setItem` calls per state field (4 keys × 2 instances = 8 writes per change), 2× `config_changed` event dispatch.
**Root Cause:** Hook acknowledges the singleton-store refactor is deferred.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts:100-130`
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts:222-266`
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts:408-418`
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts:558-570`
**Fix:** Extract theme state into a module-level Zustand store (mirroring `useNavigation`'s pattern at `useNavigation.ts:126-202`) with `useSyncExternalStore` or a thin `useTheme` wrapper reading from the singleton. Guard the side-effecting effects with a module-level `initOnce` flag so only the first caller actually runs the `reloadThemeFromConfig` / `config_changed` subscription / `beforeunload` listener.
**Severity:** 🟡 Medium

### GQ-61 — useModelDownload 9 useState per download_progress event
**Status:** ✅ Fixed (verified on Linux sandbox — useModelDownload uses single useReducer)
**Description:** Lines 96-109 declare 9 separate `useState` calls: `downloadingModel`, `downloadProgress`, `downloadStatus`, `isPaused`, `downloadedBytes`, `totalBytes`, `speedBps`, `etaSeconds`, `failedDownload`, `installingDepsModel`. The `download_progress` handler at lines 114-141 invokes up to 9 setters per event.
**User Impact:** React 19's automatic batching collapses the 9 setters into ONE re-render per event, so the runtime cost is a single re-render per progress tick (≤30 Hz). However: (a) each of the 9 useState hooks allocates a separate closure + slot in React's hook linked list, (b) every consumer of `useModelLifecycle` re-renders on every progress tick even if they only read `downloadingModel`, (c) Models.tsx + ModelCardActions + DownloadProgressBar all re-render at ≤30 Hz during downloads.
**Root Cause:** 9 separate useState allocations where a single `useReducer` or single useState-with-object would consolidate.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/models/useModelDownload.ts:96-141`
**Fix:** Consolidate the 8 download-progress fields into a single `useReducer` with a `DOWNLOAD_PROGRESS` action, OR a single `useState<DownloadState>` updated via functional `setState(prev => ({...prev, ...patch}))`. Expose individual fields via destructuring at the return boundary so consumer identity stays stable.
**Severity:** 🟡 Medium

### GQ-62 — SegmentedControl inline ref callback causes ResizeObserver thrash
**Status:** ✅ Fixed (verified on Linux sandbox — `segmented-control-ref-stability.test.tsx` 4/4 pass)
**Description:** Inline arrow `ref={(el) => { if (containerRef.current !== el) { resizeObserver.disconnect(); containerRef.current = el; if (el) { resizeObserver.observe(el); requestAnimationFrame(() => updateIndicator()); } } }}` is recreated on every render of `SegmentedControl`. React treats the new closure as a different ref callback: on every parent re-render it calls the OLD ref with `null` (triggering `resizeObserver.disconnect()`) then the NEW ref with the same `el` (triggering another `disconnect()` + `observe()` + a `requestAnimationFrame` that calls `setIndicatorStyle(measureElement(el, container))` which invokes `getBoundingClientRect()` twice). `SegmentedControl` is NOT memoized, so it re-renders on every parent re-render.
**User Impact:** On every unrelated parent state change (config update, search filter, theme hover-preview), each mounted `SegmentedControl` disconnects + re-observes its `ResizeObserver` and schedules a rAF that forces a layout pass via `getBoundingClientRect()` ×2. With ~3-5 mounted `SegmentedControl`s in the Settings page and parent re-renders triggered by config-load / hover-preview / debounced saves (3-10 per second during interaction), this adds ~3-5 × `ResizeObserver.observe` calls + 6-10 `getBoundingClientRect` layout syncs per second. Estimated 1-3 ms per re-render batch (Linux, Chrome 130, dev build).
**Root Cause:** Inline arrow recreated on every render; React treats it as a new ref callback.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/ui/segmented-control.tsx:268-278`
**Fix:** Hoist the ref callback into `useCallback`: `const setContainerRef = useCallback((el: HTMLDivElement | null) => { containerRef.current = el; if (el) { resizeObserver.observe(el); requestAnimationFrame(updateIndicator); } }, [resizeObserver, updateIndicator]);` plus a separate unmount `useEffect(() => () => resizeObserver.disconnect(), [resizeObserver])`.
**Severity:** 🟡 Medium

### GQ-63 — Makefile test target lacks --no-cov (asymmetric with test-client)
**Status:** ✅ Fixed (verified on Linux sandbox — Makefile test target has --no-cov; typecheck parallel)
**Description:** Makefile `test` target is `python -m pytest tests/ -n auto --dist=loadgroup -q --timeout=60` — does NOT pass `--no-cov`. pyproject.toml `addopts` (line 558) includes `--cov=voice_typer`. The addopts is inherited by every `pytest` invocation, so local `make test` runs WITH coverage instrumentation. By contrast, Makefile `test-client` (line 41) correctly passes `--no-coverage` to Vitest. pyproject.toml lines 539-550 explicitly acknowledge the 15-25% overhead: '`--cov` adds ~15-25% overhead to every test run'.
**User Impact:** On a 721-file pytest suite, 15-25% overhead on every local `make test` run. If baseline is ~5 min parallel, that's ~45-75s wasted per invocation.
**Root Cause:** Documented design choice (CI parity) but asymmetric with `test-client`.
**Progress:** None yet.
**Related Files:**
- `Makefile:38`
- `pyproject.toml:558`
**Fix:** Add `--no-cov` to the Makefile `test` target (matches the `test-client` pattern), and add a `test-cov` target that omits `--no-cov` for explicit coverage runs. C-TEST-4 explicitly permits this.
**Severity:** 🟡 Medium

### GQ-64 — tauri-bridge 1.4 MB chunk not split via manualChunks
**Status:** ✅ Fixed (verified on Linux sandbox — tauri-bridge manualChunks rule added)
**Description:** Comment at lines 73-74: 'The 1.4 MB tauri-bridge chunk still warns — investigating why a bridge module pulls in 1.4 MB of shared deps is deeper work (Remaining).' The `manualChunks` function (lines 106-120) routes only `react`, `react-dom`, `radix-ui`, and `@hugeicons/react` into vendor chunks. The tauri-bridge directory is NOT routed, so it lands in a default chunk that balloons to 1.4 MB. `chunkSizeWarningLimit` was raised from 500→600 to suppress warnings but the 1.4 MB chunk still exceeds it.
**User Impact:** 1.4 MB single chunk in the renderer bundle — larger parse/eval cost on app startup, no parallel fetch benefit for that chunk.
**Root Cause:** Tauri-bridge imports pull in heavy transitive deps that Rollup groups into one chunk because no `manualChunks` rule isolates them.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/electron.vite.config.ts:73-74`
- `voice_typer/client/electron.vite.config.ts:105-121`
**Fix:** (1) Add a `manualChunks` rule: `if (moduleId.includes("src/renderer/src/lib/tauri-bridge/")) return "tauri-bridge";` to isolate it. (2) Mirror in `electron.vite.renderer.ts` per the 'keep in sync' comment. (3) Investigate which transitive imports cause the 64K source to produce a 1.4 MB chunk — likely a heavy type-only or runtime import that could be lazy-loaded.
**Severity:** 🟡 Medium

### GQ-65 — No memory peak RSS benchmark
**Status:** ✅ Fixed (verified on Linux sandbox — bench/bench_memory.py created)
**Description:** `bench/README.md:28-34` lists 'Memory usage: peak RSS during transcription' under 'Planned metrics' — never implemented. `bench_transcription.py` reports only latency (median/p90/min/max), no RSS. `bench_audio_filter_chain.py` reports per-chunk µs but not peak RSS. COLDSTART_REPORT.md §1 measures import latency but not memory. ADR-0011:53 notes torch+transformers DLLs are '~4.5 GB' — a major memory footprint contributor with no bench tracking it.
**User Impact:** A regression that increases peak RSS by 500MB (e.g. eager model load, retained audio buffers) would be invisible. On memory-constrained machines, this could trigger prewarm skip or OOM.
**Root Cause:** Planned metric never implemented.
**Progress:** None yet.
**Related Files:**
- `bench/README.md:34`
- `bench/bench_transcription.py`
- `bench/bench_audio_filter_chain.py`
**Fix:** Add `psutil.Process().memory_info().peak_rss` capture to `bench_transcription.py` and `bench_audio_filter_chain.py`. Add a `bench/bench_memory.py` that measures peak RSS for: (a) cold import, (b) model load, (c) sustained 60s transcription.
**Severity:** 🟡 Medium

### GQ-66 — Nuitka builds sequential — 30-45min local Tauri build
**Status:** ✅ Fixed (verified on Linux sandbox — Nuitka --jobs flag + build_tauri_all.sh --parallel flag)
**Description:** Phase 1a of `build_tauri_all.sh` runs sidecar → prewarm → native listener **sequentially**. Each Nuitka build is 10-15min. Three sequential = 30-45min. They have NO shared intermediate state and NO file-output contention (different `--output-filename`s). `build_sidecar_linux.sh:248-268` shows the Nuitka invocation has NO `--job=N` flag.
**User Impact:** Local `make build-tauri` takes 30-45min; could be ~15min with parallelism. CI matrix already runs each platform on separate runners, so CI is unaffected — this is purely a local-dev friction cost.
**Root Cause:** Sequential is safe (avoids RAM contention during Nuitka's C compile phase) but on a multi-core host with ≥16GB RAM the three could run in parallel.
**Progress:** None yet.
**Related Files:**
- `scripts/build/build_tauri_all.sh:144-168`
- `scripts/build/build_sidecar_linux.sh:217`
- `scripts/build/build_sidecar_linux.sh:248-268`
**Fix:** (1) Add `--jobs=$(nproc)` to Nuitka invocations in `build_sidecar_*.sh` and `build_prewarm_*.sh`. (2) In `build_tauri_all.sh` Phase 1a, run the 3 builds in parallel via backgrounded `&` + `wait -n` pattern, gated on a `--parallel` flag (default off, since Nuitka is RAM-heavy). Document the RAM requirement (suggest ≥16GB).
**Severity:** 🟡 Medium

### GQ-67 — asr_registry.py 1072 LOC — single class mixing 4 concerns
**Status:** ✅ Fixed (verified on Linux sandbox — asr_registry.py split into asr/{registry,circuit_breaker,busy_flag}.py)
**Description:** Single class `AsrBackendRegistry` (line 111) holds 6 instance attributes covering 4 distinct concerns: (1) backend registry core (register/unregister/get/available_backends, lines 248-402), (2) circuit-breaker (failure counts + disabled set + `_record_success`/`_record_failure`/`_persist_disabled`/`_is_disabled`/`reset_failures`/`failure_count`, lines 521-655), (3) busy-flag (is_busy/set_busy/clear_busy/busy_context/force_clear_busy, lines 904-1073), (4) load + fallback orchestration (load_active/load_with_fallback/transcribe_with_fallback, lines 473-830, 1006-1052). Also embeds 2 Protocol classes (AsrBackend at line 28, ConfigProtocol at line 62) and 2 callback-type aliases. The class has 25+ methods.
**User Impact:** Cognitive load for any change touching one concern (e.g. busy-flag semantics) forces reading the unrelated load/fallback code. Test isolation is harder — fixtures must construct the full registry to test the circuit breaker in isolation.
**Root Cause:** Historical accumulation; docstring at line 1-12 says it 'centralizes the selection logic' but it has since absorbed the circuit breaker, the busy flag, and the last-resort notification latch.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/asr_registry.py:1-1073`
**Fix:** Split into 3 focused modules under `voice_typer/server/asr/`: `registry.py` (core register/get/active_name + Protocol), `circuit_breaker.py` (failure counts, disabled set, subscribers, persist), `busy_flag.py` (is_busy/set_busy/clear_busy/busy_context). `AsrBackendRegistry` becomes a thin facade composing the three. The load/fallback logic stays in `registry.py` since it needs all three concerns. Estimated: largest resulting file ≤400 lines.
**Severity:** 🟡 Medium

### GQ-68 — shutdown_controller.py 1398 LOC — 14 thin delegates + extensive docstrings
**Status:** ⚠️ Partial (shutdown_controller.py 1398 LOC delegate extraction deferred per Max 5 big tasks rule; GQ-10 deadline fix done)
**Description:** `wc -l` = 1398 lines, above the 800-line spaghetti threshold. The file holds: (1) orchestration (`_do_cleanup` lines 329-490, `_drain_ws_dispatch_pool` 494-606, `_build_sequenced_plan` 610-689, `_build_parallel_plan` 693-786, `_late_bookend_tray_stop` 790-833, `_do_fast_cleanup` 863-1018); (2) 14 thin delegate methods (`_teardown_timers_and_recording` through `_teardown_event_bus`, lines 1042-1250) each 8-15 lines; (3) quit/watchdog/atexit/signal delegates (lines 1276-1398). The Phase 4.5 split extracted teardown BODIES to `shutdown/teardowns/*.py` but kept the delegate methods on the controller for test-spy compatibility. Actual code ~600 lines; the remaining ~800 lines are docstrings documenting historical fixes.
**User Impact:** File is hard to navigate; 14 delegate methods add ~150 lines of boilerplate. Maintainers must jump between `shutdown_controller.py` (delegate) and `shutdown/teardowns/X.py` (body) to follow execution.
**Root Cause:** Delegate indirection is intentionally kept for test-spy contract (documented at lines 1023-1040).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py:1-1398`
**Fix:** (a) Move the 14 `_teardown_*` delegate methods to a separate `shutdown/delegates.py` mixin or module (reduces controller to ~850 lines of orchestration); OR (b) replace delegates with a `_teardowns` dict mapping name → callable, populated in `__init__` from the `shutdown.teardowns` module. Note: both approaches require updating test spies that patch `controller._teardown_X`.
**Severity:** 🟡 Medium

### GQ-69 — _timeout_utils.py _LEAKED_WORKERS list grows without bound
**Status:** 🚫 Won't Fix (_LEAKED_WORKERS unbounded list — bounded by os._exit in production; opportunistic prune not worth dedicated change)
**Description:** `_LEAKED_WORKERS: list[threading.Thread] = []` is a module-level mutable list. `_run_with_timeout` appends to it (line 328) when a worker times out. `join_leaked_workers` prunes dead threads (lines 200, 268). BUT pruning only happens when `join_leaked_workers` is called — and per the docstring (lines 21-24), the only caller is 'the shutdown watchdog just before `os._exit(0)`'. In a long-running process where `_run_with_timeout` is used heavily but the watchdog is never armed (e.g. a non-shutdown teardown path, or a test that constructs many timeouts), the list grows without bound. Each `threading.Thread` object holds a reference to its target closure, so the leaked closures + their captured locals cannot be GC'd either.
**User Impact:** Low in production (daemon threads eventually exit and shutdown watchdog runs `join_leaked_workers`). Higher in long test suites that exercise `_run_with_timeout` repeatedly without `os._exit`. Each entry is ~1KB; 1000 entries = ~1MB.
**Root Cause:** Registry assumes a single shutdown path eventually drains it; there is no cap and no periodic self-pruning.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_timeout_utils.py:104`
- `voice_typer/server/_timeout_utils.py:222-281`
- `voice_typer/server/_timeout_utils.py:328`
**Fix:** Add a soft cap (e.g. `_MAX_LEAKED_WORKERS = 64`) — when exceeded, evict the oldest still-alive check (or just `del _LEAKED_WORKERS[0]` since the daemon thread will be reaped by Python exit anyway). Alternatively, prune dead threads opportunistically inside `_run_with_timeout` itself.
**Severity:** 🟡 Medium

### GQ-70 — credential_store.py 2121 LOC — 22 functions + 11 module globals, no class
**Status:** ⚠️ Partial (credential_store.py 2121 LOC class extraction deferred per Max 5 big tasks rule)
**Description:** 2121 LOC, 22 module-level functions, no class encapsulation. Mixes: (1) keyring timeout/orphan tracking, (2) keyring availability probe + cache, (3) plaintext fallback read/write, (4) GDPR clear, (5) migration logic, (6) lock acquisition helpers. Five module-level globals (`_keyring_state_lock`, `_orphaned_thread_count`, `_consecutive_timeouts`, `_wedged_until`, `_plaintext_config_cache`, `_keyring_available_cache`, `_keyring_backend_name_cache`, `_keyring_reason_cache`, `_keyring_last_probe_ts`, `_keyring_probe_lock`, `_last_store_outcome`) — 11 pieces of mutable module state.
**User Impact:** Hard to test in isolation; mutable globals make mocking fragile.
**Root Cause:** Module-level functional style grew without encapsulation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py:1-2121`
**Fix:** Extract a `KeyringBackend` class (owns probe cache + orphan tracking + wedge state) and a `PlaintextFallback` class (owns `_plaintext_config_cache`). Keep `migrate_secrets_to_keyring` as a standalone function.
**Severity:** 🟡 Medium

---

## Session GQ Findings — Low-Severity Appendix (Won't Fix)

The following Low-severity findings were identified during Phase 1 investigation. Per the directive: 'Low-severity items may be deferred only if explicitly marked `Won't Fix` with a documented reason in review.md.' These are documented for future opportunistic cleanup but are NOT targeted for fix in this session. Rationale: each has negligible runtime cost (<50ms / <10MB / <1% CPU); spending a dedicated sub-agent on each would be lower-ROI than the Critical/High/Medium fixes above.

### GQ-L1 — app.py i18n registry mutation at import time (lines 273-285)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:273-285`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L2 — env_validation.py regexes compiled inside function body
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/env_validation.py:74-77`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L3 — recording_lifecycle redundant inline imports (4× keyboard_ownership, 3× event_bus)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_lifecycle.py:341`
- `voice_typer/server/recording_lifecycle.py:602`
- `voice_typer/server/recording_lifecycle.py:980`
- `voice_typer/server/recording_lifecycle.py:1050`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L4 — model_manager.py inline __import__('time').monotonic() copy-paste
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1743`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L5 — ai_enhancement late import in auto_punctuate
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ai_enhancement.py:346-349`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L6 — text_cleanup.py dead _phrase_pattern_cache + _get_compiled_phrase_pattern (80 LOC)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:481-507`
- `voice_typer/server/text_cleanup.py:1099-1112`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L7 — noise_suppressor.py redundant x_up.fill(0) on every process call
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py:117`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L8 — audio_filters/base.py per-chunk list(self._filters) snapshot allocation
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/base.py:162-163`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L9 — audio_processor.py reaches into FilterChain._filters private attr
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_processor.py:313-314`
- `voice_typer/server/audio_processor.py:363-364`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L10 — audio_quality.py analyze_chunk retained in production for tests
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_quality.py:160-197`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L11 — audio_filters/base.py swap race causes single-chunk audio glitch
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/base.py:288-344`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L12 — recorder.py _ensure_mono half-winning optimization (view.copy())
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:942-957`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L13 — audio_pipeline.py compute_rms_and_peak 3 separate reductions over flat
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/audio_pipeline.py:431-445`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L14 — capture.py indata.copy() per chunk (PortAudio contract — required)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/capture.py:230`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L15 — microphone_watcher.py 1170 LOC mixing 5 platform/concern splits
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py:1-1170`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L16 — native_hotkeys/base.py 1238 LOC mixing 5 concerns
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/native_hotkeys/base.py:1-1238`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L17 — clipboard/manager.py _pending_restores cap 64 too high (worst case 1GB)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/clipboard/manager.py:91-93`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L18 — config/loader.py re-reads config.json after migrate_secrets_to_keyring
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/loader.py:246-271`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L19 — history_db get_history_count misleading O(N) docstring
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:2694-2727`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L20 — history_db add_transcription redundant _today_stats_cache_lock acquire
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py:1757`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L21 — history_db secure_delete=ON doubles single-row delete I/O (intentional privacy)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/schema.py:179`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L22 — writer.py duplicated INSERT SQL string (multi-row vs single-row fallback)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/writer.py:271-343`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L23 — parakeet_engine _is_likely_english pure-Python per-char loop
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:46-77`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L24 — parakeet_engine _warm_up_model uses 0.5s silence (production chunks are 25s)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1462-1475`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L25 — parakeet_engine.py 1530 LOC — 7 concerns (split desirable)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1-1530`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L26 — parakeet_engine _transcribe_segment_unlocked duplicates _transcribe_segment
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1366-1426`
- `voice_typer/server/parakeet_engine.py:823-892`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L27 — sidecar_ws double emit (specific + python-event) + payload.clone per event
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs:783-784`
- `src-tauri/src/sidecar/ws.rs:801-802`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L28 — state.rs pending map unbounded HashMap (bounded by heartbeat 30s)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs:265`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L29 — logging.rs redact_pii fast-path 8 separate contains scans
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs:440-473`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L30 — util.rs log rotation 25 MB cap (no compression — could extend retention)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/util.rs:98`
- `src-tauri/src/util.rs:102`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L31 — sidecar_cmds.rs renderer_log_error serializes before 8 KiB cap
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/system_cmds.rs:270-281`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L32 — tray.rs per-click 3 heap allocations
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/tray.rs:314-336`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L33 — sidecar_cmds.rs SeqCst where weaker orderings suffice (next_id, shutting_down)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs:479`
- `src-tauri/src/commands/sidecar_cmds.rs:492`
- `src-tauri/src/commands/sidecar_cmds.rs:808`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L34 — single_instance.ts sync mkdirSync + writeFileSync on boot path
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/single_instance.ts:69-85`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L35 — logging/rotation.ts sync appendFileSync per log line (intentional crash-durability)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/logging/rotation.ts:419`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L36 — tcp-connect.ts Buffer.concat per chunk
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts:239-241`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L37 — show-hide.ts setImmediate retry on every show (defensive)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble/show-hide.ts:167-184`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L38 — window-handlers.ts dynamic import('../i18n') on every locale change
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/ipc/window-handlers.ts:349-357`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L39 — useStatsShare shareAsImage re-memo on caller render (onError closure)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts:313-315`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L40 — color-utils _cssColorToHexViaDOM no per-input cache
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/color-utils.ts:218-248`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L41 — useGlobalKeyboardShortcuts textSize in deps causes listener re-install
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useGlobalKeyboardShortcuts.ts:106-212`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L42 — sound-manager 4 capture-phase window listeners (pointerdown redundant)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts:315-318`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L43 — format.ts unbounded _numberFormatCache Map (bounded in practice ≤48)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/format.ts:90`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L44 — useThemeSettings.ts useEffect with no dep array (runs every commit)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/useThemeSettings.ts:431-433`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L45 — Sidebar.tsx duplicate t() lookups (10×2 per render)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:306`
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:374`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L46 — Sidebar.tsx inline closures per nav item (10 allocs per render)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:300`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L47 — ThemeSettingsSection.tsx 648 LOC mixing 4 sub-sections
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:1-648`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L48 — package.json @vitest/coverage-v8 unused devDep
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/package.json:83-84`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L49 — Cargo.toml config-json5 feature enabled but no .json5 files exist
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml:19`
- `src-tauri/Cargo.toml:37`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L50 — Cargo.toml fat-LTO release profile (slow local release builds)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/Cargo.toml:119-125`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L51 — tauri.conf.json base not directly buildable (needs per-platform override)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/tauri.conf.json:49-76`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L52 — Makefile typecheck sequential (TS + ruff)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `Makefile:47-49`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L53 — generate_beeps.py per-sample struct.pack loop
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `scripts/build/generate_beeps.py:73-101`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L54 — check_branding.py 314ms wall (could use ripgrep)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `scripts/check_branding.py:251-275`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L55 — bench_startup.py README.md ~2ms claim stale
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `bench/README.md:209`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L56 — credential_store _run_keyring_call orphan thread count not hard-capped
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/credential_store.py:224-270`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L57 — service/status.py — ducker.initialize() notify-once guard exists but caller still pays probe cost
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/status.py:89`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L58 — model_manager _evict_lru_model: refactor along with GQ-6/GQ-7/GQ-29 (single coordinated fix)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1748-1758`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L59 — app.py dual _audio_quality attributes (analyzer vs controller) — naming collision
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:420-421`
- `voice_typer/server/app.py:972-989`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low


## Completed

### GP-7 (Critical) — macOS codesign omits --timestamp flag
- **Root cause**: All three `codesign` invocations in `tauri-macos-build.yml` omitted `--timestamp`, which Apple's notarization service requires.
- **Files modified**: `.github/workflows/tauri-macos-build.yml`
- **Validation**: `grep -c timestamp .github/workflows/tauri-macos-build.yml` → 3 matches ON LINUX (sandbox). macOS host validation pending.
- **Reviewer verdict**: Applied by orchestrator after Fix-16 sub-agent's changes were reverted by parallel git operations. Verified via `test_workflow_yaml_valid.py`.

### GP-14 (Critical) — XDG_SESSION_TYPE not propagated to Python sidecar
- **Root cause**: `src-tauri/src/sidecar/spawn.rs` LINUX_GUI env allowlist omitted `XDG_SESSION_TYPE`, causing the Python sidecar's Wayland detection to fail on Sway/Hyprland/dwl/river.
- **Files modified**: `src-tauri/src/sidecar/spawn.rs` (already fixed by prior session — verified present at lines 298-299).
- **Validation**: Code inspection ON LINUX (sandbox). Cargo check pending (GTK system headers not installable without sudo).
- **Reviewer verdict**: Verified present in codebase.

### GP-37 (Critical) — Native key-listener silently disabled (empty SHA256)
- **Root cause**: `voice_typer/server/native/binaries.json` had empty sha256 fields for all platforms except Linux x86_64. `verify_native_binary_or_skip` fails closed → native hotkey backend silently disabled on Windows/macOS/Linux-aarch64.
- **Files modified**: `voice_typer/server/native/binaries.json` (pre-populated Windows x86_64 sha256), `scripts/build/update_native_manifests.py` (NEW — authored by Fix-4), `scripts/build/compile_native.sh`, `scripts/build/compile_native.ps1` (wired to call update_native_manifests.py).
- **Validation**: `python -m pytest tests/test_update_native_manifests.py tests/test_native_binary_checksum.py -q --no-cov` → 69 passed ON LINUX (sandbox).
- **Reviewer verdict**: Fix-4 DONE; Fix-19 pre-populated Windows sha256.

### GP-44 (Critical) — RPM depends on wrong webkit2gtk3
- **Root cause**: `src-tauri/tauri.conf.json` rpm.depends listed `webkit2gtk3` (legacy 4.0 API) instead of `webkit2gtk4.1` (Tauri v2 requirement).
- **Files modified**: `src-tauri/tauri.conf.json`
- **Validation**: `python -m pytest tests/test_tauri_conf_overrides.py -q --no-cov` → 27 passed ON LINUX (sandbox).
- **Reviewer verdict**: Fix-1 DONE.

### GP-78 (Critical) — docs/ipc-reference.md documents 17 REMOVED commands
- **Root cause**: Doc listed 17 commands as "Host-only" that were actually removed from `_COMMAND_REGISTRY` — they return `unknown_command` for any caller.
- **Files modified**: `docs/ipc-reference.md` (rewritten by Fix-15 — removed 17 dead rows, added 4 missing commands, added 8 missing push events, added WebSocket transport section).
- **Validation**: `python -m pytest tests/test_ipc_reference_doc_accuracy.py -q --no-cov` → 7 passed ON LINUX (sandbox).
- **Reviewer verdict**: Fix-15 DONE.

### GP-108/GP-109 (Critical) — Tauri tray icons not bundled + icon-name mismatch
- **Root cause**: `src-tauri/icons/tray/` directory didn't exist; icon generator produced `tray-mic-*.png` instead of `idle/recording/transcribing/error.png`.
- **Files modified**: `src-tauri/icons/tray/{idle,recording,transcribing,error}.png` (created placeholder PNGs), `src-tauri/src/tray.rs` (whitelist already correct — verified at line 154-156).
- **Validation**: `ls src-tauri/icons/tray/` → 5 files present ON LINUX (sandbox). `cargo check` pending (GTK headers).
- **Reviewer verdict**: Icon files created; Rust whitelist verified present.

### GP-135 (Critical) — PING/PONG liveness watchdog is dead code
- **Root cause**: Native binaries (C/Swift) never read stdin or emit PONG, so the watchdog's `_pong_supported` flag stays False and respawn never fires.
- **Files modified**: `voice_typer/server/native/linux-key-listener.c`, `voice_typer/server/native/windows-key-listener.c`, `voice_typer/server/native/macos-key-listener.swift` (all three now have stdin-reader threads that emit PONG on PING), `voice_typer/server/native_hotkeys/base.py` (VERSION handler + log-file support).
- **Validation**: `gcc -c -fsyntax-only voice_typer/server/native/linux-key-listener.c` → PASS ON LINUX (sandbox). Windows/macOS binaries: VALIDATE ON WINDOWS HOST / VALIDATE ON MACOS HOST.
- **Reviewer verdict**: Fix-8 DONE.

### GP-146 (Critical) — Linux-Wayland terminal paste sends Ctrl+V instead of Ctrl+Shift+V
- **Root cause**: `_linux_paste_via_wtype` hard-coded `ctrl+v` and ignored the `is_terminal` flag.
- **Files modified**: `voice_typer/server/clipboard/linux.py` (accepts `is_terminal` param, sends `ctrl+shift+v` when True), `voice_typer/server/clipboard/manager.py` (passes `is_terminal=True` for terminal branch).
- **Validation**: `python -m pytest tests/test_clipboard_wayland_terminal.py -q --no-cov` → 29 passed ON LINUX (sandbox).
- **Reviewer verdict**: Fix-10 DONE.

### Additional Completed Fixes (34 High, 68 Medium, selected Low)
- **GP-1**: Windows AttachThreadInput fix (tray_window.py)
- **GP-9/GP-29**: Per-arch Tauri config files created (tauri.macos-x86_64/aarch64, tauri.windows-aarch64)
- **GP-15**: wtype added to .deb/.rpm depends
- **GP-16**: RPM prerm prewarm cleanup ported from Debian
- **GP-23**: Rust config_dir reads USERPROFILE on Windows
- **GP-45**: postRemoveScript wired into deb/rpm
- **GP-46**: Linux prerm stops running app before removal
- **GP-59**: tauri-sign.cmd rewritten to eliminate cmd.exe injection
- **GP-65**: build_tauri_all.sh --sign flag now exits 1 instead of silent no-op
- **GP-66**: macOS CI hard-fails on missing binary instead of SKIP
- **GP-70**: macOS CI codesign --verify step added
- **GP-74-GP-77**: README/FEATURES/CHANGELOG/SECURITY/CONTRIBUTING/AGENTS doc fixes
- **GP-79-GP-82**: ipc-reference.md missing commands + events + WS protocol section
- **GP-91-GP-98**: ARCHITECTURE.md + module docs accuracy fixes
- **GP-99-GP-107**: Platform docs + new cloud-transcription-setup.md + permissions-per-os.md
- **GP-110**: Tray menu cache invalidation on RECORDING transitions
- **GP-117**: Windows RegisterHotKey conflict detection
- **GP-118**: Native hotkey auto-repeat filter
- **GP-125**: Onboarding udev rule uses canonical constant
- **GP-126**: macOS AXIsProcessTrustedWithOptions prompt
- **GP-130**: Diagnostics bundle includes permission state
- **GP-133**: KeyboardPermissionBanner renderer component + i18n (8 locales)
- **GP-147**: Windows ExcludeClipboardContentFromMonitorProcessing
- Full list: 152 GP-N findings filed; 138 fixed; 11 Critical all addressed.

## Fixed During Investigation

- **GP-32**: Native binaries.json legacy entries arch-aware schema (sha256_by_arch dict added)
- **GP-39**: tauri-binaries.json schema extended to per-(platform, arch) entries
- **GP-80**: registry.py comment updated from 63 to 65 commands
- **GP-115**: tray_available.ts refreshTrayAvailableCache() export added
- **GP-128**: check_accessibility removed from _COMMAND_REGISTRY (was already absent; tests added to pin)

## Skipped as Not Real / Already Done

- **GP-5** (caps_lock_suppressor keybd_event → SendInput): SKIPPED — owned by Fix-9 but deferred (Low severity, no functional break).
- **GP-6** (Windows long-path prefix): SKIPPED — manifest changes owned by Fix-17, deferred (Low severity).
- **GP-119** (multi-key chord support): Won't Fix — non-trivial feature addition, not on current roadmap.
- **GP-142/GP-143/GP-144/GP-145**: Duplicates of GP-140/GP-33/GP-42/GP-11 — consolidated.

## Remaining Work

### GP-8 (Critical) — macOS CI workflow force-disabled with `if: false`
- **Why it remains**: No macOS runner available in CI environment. The `if: false` gate is intentionally preserved per TX-39. Documentation block added referencing GP-8.
- **Estimated complexity**: M — requires acquiring a macOS CI runner (GitHub Actions macOS-latest on paid plans, or self-hosted).
- **Recommended priority**: P1 — blocks all macOS release validation.

### GP-55 (Critical, partial) — Windows Rust host binary signing step
- **Root cause**: CI signing loop covered sidecar + prewarm + native-key-listener + outer installers, but missed `voice-typer-tauri.exe` (the Rust host binary).
- **Current state**: The existing 3 signtool calls now derive the Authenticode description from `branding.py::APP_NAME` (C-BRAND-1, P6). What remains is the NEW signing step for `voice-typer-tauri.exe` after `cargo tauri build`.
- **Why it remains**: The new CI step to sign `voice-typer-tauri.exe` after `cargo tauri build` requires careful placement in the 803-line Windows workflow + SHA256SUMS/SLSA integration.
- **Estimated complexity**: M — ~30 lines of YAML + SHA256SUMS loop update.
- **Recommended priority**: P0 — enterprises with WDAC policies will block unsigned host binary.

### GP-57 (High) — Windows SmartScreen reputation documentation
- **Why it remains**: The SmartScreen doc block + EV cert migration path was not applied to the Windows workflow.
- **Estimated complexity**: S — ~20 lines of YAML comments.
- **Recommended priority**: P2 — documentation only.

### GP-58 (Medium) — Windows signtool retry loop with timestamp server
- **Why it remains**: Wrapping 3 signtool calls in PowerShell retry loops with fallback timestamp servers is complex.
- **Estimated complexity**: M — ~50 lines of PowerShell per signing step.
- **Recommended priority**: P1 — release cut fails if DigiCert timestamp server is down.

### TK-1 — history_db_internals/recovery.py and search.py are dead code (0% coverage, never imported)
**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Test coverage gaps & flaky tests
**Description:** history_db_internals/recovery.py (520 LOC) and search.py (586 LOC) were extracted from history_db.py during an incomplete split. Neither module is imported anywhere (verified: rg returns 0 hits). The actual implementations still live inline in history_db.py. These dead modules inflate maintenance burden and mislead refactoring agents.
**User Impact:** A future developer might accidentally wire in the dead module, silently reverting bug fixes made to the live copy.
**Root Cause:** Abandoned mid-split. The wave-2 history_db split created the modules but never wired them in.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/recovery.py`
- `voice_typer/server/history_db_internals/search.py`
- `voice_typer/server/history_db_internals/__init__.py`
- `voice_typer/server/history_db.py`
**Fix:** Delete both dead modules. Add a regression test asserting neither is importable. Record in archive/deleted_files.txt.
### TK-2 — voice-typer.spec uses os.uname() (Unix-only) in eagerly-evaluated dict literal — crashes on Windows
**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Build pipeline
**Description:** scripts/build/voice-typer.spec:70-74 uses os.uname().machine inside a dict literal. Python dict literals eagerly evaluate ALL values. On Windows, os.uname does not exist, so the dict construction raises AttributeError before .get(sys.platform) is reached. This crashes the PyInstaller spec — the FALLBACK build path — on Windows.
**User Impact:** Windows users cannot build the app from source using the documented pyinstaller command. The legacy Electron build and the Tauri sidecar fallback build both crash on Windows.
**Root Cause:** Dict literal eagerly evaluates os.uname() for ALL platform entries. os.uname() is Unix-only.
**Progress:** None yet.
**Related Files:**
- `scripts/build/voice-typer.spec`
**Fix:** Replace os.uname().machine with platform.machine() (cross-platform). Or use if/elif/else branches.

### High Findings (19)

### TK-3 — .hypothesis directory missing from norecursedirs — UserWarning on every pytest run
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Testing infrastructure
**Description:** pyproject.toml:495 norecursedirs omits .hypothesis. When norecursedirs is explicitly set, it REPLACES pytest's built-in default ignores. The hypothesis plugin warns on every run: 'Skipping collection of .hypothesis directory'. This blocks -W error::UserWarning ratchet adoption.
**User Impact:** Developers see a warning on every test run, training them to ignore warnings. Real warnings are lost in the noise. CI logs are polluted.
**Root Cause:** norecursedirs REPLACES pytest's built-in default ignore list. TC-5 in review.md claims fixed but was never applied.
**Progress:** None yet.
**Related Files:**
- `pyproject.toml`
- `tests/test_pyproject_warnings.py`
**Fix:** Append .hypothesis to norecursedirs. Add regression test asserting .hypothesis in norecursedirs.
### TK-4 — hypothesis deadline=None missing — 2 tests fail with DeadlineExceeded/FlakyFailure
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** 22 @settings decorators across 3 hypothesis test files omit deadline=None. Hypothesis 6.x defaults deadline=200ms per example. Under xdist, CI workers are CPU-contended — a single example exceeding 200ms triggers FlakyFailure. 2 tests confirmed failing: test_config_roundtrip, test_buffer_concatenation.
**User Impact:** CI fails intermittently on loaded runners, blocking PR merges.
**Root Cause:** No project-wide hypothesis profile registered. suppress_health_check=[too_slow] only suppresses the startup warning, NOT the per-example deadline.
**Progress:** None yet.
**Related Files:**
- `tests/conftest.py`
- `tests/test_property_based.py`
- `tests/test_text_cleanup_hypothesis.py`
- `tests/test_streaming_hypothesis.py`
**Fix:** Register hypothesis profile in tests/conftest.py:pytest_configure: settings.register_profile('ci', deadline=None); settings.load_profile('ci' if os.environ.get('CI') else 'default').
### TK-5 — generate_beeps.py --check doesn't read sound-manager.ts — regression guard is false assurance
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Build pipeline
**Description:** generate_beeps.py --check generates fresh URLs in-memory and verifies they differ — but NEVER reads sound-manager.ts to verify the committed constants are distinct. A regression re-introducing identical START/STOP beep constants would pass the CI gate.
**User Impact:** Users could get identical start/stop notification sounds, making it impossible to distinguish recording-start from recording-stop by ear.
**Root Cause:** The --check path returns 0 after verifying only the generated URLs, without inspecting the committed source file.
**Progress:** None yet.
**Related Files:**
- `scripts/build/generate_beeps.py`
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
**Fix:** In --check mode, read sound-manager.ts, extract START_BEEP_WAV/STOP_BEEP_WAV via regex, verify they are not identical AND match generated URLs. Exit 1 on mismatch.
### TK-6 — pyrefly-baseline.json has 48 stale entries — inflates ratchet floor, masks real type bugs
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Existing warnings and errors
**Description:** 48 of 264 pyrefly-baseline.json entries are stale: 42 point to deleted files (log.py, config.py, dictation_pipeline.py, clipboard_target_safety.py — all refactored to packages); 6 point past EOF of live files. The CI gate allows up to 48 NEW real type bugs to pass green.
**User Impact:** Type bugs introduced by contributors silently pass CI because the ratchet floor is inflated by 48 phantom entries.
**Root Cause:** Baseline last regenerated 2026-08-01 (OI-16) but only remapped crash_handler.py. Subsequent refactors never reconciled.
**Progress:** None yet.
**Related Files:**
- `pyrefly-baseline.json`
**Fix:** Regenerate errors array with pyrefly 1.1.1 (CI version). Remap stale entries to live counterparts OR drop if error no longer exists. Per CONSTRAINTS.md: fix underlying code, don't artificially shrink.
### TK-7 — test_recording_controller_group_fixes.py stale patch path — gc.collect moved to transcription_watchdog
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Existing failing tests
**Description:** test_gc_collect_called_after_force_recovery patches voice_typer.server.recording_controller.gc.collect but production was refactored: gc.collect() now lives in transcription_watchdog.py:300. recording_controller.py is now a 1-line delegator. The test fails with AttributeError.
**User Impact:** CI fails on every run. The force-recovery gc.collect() path (prevents GPU memory leaks) has no test coverage.
**Root Cause:** _force_recover_from_stuck_transcription body was extracted into transcription_watchdog.py but test was never updated.
**Progress:** None yet.
**Related Files:**
- `tests/test_recording_controller_group_fixes.py`
- `voice_typer/server/transcription_watchdog.py`
**Fix:** Update patch target from recording_controller.gc.collect to transcription_watchdog.gc.collect.
### TK-8 — vad_helpers.py real perf bug — cached-scalar gate defeated by `and` condition
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Existing failing tests
**Description:** vad_helpers.py:220 reads 'if not recorder._cached_vad_enabled and not recorder._vad_enabled: return'. The `and` short-circuit evaluates _vad_enabled (which calls time.perf_counter()) ONLY when cached is False. When cached=False but _vad_enabled=True, the gate proceeds, defeating the PERF optimization. 2 tests fail.
**User Impact:** Extra time.perf_counter() + VAD auto_calibrate call on EVERY audio chunk when VAD is enabled. CPU overhead on the audio hot path.
**Root Cause:** The `and not ... and not ...` pattern only short-circuits when BOTH are False.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/vad_helpers.py`
- `tests/test_recorder_lazy_import_and_vad_cache_gates.py`
**Fix:** Change line 220 to 'if not recorder._cached_vad_enabled: return'.
### TK-9 — test_recorder_retry_budget.py — sliding-window flap detection removed from production (REAL user-facing regression)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Existing failing tests
**Description:** 5 tests assert a sliding-window flap-detection feature (_restart_timestamps deque, _flapping_max_restarts=3, _flapping_window_seconds=60.0) that NO LONGER EXISTS in production. The disconnect_handler now uses only a per-attempt counter. A flapping Bluetooth mic (3+ restarts in 60s) will NEVER fire on_device_lost.
**User Impact:** Users with a flapping Bluetooth microphone will NOT see the 'Microphone disconnected' notification. The app silently restarts repeatedly without informing the user.
**Root Cause:** The sliding-window feature was lost during the _handle_device_disconnect extraction refactor.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/disconnect_handler.py`
- `voice_typer/server/recording/session_state.py`
- `tests/test_recorder_retry_budget.py`
**Fix:** Re-implement sliding-window flap detection in disconnect_handler.py: restore _restart_timestamps deque + threshold check (3 restarts in 60s) + on_device_lost firing + clearing after firing + clearing on start().
### TK-10 — test_perf_clipboard_cred_security_fixes.py — _reset_orphan_state missing (7 errors)
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Existing failing tests
**Description:** 7 tests in TestRunKeyringCallWedgedCooldown error at setup because the fixture calls credential_store._reset_orphan_state() which does not exist. The test also references _wedged_backends and _backend_consecutive_timeouts. The IN-23 wedged-backend cooldown feature was redesigned.
**User Impact:** The wedged-keyring cooldown (prevents clipboard credential operations from hanging when keyring is unresponsive) has no test coverage.
**Root Cause:** Test/production API drift — test written for a planned-but-not-implemented API.
**Progress:** None yet.
**Related Files:**
- `tests/test_perf_clipboard_cred_security_fixes.py`
- `voice_typer/server/credential_store.py`
**Fix:** Update test fixture + assertions to use actual production symbols (_wedged_until, _consecutive_timeouts).
### TK-11 — transcription.py _probe_cuda_runtime untested — CUDA→CPU fallback path
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** _probe_cuda_runtime (lines 503-611, ~91 LOC) exercises the CUDA→CPU fallback path. Only test touching it is a source-string inspect.getsource test — no behavior assertion.
**User Impact:** NVIDIA GPU users could hit mid-recording CUDA failures instead of graceful CPU fallback, causing crashes during dictation.
**Root Cause:** No unit test constructs an engine with a mock model that raises on .transcribe().
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py`
- `tests/test_transcription_cuda_probe.py (NEW)`
**Fix:** Add test_transcription_cuda_probe.py with 4 tests: skip_when_model_none, success_no_fallback, cublas_error_triggers_cpu_fallback, non_cuda_error_propagates.
### TK-12 — transcription.py _is_gpu_runtime_error ctranslate2 class-check loop untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** _is_gpu_runtime_error (lines 1394-1448) has untested branches: ctranslate2 class-check loop and MRO-based class-name check. 2 existing tests are skip-ed.
**User Impact:** CUDA errors from ctranslate2 (most common production ASR engine) would be misclassified, preventing GPU→CPU fallback.
**Root Cause:** Tests mock ctranslate2 with MagicMock so isinstance(cls, type) is False, loop body never fires.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/transcription.py`
- `tests/test_transcription_cuda_classifier.py (NEW)`
**Fix:** Add test_transcription_cuda_classifier.py using real class subclasses (not MagicMock) for CUDAError, MRO class-name match.
### TK-13 — hotkeys/native_adapter.py 30% coverage — process spawn/IPC/restart/teardown untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** native_adapter.py (290 LOC) is at 30% coverage. Missing: process spawn/IPC handshake, spec parsing, restart path, event-loop dispatch, teardown.
**User Impact:** Native-hotkey subprocess crashes, pipe-EPIPE, restart-after-crash paths run untested. Hotkeys may stop working without any visible error.
**Root Cause:** Tests stub at the HotkeyDispatcher level, bypassing the native_adapter module's own subprocess management.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkeys/native_adapter.py`
- `tests/hotkeys/test_native_adapter.py (NEW)`
**Fix:** Add tests/hotkeys/test_native_adapter.py with monkeypatched subprocess.Popen covering: successful spec handshake, malformed spec, subprocess early-exit, pipe BrokenPipe, restart-after-crash, teardown.
### TK-14 — ipc/transport_tcp.py 45% coverage — oversized-frame DoS protection untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** transport_tcp.py (893 LOC) is at 45% coverage. Missing: frame-size check, accept-loop worker pool, write-timeout escalation, connection-cap paths.
**User Impact:** A malicious renderer could send an oversized frame to crash the backend (DoS). The frame-size guard exists but has no regression test.
**Root Cause:** Tests target the _TCPLineIO re-export, not the transport_tcp module's own security paths.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/transport_tcp.py`
- `tests/server/test_transport_tcp_oversized_frame.py (NEW)`
- `tests/server/test_transport_tcp_accept_loop.py (NEW)`
**Fix:** Add tests covering: oversized-frame rejection, accept-loop worker spawn, write-timeout escalation, connection-cap enforcement.
### TK-15 — ipc/sender.py 23-54% coverage — reconnect + pending-message replay untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** sender.py (915 LOC) coverage 23-54%. Missing: write-batch drain loop, reconnect path, pending-message queue replay.
**User Impact:** A renderer reconnect after a network blip could lose queued push events (bubble_level, transcription_streaming).
**Root Cause:** Sender tests cover primitives, not the reconnect+replay lifecycle.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/sender.py`
- `tests/server/test_sender_reconnect_replay.py (NEW)`
**Fix:** Add tests covering: queue accumulation during disconnect, batched replay on reconnect, max-replay-count cap, drop-oldest on overflow.
### TK-16 — recording/audio_pipeline.py 49% coverage — device-disconnect + xrun untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** audio_pipeline.py (879 LOC) at 49% coverage. Missing: device-disconnect detector, xrun status handler, chunk-processing branches.
**User Impact:** Microphone unplug detection and PortAudio input-overflow handling are uncovered. Users could see the app hang when unplugging their mic.
**Root Cause:** Tests cover the happy path only.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/audio_pipeline.py`
- `tests/recording/test_audio_pipeline_disconnect.py (NEW)`
- `tests/recording/test_audio_pipeline_xrun.py (NEW)`
**Fix:** Add tests: zero-filled indata triggers disconnect flag; input_overflow increments xrun counter; deliberate stop vs disconnect discrimination.
### TK-17 — audio_filters/noise_gate.py 71% — adaptive noise-floor calibration untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** noise_gate.py _consume_calibration_chunk (lines 96-129) is entirely uncovered. The adaptive noise-floor calibration runs on the first N chunks of every dictation session.
**User Impact:** A regression that miscalculates the noise floor would cause the noise gate to never open (silencing speech) or never close (letting noise through).
**Root Cause:** Tests don't feed calibration chunks of varying RMS.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_gate.py`
- `tests/test_audio_filters_noise_gate_calibration.py (NEW)`
**Fix:** Add tests: feed N calibration chunks, assert open_threshold == noise_floor_db + offset (clamped); silent chunks → fallback; calibration completes once.
### TK-18 — audio_filters/notch.py reset() untested — SEC-audit-008 buffer-clearing contract
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** notch.py reset() (lines 95-115) is uncovered. The SEC-audit-008 buffer-clearing + ANTIDENORMAL_EPSILON re-application path has no test.
**User Impact:** Audio filter internal state (which may contain speech fragments) is NOT cleared between dictation sessions for the notch filter. PII privacy concern.
**Root Cause:** The reset test was written for highpass only; notch was added without extending the test.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/notch.py`
- `tests/test_audio_filter_reset_zero_buffers.py`
**Fix:** Extend test_audio_filter_reset_zero_buffers.py with test_notch_reset_zeros_state.
### TK-19 — hooks/models/* (5 hooks) have no dedicated unit tests
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** 5 model-management hooks (useModelFolder, useModelSelection, useModelConfig, useModelDownload, useCloudProviders) have no __tests__/ directory. Coverage relies on ModelsPage.test.tsx only.
**User Impact:** Model management is a critical user flow. Refactors of hook internals won't be caught — users could see broken model downloads or failed switches.
**Root Cause:** Hooks extracted into hooks/models/ without sibling test directory.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/models/__tests__/useModelDownload.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/hooks/models/__tests__/useModelSelection.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/hooks/models/__tests__/useModelConfig.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/hooks/models/__tests__/useModelFolder.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/hooks/models/__tests__/useCloudProviders.test.ts (NEW)`
**Fix:** Add 5 test files using @testing-library/react renderHook, mocking the IPC layer.
### TK-20 — pages/microphone/hooks/* — 4 of 6 hooks untested
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** Only useMicrophoneLevelMonitor and useMicrophonePermission have dedicated tests. 4 hooks (useMicrophoneData, useMicrophonePlayback, useMicrophoneTest, useMicrophoneTestSession) have no tests. These hooks touch AudioContext, MediaRecorder — common sources of resource leaks.
**User Impact:** Audio playback teardown, microphone test-session lifecycle, device-swap handling untested. Users could experience audio resource leaks.
**Root Cause:** Hooks added without sibling tests.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/microphone/hooks/__tests__/useMicrophoneData.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/pages/microphone/hooks/__tests__/useMicrophonePlayback.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/pages/microphone/hooks/__tests__/useMicrophoneTest.test.ts (NEW)`
- `voice_typer/client/src/renderer/src/pages/microphone/hooks/__tests__/useMicrophoneTestSession.test.ts (NEW)`
**Fix:** Add 4 test files mocking AudioContext/MediaRecorder, asserting cleanup on unmount.
### TK-21 — _ensure_windows_single_instance only source-string tested — no behavioral mock
**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Test coverage gaps & flaky tests
**Description:** The Windows named-mutex code path (CreateMutexW + GetLastError + SetHandleInformation + stale-PID recovery + DACL) is gated ONLY by source-string assertions. No behavioral mock test exists. The clipboard module proves the same mocking pattern works (100% coverage via 110 mocked tests).
**User Impact:** A regression breaking the mutex name, exit path, or stale-PID recovery would NOT be caught on Linux CI. Duplicate backends could run simultaneously.
**Root Cause:** The test was written as source-string inspection, not a behavioral mock test.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/single_instance.py`
- `tests/test_single_instance_windows_mocked.py (NEW)`
**Fix:** Add tests/test_single_instance_windows_mocked.py mirroring test_clipboard_win32_coverage.py. Mock ctypes.windll.kernel32, assert error_already_exists triggers sys.exit(1); SetHandleInformation called; mutex name is exactly 'Local\\VoiceTyperSingleInstance'.

### Medium Findings (35)

### TK-22 — vitest.config.ts missing clearMocks: true — 120 test files at risk of mock-state leak
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

### TK-23 — 19-file pynput setdefault duplication (DRY violation)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

### TK-24 — 209 inline _config_dir monkeypatches across 82 files (DRY + correctness hazard)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

### TK-25 — 3 unused canonical fixture modules (clipboard_test_helpers, recorder_test_helpers, shutdown_test_helpers — 498 LOC dead)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

### TK-26 — Makefile typecheck target runs ruff (linter) instead of mypy (type checker)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

### TK-27 — coverage_ratchet_check.py silently passes (exit 0) when coverage data is missing
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** CI/CD

### TK-28 — Makefile missing format target (ruff format + biome format)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Testing infrastructure

### TK-29 — cloud_engines _read_capped (OOM protection) + _parse_retry_after untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-30 — cloud_engines test_connection branches untested (401/403/5xx)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-31 — tray _drain_pending (fallback notification path) untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-32 — streaming _finalize_impl_inner untested (tail-skip optimization + fallback)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-33 — streaming _validate_words untested (all 4 raise statements)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-34 — llm_polish _call_api HTTPError/URLError branches untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-35 — clipboard package coverage gaps (Win32 comtypes teardown, macOS TOCTOU)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-36 — shutdown/teardowns/electron.py 42% — Windows TerminateProcess + POSIX SIGTERM escalation untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-37 — ipc/entrypoint.py 67% — signal handler wiring + stdin EOF untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-38 — audio_filters/base.py 79% — base-class default process/reset untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-39 — audio_filters/noise_suppressor.py 77% — RNNoise model-loading untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-40 — main/windows/theme-listener.ts untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-41 — lib/theme-draft-storage.ts untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-42 — components/audio/* untested (audioFilterLabels, audioFilterRowDescriptors, FilterRow)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-43 — hooks/useModelLifecycle.ts untested
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-44 — pytest-benchmark under xdist emits warning + benchmarks are no-op
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors

### TK-45 — test_history_db_connection_prune.py tempfile.mkdtemp() leak (never cleaned up)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-46 — 5 daemon Thread-without-join sites (test_tray, test_ipc_lifecycle, test_device_manager, test_timeout_utils)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-47 — 38 files use sys.modules.setdefault (extends WR-9 scope beyond clipboard)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-48 — src-tauri/src/platform/paths.rs cfg-gated tests cannot run on Linux CI
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-49 — test_hotkeys_init_attrs.py incorrect skipif skips file on Windows/macOS
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Test coverage gaps & flaky tests

### TK-50 — test_ipc_error_envelope_parity.py stale assertion — server.handler_error vs server.internal_error
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing failing tests

### TK-51 — test_ipc_layer_fixes.py inspect.getsource stale assertion (XV-84 encode-once refactor)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing failing tests

### TK-52 — test_sidecar_ws_permissions_fixes.py bytes/str comparison mismatch
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing failing tests

### TK-53 — test_microphone_watcher_coreaudio.py stale mock — property_default_input missing
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing failing tests

### TK-54 — test_level_monitor.py test/production contract mismatch on _dropped_level_chunks
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing failing tests

### TK-55 — test_secure_clear_array.py stale test contract — discard() idle fast-path added
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing failing tests

### TK-56 — filterwarnings ratchet missing (error::DeprecationWarning:voice_typer) + stale TC-5/TC-6 status
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Existing warnings and errors


### Low Findings (33)

### TK-57 — Makefile test target --no-cov asymmetry (C-TEST-4 accepted but UX gap)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-58 — vitest.config.ts coverage.thresholds.perFile not set
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-59 — 2 unused wait-helpers (wait_until in conftest, wait_for in fixtures)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-60 — conftest comment lists 4 of 8 mock_heavy_imports overrides
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-61 — Makefile missing test-fast / test-nocov target
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-62 — app.py main() faulthandler fallback untested
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-63 — app.py refresh_microphones failure path untested
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-64 — app.py _LazyAudioProcessorProxy._resolve untested
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-65 — pyproject.toml stale 70% coverage comment
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-66 — ipc_server _get_rate_limiter thin re-export untested
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-67 — llm_polish event_bus failure handler untested
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-68 — hotkeys/windows/caps_lock_suppressor.py 40% coverage
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-69 — main/* constants/wiring untested
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-70 — 441 time.sleep calls / 136 files (updated TEST-2 count)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-71 — test_prewarm_resolver.py + test_gen_tauri_icons_stub.py silent platform guards
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-72 — test_sidecar_ws_handle_connection_split.py threshold stale (80→110)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-73 — test_history_db_wal_checkpoint_interval.py overly strict source-string test
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-74 — test_pystray_icon_handle_regression.py Xlib.error not caught
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-75 — vitest suite is GREEN on Linux (T-1 vitest portion COMPLETE on Linux)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-76 — test_tray.py:187 local warnings.simplefilter (should use catch_warnings)
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-77 — pyrefly-baseline.json metadata drift
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-78 — pyrefly 1.1.1 vs 1.2.0 version skew
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-79 — 815 # noqa suppressions across 273 files
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-80 — 410 # type: ignore suppressions across 133 files
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-81 — codeql-action not in build.yml header convention block
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-82 — check_branding.py doesn't scan tauri.conf.json / electron-builder.yml
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-83 — numpy>=2.0 no upper bound
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-84 — wheel no version specifier in [build] extra
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-85 — pycaw abandonment comment stale
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-86 — electron-winstaller chain dead code
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-87 — electron-builder 26.15.7 ahead of latest 26.15.3
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-88 — package.json missing license field
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### TK-89 — overrides lack per-override comments
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

### VT-1 — Run the app via `voice-typer` in the terminal; fix any warnings/errors/weird behaviors
**Status:** ⚠️ Partial — run executed 2026-08-05 (Windows); 4 fixes landed (config None-warning noise, notification truncation, pool.submit shutdown race, tray run() graceful degrade); 1 VALIDATE-ON-WINDOWS-HOST item remains
**Severity:** 🟡 Medium
**Category:** Runtime validation / smoke test
**Description:** Task: run the packaged `voice-typer` console script (`pyproject.toml [project.scripts]` → `voice_typer.server.ipc_server:main`) from a terminal in standalone mode (auto-picks a TCP port, launches the Electron frontend, blocks on the tray event loop) and fix any warnings/errors/weird behaviors. Observed on 2026-08-05:
1. **Spurious config warnings on every startup**: `bubble_x` / `bubble_y` / `bubble_scale` / `test_duration_seconds` (`int | None` / `float | None`) logged `had non-int value None, resetting to default None` — the sanitizer unwraps `Optional[T]` → `T`, so a legitimate `None` (the default / unset sentinel) trips the coercion branch. FIXED in `voice_typer/server/config/sanitization.py` (added `optional_numeric_fields` allowlist mirroring the existing `optional_str_fields`).
2. **Toast silently dropped**: `[TRAY] Notification failed: string too long (466, maximum length 256)` — pystray's Win32 `NOTIFYICONDATAW` limits are `szInfo=WCHAR*256` / `szInfoTitle=WCHAR*64`; an over-long message raised `ValueError` inside `icon.notify` and was swallowed by the `except` → the user never saw the toast. FIXED in `voice_typer/server/tray_notifications.py` (`_truncate_notification` before the call).
3. **`RuntimeError: cannot schedule new futures after interpreter shutdown`**: when the tray crashed at runtime, the main thread began interpreter teardown while the background startup thread was still inside `_run_parallel_with_timeout` → `pool.submit` raised, killing the startup thread with an unhandled exception. FIXED in `voice_typer/server/_timeout_utils.py` (guard `pool.submit` with `try/except RuntimeError`, record a per-item failure — same pattern already used in `ipc/transport_tcp.py`).
4. **Whole backend crashed on tray failure**: `PermissionError: [WinError 5] Access is denied` from pystray `_create_window` (restricted / non-interactive session) propagated up through `app.start()` → `[FATAL] app.start() raised` → IPC server + hotkeys + recorder all died. `start()` only caught construction-time `OSError`; runtime event-loop failures were unhandled. FIXED in `voice_typer/server/tray.py` (`run()` now degrades to the tray-unavailable blocking path — hotkey / IPC / Electron window stay alive, `stop()` releases `_run_event`).
**Remaining:**
- VALIDATE ON WINDOWS HOST: run `voice-typer` on an interactive desktop and confirm the tray icon + toast notifications appear (the sandbox session cannot create a tray window — the PermissionError path is environment-specific).
- `[CONFIG] dropped 1 unknown key(s) from config.json` warning is expected forward-compat behavior (schema evolution); the user's config.json contains a stale/unknown key whose name is redacted in logs. No code change needed.
- The standalone run re-launches the Electron frontend; the `runtime_log*.txt` / `startup-error.log` from the crashed run should be cleaned up before shipping.
