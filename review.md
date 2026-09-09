## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

> **Won't Fix tasks live in `WONT_FIX.md`** — deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

### T-1 ΓÇö TAURI-E2E ΓÇö Full-application validation mission (GOAL MODE: zero problems)

**Status:** ≡ƒƒí Partial ΓÇö IN PROGRESS (2026-09-02 local Windows-host session): full pytest+vitest+cargo suites GREEN on the final state (14165/3683/501, 0 failed); tray status_change WS delivery, ws-mode sidecar app.start, tray Models/Microphone rebuilds (TR-1/2/3) verified landed; headless checklist suite (20 tests) green in the full run; recording_level live-level transport fixed end-to-end; notify AUMID registration added so Windows toasts are attributed correctly. Browser-driven visual walkthrough + real-model dictation on the Tauri host remain the open manual-verification phase (VALIDATE ON WINDOWS HOST ΓÇö this session ran focused/E2E-checklist evidence, not a full interactive GUI drive). **FV session 2026-09-07 (ON LINUX sandbox):** browser-mode renderer walkthrough executed via headless browser + injected mock bridge (contract mirrors tauri-bridge python-namespace): 15+ phases driven ΓÇö app shell, sidebar navigation, theme toggle, help overlay, bubble window, Settings (search/appearance/privacy/language), full onboarding flow incl. consent + language steps; 107 evidence artifacts (screenshots/snapshots/console logs) under .tmp-evidence/; the ONLY console errors are the expected pre-bridge "Python bridge not available" degradation warnings ΓÇö the renderer works in a plain browser and degrades cleanly. Console logs show only the expected pre-bridge degradation warnings plus one triaged Vite-HMR transient (a ReferenceError during concurrent App.tsx live-editing, caught by the ErrorBoundary and recovered on hot update ΓÇö not a final-state defect). Browser-Use install per ┬º2.3 could not be verified in-sandbox; the built-in agent-browser CLI was used (degraded-mode note in worklog.md under ## Degraded Mode). Full-suite green evidence for the EXACT final code state is the session's final delivery gate (worklog.md ## Validation Performed). REMAINING: interactive GUI walkthrough + real-model dictation on the Tauri host ΓÇö VALIDATE ON WINDOWS HOST.

> **TAURI ONLY ΓÇö NOT ELECTRON.** The Electron shell is being removed in the future; Tauri becomes the main (and only) runtime. Every problem must be found and fixed **in the Tauri shell**. Problems that exist only in Electron are OUT OF SCOPE and must NOT be chased. When comparing behavior ("it works in Electron but not in Tauri"), use Electron only as a behavioral reference, then fix the TAURI side.
>
> **Environment reality:** this task runs in a cloud sandbox ΓÇö no visual window, no desktop user session, but full terminal access + a controllable browser + vision (screenshot analysis). Where a human would click a switch with a mouse, the agent must TRIGGER the same action through the terminal, through code, through E2E tests, or through the browser. Every triggered action is verified either programmatically (config/state assertions, logs) or visually (screenshot + vision analysis). For every feature touched: if no test exists (E2E, unit, or golden), CREATE one and leave it in the test suite.

#### The mission

Run the **Tauri application** with the **full Python backend (sidecar) and everything else**, latest version, and test **literally everything in the application**, like a normal new user would ΓÇö then like a power user. Use every feature available. Anything that doesn't look right, isn't clean, doesn't work, doesn't do what it's supposed to do (even without throwing an error), has unclean logs, fake/misleading messages, errors, warnings, or failing tests ΓÇö **fix it immediately**.

The application also runs in a normal browser (the renderer is served on localhost). Launching it in the browser and using it there is part of this mission ΓÇö **if the app does not work in the browser, that itself is a problem that must be fixed.**

**Known broken areas to start from (already documented ΓÇö see TR-1, TR-2, TR-3 above):** tray "Models" sub-menu (dash item + "More Models" dead), Microphone page completely empty, tray menu missing the "Microphone" item. Fix these as part of this mission.

#### Checklist (exhaustive ΓÇö and the list is NOT exhaustive: anything found beyond it is also in scope)

1. **Run the app** like a normal user: Tauri host + full Python sidecar, latest version, everything healthy (logs clean).
2. **Onboarding:** go through the ENTIRE onboarding as a brand-new user. Every step, every screen. Fix anything that breaks, hangs, misleads, or looks wrong.
3. **Models:** from onboarding or the Models page, download **`Whisper Tiny`** (~75 MB ΓÇö small, so it downloads fast). Then use it: perform real transcription end-to-end and verify it works 100%.
4. **Recording & dictation:** full recording test ΓÇö start, pause, Escape-cancel, stop; everything related to recording and everything that happens to the recording AFTER dictation (paste, cleanup, history write). Verify with the model that transcription of the recording works.
5. **Templates:** open the Templates page, add templates, USE them (insert via dictation flow), verify output correctness.
6. **Vocabulary:** add custom vocabulary, use it in dictation, verify replacements come out correctly.
7. **Database:** verify things are actually persisted (history, templates, vocabulary, settings) ΓÇö survive restarts; fix any save/load problems.
8. **Clipboard:** test the clipboard/paste path end-to-end; fix problems.
9. **History page:** verify dictation/recording history is displayed correctly; fix the microphone issues there; test the filters.
10. **Microphone page + filters:** fix the empty page (TR-2); test every microphone quality/filter preset ΓÇö Advanced, Noisy Room, Studio, Auto ΓÇö all of them.
11. **Settings pages ΓÇö test EVERYTHING on every settings page** (General, AI & Audio, Appearance, Privacy ΓÇö every page, every control). Specifically named items (the list is not exhaustive):
    - **Launch at Login** (autostart): toggle on ΓåÆ verify it works; toggle off ΓåÆ verify.
    - **Fast Startup:** test it works.
    - **Notifications:** test once with notifications OFF, once ON ΓÇö verify both states behave.
    - **Tray Click:** test both modes ΓÇö click opens the app window vs. click starts dictation immediately.
    - **Bubble Behavior:** test the bubble end-to-end ΓÇö shows, works, no problems.
    - **Bubble Position:** top center, bottom center, etc. ΓÇö verify each position actually applies.
    - **Dictation hotkey:** verify it works; test hotkey VALIDATION ΓÇö try changing the dictation key to Caps Lock and other keys; fix any validation problems.
    - **Recording mode:** test `tap to record` and related modes.
    - **Stop on silence:** test with MULTIPLE option values, not just one.
    - **Paste key, Escape cancel, auto-paste,** and every other recording-related keybinding: test all of them.
    - Every other switch/toggle/field on every settings page: on, off, verify state actually changes and persists.
12. **Analytics:** perform dictations and verify the Analytics page numbers actually move/update; test sharing status; fix what's broken.
13. **Search:** perform searches on EVERY page that has search; verify results and behavior.
14. **Punctuation cheat sheet:** open it, verify content/behavior.
15. **Export/Import:** export and import History, Templates, Vocabulary ΓÇö verify round-trips are correct.
16. **Logs:** at the end, read the full session logs ΓÇö unclean logs, fake/misleading messages, spurious warnings/errors: rewrite and fix them.
17. **Everything else in the application** not listed above: test it too.

#### Tools & method

- **Terminal:** trigger features by code/IPC/test-harness when no GUI is available; inspect state, config, and database directly; run the existing test suites.
- **Browser (Browser Use ΓÇö see override below):** drive the renderer UI on localhost like a real user (navigate, click switches, fill forms), take screenshots, and use vision to VERIFY what changed (e.g. "the switch is really off", "the page really shows my mic").
- **Tests:** any feature or fix without a test gets one (E2E, unit, or golden ΓÇö whatever fits). Tests stay in the repo.
- **Green gate:** per AGENTS.md C-TEST-6 ΓÇö no claiming success without the full-suite green runs on the final code state.

#### BROWSER TOOL OVERRIDE ΓÇö BROWSER USE ONLY. (NON-NEGOTIABLE)

> The sandbox's built-in browser is DEPRECATED for this task. Before any website or browser-driven action, install **Browser Use** (`browser-use/browser-use`, with the self-healing `browser-use/browser-harness` recovery layer) per ┬º2.3, register its skill, **READ the registered skill documentation**, and route every browser-driven action of this task through it. The built-in browser is a last-resort fallback only after the ┬º2.3 retry procedure fails ΓÇö and if that happens, the run is logged as DEGRADED MODE in `worklog.md`, never silently substituted.
>
> **┬º2.3 Browser Use installation (mandatory, once per session):**
> 1. Run the official quickstart instruction, following it exactly: install or upgrade browser-use to the latest stable version with uv using Python 3.12, run `browser-use skill install` to register the skill, and connect it to the browser.
> 2. After registration, **READ the skill documentation that was installed** (its usage guide/skill files) before driving any browser task through it ΓÇö know how to operate it, not merely that it exists.
> 3. If setup or connection fails: follow the recovery steps at `https://github.com/browser-use/browser-harness/blob/main/install.md` (the self-healing harness built for exactly this), then retry setup ONCE.
> 4. If it still fails after that retry: fall back to the sandbox's built-in browser for the session, log it in `worklog.md` under `## Degraded Mode` with the exact failure reason, and continue. Do not stall the run over tooling ΓÇö but never claim nominal mode when running degraded.
> 5. Resource discipline while connected (sandbox has ~4GB RAM, no elevated privileges): headless mode always; close/release each browser context before starting the next check; never hold more concurrent contexts than strictly required; prefer sequential processing within each sub-agent's slice.
> 6. From the moment Browser Use is connected, EVERY navigation, form interaction, extraction, and behavioral task simulation in this task goes through it ΓÇö not the sandbox's native browser primitives.

#### Definition of done (the GOAL)

- Every checklist item above: exercised, verified, and passing.
- Every problem encountered: FIXED immediately, with a test left behind.
- Logs: clean (no fake messages, no spurious warnings/errors).
- Full test suites: green on the final code state (C-TEST-6).
- The Tauri application behaves correctly for a normal user from onboarding through daily use ΓÇö production-ready.
- Findings and fixes recorded in `worklog.md` / this file.

### CI-1 ΓÇö Fix all GitHub Actions CI pipeline errors and warnings

**Status:** ΓÜá∩╕Å Partial (FV session 2026-09-07, static audit ON LINUX sandbox): GP-66 + GP-70 edits verified landed (commit 91990ee8, user-approved C-CI-2 override); all action pins audited at Node-24 majors (checkout@v5, setup-python@v7, setup-node@v7, upload-artifact@v6, download-artifact@v6, setup-uv@v7, cache@v5, attest-build-provenance@v4, rust-toolchain@v1 ΓÇö no Node-20-era pins remain). REMAINING: a validated full CI re-run (manual dispatch, green) ΓÇö VALIDATE ON HOST; not executable from the sandbox.
**Description:** The CI pipeline contains several known issues: GP-66 (macOS binary-existence hard-fails instead of skipping), GP-70 (no `codesign --verify` step in Tauri macOS workflow), plus broader concerns such as `actions/upload-artifact@v5` / `setup-uv@v6` that run on deprecated Node.js 20 (hard-fail imminent when GitHub removes Node 20), potential secrets-not-set causing silent skip of signing steps, and drift between `build.yml` (Electron) and `tauri-*-build.yml` workflows. The full set of failures can only be determined by running each workflow end-to-end and inspecting logs.
**User Impact:** Red CI blocks merges and masks real regressions; unsigned binaries ship to SmartScreen; Node 20 deprecation will cause hard failures when GitHub drops it (expected late 2026).
**Root Cause:** Individual CI configurations were written at different times for Electron and Tauri targets; action pins were not kept in lockstep across workflow files; signing/secrets gates were added piecemeal without full end-to-end validation.
**Progress:** Partial ΓÇö GP-66, GP-70 documented; GP-65 (--sign flag) fixed.
**Related Files:**
- `.github/workflows/build.yml`
- `.github/workflows/tauri-windows-build.yml`
- `.github/workflows/tauri-macos-build.yml`
- `.github/workflows/tauri-linux-build.yml`
- `.github/workflows/tauri-build.yml`
- `.github/workflows/client-ci.yml`
- `.github/workflows/codeql.yml`
- `scripts/build/build_tauri_all.sh`

**Fix:** 1) Audit every workflow for pinned action versions ΓÇö replace any running Node 20 (`upload-artifact@v5`, `setup-uv@v6`, etc.) with their Node 24 majors (`upload-artifact@v6`, `setup-uv@v7`, etc.). 2) Fix GP-66 (replace `exit 1` with a skip pattern on missing binaries). 3) Fix GP-70 (add `codesign --verify` step to Tauri macOS workflow). 4) Fix GP-65 (already applied ΓÇö `build_tauri_all.sh --sign` now fails hard). 5) Validate every workflow by triggering a manual dispatch on the main branch and confirming green runs. 6) Check for any other Node 20 deprecation warnings in workflow logs.
**Severity:** ≡ƒö┤ High
**Category:** CI/CD

