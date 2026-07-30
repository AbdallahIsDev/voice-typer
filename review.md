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

### S1-CR-67 — Custom `_RecordingModule` / `_PrewarmModule` / `_ServerPlatformModule` sys.modules hacks
**Status:** ❌ Not Fixed — too large for 10-min sub-agent ceiling (multi-hour/day refactor) — removing _RecordingModule custom class requires migrating 30+ monkeypatch.setattr sites across tests/test_recording.py, tests/test_secure_clear_array.py, tests/test_recorder_*.py to patch submodules directly
- Location: `voice_typer/server/recording/__init__.py:260-349`, `voice_typer/server/prewarm/__init__.py` (289 LOC), `voice_typer/server/server_platform/__init__.py:84-277`
- Evidence: Three packages install custom module subclasses that override `__getattr__` and `__setattr__` so test patches like `monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)` propagate to submodules. ~500 LOC of `__init__.py` boilerplate exists for test-patch compatibility.
- Fix: Migrate tests to patch submodules directly; remove custom module classes and `_pkg.X` indirection. · **Found by**: R1

### S1-CR-78 — IPC protocol is unversioned — schema drift between Python/Rust/TS is undetectable at runtime
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: All IPC frames across `voice_typer/server/ipc/server.py`, `voice_typer/server/sidecar_ws.py`, `src-tauri/src/sidecar/ws.rs`, `src-tauri/src/commands/sidecar_cmds.rs`, `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`
- Evidence: No `protocol_version` field in any frame. If any layer changes the envelope shape, other layers can't detect mismatch at runtime. `server_started` handshake already has a minor inconsistency (uses `event` key instead of `type`).
- Fix: Add `protocol_version` field to the `server_started` handshake and to the auth frame. Have the Rust host check version on connect and fail fast on mismatch. · **Found by**: R3

### S1-CR-143 — macOS `VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1` silently disables key-up delivery and key suppression
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- Location: `voice_typer/server/native/macos-key-listener.swift:462-492`
- Evidence: When env var is set, CGEventTap is never created. No key-up delivery (push-to-talk mode will never fire → recording starts but never stops). No key suppression (Caps Lock as hotkey will toggle OS caps state on every press). No warning logged when active in production.
- Fix: Log a WARNING at binary startup when env var is set; emit `WARN:SKIP_ACCESSIBILITY` line that Python adapter can surface as tray notification. · **Found by**: R17

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

---

### S3-CR-3 — 65+ existing test failures (CI red)
- **Severity:** Critical (CI cannot validate new changes)
- **Status:** Pending
- **Locations:** `tests/test_tray.py` (30), `tests/test_app.py` (10), `tests/test_clipboard_win32_coverage.py` (3), `tests/test_config.py` (4), `tests/test_history_db.py` (2), `tests/handlers/test_system_handlers.py` (1), `tests/regressions/i18n_test.py` (1), `tests/regressions/security_test.py` (1), `tests/test_remaining_fixes.py` (1), `tests/tauri/mig17/test_native_key_listener_linux.py` (3 — TestSidecarOwnership), `tests/tauri/mig19/test_phase4_validation.py` (2 — frozen command registry), `tests/test_i18n_completeness.py` (13 — parity tests), `tests/test_security_doc_command_count.py` (1), `tests/test_electron_ipc_and_build.py` (1 — allowlist parity)
- **Evidence:** Reproduced by R14, R19, R20 — running targeted pytest subsets shows ~65 failures across multiple files. Tests pin private symbols/methods that have been renamed/removed/moved during refactors.
- **Root cause:** Source/tests drift; new commands added without updating frozen allowlists; i18n keys added to en.json without backfilling locales; native_hotkeys refactored from module to subpackage without updating test path assertions.
- **Impact:** CI is red. New refactors can't be validated. Tests provide negative value.
- **Proposed fix:** Per-file triage: restore missing symbols OR update tests if source was intentionally refactored. Backfill 25 missing i18n keys per locale (or add to `RW2_BACKFILLED_PENDING_TRANSLATION`). Update frozen command tables in `test_phase4_validation.py`. Update SECURITY.md command count. Fix native_hotkeys path assertion.
- **Confidence:** High (R14, R19, R20)

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

### S5-CR-26 — `_handle_set_config` reaches into `self.app._waveform_bubble` (private attr) — ADR-0008 §3.1 violation
**Status:** ❌ Not Fixed — proper fix requires files outside scope (voice_typer/server/service.py to add push_bubble_config method + voice_typer/server/providers.py to add to ServiceProtocol — both needed to satisfy tests/test_di_providers.py AST introspection)
- **Severity**: Medium · **Category**: Backend architecture · **Location**: `voice_typer/server/handlers/config_handlers.py:164-166`
- **Proposed fix**: Add `push_bubble_config(config)` to `VoiceTyperService`; encapsulate the private access inside the service.

### S5-CR-28 — `config.py` 2,698 LOC mixes 5 module-level concerns + 132-field Config dataclass
**Status:** ⚠️ Partial (verified on Linux sandbox — 170/170 targeted tests pass; `_backup_before_migration` and `_validate_non_numeric_fields` extractions still pending)
**This Run Fix:** Path-safety helpers extracted to new voice_typer/server/config_path_safety.py flat module (75 LOC); config.py imports from it (re-export preserves public API).
**Remaining extraction feasibility:**
1. **`_backup_before_migration`** (~70 LOC, line 1760) — Can extract cleanly to `config_internals/migrations.py`. Calls only module-level functions; no Config class coupling.
2. **`_validate_non_numeric_fields`** (~120+ LOC, line 2304) — Tightly coupled to Config internals: calls `cls._derive_field_type_registry()`, `cls._warn_and_coerce()`, `cls._warn_and_reset()`. Extraction requires either (a) passing those as parameters, or (b) a larger refactor of validator dependency chain.
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/config.py:1-1819`
- **Proposed fix**: Extract `secure_file_io.py`, `path_safety.py`, `systemroot_validation.py`, `_backup_before_migration` → `config_internals/migrations.py`; absorb `_validate_non_numeric_fields` into existing `config_validators.py`. `config.py` thin ≤600 LOC.

### S5-CR-56 — macOS sidecar/prewarm binaries ship unsigned (Nuitka `--macos-signed-app-name` doesn't codesign)
**Status:** ❌ Not Fixed — out of scope (file cited in finding is owned by another agent or outside this agent's file list)
- **Severity**: Medium · **Category**: Cross-platform / Packaging · **Location**: `scripts/build/build_sidecar_macos.sh:128-140` and `scripts/build/build_prewarm_macos.sh:88-100`
- **Proposed fix**: In both `build_sidecar_macos.sh` and `build_prewarm_macos.sh`, when `$MAC_SIGNING_IDENTITY` env var is non-empty, append `--macos-sign-identity="$MAC_SIGNING_IDENTITY"` to the Nuitka args. When empty, fall back to ad-hoc `codesign --force --sign -` on the output binary (mirroring `build_native_listener_macos.sh`). Update runbook §7.2 to remove the false claim.

---

### H-25 — Doc file path references stale (recording.py / hotkeys.py / prewarm.py don't exist as files — now packages)
- **Severity**: High
- **Status**: ⚠️ Partial — ADR-0020 and `docs/rw04-recording-decomposition.md` are updated. But 6 docs still reference old monolithic paths: `docs/Qwen_integation.md:39` (recording.py), `docs/native-hotkey-architecture-plan.md:24` (hotkeys.py), `docs/migration/macos-validation-runbook.md:144` (prewarm.py), `docs/migration/windows-validation-runbook.md:452` (prewarm.py), `docs/adr/0011-prewarm-architecture-analysis.md` (extensive prewarm.py refs), `docs/adr/0013-desktop-runtime-migration-analysis.md:212` (prewarm.py). NOTE: README.md, CONTRIBUTING.md, docs/ARCHITECTURE.md no longer contain stale refs.
- **Category**: Documentation
- **Location**: `docs/adr/0011`; `docs/adr/0013:212`; `docs/migration/*validation-runbook.md`; `docs/Qwen_integation.md:39`; `docs/native-hotkey-architecture-plan.md:24`

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

## XA-1 — TitleBar mixes 3 icon systems, missing hover/transition classes on sidebar toggle, parallel button system

**Status:** ⚠️ Partial (verified on Linux sandbox; sub-items 1-3 fixed (sidebar hover, duration-150, ring-3) + strokeWidth=2 standardization; sub-items 4,6 deferred — ThemeSwitch/Sidebar.test outside scope)
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

## XA-2 — Pages use inconsistent loading/empty/error patterns; EmptyState variant="error" is dead code

**Status:** ⚠️ Partial (verified on Linux sandbox; ErrorVariant Storybook story added to EmptyState.stories.tsx; items 4-7 deferred — require editing non-owned files)
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

## XA-3 — UI primitives: inconsistent focus ring, hardcoded palette colors, parallel layout systems

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with two 🔴 High sub-items)
**Description:** (XA-3-1) Focus ring inconsistency: core primitives (Button/Input/Select/Slider/Switch/Accordion) use `focus-visible:ring-3 focus-visible:ring-ring/30`; SegmentedControl/NumberInputStepper/KeyringStatusBadge/SearchField/InfoTooltip/ThemeSettingsSection-contrast-button use `focus-visible:ring-2 focus-visible:ring-ring/50` or `/30` — thinner+more opaque vs thicker+lighter. (XA-3-2) Hardcoded Tailwind palette colors in 4 settings/common files: SettingsSaveIndicator (`bg-amber-400/bg-sky-400/text-emerald-500`), PrewarmAndUpdates, KeyringStatusBadge, ThemeSettingsSection. No semantic `--success`/`--warning`/`--info` tokens — custom themes can't recolor status indicators. (XA-3-3) Three settings sections double-nest `divide-y divide-border` + apply `animate-fade-in` inconsistently. (XA-3-4) Bespoke action-button rows: 3 variants of `flex flex-wrap items-center gap-2 px-3.5 py-3.5 border-t border-border`. (XA-3-5) `PrewarmAndUpdates` defines private `Row` component duplicating `SettingRow`. (XA-3-6) `ThemeSettingsSection` uses bespoke segmented-control + reset button instead of primitives. (XA-3-7) `RangeSlider` thumb uses hardcoded `bg-white` instead of `bg-background` — breaks dark mode. (XA-3-8) `ExportFormatMenu` overrides DropdownMenu primitive's content + item styling (preserves "pre-migration" visual). (XA-3-9) `Settings.tsx:385` "Clear search" button is a hand-rolled `<button>` duplicating Button's outline variant. (XA-3-10) Dead/unused prop variants: Button `size: "lg"/"icon"/"icon-lg"` (zero callers), `SettingRow.align`, `SegmentedControl.activeClassName`, `ConfirmDialog.variant="warning"` (no-op), `DialogClose`/`SelectGroup`/`SelectLabel`/`SelectSeparator` (zero callers), 11 unused DropdownMenu sub-components. (XA-3-11) `aria-label` vs `ariaLabel` prop naming inconsistent across primitives (RangeSlider/SegmentedControl use camelCase, others use JSX `aria-label`). (XA-3-12) `NumberInputStepper` disabled opacity (`opacity-30`) differs from every other primitive (`opacity-50`). (XA-3-13) `SettingRow` uses `<span>` for label (not `<label htmlFor>`) — parallel a11y system requires every caller to manually duplicate label as `aria-label`. (XA-3-14) ThemeSettingsSection fallback hex codes bypass theme token system. (XA-3-15) SegmentedControl default tabs indicator unsuitable — both production callers override.
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

## XA-4 — Settings: search filtering inconsistent, save indicator missing i18n + error state, toast spam on every save

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with one 🔴 High sub-item)
**Description:** (XA-4-1) AudioSettingsSection skips per-row search filtering — entire section shows when any row matches. (XA-4-2) Search shows Audio section but no matching row when preset ≠ "custom" (advanced filter labels in sectionItems don't correspond to rendered rows). (XA-4-3) Settings search auto-switches tabs without notice/undo/opt-out — disorienting. (XA-4-4) "Pending…" save-indicator string is hardcoded English literal — untranslated in all 8 locales. (XA-4-5) RecordingSettingsSection NumberInputStepper inline error messages and range hints are hardcoded English ("Range: 3–30 s", "Enter a whole number", "Must be between 3 and 30"). (XA-4-6) Hidden conditional Bubble rows ("Show on Startup", "Bubble Mic Button") break search-filter consistency. (XA-4-7) "Reset to Defaults" confirmation dialog is generic — doesn't disclose scope (theme, hotkeys, consents, audio chain) and doesn't distinguish what's preserved. (XA-4-8) "Re-run Setup Wizard" and "Reset to Defaults" buttons share identical `RefreshIcon` — visual confusion near destructive action. (XA-4-9) Destructive Reset button has no visual separator from 5 non-destructive buttons. (XA-4-10) Every successful save fires BOTH a transient snackbar toast AND the sticky "Saved ✓" indicator — redundant and noisy. (XA-4-11) General tab has 19 settings rows across 3 sections — cognitive overload. (XA-4-12) No per-row "modified" indicator during pending saves. (XA-4-13) "Agree to All" privacy button grants 6 distinct consents with no confirmation. (XA-4-14) SettingsSkeleton doesn't reflect section structure — uniform row placeholders flatten visual hierarchy during load. (XA-4-15) Manual "Refresh" button provides no visible feedback that the refresh happened if nothing changed.
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

## XA-5 — Feature pages: friction in add flows, missing test/preview, no inline retry for failed downloads, Audio preset buried

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

## XA-6 — Floating bubble has no in-bubble stop/cancel/pause, no live transcription, dead error UI, broken multi-monitor, theme sync missing
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

## XA-7 — Accessibility: no focus management on page navigation, modal focus contract untested, HotkeyPicker i18n gap, theme preview hover-only
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 2 High sub-items)
**Description:** (XA-7-1) **High:** No focus management on page navigation — `<main id="main-content" tabIndex={-1}>` exists but is never focused after `navigate(...)`. Screen-reader/keyboard-only users are left with focus on the trigger; must manually Tab forward. (XA-7-2) **High:** Modal/AlertDialog/ConfirmDialog focus-trap, Escape, and focus-restore behavior is unverified by tests — only source-pattern checks. (XA-7-3) Medium: HotkeyPicker "Clear" button and "Holding:" label are hardcoded English (acknowledged in source comment). (XA-7-4) Medium: Theme preset dropdown live-preview is hover-only — keyboard users navigating with ArrowUp/Down don't fire `onMouseEnter`. (XA-7-5) Medium: Bubble position cannot be adjusted via keyboard (PVT-048 known but unfixed — 7 tests `describe.skip`'d). (XA-7-6) Medium: Help overlay (Modal) has no visible close button. (XA-7-7) Low-Medium: `<main>` skip-link target has `focus:outline-none` — sighted keyboard users get no visual confirmation focus moved. (XA-7-8) Low: Document-level Ctrl+B/Ctrl+, shortcuts fire while Radix Modal is open (`?` handler has the guard, Ctrl+* handler does not). (XA-7-9) Low: HotkeyPicker capture mode is a keyboard trap (intentional, but lacks upfront AT announcement — current announcement is sufficient). (XA-7-10) Low: axe-core coverage gaps — Onboarding skipped (OOM), Models + Dashboard known-failing. (XA-7-11) Low: Templates/Vocabulary textarea uses weaker focus indicator than Input component. (XA-7-12) Low: Help-overlay Escape handling has redundant document-level + Radix handlers. (XA-7-13) Low: Sidebar roving tabindex has no on-screen hint about arrow-key navigation. (XA-7-14) Low: ConfirmDialog AlertDialog contract (Escape + outside-click suppression) is unverified by tests.
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
1. **(XA-7-1)** In `App.tsx`, add `useEffect` keyed on `currentPage` that calls `document.getElementById("main-content")?.focus()` after a tick; pair with `aria-live="polite"` announcement of new page name.
2. **(XA-7-2)** Add `Modal-a11y.test.tsx` + `ConfirmDialog-a11y.test.tsx` covering Tab cycling, Escape close, focus restore, AlertDialog suppression of outside-click/Escape, initial focus on Cancel.
3. **(XA-7-3)** Add i18n keys `hotkeyPicker.clearAria`/`clearTitle`/`holdingPrefix` to all 8 locale files; replace literals.
4. **(XA-7-4)** Add `onFocus` handler on each `SelectItem` to call `handleThemeHover(theme.id)` for keyboard navigation.
5. **(XA-7-6)** Add `<DialogClose asChild><Button variant="ghost" size="icon-sm" aria-label={t("common.close")}>…</Button></DialogClose>` to Modal header (or `Modal.tsx` itself via `showCloseButton?: boolean` prop).
6. **(XA-7-7)** Replace `focus:outline-none` on `<main>` with `focus:outline-none focus:ring-2 focus:ring-ring/30 focus:ring-offset-2`.
7. **(XA-7-8)** Add `document.querySelector('[role="dialog"][data-state="open"]')` guard to Ctrl+* handler in `App.tsx:194`.
8. **(XA-7-11)** Add `focus-visible:ring-3 focus-visible:ring-ring/30` to textarea className in Templates/Vocabulary (or extract shared `Textarea` component).
9. **(XA-7-10)** Split Onboarding component to reduce dep graph so axe test can run; promote Models consent `<h3>` → `<h2>`; add `role="progressbar"` to Dashboard loading `<div>`.

---

## XA-8 — ARIA: EmptyState no role, NumberInputStepper + ErrorBoundary hardcoded English aria, KeyringStatusBadge redundant aria, SegmentedControl icon-only unlabeled, SearchField no role=search, sonner aria hardcoded English
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 1 High sub-item)
**Description:** (XA-8-H1) **High:** `EmptyState` wrapper has no `role` attribute — used by 5 pages for empty/error states; SR users get no announcement on transition. (XA-8-M1) Medium: `NumberInputStepper` aria-label="Increment"/"Decrement" are literal English (existing `a11y.increase`/`a11y.decrease` keys unused). (XA-8-M2) Medium: `ErrorBoundary` 5 newer strings ("Copied!"/"Copy error"/"Open logs"/"Resetting…"/"Reset settings"/"Backend reset failed…") are hardcoded English. (XA-8-M3) Medium: `KeyringStatusBadge` aria-label duplicates `<TooltipContent>` text — SR users hear it twice. (XA-8-M4) Medium: `SegmentedControl` icon-only options have no `aria-label` (only `title`, which JAWS often ignores). (XA-8-M5) Medium: `SearchField` wrapper is plain `<div>` with no `role="search"` — SR users can't navigate via "search" landmark. (XA-8-M6) Medium: `sonner` Toaster aria-label="Notifications" + close button aria-label="Close" hardcoded English (sonner library default). (XA-8-L1 through L7) Low: Slider/Switch/Button primitives don't enforce aria-label (latent risk); InfoTooltip SVG has redundant `<title>`; LastUpdatedIndicator not in aria-live region; Spinner nested inside labeled button creates redundant live region; LevelBar relies on sibling LiveQualityFeedback for SR announcements (tight coupling).
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

## XA-10 — Onboarding: missing i18n keys step4Item/step5Item (raw key strings on Welcome screen), completeDescription never rendered, setupCompleteSnack never wired, modelSelectAria not interpolated
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

## XA-12 — Recording flow: silent failure modes, no live transcription, swallowed IPC errors, no pause/resume, 61s crash detection delay
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


## XA-15 — Test infrastructure: dead helpers (590 lines), 3 duplicated baseConfig fixtures, mock boilerplate in 21-34 test files, orphan debug test
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** (XA-15-1) Medium: `__tests__/helpers/` directory (3 files, 590 lines) is dead code — zero test files import from it. (XA-15-2) Medium: `makeConfig()` fixture exists but 3 test files roll their own ~120-line `baseConfig` (Settings.test.tsx, Settings-empty-state.test.tsx, debug-test.test.tsx). (XA-15-3) Medium: Mocks duplicated across 21-34 test files instead of using `helpers/mocks.tsx` factories. (XA-15-4) Medium: Per-test setup boilerplate (`localStorage.clear()` + `cleanup()` + `mockReset()`) duplicated across 16+ test files. (XA-15-5) Medium: Orphan debug test file `debug-test.test.tsx` (242 lines) with `expect(true).toBe(true)` tautology + 4 `console.log` calls. (XA-15-6) Medium: Duplicate RTL test files (`rtl.test.ts` and `rtl.test.tsx`) with 4/5 identical test cases. (XA-15-7) Medium: CONTRIBUTING.md §7.2 doesn't mention the shared test helpers. (XA-15-8) Medium: Flaky timing patterns — 9+ test files use bare `setTimeout` waits instead of `vi.waitFor`/`findBy*`/`vi.useFakeTimers`. (XA-15-9) Medium: Mega-test-file `ux-components-behavior.test.tsx` (1752 lines) mixes 7+ component concerns. (XA-15-10) Low: `#ui` path alias declared in 6 config files but ZERO usages in code. (XA-15-11) Low: `#utils` alias duplicated identically across 6 config files. (XA-15-12) Low: `biome.json` `overrides` block has no `include` field (no-op indirection). (XA-15-13) Low: `__tests__/` directory has no README; `rw0-rewrite/`/`rw1-rewrite/` jargon undocumented. (XA-15-14) Low: Coverage threshold inconsistency: vitest 70% vs Python 65% vs docs 65%. (XA-15-15) Low: `tsconfig.web.json` has redundant `*.json` include pattern. (XA-15-16) Low: `test-setup.ts` mixes polyfills with one mock stub (`window.bubble`).
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
4. **(XA-15-2)** Replace each `baseConfig` with `import { makeConfig } from "@/__tests__/helpers/fixtures"; const baseConfig = makeConfig({...});`.
5. **(XA-15-12)** Either inline `suspicious: { noConsole: "off" }` into main `linter.rules` block OR add `include: ["src/main/**"]`.
6. **(XA-15-10)** Remove `#ui` alias from all 6 config files.
7. **(XA-15-15)** Remove redundant `"src/renderer/src/**/*.json"` line from `tsconfig.web.json:26-30`.
8. **(XA-15-14)** Update `CONTRIBUTING.md` §7.2 to say "≥ 70% (vitest) / 65% (pytest)".
9. **(XA-15-13)** Add `__tests__/README.md` explaining layout (co-locate next to source; `rw0-rewrite/`/`rw1-rewrite/` are FROZEN historical migration rounds; `helpers/` is shared test infrastructure).
10. **(XA-15-7)** Add §7.2.1 "Shared test helpers" subsection to CONTRIBUTING.md.
11. **(XA-15-8)** Replace `await new Promise((r) => setTimeout(r, N))` with `waitFor`/`findBy*`/`vi.useFakeTimers`.
12. **(XA-15-1)** Migrate `App.test.tsx`, `App-ux-fixes.test.tsx`, `App-a11y.test.tsx`, etc. to use `renderApp` + `makeConfig` + `makeToastMock` + `makeHugeiconsReactMock`. (Larger refactor — partial in this run.)

---

## XA-16 — Error handling UX: ErrorBoundary 6 hardcoded English strings, EmptyState variant dead, Parakeet success toast wrong message, no in-context bug report
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 1 High sub-item)
**Description:** (XA-16-1) **High:** ErrorBoundary fallback hardcodes 6 English strings ("Copied!"/"Copy error"/"Open logs"/"Resetting…"/"Reset settings"/"Backend reset failed — clearing local state and reloading anyway." + tooltip) — non-English users see mixed-language crash screen. (XA-16-2) Medium: `EmptyState variant="error"` is defined but NEVER used (re-used from XA-2). (XA-16-3) Medium: Parakeet deps SUCCESS toast shows the WRONG message — `showSnack(t("models.snack.parakeetDepsRequired"), "success")` displays "Dependencies required for Parakeet. Download first." as a green success toast. (XA-16-4) Medium: ErrorBoundary has no in-context "Report bug / Send feedback" path. (XA-16-5) Medium: `handleRetryConnection` makes a single attempt with no backoff — health-check path has `HEALTH_CHECK_MAX_RETRIES = 2` with 500ms backoff, manual retry path does not. (XA-16-6) Medium: Inconsistent toast lifetimes — many call sites bypass `useSnackbar` (canonical durations: success=3000/info=4000/warning=6000/error=8000) and use sonner's default 4000ms for errors. (XA-16-7) Medium: `globalErrorHandler` shows the SAME generic toast for every unhandled error. (XA-16-8) Medium: ErrorBoundary tests do not cover the PVT-fix #11 recovery features. (XA-16-9) Low-Medium: `useSnackbar` has NO unit tests. (XA-16-10) Low: Redundant double `<ErrorBoundary>` wrap (main.tsx outer + App.tsx inner). (XA-16-11) Low: `showSnack` defaults `type` to `"success"` — footgun in catch blocks. (XA-16-12) Low: No error codes anywhere; backend `code` field is stripped before display. (XA-16-13) Low: `clearSnack` dismisses ALL toasts, not "the current snackbar". (XA-16-14) Low: `LastUpdatedIndicator` has no error state. (XA-16-15) Low: `KeyringStatusBadge` plaintext warning has no "Learn how to fix" link. (XA-16-16) Low: Three overlapping log files with two parallel loggers. (XA-16-17) Low-Medium: `ErrorBoundary` does not distinguish recoverable vs fatal errors. (XA-16-18) Low: `EmptyState` stories don't cover the error variant. (XA-16-19) Low: `App-help-overlay.test.tsx` mocks `ErrorBoundary` to a passthrough, masking integration regressions.
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
6. **(XA-16-2 + XA-16-18)** Add `variant="error"` to all 4 load-failed EmptyState instances; add `ErrorVariant` Storybook story.
7. **(XA-16-8)** Add ErrorBoundary tests for `handleCopyError`/`handleOpenLogs`/`handleResetSettings`/`componentDidCatch` forwarding.
8. **(XA-16-17)** Add `level: "fatal" | "page"` prop to ErrorBoundary; wrap each page in `renderPage()` with `<ErrorBoundary level="page">`.
9. **(XA-16-12)** Preserve `code` on thrown Error as `err.code`; prepend `[VT-{code}]` in `formatErrorMessage`.

