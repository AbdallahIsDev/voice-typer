## High Priority

These items are the highest-priority remaining work for the project. They block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

> **Won't Fix tasks live in `WONT_FIX.md`**: deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

### CI-1: Fix all GitHub Actions CI pipeline errors and warnings

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

### BP-78: Rule-text drift: C-MODELS-2 and C-MIC-12 no longer describe the shipped code (user adjudication)
**Status:**  Not Fixed SKIPPED (re-skipped 2026-09-10 FV session: same AGENTS.md conflict. The user is the only one who can edit the rules; no agent action taken). SKIPPED (FV session 2026-09-07): SKIPPED: BP-78 ΓÇö conflicts with AGENTS.md `Hard "Don'ts"`: "The user is the only one who can edit these rules" (entry itself: REQUIRES USER ACTION). Recommendation carried to the Final Report: user should update C-MODELS-2 token values (w-24/gap-2/text-xs, h-3 w-3) and either refresh C-MIC-12's text to the binary-recolor contract or order a revert to the ΓÜá-glyph contract.

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

## 🚫 E. Cannot Verify (needs real host)

**19 findings require Windows / macOS / Linux desktop runtime**, they cannot be
verified or fixed on this Linux CI sandbox and must be validated on real hosts
(see `docs/migration/windows-validation-runbook.md`,
`docs/migration/macos-validation-runbook.md`,
`docs/migration/linux-validation-runbook.md`). These items are unverifiable, not
unfixable: re-check them on real hardware before marking anything done.

### Windows/macOS host validation. All fixes tested on Linux sandbox only
**Status:** ❌ Cannot Verify (needs real host), re-verified 2026-08-30: all fixes are tested on the Linux CI sandbox only. Real-host validation required for Win32 console handler, macOS clipboard restore, and native hotkey binaries per the platform validation runbooks.
**Description:** Many platform-specific fixes (Win32 console handler, macOS clipboard restore, native key-listener binaries) have been implemented but only tested on a Linux sandbox. They must be validated on real Windows/macOS hardware.
**User Impact:** Platform-specific regressions may exist on Windows/macOS that are invisible on Linux.
**Root Cause:** No real Windows/macOS CI runners available in this sandbox.
**Progress:** Blocked on host access.
**Related Files:** `docs/migration/windows-validation-runbook.md`, `docs/migration/macos-validation-runbook.md`
**Fix:** Run the platform validation runbooks on real Windows and macOS hosts.
**Severity:** 🔴 High
**Priority:** P0

### XPLAT-12: Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: ❌ Not Fixed, VALIDATE-ON-WINDOWS-HOST: Windows-on-ARM host validation required, Nuitka cross-compile + aarch64 freeze must be tested on real Windows ARM hardware
> - **2026-08-24 audit:** scaffold inert BY DESIGN, C-CI-4 gates the matrix leg (no public windows-11-arm runner; manual dispatch only per ADR-0020 §15). Action requires ARM hardware + explicit policy change; never enable blindly.
- **Description**: Code path is complete but `windows-11-arm` runner not yet GHA-available.
- **Note**: Per ADR §4.1, explicit deferral.
- **Effort**: 🔴 **HIGH**, Requires Windows-on-ARM runner access not available in this sandbox. Cannot complete.

### S1-CR-146, `StartupWMClass=Voice Typer` may not match Tauri window class
**Status:** ❌ Not Fixed, out of file scope + host-validation required (target file voice-typer.desktop.template not in scope; fix requires running Tauri app + xprop WM_CLASS on real Linux desktop)
> - **2026-08-24 audit:** plausible-true (space+case in productName makes default tao WM_CLASS match unlikely vs binary prgname `voice-typer-tauri`): verify via `xprop WM_CLASS` on a real Linux desktop, then set the matching class in `src-tauri/voice-typer.desktop.template`.
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

- **WM-6 / WM-7 / WM-8 / WM-11 / WM-12 / WM-13**, test-suite runs on real Windows/macOS/Linux desktop runtimes (only Linux-sandbox results exist so far).
- **WM-14**: Windows `taskkill` behavior. Tracked in worklog / GP-FIX sessions (no entry in this file); requires a real Windows host.
- **GP-7**: macOS notarization. Tracked in worklog / GP-FIX sessions (no entry in this file); requires a real macOS host with Developer ID + notary credentials.
- **GP-135**: cross-platform native binaries. Tracked in worklog / GP-FIX sessions (no entry in this file); requires building + running the native key-listener binaries on each real OS.
- **VT-1**: Windows host validation (config warnings, timeout utils, tray event-loop degradation from the `voice-typer` terminal run). Tracked in worklog / GP-FIX sessions (no entry in this file); requires a real Windows host.
