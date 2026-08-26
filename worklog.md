# worklog.md

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