---

## XA-17 — Hooks & state: useTheme split-brain, useConnection 5+ concerns, no focus management, prop-drilling persists
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

## XA-20 — RTL/locale formatting: tChoice unused, untranslated strings, physical CSS properties, runtime locale-isolation between main window and bubble, no platform-aware shortcuts
**Status:** ⚠️ Partial (verified on Linux sandbox; XA-20-1 useTChoice() React hook added to i18n.ts to lower barrier for CLDR pluralization; 21 sub-items deferred)
**Severity:** 🟡 Medium (with 2 Critical + 5 High sub-items)
**Description:** (XA-20-1) **Critical (re-used from XA-18-3):** `tChoice()` exists but is NEVER called anywhere; all plurals use broken binary `Singular`/`Plural` keys. (XA-20-2) **Critical:** Multiple translation files contain UNTRANSLATED English text for high-visibility strings (permissions block, relativeTime, model snack errors, about.creditsDescription) in ar/hi/ru/de/zh/es/fr. (XA-20-3) **High:** Sidebar nav item uses physical `border-l-2`/`border-l-accent`/`border-l-transparent` despite the indicator pill using logical `inset-s-0`. (XA-20-4) **High:** `SearchField` uses physical `left-3`/`right-3`/`pl-9` for the search icon, clear button, and input padding. (XA-20-5) **High:** Select and DropdownMenu primitives use physical `pr-8 pl-3` + `absolute right-2` for the chevron, and `ml-auto` for shortcut text / checkmark. (XA-20-6) **High:** `Dashboard.tsx` has its own LOCAL `formatDuration` that hardcodes English "h"/"m" labels; ignores the locale-aware `formatDuration` from `lib/format.ts`. Same in `StatCards.tsx`. (XA-20-7) **High:** `DownloadProgressBar.tsx` has its own LOCAL `formatBytes`/`formatSpeed` with hardcoded English unit labels; ignores locale-aware versions in `lib/format.ts`. (XA-20-8) Medium: `lib/format.ts` `formatDuration` fallback (when `Intl.DurationFormat` is unavailable) uses hardcoded English "h"/"m"/"s". (XA-20-9) Medium: Legacy `compactNumber` (still used by Dashboard + StatCards) hardcodes "K" suffix and uses Latin digits; the locale-aware `formatCompactNumber` exists but is unused. (XA-20-10) Medium: Bubble BrowserWindow does not receive locale-change notifications; it keeps the OLD locale (and OLD `dir` attribute) until next mount. (XA-20-11) Medium: Sonner (toast/snackbar) is hardcoded to `position="bottom-right"`; does not flip in RTL. (XA-20-12) Medium: Dialog header uses `sm:text-left` (physical alignment) instead of `sm:text-start` (logical). (XA-20-13) Medium: `formatHotkey()` uses hardcoded English modifier labels (does not use existing `hotkey.modifiers.*` translation keys). (XA-20-14) Medium: Static shortcut strings in TitleBar/Sidebar/help overlay are NOT platform-aware (macOS users see "Ctrl+B" instead of "Cmd+B"). (XA-20-15) Low: Onboarding `<ol className="ml-4 list-decimal">` uses physical left margin. (XA-20-16) Low: `StatsShareImage` uses physical `marginLeft` for unit label spacing despite setting `direction: rtl`. (XA-20-17) Low: SegmentedControl icon margin `"-ml-0.5 mr-1"` is physical. (XA-20-18) Low: TitleBar back/forward arrow icons are hardcoded physical paths; not mirrored in RTL. (XA-20-19) Low: Python tray menu has no RTL handling; relies entirely on the OS to detect direction. (XA-20-20) Low: `index.css` has zero RTL-specific CSS rules. (XA-20-21) Low: RTL test coverage is limited to `dir` attribute flipping; no assertions about visual/DOM-level RTL behavior. (XA-20-22) Low: `useLastUpdated` hook uses non-pluralized templates that won't render grammatically correct for Slavic/Semitic languages.
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
1. **(XA-20-3)** Change `border-l-2` → `border-s-2`, `border-l-accent` → `border-s-accent`, `border-l-transparent` → `border-s-transparent` in Sidebar.tsx.
2. **(XA-20-4)** Change `left-3` → `start-3`, `right-3` → `end-3`, `pl-9` → `ps-9` in SearchField.tsx.
3. **(XA-20-5)** Change `pr-8 pl-3` → `pe-8 ps-3`, `absolute right-2` → `absolute end-2`, `ml-auto` → `ms-auto` in select.tsx + dropdown-menu.tsx.
4. **(XA-20-12)** Change `sm:text-left` → `sm:text-start` in dialog.tsx:67.
5. **(XA-20-15)** Change `ml-4` → `ms-4` in Onboarding.tsx:205.
6. **(XA-20-16)** Change `marginLeft` → `marginInlineStart` in StatsShareImage.tsx:151, 198.
7. **(XA-20-17)** Change `-ml-0.5 mr-1` → `-ms-0.5 me-1` in segmented-control.tsx:352.
8. **(XA-20-18)** Add `rtl:-scale-x-100` to TitleBar back/forward SVG paths.
9. **(XA-20-6)** Remove local `formatDuration` from Dashboard.tsx:57-64; add `formatDuration` to import from `@/lib/format`. Same for StatCards.tsx:17-25.
10. **(XA-20-7)** Remove local `formatBytes`/`formatSpeed` from DownloadProgressBar.tsx:25-45; import from `@/lib/format`.
11. **(XA-20-11)** Compute position from `isRtlLocale(getLocale())`: `position={isRtlLocale(getLocale()) ? "bottom-left" : "bottom-right"}`; wrap with `useT()`.
12. **(XA-20-14)** Compute displayed shortcut string via `formatHotkeyLabel("<ctrl>+<b>")` (returns `⌃B` on macOS, `Ctrl+B` elsewhere); update `aria-keyshortcuts` to platform-correct ARIA format.
13. **(XA-20-22)** Replace template lookup in `useLastUpdated.ts:128-152` with call to `formatRelativeTime` from `lib/format.ts`.
14. **(XA-20-10)** Add Electron main-process IPC channel `locale:changed`; main window emits when `setLocale()` runs; `bubble-window.ts` subscribes and either calls `webContents.send("locale:changed", locale)` (bubble preload calls `setLocale(locale)`) OR calls `win.reload()`.
15. **(XA-20-2)** Translate missing keys in each locale file (overlaps with XA-18-4).
16. **(XA-20-1)** Migrate pluralized strings to `tChoice()` (overlaps with XA-18-3).
17. **(XA-20-21)** Add `i18n/__tests__/rtl-render.test.tsx` mounting real components under `dir="rtl"` to assert visual/DOM-level RTL behavior.

---

## Summary

| Severity | Count |
|---|---|
| Critical | 8 (XA-6, XA-9, XA-10, XA-12, XA-13, XA-14, XA-20 ×2) |
| High | 30+ |
| Medium | 80+ |
| Low | 60+ |
| **Total findings** | **~250+** |

**Top-priority fixes for Phase 4 implementation wave (by impact × confidence):**
1. XA-10-1 + XA-11-1: Add `onboarding.step4Item`/`step5Item` i18n keys (Critical — first-impression regression on Welcome screen)
2. XA-12-1: Extend `status_change` event payload to include message; populate `lastError` (Critical — unblocks 5 other findings)
3. XA-13-C1: Unpack `download_parakeet_weights()` return value (Critical — silent false success)
4. XA-13-C2 + C3: Implement `install_parakeet_deps`/`get_disk_info`/`open_models_folder`/`models_folder_supported` IPCs OR remove dead UI (Critical — dead buttons)
5. XA-14-1: Port QUIT-FLUSH-FIX from `useTheme.ts` to `useSettingsConfig.ts` (Critical — silent data loss)
6. XA-9-1 + XA-9-2 + XA-9-3 + XA-9-4: Fix focus ring + border + primary-foreground + muted-foreground contrast (Critical/High — WCAG violations)
7. XA-6-1 + XA-6-3 + XA-6-7: Bubble stop affordance + error state + theme sync (Critical/High — bubble unusable)
8. XA-4-4 + XA-19-7: Add `settings.pending` i18n key (Medium — i18n regression)
9. XA-5-6 + XA-5-16: Cancel-download confirm + `models.download.oneAtATime` key (High/Medium — destructive without undo)
10. XA-7-1: Focus management on page navigation (High — WCAG violation)
11. XA-8-H1: Add `role` to EmptyState (High — WCAG 4.1.3 violation)
12. XA-16-1 + XA-8-M2: ErrorBoundary i18n keys (High — mixed-language crash screen)
13. XA-11-2: Add `.onboarding_started` marker check to `startup_sequence.py` (High — auto-heal incorrectly fires mid-wizard)
14. XA-11-3 + XA-11-4 + XA-11-5: Fix 3 broken tests (High — CI red)
15. XA-20-3 through XA-20-18: RTL physical→logical property migration (High — RTL broken)
16. XA-20-6 + XA-20-7: Use shared `formatDuration`/`formatBytes`/`formatSpeed` (High — locale formatting broken)

**Platform qualifier:** All findings investigated ON LINUX (sandbox). Windows/macOS runtime validation pending where noted (XA-6-4 multi-monitor, XA-7-5 bubble keyboard, XA-9-11 forced-colors mode, XA-18-1 native dialogs, XA-20-19 tray RTL).
```

### Findings from Session 4
```
# Consolidated Comprehensive Review — XZ Session (Group 4: Security & Data)

> Append-only. Findings use `XZ-` prefix to avoid collision with prior sessions. Deduplicated against existing entries.
> **Independent verification (2026-07-25):** All entries below still ❌ Not Fixed. No XZ-session code changes were applied in the working tree. The XZ session's committed fixes (Nuitka + prewarm only) do not address these findings.

## Summary

20 parallel review sub-agents investigated Group 4 (Security & Data) categories: Security, Privacy & data protection, Data integrity & persistence, Configuration management, Error handling, Error recovery & resilience, Logging consistency. ~170 findings returned; consolidated below by file scope to drive 20 disjoint implementation sub-agents.

**Severity counts (XZ session):**
- Critical: 1 (XZ-SEC-01)
- High: ~30
- Medium: ~50
- Low: ~90

**Platform validation:**
- Linux sandbox: validated natively
- Windows/macOS: code+tests implemented; host validation deferred with explicit steps

---


## XZ-SEC-02 — `_write_plaintext_fallback` skips `config.json.lock` (High)

**Status:** ❌ Not Fixed
**Description:** `credential_store.py:721-770` does a read-modify-write on `config.json` without acquiring `config.json.lock`. `Config.save()` and `migrate_secrets_to_keyring` both acquire the lock — only the plaintext fallback path skips it. Concurrent `delete_secret` vs `Config.save()` race loses the delete silently; plaintext API keys survive on disk despite user's delete request.
**Root Cause:** Missing `_acquire_config_lock()` wrapping.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/config.py` (for `_acquire_config_lock` import)
**Fix:** Wrap the read-modify-write body in `with _acquire_config_lock():`. Re-read config.json INSIDE the lock. Add regression test for concurrent `delete_secret` + `Config.save()`.
**Severity:** 🔴 High

## XZ-SEC-03 — `config.json.bak` not in GDPR delete set (High)

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

### XZ-IPC-009 — Stale line-number references in comments (Low)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:952, 1573, 2445` — comments reference wrong line numbers (e.g. "set at line ~1171" but actual is line 861).
**Related Files:** `voice_typer/server/ipc_server.py`**Fix:** Replace line-number references with function/label references.
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

---

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

### XZ-CLIP-14 — Redundant safety check (Low)
**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:759, 773` — when `paste_delay == 0`, check #2 runs immediately after check #1 (redundant UIA round-trip).
**Related Files:** `voice_typer/server/clipboard/manager.py`**Fix:** Guard check #2 with `if paste_delay > 0:` so it only runs after actual sleep.
**Severity:** 🟢 Low


### XZ-PII-07 — Log retention: no time-based purge (Low)
**Status:** ❌ Not Fixed
**Description:** `log.py:674-680` rotation is size-only (5 MiB × 5). No `TimedRotatingFileHandler`, no startup sweep. Compare to `crash_handler._sweep_stale_diagnostics` (30-day mtime cutoff for crash diagnostics).
**Related Files:** `voice_typer/server/log.py`**Fix:** Add startup sweep (mirror `_sweep_stale_diagnostics`) deleting `voice-typer.log.*` rotations older than 30 days. OR switch to `TimedRotatingFileHandler` with daily rotation + 30-day retention.
**Severity:** 🟢 Low

### XZ-R10-03 — Pre-migration backup uses `shutil.copy2` (High)
**Status:** ❌ Not Fixed
**Description:** `config.py:1297-1308` `shutil.copy2(config_file, pre_bak)` follows symlinks on destination, non-atomic, no fsync. Same vulnerability class as XZ-R10-02.
**Related Files:** `voice_typer/server/config.py`**Fix:** Read source via `_secure_read_text(config_file)` and write via `_secure_atomic_write(pre_bak, raw_text)`.
**Severity:** 🔴 High

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

---

### XZ-R11-01 — `save_vocabulary_with_diff` doesn't update in-memory VocabularyManager (High)
**Status:** ❌ Not Fixed
**Description:** `service.py:1110-1161` writes user vocabulary JSON file directly. Live `self._app._vocabulary_manager._data` NEVER touched. `dictation_pipeline.py:812-816` uses stale in-memory state until app restart.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/vocabulary.py`**Fix:** After `_secure_atomic_write` in `save_vocabulary_with_diff`, reload live manager: `with live_vm._lock: live_vm._load_and_merge()`. OR refactor to delegate to live VocabularyManager CRUD methods.
**Severity:** 🔴 High

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

### XZ-R12-06 — Lockfile name mismatch (Medium)
**Status:** ❌ Not Fixed
**Description:** `single_instance.py:21` docstring says `voice-typer.lock`; `:441` code creates `backend.lock`. Docstring claims `fcntl.flock` is primary; actual code uses `O_CREAT|O_EXCL` primary with flock as secondary.
**Related Files:** `voice_typer/server/single_instance.py`**Fix:** Update docstring to match code: lockfile is `backend.lock`, primary mechanism is `O_CREAT|O_EXCL` with stale-PID recovery, flock is defense-in-depth.
**Severity:** 🟡 Medium

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

### XZ-CFG-05 — TS `DEFAULT_CONFIG` fixture drift (Medium)
**Status:** ❌ Not Fixed
**Description:** `__tests__/helpers/fixtures.ts:32-187` `DEFAULT_CONFIG` differs from Python Config defaults in 30+ fields. `llm_preset: "default"` is invalid (not in Python Literal). `schema_version: 1` vs Python `_CURRENT_SCHEMA_VERSION = 3`.
**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/helpers/fixtures.ts`
- `voice_typer/server/config.py`**Fix:** Replace hand-maintained fixture with fetch from server's `get_defaults` IPC in test setup hook. OR add CI parity test importing Python Config defaults. Fix `llm_preset` to valid value ("professional").
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


### XZ-EH-009 — Silent OSError in Windows registry probe (Medium)
**Status:** ❌ Not Fixed — except OSError still silent at task_scheduler.py:325-326
**Description:** `task_scheduler.py:331-333` `_is_prewarm_registered_registry` `except OSError: return False` with no log. Siblings `_register_prewarm_registry` and `_unregister_prewarm_registry` log at WARNING.
**Related Files:** `voice_typer/server/task_scheduler.py`**Fix:** Add `log.debug("[TASK] _is_prewarm_registered_registry: OSError reading HKCU Run key: %s", exc)` before return False.
**Severity:** 🟡 Medium

### XZ-EH-015 — Implicit ack-vs-error contract is fragile (Medium)
**Status:** ❌ Not Fixed
**Description:** `onboarding_handlers.py:27-40, 154, 185, 209, 224, 239` — 5 handlers delegate ack-vs-error to whether service's return dict contains `"error"` key. If service returns `{"error": None}` (falsy but present), handler reports `ack` for failure.
**Related Files:**
- `voice_typer/server/handlers/onboarding_handlers.py`
- `voice_typer/server/service.py` (onboarding methods)**Fix:** Migrate per documented PVT-G5-095 plan: service should `raise` on failure (typed `OnboardingError`), handler let propagate to outer `except Exception` which calls `_respond_with_error`. Eliminates implicit dict-key contract.
**Severity:** 🟡 Medium


### XZ-R16-02 — `respawn_failed` event unconsumed by renderer (High)
**Status:** ❌ Not Fixed
**Description:** `supervisor.rs:200-201` emits `respawn_failed` when supervisor exhausts 5 respawn attempts. `python-namespace.ts:65-98` only synthesizes `relaunching` + `reconnected`. `useConnection.ts:276-294` sets `"restarting"` on `reconnecting`, only exits via `reconnected`. After `respawn_failed`, renderer UI stuck on "Restarting…" forever.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`**Fix:** Add `makeListener` for `"respawn_failed"` synthesizing `{type: "error", data: {message: "respawn exhausted"}}`. In `useConnection`, subscribe and call `setConnectionStatus("disconnected")` + `setLastError(t("connection.respawnFailed"))`. Add "Relaunch app" button to disconnected UI.
**Severity:** 🔴 High

### XZ-R16-04 — Silent `get_config` catch (Medium)
**Status:** ❌ Not Fixed
**Description:** `useConnection.ts:166-173` outer catch swallows `get_config` error with no `console.error`/`console.warn`.
**Related Files:** `voice_typer/client/src/renderer/src/hooks/useConnection.ts`**Fix:** Add `console.warn("[IPC] get_config connection probe failed (attempt ${retries}/${maxRetries}):", err)`.
**Severity:** 🟡 Medium

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

---

### XZ-R17-06 — Windows logoff/shutdown: OS kills process before `_do_cleanup` finishes (Medium)
**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:963-1007` `_win32_console_handler` spawns `quit()` on daemon thread. `_do_cleanup` cumulative worst-case ~85s. Windows CTRL_LOGOFF/SHUTDOWN gives ~5 seconds.
**Related Files:** `voice_typer/server/shutdown_controller.py`**Fix:** Add fast-path for `ctrl_logoff_event`/`ctrl_shutdown_event` that skips non-critical cleanup, runs ONLY critical path (crash_recovery.flush, history_db.flush, recorder.stop/discard, _clear_backend_pid_file, CloseHandle) with 1s timeouts each. Target <3s total.
**Severity:** 🟡 Medium

### XZ-R17-08 — `_save_sync` redundant chmod per transcription (Low)
**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:177-191` `os.chmod(self._path.parent, 0o700)` called every save (after every transcription). Idempotent but wasteful syscall.
**Related Files:** `voice_typer/server/crash_recovery.py`**Fix:** Guard with "first-run" flag: `if not self._dir_ensured: ... self._dir_ensured = True`.
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

---

### XZ-R18-03 — `stop-python.ts` missing SIGKILL fallback (Medium)
**Status:** ❌ Not Fixed
**Description:** `stop-python.ts:38-43` sends SIGTERM only, immediately nulls `state.pythonProcess`. Stuck Python (in C extension) → orphaned process. Holds single-instance mutex → next launch fails. `relaunch-app.ts:67-76` has correct SIGKILL fallback pattern.
**Related Files:** `voice_typer/client/src/main/python/stop-python.ts`**Fix:** Add SIGKILL fallback matching `relaunch-app.ts`. Do NOT null `state.pythonProcess` inside kill timer — wait for `exit` event.
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

---


### XZ-CC-2 — Duplicated noise-filter defaults (Medium)
**Status:** ❌ Not Fixed
**Description:** `audio_chain_builder.py:128-153` `_DEFAULTS` dict mirrors `config.py:942-986` Config dataclass defaults. No sync mechanism (no CI test).
**Related Files:**
- `voice_typer/server/audio_chain_builder.py`
- `voice_typer/server/config.py`**Fix:** Replace `_DEFAULTS` with `Config()` instance snapshot: `_DEFAULTS = {f.name: getattr(Config(), f.name) for f in fields(Config) if f.name.startswith("noise_filter_")}`. OR add CI test mirroring `test_hotkey_reserved_sync.py`.
**Severity:** 🟡 Medium

### XZ-CC-13 — Stale TODO migrate-tests cluster (Low)
**Status:** ❌ Not Fixed
**Description:** 4 identical TODOs across 3 packages: `prewarm/__init__.py:110`, `recording/__init__.py:49, 320`, `server_platform/__init__.py:80`. All reference "CR-67 / TECH-DEBT". ~500 LOC of `__init__.py` boilerplate for test-patch compatibility.
**Related Files:**
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/server_platform/__init__.py`**Fix:** Tracked separately as CR-67/TECH-DEBT. Update TODOs with current date + tracking issue link. OR if migration actively worked, annotate "IN PROGRESS by <owner>, ETA <date>".
**Severity:** 🟢 Low

### XZ-CC-15 — `pyrefly-baseline.json` `errors: []` while CI reports 116 errors (High)
**Status:** ❌ Not Fixed
**Description:** Baseline file's own `_current_state_2026_07_22` comment admits: "Until those land, the pyrefly check step in CI will continue to exit 1 (because pyrefly reports 116 unsuppressed errors and the baseline is empty)". 34 non-platform-specific real type bugs hidden from CI.
**Related Files:** `pyrefly-baseline.json`**Fix:** (a) Pin `pyrefly==1.1.1` in `pyproject.toml [dev]` + `.github/workflows/build.yml`. (b) Fix 34 non-platform-specific real type bugs. (c) For 82 platform-specific false positives, regenerate baseline from real `pyrefly check` run on platform-appropriate interpreter. (d) DO NOT keep `errors: []` as "conservative floor" — silence-by-deletion pattern.
**Severity:** 🔴 High

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


---


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

---


### DR-3 — Rust monolith files: bubble.rs 1313 LOC, ws.rs 1241, supervisor.rs 1055, logging.rs 989, sidecar_cmds.rs 897, spawn.rs 845
**Status:** ❌ Not Fixed
**Description:** AC-138 flagged these files at smaller sizes; they have WORSENED since (bubble.rs +86%, ws.rs +24%, logging.rs +60%, supervisor.rs +11%). 4 NEW files also crossed the 500-LOC threshold: `commands/system_cmds.rs` 627, `tray.rs` 647, `platform/process.rs` 689, `commands/export.rs` 527. 9 of 21 Rust files exceed the spaghetti trigger.
**User Impact:** Compile times grow superlinearly; reviewer cognitive load per file is high; tests/production interleaving compounds navigation cost.
**Root Cause:** Incremental growth across sessions added doc comments + tests without extracting helpers. AC-138's split plans were never executed.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/sidecar/spawn.rs`
- `src-tauri/src/commands/system_cmds.rs`
- `src-tauri/src/tray.rs`
- `src-tauri/src/platform/process.rs`
- `src-tauri/src/commands/export.rs`

**Fix:** Re-prioritize bubble.rs split (it nearly doubled) — separate geometry helpers → `commands/bubble/geometry.rs`; rate-limiter → `commands/bubble/rate_limiter.rs`. Same pattern for logging.rs (writer vs panic-hook vs CombinedLogger), ws.rs (5 phase helpers + reader/writer/heartbeat), supervisor.rs (counter I/O vs respawn loop).
**Severity:** 🔴 High

---


### DR-17 — Python service/model.py: download_model 558 LOC god method
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/service/model.py:673-1230` `download_model(self, model_name)` spans 558 LOC — single largest method in the service package. Contains two nested function definitions, a 3-way branch (whisper/qwen/parakeet), an inline daemon-thread spawn, a polling loop with pause/resume state machine, two nested try/except blocks, 14 distinct return points.
**User Impact:** Untestable in isolation (closures + branch state), unreadable in code review, high blast radius for any change to progress event shape or cancel semantics.
**Root Cause:** Method grew by accretion — UX-005, NEW-MODEL-001, CR-11, NEW-PAUSE-001, HIGH-8, XA-13-C1, NEW-PRIV-011, PERF-10, NEW-PERF-004 each added a paragraph of logic without restructuring.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/model.py`

**Fix:** Split into 3 dispatch methods (`_download_whisper_family`, `_download_qwen`, `_download_parakeet`) returning a common `DownloadOutcome` TypedDict; lift `_push_progress`/`_notify` to module-level helpers taking explicit args; extract the polling loop into `_poll_download_progress(download_id, thread, target_bytes, model_name)`. `download_model` becomes ~40-LOC dispatcher; total file drops to ~600 LOC.
**Severity:** 🔴 High

---

### DR-20 — Python service/privacy.py: delete_all_personal_data 275 LOC + export_gdpr_bundle 189 LOC
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/service/privacy.py:195-469` `delete_all_personal_data` interleaves 7 distinct responsibilities: HistoryDB checkpoint+close, Rust `logs/` rmtree, hardcoded-file unlink loop, glob-pattern unlink loop, crash-archive rmtree, keychain cleanup loop, in-memory config zeroing + engine invalidation, HistoryDB re-creation. `export_gdpr_bundle` (189 LOC) similarly interleaves.
**User Impact:** Hard to test individual cleanup steps. Diff review is painful.
**Root Cause:** GDPR Art. 17/20 implementations grew organically — each `G4-CR-*` and `G4-M-*` fix added a paragraph without extracting helpers.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/privacy.py`

