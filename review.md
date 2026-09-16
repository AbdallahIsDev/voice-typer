## High Priority

These items are the highest-priority remaining work for the project. They block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

> **Won't Fix tasks live in `WONT_FIX.md`**: deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

### START HERE — MO investigation queue ranked (Wave 4 handoff index)

Investigation session prefix `MO` (2026-09-15). **FIX_EXISTING pass 2026-09-15:** top user-visible VALID items implemented (see Status ✅ Fixed (MO session...)). Remaining work is incremental architecture (MO-1/2/3), product-decision items (MO-66), and host-only validation.

Ranked highest-value first:

| Rank | ID | Why first |
|------|-----|-----------|
| 1 | **MO-66** | Settings shows "key configured" for unset API keys (empty-string redacted). User-visible correctness. Fix reverses a deliberate security tightening for `""` only — product decision required.
| 2 | **MO-90** | Release-mode sidecar can leak a zombie holding the single-instance mutex.
| 3 | **MO-3** | RecordingLifecycle 1373-line class on core dictation path — highest architecture regression risk.

**Bundles:** MO-102+MO-105 (renderer capture); MO-104 then MO-108 (tee, then coverage).

> **Freeze-override note (2026-09-15):** the ⏸️ Electron/TCP freeze marks were applied while implementers worked from pre-mark state (parallel-session collision). MO-81/82/87/88/92/93 landed as docs + behavior-preserving hygiene with tests — ACCEPTED, no revert (outcomes match the safe recipes). MO-86 freeze HELD (registry untouched). MO-8 stays rescoped to `apply_config` only; the TCP leg remains deferred. MO-8/MO-81/MO-82/MO-87/MO-88/MO-92/MO-93 sections removed as verified-fixed 2026-09-15 (TCP-leg deferral now anchored at MO-11/MO-86).

> **Accepted untracked (MO session 2026-09-15, no prior review entry — KEPT, tested):** `COMMAND_COSTS` hikes (`delete_model:50`, `restart/quit:100`, mic/level start 20, `save_*`:10 — `microphone_test_read_audio` stays 1 per C-MIC-19); ErrorBoundary + ConnectionStatusScreen calm-card restyles + sonner theme tokens; mic-test 5-min UI TTL expiry. Each is reasoned, covered by tests, and consistent with the Tauri direction.

**Coverage note:** Reliability was thin (only MO-90 + host-validation). Crash-recovery lifecycle / watchdog / circuit-breakers were checked in Wave 3 negatives and found clean, but not a dedicated deep wave.

**CAUTION:** Do not implement MO-24 (Won't Fix BP-WF-6), MO-72 (merged into MO-63), MO-45 as a C-UI-10 violation (it is not), FIELD-1 (out of scope), or anything in `WONT_FIX.md`.

### CI-1: Fix all GitHub Actions CI pipeline errors and warnings

**Status:** Partial (FV session 2026-09-07; Wave 4 2026-09-15: Description/Fix text aligned to Status — Node-24 pin work is DONE, do not re-do). GP-66 + GP-70 edits verified landed (commit 91990ee8, user-approved C-CI-2 override); all action pins audited at Node-24 majors. REMAINING: validated full CI re-run (manual dispatch, green) — VALIDATE ON HOST.
**Description:** Remaining work is end-to-end validation of the already-updated workflows, plus confirming GP-66 (macOS binary skip) and GP-70 (`codesign --verify` in tauri-macos-build.yml, under the recorded user-approved C-CI-2 override) behave correctly on a real dispatch. Action-pin Node-24 migration is complete — do not re-audit `upload-artifact@v5`/`setup-uv@v6` (those pins are already @v6/@v7).
**User Impact:** Red CI blocks merges and masks real regressions; unsigned binaries ship to SmartScreen.
**Root Cause:** Workflows written at different times; pins and signing gates were updated piecemeal; full green dispatch not yet recorded.
**Progress:** Partial — Node-24 pins done; GP-66/GP-70 landed; validation run outstanding.
**Related Files:**
- `.github/workflows/build.yml`
- `.github/workflows/tauri-windows-build.yml`
- `.github/workflows/tauri-macos-build.yml`
- `.github/workflows/tauri-linux-build.yml`
- `.github/workflows/tauri-build.yml`
- `.github/workflows/client-ci.yml`
- `.github/workflows/codeql.yml`
- `scripts/build/build_tauri_all.sh`

**Fix:** 1) Do NOT re-do Node-24 pin migration (already complete). 2) Confirm GP-66 skip behavior on a real macOS dispatch. 3) Confirm GP-70 `codesign --verify` (requires the existing user-approved C-CI-2 override — do not edit tauri-*-build.yml without that approval). 4) Trigger a full manual dispatch on main and confirm green. 5) Check logs for any remaining deprecation warnings.
**Severity:** High
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

**19 findings require Windows / macOS / Linux desktop runtime.** The
headless-provable slices were fixed + tested 2026-09-15 (4-lane wave, see
per-item notes); the interactive cores still need real hosts (see
`docs/migration/windows-validation-runbook.md`,
`docs/migration/macos-validation-runbook.md`,
`docs/migration/linux-validation-runbook.md`). New automation:
`.github/workflows/host-validation.yml` (manual dispatch, scope
all/windows/macos/linux; undispatched as of this edit) + contract pins
`tests/tauri/test_host_validation_workflow.py`. These items are partially
verifiable headless, not fully fixable: re-check the noted host
observations before marking anything done.

### Windows/macOS host validation. All fixes tested on Linux sandbox only
**Status:** ⚠️ Partial (2026-09-15 wave): headless slices fixed + green headless (signal-handler escalation counter, `binary_path` dead-code cleanup, host-validation workflow import fixes, manifest docstring; focused runs green, ruff + branding clean). Live behavior still needs real hosts per the runbooks.
**Description:** Many platform-specific fixes (Win32 console handler, macOS clipboard restore, native key-listener binaries) have been implemented but only tested on a Linux sandbox. They must be validated on real Windows/macOS hardware.
**User Impact:** Platform-specific regressions may exist on Windows/macOS that are invisible on Linux.
**Root Cause:** No real Windows/macOS desktop session in this sandbox; GHA hosted runners cover only the headless subset.
**Progress:** Headless subset automated in `.github/workflows/host-validation.yml` (undispatched; 2026-09-15 search-first wave extended it: Windows kill-path contracts + safe-handler routing probes, macOS `swiftc` compile + smoke, bundle parity via `plutil`, toolchain presence; contract pins now 8). Real defects fixed headless: `_signal_handler` never incremented `_signal_count`, so second-signal `os._exit(1)` was dead code (one-line fix + `tests/test_signal_delivery_count.py`, 3 passed); `TerminateProcess`-raise handle leak in `teardowns/electron.py` (`try/finally`) + discarded `taskkill` exit now debug-logged (`electron_launcher.py`), covered by 4 new tests; macOS `ClipboardSnapshot` capture/restore edge cases pinned (`tests/test_clipboard_macos_capture.py`, 8 passed) + bundle preflight (`tests/tauri/test_macos_bundle_preflight.py`, 9 passed). Aggregate focused run 129 passed; ruff + branding clean. Live gates blocked on host access + manual dispatch. Known pre-existing (not introduced, left to owner): Windows-fallback teardown test passes solo but fails after controller tests (early `is_windows` stub bind in `teardowns/electron.py`).
**Related Files:** `docs/migration/windows-validation-runbook.md`, `docs/migration/macos-validation-runbook.md`
**Fix:** Run the platform validation runbooks on real Windows and macOS hosts.
**Severity:** 🔴 High
**Priority:** P0

