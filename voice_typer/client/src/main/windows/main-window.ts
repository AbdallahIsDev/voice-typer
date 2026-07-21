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
 */
import path from "node:path";
import { app, BrowserWindow, Menu, nativeTheme } from "electron";
import { START_HIDDEN } from "../constants";
import { cleanConsoleMsg, RENDERER_CLR, RESET, ts } from "../logging";
import { state } from "../state";

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
	if (!state.mainWindow.isVisible()) {
		state.mainWindow.show();
	}
	if (state.mainWindow.isMinimized()) {
		state.mainWindow.restore();
	}
	state.mainWindow.focus();
}

/**
 * Fan out `window:maximized-changed` to every open BrowserWindow so the
 * custom title bar's maximize/restore button stays in sync.
 */
export function broadcastMaximized(maximized: boolean): void {
	BrowserWindow.getAllWindows().forEach((win) => {
		win.webContents.send("window:maximized-changed", maximized);
	});
}

/**
 * R6-F3: the `nativeTheme.on("updated", ...)` listener is registered ONCE
 * at module load (see `registerNativeThemeListener()` below) instead of
 * being re-registered inside `createMainWindow()` on every window
 * recreation. Previously each call to `createMainWindow()` added a NEW
 * listener to `nativeTheme` without ever removing the previous one —
 * so after N window recreations (dev-mode `relaunchApp()` + tray
 * "Restart"), there were N listeners all firing on every theme change,
 * each holding a stale reference to a destroyed BrowserWindow.
 *
 * The single module-level handler reads `state.mainWindow` live (so it
 * always operates on the current window) and is removed once via
 * `nativeTheme.off(...)` when the window is destroyed (in case the
 * module is hot-reloaded in dev — in production the listener lives
 * for the process lifetime, which is correct since `state.mainWindow`
 * is the canonical window reference).
 */
let _nativeThemeHandler: (() => void) | null = null;

/**
 * Register the global `nativeTheme.on("updated", ...)` listener exactly
 * once. Idempotent — safe to call multiple times. Exported for tests
 * (R6-F3) so we can assert it's only registered once across multiple
 * `createMainWindow()` calls.
 */
export function registerNativeThemeListener(): void {
	if (_nativeThemeHandler) return;
	_nativeThemeHandler = () => {
		if (state.mainWindow && !state.mainWindow.isDestroyed()) {
			const name = nativeTheme.shouldUseDarkColors
				? "icon-dark.png"
				: "icon.png";
			state.mainWindow.setIcon(path.join(__dirname, `../../resources/${name}`));
		}
	};
	nativeTheme.on("updated", _nativeThemeHandler);
}

/**
 * Test-only: remove the global `nativeTheme.on("updated", ...)` listener
 * and reset internal state so a test can verify registration happens
 * exactly once across `createMainWindow()` calls.
 */
export function _resetNativeThemeListenerForTest(): void {
	if (_nativeThemeHandler) {
		try {
			nativeTheme.off("updated", _nativeThemeHandler);
		} catch {
			/* best-effort */
		}
		_nativeThemeHandler = null;
	}
}

/**
 * Test-only: return whether the module-level `nativeTheme.on("updated")`
 * listener is currently registered. Used by R6-F3 unit tests.
 */
export function _nativeThemeListenerRegistered(): boolean {
	return _nativeThemeHandler !== null;
}

