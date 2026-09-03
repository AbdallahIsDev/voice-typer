## High Priority

These items are the highest-priority remaining work for the project — they block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

---

> **Won't Fix tasks live in `WONT_FIX.md`** — deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

---

### T-1 — TAURI-E2E — Full-application validation mission (GOAL MODE: zero problems)

**Status:** 🟡 Partial — IN PROGRESS (2026-09-02 local Windows-host session): full pytest+vitest+cargo suites GREEN on the final state (14165/3683/501, 0 failed); tray status_change WS delivery, ws-mode sidecar app.start, tray Models/Microphone rebuilds (TR-1/2/3) verified landed; headless checklist suite (20 tests) green in the full run; recording_level live-level transport fixed end-to-end; notify AUMID registration added so Windows toasts are attributed correctly. Browser-driven visual walkthrough + real-model dictation on the Tauri host remain the open manual-verification phase (VALIDATE ON WINDOWS HOST — this session ran focused/E2E-checklist evidence, not a full interactive GUI drive).

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

### CI-1 — Fix all GitHub Actions CI pipeline errors and warnings

**Status:** 🚫 SKIPPED this session — conflicts with AGENTS.md Hard Don'ts C-CI-2 (tauri-*-build.yml edits are forbidden as first-line fixes; any required change must be validated by a full workflow re-run and confirmed with the user). The underlying test-failure portion of this task WAS fixed via CI-ALL above; the workflow-only items (Node 20 action bumps, GP-66, GP-70) need the user's explicit go-ahead + a validated CI run.

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

---

### GP-66 — macOS CI hard-fails on missing binary instead of SKIP
**Status:** 🚫 SKIPPED this session — conflicts with AGENTS.md Hard Don'ts C-CI-2: tauri-macos-build.yml:608-609 edit requires a user-validated full workflow re-run. Change is ready to apply on approval: replace the hard `exit 1` binary-existence gate with a conditional skip.
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
**Status:** 🚫 SKIPPED this session — conflicts with AGENTS.md Hard Don'ts C-CI-2: adding the codesign --verify step to tauri-macos-build.yml requires a user-validated full workflow re-run. Change is ready to apply on approval.
**Description:** The macOS Tauri build workflow should run `codesign --verify --deep` on the built `.app` bundle to confirm ad-hoc/Developer-ID signing succeeded before the notarization step. Without this, a failed sign is not caught until notarization fails.
**User Impact:** CI can produce an unsigned .app that fails notarization, wasting a full build cycle.
**Root Cause:** The `--verify` gate was added to the Electron `build.yml` but not ported to the Tauri `tauri-macos-build.yml`.
**Progress:** None.
**Related Files:** `.github/workflows/tauri-macos-build.yml` (add codesign --verify step)
**Fix:** Add a step after codesign that runs `codesign --verify --deep --strict /path/to/Voice\ Typer.app` and exits non-zero on failure.
**Severity:** 🟡 Medium
**Category:** CI/CD / signing

---

### BP-1 — History search becomes a full-database scan for Chinese/Japanese/Korean text
**Status:** ❌ Not Fixed (investigation only)

**Description:** When a user searches their dictation history and the search text contains even a single Chinese, Japanese, or Korean character, the search quietly stops using the search index and instead reads every single row in the database to check for a match. The index-based search only works for Latin text. Since the app ships with Chinese as one of its eight supported languages, this affects a normal everyday action for those users, not an edge case.

**User Impact:** History search gets slower the more dictations a user has accumulated. With a few years of history, each search can take hundreds of milliseconds to seconds, and the search box feels like it stalls. Latin-script users never see this because their searches use the index.

**Root Cause:** `voice_typer/server/history_db_internals/search.py:452` — the query router sends any query containing a CJK/fullwidth codepoint to a `LIKE '%…%'` scan, which cannot use any index. This is distinct from the adjudicated Won't Fix item GQ-48 (separator-only queries): here the linear scan is the NORMAL path for CJK users.

**Gain vs Trade-off:** Gaining indexed-speed search for CJK users costs one extra database index (a "trigram" index, supported by SQLite since 3.34 — Python 3.11+ bundles ≥3.37) and a one-time index rebuild. Slight disk and write cost. No behavior change for Latin searches.

**If We Do It:** A Chinese user typing in the history search box sees results as fast as an English user does, regardless of how many years of dictations they have stored.

**If We Don't:** CJK-locale users experience progressively slower history search as their history grows, and may conclude the History page is broken or unusable.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.` (investigation only — nothing is implemented in this mode)

**Related Files:**
- `voice_typer/server/history_db_internals/search.py`

**Fix:** Add a second FTS5 index using the `trigram` tokenizer consulted only for queries containing CJK/wide characters (keep the existing unicode61 index for Latin queries). Verify the bundled SQLite version supports trigram on all target platforms before implementing (distro-linked builds can be older). Keep `prepare_like_search_pattern` as a final fallback.

**Simplified Fix:** Today, searching in Chinese reads the whole history file row by row. We add a second, more flexible search index that handles Chinese and other non-Latin scripts properly, so those searches jump straight to matching entries instead of scanning everything.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-2 — Native key-listener process can be left running after a hard crash
**Status:** ❌ Not Fixed (investigation only)

**Description:** The app uses a small helper program (a "native key listener") to watch keyboard input. If the app is ever killed abruptly — a crash, a force-kill, a power loss — that helper is deliberately disconnected from the app's process group and does not notice the app is gone. On Linux, the helper only dies by accident: the next keystroke writes to a dead pipe and kills it. Until then, it keeps holding the keyboard device files open. The same non-fatal pattern exists in all three platform binaries (Linux C, Windows C, macOS Swift — the macOS leg was not examined beyond the stdin thread shape and needs validation).

**User Impact:** After a hard crash on Linux, a ghost process can hold the keyboard devices until the next keystroke or reboot. On Windows when run from a dev terminal (not under the Tauri host), an orphaned listener can keep a low-level keyboard hook installed after the app is gone — keys may briefly feel "dead" to other apps. Under the normal Tauri host on Windows, the Job Object likely reaps it (needs host validation).

**Root Cause:** `voice_typer/server/native_hotkeys/_spawn.py:235` detaches the child (`start_new_session`); the native binaries' stdin reader thread exits its loop on EOF without setting the process-exit flag (e.g. `native/linux-key-listener.c:131-134`; `g_should_exit` is set only by signal handlers at `:842-847`); no PDEATHSIG on Linux; the Tauri reaper (`src-tauri/src/platform/process/posix.rs:37-41`) kills only the sidecar PID, never grandchildren.

**Gain vs Trade-off:** Making stdin-EOF fatal in the three native binaries is a one-line change per binary with no downside — the parent is gone, so the listener has no reason to live. Linux gains deterministic cleanup instead of an accidental keystroke-triggered death.

**If We Do It:** A hard crash cleans up after itself completely — no ghost processes holding input devices or keyboard hooks.

**If We Don't:** Rare but confusing leftovers: "why is my keyboard acting weird" after a crash, or a process listed as running after the app is closed.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/native_hotkeys/_spawn.py`
- `voice_typer/server/native/linux-key-listener.c`
- `voice_typer/server/native/windows-key-listener.c`
- `voice_typer/server/native/macos-key-listener.swift`

**Fix:** In each native binary's stdin reader thread, set the global exit flag when `fgets()` returns NULL (EOF), in addition to the signal path. Optionally add `prctl(PR_SET_PDEATHSIG, SIGKILL)` in a `preexec_fn` on Linux. VALIDATE ON WINDOWS HOST (Job Object behavior) and on macOS.

**Simplified Fix:** The keyboard-watcher helper is told to keep running until it gets a stop signal. We also tell it to stop when it notices the main app has closed its communication line — so it always shuts itself down when the app dies, even if the app dies suddenly.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-3 — One hard kill of the worker permanently breaks offline transcription on Windows
**Status:** ❌ Not Fixed (investigation only)

**Description:** The transcription worker uses a lock file on Windows to ensure only one copy runs. If the worker is ever force-killed (Task Manager, `taskkill`, a crash), the lock file is left behind with a stale ID. On the next launch, the Windows branch of the worker's lock check simply refuses to start — permanently — because it never checks whether the recorded process is actually still alive. The documentation inside the code claims the Windows path mirrors the Linux "stale lock recovery", which it does not. The server-side code already contains the correct reference implementation.

**User Impact:** After one force-kill, every later attempt to run offline transcription silently fails (exit code 3, a warning line in a log). Dictation appears dead until the user manually finds and deletes a lock file in a hidden AppData folder — something no ordinary user would know how to do.

**Root Cause:** `voice_typer/worker/_single_instance.py:158-176` — Windows branch checks `lock_path.exists()` then refuses; the in-code comment admits the stale-PID check is "left as TODO". Contradicted by `voice_typer/worker/__main__.py:29-34` (claims parity with the POSIX stale-PID path) and by `src-tauri/src/sidecar/spawn.rs:394-398` (claims "a stale lock is detected via PID check"; `initialize_worker` is `#[allow(dead_code)]`). The working reference implementation is `voice_typer/server/single_instance.py:485-495`.

**Gain vs Trade-off:** Pure improvement — the reference implementation already exists in the repo and only needs porting to the worker's Windows branch. No new design, no behavior change for healthy locks.

**If We Do It:** A force-killed worker self-heals: the next launch detects the stale lock, reclaims it, and dictation keeps working. Windows users never see "offline transcription mysteriously dead".

**If We Don't:** Any hard kill (user action or crash) leaves dictation broken until manual intervention in AppData — a silent, hard-to-diagnose support trap.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/worker/_single_instance.py`
- `voice_typer/worker/__main__.py`
- `voice_typer/server/single_instance.py`

**Fix:** Port the server's `_ensure_windows_single_instance` stale-PID probe (OpenProcess/GetExitCodeProcess liveness check) into the worker's Windows branch; reclaim the lockfile when the recorded PID is dead. Update the two docstrings that currently claim parity. VALIDATE ON WINDOWS HOST.

**Simplified Fix:** When the worker starts and finds a lock file, it currently gives up. We make it check whether the process that created the lock is still running; if not, it takes over the lock and starts normally — the same self-healing the app already does on Linux.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-4 — Vocabulary corrections run one full-text scan per phrase, per dictation
**Status:** ❌ Not Fixed (investigation only)