### BP-33 — Runtime-pack worker subsystem is fully built but never wired (~590 dead LOC, 10 suppressions)
**Status:** ✅ Fixed (2026-09-09, ON WINDOWS host, user chose wire-live): `WorkerState` is `app.manage()`d (`main.rs:163-167`, 279 lines — C-ARCH-1 holds); the WS reader triggers `worker::on_pack_verified` on `offline_pack_verified` (`ws/reader.rs:256-267`); stop-first then `initialize_worker` with a compare_exchange restart slot, release binary-present gate, and shutdown-time kill (`spawn/worker.rs:226-330`, `lifecycle.rs:270-290`); `dead_code` markers dropped from all now-called items (WS-bridge/supervisor fields keep theirs honestly); serialization unit test green + `cargo check` Finished + 48 spawn/worker tests green. REMAINING (next phases, still TBD per plan §7.2/§7.3): host `reconnect_worker_ws` proxy, worker respawn supervisor, port handoff to the sidecar, and the slim-core `transcribe_offline` forwarding stub — the worker runs but has no consumers yet, so the ~450 MB only materializes on hosts with a verified pack + worker binary present.

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

**Enrichment (2026-09-04 BP session — Wave 3):** Additional latent defect in the dead worker subsystem: the worker spawn handshake reuses the sidecar's 30s SERVER_STARTED_TIMEOUT_MS, but the worker's prewarm (pages ~180-200 MB runtime-pack libs, cold-HDD 80-110 MB/s) runs BEFORE worker_started is emitted (worker/__main__.py:200 → _ws_server.py:523) — once wired, cold-disk workers get killed mid-prewarm into a respawn loop of partial prewarms. Wire-time fix: dedicated 90-120s WORKER_STARTED_TIMEOUT_MS or emit worker_started before prewarm. Also: the four spawn loops (worker/release/dev) were ~270 copy-pasted lines — consolidated into `spawn/handshake_loop.rs` (BP-79, fixed 2026-09-08).

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### BP-78 ΓÇö Rule-text drift: C-MODELS-2 and C-MIC-12 no longer describe the shipped code (user adjudication)
**Status:**  Not Fixed SKIPPED (FV session 2026-09-07): SKIPPED: BP-78 ΓÇö conflicts with AGENTS.md `Hard "Don'ts"`: "The user is the only one who can edit these rules" (entry itself: REQUIRES USER ACTION). Recommendation carried to the Final Report: user should update C-MODELS-2 token values (w-24/gap-2/text-xs, h-3 w-3) and either refresh C-MIC-12's text to the binary-recolor contract or order a revert to the ΓÜá-glyph contract.

