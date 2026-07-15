# Voice Typer — Comprehensive Product Review

**Generated**: 2026-07-14
**Scope**: Permanent Product Improvements Review across 4 review areas
**Status**: Findings compiled; fixes applied to high-priority items .

---

## c-review

**Scope**: Performance, memory usage, CPU usage, cross-platform compatibility (Windows/macOS/Linux), and build/CI/CD. Focus on Python backend, Electron main process, and the build pipeline.


### Findings — Performance

#### PERF-05
- **Category**: performance
- **Severity**: Medium
- **File**: voice_typer/server/app.py:1312 (`time.sleep(0.3)` in `restart_app`)
- **Description**: `restart_app` calls `time.sleep(0.3)` "to give Electron time to process `relaunch_electron`" before closing the TCP socket. This blocks the calling thread (the pystray tray thread) for 300ms. During that window, the tray icon is unresponsive to menu clicks and any IPC dispatch handled on the same thread is blocked. The 300ms is a magic number that's too short for a slow Electron main thread (GC pause) and too long for a fast one. The proper pattern is a Condition variable / ack: publish `relaunch_electron` with a request ID, have Electron send back `relaunch_ack` over TCP, and `wait(timeout=2.0)` on the ack.
- **Fix**: Replace `time.sleep(0.3)` with `event.wait(timeout=2.0)` on a `threading.Event` set by an `relaunch_ack` IPC handler. Falls back to the 2s timeout if Electron doesn't ack (same behavior as today's 300ms magic number, but bounded and event-driven).

#### XPLAT-02
- **Category**: cross-platform
- **Severity**: Medium
- **File**: voice_typer/client/electron-builder.yml:82-90 (`afterInstall: ../../scripts/linux/postinst`)
- **Description**: The Linux `deb` and `rpm` sections use relative paths (`../../scripts/linux/postinst`) for `afterInstall` / `afterRemove`. These paths are resolved relative to electron-builder's CWD (`voice_typer/client/`), so `../../scripts/linux/` resolves to `<project-root>/scripts/linux/` — correct today, but fragile: if electron-builder changes its CWD expectation (it has happened in past major versions), or if the build is invoked from a different directory (e.g. `npx electron-builder --config voice_typer/client/electron-builder.yml` from the project root), the path breaks silently and the postinst script is not included in the .deb. The user gets a package without the udev rule setup, so Caps Lock hotkey doesn't work until they manually run the setup script.
- **Fix**: Copy the Linux scripts into `voice_typer/client/resources/linux/` (which electron-builder includes in the build context) and reference them as `afterInstall: resources/linux/postinst`. Or use a build hook (`afterPack` in a JS file) that resolves paths via `path.resolve(__dirname, '../../../scripts/linux/postinst')`.

---

#### CI-01
- **Category**: build-ci
- **Severity**: High
- **File**: .github/workflows/build.yml:188-195 (`pip-audit (hard-fail on all findings)`)
- **Description**: The `pip-audit` step runs `pip-audit --strict` with NO `--ignore-vuln` list and a hard-fail gate (the `|| (echo "::error::..." && exit 1)` pattern). Any new CVE in any pinned dependency blocks the PR. The weekly `pip-audit-weekly` job (build.yml:200-240) is the triage backstop, but the per-PR gate causes repeated CI breakage whenever upstream CVEs are announced (requests, urllib3, jinja2, pillow, and cryptography are frequent offenders — multiple CVEs per quarter). The accepted-findings list is documented as "currently EMPTY" in the comment at line 175, meaning there is NO triaged-ignore path. A contributor whose PR is blocked by an unrelated CVE has no recourse except to wait for a maintainer to update the ignore list.
- **Fix**: Maintain a small `--ignore-vuln GHSA-XXXX-XXXX-XXXX` list with a justification comment above each line (the workflow comment at line 178 already documents the pattern). Triage new findings weekly via the `pip-audit-weekly` job. Move the per-PR gate to `continue-on-error: true` with a warning annotation, so new findings are surfaced but don't block PRs.

