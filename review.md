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
> - **UPDATED partials:** ARCH-9 (213 sites / 39 files remain), S1-CR-67 (only recording/_RecordingModule left; prewarm + server_platform hacks removed), EC-25 (3 Python catch-alls + relocated-but-unsplit TS catch-alls remain), XV-105 (role pooling LIVE — 3 roles → 1 subprocess; per-spec dedup deferred), XA-5 (8 of 24 sub-items verified fixed, listed inline), XZ-R11-04 (landed 2026-08-25, Session RV: AES-256-GCM at-rest encryption live — _text_crypto.py + DEK via credential_store; completed).

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.
>
> This file was filtered by verifier-agent on 2026-07-21 to REMOVE entries for fixes that were verified-done and KEEP only items that remain partial, broken, or not-implemented.

---

### Spaghetti / Phase 4.5 Split Candidates (documented; not all fixed this run)
- **FR-S10:** `voice_typer/server/crash_recovery.py` (1292 lines, re-audited 2026-08-12 — was 1034, GREW) — Phase 4.5 candidate (create_diagnostic_bundle 384-LOC method

## Completed

### High findings — 1 ⚠️ partial remaining (verified 2026-08-12; 19 verified-fixed entries removed from file)
- **FR-54** — ⚠️ PARTIAL (verified 2026-08-12): `data?: Record<string, unknown>` added (usePython.ts:387,411) — BUT 2 `biome-ignore lint/noExplicitAny` directives REMAIN (lines 831-833; the impl signature is still `(data?: any)` with a documented TS overload-compat rationale). The claim "biome-ignore directive removed" is FALSE; "the `any` no longer propagates" is only partially true (impl retains `any`). Files: `voice_typer/client/src/renderer/src/hooks/usePython.ts`.

## Remaining Work

The following FR findings remain open — status `❌ Not Fixed`:

- **FR-26** (Medium) — Linux native key-listener no USB hotplug. Requires C code changes + inotify.
- **FR-40** (Medium) — `SUPERVISOR_MAX_RETRIES` dead in production. Requires coordinated test rewrites.
- **FR-52** (High) — Bare `dict`/`list` annotations on `ConfigApplier` + `ServiceProtocol`. Requires TypedDict refactor.
- **FR-57** (Medium) — `app.py` 1845-line wiring façade split (re-verified 2026-08-12, up from 1275). Larger refactor (Phase A+B+C).

---

### SI-29 — 36 test files define local `_make_fake_*` helpers instead of using `tests/fixtures/`
**Status:** 🟡 Partial — Phase 1 complete (sidecar_ws fixture family consolidated onto tests/fixtures/sidecar_ws_test_helpers.py; local _make_fake_* files reduced 29→15). Phases 2+ remain (reconciled 2026-08-29).
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

### SX-1 — supervisor. Crash isolation: restart backend only, keep UI alive [Medium] — Pending
- **Files**: `voice_typer/client/src/main/index.ts`, `voice_typer/server/recording_controller.py`, `voice_typer/server/ipc_server.py`.
- **Description**: A backend (Python) crash restarts the whole app; a supervisor that respawns only the backend while UI/tray/hotkey stay alive does not exist in production.
- **Goal**: Add auto-recovery that restarts just the speech backend, with a "reconnecting…" state.
- **Options**: (1) Electron + Python: respawn only Python child in production. (2) Tauri + Sidecar: Rust supervisor re-spawns sidecar. (Not meaningful under embedded PyO3.)
- **Effort**: Medium.

### Remaining Work AP

The following findings are documented in `review.md` as `❌ Not Fixed` — deferred to a future session due to scope/risk/time constraints:

| ID | Severity | Why deferred | Effort | Priority |
|---|---|---|---|---|
| AP-10 | Medium | log.exception source-line PII — dispersed across 152 callsites in 59 files (measured 2026-08-12; up from ~30/14) | L | P1 |
| AP-12 | Low | VOICE_TYPER_DEBUG=1 PII warning — documentation only | S | P2 |
| AP-26 | Low | _backup_before_migration ordering — latent, no current migrator writes to disk | S | P2 |
| AP-32 | Low | container_detect DRY — maintenance hazard, no functional impact | S | P2 |
| AP-47 | Medium | log.error → log.exception across 223 sites in 106 files (re-measured 2026-08-12 with `rg 'log\.error\(' voice_typer/server voice_typer/client/src/main voice_typer/client/src/preload`; the earlier 169/73 count is stale) — dispersed | L | P1 |

---

### EO-8 — recording/recorder.py is a 2274-LOC monolith — (file is mostly delegators now); __init__ is a 380-line god-constructor
**Status:** 🟡 Partial — recorder.py 2877→2274; god-constructor decomposed into recorder_init helpers; 3 dead delegators removed; start() critical path trimmed. Remaining: property-shim deletion blocked by source-inspection pins in tests (reconciled 2026-08-29).
**Description:** `voice_typer/server/recording/recorder.py` (2274 LOC) — the file is still 2274 LOC because (a) __init__ is a 380-line god-constructor declaring 50+ instance attributes inline, (b) 9 device-state property pairs are shims for test backward-compat, (c) ~15 delegator methods with 25-line docstrings exist solely to satisfy inspect.getsource source-string tests (FZ-8/ARCH-12/S3-CR-21).
**User Impact:** The recorder is the audio capture subsystem — every dictation goes through it. Adding a new audio feature requires editing a 2274-line file. Tests cannot construct collaborators (AudioPipeline, StreamLifecycle, etc.) in isolation — they require a real Recorder with 50+ initialized attrs. The friend-class anti-pattern (59 friend-access lines across 6 collaborator files accessing recorder._<attr> directly) breaks encapsulation.
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

### EO-19 — 4 platform/lifecycle files exceed 800-LOC spaghetti threshold: crash_recovery.py (1292), autostart_windows.py (1455), startup_sequence.py (1144), autostart_launcher.py (1164)
**Status:** 🟡 Partial — 3 of 4 files resolved: autostart_launcher.py 1164→458 (+ autostart/ package), crash_recovery.py 1412 → crash_recovery/ package, startup_sequence.py 1474 → startup_sequence/ package. Remaining: autostart_windows.py (~1541) and startup_sequence phases live in ≤653-LOC modules (threshold met); crash_recovery clean. autostart_windows split remains (reconciled 2026-08-29).
**Description:** WN-23 cited stale line counts: crash_recovery.py was 1034 → now 1292 (+258); autostart_launcher.py was 849 → now 1164 (+315); autostart_windows.py (1055 → 1455) and startup_sequence.py (956 → 1144, +188). Each file mixes 2-3 concerns that could be separate modules.
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
- **High (9):** LO-12..LO-37, LO-38, LO-50 — SettingsSaveIndicator lies on failure, useConnection disconnect paths missing lastError, bubble theme FOUC, bubble locale-change wiring broken, pluralization missing, historySort wrong locale, HelpOverlay not in Settings, PunctuationCheatSheet not discoverable, bubble partial-transcript dead code, CONTRIBUTING.md lacks i18n section.
- **Medium (35):** LO-18..LO-36, LO-39..LO-49, LO-51..LO-66 — RTL bugs, a11y gaps (aria-busy, aria-disabled, RangeSlider aria-valuetext), Sonner locale reactivity, useSnackbar retry default, visual consistency (EmptyState, raw palette colors, RangeSlider labels), dialog unsaved-changes, Models page (languages/description/accuracy/disk-space/api-key), onboarding (consent/skip/mic-test), dictation (show-more/copy/discard/audio-level/error-state), error recovery (restart button, reconnect exhaustion, RecordingErrorCard affordances), Storybook (dark/RTL variants, button stories), test helpers (renderApp/mocks), CONTRIBUTING (page/component guide), docs/ux (6 new files), README (FAQ/screenshots/support), bubble (text-size/keyboard), theme (prefers-contrast, per-preset sidebar-border), sound feedback (volume/test).
- **Low (14):** LO-67..LO-80 — HotkeyPicker default aria, AudioSettings tooltip cross-link, Onboarding tips, visual polish (strokeWidth, margins, actionIcon), ariaLabel camelCase, tooltip DRY, focusRing, label htmlFor, debounce, Spinner decorative, LocalModelsPanel subtitle.

### Phase 4 — Fixes (20 parallel fix sub-agents + 2 retries)

**Critical findings fixed (LO-1..LO-11):**

- LO-2: `LO-2` — AudioSettingsSection.tsx: replaced literal English crossLinkBannerText + goToMicrophoneLabel with `t()` calls; added keys to all 8 locales.
- LO-3: `LO-3` — RecordingErrorCard.tsx: replaced literal English "Open Microphone settings" with `t("home.openMicSettings")`; added key to all 8 locales.
- LO-5: `LO-5` — useGlobalKeyboardShortcuts.ts: renamed 4 mismatched labelKey values to match existing locale keys (openSettings→settings, goHome→home, zoomIn→textSizeUp, zoomOut→textSizeDown). Added HelpOverlay-labelkey.test.tsx.
- LO-6: `LO-6` — Added `onboarding.hotkeyTestFailure` key to all 8 locales.
- LO-7: `LO-7` — Added 8 bubble i18n keys (blockedLabel, cancellingLabel, permissionRevokedLabel, pasteFailedLabel, 4 aria keys) to all 8 locales. Switched bubble `tf()` → `t()` for regression visibility.
- LO-8: `LO-8` — index.css: dark-mode `--input` and `--sidebar-border` changed from alpha-based (1.36:1–1.62:1) to opaque oklch(0.52) (3.1:1).
- LO-9: `LO-9` — index.css: light-mode `--success`/`--warning`/`--info` L lowered (2.21:1–2.86:1 → 3.4:1–4.5:1). Also bumped per-preset dark-mode status tokens.
- LO-10: `LO-10` — Added 10 main-process i18n keys (dialog.pythonCrash.*, pythonNotFound.*, pythonStartupTimeout.*, restartLoop.*, singleInstance.earlyExitSuffix) to all 8 main locale files. Replaced 5 hardcoded English dialogs in start-python.ts, tcp-connect.ts, relaunch-app.ts with `mainT()` calls. ❌ CLAIMED C-BRAND-1 fix (literal "Voice Typer" → {appName} placeholder) is FALSE for the Python server: `voice_typer/server/i18n.py:136,142` still contain the literal "Voice Typer".
- LO-11: `LO-11` — Fixed zh/ru/de audioEnhancement equalizer/limiter values (English → genuine translations).

**High findings fixed (LO-12..LO-37, LO-38, LO-50):**
- LO-12: SettingsSaveIndicator.tsx: added `error` prop + 5th destructive state; useSettingsConfig error wired through Settings.tsx.
- LO-13: useConnection.ts: 3 disconnect paths now call `setLastError(...)`.
- LO-15: Bubble locale-change wiring: added `onLocaleChanged` to bubble preload + bridge + useBubbleBridge + useThemeSync; removed `intentionallyUnused` whitelist.
- LO-37: TroubleshootingSettingsSection: added "Keyboard Shortcuts" button opening HelpOverlay.
- LO-38: DoneStep: added PunctuationCheatSheet link + `?` shortcut tip.
- LO-50: waveform_bubble_wiring.py: `_push_bubble_set_state` now accepts `transcript` kwarg; transcription.py calls it on partial results.

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

## Completed

### 10 (High) — History DB write future hang + dead code
- **WM-10 ⚠️ PARTIAL:** recovery.py + transcription_download.py (852 LOC dead code) DELETED. `history_db_internals/search.py` legitimately REMAINS: it is the live LIKE-fallback/FTS helper imported by production (the "dead code" claim was wrong); its separator-only-query behavior is contract-pinned (tests/test_history_db.py, tests/test_history_search_cjk.py).
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

## Remaining Work

### pending:
- **WM-2** (Critical): app.py 1845 LOC monolith split (re-verified 2026-08-12) — needs 3+ sub-agents (L)
- **WM-4** (High): kill_process_tree pgid race — needs pre_exec(setpgid) + move to tokio::process::Command (M)

### Partially done / needs follow-up:
- **WM-21**: ❌ STILL OPEN (re-audited 2026-08-12): spawn.rs (now 221 LOC after the 6-submodule split) has NO stderr/buf/BufReader references — the stderr-buffering fix never landed.
- **WM-30**: ❌ STILL OPEN (re-audited 2026-08-12): recording_controller.py uses only 5 `i18n.t()` calls (not 11) and ALL 8 locale files have ZERO `recording_controller` keys — no localization work landed (worse than the "11 strings" claim).
- **WM-44**: service/dictation force_recover (blocked — needs RecordingController public method)

---

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

### GQ-48 — history_db LIKE fallback 58 ms scan on separator-only queries
**Status:** 🚫 Won't Fix (LIKE fallback 58ms scan is edge case — separator-only queries; idx_timestamp_id already mitigates ORDER BY)
**Description:** EXPLAIN QUERY PLAN: `SCAN transcriptions USING INDEX idx_timestamp` + `USE TEMP B-TREE FOR LAST TERM OF ORDER BY`. The `WHERE text LIKE ? ESCAPE '\\'` with leading `%` cannot use any index, forcing a full table scan. Benchmark on 500K-row DB: `search(query="%", limit=50)` = 58ms median. Scales linearly with N (was 5.7ms at 50K rows — 10× rows ≈ 10× time). Triggered when `_is_fts_compatible_query` returns False (query contains ONLY separator chars — `%`, `_`, punctuation).
**User Impact:** Edge-case scenario (user types only `%` or `_` in search box). At 5M rows would hit ~580ms (Critical). Bounded by `_MAX_LIST_LIMIT=500` on the result set, but the SCAN cost is unbounded.
**Root Cause:** LIKE with leading `%` cannot use any index.
**Progress:** None yet.
**Related Files:**
- `voice_typer/server/history_db_internals/search.py:382, 412, 524` (LIKE fallback MOVED out of history_db.py — re-audited 2026-08-12: the cited history_db.py:2430-2484 range now contains `wal_checkpoint` code; `prepare_like_search_pattern` / `is_fts_compatible_query` live in search.py)
**Fix:** For separator-only queries, prefer an FTS5 substring search via `MATCH '"*<char>*"'` tokenization (limited support in unicode61). Alternatively, reject these queries client-side. Low priority — edge case.
**Severity:** 🟡 Medium

### GQ-66 — Nuitka builds sequential — 30-45 min local Tauri build
**Status:** ⚠️ Partial
**Description:** Phase 1a of `build_tauri_all.sh` runs sidecar → prewarm → native listener **sequentially**. Each Nuitka build is 10-15min. Three sequential = 30-45min. They have NO shared intermediate state and NO file-output contention (different `--output-filename`s). STALE (re-audited 2026-08-12): `build_sidecar_linux.sh:250-253` NOW has `--jobs=N` with nproc — the "NO --job flag" claim is outdated. The remaining gap is Windows/macOS invocations (see Verification).
**User Impact:** Local `make build-tauri` takes 30-45 min; could be ~15min with parallelism. CI matrix already runs each platform on separate runners, so CI is unaffected — this is purely a local-dev friction cost.
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
10. **Sampled Phase 4 LO-* fixes are largely NOT done as described** — 1 of 7 sampled verified (LO-4); LO-1, LO-8 are described as completed but the code shows the fix was NOT applied.
11. **Second-pass in-place corrections (2026-08-12, applied above)**: GP-65 sign-exit-1 claim FALSE (`build_tauri_all.sh` exits 0; no `exit 1` in Phase 1e); WM-10 search.py NOT deleted (655 LOC, production-imported at history_db.py:379); C-BRAND-1 literals remain at i18n.py:136,142; C-STYLE-1 XZ-CLIP-04 remains at clipboard/manager.py:860,934; Phase 4 LO-* sampled fixes extended to 7 sampled / 1 verified; TC-1 has 5 real `pytestmark` decorators among 13 mentions (an audit claiming "ZERO decorators" is FALSE); GP-80 registry count 69 confirmed; LOC corrections — recorder.py 2274, shutdown_controller.py 1420, _do_cleanup 174, crash_recovery.py 1292, clipboard/manager.py 1080, model_manager.py 2638, hotkey-utils.ts 776, log/__init__.py 1133; line citations corrected — GQ-L27 ws.rs:796-825, GQ-L28 state.rs:58,289, GQ-33 noise_gate.py:255-274, GQ-48 search.py:382,412,524, XA-2 page files shrunk/split.

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
