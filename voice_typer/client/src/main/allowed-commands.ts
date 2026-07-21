/**
 * SEC-019: IPC command allowlist (canonical declaration).
 *
 * R6-F10: previously this Set lived inline in `src/main/index.ts`.
 * `send-to-python.ts` imported it back from `../index`, creating a
 * circular dependency (`index.ts` → `python/` → `send-to-python.ts`
 * → `index.ts`). Importing a value from a module that itself imports
 * the importer forces Node's CJS resolver to evaluate `index.ts`
 * partially before `sendToPython` is callable, which produced
 * hard-to-trace load-order bugs whenever `index.ts` was refactored.
 *
 * Moving the allowlist into its own dependency-free module breaks the
 * cycle: both `index.ts` and `send-to-python.ts` import from
 * `./allowed-commands`, and neither imports the other for this value.
 *
 * ERR-IPC-002 (fix): previously missing `quit_app` and `restart_app`,
 * which broke tray Quit/Restart (stopPython sends `quit_app`).
 * ERR-IPC-003 (fix): removed 5 dead/mismatched entries (`quit`,
 * `restart`, `save_config`, `save_vocabulary_with_diff`,
 * `complete_onboarding`) — none exist as server IPC commands.
 *
 * UX-23 (renderer bits): `repaste_last` was previously in the
 * ERR-IPC-003 "removed" list, but it IS a real app method
 * (`service.repaste_last` / `app.repaste_last`) — it was previously
 * invoked only via the tray hotkey callback, not as an IPC command.
 * Re-added here so the renderer's "Re-paste" button can call it.
 * NOTE: a server-side `_handle_repaste_last` handler still needs to
 * be registered in `_COMMAND_REGISTRY` (ipc_server.py) for the call
 * to succeed; until then the renderer call will surface an "unknown
 * command" error toast (handled gracefully by Home.tsx).
 *
 * PRESERVES the exact command strings — do not rename, reorder, or
 * deduplicate without coordinating with the Python-side
 * `tests/test_electron_ipc_and_build.py::test_allowlist_matches_server_commands`
 * test which used to slice this substring out of `index.ts`. After
 * R6-F10 the test should look for the literal `ALLOWED_COMMANDS = new Set([`
 * substring in this file instead. See `tests/test_electron_ipc_and_build.py`
 * docstring (I7 owns the Python side — coordination note left there).
 */
export const ALLOWED_COMMANDS = new Set<string>([
	"get_status",
	"toggle_dictation",
	"undo_last",
	"get_config",
	"get_defaults",
	"set_config",
	"get_history",
	"search_history",
	"get_today_stats",
	"delete_history",
	"restore_history",
	"clear_history",
	"toggle_favorite",
	"get_favorites",
	"get_microphones",
	"restart_app",
	"quit_app",
	"get_templates",
	"save_templates",
	"get_volume_backend_status",
	"get_model_status",
	// ADR-0009 Issue 3: prewarm cache status (Hot/Partial/Cold,
	// cache ratio, last-run timestamp) for the About page.
	"get_prewarm_status",
	// Task 3: manually trigger a prewarm run (force=True) from the
	// About page's "Run Prewarm Now" button.
	"run_prewarm",
	// Task 2: open the prewarm log file in the OS default text
	// editor from the About page's "View prewarm log" button.
	"open_prewarm_log",
	"get_vocabulary",
	"save_vocabulary",
	"onboarding_is_first_run",
	"onboarding_start",
	"onboarding_get_step",
	"onboarding_next_step",
	"onboarding_prev_step",
	"onboarding_set_microphone",
	"onboarding_set_hotkey",
	"onboarding_set_model",
	"onboarding_skip",
	"onboarding_apply",
	"onboarding_get_microphones",
	"onboarding_get_model_options",
	"onboarding_get_hotkey_presets",
	"download_model",
	// NEW-PRIV-011: allow cancel_model_download so the renderer can
	// cancel an in-progress HuggingFace download.
	"cancel_model_download",
	// NEW-PAUSE-001: allow pause/resume so the renderer can pause
	// and resume in-progress model downloads from the Models page.
	"pause_model_download",
	"resume_model_download",
	// NEW-DEAD-015: allow test_llm_connection so the renderer can
	// wire up a "Test connection" button on the Settings page.
	"test_llm_connection",
	// NEW-UX-005: allow delete_model so the renderer can actually
	// delete model files from disk (not just remove from UI list).
	"delete_model",
	// NEW-MODEL-001: allow get_model_catalog so the Models page can
	// fetch the available model catalog from the backend.
	"get_model_catalog",
	// Microphone test commands
	"microphone_test_start",
	"microphone_test_stop",
	"microphone_test_cancel",
	"microphone_test_status",
	"microphone_test_get_level",
	// Continuous level monitor
	"level_monitor_start",
	"level_monitor_stop",
	"level_monitor_status",
	// ESC-FIX-001: pause/resume the global ESC cancel hotkey so the
	// frontend (HotkeyPicker in hotkey capture mode) can temporarily
	// disable it, preventing the backend from processing Escape while
	// the UI is capturing a custom hotkey.
	"set_esc_cancel_paused",
	// TRAY-008: allow set_tray_locale so tray labels update when the
	// user changes the UI language in Settings.
	"set_tray_locale",
	// MODEL-IMPORT: allow import_model so the Models page can scan
	// and import pre-downloaded models from a local directory.
	"import_model",
	// RW-10: allow heartbeat so the main process can
	// prove to the Python backend that Electron is
	// still alive.  The backend's watchdog daemon
	// thread calls app.quit() if 3 consecutive
	// heartbeats are missed.
	"heartbeat",
	// PERF-005: ack that Electron received+is processing relaunch_electron
	"relaunch_ack",
	// UX-23 (renderer bits): repaste_last is a server-side app method
	// (service.repaste_last / app.repaste_last) currently wired to a
	// tray hotkey. Adding it to the IPC allowlist so the renderer's
	// "Re-paste" button (Home.tsx) can invoke it via call(). The
	// backend handler is added separately (tracked in the IPC
	// _COMMAND_REGISTRY); until then the renderer call will return
	// an "unknown command" error which the UI surfaces as a toast.
	"repaste_last",
	// d-review Finding 2: 10 server commands previously missing
	// from the allowlist — renderer calls silently rejected.
	"refresh_microphones",
	"get_rms_level",
	"get_audio_status",
	"export_diagnostics",
	"check_accessibility",
	"show_electron_notification",
	"get_vocabulary_suggestions",
	"apply_vocabulary_suggestion",
	"dismiss_vocabulary_suggestion",
	"force_cancel_transcription",
]);
