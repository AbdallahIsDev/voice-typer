/**
 * Dashboard-window constructor-options builder.
 *
 * Extracted from `main-window.ts` so `createMainWindow()` reads as a flat
 * orchestrator. Owns the platform chrome selection (macOS hiddenInset +
 * traffic lights vs frameless Windows/Linux), the themed background/icon,
 * and the SEC-014-hardened `webPreferences` block.
 */
import path from "node:path";
import { type BrowserWindowConstructorOptions, nativeTheme } from "electron";

/**
 * Build the BrowserWindow constructor options for the dashboard window.
 * `shouldShow` mirrors the START_HIDDEN autostart decision made by the
 * caller (`createMainWindow(forceShow)`); it only controls `skipTaskbar`
 * — the window itself is always created hidden and shown on
 * `ready-to-show`.
 */
export function buildMainWindowOptions(
	shouldShow: boolean,
): BrowserWindowConstructorOptions {
	return {
		width: 1000,
		height: 700,
		minWidth: 850,
		minHeight: 550,
		icon: path.join(
			__dirname,
			`../../resources/icon${nativeTheme.shouldUseDarkColors ? "-dark" : ""}.png`,
		), // Cross-platform window chrome. The app uses a custom title bar
		// everywhere (the OS frame doesn't blend with the app theme), but
		// the window-control BUTTONS are platform-convention-dependent:
		//   - macOS: native traffic lights (red/yellow/green) on the LEFT,
		//     drawn by the OS. `titleBarStyle: "hiddenInset"` hides the
		//     bar while keeping the dots (the renderer's TitleBar then
		//     omits its minimize/maximize/close and reserves a gutter).
		//     `frame: false` would strip the traffic lights entirely.
		//   - Windows/Linux: frameless + the renderer draws the three
		//     buttons on the right (the convention on both platforms).
		// `titleBarStyle` is macOS-only and ignored on Windows/Linux, so
		// the branch is explicit for readability.
		...(process.platform === "darwin"
			? {
					titleBarStyle: "hiddenInset" as const,
					trafficLightPosition: { x: 12, y: 10 },
				}
			: { frame: false as const }),
		hasShadow: false,
		// Always create hidden and gate the first `.show()` on the
		// `ready-to-show` event. Previously `show: shouldShow` flashed a
		// blank white BrowserWindow for the 200-800ms between BrowserWindow
		// construction and the renderer's first paint. With `show: false` +
		// the `ready-to-show` listener below, the window appears only once
		// the renderer has actually painted.
		show: false,
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
	};
}