**Fix:** Extract private helpers — `_gdpr_checkpoint_history_db(hdb)`, `_gdpr_unlink_personal_files(config_dir)`, `_gdpr_unlink_personal_globs(config_dir)`, `_gdpr_rmtree_rust_logs(config_dir)`, `_gdpr_clear_keychain(app)`, `_gdpr_invalidate_cached_engines(app)`, `_gdpr_recreate_history_db(app)`. Each ~15-30 LOC. `delete_all_personal_data` becomes a 20-LOC orchestrator. Same pattern for `export_gdpr_bundle`.
**Severity:** 🟡 Medium

---

### DR-21 — Python S1-CR-78 STILL REAL: IPC protocol unversioned
**Status:** ❌ Not Fixed (verified still real)
**Description:** `voice_typer/server/ipc_server.py:1218` dispatcher reads only `msg.get("type")` and `msg.get("data")` — no `protocol_version`, no handshake negotiation. Auth frame is `{"type": "auth", "token": "..."}` with no version field. ADR-0004 (IPC protocol ADR) does not mention versioning. TS-side `push_events.ts:534-558` documents the TODO but never implemented it.
**User Impact:** A stale renderer talking to a newer Python backend gets opaque `unknown_command`/`auth_failed` errors instead of a structured `protocol_version_mismatch`. Field-level schema drift on any of the 63 commands is completely undetectable at runtime.
**Root Cause:** The three-allowlist contract only validates command-name membership, not schema versioning.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/ipc/transport_tcp.py`
- `voice_typer/server/ipc/validation.py`
- `voice_typer/client/src/renderer/src/types/ipc/push_events.ts`
- `src-tauri/src/sidecar/ws.rs`

**Fix:** Add `"protocol_version": 1` to the auth frame (both Python sender and Python receiver), reject mismatches BEFORE token check with `code: "server.protocol_version_mismatch"` (register in `ErrorCodes`). Bump on any wire-incompatible change. Cross-language parity test asserting Python/Rust/TS agree on the current constant.
**Severity:** 🔴 High

---


### DR-23 — Python S1-CR-67 STILL REAL: _RecordingModule custom module class
**Status:** ❌ Not Fixed (verified still real)
**Description:** `voice_typer/server/recording/__init__.py:328-453` defines `class _RecordingModule(sys.modules[__name__].__class__)` with custom `__getattr__`/`__setattr__` routing 5 mutable globals through to owning submodules. Same pattern in `prewarm/__init__.py` and `server_platform/__init__.py` (uses different mechanism — `_pkg.X` runtime-call-time indirection). ~500 LOC of `__init__.py` boilerplate exists purely for test-patch compatibility.
**User Impact:** New contributors writing tests must use the old (wrong) patch path or the test silently no-ops. The `_RecordingModule.__setattr__` override is a global mutation point.
**Root Cause:** Tests use `monkeypatch.setattr("voice_typer.server.recording.X", ...)` against the package namespace; rather than migrate ~90-150 test files, custom module machinery was installed.
**Progress:** None yet. Tracked as "CR-67 / TECH-DEBT — OPEN, awaiting migration" in each `__init__.py`.
**Related Files:**
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/server_platform/__init__.py`

**Fix:** Migrate the 90-150 test sites to patch submodules directly. Then delete `_RecordingModule` and the `_pkg.X` indirection. Expected net deletion: ~500 LOC across three packages. (Large refactor — may need to be split across multiple sessions.)
**Severity:** 🟡 Medium

---