export function createMainWindow(forceShow = false): void {
	if (state.mainWindow) return;
	// CR-28: do NOT set `state.pythonReady = true` here.
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

	state.mainWindow = new BrowserWindow({
		width: 1000,
		height: 700,
		minWidth: 850,
		minHeight: 550,
		icon: path.join(
			__dirname,
			`../../resources/icon${nativeTheme.shouldUseDarkColors ? "-dark" : ""}.png`,
		),
		frame: false,
		hasShadow: false,
		show: shouldShow,
		// Set the window background color to match the app theme so the
		// rounded corners (border-radius on the wrapper div) don't reveal
		// a white flash when the window is hidden on close.  The renderer
		// applies its own background via CSS variables, but the area behind
		// the web content (the corners outside border-radius) shows through
		// to the Electron window background.
		backgroundColor: nativeTheme.shouldUseDarkColors ? "#1a1b1e" : "#ffffff",
		// skipTaskbar when hidden so an autostarted background instance leaves
		// no taskbar entry until the user actually opens it.
		skipTaskbar: !shouldShow,
		webPreferences: {
			preload: path.join(__dirname, "../preload/index.js"),
			backgroundThrottling: false,
			// SOUND-FIX: allow AudioContext / HTMLAudioElement to play
			// without a prior user gesture in the renderer.  The user
			// has explicitly launched VoiceTyper as a desktop app, so
			// the implicit "user gesture" of installing + running the
			// app satisfies the trust requirement.  Without this, the
			// start/stop recording audio cues don't play when the user
			// triggers recording via the GLOBAL hotkey (which fires
			// from the OS-level backend, NOT from a renderer gesture).
			// The default Chromium policy ("document-user-activation-
			// required") causes the intermittent "sometimes no sound"
			// bug — the cue plays only if the user happened to click
			// in the Electron window before pressing the hotkey.
			autoplayPolicy: "no-user-gesture-required",
			// SEC-014: explicit hardening.  These are Electron defaults
			// for most fields, but setting them explicitly guards against
			// future Electron version changes flipping a default to a
			// less-safe value.
			contextIsolation: true, // renderer can't touch Node require
			nodeIntegration: false, // no require() in renderer
			sandbox: true, // preload runs in sandboxed context
			webSecurity: true, // enforce same-origin policy
			allowRunningInsecureContent: false, // block mixed-content
			// spellcheck adds a tiny IPC surface; we don't need it.
			spellcheck: false,
		},
	});

	// CR-28 (session-1): intercept outbound `python-event` broadcasts
	// to the renderer so we can flip `state.pythonReady = true` the
	// moment the backend signals it has finished initialization.
	// The handle-message.ts module broadcasts *every* Python push
	// event to the renderer via `state.mainWindow.webContents.send(
	// "python-event", msg)` — by wrapping that single chokepoint we
	// catch the `ready` event without needing to modify
	// handle-message.ts. The wrapper is a no-op for every other
	// event and for subsequent `ready` events (idempotent).
	//
	// We use a wrapper-instead-of-ipcMain because `webContents.send`
	// is main → renderer; `ipcMain.on` only catches renderer → main.
	// There is no native Electron hook for "outgoing message
	// observed" so we override `send` on this specific webContents
	// instance. The original is captured via `.bind()` so the
	// wrapper can delegate without losing `this`.
	const wc = state.mainWindow.webContents;
	const origSend = wc.send.bind(wc) as (
		channel: string,
		...args: unknown[]
	) => void;
	// Cast through unknown to satisfy TS: Electron's `send` overload
	// set is complex; we only need to preserve call-through
	// behavior. The runtime contract is identical to the original.
	wc.send = ((channel: string, ...args: unknown[]) => {
		if (
			!state.pythonReady &&
			channel === "python-event" &&
			args.length > 0 &&
			typeof args[0] === "object" &&
			args[0] !== null &&
			(args[0] as Record<string, unknown>).type === "ready"
		) {
			state.pythonReady = true;
			console.warn(
				"[STARTUP] backend sent {type:'ready'} — pythonReady = true (backend fully initialized)",
			);
		}
		return origSend(channel, ...args);
	}) as typeof wc.send;

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

	// Close-to-tray: the X button hides the window instead of quitting the
	// app.  The process (tray icon, Python backend, bubble) stays alive.
	// Full quit only happens via the tray "Quit" menu item → stopPython().
	state.mainWindow.on("close", (event) => {
		if (!app.isQuitting) {
			event.preventDefault();
			state.mainWindow?.hide();
			// Remove from taskbar while hidden.
			state.mainWindow?.setSkipTaskbar(true);
		}
	});

	// When the window is shown again (second-instance / tray open), restore
	// the taskbar entry.
	state.mainWindow.on("show", () => {
		state.mainWindow?.setSkipTaskbar(false);
	});

	state.mainWindow.on("maximize", () => broadcastMaximized(true));
	state.mainWindow.on("unmaximize", () => broadcastMaximized(false));

	// CONSOLE-FIX: Electron 30+ deprecated the multi-argument
	// console-message signature `(_e, level, message, line, source)`.
	// The new signature is a single Event object with properties:
	//   e.level, e.message, e.lineNumber, e.sourceId
	// The old signature emitted a deprecation warning on every app start.
	state.mainWindow.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 2) {
			const tag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			console.warn(
				`${ts()}  ${RENDERER_CLR}[${tag}]${RESET} ${cleanConsoleMsg(e.message)} (${e.sourceId}:${e.lineNumber})`,
			);
		}
	});

	state.mainWindow.webContents.on("before-input-event", (_event, input) => {
		if (
			(input.control || input.meta) &&
			input.shift &&
			input.key.toLowerCase() === "i"
		) {
			// SEC-013: DevTools should only be available in dev builds.
			// In production (app.isPackaged === true), the toggle is a
			// no-op so end users (and any XSS that tries to trigger it
			// via synthetic keyboard events) can't open DevTools.
			if (!app.isPackaged) {
				state.mainWindow?.webContents.toggleDevTools();
			}
		}
	});

	if (process.env.ELECTRON_RENDERER_URL) {
		state.mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
	} else {
		state.mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
	}
}
