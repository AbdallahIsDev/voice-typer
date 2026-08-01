/**
 * Window creation entry point for the Electron main process.
 *
 * Extracted from `index.ts` (REF-2). Re-exports the main + bubble window
 * helpers and exposes a single `createWindows()` aggregator that the
 * python TCP-connect callback calls when the backend first becomes
 * reachable (preserving the original lazy-creation behaviour).
 *
 *  (session-5 dead-code cleanup): the previous re-exports
 * of `createBubbleWindow`, `isForegroundFullscreen` (from
 * `./bubble-window`) and `broadcastMaximized` (from `./main-window`)
 * were removed — grep across `src/main/**` confirms no caller imports
 * them through `../windows`; they are only used internally by their
 * own modules. `centerOnActiveDisplay`, `resetSavedBubblePosition`,
 * and `getSavedBubblePosition` (added by ) are likewise
 * imported directly from `./bubble-window` by their sole consumer
 * (`ipc/bubble-handlers.ts`), so they are not re-exported here.
 */

export {
	centerOnPrimaryDisplay,
	hideBubbleWindow,
	showBubbleWindow,
} from "./bubble-window";
export {
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
