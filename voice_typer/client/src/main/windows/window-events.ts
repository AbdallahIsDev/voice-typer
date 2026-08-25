/**
 * Dashboard-window lifecycle event wiring.
 *
 * Extracted from `main-window.ts`. Owns the close-to-tray interception,
 * taskbar-restore on show, maximize/unmaximize + macOS fullscreen sync
 * (fanned out through the injected `broadcastMaximized` helper), and the
 * `closed` state cleanup (nulls `state.mainWindow` + removes the
 * per-renderer backpressure entry for the destroyed webContents).
 */
import { app, type BrowserWindow } from "electron";
import { log } from "../logging";
// `_removeRendererFromBackpressure` clears the per-renderer rate-limit
// Map entry when the window is destroyed, preventing the
// `_rendererCallTimestamps` Map from leaking one entry per destroyed
// BrowserWindow (each `webContents.id` is a fresh integer).
import { _removeRendererFromBackpressure } from "../python/send-to-python";
import { state } from "../state";
// `isLinuxWaylandWithoutSni` is consulted in the main window's
// `close` handler so that on Linux Wayland WITHOUT StatusNotifierItem
// (Sway/Hyprland/dwl/river — no tray icon to dismiss the app to),
// closing the dashboard window flows through to `window-all-closed` →
// `app.quit()` instead of hiding to a tray that doesn't exist.
import { isLinuxWaylandWithoutSni } from "../tray_available";

/**
 * Register every lifecycle listener on the freshly created dashboard
 * window. Registration order mirrors the original inline blocks in
 * `createMainWindow()`. `shouldShow` preserves the START_HIDDEN
 * autostart decision for the `ready-to-show` gate; maximized/fullscreen
 * events fan out through the injected broadcast helper so this module
 * stays decoupled from the facade that owns it.
 */
export function registerWindowLifecycleEvents(
	win: BrowserWindow,
	shouldShow: boolean,
	broadcastMaximized: (maximized: boolean) => void,
): void {
	// Gate the first `.show()` on the `ready-to-show` event so the window
	// appears only once the renderer has painted. Pairs with `show: false`
	// in the BrowserWindow ctor above. `shouldShow` preserves the
	// START_HIDDEN autostart path.
	win.once("ready-to-show", () => {
		if (shouldShow && state.mainWindow && !state.mainWindow.isDestroyed()) {
			state.mainWindow.show();
		}
	});

	// Close-to-tray: the X button hides the window instead of quitting the
	// app.  The process (tray icon, Python backend, bubble) stays alive.
	// Full quit only happens via the tray "Quit" menu item → stopPython().
	//
	// on Linux Wayland WITHOUT StatusNotifierItem (Sway/Hyprland/
	// dwl/river) the Python tray backend sets `_tray_unavailable = True`
	// and creates NO tray icon.  Hiding the last window here would strand
	// the user — there's no tray icon to re-open the dashboard or quit
	// the app.  Consulting `isLinuxWaylandWithoutSni()` lets the close
	// flow through to `closed` → `window-all-closed` → `app.quit()` so
	// the process exits cleanly instead of becoming an invisible zombie.
	// On all other platforms (Windows, macOS, Linux-with-SNI tray icon),
	// the close-to-tray behavior is preserved.  The `app.isQuitting`
	// short-circuit is kept so the tray "Quit" menu item (which sets
	// `isQuitting = true`) still lets the close flow through to a real
	// destroy + quit.
	win.on("close", (event) => {
		if (!app.isQuitting && !isLinuxWaylandWithoutSni()) {
			event.preventDefault();
			state.mainWindow?.hide();
			// Remove from taskbar while hidden.
			state.mainWindow?.setSkipTaskbar(true);
		}
	});

	// When the window is shown again (second-instance / tray open), restore
	// the taskbar entry.
	win.on("show", () => {
		state.mainWindow?.setSkipTaskbar(false);
	});

	win.on("maximize", () => broadcastMaximized(true));
	win.on("unmaximize", () => broadcastMaximized(false));

	// macOS fullscreen sync: the green traffic light enters a fullscreen
	// space on macOS instead of emitting maximize/unmaximize. Mirror it
	// onto the same maximized-changed channel so the renderer drops
	// rounded corners / updates chrome state in fullscreen. These events
	// only fire on macOS — no-ops on Windows/Linux.
	win.on("enter-full-screen", () => broadcastMaximized(true));
	// Query the real state on leave: macOS fullscreen can return to a
	// PRE-maximized window (Option+click green = zoom, then green again =
	// fullscreen). Hardcoding `false` would leave the renderer's
	// `is-maximized` class stale — rounded corners would reappear on a
	// still-maximized window.
	win.on("leave-full-screen", () => {
		broadcastMaximized(state.mainWindow?.isMaximized() ?? false);
	});

	// Window-state cleanup: null out `state.mainWindow` once the window is actually
	// destroyed. The `close` handler above intercepts the X button to
	// hide-to-tray (preventDefault), so it never reaches `closed`. But
	// the real destroy paths (tray "Quit" → `app.isQuitting = true` →
	// close flows through; `win.destroy()` from start-python.ts:132;
	// app teardown on quit) DO reach `closed`, and without this handler
	// `state.mainWindow` keeps pointing at a destroyed BrowserWindow
	// until process exit. Any later read of `state.mainWindow` (e.g.
	// `showMainWindow()`'s `isDestroyed()` check, or the
	// `nativeTheme.on("updated")` handler) then trips over a dead
	// reference. Nulling here keeps the state invariant honest:
	// `state.mainWindow` is non-null iff a live window exists.
	// Capture the destroyed window's `webContents.id` BEFORE nulling
	// `state.mainWindow` so we can clean up the per-renderer rate-limit
	// entry in `send-to-python.ts`'s `_rendererCallTimestamps` Map.
	// Without this, each destroyed BrowserWindow leaks one Map entry
	// (keyed by its now-defunct `webContents.id`) forever.
	//
	// XV-??: capture the webContents id AT CREATION TIME (not in the
	// "closed" callback, where the window is already destroyed and
	// `.webContents` throws "TypeError: Object has been destroyed").
	const mainWebContentsId = win.webContents.id;
	win.on("closed", () => {
		const deadWebContentsId = mainWebContentsId;
		state.mainWindow = null;
		if (typeof deadWebContentsId === "number") {
			try {
				_removeRendererFromBackpressure(deadWebContentsId);
			} catch (e) {
				log.warn(
					"[main-window] _removeRendererFromBackpressure failed on closed:",
					e,
				);
			}
		}
	});
}
