/**
 * Dashboard (main) BrowserWindow creation + helpers.
 *
 * Extracted from `index.ts` (REF-2). Owns:
 *   - `createMainWindow(forceShow?)` — lazy-creates the dashboard window.
 *   - `showMainWindow()` — show + focus the dashboard, creating it if needed.
 *     Used by the `second-instance` event and the tray "Open app" path.
 *   - `broadcastMaximized(bool)` — fans out `window:maximized-changed` to
 *     every open BrowserWindow (used by the main window's maximize /
 *     unmaximize event listeners).
 *   - `_nativeThemeHandler` + `registerNativeThemeListener()` — R6-F3:
 *     the `nativeTheme.on("updated", ...)` listener is registered ONCE
 *     at module load and cleaned up when the main window is destroyed.
 *
 * The window-creation concerns live in sibling leaves; this module stays
 * the flat orchestrator + public surface:
 *   - `window-chrome.ts`      — ctor options (platform chrome / SEC-014 webPreferences).
 *   - `window-events.ts`      — close/show/maximize/fullscreen/closed wiring.
 *   - `renderer-telemetry.ts` — console-message forwarder + PII-redacted error persistence.
 *   - `renderer-recovery.ts`  — did-fail-load retry / render-process-gone / preload-error.
 *   - `input-nav-guard.ts`    — before-input-event DevTools/F11 gate + window-open deny.
 */
import path from "node:path";
import { BrowserWindow, Menu } from "electron";
import { START_HIDDEN } from "../constants";
import { WindowChannels } from "../ipc/channels";
import { log } from "../logging";
import { state } from "../state";
// The concerns below were previously inlined in this file. They
// are now in sibling modules so the window-creation logic in
// `createMainWindow()` reads as a flat sequence of "register listener →
// create window → attach handlers" without interleaved definitions of
// the crash-storm tracker / theme listener / renderer-error sink.
// Re-exported below for backward compat with existing import sites
// (notably `tcp-connect.ts`, `handle-message.ts`, `windows/index.ts`,
// and the test files `crash-storm-recovery.test.ts` /
// `main-window-native-theme.test.ts`).
import {
	_resetRenderCrashTrackingForTest as _resetRenderCrashTrackingForTestImpl,
	recordBubbleRenderCrash as recordBubbleRenderCrashImpl,
} from "./crash-storm";
import {
	installWindowOpenHandler,
	registerInputNavGuard,
} from "./input-nav-guard";
import { registerRendererRecovery } from "./renderer-recovery";
import { registerRendererTelemetry } from "./renderer-telemetry";
import {
	_nativeThemeListenerRegistered as _nativeThemeListenerRegisteredImpl,
	_resetNativeThemeListenerForTest as _resetNativeThemeListenerForTestImpl,
	registerNativeThemeListener as registerNativeThemeListenerImpl,
} from "./theme-listener";
import { buildMainWindowOptions } from "./window-chrome";
import { registerWindowLifecycleEvents } from "./window-events";

// Backward-compat re-exports. External consumers (and tests) import
// these names from `./main-window`; the implementations now live in the
// sibling modules above. Re-exporting here means no caller needs to
// change its import path.
export const registerNativeThemeListener = registerNativeThemeListenerImpl;
export const _resetNativeThemeListenerForTest =
	_resetNativeThemeListenerForTestImpl;
export const _nativeThemeListenerRegistered =
	_nativeThemeListenerRegisteredImpl;
export const recordBubbleRenderCrash = recordBubbleRenderCrashImpl;
export const _resetRenderCrashTrackingForTest =
	_resetRenderCrashTrackingForTestImpl;

/**
 * Show + focus the dashboard window, creating it if needed.
 *
 * Used by:
 *   • second-instance event  (Start Menu / Desktop click while running)
 *   • tray "Open app" IPC path (see showMainWindow IPC handler below)
 */
