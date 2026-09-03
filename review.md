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

### TR-4 - Tauri application icon is dark-on-dark: it does not adapt to the system theme
**Status:** 🟡 Investigated (2026-09-02, Windows host) — root cause VERIFIED: every opaque pixel in the bundled icons is pure black (meanLuma=0), and Windows never theme-tints taskbar/Alt-Tab window-class icons; Tauri v2 has no per-theme icon support (web-verified). Candidates: (1) RECOMMENDED — light-contoured/dual-tone icon redesign (one asset reads on both themes; fixes taskbar + open-task view + Alt-Tab; zero code); (2) runtime setIcon() theme swap (window/taskbar only while running; pinned shortcuts stay static; cache quirks); (3) AUMID SetIconOverride plumbing (fragile, not recommended). No icon/config files modified — the fix is a design asset decision for the user.
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
