# worklog.md

## 2026-08-26 review.md first-30 applicable tasks — completion wave (Windows win32 host)

### Scope resolution
First-30 findings enumerated in review.md document order; every status re-verified against current code BEFORE implementation (many were stale). Already-fixed/superseded entries verified and closed with evidence instead of re-implemented: ARCH-9, ARCH-12 policy, TEST-2 worst-10, FZ-27 (error.rs landed post-audit), SU-3, SU-7, SU-19, SU-20, SU-21, SU-22 (superseded by design — test_hf_cache_prune guards ban auto-eviction), SU-23/24/26, SU-27/28 (verified in code), SU-29/30, SU-35, SU-37, SU-38 (639<800), WM-9, ZU-19 bulk, app_cleanup tests, YJ-53 shutdown package. AB-53 kept Won't Fix with strengthened rationale (deliberate TOCTOU manifest re-read; caching would freeze hashes across dev rebuilds).

### Implemented this session (sub-agents: 6 investigation + 9 implementation slices + 1 QA audit)
- **NH-43** bubble dismiss global shortcut (`CommandOrControl+Shift+D`): new `client/src/main/shortcuts/global-shortcuts.ts`, whenReady/will-quit wiring, shared `dismissAndHideBubble()` extraction, shortcuts-catalog entry + HelpOverlay row via HotkeyChips, i18n key in all 8 locales (genuine translations).
- **YJ-16** logger facade: `redactArgsForFile` single primitive (parts-array parameterization keeps both sinks byte-identical); printfLogger duplicate formatter deleted; both public names preserved.
- **FZ-66** renamed production-called `_flushPendingOutbound`/`_resetPendingOutbound` → public names (6 files incl. string-keyed vi.mock factories).
- **FZ-62** Tauri parity: Rust `set_host_locale` command (+`SidecarState.host_locale`, require_main_window gate), bridge `setLocale`, renderer comments de-staled, parity-list + mig19 frozen-command contract updated lockstep.
- **FZ-57** platform-flag migration (container_detect, diagnostics_export×2, volume_backends/macos, hotkeys delegation, credential_store/_migration, config_internals/paths) + tokenize-based drift guard `tests/test_platform_flag_guard.py` (covers ==/!=/startswith AND membership forms; crash_handler allowlisted by design).
- **AB-49** allocation-free `analyze_full_audio`: blocked fp64 sum-of-squares (~8 MB bounded scratch; flat fp32 BLAS dot rejected — ~2e-4 relative drift corrupts cancellation-based noise_ratio), peak without np.abs temp, clamped variance; numeric-equivalence regression suite added.
- **DJ-14** GPU→CPU fallback user feedback (option b-lite): `gpu_cpu_fallback` event published AFTER classification / BEFORE synchronous reload (exception-guarded), tray toast handler wired + subscribed/unsubscribed, event registered in EVENT_TYPES + Rust ALLOWED_EVENT_TYPES (37→38 events, doc+test lockstep), order-sensitive publish-before-reload test.
- **SU-2** history_db.py split 2906→1730 LOC: extracted `history_db_internals/{encryption,corruption_recovery,crud_writes}.py` + checkpoint/FTS into writer.py; lazy `_hd.*` constant reads preserved; 12 inspect.getsource/caplog pins retargeted; avoided the tombstoned `recovery.py` module name.
- **EO-8** recorder.py 2913→2703: `__init__` decomposed into 12 `_init_*` helpers (AST-verified attribute parity), recording/format.py extracted, capture/pipeline body moves, DeviceStateShimMixin for 7 device-state property pairs; 7 pins converted to behavioral assertions per ARCH-12 policy.
- **DT-38 residual**: server_platform/__init__.py 358→256 LOC + prewarm/__init__.py 149→54 LOC; ALL `_pkg.` call-time indirection eliminated across both packages; ~80 test files migrated to owning-submodule patch targets (mic cluster 87 refs, autostart cluster 137 refs incl. object-form); consumer imports converted where import-time binds would have silently no-op'd patches (volume_ducker, status_handlers, startup_tasks/settings_controller/startup_sequence); C-CROSS-1..5 semantics untouched (143 autostart/platform tests green).
- **FZ-58 Tier-1**: 13 ticket-named test files merged into same-domain parents with EXACT collected-count arithmetic per merge (zero silent shadowing); archive/deleted_files.txt ledger created; stale production citations repointed at live tests (ipc_server, sidecar_ws, ipc/_helpers, ipc/registry, recorder, recording/__init__, tray_elapsed_timer, handlers/_log).
- **E2 baseline failures**: pyrefly-baseline.json documented-metadata restoration (errors array byte-identical; append-only audit trail keys returned; hardened parametrized guard replaces single-key test); mic-level/recording-controller failures NOT reproducible across 20 stress iterations → classified as baseline-window load flakes, no code change (evidence over guessing).
- **ZU-19 tail**: FakeConfig consolidated into tests/fixtures/config_helpers.py; hygiene drift-guard test added.