### XPLAT-12: Windows-on-ARM scaffolded but unvalidated
- **Severity**: Low
- **Status**: ✅ Validated 2026-09-16 (green emulation run; native aarch64 still infeasible BY DESIGN). Emulation validation owned by `tauri-windows-arm-validation.yml` (`windows-11-arm`, 8/8 contract tests pass).
> - **2026-09-16 acceptance (run 35077796309, success):** source build 35056028341 (post-`create:false` fix, head `d239d2e6`), explicit `artifact_run_id` (the empty-input auto-resolve picked a stale Aug-11 run, see note below). Real ARM64 host (`PROCESSOR_ARCHITECTURE=ARM64`), NSIS `Voice Typer_1.0.0_x64-setup.exe` (232.6 MB, sha256 `a14fe16e…c58c85e`) silent-install exit 0, x64 sidecar PE (`0x8664`) `SIDECAR_EXIT=0` (`voice_typer.server.ipc_server 1.0.0`), `HOST_ALIVE_AFTER_25S=True` (66.1 MB working set, pre-VS2026 runner image). Evidence artifact: `windows-arm-validation-evidence`.
> - **2026-09-16 resolver lesson:** `artifact_run_id=""` auto-resolve (`gh run list --status=success --limit 1`) returned the Aug-11 run instead of the fresh build, so the first post-fix ARM run re-tested stale bytes. Always pass the build run ID explicitly until the resolver is hardened.
> - **2026-08-24 audit:** scaffold inert BY DESIGN, C-CI-4 gates the matrix leg (no public windows-11-arm runner; manual dispatch only per ADR-0020 §15). Action requires ARM hardware + explicit policy change; never enable blindly.
> - **2026-09-15 re-check:** hosted `windows-11-arm` runners exist (the emulation workflow targets them via `runs-on: windows-11-arm`, undispatched as of this edit), but the native-build premise still holds: ctranslate2 ships zero `win_arm64` wheels across all versions and pinned cryptography 50.0.0 ships none (win_arm64 only in 46.0.0–46.0.3, verified live vs PyPI). Re-evaluate only if both publish win_arm64 wheels.
> - **2026-09-15 removal note:** the `_BINARY_NAMES_BY_PLATFORM_ARCH` map + `_windows_arch_suffix()` in `voice_typer/server/native_hotkeys/binary_path.py` were deleted as dead code (MO-5 wave; zero remaining references, `_candidate_binary_names` + legacy fallback own all lookups, 66 binary-path tests green). This narrows future ARM options further: a native aarch64 leg would now require resurrecting arch-suffixed resolution, not just flipping a gate. Consistent with the ship-x64 model — recorded here so a future agent doesn't "rediscover" the deleted map as missing.
- **Description**: Ship-x64 + validate-emulated model (native aarch64 freeze infeasible: no win_arm64 wheels); emulation run owned by `tauri-windows-arm-validation.yml`.
- **Note**: Per ADR §4.1, explicit deferral of the native leg.
- **Effort**: 🟢 LOW for emulation dispatch (manual `workflow_dispatch`); 🔴 HIGH for a native leg (needs upstream wheels + policy change).

### S1-CR-146, `StartupWMClass=Voice Typer` may not match Tauri window class
**Status:** ❌ Not Fixed (mismatch re-confirmed live 2026-09-15: template:28 `StartupWMClass=Voice Typer` vs `Cargo.toml:15` bin `voice-typer-tauri`; wiring intact via `tauri.conf.json:98` desktopTemplate + `Exec=voice-typer-tauri`). Deliberately NOT rewritten blindly; `host-validation.yml` records it as a `::warning` until `xprop WM_CLASS` on a visible Tauri window decides the value. `VALIDATE ON LINUX HOST`.
> - **2026-08-24 audit:** plausible-true (space+case in productName makes default tao WM_CLASS match unlikely vs binary prgname `voice-typer-tauri`): verify via `xprop WM_CLASS` on a real Linux desktop, then set the matching class in `src-tauri/voice-typer.desktop.template`.
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

- **WM-6 / WM-7 / WM-8 / WM-11 / WM-12 / WM-13**, headless slice green 2026-09-15 (`test_clipboard_restore_args` + `test_clipboard_borrow_restore` + `test_sidecar_ws_ready_ordering` + `test_timeout_utils`, 57 passed); live desktop runs (X11/Wayland paste, toasts, hooks, logon) still need real hosts.
- **WM-14**: Windows `taskkill` behavior. Kill paths verified correct on inspection (`electron_launcher.py:308-339` `taskkill /T /F` + `teardowns/electron.py:103-136` fallback; synthetic tree-kill probe in `host-validation.yml`); real Electron-tree kill needs a Windows host.
- **GP-7**: macOS notarization. Preflight verified (`Info.plist` mic/notification keys + entitlements + secrets-gated workflow step); full sign + notarize + staple + clean-Mac Gatekeeper check needs a real macOS host with Developer ID + notary credentials.
- **GP-135**: cross-platform native binaries. Manifest/lookup/source verified headless (x86_64 shas match disk bytes; empty aarch64/macOS shas are fail-closed BY DESIGN; mirror tables match). Open host work: per-platform hash population, `build_native_listener_windows.sh` vs `compile_native.ps1` output-dir mismatch to confirm on Windows Git Bash, arch-aware `get_expected_sha256` gap (owned elsewhere), live per-OS runs.
- **VT-1**: Windows host validation. Code verified present (`config/loader.py` warnings registry, `_timeout_utils` TIMEOUT runner, `tray_lifecycle.py:151-177` degradation) + imports/unit green (`test_timeout_utils` 34 passed); live Windows terminal re-run needs a real host.

---

### MO-61: Sidecar WS protocol_version mismatch is advisory-only while TCP rejects — transport asymmetry
**Status:** ❌ Not Fixed (investigation-only session 2026-09-14)
**Description:** The WS handshake (`sidecar_ws_internals/handshake.py:163-184`) logs a WARNING on protocol-version skew and **continues the connection**. The TCP handshake (`ipc/transport_tcp.py:550-577`) **rejects** with a structured `server.protocol_version_mismatch` error envelope and closes the socket. A host and sidecar with incompatible wire protocols will authenticate over WS and then produce confusing partial-failure symptoms (malformed dispatches, silent drops) instead of a clean, immediate rejection.
**Current Behavior:** WS: `if host_protocol_int != PROTOCOL_VERSION: log.warning(... continuing, field is advisory)`. TCP: sends `{"code": "server.protocol_version_mismatch", ...}` and returns.
**Expected Behavior:** Both transports should reject on mismatch (or both should warn-and-continue). The current asymmetry means the Tauri path is less strict than the Electron path for the same contract.
**User Impact:** Tauri host/sidecar version skew surfaces as silent dispatch failures instead of a structured error the renderer can surface.
**Root Cause:** The WS check was added as "defense-in-depth, not a security gate" (comment at handshake.py:167) while the TCP check was added later as a hard rejection; the two were never reconciled.
**Related Files:**
- `voice_typer/server/sidecar_ws_internals/handshake.py:158-184`
- `voice_typer/server/ipc/transport_tcp.py:550-577`
**Fix:** Doc-only for now (Electron-removal decision): document the asymmetry as an explicit ADR decision. No transport behavior change on either side until the single-transport Tauri world — a blind WS-reject would turn version skew into total disconnect.
**Severity:** 🟡 Medium
**Category:** IPC / Resilience

### MO-66: `_sanitize_config_for_ipc` redacts empty-string secrets, breaking renderer's "not configured" vs "configured" distinction
**Status:** ⏸️ HOLD: do not implement until the user adjudicates the empty-string redaction tradeoff.
**Description:** `ipc/history_bounds.py:261-282`: the sanitizer preserves `None` (so the renderer can distinguish "no key configured") but redacts **any** other value — including the empty string `""` — to `"<redacted>"`. The comment says this was tightened because falsy non-None values like `0`/`False` were unsafe to preserve. However, the empty string `""` is the canonical "no key set" value for the five API-key fields (`config/_schema.py:321-330` default all to `""`). After this change, the renderer receives `"<redacted>"` for an unset key and cannot distinguish it from a set key. The "key configured" UI indicator will show for unset keys.
**Current Behavior:** `if v is None: continue` then `out[k] = _REDACTED_SENTINEL` for every non-None value, including `""`.
**Expected Behavior:** Preserve `""` (the "no key" sentinel) alongside `None`, so the renderer can distinguish unset from set.
**User Impact:** Settings UI shows "key configured" for API keys that were never entered.
**Root Cause:** The 2026-10 tightening treated all falsy values uniformly without recognizing that `""` is the schema's "unset" sentinel.
**Related Files:**
- `voice_typer/server/ipc/history_bounds.py:261-282`
- `voice_typer/server/config/_schema.py:321-330`
**Fix:** Change the condition to `if v is None or v == "": continue` (preserve both "unset" sentinels). **PRODUCT DECISION REQUIRED (Wave 4):** this reverses a deliberate 2026-10 security tightening documented at `history_bounds.py:258-265` (empty-string redaction). Restoring `""` is correct for the "unset" sentinel but must be adjudicated as a security tradeoff, not applied as a silent bugfix.
**Severity:** 🟡 Medium
**Category:** Configuration / UI Correctness

### MO-10: Renderer feature hooks concentrate too much logic in single files (W1)

**Status:** ❌ Not Fixed (2026-09-15)

**Description:** Large production hooks (line counts):
- `pages/microphone/hooks/useMicrophoneTestSession.ts` — **832**
- `pages/microphone/hooks/useMicrophoneLevelMonitor.ts` — **759**
- `hooks/useConnection.ts` — **724** (with multi-path status sync invariant documented at L30-49)
- `hooks/models/useModelDownload.ts` — **710**
- `components/settings/useThemeSettings.ts` — **759**

Related `ThemeSettingsSection.tsx` size is already WONT_FIX GQ-L47 (partial extraction done); these hooks are separate active concentration points. `useConnection` correctly encodes C-HOME-1, but as one multi-path state machine.