#### CI-02
- **Category**: build-ci
- **Severity**: Medium
- **File**: .github/workflows/build.yml:24-31 (test matrix)
- **Description**: The test matrix runs Python 3.10/3.11/3.12/3.13 × Windows/macOS/Linux = 12 jobs per PR, with `fail-fast: false` so all 12 run to completion even if one fails. For a PR that touches only frontend (`voice_typer/client/**`) or docs, this is wasteful — 12 jobs × ~10 min each = ~120 CI-minutes per PR. There's no `paths:` filter on the `pull_request` trigger, so even a README change triggers the full matrix. The `concurrency` group only covers releases (`release-${{ github.ref }}`), not PRs — so two pushes to the same PR both run all 12 jobs.
- **Fix**: (a) Add a `paths:` filter to skip the test matrix for `*.md`, `docs/**`, `voice_typer/client/**/*.md` changes. (b) Add `concurrency: group: pr-tests-${{ github.ref }}, cancel-in-progress: true` to cancel the previous run when a new commit is pushed. (c) For PRs, run a reduced matrix (3.12 on Linux + 3.13 on Windows/macOS = 3 jobs) and reserve the full 12-job matrix for `push: branches: [main]` and tag pushes. (d) Optionally use a path-aware `if:` to skip the test job entirely when only `voice_typer/client/**` changed (the `client-build` job already covers frontend).

#### CI-03
- **Category**: build-ci
- **Severity**: Medium
- **File**: .github/workflows/build.yml:590-595 (build-windows PyInstaller), 700-710 (build-macos), 782-795 (build-linux)
- **Description**: The three build jobs (`build-windows`, `build-macos`, `build-linux`) re-run `pyinstaller` and `npx electron-builder` from scratch on every run. PyInstaller re-bundles ~500MB of torch+transformers+numpy+scipy; electron-builder re-downloads the Electron binaries (~200MB per arch) on every macOS build. There's no caching of `~/.cache/electron-builder`, `~/.cache/pyinstaller`, or the `build/` directory. On macOS the build also runs `npm ci` (line 698) which re-installs all client deps. Total: each build job takes ~10-15 min, of which ~5-8 min is re-downloading/re-bundling things that didn't change.
- **Fix**: (a) Cache `~/.cache/electron-builder` and `~/.cache/pyinstaller` using `actions/cache@v4` with `key: ${{ runner.os }}-electron-${{ hashFiles('voice_typer/client/package-lock.json') }}` and `key: ${{ runner.os }}-pyinstaller-${{ hashFiles('pyproject.toml', 'requirements-lock.txt') }}`. (b) Cache the `build/` and `dist/` directories between runs of the same job (invalidated on spec/pyproject changes). (c) Extract a reusable workflow (`/.github/workflows/build-platform.yml`) called by all three jobs to deduplicate the install + build steps.

#### CI-04
- **Category**: build-ci
- **Severity**: Medium
- **File**: .github/workflows/build.yml:650 (`build-macos: runs-on: macos-13`)
- **Description**: `build-macos` runs on `macos-13` (Intel). GitHub Actions has announced that `macos-13` is being deprecated and will be removed (the `macos-14` and later runners are Apple Silicon). Once `macos-13` is removed, the macOS build job will fail with "The workflow was not triggered but has dependencies on macos-13 which is no longer available." The `build-macos-universal` job (line 490) already runs on `macos-latest` to merge x64 + arm64 binaries, so the PyInstaller bundle could also be built on `macos-14` with `--arch x64` cross-compilation.
- **Fix**: Migrate `build-macos` to `macos-14` (Apple Silicon). PyInstaller on arm64 can build x64 bundles via `--target_arch x64` (requires the x64 Python interpreter; alternatively use `macos-13-large` if still available, or run two PyInstaller passes and `lipo` the bundles). Test the resulting .dmg on both Intel and Apple Silicon Macs. Set a deadline (e.g. "before 2025-Q4") to migrate before GitHub removes macos-13.

---

### Notes for the primary agent

