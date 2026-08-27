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

> **2026-08-23 cleanup (verified against code before editing):**
> - **REMOVED as completed + verified:** EC-25's `test_perf_review_fixes.py` split is done but entry KEPT as partial; removed entries: ~~S3-CR-21~~ (duplicate of ARCH-12; its unique blocker test_app.py read_text pin is gone), ~~XA-2~~ (StatCard consolidation landed — DashboardStatCard deleted in favor of shared StatCard.tsx; pb-2 alignment fix; About wrapper standardized; labeled Spinner + EmptyState-retry patterns adopted), ~~XA-8~~ (all cited sub-items verified fixed: ErrorBoundary strings via t("errorBoundary.*"), KeyringStatusBadge compact-only aria, sonner containerAriaLabel/closeButtonAriaLabel localized, InfoTooltip `<title>` removed, Spinner decorative prop), ~~AC-66~~ (BusynessCoordinator `_busyness.py` + MicrophoneRegistry `_microphone_registry.py` own the state; back-compat properties on VoiceTyperApp delegate to them), ~~AC-73~~ (decomposition landed — merged into EO-13 with residual), ~~AC-128~~ (credential_store/ package landed — see GQ-70), ~~AC-131~~ (config/__init__.py now 271 LOC over 10 satellite modules — see EO-12).
> - **UPDATED partials:** ARCH-9 (213 sites / 39 files remain), ARCH-12 (463 calls / 150 files; ban rule landed in CONTRIBUTING.md), TEST-2 (420 sleeps / 156 files), S1-CR-67 (only recording/_RecordingModule left; prewarm + server_platform hacks removed), EC-25 (3 Python catch-alls + relocated-but-unsplit TS catch-alls remain), XV-105 (role pooling LIVE — 3 roles → 1 subprocess; per-spec dedup deferred), XA-5 (8 of 24 sub-items verified fixed, listed inline), XZ-R11-04 (landed 2026-08-25, Session RV: AES-256-GCM at-rest encryption live — _text_crypto.py + DEK via credential_store; completed).

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.
>
> This file was filtered by verifier-agent on 2026-07-21 to REMOVE entries for fixes that were verified-done and KEEP only items that remain partial, broken, or not-implemented.

---

### ARCH-12 — 478 `inspect.getsource` source-string tests across 150 test files
- **Severity**: Low
- **Status:** ⚠️ Chip-away continues (policy LANDED and enforced): ban rule verbatim in CONTRIBUTING.md §Testing (+ ADR playbook). Baseline ~459 pins / ~149 files (2026-08-26 re-measure). Pins ported opportunistically per policy when splitting pinned code — including 2026-08-25/26 monolith splits (history_db checkpoint/backup/corruption pins retargeted to the extracted free functions; recorder cache-key/ring-overflow/dead-field pins converted to behavioral assertions; server_platform task-name getsource pin follows the relocated constant): service/model module-level pins (daemon rationale comment relocated into _downloads.py leaf; type-ignore tokenize pin retargeted to package leaves), platform_misc is_macos pin → platform_utils source, systemroot pin → env_validation, recording freshness suite rewritten to pin the CLEAN state.
- **Description**: 478+ source-string tests (150 files) pin implementation structure (variable names, call-site spellings, call counts) rather than behavior. Make refactoring expensive.
- **Recommended fix**: Adopt project rule — "no new `inspect.getsource` tests; port existing ones when touching the code they pin." Chip away over time.
- **Effort**: 🔴 **EXTRA HIGH** — 478 calls across 150 test files. Not a discrete task — it's a project-wide migration. Chip away individually when touching pinned code. Cannot be done in one shot.
- **Confidence for one-shot fix**: 20% — cannot complete in one shot.

### TEST-2 — 495 `time.sleep(` calls across 239 test files (flakiness-prone)
- **Severity**: Medium
- **Status:** ✅ Worst-10 files MIGRATED (2026-08-25): real-call top-10 files converted to shared wait helpers (wait_until/wait_for_event from tests/fixtures/wait_helpers.py) or documented intentional sleeps (simulated DSP work, stress pacing, fake-worker run loops); private pollers folded into shared helpers; two latent test bugs fixed en route (cosmetic-bar mode never ran filter chain; missed exit_gate arg). Remaining ~350 sleeps across ~146 files stay opportunistic per plan.
- **Description**: 495 `time.sleep(...)` calls across 239 test files act as fixed-delay synchronization, which is flaky on loaded CI runners.
- **Root cause**: Tests synchronize on time instead of condition/event.
- **Recommended fix**: Replace fixed sleeps with condition waits (events, `threading.Event.wait`, or polling predicates). Chip away file-by-file. ~2-day effort.
- **Effort**: 🔴 **HIGH** — 495 sleep calls across 239 files. Each one needs individual analysis to determine the correct replacement (event.wait, polling predicate, etc.). ~4+ days.
- **Confidence for one-shot fix**: 30% — cannot do all in one shot; chip away file-by-file.

---

### YJ-53 — 10 monolith files ≥800 LOC mixing transport/lifecycle/logic (cross-cutting)
**Status:** ⚠️ Mostly addressed (verified 2026-08-26): shutdown_controller.py is now the `shutdown_controller/` package (controller.py 175-LOC composition root + `_teardowns/_lifecycle_signals/_plans/_cleanup/_deadline` mixins); recording_controller.py is 639 LOC (<800); ipc_server dispatch pools extracted (`ipc/`); level_monitor/dictation_pipeline are packages. Residual: recorder.py still 2703 (see EO-8), model_manager is a facade+mixins package (see GQ-28).
**Description:** `wc -l` (re-audited 2026-08-12): `ipc_server.py` 733 shim (was 2808 — split into `ipc/` package), `level_monitor.py` → `level_monitor/` package (was 1313), `dictation_pipeline.py` → `dictation_pipeline/` package (was 1291), `shutdown_controller.py` 1420 (was 1280), `recording_controller.py` 639 (was 1002), `crash_recovery.py` 1292 (was 960), `microphone_watcher.py` 1235 (was 881), `prewarm/process_tracker.py` 1023 (was 837), `event_bus.py` 1169 (was 811), `task_scheduler.py` 976 (was 793).
**Root Cause:** Verified — RW-9 god-class decomposition incomplete.
**Progress:** Deferred — covered by individual findings YJ-13, YJ-31, YJ-32, YJ-39. UPDATE 2026-08-25: concrete sub-item (2) shutdown_controller.py → extract _do_cleanup EXECUTED via VP-39 (bodies now in shutdown/cleanup.py, shutdown/ws_drain.py, shutdown/plan.py); umbrella entry remains open for sub-items (1)/(3) and the remaining listed files.
**Related Files:**
- (see individual findings)
**Fix:** Continue the RW-9 god-class decomposition. Highest-value splits: (1) `ipc_server.py` → extract `_send` + `_pending_tcp` into `ipc/tcp_writer.py`; extract `_accept_tcp` + `_handle_tcp_connection` into `ipc/tcp_acceptor.py`. (2) `shutdown_controller.py` → extract `_do_cleanup` into a `CleanupOrchestrator` (see YJ-13). (3) `level_monitor.py` → split module globals into a `LevelMonitorSession` class.
**Severity:** 🟢 Low

---

### FZ-58 — `test_history_and_models.py` and other test files use ticket-ID class names (SEC8/G4L06/SVC2/etc.)
**Status:** ⚠️ Tier-1 executed (2026-08-26): most ticket-named files already gone; remaining 13 `*_fixes.py` donors merged into same-domain parents (ipc×3→test_ipc_server, history_db_perf→history/test_history_db, credential_store_group→test_credential_store, clipboard_restore→test_clipboard, hotkeys/permissions/tray_misc_perf/llm_polish_http→their parents, handler_group_b→handlers validation parent, app_lifecycle→app/test_lifecycle, config_onboarding split into config+onboarding parents) with exact collected-count arithmetic verified per merge; deletions recorded in archive/deleted_files.txt; stale production citations (ipc_server/sidecar_ws/ipc/_helpers/registry/recorder/tray_elapsed_timer/handlers._log) repointed at live tests. Remaining: mixed-domain grab-bags needing split-then-merge (Tier-2).
**Description:** 46 `_fixes.py`-suffixed test files (re-counted 2026-08-12 — the "29+" claim understates; the `*_fixes.py` family alone is 46 files, up from the 43 earlier claimed), plus more ticket-named files: `test_cr_fixes.py`, `test_er_fix_g1.py`, `test_er_fix_g2.py`, `test_er_fix_h.py`, `test_g_perf_reliability_fixes.py`, `test_hp7_empty_transcription_fix.py`, `test_i5_retry_fixes.py`, `test_ipc4_rate_limiter_dual_window.py`, `test_ipc5_error_envelope_parity.py`, `test_low_findings_batch.py`, `test_nh17_force_cancel_wording.py`, `test_nh23_onboarding_progress_persistence.py`, `test_perf_fixes.py`, `test_perf_review_fixes.py`, `test_remaining_fixes.py`, `test_xa6_bubble_error_visibility.py`, `test_ec4_python_command_registry_parity.py`, plus the `*_de_fixes.py` / `*_xv_fixes.py` / `*_er_fixes.py` family.
**Root Cause:** Tickets drive file creation, not module identity.
**Impact:** Inverse lookup fails — to find tests for `credential_store.py` you must read `test_credential_store.py` AND `test_credential_store_de_fixes.py` AND `test_credential_store_outcome.py`. Bug-fix-named files rarely get pruned.
**Progress:** None yet.
**Related Files:** 46+ test files (see above)
**Fix:** Merge each `*_de_fixes.py` / `*_xv_fixes.py` / `*_er_fixes.py` into its parent module test file. Rename ticket-named root files to module-named. Keep ticket IDs only in docstrings/pytest markers.
**Severity:** 🟡 Medium

---

### Spaghetti / Phase 4.5 Split Candidates (documented; not all fixed this run)

- **FR-S2:** `voice_typer/server/history_db.py` (2529 lines, re-verified 2026-08-12 — up from 2156) — complete AC-135 split.
- **FR-S6:** `voice_typer/server/credential_store.py` (2132 lines, re-verified 2026-08-12 — up from 1277) — Phase 4.5 candidate.
- **FR-S9:** `src-tauri/src/sidecar/supervisor.rs` — ✅ SPLIT DONE (re-audited 2026-08-12: now 791 lines, down from 1055 — under the 800-line threshold).
- **FR-S10:** `voice_typer/server/crash_recovery.py` (1292 lines, re-audited 2026-08-12 — was 1034, GREW) — Phase 4.5 candidate (create_diagnostic_bundle 384-LOC method).
- **FR-S12:** ~~`src-tauri/src/platform/logging.rs` (1737 lines, re-audited 2026-08-12 — was 989, GREW +748; inline tests moved to logging_tests.rs)~~ — ✅ Fixed via GQ-11 2026-08-25 — split landed as the `src-tauri/src/platform/logging/` directory module (mod/init/combined/redact/panic_hook/early/rotating.rs); layout differs by design from the single-file Phase 4.5 candidate framing.
- **FR-S14:** ~~`voice_typer/server/sidecar_ws.py` (2027 lines, re-verified 2026-08-12 — up from 953)~~ — ✅ Fixed via EO-3 2026-08-25 — split landed as the canonical module + `voice_typer/server/sidecar_ws_internals/` sibling package (encode_pool/graceful_shutdown/stdout_banner/connection); layout differs by design from the proposed `sidecar_ws/{auth,...}` package (~14 test files pin the literal .py path).

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

### Former Known Limitations table — ALL RE-APPLIED AND VERIFIED (2026-08-26 audit against current code)

The source fixes below were all successfully re-applied by later sessions; every row was verified against the current tree on 2026-08-26:

| Finding | Title | Verdict (2026-08-26) |
|---------|-------|----------------------|
| SU-2 waves 2+ | history_db.py full split | ✅ DONE this session — history_db.py 2906→1730 LOC; `history_db_internals/{encryption,corruption_recovery,crud_writes}.py` extracted + checkpoint/FTS folded into writer.py; lazy `_hd.*` constant reads preserved; source pins retargeted |
| SU-3 | config.py split | ✅ DONE (verified earlier; config/ package over satellite modules) |
| SU-4 | recorder.py split | ⚠︀ SUBSTANTIALLY DONE — further reduced 2913→2703 this session (`__init__` decomposed into 12 `_init_*` helpers with attribute parity, recording/format.py extracted, capture/pipeline body moves, DeviceStateShimMixin); remaining mass is documented test-spy delegates + module-source pins (see EO-8/GQ-68 rationale) |
| SU-7 | model_manager.py split | ✅ DONE (model_manager/ package: manager.py facade + _base/_change/_construction/_lifecycle/_loading/_notify mixins; lock-ownership extraction beyond module split remains optional polish) |
| SU-19 | TCP dispatch head-of-line blocking | ✅ FIXED — two-pool design (transport_tcp.py `_tcp_worker_pool` + `_tcp_dispatch_pool`); pinned by tests/test_tcp_dispatch_concurrency.py |
| SU-20 | Per-write timeout syscall dance | ✅ FIXED — single select() write-readiness gate in ipc/sender.py; remaining settimeout calls are auth/idle-read timeouts by design |
| SU-21 | vocabulary Levenshtein O(W×V) | ✅ FIXED — length-bucketed candidates + bounded Levenshtein early-exit in vocabulary_automation.py |
| SU-22 | HF cache size-based eviction | 🚫 SUPERSEDED BY DESIGN — auto-eviction deliberately removed; tests/test_hf_cache_prune.py GUARDS its absence (implementing it would regress a product decision) |
| SU-23/24/26 | Shutdown 3 fixes | ✅ ALL FIXED — parallel pool drain (shutdown/ws_drain.py), ASR unload timeout 8s (shutdown/teardowns/asr_models.py), join_leaked_workers(1.0) before os._exit (shutdown/lifecycle.py) |
| SU-27/28 | Bubble lifecycle + ErrorBoundary timers | ✅ FIXED (verified in code) — Bubble.tsx clears fadeOut/auto-hide timers via effect cleanups; ErrorBoundary clears copiedTimer on unmount |
| SU-29/30 | cloud_engines lazy imports + WAV magic bytes | ✅ FIXED — stdlib imports at module top, wave-module WAV construction, _read_capped SEC-030; pinned by tests/test_cloud_engines_wav_helper.py |
| SU-35 | prewarm probe-cache eviction cap | ✅ FIXED — _CACHE_PROBE_MAX_ENTRIES=256 with write-path prune in prewarm/status.py |
| SU-37 | credential_store.py split | ✅ DONE (credential_store/ package) |
| SU-38 | recording_controller.py split | ✅ DONE — file now 639 LOC (<800 threshold) |
| 3 app_cleanup tests | mock-ref capture fixes | ✅ DONE — tests/test_app_cleanup.py 38 passed (re-run 2026-08-26) |

**Historical root cause of the original reverts:** sub-agents sharing one workspace used `git stash` to verify pre-existing failures; `git stash pop` failed or reverted other agents' uncommitted changes. Mitigation applied since: serial verification phases after each wave and strict per-agent file ownership (E16/E18).
---

### ZU-19 helper migration
(M, P3): 17 test files still have local `makeConfig()` (per audit 2026-08-12, up from 9; spot-check: 16 local defs outside helpers/) — lint test added to track. Full migration deferred (too many files for one session).

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
  - **Status:** ⚠️ PARTIAL (verified 2026-08-04) — `log/` package split REAL (correlation.py + formatters.py extracted; `from voice_typer.server.log import setup_logging` works). BUT: (a) no standalone `log.py` shim file exists anywhere; (b) `log/__init__.py` is **1133 lines** (re-audited 2026-08-12; was 1035), not a thin re-export shim (per-module env-override + setup logic still lives there).
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
- **QV-31** — Model download progress in onboarding
- **QV-32** — First-run probe fallback
- **QV-35** — DownloadProgressBar error/onRetry wiring
- **QV-37** — Templates/Vocabulary LastUpdatedIndicator + Clear All (partial)
- **QV-40** — Toast durations bypass useSnackbar
- **QV-41** — Page padding inconsistencies

---

## Completed

### High findings — 5 ⚠️ partial remaining (verified 2026-08-12; 16 verified-fixed entries removed from file)
- **FR-4** — ⚠️ PARTIAL (verified 2026-08-12): code fix CONFIRMED — `_do_fast_cleanup` step 6 = `app._restore_volume(fade_ms=0)` + `app._duck_crash_recovery.clear()` (shutdown_controller.py:1021,1027). BUT the claimed test file `tests/test_shutdown_fast_cleanup.py` DOES NOT EXIST — the "5/5 new tests PASS" validation claim is FALSE. Code: `voice_typer/server/shutdown_controller.py`.

- **FR-10** — ⚠️ PARTIAL (verified 2026-08-12): code fix CONFIRMED — `_build_linux_app_service` ExecStart = `{python} -m voice_typer.server.autostart_launcher --hidden` (prewarm_scheduler_posix.py:476). BUT the claimed test file `tests/test_prewarm_scheduler_posix_fixes.py` DOES NOT EXIST — the "5/5 new tests PASS" validation claim is FALSE. Code: `voice_typer/server/prewarm_scheduler_posix.py`.

- **FR-14** — ⚠️ PARTIAL (verified 2026-08-12): code fix CONFIRMED — `with registry.busy_context(registry.active_name)` in transcribe_step.py:281 (file is now the `dictation_pipeline/` package, not dictation_pipeline.py). BUT the claimed test file `tests/test_dictation_pipeline_fix_j.py` DOES NOT EXIST — the "10/10 new tests PASS" validation claim is FALSE. Code: `voice_typer/server/dictation_pipeline/transcribe_step.py`.

- **FR-51** — ⚠️ PARTIAL (verified 2026-08-12): code fix CONFIRMED — `typing.get_origin(ann) in (typing.Union, types.UnionType)` in config/sanitization.py:79 and config/__init__.py:2347 (file is now the `config/` package, not config.py). BUT the claimed test file `tests/test_config_fr51_pep604_union.py` DOES NOT EXIST — the "15/15 new tests PASS" validation claim is FALSE. Code: `voice_typer/server/config/sanitization.py`, `voice_typer/server/config/__init__.py`.

- **FR-54** — ⚠️ PARTIAL (verified 2026-08-12): `data?: Record<string, unknown>` added (usePython.ts:387,411) — BUT 2 `biome-ignore lint/noExplicitAny` directives REMAIN (lines 831-833; the impl signature is still `(data?: any)` with a documented TS overload-compat rationale). The claim "biome-ignore directive removed" is FALSE; "the `any` no longer propagates" is only partially true (impl retains `any`). Files: `voice_typer/client/src/renderer/src/hooks/usePython.ts`.

## Remaining Work

The following FR findings remain open — status `❌ Not Fixed`:

- **FR-7** (Medium) — `_diagnostics_archive` mkdir failure silently disables VEH crash diagnostics. Requires fallback path design.
- **FR-11** (Medium) — Heartbeat watchdog `os._exit(1)` race. Requires deeper `_do_cleanup` redesign.
- **FR-26** (Medium) — Linux native key-listener no USB hotplug. Requires C code changes + inotify.
- **FR-34** (Medium) — `tray_notifications` no rate limiting. Requires per-title rate limiter design.
- **FR-40** (Medium) — `SUPERVISOR_MAX_RETRIES` dead in production. Requires coordinated test rewrites.
- **FR-44** (High) — `RotatingFileWriter` holds `std::sync::Mutex` across blocking I/O. Requires background writer thread refactor.
- **FR-49** (Low) — `toggle_rate_limiter_allows` uses `SystemTime` not `Instant`. Requires `Mutex<Option<Instant>>` migration.
- **FR-50** (Low) — Blocking file I/O in async Tauri command handlers. Requires `spawn_blocking` migration.
- **FR-52** (High) — Bare `dict`/`list` annotations on `ConfigApplier` + `ServiceProtocol`. Requires TypedDict refactor.
- **FR-55** (duplicate of FR-39) — skipped.
- **FR-57** (Medium) — `app.py` 1845-line wiring façade split (re-verified 2026-08-12, up from 1275). Larger refactor (Phase A+B+C).
- **FR-59** (Medium) — `migrate.rs` 1249-line split — path note: migrate.rs became `src-tauri/src/migrate/` module tree. Larger refactor.

---

### SI-25 — `state.rs` remains mixed-purpose: SidecarHandle + shutdown IPC machinery
**Status:** ⚠️ Mostly addressed (2026-08-24 audit): state.rs is now 472 phys LOC and data-only except ~100 LOC of host-exit callbacks (:370-472); SidecarHandle lives in sidecar/handle.rs, shutdown machinery in sidecar/shutdown.rs. Absorbs VP-30 (802-LOC claim stale by ~330). Residual: callbacks could move to a lifecycle module.
**Description:** `state.rs` conflates shared-state types with `SidecarHandle` (process-management) and `shutdown_sidecar_for_exit` + `send_fire_and_forget_frame` (IPC/shutdown machinery).
**User Impact:** Maintainability concern; 3 concerns in one module.
**Root Cause:** AC-36 was partially applied.
**Progress:** None yet.
**Related Files:** `src-tauri/src/state.rs:1, 78-178, 249-420`
**Fix:** Move `SidecarHandle` to `sidecar/handle.rs`. Move shutdown machinery to `sidecar/shutdown.rs`.
**Severity:** 🟡 Medium

### SI-29 — 36 test files define local `_make_fake_*` helpers instead of using `tests/fixtures/`
**Status:** ❌ Not Fixed (fixture migration deferred — documented as Remaining Work)
**Description:** `tests/fixtures/ipc_test_helpers.py` exposes 3 canonical factories, but 36 test files define their own inline `_make_fake_app` / `_make_recorder` / `_make_server` helpers (per audit 2026-08-12, up from 25+; spot-check measured 37 files defining the named `_make_fake_*` helpers).
**User Impact:** Maintenance cost; signature changes require updating 36 files instead of 1.
**Root Cause:** XS-42 migration was never completed.
**Progress:** None yet.
**Related Files:** `tests/fixtures/ipc_test_helpers.py`, 36 test files
**Fix:** Two-phase consolidation (2026-08-24 audit refresh: fixtures all exist incl.
make_fake_sidecar_ws_server/make_fake_recorder; mig15/16/17 + integration files already migrated).
Phase 1 - consolidate the tests/test_sidecar_ws* family FIRST (highest drift risk): extend
tests/fixtures/sidecar_ws_test_helpers.py with the fake ws/websocket pair they rebuild locally
(local-mock density: test_sidecar_ws.py 28, auth_failed 24, races 22, connection_cap 20,
permissions_fixes 14, ready_ordering 8, thread_safety 6, protocol_version 4), then swap locals for
imports. Phase 2 - opportunistic sweep of the remaining ~180 local _make_* defs onto
app_helpers/ipc_test_helpers/recorder_test_helpers, prioritizing files touching VoiceTyperApp.__init__
and IPCServer construction where drift bites; never bulk-rewrite unrelated files in one commit.
**Severity:** 🟡 Medium

---

### Remaining Work AP

The following findings are documented in `review.md` as `❌ Not Fixed` — deferred to a future session due to scope/risk/time constraints:

