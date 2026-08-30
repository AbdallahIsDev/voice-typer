## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

---

> **Won't Fix tasks live in `WONT_FIX.md`** — deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

---

### T-1 — TAURI-E2E — Full-application validation mission (GOAL MODE: zero problems)

**Status:** ❌ Open — assigned to the next cloud-sandbox agent. **GOAL MODE: the goal is that there are NO problems at all.** Keep working until every check below passes, everything is clean, and the Tauri application is production-ready. Do not stop, defer, or skip anything. Any problem found — big, medium, or trivial — fix it IMMEDIATELY, then move to the next check.

> **TAURI ONLY — NOT ELECTRON.** The Electron shell is being removed in the future; Tauri becomes the main (and only) runtime. Every problem must be found and fixed **in the Tauri shell**. Problems that exist only in Electron are OUT OF SCOPE and must NOT be chased. When comparing behavior ("it works in Electron but not in Tauri"), use Electron only as a behavioral reference, then fix the TAURI side.
>
> **Environment reality:** this task runs in a cloud sandbox — no visual window, no desktop user session, but full terminal access + a controllable browser + vision (screenshot analysis). Where a human would click a switch with a mouse, the agent must TRIGGER the same action through the terminal, through code, through E2E tests, or through the browser. Every triggered action is verified either programmatically (config/state assertions, logs) or visually (screenshot + vision analysis). For every feature touched: if no test exists (E2E, unit, or golden), CREATE one and leave it in the test suite.

#### The mission

Run the **Tauri application** with the **full Python backend (sidecar) and everything else**, latest version, and test **literally everything in the application**, like a normal new user would — then like a power user. Use every feature available. Anything that doesn't look right, isn't clean, doesn't work, doesn't do what it's supposed to do (even without throwing an error), has unclean logs, fake/misleading messages, errors, warnings, or failing tests — **fix it immediately**.

The application also runs in a normal browser (the renderer is served on localhost). Launching it in the browser and using it there is part of this mission — **if the app does not work in the browser, that itself is a problem that must be fixed.**

**Known broken areas to start from (already documented — see TR-1, TR-2, TR-3 above):** tray "Models" sub-menu (dash item + "More Models" dead), Microphone page completely empty, tray menu missing the "Microphone" item. Fix these as part of this mission.

#### Checklist (exhaustive — and the list is NOT exhaustive: anything found beyond it is also in scope)

1. **Run the app** like a normal user: Tauri host + full Python sidecar, latest version, everything healthy (logs clean).
2. **Onboarding:** go through the ENTIRE onboarding as a brand-new user. Every step, every screen. Fix anything that breaks, hangs, misleads, or looks wrong.
3. **Models:** from onboarding or the Models page, download **`Whisper Tiny`** (~75 MB — small, so it downloads fast). Then use it: perform real transcription end-to-end and verify it works 100%.
4. **Recording & dictation:** full recording test — start, pause, Escape-cancel, stop; everything related to recording and everything that happens to the recording AFTER dictation (paste, cleanup, history write). Verify with the model that transcription of the recording works.
5. **Templates:** open the Templates page, add templates, USE them (insert via dictation flow), verify output correctness.
6. **Vocabulary:** add custom vocabulary, use it in dictation, verify replacements come out correctly.
7. **Database:** verify things are actually persisted (history, templates, vocabulary, settings) — survive restarts; fix any save/load problems.
8. **Clipboard:** test the clipboard/paste path end-to-end; fix problems.
9. **History page:** verify dictation/recording history is displayed correctly; fix the microphone issues there; test the filters.
10. **Microphone page + filters:** fix the empty page (TR-2); test every microphone quality/filter preset — Advanced, Noisy Room, Studio, Auto — all of them.
11. **Settings pages — test EVERYTHING on every settings page** (General, AI & Audio, Appearance, Privacy — every page, every control). Specifically named items (the list is not exhaustive):
    - **Launch at Login** (autostart): toggle on → verify it works; toggle off → verify.
    - **Fast Startup:** test it works.
    - **Notifications:** test once with notifications OFF, once ON — verify both states behave.
    - **Tray Click:** test both modes — click opens the app window vs. click starts dictation immediately.
    - **Bubble Behavior:** test the bubble end-to-end — shows, works, no problems.
    - **Bubble Position:** top center, bottom center, etc. — verify each position actually applies.
    - **Dictation hotkey:** verify it works; test hotkey VALIDATION — try changing the dictation key to Caps Lock and other keys; fix any validation problems.
    - **Recording mode:** test `tap to record` and related modes.
    - **Stop on silence:** test with MULTIPLE option values, not just one.
    - **Paste key, Escape cancel, auto-paste,** and every other recording-related keybinding: test all of them.
    - Every other switch/toggle/field on every settings page: on, off, verify state actually changes and persists.
12. **Analytics:** perform dictations and verify the Analytics page numbers actually move/update; test sharing status; fix what's broken.
13. **Search:** perform searches on EVERY page that has search; verify results and behavior.
14. **Punctuation cheat sheet:** open it, verify content/behavior.
15. **Export/Import:** export and import History, Templates, Vocabulary — verify round-trips are correct.
16. **Logs:** at the end, read the full session logs — unclean logs, fake/misleading messages, spurious warnings/errors: rewrite and fix them.
17. **Everything else in the application** not listed above: test it too.

#### Tools & method

- **Terminal:** trigger features by code/IPC/test-harness when no GUI is available; inspect state, config, and database directly; run the existing test suites.
- **Browser (Browser Use — see override below):** drive the renderer UI on localhost like a real user (navigate, click switches, fill forms), take screenshots, and use vision to VERIFY what changed (e.g. "the switch is really off", "the page really shows my mic").
- **Tests:** any feature or fix without a test gets one (E2E, unit, or golden — whatever fits). Tests stay in the repo.
- **Green gate:** per AGENTS.md C-TEST-6 — no claiming success without the full-suite green runs on the final code state.

#### BROWSER TOOL OVERRIDE — BROWSER USE ONLY. (NON-NEGOTIABLE)

> The sandbox's built-in browser is DEPRECATED for this task. Before any website or browser-driven action, install **Browser Use** (`browser-use/browser-use`, with the self-healing `browser-use/browser-harness` recovery layer) per §2.3, register its skill, **READ the registered skill documentation**, and route every browser-driven action of this task through it. The built-in browser is a last-resort fallback only after the §2.3 retry procedure fails — and if that happens, the run is logged as DEGRADED MODE in `worklog.md`, never silently substituted.
>
> **§2.3 Browser Use installation (mandatory, once per session):**
> 1. Run the official quickstart instruction, following it exactly: install or upgrade browser-use to the latest stable version with uv using Python 3.12, run `browser-use skill install` to register the skill, and connect it to the browser.
> 2. After registration, **READ the skill documentation that was installed** (its usage guide/skill files) before driving any browser task through it — know how to operate it, not merely that it exists.
> 3. If setup or connection fails: follow the recovery steps at `https://github.com/browser-use/browser-harness/blob/main/install.md` (the self-healing harness built for exactly this), then retry setup ONCE.
> 4. If it still fails after that retry: fall back to the sandbox's built-in browser for the session, log it in `worklog.md` under `## Degraded Mode` with the exact failure reason, and continue. Do not stall the run over tooling — but never claim nominal mode when running degraded.
> 5. Resource discipline while connected (sandbox has ~4GB RAM, no elevated privileges): headless mode always; close/release each browser context before starting the next check; never hold more concurrent contexts than strictly required; prefer sequential processing within each sub-agent's slice.
> 6. From the moment Browser Use is connected, EVERY navigation, form interaction, extraction, and behavioral task simulation in this task goes through it — not the sandbox's native browser primitives.

#### Definition of done (the GOAL)