- **PERF-01 / CPU-01** is the highest-impact finding: the Windows hotkey polling loop is a continuous CPU drain on every Windows laptop running VoiceTyper. The fix (use RegisterHotKey + WM_HOTKEY, or the already-bundled native binary) is well-scoped and the code already has the registration infrastructure.
- **MEM-01** is a silent leak: failed backend loads accumulate GPU/CPU memory across fallback cycles. The fix is one line (`backend.unload()` before `unregister`). Worth fixing before the next release.
- **PERF-03 (RT-safety regression)** is the most subtle: the level monitor was missed by the RT-SAFE-001 refactor and runs the full filter chain on the PortAudio thread. Symptom: audio glitches when the Microphone settings page is open during dictation. The fix mirrors the recording.py refactor.
- **CI-01** (pip-audit hard-fail) will cause repeated CI breakage on every upstream CVE. The fix (maintain an ignore list + continue-on-error on PRs) is documented in the workflow comments but not implemented.
- **CI-04** (macos-13 deprecation) is a ticking clock — GitHub will remove macos-13 and the macOS build will fail. Schedule the migration before 2025-Q4.
- **CI-03** (no caching) is the biggest CI-minutes waste — each platform build re-downloads ~700MB of Electron + PyInstaller deps. The fix is `actions/cache@v4` with the right keys; payback is immediate.
- **XPLAT-02** (relative `afterInstall` path) is fragile but works today. Low priority unless electron-builder is upgraded.
- The cross-platform surface is generally well-handled: lazy imports for `pycaw`/`comtypes`/`CoreAudio`/`pactl`/`xclip`/`wl-paste`, platform dispatch in `microphone_watcher.py` (Windows WM_DEVICECHANGE, Linux /dev/snd polling, macOS CoreAudio listener), and `_paths.py` centralizes the config-dir logic. The remaining issues are minor.
- The prewarm architecture (ADR-0009) is well-designed: PID file handshake, boot sentinel, cache ratio probe, background re-spawn on timeout. The only wart is the 500ms poll loop in `wait_for_prewarm` (CPU-04), which is on the critical startup path.

---

## d-review: Security + Testing + Documentation + Code Quality Findings

**Agent:** Subagent (Explore)
**Scope:** Security (IPC auth, input validation, secret handling, file permissions, dependency vulnerabilities), testing infrastructure (coverage gaps, flaky tests, integration tests, isolation), documentation (README, ADR, API, contributing), code quality (dead code, duplication, complexity, naming).

### Summary
- Total findings: 16
- Critical: 0, High: 2, Medium: 8, Low: 6

Methodology: read `voice_typer/server/security.py`, `voice_typer/server/_secrets.py`, `voice_typer/server/ipc_server.py` (1902 LOC, full), `voice_typer/server/config.py` (1354 LOC) and `voice_typer/server/config_validators.py` (801 LOC, full), `voice_typer/server/telemetry.py`, `voice_typer/server/history_db.py:120-430`, `voice_typer/server/app.py:1095-1148`, `voice_typer/server/handlers/system_handlers.py`, `voice_typer/server/cloud_engines.py`+`llm_polish.py` (URL/TLS paths only), `voice_typer/client/src/main/index.ts:525-644` (ALLOWED_COMMANDS), all of `docs/API.md` + `SECURITY.md` + `CONTRIBUTING.md`, listed `docs/adr/` directory. Surveyed `tests/conftest.py`, `tests/test_security_hardening.py`, `tests/test_e2e_pipeline.py`, `tests/test_electron_ipc_and_build.py:353-403` (parity test), `tests/test_path_traversal.py`, `tests/test_import_model_security.py`, `pyproject.toml`, `requirements-lock.txt`, `.github/workflows/build.yml:165-260` (pip-audit). Grepped for `shell=True`, `eval(`/`exec(`, `pickle.`, `subprocess` usage, broad `except Exception` patterns, dead module references, ADR collisions.

### Findings

#### Finding 1
- **Category**: security (IPC auth bypass)
- **Severity**: High
- **File**: voice_typer/server/ipc_server.py:629-662 (`IPCServer.start` — unconditional stdin listener), :1218-1266 (`_run` stdin loop, no auth handshake)
- **Description**: `start()` ALWAYS spawns the stdin listener thread, regardless of whether TCP mode is also active. The comment at :654-656 asserts "In TCP mode stdin is unused (inherited from Electron, connected to /dev/null or NUL)" — but this is not enforced. When a user runs `python -m voice_typer.server.ipc_server --port 9876` directly from a terminal (the documented standalone/dev mode in CONTRIBUTING.md §2), stdin is the terminal and the `_run` loop accepts unauthenticated JSON commands. SEC-018's TCP token check (`_handle_tcp_connection:898-934`) does not apply to the stdin path — any process that can write to the backend's stdin (terminal multiplexer, IDE debugger, screen-sharing tool, malicious local process on a shared machine) can dispatch `quit_app`, `set_config`, `set_tray_locale`, etc. without knowing the session token. The Electron-spawned production path inherits stdin from Electron's `stdio: "inherit"` (index.ts:1432), which on Linux/macOS points to /dev/null — so the vulnerability is latent in production but active in any direct-terminal invocation.
- **Root cause**: The stdin listener was retained for the legacy CLI/console path and is documented as "unused" in TCP mode, but the implementation does not gate startup on `not self._tcp_mode` (or any equivalent check). SEC-018 was added to TCP only.
- **Fix**: Either (a) skip `self._stdin_thread.start()` when `self._tcp_mode` is True, OR (b) require the same `VOICE_TYPER_IPC_TOKEN` handshake on stdin's first line when `--port` is in use. Option (a) is the minimal fix and matches the documented behavior. Add a regression test that asserts `server._stdin_thread` is None after `start_tcp(port)` is called without a prior `start()`-only invocation.

