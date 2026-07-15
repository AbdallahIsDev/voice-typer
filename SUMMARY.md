## Objective
- Close out Findings 11 and 3 from the frontend-arch audit with proper, verified fixes. Finding 3 COMPLETE (proper useT refactor). Finding 11 COMPLETE (option a + c combined, including a backend structural regression fix). XPLAT-01 COMPLETE — originally a downgrade, now fully re-implemented without any downgrade.

## Important Details
- Environment: Windows 11, Python 3.14.6, Node 24.13, pytest 9.0.3, no `uv`; editable install; frontend `node_modules` present.
- Role: started as read-only verifier, then authorized fixer for RW-15/17/18 + Finding 3 proper fix.
- PowerShell `python - <<'PY'` heredoc FAILS (ParserError) — use Edit tool or write a `.py` script file + `python script.py`.
- XPLAT-01 (`app.py._open_config_file`) was a confirmed DOWNGRADE; now fully re-implemented without downgrade: associations respected via ShellExecuteEx (handle-based wait), lock held for editor session, reload after close, and SystemRoot-validated Notepad fallback. See "Finding 3 / XPLAT-01" completion notes.
- `tsc -p tsconfig.web.json` clean; full frontend vitest suite GREEN (254 passed; one axe-core Onboarding timeout transient — passes standalone at 4.65s, 5/5).

## Work State
### Completed
- RW-15 FULLY RESOLVED: (a) model download→INFO, (b) Bluetooth HFP WARNING→INFO, (c) buffer-telemetry gated `VOICE_TYPER_VERBOSE`, (d) VAD auto-calibration `log.debug`→`log.info` + comment rewrite.
- RW-16 RESOLVED: `config.py:1381-1409` explicit imports.
- RW-17 RESOLVED: dead test-seam delegates gone; 5 intentional ARCH-REFAC-003 delegators confirmed live and left intact.
- RW-18 RESOLVED: `StartupSequence.run()` calls `startup_tasks` directly; removed ALL stale "facade is kept for test seams" comments.
- `comprehensive-review.md`: RW-05/RW-07/RW-08 RESOLVED + Finding 3 marked RESOLVED with Status block.
- Server verification: `py_compile` clean; VAD 7 passed; startup/autostart 73 passed; 2 `test_autostart_launcher.py` failures PRE-EXISTING (present on git stash baseline — independent `launch()` bug).
- Finding 11 RE-INVESTIGATION + FULL FIX COMPLETE:
  - Prior investigation was STALE — `HistoryChangedEvent` already in `ipc.ts`, Home/Dashboard/History already subscribe to `history_changed`, and backend `delete/restore/clear/toggle_favorite` already broadcast it. The real blocker was a **structural regression** in `history_handlers.py`: the F11-FIX inserted `def _publish_history_changed` at column 0, which closed `HistoryHandlersMixin` early and orphaned 5 handler methods (`_handle_restore_history`, `_handle_clear_history`, `_handle_toggle_favorite`, `_handle_get_favorites`, `_handle_search_history`) as module-level nested funcs — breaking IPC dispatch via `getattr(self, name)`. **FIXED**: restored methods to class body; helper moved to module scope at end of file; `clear_history` now calls the shared helper (DRY). Verified: `py_compile` clean; `IPCServer._COMMAND_REGISTRY` resolves all 5 handlers at the class level (no `missing`).
  - `Settings.tsx`: added the missing `LastUpdatedIndicator` + `useLastUpdated` + manual refresh (`handleManualRefresh`) + `markUpdated()` on `config_changed` (it already had the `config_changed` listener + `RefreshIcon` import). Now all 6 cached pages show the indicator.
  - `Models.tsx`: added the missing `config_changed` listener that merges changed fields into `_cachedConfig` and recomputes the active model badge. (Models already had the indicator; it lacked any cache-invalidation path for config changes from other pages.)
  - `tsc` clean; ModelsPage (11) + Settings (4) tests pass; full frontend suite 254 passed (axe-core Onboarding timeout transient, passes standalone).