**User Impact:** Hard-to-test UI state machines; mic-test and model-download flows have historically produced user-visible bugs (see prior mic-test constraints C-MIC-16–21).

**Root Cause:** Hooks grew as the single place for IPC subscription + local state + timers + effects.

**Gain vs Trade-off:** Splitting into pure helpers + smaller hooks improves tests; risk of prop/store plumbing noise.

**If We Do It:** Safer UI evolution on mic/model flows.

**If We Don't:** High-churn product areas stay monolithic.

**My Recommendation:** ✅ Implement — extract pure status reducers / download state machines first (like `applyStatusWithReason` already is inside `useConnection`).

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts:1,30-49`
- `voice_typer/client/src/renderer/src/pages/microphone/hooks/useMicrophoneTestSession.ts`
- `voice_typer/client/src/renderer/src/hooks/models/useModelDownload.ts`

**Fix:** Move state machines/pure functions to sibling modules; keep hooks as thin wiring.

**Simplified Fix:** A few React hooks do too much; pull the pure logic out so they only glue UI to events.

**Implementation Difficulty:** 🟡 Medium

**Severity:** 🟡 Medium

**Category:** Frontend Architecture

### MO-11: Dual Electron + Tauri host path multiplies IPC allowlist and launch complexity (known migration debt)

**Status:** ⏸️ DEFERRED — Electron-removal epic tracker (user decision: Electron will be removed, Tauri only). No Electron-side investment until cutover. Parity tests stay strict (they guard Tauri too). Originally: ❌ Not Fixed (2026-09-15; intentional ADR-0020 migration state)

**Description:** Both hosts are live: Electron main (`client/src/main/index.ts`, 389 lines, wiring-only per REF-2) and Tauri host (`src-tauri/src/main.rs`, 279 lines, wiring-only per C-ARCH-1). IPC command surface must stay in lockstep across **three** allowlists (AGENTS.md §6.4: Python registry, TS `ALLOWED_COMMANDS`, Rust `allowed_commands`). Sidecar WS vs Electron TCP also duplicate transport concerns (`sidecar_ws.py` 869+ lines, `transport_tcp.py` 1026+).

**User Impact:** No bug if contracts hold; high process cost for every IPC change, and drift risk is already a named non-negotiable contract.

**Root Cause:** Migration window with two production hosts (ADR-0020). Documented, not accidental.

**Gain vs Trade-off:** Retiring Electron after Tauri validation collapses the surface; until then dual maintenance is required.

**If We Do It (retire Electron):** Halve host-side IPC/launch maintenance.

**If We Don't:** Every command change stays a 3-way edit forever until migration completes.

**My Recommendation:** ⚠️ Track as the Electron-removal epic, do not "simplify" by deleting Electron until host validation (Phase 0-W) completes AND the user orders cutover; keep parity tests strict. No standalone Electron-side work until then.

**Progress:** None yet.

**Related Files:**
- `voice_typer/client/src/main/index.ts`
- `src-tauri/src/main.rs`
- `voice_typer/server/ipc/registry.py`
- `voice_typer/client/src/main/allowed-commands.ts`
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs`

**Fix:** Complete Tauri host validation, then remove Electron host + one transport path.

**Simplified Fix:** Two desktop shells both talk to the same backend; finish the migration so only one shell remains.

**Implementation Difficulty:** 🔴 Hard (product migration, not a local refactor)

**Severity:** 🟡 Medium

**Category:** Architecture / Migration

### MO-13: Cluster of >1000-line server modules with very low function density (E3/W1 umbrella)

**Status:** ❌ Not Fixed (2026-09-15)

**Description:** AST density scan (lines vs defs) highlights long-procedure modules, not just large *files*:
- `service/offline_pack.py` — 1806 lines, 57 defs (download/resume/swap/HTTP/lock all in one module)
- `recording/_recorder_split.py` — 1669 lines, 37 defs
- `recording/device_manager.py` — 1454 lines, class body 1358 (`DeviceManager`)
- `config_applier.py` — 1318 lines (see MO-8 for `apply_config`)
- `streaming.py` — 1258 lines, multiple large classes
- `security/redaction.py` — 1270 lines

These are working subsystems, but several exceed healthy single-module size and mix I/O, state machines, and policy.

**User Impact:** Indirect: slower reviews, higher merge conflict rate, easier accidental coupling.

**Root Cause:** Feature-complete vertical growth without periodic package split (offline_pack docstring itself enumerates 15+ edge-case concerns).

**Gain vs Trade-off:** Package splits improve navigation; over-splitting can obscure related edge cases (offline_pack is intentionally cohesive).

**If We Do It:** Faster onboarding and safer concurrent edits.

**If We Don't:** Hot files remain conflict magnets.

