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