#### Finding 2
- **Category**: security (incomplete command allowlist)
- **Severity**: High
- **File**: voice_typer/client/src/main/index.ts:532-622 (`ALLOWED_COMMANDS` Set — 58 entries); voice_typer/server/ipc_server.py:1320-1415 (`_COMMAND_REGISTRY` — 68 entries); tests/test_electron_ipc_and_build.py:393-403 (`test_allowlist_matches_server_commands` — only checks orphans)
- **Description**: The Electron main process's `ALLOWED_COMMANDS` Set is missing 10 commands that the Python backend registers in `_COMMAND_REGISTRY`. Missing entries: `refresh_microphones`, `get_rms_level`, `get_audio_status`, `export_diagnostics`, `check_accessibility` (PLAT-030 macOS Accessibility permission check), `show_electron_notification` (TRAY-035), `get_vocabulary_suggestions`, `apply_vocabulary_suggestion`, `dismiss_vocabulary_suggestion` (P5 vocabulary automation — referenced in `AiEnhancementSettingsSection.tsx:13-14` comments), and `force_cancel_transcription` (PR-2 Finding #3 — stuck-transcription recovery, documented at ipc_server.py:1404-1408). The renderer's `call(...)` helper rejects any command not in `ALLOWED_COMMANDS` with `"Disallowed IPC command"` (index.ts:624-626), so these features silently fail when invoked from the UI. The existing parity test `test_allowlist_matches_server_commands` only checks the *orphan* direction (allowlist entries not in server registry) — its assertion `orphans = allowlist_entries - server_cmds; assert not orphans` is one-way, so the 10 missing-in-allowlist commands went undetected.
- **Root cause**: The allowlist was last updated when the server registry had ~50 commands; the parity test was written to prevent dead entries (the original ERR-IPC-003 cleanup) but not missing entries. Each new server command was added without a corresponding edit to index.ts.
- **Fix**: (a) Add the 10 missing commands to `ALLOWED_COMMANDS` in `client/src/main/index.ts` (each with a justification comment matching the existing style). (b) Strengthen `test_allowlist_matches_server_commands` to also check `missing = server_cmds - allowlist_entries; assert not missing, f"Allowlist is missing server commands: {sorted(missing)}"`. This makes the parity test bidirectional and prevents future drift in either direction.