export function showMainWindow(): void {
	if (!state.mainWindow || state.mainWindow.isDestroyed()) {
		createMainWindow(/* forceShow */ true);
		return;
	}
	const win = state.mainWindow;
	if (win.isMinimized()) {
		win.restore();
	}
	if (!win.isVisible()) {
		win.show();
	}
	// RAISE-TO-FRONT: `focus()` alone does NOT bring the window above
	// other applications' windows. Every OS enforces a foreground lock
	// (Windows: SetForegroundWindow from a background process is
	// refused and the taskbar button merely flashes; macOS/Linux window
	// managers behave similarly) — so a tray click left the dashboard
	// visible but buried at the bottom of the z-order. The standard
	// cross-platform workaround is a momentary always-on-top raise:
	// lift the window above everything, focus it, then immediately
	// drop the flag so normal z-order behavior resumes. `moveTop()`
	// additionally raises the z-order without stealing focus on
	// platforms where the WM refuses the focus steal.
	win.setAlwaysOnTop(true, "screen-saver");
	win.show();
	win.focus();
	win.moveTop();
	win.setAlwaysOnTop(false);
	log.info(
		"[MAIN] Dashboard window shown + raised to front (show_window request)",
	);
}

/**
 * Fan out `window:maximized-changed` to every open BrowserWindow so the
 * custom title bar's maximize/restore button stays in sync.
 */
export function broadcastMaximized(maximized: boolean): void {
	BrowserWindow.getAllWindows().forEach((win) => {
		win.webContents.send(WindowChannels.maximizedChanged, maximized);
	});
}

/**
 * Broadcast helper refactor: explicit broadcast helper for the main
 * window. Replaces the
 * previous `webContents.send` monkey-patch that intercepted outbound
 * `python-event` messages. Centralizes the  `pythonReady` flip on
 * the first `{ type: "ready" }` push and the destroyed-window guard.
 */
export function broadcastToMainWindow(channel: string, msg: unknown): void {
	if (
		!state.pythonReady &&
		channel === "python-event" &&
		typeof msg === "object" &&
		msg !== null &&
		(msg as Record<string, unknown>).type === "ready"
	) {
		state.pythonReady = true;
		log.info(
			"[STARTUP] backend sent {type:'ready'} — pythonReady = true (backend fully initialized)",
		);
	}
	if (state.mainWindow && !state.mainWindow.isDestroyed()) {
		state.mainWindow.webContents.send(channel, msg);
	}
}

/**
 * R6-F3: the `nativeTheme.on("updated", ...)` listener is registered ONCE
 * at module load (see `registerNativeThemeListener()` in
 * `./theme-listener.ts`) instead of being re-registered inside
 * `createMainWindow()` on every window recreation. Previously each call
 * to `createMainWindow()` added a NEW listener to `nativeTheme` without
 * ever removing the previous one — so after N window recreations
 * (dev-mode `relaunchApp()` + tray "Restart"), there were N listeners
 * all firing on every theme change, each holding a stale reference to a
 * destroyed BrowserWindow.
 *
 * The single module-level handler (in `./theme-listener.ts`) reads
 * `state.mainWindow` live (so it always operates on the current window)
 * and is removed once via `nativeTheme.off(...)` when the window is
 * destroyed (in case the module is hot-reloaded in dev — in production
 * the listener lives for the process lifetime, which is correct since
 * `state.mainWindow` is the canonical window reference).
 */

