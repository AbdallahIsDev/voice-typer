# Consolidated Comprehensive Review — All Sessions

## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

---

### HP-1. MIG-1.5 — Phase 0-W: Windows host validation gate

**Status:** ❌ Not Fixed — confirmed: sidecar binary is a 2-byte placeholder, never been Nuitka-frozen

**Description:** Run the 9-point ADR-0020 Windows validation gate on a real Windows machine to prove the Tauri host + Nuitka-frozen sidecar actually work before cutting over from Electron. The test scaffolding exists at `tests/tauri/mig15/` (12 test files) but has NEVER been run against a real frozen sidecar on a real Windows host.

**Confirmed findings (2026-07-24 investigation):**
- Rust host `cargo check` passes 0-error on win32 GNU target. 18+ Tauri commands registered, all compile.
- `src-tauri/bin/python-sidecar-x86_64-pc-windows-gnu.exe` = **2 bytes** (not a real Nuitka freeze).
- All 7 `src-tauri/bin/prewarm-*.*` files = **0 bytes** (empty placeholders).
- `src-tauri/resources/native/` is empty — `windows-key-listener.exe` does not exist.
- `scripts/build/nuitka_freeze.sh` is a ~1-line stub.
- `.github/workflows/tauri-windows-build.yml` exists with full CI pipeline (Nuitka freeze, prewarm build, native listener build, Authenticode signing, `cargo tauri build`, NSIS+MSI packaging) — this is the path of least resistance.
- Electron host path is intact and fully working (reversible fallback preserved).

**Progress:** 50% — Rust host compiles, Tauri commands registered, test scaffolding exists, CI workflow exists. Missing: real Nuitka freeze, real native binaries, real execution of 9-point gate.

**Fix:** Trigger `tauri-windows-build.yml` on a branch → extract CI artifacts → install on real Windows → verify 9-point gate: sidecar spawns, WS auth works, faster-whisper transcribes, enigo pastes, toast shows, shutdown clean, prewarm fires, native key listener works.

**Severity:** 🔴 Critical — blocks all downstream migration (MIG-1.6 through MIG-1.9, Phase 1–5).

---

### HP-2. MIG-1.6 — Phase 0-M: macOS validation gate (x86_64 + aarch64)

**Status:** ❌ Not Fixed — blocked on real macOS host; no macOS CI workflow exists yet

**Description:** Same as MIG-1.5 but for macOS on both Intel and Apple Silicon — prove the Tauri host + sidecar work, including notification permissions and notarization. Test scaffolding at `tests/tauri/mig16/` (10 test files). Never run on any real macOS host.

**Progress:** 0% — no real-host execution. Test files exist. No macOS CI workflow yet (unlike Windows which has `tauri-windows-build.yml`).

**Fix:** Blocked on MIG-1.5 passing first. After Phase 0-W passes: (1) create macOS CI workflow matching `tauri-windows-build.yml` structure; (2) fill macOS build scripts; (3) run on real macOS host (Intel + Apple Silicon); (4) verify 9-point gate.

**Severity:** 🔴 Critical — blocks per-platform cutover. Lower urgency than MIG-1.5 (Windows is cutover target #1).

---

### HP-3. MIG-1.7 — Phase 0-L: Linux validation gate (X11 + Wayland, incl. aarch64)

**Status:** ❌ Not Fixed — real-host X11/Wayland gate never run; tests pass in scaffold only

**Description:** Same as MIG-1.5 for Linux on X11 and Wayland (both archs). Wayland breaks `enigo` global key injection — the clipboard paste fallback must be proven. Test scaffolding at `tests/tauri/mig17/` (10 test files) verified green but real-host X11/Wayland gate NOT run.

**Current blockers:** `scripts/linux/postinst` and `scripts/linux/prerm` scripts exist at correct paths. QW-2 (Tauri v2 config key mismatch) now resolved.

**Fix:** Run Linux validation on X11 + Wayland real hosts. Test paste on both display servers. aarch64 Linux still has the `linux-key-listener` resource gap (XPLAT-11/17).

**Severity:** 🔴 Critical — blocks per-platform cutover. Lower urgency than MIG-1.5/1.6.

---

### HP-4. MIG-1.8 — Phase 1: Sidecar packaging & signing (per platform)

**Status:** ❌ Not Fixed — confirmed: all build scripts are stubs; sidecar binary is a 2-byte placeholder

**Description:** Freeze the Python backend into per-triple Nuitka executables, wire as Tauri `externalBin`, set up code-signing (Windows Authenticode / macOS Developer ID+notarization / Linux unsigned). Test scaffolding at `tests/tauri/mig18/` (9 test files).

**Confirmed findings (2026-07-24):**
- `scripts/build/nuitka_freeze.sh` = **1-line stub** (does nothing).
- `scripts/build/build_sidecar_linux.sh` and `build_sidecar_macos.sh` = also stubs.
- Windows CI pipeline (`tauri-windows-build.yml`) has Nuitka freeze fully wired in CI — this is the reference implementation for other platforms.
- Linux postinst/prerm scripts exist. macOS entitlements/Info.plist exist.

**Fix:** Blocked on MIG-1.5/1.6/1.7. After Phase 0 gates pass per platform: (1) fill Nuitka freeze scripts per ADR-0020 §4; (2) create per-platform CI workflows; (3) implement code-signing; (4) verify postinst/prerm on Linux.

**Severity:** 🔴 Critical — no production Tauri build possible without this.

---

### HP-5. MIG-1.9 — Phase 3–5: UI port + wire swap & per-platform cutover

**Status:** ❌ Not Fixed — Phase 3 ~60% code-complete but 0% runtime-tested; Phases 4–5 not started

**Description:** The final capstone — make the UI runtime-agnostic, flip from Electron to Tauri webview, cut each OS over while keeping Electron as reversible fallback. Test scaffolding at `tests/tauri/mig19/` (9 test files).

**Current state (Phase 3 — UI bridge):**
- **About 60% code-complete**: `voice_typer/client/src/renderer/src/lib/tauri-bridge/` package exists (4 submodules: `index.ts`, `detect.ts`, `python-namespace.ts`, `bubble-namespace.ts`, `window-namespace.ts`) — it auto-detects Tauri vs Electron and installs the correct `window.python`/`window.bubble`/`window.window_` namespaces.
- **Compiles clean**: TypeScript typechecks pass (0 errors in both `tsconfig.web.json` and `tsconfig.node.json`). Unit tests in `tauri-bridge-commands.test.ts` pass under Vitest.
- **Never run under real Tauri webview**: Both `main.tsx` and `bubble-main.tsx` import `./lib/tauri-bridge` at startup, but since Electron is still the active entry point, the bridge detects `window.__TAURI__` is absent and early-returns (no-op). The Tauri code path has never executed in a real browser window.
- **Main entry is still Electron**: `voice_typer/client/src/main/index.ts` still `import { app } from "electron"`. The Tauri `src-tauri/tauri.conf.json` points `frontendDist` at the Electron-renderer build output (`voice_typer/client/dist`).

**Current state (Phase 4–5 — wire swap + cutover):** 0% — no wire swap, no cutover. Nobody has ever built `cargo tauri build` or installed a Tauri-based `.exe` on any computer.

**The critical gap:** The **renderer is shared** between both stacks — the same React bundle works under both Electron and Tauri. The bridge code (Phase 3) is written and compiles. What's missing is:
1. Building the real Python sidecar binary (blocked on MIG-1.8)
2. Running `cargo tauri build` to produce an actual Tauri installer
3. Installing and clicking around to verify the bridge actually works at runtime
4. Swapping the default launch path from Electron to Tauri

**Severity:** 🔴 Critical — this is the capstone of the entire migration epic.

---

### HP-8. MIG-1.4 — Prewarm packaging + FT-1 supervisor

**Status:** ⚠️ Partial — Rust supervisor code compiles; all prewarm binaries are 0-byte placeholders

**Description:** Prewarm (model/asset warm-up) binary must launch at login/boot on each OS (LogonTrigger / LaunchAgent / systemd timer). FT-1 crash isolation must respawn the sidecar on crash without killing the UI.

**Confirmed findings (2026-07-24):**
- Rust supervisor code in `src-tauri/src/sidecar/` exists and compiles. `resolve_prewarm_exe()` exists.
- **All 7 `src-tauri/bin/prewarm-*.*` files are 0-byte placeholders** — never built.
- Platform-specific autostart wiring (LogonTrigger, LaunchAgent, systemd timer) not validated on real hosts.

**Severity:** 🟡 High — blocks production readiness but lower urgency than Phase 0 gates.


### FT-2. Full test suite dies at ~63% — context-dependent native crash [High] — Pending
- **Files (crash site)**: `tests/test_lock_order_contract.py` (passes 11/11 in isolation).
- **Files (fixed prerequisite)**: `voice_typer/server/clipboard_snapshot.py` (GDI-handle capture skip + `_configure_win32_signatures()`) — the earlier clipboard crash that this task was uncovered by is already FIXED.

- **What "reaches 63%" means (plain English)**: When you run the whole test suite (`pytest tests/`), pytest works through the test files in order and prints a running percentage of how far it has gotten. It now gets to **63% of all tests** and then the Python process suddenly dies — it never reaches 100%.

- **The picture**:
  - Test files run roughly alphabetically: `test_a...`, `test_b...`, `test_clipboard.py`, … `test_lock_order_contract.py`, …
  - **Before the clipboard fix**: `test_clipboard.py` (early in the list) crashed the whole Python process, so the run died early — it never even reached the later files.
  - **After the clipboard fix**: clipboard no longer crashes, so the run gets much further — all the way to `test_lock_order_contract.py`, at about the **63%** mark. But *there*, the process dies again (a **different** crash).
  - So "reaches 63%" = the run now progresses through 63% of the tests before hitting the **next** crash, instead of dying much earlier at clipboard.

- **The catch**: `test_lock_order_contract.py` **passes fine by itself (11/11)**. It only crashes when run as part of the big sequence — meaning some **earlier** test leaves the system in a bad native state that makes this later one blow up. That is a **separate bug** from the clipboard one.

- **Symptoms**: Process dies silently (native crash), exit code 1, no faulthandler traceback. Likely bad native state left by an earlier win32/hotkey/IME test.

- **Bottom line**: The clipboard crash is fixed. But the suite still cannot run start-to-finish because there is at least one more crasher further down.

- **Options / next moves** (decision needed):
  1. **Chase it** — bisect the full run to find which earlier test leaves the bad native state, then fix the root cause.
  2. **Stop here** — clipboard crash (the originally-scoped fix) is done; leave the suite-level crash for later.
  3. **Isolation workaround** — run tests with process isolation (e.g. `pytest-forked` / `--forked`) so one test's native crash cannot kill the whole run.
- **Effort**: Medium–Large (root-cause bisection across ~native win32/hotkey/IME tests).
- **Priority**: High (blocks a clean full-suite run).

### FT-5. "Finish dictation → nothing gets transcribed" — no transcription text produced/pasted [High] — Pending
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

plus the base repo's pre-existing comprehensive review.

## Status Legend

- ✅ Fixed — the issue was resolved in this session.
- ⚠️ Partial — partial fix applied; follow-up work documented.
- ❌ Pending — issue identified but not fixed.
- 💥 Broken — fix introduced a regression.
- 🚫 Won't Fix — issue acknowledged but consciously not addressed.

## Structure

1. **Base Set** — the original `comprehensive-review.md` from the repo root,
   preserved verbatim. This is the pre-existing set of open findings.
2. **Per-Session Findings** — each session's `comprehensive-review.md`,
   appended verbatim under a `## Session N Findings` header. Sessions used
   different formats (`## PVT-N`, `## [PVT-N]`, `**[PVT-N]**`, `### QW-N`);
   rather than risk dropping findings by parsing 5 incompatible formats, we
   preserve each session's review verbatim. The integrity check (every
   finding from every session appears at least once) is therefore trivially
   satisfied.
3. **Merge-Stage Findings** — new findings discovered during the intelligent
   sub-agent merge (NOT present in any session's original review).

---

## Base Set (original comprehensive-review.md — pre-existing open findings)

# Comprehensive Review — Open Findings (partial/broken/not-implemented items)

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.
>
> This file was filtered by verifier-agent on 2026-07-21 to REMOVE entries for fixes that were verified-done and KEEP only items that remain partial, broken, or not-implemented.

---

## Quick-Win Batch (LOW RISK, HIGH VALUE — fix first)

#### ARCH-5 — `service.py` (2,116 lines): 66-method facade
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperService` exposes 66 total methods (1 `__init__` + 65 public). 21 pure delegation, 44 with real logic. 16 section comment headers span 8 domains (history, model, onboarding, microphone_test, vocabulary, template, status, dictation).
- **Investigation**: VERIFIED. `inspect.getsource(VoiceTyperService.apply_config)` follows `__func__` to defining module — works through mixin inheritance. `hasattr(VoiceTyperService, "test_llm_connection")` works via MRO. Only 6 source-file-read assertions need updating.
- **Mixin approach is safe**: No monkeypatch-by-path blockers unlike ARCH-2/4. Re-exports in `__init__.py` will preserve all 65 public names.
- **Recommended fix**: Split into `voice_typer/server/service/{history,model,onboarding,microphone_test,vocabulary,template,status,dictation}.py` mixins or sub-services. Preserve public method names via re-export or delegation shim.
- **Effort**: 🟡 **MEDIUM** — Lower risk than other splits. ~4-5 hours.
- **Confidence for one-shot fix**: 75% — mixin approach is safe; only 6 assertions need updating.

#### ARCH-8 — `_open_config_file` extraction blocker (source-string tests)
- **Severity**: Medium
- **Status**: Pending
- **Description**: `VoiceTyperApp._open_config_file` (104 LOC) is the only remaining "fat" method on `VoiceTyperApp`. Extraction blocked by 6 `inspect.getsource` tests in `tests/test_b4_config_editor_lock.py` and `tests/regressions/concurrency_test.py` that pin literal source text.
- **Recommended fix**: Port these 6 source-string tests to behavioral tests (RW-8 pattern), then extract `ConfigEditorLauncher`. ~1-day effort.
- **Effort**: 🟡 **MEDIUM** — The source-string porting is the tricky part. Must carefully preserve test behavior. The `_open_config_file` method is only 104 LOC and relatively self-contained. ~1 day.
- **Confidence for one-shot fix**: 80% — self-contained but source-string tests add friction.

#### ARCH-9 — `app.py` test-seam re-exports (173 monkeypatch sites)
- **Severity**: Low
- **Status**: Pending
- **Description**: `app.py` re-exports 20 symbols from sibling modules so tests can monkeypatch `voice_typer.server.app.X`. 173 monkeypatch sites depend on these re-exports.
- **Recommended fix**: Migrate monkeypatch sites to canonical paths (`voice_typer.server.server_platform.is_autostart_enabled` instead of `voice_typer.server.app.is_autostart_enabled`), then delete re-export blocks. Mechanical refactor touching many files.
- **Effort**: 🔴 **HIGH** — 72+ import sites across 65+ files, ~20 re-exported symbols. Every monkeypatch site must be migrated one-by-one. High risk of breaking tests. Cannot do in one shot confidently. ~1 day.
- **Confidence for one-shot fix**: 50% — wide surface area, many tests.

#### ARCH-10 — Circular import between `ipc_server.py` and `handlers/*.py`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 handler mixins import `log` and `_validate_dict_payload` from `ipc_server.py`; `ipc_server.py` imports the mixins back. Cycle is broken by ordering (helpers defined before handler imports).
- **Rationale for Won't Fix**: Pattern is stable and documented. Moving helpers to `ipc_helpers.py` would be cleaner but provides no runtime benefit.
- **Effort**: None — Won't Fix by design.

#### ARCH-12 — 164 `inspect.getsource` source-string tests across the codebase
- **Severity**: Low
- **Status**: Pending (ongoing)
- **Description**: 164+ source-string tests pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Make refactoring expensive.
- **Recommended fix**: Adopt project rule — "no new `inspect.getsource` tests; port existing ones when touching the code they pin." Chip away over time.
- **Effort**: 🔴 **EXTRA HIGH** — 164+ calls across 30+ test files. Not a discrete task — it's a project-wide migration. Chip away individually when touching pinned code. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — cannot complete in one shot.

#### ARCH-13 — TYPE_CHECKING back-references from controllers to `VoiceTyperApp`
- **Severity**: Low
- **Status**: Won't Fix
- **Description**: 13 modules use `if TYPE_CHECKING:` to import `VoiceTyperApp` for type annotations. `VoiceTyperApp` IS the service locator.
- **Rationale for Won't Fix**: Runtime cycle is already broken via lazy imports. Annotating against `AppProtocol` (already defined in `providers.py`) would be cleaner but provides no runtime benefit.
- **Effort**: None — Won't Fix by design.

## Cross-Platform (all Pending)

#### XPLAT-12 — Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: Pending (host validation required)
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.
- **Effort**: 🔴 **HIGH** — Requires Windows-on-ARM runner access not available in this sandbox. Cannot complete.
- **Confidence for one-shot fix**: 10% — blocked by runner availability.

#### XPLAT-19 — [Partial] ADR §6.3 Win32 focus-restore now compiles
- **Severity**: High
- **Status**: **Partial** (compiles but not runtime-validated)
- **Description**: The Win32 focus-restore path (`src-tauri/src/commands/sidecar_cmds.rs`) now compiles (verified via `cargo check` EXIT:0 on win32 GNU target). Remaining work: real Windows host smoke test.
- **Recommended fix**: Run the `VALIDATE-ON-WINDOWS-HOST` block — launch elevated Notepad, dictate, confirm focus returns. Cannot be run in this sandbox.
- **Effort**: 🔴 **HIGH** — Requires actual Windows host with elevated Notepad. Cannot complete in sandbox. ~0.5 day on real hardware.
- **Confidence for one-shot fix**: 40% — blocked by hardware access.

---

## Test Infrastructure (all Pending)

#### TEST-2 — 99 `time.sleep` calls across 28 test files (flakiness-prone)
- **Severity**: Medium
- **Status**: Pending
- **Description**: 127+ `time.sleep(...)` calls across 28+ test files act as fixed-delay synchronization, which is flaky on loaded CI runners.
- **Root cause**: Tests synchronize on time instead of condition/event.
- **Recommended fix**: Replace fixed sleeps with condition waits (events, `threading.Event.wait`, or polling predicates). Chip away file-by-file. ~2-day effort.
- **Effort**: 🔴 **HIGH** — 127+ sleep calls across 28+ files. Each one needs individual analysis to determine the correct replacement (event.wait, polling predicate, etc.). ~2 days.
- **Confidence for one-shot fix**: 30% — cannot do all in one shot; chip away file-by-file.

#### TEST-4 — `test_server.py` (2,799 LOC) + `test_app.py` (2,484 LOC) are spaghetti test files
- **Severity**: Low
- **Status**: Pending
- **Description**: The two largest test files bundle many unrelated test classes with shared heavy fixtures.
- **Recommended fix**: Split by domain into `tests/server/` submodules; share fixtures via `conftest.py`.
- **Effort**: 🔴 **HIGH** — Requires careful separation of test classes, extraction of shared fixtures, and ensuring no breakage. ~1-2 days.
- **Confidence for one-shot fix**: 50% — large files with many dependencies.

#### TEST-5 — 12 modules >650 LOC with no dedicated test file
- **Severity**: Low
- **Status**: Pending
- **Description**: 12 source modules over 650 LOC have no matching `tests/*` file.
- **Recommended fix**: Add focused unit-test files per module.
- **Effort**: 🔴 **EXTRA HIGH** — Adding comprehensive tests for 12 large modules is a major effort. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — too many modules to cover in one shot.

---

## CI/CD (all Pending)

#### CI-1 — 5 `if: false` guards across 3 Tauri workflows (intentional, pre-Phase-0)
- **Severity**: Low
- **Status**: Pending (by design)
- **Description**: Five `if: false` guards disable jobs across 3 Tauri workflows; intentional scaffolding.
- **Recommended fix**: Remove guards progressively as each MIG phase lands.
- **Effort**: 🟢 **LOW** — Just removing `if: false` guards when the corresponding phase is ready. But cannot do until phases are validated. ~5 min per guard.
- **Confidence for one-shot fix**: 90% — simple YAML edits, but blocked on phase validation.

#### CI-2 — Windows workflow x86_64-only (no aarch64 Windows-on-ARM)
- **Severity**: Low
- **Status**: Pending
- **Description**: The Windows CI workflow builds only x86_64; Windows-on-ARM has no build/validate job.
- **Recommended fix**: Add an aarch64 Windows job once a runner is available.
- **Effort**: 🔴 **HIGH** — Blocked by runner availability. Cannot complete.
- **Confidence for one-shot fix**: 10% — blocked by GHA runner availability.

#### CI-4 — macOS signing order wrong (`.app` not signed before notarization)
- **Severity**: Medium
- **Status**: Pending
- **Description**: The macOS workflow invokes notarization before the `.app` bundle is signed.
- **Recommended fix**: Sign the `.app` first, then submit to notarytool.
- **Effort**: 🟡 **MEDIUM** — Requires reordering CI steps in `tauri-macos-build.yml`. Cannot validate without a real macOS runner. ~0.5 day.
- **Confidence for one-shot fix**: 60% — cannot verify without macOS runner.

---

**Bottom line for the next agent:** Do NOT trust "all green on Linux" as proof of cross-platform cutover.

---

# Per-Session Consolidated Findings (Sessions 1–6)

> Appended from per-session `comprehensive-review-N.md` reviews. Findings deduplicated by **name AND content**:
> when a session-1 authoritative finding (real `voice_typer/` paths) already covers a root cause, later sessions'
> entries point to it instead of re-listing. Sessions 2–6 agents used generic `src/voice_typing/` / `src/ui/` paths
> that do NOT match this repo; their low-confidence items are explicitly flagged. Session 6 used its own `H-*` / `M-*` scheme.


> IPCServer-duplicate (S1-CR-1) also reported in sessions 2-6 — included once here.

## Backend / IPC

### S1-CR-33 — ~45 failing vitest tests across 14 client files — real UI regressions
- **Severity**: High · **Status**: Pending
- **Category**: Existing failing tests / UI regressions
- **Location**: 14 test files under `voice_typer/client/src/renderer/src/__tests__/` and `components/__tests__/`
- **Evidence**: Subset run (4 files, 60s timeout): `Test Files 4 failed (4), Tests 18 failed | 20 passed (38)`. Partial full run showed ~45 failed across 14 files: `Sidebar.test.tsx` (9 fail), `TitleBar.test.tsx` (3 fail), `Home.test.tsx` (1), `ModelsPage.test.tsx` (5), `App-ux-fixes.test.tsx` (6), `Onboarding.test.tsx` (1), `ExportFormatMenu.test.tsx` (5), `segmented-control.test.tsx` (3), `LiveQualityFeedback.test.tsx` (1), `StatsShareImage.test.tsx` (2), `DownloadProgressBar.test.tsx` (2), `electron-ipc-build-behavior.test.tsx` (4), `consent-privacy-behavior.test.tsx` (2), `ux-components-behavior.test.tsx` (1).
- **Evidence example** (Sidebar): `× UX-16: active nav item carries the 2px left accent bar + soft accent background classes` — `AssertionError: expected 'group/button inline-flex shrink-0 ite…' to contain 'border-l-2'`. Real accessibility and UX regressions in the Sidebar and TitleBar components.
- **Impact**: Real production feature losses (aria-keyshortcuts, destructive hover tokens, etc.), not flaky tests.
- **Proposed fix**: Investigate `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx` and `TitleBar.tsx` — likely a refactor regressed the FIX-15 / UX-16 / PROD-7/9/14 changes.
- **Confidence**: High · **Found by**: R12

### S1-CR-37 — macOS Tauri workflow never codesigns sidecar + prewarm binaries before bundling (CI-4 re-framed)
- **Severity**: High · **Status**: Pending
- **Category**: CI/CD / macOS packaging / signing
- **Location**: `.github/workflows/tauri-macos-build.yml:182-285`
- **Evidence**: The job's signing steps: `Build Tauri universal .app + .dmg` (cargo tauri build — signs main .app executable via config's `signingIdentity`, but NOT the nested Mach-O binaries `python-sidecar-{x86_64,aarch64}-apple-darwin`, `prewarm-{x86_64,aarch64}-apple-darwin`, `macos-key-listener` under `Contents/Resources/` and `Contents/MacOS/`). `Notarize + staple` submits the .app to `notarytool`. The only explicit `codesign` is on the .dmg at line 270 — AFTER the .app has already been notarized. Apple's notarization service rejects .app bundles containing unsigned Mach-O binaries with `The binary is not signed`. The signing-guide.md §"Signing command" documents this exact step but the workflow doesn't execute it.
- **Impact**: Notarization will reject the .app bundle. macOS Tauri release is broken.
- **Proposed fix**: Add `codesign --force --options runtime --sign "$MAC_SIGNING_IDENTITY" --entitlements src-tauri/entitlements.plist <binary>` step for each of the 5 nested Mach-O binaries, BEFORE the notarize step. Alternatively `codesign --deep --force --options runtime --sign "$MAC_SIGNING_IDENTITY" --entitlements src-tauri/entitlements.plist "$APP_PATH"`.
- **Confidence**: High · **Found by**: R14

### S1-CR-38 — `sync_versions.py` not actually enforced in CI
- **Severity**: High · **Status**: Pending
- **Category**: CI/CD
- **Location**: `.github/workflows/build.yml:340-344`
- **Evidence**: The script `scripts/build/sync_versions.py:159-223` defines three modes: no args (print, return 0), `--apply` (write), `--check` (exit 1 if any file's version differs). CI uses **no args** — always returns 0 even when versions drift.
- **Impact**: Versions can drift across `pyproject.toml`, `voice_typer/__init__.py`, `voice_typer/client/package.json`, `voice_typer/client/electron-builder.yml` without CI failing.
- **Proposed fix**: Change `python scripts/build/sync_versions.py` to `python scripts/build/sync_versions.py --check` in `build.yml:343`.
- **Confidence**: High · **Found by**: R14

### S1-CR-41 — RPM `prerm.rpm` same legacy-path bug
- **Severity**: High · **Status**: Pending
- **Category**: Packaging / Linux / RPM
- **Location**: `scripts/linux/prerm.rpm:11`
- **Evidence**: Same NF-R9-2 class of bug, on the uninstall side. Tauri v2 bundles resources at `/usr/lib/voice-typer/resources/scripts/linux/uninstall_permissions.py`, never at `/usr/share/voice-typer/scripts/`.
- **Proposed fix**: Mirror the postinst's probe loop in `prerm.rpm`.
- **Confidence**: High · **Found by**: R15

### S1-CR-43 — Uninstall does NOT remove user data: config dir, logs, model cache (GB-sized), history DB
- **Severity**: High · **Status**: Pending
- **Category**: Packaging / data lifecycle
- **Location**: `scripts/linux/prerm:10-20`, `scripts/linux/prerm.rpm:7-16`, no Windows MSI/NSIS uninstall custom action, no macOS uninstaller
- **Evidence**: Linux prerm scripts remove ONLY system-level artifacts. They do NOT touch the user's data directory. Per `config.py:417-478`, the user data directory contains `config.json`, `history.db`, `logs/`, `huggingface/hub/` (model cache, potentially GB-sized), `backend.pid`, etc. On macOS, the LaunchAgent plist at `~/Library/LaunchAgents/com.voicetyper.plist` is orphaned. On Windows, HKCU Run key + Task Scheduler entry are orphaned.
- **Impact**: (1) Users who uninstall + reinstall get stale config / history / models. (2) Model cache (Whisper-large ~3 GB, Qwen3-ASR ~1.5 GB) orphaned on uninstall. (3) On macOS, launchd tries to spawn the deleted Python interpreter at next login. (4) On Windows, same with Run key / Task Scheduler.
- **Proposed fix**: Linux: extend `prerm`/`prerm.rpm` to also `disable_autostart` and offer `--purge` semantics for user data. Windows NSIS: add an `nsis.installerHooks` uninstaller hook that calls a cleanup script. macOS: ship an `Uninstall Voice Typer.app` helper.
- **Confidence**: High · **Found by**: R15

### S1-CR-47 — Server-side tray i18n only supports 2 of 8 locales
- **Severity**: High · **Status**: Pending
- **Category**: i18n / tray
- **Location**: `voice_typer/server/tray.py:97-100`
- **Evidence**: `_TRAY_LABELS_LOCALES = {"en": _TRAY_LABELS_EN, "es": _TRAY_LABELS_ES}`. Switching to any of `ar`, `de`, `fr`, `hi`, `ru`, `zh` falls back to English. Server-side `i18n.py:244` only `register_locale("en", _INITIAL_LABELS)` — the 50+ `notify.*` and `state.*` keys have no non-English translations registered.
- **Impact**: Tray menu, tray notifications, and tray tooltip state messages are English-only for 6 of 8 supported locales.
- **Proposed fix**: Either register locale dicts for the 6 missing locales, or auto-generate from the client-side locale JSONs.
- **Confidence**: High · **Found by**: R18

### S1-CR-49 — i18n test gate is RED: 14 of 15 completeness tests fail
- **Severity**: High · **Status**: Pending
- **Category**: i18n / existing failing tests
- **Location**: `tests/test_i18n_completeness.py`, `tests/regressions/i18n_test.py`
- **Evidence**: `pytest tests/test_i18n_completeness.py -k "key_parity or extra_keys"` yields 14 failures (7 key_parity + 6 extra_keys + 1 summary). `tests/regressions/i18n_test.py::TestSpanishTranslationComplete::test_es_json_has_same_keys_as_en` fails with 24 missing keys.
- **Impact**: i18n test infrastructure correctly catches regressions but failures are unaddressed.
- **Proposed fix**: Fix S1-CR-16, S1-CR-48, S1-CR-50 first; the 14 failures collapse to those 3 root causes.
- **Confidence**: High · **Found by**: R18

### S1-CR-50 — ~19 orphan keys in `en.json` (defined but never referenced in code)
- **Severity**: High · **Status**: Pending
- **Category**: i18n / tech debt
- **Location**: `voice_typer/client/src/renderer/src/locales/en.json`
- **Evidence**: 24-25 "missing" keys include ~19 orphans that exist only in en.json and are never referenced in `voice_typer/client/src/renderer/src/**/*.{ts,tsx}`:
  - `bubble.micButtonAria` (the generic version — code uses `micButtonStartAria`/`micButtonStopAria`)
  - `settings.bubbleClickToToggle`, `settings.bubbleClickToToggleDescription`
  - `onboarding.micLevel`, `onboarding.modelMultilingual`
  - `onboarding.permissionsTitle`, `permissionsDescription`, `permissionsLoading`, `permissionsNeeded`, `permissionsOk`, `permissionsNoneNeeded`, `permissionsTestButton`, `permissionsTestFailure`, `permissionsTestLabel`, `permissionsTestSuccess`
  - `onboarding.skipConfirmLabel`, `skipConfirmMessage`, `skipConfirmTitle`
  - `onboarding.step4Item`, `step5Item`
- **Impact**: `scripts/add_i18n_keys.py --all` would propagate these to all locale files as English placeholders for keys that no code uses.
- **Proposed fix**: Delete the orphan keys from `en.json` (not propagated to other locales).
- **Confidence**: High · **Found by**: R18

### S1-CR-52 — README architecture tree is stale (flat files are now packages)
- **Severity**: High · **Status**: Pending
- **Category**: Documentation / onboarding
- **Location**: `README.md:404-431`
- **Evidence**: README lists `recording.py`, `hotkeys.py`, `server_platform.py`, `prewarm.py / prewarm_scheduler_posix.py` as flat modules. Actual repo layout: `recording/`, `hotkeys/`, `server_platform/`, `prewarm/` are all packages.
- **Impact**: New contributors following README's "Architecture" section will look for files that don't exist.
- **Proposed fix**: Update README §"Architecture" tree to reflect package layout (or link to `docs/ARCHITECTURE.md`).
- **Confidence**: High · **Found by**: R20

### S1-CR-54 — ADR-0018 references a nonexistent test file
- **Severity**: Medium · **Status**: Not Fixed
- **Category**: Documentation / ADR accuracy
- **Location**: `docs/adr/0018-heartbeat-watchdog.md:90`
- **Evidence**: ADR-0018 §"References" cites `tests/test_ipc_server.py` as the home of `test_heartbeat_timeout_calls_quit()`. The file `tests/test_ipc_server.py` does NOT exist in the working tree (glob-confirmed). The real heartbeat test files are `tests/test_heartbeat.py` and `tests/test_heartbeat_force_exit.py`. The sibling ADR-0019 was fixed (its reference now points to the real `tests/test_ipc4_rate_limiter_dual_window.py`), but ADR-0018's broken reference was never corrected.
- **Impact**: Contributors following the ADR's pointer to verify the watchdog test will hit a dead link. ADR accuracy regression vs ADR-0019.
- **Proposed fix**: Update `docs/adr/0018-heartbeat-watchdog.md:90` to reference the real test file(s): `tests/test_heartbeat.py` (and/or `tests/test_heartbeat_force_exit.py`) and the actual test function name(s).
- **Confidence**: High · **Found by**: Verifier (2026-07-24)

## MEDIUM severity findings (41 unique)

### S1-CR-58 — ADR 0010 §2.1 documentation drift (lists removed attrs as on AppProtocol)
- Location: `docs/adr/0010-dependency-injection-boundary.md:67-74`
- Evidence: Claims `_audio_processor`, `_volume_ducker`, `_config_mutation_lock` are on `AppProtocol`. `providers.py:92-99` documents them as REMOVED in TASK-2.
- Fix: Update ADR 0010 §2.1 to remove them; mention `ServiceProtocol` methods that wrap those accesses. · **Found by**: R1


### S1-CR-65 — `apply_config_side_effects` 215-line branching method
- Location: `voice_typer/server/service.py:1045-1260`
- Evidence: Single method with 8+ parallel `if "X" in updates:` blocks; 12 distinct side-effects.
- Fix: Define `ConfigSideEffect` protocol; register ~12 handlers in a list; each handler lives in its own module. · **Found by**: R1

### S1-CR-66 — `sys.modules` registration hack in `ipc_server.py:622-632`
- Location: `voice_typer/server/ipc_server.py:622-632`
- Evidence: `_CANONICAL = "voice_typer.server.ipc_server"; if _CANONICAL not in sys.modules: sys.modules[_CANONICAL] = sys.modules["__main__"]` — needed because `python -m voice_typer.server.ipc_server` loads the file as `__main__` but handler mixins import from the canonical name.
- Fix: Give handler mixins their own helper module (`ipc/_helpers.py`); mixins import from `_helpers`; `IPCServer` imports from `_helpers` AND from mixins. No cycle, no hack. · **Found by**: R1

### S1-CR-67 — Custom `_RecordingModule` / `_PrewarmModule` / `_ServerPlatformModule` sys.modules hacks
- Location: `voice_typer/server/recording/__init__.py:260-349`, `voice_typer/server/prewarm/__init__.py` (289 LOC), `voice_typer/server/server_platform/__init__.py:84-277`
- Evidence: Three packages install custom module subclasses that override `__getattr__` and `__setattr__` so test patches like `monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)` propagate to submodules. ~500 LOC of `__init__.py` boilerplate exists for test-patch compatibility.
- Fix: Migrate tests to patch submodules directly; remove custom module classes and `_pkg.X` indirection. · **Found by**: R1

### S1-CR-69 — ADR-0015 documentation drift (allowlist location, missing commands)
- Location: `docs/adr/0015-electron-command-allowlist.md:5,56,75`
- Evidence: (1) Says allowlist at `client/src/main/index.ts:532-627` — actually at 79-191. (2) Lists `show_electron_notification` under "Not in the allowlist" but `index.ts:186` includes it. (3) ADR's "exhaustive list" omits 8 commands added later: `repaste_last`, `force_cancel_transcription`, `refresh_microphones`, `get_rms_level`, `get_audio_status`, `get_vocabulary_suggestions`, `apply_vocabulary_suggestion`, `dismiss_vocabulary_suggestion`.
- Fix: Update ADR-0015 to match actual allowlist. · **Found by**: R2

### S1-CR-73 — Component size: 9 files exceed 500-800 LOC monolith threshold
- Location: `pages/Models.tsx` (1682), `pages/Settings.tsx` (1082), `components/settings/ThemeSettingsSection.tsx` (890), `pages/Microphone.tsx` (862), `components/hotkey/HotkeyPicker.tsx` (816), `pages/Home.tsx` (849), `pages/Templates.tsx` (716), `pages/Vocabulary.tsx` (700), `pages/History.tsx` (562)
- Evidence: `Models.tsx` (1682 LOC) mixes model catalog fetching, download state machine, cloud provider config, model-family grouping, accordion UI, dialogs. `Settings.tsx` (1082) owns config state, debounced save, search-filter-with-tab-routing, reset dialog. `ThemeSettingsSection.tsx` (890) mixes oklch→sRGB color conversion, DOM-based color resolution, localStorage draft backup, hover-preview state, UI.
- Fix: Split each large component into focused subcomponents (e.g., `ModelCatalogSection`, `ModelDownloadManager`, `CloudProviderSection`, `ModelImportDialog` for `Models.tsx`). Extract color math to `lib/color-utils.ts`. · **Found by**: R2

### S1-CR-74 — Error envelope contract doc is stale (describes shape that doesn't match implementation)
- Location: `docs/architecture/error-envelope-contract.md:9-21`
- Evidence: Doc describes `{"id": 42, "ok": false, "message": "..."}` (TCP) and `{"id": 42, "ok": false, "code": "internal_error", "message": "internal error"}` (WS). Actual: `{"type": "error", "data": {"code": "internal_error", "message": "internal error"}, "id": 42}` for both paths. Doc's claimed TCP-vs-WS detail-level distinction doesn't exist.
- Fix: Update doc to show actual shape used by both paths; remove false TCP-vs-WS distinction. · **Found by**: R3

### S1-CR-76 — TS per-command timeouts are dead code — Rust's hardcoded 120s dispatch timeout always fires first
- Location: `voice_typer/client/src/renderer/src/hooks/usePython.ts:38-44` vs `src-tauri/src/commands/sidecar_cmds.rs:47` + `src-tauri/src/util.rs:40`
- Evidence: TS defines per-command timeouts (`download_model: 600_000`). But Rust has hardcoded `DISPATCH_TIMEOUT_SECS = 120`. For `download_model` (10min TS, 120s Rust), the Rust timeout fires at 120s → `invoke` rejects → user sees false-positive "dispatch timeout (120s)" error even though download still in progress.
- Fix: Either make `DISPATCH_TIMEOUT_SECS` per-command on the Rust side (accept `timeout` arg in `DispatchArgs`), or document that Rust 120s is the hard cap. · **Found by**: R3

### S1-CR-78 — IPC protocol is unversioned — schema drift between Python/Rust/TS is undetectable at runtime
- Location: All IPC frames across `voice_typer/server/ipc/server.py`, `voice_typer/server/sidecar_ws.py`, `src-tauri/src/sidecar/ws.rs`, `src-tauri/src/commands/sidecar_cmds.rs`, `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`
- Evidence: No `protocol_version` field in any frame. If any layer changes the envelope shape, other layers can't detect mismatch at runtime. `server_started` handshake already has a minor inconsistency (uses `event` key instead of `type`).
- Fix: Add `protocol_version` field to the `server_started` handshake and to the auth frame. Have the Rust host check version on connect and fail fast on mismatch. · **Found by**: R3

### S1-CR-80 — `IPCServer._accept_tcp` `pool.submit()` can race `stop()`'s `pool.shutdown()` — `RuntimeError` kills accept thread + leaks socket
- Location: `voice_typer/server/ipc/server.py:616-624` (submit), `:432-441` (shutdown)
- Evidence: `stop()` calls `self._tcp_worker_pool.shutdown(wait=False, cancel_futures=True)`. The accept loop reads `pool = self._tcp_worker_pool`, then calls `pool.submit(...)`. If `stop()` runs between the read and the submit, `pool.submit(...)` raises `RuntimeError("cannot schedule new futures after shutdown")`. The accept loop's outer `try/except OSError` does NOT catch `RuntimeError`. The just-accepted `conn` socket is leaked.
- Fix: Wrap `pool.submit(...)` in `try/except RuntimeError: conn.close(); break`. · **Found by**: R9

### S1-CR-82 — `_get_rate_limiter` TOCTOU — first-dispatch race creates distinct limiters
- Location: `voice_typer/server/ipc/rate_limiter.py:240-246`
- Evidence: Read-then-set is not atomic. With TCP worker pool at `max_workers=4` plus WS dispatch path, two simultaneous first-dispatch calls can both read `None`, both create a fresh `_RateLimiter`, both install theirs.
- Fix: Protect with a per-server lock, or use `setattr`-if-absent via a sentinel. · **Found by**: R9

### S1-CR-84 — `clipboard._force_restore_pending_at_exit` can race a finishing daemon thread
- Location: `voice_typer/server/clipboard.py:62-98`, `:1215-1251`
- Evidence: The atexit handler snapshots `_pending_restores` under the lock, then iterates the snapshot *outside* the lock calling `snapshot.restore()`. The `_delayed_restore` daemon thread does NOT remove its entry from `_pending_restores` (related to S1-CR-2). Concurrent `ClipboardSnapshot.restore()` calls from two threads race on Win32 `OpenClipboard`/`EmptyClipboard`/`SetClipboardData` and on macOS `NSPasteboard.clearContents`/`writeObjects_`.
- Fix: Have `_delayed_restore` remove its entry from `_pending_restores` on completion (the docstring already claims this); re-check the atexit snapshot under the lock per-item. · **Found by**: R9

### S1-CR-97 — `Config.save_strict()` exists but is NOT wired into `service.apply_config` — disk failures silently swallowed
- Location: `voice_typer/server/config.py:1061-1086`, `voice_typer/server/service.py:1408`
- Evidence: `save_strict()` raises `RuntimeError` on disk failure. Its docstring explicitly says: "Wiring `apply_config` (in `service.py`) to call this instead of `save()` is a follow-up task — out of scope for this file." But `service.py:1408` still calls `app.config.save()` (non-strict). The IPC handler returns `{type: "ack"}` regardless of whether disk write succeeded.
- Fix: Wire `service.apply_config` to call `save_strict()`; have the IPC handler surface disk failures. · **Found by**: R10

### S1-CR-98 — Committed ELF binary `voice_typer/server/native/linux-key-listener`
- Location: `voice_typer/server/native/linux-key-listener` (committed, 25632 bytes, ELF 64-bit x86-64)
- Evidence: CI workflow `build.yml:415-539 build-native` matrix already compiles all three binaries per-platform. The committed Linux binary is stale (built at some past commit). Windows and macOS binaries are NOT committed (only sources).
- Fix: `git rm voice_typer/server/native/linux-key-listener`; add to `.gitignore`. · **Found by**: R14

### S1-CR-99 — Windows Tauri workflow doesn't sign MSI installer or native `windows-key-listener.exe`
- Location: `.github/workflows/tauri-windows-build.yml:238-290`
- Evidence: `Sign sidecar + prewarm` (line 238-257) signs `python-sidecar-x86_64-pc-windows-msvc.exe` and `prewarm-x86_64-pc-windows-msvc.exe`. `Build the Tauri app` runs `cargo tauri build`. `Sign the final NSIS installer` signs NSIS only. NOT signed: the MSI installer and `src-tauri/resources/native/windows-key-listener.exe`.
- Fix: Either set `WIN_SIGN_COMMAND` env to a signtool invocation and let Tauri sign during bundling, or extend the `Sign sidecar + prewarm` step's `foreach` loop to include `windows-key-listener.exe` and add a separate signtool step for the MSI. · **Found by**: R14

### S1-CR-100 — Smoke tests NOT run after Tauri builds
- Location: `.github/workflows/tauri-{windows,macos,linux}-build.yml`
- Evidence: Legacy `build.yml:466-524` runs real smoke tests on native listener binaries. Tauri workflows do NOT run any of these. `tauri-linux-build.yml:231-271` only runs `objdump -p` / `ldd` static checks.
- Fix: After `cargo tauri build` in each Tauri workflow, install the produced bundle in a sandboxed way and run a `--version` / `--help` smoke test against the bundled sidecar binary. · **Found by**: R14

### S1-CR-101 — `bundle.windows.signCommand` requires undocumented `WIN_SIGN_COMMAND` env var
- Location: `src-tauri/tauri.conf.json:82-84`
- Evidence: `"signCommand": "${WIN_SIGN_COMMAND}"`. If `WIN_SIGN_COMMAND` is unset (the default in CI per `signing-guide.md`), Tauri silently skips signing. The env-var name is undocumented in `signing-guide.md`.
- Fix: Either remove `signCommand` from tauri.conf.json and rely solely on post-build signtool, OR update signing-guide.md to document `WIN_SIGN_COMMAND`. · **Found by**: R15

### S1-CR-102 — `targets: "all"` builds every target on every platform; no per-platform target trimming
- Location: `src-tauri/tauri.conf.json:48`
- Evidence: `"targets": "all"` tells Tauri to build every target the host platform supports: Windows → MSI + NSIS; macOS → DMG + `.app`; Linux → `.deb` + `.rpm` + AppImage.
- Fix: Pin per-platform targets explicitly (e.g. `["deb", "appimage"]` for Linux dev builds). · **Found by**: R15

### S1-CR-103 — macOS entitlements plist has 2 entitlements not documented in signing-guide
- Location: `src-tauri/entitlements.plist:40-47`, `docs/migration/signing-guide.md:196-203`
- Evidence: Plist ships 5 entitlements. Signing-guide documents only 3. Missing: `com.apple.security.cs.allow-unsigned-executable-memory`, `com.apple.security.automation.apple-events`.
- Fix: Add the 2 missing entitlements to signing-guide.md table with the BUILD-N02 rationale. · **Found by**: R15

### S1-CR-104 — Auto-update is not configured (documented decision, but creates poor update UX)
- Location: `docs/auto-update-feature.md:1-11`, `src-tauri/tauri.conf.json:90-106`, `voice_typer/client/electron-builder.yml:7-15`
- Evidence: Auto-update intentionally NOT wired (ADR-0020 §15). But: (1) `electron-builder.yml` still has a `publish: github` block — unconsumed dead config. (2) Users have NO auto-update mechanism. (3) No in-app "Check for updates" button.
- Fix: Remove stale `publish: github` block from `electron-builder.yml`. Optionally add a simple "Check for updates" button that hits GitHub Releases API. · **Found by**: R15

### S1-CR-106 — Wayland hotkey silently fails without `input` group + native binary; no UI remediation
- Location: `voice_typer/server/hotkeys/factory.py:82-101`
- Evidence: On a default Wayland session (Sway/Hyprland/gnome-shell) without native binary built and without manual socket wiring, the hotkey simply does not work. Factory logs but user has no in-app path to discover this.
- Fix: In `WaylandHotkey.start`, after the 30-second pynput-fallback timer fires, emit a `tray.notify` warning guiding the user to install the native binary or wire up a socket client. · **Found by**: R17

### S1-CR-108 — Hardcoded English strings in `useStatsShare.ts` shown in share image
- Location: `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts:36-37,49`
- Evidence: `const modeDisplay = isCloud ? "Cloud" : "Offline"; const modeDetail = isCloud ? "Cloud API" : "Local Model"; fasterThanAvg: \`${fasterPercent}% faster than avg typer\``. Rendered into downloadable PNG. Arabic user sharing stats sees mixed Arabic + English.
- Fix: Replace with i18n keys. · **Found by**: R18

### S1-CR-109 — Hardcoded English in `App.tsx` (clipboard-failure fallback + "Copy path" button)
- Location: `voice_typer/client/src/renderer/src/App.tsx:323, 338`
- Evidence: Fallback message and action button label both hardcoded English.
- Fix: Replace with i18n keys. · **Found by**: R18

### S1-CR-110 — Hardcoded English `" chars"` substring in `History.tsx`
- Location: `voice_typer/client/src/renderer/src/pages/History.tsx:397`
- Evidence: `chars: stats.chars > 0 ? \` (${stats.chars.toLocaleString()} chars)\` : ""`. The literal `" chars"` is concatenated into i18n placeholder. Non-English users see " (1,234 chars)" in their otherwise-translated label.
- Fix: Move to i18n key with plural/count interpolation. · **Found by**: R18

### S1-CR-111 — Locale formatting uses `undefined` (browser default) instead of user-selected app locale
- Location: `components/dashboard/StatCards.tsx:15`, `components/dashboard/ActivityList.tsx:18,20`, `pages/Dashboard.tsx:392,406`, `pages/History.tsx:397`
- Evidence: Five call sites use `toLocaleString()` / `toLocaleDateString()` / `toLocaleTimeString()` with `undefined` (browser default), NOT user-selected app locale. Arabic user still sees dates/numbers in OS locale format.
- Fix: Pass `getLocale()` (e.g., `n.toLocaleString(getLocale())`). · **Found by**: R18

### S1-CR-112 — 4 i18n helper scripts hardcode workspace path
- Location: `scripts/add_prewarm_i18n_keys.py:30`, `scripts/add_prewarm_log_i18n_keys.py:18`, `scripts/add_run_prewarm_i18n_keys.py:20`, `scripts/fix_i18n_remaining.py:5`
- Evidence: `ROOT = Path("/home/z/my-project/voice-typer")`. Will fail when repo is cloned to any other path.
- Fix: Use `Path(__file__).resolve().parent.parent` like the sibling scripts. · **Found by**: R18

### S1-CR-113 — Orphan key `a11y.opensInNewTab` in 6 non-English locales
- Location: `ar.json:999`, `de.json:999`, `fr.json:775`, `hi.json:999`, `ru.json:999`, `zh.json:999`
- Evidence: Exists in 6 non-English locales but NOT in `en.json` and is never referenced in code.
- Fix: Delete from all 6 locale files. · **Found by**: R18

### S1-CR-114 — 2 `xfail(strict=False)` tests with no tracking issue
- Location: `tests/test_shutdown_controller.py:148, 167`
- Evidence: `strict=False` means the test can pass OR fail without breaking CI — placeholder for incomplete work. The reason references "primary agent will add" but provides no GitHub issue, PR number, or timeline.
- Fix: Convert to `strict=True` or add a `# Tracking: #NNN` reference. · **Found by**: R12

### S1-CR-115 — 9+ `pytest.mark.skip` markers "rewritten as vitest" — dead weight
- Location: `tests/test_feature_hardening_regressions.py:1222-1436` (9 skips), `tests/test_consent_and_privacy.py:214-420` (10+ skips)
- Evidence: Skip markers left in place "until the vitest is verified on CI" — but per S1-CR-33, several of those vitest files are themselves currently failing. Neither layer is enforcing the invariant.
- Fix: Delete the skipped Python tests (dead code per their own reason text); fix the vitest rewrites so they actually enforce the invariants. · **Found by**: R12

### S1-CR-116 — pyrefly version unpinned in CI — "hard gate" is non-reproducible
- Location: `.github/workflows/build.yml:146`
- Evidence: `uv pip install --system pyrefly` — no version pin. Locally get pyrefly 1.1.1 reporting 146 errors. CI may install a different version.
- Fix: Pin pyrefly (e.g. `pyrefly==1.1.1`) in both `pyproject.toml [dev]` and `build.yml`. · **Found by**: R12

### S1-CR-117 — Duplicate dep declarations in `pyproject.toml` (main deps vs platform extras)
- Location: `pyproject.toml:115-116, 190-191, 119-120, 195`
- Evidence: `pycaw>=20230407; sys_platform == 'win32'` at line 115 (main) AND line 190 (`[windows]` extra). `comtypes` same. `pyobjc-core`, `pyobjc-framework-CoreAudio` duplicated between main and `[macos]`.
- Fix: Remove redundant entries from `[windows]`/`[macos]` extras; document main deps as source of truth. · **Found by**: R13

### S1-CR-118 — Pre-commit hooks NOT run in CI
- Location: `.pre-commit-config.yaml` (35 lines, 4 repos); `.github/workflows/build.yml` and `.github/workflows/client-ci.yml`
- Evidence: Grep for `pre-commit` in `.github/workflows/` returns 0 matches. CI separately runs ruff, pyrefly, biome, typecheck, branding check. But `pre-commit-hooks` repo's hygiene checks (large-file detection, merge-conflict markers, mixed line endings, trailing whitespace, missing EOF newline) are NOT enforced in CI.
- Fix: Add a `pre-commit run --all-files` step to either `build.yml` or `client-ci.yml`. · **Found by**: R14

### S1-CR-119 — `tauri-build.yml` orchestrator aggregate step silently succeeds when all platforms skip
- Location: `.github/workflows/tauri-build.yml:115-167`
- Evidence: When all 3 per-platform workflows are skipped (current state — all have `if: false`), the `Download Windows/macOS/Linux installer` steps skip silently, and the `Upload aggregated artifact` step uploads an empty `aggregated/` directory without warning.
- Fix: Add explicit `echo "::warning::All per-platform workflows are still gated behind Phase 0 — no installers were produced."` step when all three are skipped. · **Found by**: R14

### S1-CR-120 — Caches present but coverage is uneven across jobs
- Location: All workflows under `.github/workflows/`
- Evidence: cargo registry + `src-tauri/target` cached on Linux + Windows Tauri jobs but **NOT macOS Tauri**. ~5-8 min per job × 3 jobs = ~15-24 min wasted per run.
- Fix: Add the same `actions/cache@v4` step used in `tauri-linux-build.yml:108-117` and `tauri-windows-build.yml:116-125` to `tauri-macos-build.yml`. · **Found by**: R14

### S1-CR-121 — Filename typo: `docs/dublicated-text.md` should be `duplicated-text.md`
- Location: `docs/dublicated-text.md` (filename only)
- Evidence: Filename uses "dublicated" (with 'b') instead of "duplicated" (with 'p'). File content does not contain the misspelling internally.
- Fix: `git mv docs/dublicated-text.md docs/duplicated-text.md`. · **Found by**: R20

### S1-CR-122 — ADR README index is stale (missing ADR-0020; "?" statuses for 4 ADRs that have explicit statuses)
- Location: `docs/adr/README.md:6-27`
- Evidence: (1) Table stops at ADR-0019; ADR-0020 exists on disk but not in index. (2) "Status" column shows `?` for ADRs 0008, 0009, 0010, 0011, 0012. Actual ADR files declare explicit statuses.
- Fix: Add ADR-0020 row; update statuses for 0008/0009/0010/0012 from `?` to actual values. · **Found by**: R20

### S1-CR-123 — README log rotation claim is stale (1MB × 2 vs actual 5 MiB × 5)
- Location: `README.md:470`
- Evidence: README says `Uses RotatingFileHandler (1MB max, 2 backups)`. Actual: `maxBytes=5 * 1024 * 1024, backupCount=5`.
- Fix: Update README to `5 MiB max, 5 backups`. · **Found by**: R20

### S1-CR-124 — `docs/home-directory.md` log path and rotation are both wrong
- Location: `docs/home-directory.md:48, 70, 132`
- Evidence: Tree shows `├── logs/` `│   └── voice-typer.log` with "1 MB × 2 backups". Actual: log file written directly to `<DATA_DIR>/voice-typer.log` (no `logs/` subdir). Rotation is 5 MiB × 5.
- Fix: Drop `logs/` from path; bump rotation numbers; point to `logging_setup.py`. · **Found by**: R20

### S1-CR-125 — PLATFORM_STATUS.md references nonexistent `voice-typer setup` CLI subcommand
- Location: `docs/PLATFORM_STATUS.md:28`
- Evidence: Status table row "Model download (CLI)" says `✅ voice-typer setup`. But `pyproject.toml:157` declares only one entry point: `voice-typer = "voice_typer.server.ipc_server:main"`. There is no `setup` subcommand in `ipc_server.py`.
- Fix: Remove the "Model download (CLI) ✅" row or document the actual command. · **Found by**: R20

### S1-CR-126 — PLATFORM_STATUS.md "Adding a new platform" references stale module paths
- Location: `docs/PLATFORM_STATUS.md:203, 208-209`
- Evidence: References `voice_typer/server/platform.py`, `voice_typer/server/native_hotkeys.get_native_binary_path()`, `voice_typer/server/hotkeys.py`. All are now packages, not flat modules.
- Fix: Update dotted paths to point into actual packages. · **Found by**: R20

### S1-CR-127 — CHANGELOG.md references stale module paths
- Location: `CHANGELOG.md:44-45`
- Evidence: References `voice_typer/server/native_hotkeys.py` and `voice_typer/server/hotkeys.py`. Both are now packages.
- Fix: Annotate with `[now package: native_hotkeys/]` etc. · **Found by**: R20

### S1-CR-128 — FEATURES.md references stale module paths + version
- Location: `FEATURES.md:119, 205`
- Evidence: `Smart duck enabled (v2.2)` — `pyproject.toml:28` declares `version = "1.0.0"`. `prewarm.py + task_scheduler.py` — `prewarm.py` is now `prewarm/` package.
- Fix: Drop `(v2.2)` tag or document; update `prewarm.py` → `prewarm/` package. · **Found by**: R20

### S1-CR-129 — `docs/rw04-recording-decomposition.md` references a file that no longer exists
- Location: `docs/rw04-recording-decomposition.md:11, 31, 60+`
- Evidence: Doc says `voice_typer/server/recording.py was a 3,208-line god class`. Actual: `recording/` is now a package. Status is "In-progress (Wave 1 of 3)" but Wave 2 (AudioBuffer) is actually done.
- Fix: Update status; mark doc as historical and link to current package layout. · **Found by**: R20

### S1-CR-130 — `bench/COLDSTART_REPORT.md` references 3 missing artifacts
- Location: `bench/COLDSTART_REPORT.md:279-281, 169, 184, 285`
- Evidence: Lists `scripts/profile_imports.py` (MISSING), `scripts/coldstart_BEFORE.txt` (MISSING), `scripts/coldstart_AFTER.txt` (MISSING). Also references `voice_typer/server/recording.py` as flat file.
- Fix: Restore missing artifacts or remove the "Artifacts" section. · **Found by**: R20

### S1-CR-131 — `docs/rw9-god-class-decomposition.md` status table is stale
- Location: `docs/rw9-god-class-decomposition.md:15-19`
- Evidence: Status table says `app.py` line count at "Round-6 end" is 2314. Actual: `wc -l voice_typer/server/app.py` = 1179 lines.
- Fix: Add a "Post-round-6 update" note with current line count, or regenerate the table. · **Found by**: R20

### S1-CR-132 — Dev container README claims a `node_modules` named volume that doesn't exist
- Location: `.devcontainer/README.md:74`, `.devcontainer/devcontainer.json`
- Evidence: README says "Docker performance: ... The container uses a named volume for node_modules to mitigate this." But `devcontainer.json` (62 lines) defines **no `mounts` key and no `volumes` key** — there is no named volume. The claim is false.
- Fix: Either add the named volume to `devcontainer.json` or remove the false claim from the README. · **Found by**: R20

### S1-CR-134 — API.md `restart()` method name is stale
- Location: `docs/API.md:26`
- Evidence: Table row says `| restart() | — | None | Spawns a new process and quits the current one. Uses restart token for mutex bypass. |`. But `VoiceTyperApp` has no plain `restart()` method. Actual: `def restart_app(self) -> None:` at `app.py:924`.
- Fix: Rename the table row to `restart_app()`. · **Found by**: R20

### S1-CR-135 — SECURITY.md is silent on the Tauri sidecar WS auth path
- Location: `SECURITY.md` (entire file, 78 lines)
- Evidence: SECURITY.md describes SEC-018 token auth for the TCP path but does not mention the WebSocket bearer-token/HMAC auth added by ADR-0020 for the Tauri sidecar path.
- Fix: Add a "### WS Authentication (ADR-0020, Tauri sidecar)" section to SECURITY.md. · **Found by**: R20

### S1-CR-136 — Crash report lacks reproduction hint and app/system context
- Location: `voice_typer/server/crash_handler.py:521-525, 568-622`
- Evidence: Windows VEH writes `crash_diagnostics.<PID>.txt` containing: BOM, timestamp, exception code, exception address, PID, TID, one-line friendly name. Does NOT include: app version, OS version, last user action / last IPC command, "what to report next" hint.
- Fix: Extend the VEH blurb to include app version + OS version (pre-compute at `set_crash_handler_config_dir` time). Add a "Next steps: run `python scripts/diagnostics.py export`" line to the tray notification. · **Found by**: R20

### S1-CR-137 — IPC command count claims are inconsistent across docs
- Location: `docs/adr/0020-desktop-runtime-migration-analysis.md:32`, `docs/modules/sidecar_ws.md:13,26`, `docs/adr/0019-per-connection-rate-limiter.md:5`, `docs/ARCHITECTURE.md:15,81`
- Evidence: ADR-0020:32 says 69. `sidecar_ws.md:13,26` says 68. ADR-0019:5 says 68. ARCHITECTURE.md:81 says "frozen for v1 at 68". Actual count (verified by grepping `_COMMAND_REGISTRY`): **73 entries**. All three documented numbers are stale.
- Fix: Recount; update all references to the true value (73). · **Found by**: R20


### S1-CR-139 — CONTRIBUTING.md project-structure section references stale modules
- Location: `CONTRIBUTING.md:248-249`
- Evidence: Lists `recording.py` and `hotkeys.py` as flat modules.
- Fix: Update the tree to reflect packages. · **Found by**: R20

### S1-CR-140 — `docs/adr/0011-prewarm-architecture-analysis.md:9` references stale `prewarm.py`
- Evidence: Line 9 says `The prewarm module (voice_typer/server/prewarm.py)`. Actual: `voice_typer/server/prewarm/` is now a package.
- Fix: Update to package. · **Found by**: R20

### S1-CR-141 — `voice_typer/stubs/README.md:21` references stale `server_platform.py`
- Evidence: Table row for `winreg` says `Used by: server/server_platform.py, task_scheduler.py`. `server_platform` is a package.
- Fix: Update to `server/server_platform/` (package). · **Found by**: R20

### S1-CR-142 — `_NativeBackendAdapter` does not propagate `_tray` to the legacy fallback backend
- Location: `voice_typer/server/hotkey_dispatcher.py:87-88`
- Evidence: `_tray` set on adapter only, not on legacy backend. If adapter swaps to legacy backend and legacy backend ever needs to show tray notification, it has no `_tray` attribute.
- Fix: In `_swap_to_legacy`, after `self._legacy = legacy`, also set `self._legacy._tray = self._tray`. · **Found by**: R17

### S1-CR-143 — macOS `VOICE_TYPER_SKIP_ACCESSIBILITY_CHECK=1` silently disables key-up delivery and key suppression
- Location: `voice_typer/server/native/macos-key-listener.swift:462-492`
- Evidence: When env var is set, CGEventTap is never created. No key-up delivery (push-to-talk mode will never fire → recording starts but never stops). No key suppression (Caps Lock as hotkey will toggle OS caps state on every press). No warning logged when active in production.
- Fix: Log a WARNING at binary startup when env var is set; emit `WARN:SKIP_ACCESSIBILITY` line that Python adapter can surface as tray notification. · **Found by**: R17

### S1-CR-144 — `build_tray_menu_model` reads `controller._microphones` via untyped `getattr`
- Location: `voice_typer/server/tray.py:661`
- Evidence: `microphones=getattr(controller, "_microphones", None)`. Relies on `VoiceTyperApp._microphones` being initialized. If renamed, `getattr` silently returns `None` and the Microphones submenu disappears with no error.
- Fix: Add `microphones: list[dict]` to the `TrayController` Protocol, or expose a `controller.get_microphones()` method. · **Found by**: R17

### S1-CR-145 — Linux autostart `.desktop` inconsistent with bundled `.desktop` template
- Location: `voice_typer/server/server_platform/autostart_linux.py:54-62`, `src-tauri/voice-typer.desktop.template:1-10`
- Evidence: Runtime autostart uses `Exec=<python> <launcher.py> --hidden --delay 15`, `Icon=audio-input-microphone`. Bundled app-menu uses `Exec=voice-typer-tauri`, `Icon=voice-typer`. Two inconsistencies.
- Fix: Align both `.desktop` files on same `Icon=voice-typer` and same `Exec=`. · **Found by**: R15

### S1-CR-146 — `StartupWMClass=Voice Typer` may not match Tauri window class
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

### S1-CR-147 — Windows manifest does not declare Windows 11 supportedOS GUID; dpiAwareness missing in standalone manifest
- Location: `scripts/build/voice-typer.manifest:1-22`, `scripts/build/voice-typer.spec:156-186`
- Evidence: Standalone manifest has NO `<dpiAware>` / `<dpiAwareness>` settings. PyInstaller-embedded manifest DOES (`voice-typer.spec:183-184` — `dpiAware=true/pm`, `dpiAwareness=PerMonitorV2`).
- Fix: Either delete the unused standalone `voice-typer.manifest` file, or sync its contents with the PyInstaller-embedded manifest. · **Found by**: R15

### S1-CR-148 — Leftover Electron artifacts in Tauri migration: `electron-builder.yml` + `publish: github`
- Location: `voice_typer/client/electron-builder.yml` (entire file), `voice_typer/server/autostart_launcher.py`, `voice_typer/server/electron_launcher.py`, `voice_typer/server/_electron_build.py`
- Evidence: Per `docs/migration/cutover-playbook.md:8-9`, Electron build path stays intact during mixed-mode period. However: `electron-builder.yml:7-15` has a `publish: github` block — unconsumed per `signing-guide.md:431`. Dead config that could mislead future maintainers.
- Fix: Remove the `publish: github` block from `electron-builder.yml`. Launcher modules should remain until Tauri cutover completes. · **Found by**: R15

### S1-CR-149 — Tauri config has no `bundle.windows.nsis` block; default NSIS install scope may be inconsistent
- Location: `src-tauri/tauri.conf.json:82-84`
- Evidence: `bundle.windows` has only `signCommand`. No `nsis` sub-block to configure install scope. Tauri v2's default NSIS install scope is `perMachine` (requires admin elevation). Legacy Electron NSIS config explicitly sets `perMachine: false` (per-user, no UAC). Tauri build will diverge.
- Fix: Add `bundle.windows.nsis.installerScope: "user"` to match Electron path's per-user install. · **Found by**: R15

### S1-CR-150 — Dead parameter — backward-compat shim in single-caller API
- Location: `voice_typer/client/src/renderer/src/hooks/useConnection.ts:41-47,73`
- Evidence: `UseConnectionArgs` requires `currentPage: Page` (line 44-46), but the hook destructures it as `currentPage: _currentPage` and never reads it. The comment says "kept in the interface for backward compatibility with existing callers (App.tsx)." But `App.tsx:75` is the ONLY caller.
- Fix: Remove the parameter and its argument. · **Found by**: R2

### S1-CR-151 — Dead re-exports in `hooks/useSoundFeedback.ts:30-31`
- Evidence: Re-exports `initAudioContext` and `playSoundCue` from `@/lib/sound-manager`. Comment admits: "No production code currently imports these symbols from here... the re-exports keep the public surface stable for tests and external integrations." No tests import from here either (grep confirms).
- Fix: Remove dead re-exports. · **Found by**: R2

### S1-CR-152 — Stale file extension `hooks/useSnackbar.tsx` (no JSX)
- Evidence: Named `.tsx` but contains no JSX. Comment at lines 39-43 states: "named `.tsx` (not `.ts`) only because of historical extension-priority conventions; it no longer contains JSX."
- Fix: Rename to `.ts`. · **Found by**: R2

### S1-CR-153 — Repeated unsafe casts `window.bubble as import("@/types/ipc").BubbleWindowBubble | undefined` in `Bubble.tsx`
- Location: `renderer/src/Bubble.tsx:205,245,268,342,399`
- Evidence: Same inline cast 5 times.
- Fix: Single `const api = window.bubble as BubbleWindowBubble | undefined` at top of component. · **Found by**: R2

### S1-CR-154 — Beta build dependency: `electron-vite` pinned to `6.0.0-beta.1`
- Location: `voice_typer/client/package.json:55`
- Evidence: Beta version. Risk: breaking changes between beta and stable release, missing security patches, supply-chain uncertainty.
- Fix: Track to a stable release or document rationale for the beta pin. · **Found by**: R2

### S1-CR-155 — Stale compat re-export `main/index.ts:306-310`
- Evidence: `export { APP_NAME } from "./branding";` with comment "APP_NAME is re-exported here to preserve the original lazy-import behaviour (the original `index.ts` imported it at line 1828, just before `app.whenReady()`)." The original monolithic index.ts (2321 LOC, pre-split) no longer exists.
- Fix: Remove re-export; consumers (`./python/start-python.ts` and `./bootstrap.ts`) can import `APP_NAME` directly from `./branding`. · **Found by**: R2

### S1-CR-156 — Incomplete store adoption — prop drilling persists in `App.tsx:327-330`
- Evidence: `App.tsx` destructures `recordingState`, `lastError` from `useConnection` and passes them as props to `<Home>`. But `useConnection` writes these values to `appStore` (lines 86-88), and `appStore.ts:4-8` states the store exists so "any component can subscribe to connectionStatus / recordingState / lastError without prop drilling through App.tsx."
- Fix: Have `Home.tsx` read `recordingState` and `lastError` directly from `useAppStore`. · **Found by**: R2

---

## LOW severity findings (47 unique)

These are documented for completeness but may be deferred with a `Won't Fix` rationale if time-constrained. Each is captured in the appendix of the full review file. Highlights:

- R1-LOW: Keyring_status probe block duplication (`service.py:252-269` and `:282-294`)
- R1-LOW: ARCHITECTURE.md drift
- R2-LOW: Various dead code, dead re-exports, prop drilling
- R3-LOW: server_started uses 'event' key vs 'type' (S1-CR-78 captures this)
- R3-LOW: WS reader treats any id field as dispatch response
- R3-LOW: Shutdown response frame emitted as spurious Tauri event
- R3-LOW: FT-1 respawn flag not panic-safe
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
- R8-LOW: FT-1 user-facing events without attempt count or backoff timing
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

---

## Sub-agent coverage notes

- **R16 (Audio pipeline)**: Timed out / context limit. Slice was partially covered by R3 (IPC/Rust), R4 (perf — verified RT-safe callback design, bounded queues, cached snapshots), R5 (memory — verified audio buffers bounded), R8 (errors — R8-1/R8-2 cover model integrity).
- **R19 (Cross-platform)**: Failed — context deadline exceeded. Slice was partially covered by R3 (Tauri host), R13 (deps — macOS deps missing), R14 (CI/CD — per-arch configs), R15 (packaging — Linux postinst/prerm), R17 (hotkey — Wayland path).
- All other 18 sub-agents returned comprehensive findings with coverage statements.

---

## Bottom line

The codebase has substantial prior engineering investment (RT-safe audio, secure atomic writes, keyring credential store, defense-in-depth security, comprehensive ADRs). However, the Tauri cutover migration (ADR-0020) is **half-finished** in multiple critical dimensions:

1. **Tauri host-side integration broken end-to-end**: tray click handler (S1-CR-5), FT-1 zombie leak (S1-CR-3), dispatch allowlist regression (S1-CR-4), Tauri config schema mismatch (S1-CR-12), missing per-arch configs (S1-CR-13), missing bundle resources (S1-CR-15).
2. **Linux packaging broken under Tauri**: postinst/prerm hard-code legacy paths (S1-CR-39, S1-CR-41), no user-data cleanup on uninstall (S1-CR-43).
3. **CI/CD quality gates are red**: ruff baseline out of sync (S1-CR-28), pyrefly baseline empty (S1-CR-30), 17 failing config tests (S1-CR-31), ~45 failing vitest tests (S1-CR-33), 14 failing i18n tests (S1-CR-49), 30 failing tray tests (S1-CR-7).
4. **Privacy/consent regressions**: HuggingFace consent bypass (S1-CR-8), secret leak in diagnostics script (S1-CR-9), raw transcription in support bundle (S1-CR-23), GDPR delete/export incomplete (S1-CR-87, S1-CR-88).
5. **Silent data-loss bug**: clipboard restore never runs (S1-CR-2) — every paste clobbers the user's clipboard.
6. **Security supply-chain gap**: model integrity check bypassed on cache-hit (S1-CR-10, S1-CR-19), native binaries have no checksum (S1-CR-46).
7. **Codebase debt**: duplicate IPCServer class (S1-CR-1), service.py mixed-concern god file (S1-CR-18), test baseline drift (S1-CR-28, S1-CR-30), stale docs (S1-CR-52 through S1-CR-141).

The Critical/High findings (36 total) are the priority fix wave. Medium findings (105 total) are second-wave. Low findings are polish.

---

## Backend / Architecture

# CRITICAL findings (must fix this run)

### S2-CR-1 — Two parallel IPCServer implementations (~2900 LOC of dead duplicate code)
- **Severity**: Critical
- **Status**: Pending
- **Category**: Overall architecture / Spaghetti / monolith detection
- **Location**: `voice_typer/server/ipc_server.py` (2609 LOC) + `voice_typer/server/ipc/server.py` (1764 LOC) + 6 leaf modules in `voice_typer/server/ipc/`
- **Evidence**: Both files define `_validate_dict_payload`, `_pick_available_port`, `_TCPLineIO`, `_RateLimiter`, `_sanitize_config_for_ipc`, `_bound_history_limit`, `_push_event_now`, `_set_process_metadata`, the `IPCServer` class body, and `main()`. `providers.build_ipc_server` imports from `ipc_server.py` (the god-module). The `ipc/` package's `__init__.py` docstring claims "the thin `ipc_server.py` shim re-exports the same names from this package" — but `ipc_server.py` does NOT re-export, it duplicates.
- **Root cause**: ARCH-045 / "Phase 4.5 split" was started but never finished. The compat shim was never written; the original file kept growing.
- **Impact**: ~2900 LOC of duplicated security-critical code (auth handshake, rate limiter, config sanitization). Bug fixes in one copy may not propagate to the other. New contributors can't tell which copy is canonical.
- **Proposed fix**: Convert `ipc_server.py` into a true thin shim (`from voice_typer.server.ipc import *` + explicit re-exports for non-`__all__` names). Delete the duplicated class body and helpers. Target ≤50 LOC. Verify all 47 test files that import from `voice_typer.server.ipc_server` still pass.
- **Confidence**: High
- **Source**: R1, R8, R9

### S2-CR-2 — `service.py` (2364 LOC, 60+ methods) is a god-class facade
- **Severity**: Critical
- **Status**: Pending
- **Category**: Overall architecture / Spaghetti / monolith detection
- **Location**: `voice_typer/server/service.py:85-2364`
- **Evidence**: Single `VoiceTyperService` class with 60+ public methods spanning 12+ domains (download, status, dictation, config, history, microphones, lifecycle, templates, vocabulary, onboarding, model import/export, diagnostics). Reaches into 8+ private attributes of `self._app` despite docstring claiming "thin facade".
- **Root cause**: ARCH-005 created the service as a "thin facade" but every new IPC handler added a service method. No per-domain service boundary.
- **Impact**: Adding any new IPC command requires editing 3 places. The ServiceProtocol in providers.py enumerates 60+ methods that any fake service must stub. `apply_config` alone is 110 lines interleaving 6 concerns.
- **Proposed fix**: Split into per-domain services: `ConfigService`, `HistoryService`, `MicrophoneService`, `ModelService`, `VocabularyService`, `TemplatesService`, `OnboardingService`, `AudioService`, `LifecycleService`. Each owns its domain's state and is injected into IPCServer. Start with `OnboardingService` (12 methods + 200 LOC, no cross-domain coupling) and `LifecycleService` (restart/quit/export_diagnostics) as proofs of concept.
- **Confidence**: High
- **Source**: R1, R9

### S2-CR-3 — `recorder.py` (2992 LOC) god-class with 432-LOC `_process_audio_chunk`
- **Severity**: Critical
- **Status**: Pending
- **Category**: Spaghetti / monolith detection / Audio pipeline quality
- **Location**: `voice_typer/server/recording/recorder.py:228-2992`
- **Evidence**: Single `Recorder` class with ~40 methods packing 8+ concerns (VAD state machine, VAD auto-calibration, device list caching, hot-plug disconnect handling, device health checker thread, audio callback → worker thread architecture, IPC event worker thread, mono conversion). `_process_audio_chunk` is **432 LOC** doing chunk normalization, VAD probability, VAD state update, RMS/peak metering, XRUN detection, ring-buffer push, event publication, silence detection, resample dispatch.
- **Root cause**: The original `recording.py` was extracted into a `recording/` package but the `Recorder` class itself was never decomposed — only leaf helpers were pulled out.
- **Impact**: Real-time audio bugs are hard to localize because device logic, VAD logic, and worker logic all share `self` state. Test patches target 6+ cross-submodule helpers requiring fragile patch-path bridges.
- **Proposed fix**: Decompose `Recorder` into collaborators: `DeviceResolver`, `DeviceHealthMonitor`, `VadController`, `AudioWorker`, `EventWorker`, `ChunkProcessor`. Thin `Recorder` holds them and exposes `start`/`stop`/`snapshot`/`discard`.
- **Confidence**: High
- **Source**: R9, R20

### S2-CR-4 — StatCards labels render as raw key paths for en/es users
- **Severity**: Critical
- **Status**: Pending
- **Category**: UX/UI consistency / Localization / i18n
- **Location**: `voice_typer/client/src/renderer/src/components/dashboard/StatCards.tsx:42,48,54` + `i18n/translations/en.json` (missing `dashboard` namespace) + `es.json`
- **Evidence**: StatCards.tsx looks up `dashboard.cards.dictations`, `dashboard.cards.chars`, `dashboard.cards.duration`. `grep -l dashboard i18n/translations/*.json` returns ONLY ar/de/fr/hi/ru/zh — en.json and es.json have NO `dashboard` namespace. The i18n `t()` function falls back to the raw key string.
- **Root cause**: `dashboard.cards.*` keys were added to 6 of 8 locale files but never added to en.json (the source-of-truth locale) or es.json.
- **Impact**: On the Home page (default landing page), the three stat-card labels render as literal strings "dashboard.cards.dictations", "dashboard.cards.chars", "dashboard.cards.duration" for English (default) and Spanish users.
- **Proposed fix**: Add `"dashboard": { "cards": { "dictations": "Dictations", "chars": "Characters", "duration": "Duration" } }` to en.json and the same sub-tree (translated) to es.json. Run `python -m pytest tests/test_i18n_completeness.py -q` to confirm the parity gate passes.
- **Confidence**: High
- **Source**: R4

### S2-CR-5 — Onboarding welcome list lies about the wizard steps
- **Severity**: Critical
- **Status**: Pending
- **Category**: Discoverability / User onboarding
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:271-284` (welcome list) vs `Onboarding.tsx:288-420` (actual wizard steps) + `i18n/translations/en.json:1106-1110`
- **Evidence**: Welcome screen renders 3 numbered promises: (1) mic, (2) grant keyboard permission, (3) hotkey. Actual wizard: Step 1 mic, Step 2 hotkey, Step 3 model, Step 4 complete. The "Grant keyboard-monitoring permission" step is referenced in the welcome list but never shown. The i18n keys `onboarding.permissionsTitle`/`Description`/`Loading`/`Needed`/`Ok`/`NoneNeeded`/`TestLabel`/`TestSuccess`/`TestFailure`/`TestButton` and `onboarding.step4Item`/`step5Item` exist but are NEVER referenced in Onboarding.tsx.
- **Root cause**: Earlier iteration of the wizard had a "Keyboard Monitoring Permission" step (step 2) that was removed without updating the welcome screen's numbered list or pruning the orphaned i18n keys.
- **Impact**: Users are told the wizard will guide them through "Microphone → Permission → Hotkey" but actually experience "Microphone → Hotkey → Model → Complete". They reach step 2 expecting a permissions prompt and see a hotkey picker instead — confusing and erodes trust.
- **Proposed fix**: Update the welcome list to reflect the actual wizard steps: render all 5 items, ensure the wizard actually performs all 5 steps, prune orphaned `permissions*` keys (or restore the Permissions step on macOS/Linux where it's required).
- **Confidence**: High
- **Source**: R4, R6

### S2-CR-6 — Onboarding skips the Permissions step entirely (macOS/Linux users never grant OS keyboard permission)
- **Severity**: Critical
- **Status**: Pending
- **Category**: User onboarding / Cross-platform compatibility
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx` (entire file) + `voice_typer/server/handlers/onboarding_handlers.py:235-257`
- **Evidence**: Backend `OnboardingController` defines 6 steps: Welcome(0), Microphone(1), Permissions(2), Hotkey(3), Model(4), Done(5). IPC handler `_handle_onboarding_check_permissions` exists. BUT: frontend Onboarding.tsx only renders content for `step.step === 0..4`. There is no `step.step === 2 && <Permissions>` branch. Grep for `onboarding_check_permissions` in `voice_typer/client/` returns ZERO matches.
- **Root cause**: The Permissions step (UX-4 / UX-27) was implemented on the backend but never wired into the frontend renderer.
- **Impact**: macOS users complete the wizard without ever seeing Accessibility-permission instructions; Linux users never see the `input` group + udev-rule walkthrough. They press their hotkey, nothing happens (native key-listener can't fire without OS permission), and there is no in-app explanation. ADR 0008 §1 explicitly identifies this as a known High-severity gap.
- **Proposed fix**: Add a `step.step === 2` branch in Onboarding.tsx that calls `call("onboarding_check_permissions")` on mount, renders `permissionsTitle`/`Description`, and — when `data.needed === true` — shows the platform-specific instructions list. Add a "Test hotkey" button. Add a regression test.
- **Confidence**: High
- **Source**: R6

### S2-CR-7 — Onboarding step indices are off-by-one from server step_names
- **Severity**: Critical
- **Status**: Pending
- **Category**: User onboarding
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:136-169` (handleNext), `320-392` (step content), `248` (progress bar)
- **Evidence**: handleNext branches on `step?.step`: step 1→set_microphone, step 2→set_hotkey, step 3→set_model, step 4→apply. But server step_names are 1=Mic, 2=Permissions, 3=Hotkey, 4=Model, 5=Done. At frontend-step 2 (rendering Hotkey UI, calling set_hotkey), the server believes the user is on Permissions — the progress bar shows "Permissions" while the user is choosing a hotkey. At frontend-step 4 (rendering Summary, calling apply), the server is on "Model" and the user never sees "Done".
- **Root cause**: Frontend step indices were never updated when the backend added the Permissions step (UX-4 / UX-27 bump from 5→6).
- **Impact**: Progress bar shows wrong label for 3 of 5 visible steps. User clicks "Get Started" while server believes they're on the Model step.
- **Proposed fix**: Either (a) reindex the frontend: Hotkey UI at step 3, Model UI at step 4, Summary UI at step 5, handleNext apply at step 5; OR (b) gate the handleNext branches on `step.step_name` instead of `step.step`. Option (b) is more robust.
- **Confidence**: High
- **Source**: R6

### S2-CR-8 — Onboarding never asks for `voice_biometric_consent` → recording is refused on first hotkey press
- **Severity**: Critical
- **Status**: Pending
- **Category**: User onboarding / Privacy & data protection
- **Location**: `voice_typer/server/config.py:703-707` (default False) + `voice_typer/server/recording_controller.py:210-225` (refusal) + `voice_typer/client/src/renderer/src/pages/Onboarding.tsx` (no consent step) + `docs/adr/0016-granular-consent-flags.md:65`
- **Evidence**: ADR 0016 §"PRIV-009" explicitly specifies "UI location: First-run onboarding → 'I consent to on-device voice processing for transcription'". But Onboarding.tsx has no consent step. `grep "voice_biometric_consent" voice_typer/server/onboarding.py voice_typer/server/handlers/onboarding_handlers.py` returns no matches. Config default is `voice_biometric_consent: bool = False`. When user completes onboarding and presses hotkey, `recording_controller.start()` checks the flag and refuses.
- **Root cause**: ADR's design was implemented in the backend (refusal logic) but the corresponding onboarding UI was never built.
- **Impact**: Every first-run user who completes onboarding is immediately blocked from dictating. The error is shown only as a tray notification (no in-app dialog), and the user has to discover Settings → Privacy → toggle "Voice biometric processing" on their own. This is the single biggest drop-off point in the first-run funnel.
- **Proposed fix**: Add a Consent step to the wizard (between Permissions and Hotkey). Step should: (1) show `voiceBiometricsDesc` text, (2) require user to toggle a checkbox per ADR 0016, (3) call `set_config({voice_biometric_consent: true, huggingface_consent: true})` when accepted. Block "Continue" until consent is given.
- **Confidence**: High
- **Source**: R6

### S2-CR-9 — README falsely claims terminal auto-paste detection was removed
- **Severity**: Critical
- **Status**: Pending
- **Category**: Documentation
- **Location**: `README.md:333` (Auto-Paste Behavior section)
- **Evidence**: README says "terminal-specific detection (Shift+Insert for Windows Terminal / Warp / Alacritty) was removed because the Win32 focus-detection API can't reliably distinguish terminal emulators from other text fields." But `voice_typer/server/clipboard.py:151-172` defines `_TERMINAL_PROCESS_NAMES` (18 entries: windowsterminal.exe, warp.exe, alacritty.exe, wezterm-gui.exe, conemu64.exe, cmd.exe, powershell.exe, pwsh.exe, gnome-terminal, konsole, xfce4-terminal, alacritty, kitty, xterm, rxvt, tilix, terminator, foot, wezterm). `clipboard.py:1153-1180` calls `_is_terminal_process(process_name)` and routes terminal targets to `_safe_key_press(_Key.shift, _Key.insert)`.
- **Root cause**: README was not updated when terminal detection was added back (or never updated when the feature was implemented despite the docs saying it was removed).
- **Impact**: Users pasting into terminals follow the README's troubleshooting tip ("press Ctrl+Shift+V") when the app actually auto-detects 18 terminals and sends Shift+Insert automatically. Misleading troubleshooting guidance.
- **Proposed fix**: Rewrite README § Auto-Paste Behavior: "The app detects 18 known terminal process names (Windows Terminal, Warp, Alacritty, WezTerm, ConEmu, cmd, PowerShell, gnome-terminal, konsole, kitty, xterm, etc.) and sends Shift+Insert instead of Ctrl+V for those targets."
- **Confidence**: High
- **Source**: R7

### S2-CR-10 — SECURITY.md allowlist count is stale (will fail CI)
- **Severity**: Critical
- **Status**: Pending
- **Category**: Documentation / CI/CD
- **Location**: `SECURITY.md:37` (Command Allowlist section)
- **Evidence**: SECURITY.md says "only the **69** commands listed in `ALLOWED_COMMANDS`". Actual count is **70**. The `tests/test_security_doc_command_count.py` test parses SECURITY.md for the documented count and asserts it equals the source count — this test WILL FAIL on the next CI run.
- **Root cause**: SECURITY.md was not updated when commands were added.
- **Impact**: CI test fails, blocking PRs.
- **Proposed fix**: Change "69" to "70" in SECURITY.md line 37. Verify `tests/test_security_doc_command_count.py` passes.
- **Confidence**: High
- **Source**: R7

### S2-CR-11 — CONTRIBUTING.md lists 6 ADR filenames that don't exist
- **Severity**: Critical
- **Status**: Pending
- **Category**: Documentation
- **Location**: `CONTRIBUTING.md:289-294` (Project Structure → docs/adr/ listing)
- **Evidence**: Lists 6 ADR filenames that do not exist: `0001-adr-process.md` (actual: `0000-adr-process.md` + `0001-record-architecture-decisions.md`), `0002-electron-python-architecture.md` (actual: `0003-...`), `0002-ipc-protocol.md` (actual: `0004-...`), `0004-clipboard-security.md` (actual: `0006-...`), `0005-native-hotkey-architecture.md` (actual: `0007-...`), `0007-audio-filter-chain-architecture.md` (actual: `0009-...`).
- **Root cause**: ADRs were renumbered but CONTRIBUTING.md was not updated.
- **Impact**: New contributors following CONTRIBUTING.md's project-structure tree hit 404s when clicking the ADR links.
- **Proposed fix**: Replace the 6 stale filenames with the correct ones from `docs/adr/README.md` index. Better: replace the hardcoded list with a pointer to `docs/adr/README.md`.
- **Confidence**: High
- **Source**: R7

### S2-CR-12 — Tauri v2 build jobs are all `if: false` (zero CI verification of migration target)
- **Severity**: Critical
- **Status**: Pending
- **Category**: CI/CD
- **Location**: `.github/workflows/tauri-windows-build.yml:80`; `tauri-macos-build.yml:50,121,183`; `tauri-linux-build.yml:62`
- **Evidence**: All five per-platform Tauri v2 build jobs are explicitly stubbed with `if: false`. The entire ADR-0020 migration target has NEVER executed in CI.
- **Root cause**: Tauri migration is "Phase 0 not yet started" — jobs were scaffolded but disabled.
- **Impact**: No CI verification that the Tauri migration target even builds, signs, or produces a valid installer. When Phase 0 cutover is attempted, the first `if: true` flip will likely surface dozens of latent bugs.
- **Proposed fix**: Add a weekly scheduled run that flips ONE per-platform job to `if: true` behind `workflow_dispatch` only (no push/PR) so each platform's Phase 0 spike is exercised on demand.
- **Confidence**: High
- **Source**: R19

### S2-CR-13 — `requirements-lock.txt` is missing `keyring` and `websockets` (breaks hash-pinned installs)
- **Severity**: Critical
- **Status**: Pending
- **Category**: Dependency & supply-chain health
- **Location**: `requirements-lock.txt` (vs `pyproject.toml:137,146`)
- **Evidence**: pyproject.toml declares `websockets>=12.0,<14.0` and `keyring>=25.0,<26.0` as core runtime deps. `grep -E '^(websockets|keyring)' requirements-lock.txt` returns 0 matches. Lockfile header claims "Last regenerated: 2026-07-12 (Task 8: …add huggingface_hub, pyrnnoise, pyobjc-framework-Cocoa to align with pyproject.toml)" — Task 8 added 3 of the 5 missing deps and silently skipped these two.
- **Root cause**: pip-compile regen was incomplete.
- **Impact**: `pip install --require-hashes -r requirements-lock.txt` (the documented reproducible-build command) installs a Python env where (a) `import websockets` in `sidecar_ws.py:507` raises ImportError → Tauri sidecar transport silently broken; (b) `import keyring` in `credential_store.py:206` falls into its `except Exception` branch → keyring unavailable → credential_store silently degrades to PLAINTEXT API-key storage in `config.json` (security regression).
- **Proposed fix**: Run `uv pip compile --generate-hashes --python-version 3.12 -c requirements-lock.txt pyproject.toml` and append `keyring==…` and `websockets==…` entries with hashes. Verify with `pip install --require-hashes -r requirements-lock.txt` in a clean venv.
- **Confidence**: High
- **Source**: R18

### S2-CR-15 — Double-resampling corrupts audio when AudioProcessor is wired (non-16kHz mics)
- **Severity**: Critical
- **Status**: Pending
- **Category**: Audio pipeline quality
- **Location**: `voice_typer/server/recording/recorder.py:2229 + 2311 + 2585-2618 + 2708-2797 + 2395-2413`
- **Evidence**: In `_process_audio_chunk` (line 2229): `filtered = self._audio_processor.process_chunk(indata_mono.copy(), input_sample_rate=self._effective_sr)`. `audio_processor.process_chunk` RESAMPLES the chunk from input_sample_rate (e.g. 48000) to `self._sample_rate` (16000) BEFORE filtering. So `filtered` is at 16000 Hz. But `stop()`, `snapshot()`, and the VAD path use `effective_sr` (48000) as the source sample rate for resampling — corrupting the audio (16kHz→"16 from 48" = garbage at 5.33kHz).
- **Root cause**: Two sub-agents designed conflicting resampling contracts. CRIT-6 (audio_processor.py:139-148 comment) explicitly states the processor "resample[s] to the chain's construction rate (16 kHz) before filtering" — but the recorder's stop/snapshot/vad path assumes the buffer holds native-rate audio.
- **Impact**: For any user whose mic's native sample rate is not 16kHz (the vast majority — USB mics, Bluetooth headsets, most built-in mics), the final transcription audio fed to Whisper is 1/3 the length and at ~5.33kHz actual sample rate (flagged as 16kHz). Transcription produces garbage. Tests don't catch this because tests mock `sd.query_devices` to return empty (effective_sr falls through to 16000) or construct Recorder without `audio_processor=...`.
- **Proposed fix**: Track a separate `_post_filter_sr` that's 16kHz when audio_processor is wired and effective_sr when it isn't. Pass THAT rate to the VAD resample decision and chunk_duration calculation. Add a regression test that constructs Recorder with a real AudioProcessor AND a FakeInputStream delivering 48kHz audio, asserting the stop()-returned array has the correct length.
- **Confidence**: High
- **Source**: R20

---

# HIGH findings (must fix this run)

### S2-CR-16 — `clipboard.py` `_delayed_restore` arg-count mismatch causes silent thread crash on every paste
- **Severity**: High
- **Status**: Pending
- **Category**: Memory usage / Error handling
- **Location**: `voice_typer/server/clipboard.py:1018-1023` (call site, 4 args) + `:1215-1220` (function signature, 3 args after self) + `:58` (`_pending_restores` module global)
- **Evidence**: Thread target is called as `self._delayed_restore(snapshot, expected, delay, _pending_entry)` (5 positional args), but the function accepts only 4. Python raises TypeError inside the daemon thread immediately on start. The function body never runs. `_pending_restores` list grows by one entry per paste, never removed.
- **Root cause**: Verified — signature/call mismatch.
- **Impact**: On EVERY paste(): (1) daemon thread crashes with TypeError; (2) ClipboardSnapshot is NEVER restored — user's original clipboard content is replaced by transcription text and stays that way until process exit; (3) `_pending_restores` list grows unbounded — each entry holds a ClipboardSnapshot that may reference large clipboard data.
- **Proposed fix**: Add a 4th parameter `_pending_entry` to `_delayed_restore` and add `_pending_restores.remove(_pending_entry)` in a `finally` block. Update docstring.
- **Confidence**: High
- **Source**: R2

### S2-CR-17 — Electron `setTimeout` leak in send-to-python.ts (timers never cleared on success)
- **Severity**: High
- **Status**: Pending
- **Category**: Memory usage / Performance
- **Location**: `voice_typer/client/src/main/python/send-to-python.ts:61-67`
- **Evidence**: Each IPC call sets a 120s timeout. The setTimeout return value is discarded. When response arrives (handle-message.ts), `state.pendingRequests.delete(id)` runs but the 120s timer is still scheduled. The timer's closure captures state, msg, id, reject.
- **Root cause**: Verified — timer is never cleared on success path.
- **Impact**: Steady-state memory growth proportional to IPC call rate. Each leaked timer holds a closure referencing the original `msg` object (large for save_vocabulary/template/config). Over multi-hour sessions, accumulates hundreds of MB.
- **Proposed fix**: Capture timer handle and `clearTimeout` in both success and reject paths.
- **Confidence**: High
- **Source**: R2

### S2-CR-18 — `ModelLoad` thread not registered with `thread_registry` (ungraceful shutdown during model load)
- **Severity**: High
- **Status**: Pending
- **Category**: Performance (shutdown) / Reliability
- **Location**: `voice_typer/server/model_manager.py:329-338`
- **Evidence**: `start_background_load` spawns `threading.Thread(target=self.load_background, name="ModelLoad", daemon=True)` but never registers it with `app._thread_registry`. `shutdown_controller._do_cleanup()` has no join for `_model_load_thread`.
- **Root cause**: Verified — daemon=True but not registered.
- **Impact**: If user quits during model load (30-45s on cold start), thread keeps running until OS kills process. torch/CUDA resources not cleaned up gracefully.
- **Proposed fix**: Register with `app._thread_registry` in `start_background_load()`. Add a stop_event check inside `load_background` so load can be aborted mid-stream.
- **Confidence**: High
- **Source**: R2

### S2-CR-19 — Per-sample Python for-loops in 4 dynamics audio filters (3-8% CPU drain during every dictation)
- **Severity**: High
- **Status**: Pending
- **Category**: CPU usage / Resource footprint
- **Location**: `voice_typer/server/audio_filters/equalizer.py:78`, `compressor.py:74`, `limiter.py:64`, `noise_gate.py:100`
- **Evidence**: All four filters use `for i in range(n):` per-sample loops with float ops. At 16 kHz/512 samples × 16 chunks/sec × 4 filters = 32,768 Python iterations/sec, each doing float()/abs()/conditional/math.log10/exp.
- **Root cause**: OBS-ported algorithms translated verbatim to Python.
- **Impact**: Sustained 3-8% single-core CPU during every dictation AND every mic-test session. Battery drain on laptops. Can cause audio worker to fall behind on low-end CPUs, triggering ring-buffer drops.
- **Proposed fix**: Vectorize the envelope follower using `np.maximum.accumulate` for attack path + precomputed exponential-decay kernel for release path. Or port to Cython/cffi. Or gate all four behind a single "dynamics" config flag so users who only want RNNoise don't pay for them.
- **Confidence**: High
- **Source**: R3

### S2-CR-20 — VAD resamples already-resampled audio (double resample)
- **Severity**: High
- **Status**: Pending
- **Category**: CPU usage / Audio pipeline quality
- **Location**: `voice_typer/server/recording/recorder.py:2395-2414` and `:2311`
- **Evidence**: When device native rate ≠ 16kHz and audio_processor is active (default config), every chunk is resampled TWICE: step 1 (audio_processor) 48k→16k; step 2 (VAD) resamples `filtered` (already 16k) using `self._effective_sr` (48000) → 57 samples + 455 zeros instead of 170 real + 342 zeros. `chunk_duration = len(filtered) / self._effective_sr` computes 3.54ms instead of 10.6ms.
- **Root cause**: VAD code uses `self._effective_sr` (device native rate) to decide whether to resample, but `filtered` has already been resampled to 16kHz.
- **Impact**: 0.5-2ms × 16 chunks/sec = 8-32ms/sec wasted CPU. VAD accuracy degraded (model sees 1/3 of real audio → false silence detection).
- **Proposed fix**: Track the actual rate of `filtered` explicitly. After `process_chunk` returns, rate is `self._audio_processor._sample_rate` when processor active, or `self._effective_sr` when None. Use THAT rate.
- **Confidence**: High
- **Source**: R3, R20

### S2-CR-21 — Linux `pactl` subprocess polling at 500ms = 10% CPU drain during every dictation
- **Severity**: High
- **Status**: Pending
- **Category**: CPU usage / Resource footprint / Cross-platform
- **Location**: `voice_typer/server/volume_backends.py:933` (pactl subprocess) + `volume_ducker.py:586` (500ms poll loop)
- **Evidence**: Linux smart-duck monitor polls `is_speaker_active()` every 500ms. Each poll forks+execs `pactl list sink-inputs`, reads stdout pipe, parses output. Measured cost ~30-80ms per call. `LinuxVolumeBackend` does NOT override `recommended_poll_interval_ms` (defaults to 500ms like Windows COM path which is <1µs).
- **Root cause**: `recommended_poll_interval_ms` was added to VolumeBackend base but only MacVolumeBackend implements it.
- **Impact**: 10% single-core CPU drain during every dictation on Linux (silent-room case). Significant battery impact. Prevents CPU from entering deep C-states.
- **Proposed fix**: Override `recommended_poll_interval_ms` on `LinuxVolumeBackend` to 2000ms when tool is pactl/wpctl. Better: use in-process libpulse via ctypes for `is_speaker_active()`. Or read `/proc/asound` for ALSA-level activity (already implemented in `_alsa_is_playing`).
- **Confidence**: High
- **Source**: R3

### S2-CR-22 — Service-level imports private IPC helper (upward dependency)
- **Severity**: High
- **Status**: Pending
- **Category**: Overall architecture
- **Location**: `voice_typer/server/service.py:252, 280` (`from voice_typer.server.ipc_server import _sanitize_config_for_ipc`)
- **Evidence**: Service layer documented as sitting BELOW IPC layer, but it imports a private helper from IPC layer. The same helper exists in `voice_typer/server/ipc/history_bounds.py` (the canonical extracted location).
- **Root cause**: Sanitizer was extracted to `ipc/history_bounds.py` during Phase 4.5 split, but `service.py` was never updated.
- **Impact**: If `ipc_server.py` is ever truly reduced to a shim, service breaks silently. Upward coupling means service cannot be unit-tested without importing entire IPC server module (transitively: all 14 handler mixins, torch-via-lazy-import, etc.).
- **Proposed fix**: Move `_sanitize_config_for_ipc` (and its `_SECRET_CONFIG_FIELDS`/`_REDACTED_SENTINEL` constants) to a truly neutral location — `voice_typer/server/config.py` or new `config_sanitizer.py`. Update both `service.py` and `ipc_server.py` to import from new location.
- **Confidence**: High
- **Source**: R1

### S2-CR-23 — Per-handler error envelopes leak `str(exc)` to renderer (IPC-5 contract violation)
- **Severity**: High
- **Status**: Pending
- **Category**: Security / Error handling / Observability
- **Location**: `voice_typer/server/handlers/*.py` — 14 files, 66 occurrences
- **Evidence**: Every domain handler ends with `except Exception as e: resp["data"] = {"message": str(e)}`. Compare to dispatch wrapper at `ipc_server.py:1464-1473` which deliberately emits `{"code": "internal_error", "message": "internal error"}` "to avoid exposing server internals over IPC". The 66 handler paths leak str(exc) directly.
- **Root cause**: Handler-level try/except pre-empts the dispatch wrapper by returning (instead of raising) on every exception.
- **Impact**: Information leak: a `set_config` PermissionError surfaces as "Permission denied: '/home/user/.config/voice-typer/config.json'" to renderer; a transcription-engine failure surfaces file paths + CUDA version strings + internal module names. IPC-5 contract violation: `tests/test_ipc5_error_envelope_parity.py` only asserts 3 error classes (invalid_json, rate_limited, internal_error).
- **Proposed fix**: Replace `resp["data"] = {"message": str(e)}` with `resp["data"] = {"code": "internal_error", "message": "internal error"}` in all 66 sites. Or introduce a `_handler_error(resp, e, code="internal_error")` helper. Full str(e) should only go to log, never wire.
- **Confidence**: High
- **Source**: R1, R12

### S2-CR-24 — `app.py` (1179 LOC) is NOT slim — still mixes wiring with business logic
- **Severity**: High
- **Status**: Pending
- **Category**: Spaghetti / monolith detection
- **Location**: `voice_typer/server/app.py:123-1143` (VoiceTyperApp class, 1020 LOC)
- **Evidence**: Prior review claimed `app.py` was slimmed, but it's still 1179 LOC. `VoiceTyperApp` class contains: wiring/lifecycle (start, _do_startup, quit_app, restart_app at 145 LOC, _do_cleanup, quit, atexit handlers, signal handlers, win32 console handler) AND inline business logic that should be delegated: `repaste_last` (74 LOC), `undo_last` (37 LOC), `_open_config_file` (106 LOC of platform branches), `change_model`, `change_microphone`.
- **Root cause**: RW-9 Phase 6 settings_controller extraction was applied to ~4 methods but stopped there. Remaining inline methods accreted before/after the partial extraction.
- **Impact**: `restart_app` is a 145-LOC method mixing 6 concerns. `_open_config_file` has 106 LOC of platform branches. `app.py` is imported by ~20 test files via `monkeypatch.setattr`, so every inline method increases patch surface.
- **Proposed fix**: Extract controllers parallel to existing `SettingsController`: `RepasteController`, `UndoController`, `ConfigEditorLauncher`, `RestartController`. Target `app.py` ~400 LOC pure orchestrator.
- **Confidence**: High
- **Source**: R9

### S2-CR-25 — `tests/test_app.py` (2954 LOC) and `tests/test_server.py` (2799 LOC) are catch-all test dumps
- **Severity**: High
- **Status**: Pending
- **Category**: Spaghetti / monolith detection / Testing infrastructure
- **Location**: `tests/test_app.py` (2954 LOC, 29 test classes); `tests/test_server.py` (2799 LOC, 38 test classes)
- **Evidence**: `test_app.py` covers 12+ domains (state transitions, config wiring, settings window, quit/restart cleanup ×4 classes, hotkey mapping + fallback + callback chain ×3 classes, toggle dispatch, model loading, start dictation, streaming, mic selection, startup integration, resilience, no-crash, text cleanup, tray protocol, external corrections, win32 console, single instance, undo batching + grapheme, init failure, excepthook, quit-always-pushes-event). `test_app.py` GREW from 2484 to 2954 LOC since the prior review flagged it.
- **Root cause**: No per-domain test modules. Every new bug fix adds a new test class because the shared `app` fixture lives there.
- **Impact**: Running any single test class loads 2954 LOC + heavy `mock_heavy_imports` fixture. A change to shared fixture can break tests in 12 unrelated domains.
- **Proposed fix**: Split `test_app.py` into per-domain modules under `tests/app/` (lifecycle, quit_restart, dictation, hotkeys, undo_repaste, config_wiring, tray_and_console). Move `app` fixture to `tests/app/conftest.py`. Same pattern for `test_server.py` → `tests/server/`.
- **Confidence**: High
- **Source**: R9, R16

### S2-CR-26 — In-flight transcription silently lost on sidecar crash (no recovery, no user notification)
- **Severity**: High
- **Status**: Pending
- **Category**: Error recovery / Reliability
- **Location**: `src-tauri/src/sidecar/ws.rs:162-217` + `voice_typer/server/recording/recorder.py` (audio buffer lives in sidecar process)
- **Evidence**: WS reader drains `pending` dispatch requests with `{"code": "sidecar_disconnected"}` on exit — but in-flight transcriptions (audio being captured + processed, both inside sidecar process) have NO entry in `pending`. crash_recovery.add() only runs AFTER transcription completes.
- **Root cause**: Crash-recovery buffer's `add()` is called from `_store_result` AFTER successful transcription.
- **Impact**: User dictates long passage, sidecar crashes (CUDA OOM, native hotkey listener SEH), entire dictation silently lost. User sees brief "reconnecting…" banner and must remember + re-dictate.
- **Proposed fix**: Two-pronged. (a) Persist raw audio chunks to temp file as they're captured (recorder already has ring buffer — add fsync'd spill file every N chunks). On sidecar restart, prompt user "Your last recording was interrupted — recover?" (b) Emit `dictation_lost` push event from FT-1 supervisor to renderer when sidecar crash detected while recorder state was recording/transcribing.
- **Confidence**: High
- **Source**: R12

### S2-CR-27 — history_db blocking write can wedge dictation for 4.5 minutes
- **Severity**: High
- **Status**: Pending
- **Category**: Reliability and stability
- **Location**: `voice_typer/server/history_db.py:737-755` (`_submit_write`, `_WRITE_FUTURE_TIMEOUT=30.0`)
- **Evidence**: `while True: try: return future.result(timeout=30) except TimeoutError: if not writer_thread.is_alive(): raise; log.warning(...); # loops forever`. Called from `dictation_pipeline._store_result` via `history_db.flush()` on transcription thread while `_busy_event` is cleared.
- **Root cause**: Blocking-write path loops forever as long as writer thread is alive. 30s timeout only logs warning and re-loops.
- **Impact**: If writer is stalled (antivirus lock, external SQLite process, disk full), transcription thread blocks indefinitely. User cannot start new dictation, cannot cancel, only recovery is 90s×3=270s transcription watchdog.
- **Proposed fix**: Add hard upper bound on total wait (e.g. 60s max). On exceeding, raise HistoryDBError. Or make flush() non-blocking on transcription path (repaste could tolerate brief lag by retrying get_latest_text()).
- **Confidence**: High
- **Source**: R10

### S2-CR-28 — "ready" signal fires before backend is actually ready (race after sidecar restart)
- **Severity**: High
- **Status**: Pending
- **Category**: Reliability and stability
- **Location**: `voice_typer/client/src/main/windows/main-window.ts:51` + `voice_typer/server/ipc_server.py:main()` + `voice_typer/server/sidecar_ws.py:374-378`
- **Evidence**: `main-window.ts:51 state.pythonReady = true` set when BrowserWindow is created. `ipc_server.py main() server.push({"type": "ready"})` sent BEFORE `app.start()` enters tray loop and BEFORE `_do_startup` (model load, hotkey registration, mic enumeration) completes. `sidecar_ws.py:374-378` emits `ready` on first authenticated WS connection, before any model load.
- **Root cause**: "ready" signal sent before backend fully initialized.
- **Impact**: After sidecar restart, Electron considers Python "ready" immediately and may dispatch IPC commands before ASR backend is loaded. Renderer's cached "model loaded" state may not match new backend.
- **Proposed fix**: Add "backend_ready" event emitted from `StartupSequence.run()` AFTER model load completes and hotkeys are registered. Have Electron wait for this event before considering pythonReady=true.
- **Confidence**: High
- **Source**: R10

### S2-CR-29 — Config save failure silently swallowed (user's settings lost on disk-write error)
- **Severity**: High
- **Status**: Pending
- **Category**: Data integrity & persistence
- **Location**: `voice_typer/server/service.py:1408` (`app.config.save()` return value not checked)
- **Evidence**: `Config.save()` returns False on OSError/PermissionError (caught at config.py:1057-1059) but never raises. `apply_config` does not check return value. IPC handler returns `{type: "ack"}` regardless. `save_strict()` exists at config.py:1061 and WOULD raise, but is never called anywhere.
- **Root cause**: Wiring `apply_config` to call `save_strict()` was deferred per the docstring "out of scope for this file."
- **Impact**: In-memory Config has new values but on-disk config.json has old values. Renderer shows user's changes as saved. On next restart, Config.load() reads old values — user's changes silently lost. Triggers: disk full, antivirus lock, read-only mount, quota.
- **Proposed fix**: Replace `app.config.save()` at service.py:1408 with `app.config.save_strict()`. RuntimeError propagates to IPC handler's outer try/except, returned as `{type: "error"}` to renderer.
- **Confidence**: High
- **Source**: R15

### S2-CR-30 — Onboarding `apply_settings` writes `.onboarding_complete` marker even when config save failed
- **Severity**: High
- **Status**: Pending
- **Category**: Data integrity & persistence / User onboarding
- **Location**: `voice_typer/server/onboarding.py:455-459`
- **Evidence**: `config.save()` returns False on failure, does NOT raise. Docstring claims "If config.save() raises, the marker is NOT written" — but Config.save() never raises. `mark_complete()` runs unconditionally.
- **Root cause**: Return value not checked.
- **Impact**: User completes onboarding, selections applied in-memory, but on-disk config.json still has defaults. `.onboarding_complete` marker IS written. On next restart, Config.load() reads defaults; `is_first_run()` returns False (marker exists) — wizard never reappears. User stuck with default settings.
- **Proposed fix**: `if not config.save(): raise RuntimeError("failed to persist onboarding settings")` — or call `config.save_strict()`. IPC handler catches exceptions, renderer surfaces failure and lets user retry.
- **Confidence**: High
- **Source**: R15

### S2-CR-32 — Settings → Privacy → Fast Startup description misdirects user to wrong page
- **Severity**: High
- **Status**: Pending
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
- **Status**: Pending
- **Category**: UX/UI consistency
- **Location**: `i18n/translations/en.json:198` (analytics.dictationsToday), `:208` (analytics.transcriptionsPerDay), `:1064` (history.noTranscriptions), `:1081` (history.transcriptionsToday), `:210` (analytics.dayCountTooltipPlural), `components/dashboard/StatCards.tsx:42`
- **Evidence**: Same concept — count of recorded speech-to-text sessions — labeled with three different nouns: "Dictations" (Home StatCards), "Dictations Today" (Analytics), "transcriptions today" (History). "Transcriptions per day" (Analytics axis).
- **Root cause**: i18n keys authored by different feature rounds.
- **Impact**: Users tracking activity across pages have to mentally reconcile "dictation" and "transcription" — may assume numbers measure different things.
- **Proposed fix**: Pick ONE noun ("Dictation" — matches primary action verb "Start dictation"). Update all keys. Keep "transcription" only where it refers to OUTPUT TEXT.
- **Confidence**: High
- **Source**: R4

### S2-CR-34 — Settings → Recording Mode tooltip calls option "Toggle" while visible label says "Tap to Record"
- **Severity**: High
- **Status**: Pending
- **Category**: UX/UI consistency
- **Location**: `components/settings/RecordingSettingsSection.tsx:227-235` (SegmentedControl options) vs `i18n/translations/en.json:549-553`
- **Evidence**: SegmentedControl labels: "Tap to Record" / "Push to Talk". InfoTooltip text: "Toggle: press the key once to start and again to stop."
- **Root cause**: Tooltip string authored against earlier label set, not updated when labels renamed.
- **Impact**: User reads tooltip for clarification, sees "Toggle" but control says "Tap to Record" — unnecessary cognitive tax on most fundamental settings row.
- **Proposed fix**: Update `settings.hotkeySection.recordingModeInfo` to "Tap to Record: press the key once to start and again to stop. Push to Talk: hold the key while speaking." Apply to all locales.
- **Confidence**: High
- **Source**: R4

### S2-CR-35 — "ASR" acronym used in 20+ user-facing strings (jargon leaks to UI)
- **Severity**: High
- **Status**: Pending
- **Category**: Ease of use
- **Location**: Multiple strings in `i18n/translations/en.json`: `vocabulary.triggerHelp` (137), `vocabulary.category.*Desc`, `templates.triggerHelp` (184), `about.asrBackend` (239), `about.audioProcessingDesc` (247), `about.modelWeightsDesc` (249), `about.cloudAsrTitle` (250), `about.cloudAsrDesc` (251), `settings.audioEnhancement.compressorInfo` (650), `settings.audioEnhancement.limiterInfo` (662), `settings.privacy.openaiCloudAsrLabel/Info` (723,750), `settings.privacy.cloudAsrItem` (735), `models.asrTitle` (800), `models.asrSubtitle` (801), `models.cloud.title` (914), `models.cloud.consentDescription` (924)
- **Evidence**: 20+ user-facing strings use "ASR" (Automatic Speech Recognition). Examples: `about.asrBackend` = "ASR Backend", `models.asrTitle` = "ASR Models" (page heading users see), `vocabulary.triggerHelp` = "Type the word(s) exactly as the ASR mishears them."
- **Root cause**: Engineer-authored copy leaked to user-facing strings without humanizing pass.
- **Impact**: Settings/Microphone/Models/About/Vocabulary/Templates copy reads like internal docs. Users hesitate to change settings they don't understand. Models page heading says "ASR Models" — user clicking "Models" expects "Models".
- **Proposed fix**: Replace "ASR" with "speech recognizer" or "Voice Typer" depending on context. E.g. `models.asrTitle` → "Models". Apply across all 8 locales.
- **Confidence**: High
- **Source**: R4

### S2-CR-36 — Onboarding has no consent gate for HuggingFace download (PRIV-005 violation)
- **Severity**: High
- **Status**: Pending
- **Category**: User onboarding / Privacy & data protection
- **Location**: `voice_typer/server/service.py:1620-1626` (onboarding_apply change_model) + `voice_typer/server/transcription.py:821-849` (`_pre_download_model` consent check) + `.venv/lib/python3.12/site-packages/faster_whisper/utils.py:49-116` (download_model)
- **Evidence**: `onboarding_apply` calls `app.models.change_model(new_model)` → `_registry.load_active()` → `TranscriptionEngine.load()` → `_pre_download_model()` checks `consent = getattr(cfg, "huggingface_consent", False)` and returns silently when False. BUT then `WhisperModel(model_size, ...)` is called, and faster_whisper's `WhisperModel.__init__` calls `download_model(...)` → `huggingface_hub.snapshot_download(...)` with NO consent check. The download happens anyway, bypassing PRIV-005.
- **Root cause**: Voice Typer's consent gate only enforced on explicit pre-download path. Fallback path through faster_whisper's own download_model ignores consent flag.
- **Impact**: Privacy compliance gap: HuggingFace sees user's IP and download metadata without consent (GDPR Art. 13/44). UX gap: download happens silently with no `download_progress` events.
- **Proposed fix**: (a) On onboarding_apply, call `service.download_model(model_name)` (IPC path with progress events and consent gate) instead of `change_model` directly. (b) Monkey-patch or wrap `huggingface_hub.snapshot_download` so consent check is enforced on ALL callers. (c) Update `completeDescription` i18n to accurately describe what will happen.
- **Confidence**: High
- **Source**: R6

### S2-CR-37 — Onboarding model cards drop VRAM and language metadata
- **Severity**: High
- **Status**: Pending
- **Category**: User onboarding / UX
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:23-28` (ModelOption interface), `354-389` (render) + `voice_typer/server/onboarding.py:330-395` (MODEL_OPTIONS)
- **Evidence**: Backend `MODEL_OPTIONS` includes `vram_gb` and `languages` fields per UX-13/UX-32. Frontend TypeScript interface drops them: `interface ModelOption { name: string; size: string; speed: string; description: string; }`. `modelMultilingual` i18n string exists but is never used.
- **Root cause**: Interface not updated when UX-13/UX-32 added metadata fields. TypeScript structural typing silently drops fields.
- **Impact**: User on 4GB RAM laptop picks "medium.en" (2GB VRAM) and discovers only after 5-minute download that model OOMs on load. Non-English speaker picks "small.en" not realizing it's English-only — gets garbage transcriptions.
- **Proposed fix**: Extend ModelOption to include `vram_gb?: number; languages?: string[] | null;`. Render "Multilingual" badge when `languages === null` and "{vram_gb} GB" badge. When vram_gb exceeds system RAM, show warning.
- **Confidence**: High
- **Source**: R6

### S2-CR-38 — Onboarding "Skip" button has no confirmation dialog (i18n keys for it exist)
- **Severity**: High
- **Status**: Pending
- **Category**: User flows
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:180-189` (handleSkip), `436-444` (Skip button) + `i18n/translations/en.json:1150-1152`
- **Evidence**: handleSkip directly calls `await call("onboarding_skip")` then `onComplete()` — no confirmation dialog. i18n defines `skipConfirmTitle: "Skip setup?"`, `skipConfirmMessage`, `skipConfirmLabel: "Skip anyway"` — but `grep -r "skipConfirmTitle" voice_typer/client/src` returns only en.json.
- **Root cause**: Confirmation dialog designed (i18n strings present) but never implemented.
- **Impact**: User clicks "Skip" (appears next to "Continue" on every step), instantly exits wizard with no mic, no hotkey test, no model selection. Next time they press hotkey, nothing happens. Skipped snack says "Setup skipped — using defaults" but doesn't tell user where to re-run wizard.
- **Proposed fix**: Wrap handleSkip in ConfirmDialog using existing `skipConfirmTitle`/`Message`/`Label` strings. Only call `onboarding_skip` after confirmation. Add "Where to find this later" hint: "Setup skipped — re-run from Settings → Troubleshooting".
- **Confidence**: High
- **Source**: R6

### S2-CR-39 — Onboarding mic auto-selects first device, ignores default flag, no test button
- **Severity**: High
- **Status**: Pending
- **Category**: User onboarding
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:99-115` (mic auto-select), `296-316` (mic UI) + `voice_typer/server/server_platform/microphone_list.py:120-130` (default flag) + `voice_typer/client/src/renderer/src/pages/Microphone.tsx`
- **Evidence**: Auto-select logic: `return mics.microphones[0].id;` — picks first mic in sounddevice's enumeration order, not system default. Backend marks default with `default: True` but frontend ignores. Select dropdown shows mic.name only — no "Default" badge, no "Bluetooth" warning despite backend providing `is_bluetooth`. No "Test microphone" button in wizard. When no mics detected, Continue button remains enabled.
- **Root cause**: Wizard treats mic selection as simple dropdown, ignoring both default-device flag and existing mic-test infrastructure.
- **Impact**: (1) Users with multiple input devices get wrong device pre-selected. (2) Users with non-working default finish onboarding without knowing mic doesn't work. (3) Bluetooth HFP users (8kHz sample rate) get no warning.
- **Proposed fix**: (a) Pre-select mic where `default === true`. (b) Show "Default" badge and Bluetooth warning icon. (c) Add "Test microphone" button using existing TestReviewPanel. Block Continue until test passes or user clicks "Skip test". (d) When no mics detected, disable Continue and show "Refresh" button.
- **Confidence**: High
- **Source**: R6

### S2-CR-40 — Onboarding download has no progress UI (user thinks app hung)
- **Severity**: High
- **Status**: Pending
- **Category**: User onboarding / UX
- **Location**: `voice_typer/server/service.py:1620-1626` + `voice_typer/server/model_manager.py:270-321` + `voice_typer/client/src/renderer/src/pages/Home.tsx:384-397`
- **Evidence**: `onboarding_apply` calls `app.models.change_model(new_model)` → `_registry.load_active()` → `TranscriptionEngine.load()` → `WhisperModel(...)`. Uses `progress_callback` to update TRAY status string only. Does NOT call `event_bus.publish({"type": "download_progress", ...})` — that publishing happens exclusively in `service.download_model()` (service.py:1928-1964). Home.tsx subscribes to `download_progress` events but never receives them when download triggered via onboarding_apply.
- **Root cause**: Two model-download code paths (IPC `download_model` with progress events vs engine `load()` with tray-only updates). onboarding_apply uses second path.
- **Impact**: After clicking "Get Started", user sees "Setup complete! Loading your model..." snack and then nothing — no progress bar, no percentage, no ETA, no cancel. On slow connection downloading medium.en (1.5GB), user waits 10+ minutes with no feedback. Common to assume app hung and force-quit.
- **Proposed fix**: Have `onboarding_apply` call `service.download_model(model_name)` (IPC path that publishes progress events) before marking complete. Render DownloadProgressBar in wizard's final step.
- **Confidence**: High
- **Source**: R6

### S2-CR-41 — README references 5 module paths that are now packages (not .py files)
- **Severity**: High
- **Status**: Pending
- **Category**: Documentation
- **Location**: `README.md:404, 412, 415, 430`; `CONTRIBUTING.md:248`; `docs/ARCHITECTURE.md:17, 21`; `docs/migration/tauri-sidecar-bridge.md:16`; `CHANGELOG.md:44-45`; `docs/PLATFORM_STATUS.md:209`
- **Evidence**: Multiple docs reference `recording.py`, `hotkeys.py`, `server_platform.py`, `prewarm.py`, `native_hotkeys.py` — but all 5 are directories with `__init__.py` in the current source tree.
- **Root cause**: Refactor to packages (Phase 4.5 / ARCH-045) not reflected in docs.
- **Impact**: Contributors grep for `recording.py` and find nothing. IDE "go to file" fails on these paths. Architecture tree in README § Architecture is structurally wrong.
- **Proposed fix**: Update all 9+ doc references: `recording.py` → `recording/`, `hotkeys.py` → `hotkeys/`, `server_platform.py` → `server_platform/`, `prewarm.py` → `prewarm/`, `native_hotkeys.py` → `native_hotkeys/`.
- **Confidence**: High
- **Source**: R7

### S2-CR-42 — API.md has 8+ stale method signatures
- **Severity**: High
- **Status**: Pending
- **Category**: Documentation
- **Location**: `docs/API.md:23, 26, 33-35, 41, 51, 53, 59-61, 75, 93, 184, 195`
- **Evidence**: Multiple wrong signatures: `run()` method on VoiceTyperApp (doesn't exist), `restart()` (actual: `restart_app()`), `start(device_index)` (actual: `start(self)`), `cancel()` on Recorder (doesn't exist), `transcribe(audio, sample_rate)` (actual: `transcribe(self, audio, audio_stats=None)`), `get(key, default)`/`set(key, value)` on Config (don't exist; access is via attribute), `unload_all()` on ModelManager (doesn't exist).
- **Root cause**: API.md written against older version, not maintained through refactors.
- **Impact**: Contributors writing external integrations get ImportError/AttributeError. CI test only checks config defaults, not signatures.
- **Proposed fix**: Audit every method signature against current source. Either update each signature, or add `tests/test_api_doc_signatures.py` that uses `inspect.signature()` to assert every documented method matches. Given 8+ stale entries, full rewrite warranted.
- **Confidence**: High
- **Source**: R7

### S2-CR-43 — ARCHITECTURE.md line counts are wildly stale (claims 2,205 / 1,866 LOC, actual 310 / 234)
- **Severity**: High
- **Status**: Pending
- **Category**: Documentation
- **Location**: `docs/ARCHITECTURE.md:57, 65`
- **Evidence**: Line 57: "Electron main process | `voice_typer/client/src/main/index.ts` (2,205 lines)". Line 65: "Rust host | `src-tauri/src/main.rs` (1,866 lines)". Actual: index.ts is 310 lines, main.rs is 234 lines.
- **Root cause**: Numbers not updated after extraction to submodules.
- **Impact**: Architecture discussions referencing "the 2,205-line main process" are based on fiction. Refactoring decisions justified by file size look misguided.
- **Proposed fix**: Update line 57 to "310 lines" and line 65 to "234 lines" (or remove line counts entirely).
- **Confidence**: High
- **Source**: R7

### S2-CR-44 — `config.py:_validate_non_numeric_fields` is 295 LOC with 4 near-identical loops
- **Severity**: High
- **Status**: Pending
- **Category**: Refactoring / Maintainability
- **Location**: `voice_typer/server/config.py:1469-1764`
- **Evidence**: Method contains FOUR near-identical loops over `bool_fields` (1653-1675), `str_fields` (1679-1691), `int_fields` (1698-1730), `float_fields` (1735-1760). Each loop's tail is same 5-line pattern duplicated 6 times total.
- **Root cause**: Four type-coercion paths share ~80% structure but were written as four independent loops.
- **Proposed fix**: Extract a `_coerce_field(field_name, val, expected_type, defaults, warnings, *, coerce_fn=None, optional=False)` helper. Each loop becomes 3-line delegation. Or declare `FieldSpec(type, default, optional, coerce)` per field and iterate once.
- **Confidence**: High
- **Source**: R8

### S2-CR-45 — `Config.load` is 390 LOC, impossible to test sub-concerns in isolation
- **Severity**: High
- **Status**: Pending
- **Category**: Maintainability
- **Location**: `voice_typer/server/config.py:1075-1467`
- **Evidence**: `Config.load` does: secure-read, schema-version forward-migration, four inline streaming-config clamp blocks each with own try/except, max-recording-time clamping, model_size validation, qwen_model_path validation + safe-dir check, corrections_path validation + path-traversal check, privacy warning, credential_store migration + keyring:// resolution, `_validate_non_numeric_fields` call, dataclass construction, audio-preset reapplication, exception handler with diagnostic file write.
- **Root cause**: Classmethod that grew by accretion — every config hardening added another inline block.
- **Impact**: Impossible to test individual sub-concerns in isolation. Any change risks breaking load path for ALL users.
- **Proposed fix**: Extract focused helpers — `_migrate_schema(data, loaded_version)`, `_clamp_streaming_fields(data)`, `_validate_qwen_path(data)`, `_validate_corrections_path(data)`, `_resolve_keyring_refs(data)`. Body of `load` becomes 30-line orchestration.
- **Confidence**: High
- **Source**: R8

### S2-CR-46 — `_send_ctrl_v_win32` returns None, caller treats as bool — every Windows paste logs spurious "failed" warning
- **Severity**: High
- **Status**: Pending
- **Category**: Code quality / Error handling
- **Location**: `voice_typer/server/clipboard.py:1292-1397` (`_send_ctrl_v_win32`) and `:1184-1202` (caller in `paste()`)
- **Evidence**: Function signature says `-> None` (correct — never returns bool), but caller line 1191 assigns its result as bool and checks `if not paste_succeeded`. Since `_send_ctrl_v_win32` always returns None, `paste_succeeded` is always None (falsy), so warning ALWAYS fires and `paste()` ALWAYS returns False on Windows — even when SendInput returned 4 (full success).
- **Root cause**: Type annotation vs caller mismatch.
- **Impact**: Every successful Windows paste logs spurious "Auto-paste failed" warning at WARNING level. Callers that check return value mis-classify successful pastes as failures.
- **Proposed fix**: Either change annotation to `-> bool` and `return True` at end of success path, OR remove `paste_succeeded` assignment and dead `if not paste_succeeded:` branch (function already logs its own warning on partial success).
- **Confidence**: High
- **Source**: R8

### S2-CR-49 — History DB has no FTS5 index — full-text search is O(n) table scan
- **Severity**: High
- **Status**: Pending
- **Category**: Scalability
- **Location**: `voice_typer/server/history_db.py:1254` (search method) + schema at `:470-490`
- **Evidence**: Schema has only two indexes (`idx_timestamp`, `idx_favorite`). No FTS5 virtual table. `search` uses `LIKE %query%` — leading wildcard prevents any index use. Validator allows up to 1,000,000 entries.
- **Root cause**: No FTS migration was added.
- **Impact**: UI search becomes sluggish as history grows. At 100K+ rows, every search_history IPC call scans entire table and materializes up to 500 rows.
- **Proposed fix**: Add FTS5 virtual table `transcriptions_fts` populated via triggers on INSERT/DELETE. search() queries FTS table and joins back by id. MATCH queries are O(log n).
- **Confidence**: High
- **Source**: R10

### S2-CR-51 — i18n key parity broken: 25 keys missing in 7 non-English locales, 4 keys missing in en.json (CI red)
- **Severity**: High
- **Status**: Pending
- **Category**: Localization / i18n
- **Location**: `voice_typer/client/src/renderer/src/i18n/translations/{ar,de,es,fr,hi,ru,zh}.json` — 25 keys missing in each non-English locale; `en.json` — 4 keys missing
- **Evidence**: `pytest tests/test_i18n_completeness.py::TestI18nCompleteness::test_key_parity_with_en -x` fails: "ar.json is missing 25 keys that en.json has: ['bubble.micButtonAria', 'bubble.micButtonStartAria', 'bubble.micButtonStopAria', 'onboarding.micLevel', 'onboarding.modelMultilingual', 'onboarding.permissionsDescription', ...]". en.json is missing 4 keys that exist in 6 non-English locales: `a11y.opensInNewTab`, `dashboard.cards.chars`, `dashboard.cards.dictations`, `dashboard.cards.duration`.
- **Root cause**: Keys added to en.json without backfilling non-English locales. Separately, 4 keys added to non-English locales without ever being added to en.json.
- **Impact**: For end users in ar/de/es/fr/hi/ru/zh: Bubble's mic button announced to screen readers in English. CI currently red.
- **Proposed fix**: Backfill the 25 missing keys in each non-English locale (or add to `RW2_BACKFILLED_PENDING_TRANSLATION` ratchet set as stopgap). Add the 4 missing keys to en.json. Re-run `pytest tests/test_i18n_completeness.py`.
- **Confidence**: High
- **Source**: R5

### S2-CR-52 — SegmentedControl tests assert old radiogroup/radio roles, component emits tablist/tab (CI red)
- **Severity**: High
- **Status**: Pending
- **Category**: Accessibility / Testing infrastructure
- **Location**: `voice_typer/client/src/renderer/src/components/ui/__tests__/segmented-control.test.tsx:416-519`
- **Evidence**: Three tests call `screen.getByRole("radiogroup")` and `screen.getAllByRole("radio")`. But component now correctly emits `role="tablist"` and `role="tab"` for variant="tabs" (segmented-control.tsx:212, 271). Running `vitest run` produces "3 failed | 22 passed". Runtime DOM confirms `<div role="tablist">` and `<button role="tab" aria-selected="true" tabindex="0">`.
- **Root cause**: A11Y-6 fix added role="tablist"/"tab" to component but never updated these three tests.
- **Impact**: CI is broken — three failing tests block the build.
- **Proposed fix**: Update the three tests to use `getByRole("tablist")` and `getAllByRole("tab")`. Add assertions for `aria-selected`, roving `tabIndex`, ArrowLeft/ArrowRight keyboard navigation.
- **Confidence**: High
- **Source**: R5

### S2-CR-53 — SegmentedControl tabs variant missing `id`/`aria-controls` (WAI-ARIA Tabs pattern broken)
- **Severity**: High
- **Status**: Pending
- **Category**: Accessibility
- **Location**: `voice_typer/client/src/renderer/src/components/ui/segmented-control.tsx:262-299` (button render for variant="tabs") + `voice_typer/client/src/renderer/src/pages/Settings.tsx:811-872` + `voice_typer/client/src/renderer/src/pages/Models.tsx:1129-1178`
- **Evidence**: Component renders `role="tab"` with `tabIndex`/`aria-selected` but NO `id` or `aria-controls`. Settings.tsx renders tab content via `{activeTab === "general" && (<>...</>)}` blocks WITHOUT any `<div role="tabpanel" aria-labelledby={tabId} id={panelId}>`. Component's own JSDoc explicitly warns about this requirement.
- **Root cause**: A11Y-6 was half-fixed: tablist/tab roles added, but consumer call sites never updated, component never wires `id`/`aria-controls`.
- **Impact**: Screen-reader users cannot associate Settings tabs (General / AI & Audio / Appearance / Privacy) or Models tabs (Local / Cloud) with their content panels.
- **Proposed fix**: (1) In segmented-control.tsx tabs variant, accept `getTabId(value)`/`getPanelId(value)` prop pair (or auto-generate via useId) and emit `id={tabId}` + `aria-controls={panelId}` on each `<button role="tab">`. (2) In Settings.tsx and Models.tsx, wrap each conditional block in `<div role="tabpanel" id={panelId} aria-labelledby={tabId} tabIndex={0}>`. (3) Add axe-core test for tablist+tabpanel pairing.
- **Confidence**: High
- **Source**: R5

### S2-CR-54 — 14 hardcoded English error strings in HotkeyPicker and hotkey-validation.ts
- **Severity**: High
- **Status**: Pending
- **Category**: Localization / i18n
- **Location**: `voice_typer/client/src/renderer/src/components/hotkey/hotkey-validation.ts:182, 191, 204, 210, 225, 246, 263, 276, 289, 305, 323` (11 strings); `HotkeyPicker.tsx:351-353, 373-375, 412-414, 439-441, 562-564, 775-777` (3 strings)
- **Evidence**: hotkey-validation.ts returns English-only error reasons: `return { valid: false, reason: "Hotkey is empty" };` etc. HotkeyPicker.tsx sets additional hardcoded English errors. None go through `t()`. Surfaced via `setError()` rendered as `role="alert"` live region.
- **Root cause**: String literals hardcoded in source; no `t()` calls.
- **Impact**: Non-English users (Arabic primary audience + de/es/fr/hi/ru/zh) see English error messages when picking invalid hotkey.
- **Proposed fix**: Add `hotkeyValidation.*` keys to all 8 locale files. Replace each hardcoded `reason: "..."` with `reason: t("hotkeyValidation.empty")`. Update HotkeyPicker-a11y.test.tsx assertions.
- **Confidence**: High
- **Source**: R5

### S2-CR-55 — App.tsx has 7 hardcoded English strings (visible during 30-60s startup)
- **Severity**: High
- **Status**: Pending
- **Category**: Localization / i18n
- **Location**: `voice_typer/client/src/renderer/src/App.tsx:530-531, 538, 543, 452, 455, 338, 323`
- **Evidence**: Hardcoded English strings: `"✓ Starting Python"`, `"Loading model"`, `"③ Ready"`, `"Page not found"`, `"Unknown page: {String(currentPage)}"`, `label: "Copy path"` (toast action), default fallback message `"Transcription complete, but the clipboard was unavailable..."`.
- **Root cause**: Literal English strings; not wrapped in `t()`.
- **Impact**: 3-step connecting indicator is FIRST thing every user sees on cold start — non-English users see English for entire 30-60s startup window.
- **Proposed fix**: Add `app.connecting.startingPython/loadingModel/ready`, `app.pageNotFound`, `app.unknownPage`, `pasteFailed.copyPath`, `pasteFailed.defaultMessage` keys to all 8 locale files. Replace literals with `t(...)`.
- **Confidence**: High
- **Source**: R5

### S2-CR-56 — Dictation pipeline has NO end-to-end test (only narrow regression tests)
- **Severity**: High
- **Status**: Pending
- **Category**: Test coverage gaps
- **Location**: `voice_typer/server/dictation_pipeline.py` (1046 LOC) vs `tests/test_dictation_pipeline_review_fixes.py` (495 LOC, narrow scope)
- **Evidence**: DictationPipeline methods: `run()` (230 LOC) — NO direct test; `_check_resources()` (160 LOC) — NO direct test; `_handle_empty_transcription()` — NO direct test; `_clean_text()` — NO direct test; `_apply_punctuation()` — NO direct test; `_apply_llm_polish()` — NO direct test; `_apply_ai_enhancement()` — NO direct test; `_analyze_vocabulary()` (95 LOC) — NO direct test.
- **Root cause**: "review_fixes" test file was created as regression guard for two specific findings and uses `DictationPipeline.__new__(DictationPipeline)` to bypass `__init__`.
- **Impact**: The actual transcription pipeline orchestration — clean → vocabulary → template → punctuate → llm_polish → ai_enhance → store → copy_and_paste — has no end-to-end test.
- **Proposed fix**: Add `tests/test_dictation_pipeline.py` with: (1) happy-path test that constructs real DictationPipeline with mocked app + mocked transcriber returning "hello world", calls `run()`, asserts text flows through all stages in order; (2) tests for `_check_resources`; (3) tests for `_handle_empty_transcription`; (4) tests for `_apply_llm_polish` and `_apply_ai_enhancement`.
- **Confidence**: High
- **Source**: R16

### S2-CR-57 — `hotkey_dispatcher.py` has NO direct unit tests
- **Severity**: High
- **Status**: Pending
- **Category**: Test coverage gaps
- **Location**: `voice_typer/server/hotkey_dispatcher.py` (303 LOC) — no `tests/test_hotkey_dispatcher.py`
- **Evidence**: HotkeyDispatcher tested only through VoiceTyperApp integration in `tests/test_app.py:952-1080`. The keyboard_ownership guard that short-circuits when `is_hotkey_capture_active()` returns True (HOTKEY-FIX-001) and the most complex method `_on_esc_release()` (212-249) have ZERO direct tests.
- **Root cause**: Dispatcher extracted from VoiceTyperApp but tests stayed in test_app.py.
- **Impact**: Regression in `_on_esc_release` (e.g. forgetting to publish `hotkey_capture_cancel`) would not be caught — frontend would silently stay in capture mode after ESC release.
- **Proposed fix**: Add `tests/test_hotkey_dispatcher.py` with unit tests for `_make_dictation_callback`, `_make_repaste_callback`, and `_on_esc_release` (all paths).
- **Confidence**: High
- **Source**: R16

### S2-CR-58 — `tests/test_cloud_engines.py` makes real network egress (flaky + slow)
- **Severity**: High
- **Status**: Pending
- **Category**: Test coverage gaps & flaky tests
- **Location**: `tests/test_cloud_engines.py:138-161, 272-291`
- **Evidence**: Three tests rely on HTTP requests failing with RuntimeError instead of mocking HTTP layer: `test_openai_default_url_allowed`, `test_localhost_self_hosted_allowed`, `test_accepts_valid_model_name`. Comments say "the actual HTTP request will fail (no network)".
- **Root cause**: Tests use "no HTTP server" as proxy signal for URL allowlist / model-name validation passing.
- **Impact**: False negatives on runners with localhost services bound; slow tests (TCP connect timeout); tests pass for wrong reason.
- **Proposed fix**: Mock `voice_typer.server.cloud_engines._opener.open` to raise `urllib.error.URLError("test")`. Then assert URLError is wrapped as RuntimeError.
- **Confidence**: High
- **Source**: R16

### S2-CR-59 — `tests/test_e2e_pipeline.py` uses racy `_free_port()` + `time.sleep(0.2)` workaround
- **Severity**: High
- **Status**: Pending
- **Category**: Test coverage gaps & flaky tests
- **Location**: `tests/test_e2e_pipeline.py:117-123, 281`
- **Evidence**: `_free_port()` binds socket, gets port, closes socket, returns port — race window between close and rebind. `time.sleep(0.2)` at line 281 with comment "Brief pause to let the OS release the port before the next test". The S2-CR-7 production fix (`_pick_available_port` returning BOUND socket) was added to fix exactly this race in production code, but tests still use the legacy racy pattern.
- **Root cause**: Test infrastructure not updated when production fix landed.
- **Impact**: Intermittent `OSError: [Errno 98] Address already in use` on CI runners under load. The `time.sleep(0.2)` slows every e2e test by 200ms.
- **Proposed fix**: Replace `_free_port()` + `start_tcp(port)` with `_pick_available_port(0, max_tries=1)` (returns bound socket). Delete `time.sleep(0.2)`.
- **Confidence**: High
- **Source**: R16

### S2-CR-60 — `tests/test_app.py` has local `mock_heavy_imports` fixture that shadows project-wide one
- **Severity**: High
- **Status**: Pending
- **Category**: Testing infrastructure
- **Location**: `tests/test_app.py:39-88` (local fixture) vs `tests/conftest.py:132-201` (project-wide)
- **Evidence**: Local `@pytest.fixture(autouse=True) def mock_heavy_imports(monkeypatch)` duplicates the project-wide fixture. Local version does NOT honor `@pytest.mark.real_pynput`/`real_pil` markers. ADDS `monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", _force_pynput)` that project-wide doesn't have.
- **Root cause**: Local fixture predates project-wide one or was added to force pynput backend.
- **Impact**: Tests in test_app.py (2954 LOC, 28 classes) cannot use real_pynput/real_pil markers — contributors who try silently get mocked behavior.
- **Proposed fix**: Delete local fixture. Move `hotkey_dispatcher.create_hotkey_backend` force-pynput logic into project-wide fixture (gated on `if not request.node.get_closest_marker("real_pynput"):`).
- **Confidence**: High
- **Source**: R16

### S2-CR-61 — `test_winlogon_desktop_detection` has `assert True` (gives false coverage)
- **Severity**: High
- **Status**: Pending
- **Category**: Test coverage gaps & flaky tests
- **Location**: `tests/test_platform_uac.py:49-65`
- **Evidence**: Test sets up Win32 mocks but never invokes any SUT function. The `assert True` at end always passes regardless of whether production code handles Winlogon scenario.
- **Root cause**: Mock setup is dead — nothing reads it.
- **Impact**: False coverage. Test reports as passing in CI, giving impression that Winlogon desktop detection is verified. A regression that breaks Winlogon handling would not be caught.
- **Proposed fix**: Either call the actual SUT function and assert on return value, OR delete the test if SUT function doesn't exist yet.
- **Confidence**: High
- **Source**: R16

### S2-CR-62 — Mutmut configured but never run in CI (dead infrastructure)
- **Severity**: High
- **Status**: Pending
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
- **Status**: Pending
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
- **Status**: Pending
- **Category**: Test coverage gaps & flaky tests
- **Location**: `tests/` (35 files, 164 occurrences)
- **Evidence**: `tests/test_e2e_smoke.py:135` — `rec_src = inspect.getsource(recording); assert "rms_callback(chunk_rms, chunk_peak, filtered)" in rec_src`. Passes even if call moved to different module; fails on cosmetic variable rename. `tests/test_dictation_pipeline_review_fixes.py:281-297` — asserts `f"self.{flag}" not in src`. Contributor who refactors to `getattr(self._app, flag)` would BREAK this test.
- **Root cause**: Tests assert on literal source text rather than runtime behavior. Pattern proliferated far beyond justified uses.
- **Impact**: Two failure modes: (1) cosmetic refactor breaks tests; (2) SUT can be functionally broken while test still passes — false confidence.
- **Proposed fix**: For each `inspect.getsource` use, classify: (a) "structural regression guard" where behavioral test is impossible — keep, add comment; (b) "behavioral test was easier" — rewrite to call function with fake recorder and assert on callback invocation. Target ≤30 occurrences.
- **Confidence**: High
- **Source**: R16

### S2-CR-65 — pytest-timeout declared but ZERO `@pytest.mark.timeout` annotations
- **Severity**: High
- **Status**: Pending
- **Category**: Testing infrastructure
- **Location**: `pyproject.toml:162-169` (declares pytest-timeout>=2.3) vs. entire tests/ directory
- **Evidence**: `grep -rn "@pytest.mark.timeout" tests/` returns nothing. Dependency installed but unused. Combined with 182 `time.sleep` occurrences, a single hung test (e.g. `test_lock_order_contract.py` with real deadlock) will run until GitHub Actions 6-hour job timeout.
- **Root cause**: pytest-timeout added but no test ever annotated.
- **Impact**: Hung tests waste CI minutes and block PRs. No early failure signal.
- **Proposed fix**: Add `@pytest.mark.timeout(30)` to thread-stress tests and `@pytest.mark.timeout(10)` to all e2e_pipeline tests. Or set global default `timeout = 60` in `[tool.pytest.ini_options]`.
- **Confidence**: High
- **Source**: R16

### S2-CR-66 — Windows Tauri workflow hardcodes x86_64 (no Windows-on-ARM build)
- **Severity**: High
- **Status**: Pending
- **Category**: CI/CD / Cross-platform compatibility
- **Location**: `.github/workflows/tauri-windows-build.yml:73-74, 263`
- **Evidence**: `env: PYBS_TRIPLE: x86_64-pc-windows-msvc, RUST_TARGET: x86_64-pc-windows-msvc`. `cargo tauri build --target x86_64-pc-windows-msvc`. Workflow docstring claims `aarch64-pc-windows-msvc` supported but `target` input ignored. No matrix strategy. No aarch64 python-build-standalone download.
- **Root cause**: Hardcoded to x86_64.
- **Impact**: Windows ARM64 devices (Surface Pro X, Copilot+ PCs, Snapdragon X laptops) get no native build. Users run x86_64 via emulation with ~30% perf penalty + 2× battery drain.
- **Proposed fix**: Add `strategy: matrix: arch: [x86_64, aarch64]` and parameterize `RUST_TARGET`, `PYBS_TRIPLE`, `--target` flag from `${{ matrix.arch }}-pc-windows-msvc`.
- **Confidence**: High
- **Source**: R19

### S2-CR-67 — Linux aarch64 native hotkey listener not built by CI
- **Severity**: High
- **Status**: Pending
- **Category**: CI/CD / Cross-platform compatibility
- **Location**: `.github/workflows/tauri-linux-build.yml:218, 243-247`; `src-tauri/tauri.linux-aarch64.conf.json:3-5`
- **Evidence**: `if: env.BUILD_ARCH == 'x86_64'` skips native key-listener build for aarch64. `tauri.linux-aarch64.conf.json` does NOT include `linux-key-listener` in resources.
- **Root cause**: `compile_native.sh` uses host `gcc` and cannot cross-compile to aarch64.
- **Impact**: Linux aarch64 users (Raspberry Pi 5, Ampere Altra, AWS Graviton, Apple Silicon Linux VMs) install Tauri bundle with no native hotkey listener. Zero-command Caps Lock hotkey silently fails on aarch64. Pynput fallback doesn't support modifier-only hotkeys on Wayland.
- **Proposed fix**: Add self-hosted aarch64 runner (or QEMU-system-aarch64 step) that runs `compile_native.sh` natively, uploads `linux-key-listener` as separate artifact. Or document aarch64 as "sidecar-only, no native hotkey" in PLATFORM_STATUS.md.
- **Confidence**: High
- **Source**: R19

### S2-CR-68 — Linux `prerm` script doesn't probe Tauri v2 resource paths (uninstall cleanup silently skipped)
- **Severity**: High
- **Status**: Pending
- **Category**: Packaging, installer & update experience
- **Location**: `scripts/linux/prerm:12` vs `scripts/linux/postinst:31-46`
- **Evidence**: `postinst` (FIXED under NF-R9-2) probes multiple Tauri v2 paths: `/usr/lib/voice-typer/resources/scripts/install_permissions.py`, etc. `prerm` (NOT FIXED) only checks legacy path: `UNINSTALL_SCRIPT="/usr/share/voice-typer/scripts/uninstall_permissions.py"`. Under Tauri v2 the script ships at `/usr/lib/voice-typer/resources/scripts/linux/uninstall_permissions.py`. So `apt remove voice-typer` silently skips cleanup.
- **Root cause**: postinst was patched but prerm was not.
- **Impact**: Every Tauri .deb install on Linux leaves udev rule + XKB conf + manifest behind on uninstall. Users reinstalling hit untested idempotency code paths.
- **Proposed fix**: Mirror postinst probe loop in prerm. Same five candidate paths, looking for `uninstall_permissions.py`. Add regression test that diffs the two probe loops.
- **Confidence**: High
- **Source**: R19

### S2-CR-69 — Uninstaller doesn't remove autostart entries (Linux/macOS/Windows)
- **Severity**: High
- **Status**: Pending
- **Category**: Packaging, installer & update experience
- **Location**: `scripts/linux/uninstall_permissions.py:380-458`; `voice_typer/server/server_platform/autostart_linux.py:42-72`; `autostart_macos.py:61-179`; `autostart_windows.py`; `voice_typer/client/electron-builder.yml:55-59`; `src-tauri/tauri.conf.json:82-84`
- **Evidence**: Three separate autostart mechanisms (Linux `.desktop`, macOS LaunchAgent plist, Windows HKCU Run key + Task Scheduler) created at runtime. None of uninstall paths remove them. Linux prerm only runs `uninstall_permissions.py` (udev + XKB + manifest). Windows NSIS has no `deleteAppDataOnUninstall`. macOS has no uninstall script.
- **Root cause**: Autostart creation paths exist; autostart removal paths don't.
- **Impact**: After uninstall, every OS leaves stale autostart entry that fires on next login, fails to find app, spams errors.
- **Proposed fix**: (1) Linux: extend `uninstall_permissions.py --uninstall` to `unlink()` `~/.config/autostart/voice-typer.desktop`. (2) macOS: add `scripts/macos/uninstall.sh`. (3) Windows: set `deleteAppDataOnUninstall: true` in electron-builder.yml AND ship custom `uninstaller.nsh` that deletes HKCU Run key + `schtasks /delete` the task. (4) Add regression test.
- **Confidence**: High
- **Source**: R19

### S2-CR-70 — Uninstaller doesn't remove user data dir (GBs of model cache + venv left behind)
- **Severity**: High
- **Status**: Pending
- **Category**: Packaging, installer & update experience
- **Location**: `voice_typer/server/_paths.py:31-129`; `scripts/linux/uninstall_permissions.py:380-458`; `voice_typer/client/electron-builder.yml:55-59`; `src-tauri/tauri.conf.json`
- **Evidence**: config_dir holds logs + venv + HuggingFace model cache (potentially GBs). `uninstall_permissions.py` removes only `/var/lib/voice-typer/permissions-manifest.json` (root-owned). electron-builder.yml nsis block: no `deleteAppDataOnUninstall`. tauri.conf.json: no Tauri uninstall hook.
- **Root cause**: User data dir cleanup not in uninstall path.
- **Impact**: Users uninstalling Voice Typer are surprised by GBs of residual data. Support tickets "where did my disk go?" with no in-app answer.
- **Proposed fix**: (1) Add post-uninstall step on each OS that removes user data dir. (2) Document HuggingFace cache location and offer `--purge` flag. (3) Add in-app "Factory reset" button (Settings → Advanced).
- **Confidence**: High
- **Source**: R19

### S2-CR-71 — `pystray` pinned to 0.19.x because of private `_icon_handle` access (TODO S2-CR-16 never resolved)
- **Severity**: High
- **Status**: Pending
- **Category**: Dependency & supply-chain health
- **Location**: `pyproject.toml:82` (`pystray>=0.19,<0.20`), `voice_typer/server/tray.py:489-496`
- **Evidence**: `pystray` hard-pinned to 0.19 minor because `tray.py:_apply_state` reaches into private `_icon_handle` attribute as Win32 DestroyIcon workaround. 0.19 minor has not seen a release since 2023-04 (0.19.5); upstream pystray moved to 0.20+ dev.
- **Root cause**: Upstream issue for public `reset_icon_handle()` API was never filed.
- **Impact**: Lock-in to stale tray library (no security fixes from pystray 0.20+); future 0.20 release that renames/removes `_icon_handle` silently breaks Win32 DestroyIcon workaround.
- **Proposed fix**: (1) File upstream pystray issue for public `reset_icon_handle()` API. (2) Once upstream exposes it, bump to `pystray>=0.20` and replace `_icon_handle` access. (3) As stopgap, add regression test that asserts `hasattr(pystray.Icon, "_icon_handle")` so pystray version bump CI-fails loudly.
- **Confidence**: High
- **Source**: R18

### S2-CR-72 — Tauri sidecar handshake has no protocol-version check (silent version skew)
- **Severity**: High
- **Status**: Pending
- **Category**: API & IPC contract stability
- **Location**: `src-tauri/src/sidecar/spawn.rs:244-251` + `voice_typer/server/sidecar_ws.py:165-173`
- **Evidence**: `parse_server_started` (Rust) only reads `event` + `port`. `_emit_server_started` (Python) only emits `event` + `port`. No protocol-version field exchanged during handshake. When Python sidecar's `_COMMAND_REGISTRY` adds/renames/removes a command, Tauri host has no way to detect mismatch at handshake time.
- **Root cause**: No version negotiation in handshake.
- **Impact**: Version skew between Rust host and Python sidecar produces confusing partial failures: some commands work, others return `unknown_command`, push events may have unexpected `type` values.
- **Proposed fix**: Extend `_emit_server_started` to include `"protocol": <int>`. Extend `parse_server_started` to extract and return protocol int. In `spawn_sidecar_release`, compare sidecar's protocol to `const EXPECTED_PROTOCOL: u32` in Rust host; on mismatch, log clear error and emit `protocol_mismatch` Tauri event.
- **Confidence**: High
- **Source**: R20

### S2-CR-73 — Electron ALLOWED_COMMANDS missing 2 server commands (`onboarding_get_model_catalog`, `onboarding_check_permissions`)
- **Severity**: High
- **Status**: Pending
- **Category**: API & IPC contract stability
- **Location**: `voice_typer/client/src/main/index.ts:79-191` vs `voice_typer/server/ipc/server.py:1357-1471`
- **Evidence**: Python registry has handlers: `onboarding_get_model_catalog` (server.py:1407), `onboarding_check_permissions` (server.py:1409). Electron ALLOWED_COMMANDS does NOT include either. Renderer call silently dropped by `send-to-python.ts:48`. Grep confirms ZERO references in `client/src/`.
- **Root cause**: Allowlist curated incrementally per-feature; when these handlers were added, the corresponding Electron-side entries and renderer call sites were never wired.
- **Impact**: Onboarding flow cannot fetch model catalog or check OS permissions via IPC — features silently broken.
- **Proposed fix**: Either (a) add the two names to ALLOWED_COMMANDS AND wire renderer call sites, OR (b) remove handlers from `_COMMAND_REGISTRY`. Add CI test `tests/test_ipc_command_registry_sync.py` that loads both lists and asserts every server command (excluding Tauri-only `tray_click` and main-process-only `heartbeat`/`relaunch_ack`) is present in Electron allowlist.
- **Confidence**: High
- **Source**: R20

### S2-CR-74 — Logging consistency: streaming.py logs user speech content at WARNING (privacy regression)
- **Severity**: High
- **Status**: Pending
- **Category**: Observability / Privacy & data protection
- **Location**: `voice_typer/server/streaming.py:348-356`
- **Evidence**: `log.warning("[STREAMING] Word list exceeded %d entries; evicted oldest: %r", self._MAX_WORDS, evicted_word.word)`. `evicted_word.word` is actual transcribed speech from `WordTiming.word`. PIIRedactionFilter only redacts emails/phones/SSN/credit-cards/API-keys/URL-credentials; arbitrary user speech (passwords, names, addresses) passes through unredacted. Fires when user records >10,000 words in one session, regardless of `log_transcriptions=False` default.
- **Root cause**: Word-content log not gated by `log_transcriptions` config.
- **Impact**: User speech content lands in `voice-typer.log` even when user explicitly disabled transcript logging.
- **Proposed fix**: Gate word-content log on `self._app.config.log_transcriptions`, OR drop `evicted_word.word` from format string and log only `"[STREAMING] Word list exceeded %d entries; evicted oldest (idx=%d)"`. If word needed for debugging, log under `log.debug` only (still gated).
- **Confidence**: High
- **Source**: R13

### S2-CR-75 — Electron main process has NO structured logger (50+ `console.warn` lines, lost in packaged GUI)
- **Severity**: High
- **Status**: Pending
- **Category**: Observability
- **Location**: `voice_typer/client/src/main/python/start-python.ts:61,95,101,169`; `tcp-connect.ts:52,55,188,192`; `windows/bubble-window.ts:61,107,128,133,136,145,151,155,165,174,183,196,237,249,287,303`; `python/handle-message.ts:42,47,53,90`; `python/relaunch-app.ts:32,51,104,110`; `bootstrap.ts:60`; `index.ts:236`
- **Evidence**: ~50 `console.warn` calls across 12 Electron main-process files. `voice_typer/client/src/main/logging.ts` provides only ANSI color constants + timestamp helper — not a logger. Every lifecycle message (Python spawn/connect, bubble show/hide, window creation, restart cycle, TCP retry) emitted via `console.warn` because `console` has no `info` method.
- **Root cause**: No structured logger added to Electron main.
- **Impact**: (1) All Electron lifecycle logs only go to stderr — when packaged as Windows GUI app, stderr is attached to hidden console/dev/null, messages LOST in production. (2) WARN level reserved for actual warnings by Python backend; Electron side drowns out genuine warnings. (3) No correlation with Python sidecar's session_id/correlation_id.
- **Proposed fix**: Add structured logger to `voice_typer/client/src/main/logging.ts` that wraps `console` and writes (a) coloured stderr for TTY/`--port` mode and (b) rotating file at `<configDir>/electron-voice-typer.log` (mirroring `src-tauri/src/platform/logging.rs`). Use `info()` for lifecycle events, `warn()` for fallbacks/retries, `error()` for failures. Propagate Python session_id into Electron log lines.
- **Confidence**: High
- **Source**: R13

### S2-CR-76 — Handler errors logged at WARNING without operation parameters (no model name, no backend name)
- **Severity**: High
- **Status**: Pending
- **Category**: Observability
- **Location**: `voice_typer/server/handlers/config_handlers.py:104,109`; `voice_typer/server/model_manager.py:680`; `voice_typer/server/electron_launcher.py:133`; `voice_typer/server/server_platform/desktop_shortcut.py:331`
- **Evidence**: `log.warning("[IPC] change_model failed: %s", e)` — no model name. `log.warning("[IPC] set_active_backend failed: %s", e)` — no backend name. `log.warning("[MODEL] %s model failed to load", new_backend.title())` — no reason. `log.warning("[LAUNCHER] Build failed; will try npm run dev")` — no reason.
- **Root cause**: Operation parameters not passed into log message.
- **Impact**: Operator gets "change_model failed" with no hint WHICH model failed — has to grep adjacent log lines.
- **Proposed fix**: Pass relevant context into every "failed" log: `log.warning("[IPC] change_model(model_size=%s) failed: %s", validated["model_size"], e)`. Standard rule: every "failed" log MUST include (a) operation name, (b) inputs, (c) underlying exception.
- **Confidence**: High
- **Source**: R13

### S2-CR-77 — change_model mutates config + saves WITHOUT `_config_mutation_lock` (race with concurrent set_config)
- **Severity**: High
- **Status**: Pending
- **Category**: Concurrency & race conditions / Data integrity
- **Location**: `voice_typer/server/model_manager.py:555-623` (`change_model`/`_change_model_impl`) + `voice_typer/server/handlers/config_handlers.py:100-119` (`_handle_set_config`)
- **Evidence**: `_handle_set_config` calls `change_model` BEFORE `apply_config` acquires `_config_mutation_lock`. `_change_model_impl` mutates `app.config.asr_backend`/`model_size` and calls `app.config.save()` (disk write) under `_model_change_lock` (RLock), NOT `_config_mutation_lock`.
- **Root cause**: change_model holds `_model_change_lock` for entire unload/load cycle but does NOT acquire `_config_mutation_lock`.
- **Impact**: Two concurrent set_config IPC calls (or set_config racing with `_open_config_file`'s reload path, which DOES hold `_config_mutation_lock`) can interleave disk writes non-deterministically. config.json on disk can end up with torn state — e.g. `asr_backend="qwen"` but `model_size="medium"` (whisper size).
- **Proposed fix**: Either (a) acquire `_config_mutation_lock` inside change_model (in addition to `_model_change_lock`), or (b) move config mutation + save OUT of change_model and into apply_config (which already holds the lock).
- **Confidence**: Medium
- **Source**: R14

### S2-CR-78 — `active_transcriber()` reads legacy engine fields without `_model_change_lock` (race with change_model)
- **Severity**: High
- **Status**: Pending
- **Category**: Concurrency & race conditions
- **Location**: `voice_typer/server/model_manager.py:179-190` (`active_transcriber`) + `:136-178` (`_sync_registry_from_fields`)
- **Evidence**: `active_transcriber()` is called from transcription thread (DictationPipeline._transcribe), toggle thread (RecordingController._toggle_impl), and streaming worker. It reads three legacy engine fields (`self.transcriber`, `self._qwen_engine`, `self._parakeet_engine`) WITHOUT holding `_model_change_lock`. Concurrent `change_model` mutates these fields under lock.
- **Root cause**: active_transcriber doesn't acquire `_model_change_lock`.
- **Impact**: Torn read can return engine that change_model has just unloaded → RuntimeError → user-visible "Transcription failed" toast.
- **Proposed fix**: Acquire `_model_change_lock` (read-mode) in `active_transcriber`, OR have `active_transcriber` go through `registry.get_active()` only (drop the `_sync_registry_from_fields` call — registry is already source of truth).
- **Confidence**: Medium
- **Source**: R14

### S2-CR-79 — `_pending_tcp` snapshot silently dropped on write failure / shutdown short-circuit
- **Severity**: High
- **Status**: Pending
- **Category**: Concurrency & race conditions / Reliability
- **Location**: `voice_typer/server/ipc_server.py:2078-2225` (`_send` pending-tcp snapshot/drop logic)
- **Evidence**: `_send` snapshots `_pending_tcp` (clearing it under the lock) then if TCP write fails (dead client) or shutdown short-circuit fires, snapshot is NOT re-merged into `_pending_tcp`. Events silently dropped. Drain cap of 100 also means up to 900 of 1000 pending events dropped per `_send` call.
- **Root cause**: Snapshot is cleared before write success confirmed.
- **Impact**: Up to 1000 queued IPC events (including transcription_partial, vocabulary_suggestion, audio_clip) can be silently lost when TCP client dies or during shutdown.
- **Proposed fix**: Re-merge pending snapshot into `_pending_tcp` on write failure (under the lock), so next reconnect flushes them. For shutdown path, accept the drop (events are stale by definition).
- **Confidence**: High
- **Source**: R14

### S2-CR-80 — Three platform branches in `_open_config_file` duplicate lock+reload pattern
- **Severity**: High
- **Status**: Pending
- **Category**: Refactoring / Code quality
- **Location**: `voice_typer/server/app.py:749-852`
- **Evidence**: 104-LOC method. macOS (815-831) and Linux (832-849) branches contain verbatim ~14-line blocks: `with self._config_mutation_lock: with contextlib.suppress(Exception): subprocess.run([...], check=False); try: self.config = type(self.config).load() except Exception as exc: log.warning(...)`.
- **Root cause**: Three platform branches each independently implement "acquire lock → run editor → reload config" pattern.
- **Impact**: 28 lines of duplicated code. Change to "how to reload config after editor" must be made in two places.
- **Proposed fix**: Extract `_reload_config_under_lock(self)` that does try/except reload. Each platform branch calls it after platform-specific editor launch.
- **Confidence**: High
- **Source**: R8

(Full Medium/Low findings below — same FINDING block format. To save space in this file, only IDs and titles are listed for the remaining 153 findings; full blocks available in the R-agent reports.)

---

# MEDIUM findings (97 total — must fix)

**Architecture / Code quality (15)**: M-1 `apply_config_side_effects` should be private on ServiceProtocol · M-2 Vocabulary vs Templates handlers inconsistent validation · M-3 `event_bus` direct-push bypasses RT-thread deferral · M-4 ALLOWED_COMMANDS vs `_COMMAND_REGISTRY` manually synced · M-5 `dictation_pipeline._hide_or_idle_bubble` duplicated 4× · M-6 `dictation_pipeline._notify_failure_once` duplicated 4× · M-7 `dictation_pipeline._check_resources` (161 LOC) and `_copy_and_paste` (155 LOC) too long · M-8 `asr_registry.create` has 3 mutually-exclusive `*_kwargs` params · M-9 `history_db` 8 methods take `raise_on_error: bool = False` boolean flag · M-10 `service.get_config` and `get_defaults` duplicate 13-line keyring_status block · M-11 51 inline `try/except ImportError` patterns across 32 files (should use `_lazy_import`) · M-12 `_RateLimiter.reject` is a no-op kept for source-level test · M-13 `recorder._process_audio_chunk` 432 LOC · M-14 `level_monitor.start_test_recording` 120 LOC with 2 verbatim setup blocks · M-15 `config.py` mixes 5 concerns (IO/paths/migrations/dataclass/defaults).

**Performance / Resource (8)**: M-16 RNNoise round-trip resample 16k↔48k (1-3% CPU) · M-17 `level_monitor` allocation-heavy RMS computation (120-180 allocs/sec) · M-18 `level_monitor` config wiring missing `noise_suppression_method` field (always uses RNNoise + full dynamics) · M-19 `recorder._process_audio_chunk` uses `np.count_nonzero` instead of `.any()` · M-20 `level_monitor` holds `_monitor_lock` during full filter chain · M-21 `volume_backends._lock` declared but never acquired (dead code) · M-22 `_deferred_executor` ThreadPoolExecutor never shut down · M-23 Three thread-spawn sites in `recorder` for device-disconnect (not registered with thread_registry).

**Reliability / Scalability (8)**: M-24 History DB retention runs ONCE at startup (no periodic sweep) · M-25 `_pending_tcp` cap=1000 drops content-bearing events (transcription_final, vocabulary_suggestion) under high-frequency bubble_level spam · M-26 History DB queue overflow silently discards fire-and-forget writes (no user notification) · M-27 Qwen `transcribe()` releases lock during inference — race with change_model · M-28 FT-1 supervisor fixed backoff with no jitter, no circuit breaker · M-29 TCP worker pool `max_workers=4` may delay legitimate connections · M-30 Recorder `_handle_device_disconnect` no backoff between retries · M-31 Heartbeat timeout 120s holds mic + ducked volume too long after Electron crash.

**UX / Discoverability / Product Experience (20)**: M-32 "Auto Duck Volume" / "Duck Level" labels are audio-engineering jargon · M-33 Models.tsx `Save Key` button vs Settings debounced auto-save (inconsistent API key UX) · M-34 Settings → Troubleshooting and About → Resources overlap "Report a Bug" · M-35 Templates.tsx uses native `title` tooltip vs `InfoTooltip` component elsewhere · M-36 Settings → Troubleshooting "Re-run Setup Wizard" and "Reset to Defaults" share RefreshIcon · M-37 Vocabulary.tsx count footer uses `text-[10px] opacity-50` (WCAG violation, fixed in Settings but not Vocabulary) · M-38 ConfirmDialog default `title = t("common.confirm")` but no `common.confirm` key in any locale · M-39 Microphone.tsx "Active" badge misleading (mic not actually recording) · M-40 Templates.tsx has no count footer (Vocabulary has one) · M-41 Vocabulary autoDetectDesc references internal category names (circular) · M-42 App.tsx "Retry Connection" button has no in-flight feedback · M-43 Settings → Privacy "Agree to All" banner dense legal prose · M-44 Onboarding "Get Started" button same variant as "Continue" (no visual finish line) · M-45 Microphone.tsx empty state uses `opacity-30` (WCAG 1.4.11 violation) vs EmptyState.tsx `opacity-50` · M-46 Templates add/edit dialog matchMode Select has no InfoTooltip · M-47 About → Privacy and Settings → Privacy & Consent duplicate same topics with different wording · M-48 Onboarding step 3 doesn't warn about large model download size · M-49 Onboarding hotkey step doesn't mention custom hotkeys available in Settings · M-50 Onboarding step 4 says "may take a minute" — for medium.en it's 20+ minutes.

**Accessibility / i18n (10)**: M-51 SearchField missing `aria-label` and `<label>` association · M-52 Home.tsx transcription preview `<p>` has no `aria-live` (screen-reader users never hear transcription) · M-53 `useSnackbar.showUndoableToast` defaults undo label to English "Undo" · M-54 `History.tsx` `toLocaleString()` uses host locale not app locale + hardcoded "chars" suffix · M-55 TitleBar.tsx back/forward SVGs hardcoded left/right arrows (don't flip in RTL) · M-56 App.tsx rounded-l-xl + border-r-0 use physical-direction CSS (don't flip in RTL) · M-57 Bubble.tsx `ml-1` uses physical margin (doesn't flip in RTL) · M-58 HotkeyPicker `aria-label` default is literal English "Hotkey picker" · M-59 a11y tests use source-inspection (regex on file contents) instead of behavioral · M-60 axe-core test disables color-contrast rule (no runtime verification).

**Data integrity / Configuration (6)**: M-61 history_db migration not wrapped in explicit transaction (partial migration becomes permanent) · M-62 templates `save_templates` mutates in-memory BEFORE persist; failure swallowed · M-63 vocabulary CRUD methods mutate in-memory BEFORE persist; failure swallowed · M-64 `crash_recovery._load` uses `Path.read_text()` (follows symlinks; write side uses `_secure_atomic_write`) · M-65 `migrate.rs:merge_config` uses `std::fs::write` (non-atomic truncate+write) · M-66 Config schema_version preserved on load but unknown fields dropped — downgrade-then-upgrade round-trip loses v3+ fields.

**Logging / Observability (5)**: M-67 Three `except Exception: log.debug` in `startup_sequence.py` swallow exceptions with no `exc_info=True` · M-68 `level_monitor` hot-path error uses raw `log.debug` instead of `log_rate_limited` · M-69 VEH crash handler writes only exception metadata (no MemoryHandler ring buffer) · M-70 TCP retry `console.warn` spam during slow startup (no rate limiting) · M-71 `_pending_tcp` cap-exceeded warning not rate-limited.

**Testing infrastructure (3)**: M-72 tests/tauri/conftest.py asyncio-marker block redundant (pyproject has `asyncio_mode = "auto"`) · M-73 No `flaky` marker / no `pytest-rerunfailures` installed · M-74 `tests/test_e2e_pipeline.py` `E2EMockApp.__init__` writes `os.environ` directly (env-leak hazard).

**Build / CI / Packaging (8)**: M-75 macOS signing order implicit (relies on `cargo tauri build` internal signing) · M-76 Nuitka version unpinned on macOS/Linux (Windows pins `nuitka==2.5.4`) · M-77 CI uses `uv pip install --system ".[test]"` not `uv pip sync requirements-lock.txt` (no hash verification) · M-78 Windows Tauri workflow has `actions/setup-node` but no `npm ci` step · M-79 Windows Tauri workflow signs NSIS but not MSI (enterprise GPO deployments blocked) · M-80 `tauri.conf.json` `bundle.windows.signCommand: "${WIN_SIGN_COMMAND}"` — env var never set · M-81 `electron-builder.yml` declares `publish: github` but every CI invocation passes `--publish never` · M-82 mypy defined in `.pre-commit-config.yaml` but never run in CI.

**Dependency / Dead code (5)**: M-83 Dead `try/except ImportError` branches for 4 now-required deps (torch, huggingface_hub ×3, websockets) · M-84 Speex backend scaffolded but never wired into `process()` (UI exposes option that silently does nothing) · M-85 `"accelerate"` entry in mypy overrides list (dep removed) · M-86 `SINGLE_KEY_PRESETS` / `COMBO_PRESETS` deprecated exports, no callers · M-87 Deprecated config fields still shipped in `get_status()` IPC payload + dead `volume_duck_smart` update branch in `service.py:1158-1164`.

**Audio / Hotkey / Tray / Cross-platform (5)**: M-88 Wayland hotkey socket falls back to `/tmp/voice-typer-hotkey.sock` (world-writable, symlink attack surface) · M-89 Linux reserved hotkey list missing `<super>`, `<alt>+<f2>`, `<ctrl>+<alt>+<f1>..<f7>` · M-90 Ring-buffer overflow drops audio silently (no real-time notification) · M-91 `electron-vite` pinned to `6.0.0-beta.1` (pre-release) · M-92 `requirements-lock.txt` has duplicate `huggingface-hub==0.36.2` entry.

**Type safety (1)**: M-93 `providers.ServiceLocator` has 8 `Any` attrs (should be concrete types) · `native_adapter._NativeBackendAdapter.__init__(self, native_backend)` untyped · `vad_processor.VadProcessor.__init__(config: Any, vad_check_available_fn: Any | None)`.

**Concurrency (2)**: M-94 `_esc_pending_capture_exit` plain bool mutated from 3 threads without lock (check-then-act race) · M-95 `sidecar_ws._ready_emitted` read-then-write with no lock (duplicate ready event tolerated but racy).

**Misc (1)**: M-96 CONTRIBUTING.md coverage threshold says "60%" in 4 places (actual is 65%) · M-97 CHANGELOG.md "npm pins fixed" entry stale (claims typescript@^5.6.0, vite@^6.0.0, @types/node@^22.0.0; actual is typescript@^7.0.2, vite@^8.1.4, @types/node@^26.1.1).

---

# LOW findings (49 total — may defer with `Won't Fix` + rationale)

L-1 through L-49 (full list available in R-agent reports): includes items like `_hf_download_cache` no per-entry eviction (negligible), `tray_icon_cache` bounded by design, `_buffer_clear_queue` synchronous fallback acceptable, `_send_ctrl_v_win32` magic numbers (VK codes inline), `ipc_server` inline tunables, shadow declarations for tests, `use` statement ordering in `main.rs`, devcontainer prettier-vscode config (project uses Biome), `docs/dublicated-text.md` misspelling, ADR 0002/0003 duplicate titles, etc.


### S3-CR-1 — `_send_ctrl_v_win32` returns `None`; `paste()` always reports failure on Windows
- **Severity:** Critical (user-facing incorrect failure state on every Windows paste)
- **Status:** Pending
- **Locations:** `voice_typer/server/clipboard.py:1292, 1191-1201, 1389, 1397`
- **Evidence:** `_send_ctrl_v_win32(self) -> None` has no `return True/False` on success or partial-success paths. Caller at L1191 does `paste_succeeded = self._send_ctrl_v_win32()` then `if not paste_succeeded:` logs a false warning and `paste()` returns False on every Windows paste.
- **Root cause:** CLIP-14 fix updated the consumer but never the producer.
- **Impact:** Every Windows auto-paste logs a spurious "Auto-paste failed" warning. Crash-recovery's `mark_latest_pasted()` is skipped (entries stay marked unpasted). UI shows error toast even though transcription IS pasted.
- **Proposed fix:** Change signature to `-> bool`. Add `return True` after successful SendInput(4,...). Add `return False` on partial-success branch. Add `return True/False` on pynput fallback. Add an integration test that does NOT mock `_send_ctrl_v_win32` and asserts `paste()` returns True on SendInput-returns-4 path.
- **Confidence:** High (R2, R18 — duplicated finding, merged)

### S3-CR-2 — FT-1 supervisor race with shutdown — zombie sidecar
- **Severity:** Critical (zombie process holding mic + named mutex)
- **Status:** Pending
- **Locations:** `src-tauri/src/sidecar/ft1.rs:42-100, 68-69`
- **Evidence:** `ft1_respawn_inner` checks `state.shutting_down` at top of loop, sleeps up to 5000ms, then `spawn_sidecar_and_get_port(...)` WITHOUT re-checking `shutting_down` after sleep. `shutdown_sidecar` sets `shutting_down=true` and force-kills the OLD child, but FT-1 then wakes and spawns a NEW sidecar.
- **Root cause:** Missing second `shutting_down` check after sleep / before spawn.
- **Impact:** Zombie sidecar process after window-close-during-FT-1-recovery. New sidecar holds mic, native hotkey binary, Windows named mutex → next launch hits `ERROR_ALREADY_EXISTS`.
- **Proposed fix:** Re-check `state.shutting_down` after sleep (return Ok if set). Also: kill the old child before installing the new one (related S3-CR-28). Add a `Drop` guard to clear `respawn_in_progress` on panic.
- **Confidence:** High (R5, R12, R13)

### S3-CR-3 — 65+ existing test failures (CI red)
- **Severity:** Critical (CI cannot validate new changes)
- **Status:** Pending
- **Locations:** `tests/test_tray.py` (30), `tests/test_app.py` (10), `tests/test_clipboard_win32_coverage.py` (3), `tests/test_config.py` (4), `tests/test_history_db.py` (2), `tests/handlers/test_system_handlers.py` (1), `tests/regressions/i18n_test.py` (1), `tests/regressions/security_test.py` (1), `tests/test_remaining_fixes.py` (1), `tests/tauri/mig17/test_native_key_listener_linux.py` (3 — TestSidecarOwnership), `tests/tauri/mig19/test_phase4_validation.py` (2 — frozen command registry), `tests/test_i18n_completeness.py` (13 — parity tests), `tests/test_security_doc_command_count.py` (1), `tests/test_electron_ipc_and_build.py` (1 — allowlist parity)
- **Evidence:** Reproduced by R14, R19, R20 — running targeted pytest subsets shows ~65 failures across multiple files. Tests pin private symbols/methods that have been renamed/removed/moved during refactors.
- **Root cause:** Source/tests drift; new commands added without updating frozen allowlists; i18n keys added to en.json without backfilling locales; native_hotkeys refactored from module to subpackage without updating test path assertions.
- **Impact:** CI is red. New refactors can't be validated. Tests provide negative value.
- **Proposed fix:** Per-file triage: restore missing symbols OR update tests if source was intentionally refactored. Backfill 25 missing i18n keys per locale (or add to `RW2_BACKFILLED_PENDING_TRANSLATION`). Update frozen command tables in `test_phase4_validation.py`. Update SECURITY.md command count. Fix native_hotkeys path assertion.
- **Confidence:** High (R14, R19, R20)

### S3-CR-4 — Linux Tauri bundle missing permission-setup scripts (hotkeys broken)
- **Severity:** Critical (Linux .deb/.rpm install silently breaks native hotkeys)
- **Status:** Pending
- **Locations:** `src-tauri/tauri.conf.json:58-68` (base bundle.resources); `src-tauri/tauri.linux-x86_64.conf.json`; `src-tauri/tauri.linux-aarch64.conf.json`; `scripts/linux/postinst:30-46`
- **Evidence:** `bundle.resources` arrays list ONLY native key-listener binaries + prewarm binaries. They omit `scripts/linux/install_permissions.py`, `uninstall_permissions.py`, `99-voice-typer.rules`, `00-voice-typer-capslock.conf`, `voice-typer.polkit`. The postinst probes 5 candidate paths, all empty on Tauri v2 install — emits "WARNING: install_permissions.py not found" and exits 0.
- **Root cause:** NF-R9-2 fix assumed the scripts would be bundled; bundling step was never wired.
- **Impact:** Every Linux Tauri install silently skips input-group setup, udev rule, Caps Lock neutralization. Bundled `linux-key-listener` cannot read `/dev/input/event*` → native hotkeys fail with no error message.
- **Proposed fix:** Add the 5 permission files to `bundle.resources` in both `tauri.linux-x86_64.conf.json` and `tauri.linux-aarch64.conf.json`, mapping to `resources/scripts/linux/`. The postinst path probe will then resolve.
- **Confidence:** High (R17)

### S3-CR-5 — `prerm` hardcoded to legacy Electron path; never finds uninstall script
- **Severity:** Critical (uninstall leaves udev rules + XKB config orphaned)
- **Status:** Pending
- **Locations:** `scripts/linux/prerm:12`
- **Evidence:** `UNINSTALL_SCRIPT="/usr/share/voice-typer/scripts/uninstall_permissions.py"` — legacy Electron-builder install path. Tauri v2 installs to `/usr/lib/voice-typer/...`. No fallback list (unlike postinst). `[ -f "$UNINSTALL_SCRIPT" ]` always fails on Tauri v2.
- **Root cause:** Asymmetric path handling between postinst (5-path probe) and prerm (1 hardcoded path).
- **Impact:** Uninstalling the Tauri .deb/.rpm leaves udev rule and XKB config in place forever.
- **Proposed fix:** Mirror the postinst's 5-path probe in prerm. Once S3-CR-4 is fixed (script is bundled), the prerm path will resolve.
- **Confidence:** High (R17)

### S3-CR-6 — `noise_suppressor.py` deepfilternet/speex paths are silent passthrough
- **Severity:** Critical (users in noisy environments get ZERO noise suppression)
- **Status:** Pending
- **Locations:** `voice_typer/server/audio_filters/noise_suppressor.py:141-152, 91-118, 120-139`; `voice_typer/server/audio_presets.py:50-58`
- **Evidence:** `process()` only wires the `rnnoise` branch. When `noise_suppression_method == "deepfilternet"` (selected by the `noisy_room` preset) AND `deepfilternet` is installed, `_init_deepfilternet` succeeds, `self._method` stays "deepfilternet", but `process()` falls through to passthrough — ZERO noise suppression. Same for `speex`. `is_degraded` is NOT set, so UI shows filter as active.
- **Root cause:** Comment in source says "backends initialized but not yet wired".
- **Impact:** Users in noisy environments (primary use case for "noisy_room" preset) get NO neural noise suppression. Transcription accuracy substantially worse than promised.
- **Proposed fix:** Either (a) actually implement `_process_deepfilternet` and `_process_speex`, OR (b) fall back to `rnnoise` at `_init_*` time and set `is_degraded=True` with clear `degraded_reason` so UI warns user, OR (c) remove `deepfilternet`/`speex` options from `audio_presets.py` and Settings UI until wired.
- **Confidence:** High (R4, R18)

### S3-CR-7 — Tauri tray menu checkmarks don't render (`"✓"` used as accelerator)
- **Severity:** Critical (users can't see which microphone is selected from tray)
- **Status:** Pending
- **Locations:** `src-tauri/src/tray.rs:75-79`
- **Evidence:** `let check: Option<&str> = item.checked.map(|c| if c { "✓" } else { "" });` then `b = b.accelerator(acc);`. Tauri v2's `MenuItemBuilder::accelerator` expects accelerator syntax like `"Cmd+Q"`. Using `"✓"` either silently ignored or errors at menu-build. Checkmark won't render.
- **Root cause:** Tauri v2 menu API distinguishes accelerators from checkmarks; this code conflates them.
- **Impact:** On Tauri runtime, "active microphone" checkmark (the `checked=True` items in microphones submenu) doesn't render. Users can't see which mic is currently selected.
- **Proposed fix:** Use `tauri::menu::CheckMenuItem::with_id(app, &item.id, &item.label, item.checked.unwrap_or(false), !item.disabled)` for items where `checked.is_some()`. Use `MenuItemBuilder::with_id(...)` for the rest.
- **Confidence:** High (R5, R18)

### S3-CR-8 — Tauri tray right-click races with menu display (focus-steal)
- **Severity:** Critical (right-click menu unreliable on Windows/Linux)
- **Status:** Pending
- **Locations:** `src-tauri/src/tray.rs:126-133`
- **Evidence:** `TrayIconEvent::Click { .. }` matches left, right, AND middle click without filtering. Handler calls `window.show()` + `set_focus()` on every click. On right-click (which should open context menu), this races with menu display — window steals focus from menu.
- **Root cause:** `TrayIconEvent::Click` in Tauri 2 carries `button: MouseButton` field that this code ignores.
- **Impact:** Right-clicking tray icon to open menu may flash/fail to focus correctly on Windows and Linux; main window pops up unexpectedly on every right-click.
- **Proposed fix:** Match on `TrayIconEvent::Click { button: MouseButton::Left, .. }` only. For right click, do nothing (Tauri auto-opens menu on right-click when `show_menu_on_left_click(false)`).
- **Confidence:** High (R18)

### S3-CR-9 — Password-field detection is Windows-only (macOS/Linux paste into password fields)
- **Severity:** Critical (security — voice dictation into password fields on macOS/Linux)
- **Status:** Pending
- **Locations:** `voice_typer/server/clipboard_target_safety.py:285-376` (whole module); `voice_typer/server/dictation_pipeline.py:890-1046`
- **Evidence:** `_is_password_field` returns False immediately on non-Windows (`if not is_windows(): return False`). No macOS equivalent (no `NSSecureText`/`AXIsTextFieldSecure` check). No Linux equivalent (no AT-SPI `password` role check). On macOS/Linux, `_is_safe_paste_target` only checks `_is_elevated_target` (also Windows-only).
- **Root cause:** Entire `clipboard_target_safety` module is gated on `is_windows()` at every entry point.
- **Impact:** On macOS, dictation can paste transcribed text into Keychain Access prompts, Safari password fields, 1Password master-password prompts, sudo GUI dialogs. On Linux, same for polkit prompts, browser password fields, KeePassXC unlock dialogs. Voice dictation into password field is both a leak (transcription ends up in clipboard history + DB) and a UX hazard.
- **Proposed fix:** Add macOS path using NSWorkspace + Accessibility API to detect `AXIsSecureTextField` (or shell out to `osascript` to query frontmost app's focused element). Add Linux path using `pyatspi` (AT-SPI2) to query focused accessible's `password` role/state. Gate the same way as Windows (fail-closed for known credential UIs, fail-open otherwise). Until then, document the platform gap in user-facing docs.
- **Confidence:** High (R18)

### S3-CR-10 — `templates {clipboard}` substitution → LLM API exfiltration (privacy)
- **Severity:** Critical (silent clipboard exfiltration to LLM API)
- **Status:** Pending
- **Locations:** `voice_typer/server/templates.py:40-57` (`substitute_variables`); `voice_typer/server/dictation_pipeline.py:164-174, 622, 662`
- **Evidence:** `substitute_variables` replaces `{clipboard}` with `pyperclip.paste()` — user's CURRENT clipboard contents. Pipeline order: templates → ... → LLM polish. If user has template like "Note: {clipboard}" and LLM polish enabled with consent, substituted clipboard content is sent to OpenAI/Groq/Anthropic API as user-content. Clipboard may contain passwords, 2FA codes, private messages.
- **Root cause:** No allowlist or redaction gate between template substitution and API send.
- **Impact:** Silent clipboard exfiltration to third-party LLM API whenever (a) user has template with `{clipboard}` AND (b) `llm_polish` + `llm_polish_consent` are both True. `redact_pii` filter runs on LOG output but NOT on API payload.
- **Proposed fix:** Either (a) strip `{clipboard}` substitution when `config.llm_polish and config.llm_polish_consent` are both True, OR (b) apply `redact_pii` to the text before sending to the LLM API in `llm_polish._call_api`. Option (b) is more defense-in-depth.
- **Confidence:** High (R3)

### S3-CR-11 — Auto-update check phones home to GitHub on Settings mount (no consent, breaks offline guarantee)
- **Severity:** Critical (privacy — leaks user IP + UA on every Settings page open)
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/components/settings/PrewarmAndUpdates.tsx:202, 269`; `voice_typer/client/csp-plugin.ts:52, 71`
- **Evidence:** `checkForUpdate` invoked inside `useEffect` on mount of Settings section, calls `fetch("https://api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest")`. CSP `connect-src` whitelist explicitly grants `https://api.github.com`. No consent gate (no `auto_update_check_consent` flag in Config); no opt-out toggle.
- **Root cause:** Auto-update check fired on Settings-page mount, not only on explicit "Check for Updates" click.
- **Impact:** Every Settings page open leaks user's public IP, request timestamp, Electron User-Agent. Breaks the "offline guarantee" the project advertises.
- **Proposed fix:** Either (a) gate auto-check behind new `Config.auto_update_check_consent` flag (default False), OR (b) remove `useEffect` auto-call and only run check on explicit button click. Add regression test in `test_consent_and_privacy.py` asserting no fetch fires on mount when consent is False.
- **Confidence:** High (R11)

### S3-CR-12 — `requirements-lock.txt` missing `websockets` + `keyring` (install produces broken runtime)
- **Severity:** Critical (documented install path produces broken app)
- **Status:** Pending
- **Locations:** `requirements-lock.txt`; `pyproject.toml:137, 146`
- **Evidence:** `pyproject.toml` declares `websockets>=12.0,<14.0` and `keyring>=25.0,<26.0` as core deps. Neither appears in `requirements-lock.txt` (1353 lines, 62 pinned packages). `requirements.txt` includes both. The lockfile is the documented install path (`pip install --require-hashes -r requirements-lock.txt`).
- **Root cause:** pip-compile regen produced incomplete lockfile.
- **Impact:** `pip install --require-hashes -r requirements-lock.txt` succeeds, but `import websockets` (in `sidecar_ws.py`, Tauri sidecar transport) and `import keyring` (in `credential_store.py`, OS-native credential storage) raise `ModuleNotFoundError` at runtime. Sidecar dies on first start when running on Tauri path; API-key storage silently falls back to plaintext.
- **Proposed fix:** Re-run `uv pip compile --generate-hashes -o requirements-lock.txt pyproject.toml` in a clean environment. Verify output contains both `websockets` and `keyring` with hashes. Add CI step that diffs lockfile's pinned package set against pyproject.toml's `[project.dependencies]` and fails on drift.
- **Confidence:** High (R16)

### S3-CR-13 — `aarch64` Linux Tauri config missing `native/linux-key-listener`
- **Severity:** Critical (aarch64 Linux users have no native hotkeys)
- **Status:** Pending
- **Locations:** `src-tauri/tauri.linux-aarch64.conf.json:1-8`
- **Evidence:** The aarch64 overlay lists ONLY `resources/prewarm-aarch64-unknown-linux-gnu` — no `native/linux-key-listener`. x86_64 overlay correctly includes both. Tauri v2's per-platform config merge REPLACES arrays, so all 9 base resources are dropped except the one prewarm binary.
- **Root cause:** aarch64 overlay was authored by copying x86_64 overlay but the `native/linux-key-listener` entry was lost.
- **Impact:** aarch64 Linux users (Raspberry Pi 4/5, Ampere Altra, AWS Graviton) get Tauri .deb/.rpm with NO native key-listener. App falls back to pynput (X11-only) — no Wayland support, no modifier-only hotkeys. No installer warning.
- **Proposed fix:** Add `"resources/native/linux-key-listener"` to `tauri.linux-aarch64.conf.json`'s `bundle.resources` array. Mirror x86_64 structure. Add CI test that parses all `tauri.linux-*.conf.json` overlays and asserts each includes `native/linux-key-listener`.
- **Confidence:** High (R17, R20)

---

## HIGH FINDINGS

### S3-CR-14 — `ipc/` package is dead parallel implementation of `ipc_server.py` (5436 LOC duplication)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/ipc/` (9 files, 2981 LOC); `voice_typer/server/ipc_server.py` (2609 LOC)
- **Evidence:** `ipc/__init__.py` docstring claims the package was split from `ipc_server.py` — but `ipc_server.py` was never converted to a shim and still contains the full implementation. `pyproject.toml:157` declares `voice-typer = "voice_typer.server.ipc_server:main"` — production uses the monolith. No production or test code imports `voice_typer.server.ipc.server.IPCServer`. The two `IPCServer` class bodies have already drifted (845-LOC delta).
- **Root cause:** Phase 4.5 / ARCH-045 extraction was started but never completed.
- **Impact:** 5436 LOC of duplicated IPC infrastructure. Bug fixes applied to one don't propagate. Maintainers reading `ipc/__init__.py` believe split is done.
- **Proposed fix:** DELETE the `ipc/` package entirely (it's unused). Accept `ipc_server.py` as the canonical implementation. Document the decision in `voice_typer/server/ipc_server.py` header. Update `tests/test_server.py:37` import if needed.
- **Confidence:** High (R1)

### S3-CR-15 — `app.py:967` restart log missing `APP_NAME` arg (test failing)
- **Severity:** High (CI red — test pins this)
- **Status:** Pending
- **Locations:** `voice_typer/server/app.py:967`; `tests/test_app.py:2497-2514, 2516`
- **Evidence:** `log.info("[RESTART] Restarting %s...")` with NO format argument. The `%s` placeholder survives verbatim into log. `tests/test_app.py::test_restart_log_format_string_has_argument` asserts `"APP_NAME" in line` — VERIFIED FAILING.
- **Root cause:** `APP_NAME` argument was dropped from `log.info` call during RW-9 Phase 7 refactor.
- **Impact:** User-visible: restart log line shows `Restarting %s...` instead of `Restarting Voice Typer...`. 2 red tests.
- **Proposed fix:** `log.info("[RESTART] Restarting %s...", APP_NAME)`. One-line fix.
- **Confidence:** High (R1, R13)

### S3-CR-16 — `recorder.py` missing `_start_lock` (test failing, latent race)
- **Severity:** High (CI red + latent race in production)
- **Status:** Pending
- **Locations:** `voice_typer/server/recording/recorder.py`; `tests/test_recording.py:1276-1298, 1484, 1496`
- **Evidence:** `rg "_start_lock" voice_typer/server/` returns ZERO matches. `TestRec5StartLock` asserts `_start_lock` appears in `inspect.getsource(Recorder.__init__)`, `.start`, `.discard`, and that `r._start_lock` is a `threading.Lock`. VERIFIED FAILING. Tests at L1484, L1496 pin `Recorder._start_impl` source — method doesn't exist (renamed to `start`).
- **Root cause:** REC-5 `_start_lock` was either never implemented or was removed without updating test.
- **Impact:** Latent race: without `_start_lock`, concurrent `start()` and `discard()` calls can race on half-open stream state. 4 red tests.
- **Proposed fix:** Either (a) implement `_start_lock` (add `self._start_lock = threading.Lock()` to `__init__` after L249; wrap body of `start()` and `discard()` in `with self._start_lock:`), OR (b) update/delete `TestRec5StartLock` if race is acceptable. Recommend (a).
- **Confidence:** High (R1)

### S3-CR-17 — `recorder.py` 2992-LOC monolith (god class)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/recording/recorder.py` (2992 LOC)
- **Evidence:** `Recorder.__init__` = 255 LOC (L231-485), `Recorder.start` = 434 LOC (L1194-1627), `Recorder._process_audio_chunk` = 432 LOC (L2076-2507), `Recorder.stop` = 151 LOC. Class mixes device enumeration, hot-plug detection, device-health-checker thread, VAD property-shim, audio worker thread, IPC event worker thread, real-time audio callback dispatch, per-chunk heavy processing pipeline, resampling, buffer secure-clear.
- **Root cause:** Class was extracted FROM `recording.py` god-module into the `recording/` PACKAGE, but `Recorder` class itself was NOT decomposed.
- **Impact:** Any change to VAD, clipping, silence, or RMS telemetry requires editing monolith.
- **Proposed fix:** Extract cohesive helper classes that `Recorder` composes: `DeviceManager`, `AudioWorker`, `AudioPipeline`. Keep `Recorder` as thin coordinator. CRITICAL: `inspect.getsource(Recorder.X)` tests require methods to remain defined on `Recorder` class (1-line delegates are OK). Preserve `_recording_pkg.X` indirection contract.
- **Confidence:** High (R1)

### S3-CR-18 — `service.py` 2364-LOC god facade (73 methods across 9+ domains)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/service.py` (2364 LOC)
- **Evidence:** `VoiceTyperService` exposes 73 methods. Largest: `download_model` 441 LOC, `apply_config_side_effects` 227 LOC, `import_model` 147 LOC, `onboarding_apply` 105 LOC. 9 domains: history, model, onboarding, microphone_test, vocabulary, template, status, dictation, diagnostics.
- **Root cause:** Docstring says "thin facade" but is no longer thin.
- **Impact:** Single 2364-LOC file is bottleneck for every IPC handler change.
- **Proposed fix:** Split into per-domain service modules (`service_history.py`, `service_models.py`, `service_onboarding.py`, `service_microphone_test.py`, `service_config.py`, `service_templates.py`, `service_vocabulary.py`, `service_diagnostics.py`). `VoiceTyperService.__init__` constructs sub-services; 73 public methods become 1-line delegates. Tests that do `service._download_cancel_event = ...` require private attrs to remain accessible on facade (delegate via `@property` or `__getattr__`).
- **Confidence:** High (R1)

### S3-CR-19 — `ipc_server.py` 2609-LOC monolith mixing 10 concerns
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/ipc_server.py`
- **Evidence:** 10 concern boundaries: payload validation, port probing, rate limiter, sanitize_config_for_ipc, history bounds, push event shim, TCP line IO, IPCServer class (1601 LOC), process metadata, main() (336 LOC).
- **Root cause:** Never underwent per-concern split that `ipc/` package was meant to provide.
- **Impact:** Adding any new IPC command requires touching 2609-LOC file. `main()` mixes argparse, single-instance, app construction, 3 transport modes, Electron subprocess.
- **Proposed fix:** Extract within `ipc_server.py` to sibling modules: `ipc_server_transport.py`, `ipc_server_security.py`, `ipc_server_ratelimit.py`, `ipc_server_main.py`. Preserve 13 public names tests patch via re-exports. ALSO: remove `sys.modules[__main__]` mutation at L630-632 (circular-import workaround) by extracting helpers to leaf module.
- **Confidence:** High (R1)

### S3-CR-20 — `app.py` 199 monkeypatch sites depend on 27 re-exported names
- **Severity:** High (any refactor touching app.py imports risks breaking 199 test patches)
- **Status:** Pending
- **Locations:** `voice_typer/server/app.py:23-120, 1133-1179`
- **Evidence:** 27 distinct names re-exported by app.py for test-patch compatibility. 199 `monkeypatch.setattr("voice_typer.server.app.X", ...)` sites across all test files. Bottom-of-file re-exports at L1133-1179 execute AFTER VoiceTyperApp class body — hazardous.
- **Root cause:** Re-exports exist solely to keep `monkeypatch.setattr` working after underlying implementation moved to focused modules.
- **Impact:** Any refactor removing/renaming a re-exported name silently breaks 5-50 test patches.
- **Proposed fix:** Don't remove re-exports (too many test sites depend). Instead: (a) add regression test asserting every re-exported name resolves to same object as canonical module, (b) consolidate bottom-of-file re-exports to top of file, (c) add `# ruff: noqa: F401` blanket + module-level comment listing all 27 names + test files depending on each.
- **Confidence:** High (R1)

### S3-CR-21 — 164 `inspect.getsource` source-string tests across 35 files (refactor blocker)
- **Severity:** High (blocks safe refactoring of large files)
- **Status:** Pending
- **Locations:** 35 test files; 164 total `inspect.getsource()` calls
- **Evidence:** Tests pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Module-level `inspect.getsource(app)` / `inspect.getsource(service)` tests pin MODULE source text. `Path(ipc.__file__).read_text()` test (test_app.py:2472) BLOCKS converting `ipc_server.py` to shim.
- **Root cause:** Tests use source-text inspection as proxy for behavioral invariants.
- **Impact:** Extractions that MOVE methods off original class break `inspect.getsource(Recorder._process_audio_chunk)` tests. Even adding/removing comments can break module-level source-text tests.
- **Proposed fix:** For each extraction (CR-17, S3-CR-18, S3-CR-19), keep public method on original class as 1-line delegate. For module-level tests, preserve pinned literal strings in module-level comments (replicate `recording/__init__.py:229-258` "static-source check echo" pattern). Long-term: migrate source-pinning tests to behavioral tests.
- **Confidence:** High (R1, R14)

### S3-CR-22 — `event_bus.py` deferred executor never shut down (worker thread leak)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/event_bus.py:147-162`; absence of shutdown call in `shutdown_controller.py`
- **Evidence:** `_deferred_executor: ThreadPoolExecutor` created lazily with `max_workers=1, thread_name_prefix="event-bus-publisher"`. Never `shutdown()` anywhere. ThreadPoolExecutor spawns NON-daemon worker threads by default.
- **Root cause:** Module-global singleton executor with no explicit lifecycle management.
- **Impact:** On `restart_app()`, old executor orphaned + new one created → worker thread leak. On normal exit, worker thread can block up to default join timeout (~5s) slowing shutdown.
- **Proposed fix:** Add `shutdown_executor()` to `event_bus.py` calling `_deferred_executor.shutdown(wait=False, cancel_futures=True)`. Call from `shutdown_controller._do_cleanup()` after IPC server stop.
- **Confidence:** High (R3)

### S3-CR-23 — `vocabulary.py` `apply_to_text` race condition (read without lock)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/vocabulary.py:376-430`
- **Evidence:** `apply_to_text` reads `self._data.get(cat, [])` and `self._data.get(cat, {})` WITHOUT holding `self._lock`. Lock IS acquired in `add_entry`, `remove_entry`, `add_phrase`, etc. Concurrent edit while transcription is being cleaned up raises `RuntimeError: dictionary changed size during iteration`.
- **Root cause:** Lock declared at L71 with explicit purpose but read path was never wired through lock.
- **Impact:** Concurrent vocab edit + dictation → intermittent `RuntimeError` caught by `_apply_vocabulary`'s `try/except Exception` → original (uncorrected) text pasted → silent data quality degradation.
- **Proposed fix:** Wrap body of `apply_to_text` in `with self._lock:` (snapshot `_data` once under lock, then iterate snapshot outside).
- **Confidence:** High (R3)

### S3-CR-24 — `vocabulary.py` duplicates regex compilation without cache (perf)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/vocabulary.py:404, 412, 422, 425`
- **Evidence:** `apply_to_text` calls `_re.compile(_re.escape(bad), _re.IGNORECASE)` inside loop on every dictation. Sibling module `text_cleanup.py` has `_phrase_pattern_cache` (OrderedDict LRU) for same use case — vocabulary.py duplicates without caching. 500 phrases × 16 dictations/min = 8000 unnecessary `re.compile` calls/min.
- **Root cause:** text_cleanup.py was hardened with ARCH-031 / SEC-011 LRU cache; vocabulary.py was not updated to match.
- **Impact:** 1-5ms wasted CPU per dictation cycle. Scales linearly with vocabulary size.
- **Proposed fix:** Import and reuse `_get_compiled_phrase_pattern` from `text_cleanup.py`. Or factor LRU cache into shared `_phrase_pattern_cache.py` module.
- **Confidence:** High (R3)

### S3-CR-25 — `streaming.py` `_seen_timestamps` and `_word_key_index` unbounded growth (memory leak)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/streaming.py:182-198`
- **Evidence:** `_words` is `deque(maxlen=10000)` (AUDIO-019 fix), but `_seen_timestamps: set[tuple[float, float]]` and `_word_key_index: dict[str, list[int]]` have NO eviction. 30-min session at 60 words/min accumulates ~1800 entries. Dict's value-lists grow without bound for common words.
- **Root cause:** AUDIO-019 only bounded `_words`. Satellite data structures missed.
- **Impact:** ~50-200 KB leak per long streaming session. Accumulates over long-running tray-resident process.
- **Proposed fix:** When deque evicts oldest word, also remove from `_seen_timestamps` and decrement `_word_key_index[word]` (drop key when list empty).
- **Confidence:** High (R3)

### S3-CR-26 — `templates {clipboard}` privacy issue (already covered as S3-CR-10 — duplicate, merged)

### S3-CR-27 — `_validate_dict_payload` returns error envelope WITHOUT preserving request `id`
- **Severity:** High (renderer cannot match validation rejections to originating request)
- **Status:** Pending
- **Locations:** `voice_typer/server/ipc/validation.py:57-64, 82-100`; all 13 handler files using `return error` pattern
- **Evidence:** `_validate_dict_payload` returns fresh `{"type":"error","data":{...}}` dict with NO `id` field. Every handler that hits validation error does `if error: return error` — discarding `resp` passed in by `_dispatch`, which was pre-populated with `{"id": msg.get("id")}`. TCP path's `internal_error` envelope explicitly preserves `id` (B-6 fix), but validation errors bypass this.
- **Root cause:** Validation helper written before B-6 id-preservation fix.
- **Impact:** Clients using id-based request/response correlation cannot match validation rejections. Renderer's `usePython.ts` awaits by id and would time out instead of resolving rejection. Affects ~20 handler endpoints.
- **Proposed fix:** Single-point fix in `_dispatch` (server.py:1321-1327): `result["id"] = msg["id"]` after handler returns. Or have `_validate_dict_payload` accept and mutate `resp`.
- **Confidence:** High (R4)

### S3-CR-28 — FT-1 supervisor doesn't kill old child on respawn (orphaned sidecar)
- **Severity:** High
- **Status:** Pending
- **Locations:** `src-tauri/src/sidecar/ft1.rs:68-69`
- **Evidence:** On successful spawn, new child handle replaces old: `let mut child_guard = state.child.lock().unwrap(); *child_guard = Some(child);`. `SidecarHandle::ShellPlugin(CommandChild)` has no `Drop` impl that kills process. Old child handle silently dropped.
- **Root cause:** Verified. WS-reader task exit fires on WS close, which doesn't guarantee sidecar OS process has exited.
- **Impact:** Orphaned sidecar processes holding mic, native hotkey binaries, Windows named mutex. New sidecar cannot acquire mutex → "Voice Typer is already running".
- **Proposed fix:** Before assigning new child, kill old one: `if let Some(old) = child_guard.take() { let _ = old.kill_tree().await; }`.
- **Proposed fix:** Before assigning new child, kill old one: `if let Some(old) = child_guard.take() { let _ = old.kill_tree().await; }`.
- **Confidence:** High (R13)

### S3-CR-29 — FT-1 supervisor: no circuit breaker on restart loop (infinite restart loop on broken install)
- **Severity:** High
- **Status:** Pending
- **Locations:** `src-tauri/src/sidecar/ft1.rs:112-115`; `src-tauri/src/main.rs:191-206`
- **Evidence:** When FT-1 backoff exhausted, supervisor unconditionally calls `app.restart()`. No counter persists across `app.restart()` invocations. If sidecar binary missing/corrupted/crashes immediately, same failure repeats: 5 FT-1 attempts → `app.restart()` → new host → 5 FT-1 attempts → `app.restart()` → ad infinitum.
- **Root cause:** No counter persists across `app.restart()` invocations.
- **Impact:** On broken install, app enters infinite restart loop — consuming CPU, spamming log file, making machine unresponsive. User has no way to know WHY it's restarting.
- **Proposed fix:** Persist restart-attempt counter to `<config_dir>/ft1_restart_counter.json` before `app.restart()`. If counter ≥ 3, STOP loop and emit `ft1_failed` event with last spawn error so UI can show "Voice Typer could not start its backend. Last error: …. Please reinstall." Reset counter on successful `ft1_reconnected` event.
- **Confidence:** High (R13)

### S3-CR-30 — `asyncio.Queue.put_nowait` called from non-loop threads (WS corruption)
- **Severity:** High (intermittent WS frame corruption / lost events)
- **Status:** Pending
- **Locations:** `voice_typer/server/sidecar_ws.py:388-402` (`_push_to_ws` subscriber)
- **Evidence:** `event_bus.publish` called synchronously from ANY thread (audio worker, transcription thread, tray thread). `_push_to_ws` calls `outbound.put_nowait(event)` on `asyncio.Queue`. `asyncio.Queue.put_nowait` is NOT thread-safe — asyncio docs require all queue operations to run on loop thread.
- **Root cause:** asyncio Queue mutated from non-loop threads.
- **Impact:** Under contention (60 Hz bubble_level + concurrent transcription_final), queue's internal linked list can be corrupted → dropped events, stuck writer task, or `RuntimeError` taking down WS connection → FT-1 respawn. Hard to reproduce.
- **Proposed fix:** In `_push_to_ws`, capture loop at subscription time and enqueue via `loop.call_soon_threadsafe(outbound.put_nowait, event)`. Full-queue drop-oldest logic must also move into threadsafe callback.
- **Confidence:** High (R12)

### S3-CR-31 — `ipc/server.py` stdin dispatch path has narrower exception handler (silent thread death)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/ipc/server.py:1239-1264` (stdin `_run` loop); `voice_typer/server/ipc_server.py:1240-1264`
- **Evidence:** Stdin dispatch path catches only `json.JSONDecodeError` + outer `except OSError`. If `json.loads(line)` returns non-dict (e.g. `42`, `[1,2,3]` — all valid JSON), `_dispatch(msg)` calls `msg.get("type")` which raises `AttributeError`. AttributeError is neither JSONDecodeError nor OSError, escapes both `except` clauses, kills stdin thread silently. TCP path hardened by ERR-018 but stdin path was not updated.
- **Root cause:** Asymmetric error handling between TCP and stdin paths.
- **Impact:** A CLI user sending `[1,2,3]` or `42` or `"hello"` kills stdin IPC thread silently — app becomes unresponsive with no diagnostic.
- **Proposed fix:** Mirror TCP path's `try: result = self._dispatch(msg) except Exception as dispatch_exc: log.error(...); self._send({"type":"error","data":{"code":"internal_error","message":"internal error"}}, _out=stdout)`.
- **Proposed fix:** Mirror TCP path's `try: result = self._dispatch(msg) except Exception as dispatch_exc: log.error(...); self._send({"type":"error","data":{"code":"internal_error","message":"internal error"}}, _out=stdout)`.
- **Confidence:** High (R2, R4)

### S3-CR-32 — `history_db.py` migration failure not handled (partial migration masquerades as success)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/server/history_db.py:514-545`
- **Evidence:** Migration runner loops `for version in range(current_version + 1, _CURRENT_SCHEMA_VERSION + 1)` and executes each ALTER statement in try/except catching `sqlite3.Error`, logs warning, does NOT raise. After loop, `schema_meta.version` unconditionally set to `_CURRENT_SCHEMA_VERSION`. If a migration ALTER fails for non-"column already exists" reason (disk full, DB locked), version still bumped → masks partial migration.
- **Root cause:** Failure path at L537-538 logs but does not propagate or skip version bump.
- **Impact:** Partially-migrated DB (e.g. `favorite` column missing) reports schema_version=2. Future loads skip v2 migration. `row["favorite"]` raises `KeyError`. `idx_favorite` index creation fails (logged at WARNING but not raised), leaving DB without favorite index — slow favorites queries.
- **Proposed fix:** Wrap entire migration loop in single transaction. On any statement failure for non-"column already exists" reason, ROLLBACK and leave version at pre-migration value so next launch retries.
- **Confidence:** High (R2, R20)

### S3-CR-33 — `window:open-logs` handler hardcodes `~/.voice-typer` (wrong dir + stray dir created)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/main/ipc/window-handlers.ts:60`
- **Evidence:** `const logDir = path.join(os.homedir(), ".voice-typer");` — hardcoded legacy path. Every other module uses `computeConfigDir()`. Handler also calls `fs.mkdirSync(logDir, { recursive: true })` which CREATES stray legacy directory on fresh installs.
- **Root cause:** Inline comment says "Mirror voice_typer/server/config.py:_config_dir()" but code does not call `computeConfigDir()`.
- **Impact:** On macOS/Linux fresh installs, clicking "View Logs" in Settings opens empty `~/.voice-typer` folder + creates stray directory. User confusion.
- **Proposed fix:** `import { computeConfigDir } from "../single_instance";` then `const logDir = computeConfigDir();`.
- **Proposed fix:** `import { computeConfigDir } from "../single_instance";` then `const logDir = computeConfigDir();`.
- **Confidence:** High (R6)

### S3-CR-34 — `state.mainWindow.close()` in Python early-exit path leaks hidden BrowserWindow
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/main/python/start-python.ts:109-112`
- **Evidence:** In early-exit branch: `state.mainWindow.close(); state.mainWindow = null;`. Close handler in `windows/main-window.ts:125-132` calls `event.preventDefault()` when `!app.isQuitting`. At this point `app.isQuitting` is false. So `.close()` intercepted → window hidden, NOT destroyed. Then `state.mainWindow = null` orphans hidden BrowserWindow with all listeners attached.
- **Root cause:** Close-to-tray `preventDefault()` unconditional on `!app.isQuitting`; early-exit path doesn't set `app.isQuitting = true` before calling `.close()`.
- **Impact:** On Python early-exit (e.g. second-instance mutex held), hidden BrowserWindow leaks with `nativeTheme.on("updated")` listener, webContents, and React renderer process.
- **Proposed fix:** Use `state.mainWindow.destroy()` instead of `.close()` in early-exit path (destroy bypasses close event), OR set `app.isQuitting = true` before calling `.close()`.
- **Confidence:** High (R6)

### S3-CR-35 — `Models.tsx` duplicate `id="api-key-input"` (a11y violation, screen reader confusion)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/pages/Models.tsx:1520, 1535`
- **Evidence:** `<label htmlFor="api-key-input">` and `<Input id="api-key-input">` rendered inside `CLOUD_PROVIDERS.map(...)` (L1497). Same literal `id="api-key-input"` emitted 3 times in DOM (once per provider). HTML spec requires unique IDs.
- **Root cause:** ID literal hardcoded inside map callback; no provider-specific suffix.
- **Impact:** Screen readers' "label → input" jump association points to FIRST matching id regardless of which provider's label activated. AT users cannot reach Groq/Deepgram inputs by clicking labels. Fails HTML validation.
- **Proposed fix:** Use `id={`api-key-input-${provider.key}`}` and matching `htmlFor`. Add unit test asserting unique ids per rendered provider.
- **Confidence:** High (R7)

### S3-CR-36 — `App.tsx` hardcoded English strings ("Starting Python", "Loading model", "Ready")
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/App.tsx:530-543`
- **Evidence:** Three user-visible strings bypass `t()`: `Starting Python` (L530-531), `Loading model` (L538), `Ready` (L543). Rendered inside "connecting" status panel shown to every user during backend startup.
- **Root cause:** Surrounding strings DO use `t()`, indicating oversights.
- **Impact:** Non-English users see English fragments during every app launch.
- **Proposed fix:** Add `app.startingPythonStep`, `app.loadingModelStep` (with `{percent}` interpolation), `app.readyStep` to all 8 locale JSON files; replace literals with `t(...)`.
- **Confidence:** High (R7)

### S3-CR-37 — `Vocabulary.tsx` `CATEGORY_LABELS` not reactive to locale change
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:43-77`
- **Evidence:** `CATEGORY_LABELS` is module-level const that calls `t("vocabulary.category.*")` at import time. When user switches UI language via Settings → `setLocale(newLocale)`, this constant is NOT recomputed — labels stay frozen at import-time locale until renderer reloads.
- **Root cause:** Module-level const built once.
- **Impact:** User switches UI language → all vocabulary category labels stay in OLD language. Inconsistent with `Settings.tsx` (`getSearchTabHints()` is a function) and `Models.tsx` (`getProviderLabel()` is a function).
- **Proposed fix:** Convert `CATEGORY_LABELS` to `getCategoryLabels()` function called at render time; update consumers (L656, 658, 665, 667) to call function.
- **Confidence:** High (R7)

### S3-CR-38 — `Models.tsx` 1682-LOC monolith (god component)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/pages/Models.tsx` (1682 LOC)
- **Evidence:** Single `ModelsPage` component owns: model catalog fetching, download lifecycle (7 useState fields), cloud-provider API key management for 3 providers, per-provider consent toggles, HuggingFace consent banner, model-import dialog, delete-model confirm, `testConnection` with 3-way duplicated fetch logic, benchmark UI duplicated twice. 23 useState hooks, 12 handler functions.
- **Root cause:** Cohesive sub-sections never extracted.
- **Impact:** Hard to test (every test must mock 7+ IPC endpoints). Re-renders expensive.
- **Proposed fix:** Extract cohesive children: `<HuggingFaceConsentBanner>`, `<ModelFamilyAccordion>`, `<CloudProviderCard>`, `<BenchmarkSection>` (single source — currently duplicated). Extract `useModelDownload()` hook owning 7 progress-related useState fields.
- **Confidence:** High (R7)

### S3-CR-39 — `Settings.tsx` 1082-LOC monolith (write-pipeline entangled with render logic)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/pages/Settings.tsx` (1082 LOC)
- **Evidence:** After extracting `*SettingsSection` children, page still owns: tab persistence + scroll-position preservation per tab, debounced/batched config-write pipeline with 5 refs + microtask scheduling (300+ LOC), search-to-tab auto-switch, render-phase sentinel for empty-state detection, inline Troubleshooting section (110 LOC).
- **Root cause:** Write-pipeline logic never extracted to hook.
- **Impact:** Update pipeline logic (300+ LOC) is highest-risk code in renderer — entangled with render logic, hard to unit-test.
- **Proposed fix:** Extract `useSettingsConfig()` hook returning `{config, updateConfig, updateConfigDebounced, saving, saved, flushPendingUpdates}`. Extract `<TroubleshootingSection>` component.
- **Confidence:** High (R7)

### S3-CR-40 — `ThemeSettingsSection.tsx` 890-LOC monolith (5 concerns mixed)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx` (890 LOC)
- **Evidence:** File mixes: (1) localStorage draft helpers, (2) CSS-color → hex conversion with manual OKLCH→sRGB matrix math, (3) DOM-reading color probe mutating `document.documentElement.classList` for `dark`, (4) theme preview color resolver, (5) memo'd SettingsSection component.
- **Root cause:** "All theme-only helpers live here" — symptom not justification.
- **Impact:** Hard to test in isolation (color math untestable without rendering component). React Fast Refresh breaks when file exports both helpers + memo'd component.
- **Proposed fix:** Split into: `themeColorUtils.ts` (pure functions), `customThemeDraftStore.ts`, `ThemePresetSelector.tsx`, `CustomThemeEditor.tsx`, slim `ThemeSettingsSection.tsx` shell.
- **Confidence:** High (R8)

### S3-CR-41 — `HotkeyPicker.tsx` 816-LOC monolith (capture state machine + JSX + duplicated validation)
- **Severity:** High
- **Status:** Pending
- **Locations:** `voice_typer/client/src/renderer/src/components/hotkey/HotkeyPicker.tsx` (816 LOC)
- **Evidence:** Single function component holds: 9 useRef state machines, 4 useEffects, 7 useCallbacks, AND presentational JSX. Validation+dedup logic duplicated between `commitFullCombo` (L422-458) and dropdown `onSelect` inline handler (L764-787).
- **Root cause:** Capture state machine layered on incrementally without extracting hook.
- **Impact:** Untestable without rendering. State machine is riskiest code in app (cross-platform key capture, ESC race, IME) but lives behind React component so unit tests must mount whole tree.
- **Proposed fix:** Extract `useHotkeyCapture({ mode, value, onChange, occupiedHotkeys, onCaptureStart, onCaptureEnd })` custom hook. Extract `HotkeyPresetDropdown` sub-component. Extract `tryCommitHotkey(newValue, opts)` shared helper.
- **Confidence:** High (R8)

---

(Full Medium and Low findings truncated for brevity — captured in worklog.md and addressed by implementation sub-agents. The comprehensive-review.md tracks all 100 canonical findings with statuses updated as fixes land.)

Key Medium findings (CR-42 through S3-CR-76):
- S3-CR-42: `globalErrorHandler.ts` uses `require()` in ESM context (broken i18n)
- S3-CR-43: i18n placeholders mismatch (`{percent}`, `{current}`, `{total}` not interpolated in 6 locales)
- S3-CR-44: `tauri-bridge.ts` FT-1 events cast via `as unknown as PythonPushEvent`
- S3-CR-45: `a11y/axe-core.test.tsx` STUB_CONFIG drift from `VoiceTyperConfig`
- S3-CR-46: `a11y/` directory has no utility modules (only test files using source-string regex)
- S3-CR-47: `useNavigation.ts` mouse back/forward: `mouseup` + `preventDefault` cannot cancel X1/X2
- S3-CR-48: `useStatsShare.ts` debug logging left in production
- S3-CR-49: `recorder.py` reentrancy: `start()` not protected by lock
- S3-CR-50: `rate_limiter.py` lazy-init race condition
- S3-CR-51: `dispatch` 120s hang under FT-1 race
- S3-CR-52: post-auth socket no read timeout
- S3-CR-53: `level_monitor.py` stop race
- S3-CR-54: `crash_recovery.py` `_save_sync` no internal timeout
- S3-CR-55: `Models.tsx` `isBenchmarking` dead state setter (perpetual stub)
- S3-CR-56: `Templates.tsx` / `Vocabulary.tsx` `_requestDelete*` dead code (unreachable ConfirmDialog)
- S3-CR-57: `Microphone.tsx` 100ms polling re-renders entire page tree
- S3-CR-58: `Onboarding.tsx` no `cancelled` flag in init useEffect (setstate on unmounted)
- S3-CR-59: `PrewarmAndUpdates.tsx` poll loop unmount leak
- S3-CR-60: `RecordingSettingsSection.tsx` 4× duplicated `set_esc_cancel_paused` IPC call
- S3-CR-61: `PrivacySettingsSection.tsx` duplicated export buttons (90 LOC copy-paste)
- S3-CR-62: `ThemeSettingsSection.tsx` setState during render (init block)
- S3-CR-63: `NumberInputStepper` `onInvalid` effect dep-array footgun
- S3-CR-64: `SegmentedControl` radio `name` collision (no `useId`)
- S3-CR-65: Settings section 7× duplicated label/items/visible boilerplate
- S3-CR-66: `HotkeyPicker.tsx` IME composition not handled (`e.isComposing` not checked)
- S3-CR-67: `SearchField.tsx` hardcoded English "Search..." placeholder
- S3-CR-68: `RecordingSettingsSection.tsx` `DICTATION_KEY_PRESETS` hardcoded English labels
- S3-CR-69: `HotkeyPicker.tsx` 5 hardcoded English error strings
- S3-CR-70: `audio_filters/compressor.py` / `limiter.py` / `equalizer.py` / `noise_gate.py` per-sample Python loops (perf)
- S3-CR-71: `ipc/rate_limiter.py` ADR-0019 doc drift (dual-window design not in ADR)
- S3-CR-72: `audio_filters/base.py` `FilterChain.process` no try/except (single buggy filter kills session)
- S3-CR-73: `recording/resampling.py` retry-after-timeout TOCTOU race
- S3-CR-74: `hotkeys/wayland.py` socket TOCTOU window before chmod
- S3-CR-75: `hotkeys/factory.py` + `native_adapter.py` duplicate platform-selection logic
- S3-CR-76: `tray_menu.py:311-321` Tauri-side builder coupled to pystray

Low findings (CR-77 through S3-CR-100):
- S3-CR-77: `clipboard.py:1271-1290` `_send_keystroke_sequence` dead code
- S3-CR-78: `history_db.py:662-675` empty try/pass block (dead code)
- S3-CR-79: `clipboard.py:827, 969, 1253` 3 pinned UP037 ruff violations (quoted forward refs)
- S3-CR-80: `config.py:867` `volume_duck_smart` DEPRECATED comment is wrong (field is live-wired)
- S3-CR-81: `recorder.py:847` unnecessary `# type: ignore[no-untyped-def]` on `_make_vad_property`
- S3-CR-82: `prewarm/__init__.py:116-127, 273-284` 9 unused stdlib re-binds
- S3-CR-83: `clipboard.py:1271-1290` `_send_keystroke_sequence` dead code (DUPLICATE of S3-CR-77 — merged)
- S3-CR-84: `container_detect.py:14` / `text_cleanup.py:239` `__import__("logging")` pattern
- S3-CR-85: `_secrets.py:271, 286` `_user_extensions` set mutation without lock
- S3-CR-86: `dictation_pipeline.py:248-252` finally block zeroes only top-level audio array
- S3-CR-87: `hallucination.py:138-144` LogRecord construction for redaction (use `redact_pii` helper)
- S3-CR-88: `audio_processor.py:102` private attr access (`new_chain._filters`)
- S3-CR-89: `level_monitor.py:607` dead `list(_test_peak_history)` expression
- S3-CR-90: `LiveQualityFeedback.tsx:22` dead `_volumeGood` variable
- S3-CR-91: `TitleBar.tsx` redundant `<title>` inside aria-hidden SVGs
- S3-CR-92: `ActivityList.tsx` no list virtualization / `maxItems` prop
- S3-CR-93: 4 files contain stray `{" "}` literals
- S3-CR-94: `ConfirmDialog.tsx` redundant `aria-label` on titled buttons
- S3-CR-95: `ThemeSettingsSection.tsx:851-858` raw `<button>` "Reset colors" bypasses design system
- S3-CR-96: `hotkey-utils.ts:18-31` deprecated `SINGLE_KEY_PRESETS` / `COMBO_PRESETS` aliases
- S3-CR-97: `PrivacySettingsSection.tsx:334-341, 379-386` unsafe `window.window_` casts
- S3-CR-98: `useSnackbar.tsx` file extension mismatch (no JSX, named .tsx)
- S3-CR-99: `i18n.ts:280-283` `String.prototype.replace` `$` special char footgun
- S3-CR-100: `i18n.ts:14-21` eager import of all 8 locale JSON files (600KB bundle bloat)


### S4-CR-1 — `ipc/` subpackage is dead-code parallel of `ipc_server.py` (~2,800 lines dead)
- **Category**: Overall architecture / Dead code
- **Severity**: Critical
- **Location**: `voice_typer/server/ipc/server.py` (1,764 lines), `ipc/main.py` (389), `ipc/process_meta.py` (25), `ipc/push_events.py` (60), `ipc/transport.py` (duplicates `_TCPLineIO`), `ipc/rate_limiter.py` (duplicates `_RateLimiter`)
- **Evidence**: Phase 4.5 / ARCH-045 split was started but never finished. Both `ipc_server.py` (2,609-line shim with full implementations) and `ipc/` package (parallel implementations) coexist. `pyproject.toml:157` registers `voice_typer.server.ipc_server:main` as the canonical entry; `providers.py:362` imports `IPCServer` from the shim; only `ipc/validation.py` and `ipc/history_bounds.py` are actually imported by handler mixins. ~2,238+ lines of unreachable code.
- **Root cause**: Verified — refactor abandoned mid-way; the shim was retained with full body instead of becoming a re-export.
- **Impact**: Dead code inflates codebase by ~5%; bug-fix drift already observed (the dead-code `ipc/server.py` has a TCP-teardown deadlock fix that the live `ipc_server.py` is missing — see S4-CR-2). Static analysis wastes cycles. Contributors reading the wrong copy get a false picture.
- **Proposed fix**: Delete `ipc/server.py`, `ipc/main.py`, `ipc/process_meta.py`, `ipc/push_events.py`. Move the genuinely-shared leaf helpers (`validation.py`, `history_bounds.py`, plus `_RateLimiter`, `_TCPLineIO`, `_pick_available_port`, `_push_event_now`) into the package, then have `ipc_server.py` import them (delete the inline copies). Add a regression test in `tests/test_dead_code_stays_removed.py`.
- **Confidence**: High

### S4-CR-2 — TCP-teardown deadlock fix lives only in dead-code copy (production deadlocks)
- **Category**: Backend architecture / Reliability
- **Severity**: Critical
- **Location**: `voice_typer/server/ipc_server.py:2193-2197` (`_send` finally) and `:608-613` (`_TCPLineIO.close`)
- **Evidence**: Production `_send()` restores `settimeout(None)` in finally; `_TCPLineIO.close()` does not call `socket.shutdown()`. The dead-code duplicate `ipc/server.py:1696-1706` + `ipc/transport.py:120-133` has the fix: `settimeout(_prev_timeout)` and `conn.shutdown(SHUT_RDWR)` before close, with comments explaining the deadlock.
- **Root cause**: Verified — fix was applied to dead-code copy by a contributor who believed it was the canonical implementation.
- **Impact**: Production backend can deadlock during teardown when a dispatch thread is blocked in `recv` while another calls `close()`. Reader thread's BufferedReader holds its lock; close blocks indefinitely; worker never exits; process hangs with mic open and single-instance mutex held.
- **Proposed fix**: Apply the two fixes to `ipc_server.py`: (1) capture `_prev_timeout` before `settimeout(_TCP_WRITE_TIMEOUT_SECONDS)`, restore `_prev_timeout` in finally; (2) call `self.conn.shutdown(socket.SHUT_RDWR)` (suppressed) before `self.conn.close()` and before `self._reader.close()`. Then delete the dead-code copies (CR-1) so the fix can't drift again.
- **Confidence**: High

### S4-CR-3 — `_delayed_restore` signature mismatch — every paste() silently broken
- **Category**: Memory / Concurrency / UX
- **Severity**: Critical
- **Location**: `voice_typer/server/clipboard.py:1018-1023` (call site) + `:1215-1220` (signature)
- **Evidence**: `paste()` spawns `_delayed_restore` thread with 4 positional args `(snapshot, expected, delay, _pending_entry)`, but `_delayed_restore(self, snapshot, pasted_text, delay)` accepts only 3. Thread immediately dies with `TypeError: _delayed_restore() takes 4 positional arguments but 5 were given`. Verified empirically. Tests at `tests/test_clipboard_borrow_restore.py:301/318` call `_delayed_restore` directly with 3 args, missing the bug.
- **Root cause**: Verified — `_pending_entry` tuple was added to args without updating signature.
- **Impact**: (1) User's original clipboard content (text, RTF, HTML, image, file list) is NEVER restored by the daemon thread after paste — transcription text stays on clipboard until process exit. (2) Memory leak — `_pending_restores` grows by one ClipboardSnapshot per paste, never cleared mid-session. Heavy-dictation user: ~400 snapshots lingering after an 8-hour workday.
- **Proposed fix**: Update `_delayed_restore` signature to accept `pending_entry=None` and remove it from `_pending_restores` in `finally`. Add regression test that drives the production `paste()` path end-to-end and asserts the entry is removed from `_pending_restores` after the daemon thread completes.
- **Confidence**: High

### S4-CR-4 — `sidecar_ws.py` asyncio.Queue mutated cross-thread without `call_soon_threadsafe`
- **Category**: Concurrency / Reliability
- **Severity**: Critical
- **Location**: `voice_typer/server/sidecar_ws.py:386-416` (`_push_to_ws`)
- **Evidence**: `_push_to_ws` is registered as an `event_bus` subscriber; `event_bus.publish()` is called from many non-event-loop threads (transcription, hotkey, tray, IPC workers). `_push_to_ws` calls `outbound.full()`, `outbound.get_nowait()`, `outbound.put_nowait()` on an `asyncio.Queue`. asyncio.Queue is explicitly NOT thread-safe. No `loop.call_soon_threadsafe` anywhere in the file (grep-confirmed).
- **Root cause**: Verified — WS path assumes all `publish` calls come from the asyncio event loop thread; this assumption is false.
- **Impact**: Under concurrent publishers (transcription_final racing with bubble_level racing with state_changed), queue internal state corrupts. Symptoms: silently dropped events (transcription_final never reaches Tauri host → user sees no result), deadlocked writer task (writer's `await outbound.get()` never wakes), or hard asyncio loop crash killing the Tauri sidecar → FT-1 respawn loop.
- **Proposed fix**: Capture `loop = asyncio.get_running_loop()` at connection setup; in `_push_to_ws`, call `loop.call_soon_threadsafe(_enqueue_safe, outbound, event)` where `_enqueue_safe` does the full/get_nowait/put_nowait dance inside the event loop thread. Alternative: replace asyncio.Queue with stdlib `queue.Queue` consumed via `await loop.run_in_executor(None, outbound.get)`.
- **Confidence**: High

### S4-CR-5 — Tauri capability over-grants the bubble window (regression of SEC-026 sandboxing)
- **Category**: Security
- **Severity**: Critical
- **Location**: `src-tauri/capabilities/migrate-runtime.json` + `src-tauri/src/commands/sidecar_cmds.rs:22-44` (`dispatch`)
- **Evidence**: Single capability `migrate-runtime` declares `"windows": ["main", "bubble"]` and grants `core:default` (includes `core:invoke`), `shell:allow-spawn`, `shell:allow-kill`, `clipboard-manager:allow-read-text`, `clipboard-manager:allow-write-text`, `dialog:*`, and all `core:window:allow-*` to BOTH windows. The Rust `dispatch` command forwards ANY `cmd` string from the webview to the Python sidecar with NO allowlist or caller-window check. `tauri.conf.json` has `withGlobalTauri: true`, so `window.__TAURI__.core.invoke` is exposed to ALL windows including bubble.
- **Root cause**: Verified — Electron path has SEC-026 (separate `preload/bubble.ts` exposing only `bubble:*` channels). The Tauri port did not replicate this sandboxing.
- **Impact**: A compromised bubble renderer (alwaysOnTop waveform display) can: (1) dispatch arbitrary IPC commands (`set_config`, `quit_app`, `shutdown`); (2) read system clipboard (password exfiltration); (3) spawn additional python-sidecar processes (DoS); (4) close/hide the main window. Direct regression of the Electron path's hardening.
- **Proposed fix**: Split into `main-runtime.json` (`"windows": ["main"]` with all current permissions) and `bubble-runtime.json` (`"windows": ["bubble"]` granting only `core:event:default`, `core:window:allow-start-dragging`, and the `bubble_*` commands). In Rust `dispatch`, add `window: tauri::Window` to the signature and reject calls where `window.label() != "main"`. Alternatively, set `withGlobalTauri: false` and expose `invoke` via scoped preload script.
- **Confidence**: High

### S4-CR-6 — `Onboarding.tsx` missing Permissions step (renderer out of sync with server's 6-step wizard)
- **Category**: User onboarding / User flows / i18n
- **Severity**: Critical
- **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:263-420` vs `voice_typer/server/onboarding.py:42-49,125-141`
- **Evidence**: Server's `OnboardingController` declares `_total_steps = 6` with step names `[Welcome, Microphone, Permissions, Hotkey, Model, Done]`. The i18n file has `onboarding.permissionsTitle`/`permissionsDescription`/etc. The IPC handler `_handle_onboarding_check_permissions` exists. But `Onboarding.tsx` only renders cases for `step.step === 0/1/2/3/4` mapping to Welcome/Mic/Hotkey/Model/Done — there is NO `step === 2 → Permissions` branch. `onboarding_check_permissions` IPC is never called from the renderer (grep-confirmed). When server reports step=2 with `step_name="Permissions"`, the renderer shows a Hotkey picker instead.
- **Root cause**: Verified — frontend/server step-index mismatch. Permissions step was added server-side (UX-4/UX-27) but the renderer was never updated.
- **Impact**: macOS users complete the wizard without granting Accessibility permission; Linux users without being added to the `input` group. They press their hotkey and nothing happens — exactly the silent failure the Permissions step was added to prevent. On Windows the step auto-passes but UI shows mismatched step names. Progress bar caps at 83% (5/6) and never reaches 100%.
- **Proposed fix**: Add `step.step === 2 → Permissions` branch in Onboarding.tsx: call `onboarding_check_permissions` on mount, render `permissionsTitle` + `permissionsDescription` + platform-specific instructions + "Test hotkey" button. Shift Hotkey to step 3, Model to step 4, Done to step 5. Update `handleNext` accordingly. Branch on `step.step_name` instead of numeric index to prevent recurrence.
- **Confidence**: High

### S4-CR-7 — Undefined `--bg-card` CSS variable — SettingsSkeleton and ErrorBoundary are invisible
- **Category**: UX/UI consistency
- **Severity**: Critical
- **Location**: `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx:72,86` + `components/settings/SettingsSkeleton.tsx:32-35`
- **Evidence**: Both files use `bg-(--bg-card)`. Grep across the entire client/src tree confirms `--bg-card` is NEVER defined — not in `index.css`, not in `themes.ts`, not in any theme preset. The defined variable is `--card` (`index.css:77`). SettingsSkeleton's animated placeholder bars have NO background color (transparent), so `animate-pulse` pulses between transparent and transparent — the skeleton is invisible. ErrorBoundary's error-message `<pre>` and the "Reload app" button hover state are also transparent.
- **Root cause**: Verified — typo/renaming drift. Either `--bg-card` was renamed to `--card` and these call sites were missed, or the variable was never created.
- **Impact**: Settings page shows no skeleton loading state — content pops in (the exact flash the skeleton was added to prevent per BACKLOG-008). The ErrorBoundary fallback (shown when a render crash occurs) has an invisible error-details panel and unstyled hover state on the secondary button — making the recovery UI look broken at the worst possible time.
- **Proposed fix**: Replace all 5 occurrences of `bg-(--bg-card)` with `bg-(--bg-subtle)` (matches surrounding card styling in `SettingsSection.tsx`).
- **Confidence**: High

### S4-CR-8 — Transcription-text PII leak in no-client push path (INFO log)
- **Category**: Privacy / Observability
- **Severity**: Critical
- **Location**: `voice_typer/server/ipc_server.py:2252`
- **Evidence**: `log.info("[IPC] no client; dropping %s event: %s", msg_type, msg)` — `msg` is the full IPC event dict. When the renderer is disconnected, server-initiated events (transcription_final, history_changed, paste_failed) reach this branch. The transcription_final event payload contains `{text: <user speech ≤200 chars>}` (per `event_bus.py` docstring). At INFO level this is written to the rotating voice-typer.log file. The PIIRedactionFilter only scrubs emails/phones/SSNs/CCs/API keys — NOT general transcribed speech.
- **Root cause**: Verified — push() "no client" branch logs the entire msg payload for operator visibility, but payload can contain raw transcription text. PIIRedactionFilter does not cover free-form speech.
- **Impact**: User's transcribed speech (potentially sensitive: names, addresses, medical info, dictated passwords) is written to disk in plaintext whenever Electron is disconnected (sleep/resume, dev-tools inspection, brief crash). Happens after every transient disconnect. Violates the project's privacy posture (SEC-009 redaction goal).
- **Proposed fix**: Replace `msg` with a redacted summary: `log.info("[IPC] no client; dropping %s event (size=%d)", msg_type, len(str(msg)))`. For `transcription_final` specifically, never log the payload text at any level. Optionally route through `redact_pii()` if a preview is needed.
- **Confidence**: High

### S4-CR-9 — Unbounded Electron crash log (no rotation, no size cap)
- **Category**: Observability
- **Severity**: Critical
- **Location**: `voice_typer/client/src/main/bootstrap.ts:122-135, 158-161`
- **Evidence**: `setupErrorHandlers()` uses `fs.appendFileSync(crashLogPath, line, ...)` for every `uncaughtException` AND `unhandledRejection`. No rotation, no size cap, no truncation. Python backend uses RotatingFileHandler (5 MiB × 5); Rust host uses RotatingFileWriter (5 MB × 5); Electron crash log has no equivalent.
- **Root cause**: Verified — only the Python and Rust loggers have rotation; Electron crash log was added in SEC-021 without a rotation strategy.
- **Impact**: In a crash-looping scenario (renderer regression triggering `unhandledRejection` on every window load), `electron-crashes.log` grows unbounded. On a long-running install with intermittent renderer crashes over months, the file can reach hundreds of MB. Disk exhaustion in the user's userData directory is possible.
- **Proposed fix**: Add simple rotation: before append, stat the file; if size > 1 MiB, rename to `.1` (deleting prior `.1`). Or use `rotating-file-stream` npm module. Also separate the two event types: `electron-crashes.log` (uncaughtException only) and `electron-rejections.log` (unhandledRejection) so the crash log stays a useful signal.
- **Confidence**: High

### S4-CR-10 — Unbounded `startup-error.log` on repeated `app.start()` failures
- **Category**: Observability
- **Severity**: Critical
- **Location**: `voice_typer/server/ipc_server.py:2585-2594` (and `:2444-2445`)
- **Evidence**: When `app.start()` raises, the diagnostic block does `existing = diag_path.read_text(...)` then `_secure_atomic_write(diag_path, existing + buf.getvalue())`. Each subsequent `app.start()` failure APPENDS to `startup-error.log` rather than rotating or capping. By contrast, the construction-failure path (line 2445) overwrites the file with just the new content.
- **Root cause**: Verified — `app.start()` failure path was written to preserve prior diagnostic context, but no bound is enforced.
- **Impact**: In a misconfigured environment (bad GPU driver causing torch import crash on every launch), the file grows by ~2-5 KB per launch attempt. A user retrying 100 times accumulates ~500 KB; a cron-restarted service can accumulate MB per day. Combined with `crash_diagnostics.<PID>.txt` files (one per crash PID), repeated startup-failure loops can exhaust disk in the config dir.
- **Proposed fix**: Cap the file at one entry: replace read-existing-then-append with a fresh `_secure_atomic_write(diag_path, buf.getvalue())` (matching the construction-failure path). If history is desired, write to `startup-error.<timestamp>.log` and prune to the most recent N=5 files. Same for `crash_diagnostics.*.txt` — prune older than 7 days.
- **Confidence**: High

### S4-CR-11 — HuggingFace consent bypass in `service.download_model` IPC
- **Category**: Privacy & data protection
- **Severity**: Critical
- **Location**: `voice_typer/server/service.py:1903-2303` (`download_model`)
- **Evidence**: The IPC handler `download_model` calls `snapshot_download(...)` directly for Whisper-family models and `download_parakeet_weights()` for Parakeet, with NO check of `config.huggingface_consent`. The only consent gate is in `voice_typer/server/transcription.py:835` inside `TranscriptionEngine._pre_download_model`, which is only invoked from `engine.load()` — not from the IPC download path. `asr_setup.download_parakeet_weights` also has no consent gate.
- **Root cause**: Verified — consent gate lives in the engine load path, not the IPC download path; the two paths were never wired together.
- **Impact**: Clicking "Download" on the Models page phones home to huggingface.co (revealing the user's IP to a US-headquartered third party) without the explicit GDPR Art. 13/44 consent that `huggingface_consent` was specifically designed to gate (NEW-PRIV-005). The consent dialog never appears when the user explicitly clicks Download — it only appears later if they switch backend and the engine tries to auto-load.
- **Proposed fix**: In `service.download_model`, before `snapshot_download` and before `download_parakeet_weights()`, read `getattr(self._app.config, "huggingface_consent", False)`. If False, return `{"success": False, "error": "HuggingFace consent required", "consent_required": True}` and push a `consent_required` event so the renderer can show the consent dialog. Add a regression test asserting no `snapshot_download` is invoked when consent is False.
- **Confidence**: High

---

## HIGH findings

### S4-CR-12 — `_handle_tray_click` skips `_validate_dict_payload` convention
- **Category**: Backend architecture / Security
- **Severity**: High
- **Location**: `voice_typer/server/ipc_server.py:1999-2042`
- **Evidence**: Production `_handle_tray_click` validates only that `data` is a dict and contains key "id", NOT that the value is a string. A non-string `id` (e.g. `{"id": 42}`, `{"id": null}`, `{"id": ["open"]}`) passes the check and is forwarded to `tray.dispatch_tray_action(item_id)`. The dead-code duplicate `ipc/server.py:1492-1502` correctly uses `_validate_dict_payload`.
- **Impact**: Non-string `id` either raises an unhandled exception (caught by dispatcher's generic `except Exception`, returning a generic `internal_error`) or silently no-ops. Frontend gets an opaque error instead of the structured `invalid_field` envelope that ADR-0020 §2 promises.
- **Proposed fix**: Replace the inline isinstance check with `_validate_dict_payload(data, {"id": {"type": str, "required": True}})`. Add a regression test exercising the non-string-id path.
- **Confidence**: High

### S4-CR-13 — FT-1 respawn race: sidecar permanently dead after fast double-crash
- **Category**: Reliability
- **Severity**: High
- **Location**: `src-tauri/src/sidecar/ft1.rs:33-35, 82-94`; `src-tauri/src/sidecar/ws.rs:210-217`
- **Evidence**: `ft1_respawn` acquires `respawn_in_progress` via compare_exchange(false→true) at entry and only clears it AFTER `ft1_respawn_inner` returns. `ft1_respawn_inner`, on success, spawns a new sidecar, calls `reconnect_ws` (which starts a new WS reader task), logs "respawn succeeded", and returns Ok(()). The new WS reader task runs concurrently. If the new sidecar dies immediately (native hotkey binary crash, model load OOM, port bind race), the new reader's WS closes, it enters the cleanup block at ws.rs:162-185, and then spawns `std::thread::spawn(... ft1_respawn(...).await ...)` at ws.rs:212-216. At this instant, `respawn_in_progress` is STILL true. The new reader's ft1_respawn sees the flag set, logs "respawn already in progress — skipping", and returns Ok(()). The reader task exits. Then line 34 clears the flag. Result: sidecar is dead, WS reader is dead, no one is respawning, the UI shows "reconnecting…" forever.
- **Impact**: Sidecar permanently dead after a fast double-crash (crash-on-startup scenario). User must manually restart the entire app. Affects Tauri/WS path only; Electron/TCP path uses the heartbeat watchdog instead.
- **Proposed fix**: Clear `respawn_in_progress` BEFORE returning Ok(()) from `ft1_respawn_inner` (move line 34 inside the inner function, before the `return Ok(())` at ft1.rs:88). This is safe because the new WS reader task is already running and owns the new connection; a subsequent disconnect will correctly start a fresh ft1_respawn.
- **Confidence**: High

### S4-CR-14 — FT-1 retry loop orphans the old sidecar process (no Drop kill)
- **Category**: Concurrency / Reliability
- **Severity**: High
- **Location**: `src-tauri/src/sidecar/ft1.rs:62-100` (`ft1_respawn_inner` retry loop)
- **Evidence**: The loop spawns a new sidecar (line 63), stores it in `state.child` (line 68-69), then calls `reconnect_ws` (line 82). If `reconnect_ws` returns Err, the loop continues to the next iteration (line 92). On the next iteration, a NEW child is spawned and stored, overwriting the OLD Some(child) — but `SidecarHandle` has NO Drop impl and the old child is never killed. The release-build variant `SidecarHandle::ShellPlugin(CommandChild)` does NOT kill the process on drop (Tauri's `CommandChild::kill` consumes self; drop is a no-op).
- **Impact**: On FT-1 retry, the orphaned sidecar process keeps running with the microphone stream open, global hotkeys registered, and volume ducked. Each retry iteration can orphan another process. After N retries, N orphaned sidecars compete for the same hotkeys and the microphone.
- **Proposed fix**: Before overwriting `state.child` on retry, kill the old child: `if let Some(old) = child_guard.take() { let _ = tauri::async_runtime::block_on(old.kill_tree()); }`. Alternatively, impl Drop for SidecarHandle that calls kill_tree on drop.
- **Confidence**: High

### S4-CR-15 — `HotkeyDispatcher.restart` not atomic — failed restart leaves no hotkey
- **Category**: Reliability
- **Severity**: High
- **Location**: `voice_typer/server/hotkey_dispatcher.py:273-290` (`restart`)
- **Evidence**: `restart()` does `self._hotkey_backend.stop()` then `self._hotkey_backend = None` BEFORE calling `self.register()`. `register()` can fail: `create_hotkey_backend` can raise (binary not found, spec parse error), or RegisterHotKey can fail with "hotkey already in use by another app". `register()` catches exceptions (line 103-114) and shows a tray notification, but leaves `self._hotkey_backend = None`. The old backend was already stopped and set to None. The user now has NO dictation hotkey at all until they manually restart the app or pick a different hotkey.
- **Impact**: User changes hotkey in Settings, new hotkey is rejected by the OS (e.g. Snipping Tool owns Win+Shift+S), user is left with no working hotkey. The tray notification says "pick a different hotkey in Settings" but the user may not see it. The app appears broken until manual intervention.
- **Proposed fix**: Register the NEW backend BEFORE stopping the old one: `new_backend = create_hotkey_backend(hotkey_str); new_backend.start(callback); # only if start succeeds: old = self._hotkey_backend; self._hotkey_backend = new_backend; if old: old.stop()`. If new_backend.start() raises, catch it, show the tray notification, and KEEP the old backend running. Add a test verifying the old backend is still alive after a failed restart.
- **Confidence**: High

### S4-CR-16 — No single-instance enforcement on macOS/Linux (POSIX)
- **Category**: Reliability / Cross-platform
- **Severity**: High
- **Location**: `voice_typer/server/single_instance.py:183-184`; `voice_typer/server/autostart_launcher.py:499-524`
- **Evidence**: `single_instance._ensure_single_instance()` returns None immediately on non-Windows platforms. There is NO POSIX equivalent (no lockfile, no abstract-socket, no fcntl lock). The autostart launcher's "already running?" check reads the backend PID file and checks if the PID is alive, then falls back to checking if port 9876 is open. But the PID file writer (`single_instance._write_backend_pid_file`) only runs on Windows (after the mutex is acquired — `single_instance.py:401`). So on macOS/Linux, there is NO backend PID file and NO single-instance mutex. The launcher falls back to the port-9876 check. If the backend auto-incremented to port 9877 (because 9876 was busy), the launcher's port check returns False, and the launcher spawns a SECOND backend.
- **Impact**: On macOS/Linux, a user who logs in twice (autostart + manual launch), or whose autostart launcher races with a desktop shortcut, ends up with two competing backends. Symptoms: double-pasted transcriptions (both backends process the same hotkey press), mic device errors, volume ducking conflicts, SQLite database lock errors.
- **Proposed fix**: Add a POSIX lockfile-based single-instance guard. In `_ensure_single_instance`, for non-Windows: open `<config_dir>/backend.lock` with `O_CREAT|O_EXCL|O_CLOEXEC`; if EEXIST, read the PID, check liveness (reuse `_is_pid_alive`), and if stale, unlink+retry once; if alive, log and `sys.exit(1)`. Hold the fd for the process lifetime. Additionally, have `_write_backend_pid_file` run on ALL platforms (not just Windows) so the autostart launcher's PID-file check works on macOS/Linux.
- **Confidence**: High

### S4-CR-17 — `app.restart_app` calls `sys.exit(0)` unconditionally from tray thread
- **Category**: Reliability
- **Severity**: High
- **Location**: `voice_typer/server/app.py:924-1068` (`restart_app`); `voice_typer/server/shutdown_controller.py:429-498, 694-738`
- **Evidence**: `restart_app()` ends with `sys.exit(0)` at app.py:1068, unconditionally — no check for `threading.current_thread() is threading.main_thread()`. By contrast, `ShutdownController.quit()` checks `is_main = threading.current_thread() is threading.main_thread()` and only calls `sys.exit(0)` when `is_main` is True. `restart_app` is invoked from the tray menu callback, which runs on pystray's worker thread (NOT the main thread). When `sys.exit(0)` is called from a non-main thread, CPython raises SystemExit in THAT thread only — the process does not exit. The tray's `_wrap` callback wrapper suppresses SystemExit. So `sys.exit(0)` is swallowed, the tray callback returns, and the process stays alive. The process only eventually exits because `_do_cleanup()` called `tray.stop()` which breaks the pystray loop on its next iteration — but that relies on pystray polling, which can take up to 1 second.
- **Impact**: On restart, the old Python process lingers for up to ~1s after cleanup, potentially holding the single-instance mutex (Windows) or the IPC port (all platforms). The new Electron spawns a new Python backend which may hit "single instance already running" (Windows) or "port already in use" (all platforms) because the old process hasn't fully exited. The restart appears to hang or fail intermittently.
- **Proposed fix**: Mirror `quit()`'s pattern: `if threading.current_thread() is threading.main_thread(): sys.exit(0)` — and rely on `tray.stop()` (already called inside `_do_cleanup`) to break the pystray loop so `app.start()` returns and `ipc_server.main()` falls through to process exit.
- **Confidence**: High

### S4-CR-18 — IPC command-count drift (70 actual, 68-69 documented)
- **Category**: Documentation / IPC contract
- **Severity**: High
- **Location**: `SECURITY.md:37-42` (says 69); `docs/ARCHITECTURE.md:15,26,81` (say 68); `CONTRIBUTING.md:221` (says 68); `FEATURES.md:44` (says ~35); `docs/adr/0020-*.md:32,275,353` (say 69); `:594,811` (say 68); `voice_typer/server/ipc_server.py:1884-1997` + `voice_typer/client/src/main/index.ts:79-150` (actual = 70)
- **Evidence**: Counted 70 entries in both `_COMMAND_REGISTRY` and `ALLOWED_COMMANDS`. Docs disagree: SECURITY.md=69, ARCHITECTURE.md=68, ADR-0020 has both 68 and 69 in different sections, FEATURES.md=~35. The CI test `tests/test_security_doc_command_count.py::test_security_md_allowlist_count_matches_source` asserts `documented == actual` — with documented=69 and actual=70 this test should currently FAIL in CI.
- **Impact**: (1) CI security-doc-count test is either failing silently or has been bypassed. (2) SECURITY.md's authoritative security claim ("only the 69 commands") understates the attack surface by one command. (3) New contributors reading FEATURES.md see "~35" and assume the IPC surface is much smaller than it is.
- **Proposed fix**: Update all counts to 70 in one commit. Update FEATURES.md's "~35" to the actual count. Fix the existing `test_security_doc_command_count.py` test. Add a CI gate that asserts: SECURITY.md count == ALLOWED_COMMANDS count == _COMMAND_REGISTRY count.
- **Confidence**: High

### S4-CR-19 — pip-audit step in CI is non-blocking (continue-on-error swallows failures)
- **Category**: CI/CD
- **Severity**: High
- **Location**: `.github/workflows/build.yml:226-237`
- **Evidence**: pip-audit step has BOTH `continue-on-error: true` AND a `|| (echo "::warning::..." fallback)`. The step comment block (lines 198-225) says "HARD-FAIL on ANY vulnerability" and "the accepted-findings list is currently EMPTY", but the actual command structure means any vulnerability finding is converted to a warning annotation and the step is marked successful. CI never fails on a CVE.
- **Impact**: A real CVE in a pinned dependency (torch, transformers, faster-whisper) lands on `main` and ships in a release without blocking CI. The weekly sweep creates a GitHub issue, but the issue is informational — it does not block the per-PR pipeline.
- **Proposed fix**: Drop `continue-on-error: true` and the `||` warning fallback. Run `pip-audit --strict` directly so non-zero fails the step. Maintain the accepted-findings list explicitly with `--ignore-vuln <GHSA>` lines justified by comments.
- **Confidence**: High

### S4-CR-20 — Tauri workflows ALL disabled (`if: false`) — zero CI coverage
- **Category**: CI/CD
- **Severity**: High
- **Location**: `.github/workflows/tauri-windows-build.yml:80`, `.github/workflows/tauri-macos-build.yml:50,121,183`, `.github/workflows/tauri-linux-build.yml:62`
- **Evidence**: Every Tauri build job (windows/macos-aarch64/macos-x86_64/macos-universal/linux) is gated by `if: false`. The orchestrator workflow `tauri-build.yml` calls them via `workflow_call`, but GitHub Actions treats calling a workflow whose only job has `if: false` as a no-op. There is no path — manual dispatch, push, PR, tag — under which any Tauri build actually runs in CI.
- **Impact**: The entire Tauri migration (the project's stated direction per ADR-0020) has zero CI coverage. A maintainer flipping `if: false` → `if: true` has no signal that the build still works — Nuitka flag changes, Cargo dep bumps, Tauri config edits, or sidecar script regressions can land on `main` and only surface during a release cut.
- **Proposed fix**: Add a `workflow_dispatch`-only "smoke" mode that runs `cargo check` + a Nuitka `--check` dry-run on every push to `main` for the Tauri Rust host, gated to Linux-only initially. Keep `cargo tauri build` (the bundle step) behind the existing `if: false` until Phase 0 passes.
- **Confidence**: High

### S4-CR-21 — `_secure_clear_array` undefined in `recorder.py` (SEC-audit-008 silently broken)
- **Category**: Existing warnings / Memory / Privacy
- **Severity**: High
- **Location**: `voice_typer/server/recording/recorder.py:1226-1235`
- **Evidence**: Code calls `_secure_clear_array(self._cached_resampled)` and `_secure_clear_array(self._cached_no_resample_arr)` with NO import. `_secure_clear_array` is defined in `recording/buffer.py:37` and re-exported by `recording/__init__.py`, but `recorder.py` uses a bare-name lookup. The surrounding `try/except Exception: pass` swallows the resulting `NameError`. `ruff --select F821` reports both call sites. The docstring at line 1221 says "_secure_clear_array is defined at recording.py:78" — recording.py doesn't exist (it's recording/buffer.py now).
- **Impact**: SEC-audit-008's secure-zeroing of cached audio arrays NEVER executes. The previous session's audio lingers in process memory until the next GC pass frees the numpy arrays — exactly the security regression SEC-audit-008 was meant to fix. The try/except makes the failure invisible in tests.
- **Proposed fix**: Add `from voice_typer.server.recording import _secure_clear_array` at the top of recorder.py. Tighten the `except` to `except (OSError, ValueError):` so a future NameError-class bug surfaces instead of being swallowed. Add a unit test that calls the secure-clear path and asserts the array is zeroed.
- **Confidence**: High

### S4-CR-22 — `__all__` in `config_validators.py` lists 9 names that don't exist (F822 × 9)
- **Category**: Existing warnings
- **Severity**: High
- **Location**: `voice_typer/server/config_validators.py:886-895`
- **Evidence**: `__all__` lists 9 names that don't exist in the module: `_check_hotkey_type`, `_check_hotkey_length`, `_check_hotkey_not_empty`, `_check_hotkey_has_parts`, `_check_universal_reserved_shortcut`, `_check_per_platform_shortcut`, `_check_win_key_on_windows`, `_check_cmd_letter_on_macos`, `_check_alt_shift_on_windows`. The actual functions are `_check_basic_shape`, `_check_universal_reserved`, `_check_platform_reserved`, `_check_alt_shift`, `_check_ctrl_letter`, `_check_shift_letter`, `_check_single_alphanumeric`, `_check_os_shell_combos`. `ruff --select F822` reports all 9.
- **Impact**: `from voice_typer.server.config_validators import _check_hotkey_type` raises ImportError at runtime. Any caller (test or production) using star-import (`from config_validators import *`) silently loses 9 of the documented exports. The module's public API contract as declared in `__all__` is a lie. CI's "Ruff (F-rules hard-fail)" step (`build.yml:98-99`) would fail.
- **Proposed fix**: Either (a) rename the 9 actual functions to match `__all__` (preferred — the `__all__` names are more descriptive) OR (b) update `__all__` to use the real names. Add a test that does `from voice_typer.server.config_validators import *` and asserts every name in `__all__` is a real attribute.
- **Confidence**: High

### S4-CR-23 — ruff baseline drift: 3 declared, 61 actual violations
- **Category**: Existing warnings
- **Severity**: High
- **Location**: `ruff-baseline.json` vs `voice_typer/server/`
- **Evidence**: Baseline declares `total_count=3, by_rule={"UP037":3}`. Actual `ruff check voice_typer/server/ --output-format=json` returns 61 violations across 10 rule categories: E731:19, SIM105:12, E402:10, F822:9, N806:3, E501:2, F821:2, F841:2, UP022:1, F401:1. UP037 itself has 0 current violations. The ratchet script outputs `FAIL: total violation count grew from 3 to 61` with 10 per-rule regressions.
- **Impact**: CI is either currently red (and someone is ignoring it) or has been bypassed. New violations can land without detection. The baseline file's own schema invariant trivially passes — masking the regression from the test suite.
- **Proposed fix**: (a) Fix the 14 F-rule violations (real bugs — see S4-CR-21, S4-CR-22). (b) For the 47 style violations, either fix them in bulk via `ruff check --fix --unsafe-fixes` OR regenerate the baseline honestly.
- **Confidence**: High

### S4-CR-24 — `requirements-lock.txt` missing keyring + websockets + test/build/optional deps
- **Category**: Dependency health
- **Severity**: High
- **Location**: `requirements-lock.txt`
- **Evidence**: `requirements.txt` declares `keyring>=25.0,<26.0` and `websockets>=12.0,<14.0` as core deps. `requirements-lock.txt` (the hash-pinned reproducible lockfile) does NOT contain either — `grep -i keyring requirements-lock.txt` returns 0 matches, `grep -i websockets` returns 0. The lockfile also omits all test deps (pytest, pytest-asyncio, etc.), build deps (pyinstaller), and optional extras. Header says "Last regenerated: 2026-07-12" — 5+ months stale.
- **Impact**: `pip install --require-hashes -r requirements-lock.txt` silently installs a broken environment: `credential_store.py` (imports keyring) and `sidecar_ws.py` (imports websockets) will crash with `ModuleNotFoundError` on first use. The hash-pinned lockfile — which exists specifically to provide reproducible secure installs — produces a non-functional environment.
- **Proposed fix**: Regenerate via `pip-compile --generate-hashes -o requirements-lock.txt pyproject.toml`. Verify the output includes keyring, websockets, and the test/build extras. Add a CI step that does `pip install --require-hashes -r requirements-lock.txt` and imports `voice_typer.server.credential_store` + `voice_typer.server.sidecar_ws` to catch future drift.
- **Confidence**: High

### S4-CR-25 — 4 known CVEs in `transformers==4.57.6` (pin blocks fix)
- **Category**: Dependency health
- **Severity**: High
- **Location**: `pyproject.toml:89` (transformers pin) + `requirements-lock.txt:41`
- **Evidence**: pip-audit reports 4 known CVEs in transformers==4.57.6: PYSEC-2025-217 (no fix), PYSEC-2026-2288 (fix 5.0.0), PYSEC-2026-2289 (fix 5.3.0), PYSEC-2026-2290 (fix 5.5.0). pyproject.toml pins `transformers>=4.50,<5.0` with comment: "BUILD-N04: pin to <5.0. The 4.x -> 5.x boundary renamed AutoProcessor methods and removed AutoModelForCTC, both of which we use." Grep shows actual code uses `AutoProcessor.from_pretrained()` (parakeet_engine.py:387 — still supported in 5.x) and `AutoModelForTDT` (parakeet_engine.py:207 — NOT `AutoModelForCTC` as the comment claims; `AutoModelForTDT` was added in 4.50 and is still present in 5.x). The blocking concern in the comment is misdocumented.
- **Impact**: 4 known CVEs in the dependency tree, 2 of which have publicly-listed fixes that the pin blocks. Weekly pip-audit-weekly job keeps opening issues about these.
- **Proposed fix**: (1) Verify `AutoProcessor.from_pretrained` and `AutoModelForTDT` work in transformers 5.x by running the parakeet_engine tests against a 5.x install. (2) If they work, raise the upper bound to `<6.0` and bump the pin to `transformers==5.5.0` (or latest 5.x). (3) Update the BUILD-N04 comment to reflect the actual API surface used.
- **Confidence**: Medium

### S4-CR-26 — `electron-builder.yml` references nonexistent `resources/linux/postinst*` (Linux installer broken)
- **Category**: Packaging
- **Severity**: High
- **Location**: `voice_typer/client/electron-builder.yml:127-135`
- **Evidence**: `deb.afterInstall: "resources/linux/postinst"`, `afterRemove: "resources/linux/prerm"`, `rpm.afterInstall: "resources/linux/postinst.rpm"`, `afterRemove: "resources/linux/prerm.rpm"`. The directory `voice_typer/client/resources/` does NOT exist. The actual scripts live at `scripts/linux/postinst*` / `prerm*`. `generate-icons.mjs` (the `prebuild` step) only writes `resources/icon.png` / `resources/icon-256.png` — it never creates `resources/linux/`. `build.yml::build-linux` has NO copy step.
- **Impact**: electron-builder will fail when packaging the .deb/.rpm because the afterInstall/afterRemove script paths don't exist. Either the Linux build silently produces installers WITHOUT the postinst hooks (broken: no udev rule installed, no input group added, native hotkey cannot read /dev/input/event*) or electron-builder hard-fails on missing files.
- **Proposed fix**: Change the four `resources/linux/...` references in electron-builder.yml to `../../scripts/linux/...`. Or, in build.yml::build-linux before the `npx electron-builder` step, add: `mkdir -p voice_typer/client/resources/linux && cp scripts/linux/{postinst,prerm,postinst.rpm,prerm.rpm} voice_typer/client/resources/linux/`.
- **Confidence**: High

### S4-CR-27 — `WIN_SIGN_COMMAND` env var referenced but never set (Tauri Windows build hard-fails)
- **Category**: Packaging
- **Severity**: High
- **Location**: `src-tauri/tauri.conf.json:82-88`
- **Evidence**: `"windows": { "signCommand": "${WIN_SIGN_COMMAND}" }`. Grep across `.github/workflows/`, `scripts/build/`, and `docs/` shows `WIN_SIGN_COMMAND` is NEVER SET — only referenced. tauri-macos-build.yml / tauri-linux-build.yml do not set it either; tauri-windows-build.yml also never sets it (it does its own signtool signing AFTER the bundle is built, on the final NSIS/MSI). Tauri v2's bundler requires `signCommand` to be a non-empty string when the field is present; an empty value causes `tauri build` to fail with "Configuration: bundle.windows.signCommand is empty" rather than skipping signing.
- **Impact**: Even when the per-platform Windows Tauri workflow is enabled (Phase 0-W gate flip), `cargo tauri build` will hard-fail at the bundler step. The post-hoc signtool signing in `tauri-windows-build.yml` is moot because the bundle is never produced. Worse: this blocks ALL Windows Tauri release builds, not just signed ones — Tauri doesn't gracefully degrade.
- **Proposed fix**: Either (a) remove `"signCommand"` from tauri.conf.json and rely solely on post-build signtool, or (b) keep it but gate it: when `WIN_SIGN_COMMAND` is empty, Tauri skips. Document in `signing-guide.md` which path is canonical. Option (a) is simpler and matches the macOS path.
- **Confidence**: High

### S4-CR-28 — `tauri.conf.json` missing required macOS Info.plist keys (NSMicrophoneUsageDescription etc.)
- **Category**: Packaging / Cross-platform
- **Severity**: High
- **Location**: `src-tauri/tauri.conf.json:85-88` + `docs/migration/signing-guide.md:188-194`
- **Evidence**: signing-guide.md §"Required Info.plist keys" mandates `NSMicrophoneUsageDescription` ("required") + `NSUserNotificationsUsageDescription` ("required") + `LSMinimumSystemVersion=13.0` + `LSUIElement=false` + `CFBundleIdentifier=com.voicetyper.app`. `tauri.conf.json` has only `signingIdentity` + `entitlements` — no `bundle.macOS.infoPlist` block, no `bundle.macOS.minimumSystemVersion`. Grep confirms zero matches for `NSMicrophoneUsageDescription`, `infoPlist`, `minimumSystemVersion` in the file.
- **Impact**: macOS 10.14+ hard-rejects mic access without `NSMicrophoneUsageDescription` — the app will crash on first dictation with TCC denial. No `LSMinimumSystemVersion` means the .app installs on macOS 10.13+ but the native binary uses NSEvent.modifierFlags.function + CGEvent APIs that have 11+/12+ behavior changes — silent crashes on older macOS. `NSUserNotificationsUsageDescription` missing → `tauri-plugin-notification` toasts silently fail on macOS 11+.
- **Proposed fix**: Add to tauri.conf.json `bundle.macOS`:
  ```json
  "minimumSystemVersion": "13.0",
  "infoPlist": {
    "NSMicrophoneUsageDescription": "Voice Typer needs microphone access to transcribe your speech to text.",
    "NSUserNotificationsUsageDescription": "Voice Typer posts native notifications for dictation events and errors."
  }
  ```
- **Confidence**: High

### S4-CR-29 — `sync_versions.py` doesn't cover Cargo.toml + tauri.conf.json; CI check is no-op
- **Category**: Build pipeline
- **Severity**: High
- **Location**: `scripts/build/sync_versions.py:135-156` + `src-tauri/Cargo.toml:3` + `src-tauri/tauri.conf.json:4`
- **Evidence**: sync_versions.py only updates pyproject.toml, `voice_typer/__init__.py`, `voice_typer/client/package.json`, `voice_typer/client/electron-builder.yml`. It does NOT touch `src-tauri/Cargo.toml` (`version = "1.0.0"`) or `src-tauri/tauri.conf.json` (`"version": "1.0.0"`). build.yml::version-check runs `python scripts/build/sync_versions.py` (no `--check` flag) — so CI also doesn't catch drift.
- **Impact**: After the first version bump on pyproject.toml, the Tauri installer reports a different version than the Electron installer. Cutover-playbook.md §"Mixed-mode period" admits there's no runtime marker to distinguish builds, so support cannot tell users apart; mixed-mode release notes become unreliable because both installers report `1.0.0` indefinitely. When the Tauri path is enabled, `cargo tauri build` will stamp the .dmg/.deb with the Cargo.toml version, not the source-of-truth version.
- **Proposed fix**: Add `src-tauri/Cargo.toml` and `src-tauri/tauri.conf.json` to `sync_versions.py`: regex-replace `^version = "..."` (Cargo.toml) and `"version": "..."` (tauri.conf.json). Add them to `collect_versions()`. Run `sync_versions.py --check` as a hard gate in `build.yml::version-check`.
- **Confidence**: High

### S4-CR-30 — `build_tauri_all.sh` Phase 1d omits Windows `.exe` suffix (verification false-fails)
- **Category**: Build pipeline
- **Severity**: High
- **Location**: `scripts/build/build_tauri_all.sh:250-255`
- **Evidence**: `SIDECAR_BIN="$SRC_TAURI/bin/python-sidecar-$TARGET_TRIPLE"` then `if [[ ! -f "$SIDECAR_BIN" ]]; then echo "ERROR: sidecar binary not found..."; exit 4; fi`. On Windows, `TARGET_TRIPLE = x86_64-pc-windows-msvc` but the actual sidecar file is `python-sidecar-x86_64-pc-windows-msvc.exe` (verified in `build_sidecar_windows.sh:64-65`). `[[ -f ".../python-sidecar-x86_64-pc-windows-msvc" ]]` is false (no .exe).
- **Impact**: `build_tauri_all.sh` Phase 1d always exits 4 on Windows after a successful `cargo tauri build`, hiding the real bundle artifacts behind a bogus "sidecar not found" error. Developers will assume the build failed even though it succeeded.
- **Proposed fix**: Add an `EXE_SUFFIX` based on host platform: `windows) EXE_SUFFIX=".exe" ;; *) EXE_SUFFIX="" ;;` then `SIDECAR_BIN="$SRC_TAURI/bin/python-sidecar-$TARGET_TRIPLE$EXE_SUFFIX"`. Mirror the logic that `nuitka_freeze.sh` already has at lines 144-148.
- **Confidence**: High

### S4-CR-31 — macOS universal Tauri .dmg ships arm64-only native hotkey binary
- **Category**: Packaging / Cross-platform
- **Severity**: High
- **Location**: `.github/workflows/tauri-macos-build.yml:237-247` + `:98-101`
- **Evidence**: `build-aarch64` job builds + uploads `src-tauri/resources/native/macos-key-listener` (arm64-only, compiled natively on macos-14). `build-x86_64` job does NOT build `macos-key-listener` at all (its Upload artifact path list omits the native listener). Then `build-tauri-universal` job downloads both, but at line 246 runs `scripts/build/build_native_listener_macos.sh` AGAIN on the arm64 host, producing an arm64-only binary, then bundles that into the "universal" .app. The x86_64 listener is never used.
- **Impact**: A "universal" Tauri .dmg ships with an arm64-only native hotkey binary. Intel Macs (still ~30% of macOS install base per Apple's 2024 numbers) will get a "bad CPU type in executable" or silent crash when the Tauri host tries to spawn `resources/native/macos-key-listener` on x86_64.
- **Proposed fix**: In `build-tauri-universal` job, after downloading both arch artifacts: `lipo -create src-tauri/resources/native/macos-key-listener-aarch64 src-tauri/resources/native/macos-key-listener-x86_64 -output src-tauri/resources/native/macos-key-listener`. Have `build-aarch64` + `build-x86_64` upload per-arch listener artifacts (build-x86_64 currently doesn't even build the listener — add the step). Mirror the Electron path's `build-macos-universal` job.
- **Confidence**: High

### S4-CR-32 — Per-arch native binaries missing for Windows aarch64 / Linux aarch64 / macOS universal
- **Category**: Cross-platform
- **Severity**: High
- **Location**: `src-tauri/tauri.conf.json:58-68` + `scripts/build/compile_native.sh` + `voice_typer/server/native_hotkeys/binary_path.py:19-23`
- **Evidence**: tauri.conf.json bundles `resources/native/windows-key-listener.exe`, `resources/native/macos-key-listener`, `resources/native/linux-key-listener` as single-arch files. Prewarm binaries ARE arch-specific (`prewarm-x86_64-pc-windows-msvc.exe`, `prewarm-aarch64-pc-windows-msvc.exe`, etc. — 7 entries), but key-listener binaries are NOT. The compile scripts have no arch flag. The factory `binary_path.py:19-23` looks up by `sys.platform` only, with no arch suffix handling.
- **Impact**: On Windows aarch64 (Surface Pro X, Snapdragon laptops), the bundled x86_64 `windows-key-listener.exe` runs via Windows 11's x64 emulation layer — works but with overhead and only on Win11. On macOS aarch64 (Apple Silicon), the bundled x86_64 `macos-key-listener` requires Rosetta 2 (an optional install); without it, the binary fails to launch and the hotkey dispatcher falls back to the legacy `PynputHotkey`. On Apple Silicon with Rosetta installed, there's a startup penalty + memory overhead. Linux aarch64 (Raspberry Pi 4/5, Ampere servers) gets an x86_64 binary that simply fails to exec.
- **Proposed fix**: Either (a) build universal binaries (macOS `swiftc -O -target universal-apple-darwin ...`), or (b) ship per-arch variants and update `binary_path.py:_BINARY_NAMES` to be keyed by `(platform, machine)` with arch detection via `platform.machine()`. Update `tauri.conf.json` `resources` list to include all per-arch variants. Update build scripts to accept an arch argument like `build_prewarm_windows.sh` already does.
- **Confidence**: High

### S4-CR-33 — Tauri bridge missing 4 window_ methods + 3 bubble methods (silent feature loss)
- **Category**: Frontend architecture / Cross-platform
- **Severity**: High
- **Location**: `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts:240-393, 408-499` vs `voice_typer/client/src/preload/index.ts:80-135` + `src/preload/bubble.ts:18-123`
- **Evidence**: Tauri bridge `window_` object has only: minimize, toggleMaximize, close, isMaximized, onMaximizedChanged, exportHistory, exportVocabulary. Electron preload exposes ALL of those PLUS: `openLogs`, `openModelImportDialog`, `exportTemplates`, `exportConfig`. Bridge header comment claims "the renderer code works unchanged on both runtimes" — FALSE for the four missing methods. Bubble bridge similarly omits `startDrag`/`drag`/`endDrag` + `onSetState`/`resizeTo`/`toggleDictation`. The `MainRendererBubble` type marks these optional so callers must use `?.` — meaning the missing methods silently no-op rather than failing loudly.
- **Impact**: Tauri users silently lose: (1) "Open log folder" button (shows error toast), (2) "Import model" button (shows "not available outside Electron" warning), (3) templates/config GDPR export, (4) bubble drag-to-move, (5) bubble visual state indicator (idle/recording/transcribing), (6) bubble auto-resize (transparent dead zone around the pill that blocks clicks to underlying windows), (7) bubble mic-button toggle.
- **Proposed fix**: (a) Implement the 4 missing window_ commands as Tauri invoke calls (`open_logs`, `open_model_import_dialog`, `export_templates`, `export_config`) in `src-tauri/src/commands/` and wire them in `tauri-bridge.ts`; or (b) update the WindowBridge type to mark them truly optional AND surface a runtime feature-detection API. Add three Rust commands for bubble: `bubble_resize(width, height)`, `bubble_emit_state(state)`, `bubble_toggle_dictation`. Register them in `main.rs:generate_handler!`. Then wire `onSetState`, `resizeTo`, `toggleDictation` in `tauri-bridge.ts`.
- **Confidence**: High

### S4-CR-34 — `register_tray_labels` ImportError breaks server-side i18n for 6 of 8 locales
- **Category**: Localization / i18n
- **Severity**: High
- **Location**: `voice_typer/server/handlers/system_handlers.py:135-154` + `voice_typer/server/tray.py` (missing)
- **Evidence**: The `set_tray_locale` IPC handler does `from voice_typer.server.tray import (get_tray_locale, register_tray_labels, set_tray_locale)`. Verified via runtime probe: `python -c "from voice_typer.server.tray import register_tray_labels"` → `ImportError: cannot import name 'register_tray_labels'`. Tests `tests/test_tray.py::TestTrayLocaleFullCoverage::test_register_tray_labels_adds_locale` and `test_register_tray_labels_merges_over_existing` FAIL with ImportError.
- **Impact**: Every locale change in Settings → "App Language" produces an ImportError inside the IPC handler. The exception is caught and returned as `{type:"error", data:{message:"..."}}`; the renderer's `void window.python?.call(...)` ignores the rejection. The tray menu STAYS in English for the 6 of 8 locales that have no hard-coded dict (`ar`, `de`, `fr`, `hi`, `ru`, `zh`). Server-side notification strings (`notify.app.repaste_no_previous`, `notify.startup_sequence.crash_title`, etc.) defined in `voice_typer/server/i18n.py:_INITIAL_LABELS` are NEVER translated.
- **Proposed fix**: Add `register_tray_labels(locale: str, labels: dict[str, str]) -> None` to `voice_typer/server/tray.py` that merges `labels` into `_TRAY_LABELS_LOCALES[locale]` (creating the entry if absent) under a module-level lock, then update `set_tray_locale` to honor newly-registered locales. Mirror the existing pattern in `voice_typer/server/i18n.py:register_locale`.
- **Confidence**: High

### S4-CR-35 — `ALLOWED_COMMANDS` missing 3 commands (onboarding flow degraded on Electron)
- **Category**: API & IPC contract stability
- **Severity**: High
- **Location**: `voice_typer/client/src/main/index.ts:79-191` (`ALLOWED_COMMANDS`) vs `voice_typer/server/ipc_server.py:1884-1997` (`_COMMAND_REGISTRY`)
- **Evidence**: Programmatic diff shows 3 commands in Python registry but NOT in Electron allowlist: `onboarding_check_permissions`, `onboarding_get_model_catalog`, `tray_click`. The parity test `tests/test_electron_ipc_and_build.py::TestAllowlistCorrectness::test_allowlist_matches_server_commands` is FAILING — verified by running pytest: "AssertionError: Allowlist is missing server commands (renderer calls would be silently rejected): ['onboarding_check_permissions', 'onboarding_get_model_catalog', 'tray_click']". The Tauri side (`src-tauri/src/commands/sidecar_cmds.rs:dispatch`) has NO allowlist at all.
- **Impact**: Under Electron, the renderer's onboarding flow cannot call `onboarding_check_permissions` (Permissions step walkthrough for macOS Accessibility / Linux input group) or `onboarding_get_model_catalog` (full rich-metadata model catalog picker) — the calls are rejected by `sendToPython` with `"Disallowed IPC command: <cmd>"` before reaching the backend. Stack divergence: Tauri users get the full feature set; Electron users get degraded onboarding.
- **Proposed fix**: Add `"onboarding_check_permissions"`, `"onboarding_get_model_catalog"`, and `"tray_click"` to the `ALLOWED_COMMANDS` set in `voice_typer/client/src/main/index.ts`. Mark `test_allowlist_matches_server_commands` as a non-skippable CI gate.
- **Confidence**: High

### S4-CR-36 — History DB migration non-transactional (partial migration → unrecoverable state)
- **Category**: Data integrity
- **Severity**: High
- **Location**: `voice_typer/server/history_db.py:514-545`
- **Evidence**: `_init_db_schema` runs each migration statement in a try/except that only logs a warning (`log.warning("[HISTORY_DB] Migration statement failed: %s", e)`) and CONTINUES. After the loop, the schema_meta version is unconditionally bumped to `_CURRENT_SCHEMA_VERSION` and committed. Example: if `_MIGRATION_V2`'s `ALTER TABLE transcriptions ADD COLUMN favorite INTEGER DEFAULT 0;` fails (e.g., disk full mid-ALTER), the `favorite` column is missing but `schema_meta.version = 2`. On next launch, the migration loop range is empty, so the column is never added. `idx_favorite` then fails to create at line 553 — but that's also a `try`-less `cursor.execute` that would propagate as `sqlite3.OperationalError` to the writer thread init, which surfaces as `_init_error` and silently disables all writes.
- **Impact**: A partial migration leaves the schema in an inconsistent state that's never self-healing. The user sees "history not working" with no recovery path. A user-triggered `PRAGMA writable_schema` repair would be needed.
- **Proposed fix**: Wrap the migration loop in an explicit `BEGIN; … COMMIT;` (or use `with conn:` context manager). On ANY `sqlite3.Error` mid-migration, `conn.rollback()` and DO NOT bump the version — let the next launch retry. If the same migration fails N times, surface a "history DB schema is corrupt, click here to reset" notification.
- **Confidence**: High

### S4-CR-37 — `Config.save()` races with `migrate_secrets_to_keyring` (reverts key migration)
- **Category**: Data integrity / Security
- **Severity**: High
- **Location**: `voice_typer/server/credential_store.py:631-842` vs `voice_typer/server/config.py:986-1059`
- **Evidence**: `migrate_secrets_to_keyring` acquires `config.json.lock` (POSIX `fcntl.flock`, Windows `msvcrt.locking`) for the read-migrate-write sequence. But `Config.save()` does NOT acquire that lock — it goes straight to `_secure_atomic_write`. The migration can run concurrently with a `Config.save()` from any IPC handler (e.g., user toggles a checkbox while migration is in flight). Sequence: (a) `Config.load()` reads plaintext key into memory; (b) migration acquires lock, reads same plaintext, writes `keyring://openai` reference + `secrets_migrated=True` to disk; (c) `Config.save()` (no lock) writes the FULL in-memory Config — including the original plaintext `openai_api_key` and `secrets_migrated=False` — overwriting the migration's reference token. Net effect: plaintext key is back on disk, `secrets_migrated=False`, and the next launch re-runs the migration. The user's config.json contains plaintext API keys for the window between the clobbering save and the next launch.
- **Impact**: Plaintext API keys persist on disk despite the documented "RW-01: API keys stored in OS keychain" guarantee. The migration appears to "complete" but is silently reverted. Two-instance race widens the window.
- **Proposed fix**: Either (a) acquire `config.json.lock` inside `Config.save()` (cheap on POSIX, ~ms) so save() never races with migration; or (b) have `Config.save()` re-resolve `keyring://` references (write the reference token, not the in-memory plaintext, when `secrets_migrated=True`); or (c) re-check the `secrets_migrated` flag from disk at the start of `Config.save()` and skip writing API-key fields if it's True. Option (a) is the cleanest. Add a regression test that runs `migrate_secrets_to_keyring` and `Config.save()` on two threads concurrently and asserts the on-disk key is the `keyring://` reference, not plaintext.
- **Confidence**: High

### S4-CR-38 — `ALLOWED_USER_MODELS` rejects multilingual model names (silently reverts to English-only)
- **Category**: Configuration management
- **Severity**: High
- **Location**: `voice_typer/server/config.py:1277-1278` vs `voice_typer/server/onboarding.py:330-395`
- **Evidence**: `Config.load()` enforces `if data.get("model_size") not in ALLOWED_USER_MODELS: data["model_size"] = "small.en"` where `ALLOWED_USER_MODELS = {"tiny.en", "small.en", "medium.en", "qwen", "parakeet"}`. But `OnboardingController.MODEL_OPTIONS` (onboarding.py:356-394) explicitly offers `"tiny"`, `"small"`, `"medium"` (multilingual, no `.en` suffix), and the renderer's model catalog (model_registry.py:95+) defines the same. `apply_settings` (onboarding.py:454) does `config.model_size = self.selected_model` then `config.save()` — the multilingual value is persisted. On the NEXT launch, `Config.load()` reads `"small"`, sees it's not in the allowlist, and silently resets to `"small.en"`.
- **Impact**: Non-English users who pick a multilingual model in onboarding silently get English-only Whisper after the first restart. They have no signal that their choice was reverted unless they read the log. Likely affects a large fraction of non-English users.
- **Proposed fix**: Either (a) extend `ALLOWED_USER_MODELS` to `{"tiny.en","small.en","medium.en","tiny","small","medium","large-v3","qwen","parakeet"}` (matching `model_registry.py`); or (b) replace the hardcoded set with a lookup against `model_registry.get_all_models()` so the allowlist can't drift. Option (b) is the durable fix. Add a regression test that asserts every name in `OnboardingController.MODEL_OPTIONS` is in `ALLOWED_USER_MODELS`.
- **Confidence**: High

### S4-CR-39 — Tauri config dir skips legacy `~/.voice-typer` migration (Python+Tauri split-brain)
- **Category**: Data integrity / Cross-platform
- **Severity**: High
- **Location**: `src-tauri/src/platform/paths.rs:60-173` vs `voice_typer/server/config.py:445-447`
- **Evidence**: Python's `_config_dir()` checks `legacy = Path.home() / ".voice-typer"; if legacy.exists(): return legacy` FIRST (config.py:445-447). The Electron main process mirrors this in `computeConfigDir()` (single_instance.ts:37-42). But Tauri's `config_dir_from_env` skips the legacy check entirely — it goes straight to env-var → platform-default. For a user upgrading from a legacy `~/.voice-typer` install: Tauri host writes log files / single-instance lock / PID files to `~/.local/share/voice-typer/` (Linux default), while the Python sidecar (launched by Tauri) reads `config.json` from `~/.voice-typer/`.
- **Impact**: Tauri host and Python sidecar disagree on config dir for legacy installs. Tauri can't find the backend PID file (so single-instance detection fails → duplicate launches). Tauri's log file is in a different dir than Python's. The Python side keeps working (it sees the legacy dir), but Tauri-side state (window placement, theme, recent-files) is silently split-brain.
- **Proposed fix**: Add the legacy `~/.voice-typer` check at the top of `config_dir_from_env` (or in `config_dir` before delegating): `let legacy = home.map(|h| PathBuf::from(h).join(".voice-typer")); if let Some(p) = legacy { if p.exists() { return p; } }`. Mirror the `VOICE_TYPER_CONFIG_DIR` override check too. Add a Rust test that mirrors `tests/test_paths.py` to lock the resolution order.
- **Confidence**: High

### S4-CR-40 — `_secure_atomic_write` uses fixed tmp name → concurrent writes collide (EEXIST)
- **Category**: Data integrity
- **Severity**: High
- **Location**: `voice_typer/server/config.py:64-126`
- **Evidence**: `tmp_path = path.with_suffix(path.suffix + ".tmp")` is a fixed name. With `O_EXCL` on POSIX, a second concurrent caller's `os.open(... O_EXCL)` fails with `EEXIST` → `except Exception` → `tmp_path.unlink()` (deletes the FIRST caller's tmp!) → re-raise. Two realistic concurrent callers: (a) `Config.save()` from the main thread after a settings change, and (b) `credential_store._write_plaintext_fallback()` from an IPC handler thread that just stored a new API key. Both target `config.json`. The second one's failure surfaces as `Config.save()` returning False (logged at ERROR level) and the user's setting silently not persisting.
- **Impact**: Concurrent writes to the same JSON file (config.json, corrections.json, templates.json, vocabulary.json) can lose one of the writes silently. The pattern recurs in `vocabulary._save_user`, `templates._save`, `crash_recovery._save_sync`, `onboarding.mark_complete`, `duck_crash_recovery.save`, `autostart_launcher._write_pid_file` — all call `_secure_atomic_write`.
- **Proposed fix**: Use `tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")` to get a unique tmp name per call; `os.close(fd)` then `os.open(tmp, O_WRONLY|O_TRUNC|O_NOFOLLOW)`. Alternatively, include `os.getpid()` + `threading.get_ident()` in the tmp suffix. Add a multithreaded regression test that hammers `Config.save()` from 4 threads and asserts no False return.
- **Confidence**: High


## Top Priorities (Critical, must-fix-this-run)

1. **CR-1** — Tauri tray menu clicks emit a `dispatch` EVENT instead of invoking the `dispatch` COMMAND → tray menu is completely non-functional on the Tauri path. `[verified]`
2. **CR-2** — Two parallel `IPCServer` implementations (`ipc_server.py` 2609 LOC + `ipc/server.py` 1764 LOC) — ~2650 LOC of dead/duplicate code from a half-finished Phase 4.5 split. Production imports from the OLD god-module; the NEW package's `IPCServer` is dead. `[verified]`
3. **CR-3** — FT-1 supervisor orphans the old Python sidecar process on WS-reconnect failure (no `kill_tree()` on the old `SidecarHandle` before reassignment; no `Drop` impl). Up to 5 zombie Python sidecars can accumulate per flap cycle. `[verified]`
4. **CR-4** — Tauri v2 Linux `.deb` and `.rpm` bundles silently skip udev-rule / input-group / Caps Lock setup because `install_permissions.py` is NOT in `tauri.conf.json:bundle.resources`. Native hotkeys broken on EVERY Linux install. RPM `postinst.rpm` was never updated with the NF-R9-2 path-probe fix. `[verified]`
5. **CR-5** — AudioProcessor filter chain resamples each chunk to 16 kHz, then `Recorder.stop()`/`snapshot()` resample AGAIN from device native rate → every dictation on non-16 kHz mics produces 3×-too-short garbage audio. `[verified by code-flow]`
6. **CR-6** — SECURITY.md says "69" `ALLOWED_COMMANDS`, actual is 70 — test failing every CI run, blocking builds/releases. `[verified by test execution]`
7. **CR-7** — Allowlist parity test failing: `tray_click`, `onboarding_check_permissions`, `onboarding_get_model_catalog` missing from Electron `ALLOWED_COMMANDS` — 3 renderer features are silent no-ops + CI broken. `[verified by test execution]`

## Session 5 Findings

Session 5 was a UX/UI-focused review covering accessibility (WCAG), visual polish, ARIA patterns, color contrast, and frontend component correctness. Findings from this session are organized under severity sub-headings below. No consolidated section header existed prior to this edit.

## High-Severity Findings

### S5-CR-8 — ARCHITECTURE.md stale claims (4 sites)
- **Severity**: High · **Category**: Documentation · **Status**: Pending
- **Location**: `docs/ARCHITECTURE.md:60, 68, 15, 86, 67`
- **Evidence**: (1) Line 60 says `voice_typer/client/src/main/index.ts` is 2,205 lines — actual is 310 lines (REF-2 extraction split it). (2) Line 68 says `src-tauri/src/main.rs` is 1,866 lines — actual is 234 lines. (3) Lines 15, 86 say 68-command `_COMMAND_REGISTRY` — actual is 69. (4) Line 67 says "**No `core:tray:*`**" — actual `migrate-runtime.json:31-38` grants `core:tray:default` + 7 tray perms; Rust host OWNS the tray under Tauri per `tray.rs:105-160`.
- **Root cause**: Architecture doc was not updated when REF-2 (Electron main split), Tauri main split, PERF-005 (relaunch_ack), and tray-ownership flip landed.
- **Impact**: Maintainers/new contributors form an inaccurate mental model: believe index.ts is a 2,205-line god-file (it's split), main.rs is a 1,866-line monolith (it's split), IPC surface is 68 commands (it's 69), Tauri path has no tray perms (it has 8).
- **Proposed fix**: Update the four stale claims in `ARCHITECTURE.md`. Replace line 60's "(2,205 lines)" with "(310 lines — wiring-only; logic in ./python/, ./ipc/, ./windows/, ./bootstrap)"; replace line 68's "(1,866 lines)" with "(234 lines — wiring-only; logic in mod sidecar/commands/platform/tray)"; replace "68-command" with "69-command" (lines 15, 86); replace the capabilities row's "**No `core:tray:*`**..." with "**`core:tray:default` + 7 tray perms** (Rust host owns the tray; pystray is the Electron-fallback path only)".

### S5-CR-17 — ruff-baseline.json misrepresents code quality; F-rule violations silently bypass CI; SEC-audit-008 audio-buffer-clearing silently broken
- **Severity**: High · **Category**: Existing warnings/errors · **Status**: Pending
- **Location**: `voice_typer/server/recording/recorder.py:1228, 1233` (F821 `_secure_clear_array` undefined) ; `voice_typer/server/config_validators.py:886-894` (F822 9 phantom `__all__` entries) ; `voice_typer/server/recording/recorder.py:2288` (F841 unused local) ; `voice_typer/server/startup_tasks.py:273` (F841 unused local) ; `voice_typer/server/hotkeys/__init__.py:56` (F401 unused import) ; `ruff-baseline.json` (stale UP037:3, should be 61 violations across 10 rules)
- **Evidence**: `ruff check voice_typer/server/ --select F821` reports `_secure_clear_array` undefined at recorder.py:1228 and 1233. The function IS defined in `recording/buffer.py:37` and re-exported in `recording/__init__.py:114`, but `recorder.py` uses a bare-name `_secure_clear_array(...)` call instead of the `_recording_pkg._secure_clear_array(...)` pattern that the module docstring (lines 5-22) explicitly promises for cross-submodule helpers. The call sites wrap the lookup in `try/except Exception: pass`, so the resulting `NameError` is silently swallowed. pyrefly independently confirms: `ERROR Could not find name '_secure_clear_array' [unknown-name]`. `ruff --select F822` reports 9 undefined names in `__all__`: `_check_hotkey_type`, `_check_hotkey_length`, `_check_hotkey_not_empty`, `_check_hotkey_has_parts`, `_check_universal_reserved_shortcut`, `_check_per_platform_shortcut`, `_check_win_key_on_windows`, `_check_cmd_letter_on_macos`, `_check_alt_shift_on_windows`. The actual functions in the module are named `_check_basic_shape`, `_check_universal_reserved`, `_check_platform_reserved`, `_check_alt_shift`, `_check_ctrl_letter`, `_check_shift_letter`, `_check_single_alphanumeric`, `_check_os_shell_combos`. ruff-baseline.json incorrectly records only UP037:3 (stale by 47 violations).
- **Root cause**: ruff-baseline.json was last regenerated when the only violations were 3 UP037 — but UP037 was later fixed without regenerating. New violations accumulated across F/E/SIM/N/UP rules without CI catching them. The `_secure_clear_array` NameError is the result of an import forgotten during the ARCH-045 extraction of `recorder.py` from the original `recording.py` god-module.
- **Impact**: (a) SEC-audit-008 security control is silently broken — audio buffers containing potentially sensitive dictated speech (passwords, medical info, etc.) are NOT securely zeroed in process memory between dictation sessions. The comment at `recorder.py:1220-1225` explicitly claims this is the secure-clearing implementation; the actual behavior is "swallow NameError, do nothing." (b) `from voice_typer.server.config_validators import *` raises `AttributeError` at import time. (c) CI's `Ruff (F-rules hard-fail)` step at `build.yml:99` should be failing every PR.
- **Proposed fix**: (a) Replace both `_secure_clear_array(self._cached_resampled)` and `_secure_clear_array(self._cached_no_resample_arr)` with `_recording_pkg._secure_clear_array(...)` (matching the existing pattern at lines 2575 and 2992 of the same file for `_secure_clear_array_background`). (b) Replace the 9 phantom names in `__all__` with the actual function names. (c) Delete the F841 unused locals (`recorder.py:2288`, `startup_tasks.py:273`). (d) Delete the F401 unused `logging` import (`hotkeys/__init__.py:56`). (e) Regenerate `ruff-baseline.json` after fixes land.

### S5-CR-20 — Models page sticky tab bar has no background — content bleeds through when scrolling
- **Severity**: High · **Category**: Product Experience / visual consistency · **Status**: Pending
- **Location**: `voice_typer/client/src/renderer/src/pages/Models.tsx:1083-1141`
- **Evidence**: Models.tsx sticky tab bar uses `className="sticky left-0 right-0 top-0 z-50 "` — NO background color, NO border. Content below uses `pt-[156px]` (156px top padding). Compare to Settings.tsx:748 which uses `bg-(--bg-subtle) border-b border-border py-1.5` and content uses `pt-6` (24px). All other 7 pages use `pt-28` (112px). Models.tsx is the ONLY outlier with `pt-[156px]`.
- **Root cause**: Sticky tab bar was added without the visual treatment Settings uses (bg-subtle + border). The 156px padding is a magic-number workaround.
- **Impact**: When scrolling Models page, model card text bleeds through the transparent tab bar (janky, hard to read tab labels). 156px of dead space above PageHeading looks like a layout bug. Two pages with the same conceptual pattern (sticky tabs) look completely different.
- **Proposed fix**: Make Models.tsx tab bar match Settings.tsx pattern: `<div className="sticky left-0 right-0 top-0 z-40 bg-(--bg-subtle) border-b border-border py-1.5">`. Change `pt-[156px]` → `pt-6`.

### S5-CR-21 — Bubble recording/idle modes have no visible label (no mic icon, no "Recording" text)
- **Severity**: High · **Category**: Product Experience / discoverability · **Status**: Pending
- **Location**: `voice_typer/client/src/renderer/src/Bubble.tsx:455-464` (idle) ; `:470-485` (recording)
- **Evidence**: In `mode === "idle"` the bubble renders `<div className="flex h-6 items-center" />` — an EMPTY 24px-tall pill with no visible content (only an sr-only span for screen readers). In `mode === "recording"` it renders ONLY 7 vertical bars (`<span>` elements) with no text label, no mic icon, no "Recording" text. The bubble's only "label" is the `<output aria-label={t("bubble.recordingIndicatorAria")}>` which is invisible to sighted users. The mic button (lines 488-542) only renders when `behavior === "always_visible" && micButton !== false && clickToToggle !== false` — THREE separate config flags must all be ON.
- **Root cause**: Bubble was designed as a pure abstract visualizer; no thought given to first-time-user legibility. The mic button's three-way AND gating means most users who enable "Always Visible" alone will see an empty pill with no obvious action.
- **Impact**: New users seeing the always-visible bubble as an empty pill may not realize it's Voice Typer; they may try to click it (does nothing without the mic button enabled) and conclude the app is broken. During recording, the bars-only display is uninterpretable to anyone who hasn't read the docs — competitors like macOS Voice Control show "Listening" text.
- **Proposed fix**: (a) In idle mode, render a tiny mic icon + "Ready" text inside the pill. (b) In recording mode, add a small "● REC" indicator or keep bars but add an sr-only + visible 1-line label. (c) Simplify the mic button gating: show it whenever `bubble_behavior === "always_visible"` (drop the two additional flags) — and enable all three by default for new users.

### S5-CR-22 — App connecting screen has hardcoded English 3-step status list
- **Severity**: High · **Category**: Product Experience / Localization · **Status**: Pending
- **Location**: `voice_typer/client/src/renderer/src/App.tsx:510-528`
- **Evidence**: The "Starting Python backend…" header uses `t("app.startingBackend")` (i18n), but the 3-step list items below it are hardcoded English strings: `{connectingProgress !== null ? "✓" : "①"} Starting Python` ... `Loading model` ... `{"③"} Ready`. The app supports 8 languages (en/ar/de/es/fr/hi/ru/zh) per `i18n/translations/`.
- **Root cause**: The 3-step indicator was added after the initial i18n pass; the strings were never extracted to translation keys.
- **Impact**: International users get a half-translated screen at the worst possible moment (waiting for first launch). Erodes confidence in the app's localization quality.
- **Proposed fix**: Add `app.connecting.step1StartingPython`, `app.connecting.step2LoadingModel`, `app.connecting.step3Ready` keys to all 8 translation files. Use `t()` in App.tsx.

## Medium-Severity Findings (selected — full list in worklog.md)

### S5-CR-26 — `_handle_set_config` reaches into `self.app._waveform_bubble` (private attr) — ADR-0008 §3.1 violation
- **Severity**: Medium · **Category**: Backend architecture · **Location**: `voice_typer/server/handlers/config_handlers.py:164-166`
- **Proposed fix**: Add `push_bubble_config(config)` to `VoiceTyperService`; encapsulate the private access inside the service.

### S5-CR-27 — `recorder.py` is a 2992-LOC god class mixing 5 sub-concerns
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/recording/recorder.py:228-2992`
- **Proposed fix**: Split into `recording/{device_resolver,vad_controller,audio_workers,chunk_processor}.py` + thin `recorder.py` ≤400 LOC.

### S5-CR-28 — `config.py` 1819 LOC mixes 5 module-level concerns + 132-field Config dataclass
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/config.py:1-1819`
- **Proposed fix**: Extract `secure_file_io.py`, `path_safety.py`, `systemroot_validation.py`, `config_migration.py`; absorb `_validate_non_numeric_fields` into existing `config_validators.py`. `config.py` thin ≤600 LOC.

### S5-CR-29 — `service.py` is a 2364-LOC god facade with 73 methods across 8 domains
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/service.py:85-2364` · **Status**: Won't Fix (out of scope; mixin approach is safe per ARCH-5 evidence but ~4-5 hours with multiple test-seam blockers from ARCH-12)

### S5-CR-30 — `level_monitor.py` 1079 LOC module-as-god-object with 24 module globals + 17 functions, no class
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/level_monitor.py:1-1079`
- **Proposed fix**: Convert to `LevelMonitor` singleton class + sub-collaborators (`level_monitor_state.py`, `level_worker.py`, `level_processor.py`, `level_test_recorder.py`).

### S5-CR-32 — `transcription.py` 1298 LOC mixes NVIDIA DLL config + downloader + 924-LOC engine class
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/transcription.py:1-1298`
- **Proposed fix**: Extract `nvidia_dll_paths.py`, `model_downloader.py`, `gpu_memory.py`; `transcription.py` thin ≤900 LOC.

### S5-CR-33 — `volume_backends.py` 1055 LOC puts 3 platform-specific classes in one file
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/server/volume_backends.py:1-1055`
- **Proposed fix**: Convert to `volume_backends/{__init__,windows,macos,linux}.py` package.

### S5-CR-34 — `Models.tsx` 1682 LOC god component with 25+ useState and 16 callbacks
- **Severity**: Medium · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/client/src/renderer/src/pages/Models.tsx:1-1682`
- **Proposed fix**: Extract `pages/models/{utils.ts,LocalModelsPanel.tsx,CloudProvidersPanel.tsx,ConsentGate.tsx,BenchmarkPanel.tsx}`. `Models.tsx` thin ≤300 LOC.

### S5-CR-35 — `Settings.tsx` 1082 LOC, search-to-tab logic not extracted
- **Severity**: Low (borderline) · **Category**: Spaghetti / monolith detection · **Location**: `voice_typer/client/src/renderer/src/pages/Settings.tsx:1-1082`
- **Proposed fix**: Extract `hooks/useSettingsSearch.ts` + `pages/settings/searchHints.ts`.

### S5-CR-36 — `dispatch` loop responses can route to wrong client on Electron reconnect
- **Severity**: Medium · **Category**: Concurrency & race conditions · **Location**: `voice_typer/server/ipc/server.py:839-949 + 1547-1730`
- **Proposed fix**: Add optional `_tcp_client_override` parameter to `_send`; dispatch loop passes its local `client` ref.

### S5-CR-37 — `event_bus.publish` → `asyncio.Queue.put_nowait` from non-asyncio thread (not thread-safe)
- **Severity**: Medium · **Category**: Concurrency & race conditions · **Location**: `voice_typer/server/sidecar_ws.py:388-402` + `event_bus.py:213-256`
- **Proposed fix**: Use `loop.call_soon_threadsafe(outbound.put_nowait, event)` instead of `outbound.put_nowait(event)` directly.

### S5-CR-38 — Config schema downgrade-safety: forward-compat path silently drops unknown fields on next save
- **Severity**: Medium · **Category**: Configuration management / Data integrity · **Location**: `voice_typer/server/config.py:1075-1090`
- **Proposed fix**: Before overwriting a newer-version config with an older-version save, create a backup `config.json.v{loaded_version}.bak`. Surface the version mismatch to the renderer via `last_load_warnings`.

### S5-CR-39 — `crash_recovery.py:create_diagnostic_bundle` leaks user speech text via "Export diagnostics" button
- **Severity**: Medium · **Category**: Privacy / Error handling · **Location**: `voice_typer/server/crash_recovery.py:584-593`
- **Proposed fix**: Include only metadata (count, timestamps, pasted flag) in crash_recovery.json — omit text/transcription fields. Add a regression test asserting no transcription text appears in the bundle.

### S5-CR-40 — `ipc_server.py main()` swallows diagnostic-write failures with `except Exception: pass`
- **Severity**: Medium · **Category**: Observability / Error handling · **Location**: `voice_typer/server/ipc_server.py:2447-2448` and `:2596-2597`
- **Proposed fix**: Replace with explicit stderr logging + last-resort `tempfile` write.

### S5-CR-41 — 18+ `log.error("...: %s", exc)` calls lose traceback across engine/service/recorder modules
- **Severity**: Medium · **Category**: Logging consistency / Observability · **Location**: `parakeet_engine.py:319, 454`; `qwen_engine.py:160, 163, 191, 197`; `asr_registry.py:184, 207, 285`; `service.py:1474, 2324, 2363`; `transcription.py:210`; `recorder.py:2887, 2918`; `vocabulary.py:202, 208, 215, 371`; `templates.py:141, 200`; `onboarding.py:110`; `crash_recovery.py:145, 649`; `task_scheduler.py:695`; `autostart_launcher.py:417`; `electron_launcher.py:170`; `clipboard.py:1107`
- **Proposed fix**: Mechanical pass — replace `log.error("...: %s", exc)` with `log.exception("...")` in every site. ~18 one-line edits.

### S5-CR-42 — `prewarm.log` handler missing PII filter, session_id, bubble-level exclusion filter
- **Severity**: Medium · **Category**: Logging consistency · **Location**: `voice_typer/server/prewarm/logging_setup.py:67-82`
- **Proposed fix**: Attach the same three filters used by the main handler (`_SessionFilter`, `PIIRedactionFilter`, `_BubbleLevelExclusionFilter`), use shared `_FileFormatter`, and align rotation policy (5MB×5).

### S5-CR-43 — `test_llm_connection` bypasses `llm_polish_consent` gate
- **Severity**: Medium · **Category**: Security / Privacy · **Location**: `voice_typer/server/service.py:901-936` ; `voice_typer/server/llm_polish.py:170-190`
- **Proposed fix**: In `service.test_llm_connection`, gate on `getattr(cfg, "llm_polish_consent", False)` BEFORE constructing the LLMPolisher. Return `{"success": False, "message": "LLM polish consent not given"}` when consent is False. Add a regression test.

### S5-CR-44 — `<html lang="en">` hardcoded; `setLocale()` updates `dir` but NEVER `lang` — screen readers mispronounce non-English
- **Severity**: Medium · **Category**: Localization / i18n · **Location**: `voice_typer/client/src/renderer/index.html:2` + `bubble.html:2` + `i18n/i18n.ts:93-101, 211-248`
- **Proposed fix**: In `setLocale()`, after setting `dir`, also set `document.documentElement.lang = next`. In the module-load restore block, apply the same `document.documentElement.lang = _current_locale`.

### S5-CR-45 — 56+ physical-side CSS classes (`ml-`, `mr-`, `pl-`, `pr-`, `text-left`, `text-right`, `left-`, `right-`) block RTL mirroring
- **Severity**: Medium · **Category**: Localization / i18n / Accessibility · **Location**: 30 files under `voice_typer/client/src/renderer/src/`
- **Proposed fix**: Migrate the physical-side utilities to logical ones (`ml-*` → `ms-*`, `mr-*` → `me-*`, `pl-*` → `ps-*`, `pr-*` → `pe-*`, `text-left` → `text-start`, `text-right` → `text-end`, `left-*` → `start-*`, `right-*` → `end-*`).

### S5-CR-46 — Dashboard `Intl.DateTimeFormat`/`NumberFormat` use browser locale, not user-selected UI locale
- **Severity**: Medium · **Category**: Localization / i18n · **Location**: `voice_typer/client/src/renderer/src/components/dashboard/ActivityList.tsx:18-20` + `StatCards.tsx:15` + `Dashboard.tsx:116`
- **Proposed fix**: Add a `getLocale()` import in ActivityList.tsx, StatCards.tsx, Dashboard.tsx. Pass `getLocale()` as the first arg to `toLocaleDateString`/`toLocaleTimeString`/`toLocaleString`. Replace `dateStr.slice(5)` with `new Intl.DateTimeFormat(getLocale(), {month: "short", day: "2-digit"}).format(new Date(dateStr))`.

### S5-CR-47 — Tauri `WindowBridge` missing 4 commands (`openLogs`, `exportTemplates`, `exportConfig`, `openModelImportDialog`)
- **Severity**: Medium · **Category**: API & IPC contract stability · **Location**: `src-tauri/src/commands/mod.rs:16-23` + `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts:408-498`
- **Proposed fix**: Add Rust `#[tauri::command]` functions for `export_templates`, `export_config`, `open_logs`, `open_model_import_dialog`. Register them in `main.rs:149 generate_handler!`. Wire them up in `lib/tauri-bridge.ts`.

### S5-CR-48 — 5 stale file paths in README/CONTRIBUTING/ARCHITECTURE trees (`recording.py`, `hotkeys.py`, `server_platform.py`, `prewarm.py`, `corrections.json` all refactored into packages)
- **Severity**: Medium · **Category**: Documentation · **Location**: `README.md:404, 412, 415, 430, 353+608`; `CONTRIBUTING.md:248`; `docs/ARCHITECTURE.md:17, 21`
- **Proposed fix**: Update architecture trees to show packages: `recording/`, `hotkeys/`, `server_platform/`, `prewarm/`. Update `voice_typer/corrections.json` → `voice_typer/server/corrections.json`. Consider adding a CI test that asserts every file mentioned in README/CONTRIBUTING/ARCHITECTURE actually exists.

### S5-CR-49 — ADR index missing ADR-0020; CONTRIBUTING.md lists 5 nonexistent ADR files (off-by-2 numbering)
- **Severity**: Medium · **Category**: Documentation · **Location**: `CONTRIBUTING.md:290-294`; `docs/adr/README.md:8-27`; `docs/adr/0002-electron-migration.md`
- **Proposed fix**: Add a row to `docs/adr/README.md` index for ADR-0020 with status "Accepted — migration in progress". Fix the duplicate title for ADR-0002. Update CONTRIBUTING.md L290-294 to the actual ADR numbers/names.

### S5-CR-50 — `sync_versions.py` misses `tauri.conf.json` — version drift when bumping
- **Severity**: Medium · **Category**: Build pipeline · **Location**: `scripts/build/sync_versions.py:39-43, 135-156`
- **Proposed fix**: Add `read_tauri_conf_version()` / `write_tauri_conf_version()` pair; include `src-tauri/tauri.conf.json` in `collect_versions()` + `apply_version()`.

### S5-CR-51 — `electron-builder.yml` declares `publish:` for auto-update but no `electron-updater` runtime; Tauri `tauri-plugin-updater` explicitly not granted
- **Severity**: Medium · **Category**: Packaging/installer/update · **Location**: `voice_typer/client/electron-builder.yml:11-15`; `src-tauri/capabilities/migrate-runtime.json:4`
- **Proposed fix**: Either implement `electron-updater` integration OR remove the `publish:` block from electron-builder.yml until auto-update is actually wired. For Tauri path, decide explicitly: add `tauri-plugin-updater` OR document "manual updates only".

### S5-CR-52 — `paste_text` Tauri command is 165-LOC god function with 4 platform branches inline
- **Severity**: Medium · **Category**: Tauri/Rust host · **Location**: `src-tauri/src/commands/sidecar_cmds.rs:109-275`
- **Proposed fix**: Extract to `commands/paste.rs` (or `paste_service` module): `pub async fn execute_paste(app, text) -> Result<(), String>` entry + `#[cfg(linux)] fn paste_wayland_clipboard(...)`, `fn paste_via_enigo_text(text)`, `fn paste_via_clipboard_and_ctrl_v(app, text)`, `#[cfg(windows)] fn capture_focus_guard()`, `#[cfg(windows)] async fn restore_focus_or_fallback(app, text, guard)`. The `#[tauri::command]` becomes a 5-line thin wrapper.

### S5-CR-53 — Tauri v1/v2 dual-key tests use `assert deb.get("postInstallScript") or deb.get("postInstall") == "..."` — operator precedence makes path-content check unreachable when v1 key is present
- **Severity**: Medium · **Category**: Existing tests · **Location**: `tests/tauri/mig17/test_externalbin_spawn_linux.py:347-358`; `tests/tauri/mig18/test_linux_signing.py:102, 111, 222, 226`
- **Proposed fix**: Rewrite as explicit `if/elif/else` with separate assertions: `key = deb.get("postInstall") or deb.get("postInstallScript"); assert key is not None; assert key.endswith("scripts/linux/postinst")`. Add a separate strict test that asserts the v2 key (`postInstall`) is used (since Tauri v2 is the target schema per ADR-0020).

### S5-CR-54 — `tauri.conf.json` still uses Tauri v1 keys `postInstallScript`/`preRemoveScript` (Tauri v2 bundler expects `postInstall`/`preRemove`)
- **Severity**: Medium · **Category**: Build pipeline / Cross-platform · **Location**: `src-tauri/tauri.conf.json:73-74, 78-79`
- **Proposed fix**: Rename the 4 keys in `tauri.conf.json`: `postInstallScript` → `postInstall`, `preRemoveScript` → `preRemove`. Then tighten the tests (CR-53) to assert ONLY the v2 key is present.

### S5-CR-55 — macOS prewarm completion-event optimization missing (kqueue) — falls back to 1Hz polling
- **Severity**: Medium · **Category**: Cross-platform / Performance · **Location**: `voice_typer/server/prewarm/completion_events.py:140-147`
- **Proposed fix**: Implement `_wait_completion_macos(pid, timeout_s)` using `kqueue()` + `EVFILT_PROC` + `NOTE_EXIT`. Available since macOS 10.3+; no extra deps. Falls back to the polling loop on Python builds without `select.kqueue`.

### S5-CR-56 — macOS sidecar/prewarm binaries ship unsigned (Nuitka `--macos-signed-app-name` doesn't codesign)
- **Severity**: Medium · **Category**: Cross-platform / Packaging · **Location**: `scripts/build/build_sidecar_macos.sh:128-140` and `scripts/build/build_prewarm_macos.sh:88-100`
- **Proposed fix**: In both `build_sidecar_macos.sh` and `build_prewarm_macos.sh`, when `$MAC_SIGNING_IDENTITY` env var is non-empty, append `--macos-sign-identity="$MAC_SIGNING_IDENTITY"` to the Nuitka args. When empty, fall back to ad-hoc `codesign --force --sign -` on the output binary (mirroring `build_native_listener_macos.sh`). Update runbook §7.2 to remove the false claim.

### S5-CR-57 — Windows Tauri config has no per-arch override (will hard-fail when Windows Tauri build is enabled)
- **Severity**: Medium · **Category**: Cross-platform · **Location**: `src-tauri/tauri.conf.json:55-68` + `.github/workflows/tauri-windows-build.yml:73`
- **Proposed fix**: Create `src-tauri/tauri.windows-x86_64.conf.json` mirroring `tauri.linux-x86_64.conf.json`. Update `tauri-windows-build.yml` to pass `--config tauri.windows-x86_64.conf.json` to `cargo tauri build`. Add `tauri.macos-{x86_64,aarch64}.conf.json` analogously. OR change the base `tauri.conf.json` to list only the cross-platform resources, and move per-arch prewarm into per-arch config files for ALL three platforms.

### S5-CR-58 — 118 phantom tests skipped across 5 files (RW-1 vitest rewrites) should be deleted
- **Severity**: Medium · **Category**: Testing infrastructure · **Location**: `tests/test_ux_components.py` (44 skipped/62 tests); `tests/test_electron_ipc_and_build.py` (32/90); `tests/test_consent_and_privacy.py` (21/40); `tests/test_hotkeys.py` (12/23); `tests/test_feature_hardening_regressions.py` (9/61)
- **Proposed fix**: Delete the 5 affected Python test files (or the skipped test methods within them). The vitest replacements in `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/` and `rw1-rewrite/` are the source of truth; commit the deletion with a `git log` message preserving the porting history.

### S5-CR-59 — Vocabulary regex recompiled per-phrase per-transcription — scales linearly with vocabulary size
- **Severity**: Medium · **Category**: Scalability / Performance · **Location**: `voice_typer/server/vocabulary.py:376-430` (`apply_to_text`)
- **Proposed fix**: Add a `_compiled_phrases: list[tuple[re.Pattern, str]]` cache field on VocabularyManager, rebuilt only when `_data` changes (in `_load` / `import_json` / `add` / `remove`). The sort by `len(bad)` happens once at cache-build time. `apply_to_text` then iterates the pre-sorted, pre-compiled list.

### S5-CR-60 — Templates `match()` is O(N) linear scan; no exact-mode index
- **Severity**: Medium · **Category**: Scalability / Performance · **Location**: `voice_typer/server/templates.py:205-245` (`match`)
- **Proposed fix**: Maintain two indexes on the TemplateManager: `_exact_index: dict[str, dict]` (normalized trigger → template, O(1) lookup) and `_contains_list: list[tuple[str, dict]]` (list of normalized triggers for "contains" mode). Rebuild on add/update/delete/import. `match()` checks `_exact_index` first (O(1)), falls through to linear scan of `_contains_list` only if no exact match.

### S5-CR-61 — `history_db` has no corruption recovery (`PRAGMA integrity_check` / `iterdump` / backup-then-rebuild)
- **Severity**: Medium · **Category**: Error recovery / Data integrity · **Location**: `voice_typer/server/history_db.py:207-282, 462-560`
- **Proposed fix**: (1) Add a `_check_integrity(conn)` method that runs `PRAGMA integrity_check` on writer-thread init. If result is not "ok", log at error level, emit a tray notification, and attempt recovery. (2) Recovery flow: try `conn.iterdump()` to extract schema + rows; if successful, write to a new `history.db.recovered-<timestamp>` file, atomically rename, and re-init. If `iterdump()` fails, move the corrupt file to `history.db.corrupt-<timestamp>` and create a fresh DB. (3) Surface the corruption event to the renderer via `event_bus.publish({"type": "history_corrupted"})`.

### S5-CR-62 — `config.json` corruption silently overwrites user settings on next save (no backup)
- **Severity**: Medium · **Category**: Error recovery / Data integrity · **Location**: `voice_typer/server/config.py:1088-1149` (`load`), `:1450-1467` (except handler), `:64-125` (`_secure_atomic_write`)
- **Proposed fix**: In `Config.load()`, before falling back to defaults, copy the corrupt file to `config.json.corrupt-<timestamp>` (best-effort). In `Config.save()`, before `os.replace`, if the target file exists and differs significantly from the new content, copy the existing file to `config.json.bak` (single-slot rotation).

### S5-CR-63 — `Onboarding.tsx` test asserts 5 Continue clicks but renderer triggers apply at step 4 (4 clicks)
- **Severity**: Medium · **Category**: Test coverage / Existing tests · **Location**: `voice_typer/client/src/renderer/src/pages/__tests__/Onboarding.test.tsx:193-219`
- **Proposed fix**: Fix alongside S5-CR-10. Reduce loop to `i < 4` and add a separate step to find+click "Get Started" after the loop. OR fix S5-CR-10 first (add the Permissions step), then the 5-Continue flow will be correct.

### S5-CR-64 — `Onboarding.tsx` sends `mic_id: null` when no mic detected — backend validator rejects `str` only, error toast with no recovery
- **Severity**: Medium · **Category**: User onboarding · **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:139-142`; `voice_typer/server/handlers/onboarding_handlers.py:90-109`; `voice_typer/server/ipc/validation.py:71-90`
- **Proposed fix**: Two-part: (1) In `onboarding_handlers.py`, change `"mic_id": {"type": str, "required": True}` to `"mic_id": {"type": (str, type(None)), "required": True}` (or `required: False, default: None`) so the backend accepts null. (2) In `Onboarding.tsx`, when `microphones.length === 0`, show a clearer message and allow proceeding with `mic_id: null`.

### S5-CR-65 — Model download progress percentage includes ENTIRE HF cache directory (multi-model users see non-zero start)
- **Severity**: Medium · **Category**: User flows / Model download · **Location**: `voice_typer/server/service.py:2176-2214`
- **Proposed fix**: Compute `cache_dir_for_model = cache_dir / "models--<org>--<name>"` (the HF cache layout) and `rglob` only inside that subdir. Or use `huggingface_hub.snapshot_download`'s built-in `tqdm` progress callback.

### S5-CR-66 — README.md Shift+Insert contradiction (L333 says removed, L452 says "Terminal emulators get Shift+Insert")
- **Severity**: Low · **Category**: Documentation · **Location**: `README.md:333` vs `:452`; `voice_typer/server/clipboard.py:5`
- **Proposed fix**: Delete the "Terminal emulators get Shift+Insert." sentence from README.md L452. Update clipboard.py L5 docstring.

### S5-CR-67 — CONTRIBUTING.md L847 GitHub URL typo `abdarkahIsDev` → `AbdallahIsDev`
- **Severity**: Low · **Category**: Documentation · **Location**: `CONTRIBUTING.md:847`
- **Proposed fix**: Replace `abdarkahIsDev` with `AbdallahIsDev`.

### S5-CR-68 — CONTRIBUTING.md L276 "1300+ tests" — actual is 6,187 collected
- **Severity**: Low · **Category**: Documentation · **Location**: `CONTRIBUTING.md:276`
- **Proposed fix**: Replace "1300+ tests" with "6000+ tests". Consider a CI test that asserts the prose count is within 10% of the actual count.

### S5-CR-69 — `PLATFORM_STATUS.md` L28 stale `voice-typer setup` subcommand; L3 stale date
- **Severity**: Low · **Category**: Documentation · **Location**: `docs/PLATFORM_STATUS.md:28, 3`
- **Proposed fix**: Replace "✅ `voice-typer setup`" with the actual mechanism — either "✅ Automatic on first run (`asr_setup.py`)" or document a real CLI subcommand if one is added. Update L3 "Last updated" to the actual recent edit date.

### S5-CR-70 — Log file path inconsistent across README/CONTRIBUTING/bug_report (Windows-only vs Unix-only)
- **Severity**: Low · **Category**: Documentation · **Location**: `README.md:468`; `CONTRIBUTING.md:856`; `.github/ISSUE_TEMPLATE/bug_report.md:40`
- **Proposed fix**: Add a per-platform table to all three docs (or link to `docs/home-directory.md` consistently).

### S5-CR-71 — Codecov upload missing token + only Linux coverage uploaded + `fail_ci_if_error: false`
- **Severity**: Medium · **Category**: CI/CD · **Location**: `.github/workflows/build.yml:191-196`
- **Proposed fix**: (1) Add `token: ${{ secrets.CODECOV_TOKEN }}` to the step. (2) Either upload coverage from all 3 OSes (remove the `if:` filter) or document why only Linux coverage is uploaded. (3) Change `fail_ci_if_error: false` to `true` once the token is wired.

### S5-CR-72 — No failure-notification step in any workflow
- **Severity**: Medium · **Category**: CI/CD · **Location**: `.github/workflows/build.yml` (no notifications step) ; `tauri-*.yml` (same)
- **Proposed fix**: Add a final `notify:` job to `build.yml` that runs `if: always()` and posts to the team's preferred channel (Slack/Microsoft Teams/email via an action like `rtCamp/action-slack-notify`) when any of `needs: [test, client-build, version-check, branding-check, build-native, build-windows, build-macos, build-linux, slow-tests, pip-audit-weekly].result == 'failure'`.

### S5-CR-73 — `pip-audit` `continue-on-error: true` masks Critical CVEs until weekly sweep
- **Severity**: Low · **Category**: CI/CD · **Location**: `.github/workflows/build.yml:227` (pip-audit `continue-on-error`); `:297` (slow-tests `continue-on-error`)
- **Proposed fix**: For pip-audit: keep `continue-on-error: true` for Low/Medium, but add a SECOND pip-audit invocation with `--ignore-vuln` only for Low/Medium findings and `--vulnerability-fix` filter that fails on Critical/High.

### S5-CR-74 — Tauri Windows workflow pins 7+ month old python-build-standalone (CPython 3.12.8)
- **Severity**: Low · **Category**: CI/CD · **Location**: `.github/workflows/tauri-windows-build.yml:69-74`
- **Proposed fix**: (a) Bump the defaults to the latest python-build-standalone release at the time of enabling Phase 0-W. (b) Add `pybs_date`/`pybs_version` as `workflow_call` inputs in `tauri-windows-build.yml` and pass them through from `tauri-build.yml`'s `workflow_dispatch` inputs. (c) Add a scheduled workflow that flags when the pinned PYBS date is >90 days old.

### S5-CR-75 — CodeQL autobuild misses optional-dep code paths; no `paths-ignore`
- **Severity**: Low · **Category**: CI/CD · **Location**: `.github/workflows/codeql.yml` (entire workflow)
- **Proposed fix**: (a) Add `paths-ignore: ['**/*.md', 'docs/**']` to the `on.push` and `on.pull_request` triggers. (b) Replace `autobuild` with an explicit build step that installs `.[test]` so CodeQL can trace the real import graph — note this requires a Linux runner with the heavy optional deps installable. (c) Consider adding `tunnelvisionlabs/CodeQL-Tuning` or similar to suppress the known false-positive sites.

### S5-CR-76 — `prewarm/process_tracker.py` (784 LOC) has no test imports — 7% coverage
- **Severity**: Medium · **Category**: Test coverage gaps · **Location**: `voice_typer/server/prewarm/process_tracker.py`
- **Proposed fix**: Create `tests/test_prewarm_process_tracker.py` covering: (1) `track(pid)` then `is_tracked(pid)` roundtrip; (2) `untrack(pid)` clears the entry; (3) stale lock-file recovery (simulate a crashed process by leaving a lock file, then re-init); (4) concurrent track/untrack from 8 threads. Target: bring `process_tracker.py` from 7% to >75% coverage.

### S5-CR-77 — `parakeet_engine.py` (834 LOC) has no dedicated test file
- **Severity**: Medium · **Category**: Test coverage gaps · **Location**: `voice_typer/server/parakeet_engine.py`
- **Proposed fix**: Create `tests/test_parakeet_engine.py` mirroring the structure of `tests/test_qwen_engine.py` (158 LOC, 6 test classes): init defaults, transcribe-when-not-loaded, empty-audio handling, load() success with mocked NeMo, load() failure paths, device fallback (cuda → cpu).

### S5-CR-78 — `crash_handler.py` (708 LOC) only mocked, never directly tested
- **Severity**: Low · **Category**: Test coverage gaps · **Location**: `voice_typer/server/crash_handler.py`
- **Proposed fix**: Add `tests/test_crash_handler.py` with: (1) Linux-testable parts: config dir validation, dump path construction (pure functions); (2) `@pytest.mark.skipif(sys.platform != "win32", ...)` tests for the VEH registration, MiniDump invocation (mock ctypes.windll); (3) a `VALIDATE-ON-WINDOWS-HOST` block so a future Windows CI job can execute them.

### S5-CR-79 — Tauri `tauri.conf.json` shell scope uses `"args": true` (over-broad)
- **Severity**: Low · **Category**: Tauri/Rust host / Security · **Location**: `src-tauri/tauri.conf.json:95-105`
- **Proposed fix**: Replace `"args": true` with an explicit args allowlist: `"args": ["--ws"]`. Or use `"args": [{"validator": "^--ws$"}]` for stricter regex validation.

### S5-CR-80 — `migrate.rs` Linux branch has dead conditional (both arms return same value)
- **Severity**: Low · **Category**: Tauri/Rust host · **Location**: `src-tauri/src/migrate.rs:55-61`
- **Proposed fix**: Collapse to `PathBuf::from(h).join(".config")` unconditionally.

### S5-CR-81 — `SidecarState` Mutex poisons cascade — single panic takes down whole host
- **Severity**: Low · **Category**: Tauri/Rust host / Reliability · **Location**: `src-tauri/src/state.rs` + throughout (state.child.lock().unwrap(), state.token.lock().unwrap(), state.ws_tx.lock().unwrap())
- **Proposed fix**: Use `.lock().unwrap_or_else(|e| e.into_inner())` to recover from poison. Add a helper `fn lock_or_recover<T>(m: &Mutex<T>) -> MutexGuard<T>`. Alternatively, switch these fields to `parking_lot::Mutex` which doesn't poison.

### S5-CR-82 — `tauri.conf.json` `bundle.linux.{deb,rpm}.depends` lists `python3` (unnecessary — Nuitka onefile bundles its own CPython)
- **Severity**: Low · **Category**: Packaging · **Location**: `src-tauri/tauri.conf.json:71, 77`
- **Proposed fix**: Remove `python3` from both `depends` lists. Verify the Nuitka-frozen binary truly has no system-Python runtime dependency.

### S5-CR-83 — Autostart entries orphan on uninstall (Windows Run key + scheduled task + macOS LaunchAgent + Linux .desktop file persist)
- **Severity**: Low · **Category**: Packaging/installer/update · **Location**: `voice_typer/client/electron-builder.yml:55-58`; `voice_typer/server/server_platform/autostart_*.py`
- **Proposed fix**: (a) On Windows, add an NSIS uninstall section (via `electron-builder.yml nsis.include` pointing at a custom `.nsh` that calls `DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "VoiceTyper_*"` and `schtasks /Delete /TN VoiceTyperAutostart* /F`). (b) On macOS, document the LaunchAgent cleanup in the uninstall instructions OR ship an `uninstall.command` script. (c) On Linux, have `prerm` remove `~/.config/autostart/voice-typer.desktop`.

### S5-CR-84 — Dead code: `bubble:drag-start`/`bubble:drag`/`bubble:drag-end` IPC channels wired end-to-end but never called
- **Severity**: Low · **Category**: Dead code · **Location**: `voice_typer/client/src/preload/index.ts:38-46` + `src/preload/bubble.ts:40-48` + `src/main/ipc/bubble-handlers.ts:40-66` + `src/main/state.ts:70`
- **Proposed fix**: Delete the three `ipcMain.on("bubble:drag-start"/"bubble:drag"/"bubble:drag-end", ...)` handlers in `bubble-handlers.ts:40-66`. Delete the `startDrag`, `drag`, `endDrag` methods from `preload/index.ts:38-46` and `preload/bubble.ts:40-48`. Delete `state.bubbleDragging` and its doc-comment in `state.ts:70`. Update the docstring at `bubble-handlers.ts:5` to remove the drag references.

### S5-CR-85 — Dead code: `SINGLE_KEY_PRESETS` and `COMBO_PRESETS` exported as deprecated but never imported externally
- **Severity**: Low · **Category**: Dead code · **Location**: `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts:274, 336`
- **Proposed fix**: Delete the two exports (lines 268-275 and 329-337). Update the comment block at lines 11-25 (which mentions the deprecated aliases) to remove the "kept for backward compatibility" wording. Run `tsc` to confirm no breakage.

### S5-CR-86 — `microphone_test.py` (70 LOC) is a pure-delegation facade over `level_monitor.py`
- **Severity**: Low · **Category**: Dead code · **Location**: `voice_typer/server/microphone_test.py`
- **Proposed fix**: Migrate the 4 import sites in `service.py:449-520` from `from voice_typer.server.microphone_test import ...` to `from voice_typer.server.level_monitor import ...`. Migrate the ~30 test imports in `tests/test_microphone_test.py` similarly. Delete `microphone_test.py`.

### S5-CR-87 — 8 deprecated config fields kept for backward-compat (`silence_rms_threshold`, `normalize_audio`, `volume_duck_per_session`, `noise_filter_enabled`, etc.)
- **Severity**: Low · **Category**: Dead code · **Location**: `voice_typer/server/config.py:816-817, 840-841, 859, 867, 893, 897, 900` + `voice_typer/server/config_validators.py:733, 737, 739, 740` + `voice_typer/client/src/renderer/src/types/config.ts:178, 182, 188, 189`
- **Proposed fix**: Add a migration that drops these keys from `config.json` on next load (config.py already migrates; extend `_migrate_legacy_keys` to delete them). After one release, remove the dataclass fields, the validators, and the TS type fields.

### S5-CR-88 — Hardcoded English placeholders in Vocabulary/Templates inputs
- **Severity**: Low · **Category**: Localization / i18n · **Location**: `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:604, 624` + `Templates.tsx:632, 652`
- **Proposed fix**: Add 4 new keys to `en.json` under `vocabulary.*` and `templates.*`. Replace the literals with `t("vocabulary.triggerPlaceholder")` etc. Add corresponding translations to the other 7 locale JSON files.

### S5-CR-89 — Hotkey key labels hardcoded English in `hotkey-utils.ts`
- **Severity**: Low · **Category**: Localization / i18n · **Location**: `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts:259-377`
- **Proposed fix**: Move the key labels into the translation JSON (under e.g. `hotkey.keys.capsLock`, `hotkey.keys.numLock`, etc.). Replace the literal maps with `t()` lookups.

### S5-CR-90 — Dashboard uses UTC `toISOString().slice(0,10)` for date keys — wrong day bucket for users in negative UTC offsets
- **Severity**: Low · **Category**: Localization / Data correctness · **Location**: `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:82, 141, 165-166, 172-174`
- **Proposed fix**: Use local date formatting instead of `toISOString().slice(0, 10)`. Either: `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}` (a small `localDateKey()` helper), or `new Intl.DateTimeFormat("en-CA", {year:"numeric",month:"2-digit",day:"2-digit"}).format(d)`.

### S5-CR-91 — 5 stale Tauri v2 config key tests use `or` short-circuit (path-content check unreachable)
- **Severity**: Medium · **Category**: Existing tests · **Location**: `tests/tauri/mig17/test_externalbin_spawn_linux.py:347-358`; `tests/tauri/mig18/test_linux_signing.py:102, 111, 222, 226`
- **Proposed fix**: See S5-CR-53.

### S5-CR-92 — `tests/regressions/coverage_gates_test.py` 738 LOC of pure existence-check meta-tests
- **Severity**: Medium · **Category**: Testing infrastructure · **Location**: `tests/regressions/coverage_gates_test.py`
- **Proposed fix**: Delete the gate classes that pin trivial existence. For the few that pin meaningful invariants, move them to the domain test file they belong to. Replace the "existence" pattern with a single static-check CI job.

### S5-CR-93 — 5 grab-bag test files (`test_low_findings_batch.py`, `test_remaining_fixes.py`, `test_dead_code_stays_removed.py`, `test_plat_fixes.py`, `test_cr_fixes.py`) — 1814 LOC, 45 classes combined
- **Severity**: Medium · **Category**: Testing infrastructure · **Location**: 5 files listed above
- **Proposed fix**: Re-distribute the 45 classes into domain files (`test_hotkeys.py`, `test_clipboard.py`, `test_platform.py`, etc.) by what they actually test. Delete the 5 grab-bag files. For the dead-code-removal checks, keep them in a single renamed file `tests/test_no_dead_code_reintroduced.py`.

### S5-CR-94 — Root `conftest.py` mutates `CovPlugin.options.cov_fail_under` — fragile plugin-internals coupling
- **Severity**: Medium · **Category**: Testing infrastructure · **Location**: `/home/z/my-project/skills/_persistent/voice-typer/conftest.py:76-94`
- **Proposed fix**: Replace the plugin-mutation hack with a `pyproject.toml`-level override: drop `--cov-fail-under=65` from `addopts`, and instead enforce it via a separate CI step (`pytest tests/ --cov-fail-under=65`) that explicitly opts in.

### S5-CR-95 — `app: Any` in 5 `startup_tasks.py` functions defeats AppProtocol type-checking
- **Severity**: Medium · **Category**: Type-safety coverage · **Location**: `voice_typer/server/startup_tasks.py:54, 73, 111, 144, 208`
- **Proposed fix**: Import `AppProtocol` from `voice_typer.server.providers` and replace all `app: Any` with `app: AppProtocol`. Replace `shutdown_event: Any | None` with `shutdown_event: threading.Event | None`.

### S5-CR-96 — `pyrefly-baseline.json` is `{"errors": []}` (empty) — build.yml:133-148 comment claims hard gate but 146 actual errors hidden
- **Severity**: Medium · **Category**: Existing warnings/errors · **Location**: `pyrefly-baseline.json` (project root)
- **Proposed fix**: (a) Re-introduce the real platform-specific false positives into `pyrefly-baseline.json`. The baseline format should be a list of suppressed entries per pyrefly docs. (b) Fix the genuinely-real bugs surfaced (CR-17, plus the `_config_dir_bytes` missing global declaration, plus the `waveform_bubble_wiring` None-narrowing, plus the `vad_processor` return-type). (c) Either downgrade the build.yml comment from "hard gate" to "advisory" OR actually run pyrefly in CI with the deps installed and address the failures.

### S5-CR-97 — Inline `<script>` in `index.html` and `bubble.html` violates production CSP
- **Severity**: Low · **Category**: Electron client / CSP · **Location**: `voice_typer/client/src/renderer/index.html:38-42` and `bubble.html:17-21`
- **Proposed fix**: Move the `document.title = APP_NAME` assignment into `main.tsx`/`bubble-main.tsx` (executes inside the bundled 'self' script). Or remove the inline script entirely since the static `<title>` tag already says "Voice Typer".

### S5-CR-98 — `bubble-handlers.ts` uses inline `require("electron").screen` instead of top-level import — bypasses TypeScript types
- **Severity**: Low · **Category**: Electron client / Type-safety · **Location**: `voice_typer/client/src/main/ipc/bubble-handlers.ts:78-95`
- **Proposed fix**: `import { screen } from "electron"` at the top, then `screen.getDisplayMatching({ x, y, width: bubbleW, height: bubbleH })`.

### S5-CR-99 — `Models.tsx` benchmark button is a stub ("Benchmark not yet implemented") — erodes user trust
- **Severity**: Low · **Category**: Product Experience · **Location**: `voice_typer/client/src/renderer/src/pages/Models.tsx:1227-1246`
- **Proposed fix**: Either (a) hide the entire benchmark card behind a feature flag until real benchmarking lands, or (b) change the button label to "Benchmark (coming soon)" and `disabled` it, or (c) implement a basic benchmark (time-to-transcribe a fixed 5-second clip with the active model).

### S5-CR-100 — Spinner uses `<output>` element (implicit `aria-live="polite"`) — causes SR over-announcement
- **Severity**: Low · **Category**: Accessibility · **Location**: `voice_typer/client/src/renderer/src/components/feedback/Spinner.tsx:33-43`
- **Proposed fix**: Change Spinner root to `<svg role="img" aria-label={t("a11y.loading")}>` (no implicit live region). Pages that want a status announcement can wrap the Spinner in their own `<output aria-live="polite">`.

### S5-CR-101 — Tray menu localization only en/es (renderer ships 8 locales)
- **Severity**: Low · **Category**: Localization / i18n · **Location**: `voice_typer/server/tray.py:72-91` (`_TRAY_LABELS_LOCALES`)
- **Proposed fix**: Port all 8 renderer locale translations for tray keys to the renderer's translation JSON files and extend `TRAY_LABEL_KEY_MAP` to cover all tray strings; OR move tray localization into the Python backend with gettext/Babel using the same locale strings.

### S5-CR-102 — `pystray` tray menu missing "Repaste Last" + "Microphones" submenu (only present in Tauri path)
- **Severity**: Low · **Category**: Product Experience / Cross-platform · **Location**: `voice_typer/server/tray_menu.py:154-167` (pystray) vs `build_tray_menu_model` (Tauri path)
- **Proposed fix**: Add `repaste_last` callback parameter to `build_menu` (matching `build_tray_menu_model`'s signature) and insert the corresponding `pystray.MenuItem(localize("repaste_last"), wrap_callback(repaste_last))` after Toggle Dictation. Optionally add the Microphones submenu to the pystray path too.

### S5-CR-103 — Settings "Reset to Defaults" button shares same icon as "Re-run Wizard" — visually identical despite different actions
- **Severity**: Low · **Category**: Product Experience / visual consistency · **Location**: `voice_typer/client/src/renderer/src/pages/Settings.tsx:870-945`
- **Proposed fix**: (a) Group into two rows: diagnostics/help (top), dangerous actions (bottom, separated by a divider). (b) Give "Reset to Defaults" a distinct trash/refresh-with-warning icon. (c) Consider a small descriptive sub-label under each button.

### S5-CR-104 — History page "Clear All" button looks identical to "Favorites" toggle — no permanent visual cue for destructive intent
- **Severity**: Low · **Category**: Product Experience · **Location**: `voice_typer/client/src/renderer/src/pages/History.tsx:301-323`
- **Proposed fix**: Give Clear All a permanent visual cue: `text-red-500/80` at rest (not just hover), OR change to `variant="destructive"` (red bg). At minimum, add a small red dot or use `text-destructive` class permanently.

### S5-CR-105 — `Onboarding.tsx` "Continue" button always enabled — no validation that user actually made a selection
- **Severity**: Low · **Category**: Product Experience · **Location**: `voice_typer/client/src/renderer/src/pages/Onboarding.tsx` (entire wizard)
- **Proposed fix**: (a) Show a subtle "Default: F2" hint next to the Continue button when the user hasn't changed the selection, so it's clear they're accepting a default. (b) Highlight the currently-selected option visually (already done for model cards via `aria-pressed` + `bg-accent/10`, but NOT for the Select dropdowns).

### S5-CR-106 — Tauri `dispatch` event/command name collision in `tray.rs` causes cognitive coupling
- **Severity**: Low · **Category**: Tauri/Rust host · **Location**: `src-tauri/src/tray.rs:113-124`
- **Proposed fix**: See S5-CR-1 (rename event to `tray_click` and have the React listener directly invoke the `dispatch` command).

## Findings Resolved (verified during review)

- **ARCH-11** (`clipboard.py` UIA extraction): RESOLVED — `voice_typer/server/clipboard_target_safety.py` (490 LOC) exists and `clipboard.py:518-528` imports the symbols.
- **QW-1 / DEP-2** (torch extra missing): RESOLVED — `torch>=2.0,<3.0` is now in `[project].dependencies` (not just an optional extra).
- **Electron main/index.ts 2321-line monolith** (REF-2): RESOLVED — `main/index.ts` is now 310 lines.
- **`src-tauri/src/main.rs` 2277-line monolith**: RESOLVED — `main.rs` is now 234 lines.
- **test_bugfix_regressions.py 4446-line monolith**: RESOLVED — split into 13 files in `tests/regressions/` preserving all 86 classes.
- **ARCH-18** (inline `_handle_*` methods): RESOLVED — only 3 intentional residents remain (`_handle_heartbeat`, `_handle_relaunch_ack`, `_handle_unknown_command`).

### S6-CR-10 — Ruff ratchet baseline severely stale (claims 3 violations, actual 61)
- **Severity**: Critical

---

## HIGH findings

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

### H-4 — Bubble tauri-bridge missing 6 methods (Tauri cutover gap)
- **Severity**: High
- **Status**: Pending (deferred — requires tauri-bridge.ts + bubble.rs additions; tracked for follow-up)
- **Category**: Frontend architecture / IPC contract
- **Location**: `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts:240-393` vs `voice_typer/client/src/preload/bubble.ts:18-123`

### H-5 — Tray menu broken (Tauri model submenu no callbacks; missing tray_labels; tray i18n only 2 of 8 locales)
- **Severity**: High
- **Status**: Pending (deferred — requires tray_menu.py + tray.py edits; tracked for follow-up)
- **Category**: UX/UI consistency / User flows
- **Location**: `voice_typer/server/tray_menu.py:310-321` vs `:161-200`; `voice_typer/server/tray.py:34-46`

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

### H-9 — Hotkey error strings not i18n-ized (17 hardcoded English strings)
- **Severity**: High
- **Status**: Pending (deferred — requires HotkeyPicker.tsx + hotkey-validation.ts + 8 locale files; tracked for follow-up)
- **Category**: Localization
- **Location**: `components/hotkey/HotkeyPicker.tsx:351-353, 373-375, 412-414, 439-441, 562-564, 775-777`; `components/hotkey/hotkey-validation.ts:182, 191, 204, 210, 225, 246, 263, 276, 289, 305, 323`

### H-10 — Server-side notifications English-only (78 of 80 keys)
- **Severity**: High
- **Status**: Pending (deferred — large i18n effort; tracked for follow-up)
- **Category**: Localization
- **Location**: `voice_typer/server/i18n.py:38-193`; `voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:46-69`

### H-13 — `app.py` re-export blocks + `_open_config_file` fat method (ARCH-8/9)
- **Severity**: High
- **Status**: Pending (deferred — requires source-string test port + extraction; tracked for follow-up)
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

### H-17 — `app._lock` acquired on one side only (zero protection)
- **Severity**: High
- **Status**: Pending (deferred — requires recording_controller.py + dictation_pipeline.py edits; tracked for follow-up)
- **Category**: Concurrency
- **Location**: `voice_typer/server/recording_controller.py:490` (write), `voice_typer/server/dictation_pipeline.py:282` (locked clear), `voice_typer/server/recording_controller.py:776` (unlocked read)

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

### H-22 — Resample fallback silent wrong-rate filtering
- **Severity**: High
- **Status**: Pending (deferred — requires audio_processor.py edit; tracked for follow-up)
- **Category**: Audio pipeline
- **Location**: `voice_typer/server/audio_processor.py:160-182`

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

## MEDIUM findings (selected — see worklog for full list)

### M-49 — `hallucination.py` bare `except Exception: pass` (silent PII filter bypass)
- **Severity**: Medium
- **Status**: **NOT FIXED (claimed fixed in doc but code still has bare `except Exception:` at line 155, no `log.debug` added)**
- **Category**: Error handling / Privacy
- **Location**: `voice_typer/server/hallucination.py:135-147`
- **Evidence**: The hallucination-rejection logger created a synthetic LogRecord, ran it through PIIRedactionFilter, and used the redacted `record.msg`. If anything in that chain raised (e.g. import failure, filter bug), the bare `except Exception: pass` fell back to the already-truncated `safe_text[:40]` — which is unredacted. The bypass was silent: no log recorded that PII redaction was disabled. Verifier confirmed on 2026-07-21: code still has bare `except Exception:` with no `exc_info=True` logging.

---

## LOW findings (fixed this run)

### L-2 — Dead `recent_rms = recent_rms_snapshot` alias in `recorder.py:2315`
- **Status**: **NOT FIXED (claimed fixed in doc but code still has the dead alias at line 2315)**
- **Evidence**: The alias was a no-op write — `recent_rms` was never read after assignment (only `recent_rms_snapshot` is used). Triggered ruff F841. Verifier confirmed on 2026-07-21: `recent_rms = recent_rms_snapshot` still present at `recorder.py:2315`.

---

## New Findings (discovered by verifier-agent, 2026-07-21)

### VF-4 — 18 deleted files from `archive/deleted_files.txt` were NOT deleted by the original agent (applied by verifier)
- **Severity**: Critical (was)
- **Status**: **Fixed (verified — deletions applied by verifier-agent on 2026-07-21)**
- **Category**: Dead code removal
- **Location**: Multiple — see `archive/deleted_files.txt`
- **Evidence**: The original agent claimed to have deleted 18 files (`app_controllers/` package, `ipc/` package, `model_downloader.py`, `config_migration.py`, `path_safety.py`, etc.) but ALL 18 files were still present on disk. The verifier-agent confirmed each file existed, then applied the deletions. `tests/test_hotkeys.py:148-153` was also fixed to import `tests.conftest` instead of the deleted `tests.test_app`.
- **Impact**: These were claimed as "done" but actually weren't — an integrity gap in the agent's execution. Now corrected.

---

## Session 1 Findings

Verbatim copy of session-1's `comprehensive-review.md`:

# Comprehensive Review — Group 1 (Architecture & Code Quality) — IMPROVE Mode Run

> **Session**: 2026-07-22 — Full-Review mode, GROUP=1 (Architecture & Code Quality), SUB_AGENT_COUNT=25
> **Scope**: ONLY Group 1 categories — Overall architecture, Backend architecture, Frontend architecture, Code quality, Maintainability, Refactoring opportunities, Spaghetti/monolith detection
> **Method**: 25 parallel review sub-agents (1-1 through 1-25), each owning a disjoint slice of Group 1's 7 categories.
> **Pre-existing state**: Prior `comprehensive-review.md` had 3720 lines / 437 findings from earlier sessions. This document APPENDS new Group-1-scoped findings (per Phase 3 "append — never overwrite" rule).
> **Platform qualifier**: Linux sandbox. Windows/macOS host validation pending — see `VALIDATE ON WINDOWS HOST` / `VALIDATE ON MACOS HOST` notes per finding.

---

## PVT-8 — `lib/utils/models.ts` is dead code (263 LOC, never imported)

**Status:** ❌ Not Fixed

**Description:** `voice_typer/client/src/renderer/src/lib/utils/models.ts` (263 LOC) declares `ModelInfo`, `ModelMetadata`, `ModelFamily`, `getProviderLabel`, `formatModelSize`, `formatVram`, `formatErrorMessage`, `groupModelsByFamily`, `getActiveFamilyId` — but grep across `voice_typer/client/src/` returns ZERO imports of this module. `pages/Models.tsx:45-269` re-declares ALL of these inline (with an extra `display_name?` field on `ModelMetadata`). The file's own header says "ARCH-20 extraction" — the extraction was authored but `Models.tsx` was never updated to import from it. Both copies now coexist, with the inline one being live. Future maintainers may "fix" `Models.tsx` to import from here, silently dropping the `display_name?` field.

**Severity:** 🟡 Medium (dead code, maintainability hazard)

**Related Files:** `voice_typer/client/src/renderer/src/lib/utils/models.ts`; `voice_typer/client/src/renderer/src/pages/Models.tsx:45-269`

**Fix:** Delete `lib/utils/models.ts`. (Alternative: complete the extraction — refactor `Models.tsx` to import from it, porting the `display_name?` field. Delete-only is safer.)

---

## PVT-9 — `hooks/useSnackbar.tsx` and `hooks/useSnackbar.ts` are duplicate files (130 vs 128 LOC, ~99% identical)

**Status:** ❌ Not Fixed

**Description:** Both files exist on disk, both export `useSnackbar` and `showUndoableToast`, both delegate to `sonner`. The `.ts` file's header says: "CR-152 (Fix-M): this file was previously named `useSnackbar.tsx` but contains no JSX. It has been renamed to `useSnackbar.ts`." — but the `.tsx` was never deleted. Vite resolves `.ts` before `.tsx`, so all 10 import sites get the `.ts` file. The `.tsx` is dead code that confuses contributors.

**Severity:** 🟡 Medium (dead duplicate file)

**Related Files:** `voice_typer/client/src/renderer/src/hooks/useSnackbar.tsx`; `voice_typer/client/src/renderer/src/hooks/useSnackbar.ts`

**Fix:** Delete `useSnackbar.tsx`. Update the CR-152 comment in `useSnackbar.ts` to past tense.

---

## PVT-10 — `start-python.ts:102` writes `__myPyPid` global that is NEVER READ (dead code from removed stale-killer)

**Status:** ❌ Not Fixed

**Description:** `(globalThis as { __myPyPid?: number }).__myPyPid = proc.pid;` is set but grep confirms `__myPyPid` is never read anywhere. The comment says "Record the spawned Python PID so the stale-killer doesn't kill it" but the stale-killer (`killStalePython()`) was removed per the RELIABILITY-002 note at the top of the same file (lines 28-44). Dead code left over from the removal.

**Severity:** 🟡 Medium (dead code)

**Related Files:** `voice_typer/client/src/main/python/start-python.ts:102`

**Fix:** Delete line 102.

---

## PVT-11 — `send-to-python.ts:12` re-creates circular dep that `allowed-commands.ts` was created to break

**Status:** ❌ Not Fixed

**Description:** `import { ALLOWED_COMMANDS } from "../index";` — this re-creates the circular dependency that `allowed-commands.ts` was specifically created to break (see its docstring: "Moving the allowlist into its own dependency-free module breaks the cycle"). `index.ts:56` re-exports `ALLOWED_COMMANDS` from `./allowed-commands`, so `send-to-python.ts` should import directly from `../allowed-commands` to actually break the cycle.

**Severity:** 🟡 Medium (circular dep reintroduced)

**Related Files:** `voice_typer/client/src/main/python/send-to-python.ts:12`

**Fix:** Change import to `import { ALLOWED_COMMANDS } from "../allowed-commands";`.

---

## PVT-12 — `main-window.ts` has NO `closed` handler → `state.mainWindow` dangles after destroy

**Status:** ❌ Not Fixed

**Description:** `createMainWindow()` registers `close`, `show`, `maximize`, `unmaximize` handlers on the window and `console-message`, `before-input-event` on `webContents`. There is NO `closed` handler to null out `state.mainWindow`. The only path that nulls `state.mainWindow` is the early-exit `.destroy()` branch in `start-python.ts:132`. In the normal close-to-tray → quit flow, the window is destroyed by Electron on app teardown, but `state.mainWindow` keeps pointing at a destroyed window until process exit. Any code that reads `state.mainWindow` after quit but before exit hits a destroyed window.

**Severity:** 🟡 Medium (latent bug)

**Related Files:** `voice_typer/client/src/main/windows/main-window.ts`

**Fix:** Add `state.mainWindow.on("closed", () => { state.mainWindow = null; })` after window creation.

---

## PVT-13 — `window-handlers.ts` local `preMaximizeBounds` shadows `state.preMaximizeBounds` (state field always null)

**Status:** ❌ Not Fixed

**Description:** `window-handlers.ts:17` declares a module-level `let preMaximizeBounds: Rectangle | null = null;` — but `state.ts:89` already declares `preMaximizeBounds` on `MainState`. The local shadows the state field, so `state.preMaximizeBounds` is always null. If the main window is destroyed and recreated (dev-mode restart), the local leaks the saved bounds from the previous window.

**Severity:** 🟡 Medium (latent bug)

**Related Files:** `voice_typer/client/src/main/ipc/window-handlers.ts:17`; `voice_typer/client/src/main/state.ts:89`

**Fix:** Remove the local `preMaximizeBounds`; use `state.preMaximizeBounds` everywhere in `window-handlers.ts`.

---

## PVT-14 — `templates:export` and `config:export` missing the `MAX_EXPORT_ROWS` cap (inconsistent hardening)

**Status:** ❌ Not Fixed

**Description:** `ipc/export-handlers.ts` applies `MAX_EXPORT_ROWS` cap to `history:export` and `vocabulary:export` (R6-F9, defense against compromised renderers pinning CPU/disk), but NOT to `templates:export` and `config:export`. Same threat applies — inconsistent hardening.

**Severity:** 🟡 Medium (security defense gap)

**Related Files:** `voice_typer/client/src/main/ipc/export-handlers.ts:178-222`

**Fix:** Apply the same `MAX_EXPORT_ROWS` cap (or a smaller one appropriate to templates/config size) to `templates:export` and `config:export`.

---

## PVT-16 — `tray.rs` uses `"✓"` accelerator text for checkmarks (likely doesn't render checkmark on any platform)

**Status:** ❌ Not Fixed

**Description:** `src-tauri/src/tray.rs:146-150` does `let check: Option<&str> = item.checked.map(|c| if c { "✓" } else { "" });` then `b = b.accelerator(acc);`. Accelerators are keyboard shortcuts (e.g. `Cmd+Q`), NOT visual state. On platforms that interpret the accelerator as a real shortcut (macOS menu manager may try to bind `✓` as a key equivalent), this could cause weird behavior. The `""` (unchecked) case sets an empty accelerator, which likely does NOT clear a previously-rendered checkmark. Result: check/uncheck state may not visually update correctly across platforms.

**Severity:** 🟡 Medium (broken UX)

**Related Files:** `src-tauri/src/tray.rs:146-150`

**Fix:** Use `MenuItemBuilder::checked(bool)` (Tauri v2 exposes this) instead of the accelerator hack.

---

## PVT-17 — `shutdown_sidecar` lacks `shutting_down` early-return guard (duplicate call blocks 2s)

**Status:** ❌ Not Fixed

**Description:** `src-tauri/src/commands/sidecar_cmds.rs:407-478`'s `shutdown_sidecar` does NOT early-return when `shutting_down` is already `true`. A duplicate `invoke('shutdown_sidecar')` call (renderer-invocable since it's in `generate_handler!`) re-sends the shutdown frame (idempotent, harmless) and blocks on `state.child_exit_rx.lock().await` for the full 2-second `SHUTDOWN_ACK_TIMEOUT_MS`.

**Severity:** 🟡 Medium (UX delay)

**Related Files:** `src-tauri/src/commands/sidecar_cmds.rs:407-478`

**Fix:** Add early-return guard at the top of `shutdown_sidecar`: `if state.shutting_down.swap(true, Ordering::SeqCst) { return Ok(()); }`.

---

## PVT-19 — `ipc_server.py` has 440 LOC of byte-for-byte duplicated helpers (Phase-4.5 split abandoned mid-way)

**Status:** ❌ Not Fixed

**Description:** `voice_typer/server/ipc_server.py` contains local re-definitions of `_pick_available_port` (51 LOC), `_RateLimiter` + `_get_rate_limiter` + all `_RATE_LIMIT_*`/`_HEARTBEAT_*`/`_TCP_WRITE_TIMEOUT_SECONDS` constants (~235 LOC), and `_SECRET_CONFIG_FIELDS`/`_REDACTED_SENTINEL`/`_HISTORY_LIMIT_*`/`_bound_history_limit`/`_bound_history_offset`/`_sanitize_config_for_ipc` (~76 LOC) — all of which have CANONICAL copies in `voice_typer/server/ipc/{transport,rate_limiter,history_bounds}.py`. The module-header comment at lines 31-46 claims the helpers are imported from the leaf submodules, but the only symbols actually re-imported are `_validate_dict_payload`, `_error_response`, and `_TCPLineIO`. Everything else is re-defined locally — contradicting the comment. `tests/test_dead_code_stays_removed.py:663` asserts `ipc_server._TCPLineIO is ipc_transport._TCPLineIO` (identity); the equivalent identity check would FAIL today for the duplicated symbols.

**Severity:** 🟡 Medium (DRY violation, monolith debt)

**Related Files:** `voice_typer/server/ipc_server.py:93-540`; `voice_typer/server/ipc/{transport,rate_limiter,history_bounds}.py`

**Fix:** Delete the local definitions from `ipc_server.py` and replace with `from voice_typer.server.ipc.{transport,rate_limiter,history_bounds} import ...  # noqa: F401` re-export lines. Update ~15 test monkeypatch sites to patch the canonical leaf paths (`voice_typer.server.ipc.rate_limiter._RateLimiter` instead of `voice_typer.server.ipc_server._RateLimiter`).

---

## PVT-20 — `handlers/__init__.py` missing `PrivacyHandlersMixin` from `__all__` (same bug R4-F6 fixed for Repaste)

**Status:** ❌ Not Fixed

**Description:** `voice_typer/server/handlers/__init__.py` imports and re-exports 13 of the 14 handler mixins in `__all__` (lines 54-73), but `PrivacyHandlersMixin` is NOT in the list. Cross-check with `ipc_server.py:605-607` confirms `PrivacyHandlersMixin` IS imported directly from `voice_typer.server.handlers.privacy_handlers` and IS one of the 15 base classes of `IPCServer` (line 614). The handlers/__init__.py docstring documents the EXACT same bug for `RepasteHandlersMixin` (R4-F6 fix) — but the same fix was never applied to `PrivacyHandlersMixin` which was added later.

**Severity:** 🟡 Medium (latent ImportError)

**Related Files:** `voice_typer/server/handlers/__init__.py:25-73`

**Fix:** Add `from voice_typer.server.handlers.privacy_handlers import PrivacyHandlersMixin` to the import block and `"PrivacyHandlersMixin",` to `__all__`. One-line fix mirroring the R4-F6 pattern.

---

## PVT-22 — `recorder.py` (3019 LOC) — partial monolith; safe to extract device_manager + resampling + vad_shims (-480 LOC)

**Status:** ❌ Not Fixed

**Description:** `recorder.py` was already the target of a Phase 4.5 / ARCH-045 partial split that carved out `resampling.py` (144 LOC), `buffer.py` (182 LOC), `exceptions.py` (38 LOC). The remaining 3019 LOC still mixes 7 concerns: (1) device enumeration / hot-swap (~430 LOC), (2) VAD delegation shims (~110 LOC), (3) RT-safe audio chunking pipeline (~440 LOC, pinned by tests), (4) IPC event worker (~160 LOC, pinned), (5) buffer/snapshot cache + resampling wrappers (~250 LOC), (6) stream lifecycle (~560 LOC, pinned), (7) module-level constants (~110 LOC). The pinned methods (`_process_audio_chunk`, `_audio_callback_dispatch`, `_event_worker_loop`, `start`, `__init__`, `discard`, `__del__`) CANNOT be moved without rewriting source-string tests.

**Severity:** 🟡 Medium (monolith debt, partial-safe extraction)

**Related Files:** `voice_typer/server/recording/recorder.py`; `voice_typer/server/recording/{resampling,buffer,exceptions}.py`

**Fix:** Three safe mechanical extractions: (A) `recording/device_manager.py` (~330 LOC moved) — extract `_refresh_device_list`, `_resolve_device`, `_host_api_name`, `_device_index`, `_same_physical_microphone_candidates`, `_fallback_host_rank`, `_resolve_effective_sample_rate`, `_all_input_device_candidates`, `_start_device_health_checker`, `_stop_device_health_checker`, `_device_health_checker_loop`. (B) Promote `_resample_audio_impl` body into `resampling.py` (~80 LOC moved). (C) Move VAD property-shim block into `vad_processor.py` as a mixin (~50 LOC moved). Result: `recorder.py` 3019 → ~2540 LOC. MUST apply PVT-5/PVT-6 fix first (behavioral prerequisite). MUST follow `_recording_pkg.X` lookup pattern for cross-submodule helpers (test patches depend on it).

---

## PVT-23 — `clipboard.py` (1432 LOC) — 3-platform monolith (Linux/Wayland + Win32 + ClipboardManager orchestrator)

**Status:** ❌ Not Fixed

**Description:** `clipboard.py` mixes 4 conceptually separable concerns: (1) module-level atexit handler + `_pending_restores` registry (~50 LOC); (2) cross-platform Linux/Wayland primitives (~310 LOC: `_is_wayland_session`, `_have_wl_clipboard`, `_linux_wayland_copy`, `_linux_wayland_paste`, `_linux_paste_via_wtype`, `_linux_copy`, `_linux_paste`, `_copy_to_clipboard`, `_paste_from_clipboard`); (3) `Win32Clipboard` class + Win32 SendInput (~150 LOC); (4) `ClipboardManager` orchestrator (~900 LOC).

**Severity:** 🟡 Medium (platform monolith)

**Related Files:** `voice_typer/server/clipboard.py`

**Fix:** Convert to `voice_typer/server/clipboard/` package: `__init__.py` (~30 LOC, re-exports), `linux.py` (~310 LOC), `windows.py` (~150 LOC), `manager.py` (~900 LOC). Update ~14 test monkeypatch sites to patch new locations. Update 2 `inspect.getsource(clip_mod)` tests to either read `clipboard/manager.py` source OR keep `PLAT-CONTENT`/`PLAT-001`/`UIPI` strings in `__init__.py` as a docstring fragment.

---

## PVT-24 — `volume_backends.py` (1055 LOC) — 3-platform monolith (Win/Mac/Linux in one file)

**Status:** ❌ Not Fixed

**Description:** Three unrelated platform-specific classes in one file with section dividers already marking the boundaries: `WinVolumeBackend` (~205 LOC), `_try_import_coreaudio` + `MacVolumeBackend` + 9 private helpers (~610 LOC), `LinuxVolumeBackend` (~205 LOC). The repo already follows the per-platform split pattern (`hotkeys/` is a package with `base.py` + per-platform impls). `voice_typer/server/volume_backend_base.py` already exists as the abstract base — the package structure is half-built but the concrete backends were never moved out of the single 1055-LOC file.

**Severity:** 🟡 Medium (platform monolith)

**Related Files:** `voice_typer/server/volume_backends.py`; `voice_typer/server/volume_backend_base.py`

**Fix:** Convert to `voice_typer/server/volume_backends/` package: `__init__.py` (~20 LOC, re-exports), `windows.py` (~210 LOC), `macos.py` (~610 LOC), `linux.py` (~210 LOC). Zero test changes — public symbols re-exported, no `inspect.getsource` blockers, no monkeypatch blockers.

---

## PVT-25 — `ThemeSettingsSection.tsx` calls `setState` during render (React anti-pattern)

**Status:** ❌ Not Fixed

**Description:** `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:327-340` does:
```ts
if (config && !customThemeInitRef.current) {
    customThemeInitRef.current = true;
    const draft = _loadDraftFromLS();
    if (draft) setCustomDraft(draft);              // ← setState during render
    else if (config.custom_theme) setCustomDraft(config.custom_theme);  // ← setState during render
    else setCustomDraft({ light: {...}, dark: {...} });                // ← setState during render
}
```
The comment justifies this as "avoids extra render with stale null" but React's docs explicitly warn against `setState` during render — it forces a synchronous re-render before the current render commits. Breaks concurrent-rendering invariants.

**Severity:** 🟡 Medium (React anti-pattern)

**Related Files:** `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:327-340`

**Fix:** Move the init block into a `useEffect` with `[config]` deps and the same `customThemeInitRef` guard.

---

## PVT-26 — `HotkeyPicker.tsx` bypasses `usePythonEvent` for `hotkey_capture_cancel` subscription (latent bug)

**Status:** ❌ Not Fixed

**Description:** `HotkeyPicker.tsx:234-243` subscribes to `hotkey_capture_cancel` via raw `window.python?.onEvent?.(...)` instead of the `usePythonEvent` hook. The `usePython.ts` hook contains an explicit CR-6 fix that polls for `window.python` presence and re-subscribes when the bridge becomes available after mount — exactly the failure mode this component needs to handle (HotkeyPicker may mount before the bridge is installed under slow HMR). The raw subscription silently no-ops if `window.python` is undefined at mount, dropping the cancel event.

**Severity:** 🟡 Medium (latent bug)

**Related Files:** `voice_typer/client/src/renderer/src/components/hotkey/HotkeyPicker.tsx:234-243`

**Fix:** Replace with `usePythonEvent("hotkey_capture_cancel", () => cancelRecordingRef.current?.())`.

---

## PVT-27 — `recorder.py:2315` dead `recent_rms = recent_rms_snapshot` alias

**Status:** ❌ Not Fixed

**Description:** `recorder.py:2292` takes `recent_rms_snapshot` under a lock for thread-safety, then line 2315 aliases it to `recent_rms` — but `recent_rms` is never read after this assignment. The entire `recent_rms_snapshot` snapshot is also dead since nothing reads it. This is exactly the L-2 dead-alias pattern from prior reviews.

**Severity:** 🟢 Low (dead code)

**Related Files:** `voice_typer/server/recording/recorder.py:2292, 2315`

**Fix:** Delete line 2315 (`recent_rms = recent_rms_snapshot`) and the snapshot at line 2292.

---

## PVT-28 — `config.py` `_validate_non_numeric_fields` uses hand-maintained field-name sets (drift hazard)

**Status:** ❌ Not Fixed

**Description:** `config.py:1499-1657`'s `bool_fields`, `str_fields`, `int_fields`, `float_fields` sets are maintained by hand and must be kept in sync with the dataclass field declarations. The file contains a comment (lines 1540-1544) acknowledging a past bug: `"volume_duck_smart_poll_interval_ms"` was misclassified as a bool, causing a spurious "resetting to default 500" warning on every startup. If a new float field is added to the dataclass but forgotten in `float_fields`, it won't be coerced on load — a silent type-coercion gap.

**Severity:** 🟡 Medium (maintainability hazard)

**Related Files:** `voice_typer/server/config.py:1499-1657`

**Fix:** Derive the sets from `typing.get_type_hints(Config)` at class-definition time. Auto-syncs the validator to the dataclass — no more drift.

---

## PVT-29 — `Bubble.tsx` `_className` unused prop + className merges without `cn()`

**Status:** ❌ Not Fixed

**Description:** `Bubble.tsx:217` accepts a `_className` prop but never uses it (dead prop). Lines 503 and 512 use template-literal className merges (e.g. `` `bubble-pill ${mode === "transcribing" ? "transcribing" : ""}` ``) instead of the `cn()` helper — bypasses tailwind-merge benefits.

**Severity:** 🟢 Low (cleanup)

**Related Files:** `voice_typer/client/src/renderer/src/Bubble.tsx:217, 503, 512`

**Fix:** Remove `_className` prop. Replace template-literal merges with `cn()`.

---

## PVT-30 — `tauri-bridge.ts` (673 LOC) is a god module mixing 3 namespaces + 4× duplicated export logic

**Status:** ❌ Not Fixed

**Description:** Single file installs THREE global namespaces (`window.python`, `window.bubble`, `window.window_`). The `bubble` object has 14 methods (~213 LOC). The `window_` object has 4 nearly-identical export wrappers (`exportHistory`, `exportVocabulary`, `exportTemplates`, `exportConfig` at lines 504, 534, 564, 593) — each ~28 LOC of duplicated try/catch + canceled/error mapping logic (~110 LOC of pure duplication). `python.onEvent` (lines 154-213) has nested cancellation logic + FT-1 relay pattern (~60 LOC of orchestration inline).

**Severity:** 🟡 Medium (god module, DRY violation)

**Related Files:** `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts`

**Fix:** Split into `lib/tauri-bridge/{detect,python-namespace,bubble-namespace,window-namespace,index}.ts`. Extract `makeListener()` and `makeExportCommand(name, args)` factories to eliminate the 8× listener boilerplate and 4× export duplication.

---

## Spaghetti/Monolith Auto-Split Plan (Phase 4.5)

Per the MANDATORY Phase 4.5 rule, the following files MUST be split immediately (not just logged):

| File | Current LOC | Action | Target LOC | Blocked by |
|---|---|---|---|---|
| `voice_typer/server/recording/recorder.py` | 3019 | Extract device_manager + resampling promotion + vad_shims | ~2540 | PVT-5/PVT-6 (behavioral fix prerequisite) |
| `voice_typer/server/ipc_server.py` | 2711 | Delete 440 LOC of duplicate helpers + extract tcp_mixin + heartbeat_mixin + main | ~1100-1200 | None (mechanical) |
| `voice_typer/server/service.py` | 2657 | Wire `config_applier.py` + extract 8 sub-services | ~830 | PVT-21 (config_applier wiring) |
| `voice_typer/server/clipboard.py` | 1432 | Convert to `clipboard/` package | ~1390 across 4 files | ~14 test patches |
| `voice_typer/server/volume_backends.py` | 1055 | Convert to `volume_backends/` package | ~1050 across 4 files | None (zero test cost) |

---

## Findings Deferred (out of session scope or low-ROI)

The following findings were identified but are NOT being fixed in this session — they are either (a) too invasive for a single fix wave, (b) blocked by behavioral prerequisites, or (c) low-ROI polish. Listed for the next session:

- **Rust newtypes** (`DispatchId`, `Port`, `AuthToken`, `TrayIconName`, `TargetTriple`) — high-value but touches ~30 call sites across `src-tauri/src/`. Defer to a dedicated session.
- **Serde wire-frame types** replacing 14 `json!({...})` sites — high-value but invasive. Defer.
- **`thiserror` adoption** replacing ~45 `String` error sites — per-module refactor. Defer.
- **`recorder.py` deeper split** (below ~2540 LOC) — blocked by 7 source-string tests that need behavioral rewriting. Defer to CR-67 test-migration pass.
- **`service.py` 8-sub-service extraction** — blocked by PVT-21 (config_applier wiring). Do PVT-21 first; the 8-sub-service extraction is a follow-up.
- **App-level heartbeat** in `recorder.py` audio worker (PVT-1) — needs Tauri-side changes too. Defer to coordinated cross-layer session.
- **`ipc_server.py` deeper split** (TCPServerMixin + HeartbeatHandlersMixin + ServerHandlersMixin + main extraction) — Phase 2 of PVT-19. Do PVT-19 first.
- **`ft1.rs` split** (circuit_breaker + coalesce + tests) — Phase 2 of PVT-3. Do PVT-3 first.
- **`tray.rs` split** (icon + menu + state + click + tests) — Phase 2 of PVT-16. Do PVT-16 first.
- **`migrate.rs` split** — Phase 2 of PVT-4. Do PVT-4 first.
- **`spawn.rs` split** — Phase 2 of PVT-3. Do PVT-3 first.
- **`bubble.rs` split** — independent; defer to next session.
- **`export.rs` split** — independent; defer.
- **`paste.rs` split** — independent; defer.
- **Hotkey validation cross-language drift** (1-18-6) — needs property-based test. Defer.
- **Pydantic migration for `Config` schema** (1-18-1) — large refactor. Defer.
- **Deprecation warning machinery** (1-18-4) — needs UX design. Defer.

---

## Validation Status (per platform-qualified claims rule)

- **Linux (sandbox)**: Python imports verified (`import voice_typer.server.app` succeeds). `pytest tests/test_audio_processor.py tests/test_audio_filters.py` ran: 85 passed, 9 pre-existing failures (in `TestSetSampleRate` — unrelated to this review).
- **Linux (sandbox)**: `cargo check` started in background — compilation ongoing (will complete in ~5 min).
- **Windows host**: NOT run here. `VALIDATE ON WINDOWS HOST`: run `cd src-tauri && cargo check` and `cd voice_typer/client && npm run typecheck && npm run lint && npm run build` after applying fixes.
- **macOS host**: NOT run here. `VALIDATE ON MACOS HOST`: same as Windows + run `bash scripts/build/compile_native.sh` to verify the macOS key-listener binary builds.


---


## Session 2 Findings

Verbatim copy of session-2's `comprehensive-review.md`:

# Comprehensive Review — Group 2 (Performance & Resources)

**Scope**: Full-Review mode, GROUP 2 (Performance & Resources) — 7 categories: Performance, Memory leaks, CPU usage, Resource footprint, Audio pipeline quality, Scalability, Working-but-suboptimal code.

**Method**: 25 parallel review sub-agents covered disjoint file slices of the codebase. This file aggregates their findings (deduplicated, severity-calibrated).

**Environment**: Linux sandbox. Windows/macOS host validation pending for platform-specific items.

---


## [PVT-006] — recorder.py is a 3019-line spaghetti god-class (MANDATORY split)

**Status:** ❌ Not Fixed

**Description:** `voice_typer/server/recording/recorder.py` (3019 lines) mixes 7+ disjoint concerns in a single class: device management (12 methods, ~677 lines), stream lifecycle (start/stop/discard/teardown ~1100 lines), worker thread management (6 methods ~312 lines), audio callback + chunk processing (~500 lines), audio processing helpers (~470 lines), VAD property shims (18 property delegations). The docstring admits the original was "3,215-line god-module" and the split was incomplete.

**Root Cause:** Phase 4.5 / ARCH-045 extracted exceptions, buffer-clear, and resampling into sibling submodules but left the Recorder class itself as a god-class.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py` (entire file, 3019 lines)

**Fix:** Split into focused modules: `recorder/capture.py` (callback + worker loop), `recorder/lifecycle.py` (start/stop/discard/_teardown_stream), `recorder/device_management.py` (12 device methods), `recorder/format.py` (resample/mono/snapshot), `recorder/worker_threads.py`. Keep `Recorder` as a thin facade re-exporting the public API.

**Severity:** 🔴 High

---


## [PVT-008] — recorder.stop() sequentially joins 4 threads (~6s worst-case shutdown)

**Status:** ❌ Not Fixed

**Description:** `stop()` sequentially blocks on: `_teardown_stream()` (up to 300ms callback-drain poll), `_stop_audio_worker(timeout=2.0)` (up to 2s join), `_stop_event_worker(timeout=2.0)` (up to 2s join), `_stop_device_health_checker()` (up to 1s join), `_prepare_audio()` with scipy resample_poly (100-500ms for 30-min audio). Worst case: ~5.8s before stop() returns and transcription can begin. The device-health-checker join (1s) is particularly wasteful — the checker sleeps 30s between probes, so a 1s join almost always times out for no benefit.

**Root Cause:** All four thread joins are serial; none overlap.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py:2556-2582, 2644-2646`

**Fix:** Join the audio worker synchronously (needed for buffer drain), but make event worker and device-health checker fire-and-forget. Reduce `_stop_device_health_checker` timeout from 1.0s to 0.1s. Optionally run `_prepare_audio()` in parallel with worker joins.

**Severity:** 🔴 High

---

## [PVT-010] — Audio filters use per-sample Python loops on the RT thread (~2-3ms/chunk)

**Status:** ❌ Not Fixed

**Description:** `compressor.py:74-94`, `limiter.py:64-82`, `noise_gate.py:100-130`, `equalizer.py:78-97` all use `for i in range(n)` Python loops on the PortAudio RT thread (~16 Hz). Each iteration does numpy-scalar extraction + abs + conditional + math.log10 + 10.0**x + numpy assignment. ~1000 samples/chunk at 16 Hz → ~16k iterations/s with transcendental calls each. Total ~2-3ms per chunk on the RT thread — risks missed PortAudio deadlines (~32ms at 16kHz/1024-sample buffers) on slower machines, causing dropouts/clicks.

**Root Cause:** Pure-Python per-sample loop ported 1:1 from OBS C without vectorization.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_filters/compressor.py:74-94`
- `voice_typer/server/audio_filters/limiter.py:64-82`
- `voice_typer/server/audio_filters/noise_gate.py:100-130`
- `voice_typer/server/audio_filters/equalizer.py:78-97`

**Fix:** Vectorize envelope follower with `np.maximum.accumulate` + one-pole smoothing via `np.cumprod`-style recurrence, compute gain in dB vectorized (`np.log10`/`np.power`). Or wrap with `numba @njit`.

**Severity:** 🔴 High

---

## [PVT-012] — level_monitor holds lock during RNNoise filter call (50% jitter on level bar)

**Status:** ❌ Not Fixed

**Description:** `_process_level_chunk` (worker thread) acquires `_monitor_lock` for the ENTIRE chunk pipeline including RNNoise filter call (5-50ms per chunk). `get_level()` is polled every 100ms by the React frontend (`Microphone.tsx:299` `setInterval(..., 100)`) and also acquires `_monitor_lock`. At 48 kHz / 512-sample blocks, chunks arrive every ~10.7ms, so worker holds lock nearly continuously when RNNoise active. Each frontend poll blocks 5-50ms → ~50% jitter on level bar.

**Root Cause:** Filter call + numpy ops are inside the lock; only the final state writes need synchronization.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/level_monitor.py:1041-1079` (`_process_level_chunk`)

**Fix:** Move `_level_processor.process_chunk()`, `np.abs/np.sqrt/np.mean`, and test-quality-metric computation OUTSIDE the lock. Acquire `_monitor_lock` only for the final state writes. Cuts lock hold time from 5-50ms to ~0.1ms.

**Severity:** 🔴 High

---

## [PVT-013] — level_monitor stop_test_recording re-runs entire filter chain synchronously (7-70s block)

**Status:** ❌ Not Fixed

**Description:** `_test_chunks` stores RAW `indata` (line 1072, not the filtered output). At stop time, the ENTIRE recording (up to 30s) is re-processed through a fresh `AudioProcessor`. At 48 kHz / 1024-sample blocks: 30s × 48000 / 1024 ≈ 1406 blocks × 5-50ms = 7-70 seconds of synchronous blocking on the IPC thread that called `stop_test_recording`.

**Root Cause:** Live filter output not captured; post-hoc re-filtering required.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/level_monitor.py:729-757`

**Fix:** Store the FILTERED audio in `_test_chunks` during the live callback (the worker already runs `_level_processor.process_chunk` at line 1050 — just append `filtered` instead of `indata.copy()`), eliminating post-hoc re-processing. The "before" WAV (`_test_raw_chunks`) already has the raw audio.

**Severity:** 🔴 High

---

## [PVT-017] — parakeet_engine.transcribe() holds lock during entire inference (10-60s)

**Status:** ❌ Not Fixed

**Description:** The entire `transcribe()` body — including the multi-chunk loop calling `_transcribe_segment()` (which runs `self._model.generate()`) — is wrapped in `with self._lock:` (line 470). For a 5-minute recording split into ~13 chunks, the lock is held for the entire 30–60+ seconds of GPU inference. `is_loaded`, `unload()`, and any concurrent caller block for the full inference duration. Unlike `QwenEngine` (which has the RACE-032 fix that releases the lock before inference), `ParakeetEngine` was never updated.

**Root Cause:** RACE-032 pattern not applied to parakeet.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/parakeet_engine.py:459-495` (`ParakeetEngine.transcribe`)

**Fix:** Mirror QwenEngine's RACE-032 pattern: acquire `self._lock` only to snapshot `model = self._model` / `processor = self._processor` and set an inference flag, then release the lock before calling `_transcribe_segment()`.

**Severity:** 🔴 High

---

## [PVT-019] — qwen chunked transcription has no overlap dedup (duplicate words)

**Status:** ❌ Not Fixed

**Description:** `_transcribe_chunked` (line 302-356) splits audio with 3s overlap (`_QWEN_CHUNK_OVERLAP_SECONDS = 3`), but results are joined with simple `" ".join(results)` — no overlap deduplication. Each chunk's first ~3s of audio overlaps the previous chunk's last ~3s, so both chunks produce text for the overlap region. For a 5-minute recording split into ~11 chunks, ~30s of audio is transcribed twice, producing duplicated words (~50-100 duplicated words at 200 WPM).

**Root Cause:** Incorrect assumption about Whisper decoder behavior. The docstring claims "Whisper-style models generally do not re-transcribe overlap text" — Whisper transcribes ALL audio it receives.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/qwen_engine.py:302-356` (`_transcribe_chunked`)

**Fix:** Implement word-level overlap dedup similar to `ParakeetEngine._merge_chunks`, OR reduce overlap to 0 and accept boundary artifacts, OR use Whisper timestamps to trim overlap audio before transcription.

**Severity:** 🟡 Medium

---

## [PVT-021] — llm_polish 30s blocking call on dictation thread

**Status:** ❌ Not Fixed

**Description:** `_call_api` (line 255) does `with _opener.open(req, timeout=30) as resp:` — a hard 30-second blocking `urlopen` on the dictation pipeline's step-7 hot path. `dictation_pipeline.py:705` calls `self._app._llm_polisher.polish(text)` synchronously between transcription and paste. When polish is on, every dictation blocks the pipeline thread for up to 30s. User sees no paste, waveform bubble stalls, next dictation queues behind it.

**Root Cause:** Polish invoked inline on the dictation thread with no async/offload.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/llm_polish.py:255` (`_call_api`); called from `dictation_pipeline.py:705`

**Fix:** Either (a) make polish non-blocking — fire it on a worker thread and paste polished text via follow-up IPC event, falling back to raw text on a 2-3s deadline; or (b) expose `timeout` as a config field (`llm_request_timeout_s`, default 10) and lower the default.

**Severity:** 🟡 Medium

---

## [PVT-023] — macOS prewarm poll loop spawns `ps` subprocess every iteration (up to 60 spawns)

**Status:** ❌ Not Fixed

**Description:** On macOS, `_process_is_prewarm` spawns `ps -o command= -p {pid}` subprocess. `wait_for_prewarm`'s fallback poll loop (1s sleep, 60s timeout) calls `is_prewarm_running` → `_process_is_prewarm` up to 60 times. Each `ps` spawn is ~10-50ms. Up to 60 subprocess spawns (600ms-3s of fork/exec overhead) during a single `wait_for_prewarm` call on macOS. The event-based wait returns False on macOS (unsupported), so poll fallback always runs.

**Root Cause:** Poll loop re-validates PID is prewarm on every iteration via subprocess spawn.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/process_tracker.py:470-482` (`_process_is_prewarm` macOS branch); `wait_for_prewarm` poll loop at 591-596

**Fix:** Cache the cmdline check result for the duration of the poll loop. The PID can't change identity mid-wait — if it exits and gets recycled, `_process_alive` returns False first.

**Severity:** 🟡 Medium

---

## [PVT-024] — quit() holds _quit_lock across shutdown_all() join loop

**Status:** ❌ Not Fixed

**Description:** `quit()` acquires `with self._quit_lock:` and calls `app._thread_registry.shutdown_all()` INSIDE the lock. `shutdown_all()` joins each registered daemon thread with its per-thread timeout. With N threads × M-second timeouts, the lock is held for N×M seconds. A concurrent `quit()` from the atexit net, POSIX signal-watcher, Win32 console handler, or IPC `quit_app` handler blocks on `_quit_lock` for the entire window.

**Root Cause:** Lock scope too broad; only the check-then-set needs the lock.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/shutdown_controller.py:512-535`

**Fix:** Move `shutdown_all()` OUTSIDE the `with self._quit_lock:` block (after the flag is set and released). The `_shutting_down` flag already prevents double-entry; `shutdown_all()` is documented as idempotent.

**Severity:** 🔴 High

---

## [PVT-025] — download_model polling stats entire HF cache every second (10-40% CPU)

**Status:** ❌ Not Fixed

**Description:** `download_model` polling loop (line 2268-2272): `if cache_dir.exists(): total_bytes_seen = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())`. `cache_dir` is `<config_dir>/huggingface/hub` — the root of the entire HF cache, not the per-model blob directory. This `rglob("*")` runs once per second for the whole download (minutes). User with 4 cached models triggers ~thousands of `stat()` syscalls per second during every download. Sustained 10–40% CPU + heavy disk I/O on the IPC handler thread.

**Root Cause:** Walks entire HF cache root instead of the specific downloading model's directory.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/service.py:2268-2272`

**Fix:** Scope the walk to the downloading model's directory: `cache_dir / repo_dir_name`. Better: use HF hub's `snapshot_download` `max_workers` + progress callback instead of polling filesystem.

**Severity:** 🔴 High

---

## [PVT-026] — service.py is 2657-line spaghetti (3.3× the 800-line threshold)

**Status:** ❌ Not Fixed

**Description:** `service.py` (2657 lines) mixes 12+ unrelated concerns: config management, history DB CRUD, microphone + mic-test, level monitor, onboarding wizard (~230 lines), model download/import/delete/cancel/pause (`download_model` alone is ~470 lines), templates, GDPR, diagnostics, vocabulary, audio status, volume status, download-cancellation state machine. The `apply_config_side_effects` if-chain (lines 1064–1278) is a 214-line procedural dispatch table that should be a dict.

**Root Cause:** Service introduced as thin facade but accreted every new IPC feature without sub-module extraction.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/service.py` (entire file)

**Fix:** Split into `services/{config,history,microphone,model_download,onboarding,templates,gdpr,diagnostics}.py`, each <400 lines. `VoiceTyperService` becomes a facade.

**Severity:** 🔴 High

---

## [PVT-027] — ipc_server.py is 2711-line spaghetti with duplicated rate limiter

**Status:** ❌ Not Fixed

**Description:** `ipc_server.py` (2711 lines) re-defines `_RateLimiter` (lines 228-342), `_get_rate_limiter` (385-428), `_pick_available_port` (93-143), `_bound_history_limit`/`_bound_history_offset`/`_sanitize_config_for_ipc` (465-506), and constants (177-225) — verbatim duplicates of leaf modules under `ipc/`. The `rate_limiter.py` docstring admits this: "this leaf copy is kept in sync with the canonical implementation in `ipc_server.py`". Only `_TCPLineIO` and `_validate_dict_payload`/`_error_response` are imported.

**Root Cause:** ARCH-045 / CR-14 split extracted validation+transport to leaf modules but left `_RateLimiter` and history/config helpers as duplicate definitions in the god-module for `from voice_typer.server.ipc_server import X` test compatibility.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc_server.py` (entire file); duplicates in `voice_typer/server/ipc/rate_limiter.py`, `ipc/history_bounds.py`, `ipc/transport.py`

**Fix:** Replace inline definitions with imports from leaf modules (mirror existing `validation`/`transport` import pattern). The `# noqa: F401` re-export convention covers test compatibility.

**Severity:** 🔴 High

---

## [PVT-029] — app.py is 1317-line spaghetti + StartupSequence.run() is 497-line method

**Status:** ❌ Not Fixed

**Description:** `app.py` (1317 lines) exceeds the 800-line threshold. ~280-line `__init__`, ~160-line `restart_app`, ~120-line `_open_config_file`, ~30 `# noqa: F401` re-exports for test backward-compat. `startup_sequence.py:run()` (lines 66-563) is a 497-line single method mixing 13 logically independent phases: crash diagnostics → onboarding auto-heal → corrections load → crash-recovery check → history-retention thread spawn → Wayland warning → macOS accessibility check → autostart sync → parallel prewarm + mic enumeration → desktop shortcut → hotkey registration → background model load → restart electron window → bubble show.

**Root Cause:** RW-9 "god-class decomposition" extracted bodies into 9 controllers but kept thin delegates on `VoiceTyperApp` for test backward-compat. StartupSequence.run() was a literal move, not a decomposition.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/app.py` (entire file); `voice_typer/server/startup_sequence.py:66-563` (`run`)

**Fix:** Consolidate fix-history commentary into CHANGELOG. Replace delegates with `__getattr__` facade. Decompose `run()` into named phase methods (`_phase_crash_diagnostics`, `_phase_onboarding`, etc.).

**Severity:** 🔴 High

---

## [PVT-030] — Windows microphone_watcher uses PeekMessage 10Hz poll (~864k idle wakeups/day)

**Status:** ❌ Not Fixed

**Description:** `microphone_watcher.py:621-632`: Windows device-change watcher uses non-blocking `PeekMessageW` in a 10 Hz poll loop. Comment justifies as "so `stop_event` can interrupt within ~100ms" but `stop()` already calls `_post_quit_to_windows()` which posts `WM_QUIT` — a blocking `GetMessageW` would return immediately on `WM_QUIT` (and on every `WM_DEVICECHANGE`), giving both zero idle wakeups and faster stop response. ~864,000 unnecessary wakeups/day. Blocks C-states, contributes to idle battery drain.

**Root Cause:** PeekMessage+wait pattern chosen when GetMessage+WM_QUIT would suffice.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/microphone_watcher.py:621-632`

**Fix:** Replace `PeekMessageW`+`wait(0.1)` pump with `GetMessageW` (blocking). `WM_DEVICECHANGE` makes it return for events; `WM_QUIT` (already posted by `_post_quit_to_windows`) makes it return for stop.

**Severity:** 🔴 High

---

## [PVT-031] — event_bus unbounded deferred queue + listener leak

**Status:** ❌ Not Fixed

**Description:** `event_bus.py:130` — `_subscribers: set[Callable] = set()` (strong refs). If a subscriber (e.g. `IPCServer`) is destroyed without calling `unsubscribe()` (exception during `stop()`, crash, restart_app), the bound method keeps the IPCServer instance alive forever. Also: deferred-publish `ThreadPoolExecutor` has a single worker and unbounded internal `SimpleQueue`. If the one subscriber is slow (e.g. `socket.sendall` to stalled Electron renderer), each RT-thread `publish` (bubble_level fires ~60 Hz) queues a `_deliver(event, fns)` task. At 60 Hz × 10 min = 36,000 queued tasks.

**Root Cause:** Plain `set` for subscribers (not `WeakSet`); unbounded executor queue.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/event_bus.py:130, 151-196, 301-309`

**Fix:** Use `weakref.WeakSet` for `_subscribers`. Bound the deferred executor's queue (drop or coalesce `bubble_level` events when queue exceeds N).

**Severity:** 🔴 High

---

## [PVT-033] — secure_file_io.py is a 175-LOC dead duplicate of config.py helpers

**Status:** ❌ Not Fixed

**Description:** `secure_file_io.py` (175 lines) is never imported anywhere in production code. All 20+ real call sites import `_secure_atomic_write` / `_secure_read_text` from `voice_typer.server.config` instead. The `secure_file_io.py` docstring itself says "re-exported from `config.py` so existing call sites keep working unchanged" — but the originals were never deleted from `config.py`. Diff of the two implementations shows byte-for-byte identical (only parameter type annotation differs).

**Root Cause:** CR-28 extraction started (new module created with full copies) but never completed (originals never deleted, callers never switched).

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/secure_file_io.py:1-175` (entire file); duplicates at `voice_typer/server/config.py:64-127` and `:129-175`

**Fix:** Delete the originals from `config.py` and have it re-export: `from voice_typer.server.secure_file_io import _secure_atomic_write, _secure_read_text`. Verify tests that monkeypatch `voice_typer.server.config._secure_atomic_write` still work.

**Severity:** 🟡 Medium

---

## [PVT-034] — settings_controller bypasses _config_mutation_lock (lost-update race)

**Status:** ❌ Not Fixed

**Description:** `settings_controller.py:80-104` (`set_autostart`), `108-120` (`set_notifications`), `124-157` (`select_microphone`) — none acquire `_config_mutation_lock`. Each does read-modify-save: `app.config.X = enabled; app.config.save()`. Meanwhile every IPC `set_config` path acquires the lock. So a tray-menu toggle of autostart/notifications/mic racing with an IPC `set_config` is a classic lost-update: whichever save runs last wins, and the other change is silently dropped. The mic-change path is worse: `app.recorder = Recorder(app.config, ...)` (line 155) reads `app.config` outside any lock — if a concurrent IPC `set_config` mutates `microphone` or `sample_rate` mid-construction, the new `Recorder` could be built with torn config.

**Root Cause:** RW-9 extraction moved methods off `VoiceTyperApp` verbatim without adding the lock discipline that `apply_config` acquired later (RACE-011).

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/settings_controller.py:80-157`

**Fix:** Wrap each mutator body in `with self._app._config_mutation_lock:`. Better: route them through `ConfigApplier.apply_config(updates_dict)` so there's a single locked path.

**Severity:** 🔴 High

---

## [PVT-035] — vocabulary automation: no caching, O(N) Levenshtein per word

**Status:** ❌ Not Fixed

**Description:** Three compounding issues:
1. `vocabulary.py:549` — `apply_to_text` recompiles regex per phrase per dictation (up to 10,000 compiles per call). `_phrase_pattern_cache` already exists in `text_cleanup.py` but wasn't ported.
2. `vocabulary_automation.py:401` — `_collect_vocabulary_words(self._vm)` runs on every transcription. Iterates ALL 6 categories, tokenizes every value string, builds a set. Vocabulary changes rarely (only on user apply/dismiss/import).
3. `vocabulary_automation.py:723-735` — `_find_closest_vocabulary_match` linear scan over `vocab_words` computing Levenshtein per candidate. With 50,000 vocab words and 10 suspicious words per transcription: ~32M character comparisons per dictation.

**Root Cause:** No caching infrastructure; O(N) Levenshtein without indexing.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vocabulary.py:549, 531-532`
- `voice_typer/server/vocabulary_automation.py:401, 723-735`

**Fix:** (1) Reuse `text_cleanup._get_compiled_phrase_pattern()` (LRU cache). (2) Cache `vocab_words` on `VocabularyAutomation` instance; invalidate on mutation. (3) Index vocab words by length (bucket map) so `abs(len(candidate) - len(word)) > max_distance` becomes bucket lookup. For larger scale, use BK-tree.

**Severity:** 🔴 High

---

## [PVT-036] — clipboard double _is_safe_paste_target call (~30-50ms extra per paste)

**Status:** ❌ Not Fixed

**Description:** `clipboard.py:1134` first `if not self._is_safe_paste_target():` check. Line 1148 second `if not self._is_safe_paste_target():` check (after `paste_delay` sleep). Each call does `GetForegroundWindow` + `GetClassNameW` + `_is_elevated_target` (OpenProcess + OpenProcessToken + GetTokenInformation × 2 + CloseHandle × 2) + `CoInitialize` + `_get_uia_focused_element` (UIA `GetFocusedElement` RPC) + `_is_password_field` (RPC) + `_is_content_editable` (RPCs) + `CoUninitialize`. When `paste_delay == 0` (common non-RDP case), no sleep occurs — second check is pure waste: ~5-7 cross-process UIA RPCs + ~6 kernel calls duplicated.

**Root Cause:** CRIT-2 TOCTOU guard for focus changes during `paste_delay`. When `paste_delay == 0`, no focus change possible between checks.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard.py:1134, 1148`

**Fix:** Skip second `_is_safe_paste_target()` call when `paste_delay == 0`, or share `hwnd`/`focused` element between the two calls (re-fetch only `GetForegroundWindow` to detect focus change, only re-run full checks if hwnd differs).

**Severity:** 🟡 Medium

---

## [PVT-038] — 3 native hotkey subprocesses per app (triple kernel-side resource usage)

**Status:** ❌ Not Fixed

**Description:** `HotkeyDispatcher.register()` calls `create_hotkey_backend` three times — dictation, ESC cancel, repaste. Each `create_hotkey_backend` → `SubprocessHotkeyBackend._spawn_process` does `subprocess.Popen([binary, spec], ...)`. 3 separate native binary subprocesses. On Linux: 3× opens all `/dev/input/event*` FDs (typically 5–10 devices × 3 = 15–30 FDs), each receiving every keystroke 3×. On Windows: 3× `WH_KEYBOARD_LL` hooks. On macOS: 3× `NSEvent` global monitors + 3× `CGEventTap` Mach ports. Triple kernel-side resource usage and triple work per keystroke for app lifetime.

**Root Cause:** Architectural — one native binary per hotkey spec rather than one binary multiplexing multiple specs.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py:162, 259, 352`
- `voice_typer/server/native_hotkeys/base.py:235-262` (`_spawn_process`)

**Fix:** Refactor wire protocol so a single native binary accepts multiple hotkey specs (via stdin at startup) and emits matched-spec events. Or share one `SubprocessHotkeyBackend` across all three hotkeys and dispatch in Python.

**Severity:** 🔴 High

---

## [PVT-039] — blocking keyring calls stall IPC + startup (up to 150s hang)

**Status:** ❌ Not Fixed

**Description:** All keyring I/O in `credential_store.py` is synchronous with no timeout wrapper:
- `store_secret`: `keyring.set_password(...)` (line 486) — called from IPC `set_config` handler thread
- `load_secret`: `keyring.get_password(...)` (line 542) — called from `Config.load()` at startup × 5 providers
- `_probe_keyring`: `backend.get_password(...)` (line 342) — once, can hang on broken D-Bus
- migration: `keyring.set_password(...)` in loop over 5 providers (line 941) — 5 × 30s = up to 150s hang

`keyring` calls D-Bus (Linux libsecret, default timeout ~30s), Keychain (macOS, can block indefinitely waiting for unlock prompt), or Credential Manager (Windows pywin32).

**Root Cause:** No `ThreadPoolExecutor` / `Timer` / subprocess isolation around keyring calls.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/credential_store.py:486, 542, 575, 342, 941`

**Fix:** Wrap each keyring call in `concurrent.futures.ThreadPoolExecutor.submit(...).result(timeout=5)`; on `TimeoutError`, fall back to plaintext + log. Run `migrate_secrets_to_keyring` in background thread, not on startup path.

**Severity:** 🔴 High

---

## [PVT-041] — TCP buffer 4MB cap drops legitimate large replies (history, diagnostics)

**Status:** ❌ Not Fixed

**Description:** `tcp-connect.ts:96-120` — `if (state.tcpBuffer.length > 4 * 1024 * 1024) { state.tcpBuffer = ""; client.destroy(); }`. Any legitimate reply larger than 4 MiB (e.g. `get_history` for power user with tens of thousands of entries, `export_diagnostics`, large `get_vocabulary` dumps) silently truncated and TCP socket destroyed mid-reply. Renderer's `python-call` then times out after 120s instead of getting a clean error. Also: `state.tcpBuffer += chunk.toString()` is O(buffer size) per chunk — pathological when slow-streaming large reply grows buffer toward 4 MiB. `JSON.parse(line)` on multi-MB single line blocks main thread for tens-hundreds of ms.

**Root Cause:** 4 MiB cap is DoS guard against malformed frames, but triggers on cumulative buffer size.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts:96-120`

**Fix:** (a) Raise cap to 64 MiB AND surface structured error to renderer ("reply too large") rather than dropping socket. (b) Move `JSON.parse` off main thread (worker_thread) for lines above size threshold. (c) Replace string-concat buffer with array of chunks joined on demand.

**Severity:** 🟡 Medium

---

## [PVT-042] — unbounded pendingRequests map (memory bloat under sustained IPC load)

**Status:** ❌ Not Fixed

**Description:** `send-to-python.ts:71-88` — every `python-call` IPC adds entry to `state.pendingRequests` and a 120-second `setTimeout` whose closure captures `msg`, `id`, `resolve`, `reject`, `timer`. No upper bound on the Map, no concurrency limit, no per-renderer rate limit. A compromised or buggy renderer (polling loop firing `python-call` every 16ms) can enqueue thousands of pending entries per second, each pinning a 120s timer + closure for up to 2 minutes. At 60 calls/sec sustained for 120s, that's ~7,200 live entries + 7,200 live timers, each holding a `msg` object — easily tens of MB retained memory.

**Root Cause:** No backpressure on the pending map.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/python/send-to-python.ts:71-88`
- `voice_typer/client/src/main/ipc/python-call-handler.ts:16-30`
- `voice_typer/client/src/main/state.ts:53`

**Fix:** (a) Cap `pendingRequests` size (reject new calls above 256 concurrent). (b) Add per-renderer rate limit. (c) Reduce 120s timeout for non-download commands (gate timeout on command name).

**Severity:** 🟡 Medium

---

## [PVT-043] — Bubble useAudioLevels rAF loop runs at 60fps even when not recording

**Status:** ❌ Not Fixed

**Description:** `Bubble.tsx:75-133` `useAudioLevels` hook: `useEffect` (deps `[dotRefs]`) starts a `requestAnimationFrame` chain that reschedules itself every frame. Cleanup only cancels the frame on unmount. The `api.onLevel(onLevel)` IPC subscription is also active for component's entire lifetime. When `mode !== "recording"` (transcribing, idle, fading), the dot `<span>` elements are unmounted, the loop's `if (!el) continue;` skips all 7 dots — but the rAF loop keeps spinning at 60 fps. In `always_visible` idle mode the bubble can stay mounted for hours/days; rAF loop and `onLevel` IPC handler run continuously at 60 fps / per-IPC-event, draining CPU and battery.

**Root Cause:** Animation loop and IPC level subscription tied to component lifetime, not to recording state.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/Bubble.tsx:75-133` (`useAudioLevels` hook)

**Fix:** Pass `mode` into `useAudioLevels` and only start rAF loop + `onLevel` subscription when `mode === "recording"`. Tear both down when mode changes away from recording.

**Severity:** 🔴 High

---

## [PVT-046] — Unbounded WS writer channel in Rust host (OOM under backpressure)

**Status:** ❌ Not Fixed

**Description:** `src-tauri/src/sidecar/ws.rs:34` — `let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<Message>();`. `WsWriterTx = mpsc::UnboundedSender<Message>`. Producers: `sidecar_cmds.rs:230` (every `dispatch_frame`), `bubble.rs:363` (every `toggle_dictation`), `sidecar_cmds.rs:415` (shutdown), `ws.rs:38` (auth). If WS write path stalls (sidecar slow to read, TCP backpressure, half-open connection where `write.send` blocks awaiting ACK), every dispatch call enqueues a full `Message::Text` string with no backpressure. Under sustained dispatch during degraded WS, memory grows without bound until OOM. 120s `DISPATCH_TIMEOUT_SECS` bounds lifetime of pending-map entries but NOT the ws_tx channel — frames remain queued even after dispatch caller has timed out.

**Root Cause:** `mpsc::unbounded_channel` has no backpressure.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/sidecar/ws.rs:34`; type at `src-tauri/src/state.rs:21`
- Producers: `src-tauri/src/commands/sidecar_cmds.rs:230,415`, `src-tauri/src/commands/bubble.rs:363`

**Fix:** Switch to `mpsc::channel(N)` (e.g. N=256). Have `dispatch_frame` use `ws_tx.send(...).await` (bounded `Sender::send` is async) or `try_send` with fail-fast on full.

**Severity:** 🔴 High

---

## [PVT-048] — `blocking_save_file()` / `blocking_pick_folder()` called from async Tauri commands

**Status:** ❌ Not Fixed

**Description:** `src-tauri/src/commands/export.rs:58` (`.blocking_save_file()`); `src-tauri/src/commands/system_cmds.rs:123` (`.blocking_pick_folder()`); both reached from `#[tauri::command] pub async fn`. Tauri docs warn that `blocking_*` APIs must not be called from async command handlers because they park the Tokio worker thread until user dismisses dialog. A user who leaves save dialog open for tens of seconds blocks one Tokio worker the entire time. With Tauri's default 2-N worker pool, concurrent `dispatch` calls (heartbeat, status polling) queued behind the blocked worker stall. On a 2-core machine the entire IPC layer can freeze while dialog is open.

**Root Cause:** Blocking dialog API on the async runtime instead of `save_file().await` (async variant) or `spawn_blocking`.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/commands/export.rs:58`; `src-tauri/src/commands/system_cmds.rs:123`

**Fix:** Switch to async `save_file()` / `pick_folder()` methods exposed by `tauri-plugin-dialog` v2, or wrap in `spawn_blocking`.

**Severity:** 🔴 High

---

## [PVT-049] — RotatingFileWriter holds sync Mutex across flush()+metadata() (log serialization)

**Status:** ❌ BROKEN — fix absent; `logging.rs:272-278` still holds `Mutex` across `file.metadata()?.len()` and `self.rotate()`.

**Description:** `src-tauri/src/platform/logging.rs:118-143` — `write_line` does `let mut guard = self.inner.lock().unwrap();` (std Mutex), then `file.write_all(line.as_bytes())?; file.write_all(b"\n")?; file.flush()?;` (disk I/O under lock), then `let len = file.metadata()?;` (syscall under lock), then `if len > ROTATE_MAX_BYTES { ... self.rotate()?; }` (5x stat + 4x rename under lock). Single global `std::sync::Mutex<Option<File>>` serializes every log line across all threads. `eprintln!` happens before file write — stderr's own internal lock also held per call. On panic while holding the lock, mutex poisons and all subsequent log calls panic via `.unwrap()`, taking down the host.

**Root Cause:** Single sync mutex serializes every log line, with `flush` and `metadata` inside critical section.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/platform/logging.rs:118-143` (`write_line`); called from every `log::*!` via `CombinedLogger::log`

**Fix:** (a) Move `metadata()` + rotate check out of hot path (every Nth write or via separate janitor task). (b) Use a `mpsc::UnboundedSender<String>` to dedicated writer thread instead of sync mutex. (c) Handle poison explicitly instead of `.unwrap()`.

**Severity:** 🔴 High

---

## [PVT-050] — child_exit_rx never drained (stderr accumulates over session)

**Status:** ❌ Not Fixed

**Description:** `src-tauri/src/sidecar/spawn.rs:141` returns `mpsc::Receiver<CommandEvent>`. `ft1.rs:244-247` stores in `state.child_exit_rx`. The ONLY consumer is `shutdown_sidecar`, which performs a single `rx.recv().await` inside a timeout window. Between handshake-success and shutdown, NO task drains the receiver. `tauri-plugin-shell` pumps every `Stdout`/`Stderr`/`Terminated`/`Error` event from the child into this receiver for the child's entire lifetime. The release sidecar sends all non-handshake logs to stderr (ADR-0020 §1), so stderr events flow continuously. With no drainer, they accumulate in channel buffer for the whole session.

**Root Cause:** No background drainer task.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/sidecar/spawn.rs:141` (returns rx)
- `src-tauri/src/sidecar/ft1.rs:244-247` (stores in state)
- `src-tauri/src/commands/sidecar_cmds.rs:425-450` (only polled at shutdown)

**Fix:** Spawn a background task at handshake-success that drains `child_exit_rx` for the sidecar's lifetime, logging stderr lines and forwarding only `Terminated` to a oneshot channel that `shutdown_sidecar` awaits.

**Severity:** 🟡 Medium

---

## [PVT-051] — Dead `paste_text` Tauri command (165 LOC maintained but never used)

**Status:** ❌ Not Fixed

**Description:** `src-tauri/src/commands/paste.rs` (326 lines) + wrapper `paste_text` command at `sidecar_cmds.rs:331-402`. The doc explicitly states "Production traffic never reaches this command. The Python sidecar does its OWN paste internally in `voice_typer/server/dictation_pipeline.py:990-1010` … no Python code publishes a `paste_text` event, and no TS code invokes `invoke('paste_text', ...)`." Retained only for migration glue tests.

**Root Cause:** Migration scaffolding that survived the cutover.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/commands/paste.rs` (326 lines); `src-tauri/src/commands/sidecar_cmds.rs:331-402`

**Fix:** Either delete (and update migration tests) or mark `#[deprecated]` + add a test asserting `dictation_pipeline.py` still owns the paste path.

**Severity:** 🟡 Medium

---

## [PVT-052] — No benchmarks for audio-pipeline hot paths (regressions invisible)

**Status:** ❌ Not Fixed

**Description:** `bench/` contains only 2 harnesses. `bench_transcription.py` exercises only `TranscriptionEngine.transcribe_with_fallback`. `bench_startup.py` measures only `import voice_typer.server.tray`. `tests/test_benchmarks.py` covers only: text_cleanup, raw RMS computation, config parse. No `benchmark` fixture tests exist for `audio_filters/*` (FilterChain), `audio_processor.process_chunk`, `streaming.py`, `vad.py`/`vad_processor.py`, `recording/resampling.py`, `level_monitor.py`, `ipc_server._dispatch`. ADR-0009 §11 specifies a perf test: "process 60s of 48kHz audio, verify < 5% CPU, < 15ms latency, no dropouts" — unimplemented.

**Root Cause:** `pytest-benchmark` declared but never used for realtime audio path.

**Progress:** None yet.

**Related Files:**
- `bench/bench_transcription.py:39-63`; `bench/bench_startup.py:71`; `tests/test_benchmarks.py:27-86`; `docs/adr/0009-audio-filter-chain-architecture.md:559`

**Fix:** Add `tests/bench/test_filter_chain_bench.py` covering `FilterChain.process_chunk` on 1s/10s/60s 16 kHz + 48 kHz signals with `benchmark` fixture; assert `total_latency_ms < 15`. Add `test_streaming_assembler_bench.py`, `test_vad_processor_bench.py`. Wire a `--bench` CI job.

**Severity:** 🔴 High

---

## [PVT-053] — Nuitka build pipeline: no parallelism, no shared cache (~2-3 hours per release)

**Status:** ❌ Not Fixed

**Description:** Each per-platform Nuitka invocation runs single-threaded by default — no `--jobs=N`, no `--lto=yes`, no `--cache-dir` is passed in any of the 6 build scripts. `build_tauri_all.sh:145-167` invokes sidecar + prewarm + native serially per platform — prewarm is functionally a subset of sidecar (same `--include-package=faster_whisper ctranslate2 voice_typer websockets`), yet its Nuitka C compilation starts from scratch. For 3 platforms × 2 binaries × ~12 min = ~2-3 hours of Nuitka per release.

**Root Cause:** Per-platform scripts written for correctness, not build throughput. Nuitka's `--jobs` and `--cache-dir` flags never added.

**Progress:** None yet.

**Related Files:**
- `scripts/build/build_sidecar_linux.sh:217,248-262`; `build_sidecar_macos.sh:119-140`; `build_sidecar_windows.sh:128-151`; `build_prewarm_*.sh`; `build_tauri_all.sh:145-167`

**Fix:** (a) Add `--jobs="$(nproc)"` (Linux/macOS) / `--jobs=$NUMBER_OF_PROCESSORS` (Windows) to all 6 Nuitka invocations. (b) Add `--cache-dir="$PROJECT_ROOT/.nuitka-cache"` reused for sidecar + prewarm within a platform. (c) Factor duplicated `NUITKA_ARGS` into `scripts/build/_nuitka_common_args.sh`.

**Severity:** 🔴 High

---

## [PVT-054] — Dependency pins deliberately block upstream perf + security fixes (4 CVEs)

**Status:** ❌ Not Fixed

**Description:** Three pins:
- `numpy>=1.26,<2.0` — comment cites ABI breakage with faster-whisper/sounddevice. numpy 2.x has been out since June 2024 (2 years stale), bringing faster array ops, lower memory, free-threading support.
- `transformers>=4.50,<5.0` — parakeet_engine uses `AutoModelForTDT` which transformers 5.x REMOVED. 4 known CVEs (PYSEC-2026-2288/2289/2290 + PYSEC-2025-217) cannot be cleared without migrating off `AutoModelForTDT`. Comment acknowledges: "4 known CVEs cannot be cleared without migrating the parakeet engine — tracked as a follow-up task. The <5.0 pin blocks the upstream fix deliberately."
- `pystray>=0.19,<0.20` — `tray.py:_apply_state` reaches into private `_icon_handle`; 0.20+ could break workaround.

**Root Cause:** Each pin originated as a correct tactical decision, but the migration off the private/blocked API was deferred. CVE debt on transformers is ~15 months unaddressed.

**Progress:** None yet.

**Related Files:**
- `pyproject.toml:70,82,104`
- `voice_typer/server/parakeet_engine.py:192-210,395,413`

**Fix:** (a) Migrate `parakeet_engine.py:395,413` off `AutoModelForTDT` to transformers-5.x-supported class, then bump `transformers>=5.0`. (b) Retest faster-whisper/sounddevice wheels against numpy 2.x and bump `numpy>=2.0`. (c) File upstream `pystray` `reset_icon_handle()` API request.

**Severity:** 🔴 High

---

## [PVT-055] — config.py is 1826-line spaghetti + redundant Config.load() calls in prewarm

**Status:** ❌ Not Fixed

**Description:** `config.py` (1826 lines) exceeds 800-line threshold by 2.3×. Single classmethod `Config.load()` is 381 lines (parses JSON, runs schema migrations, integrates with credential_store, validates paths, coerces 4 type-buckets across 100+ fields via `_validate_non_numeric_fields` (297 more lines), calls `apply_preset`). The dataclass has 132 fields. Field-default maintenance is split across THREE places: (1) dataclass declaration, (2) bool_fields/str_fields/int_fields/float_fields sets in `_validate_non_numeric_fields`, (3) `IPC_CONFIG_ALLOWLIST` in `config_validators.py`. Adding a field requires touching all three or the field silently bypasses load-time coercion. Also: `Config.load()` is called twice within `cache_probe.py` (line 153 and 378) — doubles prewarm cold-start I/O and D-Bus traffic.

**Root Cause:** Organic growth across many ADRs without periodic decomposition. Two prewarm call sites written independently.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/config.py:1-1826` (`Config.load()` at 1096-1475; `_validate_non_numeric_fields` at 1477-1771)
- `voice_typer/server/prewarm/cache_probe.py:153, 378`

**Fix:** Decompose into `config_schema.py` (dataclass + field metadata), `config_loader.py` (JSON read + migration + asdict-based reconstruction driven by schema). Drive `_validate_non_numeric_fields` from `dataclasses.fields(cls)`. Cache `Config.load()` result in prewarm process.

**Severity:** 🔴 High

---

## [PVT-056] — audio_processor: clipping detector blind under default limiter; set_sample_rate missing

**Status:** ❌ Not Fixed

**Description:** Two related issues in `audio_processor.py`:
1. `process_chunk` fires `_run_quality_check` on POST-filter audio (line 188). The default AUTO/STUDIO/NOISY_ROOM presets enable the Limiter with `ceiling_db=-6.0` (≈0.50 linear). `AudioQualityAnalyzer.CLIPPING_THRESHOLD=0.99`. The limiter clamps every sample's envelope to ≤0.50, so the post-filter peak fed to the clipping detector can never reach 0.99 when the limiter is active. Users are never warned about mic clipping as long as the limiter is ON — the default.
2. `audio_quality_controller.py:156-165` does `set_sr = getattr(self._app._audio_processor, "set_sample_rate", None)` then `if callable(set_sr): set_sr(force_sr)`. A full read of `audio_processor.py` confirms NO `set_sample_rate` method exists on the class. The controller's `force_sr` parameter, the `set_sr` getattr branch, and the `else` debug log are all dead code. Tests `test_audio_processor.py:287-371` call `p.set_sample_rate(48000)` and `p.sample_rate` on real AudioProcessor instances — these would raise AttributeError. Pre-existing test failures.

**Root Cause:** (1) Quality metrics computed on chain OUTPUT, after brick-wall limiter. (2) AUDIO-6/AUDIO-9 fix was never implemented on AudioProcessor; controller's `force_sr` path is dead.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_processor.py:184-197` (clipping detector); 79-242 (entire class — no `set_sample_rate`)
- `voice_typer/server/audio_quality.py:52, 165, 189`
- `voice_typer/server/audio_quality_controller.py:156-165`
- `voice_typer/server/audio_filters/limiter.py:35`

**Fix:** (1) Run `_run_quality_check` on the PRE-filter (raw) chunk, not the post-filter result. (2) Either implement `set_sample_rate(self, sr)` and a `sample_rate` property on AudioProcessor that rebuilds the chain at the new rate, OR delete the `force_sr` parameter, the dead `set_sr` branch, and the corresponding tests.

**Severity:** 🔴 High

---

## [PVT-057] — Pre-existing test failures (R3-F6, R3-F14, AUDIO-6 tests)

**Status:** ❌ Not Fixed

**Description:** Several pre-existing test failures identified:
1. `tests/test_i5_retry_fixes.py:810` `TestR3F14DeadListExpressionRemoved.test_no_dead_list_test_peak_history_in_stop_test_recording` — asserts the dead `list(_test_peak_history)` line at `level_monitor.py:607` is ABSENT. The test will FAIL on current source (line still present).
2. `tests/test_i5_retry_fixes.py:736` `test_rate_limited_warning_fires_on_first_drop` (R3-F6) — asserts caplog captures a WARNING mentioning "ring buffer full" or "dropped". Will FAIL because no such log is emitted. The counter `_dropped_level_chunks` was wired but the rate-limited WARNING log was never added.
3. `tests/test_audio_processor.py:287-371` — call `p.set_sample_rate(48000)` and `p.sample_rate` on real AudioProcessor instances — these raise AttributeError (no such method/property).

**Root Cause:** Incomplete fixes from prior rounds; tests added but production code not updated (or vice versa).

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/level_monitor.py:607` (dead expression); 333-350 (missing log)
- `voice_typer/server/audio_processor.py` (missing set_sample_rate)

**Fix:** Fix the production code to match the test contracts: delete dead line 607, add rate-limited log on ring overflow, implement `set_sample_rate`/`sample_rate` on AudioProcessor (or update tests).

**Severity:** 🔴 High

---

## Summary

| Severity | Count | Status |
|----------|-------|--------|
| 🔴 Critical | 2 | ❌ Not Fixed |
| 🔴 High | 38 | ❌ Not Fixed |
| 🟡 Medium | 17 | ❌ Not Fixed |
| 🟢 Low | (many, not all listed) | ❌ Not Fixed |
| **Total listed** | **57** | |

**Spaghetti files identified** (Phase 4.5 candidates):
- `voice_typer/server/recording/recorder.py` (3019 lines) — PVT-006
- `voice_typer/server/service.py` (2657 lines) — PVT-026
- `voice_typer/server/ipc_server.py` (2711 lines) — PVT-027
- `voice_typer/server/app.py` (1317 lines) + `startup_sequence.py` `run()` (497 lines) — PVT-029
- `voice_typer/server/config.py` (1826 lines) — PVT-055
- `voice_typer/server/history_db.py` (1551 lines) — partial concerns (FTS5 dead code, migration logic)
- `voice_typer/server/clipboard.py` (1432 lines) — mostly cohesive but large
- `voice_typer/server/transcription.py` (1298 lines) — mixes DLL plumbing + download + transcription

**Environment**: All findings verified by reading source code on Linux sandbox. Runtime/perf validation pending for fixes.


---


## Session 3 Findings

Verbatim copy of session-3's `comprehensive-review.md`:

# Voice Typer — Comprehensive Review (GROUP 3: UX & UI)

**Session:** IMPROVE mode, Full-Review, GROUP 3 (UX & UI), SUB_AGENT_COUNT=25
**Date:** 2026-07-22
**Platform:** Linux sandbox (Windows/macOS host validation pending)

## Summary

25 parallel review sub-agents investigated GROUP 3 categories: UX/UI consistency, Ease of use, Accessibility, User onboarding, User flows, Developer experience, and Localization/i18n. Total findings: **~350+** across all severity levels.

### Critical Findings (P0 — must fix)

1. **[PVT-001]** 7 of 9 non-default themes have missing CSS tokens in light mode (untested by parity test) — Sub-agent 1
2. **[PVT-002]** `--destructive-foreground` has no stylesheet default; missing from 7 themes — destructive button text unreadable — Sub-agent 1
4. **[PVT-005]** Onboarding Done step shows raw i18n key strings (`onboarding.doneHotkey` etc.) instead of labels — Sub-agent 13
5. **[PVT-006]** Onboarding auto-heal defeats genuine first-run wizard after interruption — Sub-agent 13
8. **[PVT-008]** "Test hotkey" button gives false-positive success masking denied OS permission — Sub-agent 15
9. **[PVT-009]** `installGlobalErrorHandlers()` never wired in main.tsx — async errors silently swallowed — Sub-agent 19
10. **[PVT-010]** Connecting screen uses broken i18n keys (`app.startingPythonStep` etc.) — first impression broken — Sub-agent 19
11. **[PVT-011]** README architecture tree references 5 stale file paths — Sub-agent 22
12. **[PVT-012]** README internal contradiction on terminal paste behavior — Sub-agent 22
13. **[PVT-013]** 29 of 45 i18n completeness tests failing on main — Sub-agent 23
14. **[PVT-014]** 32 keys missing from EVERY non-English locale — Sub-agent 23
15. **[PVT-015]** `apply_translations.py` silently drops translations when parent namespace absent — Sub-agent 23
16. **[PVT-016]** Ratchet test misclassifies missing keys as "fully translated" — Sub-agent 23

### High Findings (P1)

17. **[PVT-017]** Bubble window doesn't receive theme presets — Sub-agent 1
18. **[PVT-019]** AccordionTrigger has no visible focus indicator — Sub-agent 2
19. **[PVT-020]** NumberInputStepper steppers inaccessible to keyboard/touch — Sub-agent 2
20. **[PVT-021]** Sidebar nav is flat list, not documented 3-group hierarchy — Sub-agent 3
21. **[PVT-022]** TitleBar close button uses hardcoded `#C42B1C` instead of destructive tokens — Sub-agent 3
22. **[PVT-023]** Missing `aria-keyshortcuts` on TitleBar buttons and Sidebar nav — Sub-agent 3
23. **[PVT-024]** Duplicate `useSnackbar.ts` and `useSnackbar.tsx` files — Sub-agent 4
24. **[PVT-025]** Spinner size prop silently broken for non-{16,20,24} values — Sub-agent 4
25. **[PVT-026]** Toast duration inconsistent (3000/4000/6000/8000ms) — Sub-agent 4
26. **[PVT-027]** Sonner Toaster missing richColors, closeButton, position, duration — Sub-agent 4
27. **[PVT-028]** Settings.tsx is 1125-line spaghetti — Sub-agent 5
28. **[PVT-029]** Search auto-switch is hint-based, not label-based — Sub-agent 5
29. **[PVT-030]** Search hints untranslated for 5 of 8 locales — Sub-agent 5
30. **[PVT-031]** lib/utils/models.ts is dead code; half-finished extraction — Sub-agent 6
31. **[PVT-032]** No retry button on download failure — Sub-agent 6
33. **[PVT-033]** No disk-space check before download; no storage-path disclosure — Sub-agent 6
34. **[PVT-034]** No way to revert to "System Default" microphone — Sub-agent 7
35. **[PVT-035]** Active mic hot-swap silently swallowed; UI shows "Unknown" — Sub-agent 7
36. **[PVT-036]** No OS-level microphone permission handling — Sub-agent 7
37. **[PVT-037]** Level polling deadlocks: micMonitoring never becomes true — Sub-agent 7
38. **[PVT-038]** Skip link target `<main>` has no `tabindex="-1"` — focus doesn't move — Sub-agent 9
39. **[PVT-039]** LiveQualityFeedback timer inside aria-live region re-announces every second — Sub-agent 10
40. **[PVT-040]** Home page has no top-level heading (no `<h1>`) — Sub-agent 10
41. **[PVT-041]** Default light `--muted-foreground` fails 4.5:1 AA for placeholder text — Sub-agent 11
42. **[PVT-042]** RangeSlider thumb hardcoded `bg-white border-0`, invisible on light tracks — Sub-agent 11
43. **[PVT-043]** Custom theme editor has NO contrast validation — Sub-agent 11
44. **[PVT-044]** Light-mode `--ring` + `focus-visible:ring-ring/30` produce focus indicators below 3:1 — Sub-agent 11
45. **[PVT-045]** Amoled light `--ring` has `/0.5` alpha compounding with `/30` = 15% opacity — Sub-agent 11
46. **[PVT-046]** axe-core.test.tsx: 5 of 9 promised pages untested — Sub-agent 12
47. **[PVT-047]** Home.tsx transcription result not in any live region — Sub-agent 12
48. **[PVT-048]** Bubble keyboard-move handler is dead code in production — Sub-agent 12
49. **[PVT-049]** accessibility.test.tsx "All Switch components" test misses 28 of 29 Switches — Sub-agent 12
50. **[PVT-050]** Onboarding Done step i18n keys missing — Sub-agent 13
51. **[PVT-051]** Onboarding Permissions step i18n missing for 7 locales — Sub-agent 13
52. **[PVT-052]** Onboarding backend instruction strings hardcoded English — Sub-agent 13
53. **[PVT-053]** Onboarding.tsx is 665-line spaghetti — Sub-agent 13
54. **[PVT-054]** Punctuation cheat sheet unreachable while dictating — Sub-agent 14
55. **[PVT-055]** FEATURES.md documents onboarding as 5-step (actually 6-step) — Sub-agent 14
56. **[PVT-056]** README/FEATURES omit help overlay, `?` shortcut, cheat sheet — Sub-agent 14
57. **[PVT-057]** No button to open OS permission settings in onboarding — Sub-agent 15
58. **[PVT-058]** No "Re-check permission" affordance after granting — Sub-agent 15
59. **[PVT-059]** IPC failure during permission probe renders misleading "No extra permission needed" — Sub-agent 15
60. **[PVT-060]** Autostart toggle has no failure feedback; silent registration failure — Sub-agent 15
61. **[PVT-061]** Microphone OS permission never probed in onboarding — Sub-agent 15
62. **[PVT-062]** Home.tsx is 856-line spaghetti — Sub-agent 16
63. **[PVT-063]** No interim/partial transcription display; `transcription_partial` event is dead — Sub-agent 16
64. **[PVT-064]** Recording errors not surfaced in-app; `lastError` never rendered — Sub-agent 16
65. **[PVT-065]** Mic button enabled during `transcribing` but clicks silently swallowed — Sub-agent 16
66. **[PVT-066]** DICTATION_KEY_PRESETS reintroduces `<shift>` hazard — Sub-agent 17
67. **[PVT-067]** Bubble.tsx is 671-line spaghetti — Sub-agent 18
68. **[PVT-068]** Bubble position NOT remembered across show/hide cycles — Sub-agent 18
69. **[PVT-069]** Error message i18n keys reused as button labels (`home.undo` etc.) — Sub-agent 19
70. **[PVT-070]** `lastError` string never rendered on Home page — Sub-agent 19
71. **[PVT-071]** `flushPendingUpdates` resolves all promises even on failure — contradictory toasts — Sub-agent 19
72. **[PVT-072]** Inconsistent event-handler naming across component library — Sub-agent 20
73. **[PVT-073]** Prop type interfaces not exported for consumers — Sub-agent 20
74. **[PVT-074]** Composite components silently drop unknown props (no `...rest`) — Sub-agent 20
75. **[PVT-075]** vitest.config.ts aliases reference non-existent barrel files — Sub-agent 21
76. **[PVT-076]** No shared test-utils; ~3500 lines of mock boilerplate duplicated — Sub-agent 21
77. **[PVT-077]** CONTRIBUTING.md IPC parity section points to wrong file/lines — Sub-agent 22
78. **[PVT-078]** ADR index missing ADR 0020; ADR 0002/0003 duplicate titles — Sub-agent 22
79. **[PVT-079]** debugging.md references non-existent `voice_typer.server.ipc.main` — Sub-agent 22
80. **[PVT-080]** 9 stale extra keys in every non-en locale — Sub-agent 23
81. **[PVT-081]** ~42 untranslated English values per locale outside any allowlist — Sub-agent 23
82. **[PVT-082]** No pluralization support in i18n.ts — Sub-agent 23
83. **[PVT-083]** No browser/OS language auto-detection — Sub-agent 23
84. **[PVT-084]** Sidebar active accent uses physical `before:left-0` (broken in RTL) — Sub-agent 24
85. **[PVT-085]** Main content uses physical `rounded-l-xl … border-r-0` (broken in RTL) — Sub-agent 24
86. **[PVT-086]** SearchField uses physical `left-3`/`right-3`/`pl-9` (broken in RTL) — Sub-agent 24
87. **[PVT-087]** History.tsx hardcoded `chars` + unlocalised `.toLocaleString()` — Sub-agent 25
88. **[PVT-088]** Templates/Vocabulary hardcoded English placeholders — Sub-agent 25
89. **[PVT-089]** About.tsx `formatBytes` hardcoded "MB"/"GB" + `.toFixed()` — Sub-agent 25
90. **[PVT-090]** Dashboard `formatDuration`/`compactNumber` hardcoded English — Sub-agent 25
91. **[PVT-091]** Models `formatVram` hardcoded (duplicated) — Sub-agent 25

### Medium Findings (P2)

92-200+. See per-agent findings in worklog. Key themes:
- Theme consistency issues (custom theme editor, FOUC, cache invalidation)
- Component library inconsistency (focus rings, disabled states, outline-none vs outline-hidden, dead CSS selectors)
- Settings UX (PrewarmAndUpdates ignores search, AudioFilterChain not filtered, save indicator misleading, no per-section reset, validation gaps)
- Models UX (no disk space, no hash verification, parakeet deps dead end, dead _initialLoading)
- Microphone UX (preset descriptions useless, notch filter no frequency slider, dead NoiseFilterRow, compressor/gate advanced controls missing, RangeSlider no aria-valuetext)
- List pages (no search on Templates, no import/export, vocabulary categories hidden, load error missing on Vocabulary, Dashboard silent refresh failure, no empty state, export truncation, no bulk ops)
- Accessibility (keyboard nav, ARIA roles, color contrast, screen reader tests, RTL)
- Onboarding (skip confirmation, i18n gaps, hotkey default mismatch, wizard state not persisted)
- Error handling (connection health check too slow, no connection toasts, ErrorBoundary limited recovery)
- Developer experience (import path inconsistency, export styles, NumberInputStepper onInvalid collision, Modal API divergence, missing storybook stories)
- Test infrastructure (no coverage thresholds, test naming inconsistent, rw0/rw1 legacy, conftest suppress)
- Documentation (stale paths, line counts, missing screenshots, Discord link dead)
- i18n (600KB locale bundle, overlapping scripts, anti-ratchet, placeholder parity, key naming)
- Hardcoded strings (locale formatting, backend error passthrough, plural support)

### Low Findings (P3)

200+. Polish items, dead code, minor inconsistencies, documentation gaps.

---

## Fix Plan

Fix sub-agents are assigned by DISJOINT FILE OWNERSHIP to prevent merge conflicts:

| Agent | File Scope | Key Fixes |
|-------|-----------|-----------|
| 1 | `themes/*.ts` + `themes.ts` + `index.css` | PVT-001, 002, 041, 044, 045 + theme token parity |
| 2 | `components/ui/*.tsx` | PVT-019, 020 + focus rings, imports, types, outline-hidden |
| 3 | `components/layout/Sidebar.tsx` + `TitleBar.tsx` + `Logo.tsx` | PVT-021, 022, 023, 084 + RTL, nav grouping |
| 4 | `components/feedback/*.tsx` + `sonner.tsx` + `useSnackbar.*` | PVT-024, 025, 026, 027 + EmptyState variant, audio quality helper |
| 5 | `pages/Settings.tsx` + `components/settings/*.tsx` | PVT-028, 029, 030 + spaghetti split, search, validation |
| 6 | `pages/Models.tsx` + `components/models/*` + `lib/utils/models.ts` | 031, 032, 033 + spaghetti split |
| 7 | `pages/Microphone.tsx` + `components/microphone/*` + `audio/*` | PVT-034, 035, 036, 037 + spaghetti split |
| 8 | `pages/History.tsx` + `Templates.tsx` + `Vocabulary.tsx` + `Dashboard.tsx` + `dashboard/*` | PVT-087, 088 + search, import/export, empty states |
| 9 | `App.tsx` + `hooks/useNavigation.ts` + `stores/appStore.ts` + `main.tsx` | PVT-009, 010, 038, 085 + skip link, connecting screen |
| 10 | `components/common/*.tsx` | PVT-072, 073, 074, 086 + API consistency, RTL |
| 11 | `pages/Home.tsx` + `hooks/usePython.ts` + `useConnection.ts` + `useSoundFeedback.ts` + `lib/sound-manager.ts` + `lib/globalErrorHandler.ts` | PVT-062, 063, 064, 065, 069, 070, 071 + recording flow |
| 12 | `Bubble.tsx` + `bubble-main.tsx` + `src/main/windows/bubble-window.ts` + `bubble-handlers.ts` + `bubble-bridge-shared.ts` + `preload/bubble.ts` | PVT-017, 048, 067, 068 + bubble spaghetti, position persistence |
| 13 | `pages/Onboarding.tsx` + `server/onboarding.py` + `handlers/onboarding_handlers.py` | PVT-005, 006, 007, 050, 051, 052, 053 + onboarding fixes |
| 14 | `components/hotkey/*` + `RecordingSettingsSection.tsx` | PVT-066 + hotkey config fixes |
| 15 | `i18n/i18n.ts` + `translations/*.json` + `rtl.test.*` + `scripts/*i18n*.py` + `test_i18n_completeness.py` | PVT-013, 014, 015, 016, 080, 081, 082, 083 + i18n system |
| 16 | `a11y/*.test.tsx` + `__tests__/*.test.tsx` + `components/__tests__/*` | 046, 047, 048, 049 + a11y test coverage |
| 17 | `pages/__tests__/*` + `hooks/__tests__/*` + `rw0-rewrite/*` + `rw1-rewrite/*` | Test consolidation, coverage |
| 18 | `test-setup.ts` + `vitest.config.ts` + `vite.config.ts` + `electron.vite.*.ts` + `tsconfig.*.json` + `biome.json` + `conftest.py` | PVT-075, 076 + test infra |
| 19 | `README.md` + `CONTRIBUTING.md` + `FEATURES.md` + `docs/*.md` + `docs/adr/*.md` | PVT-011, 012, 055, 056, 077, 078, 079 + docs refresh |
| 20 | `lib/format.ts` (new) + `lib/utils/models.ts` + `lib/color-utils.ts` + `lib/utils.ts` | PVT-089, 090, 091 + shared format utilities |
| 21 | `components/help/PunctuationCheatSheet.tsx` + `pages/About.tsx` | PVT-054 + help, about credits |
| 22 | `server/permissions.py` + `server_platform/autostart*.py` + `startup_tasks.py` + `config_applier.py` | PVT-008, 057, 058, 059, 060, 061 + permissions UX |
| 23 | `hooks/useTheme.ts` + `ThemeSettingsSection.tsx` + `themeColorCache.ts` + `ThemeSwitch.tsx` | Theme UI, contrast validation, FOUC |
| 24 | `types/ipc.ts` + `types/config.ts` + `types/stats.ts` | Type safety, IPC contracts |
| 25 | `branding.ts` (both) + `stores/appStore.ts` + `hooks/useLastUpdated.ts` + `useStatsShare.ts` | Shared hooks, state, branding sync |


---

## Merge-Stage Findings

The merge stage itself identified the following new findings (NOT present in any
session's original review, but discovered during the intelligent sub-agent merge):

## [PVT-MERGE-007] — SVC-11 "save in finally" contract replaced by G4-H-12 rollback

**Status:** ⚠️ Partial

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
- `tests/test_history_and_models.py`

**Fix:** If the SVC-11 contract is still desired, add a `try/finally` around
`apply_config_side_effects` in `config_applier.apply_config` that calls
`save_strict()` in the finally. The G4-H-12 rollback would still apply if
`save_strict()` itself raises.

**Severity:** 🟡 Medium

---

## [PVT-MERGE-008] — cargo check cannot run in sandbox (missing GTK dev headers)

**Status:** ❌ Pending

**Description:** The Linux sandbox does not have `libgtk-3-dev`,
`libwebkit2gtk-4.1-dev`, `libxdo-dev`, or `libdbus-1-dev` installed, so
`cargo check` fails at the `gdk-sys` build script (`pkg-config` cannot
find `gdk-3.0.pc`). This is an environment limitation, NOT a code issue.

**Progress:** SB sub-agent independently verified that `cargo check` passes
with deps available (their report: "cargo check — 0 errors, 0 warnings").
The full `cargo test --no-run` fails only at the link step due to missing
`.so` dev symlinks for gtk-3/webkit2gtk-4.1/xdo/dbus-1/atk-1.0/soup-3.0/
javascriptcoregtk-4.1.

**Related Files:**
- `src-tauri/` (entire Rust crate)

**Fix:** VALIDATE ON LINUX HOST with:
`apt install libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev libdbus-1-dev`

**Severity:** 🟢 Low (environment-only; no code change needed)

---

## [PVT-MERGE-009] — Duplicate _pick_available_port and _RateLimiter definitions

**Status:** ⚠️ Partial

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
- `voice_typer/server/ipc/rate_limiter.py`

**Fix:** Delete the inline definitions from `ipc_server.py` and import from
`ipc/transport.py` / `ipc/rate_limiter.py` instead.

**Severity:** 🟡 Medium

---

## [PVT-MERGE-010] — 42 pre-existing test failures on BASE

**Status:** ❌ Pending

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
- `tests/test_i5_retry_fixes.py` (18 failures — full file fails on env issues)

**Fix:** Each pre-existing failure needs individual diagnosis. Most are
environment-related (torch not installed → ASR tests fail; pyrnnoise not
installed → audio filter tests skipped/fail; etc.). The base repo's CI
presumably runs with all deps installed.

**Severity:** 🟡 Medium (pre-existing; not a merge regression)

# Consolidated Comprehensive Review - All Sessions

## Session Findings

### Findings from Session 1

# Comprehensive Review — Session EC (GROUP 1: Architecture & Code Quality)

**Session:** EC (Full-Review mode, GROUP 1)
**Date:** 2026-07-23
**Sandbox OS:** Linux. Windows/macOS validation pending real-host handoff.

## Summary

20 parallel review sub-agents investigated GROUP 1 (Architecture & Code Quality) categories: overall architecture, backend architecture, frontend architecture, code quality, maintainability, refactoring opportunities, and spaghetti/monolith detection. 100+ findings were returned, deduplicated into the entries below.

---

## [EC-1] — `recorder.py` is a 2835-line god class mixing 10+ concerns

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Spaghetti / monolith detection + Backend architecture

**Description:** `voice_typer/server/recording/recorder.py` (2835 lines) contains a single `Recorder` class with 45+ methods spanning session lifecycle, audio worker threads, IPC event worker, real-time audio callback dispatch, 442-line `_process_audio_chunk` pipeline, VAD integration, device disconnect detection, device-resolution delegation, buffer/cache management, and 27 property shims existing purely for test monkeypatch compatibility. A prior Phase 4.5 split extracted leaf modules (`device_manager.py`, `resampling.py`, `buffer.py`, `_recorder_split.py`) but left `Recorder` as the central conductor with 1-line delegators — the god class was never decomposed.

**Root Cause:** The split extracted leaf modules but kept `Recorder` as the central conductor. `_recorder_split.py` documents the full plan as a TODO but only moved 2 of 7 planned methods.

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/__init__.py` (431 lines of boilerplate)
- `voice_typer/server/recording/_recorder_split.py`

**Fix:** Use MIXIN pattern to preserve the single `Recorder` class shape and all 27 test-patch property shims. Split into: `_recorder_lifecycle.py` (start/stop/discard/snapshot/_teardown_stream), `_recorder_audio_worker.py` (worker thread loops), `_recorder_chunk_pipeline.py` (_process_audio_chunk + helpers), `_recorder_vad_shims.py` (18 VAD property shims + VAD delegators), `_recorder_device_shims.py` (9 device property shims + device delegators). Keep `recorder.py` as thin shell (~300 lines) with `class Recorder(_RecorderLifecycle, _RecorderAudioWorker, _RecorderChunkPipeline, _RecorderVadShims, _RecorderDeviceShims, object)`. Preserve all `# noqa: F401` re-exports and `inspect.getsource(Recorder.X)` test contracts.

---

## [EC-2] — `service.py` is a 2818-line god class with 75 methods across 14 domains

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Spaghetti / monolith detection + Backend architecture

**Description:** `voice_typer/server/service.py` (2818 lines) contains `VoiceTyperService` with 75 methods spanning status, dictation, history, microphones, level monitor, lifecycle, templates, volume/model status, vocabulary, config side effects, onboarding (15 methods), model import, model download (470-line `download_model`), diagnostics, and GDPR/privacy (226-line `delete_all_personal_data`). The class docstring claims "thin facade" but ~60% is domain logic. 6 TypedDicts are mixed into the same namespace.

**Root Cause:** Each new feature added methods to the only service class. `ConfigApplier` was extracted but no other domain was split.

**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/providers.py` (ServiceProtocol with ~50 methods)

**Fix:** Use MIXIN pattern. Split into: `service_types.py` (TypedDicts), `_service_downloads.py` (download_model + cancel/pause/resume + per-download registry), `_service_models.py` (model status/import/delete/test_llm), `_service_onboarding.py` (15 onboarding methods), `_service_microphones.py` (mic enumeration + test + level monitor), `_service_privacy.py` (GDPR delete/export/reset + diagnostics), `_service_templates.py`, `_service_config.py`, `_service_vocabulary.py`, `_service_status.py`. Keep `service.py` as thin shell with `class VoiceTyperService(...mixins..., object)`. Re-export TypedDicts via `# noqa: F401`.

---

## [EC-3] — `relaunch_app` vs `relaunch_electron` event name drift (Critical IPC bug)

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Overall architecture (IPC)

**Description:** Python emits `{"type": "relaunch_app"}` (app.py:1041) but Electron's `handle-message.ts:105` still listens for `msg.type === "relaunch_electron"`. The Tauri side was updated (main.rs:246, ws.rs removed the rename arm) but the Electron side was NEVER updated. The event-driven restart path is silently broken on Electron — only working via a fallback exit-code-0 path.

**Root Cause:** PVT-2 renamed the wire event on the Python+Tauri side but the Electron listener was not updated.

**Related Files:**
- `voice_typer/server/app.py:1041` (emits `relaunch_app`)
- `voice_typer/client/src/main/python/handle-message.ts:105` (listens for `relaunch_electron`)
- `voice_typer/client/src/renderer/src/types/ipc.ts:317` (phantom `RelaunchElectronEvent`)
- `voice_typer/client/src/main/python/relaunch-app.ts`, `start-python.ts` (stale comments)
- `voice_typer/server/event_bus.py:73` (stale docstring)

**Fix:** Update `handle-message.ts:105` to match `msg.type === "relaunch_app"`. Update `ipc.ts:317` to `RelaunchAppEvent { type: "relaunch_app" }`. Update stale comments in `relaunch-app.ts`, `start-python.ts`, `event_bus.py`. Add a contract test asserting emitter name matches listener name.

---

## [EC-4] — ALLOWED_COMMANDS allowlist hand-mirrored 3 times with documented drift

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Overall architecture (cross-layer DRY)

**Description:** The IPC command allowlist is independently declared in Python `_COMMAND_REGISTRY` (~77 entries), TS `allowed-commands.ts` (~73 entries), and Rust `sidecar_cmds.rs` (~73 entries). Historical drift is documented in comments ("ERR-IPC-002 fix: previously missing quit_app and restart_app"). A parity test exists but only checks COUNT, not exact membership.

**Root Cause:** Independent re-declaration per language with no codegen or shared source.

**Related Files:**
- `voice_typer/server/ipc_server.py:1655` (_COMMAND_REGISTRY)
- `voice_typer/client/src/main/allowed-commands.ts:50` (ALLOWED_COMMANDS)
- `src-tauri/src/commands/sidecar_cmds.rs:90` (ALLOWED_COMMANDS)
- `tests/test_security_doc_command_count.py` (count-only parity test)

**Fix:** Add a parity test that asserts EXACT membership across all three layers (not just count). Reconcile any missing entries. Long-term: generate TS/Rust from Python at build time.

---

## [EC-5] — DEFAULT_CONFIG test fixture drifted 40+ fields from Python Config defaults

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Overall architecture (cross-layer DRY)

**Description:** `fixtures.ts` DEFAULT_CONFIG is massively drifted from Python `Config` dataclass — 40+ of ~90 fields have wrong values (hotkey, device, beam_size, streaming_transcription, autostart, fast_startup, repaste_hotkey, auto_punctuation, llm_preset, waveform_bubble, bubble_position, history_retention_days, etc.). Every renderer test depending on `makeConfig()` tests against a phantom config that never exists in production.

**Root Cause:** Independent re-declaration with no automated sync. "Keep in sync" comments are not enforced.

**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/helpers/fixtures.ts:32` (DEFAULT_CONFIG)
- `voice_typer/server/config.py:554` (Config dataclass defaults)
- `voice_typer/server/service.py:356` (get_defaults IPC command exists)

**Fix:** Wire a test bootstrap that calls `get_defaults` IPC, OR generate `fixtures.ts` at build time from `python -m voice_typer.server.config --dump-defaults-json`. Add CI assertion that generated file matches committed one.

---

## [EC-6] — Windows per-platform Tauri config silently ignored (cross-platform binary bloat)

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical
**Category:** Overall architecture (dual-runtime migration)

**Description:** `tauri.windows-x86_64.conf.json` uses an arch-suffixed name that Tauri v2 does NOT auto-load (only `tauri.<platform>.conf.json` is auto-loaded). Windows CI runs `cargo tauri build` with NO `--config` flag, so the per-platform file is silently ignored. Every Windows installer bundles ~5 unnecessary prewarm binaries + 2 unnecessary native binaries (macOS + Linux variants).

**Root Cause:** The Windows per-platform config was named with an arch suffix Tauri v2 doesn't recognize, and CI was never wired to pass `--config`.

**Related Files:**
- `src-tauri/tauri.windows-x86_64.conf.json` (wrong name)
- `src-tauri/tauri.conf.json:62-76` (base config lists ALL 9 prewarm binaries)
- `.github/workflows/tauri-windows-build.yml:263` (no --config flag)
- `scripts/build/build_tauri_all.sh:200` (misleading comment)

**Fix:** Rename to `tauri.windows.conf.json` (auto-loaded) AND/OR add `--config` flag to Windows CI. Add post-build assertion that bundle does NOT contain non-Windows binaries.

---

## [EC-7] — `app.py` (1319 lines) mixes entry/wiring with 5 inline logic blobs

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

**Description:** `voice_typer/server/app.py` is the main orchestrator but contains ~573 lines of inline business logic: `restart_app` (165 lines), `_open_config_file` (117 lines), `quit_app` (48 lines), `_wait_for_relaunch_ack` (65 lines), `repaste_last` (74 lines), `undo_last` (79 lines). Test contracts pin `inspect.getsource(VoiceTyperApp._open_config_file)`.

**Root Cause:** RW-9 Phase 7 extracted 7 controllers but left several orchestration methods inline.

**Related Files:** `voice_typer/server/app.py`

**Fix:** Extract `app_restart_controller.py` (restart_app + _wait_for_relaunch_ack), `app_quit_controller.py` (quit_app), `repaste_controller.py` (repaste_last + undo_last). Push `_open_config_file` platform branches into `platform_launch.py` (keep method on VoiceTyperApp for `inspect.getsource` test). Keep thin delegates on VoiceTyperApp.

---

## [EC-8] — `ipc_server.py` main() is a 445-line god function with duplicated diagnostic blocks

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection + Backend architecture

**Description:** `ipc_server.py:main()` (445 lines) mixes CLI parsing, single-instance locking, app construction, transport dispatch, and startup-error diagnostics. Two near-identical diagnostic-fallback blocks (~70 lines each) are copy-pasted for construction failure and app.start() failure.

**Root Cause:** Each new feature (Tauri WS, standalone Electron, PII redaction, /tmp fallback) was added inline.

**Related Files:** `voice_typer/server/ipc_server.py:2141-2586`

**Fix:** Extract `ipc_diagnostics.py` (write_startup_diagnostic helper — one source for both call sites). Extract `ipc_cli.py` (parse_ipc_args, validate_args, dispatch_transport). `main()` shrinks to ~30 lines. Update `inspect.getsource` test.

---

## [EC-9] — WS `shutdown` command bypasses _COMMAND_REGISTRY and service layer

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Backend architecture + Overall architecture (IPC)

**Description:** `sidecar_ws.py:322-344` intercepts `shutdown` BEFORE dispatch, calling `server.app.quit()` directly. TCP path's `quit_app` goes through `service.quit()`. The service layer is bypassed on WS — any future shutdown side-effect added to `service.quit()` silently won't run on Tauri. `shutdown` is NOT in `_COMMAND_REGISTRY`.

**Root Cause:** WS path needed cooperative shutdown (ADR-0020 §10) but the command was never registered in the shared dispatch table.

**Related Files:**
- `voice_typer/server/sidecar_ws.py:322-344`
- `voice_typer/server/ipc_server.py:1655` (_COMMAND_REGISTRY — no `shutdown` entry)

**Fix:** Add `"shutdown": "_handle_shutdown"` to `_COMMAND_REGISTRY`. Implement `_handle_shutdown` calling `self.service.quit()`. Remove the special-case branch from `sidecar_ws.py`.

---

## [EC-10] — Error code drift: legacy vs namespaced forms across 3 layers

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Overall architecture (IPC) + cross-layer DRY

**Description:** G4-M-22 introduced namespaced error codes (`server.internal_error`, `client.invalid_payload`) but migration was partial. `_respond_with_error` still stamps legacy `"internal_error"`. TCP emits `"shutting_down"`, WS emits `"server.shutting_down"`. ERROR_CODES registry lists 9 namespaced codes but 15+ legacy codes are actively emitted and NOT registered. Renderer's `ErrorEvent.code` is bare `string` — no narrowing.

**Root Cause:** Namespacing migration was started but never completed in the most-used error paths.

**Related Files:**
- `voice_typer/server/handlers/_base.py:178` (legacy `internal_error`)
- `voice_typer/server/ipc_server.py:1186,1547` (legacy `internal_error`, `shutting_down`)
- `voice_typer/server/sidecar_ws.py:404,316` (legacy `internal_error`, `server.shutting_down`)
- `voice_typer/server/ipc/validation.py:76` (ERROR_CODES registry — incomplete)
- `voice_typer/client/src/renderer/src/types/ipc.ts:82` (ErrorEvent.code is bare string)

**Fix:** Align all emitters to namespaced forms. Update `_respond_with_error` to `"server.internal_error"`. Align TCP `shutting_down` → `server.shutting_down`. Add all actively-emitted codes to ERROR_CODES. Add contract test asserting every emitted `code` is in ERROR_CODES.

---

## [EC-11] — `auth_failed` drift: TCP sends error frame, WS closes with code 1008

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Overall architecture (IPC)

**Description:** TCP path sends `{"type":"error","data":{"code":"auth_failed",...}}` before closing. WS path closes with WS code 1008 and sends NO error frame. Rust client has a dead `auth_failed` match arm that Python WS never sends. Electron has no `auth_failed` handler at all.

**Root Cause:** TCP path was hardened (IPC-5) but WS path was not.

**Related Files:**
- `voice_typer/server/ipc_server.py:962` (TCP — sends error frame)
- `voice_typer/server/sidecar_ws.py:475` (WS — no error frame, just close)
- `src-tauri/src/sidecar/ws.rs:266` (dead `auth_failed` arm)
- `voice_typer/client/src/main/python/handle-message.ts` (no auth_failed handler)

**Fix:** WS path should send `auth_failed` error frame BEFORE `websocket.close(code=1008)`. Add Electron `auth_failed` handler. Add cross-transport parity test.

---

## [EC-12] — Microphone.tsx (1193 lines), Templates.tsx (1069), Vocabulary.tsx (1053), Onboarding.tsx (884), Home.tsx (849) are React monoliths

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

**Description:** Five React page components exceed 800 lines mixing layout + data-fetching + business logic + many inline sub-components with 0 extracted sub-components. Microphone.tsx has 18 useState + 6 useEffect + 9 handlers. Onboarding.tsx has 6 inline step components (PVT-053 refactor was incomplete). Home.tsx has 4 inline sub-components + 1 inline hook.

**Root Cause:** Pages grew organically; prior refactors (PVT-053, PVT-062) converted inline JSX to inline functions but stopped short of file extraction.

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Microphone.tsx`
- `voice_typer/client/src/renderer/src/pages/Templates.tsx`
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx`
- `voice_typer/client/src/renderer/src/pages/Onboarding.tsx`
- `voice_typer/client/src/renderer/src/pages/Home.tsx`

**Fix:** Extract each to a `pages/<name>/` package: `lib/` for helpers, `hooks/use<Name>Lifecycle.ts` for state+IPC, `components/` for sub-components. Keep the page file as thin composition root (~120 lines) with `export default` signature unchanged. Preserves App.tsx routing.

---

## [EC-13] — Tauri bridge missing `logError`/`openElectronLogs` + bubble namespace no window-label split

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Frontend architecture (Tauri bridge)

**Description:** Tauri bridge installs NEITHER `logError` nor `openElectronLogs` (both exist on Electron preload). ErrorBoundary's `componentDidCatch` cannot persist renderer errors under Tauri. Additionally, `createBubbleNamespace` returns the FULL bubble API on BOTH windows — no window-label check. A compromised main renderer on Tauri can invoke `bubble_resize`, `bubble_toggle_dictation` directly, bypassing the bubble-window sandbox (SEC-026 regression).

**Root Cause:** Tauri bridge was never extended for G4-M-69 renderer-error persistence. Bubble namespace has no `windowLabel` parameter.

**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/window-namespace.ts` (missing logError/openElectronLogs)
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts` (no window-label split)
- `voice_typer/client/src/preload/index.ts` (has both)

**Fix:** Add `logError` + `openElectronLogs` to window-namespace.ts (invoke Rust commands). Add `windowLabel` parameter to `createBubbleNamespace`; when `"main"`, return only MainRendererBubble subset. Mirror Electron preload's split.

---

## [EC-14] — FT-1 events (`reconnecting`, `reconnected`) not in PythonPushEvent union (rule #26 violation)

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Frontend architecture + Overall architecture (IPC)

**Description:** Tauri bridge synthesizes `reconnecting`/`reconnected` events and casts via `as unknown as PythonPushEvent`. Neither type is in the `PythonPushEvent` union (27 types). `usePythonEvent("reconnecting", ...)` compiles because `type` param is `string`, not `keyof PythonPushEvent`. Rule #26 (P4: every IPC message must have matching send/receive type definitions) is directly violated.

**Root Cause:** FT-1 events are Tauri-only; the union was never extended to model them.

**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts:73-91` (unsafe casts)
- `voice_typer/client/src/renderer/src/types/ipc.ts:322` (PythonPushEvent union — missing types)
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts:277,287` (subscribes to both)

**Fix:** Add `ReconnectingEvent` and `ReconnectedEvent` to ipc.ts and the union. Widen `RelaunchElectronEvent.type` to include `"relaunch_app"`. Tighten `usePythonEvent`'s `type` param to `keyof PythonPushEvent["type"]`. Remove `as unknown as` casts.

---

## [EC-15] — 478 `except Exception:` blocks across 89 Python files; 37 silent `pass`

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Code quality

**Description:** 478 broad `except Exception:` blocks across 89 files. 37 are silent `pass` (completely discard errors). 36 swallow-and-return default values without logging. `clipboard_target_safety.py` has 9 `return False` from paste-safety checks — fail-open security posture. A dead `_warn_paste_safety_once` helper exists but was never wired in.

**Root Cause:** Project-wide convention to defensively wrap every external call without a logging or re-raise policy.

**Related Files:** `voice_typer/server/clipboard_target_safety.py`, `voice_typer/server/clipboard/windows.py`, `voice_typer/server/clipboard/manager.py`, `voice_typer/server/recording/recorder.py`, `voice_typer/server/service.py`, `voice_typer/server/ipc_server.py`, + 83 more

**Fix:** Wire `_warn_paste_safety_once` into the 9 silent blocks in `clipboard_target_safety.py`. Narrow silent-pass blocks to `except OSError:` + `log.debug(exc_info=True)`. For paste-safety paths, replace `return False` with `log.warning` + `return True` (fail-closed).

---

## [EC-16] — Rust: 10 production `.lock().unwrap()` sites bypass poison-safe `lock()` helper

**Status:** ❌ Not Fixed
**Severity:** 🔴 High
**Category:** Code quality

**Description:** A poison-safe `lock()` helper exists at `state.rs:32` but 10 production sites still use inline `.lock().unwrap()`. A poisoned Mutex re-panics on every subsequent `.lock().unwrap()`, permanently bricking the FT-1 resilience layer. The helper is even marked `#[allow(dead_code)]` (stale — it IS used).

**Root Cause:** Migration was started but never completed.

**Related Files:** `src-tauri/src/sidecar/ws.rs:212`, `src-tauri/src/state.rs:336,362`, `src-tauri/src/main.rs:257,325,347`, `src-tauri/src/commands/sidecar_cmds.rs:332,368,633,690`

**Fix:** Replace all 10 `.lock().unwrap()` with `crate::state::lock(&state.<field>)`. Remove stale `#[allow(dead_code)]`.

---

## [EC-17] — Cross-layer DRY: duplicated helpers across Python modules

**Status:** ❌ Not Fixed
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

**Related Files:** (see description — 20+ files)

**Fix:** Extract shared modules: `_http_safety.py` (_NoRedirectHandler), `asr_utils.py` (release_gpu_memory, _download_with_retry, _cleanup_hf_cache_dir), `_win32_ctypes.py` (typed Win32 wrappers), `asr_errors.py` (ConsentRequiredError), `_retry.py` (retry_with_backoff). Consolidate platform predicates to `platform_utils.py` single source.

---

## [EC-18] — `ws.rs` reconnect_ws is a 590-line god function; bubble.rs duplicates dispatch_frame

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Spaghetti / monolith detection + Code quality

**Description:** `reconnect_ws` (590 lines) performs connect, auth handshake, writer task, reader task, heartbeat task — all inline. `bubble.rs:629-674` duplicates the WS-send pattern from `dispatch_frame` (documented as PVT-25 TODO). FT-1 trigger block duplicated in ws.rs:686 and 703.

**Root Cause:** Functions grew organically; helpers were never extracted.

**Related Files:** `src-tauri/src/sidecar/ws.rs`, `src-tauri/src/commands/bubble.rs`, `src-tauri/src/sidecar/ft1.rs`

**Fix:** Extract `ws_auth_handshake`, `ws_reader_task`, `ws_writer_task`, `ws_heartbeat_supervisor` from reconnect_ws. Extract `dispatch_fire_and_forget` helper for bubble.rs. Extract `trigger_ft1_respawn_off_thread` for the duplicated FT-1 block.

---

## [EC-19] — 155 empty `catch {}` blocks across 39 TS files

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Code quality

**Description:** 155 empty `catch {}` blocks across 39 non-test TS files. Many have no explanatory comment. IPC failures vanish silently — renderer shows stale data instead of error toast. `Home.tsx:458,466,473` has three sequential silent IPC catches.

**Root Cause:** Empty catches are convenient for "best-effort" paths and were applied uniformly.

**Related Files:** `src/renderer/src/pages/Home.tsx`, `src/renderer/src/hooks/useTheme.ts`, `src/main/windows/bubble-window.ts`, `src/main/logging.ts`, + 35 more

**Fix:** Add `logger.warn` or explanatory comment to each empty catch. Adopt lint rule requiring either a comment or a log call inside `catch {}`.

---

## [EC-20] — Page registry scattered across 4 locations; `onboarding` already missing from one

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Frontend architecture

**Description:** The page registry is duplicated 4 times: `Page` union (ipc.ts:24), `KNOWN_PAGES` Set (useNavigation.ts:28), `renderPage()` switch (App.tsx:384), `pageMap` for navigate event (App.tsx:289). The navigate `pageMap` has already drifted — it has 9 entries while the type has 10 (missing `onboarding`). A backend `navigate` event with `path: "onboarding"` hits the else branch and logs a warning.

**Root Cause:** No single source of truth for the route table.

**Related Files:** `types/ipc.ts:24`, `hooks/useNavigation.ts:28`, `App.tsx:289,384`

**Fix:** Introduce a single data-driven route table in `router/routes.ts`. Derive `KNOWN_PAGES`, `renderPage()`, and `pageMap` from it. Add `onboarding` to the navigate handler.

---

## [EC-21] — appStore half-migrated; prop drilling persists despite store

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Frontend architecture

**Description:** `appStore.ts` was created (BACKLOG-004) to eliminate prop drilling but only the `config` slice was migrated. App.tsx still prop-drills `recordingState`, `lastError`, `onNavigate`, `themeMode` into 5 pages. Only Settings.tsx subscribes to the store. `recordingState` and `lastError` ARE in the store but Home.tsx receives them as props.

**Root Cause:** The store migration was abandoned half-done.

**Related Files:** `stores/appStore.ts`, `App.tsx:388-417`, `pages/Home.tsx`, `pages/History.tsx`, `pages/Dashboard.tsx`, `pages/Settings.tsx`

**Fix:** Have `useConnection` write `recordingState`/`lastError` into the store. Replace props in Home/History/Dashboard/Settings with `useAppStore(s => s.recordingState)` etc. Delete page prop interfaces.

---

## [EC-22] — Service.py layering violation: reaches into ipc_server for `_sanitize_config_for_ipc`

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Backend architecture

**Description:** `service.py:336,364` reaches DOWN into the IPC transport layer (`ipc_server.py`) for `_sanitize_config_for_ipc`, creating a real import cycle (ipc_server imports VoiceTyperService from service). The function actually lives in `ipc/history_bounds.py`.

**Root Cause:** Service layer is no longer transport-agnostic.

**Related Files:** `voice_typer/server/service.py:336,364`, `voice_typer/server/ipc_server.py:62`, `voice_typer/server/ipc/history_bounds.py:74`

**Fix:** Move `_sanitize_config_for_ipc` to a transport-neutral module (e.g. `config_sanitizer.py` or `_secrets.py`). Both `service.py` and `ipc_server.py` import from the neutral module.

---

## [EC-23] — Docs drift: ADR-0020 stale line numbers, 73 vs 77 command count, ARCHITECTURE.md removed file paths

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Maintainability

**Description:** Multiple doc drift issues:
1. ADR-0020 has 7 stale line numbers + self-contradictory file-size claims (1,952 vs 2,478 vs actual 2,586)
2. "73-command" appears in 7 docs but actual count is 77 (ARCHITECTURE.md L15 says 77, L26 says 73 — self-contradiction)
3. ARCHITECTURE.md L20 references `clipboard.py` (removed — now `clipboard/manager.py`); L37 references `volume_backends.py` (removed — now `volume_backends/` package)
4. `error-envelope-contract.md` documents stale `{"ok": false, "message": ...}` shape; actual is `{"type":"error","data":{"code":...,"message":...}}`
5. `event_bus.py:73` docstring still lists `relaunch_electron` as canonical (renamed to `relaunch_app`)

**Root Cause:** Docs written against earlier snapshots; code grew but docs weren't refreshed.

**Related Files:** `docs/adr/0020-*.md`, `docs/ARCHITECTURE.md`, `docs/architecture/error-envelope-contract.md`, `voice_typer/server/event_bus.py`, + 5 more docs

**Fix:** Update all "73-command" → "77-command". Update ARCHITECTURE.md file paths. Rewrite error-envelope-contract.md to match actual shape. Update event_bus.py docstring. Strip stale line numbers from ADR-0020 in favor of "locate by name" guidance. Add CI test for doc accuracy.

---

## [EC-24] — Dead code: 9 production methods called only from tests; legacy attributes; stale suppressions

**Status:** ❌ Not Fixed
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

## [EC-25] — Test organization: 12+ catch-all test files mixing unrelated domains

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

**Related Files:** (see description — 15+ test files)

**Fix:** Move each class to its matching domain test file. Delete catch-all files after move. For TS, split catch-all test files into per-component test files.

---

## [EC-26] — 27 silent `if sys.platform` guards in tests (false-green on non-matching platforms)

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Maintainability

**Description:** 27 test sites use `if sys.platform == "win32": <assert>` pattern that returns silently on non-matching platforms — pytest reports PASS, not SKIP. Coverage gaps are hidden. 182 `time.sleep` occurrences with tight timeouts (<1.0s in 8 places) are flaky-test hotspots.

**Root Cause:** Silent guards instead of proper `@pytest.mark.skipif` markers; time-based instead of event-based synchronization.

**Related Files:** `tests/test_clipboard_security.py`, `tests/test_plat_fixes.py`, `tests/tauri/test_prewarm_resolver.py`, + 24 more

**Fix:** Replace every silent `if sys.platform` guard with `@pytest.mark.skipif(sys.platform != X, reason="...")`. Raise tight timeouts to ≥1.0s. Replace time.sleep polling with event-based synchronization where possible.

---

## [EC-27] — `transcription.py` packs 3 concerns; tray.py stale header; clipboard/manager.py packs 5 concerns

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Spaghetti / monolith detection

**Description:**
1. `transcription.py` (1372 lines) packs NVIDIA DLL path management (~155 lines), HuggingFace download helpers (~150 lines), and TranscriptionEngine class — 3 unrelated concerns.
2. `tray.py` (1373 lines) header comment claims "~670 lines" (stale, 2x growth). Packs i18n localization, elapsed timer, Wayland SNI detection + TrayIcon class.
3. `clipboard/manager.py` (1037 lines) packs paste-security validation (200-line `_is_safe_paste_target`), keystroke synthesis, delayed-restore registry, + ClipboardManager class.

**Root Cause:** Modules accumulated concerns; prior partial extractions left residual mixing.

**Related Files:** `voice_typer/server/transcription.py`, `voice_typer/server/tray.py`, `voice_typer/server/clipboard/manager.py`

**Fix:** Extract `cuda_dll_paths.py` + `whisper_download.py` from transcription.py. Extract `tray_i18n.py` + `tray_elapsed_timer.py` + `tray_wayland_detect.py` from tray.py. Extract `clipboard/paste_security.py` + `clipboard/restore_registry.py` from clipboard/manager.py. Preserve re-export shims for test contracts.

---

## [EC-28] — `config.py` and `history_db.py` are large-but-cohesive (NOT monoliths)

**Status:** ✅ Verified — No split needed
**Severity:** 🟢 Low
**Category:** Spaghetti / monolith detection

**Description:** `config.py` (2002 lines) is cohesive (Config schema + load + save + migrate). ~280 lines of extraneous path-safety/lock primitives could be extracted but the file is NOT a monolith. `history_db.py` (1961 lines) is textbook cohesive (thread-safe SQLite for transcription history). `dictation_pipeline.py` (1291 lines) and `model_manager.py` (1130 lines) are also cohesive (already extractions from app.py).

**Fix:** Optional micro-extraction for `config.py` path-safety helpers → `path_safety.py`. No split for history_db.py, dictation_pipeline.py, model_manager.py.

---

## [EC-29] — WindowsNativeHotkey (1473 lines) god class; two parallel hotkey ABCs

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Category:** Backend architecture

**Description:** `hotkeys/windows_native.py` (1473 lines) packs 8 concerns: RegisterHotKey, WH_KEYBOARD_LL hook, WM_HOTKEY message loop, GetAsyncKeyState polling, modifier-only polling (300 lines), Caps Lock suppression, IME detection, AltGr detection. Two parallel ABCs (`HotkeyBackend` + `SubprocessHotkeyBackend`) are bridged by a 501-line `_NativeBackendAdapter`.

**Root Cause:** Phase 4.5 moved the class but didn't decompose it. Import cycle forced two parallel hierarchies.

**Related Files:** `voice_typer/server/hotkeys/windows_native.py`, `voice_typer/server/hotkeys/base.py`, `voice_typer/server/native_hotkeys/base.py`, `voice_typer/server/hotkeys/native_adapter.py`

**Fix:** Decompose WindowsNativeHotkey into `WindowsHotkeyContext` + strategy classes (`PollingDetectionStrategy`, `MessageLoopDetectionStrategy`, `LowLevelHookDetectionStrategy`) + `CapsLockSuppressor` + `ImeCompositionGuard`. Break the ABC cycle by moving shared interface to `hotkeys/_shared.py`. Delete `_NativeBackendAdapter` delegation methods.

---

## [EC-30] — Remaining Medium/Low findings (consolidated)

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium / 🟢 Low
**Category:** Various

**Description:** Consolidated lower-priority findings:
1. `_send()` in ipc_server.py is 295 lines mixing 3 transport write paths (EC-B2)
2. 25 of 67 IPC handlers skip `_validate_dict_payload` (EC-B2)
3. TCP vs WS drift: pending-event cap (1000 vs 256), write timeout (TCP 2s / WS none) (EC-D1)
4. `state_changed` vs `ready` post-auth signal drift (EC-D1)
5. No `protocol_version` on auth frame (EC-D1)
6. Rust ALLOWED_EVENT_TYPES has 7+ phantom entries (EC-D1)
7. Bubble event naming drift: `bubble_level` untranslated on Tauri vs `bubble:level` on Electron (EC-D1)
8. Heartbeat interval drift: TCP 5s/120s vs Tauri 10s/30s (EC-D1)
9. `asr_setup.py` mixes 4 unrelated concerns (EC-B4)
10. `NativeHotkeyRecorder` monkeypatches `backend._handle_line` (EC-B4)
11. Whisper consent gate returns silently; Parakeet raises `ConsentRequiredError` (EC-B4)
12. `ConsentRequiredError` lives in `cloud_engines.py` but local engines import it (EC-B4)
13. `_verify_model_integrity` wrapper swaps arguments (EC-B4)
14. `capture_custom_hotkey` (Win32 polling) duplicates `NativeHotkeyRecorder` (EC-B4)
15. `MODEL_REGISTRY` not authoritative — engines hardcode repo_ids (EC-B4)
16. `_RecordingModule` custom module class + 27 property shims (EC-B3, CR-67)
17. DeviceManager ↔ Recorder circular back-reference (EC-B3)
18. `FilterChain.process` holds lock across all 8 filter calls (EC-B3)
19. `_process_audio_chunk` is 441 lines / 13 concerns (EC-B3)
20. `logging.ts` duplicate loggers + 70 lines defensive require boilerplate (EC-C1)
21. `tcp-connect.ts` 284-line god function (EC-C1)
22. `state.ts` 20-field mutable god-object (EC-C1)
23. `i18n.setMainLocale` dead code — main process stuck at "en" (EC-C1)
24. `relaunch-app.ts` dev/prod duplication (EC-C1)
25. `allowed-commands.ts` unstructured Set (EC-C1)
26. Components/ folder organization inconsistent (EC-C2)
27. Module-level cache helpers duplicated 4x across pages (EC-C2)
28. Preload object literals unchecked against bridge interfaces (EC-C3)
29. `usePython.ts` dead `type:"error"` branch + misleading comment (EC-C3)
30. Tauri deb/rpm `python3` dep contradicts R6-F8 (EC-D3)
31. No Electron deprecation timeline (EC-D3)
32. Mixed-mode runtime marker unimplemented (EC-D3)
33. TAURI_SIDECAR scattered 13x with no central helper (EC-D3)
34. TAURI_SIDECAR self-set creates asymmetric contract (EC-D3)
35. Signing config asymmetric between stacks (EC-D3)
36. ADR "(or equivalent)" vagueness + stale tray-ownership doc (EC-D3)
37. 15 Python complexity hotspots (CC > 35) (EC-E1)
38. `Any` hotspots in asr_registry.py, recorder.py (EC-E1)
39. 12 monkey-patch `# type: ignore` suppressions (EC-E1)
40. 155 empty TS `catch {}` blocks (EC-E2)
41. `reconnect_ws` 590 lines (EC-E2)
42. `ft1_respawn_inner` 187 lines (EC-E2)
43. Late stdlib imports 11x (EC-G1)
44. In-function `re.compile` 6x (EC-G1)
45. Rotating logger constants 3x (EC-G1)
46. Raw `sys.platform` checks 10x (EC-G1)
47. `_secure_atomic_write` late imports 14x (EC-G1)
48. `rate_limiter.py` stale comment (EC-G1)
49. Worker polling pattern duplicated 6x (EC-G1)
50. `_config_dir` 3 indirection layers (EC-G1)
51. migrate.rs (546 lines) and commands/paste.rs (415 lines) have 0 Rust tests (EC-F2)
52. ADR-0013/0020 slug collision (EC-F2)
53. No Rust integration tests directory (EC-F2)

**Fix:** Address the highest-impact items from this list during Phase 4 fix wave. Items 1-14 are IPC/backend architecture fixes; 15-19 are recording pipeline; 20-28 are frontend; 29-36 are dual-runtime; 37-52 are code quality / tests / docs.

---

*End of comprehensive review. Total findings: 30 entries (EC-1 through EC-30), covering 100+ sub-findings from 20 review sub-agents.*


### Findings from Session 2


---

# XV Session — Performance & Resources Review (GROUP 2)

Session: XV (Full-Review mode, GROUP 2 only). Sub-agent wave: SA1-SA20 (20 parallel). All findings scoped to GROUP 2 (Performance, Memory, CPU, Resource footprint, Audio pipeline quality, Scalability, Working-but-suboptimal). Platform: Linux sandbox; Windows/macOS host validation noted per finding where relevant.

Total findings: 163.
Severity breakdown: 🔴 Critical=2, 🔴 High=22, 🟡 Medium=64, 🟢 Low=75.

## [XV-1] — Lazy torch import in service.py loads 500MB-1GB into RSS on every startup

**Status:** ❌ Not Fixed

**Description:** Lazy torch import in service.py loads 500MB-1GB into RSS on every startup. Category: Performance / Memory.

**Root Cause:** verified — should use `importlib.util.find_spec()` which only resolves file path.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/service.py`

**Fix:** Replace both checks with `importlib.util.find_spec(pkg) is not None`.

**Severity:** 🔴 High

## [XV-2] — download_model polls entire HF hub cache every 1s during downloads

**Status:** ❌ Not Fixed

**Description:** download_model polls entire HF hub cache every 1s during downloads. Category: Performance / Resource footprint.

**Root Cause:** verified — `cache_dir` is hub root, not per-model subdir.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/service.py`

**Fix:** Scan only `cache_dir / f"models--{repo_id.replace('/', '--')}"`.

**Severity:** 🟡 Medium

## [XV-3] — _open_config_file blocks tray thread + config lock for entire editor session (Windows)

**Status:** ❌ Not Fixed

**Description:** _open_config_file blocks tray thread + config lock for entire editor session (Windows). Category: Performance / CPU usage.

**Root Cause:** verified — tray menu thread + IPC set_config calls all block.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/platform_launch.py`

**Fix:** Spawn editor detached; reload config via separate trigger.

**Severity:** 🟡 Medium

## [XV-4] — Dead `import numpy as np` in app.py

**Status:** ❌ Not Fixed

**Description:** Dead `import numpy as np` in app.py. Category: Working-but-suboptimal.

**Root Cause:** verified — leftover from RW-9 god-class decomposition.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/app.py`

**Fix:** Delete line 18.

**Severity:** 🟢 Low

## [XV-5] — refresh_microphones cache bypassed on empty PortAudio result

**Status:** ❌ Not Fixed

**Description:** refresh_microphones cache bypassed on empty PortAudio result. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — truthiness guard conflates populated with non-empty.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/service.py`

**Fix:** Use `is not None` instead of truthiness; initialize to None.

**Severity:** 🟢 Low

## [XV-6] — autostart_launcher unconditionally sleeps 2s on every login

**Status:** ❌ Not Fixed

**Description:** autostart_launcher unconditionally sleeps 2s on every login. Category: Performance (startup speed).

**Root Cause:** verified — fixed sleeps; child already detached.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/autostart_launcher.py`

**Fix:** Replace with bounded poll for IPC port readiness.

**Severity:** 🟢 Low

## [XV-7] — shutdown_controller runs 16 sequential teardowns, worst-case ~90s shutdown

**Status:** ❌ Not Fixed

**Description:** shutdown_controller runs 16 sequential teardowns, worst-case ~90s shutdown. Category: Performance (shutdown behavior).

**Root Cause:** verified — no parallelism.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/shutdown_controller.py`

**Fix:** Group independent teardowns into ThreadPoolExecutor with shared 10s deadline.

**Severity:** 🔴 High

## [XV-8] — Electron termination path not wrapped in _run_with_timeout

**Status:** ❌ Not Fixed

**Description:** Electron termination path not wrapped in _run_with_timeout. Category: Performance / Resource footprint.

**Root Cause:** verified — only teardown step not bounded.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/shutdown_controller.py`

**Fix:** Wrap both branches in `_run_with_timeout(timeout=5.0)`; escalate to SIGKILL after 2s on POSIX.

**Severity:** 🟡 Medium

## [XV-9] — crash_recovery `_save_lock` has no timeout; can hang atexit/GC

**Status:** ❌ Not Fixed

**Description:** crash_recovery `_save_lock` has no timeout; can hang atexit/GC. Category: Performance / Memory.

**Root Cause:** verified — no timeout on lock acquisition in shutdown/atexit paths.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/crash_recovery.py`

**Fix:** Use `lock.acquire(timeout=2.0)` in `__del__`/`_atexit_save`/post-join; skip save on timeout.

**Severity:** 🟡 Medium

## [XV-10] — tray.stop() timeout leaks daemon; main thread stays blocked in tray.run()

**Status:** ❌ Not Fixed

**Description:** tray.stop() timeout leaks daemon; main thread stays blocked in tray.run(). Category: Performance (shutdown).

**Root Cause:** suspected — timeout abandons call; main thread has no other unblock signal.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/shutdown_controller.py`

**Fix:** After timeout, fall back to `os._exit(0)` from cleanup thread; or install watchdog.

**Severity:** 🟡 Medium

## [XV-11] — crash_recovery list-based trim O(N); redundant 1s poll loop

**Status:** ❌ Not Fixed

**Description:** crash_recovery list-based trim O(N); redundant 1s poll loop. Category: Working-but-suboptimal.

**Root Cause:** verified — should use `collections.deque(maxlen=10)`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/crash_recovery.py`

**Fix:** Replace list with bounded deque.

**Severity:** 🟡 Medium

## [XV-12] — crash_handler `report_pending_crash` does O(N²) stat calls on startup

**Status:** ❌ Not Fixed

**Description:** crash_handler `report_pending_crash` does O(N²) stat calls on startup. Category: Performance (startup) / Scalability.

**Root Cause:** verified — per-iteration retention enforcement.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/crash_handler.py`

**Fix:** Move `_enforce_archive_retention` out of loop; cache stat results.

**Severity:** 🟡 Medium

## [XV-13] — container_detect / prewarm_resolver re-probe invariant system state per call

**Status:** ❌ Not Fixed

**Description:** container_detect / prewarm_resolver re-probe invariant system state per call. Category: Working-but-suboptimal / Resource footprint.

**Root Cause:** verified — system Identity doesn't change during process lifetime.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/container_detect.py`
- `voice_typer/server/prewarm_resolver.py`

**Fix:** `@functools.lru_cache(maxsize=1)` or module-level cache.

**Severity:** 🟢 Low

## [XV-14] — prewarm `_warm_file` emits one INFO log per file (10K-60K records per run)

**Status:** ❌ Not Fixed

**Description:** prewarm `_warm_file` emits one INFO log per file (10K-60K records per run). Category: Performance / Resource footprint.

**Root Cause:** verified — INFO level in production.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/cache_probe.py`

**Fix:** Demote per-file log to DEBUG; keep per-package summary at INFO.

**Severity:** 🟡 Medium

## [XV-15] — prewarm `_warm_package_files` materializes + sorts entire tree

**Status:** ❌ Not Fixed

**Description:** prewarm `_warm_package_files` materializes + sorts entire tree. Category: Performance / Memory.

**Root Cause:** verified — sort is wasted.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/cache_probe.py`

**Fix:** Iterate `root.rglob("*")` directly or use `os.walk`.

**Severity:** 🟢 Low

## [XV-16] — prewarm warms redundant files + misleading `del chunk`

**Status:** ❌ Not Fixed

**Description:** prewarm warms redundant files + misleading `del chunk`. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — .py not loaded at import time when .pyc present.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/cache_probe.py`

**Fix:** Filter by suffix; delete `del chunk`.

**Severity:** 🟢 Low

## [XV-17] — prewarm macOS wait_for_prewarm forks `ps` up to 60×/call

**Status:** ❌ Not Fixed

**Description:** prewarm macOS wait_for_prewarm forks `ps` up to 60×/call. Category: CPU usage.

**Root Cause:** verified — `model_manager.try_load()` calls this on every app launch.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/process_tracker.py`

**Fix:** Use `os.kill(pid, 0)` for liveness; only cmdline-check once at entry.

**Severity:** 🟡 Medium

## [XV-18] — prewarm get_prewarm_status re-probes every weights file per IPC call

**Status:** ❌ Not Fixed

**Description:** prewarm get_prewarm_status re-probes every weights file per IPC call. Category: Scalability.

**Root Cause:** verified — no caching.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/process_tracker.py`

**Fix:** Memoize with 30s TTL keyed on directory mtime.

**Severity:** 🟢 Low

## [XV-19] — prewarm Config.load + _resolve_hf_cache_dir called multiple times per run

**Status:** ❌ Not Fixed

**Description:** prewarm Config.load + _resolve_hf_cache_dir called multiple times per run. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — repeated disk stats + config parses per prewarm run.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/prewarm/cache_probe.py`
- `voice_typer/server/prewarm/paths.py`

**Fix:** `@lru_cache(maxsize=1)` on `_resolve_hf_cache_dir`.

**Severity:** 🟢 Low

## [XV-20] — CRITICAL: recorder buffer math assumes 1024-sample chunks at 16kHz, actual is 512 samples at native rate

**Status:** ❌ Not Fixed

**Description:** CRITICAL: recorder buffer math assumes 1024-sample chunks at 16kHz, actual is 512 samples at native rate. Category: Audio pipeline quality / Scalability.

**Root Cause:** verified — stale 1024-sample / 16kHz assumption; native rate not used for capacity.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Compute `chunk_seconds = blocksize / effective_sr`; size `needed_chunks = int(max_rec / chunk_seconds) + safety` after `_resolve_effective_sample_rate`.

**Severity:** 🔴 Critical

## [XV-21] — preroll buffer capacity computed with wrong sample rate (config 16kHz vs device 48kHz)

**Status:** ❌ Not Fixed

**Description:** preroll buffer capacity computed with wrong sample rate (config 16kHz vs device 48kHz). Category: Audio pipeline quality.

**Root Cause:** verified — wrong sample rate.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Size the deque inside `start()` after `_effective_sr` is known.

**Severity:** 🔴 High

## [XV-22] — _recorder_split.py O(N) re-copy of cached prefix on every snapshot (~200GB memcpy over 30min session)

**Status:** ❌ Not Fixed

**Description:** _recorder_split.py O(N) re-copy of cached prefix on every snapshot (~200GB memcpy over 30min session). Category: Performance / Memory.

**Root Cause:** verified — only the no-new-chunks case was optimized.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/_recorder_split.py`

**Fix:** Store prefix as list of segments; concatenate lazily when caller needs contiguous array.

**Severity:** 🔴 High

## [XV-23] — `stop()` holds `self._lock` during `np.concatenate(list(self._buffer))` (~50-300ms)

**Status:** ❌ Not Fixed

**Description:** `stop()` holds `self._lock` during `np.concatenate(list(self._buffer))` (~50-300ms). Category: Performance / Audio pipeline quality.

**Root Cause:** verified — audio worker thread blocks on lock for concatenate duration.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Inside lock: `chunks = list(self._buffer); self._buffer = deque(...)`. Release lock; concatenate outside.

**Severity:** 🔴 High

## [XV-24] — preroll buffer not zeroed after prepend (privacy + memory)

**Status:** ❌ Not Fixed

**Description:** preroll buffer not zeroed after prepend (privacy + memory). Category: Memory / Audio pipeline quality.

**Root Cause:** verified — SEC-audit-008 privacy gap; ~preroll_seconds × native_sr × 4 bytes redundant retention.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** After prepend loop, zero-fill + clear `_preroll_buffer`.

**Severity:** 🟡 Medium

## [XV-25] — ring buffer capacity fixed at 64 chunks regardless of sample rate

**Status:** ❌ Not Fixed

**Description:** ring buffer capacity fixed at 64 chunks regardless of sample rate. Category: Audio pipeline quality / Resource footprint.

**Root Cause:** verified — fixed capacity; wrong comment.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Make capacity proportional to `effective_sr / blocksize × 4s`, computed in `start()`.

**Severity:** 🟡 Medium

## [XV-26] — silence timer not reset on device disconnect → auto-stop fires after hot-swap recovery

**Status:** ❌ Not Fixed

**Description:** silence timer not reset on device disconnect → auto-stop fires after hot-swap recovery. Category: Audio pipeline quality.

**Root Cause:** verified — silence state not reset on disconnect path.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Reset `_silence_start_time = None` and `_silence_timer = 0.0` on disconnect recovery.

**Severity:** 🟡 Medium

## [XV-27] — `stop()` racing with `_handle_device_disconnect` can leak stream + zombie callback

**Status:** ❌ Not Fixed

**Description:** `stop()` racing with `_handle_device_disconnect` can leak stream + zombie callback. Category: Audio pipeline quality / Resource footprint.

**Root Cause:** suspected — no re-check before critical reassignment.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Re-check `_captured_generation != self._stop_generation` before `self._stream = stream` under `self._lock`.

**Severity:** 🟡 Medium

## [XV-28] — _prepare_audio ignores `_cached_target_sr` optimization

**Status:** ❌ Not Fixed

**Description:** _prepare_audio ignores `_cached_target_sr` optimization. Category: Working-but-suboptimal.

**Root Cause:** verified — inconsistent application of optimization.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** `target_sr = getattr(self, "_cached_target_sr", None) or self.config.sample_rate`.

**Severity:** 🟢 Low

## [XV-29] — _dropped_chunks and _rms_callback_error_count lazy init, no reset in `start()`

**Status:** ❌ Not Fixed

**Description:** _dropped_chunks and _rms_callback_error_count lazy init, no reset in `start()`. Category: Working-but-suboptimal.

**Root Cause:** verified — fragile for tests; per-session counter semantics broken.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Declare both in `__init__`; reset in `start()`.

**Severity:** 🟢 Low

## [XV-30] — event worker 50ms poll prevents deep C-states on battery

**Status:** ❌ Not Fixed

**Description:** event worker 50ms poll prevents deep C-states on battery. Category: CPU usage / Resource footprint (battery).

**Root Cause:** verified — copy-pasted timeout without considering event worker's lower latency requirement.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording/recorder.py`

**Fix:** Increase event worker timeout to 0.5-1.0s; or use sentinel item pushed on stop.

**Severity:** 🟢 Low

## [XV-31] — CRITICAL: `AudioProcessor.set_sample_rate` missing — filter chain never rebuilt on device rate change

**Status:** ❌ Not Fixed

**Description:** CRITICAL: `AudioProcessor.set_sample_rate` missing — filter chain never rebuilt on device rate change. Category: Audio pipeline quality.

**Root Cause:** verified — `set_sample_rate` was never implemented; dead-fallback logs at DEBUG.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_processor.py`
- `voice_typer/server/audio_quality_controller.py`

**Fix:** Implement `AudioProcessor.set_sample_rate(sr)` that updates `self._sample_rate` and triggers `rebuild_from_config(self._config)`.

**Severity:** 🔴 Critical

## [XV-32] — RNNoise 16k↔48k resample round-trip runs on PortAudio RT thread

**Status:** ❌ Not Fixed

**Description:** RNNoise 16k↔48k resample round-trip runs on PortAudio RT thread. Category: Performance / CPU usage / Audio pipeline quality.

**Root Cause:** verified — RNNoise hard-bound to 48kHz/480-sample frames; chain built at config.sample_rate (16kHz default).

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py`

**Fix:** (a) Build chain at 48kHz natively; resample once at output; OR (b) move round-trip to worker thread; OR (c) pre-allocate polyphase filter via `firwin` + `upfirdn`.

**Severity:** 🔴 High

## [XV-33] — RNNoise round-trip truncates/zero-pads to match input length → audible artifacts

**Status:** ❌ Not Fixed

**Description:** RNNoise round-trip truncates/zero-pads to match input length → audible artifacts. Category: Audio pipeline quality.

**Root Cause:** verified — `resample_poly` output length != input length after round-trip.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py`

**Fix:** Use stateful resampler (`CascadeFilter`/`upfirdn` with carry) so output length exactly matches input across chunks.

**Severity:** 🔴 High

## [XV-34] — `AudioProcessor.process_chunk` calls `resample_poly` on RT thread

**Status:** ❌ Not Fixed

**Description:** `AudioProcessor.process_chunk` calls `resample_poly` on RT thread. Category: Performance / CPU usage.

**Root Cause:** verified — docstring asserts pre-allocated buffers only, but `resample_poly` allocates per call.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_processor.py`

**Fix:** (a) Implement `set_sample_rate` (XV-31); (b) move resample to worker thread; (c) cache polyphase filter coefficients.

**Severity:** 🔴 High

## [XV-35] — Per-sample Python loops in 4 audio filters (NoiseGate, Compressor, Limiter, Equalizer)

**Status:** ❌ Not Fixed

**Description:** Per-sample Python loops in 4 audio filters (NoiseGate, Compressor, Limiter, Equalizer). Category: CPU usage.

**Root Cause:** verified — literal Python port of OBS C loop; CPython boxes floats per iteration.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_filters/noise_gate.py`
- `compressor.py`
- `limiter.py`
- `equalizer.py`

**Fix:** Vectorize with numpy (np.maximum.accumulate, np.where, lfilter) OR use `numba.njit` on existing loops.

**Severity:** 🟡 Medium

## [XV-36] — `rebuild_from_config` reconstructs entire filter chain on every config change

**Status:** ❌ Not Fixed

**Description:** `rebuild_from_config` reconstructs entire filter chain on every config change. Category: Performance.

**Root Cause:** verified — no diff/patch logic; RNNoise model re-loaded per rebuild.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_processor.py`
- `voice_typer/server/audio_chain_builder.py`

**Fix:** Add `reconfigure(params)` per filter; `build_chain` diffs new vs old; cache RNNoise backend at module level.

**Severity:** 🟡 Medium

## [XV-37] — Resample fallback logged at DEBUG (silent filter-chain mistune on non-integer-ratio devices)

**Status:** ❌ Not Fixed

**Description:** Resample fallback logged at DEBUG (silent filter-chain mistune on non-integer-ratio devices). Category: Audio pipeline quality.

**Root Cause:** verified — fallback is intentional but log level hides it.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_processor.py`

**Fix:** Bump log to WARNING (one-shot); surface via degraded flag; use `scipy.signal.resample` (FFT) as fallback for non-integer ratios.

**Severity:** 🟡 Medium

## [XV-38] — RNNoise int16 cast without clip → wraparound on transient peaks

**Status:** ❌ Not Fixed

**Description:** RNNoise int16 cast without clip → wraparound on transient peaks. Category: Audio pipeline quality.

**Root Cause:** suspected — depends on upstream filter output exceeding ±1.0.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_filters/noise_suppressor.py`

**Fix:** `np.clip(frame, -1.0, 1.0)` before cast; or use float32 API if available.

**Severity:** 🟡 Medium

## [XV-39] — highpass/notch `reset()` allocates new `np.zeros` after just zeroing existing array

**Status:** ❌ Not Fixed

**Description:** highpass/notch `reset()` allocates new `np.zeros` after just zeroing existing array. Category: Working-but-suboptimal / Memory.

**Root Cause:** verified — redundant allocation.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_filters/highpass.py`
- `voice_typer/server/audio_filters/notch.py`

**Fix:** Drop the `zi = np.zeros(...)` line; reuse the just-zeroed array.

**Severity:** 🟢 Low

## [XV-40] — `AudioQualityAnalyzer.analyze_chunk` is dead code (controller bypasses it)

**Status:** ❌ Not Fixed

**Description:** `AudioQualityAnalyzer.analyze_chunk` is dead code (controller bypasses it). Category: Working-but-suboptimal.

**Root Cause:** suspected — retained for backward compatibility / external callers.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/audio_quality.py`

**Fix:** Delete if no callers; or update docstring + add `@deprecated`.

**Severity:** 🟢 Low

## [XV-41] — VAD sub-chunking doubles Silero inference cost on audio worker thread

**Status:** ❌ Not Fixed

**Description:** VAD sub-chunking doubles Silero inference cost on audio worker thread. Category: Performance / CPU usage.

**Root Cause:** verified — per-sub-chunk inference + `.item()` host-device sync.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`

**Fix:** Batch sub-chunks into 2D tensor `[num_sub, expected]`; single `model()` call. Silero supports batched input.

**Severity:** 🔴 High

## [XV-42] — `text_cleanup._correct_whisper_phrases` O(N×M) regex search per dictation

**Status:** ❌ Not Fixed

**Description:** `text_cleanup._correct_whisper_phrases` O(N×M) regex search per dictation. Category: Performance / CPU usage.

**Root Cause:** verified — no pre-filter; cache only memoizes pattern compilation, not match decision.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/text_cleanup.py`

**Fix:** Build single combined `re.compile("|".join(re.escape(bad) for bad, _ in phrases))`; use lookup-table callback.

**Severity:** 🟡 Medium

## [XV-43] — VAD model lazy-loaded on first audio chunk → 150-600ms initial dropout

**Status:** ❌ Not Fixed

**Description:** VAD model lazy-loaded on first audio chunk → 150-600ms initial dropout. Category: Performance / Audio pipeline quality.

**Root Cause:** verified — lazy load chosen for import cost, but deferred to first audio chunk instead of off the audio thread.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`

**Fix:** Call `vad._load_model()` eagerly from background thread at startup; expose `vad.preload()` API.

**Severity:** 🔴 High

## [XV-44] — VAD `max(probs)` aggregates sub-chunks → false positives on impulsive noise

**Status:** ❌ Not Fixed

**Description:** VAD `max(probs)` aggregates sub-chunks → false positives on impulsive noise. Category: Audio pipeline quality.

**Root Cause:** verified — OR of per-sub-chunk probabilities with no hysteresis.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`

**Fix:** Use mean of top-2 sub-chunk probabilities, or `sorted(probs)[-2]` when `len >= 2`.

**Severity:** 🟡 Medium

## [XV-45] — VAD zero-pads short chunks → systematically under-reports speech (false negatives)

**Status:** ❌ Not Fixed

**Description:** VAD zero-pads short chunks → systematically under-reports speech (false negatives). Category: Audio pipeline quality.

**Root Cause:** verified — padding with zeros is out-of-distribution for Silero.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`

**Fix:** Pad by reflecting chunk (`audio_tensor.flip(0)`) instead of zeros; or scale prob by `expected/n`.

**Severity:** 🟡 Medium

## [XV-46] — Silero VAD `reset_states()` never called → LSTM state accumulates across sessions

**Status:** ❌ Not Fixed

**Description:** Silero VAD `reset_states()` never called → LSTM state accumulates across sessions. Category: Audio pipeline quality / Scalability.

**Root Cause:** verified — Silero exposes `reset_states()`; codebase never invokes it.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`
- `voice_typer/server/vad_processor.py`

**Fix:** Expose `vad.reset_states()` calling `_model.reset_states()` (guarded by `hasattr`); invoke from `VadProcessor.reset()` or `Recorder.start()`.

**Severity:** 🟡 Medium

## [XV-47] — `vad_processor` grey-zone forced transition cuts off soft-spoken users after ~1s

**Status:** ❌ Not Fixed

**Description:** `vad_processor` grey-zone forced transition cuts off soft-spoken users after ~1s. Category: Audio pipeline quality.

**Root Cause:** verified — bound calibrated for normal-volume speech; soft-spoken users produce speech legitimately hovering in 0.3-0.5 prob band.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad_processor.py`

**Fix:** Make `_grey_zone_hold_limit` configurable; or scale by rolling mean of recent speech probs.

**Severity:** 🟡 Medium

## [XV-48] — `hallucination.py` allowlist too narrow; Tier-1 RMS threshold too strict; dead-code branch

**Status:** ❌ Not Fixed

**Description:** `hallucination.py` allowlist too narrow; Tier-1 RMS threshold too strict; dead-code branch. Category: Audio pipeline quality.

**Root Cause:** verified — narrow allowlist + uncalibrated threshold + dead branch.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hallucination.py`

**Fix:** Add common single-token hallucinations; relax Tier-1 RMS to `< 0.01`; remove dead branch.

**Severity:** 🟡 Medium

## [XV-49] — `torch.hub.load` fallback has no timeout; can block audio worker 30+ seconds on offline machines

**Status:** ❌ Not Fixed

**Description:** `torch.hub.load` fallback has no timeout; can block audio worker 30+ seconds on offline machines. Category: Performance / CPU usage.

**Root Cause:** verified — no timeout parameter; no negative cache.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`

**Fix:** Wrap in `signal.alarm`/`ThreadPoolExecutor` with 5s deadline; negative-cache failure; promote log to WARNING.

**Severity:** 🟢 Low

## [XV-50] — Silero VAD model never unloaded when user disables VAD mid-session

**Status:** ❌ Not Fixed

**Description:** Silero VAD model never unloaded when user disables VAD mid-session. Category: Memory / Resource footprint.

**Root Cause:** verified — one-way ratchet (load → retain forever).

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`
- `voice_typer/server/vad_processor.py`

**Fix:** Add `vad.unload()` setting `_model = None`; wire to `on_config_changed` when VAD transitions to False.

**Severity:** 🟢 Low

## [XV-51] — `vad.is_speech` fallback uses inconsistent thresholds vs `VadProcessor`

**Status:** ❌ Not Fixed

**Description:** `vad.is_speech` fallback uses inconsistent thresholds vs `VadProcessor`. Category: Audio pipeline quality.

**Root Cause:** verified — two independent Silero thresholds + third magic RMS number.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vad.py`

**Fix:** Accept optional `threshold` parameter; eliminate `vad.py:VAD_THRESHOLD` in favor of config-derived value.

**Severity:** 🟢 Low

## [XV-52] — `text_cleanup.clean_transcribed_text` re-tokenizes 4× per call + uncompiled regex

**Status:** ❌ Not Fixed

**Description:** `text_cleanup.clean_transcribed_text` re-tokenizes 4× per call + uncompiled regex. Category: Working-but-suboptimal / Performance.

**Root Cause:** verified — each cleanup function self-contained; no shared tokenization pass.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/text_cleanup.py`

**Fix:** Tokenize once at top; precompile `_RE_TOKEN_MATCH`; replace char walk with `re.sub(r"\bi\b", ...)`.

**Severity:** 🟢 Low

## [XV-53] — Volume `fade_to` runs 10 subprocess steps on Linux/macOS → audible stepping + multi-second latency

**Status:** ❌ Not Fixed

**Description:** Volume `fade_to` runs 10 subprocess steps on Linux/macOS → audible stepping + multi-second latency. Category: Audio pipeline quality / Performance.

**Root Cause:** verified — base class assumes set_linear is in-process; subprocess backends don't override.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/volume_backend_base.py`
- `voice_typer/server/volume_backends/linux.py`
- `voice_typer/server/volume_backends/macos.py`

**Fix:** Override `fade_to` in Linux/macOS backends with single native ramp; OR make adaptive (collapse to 1 step if subprocess-based).

**Severity:** 🔴 High

## [XV-54] — `level_monitor` duplicates test chunks (`_test_chunks` + `_test_raw_chunks` hold identical data)

**Status:** ❌ Not Fixed

**Description:** `level_monitor` duplicates test chunks (`_test_chunks` + `_test_raw_chunks` hold identical data). Category: Memory.

**Root Cause:** verified — _test_chunks is historical artifact; filtering moved to stop_test_recording.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/level_monitor.py`

**Fix:** Store only `_test_raw_chunks`; derive `audio` from `raw_audio.copy()` in `stop_test_recording`.

**Severity:** 🟡 Medium

## [XV-55] — `_process_level_chunk` holds `_monitor_lock` during RNNoise processing (~50ms)

**Status:** ❌ Not Fixed

**Description:** `_process_level_chunk` holds `_monitor_lock` during RNNoise processing (~50ms). Category: CPU usage / Audio pipeline quality.

**Root Cause:** verified — lock covers expensive filter call that only writes locals.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/level_monitor.py`

**Fix:** Move RNNoise + np ops OUTSIDE the lock; acquire lock only for shared-state writes.

**Severity:** 🟡 Medium

## [XV-56] — `_pactl_get` makes 2 sequential subprocess calls (~200ms latency per duck/restore on Linux)

**Status:** ❌ Not Fixed

**Description:** `_pactl_get` makes 2 sequential subprocess calls (~200ms latency per duck/restore on Linux). Category: Performance.

**Root Cause:** verified — separate subcommands; no single combined call used.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/volume_backends/linux.py`

**Fix:** Replace with single `pactl list sinks`; OR run both in parallel.

**Severity:** 🟡 Medium

## [XV-57] — smart-duck polls `pactl list sink-inputs` every 500ms = ~10-20% CPU on Linux

**Status:** ❌ Not Fixed

**Description:** smart-duck polls `pactl list sink-inputs` every 500ms = ~10-20% CPU on Linux. Category: CPU usage / Resource footprint (battery).

**Root Cause:** verified — Linux backend inherits base 500ms default; per-call cost ~50-100ms.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/volume_backends/linux.py`
- `voice_typer/server/volume_ducker.py`

**Fix:** Override `recommended_poll_interval_ms` to 1000-2000ms; cache result with 250ms TTL; use `/proc/asound` procfs fast path.

**Severity:** 🟡 Medium

## [XV-58] — `_dropped_level_chunks` counter never logged/exposed (silent telemetry gap)

**Status:** ❌ Not Fixed

**Description:** `_dropped_level_chunks` counter never logged/exposed (silent telemetry gap). Category: Working-but-suboptimal / Observability.

**Root Cause:** verified — grep finds only declaration + global + increment; no consumers.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/level_monitor.py`

**Fix:** Log with throttling (every 5s if >0); expose via `get_level_diagnostics()` IPC.

**Severity:** 🟢 Low

## [XV-59] — `osascript is_speaker_active` is dead heuristic (smart-duck disabled for osascript)

**Status:** ❌ Not Fixed

**Description:** `osascript is_speaker_active` is dead heuristic (smart-duck disabled for osascript). Category: Working-but-suboptimal.

**Root Cause:** verified — fallback path is short-circuited by upstream disable.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/volume_backends/macos.py`

**Fix:** Remove the osascript `is_speaker_active` fallback; have it return True.

**Severity:** 🟢 Low

## [XV-60] — macOS CoreAudio mic watcher bypasses debounce + active-mic-lost detection

**Status:** ❌ Not Fixed

**Description:** macOS CoreAudio mic watcher bypasses debounce + active-mic-lost detection. Category: Audio pipeline quality.

**Root Cause:** verified — drop-in replacement was wired with raw callback.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/microphone_watcher.py`

**Fix:** Pass `self._invoke_callback` (debounced dispatcher) instead of raw callback.

**Severity:** 🔴 High

## [XV-61] — macOS polling fallback calls `sd.query_devices()` every 1s (10-50ms CPU)

**Status:** ❌ Not Fixed

**Description:** macOS polling fallback calls `sd.query_devices()` every 1s (10-50ms CPU). Category: CPU usage.

**Root Cause:** verified — heavy enumeration on macOS fallback path.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/microphone_watcher.py`

**Fix:** Raise default poll_interval to 2-3s; OR cache `sd.query_devices()` + diff by count first.

**Severity:** 🟡 Medium

## [XV-62] — `waveform_bubble_wiring` uses `getattr` despite `__init__` pre-declaration; `hasattr` dead branches

**Status:** ❌ Not Fixed

**Description:** `waveform_bubble_wiring` uses `getattr` despite `__init__` pre-declaration; `hasattr` dead branches. Category: Working-but-suboptimal.

**Root Cause:** verified — leftover from pre-RW-9 lazy-creation pattern.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/waveform_bubble_wiring.py`

**Fix:** Use direct attribute access; drop `hasattr` checks.

**Severity:** 🟢 Low

## [XV-63] — Waveform 16ms throttle drops 36% of 48kHz chunks

**Status:** ❌ Not Fixed

**Description:** Waveform 16ms throttle drops 36% of 48kHz chunks. Category: Audio pipeline quality.

**Root Cause:** suspected — throttle predates queue+worker redesign; backpressure now subsumed by queue.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/waveform_bubble_wiring.py`

**Fix:** Lower gate to ~8ms OR remove (queue maxsize=64 + PERF-3 drain handle backpressure).

**Severity:** 🟢 Low

## [XV-64] — `microphone_watcher._invoke_callback` has lazy `import time` inside method body

**Status:** ❌ Not Fixed

**Description:** `microphone_watcher._invoke_callback` has lazy `import time` inside method body. Category: Working-but-suboptimal.

**Root Cause:** verified — idiomatic violation.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/microphone_watcher.py`

**Fix:** Move `import time` to module-level imports.

**Severity:** 🟢 Low

## [XV-65] — `QwenEngine.load()` never moves model to CUDA (device="cuda" dead code)

**Status:** ❌ Not Fixed

**Description:** `QwenEngine.load()` never moves model to CUDA (device="cuda" dead code). Category: Performance / CPU usage.

**Root Cause:** verified — Qwen3-ASR-1.7B runs entirely on CPU regardless of GPU config.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/qwen_engine.py`

**Fix:** After `from_pretrained()`, resolve effective device via `torch.cuda.is_available()` when `auto`; call `self._model.to(effective_device)` (+ float16 for CUDA). Add `_resolve_device` helper.

**Severity:** 🔴 High

## [XV-66] — `ParakeetEngine.transcribe` holds `self._lock` for entire 13-chunk loop (~13s)

**Status:** ❌ Not Fixed

**Description:** `ParakeetEngine.transcribe` holds `self._lock` for entire 13-chunk loop (~13s). Category: CPU usage / Performance.

**Root Cause:** verified — lock held across all sequential GPU calls.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/parakeet_engine.py`

**Fix:** Capture `model = self._model` under lock, set `_inference_event`, release lock, run chunk loop outside, clear event in `finally`.

**Severity:** 🔴 High

## [XV-67] — Parakeet transcription processes chunks sequentially (no batching)

**Status:** ❌ Not Fixed

**Description:** Parakeet transcription processes chunks sequentially (no batching). Category: Performance / Audio pipeline quality.

**Root Cause:** verified — chunks processed strictly sequentially with no batching.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/parakeet_engine.py`

**Fix:** Batch 2-4 chunks per `processor()` + `generate()` call; fall back to sequential on OOM.

**Severity:** 🟡 Medium

## [XV-68] — Parakeet `load()` `snapshot_download` has no retry (inconsistent with Whisper/Parakeet-via-Models-page paths)

**Status:** ❌ Not Fixed

**Description:** Parakeet `load()` `snapshot_download` has no retry (inconsistent with Whisper/Parakeet-via-Models-page paths). Category: Performance.

**Root Cause:** verified — no retry wrapper; inconsistent across entry points.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/parakeet_engine.py`

**Fix:** Wrap in `_download_with_retry(..., max_attempts=4, delays=(2,4,8,16))`.

**Severity:** 🟡 Medium

## [XV-69] — parakeet/qwen branches in `_ensure_engine` omit `config=` kwarg → ConsentRequiredError on cold boot

**Status:** ❌ Not Fixed

**Description:** parakeet/qwen branches in `_ensure_engine` omit `config=` kwarg → ConsentRequiredError on cold boot. Category: Performance.

**Root Cause:** verified — config kwarg omitted for parakeet/qwen construction.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/model_manager.py`

**Fix:** Add `config=self._app.config` to both `parakeet_kwargs` and `qwen_kwargs`.

**Severity:** 🟡 Medium

## [XV-70] — `touch_active_model` declared but never called → LRU evicts actively-used model

**Status:** ❌ Not Fixed

**Description:** `touch_active_model` declared but never called → LRU evicts actively-used model. Category: Scalability / Performance.

**Root Cause:** verified — `touch_active_model` has no callers; `_evict_lru_model` uses stale timestamps.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/model_manager.py`
- `voice_typer/server/dictation_pipeline.py`

**Fix:** Call `self._app.models.touch_active_model()` from `DictationPipeline._transcribe` after every successful `transcribe_with_fallback`.

**Severity:** 🟡 Medium

## [XV-71] — Redundant `_warm_up_model` after `_probe_cuda_runtime` (~1.5s redundant on every CUDA load)

**Status:** ❌ Not Fixed

**Description:** Redundant `_warm_up_model` after `_probe_cuda_runtime` (~1.5s redundant on every CUDA load). Category: Performance.

**Root Cause:** verified — overlap in purpose; both run unconditionally on every fresh CUDA load.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/transcription.py`

**Fix:** Drop `_warm_up_model`; keep probe (it also validates CUDA runtime + triggers CPU fallback).

**Severity:** 🟢 Low

## [XV-72] — `release_gpu_memory()` called inside lock before `del self._model` (no-op + sync cost)

**Status:** ❌ Not Fixed

**Description:** `release_gpu_memory()` called inside lock before `del self._model` (no-op + sync cost). Category: Performance / Memory.

**Root Cause:** verified — call is no-op for VRAM release; inconsistent with sibling `_transcribe_with_fallback_unlocked`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/transcription.py`

**Fix:** Delete the call; `_pending_gc_collect = True` already triggers deferred cleanup outside lock.

**Severity:** 🟢 Low

## [XV-73] — `_evict_lru_model` calls `engine.unload()` inside `_model_lru_lock` (~100-500ms gc+CUDA sync)

**Status:** ❌ Not Fixed

**Description:** `_evict_lru_model` calls `engine.unload()` inside `_model_lru_lock` (~100-500ms gc+CUDA sync). Category: CPU usage / Performance.

**Root Cause:** verified — unload inside LRU lock; `touch_model` contends.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/model_manager.py`

**Fix:** Capture `oldest_backend` + `engine` under lock; release lock before `engine.unload()`; re-acquire only for `del`.

**Severity:** 🟢 Low

## [XV-74] — `_MAX_LOADED_MODELS = 2` is count-based, ignores VRAM capacity

**Status:** ❌ Not Fixed

**Description:** `_MAX_LOADED_MODELS = 2` is count-based, ignores VRAM capacity. Category: Scalability / Resource footprint.

**Root Cause:** verified — fixed count; metadata field unused by eviction.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/model_manager.py`

**Fix:** Detect total VRAM via `torch.cuda.get_device_properties(0).total_memory`; sum `required_vram_mb`; evict LRU until new model fits.

**Severity:** 🟢 Low

## [XV-75] — LLM polish 30s synchronous block on transcription thread

**Status:** ❌ Not Fixed

**Description:** LLM polish 30s synchronous block on transcription thread. Category: Performance / CPU usage.

**Root Cause:** verified — synchronous urllib call with no async/deferred path; invoked inline on single transcription thread.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/llm_polish.py`
- `voice_typer/server/dictation_pipeline.py`

**Fix:** Run polish on dedicated worker thread with shorter effective deadline (10s); OR paste rule-based text immediately + publish follow-up `llm_polish_applied` event when polish returns.

**Severity:** 🔴 High

## [XV-76] — LLM polish has no input size cap → 30k+ char dictations ship in full

**Status:** ❌ Not Fixed

**Description:** LLM polish has no input size cap → 30k+ char dictations ship in full. Category: Scalability / Performance.

**Root Cause:** verified — no truncation; no chunking.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/llm_polish.py`

**Fix:** Add `MAX_INPUT_CHARS = 8000` guard; skip polish with `llm_polish_skipped` event.

**Severity:** 🟡 Medium

## [XV-77] — `_lazy_import` caches `ImportError` permanently (no recovery without restart)

**Status:** ❌ Not Fixed

**Description:** `_lazy_import` caches `ImportError` permanently (no recovery without restart). Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — cache is write-once with no invalidation API.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/_lazy_import.py`

**Fix:** Add time-bound cache (retry once per 60s); OR expose `reset_cache()` called from `probe_required_deps()` success path.

**Severity:** 🟡 Medium

## [XV-78] — `_lazy_import.__setattr__` mutates real module in `sys.modules` (load-bearing but undocumented)

**Status:** ❌ Not Fixed

**Description:** `_lazy_import.__setattr__` mutates real module in `sys.modules` (load-bearing but undocumented). Category: Working-but-suboptimal.

**Root Cause:** verified — documented as intentional but undocumented as global side-effect.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/_lazy_import.py`

**Fix:** Add docstring warning; OR expose `_reset_proxy_cache(name)` helper.

**Severity:** 🟢 Low

## [XV-79] — `ai_enhancement` compiles regex inside function bodies (contradicts own design principle)

**Status:** ❌ Not Fixed

**Description:** `ai_enhancement` compiles regex inside function bodies (contradicts own design principle). Category: Working-but-suboptimal / Performance.

**Root Cause:** verified — contradicts documented design principle.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ai_enhancement.py`

**Fix:** Hoist both patterns + `pronouns` set to module level.

**Severity:** 🟢 Low

## [XV-80] — `llm_polish._call_api` error wrapping loses exception class info

**Status:** ❌ Not Fixed

**Description:** `llm_polish._call_api` error wrapping loses exception class info. Category: Working-but-suboptimal / Observability.

**Root Cause:** verified — `exc_info` not passed; original class preserved on `__cause__` but never logged.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/llm_polish.py`

**Fix:** Log `exc_info=True` in outer handler; OR branch on exception type with category tag.

**Severity:** 🟢 Low

## [XV-81] — `RateLimiter.allow()` does O(n) `sum()` per call (6-24% CPU under load)

**Status:** ❌ Not Fixed

**Description:** `RateLimiter.allow()` does O(n) `sum()` per call (6-24% CPU under load). Category: Performance / CPU usage.

**Root Cause:** verified — G4-M-09 cost-weighted refactor replaced O(1) len() check with O(n) scan.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`

**Fix:** Maintain `self._burst_total` + `self._sustained_total` as int fields; increment on append, decrement on evict.

**Severity:** 🟡 Medium

## [XV-82] — `ipc_server` pending_tcp snapshot+clear+remerge on every push when client disconnected

**Status:** ❌ Not Fixed

**Description:** `ipc_server` pending_tcp snapshot+clear+remerge on every push when client disconnected. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — snapshot+clear placed unconditionally; tcp_mode-only branch doesn't consume `pending`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc_server.py`

**Fix:** Only snapshot+clear when `tcp_client is not None`.

**Severity:** 🟡 Medium

## [XV-83] — TCP `json.dumps` uses `ensure_ascii=True` (vs WS path `ensure_ascii=False`)

**Status:** ❌ Not Fixed

**Description:** TCP `json.dumps` uses `ensure_ascii=True` (vs WS path `ensure_ascii=False`). Category: Performance.

**Root Cause:** verified — TCP path predates WS path; never updated.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc_server.py`

**Fix:** `line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))`.

**Severity:** 🟡 Medium

## [XV-84] — WS frame double UTF-8 encode (size check + send)

**Status:** ❌ Not Fixed

**Description:** WS frame double UTF-8 encode (size check + send). Category: Performance / Memory.

**Root Cause:** verified — encoded bytes not reused for send.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/sidecar_ws.py`

**Fix:** Encode once: `raw_bytes = json.dumps(event, ensure_ascii=False).encode("utf-8")`; `websocket.send(raw_bytes)`.

**Severity:** 🟢 Low

## [XV-85] — `ipc.validation` inline `import json` + per-call schema scan

**Status:** ❌ Not Fixed

**Description:** `ipc.validation` inline `import json` + per-call schema scan. Category: CPU usage.

**Root Cause:** verified — schema discovery inside hot path; inline import is code smell.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc/validation.py`

**Fix:** Move `import json` to module top; precompute `max_payload_bytes` per schema at definition time.

**Severity:** 🟢 Low

## [XV-86] — IPC `transport.py` uses `buffering=1` on read side (potentially small recv buffer)

**Status:** ❌ Not Fixed

**Description:** IPC `transport.py` uses `buffering=1` on read side (potentially small recv buffer). Category: Performance.

**Root Cause:** suspected — `buffering=1` chosen for line-buffering but read-side implication non-obvious.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc/transport.py`

**Fix:** Use `buffering=io.DEFAULT_BUFFER_SIZE` or explicit 65536.

**Severity:** 🟢 Low

## [XV-87] — WS `rate_limiter` not cached in dispatch closure (2 dict lookups per frame)

**Status:** ❌ Not Fixed

**Description:** WS `rate_limiter` not cached in dispatch closure (2 dict lookups per frame). Category: Performance.

**Root Cause:** verified — closure caches executor but not limiter.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/sidecar_ws.py`

**Fix:** Resolve limiter once in `_make_dispatch` alongside `ws_dispatch_pool`.

**Severity:** 🟢 Low

## [XV-88] — `vocabulary._save_user` contains 42 lines of dead code (duplicate retry loop)

**Status:** ❌ Not Fixed

**Description:** `vocabulary._save_user` contains 42 lines of dead code (duplicate retry loop). Category: Working-but-suboptimal.

**Root Cause:** verified — incomplete refactor left prior implementation as dead code.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vocabulary.py`

**Fix:** Delete lines 232-273 entirely; add regression test asserting `_save_user` raises after retries exhausted.

**Severity:** 🔴 High

## [XV-89] — `history_db.apply_retention` O(N²) COUNT queries

**Status:** ❌ Not Fixed

**Description:** `history_db.apply_retention` O(N²) COUNT queries. Category: Performance / Scalability.

**Root Cause:** verified — COUNT inside batched deletion loop.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/history_db.py`

**Fix:** Compute `total` once before loop; decrement by `batch_deleted` per iteration.

**Severity:** 🟡 Medium

## [XV-90] — `get_today_stats` uses non-sargable `DATE(timestamp)` predicate (full table scan)

**Status:** ❌ Not Fixed

**Description:** `get_today_stats` uses non-sargable `DATE(timestamp)` predicate (full table scan). Category: Performance.

**Root Cause:** verified — non-sargable predicate.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/history_db.py`

**Fix:** `WHERE timestamp >= DATE('now') AND timestamp < DATE('now', '+1 day')`.

**Severity:** 🟡 Medium

## [XV-91] — `templates.substitute_variables` eagerly calls `_get_clipboard_text()` per expansion

**Status:** ❌ Not Fixed

**Description:** `templates.substitute_variables` eagerly calls `_get_clipboard_text()` per expansion. Category: Performance.

**Root Cause:** verified — eager evaluation of all variables.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/templates.py`

**Fix:** Lazy substitution — only compute value when `text` contains `{var}`.

**Severity:** 🟡 Medium

## [XV-92] — `vocabulary.apply_to_text` re-compiles regex per entry per dictation (up to 10K compiles)

**Status:** ❌ Not Fixed

**Description:** `vocabulary.apply_to_text` re-compiles regex per entry per dictation (up to 10K compiles). Category: Performance.

**Root Cause:** verified — pattern re-compiled every `apply_to_text` invocation.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vocabulary.py`

**Fix:** Cache compiled patterns keyed by `(bad_phrase, ignore_case)`; invalidate on add/remove/import.

**Severity:** 🟡 Medium

## [XV-93] — `vocabulary_automation` O(words × |V|) Levenshtein per dictation

**Status:** ❌ Not Fixed

**Description:** `vocabulary_automation` O(words × |V|) Levenshtein per dictation. Category: Performance / CPU usage.

**Root Cause:** verified — O(words × |V|) per transcription.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vocabulary_automation.py`

**Fix:** Bucket `vocab_words` by length into `dict[int, set[str]]`; iterate only buckets `[len(word)-2, len(word)+2]`.

**Severity:** 🟡 Medium

## [XV-94] — `clipboard_snapshot` captures every format with no size cap (unbounded RAM on heavy clipboards)

**Status:** ❌ Not Fixed

**Description:** `clipboard_snapshot` captures every format with no size cap (unbounded RAM on heavy clipboards). Category: Memory / Resource footprint.

**Root Cause:** suspected — heavy clipboard payloads duplicated into Python memory on every dictation.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard_snapshot.py`

**Fix:** Skip formats whose `GlobalSize` exceeds 16MB; log debug.

**Severity:** 🟢 Low

## [XV-95] — `history_db` WAL checkpoint interval docstring/log says 60s, actual is 300s

**Status:** ❌ Not Fixed

**Description:** `history_db` WAL checkpoint interval docstring/log says 60s, actual is 300s. Category: Working-but-suboptimal.

**Root Cause:** verified — stale docstring/log after interval bump.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/history_db.py`

**Fix:** Update docstring + log message to reference `_WAL_CHECKPOINT_INTERVAL`.

**Severity:** 🟢 Low

## [XV-96] — `history_db.clear_all` batch size 100 too small for power users

**Status:** ❌ Not Fixed

**Description:** `history_db.clear_all` batch size 100 too small for power users. Category: Performance / Scalability.

**Root Cause:** verified — chunking added to bound WAL growth but SQLite autocheckpoint already self-bounds.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/history_db.py`

**Fix:** Increase `_CLEAR_ALL_BATCH_SIZE` to 1000; OR single DELETE + wal_autocheckpoint.

**Severity:** 🟢 Low

## [XV-97] — `vocabulary_automation._collect_vocabulary_words` rebuilds set every dictation

**Status:** ❌ Not Fixed

**Description:** `vocabulary_automation._collect_vocabulary_words` rebuilds set every dictation. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — no caching; rebuilds set per call.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/vocabulary_automation.py`
- `voice_typer/server/vocabulary.py`

**Fix:** Add `_generation` counter to `VocabularyManager`; cache set + last-generation; rebuild only on generation change.

**Severity:** 🟢 Low

## [XV-98] — `_is_safe_paste_target()` called twice per paste on Windows (full UIA + elevation work × 2)

**Status:** ❌ Not Fixed

**Description:** `_is_safe_paste_target()` called twice per paste on Windows (full UIA + elevation work × 2). Category: Performance / CPU usage.

**Root Cause:** verified — CRIT-2 TOCTOU re-validation implemented as full re-run rather than targeted re-check.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard/manager.py`

**Fix:** Split into full path (line 759) and cheap re-validation path (line 773) skipping `_is_elevated_target`.

**Severity:** 🟡 Medium

## [XV-99] — Linux AT-SPI tree walk has no state-based pruning (seconds of latency per paste)

**Status:** ❌ Not Fixed

**Description:** Linux AT-SPI tree walk has no state-based pruning (seconds of latency per paste). Category: Performance.

**Root Cause:** verified — full DFS without state-based pruning.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard_target_safety.py`

**Fix:** At each level, fetch child's StateSet; skip children with neither `STATE_SHOWING` nor `STATE_ACTIVE`.

**Severity:** 🟡 Medium

## [XV-100] — Linux clipboard `shutil.which()` called per-paste for invariant binaries

**Status:** ❌ Not Fixed

**Description:** Linux clipboard `shutil.which()` called per-paste for invariant binaries. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — repeated filesystem probing for invariant system binaries.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard/linux.py`

**Fix:** Cache results in module-level variables on first call.

**Severity:** 🟢 Low

## [XV-101] — `_release_stuck_modifiers()` runs before every early-return path in `paste()`

**Status:** ❌ Not Fixed

**Description:** `_release_stuck_modifiers()` runs before every early-return path in `paste()`. Category: Performance / CPU usage.

**Root Cause:** verified — defensive placement predates rate-limit short-circuit.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard/manager.py`

**Fix:** Move to just before keystroke send (after second safety check).

**Severity:** 🟢 Low

## [XV-102] — `_detect_focused_process()` not amortized against hwnd already captured by safety check

**Status:** ❌ Not Fixed

**Description:** `_detect_focused_process()` not amortized against hwnd already captured by safety check. Category: Performance.

**Root Cause:** verified — process-name detection is separate Win32 round-trip not amortized.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard/manager.py`

**Fix:** Have `_is_safe_paste_target` return `(safe, hwnd, process_name)`; or cache process name keyed by hwnd.

**Severity:** 🟢 Low

## [XV-103] — `_get_uia_singleton` / `_get_we_elevated` init race (no lock)

**Status:** ❌ Not Fixed

**Description:** `_get_uia_singleton` / `_get_we_elevated` init race (no lock). Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — classic check-then-act race on module-level mutable state.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard_target_safety.py`

**Fix:** Wrap init in module-level `threading.Lock()`.

**Severity:** 🟢 Low

## [XV-104] — `_pending_restores` peak memory scales with paste rate × snapshot size

**Status:** ❌ Not Fixed

**Description:** `_pending_restores` peak memory scales with paste rate × snapshot size. Category: Memory / Scalability.

**Root Cause:** verified — by-design borrow/restore lifecycle; list properly drains but peak scales with rate × size.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/clipboard/manager.py`

**Fix:** Document memory bound; consider capping in-flight list length.

**Severity:** 🟢 Low

## [XV-105] — N hotkeys = N native subprocesses (no pooling)

**Status:** ❌ Not Fixed

**Description:** N hotkeys = N native subprocesses (no pooling). Category: Scalability / Resource footprint.

**Root Cause:** verified — factory constructs one adapter per call; no process pooling.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/native_hotkeys/base.py`

**Fix:** Refactor `SubprocessHotkeyBackend` to accept list of specs and emit per-spec match events; OR introduce process-pool singleton.

**Severity:** 🟡 Medium

## [XV-106] — `timeBeginPeriod(8)` system-global on Windows (timer resolution change for entire OS)

**Status:** ❌ Not Fixed

**Description:** `timeBeginPeriod(8)` system-global on Windows (timer resolution change for entire OS). Category: CPU usage / Resource footprint (battery).

**Root Cause:** verified — winmm uses refcount; system stays at 8ms as long as ANY backend polling.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkeys/windows_native.py`

**Fix:** Use `CREATE_WAITABLE_TIMER_HIGH_RESOLUTION` (Win10 1803+) with `SetWaitableTimerEx`; or prefer `WH_KEYBOARD_LL` hook path more aggressively.

**Severity:** 🟡 Medium

## [XV-107] — `windows_native.py` docstrings claim 1ms Sleep, actual is 8ms

**Status:** ❌ Not Fixed

**Description:** `windows_native.py` docstrings claim 1ms Sleep, actual is 8ms. Category: Working-but-suboptimal.

**Root Cause:** verified — docstring drift after PERF-01/CPU-01 refactor.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkeys/windows_native.py`

**Fix:** Update docstrings to Sleep(8) (~125 Hz with timeBeginPeriod(8)).

**Severity:** 🟢 Low

## [XV-108] — `native_hotkeys/base.py` EOF busy-spins when subprocess crashes

**Status:** ❌ Not Fixed

**Description:** `native_hotkeys/base.py` EOF busy-spins when subprocess crashes. Category: CPU usage.

**Root Cause:** suspected — no backoff sleep between EOF and re-poll; race window normally microseconds but can stretch under load.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/native_hotkeys/base.py`

**Fix:** Add `time.sleep(0.01)` or `self._stop_event.wait(timeout=0.01)` on EOF before continue.

**Severity:** 🟢 Low

## [XV-109] — `capture.py` brute-force scans 250 VK codes per iteration

**Status:** ❌ Not Fixed

**Description:** `capture.py` brute-force scans 250 VK codes per iteration. Category: Performance / CPU usage.

**Root Cause:** verified — brute-force scan of entire VK table; reverse-lookup table not built.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkeys/capture.py`

**Fix:** Build reverse `_VK_MAP` (`{vk: name}`) and iterate only its keys (~80 vs 250); reduce Sleep(20) to Sleep(5).

**Severity:** 🟢 Low

## [XV-110] — `native_adapter._schedule_native_retry` leaves dead hotkey window during swap

**Status:** ❌ Not Fixed

**Description:** `native_adapter._schedule_native_retry` leaves dead hotkey window during swap. Category: Performance.

**Root Cause:** verified — intentional to avoid double-backend window but sacrifices coverage during swap.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkeys/native_adapter.py`

**Fix:** Start new backend BEFORE stopping old one; or keep stopped legacy as warm spare.

**Severity:** 🟢 Low

## [XV-111] — `WaylandHotkey.start()` always starts pynput fallback for 30s

**Status:** ❌ Not Fixed

**Description:** `WaylandHotkey.start()` always starts pynput fallback for 30s. Category: CPU usage.

**Root Cause:** verified — belt-and-suspenders fallback runs unconditionally even when socket path known-good.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/hotkeys/wayland.py`

**Fix:** Defer pynput fallback until socket has been listening 5s with no client; skip when `XDG_SESSION_TYPE=wayland` AND no `DISPLAY`.

**Severity:** 🟢 Low

## [XV-112] — `binary_path.get_native_binary_path()` not cached (6 stats × 3 backends at startup)

**Status:** ❌ Not Fixed

**Description:** `binary_path.get_native_binary_path()` not cached (6 stats × 3 backends at startup). Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — no module-level cache; binary path is per-platform constant.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/native_hotkeys/binary_path.py`

**Fix:** `@functools.lru_cache(maxsize=1)` on `get_native_binary_path()`.

**Severity:** 🟢 Low

## [XV-113] — Tray elapsed timer rebuilds icon every 1s tick during RECORDING (Windows NIM_MODIFY × 600 per 10min)

**Status:** ❌ Not Fixed

**Description:** Tray elapsed timer rebuilds icon every 1s tick during RECORDING (Windows NIM_MODIFY × 600 per 10min). Category: CPU usage.

**Root Cause:** verified — icon reassignment unconditional on every tick during RECORDING.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/tray.py`

**Fix:** Split into `_set_icon(state)` + `_refresh_tooltip()`; elapsed timer calls only `_refresh_tooltip()`.

**Severity:** 🟡 Medium

## [XV-114] — `_maybe_publish_tray_menu` builds full menu before TAURI_SIDECAR guard (wasted work on Electron)

**Status:** ❌ Not Fixed

**Description:** `_maybe_publish_tray_menu` builds full menu before TAURI_SIDECAR guard (wasted work on Electron). Category: Performance.

**Root Cause:** verified — guard is inside `publish_tray_menu`, not at top of `_maybe_publish_tray_menu`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/tray.py`

**Fix:** Add early return at top: `if os.environ.get("TAURI_SIDECAR") != "1": return False`.

**Severity:** 🟡 Medium

## [XV-115] — `_publish_tray_state` computes tooltip before TAURI_SIDECAR guard

**Status:** ❌ Not Fixed

**Description:** `_publish_tray_state` computes tooltip before TAURI_SIDECAR guard. Category: Performance.

**Root Cause:** verified — tooltip computed before guard; discarded on Electron runtime.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/tray.py`

**Fix:** Hoist TAURI_SIDECAR check to top; return early before `_compute_tooltip`.

**Severity:** 🟢 Low

## [XV-116] — Tray icon cache retains 256×256 ICO plane (~1.5MB avoidable on Windows)

**Status:** ❌ Not Fixed

**Description:** Tray icon cache retains 256×256 ICO plane (~1.5MB avoidable on Windows). Category: Memory / Resource footprint.

**Root Cause:** verified — 256×256 plane inflates buffer; comment misleading.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/tray_icon.py`

**Fix:** Drop 256×256 from sizes list; update cache-footprint comment.

**Severity:** 🟢 Low

## [XV-117] — `_get_icon_path` is dead code

**Status:** ❌ Not Fixed

**Description:** `_get_icon_path` is dead code. Category: Working-but-suboptimal.

**Root Cause:** verified — dead helper confirmed by full file read.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/tray_icon.py`

**Fix:** Delete; or wire into `_make_icon` Windows path before PNG→ICO synthesis.

**Severity:** 🟢 Low

## [XV-118] — `tray_models.build_models_submenu_data` calls `ensure_hf_env()` every invocation

**Status:** ❌ Not Fixed

**Description:** `tray_models.build_models_submenu_data` calls `ensure_hf_env()` every invocation. Category: Performance.

**Root Cause:** suspected — env-var side effect is process-global and idempotent; call should be once-per-process.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/tray_models.py`

**Fix:** Cache with module-level `_hf_env_ensured: bool` flag; or move to startup_tasks.

**Severity:** 🟢 Low

## [XV-119] — `_config_dir()` no cache → 30-50 stat()s at startup, 3+ per Config save

**Status:** ❌ Not Fixed

**Description:** `_config_dir()` no cache → 30-50 stat()s at startup, 3+ per Config save. Category: Performance / CPU usage.

**Root Cause:** verified — no caching layer; every helper in `_paths.py` re-resolves path on each invocation.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/config.py`

**Fix:** `@functools.lru_cache(maxsize=1)` on `_config_dir()`; expose `_reset_config_dir_cache()` test helper.

**Severity:** 🔴 High

## [XV-120] — `apply_config` G4-L-20 dirty-check does 2× deep-copy + 2× JSON serialize of 150+ fields per IPC call

**Status:** ❌ Not Fixed

**Description:** `apply_config` G4-L-20 dirty-check does 2× deep-copy + 2× JSON serialize of 150+ fields per IPC call. Category: Performance / CPU usage.

**Root Cause:** verified — dirty-check compares entire post-setattr state against pre-setattr state.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/config_applier.py`

**Fix:** Replace with targeted check on `updates` keys only; keep `pre_state_dict` only for rollback.

**Severity:** 🟡 Medium

## [XV-121] — Duplicated API-key redaction patterns between `_secrets.py` and `credential_store.py`

**Status:** ❌ Not Fixed

**Description:** Duplicated API-key redaction patterns between `_secrets.py` and `credential_store.py`. Category: Working-but-suboptimal / Security.

**Root Cause:** verified — `credential_store._API_KEY_RE` not updated when `_secrets._KEY_PATTERNS` aligned to 20-char threshold in G4-L-06.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/_secrets.py`

**Fix:** Delete `_API_KEY_RE` from credential_store.py; delegate to `_secrets.redact_secret`.

**Severity:** 🟡 Medium

## [XV-122] — `PIIRedactionFilter` runs 8-12 regex subs per log record unconditionally

**Status:** ❌ Not Fixed

**Description:** `PIIRedactionFilter` runs 8-12 regex subs per log record unconditionally. Category: CPU usage.

**Root Cause:** verified — no content-based fast-path; ordinary log lines pay 8 regex subs.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/security.py`

**Fix:** Add fast-path scan: `if not _FAST_TRIGGER.search(text): return text` where `_FAST_TRIGGER = re.compile(r"[@+]|\d{3,}|Bearer|Token|sk-|key=|[A-Za-z0-9_\-]{20,}")`.

**Severity:** 🟡 Medium

## [XV-123] — `permissions.py` re-imports pyobjc on every call when unavailable

**Status:** ❌ Not Fixed

**Description:** `permissions.py` re-imports pyobjc on every call when unavailable. Category: CPU usage.

**Root Cause:** verified — `try/except ImportError: return UNKNOWN` re-executes import on every call.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/permissions.py`

**Fix:** Cache `_PYOBJC_AVAILABLE: bool | None` at module level.

**Severity:** 🟢 Low

## [XV-124] — `apply_config_side_effects` rebuilds 30-element `filter_chain_keys` set per call

**Status:** ❌ Not Fixed

**Description:** `apply_config_side_effects` rebuilds 30-element `filter_chain_keys` set per call. Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — set literal inside method body, not module-level constant.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/config_applier.py`

**Fix:** Hoist to module-level `frozenset`; drop `set()` wrapper on `updates.keys()`.

**Severity:** 🟢 Low

## [XV-125] — `log_rate_limited` eager `msg % args` defeats lazy formatting claim

**Status:** ❌ Not Fixed

**Description:** `log_rate_limited` eager `msg % args` defeats lazy formatting claim. Category: Performance.

**Root Cause:** verified — comment-claimed laziness contradicted by eager `msg % args` expression.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/log_rate_limit.py`

**Fix:** Replace with `logger.debug(msg + " (suppressed occurrence %d)", *args, count)`.

**Severity:** 🟡 Medium

## [XV-126] — `_BubbleLevelExclusionFilter` calls `getMessage()` twice per file-bound record

**Status:** ❌ Not Fixed

**Description:** `_BubbleLevelExclusionFilter` calls `getMessage()` twice per file-bound record. Category: Performance.

**Root Cause:** verified — filter calls getMessage() which CPython recomputes on every call.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/log.py`

**Fix:** Check `self._MARKER not in record.msg` (raw template) instead of `getMessage()`.

**Severity:** 🟢 Low

## [XV-127] — `_RATE_LIMIT_COUNTS` dict unbounded (no eviction, no cap)

**Status:** ❌ Not Fixed

**Description:** `_RATE_LIMIT_COUNTS` dict unbounded (no eviction, no cap). Category: Memory / Scalability.

**Root Cause:** verified — no eviction; API invites future leaks.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/log_rate_limit.py`

**Fix:** Add soft cap (1024 keys); evict oldest 25% when exceeded.

**Severity:** 🟢 Low

## [XV-128] — `thread_registry` never evicts dead entries from `self._entries`

**Status:** ❌ Not Fixed

**Description:** `thread_registry` never evicts dead entries from `self._entries`. Category: Memory.

**Root Cause:** verified — no auto-eviction; `list_active()` filters by `is_alive()` but dict holds refs.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/thread_registry.py`

**Fix:** At end of `shutdown_all()`: `self._entries = {n: e for n, e in self._entries.items() if e.thread.is_alive()}`.

**Severity:** 🟢 Low

## [XV-129] — `timer_coordinator` never removes fired timers from `_pending_timers`

**Status:** ❌ Not Fixed

**Description:** `timer_coordinator` never removes fired timers from `_pending_timers`. Category: Memory.

**Root Cause:** verified — no self-removal on fire; list grows across cycles without cancel.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/timer_coordinator.py`

**Fix:** In `guarded_func`, after invoking `func()`, remove self from `_pending_timers` under lock.

**Severity:** 🟢 Low

## [XV-130] — PII filter attached to both logger and handler (double-scan for direct-`voice_typer` records)

**Status:** ❌ Not Fixed

**Description:** PII filter attached to both logger and handler (double-scan for direct-`voice_typer` records). Category: Performance.

**Root Cause:** verified — dual attachment intentional for child-logger coverage; side effect is double-scan for direct records.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/log.py`

**Fix:** Attach `_pii_filter` and `_SessionFilter` to handlers only.

**Severity:** 🟢 Low

## [XV-131] — `_infer_topic` linear scan over 80 keywords per INFO record

**Status:** ❌ Not Fixed

**Description:** `_infer_topic` linear scan over 80 keywords per INFO record. Category: CPU usage.

**Root Cause:** verified — linear scan + full-string lower() on every prefixless INFO record.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/log.py`

**Fix:** Precompile single regex with named groups per topic; or flat `{keyword: topic}` dict.

**Severity:** 🟢 Low

## [XV-132] — `thread_registry.shutdown_all` dead branch + lazy import + missing eviction

**Status:** ❌ Not Fixed

**Description:** `thread_registry.shutdown_all` dead branch + lazy import + missing eviction. Category: Working-but-suboptimal.

**Root Cause:** verified — all three observable in source.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/thread_registry.py`

**Fix:** Remove dead branch; move `import time` to module top; add eviction.

**Severity:** 🟢 Low

## [XV-133] — `_JsonFormatter` redundant `str()` on value already typed `str`

**Status:** ❌ Not Fixed

**Description:** `_JsonFormatter` redundant `str()` on value already typed `str`. Category: Working-but-suboptimal.

**Root Cause:** verified — redundant `str()` on value already typed `str`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/log.py`

**Fix:** Replace `str(payload["message"])` with `payload["message"]`.

**Severity:** 🟢 Low

## [XV-134] — `recording_controller` uses `Timer(0, func)` instead of plain daemon Thread

**Status:** ❌ Not Fixed

**Description:** `recording_controller` uses `Timer(0, func)` instead of plain daemon Thread. Category: Working-but-suboptimal.

**Root Cause:** verified — `Timer(0)` overkill for run-on-another-thread-ASAP.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/recording_controller.py`

**Fix:** For `delay == 0`, short-circuit to `threading.Thread(target=func, daemon=True).start()`; skip `_pending_timers` append.

**Severity:** 🟢 Low

## [XV-135] — `main.rs` `std::thread::sleep(10ms)` on Tauri event-loop thread

**Status:** ❌ Not Fixed

**Description:** `main.rs` `std::thread::sleep(10ms)` on Tauri event-loop thread. Category: CPU usage / Performance.

**Root Cause:** verified — `app.listen` registers synchronous callback; sleep blocks calling thread.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/main.rs`

**Fix:** Spawn async task: `tauri::async_runtime::spawn(async move { ws_tx.try_send(...); tokio::time::sleep(Duration::from_millis(10)).await; restart_handle.restart(); })`. Or drop sleep entirely; rely on WS writer task's channel-close flush.

**Severity:** 🔴 High

## [XV-136] — `spawn.rs` calls `kill_process_tree` synchronously on tokio worker (200-500ms stalls)

**Status:** ❌ Not Fixed

**Description:** `spawn.rs` calls `kill_process_tree` synchronously on tokio worker (200-500ms stalls). Category: CPU usage / Performance.

**Root Cause:** verified — `SidecarHandle::kill_tree` path correctly wrapped; spawn.rs paths not.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/sidecar/spawn.rs`

**Fix:** Wrap each `kill_process_tree(pid)` call in `tauri::async_runtime::spawn_blocking(move || kill_process_tree(pid)).await`.

**Severity:** 🔴 High

## [XV-137] — `RotatingFileWriter::write_line` calls `flush()` + `metadata()` per line (2 syscalls per log)

**Status:** ❌ Not Fixed

**Description:** `RotatingFileWriter::write_line` calls `flush()` + `metadata()` per line (2 syscalls per log). Category: Performance.

**Root Cause:** verified — both calls unconditional per write; docstring acknowledges lazy rotation but not per-write overhead.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/platform/logging.rs`

**Fix:** Track in-memory `current_size: u64` counter; only `metadata()` every 64 writes or when counter exceeds threshold. Drop per-write `flush()`; flush on 1s timer + at exit.

**Severity:** 🔴 High

## [XV-138] — `migrate_electron_userdata` runs synchronously on setup thread (5-30s on first launch)

**Status:** ❌ Not Fixed

**Description:** `migrate_electron_userdata` runs synchronously on setup thread (5-30s on first launch). Category: Performance (startup).

**Root Cause:** verified — migration runs inline on setup thread before sidecar spawn.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/migrate.rs`
- `src-tauri/src/main.rs`

**Fix:** Move migration into `tauri::async_runtime::spawn` block at main.rs:320 BEFORE `spawn_sidecar_and_get_port`; or wrap fs ops in `spawn_blocking`.

**Severity:** 🔴 High

## [XV-139] — Dead `token` field in `SidecarState` (write-only, never read)

**Status:** ❌ Not Fixed

**Description:** Dead `token` field in `SidecarState` (write-only, never read). Category: Working-but-suboptimal.

**Root Cause:** verified — field's own doc comment confirms dead.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/ft1.rs`

**Fix:** Remove field + delete write sites. Local `token` / `new_token` variables already hold value where needed.

**Severity:** 🟡 Medium

## [XV-140] — `ws.rs` spawns fresh OS thread per disconnect (thread churn under FT-1 flap)

**Status:** ❌ Not Fixed

**Description:** `ws.rs` spawns fresh OS thread per disconnect (thread churn under FT-1 flap). Category: CPU usage / Performance.

**Root Cause:** verified — `!Send` future forces thread::spawn+block_on bridge; no dedicated FT-1 worker.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/sidecar/ws.rs`

**Fix:** Use single dedicated FT-1 worker thread receiving disconnect notifications via `tokio::sync::mpsc::UnboundedReceiver<FT1Trigger>`.

**Severity:** 🟡 Medium

## [XV-141] — `sidecar_cmds.rs` double `ws_tx` mutex lock per dispatch

**Status:** ❌ Not Fixed

**Description:** `sidecar_cmds.rs` double `ws_tx` mutex lock per dispatch. Category: Performance.

**Root Cause:** verified — two lock/unlock cycles per dispatch; forces `!Send` guard issue.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/commands/sidecar_cmds.rs`

**Fix:** Merge `ws_tx` + `pending` into single `AsyncMutex<DispatchState>` struct.

**Severity:** 🟡 Medium

## [XV-142] — Redundant inner `Arc` on `PendingMap`

**Status:** ❌ Not Fixed

**Description:** Redundant inner `Arc` on `PendingMap`. Category: Working-but-suboptimal / Performance.

**Root Cause:** verified — F-S1 TODO explicitly documents redundant Arc; no `.pending.clone()` calls outside test setup.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/sidecar/ft1.rs`
- `src-tauri/src/sidecar/ws.rs`

**Fix:** Drop inner `Arc`; type as `AsyncMutex<HashMap<u64, oneshot::Sender<Value>>>`. Update 3 test sites.

**Severity:** 🟡 Medium

## [XV-143] — `ft1.rs` read/write restart counter not wrapped in `spawn_blocking` (fsync on tokio worker)

**Status:** ❌ Not Fixed

**Description:** `ft1.rs` read/write restart counter not wrapped in `spawn_blocking` (fsync on tokio worker). Category: CPU usage / Performance.

**Root Cause:** verified — sync fs I/O including fsync on tokio worker.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/sidecar/ft1.rs`

**Fix:** Wrap in `tauri::async_runtime::spawn_blocking`.

**Severity:** 🟡 Medium

## [XV-144] — `paste.rs` constructs fresh `Enigo` per paste (~1-5ms XOpenDisplay/CGEventSource per call)

**Status:** ❌ Not Fixed

**Description:** `paste.rs` constructs fresh `Enigo` per paste (~1-5ms XOpenDisplay/CGEventSource per call). Category: Performance.

**Root Cause:** verified — `Enigo::new` allocates internal state (X11 display connection, CGEventSource, etc.) per call.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/commands/paste.rs`

**Fix:** Cache `Enigo` in `SidecarState` behind `Mutex<Enigo>`; or `thread_local!` with `RefCell<Option<Enigo>>`.

**Severity:** 🟡 Medium

## [XV-145] — `paste.rs` enigo calls block async runtime (100-400ms for 200-char text)

**Status:** ❌ Not Fixed

**Description:** `paste.rs` enigo calls block async runtime (100-400ms for 200-char text). Category: CPU usage / Performance.

**Root Cause:** verified — sync enigo calls block async runtime worker.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/commands/paste.rs`

**Fix:** Wrap in `tauri::async_runtime::spawn_blocking`.

**Severity:** 🟡 Medium

## [XV-146] — `util.rs::encode` uses `format!` per byte (32 heap allocations per token)

**Status:** ❌ Not Fixed

**Description:** `util.rs::encode` uses `format!` per byte (32 heap allocations per token). Category: Working-but-suboptimal / Performance.

**Root Cause:** verified — pre-allocates String but uses format! per byte.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/util.rs`

**Fix:** Use `std::fmt::Write`: `for b in bytes { write!(s, "{:02x}", b).unwrap(); }`.

**Severity:** 🟢 Low

## [XV-147] — `export.rs::json_to_csv` per-cell allocations (~220K for 10K-row export)

**Status:** ❌ Not Fixed

**Description:** `export.rs::json_to_csv` per-cell allocations (~220K for 10K-row export). Category: Performance / Memory.

**Root Cause:** verified — functional `map → collect → join` pattern eagerly allocates per element.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/commands/export.rs`

**Fix:** Write directly into pre-allocated `String` via `write!`; change `csv_escape` to `csv_escape_into(&mut String, &str)`.

**Severity:** 🟢 Low

## [XV-148] — WS reader emits payload twice per server event (specific + generic `python-event`)

**Status:** ❌ Not Fixed

**Description:** WS reader emits payload twice per server event (specific + generic `python-event`). Category: Memory.

**Root Cause:** verified — ADR-0020 §6.3 mandates dual emission; clone is necessary.

**Progress:** None yet.

**Related Files:**
- `src-tauri/src/sidecar/ws.rs`

**Fix:** Construct `python-event` wrapper object once + reuse via `serde_json::to_string`; OR change contract to emit only `python-event` with type filter.

**Severity:** 🟢 Low

## [XV-149] — `tcp-connect.ts` UTF-8 decode across chunk boundaries corrupts non-ASCII text

**Status:** ❌ Not Fixed

**Description:** `tcp-connect.ts` UTF-8 decode across chunk boundaries corrupts non-ASCII text. Category: Scalability / Audio pipeline quality (text integrity).

**Root Cause:** suspected — `Buffer.toString()` does not buffer partial multi-byte sequences across chunks.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/python/tcp-connect.ts`

**Fix:** Use `StringDecoder` from `node:string_decoder`; OR accumulate raw `Buffer` chunks + split on `0x0a` bytes.

**Severity:** 🟡 Medium

## [XV-150] — `mdn-data` (~30MB) is a dead production dependency

**Status:** ❌ Not Fixed

**Description:** `mdn-data` (~30MB) is a dead production dependency. Category: Resource footprint.

**Root Cause:** verified — dead production dependency; ~30MB of MDN Web Docs reference data.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/package.json`

**Fix:** Remove from `dependencies`; move to `devDependencies` if needed transitively.

**Severity:** 🟡 Medium

## [XV-151] — `will-quit` 3s delay when `pythonProcess` is null (adopted Python + post-crash paths)

**Status:** ❌ Not Fixed

**Description:** `will-quit` 3s delay when `pythonProcess` is null (adopted Python + post-crash paths). Category: Performance (shutdown).

**Root Cause:** verified — no else branch when pythonProcess is null.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/index.ts`

**Fix:** Add `else { setImmediate(() => app.exit(0)); }` branch.

**Severity:** 🟡 Medium

## [XV-152] — `bubble:hidden` listener leak on rapid hide→hide→wait→show cycles

**Status:** ❌ Not Fixed

**Description:** `bubble:hidden` listener leak on rapid hide→hide→wait→show cycles. Category: Memory.

**Root Cause:** verified — leak scenario traced through code path; stale listener lingers until next `bubble:hidden` emit.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/windows/bubble-window.ts`

**Fix:** Call `ipcMain.removeAllListeners("bubble:hidden")` unconditionally in `showBubbleWindow`; OR track `state._pendingHideListener` and `removeListener` before registering new one.

**Severity:** 🟢 Low

## [XV-153] — `VT_BUBBLE_TEST` timers not cleared on shutdown

**Status:** ❌ Not Fixed

**Description:** `VT_BUBBLE_TEST` timers not cleared on shutdown. Category: CPU usage / Working-but-suboptimal.

**Root Cause:** verified — none of the three timer handles are stored or cleared.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/index.ts`

**Fix:** Store all 3 handles in module-level variables; clear in `before-quit`.

**Severity:** 🟢 Low

## [XV-154] — `logging.ts` synchronous file I/O on main process event loop (statSync + appendFileSync per log line)

**Status:** ❌ Not Fixed

**Description:** `logging.ts` synchronous file I/O on main process event loop (statSync + appendFileSync per log line). Category: CPU usage / Performance.

**Root Cause:** verified — synchronous logging on Electron main-process event loop; statSync unconditionally before size check.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/logging.ts`

**Fix:** Cache file size in module-level var; only statSync when cached size exceeds threshold. OR switch to `fs.createWriteStream` with `.write(line)`.

**Severity:** 🟡 Medium

## [XV-155] — Renderer console-message double file write for ERROR

**Status:** ❌ Not Fixed

**Description:** Renderer console-message double file write for ERROR. Category: CPU usage.

**Root Cause:** verified — double synchronous write for ERROR; INFO now also flows through `log.info`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`

**Fix:** Revert PVT-G5-081 gate to `level >= 2` for file-tee; OR for ERROR write only to `electron-renderer-errors.log`.

**Severity:** 🟢 Low

## [XV-156] — Shutdown-path timers missing `.unref()` (keep event loop alive past quit)

**Status:** ❌ Not Fixed

**Description:** Shutdown-path timers missing `.unref()` (keep event loop alive past quit). Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — only `bootstrap.ts:419` uses unref; rest don't.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/python/stop-python.ts`
- `voice_typer/client/src/main/python/relaunch-app.ts`
- `voice_typer/client/src/main/python/tcp-connect.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`
- `voice_typer/client/src/main/index.ts`

**Fix:** Add `.unref()` to shutdown-path timers; keep `heartbeatInterval` and `_tcpRetryTimer` without unref.

**Severity:** 🟢 Low

## [XV-157] — `stopPython()` called up to 4× on breaker trip (duplicate quit_app writes + multiple killTimers)

**Status:** ❌ Not Fixed

**Description:** `stopPython()` called up to 4× on breaker trip (duplicate quit_app writes + multiple killTimers). Category: Working-but-suboptimal / Performance.

**Root Cause:** verified — defensive redundancy intentional but side effects wasteful.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/bootstrap.ts`
- `voice_typer/client/src/main/index.ts`
- `voice_typer/client/src/main/python/stop-python.ts`

**Fix:** Add idempotency guard: `if (state._stopPythonCalled) return; state._stopPythonCalled = true;`. Reset in `startPython`.

**Severity:** 🟢 Low

## [XV-158] — `App.tsx` subscribes to entire `config` object → re-render storms on every settings change

**Status:** ❌ Not Fixed

**Description:** `App.tsx` subscribes to entire `config` object → re-render storms on every settings change. Category: Performance / CPU usage.

**Root Cause:** verified — zustand selector returns whole config object; mergeConfig always allocates new.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx`

**Fix:** Replace with field-level selectors: `useAppStore((s) => s.config?.onboarding_completed === true)`, etc.

**Severity:** 🔴 High

## [XV-159] — `subscribeBridgeReady` creates 12+ polling intervals (no short-circuit when bridge already ready)

**Status:** ❌ Not Fixed

**Description:** `subscribeBridgeReady` creates 12+ polling intervals (no short-circuit when bridge already ready). Category: Resource footprint / CPU usage.

**Root Cause:** verified — `subscribeBridgeReady` unconditionally creates polling interval without checking snapshot first.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`

**Fix:** Short-circuit: `if (getBridgeReadySnapshot()) return () => {};`.

**Severity:** 🟡 Medium

## [XV-160] — `useModelLifecycle` does double `setModels` (redundant array iteration + object allocation)

**Status:** ❌ Not Fixed

**Description:** `useModelLifecycle` does double `setModels` (redundant array iteration + object allocation). Category: Working-but-suboptimal.

**Root Cause:** verified — reconcile-active-model pass appended as separate setModels call.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts`

**Fix:** Merge into single `setModels` call with combined map callback.

**Severity:** 🟢 Low

## [XV-161] — `App.tsx` help overlay array rebuilt on every render (no `useMemo`)

**Status:** ❌ Not Fixed

**Description:** `App.tsx` help overlay array rebuilt on every render (no `useMemo`). Category: Performance / Working-but-suboptimal.

**Root Cause:** verified — inline array literal in JSX with no useMemo.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx`

**Fix:** Wrap in `useMemo` keyed on `[t, dictationLabel, repasteLabel]`; OR gate behind `showHelpOverlay`.

**Severity:** 🟢 Low

## [XV-162] — `AudioFilterChain` 30 inline handler closures recreated per render

**Status:** ❌ Not Fixed

**Description:** `AudioFilterChain` 30 inline handler closures recreated per render. Category: Working-but-suboptimal.

**Root Cause:** verified — 30 inline handler closures recreated per render.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx`

**Fix:** Use single `useCallback` factory: `const makeHandler = useCallback(<K>(field: K) => (v) => onConfigChange({ [field]: v }), [onConfigChange])`.

**Severity:** 🟢 Low

## [XV-163] — `useConnection` makes 7 separate `useAppStore` selector calls (4 actions + 3 values)

**Status:** ❌ Not Fixed

**Description:** `useConnection` makes 7 separate `useAppStore` selector calls (4 actions + 3 values). Category: Performance.

**Root Cause:** verified — zustand runs all registered selectors on every `set()` call; action-only selectors stable but still execute.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`

**Fix:** Group action selectors via `useShallow` (zustand v4.4+); OR extract actions via `useAppStore.getState()` for one-shot reads.

**Severity:** 🟢 Low
```

### Findings from Session 3
```
# Comprehensive Review — Session XA (Group 3: UX & UI)

**Session:** XA — Full-Review mode, GROUP 3 (UX & UI)
**Sub-agent count:** 20 review agents → 20 implementation agents
**Scope:** UX/UI consistency, Ease of use, Accessibility, User onboarding, User flows, Developer experience, Localization / i18n
**Platform qualifier:** Findings investigated ON LINUX (sandbox). Windows/macOS runtime validation pending where noted.

This file is APPENDED to the existing comprehensive-review.md from prior sessions. Items below use the `XA-N` prefix to avoid collision with prior sessions' IDs.

---

## XA-1 — TitleBar mixes 3 icon systems, missing hover/transition classes on sidebar toggle, parallel button system

**Status:** ❌ Not Fixed
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

**Status:** ❌ Not Fixed
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

**Status:** ❌ Not Fixed
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

## XA-9 — Color contrast: focus ring invisible in every theme, --border fails WCAG 1.4.11 everywhere, white primary-button text fails AA in 12 themes, dark-theme muted text fails AA in 9 themes

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical (with 4 High sub-items)
**Description:** 145 contrast failures detected across 12 themes + custom via programmatic WCAG 2.1 audit. (XA-9-1) **Critical:** Focus ring invisible in every theme — `focus-visible:ring-3 focus-visible:ring-ring/30` composites to 1.15:1–2.45:1, far below WCAG 1.4.11's 3:1 minimum. (XA-9-2) **High:** `--border` fails WCAG 1.4.11 (3:1) in every theme/mode (1.07:1–1.50:1) — affects every card, dialog, popover, dropdown, separator, grid divider. (XA-9-3) **High:** White primary-button text fails AA 4.5:1 in 12 theme/modes (monokai 2.50:1, tokyo-night 3.08:1, catppuccin 3.16:1, nord 3.57:1, github dark 3.64:1, ayu 3.05:1, etc.). (XA-9-4) **High:** Dark-theme muted/placeholder text fails AA 4.5:1 in 9 dark themes (3.0:1–4.3:1). (XA-9-5) Med-High: Destructive button text fails AA in 3 themes + custom theme dark mode. (XA-9-6) Medium: Theme-preview swatches misrepresent actual button contrast — show foreground-on-primary, not primary-foreground-on-primary. (XA-9-7) Medium: Custom theme editor skips contrast validation for `--border` and several critical pairs. (XA-9-8) Medium: Color-only status indication in TestReviewPanel (WCAG 1.4.1). (XA-9-9) Medium: 30+ hardcoded `text-{color}-{n}` classes bypass theme system. (XA-9-10) Low-Med: No cache invalidation on `prefers-color-scheme` change. (XA-9-11) Medium: `forced-colors: active` only patches focus outlines, not text/background tokens. (XA-9-12) Low-Med: `prefers-contrast: high` block doesn't override `--muted-foreground` or `--border` correctly. (XA-9-13) Low: Latent orphan-var risk if new vars added without updating `THEME_VARIABLES`. (XA-9-14) Medium: Custom `deriveCustomVars` hardcodes `#ffffff` for primary/accent/destructive foregrounds regardless of bg lightness. (XA-9-15) Low: Duplicate `contrastRatio` impl in ThemeSettingsSection vs color-utils.
**Root Cause:** `--ring` token tuned for raw contrast but math never accounted for the 30% alpha applied at usage time; `--border` chosen for visual subtlety not contrast; `--primary-foreground` assumed `--primary` is always dark enough for white text; dark-theme `--muted-foreground` tuned for `oklch(0.18)` bg but most dark themes use `oklch(0.11)–0.15`.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/ui/{button,input,select}.tsx` (focus ring usage)
- `voice_typer/client/src/renderer/src/index.css:98, 144` (default `--border`)
- `voice_typer/client/src/renderer/src/themes/{default,github,sepia,dracula,solarized,ayu,tokyo-night,catppuccin,nord,amoled,monokai,custom}.ts`
- `voice_typer/client/src/renderer/src/themes/__tests__/parity.test.ts`
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts:135-179`
- `voice_typer/client/src/renderer/src/theme-bootstrap.ts`
- `voice_typer/client/src/renderer/src/lib/color-utils.ts`
- `voice_typer/client/src/renderer/src/components/settings/{themeColorCache,ThemeSettingsSection}.tsx`
- `voice_typer/client/src/renderer/src/components/microphone/TestReviewPanel.tsx:148-158, 232, 239`
- 30+ files with hardcoded `text-amber-*`/`text-emerald-*`/`text-red-*` (see XA-3-2 for partial list)
**Fix (prioritized):**
1. **(XA-9-1)** Drop `/30` from `focus-visible:ring-ring/30` in all primitives (use full-opacity `--ring`); OR raise `--ring` lightness to ~0.7–0.8 in dark themes and ~0.3–0.4 in light themes so 30%-alpha composite still clears 3:1.
2. **(XA-9-2)** Raise `--border` to at least `oklch(0.78 …)` in light themes and `oklch(0.34 …)` in dark themes (both clear 3:1); OR introduce separate `--border-strong` token for interactive surfaces.
3. **(XA-9-3)** Compute `--primary-foreground` per-theme by checking `contrastRatio(white, primary) ≥ 4.5` and falling back to `black`/`oklch(0.1 0 0)` if it fails. Same for `--accent-foreground`/`--sidebar-primary-foreground`/`--destructive-foreground`.
4. **(XA-9-4)** Raise `--muted-foreground` to L=0.62–0.65 in dark themes with very dark backgrounds.
5. **(XA-9-5)** Darken monokai `--destructive` to `oklch(0.5 0.22 0)`; amoled light to `oklch(0.52 0.22 27)`; swap custom theme's destructive values.
6. **(XA-9-6)** Render preview "A" using `--primary-foreground` (not `--foreground`).
7. **(XA-9-7)** Expand `_getContrastPair` to return array of pairs; add explicit `--border` 3:1 check + `--primary`/`--primary-foreground` 4.5:1 check; consider blocking save when below 3:1.
8. **(XA-9-8)** Add status icon (check/exclamation/X) next to color in TestReviewPanel; prefix with "Good"/"OK"/"Poor" text.
9. **(XA-9-14)** In `deriveCustomVars`, compute `--primary-foreground`/`--accent-foreground`/`--destructive-foreground` via `contrastRatio(primary, "#ffffff")` vs `contrastRatio(primary, "#000000")` and pick winner.
10. **(XA-9-15)** Delete local `contrastRatio` in ThemeSettingsSection; import from `@/lib/color-utils`.
11. **(XA-9-11)** Add `@media (forced-colors: active)` block to `index.css` that remaps `--background`/`--foreground`/`--primary`/`--destructive` to system colors (Canvas/CanvasText/Highlight/etc.); or skip `applyThemeVars` when forced-colors active.
12. **(XA-9-12)** Add `--muted-foreground`/`--text-secondary` to `@media (prefers-contrast: high)` overrides; ensure `.border-border` class (not just `.border`) is overridden.

---

## XA-10 — Onboarding: missing i18n keys step4Item/step5Item (raw key strings on Welcome screen), completeDescription never rendered, setupCompleteSnack never wired, modelSelectAria not interpolated

**Status:** ❌ Not Fixed
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

## XA-11 — Onboarding backend: startup_sequence ignores .onboarding_started marker, broken tests, dead code, missing per-step logging

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 2 High sub-items)
**Description:** (XA-11-1) **Critical (re-used from XA-10-1):** Missing i18n keys `onboarding.step4Item`/`step5Item` — Welcome screen renders raw key strings. (XA-11-2) **High:** `startup_sequence.py:151-183` auto-heal ignores `.onboarding_started` marker — PVT-006 fix incomplete. Crash mid-wizard silently skips onboarding (auto-heal fires because `config_file.exists()` is True and started_marker check is missing). (XA-11-3) **High:** `About.test.tsx:121-128` asserts raw ISO fallback but `formatRelativeTime` now returns localized date — test will fail. (XA-11-4) **High:** `consent-privacy-behavior.test.tsx:420-438` asserts About page links to CHANGELOG.md but no such link exists. (XA-11-5) Medium: `Onboarding.test.tsx:219-294` "falls back to defaults" test asserts "F2" but default hotkey is now `<caps_lock>` — test will fail. (XA-11-6) Medium: `onboarding_reset` IPC handler + `reset_onboarding_complete` function are dead code — never invoked by any renderer. (XA-11-7) Medium: `completeDescription` + `setupCompleteSnack` i18n keys defined but never used (re-used from XA-10). (XA-11-8) Medium: `PunctuationCheatSheetButton` is dead code — onboarding has no link to the cheat sheet. (XA-11-9) Medium: FEATURES.md claims multilingual Whisper models are NOT offered, but `onboarding.py:424-489` includes them. (XA-11-10) Medium: README quick-start doesn't mention onboarding wizard or model download wait — time-to-first-transcription exceeds 5 minutes. (XA-11-11) Medium: No in-app "what's new" surfacing after update. (XA-11-12) Low: Per-step onboarding completions are not logged. (XA-11-13) Low: Onboarding error paths return generic messages — not actionable. (XA-11-14) Low: `App.tsx:381` hotkey label fallback uses stale `<f2>` default — doesn't match onboarding default of `<caps_lock>`. (XA-11-15) Low: Onboarding IPC response types are loosely typed — no shared schema. (XA-11-16) Low: User-facing docs have no version tag. (XA-11-17) Low: Double-save in `apply_settings` + `onboarding_apply` can leave inconsistent state if second save fails. (XA-11-18) Low: No tooltips/coach marks in the app that point to docs. (XA-11-19) Low: No guided tour — only one-shot onboarding. (XA-11-20) Low: `mark_started` has no success log.
**Root Cause:** PVT-006 fix split across two agents' scopes (renderer/controller side landed; startup_sequence.py gate never did); tests not updated after PVT-017 changed default hotkey from `<f2>` to `<caps_lock>`.
**Related Files:**
- `voice_typer/server/startup_sequence.py:151-183`
- `voice_typer/server/onboarding.py:123-176, 534-575, 260-297`
- `voice_typer/server/handlers/onboarding_handlers.py:359-380`
- `voice_typer/server/startup_tasks.py:400-461`
- `voice_typer/server/service.py:1294-1348, 1404-1410`
- `voice_typer/client/src/renderer/src/pages/About.tsx:37-47, 343-377, 168-173`
- `voice_typer/client/src/renderer/src/pages/__tests__/About.test.tsx:121-128`
- `voice_typer/client/src/renderer/src/__tests__/rw1-rewrite/consent-privacy-behavior.test.tsx:420-438`
- `voice_typer/client/src/renderer/src/pages/__tests__/Onboarding.test.tsx:219-294`
- `voice_typer/client/src/renderer/src/components/help/PunctuationCheatSheet.tsx:186-223`
- `voice_typer/client/src/renderer/src/components/settings/PrewarmAndUpdates.tsx:506-515`
- `voice_typer/client/src/renderer/src/App.tsx:381`
- `FEATURES.md:294, 341`
- `README.md:44-68`
**Fix (prioritized):**
1. **(XA-11-2)** Add `started_marker = _config_dir() / ".onboarding_started"` check in `startup_sequence.py:164`: if `config_file.exists() and not started_marker.exists()` → auto-heal; else → save default config.
2. **(XA-11-3)** Update `About.test.tsx:121-128` assertion to match new localized date format: assert result matches `/^[A-Z][a-z]{2} \d{1,2}, \d{4}$/` for `en` locale.
3. **(XA-11-4)** Add "View Changelog" button to `About.tsx` Resources section linking to `CHANGELOG.md` URL, using existing `about.viewChangelog` i18n key.
4. **(XA-11-5)** Update `Onboarding.test.tsx:219-294` assertion: `expect(summaryText).toContain("CAPS LOCK")` instead of "F2".
5. **(XA-11-6)** Call `onboarding_reset` IPC from `handleReRunWizard` before navigating (in `TroubleshootingSettingsSection.tsx:113-117`).
6. **(XA-11-8)** Mount `PunctuationCheatSheetButton` in DoneStep of Onboarding.tsx.
7. **(XA-11-9)** Update `FEATURES.md:294, 341` to `✅ Multilingual variants (tiny/small/medium) + Parakeet offered in onboarding (UX-32)`; update "Last updated" date.
8. **(XA-11-10)** Add "First Launch" subsection to README after Quick Install describing 6-step wizard + model download wait with time estimate.
9. **(XA-11-14)** Change `App.tsx:381` to `formatHotkey(config?.hotkey ?? "<caps_lock>")`.
10. **(XA-11-20)** Add `log.info("[ONBOARDING] Marked as started")` after `_secure_atomic_write` in `mark_started`; promote failure log from `debug` to `warning`.
11. **(XA-11-12)** Add `log.info("[ONBOARDING] Step advanced to %d (%s)", new_step, ctrl.step_name)` in `next_step`/`prev_step`; add per-setter logs.
12. **(XA-11-17)** Move `app.config.onboarding_completed = True` inside `apply_settings` before its `config.save()`; eliminate second `app.config.save()` call in `onboarding_apply`.

---

## XA-12 — Recording flow: silent failure modes, no live transcription, swallowed IPC errors, no pause/resume, 61s crash detection delay

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical (with 1 Critical + 4 High sub-items)
**Description:** (XA-12-1) **Critical:** `RecordingErrorCard` is dead code — `lastError` is never set for recording/transcription failures. Backend's failure paths report errors only via tray icon (`tray.set_state(ERROR, msg)`) + `tray.notify(...)`. `status_change` push event carries only `state.value` (e.g., `"error"`), not the human-readable message. User sees brief red status pill (~3s) with no message, no retry button, no audible error cue. (XA-12-2) **High:** No live transcription visible during recording — user has no real-time feedback that speech is being captured correctly. `StreamingTranscriptionSession` exists but output is only consumed after recording stops. (XA-12-3) **High:** `handleToggle` silently swallows IPC failures — `catch (err) { console.error(...) }` only. (XA-12-4) **High:** Empty/short recording (<0.5s) gives no renderer feedback — stop sound plays but no transcription follows and no explanation. (XA-12-5) **High:** Empty transcription result (engine returns "") gives no renderer-level feedback — only tray status/notification. (XA-12-6) Medium: Bubble mic button is never disabled — clicking during transcribing/disconnected is a silent no-op. (XA-12-7) Medium: No pause/resume recording capability — only start/stop. (XA-12-8) Medium: Backend crash mid-recording has ~61s detection delay — bubble shows frozen bars. (XA-12-9) Medium: VAD/silence auto-stop notification is tray-only — user in the app window sees recording silently stop. (XA-12-10) Medium: No audio quality feedback during real recording — LevelBar/LiveQualityFeedback are Microphone Test page only. (XA-12-11) Medium: Undo window is only 5 seconds — `lastText` auto-clears, removing Undo and Re-paste buttons. (XA-12-12) Medium: Bubble error mode shows only "⚠ Error" label — no message, no actionable guidance. (XA-12-13) Medium: User cannot review or edit transcription before it's pasted — pipeline auto-commits with no confirmation step. (XA-12-14) Low: Hotkey press during transcribing is silently ignored. (XA-12-15) Low: MicToggleButton disabled during loading but hotkey queues — inconsistent behavior. (XA-12-16) Low: Sound feedback has no "transcription complete" cue. (XA-12-17) Low: Force-cancel link only available on Home page. (XA-12-18) Low: Undo sends backspace keystrokes — destructive if cursor or focus has changed. (XA-12-19) Low: LevelBar clipping detection is RMS-only — transient peaks not flagged. (XA-12-20) Low: Test file coverage is extremely narrow — only tests listener count. (XA-12-21) Low: `recording_started` handler only clears `lastText` on Home page — stale text persists on return.
**Root Cause:** Backend communicates errors via `tray.set_state(ERROR, msg)` + `tray.notify(msg)`, but `status_change` push event only carries `state.value` (not `msg`); renderer's `lastError` is therefore never populated by recording failures.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx:632-641, 695, 686-692, 44, 589-595, 604-611, 723-732, 738-742`
- `voice_typer/client/src/renderer/src/Bubble.tsx:217-280, 264-277, 282-284`
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts:245-273, 201-241`
- `voice_typer/client/src/renderer/src/hooks/useSoundFeedback.ts:52-65`
- `voice_typer/server/recording_controller.py:352-360, 418-429, 165-167, 178-189, 368-378, 458-464, 645-660`
- `voice_typer/server/dictation_pipeline.py:300-320, 674-784, 1135-1291, 1088-1096, 1189-1198`
- `voice_typer/server/ipc_server.py:1430-1437`
- `voice_typer/server/handlers/dictation_handlers.py:34-63`
- `voice_typer/server/streaming.py:1-80`
- `voice_typer/client/src/renderer/src/components/feedback/{LiveQualityFeedback,LevelBar}.tsx`
- `voice_typer/client/src/renderer/src/bubble-components.tsx:444-493`
- `voice_typer/client/src/renderer/src/__tests__/rw0-rewrite/Home-transcription-final.test.tsx`
**Fix (prioritized — root cause fix unblocks 5 other findings):**
1. **(XA-12-1)** Extend `status_change` event payload in `ipc_server.py:1430-1437` to include the `message` field; update `useConnection.ts:245-260` to set `lastError` from `data.message` when `status === "error"` instead of clearing it. (Alternative: publish `event_bus.publish({"type": "error", "data": {"message": ...}})` from failure paths.)
2. **(XA-12-3)** Add `toast.error(t("home.toggleFailed"))` in `handleToggle` catch block (Home.tsx:632-641).
3. **(XA-12-4)** Publish `event_bus.publish({"type": "toast", "data": {"message": "Recording too short — try again"}})` from `recording_controller.py:458` when `duration < 0.5`.
4. **(XA-12-5)** Publish `event_bus.publish({"type": "transcription_empty", "data": {"reason": <reason>}})` from `dictation_pipeline.py:_handle_empty_transcription`; subscribe in Home.tsx and show toast.
5. **(XA-12-6)** Add `disabled` prop to `BubbleMicButton`; pass `disabled={mode === "transcribing" || mode === "error"}` from Bubble.tsx.
6. **(XA-12-11)** Increase `LAST_TEXT_AUTO_CLEAR_MS` from 5_000 to 30_000 (or make configurable).
7. **(XA-12-9)** Publish `event_bus.publish({"type": "silence_auto_stop", "data": {"reason": "no_audio_detected"}})` from `recording_controller.py:645-660`.
8. **(XA-12-16)** Add `playSoundCue("complete")` subscription on `transcription_final` in `useSoundFeedback.ts`; add "complete" cue to `sound-manager.ts`.
9. **(XA-12-19)** Add `peak` to `LevelBarProps`; pass real peak from Microphone.tsx; update `LevelBar.tsx:66` to `getVolumeTier(level, peak)`.
10. **(XA-12-8)** When `recordingState === "recording"`, increase health check frequency to every 5-10s (or add recording heartbeat).
11. **(XA-12-2 + XA-12-7 + XA-12-13 + XA-12-17 + XA-12-18)** Larger features — flag for follow-up.

---

## XA-13 — Model download: Parakeet silent success, dead install_parakeet_deps IPC, dead disk-space IPCs, duplicate cancel toast, raw str(exc) errors

**Status:** ❌ Not Fixed
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

## XA-14 — Settings save flow: debounced saves silently lost on unmount/navigate/close, validation errors discarded, partial-success invisible

**Status:** ❌ Not Fixed
**Severity:** 🔴 Critical (with 1 Critical + 3 High sub-items)
**Description:** (XA-14-1) **Critical:** Debounced text-field saves are silently lost on unmount/navigate/close — `updateConfigDebounced` updates local state + `_cachedConfig` immediately but pending value is NEVER added to `pendingUpdatesRef.current` (only inside the timer callback). Unmount cleanup clears timers without firing them. No `beforeunload` listener (compare with `useTheme.ts:391-398` QUIT-FLUSH-FIX). User types in LLM API key/URL/model, navigates away within 500ms, value silently dropped. (XA-14-2) **High:** Validation errors are surfaced only as a generic toast; specific message is discarded — backend returns specific validator text ("field 'history_max_entries' must be in [10, 1000000], got 5"), renderer shows only generic "Failed to save setting". (XA-14-3) **High:** Partial-success `model_errors` envelope is silently swallowed; UI shows "Saved ✓" when model swap failed — backend catches `change_model`/`set_active_backend` failures and includes them as `data.model_errors` with `data.status = "partial"`, but renderer treats any non-throwing resolution as full success. (XA-14-4) **High:** Rejected (unknown) keys are silently dropped; UI shows "Saved ✓" for fields the backend ignored — concrete trigger: "Re-run onboarding wizard" button calls `updateConfig({ onboarding_completed: false })` but `onboarding_completed` is explicitly EXCLUDED from `IPC_CONFIG_ALLOWLIST`; backend never persists it. (XA-14-5) Medium: `SettingsSaveIndicator` has no error state; failures vanish after the 8s toast. (XA-14-6) Medium: No unsaved-changes guard before page navigation or app close. (XA-14-7) Medium: Theme (color scheme) changes bypass `SettingsSaveIndicator` entirely. (XA-14-8) Medium: "Reset to defaults" is atomic with only the first error shown; silently skips API keys. (XA-14-9) Medium: No retry path for failed saves; failed value is overwritten by `loadConfig()`. (XA-14-10) Low: `Pending…` label is hardcoded English (re-used from XA-4-4). (XA-14-11) Low: Scroll position not preserved across page navigations (only active tab survives). (XA-14-12) Low: No "restart required" communication for settings that only take effect on next launch. (XA-14-13) Low: No audit log of successful config changes. (XA-14-14) Low: Cross-field validation is not enforced or warned about. (XA-14-15) Low: Many backend settings have no UI exposure (hidden config). (XA-14-16) Low: `setLocale` bypasses `useSettingsConfig`; not part of reset-to-defaults. (XA-14-17) Medium: `useSettingsConfig.flushPendingUpdates` swallows the response; no per-field ack tracking (umbrella for XA-14-3, XA-14-4).
**Root Cause:** Unmount cleanup mirrors pre-fix `useTheme.ts` pattern (clear timers, drop pending); QUIT-FLUSH-FIX applied to `useTheme.ts` but never ported to `useSettingsConfig.ts`. Save flow designed as fire-and-forget; response schema enriched server-side (G4-M-20) without corresponding client-side reader.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/useSettingsConfig.ts:119-131, 132-135, 180-220, 209-220`
- `voice_typer/client/src/renderer/src/components/settings/SettingsSaveIndicator.tsx:30-80, 55-62`
- `voice_typer/client/src/renderer/src/pages/Settings.tsx:183-211, 213-221, 364-372, 82-88, 122-143`
- `voice_typer/client/src/renderer/src/hooks/useTheme.ts:391-398, 368-387`
- `voice_typer/server/handlers/config_handlers.py:99-100, 104-105, 129-201, 283-292, 218`
- `voice_typer/server/config_validators.py:903-981, 935-942, 102-104, 687, 770-779`
- `voice_typer/server/settings_controller.py:105, 128, 157, 168`
- `voice_typer/client/src/renderer/src/components/settings/TroubleshootingSettingsSection.tsx:114`
- `voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:246-273`
**Fix (prioritized):**
1. **(XA-14-1)** In unmount cleanup, fire each pending debounced timer's `updateConfig` call synchronously before clearing (or merge all pending debounced values into `pendingUpdatesRef.current` and flush). Add `beforeunload` listener that calls `flushPendingUpdatesRef.current()` (mirror `useTheme.ts:391-398`).
2. **(XA-14-17 + XA-14-3 + XA-14-4)** Capture response: `const result = await call<...>("set_config", diff)`. If `result.data?.model_errors?.length`, show warning toast. If `result.data?.rejected?.length`, show inline warning per field.
3. **(XA-14-2)** In catch block, parse `err.message` for backend's specific text and show it in toast (or banner). Add `error` prop to `SettingRow` and per-field error state in `useSettingsConfig` keyed by field name.
4. **(XA-14-5)** Add `error` prop to `SettingsSaveIndicatorProps`; add 5th state with red dot + "Save failed" label + retry affordance. Set in catch block; clear on next successful save.
5. **(XA-14-9)** Don't call `loadConfig()` immediately on failure — keep attempted value in local state, mark field with inline error, let user edit + retry.
6. **(XA-14-4)** Route "Re-run onboarding wizard" button through dedicated IPC command (or `complete_onboarding` with `false`) instead of `set_config`.
7. **(XA-14-6)** Expose `hasPendingOrSaving` flag; wrap `onNavigate` calls in ConfirmDialog guard when true; add `beforeunload` listener that calls `event.preventDefault()`.
8. **(XA-14-10)** Add `settings.pending` i18n key (re-used from XA-4-4).
9. **(XA-14-13)** In `_handle_set_config` after `apply_config`, log each applied field at INFO with old→new values (exclude secret fields).
10. **(XA-14-11)** Persist scroll positions to `sessionStorage` keyed by tab; restore on mount.

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

## XA-18 — i18n: main-process dialogs always English (setMainLocale dead code), Settings search broken for 5/7 non-English locales, tChoice unused, ~140 backfilled untranslated keys

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 3 High sub-items)
**Description:** (XA-18-1) **High:** Main-process Electron dialogs always render in English — `setMainLocale()` is dead code; `MAIN_STRINGS` table has all 8 locales fully translated but `currentLocale` is stuck at `"en"`. (XA-18-2) **High:** Settings search keywords (`settings.searchHints.*`) untranslated in 5 of 7 non-English locales (de/fr/hi/ru/zh) — Settings search broken for non-English queries (French user typing "couleur" gets zero matches). (XA-18-3) **High:** `tChoice()` pluralization infrastructure is implemented but completely unused — UI uses incorrect `count === 1 ? singular : plural` pattern that breaks Russian/Arabic grammar. (XA-18-4) Medium: ~140+ translation keys intentionally backfilled as English in de/fr/hi/ru/zh (well-documented gap, but high user visibility). (XA-18-5) Medium: No missing-key logging in dev mode — silent fallbacks hide translation bugs. (XA-18-6) Medium: Tray menu i18n incomplete — only 2 of 18 tray keys pushed from renderer to backend. (XA-18-7) Low: Hardcoded English fallback string in `ModelCardActions.tsx:68` (re-used from XA-5-16). (XA-18-8) Low: No CSS font-family fallback for CJK / Arabic / Devanagari scripts. (XA-18-9) Low: Duplicate localStorage write in language switcher. (XA-18-10) Low: Unused `_locale` parameter in `trayLabelsForLocale` — relies on global state call order.
**Root Cause:** PVT-G5-068 removed `i18n:set-locale` IPC as "dead code" without replacing the locale-sync pathway; `tChoice()` added (PVT-082) but never wired into call sites; RW-2 backfilled keys as English to keep parity gate passing, translation deferred.
**Related Files:**
- `voice_typer/client/src/main/i18n.ts:166, 156`
- `voice_typer/client/src/main/ipc/window-handlers.ts:12-21`
- `voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx:53-56, 248-273, 58-69`
- `voice_typer/client/src/renderer/src/i18n/i18n.ts:342-480, 317-340, 266, 285-291`
- `voice_typer/client/src/renderer/src/i18n/translations/{de,fr,hi,ru,zh}.json:423-428`
- `voice_typer/client/src/renderer/src/pages/{Vocabulary,Dashboard,Templates}.tsx` (pluralization patterns)
- `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx:68`
- `voice_typer/client/src/renderer/src/index.css:5-18, 22-24`
- `voice_typer/server/i18n.py:215-235`
- `voice_typer/server/tray.py:74-101, 128-131`
- `tests/test_i18n_completeness.py:302-577, 350-354`
- `voice_typer/client/scripts/{translate-i18n,translate-i18n-all}.js`
**Fix (prioritized):**
1. **(XA-18-1)** Restore locale-sync pathway: either re-add `i18n:set-locale` IPC handler that calls `setMainLocale(locale)` (have renderer invoke on `setLocale()` and app boot), OR have renderer write locale to a small JSON file in user-data dir that main reads on boot, OR intercept existing `set_tray_locale` dispatch IPC in main process. Simplest: re-add `i18n:set-locale` IPC and call from renderer's `setLocale()`.
2. **(XA-18-2)** Translate `searchHints.*` keyword lists for de/fr/hi/ru/zh. Consider bilingual matching (include both English AND native keywords).
3. **(XA-18-3)** Migrate `vocabulary.entryCount*`, `analytics.dayCountTooltip*`, `templates.importSuccess*`, `vocabulary.importSuccess*` to use `tChoice()` with `*_one`/`*_few`/`*_many`/`*_other` keys. Add plural-suffixed keys to `ru.json` and `ar.json`.
4. **(XA-18-5)** In dev mode (`import.meta.env?.DEV`), log one-time warning per missing key: `console.warn('[i18n] Missing key "${key}" in locale "${_currentLocale}" — falling back to English')`.
5. **(XA-18-6)** Expand `TRAY_LABEL_KEY_MAP` to cover all 18 tray keys; add missing keys to en.json and translate in all 8 locales.
6. **(XA-18-8)** Add `:lang(zh)`/`:lang(ar)`/`:lang(hi)` selectors to `index.css` with locale-appropriate font stacks.
7. **(XA-18-9)** Remove redundant `localStorage.setItem` in GeneralSettingsSection.tsx:250-255.
8. **(XA-18-10)** Remove unused `_locale` parameter from `trayLabelsForLocale`, OR introduce `tForLocale(key, locale)` variant.
9. **(XA-18-4)** Commission native translation for backfilled keys (larger effort — partial in this run, focused on highest-visibility: ErrorBoundary, onboarding step4Item/step5Item, settings.pending, models.download.oneAtATime, models.snack.parakeetDepsInstalled, about.loading, settings.searchHints, hotkeyPicker.clearAria/clearTitle/holdingPrefix, settings.hotkeySection range/parse/range errors).

---

## XA-19 — Hardcoded strings audit: 21 distinct findings (ErrorBoundary, HotkeyPicker, hotkey-utils, RecordingSettingsSection, ModelCardActions, Sidebar, SettingsSaveIndicator, NumberInputStepper, App.tsx, etc.)

**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium (with 3 High sub-items)
**Description:** 21 distinct findings of hardcoded English strings that should be i18n keys. (XA-19-1) **High:** ErrorBoundary crash-recovery UI ships 5 hardcoded English strings (re-used from XA-8-M2 + XA-16-1). (XA-19-2) **High:** HotkeyPicker ships hardcoded English aria-label/title/"Holding:" prefix (re-used from XA-7-3). (XA-19-3) **High:** `hotkey-utils.ts` ships hardcoded English throughout (presets, key names, "None", validation errors). (XA-19-4) Medium: `RecordingSettingsSection.tsx` ships hardcoded form-validation errors and range hints (re-used from XA-4-5). (XA-19-5) Medium: `ModelCardActions.tsx` falls back to hardcoded English "Only one download at a time" (re-used from XA-5-16). (XA-19-6) Medium: `Sidebar.tsx` shows hardcoded English group labels ("Main", "Power features", "System") because `nav.group.*` keys are missing. (XA-19-7) Medium: `SettingsSaveIndicator.tsx` shows hardcoded English "Pending…" (re-used from XA-4-4). (XA-19-8) Medium: `number-input-stepper.tsx` hardcodes `aria-label="Increment"`/`"Decrement"` (re-used from XA-8-M1). (XA-19-9) Medium: `setLoadError` fallbacks leak English "Failed to load X" / "Unknown error" across 5 pages. (XA-19-10) Medium: `App.tsx` ships 4 hardcoded English strings (paste_failed fallback, "Caps Lock", "Page not found", "Unknown page"). (XA-19-11) Medium: `useModelLifecycle.ts` interpolates hardcoded English `"Open folder failed"` into translated toast template. (XA-19-12) Medium: `TitleBar.tsx` concatenates translated labels with hardcoded English keyboard-shortcut suffixes (no platform-awareness). (XA-19-13) Medium: `Onboarding.tsx` concatenates model `description — size (speed)` breaking i18n; `speed` value comes from hardcoded English in `INITIAL_MODELS`. (XA-19-14) Low: `LastUpdatedIndicator.tsx` concatenates translated label with hardcoded `": "` separator. (XA-19-15) Low: `SearchField.tsx` and `HotkeyPicker.tsx` default placeholder/aria-label are hardcoded English. (XA-19-16) Low: `useSnackbar.ts` default undo label is hardcoded English `"Undo"`. (XA-19-17) Low: `Templates.tsx` and `Vocabulary.tsx` `parseImportedTemplates`/`parseImportedVocabulary` throw English errors that surface via `t("...importFailed", { error: err.message })`. (XA-19-18) Low: `PrewarmAndUpdates.tsx` throws English HTTP errors that surface via `t("about.updateCheckFailed", { error: ... })`. (XA-19-19) Medium: `About.tsx` calls `t("about.loading")` but the key is missing from `en.json` (and all locale JSONs). (XA-19-20) Low: `App.tsx` `paste_failed` event handler also surfaces backend-supplied English `payload.message` without translation. (XA-19-21) Low: Hardcoded unit suffixes `"s"`, `"min"`, `"Hz"`, `"dB"`, `"ms"`, `"%"`, `"px"` may need locale-aware formatting.
**Root Cause:** Components authored before i18n rollout; defensive-literal pattern (`translated === key ? "fallback" : translated`) masks missing-key bugs; string concatenation breaks ICU-style interpolation.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx` (XA-19-1)
- `voice_typer/client/src/renderer/src/components/hotkey/HotkeyPicker.tsx` (XA-19-2)
- `voice_typer/client/src/renderer/src/components/hotkey/hotkey-utils.ts` (XA-19-3)
- `voice_typer/client/src/renderer/src/components/settings/RecordingSettingsSection.tsx` (XA-19-4)
- `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx` (XA-19-5)
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:66, 69, 72, 110-113` (XA-19-6)
- `voice_typer/client/src/renderer/src/components/settings/SettingsSaveIndicator.tsx` (XA-19-7)
- `voice_typer/client/src/renderer/src/components/ui/number-input-stepper.tsx:234, 249` (XA-19-8)
- `voice_typer/client/src/renderer/src/pages/{History,Microphone,Templates,Vocabulary,Onboarding}.tsx` (XA-19-9)
- `voice_typer/client/src/renderer/src/App.tsx:324, 380, 422, 425` (XA-19-10)
- `voice_typer/client/src/renderer/src/hooks/useModelLifecycle.ts:843` (XA-19-11)
- `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx:198, 209, 233, 263` (XA-19-12)
- `voice_typer/client/src/renderer/src/pages/Onboarding.tsx:342` + `lib/utils/models.ts:75-153` (XA-19-13)
- `voice_typer/client/src/renderer/src/components/common/LastUpdatedIndicator.tsx:50` (XA-19-14)
- `voice_typer/client/src/renderer/src/components/common/SearchField.tsx:25` + `HotkeyPicker.tsx:155` (XA-19-15)
- `voice_typer/client/src/renderer/src/hooks/useSnackbar.ts:157` (XA-19-16)
- `voice_typer/client/src/renderer/src/pages/{Templates,Vocabulary}.tsx` (XA-19-17)
- `voice_typer/client/src/renderer/src/components/settings/PrewarmAndUpdates.tsx:267, 269` (XA-19-18)
- `voice_typer/client/src/renderer/src/pages/About.tsx:269, 320` (XA-19-19)
- `voice_typer/client/src/renderer/src/App.tsx:322-323` (XA-19-20)
- `voice_typer/client/src/renderer/src/i18n/translations/en.json` (target for new keys)
**Fix (prioritized):**
1. **(XA-19-1)** Add ErrorBoundary i18n keys (overlaps with XA-8-M2 + XA-16-1).
2. **(XA-19-2)** Add HotkeyPicker i18n keys (overlaps with XA-7-3).
3. **(XA-19-3)** Refactor `hotkey-utils.ts` `displayMap` to key→i18n-key map (`{ ctrl: "hotkey.keys.ctrl", ... }`); convert `getSingleKeyPresets()`/`getComboPresets()` to return `labelKey`; replace 6 validation error returns with `t("hotkey.errors.*")`; replace `return "None"` with `return t("hotkey.none")`.
4. **(XA-19-4)** Add `settings.hotkeySection.rangeHintSeconds`/`rangeHintMinutes`/`parseError`/`rangeErrorSeconds`/`rangeErrorMinutes` keys (overlaps with XA-4-5).
5. **(XA-19-5)** Add `models.download.oneAtATime` key (overlaps with XA-5-16).
6. **(XA-19-6)** Add `nav.group.main`/`power`/`system` keys to all 8 locale files.
7. **(XA-19-7)** Add `settings.pending` key (overlaps with XA-4-4).
8. **(XA-19-8)** Replace `aria-label="Increment"`/`"Decrement"` with `t("a11y.increase")`/`t("a11y.decrease")` (existing keys).
9. **(XA-19-9)** Add `*.loadFailedDescription` keys (no interpolation); replace `setLoadError` calls so description always uses translated string; raw `err.message` goes to console only.
10. **(XA-19-10)** Add `home.pasteFailedMessage` key; delete local `formatHotkey` in App.tsx and import `formatHotkeyLabel` from `@/components/hotkey/hotkey-utils`; add `app.pageNotFoundTitle`/`pageNotFoundDescription` keys.
11. **(XA-19-11)** Add `models.openFolderFailed` key; replace `t("models.import.failed", { error: "Open folder failed" })` with `t("models.openFolderFailed")`.
12. **(XA-19-12)** Add i18n keys with interpolation `a11y.toggleSidebarWithShortcut`/`titleBar.backWithShortcut`/`titleBar.forwardWithShortcut`; compute shortcut string via `formatHotkeyLabel("<ctrl>+<b>")`.
13. **(XA-19-13)** Add i18n keys `models.speed.fastest`/`fast`/`slow`/`variable`; add template `onboarding.modelOption` = `"{description} — {size} ({speed})"`.
14. **(XA-19-14)** Add `common.lastUpdatedWithValue` = `"Last updated: {value}"` key.
15. **(XA-19-19)** Add `about.loading` key to all 8 locale files.
16. **(XA-19-15 + XA-19-16)** Change defaults to `t("common.search")` / `t("hotkeyPicker.defaultAriaLabel")` / `t("common.undo")`.
17. **(XA-19-17 + XA-19-18)** Define error code constants; map codes to translated messages in catch blocks.

---

## XA-20 — RTL/locale formatting: tChoice unused, untranslated strings, physical CSS properties, runtime locale-isolation between main window and bubble, no platform-aware shortcuts

**Status:** ❌ Not Fixed
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

## XZ-SEC-01 — `_security_attributes.py` references nonexistent `wintypes.VOID` (Critical)

**Status:** ❌ Not Fixed
**Description:** `_security_attributes.py:105` uses `ctypes.POINTER(wintypes.VOID)` but `wintypes.VOID` does not exist (only `LPVOID`). Every entry into `_create_restrictive_security_attributes` raises `AttributeError`, caught by the broad `except Exception` at line 240. The restrictive per-user-SID DACL on the single-instance mutex is therefore never built — falling back to the default per-user DACL. 8 unit tests in `tests/test__security_attributes.py` fail.
**Root Cause:** Wrong type reference. Should be `ctypes.c_void_p` (or `wintypes.LPVOID`).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/_security_attributes.py`
- `tests/test__security_attributes.py` (stale docstring + 2 tests asserting pre-CR-003 behavior — XZ-SEC-11)
**Fix:** Replace `("Sid", ctypes.POINTER(wintypes.VOID))` with `("Sid", ctypes.c_void_p)`. Delete the "Known SUT quirk" section from the test module docstring. Rename `test_failure_return_enters_null_dacl_fallback` → `test_failure_return_is_none_no_null_dacl` and assert `SetSecurityDescriptorDacl.assert_not_called()`. All 16 tests must pass.
**Severity:** 🔴 Critical
**VALIDATE ON WINDOWS HOST:** Inspect mutex DACL via AccessChk/Process Explorer.

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
- `voice_typer/server/service.py` (`_GDPR_PERSONAL_FILES`, `_GDPR_PERSONAL_GLOBS`, `delete_all_personal_data`, `export_gdpr_bundle`)
**Fix:** Add `"config.json.bak"`, `".restart_token"`, `"config.json.lock"` to `_GDPR_PERSONAL_FILES`. Add glob patterns for `"history.db.corrupt-*"`, `"voice-typer-diagnostics-*.zip"`, `"gdpr-export-*.zip"` to `_GDPR_PERSONAL_GLOBS`. Add regression test creating each artifact + asserting all gone after `delete_all_personal_data()`.
**Severity:** 🔴 High

## XZ-SEC-04 — `secrets_migrated` flag set unconditionally; auto-migration never re-triggers (Medium)

**Status:** ❌ Not Fixed
**Description:** `credential_store.py:1047` sets `data["secrets_migrated"] = True` even when keyring was unavailable and 0 secrets migrated. The docstring at `:860-863` claims auto-migration on next launch — contradicted by `docs/security/credential-store.md:108-117`. Plaintext API keys persist indefinitely on systems where keyring becomes available later.
**Root Cause:** Unconditional flag + docstring drift.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/config.py` (Config.load gate)
- `docs/security/credential-store.md`
**Fix:** Track `secrets_migrated_keyring_was_unavailable: bool`. Re-trigger migration in `Config.load` when prior attempt failed AND keyring is now available. Add renderer IPC `migrate_secrets_to_keyring_now` + UI button in `KeyringStatusBadge`. Update docstring.
**Severity:** 🟡 Medium

## XZ-SEC-05 — `extend_url_allowlist` is dead code; users can't configure self-hosted endpoints (Medium)

**Status:** ❌ Not Fixed
**Description:** `_secrets.py:288-353` defines `extend_url_allowlist` with elaborate audit logging (G4-M-55), but grep finds ZERO production callers — only tests and docstrings. Users running self-hosted LLM/ASR endpoints on non-loopback hosts get `ValueError` from `assert_url_allowed` with no in-app remediation path.
**Root Cause:** Function wired into tests/ADRs but never into production.
**Related Files:**
- `voice_typer/server/_secrets.py`
- `voice_typer/server/cloud_engines.py` (assert_url_allowed callers)
- `voice_typer/server/llm_polish.py` (assert_url_allowed callers)
- `voice_typer/server/config.py` (Config field for trusted hosts)
- `voice_typer/client/src/renderer/src/components/models/CloudProvidersPanel.tsx` (UI affordance — optional)
**Fix:** Wire `extend_url_allowlist` from a new IPC command `add_trusted_endpoint`. Persist user extensions to `config.json` under `trusted_extra_hosts: list[str]`. Re-apply on `Config.load`. Add end-to-end test that a user-configured `https://my-vllm.lan` URL passes `assert_url_allowed` after the host is added.
**Severity:** 🟡 Medium

## XZ-SEC-06 — `last_store_outcome` is dead code; renderer never sees per-store fallback reason (Medium)

**Status:** ❌ Not Fixed
**Description:** `credential_store.py:182-234` defines `last_store_outcome` and `_set_last_store_outcome` is called from `store_secret` on every path. But `_handle_set_config` IPC handler (`config_handlers.py:218`) NEVER calls `last_store_outcome()`. If keyring breaks mid-session, the next `set_config` silently falls back to plaintext and the user gets a generic `ack` with no signal.
**Root Cause:** Fix-G wiring promised in docstring was never landed.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/handlers/config_handlers.py`
- `voice_typer/client/src/renderer/src/components/common/KeyringStatusBadge.tsx` (UI toast)
**Fix:** In `_handle_set_config`, after `apply_config(validated)`, call `credential_store.last_store_outcome()` and include in the `ack` payload under `data.store_outcome` (only when `stored_in != "keyring"`). Renderer's `KeyringStatusBadge` renders an ephemeral toast on plaintext fallback. Add regression test.
**Severity:** 🟡 Medium

## XZ-SEC-07 — Duplicated redaction with divergence (Low)

**Status:** ❌ Not Fixed
**Description:** `credential_store._redact_sensitive` uses 32+ char threshold; `_secrets._KEY_PATTERNS` uses 20+ char threshold (after G4-L-06). 20-31 char tokens (GitLab PAT, GitHub PAT, Slack legacy) leak via `keyring_status.reason` tooltip.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/_secrets.py`
**Fix:** Replace `credential_store._redact_sensitive` with a thin wrapper around `_secrets.redact_secret` (plus existing `_PATH_RE` and `_REASON_MAX_LEN` truncation). Add regression test passing 24-char bare token.
**Severity:** 🟢 Low

## XZ-SEC-08 — Generic `KEYRING_SERVICE_NAME` (Low)

**Status:** ❌ Not Fixed
**Description:** `credential_store.py:97` uses `"voice-typer"` (no reverse-DNS). Another app registering the same service name + provider names could read Voice Typer secrets.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `docs/security/credential-store.md`
**Fix:** Change to `app.voicetyper` (or `com.github.voice-typer`). Add one-time migration in `migrate_secrets_to_keyring` (copy entries from old service name to new, delete old, gated on `service_name_migrated` flag).
**Severity:** 🟢 Low

## XZ-SEC-09 — `.restart_token`, diagnostics zips missing from GDPR delete set (Low)

**Status:** ❌ Not Fixed
**Description:** Same root as XZ-SEC-03 — `.restart_token` (bearer token granting app restart) and `voice-typer-diagnostics-*.zip` (containing redacted config + log) survive GDPR erasure.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/security.py` (token generation/persistence)
**Fix:** Folded into XZ-SEC-03 fix (add to `_GDPR_PERSONAL_FILES` and `_GDPR_PERSONAL_GLOBS`).
**Severity:** 🟢 Low

## XZ-SEC-10 — `load_secret` has no audit log (Low)

**Status:** ❌ Not Fixed
**Description:** `credential_store.py:519-559` `load_secret` returns silently on both success and fallback paths. A compromised process exfiltrating secrets via repeated `load_secret` calls leaves no trace in logs.
**Related Files:**
- `voice_typer/server/credential_store.py`
**Fix:** Add INFO log on keyring-success path: `log.info("[CREDENTIAL_STORE] loaded secret for provider=%s from keyring (len=%d)", provider, len(value))`. Match store-side format. Add test asserting INFO log fires on successful load.
**Severity:** 🟢 Low

## XZ-SEC-11 — `test__security_attributes.py` stale docstring + 2 tests (Low)

**Status:** ❌ Not Fixed
**Description:** Test module docstring (lines 10-32) describes pre-CR-003 NULL-DACL fallback behavior. `test_failure_return_enters_null_dacl_fallback` asserts NULL DACL — but post-CR-003 the function returns `None` BEFORE calling `SetSecurityDescriptorDacl`.
**Related Files:**
- `tests/test__security_attributes.py`
**Fix:** Folded into XZ-SEC-01 fix.
**Severity:** 🟢 Low

## XZ-SEC-12 — `is_url_allowed` returns True for empty URL (Low)

**Status:** ❌ Not Fixed
**Description:** `_secrets.py:361-377` — `is_url_allowed` returns `True` for empty URL while `assert_url_allowed` raises `ValueError`. Inconsistent contract.
**Related Files:**
- `voice_typer/server/_secrets.py`
**Fix:** Make `is_url_allowed` return `False` for empty URLs. Update docstring. Add test asserting both functions agree on empty-URL handling.
**Severity:** 🟢 Low

---

## XZ-IPC-001 — Standalone stdin auth bypass (Medium)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:main()` sets `server._tcp_mode = True` only for `--port`/`--ws` modes, NOT for standalone (the primary user-facing entry point via `voice-typer` console script). `start()` then spawns the unauthenticated stdin listener alongside the token-authenticated TCP server. On Linux, TIOCSTI injection is possible; on all platforms, accidental paste of JSON into terminal triggers unintended IPC commands.
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** In `main()`, set `server._tcp_mode = True` unconditionally before `server.start()` — `main()` never uses stdin/stdout mode.
**Severity:** 🟡 Medium

## XZ-IPC-002 — TCP accept-timeout DoS (Medium)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:786-794` accepts TCP connections and queues them to `ThreadPoolExecutor` (max_workers=4, unbounded queue). No socket timeout set at accept time — queued connections have no deadline until a worker picks them up. Local attacker opens N connections rapidly → server holds N file descriptors for tens of seconds.
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Set `conn.settimeout(10.0)` immediately after `accept()` (line 770), before `pool.submit`.
**Severity:** 🟡 Medium

## XZ-IPC-003 — WS no max_connections (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:728-733` calls `serve()` without concurrent-connection limit. Local attacker can open many WS connections faster than 5s auth timeout reaps them.
**Related Files:** `voice_typer/server/sidecar_ws.py`
**Fix:** Implement `asyncio.Semaphore(MAX_WS_CONNECTIONS=16)` in `_handle_connection`. Reject overflow with 1008 close.
**Severity:** 🟢 Low

## XZ-IPC-004 — Authenticated idle-connection DoS (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:967-968` clears the 5s auth timeout after auth succeeds (`conn.settimeout(None)`). Dispatch loop blocks on `readline` forever. Heartbeat watchdog never fires because `_last_heartbeat_at` is None (only set by heartbeat command). 4 idle authenticated connections deadlock the TCP worker pool.
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Set per-connection idle-read timeout (60s) after auth, treat `socket.timeout` as disconnect. OR: change watchdog to fire if `_last_heartbeat_at is None AND connection open > _HEARTBEAT_TIMEOUT_SECONDS`.
**Severity:** 🟢 Low

## XZ-IPC-005 — WS `shutdown` bypasses rate limiter (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:321-344` handles `shutdown` BEFORE the rate-limiter check. Authenticated client can spam unlimited `shutdown` frames, each spawning a daemon thread that calls `app.quit()`.
**Related Files:** `voice_typer/server/sidecar_ws.py`
**Fix:** Add idempotency guard: `if server._shutdown_requested: return ack; server._shutdown_requested = True` before spawning the thread.
**Severity:** 🟢 Low

## XZ-IPC-006 — Dead else branch in `_handle_tcp_connection` (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:881-947` — SEC-2 early-return at line 868-876 makes the `else` branch (line 946-947) unreachable. The `if expected_token:` guard at line 881 is redundant.
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Remove the `else` branch and de-indent the `if expected_token:` body.
**Severity:** 🟢 Low

## XZ-IPC-007 — `ipc_server.py` is 2587-line monolith (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py` is 2587 lines mixing IPCServer class, main(), dispatch logic, _send path with 4 branches, _handle_tcp_connection (360 lines).
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Extract `main()` into `ipc_entry.py`. Extract `_send` branches into helpers. Extract auth handshake. Mechanical refactor preserving all public APIs and tests.
**Severity:** 🟢 Low

## XZ-IPC-008 — Dead `_ws_loop` variable + redundant loop reassignments (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:534, 550, 560` — three `loop = asyncio.get_running_loop()` assignments in same function. `_ws_loop` (line 550) is dead. Lines 560 and 570 are redundant.
**Related Files:** `voice_typer/server/sidecar_ws.py`
**Fix:** Delete line 550 and lines 560/570. Keep line 534 + `server._ws_loop = loop`.
**Severity:** 🟢 Low

## XZ-IPC-009 — Stale line-number references in comments (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:952, 1573, 2445` — comments reference wrong line numbers (e.g. "set at line ~1171" but actual is line 861).
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Replace line-number references with function/label references.
**Severity:** 🟢 Low

## XZ-IPC-010 — Duplicated diagnostic-file writing blocks (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:2316-2382` and `:2513-2574` are near-identical ~65-line blocks writing PII-redacted traceback to `startup-error.log`. Block 2 does NOT redact `sys.argv` (potential `--ipc-token sk-...` leak).
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Extract `_write_startup_diagnostic(buf: StringIO, *, include_argv: bool = True) -> None` helper. Both blocks call it with `include_argv=True`.
**Severity:** 🟢 Low

## XZ-IPC-011 — Stale test docstring (Low)

**Status:** ❌ Not Fixed
**Description:** `tests/test_server.py:1717-1727` `test_no_token_env_allows_unauthenticated` docstring claims server accepts unauthenticated connections — but SEC-2 fix changed it to refuse-all. Test body is a no-op (only asserts env var unset).
**Related Files:** `tests/test_server.py`
**Fix:** Update docstring. Convert test into real assertion that `_handle_tcp_connection` closes the conn and returns when `expected_token` is empty.
**Severity:** 🟢 Low

## XZ-IPC-012 — `is True` idiom fragility (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:1577, 1934` and `sidecar_ws.py:311` use `getattr(self.app, "_shutting_down", False) is True` — accommodates test MagicMock auto-vivification. A real refactor setting `_shutting_down = 1` (truthy int) would bypass the shutdown gate.
**Related Files:** `voice_typer/server/ipc_server.py`, `voice_typer/server/sidecar_ws.py`
**Fix:** Add assertion in `VoiceTyperApp.__init__` that `_shutting_down` is a bool. Change `is True` back to truthiness.
**Severity:** 🟢 Low

---

## XZ-R3-01 — Heartbeat starvation → backend crash (High)

**Status:** ❌ Not Fixed
**Description:** `ipc/rate_limiter.py:69-76` `COMMAND_COSTS` gives heartbeat cost 1 with no priority. Per-process burst budget is 200 msg/s shared across ALL connections. A compromised renderer sustaining ≥200 msg/s of cheap commands exhausts the budget; every heartbeat during the attack window is rejected. After 120s (24 missed heartbeats), `_check_heartbeat_timeout` calls `app.quit()`, killing the backend.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`
- `voice_typer/server/ipc_server.py` (allow() call at line 1083, heartbeat watchdog at 1252-1305)
**Fix:** Add dedicated heartbeat budget that bypasses the burst check: `if command == "heartbeat": return True`. OR add per-command-type sub-limits.
**Severity:** 🔴 High

## XZ-R3-02 — Rate limiter cost map coverage gap (Medium)

**Status:** ❌ Not Fixed
**Description:** `ipc/rate_limiter.py:69-76` `COMMAND_COSTS` covers only 5 of 69 commands. Expensive uncovered commands (delete_model, test_llm_connection, run_prewarm, restart_app, quit_app, save_vocabulary, save_templates, microphone_test_start, level_monitor_start, force_cancel_transcription) default to cost 1.
**Related Files:** `voice_typer/server/ipc/rate_limiter.py`
**Fix:** Expand COMMAND_COSTS: delete_model=50, test_llm_connection=20, run_prewarm=50, save_vocabulary=10, save_templates=10, restart_app=100, quit_app=100, microphone_test_start=20, level_monitor_start=20, force_cancel_transcription=10. Add per-command-type sliding-window cap (max 1 restart_app per 10s).
**Severity:** 🟡 Medium

## XZ-R3-03 — Duplicated `_get_rate_limiter` implementation (Medium)

**Status:** ❌ Not Fixed
**Description:** `ipc/rate_limiter.py:318-361` and `ipc_server.py:116-159` are byte-for-byte identical implementations. Drift risk: fix one, forget the other.
**Related Files:**
- `voice_typer/server/ipc/rate_limiter.py`
- `voice_typer/server/ipc_server.py`
**Fix:** Extract as `@classmethod get_or_create(cls, server)` on `_RateLimiter`. Both call sites import the single function. Tests still monkey-patch `ipc_server._RateLimiter` (classmethod resolves cls from instance).
**Severity:** 🟡 Medium

## XZ-R3-04 — `set_tray_locale` unbounded input (Medium)

**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:196-209` schema has NO `max_value_len` on locale, NO `max_payload_bytes`, NO validation of labels dict contents. `register_tray_labels` (`tray.py`) accumulates without cap.
**Related Files:**
- `voice_typer/server/handlers/system_handlers.py`
- `voice_typer/server/tray.py`
**Fix:** Add `max_value_len: 64` to locale, `max_payload_bytes: 64*1024` to schema. Validate label dict: keys ≤ 64 chars, values ≤ 1024 chars. Cap `_TRAY_LABELS_LOCALES[locale]` at 200 keys.
**Severity:** 🟡 Medium

## XZ-R3-05 — Silent failure on restart_app/quit_app (Medium)

**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:43-58` sends ack BEFORE `service.restart()`/`service.quit()`. If the service call raises, error is logged server-side but NO error event is pushed to client. Client proceeds as if restart succeeded.
**Related Files:** `voice_typer/server/handlers/system_handlers.py`
**Fix:** After `self._send(resp)`, if `service.restart()`/`service.quit()` raises, push follow-up error event via `event_bus.publish({"type": "restart_failed", ...})`. Client subscribes + surfaces toast.
**Severity:** 🟡 Medium

## XZ-R3-06 — bool-as-int type confusion (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:174` uses `isinstance(value, expected_type)` — for `(int, str)`, `isinstance(True, (int, str))` returns True (bool subclass of int). History handlers accept `{"limit": true}` silently coerced to `limit=1`.
**Related Files:**
- `voice_typer/server/ipc/validation.py`
- `voice_typer/server/ipc/history_bounds.py`
- `voice_typer/server/handlers/history_handlers.py`
**Fix:** In `_validate_dict_payload`, when expected_type includes int, explicitly reject bool. Or add `reject_bool` rule.
**Severity:** 🟢 Low

## XZ-R3-07 — `max_payload_bytes` fragile scoping (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:153-167` — `max_payload_bytes` is per-field but checks whole payload. Helper checks FIRST field that declares it and breaks. Second field's value silently ignored.
**Related Files:** `voice_typer/server/ipc/validation.py`
**Fix:** Lift `max_payload_bytes` to top-level schema argument: `_validate_dict_payload(data, schema, max_payload_bytes=...)`.
**Severity:** 🟢 Low

## XZ-R3-08 — `default` rule doesn't fire for explicit None (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc/validation.py:171-225` — default rule only fires when field is ABSENT. Explicit `null` fails type check instead of using default. `show_electron_notification` works around with 8 lines of pre-coercion.
**Related Files:** `voice_typer/server/ipc/validation.py`
**Fix:** Add `none_to_default: bool = True` rule. When field value is None and rule is True, use default.
**Severity:** 🟢 Low

## XZ-R3-09 — Sidecar env vars not validated (Low)

**Status:** ❌ Not Fixed
**Description:** `env_validation.py:222-246` `_validate_sidecar_env` only logs warnings — does NOT pop, reset, or reject unsafe values. `VOICE_TYPER_NATIVE_DIR` and `VOICE_TYPER_PREWARM_EXE` paths not run through `_validate_path_safety`.
**Related Files:** `voice_typer/server/env_validation.py`
**Fix:** For `VOICE_TYPER_NATIVE_DIR` and `VOICE_TYPER_PREWARM_EXE`, run `_validate_path_safety` against expected parent. Pop on failure. Validate `VOICE_TYPER_IPC_TOKEN` against alphanumeric pattern.
**Severity:** 🟢 Low

## XZ-R3-10 — Path/URL leak in env-validation logs (Low)

**Status:** ❌ Not Fixed
**Description:** `env_validation.py:54-58, 89-93, 128-133` logs full path/URL values via `%r`. Path may contain username (e.g. `/home/real-username/...`).
**Related Files:** `voice_typer/server/env_validation.py`
**Fix:** Truncate to first 64 chars + "...<truncated>". Avoid `%r`. Or log only validation failure, not value.
**Severity:** 🟢 Low

## XZ-R3-11 — Inconsistent `_error_response` usage (Low)

**Status:** ❌ Not Fixed
**Description:** `handlers/privacy_handlers.py:139-143, 206-210` calls `_error_response` without returning result. `system_handlers.py:359-365, 425-427` builds envelope inline because `_error_response` doesn't support `field` key.
**Related Files:**
- `voice_typer/server/handlers/privacy_handlers.py`
- `voice_typer/server/handlers/system_handlers.py`
- `voice_typer/server/handlers/_base.py`
**Fix:** Make `privacy_handlers` consistent (`return _error_response(...)`). Extend `_error_response` to accept optional extra fields (`field=..., **extra`).
**Severity:** 🟢 Low

## XZ-R3-12 — No input validation on `check_accessibility` (Low)

**Status:** ❌ Not Fixed
**Description:** `handlers/system_handlers.py:83-177` ignores `data` entirely — no `_validate_dict_payload` call. Inconsistency with other handlers.
**Related Files:** `voice_typer/server/handlers/system_handlers.py`
**Fix:** Add empty-schema validation at top of handler.
**Severity:** 🟢 Low

## XZ-R3-13 — `register_tray_labels` accumulates without cap (Low)

**Status:** ❌ Not Fixed
**Description:** `tray.py:register_tray_labels` merges new labels over existing without bound. Module-level `_TRAY_LABELS_LOCALES` grows monotonically across calls.
**Related Files:** `voice_typer/server/tray.py`
**Fix:** Cap at 200 keys per locale. Drop oldest or reject with `client.invalid_field`. Validate label keys against known set.
**Severity:** 🟢 Low

---

## XZ-R4-001 — Bearer-token auth at handshake only; ADR "HMAC" wording misleading (Medium)

**Status:** ❌ Not Fixed
**Description:** `Cargo.toml:56-61` comment + `sidecar_ws.py:189-244` + `ws.rs:190-382` — ADR-0020 §3 says "HMAC" but implementation is one-shot bearer-token check at WS handshake. No per-message MAC, no nonce, no replay protection.
**Related Files:**
- `src-tauri/Cargo.toml` (comment)
- `voice_typer/server/sidecar_ws.py`
- `src-tauri/src/sidecar/ws.rs`
- `docs/adr/0017-cloud-url-allowlist-https.md` (or ADR-0020)
**Fix:** Update ADR-0020 §3 to document bearer-token model. Add threat-model note: loopback-only bind + ephemeral port + token rotation on FT-1 respawn are compensating controls.
**Severity:** 🟡 Medium

## XZ-R4-002 — Bearer token via env var readable by same-user processes on Linux (Medium)

**Status:** ❌ Not Fixed
**Description:** `sidecar/spawn.rs:81-84` sets `VOICE_TYPER_IPC_TOKEN` env var on sidecar. Linux same-user processes can read `/proc/<pid>/environ` to recover token.
**Related Files:** `src-tauri/src/sidecar/spawn.rs`
**Fix:** Pass token via Unix domain socket ancillary fd, pipe between parent/child, or temp file with 0600 perms that sidecar reads + unlinks. Env-var is weakest link.
**Severity:** 🟡 Medium

## XZ-R4-003 — `.lock().unwrap()` in production despite poison-safe helper (Medium)

**Status:** ❌ Not Fixed
**Description:** `state.rs:33` defines poison-safe `lock()` helper. ~10 production call sites use raw `.lock().unwrap()` instead: `main.rs:257,325,347`, `ws.rs:212`, `sidecar_cmds.rs:332,368,633,690`, `state.rs:336,362`. `panic = "abort"` in release profile means panic = abort.
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/state.rs`
**Fix:** Sweep all production `.lock().unwrap()` calls on `SidecarState` fields to use `crate::state::lock(&state.X)`. Add clippy lint to prevent regression.
**Severity:** 🟡 Medium

## XZ-R4-004 — `tauri.conf.json` shell scope allows `args: true` for sidecar (Medium)

**Status:** ❌ Not Fixed
**Description:** `tauri.conf.json:117-125` allows `"args": true` for `bin/python-sidecar`. Host only ever sends `["--ws"]`. Compromised main renderer can spawn sidecar with arbitrary CLI args.
**Related Files:**
- `src-tauri/tauri.conf.json`
- `src-tauri/capabilities/main-runtime.json`
**Fix:** Change `"args": true` to `"args": ["--ws"]`.
**Severity:** 🟡 Medium

## XZ-R4-005 — `withGlobalTauri: true` exposes full Tauri API (Medium)

**Status:** ❌ Not Fixed
**Description:** `tauri.conf.json:13` `withGlobalTauri: true` exposes all Tauri plugin APIs on `window.__TAURI__`. Tauri v2 docs recommend `false` for production. Increases XSS blast radius.
**Related Files:** `src-tauri/tauri.conf.json`
**Fix:** Set `withGlobalTauri: false`. Renderer already uses `invoke('dispatch', ...)` via `@tauri-apps/api/core`.
**Severity:** 🟡 Medium

## XZ-R4-006 — `open_logs` bypasses shell-plugin scope (Low)

**Status:** ❌ Not Fixed
**Description:** `commands/system_cmds.rs:67-97` spawns `explorer.exe`/`open`/`xdg-open` via raw `std::process::Command`, bypassing `tauri-plugin-shell`'s scope mechanism.
**Related Files:** `src-tauri/src/commands/system_cmds.rs`
**Fix:** Route through `tauri_plugin_opener` with explicit scope. OR add sanity check that `path == config_dir(&app)` before spawning (make implicit invariant explicit).
**Severity:** 🟢 Low

## XZ-R4-007 — `main.rs` is 449 LOC, exceeds ~280 wiring-only target (Low)

**Status:** ❌ Not Fixed
**Description:** `main.rs:43` docstring claims "~280 lines" but file is 449. Inline relaunch listener (48 LOC) duplicates `dispatch_frame`'s send path.
**Related Files:** `src-tauri/src/main.rs`
**Fix:** Extract relaunch listener body into `sidecar_cmds::send_relaunch_ack(state)`. Listener becomes 4-5 lines.
**Severity:** 🟢 Low

## XZ-R4-008 — `SidecarState.token` is write-only dead state held in plain String (Low)

**Status:** ❌ Not Fixed
**Description:** `state.rs:247-263` field is documented as WRITE-ONLY dead state — written at `main.rs:325` and `ft1.rs:352-353` but never read. Held in plain memory (no `zeroize`).
**Related Files:**
- `src-tauri/src/state.rs`
- `src-tauri/src/main.rs:325`
- `src-tauri/src/sidecar/ft1.rs:352-353`
**Fix:** Remove field + write sites. If retained for future, add `zeroize::Zeroizing<String>`.
**Severity:** 🟢 Low

## XZ-R4-009 — FT-1 restart counter file has no integrity protection (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar/ft1.rs:151-166` writes counter as plain JSON, no HMAC. Same-user attacker with write access to `<config_dir>/ft1_restart_counter.json` can reset count to 0 indefinitely, bypassing CR-29 breaker.
**Related Files:** `src-tauri/src/sidecar/ft1.rs`
**Fix:** Add HMAC-SHA256 over `(count, ts)` using per-install random key in separate 0600 file. Verify on read; reject if mismatch.
**Severity:** 🟢 Low

## XZ-R4-010 — Inline WS frame construction duplicates dispatch path (Low)

**Status:** ❌ Not Fixed
**Description:** `main.rs:260-265` (relaunch_ack) and `commands/bubble.rs:653-674` (toggle_dictation) construct WS frames inline, bypassing `dispatch_frame`. `id` collisions possible.
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/commands/bubble.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
**Fix:** Add `dispatch_fire_and_forget(state, cmd, data)` to `sidecar_cmds.rs`. Both call sites delegate.
**Severity:** 🟢 Low

## XZ-R4-011 — Dev-mode sidecar spawn hardcodes `RUST_LOG=debug`, skips `VOICE_TYPER_PREWARM_EXE` (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar/spawn.rs:249-260` (dev) diverges from release (`:79-84`): missing `VOICE_TYPER_PREWARM_EXE`, hardcoded `RUST_LOG=debug` overrides user setting.
**Related Files:** `src-tauri/src/sidecar/spawn.rs`
**Fix:** Mirror release env-var set in dev (add `VOICE_TYPER_PREWARM_EXE`). Only set `RUST_LOG=debug` if env var is unset.
**Severity:** 🟢 Low

## XZ-R4-012 — WS auth-read path not wrapped in `catch_unwind` (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar/ws.rs:266-382` auth wait + JSON parse + emit — NOT wrapped in `catch_unwind`. Reader/writer task bodies ARE wrapped. Asymmetry.
**Related Files:** `src-tauri/src/sidecar/ws.rs`
**Fix:** Wrap auth-read block in `AssertUnwindSafe(async {...}).catch_unwind()` with fallback calling `cleanup_and_trigger_ft1_respawn` on panic.
**Severity:** 🟢 Low

## XZ-R4-013 — `migrate.rs::copy_missing_recursive` follows symlinks (Low)

**Status:** ❌ Not Fixed
**Description:** `migrate.rs:510-545` uses `path.is_dir()` / `path.is_file()` which follow symlinks. Attacker pre-planting symlink in old Electron `models/` dir could copy `~/.ssh/id_rsa` into new config dir.
**Related Files:** `src-tauri/src/migrate.rs`
**Fix:** Use `std::fs::symlink_metadata(path)` and check `file_type().is_symlink()` — skip symlinks entirely.
**Severity:** 🟢 Low

## XZ-R4-014 — `paste_text` IPC accepts arbitrary-length text (Low)

**Status:** ❌ Not Fixed
**Description:** `commands/sidecar_cmds.rs:518-521` `PasteTextArgs { text: String }` has no length cap. WS path caps at `MAX_FRAME_BYTES = 1 MiB`, but Tauri IPC has no equivalent on renderer→host path.
**Related Files:** `src-tauri/src/commands/sidecar_cmds.rs`
**Fix:** Add `if args.text.len() > PASTE_MAX_BYTES { return Err("paste text too large".into()); }` at top of `paste_text`.
**Severity:** 🟢 Low

## XZ-R4-015 — Tray capabilities overly broad (Low)

**Status:** ❌ Not Fixed
**Description:** `capabilities/main-runtime.json:31-38` grants `core:tray:allow-set-icon`, `allow-set-tooltip`, `allow-set-title`, `allow-set-menu`, `allow-new`, `allow-remove-by-id` to main window. Rust host owns tray state; renderer doesn't need these.
**Related Files:** `src-tauri/capabilities/main-runtime.json`
**Fix:** Drop `set-icon`, `set-tooltip`, `set-title`, `set-menu`, `new`, `remove-by-id`. Keep only `core:tray:default`.
**Severity:** 🟢 Low

## XZ-R4-016 — `kill_process_tree` SIGKILL race (Low)

**Status:** ❌ Not Fixed
**Description:** `state.rs:187-241` collects descendants ONCE, then SIGKILLs 200ms later. If PID is reused during grace period, SIGKILL kills wrong process.
**Related Files:** `src-tauri/src/state.rs`
**Fix:** Before each SIGKILL, re-verify via `pgrep -P <parent_pid>` that descendant is still a child. Skip if not.
**Severity:** 🟢 Low

## XZ-R4-017 — `bubble_toggle_dictation` rate limiter uses wall-clock `SystemTime` (Low)

**Status:** ❌ Not Fixed
**Description:** `commands/bubble.rs:712-739` uses `SystemTime::now()` (NTP-skew susceptible). Malicious NTP spoof could disable rate limiter.
**Related Files:** `src-tauri/src/commands/bubble.rs`
**Fix:** Use `Instant::now()` (monotonic) with `OnceLock<Instant>` anchor.
**Severity:** 🟢 Low

## XZ-R4-018 — Stale PVT-25 TODOs (Informational)

**Status:** ❌ Not Fixed
**Description:** `commands/bubble.rs:18, 28, 650` reference PVT-25 follow-ups: F-Q9, F-S1, dispatch_fire_and_forget helper.
**Related Files:** `src-tauri/src/commands/bubble.rs`
**Fix:** Address in PVT-25 fix wave (covered by XZ-R4-010 for the dispatch helper).
**Severity:** 🟢 Low

## XZ-R4-019 — `tray.rs::on_menu_event` no pending-map size cap (Low)

**Status:** ❌ Not Fixed
**Description:** `tray.rs:219-241` spawns tokio task per click; `sidecar_cmds.rs:345-349` registers pending entry without size cap. Unresponsive sidecar + rapid clicks → 1000s of pending entries (80 bytes each, auto-expire 120s).
**Related Files:**
- `src-tauri/src/tray.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
**Fix:** Add `pending.len() > PENDING_MAX=1024` guard at `dispatch_frame:347` rejecting with `pending_full` error.
**Severity:** 🟢 Low

---

## XZ-R5-001 — Missing `setWindowOpenHandler` on both windows (Medium)

**Status:** ❌ Not Fixed
**Description:** `windows/main-window.ts:226-276` and `windows/bubble-window.ts:201-238` do NOT register `webContents.setWindowOpenHandler`. Compromised renderer can pop arbitrary external URLs in fresh BrowserWindow.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`
**Fix:** Install `wc.setWindowOpenHandler(({url}) => { if (url.startsWith("https://")) shell.openExternal(url); return {action: "deny"}; });` after creating each window.
**Severity:** 🟡 Medium

## XZ-R5-002 — CSP HTTP-header not per-window (Low)

**Status:** ❌ Not Fixed
**Description:** `bootstrap.ts:98-125` `setupCsp` registers `onHeadersReceived` on `session.defaultSession` without discriminating by `webContents`. Same CSP (with `connect-src 'self' https://api.github.com`) injected for bubble window — contradicts `csp-plugin.ts:79-96` stricter `CSP_PROD_BUBBLE`.
**Related Files:**
- `voice_typer/client/src/main/bootstrap.ts`
- `voice_typer/client/csp-plugin.ts`
**Fix:** Use `details.webContents === state.bubbleWindow?.webContents` to select per-window CSP inside `onHeadersReceived`. Import `CSP_PROD_BUBBLE` / `CSP_PROD_MAIN` from `csp-plugin.ts`.
**Severity:** 🟢 Low

## XZ-R5-003 — Allowlist doesn't separate renderer-reachable from internal-only (Medium)

**Status:** ❌ Not Fixed
**Description:** `allowed-commands.ts:50-207` `ALLOWED_COMMANDS` contains internal-only commands (`quit_app`, `heartbeat`, `relaunch_ack`, `restart_app`). Renderer's `python-call` IPC handler uses same allowlist. Compromised renderer can invoke `quit_app`, `restart_app`, `delete_all_personal_data`.
**Related Files:**
- `voice_typer/client/src/main/allowed-commands.ts`
- `voice_typer/client/src/main/python/send-to-python.ts`
- `voice_typer/client/src/main/ipc/python-call-handler.ts`
**Fix:** Split into `ALLOWED_COMMANDS_RENDERER` (safe subset) and `ALLOWED_COMMANDS_INTERNAL` (quit_app, heartbeat, relaunch_ack, restart_app). `python-call-handler.ts` checks `ALLOWED_COMMANDS_RENDERER.has(cmd)`.
**Severity:** 🟡 Medium

## XZ-R5-004 — `setMainLocale` is dead code; native dialogs always English (Medium)

**Status:** ❌ Not Fixed
**Description:** `i18n.ts:166-172` `setMainLocale` exported but NO production caller. Native Electron dialogs (`dialog.showErrorBox`, model-folder picker, export save-as) always render in English even when user selected non-English.
**Related Files:**
- `voice_typer/client/src/main/i18n.ts`
- `voice_typer/client/src/main/ipc/window-handlers.ts`
- `voice_typer/client/src/main/bootstrap.ts`
**Fix:** Re-add `i18n:set-locale` IPC handler that calls `setMainLocale`. OR read renderer's localStorage locale file from disk in `bootstrapRuntime()`. Update i18n.ts docstring.
**Severity:** 🟡 Medium

## XZ-R5-005 — Dead `_bubblePageReady` state field (Low)

**Status:** ❌ Not Fixed
**Description:** `state.ts:81, 114` — set true on `bubble:ready`, reset on close, but `showBubbleWindow()` never consults it.
**Related Files:**
- `voice_typer/client/src/main/state.ts`
- `voice_typer/client/src/main/ipc/bubble-handlers.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`
**Fix:** Delete `_bubblePageReady` (and `bubble:ready` handler). OR add read in `showBubbleWindow()` to defer `bubble:show` send until ready (with timeout fallback).
**Severity:** 🟢 Low

## XZ-R5-006 — Dead `__myPyPid` global write (Low)

**Status:** ❌ Not Fixed
**Description:** `python/start-python.ts:102` writes `globalThis.__myPyPid` for "stale-killer" that was removed per RELIABILITY-002.
**Related Files:** `voice_typer/client/src/main/python/start-python.ts`
**Fix:** Delete line 102 and comment on line 101.
**Severity:** 🟢 Low

## XZ-R5-007 — Two overlapping structured loggers (Low)

**Status:** ❌ Not Fixed
**Description:** `logging.ts:291-324` (`logger`) and `:500-513` (`log`) overlap. Both write WARN/ERROR to 5 MiB rotated files in userData (different files: `electron-main.log` vs `electron-runtime.log`). Acknowledged in DUPLICATION NOTE at `:26-34`.
**Related Files:** `voice_typer/client/src/main/logging.ts`
**Fix:** Consolidate into one logger supporting both `.info(msg, ...args)` and `.info(msg, obj)` call styles. Route all output to single `electron-main.log`.
**Severity:** 🟢 Low

## XZ-R5-008 — Duplicated defensive `require("../logging")` pattern (Low)

**Status:** ❌ Not Fixed
**Description:** `windows/main-window.ts:27-77` (51 lines) and `windows/bubble-window.ts:31-57` (27 lines) contain near-identical defensive-require blocks.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`
**Fix:** Export single `createLogger()` factory from `logging.ts` handling fallback internally.
**Severity:** 🟢 Low

## XZ-R5-009 — Single-instance stale-PID recovery PID-reuse lockout (Low)

**Status:** ❌ Not Fixed
**Description:** `single_instance.ts:91-108` uses `process.kill(pid, 0)` to check if PID exists — but doesn't verify it's Voice Typer. PID reuse → lockout until unrelated process exits.
**Related Files:** `voice_typer/client/src/main/single_instance.ts`
**Fix:** Before declaring PID "alive", verify process name via `/proc/<pid>/cmdline` (Linux), `wmic process where processid=<pid> get commandline` (Windows), `ps -p <pid> -o comm=` (macOS).
**Severity:** 🟢 Low

## XZ-R5-010 — Bubble IPC accepts non-numeric payloads without type validation (Low)

**Status:** ❌ Not Fixed
**Description:** `bubble-handlers.ts:79-116, 139-153` — TS annotations are runtime lies. `deltaX` as string → `x + deltaX` becomes concatenation → `newX = NaN` → `setPosition(NaN, NaN)` no-ops.
**Related Files:** `voice_typer/client/src/main/ipc/bubble-handlers.ts`
**Fix:** Add `if (typeof deltaX !== "number" || typeof deltaY !== "number") return;` at top of `bubble:move-by` (and `width`/`height` in `bubble:resize`).
**Severity:** 🟢 Low

## XZ-R5-011 — No Windows code-signing enforcement; no entitlements file (Medium)

**Status:** ❌ Not Fixed
**Description:** `electron-builder.yml:27-59` — signing purely env-driven (no `certificateFile`/`certificateSubjectName`). PR builds ship UNSIGNED. No macOS entitlements file. No Linux AppImage signing.
**Related Files:** `voice_typer/client/electron-builder.yml`
**Fix:** Add `win.signingHashAlgorithms: ["sha256"]`. Consider failing build if `CSC_LINK` empty when publishing. Add `mac.entitlements: resources/entitlements.mac.plist` declaring `com.apple.security.device.audio-input`. Configure AppImage GPG signing.
**Severity:** 🟡 Medium

## XZ-R5-012 — Multiple in-scope files exceed 300-line rule (Low)

**Status:** ❌ Not Fixed
**Description:** `logging.ts` (513), `bubble-window.ts` (531), `main-window.ts` (488), `bootstrap.ts` (436), `tcp-connect.ts` (321). `index.ts` (209) is OK.
**Related Files:** (multiple)
**Fix:** Split each into focused submodules. Defer if test-seam analysis shows high risk.
**Severity:** 🟢 Low

---

## XZ-R6-NH-01 — Native hotkey binary TOCTOU on watchdog respawn (Medium)

**Status:** ❌ Not Fixed
**Description:** `native_hotkeys/base.py:291-317` `_spawn_process` reuses cached `self._binary_path` on watchdog respawn WITHOUT re-running `verify_native_binary_or_skip`. Attacker swapping binary during respawn window achieves code execution.
**Related Files:** `voice_typer/server/native_hotkeys/base.py`
**Fix:** Call `verify_native_binary_or_skip(self._binary_path)` at top of `_spawn_process`. Return early / set `_failed=True` if verification fails.
**Severity:** 🟡 Medium

## XZ-R6-NH-02 — Factory discards verified binary path (Low)

**Status:** ❌ Not Fixed
**Description:** `native_hotkeys/factory.py:18-31` verifies `binary` then passes only `hotkey_str` to constructor. `base.py:89` re-discovers via `get_native_binary_path()`.
**Related Files:**
- `voice_typer/server/native_hotkeys/factory.py`
- `voice_typer/server/native_hotkeys/base.py`
**Fix:** Pass verified `binary` Path into constructor: `MacNativeHotkey(hotkey_str, binary_path=binary)`.
**Severity:** 🟢 Low

## XZ-R6-NH-03 — Manifest key mismatch disables native hotkeys in dev (Low)

**Status:** ❌ Not Fixed
**Description:** `native/binaries.json` uses arch-suffixed keys (`linux-key-listener-x86_64`). Dev tree has legacy non-suffixed `linux-key-listener`. `get_expected_sha256` returns None → `verify_native_binary_or_skip` fails closed → falls back to pynput.
**Related Files:**
- `voice_typer/server/native/binaries.json`
- `voice_typer/server/native_hotkeys/binary_path.py`
**Fix:** Add legacy-name alias lookup in `get_expected_sha256`. Or regenerate `binaries.json` with both arch-suffixed and legacy keys.
**Severity:** 🟢 Low

## XZ-R6-AS-01 — Tauri binary spawned at autostart with no integrity check (Low-Medium)

**Status:** ❌ Not Fixed
**Description:** `autostart_launcher.py:242-305` `_tauri_binary` returns path with NO hash verification. Spawned at login with user's full privileges. `VT_TAURI_BINARY` env var attack vector.
**Related Files:** `voice_typer/server/autostart_launcher.py`
**Fix:** Add `verify_tauri_binary_or_skip(path)` mirroring native hotkey pattern. Maintain `tauri-binaries.json` manifest. Call in `_spawn_tauri_host` before `subprocess.Popen`.
**Severity:** 🟢 Low

## XZ-R6-AS-02 — Electron binary not integrity-verified (Low)

**Status:** ❌ Not Fixed
**Description:** `_electron_build.py:67-80` `_electron_binary` returns path with no hash check.
**Related Files:** `voice_typer/server/_electron_build.py`
**Fix:** Optional — add hash check similar to `verify_native_binary_or_skip`. Lower priority (path typically user-writable only).
**Severity:** 🟢 Low

## XZ-R6-AS-03 — Stale Run-key cleanup uses naive command-line parsing (Low)

**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart_windows.py:401` parses registry value naively. Doesn't handle escaped quotes / multiple leading quotes.
**Related Files:** `voice_typer/server/server_platform/autostart_windows.py`
**Fix:** Use `ctypes.windll.shell32.CommandLineToArgvW` or `shlex.split(value, posix=False)`.
**Severity:** 🟢 Low

## XZ-R6-AS-04 — `.desktop` quoting doesn't escape newlines (Low)

**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart.py:71-94` `_desktop_quote` reserves `\n` but doesn't escape it inside quoted string. Could inject new .desktop fields.
**Related Files:** `voice_typer/server/server_platform/autostart.py`
**Fix:** Reject args containing `\n`/`\r` with `ValueError`.
**Severity:** 🟢 Low

## XZ-R6-AS-05 — plist f-string XML construction (Low)

**Status:** ❌ Not Fixed
**Description:** `server_platform/autostart_macos.py:116-138` and `prewarm_scheduler_posix.py:96-132` build plist via f-string + `xml.sax.saxutils.escape` (only escapes `&`, `<`, `>`). Doesn't escape `"` or `/`.
**Related Files:**
- `voice_typer/server/server_platform/autostart_macos.py`
- `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** Build plist with `xml.etree.ElementTree` (handles all escaping). Match Windows Task Scheduler pattern.
**Severity:** 🟢 Low

## XZ-R6-AS-06 — `_schtasks_elevated` cmd.exe arg quoting (Low)

**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:546` quotes args only if contains space or `&`. Doesn't escape embedded `"`. cmd.exe metacharacter injection.
**Related Files:** `voice_typer/server/task_scheduler.py`
**Fix:** Use `subprocess.list2cmdline(args)` for proper Windows arg quoting.
**Severity:** 🟢 Low

## XZ-R6-AS-07 — `SYSTEMROOT` env var trust (Low)

**Status:** ❌ Not Fixed
**Description:** `platform_launch.py:111-129` `_systemroot_notepad_path` uses `os.environ.get("SYSTEMROOT")` first. Attacker setting `SYSTEMROOT=C:\Users\attacker` could return malicious `notepad.exe`.
**Related Files:** `voice_typer/server/platform_launch.py`
**Fix:** Reverse candidate order — try hardcoded `C:\Windows\System32\notepad.exe` FIRST.
**Severity:** 🟢 Low

## XZ-R6-AS-08 — PowerShell .ps1 temp file TOCTOU (Low)

**Status:** ❌ Not Fixed
**Description:** `server_platform/desktop_shortcut.py:258-276` writes PowerShell script to temp file with `delete=False`, then `powershell -File <tmp>`. TOCTOU window.
**Related Files:** `voice_typer/server/server_platform/desktop_shortcut.py`
**Fix:** Use `subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], ...)` to avoid temp file.
**Severity:** 🟢 Low

## XZ-R6-AS-09 — `assets/logo-256.png` missing (Low)

**Status:** ❌ Not Fixed
**Description:** `server_platform/desktop_shortcut.py:85-107` `_generate_icon_ico` always returns None — `assets/logo-256.png` doesn't exist. Windows .lnk shortcuts show generic icon.
**Related Files:** `voice_typer/server/server_platform/desktop_shortcut.py`
**Fix:** Add `logo-256.png` to `voice_typer/server/assets/`. Or update path to actual PNG location.
**Severity:** 🟢 Low

## XZ-R6-AS-10 — `taskkill` timeout silent failure (Low)

**Status:** ❌ Not Fixed
**Description:** `electron_launcher.py:283-287` `terminate_electron` Windows branch catches `subprocess.TimeoutExpired` at DEBUG. Orphan Electron renderer/GPU processes if taskkill hangs.
**Related Files:** `voice_typer/server/electron_launcher.py`
**Fix:** Catch `subprocess.TimeoutExpired` explicitly and log at WARNING. Follow-up `os.kill(pid, signal.SIGTERM)` fallback.
**Severity:** 🟢 Low

## XZ-R6-AS-11 — systemd unit injection (Low)

**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:226-241` `_build_linux_service` interpolates `python` (from env var) into `ExecStart=` without escaping newlines.
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** Validate that `python` and `args` contain no newlines. Use systemd literal escaping.
**Severity:** 🟢 Low

---

## XZ-CLIP-01 — macOS/Linux password-field detection fails OPEN silently (High)

**Status:** ❌ Not Fixed
**Description:** `clipboard_target_safety.py:709-714` (macOS) and `:868-873` (Linux) catch AX/AT-SPI exceptions and `return False` (fail-open) at DEBUG. Dictation CAN reach password manager fields when AX/AT-SPI degraded.
**Related Files:** `voice_typer/server/clipboard_target_safety.py`
**Fix:** Mirror Windows CLIP-2 pattern: log at WARNING (once via dedup guard), consider fail-CLOSED for known credential-dialog heuristics. For macOS, detect `kAXErrorAPIDisabled` and surface one-shot WARNING.
**Severity:** 🔴 High

## XZ-CLIP-02 — `wl-copy` receives dictated text as CLI arg (High)

**Status:** ❌ Not Fixed
**Description:** `clipboard/linux.py:182-189` passes text as positional arg to `wl-copy`. Visible via `/proc/<pid>/cmdline` to ANY local user. Docstring claims "piped to stdin" — contradicts implementation.
**Related Files:** `voice_typer/server/clipboard/linux.py`
**Fix:** Pipe via stdin: `subprocess.run(["wl-copy"], input=text.encode("utf-8"), stdin=subprocess.PIPE, ...)`. Fix stale docstring.
**Severity:** 🔴 High

## XZ-CLIP-03 — Outer exception handler fails open (High)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:400-407` `_is_safe_paste_target` outer `except Exception` returns `True` (fail-open) for ANY exception including security-relevant ones (Win32 APIs during shutdown, broken COM init).
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Tighten outer except to only fail-open for `ImportError`/`AttributeError` (ctypes missing). For all other exceptions, fail-CLOSED (return False) and log WARNING.
**Severity:** 🔴 High

## XZ-CLIP-04 — TOCTOU re-check Windows-only (Medium)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:790-854` safe_hwnd re-check is Windows-only. macOS (`_safe_key_press`) and Linux Wayland (`wtype`) have TOCTOU window between safety check and keystroke.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Add platform-native TOCTOU re-checks: macOS — re-fetch `NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier()`; Linux — re-fetch focused AT-SPI accessible.
**Severity:** 🟡 Medium

## XZ-CLIP-05 — Overlapping paste cycles lose original clipboard (Medium)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:920-931` `_delayed_restore` defensive check can't distinguish "user copied something new" from "another paste cycle changed clipboard". User's original content silently lost when two cycles overlap.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Track cycle ownership via monotonic cycle ID or Win32 clipboard sequence number. Only skip restore if DIFFERENT owner took clipboard.
**Severity:** 🟡 Medium

## XZ-CLIP-06 — Wrong variable in seq-mismatch re-copy (Medium)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:725-726` uses `self._last_copied_text` (mutable instance state) instead of `pasted_text` (per-request value, per DP4). Concurrent `copy()` clobbers `_last_copied_text` → wrong text re-copied.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Replace `self._last_copied_text` with `expected` (or `pasted_text`) at lines 725-726. Add regression test.
**Severity:** 🟡 Medium

## XZ-CLIP-07 — Overly broad window-class block (Medium)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:302-305` `blocked_classes = {"#32770", ...}` — `#32770` is generic Win32 Dialog class (used by Open/Save As/Properties too). Blocks legitimate dictation into standard dialogs.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Remove `#32770`. Rely on UIA `IsPassword` check + `_CRED_DIALOG_CLASSES` (specific). Unify the two class sets.
**Severity:** 🟡 Medium

## XZ-CLIP-08 — `_last_copied_text` retained when `clipboard_save_restore=False` (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:148, 531, 951` — `_last_copied_text` set in `copy()` unconditionally. Cleared only in `_delayed_restore`'s finally block, which only runs when `snapshot is not None`. When `clipboard_save_restore=False`, snapshot is None → never cleared.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Clear `self._last_copied_text = ""` at end of `paste()` (in `finally` block) when `snapshot is None`.
**Severity:** 🟢 Low

## XZ-CLIP-09 — Dead production code (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:967-983` `restore_now` and `:985-1004` `_send_keystroke_sequence` — no production callers. Only tests.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Remove both methods. Update tests that exercise them to test `_safe_key_press` / snapshot-restore path instead.
**Severity:** 🟢 Low

## XZ-CLIP-10 — Race detection Windows-only (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:699-735` PLAT-CLIPRACE seq-mismatch only fires on Windows. macOS/Linux have no equivalent — stale paste if clipboard modified between copy and paste.
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** On macOS use `NSPasteboard.changeCount`. On Linux Wayland, accept residual risk and document.
**Severity:** 🟢 Low

## XZ-CLIP-11 — Unnecessary clipboard read in templates (Low)

**Status:** ❌ Not Fixed
**Description:** `templates.py:47-64` `substitute_variables` calls `_get_clipboard_text()` on EVERY template match, even when output doesn't contain `{clipboard}`.
**Related Files:** `voice_typer/server/templates.py`
**Fix:** Gate on `"{" + var + "}" in text`. Only call `_get_clipboard_text()` when `{clipboard}` template variable present.
**Severity:** 🟢 Low

## XZ-CLIP-12 — Fragile private-API import (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/windows.py:168-173` imports `INPUT`, `KEYBDINPUT`, `INPUT_union`, `SendInput` from `pynput._util.win32` (private submodule).
**Related Files:** `voice_typer/server/clipboard/windows.py`
**Fix:** Define structs inline via `ctypes.Structure` (~30 LOC). Call `SendInput` via `ctypes.windll.user32.SendInput` directly.
**Severity:** 🟢 Low

## XZ-CLIP-13 — Signal handler exit path (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/__init__.py:298-308` `_signal_restore_handler` `raise SystemExit(...)` can be caught by frameworks overriding `sys.excepthook` or running `try: except SystemExit:`.
**Related Files:** `voice_typer/server/clipboard/__init__.py`
**Fix:** Use `os._exit(128 + signum)` in fallback path AFTER `_force_restore_pending_at_exit()` has run.
**Severity:** 🟢 Low

## XZ-CLIP-14 — Redundant safety check (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:759, 773` — when `paste_delay == 0`, check #2 runs immediately after check #1 (redundant UIA round-trip).
**Related Files:** `voice_typer/server/clipboard/manager.py`
**Fix:** Guard check #2 with `if paste_delay > 0:` so it only runs after actual sleep.
**Severity:** 🟢 Low

## XZ-CLIP-15 — Duplicated credential-class sets (Low)

**Status:** ❌ Not Fixed
**Description:** `clipboard/manager.py:302` `blocked_classes` vs `clipboard_target_safety.py:236-241` `_CRED_DIALOG_CLASSES` — two different sets, ZERO overlap.
**Related Files:**
- `voice_typer/server/clipboard/manager.py`
- `voice_typer/server/clipboard_target_safety.py`
**Fix:** Unify into single `_CRED_DIALOG_CLASSES` set in `clipboard_target_safety.py`. Import in `manager.py`. Remove `#32770` (see XZ-CLIP-07).
**Severity:** 🟢 Low

---

## XZ-PRIV-01 — Audio filter state not zeroed at stop()/discard() (Medium)

**Status:** ❌ Not Fixed
**Description:** `recording/recorder.py:2536-2579` `_secure_clear_caches` zeros `_cached_resampled` and `_cached_no_resample_arr` but NOT audio processor's filter state. `AudioProcessor.reset()` only called from `Recorder.start()`. IIR `zi` arrays + RNNoise `_carry` (up to 479 samples, ~2KB at 16kHz float32) retain audio-derived residuals.
**Related Files:** `voice_typer/server/recording/recorder.py`
**Fix:** Add `if self._audio_processor is not None: self._audio_processor.reset()` to `_secure_clear_caches()`.
**Severity:** 🟡 Medium

## XZ-PRIV-02 — Streaming transcription path doesn't zero audio arrays (Medium)

**Status:** ❌ Not Fixed
**Description:** `streaming.py:119-123` `AudioWindow` holds view into snapshot array. After transcription, arrays linger in process memory until GC. Batch path in `dictation_pipeline.py:328-331` correctly calls `.fill(0)`. Streaming path does not.
**Related Files:** `voice_typer/server/streaming.py`
**Fix:** In `StreamingTranscriptionSession.finalize()`, iterate assembler's committed windows and call `window.audio.fill(0)` before letting them go out of scope. Or add `secure_clear_audio()` method to assembler.
**Severity:** 🟡 Medium

## XZ-PRIV-03 — Consent gate inconsistency (mic test path) (Low)

**Status:** ❌ Not Fixed
**Description:** `recording_controller.py:221-241` enforces `voice_biometric_consent` for dictation. `level_monitor_handlers.py:20-51` (mic test) does NOT. Test recording audio (up to 30s) captured + returned over IPC without consent.
**Related Files:**
- `voice_typer/server/handlers/level_monitor_handlers.py`
- `voice_typer/server/handlers/microphone_test_handlers.py`
- `voice_typer/server/level_monitor.py`
**Fix:** Add `voice_biometric_consent` check to `_handle_level_monitor_start` and `_handle_level_monitor_start_test_recording`. Apply `_secure_clear_array_background` to `_test_chunks` before `.clear()` in `stop_test_recording`.
**Severity:** 🟢 Low

## XZ-PRIV-04 — Per-segment DEBUG log not gated (Low)

**Status:** ❌ Not Fixed
**Description:** `transcription.py:1041-1046` logs raw segment text at DEBUG unconditionally. `hallucination.py:124-133` correctly gates via `log_transcriptions` flag and applies `redact_pii`. Transcription DEBUG log does neither.
**Related Files:** `voice_typer/server/transcription.py`
**Fix:** Gate on `getattr(self.config, "log_transcriptions", False)`, OR route through `redact_pii()`.
**Severity:** 🟢 Low

---

## XZ-PII-01 — `_crash_excepthook` logs full user speech to rotating log at CRITICAL (High)

**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:929-933` logs full `exc_info=(exc_type, exc_value, exc_tb)` at CRITICAL. `PIIRedactionFilter` only catches structured PII patterns + API-key-shaped tokens. Plain user speech in `str(exc_value)` passes through verbatim into `voice-typer.log` (5×5 MiB rotation).
**Related Files:** `voice_typer/server/crash_handler.py`
**Fix:** In `_crash_excepthook`, before logging, run `exc_value` through `redact_pii(str(exc_value))` AND `redact_secret()`. Pass redacted string as log message body. Avoid `exc_info=` or substitute redacted traceback. Gate full-traceback CRITICAL log behind `VOICE_TYPER_DEBUG=1`.
**Severity:** 🔴 High

## XZ-PII-02 — Crash marker file persistent PII archive (Medium)

**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:940-956` writes `_safe_value = str(exc_value)[:200]` to marker file — no PII redaction. Re-logged at WARNING on next startup (`:803-841`). Summary shown in tray notification (`startup_sequence.py:94`).
**Related Files:**
- `voice_typer/server/crash_handler.py`
- `voice_typer/server/startup_sequence.py`
**Fix:** Apply `redact_pii()` + `redact_secret()` to `_safe_value` before writing marker file. For tray notification, show only `exc_type.__name__` + thread name + timestamp.
**Severity:** 🟡 Medium

## XZ-PII-03 — `redact_pii()` doesn't redact API keys (Medium)

**Status:** ❌ Not Fixed
**Description:** `security.py:266-268` `redact_pii` only applies `_PATTERNS` (email, IBAN, phone, SSN, CC). Internal `_redact_text` calls both `_PATTERNS` and `redact_secret`. `llm_polish.py:215-217` docstring claims "API keys" covered — false.
**Related Files:**
- `voice_typer/server/security.py`
- `voice_typer/server/llm_polish.py`
- `voice_typer/server/cloud_engines.py`
**Fix:** Make `redact_pii()` also call `redact_secret(text)` and `redact_url(text)` to match `_redact_text`. Single source of truth.
**Severity:** 🟡 Medium

## XZ-PII-04 — Electron TCP invalid-JSON log leaks transcription (Medium)

**Status:** ❌ Not Fixed
**Description:** `tcp-connect.ts:203` `console.error("Invalid JSON from Python:", line)` logs raw TCP line, which may contain `transcription_final` events with user speech.
**Related Files:** `voice_typer/client/src/main/python/tcp-connect.ts`
**Fix:** Replace with `console.error("[TCP] invalid JSON from Python, skipping line (len=%d)", line.length)`. Gate debug preview behind `process.env.VOICE_TYPER_DEBUG` + redaction helper.
**Severity:** 🟡 Medium

## XZ-PII-05 — LLM polish failure log doesn't redact (Low)

**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:886-887` `log.warning("[LLM_POLISH] Polish failed: %s", exc)` doesn't apply explicit redaction. Inner `LLMPolisher.polish()` at `llm_polish.py:167` does wrap with `redact_secret`.
**Related Files:** `voice_typer/server/dictation_pipeline.py`
**Fix:** Wrap with `redact_secret(str(exc))` for parity.
**Severity:** 🟢 Low

## XZ-PII-06 — Cloud engines inconsistent redaction (Low)

**Status:** ❌ Not Fixed
**Description:** `cloud_engines.py:654-658, 784-787` generic `Exception` branches use only `redact_secret(str(exc))` — skip `redact_url`. `HTTPError`/`URLError` branches at `:614, 639, 753, 774` correctly use both.
**Related Files:** `voice_typer/server/cloud_engines.py`
**Fix:** Use `redact_secret(redact_url(str(exc)))` consistently in all four branches.
**Severity:** 🟢 Low

## XZ-PII-07 — Log retention: no time-based purge (Low)

**Status:** ❌ Not Fixed
**Description:** `log.py:674-680` rotation is size-only (5 MiB × 5). No `TimedRotatingFileHandler`, no startup sweep. Compare to `crash_handler._sweep_stale_diagnostics` (30-day mtime cutoff for crash diagnostics).
**Related Files:** `voice_typer/server/log.py`
**Fix:** Add startup sweep (mirror `_sweep_stale_diagnostics`) deleting `voice-typer.log.*` rotations older than 30 days. OR switch to `TimedRotatingFileHandler` with daily rotation + 30-day retention.
**Severity:** 🟢 Low

## XZ-PII-08 — `log_rate_limit` eager render before level check (Low)

**Status:** ❌ Not Fixed
**Description:** `log_rate_limit.py:166-168` comment claims "lazy formatting preserved" but `msg % args` runs eagerly before `logger.debug()`. Wasted CPU on every suppressed call.
**Related Files:** `voice_typer/server/log_rate_limit.py`
**Fix:** Either accept trade-off and fix misleading comment, OR restructure to preserve lazy formatting.
**Severity:** 🟢 Low

---

## XZ-R10-01 — Windows reparse-point check is dead code (High)

**Status:** ❌ Not Fixed
**Description:** `secure_file_io.py:127-141` `_secure_read_text` Windows branch: `raise OSError(...)` raised inside `try` block is immediately caught by same `try`'s `except (AttributeError, OSError): pass`. Repoint protection does not exist on Windows.
**Related Files:** `voice_typer/server/secure_file_io.py`
**Fix:** Split `try` so reparse-point `raise` is NOT covered by tolerant `except`:
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

## XZ-R10-02 — `.bak` backup write bypasses `_secure_atomic_write` (High)

**Status:** ❌ Not Fixed
**Description:** `config.py:1114-1129` `_save_locked` backup uses `Path.write_bytes` — follows symlinks on destination, not atomic, no fsync, brief 0o644 window before `os.chmod(bak_path, 0o600)`.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Use `_secure_atomic_write(bak_path, content.decode("utf-8"))` for backup too.
**Severity:** 🔴 High

## XZ-R10-03 — Pre-migration backup uses `shutil.copy2` (High)

**Status:** ❌ Not Fixed
**Description:** `config.py:1297-1308` `shutil.copy2(config_file, pre_bak)` follows symlinks on destination, non-atomic, no fsync. Same vulnerability class as XZ-R10-02.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Read source via `_secure_read_text(config_file)` and write via `_secure_atomic_write(pre_bak, raw_text)`.
**Severity:** 🔴 High

## XZ-R10-04 — `_write_plaintext_fallback` lock-free read-modify-write (High)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-SEC-02 — `credential_store.py:721-770` does read-modify-write on `config.json` without acquiring `config.json.lock`.
**Related Files:** `voice_typer/server/credential_store.py`
**Fix:** Folded into XZ-SEC-02 fix.
**Severity:** 🔴 High

## XZ-R10-05 — `_validate_non_numeric_fields` overwrites migration warnings (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:1946` `data["_load_warnings"] = warnings` (fresh local list) overwrites migration-added list. Migration warnings silently dropped from `instance.last_load_warnings`.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Use `data.setdefault("_load_warnings", []).extend(warnings)`.
**Severity:** 🟡 Medium

## XZ-R10-06 — `save()` doesn't catch `TypeError` from `json.dumps` (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:1110` `json.dumps(data, indent=2)` can raise `TypeError` (non-JSON-serializable value like set/datetime). `save()`'s `except` tuple is `(TimeoutError, OSError, PermissionError)` — `TypeError` propagates. Docstring says "never raises".
**Related Files:** `voice_typer/server/config.py`
**Fix:** Widen `save()`'s `except` to `(TimeoutError, OSError, PermissionError, TypeError, ValueError)` and return False.
**Severity:** 🟡 Medium

## XZ-R10-07 — `_migrate_from_legacy` uses non-atomic `shutil.copytree` (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:347-364` `shutil.copytree` is non-atomic, file-by-file. Interrupted migration leaves partial target dir. Called from `logging_setup._setup_logging()` at every startup.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Copy to staging dir (`target.with_suffix(".migrating")`) via `shutil.copytree`, then atomically rename via `os.replace`. On failure, clean up staging. Add O_NOFOLLOW checks.
**Severity:** 🟡 Medium

## XZ-R10-08 — Windows config file ACLs not enforced (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:1088-1092` `_save_locked` chmod is `if not is_windows():` no-op on Windows. `_secure_atomic_write` mkstemp inherits parent dir DACL. If `%APPDATA%` shared or `VOICE_TYPER_CONFIG_DIR` set to shared location, config.json (with plaintext API keys) world-readable.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/secure_file_io.py`
**Fix:** On Windows, after `os.replace`, explicitly set file ACL via `win32security.SetFileSecurity` (or `icacls /inheritance:r /grant:r "%USERNAME%:F"`). VALIDATE ON WINDOWS HOST.
**Severity:** 🟡 Medium

## XZ-R10-09 — Deprecated fields never actually leave config.json (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:524-547` v3 migration prunes 9 keys, but dataclass still declares them. `asdict(self)` re-serializes them with defaults. v3 "prune" cosmetically ineffective.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Add v4 migration that prunes ALL fields marked "DEPRECATED". Remove from IPC_CONFIG_ALLOWLIST. Remove from `_validate_non_numeric_fields`. Remove from TS type. Bump `_CURRENT_SCHEMA_VERSION` to 4.
**Severity:** 🟡 Medium

## XZ-R10-10 — `.corrupt-<timestamp>` 1-second resolution collides (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:1628-1641` corrupt-config quarantine uses `int(time.time())` (1s resolution). Two corrupt loads in same second overwrite.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Add PID or microsecond suffix: `f"config.json.corrupt-{int(time.time())}-{os.getpid()}"`. Or use `time.time_ns()`.
**Severity:** 🟢 Low

## XZ-R10-11 — Lock file no `O_NOFOLLOW` (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:397` (POSIX) and `:440` (Windows) — `os.open(lock_file, O_CREAT|O_RDWR, 0o600)` no `O_NOFOLLOW`. Symlink attack. Lock fail-open on open error.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Add `os.O_NOFOLLOW` to POSIX `os.open` flags. On Windows, add `os.lstat` reparse-point check before opening. Consider fail-closed when lock can't be acquired.
**Severity:** 🟢 Low

## XZ-R10-12 — `_secure_read_text` no file-size cap (Low)

**Status:** ❌ Not Fixed
**Description:** `secure_file_io.py:116, 137` `f.read()` reads entire file with no size limit. Memory-exhaustion DoS if config.json replaced with huge file.
**Related Files:** `voice_typer/server/secure_file_io.py`
**Fix:** Add `max_bytes` parameter (default 4 MB) and read in chunks, raising `ValueError` if exceeded. Or use `os.fstat` to check size before reading.
**Severity:** 🟢 Low

## XZ-R10-13 — `config.py` is 2002-line monolith (Low)

**Status:** ❌ Not Fixed
**Description:** 8+ distinct concerns in one file. `_validate_systemroot` (104 lines) out of place. `Config.load()` is 482 lines in single try/except.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Split into `config_paths.py`, `config_lock.py`, `config_migrations.py`, `config_dataclass.py`, `config_loader.py`, `config_saver.py`. Move `_validate_systemroot` to `env_validation.py`.
**Severity:** 🟢 Low

## XZ-R10-14 — Stale `save_strict` docstring (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:1153-1155` claims wiring `apply_config` to call `save_strict` is "follow-up task". `config_applier.py:638` already calls it.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Update docstring to reflect CR-97 wiring is done.
**Severity:** 🟢 Low

## XZ-R10-15 — `custom_theme: dict` not type-validated on load (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:824` `custom_theme: dict | None = None` NOT in any of bool/str/int/float field sets. Hand-edited config with `"custom_theme": [...]` or `42` round-trips without complaint.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Add `dict_fields = {"custom_theme"}` set to `_validate_non_numeric_fields`. Coerce non-dict values to None with warning.
**Severity:** 🟢 Low

---

## XZ-R11-01 — `save_vocabulary_with_diff` doesn't update in-memory VocabularyManager (High)

**Status:** ❌ Not Fixed
**Description:** `service.py:1110-1161` writes user vocabulary JSON file directly. Live `self._app._vocabulary_manager._data` NEVER touched. `dictation_pipeline.py:812-816` uses stale in-memory state until app restart.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/vocabulary.py`
**Fix:** After `_secure_atomic_write` in `save_vocabulary_with_diff`, reload live manager: `with live_vm._lock: live_vm._load_and_merge()`. OR refactor to delegate to live VocabularyManager CRUD methods.
**Severity:** 🔴 High

## XZ-R11-02 — `history.db.corrupt-<ts>` survives GDPR delete (Medium)

**Status:** ❌ Not Fixed
**Description:** `history_db.py:906-919` corrupt DB renamed (not deleted) — contains dictated text. `_GDPR_PERSONAL_FILES` only lists `history.db`, `history.db-wal`, `history.db-shm`. No glob for `history.db.corrupt-*`.
**Related Files:**
- `voice_typer/server/service.py` (GDPR delete + export)
- `voice_typer/server/history_db.py`
**Fix:** Add `"history.db.corrupt-*"` glob to both `delete_all_personal_data` and `export_gdpr_bundle`. Optional: rotation (keep most-recent N snapshots).
**Severity:** 🟡 Medium

## XZ-R11-03 — Migration "duplicate column name" handler bumps version without verifying (Medium)

**Status:** ❌ Not Fixed
**Description:** `history_db.py:798-811` — if V2 migration's first ALTER fails on "duplicate column name: favorite", handler treats whole migration as complete and bumps version. `language` column NEVER added. Subsequent `add_transcription(text, language="en")` fails silently.
**Related Files:** `voice_typer/server/history_db.py`
**Fix:** Replace blanket "duplicate column name → done" heuristic with per-column existence checks via `PRAGMA table_info(transcriptions)`. Run only the missing ALTERs.
**Severity:** 🟡 Medium

## XZ-R11-04 — No encryption at rest for dictated text (Medium)

**Status:** ❌ Not Fixed
**Description:** `history_db.py` stores dictated `text` in plaintext. File perms 0o600 / dir 0o700, `secure_delete=ON`, GDPR delete unlinks after checkpoint. But while running (or after unclean shutdown before checkpoint), text recoverable by same-user/root.
**Related Files:** `voice_typer/server/history_db.py`
**Fix:** Consider optional SQLCipher integration gated behind user setting. OR application-layer encryption of `text` column with key from OS keystore. At minimum document threat model in `docs/privacy/`. VALIDATE ON WINDOWS/MACOS HOST (file-perm mitigations are POSIX-only).
**Severity:** 🟡 Medium

## XZ-R11-05 — Dead code in `_save_user` (Low)

**Status:** ❌ Not Fixed
**Description:** `vocabulary.py:232-273` second retry block unreachable — `final_exc` is always set if loop didn't return. Trailing comment claims "best-effort" but live code raises.
**Related Files:** `voice_typer/server/vocabulary.py`
**Fix:** Delete lines 232-273. Keep only live first-loop + raise.
**Severity:** 🟢 Low

## XZ-R11-06 — `TemplateManager` has no lock (Low)

**Status:** ❌ Not Fixed
**Description:** `templates.py:84-391` — no `threading.Lock`. `match()` iterates `self._templates` while CRUD methods mutate in place. Same race CR-23 fixed for vocabulary.
**Related Files:** `voice_typer/server/templates.py`
**Fix:** Add `self._lock = threading.Lock()`. Guard `add`/`update`/`delete`/`import_json`/`_save`/`_load`/`match`/`export_json`. In `match`, snapshot `self._templates` under lock before iterating.
**Severity:** 🟢 Low

## XZ-R11-07 — IPC cap (1024) bypasses SEC-011 limits (200/500) (Low)

**Status:** ❌ Not Fixed
**Description:** `vocabulary_handlers.py:94` `_max_value_len = 1024` looser than `vocabulary.py:46-47` `MAX_PATTERN_LENGTH=200`/`MAX_REPLACEMENT_LENGTH=500`. `save_vocabulary_with_diff` bypasses CRUD methods.
**Related Files:**
- `voice_typer/server/handlers/vocabulary_handlers.py`
- `voice_typer/server/service.py` (save_vocabulary_with_diff)
**Fix:** Lower IPC `_max_value_len` to 500. OR enforce per-category limits inside `save_vocabulary_with_diff` before writing.
**Severity:** 🟢 Low

## XZ-R11-08 — WAL/SHM files not chmod'd after lazy creation (Low)

**Status:** ❌ Not Fixed
**Description:** `history_db.py:609-617` chmod loop runs in `_open_write_conn` BEFORE WAL/SHM created by first write. Files later created with default umask (0o644 = world-readable on Linux).
**Related Files:** `voice_typer/server/history_db.py`
**Fix:** After first write materializing WAL/SHM, re-run chmod loop. OR set `os.umask(0o077)` at process startup.
**Severity:** 🟢 Low

## XZ-R11-09 — `_handle_restore_history` no payload-size cap (Low)

**Status:** ❌ Not Fixed
**Description:** `history_handlers.py:120-143` — no `max_payload_bytes`, no `max_value_len`. Compare with vocabulary (1 MB + 1024 chars) and templates (256 KB + 1024 chars).
**Related Files:** `voice_typer/server/handlers/history_handlers.py`
**Fix:** Add `"_payload": {"max_payload_bytes": 256 * 1024}` and `"record": {"type": dict, "required": True}` to schema.
**Severity:** 🟢 Low

## XZ-R11-10 — `save_vocabulary_with_diff` throwaway manager (Low)

**Status:** ❌ Not Fixed
**Description:** `service.py:1122-1123` constructs fresh `VocabularyManager()` per IPC call, loads bundled + user from disk, builds merged `_data`, then immediately discards. Double file read.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Reuse live `self._app._vocabulary_manager`. Compute diff against `mgr._data` (already merged).
**Severity:** 🟢 Low

## XZ-R11-11 — Missing `PRAGMA foreign_keys=ON` (Low)

**Status:** ❌ Not Fixed
**Description:** `history_db.py:586-618` writer connection doesn't set `foreign_keys=ON`. Current schema has no FK constraints, so no-op today. Latent footgun if FKs added.
**Related Files:** `voice_typer/server/history_db.py`
**Fix:** Add `conn.execute("PRAGMA foreign_keys=ON")` to `_open_write_conn`.
**Severity:** 🟢 Low

---

## XZ-R12-01 — Onboarding auto-heal ignores `.onboarding_started` marker (High)

**Status:** ❌ Not Fixed
**Description:** `startup_sequence.py:151-183` auto-heal fires whenever `not onboarding_completed AND config.json exists`. `onboarding.py:123-176` added `mark_started()` + `.onboarding_started` marker to prevent clobbering in-progress wizard. Gate never added.
**Related Files:**
- `voice_typer/server/startup_sequence.py`
- `voice_typer/server/onboarding.py`
**Fix:** In `startup_sequence.py:165`, add `started_marker = _config_dir() / ".onboarding_started"; if config_file.exists() and not started_marker.exists():` for auto-heal. Otherwise save default config and defer to wizard.
**Severity:** 🔴 High

## XZ-R12-02 — `_migrate_from_legacy` non-atomic (High)

**Status:** ❌ Not Fixed
**Description:** `config.py:347-364` `shutil.copytree(legacy, target, dirs_exist_ok=True)` non-atomic, file-by-file. Interrupted migration leaves partial target. Same as XZ-R10-07.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Folded into XZ-R10-07 fix.
**Severity:** 🔴 High

## XZ-R12-03 — `migrate.rs` sentinel written after partial failures (High)

**Status:** ❌ Not Fixed
**Description:** `migrate.rs:296-304` writes sentinel marker UNCONDITIONALLY after all steps, even if individual steps (config, history.db, recovery) failed. Next launch: `migration_marker.exists()` → early-return. User's history.db / recovery.json / config.json NOT migrated.
**Related Files:** `src-tauri/src/migrate.rs`
**Fix:** Track `migration_failed` counter. Only write sentinel if ALL critical steps succeeded. If any failed, retry on next launch (no sentinel) OR write `.migration-partial` marker + surface user notification.
**Severity:** 🔴 High

## XZ-R12-04 — Model file copy non-atomic (Medium)

**Status:** ❌ Not Fixed
**Description:** `migrate.rs:504-546` `std::fs::copy` not atomic, no fsync. Combined with `if dst_path.exists() { continue; }` guard, partial file looks "existing" on next launch — corrupt model file in target.
**Related Files:** `src-tauri/src/migrate.rs`
**Fix:** Use atomic copy (temp+fsync+rename) for model files too. OR write `.partial` marker file alongside destination, skip/rewrite on next launch if marker exists. At minimum fsync after `std::fs::copy`.
**Severity:** 🟡 Medium

## XZ-R12-05 — `VOICE_TYPER_RESTART` bypass dead code (Medium)

**Status:** ❌ Not Fixed
**Description:** `single_instance.py:12-14, 203-205, 232-236` docstrings claim 30s time-limited restart bypass. Actual code never reads `VOICE_TYPER_RESTART` or calls `security.verify_restart_token()`. Function is dead in production.
**Related Files:**
- `voice_typer/server/single_instance.py`
- `voice_typer/server/security.py`
**Fix:** Either implement bypass in `_ensure_single_instance` by calling `security.verify_restart_token()`, OR delete dead SEC-001 infrastructure and update docstrings.
**Severity:** 🟡 Medium

## XZ-R12-06 — Lockfile name mismatch (Medium)

**Status:** ❌ Not Fixed
**Description:** `single_instance.py:21` docstring says `voice-typer.lock`; `:441` code creates `backend.lock`. Docstring claims `fcntl.flock` is primary; actual code uses `O_CREAT|O_EXCL` primary with flock as secondary.
**Related Files:** `voice_typer/server/single_instance.py`
**Fix:** Update docstring to match code: lockfile is `backend.lock`, primary mechanism is `O_CREAT|O_EXCL` with stale-PID recovery, flock is defense-in-depth.
**Severity:** 🟡 Medium

## XZ-R12-07 — Prewarm path parsing bug (Medium)

**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:62-69` parses dev fallback `f'"{exe}" -m voice_typer.server.prewarm'` via `resolved.split(" ", 1)[0].strip('"')`. Fails when Python path contains space (common on macOS `/Users/My Name/...`).
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** Use `shlex.split(resolved)[0]` to parse dev-fallback command line.
**Severity:** 🟡 Medium

## XZ-R12-08 — Onboarding fail counter in-memory only (Medium)

**Status:** ❌ Not Fixed
**Description:** `startup_sequence.py:194-209` `_onboarding_fail_count` is `app` attribute (in-memory only). Counter resets on every process restart. "After 3 failures" circuit breaker only trips if all 3 in same session.
**Related Files:** `voice_typer/server/startup_sequence.py`
**Fix:** Persist fail counter to `.onboarding_fail_count` file with timestamp. Reset on successful completion.
**Severity:** 🟡 Medium

## XZ-R12-09 — Prewarm scheduler unit files non-atomic (Medium)

**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:140, 270-271` `Path.write_text` (truncate-then-write). systemd/launchd refuse to load corrupt unit file on next reload. Silent failure.
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** Use `_secure_atomic_write` for plist and unit files.
**Severity:** 🟡 Medium

## XZ-R12-10 — Linux prewarm no immediate start (Medium)

**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:265-292` Linux registration `systemctl --user enable` but NOT `start`. Timer only fires at next boot. macOS path correctly does `launchctl load` (starts immediately).
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** After `enable`, also run `systemctl --user start voice-typer-prewarm.timer` (best-effort, non-fatal).
**Severity:** 🟡 Medium

## XZ-R12-11 — `merge_config` docstring vs implementation mismatch (Low)

**Status:** ❌ Not Fixed
**Description:** `migrate.rs:312-316` docstring promises per-key mtime resolution. Implementation uses single whole-file mtime to decide ALL overlapping keys.
**Related Files:** `src-tauri/src/migrate.rs`
**Fix:** Update docstring to match implementation: "the entire newer file's values win for overlapping keys".
**Severity:** 🟢 Low

## XZ-R12-12 — `migrate.rs` parse-failure fail-open (Low)

**Status:** ❌ Not Fixed
**Description:** `migrate.rs:344-357` corrupt source `config.json` silently treated as `Value::Null` (empty). User's old Electron settings silently dropped. No backup preserved.
**Related Files:** `src-tauri/src/migrate.rs`
**Fix:** Before treating as Null, copy corrupt source to `config.json.corrupt-pre-migration.<timestamp>.bak`. Surface user notification.
**Severity:** 🟢 Low

## XZ-R12-13 — FT-1 docstring stale (Low)

**Status:** ❌ Not Fixed
**Description:** `ft1.rs:22-27` claims "disk-persisted counter". `main.rs:318` resets to 0 on every fresh launch. Counter only counts within-session.
**Related Files:**
- `src-tauri/src/sidecar/ft1.rs`
- `src-tauri/src/main.rs:318`
**Fix:** Either remove `main.rs:318` reset, OR update `ft1.rs` docstring to say "counter is reset on every fresh app launch; breaker only protects against within-session flapping".
**Severity:** 🟢 Low

## XZ-R12-14 — Prewarm sentinel fail-open (Low)

**Status:** ❌ Not Fixed
**Description:** `prewarm/paths.py:109-130` `_already_warmed` returns False when `_boot_time()` returns None (no psutil + no GetTickCount64 fallback on POSIX). Prewarm re-runs on EVERY trigger.
**Related Files:** `voice_typer/server/prewarm/paths.py`
**Fix:** Add POSIX fallback for `_boot_time()` using `os.popen("uptime -s")` or `/proc/stat` btime (Linux) / `sysctl -n kern.boottime` (macOS). Or fall back to comparing sentinel's mtime to process start time.
**Severity:** 🟢 Low

## XZ-R12-15 — Re-onboarding marker inconsistency (Low)

**Status:** ❌ Not Fixed
**Description:** `startup_tasks.py:438-440` IPC `reset_onboarding_complete` only deletes `.onboarding_complete`. `onboarding.py:188-193` `OnboardingController.reset` deletes both. IPC handler leaves stale `.onboarding_started`.
**Related Files:**
- `voice_typer/server/startup_tasks.py`
- `voice_typer/server/onboarding.py`
**Fix:** Have `reset_onboarding_complete` delegate to `OnboardingController.reset()`, or at minimum also delete `.onboarding_started`.
**Severity:** 🟢 Low

## XZ-R12-16 — `crash_recovery.__del__` racy read (Low)

**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:513` `if self._entries:` reads without acquiring `self._lock`. Concurrent `add()` could mutate.
**Related Files:** `voice_typer/server/crash_recovery.py`
**Fix:** Acquire `self._lock` for the check, OR always call `_save_sync()` unconditionally.
**Severity:** 🟢 Low

## XZ-R12-17 — `history.db` WAL sidecar migration loses data (Low)

**Status:** ❌ Not Fixed
**Description:** `migrate.rs:252-266` copies WAL/SHM sidecars independently. If `-wal` copy fails, target has `history.db` without WAL → committed-but-uncheckpointed transactions lost.
**Related Files:** `src-tauri/src/migrate.rs`
**Fix:** If either sidecar copy fails, delete target sidecars (SQLite starts fresh) AND log user-visible warning. OR use SQLite `.backup` API.
**Severity:** 🟢 Low

## XZ-R12-18 — Prewarm unregister leaves in-flight service (Low)

**Status:** ❌ Not Fixed
**Description:** `prewarm_scheduler_posix.py:295-317` stops TIMER, not in-flight SERVICE. An in-flight oneshot continues. Unit files unlinked while service still running.
**Related Files:** `voice_typer/server/prewarm_scheduler_posix.py`
**Fix:** Also run `systemctl --user stop voice-typer-prewarm.service` (best-effort) before unlinking unit files.
**Severity:** 🟢 Low

---

## XZ-CFG-01 — `disabled_backends` Config field missing (High)

**Status:** ❌ Not Fixed
**Description:** `asr_registry.py:64, 308` reads/writes `config.disabled_backends` via `getattr`/`setattr`. Config dataclass has NO such field. `asdict(self)` doesn't serialize dynamic attrs. Persistence is fictitious — disabled backends re-enabled on every restart.
**Related Files:**
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py` (IPC allowlist)
**Fix:** Add `disabled_backends: list[str] = field(default_factory=list)` to Config dataclass. Add to `IPC_CONFIG_ALLOWLIST` with list-of-strings validator. Remove defensive `try/except` in `_persist_disabled`.
**Severity:** 🔴 High

## XZ-CFG-02 — `ALLOWED_USER_MODELS` frozen at 8 entries while `MODEL_REGISTRY` has 18 (High)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:43-52` `ALLOWED_USER_MODELS` has 8 entries. `model_registry.py:98-363` `MODEL_REGISTRY` has 18. `Config.load()` silently resets `model_size` to "small.en" for 10 unlisted models. Tray menu allows selecting them but IPC validator rejects.
**Related Files:**
- `voice_typer/server/config_validators.py`
- `voice_typer/server/model_registry.py`
- `voice_typer/server/config.py` (Config.load reset)
- `voice_typer/client/src/renderer/src/types/config.ts` (ModelSize type)
- `tests/test_config.py` (stale test pins bug)
**Fix:** Derive `ALLOWED_USER_MODELS = frozenset(MODEL_REGISTRY.keys())` at import time. Update or remove stale regression test. Update TS `ModelSize` to `string` (or generated union). Add WARNING log on Config.load reset.
**Severity:** 🔴 High

## XZ-CFG-03 — TS declares `bubble_x`/`bubble_y`/`bubble_scale`/`test_duration_seconds` Python doesn't have (High)

**Status:** ❌ Not Fixed
**Description:** `config.ts:135-164` declares 4 fields (2 required) that have no Python counterpart. Renderer calls `set_config({ bubble_x, bubble_y })` — IPC validator drops as unknown. User's bubble position setting silently lost. `bubble_x`/`bubble_y` declared REQUIRED (not optional).
**Related Files:**
- `voice_typer/client/src/renderer/src/types/config.ts`
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
**Fix:** Either add fields to Python Config dataclass + IPC_CONFIG_ALLOWLIST, OR mark `@deprecated` in TS and remove from interface. Make `bubble_x`/`bubble_y` optional (`?:`). Add CI parity test diffing TS interface keys against Python dataclass fields.
**Severity:** 🔴 High

## XZ-CFG-04 — `validate_config()` is dead code (High)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:984-1044` `validate_config(cfg) -> list[str]` defined but NEVER called in production. Docstring says "Agent 2-a is coordinated to call it" — stale cross-agent TODO. `Config.load()` never runs full-config validator. Hand-edited config with `noise_suppression_method="speex"` loads silently.
**Related Files:**
- `voice_typer/server/config_validators.py`
- `voice_typer/server/config.py`
**Fix:** Call `validate_config(instance)` at end of `Config.load()` (after `apply_preset`), append errors to `instance.last_load_warnings`, log at WARNING. Add regression test loading config with out-of-range float.
**Severity:** 🔴 High

## XZ-CFG-05 — TS `DEFAULT_CONFIG` fixture drift (Medium)

**Status:** ❌ Not Fixed
**Description:** `__tests__/helpers/fixtures.ts:32-187` `DEFAULT_CONFIG` differs from Python Config defaults in 30+ fields. `llm_preset: "default"` is invalid (not in Python Literal). `schema_version: 1` vs Python `_CURRENT_SCHEMA_VERSION = 3`.
**Related Files:**
- `voice_typer/client/src/renderer/src/__tests__/helpers/fixtures.ts`
- `voice_typer/server/config.py`
**Fix:** Replace hand-maintained fixture with fetch from server's `get_defaults` IPC in test setup hook. OR add CI parity test importing Python Config defaults. Fix `llm_preset` to valid value ("professional").
**Severity:** 🟡 Medium

## XZ-CFG-06 — TS types looser/tighter than Python validators (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.ts:243` includes `"speex"` for `noise_suppression_method` — Python doesn't accept. `:221-228` `audio_preset` includes legacy `"none"`/`"recommended"` — IPC validator excludes. `:103` `llm_preset: string` — IPC enforces enum. `:1-6` `ModelSize` missing `tiny`/`small`/`medium`.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/config.ts`
- `voice_typer/server/config_validators.py`
- `voice_typer/server/config.py`
**Fix:** Generate TS types from Python dataclass + IPC_CONFIG_ALLOWLIST via build-time script. Short term: remove `"speex"`, `"none"`, `"recommended"` from TS; change `llm_preset` to union; add missing `ModelSize` values.
**Severity:** 🟡 Medium

## XZ-CFG-07 — Hand-maintained type-coercion lists (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:1645-1947` `_validate_non_numeric_fields` has 4 hand-maintained sets (110+ entries). `int_fields` OMITS `schema_version`. `STARTUP-6` comment shows prior misclassification.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Derive coercion lists from `cls.__dataclass_fields__` and field type annotation at class-init time. OR replace with `__post_init__` running each field through type-coercion based on annotation.
**Severity:** 🟡 Medium

## XZ-CFG-08 — Deprecated fields still in dataclass + IPC allowlist + TS type (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:852-948` 10+ deprecated fields still declared. v3 migration pruned 9 keys but left others. IPC allowlist includes some (e.g. `silence_rms_threshold`). TS type includes them.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
- `voice_typer/client/src/renderer/src/types/config.ts`
**Fix:** Add v4 migration pruning ALL deprecated fields. Remove from IPC_CONFIG_ALLOWLIST. Remove from `_validate_non_numeric_fields`. Remove from TS type (mark `@deprecated` for one release). Bump `_CURRENT_SCHEMA_VERSION` to 4.
**Severity:** 🟡 Medium

## XZ-CFG-09 — `push_to_talk_hotkey` settable but ignored (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:657-658` declares field. `config_validators.py:682` in IPC allowlist. TS comment at `:59-81` says "DEAD/UNUSED — server never reads it". Hotkey listener uses main `hotkey` for PTT. User sets PTT hotkey expecting it to gate mic — silently doesn't work.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
- `voice_typer/client/src/renderer/src/types/config.ts`
**Fix:** Either remove from IPC_CONFIG_ALLOWLIST, OR have validator reject non-empty values with "not yet implemented; use main hotkey field", OR implement the feature.
**Severity:** 🟡 Medium

## XZ-CFG-10 — `apply_preset` silently reverts user toggles (Medium)

**Status:** ❌ Not Fixed
**Description:** `config.py:1594-1601` runs `apply_preset(instance.audio_preset, instance)` on every `Config.load()`. When `audio_preset != "custom"`, overwrites 7 filter toggle fields with preset values. User toggles `noise_filter_highpass: False` via IPC, restart → preset overwrites to `True`. No warning.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/audio_presets.py`
- `voice_typer/server/config_applier.py`
- `voice_typer/server/service.py` (apply_config)
**Fix:** In `service.apply_config`, when individual filter toggle is in `validated` AND `audio_preset != "custom"`, auto-switch `audio_preset` to "custom" (with INFO log) before writing.
**Severity:** 🟡 Medium

## XZ-CFG-11 — Pre-migration backup filename collides (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:1297-1308` `config.json.pre-migration-v{loaded_version}.bak` — no timestamp. Downgrade-then-upgrade overwrites first backup.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Add timestamp: `config.json.pre-migration-v{loaded_version}-{int(time.time())}.bak`. Cap retained backups to 3.
**Severity:** 🟢 Low

## XZ-CFG-12 — Inconsistent unknown-key logging (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:1214-1221` `Config.load()` unknown keys logged at WARNING. `config_validators.py:918-923` IPC validator unknown keys logged at DEBUG.
**Related Files:**
- `voice_typer/server/config.py`
- `voice_typer/server/config_validators.py`
**Fix:** Promote IPC validator unknown-key log to WARNING (matching Config.load). OR keep DEBUG but add renderer toast when `rejected_keys` non-empty.
**Severity:** 🟢 Low

## XZ-CFG-13 — `schema_version` missing from `int_fields` (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:1783-1796` `int_fields` set OMITS `schema_version` (an int field). Latent — current code always sets `schema_version` to int before construction.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Add `"schema_version"` to `int_fields`. OR derive int_fields from dataclass annotations (XZ-CFG-07).
**Severity:** 🟢 Low

## XZ-CFG-14 — Insecure default `llm_api_url` (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:696` `llm_api_url: str = "https://api.openai.com/v1/chat/completions"` pre-fills OpenAI. `cloud_api_url` defaults to empty (safer). Inconsistent.
**Related Files:** `voice_typer/server/config.py`
**Fix:** Change `llm_api_url: str = ""` (empty) to match `cloud_api_url`. Renderer should require URL before `llm_polish` can be enabled.
**Severity:** 🟢 Low

## XZ-CFG-15 — `last_load_warnings` undocumented IPC field (Low)

**Status:** ❌ Not Fixed
**Description:** `history_bounds.py:74-83` `_sanitize_config_for_ipc` returns `config.__dict__.copy()` including `last_load_warnings`. TS `VoiceTyperConfig` doesn't declare it.
**Related Files:**
- `voice_typer/server/ipc/history_bounds.py`
- `voice_typer/client/src/renderer/src/types/config.ts`
**Fix:** Add `last_load_warnings?: string[] | null` to TS type. OR exclude from `_sanitize_config_for_ipc` and expose via separate IPC field.
**Severity:** 🟢 Low

---

## XZ-EH-001 — `get_volume_backend_status` returns `str(exc)` to IPC (High)

**Status:** ❌ Not Fixed
**Description:** `service.py:820` returns `{"reason": str(exc)}` unredacted to IPC layer. `status_handlers._handle_get_volume_backend_status` passes straight to renderer. Sister methods (`delete_model`, `test_llm_connection`, `export_diagnostics`, `export_gdpr_bundle`, `force_cancel_transcription`) correctly call `redact_secret(redact_url(str(exc)))`.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Replace `"reason": str(exc)` with `"reason": redact_secret(redact_url(str(exc)))`. Add `log.warning(...)` before return.
**Severity:** 🔴 High

## XZ-EH-002 — `onboarding_apply` returns `str(exc)` to IPC (High)

**Status:** ❌ Not Fixed
**Description:** `service.py:1452-1453` returns `{"error": str(exc)}` unredacted. Handler `onboarding_handlers.py:233-244` passes straight to renderer. Handler's own `except Exception` never fires because service swallows.
**Related Files:**
- `voice_typer/server/service.py`
- `voice_typer/server/handlers/onboarding_handlers.py`
**Fix:** Change service methods to `raise` so handler's CR-20 catch-all fires. OR at minimum apply `redact_secret(redact_url(str(exc)))` and log server-side at ERROR before returning.
**Severity:** 🔴 High

## XZ-EH-003 — `import_model` returns `str(exc)` per-model to IPC (High)

**Status:** ❌ Not Fixed
**Description:** `service.py:1599-1600` `errors.append({"model": model_name, "error": str(exc)})`. `shutil.Error` string form enumerates source/dest file paths (leaks cache layout).
**Related Files:** `voice_typer/server/service.py`
**Fix:** Wrap each `str(exc)` with `redact_secret(redact_url(str(exc)))`. Log per-model error at WARNING server-side.
**Severity:** 🔴 High

## XZ-EH-004 — `download_model` leaks `str(exc)` in 3 places (High)

**Status:** ❌ Not Fixed
**Description:** `service.py:2229-2231` — `_push_progress(0, f"Download failed: {exc}")` (IPC event), `_notify(APP_NAME, f"Failed to download {model_name}: {exc}")` (tray notification), `return {"success": False, "error": str(exc)}` (IPC response). Three concurrent leaks on same error path.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Wrap all three interpolations with `redact_secret(redact_url(str(exc)))`. `_notify` should use friendly error mapping.
**Severity:** 🔴 High

## XZ-EH-005 — Silent `except Exception: pass` in `level_monitor_start` (Medium)

**Status:** ❌ Not Fixed
**Description:** `service.py:670` `update_level_processor` failures silently swallowed. User sees "level monitor started" but audio filters may not be applied.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Replace `pass` with `log.debug("[SERVICE] level_monitor_start: update_level_processor failed", exc_info=True)`.
**Severity:** 🟡 Medium

## XZ-EH-006 — Silent `except Exception: pass` in download progress polling (Medium)

**Status:** ❌ Not Fixed
**Description:** `service.py:2094` poll loop catches any exception silently. Progress bar freezes for that iteration with no log.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Replace `pass` with `log.debug("[SERVICE] download progress poll failed (non-fatal)", exc_info=True)`.
**Severity:** 🟡 Medium

## XZ-EH-007 — Silent `except Exception: pass` in cache invalidation (Low)

**Status:** ❌ Not Fixed
**Description:** `service.py:1611` `invalidate_model_availability_cache` failure silently swallowed. Sister calls at `:989, 2145, 2204` log at DEBUG.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Replace `pass` with `log.debug("[SERVICE] import_model: invalidate_model_availability_cache failed", exc_info=True)`.
**Severity:** 🟢 Low

## XZ-EH-008 — Silent `except Exception: pass` × 2 in `_check_resources` (Medium)

**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:467-468` (RAM check) and `:578-579` (GPU check) silently swallow. Docstring at `:407-409` claims "failures logged at DEBUG" — code uses `pass`.
**Related Files:** `voice_typer/server/dictation_pipeline.py`
**Fix:** Replace `pass` with `log.debug("[RESOURCE] ... check failed (non-fatal)", exc_info=True)`. Fix docstring. Drop redundant `ImportError` from `(ImportError, Exception)`.
**Severity:** 🟡 Medium

## XZ-EH-009 — Silent OSError in Windows registry probe (Medium)

**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:331-333` `_is_prewarm_registered_registry` `except OSError: return False` with no log. Siblings `_register_prewarm_registry` and `_unregister_prewarm_registry` log at WARNING.
**Related Files:** `voice_typer/server/task_scheduler.py`
**Fix:** Add `log.debug("[TASK] _is_prewarm_registered_registry: OSError reading HKCU Run key: %s", exc)` before return False.
**Severity:** 🟡 Medium

## XZ-EH-010 — Silent OSError in elevated-schtasks output read (Medium)

**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:581-585` returns `(exit_code.value, "")` if temp-file read fails. "Access is denied" detection silently fails. `WaitForSingleObject` return value discarded. `GetExitCodeProcess` boolean return not checked.
**Related Files:** `voice_typer/server/task_scheduler.py`
**Fix:** Replace `except OSError: pass` with `log.debug(...)`. Check `WaitForSingleObject` return (WAIT_TIMEOUT=124, WAIT_FAILED=1). Check `GetExitCodeProcess` boolean.
**Severity:** 🟡 Medium

## XZ-EH-011 — Silent `except Exception` in POSIX prewarm probe (Low)

**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:562-566` `is_prewarm_registered` POSIX delegate wrapped in `except Exception: return False` with no log. Siblings `register_prewarm_task` (line 681) and `unregister_prewarm_task` (line 728) DO log.
**Related Files:** `voice_typer/server/task_scheduler.py`
**Fix:** Change to `except Exception as exc: log.warning("[TASK] POSIX is_prewarm_registered raised: %s", exc); return False`.
**Severity:** 🟢 Low

## XZ-EH-012 — Silent `except Exception: pass` in onboarding start marker write (Low)

**Status:** ❌ Not Fixed
**Description:** `onboarding_handlers.py:79-84` `.onboarding_started` marker write silently swallowed. Comment claims "non-critical" but PVT-006 rationale says it prevents auto-heal clobbering.
**Related Files:** `voice_typer/server/handlers/onboarding_handlers.py`
**Fix:** Replace `pass` with `log.debug("[IPC] onboarding_start: mark_started failed (auto-heal may reset on restart)", exc_info=True)`.
**Severity:** 🟢 Low

## XZ-EH-013 — Inconsistent error envelopes in model handlers (Medium)

**Status:** ❌ Not Fixed
**Description:** `model_handlers.py:67-69, 211-213, 245-246, 281-283` manual error envelopes omit `code` field. Contract in `handlers/_base.py:117-127` and `ipc/validation.py:97-122` requires `code` (e.g. `client.missing_field`, `client.not_found`).
**Related Files:** `voice_typer/server/handlers/model_handlers.py`
**Fix:** Replace each manual envelope with `_error_response(resp, ..., code="client.missing_field")` (or `client.not_found`). Better: change schema to `"required": True` and let `_validate_dict_payload` emit canonical envelope.
**Severity:** 🟡 Medium

## XZ-EH-014 — `not_found` code uses legacy non-namespaced form (Low)

**Status:** ❌ Not Fixed
**Description:** `status_handlers.py:200, 319` use `code="not_found"` (legacy). G4-M-22 registry lists `client.not_found` as canonical.
**Related Files:** `voice_typer/server/handlers/status_handlers.py`
**Fix:** Change `code="not_found"` to `code="client.not_found"` at both call sites.
**Severity:** 🟢 Low

## XZ-EH-015 — Implicit ack-vs-error contract is fragile (Medium)

**Status:** ❌ Not Fixed
**Description:** `onboarding_handlers.py:27-40, 154, 185, 209, 224, 239` — 5 handlers delegate ack-vs-error to whether service's return dict contains `"error"` key. If service returns `{"error": None}` (falsy but present), handler reports `ack` for failure.
**Related Files:**
- `voice_typer/server/handlers/onboarding_handlers.py`
- `voice_typer/server/service.py` (onboarding methods)
**Fix:** Migrate per documented PVT-G5-095 plan: service should `raise` on failure (typed `OnboardingError`), handler let propagate to outer `except Exception` which calls `_respond_with_error`. Eliminates implicit dict-key contract.
**Severity:** 🟡 Medium

## XZ-EH-016 — Fragile `os.fdopen` double-close in `qwen_engine.py` (Medium)

**Status:** ❌ Not Fixed
**Description:** `qwen_engine.py:181-187` `with os.fdopen(fd, ...) as f: json.load(f)` — if `json.load` raises, `with`'s `__exit__` closes `fd`. Outer `except Exception:` then calls `os.close(fd)` again — double-close. Suppressed by `contextlib.suppress(OSError)`.
**Related Files:** `voice_typer/server/qwen_engine.py`
**Fix:** Restructure: call `os.close(fd)` ONLY if `os.fdopen` itself raised:
```python
fd = os.open(...)
try:
    f = os.fdopen(fd, "r", encoding="utf-8")
except Exception:
    with contextlib.suppress(OSError):
        os.close(fd)
    raise
with f:
    json.load(f)
```
**Severity:** 🟡 Medium

## XZ-EH-017 — `i18n.t` interpolates raw exception into tray notification (Low)

**Status:** ❌ Not Fixed
**Description:** `app.py:728-729` `i18n.t("notify.app.undo_failed", error=e)` interpolates raw exception into user-facing tray notification. pynput failures include OS-level error strings, X11 paths, AT-SPI addresses.
**Related Files:** `voice_typer/server/app.py`
**Fix:** Pass fixed user-friendly message: `i18n.t("notify.app.undo_failed")` (omit `error=` kwarg). Raw `str(e)` already captured in `log.warning`.
**Severity:** 🟢 Low

## XZ-EH-018 — Unbounded `subprocess.Popen().wait()` holds IPC lock (Low)

**Status:** ❌ Not Fixed
**Description:** `app.py:860` `_open_config_file` Windows notepad path: `subprocess.Popen([notepad, config_file]).wait()` no timeout. Holds `_config_mutation_lock` indefinitely. Subsequent `set_config` IPC calls block forever.
**Related Files:** `voice_typer/server/app.py`
**Fix:** Add watchdog `subprocess.Popen(...).wait(timeout=600)` with `TimeoutExpired` handler notifying user.
**Severity:** 🟢 Low

## XZ-EH-019 — `service.py` is 2818-line god-class (Medium)

**Status:** ❌ Not Fixed
**Description:** `VoiceTyperService` class spans ~2650 lines. 13 distinct functional domains in one class. GDPR delete (234 lines), download_model (600 lines), onboarding (325 lines) are themselves god-methods.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Extract domain controllers (`GdprController`, `DownloadController`, `OnboardingServiceDelegate`, `MicrophoneTestDelegate`) following `SettingsController` / `AudioQualityController` pattern. Multi-PR refactor.
**Severity:** 🟡 Medium

## XZ-EH-020 — `contextlib.suppress(Exception)` too broad in task scheduler (Low)

**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:704` `with contextlib.suppress(Exception): _schtasks(["/Delete", ...])`. Catches `TypeError`, `AttributeError`, `ImportError` — all silently swallowed.
**Related Files:** `voice_typer/server/task_scheduler.py`
**Fix:** Narrow to `contextlib.suppress(subprocess.SubprocessError, OSError)`. Use `try/except` with `log.debug` for visibility.
**Severity:** 🟢 Low

## XZ-EH-021 — `volume_ducker.initialize()` failure logged at DEBUG (Low)

**Status:** ❌ Not Fixed
**Description:** `service.py:812-814` `log.debug("volume_ducker.initialize failed", exc_info=True)` — invisible at default INFO. Polled every ~2s.
**Related Files:** `voice_typer/server/service.py`
**Fix:** Use notify-once pattern: log first occurrence at WARNING, then DEBUG for subsequent.
**Severity:** 🟢 Low

## XZ-EH-022 — `_check_resources` docstring drift (Low)

**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:407-409` promises "failures logged at DEBUG level" — code uses `pass` (XZ-EH-008).
**Related Files:** `voice_typer/server/dictation_pipeline.py`
**Fix:** Folded into XZ-EH-008 fix.
**Severity:** 🟢 Low

## XZ-EH-023 — `_schtasks_elevated` lacks log for empty-output case (Low)

**Status:** ❌ Not Fixed
**Description:** `task_scheduler.py:575-585` full elevated-schtasks path has zero log lines. Sibling `_schtasks` (line 481-499) logs WARNING on `FileNotFoundError` and ERROR on `TimeoutExpired`.
**Related Files:** `voice_typer/server/task_scheduler.py`
**Fix:** Add `log.debug` or `log.warning` for each failure mode per XZ-EH-010.
**Severity:** 🟢 Low

---

## XZ-R16-01 — Rust poisonable mutexes (High)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-R4-003 — `state.rs:33` poison-safe `lock()` helper exists but ~10 production call sites use raw `.lock().unwrap()`. Panic while holding `state.ws_tx`/`state.child`/`state.token` aborts (release `panic = "abort"`).
**Related Files:**
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar/ws.rs`
- `src-tauri/src/commands/sidecar_cmds.rs`
- `src-tauri/src/state.rs`
**Fix:** Folded into XZ-R4-003 fix.
**Severity:** 🔴 High

## XZ-R16-02 — `ft1_failed` event unconsumed by renderer (High)

**Status:** ❌ Not Fixed
**Description:** `ft1.rs:200-201` emits `ft1_failed` when FT-1 exhausts 5 respawn attempts. `python-namespace.ts:65-98` only synthesizes `ft1_relaunching` + `ft1_reconnected`. `useConnection.ts:276-294` sets `"restarting"` on `reconnecting`, only exits via `reconnected`. After `ft1_failed`, renderer UI stuck on "Restarting…" forever.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts`
**Fix:** Add `makeListener` for `"ft1_failed"` synthesizing `{type: "error", data: {message: "FT-1 respawn exhausted"}}`. In `useConnection`, subscribe and call `setConnectionStatus("disconnected")` + `setLastError(t("connection.ft1Failed"))`. Add "Relaunch app" button to disconnected UI.
**Severity:** 🔴 High

## XZ-R16-03 — Tauri/Electron error envelope inconsistency (Medium)

**Status:** ❌ Not Fixed
**Description:** `usePython.ts:188-205` Electron path throws `Error` objects. Tauri path: `invoke` rejects with raw string (Tauri v2 behavior). Callers using `err instanceof Error ? err.message : String(err)` work; `Microphone.tsx:278` and `lib/utils/models.ts:252` don't — lose server error on Tauri.
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/usePython.ts`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts`
**Fix:** Wrap `withCommandTimeout` call in try/catch normalizing rejection to `Error`:
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

## XZ-R16-04 — Silent `get_config` catch (Medium)

**Status:** ❌ Not Fixed
**Description:** `useConnection.ts:166-173` outer catch swallows `get_config` error with no `console.error`/`console.warn`. Inner `get_status` (line 116) was fixed by G4-H-22.
**Related Files:** `voice_typer/client/src/renderer/src/hooks/useConnection.ts`
**Fix:** Add `console.warn("[IPC] get_config connection probe failed (attempt ${retries}/${maxRetries}):", err)`.
**Severity:** 🟡 Medium

## XZ-R16-05 — `usePythonEvent` handler not wrapped (Medium)

**Status:** ❌ Not Fixed
**Description:** `usePython.ts:294-303` `currentCleanup = handlerRef.current(event.data)` NOT wrapped in try/catch. Throwing handler escapes into Tauri/Electron dispatch. `currentCleanup` never updated (stale persists).
**Related Files:** `voice_typer/client/src/renderer/src/hooks/usePython.ts`
**Fix:** Wrap: `try { currentCleanup = handlerRef.current(event.data); } catch (err) { console.error("usePythonEvent handler threw:", err); currentCleanup = undefined; }`.
**Severity:** 🟡 Medium

## XZ-R16-06 — ErrorBoundary "Try Again" can loop (Medium)

**Status:** ❌ Not Fixed
**Description:** `ErrorBoundary.tsx:130-139` `handleReset` clears error state but NOT underlying poisoned state (localStorage, malformed theme token). React re-renders same children against same state → same crash.
**Related Files:** `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx`
**Fix:** Rename "Try Again" to "Retry render" with tooltip. OR have `handleReset` clear localStorage keys known to feed render. OR only show "Try Again" after "Reset settings" attempted.
**Severity:** 🟡 Medium

## XZ-R16-07 — `globalErrorHandler.test.ts` listener leak (Low)

**Status:** ❌ Not Fixed
**Description:** `globalErrorHandler.test.ts:38-53` `_resetGlobalErrorHandlerStateForTests()` only resets `_installed` flag. Real listeners NEVER removed between tests. `vi.spyOn` + `mockRestore` doesn't remove listeners. After test N, `window` has `2*N` accumulated listeners.
**Related Files:** `voice_typer/client/src/renderer/src/lib/__tests__/globalErrorHandler.test.ts`
**Fix:** Track installed listeners and remove in `afterEach`:
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

## XZ-R16-08 — Sound manager silent failures (Low)

**Status:** ❌ Not Fixed
**Description:** `sound-manager.ts:145, 181, 296, 365, 371` — 5 catch blocks swallow audio failures with no log.
**Related Files:** `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
**Fix:** Add `console.debug("[sound-manager] <specific failure>", err)` to each catch.
**Severity:** 🟢 Low

## XZ-R16-09 — Logging prefix inconsistency (Low)

**Status:** ❌ Not Fixed
**Description:** Renderer logs use mixed prefixes: `[Renderer]`, `[ErrorBoundary]`, `[tauri-bridge]`, `[bubble IPC]`, `[IPC]`, or no prefix.
**Related Files:** Multiple renderer files
**Fix:** Adopt single `[renderer:<module>]` convention. Mechanical sweep.
**Severity:** 🟢 Low

## XZ-R16-10 — Dead `_formatReasonForConsole` wrapper (Low)

**Status:** ❌ Not Fixed
**Description:** `globalErrorHandler.ts:130-132` is trivial passthrough to `_formatForConsole`.
**Related Files:** `voice_typer/client/src/renderer/src/lib/globalErrorHandler.ts`
**Fix:** Inline the call. Delete `_formatReasonForConsole`.
**Severity:** 🟢 Low

## XZ-R16-11 — Dead `SidecarState.token` field (Low)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-R4-008.
**Related Files:** `src-tauri/src/state.rs`
**Fix:** Folded into XZ-R4-008 fix.
**Severity:** 🟢 Low

## XZ-R16-12 — Brittle redacted-sentinel match in ErrorBoundary (Low)

**Status:** ❌ Not Fixed
**Description:** `ErrorBoundary.tsx:236-248` filters `"<redacted>"` via string equality. If backend changes sentinel format, filter fails silently and reset writes sentinel string as actual API key.
**Related Files:** `voice_typer/client/src/renderer/src/components/feedback/ErrorBoundary.tsx`
**Fix:** Either export sentinel constant from backend and import in renderer, OR skip ALL keys whose current value matches `/^<redacted.*>$/i` / is null / is empty string for known secret fields.
**Severity:** 🟢 Low

---

## XZ-R17-01 — FT-1 circuit breaker never trips (High)

**Status:** ❌ Not Fixed
**Description:** `ft1.rs:192-215` + `main.rs:318` — three counter-reset paths defeat breaker: (a) success → write(0); (b) exhaustion → `app.restart()` → new process → main.rs:318 writes 0; (c) any fresh launch → main.rs:318 writes 0. Persistently-flapping sidecar triggers infinite `ft1_respawn` cycles without surfacing `ft1_failed`.
**Related Files:**
- `src-tauri/src/sidecar/ft1.rs`
- `src-tauri/src/main.rs:318`
**Fix:** Decouple counter from success-reset. Decay counter (reduce by 1 every 60s of uptime). OR count DISTINCT crash events and reset only after sustained uptime threshold (5 min). Remove `main.rs:318` reset OR make conditional on "clean exit" flag.
**Severity:** 🔴 High

## XZ-R17-02 — Hotkey callbacks no `_shutting_down` guard (High)

**Status:** ❌ Not Fixed
**Description:** `hotkey_dispatcher.py:220-226, 235-241, 279-295` callbacks call `toggle_dictation()` without checking `_shutting_down`. `recording_controller.py:148-201` `_toggle_impl` can START recording during shutdown. `shutdown_controller.py:436-475` stops backends with 5s timeout each but doesn't null refs.
**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/recording_controller.py`
- `voice_typer/server/shutdown_controller.py`
**Fix:** Add `_shutting_down` guard at TOP of `_dictation_callback`, `_esc_callback`, `_repaste_callback`, `_toggle_impl`. In `_do_cleanup`, after stopping each backend, set attribute to None (use `hotkey_dispatcher.stop_all()`).
**Severity:** 🔴 High

## XZ-R17-03 — `_crash_excepthook` writes marker non-atomically (Medium)

**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:940-956` `marker_path.write_text(content)` — truncate-then-write, default umask (022 on Linux → 0644 = world-readable). Compare to `crash_recovery.py:191` and `duck_crash_recovery.py:68` which use `_secure_atomic_write`.
**Related Files:** `voice_typer/server/crash_handler.py`
**Fix:** Use `_secure_atomic_write(marker_path, content)` — atomic write + O_NOFOLLOW + 0o600 on POSIX.
**Severity:** 🟡 Medium

## XZ-R17-04 — VEH handler only captures 4 of ~15 fatal Windows exception codes (Medium)

**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:59-71` `_CRASH_CODES` only includes STATUS_HEAP_CORRUPTION, STATUS_ACCESS_VIOLATION, STATUS_STACK_BUFFER_OVERRUN, STATUS_FATAL_APP_EXIT. Missing: STATUS_IN_PAGE_ERROR (0xC0000006), STATUS_ILLEGAL_INSTRUCTION (0xC000001D), STATUS_PRIVILEGED_INSTRUCTION (0xC0000096), STATUS_DATATYPE_MISALIGNMENT (0xC0000002), STATUS_BREAKPOINT (0x80000003), STATUS_SINGLE_STEP (0x80000004), etc.
**Related Files:** `voice_typer/server/crash_handler.py`
**Fix:** Expand `_CRASH_CODES` to include all fatal SEH codes. Add friendly names. Update `summary_parts` logic in `report_pending_crash`.
**Severity:** 🟡 Medium

## XZ-R17-05 — `duck_crash_recovery.save()` fire-and-forget (Medium)

**Status:** ❌ Not Fixed
**Description:** `duck_crash_recovery.py:53-70` save() called AFTER volume reduced. If write fails (disk full, permissions, NFS hang), crash recovery file NOT written. App crash → next launch `load_stale()` returns None → system volume NEVER restored. User's speakers stuck at 25%.
**Related Files:** `voice_typer/server/duck_crash_recovery.py`
**Fix:** (1) Retry save() up to 3 times with 100ms backoff. (2) If all retries fail, DO NOT duck — call `VolumeDucker.restore()` immediately. (3) Surface tray notification. OR persist duck state BEFORE ducking volume.
**Severity:** 🟡 Medium

## XZ-R17-06 — Windows logoff/shutdown: OS kills process before `_do_cleanup` finishes (Medium)

**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:963-1007` `_win32_console_handler` spawns `quit()` on daemon thread. `_do_cleanup` cumulative worst-case ~85s. Windows CTRL_LOGOFF/SHUTDOWN gives ~5 seconds.
**Related Files:** `voice_typer/server/shutdown_controller.py`
**Fix:** Add fast-path for `ctrl_logoff_event`/`ctrl_shutdown_event` that skips non-critical cleanup, runs ONLY critical path (crash_recovery.flush, history_db.flush, recorder.stop/discard, _clear_backend_pid_file, CloseHandle) with 1s timeouts each. Target <3s total.
**Severity:** 🟡 Medium

## XZ-R17-07 — SIGTERM during startup race (Medium)

**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:706-757` `quit()` checks `is_main = threading.current_thread() is threading.main_thread()`. Signal watcher spawns on daemon thread → `is_main` is False → `sys.exit(0)` never called. If signal arrives BEFORE main thread enters `tray.run()`, main thread continues startup with torn-down subsystems → None-reference crashes.
**Related Files:** `voice_typer/server/shutdown_controller.py`
**Fix:** After `_do_cleanup()` returns in `quit()`, if NOT on main thread, call `os._exit(0)` as last resort (after cleanup_done flag set). OR set flag that main thread checks at key startup milestones.
**Severity:** 🟡 Medium

## XZ-R17-08 — `_save_sync` redundant chmod per transcription (Low)

**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:177-191` `os.chmod(self._path.parent, 0o700)` called every save (after every transcription). Idempotent but wasteful syscall.
**Related Files:** `voice_typer/server/crash_recovery.py`
**Fix:** Guard with "first-run" flag: `if not self._dir_ensured: ... self._dir_ensured = True`.
**Severity:** 🟢 Low

## XZ-R17-09 — `_save_loop` doesn't `task_done()` for None sentinel (Low)

**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:268-292` worker breaks out on None sentinel without calling `task_done()`. Latent — `Queue.join()` would block forever (currently unused).
**Related Files:** `voice_typer/server/crash_recovery.py`
**Fix:** Add `self._save_queue.task_done()` before `break`.
**Severity:** 🟢 Low

## XZ-R17-10 — `duck_crash_recovery.load_stale()` doesn't clear file (Low)

**Status:** ❌ Not Fixed
**Description:** `duck_crash_recovery.py:72-96` returns saved state but does NOT clear file. If caller crashes between `load_stale()` and `clear()`, file persists. Next launch restores same state again — potentially to WRONG level.
**Related Files:** `voice_typer/server/duck_crash_recovery.py`
**Fix:** Add "consumed" flag to file. On next launch, if "consumed" is true, return None. OR clear file inside `load_stale()` and have caller re-save if restore fails.
**Severity:** 🟢 Low

## XZ-R17-11 — `_do_cleanup` doesn't null hotkey backend refs (Low)

**Status:** ❌ Not Fixed
**Description:** `shutdown_controller.py:436-475` stops backends individually (with timeouts) but doesn't null refs. `hotkey_dispatcher.stop_all()` exists and nulls them, but NOT called from shutdown path.
**Related Files:** `voice_typer/server/shutdown_controller.py`
**Fix:** Replace individual stop calls with `app.hotkeys.stop_all()` (wrapped in `_run_with_timeout`). OR keep individual stops AND add `setattr(app.hotkeys, "_hotkey_backend", None)` etc.
**Severity:** 🟢 Low

## XZ-R17-12 — Stale TODO Fix-A in `hotkey_dispatcher.py` (Low)

**Status:** ❌ Not Fixed
**Description:** `hotkey_dispatcher.py:51-58` references `ipc/server.py:1022` which doesn't exist. Actual file is `ipc_server.py`. Bug already fixed (file renamed, attribute updated).
**Related Files:** `voice_typer/server/hotkey_dispatcher.py`
**Fix:** Delete TODO Fix-A comment block. Replace with one-line comment.
**Severity:** 🟢 Low

## XZ-R17-13 — `__del__` and `atexit` redundant double-write (Low)

**Status:** ❌ Not Fixed
**Description:** `crash_recovery.py:96-113` (atexit) + `:484-516` (`__del__`) both call `_save_sync()` during shutdown. Both serialized by `_save_lock`. Redundant atomic-write.
**Related Files:** `voice_typer/server/crash_recovery.py`
**Fix:** Add `_final_save_done` flag both paths check. OR remove `__del__` entirely — atexit is documented safety net.
**Severity:** 🟢 Low

## XZ-R17-14 — `python_crash.*.txt` world-readable on POSIX (Low)

**Status:** ❌ Not Fixed
**Description:** `crash_handler.py:940-956` `write_text` uses default umask (0644). File lives in config_dir root (typically 0755 on multi-user systems). Contains `exc_value` (truncated to 200 chars) — can include user speech fragments.
**Related Files:** `voice_typer/server/crash_handler.py`
**Fix:** Folded into XZ-R17-03 fix (use `_secure_atomic_write` for 0o600).
**Severity:** 🟢 Low

---

## XZ-R18-01 — `relaunch_electron` vs `relaunch_app` event name mismatch (High)

**Status:** ❌ Not Fixed
**Description:** `app.py:1041` publishes `{"type": "relaunch_app"}`. `handle-message.ts:105` listens for `"relaunch_electron"`. PVT-2 renamed Python side but NOT Electron TCP path. `relaunch_ack` NEVER sent → `_wait_for_relaunch_ack(timeout=2.0)` ALWAYS times out (2s delay per restart). PERF-005 event-driven ack path is dead code on Electron.
**Related Files:**
- `voice_typer/client/src/main/python/handle-message.ts`
- `voice_typer/server/app.py` (stale docstrings at :980, 985, 993, 1026, 1050, 1144)
**Fix:** Change `msg.type === "relaunch_electron"` to `msg.type === "relaunch_app"` (or accept both for backward compat). Update stale docstrings in `app.py`.
**Severity:** 🔴 High

## XZ-R18-02 — Partial-failure in `_clean_text`/`_apply_punctuation` loses transcription (Medium)

**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:198, 213` — `_clean_text()` and `_apply_punctuation()` are the only two middle-pipeline steps NOT wrapped in try/except. If either throws, exception propagates to outer `run()` → tray error + abort. Transcription NEVER saved to crash recovery because `_store_result()` (line 233) runs AFTER.
**Related Files:** `voice_typer/server/dictation_pipeline.py`
**Fix:** Wrap `_clean_text()` and `_apply_punctuation()` in try/except matching `_apply_vocabulary` pattern: `log.warning(...)` + notify-once + return original text.
**Severity:** 🟡 Medium

## XZ-R18-03 — `stop-python.ts` missing SIGKILL fallback (Medium)

**Status:** ❌ Not Fixed
**Description:** `stop-python.ts:38-43` sends SIGTERM only, immediately nulls `state.pythonProcess`. Stuck Python (in C extension) → orphaned process. Holds single-instance mutex → next launch fails. `relaunch-app.ts:67-76` has correct SIGKILL fallback pattern.
**Related Files:** `voice_typer/client/src/main/python/stop-python.ts`
**Fix:** Add SIGKILL fallback matching `relaunch-app.ts`. Do NOT null `state.pythonProcess` inside kill timer — wait for `exit` event.
**Severity:** 🟡 Medium

## XZ-R18-04 — Early-exit dialog misleading for non-single-instance crashes (Medium)

**Status:** ❌ Not Fixed
**Description:** `start-python.ts:134-137` shows "Only one instance can run" dialog for ALL early exits — including missing model, port collision, token mismatch, syntax error, OOM.
**Related Files:** `voice_typer/client/src/main/python/start-python.ts`
**Fix:** Include actual exit code in dialog ("Python backend exited early (code=N). Check logs."). OR read Python's last stderr lines for more specific message. At minimum log exit code prominently.
**Severity:** 🟡 Medium

## XZ-R18-05 — LLM polish failure silent (Medium)

**Status:** ❌ Not Fixed
**Description:** `dictation_pipeline.py:886-887` catches but does NOT notify. `llm_polish.py:163-168` same pattern. User pays for LLM API never used, or believes feature broken without diagnostic.
**Related Files:**
- `voice_typer/server/dictation_pipeline.py`
- `voice_typer/server/llm_polish.py`
**Fix:** Add notify-once pattern (matching `_apply_vocabulary`). Publish `{"type": "llm_polish_failed"}` to event bus for renderer toast.
**Severity:** 🟡 Medium

## XZ-R18-06 — Sidecar WS allows multiple simultaneous authenticated connections (Medium)

**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:462-514` `_handle_connection` — NO check for existing authenticated connection. Old + new connections coexist during overlap window. Both have separate `outbound` queues + writer tasks + event_bus subscribers.
**Related Files:** `voice_typer/server/sidecar_ws.py`
**Fix:** Track `server._active_ws_connection` (set on auth, cleared in `finally`). If new connection authenticates while one is active, log warning and close OLD one (or reject new with 1008).
**Severity:** 🟡 Medium

## XZ-R18-07 — `_tcpStartupTimeoutTimer` not reset by `relaunchApp()` (Low)

**Status:** ❌ Not Fixed
**Description:** `tcp-connect.ts:29, 43` — module-level timer, NOT on `state`. `relaunchApp()` (dev mode) resets other state but NOT this timer. Premature timeout dialog + unexpected quit after manual restart during slow first connect.
**Related Files:** `voice_typer/client/src/main/python/tcp-connect.ts`
**Fix:** Move `_tcpStartupTimeoutTimer` onto `state` so `relaunchApp()` and `stopPython()` can clear it. OR call `clearTcpStartupTimeout()` at top of `relaunchApp()`.
**Severity:** 🟢 Low

## XZ-R18-08 — Cloud engine fallback no user-visible signal (Low)

**Status:** ❌ Not Fixed
**Description:** `cloud_engines.py:484-489` logs WARNING but never surfaces to user. Cloud outage invisible until user checks logs.
**Related Files:** `voice_typer/server/cloud_engines.py`
**Fix:** Publish `{"type": "cloud_fallback_used", "data": {"provider": self.provider}}` to event_bus for renderer toast.
**Severity:** 🟢 Low

## XZ-R18-09 — Dead triple `loop = asyncio.get_running_loop()` (Low)

**Status:** ❌ Not Fixed
**Description:** `sidecar_ws.py:534, 550, 560` — three assignments of running loop in same function. First stores on `server._ws_loop` (never read back). Second is dead local. Only third is captured by `_push_to_ws` closure.
**Related Files:** `voice_typer/server/sidecar_ws.py`
**Fix:** Folded into XZ-IPC-008 fix.
**Severity:** 🟢 Low

## XZ-R18-10 — Duplicated kill/cleanup logic (Low)

**Status:** ❌ Not Fixed
**Description:** `stop-python.ts:38-51` vs `relaunch-app.ts:56-113` (dev) and `:150-193` (prod) — kill-Python + clear-state pattern repeated 3× with subtle inconsistencies (SIGKILL fallback — see XZ-R18-03).
**Related Files:**
- `voice_typer/client/src/main/python/stop-python.ts`
- `voice_typer/client/src/main/python/relaunch-app.ts`
**Fix:** Extract shared `_killPythonAndResetState()` helper. Call from `stopPython()`, `relaunchApp()` dev branch, `relaunchApp()` prod branch.
**Severity:** 🟢 Low

## XZ-R18-11 — No max-retries/cooldown on `relaunchApp()` (Low)

**Status:** ❌ Not Fixed
**Description:** `relaunch-app.ts:200-201` production branch — no retry counter, no cooldown. Deterministic Python-side `sys.exit(0)` loop would drain battery + spam system.
**Related Files:** `voice_typer/client/src/main/python/relaunch-app.ts`
**Fix:** Add restart counter (max 3 restarts per 60s window) stored in temp file. If exceeded, show dialog + `app.quit()`.
**Severity:** 🟢 Low

## XZ-R18-12 — `handle-message.ts` broadcasts unknown push events without type validation (Low)

**Status:** ❌ Not Fixed
**Description:** `handle-message.ts:133-143` — no validation that `msg.type` is string or in known set. Unknown events fall through to broadcast-to-renderer.
**Related Files:** `voice_typer/client/src/main/python/handle-message.ts`
**Fix:** Add type guard: `if (typeof msg.type !== "string") { console.warn("[TCP] push event missing type string, dropping"); return; }`. Optionally maintain known-event allowlist.
**Severity:** 🟢 Low

## XZ-R18-13 — Stale docstring in `app.py` `restart_app()` (Low)

**Status:** ❌ Not Fixed
**Description:** `app.py:980, 985` docstring references `relaunch_electron` — actual code publishes `relaunch_app`.
**Related Files:** `voice_typer/server/app.py`
**Fix:** Folded into XZ-R18-01 fix.
**Severity:** 🟢 Low

---

## XZ-LOG-01 — Python stderr fallback unredacted (High)

**Status:** ❌ Not Fixed
**Description:** `ipc_server.py:2350, 2546` `print(buf.getvalue(), file=sys.stderr)` — `buf` holds raw traceback from `traceback.print_exc(file=buf)`. `/tmp` fallback at line 2366 correctly redacts via `_redact_text`; stderr fallback does not. Rust `spawn.rs:120` captures sidecar stderr and logs via `log::info!` UNREDACTED (Rust `CombinedLogger` has no PII filter — XZ-LOG-02).
**Related Files:** `voice_typer/server/ipc_server.py`
**Fix:** Change `print(buf.getvalue(), file=sys.stderr)` to `print(_redact_text(buf.getvalue()), file=sys.stderr)`. Apply to both instances (lines 2350 and 2546).
**Severity:** 🔴 High

## XZ-LOG-02 — No PII redaction in Rust `CombinedLogger::log` (High)

**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/platform/logging.rs:109-143` `log()` writes `record.args()` verbatim to file + stderr. Python has `PIIRedactionFilter`; Rust has nothing. Most active vector is `spawn.rs:120 [SIDECAR] stderr: {}` piping arbitrary Python sidecar stderr into Rust file log.
**Related Files:** `src-tauri/src/platform/logging.rs`
**Fix:** Port minimal redaction filter to Rust. Reuse same regex set as Python `PIIRedactionFilter` (email, phone, SSN, CC, IBAN, `Bearer …`, `Token …`, `sk-…`, `user:pass@host`). Implement as `fn redact(s: &str) -> String` called inside `CombinedLogger::log` before `format!`.
**Severity:** 🔴 High

## XZ-LOG-03 — No PII redaction in Electron loggers (Medium)

**Status:** ❌ Not Fixed
**Description:** `logging.ts:218-233` `formatLine` + `:395-409` `formatArgsForFile` write raw to disk. `main-window.ts:400-421` `console-message` event forwards renderer console output to `electron-runtime.log` + `electron-renderer-errors.log` unredacted. `cleanConsoleMsg` only strips printf specifiers.
**Related Files:**
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/windows/main-window.ts`
- `voice_typer/client/src/main/windows/bubble-window.ts`
**Fix:** Port TS redaction helper mirroring Python's `redact_pii`/`redact_secret`/`redact_url`. Apply inside `formatLine` and `formatArgsForFile` before returning line.
**Severity:** 🟡 Medium

## XZ-LOG-04 — Inconsistent log file locations (Medium)

**Status:** ❌ Not Fixed
**Description:** Python: `<config_dir>/voice-typer.log`. Prewarm: `<config_dir>/prewarm.log`. Rust: `<config_dir>/logs/voice-typer.log` (only one in `logs/` subdir). Electron: 5 files at config_dir root. 7 log files scattered across 2 directory levels.
**Related Files:**
- `voice_typer/server/log.py`
- `voice_typer/server/prewarm/logging_setup.py`
- `src-tauri/src/platform/logging.rs`
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/bootstrap.ts`
**Fix:** Align on ONE convention. Either move Rust log to `<config_dir>/voice-typer-rust.log` (root, matches Python/Electron), OR move ALL logs to `<config_dir>/logs/`.
**Severity:** 🟡 Medium

## XZ-LOG-05 — Inconsistent log line format (Medium)

**Status:** ❌ Not Fixed
**Description:** Python: `YYYY-MM-DD  HH:MM:SS [session_id] [thread] LEVEL [component] msg` (double space, no millis). Rust: `YYYY-MM-DD HH:MM:SS.mmm LEVEL target file:line -- msg` (single space, millis, no session_id). Electron: `ISO-8601Z [LEVEL] msg {json_args}`.
**Related Files:** (same as XZ-LOG-04)
**Fix:** Align timestamp format (ISO-8601). Better: add structured JSON formatter to Rust and Electron matching Python's `_JsonFormatter` schema. Gate behind `VOICE_TYPER_LOG_JSON` env var.
**Severity:** 🟡 Medium

## XZ-LOG-06 — Duplicated Electron loggers (Medium)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-R5-007.
**Related Files:** `voice_typer/client/src/main/logging.ts`
**Fix:** Folded into XZ-R5-007 fix.
**Severity:** 🟡 Medium

## XZ-LOG-07 — Stale comment in `handlers/_log.py` (Low)

**Status:** ❌ Not Fixed
**Description:** `handlers/_log.py:21-30` claims consolidation aspirational, 10 of 13 handlers declare inline. Reality: 0 handlers declare inline; 9 of 15 import from `_log.py`; 6 inherit `HandlerBase`.
**Related Files:** `voice_typer/server/handlers/_log.py`
**Fix:** Update docstring to reflect reality.
**Severity:** 🟢 Low

## XZ-LOG-08 — No session_id/correlation_id in Rust/Electron (Low)

**Status:** ❌ Not Fixed
**Description:** Python's `_SessionFilter` injects 8-char per-process session_id + `_correlation_id` contextvar. Rust `CombinedLogger` and Electron `formatLine` have neither. Cross-process correlation relies on timestamp proximity only.
**Related Files:**
- `src-tauri/src/platform/logging.rs`
- `src-tauri/src/main.rs` (session_id generation)
- `voice_typer/client/src/main/logging.ts`
- `voice_typer/client/src/main/bootstrap.ts`
**Fix:** Generate session_id in Rust `main.rs` at startup, prepend to every `CombinedLogger::log` line. Pass to Python sidecar via `VOICE_TYPER_SESSION_ID` env var. For Electron, generate in `bootstrap.ts`, pass to Python via spawn env, include in `formatLine`.
**Severity:** 🟢 Low

## XZ-LOG-09 — `log_rate_limited` unevenly applied (Low)

**Status:** ❌ Not Fixed
**Description:** `log_rate_limit.py` helper exists but used at only 3 call sites. Cloud retry loops, WS heartbeat-miss, WS invalid-JSON — all match flood-risk description but don't use helper.
**Related Files:**
- `voice_typer/server/cloud_engines.py`
- `voice_typer/server/sidecar_ws.py`
- `src-tauri/src/sidecar/ws.rs`
**Fix:** Audit listed call sites. Apply `log_rate_limited` to cloud retry-loop warnings, WS heartbeat-miss, WS invalid-JSON/unexpected-frame.
**Severity:** 🟢 Low

## XZ-LOG-10 — `RotatingFileHandler` not inter-process safe (Low)

**Status:** ❌ Not Fixed
**Description:** `log.py:674-680` `RotatingFileHandler` uses `threading.Lock` (thread-safe, NOT inter-process safe). Main app + prewarm process both write to same `voice-typer.log`.
**Related Files:** `voice_typer/server/log.py`
**Fix:** Use `WatchedFileHandler` (re-opens file on each emit, detects rotation by inode change). OR add `fcntl.flock`/`msvcrt.locking` inter-process lock around rotation. OR route ALL prewarm logs to `prewarm.log` only.
**Severity:** 🟢 Low

## XZ-LOG-11 — `print()` in `recorder.py:40` (Low)

**Status:** ❌ Not Fixed
**Description:** `native_hotkeys/recorder.py:40` `print(f"Captured: {result}")` — debug print left in production. Under `--ws`/`--port` mode, triggers Rust host's "unexpected stdout line" warning per hotkey capture.
**Related Files:** `voice_typer/server/native_hotkeys/recorder.py`
**Fix:** Replace with `log.info("[HOTKEY] Captured: %s", result)` (or remove if debugging leftover).
**Severity:** 🟢 Low

## XZ-LOG-12 — `PIIRedactionFilter` blind spots (Low, Informational)

**Status:** ❌ Not Fixed
**Description:** `security.py:178-204` redacts structured PII patterns but not free-form transcription text. Mitigated by logging only SHA-256 hash + length. Risk: future regression logging `text` directly.
**Related Files:**
- `voice_typer/server/security.py`
- `voice_typer/server/dictation_pipeline.py`
**Fix:** Add regression test grepping for `log.*(text|transcript|partial|final_text|result)` calls interpolating variable directly. Document convention in CONTRIBUTING.md.
**Severity:** 🟢 Low

---

## XZ-CC-1 — Duplicated VAD default constants (High)

**Status:** ❌ Not Fixed
**Description:** `vad_processor.py:73-78` (canonical) and `recording/recorder.py:160-165` (compat shim) define same 6 constants. Recorder comment admits "no longer referenced internally after VadProcessor extraction". 4 of 6 unused.
**Related Files:**
- `voice_typer/server/vad_processor.py`
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/__init__.py`
**Fix:** Delete 4 dead constants (`_DEFAULT_VAD_CALIBRATION_DURATION`, `_DEFAULT_VAD_HANGOVER_FRAMES`, `_DEFAULT_VAD_SILENCE_FRAMES`, `_DEFAULT_VAD_SPEECH_FRAMES`) from `recorder.py:162-165` and from `recording/__init__.py:171-176, 218-223`. For 2 used, import from `vad_processor`.
**Severity:** 🔴 High

## XZ-CC-2 — Duplicated noise-filter defaults (Medium)

**Status:** ❌ Not Fixed
**Description:** `audio_chain_builder.py:128-153` `_DEFAULTS` dict mirrors `config.py:942-986` Config dataclass defaults. No sync mechanism (no CI test).
**Related Files:**
- `voice_typer/server/audio_chain_builder.py`
- `voice_typer/server/config.py`
**Fix:** Replace `_DEFAULTS` with `Config()` instance snapshot: `_DEFAULTS = {f.name: getattr(Config(), f.name) for f in fields(Config) if f.name.startswith("noise_filter_")}`. OR add CI test mirroring `test_hotkey_reserved_sync.py`.
**Severity:** 🟡 Medium

## XZ-CC-3 — Duplicated LLM default URL + model (Medium)

**Status:** ❌ Not Fixed
**Description:** `llm_polish.py:110-111` `_DEFAULT_URL`/`_DEFAULT_MODEL` duplicate `config.py:696-697` Config dataclass defaults. `LLMPolish.__init__` accepts `api_url`/`model` as optional kwargs and falls back to module-level constants.
**Related Files:**
- `voice_typer/server/llm_polish.py`
- `voice_typer/server/config.py`
**Fix:** Delete `_DEFAULT_URL`/`_DEFAULT_MODEL` from `llm_polish.py`. Make `api_url`/`model` required kwargs, OR import from Config. Update call sites to forward Config values.
**Severity:** 🟡 Medium

## XZ-CC-4 — Duplicated secret-redaction implementation (High)

**Status:** ❌ Not Fixed
**Description:** `credential_store.py:253-279` `_redact_sensitive` parallel implementation. Threshold mismatch: 32+ char vs `_secrets._KEY_PATTERNS` 20+ char. Provider coverage mismatch: `gsk_` (Groq) only in credential_store. Flag-form coverage mismatch: `_secrets` has `_FLAG_VALUE_PATTERN`/`_BARE_KEY_VALUE_PATTERN`, credential_store doesn't.
**Related Files:**
- `voice_typer/server/credential_store.py`
- `voice_typer/server/_secrets.py`
**Fix:** Folded into XZ-SEC-07 fix (delegate to `_secrets.redact_secret`, add `gsk_` + `sk-ant-` prefixes to canonical helper).
**Severity:** 🔴 High

## XZ-CC-5 — Dead compat-shim VAD constants (Low)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-CC-1 — 4 of 6 `_DEFAULT_VAD_*` in `recorder.py` unused.
**Related Files:** (same as XZ-CC-1)
**Fix:** Folded into XZ-CC-1 fix.
**Severity:** 🟢 Low

## XZ-CC-6 — `ToggleDictationResult.recording` phantom field (Medium)

**Status:** ❌ Not Fixed
**Description:** `ipc.ts:478-480` declares `recording: boolean` as REQUIRED. Python `_handle_toggle_dictation` returns `{"type": "ack"}` with NO `data` field. `Home.tsx:635` calls `await call("toggle_dictation")` with `T = unknown` — result discarded. Future contributor writing `const { recording } = await call<ToggleDictationResult>(...)` gets `recording: undefined` while TS type-checks as `boolean`.
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts`
- `voice_typer/server/handlers/dictation_handlers.py`
**Fix:** Either (a) update Python handler to populate `resp["data"] = {"recording": self.service.is_recording()}` so type matches wire, OR (b) change TS type to `ToggleDictationResult = undefined` and update mapping. Delete `ResponseData<T>` if no consumer.
**Severity:** 🟡 Medium

## XZ-CC-7 — `TranscriptionFinalEvent.duration_ms?` never sent (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc.ts:105-108` declares optional `duration_ms?: number`. Python `dictation_pipeline.py:1091-1096` publishes `{text: string}` only. Comment claims "mirrors wire format" — misleading. Line reference `:911` stale (actual `:1093`).
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc.ts`
- `voice_typer/server/dictation_pipeline.py`
**Fix:** Either (a) populate `duration_ms` in Python sender, OR (b) delete `duration_ms?` from TS type. Update stale line reference.
**Severity:** 🟢 Low

## XZ-CC-8 — `requirements.txt` missing 2 macOS pyobjc deps (High)

**Status:** ❌ Not Fixed
**Description:** `pyproject.toml:155, 163` declares `pyobjc-framework-CoreFoundation` and `pyobjc-framework-ApplicationServices` (macOS-only). `requirements.txt:43-64` declares `pyobjc-core`, `pyobjc-framework-CoreAudio`, `pyobjc-framework-Cocoa` but NOT the two above. `pip install -r requirements.txt` on macOS → silently broken mic watcher + accessibility probe.
**Related Files:**
- `requirements.txt`
- `pyproject.toml`
**Fix:** Add `pyobjc-framework-CoreFoundation>=9.0; sys_platform == 'darwin'` and `pyobjc-framework-ApplicationServices>=9.0; sys_platform == 'darwin'` to `requirements.txt`. Better: delete `requirements.txt` entirely and document `pip install -r requirements-lock.txt` as only supported path.
**Severity:** 🔴 High

## XZ-CC-9 — Three competing requirements files (Medium)

**Status:** ❌ Not Fixed
**Description:** `requirements.txt` header claims hash-pinned but contains ZERO hashes. `pip install --require-hashes -r requirements.txt` will FAIL. `requirements-lock.txt` actually has hashes. Three sources of truth with no sync.
**Related Files:**
- `requirements.txt`
- `requirements-lock.txt`
- `pyproject.toml`
**Fix:** Delete `requirements.txt`. Update `README.md`/`CONTRIBUTING.md` to point developers at `pip install -r requirements-lock.txt` or `uv sync`. If fast-path needed, document `pip install -e .` (reads `pyproject.toml` directly).
**Severity:** 🟡 Medium

## XZ-CC-10 — Cargo dep version drift (Medium)

**Status:** ❌ Not Fixed
**Description:** `Cargo.toml:62` `rand = "0.8"` → Cargo.lock has BOTH `rand 0.8.7` and `rand 0.9.5` (dual-resolution, ~50-100 KB bloat). `Cargo.toml:53` `tokio-tungstenite = "0.24"` (current 0.27.x, 3 minors behind). `Cargo.toml:49` `enigo = "0.2"` (current 0.14.x, 12 minors behind).
**Related Files:** `src-tauri/Cargo.toml`
**Fix:** Bump `rand = "0.9"` (update `thread_rng()` → `rng()`). Bump `tokio-tungstenite = "0.27"`. Leave `enigo` for separate PR (larger refactor).
**Severity:** 🟡 Medium

## XZ-CC-11 — 62 `# type: ignore` / `pyrefly: ignore` in security-critical code (Medium)

**Status:** ❌ Not Fixed
**Description:** 62 occurrences across 29 Python files. Top concentrations: `native_adapter.py` (9), `clipboard_snapshot.py` (7), `credential_store.py` (6). Mix of legitimate platform-only import suppression + real type holes. Pyrefly baseline's `errors: []` hides 116 unsuppressed errors.
**Related Files:** cross-cutting (29 files)
**Fix:** Audit 62 suppression sites — verify each suppression reason documented inline. For 34 non-platform-specific pyrefly errors, fix one-by-one. Pin `pyrefly==1.1.1` in `pyproject.toml [dev]` + CI.
**Severity:** 🟡 Medium

## XZ-CC-12 — Stale TODO Fix-A cluster (Low)

**Status:** ❌ Not Fixed
**Description:** 5 cross-file TODOs about replacing `_wait_for_relaunch_ack` with public IPCServer API: `app.py:1074, 1169, 1191`, `hotkey_dispatcher.py:51, 55`. None have date/PR reference.
**Related Files:**
- `voice_typer/server/app.py`
- `voice_typer/server/hotkey_dispatcher.py`
**Fix:** Either execute Fix-A (add `IPCServer.wait_for_relaunch_ack(timeout) -> bool` public method, replace 3 `app.py` getattr call sites, delete 5 TODOs), OR convert to tracked issue with owner + date.
**Severity:** 🟢 Low

## XZ-CC-13 — Stale TODO migrate-tests cluster (Low)

**Status:** ❌ Not Fixed
**Description:** 4 identical TODOs across 3 packages: `prewarm/__init__.py:110`, `recording/__init__.py:49, 320`, `server_platform/__init__.py:80`. All reference "CR-67 / TECH-DEBT". ~500 LOC of `__init__.py` boilerplate for test-patch compatibility.
**Related Files:**
- `voice_typer/server/prewarm/__init__.py`
- `voice_typer/server/recording/__init__.py`
- `voice_typer/server/server_platform/__init__.py`
**Fix:** Tracked separately as CR-67/TECH-DEBT. Update TODOs with current date + tracking issue link. OR if migration actively worked, annotate "IN PROGRESS by <owner>, ETA <date>".
**Severity:** 🟢 Low

## XZ-CC-14 — `package.json` `//devDependencies` "DO NOT DOWNGRADE" comment (Low)

**Status:** ❌ Not Fixed
**Description:** `package.json:53` comment exists because prior agent downgraded TS to 5.6.3, breaking `npm ci`. Project on bleeding edge (TS 7.0.2, electron-vite 6.0.0-beta.1 pre-release).
**Related Files:** `voice_typer/client/package.json`
**Fix:** Pin TypeScript to exact version (`"typescript": "7.0.2"` not `^7.0.2`). Migrate `electron-vite` off beta when 6.0.0 ships. Move warning to CONTRIBUTING.md.
**Severity:** 🟢 Low

## XZ-CC-15 — `pyrefly-baseline.json` `errors: []` while CI reports 116 errors (High)

**Status:** ❌ Not Fixed
**Description:** Baseline file's own `_current_state_2026_07_22` comment admits: "Until those land, the pyrefly check step in CI will continue to exit 1 (because pyrefly reports 116 unsuppressed errors and the baseline is empty)". 34 non-platform-specific real type bugs hidden from CI.
**Related Files:** `pyrefly-baseline.json`
**Fix:** (a) Pin `pyrefly==1.1.1` in `pyproject.toml [dev]` + `.github/workflows/build.yml`. (b) Fix 34 non-platform-specific real type bugs. (c) For 82 platform-specific false positives, regenerate baseline from real `pyrefly check` run on platform-appropriate interpreter. (d) DO NOT keep `errors: []` as "conservative floor" — silence-by-deletion pattern.
**Severity:** 🔴 High

## XZ-CC-16 — `ResponseData<T>` mapped type exported but never imported (Low)

**Status:** ❌ Not Fixed
**Description:** `ipc.ts:524-549` 26-line conditional-types cascade. `grep ResponseData` returns 1 hit — the declaration. `usePython.call` uses `async <T = unknown>(type: string, ...)` — generic over `T` with default `unknown`, NOT constrained to `PythonRequest["type"]`.
**Related Files:** `voice_typer/client/src/renderer/src/types/ipc.ts`
**Fix:** Either (a) wire `ResponseData<T>` into `usePython.call` by constraining generic, OR (b) delete `ResponseData<T>` and dead result types (`ToggleDictationResult`, `ToggleFavoriteResult`, `SaveVocabularyResult`).
**Severity:** 🟢 Low

---

## XZ-14-01 — Circuit breaker public API missing (High)

**Status:** ❌ Not Fixed
**Description:** `asr_registry.py` lacks `reset_failures` and `failure_count` public methods. 4 tests in `test_asr_registry_lifecycle.py` fail with `AttributeError`. Disabled backend permanently unreachable for session.
**Related Files:**
- `voice_typer/server/asr_registry.py`
- `tests/test_asr_registry_lifecycle.py`
**Fix:** Add `failure_count(name) -> int` and `reset_failures(name) -> None` public methods. Add IPC handler `reset_backend`. Add Settings UI affordance.
**Severity:** 🔴 High

## XZ-14-02 — `disabled_backends` persistence fictitious (High)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-CFG-01 — Config dataclass has no `disabled_backends` field. `getattr` returns None, `setattr` raises AttributeError (silently swallowed).
**Related Files:**
- `voice_typer/server/asr_registry.py`
- `voice_typer/server/config.py`
**Fix:** Folded into XZ-CFG-01 fix.
**Severity:** 🔴 High

## XZ-14-03 — `validate_config` dead code (Medium)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-CFG-04.
**Related Files:**
- `voice_typer/server/config_validators.py`
- `voice_typer/server/config.py`
**Fix:** Folded into XZ-CFG-04 fix.
**Severity:** 🟡 Medium

## XZ-14-04 — No cross-field hotkey conflict check (Medium)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:618-662` `_validate_hotkey` only checks against reserved/system shortcuts. Never sees OTHER hotkey fields. User can set `hotkey=<ctrl>+<space>` AND `push_to_talk_hotkey=<ctrl>+<space>` simultaneously.
**Related Files:** `voice_typer/server/config_validators.py`
**Fix:** Add post-loop cross-field check in `validate_config_update`: collect 3 hotkey values, normalize via `hotkey_spec.parse_hotkey`, reject duplicates.
**Severity:** 🟡 Medium

## XZ-14-05 — Cross-platform hotkey portability not enforced (Medium)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:423-429, 487-509` `_platform_key()` returns current platform. `_check_platform_reserved` only consults current platform's reserved list. Config not portable across OSes — `<cmd>+<q>` passes on Linux but quits apps on macOS.
**Related Files:**
- `voice_typer/server/config_validators.py`
- `voice_typer/server/hotkey_reserved.json`
**Fix:** Either (a) check ALL platforms' reserved lists and warn (don't reject) on cross-platform conflicts, OR (b) tighten universal_reserved to cover union of all per-platform combos. Option (a): emit warning via `_load_warnings`.
**Severity:** 🟡 Medium

## XZ-14-06 — Silent unloaded-backend fallback (Medium)

**Status:** ❌ Not Fixed
**Description:** `asr_registry.py:111-134` `get_active()` last-resort branch returns unloaded backend, logs WARNING, `transcribe_with_fallback` returns empty string. No tray notification.
**Related Files:** `voice_typer/server/asr_registry.py`
**Fix:** When `get_active()` falls through to last-resort, fire tray notification via same path as `load_with_fallback` failures. OR have `transcribe_with_fallback` raise `BackendNotReadyError`.
**Severity:** 🟡 Medium

## XZ-14-07 — `VOICE_TYPER_CONFIG_DIR` validation weaker than `HF_HOME` (Low)

**Status:** ❌ Not Fixed
**Description:** `env_validation.py:52-58` only rejects NUL bytes via `_path_pattern = re.compile(r"^[^\0]+$")`. `HF_HOME` block at `:87-111` calls `_validate_path_safety(Path(hf_home), Path.home())`.
**Related Files:** `voice_typer/server/env_validation.py`
**Fix:** Add `_validate_path_safety(Path(config_dir), Path.home())` call to `VOICE_TYPER_CONFIG_DIR` block, mirroring `HF_HOME` pattern.
**Severity:** 🟢 Low

## XZ-14-08 — `language` field no format check (Low)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:666` `_VALIDATOR_LANGUAGE = _make_str_validator(max_len=16)` — no check that value is recognized language code. User can set `language="zzzzz"` or `language="english"` (both pass) → Whisper load fails with cryptic error.
**Related Files:** `voice_typer/server/config_validators.py`
**Fix:** Add regex validator accepting common Whisper language codes (2-letter ISO 639-1 + 3-letter extensions). Or source allowlist from `whisper.tokenizer.LANGUAGES` at module init.
**Severity:** 🟢 Low

## XZ-14-09 — Arbitrary lower bounds (Low)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:820` `max_recording_time_seconds lo=300` (5 min minimum, likely typo for 30). `:829` `recording_channels lo=0` (nonsensical 0 channels). `:792` `history_max_entries lo=10` inconsistent with `:791` `history_retention_count lo=0`.
**Related Files:** `voice_typer/server/config_validators.py`
**Fix:** `max_recording_time_seconds: lo=30`. `recording_channels: lo=1`. `history_max_entries: lo=0` (match retention_count semantics).
**Severity:** 🟢 Low

## XZ-14-10 — `config_validators.py` 1102-line monolith (Low)

**Status:** ❌ Not Fixed
**Description:** Mixes validator primitives, hotkey pipeline, IPC field schema in one file. `IPC_CONFIG_ALLOWLIST` is 220-line inline literal.
**Related Files:** `voice_typer/server/config_validators.py`
**Fix:** Split into `validators/` package: `_primitives.py`, `_hotkey.py`, `_schema.py`, `_api.py`. Re-export via package `__init__.py`.
**Severity:** 🟢 Low

## XZ-14-11 — Hotkey_reserved.json intentional duplication (Low, Informational)

**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/hotkey_reserved.json` ↔ `voice_typer/client/src/renderer/src/data/hotkey_reserved.json` byte-identical. Documented as Vite HMR crash workaround. Drift caught by `tests/test_hotkey_reserved_sync.py`.
**Related Files:**
- `voice_typer/server/hotkey_reserved.json`
- `voice_typer/client/src/renderer/src/data/hotkey_reserved.json`
**Fix:** Re-test whether Vite HMR crash reproduces with current Vite version. If fixed, switch frontend to `@server/hotkey_reserved.json` import. If not fixed, add pre-commit hook running sync test.
**Severity:** 🟢 Low

## XZ-14-12 — Dead `_change_model_impl` shim (Low)

**Status:** ❌ Not Fixed
**Description:** `model_manager.py:968-994` deprecated shim with ZERO callers. Also broken signature: calls `_change_model_unload_phase(new_backend)` with one arg, but current signature requires two (`new_backend, old_backend`).
**Related Files:** `voice_typer/server/model_manager.py`
**Fix:** Delete `_change_model_impl` entirely.
**Severity:** 🟢 Low

## XZ-14-13 — Path env vars leak username in logs (Low)

**Status:** ❌ Not Fixed
**Description:** Same as XZ-R3-10.
**Related Files:** `voice_typer/server/env_validation.py`
**Fix:** Folded into XZ-R3-10 fix.
**Severity:** 🟢 Low

## XZ-14-14 — Stale TODO in `model_manager.py:touch_active_model` (Low)

**Status:** ❌ Not Fixed
**Description:** `model_manager.py:1106-1131` docstring claims wiring tracked under "FIX-15 / follow-up". But `dictation_pipeline.py:631-636` already calls it.
**Related Files:** `voice_typer/server/model_manager.py`
**Fix:** Update docstring to "Wired into DictationPipeline._transcribe (dictation_pipeline.py:636)". Delete FIX-15 reference.
**Severity:** 🟢 Low

## XZ-14-15 — `_make_custom_theme_validator` no key-count cap (Low)

**Status:** ❌ Not Fixed
**Description:** `config_validators.py:245-275` validates 6 required keys per mode but doesn't reject extra keys, doesn't bound total dict size, doesn't bound length of hex color values.
**Related Files:** `voice_typer/server/config_validators.py`
**Fix:** Add `if len(v) > 64: return "too many top-level keys"` and `if len(mode_dict) > 64: return f"{mode} has too many keys"`.
**Severity:** 🟢 Low

## XZ-14-16 — Migration runner bumps schema_version even on failure (Low)

**Status:** ❌ Not Fixed
**Description:** `config.py:1224-1290` migration runner continues with partially-migrated data on migrator exception, bumps `schema_version` to `_CURRENT_SCHEMA_VERSION`. User's config stuck in half-migrated state.
**Related Files:** `voice_typer/server/config.py`
**Fix:** On migrator exception, do NOT bump `schema_version` — leave at `version - 1` so migration re-runs. Include timestamp or failed-version in `.bak` filename.
**Severity:** 🟢 Low

## XZ-14-17 — `asr_setup.py` docstring/count mismatch (Low)

**Status:** ❌ Not Fixed
**Description:** `asr_setup.py:162-163, 319-324, 412-420` `_MAX_DOWNLOAD_RETRIES = 4`. Docstring says "max 4 retries" (5 total attempts). Inline comment lists "1s, 2s, 4s, 8s" (4 delays = 4 attempts).
**Related Files:** `voice_typer/server/asr_setup.py`
**Fix:** Update docstring to "max 4 attempts (1 initial + 3 retries with exponential backoff 1s, 2s, 4s, 8s)". OR rename constant to `_MAX_DOWNLOAD_ATTEMPTS`.
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

## [XS-1] — Pyrefly CI gate is theatre — 116 type errors pass with exit 0

**Status:** ❌ Not Fixed

**Description:** The `Run pyrefly type check` step in `.github/workflows/build.yml:157-160` runs `pyrefly check voice_typer/` with no `|| true`, and the inline comment claims it is a 'hard gate'. But pyrefly 1.1.1's `check` command exits 0 even when it reports 116 unsuppressed type errors (35 suppressed, 81 warnings). The `pyrefly-baseline.json` is intentionally empty (`errors: []`) per CR-074, and the downstream audit step (build.yml:168) diffs against this empty baseline — so it also catches nothing. 116 real type errors (56 missing-attribute, 26 missing-import, 8 bad-argument-type, 7 unbound-name, 5 bad-return, etc.) silently pass on every PR.

**Root Cause:** pyrefly 1.1.1's `check` command does not return non-zero on type errors by default — only on missing config / invalid args. The CI step invokes it with no `--strict` / `--error-format` wrapper that would convert error counts into a non-zero exit.

**Progress:** None yet.

**Related Files:**
- `.github/workflows/build.yml:157-160`
- `pyrefly-baseline.json`

**Fix:** Either (a) wrap the pyrefly invocation to enforce non-zero exit on errors: `pyrefly check voice_typer/ --output-format=json | python -c "import sys,json; d=json.load(sys.stdin); sys.exit(1 if d.get('errors') else 0)"`, or (b) regenerate the baseline from a real `pyrefly check voice_typer/ --output-format=json > pyrefly-baseline.json` run so the baseline captures the 116 known errors and the audit step can diff against it. Option (a) is the honest fix; option (b) is the ratchet approach. Also delete the false 'hard gate' and 'exit 1' comments.

**Severity:** 🔴 Critical

**Category:** Testing infrastructure

---

## [XS-2] — Tauri v1→v2 config key renames NOT applied (postInstallScript → postInstall)

**Status:** ❌ Not Fixed

**Description:** `src-tauri/tauri.conf.json` lines 88-89 and 100-101 use the Tauri v1 key names `postInstallScript` and `preRemoveScript`. Tauri v2 renamed these to `postInstall` and `preRemove`. The Cargo.lock pins `tauri 2.11.5`, so v2 semantics apply. The v2 `BundleConfig` struct does NOT set `#[serde(deny_unknown_fields)]` on the deb/rpm sub-structs, so the unknown v1 keys are silently ignored by serde — meaning the postinst/prerm scripts are NEVER wired into the generated .deb/.rpm. On Linux installs, this means the udev rule for keyboard access, input group membership, Caps Lock neutralization, polkit policy, and the permissions manifest are ALL not installed. Keyboard hotkeys silently break on every fresh Linux .deb/.rpm install.

**Root Cause:** Tauri v1→v2 migration renamed `postInstallScript`→`postInstall` and `preRemoveScript`→`preRemove`. The config file was never updated.

**Progress:** None yet.

**Related Files:**
- `src-tauri/tauri.conf.json:88-89,100-101`

**Fix:** Rename all 4 keys (`postInstallScript` → `postInstall`, `preRemoveScript` → `preRemove`) in both the `deb` and `rpm` blocks of `src-tauri/tauri.conf.json`.

**Severity:** 🔴 Critical

**Category:** Build pipeline

---

## [XS-3] — Tauri postInstall/preRemove paths wrong (../../ should be ../)

**Status:** ❌ Not Fixed

**Description:** Even after the XS-2 rename, the paths `../../scripts/linux/postinst` etc. are wrong. Tauri v2 resolves bundle paths relative to the `tauri.conf.json` directory (`src-tauri/`). From `src-tauri/`: `../scripts/linux/postinst` → `<repo>/scripts/linux/postinst` (EXISTS); `../../scripts/linux/postinst` → parent of repo (does NOT exist). Verified via `realpath`. Currently masked by XS-2 (serde ignores the v1 keys entirely so the path is never resolved). Will surface immediately after XS-2 is fixed.

**Root Cause:** Path has one extra `../` — likely a copy-paste error assuming the config was at repo root rather than in `src-tauri/`.

**Progress:** None yet.

**Related Files:**
- `src-tauri/tauri.conf.json:88,89,100,101`

**Fix:** Change all 4 paths from `../../scripts/linux/...` to `../scripts/linux/...` in the same edit as XS-2.

**Severity:** 🔴 Critical

**Category:** Build pipeline

---

## [XS-4] — tauri.linux-aarch64.conf.json lists nonexistent linux-key-listener resource

**Status:** ❌ Not Fixed

**Description:** `src-tauri/tauri.linux-aarch64.conf.json` line 5 includes `"resources/native/linux-key-listener"` in the resource override. But `scripts/build/build_tauri_all.sh` line 198-201 comment explicitly says 'aarch64 omits linux-key-listener because compile_native.sh can't cross-compile it'. On an aarch64 Linux build, `cargo tauri build --target aarch64-unknown-linux-gnu --config tauri.linux-aarch64.conf.json` will fail at the `tauri-build` resource-copy step (it canonicalizes every declared resource and hard-fails on missing files).

**Root Cause:** Config file was not updated when the aarch64 native listener build was disabled.

**Progress:** None yet.

**Related Files:**
- `src-tauri/tauri.linux-aarch64.conf.json:5`

**Fix:** Remove line 5 (`"resources/native/linux-key-listener"`) from `tauri.linux-aarch64.conf.json`. The aarch64 .deb/.rpm will ship without the native listener (the Python sidecar's hotkeys factory already has a fallback path for missing native binaries).

**Severity:** 🔴 Critical

**Category:** Build pipeline

---

## [XS-5] — Pre-commit framework ruff hook is broken: invalid --check flag

**Status:** ❌ Not Fixed

**Description:** `.pre-commit-config.yaml:9-11` declares `args: [--check, --fix]` for the `ruff` hook. The ruff-pre-commit `ruff` hook runs `ruff check --force-exclude`. The args are appended → `ruff check --force-exclude --check --fix`. Verified locally: `ruff check --force-exclude --check --fix` → `error: unexpected argument '--check' found`. `ruff check` has no `--check` flag (only `ruff format` does). This appears to be a copy-paste from the `ruff-format` hook below. Any contributor who runs `pre-commit install` and commits will see the ruff hook fail, blocking the commit. They will reach for `--no-verify` (bypassing ALL hooks) or `SKIP=ruff`.

**Root Cause:** Copy-paste error: `--check` is a `ruff format` flag, not a `ruff check` flag.

**Progress:** None yet.

**Related Files:**
- `.pre-commit-config.yaml:9-11`

**Fix:** Change to `args: [--fix]` (drop `--check`).

**Severity:** 🔴 Critical

**Category:** CI/CD

---

## [XS-6] — Husky pre-push sources deprecated _/husky.sh (v8 idiom) under husky v9 — blocks all pushes

**Status:** ❌ Not Fixed

**Description:** `.husky/pre-push:2` runs `. "$(dirname -- "$0")/_/husky.sh"`. `package.json:70` pins `husky: ^9.1.7` (v9). Husky v9 deprecated the `_/husky.sh` wrapper; the `_/` directory is NOT created by husky v9. Verified: `ls .husky/_/` → No such file or directory. The sibling `.husky/pre-commit` does NOT source `_/husky.sh` (correctly v9-style). Only `pre-push` has the stale v8 line. When pre-push fires, line 2 tries to source a non-existent file. Under `sh -e` (husky's default), this aborts the script → pre-push fails → every push is blocked.

**Root Cause:** Husky v8→v9 migration was incomplete — `pre-push` was not updated to match `pre-commit`'s v9 style.

**Progress:** None yet.

**Related Files:**
- `.husky/pre-push:2`

**Fix:** Remove line 2 from `.husky/pre-push` (match `.husky/pre-commit`'s v9 style).

**Severity:** 🔴 Critical

**Category:** CI/CD

---

## [XS-7] — build_prewarm_*.sh --check is a stub that returns false positives

**Status:** ❌ Not Fixed

**Description:** All three `build_prewarm_{linux,macos,windows}.sh` scripts implement `--check` identically: `echo '[build_prewarm_linux] --check: same toolchain as build_sidecar_linux — OK if that passes.'; exit 0`. Verified empirically: `bash scripts/build/build_prewarm_linux.sh --check` prints the message and exits 0 — even though the build toolchain (pybs interpreter, nuitka, faster_whisper, ctranslate2) is NOT installed. By contrast, `build_sidecar_{linux,macos,windows}.sh --check` actually probes `import nuitka`, `import faster_whisper, ctranslate2`, etc. and exits 1 if missing. Any CI step that gates on `build_prewarm_*.sh --check` before running the real Nuitka prewarm build will get a green light even when the toolchain is broken.

**Root Cause:** Prewarm --check was implemented as a stub deferring to sidecar --check but never actually sources or invokes it.

**Progress:** None yet.

**Related Files:**
- `scripts/build/build_prewarm_linux.sh`
- `scripts/build/build_prewarm_macos.sh`
- `scripts/build/build_prewarm_windows.sh`

**Fix:** Either (a) source `build_sidecar_<plat>.sh --check` from the prewarm --check (sharing the same toolchain verification), or (b) remove the --check flag from the prewarm scripts to eliminate the false sense of safety.

**Severity:** 🔴 Critical

**Category:** Build pipeline

---

## [XS-8] — sync_versions.py is broken for 3 of 4 target files + missing Cargo.toml/tauri.conf.json sync

**Status:** ❌ Not Fixed

**Description:** `scripts/build/sync_versions.py` claims to sync versions across 4 files but: (1) `voice_typer/__init__.py` — BROKEN: regex `r'__version__\s*=\s*"([^"]+)"'` does NOT match the PEP 562 lazy `__getattr__` pattern; `write_init_py_fallback()` would append a new `__version__ = "<version>"` line that shadows the lazy `__getattr__` and breaks the coldstart optimization. (2) `voice_typer/client/package.json` — WORKS. (3) `voice_typer/client/electron-builder.yml` — NO-OP (no top-level `version:` field). (4) `pyproject.toml` — read-only source of truth. MISSING TARGETS: `src-tauri/Cargo.toml` has `version = "1.0.0"` (NOT synced); `src-tauri/tauri.conf.json` has `"version": "1.0.0"` (NOT synced). Both will drift silently when pyproject.toml version is bumped. CI gap: `.github/workflows/build.yml:363` runs `python scripts/build/sync_versions.py` WITHOUT the `--check` flag, so even the checks that DO work are not enforced as a gate.

**Root Cause:** Script was written before the PEP 562 lazy `__getattr__` refactor of `__init__.py` and before the Tauri migration added Cargo.toml/tauri.conf.json as version-carrying files.

**Progress:** None yet.

**Related Files:**
- `scripts/build/sync_versions.py`
- `.github/workflows/build.yml:363`
- `voice_typer/__init__.py:13`

**Fix:** (1) Add `--check` to the sync_versions.py invocation in build.yml:363. (2) Add `src-tauri/Cargo.toml` and `src-tauri/tauri.conf.json` to the sync targets. (3) Remove the broken `voice_typer/__init__.py` sync path entirely (incompatible with PEP 562). (4) Fix the stale `scripts/sync_versions.py` path in `__init__.py:13` (actual is `scripts/build/sync_versions.py`).

**Severity:** 🔴 Critical

**Category:** Build pipeline

---

## [XS-9] — conftest.py --cov shim doesn't actually strip addopts (subset runs fail on coverage)

**Status:** ❌ Not Fixed

**Description:** The root `conftest.py:40-68` shim strips `--cov*` flags from `sys.argv` when pytest-cov is not installed. But `--cov=voice_typer --cov-fail-under=65` live in `addopts` in `pyproject.toml:399`, NOT in `sys.argv`. Empirically verified: `pytest tests/test_text_cleanup.py --collect-only -q` (with pytest-cov installed, so the shim is a no-op) FAILS with `FAIL Required test coverage of 65% not reached. Total coverage: 0.32%`. This proves the addopts threshold fires on subset runs. The shim is dead code that gives a false sense of robustness. The conftest docstring's claim that 'Local subset runs no longer see the threshold' is false.

**Root Cause:** The shim's author assumed `--cov` flags arrive via sys.argv (command line), but they actually arrive via `addopts` (pyproject.toml), which pytest merges into the args list AFTER `pytest_load_initial_conftests` runs.

**Progress:** None yet.

**Related Files:**
- `conftest.py:40-68`
- `pyproject.toml:399`

**Fix:** Move `--cov=voice_typer --cov-fail-under=65` out of `addopts` into the CI invocation explicitly (build.yml:208 already passes `--cov=voice_typer`, add `--cov-fail-under=65` there). Update the conftest.py docstring to reflect reality. Local subset runs (`pytest tests/test_foo.py`) will then work without coverage enforcement; CI still enforces the threshold.

**Severity:** 🔴 High

**Category:** Testing infrastructure

---

## [XS-10] — CI lint pipeline is red — ruff ratchet grew (180→192), F-rule hard gate fails, pyrefly fails

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

## [XS-11] — IPC error-envelope `code` field missing `server.` prefix (33+ handler test failures)

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

## [XS-12] — Recording-pipeline refactor dropped multiple internal contracts (54 test failures)

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

## [XS-13] — Config.set_mutation_lock API missing (11 test failures)

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

## [XS-14] — VoiceTyperService.apply_config_side_effects does not delegate to config_applier (14 service-layer test failures)

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

## [XS-15] — TemplateManager lost public `templates` attribute (6 test failures)

**Status:** ❌ Not Fixed

**Description:** `tests/test_templates.py::TestTemplateCRUD::test_add_template`, `test_delete_template`, `test_templates_property_returns_copy`, `TestTemplatePersistence::test_templates_persist_across_instances`, `test_empty_templates_file`, `TestTemplateImportExport::test_import_json` — all fail with `AttributeError: 'TemplateManager' object has no attribute 'templates'. Did you mean: '_templates'?`

**Root Cause:** Public attribute was made private (`templates` → `_templates`) without a property shim; tests (and presumably callers) break.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/templates.py`
- `tests/test_templates.py`

**Fix:** Add a `@property` shim: `@property\ndef templates(self): return self._templates` (or return a copy per the `test_templates_property_returns_copy` contract). Run `pytest tests/test_templates.py -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-16] — App lifecycle contracts broken (11 test failures in tests/app/)

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

## [XS-17] — AsrBackendRegistry missing reset_failures + circuit-breaker counter broken (4 test failures)

**Status:** ❌ Not Fixed

**Description:** `tests/test_asr_registry_lifecycle.py::TestCircuitBreaker::test_failure_count_increments_on_load_failure`, `test_failure_count_resets_on_success`, `test_backend_disabled_after_max_consecutive_failures`, `test_reset_failures_clears_disabled_state` — all fail. `AttributeError: 'AsrBackendRegistry' object has no attribute 'reset_failures'`; counter assertion failures on increment/reset.

**Root Cause:** Circuit-breaker API partially removed from `voice_typer/server/asr_registry.py`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/asr_registry.py`
- `tests/test_asr_registry_lifecycle.py`

**Fix:** Restore `reset_failures(self)` method + circuit-breaker counter (increment on failure, reset on success, disable after max consecutive failures). Run `pytest tests/test_asr_registry_lifecycle.py -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-18] — single_instance_posix backend.lock not created at expected path (11 test failures)

**Status:** ❌ Not Fixed

**Description:** `tests/test_single_instance_posix.py::TestFirstInstanceAcquiresLock` (4), `TestSecondInstanceRejected::test_non_silent_writes_stderr_message`, `TestStaleLockRecovery` (5), `TestDispatcherRouting::test_does_not_call_posix_helper_on_windows` — all fail. `backend.lock` not created in config dir; PID not written; stale-lock recovery path broken; dispatcher routing fallback broken.

**Root Cause:** `_ensure_single_instance_posix` no longer creates the lock file at the expected path (likely config-dir resolution regressed).

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/single_instance.py`
- `tests/test_single_instance_posix.py`

**Fix:** Restore the lock file creation at the expected config-dir path, PID writing, stale-lock recovery, and dispatcher routing. Run `pytest tests/test_single_instance_posix.py -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-19] — ModelManager._log includes non-cp1252 char (→) — Windows console crash risk

**Status:** ❌ Not Fixed

**Description:** `tests/test_logging_formatting.py:63` fails: `assert offenders == []` — `model_manager.py:366` has `'charmap' codec can't encode character '\u2192'`. Windows console uses cp1252 by default; the `→` arrow will raise `UnicodeEncodeError` on Windows when the log line is emitted.

**Root Cause:** Non-ASCII Unicode arrow character in a log message — Windows console can't encode it.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/model_manager.py:366`

**Fix:** Replace `→` (U+2192) with ASCII `->` in `model_manager.py:366`. Scan the file for other non-ASCII chars in log messages. Run `pytest tests/test_logging_formatting.py -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-20] — Transcription store_result does not call redact_pii when log_transcriptions=True (PII leak)

**Status:** ❌ Not Fixed

**Description:** `tests/regressions/security_test.py::TestTranscriptionLoggingRedactsPii::test_store_result_calls_redact_pii_when_log_transcriptions_true` fails: `AssertionError: Expected 'redact_pii' to have been called once. Called 0 times.` When `log_transcriptions=True`, the transcription is logged un-redacted.

**Root Cause:** The `redact_pii` call was removed from the `store_result` path in `voice_typer/server/transcription.py`.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/transcription.py`
- `tests/regressions/security_test.py`

**XS-20 test update** (S) — Update `tests/regressions/security_test.py::TestTranscriptionLoggingRedactsPii` to assert on the SHA-256 hash log line instead of `redact_pii` call. The production code is correct (more secure); the test is stale.
**Fix:** Restore the `redact_pii(transcription_text)` call in `store_result` when `log_transcriptions=True`. Run `pytest tests/regressions/security_test.py::TestTranscriptionLoggingRedactsPii -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-21] — Tauri mig19 fixture path stale — tauri-bridge.ts moved (17 errors)

**Status:** ❌ Not Fixed

**Description:** `tests/tauri/mig19/test_reconnect_ux.py` and `test_usepython_bridge.py` — 17 of 21 tauri errors are a single stale-fixture path. The `tauri_bridge_source` fixture asserts `_TAURI_BRIDGE.exists()` and the file `voice_typer/client/src/renderer/src/lib/tauri-bridge.ts` does not exist (it was moved/renamed during the renderer refactor).

**Root Cause:** Fixture path was not updated when `tauri-bridge.ts` was moved/renamed.

**Progress:** None yet.

**Related Files:**
- `tests/tauri/mig19/test_reconnect_ux.py`
- `tests/tauri/mig19/test_usepython_bridge.py`

**Fix:** Update the fixture path in both test files to point to the new location of `tauri-bridge.ts`. Use `rg -l 'tauri-bridge' voice_typer/client/src/` to find the current location. Run `pytest tests/tauri/mig19/ -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-22] — Module-level env mutations leak (DISPLAY/WAYLAND_DISPLAY) — 4 test files

**Status:** ❌ Not Fixed

**Description:** `tests/test_clipboard_paste_restore.py:58-59`, `tests/test_clipboard_regression.py:34-35`, `tests/test_clipboard_borrow_restore.py:43-44`, `tests/test_clipboard_password_detection.py:41-42` all do `os.environ.setdefault('DISPLAY', ':99')` + `os.environ.pop('WAYLAND_DISPLAY', None)` at MODULE LOAD TIME. These leak into the entire test session — break any later test that needs Wayland.

**Root Cause:** Module-level env mutations are not auto-restored (unlike `monkeypatch.setenv`).

**Progress:** None yet.

**Related Files:**
- `tests/test_clipboard_paste_restore.py`
- `tests/test_clipboard_regression.py`
- `tests/test_clipboard_borrow_restore.py`
- `tests/test_clipboard_password_detection.py`

**Fix:** Move `DISPLAY`/`WAYLAND_DISPLAY` setup into a session-scoped autouse fixture in `tests/conftest.py` that uses `monkeypatch.setenv`/`monkeypatch.delenv` for auto-restore. Or use `pytest.fixture(autouse=True)` per-file. Delete the module-level mutations.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-23] — Direct os.environ[...] = ... without restore (4 test files) — order-dependent leaks

**Status:** ❌ Not Fixed

**Description:** `tests/test_e2e_pipeline.py:85`, `tests/test_sec_8_9_10_security_fixes.py:100`, `tests/test_ipc5_error_envelope_parity.py:145`, `tests/test_ipc_dispatch_errors.py:159` all do `os.environ['VOICE_TYPER_CONFIG_DIR'] = str(tmp_path)` (or `_OVERRIDE` variant) directly without try/finally restore. If a later test imports `voice_typer.server.config` and calls `_config_dir()` without its own monkeypatch, it'll resolve to a stale `tmp_path` from a previous test that may have been garbage-collected → `FileNotFoundError`.

**Root Cause:** `_MockApp.__init__` does raw `os.environ[...] = ...` instead of using `monkeypatch.setenv` (which auto-restores).

**Progress:** None yet.

**Related Files:**
- `tests/test_e2e_pipeline.py`
- `tests/test_sec_8_9_10_security_fixes.py`
- `tests/test_ipc5_error_envelope_parity.py`
- `tests/test_ipc_dispatch_errors.py`

**Fix:** Replace direct `os.environ[...] = ...` with `monkeypatch.setenv('VOICE_TYPER_CONFIG_DIR', str(tmp_path))` (requires passing `monkeypatch` into `_MockApp.__init__`). Deduplicate the 4 `_MockApp` classes into a shared helper in `tests/fixtures/`.

**Severity:** 🔴 High

**Category:** Existing failing tests

---

## [XS-24] — build.yml::test push matrix forces architecture: x64 on ALL macOS entries — no arm64 coverage

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

## [XS-25] — 18 of 24 CI jobs have NO timeout-minutes — hung jobs run to 6h GitHub cap

**Status:** ❌ Not Fixed

**Description:** 13 jobs in `build.yml` + 1 in `codeql.yml` + 1 in `client-ci.yml` + 3 in `tauri-build.yml` (aggregate job) have NO `timeout-minutes`. A single hung job (PyInstaller, electron-builder, CodeQL autobuild) consumes a runner for 6 hours. For a tag release, a hung `build-windows` would block the release for 6h.

**Root Cause:** `timeout-minutes` was never added to most jobs.

**Progress:** None yet.

**Related Files:**
- `.github/workflows/build.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/client-ci.yml`
- `.github/workflows/tauri-build.yml`

**Fix:** Add `timeout-minutes` to every job: test/lint=30, build jobs (PyInstaller/electron-builder)=60, CodeQL=60, trivial (version-check, branding-check)=10, slow-tests=60, pip-audit-weekly=30, tauri-build aggregate=10.

**Severity:** 🔴 High

**Category:** CI/CD

---

## [XS-26] — tauri-macos-build.yml missing all 3 caches (cargo, npm, uv) — re-fetches everything every run

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

## [XS-27] — tauri-windows-build.yml cancel-in-progress: true cancels release builds

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

## [XS-28] — macOS/Windows Tauri CI workflows don't apply per-platform config overrides — will hard-fail

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

## [XS-29] — Three versions of `windows` crate in Cargo.lock (0.56, 0.57, 0.61) — compile bloat

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

## [XS-30] — requirements-lock.txt has duplicate unpinned websockets/keyring entries — breaks --require-hashes

**Status:** ❌ Not Fixed

**Description:** `requirements-lock.txt` lines 1783-1784 have unpinned stub entries `websockets>=12.0,<14.0` and `keyring>=25.0,<26.0` (NO hashes) — leftover from CR-12 FIX. The bottom stub block (lines 1777-1784) is a leftover from before CR-24's full regeneration. The comment at line 1777 acknowledges this. Result: `pip install --require-hashes -r requirements-lock.txt` FAILS with 'requirement websockets>=12.0,<14.0 does not contain a hash'.

**Root Cause:** Lockfile regeneration did not remove the pre-regeneration stub block.

**Progress:** None yet.

**Related Files:**
- `requirements-lock.txt:1777-1784`

**Fix:** Delete the stub block (lines 1777-1784). Verify with `pip install --require-hashes -r requirements-lock.txt --dry-run` (Linux sandbox).

**Severity:** 🔴 High

**Category:** Dependency & supply-chain health

---

## [XS-31] — requirements.txt missing 2 pyobjc deps — macOS dev-install broken

**Status:** ❌ Not Fixed

**Description:** `requirements.txt` is missing `pyobjc-framework-CoreFoundation>=9.0` and `pyobjc-framework-ApplicationServices>=9.0` (both present in `pyproject.toml` lines 155, 163). Same class of bug as DEP-3 (which fixed huggingface_hub/keyring/pyrnnoise/pyobjc-framework-Cocoa). A `pip install -r requirements.txt` on macOS will produce a working audio setup but a BROKEN AX-trust probe (permissions.py falls back to `PermissionState.UNKNOWN`) and a BROKEN CoreAudio event-listener (microphone_watcher_coreaudio falls back to 1 Hz polling).

**Root Cause:** Two newer pyobjc framework deps were added to pyproject.toml but never propagated to requirements.txt.

**Progress:** None yet.

**Related Files:**
- `requirements.txt`

**Fix:** Append `pyobjc-framework-CoreFoundation>=9.0; sys_platform == 'darwin'` and `pyobjc-framework-ApplicationServices>=9.0; sys_platform == 'darwin'` to `requirements.txt` (mirroring pyproject.toml lines 155, 163).

**Severity:** 🔴 High

**Category:** Dependency & supply-chain health

---

## [XS-32] — electron-vite pinned to beta (6.0.0-beta.1) — latest stable is 5.0.0

**Status:** ❌ Not Fixed

**Description:** `voice_typer/client/package.json:69` pins `electron-vite: 6.0.0-beta.1` (no caret — exact bare pin). `npm view electron-vite dist-tags` confirms `latest: 5.0.0`, `beta: 6.0.0-beta.1`. Beta versions of build tooling are risky for production releases — they can introduce breaking changes between prereleases, don't get security patches the way released versions do.

**Root Cause:** Project is on the beta channel with no documented rationale.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/package.json:69`

**Fix:** Either downgrade to `^5.0.0` (stable) OR add a comment documenting why the 6.0 beta is required (which specific feature).

**Severity:** 🔴 High

**Category:** Dependency & supply-chain health

---

## [XS-33] — 8 JS vulnerabilities (3 HIGH) — all transitive via electron-builder + shadcn

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

## [XS-34] — Pre-commit + husky conflict — both write .git/hooks/pre-commit

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

## [XS-35] — Husky pre-push too slow (10-15 min) + mypy installs torch (~2GB) — will be universally --no-verify'd

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

## [XS-36] — 27 broad except Exception: pass sites swallow real bugs

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

## [XS-37] — MANIFEST.in dead refs + missing data files (silero_vad.jit, stubs, native sources)

**Status:** ❌ Not Fixed

**Description:** `MANIFEST.in` lines 13, 28, 29 reference `voice_typer/server/assets/` — but the directory does NOT exist (dead config). Missing data files for sdist completeness: (1) `voice_typer/server/silero_vad.jit` — bundled Silero VAD JIT model (PyInstaller spec bundles it); NOT in MANIFEST.in → `pip install voice-typer` from sdist would crash at `torch.jit.load()` on first dictation. (2) `voice_typer/stubs/*.pyi` — pyrefly stubs; NOT in MANIFEST.in → from-sdist install + `pyrefly check` emits 73 false-positive missing-import errors. (3) `voice_typer/server/native/*.c` and `*.swift` (native listener sources) — NOT in MANIFEST.in → from-sdist install can't recompile native binaries. (4) `requirements-lock.txt` — NOT in MANIFEST.in → defeats its purpose for downstream consumers.

**Root Cause:** MANIFEST.in was not updated when data files were added.

**Progress:** None yet.

**Related Files:**
- `MANIFEST.in`

**Fix:** Regenerate MANIFEST.in: (a) remove dead `voice_typer/server/assets/` references; (b) add `recursive-include voice_typer/stubs *.pyi`; (c) add `include voice_typer/server/silero_vad.jit`; (d) add `recursive-include voice_typer/server/native *.c *.swift *.json`; (e) add `include requirements-lock.txt`; (f) add `recursive-include tests *.py`.

**Severity:** 🔴 High

**Category:** Build pipeline

---

## [XS-38] — Pre-existing vitest failure: window-open-logs.test.ts:104 — incomplete electron mock

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

## [XS-39] — conftest.py docstring contradicts empirically-verified behavior

**Status:** ❌ Not Fixed

**Description:** `conftest.py:11-13` states: 'Coverage-fail-under is now enforced ONLY by an explicit CI step that opts in (`pytest tests/ --cov-fail-under=65`). Local subset runs no longer see the threshold.' Three contradictions: (1) build.yml:208-209 invokes pytest with NO explicit `--cov-fail-under=65`; the threshold comes from `addopts`. (2) Empirically, subset runs DO see the threshold. (3) The docstring's suggested workarounds work but are presented as convenience rather than the only way.

**Root Cause:** Docstring was written to describe the INTENDED behavior of the CR-94 fix (which mutated `options.cov_fail_under`), but that mutation was removed and never replaced.

**Progress:** None yet.

**Related Files:**
- `conftest.py:1-33`

**Fix:** After applying XS-9 (move --cov-fail-under to CI only), update the conftest.py docstring to accurately describe: 'Coverage threshold is enforced ONLY in CI (build.yml passes --cov-fail-under=65 explicitly). Local subset runs do not enforce coverage.'

**Severity:** 🟡 Medium

**Category:** Testing infrastructure

---

## [XS-40] — Missing --strict-markers — marker typos silently disable opt-out mechanisms

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

## [XS-41] — tests/tauri/conftest.py has redundant asyncio marker hook

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

## [XS-42] — Cross-test helper duplication — 26 test files copy-paste factory functions

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

## [XS-43] — Unused/dead WAV fixtures (silence.wav, tone.wav, noise.wav) — 96KB dead test data

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

## [XS-44] — Stale stubs: AppKit.pyi missing NSPasteboard/NSPasteboardItem/NSWorkspace; ApplicationServices.pyi missing AXUIElement*

**Status:** ❌ Not Fixed

**Description:** `voice_typer/stubs/AppKit.pyi` only declares `NSApplication, NSStatusBar, NSStatusItem, NSImage, NSMenu, NSMenuItem, NSEvent, NSWindow, NSView, NSResponder, NSTimer`. But runtime code in `clipboard_snapshot.py:380,417,430` and `clipboard_target_safety.py:637` accesses `AppKit.NSPasteboard`, `AppKit.NSPasteboardItem`, `AppKit.NSWorkspace` — none present. `voice_typer/stubs/ApplicationServices.pyi` only declares `AXIsProcessTrustedWithOptions`, `AXIsProcessTrusted`, `AXMakeProcessTrusted`. But `clipboard_target_safety.py:650,659,673,692` accesses `AXUIElementCreateApplication`, `AXUIElementCopyAttributeValue` — none present. Pyrefly reports 56 missing-attribute errors (mostly these).

**Root Cause:** Stubs were not updated when clipboard code added macOS pasteboard/AX API calls.

**Progress:** None yet.

**Related Files:**
- `voice_typer/stubs/AppKit.pyi`
- `voice_typer/stubs/ApplicationServices.pyi`

**Fix:** Add `NSPasteboard: Any`, `NSPasteboardItem: Any`, `NSWorkspace: Any` to `AppKit.pyi`. Add `def AXUIElementCreateApplication(pid: Any) -> Any: ...` and `def AXUIElementCopyAttributeValue(element: Any, attribute: Any, value: Any) -> int: ...` to `ApplicationServices.pyi`. (Two-line additions per stub, matching the existing permissive `Any` typing style.)

**Severity:** 🟡 Medium

**Category:** Testing infrastructure

---

## [XS-45] — torch not in autouse mock_heavy_imports fixture — 6 local mock setups + 17s import tax

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

## [XS-46] — mock_heavy_imports emits uncategorized UserWarning 102× per file when numpy missing

**Status:** ❌ Not Fixed

**Description:** `tests/conftest.py:256, 291, 316` call `warnings.warn(...)` without specifying a category (defaults to `UserWarning`). When the test environment lacks `numpy`, `voice_typer.server.app` fails to import and these 3 warnings fire once per test. Observed: `test_config.py` alone emits 102 copies of each UserWarning (204 total warnings on a 102-test file). Real warnings get buried.

**Root Cause:** Warnings emitted without a category and without per-session dedup.

**Progress:** None yet.

**Related Files:**
- `tests/conftest.py:256,291,316`

**Fix:** (a) Give these a dedicated `class MockHeavyImportsWarning(UserWarning): ...` so contributors can filter them. (b) Emit them only ONCE per session using a module-level `_warned` flag. (c) Ensure numpy is installed in the test venv (XS-1 test-infra issue).

**Severity:** 🟡 Medium

**Category:** Existing warnings and errors

---

## [XS-47] — Ruff ratchet --regenerate bypass hole when baseline missing/corrupt

**Status:** ❌ Not Fixed

**Description:** `scripts/ruff_ratchet_check.py:222-248` `regenerate()` function: the 'refuse to grow' check is gated on `BASELINE_PATH.is_file()` AND successful JSON parse. If `ruff-baseline.json` is deleted or corrupted, `--regenerate` writes a new baseline at ANY count (even a massive regression). A contributor (or accident) who `rm ruff-baseline.json` then `--regenerate` can lock in an arbitrary regression.

**Root Cause:** Refuse-to-grow check has a missing-baseline escape hatch.

**Progress:** None yet.

**Related Files:**
- `scripts/ruff_ratchet_check.py:222-248`

**Fix:** Refuse to regenerate if the baseline is missing/corrupt — require an explicit `--force` flag or a manual baseline file creation first.

**Severity:** 🟡 Medium

**Category:** Testing infrastructure

---

## [XS-48] — Stale ruff_ratchet_check.py docs say 'voice_typer/server/' but CI uses full scope

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

## [XS-49] — 5 stale PORT-CANDIDATE / DELETE-CANDIDATE skipped tests in tests/regressions/

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

## [XS-50] — 4 xfail(strict=False) in test_ipc_server_shim.py — abandoned work, never run

**Status:** ❌ Not Fixed

**Description:** `tests/test_ipc_server_shim.py:38,63,76,95` — 4 tests marked `xfail(strict=False, reason=_XFAIL_REASON)` where `_XFAIL_REASON` says 'Fix-A shim reduction abandoned'. If the work is abandoned, these tests document intent but never run. Keeping `xfail(strict=False)` means they could silently start passing (xpass) without anyone noticing.

**Root Cause:** Abandoned work left as xfail instead of being deleted or converted to skip.

**Progress:** None yet.

**Related Files:**
- `tests/test_ipc_server_shim.py`

**Fix:** Either delete the tests (abandoned = no value), OR convert to `pytest.mark.skip(reason='abandoned — see ISSUE-XXX')` to make it explicit they're not expected to pass.

**Severity:** 🟡 Medium

**Category:** Existing failing tests

---

## [XS-51] — 2 xfail in test_shutdown_controller.py reference stale 'wiring pending' TODOs

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

## [XS-52] — Tight upper-bound timing assertions will flake on slow CI (16 sites)

**Status:** ❌ Not Fixed

**Description:** 16 tests assert tight upper-bound timings that will flake on slow CI runners: `tests/test_g_perf_reliability_fixes.py:215` (`elapsed_ms < 5.0` — 5ms budget, any GC pause blows it), `tests/test_history_db.py:496` (`< 50.0`), `tests/test_thread_registry.py:275,369,403` (`< 0.4`, `< 0.5`, `< 1.0` with 0.1s join_timeout — 4× margin too tight), `tests/test_prewarm.py:529,543,575,611` (`< 0.1`, `< 0.5`, `< 2.0`), `tests/test_recording.py:504` (`< 1.0`), `tests/test_recording_discard.py:160` (`< 0.300`), `tests/test_smart_duck_monitor.py:440` (`< 0.5`), `tests/test_audio_quality_controller.py:122,389` (`< 2.0`), `tests/test_rw7_rw8_audio_callback.py:465` (`< 500`), `tests/test_e2e_pipeline.py:611` (`4.5 <= elapsed <= 8.0` — tight lower AND upper bound).

**Root Cause:** Timing assertions are too tight for CI runner variance.

**Progress:** None yet.

**Related Files:**
- `tests/test_g_perf_reliability_fixes.py:215`
- `tests/test_history_db.py:496`
- `tests/test_thread_registry.py:275,369,403`
- `tests/test_prewarm.py:529,543,575,611`
- `tests/test_recording.py:504`
- `tests/test_recording_discard.py:160`
- `tests/test_smart_duck_monitor.py:440`
- `tests/test_audio_quality_controller.py:122,389`
- `tests/test_rw7_rw8_audio_callback.py:465`
- `tests/test_e2e_pipeline.py:611`

**Fix:** Loosen or remove the tight upper-bound timing assertions. Specifically: increase `test_g_perf_reliability_fixes.py:215` from 5ms to 50ms (or remove — the test already verifies the filter chain runs on a worker thread via mock assertions); increase `test_history_db.py:496` from 50ms to 500ms; widen `test_e2e_pipeline.py:611` to `3.0 <= elapsed <= 15.0` (or mock the auth timeout).

**Severity:** 🟡 Medium

**Category:** Existing failing tests

---

## [XS-53] — Race-prone time.sleep() synchronization in tests (30+ sites)

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

## [XS-54] — build.yml has no top-level permissions: block — 7 of 11 jobs inherit repo-default

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

## [XS-55] — No environment: release gating on signing jobs — secrets exposed to tag-push runs

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

## [XS-56] — pip-audit has continue-on-error: true — vulnerabilities NEVER fail the build

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

## [XS-57] — slow-tests job has continue-on-error: true — failures invisible in CI status

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

## [XS-58] — CONTRIBUTING.md stale about mypy hook args (contradicts CR-183 fix)

**Status:** ❌ Not Fixed

**Description:** `CONTRIBUTING.md:355-358, 446-448` describes mypy hook with `--ignore-missing-imports` and `--no-strict-optional` 'to keep the dev loop fast'. But `.pre-commit-config.yaml:19-29` (CR-183 comment) explicitly states these args were REMOVED so mypy reads `pyproject.toml` (`ignore_missing_imports = false`, strict-optional enabled, per-module overrides).

**Root Cause:** CONTRIBUTING.md was not updated when CR-183 removed the mypy args.

**Progress:** None yet.

**Related Files:**
- `CONTRIBUTING.md:355-358,446-448`

**Fix:** Update CONTRIBUTING.md lines 355-358 and 446-448 to match the post-CR-183 config (mypy reads pyproject.toml; no `--ignore-missing-imports` override).

**Severity:** 🟡 Medium

**Category:** CI/CD

---

## [XS-59] — No SKIP= / --no-verify documentation in CONTRIBUTING.md

**Status:** ❌ Not Fixed

**Description:** Grep for `SKIP=|--no-verify|skip hooks` in CONTRIBUTING.md returns ZERO matches. Pre-commit framework supports `SKIP=mypy git commit -m '...'` and `git commit --no-verify`. Neither is documented. Given XS-5 (broken ruff hook) and XS-35 (slow pre-push), this is acutely needed.

**Root Cause:** Escape hatches never documented.

**Progress:** None yet.

**Related Files:**
- `CONTRIBUTING.md`

**Fix:** Add a 'Skipping hooks' subsection documenting: `SKIP=ruff git commit ...` (skip one hook), `SKIP=mypy,client-typecheck git commit ...` (skip multiple), `git commit --no-verify` (skip all pre-commit), `git push --no-verify` (skip pre-push), and when to use each.

**Severity:** 🟡 Medium

**Category:** CI/CD

---

## [XS-60] — Dev container VS Code settings use Prettier/ESLint but project uses Biome

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

## [XS-61] — Dev container postCreateCommand does not install pre-commit hooks

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

## [XS-62] — lint-staged has redundant *.py entry + husky bypasses it

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

## [XS-63] — tsconfig strict flags missing (noUnusedLocals, noUnusedParameters, noImplicitReturns, noFallthroughCasesInSwitch)

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

## [XS-64] — biome.json deprecated recommended: true + global noConsole: off + 2 stale biome-ignores

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

## [XS-65] — Production console.info calls in useStatsShare.ts + dead debug-test.test.tsx

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

## [XS-66] — 6 dead exports/code in TS client (TestReviewPanel, Vocabulary, branding, logging, useSettingsConfig)

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

## [XS-67] — vite build warnings: 2 font asset warnings + 2 large chunks (>1 MB)

**Status:** ❌ Not Fixed

**Description:** `electron-vite build` emits 2 font warnings (`/fonts/InterVariable.woff2` and `/fonts/InterVariable-Italic.woff2` referenced but didn't resolve at build time). 2 large chunks: `out/renderer/assets/index-Csi7j309.js` (1,044 KB) and `out/renderer/assets/tauri-bridge-B27Dtmua.js` (1,410 KB — unusually large for a tauri-bridge chunk; suggests shared deps lumped in). No `chunkSizeWarningLimit` set.

**Root Cause:** Font path resolution + chunking strategy not optimized.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/electron.vite.config.ts`

**Fix:** Verify `/fonts/InterVariable*.woff2` actually ship at that path in the packaged .asar; if not, fix the CSS/HTML reference to a project-relative path. Add `build.chunkSizeWarningLimit: 600` and/or `manualChunks` config to surface future regressions. Investigate why the tauri-bridge chunk is 1.4 MB.

**Severity:** 🟡 Medium

**Category:** Build pipeline

---

## [XS-68] — typecheck:root is a no-op against solution-style tsconfig

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

## [XS-69] — Vitest coverage thresholds configured but NOT enforced — @vitest/coverage-v8 not installed, --coverage never passed

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

## [XS-70] — PytestUnraisableExceptionWarning project-wide filter likely stale

**Status:** ❌ Not Fixed

**Description:** `pyproject.toml:407` filter `ignore::pytest.PytestUnraisableExceptionWarning` is likely stale. Across 226 tests in 7 files run with `-W error::pytest.PytestUnraisableExceptionWarning` and `filterwarnings=` cleared, ZERO warnings fire. The only known-true-positive site is `tests/test_tray.py:186-188`, which already has its own LOCAL `warnings.simplefilter('ignore', pytest.PytestUnraisableExceptionWarning)` inside a `with warnings.catch_warnings():` block. The project-wide filter masks any NEW real `__del__`-raises bugs across the entire test suite.

**Root Cause:** Project-wide filter predates the local fix and was never removed.

**Progress:** None yet.

**Related Files:**
- `pyproject.toml:407`

**Fix:** Remove line 407 (`ignore::pytest.PytestUnraisableExceptionWarning`). The local filter in `tests/test_tray.py:187` is sufficient. Run full pytest suite to confirm zero failures. If any test now fails, add a targeted `module:PytestUnraisableExceptionWarning` filter (NOT blanket).

**Severity:** 🟡 Medium

**Category:** Existing warnings and errors

---

## [XS-71] — Python-side guard for 'no blanket ResourceWarning filter' was lost in RW-1 TS rewrite

**Status:** ❌ Not Fixed

**Description:** The TS test `electron-ipc-build-behavior.test.tsx:1051` explicitly says it is 'a rewrite of TestNoBlanketResourceWarningFilter' — but the original Python test no longer exists in `tests/`. The TS test runs only when the JS/TS test suite runs (`vitest`); it does NOT run as part of `pytest`. If a contributor only runs `pytest`, the guard is invisible.

**Root Cause:** Python guard deleted during RW-1 rewrite without preserving a Python-side mirror.

**Progress:** None yet.

**Related Files:**
- `tests/test_ruff_ratchet.py (or new tests/test_pyproject_warnings.py)`

**Fix:** Restore a tiny Python guard test that reads `pyproject.toml` and asserts no line in `filterwarnings` starts with `ignore::ResourceWarning`. Mirror the TS assertion.

**Severity:** 🟡 Medium

**Category:** Testing infrastructure

---

## [XS-72] — No CODEOWNERS file — no auto-assignment of reviewers

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

## [XS-73] — Dependabot github-actions ecosystem has no groups: block — PR flooding

**Status:** ❌ Not Fixed

**Description:** `.github/dependabot.yml` covers all 4 ecosystems (pip, npm, cargo, github-actions) with grouping for npm/cargo/pip. The `github-actions` ecosystem has NO `groups:` block — each action update (actions/checkout, actions/setup-python, etc.) gets its own PR. With ~10 distinct actions across 9 workflows, this floods the PR queue on Monday mornings.

**Root Cause:** github-actions group never added.

**Progress:** None yet.

**Related Files:**
- `.github/dependabot.yml`

**Fix:** Add `groups: actions-deps: patterns: ['*']` to the `github-actions` ecosystem.

**Severity:** 🟡 Medium

**Category:** CI/CD

---

## [XS-74] — 9 of 22 direct Python deps have no inline version-policy comment

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

## [XS-75] — 8 platform-only Python deps have no upper bound — future breaking changes can flow in

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

## [XS-76] — pystray (LGPL, unmaintained 3+ years) + pycaw (unmaintained 3+ years) — supply-chain risk

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

## [XS-77] — Pre-existing TS renderer test failures (52 tests) — multiple root causes

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

## [XS-78] — Critical TS main-process modules untested (handle-message, send-to-python, python-call-handler, single_instance, bubble-handlers)

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

## [XS-79] — privacy_handlers.py (217 LOC) has zero direct tests

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

## [XS-80] — WinVolumeBackend ducking logic untested (smoke only) + MacVolumeBackend CoreAudio path untested

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

## [XS-81] — hotkeys/capture.py::capture_custom_hotkey is DEAD CODE (127 LOC)

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

## [XS-82] — Dead code: ModelManager + RecordingController have no dedicated test files (1131 + 919 LOC)

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

## [XS-83] — build:dist:electron skips prebuild (icon generation) — will fail on fresh checkout

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

## [XS-84] — No pytest-xdist — 331 test files run serially on each matrix leg (CI minutes cost)

**Status:** ❌ Not Fixed

**Description:** No `pytest-xdist` installed, no `-n auto` flag. All 331+ Python test files run SERIALLY on each matrix leg. The `test` job runs on 3 OSes × 1 Python (PR) or 3 OSes × 4 Pythons (push) = up to 12 serial full-suite runs per CI invocation.

**Root Cause:** Parallelism never added.

**Progress:** None yet.

**Related Files:**
- `pyproject.toml (test extras)`
- `.github/workflows/build.yml:208`

**Fix:** Add `pytest-xdist` to `[test]` extra. Change CI pytest invocation to `pytest tests/ -n auto --dist=loadgroup ...`. Expect ~2-3x speedup on 12-leg matrix.

**Severity:** 🟡 Medium

**Category:** Testing infrastructure

---

## [XS-85] — No JUnit XML + no PR test-result reporting — failed tests buried in CI logs

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

## [XS-86] — No coverage ratchet — coverage can drop from 70% → 65.01% without CI signal

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

## [XS-87] — mutation.yml is dead code (if: false) + doc drift (mutmut still in dev extras)

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

## [XS-88] — stale eslint-disable directives (3 confirmed stale) in TS client

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

## [XS-89] — Unused pytest-mock in [test] extras (zero usages)

**Status:** ❌ Not Fixed

**Description:** `pyproject.toml:204` lists `pytest-mock` in `[test]` extras. Grep for `\bmocker\b` (the pytest-mock fixture) across `tests/**/*.py` returns ZERO matches. The TEST-033 mocking convention explicitly recommends `monkeypatch` and `unittest.mock.patch` — never `mocker`. Every `pip install .[test]` pulls in pytest-mock for no benefit.

**Root Cause:** Dependency never removed after the project standardized on monkeypatch + unittest.mock.

**Progress:** None yet.

**Related Files:**
- `pyproject.toml:204`

**Fix:** Remove `pytest-mock` from the `test` list in `pyproject.toml:204`.

**Severity:** 🟢 Low

**Category:** Dependency & supply-chain health

---

## [XS-90] — stale doc references in tests/manual/ (test_round10_bugfixes.py, test_new_dead_002_scripts.py don't exist)

**Status:** ❌ Not Fixed

**Description:** `tests/manual/diagnose_f2.py:11` and `tests/manual/README.md:30,32` reference `tests/test_round10_bugfixes.py` and `tests/test_new_dead_002_scripts.py` — neither file exists. `tests/manual/README.md:30-31` also has an orphaned parenthetical.

**Root Cause:** Doc references never updated after test files were renamed/deleted.

**Progress:** None yet.

**Related Files:**
- `tests/manual/diagnose_f2.py:11`
- `tests/manual/README.md:30,32`

**Fix:** Update the docstrings/README to reference the actual current test files (`test_e2e_smoke.py`, `test_e2e_regression.py`, `test_transcription.py::TestFallbackChain`). Fix the orphaned parenthetical.

**Severity:** 🟢 Low

**Category:** Testing infrastructure

---

## [XS-91] — stale stub comment in pyproject.toml references nonexistent webrtcvad stub

**Status:** ❌ Not Fixed

**Description:** `pyproject.toml:311` comment: `# PYREFLY-001: local stubs for platform-only deps (pycaw, comtypes, pyobjc, webrtc)`. There is NO `webrtcvad.pyi` stub (the only mention of 'webrtcvad' is in `FEATURES.md:295` as a NOT-SUPPORTED item).

**Root Cause:** Stale comment.

**Progress:** None yet.

**Related Files:**
- `pyproject.toml:311`

**Fix:** Drop 'webrtc' from the comment.

**Severity:** 🟢 Low

**Category:** Testing infrastructure

---

## [XS-92] — Redundant tmp_config_dir fixture in tests/app/conftest.py (identical to project-wide)

**Status:** ❌ Not Fixed

**Description:** `tests/app/conftest.py:16-25` `tmp_config_dir` fixture has an identical body to `tests/conftest.py:327-331` `tmp_config_dir`. The `tests/app/conftest.py` docstring justifies the override for 'behavioural parity' — but the behavior IS identical. A future change to the project-wide fixture would NOT propagate to `tests/app/` tests because the override shadows it.

**Root Cause:** Local override copied verbatim during a test split and never removed.

**Progress:** None yet.

**Related Files:**
- `tests/app/conftest.py:16-25`

**Fix:** Delete the `tmp_config_dir` fixture from `tests/app/conftest.py:16-25`. The project-wide fixture will be picked up automatically.

**Severity:** 🟢 Low

**Category:** Testing infrastructure

---

## [XS-93] — tests/tauri/ missing __init__.py (inconsistent with siblings)

**Status:** ❌ Not Fixed

**Description:** `tests/tauri/` has no `__init__.py`, but `tests/__init__.py`, `tests/handlers/__init__.py`, `tests/app/__init__.py`, and all 5 `tests/tauri/mig*/__init__.py` exist. With `--import-mode=prepend` (default), if two test files in `tests/tauri/` and `tests/tauri/mig17/` ever share a name, the second import would shadow the first.

**Root Cause:** `tests/tauri/__init__.py` was never created or was deleted.

**Progress:** None yet.

**Related Files:**
- `tests/tauri/__init__.py (new file)`

**Fix:** Either (a) add an empty `tests/tauri/__init__.py` for consistency, OR (b) switch the project to `--import-mode=importlib` in addopts (pytest 9 recommended default).

**Severity:** 🟢 Low

**Category:** Testing infrastructure

---

## [XS-94] — 6 redundant @pytest.mark.asyncio decorators (auto mode handles it)

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

## [XS-95] — tests/test_e2e_smoke.py and test_e2e_regression.py are misnamed (unit tests, not e2e)

**Status:** ❌ Not Fixed

**Description:** `tests/test_e2e_smoke.py` (14 tests) contains unit-level regression guards for individual fixes (STARTUP-1/2/4/6/7, #8/#13, UX-005, ARCH-007/008, T021, APP_NAME branding). No live IPC server, no real microphone, no Electron. `tests/test_e2e_regression.py` (24 tests) contains source-inspection, prewarm import filtering, POSIX prewarm scheduler, Windows autostart. None start a real IPC server or exercise real audio. The 'e2e' label is misleading.

**Root Cause:** Files named aspirationally; content is unit-level.

**Progress:** None yet.

**Related Files:**
- `tests/test_e2e_smoke.py`
- `tests/test_e2e_regression.py`

**Fix:** Rename `test_e2e_smoke.py` → `test_refactor_invariants.py` and move `TestBrandingConstants` to a dedicated `test_branding.py`. Rename `test_e2e_regression.py` → `test_prewarm_autostart_regression.py`. (Low priority — cosmetic.)

**Severity:** 🟢 Low

**Category:** Test coverage gaps

---

## [XS-96] — test_i18n_completeness.py tests CLIENT i18n, not server i18n.py (misleading filename)

**Status:** ❌ Not Fixed

**Description:** `tests/test_i18n_completeness.py` (799 lines) tests the CLIENT i18n JSON files at `voice_typer/client/src/renderer/src/i18n/translations/en.json` — a completely different module from `voice_typer/server/i18n.py` (245 lines, provides `t(key, **fmt)` translation). Server `i18n.py` has NO direct unit test. Untested paths: locale switching, fallback chain, format interpolation failure, thread-safety.

**Root Cause:** Filename collision masks a coverage gap.

**Progress:** None yet.

**Related Files:**
- `tests/test_i18n.py (new file for server i18n)`

**Fix:** Create `tests/test_i18n.py` testing server `i18n.py`: `t()`, `register_locale`, `set_locale`, fallback chain, format interpolation failure, thread-safety. (The existing `test_i18n_completeness.py` stays for client i18n.)

**Severity:** 🟢 Low

**Category:** Test coverage gaps

---

## [XS-97] — docs/adr/0000-template.md missing (test_remaining_fixes.py fails)

**Status:** ❌ Not Fixed

**Description:** `tests/test_remaining_fixes.py:177` fails: `AssertionError: ADR template should exist`. `docs/adr/0000-template.md` is missing.

**Root Cause:** ADR template file was deleted.

**Progress:** None yet.

**Related Files:**
- `docs/adr/0000-template.md (new file)`

**Fix:** Restore `docs/adr/0000-template.md` (a minimal ADR template: Title, Status, Context, Decision, Consequences). Run `pytest tests/test_remaining_fixes.py::TestDocsADirectory::test_template_exists -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🟢 Low

**Category:** Existing failing tests

---

## [XS-98] — test_sec_8_9_10_security_fixes.py:1072 off-by-one (32-char literal asserted to be 31)

**Status:** ❌ Not Fixed

**Description:** `tests/test_sec_8_9_10_security_fixes.py:1072` asserts `len(token) == 31` but the literal `'0123456789abcdefghij123456789abc'` is 32 chars. Test bug — the production redaction logic is likely correct.

**Root Cause:** Off-by-one in test fixture.

**Progress:** None yet.

**Related Files:**
- `tests/test_sec_8_9_10_security_fixes.py:1072`

**Fix:** Change `len(token) == 31` to `len(token) == 32` (or shorten the literal to 31 chars). Run `pytest tests/test_sec_8_9_10_security_fixes.py::TestG4L06RedactSecretThreshold20::test_31_char_bare_token_redacted -q --timeout=30 -o addopts=''` to verify.

**Severity:** 🟢 Low

**Category:** Existing failing tests

---

## [XS-99] — test_hotkey_validation.py:161 — <ctrl>+<alt>+<f2> is Linux-reserved (VT2 switch)

**Status:** ❌ Not Fixed

**Description:** `tests/test_hotkey_validation.py:161` parametrize entry `<ctrl>+<alt>+<f2>` fails on Linux: `Expected '<ctrl>+<alt>+<f2>' to be allowed, but got: reserved by operating system (linux)`. `<ctrl>+<alt>+<f2>` switches to VT2 on Linux. The test parametrization should be platform-conditional.

**Root Cause:** Platform-specific expectation not guarded.

**Progress:** None yet.

**Related Files:**
- `tests/test_hotkey_validation.py:161`

**Fix:** Make the `<ctrl>+<alt>+<f2>` parametrize entry platform-conditional (skip on Linux, or use `pytest.mark.skipif(sys.platform == 'linux', reason='VT2 reserved')`).

**Severity:** 🟢 Low

**Category:** Existing failing tests

---

## [XS-100] — test_e2e_regression.py:200 — mock torch module lacks __spec__

**Status:** ❌ Not Fixed

**Description:** `tests/test_e2e_regression.py:200` fails: `ValueError: torch.__spec__ is not set`. `_warm_package_files('torch')` calls `importlib.util.find_spec('torch')`, but the test mocks `torch` as a stub module without `__spec__`.

**Root Cause:** Incomplete mock — missing `__spec__` attribute.

**Progress:** None yet.

**Related Files:**
- `tests/test_e2e_regression.py:200`

**Fix:** Set `__spec__` on the fake `torch` module in the test mock. Pattern: `mock_torch.__spec__ = importlib.util.spec_from_loader('torch', loader=None)`.

**Severity:** 🟢 Low

**Category:** Existing failing tests

---

## [XS-101] — wintypes.VOID absent on Linux — 8 test__security_attributes.py failures

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

## [XS-102] — shadcn in dependencies, not devDependencies

**Status:** ❌ Not Fixed

**Description:** `voice_typer/client/package.json:91` has `shadcn` in `dependencies`. shadcn is a scaffolding CLI (code-generator); it should be a devDep. Moving it would shrink production install footprint.

**Root Cause:** Misclassified dependency.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/package.json:91`

**Fix:** Move `shadcn` from `dependencies` to `devDependencies`.

**Severity:** 🟢 Low

**Category:** Dependency & supply-chain health

---

## [XS-103] — dead require in translate-i18n.js (translate-i18n-partial.js doesn't exist)

**Status:** ❌ Not Fixed

**Description:** `voice_typer/client/scripts/translate-i18n.js:56` does `require('./translate-i18n-partial.js')` inside a try/catch. The file does NOT exist. The catch swallows the `MODULE_NOT_FOUND` error.

**Root Cause:** Dead require left from a removed feature.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/scripts/translate-i18n.js:56`

**Fix:** Remove the dead `require('./translate-i18n-partial.js')` block.

**Severity:** 🟢 Low

**Category:** Build pipeline

---

## [XS-104] — tokio features = ['full'] is over-broad

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

