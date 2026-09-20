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
- **WM-14**: Windows `taskkill` behavior. **2026-09-17:** Electron-tree kill path (`electron_launcher.py` + `teardowns/electron.py`) was removed with the Electron host; the synthetic `taskkill /T /F` probe remains in `host-validation.yml`. Live Electron-tree kill is N/A post-removal; Tauri sidecar kill paths are owned by `src-tauri/src/sidecar/`.
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

### MO-86: `_READONLY_COMMANDS` only contains 4 of ~20 pure-read commands
**Status:** ⏸️ DEFERRED — Electron TCP-transport freeze. **2026-09-17 note:** the TCP consumer is gone (Electron removed; WS-only). Revisit only if the WS dispatcher still consults `_READONLY_COMMANDS` for lock-bypass; do the per-handler mutation audit against the surviving WS path. Do not invent Fixed without that audit.
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

---

# FV Session — 2026-09-19 (INVESTIGATION, GROUP 0, 7 sub-agents, orchestrator-verified)

> New findings from investigation session FV. All findings are NOT FIXED (investigation mode: zero code changes this session). Findings are ordered High → Medium → Low within this block. Deduped against WONT_FIX.md (C-REVIEW-1/2) and prior review.md entries. Evidence verified by the orchestrator for cross-confirmed findings (marked).

## FV Medium Priority

### FV-2 — Microphone-change recovery path raises after a failed recorder build instead of recreating the recorder
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** When the user changes the microphone from the tray, the code first saves the new choice, then reads the current recorder object, and finally recreates the recorder with the new device. The recorder is built lazily in the background; if that initial build failed (for example no audio device was available at startup), reading it raises the stored build error — and because the microphone-change handler has no guard for that case, the exception aborts the handler before it reaches the recreate step. The new microphone choice is persisted, but the recorder is never rebuilt, so the failure persists until the app is restarted. The tray callback wrapper catches only SystemExit, so the exception escapes the handler silently — no error message reaches the user.
**User Impact:** A user whose audio system was unavailable at startup (common after reboots with USB mic not yet plugged) fixes it by selecting their microphone from the tray — the one action that should recover — and nothing happens: no error, no recovery, dictation stays broken until they restart the app.
**Root Cause:** Verified (static trace) — the mic-change path predates the lazy-raise/None semantics of the recorder property (app_lazy_hub.py:544-567) and was never updated; the correct guard reference is `service/microphone_test.py:102-112`, which handles both cases (per Review Wave 2).
**Gain vs Trade-off:** Pure improvement — the recreate path already exists at line 168; the fix only adds the same guard used everywhere else. No trade-off identified.
**If We Do It:** Selecting a microphone after a failed recorder build recreates the recorder with the new device, dictation recovers without a restart, and the "Microphone changed" notification appears.
**If We Don't:** The recovery action silently fails for exactly the users who need it most; persisted config then disagrees with live behavior until restart.
**My Recommendation:** ✅ Implement — small, isolated, restores intended behavior.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/settings_controller.py:157,168`
- `voice_typer/server/app_lazy_hub.py:544-567`
**Fix:** Wrap the recorder interaction in `select_microphone` with the raise/None guard pattern used by `service/microphone_test.py:102-112` (which guards BOTH the raise and the None case — the correct reference pattern, per Review Wave 2): treat a raise or None as "no active recorder" and still run the `Recorder(...)` recreation at line 168 (which may succeed with the new device), logging the prior build error. Add a focused test simulating a failed recorder build then a mic change.
**Simplified Fix:** When the audio engine failed to start earlier, changing the microphone currently crashes the change-handler halfway. Making it tolerate the earlier failure lets it finish rebuilding the audio engine with the chosen microphone.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-3 — History pagination: IPC layer allows offset up to 10,000,000 but the database layer asserts offset < 1000
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** Two safety limits were written independently and now disagree. The IPC validation layer clamps the requested page offset to at most ten million, and the database layer asserts that any offset is below 1000 and tells callers to use cursor pagination instead. Any client sending an offset between 1000 and ten million passes validation, then hits the database-layer assertion and gets an opaque internal-error response instead of a bounded page. The assert also uses Python's `assert` statement, which is stripped entirely when the app runs with optimizations enabled (`python -O`), which would re-open the slow deep-scan the clamp exists to prevent.
**User Impact:** Under normal use the renderer pages with cursors, so most users never see this; a buggy, legacy, or hostile client triggers an opaque "something broke" error with no guidance. The real risk is future drift: the two limits can silently diverge further.
**Root Cause:** Verified — the IPC clamp (SEC-010) and the DB-layer deep-offset contract were written independently and never unified (history_bounds.py:178 vs search.py:359).
**Gain vs Trade-off:** Pure improvement — one shared constant replaces two disagreeing ones; no behavior change for valid requests.
**If We Do It:** Deep-offset requests return a clean bounded/empty page (or a precise error) on every execution mode, and both layers read one shared limit.
**If We Don't:** The mismatch remains a trap for any future caller, and optimized builds silently lose the deep-scan protection.
**My Recommendation:** ✅ Implement — small unification with a test.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/history_bounds.py:171-178,243`
- `voice_typer/server/handlers/history_handlers.py:156-169`
- `voice_typer/server/history_db_internals/search.py:344-362,726-732`
**Fix:** Unify the bound: export one shared constant (e.g. `HISTORY_OFFSET_LIMIT = 999`) imported by both the IPC clamp and the DB layer; replace the bare `assert` with a real raise/precise error so it survives `python -O` (prefer the precise-error option — silently clamping offset to 999 would return the wrong page, not an empty one). Update the pinning test `tests/server/test_ipc_history_bounds.py:195-212` (it pins the 10M clamp). Add a test sending offset=1000+ asserting a precise bounded response, not an opaque error.
**Simplified Fix:** Two parts of the app disagree on how far you can page through history. Making them share one limit, and turning the check into a real one that survives optimized mode, removes the trap.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-4 — Sidecar WS auth-failure cleanup can clear a newer connection's sender (race window ~30-45s dead commands)
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** The Rust host guards most connection cleanup with a generation number so an old connection's cleanup never clobbers a new one. The auth-failure cleanup path missed that discipline: it unconditionally clears the shared sender slot. A specific interleaving — cold-start connection waiting in its 3-second auth window while the user clicks Retry (which establishes a newer connection) — lets the old connection's timeout wipe the new connection's live sender. Dispatches then fail fast against a healthy backend until the heartbeat notices and respawns (~30-45 seconds of dead commands), after which everything self-heals.
**User Impact:** Rare but real: a user clicking Retry during startup can land in a half-minute window where every button acts dead ("Lost connection"-style) even though the backend is healthy, then it recovers by itself.
**Root Cause:** Suspected (interleaving derived from code, not reproduced at runtime) — the generation-guard discipline added for reader/writer cleanup (C-WS-3 era) was not applied to `cleanup_and_trigger_respawn` in respawn_scheduler.rs:388-396; its comment justifies only the respawn trigger, not the ungated clear.
**Gain vs Trade-off:** Pure fix — mirrors the exact guard pattern already used by reader.rs:381-389 and writer.rs:103-116; the `None` respawn trigger stays as-is per C-WS-3.
**If We Do It:** The auth-timeout of an old connection can no longer wipe a newer live connection; the Retry-during-startup window works immediately.
**If We Don't:** The rare race persists; users occasionally see a dead UI window after retrying during startup and learn to distrust the Retry button.
**My Recommendation:** ✅ Implement — small, mirrors an existing pattern; needs a regression test.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/sidecar/ws/respawn_scheduler.rs:388-396`
- `src-tauri/src/sidecar/ws/reader.rs:381-389` (the guarded reference pattern)
- `src-tauri/src/sidecar/ws/writer.rs:103-116` (the guarded reference pattern)
**Fix:** Pass the requesting connection's generation into `cleanup_and_trigger_respawn` (call sites are inside `wait_for_auth_ok`, which can receive `my_generation` from `reconnect_ws`), and skip the `ws_tx` clear + pending drain when `state.ws_generation.load(SeqCst) != my_generation` — mirroring reader/writer cleanup. Keep the `None`-generation respawn trigger unchanged (C-WS-3 allows it on auth-failure paths).
**Simplified Fix:** When an old connection gives up waiting for login, it currently also disconnects a newer connection that just succeeded. Teaching it to check whether a newer connection already took over prevents that.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟡 Medium

### FV-5 — Residual TCP transport residue (test-only helpers, dead slots, argparse stub) left after the transport removal
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** The TCP IPC transport was removed in commit 50f4ad67 (2026-09-17, "remove Electron launcher + TCP IPC transport"): the `--port` entry mode now rejects with a clear error and exits (entrypoint.py:306-314), and no TCP listener exists in production code (start_tcp/_accept_tcp have no definitions). What remains is residue: `ipc/transport.py` still carries the test-only `_TCPLineIO` and `_pick_available_port` helpers (consumed by 12+ test files); `ipc_server.py` retains never-assigned `_tcp_*` state slots and TCP write-path machinery in the output mixin; and the entrypoint still parses the now-unsupported `--port` argument. (Corrected by Review Wave 2 — the original Wave-1 claim that the TCP listener surface was "live and invocable" was FALSE, verified at entrypoint.py:306-314 and git 50f4ad67.)
**User Impact:** No direct user impact; maintenance cost only — dead slots and test-only helpers in production modules keep entry files above their wiring budget and invite confusion about which transport is live.
**Root Cause:** Verified — the 50f4ad67 removal deleted the listener but left the helper/slot residue.
**Gain vs Trade-off:** Cleanup shrinks production modules and removes ambiguity; the test-only helpers need a decision (migrate consuming tests or relocate helpers to test support). Deletions recorded per E15.
**If We Do It:** One unambiguous transport story (WS + gated stdin); entry modules shrink.
**If We Don't:** Residue keeps accreting and misleading future readers.
**My Recommendation:** ✅ Implement — small, now correctly scoped after review.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/transport.py` (test-only `_TCPLineIO`, `_pick_available_port`)
- `voice_typer/server/ipc_server.py` (never-assigned `_tcp_*` slots, OutputMixin TCP write path)
- `voice_typer/server/ipc/entrypoint.py` (`--port` argparse stub, rejecting at :306-314)
**Fix:** Decide the fate of the test-only helpers (migrate consuming tests to WS fixtures, or move helpers to a test-support location), then delete the never-assigned `_tcp_*` slots, the dead OutputMixin TCP write path, and the `--port` argparse stub (it already exits with an error — removing it entirely is safe once no test relies on the rejection message). Record deletions in archive/deleted_files.txt per E15.
**Simplified Fix:** The old communication channel was already removed, but leftover parts of it (helpers only tests use, dead internal slots, a rejected command-line option) still sit in the code. Deciding where the test helpers belong and deleting the rest finishes the cleanup.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low (downgraded from Medium by Review Wave 2 — the live-surface claim was false)

