/**
 * Bubble overlay BrowserWindow creation + helpers.
 *
 * Extracted from `index.ts` (REF-2). DR-7 split this module into 4
 * focused files under `./bubble/`; this file is now a thin re-export
 * aggregator so all existing consumers (`bubble-handlers.ts`,
 * `handle-message.ts`, `window-handlers.ts`, `windows/index.ts`,
 * `dev/bubble-test.ts`, and the runtime tests in
 * `__tests__/bubble-window-fallback.test.ts`) can keep importing from
 * `./bubble-window` unchanged.
 *
 * Original module overview (now spread across `./bubble/`):
 *   - `centerOnPrimaryDisplay()` — top/bottom centered position for the bubble.
 *   - `centerOnActiveDisplay()` — PVT-068: multi-monitor aware positioning
 *     using `screen.getCursorScreenPoint()` to find the display the user
 *     is currently on (rather than always the primary display).
 *   - `isForegroundFullscreen()` — best-effort exclusive-fullscreen detection
 *     (SEC-025) so we don't paint over fullscreen apps.
 *   - `createBubbleWindow()` — lazy-creates the always-on-top transparent pill.
 *   - `showBubbleWindow()` / `hideBubbleWindow()` — animated show/hide with
 *     rapid-toggle guard + renderer-driven exit animation.
 *
 * PVT-068: bubble position is now remembered across show/hide cycles.
 * The BrowserWindow's `moved` event persists the user's last drag
 * position to module-level state (`savedBubblePos`); on the next
 * `showBubbleWindow()` we restore those coordinates instead of
 * re-centering. A `bubble:set-position` IPC (top/bottom toggle from
 * the Settings page) resets the saved position so the new edge
 * default takes effect. In-session persistence only — durable
 * persistence to the Python config is a follow-up (config.py is out
 * of scope for this fix).
 */
export {
	centerOnActiveDisplay,
	centerOnPrimaryDisplay,
	getActiveDisplay,
	getSavedBubblePosition,
	isForegroundFullscreen,
	isPositionOnAnyDisplay,
	resetSavedBubblePosition,
	savedBubblePos,
	setSavedBubblePosition,
} from "./bubble/positioning";
export {
	HideAnimationSlot,
	clearCurrentHideAnimationCallback,
	consumeHideAnimationCallback,
	currentHideAnimationSlot,
	onHideAnimationComplete,
} from "./bubble/hide-animation";
export {
	createBubbleWindow,
	notifyBubbleLocaleChanged,
} from "./bubble/lifecycle";
export { hideBubbleWindow, showBubbleWindow } from "./bubble/show-hide";
