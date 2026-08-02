/**
 * Global `nativeTheme.on("updated", ...)` listener for the dashboard
 * window's taskbar icon.
 *
 * Extracted from `main-window.ts` (split). Owns:
 *   - `_nativeThemeHandler` — the single module-level listener closure.
 *   - `registerNativeThemeListener()` — idempotent registration; safe to
 *     call from `createMainWindow()` on every window recreation.
 *   - `_resetNativeThemeListenerForTest()` / `_nativeThemeListenerRegistered()`
 *     — test seams used by `__tests__/main-window-native-theme.test.ts`.
 *
 * R6-F3 rationale (preserved from the original main-window.ts docstring):
 * the listener is registered ONCE at module load instead of being
 * re-registered inside `createMainWindow()` on every window recreation.
 * Previously each call to `createMainWindow()` added a NEW listener to
 * `nativeTheme` without ever removing the previous one — so after N
 * window recreations (dev-mode `relaunchApp()` + tray "Restart"), there
 * were N listeners all firing on every theme change, each holding a
 * stale reference to a destroyed BrowserWindow.
 *
 * The single module-level handler reads `state.mainWindow` live (so it
 * always operates on the current window) and is removed once via
 * `nativeTheme.off(...)` when the window is destroyed (in case the
 * module is hot-reloaded in dev — in production the listener lives for
 * the process lifetime, which is correct since `state.mainWindow` is
 * the canonical window reference).
 */
import path from "node:path";
import { nativeTheme } from "electron";
import { state } from "../state";

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
		} catch (e) {
			/* best-effort: test-only cleanup; listener may already be gone */
			console.warn(
				"[theme-listener] _resetNativeThemeListenerForTest off() failed:",
				e,
			);
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