### Integration fixes (orchestrator-owned)
main.rs contract pins (385→389 lines, 18→19 commands, doc+test lockstep), 14 E501s from patch-path retargeting, cp1252-safe comment sweep, ruff F401/F841 fallout, stale-comment nits (bubble-handlers, send-to-python backticks, invented AUDIO-NP prefix removed), gpu_cpu_fallback registration lockstep, independent QA audit findings (verdict FIX-FIRST → all blockers/should-fixes/nits resolved).

### Constraints honored
C-CROSS-1..5 untouched; C-MIC-7..10 enumeration semantics untouched; C-I18N-1/2 (all 8 locales, genuine translations); C-BRAND-1 clean (check_branding OK); C-STYLE-1 (no ticket IDs in source); C-LOG-1/2 untouched; E15 deletions recorded; E16 big tasks sequential with disjoint slice waves; E18 no git reset/stash/restore used by any agent.

### Validation performed (Windows win32)
Per-wave targeted suites green after EVERY wave (counts in agent reports above): history suites 285p, recorder sweep 801p, autostart 357p, mic 495p, prewarm/handlers 475p, merged parents 792p, logging/bubble/tcp 210p vitest, cargo test 489+22p, tauri-bridge vitest 25p, doc-accuracy 34p. Full-suite gates: see final gate section appended below after the closing run.

### Known limitations
- recorder.py/model_manager lock-ownership extraction beyond module split left as optional polish (documented in GQ-28/EO-8 statuses).
- FZ-58 Tier-2 (mixed-domain grab-bag splits) remains scheduled handoff.
- Concurrent user workstream on microphone-test/level_monitor files ran in the same checkout throughout; those files were treated as frozen by every agent and are excluded from this session's commit.

---


## 2026-XX Microphone startup reconciliation + config canonicality audit

### Root causes found
1. **Stale `microphone` id survived startup** — `startup_tasks.load_microphones()` enumerated devices but never validated `config.microphone` against them. Reconciliation lived ONLY in the renderer (`useMicrophoneData.ts::reconcileActiveMic`), so it ran on Microphone-page open, too late, with two user-visible snacks.
2. **"mic-42"** — exists in ZERO production paths; it is a test-fixture literal (`tests/test_onboarding_apply_rollback.py`). Any unresolvable persisted string hits the same validation gap; fix is generic (validate whatever is persisted).
3. **Dev-vs-built config "overwrites"** — verified NOT a race: both runtimes share one profile (`~/.voice-typer` legacy-first, mirrored in `client/src/main/config-dir.ts`); Electron single-instance lock (per-userData) + Python `Local\VoiceTyperSingleInstance` mutex prevent concurrent backends. The only overwrite path is the documented stale-build downgrade (older build drops newer-schema unknown keys at next explicit save — warned in `config/loader.py::_filter_unknown_keys_impl`). Intentional design; documented in AGENTS.md C-CONF-5 rather than changed (E5/E12).
4. **`null` semantics confirmed canonical** — `"microphone": null` = System Default end-to-end (allowlist validator `str|None`, `resolve_mic_id_to_device_index(None) → None`, recorder opens OS default). Preserved and documented (C-CONF-4).
5. **Live monitor/consent** — already correct: `useMicrophoneLevelMonitor` starts only when `config.voice_biometric_consent`; backend enforces `consent_required`; "Monitoring Off" without consent is not a selection failure. No changes needed; no race with reconciliation (backend persists before renderer connects).

### Changes
- `voice_typer/server/startup_tasks.py`: new `_reconcile_configured_microphone(app, mics)` + `_publish_mic_reconciled()`; called from every `load_microphones()` path (startup phase-6 mic task AND tray refresh). Stale str id → silent fallback to null + WARNING diagnostic + `config_changed {microphone: null}` push. Legacy resolvable id → migrated to stable id + INFO + push. Valid id → untouched, healthy INFO. Empty enumeration / non-str values → never touched. Reconciler crash wrapped so enumeration/tray update never degrades.
- `tests/test_startup_mic_reconciliation.py`: 10 tests (stale fallback+persist+log+event, valid untouched, None no-op, legacy migration, empty-enumeration guard, non-str guard, lock usage, resolver-crash fail-safe, load_microphones integration ×2).
- `AGENTS.md`: appended Category "Configuration Canonicality & Microphone Startup Reconciliation" (C-CONF-1..C-CONF-5). Append-only.
- Frontend: NO code change required — renderer already merges `config_changed` payloads (`useSettingsConfig.mergeExternalConfig`) and the page keeps its mid-session hot-unplug snack (legitimate UX).