### FV-6 — Backend core docstrings/comments still describe the retired TCP/predecessor transport as live
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** ≈130 raw rg matches for TCP/predecessor references across the backend core (127 counted by the investigator, including identifiers) still describe events flowing "over the TCP channel" to "the predecessor renderer", including citations to line numbers that no longer exist (ipc_server.py was once ~1900 lines, now 862). The Electron-removal cutover (2026-09-17) updated the code but not the core-path documentation.
**User Impact:** None directly; the cost lands on maintainers and future agents who read the dispatch path's own docs and are told the live architecture is TCP+predecessor — the exact stale-claim class the project's rules warn about, risking wrong "fixes" against a retired transport.
**Root Cause:** Verified — the 2026-09-17 cutover did not sweep core-path docstrings.
**Gain vs Trade-off:** Pure documentation fix; no behavior change, no rule conflict.
**If We Do It:** Documentation matches reality: "event_bus → sidecar_ws → Tauri host"; rotted line citations removed.
**If We Don't:** Every future session re-learns the architecture wrong; stale line citations rot further.
**My Recommendation:** ✅ Implement — cheap, prevents real misdirection (pairs with FV-5).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/event_bus.py:40`
- `voice_typer/server/app_lifecycle.py:13,143,175,178,213,217`
- `voice_typer/server/ipc_server.py:2-8`
**Fix:** Documentation-only sweep over the backend core scope replacing "TCP channel"/"predecessor" with "event_bus → sidecar_ws → Tauri host" on CURRENT-behavior sentences (keep historical "previously" notes); remove rotted line citations.
**Simplified Fix:** The internal instruction pages still say messages travel by an old route that no longer exists. Updating them to describe the current route stops future work from being planned against the wrong map.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-7 — E13 violation: `# type: ignore[no-untyped-def]` hides a missing return annotation (siblings show the correct pattern)
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** One function in the settings editor carries a type-checker suppression even though its parameter is typed and only the return annotation is missing. The two sibling functions directly below it have the identical shape (typed param, untyped return) and carry no suppression. The suppression both violates the project's no-suppressed-errors rule and is trivially fixable.
**User Impact:** None today; the suppression masks future genuine type errors on that line and sets a bad precedent (the checker was silenced instead of satisfied).
**Root Cause:** Verified — annotation omitted and silenced rather than added.
**Gain vs Trade-off:** Pure fix.
**If We Do It:** The rule is honored and the function signature documents its return type.
**If We Don't:** One more sanctioned suppression in the codebase; future errors on that line are invisible.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/config_editor.py:470` (primary)
- `voice_typer/server/ipc/rate_limiter.py:522`, `sidecar_ws_internals/graceful_shutdown.py:246` (lesser: attr-defined on dynamic injection — declaring attributes on the class removes both)
**Fix:** Add the return annotation (`-> None` or the actual type from `platform_launch`) and delete the suppression. For the two attr-defined sites, declare the dynamically-injected attributes on the class instead. The `os.startfile` platform-gated sites (status_handlers.py:274, config_editor.py:229) are documented mypy false positives (mypy has no per-platform conditional ignore) — record them as false positives per E13 rather than suppressing.
**Simplified Fix:** A note telling the type checker to "shut up" on one function is removed by simply writing the missing type on that function, the way its neighbors already do.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-8 — 480 dead i18n keys: `hotkey.keys.*`, `hotkey.combos.*`, `hotkey.presets.*` duplicated by the live `hotkeyKeys.*` family
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** Three whole key families (60 keys) exist in all 8 locale files but nothing in the code reads them — the live keycap layer consumes the separate `hotkeyKeys.*` family which carries the same labels. Review Wave 2 found five more dead keys under `hotkey.errors.*` (empty, invalid, noKeys, singleKeyOnly, comboMustEndNonModifier — the other two, fnMacOnly/fnMacOnlyShort, are live in hotkey-capture-state.ts) — fold them into the same cleanup. This is dead duplication left behind after the keycap layer moved.
**User Impact:** None at runtime; translation-file bloat (480 dead strings) and a real drift trap: a translator updating `hotkey.keys.capsLock` sees no effect and files a bug, or the dead values silently diverge from the live ones.
**Root Cause:** Verified — legacy key families left behind after the keycap layer moved to `hotkeyKeys.*` (grep shows zero consumers of the dead families).
**Gain vs Trade-off:** Pure cleanup; the compile-time TranslationKey union plus parity tests catch any accidental live reference during removal.
**If We Do It:** Locale files shrink by 60 keys each; single source of truth for keycap labels.
**If We Don't:** Drift trap persists; every future translator wastes effort on dead keys.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/translations/en.json` (+ ar, de, es, fr, hi, ru, zh)
**Fix:** Delete the three dead namespaces (`hotkey.keys.*`, `hotkey.combos.*`, `hotkey.presets.*`) plus the five dead `hotkey.errors.*` keys (empty, invalid, noKeys, singleKeyOnly, comboMustEndNonModifier) from all 8 locale files in one change; the compile-time key union and existing parity tests verify nothing live referenced them.
**Simplified Fix:** Sixty translation labels exist twice — one live copy, one dead copy. Deleting the dead copy removes confusion for translators with no user-visible change.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-9 — Four i18n keys ship untranslated English inside non-English locale files (C-I18N-2)
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** Four keys were added to non-English locale files by pasting the English value instead of translating it: `onboarding.backendLocalLabel` ("Local model") is English in de/es/fr/hi/ru/zh; `templates.variablesTooltip` in es/ru/zh; `models.progress.eta` in fr/ru/zh; `about.cloudTitle` in de. This is exactly the failure mode the project's localization rule names as the #1 silent downgrade.
**User Impact:** Non-English users hit occasional English strings in core surfaces — "Local model" in onboarding is the most visible (a first-run screen).
**Root Cause:** Suspected — keys added at different times; the "add to all 8 files" step was satisfied by pasting English.
**Gain vs Trade-off:** Pure fix; one-line value edits, no key-set change.
**If We Do It:** German/French/Spanish/Russian/Chinese users see fully translated onboarding, templates tooltip, download ETA, and About heading.
**If We Don't:** The app reads as unfinished in those languages at exactly the moments that build trust (onboarding).
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/translations/{de,es,fr,hi,ru,zh}.json`
**Fix:** Translate the 4 keys in the affected locales (genuine translations per C-I18N-2, e.g. de "Lokales Modell", ru "Локальная модель", zh "本地模型" for onboarding.backendLocalLabel).
**Simplified Fix:** A handful of labels were left in English inside the translated files. Translating those few labels finishes the job.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-10 — Tray "microphone changed" notification hardcodes English "System Default" inside localized text
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** When the microphone changes to the system default, the tray notification interpolates the hardcoded English label "System Default" into an otherwise localized sentence. In Arabic or Russian the message reads half English.
**User Impact:** Mixed-language notifications ("تم تغيير الميكروفون إلى System Default") read as unfinished to non-English users.
**Root Cause:** Verified — the concept is inlined instead of resolved through an i18n key (device names themselves are legitimately unlocalized).
**Gain vs Trade-off:** Pure fix; one new key added to all 8 locales per C-I18N-1.
**If We Do It:** The notification is fully localized in every language.
**If We Don't:** Minor but persistent polish gap in every non-English session.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/settings_controller.py:155`
**Fix:** Add a `settings.systemDefaultDevice` style key to the SERVER i18n layer (`voice_typer/server/i18n.py` registry + its locale registrations — the tray notification is server-side, so a renderer translations key would never be read; per Review Wave 2) and pass its translation as the label when `mic_name` is None.
**Simplified Fix:** The "you are now on the default microphone" message contains an English phrase inside translated sentences. Adding the phrase to the translation system fixes every language at once.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-11 — CI-errors.md can record a vacuous green "No test failures ✅" when tests never ran
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** The script that writes the committed CI status file treats "zero JUnit files found" the same as "all tests passed": with no arguments it emits the green banner. The workflow's download step is continue-on-error, so a run whose test job crashed before pytest (or whose artifact download failed) gets recorded as green with "(0 JUnit file(s) checked)".
**User Impact:** The file's whole purpose — surfacing the red state without CLI access — silently degrades: a broken run can be recorded as green, and the current file on disk is exactly that state.
**Root Cause:** Verified mechanism (code path exact); the triggering run's cause suspected (no JUnit artifacts in the latest run).
**Gain vs Trade-off:** Pure fix in the Python script; no workflow-structure change (no C-CI conflict).
**If We Do It:** "Tests did not run / no data" is loudly distinct from "all green"; the file becomes trustworthy again.
**If We Don't:** A future red run can be recorded as green; users relying on the file are misled.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `scripts/ci/write_ci_errors.py:246`
- `.github/workflows/build.yml:619-629`
- `CI-errors.md:5`
**Fix:** In write_ci_errors.py, when `files_checked == 0` emit a distinct "NO TEST DATA — tests did not run or report" banner instead of "No test failures ✅".
**Simplified Fix:** The CI status page says "all tests passed" even when it has no test results at all. Making "no data" look different from "all passed" makes the page honest.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-12 — Two modules with no dedicated behavioral tests (message_loop_strategy, app_admin)
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** Review Wave 2 corrected the original Wave-1 claim: of eight candidate modules, six DO have behavioral tests reachable via documented re-export shims or direct imports — the Windows DACL builder is tested via the `_security_attributes.py` shim (`tests/test__security_attributes.py`), the hotkey spec parser has a dedicated suite (`TestParseHotkeySpec` in `tests/test_native_hotkeys.py`), and the config service, atexit safety, lifecycle signals, and dictation delegates are all covered. The genuinely untested modules are: `hotkeys/windows/message_loop_strategy.py` (108 lines) and `app_admin.py` (235 lines, with a pyrefly-baseline flag already noting a missing-attribute issue).
**User Impact:** None immediate; regression risk on the Windows message-loop strategy (hotkey delivery) and admin-elevation paths.
**Root Cause:** Verified — zero-hit searches on exported symbols for exactly these two modules; the other six were false negatives in the original filename-based Wave-1 search (shim-re-exported tests were missed).
**Gain vs Trade-off:** Pure gain — two focused test files; no risk.
**If We Do It:** Hotkey message-loop and admin-elevation behavior is pinned by tests.
**If We Don't:** The next refactor of either ships on faith.
**My Recommendation:** ✅ Implement (scope narrowed and downgraded after review).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/hotkeys/windows/message_loop_strategy.py`
- `voice_typer/server/app_admin.py`
**Fix:** Add focused test files for the two modules (mock externals per E6, tests in tests/ per C-TEST-5). When auditing neighbors, search by exported symbols — not filenames — to avoid the shim-reexport blind spot that produced the original over-claim.
**Simplified Fix:** A first pass thought eight parts of the app lacked tests; a review found six of them are tested through re-export routes. Two genuinely untested parts remain — writing small test files for them closes the gap.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low (downgraded from Medium by Review Wave 2 — six of eight modules are tested)

### FV-13 — Flaky wall-clock budget assertions in performance tests (tightest: 100ms absolute)
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** Thirty-eight wall-clock upper-bound assertions exist across the suite (range 0.1s to 7.0s; the tightest is 100 ms at `tests/test_model_manager_background_change.py:106`, per Review Wave 2). On loaded CI runners (the repo's own workflow files document weeks of cancelled 29.8-minute runs), these budgets blow without any code regression. The suite has the right pattern available (poll-based wait_for fixture) but the perf-regression tests don't use it.
**User Impact:** Intermittent red CI legs, wasted re-runs, and the "works locally, flakes on runner" class of distrust.
**Root Cause:** Verified pattern presence; flake frequency suspected (no run history in the sandbox).
**Gain vs Trade-off:** Pure fix — budgets become ratios of the in-test simulated slow duration, which scales with machine speed automatically.
**If We Do It:** Performance-regression tests keep their sensitivity but stop flaking on slow runners.
**If We Don't:** CI noise continues to mask real failures.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `tests/test_recording_discard.py:160` (elapsed < 0.300)
- `tests/test_pack_checksum_background.py:79,92`
- `tests/test_single_instance.py:474`
- `tests/test_hotkey_dispatcher.py:563`, `tests/test_dictation_pipeline_abort.py:652`, `tests/test_recording_controller_watchdog_join.py:150`
**Fix:** Convert absolute budgets to ratios of the in-test simulated slow duration where one exists (e.g. `elapsed < 0.25 * SLOW_TASK_S`); for "returns immediately" budgets (no simulated slow duration — the ratio remedy does not apply, per Review Wave 2), add machine-load slack or restructure the assertion.
**Simplified Fix:** Some speed-check tests set fixed time limits so strict that a busy machine fails them even when the code is fine. Scaling the limits to the simulated workload keeps the checks meaningful without the false alarms.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-14 — npm audit runs as a soft gate (continue-on-error) despite documented overrides being verified
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** The client CI's production-dependency audit runs with continue-on-error and an in-file TODO to switch to hard-fail "once the override list is verified in production CI". The package.json overrides now each carry advisory IDs and the notes claim npm audit = 0 vulnerabilities (2026-08-04) — the stated precondition appears met, but the gate was never flipped.
**User Impact:** A new HIGH/CRITICAL advisory in production dependencies would not fail client CI; discovery depends on someone reading a warn-only log.
**Root Cause:** Verified — deliberate temporary soft gate; TODO not actioned.
**Gain vs Trade-off:** Pure hardening; small risk of a CI red leg the first time a new advisory lands — which is the point.
**If We Do It:** Production-dependency vulnerabilities fail CI visibly.
**If We Don't:** Supply-chain risk sits behind an unread warning.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `.github/workflows/client-ci.yml:69-71`
- `voice_typer/client/package.json:56-77` (the documented overrides)
**Fix:** Flip `npm audit --audit-level=high --omit=dev` to hard-fail (remove continue-on-error), keeping the documented overrides as the accepted-findings list.
**Simplified Fix:** The dependency security scan currently only warns instead of failing the build. Turning it into a real gate means a vulnerable dependency can't slip through unnoticed.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-15 — review.md MO-66 status is stale: the code already implements the adjudicated carve-out
**Status:** ⚠️ Partial (review.md hygiene, no code change needed)
**Description:** MO-66 is on HOLD ("do not implement until the user adjudicates"), but the current code already implements exactly its proposed fix: `if v is None or v == "": continue`, with a docstring citing a "User-adjudicated product decision (2026-09-16)". Either the adjudication happened and review.md was never updated, or the code drifted past a HOLD.
**User Impact:** None directly; wave agents reading review.md will treat the empty-string redaction as unresolved and may re-litigate or "fix" already-adjudicated behavior.
**Root Cause:** Verified code state vs stale review.md status; which side is wrong needs the user's memory of the 2026-09-16 decision.
**Gain vs Trade-off:** Pure bookkeeping — either update MO-66's status to Fixed (with the adjudication date) or, if the adjudication never happened, treat the code as a HOLD violation to revert. No middle ground.
**If We Do It:** review.md reflects reality; future sessions stop re-litigating.
**If We Don't:** Every fix-mode session re-encounters the HOLD and may act wrongly in either direction.
**My Recommendation:** 🟡 Defer to the user — one question: did you adjudicate MO-66 on 2026-09-16? If yes, mark it Fixed; if no, the code must be rolled back to the strict redaction (which is a code change requiring a fix session).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/history_bounds.py:275-285`
- `review.md` (MO-66 entry)
**Fix:** Reconcile MO-66's status with HEAD (user decision required: status-update vs code revert). This session did NOT change MO-66 or the code — investigation only.
**Simplified Fix:** The to-do list says a decision is waiting on an old change, but the change is already made in the code. The owner just needs to confirm which one is right.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low (downgraded from Medium by Review Wave 2 — bookkeeping item, no direct user impact)

## FV Low Priority

