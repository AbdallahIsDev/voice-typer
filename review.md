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
**Status:** ⚠️ Partial (2026-09-15 wave): headless slices fixed + green headless (signal-handler escalation counter, `binary_path` dead-code cleanup, host-validation workflow import fixes, manifest docstring; focused runs green, ruff + branding clean). Live behavior still needs real hosts per the runbooks.
**Description:** Many platform-specific fixes (Win32 console handler, macOS clipboard restore, native key-listener binaries) have been implemented but only tested on a Linux sandbox. They must be validated on real Windows/macOS hardware.
**User Impact:** Platform-specific regressions may exist on Windows/macOS that are invisible on Linux.
**Root Cause:** No real Windows/macOS desktop session in this sandbox; GHA hosted runners cover only the headless subset.
**Progress:** Headless subset automated in `.github/workflows/host-validation.yml` (undispatched; 2026-09-15 search-first wave extended it: Windows kill-path contracts + safe-handler routing probes, macOS `swiftc` compile + smoke, bundle parity via `plutil`, toolchain presence; contract pins now 8). Real defects fixed headless: `_signal_handler` never incremented `_signal_count`, so second-signal `os._exit(1)` was dead code (one-line fix + `tests/test_signal_delivery_count.py`, 3 passed); `TerminateProcess`-raise handle leak in `teardowns/electron.py` (`try/finally`) + discarded `taskkill` exit now debug-logged (`electron_launcher.py`), covered by 4 new tests; macOS `ClipboardSnapshot` capture/restore edge cases pinned (`tests/test_clipboard_macos_capture.py`, 8 passed) + bundle preflight (`tests/tauri/test_macos_bundle_preflight.py`, 9 passed). Aggregate focused run 129 passed; ruff + branding clean. Live gates blocked on host access + manual dispatch. Known pre-existing (not introduced, left to owner): Windows-fallback teardown test passes solo but fails after controller tests (early `is_windows` stub bind in `teardowns/electron.py`).
**Related Files:** `docs/migration/windows-validation-runbook.md`, `docs/migration/macos-validation-runbook.md`
**Fix:** Run the platform validation runbooks on real Windows and macOS hosts.
**Severity:** 🔴 High
**Priority:** P0

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
