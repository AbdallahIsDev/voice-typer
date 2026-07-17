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

export function createMainWindow(forceShow = false): void {
	if (state.mainWindow) return;
	state.pythonReady = true;

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

	nativeTheme.on("updated", () => {
		if (state.mainWindow) {
			const name = nativeTheme.shouldUseDarkColors
				? "icon-dark.png"
				: "icon.png";
			state.mainWindow.setIcon(path.join(__dirname, `../../resources/${name}`));
		}
	});

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