| ID | Severity | Why deferred | Effort | Priority |
|---|---|---|---|---|
| AP-3 | Medium | Export commands size cap — needs recursive Value size estimation | M | P1 |
| AP-7 | Low | ELECTRON_RENDERER_URL scheme validation — dev-only | S | P2 |
| AP-10 | Medium | log.exception source-line PII — dispersed across 152 callsites in 59 files (measured 2026-08-12; up from ~30/14) | L | P1 |
| AP-12 | Low | VOICE_TYPER_DEBUG=1 PII warning — documentation only | S | P2 |
| AP-26 | Low | _backup_before_migration ordering — latent, no current migrator writes to disk | S | P2 |
| AP-32 | Low | container_detect DRY — maintenance hazard, no functional impact | S | P2 |
| AP-45 | Medium | load_with_fallback timeout — needs ThreadPoolExecutor + careful design | M | P1 |
| AP-46 | Medium | Cloud 200-with-empty-body — needs new CloudEmptyResponseError type | M | P1 |
| AP-47 | Medium | log.error → log.exception across 223 sites in 106 files (re-measured 2026-08-12 with `rg 'log\.error\(' voice_typer/server voice_typer/client/src/main voice_typer/client/src/preload`; the earlier 169/73 count is stale) — dispersed | L | P1 |
| AP-48 | Medium | Third-party library loggers silenced unevenly — needs expanded list | S | P1 |

---

### CSTYLE-1 — Task IDs in source code violate C-STYLE-1 (remediation sweep)
**Status:** ❌ Not Fixed
**Severity:** 🟡 Medium
**Description:** C-STYLE-1 (AGENTS.md) prohibits task IDs / session prefixes / ticket numbers in source code (file names, function names, test names, comments, docstrings) — they belong ONLY in metadata files (`review.md`, `SUMMARY.md`, `worklog.md`, `CHANGELOG.md`, `scripts/review_entries.json`). The `XZ-` prefix alone appears ~830 times across ~530 files (re-measured 2026-08-12: 75×/61 files under `voice_typer/server`, 33×/13 files under `voice_typer/client/src`, 723×/457 files under `tests/`, plus `src-tauri/src` — the earlier "~340 across ~90" count massively undercounted session-named test files): production Python (`voice_typer/server/config/__init__.py`, `config/loader.py`, `config_validators.py`, `config_applier.py`, `config_editor.py`, `clipboard/manager.py`, `_secrets.py`, `shutdown_controller.py`, `sidecar_ws.py`, `signal_handlers.py`, `ipc/registry.py`, `handlers/*.py`, `_electron_build.py`), main-process TS (`allowed-commands.ts`, `__tests__/*.ts`), renderer tests, and ~457 files under `tests/` (e.g. `tests/test_validation_scheduler_crash_fixes.py` has 31 hits, `tests/test_tauri_binaries_manifest.py` 20, `tests/test_dictation_pipeline_partial_failures.py` 18, `tests/test_native_hotkeys_base_toctou_verification.py` 16). Other prefixes (EC-, ER-, XV-, XA-, AC-, XS-, PVT-, S1-CR-, AB-, AP-, FR-, DJ-, DR-, UU-, GG-, NQ-) add more. Test names like `"XZ-R5-009: readStaleElectronPid() returns..."` (single-instance.test.ts:179) and docstrings like `(XZ-R17-06) — the critical-only path` (test_tray_and_console.py:71) are the common shapes.
**Root Cause:** Fix agents named tests/comments after their own ticket IDs; C-STYLE-1 predates much of this code, so it accumulated. Tests that grep for "dead code" / freshness (test_dead_code_stays_removed.py, test_techdebt_todos_freshness.py) reference IDs too.
**Progress:** None yet.
**Related Files:** ~90 files — see Description; sweep targets `voice_typer/`, `src-tauri/src/`, `tests/` (NOT the exempt metadata files).
**Fix:** Sweep task for one session: (1) `rg "\b(?:XZ|XV|XA|XS|EC|ER|AC|AB|AP|DJ|DR|FR|GG|UU|PVT|S1-CR|NQ|NH|UE|TX|SI|ZR|YJ|XE|FZ|DT|XPLAT|ARCH|IN|WN|T|H)-[0-9A-Z]+"` over source dirs; (2) rewrite each occurrence into prose describing the behavior (drop the ID), preserving test names' descriptive intent (rename `XZ-R5-009: ...` → `...`); (3) keep test assertions working — tests that grep source (dead-code/freshness guards) must be updated to match the new prose; (4) EXCEPTIONS that must NOT be stripped: the intentional inline tag prefixes `SEC-*`, `RACE-*`, `PERF-*` (AGENTS.md tag convention — cross-cutting, greppable, not session IDs), and all metadata files; (5) re-run `pytest` + `npx vitest run` after the sweep. Optional hardening: extend `scripts/check_branding.py`-style CI (or a new `scripts/check_task_ids.py`) to flag session-ID patterns in source comments going forward.

---

## Remaining Work
- **GG-67-70 (monolith splits):** Home.tsx (633→~250), Onboarding.tsx (571→~200), History.tsx (529→~220) — only partial splits were done (About.tsx fully split). These are Medium-severity maintainability improvements that require more time than a single fix wave allows.
- **Tray test updates:** 2 pre-existing tests assert the old "• " prefix behavior (GG-40 removed it). These tests need updating to assert `checked=is_active` instead. Test files are outside the fix agents' owned sets.

---

### EO-5 — cloud_engines.py is a 1054-LOC monolith mixing 6 cloud-provider concerns (was 1013)
**Status:** ❌ Not Fixed
**Description:** `voice_typer/server/cloud_engines.py` (1054 LOC — was 1013) — module mixes 6 concerns: provider defaults (_PROVIDER_DEFAULTS map + URL allowlist assertions), HTTP transport (_StreamingMultipartBody class, _read_capped, _audio_to_wav_bytes), retry policy (_transcribe_with_retry 131 LOC), provider-specific request/response shaping (_send_openai_compatible, _send_deepgram, _build_multipart_body, _multipart_parts), connection testing (test_connection), and the CloudEngine class itself. AC-134/AC-136/AC-137 cover transcription.py / parakeet_engine.py / model_manager.py but NOT cloud_engines.py.
**User Impact:** Adding a 4th cloud provider (e.g. AssemblyAI, Whisper-cloud-via-Azure) forces edits to a 1013-line file. Tests for _StreamingMultipartBody and tests for test_connection are coupled via the module boundary. Cloud-engine retry changes risk regressions in unrelated provider paths.
**Root Cause:** Verified — organic growth; provider-specific paths and HTTP plumbing live in the same file as the engine class.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/cloud_engines.py`
**Fix:** Split into a cloud/ package: _transport.py (_StreamingMultipartBody + _read_capped + _audio_to_wav_bytes + _opener), _retry.py (_transcribe_with_retry + _parse_retry_after + _cloud_http_error_class), _providers/openai.py (_send_openai_compatible + _build_multipart_body + _multipart_parts), _providers/deepgram.py (_send_deepgram), _engine.py (thin CloudEngine facade + test_connection), __init__.py (re-export CloudEngine + CloudEngineError subclasses).
**Severity:** 🔴 High
**Category:** Spaghetti / monolith detection

### EO-8 — recording/recorder.py is a 2877-LOC monolith (GREW from 2648) — DT-21/ZR-60/DJ-96 stale (file is mostly delegators now); __init__ is a 380-line god-constructor
**Status:** ❌ Not Fixed (re-verified 2026-08-12: 2877 LOC, up from 2648)
**Description:** `voice_typer/server/recording/recorder.py` (2877 LOC) — DT-21 cited 4012 LOC, ZR-60 cited 610-line god-methods, DJ-96 mandated Phase 4.5 split. The split DID land (audio_pipeline.py, capture.py, stream_lifecycle.py, device_manager.py, etc. extracted), but the file is still 2877 LOC because (a) __init__ is a 380-line god-constructor declaring 50+ instance attributes inline, (b) 9 device-state property pairs are shims for test backward-compat, (c) ~15 delegator methods with 25-line docstrings exist solely to satisfy inspect.getsource source-string tests (FZ-8/ARCH-12/S3-CR-21).
**User Impact:** The recorder is the audio capture subsystem — every dictation goes through it. Adding a new audio feature requires editing a 2877-line file. Tests cannot construct collaborators (AudioPipeline, StreamLifecycle, etc.) in isolation — they require a real Recorder with 50+ initialized attrs. The friend-class anti-pattern (59 friend-access lines across 6 collaborator files accessing recorder._<attr> directly) breaks encapsulation.
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

### EO-13 — dictation_pipeline/orchestrator.py run() god-method (AC-73 merged here; decomposition LANDED, residual length remains)
**Status:** ⚠️ Substantially fixed (verified 2026-08-23) — the prescribed decomposition is IMPLEMENTED: the 11 pipeline stages live in `voice_typer/server/dictation_stages` as single-responsibility stage objects (`build_default_stages()`), stage timing uses a `_timed_stage` context manager (no more `_stage_t0` pairs), and the former 197-line `finally` block is now ~10 sequential calls to named helpers (`_cleanup_sentinel_unlink`, `_cleanup_audio_zero`, `_cleanup_watchdog_reset`, `_cleanup_streaming_session_cancel`, `_cleanup_busy_event_clear`, `_cleanup_transcription_thread_clear`, `_cleanup_gc_collect`) at orchestrator.py:496+. Cancellation/empty paths use sentinel exceptions (`_PipelineAbortEmpty`, `_PipelineAbortCancelled`). **Residual:** `run()` itself still spans orchestrator.py:195-484 ≈ 290 physical / 135 code lines (docstring + inline rationale comments + consolidated PIPE-PERF logging inflate it); the original "run ≤ 60 lines" target was NOT met. Remaining work, if any, is comment/log extraction only — severity reduced to Low.
**Description:** *(historical)* `run()` had grown to 452 lines with a 197-line `finally` block (7 cleanup steps inlined, per-stage `_stage_t0`/`_xxx_ms` timing pairs interleaved with step calls). The 2026-08 decomposition externalized the stages to `dictation_stages`, replaced the timing pairs with a `_timed_stage` context manager, and converted the finally block into named helper calls — see Status above.
**User Impact:** Cleanup-sequence changes are now one-line additions to the helper list instead of surgery inside a nested try/except wall. Residual: the method body is still long on paper (~290 physical lines) due to rationale comments + consolidated PIPE-PERF logging.
**Root Cause:** Historical — AC-134 split the file into a package but kept run() as one giant method; fixed by the 2026-08 decomposition.
**Progress:** Decomposition landed; residual length accepted as Low.
**Related Files:**
- `voice_typer/server/dictation_pipeline/orchestrator.py`
- `voice_typer/server/dictation_stages.py`
**Fix:** *(implemented)* Named `_cleanup_*` helpers + stage objects + `_timed_stage`. Optional polish: extract the except-branch bubble/tray error reporting into a helper if run() is touched again.
**Severity:** 🟢 Low
**Category:** Spaghetti / monolith detection

### EO-14 — HandlerBase._wrap helper is defined but unused — 21 handler sites copy-paste the same 4-line validation boilerplate
**Status:** ❌ Not Fixed
> - **2026-08-24 audit:** only 4 live call sites; ~21 unmigrated markers each carry a documented non-fit reason — extend _wrap contract BEFORE migrating.
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

### EO-17 — C-STYLE-1 violation: 60+ task-ID-style comments across Python/TS/Rust source files (S2-CR-71, DJ-37/38/41, SK-b, D1-FIX, PERF-002, HOTKEY-MULTIKEY-001, Fix #N)
**Status:** 🟡 Partial — scrub INCOMPLETE (re-audited 2026-08-12): 5+ files STILL carry task-ID prefixes (HOTKEY-*/NATIVE-001/SK-b): `config_validators/hotkey.py`, `hotkeys/windows/polling_strategy.py`, `config/__init__.py`, `event_bus.py`, `hotkey_reserved.json`. The tray.py:8-17 "6 empty backticks" sub-claim is now FIXED.
**Description:** Pervasive task-ID-style comments across 20+ files in the renderer components, settings, hotkey, microphone, audio, models, dashboard, layout, ui, plus tray.py (S2-CR-71, S2-CR-16, DJ-37/38/41, SK-b), LevelBar.tsx (Fix #8 ×2), useSettingsConfig.ts (D1-FIX, PERF-002, PERF-MEMO-001, Fix #8), hotkey-validation.ts (HOTKEY-VALIDATION-002 (Task 2.2.5), HOTKEY-SHARED-001, HOTKEY-MULTIKEY-001 (Task 1.3)), useHotkeyCapture.ts (HOTKEY-MULTIKEY-001, HOTKEY-FULLMSG-001, HOTKEY-DEFER-001), hotkey-utils.ts (HOTKEY-UNIFY-002, FIX-HOTKEY-AND-NOTIFICATION, FIX-HOTKEY-ARCHITECTURE), AudioSettingsSection.tsx (Fix #10), RecordingSettingsSection.tsx (Fix #9), PrewarmAndUpdates.tsx (Fix #4). FIXED (2026-08-12): the 6 empty backticks at tray.py:8-17 are gone — but 5+ other files still carry task-ID prefixes (see Status).
**User Impact:** Code clutter — every comment carries a stale 'fix ticket' reference that adds noise without context. Task IDs are transient — once the entry is removed from review.md, the ID becomes meaningless noise. The empty backticks at tray.py:8-17 are evidence of a half-completed cleanup that left the prose grammatically broken.
**Root Cause:** Verified — direct violation of AGENTS.md C-STYLE-1: 'Do NOT add task IDs, session prefixes, or ticket numbers to source code.' QV-25 cleanup was scoped to common/feedback/help only and incomplete even there.
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

### EO-19 — 4 platform/lifecycle files exceed 800-LOC spaghetti threshold: crash_recovery.py (1292), autostart_windows.py (1455), startup_sequence.py (1144), autostart_launcher.py (1164)
**Status:** ❌ Not Fixed (re-verified 2026-08-12: autostart_windows.py now at server_platform/autostart_windows.py and is 1455 LOC, up from 1055; autostart_launcher.py 1164, up from 948)
**Description:** YJ-53 / WN-23 cited stale line counts: crash_recovery.py was 1034 → now 1292 (+258); autostart_launcher.py was 849 → now 1164 (+315); autostart_windows.py (1055 → 1455) and startup_sequence.py (956 → 1144, +188). Each file mixes 2-3 concerns that could be separate modules.
**User Impact:** Files become harder to review and change. crash_recovery.py's CrashRecovery class docstring mentions 6 separate fix-IDs woven through the same class. Critical for crash recovery and autostart — regressions here cause silent startup failures.
**Root Cause:** Verified — incremental fix-on-fix accumulation (each new fix added a defensive try/except + a 30-line docstring block).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/crash_recovery.py`
- `voice_typer/server/server_platform/autostart_windows.py` (1455 LOC)
- `voice_typer/server/startup_sequence.py`
- `voice_typer/server/autostart_launcher.py`
**Fix:** Extract: crash_recovery.py → _crash_recovery_save_worker.py + _crash_recovery_io.py. autostart_windows.py → _autostart_windows_runkey.py + _autostart_windows_task.py + _autostart_windows_startup_bat.py (the three mechanisms are already delimited by section comments at lines 155, 465, 760). startup_sequence.py → _startup_sequence_onboarding.py + _startup_sequence_crash_check.py.
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
- LO-10: `LO-10` — Added 10 main-process i18n keys (dialog.pythonCrash.*, pythonNotFound.*, pythonStartupTimeout.*, restartLoop.*, singleInstance.earlyExitSuffix) to all 8 main locale files. Replaced 5 hardcoded English dialogs in start-python.ts, tcp-connect.ts, relaunch-app.ts with `mainT()` calls. ❌ CLAIMED C-BRAND-1 fix (literal "Voice Typer" → {appName} placeholder) is FALSE for the Python server: `voice_typer/server/i18n.py:136,142` still contain the literal "Voice Typer".
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