### DR-32 — Python DRY: 16000 sample rate hardcoded 30+ places (FZ-28)
**Status:** ❌ Not Fixed (verified still real)
**Description:** Literal `16000` appears 30+ times in production Python code and 20+ times in TS test fixtures. A canonical `_WHISPER_SAMPLE_RATE = 16000` exists in `transcription.py:93` but is module-local and NEVER imported by any other file.
**User Impact:** If Whisper ever accepts 8 kHz / 24 kHz, every site must be hunted down. A wrong literal silently miscalculates duration.
**Root Cause:** No shared constants module; engineers inline `16000` rather than reaching for the existing constant.
**Progress:** None yet. FZ-28 marked "❌ Not Fixed".
**Related Files:**
- `voice_typer/server/transcription.py`
- `voice_typer/server/config.py`
- `voice_typer/server/vad.py`
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/qwen_engine.py`
- `voice_typer/server/audio_processor.py`
- `voice_typer/server/audio_chain_builder.py`
- `voice_typer/server/cloud_engines.py`
- `voice_typer/server/level_monitor.py`
- `voice_typer/server/microphone_test_recorder.py`
- `voice_typer/server/service/status.py`
- `voice_typer/server/audio_filters/*.py`
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/audio_pipeline.py`
- `voice_typer/server/server_platform/microphone_list.py`

**Fix:** Create `voice_typer/server/_audio_constants.py` exporting `WHISPER_SAMPLE_RATE = 16000`, `SILERO_VAD_SAMPLE_RATES = frozenset({8000, 16000})`, `RNNOISE_SAMPLE_RATE = 48000`, `NATIVE_MIC_RATES = frozenset({8000, 16000, 44100, 48000})`. Update all 30+ call sites.
**Severity:** 🟡 Medium

---


### DJ-2 — VoiceTyperApp.__init__ god-constructor blocks tray icon appearance
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `VoiceTyperApp.__init__` synchronously constructs ~25 subsystems on the main thread BEFORE `tray.start(bg_work=self._do_startup)` is called. The chain includes AudioProcessor, AudioQualityAnalyzer, Recorder, ModelManager, ClipboardManager, TrayIcon, SettingsController, ShutdownController, LifecycleController, UndoRepasteController, AudioQualityController, HotkeyDispatcher, TimerCoordinator, HistoryDB (opens sqlite3 write queue + spawns writer thread), CrashRecovery, DuckCrashRecovery, VolumeController, VolumeDucker, WaveformBubble, WaveformBubbleWiring, TemplateManager (reads JSON from disk), VocabularyManager (reads JSON from disk). Only after all of these does `start()` create the pystray icon.

**User Impact:** The user sees nothing — no tray icon, no window, no feedback — for the entire duration of `__init__`. On a cold disk the file I/O from TemplateManager/VocabularyManager and the HistoryDB thread spawn add hundreds of milliseconds. The user may think the app failed to launch.

**Root Cause:** Verified — VoiceTyperApp is a god-class constructor. The RW-9 decomposition extracted methods into controllers but kept eager construction of all controllers in __init__.

**Progress:** Deferred — VoiceTyperApp.__init__ god-constructor refactor. Out of session scope.

**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/startup_sequence.py`

**Fix:** Defer construction of non-critical subsystems to the bg startup thread (`StartupSequence.run`) or use lazy `@property` patterns for: TemplateManager, VocabularyManager, WaveformBubble, WaveformBubbleWiring, UndoRepasteController, AudioQualityController, DuckCrashRecovery, VolumeDucker. Keep only Config, ThreadRegistry, TrayIcon, Recorder, ModelManager, HotkeyDispatcher, ShutdownController on the critical path. For TemplateManager/VocabularyManager, replace eager-init with a lazy `@property` + a `reload()` call after config changes.

---

### DJ-3 — Redundant eager Qwen engine init on main thread
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `app.py:196-199` synchronously calls `self.models._ensure_engine("qwen")` in `__init__` on the main thread. `_ensure_engine` calls `importlib.import_module("voice_typer.server.qwen_engine")` (which eagerly imports numpy) and constructs the engine object. However, `load_background` (model_manager.py:331) — which runs on the ModelLoad daemon thread started later in `StartupSequence.run` — already calls `_ensure_engine(backend_name)`.

**User Impact:** When Qwen is the configured backend, the main thread pays the cost of importing qwen_engine.py + numpy + constructing the engine, before the tray icon is shown. ~5-50ms on warm cache, more on cold cache.

**Root Cause:** Verified — the eager call is redundant with the background load. The comment 'mirrors the pre-Round-9 behavior' indicates this was kept for backward compat, but the ModelLoad thread already handles it.

**Progress:** Deferred — remove eager _ensure_engine('qwen') call. Out of session scope.

**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/model_manager.py`

**Fix:** Remove the eager `_ensure_engine("qwen")` call from app.py:196-199. The `load_background` thread will construct the engine on the daemon thread. If engine-construction failure needs early surfacing, add `notify_on_failure=True` to the background load (already exists at model_manager.py:560).

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

### DJ-7 — ASR models not unloaded on shutdown — CUDA memory leak across restart cycles
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** shutdown_controller._do_cleanup runs 14 parallel teardown helpers — NONE of them touch `app._asr_registry` / `app.models`. `asr_registry.unload()` (line 753) is only invoked on (a) backend load failure and (b) `app._change_model()`. On a normal quit / restart_app / atexit, the active Parakeet / Whisper backend's `unload()` is never called. Combined with Finding DJ-6 (host force-kills after 2-6s), the Python process is SIGKILLed before Python's GC can drop the model references — meaning torch's `empty_cache()` / `cuda.synchronize()` / context destructor never runs.

**User Impact:** On systems with discrete GPU (Parakeet/Qwen backends): CUDA memory leak across rapid restart cycles; potential 'out of memory' on next launch if user restarts within ~1s. On CPU-only Whisper: ~1-3GB RSS stays resident longer than necessary (until OS reaps the killed process), causing momentary memory pressure. Degrades the 'Restart' tray action's reliability on GPU systems.

**Root Cause:** Verified — _do_cleanup has no ASR teardown step.

**Progress:** Deferred — ASR models not unloaded on shutdown. Out of session scope.

**Related Files:**
- `voice_typer/server/shutdown_controller.py`
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/model_manager.py`

**Fix:** Add a 15th parallel helper `_teardown_asr_models` that calls `app._asr_registry.unload()` (unload() is idempotent and already handles per-backend errors with try/except). Place it FIRST in the parallel batch. Inside the helper, after unload(), call `torch.cuda.empty_cache()` and `torch.cuda.synchronize()` under try/except (guard with `hasattr(torch, 'cuda') and torch.cuda.is_available()`).

---


### DJ-9 — In-flight WS handler races with DB teardown — silently loses final transcription
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** The WS pool drain at shutdown_controller.py:434-436 bounds the wait at 5s. If a handler finishes JUST inside that window (say 4.9s), it returns normally — but if the handler itself calls `app.history_db.add_transcription()` (e.g. a transcribe command finishing mid-shutdown), the write hits a SQLite connection that's being closed concurrently by `_teardown_history_db` (which runs in the parallel batch starting at T=0.5s). The `_shutting_down` gate at sidecar_ws.py:434 ONLY rejects NEW requests (after the flag flips). In-flight requests that were accepted BEFORE the flag flipped continue to run on the dispatch pool worker threads.

**User Impact:** In-flight transcriptions or LLM-polish results are silently lost on shutdown. The history DB's WAL may also be left in an inconsistent state if a write is mid-flush when close() runs. User-visible: 'I was dictating and clicked Quit, but the last sentence never appeared in the output window and isn't in history.'

**Root Cause:** Suspected — the WS pool drain and the parallel teardown batch are not strictly ordered; the 5s drain timeout is a soft barrier, not a hard one. The _shutting_down gate is set BEFORE _do_cleanup runs, so NEW requests are rejected, but in-flight ones race.

**Progress:** Deferred — in-flight WS handler race with DB teardown. Out of session scope.

**Related Files:**
- `voice_typer/server/shutdown_controller.py`
- `voice_typer/server/sidecar_ws.py`

**Fix:** Move `_teardown_history_db` and `_teardown_crash_recovery` OUT of the parallel batch and into a sequential post-drain phase. The WS pool drain must complete (or time out) BEFORE any subsystem that an in-flight handler might touch is torn down. Restructure: (1) Early bookend: ipc_server.stop + ws_pool shutdown(wait=True, 5s); (2) SEQUENTIAL: history_db.flush + close, crash_recovery.flush + shutdown; (3) Parallel batch (12 remaining helpers); (4) Late bookend: tray.stop.

---

### DJ-10 — Electron 3s SIGTERM-only killTimer with no SIGKILL escalation — orphans native hotkey binary
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** stop-python.ts:137-148 arms a 3s `killTimer` that calls `state.pythonProcess.kill()` (SIGTERM by default). There is NO escalation to SIGKILL after a further grace period. If Python is stuck in a non-async-signal-safe state (e.g. ctranslate2 holding the GIL, or a PortAudio stream callback in C land), SIGTERM is queued but not delivered. `state.pythonProcess.kill()` returns true synchronously even if the signal is queued; the killTimer then sets `state.pythonProcess = null`, so the `pythonProcess.once('exit')` handler is never registered.

**User Impact:** On the Electron path: native hotkey binary (windows-key-listener / macos-key-listener / linux-key-listener) is orphaned when Python is killed mid-cleanup. The binary holds the global hotkey registration (RegisterHotKey on Windows, CGEventTap on macOS, evdev grab on Linux) — on Windows this means the F2 hotkey stops working system-wide until the orphaned binary is manually killed.

**Root Cause:** Verified — 3s SIGTERM-only killTimer with no SIGKILL escalation; no kill_tree equivalent on Electron path.

**Progress:** Deferred — Electron 3s SIGTERM-only killTimer. Out of session scope.

**Related Files:**
- `voice_typer/client/src/main/python/stop-python.ts`
- `voice_typer/client/src/main/__tests__/shutdown-hooks.test.ts`

**Fix:** (1) Extend the killTimer to 5-10s (matches the sidecar's typical cleanup time). (2) After SIGTERM, arm a SECOND 3s timer that escalates to SIGKILL (`state.pythonProcess.kill('SIGKILL')` on POSIX, `taskkill /F /T /PID` on Windows for tree kill). (3) On Windows, use `taskkill /T` instead of `pythonProcess.kill()` to reap the native hotkey binary child. (4) Update shutdown-hooks.test.ts to pin the SIGKILL escalation contract.

---

### DJ-11 — _capitalize_pronoun_i is O(n²) — per-character scan with O(n) substring copies inside loop
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `text_cleanup.py:833-862` `_capitalize_pronoun_i` runs on EVERY dictation. For each 'i' character encountered, the function allocates TWO fresh substrings (`text[:i]` and `text[i+1:]`), each O(n). With O(n) 'i' characters in typical English text (~1% of chars), total cost is O(n²). For a 5000-10000 char long-form dictation (power users / meeting transcription), the cost climbs into the 25M-100M char-op range, adding tens of ms per dictation.

**User Impact:** For long-form dictations (multi-paragraph, 5000+ chars — power users / meeting transcription), the cleanup pass takes tens of milliseconds longer than necessary, paid in full before the user sees their text. For short dictations the cost is invisible.

**Root Cause:** Verified — per-character Python loop with O(n) substring copies inside the loop body.

**Progress:** Deferred — _capitalize_pronoun_i O(n²) regex rewrite. Out of session scope.

**Related Files:**
- `voice_typer/server/text_cleanup.py`

**Fix:** Replace the per-character scan with a single compiled regex pass that matches a standalone `i` and uses a callable replacement to consult the Roman-numeral context sets. Equivalent semantics, O(n). Alternatively, tokenize the text once (it has already been split-and-rejoined upstream in `clean_transcribed_text`, line 438) and operate on the token list.

---

### DJ-12 — Uninterruptible transcription inference — abort hotkey doesn't free compute
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `transcription.py:833-834, 867-880` `TranscriptionEngine.transcribe` holds `self._lock` for the WHOLE inference (ctranslate2 / transformers `model.generate()` C call). The watchdog (`dictation_pipeline.py:317-348`, CR-006) can mark a cycle as cancelled and SKIP THE PASTE after the late transcription completes — but the underlying `model.generate()` C call is uninterruptible from Python. There is no `abort()` / `cancel()` on the inference itself.

**User Impact:** When the user hits the abort hotkey mid-transcription, the dictation thread keeps running the inference to completion (1-30s on CPU; 0.3-5s on GPU). The user sees 'abort acknowledged' in the tray but the underlying CPU/GPU is still pegged. On a hot GPU under contention, this delays the next dictation start by the full inference duration. The CR-006 mitigation only prevents the late paste — it does not free the compute.

**Root Cause:** Verified — synchronous C-level inference with no Python-level cancellation token plumbed into the engine.

**Progress:** Deferred — uninterruptible transcription inference. Out of session scope.

**Related Files:**
- `voice_typer/server/transcription.py`
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/server/cloud_engines.py`

**Fix:** Run the inference on a worker thread with a `threading.Event` abort token, and have the watchdog `join(timeout=…)` on it; on timeout, call ctranslate2's interrupt API (ctranslate2 >= 4.x exposes `model.interrupt()`) or fall through to the existing skip-paste path. For Parakeet, transformers exposes `generation_config` callbacks (`stop_strings`, stopping_criteria) that can be checked between generated tokens — wire the abort event to a StoppingCriteria sub-class.

---

### DJ-13 — Cloud engines block transcription thread up to 35s with no abort
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `cloud_engines.py:593-602, 755` — `_send_openai_compatible` and `_send_deepgram` use `urllib.request.OpenerDirector.open()` with `timeout=30` and `max_retries=3` with exponential backoff (0.5s, 1.0s, 2.0s). Worst-case wall time before failure is ~35s. There is no abort token consulted between retries or during the open() call. The dictation_pipeline.run() watchdog can mark the cycle as cancelled but cannot interrupt the in-flight HTTP request.

**User Impact:** When a cloud provider (OpenAI/Groq/Deepgram) is slow or hangs (e.g. a stuck TCP connection that the OS doesn't RST for 30s), the user sees a frozen tray 'Transcribing…' for up to ~35s with no way to abort. This is materially worse than the local-engine path where the watchdog at least marks the cycle cancelled.

**Root Cause:** Verified — synchronous `urllib.request.OpenerDirector.open()` with a 30s timeout, no `threading.Event` checked between retries.

**Progress:** Deferred — cloud engines per-retry abort check. Out of session scope.

**Related Files:**
- `voice_typer/server/cloud_engines.py`
- `voice_typer/server/dictation_pipeline.py`

**Fix:** (a) Check `self._cancel_event.is_set()` before each retry; if set, raise immediately. (b) Reduce the per-request timeout from 30s to 10s and rely on the retry loop for resilience — 10s is already 5× a typical Whisper-API response. (c) For Deepgram streaming-mode API, use the WebSocket endpoint so partial results stream back and the user sees incremental progress instead of a single blocking call.

---

### DJ-14 — GPU→CPU fallback cold-loads CPU model — 5-50s frozen tray
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `transcription.py:1044-1057` `_transcribe_with_fallback_unlocked` on a GPU runtime error tears down + reloads on CPU IN-LINE on the transcription thread: `del self._model`, `self._model = None`, `self._device = 'cpu'`, `self._compute_type = 'int8'`, `self._reload_under_lock()` (cold WhisperModel() construction, 5-50s), then retries. The docstring admits cold model load is 5-50s.

**User Impact:** When a transient GPU error (e.g. a single OOM from a concurrent process briefly spiking VRAM) fires mid-dictation, the user waits: (failed GPU inference, ~1-5s) + (cold CPU model load, ~5-50s) + (CPU retry inference, ~3-15s) = 9-70s total before they see any text. The tray stays at 'Transcribing…' the entire time. This is the worst-case user-visible latency in the app.

**Root Cause:** Verified — fallback path calls `self._reload_under_lock()` synchronously which runs the full `_load_transcriber_impl` chain.

**Progress:** Deferred — pre-warm CPU whisper-tiny.en fallback. Out of session scope.

**Related Files:**
- `voice_typer/server/transcription.py`
- `voice_typer/server/parakeet_engine.py`
- `voice_typer/server/model_manager.py`

**Fix:** (a) Keep a pre-warmed CPU whisper-tiny.en backend resident in the registry (loaded once at startup in a background thread), so the fallback path is a registry lookup + transcribe, not a cold load. (b) Make the GPU→CPU fallback a one-shot per session — instead surface a tray notification 'GPU failed, switch to CPU?' and let the user accept or retry GPU. (c) For Parakeet, `self._model.to(device='cpu', dtype=self._torch.float32)` (parakeet_engine.py:1044) is faster than a full reload (~1-3s vs 5-50s) — the Whisper path should mirror this.

---

### DJ-15 — _correct_whisper_phrases / _remove_extra_words O(N×M) — no Aho-Corasick automaton
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `text_cleanup.py:619-685` `_correct_whisper_phrases` and `_remove_extra_words` each run on each dictation. For N phrases × M text length, the membership-test loop is O(N×M). The XV-42 optimization (substring check vs regex search) brought the constant factor down ~10×, but the algorithmic complexity is unchanged. The docstring acknowledges: 'a true O(N+M) solution would require an Aho-Corasick automaton, which is out of scope for this fix.'

**User Impact:** For the default corrections.json (~50 phrases) on a 500-char dictation, cost is ~25K substring operations = <1ms — invisible. For a user with a large custom corrections file (the SEC-010 cap allows up to 5,000 entries) on a 2,000-char dictation, cost is ~10M substring operations = ~50-100ms per dictation, paid in full on the cleanup hot path between transcription and paste.

**Root Cause:** Verified — linear scan over the phrase list per dictation, with no pre-built automaton.

**Progress:** Deferred — Aho-Corasick for phrase matching. Out of session scope.

**Related Files:**
- `voice_typer/server/text_cleanup.py`

**Fix:** Build an Aho-Corasick automaton from the phrase list once at `configure_corrections` time (the `pyahocorasick` package is ~50KB, or a pure-Python implementation is ~200 lines). The automaton finds all phrase matches in O(N+M) regardless of how many phrases are in the dictionary. Alternative: collapse the two functions into one pass (currently `_correct_whisper_phrases` and `_remove_extra_words` each scan the full phrase list separately, doubling the cost).

---

### DJ-16 — Per-segment redundant getattr + import in transcription logging loop
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

**Description:** `transcription.py:919-938` — inside the per-segment loop: `_log_transcriptions_flag = self.config is not None and getattr(self.config, 'log_transcriptions', False)` (per-segment getattr), and `from voice_typer.server.security import redact_pii` (per-segment import lookup). When `log_transcriptions=True`, `redact_pii(_seg_text)` runs the full PII regex suite (4+ compiled patterns) per segment.

**User Impact:** For a typical dictation with 5-20 segments the overhead is microseconds and unmeasurable. For a long recording with 100+ segments AND `log_transcriptions=True` (opt-in), the redundant `getattr` + import lookups add ~1ms; the per-segment `redact_pii` calls add ~50-100ms of regex work that could be batched.

**Root Cause:** Verified — flag check, import, and PII regex call all sit inside the segment iteration loop instead of being hoisted out before the loop.

**Progress:** Deferred — hoist getattr + import in transcription logging. Out of session scope.

**Related Files:**
- `voice_typer/server/transcription.py`

**Fix:** (a) Hoist `_log_transcriptions_flag` to before the segment loop (computed once). (b) Hoist the `from voice_typer.server.security import redact_pii` to before the loop (or to module top under a `try/except ImportError`). (c) When `log_transcriptions=True`, defer the PII redaction to the post-loop `result = ' '.join(text_parts).strip()` stage and redact `result` once.

---


### DJ-19 — _all_read_connections pruning is reactive — dead-thread 20MB connections leak until next new-thread read
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `history_db.py:1037-1047` `_get_read_conn` appends to `_all_read_connections`. Pruning (`_prune_dead_read_connections_locked`) is REACTIVE — only fires when a NEW connection is created on a thread that doesn't already have one. If N threads each create a read connection, then die, and NO new thread creates a connection afterward, the N dead-thread connections (each 20MB) sit in `_all_read_connections` until the next `_get_read_conn` call from a fresh thread.

**User Impact:** At 20MB per leaked connection × even 10-20 dead threads = 200-400MB of phantom SQLite page cache that is never reclaimed until the next new-thread read. For a long-running tray process over hours, this compounds with DJ-18 (cursors pinning their connections). The 20MB cache is also not reused across threads (each thread has its own connection), so the cache hit rate is poor.

**Root Cause:** Verified — pruning is REACTIVE (only fires when a NEW connection is created).

**Progress:** Deferred — _all_read_connections periodic prune. Out of session scope.

**Related Files:**
- `voice_typer/server/history_db.py`

**Fix:** Either (a) schedule a periodic prune on a background timer (e.g. every 60s, walk `_all_read_connections` and close any whose thread_ident is not alive), or (b) bound the list size with an LRU eviction policy (close oldest connection when count exceeds e.g. 8), or (c) prefer a connection pool (e.g. a small `queue.Queue` of N reusable read connections) instead of per-thread connections.

---


### DJ-22 — _pending_restores entry not removed in two error paths — orphaned snapshot pins 16-50MB
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `clipboard/manager.py:1066-1116` `_delayed_restore` daemon thread body. The DE-63 refactor moved `_pending_restores.remove(pending_entry)` from the `finally` block to a `try` block BEFORE `snapshot.restore()`. This narrowed the atexit race but opened two new leak windows: (1) `_cb.time.sleep(delay)` raises → except catches → finally runs but does NOT remove the entry; (2) the lock-acquire fails catastrophically (line 1089's broad `except Exception`) → entry stays in list AND restore proceeds without claim. Each entry holds a ClipboardSnapshot which can hold up to `_MAX_FORMAT_BYTES = 16MB` per format × N formats.

**User Impact:** Rare in practice (requires signal delivery during the 150ms sleep, or a catastrophic threading.Lock failure), but each orphaned entry can pin a multi-megabyte ClipboardSnapshot. Over a long-running tray process with many paste cycles, even a 0.01% leak rate accumulates.

**Root Cause:** Verified — the DE-63 refactor moved the `_pending_restores.remove(pending_entry)` from the `finally` block to a `try` block BEFORE `snapshot.restore()`. This narrowed the atexit race but opened two new leak windows.

**Progress:** Deferred — clipboard _pending_restores finally remove. Out of session scope.

**Related Files:**
- `voice_typer/server/clipboard/manager.py`

**Fix:** Re-add a defensive `_pending_restores.remove(pending_entry)` (under the lock, with `contextlib.suppress(ValueError)`) to the `finally` block at manager.py:1116, AFTER the existing `_last_copied_text` clear. This catches the two leak windows without reintroducing the DE-63 atexit race (the `ValueError` suppress handles the case where atexit already claimed the entry).

---


### DJ-27 — Stale keyring availability cache — secrets fall through to plaintext for entire session
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `credential_store.py:388-402` — three module-level globals set once on first probe: `_keyring_available_cache`, `_keyring_backend_name_cache`, `_keyring_reason_cache`. `is_keyring_available()` populates them once and returns the cached bool. `_reset_keyring_cache()` exists (line 486) but is documented as 'Test-only' — no production code path calls it. On Linux headless without gnome-keyring-daemon, the probe returns (False, 'fail', 'no usable keyring backend'). On macOS with a locked keychain at app startup, the probe read may raise → cached as False. In both cases, the cache says 'unavailable' for the entire process lifetime — even if the user subsequently unlocks their keychain or installs gnome-keyring-daemon mid-session.

**User Impact:** A user who starts Voice Typer with keychain locked (e.g. fresh boot before login keychain unlock on macOS, or systemd user session before gnome-keyring-daemon on Linux) has every subsequent API key operation routed to the plaintext fallback for the entire session, even after the keychain becomes available. This compounds DJ-24 — plaintext secrets accumulate in memory AND on disk for the entire session.

**Root Cause:** Verified — cache is set once, never invalidated in production; reset helper exists but is test-only.

**Progress:** Deferred — keyring availability cache re-probe. Out of session scope.

**Related Files:**
- `voice_typer/server/credential_store.py`

**Fix:** Either (a) re-probe on a slow cadence (e.g. every 5 minutes via a daemon timer) so a backend that appears mid-session is picked up, or (b) re-probe on demand when store_secret / load_secret is called AND the cache says unavailable AND the last probe was more than N seconds ago (cheap rate-limited re-probe). Document the chosen policy in `_probe_keyring`'s docstring.

---


### DJ-32 — _dispatch uses getattr(self.app, '_shutting_down') instead of cached snapshot
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `ipc_server.py:1215, 1300, 1313` `_dispatch` calls `getattr(self.app, '_shutting_down', False)` 3 times per dispatch. The `_send` path already has the optimization (`self._cached_shutting_down`, refreshed in start()/stop() — see ipc_server.py:533-561 comment). `_dispatch` does NOT use the cached snapshot — it does a fresh `getattr(self.app, ...)` traversal on every call. The comment at sender.py:206-214 notes this `getattr` is ~2× slower than a direct attribute access.

**User Impact:** Per-dispatch overhead on every IPC round-trip. Three `getattr` traversals per dispatch × ~2-10 dispatches/sec baseline = measurable CPU.

**Root Cause:** Verified — the cache pattern was added for `_send` (the push hot path) but not propagated to `_dispatch` (the dispatch hot path). Both paths have the same `is True` shutdown-gate semantics.

**Progress:** Deferred — _cached_shutting_down in _dispatch. Out of session scope.

**Related Files:**
- `voice_typer/server/ipc_server.py`

**Fix:** Read `self._cached_shutting_down` (already maintained by start()/stop()) instead of `getattr(self.app, '_shutting_down', False)` in `_dispatch`. Replace all three call sites. Add a defensive `getattr(self, '_cached_shutting_down', False) is True` for test fixtures that bypass `__init__` (mirroring the sender.py:224 pattern).

---

### DJ-33 — event_bus.publish allocates fresh list(_subscribers) per publish on 60Hz hot path
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `event_bus.py:595-596` `publish` acquires `_lock` and allocates a fresh `list(_subscribers)` snapshot per publish. At 60Hz `bubble_level` + dispatch responses + state-change pushes, this is 60+ list allocations/sec. The lock is also acquired on every `subscribe()`/`unsubscribe()` (lines 492, 504), creating contention between publishers and subscribe/unsubscribe operations.

**User Impact:** Per-publish lock acquisition + list allocation on the audio/UI hot path. Sustained CPU during recording; GC pressure from short-lived list objects.

**Root Cause:** Suspected — the snapshot is necessary because subscribers may unsubscribe mid-iteration (RuntimeError guard), but the snapshot doesn't need to be a fresh `list()` on every publish.

**Progress:** Deferred — event_bus tuple snapshot. Out of session scope.

**Related Files:**
- `voice_typer/server/event_bus.py`

**Fix:** Maintain `__subscribers_snapshot: tuple = ()` updated atomically on subscribe/unsubscribe (under the lock). `publish()` reads the tuple without the lock (tuple read is atomic under GIL) and iterates it. Eliminates both the per-publish lock acquisition and the per-publish list allocation.

---


### DJ-51 — appendFileSync open/close per call — no persistent write stream
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `client/src/main/logging/rotation.ts:127-148` + `structuredLogger.ts:114` — Node's `fs.appendFileSync` does `open(O_APPEND|O_CREAT) → write → close` per call. No persistent write stream is held open for the lifetime of the process. Per log line: 3 syscalls (open+write+close) on top of the rotateIfNeeded cache check.

**User Impact:** Marginal per-call (sub-millisecond) but compounds under load. More significantly, the open/close pattern prevents the OS from coalescing consecutive small writes into a single disk flush — each appendFileSync is a separate I/O submission.

**Root Cause:** Verified. The pattern is the standard Node idiom for low-volume logging, but Voice Typer uses it for every WARN/ERROR (default production) and every INFO when PERSIST_INFO=1.

**Progress:** Deferred — appendFileSync open/close per call. Out of session scope.

**Related Files:**
- `voice_typer/client/src/main/logging/rotation.ts`
- `voice_typer/client/src/main/logging/structuredLogger.ts`

**Fix:** Hold a persistent `fs.createWriteStream(path, { flags: 'a' })` per log file in a module-level Map; write to it via `stream.write(line)`. The stream buffers and flushes in the background. Rotation requires destroying + recreating the stream (one-time cost on rotation, not per-write). Alternative: keep `appendFileSync` but document that the design trades throughput for crash-safety.

---


### DJ-68 — Service-layer mic cache not invalidated by OS watcher
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `device_manager.py:184-204` (`_invalidate_device_cache`) vs `service/microphone_test.py:59-67` (service-layer 5s TTL cache). `MicrophoneDeviceWatcher(on_change=self._invalidate_device_cache)` wires the OS-event callback to invalidate ONLY `DeviceManager._device_list_cache`. A separate cache lives on the service layer: `_microphones_cache` / `_microphones_cache_ts` with a 5s TTL, used by `get_microphones()` which returns `self._app._microphones` (the cached list, no PortAudio query). The OS watcher never calls `service.refresh_microphones(force=True)` or invalidates `_microphones_cache`.

**User Impact:** After a USB/BT hot-plug event, the Electron UI continues to show the stale microphone dropdown (including the unplugged device, missing the newly-plugged one) for up to 5s. If the user clicks the stale device, `Recorder.start()` (which uses the invalidated `DeviceManager` cache) correctly fails over, but the user experience is confusing.

**Root Cause:** Verified — two independent caches for the same underlying `sd.query_devices()` data, with the OS watcher only invalidating one of them.

**Progress:** Deferred — service-layer mic cache invalidation. Out of session scope.

**Related Files:**
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/service/microphone_test.py`

**Fix:** Have `DeviceManager._invalidate_device_cache` (or the watcher's `on_change` callback) also call `service.refresh_microphones(force=True)` (or reset `_microphones_cache_ts = 0.0`). Alternatively, collapse the two caches into one and have `get_microphones()` consult `DeviceManager._refresh_device_list()`.

---

### DJ-69 — Device index used as persistent identifier — unstable across hot-swap
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `device_manager.py:332-346` (`_resolve_device`), `:360-398` (`_same_physical_microphone_candidates`) + `microphone_list.py:120-130`. `config.microphone` stores a PortAudio device INDEX as a string. PortAudio device indexes are positional in `sd.query_devices()` and shift whenever devices are added or removed. If the saved index now points to a different physical device (common after USB unplug + replug of a different device), the function adopts the new device's name as `selected_name` and looks for same-named alternates — silently substituting the new device.

**User Impact:** User selects 'USB Mic A' (index 5). Later unplugs Mic A and plugs in 'Webcam Mic B' which inherits index 5. Next `start()`: app records from Webcam Mic B without any warning. The user believes they're dictating to their USB mic but audio comes from the webcam — silent device substitution, privacy concern, and confusing transcriptions.

**Root Cause:** Verified — the persistent identifier is the PortAudio positional index, which is not stable across hot-swap.

**Progress:** Deferred — persist device name not just index. Out of session scope.

**Related Files:**
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/server_platform/microphone_list.py`

**Fix:** Persist the device NAME (and host API) in `config.microphone` alongside or instead of the index. At `start()`, resolve by name first (via `find_microphone_by_name`), falling back to the saved index only if the name is not found. Add a one-time warning when the saved index's current name doesn't match the saved name.

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


### DJ-82 — Post-reconnect drain: 101 separate sendall syscalls per _send cycle under lock
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `ipc/sender.py:362-404` (per-event write loop in pending drain) + `transport.py:93-97` (`_TCPLineIO.write` does one sendall per call). After a client reconnect, each `_send` call performs up to 101 separate `socket.sendall` syscalls: 1 for the current event + up to 100 for the pending drain. Each `tcp_client.write` calls `self.conn.sendall(text.encode('utf-8'))` — a fresh syscall. The entire block runs under `self._tcp_write_lock`, so all 101 syscalls block every other writer thread. At the 60-125 Hz push rate, the post-reconnect drain window sustains up to 12,625 sendall syscalls/sec.

**User Impact:** After a sleep/resume or transient disconnect, the renderer's reconnect experiences a multi-second burst of 10K+ syscalls/sec on the audio-worker-adjacent push path. During this window the `_tcp_write_lock` is contended, delaying `transcription_partial` updates and `state_changed` events. The user perceives a multi-second 'frozen' UI after every reconnect.

**Root Cause:** Verified — per-event sendall under lock with no batching.

**Progress:** Deferred — batch drain sendall. Out of session scope.

**Related Files:**
- `voice_typer/server/ipc/sender.py`
- `voice_typer/server/ipc/transport.py`

**Fix:** Batch the drain into a single `sendall`. Replace the per-event loop with: `batch = ''.join(p + '\n' for p in recent)` then `tcp_client.write_raw(batch)` (single sendall). Add a `write_raw(self, text: str)` method to `_TCPLineIO` that calls `self.conn.sendall(text.encode('utf-8'))` once for the whole batch. The current event can be prepended to the batch for a single sendall covering both. This reduces 101 syscalls → 1 syscall per `_send` during drain — a 100× reduction in kernel transitions and lock-hold time.

---


### DJ-87 — Reconnect drops in-flight requests — 'flaky button' feel during brief disconnects
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `send-to-python.ts:148-151` (immediate reject when socket null) + `tcp-connect.ts:257-291` (close handler rejects all pendingRequests). When the TCP socket is null (between disconnect and reconnect), `sendToPython` rejects immediately with `new Error('Python backend is not connected')` — no queuing, no retry. On the close event, every entry in `state.pendingRequests` is rejected and the map is cleared. There is no re-send-on-reconnect mechanism: a `toggle_dictation` request that was written to the socket but whose response hadn't arrived when the socket dropped is silently lost on the Electron side, even though the Python side may have already processed it.

**User Impact:** During a transient TCP blip (sleep/resume, Wi-Fi flap on a laptop, brief GC pause on the Python side triggering the 2s write timeout), any in-flight user action fails. The user clicks 'Stop recording' → socket drops mid-flight → request rejected → Electron shows 'Python backend is not connected' toast → user clicks again → by now Python has already stopped recording from the first request → second click starts a new recording. The state mismatch between what Python did and what Electron thinks happened is a real UX hazard.

**Root Cause:** Verified — immediate reject on null socket; close handler rejects all pending requests with no replay.

**Progress:** Deferred — reconnect replay queue. Out of session scope.

**Related Files:**
- `voice_typer/client/src/main/python/send-to-python.ts`
- `voice_typer/client/src/main/python/tcp-connect.ts`

**Fix:** Add a small outbound queue with replay-on-reconnect for idempotent commands. When `state.tcpSocket` is null, instead of rejecting immediately, push the message to `state.pendingOutbound: Array<{msg, resolve, reject, ts}>` (bounded to e.g. 16 entries — drop-oldest for non-idempotent commands like `toggle_dictation`). On reconnect, flush the queue in FIFO order. For non-idempotent commands, still reject immediately to avoid double-execution. For idempotent commands (`get_config`, `get_status`, `heartbeat`, `set_config`), the replay is safe.

---

### DJ-88 — AudioFilterChain.tsx inline t() calls defeat useMemo labels optimization
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `AudioFilterChain.tsx:264-353` (labels memo) + `:423, 440, 463, 503, 521, 542, 565, 586, 607, 632, 646, 663, 684, 709, 727, 750, 771, 792, 813, 838, 856, 877, 902, 919` (inline `info={t(...)}`) + 17 `ariaLabel={t(...)}` + 7 `aria-label={t(...)}`. The `labels` `useMemo` was added because 'previously re-resolved on every render — ~80 t() calls per render = 0.5-1 ms wasted per Settings interaction'. But only the label/infoSearch strings were hoisted into the memo. The `info=`, `aria-label=`, and `ariaLabel=` props still call `t()` inline in the JSX render path — 48 additional `t()` calls per render that the memo was supposed to eliminate.

**User Impact:** Every slider drag re-resolves 48 translation strings. On a 60-Hz drag this is ~2,880 `t()` calls per second. Settings page feels sluggish on slower hardware (Ryzen U-class laptops).

**Root Cause:** Verified — the memoization was applied to one category of `t()` calls (visible labels) but the inline `info=` / `aria-*` calls were left as-is, defeating half of the intended perf win.

**Progress:** Deferred — AudioFilterChain inline t() calls. Out of session scope.

**Related Files:**
- `voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx`

**Fix:** Extend the `labels` `useMemo` (or add a second `useMemo` keyed on `[_locale]`) to resolve all `info` / `ariaLabel` / `aria-label` strings once per locale change, then reference them from the JSX. Alternatively, wrap the entire row-rendering in a `React.memo`'d child component that receives pre-resolved strings as props.

---

### DJ-89 — usePythonEvent subscribes to global stream — N listeners × 33 event types = 3N dispatches on Tauri
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `usePython.ts:548-568` + `preload/index.ts:20-27` + `lib/tauri-bridge/python-namespace.ts:70-105`. Each `usePythonEvent(type, handler)` call adds a NEW `ipcRenderer.on('python-event', ...)` listener to the global IPC bus. The `KNOWN_EVENT_TYPES` set lists 33 event types; typical mounted-component count is ~12+ (Home, History, Dashboard, Settings, Microphone, Vocabulary, Templates, Models, App, plus several hook consumers). Every Python push event — including the ≤30 Hz `bubble_level` and `mic_level` streams during recording — invokes ALL N callbacks, each performing an `event.type === type` filter. On Tauri each `usePythonEvent` call installs THREE Tauri `event.listen` subscriptions — so the multiplier is 3N on Tauri.

**User Impact:** During recording, `bubble_level` events fire at ~30 Hz. With 12+ mounted `usePythonEvent` consumers, that's 360+ callback invocations per second just for one event stream — each doing a property access + string compare. CPU cost is small per-call (sub-microsecond) but the wake-up count is significant on battery; on Tauri the 3× multiplier triples the listener-registry overhead.

**Root Cause:** Verified — the bridge exposes a single global event stream and the hook filters by `type` client-side. There is no per-type subscription at the bridge level.

**Progress:** Deferred — usePythonEvent per-type listener. Out of session scope.

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`
- `voice_typer/client/src/preload/index.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`

**Fix:** Have the preload/Tauri bridge expose a per-type `onEvent(type, callback)` API that internally maintains a `Map<type, Set<callback>>` and dispatches only matching callbacks. Alternatively, add a single shared dispatcher in `usePython.ts` (module-level `Map<type, Set<handler>>`) that subscribes to `onEvent` exactly ONCE and fan-outs to per-type subscribers — eliminating the N-listener multiplication.

---

### DJ-90 — bubble-components useAudioLevels rAF loop runs at 60Hz even when bubble hidden/idle
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `bubble-components.tsx:354-404` (useAudioLevels rAF loop). The rAF loop unconditionally schedules the next frame at the top of the callback, then early-returns when the bubble window is hidden OR not in recording mode. The comment at lines 354-357 explicitly accepts this trade-off 'so the loop resumes instantly when the bubble becomes visible / re-enters recording mode.' But the cost is that a hidden/idle bubble window still wakes the renderer 60 times per second forever (each wake does a ref-read + early-return).

**User Impact:** A bubble BrowserWindow created at app startup but not yet shown (or shown then hidden between dictations) keeps the renderer's compositor awake at 60 Hz. Multiplied by the ~3-hour typical workday, that's ~648,000 wasted rAF wakeups per day per bubble window. On battery-powered laptops this measurably shortens battery life.

**Root Cause:** Verified — the `requestAnimationFrame(animate)` call precedes the `visibleRef.current` / `recordingRef.current` checks. The existing comment acknowledges this as intentional.

**Progress:** Deferred — bubble-components rAF loop. Out of session scope.

**Related Files:**
- `voice_typer/client/src/renderer/src/bubble-components.tsx`

**Fix:** Move the `requestAnimationFrame(animate)` call to the END of the callback (after the work), guarded by `if (visibleRef.current && recordingRef.current)`. Add an external 'wake' trigger in the existing `useEffect` (lines 299-334) that already tracks `mode`/visibility: when the bubble becomes visible OR re-enters recording, kick off a fresh `requestAnimationFrame(animate)`. This trades one extra frame of latency on resume for zero CPU while paused.

---

### DJ-91 — showBubbleWindow redundant OS calls — 2× setAlwaysOnTop + setVisibleOnAllWorkspaces per show
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `bubble-window.ts:519-664` (showBubbleWindow). On every dictation start, `showBubbleWindow` makes 2× `setAlwaysOnTop`, 1× `setVisibleOnAllWorkspaces` (already done at window creation), 1× `moveTop`, plus a `setImmediate` that always fires (even on the happy path) and re-checks `isVisible()`. The defensive duplication is documented but the OS calls themselves are NOT free — each `setAlwaysOnTop` / `setVisibleOnAllWorkspaces` is a Win32/macOS Cocoa round-trip.

**User Impact:** Each show adds 1-3 ms of OS-level window-manager round-trips on Windows (where `setAlwaysOnTop` is synchronous and re-flows the z-order). On rapid show/hide cycles (e.g. short dictations back-to-back), this compounds. The `setImmediate` callback also allocates a closure and a macrotask tick on every show.

**Root Cause:** Verified — defensive layering of best-effort calls. The first `setAlwaysOnTop` (line 573) and the second (line 618) use the SAME arguments; the second is purely redundant on the happy path.

**Progress:** Deferred — showBubbleWindow redundant OS calls. Out of session scope.

**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts`

**Fix:** Collapse the redundant calls. Perform `setAlwaysOnTop` once before `show()`, and only retry inside the `setImmediate` fallback IF `!isVisible()`. Drop the second `setAlwaysOnTop` at line 618 entirely. The `setVisibleOnAllWorkspaces` at line 586 is already a no-op if called with the same args — keep it as a defensive sync but mark it as idempotent in a comment.

---

### DJ-92 — useHotkeyCapture effect with no deps array runs after every render
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `useHotkeyCapture.ts:743-753`. The effect has NO dependency array (line 753 closes with `});` not `}, [...]);`). It runs after EVERY render of any component using `useHotkeyCapture` (every `HotkeyPicker` instance — the Settings page renders 4-6 of them). The comment acknowledges the intent but the side effect is that every Settings-page render (slider drag, toggle, search-box keystroke) triggers 5 ref assignments per `HotkeyPicker` × ~6 pickers = 30 ref writes per render.

**User Impact:** 30 ref writes per Settings render is not catastrophic, but the effect also forces React to commit a side-effect phase after every render, which delays paint. On slower hardware, dragging an AudioFilterChain slider (which re-renders the Settings tree) is measurably janky because each frame triggers 6 effect cleanups + 6 effect re-runs across the `HotkeyPicker` instances.

**Root Cause:** Verified — documented intentional pattern, but it forgoes the dependency-array optimization. The actual handlers are themselves `useCallback`'d with stable deps.

**Progress:** Deferred — useHotkeyCapture effect deps array. Out of session scope.

**Related Files:**
- `voice_typer/client/src/renderer/src/components/hotkey/useHotkeyCapture.ts`

**Fix:** Add a dependency array of `[onCaptureStart, onCaptureEnd, handleKeyDown, handleKeyUp, cancelRecording]`. Since the `handle*` callbacks are already `useCallback`'d (stable identity unless their deps change) and `onCaptureStart`/`onCaptureEnd` come from the parent, the effect will re-run only when one of those references actually changes — typically never during a slider drag. The always-attached keyboard listener effect (`useEffect([], ...)` at line 759) stays unchanged.

---

### DJ-93 — Dashboard recomputes maxCount + rebuilds 7 bars + 14 t() calls per render
**Status:** ❌ Not Fixed
**Severity:** 🟢 Low

**Description:** `Dashboard.tsx:493` + `:625-668` + `:708-714`. The Dashboard recomputes `maxCount` (which spreads + iterates the 7-element dailyActivity array) and re-runs `barHeight(day.count, maxCount)` 7 times per render. The inline `style={{ height: ... }}` is a fresh object every render, breaking `React.memo` on the bar `<div>`. The `day.count === 1 ? t('...singular', { label, count }) : t('...plural', { label, count })` ternary calls `t()` twice per bar per render.

**User Impact:** Dashboard only re-renders on `transcription_final` events (debounced 500ms) and on locale change — so this is NOT a hot path. Impact is low. But each Dashboard render still does ~14 `t()` calls + 7 `Math.max` iterations + 7 inline-style allocations that could be cached.

**Root Cause:** Suspected — the render path was written before the data was made stable.

**Progress:** Deferred — Dashboard maxCount memo. Out of session scope.

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx`

**Fix:** Wrap `maxCount` in `useMemo(() => Math.max(1, ...d.dailyActivity.map((a) => a.count)), [d.dailyActivity])`. Extract the 7-bar chart into a `React.memo`'d `<DailyActivityChart days={d.dailyActivity} maxCount={maxCount} />` child. Hoist the share-image container's inline style to a module-level constant.

---


### DJ-96 — recorder.py is a 3772-line monolith mixing 7 concerns — Phase 4.5 mandatory split
**Status:** ⚠️ Partial
**Severity:** 🔴 Critical

**Description:** `recorder.py:1-3772` — single 3772-line module containing a 3430-line `Recorder` class with 50+ methods spanning 7 disjoint concerns (device enumeration, VAD state, audio I/O, thread lifecycle, secure-clear, session state, resampling). The file already delegates to 6 sibling modules (DeviceManager, AudioPipeline, DisconnectHandler, VadShimMixin, resampling, buffer) but the orchestrator still mixes all concerns: `start()` is 237 LOC, `stop()` is 200 LOC, `__init__` is 390 LOC, `_process_audio_chunk` is 176 LOC, `_handle_device_disconnect` is 115 LOC. Property-shim boilerplate (8 device-state properties + 6 AudioPipeline delegators + 7 device-resolution delegators + 3 health-checker delegators) accounts for ~290 LOC of pure mechanical delegation. Project rule: 'no entry file > ~800 lines mixing concerns'.

**User Impact:** Any change to `Recorder` requires reading 3772 lines to find the relevant code. Test patches via `monkeypatch.setattr('voice_typer.server.recording.X', ...)` are coupled to a `__init__.py` custom module class (CR-67 / TECH-DEBT) that exists ONLY because `recorder.py` looks up cross-submodule helpers through the package namespace at call time. Each new collaborator extraction shrinks `recorder.py` and reduces the surface that needs the patch-path bridge.

**Root Cause:** Verified — `_recorder_split.py` documents the planned decomposition but only `snapshot()` + `discard()` were actually moved (372 LOC total); the rest of the plan was never executed. `docs/rw04-recording-decomposition.md` confirms 'Wave 2 + Wave 3 remain in Recorder as follow-up waves' — those waves never landed.

**Progress:** Phase 4.5 split deferred — only constants + dead-code deletion landed. Full 3-wave extraction (worker_threads.py, stream_lifecycle.py, session_state.py) requires a multi-hour refactor that exceeded the 10-min sub-agent budget.

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/_recorder_split.py`
- `voice_typer/server/recording/__init__.py`

**Fix:** Execute the three-wave extraction: (1) `recording/worker_threads.py` (~410 LOC: audio-worker + event-worker lifecycle); (2) `recording/stream_lifecycle.py` (~620 LOC: stream-open + process + close, merging the duplicated `_open_stream_*` pair and the triplicated AudioProcessor-retune block); (3) `recording/session_state.py` (~250 LOC: per-session reset + secure-clear, merging the duplicated `_secure_clear_*_caches` pair). Each wave preserves the 1-line delegator pattern on `Recorder` so `inspect.getsource(Recorder.X)` regression tests keep passing. Estimated post-split `recorder.py` size: ~1200 LOC.

---


### DJ-99 — AudioProcessor retune block duplicated between start() and DisconnectHandler.restart_stream()
**Status:** ❌ Not Fixed
**Severity:** 🔴 High

**Description:** `recorder.py:2506-2537` AND `disconnect_handler.py:224-248`. The 'retune AudioProcessor to new device native rate' block is copy-pasted between `start()` and `DisconnectHandler.restart_stream()`. Both follow the identical pattern: `if recorder._audio_processor is not None: _proc_sr = getattr(recorder._audio_processor, '_sample_rate', None); if _proc_sr is not None and int(_proc_sr) != int(effective_sr): _set_sr = getattr(recorder._audio_processor, 'set_sample_rate', None); if callable(_set_sr): try: _set_sr(int(effective_sr)); except: log.warning(...); else: try: recorder._audio_processor.rebuild_from_config(recorder.config); except: log.warning(...)`. The only differences are: log message text and the `effective_sr` variable name.

**User Impact:** ~30 LOC duplicated. The retune logic is non-trivial (3-level fallback: set_sample_rate → rebuild_from_config → log-and-continue). A bug in any branch must be fixed in two places. The disconnect_handler copy already diverged: it omits the `log.info(... rebuild_from_config called ...)` line that the start() copy has.

**Root Cause:** Verified — both blocks have the comment 'Mirrors the start() retune logic'. The author explicitly acknowledged the duplication but did not factor it out.

**Progress:** Deferred — requires extracting retune_audio_processor helper. Out of session scope.

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/disconnect_handler.py`

**Fix:** Extract `recording/_audio_processor_helpers.py::retune_audio_processor(proc, effective_sr, config, *, context: str) -> None` that runs the 3-level fallback + logging. Call from both `start()` and `DisconnectHandler.restart_stream()`. The `context` parameter substitutes for the divergent log messages. Saves ~30 LOC + future fixes land in one place.

---

### DJ-102 — Dead attrs: _previous_chunk_pending + _skipped_frames — declared, reset, incremented, NEVER read
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `recorder.py:638-639`, `session_state.py:205-210`, `capture.py:192`. `_previous_chunk_pending` and `_skipped_frames` are declared in `__init__`, reset in `_reset_session_state`, and `_skipped_frames` is incremented in capture.py (`self._skipped_frames += 1`). But NEITHER attribute is ever READ anywhere in the codebase. The reset comment says: 'RT-SAFE-001: the _previous_chunk_pending flag is no longer used (replaced by ring buffer overflow detection), but we keep resetting it for diagnostic cleanliness.'

**User Impact:** 2 dead instance attributes + 3 lines of dead reset code + 1 line of dead increment code. Minor, but `_skipped_frames += 1` runs on every ring-buffer overflow (16 Hz hot path) for no benefit. The naming also misleads — `_skipped_frames` implies the value is consumed somewhere.

**Root Cause:** Verified — `grep -r '_previous_chunk_pending'` finds only declarations + resets + comment mentions; `grep -r '_skipped_frames'` finds only declarations + resets + increment. No production read, no test assertion.

**Progress:** NOT applied — both attrs still declared (recorder.py:638-639), reset (session_state.py:205-210), and incremented (capture.py:192). See `session_state.py:205` comment.

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/session_state.py`
- `voice_typer/server/recording/capture.py`

**Fix:** Delete the two `__init__` declarations (recorder.py:638-639), the two resets (session_state.py:209-210), and the increment (capture.py:192). Keep `_dropped_ring_chunks` (which IS read by tests and by `level_monitor.py`).

---

### DJ-103 — Stale doc references to non-existent _callback_impl / _audio_callback_record
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `recorder.py:1101`, `audio_pipeline.py:544`. Two stale documentation references to methods that no longer exist: `recorder.py:1101` ('the primary disconnect detection is done in the audio callback via zero-filled indata detection (see _audio_callback_record)') — `_audio_callback_record` does not exist; current name is `_audio_callback_dispatch`. `audio_pipeline.py:544` ('This method contains the heavy processing pipeline that was previously in the PortAudio callback (`_callback_impl`)') — same stale reference to `_callback_impl`. A third reference at recorder.py:269 was cleaned up during the RW-04 refactoring.

**User Impact:** Readers chasing the referenced method names waste time grepping for non-existent symbols.

**Root Cause:** Verified — leftover references from before the RT-SAFE-001 refactor that split the monolithic callback.

**Progress:** NOT applied — `_audio_callback_record` ref remains at recorder.py:1101; `_callback_impl` ref remains at audio_pipeline.py:544.

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/audio_pipeline.py`

**Fix:** Update the references: `_callback_impl` → `_audio_callback_dispatch` (or `_build_audio_callback` where the closure context is intended); `_audio_callback_record` → `_audio_callback_dispatch`.

---

### DJ-104 — blocksize=512 literal hard-coded in 7 sites across 2 files
**Status:** ⚠️ Partial
**Severity:** 🟡 Medium

**Description:** `recorder.py:562, 564, 1988, 2105, 2175, 2309` + `disconnect_handler.py:164`. The blocksize literal `512` is hard-coded in 6 places in `recorder.py` plus 1 in `disconnect_handler.py`. Comments at multiple sites say 'matches sd.InputStream blocksize' — the literal is load-bearing across modules but has no module constant.

**User Impact:** Changing the blocksize (e.g. to 1024 for lower callback frequency on slow ARM devices) requires editing 7 sites across 2 files. Easy to miss one. Also prevents test parameterization of the blocksize.

**Root Cause:** Verified — `grep -n '^_AUDIO_BLOCKSIZE|^BLOCKSIZE|^_BLOCK_SIZE' recorder.py` returns no matches. The blocksize is a de-facto project-wide constant (Silero VAD requires 512-sample blocks per AUDIO-001/VAD-001, and `vad.py` pads/truncates to handle driver deviations).

**Progress:** Added _AUDIO_BLOCKSIZE + _TEARDOWN_CALLBACK_DRAIN_BUDGET_S + _TEARDOWN_CALLBACK_POLL_INTERVAL_S constants. Not yet referenced in all 7 sites — deferred.

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/disconnect_handler.py`

**Fix:** Add `_AUDIO_BLOCKSIZE: int = 512` to the module constants block (recorder.py:232-300) and reference it everywhere. Export from `__init__.py` so `disconnect_handler.py` can `from .recorder import _AUDIO_BLOCKSIZE`. Tag the constant with a comment explaining the VAD-001 constraint. Note: DJ-58 proposes making blocksize adaptive to sample rate, which would change this — coordinate the two fixes.

---

### DJ-105 — _ensure_mono allocates fresh mean array per chunk on stereo input
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium

**Description:** `recorder.py:851-865` (`_ensure_mono`). `_ensure_mono` is called from the RT audio callback path AND from the worker thread path. On the multi-channel branch (line 862): `if audio.ndim == 2 and audio.shape[1] > 1: return np.mean(audio, axis=1, dtype=np.float32)`. `np.mean` with `out=` not supplied allocates a fresh float32 array per call. At 16 Hz callback rate on a stereo device, this is 16 allocations/sec of ~2 KB each. The project's own comments (line 270) emphasize the RT callback must 'complete before the next buffer arrives (~32ms at 512 blocksize / 16kHz)'.

**User Impact:** ~32 KB/sec of garbage on the RT thread for stereo devices. Marginal CPU cost (~50 µs per allocation × 16 Hz = 0.8 ms/sec). Not a critical regression but violates the RT-safety contract documented at line 270.

**Root Cause:** Suspected — the allocation could be avoided by pre-allocating a mono scratch buffer in `__init__`.

**Progress:** Deferred — requires pre-allocated _mono_scratch buffer. Out of session scope.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Add `self._mono_scratch: np.ndarray = np.empty(1024, dtype=np.float32)` to `__init__`. In `_ensure_mono`, resize the scratch if `audio.shape[0] > len(self._mono_scratch)` (under no lock — only the callback and worker touch it, both synchronously), then `np.mean(audio, axis=1, out=self._mono_scratch[:audio.shape[0]], dtype=np.float32)` and return a view. Target module: `recording/stream_lifecycle.py` (per DJ-96).

---


### Findings from Session 4 (prefix FR — Security & Data)


### Lower-Severity Findings (Documented; not all fixed this run)

The following findings were identified during Phase 1 review but are documented here for tracking. Selected Low-severity items may be fixed if time permits during Phase 4.

### Spaghetti / Phase 4.5 Split Candidates (documented; not all fixed this run)

- **FR-S1:** `voice_typer/server/config.py` (2242 lines) — split into config_dataclass/loader/saver/purge.
- **FR-S2:** `voice_typer/server/history_db.py` (2156 lines) — complete AC-135 split.
- **FR-S3:** `voice_typer/server/ipc_server.py` (2133 lines) — Phase 4.5 candidate.
- **FR-S4:** `voice_typer/server/config_validators.py` (1678 lines) — Phase 4.5 split (deferred since DT-39).
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

Spaghetti splits are deferred to a future run due to the high risk of regression on 1000+ line files containing business logic. The splits require careful planning and test coverage that exceeds the 10-minute sub-agent ceiling.

---

## Verifier Findings (2026-07-30)

The following items were identified during the verifier pass of the Fix-Existing sub-agent run. They are not compile errors or bugs — each is a documented maintenance item or deferred work item.

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

## AB-3 — `device_manager._resolve_effective_sample_rate` and `_same_physical_microphone_candidates` bypass the device cache (200-1200ms avoidable RPC latency on hot-swap)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `_refresh_device_list` builds the cached device list with only `{id,index,name,max_input_channels}` — omitting `default_samplerate` and `hostapi`. Consequently `_resolve_effective_sample_rate` must issue a fresh `sd.query_devices(device)` RPC and a second `sd.query_hostapis(host_api_idx)` RPC on every `start()` candidate (1-3) and every disconnect-restart. `_same_physical_microphone_candidates` also calls `sd.query_devices(device)` + `list(sd.query_devices())` directly, bypassing the cache. The host_api index→name mapping is stable at runtime but re-queried on every call.
**User Impact:** On the `start()` critical path (hotkey press → recording begins), 1-3 candidates × 2 RPCs each × 50-200ms/RPC = 100-1200ms of avoidable latency on Windows MME. On hot-swap restart, 100-600ms additional avoidable latency. BT mic flapping 3 times = 300-1800ms of cumulative wasted time before recording resumes.
**Root Cause:** Cache schema was designed when only `_cached_max_input_channels` consumed it; `_resolve_effective_sample_rate` was never updated to use it.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/recording/disconnect_handler.py`
**Fix:** (a) Extend the cache dict in `_refresh_device_list` to include `default_samplerate` and `hostapi`. Add a `_cached_device_info(device)` helper. (b) Refactor `_resolve_effective_sample_rate` to read from the cache. (c) Build a `dict[int, str]` host_api cache lazily in `DeviceManager.__init__`. (d) Replace `sd.query_devices(...)` calls in `_same_physical_microphone_candidates` and `disconnect_handler.restart_stream` with cached helpers.
**Severity:** 🔴 High

## AB-5 — Level monitor runs RNNoise filter chain on every cosmetic level-bar chunk (15-100% CPU peg for a non-functional bar)
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

## AB-6 — Microphone watcher Linux poll interval defaults to 1.0s (DJ-48 fix never applied, 1Hz idle wakeups for app lifetime)
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

## AB-7 — `microphone_list.list_microphones` queries PortAudio on every IPC call (50-200ms latency per call)
**Status:** ❌ Not Fixed (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `list_microphones()` calls `sd.query_devices(kind="input")`, `sd.query_hostapis()`, and `sd.query_devices()` on every invocation. No module-level cache. `find_microphone_by_name` and `find_microphone_by_id` call `_pkg.list_microphones()` in a loop — re-enumerating all devices on every call. The production caller `device_manager.py:620-622` calls `find_microphone_by_name` during device restart-after-disconnect recovery. Each call = 50-200 ms PortAudio round-trip.
**User Impact:** 50-200 ms latency added to device-restart path after a Bluetooth/USB disconnect. Same PortAudio data fetched 2-3× within a single recovery sequence (list → find by name → resolve device).
**Root Cause:** Caching is delegated to specific callers but `find_microphone_by_name`/`find_microphone_by_id` bypass both caches and hit PortAudio directly.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/server_platform/microphone_list.py`
**Fix:** Add a 5 s module-level TTL cache inside `list_microphones()` so ALL callers benefit. Invalidate the cache when the OS device-change watcher fires (the watcher already calls `_invalidate_device_cache` on device_manager — extend it to invalidate the platform-layer cache too).
**Severity:** 🟡 Medium

## AB-22 — `VocabularyManager.apply_to_text` redundantly tokenizes 4× per dictation (4 word-level categories each split+join)
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

## AB-23 — `VocabularyManager.apply_to_text` snapshot allocates 6 containers × 5000 ref-copies per dictation (over-applied CR-23 snapshot)
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

## AB-24 — LLM polish call blocks dictation pipeline thread (up to 10s paste latency)
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

## AB-35 — Windows native hotkey installs 3 separate WH_KEYBOARD_LL hooks (3× per-keystroke system-wide CPU)
**Status:** ⚠️ Partial (code audit: fix was never applied to source; see AB audit report for details)
**Description:** `hotkey_dispatcher.register()` creates 3 independent `HotkeyBackend` instances (main dictation, ESC cancel, repaste). On Windows in toggle mode, each backend's `start()` calls `_install_low_level_hook()` which calls `SetWindowsHookExW(WH_KEYBOARD_LL, ...)` — a SYSTEM-WIDE low-level keyboard hook. The hook proc (`_hook_proc` at `windows_native.py:732`) fires for EVERY keystroke system-wide. With 3 backends all in toggle mode, 3 separate hooks fire per keystroke = ~12 syscalls per keystroke system-wide, plus 3 dedicated `GetMessageW` message-pump threads.
**User Impact:** Constant per-keystroke CPU overhead system-wide (even when the user is typing in an unrelated app). Scales linearly with the number of registered hotkeys (currently 3, could grow). At 100 WPM typing (~5 keystrokes/sec), overhead is ~60 syscalls/sec — small but persistent.
**Root Cause:** The architecture uses one backend per hotkey spec rather than one backend serving multiple specs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/hotkeys/windows_native.py`
- `voice_typer/server/hotkey_dispatcher.py`
**Fix:** Consolidate the 3 hotkey specs into a single `WH_KEYBOARD_LL` hook that dispatches to the appropriate callback based on the vk code + modifiers. This reduces system-wide per-keystroke overhead from 3× to 1×. Alternatively, only install the LL hook for the main dictation hotkey and use `RegisterHotKey`+`WM_HOTKEY` (event-driven, no per-keystroke proc) for ESC and repaste.
**Severity:** 🟡 Medium

## AB-42 — Audio filter chain per-chunk heap allocation churn (compressor/limiter/equalizer zi wrappers + RNNoise resampler zero-stuffing)
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

## AB-47 — `microphone_test_recorder.start_test_recording` has duplicate ~35-line setup blocks (DRY violation)
**Status:** 🚫 Won't Fix
**Description:** `microphone_test_recorder.py:142-187` and `200-236`: `start_test_recording` has two near-identical ~35-line blocks. Only the entry condition differs (monitor already on right device vs. monitor needed start).
**User Impact:** Maintainability — any change (e.g. adding a new test-state field) must be applied in two places or the two paths drift.
**Root Cause:** Copy-paste duplication.
**Progress:** Won't Fix (Low-severity, deferred — outside Critical/High/Medium mandate; will be addressed in a future cleanup pass).
**Related Files:**
- `voice_typer/server/microphone_test_recorder.py`
**Fix:** Extract a `_enter_test_mode_locked(duration, filters) -> dict` helper that both branches call after acquiring the lock.
**Severity:** 🟢 Low

---

## AB-48 — `waveform_bubble_wiring` 8ms throttle is below chunk period (effectively a no-op; 31-94 Hz IPC to renderer)
**Status:** 🚫 Won't Fix
**Description:** `waveform_bubble_wiring.py:156`: The throttle is `if now - self._last_bubble_level_push_ts < 0.008:  # 8 ms = ~125 Hz`. At 16 kHz / blocksize 512, chunk period = 32 ms → every chunk passes the gate. At 48 kHz / blocksize 512, chunk period ≈ 10.7 ms → ALL chunks pass there too (~94 Hz < 125 Hz gate). So the throttle is effectively a no-op for any realistic device; `bubble_level` is published on EVERY chunk (31-94 Hz).
**User Impact:** 31-94 IPC messages/sec to the renderer (each ~40 B JSON + TCP framing + Electron parse + React re-render check). Renderer paints at ≤60 Hz, so 30-50% of these messages are wasted work on both Python (json.dumps + socket.sendall) and Electron (IPC dispatch + React reconciliation).
**Root Cause:** 8 ms gate is below the source period at every supported rate.
**Progress:** Won't Fix (Low-severity, deferred — the bounded queue + PERF-3 latest-only drain already handles backpressure; throttle tuning is a micro-optimization).
**Related Files:**
- `voice_typer/server/waveform_bubble_wiring.py`
**Fix:** Raise the throttle to 16 ms (~60 Hz). Update the `event_bus.py:44` and `log.py:272` comments to match.
**Severity:** 🟢 Low

---

## AB-49 — `audio_quality.analyze_full_audio` allocates 3 full-length temporary arrays (57 MB spike on 5-min recording)
**Status:** 🚫 Won't Fix
**Description:** `audio_quality.py:210,211,231`: `analyze_full_audio` allocates three full-length temporary arrays: `np.sqrt(np.mean(np.square(audio), dtype=np.float64))`, `np.max(np.abs(audio))`, `np.var(audio)`. For a 5-minute @16 kHz recording (4.8M samples ≈ 19 MB), this is ~57 MB of transient peak allocation. The identical metric is computed allocation-free in `AudioProcessor._run_quality_check` (`audio_processor.py:423-425`) using `np.dot(flat, flat)/size` and `max(flat.max(), -flat.min())`.
**User Impact:** A brief 50-60 MB memory spike after `recorder.stop()` (only when `config.audio_quality_warnings=True`; default False short-circuits at `audio_quality_controller.py:221-222`). No leak, but wasteful and inconsistent with the hot-path pattern.
**Root Cause:** Pre-existing implementation predates the allocation-free pattern adopted in `_run_quality_check`.
**Progress:** Won't Fix (Low-severity, deferred — only fires when `audio_quality_warnings=True` which is default False; brief one-time spike).
**Related Files:**
- `voice_typer/server/audio_quality.py`
**Fix:** Replace with allocation-free equivalents: `rms = float(np.sqrt(np.dot(audio, audio) / audio.size))`, `peak = max(float(audio.max()), -float(audio.min()))`, `variance = float(np.dot(audio, audio) / audio.size) - (audio.mean()**2)`.
**Severity:** 🟢 Low

---

## AB-50 — Dead code: `text_cleanup._active_phrase_patterns` + `_active_extra_word_patterns` (XV-42 left eager precompile in place)
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

## AB-51 — `vocabulary_automation._find_closest_vocabulary_match` O(V) scan per low-confidence word (no index structure)
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

## AB-52 — `hotkeys/factory.py:60` docstring claims "1kHz" polling but actual cadence is 125 Hz (8ms sleep)
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

## AB-53 — `native_hotkeys.binary_path.load_binary_manifest` not cached (re-reads binaries.json on every backend spawn)
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

## AB-54 — `crash_handler._python_excepthook` body is duplicated between sys.excepthook and threading.excepthook (DRY violation)
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

## AB-55 — `model_manager._change_model_unload_phase` has dead `elif self.transcriber is not None` branch
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

## AB-56 — `model_manager.try_load` is 142 LOC of dead code with a 60s `wait_for_prewarm` latent perf landmine
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

### Findings from Session 2 (XE prefix — 53 findings)

### XE-19-4 — Medium: prewarm.log handler uses plain RotatingFileHandler — no post-rotation chmod (FR-2 not extended to prewarm.log)
**Status:** ❌ Not Fixed
**Description:** prewarm/logging_setup.py:101-107 creates prewarm.log handler as logging.handlers.RotatingFileHandler — STOCK handler, NOT log._SecureRotatingFileHandler. Initial os.chmod(prewarm_log, 0o600) at line 111-113 locks down at setup time, but RotatingFileHandler.doRollover() (stock) has NO post-rotation chmod hook. After first 5 MiB rotation, new active prewarm.log created with mode 0o666 & ~umask = 0o644 (world-readable on POSIX). Main voice-typer.log was fixed for this exact issue via _SecureRotatingFileHandler (FR-2 fix), but prewarm.log was missed. log.py EXPORTS _SecureRotatingFileHandler, yet prewarm/logging_setup.py doesn't use it.
**User Impact:** After ~50s of prewarm model-warming, prewarm.log crosses 5 MiB and rotates. New active prewarm.log is 0o644 — world-readable on multi-user POSIX systems. Existing test_logging_rotation_perms.py ONLY tests voice-typer.log rotation — prewarm.log rotation has NO perms regression test.
**Root Cause:** FR-2 fix applied to voice-typer.log but not propagated to prewarm.log. Prewarm handler predates FR-2 fix and was never updated.
**Progress:** None yet.
**Related Files:** voice_typer/server/prewarm/logging_setup.py (101-122), voice_typer/server/log.py (exports _SecureRotatingFileHandler at line 1232)
**Fix:** In prewarm/logging_setup.py:101, replace logging.handlers.RotatingFileHandler(...) with `from voice_typer.server.log import _SecureRotatingFileHandler` and `prewarm_handler = _SecureRotatingFileHandler(prewarm_log, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8", errors="backslashreplace")`. Gives prewarm.log same post-rotation chmod (FR-2) AND inter-process rotation lock (XZ-LOG-10). Add regression test mirroring test_logging_rotation_perms.py::test_post_rotation_mode_is_0o600 but for prewarm.log.
**Severity:** 🟡 Medium

### XE-19-5 — Medium: _SecureRotatingFileHandler.doRollover chmod runs OUTSIDE the lock + silently suppresses OSError
**Status:** ❌ Not Fixed
**Description:** log.py:1307-1318 doRollover: lock_fd = self._acquire_rotation_lock(); try: ... super().doRollover(); finally: self._release_rotation_lock(lock_fd); if os.name == "posix": with contextlib.suppress(OSError): os.chmod(self.baseFilename, 0o600). Two issues: (1) os.chmod runs AFTER lock released (outside try/finally). Between super().doRollover() (creates new file at 0o644) and chmod, file is world-readable. (2) contextlib.suppress(OSError) silently swallows chmod failures. If chmod fails, file stays 0o644 until NEXT rotation — potentially hours/days. No WARNING, no metric.
**User Impact:** On multi-user POSIX systems, determined attacker can open voice-typer.log during rotation race window and continue reading dictated-text previews. If chmod fails silently (NFS root-squash, disk-full), log file stays world-readable indefinitely with no operator-visible signal.
**Root Cause:** FR-2 fix applied but placed chmod outside lock scope and added contextlib.suppress(OSError) for "best-effort" safety.
**Progress:** None yet.
**Related Files:** voice_typer/server/log.py (doRollover:1307-1318)
**Fix:** Move chmod INSIDE try block (before finally releases lock): `try: ... super().doRollover(); if os.name == "posix": try: os.chmod(self.baseFilename, 0o600) except OSError as exc: log.warning("..."); finally: self._release_rotation_lock(lock_fd)`. Replace contextlib.suppress with logged try/except.
**Severity:** 🟡 Medium

### XE-19-6 — Medium: Windows rotation lock silently fails — msvcrt.locking failure suppressed, returns fd as if locked
**Status:** ❌ Not Fixed
**Description:** log.py:1261-1269 Windows lock-acquisition: `fd = os.open(...); os.write(fd, b"\0"); os.lseek(fd, 0, SEEK_SET); with contextlib.suppress(OSError): msvcrt.locking(fd, msvcrt.LK_LOCK, 1); return fd`. msvcrt.locking(fd, LK_LOCK, 1) blocks ~10 seconds then raises PermissionError (subclass of OSError) if byte still locked. contextlib.suppress SWALLOWS this, returns fd (valid). Caller treats any non-None return as "lock acquired" and proceeds with rotation. So on Windows, if two processes race to rotate and second waits >10s, second process rotates WITHOUT lock — exactly the race XZ-LOG-10 was designed to prevent.
**User Impact:** On Windows, prewarm scheduled task and main backend can both write voice-typer.log. If both hit 5 MiB threshold within 10 seconds, second's lock acquisition times out, silently suppressed — both rotate concurrently, second's os.rename fails, second re-opens voice-typer.log in write mode (truncating it). DJ-49 race re-emerges on Windows despite XZ-LOG-10 lock.
**Root Cause:** contextlib.suppress(OSError) around msvcrt.locking conflates "lock acquisition failed" (rotation-safety-critical) with "lock file couldn't be opened" (recoverable).
**Progress:** None yet.
**Related Files:** voice_typer/server/log.py (_acquire_rotation_lock:1251-1278, Windows branch:1261-1269)
**Fix:** Remove contextlib.suppress(OSError) around msvcrt.locking. Let PermissionError propagate to outer except Exception (line 1270), which closes fd and returns None. Add Windows-specific timeout-retry: if LK_LOCK raises after 10s, try LK_NBLCK once; if that fails, return None. Log at WARNING (not DEBUG) when Windows lock times out.
**Severity:** 🟡 Medium

### XE-19-7 — Medium: Startup banner reports ROOT logger level (WARNING) instead of voice_typer logger level
**Status:** ❌ Not Fixed (duplicate of XE-5-B from different agent)
**Description:** logging_setup.py:70 reads `_root_level = logging.getLogger().level` — ROOT logger (Python default WARNING=30). setup_logging only modifies logging.getLogger("voice_typer"). So banner always reports level=WARNING regardless of debug/quiet flags. Verified empirically.
**User Impact:** Operator reading voice-typer.log sees `[STARTUP] logging initialized: file=..., level=WARNING, ..., debug=True` — cannot tell whether DEBUG records are being captured.
**Root Cause:** logging.getLogger() (no name arg) returns true root logger, whose level is never modified by setup_logging.
**Progress:** None yet.
**Related Files:** voice_typer/server/logging_setup.py (line 70)
**Fix:** Change to `_root_level = logging.getLogger("voice_typer").level`.
**Severity:** 🟡 Medium

### XE-20-1 — Medium: Rust + TS redact_pii both omit Python's generic 20+ char bare-token pattern and SEC-9 flag/key=value patterns
**Status:** ❌ Not Fixed
**Description:** Python's canonical _secrets._KEY_PATTERNS contains 5 patterns: Bearer, Token, sk-, gsk_, and generic 20+ char bare-alphanumeric-token catch-all (`\b[A-Za-z0-9_\-]{20,}\b`). Python also has _FLAG_KEY_PATTERNS (SEC-9) for --token=abc, token=abc, api_key=abc forms. Rust redact_pii (logging.rs:323-499) ports only Bearer/Token/sk-/gsk_ — omits generic 20+ char pattern. TS redactPii (rotation.ts:59-84) ports Bearer/Token/sk-/pk-/key- — no generic 20+ char pattern, no gsk_, no SEC-9 flag patterns.
**User Impact:** Bare API keys without known prefix — GitHub PATs (ghp_...), GitLab PATs (glpat-...), Slack tokens (xoxb-...), Anthropic keys (sk-ant-...), 20+ char session tokens — appear UNREDACTED in Rust (voice-typer-rust.log) and TS (electron-*.log) file logs, but ARE redacted in Python sidecar log. Flag-form secrets (--token=abc123, api_key=xyz) similarly leak in Rust/TS. Support bundle containing all three log files exposes secret in 2 of 3 layers.
**Root Cause:** Both ports written pattern-by-pattern from Python source but stopped before generic catch-all. SEC-9 flag patterns never ported. No cross-layer parity test.
**Progress:** None yet.
**Related Files:** src-tauri/src/platform/logging.rs (redact_pii:323-499), voice_typer/client/src/main/logging/rotation.ts (redactPii:59-84), voice_typer/server/_secrets.py (_KEY_PATTERNS:43-68, _FLAG_KEY_PATTERNS:70-164)
**Fix:** (a) Add generic 20+ char bare-token redaction step to both Rust redact_pii and TS _SECRET_PATTERNS. (b) Port SEC-9 _FLAG_KEY_PATTERNS to both sides. (c) Add cross-layer parity test that feeds corpus of known-secret shapes to Python redact_secret + Rust redact_pii + TS redactPii and asserts all three produce same redacted output.
**Severity:** 🟡 Medium

### XE-20-2 — Medium: TS redactPii missing gsk_ pattern; sk- charset too restrictive
**Status:** ❌ Not Fixed
**Description:** Python _secrets.py:54 and Rust logging.rs:392 both redact gsk_<token> (Groq API keys). TS rotation.ts:77-84 _SECRET_PATTERNS has NO gsk_ pattern — only sk-, pk-, key-. Groq API key logged via logger.warn or log.error lands unredacted in electron-*.log. Additionally, TS sk- regex `/\b(?:sk|pk|key)-[A-Za-z0-9]{10,}\b/g` uses charset [A-Za-z0-9] (alphanumeric only), while Python (sk-[A-Za-z0-9_\-]+) and Rust (is_ascii_alphanumeric() || '-' || '_') both include _ and -. OpenAI project key like sk-proj-1234567890abcdef (contains dashes) redacted by Python and Rust but NOT by TS — regex matches sk- then proj (4 chars < 10 minimum), fails, leaves entire key visible.
**User Impact:** Groq API keys and OpenAI project-scoped keys (sk-proj-...) leak in Electron-side log files. Python and Rust redact them correctly. Cross-layer inconsistency means single support bundle exposes key in Electron layer.
**Root Cause:** TS port hand-transcribed patterns, forgot gsk_, used more restrictive charset than Python canonical regex. No parity test caught drift.
**Progress:** None yet.
**Related Files:** voice_typer/client/src/main/logging/rotation.ts (77-84), voice_typer/server/_secrets.py (50-54), src-tauri/src/platform/logging.rs (377-401)
**Fix:** (a) Add `[/\bgsk_[A-Za-z0-9_\-]{8,}\b/g, "gsk_***"]` to _SECRET_PATTERNS. (b) Change sk-/pk-/key- regex from [A-Za-z0-9]{10,} to [A-Za-z0-9_\-]{8,} to match Python's charset and Rust's 8-char threshold. (c) Add unit tests asserting redactPii("groq key gsk_1234567890abcdef") returns "groq key gsk_***" and redactPii("openai sk-proj-1234567890abcdef") returns "openai sk-***".
**Severity:** 🟡 Medium

### XE-20-3 — Medium: deleteElectronPersonalDataLogs omits electron-runtime.log and electron-lifecycle.log from GDPR erasure scope
**Status:** ❌ Not Fixed
**Description:** structuredLogger.ts:270-310 deleteElectronPersonalDataLogs (GDPR Art. 17 helper for Electron-side logs) filters readdirSync(userData) for only electron-main.log and electron-renderer-errors.log (lines 285-291). Does NOT include electron-runtime.log (written by printfLogger.ts:230-247 for every WARN/ERROR from printf-style log.* API) or electron-lifecycle.log (written by appendLifecycleLine for opt-in INFO persistence when VOICE_TYPER_ELECTRON_INFO_LOG=1).
**User Impact:** When FR-59 wires this helper to IPC handler, GDPR "delete all personal data" request would leave electron-runtime.log (up to 10 MiB of WARN/ERROR lines) and electron-lifecycle.log (up to 2 MiB of opt-in INFO) on disk. Redaction not perfect (no generic bare-token pattern — see XE-20-1; no dictated-text redaction), so residual user data survives erasure.
**Root Cause:** Helper authored before electron-runtime.log (printfLogger split) and electron-lifecycle.log (PERSIST_INFO opt-in) were added. Deletion scope never updated.
**Progress:** None yet.
**Related Files:** voice_typer/client/src/main/logging/structuredLogger.ts (270-310), voice_typer/client/src/main/logging/printfLogger.ts (230-247), voice_typer/client/src/main/logging/structuredLogger.ts (132-172)
**Fix:** Extend candidates filter at lines 285-291 to also match `name === "electron-runtime.log" || name.startsWith("electron-runtime.log.")` and `name === "electron-lifecycle.log" || name.startsWith("electron-lifecycle.log.")`. Add unit test that creates all four log files (+ .1 backups) in temp userData dir, calls deleteElectronPersonalDataLogs, asserts all unlinked.
**Severity:** 🟡 Medium

---

### Findings from Session 3 (UE prefix — 50 findings)

### UE-1 — `_do_fast_cleanup` is dead code; Windows logoff/shutdown uses slow path → silent data loss
**Status:** ❌ Not Fixed
**Description:** `shutdown_controller._do_fast_cleanup` (85 LOC, written for XZ-R17-06) is never invoked. `signal_handlers.win32_console_handler` routes `CTRL_LOGOFF_EVENT`/`CTRL_SHUTDOWN_EVENT` to `controller.quit()` (full `_do_cleanup`, ~25-85s) instead. Windows gives the process ~5s before force-kill. The fast path was specifically written to run critical-only cleanup with 1s timeouts each (<3s total) — but the cross-file routing change was never made.
**User Impact:** When the user logs off, shuts down, or restarts Windows while Voice Typer is running, the OS kills the process mid-cleanup. Pending history_db INSERTs and crash_recovery snapshots are silently lost. The single-instance mutex + PID file may not be released, blocking the next launch. The Electron subprocess may be orphaned.
**Root Cause:** The XZ-R17-06 follow-up task ("route logoff/shutdown to `_do_fast_cleanup`") was documented in the method docstring but never implemented in `signal_handlers.py`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`
- `voice_typer/server/signal_handlers.py`
**Fix:** In `signal_handlers.win32_console_handler`, route `ctrl_logoff_event`/`ctrl_shutdown_event` to `controller._do_fast_cleanup()` instead of `controller.quit`. Add a final `os._exit(0)` at the end of `_do_fast_cleanup` (cleanup is complete; bypassing atexit is acceptable). Add a regression test asserting `_do_fast_cleanup` is the dispatch target.
**Severity:** 🔴 Critical

---

### UE-2 — `_teardown_sounddevice` ignores `wait()` return value → DE-54 PortAudio deadlock
**Status:** ❌ Not Fixed
**Description:** `shutdown_controller._teardown_sounddevice` (line 873) calls `self._recorder_teardown_done.wait(timeout=9.5)` but discards the return value. When the outer 10s timeout leaks the `_teardown_recorder` worker mid-execution, `_recorder_force_closed` is never published (set only at the final line 695) and the event is never set. The code reads `self._recorder_force_closed` (False), proceeds to `sd.stop()`, and reproduces the exact DE-54 PortAudio deadlock the code documents as avoided.
**User Impact:** On shutdown where `recorder.stop()` raises and `recorder.discard()` hangs near the 10s deadline (WASAPI/PortAudio backends), the host deadlocks on `sd.stop()`. Leaked daemon threads access PortAudio when `tray.stop()`/`os._exit()` fires, risking segfault or corrupted audio device state across restarts.
**Root Cause:** The happens-before contract between `_teardown_recorder` and `_teardown_sounddevice` assumes the recorder helper always reaches its final line. The `wait()` return value (True=set, False=timeout) is the correct signal but is ignored.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py`
**Fix:** `done = self._recorder_teardown_done.wait(timeout=9.5); if not done or self._recorder_force_closed: log.warning(...); return` — skip `sd.stop()` on timeout/force-close.
**Severity:** 🔴 High

---

### UE-3 — `CrashRecovery.mark_pasted` deadlocks post-shutdown (non-reentrant lock re-entry)
**Status:** ❌ Not Fixed
**Description:** `mark_pasted()` calls `_enqueue_save()` **inside** `with self._lock:`. When `_stopped == True` (post-`shutdown()`), `_enqueue_save` falls through to synchronous `_save_sync()`, which acquires `_save_lock` then tries to acquire `_lock` for the JSON snapshot. `self._lock` is a non-reentrant `threading.Lock()` — the same thread already holds it → permanent deadlock. Sibling methods (`add`, `mark_latest_pasted`, `clear`) all call `_enqueue_save()` **outside** the lock — `mark_pasted` is the inconsistent outlier.
**User Impact:** Any post-shutdown call to `mark_pasted(index)` hangs the calling thread indefinitely; only a process kill recovers. `mark_latest_pasted` is called in production (`dictation_pipeline.py:1618`); the asymmetric `mark_pasted` is a public API method.
**Root Cause:** Lock-acquisition inconsistency — `mark_pasted` was not updated when siblings were fixed to release the lock before enqueue.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_recovery.py`
**Fix:** Move `_enqueue_save()` outside the `with self._lock:` block in `mark_pasted`, matching `add`/`mark_latest_pasted`/`clear`. Add a regression test that calls `mark_pasted` after `shutdown()` and asserts no hang.
**Severity:** 🔴 High

---

### UE-4 — Sidecar restart storm: circuit breaker granularity is per-`respawn`, not per-`app.restart()`
**Status:** ❌ Not Fixed
**Description:** The disk-persisted restart counter (`MAX_RESTART_ATTEMPTS = 3`) is incremented once per `respawn` invocation, NOT per `app.restart()` exhaustion relaunch. `respawn_inner` runs the full 5-iteration backoff schedule (~15.5s) before falling back to `app.restart()`. On a permanently-broken install, each fresh process relaunch triggers a fresh `respawn`, bumping the counter by 1. The breaker trips only after 3 full process relaunches (~47s of OS-level app flicker: window recreate ×3, mic open/close ×3, hotkey re-register ×3) before the user sees `supervisor_failed`.
**User Impact:** A broken install produces a ~47s restart storm — the window flashes open/shut 3 times with no error banner. The circuit breaker was designed to prevent exactly this but its granularity defeats the intent.
**Root Cause:** Counter increment is at the wrong level (per-inner-restart-attempt rather than per-outer-relaunch).
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs`
**Fix:** Increment the counter on the `app.restart()` path explicitly and check `restart_count >= MAX` *before* calling `app.restart()` (so the breaker trips on the 3rd relaunch attempt, not the 4th). Also clear `respawn_in_progress` immediately before `app.restart()` as defense-in-depth (currently relies on `-> !` divergence).
**Severity:** 🔴 High

---

### UE-5 — Diagnostic bundle ships archived crash dumps UNREDACTED (PII/secret leak)
**Status:** ❌ Not Fixed
**Description:** `diagnostics_export.create_diagnostic_bundle` writes prior `crash_diagnostics_archive/*` files into the zip **verbatim** via `zf.write(...)`. The line-by-line `redact_secret(redact_pii(line))` pipeline that protects the live `voice-typer.log` is NOT applied to archived crash dumps. Each archived file embeds a Python traceback + `sys.modules` snapshot + platform header — any of which can carry API keys (URL query-string `?key=sk-…`), env-var dumps, bearer tokens, or `str(exception)` payloads.
**User Impact:** A user who attaches the diagnostic bundle to a support ticket leaks every secret-bearing crash traceback from prior sessions in cleartext — the very failure mode the live-log redaction exists to prevent.
**Root Cause:** The redaction pipeline was wired for the live log but never extended to the archive path.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/diagnostics_export.py`
- `voice_typer/server/_secrets.py`
- `voice_typer/server/security.py`
**Fix:** Read each archived file as text (`errors="replace"`), pipe through `redact_secret(redact_pii(line), aggressive=True)`, and `zf.writestr(...)` the redacted bytes. Introduce a unified `redact_for_export(text)` in `_secrets.py` (running `redact_secret(redact_pii(text), aggressive=True)`) and route BOTH `ipc_diagnostics.write_startup_diagnostic` AND `diagnostics_export.create_diagnostic_bundle` through it. Also pass `aggressive=True` at the live-log redaction call site.
**Severity:** 🔴 High

---

### UE-6 — Rust PII redaction INCOMPLETE vs Python: missing 20+ char token + flag/`key=value` patterns
**Status:** ❌ Not Fixed
**Description:** `logging.rs::redact_pii` fast-path skips `key=` and the 20+ char alphanumeric trigger (Python's `_FAST_TRIGGER` includes both). Python `_KEY_PATTERNS` has 5 patterns; Rust implements only the first 4 (`Bearer`, `Token`, `sk-`, `gsk_`). Missing in Rust: (1) `\b[A-Za-z0-9_\-]{20,}\b` — catches bare GitLab/GitHub/Slack PATs with no prefix; (2) `_FLAG_KEY_PATTERNS` — redacts `--token=abc`, `token=abc`, `password=abc`, `secret=abc`, `api_key=abc`, etc. across 15+ secret keywords. A sidecar stderr line `pat=glpt_Xb8zV9pT3q2aR1wM5sN7` (24-char GitLab PAT, no Bearer prefix) is redacted by Python but written verbatim to `voice-typer-rust.log` by the Rust host.
**User Impact:** Secrets that Python correctly scrubs leak into the Rust-side log file (and from there into diagnostic bundles if the Rust log is ever bundled). Asymmetric redaction between the two layers.
**Root Cause:** Hand-rolled byte-state-machine port of Python regexes — each new Python pattern requires hand-porting a new state machine, and omissions are silent.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs`
- `voice_typer/server/_secrets.py` (reference for parity)
**Fix:** Extend `redact_pii` with (a) flag-form matcher mirroring `_FLAG_VALUE_PATTERN` + `_BARE_KEY_VALUE_PATTERN` keyword list, (b) 20+ char alphanumeric-run catch-all, (c) add `key=` and `has_20plus_alphanumeric_run` to the fast-path trigger list. Add parity tests that assert both layers redact the same secret-bearing strings.
**Severity:** 🔴 High

---

### UE-7 — `spawn_heartbeat_task` take/store race leaks heartbeat tasks
**Status:** ❌ Not Fixed
**Description:** The take+abort+spawn+store sequence in `spawn_heartbeat_task` drops the `heartbeat_handle` AsyncMutex lock between `hb_guard.take()` and `*hb_guard = Some(handle)` — the window spans the entire `tauri::async_runtime::spawn(...)` call. `reconnect_ws` is called from TWO unsynchronized paths: `main.rs:416` (cold start, NOT under `respawn_in_progress`) and `supervisor.rs:457` (under the flag). If a reader-exit during cold-start auth triggers `trigger_respawn_off_thread`, both reconnects can interleave their take/store: cold-start takes None → respawn takes None → cold-start stores H1 → respawn stores H2 (overwrites H1, H1 is never aborted).
**User Impact:** Leaked heartbeat task runs indefinitely, dispatching `heartbeat` frames every 10s to a dead WS, generating spurious miss logs and competing with the live heartbeat. After N reconnects, up to N leaked tasks. Self-bounded only by process lifetime.
**Root Cause:** Lock not held across the spawn+store; the abort of the previous handle happens after the store instead of being atomic with the take.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Hold the `heartbeat_handle` lock across spawn + store. Abort the previous handle AFTER releasing the lock: `let prev = { let mut g = state.heartbeat_handle.lock().await; let prev = g.take(); *g = Some(spawn(...)); prev }; if let Some(p) = prev { p.abort(); }`.
**Severity:** 🟡 Medium

---

### UE-8 — `cleanup_and_trigger_respawn` does NOT drain pending → in-flight dispatches orphaned
**Status:** ❌ Not Fixed
**Description:** The auth-failure cleanup clears `state.ws_tx` and triggers respawn, but explicitly does NOT drain `state.pending` (comment: "at auth time no dispatch requests have been queued yet"). This assumption is FALSE: `queue_auth_and_store_ws_tx` stores `ws_tx` BEFORE `wait_for_auth_ok` runs. Any `dispatch` Tauri command invoked in that window (up to 3s auth timeout) will clone `ws_tx` (Some), insert into `pending`, `try_send` (succeeds — writer task is running), and await a response that will never come (server hasn't authed, frame is dropped server-side). Each such dispatch leaks a `oneshot::Sender` in `pending` until its 15s/120s timeout.
**User Impact:** On a slow sidecar start, the renderer's `get_status` poll + model-list poll + settings read can all queue, leaking 3+ entries per cold start. Bounded by timeout but produces misleading "dispatch timeout" errors and ~120s of stuck UI.
**Root Cause:** Stale assumption that no dispatches are queued before auth completes.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Drain `pending` in `cleanup_and_trigger_respawn` (mirror the reader-cleanup block), sending `{"type":"error","data":{"code":"sidecar_disconnected"}}` to each orphaned oneshot.
**Severity:** 🟡 Medium

---

### UE-9 — Streaming session TOCTOU: `_stop_impl` pokes private `_cancel_event`, leaves session dangling
**Status:** ❌ Not Fixed
**Description:** `recording_controller._stop_impl` reads the streaming session via `get_streaming_session()`, then directly pokes `session._cancel_event.set()` under `contextlib.suppress(Exception)`. The session is NEVER popped — `self._streaming_session` continues to point at the now-cancelled session. `pop_streaming_session()` was specifically introduced to make get+clear atomic for the cancellation path (ARCH-018), but `_stop_impl`'s main path bypasses it. Accessing the private `_cancel_event` attribute is a fragile contract; any rename is silently swallowed by `suppress(Exception)`.
**User Impact:** Stale-session reference held across the entire transcription window. A concurrent `_cancel_streaming_session()` pops the already-cancelled session and calls `cancel()` again (relies on idempotency). Inconsistent with the early-return path at line 627 which does pop.
**Root Cause:** `_stop_impl` was not migrated to use the atomic `pop_streaming_session()` helper when it was introduced.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording_controller.py`
**Fix:** Use `pop_streaming_session()` + `session.cancel()` (public method) instead of get + private-attr poke.
**Severity:** 🔴 High

---

### UE-10 — Pipeline finally block reintroduces ARCH-018 TOCTOU on streaming session (FT-5 family)
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.run()`'s finally block does `session = get_streaming_session()` (lock #1) → `if session is not None and not recorder.recording:` (stale snapshot) → `set_streaming_session(None)` (lock #2). This is exactly the get-then-check-then-set pattern that `pop_streaming_session()` was introduced to eliminate. Between the get and the set, a concurrent `_start_streaming_session_if_enabled` can install a NEW session — which this code then clobbers with `None`, leaking an active streaming worker thread.
**User Impact:** After rapid stop→start (user double-tap hotkey, or auto-stop Timer immediately followed by hotkey), the new recording's streaming session is killed silently. Streaming transcriptions stop appearing until the next restart. This is a confirmed FT-5 (finish dictation → nothing transcribed) family race.
**Root Cause:** Pipeline finally block predates the atomic `pop_streaming_session()` helper and was never migrated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py`
**Fix:** Use `pop_streaming_session()` (atomic) in the finally block — own the session before clearing, never write back to the slot. Also fix the `_transcribe` path (UE-10 sibling): use `pop_streaming_session()` before `session.finalize()` so the slot is cleared even if finalize raises.
**Severity:** 🔴 High

---

### UE-11 — `set_active_backend` unloads model mid-transcription (no busy/recording guard) → crash/corruption
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline._transcribe` captures `active = models.active_transcriber()` then calls `active.transcribe_with_fallback(...)` (1-30s ctranslate2/torch inference). `model_manager.set_active_backend` (IPC-triggered by renderer backend dropdown) unconditionally runs `_change_model_unload_phase` (unloads the OLD backend, frees VRAM) — it does NOT consult `recorder.recording` or `_busy_event.is_set()`, unlike `change_model` which defers when busy. During an active transcription, the ctranslate2 model currently executing inference is unloaded from underneath the transcribe thread. Outcomes: corrupted output, segfault/heap corruption, or a stuck thread.
**User Impact:** If the user changes the ASR backend dropdown while a dictation is transcribing, the app can crash, produce garbage text, or hang. The `device_info` re-fetch at line 785 also races — a concurrent swap returns a different backend, so the tray reports the wrong device for the result just produced.
**Root Cause:** `set_active_backend` lacks the busy/recording guard that `change_model` has.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py`
- `voice_typer/server/dictation_pipeline.py` (capture `active` once, reuse for device_info)
**Fix:** `set_active_backend` should mirror `change_model`'s deferral: if `recorder.recording or not _busy_event.is_set()`, capture the requested backend in `_pending_backend_change` (sibling to `_pending_model_change`) and apply on next `_start_dictation`. In `_transcribe`, capture the backend object once and reuse the local for `device_info` (fixes UE-10-F6 redundant+racy second `active_transcriber()` call).
**Severity:** 🔴 High

---

### UE-12 — Dead 1591-line `level_monitor.py` monolith shadowed by package (dead code + confusion)
**Status:** ❌ Not Fixed
**Description:** Python prefers the `level_monitor/` package over the same-named `level_monitor.py` module. Runtime check confirms `level_monitor.__file__` resolves to `level_monitor/__init__.py`. The 1591-line `level_monitor.py` is STALE: it lacks ER-14 (`_idle_timeout_auto_stop`, `_LEVEL_IDLE_TIMEOUT_SEC`), ER-75 (`_LEVEL_WORKER_BACKSTOP_TIMEOUT_SEC`), and R3-F6 features present in the package. The `__init__.py` docstring explicitly says "This file was previously a 1586-line god-module" (AC-129).
**User Impact:** 1591 LOC of dead code shipped in production. Any maintainer reading `level_monitor.py` first will be misled. Diverging bug fixes (the package has ER-14 idle auto-stop logic; the monolith does not) — anyone who later "fixes" the monolith thinking it's live will introduce drift.
**Root Cause:** The AC-129 split extracted the package but left the original file in place.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor.py` (DELETE)
**Fix:** Delete `level_monitor.py`. No production import resolves to it (verified: `microphone_test_recorder.py` does `import voice_typer.server.level_monitor as _lm` — gets the package). Record deletion in `archive/deleted_files.txt`.
**Severity:** 🔴 High

---

### UE-13 — Unprotected stdin IPC path is still the default (security + reliability)
**Status:** ❌ Not Fixed
**Description:** In TCP mode, `transport_tcp.py` refuses ALL connections if `VOICE_TYPER_IPC_TOKEN` is unset. In stdin mode (`ipc_server._run`), there is NO auth — any process that can write to the backend's stdin dispatches arbitrary commands including `quit_app`, `shutdown`, `set_config`, `delete_all_personal_data`, `import_model`. The `start()` docstring acknowledges this but `python -m voice_typer.server.ipc_server` (no args) STILL starts the stdin listener as the default with no env-var gate, no deprecation warning, no log line.
**User Impact:** A misconfigured Tauri host that forgets `--ws` or `--port` silently falls back to unauthenticated stdin IPC. Any local process can pipe JSON to stdin and invoke destructive commands.
**Root Cause:** The `--ws` flag was added for the Tauri migration but the legacy stdin default was never gated.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
**Fix:** Gate the stdin listener behind `VOICE_TYPER_ALLOW_STDIN_IPC=1`; log a WARNING when stdin mode is entered without the gate. Add a `_shutdown_started: threading.Event` gate to `_handle_shutdown` (no-op the second invocation) while editing this file — addresses UE-18-F10 sibling.
**Severity:** 🔴 High

---

### UE-14 — `bubble_dismiss` Rust command MISSING → silent no-op under Tauri (reliability regression)
**Status:** ❌ Not Fixed
**Description:** TS interface `BubbleWindowExtras.dismiss?: () => void` is declared OPTIONAL with comment "the Tauri bridge does not yet implement `dismiss` (no `bubble_dismiss` Rust command); the dismiss-button click handler tolerates the missing method via optional chaining." No `bubble_dismiss` exists in `#[tauri::command]` list nor in `generate_handler![]`. The Electron-side `bubble-handlers.ts` HAS the `bubble:dismiss` IPC handler. Renderer `Bubble.tsx:180` does `getBubbleApi()?.dismiss?.()` — under Tauri this silently no-ops. Tests mock `dismiss: vi.fn()`, masking the contract drift.
**User Impact:** In `always_visible` bubble mode (the only mode where the dismiss '×' button is shown), clicking the button does nothing under the Tauri host. The bubble stays visible. The user has no way to manually hide it short of toggling dictation.
**Root Cause:** The Tauri migration added bubble commands but missed `dismiss`; the optional-chaining "tolerates" the regression rather than fixing it.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/main.rs` (generate_handler registration)
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts`
- `voice_typer/client/src/renderer/src/types/ipc/bubble_bridge.ts`
**Fix:** Add a `bubble_dismiss` Rust command (mirror of `bubble_hide_complete` but unconditionally hides the bubble window). Register it in `generate_handler![]`. Wire `dismiss` in `bubble-namespace.ts`. Also fix `bubble_resize`/`bubble_move_by` u32/i32 coercion (accept `f64`, round in Rust) and the clamp-bounds divergence with Electron — addresses UE-19-F03/F04 siblings.
**Severity:** 🔴 High

---

### UE-15 — 17 dead `_handle_*` methods across handler modules (~1100 LOC dead)
**Status:** ❌ Not Fixed
**Description:** Cross-referenced `_COMMAND_REGISTRY` against `ALLOWED_COMMANDS` and `_handle_*` definitions. 17 handler methods exist on `IPCServer` (so `getattr` would resolve them) but are NOT registered AND NOT in the renderer allowlist: `_handle_refresh_microphones`, `_handle_microphone_test_status`, `_handle_level_monitor_status`, `_handle_test_llm_connection`, `_handle_export_diagnostics`, `_handle_check_accessibility`, `_handle_show_electron_notification`, `_handle_onboarding_get_step`, `_handle_onboarding_get_model_catalog`, `_handle_onboarding_request_keyboard_permission`, `_handle_delete_all_personal_data`, `_handle_export_gdpr_bundle`, `_handle_get_vocabulary_suggestions`, `_handle_apply_vocabulary_suggestion`, `_handle_dismiss_vocabulary_suggestion`, `_handle_get_rms_level`, `_handle_get_audio_status`. The `privacy_handlers.py` docstring still CLAIMS `delete_all_personal_data`/`export_gdpr_bundle` are registered (stale). The entire `vocabulary_automation_handlers.py` module (305 lines, 3 handlers) is 100% dead.
**User Impact:** ~1100 LOC of dead production code inflating the surface area, slowing review, and misleading maintainers. Stale docstrings actively lie about registration. Tests exercise dead IPC surface.
**Root Cause:** Commands were removed from the registry during the Tauri migration (moved to Rust host) but the Python handler methods and their unit tests were never deleted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/handlers/microphone_handlers.py`
- `voice_typer/server/handlers/microphone_test_handlers.py`
- `voice_typer/server/handlers/level_monitor_handlers.py`
- `voice_typer/server/handlers/model_handlers.py`
- `voice_typer/server/handlers/system_handlers.py`
- `voice_typer/server/handlers/onboarding_handlers.py`
- `voice_typer/server/handlers/privacy_handlers.py`
- `voice_typer/server/handlers/status_handlers.py`
- `voice_typer/server/handlers/vocabulary_automation_handlers.py` (DELETE entire module)
**Fix:** Delete the 17 dead `_handle_*` methods. Delete the entire `vocabulary_automation_handlers.py` module. Fix the stale docstrings in `privacy_handlers.py` and `model_handlers.py`. Remove corresponding dead unit tests in `tests/handlers/`. Record module deletion in `archive/deleted_files.txt`.
**Severity:** 🟡 Medium

---

### UE-16 — Rate-limit summary dicts evade LRU cap → unbounded memory; summary demotes ERROR→INFO
**Status:** ❌ Not Fixed
**Description:** `_RATE_LIMIT_COUNTS` is an `OrderedDict` capped at `_MAX_COUNTERS=1024` with LRU eviction. But `_RATE_LIMIT_NEXT_SUMMARY_DEADLINE` and `_RATE_LIMIT_SUPPRESSED_SINCE_SUMMARY` (keyed by the same `counter_key` tuple) are NEVER pruned when their keys are evicted — they grow without bound with dynamic messages. Separately, the GT-66 summary is hardcoded at `_log.info(...)` — if a caller invokes `log_rate_limited(log, logging.CRITICAL, ...)` and the error fires 1000× in 60s, the operator sees one CRITICAL line then ~60s later an INFO summary. The CRITICAL severity is lost; alerting rules keyed on `level>=ERROR` miss the recurrence.
**User Impact:** Slow memory leak in long-running server with dynamic error messages. Alerting on error-rate-limited paths silently degrades.
**Root Cause:** LRU cap applied to only one of three correlated dicts; summary level hardcoded.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/log_rate_limit.py`
**Fix:** In the eviction loop, also `popitem(last=False)` from the two summary dicts. Compute summary level as `max(logging.INFO, level)` so an ERROR-rate-limited path surfaces an ERROR summary.
**Severity:** 🟡 Medium

---

### UE-17 — Rotation chmod happens AFTER lock release → TOCTOU perms window (PII exposure)
**Status:** ❌ Not Fixed
**Description:** `_SecureRotatingFileHandler.doRollover()` calls `super().doRollover()` (creates new file with process's restored umask, often 0o022 → 0o644 world-readable) inside the rotation lock, then releases the lock, THEN does `os.chmod(self.baseFilename, 0o600)`. The chmod runs AFTER the inter-process rotation lock is released. A different local user could `open()` the file directly during that window and read dictated-text fragments.
**User Impact:** Brief world-readable window on the log file after each rotation (5×5 MiB → rotates ~daily under heavy use). Dictated-text fragments in the log are readable by other local users during the window.
**Root Cause:** chmod placed outside the lock; umask not tightened during file creation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/log.py`
**Fix:** `os.umask(0o077)` at the start of `doRollover` and restore in `finally` so the file is created 0o600 from the start (eliminates the race window). Also move the chmod inside the `try:` block before `_release_rotation_lock` as belt-and-suspenders.
**Severity:** 🟡 Medium

---

### UE-18 — `_handle_shutdown` spawns untracked cleanup thread; double-shutdown race
**Status:** ❌ Not Fixed
**Description:** `_handle_shutdown` returns the ack envelope, then spawns a daemon thread (`name="ipc-shutdown-cleanup"`) to run `self.service.quit()`. The thread is NOT registered on `self.app._thread_registry` (compare `start()` which explicitly registers `heartbeat-watchdog` and `ipc-server`). If the host sends `shutdown` then SIGTERM (or two `shutdown` frames arrive on different connections), two cleanup threads spawn and may interleave resource teardown — `recorder.stop()` while the other flushes `crash_recovery.json`, etc.
**User Impact:** Race on `recorder.stop()`, `history_db.flush()`, `hotkey.unregister()`, PID file clear, `tray.stop()`, Win32 mutex `CloseHandle` — any may run twice or interleave, leaving resources half-released.
**Root Cause:** No idempotency gate on the shutdown handler.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
**Fix:** Add `_shutdown_started: threading.Event` gate at the top of `_handle_shutdown` that no-ops the second invocation. Register the cleanup thread on `_thread_registry`.
**Severity:** 🟡 Medium

---

### UE-19 — `permissions.probe_native_listener` + `request_*_result` variants are fully dead (~280 LOC)
**Status:** ❌ Not Fixed
**Description:** `probe_native_listener` (~130 LOC) has ZERO production callers — documented purpose ("onboarding Test hotkey button") was never wired to an IPC handler. `request_keyboard_permission_result`, `request_microphone_permission_result`, and `request_microphone_permission` (~150 LOC total) have ZERO production callers — only docstring cross-references. The IPC handler calls the non-`_result` variant `request_keyboard_permission`.
**User Impact:** ~280 LOC of dead code in a 1495-line `permissions.py` module, inflating review surface.
**Root Cause:** Onboarding feature was scoped down; the probe + result-variant functions were never removed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/permissions.py`
**Fix:** Delete `probe_native_listener`, `request_keyboard_permission_result`, `request_microphone_permission_result`, `request_microphone_permission`. Remove corresponding dead unit tests. Verify no sibling-repo imports (grep confirms zero).
**Severity:** 🟡 Medium

---

### UE-20 — Dead `theme-utils.ts` (368 LOC) + divergent `formatBytes` triplication
**Status:** ❌ Not Fixed
**Description:** `lib/theme-utils.ts` (368 LOC) has ZERO importers — all its exports are duplicated by `lib/color-utils.ts` (`contrastRatio`) and `lib/theme-draft-storage.ts` (`saveDraftToLS`/`loadDraftFromLS`/`clearDraftLS`) which ARE imported. Separately, `formatBytes` exists in 3 divergent copies: `lib/format.ts:227` (returns "0 B"), `About.tsx:120` (returns "0 MB" for ≤0), `DownloadProgressBar.tsx:77` (returns "—"). The `lib/format.ts` docstring claims XA-20-7 consolidation but `DownloadProgressBar.tsx` still defines its own local copy. `formatSpeed` is similarly dead in `lib/format.ts`.
**User Impact:** 368 LOC dead module masking live modules; risk of accidental import of the wrong module. Three `formatBytes` implementations drift independently — same input produces different output depending on which page renders it.
**Root Cause:** Consolidation claimed but never completed; dead module never deleted.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/theme-utils.ts` (DELETE)
- `voice_typer/client/src/renderer/src/lib/format.ts`
- `voice_typer/client/src/renderer/src/pages/About.tsx`
- `voice_typer/client/src/renderer/src/components/DownloadProgressBar.tsx`
**Fix:** Delete `lib/theme-utils.ts`. Move `formatBytes`/`formatRelativeTime` from `About.tsx` to `lib/format.ts`. Replace `DownloadProgressBar.tsx`'s local `formatBytes`/`formatSpeed` with imports from `lib/format.ts`. Delete the dead `formatBytes`/`formatSpeed` from `lib/format.ts` OR (preferred) keep them and make all consumers import from there. Record deletion in `archive/deleted_files.txt`.
**Severity:** 🟡 Medium

---

### UE-21 — `_run_with_timeout` leaks worker threads; `_run_parallel_with_timeout` silently drops duplicate-desc items
**Status:** ❌ Not Fixed
**Description:** `_run_with_timeout` returns `TIMEOUT` and leaks the worker thread if it doesn't finish in `timeout`. Callers that ignore the return value race the leaked worker — `shutdown_controller.py` calls it ~14 times, each a potential leak point. Separately, `_run_parallel_with_timeout` re-orders results by `desc` via `by_desc = {desc: value}` — if two items share the same `desc` string, the later overwrites the earlier (silent data loss). Uniqueness is plausible but NOT enforced.
**User Impact:** Leaked shutdown workers can stall process exit via the `concurrent.futures.thread` atexit join. Duplicate-desc silent data loss in parallel teardown produces inconsistent state.
**Root Cause:** No worker tracking; no uniqueness assertion on `desc`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_timeout_utils.py`
**Fix:** Track leaked workers in a registry joined before `os._exit(0)` in the watchdog path. Add `assert len(set(descs)) == len(items)` at entry to `_run_parallel_with_timeout`. Add `__all__` cleanup (remove `_TIMEOUT`/`_DE11_GRACE_PERIOD_SECONDS` aliases).
**Severity:** 🟡 Medium

---

### UE-22 — `MicrophoneDeviceWatcher.start()` idempotency guard is lock-free → double-spawn race
**Status:** ❌ Not Fixed
**Description:** `start()` does `if self._thread is not None or self._coreaudio_watcher is not None: return` with no lock. If two callers race (e.g. `RecordingController.setup()` and a config-reload path), both can pass the guard and spawn two polling threads (Linux/Windows) or two `CoreAudioMicrophoneWatcher` instances (macOS). `_stop_event` is shared, so only the most-recently-started thread sees it; the orphan spins forever as a daemon. On macOS, two CTCoreAudio listeners register on `kAudioHardwarePropertyDevices` (double-firing).
**User Impact:** Duplicate device-cache invalidation callbacks, orphaned thread that can't be stopped via `stop()`. On macOS: potential use-after-free in pyobjc's listener-proc wrapper under rapid start/stop.
**Root Cause:** No lock on the start/stop lifecycle.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py`
- `voice_typer/server/microphone_watcher_coreaudio.py`
**Fix:** Add `self._lock = threading.Lock()` and guard the start/stop lifecycle (mirror `VolumeDucker`'s pattern). Capture `thread`/`run_loop` atomically in the CoreAudio path.
**Severity:** 🟡 Medium

---

### UE-23 — `VolumeDucker._stop_smart_duck_monitor()` runs without `self._lock` → premature-stop race
**Status:** ❌ Not Fixed
**Description:** `restore()` calls `_stop_smart_duck_monitor()` BEFORE acquiring `self._lock`. Meanwhile `duck()` holds `self._lock` and calls `_start_smart_duck_monitor()` inside the lock. Sequence: Thread A (restore) reads `_monitor_thread`, sets `_monitor_stop`, sets `_monitor_thread = None` — all without the lock. Thread B (concurrent duck) acquires lock, reads `_monitor_thread` (now None), spawns fresh monitor T2. T2 sees `_monitor_stop` is still set (A's `.set()` was never cleared), exits immediately. The user gets no retroactive-duck protection for the new dictation.
**User Impact:** Smart-duck monitor silently disabled for the second of two back-to-back dictations. Audio bleed-into-mic possible.
**Root Cause:** `_stop_smart_duck_monitor` called outside the lock in `restore()`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/volume_ducker.py`
**Fix:** Call `_stop_smart_duck_monitor()` from inside `self._lock` in `restore()`. Also drop the lock during `backend.fade_to()` (up to 150ms blocking) in `duck()` to reduce ESC-cancel latency — re-acquire for post-fade state writes.
**Severity:** 🟡 Medium

---

### UE-24 — Level worker swallows all exceptions at DEBUG → silent level-bar freeze
**Status:** ❌ Not Fixed
**Description:** `level_monitor/worker._level_worker_loop` catches `except Exception: log.debug(...)`. If `_level_processor.process_chunk` starts raising on every chunk (RNNoise model corrupted, numpy mismatch, filter misconfiguration), the worker silently drops every chunk at DEBUG — no WARNING, no operator-visible signal. `_dropped_level_chunks` does NOT increment (chunks are popped then error in processing) — existing telemetry misses this failure mode entirely.
**User Impact:** Silent level-bar freeze with no actionable error. Frontend shows stale last-computed value; no diagnostic at default log levels.
**Root Cause:** Overly-broad exception suppression at too-low a log level with no error counter.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/level_monitor/worker.py`
**Fix:** Add a per-burst error counter + throttled WARNING (mirror the `_dropped_level_chunks` 5-second throttle pattern); escalate to ERROR if the rate exceeds N/sec.
**Severity:** 🟡 Medium

---

### UE-25 — Volume backends swallow errors → silent no-op on failure (observability gap)
**Status:** ❌ Not Fixed
**Description:** `WinVolumeBackend.is_speaker_active` catches `Exception` and returns `True` at DEBUG — a stuck/revoked COM pointer makes smart-duck silently always-duck. `WinVolumeBackend.get_state` returns `None` on any exception — `VolumeDucker.duck()` logs "get_state failed — not ducking" at WARNING but user sees no duck and no notification. `LinuxVolumeBackend._alsa_is_playing` returns `True` ("safe default — duck anyway") on any error. `MacVolumeBackend._osascript_run` returns `None` at DEBUG; `_osascript_get_state` returns `None` → duck skipped silently.
**User Impact:** Volume ducking degrades to a no-op silently when the backend breaks. Crash-recovery file is never written → no recovery on next launch. User reports "dictation doesn't lower my music" with no log breadcrumb.
**Root Cause:** Backends swallow errors and return safe defaults with no error tracking.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/volume_backends/windows.py`
- `voice_typer/server/volume_backends/linux.py`
- `voice_typer/server/volume_backends/macos.py`
**Fix:** Add a per-backend consecutive-error counter; surface as a tray notification + WARNING log after N failures (mirror `level_monitor`'s throttled drop-counter pattern).
**Severity:** 🟡 Medium

---

### UE-26 — Protocol-version negotiation exists only on WS path; never bumped despite registry churn
**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:208` defines `PROTOCOL_VERSION: int = 1` and emits it only on the WS sidecar path. The TCP path and stdin path emit NO protocol version. The docstring says "Bump this integer whenever the `_COMMAND_REGISTRY` adds/removes/renames a command OR the push-event `type` vocabulary changes" — but the registry has had ≥15 command removals and the renderer allowlist has been pruned twice, yet `PROTOCOL_VERSION` is still `1`. No test asserts the version is monotonic w.r.t. registry mutations.
**User Impact:** An old client on TCP or stdin connects, passes auth, then silently gets `unknown_command` errors for every removed command — no early `protocol_mismatch` signal. Host cannot tell "stale client" from "server bug."
**Root Cause:** Documented bump-on-change contract is unenforced; version not emitted on all transports.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
- `voice_typer/server/ipc/transport_tcp.py`
- `voice_typer/server/ipc_server.py`
**Fix:** Extend `PROTOCOL_VERSION` emission to TCP and stdin paths. Add a contract test asserting the version bumps on registry diff. Bump to `2` to reflect cumulative registry churn.
**Severity:** 🟡 Medium

---

### UE-27 — Dead `open_host_logs` Rust command + dead `deleteElectronPersonalDataLogs` forward-declaration
**Status:** ❌ Not Fixed
**Description:** `open_host_logs` Tauri command (`system_cmds.rs:243` + registration in `main.rs:266`) has ZERO `invoke` callers — the renderer's "View Logs" button calls `window.window_?.openLogs?.()` → `open_logs` (opens config root, NOT logs/ subdir). `deleteElectronPersonalDataLogs` (`structuredLogger.ts:270` + barrel re-export) is a forward-declaration for an IPC handler that was never built; ZERO production callers.
**User Impact:** 23 LOC of dead `#[tauri::command]` code + registration slot wasted. "View Logs" UX still dumps the user at the config root rather than the logs/ subdir. Untested GDPR helper risks bit-rot.
**Root Cause:** GT-83 intended fix never reached the renderer; PI-6 IPC handler never built.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/system_cmds.rs`
- `src-tauri/src/main.rs`
- `voice_typer/client/src/main/logging/structuredLogger.ts`
- `voice_typer/client/src/main/logging/index.ts`
**Fix:** Delete `open_host_logs` command + registration. Delete `deleteElectronPersonalDataLogs` + barrel re-export. (If the "View Logs" UX should open logs/, wire `openLogs` to a new `open_logs_dir` command — but that's a separate enhancement; this finding is just dead-code removal.)
**Severity:** 🟢 Low

---

### UE-28 — `state.rs::kill_process_tree` deprecated shim still used by 4 spawn.rs callers
**Status:** ❌ Not Fixed
**Description:** `state.rs:172-182` documents `kill_process_tree` as "Deprecated: use `crate::platform::process::kill_process_tree` directly" but is NOT annotated `#[deprecated]`. 4 active callers in `spawn.rs:344,376,425,598` still route through the shim.
**User Impact:** Two paths to the same logic invite divergence; the shim adds a hop on a shutdown hot-path.
**Root Cause:** Migration never completed.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/sidecar/spawn.rs`
**Fix:** Migrate the 4 `spawn.rs` callers to `crate::platform::process::kill_process_tree(pid)` directly. Delete the `state::kill_process_tree` shim.
**Severity:** 🟢 Low

---

### UE-29 — `sidecar_ws.py` `_handle_connection_inner` is a 375-line monolith (spaghetti)
**Status:** ❌ Not Fixed
**Description:** `_handle_connection_inner` (lines 618-992) is a single ~375-line async function doing: duplicate-auth check, ready-event emission, state_changed snapshot, event_bus subscriber registration, outbound-queue setup, writer-task creation, read loop (JSON parse, type check, dispatch, response send), three exception handlers, and a 6-step `finally` cleanup. 5 distinct concerns mashed together; comment-to-code ratio ~2:1. Hard to test in isolation; hard to reason about exception flow.
**User Impact:** High cognitive load for reviewers; the heartbeat race (UE-7) and cleanup-drain gap (UE-8) are partly consequences of the reader/heartbeat/auth logic being visually adjacent rather than modularly separated.
**Root Cause:** RW-style decomposition extracted helpers but left the orchestrator as a god-function.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/sidecar_ws.py`
**Fix:** Extract into named helpers: `_check_duplicate_auth`, `_emit_ready_if_first`, `_install_subscriber`, `_start_writer`, `_read_loop`. The orchestrator becomes a ~30-line coordinator. This also makes UE-7/UE-8 easier to fix in isolation. (Coordinate with the ws.rs split — UE-30 — to keep Python and Rust sides consistent.)
**Severity:** 🟡 Medium

---

### UE-30 — `ws.rs` 1454-line monolith mixes 8+ concerns (spaghetti)
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/sidecar/ws.rs` co-locates 8+ concerns: event-type allowlist + HashSet cache, supervisor thread management + `OnceLock<mpsc::Sender>`, auth-time cleanup, WS connect with timeout, WS writer channel setup, auth handshake with catch_unwind, WS reader task with dispatch fulfillment + bubble coalescing + event translation, heartbeat task with miss tracking, event-name translation table, + ~220 lines of tests. Comment-to-code ratio ~50%.
**User Impact:** Hard to navigate, hard to test in isolation, high cognitive load. The heartbeat race (UE-7) and cleanup-drain gap (UE-8) are partly consequences of the monolithic structure.
**Root Cause:** XZ-11 extraction claimed to split the "585-line god function" but the FILE itself stayed 1454 lines.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Split into `sidecar/ws/{mod,allowlist,supervisor_trigger,connect,auth,writer,reader,heartbeat,translate}.rs`. `mod.rs` becomes the `reconnect_ws` orchestrator (~80 lines) + re-exports. No behavior change — same public APIs, same command names, same tests passing.
**Severity:** 🟡 Medium

---

### UE-31 — `logging.rs` 2161-line monolith mixing 6 concerns (spaghetti) — GROUP 5 mandatory
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/platform/logging.rs` conflates: init orchestration, `CombinedLogger` multi-sink dispatch, a 515-LOC PII redaction engine (5 `try_match_*` state machines), `install_panic_hook`, `EarlyLogger` + `EARLY_LOGGER_HANDLE`, `RotatingFileWriter`, + ~920 LOC tests (42% of the file). The redaction sub-concern alone is larger than most files in `src-tauri/src/`.
**User Impact:** Hard to audit PII redaction correctness (UE-6 fix requires editing the 515-LOC sub-concern in isolation). Comment sediment accretes per session.
**Root Cause:** Never decomposed after initial extraction.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging.rs`
**Fix:** Split into `platform/logging/{mod,init,combined,redact,panic_hook,early,rotating}.rs`. `mod.rs` re-exports the public API. Co-located `tests/` per sub-module. This is a prerequisite for safely expanding the redaction engine (UE-6).
**Severity:** 🟡 Medium

---

### UE-32 — `ipc_server.py` 2112-line monolith + registry-as-changelog antipattern (spaghetti)
**Status:** ❌ Not Fixed
**Description:** `ipc_server.py` is 2112 lines mixing 8+ concerns: lifecycle, transport, push-event registration, tray hook, command registry literal (190 LOC interleaved with ~30 "X was REMOVED" comments), dispatcher, builtin handlers, rate-limiter re-export shim, process metadata, argparse, `main()`. The 190-line registry literal is a registry-as-changelog antipattern.
**User Impact:** A maintainer scanning the file cannot locate "where is command X dispatched?" without scrolling past 600 lines of comments. High comment density masks live code changes during review.
**Root Cause:** 17-mixin decomposition extracted handlers but `IPCServer` itself + registry literal remained.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc_server.py`
**Fix:** Extract `_COMMAND_REGISTRY` + `_READONLY_COMMANDS` + `_PYTHON_ONLY_COMMANDS` to `ipc/registry.py` (module-level dict). Move the 30 "REMOVED" comments to `CHANGELOG.md` or the existing `tests/test_dead_code_stays_removed.py` regression guard. Leave the `IPCServer` class body for a future deeper split (the mixins ARE extracted). Coordinate with UE-13/UE-18 (same file).
**Severity:** 🟡 Medium

---

### UE-33 — `config.py` Config dataclass 2078-LOC residual monolith (spaghetti)
**Status:** ❌ Not Fixed
**Description:** Despite the `config_internals/` split (811 LOC) and `config_validators.py` extraction (1735 LOC), the `Config` dataclass itself spans ~2078 LOC mixing 7 concerns: field declarations + defaults, `__post_init__` + mutation-lock plumbing, save/atomic-write/credential-routing, load orchestrator + `_filter_unknown_keys`, migration backups + downgrade, field coercion (6 `_coerce_*`/`_validate_*_path` helpers), non-numeric field sanitization (`_warn_and_reset`/`_warn_and_coerce`/`_validate_non_numeric_fields` — a single 340-LOC method).
**User Impact:** 2555-LOC file is hard to navigate; the 340-LOC `_validate_non_numeric_fields` is a maintenance hazard.
**Root Cause:** Decomposition extracted free-function helpers but the dataclass methods remained.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config.py`
**Fix:** Extract `config/coercion.py` (the 6 `_coerce_*`/`_validate_*_path` helpers — pure dict transforms) and `config/sanitization.py` (`_warn_and_reset`/`_warn_and_coerce`/`_validate_non_numeric_fields`/`_derive_field_type_registry`). `Config` shrinks to ~600 LOC. No behavior change — same public API, same tests passing.
**Severity:** 🟡 Medium

---

### UE-34 — `history_db.py` HistoryDB 2100-LOC class mixing 8 concerns (spaghetti) + writer-death silent data loss
**Status:** ❌ Not Fixed
**Description:** Despite `history_db_internals/` extraction (862 LOC), `HistoryDB` class body holds ~1400 LOC of un-extracted logic: writer-thread queue + lifecycle (~750 LOC), corruption recovery (~500 LOC), read-connection pool (~300 LOC), CRUD (~250 LOC), reads (~400 LOC). Separately (reliability): `_submit_write` guards on `_shutdown.is_set()` but NOT on `_init_error is not None`. If schema init fails, the writer thread exits but `_shutdown` is never set. `add_transcription` (fire-and-forget, `wait=False`) calls `_submit_write` → passes the guard → `put_nowait` succeeds → returns -1 immediately. The queued item never executes (writer dead). Silent data loss for the entire session.
**User Impact:** Monolith: hard to navigate, hard to test writer/recovery/pool in isolation. Writer-death: if schema init fails (corrupt DB, disk full), every fire-and-forget `add_transcription` for the rest of the session silently drops the transcription. User dictates all day; nothing is saved; no error shown.
**Root Cause:** Monolith: decomposition stopped at free functions. Writer-death: missing guard on `_init_error`.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db.py`
**Fix:** (1) Reliability: add `if self._init_error is not None: return None` at the top of `_submit_write`; have `add_transcription` check `health_check()`-style and log at ERROR + return -1 with a clear "writer unavailable" signal. (2) Spaghetti (defer if time-boxed): extract `history_db/writer.py`, `history_db/read_pool.py`, `history_db/recovery.py` following the existing `_internals/` pattern.
**Severity:** 🟡 Medium (reliability portion is High-impact)

---

### UE-35 — Disconnect restart writes `_device_disconnected` outside lock → BT-flap race masks real disconnect
**Status:** ❌ Not Fixed
**Description:** `disconnect_handler.restart_stream` writes `recorder._actual_channels`, `recorder._device_disconnected = False`, `recorder._device_disconnect_retries = 0` (lines 206-212) WITHOUT any lock after releasing `recorder._lock`. A concurrent `_device_health_checker_loop` (device_manager.py:420) reading/writing `_device_disconnected` (line 423/456) can race: the checker may set `_device_disconnected = True` the instant before `restart_stream` sets it False, masking the new disconnect.
**User Impact:** BT mic flapping (disconnect/reconnect/disconnect within ~1s) can leave the recorder in a state where the second disconnect is not detected because the first restart's `_device_disconnected = False` write won the race. User's BT mic silently stops capturing.
**Root Cause:** Three state writes outside the lock that should be inside.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/disconnect_handler.py`
**Fix:** Move the three assignments (`_actual_channels`, `_device_disconnected`, `_device_disconnect_retries`) inside the `recorder._lock` block. Also narrow the broad `except Exception` in `restart_stream` to portaudio-specific exceptions (`sd.PortAudioError`, `OSError`); re-raise `AttributeError`/`TypeError`/`KeyError` so programming bugs don't mask as "transient device failure."
**Severity:** 🟡 Medium

---

### UE-36 — Hot-swap does not flush or tag stale-rate buffer contents → pitch/speed artifacts
**Status:** ❌ Not Fixed
**Description:** On hot-swap, `restart_stream` opens a new stream and resets `recorder._buffer_sr = None` but does NOT flush or boundary-tag the existing `_buffer` contents. Pre-disconnect chunks are at the OLD `_buffer_sr`; post-disconnect chunks at the NEW rate. When `stop()` later concatenates and calls `_prepare_audio(audio, _buffer_sr)`, the NEW rate is used as the source rate for the entire buffer — but early chunks were captured at the old rate.
**User Impact:** When no AudioProcessor is active, a hot-swap between devices of different native rates (44.1 kHz → 48 kHz, or BT HFP 8 kHz → USB 16 kHz) produces a rate-inconsistent buffer resampled at the wrong source rate → pitch/speed artifacts on the pre-disconnect portion.
**Root Cause:** No buffer flush or boundary marker on hot-swap.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/disconnect_handler.py`
**Fix:** Flush `_buffer` on hot-swap restart (losing pre-disconnect audio, simplest), OR insert a boundary marker so `stop()` can split-and-resample the two halves at their respective rates. Coordinate with UE-35 (same file).
**Severity:** 🟡 Medium

---

### UE-37 — `service/_base.py` declares 11 attributes as `Any` → 3 downstream pyrefly baseline errors
**Status:** ❌ Not Fixed
**Description:** `ServiceMixinBase` declares 11 attributes as `Any` (`_app`, `_config_applier`, `_download_cancel_lock`, `_active_download_id`, `_microphones_cache`, `_model_status_cache`, …). The docstring acknowledges the alternative (`Protocol`) but argues for `Any` to keep mixins decoupled. This `Any` scaffold is the root cause of 3 downstream pyrefly baseline errors at `service/__init__.py:199/214/220` (`bad-override-mutable-attribute`) and `service/model.py:69, 178` — `VoiceTyperService` re-declares the attrs with narrowed types that conflict with the `Any` in the base.
**User Impact:** Type-checker is blind to attribute-shape mismatches between the mixin base and the concrete service — real bugs hide behind the `Any`.
**Root Cause:** `Any` chosen for decoupling; downstream narrowing conflicts.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/service/_base.py`
- `voice_typer/server/service/__init__.py`
- `voice_typer/server/service/model.py`
**Fix:** Replace the 11 `Any` attrs with `TYPE_CHECKING`-only `Protocol` or `ClassVar[...]` annotations matching the runtime types declared on the concrete service. Drop the 3 corresponding pyrefly baseline entries. Also fix the 2 `# type: ignore[return-value]` in `service/model.py:844, 1118` (UE-13-10) hiding real shape mismatches in the HuggingFace consent gate.
**Severity:** 🟡 Medium

---

### UE-38 — `secure_file_io.PersistedJson` is `Any`-everywhere; should be `Generic[T]`
**Status:** ❌ Not Fixed
**Description:** `PersistedJSON` is a generic-shaped class with `Any` everywhere (`default: Any`, `load() -> Any`, `save(data: Any)`). Callers (`VocabularyManager`, `TemplateManager`) get zero type-checking on the round-trip.
**User Impact:** Type-checker cannot catch a `VocabularyManager` that saves the wrong shape — silent corruption.
**Root Cause:** Class never parameterized.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/secure_file_io.py`
**Fix:** Make `PersistedJSON` `Generic[T]` with `default: T`, `load() -> T`, `save(data: T) -> None`. Update the 2 callers to parameterize. Mechanical refactor with big type-safety payoff.
**Severity:** 🟡 Medium

---

### UE-39 — Preload `python.call` signature wider than bridge contract; `i18n:set-locale` accepts both string and `{locale}`
**Status:** ❌ Not Fixed
**Description:** Preload `call: (msg: Record<string, unknown>) => ipcRenderer.invoke(...)` is wider than the bridge type `call: (msg: { type: string; data?: Record<string, unknown> }) => Promise<unknown>`. The runtime `python-call-handler.ts:71` coerces with `String((msg as { type: unknown }).type)`, masking malformed shapes as `"<unknown>"`. Separately, `i18n:set-locale` handler branches on `typeof payload === "string"` vs object — but the preload always sends a bare string, so the object branch is dead code. Separately, `KNOWN_EVENT_TYPES` (33-entry Set hand-mirrored against `PythonPushEvent` union) has no parity test — only a dev-time `console.warn`.
**User Impact:** A renderer bug that constructs `call({})` compiles against the preload signature, ships, and only fails at the backend with `Disallowed IPC command: <unknown>` — opaque. Adding event #34 to the union without updating the set silently disables the typo-warning for the new event.
**Root Cause:** Preload signature widened for flexibility; parity test never added.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/preload/index.ts`
- `voice_typer/client/src/main/ipc/python-call-handler.ts`
- `voice_typer/client/src/main/ipc/window-handlers.ts`
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`
**Fix:** Tighten the preload's `call` parameter to `{ type: string; data?: Record<string, unknown> }`. Add a runtime `typeof msg?.type !== "string"` reject in `python-call-handler.ts`. Delete the dead `{ locale }` branch in `window-handlers.ts`. Add a parity test that asserts `KNOWN_EVENT_TYPES` equals a TS-derived set (via `satisfies Record<PythonPushEvent["type"], true>`).
**Severity:** 🟡 Medium

---

### UE-40 — `_SECRET_CONFIG_FIELDS` exists as TWO divergent frozensets (dead code masking real bug)
**Status:** ❌ Not Fixed
**Description:** `_SECRET_CONFIG_FIELDS` exists in `config_sanitizer.py:101` (canonical, structurally derived from `credential_store.PROVIDER_TO_CONFIG_FIELD`) AND `ipc/history_bounds.py:50` (hand-listed 5-field literal). The `config_sanitizer.py` docstring falsely claims "the two names refer to the SAME frozenset object — alias, not a copy" — but `ipc/history_bounds.py:50` constructs an independent frozenset. If a 6th provider is added to `credential_store.PROVIDER_TO_CONFIG_FIELD`, the canonical set grows but the hand-listed set does NOT. The hand-listed set is used by `ipc/history_bounds._sanitize_config_for_ipc` for IPC-server redaction — a new provider's API key could leak via IPC. Test `test_ipc_de33_to_de36.py` pins the 5-field set, cementing the drift.
**User Impact:** Adding a new cloud provider (e.g. a 6th ASR backend with an API key) silently leaves its key unredacted in IPC traffic.
**Root Cause:** Copy-paste duplication; docstring lie掩盖s it.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/history_bounds.py`
- `voice_typer/server/config_sanitizer.py`
**Fix:** Make `ipc/history_bounds._SECRET_CONFIG_FIELDS` an alias import from `config_sanitizer._SECRET_CONFIG_FIELDS`. Relax `test_ipc_de33_to_de36.py` 5-field pinning test to assert "contains at least the 5 known fields" rather than "exactly 5."
**Severity:** 🟡 Medium

---

### UE-41 — `vocabulary_automation.auto_apply_high_confidence_suggestions` check-and-apply not atomic → duplicate entries
**Status:** ❌ Not Fixed
**Description:** `auto_apply_high_confidence_suggestions` takes a snapshot under the lock, then calls `self.apply_suggestion(suggestion)` per item OUTSIDE the lock. `apply_suggestion` re-checks `applied/dismissed` (line 547) so a single call is safe, but two concurrent `auto_apply` calls can both pass the snapshot's `if suggestion.applied: continue` check, then both call `apply_suggestion`, and the second `_vm.add_entry("misspellings", ...)` adds a duplicate. Also `apply_suggestion` mutates `suggestion.applied = True` OUTSIDE the lock.
**User Impact:** Duplicate vocabulary entries under concurrent auto-apply (rare but possible if two transcriptions finish simultaneously).
**Root Cause:** Snapshot-then-act pattern without holding the lock across the check-and-apply.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/vocabulary_automation.py`
**Fix:** Hold the lock across the check-and-apply in `auto_apply_high_confidence_suggestions`; mutate `applied`/`dismissed` under the lock.
**Severity:** 🟢 Low

---

### UE-42 — Stale `as never` casts + duplicated `__TAURI__` casts + `csvEscape` divergence (TS/Rust parity)
**Status:** ❌ Not Fixed
**Description:** (a) `python-namespace.ts:111,178` uses `code: "internal_error" as never` — `ErrorCodes` already includes `"internal_error"`; the cast is stale and lies to the type system. (b) `detect.ts:79,89` + `usePython.ts:257` each re-declare `window as unknown as { __TAURI__?: ... }` — 3 duplicated casts; the `__TAURI__` global augmentation is NOT in `bubble_bridge.ts`'s `declare global` block. (c) `export-handlers.ts:70` (TS `csvEscape`) ↔ `export.rs:222` (Rust `csv_escape`) diverge: TS always wraps in double-quotes; Rust only wraps when the cell contains special chars. The Rust docstring says "Mirrors the Electron-side `csvEscape`" but the line reference is stale and the claim is wrong.
**User Impact:** (a) Future `ErrorCodes` narrowing silently defeated. (b) Three sites need updating if `__TAURI__` shape evolves. (c) Same CSV exported via Electron vs Tauri produces different byte output.
**Root Cause:** Stale casts never cleaned up; CSV parity never enforced.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/detect.ts`
- `voice_typer/client/src/renderer/src/types/ipc/bubble_bridge.ts`
- `voice_typer/client/src/main/ipc/export-handlers.ts`
- `src-tauri/src/commands/export.rs`
**Fix:** (a) Replace `as never` with bare `"internal_error"` literal. (b) Add `__TAURI__?: TauriGlobal` to the `declare global` block in `bubble_bridge.ts`; remove the 3 casts. (c) Unify on RFC-4180 (Rust's strategy — quote only when needed) in both, OR delete the TS-side `csvEscape` if the Electron export path is being phased out. Update the stale cross-reference comment.
**Severity:** 🟢 Low

---

### UE-43 — `migrate.rs` 1203-LOC monolith + stale `atomic_write_bytes` re-export + over-exposed `pub`
**Status:** ❌ Not Fixed
**Description:** `migrate.rs` (1203 LOC = 838 prod + 365 tests) mixes 6+ concerns: per-platform path resolution, migration orchestration, sentinel idempotency marker, JSON merge, atomic copy (small-file vs streaming), SQLite WAL/SHM path builder, backup, recursive copy. The `atomic_copy`/`atomic_copy_file` helpers stayed here instead of being promoted to `util.rs` alongside `atomic_write_bytes` (PVT-G5-033). Separately: `pub(crate) use crate::util::atomic_write_bytes;` re-export at line 562 is stale (supervisor.rs already imports from `crate::util` directly). Separately: `pub fn migrate_electron_userdata`, `pub fn create_tray`, `pub const APP_NAME` are over-exposed (`pub` on a binary crate).
**User Impact:** 1203-LOC file hard to navigate; dead re-export invites divergence; `pub` on a binary crate suggests an external API that doesn't exist.
**Root Cause:** Never decomposed; re-export and visibility never cleaned up after migrations.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/migrate.rs`
- `src-tauri/src/tray.rs`
- `src-tauri/src/branding.rs`
**Fix:** (1) Delete the stale `atomic_write_bytes` re-export at migrate.rs:562. (2) Demote `pub fn migrate_electron_userdata`/`pub fn create_tray`/`pub const APP_NAME` to `pub(crate)`. (3) Defer the full `migrate/{mod,candidates,merge,copy,sentinel}.rs` split if time-boxed — the dead-re-export and visibility fixes are the high-value low-risk part.
**Severity:** 🟢 Low

---

### UE-44 — Rust saturating-cast pattern not applied in 3 remaining `as` sites + `redact_pii` unwrap
**Status:** ❌ Not Fixed
**Description:** The project adopted `try_from().unwrap_or(MAX)` saturating-cast pattern (PVT-G5-051, GT-D3-7) but 3 sites still use raw `as`: `commands/bubble.rs:130-136` (`screen_w = screen_size.width as i32` — u32→i32 truncation on >2^31-px displays), `commands/bubble.rs:697` (`d.as_nanos() as u64` — u128→u64 truncation after ~584 years), `util.rs:209` (`i64→u64` cast in `now_timestamp`). Separately, `platform/logging.rs:494` uses `rest.chars().next().unwrap()` in the production `redact_pii` (called from the panic hook) — safe by loop invariant but brittle to a future refactor that changes the loop bound; a logger panic is self-reinforcing.
**User Impact:** (a) On >2^31-px virtual displays (8K surround, large HiDPI canvases), the `as i32` cast silently wraps to negative coordinates, moving the bubble off-screen. (b) The `unwrap` in `redact_pii` is a latent panic-in-logger risk.
**Root Cause:** Saturating-cast migration was incomplete; the unwrap predates the no-panic-in-logger discipline.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/util.rs`
- `src-tauri/src/platform/logging.rs`
**Fix:** Replace the 3 `as` casts with `i32::try_from(...).unwrap_or(i32::MAX)` / `u64::try_from(...).unwrap_or(u64::MAX)`. Replace `rest.chars().next().unwrap()` with `if let Some(ch) = rest.chars().next() { ... }`. Coordinate with UE-6/UE-31 (logging.rs is shared).
**Severity:** 🟢 Low

---

### UE-45 — `single_instance.ts` uses `console.warn` for 9+ diagnostic paths (observability debt)
**Status:** ❌ Not Fixed
**Description:** `single_instance.ts` never imports the `log` logger. Every diagnostic is `console.warn("[single_instance] …")` (9+ sites: lines 44, 81, 92, 170, 210, 233, 239, 249, 259). The codebase's own rationale (`bootstrap.ts:124`, `index.ts:44`) states `console.warn` is "lost in packaged builds where `console.warn` has no terminal attached." `bootstrap.ts` itself was migrated to `log.warn` (DE-87 / S2-CR-75) but `single_instance.ts` was missed.
**User Impact:** Stale-lock recovery warnings, legacy-dir probe failures, PID-file write failures, and single-instance-release failures are all silently dropped in packaged builds — operators triaging "second instance exits immediately" or "stale PID not cleared" have no file-log trail.
**Root Cause:** DE-87 migration missed this file.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/main/single_instance.ts`
**Fix:** `import { log } from "./logging"`, replace `console.warn` → `log.warn`. Update `single-instance.test.ts` assertions that check `console.warn` calls.
**Severity:** 🟡 Medium

---

### UE-46 — `_LONG_RUNNING_COMMANDS` underscore prefix misleading + stale "TODO" tokens in resolved PVT-25 doc-comments
**Status:** ❌ Not Fixed
**Description:** (a) `commands/sidecar_cmds.rs:49` `const _LONG_RUNNING_COMMANDS` — leading underscore misleadingly suggests unused, but IS consumed at line 116. (b) `commands/bubble.rs:642` + `commands/sidecar_cmds.rs:398` contain stale doc-comments still referring to "the PVT-25 TODO that called for [this extraction]" — the TODO has been resolved; the literal token `TODO` trips `rg TODO` and misleads readers. (c) `state.rs:218-224` has a stale "GT-FIX-20 coordination note" about `heartbeat_handle` field addition — `main.rs:243` already uses `SidecarState::new()`. (d) `state.rs:13-21` (`state.ts`) has a stale `preMaximizeBounds` docstring — the test fixtures were already cleaned up.
**User Impact:** Misleading documentation; reviewers waste time hunting for fixtures/TODOs that no longer exist.
**Root Cause:** Cleanup never completed after migrations.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/state.rs`
- `voice_typer/client/src/main/state.ts`
**Fix:** (a) Rename `_LONG_RUNNING_COMMANDS` → `LONG_RUNNING_COMMANDS`. (b) Rephrase the PVT-25 comments to "the PVT-25 extraction" (drop "TODO"). (c) Delete the GT-FIX-20 coordination note. (d) Trim the `preMaximizeBounds` paragraph to a one-line historical note.
**Severity:** 🟢 Low

---

### UE-47 — Empty ASR output treated as "no speech" → masks misconfiguration/unloaded-backend (observability)
**Status:** ❌ Not Fixed
**Description:** `dictation_stages.EmptyCheckStage` treats empty `""` as "no speech" via `_handle_empty_transcription`. But empty output has three distinct causes: (1) genuine silence, (2) `asr_registry.get_active()` returned an UNLOADED backend — `transcribe_with_fallback` on an unloaded Whisper/Parakeet/Qwen typically returns `""` without raising, (3) cloud provider returned 200 with empty body (consent-revoked tokens). The pipeline never raises — always returns to IDLE. The user sees the same "No speech detected" toast regardless of cause. Separately, `_handle_empty_transcription` silently suppresses ALL user feedback for short (<15s) near-silent recordings — the user sees nothing.
**User Impact:** A user whose model failed to load sees "No speech detected" — same as a user who said nothing. No diagnostic difference except one WARNING log line. The user cannot tell "my mic is broken" from "the model didn't load" from "I was silent."
**Root Cause:** Three failure modes collapsed into one silent path.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/server/dictation_stages.py`
**Fix:** In `_transcribe`, capture `active.is_loaded` before the call and include it in the empty-warning; if `is_loaded is False`, raise a distinct `BackendNotLoadedError` so the generic `except Exception` block in `run()` surfaces a friendly "model not loaded" message instead of falling through to `_handle_empty_transcription`. Publish a `dictation_suppressed` event for the <15s silent path so the renderer can show a subtle inline bubble. (Coordinate with UE-10 — same file.)
**Severity:** 🟡 Medium

---

### UE-48 — Stuck ctranslate2 holds GPU; next dictation piles up (reliability)
**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline._transcribe` calls `active.transcribe_with_fallback(...)` with no cancellation token, no timeout, no abort hook. The watchdog (`recording_controller._force_recover_from_stuck_transcription`) only force-recovers app state (`_busy_event.set()`, tray to IDLE, `_cancelled_cycle_ids.add`, `_current_audio = None`) — it cannot interrupt the C-level ctranslate2 call. The Python thread continues running, holding the GIL intermittently and the ctranslate2 model/GPU memory until the call returns (5-30 min documented). If the user starts a new dictation, `_start_dictation` calls `ensure_active_engine_loaded` → the same backend object stuck in the previous cycle's C call. The new transcription queues behind the stuck one (GIL/ctranslate2 internal lock) or attempts concurrent inference on the same model — undefined behavior.
**User Impact:** After a stuck transcription (rare but documented), every subsequent dictation piles up behind the stuck call. The app appears frozen; force-quit is the only recovery.
**Root Cause:** No per-backend "busy" flag; no unload-on-stuck escalation.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/server/recording_controller.py`
**Fix:** The registry should expose a per-backend "busy" flag (set when `transcribe_with_fallback` is entered, cleared on exit); `ensure_active_engine_loaded`/`_start_dictation` should reject or queue the new request when the active backend is busy. As defense-in-depth, the watchdog's force-recover path should call `backend.unload()` after a 2nd force-recovery to actually tear down the stuck model. (Coordinate with UE-11 — model_manager.py is shared.)
**Severity:** 🟡 Medium

---

### UE-49 — Dead `server_platform/platform_flags.py` shim + dead `HotkeyDispatcher` property accessors + dead `vad.reset()`
**Status:** ❌ Not Fixed
**Description:** (a) `server_platform/platform_flags.py` (40 LOC) re-exports `is_windows`/`is_macos`/`is_linux` from `platform_utils` — ZERO consumers in production or tests. (b) `HotkeyDispatcher.hotkey_backend`/`esc_backend`/`repaste_backend` @property accessors (hotkey_dispatcher.py:65-75) — ZERO readers; tests directly write the private `_hotkey_backend`. (c) `vad.reset()` (vad.py:449-458) — only test callers; production uses `vad.reset_states` and `vad.unload`. (d) `platform_utils.platform_name()` (line 55) — only test callers.
**User Impact:** Dead code inflating review surface; misleading "back-compat" comments.
**Root Cause:** Soft-deprecation shims and test-only helpers never removed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/server_platform/platform_flags.py` (DELETE)
- `voice_typer/server/server_platform/__init__.py` (remove re-exports)
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/vad.py`
- `voice_typer/server/platform_utils.py`
**Fix:** Delete `platform_flags.py` + corresponding `__init__.py` re-exports. Delete the 3 `@property` accessors on `HotkeyDispatcher`. Delete `vad.reset()`. Delete `platform_utils.platform_name()` if confirmed unused. Update/remove corresponding dead tests. Record deletions in `archive/deleted_files.txt`.
**Severity:** 🟢 Low

---

### UE-50 — `legacy_code` field emitted in every IPC error response, never consumed by frontend
**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:415,454,499,517,539` + `ipc/transport_tcp.py:506,548` emit a `legacy_code` field in every error response. Comment at validation.py:409: "Drop `legacy_code` once the renderer migrates." Verified `rg "legacy_code" voice_typer/client/src/` → ZERO frontend references. The renderer has migrated to the namespaced `code` form. The `legacy_code` field is stale tech-debt adding bytes to every error response and ~15 lines of duplication.
**User Impact:** Minor bandwidth/perf; maintenance burden.
**Root Cause:** Migration completed but the legacy field never removed.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/ipc/validation.py`
- `voice_typer/server/ipc/transport_tcp.py`
**Fix:** Remove the `legacy_code` field from all emission sites (~10 sites). Update any tests that assert on `legacy_code`.
**Severity:** 🟢 Low

---
