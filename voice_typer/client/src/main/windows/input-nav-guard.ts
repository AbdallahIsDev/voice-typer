/**
 * Renderer input/navigation guards for the dashboard window.
 *
 * Extracted from `main-window.ts`. Owns the `before-input-event`
 * DevTools/F11 gate (SEC-013) and the deny-all `setWindowOpenHandler`
 * hardening.
 */
import { app, type BrowserWindow, shell } from "electron";
import { log } from "../logging";
import { state } from "../state";

/**
 * Register the `before-input-event` gate on the dashboard window.
 *
 * F11 → toggle fullscreen. `Menu.setApplicationMenu(null)` (called by
 * `createMainWindow()`) strips the default menu's "Toggle Full Screen"
 * F11 accelerator, so without this F11 is a silent no-op. Match the
 * standard desktop convention (browser-style full viewport) instead.
 * This can't conflict with a user-assigned F11 dictation hotkey: the
 * OS-level global listener consumes the key before it reaches the app
 * when F11 is bound, so this handler only fires when F11 is unbound.
 * `isAutoRepeat` is skipped so holding F11 doesn't thrash the toggle,
 * and only keyDown is handled (keyUp would double-toggle on a single
 * press).
 */
export function registerInputNavGuard(win: BrowserWindow): void {
	win.webContents.on("before-input-event", (_event, input) => {
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

		if (
			input.type === "keyDown" &&
			!input.isAutoRepeat &&
			input.key.toLowerCase() === "f11"
		) {
			const win = state.mainWindow;
			if (win && !win.isDestroyed()) {
				win.setFullScreen(!win.isFullScreen());
			}
		}
	});
}

/**
 * Install the deny-all window-open guard on the dashboard window.
 *
 * Window-open hardening: deny every renderer-initiated window.open() /
 * target=_blank navigation by default. Without this handler, a
 * compromised renderer (XSS, dependency supply-chain, malicious
 * transcription payload that reaches an innerHTML sink) can pop an
 * arbitrary external URL inside a fresh Electron BrowserWindow —
 * bypassing the renderer sandbox and exposing Node primitives to
 * untrusted content.
 *
 * Behavior:
 *   • https URLs → routed to the user's default browser via
 *     `shell.openExternal` and the in-app window is denied.
 *   • All other schemes (file://, javascript:, data:, blob:) →
 *     denied silently with a WARN log so a redirected/typo'd URL
 *     is visible without crashing the renderer.
 *   • `shell.openExternal` failures are logged but never block
 *     the deny (the URL was already going to be denied anyway).
 */
export function installWindowOpenHandler(win: BrowserWindow): void {
	win.webContents.setWindowOpenHandler(({ url }) => {
		if (/^https?:\/\//i.test(url)) {
			// Fire-and-forget; openExternal is async but the
			// handler must return synchronously. A rejection
			// (e.g. no default browser configured on a fresh
			// OS install) is logged but does not change the
			// deny verdict.
			void shell.openExternal(url).catch((err: unknown) =>
				log.warn("[MAIN] setWindowOpenHandler: shell.openExternal failed", {
					url,
					error: (err as Error)?.message,
				}),
			);
		} else {
			log.warn(
				"[MAIN] setWindowOpenHandler: denied non-https window.open target",
				{ url },
			);
		}
		return { action: "deny" };
	});
}