**My Recommendation:** ✅ Implement selectively — split only modules with independent lifecycles (e.g. offline_pack HTTP vs install/swap vs events); leave cohesive single-concern large files if splitting adds only file count.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/service/offline_pack.py`
- `voice_typer/server/recording/device_manager.py`
- `voice_typer/server/streaming.py`
- `voice_typer/server/security/redaction.py`

**Fix:** Package-split by lifecycle/concern with re-export façades (E1).

**Simplified Fix:** Several backend files are huge; split the ones where the pieces have independent jobs.

**Implementation Difficulty:** 🟡 Medium

**Severity:** 🟡 Medium

**Category:** Architecture

### MO-14: IPCServer `__init__` wires side effects (daemon threads, deferred recorder access) inside the constructor

**Status:** ❌ Not Fixed (2026-09-15)

**Description:** Within the 339-line constructor (`ipc_server.py:422+`), service construction also **spawns a daemon thread** to wire a mic-cache invalidator (`:481-501`) with a nested `except Exception: log.debug`. Constructors that start threads and depend on lazy `app.recorder` make IPC server construction a side-effectful lifecycle event rather than pure wiring (E3). Related lifecycle work lives in `ipc/lifecycle.py` (776 lines).

**User Impact:** Harder to construct IPC servers in tests without thread noise; race window while invalidator is unwired (documented STARTUP-9).

**Root Cause:** Avoid blocking IPC startup on multi-second recorder build by deferring wiring ad hoc in `__init__`.

**Gain vs Trade-off:** Moving deferred wiring into an explicit `start()`/`wire_background()` phase clarifies lifecycle.

**If We Do It:** Predictable construction vs start boundary.

**If We Don't:** Constructor side effects remain implicit.

**My Recommendation:** ✅ Implement — move deferred wiring to an explicit post-start hook called from the real entrypoint.

**Progress:** None yet.

**Related Files:**
- `voice_typer/server/ipc_server.py:422-501`
- `voice_typer/server/ipc/lifecycle.py`

**Fix:** Separate construct vs start; no threads in `__init__`.

**Simplified Fix:** Creating the IPC server already starts background work; make that an explicit start step.

**Implementation Difficulty:** 🟢 Easy

**Severity:** 🟢 Low

**Category:** Architecture / Lifecycle

---

**Investigation Wave 1 / Group 1 (Architecture & Code Quality):** 14 entries (MO-1 … MO-14). Investigation only; no production code changes.

---

## UX/UI Audit (Investigation Wave 1, sub-agent #3)

Findings MO-40 through MO-50 from the Group 3 UX/UI audit. All evidence-based with file:line citations. No production code was modified.

---

### MO-86: `_READONLY_COMMANDS` only contains 4 of ~20 pure-read commands
**Status:** ⏸️ DEFERRED — Electron TCP-transport freeze (consumer is the TCP dispatcher; revisit post-removal if TCP survives, with the per-handler mutation audit).
**Description:** `_READONLY_COMMANDS` in `voice_typer/server/ipc/registry.py:139-146` contains only `get_status`, `get_config`, `get_model_catalog`, and `heartbeat`. The dispatcher uses this frozenset to bypass the per-server `_dispatch_lock` so long-running mutating handlers don't block status polls. However, at least 12 additional commands are pure reads with no state mutation: `get_history`, `get_history_count`, `get_transcription_text`, `get_today_stats`, `get_favorites`, `get_microphones`, `get_vocabulary`, `get_correction_usage`, `get_templates`, `get_defaults`, `get_download_queue`, `get_volume_backend_status`, `get_model_status`. Under the current set, a `download_model` (long-running, holds the lock) blocks `get_history` from a second connection.
**Current Behavior:** 4 commands bypass the dispatch lock; ~13 pure-read commands are serialized behind it.
**Expected Behavior:** All pure-read commands are in `_READONLY_COMMANDS`.
**User Impact:** Perceived UI freeze when a long-running mutating handler (model download, vocabulary save) holds the lock while the renderer polls status/history.
**Root Cause:** Set was created with a minimal initial membership and never expanded.
**Related Files:**
- `voice_typer/server/ipc/registry.py:139-146`
- `voice_typer/server/ipc/dispatcher.py` (lock-bypass logic)
**Fix:** Audit each handler for state mutation; add confirmed pure-read commands to `_READONLY_COMMANDS`. Add a test that asserts every `get_*` command is either in `_READONLY_COMMANDS` or has a documented mutation reason.
**Severity:** 🟡 Medium
**Category:** Concurrency / performance

### MO-90: Handshake loop preserves process-leak quirk on stdout-close-before-handshake
**Status:** ❌ Not Fixed (documented intentional quirk)
**Description:** `src-tauri/src/sidecar/spawn/handshake_loop.rs:30-35` documents: "the stdout-closed-before-handshake error paths in BOTH helpers return WITHOUT killing the child. The release loop leaks the process on channel-close (the shell-plugin child does not kill on Drop); the dev loop relies on `kill_on_drop(true)`." This means if the sidecar's stdout closes before the `server_started` handshake line (e.g. the sidecar crashes during startup), the release-mode child process is leaked — it holds the single-instance mutex and can prevent subsequent launches (RACE-011 class of bug).
**Current Behavior:** Release-mode spawn path does not kill the child on stdout-close-before-handshake.
**Expected Behavior:** Child is killed on every failure path, including stdout-close.
**User Impact:** A crashed sidecar during startup can leave a zombie process that blocks relaunch until manually killed.
**Root Cause:** Documented as "pre-existing shape of all four loops and is kept exactly" — deliberate preservation of prior behavior.
**Related Files:**
- `src-tauri/src/sidecar/spawn/handshake_loop.rs:30-35,164-210`
**Fix:** Add `child.kill()` to the stdout-close error path in `read_handshake_from_command_events`. Validate that the supervisor's respawn path is not confused by the kill.
**Severity:** 🟡 Medium
**Category:** Reliability / platform

### MO-94 residual verdict: coverage is ADEQUATE — do not re-file

Re-audited the four named test surfaces against the production handlers:

- `tests/test_ipc_server.py:1655-1707` (`TestCheckPackUpdateDispatch`) exercises real `_dispatch` for registration, rate-limit cost, structured-ack shape, and never-raises-on-handler-error.
- `tests/test_ipc_server.py:2923-2963` (`TestTranscribeOfflineDegradation`) covers the full Phase 2d degradation matrix: pack missing → `queued:false + degraded:true + reason:"offline_pack_missing"`; pack present → `queued:true`; check failure → fail-safe degrade.
- `tests/test_update_check.py:283-917` covers service-level `check_offline_pack_update` (consent required / network fail / success / trigger_download) plus the IPC wrapper (`app is None` safe default).
- `tests/test_worker_transcribe.py:114-184` covers worker dispatch, string sample-rate coercion, and engine-error-still-emits-result.

Production handlers (`voice_typer/server/ipc/lifecycle.py:761-844`) are forwarder stubs by design until the slim-core→worker hop lands (documented in the handler docstring). The only untested edge is "payload validation when pack is present" — irrelevant while the handler acks without consuming the payload. **No residual coverage hole worth a finding.** Do not re-file MO-94.

Files inspected for this verdict: `voice_typer/server/ipc/lifecycle.py:761-844`, `voice_typer/server/service/update_check.py`, `voice_typer/server/ipc/registry.py:394-418`, `voice_typer/server/ipc/rate_limiter.py:74-75`, the four test files above.

---

### Areas inspected with no new High/Critical findings (negative results, recorded for Wave 4)

- **Electron main security surface:** `window-chrome.ts:86-90` and `bubble/lifecycle.ts:165-175` keep `contextIsolation:true`, `nodeIntegration:false`, `webSecurity:true`, `allowRunningInsecureContent:false`. `input-nav-guard.ts` denies non-https `shell.openExternal`. No exploitable path found.
- **Global hotkey registration (Electron):** `global-shortcuts.ts` is best-effort with idempotent register/unregister and non-fatal failure policy — correct by design (C-UI constraints; dismiss also available via bubble ×).
- **Onboarding completion:** `onboarding.py:793-869` persists `onboarding_completed` before `config.save()`, raises on `save() is False`, and only then `mark_complete()`. The infinite-wizard-reappear loop is already fixed. Skip path re-raises on marker-write failure. No data-loss path found.
- **History search:** `history_db_internals/search.py:456-535` handles FTS / CJK trigram / LIKE-fallback with explicit degraded paths and query caps. GQ-48 (LIKE separator-only scan) is already WONT_FIX. No new defect.
- **Model download resume (HF):** `offline_pack.download_offline_pack_with_resume` + `_probe_partial_for_resume` have extensive tests (`tests/test_pack_download_resume.py`, `test_pack_github_rate_limit.py`, `test_pack_disk_full_during_download.py`). The HF `resume_download=True` path is covered by `tests/model_download/`. The residual gap is the *service-layer* resume no-op (MO-95), not the transport resume.
- **Secondary-page empty states:** History, Vocabulary, Templates, Models, Dashboard/Analytics, Microphone all ship dedicated EmptyState variants with load-error vs empty vs no-results distinction (see the per-page test files under `pages/__tests__/`). No missing empty state found.
- **C-BRAND-1:** grep for the literal `"Voice Typer"` outside branding modules returns only `branding.py` and a comment in `_paths.py`. Clean.
- **Tray menu (server):** `tray_menu.py` structure + Tauri dict path look consistent; `wrap_callback` correctly suppresses `SystemExit` after `tray.stop()`. No new crash path found.

### Files inspected (Wave 3)

**Production (Python):**
`voice_typer/server/ipc/lifecycle.py`, `voice_typer/server/service/update_check.py`, `voice_typer/server/service/offline_pack.py`, `voice_typer/server/service/model/_downloads.py`, `voice_typer/server/service/model/_delete_import.py`, `voice_typer/server/asr_setup.py`, `voice_typer/server/dictation_pipeline/paste_step.py`, `voice_typer/server/dictation_pipeline/helpers.py`, `voice_typer/server/dictation_pipeline/enhancement_steps.py`, `voice_typer/server/clipboard/manager/_paste.py`, `voice_typer/server/hotkey_dispatcher.py`, `voice_typer/server/hotkeys/native_adapter.py`, `voice_typer/server/tray_menu.py`, `voice_typer/server/onboarding.py`, `voice_typer/server/handlers/onboarding_handlers.py`, `voice_typer/server/handlers/model_handlers.py`, `voice_typer/server/history_db_internals/search.py`, `voice_typer/server/vocabulary_automation.py`, `voice_typer/server/i18n.py`, `voice_typer/server/ipc/registry.py`, `voice_typer/server/ipc/rate_limiter.py`

**Production (TypeScript):**
`voice_typer/client/src/main/shortcuts/global-shortcuts.ts`, `voice_typer/client/src/main/windows/main-window.ts`, `voice_typer/client/src/main/windows/window-chrome.ts`, `voice_typer/client/src/main/windows/window-events.ts`, `voice_typer/client/src/main/windows/input-nav-guard.ts`, `voice_typer/client/src/main/ipc/window-handlers.ts`, `voice_typer/client/src/renderer/src/hooks/models/useModelDownload.ts`, `voice_typer/client/src/renderer/src/hooks/models/useModelSelection.ts`, `voice_typer/client/src/renderer/src/hooks/usePasteFailedToast.ts`, `voice_typer/client/src/renderer/src/hooks/usePasteDeferredToast.ts`

**Tests:**
`tests/test_ipc_server.py` (TestCheckPackUpdateDispatch, TestTranscribeOfflineDegradation), `tests/test_update_check.py`, `tests/test_worker_transcribe.py`, `tests/handlers/test_model_handlers.py`, `tests/test_pack_download_resume.py`, `tests/test_pack_github_rate_limit.py`, `tests/test_pack_disk_full_during_download.py`

**Metadata:**
`review.md` (all existing MO-*), `WONT_FIX.md`, `AGENTS.md`, `.Skills/systematic-debugging/SKILL.md`

---

## Tauri log-parity (LOG audit 2026-09-15 — migrate before Electron removal)

Investigation-only audit comparing the Electron and Tauri log paths end-to-end. Shared core is LOSSLESS: Python writes the same `<config>/logs/voice-typer.log` (same C-LOG-1/C-LOG-2 template, same rotation, same session-id handoff) under both runtimes. The 7 items below are the HOST-SIDE gap: log sources Electron produces that Tauri silently drops. Every one must land before Electron is removed, or its logs die with it.

**Bundles:** MO-102+MO-105 (renderer capture); MO-104 then MO-108 (tee, then coverage).

### MO-105: No webview console capture under Tauri (INFO+ routing + ERROR sink)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron captures both webviews' console via `console-message` (`windows/renderer-telemetry.ts:44`, `windows/bubble/console-forwarder.ts:1-32`, L0 drop / L1 info / L2 warn / L3+ error + ERROR sink). Tauri has no equivalent: `tauri-bridge` does dispatch/listen only (`python-namespace.ts:36-202`), and bridge failures `console.warn` into the void (`bubble-namespace.ts:145-151`, `detect.ts:138-145`).
**Current Behavior:** Renderer warnings/errors during Tauri support triage are unavailable unless explicitly logged.
**Expected Behavior:** Capture webview console at the same routing (INFO stays out of the file, WARN/ERROR persisted), without breaking the volume contract.
**User Impact:** Support log completeness for UI-side issues post-Electron.
**Root Cause:** Never built — dispatch bridge only.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble/console-forwarder.ts:89-114` (contract to mirror)
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts:145-151`
**Fix:** Renderer-side forward (console warn/error → `renderer_log`) or host-side webview capture; keep INFO out of the file; cap volume.
**Severity:** 🟡 Medium
**Category:** Logging / Tauri parity
**Resolution (2026-09-16):** Implemented renderer-side capture in `lib/console-capture.ts`, installed from `main.tsx` next to `installGlobalErrorHandlers()`: `console.warn`/`console.error` are forwarded through the SHARED `window.window_.logError` sink while INFO/DEBUG/`log` stay out (the host file is WARN-only by default, MO-114). The original console method always runs FIRST and is never replaced with a no-op, so DevTools output is unchanged. Volume is bounded to 20 forwarded records per 10 s window; the first suppression of each window is itself reported once (`[capture] suppressed further console.<level> records this window (cap 20/10000ms)`) so a truncated burst is visible instead of silent, and one captured record is capped at 2 000 chars. The Electron side gained the same classification: `normalizeRendererLogLevel` in `main/ipc/window-handlers.ts` maps `warn`/`warning` → the canonical `WARN` label and everything else → `ERROR` (fail-loud), matching the Rust `parse_renderer_level` exactly. Tests: `lib/__tests__/console-capture.test.ts` (7: level routing, passthrough, INFO exclusion, per-window cap + single notice, idempotent install, truncation, bridge-absent no-op) and the `normalizeRendererLogLevel` suite in `main/__tests__/pii-scrubber.test.ts`.

### MO-107: Crash-loop breaker parity decision (Electron 5-in-60s exit vs Rust)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron kills the process after 5 uncaught exceptions in 60s (`bootstrap/error-handlers.ts:59-60,150-162,195-240`) with `electron-crashes.log` / `electron-rejections.log`. Rust has a redacting panic hook (`platform/logging/panic_hook.rs:70-109`) but no breaker semantics; sidecar crashes are covered by `restart_counter.json` + supervisor (`supervisor.rs:101-104,173-188`), which must NOT be merged with the Electron file per C-PERSIST-4.
**Current Behavior:** Undefined host-crash-loop policy post-Electron.
**Expected Behavior:** Product decision: port breaker semantics to the Rust host, or document as intentionally dropped (panics abort; supervisor covers sidecar).
**User Impact:** Crash-loop behavior change at Electron removal if undecided.
**Root Cause:** Never decided — migration window debt.
**Related Files:**
- `voice_typer/client/src/main/bootstrap/error-handlers.ts:74-82,195-240`
- `src-tauri/src/platform/logging/panic_hook.rs:70-109`
**Fix:** Decide + implement or document. Agent action only after decision.
**Severity:** 🟢 Low
**Category:** Logging / product decision

### MO-108: Log-file coverage audit — sweep, open-logs, diagnostics bundle
**Status:** ✅ Fixed 2026-09-16
**Description:** Three independent coverage lists must agree on the file set, or new files rot: Python sweep (`log/__init__.py:194-272`, covers host logs incl. `voice-typer-rust.log`), Rust sweep (`platform/logging/init.rs:34-74`), open-logs target (`commands/system_cmds/dialogs.rs:26-28,40-49` → `<config>/logs` dir), diagnostics bundle (runbook expects `voice-typer.log` + `sidecar.log`, `windows-validation-runbook.md:1324,1372`). `sidecar.log` (MO-104) and any MO-105 sink must be added to all three once they exist; today the lists are unaudited against each other.
**Current Behavior:** Unaudited; a new log file can miss rotation or the support bundle.
**Expected Behavior:** One audit pass + tests pinning the file set across sweep/open-logs/bundle.
**User Impact:** Unbounded growth or missing logs in support bundles.
**Root Cause:** Lists grew per-runtime, never reconciled.
**Related Files:**
- `voice_typer/server/log/__init__.py:194-272`
- `src-tauri/src/platform/logging/init.rs:34-74`
- `src-tauri/src/commands/system_cmds/dialogs.rs:26-28,40-49`
**Fix:** Audit + pin tests. Do after MO-104 (needs the tee's filename).
**Severity:** 🟢 Low
**Category:** Logging / maintenance
**Resolution (2026-09-16):** Audit result: the three RUNTIME mechanisms (Python sweep `log/setup.py`, Rust sweep `platform/logging/init.rs`, open-logs target `commands/system_cmds/dialogs.rs`) are already directory-scoped and therefore self-maintaining; the **diagnostics bundle** was the one hardcoded list and silently dropped the Rust host log (its glob was `voice-typer.log*`, which never matched the host log even before the naming was clarified), plus MO-104's `sidecar.log`, `worker.log`, `startup-error.log`, the crash buffer and `native-*.log`. `scripts/diagnostics.py::_collect_logs_into` is now directory-driven (every regular file in `<config>/logs/`, minus the `*.lock` truncation locks, plus a legacy root `<config>/voice-typer.log`), with `_zip_log_name` preserving the documented `rust-voice-typer.log[.N]` rename so the two host logs cannot collide in the zip, and `_unique_zip_name` as a never-overwrite guard. Pinned by `tests/test_log_file_coverage.py` (9 tests).

### MO-109: Second-instance / tray-click raise-to-front missing under Tauri (minimized/buried window stays buried)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron raises the existing window through a full sequence (`restore()` if minimized + `setAlwaysOnTop(true,"screen-saver")` raise + `show()` + `focus()` + `moveTop()`). Tauri's single-instance and tray-click paths only do `show()` + `set_focus()`; the full raise sequence exists only in `host_events::show_main_window`.
**Current Behavior:** Second launch / tray click while minimized or behind other windows only flashes the taskbar; window does not come forward.
**Expected Behavior:** Route single-instance (`main.rs`), tray click (`tray.rs`), and any other show path through the `host_events::show_main_window` sequence (unminimize + show + always-on-top raise + focus).
**User Impact:** Start Menu / second launch while dashboard minimized feels dead after Electron removal.
**Root Cause:** Partial port — raise logic built once in `host_events.rs` but not wired to the two other entry points.
**Related Files:**
- `voice_typer/client/src/main/windows/main-window.ts:77-108`
- `voice_typer/client/src/main/single_instance.ts:272-277`
- `src-tauri/src/main.rs:122-135`
- `src-tauri/src/tray.rs:191-197`
- `src-tauri/src/host_events.rs:96-128`
**Fix:** Call the shared show routine from all three paths; add host test for minimized → shown + focused.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity

### MO-110: Standalone adopted-backend mode (VT_PYTHON_PORT/VT_IPC_TOKEN attach) has no Tauri path
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron can attach to an already-running backend (terminal `VoiceTyper` CLI flow) via `VT_PYTHON_PORT` + `VT_IPC_TOKEN` with no spawn (`pythonProcess` stays null, restart/stop become safe no-ops). Tauri always resolves + spawns a second backend with `env_clear()`.
**Current Behavior:** CLI-parent launch under Tauri double-spawns (mutex/port collision or orphaned parent).
**Expected Behavior:** Tauri checks the adopted env first and attaches instead of spawning, mirroring the Electron adopt path.
**User Impact:** Dev/standalone terminal workflow breaks after Electron removal.
**Root Cause:** Never ported — zero `VT_PYTHON_PORT` references in `src-tauri/src`.
**Related Files:**
- `voice_typer/client/src/main/python/start-python.ts:126-136`
- `voice_typer/client/src/main/python/restart-backend.ts:61-66`
- `voice_typer/client/src/main/python/stop-python.ts:211-215`
- `src-tauri/src/sidecar/spawn/release_mode.rs:58-131`
- `src-tauri/src/sidecar/spawn/dev_mode.rs:71-85`
**Fix:** Adopt-if-present check before resolve+spawn; keep restart/stop as safe no-ops in adopted mode.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity

### MO-111: KMP_DUPLICATE_LIB_OK not set on Tauri sidecar spawn (OpenMP dual-runtime hang risk)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron explicitly passes `KMP_DUPLICATE_LIB_OK=TRUE` alongside `windowsHide` for the console-less sidecar. Tauri's spawn does `env_clear()` + a narrow allowlist (PATH/HOME/TEMP/locale/GUI-bus/XPC) with no KMP entry, so the GUI-subsystem sidecar loses the workaround.
**Current Behavior:** Possible silent stall at torch import on machines with dual OpenMP runtimes; MO-104 tee gap makes it undebuggable.
**Expected Behavior:** Set `KMP_DUPLICATE_LIB_OK=TRUE` on the sidecar spawn env (both release and dev) or add it to the passthrough allowlist.
**User Impact:** App hangs at startup on affected machines with no log trace.
**Root Cause:** Env allowlist built without the Electron spawn-env audit.
**Related Files:**
- `voice_typer/client/src/main/python/start-python.ts:150-164`
- `src-tauri/src/sidecar/spawn/release_mode.rs:88-99`
- `src-tauri/src/sidecar/spawn/dev_mode.rs:77-85`
- `src-tauri/src/sidecar/spawn/env_allowlist.rs:56-144`
**Fix:** Add the var to spawn env + test asserting its presence.
**Severity:** 🟡 Medium
**Category:** Reliability / Tauri parity

### MO-112: macOS dock activate re-show path missing under Tauri
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron handles `app.on("activate")` by creating or showing the dashboard. Tauri has no `activate` handler, while macOS close keeps the app alive hidden — so dock click after closing the window does nothing.
**Current Behavior:** macOS users closing the window then clicking the dock icon get no window back (tray/Cmd+Tab only).
**Expected Behavior:** Dock activate restores/creates the main window like Electron.
**User Impact:** macOS window recovery broken after Electron removal.
**Root Cause:** Never ported — zero `activate` matches in `src-tauri/src`.
**Related Files:**
- `voice_typer/client/src/main/index.ts:370-376`
- `src-tauri/src/commands/sidecar_cmds/window_close.rs:88-95`
**Fix:** Register macOS activate handler routing to the shared show routine (see MO-109).
**Severity:** 🟢 Low
**Category:** Platform / Tauri parity

### MO-113: GPU/utility child-process crash telemetry missing under Tauri
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron starts `crashReporter` (no upload) and logs `child-process-gone` (GPU / utility crashes) to host logs. Tauri has only the Rust panic hook — WebView2/WKWebView/webkit utility crashes leave no host-log trace.
**Current Behavior:** Blank-window / GPU-failure triage loses its only signal; `voice-typer-rust.log` stays clean while nothing renders.
**Expected Behavior:** Log renderer/utility child abnormal exits to the Rust log at ERROR with redaction.
**User Impact:** Support sees "window won't render" with no evidence after Electron removal.
**Root Cause:** No Tauri equivalent of `child-process-gone` was ever wired.
**Related Files:**
- `voice_typer/client/src/main/bootstrap/runtime.ts:59-80`
- `src-tauri/src/platform/logging/panic_hook.rs:70-109`
**Fix:** Subscribe to WebView/child abnormal-exit events and log them; document in runbooks.
**Severity:** 🟢 Low
**Category:** Logging / Tauri parity
**Resolution (2026-09-16):** No Tauri/wry platform surfaces a renderer/GPU crash event (WebView2's `ProcessFailed` is not exposed by wry, WKWebView has no crash notification, webkit2gtk's `web-process-crashed` is likewise unsurfaced), so a process-level subscription is not implementable. Implemented the observable equivalent: a renderer **liveness heartbeat** — `useRendererHeartbeat` (renderer, visible-window only) → `renderer_heartbeat` command (main-window-only, SEC-026) → `platform::renderer_watchdog` evaluates a `30s` stall threshold while the main window is visible + un-minimized and logs ONE `ERROR [RENDERER-WATCHDOG] webview unresponsive: no heartbeat for Ns …` per stall episode (plus one `INFO` when beats resume). A frozen/blank webview stops executing timers, so this yields the blank-window signal Electron's `child-process-gone` provided, without inventing process control the platform cannot back. Background/occluded windows are explicitly exempt (engine-level timer throttling would otherwise read as a false stall). Tests: `platform/renderer_watchdog_tests.rs` (policy + state + cross-language interval pin), `hooks/__tests__/useRendererHeartbeat.test.ts`.

### MO-114: Host-file INFO volume contract inverted (Electron WARN-only vs Rust Info-default)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron production host file is WARN/ERROR-only; INFO goes to stdout only unless `VOICE_TYPER_ELECTRON_INFO_LOG=1` (then a separate 1 MiB `electron-lifecycle.log`). Tauri writes INFO to the single `voice-typer-rust.log` by default (`RUST_LOG` else `VOICE_TYPER_DEBUG` else Info).
**Current Behavior:** Log volume/rotation behavior silently changes at cutover; WARN-only-tuned runbooks mislead; higher disk use than the Electron baseline.
**Expected Behavior:** Product decision + documented contract: either keep Tauri INFO-default deliberately (update runbooks/rotation) or mirror the WARN-only + opt-in INFO file.
**User Impact:** Support sizing/rotation surprises; misleading runbook expectations.
**Root Cause:** Two logging defaults designed independently, never reconciled.
**Related Files:**
- `voice_typer/client/src/main/logging/structuredLogger.ts:349-380`
- `voice_typer/client/src/main/logging/printfLogger.ts:259-285`
- `src-tauri/src/platform/logging/init.rs:161-172`
**Fix:** Decide, document, align rotation/sweep coverage (see MO-108).
**Severity:** 🟢 Low
**Category:** Logging / Tauri parity
**Resolution (2026-09-16):** Kept as a deliberate, documented product decision: the Rust host file stays WARN-only by default (`RUST_LOG` / `VOICE_TYPER_DEBUG` opt into INFO to the single `voice-typer-rust.log`), so host-file volume matches the Electron baseline's WARN-only contract instead of silently growing. Because the file sink now filters INFO, the `[STARTUP] logging initialized: file=…, file_level=…` banner is written directly to the file (bypassing the level filter) so the session join key survives on line 1. Rotation/sweep coverage is directory-scoped (see MO-108) and pinned by `tests/test_log_file_coverage.py`. Rust tests: file-level contract + banner-survives-filter in `platform/logging_tests.rs`.

### MO-115: Rust 256 KiB inbound dispatch-data cap rejects saves the server schema allows (large vocabularies)
**Status:** ✅ Fixed 2026-09-16
**Description:** Rust rejects dispatch `data` over 256 KiB pre-dispatch (`data_too_large`); the server vocabulary handler allows up to 1 MiB and TCP inbound allows 1 MiB/line; the Electron sender has no data-size gate.
**Current Behavior:** `save_vocabulary` payloads between 256 KiB–1 MiB pass on Electron/TCP but hard-fail on Tauri with no client-side recourse.
**Expected Behavior:** Aligned caps (raise Rust cap to match server validation or enforce a documented UI-side limit before send).
**User Impact:** Users with thousands of vocabulary entries get a hard save failure after Electron removal — silent feature regression.
**Root Cause:** Host-side cap chosen without auditing the largest legitimate payload (vocabulary save).
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs:370`
- `voice_typer/server/handlers/vocabulary_handlers.py:89`
- `voice_typer/server/ipc/transport.py:214`
**Fix:** Align caps + add a boundary test (256 KiB–1 MiB vocabulary round-trip through Tauri dispatch).
**Severity:** 🟡 Medium
**Category:** IPC / Tauri parity
**Resolution (2026-09-16):** The Rust dispatch-data cap is now DERIVED from the 1 MiB transport frame ceiling minus the WS envelope headroom (`ENVELOPE_HEADROOM_BYTES`), so a `save_vocabulary` payload the server schema accepts no longer hard-fails at the host gate. The download-scale timeout list stays authoritative in one place per side and is pinned TS↔Rust by `main/__tests__/long-running-commands-parity.test.ts` (which also caught a parser bug in the pin itself: it anchored on a bare `&[` and could slice the type annotation).