- Every checklist item above: exercised, verified, and passing.
- Every problem encountered: FIXED immediately, with a test left behind.
- Logs: clean (no fake messages, no spurious warnings/errors).
- Full test suites: green on the final code state (C-TEST-6).
- The Tauri application behaves correctly for a normal user from onboarding through daily use — production-ready.
- Findings and fixes recorded in `worklog.md` / this file.

---

## Base Set (original review.md — pre-existing open findings)

> **2026-08-23 cleanup (verified against code before editing):**
> - **REMOVED as completed + verified:** EC-25's `test_perf_review_fixes.py` split is done but entry KEPT as partial; removed entries: ~~S3-CR-21~~ (duplicate of ARCH-12; its unique blocker test_app.py read_text pin is gone), ~~XA-2~~ (StatCard consolidation landed — DashboardStatCard deleted in favor of shared StatCard.tsx; pb-2 alignment fix; About wrapper standardized; labeled Spinner + EmptyState-retry patterns adopted), ~~XA-8~~ (all cited sub-items verified fixed: ErrorBoundary strings via t("errorBoundary.*"), KeyringStatusBadge compact-only aria, sonner containerAriaLabel/closeButtonAriaLabel localized, InfoTooltip `<title>` removed, Spinner decorative prop), ~~AC-66~~ (BusynessCoordinator `_busyness.py` + MicrophoneRegistry `_microphone_registry.py` own the state; back-compat properties on VoiceTyperApp delegate to them), ~~AC-73~~ (decomposition landed — merged into EO-13 with residual), ~~AC-128~~ (credential_store/ package landed — see GQ-70), ~~AC-131~~ (config/__init__.py now 271 LOC over 10 satellite modules — see EO-12).
> - **UPDATED partials:** ARCH-9 (213 sites / 39 files remain), S1-CR-67 (only recording/_RecordingModule left; prewarm + server_platform hacks removed), EC-25 (3 Python catch-alls + relocated-but-unsplit TS catch-alls remain), XV-105 (role pooling LIVE — 3 roles → 1 subprocess; per-spec dedup deferred), XA-5 (8 of 24 sub-items verified fixed, listed inline), XZ-R11-04 (landed 2026-08-25, Session RV: AES-256-GCM at-rest encryption live — _text_crypto.py + DEK via credential_store; completed).
> - **2026-08-30 reconciliation (verified against code before editing):** ~~GQ-L7~~ (x_up.fill removal landed — db17d364, resampler verified green; closed as fixed despite prior Won't-Fix), ~~GQ-L15~~ (microphone_watcher.py split into the microphone_watcher/ package — cf773c3e, 98 watcher tests green), ~~GQ-L16~~ (native_hotkeys base.py decomposed into _matching/_reader/_spawn/_watchdog mixins — 0b0d7e6f + 3edf78ec facade contract; mypy mixin-idiom growth absorbed by the 2026-08-30 baseline reconcile). Removed as fully completed + verified: ~~GQ-15~~ (bench harness + README claims), ~~GQ-66~~ (Nuitka --jobs in all four win/macos scripts), ~~4 pre-existing test_sidecar_ws_races.py failures~~ (7/7 pass since 1d202e12). Moved to WONT_FIX.md: GQ-32 (rationale verified standing), GP-119, GQ-L27/ER-35.

> **Platform warning:** The cloud agent's SUMMARY claimed "all tests pass on Linux." Results in this file tagged **Windows (win32)** are reproduced on this runner and contradict the Linux-only claims. Do NOT trust a Linux-only pass as proof of cross-platform cutover.
> This file was filtered by verifier-agent on 2026-07-21 to REMOVE entries for fixes that were verified-done and KEEP only items that remain partial, broken, or not-implemented.

---

### FR-54 — `usePython` bridge: `Record<string, unknown>` hardening landed, but 2 `noExplicitAny` escapes remain
**Status:** ⚠️ Partial — re-verified 2026-08-30. The public `PythonCall` signature is hardened (`data?: Record<string, unknown>` at `lib/python-bridge/usePython.ts:47`), but the event-handler implementation overload STILL retains `(data?: any)` under 2 `biome-ignore` directives. The file was split into the `lib/python-bridge/` package since the 2026-08-12 audit — the escapes now live at `lib/python-bridge/usePythonEvent.ts:107-110` (not the old usePython.ts:831-833).
**Description:** The 2026-08-12 claim "biome-ignore directive removed" was FALSE then and remains FALSE: the impl signature is still `handler: (data?: any) => ...` with a documented TS-overload-compat rationale. `usePython.ts` is now a 16-line re-export barrel; the real code moved to `lib/python-bridge/`. `data?: Record<string, unknown>` is used across the bridge (`usePython.ts:47,54`, `usePythonEvent.ts:92`, `event-dispatcher.ts:29,53`), but the `any` escape in the overload impl is unresolved.
**User Impact:** `any` still escapes the bridge's event-handler surface, so type-checking does not guarantee payload shapes for `usePythonEvent` consumers.
**Root Cause:** TS overload-compat — a single non-`any` overload cannot satisfy the event-dispatch call sites without widening; the impl keeps `any` under a deliberate exemption.
**Progress:** Partial — the public signatures are typed; the overload impl exemption remains.
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/python-bridge/usePythonEvent.ts` (biome-ignores at :107-110)
- `voice_typer/client/src/renderer/src/lib/python-bridge/usePython.ts` (`Record<string, unknown>` at :47)
- `voice_typer/client/src/renderer/src/hooks/usePython.ts` (barrel re-export, 16 LOC)
**Fix:** Eliminate the 2 `biome-ignore lint/noExplicitAny` directives by refactoring the overload impl to accept `Record<string, unknown>` (with an internal cast) instead of `any`, verifying `tsc -p tsconfig.web.json --noEmit` + the usePythonEvent tests stay green.
**Severity:** 🟡 Medium
**Category:** Type safety / a11y (bridge typing)

---

### FR-26 — Linux native key-listener has no USB hotplug support
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `voice_typer/server/native/linux-key-listener.c` contains no `inotify` / `udev` / `hotplug` handling.
**Description:** The Linux native key-listener enumerates input devices once at startup; plugging/unplugging a USB keyboard while the app runs is never detected, so hotplugged devices are not monitored.
**User Impact:** Users who hotplug keyboards miss dictation/hotkey events until restart.
**Root Cause:** Requires C code changes — `inotify` on `/dev/input` (or udev) + re-opening device handles on add/remove.
**Progress:** None.
**Related Files:** `voice_typer/server/native/linux-key-listener.c`
**Fix:** Add `inotify`/udev device-add/remove monitoring in the C listener and reopen the evdev set on hotplug; validate on a real Linux desktop.
**Severity:** 🟡 Medium
**Category:** Platform (Linux) / native binary

---

### FR-40 — `SUPERVISOR_MAX_RETRIES` dead-code / coordination debt
**Status:** ⚠️ Partial — re-verified 2026-08-30: the constant moved to Rust and is now ACTIVE. `pub(crate) const SUPERVISOR_MAX_RETRIES: u32 = 5` lives at `src-tauri/src/util.rs:58`; `supervisor.rs` iterates it; `util_tests.rs` pins it (`= 5`) and ties `SUPERVISOR_BACKOFF_MS.len()` to it. The Python-side dead code (the original finding) is gone — the residual is the Rust constant being duplicated as literals elsewhere.
**Description:** Originally filed as "SUPERVISOR_MAX_RETRIES dead in production" (Python side). The Rust supervisor now owns retry-counting; `supervisor.rs:394-397` documents that an `attempt >= SUPERVISOR_MAX_RETRIES` guard was previously dead code because `SUPERVISOR_BACKOFF_MS.len() == SUPERVISOR_MAX_RETRIES == 5`.
**User Impact:** Low — the retry cap is functional in Rust; the residual is drift risk if the constant and backoff array length ever diverge.
**Root Cause:** Cross-language migration left the semantics to be re-pinned in Rust; coordinated test rewrites were deferred.
**Progress:** Substantial — constant is now live in Rust with parity tests.
**Related Files:**
- `src-tauri/src/util.rs` (:58)
- `src-tauri/src/sidecar/supervisor.rs`
- `src-tauri/src/util_tests.rs`
**Fix:** Confirm no Python-side `SUPERVISOR_MAX_RETRIES` remnant; optionally replace the backoff-array-length coupling with an explicit assertion. Low urgency.
**Severity:** 🟡 Medium
**Category:** Lifecycle / concurrency

---

### FR-52 — Bare `dict`/`list` annotations on `ConfigApplier` + `ServiceProtocol`
**Status:** ⚠️ Partial — re-verified 2026-08-30: `ServiceProtocol` return types were narrowed to `dict[str, object]` / `list`, but bare `-> list` remains on `get_history`, `search_history`, `get_microphones`, `get_favorites`, `get_templates` (providers.py:417-429), and `ConfigApplier` still returns bare `-> dict` on `to_filter_dict`, `_apply_audio_preset`, `apply_config_side_effects`, `apply_config` (config_applier.py:211,262,291,884,970) with bare `dict` parameters throughout.
**Description:** The TypedDict refactor proposed in the original finding was only partially applied: the protocol return types were widened/narrowed but many bare `dict`/`list` annotations remain on the config-applier surface.
**User Impact:** Bare annotations weaken static checking at the config side-effect boundary; callers can't see the exact shape of `side_effect_status`.
**Root Cause:** TypedDict refactor was scoped out; the `dict[str, object]` widening on the protocol was done instead.
**Progress:** Partial — protocol return types typed; ConfigApplier + remaining `list` returns untyped.
**Related Files:**
- `voice_typer/server/providers.py` (:417-429 bare `list`)
- `voice_typer/server/config_applier.py` (:211,262,291,884,970 bare `dict`)
**Fix:** Define TypedDicts for the `side_effect_status` / handler payloads and replace the bare `dict`/`list` annotations; keep `ServiceProtocol` in sync.
**Severity:** 🟡 Medium (originally High-rated)
**Category:** Type safety

---

### FR-57 — `app.py` wiring façade split (WM-2 merged here; duplicate WM-2 entry deleted)
**Status:** ❌ Pending — re-synced 2026-08-30 audit: measured **1158 LOC** (not 1845). WM-2 (Critical-rated duplicate of this task) merged into this entry on 2026-08-30; its line entry deleted.
**Description:** `voice_typer/server/app.py` is the VoiceTyperApp wiring façade. FR-57 claimed 1845 LOC ("re-verified 2026-08-12, up from 1275"); WM-2 claimed the same 1845 LOC at Critical severity. Both counts are stale — measured **1158 LOC on 2026-08-30**. The file still exceeds the E3 ~300-line wiring-only budget (~3.9×), so the residual refactor is real but smaller than originally framed; the Phase A+B+C plan predates the extraction work that already landed and must be re-derived from the current file.
**User Impact:** Wiring any new subsystem means editing an oversized façade; collaborators cannot be constructed in isolation; every change to central wiring carries elevated regression risk.
**Root Cause:** Incremental fix-on-fix accumulation on the central wiring object; prior extraction rounds reduced 1845 → 1158 without finishing the split.
**Progress:** Partial — substantial extraction landed since the 2026-08-12 measurement (1845 → 1158 measured 2026-08-30).
**Related Files:**
- `voice_typer/server/app.py` (1158 LOC, measured 2026-08-30)
**Fix:** Re-audit app.py's current concern clusters first, then extract the 2–3 largest cohesive blocks into `app/` submodules create-first (new modules complete and verified before trimming the façade; keep re-exports so old public names still resolve). Keep `VoiceTyperApp` as the façade. Large refactor — needs 3+ sub-agents (per WM-2) and runs as ONE big task at a time; verify with `pytest --collect-only` + focused tests after each extraction.
**Severity:** 🟡 Medium (WM-2's 🔴 Critical rating applied to the former 1845-LOC state; re-rated at measured 1158 LOC — re-rate on re-scope)
**Category:** Spaghetti / monolith detection

---

### SI-29 — 36 test files define local `_make_fake_*` helpers instead of using `tests/fixtures/`
**Status:** 🟡 Partial — Phase 1 complete (sidecar_ws fixture family consolidated onto tests/fixtures/sidecar_ws_test_helpers.py; local _make_fake_* files reduced 29→15). Phase 2 complete (reconciled 2026-08-30): the 2 VoiceTyperApp-duplicating test files now use tests/fixtures/app_helpers.make_voice_typer_app (+ACL no-op added to the canonical helper), the 2 real-Recorder files use recorder_test_helpers.make_recorder, and 9 IPCServer test files use make_bare_ipc_server/make_ipc_server_with_fakes (+make_buffered_mock_tcp_client). Remaining: 13 domain-specific _make_fake_* helpers + 9 thin named adapters intentionally stay local (out-of-scope per the entry's own "never bulk-rewrite unrelated files" guidance).
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

### SX-1 — supervisor. Crash isolation: restart backend only, keep UI alive [Medium] — 🟡 Partial
- **Files**: `voice_typer/client/src/main/index.ts`, `voice_typer/server/recording_controller.py`, `voice_typer/server/ipc_server.py`.
- **Description**: A backend (Python) crash restarts the whole app; a supervisor that respawns only the backend while UI/tray/hotkey stay alive does not exist in production.
- **Goal**: Add auto-recovery that restarts just the speech backend, with a "reconnecting…" state.
- **Options**: (1) Electron + Python: respawn only Python child in production. (2) Tauri + Sidecar: Rust supervisor re-spawns sidecar. (Not meaningful under embedded PyO3.)
- **Effort**: Medium.
- **Status (reconciled 2026-08-30):** Option (2) Tauri is ALREADY SATISFIED — src-tauri/src/sidecar/supervisor.rs respawns ONLY the sidecar (5-attempt backoff, WS generation staleness re-checks), the renderer shows the existing "restarting" state and auto-recovers on supervisor_reconnected/state_changed, escalating to full relaunch only after backoff exhaustion with the restart_counter.json 3-attempt/10-min circuit breaker. Residual (Electron option 1): the production crash branch in start-python.ts still shows a crash dialog + app.quit(); a production-ready restartBackend() respawn primitive, the restart_history.json breaker, and the renderer "reconnecting" handler all exist and just need an auto-respawn watchdog wiring them together.

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
**Status:** 🟡 Partial — recorder.py 2877→2274→1759; god-constructor decomposed into recorder_init helpers; start() critical path trimmed. Shims + delegators DELETED (reconciled 2026-08-30): DeviceStateShimMixin (8 device-state property pairs) and VadShimMixin (18+1 VAD properties) removed; 34 pure delegator methods removed; production collaborators and tests now route through the owning collaborators (recorder._devices._X / recorder._vad._X / _session_state / _stream_lifecycle / _capture). Recorder.stop/snapshot kept as documented 1-line public-API delegators. Remaining: the ≤500-LOC target requires the state-ownership inversion (locks/buffers/worker handles moving into SessionState/StreamLifecycle/capture) — deferred-scale, needs a dedicated session with full-suite green gates before and after; module-source pins (tests/test_recorder_secure_clear_array.py) and the RT-literal pin (tests/test_recording_and_audio.py) constrain the shape.
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
**Status:** 🟡 Partial — 4 of 4 files resolved: autostart_launcher.py 1164→458 (+ autostart/ package), crash_recovery.py 1412 → crash_recovery/ package, startup_sequence.py 1474 → startup_sequence/ package, autostart_windows.py 1541 → 877-LOC facade + _autostart_windows_{task,sweep,uninstall,startup_bat}.py submodules (85/424/179/169; landed 2026-08-30 — C-CROSS-1/2/4 and the C-ARCH-2 dotted-patch surface preserved via lazy sibling-module-object reads; drift-pin paths follow the moved literals). startup_sequence phases live in ≤653-LOC modules (threshold met); crash_recovery clean.
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

### FI-S1 — `history_db.py` 2529-LOC monolith: partial split done, HistoryDB class body still large
**Status:** ⚠️ Partial — re-verified 2026-08-30: `history_db.py` is 1730 LOC (down from 2529), with 64 methods and 113 references into `history_db_internals/`. The `history_db_internals/` package has 8 modules (corruption_recovery, crud_writes, encryption, reader, retention, schema, search, writer). The HistoryDB class body is still large — the original propose of extracting class methods into `{writes,queries,migration,fts_search,retention,lifecycle}.py` was partially done (layout differs: crud_writes instead of writes, no queries or migration modules).
**Description:** The original monolith split was proposed as extracting class methods from HistoryDB into dedicated internals modules. The split landed partially — `history_db_internals/` has 8 modules, but the HistoryDB class in `history_db.py` is still 1730 LOC with 64 methods.
**User Impact:** Editing any history feature requires touching the 1730-LOC HistoryDB class; collaborators cannot be constructed in isolation.
**Root Cause:** Incremental fix-on-fix accumulation; the class-method extraction was done only partially.
**Progress:** Partial — 8 internals modules extracted, but the HistoryDB class body is still ~3× the E3 wiring limit.
**Related Files:**
- `voice_typer/server/history_db.py` (1730 LOC)
- `voice_typer/server/history_db_internals/` (8 modules)
**Fix:** Execute AC-135 plan: extract remaining class methods into `history_db_internals/{writes,queries,migration,fts_search,retention,lifecycle}.py` (or align with the existing layout). Target: HistoryDB class ≤ 500 LOC.
**Severity:** 🟡 Medium
**Category:** Spaghetti / monolith detection

---

### FI-S5 — `crash_recovery.py` 1292-LOC monolith: split to package — DONE
**Status:** ✅ DONE — re-verified 2026-08-30: `crash_recovery.py` no longer exists as a monolith. The `crash_recovery/` package has 4 modules: `_io.py` (389 LOC), `_store.py` (641 LOC), `_worker.py` (484 LOC), `__init__.py` (93 LOC). The proposed filenames `{persistence,lost_dictation,load_quarantine}.py` differ from the actual `{_io,_store,_worker}` but the monolith is fully split. The review.md's earlier "partial split done (diagnostics_export.py extracted) but file still grew" is stale — the package split completed.
**Description:** The original finding proposed extracting `crash_recovery/{persistence,lost_dictation,load_quarantine}.py`. The actual split landed as `crash_recovery/{_io,_store,_worker}.py` — layout differs by design but the monolith is gone.
**User Impact:** None — crash recovery is now split.
**Root Cause:** The split was completed as part of EO-19; the review.md entries were not updated.
**Progress:** Done.
**Related Files:** `voice_typer/server/crash_recovery/` (4 modules, 1607 LOC total)
**Fix:** Already applied.
**Severity:** 🟢 Low (already done)
**Category:** Spaghetti / monolith detection

---

### FI-S7 — `cloud_engines.py` 1054-LOC monolith: partial split, file still large
**Status:** ⚠️ Partial — re-verified 2026-08-30: `cloud_engines.py` is 843 LOC (down from 1054). The `cloud/` package exists with `_defaults.py`, `_retry.py`, `_transport.py`, `_providers/` (openai.py, deepgram.py). The proposed `{_multipart,_http_helpers,_openai_provider,_deepgram_provider}.py` layout differs — `_transport` covers HTTP helpers, `_providers/openai` has the multipart logic, but no `_multipart`/`_http_helpers` top-level modules. `cloud_engines.py` still holds the main engine logic.
**Description:** The original finding proposed extracting `cloud/{_multipart,_http_helpers,_openai_provider,_deepgram_provider}.py`. The split landed partially: the `cloud/` package exists with provider modules and shared transport, but `cloud_engines.py` is still 843 LOC.
**User Impact:** Adding a new cloud provider requires editing the 843-LOC `cloud_engines.py` monolith.
**Root Cause:** The extraction was partial — provider constants and transport shared helpers were extracted, but the main engine dispatch stayed in the monolith.
**Progress:** Partial — `cloud/` package with providers landed; `cloud_engines.py` still 843 LOC.
**Related Files:**
- `voice_typer/server/cloud_engines.py` (843 LOC)
- `voice_typer/server/cloud/` (`_defaults`, `_retry`, `_transport`, `_providers/{openai,deepgram}`)
**Fix:** Extract the remaining engine dispatch logic from `cloud_engines.py` into `cloud/{engine,dispatch}.py` (or similar). Target: `cloud_engines.py` ≤ 300 LOC.
**Severity:** 🟡 Medium
**Category:** Spaghetti / monolith detection

---

### FI-11-A — Prewarm binary integrity: no runtime SHA-256 verification
**Status:** ❌ Not Fixed — re-verified 2026-08-30: no SHA-256 verification of the prewarm binary exists anywhere (`voice_typer/server/prewarm/`, `src-tauri/src/`, manifest files). The prewarm binary is launched without integrity checking.
**Description:** The prewarm binary (frozen Python exe, ~100 MB) is launched at startup/boot with no hash verification. If the on-disk binary is corrupted or tampered with, the app silently runs a degraded warm phase. The fix requires a manifest schema (signed hashes tracked during build) and launcher-side verification before spawn.
**User Impact:** Corrupted prewarm binary goes undetected; degraded warm phase silently.
**Root Cause:** No integrity manifest was implemented; the launcher trusts the on-disk binary.
**Progress:** None.
**Related Files:** `voice_typer/server/prewarm/`, `scripts/build/build_prewarm_*.sh`
**Fix:** Generate a SHA-256 manifest at build time, bundle it alongside the prewarm binary, and verify the hash before spawning. Requires manifest schema design + launcher wiring.
**Severity:** 🔴 High
**Category:** Security / build integrity

---

### Windows/macOS host validation — all fixes tested on Linux sandbox only
**Status:** ❌ Cannot Verify (needs real host) — re-verified 2026-08-30: all fixes are tested on the Linux CI sandbox only. Real-host validation required for Win32 console handler, macOS clipboard restore, and native hotkey binaries per the platform validation runbooks.
**Description:** Many platform-specific fixes (Win32 console handler, macOS clipboard restore, native key-listener binaries) have been implemented but only tested on a Linux sandbox. They must be validated on real Windows/macOS hardware.
**User Impact:** Platform-specific regressions may exist on Windows/macOS that are invisible on Linux.
**Root Cause:** No real Windows/macOS CI runners available in this sandbox.
**Progress:** Blocked on host access.
**Related Files:** `docs/migration/windows-validation-runbook.md`, `docs/migration/macos-validation-runbook.md`
**Fix:** Run the platform validation runbooks on real Windows and macOS hosts.
**Severity:** 🔴 High
**Priority:** P0

---

---

### WM-10 — History DB: dead code deleted; search.py legitimately alive
**Status:** ⚠️ Partial — re-verified 2026-08-30: `recovery.py` + `transcription_download.py` (852 LOC dead code) DELETED. `history_db_internals/search.py` REMAINS — it is the live LIKE-fallback/FTS helper imported by production (10 import sites). The "dead code" claim was wrong about search.py; its separator-only-query behavior is contract-pinned (`tests/test_history_db.py`, `tests/test_history_search_cjk.py`).
**Description:** The original finding claimed three files were dead code. Two were deleted. The third (`search.py`) is a production-imported module that was incorrectly flagged as dead.
**User Impact:** None — the correct deletions landed; the false-positive flag was corrected.
**Root Cause:** The dead-code audit misidentified `search.py` (it looked like a leftover from the FTS5 migration but is actually the live search dispatcher).
**Progress:** Deletions done; false-positive documented and left in place.
**Related Files:**
- `voice_typer/server/history_db_internals/search.py` (live, 655 LOC)
- `tests/test_history_db.py`, `tests/test_history_search_cjk.py`
**Fix:** Already applied — dead code deleted; search.py correctly kept.
**Severity:** 🟢 Low (already resolved)
**Category:** Dead code cleanup

---

### WM-4 — `kill_process_tree` pgid race (Tauri externalBin cannot use `pre_exec setpgid`)
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `posix.rs:52-94` has `pre_exec(|| { setsid(); ... })` for the dev-mode spawn path. However, `mod.rs:43` documents that the Tauri `externalBin` API does NOT expose a `pre_exec` hook, so `setpgid(0, 0)` cannot be called in the release-mode spawn. The pgid-guard fallback (`signal_process_group` at `posix.rs:381-410`) rejects host-pgid matches, but the sidecar still shares the host's pgid — killing the sidecar's process group would kill the host.
**Description:** The `kill_process_tree` function sends `SIGTERM`/`SIGKILL` to the sidecar's process group via `kill(-pgid, sig)`. If the sidecar shares the host's pgid (which it does in release mode, since Tauri's `externalBin` does not call `setsid()`/`setpgid()`), the signal would kill the host. The `signal_process_group` guard at `posix.rs:387` checks `sidecar_pgid == host_pgid` and skips the group signal, but that means the sidecar's children are not killed — only individual child-kill is used. The fix requires either: (a) moving to `tokio::process::Command` (which supports `pre_exec`) for the release path, or (b) adding a `setpgid` call in the sidecar's own startup code.
**User Impact:** Sidecar children may leak on shutdown in release mode if the individual-kill path misses grandchild processes.
**Root Cause:** Tauri v2's `externalBin` API does not expose `pre_exec`/`setpgid`. The process-group kill is gated out, leaving only individual-kill.
**Progress:** None — the dev-mode path is fixed (uses `setsid()` via `pre_exec`); the release-mode path is blocked on Tauri API limitations.
**Related Files:**
- `src-tauri/src/platform/process/posix.rs:52-94`, `:381-410`
- `src-tauri/src/platform/process/mod.rs:43`
**Fix:** Either (a) port the release-mode sidecar spawn to `tokio::process::Command` (bypassing `externalBin`) to regain `pre_exec`/`setpgid` control, or (b) have the Python sidecar call `os.setpgid(0, 0)` as its first startup action.
**Severity:** 🔴 High
**Category:** Lifecycle / process management

---

### WM-30 — recording_controller.py i18n: locale keys exist in all 8 locales, but backend still uses no `i18n.t()`
**Status:** ⚠️ Partial — re-verified 2026-08-30: `recording_controller.py` itself uses ZERO `i18n.t()` calls. HOWEVER, all 8 renderer locale files now have `recording_controller` keys (14 entries: consent_required, watchdog, mic_disconnected, silence_auto_stop, max_duration_stop, etc. at `en.json:1983-2002`). The 2026-08-12 claim "ALL 8 locale files have ZERO keys" is now FALSE — the i18n keys exist, but the Python backend does not use them.
**Description:** The original finding claimed strings were neither in the backend nor in the locale files. The renderer-side keys landed; the backend `recording_controller.py` localization did not.
**User Impact:** Backend notification strings (consent, watchdog, mic-disconnected, etc.) are not localized — they use the renderer-side keys but the Python backend's tray notifications are not wired to them.
**Root Cause:** Locale keys were added to the renderer translations but the Python `recording_controller.py` was never updated to call `i18n.t()`.
**Progress:** Partial — renderer keys done; Python backend wiring missing.
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/translations/*.json` (keys present)
- `voice_typer/server/recording_controller.py` (no `i18n.t()` calls)
**Fix:** Wire `i18n.t("recording_controller.*")` calls in the Python backend notification paths.
**Severity:** 🟡 Medium
**Category:** i18n

---

### WM-44 — service/dictation force_recover: functional but public wrapper deferred
**Status:** ⚠️ Partial — re-verified 2026-08-30: `service/dictation.py:50-93` calls `self._app.recording._force_recover_from_stuck_transcription(force=True)` — works at runtime via private-method access. The public `RecordingController.force_recover()` wrapper was explicitly deferred (documented in the inline comment at lines 61-87).
**Description:** The force-cancel-transcription path works (the private method is called). The layering violation (service layer accessing a private method on RecordingController) was flagged for follow-up but never cleaned up.
**User Impact:** None — the feature works. The residual is a `# noqa: SLF001`-level smell.
**Root Cause:** The public wrapper was deferred to avoid a parallel-worker file conflict and never revisited.
**Progress:** Functional; cleanup deferred.
**Related Files:**
- `voice_typer/server/service/dictation.py:50-93`
- `voice_typer/server/recording_controller.py:445-447`
**Fix:** Add a public `RecordingController.force_recover(self, *, force: bool = False) -> None` method and update the service call. This is a one-method extraction.
**Severity:** 🟡 Low
**Category:** Architecture / layering

---

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

---

### GP-66 — macOS CI hard-fails on missing binary instead of SKIP
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `tauri-macos-build.yml:608-609` still `test -x "$ARM_LISTENER" || { echo "MISSING: $ARM_LISTENER"; exit 1; }` — hard `exit 1`, not a conditional skip.
**Description:** When the macOS native key-listener binary is absent (e.g. on a non-macOS CI runner or a partial checkout), the workflow should skip the binary-existence gate gracefully. Instead it hard-fails with `exit 1`, blocking the rest of the job.
**User Impact:** CI runs on Linux/macOS matrix legs fail unnecessarily if the binary isn't pre-built for that target.
**Root Cause:** The gate was written as a fail-fast assertion rather than a conditional skip.
**Progress:** None.
**Related Files:** `.github/workflows/tauri-macos-build.yml:608-609`
**Fix:** Replace `test -x "$BIN" || { echo "MISSING"; exit 1; }` with `test -x "$BIN" || { echo "MISSING — skipping"; continue; }` (or similar `skip` pattern) so the build continues without the native listener binary.
**Severity:** 🟡 Medium
**Category:** CI/CD

---

### GP-70 — macOS CI codesign --verify step missing
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `tauri-macos-build.yml` has no `codesign --verify` step. Codesign steps exist only in `build.yml` (the Electron path), not in the Tauri macOS workflow.
**Description:** The macOS Tauri build workflow should run `codesign --verify --deep` on the built `.app` bundle to confirm ad-hoc/Developer-ID signing succeeded before the notarization step. Without this, a failed sign is not caught until notarization fails.
**User Impact:** CI can produce an unsigned .app that fails notarization, wasting a full build cycle.
**Root Cause:** The `--verify` gate was added to the Electron `build.yml` but not ported to the Tauri `tauri-macos-build.yml`.
**Progress:** None.
**Related Files:** `.github/workflows/tauri-macos-build.yml` (add codesign --verify step)
**Fix:** Add a step after codesign that runs `codesign --verify --deep --strict /path/to/Voice\ Typer.app` and exits non-zero on failure.
**Severity:** 🟡 Medium
**Category:** CI/CD / signing

---

### LO-23 — RangeSlider.tsx: `aria-valuetext` on ROOT not THUMB (`getThumbAriaValueText`) not applied
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `RangeSlider.tsx:151` still sets `aria-valuetext={...}` directly on the Slider root; no `getThumbAriaValueText` helper exists anywhere.
**Description:** The claimed fix (moving `aria-valuetext` from the ROOT to the THUMB via a `getThumbAriaValueText` prop) was not applied.
**User Impact:** Screen readers may not announce the "value + unit" text on the thumb in all browsers.
**Root Cause:** Fix claimed but never applied; the shadcn `Slider` primitive was not updated to support per-thumb valuetext.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/components/common/RangeSlider.tsx:151`
**Fix:** Add a `getThumbAriaValueText` prop to `components/ui/slider.tsx` and pass it from RangeSlider.
**Severity:** 🟡 Medium
**Category:** A11y

---

### LO-28 — RangeSlider.tsx: visible min/max labels not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `RangeSlider.tsx` renders only the value + suffix; no visible min/max labels were added.
**Description:** The claimed addition of visible min/max labels beside the slider never landed.
**User Impact:** Sighted users see only the current value, not the range endpoints.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/components/common/RangeSlider.tsx`
**Fix:** Render min/max endpoint labels (muted small text at both ends of the track).
**Severity:** 🟡 Low
**Category:** UX

---

### LO-29 — VocabDialog/TemplateDialog + Modal `onCloseIntent` unsaved-changes gate not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: zero matches for `onCloseIntent` anywhere in the renderer.
**Description:** The claimed gate (an `onCloseIntent` prop on Modal so dialogs can warn before discarding unsaved edits) was never added.
**User Impact:** Users can close a vocab/template edit dialog and silently lose unsaved changes.
**Root Cause:** Fix claimed but never applied; no `onCloseIntent` plumbing exists in Modal.tsx or the dialogs.
**Progress:** None.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/common/Modal.tsx`
- `voice_typer/client/src/renderer/src/pages/templates/components/TemplateDialog.tsx`
**Fix:** Add `onCloseIntent` (fired before close completes, cancellable) to Modal and wire it in the vocab/template edit surfaces.
**Severity:** 🟡 Medium
**Category:** UX / data-loss prevention

---

### LO-37 — TroubleshootingSettingsSection: "Keyboard Shortcuts" button opening HelpOverlay not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `TroubleshootingSettingsSection.tsx` renders 6 buttons — no "Keyboard Shortcuts" button, no HelpOverlay integration.
**Description:** The claimed "Keyboard Shortcuts" button in the Troubleshooting section never landed.
**User Impact:** The help overlay is reachable only via the `?` title-bar control and keyboard shortcut, not from Settings troubleshooting.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/components/settings/TroubleshootingSettingsSection.tsx`
**Fix:** Add a "Keyboard Shortcuts" button that opens the shared `HelpOverlay`.
**Severity:** 🟡 Low
**Category:** UX

---

### LO-38 — DoneStep: PunctuationCheatSheet link + `?` shortcut tip not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `DoneStep.tsx` (78 LOC) renders the onboarding summary only; no PunctuationCheatSheet link, no `?` shortcut tip.
**Description:** The claimed Done-step additions (a PunctuationCheatSheet link + a `?`-shortcut tip) never landed.
**User Impact:** Onboarding completion doesn't surface punctuation help or the help-overlay shortcut.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/pages/onboarding/components/DoneStep.tsx`
**Fix:** Add a PunctuationCheatSheetButton + a `?` shortcut hint to the Done step.
**Severity:** 🟡 Low
**Category:** UX

---

### LO-39 — Hidden config field UI rows not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `log_transcriptions`, `clipboard_save_restore`, `unsafe_paste_on_unknown_focus`, `warn_elevated_paste`, `warn_password_paste` appear only in config types + test fixtures; no Settings UI rows render them.
**Description:** The claimed UI rows for hidden config fields never landed.
**User Impact:** Users cannot toggle these fields from Settings (only via config.json).
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/types/config.ts` (fields exist), `voice_typer/client/src/renderer/src/components/settings/*` (no rows)
**Fix:** Add SettingRow toggles for each hidden field (with i18n keys in all 8 locales).
**Severity:** 🟡 Low
**Category:** UI

---

### LO-40 — Settings search "results from other tabs" section not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `Settings.tsx` has no "results from other tabs" section.
**Description:** The claimed search UX (grouping results by tab) never landed.
**User Impact:** Search results are not organized by settings tab.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/pages/Settings.tsx`
**Fix:** Implement the "results from other tabs" grouping.
**Severity:** 🟡 Low
**Category:** UX

---

### LO-42 — AiEnhancement cross-slider validation + LLM URL validation not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `AiEnhancementSettingsSection.tsx` has two RangeSliders (min 0 / max 1) with no cross-validation; `ModelSettingsSection.tsx` `llm_api_url` is a plain input with no URL validation.
**Description:** The claimed cross-slider constraint and LLM URL format validation never landed.
**User Impact:** No guardrail if one AI slider is set below another; malformed LLM URLs are accepted.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/AiEnhancementSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/components/settings/ModelSettingsSection.tsx`
**Fix:** Add cross-slider validation + `llm_api_url` URL-format validation.
**Severity:** 🟡 Low
**Category:** UI / validation

---

### LO-46..LO-49 — Home transcription preview show-more/copy, Discard button, recording level, MicToggleButton error state not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `LastTranscriptionPreview.tsx` has Undo/Repaste only (no show-more/copy); no "Discard" button anywhere in the renderer; no recording audio-level display on Home; MicToggleButton has no error state.
**Description:** Four claimed Home-page features never landed.
**User Impact:** Users cannot show-more/copy a long transcription from the preview, discard a recording, see live level during recording, or see an error state on the mic toggle.
**Root Cause:** Fixes claimed but never applied.
**Progress:** None.
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/home/components/LastTranscriptionPreview.tsx`
- `voice_typer/client/src/renderer/src/pages/home/components/MicToggleButton.tsx`
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
**Fix:** Add the show-more/copy actions, Discard button, level indicator, and error-state styling.
**Severity:** 🟡 Medium
**Category:** UX

---

### LO-50 — `waveform_bubble_wiring.py` `_push_bubble_set_state` transcript kwarg not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `waveform_bubble_wiring.py:259` signature is still `def _push_bubble_set_state(state: str) -> None:`; no `transcript` kwarg; payload only carries `{"state": state}`. `transcription.py` does not call it with a transcript.
**Description:** The claimed partial-transcript push to the bubble never landed.
**User Impact:** The bubble cannot display live partial transcription text.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/server/waveform_bubble_wiring.py:259-265`, `voice_typer/server/waveform.py:56`
**Fix:** Widen `on_set_state` to accept an optional transcript and publish it in the payload; wire `transcription.py` partial results.
**Severity:** 🟡 Medium
**Category:** Bubble feature

---

### LO-66 — Sound-feedback volume slider + Test Sound button not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: no "Test Sound" button, no `sound_volume` config, no volume slider anywhere; `RecordingSettingsSection.tsx` has only the `sound_feedback_enabled` toggle.
**Description:** The claimed volume slider + Test Sound button (config field + sound-manager multiplier) never landed.
**User Impact:** Users cannot adjust sound-feedback volume or preview a cue.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/RecordingSettingsSection.tsx`
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts`
- `voice_typer/client/src/renderer/src/types/config.ts` (no `sound_volume` field)
**Fix:** Add a `sound_volume` config field, a volume slider + Test Sound button in RecordingSettingsSection, and apply the multiplier in sound-manager.
**Severity:** 🟡 Low
**Category:** UX / settings

---

### LO-68 — `microphoneQualityInfo` cross-link not appended
**Status:** ❌ Not Fixed (alternative implemented) — re-verified 2026-08-30: en.json `microphoneQualityInfo` has no cross-link text. The cross-link exists as a SEPARATE banner (`crossLinkBanner`), not appended to the info string.
**Description:** The claimed cross-link appended to the microphone-quality info tooltip never landed (a separate banner was used instead).
**User Impact:** The info tooltip doesn't link to the Microphone page (but a separate banner does).
**Root Cause:** Fix implemented differently (separate banner) than claimed (appended text).
**Progress:** Functionally covered by the separate banner.
**Related Files:** `voice_typer/client/src/renderer/src/i18n/translations/*.json`
**Fix:** Either append the cross-link text to `microphoneQualityInfo` or accept the banner as the resolution.
**Severity:** 🟢 Low
**Category:** i18n / UX

---

### LO-78 — SearchField debounce not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `SearchField.tsx` calls `onChange(e.target.value)` directly; zero debounce logic.
**Description:** The claimed debounce on the shared SearchField never landed.
**User Impact:** Rapid typing fires a state update per keystroke (no debounce batching).
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/components/common/SearchField.tsx`
**Fix:** Add an optional debounce (e.g. `useDebouncedValue`) with a default delay, keeping the input controlled.
**Severity:** 🟡 Low
**Category:** Performance / UX

---

### LO-80 — LocalModelsPanel `localModelsDescription` subtitle not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `LocalModelsPanel.tsx` has no `localModelsDescription` subtitle; the string appears only in test fixtures.
**Description:** The claimed descriptive subtitle on the local-models panel never landed.
**User Impact:** The panel lacks the intended explanatory subtitle.
**Root Cause:** Fix claimed but never applied.
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/components/models/LocalModelsPanel.tsx`
**Fix:** Render the localized subtitle under the panel heading.
**Severity:** 🟢 Low
**Category:** UI

---

### LO-3 — RecordingErrorCard: `t("home.openMicSettings")` localization — component DELETED
**Status:** ⚠️ Moot — re-verified 2026-08-30: `RecordingErrorCard` no longer exists. `client-pages-fixes.test.tsx:440-452` asserts it is not inlined in Home.tsx; `Home-recording-flow-fixes.test.tsx:945` says "RecordingErrorCard deleted". No `openMicSettings` key exists anywhere.
**Description:** The claimed fix (replacing literal "Open Microphone settings" with `t("home.openMicSettings")`) is untraceable — the component was removed rather than localized.
**User Impact:** None — the component is gone; the claimed work is obsolete.
**Root Cause:** The card was deleted as part of the Home-page decomposition before the localization was applied.
**Progress:** N/A (component removed).
**Related Files:** none (RecordingErrorCard deleted)
**Fix:** No action — mark resolved-as-superseded.
**Severity:** 🟢 Low (superseded)
**Category:** i18n (obsolete)

---

### LO-8 — index.css dark-mode `--input` / `--sidebar-border` still alpha-based
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `index.css:175` `--input: oklch(1 0 0 / 15%)`, `:188` `--sidebar-border: oklch(1 0 0 / 10%)` — both still alpha-composited (~1.5:1). Per-preset theme files (themes/*.ts) DO override with opaque values, but the base `.dark` block was never changed to opaque `oklch(0.52)`.
**Description:** The claimed contrast fix (alpha → opaque, 1.5:1 → 3.1:1) was not applied to index.css itself. Only the per-preset theme overrides carry the opaque values, so any theme that does NOT override (or the base fallback) still gets the low-contrast alpha border.
**User Impact:** Border/input contrast in dark mode depends on the active preset; base fallback remains ~1.5:1 (below WCAG 1.4.11).
**Root Cause:** Fix landed only in per-preset theme files, not the base index.css `.dark` block it claimed to change.
**Progress:** Partial (per-preset themes opaque; base block untouched).
**Related Files:** `voice_typer/client/src/renderer/src/index.css:175,188`
**Fix:** Change the `.dark` base block `--input`/`--sidebar-border` to opaque values (e.g. `oklch(0.52 ...)` / `oklch(0.2 ...)`).
**Severity:** 🟡 Medium
**Category:** A11y / contrast

---

### LO-10 — main-process i18n keys added; C-BRAND-1 sub-claim FALSE
**Status:** ⚠️ Partial — re-verified 2026-08-30: the 10 main-process dialog keys (`dialog.pythonBackend.*`: earlyExitSuffix, restartLoopTitle/Body, crashTitle/Body, notFoundTitle/Body, timeoutTitle/Body, preloadError) exist in all 8 main locale files, and `mainT()` calls are wired in `start-python.ts`, `relaunch-app.ts`, `startup-watchdog.ts`. BUT the C-BRAND-1 sub-claim is FALSE: `voice_typer/server/i18n.py:169,175` still contain literal "Voice Typer" strings.
**Description:** The main-process localization landed; the server-side branding literals did not.
**User Impact:** Server-side notify strings are not runtime-substituted with `{appName}`.
**Root Cause:** C-BRAND-1 was claimed fixed, but server i18n fallback strings were missed.
**Progress:** Main dialogs done; server i18n.py literals remain.
**Related Files:** `voice_typer/server/i18n.py:169,175`
**Fix:** Replace the literal "Voice Typer" strings in i18n.py with the `APP_NAME` constant.
**Severity:** 🟡 Medium
**Category:** Branding (C-BRAND-1) / i18n

---

### LO-12 — SettingsSaveIndicator `error` prop + 5th destructive state not added
**Status:** ❌ Not Fixed — re-verified 2026-08-30: no `SettingsSaveIndicator.tsx` exists anywhere in the renderer. `useSettingsConfig.ts` exposes `error`/`loadError` state, but no save-indicator component with an `error` prop or a 5th variant was added.
**Description:** The claimed component change never landed — the component itself is absent.
**User Impact:** No visible destructive/error save state is rendered.
**Root Cause:** Fix claimed but never applied (the save-status UI may use a different surface).
**Progress:** None.
**Related Files:** `voice_typer/client/src/renderer/src/components/settings/useSettingsConfig.ts` (error state exists)
**Fix:** Confirm the intended save-status surface and add the error variant there.
**Severity:** 🟡 Low
**Category:** UX

---

### LO-15 — Bubble locale-change wiring + `intentionallyUnused` whitelist removal not done
**Status:** ❌ Not Fixed — re-verified 2026-08-30: `preload/__tests__/ipc-contract.test.ts:154` STILL contains `const intentionallyUnused = new Set<string>(["bubble:locale-changed"])`; no `onLocaleChanged` handler exists in bubble preload/bridge code. `lifecycle.ts:377` sends the channel, but the renderer-side listener was never wired.
**Description:** The claimed locale-change wiring (preload + bridge + useBubbleBridge + useThemeSync) and the whitelist removal never landed; the contract test still excludes the channel as intentionally unused.
**User Impact:** The bubble's `dir`/locale does not update live when the user switches language.
**Root Cause:** Fix claimed but never applied; the contract-test exemption is still in place.
**Progress:** None.
**Related Files:** `voice_typer/client/src/preload/__tests__/ipc-contract.test.ts:154`, `voice_typer/client/src/main/windows/bubble/lifecycle.ts:377`
**Fix:** Add the `bubble:locale-changed` listener to the bubble preload + useBubbleBridge + useThemeSync, then remove the `intentionallyUnused` exemption.
**Severity:** 🟡 Medium
**Category:** i18n / bubble

---

### LO-51..LO-54 — ConnectionStatusScreen restart button + respawn-exhaustion + RecordingErrorCard — PARTIAL
**Status:** ⚠️ Partial — re-verified 2026-08-30: ConnectionStatusScreen has a "Force retry" button in the restarting state (`isRestarting` + `data-testid="connection-status-force-retry"`), and `respawn_exhausted` handling is live (useConnection + python-namespace). BUT RecordingErrorCard was DELETED, so the Copy/Open-logs/expand affordances are moot.
**Description:** The restart-backend button and reconnect-exhaustion notification are implemented; the RecordingErrorCard enhancements are obsolete (component removed).
**User Impact:** Restart/exhaustion covered; the deleted card's affordances are gone.
**Root Cause:** Mixed delivery — some sub-claims landed, one component was deleted.
**Progress:** Restart + exhaustion done; RecordingErrorCard superseded.
**Related Files:** `voice_typer/client/src/renderer/src/components/layout/ConnectionStatusScreen.tsx`, `hooks/useConnection.ts`
**Fix:** Mark restart/exhaustion resolved; drop the RecordingErrorCard sub-claim.
**Severity:** 🟢 Low (residual superseded)
**Category:** UX / lifecycle

---

### LO-55..LO-57 — Storybook dark/RTL variants + button.stories + test helpers — PARTIAL
**Status:** ⚠️ Partial — re-verified 2026-08-30: 8 `.stories.tsx` files exist (button, RangeSlider, PageHeading, StatCards, InfoTooltip, EmptyState, LevelBar, Spinner), but grep found no dark/RTL/locale variants in them. `renderApp.tsx` + `mocks.tsx` test helpers exist and are used.
**Description:** The claimed Storybook dark/RTL variants on 8 stories were not found; the shared test helpers did land.
**User Impact:** Storybook has no dark/RTL preview variants.
**Root Cause:** Partial delivery — helpers landed, story variants did not.
**Progress:** Test helpers done; story variants missing.
**Related Files:** `voice_typer/client/src/renderer/src/components/**/*.stories.tsx`, `__tests__/helpers/renderApp.tsx`
**Fix:** Add dark/RTL variants to the 8 stories.
**Severity:** 🟢 Low
**Category:** DX / storybook

---

### LO-59..LO-61 — CONTRIBUTING §6.6 done; docs/ux count and README FAQ NOT done
**Status:** ⚠️ Partial — re-verified 2026-08-30: `CONTRIBUTING.md:1005` has "### 6.6 Renderer page & component conventions". BUT only **1** docs/ux/*.md file exists (`model-delete-rationale.md`), not 6. No README FAQ or screenshots section found.
**Description:** One of the three sub-claims landed; the docs count and README work did not.
**User Impact:** README lacks the promised FAQ/screenshots; 5 of 6 docs/ux files are missing.
**Root Cause:** Partial delivery.
**Progress:** CONTRIBUTING §6.6 done; docs/ux + README incomplete.
**Related Files:** `CONTRIBUTING.md:1005`, `docs/ux/` (1 file), `README.md`
**Fix:** Create the remaining docs/ux files + README FAQ/screenshots.
**Severity:** 🟢 Low
**Category:** Docs

---

### LO-62..LO-63 — Bubble text-size propagation + global hotkeys — PARTIAL
**Status:** ⚠️ Partial — re-verified 2026-08-30: `dismissBubble` (Ctrl+Shift+D) is wired (shortcuts.ts:176 + HelpOverlay + all 8 locales). BUT no `text_size` propagation into the bubble renderer and no Ctrl+Shift+M toggle hotkey exist.
**Description:** The dismiss hotkey landed; text-size propagation and the toggle hotkey did not.
**User Impact:** Bubble can be dismissed via hotkey but does not follow app text-size, and there is no toggle hotkey.
**Root Cause:** Partial delivery.
**Progress:** dismissBubble done; text_size + toggle missing.
**Related Files:** `voice_typer/client/src/renderer/src/components/hotkey/shortcuts.ts`, `bubble/useThemeSync.ts`
**Fix:** Propagate text_size to the bubble; add the Ctrl+Shift+M toggle binding.
**Severity:** 🟢 Low
**Category:** Bubble / hotkeys

---

### TR-1 - Tauri tray "Models" sub-menu: unknown dash item + "More Models" click does nothing
**Status:** ❌ Not Fixed — user-reported on the Windows host (2026-08-30). Root cause UNKNOWN — deliberately not investigated; diagnosing it is the fixing agent's mission.
**Description (user report, plain English):** In the Tauri app, when I hover on "Models" in the tray menu, it opens a sub-menu with a first item which is just a dash ("-") — I don't know what that is or what it's for. The other option is "More Models", which is great. But when I click on "More Models", it doesn't do anything. It should bring the app window to the screen to be visible and automatically redirect to the Models page. Right now it doesn't do this. Also, no models appear in this sub-menu — which is completely correct in my use case, because I don't have any models installed. That part is fine; the broken parts are the dash item and the "More Models" click doing nothing.
**Evidence (logs captured at click time, 2026-08-30):**
```
07:23:23 WARN  [dispatch] id=119 cmd=tray_click server error [server.unknown_tray_item]: server error
2026-08-30  10:23:23  DEBUG [SIDECAR-WS] TX response id=119 status=sent
07:23:23 WARN  [TRAY] tray_click dispatch failed: server error [server.unknown_tray_item]: server error
```
**Root Cause:** UNKNOWN — do not assume; investigate as the mission.
**Related Areas (starting hints only, unverified):** the tray menu model published by the Python sidecar vs. the Rust tray click dispatch; the Models-page navigation path from the tray.
**Severity:** ?? Medium-High (a primary tray navigation path is dead in the Tauri shell)
**Category:** Tauri / tray / navigation

---

### TR-2 - Tauri Microphone page is completely empty (no microphones listed)
**Status:** ❌ Not Fixed — user-reported on the Windows host (2026-08-30). Root cause UNKNOWN — deliberately not investigated; diagnosing it is the fixing agent's mission.
**Description (user report, plain English):** Inside the Tauri app, when I open the Microphone page, it is completely empty. No available microphones appear at all. I am 100% sure this is a bug — this machine does have microphones (they work in the Electron app and in Windows itself).
**Evidence:** No logs captured at report time — reproduce on the Windows host first.
**Root Cause:** UNKNOWN — do not assume; investigate as the mission.
**Related Areas (starting hints only, unverified):** the Microphone page in the Tauri runtime vs. the same page working in the Electron runtime; microphone enumeration reaching the renderer.
**Severity:** ?? High (a whole settings page is unusable in the Tauri shell)
**Category:** Tauri / microphone page / enumeration

---

### TR-3 - Tauri tray menu has no "Microphone" item at all
**Status:** ❌ Not Fixed — user-reported on the Windows host (2026-08-30). Root cause UNKNOWN — deliberately not investigated; diagnosing it is the fixing agent's mission.
**Description (user report, plain English):** In the tray menu of the Tauri app, there is no option or button called "Microphone" — unlike "Models", which does appear in the tray menu with its sub-menu. The Microphone entry is simply missing from the tray menu.
**Evidence:** No logs captured at report time — reproduce on the Windows host first.
**Root Cause:** UNKNOWN — do not assume; investigate as the mission. (Possibly related to TR-1/TR-2 — same tray menu model — but that connection is unverified.)
**Related Areas (starting hints only, unverified):** the tray menu model publisher (Python sidecar side) — which items it includes and why "Models" appears but "Microphone" does not.
**Severity:** ?? Medium (tray-based microphone access missing in the Tauri shell)
**Category:** Tauri / tray menu

---

### TR-4 - Tauri application icon is dark-on-dark: it does not adapt to the system theme
**Status:** ❌ Not Fixed — user-reported on the Windows host (2026-08-30). Root cause UNKNOWN — deliberately not investigated; diagnosing it is the fixing agent's mission.
**Description (user report, plain English):** The Tauri application icon is just a black icon, while I am using dark mode in my system. My taskbar is dark, and the application icon is dark — so this is not great. It should be the opposite: if I am in dark mode, the icon should be light; if I am in light mode, the icon should be dark/black — and vice versa. It is not only the icon inside the taskbar: when I open the taskbar, the icon appears black there too, and when I press Alt+Tab, the window also appears with the black icon on a gray background. Because my device is in dark mode, none of this looks right. The icon should contrast with the system theme everywhere Windows surfaces it.
**Evidence:** No logs needed — visual issue; reproduce on the Windows host (dark mode + light mode) and capture screenshots of the taskbar, open taskbar, and Alt+Tab switcher.
**Root Cause:** UNKNOWN — do not assume; investigate as the mission. (Starting questions the fixing agent may explore, unverified: whether Tauri/Windows supports theme-aware icon variants at all, and whether shortcuts/window-class icons can switch with the system theme.)
**Related Areas (starting hints only, unverified):** the Tauri window/app icon configuration (`src-tauri/tauri.conf.json` icons), the bundled `.ico` variants, and any theme-reactive icon-swap mechanism Windows may offer.
**Severity:** ?? Medium (visible polish defect on the primary surface — taskbar + Alt+Tab — for every dark-mode user)
**Category:** Tauri / app icon / theming

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