### Constraints respected
- No IPC surface change (no parity-test impact; `tests/test_electron_ipc_and_build.py` green).
- No sub-agent tool available in this harness — investigation/implementation phases run sequentially with file-ownership discipline instead.
- No web-search tool available in this harness; relied on repo code + tests per task directive ("Trust the source code... over assumptions").

### Validation performed (Windows 11 host)
- `pytest tests/test_startup_mic_reconciliation.py` — 10 passed.
- `pytest tests/regressions/test_audio.py tests/app/test_tray_and_console.py tests/app/test_lifecycle.py` — 87 passed.
- `pytest tests/test_startup_perf.py tests/test_startup_onboarding_marker.py` — 8 passed.
- `pytest tests/test_electron_ipc_and_build.py` — 36 passed (IPC parity intact).
- `ruff check` on changed files — clean. `pyrefly check` on changed files — 0 errors.

### Known limitations
- Full pytest suite not re-run this session (pre-existing uncommitted working tree from other sessions present; targeted suites above cover all touched behavior). Baseline failures per E2 not re-baselined for the same reason.


## 2026-XX (2nd) Microphone page: concrete-device selection + test transport + UI dedup

### Root causes found
1. **"Selected microphone disconnected" on valid device selection** — PortAudio fires `PaStreamFinishedCallback` on EVERY inactive transition, including intentional `stop()`/`close()` during monitor restarts (device switch) and page unmount. The unguarded `_level_stream_finished` callback emitted a bogus `device_lost` push on every selection change; the renderer toast + `deviceLostStore` paused the meter and showed "Selected microphone disconnected". Fix: identity-aware finished-callback (`_make_stream_finished_guard(stream_cell)`) — only reports loss when the FINISHING stream is still the CURRENT active monitor stream.
2. **Mic test records 10 s but delivers nothing + 15 s IPC timeout** — `stop_test_recording` returned BOTH WAVs base64 (~0.88 MB each → ~2.4 MB total) in one response frame; the deliberate 1 MiB outbound frame cap (`_TCP_MAX_OUTBOUND_BYTES`) silently dropped it ("outbound TCP frame exceeds ... dropping"). Fix: file-reference transport — stop persists both WAVs under `<config>/mic-test-recordings/`, returns small {"path","bytes"} refs; new chunked IPC command `microphone_test_read_audio` (≤256 KiB binary/slice, recordings-dir containment enforced); renderer assembles chunks into the existing playback data-URI flow. Keep-only-latest purge on next test start.
3. **Duplicate timers + noisy quality line** — Stop button showed "(Ns)" countdown while LiveQualityFeedback ALSO showed "Recording MM:SS / MM:SS" plus a flickering voice-quality status line duplicating the live LevelBar. Fix: one timer readout only; voice-quality line removed (component stripped to timer); Stop button plain label; unused i18n keys removed across all 8 locales.
4. **Consent** — untouched by design; point-of-use gate still fires only when consent missing.

### Changes
- Backend: `level_monitor/monitoring.py` (identity guard), `level_monitor/test_recording.py` (disk transport + purge + `read_test_recording_slice`), `service/microphone_test.py` (transcription reads persisted WAV directly), `handlers/microphone_test_handlers.py` (+`_handle_microphone_test_read_audio`), `ipc/registry.py` + `ipc/rate_limiter.py` (+command), `providers.py` (ServiceProtocol method).
- Hosts: `client/src/main/allowed-commands.ts`, `src-tauri/src/commands/sidecar_cmds/allowlist.rs` (+1 each, snapshots updated).
- Frontend: `pages/microphone/lib/types.ts` (`TestStopResult.audio_file/raw_audio_file`, `TestAudioChunk`), `useMicrophoneTestSession.ts` (chunked fetch + cache write-through), `ActiveMicrophoneCard.tsx` / `Microphone.tsx` (peak/countdown prop removal), `LiveQualityFeedback.tsx` (timer only), i18n ×8 locales.
- Docs/tests: ADR-0020 §16 addendum, SECURITY.md/FEATURES.md/CHANGELOG.md/CONTRIBUTING.md/docs/ipc-reference.md count reconciliation; tests updated (`test_level_monitor.py`, `test_mic_test_degradation.py`, `test_g_perf_reliability.py`, `test_ipc_dispatch_errors.py` +2 classes, `test_ipc_server_lifecycle_fixes.py`, doc-count suites) + new `tests/test_mic_page_selection_and_test_transport.py` (12 tests).