export function createMainWindow(forceShow = false): void {
	if (state.mainWindow) return;
	//do NOT set `state.pythonReady = true` here.
	//
	// The BrowserWindow is created on the first successful TCP
	// connect (see tcp-connect.ts:59 `createWindows()`), but "TCP
	// alive" only means the Python IPC server has bound its port
	// and accepted our auth line. The backend's heavier subsystems
	// — model load, history DB schema init, tray, hotkey
	// registration, dictation pipeline — may still be
	// initializing on the Python side. Treating that as
	// "pythonReady" masked real backend-startup failures: if
	// Python crashed *after* TCP accept but *before* finishing
	// init, start-python.ts's exit handler saw `pythonReady ===
	// true` and classified the crash as a "real crash during
	// operation" instead of the more accurate "early exit during
	// backend init".
	//
	// We instead defer `state.pythonReady = true` until the
	// backend sends its `{"type":"ready"}` push event (emitted by
	// ipc_server.py:2557 right before `app.start()` enters the
	// tray event loop). The interceptor installed below catches
	// that event the first time the main process broadcasts it
	// to the renderer via `webContents.send("python-event", msg)`
	// (see handle-message.ts:115) and flips the flag.
	//
	// The distinction matters for start-python.ts's exit
	// handler: if Python exits before sending `ready`, it's an
	// "early exit" (clear single-instance / setup-failure dialog);
	// after `ready`, it's a "crash" (silent quit). This matches
	// the user's mental model and surfaces real init failures
	// instead of swallowing them.

	// When autostarted hidden, the window is created but not shown — the
	// React app still boots (so opening it later is instant) while staying
	// off the taskbar.  forceShow overrides this (second-instance / tray
	// open) so the window appears immediately.
	const shouldShow = forceShow || !START_HIDDEN;

	state.mainWindow = new BrowserWindow(buildMainWindowOptions(shouldShow));

	// Broadcast helper refactor: the previous `webContents.send` monkey-patch has been
	// replaced with the explicit `broadcastToMainWindow(channel, msg)`
	// helper (see the export above). Callers in handle-message.ts and
	// tcp-connect.ts now route their `python-event` broadcasts through
	//that helper, which centralizes the  `pythonReady` flip and
	// the destroyed-window guard.

	// R6-F3 (session-3): register the module-level nativeTheme
	// listener once. Previously this was an inline
	// `nativeTheme.on("updated", ...)` inside `createMainWindow()`,
	// which leaked a new listener per window recreation (dev-mode
	// `relaunchApp()` + tray "Restart").
	// `registerNativeThemeListener()` is idempotent — the second
	// call is a no-op, so subsequent `createMainWindow()`
	// invocations don't stack duplicate handlers.
	//
	// Note: the inline `nativeTheme.on("updated", ...)` listener
	// that was previously here has been REMOVED — it is now
	// registered inside `registerNativeThemeListener()` to prevent
	// the per-recreation leak. Keeping both would register TWO
	// listeners (the idempotent one + the leaked inline one).
	registerNativeThemeListener();

	Menu.setApplicationMenu(null);

	registerWindowLifecycleEvents(
		state.mainWindow,
		shouldShow,
		broadcastMaximized,
	);
	registerRendererTelemetry(state.mainWindow);
	registerRendererRecovery(state.mainWindow);
	registerInputNavGuard(state.mainWindow);
	installWindowOpenHandler(state.mainWindow);

	if (process.env.ELECTRON_RENDERER_URL) {
		// void + .catch: loadURL returns a Promise that rejects on
		// load failure (dev server 500, malformed URL, network
		// unreachable). Without .catch the rejection would bubble
		// up as an unhandled-rejection and feed the SEC-021 breaker
		// (which trips on repeated unhandled rejections and force-
		// quits the app). The `did-fail-load` handler above already
		// logs the failure with structured detail; this .catch only
		// suppresses the unhandled rejection so the breaker stays
		// calm. `void` discards the .catch's resolved value.
		void state.mainWindow
			.loadURL(process.env.ELECTRON_RENDERER_URL)
			.catch((e) => {
				log.warn("[MAIN] loadURL rejected:", e);
			});
	} else {
		void state.mainWindow
			.loadFile(path.join(__dirname, "../renderer/index.html"))
			.catch((e) => {
				log.warn("[MAIN] loadFile rejected:", e);
			});
	}
}