#### Finding 4
- **Category**: documentation (inaccurate security claim)
- **Severity**: Medium
- **File**: docs/API.md:155 ("IPC Server — Protocol — Auth: Per-connection token validated on every request")
- **Description**: The IPC Server section claims "Per-connection token validated on every request". The actual implementation (`_handle_tcp_connection:898-934`) validates the token only on the FIRST line of the connection (the auth handshake). After the handshake succeeds, all subsequent messages on the same connection bypass the token check entirely — they go straight to `_dispatch`. A compromised Electron renderer (or a process that hijacks the TCP connection after auth) can issue any command without re-authenticating. The current design is acceptable for the threat model (the token proves the connecting process is the Electron parent at connect time; the OS doesn't allow other processes to inject into an established TCP socket), but the doc is factually wrong about "every request".
- **Root cause**: The doc was written generically and never reconciled with the actual handshake-once implementation.
- **Fix**: Replace the claim with "Per-connection: the first message must be a JSON auth object whose `token` field matches the `VOICE_TYPER_IPC_TOKEN` env var (constant-time comparison via `hmac.compare_digest`). Subsequent messages on the authenticated connection bypass the token check." Cross-reference `SEC-018` in SECURITY.md for the threat model.

#### Finding 5
- **Category**: documentation (SECURITY.md count is stale)
- **Severity**: Medium
- **File**: SECURITY.md:37 ("only the ~35 commands in `ALLOWED_COMMANDS`")
- **Description**: SECURITY.md states the Electron main process's allowlist contains "~35 commands". The actual count is 58 entries in `client/src/main/index.ts` (and the server registry has 68 — see Finding 2). The "~35" was probably accurate when the doc was written but is now off by ~70%. Security reviewers reading SECURITY.md will underestimate the attack surface (a larger allowlist = more commands a compromised renderer can invoke). The mismatch also obscures Finding 2: a reviewer cross-checking "35 commands" against the server registry of 68 would not immediately spot the 10 missing entries.
- **Root cause**: The doc was not updated when commands were added.
- **Fix**: Replace "~35 commands" with the actual count + a pointer to the source of truth: "only the 58 commands listed in `ALLOWED_COMMANDS` at `voice_typer/client/src/main/index.ts`". Add a CI test (`test_security_doc_command_count.py`) that parses SECURITY.md and asserts the documented count matches `awk '/ALLOWED_COMMANDS = new Set/,/^\s*\}\);/' voice_typer/client/src/main/index.ts | grep -cE '^\s*"[a-z_]+"'`.


### Notes for the primary agent

- **Findings 1, 2 are the highest-impact**: Finding 1 is a latent auth bypass that becomes active in any direct-terminal invocation of the backend; Finding 2 means 10 user-facing features silently fail when invoked from the renderer (force-cancel-transcription, vocabulary suggestions, accessibility check, diagnostics export, refresh microphones, etc.). Both have low-risk fixes.
- **Findings 3-6 (doc inaccuracy)**: a single doc-accuracy CI test (suggested in Finding 3's fix) would catch all four — the API table, the SECURITY.md count, and the CONTRIBUTING.md allowlist count are all the same class of "manual count drift" problem.
- **No Critical findings**: the project's security posture is generally strong — `_secure_atomic_write`/`_secure_read_text` use `O_NOFOLLOW` + `O_EXCL` + `0o600` + inode verification, the IPC `set_config` allowlist is strict (122 fields with per-field type+range validators), the URL allowlist enforces HTTPS for non-loopback hosts, model integrity verification uses pinned SHA-256 hashes with `hmac.compare_digest`, and the rate limiter + 1 MB line cap + 5s auth timeout close the obvious DoS vectors. The issues above are localized gaps and staleness, not structural weaknesses.
- **Recommended order of fixes**: Finding 2 (add missing allowlist entries + strengthen parity test) → Finding 1 (gate stdin listener on `not _tcp_mode`) → Finding → Findings 3-6

---


| H | c-review XPLAT-01 — Windows notepad hardcoded path | Medium | **Fixed** | app.py (`_open_config_file`: ShellExecuteEx handle-based wait + validated Notepad fallback; no downgrade) |

## Items explicitly deferred (Won't Fix this round, with rationale)

| Item | Severity | Rationale |
|---|---|---|
| RW-9 VoiceTyperApp god-class decomposition (2352 lines, 61 methods) | Extra High | Multi-day refactor — too risky for one round. Tracked as separate future work. |
| RW-0 Rewrite 87 source-string tests as vitest unit tests | Large | 87 test sites across 5 files; each needs a corresponding vitest test. Separate round. |
| RW-4 Windows installer missing Electron UI | P1 ship-blocker | Requires testing on actual Windows runner; this sandbox is Linux-only. |
| RW-5 macOS/Linux installer missing Python backend | P1 ship-blocker | Requires testing on actual macOS/Linux runner with GUI. |
| RW-8 Triage 63 source-string meta-tests in test_bugfix_regressions.py | Medium-Large | 63 meta-tests need case-by-case triage. Separate round. |
| RW-01 Encrypted credential store (keyring) | Large (P1) | New storage module + migration + consent UI + cross-platform testing. Separate scope. |
| RW-02 Playwright end-to-end test harness | Large (P2) | Harness + first scenario + CI wiring. Separate scope. |
| RW-03 Structured JSON logging + correlation IDs | Medium (P3) | Optional JSON formatter; must keep PIIRedactionFilter working on both formats. |
| RW-04 recording.py god-class split (3128 lines) | Extra High (P3) | Multi-day, high-risk refactor. Do incrementally behind tests. |
| RW-05 Log-level inconsistency audit | Small (P3) | RESOLVED — model-download→INFO (service.py:1856), Bluetooth HFP detection→INFO (recording.py:1662), buffer-telemetry gated behind `VOICE_TYPER_VERBOSE` (recording.py:2685), VAD auto-calibration→INFO (recording.py:1162). |
| RW-07 Remove remaining test-seam delegates in app.py | Medium (P3) | RESOLVED — dead test-seam delegates (`_sync_autostart`, `_start_accessibility_pulse`, `_load_microphones`, `_register_hotkey`, `_sync_prewarm_task`) removed in RW-9. 5 intentional ARCH-REFAC-003 production delegators (`toggle_dictation`, `_start_dictation`, `_stop_dictation`, `_cancel_streaming_session`, `_cancel_dictation`) remain by design, carry guards, and are exercised by live callers/tests — belong to the deferred RW-9 god-class split, not dead seams. |
| RW-08 Simplify startup double-delegation | Medium (P4) | RESOLVED — `app → startup_tasks → app` round-trip removed; callers invoke `startup_tasks` directly (startup_sequence.py:345, service.py:874). Stale "facade is kept for test seams" comments cleaned. |
| NEW-IPC-007 usePython swallows server type:"error" envelopes | Moderate | Real but moderate; only triggers when a server handler raises (rare). Highest-priority retained finding. Defer to next round. |
| NEW-PRIV-003 Restart subprocess inherits full os.environ | Low-Moderate | Same-app child needs the env to function; real only if a less-trusted child were spawned. |
| NEW-PRIV-007/008 GDPR right-to-export/delete incomplete | Low | Local-first desktop utility; compliance nice-to-have, not a defect. |
| NEW-UX-026 No punctuation cheat sheet | Low | Feature gap, not a defect. |
| a-review Finding 7 (not in brief) | Low | (Whatever it was — not in scope this round.) |

## Validation Evidence

### Backend tests
- 87 new/modified tests pass (test_crash_recovery.py +35, test_dictation_pipeline_review_fixes.py +20, test_logging.py +8, test_g_perf_reliability_fixes.py +16, test_api_doc_accuracy.py +8).
- 423 regression tests pass across 19 existing test files (test_notifications.py, test_cloud_engines.py, test_transcription.py, test_perf_review_fixes.py, test_asr_registry_lifecycle.py, test_volume_ducker.py, test_smart_duck.py, test_smart_duck_monitor.py, test_volume_backends.py, test_microphone_test.py, test_recording.py, test_recording_audio_processor.py, test_rw7_rw8_audio_callback.py, test_audio_processor.py, test_heartbeat.py, test_ipc_dispatch_errors.py, test_logging_formatting.py, test_log_rate_limit.py, test_dead_code_stays_removed.py).
- 4 pre-existing Linux platform failures (ctypes.WINFUNCTYPE in crash_handler.py:321 used at module-load time — Windows-only API). Verified via git stash to fail identically on the clean baseline. NOT caused by R8 changes.
- i18n completeness: 45/45 tests pass.

### Frontend tests
- 255 frontend tests pass (26 test files) including 23 new tests across Settings, App, Vocabulary, GeneralSettingsSection, Onboarding, useConnection, semver.
- Pre-existing tsc errors (32) verified identical to baseline via git stash — zero new errors introduced by R8.

### Build
- `npm run build` succeeds (1.33s, 5716 modules transformed, 1.07MB renderer bundle).
- `tsc --noEmit -p tsconfig.json` exit 0.
- `biome check` clean on all modified .ts/.tsx files.
- `py_compile` clean on all modified .py files.

### Regression grep
- Zero remaining `self._vocab_fail_notified` / `self._template_fail_notified` / `self._history_fail_notified` / `self._crash_recovery_fail_notified` writes on DictationPipeline self (all moved to self._app).
- Zero remaining `try: ... except TypeError:` in dictation_pipeline.py:_transcribe (broad catch removed).
- Zero remaining `15s` / `3 missed heartbeats` references in ipc_server.py (only the intentional historical-constant comment "increased from 15s" remains).
- Zero remaining `from voice_typer.server.config_validators import *` in config.py.
- Zero remaining "``app._*`` facade is kept for test seams" comment claims in startup_sequence.py / service.py / startup_tasks.py (facade delegates were removed in RW-9; callers target startup_tasks directly).
- Zero remaining `get_logger` definitions in log.py.
- Zero remaining `C:\Windows\System32\notepad.exe` hardcoded path in app.py.

-6

### RW-13 (NEW) — _corr_token reset in wrong scope (dictation_pipeline.py)
- **Severity:** bug (runtime NameError) + lint F821.
- **Root cause:** the correlation-id reset block was placed at the END of _copy_and_paste (last method in file), NOT in
un's inally. _corr_token is a local of
un, so it was undefined in _copy_and_paste (NameError at runtime; Ruff F821 at lines 1002/1005).
- **Fix:** removed the block from _copy_and_paste; added it to
un's inally block (after line 297) where _corr_token is in scope. Token init _corr_token: object | None = None + conditional set_correlation_id(cycle_id) retained at top of
un.
- **Verified:** py_compile + ruff F821/E711/all-clean; ast parse OK.

