## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

> **Won't Fix tasks live in `WONT_FIX.md`** — deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

### T-1 — TAURI-E2E — Full-application validation mission (GOAL MODE: zero problems)

**Status:** 🟡 Partial — IN PROGRESS (2026-09-02 local Windows-host session): full pytest+vitest+cargo suites GREEN on the final state (14165/3683/501, 0 failed); tray status_change WS delivery, ws-mode sidecar app.start, tray Models/Microphone rebuilds (TR-1/2/3) verified landed; headless checklist suite (20 tests) green in the full run; recording_level live-level transport fixed end-to-end; notify AUMID registration added so Windows toasts are attributed correctly. Browser-driven visual walkthrough + real-model dictation on the Tauri host remain the open manual-verification phase (VALIDATE ON WINDOWS HOST — this session ran focused/E2E-checklist evidence, not a full interactive GUI drive). **FV session 2026-09-07 (ON LINUX sandbox):** browser-mode renderer walkthrough executed via headless browser + injected mock bridge (contract mirrors tauri-bridge python-namespace): 15+ phases driven — app shell, sidebar navigation, theme toggle, help overlay, bubble window, Settings (search/appearance/privacy/language), full onboarding flow incl. consent + language steps; 107 evidence artifacts (screenshots/snapshots/console logs) under .tmp-evidence/; the ONLY console errors are the expected pre-bridge "Python bridge not available" degradation warnings — the renderer works in a plain browser and degrades cleanly. Console logs show only the expected pre-bridge degradation warnings plus one triaged Vite-HMR transient (a ReferenceError during concurrent App.tsx live-editing, caught by the ErrorBoundary and recovered on hot update — not a final-state defect). Browser-Use install per §2.3 could not be verified in-sandbox; the built-in agent-browser CLI was used (degraded-mode note in worklog.md under ## Degraded Mode). Full-suite green evidence for the EXACT final code state is the session's final delivery gate (worklog.md ## Validation Performed). REMAINING: interactive GUI walkthrough + real-model dictation on the Tauri host — VALIDATE ON WINDOWS HOST.

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

### CI-1 — Fix all GitHub Actions CI pipeline errors and warnings

**Status:** ⚠️ Partial (FV session 2026-09-07, static audit ON LINUX sandbox): GP-66 + GP-70 edits verified landed (commit 91990ee8, user-approved C-CI-2 override); all action pins audited at Node-24 majors (checkout@v5, setup-python@v7, setup-node@v7, upload-artifact@v6, download-artifact@v6, setup-uv@v7, cache@v5, attest-build-provenance@v4, rust-toolchain@v1 — no Node-20-era pins remain). REMAINING: a validated full CI re-run (manual dispatch, green) — VALIDATE ON HOST; not executable from the sandbox.
**Description:** The CI pipeline contains several known issues: GP-66 (macOS binary-existence hard-fails instead of skipping), GP-70 (no `codesign --verify` step in Tauri macOS workflow), plus broader concerns such as `actions/upload-artifact@v5` / `setup-uv@v6` that run on deprecated Node.js 20 (hard-fail imminent when GitHub removes Node 20), potential secrets-not-set causing silent skip of signing steps, and drift between `build.yml` (Electron) and `tauri-*-build.yml` workflows. The full set of failures can only be determined by running each workflow end-to-end and inspecting logs.
**User Impact:** Red CI blocks merges and masks real regressions; unsigned binaries ship to SmartScreen; Node 20 deprecation will cause hard failures when GitHub drops it (expected late 2026).
**Root Cause:** Individual CI configurations were written at different times for Electron and Tauri targets; action pins were not kept in lockstep across workflow files; signing/secrets gates were added piecemeal without full end-to-end validation.
**Progress:** Partial — GP-66, GP-70 documented; GP-65 (--sign flag) fixed.
**Related Files:**
- `.github/workflows/build.yml`
- `.github/workflows/tauri-windows-build.yml`
- `.github/workflows/tauri-macos-build.yml`
- `.github/workflows/tauri-linux-build.yml`
- `.github/workflows/tauri-build.yml`
- `.github/workflows/client-ci.yml`
- `.github/workflows/codeql.yml`
- `scripts/build/build_tauri_all.sh`

**Fix:** 1) Audit every workflow for pinned action versions — replace any running Node 20 (`upload-artifact@v5`, `setup-uv@v6`, etc.) with their Node 24 majors (`upload-artifact@v6`, `setup-uv@v7`, etc.). 2) Fix GP-66 (replace `exit 1` with a skip pattern on missing binaries). 3) Fix GP-70 (add `codesign --verify` step to Tauri macOS workflow). 4) Fix GP-65 (already applied — `build_tauri_all.sh --sign` now fails hard). 5) Validate every workflow by triggering a manual dispatch on the main branch and confirming green runs. 6) Check for any other Node 20 deprecation warnings in workflow logs.
**Severity:** 🔴 High
**Category:** CI/CD

### BP-30 — Model idle-unload creates and cancels a Timer object on every dictation
**Status:** ❌ Not Fixed (investigation only)

**Description:** With idle-unload enabled, every successful transcription re-arms the idle-unload deadline by cancelling the previous `threading.Timer` and constructing and starting a new one — one Timer object + thread per dictation. The repo's own watchdog module documents and uses the better pattern (a persistent thread looping on `Event.wait(timeout)` recomputed from a last-touch timestamp) after an explicit race fix adopted it. Two divergent timer patterns thus coexist in one codebase. (Distinct from the Won't Fix GQ-L58, which defers an eviction-refactor; this is the idle-unload timer mechanism.)

**User Impact:** None measurable (µs-scale per call). The cost is pattern discipline: the identity-check race guard the current code needs (:247-267) exists only because of the create/cancel pattern.

**Root Cause:** `voice_typer/server/model_manager/_lifecycle.py:241-275` (`touch_model` → `_schedule_idle_unload_timer`); contrast `voice_typer/server/transcription_watchdog.py:182-193` (RACE-013 persistent-thread pattern).

**Gain vs Trade-off:** One persistent daemon thread + monotonic deadline; the timer-identity race guard becomes unnecessary. Contained change with tests already covering idle-unload behavior.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/model_manager/_lifecycle.py`

**Fix:** Mirror the watchdog: one persistent daemon thread; on touch, update `last_touch = time.monotonic()` and set the wake Event; the loop waits `Event.wait(timeout=max(0, deadline - now))` and unloads on expiry. Existing idle-unload tests pin behavior.

**Simplified Fix:** After each dictation, the app throws away its "unload the model after N idle minutes" timer and builds a new one. It already knows the better way — one persistent timer that just gets its deadline nudged — and uses it elsewhere. We switch this spot to that way too.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

**Enrichment (2026-09-04 BP session — Wave 1):** Additional evidence: `_lifecycle.py:241-267` constructs `threading.Timer(delay, lambda: None)` then overrides `timer.function = _fire` post-construction — relying on CPython internals (Timer.run reads self.function at fire time; verified against the local 3.12.14 interpreter source). The internals-dependent mutation AND the identity-check race guard both disappear under the persistent-deadline-thread fix already proposed here.

### BP-31 — Concurrent model download requests are refused rather than queued
**Status:** ❌ Not Fixed (investigation only)

**Description:** The download manager deliberately allows only one gateable model download at a time (the single-flight guard is what makes pause/cancel reliable) — but a second download request arriving while one is active is outright REFUSED with an error toast ("Another model download is already in progress") rather than queued. Downloading several models from the Models page means clicking, getting an error, and manually retrying after each completes. The refusal is documented in-code as deliberate; the queue is the missing UX layer.

**User Impact:** Multi-model setup requires manual sequential retries with an error toast between each — feels broken for a first-session user downloading 2-3 models.

**Root Cause:** `voice_typer/server/service/model/_downloads.py:386-404` (single-flight guard; refusal response), :882-884.

**Gain vs Trade-off:** A one-slot pending queue (next-request-wins) or a UI-level "queued" state. Constraint: keep the shared transfer gate (per-download gates would be the bigger refactor and risk the pause/cancel reliability the gate provides). Alternatively a renderer-side "queue the click, auto-retry when the current finishes" preserves the backend contract entirely.

**My Recommendation:** ✅ Implement — backend FIFO queue, serialized transfers (user decision 2026-09-08; supersedes the renderer-first slice below)

**Progress:** `Decision recorded 2026-09-08 — awaiting implementation.`

**Related Files:**
- `voice_typer/server/service/model/_downloads.py`
- `voice_typer/client/src/renderer/src/components/models/` (queue UI)

**Fix:** Decided solution (2026-09-08) — backend FIFO queue, serialized transfers (NOT true parallel):
- User clicks Download on N models → each shows a "queued" state; transfers run one at a time through the existing single-flight gate, which stays untouched (true parallel would require per-download pause/abort gates — the bigger refactor that risks the pause/cancel reliability the shared gate provides — for no real wall-time win on split bandwidth).
- Backend over renderer-side: the queue survives navigation/reload, is the single source of truth, and works for non-renderer triggers too. Renderer renders queue position from existing progress events plus one new `queue_position` field.
- Queue holds model names only, so unbounded depth is fine (cap display, not storage). Cancel-anywhere must remove from the queue, not just the active transfer — the main edge to test (E6 test mandatory).
- Fallback slice if a try-and-revert is wanted first: renderer keeps a one-deep local queue and auto-fires on the download-complete event; backend unchanged.

**Simplified Fix:** If you try to download a second speech model while the first is still downloading, the app shows an error and makes you click again later. We make that second request wait its turn automatically instead.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### BP-32 — App restart hard-kills the Python backend, skipping the graceful shutdown machinery
**Status:** ❌ Not Fixed (investigation only)

**Description:** When the user picks tray Restart (or a relaunch fires), the Rust host calls `app.restart()` without ever sending the shutdown frame or calling `begin_shutdown()`. Note: on the LOCKED tauri 2.11.5, `App::restart()` DOES trigger `RunEvent::ExitRequested` + `RunEvent::Exit` and waits for Exit delivery (issue #12310 was fixed in tauri 2.4.0 — an earlier version of this entry overstated this; corrected in Review Wave 2). The residual defects are real but narrower: (a) `on_host_exit`'s ~35s-bounded cooperative teardown runs on a DETACHED std::thread that races process exit after Exit delivery — nothing joins it before the restart; (b) `on_relaunch_app` never calls `begin_shutdown()`/notify, so the supervisor and sidecar are not told a restart is coming; (c) the pre-restart flush delay is 10ms (`PRE_RESTART_FLUSH_DELAY_MS`) while the sidecar's own graceful cleanup takes 3-4s — the restart path structurally cannot wait for it; (d) on the supervisor-exhaustion leg the sidecar is already dead, so "mid-cleanup kill" applies only to the tray-Restart leg — where the OS-level reaper (POSIX: unconditional `kill -9`; Windows: job-object TerminateProcess) is what actually ends a still-alive backend.

**User Impact:** After a manual "Restart" from the tray, a still-alive backend can be hard-killed mid-cleanup — exactly when it may be checkpointing the history database, flushing crash-recovery entries, or tearing down the native hotkey listener. The restart path also never tells the backend a restart is coming (no shutdown frame), so it cannot prioritize its own cleanup. In the worst case a restart the user initiated to "fix" the app leaves stale state behind.

**Root Cause:** `lifecycle.rs::on_relaunch_app` (production branch) and the supervisor exhaustion arm invoke `app.restart()` without cooperative teardown: no shutdown frame, no `begin_shutdown()`, and the detached-thread teardown races the restart. Verified against tauri 2.11.5 semantics (W2-R1: tauri v2.4.0 release notes — restart waits for RunEvent::Exit; the detached-thread race is the live defect, not the event emission).

**Gain vs Trade-off:** Gain: every exit path runs the same cooperative teardown, eliminating a designed-safety-stage skip. Trade-off: restart latency grows by up to the bounded graceful-wait budget (seconds, capped) — acceptable for a user-visible restart.

**If We Do It:** Tray Restart and crash-exhaustion relaunch wait (bounded) for the backend to flush and exit cleanly before the app process restarts.

**If We Don't:** Restarts keep hard-killing the backend; WAL resilience mostly hides it, until one day it doesn't (corrupt recovery entries, orphaned hotkey process on slow teardown).

**My Recommendation:** ✅ Implement — closes a real safety-stage gap with bounded latency cost.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs:776-790` (exhaustion path)
- `src-tauri/src/sidecar/lifecycle.rs:51-93` (relaunch path)
- `src-tauri/src/platform/process/posix.rs:41` (unconditional kill -9 reaper)