### FV-16 — Dead 2.27 MB torch-format VAD asset (`silero_vad.jit`) ships in every installer and extracts per version
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** The project completed its torch-free migration (HEAD commit is literally "torch-free Phase 1c"), but the old torch-format Silero VAD model file is still in the tree and still packaged (package-data includes every data file). Only one reference remains — a stale log-message string. The onnx-format model the app actually uses sits next to it. Extraction happens into the per-version onefile cache dir (per Review Wave 2's softening — not literally every launch).
**User Impact:** Every installer and every one-file extraction carries 2.27 MB of dead weight; a "VAD unavailable" diagnostic message names the wrong file, misleading anyone debugging audio issues.
**Root Cause:** Verified — dead asset left behind by the torch→ONNX migration.
**Gain vs Trade-off:** Pure removal (2.27 MB × installer + payload + tempdir). One caveat: the CI rule that lists required package data still names this file in its rationale — the flag itself stays, only the rationale text is stale (user-owned edit per AGENTS.md — agents must not edit AGENTS.md).
**If We Do It:** Installers shrink; the misleading diagnostic names the real dependency.
**If We Don't:** Dead payload and a misleading error message persist.
**My Recommendation:** ✅ Implement (file deletion + error-string fix; recommend the user updates the AGENTS.md rationale list).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/silero_vad.jit` (2,272,526 bytes — delete; record in archive/deleted_files.txt per E15)
- `voice_typer/server/vad_processor.py:269` (stale reference in log string)
**Fix:** Delete `silero_vad.jit` ONLY together with its packaging pins in the same change (per Review Wave 2): `tests/tauri/test_config_script_drift.py`'s `IMPORT_TIME_DATA_FILES` lists the .jit and asserts `is_file()` (test would go red otherwise), and MANIFEST.in carries an explicit `include voice_typer/server/silero_vad.jit` entry whose comment warns against deletion without a packaging decision and cites `export_silero_vad_onnx.py` — a script that no longer exists in tree or git history (itself worth cleaning from the comment). Correct the stale error string at vad_processor.py:269 to name onnxruntime/silero_vad.onnx; recommend (user-owned) updating the AGENTS.md C-CI-8/C-CI-9 rationale lists (both still name torch-era requirements). Record the deletion in archive/deleted_files.txt per E15.
**Simplified Fix:** An old copy of the voice-activity model, from before a technology switch, is still being shipped in every download. Deleting it and fixing one outdated error message saves space and confusion.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-17 — Audio filter rebuild signature is a hand-maintained parallel list with no test guard
**Status:** ✅ Fixed (2026-09-20 FV fix session; focused suites green ON WINDOWS sandbox)
**Description:** A 26-field config-signature tuple decides when the live audio filter chain rebuilds. It is currently in sync with the fields the chain builder actually reads, but the sync is maintained by hand with only a prose comment; no test checks it. A future config field added to the builder but not the tuple makes live config changes silently ignore the new setting until a restart.
**User Impact:** None today; the failure mode is a future silent setting-ignore (e.g. a new noise-filter slider that "doesn't do anything until restart") with no test to catch it.
**Root Cause:** Verified — hand-maintained parallel list with no guard of the tuple↔build_chain sync. (Review Wave 2 correction: two test files DO import `_CONFIG_SIGNATURE_FIELDS` — `tests/test_noise_gate_adaptive_config.py:43` and `tests/test_microphone_test_filters_contract.py:216` — but they pin other contracts, not the sync.)
**Gain vs Trade-off:** Pure guard — either a shared exported constant or a source-inspection test (a pattern the repo already uses elsewhere).
**If We Do It:** Adding a new audio setting without registering it fails a test loudly.
**If We Don't:** The next audio-settings feature can ship the silent-ignore bug class undetected.
**My Recommendation:** ✅ Implement — the drift consequence is High even though today's severity is Low.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/audio_processor.py:156-197` (`_CONFIG_SIGNATURE_FIELDS`)
- `voice_typer/server/audio_chain_builder.py:59-137` (`build_chain` field reads)
**Fix:** Either export the field-list constant from `audio_chain_builder` and have both sides consume it, or add a source-inspection unit test that scans `build_chain`'s source for `config.<attr>` reads and asserts each appears in `_CONFIG_SIGNATURE_FIELDS` (pattern proven by `tests/test_recording_and_audio.py::test_callback_does_not_do_heavy_processing`).
**Simplified Fix:** A list that mirrors which sound settings matter is kept in sync by memory alone. Adding an automatic cross-check means a new setting can't be forgotten silently.
**Implementation Difficulty:** 🟡 Medium
**Severity:** 🟢 Low (High consequence when it drifts)

### FV-18 — onnxruntime sessions use default thread pools for tiny per-chunk models
**Status:** ❌ Not Fixed
**Description:** The VAD and noise-suppression models are tiny (per-chunk work is sub-2ms) but their inference sessions are created without thread-pool options, so each session's default pool spans all cores. For work this small, the fan-out and spin-wait synchronization between 16-32ms audio chunks may cost more than it saves, and keeps cores waking (battery).
**User Impact:** Suspected — slightly higher per-run latency and background CPU during dictation and whenever the level monitor runs. Needs on-host A/B measurement.
**Root Cause:** Suspected (code fact verified; overhead not measured in sandbox — onnxruntime not installable here).
**Gain vs Trade-off:** One-line session options change; behavior identical, CPU pinned to single-thread per session. Trade-off: none if A/B confirms; revert if it regresses.
**If We Do It:** Lower idle wakeups and possibly lower per-chunk latency on battery-powered machines.
**If We Don't:** Potential background CPU cost persists unnoticed.
**My Recommendation:** 🟡 Try and revert — measure first on a dev machine.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/vad.py:209-212`
- `voice_typer/server/audio_filters/gtcrn_backend.py:156-159`
**Fix:** Pass `ort.SessionOptions()` with `intra_op_num_threads=1` and `inter_op_num_threads=1` to both sessions; A/B measure session.run wall time and process CPU before/after.
**Simplified Fix:** The two small sound-analysis models currently spin up all processor cores for tiny bursts of work. Telling them to use one thread may cut background CPU and battery drain — measure to confirm.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-19 — Silero VAD v5 is available (current bundle is v4)
**Status:** ❌ Not Fixed
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

### FV-20 — FTS5 special-command string interpolation has no entry allowlist
**Status:** ❌ Not Fixed
**Description:** Two database helpers build special commands (FTS5 rebuild/optimize, WAL checkpoint mode) via string interpolation because these statements cannot use parameter binding (documented-true for PRAGMA; per Review Wave 2, the FTS5 VALUES slot's bindability is unverified — the allowlist fix is correct regardless). All current callers pass internal literals, so nothing is injectable today, but the function signatures accept arbitrary strings — one future caller forwarding user-influenced text becomes SQL injection into the writer connection.
**User Impact:** None today (all call sites verified internal); defense-in-depth gap.
**Root Cause:** Verified code fact; future exploitability suspected-only.
**Gain vs Trade-off:** Pure hardening (whitelist at function entry); zero cost for current callers.
**If We Do It:** The dynamic-SQL surface cannot be misused by a future caller.
**If We Don't:** The safety of the surface depends on every future caller remembering.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/history_db_internals/retention.py:164-166`
- `voice_typer/server/history_db_internals/crud_writes.py:540`
**Fix:** `if command not in {"rebuild", "optimize"}: raise ValueError(...)` at function entry (whitelist dynamic SQL fragments); optionally the same for the PRAGMA mode.
**Simplified Fix:** Two low-level database commands accept any text where only two fixed words are ever valid. Checking the word against the fixed set at the door removes the future misuse risk.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-21 — Clipboard password-field safety fails open when UIA is unavailable (documented tradeoff)
**Status:** ❌ Not Fixed
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

### FV-22 — Stale predecessor-main references in Rust comments (point at deleted files as canonical)
**Status:** ❌ Not Fixed
**Description:** The Rust host's comments still instruct "keep in sync with the TS allowlist" and cite `voice_typer/client/src/main/...` paths — files deleted in the 2026-09-17 Electron removal. 27 references across 15 files (Review Wave 2 recount; the Wave-1 list missed 7 single-reference files). A future agent following allowlist.rs's inline instructions could try to find or re-create the deleted TS allowlist (an explicit AGENTS.md prohibition) or audit the wrong branding file.
**User Impact:** None directly; misdirection risk for future maintainers/agents on the IPC parity surface.
**Root Cause:** Verified — predecessor-cutover comment sweep never ran over src-tauri docs.
**Gain vs Trade-off:** Pure comment sweep.
**If We Do It:** Comments point at the real two-layer parity (Python _COMMAND_REGISTRY ↔ Rust allowed_commands()).
**If We Don't:** The most-protected surface in the repo carries instructions that are impossible to follow correctly.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds/allowlist.rs:56-103`
- `src-tauri/src/branding.rs:17`, `src-tauri/src/sidecar/supervisor.rs`, `lifecycle.rs`, `bubble/math.rs` (4 refs), `bubble/commands.rs` (4 refs), `system_cmds/dialogs.rs` (2 refs), `platform/power.rs` + 7 more single-reference files
**Fix:** Comment sweep: replace predecessor `client/src/main/...` references with current paths (renderer types/ipc/, branding.ts, tests); rewrite allowlist.rs's sync contract to the actual two-layer parity delta (host-dispatched 4 commands).
**Simplified Fix:** Notes inside the Windows host code still point to files that were deleted. Updating them to the current files stops future work from chasing ghosts.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low (Medium for the allowlist.rs instance specifically)

### FV-24 — Home page duplicates cached-stats loading on cold mount (4 reads where 2 suffice)
**Status:** ❌ Not Fixed
**Description:** The Home page reads its cached stats twice on mount: the two state initializers each call the loader, then the loading-flag initializer calls both loaders again — four storage reads and parses per cold mount where two suffice.
**User Impact:** Negligible latency; pure waste and a drift risk if the loaders ever gain side effects.
**Root Cause:** Verified — initializer duplication when the loading flag was derived.
**Gain vs Trade-off:** Pure cleanup, no behavior change.
**If We Do It:** Halved cold-mount cache reads.
**If We Don't:** Nothing user-visible; minor waste persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Home.tsx:113-124`
**Fix:** Compute the two cached values exactly once per mount (e.g. a `useRef` holder populated inside the first initializer, or memoized module-scope loaders) and reuse them in all three initializers — plain locals before the `useState` calls would re-run the loaders on every render, which is worse in the empty-cache case (per Review Wave 2).
**Simplified Fix:** The home screen reads its saved statistics twice in a row when opening. Reading once and reusing the values is a small free speedup.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-25 — Sidebar gap comment contradicts the rendered value (gap-5 narrated, gap-4 rendered)
**Status:** ❌ Not Fixed
**Description:** A comment in the sidebar explains the expanded group gap as the wider value while the code renders the narrower one; the project's rules document the wider value too. Either the comment or the code is stale relative to the two-group consolidation.
**User Impact:** None directly; a future agent reading the comment may "restore" the narrated value and claim contract compliance, producing silent visual drift.
**Root Cause:** Suspected — rhythm value changed during the two-group consolidation without updating the comment.
**Gain vs Trade-off:** One-line correction; needs a 30-second product check of which value is intended.
**If We Do It:** Comment and code agree; the contract is unambiguous.
**If We Don't:** The trap stays armed for the next reader.
**My Recommendation:** 🟡 Defer to user — one product question (which gap is intended?) decides a one-line fix in either direction.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx:336-343`
**Fix:** Gate on the user/product decision FIRST (per Review Wave 2): two documented sources (AGENTS.md C-SIDEBAR-6 and the in-file comment) say gap-5 expanded while the code renders gap-4 — the likelier story is the CODE regressed from the deliberate rhythm during the two-group consolidation, not comment drift. If gap-5 is confirmed intended, fix Sidebar.tsx:343 to gap-5; only if the user confirms gap-4, align the comment. Either way C-SIDEBAR-6's actual prohibition (flattening both states to one gap) stays untouched.
**Simplified Fix:** A note in the sidebar code describes spacing that doesn't match the code. Deciding which is right and fixing the other removes the contradiction.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-26 — Settings save-failure banner announces politely while Home errors announce assertively
**Status:** ❌ Not Fixed
**Description:** The Settings page announces a failed save with the polite live-region role, while Home's error line uses the assertive alert role. A failed settings write is data-loss-risk and should match the app's own error convention.
**User Impact:** Screen-reader users learn of a failed settings save later than the convention promises (queued behind other polite announcements).
**Root Cause:** Verified — inconsistent failure-announcement semantics across pages.
**Gain vs Trade-off:** One attribute change; no visual change.
**If We Do It:** Save failures are announced with the same urgency as other errors.
**If We Don't:** Inconsistent announcement semantics persist for AT users.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Settings.tsx:401-409`
- `voice_typer/client/src/renderer/src/pages/Home.tsx:595-597` (the reference convention)
**Fix:** Switch the save-error banner to `role="alert"` AND remove the explicit `aria-live="polite"` attribute — an explicit polite value would override role="alert"'s implicit assertive semantics and make the change a no-op (per Review Wave 2). Update the pinning test `pages/__tests__/Settings.search-and-save-error.test.tsx:203-204` (it asserts `aria-live="polite"` + `role="status"` exactly and would go red otherwise). No i18n key changes.
**Simplified Fix:** When saving settings fails, screen readers hear the news in a quiet voice instead of the urgent one other errors use. Making it urgent matches the app's own convention.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-27 — Two RTL-unaware physical margins (`ml-auto`, `ml-2`) anchor to the wrong side in Arabic
**Status:** ❌ Not Fixed
**Description:** The app's standard is logical properties (`ms-/me-`; 26 logical vs 2 real physical usages — 3 more rg matches are comments only, per Review Wave 2), but two spots still use physical left-margins: the accordion trigger icon and the spinner gap. In right-to-left locales these anchor to the wrong side.
**User Impact:** Minor visual misplacement on the Models/Microphone pages (Arabic users).
**Root Cause:** Verified (class analysis; visual effect inferred).
**Gain vs Trade-off:** Two class swaps; no risk.
**If We Do It:** RTL layouts anchor correctly everywhere.
**If We Don't:** Minor misplacement persists for Arabic users.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/components/ui/accordion.tsx:46` (`ml-auto`)
- `voice_typer/client/src/renderer/src/components/feedback/Spinner.tsx:121` (`ml-2`)
**Fix:** Swap to `ms-auto` / `ms-2`; ALSO remove the now-stale `CURRENTLY_VIOLATING` allowlist entries in `rtl-physical-css-guard.test.ts` for the fixed spots (the guard's regex `ml-\d+` is blind to `ml-auto`, which is why accordion.tsx evaded it — worth widening the guard while touching it; per Review Wave 2). The ActiveMicrophoneCard comment at line 206 mentions `ml-auto` but the code already uses `ms-auto` — the comment is stale, tidy it opportunistically.
**Simplified Fix:** Two spacing classes position elements from the left edge only; in right-to-left languages they should position from the start edge. Swapping to start-aware classes fixes Arabic.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-28 — `tChoice()` plural resolution skips the primary-subtag fallback step that `t()` has
**Status:** ❌ Not Fixed
**Description:** The two translation lookups walk different fallback chains: the main one tries current locale → primary subtag → English → raw key; the plural one skips the subtag step. No impact today (all 8 locales are primary), but a future regional locale gets inconsistent plural resolution.
**User Impact:** None today.
**Root Cause:** Verified code asymmetry.
**Gain vs Trade-off:** Pure symmetry fix + a comment pinning the intended chain.
**If We Do It:** Future regional locales resolve plurals consistently.
**If We Don't:** Latent asymmetry persists.
**My Recommendation:** ✅ Implement (next time the i18n core is touched).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/translate.ts:335-360` (`tChoice`) vs `:194-228` (`t`)
**Fix:** Mirror the subtag step in `tChoice()` or drop it from `t()`; add a comment pinning the intended chain.
**Simplified Fix:** The singular and plural message lookups follow slightly different search orders. Making them identical keeps future language variants consistent.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-29 — Tracked repo junk: 0-byte pytest_collect.err, placeholder root bin/, stale .exe.old backup
**Status:** ❌ Not Fixed
**Description:** Three stray files are tracked in git: an empty error-log file at the root, a placeholder text file posing as the Linux sidecar binary at root `bin/` (the real stubs live under src-tauri/), and a 279 KB `.exe.old` backup next to the live Windows key-listener binary (unmanifested — the integrity manifest has no entry for it).
**User Impact:** Repo bloat and contributor confusion (a root "binary" that nothing consumes; an old exe that looks shippable but is unverified).
**Root Cause:** Verified — leftovers from an older stub location and a binary-update backup committed.
**Gain vs Trade-off:** Pure deletion (verify nothing references the root bin/ path first).
**If We Do It:** Repo shrinks ~280 KB and stops misleading.
**If We Don't:** Confusion persists for anyone auditing the binary integrity story.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `pytest_collect.err` (0 bytes, root)
- `bin/python-sidecar-x86_64-unknown-linux-gnu` (placeholder, root)
- `voice_typer/server/native/windows-key-listener.exe.old` (279,419 bytes)
**Fix:** `git rm` all three after verifying nothing references the root `bin/` path (tauri externalBin resolves against src-tauri/); record in archive/deleted_files.txt per E15.
**Simplified Fix:** Three leftover files — an empty log, a fake placeholder binary, and an old backup copy of a Windows helper — are cluttering the repository. Removing them keeps the shipped-files story clean.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-30 — AGENTS.md pipeline snippet cites coverage floor 65 while every real gate enforces 78/80
**Status:** ⚠️ Partial (documentation accuracy; AGENTS.md is user-owned)
**Description:** The AGENTS.md validation-pipeline snippet and the root conftest docstring still cite `--cov-fail-under=65`, but the actual floor is 78 everywhere it is enforced (pyproject, CI workflow, Makefile — guarded by a consistency test), with the ratchet at 80. Contributors following the AGENTS.md command literally run a weaker gate locally than CI.
**User Impact:** Locally-green states that are CI-red; confusion about the real floor.
**Root Cause:** Verified — floor raised 65→78 (commit 3555c269) without updating the AGENTS.md snippet or conftest docstring.
**Gain vs Trade-off:** Documentation accuracy; requires an AGENTS.md edit, which agents must NOT make (user-owned file) — flagged for user sign-off.
**If We Do It:** Documentation matches the enforced gate.
**If We Don't:** The mismatch keeps producing false local greens.
**My Recommendation:** 🟡 Defer to user — one-line AGENTS.md + conftest docstring edit for the owner to approve/apply.
**Progress:** `None yet.`
**Related Files:**
- `AGENTS.md:598` (user-owned — recommend, do not edit)
- `conftest.py` (root docstring)
- `pyproject.toml:890`, `.github/workflows/build.yml:450`, `Makefile:62` (the real sites)
**Fix:** Update the two stale mentions to 78 (AGENTS.md edit requires user sign-off; conftest docstring is a normal repo edit).
**Simplified Fix:** The project's instruction file quotes an old code-coverage minimum (65%) while the real gates enforce 78-80%. The owner should update the quoted number so local checks match CI.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-31 — tests/ root holds 716 flat test files (243 already live in 26 domain subdirs)
**Status:** ❌ Not Fixed
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

### FV-32 — Model-load "warm/cold" label uses a 5-second wall-time guess
**Status:** ❌ Not Fixed
**Description:** The log line that labels a model load "warm (page-cache)" vs "cold (disk)" decides by a hardcoded 5-second threshold. Slow machines mislabel warm loads as cold; fast NVMe mislabels cold loads as warm — and the label feeds prewarm-effectiveness triage.
**User Impact:** None directly; misleading perf diagnostics.
**Root Cause:** Verified heuristic, no page-cache probe.
**Gain vs Trade-off:** Report the measured duration (already appended per the duration rule) and drop or de-emphasize the guess, or reuse the existing cache-ratio probe.
**If We Do It:** Prewarm triage reads facts, not guesses.
**If We Don't:** Perf triage can chase phantom cold loads.
**My Recommendation:** ✅ Implement (trivial: drop the label or reuse the existing ratio probe).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/transcription.py:543`
- `voice_typer/server/prewarm/cache_probe.py::_cache_ratio` (the reusable probe)
**Fix:** Report the measured duration and drop/de-emphasize the heuristic label, or compute page-cache hit ratio the way cache_probe.py already does (reuse, don't reimplement).
**Simplified Fix:** The startup log guesses whether a model came from disk or cache based on a fixed time cutoff. Either remove the guess or use the real cache-measuring code the project already has.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-33 — state.rs:175 doc comment fused onto the wrong field's line
**Status:** ❌ Not Fixed
**Description:** A field's documentation comment got merged onto the tail of the previous field's line during an insertion; the documented field itself now has no doc comment where it is declared.
**User Impact:** None; `cargo doc`/IDE hover shows no doc for the field.
**Root Cause:** Verified formatting accident.
**Gain vs Trade-off:** Pure formatting fix.
**If We Do It:** Docs render correctly.
**If We Don't:** Cosmetic gap persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/state.rs:175,186`
**Fix:** Move the doc comment onto its own line above `host_locale`.
**Simplified Fix:** A description meant for one piece of data got pasted onto the end of a different line. Moving it to its own line above the right field fixes the documentation.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-34 — dispatch.rs comments cite the old 256-cap channel while the code now uses 64
**Status:** ❌ Not Fixed
**Description:** Two comments in the dispatch command derive memory-math and backpressure expectations from a 256-slot channel capacity, but the writer channel was reduced to 64 with its own rationale comment. Readers size OOM math and backpressure reasoning 4x off.
**User Impact:** None; misleading docs on a hot path.
**Root Cause:** Verified — capacity reduction (256→64) not propagated to dispatch.rs comments.
**Gain vs Trade-off:** Pure comment fix (reference the constant by name instead of a literal).
**If We Do It:** Future capacity reasoning starts from the right number.
**If We Don't:** The next tuning pass can re-derive the wrong cap.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/commands/sidecar_cmds/dispatch.rs:188,367`
- `src-tauri/src/sidecar/ws.rs:112` (`WS_WRITER_CHANNEL_CAPACITY = 64`)
**Fix:** Update the two comments to reference `WS_WRITER_CHANNEL_CAPACITY` by name instead of a literal.
**Simplified Fix:** Notes about a queue's size still describe the old, four-times-larger size. Pointing them at the live setting keeps future memory reasoning correct.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-35 — Respawn-scheduler thread-bridge rationale claims `!Send` future; current code compiles on the runtime directly
**Status:** ❌ Not Fixed
**Description:** The dedicated std thread + block_on bridge is documented as required because the respawn future is `!Send` — but the current code shape compiles green on the async runtime (which requires `Send`), so the claim is stale. The bridge still provides queue serialization + bounded backpressure (legitimate value), but the stated reason is wrong.
**User Impact:** None at runtime; misleads future maintenance (someone "fixing" a compile error by re-adding a bridge, or refusing to simplify because they believe a constraint that no longer exists).
**Root Cause:** Suspected (static reasoning; cargo unavailable in sandbox to confirm) — rationale true for an older code shape.
**Gain vs Trade-off:** Comment correction is free; actual simplification would need cargo verification and is optional (the bridge keeps serialization value).
**If We Do It:** Docs state the real rationale (serialized queue + backpressure + historical workaround).
**If We Don't:** False constraint persists in the docs of a sanctioned pattern.
**My Recommendation:** ✅ Implement the comment fix; 🟡 Try-and-revert the structural simplification only with cargo available.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/sidecar/ws/respawn_scheduler.rs:103-108,213-218`
- `src-tauri/src/sidecar/spawn.rs:309-314`
**Fix:** Correct the comments to the real rationale, or verify with `cargo check` in a proper env and either keep the bridge for serialization value (documented as such) or simplify.
**Simplified Fix:** A note says a background job must run on its own thread for a technical reason that no longer applies. Either fix the note or, after checking, simplify the code — the thread still has honest value either way.
**Implementation Difficulty:** 🟢 Easy (comment) / 🟡 Medium (simplification)
**Severity:** 🟢 Low

### FV-36 — i18n locale.ts comment contains the literal app name
**Status:** ❌ Not Fixed
**Description:** A prose comment in the locale module says the locales ship with the app, using the literal brand name. The branding rule requires prose comments to avoid the literal brand (the check exempts comments, so it bypasses CI enforcement).
**User Impact:** None; cosmetic compliance gap for future renames.
**Root Cause:** Verified.
**Gain vs Trade-off:** One-word comment reword.
**If We Do It:** Rename propagation audit stays comment-clean.
**If We Don't:** Cosmetic gap persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/i18n/locale.ts:16`
**Fix:** Reword the comment to "the app" phrasing.
**Simplified Fix:** One code comment spells out the product name instead of saying "the app." Replacing it keeps a future rename from missing this spot.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-37 — Entry modules carry module-top test-seam re-exports (`# noqa: F401`) that attract new patch sites
**Status:** ❌ Not Fixed
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

### FV-38 — app.py is 436 lines vs the ~300-line wiring budget (re-export volume, not logic)
**Status:** ❌ Not Fixed
**Description:** The Python entry file is 136 lines over the soft budget. Content audit: it IS wiring-only (delegates, re-export blocks with long rationale comments, no business logic), and the split modules are each a single documented concern — the split itself is healthy. The overage is ~45% re-export blocks with pinned-name constraints (security tests pin specific names in THIS file).
**User Impact:** None.
**Root Cause:** Verified — E3's letter exceeded by comment/re-export volume; a fix relocating re-export blocks would conflict with documented test pins (the pinning tests constrain location, not verbosity).
**Gain vs Trade-off:** Trimming the re-export comment blocks to single lines (keeping the pinned names in-file) is safe; aggressive relocation would break pinned tests.
**If We Do It:** Entry file drifts back toward budget without breaking pins.
**If We Don't:** Letter-level deviation persists; intent is met.
**My Recommendation:** 🟡 Defer — compliant-in-spirit; only worth trimming comments opportunistically.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/app.py` (436 lines; pinned-name constraints at lines 378-395, 426-436)
- Cross-reference: FV-37 (patch-site migration for the same re-export blocks)
**Fix:** Accept as compliant-in-spirit, or trim the re-export comment blocks to single lines while keeping the pinned names in-file.
**Simplified Fix:** The app's main file is over its size guideline, but only because of explanatory notes, not tangled logic. Trimming the notes opportunistically is safe; a big reorganization would break tests that pin names to this file.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

---

# FV Wave 3 Additions — 2026-09-19 (deeper-pass investigation, 7 sub-agents incl. 1 re-dispatch)

> Findings from the deep reads of files Wave 1 could not fully cover, plus edge cases. All NOT FIXED (investigation mode). Ordered Critical → High → Medium → Low.

## FV Critical

### FV-39 — macOS/Linux release pipelines build a deleted prewarm module: any dispatch fails mid-job (plus dead prewarm scripts)
**Status:** ❌ Not Fixed
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

## FV High

### FV-40 — Clipboard restore re-registers builtin formats by name — user's original clipboard content is silently lost (Windows)
**Status:** ❌ Not Fixed
**Description:** The clipboard snapshot/restore mechanism stores a display name for every captured format and, on restore, calls the Windows `RegisterClipboardFormatW` API with that name to get the target format id. For BUILTIN formats (text, bitmap, file list) that is wrong: the API creates NEW custom format ids for names that are not real registered-format names, so after `EmptyClipboard()` the user's data is written back under bogus ids instead of the builtin ones. The ADR documenting this mechanism carries the same flaw, and the tests mock the API to return 1, so CI cannot catch it. Root cause code-verified; the runtime data-loss behavior needs a Windows host to confirm (marked suspected per platform-claims rule).
**User Impact:** After any dictation that uses clipboard restore (the "give the clipboard back" promise), the user's original clipboard content is effectively erased — text does not come back as text. This is the most common restore case, so the feature's core promise silently fails on Windows. (Runtime confirmation requires a Windows host — VALIDATE ON WINDOWS HOST.)
**Root Cause:** Verified (code path + documented Win32 semantics): re-register-by-name applied unconditionally instead of only to registered formats (ids ≥ 0xC000); the builtin/registered distinction was never captured in the snapshot tuples.
**Gain vs Trade-off:** Pure fix — keep `target_fmt = fmt` for builtin ids; no trade-off. A regression test asserting SetClipboardData receives format 13 for a CF_UNICODETEXT item pins it.
**If We Do It:** Clipboard restore actually restores: text stays text, and the borrow/restore trust contract holds end-to-end.
**If We Don't:** Every clipboard-restore on Windows destroys the user's prior clipboard content silently — a data-loss class bug wearing a working feature's clothes.
**My Recommendation:** ✅ Implement — high-impact, small fix, needs one Windows host run to confirm + a regression test.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/clipboard_snapshot.py:484-497` (restore), `:366-369`, `:169-171` (capture naming)
- `docs/adr/0012` §4.3 (same flaw documented)
- `tests/test_clipboard.py:820-909` (mock hides the bug)
**Fix:** Keep `target_fmt = fmt` for builtin format ids (< 0xC000); only re-register names for genuinely registered formats. Add a regression test asserting SetClipboardData receives fmt 13 for a CF_UNICODETEXT snapshot item (stop mocking RegisterClipboardFormatW to a constant for that case). VALIDATE ON WINDOWS HOST.
**Simplified Fix:** When the app gives the clipboard back after dictation, it asks Windows to look up each saved format by NAME — but the standard formats (like plain text) are identified by NUMBER, not name. Looking them up by name creates wrong placeholders, so the saved text never actually returns. Keeping the original numbers fixes it.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### FV-41 — Windows host-validation runbook pins a Nuitka version that crashes on the current lockfile (2.5.4 vs mandated 2.8.10)
**Status:** ❌ Not Fixed
**Description:** The runbook that gates Phase 0-W host validation (which unlocks push triggers per ADR-0020 §15) instructs installing `nuitka==2.5.4`, while the repo's own hard rule (C-CI-6/NU-105) and the Windows workflow mandate `nuitka==2.8.10` because Nuitka <2.8.0 crashes compiling numpy ≥2.5 (the lockfile has numpy 2.5.2). The workflow header even claims the runbook tracks the same version — false. The runbook §2 also instructs building the deleted prewarm module (FV-39).
**User Impact:** Anyone executing the host-validation procedure follows a toolchain recipe that cannot compile the current dependencies — wasted maintainer cycles on a known-crashing version, at the exact gate that matters most for release readiness.
**Root Cause:** Verified — the runbook was not updated through the NU-105 pin change (2026-08-06) or the pack-split prewarm removal.
**Gain vs Trade-off:** Pure documentation fix (docs are not C-CI-2-protected); zero risk.
**If We Do It:** The validation procedure compiles the current tree; the doc and the pipeline tell the same story.
**If We Don't:** The release gate procedure remains self-defeating.
**My Recommendation:** ✅ Implement — docs-only edit.
**Progress:** `None yet.`
**Related Files:**
- `docs/migration/windows-validation-runbook.md:48,252,429-465`
- `.github/workflows/tauri-windows-build.yml:13-26` (the mandated pin)
**Fix:** Bump the runbook's Nuitka pin to 2.8.10 (matching C-CI-6), mark §2's prewarm instructions historical (cross-reference FV-39).
**Simplified Fix:** The instruction sheet for validating the app on Windows tells you to install an old compiler version that is known to crash on this project's current dependencies. Updating the sheet to the version the project actually mandates makes the procedure work.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🔴 High

### FV-42 — auto-update feature doc points at deleted files and stale command counts
**Status:** ❌ Not Fixed
**Description:** The auto-update documentation cites `voice_typer/server/service/offline_pack.py::download_offline_pack_with_resume` (the module became a package; the function moved), references the deleted `client/src/main/allowed-commands.ts` allowlist, mis-locates the `offline_pack_consent` schema, and quotes lockstep counts that include the removed TypeScript set. The feature claims themselves verify TRUE (update check, pack download, publish scripts, and the online-status hook all exist).
**Root Cause:** Verified — post-cutover doc not re-synced after the src/main deletion and the service restructure.
**User Impact:** Contributors wiring the auto-update IPC surface chase deleted files; the doc actively misleads at each step it's wrong.
**Gain vs Trade-off:** Pure doc refresh.
**If We Do It:** The feature doc matches the current tree.
**If We Don't:** Dead-path churn continues.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `docs/auto-update-feature.md:~30,179`
**Fix:** Refresh the module paths (`service/offline_pack/download.py:31`, config `_schema.py`), remove the TS-allowlist references, update the lockstep counts to the two-allowlist reality.
**Simplified Fix:** The document describing the auto-update feature points to files that no longer exist and counts a list that was deleted. Updating the paths and numbers makes it a reliable guide again.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium (downgraded from High by Review Wave 4 — docs-only contributor misdirection, matching the FV-56 precedent)

### FV-43 — CONTRIBUTING's i18n section points into the deleted main-process tree
**Status:** ❌ Not Fixed
**Description:** CONTRIBUTING.md's i18n contributor flow references `voice_typer/client/src/main/i18n/locales/*.json` and `src/main/i18n.ts` — both deleted on 2026-09-17. The real locale files live in `client/src/renderer/src/i18n/translations/`. This is the exact section a new localization contributor starts from.
**Root Cause:** Verified — the doc was not cut over with the Electron removal.
**User Impact:** A localization contributor's first steps dead-end; the highest-friction path for exactly the contributors the section exists to serve.
**Gain vs Trade-off:** Pure doc fix.
**If We Do It:** The i18n contributor flow works as written.
**If We Don't:** New contributors burn their first session on a deleted tree.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `CONTRIBUTING.md:950-951`
**Fix:** Rewrite the i18n section against the renderer tree (`renderer/src/i18n/translations/{ar,de,en,es,fr,hi,ru,zh}.json`).
**Simplified Fix:** The contributor guide's translation instructions point into a folder that was deleted. Pointing them at the current translation folder makes the guide usable.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium (downgraded from High by Review Wave 4 — docs-only contributor misdirection, matching the FV-56 precedent)

## FV Medium Priority (Wave 3)

### FV-44 — ~70 unreachable lines of restart-loop machinery in the IPC entrypoint (potentially-unbound `app` in the dead tail)
**Status:** ❌ Not Fixed
**Description:** The entrypoint's `while True:` "in-place-restart loop" can never iterate: the WS branch exits at line 621 and the non-WS branch exits at 631, making everything after — a `ready` push, the tray loop, the restart check — unreachable (~70 lines). In early-bind mode `app` is never assigned in `main()`, so the dead tail also references a potentially-unbound name. Comments at 439-447 still narrate the removed standalone/terminal restart mode.
**User Impact:** None at runtime; the dead tail is a trap — future edits to transport dispatch could re-wire into the NameError path, and the misleading architecture narration costs every reader.
**Root Cause:** Verified — the TCP/standalone removal (50f4ad67) deleted the path but left the loop machinery.
**Gain vs Trade-off:** Pure deletion; both arms already exit, so no behavior change.
**If We Do It:** The entry module shrinks ~70 lines and tells the truth about control flow.
**If We Don't:** The trap stays armed.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/entrypoint.py:439-700` (esp. 622-700)
**Fix:** Delete the unreachable tail + the loop wrapper (keep the single-iteration body), record per E15; update the stale restart-mode comments.
**Simplified Fix:** Old leftover code that can never run still sits in the app's startup file, including a reference to a variable that may not exist. Removing the dead block makes the file honest and smaller.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-45 — `--allow-stdin` flag never actually enables the stdin listener (dead CLI surface + misleading log)
**Status:** ❌ Not Fixed
**Description:** The `--allow-stdin` CLI flag sets an env var and logs "stdin listener will be spawned if _tcp_mode is False" — but `main()` unconditionally sets `server._tcp_mode = True` before `server.start()`, and the stdin listener is only spawned when `_tcp_mode` is False. The flag can never take effect through `main()`. (The name `_tcp_mode` is itself a misnomer now — it means "don't spawn the stdin listener", TCP having been removed.)
**User Impact:** Dev-facing: a developer who passes the flag believes they enabled the listener; the help text and log promise behavior the entry point forecloses. Fail-closed, so no security impact.
**Root Cause:** Verified — the unconditional `_tcp_mode = True` hardening postdates the flag; never reconciled.
**Gain vs Trade-off:** Pure honesty fix; keep the env var for direct-API/tests.
**If We Do It:** The CLI surface stops promising what it can't do.
**If We Don't:** Trust erosion on the CLI surface continues.
**My Recommendation:** ✅ Implement — remove the flag from the CLI (keep the env var documented) OR route it through a path that doesn't force _tcp_mode; fix/delete the log line either way.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/entrypoint.py:298-303,504,554`
- `voice_typer/server/ipc/lifecycle.py:247`
**Fix:** Either remove `--allow-stdin` from the CLI (keep the env var for direct-API/test usage, documented) or make the flag route through a path that doesn't force `_tcp_mode = True`; delete the misleading log line. Consider renaming `_tcp_mode` to reflect its actual meaning (stdin-listener gate).
**Simplified Fix:** A developer command-line switch promises to turn on a direct text-channel mode, but a later safety change made that promise impossible. Either removing the switch or making it genuinely work stops it from misleading.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-46 — `get_transcription_text` returns unbounded payloads; >1 MiB rows are silently dropped by the WS frame cap
**Status:** ❌ Not Fixed
**Description:** The list handlers enforce a history frame cap (truncation + a structured `payload_too_large` error) precisely because the WS layer silently drops oversized frames — but the "show full text" handler returns the complete row text with no size guard. Dictation text length is unbounded upstream, so one extreme-length session (hours) makes the request die silently: the backend logs a successful dispatch, the renderer gets nothing.
**User Impact:** Rare but severe-per-occurrence: the History "show full text" affordance hangs forever with zero feedback for an extreme-length dictation.
**Root Cause:** Verified (mechanism — the frame cap and its drop behavior are documented in-repo); the renderer hang is suspected (promise never resolves).
**Gain vs Trade-off:** Apply the existing cap machinery to this handler (or chunk it like the microphone-test audio reader); a precise `payload_too_large` response lets the renderer degrade gracefully instead of hanging.
**If We Do It:** Even extreme rows give a defined response (truncated preview or explicit too-large error).
**If We Don't:** The silent-drop class persists on the one endpoint whose entire job is returning the full text.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/handlers/history_handlers.py:608-646`
- `voice_typer/server/sidecar_ws_internals/outbound.py:147-152` (the drop)
- `voice_typer/server/ipc/validation.py:292` (the existing cap machinery)
**Fix:** Run `_enforce_payload_size_cap` on the result in `_handle_get_transcription_text`, returning a structured `client.payload_too_large` error (or chunked reads à la `microphone_test_read_audio` / C-MIC-17) so the renderer can degrade gracefully.
**Simplified Fix:** One command — "show me the full text of this entry" — has no size limit, while the transport silently discards oversized messages. Giving it the same limit-and-report treatment as every other list command turns a silent hang into a clean message.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-47 — Three history list handlers repeat an identical ~45-line control flow
**Status:** ❌ Not Fixed
**Description:** The three list handlers (history, favorites, search) duplicate the same ~45-line pipeline: schema validation → limit/offset bounding → cursor extraction → service call with/without cursor → frame-cap enforcement → envelope assembly. Only the service method name and one extra field differ. This is the exact drift class that produced the offset-clamp mismatch (FV-3).
**User Impact:** None directly; a change to cursor/pagination semantics must be made in three places, and one will be missed.
**Root Cause:** Verified — copy-paste growth from one canonical handler.
**Gain vs Trade-off:** One shared helper parameterized by the service callable; pinned tests unchanged (same envelopes).
**If We Do It:** Pagination semantics have one home.
**If We Don't:** Triplication keeps inviting drift.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/handlers/history_handlers.py:118-193,456-501,513-569`
**Fix:** Extract one shared `_list_handler(service_call, extra_schema)` helper parameterized by the service callable; keep response envelopes byte-identical so the pinned tests stay green.
**Simplified Fix:** Three nearly identical copies of the same page-fetching recipe differ only in which data they fetch. Merging them into one shared recipe means future changes happen in one place.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

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

### FV-49 — `onboarding_already_complete` error code is emitted but unregistered; the audit regex misses keyword-arg codes
**Status:** ❌ Not Fixed
**Description:** The onboarding handler emits `code="onboarding_already_complete"` on the wire, but the code exists in neither `ErrorCodes` nor `LegacyErrorCodes` nor the audit test's known-list — and the audit regex only matches dict-literal `"code": "..."` forms, so keyword-arg emissions escape it entirely (the test file documents this blind spot itself).
**User Impact:** The renderer receives an unknown code and falls through to generic error handling (suspected: renderer branch not read) — a user re-triggering onboarding gets a generic "unknown error" instead of the intended "already complete" message.
**Root Cause:** Verified — the code predates/escaped the namespacing migration; the audit's coverage gap let it through.
**Gain vs Trade-off:** Registering the constant + extending the audit closes both the specific gap and the class.
**If We Do It:** The error registry's "single source of truth" claim holds; the audit catches keyword-arg codes.
**If We Don't:** Unknown-code drift continues silently.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/handlers/onboarding_handlers.py:196`
- `tests/test_error_codes_registry.py:117-121,157-158`
**Fix:** Register `ONBOARDING_ALREADY_COMPLETE` (client.* namespace) in `ErrorCodes`, use the constant, add it to the TS union parity set; extend the audit regex to also match `code="..."` keyword args (or add it to the documented keyword-arg list).
**Simplified Fix:** One error message the app sends isn't in the app's official list of error messages, so the interface shows a generic error instead. Adding it to the list — and teaching the checker to catch this pattern — fixes the message and the blind spot.
**Implementation Difficulty:** 🟢 Easy
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

### FV-51 — A device reporting `default_samplerate=0` becomes unopenable (no positivity guard before InputStream)
**Status:** ❌ Not Fixed
**Description:** The device manager reads a device's native sample rate straight from the driver-supplied info dict with no positivity/sanity check; a device reporting 0 flows into `sd.InputStream(samplerate=0)`, which PortAudio rejects at open (paInvalidSampleRate, -9996 — corrected by Review Wave 4; -9997 is paInvalidChannelCount) — even though the app's documented 16 kHz + PortAudio-resample fallback would work fine.
**User Impact:** A misbehaving driver makes the device unusable in the app (shows as "failed to open device") when it would otherwise record at the target rate. Prevalence unproven (no audio hardware in the sandbox).
**Root Cause:** Suspected prevalence / verified mechanism — missing validation of a driver-supplied value.
**Gain vs Trade-off:** Treat out-of-range rates as unknown → warn + use the target rate (mirrors the existing query-failure branch); pure robustness.
**If We Do It:** Misreporting devices still record.
**If We Don't:** The edge device stays unopenable.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/recording/device_manager.py:1477-1511`
- `voice_typer/server/recording/stream_lifecycle.py:171-197`
**Fix:** Treat `native_rate <= 0` (or outside [8000, 384000]) as unknown: log a warning and return `target_sr` (mirroring the existing query-failure branch). Add a unit test with a fake device info dict reporting 0.
**Simplified Fix:** If a microphone driver reports an invalid recording speed, the app currently passes that invalid value along and the device fails to open. Treating impossible values as "unknown" and falling back to the app's standard speed keeps the device working.
**Implementation Difficulty:** 🟢 Easy
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

### FV-53 — Dashboard empty state interpolates the raw backend hotkey string ("<caps_lock>" leak)
**Status:** ❌ Not Fixed
**Description:** The Dashboard's no-data description interpolates the RAW config hotkey value into "Press {hotkey} on the Home page to dictate…" — backend hotkeys are stored in technical syntax (`<caps_lock>`), and the Home page always normalizes before display, but this site does not.
**User Impact:** A first-run user (default hotkey) reads "Press <caps_lock> on the Home page to dictate." — technical syntax leaking into a first-run empty state, exactly where polish matters most.
**Root Cause:** Verified — raw config value interpolated without the `normalizeHotkey`/`formatHotkey`/HotkeyChips treatment.
**Gain vs Trade-off:** Pass the formatted hotkey (or render `HotkeyChips` per C-UI-1's Home precedent); aligns with C-UI-1/C-UI-2.
**If We Do It:** First-run users see "Press Caps Lock…" (or keycap chips) like everywhere else.
**If We Don't:** The syntax leak persists at the first-run moment.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:241-243`
- `voice_typer/client/src/renderer/src/pages/dashboard/hooks/useDashboardData.ts:229,568` (the raw source; path corrected by Review Wave 4)
**Fix:** Render `HotkeyChips` with the normalized hotkey — the PRIMARY fix, per C-UI-1's Home-line precedent (plain `formatHotkey` text is C-UI-1-noncompliant for multi-key hotkeys; ordering corrected by Review Wave 4).
**Simplified Fix:** The analytics page's welcome text shows the keyboard shortcut in internal code form, like "<caps_lock>", instead of the friendly "Caps Lock" the rest of the app displays. Running it through the same formatter fixes it.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-54 — Vocabulary's select-all operates on invisible rows (visibleIds not display-capped; Templates caps correctly)
**Status:** ❌ Not Fixed
**Description:** The shared collection header's select-all is specified to derive from the rows actually RENDERED (display-capped view). Templates slices its ids to the display cap (200); Vocabulary passes the full filtered list — so with more than 200 entries, the header checkbox, the "N selected" count, and bulk actions (delete/export) all operate on rows the user cannot see. Sibling pages, same control, different semantics.
**User Impact:** With a large vocabulary, a user can select-all and delete/export entries that are not on screen — invisible-row bulk operations with real data consequences.
**Root Cause:** Verified — Vocabulary never adopted the display-cap slice when the cap was added.
**Gain vs Trade-off:** One-line slice; brings Vocabulary in line with the shared contract and its sibling page.
**If We Do It:** Select-all means "what I can see"; bulk operations match the visible selection.
**If We Don't:** Invisible-row bulk deletes remain possible.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:390`
- `voice_typer/client/src/renderer/src/pages/Templates.tsx:228-230` (the correct pattern)
- `voice_typer/client/src/renderer/src/components/common/CollectionListHeader.tsx:51-54` (the contract doc)
**Fix:** `visibleIds={filteredSorted.slice(0, displayCount).map((e) => e._id)}`.
**Simplified Fix:** On the vocabulary page, "select all" currently selects every matching entry — including ones past the end of the visible list — so bulk actions can affect items the user cannot see. Limiting it to the visible rows, as the templates page already does, makes the behavior match the screen.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-55 — About & Privacy "Check for Updates" gives zero outcome feedback (silent success AND silent failure)
**Status:** ❌ Not Fixed
**Description:** The update-check action deliberately has no status readout: the only UI is the button label flipping to "Checking…" and back. Its catch block is silent by design ("the user can simply click the button again"). As a result the user cannot distinguish "no update available", "download started", and "the check failed (offline/GitHub unreachable)".
**User Impact:** An action whose result is invisible trains distrust; "click again" only makes sense if the user knows it failed.
**Root Cause:** Verified — minimalism extended to zero outcome feedback.
**Gain vs Trade-off:** A transient outcome (snackbar or one inline line: "You're up to date" / "Check failed — check your connection"); keep no-fetch-on-mount and the consent gate untouched.
**If We Do It:** Every check ends with an answer.
**If We Don't:** The button stays a coin flip.
**My Recommendation:** ✅ Implement (any wording must go through all 8 locales per C-I18N-1).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/AboutAndPrivacy.tsx:115-147`
**Fix:** Add a transient outcome line or snackbar for both success ("You're up to date") and failure ("Check failed — check your connection"); new keys in all 8 locales per C-I18N-1; keep the consent gate and no-fetch-on-mount untouched.
**Simplified Fix:** Clicking "Check for Updates" gives no indication of what happened — it worked, found nothing, or failed all look the same. A brief message showing the outcome removes the guesswork.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-56 — Renderer IPC types' comments still instruct contributors to add commands to the DELETED TS allowlist
**Status:** ❌ Not Fixed
**Description:** Ten comment sites in `types/ipc/requests.ts` (lines 241, 301, 344, 379, 391, 396, 437, 503, 582, 618 — count corrected by Review Wave 4) tell contributors a command is "in the renderer allowlist (`src/main/allowed-commands.ts`)" and to "add the matching `ALLOWED_COMMANDS` entry" — the file and the Set were deleted on 2026-09-17, and AGENTS.md §6.4 explicitly forbids reintroducing a third allowlist. This is the renderer-side twin of FV-22 (Rust side).
**User Impact:** None directly; a future contributor following the in-file instructions would break the two-allowlist parity architecture and fail the parity tests — or worse, "fix" the tests.
**Root Cause:** Verified — comments written pre-cutover; not updated when `src/main/` was deleted.
**Gain vs Trade-off:** Pure comment fix (reference the Rust allowlist + Python registry only).
**If We Do It:** The IPC type docs stop directing contributors into a forbidden edit.
**If We Don't:** The most-edited type file carries sabotage instructions.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc/requests.ts:241-243,344-346,378-380,386-397,434-443,500-511`
**Fix:** Reword all 10 comment sites to reference the real parity surface (`src-tauri/src/commands/sidecar_cmds/allowlist.rs` + Python `_COMMAND_REGISTRY`).
**Simplified Fix:** Notes in the app's interface-type files tell developers to register new commands in a list that was deleted — and that the project rules say must not come back. Updating the notes to point at the two real lists prevents well-intentioned breakage.
**Implementation Difficulty:** 🟢 Easy
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

### FV-58 — Windows worker build freezes from the unpinned system Python (PYBS env contract mismatch)
**Status:** ❌ Not Fixed
**Description:** The worker build step sets `VOICE_TYPER_PYBS_DIR` to a workspace-relative path, but the workflow extracts Python Build Standalone to `C:\tools\pybs\python` — the script's lookup chain (path check → `command -v python` fallback with only a stderr warning) then silently freezes the shipped worker exe from whatever unpinned interpreter is on PATH. Also, comments describing the worker/torch-free gates as "SKIP (Phase 2a pending)" are stale — the gates are live at HEAD.
**User Impact:** The shipped worker binary can be built from a non-checksum-verified interpreter; the misleading comments hide active gates. Never validated end-to-end (last full CI success predates the torch-free Phase 1c).
**Root Cause:** Verified (env/path contract written against a layout the workflow never used); runtime fallback behavior medium-confidence (static read).
**Gain vs Trade-off:** Fixing the env contract is a protected-workflow edit — requires user validation per C-CI-2; the comment corrections alone are docs-audit items inside a protected file (propose, don't edit silently).
**If We Do It:** The worker freezes from the pinned, hash-verified interpreter; comments match reality.
**If We Don't:** Release worker binaries keep an unpinned provenance.
**My Recommendation:** 🟡 Defer to user — workflow edit, C-CI-2-protected; propose the exact diff for user-validated change.
**Progress:** `None yet.`
**Related Files:**
- `.github/workflows/tauri-windows-build.yml:505-511,521-541,546-561,642-661` (esp. :534 vs :382-388)
- `scripts/build/build_worker_windows.sh` (the lookup chain)
**Fix:** Point `VOICE_TYPER_PYBS_DIR` at the actual extraction path (or make the script hard-fail when PYBS is a directory without python.exe); correct the stale gate comments. USER MUST CONFIRM (C-CI-2).
**Simplified Fix:** The step that builds a shipped helper program looks for its Python in the wrong folder, quietly falls back to whatever Python it finds, and builds anyway. Pointing it at the right folder — with the owner's approval, since the build recipe is protected — makes the helper's origin verifiable.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium

### FV-59 — ADR sequence gap: index lists ADR-0021 but the file is named XZ-R11-04 (dead index link)
**Status:** ❌ Not Fixed
**Description:** The ADR index's row for "0021-at-rest-encryption.md" points at a file that does not exist — the actual ADR is named `XZ-R11-04-at-rest-encryption.md`, violating the repo's own numbering convention (the docs skill's rule: continue the sequence, never a second scheme).
**User Impact:** Dead index link; the ADR is undiscoverable by number.
**Root Cause:** Verified — a session-tagged filename never normalized into the sequence.
**Gain vs Trade-off:** Pure rename + index update (docs-only).
**If We Do It:** The ADR sequence is contiguous and greppable.
**If We Don't:** The convention violation invites more out-of-sequence names.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `docs/adr/README.md:29`
- `docs/adr/XZ-R11-04-at-rest-encryption.md`
**Fix:** Rename to `0021-at-rest-encryption.md` + update the index (record the rename in archive/deleted_files.txt per E15).
**Simplified Fix:** A design-decision record was filed under a work-session name instead of the standard numbering, and the table of contents points at the standard number. Renaming it to fit the sequence fixes the broken link.
**Implementation Difficulty:** 🟢 Easy
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

### FV-61 — Sign/attestation gates compare a boolean input to the string 'true' (suspected silently-inert gates)
**Status:** ❌ Not Fixed
**Description:** Gate conditions string-compare `type: boolean` inputs (`inputs.sign == 'true'`): six sites in the Windows workflow plus ~6 more across the macOS and Linux workflows (codesign/notarize, attestation/SLSA) — ~12 gates across 3 workflows (scope widened by Review Wave 4). GitHub's expression coercion (bool→number in comparisons, `'true'`→NaN) is suspected to make these comparisons always false — which would make the signing gates, the fail-fast "sign=true but secrets missing" gate, and the SLSA attestation gate silently inert: the exact "silently ships unsigned" class those gates were built to prevent. Confidence ~80% (raised by Review Wave 4): GitHub's documented coercion semantics make `true == 'true'` resolve false, and git archaeology shows the working historical gate form was `env.WIN_CSC_LINK != ''` (pre-TX-23); run logs remain uninspectable from the sandbox, and AGENTS.md documents the gates as effective — verify-first stays the right recommendation.
**User Impact:** If confirmed: a misconfigured release could ship unsigned while every gate shows green. If the coercion works as documented elsewhere, impact is nil. Needs verification of a past signed dispatch's step list.
**Root Cause:** Suspected — GitHub expression-language coercion semantics applied to boolean inputs.
**Gain vs Trade-off:** Verification is free (read one past run); the fix if confirmed (`inputs.sign == true` or `formatBoolean`) is a protected-workflow edit requiring user validation per C-CI-2.
**If We Do It:** The gates demonstrably fire; the TX-23/CRIT-7 guarantees hold for real.
**If We Don't:** The signing guarantee may be resting on an expression quirk.
**My Recommendation:** 🟡 Defer to user — VERIFY FIRST (inspect a past signed dispatch: did "Sign the final NSIS installer" execute?); only if confirmed inert, apply the user-validated fix.
**Progress:** `None yet.`
**Related Files:**
- `.github/workflows/tauri-windows-build.yml:585,610,889,915,946,1136`
- `.github/workflows/tauri-macos-build.yml:855,861,915,922`
- `.github/workflows/tauri-linux-build.yml:412,1028`
**Fix:** First verify against a past signed run's step list (or a minimal boolean-input test workflow). If gates are inert, change comparisons to `inputs.sign == true`. USER MUST CONFIRM (C-CI-2/C-CI-11).
**Simplified Fix:** Several build-safety switches are written as "is the setting equal to the word 'true'" when the setting is actually a yes/no flag — a comparison style that may never match. Checking one past build's logs tells whether the safety switches actually work; if not, the comparison needs one small correction.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟡 Medium (suspected; P1 to verify)

## FV Low Priority (Wave 3)

### FV-62 — Batched history inserts resolve every future with the LAST row's id
**Status:** ❌ Not Fixed
**Description:** In the batched multi-row insert path, every item's future resolves with the same `last_row_id` (the last row's id), while the sibling encrypt-batch helper 40 lines earlier uses the correct per-row arithmetic. The docstring claims each future gets its own row id. Currently latent (the only caller passes `future=None`), but any future wait=True batch caller gets wrong ids → wrong-row operations.
**User Impact:** None today; latent wrong-row data operations for future callers.
**Root Cause:** Verified — the batching optimization copied single-row future resolution verbatim.
**Gain vs Trade-off:** One-line arithmetic fix.
**If We Do It:** The API honors its docstring.
**If We Don't:** A latent trap for the next batch caller.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/history_db_internals/writer.py:615-621` (vs `:473-479` the correct pattern)
**Fix:** `it.future.set_result(last_row_id - len(batch) + 1 + i)` per item.
**Simplified Fix:** When several history entries are saved in one batch, the "id of my saved row" answer given to each caller is always the id of the last one. Computing each row's own id fixes the promise.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-63 — sidecar_ws.py: ~145 lines of re-export/test-pin blocks, wrong `run()` docstring, message-string loop-stop check
**Status:** ❌ Not Fixed
**Description:** The canonical WS transport entrypoint carries (a) ~145 lines of `# noqa: F401` re-exports with test-pin-map comments (the same patch-attractor pattern as FV-37 — new instance, cross-referenced); (b) a `run()` docstring claiming "Returns the bound port" while it returns exit codes 0/1/2/3; (c) a graceful-stop check that string-matches CPython's RuntimeError message ("Event loop stopped") — brittle across Python versions.
**User Impact:** None; the doc bug misleads every reader of the transport entrypoint; the message-match can silently break on a Python upgrade.
**Root Cause:** Verified — split leftovers + one stale doc + one message-based classification.
**Gain vs Trade-off:** Trivial fixes; the re-export volume is defensible while tests pin paths (opportunistic per FV-37's strategy).
**If We Do It:** The entrypoint's docs tell the truth; loop-stop classification is robust.
**If We Don't:** Drift trap persists.
**My Recommendation:** ✅ Implement (docstring + robustness check; re-export trimming per FV-37's incremental policy).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/sidecar_ws.py:215-359,816-820,943-948`
**Fix:** Fix the `run()` docstring; replace the message-match with an exception-type+flag check (or a `server._ws_graceful_stop_requested`-only check); cross-reference FV-37 for the re-export strategy.
**Simplified Fix:** Three small blemishes in the app's main communication file: a wrong description of what the main function returns, a fragile way of detecting a normal shutdown, and a wall of test-only plumbing. Fixing the first two is trivial; the third shrinks gradually.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

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

### FV-65 — Payload-size validation measures ASCII-escaped JSON while the wire sends compact UTF-8 (CJK payloads measure ~2-3× their wire size)
**Status:** ❌ Not Fixed
**Description:** The payload validator measures size with default `json.dumps` (ensure_ascii=True, default separators), while the size cap and the actual wire send use `ensure_ascii=False` + compact separators + UTF-8 byte length. A CJK-heavy payload measures ~2× its real wire size in the validation guard (6 escaped characters vs 3 UTF-8 wire bytes per CJK char; up to ~3× for astral chars — corrected by Review Wave 4) — false rejections for payloads that would fit.
**User Impact:** Non-English dictation search/history payloads near the cap get rejected though they would fit on the wire.
**Root Cause:** Verified — three serialization conventions, never unified.
**Gain vs Trade-off:** One shared measure helper used by all three sites.
**If We Do It:** Validation matches reality for all languages.
**If We Don't:** The inconsistency invites future drift between the guards.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/ipc/validation.py:614` (vs `:311` and `ipc/sender.py:424`)
**Fix:** One shared `_measure_payload_bytes()` helper (ensure_ascii=False, compact separators, UTF-8 len) used by all three sites.
**Simplified Fix:** The size-check for outgoing messages counts characters as if they were typed in English, but the sending code counts real bytes — so messages in Arabic or Chinese look about twice as big as they are and get refused. Making the check count the same way as the sender fixes it.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-66 — ErrorCodes constants imported but bare string literals still emitted at 9 sites
**Status:** ❌ Not Fixed
**Description:** Both config_handlers.py and sidecar_ws.py import `ErrorCodes` yet still emit bare legacy literals (`"invalid_payload"`, `"invalid_field"`, `"auth_failed"`) at inline sites — the module's own comment says the constants were imported so the literals "can be replaced"; the replacement never finished. A related stale comment justifies an inline multi-error envelope as "temporary, owned by another agent" — now permanent.
**User Impact:** None; drift risk vs the single-source-of-truth registry intent.
**Root Cause:** Verified — partial migration to constants.
**Gain vs Trade-off:** Pure constant adoption; extend `_error_response` to accept extra data (the old constraint no longer applies) and fold the inline envelope in.
**If We Do It:** The registry is the single truth it claims to be.
**If We Don't:** Partial migration persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/handlers/config_handlers.py:111,146,532,543,561,570,583,597,603` (570 added, 582 corrected to 583 — Review Wave 4)
- `voice_typer/server/sidecar_ws.py:578`
**Fix:** Use `ErrorCodes.*`/`LegacyErrorCodes.*` at these sites; extend `_error_response` to accept an extra-data dict and fold the set_config multi-error envelope into it.
**Simplified Fix:** The project keeps a central list of error names, and half the code uses it while nine spots still type the names by hand. Finishing the switchover means error wording can never drift between the two.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-67 — Silence-mask uses `np.abs` (~115 MB transient at 30-min dictation) where a boolean comparison needs ~29 MB
**Status:** ❌ Not Fixed
**Description:** The discard-path silence mask computes `np.abs(flat)` on the full float32 buffer — a ~115 MB transient for a 30-minute dictation (the comment documents it as the accepted single allocation) — while the equivalent boolean pair comparison `(flat<0.001)&(flat>-0.001)` needs ~29 MB. The same pattern appears in the transcription quality summary.
**User Impact:** A stop()-time memory spike on long dictations; invisible on typical short sessions.
**Root Cause:** Verified — chosen for brevity, not measured.
**Gain vs Trade-off:** In-place boolean comparisons; identical result.
**If We Do It:** Stop-time RAM spike drops ~4×.
**If We Don't:** The spike persists on long sessions.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/recording/_recorder_split.py:1582-1591`
- `voice_typer/server/transcription_result.py:148-149`
**Fix:** Replace the abs() mask with in-place boolean comparisons.
**Simplified Fix:** To decide whether a long recording is silence, the app first makes a full copy of the audio with signs stripped — a big memory spike. Asking the same question without the copy uses a quarter of the memory.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-68 — `_last_rms` written under the state lock per chunk while discard() writes it lock-free (inconsistent discipline)
**Status:** ❌ Not Fixed
**Description:** The audio pipeline takes the state lock for every 16 Hz chunk write of `_last_rms` (a GIL-atomic float assignment) while the discard path writes the same field lock-free — pointless lock traffic that contradicts the file's own RACE-001 rationale about minimal lock scope in the hot path.
**User Impact:** None measurable; lock discipline noise.
**Root Cause:** Verified — inconsistent convention.
**Gain vs Trade-off:** Plain assignment + comment; zero risk.
**If We Do It:** Hot-path lock traffic drops by one acquisition per chunk.
**If We Don't:** Cosmetic inconsistency persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/recording/audio_pipeline.py:1015-1016` (vs `_recorder_split.py:810`)
**Fix:** Plain assignment + a comment explaining the atomicity rationale (mirror the discard path's convention).
**Simplified Fix:** One frequently-updated number is protected by a lock in one part of the code and not in another, though it doesn't need the lock at all. Dropping the lock in the busy part is safe and consistent.
**Implementation Difficulty:** 🟢 Easy
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

### FV-70 — macOS CoreAudio watcher: stop() before run-loop publication leaves the listener thread blocked forever
**Status:** ❌ Not Fixed
**Description:** If `stop()` wins the state lock before the run thread publishes its loop reference, the stop path snapshots None and never calls `CFRunLoopStop` — the thread blocks in `CFRunLoopRun()` forever (join times out), and registered listeners keep firing for the process lifetime.
**User Impact:** macOS-only; a watcher that stops during its startup window leaks its thread and keeps delivering device-change callbacks. Mechanism verified by trace; the ms-scale window not reproducible without macOS (VALIDATE ON MACOS HOST).
**Root Cause:** Suspected (timing window) / verified (mechanism).
**Gain vs Trade-off:** Re-check the stop flag after publishing the loop reference; trivial.
**If We Do It:** The watcher always terminates cleanly.
**If We Don't:** A rare thread leak persists on macOS.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/microphone_watcher_coreaudio.py:264-299` (vs `:487-493`)
**Fix:** Re-check `_stop_event.is_set()` after publishing `_run_loop`; fall through to the finally cleanup. VALIDATE ON MACOS HOST.
**Simplified Fix:** On Mac, if the device-watcher is told to stop at the exact moment it's starting, the stop signal is missed and the watcher runs forever in the background. Double-checking the stop signal after startup closes the gap.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-71 — `disconnect_handler.py:136` suppresses a real attribute-absence with `# type: ignore[attr-defined]`
**Status:** ❌ Not Fixed
**Description:** The disconnect path calls `proc.rebuild_from_config(config)` with a blanket attribute suppression, though the sibling probe uses getattr and the surrounding except already catches the failure. The suppression hides a genuine possible-absence (E13).
**User Impact:** None; type-checker blind spot on an error path.
**Root Cause:** Verified — suppression instead of the duck-typing used 30 lines away.
**Gain vs Trade-off:** getattr-style check; drop the ignore.
**If We Do It:** The error path is honestly typed.
**If We Don't:** One more sanctioned suppression.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/recording/disconnect_handler.py:136`
**Fix:** getattr-duck-typing (mirror the sibling probe), drop the ignore.
**Simplified Fix:** A "shut up" note to the type checker hides that a function might genuinely be missing on some objects. Checking for it directly, as neighboring code already does, is both honest and safer.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-72 — level-monitor docstring says idle timeout "default 5.0" — actual constant is 60.0
**Status:** ❌ Not Fixed
**Description:** `get_level`'s docstring narrates a 5-second idle timeout; the actual `_LEVEL_IDLE_TIMEOUT_SEC = 60.0` (the module docstring agrees with 60).
**User Impact:** None; misleading doc on a public method.
**Root Cause:** Verified — docstring drift.
**Gain vs Trade-off:** One-word fix.
**If We Do It:** Docs match the constant.
**If We Don't:** Cosmetic gap.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/level_monitor/monitoring.py:288` (vs `_state.py:241`)
**Fix:** Correct the docstring to 60.0.
**Simplified Fix:** One method description quotes the wrong idle timeout (5 seconds instead of 60). Fixing the number takes seconds.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-73 — Microphone-test recording: ~34-line setup block duplicated around the lock seam
**Status:** ❌ Not Fixed
**Description:** The "set test mode + reset state + start auto-stop timer" block is duplicated verbatim at two sites around the lock-release/restart seam (plus a branch whose body is only a comment pointing below the lock).
**User Impact:** None; the duplication is exactly where future timer/state changes must land twice.
**Root Cause:** Verified — copy-paste at a lock seam.
**Gain vs Trade-off:** Extract `_begin_test_locked(...)` (both sites hold the lock).
**If We Do It:** Test-mode setup has one home.
**If We Don't:** Drift risk at the lock seam.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/level_monitor/test_recording.py:528-561` (vs `:577-616`)
**Fix:** Extract a `_begin_test_locked(...)` helper used by both sites.
**Simplified Fix:** The code that prepares a microphone test is pasted twice around a locking boundary. Extracting it into one shared function means future changes are made once.
**Implementation Difficulty:** 🟢 Easy
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

### FV-75 — Microphone Quality accordion: padding contract comment describes a primitive padding that doesn't exist
**Status:** ❌ Not Fixed
**Description:** The comment narrates "the shared primitive already pads horizontally (px-4)" and "ONE deliberate px-2" — but the primitive's AccordionContent has NO horizontal padding, and the code renders RadioGroup `gap-1 px-4` plus per-row `p-2`. The rendered 24px total matches the pinned C-MIC-15 contract, so no visual regression — but the next editor "restoring" the narrated values breaks alignment. (Same drift class as FV-25, new instance.)
**User Impact:** None today; a trap for the next editor.
**Root Cause:** Suspected — padding moved from primitive to per-instance without updating the comment (or the contract text was written against an older primitive).
**Gain vs Trade-off:** Update the comment to describe the real padding sources (or realign to the C-MIC-14 stated premise — product call).
**If We Do It:** The comment matches the code it annotates.
**If We Don't:** The phantom-padding trap stays.
**My Recommendation:** ✅ Implement (comment fix; confirm the C-MIC-14 premise wording while there).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/microphone/components/PresetAccordionSelector.tsx:184-193,217,230`
- `voice_typer/client/src/renderer/src/components/ui/accordion.tsx:97`
**Fix:** Rewrite the comment to describe the actual padding sources (per-instance px-4 + row p-2 = the pinned 24px total); note the primitive drift vs C-MIC-14's premise.
**Simplified Fix:** A comment explains spacing as coming from a shared building block that no longer provides it. Rewriting the note to describe where the spacing really comes from prevents a future "fix" from breaking the layout.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-76 — Expanded Microphone Quality panel shows "MICROPHONE QUALITY" twice (header + switch-row reuse the same label)
**Status:** ❌ Not Fixed
**Description:** The expanded accordion shows the uppercase trigger label "MICROPHONE QUALITY" and, directly below, an enable-switch row whose visible label reuses the exact same i18n key — "Microphone Quality" appears twice on screen simultaneously.
**User Impact:** Reads as a duplication glitch in the expanded panel.
**Root Cause:** Verified — key reuse for the switch row.
**Gain vs Trade-off:** Label the switch row with a distinct key (e.g. "Enable audio processing", all 8 locales per C-I18N-1) or drop the row label (the switch is the only control).
**If We Do It:** The expanded panel reads clean.
**If We Don't:** Cosmetic duplication persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/microphone/components/PresetAccordionSelector.tsx:147-148,199-200`
**Fix:** Distinct key for the switch row (all 8 locales) or drop the redundant label.
**Simplified Fix:** When the microphone-quality section is expanded, its title appears twice — once as the section header and once as the label of the on/off switch. Giving the switch its own label (or none) removes the echo.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-77 — Dashboard header narrates a stale LOC budget and a pure helper lives in the page against its own architecture note
**Status:** ❌ Not Fixed
**Description:** The file header claims "LOC history: 732 (pre-split) → <150 (post-split)" vs actual 399 lines, and states pure helpers live in `./dashboard/lib/` while `computeTrend` sits in the page file.
**User Impact:** None; misleading size budget + architecture note for future editors (FV-25's class).
**Root Cause:** Verified — file grew post-split without updating the self-narrating header.
**Gain vs Trade-off:** Move `computeTrend` to `dashboard/lib`, correct the header.
**If We Do It:** The header's claims are true again.
**If We Don't:** Drift trap persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Dashboard.tsx:4,93-101`
**Fix:** Move `computeTrend` into `dashboard/lib/`; update the LOC note.
**Simplified Fix:** The analytics page's own notes claim it's under 150 lines with all helpers moved out — it's 399 with a helper still inside. Making the code match the notes (or vice versa) keeps the file honest.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-78 — "Show more" pill button is a byte-identical copy in Templates and Vocabulary (one has a testid, one doesn't)
**Status:** ❌ Not Fixed
**Description:** The raw "Show more" pill (100-char class stack) is duplicated byte-for-byte in both pages; Templates carries `data-testid="templates-show-more"`, Vocabulary's has none. (E7 duplicate UI logic.)
**User Impact:** None; styling drift risk + inconsistent test coverage.
**Root Cause:** Verified — copy-paste twin.
**Gain vs Trade-off:** Shared `ShowMoreButton` component with one testid convention.
**If We Do It:** One source of truth for the pill.
**If We Don't:** Twins drift.
**My Recommendation:** ✅ Implement (C-FILTER-1's sharing precedent supports it).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Templates.tsx:253-262`
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:439-447`
**Fix:** Extract a shared `ShowMoreButton` (or into the Collection family); one testid convention.
**Simplified Fix:** The "show more entries" button was copied between two pages with slightly different test hooks. Making it one shared component keeps the pages in sync.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-79 — Vocabulary's clear-all handler references state ~20-80 lines before its declaration (use-before-define reading hazard)
**Status:** ❌ Not Fixed
**Description:** `handleClearAllConfirm` references `persistVocabulary`, `setEntries`, and `selection` well before their declarations. It works (closures resolve at call time) but reads as use-before-define and is a lint-config hazard.
**User Impact:** None; comprehension cost.
**Root Cause:** Verified — definition order.
**Gain vs Trade-off:** Move the handler below the hook calls.
**If We Do It:** The file reads top-down.
**If We Don't:** Cosmetic hazard persists.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/pages/Vocabulary.tsx:78-93`
**Fix:** Move the handler below the state declarations it uses.
**Simplified Fix:** In the vocabulary page, the "clear all" logic appears in the file before the data it uses is defined. Reordering so definitions come first makes the file easier to read safely.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-80 — `reconnected` synthetic-event path skips the get_status catch-up the other connect paths perform
**Status:** ❌ Not Fixed
**Description:** The initial probe and the background reconnect both follow "connected" with a `get_status` catch-up through `applyStatusWithReason`; the `reconnected` host-bridge synthetic event only flips config + status. Setting connected also clears `lastError` while potentially leaving a stale `recordingState: "error"` — the pill/description disagreement class C-HOME-1 forbids, on a 5th sync path the contract text doesn't enumerate.
**User Impact:** Suspected transient (or, if the connect-time state push is dropped, persistent) Home-page state where the pill reads ERROR but the description shows the normal dictate hint.
**Root Cause:** Suspected — reliance on the connect-time `state_changed` push for this path (documented for WS-connect, not for the synthetic event).
**Gain vs Trade-off:** Mirror the catch-up + `applyStatusWithReason` in the handler; aligns with C-HOME-1's spirit.
**If We Do It:** Every reconnect path hydrates state identically.
**If We Don't:** The 5th path stays divergent.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useConnection.ts:558-574` (vs `:153-174`, `:417-437`)
**Fix:** Mirror the `get_status` catch-up + `applyStatusWithReason` in the `reconnected` handler.
**Simplified Fix:** When the app regains its backend connection one particular way, it skips a "what's the current state?" refresh the other ways perform — so the home screen can briefly show an error that no longer applies. Adding the same refresh to that path keeps the screen truthful.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-81 — python-namespace comment says "THREE Tauri events" while four listeners register (no shared cancel flag)
**Status:** ❌ Not Fixed
**Description:** The bridge's doc comment narrates three subscribed events with a shared cancelled flag; the code registers FOUR listeners (python-event, supervisor_relaunching, supervisor_reconnected, supervisor_failed), each self-cancelling.
**User Impact:** None; reader confusion.
**Root Cause:** Verified — comment not updated when supervisor_failed was added.
**Gain vs Trade-off:** Trivial comment fix.
**If We Do It:** The bridge's docs match its code.
**If We Don't:** Cosmetic.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/tauri-bridge/python-namespace.ts:18-23` (vs `:83-193`)
**Fix:** Update the comment to four listeners / per-listener cancellation.
**Simplified Fix:** A comment counting three event subscriptions sits above code that registers four. Correcting the count keeps the docs useful.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-82 — SetTrayLocaleRequest carries the doc comment of get_status (misplaced during the monolith split)
**Status:** ❌ Not Fixed
**Description:** The type for `set_tray_locale` (a locale+labels payload) is documented with the connection-readiness-probe description of `get_status` — copy-paste residue from the split of the types file.
**User Impact:** None; misleading documentation on the IPC surface types are supposed to keep accurate.
**Root Cause:** Verified — copy-paste during the split.
**Gain vs Trade-off:** Trivial doc move.
**If We Do It:** Each request type documents itself.
**If We Don't:** Cosmetic.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/types/ipc/requests.ts:30-36`
**Fix:** Replace with a set_tray_locale description; move the probe text to GetStatusRequest.
**Simplified Fix:** One command-type's description was pasted onto a different command-type. Putting each description on its own type makes the reference file trustworthy.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-83 — `compactNumber` dead conditional (both branches produce identical strings)
**Status:** ❌ Not Fixed
**Description:** The formatter branches on `display === Math.floor(display)` but in the true branch `Math.floor(display) === display`, so both returns produce the same string — leftover from a rounding change.
**User Impact:** None; dead branching noise in a shared formatter.
**Root Cause:** Verified.
**Gain vs Trade-off:** Collapse to a single return.
**If We Do It:** The formatter has no phantom branch.
**If We Don't:** Cosmetic.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/lib/format.ts:161-164`
**Fix:** Collapse to a single return.
**Simplified Fix:** A number-formatting helper contains an if/else whose two branches return exactly the same thing. Removing the pointless branch changes nothing and simplifies the code.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-84 — `useGlobalSearch` is a Zustand store filed under hooks/ (every other store lives in stores/)
**Status:** ❌ Not Fixed
**Description:** The module exports `create<GlobalSearchState>(...)` — a store, not a hook — filed under hooks/. Two other stores also live outside stores/ (useNavigation.ts, theme/themeStore.ts), so the placement is inconsistent rather than a single outlier (claim softened per Review Wave 4).
**User Impact:** None; discoverability cost (E3 "place by concern").
**Root Cause:** Verified — pre-convention placement.
**Gain vs Trade-off:** Move to `stores/` keeping the `use*` export name (zustand convention).
**If We Do It:** All stores live where stores live.
**If We Don't:** One misplaced module.
**My Recommendation:** ✅ Implement (next refactor touch).
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/client/src/renderer/src/hooks/useGlobalSearch.ts:13-19`
**Fix:** Move to `stores/` (keep the export name; update import paths).
**Simplified Fix:** The app's global search state module is filed under "hooks" though it's a data store, unlike every other store. Moving it to the store folder makes the layout predictable.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-85 — Segmented download: ETag read from the request header dict (always None) in the lengthless-HEAD Range-probe fallback
**Status:** ❌ Not Fixed
**Description:** The lengthless-HEAD Range-probe fallback (non-206 outcome) returns `base_headers.get("ETag")` — the REQUEST header dict, never set — so the ETag is always None there; the 206 branch and the true HEAD branch both read the response header correctly. (Branch attribution corrected by Review Wave 4.)
**User Impact:** Latent/unreachable today: this branch always pairs with total=None, which the only caller rejects before the resume-matching ever sees the etag (per Review Wave 4). The mismatch remains a live trap for any future caller that consumes the etag; the one-line fix is still correct.
**Root Cause:** Verified — copy-paste.
**Gain vs Trade-off:** One-line fix (`resp.getheader("ETag")`).
**If We Do It:** Resume validation works on the HEAD path too.
**If We Don't:** The final-hash safety net stays the only defense.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/segmented_download.py:327` (vs `:330`)
**Fix:** Return `resp.getheader("ETag")` in the lengthless-HEAD Range-probe fallback.
**Simplified Fix:** When checking whether a paused download can continue, one code path reads the file's fingerprint from the wrong place — the outgoing request instead of the response — so it always sees "no fingerprint". Reading it from the response makes the check real.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-86 — `_is_transient_http` helper defined, never called (the check is inlined verbatim 80 lines later)
**Status:** ❌ Not Fixed
**Description:** The retry-classification helper exists but the fetch loop inlines the identical `status == 429 or 500 <= status <= 599` expression — dead helper + drift risk (E7).
**User Impact:** None.
**Root Cause:** Verified.
**Gain vs Trade-off:** Call the helper at the inline site.
**If We Do It:** One retry-classification truth.
**If We Don't:** Twin logic.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/segmented_download.py:361-362` (vs `:441`)
**Fix:** Replace the inline expression with the helper call.
**Simplified Fix:** A helper that classifies retryable download errors exists but isn't used — the same rule is typed out again nearby. Using the helper removes the duplicate.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-87 — Snapshot file placement uses API-supplied paths with no local traversal guard
**Status:** ❌ Not Fixed
**Description:** The download snapshot writes files at `snap_dir / filename` where the filename comes from the remote tree API; the sanitizer that strips dangerous components applies only to scratch files. A compromised-but-allowlisted repo could direct writes outside the snapshot dir (needs manifest-pin cooperation; HF's own client has the same trust model — low).
**User Impact:** None under the current trust model; defense-in-depth gap.
**Root Cause:** Verified — no local guard on API-provided paths.
**Gain vs Trade-off:** Reject `..`/absolute components before placement; trivial.
**If We Do It:** Placement is locally safe regardless of upstream.
**If We Don't:** Relies on upstream trust.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `voice_typer/server/segmented_download.py:915-917` (vs the sanitizer at `:159-164`)
**Fix:** Reject `..`/absolute path components before placement (reuse the existing `_safe_filename` sanitizer).
**Simplified Fix:** File paths that come from a download server are trusted as-is when placing local files. Checking them for escape attempts ("..", absolute paths) first is cheap insurance.
**Implementation Difficulty:** 🟢 Easy
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

### FV-89 — env_logger fallback path uses millisecond ISO timestamps (C-LOG-1 template violation on the degraded path)
**Status:** ❌ Not Fixed
**Description:** When file-logger initialization fails, the fallback uses `env_logger` with `format_timestamp_millis()` — emitting `[ISO-T + millis LEVEL module] msg`, violating the C-LOG-1 terminal template (`HH:MM:SS  LEVEL  msg`, seconds-only, no module). The main logging path is fully compliant (verified).
**User Impact:** None in normal operation; log format breaks only on the degraded path.
**Root Cause:** Verified — fallback predates the C-LOG-1 template.
**Gain vs Trade-off:** Custom `.format()` mirroring the early logger's pattern; aligns with C-LOG-1.
**If We Do It:** Log format is uniform even in failure mode.
**If We Don't:** Degraded-path logs are unparseable by the format's tooling.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/platform/logging/init.rs:331-334` (fix pattern at `early.rs:123`)
**Fix:** Replace `format_timestamp_millis()` with a custom `.format()` mirroring early.rs's `"{} {:5} {}"` + `now_time_only`.
**Simplified Fix:** If the app's log file can't be opened, a fallback logger prints timestamps in a different, messier format than the project's standard. Teaching the fallback the standard format keeps logs consistent even in failure.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-90 — Tray menu `accelerator` field: documented as Python-populated and shortcut-wiring; no producer exists
**Status:** ❌ Not Fixed
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

### FV-91 — migrate/config_merge inline comment contradicts its own docstring and code (per-key vs whole-file)
**Status:** ❌ Not Fixed
**Description:** An inline comment claims per-key source selection; the corrected docstring and the code make one file-level decision.
**User Impact:** None; reader confusion on the migration path.
**Root Cause:** Verified — leftover comment from before the correction.
**Gain vs Trade-off:** One-line reword.
**If We Do It:** Comments agree with the code.
**If We Don't:** Cosmetic.
**My Recommendation:** ✅ Implement.
**Progress:** `None yet.`
**Related Files:**
- `src-tauri/src/migrate/config_merge.rs:132-134` (vs `:158`)
**Fix:** Reword the inline comment to the file-level mtime decision.
**Simplified Fix:** A note inside the config-merge code describes a per-setting strategy that the code doesn't use. Rewording it to match the actual strategy removes the contradiction.
**Implementation Difficulty:** 🟢 Easy
**Severity:** 🟢 Low

### FV-92 — Dismiss-hotkey on an idle bubble fire-and-forgets a dictation toggle (suspected hidden recording)
**Status:** ❌ Not Fixed
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
**Severity:** 🟢 Low (Medium if confirmed on host — Review Wave 4)

### FV-93 — Workflow comment drift: line-number citations rotted across tauri-*.yml and mutation.yml
**Status:** ❌ Not Fixed
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
**Status:** ❌ Not Fixed
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