**Description:** Two AGENTS.md Hard "Don'ts" have drifted from the code they pin: (1) C-MODELS-2 pins download-button tokens `w-[88px]`/`h-3.5 w-3.5`, but the code ships `w-24`/`h-3 w-3` with a dated 2026-08-28 rationale comment ΓÇö a deliberate later user decision whose rule text was never updated; (2) C-MIC-12 pins "clipping signaled by the ΓÜá glyph and aria tier text, never by recoloring the fill", but the evolved design (documented in code) removed the glyph and DOES recolor the fill (bg-primary ΓåÆ bg-destructive) ΓÇö the rAF-writes-only-transform invariant IS preserved.

**User Impact:** A future agent obeying the rule text will "fix" the code backwards ΓÇö undoing deliberate 2026-08-28+ design decisions. This is the exact failure mode AGENTS.md rules exist to prevent, inverted.

**Root Cause:** Rule text not updated when the user changed the design after the rule was written.

**Gain vs Trade-off:** No code change; the gain is rule/code agreement. Only the user can edit AGENTS.md.

**If We Do It:** Rule text matches the shipped contracts; agents stop being misled.

**If We Don't:** The next session risks reverting deliberate design.

**My Recommendation:** Γ£à Implement ΓÇö by the USER: update C-MODELS-2's token values (w-24/gap-2/text-xs, h-3 w-3) and either update C-MIC-12's text to the binary-recolor contract or direct a revert to the ΓÜá-glyph contract. Recorded here so the decision is tracked; agents take no action until then.