**Fix:** In `on_relaunch_app` (production branch, before `app.restart()`): call `state.begin_shutdown()`, send the `{"type":"shutdown"}` frame, and run a short-budget bounded wait (reuse `shutdown_sidecar_for_exit` or a short sibling — the 10ms `PRE_RESTART_FLUSH_DELAY_MS` cannot cover the sidecar's 3-4s cleanup; pick an honest bound or make the wait configurable). Consider joining/awaiting the detached teardown thread before restart. Add a supervisor/lifecycle test asserting the shutdown frame + begin_shutdown are attempted before the restart call. Co-implement with BP-35 (same function). (Exhaustion arm: sidecar already dead — only begin_shutdown matters there.)

**Simplified Fix:** Make the app tell the background engine "we're restarting, please finish saving" and give it a brief, honest moment to do so — instead of killing it without warning and hoping for the best.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-33 — Runtime-pack worker subsystem is fully built but never wired (~590 dead LOC, 10 suppressions)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The runtime-pack worker (`WorkerState` in state.rs, `spawn/worker.rs`, the worker section of `spawn.rs`, `platform/worker_path.rs`) is complete, tested code that nothing ever calls: `main.rs` never manages `WorkerState`, and `initialize_worker` has zero production callers. Eleven `#[allow(dead_code)]` Phase-2c suppressions carry "wired when … Phase 2c" comments (state.rs×7, worker.rs×2, spawn.rs×2) — Phase 2c never arrived. Size: ~492 comment-stripped LOC (~984 raw) plus ~700 lines of associated tests.

**User Impact:** None directly — but the shipped binary carries ~1,600 raw lines of dead machinery and its tests, readers can't tell live supervisor code from dead twins, and the dead code creates false confidence that worker respawn/isolation exists.

**Root Cause:** Phase 2b scaffolding landed ahead of the Phase 2c wiring that never followed.

**Gain vs Trade-off:** Gain: ~590 LOC removed from the shipped crate, 10 suppressions gone, E15/E13 hygiene. Trade-off: the runtime-pack split (docs/plan-runtime-pack-split.md) is a live plan — deletion must be weighed against imminent wiring; this is a product-decision gate, not a pure cleanup.

**If We Do It:** Either the worker lifecycle goes live (manage the state + call the initializer after pack verification) or the subsystem is excised and recorded in archive/deleted_files.txt.

**If We Don't:** Dead weight and suppressed warnings keep growing; every future audit re-discovers the same confusion.

**My Recommendation:** 🟡 Defer — needs a product decision on the runtime-pack timeline; until then the debt is documented here.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/state.rs:256-385`
- `src-tauri/src/sidecar/spawn/worker.rs` (337 lines)
- `src-tauri/src/sidecar/spawn.rs:243-447`
- `src-tauri/src/platform/worker_path.rs`

**Fix:** Decision gate first (wire Phase 2c vs excise per E15 with archive/deleted_files.txt entry). If wiring: `app.manage(WorkerState::…)` + call `initialize_worker` after pack verification. If excising: delete the four sites + their tests AND repoint/delete `tests/tauri/mig18/test_externalbin_wiring.py:501-533` — 8+ assertions regex-pin `worker_path.rs` contents (`worker_exe_path_from_env`, `WORKER_BIN_BASE_NAME`, `current_target_triple`, `cfg!(windows)`), the same cross-language gate-test seam BP-39 item (4) documents.

**Simplified Fix:** The code for a background "worker" helper program is finished but never turned on — either turn it on or take it out; don't leave it half-built.

**Implementation Difficulty:** 🟢 Easy (wire) / 🟡 Medium (excise — includes repointing the mig18 gate tests)
**Severity:** 🟡 Medium

**Enrichment (2026-09-04 BP session — Wave 3):** Additional latent defect in the dead worker subsystem: the worker spawn handshake reuses the sidecar's 30s SERVER_STARTED_TIMEOUT_MS, but the worker's prewarm (pages ~180-200 MB runtime-pack libs, cold-HDD 80-110 MB/s) runs BEFORE worker_started is emitted (worker/__main__.py:200 → _ws_server.py:523) — once wired, cold-disk workers get killed mid-prewarm into a respawn loop of partial prewarms. Wire-time fix: dedicated 90-120s WORKER_STARTED_TIMEOUT_MS or emit worker_started before prewarm. Also: the four spawn loops (worker/release/dev) are ~270 copy-pasted lines — see BP-79.

### BP-34 — Rust host logs render a session id on every file line (C-LOG-1 divergence)
**Status:** ❌ Not Fixed (investigation only) — flagged: conforming fix would change a pinned log format (C-LOG-1 requires user-approved format changes + test updates)

**Description:** The canonical log line rule says no per-line session id, with the only sanctioned occurrence being the first-line banner. Python complies. The Rust `CombinedLogger` prints `[sid a3f1b2c4]` on EVERY file log line (`combined.rs:125-131`), and the Rust side has no first-line banner equivalent at all.

**User Impact:** None visible — but log readers and any tooling matching the canonical template miss every Rust line, and the two runtimes' logs look different for the same reason the format rule exists.

**Root Cause:** Rust logger implemented before/independently of the 2026-08-08 format fix; C-LOG-1's text explicitly names the Rust files but the `[sid]` field was never removed.

**Gain vs Trade-off:** Gain: format parity + rule compliance. Trade-off: a pinned, user-approved format changes on the Rust side (C-LOG-1's own change protocol: update tests, user approval).

**If We Do It:** Rust log lines match the Python template; a single init-time banner carries the session id.

**If We Don't:** The binding rule stays violated; grep tooling keeps missing Rust lines.

**My Recommendation:** 🟡 Try and revert — small, test-pinned change; requires user sign-off per C-LOG-1.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/platform/logging/combined.rs:125-131`
- `src-tauri/src/util/crypto.rs:33` (`session_id`, re-exported at `util.rs:30`)

**Fix:** Drop `[sid]` from the file-line formatter; emit one `session=xxxxxxxx` first-line banner at logger init (mirroring Python's `[STARTUP] logging initialized:` line). Update `src-tauri/src/util_tests.rs` / logging tests accordingly. USER APPROVAL REQUIRED per C-LOG-1 before the format changes.

**Simplified Fix:** Make the Rust log lines follow the same clean format as the Python ones, and print the session marker once at startup instead of on every line.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-35 — Tray Restart leaves the tripped sidecar breaker armed: next single crash shows "Please reinstall"
**Status:** ❌ Not Fixed (investigation only)

**Description:** A helper (`clear_restart_counter_for_user_restart`) was written specifically so the tray "Restart" menu item clears the persisted sidecar crash-loop counter — it is dead code with zero callers ("the caller is owned by a different lane and will be added separately" — never added). After the breaker trips (3 relaunches in 10 minutes), a user-initiated Restart produces a fresh app process, but `restart_counter.json` still holds count 3, so the FIRST sidecar crash in the new session immediately emits the "Please reinstall" failure instead of getting the normal 3-attempt budget.

**User Impact:** The user restarts the app to recover from a bad patch of crashes; the app then treats the very next hiccup as fatal and tells them to reinstall. Recovery UX degrades exactly when the user is already troubleshooting.

**Root Cause:** `on_relaunch_app` never calls the documented counter-clear helper (supervisor.rs:219-227 dead; only respawn-success and cold-start paths touch the counter).

**Gain vs Trade-off:** Pure improvement — the helper already exists, is tested, and its documented contract is exactly this call site; no behavior is lost.

**If We Do It:** After tray Restart, a fresh session gets a clean 3-attempt budget again.

**If We Don't:** Restart after a flap remains a false "one more crash = reinstall" trap.

**My Recommendation:** ✅ Implement — one-line wiring of an existing, tested helper.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs:219-227`
- `src-tauri/src/sidecar/lifecycle.rs:51-93`

**Fix:** Call `clear_restart_counter_for_user_restart` in `on_relaunch_app` before `app.restart()` (production branch; dev branch before early return). Add/extend the lifecycle test asserting the counter file resets on user-initiated relaunch.

**Simplified Fix:** When the user chooses Restart from the tray menu, also reset the "how many times has it crashed recently" counter, so the restarted app gets a fresh chance.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-36 — Restart-counter write blocks a Tokio worker on the respawn-success path
**Status:** ❌ Not Fixed (investigation only)

**Description:** The supervisor's own documentation says restart-counter disk I/O must run on `spawn_blocking` (a prior inline read could exceed 100ms under AV-scan/disk contention), and the exhaustion path bundles read+write into `spawn_blocking` correctly — but the SUCCESS path calls `write_restart_counter(0)` inline on the async worker (an atomic temp-file write + fsync + rename).

**User Impact:** None visible per occurrence; a Tokio worker thread (shared with the WS reader, heartbeat, and dispatches) stalls for the fsync duration right at the most sensitive moment — the fresh reconnect after a respawn.

**Root Cause:** Inconsistent application of the file's own blocking-I/O rule.

**Gain vs Trade-off:** Pure improvement; no behavior change, just moving one call onto the blocking pool.

**If We Do It:** The reconnect window never stalls the async runtime on disk I/O.

**If We Don't:** Occasional sub-100ms stalls exactly during recovery — invisible but real.

**My Recommendation:** ✅ Implement — mirrors an existing pattern in the same file.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/supervisor.rs:621` (inline write) vs `:280-290` (the rule) and `:736-749` (correct pattern)

**Fix:** Move `write_restart_counter(0)` into `tauri::async_runtime::spawn_blocking`, or fold it into the existing reconnect-success sequence that already runs off-thread. Keep the counter semantics identical.

**Simplified Fix:** Move one disk write off the event-handling thread so time-critical reconnect work never waits on the hard drive.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-37 — Heartbeat timeout cancels dispatch cleanup; pending entries leak until reader drain (and the mitigation comment is wrong)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `heartbeat` wraps `dispatch_inner` in a 15s `tokio::time::timeout`; on timeout the inner future is DROPPED, so `dispatch_frame`'s own cleanup (removing the pending-map entry) never runs. An in-code comment claims "dispatch_frame's internal 120s timeout eventually removes the entry" — both the value (it's 15s, not 120s) and the mechanism (the inner timer never fires once dropped) are wrong. The file already documents a promised-but-never-implemented Drop guard for exactly this.

**User Impact:** None visible — a bounded handful of stale pending-map entries per hang episode, cleared on respawn/reader drain (PENDING_MAX caps growth). The real cost is the misleading comment steering future maintainers away from the actual leak.

**Root Cause:** tokio timeout cancellation drops the inner future before its cleanup; the documented Drop guard was never added.

**Gain vs Trade-off:** Pure improvement — makes every cancellation path self-cleaning and the comments truthful.

**If We Do It:** Cancelled dispatches clean up their own bookkeeping immediately, on every path.

**If We Don't:** Harmless-but-real bounded leak persists with a comment that actively misleads.

**My Recommendation:** ✅ Implement — small, surgical, already half-designed in the code's own comments.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/ws/heartbeat.rs:121-133,199-207`
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs:400-476`

**Fix:** Add the Drop guard the file already documents: a small struct holding `state` + `id` whose `Drop` removes the pending-map entry; construct it in `dispatch_frame` around the await. Fix the two wrong comments while there.

**Simplified Fix:** When a request is cancelled mid-flight, make it remove itself from the "waiting for reply" list automatically, and correct the explanatory note that currently describes the wrong behavior.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-38 — shutdown_sidecar command bypasses the canonical begin_shutdown contract
**Status:** ❌ Not Fixed (investigation only)

**Description:** `state.rs` defines `begin_shutdown()` (swap + notify_one, "keep any future call site on THIS method, never re-order the two steps") and names the production callers. The renderer-invocable `shutdown_sidecar` command does a raw `shutting_down.swap(true, SeqCst)` with no `notify_one()` — a supervisor mid-backoff (up to 8s) is not woken and only notices after its current sleep.

**User Impact:** A shutdown requested through the command path can lag up to the current backoff step (~seconds) before the supervisor reacts. Correctness is preserved (all pre-spawn re-checks still prevent zombies); it's the contract and the responsiveness that are broken.

**Root Cause:** The command path predates/was never migrated to the canonical method.

**Gain vs Trade-off:** Pure improvement — one-line replacement, no semantics lost.

**If We Do It:** Every shutdown path wakes the supervisor immediately.

**If We Don't:** Documented contract keeps being silently violated; delayed observability on this one path.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/commands/sidecar_cmds/shutdown.rs:47-53`
- `src-tauri/src/state.rs:216-247`

**Fix:** Replace the raw swap with `state.begin_shutdown()` (use its return value for the duplicate-call short-circuit). Extend the state test to pin that the command path routes through begin_shutdown.

**Simplified Fix:** Route the shutdown button through the same official "shut down now" function every other path uses, so the background supervisor reacts immediately.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-70 — Home's download progress bar can freeze at "100%" forever
**Status:** ❌ Not Fixed (investigation only)

**Description:** Home sets `downloadPct` from ANY `download_progress` event — including model downloads started on the Models page — but clears it only when `recordingState` changes. No completion event is subscribed on Home. The progress bar renders whenever `downloadPct !== null`.

**User Impact:** Start a model download on Models, navigate Home, watch it hit 100%... and the "Downloading model" bar stays there indefinitely (until navigation or a recording-state flip). Reads as a stuck download.

**Root Cause:** Progress state lifecycle tied to the wrong clearing signal.

**Gain vs Trade-off:** Pure improvement — clear on the right events.

**If We Do It:** The bar appears exactly while a download is in flight.

**If We Don't:** Occasional "stuck at 100%" confusion.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx:436-448,700-714`

**Fix:** Gate the handler on `recordingState === "loading"` (the state it exists for) or subscribe to the download-complete/state event and clear there. (Inferred from code+event topology — no live GUI in sandbox; verify during implementation.)

**Simplified Fix:** The home page shows a download bar that never dismisses after a download finishes elsewhere — clear it when the download completes, not only when recording state changes.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-71 — History footer renders a literal "N+" placeholder instead of a real count
**Status:** ❌ Not Fixed (investigation only)

**Description:** `t("history.showingCap", { shown: "200", total: "N+" })` — every locale's template interpolates `{total}` ("Showing {shown} of {total} — use search to find older"), but the call site passes the literal string "N+", and hardcodes "200" instead of the display-cap constant. Result: every locale renders "Showing 200 of N+".

**User Impact:** Users see a placeholder that reads like a broken value, in every language.

**Root Cause:** Call-site placeholder never wired to real data (the endpoint exists — get_history_count is already used by the Analytics page).

**Gain vs Trade-off:** Pure improvement — real total via an existing endpoint; locale files unchanged.

**If We Do It:** "Showing 200 of 1,482 — use search to find older."

**If We Don't:** The footer keeps reading "of N+".

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/History.tsx:526`

**Fix:** Wire the real total via `get_history_count` (already consumed by useDashboardData) and `String(HISTORY_DISPLAY_CAP)` for shown. No locale file changes needed.

**Simplified Fix:** The history page footer says "Showing 200 of N+" — show the actual total, which the app already knows how to fetch.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-72 — Microphone test renders a raw i18n key in the no-model state
**Status:** ❌ Not Fixed (investigation only)

**Description:** `TestReviewPanel` calls `t("microphoneTest.qualityNotApplicable")` — but the key exists (in all 8 locales, correctly translated) only at `microphoneTest.qualityFeedback.qualityNotApplicable`. The call site is missing one path level. The t() fallback chain renders the literal key string when no map has it; the only detector is a dev-only console.warn.

**User Impact:** Users without a speech model installed — fresh installs, exactly the population C-MIC-20's N/A state was built for — see bold text reading "microphoneTest.qualityNotApplicable" in the "Estimated Transcription Quality" row, in every locale.

**Root Cause:** Typo'd key path; no compile-time key validation (see BP-73 — the systemic fix).

**Gain vs Trade-off:** Pure improvement — one-line fix, translations already exist everywhere.

**If We Do It:** The row reads "N/A — transcription unavailable" as designed.

**If We Don't:** Fresh installs see a raw key in the mic test — first-impression polish failure.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/microphone/TestReviewPanel.tsx:250`

**Fix:** `t("microphoneTest.qualityFeedback.qualityNotApplicable")`. Add a regression assertion for the row's no-model state. (Systemic guard: BP-73.)

**Simplified Fix:** A missing dot in a translation key name makes the microphone test show the key itself instead of the words "N/A — transcription unavailable" — add the missing dot.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### BP-73 — t() accepts any string: key typos ship raw keys to production UI
**Status:** ❌ Not Fixed (investigation only)

**Description:** The i18n `t(key: string)` surface is untyped: ~1,134 unique static keys across ~1,900 call sites, zero compile-time validation, and the production fallback intentionally renders the raw key. This exact bug class just shipped (BP-72). Locale-parity tests compare locale↔locale, never call-site↔catalog. `resolveJsonModule` is already on, so a keyof-derived key union is available with no new dependency; a strict+loose overload pattern for dynamic keys already exists in this codebase (PythonCall).

**User Impact:** Any key rename or typo at a call site renders the raw key in production, in all locales, until someone happens to notice the string on screen.

**Root Cause:** No type contract between call sites and the catalog.

**Gain vs Trade-off:** Gain: the bug class becomes a compile error. Trade-off: migration effort for dynamic keys (small — documented loose overload) and a generated/derived union to maintain.

**If We Do It:** Key typos fail typecheck instead of shipping.

**If We Don't:** The next key edit can silently regress the UI.

**My Recommendation:** ✅ Implement — proven pattern, proven need.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/translate.ts:131`
- `voice_typer/client/src/renderer/src/i18n/translations/en.json` (catalog source)
- `voice_typer/client/tsconfig.web.json:14` (resolveJsonModule)

**Fix:** Derive a flat key union from en.json (recursive mapped/template-literal type or generated flat-keys module); type `t()`/`tChoice()`/`useT()` with a strict overload + a documented loose overload for dynamic keys (mirror lib/python-bridge/usePython.ts:40-48). Add a CI-side call-site↔catalog completeness check for the dynamic-key escape hatch.

**Simplified Fix:** Translation lookups accept any text as a key, so a typo shows up in the app as the key name itself; make the compiler check key names against the English dictionary.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-74 — Side effects inside setState updaters (theme draft + config merge) — StrictMode double-fires saves
**Status:** ❌ Not Fixed (investigation only)

**Description:** `setCustomDraft((prev) => { ...applyThemeVars(...); cache delete; saveDraftToLS(updated); updateConfigDebounced(...); return updated; })` — DOM writes, cache mutation, localStorage write, and an IPC-save arming all INSIDE the state updater. Same pattern in useSettingsConfig (`_cachedConfig` mutation inside setConfig's updater). React requires updaters to be pure; StrictMode (enabled) double-invokes them in dev, so each custom-color edit double-fires the LS write + save arming; in production, an interrupted/replayed render can re-invoke an updater with a different base state (double IPC save / stale LS write).

**User Impact:** Dev: doubled saves per edit. Production: latent hazard of a double `set_config` or stale local draft under render interruption — low probability, real cost when it fires.

**Root Cause:** Updater used as a convenient "compute + apply" block; purity contract violated.

**Gain vs Trade-off:** Pure improvement — the code already maintains `customDraftRef` outside the updater (the fix is mostly relocation); behavior-preserving.

**If We Do It:** React contract restored; dev double-fire gone; the latent production hazard closed.

**If We Don't:** The pattern stays as a landmine for the next React upgrade or replay behavior.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/useThemeSettings.ts:574-603,430-433`
- `voice_typer/client/src/renderer/src/components/settings/useSettingsConfig.ts:451-457`

**Fix:** Compute `updated` from `customDraftRef.current` OUTSIDE the updater; `setCustomDraft(() => updated)` pure; then apply vars/cache/LS/debounced-save after. For mergeExternalConfig: mutate `_cachedConfig` after a plain (non-updater) `setConfig(merged)`.

**Simplified Fix:** Two settings hooks tuck file-saving and screen-painting work inside a function React may call more than once — move that work outside so it runs exactly once.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-75 — PrivacySettingsSection consent rows are 9 hand-written copies (data-driven fix)
**Status:** ❌ Not Fixed (investigation only)

**Description:** PrivacySettingsSection (638 lines) contains 9 verbatim-shaped `(checked) => updateConfig({key: checked})` handlers, ~20 label/info local variables repeated per row, two visibility arrays, and an Agree-to-All literal duplicating the row set (6 keys). Adding consent #7 (a new cloud provider) requires ~7 coordinated edits in one file.

**User Impact:** None directly — the cost is drift risk and maintenance friction exactly where new providers get added.

**Root Cause:** Rows hand-rolled instead of data-driven (the settingsSections.ts registry precedent exists in the same tree).

**Gain vs Trade-off:** Pure refactor, behavior-preserving; matches an established in-repo pattern.

**If We Do It:** Adding a consent becomes a one-array-entry change.

**If We Don't:** Each new provider multiplies the hand-sync surface.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/PrivacySettingsSection.tsx:98-125,148-210`

**Fix:** Data-drive the rows: one `CONSENT_FIELDS: {configKey, labelKey, infoKey, ariaKey}[]` array → map to SettingRow+Switch; one handler factory; Agree-to-All and the granted-count computed from the same array. i18n keys unchanged (C-I18N-1 respected).

**Simplified Fix:** The privacy page repeats the same switch-row recipe nine times by hand — describe the rows once in a list and generate them.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-76 — Microphone quality preset surface is forked three ways — one fork is production-dead code
**Status:** ❌ Not Fixed (investigation only — corrected in Review Wave 2)

**Description:** The 5-preset audio-quality surface (auto/studio/noisy_room/off/custom) exists in THREE implementations: (1) an inline Select in `components/settings/AudioSettingsSection.tsx:371-400` (the LIVE Settings→Audio surface), (2) the accordion+RadioGroup on the Microphone page (`pages/microphone/components/PresetAccordionSelector.tsx` — whose comment admits "the labels/descriptions here mirror AudioPresetSelector's data"), and (3) `components/microphone/AudioPresetSelector.tsx` — which is PRODUCTION-DEAD: zero render sites outside its own tests (W2-R4 verified: no value imports outside tests; kept alive only by feature-friction.test.tsx renders and 4 stale comments claiming it's live). The preset label/description data is duplicated between the live pair (correction from Review Wave 2: ALL surfaces share ONE i18n key family, `settings.audioEnhancement.preset*` — there is no second key family; the fork is in the component/data layer, not the translations). The shared `AudioPreset` type IS correctly single-sourced (imported from AudioPresetSelector — the one thing keeping the dead file alive).

**User Impact:** Adding or renaming a preset requires edits in two live components (plus one dead file to keep compiling); the two live surfaces can drift in labels and behavior; the dead component misleads every reader (comments claim it is the live surface).

**Root Cause:** Presentation fork carried the data fork with it (E7); BP-15 filed the page-level fork — this is the component-level family. A refactor removed the render sites of AudioPresetSelector without deleting the file.

**Gain vs Trade-off:** Gain: one data source for preset values/labels/descriptions + ~100 dead LOC removed (E15). Trade-off: consolidation must NOT take the dropdown form on the Microphone page (C-MIC-4 forbids a dropdown there; C-MIC-15 pins the accordion's compact header) — the shared base must be the accordion pattern or a preset-data registry module consumed by both presentations.

**If We Do It:** Preset changes propagate to both live surfaces from one source; the dead file is gone; its stale comments stop misleading.

**If We Don't:** Drift risk stays double and the dead-file confusion persists.

**My Recommendation:** ✅ Implement — extract a shared preset-data module (one key family already exists); delete or fold the dead AudioPresetSelector (re-pointing its type import and its test renders). Related: BP-15.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/AudioSettingsSection.tsx:371-400` (live Select)
- `voice_typer/client/src/renderer/src/pages/microphone/components/PresetAccordionSelector.tsx:7-8` (live accordion)
- `voice_typer/client/src/renderer/src/components/microphone/AudioPresetSelector.tsx:52-103` (production-dead; type source + tests only)

**Fix:** Extract a preset-data module (values, label keys, description keys); feed both live presentations from it; consolidate presentation on the pinned accordion+RadioGroup pattern for the Microphone page. For the dead file: either delete it and move the `AudioPreset` type to the data module (re-point PresetAccordionSelector/TestReviewPanel imports, delete/adjust feature-friction.test.tsx renders, record in archive/deleted_files.txt) — or fold it into the data module. C-MIC-4/C-MIC-15 constrain the shape.

**Simplified Fix:** The microphone-quality options are defined three times — two live (settings page + microphone page) and one leftover that nothing displays anymore — define them once, share, and delete the leftover.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

**Enrichment (2026-09-04 BP session — Wave 5):** The dead-fork family has a SERVER-side twin: audio_presets.py's display layer (PRESET_INFO/ALL_PRESETS/get_preset_for_display, ~32 LOC) is production-dead while its docstring claims "frontend fetches presets via IPC" (no such handler) — folded into BP-142(e).

### BP-77 — Renderer micro-batch: lib→pages import, space-y remnant, segmented-control ref churn, TitleBar twins, InfoTooltip glyph
**Status:** ❌ Not Fixed (investigation only)

**Description:** Five verified small items: (1) `lib/utils/models.ts` imports `MODEL_DEFAULT` from `@/pages/onboarding/lib/constants` — the only lib→pages import in the tree (inverted layering; 20 consumers); (2) PrivacySettingsSection still uses `space-y-0.5` for `<li>` spacing — the single production C-UI-10 remnant (gap on the parent is the contract); (3) segmented-control creates a new ref-callback identity per option per render (2N ref attach/detach + Map churn per re-render) and re-observes the container per value change — the file's own comment documents this thrash class as fixed for the container; (4) TitleBar's four toolbar buttons repeat a ~10-class stack with Back/Forward as ~33-line near-twins; (5) InfoTooltip hand-rolls a 12×12 `?` SVG while the app's icon system is hugeicons everywhere — two visually distinct info glyphs coexist.

**User Impact:** Individually invisible; collectively drift surface + micro render/observer churn on shared controls (segmented control is used by Settings tabs, Analytics range, recording-mode toggles, Models).

**Root Cause:** Leftover refactor remnants + mechanical duplication.

**Gain vs Trade-off:** Pure improvement (items 2-5); item 1 is a create-first move + compat re-export per E1.

**If We Do It:** One dependency direction, one spacing idiom, stable refs, ~90 lines saved in TitleBar, one icon language.

**If We Don't:** The micro-debt accumulates where every future edit lands.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/lib/utils/models.ts:16`
- `voice_typer/client/src/renderer/src/components/settings/PrivacySettingsSection.tsx:273`
- `voice_typer/client/src/renderer/src/components/ui/segmented-control.tsx:210-215,255-265,369-371,417`
- `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx:452-596`
- `voice_typer/client/src/renderer/src/components/feedback/InfoTooltip.tsx:115-133`

**Fix:** (1) move MODEL_DEFAULT to lib/utils/models.ts, re-export from the onboarding constants for compat; (2) `flex list-disc flex-col gap-0.5 ps-4 text-xs`; (3) per-option stable callbacks in a ref-held Map + decouple setContainerRef from updateIndicator (read value from a ref inside the observer callback); (4) extract ToolbarButton + NavChevron; (5) render a hugeicons help glyph in InfoTooltip.

**Simplified Fix:** Five small front-end tidy-ups: fix an upside-down import, one leftover old spacing style, avoid needless re-attachments in a shared control, merge four near-identical toolbar buttons, and use the app's standard question-mark icon.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-78 — Rule-text drift: C-MODELS-2 and C-MIC-12 no longer describe the shipped code (user adjudication)
**Status:** ❌ Not Fixed (investigation only) — REQUIRES USER ACTION (agents may not edit AGENTS.md)

**Description:** Two AGENTS.md Hard "Don'ts" have drifted from the code they pin: (1) C-MODELS-2 pins download-button tokens `w-[88px]`/`h-3.5 w-3.5`, but the code ships `w-24`/`h-3 w-3` with a dated 2026-08-28 rationale comment — a deliberate later user decision whose rule text was never updated; (2) C-MIC-12 pins "clipping signaled by the ⚠ glyph and aria tier text, never by recoloring the fill", but the evolved design (documented in code) removed the glyph and DOES recolor the fill (bg-primary → bg-destructive) — the rAF-writes-only-transform invariant IS preserved.

**User Impact:** A future agent obeying the rule text will "fix" the code backwards — undoing deliberate 2026-08-28+ design decisions. This is the exact failure mode AGENTS.md rules exist to prevent, inverted.

**Root Cause:** Rule text not updated when the user changed the design after the rule was written.

**Gain vs Trade-off:** No code change; the gain is rule/code agreement. Only the user can edit AGENTS.md.

**If We Do It:** Rule text matches the shipped contracts; agents stop being misled.

**If We Don't:** The next session risks reverting deliberate design.

**My Recommendation:** ✅ Implement — by the USER: update C-MODELS-2's token values (w-24/gap-2/text-xs, h-3 w-3) and either update C-MIC-12's text to the binary-recolor contract or direct a revert to the ⚠-glyph contract. Recorded here so the decision is tracked; agents take no action until then.

**Progress:** `None yet.` (user action)

**Related Files:**
- `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx:78-84`
- `voice_typer/client/src/renderer/src/components/feedback/LevelBar.tsx:54-70,126-147`
- `AGENTS.md` (C-MODELS-2, C-MIC-12 — user-edited only)

**Fix:** User updates the two rule texts (or orders reverts). Agent-side: none until adjudicated.

**Simplified Fix:** Two of the project's "don't change this" rules describe an older version of two controls; the rules need a one-line refresh from the project owner so future assistants don't undo the newer design.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-79 — Four sidecar/worker spawn loops are copy-paste twins (~270 duplicated lines)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `spawn/worker.rs`, `release_mode.rs`, `dev_mode.rs` share the identical spawn skeleton — env clear + allowlist, spawn, the 500ms-poll/30s handshake loop with four identical CommandEvent arms including a spawn_blocking kill_process_tree + child.kill + 500ms drain, and the deadline kill. NB (Review Wave 4): the loops split 2×2 by process machinery — the release pair (worker.rs/release_mode.rs) uses ShellPlugin + CommandEvent + register_kill_on_parent_exit, the dev pair (dev_mode.rs + the dev sidecar loop) uses tokio Command + read_line + kill_on_drop — so a shared helper must abstract the event-source/handle axis, not just binary/parser/log-tag.

**User Impact:** None directly — but any fix to one loop (e.g. the handshake-timeout defect, kill semantics, or BP-33's wiring) must be hand-replicated four times; a miss leaves the other loops broken silently.

**Root Cause:** Parameterizable helper never extracted; each new runtime mode copy-pasted the previous loop.

**Gain vs Trade-off:** Pure improvement (single parameterized spawn helper over binary/parser/log-tag/args); no behavior change.

**If We Do It:** Spawn-lifecycle fixes land once and apply to all four runtimes.

**If We Don't:** The four loops keep drifting — one already has the latent worker-handshake timeout issue (see BP-33 enrichment).

**My Recommendation:** ✅ Implement (fold into BP-33's wire-or-excise decision: parameterize the surviving loops).

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/spawn/worker.rs:60-220,227-337`
- `src-tauri/src/sidecar/spawn/release_mode.rs:38-364`
- `src-tauri/src/sidecar/spawn/dev_mode.rs:48-242`

**Fix:** Extract one spawn helper parameterized over (binary, parser fn, log tag, dev args); each mode calls it. Coordinate with BP-33 (the worker twin may be excised instead).

**Simplified Fix:** Four copies of the "start the background program and wait for it to say hello" recipe exist — make it one shared recipe.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-80 — Rust atomic-write fsync opens the temp file read-only — a guaranteed no-op (and a warn-log) on Windows
**Status:** ❌ Not Fixed (investigation only)

**Description:** `util/atomic_fs.rs:186-204` opens the temp file with read-only access to fsync it. Win32 `FlushFileBuffers` requires GENERIC_WRITE (MSDN), so on Windows the fsync is a guaranteed no-op that also emits a warning log per migrated model file. There is also no parent-directory fsync (the sibling `atomic_write_bytes` has one), and the temp name is not dotted despite the docstring claiming "dotfile".

**User Impact:** The durability claim of atomic writes doesn't hold on the primary platform (Windows): a crash in the rename window can lose the file; migration logs collect spurious warnings; killed migrations leave visible orphan temp files.

**Root Cause:** OpenOptions copy-paste without the Windows access-mode requirement.

**Gain vs Trade-off:** Pure improvement (write-mode open + parent-dir fsync mirror); negligible cost.

**If We Do It:** Atomic writes are durably fsynced on every platform; the warn noise is gone.

**If We Don't:** Windows keeps shipping best-effort-only atomic writes.

**My Recommendation:** ✅ Implement — same defect class as the TS-side BP-103 (fix both).

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/util/atomic_fs.rs:186-204` (+`:103-110,155,169`)

**Fix:** `OpenOptions::new().write(true)` for the temp handle; mirror the parent-dir fsync from `atomic_write_bytes`; prefix the temp name with a dot. Add a Windows-qualified test (VALIDATE ON WINDOWS HOST).

**Simplified Fix:** The "safely save a file" helper asks Windows to flush the file to disk without permission to write to it — which Windows refuses — so the safety step never happens there.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-81 — Migration batch: owned-value deep-clone + identical-content rewrite + uncounted model-copy failures
**Status:** ❌ Not Fixed (investigation only)

**Description:** Three verified items in the Electron→Tauri migration path: (1) `migrate/config_merge.rs:117-120,140-147` deep-clones `new_val`'s object although it is owned and never reused (the move pattern the docstring claims was applied to old_val was never applied to new_val) and the file is always rewritten even when `written == 0` (BTreeMap re-sort + mtime bump for identical content); (2) `migrate/mod.rs:275,376-385,401` + `migrate/copy.rs:117-125` — model-file copy failures are non-critical and counted NOWHERE: the sentinel is written with `failures=0` and the summary reports only `migration_failed`; (3) `migrate/copy.rs` orphan temps visible after a killed migration.

**User Impact:** A disk-full during model migration (GB-scale copies) writes a success-shaped sentinel and never retries — the user silently re-downloads models. No-op merges churn the config file.

**Root Cause:** Mechanical copy-paste + optimistic error accounting.

**Gain vs Trade-off:** Pure improvement (move semantics + skip no-op writes + a `models_failed` counter + warn).

**If We Do It:** Migrations report honest failures and retry-able state; no-op merges don't touch the file.

**If We Don't:** The silent re-download trap stays.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/migrate/config_merge.rs:117-147`
- `src-tauri/src/migrate/{mod.rs:275,376-385,401, copy.rs:117-125}`

**Fix:** (1) `match new_val { Value::Object(o) => o, _ => Map::new() }` + skip the write when `written == 0`; (2) separate `models_failed` counter surfaced in the summary + a WARN line per failed copy; (3) dotted temp names (with BP-80).

**Simplified Fix:** The settings-migration code needlessly copies data it already owns, rewrites the file even when nothing changed, and doesn't count failed model copies as failures.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-82 — host_locale is write-only and every native dialog title is hardcoded English
**Status:** ❌ Not Fixed (investigation only)

**Description:** `commands/system_cmds/locale.rs` stores the renderer's locale into `state.host_locale` "so the host can localize its native surfaces" — grep shows no production reader. Meanwhile every native dialog title is hardcoded English ("Select Model Folder", "Export Templates/Config/History/Vocabulary") in dialogs.rs and the export commands.

**User Impact:** Non-English users get English OS-level dialog titles; the i18n parity gap is invisible because the mechanism "exists".

**Root Cause:** The localization seam was built one side only.

**Gain vs Trade-off:** Gain: native dialogs follow the app language. Trade-off: title strings need a locale→title map IN THE RUST HOST (the Electron main's mainT() does not exist there — Review Wave 4 correction; a small Rust-side lookup table is required).

**If We Do It:** Native dialogs match the app language.

**If We Don't:** The write-only field keeps implying support that isn't wired.

**My Recommendation:** ✅ Implement (consume host_locale at the title sites) — or explicitly demote to a documented stub.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/commands/system_cmds/locale.rs:26` (state.rs:187)
- `src-tauri/src/commands/system_cmds/{dialogs.rs:127, export.rs:41,87}`, `src-tauri/src/commands/export.rs:56,78`

**Fix:** Pass localized titles from the locale module at each dialog site (mainT-style lookup keyed off `state.host_locale`); add a test pinning that a non-English locale yields a non-English title.

**Simplified Fix:** The app tells the desktop shell which language the user speaks, then ignores it — every system dialog still says "Select Model Folder" in English.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### BP-83 — Rust micro-batch 2: rate-limit sentinel collision, open-logs opens the wrong folder, CSV filter on JSON exports
**Status:** ❌ Not Fixed (investigation only)

**Description:** Four verified small items: (1) `commands/bubble/rate_limit.rs:51-92` — the first call anchors `Instant::now()` then stores a value that can be exactly 0 on Windows (QPC granularity), colliding with the "never toggled" sentinel → the second rapid toggle bypasses the limiter once; (2) `commands/system_cmds/dialogs.rs:44-94` — "Open Logs" opens the config-dir ROOT, not `<config_dir>/logs/` (init.rs:99), while the docstring describes a third, wrong path; (3) `commands/export.rs:110-111` + `system_cmds/export.rs:36-43,83-90` — JSON-only exports (templates/config) offer the CSV filter in the save dialog, letting users save JSON content as .csv; (4) `theme_icon.rs:71-85` — `apply_startup` duplicates `apply_to_window`'s match/log body instead of delegating (14 lines).

**User Impact:** (2) is user-visible: the Open Logs action lands the user in the wrong folder every time. The rest is polish.

**Root Cause:** Mechanical shortcuts + one path drift.

**Gain vs Trade-off:** Pure improvement.

**If We Do It:** Open Logs lands in the logs folder; no mislabeled exports; no sentinel edge case.

**If We Don't:** Small frictions persist.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/commands/bubble/rate_limit.rs:51-92`
- `src-tauri/src/commands/system_cmds/dialogs.rs:44-94`
- `src-tauri/src/commands/export.rs:110-111`, `src-tauri/src/commands/system_cmds/export.rs:36-43,83-90`
- `src-tauri/src/theme_icon.rs:71-85`

**Fix:** (1) store `now.max(1)` (or u64::MAX sentinel); (2) `config_dir().join("logs")` + fix the docstring; (3) parameterize the filter list per export kind; (4) delegate from apply_startup.

**Simplified Fix:** Four small fixes: a timing edge case, the "Open Logs" button opening the wrong folder, a save dialog offering the wrong file type, and one duplicated function.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-84 — Crash recovery never receives the dictation cycle id: lost-dictation detection always says "not recoverable"
**Status:** ❌ Not Fixed (investigation only)

**Description:** `CrashRecovery.add()` accepts a `cycle_id`, and `_detect_and_notify_lost_dictation` matches the `.dictation-in-flight` sentinel's cycle_id against entry cycle_ids to decide recoverability. But NONE of the four production call sites (storage_step.py:118, orchestrator.py:455, paste_step.py:89, dictation_stages.py:414) passes `cycle_id` — so `recoverable` is ALWAYS False in production. The tests pass `cycle_id=` manually and mask the defect.

**User Impact:** After a hard crash mid-dictation, the user is told nothing is recoverable while the partial text sits in recovery.json — the crash-recovery feature's core promise silently never fires.

**Root Cause:** Parameter exists, callers never grew the argument; tests compensate manually.

**Gain vs Trade-off:** Pure improvement — the feature starts working as designed.

**If We Do It:** Crashed dictations are detected and offered for recovery.

**If We Don't:** The crash-recovery notification path remains dead in production.

**My Recommendation:** ✅ Implement — high user-trust impact, cheap fix.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_pipeline/{storage_step.py:118, orchestrator.py:455, paste_step.py:89}`
- `voice_typer/server/dictation_stages.py:414`
- `voice_typer/server/crash_recovery/_store.py:378-383`

**Fix:** Thread the pipeline's cycle id into all four `add()` call sites; change the tests to go through the production callers (or add one that does); add a regression test asserting a sentinel-with-matching-cycle yields `recoverable=True`.

**Simplified Fix:** The crash-recovery notebook records what was typed but forgets to write which recording session it belonged to, so after a crash the app never recognizes the saved text as recoverable.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### BP-85 — CancellationGuard writes crash-recovery entries even when the user disabled crash recovery
**Status:** ❌ Not Fixed (investigation only)

**Description:** The guard's crash-recovery write (`dictation_stages.py:410-419`) is not gated on `config.crash_recovery_enabled` — while every other path gates (storage_step.py:116, paste_step.py:88, orchestrator.py:453). `CrashRecovery.add()` itself doesn't gate either.

**User Impact:** A user who turned OFF crash recovery (a privacy choice) still gets ESC-cancelled / watchdog-aborted late transcriptions persisted to disk — the opt-out is bypassed on this path.

**Root Cause:** The guard was written without the config gate the sibling paths carry.

**Gain vs Trade-off:** Pure improvement — restores the privacy opt-out's integrity; no legit behavior lost.

**If We Do It:** The crash_recovery_enabled setting means what it says on every path.

**If We Don't:** A documented privacy control has a hole.

**My Recommendation:** ✅ Implement — privacy-contract fix.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_stages.py:410-419`
- `voice_typer/server/crash_recovery/_store.py` (add())

**Fix:** Add the `config.crash_recovery_enabled` gate to the guard's write (mirror storage_step's shape); regression test: guard write with the flag off → no file write.

**Simplified Fix:** When the user says "don't save my unsent dictations for recovery", one code path ignores it — close that hole.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### BP-86 — The AI-enhancement failure path publishes the wrong event type, unguarded — and has zero tests
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_apply_ai_enhancement`'s failure path (enhancement_steps.py:381-390) publishes `llm_polish_failed` for a rule-based enhancer failure (E9-class event-type mismatch), and the publish is not suppress-guarded — a raising event bus aborts the whole dictation via run()'s generic except, contradicting the module's documented "does NOT abort" contract. There are zero tests for this path (`rg _apply_ai_enhancement tests/` → 0). Related: `_apply_llm_polish`'s except imports `redact_secret` unguarded (:313).

**User Impact:** A transient enhancement failure can kill the entire dictation result instead of passing the text through — the opposite of the designed behavior.

**Root Cause:** Failure path never tested, so the abort-contradiction and event-type drift shipped.

**Gain vs Trade-off:** Pure improvement — failure isolation per the module's own contract.

**If We Do It:** Enhancement failures degrade to unpolished text (as documented) with an accurate event.

**If We Don't:** The latent abort stays.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_pipeline/enhancement_steps.py:381-390,313`

**Fix:** Suppress-wrap the publish; correct the event type (enhancement-specific key or a shared `text_enhancement_failed`); add failure-path tests; move the `redact_secret` import to module top (guarded).

**Simplified Fix:** When the optional text-polish step fails, one untested code path can throw away the whole transcription instead of just skipping the polish — and it reports the failure under the wrong name.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-87 — Five dead "extracted" pipeline modules (~560 LOC) duplicate the live orchestrator and claim to be wired
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_run_body.py`, `_finalize.py`, `_cancelled.py`, `_stage_timer.py`, `resource_probe.py` in dictation_pipeline/ are complete implementations whose docstrings claim `run()` delegates to them — import-grep proves nothing imports them. The live orchestrator contains the real logic; the dead copies have already drifted (e.g. a `_partial_transcript` mirroring absent from the live code).

**User Impact:** None directly — but a future "wiring" of these modules would silently change behavior, and the docstrings mislead every reader of the core flow.

**Root Cause:** An extraction that renamed docstrings but never moved the callers — the inverse of the usual unfinished split.

**Gain vs Trade-off:** Pure E15 removal; alternative (actually wire them) is a refactor with behavior-equivalence burden.

**If We Do It:** One implementation of the pipeline; honest docstrings.

**If We Don't:** The trap stays armed for the next refactorer.

**My Recommendation:** ✅ Implement (delete; record in archive/deleted_files.txt).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_pipeline/{_run_body.py, _finalize.py, _cancelled.py, _stage_timer.py, resource_probe.py}`

**Fix:** Delete the five modules (E15; no test file imports them — Review Wave 4 correction); fix the live orchestrator's docstrings. (Alternative: complete the extraction — only if a decomposition of run() is genuinely planned; BP-12 enrichment notes run() is ~290 lines.)

**Simplified Fix:** Five leftover copies of the dictation engine's inner steps say "the engine now runs through us" — it doesn't. Delete them.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-88 — History and crash-recovery flush waits run on the pre-paste critical path
**Status:** ❌ Not Fixed (investigation only)

**Description:** Stage 10 of the pipeline runs `history_db.flush()` (unbounded-arg wait; the "≈10s on SQLite busy" docstring figure is the pre-fix root-cause note — current worst case is bounded by the 30s/60s future timeouts; Review Wave 4 clarification) and `crash_recovery.flush(timeout=0.5)` BEFORE PasteStage (stage 11). Every dictation's paste latency therefore includes these waits.

**User Impact:** Paste can stall behind database/recovery flushing — a feelable latency spike on the app's most latency-sensitive action, worst under disk contention.

**Root Cause:** Durability waits sequenced before the user-visible completion instead of after (or off-thread).

**Gain vs Trade-off:** Gain: paste latency no longer contains flush waits. Trade-off: moving waits off the pre-paste path needs a race analysis (durability before "done" vs after) — the fix direction is a design decision (writer FIFO + repaste-path flush), per E5 evaluate 2-3 options.

**If We Do It:** Text appears immediately; durability completes in the background bounded.

**If We Don't:** Occasional multi-hundred-ms (worst-case seconds) paste stalls persist.

**My Recommendation:** 🟡 Try and revert — redesign the flush sequencing with a bounded background drain; revert if repaste semantics regress.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_pipeline/storage_step.py` (stage 10)
- `voice_typer/server/history_db.py` (flush), `voice_typer/server/crash_recovery/_store.py`

**Fix:** Options: (a) move flushes to a post-paste stage; (b) writer-thread FIFO with the repaste path forcing the flush; (c) reduce to crash_recovery-only before paste + history after. Add a latency bench assertion (paste path has no blocking flush). Related: BP-49 (same flush family, correction_usage).

**Simplified Fix:** Before the typed text lands in your document, the app waits for the history database and the recovery file to finish saving — move those saves after the text appears.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

### BP-89 — ESC-cancelled dictations can surface a misleading "No speech detected" toast
**Status:** ❌ Not Fixed (investigation only)

**Description:** ESC-during-transcribe marks the cycle cancelled and aborts the engine; if the abort lands before the first segment, the EMPTY result flows to EmptyCheckStage, whose `_handle_empty_transcription` (transcribe_step.py:418-561) has no cancelled-cycle check (the CancellationGuard is documented "intentionally NARROW", paste-only). The user who pressed ESC sees "No speech detected — check your microphone".

**User Impact:** The app tells an escaping user their microphone is broken — misleading support-bait.

**Root Cause:** Abort path and empty-result path were never composed; abort tests don't cover this interaction.

**Gain vs Trade-off:** Pure improvement — cancelled cycles end quietly (or with a "cancelled" toast).

**If We Do It:** ESC ends silently and correctly.

**If We Don't:** Occasional misleading error after a deliberate cancel.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_pipeline/transcribe_step.py:418-561`
- `voice_typer/server/recording_lifecycle.py:1280-1329`

**Fix:** Add a cancelled-cycle check at the top of `_handle_empty_transcription` (return quietly / emit the cancel toast). While there: fold the duplicated bubble-teardown into `_hide_or_idle_bubble` (dictation_stages.py:424-433). Test: abort-before-first-segment → no empty-transcription toast.

**Simplified Fix:** When you cancel a dictation with Escape at just the wrong moment, the app claims you have a microphone problem — recognize the cancel and stay quiet.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-90 — Pipeline micro-batch: vestigial watchdog parameter, _SHARED_STAGES contract contradiction, unfinished _busy_event migration
**Status:** ❌ Not Fixed (investigation only)

**Description:** Three verified small items: (1) `orchestrator.py:195-221` — `run()`'s `watchdog` parameter is vestigial (sole caller passes None) and `self._watchdog` is write-only; (2) `dictation_stages.py:463-465` — `_SHARED_STAGES` class-level sharing contradicts `build_default_stages`' documented "fresh list so callers can mutate" contract (safe today, mutation hazard); (3) the inverted `_busy_event` semantics are still written raw at every dictation-flow call site (orchestrator.py:616, transcribe_step.py:560, paste_step.py:141, recording_lifecycle stop/cancel) despite `_busyness.py`'s coordinator existing and listing these files as un-migrated.

**User Impact:** None directly — maintainability and migration-completion debt in the core flow.

**Root Cause:** Three unfinished migrations.

**Gain vs Trade-off:** Pure improvement.

**If We Do It:** The coordinator owns busyness signaling; dead parameters gone; contracts truthful.

**If We Don't:** The staged migration stays half-done with its own TODO map.

**My Recommendation:** ✅ Implement (finish the documented migration).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/dictation_pipeline/orchestrator.py:195-221,616`
- `voice_typer/server/dictation_stages.py:463-465`
- `voice_typer/server/dictation_pipeline/{transcribe_step.py:560, paste_step.py:141}`, `voice_typer/server/recording_lifecycle.py`

**Fix:** Remove the watchdog parameter; fix the `_SHARED_STAGES` docstring or stop sharing; route `_busy_event` writes through the busyness coordinator per its own migration list.

**Simplified Fix:** Three tidy-ups in the dictation engine: delete an unused option, make one comment match the code, and finish a half-completed migration to the shared "is the app busy" tracker.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-91 — config_applier's module-level side-effect function is a silent no-op citing a registry that doesn't exist
**Status:** ❌ Not Fixed (investigation only)

**Description:** Module-level `apply_config_side_effects(updates, service)` (config_applier.py:321-367) returns `{"autostart_status": None, "prewarm_status": None}` unconditionally — a silent no-op with zero production callers. Its docstring claims the canonical dispatch lives in `voice_typer.server.service._CONFIG_SIDE_EFFECTS` — no such name exists anywhere. The one test that touches the area takes the `ConfigApplier` class branch, so the module-function elif never executes.

**User Impact:** None today — it's a contributor trap (same class as BP-52): calling the obvious module function silently skips side effects and returns empty status; the docstring points at a phantom registry.

**Root Cause:** An extraction seam that was never completed or removed.

**Gain vs Trade-off:** Pure improvement — delete, or delegate to `service.apply_config_side_effects(updates)`.

**If We Do It:** One true entry point for config side effects; honest docs.

**If We Don't:** The trap stays.

**My Recommendation:** ✅ Implement (delegate or delete — prefer delete; the ConfigApplier class path is the live mechanism).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/config_applier.py:321-367` (docstring :334; real registry :898-910)

**Fix:** Either delete the module function (test already covers the class branch) or make it delegate to the service method; remove the phantom-registry reference. Cross-ref BP-52.

**Simplified Fix:** A "apply settings changes" helper that does nothing, points to a list that doesn't exist, and is only kept alive by a test that never runs it — remove or wire it.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-92 — The diagnostics-export pipeline is production-dead across all four layers (~869 LOC) with stale caller claims
**Status:** ❌ Not Fixed (investigation only)

**Description:** `export_diagnostics` was removed from the server command registry, the TS allowlist, and the Rust allowlist; no Rust command exists; the renderer's Diagnostics section is copy-to-clipboard only; the tray entries were removed. Only tests call `create_diagnostic_bundle`. Meanwhile stale claims persist: `_store.py:635-637` says the CLI uses the server pipeline (it has its own local implementation), and `handlers/_base.py:293` cites the unreachable "ships the log file back to the renderer" path as redaction rationale.

**User Impact:** None directly (the CLI script covers the need) — but ~869 lines of dead pipeline + misleading docs; if ever re-wired as-is, one export costs ~20s CPU (full-log redaction) + a full re-hash of the ~1.3 GB Parakeet ONNX.

**Root Cause:** The feature's surfaces were removed one by one; the pipeline and its rationale comments were left.

**Gain vs Trade-off:** Gain: E15 removal + truthful docs. Trade-off: losing the (dead) server-side export capability — the CLI exists; re-wiring is a product decision.

**If We Do It:** ~869 LOC gone; docs describe the real CLI path.

**If We Don't:** The dead pipeline waits to confuse the next diagnostics session.

**My Recommendation:** 🟡 Defer — user decision: delete (E15, record in archive/deleted_files.txt) or re-wire a real surface (with the 1 MB log-tail cap + ONNX-hash caching the CLI already has).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/diagnostics_export.py` (753 LOC), `voice_typer/server/service/diagnostics.py:44-113`, `voice_typer/server/crash_recovery/_store.py:618-641`, `voice_typer/server/handlers/_base.py:293`
- `scripts/diagnostics.py:124` (the live CLI)

**Fix:** Decide-and-do: (a) delete the pipeline + delegate + tests, fix the two stale claims; or (b) re-wire an IPC surface, adopting the CLI's 1MB log-tail cap and caching ONNX hashes by (path, size, mtime).

**Simplified Fix:** A full "export diagnostics" machinery exists in the app's backend but every button that used it was removed — only the command-line script works. Delete the dead machinery or put a button back.

**Implementation Difficulty:** 🟢 Easy (delete) / 🟡 Medium (rewire)
**Severity:** 🟡 Medium

### BP-93 — The crash buffer's PII filter is attached but never runs (handle() override bypasses filter application)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_CrashBufferMemoryHandler.handle()` overrides stdlib `Handler.handle()` WITHOUT calling super — so the PII filter attached at `_memory_buffer.py:111` never executes (demonstrated empirically with a mirror class: 0 filter invocations). Buffered records are redacted ONLY because the earlier file handler's filter mutates the shared LogRecord in place; that implicit invariant (handler order + level alignment across all three logging modes) is verified today but pinned by nothing.

**User Impact:** None today (redaction happens via the file handler's in-place mutation reaching the buffer) — but the crash buffer's own documented fail-closed protection is unsound-as-stated, and a future handler reorder or level change silently ships unredacted records into crash dumps. (Also corrects the Wave-2 BP-51 enrichment: the third filter attachment is attach-only, NOT a third redaction pass.)

**Root Cause:** Override skipped the filter-application step the base class performs.

**Gain vs Trade-off:** Pure improvement — make the filter real (call `self.filter(record)` in handle()) or drop it and pin the ordering invariant with a test.

**If We Do It:** The crash buffer's redaction no longer depends on an unpinned cross-handler accident.

**If We Don't:** A reorder away from shipping PII into crash archives.

**My Recommendation:** ✅ Implement — one-line fix plus an invariant test.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/crash_handler/_memory_buffer.py:66-153`

**Fix:** In `handle()`, apply filters (call `self.filter(record)` per stdlib semantics) before buffering; OR remove the inert addFilter and add a test pinning file-handler-before-buffer ordering + level alignment across modes. Update the docstring.

**Simplified Fix:** The crash log's privacy filter is plugged in but the socket it's plugged into was bypassed — privacy scrubbing only happens by accident of ordering. Make it deliberate.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-94 — Handler-layer duplication: history cursor extraction ×3, onboarding ack boilerplate ×7 (with a double-redaction bug)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The keyset-cursor extraction block (~45 lines: schema, before_id narrowing, negative check, cursor branch) is copy-pasted verbatim three times in history_handlers.py (94-142, 437-482, 518-571), each with an inline error envelope bypassing `_error_response`. Onboarding handlers repeat an ack-vs-error boilerplate (~15 lines) seven times, and `onboarding_apply` double-redacts the same string (:475 then :480).

**User Impact:** None directly — the same three history call sites already drifted once at the DB layer (BP-56); this triplication is the drift-enabler. ~240 duplicated lines across the two files.

**Root Cause:** Copy-paste handler authoring; extraction never followed.

**Gain vs Trade-off:** Pure improvement (two helpers).

**If We Do It:** Cursor/contract changes land once; the double-redaction bug is gone.

**If We Don't:** Every future handler copy carries the drift risk.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/handlers/history_handlers.py:94-142,437-482,518-571`
- `voice_typer/server/handlers/onboarding_handlers.py:274-481`

**Fix:** Extract `_extract_history_cursor(d, resp)` and an onboarding `_ack_or_error(cmd, result)` helper; drop the second redaction call. Cross-ref BP-56.

**Simplified Fix:** The same "read the next page of history" preamble is pasted into three handlers and the same "confirm or fail" block into seven — turn each into one shared helper.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-95 — Infra micro-batch 2: cloud provider map 3-of-5 copy, quarantine TOCTOU rename, double chmod
**Status:** ❌ Not Fixed (investigation only)

**Description:** Three verified items: (1) `handlers/cloud_test_handlers.py:86-95` — a 3-key manual copy of credential_store's 5-key canonical provider map, justified by a false rationale ("avoid importing the keyring module" — the import is lazy), plus a stale comment; (2) `crash_recovery/_io.py:163-198` — quarantine still uses strftime + `while exists()` TOCTOU + `Path.rename` (fails on Windows when destination exists) while security/file_io.py:709-789 has the hardened os.replace + unique-suffix version; (3) `security/file_io.py:927,954` — PersistedJSON.save re-chmods 0o600 one line after `_secure_atomic_write` already did (documented, 3 layers, zero drift risk — below threshold, noted for opportunistic cleanup).

**User Impact:** None directly; (1) means a new cloud provider is silently unsupported by "test connection"; (2) loses a forensic copy on concurrent same-second quarantines (rare).

**Root Cause:** Copy-paste + one module not migrated to the hardened helper.

**Gain vs Trade-off:** Pure improvement.

**If We Do It:** Provider map single-sourced; quarantine atomic; docs truthful.

**If We Don't:** Small drift traps persist.

**My Recommendation:** ✅ Implement ((3) opportunistic only).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/handlers/cloud_test_handlers.py:86-95,333-335`
- `voice_typer/server/crash_recovery/_io.py:163-198`
- `voice_typer/server/security/file_io.py:927,954`

**Fix:** (1) import the canonical map; (2) os.replace + pid+ns suffix mirroring file_io; (3) collapse the redundant chmod when the file is next touched.

**Simplified Fix:** Three cleanups: a copied settings map missing two entries, an old-style file move that can silently lose a copy, and a permission set twice in a row.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-96 — The dictation start path holds its lock through worker join: ESC/cancel can block up to 2.0 s
**Status:** ❌ Not Fixed (investigation only)

**Description:** `recording_lifecycle.py:620-625` manually `release()`s the `_toggle_lock` (RLock) before joining the worker — but on the user-facing entry path (`toggle()` → `start()` = two acquisitions) the manual release drops the count 2→1 and the lock remains owned through `worker.join(0.1–2.0s)`. (Review Wave 4: there IS one production entry where the release works — the pending-dictation auto-start at `model_manager/_loading.py:264` calls `start()` directly, single acquisition; all user-facing entries — F2/IPC/tray — go via `toggle()` and hit the bug.) Concurrent `stop()`/`cancel()` (auto-stop timer, ESC) block for up to 2.0 s on the cold-model start path — exactly what the comment claims to prevent. Python-docs-verified RLock semantics. Borderline-High (bounded ≤2 s hang, no crash, no deadlock — Review Wave 4 annotation).

**User Impact:** On a cold start (model loading), pressing ESC or the auto-stop firing during the start window can hang the UI action for up to two seconds. No crash; degraded cancel responsiveness.

**Root Cause:** RLock recursion count miscounted by the manual release.

**Gain vs Trade-off:** Pure improvement — restructure so the join runs lock-free (single acquisition). No behavior lost.

**If We Do It:** Cancel/stop always react immediately, even mid-start.

**If We Don't:** Occasional ≤2 s unresponsive Escape on cold starts.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/recording_lifecycle.py:620-673` (entry: toggle; F2 path)

**Fix:** Restructure so `_toggle_lock` is acquired exactly once around the state decision and the worker join happens outside the lock (pass the decision result to the join site). Add a timing test: concurrent cancel during cold start returns < 100ms.

**Simplified Fix:** The "start dictation" path keeps a lock it thinks it released — because the lock was taken twice — so pressing Escape during a slow start can freeze for up to two seconds.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🔴 High

### BP-97 — The ring-buffer resize guard compares against a constant, not the live buffer size (stuck at 5.98 s or 0.68 s)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `recording/session_state.py:637-653` decides whether to resize the ring by comparing the computed size against the module CONSTANT (64), not the live `maxlen`. A 48 kHz session resizes to 187 slots; every later 16 kHz session computes 64 == constant → skips resize → the ring stays at 187 chunks = 5.98 s at 16 kHz (worse than the pre-fix 4 s the code documents as eliminated). Conversely a mid-session 16k→48k device switch updates effective_sr but never re-runs resize (disconnect_handler.py:564) → ring stays at 64 = 0.68 s and preroll is sized for the old rate.

**User Impact:** Silence auto-stop latency drifts by device history; reduced stall headroom / lost preroll on rate-switching devices (e.g. BT headset ↔ built-in mic).

**Root Cause:** Guard written against the wrong reference + resize not invoked on rate change.

**Gain vs Trade-off:** Pure improvement (compare vs live maxlen; re-run resize on restart_stream).

**If We Do It:** Ring sizing is always correct for the current rate and history.

**If We Don't:** Buffer sizing silently depends on which microphone you used first.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/recording/session_state.py:637-653`
- `voice_typer/server/recording/disconnect_handler.py:564`

**Fix:** Guard against the live ring size and invoke the REAL resize method (`SessionState.resize_buffers_for_sample_rate`, today called from `_recorder_split.py:1016`) when `effective_sr` changes in `restart_stream`. Test: 48k session → 16k session → maxlen == 64; 16k→48k mid-session → resize fires. (Method name corrected in Review Wave 4.)

**Simplified Fix:** The recording buffer is sized by comparing the new size to a number in the code instead of the buffer's actual size — so it can stay wrongly sized after you switch microphones.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-98 — VAD auto-calibration bypasses its own clamping floors (silence auto-stop can never fire)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `vad_processor.py:541-542` — `auto_calibrate` writes `_speech_threshold_db`/`_silence_threshold_db` (underscore attributes) directly, bypassing the clamping setters that the R18-F14 comment explicitly says guard "an auto-calibration artifact". The only production writer is the bypassing one; the pinning test asserts UNclamped values (−84/−72 vs floors −65/−55).

**User Impact:** Scope note (Review Wave 4): the dB fallback path runs only when Silero VAD is UNAVAILABLE (default `use_silero_vad=True` gates it at vad_processor.py:499) — so the impact hits environments without the Silero runtime: on a quiet microphone or digital-silence input, calibration can set the speech threshold near −72 dBFS — ambient noise then reads as SPEECH, and silence-based auto-stop never fires: recordings run to the maximum duration.

**Root Cause:** Calibration written against the raw attributes instead of the guarded setters.

**Gain vs Trade-off:** Pure improvement — the documented floors finally apply; the pinning test updates to clamped values.

**If We Do It:** Quiet-mic users get working silence auto-stop.

**If We Don't:** A class of users must always stop manually.

**My Recommendation:** ✅ Implement — small fix, big UX win for quiet mics.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/vad_processor.py:541-542,834-852`

**Fix:** Write through the clamping setters; update the test's expected values to the clamped range. Regression test: calibration input below the floor clamps to the floor.

**Simplified Fix:** The "listen to the room and set the noise threshold" routine skips the safety limits written for exactly that routine — on quiet microphones the app then never detects silence and records until the maximum time.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-99 — Audio lifecycle batch: level monitor not restarted after failed start; synchronous volume-duck on the locked hotkey thread
**Status:** ❌ Not Fixed (investigation only)

**Description:** Two verified items: (1) `recording_lifecycle.py:441,626-673` — the start path stops the level monitor before `recorder.start()` but the EXCEPT path never calls the restart helper (stop and stop-failure paths do) → with the always-visible bubble, the level bar flatlines after one failed start until the next toggle; (2) `recording_lifecycle.py:463` + `volume_controller.py:93-122` — volume ducking runs synchronously on the F2 hotkey thread under `_toggle_lock`: initialize + get_state + is_speaker_active + fade_to = 0.15-0.7 s of subprocess calls (pactl/osascript) per start, aggravating BP-96's lock hold; the ESC-path `restore()` is also synchronous (~250 ms, partially acknowledged in-code).

**User Impact:** (1) flatline after a failed start; (2) the dictation hotkey feels sluggish on Linux/macOS (subprocess volume fades inline).

**Root Cause:** Missing except-path parity + inline subprocess work on the hot path.

**Gain vs Trade-off:** Pure improvement — move `_duck_volume()` into the DictationStart worker thread (the restore path can follow later); add the restart call.

**If We Do It:** Hotkey-to-recording latency drops by the duck duration; bubble level survives failed starts.

**If We Don't:** Sluggish start feel persists on non-Windows platforms.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/recording_lifecycle.py:441,463,626-673`
- `voice_typer/server/volume_controller.py:93-122`, `voice_typer/server/volume_ducker.py`

**Fix:** (1) one call to `_maybe_restart_level_monitor_for_always_visible_bubble` in the start except path; (2) move ducking off the hotkey thread into the start worker — note the in-code comment (:461-462, "first chunk benefits") when re-timing; the ESC-path restore() lock-through-fade is separately WONT_FIX'd as BP-WF-13 (do not re-litigate). Tests: failed-start → monitor restarted; duck called off-thread.

**Simplified Fix:** Two responsiveness fixes: restart the level meter after a failed recording start, and stop doing slow volume-fade subprocess calls on the hotkey thread before recording begins.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-100 — Dead filter-toggle API across three layers; adaptive noise gate is a half-knob
**Status:** ❌ Not Fixed (investigation only)

**Description:** (1) `set_filter_enabled` is defined at three layers (audio_chain_builder.py:169-208, audio_processor.py:402-433, audio_filters/base.py:245) with ZERO production or test callers — docstrings admit "IPC … NOT wired" (same disease as BP-87's dead modules, different symbols). (2) `noise_filter_gate_adaptive` is a half-knob: missing from `_CONFIG_SIGNATURE_FIELDS` (a rebuild short-circuit ignores an adaptive-only change) AND from the IPC allowlist (unsettable from the UI).

**User Impact:** None today — dead API surface + a config field that can only be set by hand-editing config.json, and even then may not take effect without another change.

**Root Cause:** Feature scaffolded, never wired.

**Gain vs Trade-off:** Pure improvement — wire or delete (E15); add the signature field + allowlist entry if the knob stays.

**If We Do It:** No dead API; the adaptive knob works end-to-end or doesn't exist.

**If We Don't:** The scaffold keeps implying runtime filter control that isn't reachable.

**My Recommendation:** ✅ Implement (prefer delete unless a product need exists for runtime toggles).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/audio_chain_builder.py:169-208`, `voice_typer/server/audio_processor.py:402-433`, `voice_typer/server/audio_filters/base.py:245`
- `voice_typer/server/config/_schema.py:719`, `voice_typer/server/config_validators/allowlist.py:504-516`

**Fix:** Delete the three dead layers (record in archive/deleted_files.txt) OR wire an IPC command through the allowlist + registry + renderer. Add `noise_filter_gate_adaptive` to `_CONFIG_SIGNATURE_FIELDS` and (if kept) the allowlist.

**Simplified Fix:** A "turn this audio filter on/off at runtime" switch exists in three places but is connected to nothing, and one noise-gate setting can't actually be changed from the app — remove or connect them.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-101 — Device prewarm opens a real microphone stream at every launch, ungated by visibility
**Status:** ❌ Not Fixed (investigation only) — C-BG-1 concern class, distinct mechanism; flag for the C-BG-1 owner

**Description:** `recording/device_prewarm.py:72-122,233-241` opens and starts a real InputStream at every app launch (to warm PortAudio) with no visibility gate — the same OS-mic-indicator concern C-BG-1 addresses for the level monitor, but via a different path. Additionally, `cached_max_input_channels(None)` bypasses the prewarmed cache with a direct 50-200 ms `sd.query_devices` call on the DEFAULT-device path, whose comment assumes default-mic users are a minority — inverting C-MIC-1's pinned `microphone: null` (System Default) fresh-install default.

**User Impact:** A brief OS mic-indicator blip at every launch for a privacy-first app; the default-device majority pays a cold 50-200 ms query the cache was built to avoid.

**Root Cause:** Prewarm predates C-BG-1's hidden-start invariant; cache miss path assumes the minority case.

**Gain vs Trade-off:** Gain: no indicator blip on hidden starts; default-device users get the cached path. Trade-off: prewarm's purpose (warm the device path before first dictation) may need re-timing (defer to first visible interaction) — a product-timing decision.

**If We Do It:** The mic indicator only lights when the user is actually about to dictate.

**If We Don't:** Every launch blips the indicator (minor but trust-relevant for this app's positioning).

**My Recommendation:** 🟡 Try and revert — gate the stream-open on visibility (or first dictation); keep the query warm-up. Requires the C-BG-1 owner's/product's call.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/recording/device_prewarm.py:72-122,233-241` (+recorder_init.py:265)

**Fix:** Gate the prewarm stream-open on the same visibility contract as the level monitor (or defer to first dictation trigger); cache the default-device channel count. Verify no first-dictation latency regression (bench).

**Simplified Fix:** The app briefly opens the microphone at every startup just to warm it up, even when the window is hidden — tie the warm-up to the user actually being ready to dictate.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### BP-102 — Audio-path documentation drift cluster (5 sites on load-bearing behavior)
**Status:** ❌ Not Fixed (investigation only)

**Description:** Five verified doc/code mismatches: (1) chain-builder docstring says notch AFTER highpass; code appends notch FIRST; (2) vad_helpers.py:165-170 says the grey zone is a "pass branch — no counter resets" while the VAD processor decays/promotes at the grey limit; (3) VolumeController docstring claims fade/poll are "not user-configurable, ignored" while the code reads both config fields; (4) duck default level drifts 0.25 (VolumeDucker) vs 0.20 (VolumeController fallback); (5) session_state.py:727 hardcodes 512 beside the imported `_AUDIO_BLOCKSIZE`, and rate comments (16 Hz/30 Hz/94 Hz) are mutually inconsistent for the same hot path.

**User Impact:** None directly — future maintainers tune the wrong knob because the docs describe a different machine.

**Root Cause:** Comments aged past refactors.

**Gain vs Trade-off:** Pure doc hygiene (fold into BP-24's sweep).

**If We Do It:** The audio path's documentation matches its behavior.

**If We Don't:** The next performance/quality tuning session trusts false docs.

**My Recommendation:** ✅ Implement (append to BP-24's fix scope).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/audio_chain_builder.py` (:169 area), `voice_typer/server/recording/vad_helpers.py:165-170`, `voice_typer/server/volume_controller.py`, `voice_typer/server/volume_ducker.py`, `voice_typer/server/recording/session_state.py:727`

**Fix:** Correct the five comments; replace the hardcoded 512 with the imported constant; unify the rate-figure language.

**Simplified Fix:** Five comments in the audio code describe behavior the code no longer has — fix the comments before someone tunes the wrong setting.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-103 — Windows never persists the restart-history crash-loop breaker (read-only fsync handle throws)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `main/python/atomic-write.ts:52-53` opens the temp file with `"r"` then `fsyncSync(fd)`. Win32 `FlushFileBuffers` requires GENERIC_WRITE (MSDN; libuv maps fsync→FlushFileBuffers and `"r"`→read-only) — on Windows this throws EACCES/EPERM BEFORE the rename. The sole caller (relaunch-app.ts:168-175) catches and warns → `restart_history.json` never persists on Windows → the Electron crash-loop breaker (3 restarts in 60s, C-PERSIST-4's Electron half) silently never fires on the primary platform. Tests fully mock `node:fs` (openSync → 42) and CI runs Linux (fsync on O_RDONLY is legal on POSIX) — the defect is CI-invisible.

**User Impact:** On Windows, a crash-looping app relaunches unboundedly instead of stopping after 3 quick restarts — the exact breaker the file exists to provide. (Platform-qualified: mechanism verified from Win32 docs + code; runtime reproduction needs a Windows host — VALIDATE ON WINDOWS HOST.)

**Root Cause:** Wrong open flag for the fsync contract — the same class as BP-80 on the Rust side.

**Gain vs Trade-off:** Pure improvement — `"r+"` satisfies GENERIC_WRITE and is POSIX-equivalent.

**If We Do It:** The crash-loop breaker works on Windows.

**If We Don't:** Windows crash loops have no circuit breaker.

**My Recommendation:** ✅ Implement — one-character-class fix, pin it in the test.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/main/python/atomic-write.ts:52-53`
- `voice_typer/client/src/main/python/relaunch-app.ts:168-175` (sole caller)

**Fix:** `fs.openSync(tmpPath, "r+")`; update atomic-write.test.ts to pin the open flag. VALIDATE ON WINDOWS HOST.

**Simplified Fix:** The "safe file save" helper asks Windows to flush a file it opened read-only, which Windows refuses — so the crash-restart safety file is never actually saved on Windows.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### BP-104 — Eight reject sites throw bare Errors, degrading to generic "command failed" mid-flight
**Status:** ❌ Not Fixed (investigation only)

**Description:** The shared `PythonIpcError` union has exactly the right codes (backend_not_connected, backend_exited_early, …) and the renderer maps them to curated localized messages — but eight socket-lifecycle reject sites (`tcp/close-handler.ts:58-64`, `tcp/frame-reader.ts:38-44`, `start-python.ts:179,255`, `restart-backend.ts:122`, `relaunch-app.ts:337,481`, `send-to-python.ts:145`) throw plain `Error`, which python-call-handler classifies as generic `command_failed`. Identical pre-flight failures show curated messages; mid-flight disconnects show the generic fallback.

**User Impact:** A backend disconnect mid-command produces a vague "X failed" toast while the same condition before the command produces "Lost connection to the Python backend" — inconsistent error UX exactly when the user is already confused.

**Root Cause:** The typed contract never propagated to the socket-lifecycle owners; errors.ts's docstring ("every reject site constructs a PythonIpcError") overclaims.

**Gain vs Trade-off:** Pure improvement — typed errors at every site + tests pinning rejection codes.

**If We Do It:** All backend-disconnect errors read consistently.

**If We Don't:** The docstring keeps overclaiming; mid-flight errors stay generic.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/main/python/{tcp/close-handler.ts:58-64, tcp/frame-reader.ts:38-44, start-python.ts:179,255, restart-backend.ts:122, relaunch-app.ts:337,481, send-to-python.ts:145}`
- `voice_typer/client/src/main/ipc/python-call-handler.ts:156-157`

**Fix:** Construct `PythonIpcError` with the matching code at each site; add tests pinning the rejection codes; scope the errors.ts docstring truthfully.

**Simplified Fix:** When the connection to the engine drops mid-request, the app shows a generic failure message instead of the clear "lost connection" one it shows in other cases — use the proper error type everywhere.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-105 — Renderer-telemetry duplicates console-forwarder byte-for-byte; logging health is recorded but never surfaced
**Status:** ❌ Not Fixed (investigation only)

**Description:** (1) `windows/renderer-telemetry.ts:46-84` and `windows/bubble/console-forwarder.ts:63-78` produce byte-identical output under parameter substitution; the helper's own docstring promises the main-window migration "when it migrates to this helper" — never landed (main-window.ts:235 still uses its own copy; `cleanConsoleMsg` runs twice per ERROR line). (2) `logging/rotation.ts:80-148` — `getLoggingHealth()` has zero production consumers (no IPC, no diagnostics export, no Tauri port); the ring buffer that exists to surface "logging degraded" records it invisibly; the docstring admits "NOT wired to an IPC handler yet".

**User Impact:** None visible — observability that was built to catch silent log-degradation failures can't report them.

**Root Cause:** Two half-landed migrations.

**Gain vs Trade-off:** (1) pure consolidation; (2) wire an IPC + Diagnostics surface OR decide-and-drop at Electron decommission (BP-22 item).

**If We Do It:** One console forwarder; logging degradation becomes user/diagnostics-visible (or is consciously dropped).

**If We Don't:** The invisible observability gap stays.

**My Recommendation:** ✅ Implement (1); 🟡 Defer (2) to the BP-22 checklist disposition.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/main/windows/{renderer-telemetry.ts:46-84, bubble/console-forwarder.ts:63-78, main-window.ts:235}`
- `voice_typer/client/src/main/logging/rotation.ts:80-148`

**Fix:** (1) delegate renderer-telemetry to `attachConsoleForwarder`, keeping only the ERROR-persistence block; (2) add a `logging:get-health` IPC + Diagnostics row, or add to BP-22's decide-and-drop list.

**Simplified Fix:** Two copies of the same console-forwarding code exist, and a health monitor for the logging system records its findings where nobody can see them — consolidate and surface (or drop) them.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-106 — Main-TS micro-batch 2: cross-process dismiss-shortcut constant, task-ID residue, CSP comment overclaim
**Status:** ❌ Not Fixed (investigation only)

**Description:** Three verified items: (1) the dismiss-bubble shortcut is defined twice cross-process (accelerator form in `shortcuts/global-shortcuts.ts:44`, display form in renderer `hotkey/shortcuts.ts:199`), linked only by comments — a shared constant (precedent: `shared/python-call-error-code.ts`) would pin them; (2) ~20 E4/C-STYLE-1 residue sites survive in main-TS comments ("XV-??", "( sub-finding 1-B-10)", dangling "( / )" prefixes, empty "()" parens) plus an i18n.ts doc claim that TS enforces locale-key parity via a "mapped type" (the type is `Record<string,string>`; only contract tests enforce it); (3) `bootstrap/csp.ts:41-47` claims frame-ancestors is "enforced … in production" — `onHeadersReceived` never fires for file:// loads, so the header CSP (the only spec-honored channel for frame-ancestors) is dev-only (residual risk ≈ nil: deny-all window-open + sandbox).

**User Impact:** (1) a future shortcut change can silently desync main vs renderer; (2)-(3) doc trust.

**Root Cause:** Mechanical residue + one comment written before the file:// semantics were checked.

**Gain vs Trade-off:** Pure improvement.

**If We Do It:** Shortcut forms can't drift; comments are truthful.

**If We Don't:** Small traps accumulate.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/main/shortcuts/global-shortcuts.ts:44` + `voice_typer/client/src/renderer/src/components/hotkey/shortcuts.ts:199`
- `voice_typer/client/src/main/{window-events.ts:115, windows/bubble/console-forwarder.ts:2, dev/bubble-test.ts:2, python/atomic-write.ts:2, windows/bubble/hide-animation.ts:3, python/tcp/startup-watchdog.ts:26, bootstrap/csp.ts:2, i18n.ts:34-35}`

**Fix:** (1) shared `{accelerator, display}` constant in `src/shared/`; (2) comment-cleanup pass (fold into BP-24's sweep); (3) correct the csp.ts claim (note dev-only enforcement of frame-ancestors + the compensating controls).

**Simplified Fix:** The Escape-shortcut is defined separately in two processes with only comments keeping them in sync; plus a batch of stale comment cleanup and one overclaiming security note.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-107 — Templates and Vocabulary are a forked subsystem (~1,100 duplicated lines beyond the page roots)
**Status:** ❌ Not Fixed (investigation only) — extends BP-15 with the subcomponent layer

**Description:** Beyond BP-15's page-level fork, the subcomponent layer is a 1:1 mirror: Toolbar, BulkBar, ListHeader, ListRow, use*Selection hooks, and import/export hooks exist as near-identical pairs (files' own headers: "a 1:1 mirror of the Vocabulary page's … (the Templates UI is an exact copy)"), with byte-identical class strings. Drift has already shipped: row action buttons use `size="icon-xs"` (Vocabulary) vs `size="icon-sm"` (Templates); `title` tooltips exist only on Template rows; the Add-button aria-label exists only on Templates.

**User Impact:** None directly — every cross-page standardization (C-UI-9, C-FILTER-1) costs double work, and the two pages have already diverged visually.

**Root Cause:** The Templates page was created by copying Vocabulary wholesale; only the roots were ever examined.

**Gain vs Trade-off:** Gain: one parameterized collection-page family (list, selection, toolbar, bulk actions). Trade-off: a real refactor (~1,100 lines) with behavior-preservation burden; the pages have legitimately different data models (template vs correction) so parameterization must stay shallow.

**If We Do It:** A fix to list behavior/visuals lands on both pages at once.

**If We Don't:** The pages keep diverging; every design-system change costs double.

**My Recommendation:** ✅ Implement (create-first shared family: generic useListSelection + shared collection components parameterized by row renderer).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/templates/{components/, hooks/}` vs `pages/vocabulary/{components/, hooks/}` (12+ mirrored files)

**Fix:** Extract the shared selection hook (generic over row id), Toolbar/BulkBar/ListHeader shells with slot props, and the import/export hook shape; keep per-domain row renderers and data hooks. Sequence after BP-75's descriptor pattern proves out.

**Simplified Fix:** The Templates page was built by copying the Vocabulary page, including all its sub-parts — a thousand duplicated lines that are already drifting apart; build the shared parts once.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

### BP-108 — Bubble mid-flow aria fallbacks are hardcoded English + hardcoded brand; 4 keys missing from ALL 8 locales
**Status:** ❌ Not Fixed (investigation only)

**Description:** `bubble/helpers.ts:68,72,77,82` build fallback aria strings like "Voice Typer blocked indicator" as literal English with the hardcoded brand. A programmatic scan shows the 4 `*IndicatorAria` keys exist in NO locale file (the other 19 bubble keys exist everywhere); the tf() fallback always renders.

**User Impact:** Non-English screen-reader users hear English + an unrenamable brand during dictation state changes (blocked/cancelling/permission revoked/paste failed).

**Root Cause:** Fallbacks written as literals; the keys were never added to any locale.

**Gain vs Trade-off:** Pure improvement — add 4 keys × 8 locales (real translations per C-I18N-2) and route through t(); the brand goes through the `{appName}` placeholder per C-BRAND-1.

**If We Do It:** Bubble state changes are announced in the user's language.

**If We Don't:** SR users get English + a rename-hostage brand string.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/bubble/helpers.ts:68-82`
- `voice_typer/client/src/renderer/src/i18n/translations/*.json` (missing keys)

**Fix:** Add `bubble.{blocked,cancelling,permissionRevoked,pasteFailed}IndicatorAria` to all 8 locales (genuinely translated, `{appName}` placeholder for the brand); switch the fallbacks to t() with the literals removed. Add the keys to the locale-parity test.

**Simplified Fix:** The floating dictation bubble announces state changes to screen readers in hardcoded English — including the app's name — in every language; add the proper translations.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-109 — The branding checker can't see the brand embedded inside longer string literals
**Status:** ❌ Not Fixed (investigation only)

**Description:** `scripts/check_branding.py:319-322` matches the brand as a quoted standalone literal; the BP-108 literals ("Voice Typer blocked indicator") sit inside longer strings and pass the scanner untouched (verified by execution: scanner says OK while the literals are on code lines). The exemption branches were traced — the gap is the pattern itself.

**User Impact:** None directly — but the CI guard that C-BRAND-1 relies on has a blind spot, which is exactly how BP-108's literals shipped.

**Root Cause:** Regex covers only whole-string matches.

**Gain vs Trade-off:** Gain: substring-in-literal detection for ts/tsx (rs already handled differently). Trade-off: more matches to triage (comment/locale exemptions already exist and must stay).

**If We Do It:** Future "Voice Typer …" literals fail CI instead of shipping silently.

**If We Don't:** The blind spot stays — the next hardcoded brand ships.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `scripts/check_branding.py:319-322`

**Fix:** Add a substring-in-literal pattern for ts/tsx source (excluding the documented exemptions: comments, locale files, the three source-of-truth files); re-run BP-108's sites as the regression case.

**Simplified Fix:** The automated "did anyone hardcode the app name?" checker only catches the name when it's the ENTIRE text — not when it's part of a sentence, which is how the last violation slipped through.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

**Enrichment (2026-09-04 BP session — Wave 5):** The scanner gap also has a Python-side instance: desktop_shortcut.py:606/641 hardcode "Voice Typer.lnk" inside longer literals — the executed checker returns OK (same blind spot). Fix both languages in this entry's scope: f"{APP_NAME}.lnk" + legacy-name fallback.

### BP-110 — History's background refresh truncates deep-browsed lists and kills Load-More
**Status:** ❌ Not Fixed (investigation only)

**Description:** `useHistoryCache.ts:335-351` computes refreshLimit = the loaded offset (up to 5000) — but the server clamps limits to `_HISTORY_LIMIT_MAX = 500` (history_bounds.py:168). The refresh then calls setRecords with ≤500 rows (shrinking the list), setHasMore(500 ≥ refreshLimit > 500 → false), and resets the offset: a user who has deep-browsed (loaded 800+ rows) sees the list collapse to the newest 500 with Load-More dead until remount.

**User Impact:** After background-refresh fires (config change, reconnect) during deep browsing, older rows vanish from the screen and can't be reloaded without leaving the page.

**Root Cause:** Renderer asks for N, backend caps at 500, renderer trusts the response shape without reconciling against its current holdings.

**Gain vs Trade-off:** Pure improvement — merge top-500 with the existing tail (keyset ordering guarantees no overlap).

**If We Do It:** Background refresh updates in place without truncation.

**If We Don't:** The deep-browsing truncation trap persists.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/history/hooks/useHistoryCache.ts:335-351`
- `voice_typer/server/ipc/history_bounds.py:168`

**Fix:** On refresh, merge the returned top-500 with the existing tail keyed by id (keyset order → no duplicates), keep hasMore semantics from the merged length. Regression test: load 800 rows → trigger refresh → list still ≥800 and Load-More alive.

**Simplified Fix:** When the history page quietly refreshes in the background while you've scrolled deep into old entries, it throws away everything past the newest 500 and disables "Load More" — merge instead of replace.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-111 — Renderer misc batch: dead dashboard helpers, circular import, deprecated refs, unvalidated cache, bubble rAF writes
**Status:** ❌ Not Fixed (investigation only)

**Description:** Six verified small items: (1) dead dashboard helpers `computeDailyActivity`/`dayLabel`/`barHeight` (zero callers, prod+tests — superseded); (2) `dashboard/lib/format.ts:11` ↔ `streaks.ts:17` circular import while the header comment claims none; (3) `MutableRefObject` (deprecated in React 19 types) in 6 in-scope files; (4) `home/lib/cache.ts:23-29` `loadCachedRecent` accepts any JSON array — no per-entry validation (asymmetric with loadCachedStats), feeding ActivityList; (5) bubble visualizer rAF writes `style.height/opacity` per frame (contradicts the app's own transform-only C-MIC-12 contract — 7 dots, tiny window, small impact); (6) a contradictory stale comment pair in useAudioLevels.animate() describing superseded reduced-motion designs.

**User Impact:** (4) a corrupted/hand-edited cache can crash Home mount (suspected path); (5) minor per-frame layout work; the rest is hygiene.

**Root Cause:** Superseded helpers left behind + mechanical drift.

**Gain vs Trade-off:** Pure improvement (1)-(3), (6); (4) adds validation; (5) is a C-MIC-12-consistency call (WONT-FIX candidate per W3-A7 — tiny window).

**If We Do It:** Dashboard lib is acyclic and lean; Home cache is validated; bubble rAF follows the app contract.

**If We Don't:** Small debt items accumulate on the dashboard/home paths.

**My Recommendation:** ✅ Implement (1)-(4), (6); 🟡 Try and revert (5).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/dashboard/lib/{streaks.ts:85-113, format.ts:47-71,14-17}`
- `voice_typer/client/src/renderer/src/pages/home/lib/cache.ts:11,23-29` + 5 microphone hooks (MutableRefObject)
- `voice_typer/client/src/renderer/src/bubble/useAudioLevels.ts:225-250,265-274`

**Fix:** (1) delete the three helpers (E15); (2) import localDateKey from @/lib/format (or (1) dissolves it) + fix comment; (3) RefObject rename; (4) per-entry validation or versioned cache key; (5) scaleY transform (origin bottom); (6) delete the stale block.

**Simplified Fix:** A handful of front-end tidy-ups: delete unused chart helpers, break an accidental import cycle, use the current React type, validate cached data before trusting it, animate the bubble with transforms, and remove a self-contradicting comment.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

**Enrichment (2026-09-04 BP session — Wave 5; corrected Wave 6):** The dead-dashboard-helpers finding (1) holds for its three symbols (computeDailyActivity/dayLabel/barHeight — zero callers). Wave 5 additionally claimed recordDayKey/dayGroupHeading/rangeDaySpan were dead — **Wave 6 REFUTED this**: they are LIVE (groupRecordsByDate → ActivityList with groupByDate=true on History's default sort; computePeriodStats/computeCorrectionStats/buildActivityBars → useDashboardData). Their correct disposition is un-exporting (test-only direct importers), NOT deletion — do not fold them into the deletion set.

### BP-112 — Checkbox and radio-group focus rings ship at 30% alpha (C-FOCUS-2 violation the sweep missed)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `components/ui/checkbox.tsx:31` and `components/ui/radio-group.tsx:37` render `focus-visible:ring-3 focus-visible:ring-ring/30`. The C-FOCUS-2 fix swept Button/Input/Textarea/SelectTrigger (pinned by focus-ring-contrast.test.tsx) — these two were never covered, and both are also the only 2/16 ui files importing `cn` from `@/lib/utils` instead of `#utils` (a distinct lineage cluster). The test's own header documents that /30 composites to 1.15:1-2.45:1 — below WCAG 1.4.11's 3:1. Partial mitigation exists (full-opacity `focus-visible:border-ring` + enlarged hit area), so the indicator isn't fully invisible — but the ring violates the pinned "never the alpha" contract.

**User Impact:** Keyboard users get a sub-contrast focus indicator on standard form controls (Settings/Microphone switches and choices) in some themes.

**Root Cause:** The C-FOCUS-2 sweep's file list was incomplete; the two files came from a different lineage and were missed.

**Gain vs Trade-off:** Pure improvement — drop `/30`, keep ring-3 (C-FOCUS-5); extend the pinning test.

**If We Do It:** Every interactive primitive meets the pinned focus-contrast contract.

**If We Don't:** Two standard controls keep an effectively-invisible ring in every theme per the test's own measurements.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/ui/{checkbox.tsx:31, radio-group.tsx:37}`
- `voice_typer/client/src/renderer/src/components/ui/__tests__/focus-ring-contrast.test.tsx` (coverage gap)

**Fix:** Remove `/30` from both files; add Checkbox + RadioGroupItem blocks to focus-ring-contrast.test.tsx; (opportunistic) unify the `cn` import path to `#utils`.

**Simplified Fix:** Two standard controls (checkboxes, radio buttons) still have the washed-out focus ring that was fixed everywhere else — the earlier sweep missed them because they came from a different code lineage.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-113 — Settings in-section search: per-row gating missing in 7 sections (incl. Recording, the largest); label derivation triplicated
**Status:** ❌ Not Fixed (investigation only)

**Description:** The per-row `isVisible(...) && <SettingRow>` gating (so a search shows only matching rows) exists in MOST sections — General (6 rows gated), Audio (5), PostProcessing (5), Theme (4), Privacy (8), LinuxWindowButtons (6), PrewarmAndUpdates (5), plus AudioFilterChain — but NOT in: **Recording (the largest, 14 rows), Overlay, LlmPolishing, AiEnhancement, Diagnostics, Resources, Troubleshooting** — those show the WHOLE section when one row matches; Audio's own comment (:270-274) calls the ungated behavior "defeated the purpose of in-section search". Additionally, the label-locals + items-array + 3-arg isVisible pattern is triplicated across the gated sections too (Recording: 28 locals + 14-row array; Audio: 42/21; PostProcessing 36 t()-calls; …) plus 4 identical `set_esc_cancel_paused` callbacks — the same family as BP-75. (Enumeration corrected in Review Wave 4 — the original filing understated the rollout.)

**User Impact:** Searching settings on the largest sections (Recording) shows everything instead of the match — search feels broken there.

**Root Cause:** The per-row fix rolled out to a subset; the authoring pattern (locals+array+JSX) makes each new section a copy-paste.

**Gain vs Trade-off:** Pure improvement — extract a shared `GatedSettingRow`/descriptor pattern (collapses the triplication too); behavior change is exactly the intended search behavior.

**If We Do It:** Settings search filters rows in every section.

**If We Don't:** Search on most sections remains cosmetic.

**My Recommendation:** ✅ Implement — sequence after BP-75 (same descriptor pattern).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/{RecordingSettingsSection.tsx, OverlaySettingsSection.tsx, PostProcessingSettingsSection.tsx, LlmPolishingSettingsSection.tsx, AiEnhancementSettingsSection.tsx}` (ungated) vs `{GeneralSettingsSection.tsx, AudioSettingsSection.tsx:270-274}` (gated)

**Fix:** Apply the per-row wrap to the ungated sections (Recording + Overlay + LlmPolishing + AiEnhancement + Diagnostics + Resources + Troubleshooting) — or extract the shared GatedSettingRow + descriptor rows per the audioFilterRowDescriptors precedent (folds BP-75's family). Add a per-section search test.

**Simplified Fix:** Searching within a settings page only hides non-matching rows on most sections — but on Recording (the largest) and six smaller ones the search highlights nothing and shows everything. Finish the rollout.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### BP-114 — Renderer micro-batch 3: wrong error copy on prewarm run, unmemoized models components, hand-rolled chevrons, forked focus-modality hook, loose push payloads
**Status:** ❌ Not Fixed (investigation only)

**Description:** Six verified items: (1) PrewarmAndUpdates.tsx:242-245 — the "Run Prewarm Now" failure shows the View-Log handler's error copy ("Could not open prewarm log"); (2) components/models/* has zero memo() (LocalModelsPanel, CloudProvidersPanel, ModelCardActions, DownloadProgressBar) while every settings section is memo'd — download progress ticks re-render all model rows; (3) hand-rolled SVG chevrons/arrows in number-input-stepper + HotkeyPicker (the app is standardized on hugeicons — extends BP-77's InfoTooltip item); (4) the pointer-modality focus state machine (C-FOCUS-3's documented pattern) is duplicated verbatim in textarea.tsx and input.tsx — a future contract fix must land twice; (5) push_events.ts payloads are looser than the Python emitters (documented-intentional looseness; high-traffic payloads like download_progress could be tightened — E9); (6) ~30 task-ID comment residue sites across settings/hotkey/lib/types (E4/C-STYLE-1) with ~15 dangling stripped-ID placeholders.

**User Impact:** (1) a user who clicked "Run" is told opening a log failed; (2) heavier re-renders during downloads; the rest is drift/hygiene.

**Root Cause:** Copy-paste error key + uneven memo discipline + pattern forking.

**Gain vs Trade-off:** Pure improvement; (5) optionally tighten only the high-traffic payloads.

**If We Do It:** Honest error copy, calm downloads during ticks, one glyph language, one focus-modality implementation.

**If We Don't:** Small UX lie + drift surface persist.

**My Recommendation:** ✅ Implement ((5) 🟡 Defer — documented-intentional).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/components/settings/PrewarmAndUpdates.tsx:242-245`
- `voice_typer/client/src/renderer/src/components/models/*`
- `voice_typer/client/src/renderer/src/components/ui/number-input-stepper.tsx:66-208`, `components/hotkey/HotkeyPicker.tsx:191-205`
- `voice_typer/client/src/renderer/src/components/ui/{textarea.tsx:15-36, input.tsx:14-35}`
- `voice_typer/client/src/renderer/src/types/ipc/push_events.ts`
- ~30 E4 residue sites (see W3-A8 report)

**Fix:** (1) new `about.prewarmRunFailed` key (all 8 locales); (2) memo() the three components + stabilize handler props; (3) hugeicons glyphs; (4) extract `usePointerFocusModality()` (or shared class-pair const); (5) tighten download_progress/notification/navigate payloads to match emitters; (6) comment-cleanup pass (fold into BP-24).

**Simplified Fix:** Six front-end fixes: one wrong error message, components that re-render too much during downloads, two hand-drawn icons, a duplicated focus pattern, loose event data types, and leftover task-number comments.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

**Enrichment (2026-09-04 BP session — Wave 5):** Item (6) residue count grew: ~40 additional E4/C-STYLE-1 sites in hooks/ + hooks/models/ + test-setup.ts + a11y tests + lib/theme-draft-storage.ts ("fix #4/#7/#8/#9" ×17, "Sub-agent N" ×7, "DX-013", "SEGMENTED-CTRL-FIX", "Phase 4.5" ×5, stripped "( partial split)" ×2). Include in the comment-cleanup pass.

### BP-115 — The offline runtime-pack download verifies a .partial and returns: the install stage does not exist
**Status:** ❌ Not Fixed (investigation only)

**Description:** `download_offline_pack_with_resume` writes `pack-<version>.partial`, verifies the SHA-256, and returns. Repo-wide, `.partial` is referenced ONLY inside offline_pack.py — no code (Python, Rust, worker, or renderer) renames it, extracts it, writes `<version>/pack-manifest.json`, or calls `atomic_swap_offline_pack` (zero production callers; the swap exists but nothing invokes it). `_local_offline_pack_version()` scans for a manifest that is never created on the runtime path. Only the NSIS full-offline installer installs a pack. Consequences: consent-gated pack delivery never yields a usable offline engine; `update_available` stays True and re-triggers every launch (startup_tasks.py:320 `trigger_download=True`); every launch re-hashes the whole ~200 MB partial; and a `Range: bytes=<size>-` request on the complete file gets a 416 → RuntimeError → exception log loop each launch.

**User Impact:** A user who consents to the offline pack download downloads ~200 MB successfully… and the app remains without the offline engine, re-attempting on every start (status detail: the 416-loop is server-dependent — suspected, doc-supported).

**Root Cause:** The pipeline was built download-first; the install stage (§8.3 swap, manifest write) was never wired into the `_bg` success path. Plan §12 tracks only the Rust main.rs wiring gap — install is untracked.

**Gain vs Trade-off:** Gain: the consent flow actually delivers a working offline engine. Trade-off: new install code (extract/verify/swap) must be written and tested — the pieces exist unwired.

**If We Do It:** After download: rename/extract into `<version>.new/`, write the manifest, verify, atomic-swap, start the worker; treat `offset == content_length` as already-downloaded; require 206 on resume.

**If We Don't:** The offline-pack feature remains non-functional end-to-end on the consent path.

**My Recommendation:** ✅ Implement — the feature's core promise is currently unreachable.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/offline_pack.py:877-1074` (download), `:797-871` (orphan swap)
- `voice_typer/server/service/update_check.py:602-635` (`_bg`), `:474-509` (manifest scan)
- `scripts/windows/full-offline-installer.nsi:99` (the only real installer)

**Fix:** Add the install step to `_bg`'s success path: extract into `<version>.new/`, write `pack-manifest.json`, run `verify_offline_pack_or_skip`, call `atomic_swap_offline_pack`, then worker start (depends on BP-33's wiring decision). Treat a completed file as done (skip 416); require status 206 when resuming (see BP-118). Tests: happy-path install from a fixture pack; already-downloaded short-circuit.

**Simplified Fix:** The big offline-speech-engine download finishes, gets verified… and then nothing installs it — the app leaves the downloaded file as-is and tries again every launch. Write the missing "unpack and activate" step.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🔴 High

### BP-116 — offline_pack carries ~600 lines of test-only scaffold; the pack lock misclassifies contention
**Status:** ❌ Not Fixed (investigation only)

**Description:** Nine public §8.x APIs have zero production callers (each with a dedicated test file): the §8.8 disk gate (never called before download), the §8.13 cross-process lock (update_check's own comment admits unwired), the §8.3 swap, §8.14/15 transcription queue, §8.17 download queue (loop never calls pack_should_pause), §8.11 fallback, §8.5 metered detection (`_nlm_detect_metered` returns None unconditionally, "Real implementation omitted"), §8.18 Windows Authenticode (`_wintrust_verify` returns None unconditionally; macOS counterpart is real), plus OfflinePackCorruptError/retry constant never raised. The lock itself misclassifies: `_try_native_lock` catches BlockingIOError, but fcntl LOCK_NB contention raises OSError EACCES/EAGAIN → falls to the pid-file fallback, where an empty lock file (the window between holder's flock and _write_pid) returns True → BOTH instances proceed. `download_offline_pack_with_resume` is also a ~198-line 6-concern method (BP-12 family).

**User Impact:** The 1508-line module reads as a complete subsystem; protections users assume (disk gate, lock, corruption retry) don't run; two app instances could both write the partial (rare, real).

**Root Cause:** Plan-§8 scaffold landed ahead of the (missing, BP-115) install wiring; lock exception mapping never checked against fcntl docs.

**Gain vs Trade-off:** Gain: honest module (wire the stages that matter to BP-115's flow, delete the rest per E15) + a correct lock. Trade-off: wiring decision couples to BP-115/BP-33.

**If We Do It:** The pack module does what it appears to do; concurrent pack downloads are actually exclusive.

**If We Don't:** The scaffold keeps implying safety stages that never run.

**My Recommendation:** ✅ Implement (wire disk-gate + lock + swap into BP-115's flow; delete metered/Authenticode stubs or implement; fix the BlockingIOError catch).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/offline_pack.py:480-507,591-759,797-871,1202-1336,311-329,1342-1416,228-235`

**Fix:** With BP-115: call the disk gate pre-download, take the lock around the partial (catch OSError EACCES/EAGAIN → return False), wire the swap; delete or implement the metered/Authenticode/queue stubs; slice the download method into phases (BP-12 pattern).

**Simplified Fix:** Half of the offline-pack module is finished-but-never-called safety machinery, and its "only one app at a time may download" lock fails open in a race — wire up the parts that matter and delete the pretend parts.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

**Enrichment (2026-09-04 BP session — Wave 5):** Two more zero-caller §8.x APIs for the scaffold list: `verify_offline_pack_signature_macos` (:1419) and the `OfflinePackFileEntry` type (:119).

### BP-117 — update_check's in-flight guard leaks registration on late failure: the version becomes un-retriggerable for the session
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_trigger_pack_download` adds the version to `_ACTIVE_PACK_DOWNLOADS` (update_check.py:578-585) BEFORE `dest.parent.mkdir()` and `thread.start()`. If either raises (disk full, permissions, thread exhaustion), the exception propagates — `_bg`'s finally never ran (the thread never started) — so every later trigger for that version logs "already in flight — skipping duplicate trigger" and returns False until restart.

**User Impact:** One transient failure (e.g. a full disk at the wrong moment) permanently blocks that version's pack auto-download for the whole session — silently.

**Root Cause:** Registration ordering (register-then-risk instead of risk-then-register).

**Gain vs Trade-off:** Pure improvement — discard on failure or register after start.

**If We Do It:** Transient failures retry on the next launch/trigger.

**If We Don't:** The stuck-registration trap persists.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/update_check.py:578-606,631-642`

**Fix:** Wrap post-registration setup in try/except that discards from `_ACTIVE_PACK_DOWNLOADS` on failure, or register only after `thread.start()` succeeds. Test: mkdir raises → second trigger proceeds.

**Simplified Fix:** If starting the engine-pack download fails at just the wrong moment, the app forever thinks the download is already running (for that session) and never retries — clean up on failure.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-118 — The pack download's HTTP response is never closed; resume accepts 200 where 206 is required
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_http_get_streaming` (offline_pack.py:1088-1123) captures the `urlopen` response in a closure with no `with`/`.close()`; non-200/206 and rate-limit raises leave it open, and mid-download aborts abandon the generator — the socket is freed only at GC. The sibling `_http_get_manifest` uses `with opener.open(...)` correctly — the two transports disagree. Separately, the resume path opens `"ab"` for ANY 200/206: a server/proxy ignoring Range and returning 200 with a full body APPENDS after the existing partial → guaranteed corruption (SHA-caught, but a wasted ~200 MB download).

**User Impact:** Socket/file-handle pressure on failures; the corrupt-append path wastes a full re-download behind proxies that strip Range headers.

**Root Cause:** Missing resource discipline + status-code contract not enforced on resume.

**Gain vs Trade-off:** Pure improvement (with-block + try/finally in the generator; require 206 when offset > 0 else restart with "wb").

**If We Do It:** Clean connection lifecycle; no silent corrupt-append.

**If We Don't:** Rare-but-real resource leaks and proxy-dependent corruption churn.

**My Recommendation:** ✅ Implement (fold into BP-115's download rework).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/offline_pack.py:1088-1123`

**Fix:** `with urlopen(...) as resp` + try/finally in the chunk generator; on resume require `status == 206` (else seek-0 restart with "wb"); 416-with-offset-equals-length → treat as complete (BP-115).

**Simplified Fix:** The big-file downloader never explicitly closes its network connection, and when resuming it accepts a full-file reply where only a partial was asked for — appending the whole file onto the half it already had.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-119 — The purge inventory in _paths.py carries two never-matching filenames and is production-dead
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_paths.user_data_subpaths_for_purge` (276-315) lists `crash_recovery.json` (the canonical file is `recovery.json`) and `onboarding.marker` (canonical: `.onboarding_status.json` etc.) — neither name matches any real file. The function has zero production callers (a cleanup test only); both this registry and `_user_data_files._USER_DATA_FILES` (the live one, consumed by config.purge_user_data) claim exhaustiveness.

**User Impact:** None today (dead function) — but any future consumer of the `_paths` list silently misses recovery + onboarding files on uninstall/factory-reset.

**Root Cause:** A forked registry that drifted before dying.

**Gain vs Trade-off:** Pure improvement — derive the file entries from the canonical constants (or delete the function).

**If We Do It:** One purge inventory, correct filenames.

**If We Don't:** The dead fork waits to be revived with its wrong names.

**My Recommendation:** ✅ Implement (prefer deletion; the live registry exists).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/_paths.py:276-315`
- `voice_typer/server/_user_data_files.py:139-166` (the live registry)

**Fix:** Delete `user_data_subpaths_for_purge` (re-point its test at the live registry), or rebuild it composing `_USER_DATA_FILES`' canonical names. Record deletion in archive/deleted_files.txt.

**Simplified Fix:** A leftover list of "files to delete on uninstall" names two files that don't exist and is used by nothing — delete it or rebuild it from the real list.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-120 — Pack/update micro-batch: payload drift, stale suppressions, disk-space re-implementation, unreachable backoff, facade layering, double-parse
**Status:** ❌ Not Fixed (investigation only)

**Description:** Seven verified small items in offline_pack/update_check: (1) E9 payload drift — Python emits `offline_pack_verified={version}` / `offline_pack_ready={worker_pid}` while renderer types require `{version, sha256}` / `{version, worker_pid}` (documented richer payloads not emitted); (2) `LOOPBACK_HOSTS # noqa: F401` re-export with zero importers + false comment; `# type: ignore[return-value]` (use cast/TypedDict); function-local imports of top-level-available modules; `__import__("contextlib").suppress`; (3) `check_offline_pack_disk_space` re-implements asr_utils' `_check_disk_space_for_download` while its docstring claims it wraps it (ENRICHES BP-26); (4) the rate-limit backoff tuple's 8.0 s entry is unreachable (loop raises at attempt 3 — docs promise 1/2/4/8); (5) `_hf_cache_cleanup` is a 3-layer facade whose docstrings claim consumers that no longer reference it (parakeet_engine has zero references) and asr_utils functions it no longer contains; (6) update_check's manifest is parsed twice and read three times (temp-file round-trip whose second read is discarded); (7) update_check carries a stale C-DATA-1 note ("user must extend category (3)/(4)" — done 2026-08-15).

**User Impact:** None directly — contract drift, doc rot, and micro-costs on the pack path.

**Root Cause:** Accumulated drift around the (missing) install stage.

**Gain vs Trade-off:** Pure improvement; most items fold into BP-115/116's rework.

**If We Do It:** The pack path's types, docs, and helpers agree with reality.

**If We Don't:** Micro-debt compounds exactly where BP-115's work will land.

**My Recommendation:** ✅ Implement (fold into BP-115/116).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/offline_pack.py:104,395,488,168,944-971,1167-1238`
- `voice_typer/server/service/update_check.py:43-47,444-471,466,684,850`
- `voice_typer/server/_hf_cache_cleanup.py:5-27`

**Fix:** (1) emit the documented fields or mark renderer fields optional; (2) import hygiene per item; (3) shared `ensure_disk_space` helper; (4) sleep-then-raise on attempts 1..N or drop the 8.0 entry + fix docs; (5) collapse the facade to direct asr_utils imports; (6) dict-level manifest validation, drop the temp-file round-trip; (7) update the note.

**Simplified Fix:** Seven small cleanups around the offline-pack downloader: mismatched event data, stale comments, a duplicated disk-space check, a retry delay that can never happen, a needless middleman module, a file read twice, and an outdated policy note.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-121 — The volume-backend status shown in Settings can never refresh (test-only parameter, no invalidation path)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `service/status.py:180-286` caches the volume-backend status; `_force_refresh` is passed by exactly one test file and zero production code; the registry has no `refresh_volume_backend` command; the renderer only displays the result. The cache's own docstring says "a separate task will add a refresh_volume_backend IPC command" — never landed (same failure class as BP-91).

**User Impact:** After the first successful ducking init, the backend name/availability shown in Settings → Audio is frozen for the process lifetime — the documented recovery case (installing pyobjc mid-session) can't surface without a restart. Ducking itself works; only the display is stale.

**Root Cause:** Cache landed with a designed invalidation seam that was never wired.

**Gain vs Trade-off:** Gain: status becomes refreshable. Trade-off: small new IPC surface (registry + handler + renderer type — E9 parity) OR a TTL OR invalidate in config apply.

**If We Do It:** The Audio settings row reflects reality within a refresh.

**If We Don't:** Frozen-until-restart display persists.

**My Recommendation:** ✅ Implement (TTL is the least-surface option; IPC refresh is the most useful).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/status.py:64,180-286`
- `voice_typer/server/handlers/status_handlers.py:111-122`

**Fix:** Options: (a) wire `refresh_volume_backend` IPC (registry + handler `_force_refresh=True` + renderer request type); (b) TTL mirroring `_OFFLINE_PACK_STATUS_TTL_S`; (c) invalidate in `ConfigApplier.apply_config_side_effects`. Pick per E5 — (b) is cheapest, (a) most explicit.

**Simplified Fix:** The settings page shows which audio-volume system it found — but only once per app run; if you install the missing piece it uses, the page won't notice until restart.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-122 — privacy.py's GDPR pipeline duplicates its walk/rmtree/error recipes 4× and 3× (with dead shadow branches)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_gdpr_rmtree_rust_logs` / `_gdpr_rmtree_db_dir` / `_gdpr_rmtree_electron_profile` / `_gdpr_rmtree_crash_archive` are four ~40-line copies of the same exists→rmtree→erased/failed/log shape (~150 LOC); `_gdpr_unlink_personal_files` / `_gdpr_unlink_personal_globs` / `_gdpr_post_cleanup_sweep` repeat the unlink+error-capture block; five `except PermissionError` branches have byte-identical bodies to the following `except Exception` (dead shadows); the engine-invalidation snippet is duplicated privacy.py:597-600 ↔ config_service.py:367-370.

**User Impact:** None directly — drift risk in the GDPR delete/export paths; the dead branches mislead readers into thinking locked-file handling differs.

**Root Cause:** God-class extraction copied recipes instead of extracting them.

**Gain vs Trade-off:** Pure improvement — one `_gdpr_rmtree_dir(config_dir, name, erased, failed)` + one `_safe_unlink`; collapse each PermissionError+Exception pair.

**If We Do It:** GDPR pipeline changes land once.

**If We Don't:** The sixth copy is one paste away.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/privacy.py:324-538,700-720,872-883,597-600`
- `voice_typer/server/service/config_service.py:367-370`

**Fix:** Extract the two helpers; route the four rmtree + three unlink sites through them; delete the dead PermissionError shadows; share the engine-invalidation helper.

**Simplified Fix:** The "delete my data" code has four copies of the same folder-removal recipe and three of the same file-removal recipe — one recipe each.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-123 — _GDPR_PERSONAL_GLOBS re-declares the 6-pattern inventory by hand beside the imported original
**Status:** ❌ Not Fixed (investigation only)

**Description:** privacy.py imports `_GDPR_PERSONAL_GLOBS` from `_user_data_files` as the inventory — and `_GDPR_PERSONAL_FILES` IS single-sourced — but the class's own GLOBS tuple re-declares all six inventory patterns (corrupt/pre-migration × bare/-wal/-shm) by hand, with comments admitting "mirrored from _user_data_files._GDPR_PERSONAL_GLOBS". A one-directional import-time assert is the only drift guard.

**User Impact:** None today — a quarantine-filename format change must be edited in two hand-written tuples; the assert catches only one drift direction.

**Root Cause:** Half-finished single-sourcing (FILES migrated, GLOBS left).

**Gain vs Trade-off:** Pure improvement — compose the tuple from the import + extras; the assert becomes deletable.

**If We Do It:** One authoritative glob list.

**If We Don't:** The two-tuple sync hazard persists.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/privacy.py:134-204,1164-1171`
- `voice_typer/server/_user_data_files.py:248-255`

**Fix:** `_GDPR_PERSONAL_GLOBS = _GDPR_PERSONAL_GLOBS_INVENTORY + (extra patterns…)` — preserves superset semantics; delete the hand copy and the one-way assert.

**Simplified Fix:** One list of "private file patterns" is copied by hand into a second file that already imports the first — build the copy from the import instead.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-124 — The symlink-poisoning directory check is duplicated verbatim across two modules
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_find_symlink_in_tree` exists byte-identically (including a function-local `import os`) in `service/_helpers.py:11-35` (consumer: the import_model path — `service/model/_delete_import.py:338`) and `config_internals/paths.py:512-536` (consumer: legacy migration). The mirror's docstring admits the copy exists to avoid a circular dependency (service._helpers sits under a package importing config). Both copies guard the same attack class (e.g. `legacy/models/qwen → ~/.ssh/id_rsa`).

**User Impact:** None today — a hardening fix (dangling-symlink handling, mid-walk permission errors) applied to one copy silently misses the other on two security-relevant paths.

**Root Cause:** Layering constraint forced a copy; the function only needs stdlib, so a stdlib-leaf home removes the constraint.

**Gain vs Trade-off:** Pure improvement — move to a leaf module, import from both sites.

**If We Do It:** The poison check is single-sourced and hardenable once.

**If We Don't:** Two copies keep guarding two attack surfaces independently.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/_helpers.py:11-35`
- `voice_typer/server/config_internals/paths.py:512-536`

**Fix:** Move the function to a stdlib-only leaf (e.g. `_fs_walk.py` or `_paths.py`), import from both consumers, delete both copies. ENRICHES BP-26.

**Simplified Fix:** The "make sure a folder doesn't secretly contain a link to somewhere dangerous" check is pasted into two places that each guard a different attack — share it.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-125 — Service-layer residue batch: broken docstring refs, stale mixin count, inert noqa markers, the _secrets shim, local imports
**Status:** ❌ Not Fixed (investigation only)

**Description:** Five verified residue classes in the service layer: (1) a mechanical session-tag strip left ~30 broken docstring references ("per  the rotating log", "( / ) ───" headers, dangling "see  for the rationale") + ~10 surviving task-ID references + a stale "eight domain mixins" count (11 actually composed); (2) stale/inert noqa markers — 2× F821 empirically proven stale (TYPE_CHECKING imports resolve them), 1× PLW0603 + 4× BLE001 referencing rules the project never selects; (3) a dead speculative compat path in `_gdpr_checkpoint_history_db` (26 lines; outer except unreachable; fallback for a signature that doesn't exist); (4) a 27-line "set_config REMOVED" comment block duplicated verbatim (already whitespace-drifted) in service/__init__.py and config_service.py; (5) `_secrets.py` back-compat shim re-exports 20+ private names to 25+ production consumers that were never migrated to security.*; plus ~20 function-local stdlib imports in privacy.py.

**User Impact:** None at runtime — docstrings instruct readers to consult references that aren't there, C-STYLE-1 forbids the surviving IDs, and the shim defeats "private means private".

**Root Cause:** Mechanical cleanup passes (tag strip, noqa adds, extraction) stopped at 80%.

**Gain vs Trade-off:** Pure hygiene; the _secrets migration is wide mechanical churn with zero behavior change (keep-or-migrate is a judgment call — noted for the user).

**If We Do It:** The service layer's docs and imports stop lying.

**If We Don't:** The residue keeps taxing every reader.

**My Recommendation:** ✅ Implement (items 1-4 + import hoisting); 🟡 Defer the _secrets shim migration (wide churn, zero behavior change — user's call).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/{privacy.py:117-206, config_service.py:4-250, status.py:4, __init__.py:11,53, _base.py:4-7, _download_helpers.py:64-230}`
- `voice_typer/server/service/privacy.py:288-313` (dead compat path)
- `voice_typer/server/_secrets.py:1-55`

**Fix:** (1) docstring repair pass over the 8 service files (fill/delete empty refs, fix the mixin count, strip surviving IDs — fold into BP-24); (2) delete the stale/inert noqas (empirically verified safe); (3) collapse the checkpoint compat path to one try/except; (4) keep the REMOVED block once (config_service.py); (5) _secrets: mechanical import rewrite then delete the shim, or trim private-name re-exports to test-only; hoist privacy.py's stdlib imports.

**Simplified Fix:** A tidy-up batch for the backend's service files: repair comments a previous cleanup pass broke, delete "ignore this warning" notes that suppress nothing, remove one dead compatibility branch and one duplicated 27-line note, and decide what to do with an old forwarding module. (Cross-ref: BP-25 — the noqa-consolidation theme.)

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-126 — The autostart launcher imports the full app module on every login just to read a PID file
**Status:** ❌ Not Fixed (investigation only)

**Description:** autostart_launcher.py imports the full `voice_typer.server.app` orchestrator (169 marginal modules — sqlite3, ssl, http.client, the email chain, history_db, i18n, 5 app mixins…) merely to call PID/port helpers that live in the light `single_instance` module. Measured ~143 ms warm-cache; worse cold (AV-scan territory on Windows, W0-corroborated). The heavy import is load-bearing only because `_backend_pid_file()` resolves `_config_dir` through the app module at call time (a COMPAT-REFAC test-seam indirection). Paid on BOTH the fresh-start and focus-only login paths.

**User Impact:** Every login-run of the launcher (including the lightweight "focus the running app" path) pays a full backend-app import — measurable logon latency for a process that only needs a number from a file.

**Root Cause:** Test-seam indirection keeps the app import load-bearing (C-ARCH-2's documented anti-pattern).

**Gain vs Trade-off:** Pure improvement (import from single_instance + migrate its `_config_dir` seam to config) — C-ARCH-2-canonical direction.

**If We Do It:** Login-time focus runs get materially lighter (realistic warm saving ≈40–70 ms — importing `single_instance` directly still pulls ~97 modules transitively; the full ~143 ms win needs the two helpers in a truly light leaf beside `_paths`. Review Wave 6 calibration.)

**If We Don't:** Every logon pays the tax.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/autostart_launcher.py:328`, `voice_typer/server/autostart/pid_file.py:48`, `voice_typer/server/single_instance.py:216-217`

**Fix:** Import the helpers from `single_instance` directly; migrate its call-time `_config_dir` app-module resolution to `config._config_dir`. Related: BP-5's startup-wait family.

**Simplified Fix:** The little program that runs at login to wake the app loads the ENTIRE voice engine's code just to read an 8-digit process number — load only the small helper it needs.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-127 — The autostart interpreter-resolution recipe is copy-pasted across three platform registrars
**Status:** ❌ Not Fixed (investigation only)

**Description:** The "find the right Python to launch with" recipe (pythonw preference → venv probe → can-import check → warning → existence check → Tauri fallback) is duplicated verbatim ×3 across `server_platform/autostart.py:194-252`, `autostart_windows.py:240-305`, `autostart_macos.py:91-116`, with drift already visible between copies. This is exactly the mechanism class that produced C-CROSS-1's month-long Windows logon break (a fix in one copy, not the others).

**User Impact:** None today — a future interpreter-handling fix that lands in one copy leaves the other platforms silently broken at logon.

**Root Cause:** Platform registrars copy-pasted the shared resolution logic.

**Gain vs Trade-off:** Pure improvement (one shared `resolve_autostart_interpreter()`; Windows output shapes unchanged — C-CROSS-1/2 respected).

**If We Do It:** Interpreter fixes land once for all platforms.

**If We Don't:** The drift trap that already cost a month of broken logons stays armed.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/server_platform/{autostart.py:194-252, autostart_windows.py:240-305, autostart_macos.py:91-116}`

**Fix:** Extract the shared resolver (platform-agnostic core, platform-specific candidates injected); keep each registrar's output shapes byte-identical (C-CROSS-1/2 pins).

**Simplified Fix:** The "which Python should launch the app at login?" logic exists in three copies for three operating systems — make it one shared routine so fixes reach every platform.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-128 — The stale-Run-key cleanup loop skips the doubled-backslash check C-CROSS-4 mandates
**Status:** ❌ Not Fixed (investigation only)

**Description:** `_autostart_windows_runkey.py:115-171` (stale-entry sweep) re-implements `_validate_runkey_command` WITHOUT the raw-string doubled-backslash check: malformed legacy Run-key values pass `Path.exists()` (which collapses `\\`) and are never swept. NOT a would-conflict — routing the loop through the canonical validator EXTENDS C-CROSS-4's mandated check to the sweep path; no registration output shape changes.

**User Impact:** A malformed legacy Run-key entry (the exact class C-CROSS-4's self-heal exists to purge) survives the sweep forever if its path happens to exist after separator collapse.

**Root Cause:** The sweep was written before the validator gained the raw-string check; it re-implements the old shape.

**Gain vs Trade-off:** Pure improvement — the sweep uses the canonical validator.

**If We Do It:** The self-heal actually heals all malformed entries.

**If We Don't:** The one entry-shape the validator specifically catches is the one the sweep misses.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/server_platform/_autostart_windows_runkey.py:115-171` vs `autostart_windows.py:534-580`

**Fix:** Route the sweep's per-entry validity check through `_validate_runkey_command` (extend the existing tests for the sweep with a doubled-backslash fixture).

**Simplified Fix:** The cleanup pass that deletes broken "start at login" entries checks a weaker condition than the app's own broken-entry detector — use the real detector.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-129 — Prewarm-sync startup ceremony is a no-op; schtasks runs SYNCHRONOUSLY on the startup path (30 s ceiling)
**Status:** ❌ Not Fixed (investigation only) — third blocking site beyond BP-5's two

**Description:** (1) startup_tasks.py:224-242 + startup_sequence/_phases_late.py:339-427 spawn a dedicated daemon thread to call a DOCUMENTED STUB — the prewarm-sync ceremony is a no-op wearing a startup thread, and its every-startup INFO log ("Syncing prewarm task — triggers: boot + event") misdescribes both the deleted task and the live XML (actual: LogonTrigger). (2) task_scheduler.py:57-60's docstring claims schtasks runs "from a background thread" — it actually runs SYNCHRONOUSLY via phase-6 `sync_autostart` → `is_autostart_enabled` → `/Query` with a 30-second hang ceiling, ON the critical path BEFORE hotkey registration.

**User Impact:** Startup pays a synchronous `schtasks /Query` (fast normally, up to 30 s under system load — before the hotkey works), plus a no-op thread and a misleading log line on every boot.

**Root Cause:** Half-removed prewarm task ceremony + a docstring that describes an async design that was never wired.

**Gain vs Trade-off:** Gain: startup drops a subprocess round-trip and a dead thread; logs tell the truth. Trade-off: none identified (the sync_autostart path's checks must still run — the finding is the synchronous placement + no-op ceremony).

**If We Do It:** Hotkey registration no longer waits behind schtasks; boot logs are truthful.

**If We Don't:** Occasional 30 s-class startup stalls before the hotkey works.

**My Recommendation:** ✅ Implement (move the query off the critical path; delete the no-op ceremony; fix the docstring).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/startup_tasks.py:224-242`, `voice_typer/server/startup_sequence/_phases_late.py:339-427`
- `voice_typer/server/task_scheduler.py:57-60`

**Fix:** Delete the stub-calling ceremony + INFO line; move the `/Query`-dependent checks after hotkey registration (or background them with a completion event); fix the task_scheduler docstring. Related: BP-5 (same startup-blocking family).

**Simplified Fix:** At every startup the app spawns a thread to call an empty placeholder function and logs a description of a task that no longer exists — and a Windows scheduling query that can hang for up to 30 seconds runs before the dictation hotkey is even registered.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-130 — Autostart batch: dead pid-file port stub, launcher spawn recipe ×5, log-handle leak, whole-binary hashing
**Status:** ❌ Not Fixed (investigation only)

**Description:** Four verified items: (1) `autostart/pid_file.py:19-78` — `_read_ipc_port_from_pid_file` is a forward-compat stub: the writer never emits `port=`, the parse loop is dead-in-practice, the comment's claimed second-backend protection cannot fire, and docstrings cite "review.md MED-Y" which no longer exists; (2) the launcher spawn recipe exists ×5 (tauri_spawn.py:357-382, focus.py:64-87+98-126, electron_spawn.py:41-78+128-176, + siblings) with three divergent cleanup shapes — Rust-side twins filed as BP-79; (3) `electron_spawn._launch_electron_built` leaks its opened log-file handles on the Popen-failure path (no finally — its own comment warns about exactly this); (4) `tauri_spawn.py:314` reads the whole 10-40 MB Tauri binary into RAM to hash it (once per launcher run incl. focus-only) — chunked hashing is the documented practice.

**User Impact:** None directly — maintenance surface, a handle leak on a failure path, and peak-RAM spikes during login.

**Root Cause:** Copy-paste spawn recipes + premature forward-compat.

**Gain vs Trade-off:** Pure improvement (extract the spawn recipe; fix the leak with finally; chunk the hash; land-or-delete the port stub).

**If We Do It:** One spawn recipe; no handle leaks; flat memory during launcher verification.

**If We Don't:** The five recipes keep drifting.

**My Recommendation:** ✅ Implement (coordinate with BP-79/BP-22).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/autostart/{pid_file.py:19-78, tauri_spawn.py:314,357-382, focus.py:64-126, electron_spawn.py:41-176}`

**Fix:** (1) land the writer's `port=` half or delete the stub + comments; (2) extract the shared launcher spawn helper (cleanup shape unified); (3) try/finally the log handles; (4) 1 MB-chunk sha256.

**Simplified Fix:** A batch of login-program cleanups: a "read the port number" feature that was never finished, the same start-the-app recipe pasted five times, a file handle left open on one failure path, and a whole program read into memory just to compute its checksum.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-131 — Credential migration's lock-abort path marks secrets_migrated=True without migrating
**Status:** ❌ Not Fixed (investigation only)

**Description:** When the migration lock times out (`TimeoutError`, 5 s), `_migration.py:255-295` logs "ABORTING migration… The next launch will retry" — then writes `secrets_migrated=True` into config.json (:281-288). The flag gate (:360-361) makes the next launch SKIP: the log message is false. The write is an UNLOCKED read-modify-write of config.json performed right after failing to acquire the lock that exists to serialize those writes — a narrow interleaving can restore stale plaintext over a completed migration. The lock is shared with ordinary `Config.save()` (a >5 s save at startup aborts migration and permanently marks it complete). The docs contract says deferral must set `secrets_migrated_keyring_was_unavailable`, not the main flag. The test docstring is internally contradictory about whether config.json is touched.

**User Impact:** Plaintext API keys can persist in config.json forever with zero diagnostic and a warning promising a retry that never happens.

**Root Cause:** Abort branch wrote the success flag instead of the deferral diagnostic.

**Gain vs Trade-off:** Pure improvement — set the deferral flag so the retry actually happens (probes are rate-limited); no security weakening.

**If We Do It:** A lock conflict delays migration to the next launch (as the warning already claims).

**If We Don't:** The silent permanent-plaintext trap stays.

**My Recommendation:** ✅ Implement — fixes the documented contract, not just the message.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/credential_store/_migration.py:130-135,255-295,360-361`
- `docs/security/credential-store.md:106-114,199-202`

**Fix:** On lock-acquire failure: do NOT set `secrets_migrated`; set `secrets_migrated_keyring_was_unavailable=True` (existing diagnostic); align the warning text; fix the contradictory test docstring; add an abort-path regression test asserting the flag is NOT set.

**Simplified Fix:** If the app can't get the lock that protects the "move secrets into the system keychain" step, it logs "we'll try again next launch" but then marks the job as DONE — so it never tries again. Mark it as "deferred" instead.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-132 — auto_capitalize turns "may", "march", "august" into mid-sentence capitals (empirically reproduced)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The proper-noun single-word list capitalizes EVERY occurrence: "the plan may work" → "The plan May work"; "we march at dawn" → "We March at dawn"; "an august body" → "An August body". Empirically reproduced in-sandbox. The module's own contract says "we'd rather under-capitalize than mangle a common noun". Default-on (`auto_capitalize: bool = True`). No test pins the wrong behavior (tests cover only monday/july). Related ambiguity class: BP-48 (were/its) — and the same map also carries "ill"→"I'll" and "id"→"I'd" ("she felt ill" → "she felt I'll"; "enter your id" → "enter your I'd" — also reproduced).

**User Impact:** Users with the (default-on) enhancement get mid-sentence "May"/"March"/"I'll" injected into ordinary speech.

**Root Cause:** Month/modal names that double as high-frequency common words included as unconditional single-word capitals.

**Gain vs Trade-off:** Gain: no mangled sentences. Trade-off: date-words stop auto-capitalizing (they're the only members doubling as top-frequency words) — or context-gate them next to day-of-month numbers.

**If We Do It:** Ordinary speech passes through unmangled.

**If We Don't:** Every default user gets occasional capitalized modals.

**My Recommendation:** ✅ Implement (drop "may", "march", "august", "ill", "id"; coordinate with BP-48's fix in the same module).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/ai_enhancement.py:104-105,150-181,186-190,294-298`

**Fix:** Remove the five ambiguous entries (or context-gate month names next to digits); regression tests with the reproduced sentences.

**Simplified Fix:** The auto-capitalizer treats the words "may", "march", and "august" as always-proper-nouns — so "the plan may work" becomes "the plan May work". Remove the words that are usually ordinary words.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-133 — The cached LLM polisher keeps using the OLD OpenAI key after key rotation
**Status:** ❌ Not Fixed (investigation only)

**Description:** `LLMPolisher` is constructed once with the `api_key` snapshotted BY VALUE and cached on `app._llm_polisher`. The invalidation predicate (`config_applier.py:1187-1191`: `any(k.startswith("llm_"))`) covers llm_* fields, reset-to-defaults, and GDPR delete — but NOT `openai_api_key`, even though the polish path falls back to it when `llm_api_key` is empty. An `openai_api_key` rotation leaves the cached polisher authenticating with the revoked key until restart/llm_*/reset/GDPR. Cloud TRANSCRIPTION is immune (fresh engine per call); only the polish path caches.

**User Impact:** After a routine OpenAI key rotation, LLM polish keeps failing ("polish failed" toast) despite the new key being valid — a silently broken feature until restart.

**Root Cause:** Invalidation predicate narrower than the effective-key dependency set.

**Gain vs Trade-off:** Pure improvement — widen the predicate to the credential field set (or any `*_api_key` change).

**If We Do It:** Key rotations take effect immediately.

**If We Don't:** The silent broken-feature window persists after every rotation.

**My Recommendation:** ✅ Implement — one-line predicate + test.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/config_applier.py:1187-1191`
- `voice_typer/server/dictation_pipeline/enhancement_steps.py:236,266-274`, `voice_typer/server/llm_polish.py:146`

**Fix:** Widen the invalidation to the provider credential field set (import PROVIDER_TO_CONFIG_FIELD per BP-95's single-sourcing) or any `*_api_key` change; test: rotate `openai_api_key`, assert `app._llm_polisher is None`.

**Simplified Fix:** When you replace your OpenAI key in settings, the text-polish feature keeps using the OLD revoked key from its cached copy until the app restarts — refresh the cache when ANY provider key changes.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-134 — llm_polish uses deprecated max_tokens/temperature (rejected by OpenAI reasoning models)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The polish request payload sends `max_tokens: 1024` and `temperature: 0.3`. OpenAI has deprecated `max_tokens` in favor of `max_completion_tokens`, and reasoning models (o-series) hard-reject `max_tokens` and non-1 `temperature`. The default model (gpt-4o-mini) works today; the module advertises "any OpenAI-compatible API" and `llm_model` is user-configurable — pointing it at an o-series model makes every polish call 400. Groq/Ollama/vLLM still require `max_tokens`, so an unconditional swap breaks them.

**User Impact:** Users selecting a reasoning model get a silently failing (best-effort) polish.

**Root Cause:** Payload written pre-deprecation; no endpoint-aware parameter choice.

**Gain vs Trade-off:** Gain: forward compatibility with reasoning models. Trade-off: endpoint-conditional payload (small complexity) — or document the limitation.

**If We Do It:** Reasoning models work for polish; compatible endpoints unchanged.

**If We Don't:** The limitation is invisible until someone selects an o-series model.

**My Recommendation:** 🟡 Try and revert — choose the parameter by endpoint (api.openai.com → max_completion_tokens, temperature 1; others keep max_tokens); minimally, document the limitation in the module docstring.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/llm_polish.py:324-341`

**Fix:** Endpoint-aware payload selection + a clear error surface for rejected params; or document the supported-model constraint.

**Simplified Fix:** The text-polish request uses two parameters that newer OpenAI models reject outright — pick the right parameter per provider, or at least document which models are supported.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-135 — credential-store docs drift: the security runbooks probe the WRONG keyring service name
**Status:** ❌ Not Fixed (investigation only)

**Description:** `docs/security/credential-store.md` documents `KEYRING_SERVICE_NAME = "voice-typer"` — the actual service name is `"com.voicetyper.keyring"` (`_schema.py:36`, with legacy names migrated at :49). ALL THREE platform verification runbooks probe the legacy name (`secret-tool search service voice-typer` :274; `security find-generic-password -s voice-typer` :303; `cmdkey /list` Target `voice-typer:openai` :331) — an operator following the security runbook concludes keys are absent from the keychain. Also: stale single-file paths throughout the doc (the package split predates it), the API list omits `clear_in_memory_secrets`, pyproject cites the old path, and `_KNOWN_PROVIDERS == PROVIDER_TO_CONFIG_FIELD.keys()` makes the `delete_secret` orphan-cleanup loop production-inert (the historical-names registry was built but never populated).

**User Impact:** Security-audit runbooks actively mislead operators into thinking secrets aren't stored; maintenance readers follow stale paths.

**Root Cause:** The doc predates the keyring-name migration and the package split; the history registry was never populated.

**Gain vs Trade-off:** Pure docs/wiring improvement (update name + runbook commands + paths; populate-or-delete the inert loop — same built-but-inert class as BP-43/BP-52).

**If We Do It:** Runbooks find the actual keyring entries; the orphan loop either works or is gone.

**If We Don't:** Every future security audit starts from a wrong premise.

**My Recommendation:** ✅ Implement (docs part is user-relevant; loop is optional).

**Progress:** `None yet.`

**Related Files:**
- `docs/security/credential-store.md:86-88,131,274,303,331`
- `voice_typer/server/credential_store/{_schema.py:36,49,79, _crud.py:356-376}`

**Fix:** Update the service name + all three runbook commands + file paths; document the legacy cutover; populate `_KNOWN_PROVIDERS_HISTORY` with the names the migration actually stored (or delete the inert loop, keeping the test-pinned validation).

**Simplified Fix:** The security documentation tells operators to search the password store for the app under the WRONG name — so audits conclude the secrets are missing. Update the name and the search commands.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-136 — A test pins the REMOVED 3-arg RMS-callback contract via the echo-comment grep
**Status:** ❌ Not Fixed (investigation only)

**Description:** `tests/test_vad.py:710-726` (`test_recorder_callback_passes_three_args`) greps `inspect.getsource(recording)` for `rms_callback(chunk_rms, chunk_peak, filtered)` — which exists ONLY in the package `__init__.py`'s echo-comment block (:74 and :298 — Review Wave 6 second cite), not in the live code (the production call at audio_pipeline.py:991 is the 2-arg form with an invariant comment "Callers MUST now use the 2-arg signature"). The e2e_smoke companion still claims "run Silero VAD on the live stream" (false). A live arity regression at the real call site is swallowed by the DEBUG-only RMS-callback error suppression while these tests stay green; restoring the pinned 3-arg contract would reintroduce the native-rate→16 kHz Silero bias that BUBBLE-FIX-4.1 fixed. `tests/regressions/test_parakeet_merge.py::TestSourceCheck` greps the same echo block for suppression-logic strings.

**User Impact:** None directly — false end-to-end coverage claims; a future "fix" that trusts the test reintroduces a degraded-core waveform bug.

**Root Cause:** Source-inspection tests target the echo substrate instead of the owning module (the BP-13 pattern, now with a concrete contract-pinning instance).

**Gain vs Trade-off:** Pure improvement — tests pin the LIVE contract behaviorally.

**If We Do It:** Arity regressions at the real call site fail tests; the echo block can be deleted.

**If We Don't:** The pin guards a contract the production code explicitly forbids.

**My Recommendation:** ✅ Implement (with BP-13's fix).

**Progress:** `None yet.`

**Related Files:**
- `tests/test_vad.py:669-737`, `tests/test_e2e_smoke.py:106-142`, `tests/regressions/test_parakeet_merge.py:66-82`
- `voice_typer/server/recording/audio_pipeline.py:974-1015` (live contract), `voice_typer/server/recording/__init__.py:284-315` (echo)

**Fix:** Re-point the signature-pinning tests at `audio_pipeline` source (or better, behavioral: call the pipeline with a mock callback and assert the 2-arg invocation); rewrite the e2e docstring to the current contract; delete the dead `audio_chunk` params + stale echo lines. Cross-ref BP-13.

**Simplified Fix:** A test claims the audio engine calls a three-argument callback — but the code it inspects is a leftover comment, and the real code deliberately uses two arguments. The test is protecting a contract the app removed on purpose.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-137 — The Rust WS event allowlist silently drops 10 Python-published events; 3 documented consumer contracts are dead end-to-end
**Status:** ❌ Not Fixed (investigation only)

**Description:** A full 4-way population diff (48 Python-published event names vs Rust ALLOWED_EVENT_TYPES 61 / TS union 51 / KNOWN_EVENT_TYPES 51 / Python EVENT_TYPES 39) shows 10 published events dropped at the host gate with only a warn: `asr_backend_ready`, `asr_backend_load_failed`, `microphone_permission_revoked`, `download_stalled`, `cloud_fallback_used`, `dictation_suppressed`, `history_corrupted`, `history_fts5_rebuild_failed`, `microphone_disconnected`, `paste_deferred`. `_push_to_ws` forwards everything; reader.rs:188-197 drops non-allowlisted names. Three documented consumer contracts are dead end-to-end: the `set_config` ack's `model_loading` → "renderer shows a spinner and dismisses it on asr_backend_ready" (event never arrives); `microphone_permission_revoked`'s promised "distinct banner" (user gets a misleading silence toast instead); `tray_fallback_notification` passes the gate but has no subscriber or TS-union entry (delivered to nobody). The parity guard (`test_event_types_parity.py`) only tests the TS→Rust direction — the Python-published→Rust direction is unguarded, and Python's own EVENT_TYPES registry lacks 11 published names.

**User Impact:** Documented UX behaviors (model-loading spinner dismissal, permission-revoked banner, tray-fallback notification) silently never worked; occasional unknown-event warn noise on model switches (bounded: first occurrence + every 100th — Review Wave 6 calibration).

**Root Cause:** Event-name parity never asserted in the emitting direction; allowlist grown by consumer requests, not by emitter inventory.

**Gain vs Trade-off:** Gain: every published event either has a consumer or is deleted (E15); the blind parity direction gets guarded. Trade-off: 10 wiring/removal decisions to make.

**If We Do It:** No silently-dead event contracts; the guard catches the next name drift at CI.

**If We Don't:** The drop class that already bit once (tray_fallback_notification) keeps accumulating.

**My Recommendation:** ✅ Implement — fix the underlying contract per event (wire or delete), then extend the parity test to the Python-published→Rust direction.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/ws/event_protocol.rs` (ALLOWED_EVENT_TYPES), `src-tauri/src/sidecar/ws/reader.rs:188-197`
- Emitters: `voice_typer/server/model_manager/_change.py:766,823`, `recording_controller.py:551`, + 7 more (see W5-A4 report)
- `tests/test_event_types_parity.py` (guard gap)

**Fix:** Per dropped event: wire the consumer (TS union + KNOWN_EVENT_TYPES + allowlist + subscriber) or delete the emit (E15). Extend test_event_types_parity.py with the Python-published ⊆ Rust-allowlist assertion and EVENT_TYPES ⊇ published. Model-loading spinner: subscribe `asr_backend_ready` or remove the documented claim.

**Simplified Fix:** The desktop shell keeps a list of event names it will deliver to the window — but ten events the engine actually sends aren't on the list, so three promised behaviors (a loading spinner that clears, a permission banner, a fallback notification) have never once worked. Fix the list — and add a test comparing what the engine sends to what the list accepts.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🔴 High

### BP-138 — The consent_required push-event type is wrong for 3 of 4 emitters (the load-bearing key is missing from the type)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `push_events.ts` declares `ConsentRequiredEvent` as required `{provider, model, message}`, citing `service/model.py:596-605` — a file that no longer exists. Actual emitters: recording_lifecycle.py:146 emits `{consent_field}`; enhancement_steps.py:361 `{consent_field: "llm_polish_consent"}`; update_check.py:766 `{provider, scope, model, consent_field, message}`; model/_downloads.py:207 `{provider, model, message}` (the only match). The renderer (App.tsx:385-408) reads only `consent_field`.

**User Impact:** None today (the one consumer reads the field the type omits) — any new consumer trusting the type reads `undefined` for three "required" fields.

**Root Cause:** Type written from one emitter (since moved) and never re-derived.

**Gain vs Trade-off:** Pure improvement — retype from the emitter inventory (all-optional or a discriminated union) + pin with the ipc-types test.

**If We Do It:** The typed seam stops lying.

**If We Don't:** The next consent-surface consumer ships against a fiction.

**My Recommendation:** ✅ Implement (with BP-114(5)'s tightening work — that filed the loose direction; this is the wrongly-STRICT direction).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc/push_events.ts` (ConsentRequiredEvent)
- Emitters: `voice_typer/server/{recording_lifecycle.py:146, dictation_pipeline/enhancement_steps.py:361, service/update_check.py:766, service/model/_downloads.py:207}`

**Fix:** `data: {consent_field?: string; provider?: string; scope?: string; model?: string; message?: string}` (or discriminated union by emitter); update the provenance cite; extend the ipc-types parity test to pin field presence per emitter.

**Simplified Fix:** The type describing "consent needed" events demands fields most senders don't include and omits the one field the app actually reads — derive the type from what the senders really send.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-139 — The "Download Deps" UI branch and its command can never fire (phantom backend command + hardcoded deps_ok=True)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `useModelDownload.ts:464-496` calls `install_parakeet_deps` — a command that exists in NO backend layer (registry, Rust allowlist, TS ALLOWED_COMMANDS, PythonRequest union — zero server matches), with a test asserting its success snack. The UI gate (`depsOk === false`) never renders because `deps_ok` is hardcoded `True` for all engines since the torch-gate removal (2026-08-15).

**User Impact:** None — dead-end wiring + a false-green test.

**Root Cause:** The renderer flow outlived its backend command and its gating flag.

**Gain vs Trade-off:** Pure E15 removal (delete the flow, branch, test, and orphaned i18n keys) — or wire a real command through all §6.4 touchpoints.

**If We Do It:** No phantom contract in the models UI.

**If We Don't:** The dead wiring implies a dependency-install capability that doesn't exist.

**My Recommendation:** ✅ Implement (delete).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/models/useModelDownload.ts:464-496`
- `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx` (Branch 4)
- `voice_typer/server/service/model/_status.py:73,91,107`

**Fix:** Delete the installDeps flow + Branch 4 + its test + the `models.download.deps*` i18n keys if orphaned (E15; archive/deleted_files.txt).

**Simplified Fix:** The Models page contains a "Download Deps" button flow that calls a command no part of the app implements, behind a condition that is permanently false — remove the dead branch.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-140 — The mypy ratchet baseline is stale in the FAILING direction: the pre-push gate is red at HEAD (1074 live vs 969 floor)
**Status:** ❌ Not Fixed (investigation only — pre-existing debt, surfaced by this session's investigation)

**Description:** Running the repo's own ratchet with the pinned mypy 2.3.1: exit 1, total 969→1074 (+105: attr-defined 540→640, name-defined 128→129, arg-type 28→30; ~9 import-not-found are sandbox-env noise, ~96 structural are env-independent). Git-verified: the baseline was last reconciled 2026-09-1 (7672b9e3); the dictation_pipeline paste_step split + siblings landed 2026-09-03 (c56b44f3) without the documented "reconcile after landing" step. Consequence: `.pre-commit-config.yaml`'s pre-push mypy hook fails for every contributor at HEAD.

**User Impact:** Contributors' pre-push hook is red right now (before this session touched anything); the growth-blocking guarantee is breached.

**Root Cause:** A split landed without its baseline reconciliation step (the mypy-baseline discipline exists and was skipped).

**Gain vs Trade-off:** Gain: green pre-push gate + a floor that means something. Trade-off: ~100 attr-defined errors must be fixed at source (mixin attribute declarations — the same pattern prior reconciles used) before the floor is legitimately corrected; E13 forbids a bare regenerate.

**If We Do It:** Pre-push is green; the ratchet protects again.

**If We Don't:** Every contributor's pre-push fails; teams learn to ignore the gate.

**My Recommendation:** ✅ Implement — fix the ~100 new attr-defined errors at source (mixin attribute declarations), then reconcile the floor per the documented process. (Fix-existing session's job; recorded here as the actionable finding.)

**Progress:** `None yet.`

**Related Files:**
- `mypy-baseline.json`, `scripts/mypy_ratchet_check.py`
- `voice_typer/server/dictation_pipeline/*` (the 2026-09-03 split's unmixin-declared attributes)

**Fix:** Add the missing attribute declarations on the pipeline mixins (~96 errors), then update the baseline floor through the script's sanctioned reconcile path (fix-then-lower, never regenerate-to-hide).

**Simplified Fix:** The "type errors must not grow" guard has fallen behind the code by about a hundred errors, so the check developers run before publishing now fails even on a clean checkout — fix the new errors at their source, then update the guard properly.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🔴 High

### BP-141 — The pyrefly baseline is 35% fiction: 267 entries describe fixed errors while 173 new errors ride under the stale headroom
**Status:** ❌ Not Fixed (investigation only — pre-existing debt, surfaced by this session)

**Description:** CI's exact pyrefly invocation run live: 672 errors vs baseline 766. Position-independent diff: 267 baseline entries no longer occur (clusters exactly matching the mixin debt fixed 2026-09-01 without refreshing the pyrefly floor) while 173 live errors are absent from the baseline (native_hotkeys splits) — absorbed silently because the gate is live ≤ 766. File:line scan: 369 of 630 missing-attribute entries match exactly, 39 within ±3 lines, 222 drifted >3 lines, 1 symbol moved files entirely.

**User Impact:** None directly — the ratchet cannot catch new errors until ~94 more accumulate; the "known errors" count is inflated by 267 ghosts.

**Root Cause:** Same unreconciled-landing pattern as BP-140, in the silently-permissive direction.

**Gain vs Trade-off:** Gain: a floor that reflects reality. Trade-off: the 173 new errors must be fixed (or documented as false positives) before shrinking the floor — E13-compliant direction only.

**If We Do It:** New pyrefly errors surface immediately again.

**If We Don't:** The gate stays 35% fiction.

**My Recommendation:** ✅ Implement (fix the 173, then legitimately shrink the floor — the 267 describe errors already genuinely fixed).

**Progress:** `None yet.`

**Related Files:**
- `pyrefly-baseline.json`, `scripts/regenerate_pyrefly_baseline.py`

**Fix:** Fix/annotate the 173 new errors (native_hotkeys splits), then reconcile the baseline to the verified-live state via the sanctioned process. Related: BP-140 (the loud twin).

**Simplified Fix:** The second type-checker's "known issues" list contains 267 entries for problems that were already fixed and is missing 173 real new ones — the guard can't see new problems until nearly a hundred more pile up.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

### BP-142 — Python dead/stale public-API remainder batch (9 items, incl. a whole dead module with a false consumer claim)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The final census found: (a) `dependency_guard.py` (77 LOC) whole module production-dead — its docstring's claimed consumer (`rebuild-and-launch.ps1`) does not exist anywhere; (b) `model_registry.py` trio — `get_default_model_size` (0 refs), `get_user_selectable_model_names` (0 refs + a docstring that FALSELY claims it sources ALLOWED_USER_MODELS), `get_models_by_backend` (test-only + false "Used by the Models page" claim); (c) `asr_errors.py:100 HuggingFaceConsentRequiredError` never raised in production (the base class is raised at asr_utils.py:475 — the subclass would carry provider/scope; today HF consent events ship empty fields, half-breaking the typed envelope); (d) `tray_notifications.py:141 clear_notify_dedup_cache` (0 refs); (e) `audio_presets.py` display layer (`PRESET_INFO`/`ALL_PRESETS`/`get_preset_for_display`, ~32 LOC) dead while the module docstring claims "frontend fetches presets via IPC" — no such handler (server-side twin of BP-76's dead component); (f) `i18n.py:345 get_locale` test-only; (g) `event_bus.py:972 publish_sync` test-only (outside BP-52's block); (h) `branding.py:33-34 APP_URL/APP_REPO` test-only while update_check.py:113-114 re-hardcodes the repo URL; (i) `hotkey_spec.py:76 CANONICAL_MODIFIERS` __all__-only.

**User Impact:** None directly — ~160 dead LOC and contributor-trap docstrings (the BP-52 failure mode); (c) is a real wire-format gap (empty provider/scope on HF consent events).

**Root Cause:** E15 cleanup never ran over the long tail.

**Gain vs Trade-off:** Pure improvement (delete or wire; (c) prefer raising the subclass).

**If We Do It:** No dead public surface left with false claims; HF consent events carry their fields.

**If We Don't:** The census debt accumulates.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/{dependency_guard.py, model_registry.py:283,301,334, asr_errors.py:100, tray_notifications.py:141, audio_presets.py, i18n.py:345, event_bus.py:972, branding.py:33-34, hotkey_spec.py:76}`
- `voice_typer/server/asr_utils.py:475`

**Fix:** Delete the dead items (E15; archive/deleted_files.txt) fixing the false docstrings on the way; for (c) raise `HuggingFaceConsentRequiredError` at asr_utils.py:475; for (h) have update_check import APP_REPO.

**Simplified Fix:** Nine leftovers the final dead-code census found — including a whole module whose only "user" is a script that doesn't exist, and three functions whose documentation claims consumers they don't have.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-143 — Dead-code remainder: Rust csv_escape test-only twin; TS dead exports (semver.ts whole module) with NO dead-export guard
**Status:** ❌ Not Fixed (investigation only)

**Description:** (a) `src-tauri/src/commands/export.rs:290-295` — `csv_escape` is `#[allow(dead_code)] // test-only`: zero production callers (json_to_csv uses `csv_escape_into`); its only callers are 28 test sites. (b) TS: `renderer/src/lib/semver.ts` (45 LOC) is entirely production-dead (only its own test; docstring cites an About.tsx comparison that no longer exists client-side); `main/tray_available.ts:196 refreshTrayAvailableCache` (0 refs); `pages/onboarding/lib/constants.ts:47 ONBOARDING_MIC_TEST_DURATION_SEC=5` (0 refs — superseded by the fixed 10 s); `lib/utils/models.ts isModelActive` test-only. (c) Guard gap: the client has NO dead-export guard (noUnusedLocals covers locals only; biome has no unused-exports rule; no knip) — 184 no-consumer exports found, 4 real dead symbols slipped through.

**User Impact:** None directly — dead surface + the next dead export ships invisibly.

**Root Cause:** The long tail + a tooling gap the Python side already solved (test_dead_code_stays_removed.py).

**Gain vs Trade-off:** Pure improvement (delete + add a knip or vitest dead-export gate).

**If We Do It:** Both languages have dead-export tripwires.

**If We Don't:** The TS dead surface grows silently.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/commands/export.rs:290-295` (+ export_tests.rs)
- `voice_typer/client/src/renderer/src/lib/semver.ts`, `voice_typer/client/src/main/tray_available.ts:196`, `voice_typer/client/src/renderer/src/pages/onboarding/lib/constants.ts:47`, `voice_typer/client/src/renderer/src/lib/utils/models.ts`

**Fix:** Delete the listed symbols/modules (move csv_escape coverage onto csv_escape_into; record in archive/deleted_files.txt); add a knip (or vitest census) dead-export gate to CI.

**Simplified Fix:** One dead function kept alive by tests on the Rust side, a dead version-comparison module on the front end, and no automated check that would catch the next one — delete them and add the check.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-144 — The Python single-instance subsystem (841 LOC) is an Electron-only leftover missing from the decommission checklist
**Status:** ❌ Not Fixed (investigation only)

**Description:** `single_instance.py` + `_security_attributes.py` + `security/win32_dacl.py` (1,176 LOC across the three; 841 is single_instance.py alone — Review Wave 6 basis note) have exactly ONE production trigger: `ipc/entrypoint.py:403` `_single_instance_mutex = None if _tauri_sidecar else _ensure_single_instance(...)` — gated OFF in Tauri mode (the Rust side owns single-instance there via the tauri plugin). The subsystem is NOT on BP-22's decommission list. THREE symbols inside it ARE live in Tauri mode and need extract-first treatment: `_is_pid_alive` (tray_window.py:69), `_backend_pid_file` (autostart_launcher.py, autostart/pid_file.py), and `_clear_backend_pid_file` (the shutdown/atexit path). (Corrected in Review Wave 6 — the original filing's `_write_backend_pid_file` is NOT live: its call sites are all inside the gated functions; consumers reference the PID *file/constant*, never the function.)

**User Impact:** None — dead-in-Tauri-mode code maintained as if live.

**Root Cause:** The decommission checklist enumerated the launcher/main-process surfaces; the Python-side single-instance subsystem was never inventoried.

**Gain vs Trade-off:** Pure improvement (checklist addition + extract-first before deletion; ~841 LOC removed at cutover).

**If We Do It:** The BP-22 cutover deletes a complete, inventoried subsystem instead of leaving orphaned modules.

**If We Don't:** The cutover leaves 841 lines of orphan + risk of deleting live helpers accidentally.

**My Recommendation:** ✅ Implement (append to BP-22's checklist with the two extract-first helpers + `shutdown/teardowns/electron.py`).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/{single_instance.py, _security_attributes.py, security/win32_dacl.py}`
- `voice_typer/server/ipc/entrypoint.py:403` (the gate)

**Fix:** BP-22 checklist addition — extract-FIRST the three LIVE-in-Tauri-mode symbols: `_is_pid_alive` (tray_window.py:69), `_backend_pid_file` (autostart_launcher.py:328-330, autostart/pid_file.py:48-50), and `_clear_backend_pid_file` (shutdown/cleanup.py:351, shutdown/teardowns/pid_file.py:33 via atexit, shutdown/lifecycle.py:348) to a runtime-neutral leaf; then delete the subsystem + `shutdown/teardowns/electron.py` at cutover (E15; archive/deleted_files.txt). (Correction from Review Wave 6: the originally-listed `_write_backend_pid_file` is NOT live in Tauri mode — its only call sites are inside the gated functions; deleting it with the subsystem is safe.)

**Simplified Fix:** The old "only one copy of the app may run" system on the Python side is switched off in the new runtime but was never put on the retirement list — add it, after moving out the two helpers that are still used.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-145 — `make typecheck` is fail-open: a bare `wait` masks all three gate failures
**Status:** ❌ Not Fixed (investigation only)

**Description:** Makefile:69-73 runs `npm run typecheck &`, `mypy_ratchet_check.py &`, `ruff check … &`, then bare `wait`. POSIX `wait` with no arguments returns 0 regardless of the children's exit codes (empirically verified). The repo's own `build_tauri_all.sh:223-234` uses the correct fail-closed pattern (`wait -n || ANY_FAIL=1` + per-PID collection).

**User Impact:** tsc/mypy/ruff failures print in interleaved output but `make typecheck` exits 0 — a false-green dev gate that undermines E1 wiring verification for anyone driving the loop via make. This is not hypothetical: the mypy ratchet is RED at HEAD right now (BP-140) and `make typecheck` masks it today.

**Root Cause:** Parallelization added without exit-code aggregation.

**Gain vs Trade-off:** Pure improvement (reuse the build script's proven pattern).

**If We Do It:** `make typecheck` fails when any check fails.

**If We Don't:** The dev gate keeps lying.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `Makefile:69-73` (vs `scripts/build/build_tauri_all.sh:223-234` — the correct pattern)

**Fix:** Capture PIDs and `wait "$PID" || FAIL=1` per child; exit non-zero at end (mirror build_tauri_all.sh Phase 1a).

**Simplified Fix:** The make target that runs three code checks in parallel then waits for them reports SUCCESS even when the checks failed — the wait command it uses ignores their exit codes. Collect them properly.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### BP-146 — The three ratchet scripts are ~250 copy-pasted lines of the same skeleton (one copy already diverged)
**Status:** ❌ Not Fixed (investigation only)

**Description:** `ruff_ratchet_check.py`, `mypy_ratchet_check.py`, `coverage_ratchet_check.py` share `_env_path`/`_display_path`/`_load_baseline`/`_format_table`/`compare`/`regenerate` (metadata-preserve, refuse-to-regrow, `--force`) and the argparse shape — ruff↔mypy ~200 identical lines, coverage ~80 more. Drift already present: coverage lacks the `BASELINE_PATH` env redirection its siblings have (tests can't redirect its baseline to temp); its argparse description carries task-ID residue ("XS-86: coverage ratchet comparison script." — user-visible in --help); mypy's hard-failure diagnostic prints a literal `{proc.returncode}` (missing f-prefix).

**User Impact:** None directly — every gate-hardening fix must be triplicated; one copy is already diverged.

**Root Cause:** Three sessions each cloned the ruff script.

**Gain vs Trade-off:** Pure improvement (extract `scripts/_ratchet_common.py`; scripts/ is already a package).

**If We Do It:** Gate fixes land once; the f-string and residue fixed.

**If We Don't:** The trio keeps drifting.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `scripts/{ruff_ratchet_check.py, mypy_ratchet_check.py:116, coverage_ratchet_check.py}`

**Fix:** Extract the shared skeleton (baseline load/validate, refuse-to-regrow regenerate, table, argparse builder) into `_ratchet_common.py`; fix the f-prefix + task-ID residue; add the coverage BASELINE_PATH redirection.

**Simplified Fix:** The three "don't let errors grow" scripts are near-copies of each other, and one has already drifted — build the shared core once.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-147 — ~3,800 lines of one-off session tooling are dead inside scripts/
**Status:** ❌ Not Fixed (investigation only)

**Description:** `scripts/append_review.py` (1,118), `append_review_findings.py` (1,891), `update_review_status.py`, `update_review_statuses.py`, `update_review_entry_status.py`, `enumerate_review_entries.py`, `extract_review_range.py`, `apply_remaining_fixes.py`, `fix_general_settings_section.py` + review_entries.json/review_range.json — zero references across Makefile, workflows, docs, pre-commit, husky, package.json, RELEASING/CONTRIBUTING/AGENTS. They hardcode sandbox-absolute paths (`/home/z/my-project/voice-typer/review.md`) and embed session payload (EC-25 prose, 2026-08-25 status text); pyproject's per-file-ignores itself calls append_review_findings.py "a one-off Phase-3 review-compilation tool". Second tier (live but unreferenced/undocumented): chunk_gate_driver.py (the C-TEST-6 chunk protocol, hardcoding sandbox paths + .venv), package_changes.py, build_changes_zip.py, regenerate_pyrefly_baseline.py, gen_caption_glyph_paths.py.

**User Impact:** None directly — contributors/agents can't tell live tooling from residue inside the lint-covered scripts/ tree.

**Root Cause:** E15 cleanup never ran on session tooling after their sessions closed.

**Gain vs Trade-off:** Gain: ~3,800 lines removed + a scripts/ tree where everything is live or documented. Trade-off: none — the one-offs are reproducible from review.md if ever needed.

**If We Do It:** scripts/ contains only live, referenced tooling.

**If We Don't:** The residue keeps taxing triage.

**My Recommendation:** ✅ Implement (delete the one-off cluster; document or scratch/-move the second tier).

**Progress:** `None yet.`

**Related Files:**
- `scripts/{append_review.py, append_review_findings.py, update_review_status.py, update_review_statuses.py, update_review_entry_status.py, enumerate_review_entries.py, extract_review_range.py, apply_remaining_fixes.py, fix_general_settings_section.py}`
- `pyproject.toml` (the per-file E501 ignore for a dead file)

**Fix:** Delete the one-off cluster + JSON payloads (E15; archive/deleted_files.txt) AND remove the `scripts/append_review_findings.py` exemption entries in `tests/test_product_namespace_consistency.py:43,91` (a live test reference — Review Wave 6 addition); drop the dead per-file ignore; document the second tier (docstrings + a scripts/README or relocation).

**Simplified Fix:** Almost four thousand lines of helper scripts from finished work sessions — review-list editors, status updaters, one specific settings-page fixer — sit unused in the tools folder. Delete them.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-148 — Makefile drift batch: setup missing the lock file, lint scope narrower than CI, test-fast marker fiction, bench heredoc
**Status:** ❌ Not Fixed (investigation only)

**Description:** Four verified items: (a) `make setup` installs `-e ".[test,dev]"` only, while the canonical dev loops (AGENTS.md/CONTRIBUTING) also install `-r requirements-lock.txt` (hash-pinned base deps — pip-audit --require-hashes depends on it); (b) `make lint`/`format`/`typecheck` run ruff on `voice_typer/ tests/` omitting `scripts/ conftest.py` — the AGENTS.md pipeline and the ruff ratchet use the wider scope → local-clean/CI-red skew; (c) `make test-fast` filters `-m "not slow and not integration"` but `integration` is registered NOWHERE (0 hits) and `slow` is already skipped by default via conftest — the target's documented semantics are mostly fiction (real delta: `--timeout=30`); (d) `make bench` is a ~500-char single-line Python heredoc with a hand-maintained script list duplicating bench/'s contents.

**User Impact:** Contributors using make get an unpinned env, a narrower lint scope than CI, and targets whose documented semantics don't match behavior.

**Root Cause:** Makefile written once; the canonical scopes evolved without sync.

**Gain vs Trade-off:** Pure improvement (sync all four).

**If We Do It:** make-based dev matches the documented pipeline exactly.

**If We Don't:** The local/CI skew keeps generating false-clean runs.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `Makefile:45-47,55-56,61-67,69-73,81-83`

**Fix:** (a) add `-r requirements-lock.txt` to setup; (b) add `scripts/ conftest.py` to the ruff invocations; (c) drop `not integration` and re-document test-fast; (d) move the bench driver to a small glob-based script.

**Simplified Fix:** Four make-target fixes: the setup target skips the pinned dependency list, the lint target checks fewer files than CI does, a "fast tests" option filters by a marker that nothing uses, and the benchmark target is a giant one-liner.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-149 — Build tooling batch: set -e defeats exit-code contracts, sync_versions reformats package.json, pyproject matrix comment drift, publish templates stale
**Status:** ❌ Not Fixed (investigation only)

**Description:** Four verified items: (a) `build_tauri_all.sh` Phase 1c/1b run cargo/npm in a subshell then `BUILD_RC=$?` — under `set -euo pipefail` (line 37) the failing subshell aborts the script BEFORE the capture (empirically proven), so the documented exit-code contract (3 = cargo build failed) and the ERROR diagnostic are skipped; the gate still fails (fail-closed holds), only the contract/diagnostics break; (b) `sync_versions.py --apply` full-re-serializes package.json with 2-space indent while the file on disk is TAB-indented (biome's format) — every version bump churns the whole file's indentation (format ping-pong); its docstring also claims CHANGELOG.md syncing that doesn't exist; (c) pyproject's load-bearing "intentional divergence" matrix comment says `.python-version | 3.12.7` while the actual pin is 3.13.7, and classifiers stop at 3.13 while the declared window is 3.10-3.14; (d) `publish_pack_release.py`'s ASSET_NAME_TEMPLATES document §10.1-era names contradicting the canonical §11.9 `artifact_names.py` module (documentation-only, but it's the in-repo naming reference for a live script). MANIFEST.in also omits CODE_OF_CONDUCT.md.

**User Impact:** None directly — broken diagnostics on failure paths, noisy release diffs, a comment future agents rely on for tool decisions now lying, and a stale naming reference.

**Root Cause:** Scripts evolved; contracts/comments didn't follow.

**Gain vs Trade-off:** Pure improvement (wrap phases in `|| { …; exit N; }`; targeted version-field write; comment/classifier sync; derive templates from artifact_names).

**If We Do It:** Failure paths diagnose correctly; release diffs are minimal; the matrix comment tells the truth.

**If We Don't:** The failure-path contract stays broken and the doc drift compounds.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `scripts/build/build_tauri_all.sh:37,261-332`
- `scripts/build/sync_versions.py:9-11,94-100`
- `pyproject.toml:48-62,689-727`, `.python-version`
- `scripts/release/publish_pack_release.py:95-105` vs `scripts/build/artifact_names.py`

**Fix:** (a) `|| { echo; exit N; }` wrappers (Phase 1a's pattern); (b) targeted regex version-field write + docstring fix; (c) update the matrix row + add the 3.14 classifier; (d) import/reference artifact_names in the publisher; add CODE_OF_CONDUCT.md to MANIFEST.in.

**Simplified Fix:** Four build-tool fixes: a script whose error codes can't fire, a version bumper that reformats a whole file, a comment that misstates the pinned Python version, and a naming reference that predates the real naming module.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-150 — The cloud-provider "Testing…" toast is hardcoded English
**Status:** ❌ Not Fixed (investigation only)

**Description:** `hooks/models/useCloudProviders.ts:247` sets `message: "Testing…"` (rendered at CloudProvidersPanel.tsx:364) while every other branch in the same hook uses `t("models.test.*")`. The `models.test` family exists in all 8 locales but has no `testing` key. The literal bypasses t() entirely, so missing-key tooling can't see it.

**User Impact:** Non-English users see an English "Testing…" toast for the whole network-probe duration, in every locale.

**Root Cause:** One branch missed the i18n pass.

**Gain vs Trade-off:** Pure improvement — add `models.test.testing` to all 8 locales (genuinely translated) + the t() swap.

**If We Do It:** The toast is localized everywhere.

**If We Don't:** One English string leaks into 7 locales.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/models/useCloudProviders.ts:247` (render: `components/models/CloudProvidersPanel.tsx:364`)

**Fix:** Add `models.test.testing` to all 8 locale files (real translations per C-I18N-2); swap the literal for `t("models.test.testing")`. Related family: BP-72/BP-108.

**Simplified Fix:** The "Testing…" message shown while checking a cloud provider connection is hardcoded in English while every neighboring message is translated — add the key and translate it.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-151 — Renderer remainder micro-batch: false cross-tab claim, toast dispatch ×3, TFn type ×8, unvalidated theme-draft parse
**Status:** ❌ Not Fixed (investigation only)

**Description:** Four verified items: (1) `useSessionStorage.ts:13-17,60-73` — the cross-tab sync listener is dead code (sessionStorage storage events do NOT fire across tabs/windows — MDN-verified) with a docstring claiming the sync exists; (2) `useSnackbar.ts:136-149,202-215,254-267` — the toast.success/error/warning/info switch duplicated ×3 verbatim (E7); (3) the `TFn` translate-function type is re-declared ×8 across 7 hooks + lib/errors (the same family as BP-27's CallFn ×15 — unenumerated there); (4) `theme-draft-storage.ts:47` — unvalidated `JSON.parse as CustomThemeData` (second site of BP-111's unvalidated-cache class).

**User Impact:** None directly — dead listener, drift surface, and a corrupted custom-theme draft can crash a consumer (suspected path).

**Root Cause:** Mechanical duplication + an aspirational comment that outran the platform.

**Gain vs Trade-off:** Pure improvement (delete the dead listener + fix the comment; one dispatchToast helper; shared TranslateFn — which also feeds BP-73's work; per-entry validation).

**If We Do It:** One toast dispatcher, one TFn, honest docs, validated drafts.

**If We Don't:** Micro-debt persists in the hooks layer.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/{useSessionStorage.ts:13-73, useSnackbar.ts:136-267}` + 7 hooks (TFn)
- `voice_typer/client/src/renderer/src/lib/theme-draft-storage.ts:47`

**Fix:** (1) delete the storage-event listener + correct the docstring; (2) extract `dispatchToast`; (3) shared `TranslateFn` in i18n (feeds BP-73); (4) per-entry validation mirroring BP-111's fix.

**Simplified Fix:** Four small front-end fixes: a sync feature that the browser doesn't actually support, three copies of the same toast-dispatch code, the same type declared eight times, and saved theme data loaded without checking it's valid.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-152 — axe-core wiring scans page roots in empty/stub states only
**Status:** ❌ Not Fixed (investigation only) — test-infrastructure wiring (Group 1), not an a11y finding

**Description:** `a11y/axe-core.test.tsx` scans all 10 pages in EMPTY/stub states (ConfirmDialog stubbed null :315-317). The renderer-wide axe-consumer inventory is exactly 6 files. No full axe pass exists for: ConsentGateDialog (the GDPR gate), HelpOverlay, ShareStatsDialog, the app shell (Sidebar/TitleBar/GlobalSearchBar), populated page states, or the non-default Settings sections. Behavioral name/role tests exist, but not the full rule-set.

**User Impact:** Heading-order, nested-interactive, and name-computation violations on unscanned surfaces ship silently; the suite's "all pages" claim is true only for empty states.

**Root Cause:** The a11y harness covered the mount states; the interactive surfaces never got scans.

**Gain vs Trade-off:** Gain: full-rule coverage of the dialogs/shell/populated states. Trade-off: test runtime (+scans) — the existing renderApp+fixtures helpers make the additions cheap.

**If We Do It:** The a11y suite's coverage matches its claim.

**If We Don't:** Unscanned surfaces stay unverified.

**My Recommendation:** ✅ Implement (scans for dialog-open states, shell, one populated page, Settings section cycle).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/a11y/axe-core.test.tsx`

**Fix:** Add scans via the existing renderApp+fixtures helpers: ConsentGateDialog open, HelpOverlay open, ShareStatsDialog open, app shell, one populated page (History with rows), one Settings section cycle.

**Simplified Fix:** The automated accessibility checker only examines pages in their empty state — never the dialogs, the sidebar, or pages with data in them. Extend the checks to the states users actually see.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### BP-153 — test-setup clears localStorage but not sessionStorage
**Status:** ❌ Not Fixed (investigation only)

**Description:** `test-setup.ts:24-30` afterEach clears localStorage only. Page filter state now lives in sessionStorage (`vt:filters:*` via useFilterState), consumed by 4 pages and exercised by several test files. Cross-file order is safe (vitest isolate) but intra-file order-dependence is latent — the setup file's own header says the centralized cleanup exists to prevent exactly this drift.

**User Impact:** None — a latent test flake class for default-filter-state assertions.

**Root Cause:** The cleanup predates the filter-state move to sessionStorage.

**Gain vs Trade-off:** Pure improvement (one line).

**If We Do It:** Filter-state assertions can't inherit earlier tests' state.

**If We Don't:** The latent flake waits for a stateful test pair.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/test-setup.ts:24-30`

**Fix:** Add `sessionStorage.clear()` to the afterEach.

**Simplified Fix:** The test cleanup empties one browser storage area but not the one the app now actually uses for filters — clear both.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-154 — The release-mode sidecar's event channel is never drained after the handshake (pipe back-pressure deadlock class)
**Status:** ❌ Not Fixed (investigation only)

**Description:** tauri-plugin-shell 2.3.5 (exact Cargo.lock version, primary-source verified) creates the process event channel as bounded `channel(1)` with backpressure; two pipe-reader threads park inside `tx.send(...).await` when it's full, and the wait-thread's `Terminated` send parks too. The host drains the receiver only during the handshake, then stores it in `state.child_exit_rx` untouched until shutdown. Dev-mode is immune (stderr inherited). Post-handshake stderr beyond the pipe buffer (64 KB Linux / ≤65,535 B Windows) blocks the sidecar's writer threads: ctranslate2/onnxruntime device dumps on model load (which occurs AFTER server_started), torch/Python warnings, idle-unload reload cycles (BP-30 amplifier), and crash tracebacks over a long session can exceed it. The first event drained at the 30 s exit wait is typically a stale Stderr line → the "unexpected event" arm force-kills instead of exiting cooperatively — squandering the WAL-checkpoint window (enriches BP-32's story).

**User Impact:** Latent: sidecar threads blocked mid-stderr-write; if the GIL is held, heartbeat misses → respawn (recovery works, so the visible symptom is an occasional unexplained restart). Graceful shutdown degrades to force-kill after stderr-heavy sessions.

**Root Cause:** The handshake loop's receiver was parked in state instead of being permanently drained.

**Gain vs Trade-off:** Gain: no post-handshake back-pressure class; graceful exit works after verbose sessions. Trade-off: a permanent drain task (small) or stderr-to-file redirection (mirrors the Python side's RACE-009 pattern).

**If We Do It:** Long sessions with verbose engines stay healthy; shutdown stays cooperative.

**If We Don't:** The latent deadlock class and the force-kill-on-exit degradation persist.

**My Recommendation:** ✅ Implement (drain task after handshake — loop recv(), log Stderr at debug, Terminated → supervisor respawn path).

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/spawn/release_mode.rs:99-105,293-304` → `spawn.rs:219-223` → `state.rs:133`
- `src-tauri/src/sidecar/shutdown.rs:114-143` (the only drains)

**Fix:** Spawn a permanent drain task for `child_exit_rx` after the handshake (log Stderr lines at debug; Terminated routes to the supervisor respawn path). Alternative: redirect sidecar stderr to a file. VALIDATE ON WINDOWS HOST (pipe semantics).

**Simplified Fix:** After the background engine finishes saying hello, the app stops reading its error-output pipe — if the engine later writes more than the pipe can hold, its writes block and it can hang. Keep a small reader running for the app's whole lifetime.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-155 — The rotating logger has an unbounded queue and a per-WARN flush barrier with no timeout
**Status:** ❌ Not Fixed (investigation only)

**Description:** `platform/logging/rotating.rs:81` uses a std unbounded `mpsc::channel()`; `:125-137` `flush()` blocks on `ack_rx.recv()` with NO timeout; `combined.rs:194-195` fires that barrier for every record at WARN or above — from ANY thread, including tokio workers (reader/writer/heartbeat/supervisor), the Tauri event loop, and the panic hook (which fires while panic-point locks are still held). Senders never block, so a wedged writer means grow-only memory; every warning-logging thread hangs on the barrier until the writer drains the whole queue and completes a flush syscall.

**User Impact:** Normal case: a cross-thread barrier + write syscall per warn/error (the Rust-side analog of Python's BP-51 logging-cost class). Pathological case (stalled disk — roaming profile, sync-watched config dir, full disk, AV): every warning-logging thread hangs indefinitely and error paths stall async tasks.

**Root Cause:** Flush protocol without a timeout; command channel unbounded.

**Gain vs Trade-off:** Gain: bounded worst-case + no cross-thread barrier cost per warn. Trade-off: a timeout makes flush best-effort (log the miss) — acceptable for a log sink.

**If We Do It:** Warning logging can never wedge the runtime's threads.

**If We Don't:** The pathological hang class stays.

**My Recommendation:** ✅ Implement (`recv_timeout` + best-effort fallback; coalesce barriers; bound the channel).

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/platform/logging/{rotating.rs:81,125-137, combined.rs:194-195, panic_hook.rs:109}`

**Fix:** `recv_timeout` on the ack (best-effort fallback with a missed-flush log); at most one flush barrier per N ms (coalescing); bounded channel with drop-oldest. Cross-ref BP-51 (same class, other language).

**Simplified Fix:** Every warning from any part of the desktop shell waits for the log-writer thread to finish saving — with no timeout. If the disk stalls, every thread that logs a warning freezes too. Add a timeout and stop stacking duplicate wait requests.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### BP-156 — Dialog/export oneshot bridges await with no timeout
**Status:** ❌ Not Fixed (investigation only)

**Description:** `commands/export.rs:106-116` and `commands/system_cmds/dialogs.rs:124-131` await the tauri-plugin-dialog callback bridge's oneshot with no timeout. If the callback never fires (window destroyed mid-dialog, plugin edge case), the async command future parks forever — the renderer's `invoke()` promise never settles.

**User Impact:** Rare: a dialog action whose window closes underneath it leaves a dangling promise (the dialog UI is gone anyway).

**Root Cause:** Bridge written without the failure leg.

**Gain vs Trade-off:** Pure improvement (generous timeout → `{"canceled": true}`).

**If We Do It:** No permanently-parked command futures.

**If We Don't:** The rare leak persists.

**My Recommendation:** ✅ Implement (cheap hardening; W5-A8 flagged it as arguably plugin-guaranteed — implement defensively anyway).

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/commands/export.rs:106-116`, `src-tauri/src/commands/system_cmds/dialogs.rs:124-131`

**Fix:** Wrap each `rx.await` in a generous timeout (e.g. 10 min) resolving to the canceled response.

**Simplified Fix:** Two spots wait forever for a file-dialog answer that might never come — add a long timeout that treats silence as cancel.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-157 — Cold start pays serial build-before-bind plus repeatable per-boot costs (hotkey works late)
**Status:** ❌ Not Fixed (investigation only — audited 2026-09-04, no code changed)

**Description:** Three verified boot-ordering costs stack on every cold start. (1) Build-before-bind: `voice_typer/server/ipc/entrypoint.py:417` constructs the full `VoiceTyperApp()` (all builders) BEFORE the WS server is serving, even though a ws-startup thread (`entrypoint.py:509-515`) already runs `app.start()` concurrently — the Rust host blocks up to 30 s waiting for `server_started` (`src-tauri/src/util.rs:95` `SERVER_STARTED_TIMEOUT_MS`), so the whole construction window is user-visible dead time. On the Rust side, the one-time Electron→Tauri migration and the sidecar spawn run serially inside one spawn task (`src-tauri/src/main.rs:328-339`; comment at `:270-273` documents the ordering intent). (2) Repeatable per-boot costs: `parse_ipc_args()` (`entrypoint.py:157`) calls `importlib.metadata.version("voice-typer")` at `:182-188` on EVERY boot just to feed the `--version` action (`:217-218`), although `:190` already carries a `"1.0.0"` fallback; the Silero VAD preload is spawned from TWO sites (`startup_sequence/_phases_early.py:172-198` spawns `_vad_preload_worker` → `vad.preload()` at `:198`, and `app_recording_init.py:171-179` spawns a second preload in the recorder-init path); stale backup/`.tmp` sweeps (`startup_sequence/_maintenance.py:81+`, wired into the phase-2 path per `_phases_early.py:7`) run synchronously before ready although nothing downstream needs a swept directory. (3) Hotkey-after-mics: `app.hotkeys.register()` (`_phases_late.py:548`) sits BEHIND the mic task's 5.0 s budget (`_phases_late.py:473-479` — `("mic", _mic_task, 5.0)`), so on a machine with a hung audio stack the dictation hotkey is dead for 5 s; the late-mic recovery path already exists (`startup_tasks.py:585-613` pushes `microphones_changed` at `:613`), so nothing requires mics to finish first.

**User Impact:** Every cold start — the path users judge the app by — is slower than the architecture requires: the host stares at a non-responsive backend through construction + migration + sweeps, and in the worst case the hotkey stays dead through a 5 s mic timeout. None of this breaks anything; it is pure avoidable latency on the highest-visibility path.

**Root Cause:** Build-then-serve ordering (construct everything, then announce), eager per-boot work that could be lazy/background, and a hotkey registration ordered after a slow I/O task whose late result already has a dedicated event.

**Gain vs Trade-off:** Phase 1 (below) is pure latency wins with in-codebase precedent for every move; Phase 2 (bind-before-build) is the big win but touches the host↔sidecar handshake, so it ships behind a flag with the current order as fallback. Explicit NON-goal: joining the Tauri migration with the sidecar spawn via `tokio::join!` — the migration writes the config the sidecar reads, so a join needs merge-newer-wins analysis for a win that only exists on one-time first launch; NOT worth the race surface. (Distinct from BP-5's Windows-shortcut/launcher-sleep sites and BP-129's synchronous `schtasks` probe — cross-reference; and from BP-21, which instruments startup but does not reorder it.)

**If We Do It:** The backend announces `server_started` in ~ms, the host connects immediately, the hotkey registers before any audio I/O, and construction/migration/sweeps/preload resolve concurrently behind a live UI.

**If We Don't:** Cold start keeps paying serial construction + migration + sweeps + double preload on every boot, and the hotkey keeps waiting out the mic timeout on sick audio stacks.

**My Recommendation:** ✅ Implement — Phase 1 unconditionally (all-safe), Phase 2 behind a one-release flag.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/ipc/entrypoint.py:157,182-190,217-218,417,509-515`
- `voice_typer/server/sidecar_ws.py:526` (`_emit_server_started`)
- `src-tauri/src/main.rs:270-273,328-339`, `src-tauri/src/util.rs:95`
- `voice_typer/server/startup_sequence/_phases_early.py:7,172-198`
- `voice_typer/server/startup_sequence/_maintenance.py:29,78-81+`
- `voice_typer/server/startup_sequence/_phases_late.py:473-479,548`
- `voice_typer/server/app_recording_init.py:171-179`
- `voice_typer/server/startup_tasks.py:585-613`

**Fix:** Phase 1 (safe, do first): (a) lazy `--version` — only call `importlib.metadata.version()` when `--version` is actually in `argv`, else keep the `"1.0.0"` placeholder; (b) delete ONE VAD-preload spawn site (keep the phase-1 one; confirm both fire per boot with a spawn-counter test first — if the recorder-path one only fires on first-record, keep both and close this sub-item); (c) move the stale backup/`.tmp` sweeps to a fire-and-forget daemon thread after `ready` (nothing reads a swept dir before then); (d) move `app.hotkeys.register()` BEFORE the mic task — late mics already arrive via the existing `microphones_changed` push (`startup_tasks.py:613`), which is the recovery path by design. Add a migration sentinel (skip when the Electron source dir is absent AND a `migration.done` marker postdates it) so repeat launches skip the scan in ~ms — keep serial order, no join. Phase 2 (flagged): bind the WS listener + emit `server_started` FIRST, construct `VoiceTyperApp()` on the existing ws-startup thread path, buffer pre-ready frames (small cap; the existing 5 s auth timeout bounds the window), late-bind dispatch; keep the current order behind a flag for one release; supervisor respawn stays the fallback. Do NOT touch the C-WS-1 ready-first contract or `wait_for_auth_ok` strictness; no new crates; C-LOG-1/C-LOG-2 line formats unchanged.

**Simplified Fix:** Announce "I'm alive" before doing the heavy lifting, stop redoing one-time work on every boot, load the hotkey before poking the audio stack, and only preload the voice model once.

**Implementation Difficulty:** 🟡 Medium (Phase 1 🟢 Easy; Phase 2 needs the handshake flag + buffer)
**Severity:** 🟡 Medium

### BP-158 — Every model-availability check re-stats the disk: no shared is-downloaded verdict (5 s caches paper over it)
**Status:** ❌ Not Fixed (investigation only — audited 2026-09-04, no code changed)

**Description:** At least four independent code paths answer the same question — "is model X fully on disk?" — by re-walking the snapshot directory every time. The service layer keeps a 5 s TTL cache (`service/model/_status.py:17-28`, `TTL_SECONDS = 5.0` at `service/model/_constants.py:8`) whose per-repo probe `is_model_snapshot_complete()` (`_status.py:64-82`) stats the directory on every miss; the tray keeps a SECOND 5 s cache (`tray_models.py:42`) in front of `_check_hf_model_downloaded` (`tray_models.py:167`, with qwen/parakeet wrappers at `:221/:234`); the tooltip tick re-checks through `compute_tooltip` (`tray_publish.py:61`, cache-check comment `:125-135`, tick re-entry `:171-173` + `:234`) on a 1 s tick (`tray_state.py:203-208`, `tray_elapsed_timer.py:4-5,40-41,51-63`). The 5 s TTLs do not fix the shape: N consumers × every-5-s expiry = sustained redundant filesystem walks forever, plus a known-stale window (a download finishing inside the TTL still reads "not downloaded" for up to 5 s) and double maintenance of two caches that can disagree. Explicit invalidation already exists on the mutation paths (`service/model/_downloads.py:814` and `:1007` call `invalidate_model_availability_cache()`; `service/model/_delete_import.py:15-31` wraps the same call with a failure log, invoked on delete at `:123-125`) — but it only clears ONE of the two caches.

**User Impact:** Constant low-grade disk/CPU churn on every running instance (worst on Windows Defender-scanned profiles and HDDs); up to 5 s stale "not downloaded" badges/tooltips right after a download completes; two caches to keep coherent on every future change.

**Root Cause:** No single authoritative `is_available(repo_id)` verdict — each consumer grew its own stat-the-disk check with its own TTL band-aid, and invalidation covers only the service-side cache.

**Gain vs Trade-off:** One shared module replaces both TTL caches and all direct probe call sites; filesystem mtime (1 stat call) replaces directory walks on the hot path; explicit invalidation (already proven on the mutation paths) becomes authoritative instead of advisory. The 5 s TTLs disappear — correctness comes from mtime + invalidation, not expiry. Risk is a missed invalidation path (a mutation that bypasses the known delete/download/import sites) — mitigated by keeping the probe itself as the slow path and adding an adversarial test that mutates behind the cache's back.

**If We Do It:** Availability checks cost one `stat()` when nothing changed; download-complete reflects instantly everywhere (service, tray, tooltip); one cache to maintain.

**If We Don't:** Perpetual redundant I/O on a 1 s tick, stale badges after every download, and the next consumer adds a third cache.

**My Recommendation:** ✅ Implement (with staging: shared module first, consumer migration second, TTL deletion last).

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/model/_status.py:17-28,64-82`
- `voice_typer/server/service/model/_constants.py:8`
- `voice_typer/server/tray_models.py:42,167,221,234`
- `voice_typer/server/tray_publish.py:61,125-135,171-173,234`
- `voice_typer/server/tray_state.py:203-208`, `voice_typer/server/tray_elapsed_timer.py:4-63`
- `voice_typer/server/service/model/_downloads.py:811-814,1004-1007`
- `voice_typer/server/service/model/_delete_import.py:15-31,123-125`

**Fix:** (1) New single-source module (e.g. `voice_typer/server/model_availability.py`) exposing `is_available(repo_id) -> bool`: stat the repo dir mtime (1 call); on (mtime equal AND monotonic age < 300 s) return the cached bool; else run the full `is_model_snapshot_complete()` probe and store (bool, mtime, now). While a download_id is active for the repo, return False WITHOUT probing (prevents a mid-download "available" flash; cleared by the existing completion invalidation). (2) Migrate call sites in order: service `_compute_model_status` per-repo checks → tray `_check_hf_model_downloaded` body → tooltip/precheck paths; DELETE the tray-side TTL cache, keep one store. (3) Tooltip tick split: compute the static portion (model label, hotkey, i18n strings) once at RECORDING start / config change / locale switch (locate the locale-switch invalidation path and hook it — must-verify step), and let the 1 s tick append only the mm:ss elapsed; throttle the Tauri publish to every 5th tick or on state change. Keep the returned `{downloaded, deps_ok}` shapes identical; C-LOG-1/C-LOG-2 formats unchanged; no IPC shape changes. Tests (hermetic, no disk walks of real snapshots): fake repo dir + controlled mtime + call-counting probe stub proving 100 ticks → 1 probe; shared-counter test proving service + tray consult ONE store; in-progress-download override test; adversarial test (mutate dir behind the cache with unchanged mtime → still correct via invalidation path); tooltip test (10 ticks → 1 `compute_tooltip`).

**Simplified Fix:** One shared "is this model fully downloaded?" answer with a cheap freshness check, instead of every corner of the app re-scanning the disk every few seconds — and the clock display stops recomputing the whole tooltip every second.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### BP-159 — Dashboard re-fetches all six IPCs (incl. a 500-row history sample) on every single dictation
**Status:** ❌ Not Fixed (investigation only — audited 2026-09-04, no code changed)

**Description:** `refreshData()` in `voice_typer/client/src/renderer/src/pages/dashboard/hooks/useDashboardData.ts:153-197` `Promise.all`s SIX IPCs on every refresh — `get_config` (`:157`), `get_history` with `limit: DASHBOARD_SAMPLE_LIMIT` (`:158-162`, limit 500 at `:55`), `get_history_count` (`:169`), `get_status` (`:174-176`), `get_correction_usage` (`:182-184`), `get_model_status` (`:196`) — and it runs on EVERY `transcription_final` and `history_changed` event (subscriptions at `:387-388`, 500 ms debounce at `:367`, stale-while-hidden flag at `:338-343`). So each completed dictation re-pulls the full 500-row history sample (each row carrying a ~500-char preview per `types/ipc/history.ts:24`) plus config + status + model-status, when the only thing that actually changed is: one new row, one higher count, and possibly the correction counters. Config, model-install state, and backend status cannot change as a result of a dictation finishing — re-fetching them per keystroke-completion is pure waste, and the 500-row re-serialization dominates the refresh on large histories.

**User Impact:** Per-dictation UI jank that grows with history size (500 preview-bearing rows re-fetched, re-serialized, and re-processed into day buckets on every single transcription); wasted backend work (config read + model-status filesystem stats + status snapshot per dictation); the cost is invisible on small histories and linearly worse on large ones — exactly the long-term-user regression profile.

**Root Cause:** One `refreshData` serves both mount (needs everything) and event refresh (needs the delta) with no hot/cold split; the event path reuses the mount path verbatim.

**Gain vs Trade-off:** The hot path shrinks to ~3 cheap IPCs + a 10-row prepend in the common case; every fallback (count mismatch, malformed delta, any error) reuses the existing full `refreshData` unchanged, so the worst case of the new code is byte-identical to today. No backend changes, no IPC shape changes, no visual changes. (Related: BP-28's renderer micro-batch covers different sites — onboarding round-trips, export pagination, AudioContext; no overlap. The background-refresh dedup helper proposed elsewhere composes with this: helper owns WHEN, this owns WHAT.)

**If We Do It:** Per-dictation refresh becomes O(1)-shaped (10 rows + count + corrections) with the full fetch reserved for mount, config changes, and genuine history rewrites.

**If We Don't:** Dashboard refresh cost stays proportional to history size on every dictation, forever.

**My Recommendation:** ✅ Implement.

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/dashboard/hooks/useDashboardData.ts:55,93,150-199,260,288,338-351,367,387-388`
- `voice_typer/client/src/renderer/src/types/ipc/history.ts:24`

**Fix:** Split `refreshData` into mount vs event paths. Event path (`transcription_final` / `history_changed`): `Promise.all([get_history({limit: 10}), get_history_count(), get_correction_usage()])`; if `count === prevCount + 1` AND the 10-row head's first id is unseen, prepend `delta.slice(0, 500 - prevList.length ? 1 : 1)`… precisely: prepend the single new row to the cached sample array (cap at `DASHBOARD_SAMPLE_LIMIT`), set count, merge correction snapshot — no other IPCs. Cold getters (`get_config`, `get_model_status`, `get_status`) move to mount + the existing `config_changed` subscription only. ANY mismatch (count jump ≠ +1, empty delta, head-id already present, any throw) → fall through to the current full `refreshData()` verbatim (keep it as `refreshDataFull`). Keep the 500 ms debounce, the stale-while-hidden flag, and all empty-state semantics untouched. Tests (vitest, mocked `window.python`): event with count+1 → asserts `get_history` called with `{limit: 10}` and `get_config`/`get_model_status`/`get_status` NOT called, list prepended + capped; count jump +2 (import/restore path) → full refresh fired; malformed delta → full refresh fired; corrections card updates from the hot path alone.

**Simplified Fix:** When a dictation finishes, the dashboard fetches just the new row instead of re-downloading the whole history, settings, and model state — the heavy fetch only runs when something heavy actually changed.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

## 🚫 E. Cannot Verify (needs real host)

**19 findings require Windows / macOS / Linux desktop runtime** — they cannot be
verified or fixed on this Linux CI sandbox and must be validated on real hosts
(see `docs/migration/windows-validation-runbook.md`,
`docs/migration/macos-validation-runbook.md`,
`docs/migration/linux-validation-runbook.md`). These items are unverifiable, not
unfixable: re-check them on real hardware before marking anything done.

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

### XPLAT-12 — Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: ❌ Not Fixed — VALIDATE-ON-WINDOWS-HOST: Windows-on-ARM host validation required — Nuitka cross-compile + aarch64 freeze must be tested on real Windows ARM hardware
> - **2026-08-24 audit:** scaffold inert BY DESIGN — C-CI-4 gates the matrix leg (no public windows-11-arm runner; manual dispatch only per ADR-0020 §15). Action requires ARM hardware + explicit policy change; never enable blindly.
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.
- **Effort**: 🔴 **HIGH** — Requires Windows-on-ARM runner access not available in this sandbox. Cannot complete.

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