- XPLAT-01 FULL RE-IMPLEMENTATION COMPLETE (was a downgrade; now clean):
  - `app.py` `_open_config_file` Windows branch now opens the user's default editor via `ShellExecuteEx` (`SEE_MASK_NOCLOSEPROCESS`) which yields a process handle, so it **blocks until the editor exits and reloads afterward** — same guarantees as macOS `open -W` / Linux `xdg-open`. No `os.startfile` (which returns immediately with no handle and caused the regressions).
  - `_config_mutation_lock` held for the entire editor session → TOCTOU race (SEC-audit-011) closed.
  - Auto-reload-after-editor-close restored (reload runs after the handle signals exit, not on launch).
  - When no `.json` handler is associated, falls back to the **SystemRoot-validated** `%SYSTEMROOT%\System32\notepad.exe` (existence-checked; `C:\Windows\System32\notepad.exe` as verified fallback) — never a bare PATH/cwd-resolved `notepad`. `os.startfile` only a last resort.
  - Module-level helpers: `_windows_open_with_default_app`, `_windows_wait_for_process_exit`, `_windows_close_process_handle`, `_systemroot_notepad_path` (ctypes usage guarded, returns `None` off-Windows).
  - `py_compile` clean; `tests/test_b4_config_editor_lock.py` (9) + `tests/test_api_doc_accuracy.py::TestWindowsOpenConfigFile` (3) pass; `test_bugfix_regressions.py::test_open_config_file_holds_config_mutation_lock` passes. `comprehensive-review.md` XPLAT-01 marked fixed (no downgrade).
- Finding 3 PROPER FIX COMPLETE (was quick-fix + deferred LocaleProvider; now done):
  - `i18n/i18n.ts`: added `useSyncExternalStore`; `_localeSubscribers` set + `subscribeLocale`/`getLocaleSnapshot`/`useT()` hook; `setLocale` now notifies subscribers + persists `localStorage["voice-typer-ui-locale"]`.
  - `App.tsx`: imports `useT`, `const t = useT();` at root (cascades re-render to whole tree).
  - All 7 memoized settings sections (General/AiEnhancement/Audio/Model/Privacy/Theme/Recording): import `useT`, `const t = useT();` before `if (!config)` guard.
  - `GeneralSettingsSection.tsx`: removed `window.location.reload()` + stale B-REVIEW-3 workaround comment; updated header comment. (Its redundant `localStorage.setItem` + TRAY push in the switcher remain — harmless.)
  - `GeneralSettingsSection.test.tsx`: updated header comment + wrapped `setLocale` calls in `act()` — 3 tests pass, no act warnings.
  - `tsc` clean; full frontend suite 255 passed.

### Active
- (none — Finding 3 and Finding 11 both finished)

### Blocked / Pending user decision
- Separate-audit findings (`telemetry`, `change_hotkey`, `get_stats`) unverified — audit file not provided.
- Deferred R8 items still open: RW-9/RW-04 god-class, RW-0 vitest, RW-4/RW-5 installers, RW-01 keyring, RW-02 Playwright, RW-03 JSON logging, RW-08 meta-test triage, NEW-IPC-007 (`usePython` swallows server `type:"error"`), pre-existing `ctypes.WINFUNCTYPE` Linux failure (crash_handler.py:321).

## Next Move
1. Report done — RW-15..18 closed, Finding 3 properly fixed, Finding 11 fully fixed + verified, XPLAT-01 fully re-implemented without downgrade.

## Relevant Files
- voice_typer/server/recording.py:1162 (RW-15(d)), 1662 (INFO), 2685 (gated telemetry).
- voice_typer/server/startup_sequence.py (330-434) + service.py:871 + startup_tasks.py:11-13: RW-18 cleanup.
- comprehensive-review.md: RW-05/RW-07/RW-08 RESOLVED; Finding 3 RESOLVED; Finding 11 RESOLVED (option a + c) + Status.
- voice_typer/client/src/renderer/src/i18n/i18n.ts: `useT` + `subscribeLocale` + `setLocale` notify (Finding 3).
- voice_typer/client/src/renderer/src/App.tsx: `useT` at root (Finding 3).
- voice_typer/client/src/renderer/src/components/settings/{General,AiEnhancement,Audio,Model,Privacy,Theme,Recording}SettingsSection.tsx: `useT` subscription (Finding 3); GeneralSettingsSection reload removed.
- voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.test.tsx: act() wrapped, 3 pass.
- voice_typer/server/handlers/history_handlers.py: F11 backend structural regression FIXED — all 8 handlers now class methods; `_publish_history_changed` module-level helper; broadcasts from delete/restore/clear/toggle_favorite.
- voice_typer/client/src/renderer/src/types/ipc.ts: `HistoryChangedEvent` already in PushEvent union.
- voice_typer/client/src/renderer/src/pages/Settings.tsx: added `LastUpdatedIndicator` + `useLastUpdated` + `handleManualRefresh` + `markUpdated()` on `config_changed` (Finding 11).
- voice_typer/client/src/renderer/src/pages/Models.tsx: added `config_changed` listener to invalidate `_cachedConfig` (Finding 11).
- voice_typer/server/app.py (`_open_config_file`): XPLAT-01 fully re-implemented (ShellExecuteEx handle-wait + validated Notepad fallback + lock for session + reload after close); helpers at module level.