### MO-116: Model-download dispatch timeout 120 s (Electron) vs 1 h (Tauri) — divergent failure UX
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron times out download/import renderer calls at 120 s while the backend keeps downloading (false-failure + Retry-button mode); Rust deliberately caps `_DOWNLOAD_COMMANDS` at 1 h to fix exactly that.
**Current Behavior:** Same slow download fails on Electron, succeeds on Tauri — two shells, two stories until cutover.
**Expected Behavior:** Same deadline both hosts (Tauri's 1 h behavior is the better one; self-resolves on Electron removal, but document until then).
**User Impact:** Confusing pre-cutover download-failure reports; no post-cutover impact.
**Root Cause:** Timeout raised on the Rust side only; Electron constant never updated.
**Related Files:**
- `voice_typer/client/src/main/constants.ts:111-112`
- `voice_typer/client/src/main/python/send-to-python.ts:278-285`
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs:59-75`
**Fix:** Raise Electron LONG timeout to match (or document as known pre-cutover divergence); no Tauri change.
**Severity:** 🟡 Medium
**Category:** IPC / Tauri parity
**Resolution (2026-09-16):** Electron and Tauri now share the SAME download-scale timeout budget: `IPC_TIMEOUT_DOWNLOAD_MS` (1 h) is applied to the long-running download/import dispatch set on the Electron `sendToPython` path, matching the Rust `DISPATCH_DOWNLOAD_TIMEOUT_SECS`. The two lists and the duration are pinned together (TS reads the Rust constants directly), so a future edit to either side fails the test instead of silently reintroducing the "one host times out at 120 s" divergence.

### MO-117: Notification click-routing and duration dropped on Tauri (consent/model deep-links dead)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron toasts carry `click_path` navigation, `click_consent_field` consent deep-links (session nonce), `duration_ms` auto-close, and an `isSupported` gate. Tauri shows title/body-only toasts with extra fields intentionally ignored and no click handler.
**Current Behavior:** Consent-gate and model toasts lose their action under Tauri; timed toasts persist until dismissed; users must navigate to Settings by hand.
**Expected Behavior:** Click navigates (incl. consent-row deep-link) and duration is honored, as on Electron.
**User Impact:** Actionable toasts become informational after Electron removal.
**Root Cause:** Click-routing deferred as follow-up at `host_events.rs:29-41`.
**Related Files:**
- `voice_typer/client/src/main/python/handle-message.ts:154-241`
- `src-tauri/src/host_events.rs:29-41,63-85`
**Fix:** Implement click handler + duration in the Tauri notifier; keep nonce/session safety.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity
**Resolution (2026-09-16, partial by design):** `host_events.rs` now parses the full notification contract (`title`, `message`, `click_path`, `click_consent_field`, `duration_ms`) and, when click routing is present, broadcasts the SAME `navigate` event Electron's `notif.on("click")` broadcast (`{path}` / `{path:"/settings", consent_field}`), which the renderer's existing `useNavigateEvent` consumes unchanged, so the consent-row deep-link is live under Tauri. `duration_ms` is parsed and logged. Residual gap, recorded rather than faked: `tauri-plugin-notification` 2.3.3's desktop backend is `notify-rust`, which exposes NEITHER a click callback NOR a close/timeout API on Windows/macOS/Linux (verified in the vendored plugin source), so the toast cannot defer its action to an actual click; the navigation fires when the notification is raised. Closing it fully requires replacing the notification stack (direct WinRT toast activation + platform-specific equivalents), which is a separate decision.

### MO-118: External https links have no opener path under Tauri (help/share/changelog dead)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron routes https out via `shell.openExternal` (deny rest). Tauri sets `shell.open:false` with no opener plugin/capability/handler, while the renderer uses bare `<a target=_blank>` and `window.open(https…)` (docs, troubleshooting, dashboard Telegram/X share).
**Current Behavior:** External help/feedback/share links are dead or trapped in the webview under Tauri (CSP `default-src 'self'`).
**Expected Behavior:** Opener capability or Rust `open_url` command wired to these call sites, https-only like Electron.
**User Impact:** Every outbound link broken after Electron removal.
**Root Cause:** Opener never granted — C-TAURI-2 narrowed `plugins.shell` to `{open:false}` and no replacement route was built.
**Related Files:**
- `voice_typer/client/src/main/windows/input-nav-guard.ts:68-91`
- `src-tauri/tauri.conf.json:54,136-138`
- `src-tauri/capabilities/main-runtime.json:18-19`
- `voice_typer/client/src/renderer/src/components/settings/advanced/ResourcesSettingsSection.tsx:124`
- `voice_typer/client/src/renderer/src/components/settings/advanced/PrewarmAndUpdates.tsx:441`
- `voice_typer/client/src/renderer/src/components/settings/advanced/TroubleshootingSettingsSection.tsx:309-336`
- `voice_typer/client/src/renderer/src/components/dashboard/ShareStatsDialog.tsx:216`
**Fix:** Grant opener (or `open_url` command) + route all external anchors through it; add click test.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity
**Resolution (2026-09-16):** New `lib/external-links.ts` is the one route every external https link uses: it invokes the Rust `open_external_url_command` (https-only, OS default browser, same policy as Electron's `input-nav-guard.ts`) when the bridge exists and falls back to classic `window.open` semantics otherwise. Call sites migrated: the Settings→Resources link grid (7 links, anchors → `onClick` buttons), the Prewarm/Updates changelog button, and the dashboard share dialog's `window.open`. Unit tests: `lib/__tests__/external-links.test.ts`.

### MO-119: Bubble never receives locale changes on Tauri (stale language/RTL until reload)
**Status:** ✅ Fixed 2026-09-16
**Description:** Electron pushes `bubble:locale-changed` on language switch. The Tauri bubble namespace documents "host does not yet broadcast a locale event" — listener wired for parity but never fired.
**Current Behavior:** Language switch re-renders main window immediately; bubble pill keeps old locale/dir until app reload.
**Expected Behavior:** `set_host_locale` also emits to the bubble webview.
**User Impact:** Wrong-language / wrong-direction bubble after every language change.
**Root Cause:** Emit side of the listener never implemented.
**Related Files:**
- `voice_typer/client/src/main/windows/bubble/lifecycle.ts:356-367`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/bubble-namespace.ts:290-310`
**Fix:** Emit locale event to bubble on `set_host_locale`; add renderer test.
**Severity:** 🟢 Low
**Category:** Platform / Tauri parity
**Resolution (2026-09-16):** `set_host_locale` broadcasts the locale to the bubble window as well, so an in-app language switch re-renders the bubble immediately (no stale language/RTL until reload). The host-side storage stays the parity sink for native surfaces; the broadcast is pinned by `commands/system_cmds_tests.rs`.

### MO-120: window_ bridge gaps — no restartBackend, no revealStatsImage on Tauri
**Status:** ✅ Fixed 2026-09-16
**Description:** Preload installs `restartBackend` and `revealStatsImage`; the Tauri `window_` namespace omits both (marked optional in bridge types). `useConnection` degrades to a "relaunch the app" hint; `useStatsShare` reveal silently no-ops (save/copy have fallbacks, reveal has none).
**Current Behavior:** Dead-backend Retry can't restart the sidecar (full app relaunch required); Analytics reveal-in-folder button silently does nothing.
**Expected Behavior:** Rust `restart_sidecar` command + reveal-via-`open_path`, or explicit disabled UI states where unavailable.
**User Impact:** One-click recovery and Reveal both lost after Electron removal.
**Root Cause:** Two preload methods never ported to the Tauri bridge.
**Related Files:**
- `voice_typer/client/src/main/preload/index.ts:120-152`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/window-namespace.ts:97-247`
- `voice_typer/client/src/renderer/src/types/ipc/bridge.ts:85-120`
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts:729-746`
- `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts:344-351`
- `voice_typer/client/src/main/ipc/backend-restart-handler.ts:26`
**Fix:** Add both bridge methods (Rust side + namespace + types); keep supervisor auto-respawn as the crash path.
**Severity:** 🟢 Low
**Category:** Platform / Tauri parity
**Resolution (2026-09-16):** Both bridge gaps are closed. `restartBackend` invokes the new Rust `restart_sidecar` command, which delegates to the supervisor's respawn path and resolves the SAME `{ok, reason?}` envelope Electron's `backend:restart` handler returned (so `useConnection.ts`'s escalation branch works unchanged on both runtimes); in adopted-backend mode it refuses with `reason:"adopted"`. `revealStatsImage` routes through the new `reveal_path_command` (Electron `shell.showItemInFolder` parity). The bridge-parity test's now-stale `TAURI_MISSING_WINDOW_METHODS` entries were deleted (its staleness assertion requires that).

### MO-121: Stats-image Save-As / copy / reveal has no Tauri command (silent anchor fallback)
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep)
**Description:** Electron serves stats images through native Save-As (`dialog.showSaveDialog(title: dialog.export.statsImage)`), Downloads, and Reveal. Tauri has no stats-image command; the renderer falls back to anchor download / `navigator.clipboard` with no localized dialog and no reveal.
**Current Behavior:** Analytics/Dashboard Share-image Save-As + Reveal broken under Tauri (see also MO-120 for the reveal half).
**Expected Behavior:** Rust stats-image export command mirroring `export_history/vocabulary/templates` shape, or explicit UI disable with reason.
**User Impact:** Share-image flow degraded after Electron removal.
**Root Cause:** Stats-image handlers never ported (only history/vocabulary/templates/config were).
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useStatsShare.ts:275,295,318,347`
- `voice_typer/client/src/main/ipc/stats-image-handlers.ts:145-146`
**Fix:** Port the command; localize the dialog title like the other exports.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity

### MO-122: Transport robustness contract differs per host (backpressure 256 vs 1024 + duplicate-connection steal vs reject)
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep)
**Description:** Two coupled divergences: (a) Electron rejects at 256 pending requests with `command_failed`, Rust at 1024 with `pending_full`, and neither has auto-retry (parse-only, pinned by `error-envelope.test.ts`); (b) WS enforces single-connection + 16-conn cap + Origin rejection with explicit `duplicate_connection`/`max_connections_reached`, while a second TCP auth silently reassigns `_tcp_client` leaving the first loop push-starved with no signal.
**Current Behavior:** Same overload / duplicate-client state surfaces differently per host; callers branching on the code see different values.
**Expected Behavior:** Same threshold + same machine-readable code, and same duplicate-client invariant (explicit rejection both sides) — or a documented per-host contract callers can branch on.
**User Impact:** Minimal day-to-day (only bites under retry storms / stale-zombie + fresh-client races); diagnostic confusion pre-cutover, strict behavior remains post-cutover.
**Root Cause:** Two transports designed independently; WS hardened later, TCP never updated.
**Related Files:**
- `voice_typer/client/src/main/state.ts:48`
- `src-tauri/src/commands/sidecar_cmds/allowlist.rs:46`
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs:437-454`
- `voice_typer/server/sidecar_ws_internals/connection.py:111-193`
- `voice_typer/server/sidecar_ws.py:435,528-549`
- `voice_typer/server/ipc/transport_tcp.py:672-676`
**Fix:** Align threshold + code; make TCP duplicate-auth explicit; document the contract.
**Severity:** 🟢 Low
**Category:** IPC / Tauri parity

### MO-123: TitleBar maximize state denied — isMaximized + onResized capability missing
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep)
**Description:** The Tauri capability grants `allow-toggle-maximize` but not `allow-is-maximized` / `allow-on-resized` (Tauri v2 zero-permissions default), while `window_` calls `isMaximized()` + `onResized()` on mount and on every resize to mirror `is-maximized` onto `<html>`.
**Current Behavior:** Invoke denied → `toggleMaximize` works but UI state desyncs: maximize/restore glyph + `rounded-lg` / `is-maximized` class stale (incl. OS-snap paths that bypass the button).
**Expected Behavior:** Grant both permissions; no code change.
**User Impact:** Maximize/restore icon + window-corner rounding wrong under Tauri.
**Root Cause:** Capability grant written without the bridge's query/subscribe audit.
**Related Files:**
- `src-tauri/capabilities/main-runtime.json:9-16`
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/window-namespace.ts:106-115`
- `voice_typer/client/src/renderer/src/hooks/useWindowMaximized.ts:32-64`
- `voice_typer/client/src/renderer/src/App.tsx:211,376,385`
- `voice_typer/client/src/renderer/src/components/layout/TitleBar.tsx:504-506,715-718`
- `voice_typer/client/src/main/ipc/window-handlers.ts:138-141`
**Fix:** Add the two grants + host invoke test.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity

### MO-124: Close-to-tray leaves taskbar entry under Tauri (skipTaskbar not mirrored)
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep)
**Description:** Electron hides as `hide()` + `setSkipTaskbar(true)` (restored on show). Tauri `window_close` only `hide()`s.
**Current Behavior:** Close-to-tray still shows a taskbar button under Tauri — a ghost entry for a "hidden" app.
**Expected Behavior:** Mirror the skipTaskbar pair on hide/show.
**User Impact:** Tray UX feels broken (hidden but present).
**Root Cause:** One-line port omission in `window_close.rs`.
**Related Files:**
- `voice_typer/client/src/main/windows/window-events.ts:64-70`
- `src-tauri/src/commands/sidecar_cmds/window_close.rs:74-85`
**Fix:** Set skip-taskbar false/true around hide/show; test the pair.
**Severity:** 🟢 Low
**Category:** Platform / Tauri parity

### MO-125: System-wide bubble-dismiss accelerator not registered under Tauri
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep)
**Description:** Electron registers a global `BUBBLE_DISMISS_ACCELERATOR` (CmdOrCtrl+Shift+D) to dismiss the bubble from anywhere. Tauri ships no global-shortcut plugin/registration; only the bubble × button works. The native dictation hotkey (Python binaries) is unaffected.
**Current Behavior:** Keyboard dismiss lost system-wide under Tauri.
**Expected Behavior:** Register the same global accelerator on Tauri, or document as intentionally dropped.
**User Impact:** Keyboard-first dictation flow loses its dismiss key.
**Root Cause:** Global-shortcut surface never ported (tauri.conf plugins list has notification/single-instance/dialog/shell only).
**Related Files:**
- `voice_typer/client/src/main/shortcuts/global-shortcuts.ts:70-79`
- `src-tauri/tauri.conf.json:132-139`
**Fix:** Register accelerator via Tauri global-shortcut (or record Won't Fix with rationale).
**Severity:** 🟢 Low
**Category:** Platform / Tauri parity

### MO-126: OS power events (suspend/resume/on-battery) have no Tauri counterpart
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep)
**Description:** Electron bridges `powerMonitor` suspend/resume/on-battery. Tauri has no power handling (only supervisor backoff + shutdown notify).
**Current Behavior:** Suspend-mid-recording / resume behavior unhandled under Tauri.
**Expected Behavior:** Handle suspend (stop/finalize recording safely) + resume (re-probe devices/sidecar) or document as dropped.
**User Impact:** Recording/device edge cases after sleep/wake.
**Root Cause:** Power surface never ported.
**Related Files:**
- `voice_typer/client/src/main/power.ts:224,241,257`
**Fix:** Subscribe to OS power events in Rust host; route to sidecar/recorder lifecycle.
**Severity:** 🟢 Low
**Category:** Platform / Tauri parity

### MO-127: Hotkey-triggered sound cues may be autoplay-blocked under Tauri (needs host validation)
**Status:** ❌ Not Fixed (audit 2026-09-16, 3-agent Electron-vs-Tauri parity sweep; suspected, needs Windows/WebView2 host run)
**Description:** Electron sets `autoplayPolicy: no-user-gesture-required` so OS-hotkey-triggered start/stop beeps always play. Tauri has no autoplay key (WebView2/WebKitGTK default = gesture-required); `sound-manager` gesture-resume + HTMLAudio fallback stay policy-gated. Respects C-SOUND-1 (start/stop only, never a complete cue).
**Current Behavior (suspected):** First hotkey-dictated cues silent until a prior window click under Tauri.
**Expected Behavior:** Autoplay allowed for the main window (or a host-side beep path) so hotkey cues always play.
**User Impact:** Start/stop beeps intermittent when dictating purely via hotkey.
**Root Cause:** Autoplay policy set for Electron window creation, never set for the Tauri webview.
**Related Files:**
- `voice_typer/client/src/main/windows/window-chrome.ts:69-81`
- `src-tauri/tauri.conf.json`
- `voice_typer/client/src/renderer/src/lib/sound-manager.ts:34-40`
**Fix:** Set webview autoplay policy (or host-side cue); validate on Windows host per runbook.
**Severity:** 🟡 Medium
**Category:** Platform / Tauri parity

---

## Completed and removed from the queue (2026-09-15)
- MO-42: focus ring unified on `ring-1` (1px) per user decision 2026-09-15; WCAG 2.4.7 met, 2.4.13 AAA gap owner-accepted; vitest + tsc + biome green.


Verified-fixed (implemented + focused/related suites green in-session);
removed to keep the queue actionable. Full evidence in git history.
Deferred/hold/user-only items are NOT listed here — they stay above.

- MO-4: _wrap migration, zero TODOs, handlers suites green.
- MO-8: apply_config split (TCP leg untouched per rescope).
- MO-20, MO-21: single-query + cache-first health checks.
- MO-22, MO-23, MO-27: stop-check, registry wiring, backstop/idle-exit.
- MO-40, MO-41, MO-43, MO-44, MO-46, MO-47, MO-48, MO-49: renderer
  fixes + locale parity, vitest green.
- MO-50: key parity verified clean (informational, never a defect).
- MO-60, MO-62, MO-63, MO-64, MO-65, MO-67, MO-68, MO-69: redaction,
  SEC-002 raise, ACLs, costs, heartbeat doc, scrub helper, ACL banner,
  cost-parity contract test.
- MO-80: icons-drift Node pin aligned (dispatch validation stays
  under CI-1/host items).
- MO-81, MO-82: keep+document direction; doc drift fixed.
- MO-85: sleep cuts incl. 4th site; timing suites green.
- MO-87, MO-88, MO-92: sender types + behavioral tests.
- MO-93: validation docstring example swap.
- MO-95, MO-96, MO-97, MO-98, MO-99, MO-101: resume bool, server
  i18n migration, clipboard reason codes, renderer reason map,
  paste_deferred cleanup.
