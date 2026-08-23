/**
 * Bubble overlay BrowserWindow creation + helpers.
 *
 * Extracted from `index.ts` (REF-2).  split this module into 4
 * focused files under `./bubble/`; this file is now a thin re-export
 * aggregator so all existing consumers (`bubble-handlers.ts`,
 * `handle-message.ts`, `window-handlers.ts`, `windows/index.ts`,
 * `dev/bubble-test.ts`, and the runtime tests in
 * `__tests__/bubble-window-fallback.test.ts`) can keep importing from
 * `./bubble-window` unchanged.
 *
 * Original module overview (now spread across `./bubble/`):
 *   - `centerOnPrimaryDisplay()` — top/bottom centered position for the bubble.
 *   - `centerOnActiveDisplay()` — : multi-monitor aware positioning
 *     using `screen.getCursorScreenPoint()` to find the display the user
 *     is currently on (rather than always the primary display).
 *   - `isForegroundFullscreen()` — best-effort exclusive-fullscreen detection
 *     (SEC-025) so we don't paint over fullscreen apps.
 *   - `createBubbleWindow()` — lazy-creates the always-on-top transparent pill.
 *   - `showBubbleWindow()` / `hideBubbleWindow()` — animated show/hide with
 *     rapid-toggle guard + renderer-driven exit animation.
 *
 * : bubble position is now remembered across show/hide cycles.
 * The BrowserWindow's `moved` event persists the user's last drag
 * position to module-level state (`savedBubblePos`) and schedules a
 * debounced durable persist to the Python config; on the next
 * `showBubbleWindow()` we restore those coordinates instead of
 * re-centering, falling back to the durable config pair after a
 * restart. A `bubble:set-position` IPC (top/bottom toggle from the
 * Settings page) resets the saved position so the new edge default
 * takes effect (the Python side clears both keys server-side on the
 * same toggle).
 */

export {
	clearCurrentHideAnimationCallback,
	consumeHideAnimationCallback,
	currentHideAnimationSlot,
	HideAnimationSlot,
	onHideAnimationComplete,
} from "./bubble/hide-animation";
export {
	createBubbleWindow,
	notifyBubbleLocaleChanged,
} from "./bubble/lifecycle";
export {
	cancelScheduledDurablePersist,
	centerOnActiveDisplay,
	centerOnPrimaryDisplay,
	getActiveDisplay,
	getPersistedBubblePosition,
	getSavedBubblePosition,
	isForegroundFullscreen,
	isPositionOnAnyDisplay,
	recordBubbleMoved,
	resetSavedBubblePosition,
	resolveRestoredBubblePosition,
	savedBubblePos,
	setPersistedBubblePosition,
	setSavedBubblePosition,
	suppressDurablePersistFor,
} from "./bubble/positioning";
export { hideBubbleWindow, showBubbleWindow } from "./bubble/show-hide";
