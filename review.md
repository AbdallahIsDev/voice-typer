## High Priority

These items are the highest-priority remaining work for the project. They block the Tauri migration, fix core functionality, or address critical infrastructure gaps. Items in this section are ordered by priority (top = most urgent).

> **Won't Fix tasks live in `WONT_FIX.md`**: deliberately not solved. Do NOT fix them (AGENTS.md C-REVIEW-1). See that file for the full list.

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
**Status:** ⚠️ Partial (2026-09-21 FV session: no further headless slice exists; every remaining item requires a real desktop host, so nothing was changed. Recorded as a host-only gate.) (2026-09-15 wave): headless slices fixed + green headless (signal-handler escalation counter, `binary_path` dead-code cleanup, host-validation workflow import fixes, manifest docstring; focused runs green, ruff + branding clean). Live behavior still needs real hosts per the runbooks.
**Description:** Many platform-specific fixes (Win32 console handler, macOS clipboard restore, native key-listener binaries) have been implemented but only tested on a Linux sandbox. They must be validated on real Windows/macOS hardware.
**User Impact:** Platform-specific regressions may exist on Windows/macOS that are invisible on Linux.
**Root Cause:** No real Windows/macOS desktop session in this sandbox; GHA hosted runners cover only the headless subset.
**Progress:** Headless subset automated in `.github/workflows/host-validation.yml` (undispatched; 2026-09-15 search-first wave extended it: Windows kill-path contracts + safe-handler routing probes, macOS `swiftc` compile + smoke, bundle parity via `plutil`, toolchain presence; contract pins now 8). Real defects fixed headless: `_signal_handler` never incremented `_signal_count`, so second-signal `os._exit(1)` was dead code (one-line fix + `tests/test_signal_delivery_count.py`, 3 passed); `TerminateProcess`-raise handle leak in `teardowns/electron.py` (`try/finally`) + discarded `taskkill` exit now debug-logged (`electron_launcher.py`), covered by 4 new tests; macOS `ClipboardSnapshot` capture/restore edge cases pinned (`tests/test_clipboard_macos_capture.py`, 8 passed) + bundle preflight (`tests/tauri/test_macos_bundle_preflight.py`, 9 passed). Aggregate focused run 129 passed; ruff + branding clean. Live gates blocked on host access + manual dispatch. Known pre-existing (not introduced, left to owner): Windows-fallback teardown test passes solo but fails after controller tests (early `is_windows` stub bind in `teardowns/electron.py`).
**Related Files:** `docs/migration/windows-validation-runbook.md`, `docs/migration/macos-validation-runbook.md`
**Fix:** Run the platform validation runbooks on real Windows and macOS hosts.
**Severity:** 🔴 High
**Priority:** P0

### S1-CR-146, `StartupWMClass=Voice Typer` may not match Tauri window class
**Status:** ❌ Not Fixed — host-only gate (`VALIDATE ON LINUX HOST` via `xprop WM_CLASS`); intentionally not rewritten blind (2026-09-21 FV session: left unchanged). (mismatch re-confirmed live 2026-09-15: template:28 `StartupWMClass=Voice Typer` vs `Cargo.toml:15` bin `voice-typer-tauri`; wiring intact via `tauri.conf.json:98` desktopTemplate + `Exec=voice-typer-tauri`). Deliberately NOT rewritten blindly; `host-validation.yml` records it as a `::warning` until `xprop WM_CLASS` on a visible Tauri window decides the value. `VALIDATE ON LINUX HOST`.
> - **2026-08-24 audit:** plausible-true (space+case in productName makes default tao WM_CLASS match unlikely vs binary prgname `voice-typer-tauri`): verify via `xprop WM_CLASS` on a real Linux desktop, then set the matching class in `src-tauri/voice-typer.desktop.template`.
- Location: `src-tauri/voice-typer.desktop.template:9`
- Evidence: Binary is `voice-typer-tauri` (per `Cargo.toml:15`). Tauri v2 sets WM_CLASS based on binary name. If actual WM_CLASS is `voice-typer-tauri` but `StartupWMClass=Voice Typer`, WM may show duplicate icon.
- Fix: Verify actual WM_CLASS via `xprop WM_CLASS` on a running Tauri window; set `StartupWMClass` to match. `VALIDATE ON LINUX HOST`. · **Found by**: R15

- **WM-6 / WM-7 / WM-8 / WM-11 / WM-12 / WM-13**, headless slice green 2026-09-15 (`test_clipboard_restore_args` + `test_clipboard_borrow_restore` + `test_sidecar_ws_ready_ordering` + `test_timeout_utils`, 57 passed); live desktop runs (X11/Wayland paste, toasts, hooks, logon) still need real hosts.
- **WM-14**: Windows `taskkill` behavior. **2026-09-17:** Electron-tree kill path (`electron_launcher.py` + `teardowns/electron.py`) was removed with the Electron host; the synthetic `taskkill /T /F` probe remains in `host-validation.yml`. Live Electron-tree kill is N/A post-removal; Tauri sidecar kill paths are owned by `src-tauri/src/sidecar/`.
- **GP-7**: macOS notarization. Preflight verified (`Info.plist` mic/notification keys + entitlements + secrets-gated workflow step); full sign + notarize + staple + clean-Mac Gatekeeper check needs a real macOS host with Developer ID + notary credentials.
- **GP-135**: cross-platform native binaries. Manifest/lookup/source verified headless (x86_64 shas match disk bytes; empty aarch64/macOS shas are fail-closed BY DESIGN; mirror tables match). Open host work: per-platform hash population, `build_native_listener_windows.sh` vs `compile_native.ps1` output-dir mismatch to confirm on Windows Git Bash, arch-aware `get_expected_sha256` gap (owned elsewhere), live per-OS runs.
- **VT-1**: Windows host validation. Code verified present (`config/loader.py` warnings registry, `_timeout_utils` TIMEOUT runner, `tray_lifecycle.py:151-177` degradation) + imports/unit green (`test_timeout_utils` 34 passed); live Windows terminal re-run needs a real host.

