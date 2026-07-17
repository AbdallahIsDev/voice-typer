/**
 * Window creation entry point for the Electron main process.
 *
 * Extracted from `index.ts` (REF-2). Re-exports the main + bubble window
 * helpers and exposes a single `createWindows()` aggregator that the
 * python TCP-connect callback calls when the backend first becomes
 * reachable (preserving the original lazy-creation behaviour).
 */

export {
	centerOnPrimaryDisplay,
	createBubbleWindow,
	hideBubbleWindow,
	isForegroundFullscreen,
	showBubbleWindow,
} from "./bubble-window";
export {
	broadcastMaximized,
	createMainWindow,
	showMainWindow,
} from "./main-window";

import { createMainWindow } from "./main-window";

/**
 * Create the dashboard window on demand. Called from `tcpConnect`'s
 * connect callback (the moment the Python backend becomes reachable)
 * and from `app.on("activate", …)` on macOS.
 *
 * `forceShow` defaults to `false` so an autostarted hidden instance
 * keeps the window off-screen until the user opens it (second-instance
 * or tray "Open app").
 */
export function createWindows(forceShow = false): void {
	createMainWindow(forceShow);
}
