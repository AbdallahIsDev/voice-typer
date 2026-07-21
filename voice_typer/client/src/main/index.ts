/**
 * Electron main-process entry point.
 *
 * REF-2: this file is now wiring-only (≤300 lines). All cohesive
 * function groups have been extracted into focused modules:
 *
 *   - `./state`             — shared mutable state (pythonProcess, tcpSocket,
 *                             mainWindow, bubbleWindow, pendingRequests, …)
 *   - `./logging`           — `ts()`, `cleanConsoleMsg()`, ANSI color constants
 *   - `./constants`         — `IPC_PORT`, `IPC_TOKEN`, `START_HIDDEN`,
 *                             `BUBBLE_WIDTH`, `BUBBLE_HEIGHT`, `HEARTBEAT_INTERVAL_MS`
 *   - `./single_instance`   — `computeConfigDir`, `electronPidFile*`,
 *                             `acquireSingleInstanceLock` (+ `app.on("second-instance")`)
 *   - `./windows/`          — `createMainWindow`, `createBubbleWindow`,
 *                             `showBubbleWindow`, `hideBubbleWindow`,
 *                             `centerOnPrimaryDisplay`, `showMainWindow`,
 *                             `createWindows` aggregator
 *   - `./python/`           — `pythonArgs`, `startPython`, `stopPython`,
 *                             `tcpConnect`, `sendToPython`, `handleMessage`,
 *                             `relaunchApp`
 *   - `./ipc/`              — `registerIpcHandlers()` (window controls, config
 *                             export, bubble IPC, python-call bridge)
 *   - `./bootstrap`         — `bootstrapRuntime()` (sessionNonce, userData,
 *                             CSP, error handlers)
 *
 * What stays here:
 *   - ALLOWED_COMMANDS set (canonical declaration — the Python
 *     `test_allowlist_matches_server_commands` test and the vitest
 *     Section 13 port slice the literal allowlist Set declaration
 *     substring from this file).
 *   - Security-warning suppression.
 *   - `app.setAppUserModelId("VoiceTyper")`.
 *   - `acquireSingleInstanceLock()` + `registerIpcHandlers()` calls.
 *   - `app.whenReady()` → `bootstrapRuntime()` + VT_BUBBLE_TEST + `startPython()`.
 *   - `app.on("before-quit" | "window-all-closed" | "activate", …)`.
 */
import { app } from "electron";
import { bootstrapRuntime } from "./bootstrap";
import { APP_NAME } from "./branding";
import { registerIpcHandlers } from "./ipc";
import { BUBBLE_CLR, RESET, ts } from "./logging";
import { startPython, stopPython } from "./python";
import {
	acquireSingleInstanceLock,
	clearElectronPidFile,
} from "./single_instance";
import { state } from "./state";
import { isLinuxWaylandWithoutSni } from "./tray_available";
import { createWindows, showBubbleWindow, showMainWindow } from "./windows";

// Suppress Electron's built-in security-warning console spam in dev mode
// (the "Insecure Content-Security-Policy" message about unsafe-eval).
// Vite dev mode needs unsafe-eval for sourcemaps — this is expected.
if (process.env.npm_lifecycle_event === "dev" || !app.isPackaged) {
	process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = "true";
}