---

# FV Session — 2026-09-19 (INVESTIGATION, GROUP 0, 7 sub-agents, orchestrator-verified)

### FV-19 — Silero VAD v5 is available (current bundle is v4)
**Status:** ❌ Not Fixed (2026-09-21 FV session: evaluated and deliberately left. A model swap is a design decision (v5 needs 576-sample chunking + a different state shape) and its claimed speedup cannot be validated without real-host audio calibration of `vad_speech_threshold`; shipping it unvalidated risks silently degrading core dictation (AGENTS.md E12). No code changed.)
**Description:** The bundled VAD model is the v4 export; v5 is released upstream with claimed ONNX speedups (upstream compares ONNX-vs-torch at 4-5x; the v4→v5 swap benefit is unquantified, per Review Wave 2). Note: v5 is NOT a drop-in — at 16 kHz it requires a 576-sample input tensor (64 rolling context + 512 new samples) vs the current 512-sample contract. This is a model swap, which is a design decision, not a silent upgrade.
**User Impact:** Potentially faster/more accurate voice-activity detection; VAD is already sub-millisecond per call so practical urgency is low.
**Root Cause:** Verified (v4 asset in tree; v5 exists upstream — web-verified against the Silero VAD repository).
**Gain vs Trade-off:** Claimed ONNX speedups (upstream compares ONNX-vs-torch at 4-5x; the v4→v5 swap benefit is unquantified, per Review Wave 2) and possibly better accuracy; trade-off: input chunking (576-sample), state-shape, and I/O-name changes in the integration, plus the speech-threshold setting may need retuning (calibrated differently).
**If We Do It:** Faster VAD with the same latency contract; threshold semantics validated.
**If We Don't:** Nothing breaks; v4 keeps working.
**My Recommendation:** 🟡 Try and revert — evaluate the swap behind the existing probability calibration, treat threshold retuning as part of it.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/vad.py:91,97` (`_VAD_MODEL_PATH`, `_VAD_STATE_SHAPE`)
- `voice_typer/server/silero_vad.onnx` (the v4 asset)
**Fix:** Swap the bundled asset for the v5 export; adapt the input chunking (576-sample input = 64 rolling context + 512 new samples, per Review Wave 2), `_VAD_STATE_SHAPE` (different state shape), and the I/O-name discovery (already name-driven, vad.py:216-222); re-validate `vad_speech_threshold` calibration.
**Simplified Fix:** A newer version of the voice-activity detection model is published, claiming to be about three times faster. Swapping it in is a deliberate upgrade, not a bug fix, and needs its sensitivity re-tuned.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### FV-21 — Clipboard password-field safety fails open when UIA is unavailable
**Status:** ✅ Fixed (2026-09-21: macOS/Linux password checks and the non-Windows dispatcher now fail closed when detection infra is unavailable; auto-paste suppressed, text stays on clipboard. Windows UIA path was already fail-closed. Tests: tests/clipboard/test_password_field_fail_closed.py + updated test_clipboard_password_detection.py.)
**Description:** The check that prevents auto-pasting into password fields degrades to "not a password field" when the Windows accessibility API (UIA) is unavailable, so dictation auto-paste can land in a password field during those windows. This is a documented deliberate tradeoff (a UIA hiccup must never block pasting), flagged here for the record.
**User Impact:** Rare: dictated text could auto-paste into a password field during a UIA failure window. The alternative (fail closed) would block all pasting whenever UIA hiccups — worse for the core flow.
**Root Cause:** Deliberate fail-open design (documented inline).
**Gain vs Trade-off:** Optional hardening: suppress auto-paste on check failure, keeping the text in the clipboard for manual paste — the fallback path already exists. Trade-off: pasting silently requires one manual Ctrl+V during UIA hiccups.
**If We Do It:** Password fields can never receive auto-pasted dictation even during UIA failures.
**If We Don't:** The rare window persists; dictated text in a password field is masked and must be cleared manually.
**My Recommendation:** 🟡 Try and revert — product call between availability and fail-closed.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/clipboard_target_safety/validation.py:47-70`
- `voice_typer/server/clipboard_target_safety/injection.py:127-134`
**Fix:** When the safety check fails open, suppress auto-paste and keep text in clipboard for manual paste (fallback message already exists at clipboard/manager/_paste.py:163).
**Simplified Fix:** In the rare moments Windows can't tell the app what field is focused, the app currently guesses "safe" and pastes. The safer guess is "hold the text and let the user paste it themselves."
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-31 — tests/ root holds 716 flat test files (243 already live in 26 domain subdirs)
**Status:** ❌ Not Fixed (2026-09-21 FV session: re-confirmed 727 flat root files; the entry's own recommendation is 🟡 Defer — opportunistic migration only when a file is already being edited, since a bulk move is churn-for-churn. No bulk move performed.)
**Description:** Newer test domains get subdirectories, but 716 legacy files still sit flat at the tests/ root. Largest sampled files are single-domain (no catch-all mixing found — the E3 violation pattern is absent), so this is navigability cost, not a correctness risk.
**User Impact:** None directly; contributors spend time finding the right test file in a 716-file flat directory.
**Root Cause:** Verified — legacy files never migrated as the subdirs evolved.
**Gain vs Trade-off:** Pure organization; risk-free if done opportunistically (migrate files only when already touched, per E1 create-first).
**If We Do It:** Test layout matches the domain structure that already exists.
**If We Don't:** Navigability cost persists and grows.
**My Recommendation:** 🟡 Defer — opportunistic migration only when files are touched anyway; a bulk move is churn-for-churn.
**Progress:** `None yet.`
**Related Files:**
- `tests/` (716 flat root files vs 243 in 26 subdirs)
**Fix:** Opportunistic migration of root files into existing subdirs when touched (no bulk move). Manual/ scripts stay uncollected by design (no test_ prefix).
**Simplified Fix:** Most new test files are organized into folders by topic, but hundreds of old ones sit loose at the root. Moving them only when already editing them avoids churn while slowly tidying.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### FV-37 — Entry modules carry module-top test-seam re-exports (`# noqa: F401`) that attract new patch sites
**Status:** ❌ Not Fixed (2026-09-21 FV session: re-confirmed (`ipc_server.py` 18, `app.py` 10 `noqa: F401`). The entry requires an incremental, never-batch migration tied to E1 create-first; no file in this session's scope needed it. Left for opportunistic migration.)
**Description:** The backend entry modules re-export 15+ names purely so old tests can patch them at the entry-module path, with noqa noise on each. The split was meant to remove this coupling; the re-exports keep the entry module a patch attractor, so every new test is incentivized to patch the wrong place, re-growing the coupling.
**User Impact:** None; maintainability cost.
**Root Cause:** Verified — historical patch sites left pointing at the entry module when the god-module was split.
**Gain vs Trade-off:** Incremental migration (only when already touching a file, rewriting the pinned tests in the same change per E1) vs the current silent growth. Batch-swap is explicitly wrong.
**If We Do It:** Entry modules shrink toward the wiring-only budget and the patch surface stops growing.
**If We Don't:** The attractor persists.
**My Recommendation:** 🟡 Defer — opportunistic, never batch.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc_server.py:11-41`
- `voice_typer/server/app.py:8-83`
- Cross-reference: FV-38 (app.py line budget — the same re-export blocks)
**Fix:** Incremental (only when already touching a file): migrate patch sites to the owning submodule per the C-ARCH-2 shape and drop the re-export; each migration must rewrite the pinned tests in the same change (E1 create-first); do NOT batch-swap.
**Simplified Fix:** Old tests reach into the app's front-door modules to swap out internals. Migrating them to the modules that own those internals — one file at a time, only when already editing it — keeps the front doors thin.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

---

# FV Wave 3 Additions — 2026-09-19 (deeper-pass investigation, 7 sub-agents incl. 1 re-dispatch)

> Findings from the deep reads of files Wave 1 could not fully cover, plus edge cases. All NOT FIXED (investigation mode). Ordered Critical → High → Medium → Low.

## FV Critical

### FV-39 — macOS/Linux release pipelines build a deleted prewarm module: any dispatch fails mid-job (plus dead prewarm scripts)
**Status:** ❌ Not Fixed — SKIPPED this session, conflicts with AGENTS.md Hard "Don'ts": C-CI-2 (the fix edits `tauri-macos-build.yml` / `tauri-linux-build.yml`, which are protected; any change must be user-validated by a full re-run). Its own recommendation is 🟡 Defer to the owner. Recorded in `worklog.md`.
**Description:** The prewarm pipeline was migrated to the slim-core worker (commit 46cf40fa deleted `voice_typer/server/prewarm/__main__.py`; the directory now holds only `__init__.py`, `cache_probe.py`, `status.py`), and the Windows workflow removed its prewarm step after a FATAL on main (noted at tauri-windows-build.yml:563-570). But the macOS workflow still runs `scripts/build/build_prewarm_macos.sh` (steps at :250, :447) and Linux runs `build_prewarm_linux.sh` (:647) — both scripts' Nuitka invocations target the deleted module, and macOS lists `prewarm-<triple>` artifact uploads with `if-no-files-found: error` (:297). The three build_prewarm_*.sh scripts and the retired PyInstaller packaging (voice-typer.spec + the `pyinstaller` build extra in pyproject.toml) are dead-but-invokable.
**User Impact:** Any dispatch of the release pipeline (tauri-build.yml all/macos/linux) at HEAD fails mid-job after the expensive sidecar build — macOS/Linux release builds are red, and the all-platforms manifest gate can never pass. No user-facing impact in the shipped app (releases are manual-dispatch only), but the release capability is silently broken.
**Root Cause:** Verified — plan-runtime-pack-split §6.2 P-1 removal was applied to the Windows workflow only; the sibling workflows, the build scripts, and the legacy packaging extra were never swept.
**Gain vs Trade-off:** Mirroring the Windows removal (drop prewarm steps + artifact paths, delete the three scripts + spec + extra, record in archive/deleted_files.txt) un-breaks the pipelines; the risk is that these are the repo's most fragile CI files and any edit must be validated by a full re-run.
**If We Do It:** macOS/Linux release dispatches proceed past prewarm to the worker-based path; dead scripts stop inviting invocation.
**If We Don't:** The release pipeline for two platforms is broken until someone dispatches and debugs it live.
**My Recommendation:** 🟡 Defer to user — the fix edits C-CI-2-protected workflows, so it requires user confirmation and a full validated re-run per the rule. Would conflict with AGENTS.md: C-CI-2 (protected workflow files; the change is genuinely required but must be user-validated).
**Progress:** `None yet.`
**Related Files:**
- `.github/workflows/tauri-macos-build.yml:250,297,447`
- `.github/workflows/tauri-linux-build.yml:647`
- `scripts/build/build_prewarm_macos.sh:176`, `build_prewarm_linux.sh:166`, `build_prewarm_windows.sh:190`
- `scripts/build/voice-typer.spec`, `pyproject.toml` ([build] extra `pyinstaller`)
**Fix:** Mirror the Windows removal in the macOS/Linux workflows (drop prewarm build steps + prewarm artifact paths — note Linux :652-660 carries a second uncited hard-fail `test -x $PREWARM` site, per Review Wave 4), delete the three build_prewarm_*.sh scripts, the voice-typer.spec, and the pyinstaller extra; record all deletions in archive/deleted_files.txt per E15 (the ledger does not currently exist on disk — deleted at ebce5598 — so RECREATE it). USER MUST CONFIRM + validate with a full re-run (C-CI-2), and the AGENTS.md update is user-owned: the fix also conflicts with the letter of C-CI-9, C-CI-11, and C-CI-13 — a coordinated user-owned AGENTS.md edit is required alongside (per Review Wave 4).
**Simplified Fix:** The Mac and Linux build recipes still try to compile a helper program that was deleted weeks ago, so those builds fail every time they run. Removing the dead steps and leftover scripts — with the owner's approval, since these recipes are deliberately protected — makes the builds work again.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🔴 Critical

## FV Medium Priority

### FV-48 — `_handle_set_config` is a single 413-line method mixing eight concerns
**Status:** ❌ Not Fixed
**Description:** The config-set handler — the single most security-sensitive handler (the SEC-002 allowlist path) — is one method mixing payload validation, echo accounting, bubble-position clearing, model swap with loading status, backend swap, failed-key persistence filtering, URL-allowlist re-apply, tray-cache invalidation, config/bubble pushes, and envelope assembly.
**User Impact:** None directly; the change-risk on this handler is disproportionately high and review burden grows with every accreted concern.
**Root Cause:** Verified — accretion; the comments narrate each addition.
**Gain vs Trade-off:** Extract phases into helpers with explicit inputs/outputs; no behavior change; handler-envelope pins stay green.
**If We Do It:** The most-audited handler becomes reviewable in units.
**If We Don't:** Every future settings feature grows the monolith.
**My Recommendation:** ✅ Implement (next time the handler is touched, or as a dedicated refactor).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/handlers/config_handlers.py:93-506`
**Fix:** Extract phases (validate → apply model/backend → persist → side-effects → respond) into helpers with explicit inputs/outputs; keep response envelopes identical.
**Simplified Fix:** The function that saves settings is one giant block doing eight different jobs. Splitting it into named steps makes it safe to change and much easier to review.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### FV-50 — 158 task-ID comment remnants (57 Python + 101 Rust) violate the no-task-IDs-in-code rule
**Status:** ❌ Not Fixed
**Description:** 57 occurrences across 27 Python server files plus 101 across 43 Rust source files (per Review Wave 4's repo-wide recount; the Wave-3 count of 14 was slice-scoped) still carry session/task tags (`R13-F3`, `F11-FIX`, `P4-A9`, `SVC-10`, `WAL-CHECKPOINT-FIX`, `MO-1xx`, `T3-05`, `TR-4`, …) — the residue of a past sweep that stripped tags incompletely. Pervasive grammar artifacts of the stripping ("(fix):", " : replaces", dangling double spaces) compound the readability cost.
**User Impact:** None; the IDs are meaningless noise for future sessions (their review.md entries are long gone) and the dangling artifacts hurt readability.
**Root Cause:** Verified — incomplete tag-stripping sweep.
**Gain vs Trade-off:** Purpose-named prose replaces tags; opportunistic per-file, never batch (the sanctioned greppable tags SEC-*/RACE-*/PERF-*/ADR-* must be preserved).
**If We Do It:** Comments describe durable facts; the rule's letter is met.
**If We Don't:** Noise persists and invites more tagging.
**My Recommendation:** ✅ Implement (opportunistic sweep; a dedicated session could finish it in one pass).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/validation.py:10,759`, `handlers/history_handlers.py:324,386,406,430`, `handlers/config_handlers.py:143`, `ipc/registry.py:431`, `service/onboarding.py:233` (Python representative sites)
- `src-tauri/src/sidecar/supervisor.rs:230`, `platform/power.rs`, `main.rs:1`, `state.rs`, `spawn.rs` (Rust representative sites — 90 of the 101 are MO-NNN line-tags; full list via `rg 'MO-[0-9]+|T3-05|TR-4|M-65' src-tauri/src`)
**Fix:** Sweep the ~70 affected files replacing task-ID tags with purpose-named prose (the Python families QUIT-CLEAN-001/PLAT-HLEAK/DB-LOCK-FIX/WAL-CHECKPOINT-FIX/IMPL-A are residue to replace, per Review Wave 4); KEEP the sanctioned greppable tags (SEC-*, RACE-*, PERF-*, ADR-*, NU-*, IPD-*, TX-* — these are documented conventions); fix the stripping grammar artifacts. Opportunistic per-file or one dedicated session — never a blind batch (E1).
**Simplified Fix:** Comments in the code still reference long-retired work-item numbers, leftovers of a half-finished cleanup. Rewording them to describe what the code does makes the comments useful again.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### FV-52 — Recorder duplicates the canonical microphone-id resolver in a ~130-line hand-rolled ladder (E7)
**Status:** ❌ Not Fixed
**Description:** The recorder's device-resolution code re-implements the id-resolution ladder that the project's own rules designate as THE shared resolvers (`resolve_mic_id_to_device_index` / `find_microphone_by_id` in `server_platform/microphone_list.py`, per C-MIC-2) — with divergent unresolvable-fallback semantics (the copy falls back to a stale index/name string; the canonical one falls back to None → System Default).
**User Impact:** Latent: the two ladders drift apart per future id-shape change; today they agree by luck.
**Root Cause:** Verified — the recorder path predates the canonical resolver and was never consolidated. (Nuance per Review Wave 4: the ladder already consults `find_microphone_by_id` for non-digit strings; the true duplication is the legacy-compound tail plus the divergent unresolvable-fallback semantics.)
**Gain vs Trade-off:** Delegating to the canonical resolver (keeping the recorder's candidate policy on top) removes the duplication; C-MIC-2 actively favors this.
**If We Do It:** One id-resolution truth; C-MIC-2's contract is enforced by construction.
**If We Don't:** Two ladders to maintain and fix per change.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/recording/device_manager.py:1006-1137` (the ladder)
- `voice_typer/server/server_platform/microphone_list.py:246,579` (the canonical resolvers)
**Fix:** Delegate the resolution step to the canonical resolvers; keep the recorder's candidate-selection policy layered on top; add a test that patches a resolver to confirm the recorder path consults it.
**Simplified Fix:** Two copies of the "find the chosen microphone" logic exist, one of them old and slightly different. Making the old copy call the shared one removes the difference.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### FV-57 — Model-integrity cache trusts metadata over bytes (forgeable by user-privileged malware)
**Status:** ❌ Not Fixed
**Description:** The model-integrity cache (user-writable JSON keyed by repo/path/mtime/size → sha256) lets verification skip reading the model bytes when metadata matches. An attacker with user-level write access can preserve mtime+size while substituting bytes, or forge the cache directly to the pinned hash — verification then never reads the file. The comment claims the cache "does NOT weaken the security guarantee", which is incorrect under that threat model.
**User Impact:** Defense-in-depth erosion, not a new RCE: user-privileged malware can already do worse. The integrity check's purpose (detect corruption/tampering of model files) silently degrades to a metadata check.
**Root Cause:** Verified (code + cache format) — cache hit trusts metadata over bytes.
**Gain vs Trade-off:** Bind cache entries to a non-forgeable anchor (MAC keyed by an install-dir secret) or restrict the cache to the failure-details path; at minimum correct the security comment.
**If We Do It:** The integrity guarantee matches its documented claim.
**If We Don't:** The security comment overstates the guarantee.
**My Recommendation:** ✅ Implement (comment fix at minimum; MAC binding as the full fix).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/security/model_integrity.py:197-215,322-359`
**Fix:** MAC-bind cache entries to an app-install-dir secret, or restrict the cache to failure-detail reporting; correct the security comment either way.
**Simplified Fix:** The "are the speech models genuine?" check keeps a shortcut file so it doesn't re-read big files every launch. Software with enough access could edit that shortcut to lie. Anchoring the shortcut to a secret the attacker can't forge restores the guarantee.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### FV-60 — pytest is pinned `<9` while current stable is 9.1.1 (deliberate cap, undocumented reason)
**Status:** ❌ Not Fixed
**Description:** pyproject caps `pytest>=7,<9` — the current stable major (9.1.1, June 2026) is excluded. Every other toolchain dependency is current (ruff 0.16.8, mypy 1.20, react 19.3, vite 8.3, vitest 5.0.1, tauri 2.6.3, tokio 1.47 LTS; lockfile hash-pinned; npm audit clean). The one-major-behind runner cap has no documented reason.
**User Impact:** None direct; the suite misses current pytest fixes/features and the eventual major-bump becomes larger the longer it waits.
**Root Cause:** Verified pin + current-version check (web); reason for the cap undocumented.
**Gain vs Trade-off:** Evaluate `<10` after reviewing pytest 9's breaking changes (plugin API, assertion repr changes) against the suite's heavy customization (xdist, importlib mode, custom conftest).
**If We Do It:** The runner tracks current; the bump is reviewed rather than accidental.
**If We Don't:** The gap grows until an unrelated dependency forces a rushed migration.
**My Recommendation:** 🟡 Try and revert — bump on a branch, run the full suite, revert if red (web-search the pytest 9 breaking-changes list first).
**Progress:** `None yet.`
**Related Files:**
- `pyproject.toml:329`
**Fix:** Evaluate the `<10` cap after a pytest 9 breaking-change review + full-suite run.
**Simplified Fix:** The test runner is held one major version back with no written reason. Checking what changed in the new version, then upgrading on a trial branch, keeps the project current without gambling the suite.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

## FV Low Priority

### FV-64 — `VT_EARLY_SERVER_STARTED` migration escape hatch has no recorded sunset (~270 LOC permanent second path)
**Status:** ❌ Not Fixed
**Description:** A default-OFF env-var escape hatch for a launch-order migration carries ~270 production lines across two modules (early-bind branch, startup thread, bounded buffer/drain/flush) plus a test file — with no sunset date recorded anywhere. If never retired, it doubles the entry point's reasoning surface permanently.
**User Impact:** None; maintenance-only.
**Root Cause:** Verified — deliberate escape hatch, no sunset recorded.
**Gain vs Trade-off:** If the migration release shipped, delete the mode (E15); otherwise record the sunset release in the comment.
**If We Do It:** One launch-order code path.
**If We Don't:** The second path lives forever.
**My Recommendation:** 🟡 Defer to user — retirement timing is a product call.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/entrypoint.py:176-196,467-507,593-599,745-813`
- `voice_typer/server/sidecar_ws.py:674-813`
**Fix:** If the migration release has shipped: delete the mode (record per E15). Otherwise: record the sunset release in the env-var comment.
**Simplified Fix:** A temporary alternate startup mode, added to ease a past transition, was never given an end date. Either removing it now that the transition is done, or writing down when it ends, stops it from becoming permanent.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### FV-69 — `_recorder_split.py` (1668 lines) still holds 4 concerns + machine-generated locals (`_uu36_*`)
**Status:** ❌ Not Fixed
**Description:** The module carries the buffer class, snapshot logic, start/stop, and discard in one file, with its own header documenting a split plan "to be completed", plus machine-generated local names (`_uu36_sizing_sr` etc.) that survived a refactor. It is a helper module, not an entry file, so no E3 letter violation — but the documented plan was never finished.
**User Impact:** None; navigability cost on the largest recording module.
**Root Cause:** Verified — incomplete follow-through on the module's own documented split plan.
**Gain vs Trade-off:** Finishing the create-first split (E1) removes the last concerns; renaming locals is free.
**If We Do It:** The module's structure matches its own plan.
**If We Don't:** The debt grows with every edit.
**My Recommendation:** 🟡 Defer — finish opportunistically when the module is next touched for a real change.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/recording/_recorder_split.py:1-41,1052-1060`
**Fix:** Complete the documented create-first split (buffer/snapshot/lifecycle/discard modules with re-exports per E1); rename the `_uu36_*` locals to purpose names.
**Simplified Fix:** The main recording file was planned to be split into focused pieces, but the plan was never finished, and it still contains auto-generated placeholder names from an old tool. Completing the split and giving things real names makes it navigable.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### FV-74 — Level monitor holds its lock across device query + stream open (50-200 ms) — stalls level polls during device switch
**Status:** ❌ Not Fixed
**Description:** The monitor lock is held across `query_devices` + `InputStream(...)` + `start()` while the old-stream close was deliberately moved outside the same lock — an internal inconsistency that stalls `get_level()` IPC polls and worker drain during a device switch (cosmetic surfaces only, no RT impact).
**User Impact:** Brief level-bar freeze during device switches.
**Root Cause:** Verified — lock scope grew with the open path.
**Gain vs Trade-off:** Query/open outside, re-check-and-commit under the lock; standard pattern, low risk.
**If We Do It:** Level polls stay responsive during switches.
**If We Don't:** Cosmetic stall persists.
**My Recommendation:** ✅ Implement (next time the monitor is touched).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/level_monitor/monitoring.py:557-751`
**Fix:** Do query/open outside the lock; re-check state and commit under the lock (standard double-check pattern).
**Simplified Fix:** While switching microphones, the level meter holds an internal lock through the slow device-opening step, briefly freezing the on-screen level bar. Doing the slow step first and taking the lock only to record the result keeps the bar live.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### FV-88 — URL allowlist resolves DNS at validation time; the connection re-resolves at connect time (TOCTOU)
**Status:** ❌ Not Fixed
**Description:** The allowlist check resolves the hostname to an IP at validation; the HTTP client then re-resolves at connect time — a classic check-then-connect race. An attacker controlling DNS for an allowlisted host could pass validation with a public IP and connect to a private one (the API-key-exfiltration class the check targets). Allowlist + HTTPS are still enforced; the gap is documented in the docstring.
**User Impact:** Low under current threats; a documented defense-in-depth gap.
**Root Cause:** Verified design limitation (acknowledged inline).
**Gain vs Trade-off:** Pin the resolved IP with Host/SNI, or re-verify the peer IP post-connect; moderate complexity.
**If We Do It:** Validation and connection agree on the target.
**If We Don't:** The acknowledged gap persists.
**My Recommendation:** 🟡 Try and revert — evaluate the pinning approach; the docstring's acknowledgment may be the accepted state.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/security/url_allowlist.py:517-542`
**Fix:** Pin the validated IP into the request (Host/SNI) or re-verify the peer IP post-connect.
**Simplified Fix:** The "is this address allowed?" check looks up where the address points at approval time, but the actual connection looks it up again — and the answer can change between the two. Making the connection use the address that was approved closes the window.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low

### FV-90 — Tray menu `accelerator` field: documented as Python-populated and shortcut-wiring; no producer exists
**Status:** ✅ Fixed (2026-09-21 FV session: the struct doc now states no producer exists, that a producer would only render a key-equivalent hint, and that global hotkeys live in `shortcuts.rs`; verified zero `accelerator` producers in the Python tree — `cargo check` Finished ON WINDOWS).
**Description:** The tray menu struct's `accelerator` field doc claims it's "populated by the Python sidecar's build_tray_menu_model" and wires a global shortcut — but grep finds no producer anywhere in the Python tree; only tests exercise it. Tauri v2 tray accelerators are (suspected) open-menu key-equivalents, not global hotkeys.
**User Impact:** None today; a future agent populating accelerators expecting global hotkeys gets silent nothing.
**Root Cause:** Verified no-producer; suspected platform-semantics overclaim.
**Gain vs Trade-off:** Correct the comment or drop the field.
**If We Do It:** The tray struct's docs stop promising a feature that doesn't exist.
**If We Don't:** Misdirection persists on a C-TRAY-protected surface.
**My Recommendation:** ✅ Implement (comment fix; dropping the field touches tray code — C-TRAY rules apply to menu CONTENT, not struct docs, so a doc fix is safe).
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/tray/menu.rs:36-53,124-137`
**Fix:** Correct the comment (no producer; accelerators are menu key-equivalents at most) or drop the unused field.
**Simplified Fix:** The tray menu's shortcut field is documented as filled in by the backend and wiring global hotkeys — but nothing fills it. Fixing the note stops someone from building on the phantom feature.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-92 — Dismiss-hotkey on an idle bubble fire-and-forgets a dictation toggle (suspected hidden recording)
**Status:** ✅ Fixed (2026-09-21: Python `toggle_dictation` is start/stop (`service/dictation.py`). Host now caches last `tray_state` icon (`SidecarState.last_tray_icon`); dismiss accelerator calls `toggle_dictation` only when last icon is `recording`/`transcribing`, else hide-only. Tests: shortcuts_tests.rs + global_shortcut_parity.py green. cargo check Finished.)
**Description:** The bubble-dismiss path always fire-and-forgets `toggle_dictation` before hiding the window. If the bubble is visible-but-idle (not recording), the toggle STARTS a dictation and then the bubble hides — potentially leaving the microphone recording with no visible indicator. The documented Python semantics (`service/dictation.py:36-38`: "Start or stop dictation") directly contradict the in-code comment's claim that idle toggle "returns the current state" (citation added by Review Wave 4); a host run is still needed to confirm the end-to-end behavior.
**User Impact:** Suspected: a user pressing the dismiss key on an idle bubble starts a recording they can't see. Needs Python-side verification + a Windows/macOS host for the bubble window.
**Root Cause:** Suspected — blind toggle instead of state-gated cancel.
**Gain vs Trade-off:** Gate on the host-known recording state or use an idempotent cancel command.
**If We Do It:** Dismiss never starts recording.
**If We Don't:** The suspected hidden-recording edge persists.
**My Recommendation:** 🟡 Try and revert — verify Python-side toggle semantics first; if confirmed, gate the dismiss path.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/shortcuts.rs:63-88`
**Fix:** Verify `toggle_dictation`'s idle behavior Python-side; if it starts recording, gate the dismiss path on the host-known recording state (or call an idempotent cancel). VALIDATE on host.
**Simplified Fix:** The key that dismisses the small floating window also fires a "start/stop dictation" command blindly — so dismissing an idle window might start a recording with no window showing it. Checking the actual state before toggling prevents the invisible recording.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-93 — Workflow comment drift: line-number citations rotted across tauri-*.yml and mutation.yml
**Status:** ✅ Fixed (2026-09-21: comment-bloat pass already replaced rotted line-number citations with named steps / `docs/code-notes/ci-workflows.md` pointers; numpy pin comments cite `nuitka==2.8.10` + lockfile guidance without stale `numpy==2.5.1` line refs; mutation.yml no longer cites pyproject `:281`; re-grepped workflows — no remaining `:line` / `see :N` citations. Comment-only C-CI-2-safe.)
**Description:** Multiple workflow comments cite line numbers that have drifted (uv/PYBS step refs off by ~20 lines; a job-level block cited at its old location; "numpy==2.5.1" vs lockfile 2.5.2; mutation.yml's pyproject line refs off by ~128 (mutmut config now at :409 vs cited :281 — magnitude corrected by Review Wave 4); codeql's "8 workflow files" vs 12). AGENTS.md calls these tags greppable anchors — drift misleads every audit that trusts them.
**User Impact:** None; audit misdirection on protected files.
**Root Cause:** Verified — comments not updated as files grew.
**Gain vs Trade-off:** Comment-text refresh only (no structural change — C-CI-2 safe); prefer naming symbols over line numbers where possible.
**If We Do It:** The anchors point where they claim.
**If We Don't:** Audits keep chasing moved lines.
**My Recommendation:** ✅ Implement (comment-only edits inside protected files are documentation, not structure — but batch with the next user-validated workflow change to minimize churn).
**Progress:** `None yet.`
**Related Files:**
- `.github/workflows/tauri-windows-build.yml:14,16,160`, `tauri-macos-build.yml:503`, `mutation.yml:9,32`, `codeql.yml:63`
**Fix:** Refresh the stale line refs (or replace line numbers with named steps/symbols); fix the numpy version citation.
**Simplified Fix:** Cross-reference notes inside the build recipes point at line numbers that have shifted as the files grew. Refreshing the pointers (or naming steps instead of lines) keeps the notes reliable.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-94 — Fresh-clone build breakage: tauri.conf.json still references 5 linux-scripts resources that the wipe wave deleted from the tree
**Status:** ✅ Fixed (2026-09-21 option b): `src-tauri/resources/linux-scripts/*` restored + tracked (5 files); `scripts/gen_tauri_icons_stub.py` now `_sync_linux_scripts()` on generate and `_linux_scripts_problems()` on `--check` (copies from canonical `scripts/linux/`). Config refs kept — they are intentional bundle.resources. Tests: `tests/tauri/test_gen_tauri_icons_stub.py` **52 passed, 2 skipped** (verified 2026-09-21; `test_linux_installer_paths.py` does not exist — earlier "134 passed" claim was wrong). `cargo tauri build` still needs those files present at build time; generator now heals missing copies.)
**Description:** `src-tauri/tauri.conf.json:86-90` (and both per-arch Linux configs) reference five files under `resources/linux-scripts/` (install_permissions.py, uninstall_permissions.py, 99-voice-typer.rules, 00-voice-typer-capslock.conf, voice-typer.polkit) — but commit 647daa5f ("complete Electron/Torch wipe waves") deleted the tracked `src-tauri/resources/linux-scripts/` files AND the guarding test (`tests/test_windows_installer_extra_resources.py`) while leaving the config references live. A fresh clone therefore fails `cargo check` (and any tauri build) with `resource path 'resources/linux-scripts/install_permissions.py' doesn't exist` — reproduced live this session after the stub generator correctly created `bin/` and `resources/native/` but not `linux-scripts/`. The .gitignore asymmetry (native/ and prewarm-* are ignored; linux-scripts/ is not) shows the deletion wave expected the whole resources tree gone. Sibling of FV-39 (same incomplete-removal class, different mechanism: config resource refs vs workflow steps).
**User Impact:** A new contributor's first `cargo check`/dev build fails on a fresh clone with an error that names a file they cannot find anywhere in the repo (the canonical copies live at `scripts/linux/`, undocumented for this purpose); the C-TDEV-1 one-command dev recipe is broken until manual copies are made. CI release builds fail the same way unless a workflow step copies the files (the last full CI success predates the wipe wave — see FV-39's evidence).
**Root Cause:** Verified — git show 647daa5f --stat (deletions) vs tauri.conf.json:86-90 (live references) vs live cargo-check reproduction on a fresh clone.
**Gain vs Trade-off:** Two candidate fixes: (a) remove the config references if the resources are genuinely retired (coordinated with FV-39's user-validated workflow cleanup), or (b) restore the files / add a generator step that copies them from scripts/linux/ (the canonical source, verified byte-identical by check_linux_scripts_lf.py's design). Decision belongs to the user (intent of the wipe wave).
**If We Do It:** Fresh clones build; the dev recipe works as documented.
**If We Don't:** Every new contributor and every CI dispatch hits the same wall and burns time diagnosing a config-vs-tree mismatch.
**My Recommendation:** 🟡 Defer to user — one product question (were the linux-scripts resources meant to be retired with the wipe wave, or kept?) decides between (a) and (b).
**Progress:** `None yet.` (dev-env workaround applied this session: untracked copies from scripts/linux/ — the tracked tree is untouched)
**Related Files:**
- `src-tauri/tauri.conf.json:86-90`, `src-tauri/tauri.linux-x86_64.conf.json:7-11`, `src-tauri/tauri.linux-aarch64.conf.json`
- `scripts/linux/` (the canonical sources)
- `scripts/gen_tauri_icons_stub.py` (creates bin/ + native/ but not linux-scripts/)
- git 647daa5f (the deletion), `tests/test_windows_installer_extra_resources.py` (deleted guard)
**Fix:** User decision: (a) remove the resource references from tauri.conf.json + per-arch Linux configs (retire them; coordinate with FV-39's cleanup and record in archive/deleted_files.txt), or (b) make the stub generator (or a documented prep step) copy the five files from scripts/linux/ into src-tauri/resources/linux-scripts/ AND add a .gitignore rule for the directory (matching native/ + prewarm-*).
**Simplified Fix:** The build recipe lists five Linux helper files that were deleted from the project but never removed from the recipe — so a fresh copy of the project cannot build. Either take them off the recipe (if they're retired) or put them back automatically from the folder that still holds the originals.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### FV-95 — 10 pre-existing pytest failures at HEAD on Linux (5 clustered in mic-test quality grading, order-dependent)
**Status:** ❌ Not Fixed
**Description:** The full-suite chunked run (16 chunks, Σ collected = 15,506 = total, coverage PROVEN) on this Linux sandbox found 10 failing tests at HEAD 97513fa9 with zero session code changes: 5 in `test_mic_test_quality_grading.py` (quality grading returns "very_low" where tests expect "low"/"good"), 2 in `test_install_permissions_gsettings.py` (Sway flow + uninstall restore), and one each in `test_autostart_installer_linux.py` (single-instance plugin wiring), `test_dictation_pipeline_check_resources.py` (RAM ctypes fallback logging), `test_logging_rotation_perms.py` (chmod-inside-lock). Re-running the mic-grading file solo produces a DIFFERENT failure subset (4 failures, 2 names differing from the chunked run's 5) — the failures are order/state-dependent, not deterministic. This also empirically confirms FV-11: CI-errors.md records "No test failures ✅ (0 JUnit files checked)" while 10 real failures exist at HEAD.
**User Impact:** The mic-test quality-grading failures mean the microphone test's quality verdict logic drifted from its pinned expectations (a user's test recording can be graded worse than designed); the others are wiring/persistence expectations that regressed silently. CI's vacuous green means none of this surfaces in the committed status file.
**Root Cause:** Suspected per-test (needs fix-session diagnosis; likely shared-state leakage in the grading module for the order-dependent cluster + three genuine expectation drifts); VERIFIED that the failures exist at HEAD with a clean tree (git status: only review.md modified this session).
**Gain vs Trade-off:** A fix session should diagnose each at root cause (systematic-debugging: reproduce → isolate shared state → fix). No quick fixes — some may be test expectations to update, some may be real regressions.
**If We Do It:** The suite returns to green; CI-errors.md reflects reality.
**If We Don't:** The suite ships 10 red on every full run; the "0 JUnit files" vacuous green keeps hiding them.
**My Recommendation:** ✅ Implement (next FIX_EXISTING session; P0 per the never-grandfather rule).
**Progress:** `None yet.`
**Related Files:**
- `tests/test_mic_test_quality_grading.py` (5 failed; order-dependent cluster)
- `tests/test_install_permissions_gsettings.py` (2 failed)
- `tests/tauri/mig17/test_autostart_installer_linux.py`, `tests/test_dictation_pipeline_check_resources.py`, `tests/test_logging_rotation_perms.py` (1 each)
- full failure list + chunk log: /tmp/chunk_run.log, /tmp/chunk_results.json (session artifacts)
**Fix:** Fix-session work: reproduce each with --tb=short, trace the shared state in the mic-grading module (module-level cache suspected for the order dependence), correct code or expectations at root cause, add the missing isolation if state leakage is confirmed.
**Simplified Fix:** Ten tests fail on a clean copy of the project — five of them flaky depending on run order, which usually means leftover shared state between tests. A repair session should find what's leaking, fix the five flaky ones properly, and check whether the other five point at real behavior changes.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🔴 High