**Progress:** `None yet.` (user action)

**Related Files:**
- `voice_typer/client/src/renderer/src/components/models/ModelCardActions.tsx:78-84`
- `voice_typer/client/src/renderer/src/components/feedback/LevelBar.tsx:54-70,126-147`
- `AGENTS.md` (C-MODELS-2, C-MIC-12 ΓÇö user-edited only)

**Fix:** User updates the two rule texts (or orders reverts). Agent-side: none until adjudicated.

**Simplified Fix:** Two of the project's "don't change this" rules describe an older version of two controls; the rules need a one-line refresh from the project owner so future assistants don't undo the newer design.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

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
**Status:** ❌ Not Fixed (investigation only - status restored 2026-09-08, fix not implemented)

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

### BP-160 - Tray-click window restore takes ~15s after 1-2h idle despite live background process
**Status:** ❌ Not Fixed (user-reported 2026-09-08 — documented verbatim from the user's report, NO investigation performed; the fixing agent must investigate the cause itself)

**Description:** (User's report, unedited) When the machine boots, autostart launches the application automatically and Electron runs in the background. Clicking the tray icon is supposed to bring Electron up fully visible, including a taskbar entry — and that normally works with no problem. BUT: if the machine sits for a long time (about an hour or two) without the tray icon being clicked, then clicking the tray icon to show the Electron application takes a long time — around 15 seconds, possibly more — before the window becomes fully visible. The user explicitly verified beforehand (without clicking the tray icon) via Task Manager that the Electron process IS running in the background (present as a background process, absent from the taskbar) — so the window should appear immediately on click, not behave like a cold start. The tray icon itself works fine the whole time. Cause unknown.

**User Impact:** After any long idle period, the first tray-click restore feels frozen for ~15s+ even though the app is already running — the most common "return to the app" path feels broken exactly when the user comes back to the machine.

**Root Cause:** Unknown — NOT investigated (per the reporter's instruction). The fixing agent must reproduce (autostart → boot → leave 1-2h idle → tray-click restore, confirming via Task Manager first that the process is alive in the background) and trace the tray-click → window-show path to find why the restore stalls. Unverified hypotheses only, NOT claims: renderer reload on show, backend reconnect wait, OS-suspended process, window-show sequencing.

**Related Files:** TBD by the fixing agent (the Electron tray-click → window-show path plus anything it waits on before showing).

**Fix:** TBD by the fixing agent after investigation. Expected behavior: with the background process alive, a tray click restores the fully-visible window (plus taskbar entry) promptly, regardless of how long the app has been idle.

**Simplified Fix:** Clicking the tray icon after the app sat in the background for an hour or two should show the window instantly — today it hangs ~15 seconds first.

**Implementation Difficulty:** ❓ Unknown (pending investigation)
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