**Description:** Every dictation's text passes through the user's vocabulary corrections (misspelled words, phrase fixes). The vocabulary engine applies each correction phrase one at a time: for each of the user's phrases, it re-scans the entire dictation text. A user with a large vocabulary (the app allows up to 5,000 phrases plus 5,000 word patterns) triggers up to 10,000 full-text passes per dictation. The bundled default (8 phrases) is unaffected, so this only bites power users. The repo's own text-cleanup engine already solved this exact problem years ago with a "combine all phrases into one pattern, single pass" design — the vocabulary engine never received that upgrade.

**User Impact:** Power users with large correction dictionaries experience a growing delay between stopping speech and seeing the text — potentially seconds per dictation at the cap. Everyone else is unaffected.

**Root Cause:** `voice_typer/server/vocabulary.py:882-896` — the phrase loop runs `pattern.subn(...)` per entry over the full text. The default corrections route goes through vocabulary (`dictation_pipeline/text_steps.py:64`, `skip_corrections=vocab_enabled` defaults to on). Contrast: `voice_typer/server/text_cleanup/_engine.py:46-54, 485-499` documents the combined-alternation single-pass design. GQ-32 (Won't Fix) measured 145ms for the single-pass engine at the same cap — the loop is strictly worse (M passes vs 1), so the total is plausibly seconds at the cap.

**Gain vs Trade-off:** Faster dictation completion for power users, using an approach already proven inside the same codebase. Trade-off: the usage-tracking callback (which counts hits per phrase) must be preserved while switching to the combined pattern — the one subtle part.

**If We Do It:** Dictation text appears noticeably sooner for users with large vocabularies; delay stops growing with dictionary size.

**If We Don't:** Heavy vocabulary users keep waiting longer and longer per dictation as their dictionary grows, and may disable the feature that is supposed to help them.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/vocabulary.py`
- `voice_typer/server/dictation_pipeline/text_steps.py`
- `voice_typer/server/text_cleanup/_engine.py`

**Fix:** Reuse the repo's combined-alternation design in `VocabularyManager.apply_to_text`: build one alternation regex per category (the compile cache at `:195-199` already exists), run a single `re.sub` per category with a lookup-dict callback that increments the usage tracker counts. No cap change, no new dependency.

**Simplified Fix:** Instead of re-reading the whole dictated text once for every correction the user has saved, we combine all their corrections into one search and apply them in a single pass — the same trick the app already uses for its built-in corrections.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-5 — First-run Windows startup and every login pay avoidable blocking waits
**Status:** ❌ Not Fixed (investigation only)

**Description:** Two blocking waits sit on time-critical paths. (1) On Windows first run, the desktop-shortcut creation runs synchronously on the startup thread BEFORE the hotkey is registered and microphones are enumerated. The fast path (creating the shortcut via Windows' built-in COM API) is quick, but if that API is unavailable the code falls back to launching a PowerShell process with a 30-second ceiling — and a second PowerShell step (stamping the shortcut icon) takes "seconds" by its own comment. While this runs, the dictation hotkey does not work yet. (2) At every autostart login where the backend is already running, the launcher sleeps a fixed 0.5 seconds before exiting — and the OS waits for the launcher to exit before considering login complete.

**User Impact:** First-run Windows users wait up to seconds before their hotkey works (in the fallback case). Every autostart login with a prewarmed backend takes an extra 0.5 seconds for no functional reason. Neither breaks anything; both are avoidable sluggishness on the two paths users most judge an app by.

**Root Cause:** (1) `voice_typer/server/startup_sequence/_phases_late.py:519` calls `ensure_desktop_shortcut(app)` synchronously; the "shortcut creation is fast" comment at `:514-517` is only true for the happy path. The same file already dispatches equally-idempotent work to fire-and-forget daemon threads at `:414-427` (the precedent to copy). (2) `voice_typer/server/autostart_launcher.py:349-353` — unconditional `time.sleep(0.5)` in the "backend already running" branch; the focus child is spawned detached (`autostart/focus.py:77,116`) and never waited on, so the sleep's purpose is undocumented.

**Gain vs Trade-off:** Pure latency wins. Fix (1) uses an in-file precedent; fix (2) removes a wait whose purpose is unproven — regression-check focus behavior when removing. The `[AUTOSTART] RESULT` observability lines (C-CROSS-5) must be preserved.

**If We Do It:** The hotkey works as early as possible on first run; logins complete without the artificial half-second lag.

**If We Don't:** First-run and login feel slower than they should, for reasons invisible to the user.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/startup_sequence/_phases_late.py`
- `voice_typer/server/startup_tasks.py`
- `voice_typer/server/server_platform/desktop_shortcut.py`
- `voice_typer/server/autostart_launcher.py`
- `voice_typer/server/autostart/focus.py`

**Fix:** (1) Dispatch `ensure_desktop_shortcut` on a fire-and-forget named daemon thread mirroring `sync_prewarm_task`; update the stale comment. (2) Remove or bound the 0.5s sleep (do NOT `child.wait()` the focus GUI child for its lifetime — it would block login until the app closes); verify focus still works; keep the RESULT line.

**Simplified Fix:** The app creates its desktop shortcut on a background thread instead of making the startup sequence wait for it, and the login helper stops sleeping half a second before exiting when there is nothing to wait for.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-6 — Performance benchmark suite no longer guards the budgets it exists to guard
**Status:** ❌ Not Fixed (investigation only)

**Description:** The benchmark suite — the project's early-warning system for performance regressions — has four problems. (1) The audio filter-chain benchmark's recorded baselines sit 3-11× ABOVE the documented real-time budgets (50ms recorded vs <32ms budget at 16kHz; 60ms vs <10.6ms at 48kHz), with tolerance widened so CI only fails above ~100ms. (2) The most expensive filter (the neural noise suppressor) is excluded from the tracked chain, so the production configuration is unguarded. (3) The "model load time" benchmark never actually loads a model — it times the constructor, which was made deliberately hollow by a later refactor. (4) The percentile math at the default 10 iterations makes "p90" equal the maximum, and there is no warm-up iteration on the CPU path, so the reported number includes one-time cold-start cost. Additionally, the three surfaces users actually feel — end-to-end dictation latency, real cross-process IPC round-trip, and model-swap time — have no benchmarks at all.

**User Impact:** None directly today. The risk is future: a change that slows transcription or the audio chain by 2-5× would sail through CI because the guardrails are miscalibrated, and users would feel it as laggy dictation with no diagnostic trail.

**Root Cause:** Baselines were recorded on a noisy sandbox and ratcheted up instead of investigated (bench/bench-baseline.json entries carry the budget-vs-observed mismatch in their own notes); `bench/bench_transcription.py:192-209` predates the load-deferral refactor; `_percentile` at `:212-217` uses `int(len*0.9)`; `bench_ipc.py:18-21` measures in-process event-bus dispatch, not the real TCP/WS hop.

**Gain vs Trade-off:** Restores the meaning of the perf gates at the cost of re-baselining on quieter hardware and writing one new end-to-end benchmark. No product behavior change.

**If We Do It:** Performance regressions get caught before shipping; the "is it slower?" question is answerable with numbers.

**If We Don't:** The benchmark suite keeps printing green while real budgets are blown — a false sense of safety.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `bench/bench_audio_filter_chain.py`
- `bench/bench-baseline.json`
- `bench/bench_transcription.py`
- `bench/bench_ipc.py`

**Fix:** (1) Add a hard `realtime_margin > 0` assertion for both sample rates independent of the ratchet; (2) add a tracked `noise_suppression="rnnoise"` variant (skip-if-unavailable); (3) call `engine.load()` in `bench_model_load`; (4) use nearest-rank/ceil percentile, ≥20 iterations, 1 untimed warmup; (5) add a dictation end-to-end bench and a real loopback IPC round-trip. Baseline changes are additive per the baseline file's own rule. No CI workflow files are touched (no C-CI-2 conflict — these are repo bench/ files).

**Simplified Fix:** The app's speed-measurement tools have drifted: some measure the wrong thing, some compare against limits that were quietly raised past the point of meaning, and the speeds users actually feel are never measured. We fix the measurements so they catch real slowdowns again.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-7 — Fast Startup (prewarm) re-reads hot files, never warms model weights, and its status card always shows "cold"
**Status:** ❌ Not Fixed (investigation only)

**Description:** The "Fast Startup" feature has three defects. (1) Every worker start re-reads every byte of the warmed library files even when they are already in the OS cache — a latency-based cache-hit probe exists in the same file but is never consulted. (2) The warm list covers only five library folders; it never warms the model weight files, which dominate cold-start cost after a reboot (a ~3GB model file read). (3) The Settings "Cache Status" card — the feature's only visible effectiveness signal — looks for a file named `model.safetensors`, which no current backend downloads (Whisper ships `model.bin`; Parakeet ships `.onnx` files; the legacy safetensors pin is no longer fetched). The card therefore always reports "cold / 0 bytes" even right after a successful warm run, and a 25-line helper looking for the same non-shipped filename is dead code.

**User Impact:** After a reboot, the first dictation still pays the full multi-GB weight read even with Fast Startup enabled — the feature buys far less than promised. And because the status card always says "cold", users cannot tell whether prewarm is doing anything, so the feature looks broken even when the library-warming part works.

**Root Cause:** (1) `voice_typer/server/prewarm/cache_probe.py:648-679` warms unconditionally; `_cache_ratio` (`:682+`) gates only the status page. (2) `_WORKER_WARM_PACKAGES` at `:302-308` is library-only. (3) `prewarm/status.py:254` hardcodes `model.safetensors`; actual pinned payloads per `security/model_integrity.py:126-150` (model.bin) and `:116-118` (.onnx); `_find_parakeet_weights` (`cache_probe.py:539-564`) has zero callers. (Pre-empting the counter-argument: the manifest does still pin a legacy `nvidia/parakeet-tdt-0.6b-v3` safetensors entry at `model_integrity.py:95-103`, but the engine is ONNX-only and that entry is no longer downloaded — the conclusion stands.)

**Gain vs Trade-off:** Faster first-dictation-after-reboot and an honest status card. Trade-off: warming multi-GB weights adds background disk I/O at startup (bounded, daemon thread, gated by the cache-hit probe so it is skipped when already warm).

**If We Do It:** First dictation after a reboot starts transcribing seconds sooner; the Cache Status card reflects reality and users can trust the Fast Startup toggle.

**If We Don't:** Fast Startup under-delivers silently and its status indicator always lies — the worst combination for user trust in a premium app.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/prewarm/status.py`
- `voice_typer/server/prewarm/cache_probe.py`
- `voice_typer/server/security/model_integrity.py`

**Fix:** Probe the union of backend payload names (model.bin, *.onnx shards, model.safetensors) via the pinned manifest per `model_integrity.py`'s `model_extensions`; extend the warm phase to `_warm_file` the active model's pinned files gated by the `_cache_ratio` skip; delete or repurpose `_find_parakeet_weights`.

**Simplified Fix:** The "warm up the app for a fast start" feature currently warms the wrong files, skips the biggest ones entirely, and its status indicator looks for a file the app no longer uses — so it always says "cold". We point it at the files that are actually used, including the big model files, and teach it to skip work that is already done.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-8 — Bare "ignore all errors" blocks hide real failures on the recording and startup paths
**Status:** ❌ Not Fixed (investigation only)

**Description:** A sweep found bare `except: pass` blocks (catch-everything, do-nothing, log-nothing) at nine sites on paths users depend on: the microphone device-open path (channel-count probing and Bluetooth quality detection, three sites), the buffer-clearing worker, the device-change notification at startup, the download fallback's file cleanup, and three sites in the native-hotkey reader/core/recorder. Sibling code in the same files handles identical best-effort situations correctly by logging at DEBUG level — these nine sites predate or skipped that discipline. The repo even has a test file that pins "no broad excepts" for a list of owned files; four of these files are not on that list, so the contract does not protect them.

**User Impact:** Individually small; collectively a diagnosability trap. When a microphone fails to open with a fallback-to-defaults, or a device-change notification silently fails to reach the UI, there is zero log trail — support (and future agents) must guess. One block hides a device-cache probe failure on the hotkey critical path.

**Root Cause:** Sites: `voice_typer/server/startup_tasks.py:608-618`; `voice_typer/server/recording/stream_lifecycle.py:162-163, 208-209, 295-296`; `voice_typer/server/recording/buffer.py:337-338`; `voice_typer/server/segmented_download.py:926-930`; `voice_typer/server/native_hotkeys/_reader.py:97-100`, `_core.py:479-480, 488-489`. `tests/test_broad_except_cleanup.py`'s `_OWNED_FILES` omits stream_lifecycle.py and the native_hotkey modules.

**Gain vs Trade-off:** Pure observability gain — same behavior, plus a log line. No risk. Narrowing exception types where the failure domain is known (device APIs raise specific errors) also converts silent wrong-behavior into loud failures.

**If We Do It:** Every "best-effort" fallback leaves a breadcrumb; device problems become diagnosable from the log file alone.

**If We Don't:** Intermittent device problems remain mysteries — the log says nothing precisely when something went wrong.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/startup_tasks.py`
- `voice_typer/server/recording/stream_lifecycle.py`
- `voice_typer/server/recording/buffer.py`
- `voice_typer/server/segmented_download.py`
- `voice_typer/server/native_hotkeys/_reader.py`
- `voice_typer/server/native_hotkeys/_core.py`
- `tests/test_broad_except_cleanup.py`

**Fix:** Replace each bare `pass` with `log.debug("...", exc_info=True)` matching the sibling pattern; narrow the device-path blocks to the documented failure types (mirroring `device_manager.py`'s narrowed tuples); add the four unprotected files to `_OWNED_FILES` so the contract is pinned.

**Simplified Fix:** Several places in the app respond to unexpected errors by silently doing nothing — not even writing a log line. We make each of them write a short diagnostic note instead, so when something goes wrong there is evidence to act on.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-9 — A safety-critical comment documents the wrong guarantee on the streaming zero-clear path
**Status:** ❌ Not Fixed (investigation only)

**Description:** The recorder hands audio snapshots to the streaming engine as live views into its internal buffers. A comment on the streaming side claims the snapshots are always fresh copies that "do not share memory with the recorder's internal buffer". That is false — the snapshots ARE live views. The system remains correct only because of a subtle numpy behavior (slices-of-views report the ROOT buffer as their `.base`, and the zero-clear guard compares by that root identity) — which the comment never mentions. The guard exists to prevent a past corruption bug (zeroing buffers the stream is still reading); a maintainer who trusts the comment would conclude the guard is redundant and could remove it, silently reintroducing corrupted dictation audio.

**User Impact:** None today. This is a loaded trap: the next well-intentioned "cleanup" of the streaming path could corrupt recordings. (The reviewer independently re-verified the numpy semantics with a live probe — the gate works exactly as described.)

**Root Cause:** `voice_typer/server/streaming.py:152-158` (false comment) vs `voice_typer/server/recording/_recorder_split.py:719, 741, 756, 761` (`buf.view()` and `_cached_resampled[:len]` live views); the working guard at `streaming.py:42-56` + `:1027-1032`. The documented provenance anchor is `_recorder_split.py:157-159`.

**Gain vs Trade-off:** Comment-only fix: zero behavior change, removes a latent correctness trap. Optionally walking `base.base` chains in the guard was considered and should be DEFERRED (the current gate is verified correct; changing it carries regression risk).

**If We Do It:** Future maintainers read the true contract and preserve the guard; the corruption class of bug cannot be reintroduced by a comment-trusting cleanup.

**If We Don't:** The trap stays armed — the pre-fix corruption bug documented in the finally-block comment can come back through routine refactoring.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/streaming.py`
- `voice_typer/server/recording/_recorder_split.py`

**Fix:** Rewrite the comment to state: snapshot returns a live view over `buf.storage` / `_cached_resampled`; window slices are nested views; the zero-gate's provenance check works because numpy reports the ROOT buffer as `.base` for simple slices — do not reshape/fancy-index the snapshot without revisiting `_is_view_of_live_recorder_audio`. Anchor on the invariant at `_recorder_split.py:157-159`.

**Simplified Fix:** An explanatory note in the code claims the audio data is always a private copy, but it is actually a shared window into live memory, kept safe by a subtle protection. We rewrite the note to describe the truth, so nobody "simplifies away" the protection and corrupts recordings.

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

---

### BP-11 — Server "mixin mosaic": four composed classes share hidden state through injected attributes (58 suppression sites)
**Status:** ❌ Not Fixed (investigation only)

**Description:** Four central server classes are assembled from many "mixin" partial classes: the IPC server (20 mixins), the main app (5, ~3,280 lines across 8 files), the dictation pipeline (6), and the service layer (11, with non-cooperative `__init__` calls). Each mixin freely reads and mutates attributes defined by its siblings through the shared `self`, with no declared contract between them. Worse, several subsystems (the WebSocket layer, the hotkey dispatcher) attach their attributes and even replace methods onto these objects AFTER construction — a pattern that requires 58 `type: ignore` / error-suppression sites to coexist with the type checker (16 of them genuine dynamic-attribute injections; 6 undeclared `_ws_*` attributes plus 2 injected methods, one of which wraps/replaces the IPC server's `stop` at instance level).

**User Impact:** None directly today. The cost is stability of future change: exactly this hidden-state injection pattern produced past incidents (the C-WS-1/C-WS-2 classes of bugs were found on lifecycle boundaries where these attributes materialize), and no type-checker or interface can currently catch a rename that breaks a sibling mixin.

**Root Cause:** Historical monolith splits preserved the god-object's shared-state semantics while physically distributing the code; the injection sites were never given declarations. Key sites: `voice_typer/server/sidecar_ws_internals/graceful_shutdown.py:118,130,132,233,245,266` (instance-level `stop` wrap), `encode_pool.py:97,143`, `sidecar_ws.py:1371,1493`, `hotkey_dispatcher.py:454-1173` (5 injection sites), `ipc/rate_limiter.py:515`; `ipc_server.py:354-375` (20 bases), `app.py:103`, `dictation_pipeline/__init__.py:109-116`, `service/__init__.py:109-183`.

**Gain vs Trade-off:** Phase 1 (declare the 8 undeclared attributes next to the 5 already-declared ones in `IPCServer.__init__`; declare the 4 hotkey-backend hooks on the base class; replace the instance-level `stop` wrap with an explicit hook) is contained, behavior-identical, and deletes ~16 suppressions. Phase 2 (Protocol-based interface extraction) is a large, staged refactor — worth doing incrementally, not as one big bang.

**If We Do It:** The WebSocket shutdown path and hotkey hooks become statically checkable; a renamed attribute fails at type-check time instead of at 2am in production; ~16 suppression comments disappear.

**If We Don't:** Every future transport, shutdown, or hotkey change risks the silent-failure class the project has repeatedly paid for, and the suppression count keeps growing.

**My Recommendation:** ✅ Implement (phase 1 now; phase 2 incrementally)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/ipc_server.py`
- `voice_typer/server/sidecar_ws.py`
- `voice_typer/server/sidecar_ws_internals/graceful_shutdown.py`
- `voice_typer/server/sidecar_ws_internals/encode_pool.py`
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/app.py`
- `voice_typer/server/dictation_pipeline/__init__.py`
- `voice_typer/server/service/__init__.py`

**Fix:** Phase 1: declare the 8 remaining injected attributes in `IPCServer.__init__` beside the existing 5 (mirroring the completed cleanup documented at `ipc_server.py:641-645`); declare `_tray`/`_on_state_change_callback`/`_delegated`/`_prefer_message_loop_first` on the `HotkeyBackend` base; replace `server.stop = wrapped_stop` with an explicit `set_stop_wrapper()` hook; delete the paired suppressions. Phase 2 (🟡-🟠 per stage): `@runtime_checkable` Protocols for consumed state; migrate shared mutable state into the existing coordinators (TimerCoordinator/MicrophoneRegistry precedent) until mixins become standalone controllers.

**Simplified Fix:** The app's biggest classes are assembled from many pieces that secretly share variables with each other, and some subsystems bolt their variables on later — which forces the code to tell the type-checker to look away 58 times. We first make every shared variable an official, declared part of the class so the checker can verify it, then gradually give each piece its own explicit interface.

**Implementation Difficulty:** 🔴 Very Hard (phase 1 alone: 🟡-🟠)
**Severity:** 🟡 Medium

---

### BP-12 — God-class and giant method bodies the file splits never finished
**Status:** ❌ Not Fixed (investigation only)

**Description:** The hotkey dispatcher is a single 1,476-line class owning at least five responsibilities (the main dictation hotkey, the Escape-cancel backend, the repaste backend, the shared native-subprocess pooling machinery ~300 lines, and the push-to-talk safety timer). Meanwhile the "extracted" modules still contain monolith-grade method bodies: the recording start path is a ~370-line method (consent gate, tray states, recorder start, worker spawn, error mapping all inline), the stop-and-transcribe path is ~188 lines with ten numbered concerns, and the WebSocket dispatch builder is a 333-line closure factory that also lazily creates four server attributes.

**User Impact:** None directly. The cost is fix velocity and regression risk: every hotkey/pooling change re-reads the same giant class; unit-testing any single concern inside the 370-line start path requires the whole recorder/tray/config matrix; ESC/repaste/pooling changes collide in one file.

**Root Cause:** `voice_typer/server/hotkey_dispatcher.py:100-1476` (5 responsibilities; restart at :1222-1367 ~145 lines, register_esc :910-1050 ~140 lines, pooling ~300 lines); `voice_typer/server/recording_lifecycle.py:307-677` (`_start_impl` ~370L), `:1041-1229` (`_run_stop_and_transcribe` ~188L); `voice_typer/server/sidecar_ws.py:682-1015` (`_make_dispatch` 333L closure factory + lazy attribute creation).

**Gain vs Trade-off:** Decomposition into owned objects (HotkeyPool, EscHotkeyController, RepasteHotkeyController, PttSafetyTimer; named start steps behind the toggle lock) preserves behavior verbatim and follows the repo's own stage-object precedent (`dictation_stages.py`). Cost: large mechanical refactor with test-pin migration; best executed AFTER BP-13's test-seam work (which unblocks the pins).

**If We Do It:** Each hotkey concern, each recording-start step, and the dispatch factory become independently testable and independently changeable; the highest-churn files stop being the slowest to modify safely.

**If We Don't:** The most-edited code stays the hardest to edit safely; every hotkey or recording fix pays a comprehension tax proportional to 1,476+370+333 lines.

**My Recommendation:** ✅ Implement (after BP-13)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/hotkey_dispatcher.py`
- `voice_typer/server/recording_lifecycle.py`
- `voice_typer/server/sidecar_ws.py`

**Fix:** Extract `HotkeyPool` (shared-native pooling + matcher multiplexing + state resync), `EscHotkeyController`, `RepasteHotkeyController`, `PttSafetyTimer` — dispatcher becomes a ~300-line facade with `self.hotkeys.<field>` accessors stable (E1 create-first). Split `_start_impl` into named steps (consent gate → state checks → recorder start → worker spawn) behind `_toggle_lock`; move `_make_dispatch`'s pool/event/lock creation into `IPCServer.__init__` (rides BP-11 phase 1), leaving a ~60-line factory.

**Simplified Fix:** A few of the app's most important files each cram five jobs' worth of code into single enormous classes and methods. We split each job into its own named module and each giant method into named steps — no behavior change, just code you can read and test one piece at a time.

**Implementation Difficulty:** 🔴 Very Hard (🟠 if executed after BP-13)
**Severity:** 🟡 Medium

---

### BP-13 — Test-seam-shaped module boundaries + live C-ARCH-2 violation in the recording package
**Status:** ❌ Not Fixed (investigation only)

**Description:** Several module boundaries in the server were drawn to keep old tests passing rather than to separate concerns: `sidecar_ws.py` stays a 1,552-line file (with 898 lines already extracted beside it) because ~14 test files pin its literal path; `app.py` keeps builders in place because a lock-order contract test reads its source text; `recording_lifecycle.py` documents a re-entrant stop-hop that exists only for monkeypatch contracts. The sharpest instance is the recording package: production code deliberately routes its OWN function calls through the package object ("patch-path bridges") so tests can intercept them at the package level — the exact pattern the project's C-ARCH-2 rule (2026-08-26) prohibits in that package, one the two sibling packages already cleaned. One test patch is provably a no-op (it patches a re-export the production code never reads, while claiming to bypass something); two pin-tests actively forbid the canonical form; and a commit AFTER the rule landed added a new bridge call site plus pattern-endorsement comments. The package's own docstring claims the migration is complete, which is false.

**User Impact:** None directly. The cost: a recording-layer bug fix must thread through bridge + implementation + ~21 package-level test patches; the no-op test patch silently stops mocking what it claims to mock (its assertions pass against real behavior by accident); future agents inherit a false "migration complete" note.

**Root Cause:** `voice_typer/server/recording/` — bridges at `recorder.py:42`, `audio_pipeline.py:62`, `resampling.py:44`, `session_state.py:69`, `disconnect_handler.py:626`, `_recorder_split.py:592/777/1324`; 19 production call sites (recorder.py:412/417/428/495, session_state.py:221/241/442/450/471/491/639/652, audio_pipeline.py:665, resampling.py:422/489, disconnect_handler.py:635, _recorder_split.py:596/877/1481); 21 package-attr test patches (test_recording.py ×9, test_recording_and_audio.py ×2, test_audio_pipeline_vad_fir_cache.py ×5, test_recording_controller_resampling_fix.py:66, test_session_state_module.py ×3, test_buffer_clear_worker.py:220 — the no-op). Post-rule commit 96ca879e (2026-09-01) added 1 call site + 3 endorsement doc lines. Pin-tests forbidding canonical form: test_recorder_secure_clear_array.py:41-99, test_session_state_module.py:576-614. False claim: `recording/__init__.py:50-55`. Adjudication upheld by W4-R1 against AGENTS.md:791-795; server_platform/ and prewarm/ verified clean (canonical shape achievable).

**Gain vs Trade-off:** Migrating ~21 test patches to owning-submodule form and deleting the 6 bridges removes dual patch paths and the no-op patch class of bug, and finally makes the docstring true. Cost: mechanical churn across ~10 test files — the same migration C-ARCH-2 already completed for two other packages, so the recipe is proven.

**If We Do It:** Recording-layer tests patch the module that owns each function (single patch path, no no-ops possible); the recording package matches the canonical shape its siblings already achieved; sidecar_ws's split can then be finished.

**If We Don't:** The recording layer keeps the exact CR-67-era debt class the rule was written to prevent, and every future recording refactor pays double patch-path comprehension cost.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/recording/recorder.py`
- `voice_typer/server/recording/resampling.py`
- `voice_typer/server/recording/session_state.py`
- `voice_typer/server/recording/audio_pipeline.py`
- `voice_typer/server/recording/disconnect_handler.py`
- `voice_typer/server/recording/_recorder_split.py`
- `voice_typer/server/recording/__init__.py`
- `tests/test_recording.py`, `tests/test_recording_and_audio.py`, `tests/test_audio_pipeline_vad_fir_cache.py`, `tests/test_session_state_module.py`, `tests/test_buffer_clear_worker.py`, `tests/test_recorder_secure_clear_array.py`

**Fix:** (1) migrate the 21 package-attr patches to owning-submodule form (`…recording.resampling._get_resample_poly`, `…recording.buffer._secure_clear_array`, …); (2) rewrite the 19 call sites as sibling-module-object reads (`from . import buffer as _buffer_mod`); (3) delete the 6 bridges; (4) invert the two source-pin tests to pin the CANONICAL form; (5) correct the `__init__.py` note. Keep the `np`/`sd`/`time` stdlib proxies (same rationale as server_platform's carve-out). Then, as phase 2, finish the sidecar_ws split the same way (its pin list is in `sidecar_ws.py:183-225`).

**Simplified Fix:** Some of the app's internal wiring is arranged a specific way purely so old tests can eavesdrop on it — including one test that thinks it's eavesdropping but isn't. We re-point those tests at the proper places and simplify the wiring to its natural shape, exactly as was already done for two other parts of the app.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-14 — Tray cluster: distributed god-object, 990-line 4-concern menu module, dead test-only menu builder
**Status:** ❌ Not Fixed (investigation only)

**Description:** The tray was split into 10+ satellite modules, but the split preserved the old god-object: `TrayIcon` still owns ~25 mutable state attributes that the satellites mutate directly, and 30+ delegate methods re-import their target module on every call. The tray menu module (990 lines) mixes four concerns (pystray menu building, Tauri dict-model building, event publishing, click dispatch) — publishing is also split confusingly across two sibling modules that both define a function named `publish_tray_state` with different signatures, disambiguated only by import aliasing. A 105-line parameterized menu builder has zero production callers (only tests call it) and has drifted from the real menu structure — meaning the tested tray shape is not the shipped one. Additionally: the Python icon-name map vs the Rust icon whitelist lacks a parity pair in the existing cross-language drift-guard test family (7 pairs exist; the icon pair is missing — adding a renamed state silently freezes the Tauri tray icon at its last state), and task-ID debris litters the cluster's docstrings.

**User Impact:** Mostly indirect: future tray changes are slow and risky, and the icon-name drift risk means a future state addition could freeze the tray icon silently. The dead menu builder means tray tests validate a menu users never see — real regressions in the shipped menu can pass tests.

**Root Cause:** `voice_typer/server/tray.py:8-27` + `tray_lifecycle.py:16-20` (docstrings admit the test-pin-shaped layout); `tray_menu.py` 990L (build_menu_for_tray :542-680, build_tray_menu_model :238-443, publish :446-513, dispatch :954-990); `tray_menu.py:467` vs `tray_publish.py:108` (same-name functions); `tray_menu.py:115-218` (`build_menu` — zero production callers, pinned by tests/test_tray_menu.py, tests/test_e2e_smoke.py:174, tests/tauri/mig19/test_tray_menu.py:301-321); `tray_publish.py:51-58` vs `src-tauri/src/tray/icon_cache.rs:59` (missing 8th parity pair in tests/tauri/test_tray_icons.py's existing 7); task-ID debris at tray_menu.py:1 (:56, :74, :133), tray.py:123 ("P4 #30"), tray.py:646, tray_menu.py:159/195 ("Finding #3"), tray_menu.py:84, :549-560, :684, tray_window.py:3/7/160/245, tray_state.py:55-58 (mangled docstring), waveform_bubble_wiring.py:74.

**Gain vs Trade-off:** Restructuring buys testability and stops the drift risks; the tray's user-facing behavior (C-TRAY-1/2: minimal menu, no Undo/Repaste items) is untouched. Cost: migrating ~90 test monkeypatch pins — significant but mechanical. The parity pair and the dead-builder deletion are small, high-value slices that can ship independently.

**If We Do It:** Tray changes become contained; the shipped menu is what tests verify; an icon-name change fails loudly at test time instead of freezing the tray silently.

**If We Don't:** The tray keeps its invisible-god-object shape; the icon-name drift landmine stays armed; tray tests keep passing while validating a phantom menu.

**My Recommendation:** ✅ Implement (parity test + dead-builder removal first; restructure staged)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/tray.py`
- `voice_typer/server/tray_menu.py`
- `voice_typer/server/tray_publish.py`
- `voice_typer/server/tray_state.py`
- `voice_typer/server/tray_lifecycle.py`
- `src-tauri/src/tray/icon_cache.rs`
- `tests/tauri/test_tray_icons.py`

**Fix:** (1) Add the 8th drift-guard pair to `tests/tauri/test_tray_icons.py`: assert `set(_APP_STATE_TO_ICON_NAME.values()) ⊆ ALLOWED_ICON_NAMES` parsed from the Rust test source. (2) Migrate the 3 test files off `build_menu` to `build_menu_for_tray`/`build_tray_menu_model`, then delete `build_menu` (E15; archive/deleted_files.txt). (3) Rename `tray_menu.publish_tray_state` → `publish_tray_state_event` (re-export). (4) Move publish/dispatch out of tray_menu.py into tray_publish.py. (5) Introduce a `TrayState` dataclass owned by TrayIcon; collapse the delegate layer; migrate the ~90 pins. (6) Clean the task-ID debris (folds into BP-24 if that ships first).

**Simplified Fix:** The system-tray code was physically split into many files but they all still reach into one shared bag of state, one file still does four unrelated jobs, and a chunk of menu-building code exists only so old tests have something to test — it doesn't match the real menu. We tidy the split, give the tests the real menu to verify, and add a check that the tray icon names Python sends match the names the Windows/Mac/Linux side accepts.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-15 — Templates and Vocabulary pages are a self-admitted 1:1 code fork (~270 duplicated lines)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The Templates page was built by copying the Vocabulary page, as its own header admits ("mirrors the Vocabulary page's useVocabularySelection exactly... only the row data shape differs"). Two whole hook families are near-identical: the selection hooks (~150 lines each — selected-ids set, toggle, select-many, bulk delete with a 6-second undo toast) and the import/export hooks (~175 vs ~203 lines — same import skeleton, same export flow, same reset logic, differing only in the data shape and message keys). The toolbars and list rows follow the same parallel pattern, and the fork is already drifting (one uses `_id`, the other `id`).

**User Impact:** None today. The cost: a bug fixed on one page (an undo-restore edge case, an import error path) must be remembered and replicated on the other — the historical failure mode of every copy-paste fork.

**Root Cause:** `voice_typer/client/src/renderer/src/pages/templates/hooks/useTemplateSelection.ts:1-4` (self-admission), `useVocabularySelection.ts`, `useTemplateImportExport.ts`, `useVocabularyImportExport.ts` (+ toolbars VocabToolbar/TemplateToolbar).

**Gain vs Trade-off:** One generic hook pair parameterized by row shape and message keys; both pages keep their tests. The refactor must migrate the guard/source-grep tests that pin the current shapes (useRowSelection difficulty 🟠 accordingly). No user-visible change.

**If We Do It:** Fixes land once and both pages get them; the ~270 duplicated lines become ~40.

**If We Don't:** The pages silently diverge — users of one page get fixes the other never receives.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/templates/hooks/useTemplateSelection.ts`
- `voice_typer/client/src/renderer/src/pages/vocabulary/hooks/useVocabularySelection.ts`
- `voice_typer/client/src/renderer/src/pages/templates/hooks/useTemplateImportExport.ts`
- `voice_typer/client/src/renderer/src/pages/vocabulary/hooks/useVocabularyImportExport.ts`

**Fix:** Extract a generic `useRowSelection<T extends { id: string }>` (params: rows, ref, persist, showSnack, i18n keys) and a `useBridgeImportExport({parse, dedupeKey, persist, bridgeFn, i18n})` factory; both pages consume thin adapters. Behavior-preserving; existing page test suites pin the result.

**Simplified Fix:** The Templates page was created by copying the Vocabulary page and changing the data type — nearly 270 lines are duplicated between them. We turn the shared behavior into one parameterized piece of code both pages use, so a fix made once helps both.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-16 — Two load-bearing renderer patterns are copy-pasted across 22 and 3 files respectively
**Status:** ❌ Not Fixed (investigation only)

**Description:** Two patterns that every page needs exist only as copy-paste. (1) The "latest-ref mirror" (`const ref = useRef(fn); useEffect(() => { ref.current = fn }, [fn])`) — the guard that keeps effect callbacks fresh without re-running effects — is duplicated verbatim in 22 production files, each carrying its own variant of the same explanatory comment. An AST-based guard test pins all 22 copies individually, so the pattern is load-bearing and must be migrated carefully (that test moves in lockstep). (2) The background-refresh block (stale-flag + debounced refresh + window-visibility gate + dual event subscription + unmount cleanup, ~50-60 lines) is duplicated across Home, History, and Dashboard, with an empty-stats object literal repeated 4×.

**User Impact:** None directly. The cost: the next refresh-logic bug (a debounce window change, a visibility race) must be fixed three times, and a subtle mistake in one of the 22 ref-mirrors is invisible because each looks locally correct.

**Root Cause:** callRef mirrors: 22 files incl. `hooks/useConnection.ts:162-165`, `pages/Home.tsx:102`, `components/settings/useSettingsConfig.ts:175-178`, `pages/templates/hooks/useTemplates.ts:80`, `hooks/useTheme.ts:552-555` (+17); guard: `__tests__/useEffect-call-dep-guard.test.ts`. Refresh blocks: `pages/Home.tsx:206-280`, `pages/history/History.tsx:89-187`, `pages/dashboard/hooks/useDashboardData.ts:335-398`; lockstep test `client-pages-fixes.test.tsx:460-468`.

**Gain vs Trade-off:** One canonical `useLatestRef` hook and one `useDebouncedEventRefresh` hook, with the rationale comment written once. The migration is mechanical but wide (22 sites + 2 guard/source-grep tests move in lockstep — hence 🟠 not 🟡).

**If We Do It:** The two most-repeated infrastructure patterns in the renderer have single, tested homes; future pages use them for free.

**If We Don't:** Every new page copies the 8-line mirror + comment again; the count grows past 22.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/` (22 mirror sites)
- `voice_typer/client/src/renderer/src/__tests__/useEffect-call-dep-guard.test.ts`
- `voice_typer/client/src/renderer/src/pages/Home.tsx`
- `voice_typer/client/src/renderer/src/pages/history/History.tsx`
- `voice_typer/client/src/renderer/src/pages/dashboard/hooks/useDashboardData.ts`

**Fix:** Create `hooks/useLatestRef.ts` exporting `useLatest<T>(value)`; replace the 22 mirrors mechanically; update the AST guard to accept the canonical hook (or assert its usage). Create `useDebouncedEventRefresh(fetch, {events, delay})`; consume in Home/History/Dashboard; move the source-grep test expectation alongside.

**Simplified Fix:** Two small pieces of plumbing that nearly every screen in the app needs were copied by hand into 25 places instead of being written once. We write them once and point everyone at the shared version — with the automated checks updated to know about it.

**Implementation Difficulty:** 🟠 Hard
**Severity:** 🟡 Medium

---

### BP-17 — App.tsx is 605 lines with event business logic in the entry file (E3 breach)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The renderer's entry component claims in its own header to be "pure wiring: hooks, overlays, layout" but is 605 lines (327 code) and contains three inline event handlers with real branching logic: the navigation event handler (consent-field routing plus a legacy-literal override, ~37 lines), the consent-required handler (field validation, a dictation-retry list, and the consent-gate call, ~24 lines), and a download-progress ref-gated update. Twelve other App-level behaviors were already extracted to hooks — these three are the residue that keeps growing the entry file. Related finding in the same file: the consent-field retry list is inlined in App.tsx while the canonical consent-field registry lives in `lib/consentGate.ts` — adding a fifth cloud provider's consent field would silently lose the "retry dictation after Allow" behavior unless someone remembers the fourth parallel list (a functional degradation risk, not just style).

**User Impact:** None today. The risk: a new consent field or navigation rule lands in the entry file by default, growing the least-testable file in the app; and the consent-retry gap is a real future functional bug for cloud users.

**Root Cause:** `voice_typer/client/src/renderer/src/App.tsx:50-605` (header claim :41-43); inline handlers at `:307-344` (navigate), `:385-409` (consent_required), `:429-437` (download_progress); parallel consent list at `:398-402` vs `lib/consentGate.ts:79-87`.

**Gain vs Trade-off:** Extracting `useNavigateEvent`/`useConsentRequiredEvent` hooks (mirroring the existing use*Toast pattern) drops App.tsx to ~230 code lines, matching both the E3 rule and its own header. Exporting `DICTATION_RETRY_CONSENT_FIELDS` from consentGate kills the parallel list. Zero behavior change.

**If We Do It:** The entry file becomes genuinely wiring-only; the consent-retry behavior follows the registry automatically when providers are added.

**If We Don't:** The entry file keeps accreting event logic; the consent-retry landmine stays armed for the next cloud provider.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/App.tsx`
- `voice_typer/client/src/renderer/src/lib/consentGate.ts`

**Fix:** Extract the two handlers into `hooks/useNavigateEvent.ts` + `hooks/useConsentRequiredEvent.ts` (call the existing extraction pattern); export `DICTATION_RETRY_CONSENT_FIELDS` from consentGate and use `.includes(field)`; C-BG-1's visibility-grace effect (:111-151) stays in place untouched.

**Simplified Fix:** The app's top-level file is supposed to be a thin skeleton of wiring, but three chunks of real decision-making logic live inside it. We move each chunk into its own named module, and make the "which dialogs allow retrying dictation" list come from the one authoritative place instead of a private copy.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-18 — Dead 100-line fallback duplicating the ASR utils (7 suppressions die with it)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The Parakeet engine's helper module wraps six imports from a sibling module in a try/except that, on failure, defines six `None` placeholders and ~100 lines of "mirror the pre-migration bodies verbatim" local re-implementations of the same functions. The sibling module lives in the same package — if it were unimportable, this file's own imports would already have failed — so the fallback is unreachable dead code from a completed parallel refactor (its own pragma comment says so). Seven `# type: ignore` suppressions exist solely to support the dead path.

**User Impact:** None. The cost is pure debt: ~100 lines that must be kept behaviorally in sync with the real implementations (and will not be), plus suppression noise in exactly the modules a type-checker should be guarding.

**Root Cause:** `voice_typer/server/parakeet_engine/_helpers.py:25-160` (fallbacks :39-44, dispatch :160-163, pragma "# no cover — defensive fallback during parallel refactor"; asr_utils documented as the canonical home).

**Gain vs Trade-off:** Pure deletion (E15): the module imports the six names directly, keeps the back-compat aliases tests import, and the 7 suppressions disappear. Zero behavior change; deletion recorded in archive/deleted_files.txt.

**If We Do It:** One less duplicated copy of engine-selection logic; the type-checker sees the real signatures.

**If We Don't:** The dead mirror keeps silently rotting next to the real functions it copies.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/parakeet_engine/_helpers.py`

**Fix:** Delete the try/except wrapper, the 6 `None` placeholders, and the 6 `_local_*` implementations; import the 6 names directly from `asr_utils`; keep test-imported aliases. Record the deletion per E15.

**Simplified Fix:** A leftover safety copy of six helper functions — created during a file move that finished long ago — is still sitting in the code, along with seven "ignore the type-checker here" notes that exist only for that copy. We delete the copy.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-19 — The native binaries' `--log-file` diagnostic feature is fully built but never switched on
**Status:** ❌ Not Fixed (investigation only)

**Description:** All three native key-listener binaries (Linux, Windows, macOS) implement `--log-file <path>` — parsing the flag and writing timestamped diagnostic logs (init steps, permission checks, device opens, hook installs). The Python side documents the same contract, carries a ~50-line memoized helper computing the log path, and a state slot for it. But the actual spawn command passes only the binary path and the hotkey — the flag is never passed, and the helper has zero callers. The entire support-diagnostics mechanism for native hotkey problems was built three times (once per OS) and never wired up.

**User Impact:** Support bundles for hotkey problems (the hardest class to diagnose remotely) never contain the native-side trace. When "the hotkey doesn't work" lands, the only native evidence is its stdout lines — the permission/hook details the binaries were built to record are lost.

**Root Cause:** `voice_typer/server/native_hotkeys/_spawn.py:214` (cmd = [binary, hotkey_str] — flag absent), `:284-329` (`_compute_native_log_path` zero callers), `_core.py:176-185` (documented contract); native implementations: `native/linux-key-listener.c:805-816`, `native/windows-key-listener.c:711-722`, `native/macos-key-listener.swift:455-461`.

**Gain vs Trade-off:** Two options: wire the flag (one conditional append to the spawn command — support bundles gain the native trace) or delete the feature (E15 — ~50 lines + 3 docstrings). Wiring is the higher-value option; deletion is the honest one if the trace is unwanted. Either resolves the "built three times, used zero times" inconsistency.

**If We Do It (wire):** Hotkey support cases come with a native diagnostic log, turning the hardest remote-diagnosis class into a readable one.

**If We Don't:** Three OSes carry dead diagnostic code and docstrings promising logs that never appear.

**My Recommendation:** ✅ Implement (wire the flag)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/native_hotkeys/_spawn.py`
- `voice_typer/server/native_hotkeys/_core.py`
- `voice_typer/server/native/linux-key-listener.c`
- `voice_typer/server/native/windows-key-listener.c`
- `voice_typer/server/native/macos-key-listener.swift`

**Fix:** Append `["--log-file", str(path)]` to the spawn command when the helper yields a path (error-tolerant: skip silently if None), OR delete helper + state + docstring claims. VALIDATE ON WINDOWS/MACOS HOST after wiring.

**Simplified Fix:** The keyboard-watcher helper programs on all three operating systems can each write a detailed diagnostic log if asked — but the app never asks. We start asking (or, if the logs aren't wanted, remove the unused machinery).

**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

---

### BP-20 — main.rs is 413 lines, past its ≤~300 wiring-only ceiling (C-ARCH-1)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The Rust host's entry file is 413 lines while its own header claims "~280 lines" and the project's C-ARCH-1 rule caps it at ~300 wiring-only lines. The content is still wiring in substance (bodies are delegated), but the drift is real and self-accelerating: it grew from 280 to 413 through accretion of setup glue plus large history-comment blocks, and three blocks contain inline implementation detail (the panic-payload downcast chain, the VT_START_HIDDEN env handling + window hide, the tray-available atomic store). The sibling finding for the Electron entry file (index.ts, 382 lines, Electron-only) was routed to Won't Fix because that shell is being removed; the Tauri main.rs is the FUTURE runtime, so the drift matters here.

**User Impact:** None today. The risk: C-ARCH-1 exists because this file once regressed to 2,277 lines; a guard that no longer self-enforces (the header lie proves nobody checks) lets the next accretion round continue quietly.

**Root Cause:** `src-tauri/src/main.rs:1-413` (stale header :23; inline blocks :236-246 VT_START_HIDDEN, :297-304 tray_available, :320-354 spawn body, :347-351 panic downcast).

**Gain vs Trade-off:** Extracting the window-bootstrap block (:215-246) and the sidecar-init guarded body (:320-354) into focused modules drops main.rs to ~280-300 and makes the header true. Pure move, no behavior change; wiring audit (E1) after.

**If We Do It:** The rule becomes self-enforcing again — the file visibly fits its budget and the header tells the truth.

**If We Don't:** The drift continues toward the historical regression the rule exists to prevent.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/main.rs`

**Fix:** Extract (a) the main-window bootstrap block into a focused module alongside the existing window command modules, and (b) the sidecar-init task body (including the catch_unwind wrapper) into `sidecar::spawn::initialize_sidecar_guarded(&app_handle, state)`; update the header. Cross-references BP-21 (same rule, renderer side — separate agents, never merged).

**Simplified Fix:** The main file of the Windows/Mac/Linux host program is supposed to stay short — a table of contents, not a novel. It has quietly grown past its limit. We move two chunks out into their own modules, restoring the file to its intended size.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-21 — Startup timeline instrumentation is dead on the Tauri runtime (the future only runtime)
**Status:** ❌ Not Fixed (investigation only)

**Description:** The one log line that attributes startup latency ("Launch timeline: electron boot Xs, backend init Ys") is computed from two environment markers that ONLY the Electron main process sets. The Tauri host sets neither — so on the production-future runtime the line silently no-ops, and no benchmark measures the Tauri end-to-end startup path either (the worker bench excludes interpreter + app-construction time before the WS server binds). When a user asks "why was my cold start slow?", the answerable evidence exists only on the shell being retired.

**User Impact:** None functionally. Support impact: the primary startup-slowness diagnostic is absent on the runtime every user will soon be on.

**Root Cause:** `voice_typer/server/startup_timeline.py:32-58` (derives from `VOICE_TYPER_BOOT_EPOCH_MS` / `VOICE_TYPER_SPAWN_EPOCH_MS`); setters exist only in `voice_typer/client/src/main/index.ts` + `python/start-python.ts`; rg over `src-tauri/src` finds no setter. (Python side already handles absent markers — zero Python changes needed.)

**Gain vs Trade-off:** Timing-only change in the Tauri host: set both env vars immediately before spawning the sidecar (alongside the existing `VOICE_TYPER_CONFIG_DIR` env additions — the spawn contract already adds env vars there). No behavior change; no C-TOKIO-1 involvement.

**If We Do It:** The launch-timeline line works on the Tauri runtime; "why was startup slow" becomes answerable from the log.

**If We Don't:** Startup-perf questions on the production runtime have no attribution evidence.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/sidecar/spawn.rs`
- `src-tauri/src/sidecar/lifecycle.rs`
- `voice_typer/server/startup_timeline.py`

**Fix:** In the Tauri sidecar spawn path, set `VOICE_TYPER_BOOT_EPOCH_MS` (host start, once) and `VOICE_TYPER_SPAWN_EPOCH_MS` (immediately before spawn) into the child env. Cross-references T-1 (host-parity family — opposite direction; never merged).

**Simplified Fix:** The app writes a helpful "here's how long each startup phase took" log line — but only the old Electron shell feeds it the timestamps it needs. We make the new Tauri shell provide the same timestamps so the line works everywhere.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-22 — Electron decommission checklist (gated on T-1)
**Status:** ❌ Not Fixed (investigation only — documentation artifact)

**Description:** The project carries two complete host shells: the Electron main process (~12,300 lines) and the Tauri host (~19,000 lines), each with its own Python-spawn/restart/relaunch machinery, and the Python backend keeps both transports plus five Electron-only support modules. The project's stated direction (review.md T-1) is Tauri-only. No code change is proposed now — Electron cannot be removed until T-1's Windows-host validation completes. What is missing is the explicit decommission checklist: the gated list of what gets deleted (Electron `main/`, `transport_tcp.py`, the `electron_*` modules, the two re-export shims) and in what order, so a future session executes a plan instead of re-deriving one. The two crash-loop breaker files (restart_history.json / restart_counter.json) must NOT be merged (C-PERSIST-4) — they die with their respective shells.

**User Impact:** None directly. Until decommission, every host-level fix lands twice (the AGENTS.md constraint history documents this cost: C-WS-1/2 bugs were found by fixing one side of the mirror), halving fix velocity on user-facing breakage.

**Root Cause:** Mid-flight migration (ADR-0020); deletion is correctly gated on T-1 host validation — the checklist simply does not exist yet.

**Gain vs Trade-off:** A documentation artifact now; when T-1's validation completes, deletion becomes a mechanical, safe execution instead of a risky ad-hoc purge. No trade-off.

**If We Do It:** The path to single-shell is written down and gated; future sessions stop paying the dual-host tax sooner.

**If We Don't:** The dual-shell tax persists indefinitely with no written exit plan.

**My Recommendation:** ✅ Implement (the checklist document only)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/main/`
- `voice_typer/server/ipc/transport_tcp.py`
- `voice_typer/server/electron_launcher.py`
- `review.md` (T-1)

**Fix:** Author the checklist (gated on T-1 host validation): delete Electron main tree + Python Electron-support modules + transport_tcp (after WS parity confirmed) under E15 rules, with the affected test files enumerated per item. Do NOT merge the circuit-breaker files (C-PERSIST-4).

**Simplified Fix:** The app currently ships with two complete "outer shells" (the old Electron one and the new Tauri one) and keeps both alive, which doubles the work for every outer-shell fix. We write down the exact, safe removal plan for the old shell so it can be executed the moment the new one is fully validated.

**Implementation Difficulty:** 🟢 Easy (document; the deletion itself is a separate gated task)
**Severity:** 🟡 Medium

---

### BP-23 — Settings page deep-link effects are ~85% duplicated twins
**Status:** ❌ Not Fixed (investigation only)

**Description:** The Settings page contains two large useEffects that implement "scroll to a specific row and highlight it" — one for consent deep-links (navigating from the Home error state to a privacy toggle) and one for search deep-links. They share ~85% of their logic: a one-shot guard, a bounded retry loop (up to 60 attempts, 50ms apart) waiting for the row to render, the same scrollIntoView call, the same 2600ms highlight-ring timer, and the same cleanup. They differ only in how they find the target row and how the ring is applied. They have already drifted subtly in retry bounds and ring mechanism.

**User Impact:** None today. The cost: a fix to the shared machinery (e.g. the 3-second safety net covering both) requires understanding two near-twins, and divergence between them grows with each edit.

**Root Cause:** `voice_typer/client/src/renderer/src/pages/Settings.tsx:233-272` (consent) vs `:282-331` (search); shared safety net at `:336-344`.

**Gain vs Trade-off:** One parameterized helper (matcher + ring applier as parameters), byte-identical behavior per path, pinned by the existing settings tests. Pure refactor.

**If We Do It:** The deep-link machinery has one home; fixing the retry logic once fixes both entry points.

**If We Don't:** The twins keep drifting; each new deep-link type copies one of them again.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Settings.tsx`

**Fix:** Extract `scrollToRowWithHighlight({target, matchFn, onFound, ringLifetime})` local to Settings.tsx (or pages/settings/lib/), parameterized by the row matcher; both effects call it with their matcher; behavior byte-identical.

**Simplified Fix:** Two nearly identical blocks of Settings-page code both implement "jump to a row and flash a ring around it" — differing only in how they find the row. We merge them into one shared routine with the "how to find it" part supplied by each caller.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

## BP Session — Medium/Low Priority (batched)

> Smaller items batched by family so a fixing session can clear each batch in one focused pass. All are evidence-verified; none block anything.

### BP-24 — Documentation/comment/log-format hygiene sweep (16+ sites)
**Status:** ❌ Not Fixed (investigation only)

**Description:** A batch of documentation defects that actively mislead: seven Rust comment scaffolds left mangled after a task-ID strip (e.g. `dispatch.rs:9` `// (): poison-safe Mutex helper`, `ws.rs:207` "Finding ) covering" — ungrammatical); a dangling `(see )` in sidecar_ws.py:300; the level-monitor docstring claiming a 5-second idle timeout when the code uses 60 (`monitoring.py:11-14` vs `_state.py:241` — 12× off on a battery-relevant knob); four log lines violating the C-LOG-2 duration convention (the CUDA warm-up completion line lost its duration suffix when extracted to `transcription_cuda_probe.py:188`; the model-load lines at `transcription.py:552-558`, `parakeet_engine/_load.py:51-56, :307-312` use ad-hoc `%.1fs` formats that break for >60s loads and are not grep-summarizable — hard rule violations); session-ID debris in code (TY-11 thread name `model_manager/_lifecycle.py:253`, TY-5 log string `recording/device_manager.py:357`); tray-cluster tag debris (mangled docstrings, "#13:", "P4 #30", "Finding #3", empty-paren tags at 10+ sites per BP-14); a stale "Undo Last" tray-menu docstring line (tray_menu.py:552 — the menu the rule C-TRAY-2 says must never have it); and a stale "three native processes run at once" comment (`native_hotkeys/_spawn.py:204-213`) describing an architecture that pooling already replaced.

**User Impact:** Support and future maintenance: log durations that can't be summed; docs that describe removed behavior; two hard rule violations (C-LOG-2, C-STYLE-1/E4).

**Root Cause:** Multiple incomplete cleanup passes (tag strips, extractions) each leaving residue.

**Gain vs Trade-off:** Pure documentation/log-format fixes; the C-LOG-2 sites are mechanical `format_duration()` adoptions. No behavior change (log content only).

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs`, `src-tauri/src/state.rs`, `src-tauri/src/sidecar/ws.rs`, `src-tauri/src/commands/sidecar_cmds/shutdown.rs`
- `voice_typer/server/sidecar_ws.py`
- `voice_typer/server/level_monitor/monitoring.py`
- `voice_typer/server/transcription.py`, `voice_typer/server/transcription_cuda_probe.py`, `voice_typer/server/parakeet_engine/_load.py`
- `voice_typer/server/model_manager/_lifecycle.py`, `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/tray_menu.py`, `voice_typer/server/tray_state.py`, `voice_typer/server/native_hotkeys/_spawn.py`

**Fix:** Per-site: restore comment subjects; fix the 60.0 docstring; import `format_duration` (voice_typer.server.duration) at the four C-LOG-2 sites and splice per the rule (leading space, no ad-hoc formats); rename the TY-11 thread and drop the TY-5 prefix; rewrite the mangled tray docstrings by purpose; delete the "Undo Last" doc line; rewrite the three-processes comment.

**Simplified Fix:** A cleanup pass collects every place where a comment or log message describes something that isn't true anymore — wrong time limits, references to deleted items, mangled sentences, missing timing info — and makes them tell the truth.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

---

### BP-25 — Type-checker suppression inventory + mypy overrides + noqa consolidation
**Status:** ❌ Not Fixed (investigation only)

**Description:** The repo maintains inline `# type: ignore` suppressions at 14+ audio/ASR/DB sites while ALSO maintaining a canonical pyproject overrides block for untyped third-party modules — but onnx_asr / onnxruntime / tokenizers were never added to that block (faster_whisper is listed as an exact module while one site imports its submodule, which the exact-module entry does not cover). One suppression in the DB layer is provably stale (the attribute it "ignores" is declared and initialized; sibling files access it unsuppressed). Separately, the service layer carries 8 scattered `# noqa` private-attribute workarounds (B009/B010) around three app attributes that ADR-0008 deliberately excluded from the protocol — the same workaround copy-pasted per call site instead of one accessor.

**User Impact:** None. The cost: suppressions mask real type drift on the audio/ASR hot paths, and new imports of these libraries re-invent inline suppressions instead of following the config-first pattern.

**Root Cause:** 14 verified inline sites (vad.py:147, gtcrn_backend.py:103, noise_suppressor.py:278 [import-not-found], disconnect_handler.py:126, qwen_onnx_model.py:192/299/333, parakeet_engine/_load.py:44/45/76, _helpers.py:39-44/96, asr_utils.py:153, retention.py:194); pyproject.toml:822-834 overrides block; 8 noqa sites (service/onboarding.py:215, service/config_service.py:277/368/370, service/template.py:55, service/microphone_test.py:47/92, service/diagnostics.py:79).

**Gain vs Trade-off:** Moving library-level ignores into the auditable config block follows the repo's own ERR-ERR-006 rule; requires verifying (per that rule's comment) that no stub packages exist for those libraries first. The stale retention.py suppression is removed after confirming pyrefly passes. The AppProtocol accessors consolidate 8 workarounds into one.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `pyproject.toml`
- `voice_typer/server/history_db_internals/retention.py`
- `voice_typer/server/service/` (6 files)

**Fix:** Add `onnx_asr`, `onnxruntime`, `tokenizers`, `faster_whisper.*` to `[[tool.mypy.overrides]]` after verifying no stub packages exist; delete the 14 inline ignores; remove retention.py:194's stale ignore (align the typing import with crud_writes.py if needed); introduce one accessor pair (or `AppProtocolInternal`) owning the three private attributes and delete the 8 noqa markers.

**Simplified Fix:** The code tells the type-checker to "look away" in about twenty places — some legitimately (third-party code without type info), some just stale. We move the legitimate ones into the project's single config list, delete the stale ones, and give the repeated workarounds one shared home.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

### BP-26 — Server DRY micro-batch (6 small duplications)
**Status:** ❌ Not Fixed (investigation only)

**Description:** Six small server-side duplications, each verified: (1) the auth-timeout constant defined twice (5.0 in both transports, with a comment requiring manual sync — the shared home `ipc/auth.py` already exists); (2) the 12-column history SELECT projection duplicated verbatim in 8 SQL statements (one new column = 8 synchronized edits); (3) the `TAURI_SIDECAR` runtime-mode guard inlined at 5 sites across 3 modules while a canonical helper exists unused; (4) `SYSTEM = sys.platform` defined in the package `__init__` while a sibling module documents itself as the single owner (tests must patch both); (5) the CREATE_NO_WINDOW subprocess flag pattern inlined 3× with near-identical comments; (6) the Linux process-walker imports its depth constant from the macOS bundle-id module (importing the Linux path pulls macOS code). Plus a per-word uncompiled regex in vocabulary_automation where the identical pattern is already precompiled in a sibling module.

**User Impact:** None. All are drift risks: each duplicated constant/pattern can silently diverge (the auth timeout comment itself warns this).

**Root Cause:** (1) sidecar_ws.py:301 vs ipc/transport_tcp.py:473; (2) history_db_internals/search.py (8 blocks, :300-658); (3) tray_menu.py:461/503, tray_lifecycle.py:108, tray.py:149/273 vs tray_notifications.py:248-252; (4) server_platform/__init__.py:110 vs platform_flags.py:43-49; (5) autostart.py:336-341, _autostart_windows_uninstall.py:157-163, _autostart_windows_sweep.py:209-211; (6) linux_proc_walk.py:34; vocabulary_automation.py:430/405.

**Gain vs Trade-off:** Pure single-sourcing — constants move to their documented owners, patterns to one helper. No behavior changes.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/ipc/auth.py`, `voice_typer/server/sidecar_ws.py`, `voice_typer/server/ipc/transport_tcp.py`
- `voice_typer/server/history_db_internals/search.py`
- `voice_typer/server/tray_types.py` (helper home)
- `voice_typer/server/server_platform/__init__.py`, `platform_flags.py`, `linux_proc_walk.py`, `macos_bundle_id.py`
- `voice_typer/server/vocabulary_automation.py`

**Fix:** Move the auth timeout to ipc/auth.py (import in both transports); extract `_LIST_COLUMNS_SQL` (+ `t.`-prefixed variant) in search.py; hoist `_is_tauri_sidecar()` to tray_types.py and consume at the 5 sites; replace `__init__.py`'s SYSTEM with a re-export; extract the subprocess no-window kwargs helper; move `_MAX_CHAIN_DEPTH` to a neutral module; import the precompiled token pattern.

**Simplified Fix:** Half a dozen places where the same small fact or recipe is written down twice (a timeout value, a column list, a mode check, a flag pattern) get consolidated to one authoritative copy each.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

### BP-27 — Renderer DRY micro-batch (9 small duplications)
**Status:** ❌ Not Fixed (investigation only)

**Description:** Nine verified small renderer duplications: the local `CallFn` type is declared 15× across hooks while the canonical `PythonCall` exists in the bridge; the templates page borrows vocabulary/history i18n keys for its export toasts (editing History's copy changes what Templates users see); the identical a11y biome-ignore pair + row-click wrapper is copy-pasted in 5 selectable-row components; the Tauri-bridge install gate (with a ~30-line rationale comment) is byte-identical in both entry files; the mic-default reconciliation is duplicated in-file in the onboarding wizard; the history export hook carries a dead `records` parameter and the only un-rationaled cast in the renderer; a no-op setState sits in the level-bar unmount cleanup; the route lazy-import registry is maintained twice (PageSwitch's 10 imports vs prefetch's 9 entries) with a stale comment; and the empty-stats literal appears 4×.

**User Impact:** None. All drift risks; the i18n borrowing is the most user-adjacent (Templates copy silently changing with History copy).

**Root Cause:** (1) CallFn ×15 (useVocabulary.ts:55, useTemplateDialog.ts:23, useTemplates.ts:35, useFirstRecordingCelebration.ts:12, useGlobalKeyboardShortcuts.ts:51, useOnboardingComplete.ts:22, useModelDownload.ts:67, useModelFolder.ts:48, useModelSelection.ts:36, useModelConfig.ts:45, useCloudProviders.ts:61 + the 4 originally cited); (2) useTemplateImportExport.ts:76/90/92/99; (3) 5 row components; (4) main.tsx:41-47 vs bubble-main.tsx:34-40; (5) useOnboardingWizard.ts:253-265 vs :458-468; (6) useHistoryExport.ts:59/:149; (7) RecordingLevelBar.tsx:51; (8) PageSwitch.tsx:17-25 vs prefetch.ts:24-36, routes.ts:28; (9) empty-stats ×4.

**Gain vs Trade-off:** Single-sourcing with behavior preserved; the i18n fix adds templates.* keys to all 8 locales (C-I18N-1 compliant, genuinely translated per C-I18N-2).

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/` (11 CallFn sites), `lib/python-bridge/usePython.ts`
- `voice_typer/client/src/renderer/src/pages/templates/hooks/useTemplateImportExport.ts` + 8 locale files
- `voice_typer/client/src/renderer/src/components/common/` (new SelectableRow)
- `voice_typer/client/src/renderer/src/main.tsx`, `bubble-main.tsx`, `lib/tauri-bridge/install.ts`
- `voice_typer/client/src/renderer/src/pages/onboarding/hooks/useOnboardingWizard.ts`
- `voice_typer/client/src/renderer/src/pages/history/hooks/useHistoryExport.ts`
- `voice_typer/client/src/renderer/src/router/`

**Fix:** Export `PythonCall` from the python-bridge barrel, delete 15 local copies; add templates.export* keys in 8 locales; extract SelectableRow owning the a11y pair; export `ensureTauriBridgeInstalled()` with the single canonical comment; extract `resolveDefaultMic`; drop the dead param + document/fix the cast; delete the no-op cleanup; build one PAGE_LOADERS map consumed by both router files + fix the stale comment; one EMPTY_STATS constant.

**Simplified Fix:** Nine places where the renderer wrote the same small thing twice — a type definition, a translated message, a row-click wrapper, a startup gate, and friends — each get one shared copy.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

### BP-28 — Renderer performance micro-batch (3 items)
**Status:** ❌ Not Fixed (investigation only)

**Description:** Three verified performance items: (1) the onboarding wizard makes 5 sequential IPC round-trips before first content (start → config → microphones → hotkey presets → model options), where the Dashboard page already demonstrates the correct `Promise.all` pattern — the wizard's first-run content is delayed by the SUM of 5 round-trips; (2) the history export loop pages with OFFSET (each page forces the database to skip past all previous rows — ~50k row visits for a 1,000-row export; O(pages × offset)) while the page-cache hook uses proper cursor (keyset) pagination — two divergent pagination strategies for the same data; (3) the sound-feedback AudioContext is never closed when the user toggles the feature off at runtime (documented limitation) — the audio device/thread stays held until app restart.

**User Impact:** (1) first-run onboarding content arrives measurably later than it could; (2) large-history exports are slower than needed (masked by the save dialog); (3) an idle audio context is held after the user turns sounds off.

**Root Cause:** (1) useOnboardingWizard.ts:198-276; (2) useHistoryExport.ts:85-126 vs useHistoryCache.ts:280-316; (3) useSoundFeedback.ts:122-159 (limitation documented :142-148).

**Gain vs Trade-off:** All three are contained, behavior-preserving-elsewhere fixes. The AudioContext one is a documented deferral — implement only if valued (it holds an audio thread the user asked to disable).

**My Recommendation:** ✅ Implement (1) and (2); 🟡 Try-and-revert (3)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/client/src/renderer/src/pages/onboarding/hooks/useOnboardingWizard.ts`
- `voice_typer/client/src/renderer/src/pages/history/hooks/useHistoryExport.ts`
- `voice_typer/client/src/renderer/src/hooks/useSoundFeedback.ts`

**Fix:** (1) `Promise.all` the four content fetches after `onboarding_start`, applying config prefill before the mic reconciliation; (2) thread cursor params through the export loop (derive from last row, same defensive fallback), optionally share the endpoint selector; (3) subscribe to config_changed for sound_feedback_enabled and close/re-init the AudioContext on flip.

**Simplified Fix:** The first-run setup screen asks the backend five questions one at a time instead of all at once; the history export reads pages the slow way the app already knows how to avoid; and the sound-effects audio engine stays powered on after the user switches sounds off. All three get the obvious fix.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

### BP-29 — Cloud transport: false pooling comments + uninterruptible retry waits
**Status:** ❌ Not Fixed (investigation only)

**Description:** Two verified cloud-layer items. (1) Three files claim the stdlib URL-opener provides connection pooling ("reuses TCP connections like requests.Session") — the stdlib provably opens a NEW connection per request and sends `Connection: close` (verified against the CPython source). Every cloud transcription and LLM polish request therefore pays a full TCP+TLS handshake that the comments claim is already amortized. (2) During rate-limit backoff (Retry-After honored up to 60s) and retry backoff, the engine sleeps with plain `time.sleep` — the user's ESC-abort is only checked at the TOP of the next attempt, so cancelling a cloud dictation can lag up to 60 seconds despite the abort machinery existing.

**User Impact:** (1) cloud users pay ~100-300ms extra per request (network-dependent) — and future maintainers may "optimize" around pooling that doesn't exist; (2) pressing Escape during a rate-limited cloud retry appears to do nothing for up to a minute.

**Root Cause:** (1) security/http_safety.py:203-206, cloud/_transport.py:21-22, cloud/_engine.py:543-545 (false comments; stdlib `AbstractHTTPHandler.do_open` behavior); (2) cloud/_engine.py:459/494 (`time.sleep`) vs abort check at :399; cap at cloud/_retry.py:90.

**Gain vs Trade-off:** (1) Minimum fix is correcting the comments (truth first); an optional stdlib keep-alive wrapper is W2-qualified (neither requests nor urllib3 is a dependency). (2) `abort_event.wait(timeout)` replaces each sleep — the Event returns early when abort fires. Both are contained.

**My Recommendation:** ✅ Implement

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/security/http_safety.py`
- `voice_typer/server/cloud/_transport.py`
- `voice_typer/server/cloud/_engine.py`
- `voice_typer/server/cloud/_retry.py`

**Fix:** Correct the three pooling comments (drop the claim); replace `time.sleep(wait)` with `if self._abort_event.wait(timeout=wait): raise CloudEngineError(...aborted...)` at both sites. Optional second stage: per-host persistent `http.client.HTTPSConnection` wrapper.

**Simplified Fix:** Comments in the cloud code promise connection reuse that the standard library doesn't actually provide, so every cloud request pays a fresh handshake — we fix the comments and optionally add real reuse. And when the cloud service says "wait before retrying", the app sleeps rigidly instead of waking early when the user cancels — we make the wait interruptible.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

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

---

### BP-31 — Concurrent model download requests are refused rather than queued
**Status:** ❌ Not Fixed (investigation only)

**Description:** The download manager deliberately allows only one gateable model download at a time (the single-flight guard is what makes pause/cancel reliable) — but a second download request arriving while one is active is outright REFUSED with an error toast ("Another model download is already in progress") rather than queued. Downloading several models from the Models page means clicking, getting an error, and manually retrying after each completes. The refusal is documented in-code as deliberate; the queue is the missing UX layer.

**User Impact:** Multi-model setup requires manual sequential retries with an error toast between each — feels broken for a first-session user downloading 2-3 models.

**Root Cause:** `voice_typer/server/service/model/_downloads.py:386-404` (single-flight guard; refusal response), :882-884.

**Gain vs Trade-off:** A one-slot pending queue (next-request-wins) or a UI-level "queued" state. Constraint: keep the shared transfer gate (per-download gates would be the bigger refactor and risk the pause/cancel reliability the gate provides). Alternatively a renderer-side "queue the click, auto-retry when the current finishes" preserves the backend contract entirely.

**My Recommendation:** 🟡 Try-and-revert (renderer-side queue first — smallest blast radius)

**Progress:** `None yet.`

**Related Files:**
- `voice_typer/server/service/model/_downloads.py`
- `voice_typer/client/src/renderer/src/components/models/` (queue UI)

**Fix:** Preferred first slice: renderer keeps a one-deep local queue — a second Download click while one is active shows "queued" state and auto-fires on the download-complete event; backend unchanged. Backend alternative: serialize requests through a one-slot pending queue with the existing gate semantics.

**Simplified Fix:** If you try to download a second speech model while the first is still downloading, the app shows an error and makes you click again later. We make that second request wait its turn automatically instead.

**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

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