// SEC-019: IPC command allowlist. The canonical declaration lives here
// (in `src/main/index.ts`) so that the Python `test_allowlist_matches_server_commands`
// test and the vitest Section 13 port can slice the literal allowlist Set
// declaration substring from this file's source.
// `sendToPython` (in `./python/send-to-python.ts`) imports this set at runtime.
//
// ERR-IPC-002 (fix): previously missing `quit_app` and `restart_app`,
// which broke tray Quit/Restart (stopPython sends `quit_app`).
// ERR-IPC-003 (fix): removed 5 dead/mismatched entries (`quit`,
// `restart`, `save_config`, `save_vocabulary_with_diff`,
// `complete_onboarding`) — none exist as server IPC commands.
//
// UX-23 (renderer bits): `repaste_last` was previously in the
// ERR-IPC-003 "removed" list, but it IS a real app method
// (`service.repaste_last` / `app.repaste_last`) — it was previously
// invoked only via the tray hotkey callback, not as an IPC command.
// Re-added here so the renderer's "Re-paste" button can call it.
// NOTE: a server-side `_handle_repaste_last` handler still needs to
// be registered in `_COMMAND_REGISTRY` (ipc_server.py) for the call
// to succeed; until then the renderer call will surface an "unknown
// command" error toast (handled gracefully by Home.tsx).
export const ALLOWED_COMMANDS = new Set([
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
	// CR-35: 3 commands were missing from the allowlist, causing the
	// Electron path to silently reject renderer calls (the Tauri path
	// has no allowlist and worked). Added to match _COMMAND_REGISTRY.
	"onboarding_check_permissions",
	"onboarding_get_model_catalog",
	"tray_click",
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

try {
	// Best-effort — only matters on Windows 7+.
	app.setAppUserModelId("VoiceTyper");
} catch {}

// Single-instance gate + `app.on("second-instance")` handler. Must run
// before `app.whenReady()` — the lock is checked at process start.
// On a duplicate launch it calls `app.exit(0)`; on the primary instance
// it writes the PID file and registers the second-instance → showMainWindow
// handler. See `./single_instance.ts` for the stale-PID recovery path.
acquireSingleInstanceLock();

// Register every `ipcMain.on` / `ipcMain.handle` listener (window
// controls, bubble IPC, config/history/vocabulary/templates export,
// python-call bridge). See `./ipc/`.
registerIpcHandlers();

// Tracks a genuine quit (tray Quit / Cmd+Q) so the close-to-tray handler
// on the window knows to let the close proceed instead of hiding.
app.isQuitting = false;

app.whenReady().then(() => {
	// SEC-029 nonce, NEW-PRIV-010 userData, SEC-012 CSP, SEC-021 error handlers.
	bootstrapRuntime();

	if (process.env.VT_BUBBLE_TEST === "1") {
		console.warn(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] VT_BUBBLE_TEST=1 -- showing bubble for diagnostics${RESET}`,
		);
		setTimeout(() => {
			showBubbleWindow();
			const id = setInterval(() => {
				const rms = 0.05 + 0.4 * Math.abs(Math.sin(Date.now() / 200));
				state.bubbleWindow?.webContents.send("bubble:level", {
					rms,
					peak: rms * 1.5,
				});
			}, 100);
			setTimeout(() => clearInterval(id), 10_000);
		}, 1500);
	}
	startPython();
	// CR-20: pre-warm the Wayland-without-SNI cache so the
	// `window-all-closed` handler returns instantly instead of
	// blocking on the D-Bus subprocess check (up to 4s worst-case
	// if neither `gdbus` nor `dbus-send` is installed). The check
	// is ~1ms on a warm session bus; running it here (after
	// startPython spawns the backend, before any window events
	// fire) keeps quit-path latency at zero.
	isLinuxWaylandWithoutSni();
});

app.on("before-quit", () => {
	app.isQuitting = true;
	stopPython();
	// P1-1.4: clear our PID file so the next launch doesn't think
	// we're still alive.  Best-effort — if the disk is gone, the
	// stale-PID recovery path will handle it on next start.
	clearElectronPidFile();
});

// With close-to-tray, closing the dashboard window just hides it — the
// process keeps running.  So window-all-closed only fires on a real quit
// (last window destroyed) or on macOS when all windows are closed by the
// user.  Guard accordingly.
//
// CR-20: on Linux Wayland WITHOUT StatusNotifierItem (Sway/Hyprland/dwl/
// river), the Python tray backend sets `_tray_unavailable = True` and
// creates NO tray icon.  Without a tray icon, the user has no UI
// affordance to quit the app after closing the last window.  Detect this
// case on the Electron side (mirroring tray.py::_is_linux_wayland_without_sni)
// and call `app.quit()` so the user isn't stranded.
app.on("window-all-closed", () => {
	if (app.isQuitting) return;
	if (process.platform !== "darwin") {
		// CR-20: if there's no tray icon to fall back to (Wayland-
		// without-SNI), quit instead of leaving the user stranded.
		if (isLinuxWaylandWithoutSni()) {
			app.quit();
			return;
		}
		// Don't quit: the tray icon + backend keep the app alive.  Quit only
		// happens explicitly via the tray menu.
	}
});

// macOS: clicking the dock icon when no windows are open should re-show
// the dashboard (mirrors second-instance on the other platforms).
app.on("activate", () => {
	if (!state.mainWindow || state.mainWindow.isDestroyed()) {
		createWindows(/* forceShow */ true);
	} else {
		showMainWindow();
	}
});

// APP_NAME is re-exported here to preserve the original lazy-import
// behaviour (the original `index.ts` imported it at line 1828, just
// before `app.whenReady()`). It's used by `./python/start-python.ts`
// (dialog.showErrorBox) and `./bootstrap.ts` (crash dialog).
export { APP_NAME } from "./branding";