### Validation performed (Windows 11 host)
- Full vitest: 3522 passed / 33 skipped. Full pytest (-n auto): 13889 passed, 884 skipped, **2 pre-existing failures unrelated to this scope** (`test_platform.py::TestLinuxDesktopExec` + `test_ruff_ratchet` E501s) — all traced to files modified by OTHER uncommitted sessions (autostart_windows.py, shutdown_controller, startup_sequence*, config_wiring); ownership rules forbid touching them.
- cargo check: Finished; sidecar_cmds_tests 20 passed (allowlist snapshot bumped 69→70).
- ruff/pyrefly clean on all touched files.

### Known limitations
- Pre-existing dirty-tree failures above remain (other sessions' work).


## 2026-XX (3rd) Mic-test post-recording pipeline: rate-limit, timer, playback integrity, honest metrics

### Root causes
1. **Rate-limit burst after auto-stop** — `COMMAND_COSTS["microphone_test_read_audio"]=30` (my prior misread of the cost map as a per-command allowance; it is a COST against the SHARED 200/s burst + 600/10s budgets). ~8 cheap slice reads consumed the whole burst window; tail chunks rejected ("rate limit hit (N rejected)", N cumulative) → fetch threw → audio null → TestReviewPanel gated on audio refs → panel never rendered → "returns to Start Test". Manual stop at ~7s produced fewer slices, squeaking under budget — hence the manual/auto asymmetry. Fix: cost 1 with invariant comment.
2. **Frozen `Recording... 00:00 / 00:00`** — TWO stacked bugs: (a) dep-driven cleanup effect `[testRunning]` re-ran its PREVIOUS closure on the false→true commit and cleared the CURRENT interval refs created by startTest one tick earlier; (b) LQF totalSeconds was fed `testDurationMs/1000`, which is 0 during recording (set only at completion). Fixes: single lifecycle-synced interval (drives visible elapsed + grace-period safety-net stop only), unmount-only teardown ([] deps) guarded by an internal synchronous `recordingActiveRef` (prop-ref identity proved unstable under inline-args test renders), totalSeconds = fixed MICROPHONE_TEST_DURATION_SEC.
3. **Playback red toasts even when result existed** — base64 chunk fragments joined verbatim; default slice size 256*1024 (%3==1) injected mid-stream "=" padding from independent per-slice encodings → corrupted WAV decode → both Play buttons failed. Fix: server clamps interior slices to 3-byte multiples (final fragment keeps natural padding).
4. **Fabricated metrics without model** — service set `transcription_unavailable` ONLY when models!=None but engine missing; `models==None` (no model subsystem) produced NO marker → frontend showed numeric "Estimated Transcription Quality 0%" derived from absent data. Fix: marker set on all non-transcribable paths; frontend renders N/A row when flagged; audio-derived analysis untouched.

### Changes
- Backend: `ipc/rate_limiter.py` (cost 1), `level_monitor/test_recording.py` (3-byte-aligned slices + explicit raw=/filtered= log wording), `service/microphone_test.py` (models==None → marker), new tests in `tests/test_mic_page_selection_and_test_transport.py` (join-safety round-trip, cost guard, no-model contract).
- Frontend: `useMicrophoneTestSession.ts` (single timer driving elapsed + grace fallback stop; unmount-only teardown w/ internal flag; single-flight `_inFlightAudioFetches` dedupe; benign "No test running" no-op; countdown state removed as dead code), `ActiveMicrophoneCard.tsx` (fixed totalSeconds; container prop trim), `TestReviewPanel.tsx` (N/A gating), composition hook passthrough trim, i18n ×8 (`microphoneTest.qualityNotApplicable`), test updates incl. frozen-timer regression (fake timers), stale-trigger no-op test, single-flight coverage via collapsed concurrent stops.

### Validation
- Full vitest: 3524 passed / 33 skipped. Targeted backend mic/IPC suites: 194 passed. ruff+pyrefly clean on touched files. typecheck:ci clean.

### Unrelated working-tree failures intentionally ignored
Other agent's uncommitted churn (~180 files): autostart/shutdown/startup-sequence E501 ruff-ratchet fails, `test_platform::TestLinuxDesktopExec` failure, bubble-handlers/global-shortcuts typecheck breakage. None touch this task's files.