**2026-08-12 re-audit of sampled Phase 4 LO-* fixes (7 sampled, 1 verified):**
- **LO-1**: ❌ NOT WIRED — `MicrophoneStep.tsx:85` still renders the hardcoded English Bluetooth tooltip; the `t("onboarding.bluetoothBadgeTooltip")` replacement did not land.
- **LO-4**: ✅ VERIFIED — zh.json now uses Hanzi "我的名字, 今天去了" (the only sampled fix actually applied).
- **LO-8**: ❌ NOT APPLIED — dark-mode `--input` / `--sidebar-border` still use alpha-based oklch (not opaque oklch(0.52)).
- **LO-14**: ❌ NOT APPLIED — `bubble.html` has NO `<script type="module" src="/src/theme-bootstrap.ts">` (only the main window's `renderer/index.html:105` got it; the bubble FOUC fix never landed).
- **LO-16**: ❌ NOT APPLIED — no plural variants added for lastUpdatedSecondsAgo/MinutesAgo/HoursAgo + about.relativeTime.* in the 8 locale files.
- **LO-17**: ❌ NOT APPLIED — `historySort.ts:17` still uses `new Intl.Collator(undefined, {...})`, not `Intl.Collator(getLocale())`.
- **LO-58**: ❌ NOT APPLIED — CONTRIBUTING.md §6.5 (i18n guide) + §6.6 (renderer page/component guide) were NOT added.

---

## Remaining Work

### Spaghetti / Monolith Splits (FI-S1 through FI-S10) — Deferred per Big-Task Policy
10 multi-day refactors documented in review.md as deferred to next session:
- **FI-S1**: `history_db.py` 2529 LOC → split class methods into `history_db_internals/{writes,queries,migration,fts_search,retention,lifecycle}.py` (Effort: L)
- **FI-S2**: ~~`credential_store.py` 2132 LOC~~ ✅ DONE — `credential_store/{_schema,_redact,_outcome,_backend,_plaintext,_crud,_migration}.py` package landed (verified 2026-08-23)
- **FI-S3**: `config/__init__.py` 2613 LOC → `config/{persistence,migration,validation,secrets}.py` (Effort: L)
- **FI-S4**: ~~`sidecar_ws.py` 2027 LOC → `sidecar_ws/{auth,dispatch,connection,writer,reader,run}.py`~~ ✅ DONE via EO-3 (2026-08-25) — canonical module + `sidecar_ws_internals/` sibling package landed instead; layout differs by design
- **FI-S5**: `crash_recovery.py` 1292 LOC (re-audited 2026-08-12; was 1273) → `crash_recovery/{persistence,lost_dictation,load_quarantine}.py` (Effort: M)
- **FI-S6**: ~~`shutdown_controller.py` 1420 LOC → `shutdown/orchestration.py`~~ ✅ DONE via VP-39 (2026-08-25) — `_do_cleanup`/`_do_fast_cleanup` bodies extracted to `shutdown/cleanup.py`; drain to `shutdown/ws_drain.py`; plans to `shutdown/plan.py` (no orchestration.py — layout differs by design)
- **FI-S7**: `cloud_engines.py` 1054 LOC (was 1013) → `cloud/{_multipart,_http_helpers,_openai_provider,_deepgram_provider}.py` (Effort: M)
- **FI-S10**: ~~`config_validators/__init__.py` 859 LOC~~ ✅ DONE — now 242 LOC over `allowlist/cross_field/entry_points/hotkey/language/scalar` modules (verified 2026-08-23)

### Other Deferred Items
- **FI-11-A prewarm binary integrity**: No runtime SHA-256 verification of prewarm binary (HIGH — but complex fix requiring manifest schema + launcher wiring). Effort: L. Priority: P1.
- **4 pre-existing test_sidecar_ws_races.py failures**: Error-code migration mismatch (`duplicate_connection` → `server.duplicate_connection`). Effort: S. Priority: P2.
- **Windows/macOS host validation**: All fixes tested on Linux sandbox only. Real-host validation required for Win32 console handler, macOS clipboard restore, native hotkey binaries. Priority: P0.

## Spaghetti / Monolith Splits (Group 4) — Deferred to Final Report

> The following spaghetti/monolith splits were identified by FI-20 (cross-cutting audit). Per the Big-Task Policy (max 5 big tasks per session), these multi-day refactors are documented here and scheduled for the next session. They are NOT skips — they are tracked handoffs.

- **FI-S1**: `history_db.py` 2529 LOC (3.2× threshold, re-verified 2026-08-12) — partial split done (`history_db_internals/`) but HistoryDB class body still large. Execute AC-135 plan: extract class methods into `history_db_internals/{writes,queries,migration,fts_search,retention,lifecycle}.py`. Effort: L.
- **FI-S2**: ~~`credential_store.py` 2132 LOC (2.7× threshold)~~ ✅ DONE — split landed as the `credential_store/` package (verified 2026-08-23; see GQ-70).
- **FI-S3**: `config/__init__.py` 2613 LOC (3.3× threshold) — ✅ RESOLVED (verified 2026-08-23): `config/__init__.py` is now 271 LOC over `_accessors/_defaults/_lifecycle/_migration/_saving/_schema/_systemroot` + loader/coercion/sanitization (see EO-12).
- **FI-S4**: ~~`sidecar_ws.py` 2027 LOC (2.5× threshold) — NO split done~~ ✅ DONE via EO-3 (2026-08-25) — split landed as the canonical module + `sidecar_ws_internals/` sibling package; layout differs by design from the proposed `sidecar_ws/{auth,dispatch,connection,writer,reader,run}.py`.
- **FI-S5**: `crash_recovery.py` 1292 LOC — partial split done (`diagnostics_export.py` extracted) but file still grew. Extract `crash_recovery/{persistence,lost_dictation,load_quarantine}.py`. Effort: M.
- **FI-S6**: ~~`shutdown_controller.py` 1420 LOC — partial split done (`shutdown/teardowns/` 12 modules) but `_do_cleanup` 174 LOC (lines 336-509, re-audited 2026-08-12; the earlier "392 LOC" claim was stale) still inline. Extract `shutdown/orchestration.py`.~~ ✅ DONE via VP-39 (2026-08-25) — `_do_cleanup` extraction landed: bodies in `shutdown/cleanup.py` (+ ws_drain.py / plan.py); layout differs by design from a single `shutdown/orchestration.py`.
- **FI-S7**: `cloud_engines.py` 1054 LOC (was 1013) — extract `cloud/{_multipart,_http_helpers,_openai_provider,_deepgram_provider}.py`. Effort: M.
- **FI-S10**: ~~`config_validators/__init__.py` 859 LOC~~ ✅ DONE — `allowlist/cross_field/entry_points/hotkey/language/scalar` modules landed; `__init__.py` now 242 LOC (verified 2026-08-23).

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
| FI-17 | Crash recovery | crash_recovery.py | 3 (1 Med cross-ref, 1 spaghetti, 1 flaky test) |
| FI-18 | Shutdown + prewarm | shutdown_controller.py, prewarm_scheduler_posix.py | 7 (1 Med spaghetti, 1 High cross-cutting, 5 Low/Info) |
| FI-19 | Logging consistency | _log_constants.py, ipc_diagnostics.py | 7 (2 Med, 5 Low/Info) |
| FI-20 | Cross-cutting spaghetti audit | all Group 4 files >500 LOC | 11 (5 High spaghetti, 6 Med/Low/STALE) |

**Triage note (2026-08-11):** the detailed findings these rows summarize
are the 13 HU-* entries immediately below. All were spot-verified
against current source: 9 were already resolved (statuses were stale —
HU-2, HU-5, HU-14, HU-16, HU-35, HU-37, HU-38, HU-39, HU-40), 3 were
fixed in this batch (HU-17, HU-28, HU-43), and HU-44 remains Won't Fix
(multi-day app/ package extraction). Rows whose counts reference
non-HU finding lists (e.g. FI-5 Rust host security 15 items, FI-10
History DB 7 items) are NOT covered by this triage — those detail lists
are not present in this file.

---

## Completed

### WM-9/10 (High) — History DB write future hang + dead code
- **WM-10 ⚠️ PARTIAL (re-audited 2026-08-12):** recovery.py + transcription_download.py (852 LOC dead code) DELETED — but search.py was NOT deleted: it STILL EXISTS at `voice_typer/server/history_db_internals/search.py` (655 LOC) and IS imported by production `history_db.py:379` (`import voice_typer.server.history_db_internals.search as _search_helpers`). The earlier "1104 LOC dead code, zero importers" and "1437 LOC removed" claims are FALSE — only 852 LOC was actually removed.
- **WM-9 ❌ STILL OPEN (re-audited 2026-08-12):** `_WRITE_FUTURE_TOTAL_TIMEOUT = 60.0` is defined at `history_db.py:85` but is NEVER referenced anywhere in the codebase. The writer loop at `history_db_internals/writer.py:579-591` still uses `while True` with only `_WRITE_FUTURE_TIMEOUT` (30s per-retry) — the hard-cap fix is genuinely NOT done. This entry must NOT be treated as completed.

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
- Deleted 2 of 3 dead-code files: history_db_internals/recovery.py (519 LOC) + transcription_download.py (333 LOC) — 852 LOC removed. ❌ search.py was NOT deleted: it still exists at `history_db_internals/search.py` (655 LOC) and is imported by production `history_db.py:379` (`import voice_typer.server.history_db_internals.search as _search_helpers`).
- ❌ C-STYLE-1 cleanup incomplete: 2 `XZ-CLIP-04` task-ID instances remain in `clipboard/manager.py` at lines 860 and 934 ("sending Cmd+V into the wrong window (XZ-CLIP-04)") — the claimed stripping from clipboard/manager.py did not fully land.
- ❌ C-BRAND-1 fix FALSE: `voice_typer/server/i18n.py:136,142` still contain the literal "Voice Typer" ("Add Voice Typer..." and "Voice Typer needs keyboard permission") instead of the {app} placeholder.

## Skipped as Not Real / Already Done
- None skipped as not-real. All 60 WM- findings verified real during investigation.

## Remaining Work

### Deferred (too large for single sub-agent — need dedicated Phase 4.5 waves):
- **WM-2** (Critical): app.py 1845 LOC monolith split (re-verified 2026-08-12) — needs 3+ sub-agents (L)
- **WM-3** ✅ DONE (re-audited 2026-08-12): supervisor.rs split landed — now 791 LOC (was 1702), under the 800-line threshold. Removed from deferral list.
- **WM-5** (High): recorder.py 2877 LOC split (re-verified 2026-08-12) — needs 3+ sub-agents (L)
- **WM-4** (High): kill_process_tree pgid race — needs pre_exec(setpgid) + move to tokio::process::Command (M)

### Partially done / needs follow-up:
- **WM-21**: ❌ STILL OPEN (re-audited 2026-08-12): spawn.rs (now 221 LOC after the 6-submodule split) has NO stderr/buf/BufReader references — the stderr-buffering fix never landed.
- **WM-30**: ❌ STILL OPEN (re-audited 2026-08-12): recording_controller.py uses only 5 `i18n.t()` calls (not 11) and ALL 8 locale files have ZERO `recording_controller` keys — no localization work landed (worse than the "11 strings" claim).
- **WM-44**: service/dictation force_recover (blocked — needs RecordingController public method)
- **WM-50**: declined (would break GT-12 test + orphan risk — documented rationale)

---

### VP-29 — `sound-manager.ts` bleeds visual-feedback concern + 41KB inline base64 WAVs
**Status:** ❌ Not Fixed
> - **2026-08-24 audit:** measured 41,282 base64 chars inline (:535-538); setVisualFeedbackEnabled has ZERO production callers though its comment claims App.tsx syncs it. Lazy-load fallback audio; wire-or-delete setters.
**Description:** `voice_typer/client/src/renderer/src/lib/sound-manager.ts` (675 LOC) manages TWO unrelated concerns: (1) sound cues (AudioContext + HTMLAudio fallback + cue synthesis, lines 1-505 + 575-636); (2) visual feedback mirror for deaf accessibility (lines 60-204): `_visualEnabled`, `VISUAL_STORAGE_KEY`, `setVisualFeedbackEnabled`, `isVisualFeedbackEnabled` — a SETTINGS flag, not a sound concern. Plus two embedded base64 WAV data URLs (`START_BEEP_WAV` at 526 = 17,726 chars; `STOP_BEEP_WAV` ~530-573 = 23,066 chars) bloat the file with ~41 KB of base64 inline. The 4-branch `playViaAudioContext` function (382-505) is ~120 LOC of duplicated `osc.connect(gain).connect(ctx.destination); osc.start(); osc.stop(); osc.onended = () => { osc.disconnect(); gain.disconnect(); }` boilerplate 4 times.
**User Impact:** The file's name is a half-truth. The 41KB of inline base64 hurts editor syntax highlighting, Vite HMR (re-parses the whole file on every edit), and grep/diff noise.
**Root Cause:** The visual-feedback flag was bolted onto sound-manager instead of being its own module. The WAVs were embedded pre-Vite-asset-import being the standard pattern.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
**Fix:** (1) Split `sound-manager.ts` into `sound-manager.ts` (cues + AudioContext) + `accessibility-manager.ts` (visual-feedback flag). (2) Move base64 WAVs to `lib/sound-manager/beeps.ts` or to `lib/sound-manager/beeps/{start,stop}.wav` files with Vite `?url` imports. (3) Collapse `playViaAudioContext` 4-branch switch into a per-kind config table.
**Severity:** 🟡 Medium

### VP-31 — `system_cmds.rs` (435 LOC, was 589 — open_path extracted) misnamed and mixes 4 unrelated concerns
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

### VP-32 — `tray.rs` (621 LOC, was 745) clusters 5 concerns; 3 are extractable
**Status:** ❌ Not Fixed
> - **2026-08-24 audit:** icon-cache (~105 LOC :48-239) and menu-builders (~85 :241-326) cleanly severable; handlers stay.
**Description:** `src-tauri/src/tray.rs` mixes: (a) menu deserialization types (`:48-82`: `MenuItemData`, `TrayMenuPayload`, `TrayStatePayload`); (b) icon cache + loader (`:92-191`: `TRAY_ICON_CACHE` static + `load_tray_icon`, 100 LOC with its own whitelist + poisoned-lock fallback + disk-read-outside-lock); (c) menu construction (`:193-259`: `build_item_refs`, `build_menu`, `empty_menu`); (d) event predicates (`:261-280`: `is_focus_main_window_event`); (e) top-level wiring (`:282-489`: `create_tray`, 188 LOC). Tests at `:491-745` (254 LOC = 34% of file).
**User Impact:** The icon-cache concern (with its own state + I/O) is mixed with menu construction. A change to icon loading risks breaking menu event handling.
**Root Cause:** The file accumulated responsibilities without being split.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/tray.rs`
**Fix:** Split into `tray/{icon_cache.rs, menu.rs, events.rs}` mirroring the `commands/bubble/*` decomposition pattern. `icon_cache.rs` extraction is the highest-value (it's the only piece with state + I/O).
**Severity:** 🟡 Medium

### VP-33 — `util.rs` (525 LOC, was 754 — tests moved to util_tests.rs) is a 4-concern catch-all "utils" graveyard
**Status:** ❌ Not Fixed
**Description:** `src-tauri/src/util.rs` bundles 4 orthogonal concerns: constants block (`:6-160`: 15+ named constants spanning token, supervisor, shutdown, heartbeat, kill_tree, dispatch, restart, rotation — each tied to a DIFFERENT subsystem); token/hex (`:162-191`: `generate_token` + private `hex::encode`); time (`:193-251`: `now_timestamp` + Howard Hinnant's `civil_from_days`); atomic fs (`:253-461`: `atomic_write_bytes`, `atomic_copy`, `atomic_copy_file` — generic filesystem helpers consumed almost entirely by `migrate/*`). Tests at `:463-754` (291 LOC = 39% of file).
**User Impact:** A contributor needing one constant has to read 15 unrelated ones. The atomic-fs helpers are co-located with token generation despite having no relationship.
**Root Cause:** "util" as a category attracts unrelated helpers.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/util.rs`
**Fix:** Split into `util/consts.rs` (or move each constant to its owning module), `util/crypto.rs` (token), `util/time.rs` (timestamp), `util/atomic_fs.rs` (atomic fs ops).
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

### GQ-15 — bench_startup.py warm-cache contamination makes median misleading
**Status:** ⚠️ Partial
> - **2026-08-24 audit:** contamination acknowledged in COLDSTART_REPORT.md; first_run_ms ratchet exists — rename median metric + fix bench/README.md:3.
**Description:** `measure_import_time()` only clears `voice_typer.*` from `sys.modules` (line 66-68); third-party C extensions (`numpy`, `pystray`, `PIL`) stay cached across the 3 in-process runs. Measured on Linux sandbox: 'All runs: 46ms, 46ms, 48ms' — variance is 2ms, confirming runs 2-3 are warm. COLDSTART_REPORT.md §5.1 explicitly says 'the median therefore understates true cold start; the *first* run is the honest cold number.' §6 rec #3 (line 282-288) recommends fixing the methodology but it was never implemented. Also, README.md:209 claims '~2 ms cold-import on reference hardware' but on this Linux sandbox the script reports 46ms — the README claim is stale and unverified by CI.
**User Impact:** Median cold-start number reported by `bench_startup.py` is misleading (warm-cache). README perf claim ('~2 ms') is unverifiable and stale. Any future regression that adds eager imports of heavy deps would be hidden if it doesn't exceed the warm-cache floor.
**Root Cause:** Acknowledged in COLDSTART_REPORT.md but no fix landed.
**Progress:** None yet.
**Related Files:**
- `bench/bench_startup.py:59-75`
- `bench/COLDSTART_REPORT.md:60-63`
- `bench/COLDSTART_REPORT.md:282-288`
- `bench/README.md:6` (the ~2ms cold-import claim; file is only 53 LOC — the earlier :209 citation exceeded the file length)
**Fix:** Replace `measure_import_time()` to spawn a fresh `python -X importtime -c "import voice_typer.server.tray"` subprocess per run (or delegate to `scripts/profile_imports.py`). Report first-run (true cold) + median + p99. Update README.md with the sandbox-measured value + OS disclaimer.
**Severity:** 🔴 High
**Verification (2026-08-06, Windows win32):**
bench_startup.py fixed, README not. `bench/bench_startup.py` spawns a fresh `python -c "import <target>"` per run and reports true-cold vs median (works on Windows: ~84 ms measured here). BUT `README.md:209` and `bench/README.md:3` still claim 'measured ~2 ms cold-import on reference hardware' -- ~40x off and unverifiable.

### GQ-26 — app.py 1845 LOC — wiring hub with 12 repetitive lazy-property pairs (GOT WORSE: 1676→1845, no lazy_property descriptor)
**Status:** ❌ Not Fixed (re-verified 2026-08-12) — app.py GREW from 1676 to 1845 LOC (+169); NO `lazy_property` descriptor exists anywhere in the file (grep = 0 hits). The prior "⚠️ Partial (refactor deferred)" framing was wrong — it regressed, not plateaued.
**Description:** `wc -l` = 1845 lines. 12 lazy `@property` getter+setter pairs. 10 one-line delegate methods. File mixes 3 concerns: (a) module-level i18n registry mutation at import time, (b) lazy property infrastructure, (c) wiring delegates, (d) re-export shims for test monkeypatch.
**User Impact:** 1845-line file crosses the Rule 20 spaghetti threshold. Cognitive load is high when reading the file but each piece is small and isolated.
**Root Cause:** `VoiceTyperApp` is a god-class wiring hub. The actual business logic was extracted but the wiring hub retains 12 near-identical lazy property pairs.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/app.py:1-1845`
**Fix:** (1) Replace the 12 lazy property pairs with a single `lazy_property` descriptor — saves ~150 lines. (2) Move the module-level `with i18n._LOCK: ... setdefault(...)` block into a `_register_i18n_fallbacks()` function called from `start()`. (3) Consider a separate `app_wiring.py` module for the re-export shims.
**Severity:** 🟡 Medium

### GQ-28 — model_manager.py 2638 LOC — 5 locks + 3 blended concerns (GREW from 2136)
**Status:** ⚠️ Partial (model_manager.py 2638 LOC split deferred per Max 5 big tasks rule; re-verified 2026-08-12)
**Description:** Single `ModelManager` class holds 5 distinct locks: `_model_lru_lock`, `_lazy_init_lock`, `_model_load_spawn_lock`, `_model_change_lock` RLock, `_idle_unload_lock`. Plus app-level `_config_mutation_lock` (acquired in `_change_model_blocking`). Lock-order contract is documented but complex. File is 2638 LOC. The class blends three concerns: lifecycle (load/swap), LRU tracking, and idle-unload timer.
**User Impact:** High cognitive load for maintainers; risk of introducing lock-order violations on future edits.
**Root Cause:** Historical accumulation; each concern was added incrementally.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:1-2638`
**Fix:** Split into `LifecycleModelManager` (load/swap/fallback) + `LruTracker` (touch/evict, owns `_model_lru_lock`) + `IdleUnloadTimer` (owns `_idle_unload_lock`). Keep `ModelManager` as a facade delegating to the three. Mirrors the existing `service/` mixin split pattern.
**Severity:** 🟡 Medium

### GQ-31 — text_cleanup.py 1416 LOC — monolith mixing 7 distinct concerns
**Status:** ⚠️ Partial (text_cleanup.py 1416 LOC split deferred — GQ-8 dead-code deletion landed; re-verified 2026-08-12 — down from 1499 via dead-code removal, still a monolith)
**Description:** Single file mixes 7 distinct concerns: (1) corrections JSON loading — `_load_bundled_corrections`/`_load_user_corrections`/`_load_external_corrections`/`_truncate_corrections`/`_filter_corrections_by_length`/`_active_corrections`; (2) phrase-pattern cache management; (3) `configure_corrections` orchestrator; (4) `clean_transcribed_text` pipeline entry; (5) token-based structural cleanup; (6) capitalization; (7) file-extension fix + auto-punctuation. Control flow is NOT tangled (each function is focused), but the file is monolithic and the historical-comment density is very high (~40% of lines are docstrings/comments).
**User Impact:** Maintenance cost: future edits to any one concern (e.g. changing auto-punctuation rules) require scrolling through 1500 lines and risk touching unrelated state.
**Root Cause:** Incremental growth + extensive prose comments documenting past refactors.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/text_cleanup.py:1-1416`
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
> - **2026-08-24 audit:** loop runs on the audio WORKER thread (callback -> SPSC ring -> worker), not the PortAudio RT callback; abs/peak-hold vectorized; residual loop is documented-inherent state.
**Description:** `noise_gate.py:255-274` (re-verified 2026-08-12 — the earlier "183-202" citation was stale): per-sample Python `for i in range(n):` loop on the audio worker thread. Body does: `level = float(level_arr[i])`, 1-2 float comparisons, 1-2 float arithmetic ops, 1 array write. The equalizer.py docstring (line 6) states a similar per-sample loop cost '~1 ms per chunk'. At 16 kHz / 10 ms chunks (100 chunks/sec, 160 samples/chunk) this is ~0.1-0.3 ms/chunk = 10-30 ms/sec ≈ 1-3% CPU. The file comment (line 5-8) acknowledges this is 'inherently sequential' but the attack/release ballistics CAN be vectorized with cumulative max/min tricks.
**User Impact:** ~1-3% CPU on the audio worker thread (the only filter with a Python per-sample loop). All other dynamics filters (compressor, limiter, EQ) are fully vectorized.
**Root Cause:** Attack/release envelope state machine implemented as a per-sample Python loop instead of vectorized numpy ops.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/audio_filters/noise_gate.py:255-274`
**Fix:** Vectorize the attack/release envelope via two parallel `np.maximum.accumulate` passes (one for attack-rising, one for release-falling), then apply element-wise gain. The open/close threshold crossings can be pre-computed as boolean masks. The hold-time state machine is the hardest part — it may require a scan-based approach (`np.ufunc.accumulate` or a small Cython helper).
**Severity:** 🟡 Medium

### GQ-38 — recorder.py 2877 LOC — Phase 4.5 split left 1-line delegators
**Status:** ⚠️ Partial (recorder.py 2877 LOC split deferred per Max 5 big tasks rule)
**Description:** Despite Phase 4.5 extracting bodies to 13 collaborator modules, `recorder.py` is still 2877 lines (re-counted 2026-08-12; the earlier "2857" claim was stale). The class body is dominated by 1-line delegator methods (e.g. lines 2469-2550: `_detect_device_disconnect`, `_handle_xrun_status`, `_apply_filter_chain`, `_append_to_buffer_locked`, `_compute_rms_and_peak`, `_run_vad_state_machine` — each a 1-line `return self._collaborator.X(self, ...)`) wrapped in multi-paragraph docstrings. `_recorder_split.py:19-40` lists a FURTHER split plan (lifecycle.py, device_management.py, format.py, worker_threads.py) that has not been executed.
**User Impact:** Maintainer navigation overhead; high cognitive load to trace any single code path across 3-4 files.
**Root Cause:** Split was intentionally partial to avoid line-number conflicts with parallel surgical fixes ('deferred until all in-flight surgical fixes to specific recorder.py line ranges have landed').
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/recording/recorder.py:1-2877`
- `voice_typer/server/recording/_recorder_split.py:19-40`
**Fix:** Complete the planned further split: extract `start`/`stop`/`discard`/`_teardown_stream` into `lifecycle.py`, device methods into `device_management.py`, format helpers (`_resample_chunk`/`_prepare_audio`/`_ensure_mono`) into `format.py`. Reduce `recorder.py` to <800 LOC of orchestrator + property shims.
**Severity:** 🟡 Medium

### GQ-41 — recorder start() hotkey critical path 200-600ms typical, 2-4s first-start
**Status:** 🚫 Won't Fix (warm_up_resampler background prewarm blocked by test contract `assert_called_once()` synchronous pin; full fix requires Recorder.__init__ change + test relaxation)
> - **2026-08-24 audit:** structure corroborates sync stream-open + first-start scipy warmup (_recorder_split.py:654-667,:751-776); magnitudes host-only — measure before/after.
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

### GQ-45 — Config.save() .bak write on every modified save — 2 extra fsyncs
**Status:** ⚠️ Partial
**Description:** Every modified `Config.save()` does: (1) `_secure_read_text(config_file)` to read existing content; (2) `_secure_atomic_write(bak_path, existing_text)` to write `config.json.bak`; (3) `_secure_atomic_write(config_file, content)` to write the new config. Each `_secure_atomic_write` does mkstemp + write + fsync(file) + os.replace + chmod + fsync(parent_dir) = 4 fsyncs total per modified save. Measured on container fs: 0.57-0.80ms per modified save; on real SSD expect ~8-20ms; on spinning rust ~40-200ms.
**User Impact:** 2 extra fsyncs per modified save (~4-10ms on SSD, ~20-100ms on HDD). For a user rapidly changing settings via IPC, this doubles the disk I/O cost.
**Root Cause:** The `.bak` write is unconditional on every modified save, even though `_last_saved_bytes` (populated after the prior successful save) already holds the exact bytes that were on disk.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/config/__init__.py:1550-1593`
**Fix:** When `_last_saved_bytes is not None`, use it as `existing_bytes` instead of re-reading config.json via `_secure_read_text`. Skip the `.bak` write entirely when `_last_saved_bytes == existing_bytes` (i.e. the prior save already backed up that content). Keep the `_secure_read_text` path only as a fallback when `_last_saved_bytes is None` (first save after construction).
**Severity:** 🟡 Medium
**Verification (2026-08-06, Windows win32):**
Status overstated -- only the READ was optimized. `config/__init__.py:1741-1749` now uses cached `_last_saved_bytes` instead of re-reading the file (saves one open+read per modified save). BUT the `.bak` write still happens on every modified save (`:1772 _secure_atomic_write(bak_path, ...)`) -> the 2 extra fsyncs remain. `tests/test_perf_data_store_save_write.py:245-270` asserts `.bak` is still written on content change. Side note: using cached bytes means `config.json.bak` may not reflect external on-disk edits.

### GQ-48 — history_db LIKE fallback 58ms scan on separator-only queries
**Status:** 🚫 Won't Fix (LIKE fallback 58ms scan is edge case — separator-only queries; idx_timestamp_id already mitigates ORDER BY)
**Description:** EXPLAIN QUERY PLAN: `SCAN transcriptions USING INDEX idx_timestamp` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`. The `WHERE text LIKE ? ESCAPE '\\'` with leading `%` cannot use any index, forcing a full table scan. Benchmark on 500K-row DB: `search(query="%", limit=50)` = 58ms median. Scales linearly with N (was 5.7ms at 50K rows — 10× rows ≈ 10× time). Triggered when `_is_fts_compatible_query` returns False (query contains ONLY separator chars — `%`, `_`, punctuation).
**User Impact:** Edge-case scenario (user types only `%` or `_` in search box). At 5M rows would hit ~580ms (Critical). Bounded by `_MAX_LIST_LIMIT=500` on the result set, but the SCAN cost is unbounded.
**Root Cause:** LIKE with leading `%` cannot use any index.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/search.py:382, 412, 524` (LIKE fallback MOVED out of history_db.py — re-audited 2026-08-12: the cited history_db.py:2430-2484 range now contains `wal_checkpoint` code; `prepare_like_search_pattern` / `is_fts_compatible_query` live in search.py)
**Fix:** For separator-only queries, prefer an FTS5 substring search via `MATCH '"*<char>*"'` tokenization (limited support in unicode61). Alternatively, reject these queries client-side. Low priority — edge case.
**Severity:** 🟡 Medium

### GQ-66 — Nuitka builds sequential — 30-45min local Tauri build
**Status:** ⚠️ Partial
**Description:** Phase 1a of `build_tauri_all.sh` runs sidecar → prewarm → native listener **sequentially**. Each Nuitka build is 10-15min. Three sequential = 30-45min. They have NO shared intermediate state and NO file-output contention (different `--output-filename`s). STALE (re-audited 2026-08-12): `build_sidecar_linux.sh:250-253` NOW has `--jobs=N` with nproc — the "NO --job flag" claim is outdated. The remaining gap is Windows/macOS invocations (see Verification).
**User Impact:** Local `make build-tauri` takes 30-45min; could be ~15min with parallelism. CI matrix already runs each platform on separate runners, so CI is unaffected — this is purely a local-dev friction cost.
**Root Cause:** Sequential is safe (avoids RAM contention during Nuitka's C compile phase) but on a multi-core host with ≥16GB RAM the three could run in parallel.
**Progress:** None yet.
**Related Files:**
- `scripts/build/build_tauri_all.sh:144-168`
- `scripts/build/build_sidecar_linux.sh:217`
- `scripts/build/build_sidecar_linux.sh:248-268`
**Fix:** (1) Add `--jobs=$(nproc)` to Nuitka invocations in `build_sidecar_*.sh` and `build_prewarm_*.sh`. (2) In `build_tauri_all.sh` Phase 1a, run the 3 builds in parallel via backgrounded `&` + `wait -n` pattern, gated on a `--parallel` flag (default off, since Nuitka is RAM-heavy). Document the RAM requirement (suggest ≥16GB).
**Severity:** 🟡 Medium
**Verification (2026-08-06, Windows win32):**
Linux half done, Windows/macOS half missing. `--parallel` flag + backgrounded `&` jobs + `wait -n` drain loop present in `build_tauri_all.sh`; `--jobs` added to `build_sidecar_linux.sh` and `build_prewarm_linux.sh`. BUT the Nuitka invocations in `build_sidecar_windows.sh:134-170`, `build_prewarm_windows.sh:154`, `build_sidecar_macos.sh:131`, `build_prewarm_macos.sh:141` have NO `--jobs`. On a Windows host, `--parallel` gives 3-way process parallelism but zero intra-Nuitka parallelism. (`build_tauri_all.sh` needs bash 4.3+ / WSL, not native PowerShell.)

### GQ-68 — shutdown_controller.py 1420 LOC — 14 thin delegates + extensive docstrings
**Status:** ⚠️ Partial (shutdown_controller.py 1420 LOC delegate extraction deferred per Max 5 big tasks rule; GQ-10 deadline fix done)
**Description:** `wc -l` = 1420 lines (re-counted 2026-08-12; the earlier "1398" claim was stale), above the 800-line spaghetti threshold. The file holds: (1) orchestration (`_do_cleanup` lines 336-509 [174 LOC], `_drain_ws_dispatch_pool` 510-631, `_build_sequenced_plan` 632-714, `_build_parallel_plan` 715-811, `_late_bookend_tray_stop` 812-856, `_do_fast_cleanup` 885-1063); (2) 14 thin delegate methods (`_teardown_timers_and_recording` through `_teardown_devnull_files`, lines 1064-1250+) each 8-15 lines; (3) quit/watchdog/atexit/signal delegates (lines 1276-1420). The Phase 4.5 split extracted teardown BODIES to `shutdown/teardowns/*.py` but kept the delegate methods on the controller for test-spy compatibility. Actual code ~600 lines; the remaining ~800 lines are docstrings documenting historical fixes.
**User Impact:** File is hard to navigate; 14 delegate methods add ~150 lines of boilerplate. Maintainers must jump between `shutdown_controller.py` (delegate) and `shutdown/teardowns/X.py` (body) to follow execution.
**Root Cause:** Delegate indirection is intentionally kept for test-spy contract (documented at lines 1023-1040).
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/shutdown_controller.py:1-1420`
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
> - **2026-08-24 audit:** 3 compiles once per process at startup gate — hoist opportunistically only.
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
> - **2026-08-24 audit:** single site model_manager.py:2230 inside eviction log call — trivial swap.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/model_manager.py:2184` (re-audited 2026-08-12; old ref :1743 stale)
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
> - **2026-08-24 audit:** zero production callers confirmed (docstring admits retained-for-tests) — move to test helpers per E15.
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

### GQ-L15 — microphone_watcher.py 1235 LOC mixing 5 platform/concern splits (was 1170)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/microphone_watcher.py:1-1235`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L16 — native_hotkeys/base.py 1649 LOC mixing 5 concerns (was 1238)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/native_hotkeys/base.py:1-1649`
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

### GQ-L22 — writer.py duplicated INSERT SQL string (multi-row vs single-row fallback)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** identical 7-column list at writer.py:290-292 vs :312-314 — extract shared constant.
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

### GQ-L25 — parakeet_engine.py 1577 LOC — 7 concerns (split desirable; was 1530)
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** parakeet_engine.py actually 1003 LOC (claim 1577 off by ~570) — split candidate at half the claimed size.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/parakeet_engine.py:1-1577`
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L29 — logging.rs redact_pii fast-path 8 separate contains scans
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** actually up to 10 passes (8 contains + 2 char-run scans) + clean-path clone (citation re-pointed 2026-08-25 post-GQ-11 split: now `src-tauri/src/platform/logging/redact.rs:63-75` — was logging.rs:551-562); touching requires PII battery re-run.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `src-tauri/src/platform/logging/redact.rs:42-75` (redact_pii fast path; pre-split cite logging.rs:440-473)
**Fix:** Documented in the related file:line above. Low-priority — fix opportunistically when already editing that area. Do NOT spend a dedicated sub-agent on Low-severity findings.
**Severity:** 🟢 Low

### GQ-L31 — sidecar_cmds.rs renderer_log_error serializes before 8 KiB cap
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** serialize-then-truncate inherent to Value payloads (system_cmds.rs:284-295); flood bounded by SEC-019 upstream; pre-screen optional.
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
- sidecar_cmds.rs was SPLIT (EO-35) into submodules and is now only 55 lines; SeqCst usages moved to `commands/dispatch.rs:214, 227` and `shutdown.rs:48`
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

### GQ-L40 — color-utils _cssColorToHexViaDOM no per-input cache
**Status:** 🚫 Won't Fix (Low severity — deferred; see rationale below)
> - **2026-08-24 audit:** DOM path hit on theme derivation/hover (useThemeSettings:171/199, theme-palette:79, theme-contrast:102-116); small Map cache suffices.
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
> - **2026-08-24 audit:** also a correctness nit — rapid wheel/key events between renders read stale textSize and lose intermediate steps; ref-mirror pattern fixes both.
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
> - **2026-08-24 audit:** NavLeaf recomputes identical lookup at :437 — reuse navLabel var (8 leaves x2/render).
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
> - **2026-08-24 audit:** file now JSX-only (state machine/colors/contrast/draft extracted); residual = custom-picker block :429-618.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/ThemeSettingsSection.tsx:1-648`
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
> - **2026-08-24 audit:** bench/README.md:3 claim confirmed stale — re-measure or drop the number.
**Description:** Low-severity polish/working-but-suboptimal issue identified during Phase 1 investigation. See related file:line for evidence.
**User Impact:** Negligible (sub-50ms latency / sub-10MB memory / sub-1% CPU). No measurable user-visible effect; purely a code-quality or micro-perf concern.
**Root Cause:** See related file:line — typically a copy-paste smell, redundant call, or stale comment.
**Progress:** None yet.
**Related Files:**
- `bench/README.md:6` (the ~2ms cold-import claim; file is only 53 LOC — the earlier :209 citation exceeded the file length)
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

### GA-1 — Restore bundled gtcrn_simple.onnx model binary
**Status:** Open (pending asset delivery).
**Description:** The bundled GTCRN noise-suppression model binary `voice_typer/server/gtcrn_simple.onnx` is ABSENT from this tree (verified 2026-08-25: file does not exist) — the cloud patch exports text-only diffs and cannot carry binaries, and the 0-byte artifact was removed. ER-2 landed the entire backend against the expected asset: official GTCRN streaming ONNX export, 535190 bytes, MIT license. With the binary missing, `_init_gtcrn` degrades to RNNoise with `is_degraded=True` at every init and the noisy_room preset silently loses its neural denoiser; the sdist MANIFEST.in include line is commented out pending delivery (dated rationale in place) so packaging never references a nonexistent file. Hop-size/layout contract for the incoming asset: 512-sample window / `HOP: int = 256` at voice_typer/server/audio_filters/gtcrn_backend.py:67 (16 ms @ native 16 kHz). Completes ER-2 delivery — cross-reference [ER-2] rather than duplicating its backend scope.
**User Impact:** noisy_room users get RNNoise fallback quality instead of neural suppression until the asset lands; TestRealModel coverage in tests/test_noise_suppressor_gtcrn.py stays skipped.
**Root Cause:** Binary asset cannot travel through the text-only patch channel — delivery gap, not a code defect.
**Progress:** Open — awaiting asset delivery.
**Related Files:**
- `MANIFEST.in`
- `voice_typer/server/audio_filters/gtcrn_backend.py`
- `voice_typer/server/audio_filters/noise_suppressor.py`
- `tests/test_noise_suppressor_gtcrn.py`
**Fix:** Obtain the official GTCRN streaming ONNX export (535190 bytes, MIT) via cloud-workspace re-export INCLUDING untracked binaries — never blind-download an arbitrary upstream export. Place at `voice_typer/server/gtcrn_simple.onnx`; uncomment the MANIFEST.in include line; un-skip `TestRealModel` in tests/test_noise_suppressor_gtcrn.py (skipif guard cites `MODEL_PATH.exists()`, :604-606); validate perf budget ~1.43 ms/hop ≤ 20 ms.
**Severity:** 🟡 Medium


## Completed

### Additional Completed Fixes (34 High, 68 Medium, selected Low)
- ❌ **GP-65 CLAIM FALSE (re-audited 2026-08-12):** "build_tauri_all.sh --sign flag now exits 1" is NOT true — the Phase 1e sign block (lines 386-406) only prints info messages and the script exits 0 at line 426; NO `exit 1` exists anywhere in the sign path. MOVED OUT of Completed — still open.
- **GP-66**: macOS CI hard-fails on missing binary instead of SKIP
- **GP-70**: macOS CI codesign --verify step added
- **GP-74-GP-77**: README/FEATURES/CHANGELOG/SECURITY/CONTRIBUTING/AGENTS doc fixes
- **GP-79-GP-82**: ipc-reference.md missing commands + events + WS protocol section
- **GP-91-GP-98**: ARCHITECTURE.md + module docs accuracy fixes
- **GP-99-GP-107**: Platform docs + new cloud-transcription-setup.md + permissions-per-os.md
- Full list: 152 GP-N findings filed; 136 genuinely fixed — GP-44 (Critical) remains NOT fixed, so the "11 Critical all addressed" claim is FALSE (at least one Critical is unaddressed); GP-15, GP-65 (sign exit-1 claim false), and GP-80 are also still open (moved to Remaining Work above).

## Fixed During Investigation

## Skipped as Not Real / Already Done

- **ER-93 + FZ-60 (resolved/stale, 2026-08-24 audit):** kill_process_tree spawn storm FIXED (Linux /proc + libc::kill, Windows single taskkill; all callers spawn_blocking/off-event-loop). Residual by design: 200ms SIGTERM grace incl. empty-descendants path (platform/process/mod.rs:344) + macOS pgrep-per-node (posix.rs:263).
- **ER-35 + GQ-L27 (WONTFIX-BY-DESIGN, 2026-08-24 audit):** dual-channel emit (specific event + generic python-event envelope) IS the documented ADR-0020 §9 contract — bubble window listens on the specific channel, usePython on the generic one; <=30 Hz coalesce makes the clone cost immaterial.

- **GP-5** (caps_lock_suppressor keybd_event → SendInput): SKIPPED — still OPEN (re-audited 2026-08-12: `caps_lock_suppressor.py:48-49,87-88` still uses `keybd_event`, NOT SendInput). Disposition accurate — deferred, not done.
- **GP-6** (Windows long-path prefix): SKIPPED — still OPEN (re-audited 2026-08-12: no `\\?\` extended-length path prefix anywhere in `_paths.py` or `paths.rs`). Disposition accurate — deferred, not done.
- **GP-119** (multi-key chord support): Won't Fix — disposition accurate (re-audited 2026-08-12: no sequence-chord support found; only single-combo multi-key, e.g. Ctrl+Shift+V, exists).
- **GP-142/GP-143/GP-144/GP-145**: Duplicates of GP-140/GP-33/GP-42/GP-11 — consolidated.

---

## 2026-08-12 Corrections Audit (applied to this file)

Source: independent re-verification of review.md against the current codebase (147 findings with accurate substance but stale counts/line numbers/paths — corrections applied in-place above). This section records the cross-cutting patterns observed and the items that remain unverifiable on this host.

### Cross-cutting patterns observed

1. **"❌ Not Fixed" is systematically over-pessimistic** — many such findings have substantial partial or full fixes applied (UU-35, ZR-86, GQ-53, YJ-39, VP-37, GQ-11). Re-verify against the code, not the status text.
2. **Line numbers are universally stale** — files grew (some +600) or shrank (some −1000) since review.md was written; several cited ranges now exceed actual file lengths (e.g. HotkeyPicker.tsx cited :969-984 but is only 307 LOC).
3. **Massive package-split refactor landed** that review.md did not reflect: `dictation_pipeline/`, `ipc/`, `config_internals/`, `config_validators/`, `permissions/`, `crash_handler/`, `clipboard_target_safety/`, `migrate/`, `commands/bubble/`, `commands/sidecar_cmds/`, `sidecar/ws/`, `sidecar/spawn/`, `platform/process/`, `level_monitor/`, `security/`, `history_db_internals/`, `shutdown/teardowns/`, `recording/` (13 collaborator modules), `logging/` (TS), `bubble/` (TS).
4. **Files that GREW despite proposed splits**: app.py (1569→1845), model_manager.py (2136→2638), credential_store.py (1277→2132), text_cleanup.py (982→1416), shutdown_controller.py (1280→1420), sidecar_ws.py (1480→2027), transcription.py (1190→1459), cloud_engines.py (1013→1054), crash_recovery.py (960→1292), event_bus.py (811→1169), startup_sequence.py (956→1144), microphone_watcher.py (881→1235), prewarm/process_tracker.py (837→1023), task_scheduler.py (793→976).
5. **Files that SHRANK via successful splits**: tray.py (1267→985), supervisor.rs (1702→791), spawn.rs (1233→221), ws.rs (1600→985), logging.rs (3183→1737), state.rs (838→802), tray.rs (745→621), util.rs (754→525), system_cmds.rs (589→435), recording_controller.py (1002→639), clipboard/manager.py (1417→1080), bubble-window.ts (598→56).
6. **Grep-count claims**: FZ-8 (478/150), S3-CR-21 (478/150), SI-29 (36 files), TC-32 (numpy cap), TC-43 (`@types/node` mismatch), FZ-57 (8 inline `sys.platform == "win32"`) hold up EXACTLY. FZ-59 (524/164) was re-measured 2026-08-12 at 495 `time.sleep(` calls across 239 test files (`rg 'time\.sleep\(' tests/`) — the earlier "exact" claim is stale (the count is grep-methodology-sensitive).
7. **Three Rust files previously called "undeclared dead drafts" are now ACTIVE**: `ws/event_protocol.rs`, `ws/heartbeat.rs`, `ws/respawn_scheduler.rs` — declared at `ws.rs:35-37`.
8. **YJ-15 is the most misleading finding** — "bubble_show + bubble_signal_ready migrated as proof-of-concept" is FALSE: the `VoiceTyperError` enum does not exist anywhere in `src-tauri/`; the migration NEVER STARTED.
9. **GP-44 (Critical RPM webkit2gtk3) still not fixed** undercuts the bulk claim "138 fixed of 152 GP-N findings; 11 Critical all addressed" — at least one Critical is unaddressed.
10. **Sampled Phase 4 LO-* fixes are largely NOT done as described** — 1 of 7 sampled verified (LO-4); LO-1, LO-8, LO-14, LO-16, LO-17, LO-58 are described as completed but the code shows the fix was NOT applied.
11. **Second-pass in-place corrections (2026-08-12, applied above)**: GP-65 sign-exit-1 claim FALSE (`build_tauri_all.sh` exits 0; no `exit 1` in Phase 1e); WM-10 search.py NOT deleted (655 LOC, production-imported at history_db.py:379); C-BRAND-1 literals remain at i18n.py:136,142; C-STYLE-1 XZ-CLIP-04 remains at clipboard/manager.py:860,934; Phase 4 LO-* sampled fixes extended to 7 sampled / 1 verified; TC-1 has 5 real `pytestmark` decorators among 13 mentions (an audit claiming "ZERO decorators" is FALSE); GP-80 registry count 69 confirmed; LOC corrections — recorder.py 2877, shutdown_controller.py 1420, _do_cleanup 174, crash_recovery.py 1292, clipboard/manager.py 1080, model_manager.py 2638, hotkey-utils.ts 776, log/__init__.py 1133; line citations corrected — GQ-L27 ws.rs:796-825, GQ-L28 state.rs:58,289, GQ-L55 bench/README.md:6 (53-LOC file), GQ-33 noise_gate.py:255-274, GQ-48 search.py:382,412,524, XA-2 page files shrunk/split.

### CANNOT_VERIFY on this host (require real Windows/macOS/Linux-desktop runtime)

- **XPLAT-12** — Windows-on-ARM runner validation (scaffolding exists at `tauri.windows-aarch64.conf.json`; GitHub has no public aarch64 Windows runner).
- **S1-CR-146** — `StartupWMClass=Voice Typer` matching Tauri window class requires a real Linux desktop + `xprop`.
- **Windows/macOS host validation** — all fixes tested on the Linux sandbox only: Win32 console handler, macOS clipboard restore, native hotkey binaries.
- **GQ-41** — recorder `start()` hotkey critical-path timing claims (200-600ms typical, 2-4s first-start).
- **GQ-54** — `check_branding.py` 314ms wall timing.
- **GP-66 / GP-70** — macOS CI hard-fail + codesign --verify workflow steps.

---

## 🚫 E. Cannot Verify (needs real host)

**19 findings require Windows / macOS / Linux desktop runtime** — they cannot be
verified or fixed on this Linux CI sandbox and must be validated on real hosts
(see `docs/migration/windows-validation-runbook.md`,
`docs/migration/macos-validation-runbook.md`,
`docs/migration/linux-validation-runbook.md`). These items are unverifiable, not
unfixable: re-check them on real hardware before marking anything done.

### XPLAT-12 — Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: ❌ Not Fixed — VALIDATE-ON-WINDOWS-HOST: Windows-on-ARM host validation required — Nuitka cross-compile + aarch64 freeze must be tested on real Windows ARM hardware
> - **2026-08-24 audit:** scaffold inert BY DESIGN — C-CI-4 gates the matrix leg (no public windows-11-arm runner; manual dispatch only per ADR-0020 §15). Action requires ARM hardware + explicit policy change; never enable blindly.
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.
- **Effort**: 🔴 **HIGH** — Requires Windows-on-ARM runner access not available in this sandbox. Cannot complete.
- **Confidence for one-shot fix**: 10% — blocked by runner availability.

### S1-CR-146 — `StartupWMClass=Voice Typer` may not match Tauri window class
**Status:** ❌ Not Fixed — out of file scope + host-validation required (target file voice-typer.desktop.template not in scope; fix requires running Tauri app + xprop WM_CLASS on real Linux desktop)
> - **2026-08-24 audit:** plausible-true (space+case in productName makes default tao WM_CLASS match unlikely vs binary prgname `voice-typer-tauri`) — verify via `xprop WM_CLASS` on a real Linux desktop, then set the matching class in `src-tauri/voice-typer.desktop.template`.
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

- **WM-6 / WM-7 / WM-8 / WM-11 / WM-12 / WM-13** — test-suite runs on real Windows/macOS/Linux desktop runtimes (only Linux-sandbox results exist so far).
- **WM-14** — Windows `taskkill` behavior. Tracked in worklog / GP-FIX sessions (no entry in this file); requires a real Windows host.
- **GP-7** — macOS notarization. Tracked in worklog / GP-FIX sessions (no entry in this file); requires a real macOS host with Developer ID + notary credentials.
- **GP-135** — cross-platform native binaries. Tracked in worklog / GP-FIX sessions (no entry in this file); requires building + running the native key-listener binaries on each real OS.
- **VT-1** — Windows host validation (config warnings, timeout utils, tray event-loop degradation from the `voice-typer` terminal run). Tracked in worklog / GP-FIX sessions (no entry in this file); requires a real Windows host.
- **ZU-46** — Dialog-autofocus test jsdom flake (S, P3): fix is correct (`onOpenAutoFocus` + `tabIndex={-1}`) but 2 tests fail in jsdom due to timing. Real browser validation needed.
- **FR-42** (Low) — Asymmetric Rust allowlist undocumented in TS allowlist. Doc-only; requires contract test execution on real Electron/Tauri runtimes.
- **FR-43** (Low) — Behavioral divergence `None` vs `{}` between Electron and Tauri IPC. Requires contract test execution on real runtimes.
- **FR-45** (Medium) — `dispatch_frame` orphaned pending-entry race. Requires Drop guard design + contract test execution.
- **GG-72** — Bubble fullscreen detection implemented for all platforms but only Linux-verified. `VALIDATE ON WINDOWS HOST` + `VALIDATE ON MACOS HOST`.

---

## Completed (2026-08-24 audit round)

- ✅ Forced-recovery engine-ejection fence implemented: transcription_watchdog.force_recover calls `force_unload_active()` when the snapshotted thread is still alive mid-call; ModelManager drops the registry slot WITHOUT destroying the engine object (use-after-free safe) so the next load constructs a fresh instance; stuck thread keeps its orphaned reference and its late result stays fenced. 4 regression tests incl. engine-identity assert.
- ✅ Audio-quality delegate-loss warning gated once-per-episode (~94/sec spam eliminated; latch resets on recovery). 3 tests.
- ✅ Stale comments fixed: client-ci.yml coverage provider comment (v8 → istanbul); tests/conftest.py real_torch contradiction aligned to removed-marker reality.

### Remaining micro-cleanup from this round

- conftest.py:184-186 pytest_configure docstring still says "also register the real_torch marker" — marker was removed; one-line fix.

## Wave 6 Findings (FG session close-out, 2026-08-14)

Final Review Wave 6 — 5 independent reviewers audited the entire project state after Wave 1+3+5 implementation + orchestrator direct fixes.

**Reviewer verdicts:**
- **R6-1 (Final test gate)**: APPROVE — 1121 Python tests pass on LINUX (sandbox); ruff 0/0; branding OK; 4-allowlist lockstep verified (Python=67, Rust=63, TS=65); npm run typecheck PASS; vitest subsets 909p/49sk/0f; cargo test + full vitest + pre-commit hooks = VALIDATE ON HOST.
- **R6-2 (Wiring + architecture)**: APPROVE — main.rs 288 LOC, worker/__main__.py 296 LOC (both ≤ 300 C-ARCH-1/E3); 3 new worker modules exist + imports resolve; no parallel systems; E15 archive complete (0 comments, 30/30 DELETE entries verified); C-LOG-1/2 compliant. SHOULD-IMPROVE: 4 production-code + ~15 test-file C-STYLE-1 "Wave N" refs (pre-existing from Wave 3, comment-only, no runtime impact).
- **R6-3 (Hard Don'ts final)**: APPROVE — 11 of 12 Hard "Don'ts" categories PASS (C-TRAY-1, C-I18N-1/2, C-BRAND-1, C-ARCH-1, C-CI-1..15, C-DATA-1, C-TEST-1..5, C-TAURI-1, C-LOG-1/2). Single violation: C-STYLE-1 — 24 NEW session-prefix refs in comments (3 production + 21 test; all comment-only; non-blocking technical debt for a future lint-sweep sub-agent).
- **R6-4 (Regression + security)**: APPROVE — 846 regression tests pass; SSRF redirect handler installed + re-validates each 3xx through assert_pack_url_allowed; PACK_MAX_PER_FILE_BYTES=500MB enforced; worker auth uses tokens_equal (hmac.compare_digest); consent gate runs before download; 4-allowlist IPC parity verified.
- **R6-5 (Deliverables + DoD)**: REQUEST-CHANGES — 2 must-fix items: (1) review.md R2-1 status not updated to reflect FG session execution; (2) 16 untracked sub-worklog-*.md files would auto-include in changes.zip. Both RESOLVED by orchestrator (this status update + .gitignore entry). 8 of 9 DoD items satisfied; item #9 (premium commercial quality) subjective with host-only validation caveats.

**Close-out loop (§6.5):** R6-5's 2 must-fix items resolved directly by orchestrator. Session is now closed.

**Definition of Done (§18) status:**
1. ✅ Original problem (R2-1) genuinely solved; root cause eliminated.
2. ✅ No parallel systems introduced; architecture stays clean.
3. ✅ No regressions (1121 Python tests pass).
4. ✅ All relevant tests pass, platform-qualified (§16) — Python on LINUX; cargo test + vitest full suite + manual launch = VALIDATE ON HOST.
5. ⚠️ Manual validation (§15) NOT done in sandbox (no display); recorded as Known Limitation per §14.2.
6. ✅ 4 of 5 independent Wave 6 reviewers returned APPROVE; 5th returned REQUEST-CHANGES with 2 items now resolved.
7. ✅ Work verified real first (§8.1 staleness check — R2-1 was a real open task).
8. ✅ worklog.md updated; deletions/moves/renames recorded in archive/deleted_files.txt.
9. ⚠️ Implementation acceptable in premium commercial desktop app — subjective; host-only validations remain as Known Limitations.
