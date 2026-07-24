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
 *   - ALLOWED_COMMANDS re-export (canonical declaration lives in
 *     `./allowed-commands.ts`; this file re-exports it so existing
 *     imports like `send-to-python.ts` keep working).
 *   - Security-warning suppression.
 *   - `app.setAppUserModelId("VoiceTyper")`.
 *   - `acquireSingleInstanceLock()` + `registerIpcHandlers()` calls.
 *   - `app.whenReady()` → `bootstrapRuntime()` + VT_BUBBLE_TEST + `startPython()`.
 *   - `app.on("before-quit" | "will-quit" | "window-all-closed" | "activate", …)`.
 */
import { app } from "electron";
import { bootstrapRuntime } from "./bootstrap";
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

// CR-063: the canonical ALLOWED_COMMANDS declaration lives in
// `./allowed-commands` (a dependency-free leaf module). It is
// re-exported here so existing imports — notably
// `./python/send-to-python.ts` imports `ALLOWED_COMMANDS` from
// `../index` — keep working without introducing a circular
// dependency (allowed-commands.ts imports nothing from this file).
export { ALLOWED_COMMANDS } from "./allowed-commands";

// Suppress Electron's built-in security-warning console spam in dev mode
// (the "Insecure Content-Security-Policy" message about unsafe-eval).
// Vite dev mode needs unsafe-eval for sourcemaps — this is expected.
if (process.env.npm_lifecycle_event === "dev" || !app.isPackaged) {
	process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = "true";
}

// CR-063: the canonical ALLOWED_COMMANDS declaration lives in
// `./allowed-commands.ts` (see top-of-file re-export). The inline
// duplicate declaration that used to live here was removed to
// eliminate the risk of the two declarations drifting out of sync
// (the inline copy was previously missing 3 entries that the
// canonical copy had, and vice-versa for other entries).

try {
	// Best-effort — only matters on Windows 7+.
	app.setAppUserModelId("VoiceTyper");
} catch (e) {
	// setAppUserModelId can throw on non-Windows or if the registry
	// write fails; non-fatal — Windows taskbar grouping falls back
	// to the default (app.exe name) which is acceptable.
	console.warn("[main] setAppUserModelId failed (non-fatal):", e);
}

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

// GT-11: SIGTERM/SIGINT → app.quit() → before-quit → stopPython().
// 3s hard backstop if before-quit hangs.
let _signalQuitFired = false;
const signalQuitHandler = () => {
	if (_signalQuitFired) return;
	_signalQuitFired = true;
	try {
		app.quit();
	} catch (e) {
		console.warn("[main] app.quit() from signal handler failed:", e);
		process.exit(0);
	}
	setTimeout(() => process.exit(0), 3000).unref();
};
process.on("SIGTERM", signalQuitHandler);
process.on("SIGINT", signalQuitHandler);

app.on("before-quit", () => {
	app.isQuitting = true;
	stopPython();
	// P1-1.4: clear our PID file so the next launch doesn't think
	// we're still alive.  Best-effort — if the disk is gone, the
	// stale-PID recovery path will handle it on next start.
	clearElectronPidFile();
});

// PVT-G5-005 (R6-F7): belt-and-suspenders `will-quit` handler.
// GT-71: removed the 3s forceExitTimer that raced with stopPython's
// killTimer. Now relies on killTimer (no longer .unref()'d) +
// pythonProcess.once('exit') to call app.exit(0).
// GT-60: if pythonProcess is already null, exit immediately.
let _willQuitStopPythonFired = false;
app.on("will-quit", (event) => {
	if (_willQuitStopPythonFired) return;
	_willQuitStopPythonFired = true;
	event.preventDefault();
	try {
		stopPython();
	} catch (err) {
		console.warn("[main] stopPython failed during will-quit:", err);
	}
	if (state.pythonProcess) {
		state.pythonProcess.once("exit", () => {
			app.exit(0);
		});
	} else {
		app.exit(0);
	}
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

// APP_NAME is re-exported here to preserve the public API surface
// (PVT-G5-086: the unused direct import was removed; this re-export
// remains so any external consumer importing from `./index` still
// resolves `APP_NAME`).
export { APP_NAME } from "./branding";
