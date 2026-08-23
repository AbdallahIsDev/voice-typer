/**
 * Bubble overlay geometry + saved-position state.
 *
 * Extracted from `bubble-window.ts` (). Owns:
 *   - `savedBubblePos` + `getSavedBubblePosition()` /
 *     `resetSavedBubblePosition()` / `setSavedBubblePosition()` —
 *      in-session persistence of the user's last drag
 *     position.
 *   - `centerOnPrimaryDisplay()` — top/bottom centered position for
 *     the bubble.
 *   - `centerOnActiveDisplay()` —  multi-monitor aware
 *     positioning using `screen.getCursorScreenPoint()`.
 *   - `getActiveDisplay()` — resolve the display the cursor is on.
 *   - `isPositionOnAnyDisplay()` —  validate a candidate
 *     position against the current set of displays' work areas.
 *   - `isForegroundFullscreen()` — SEC-025 best-effort exclusive-
 *     fullscreen detection.
 *
 * : bubble position is now remembered across show/hide cycles.
 * The BrowserWindow's `moved` event (wired in `lifecycle.ts` via
 * `recordBubbleMoved`) persists the user's last drag position to
 * module-level state (`savedBubblePos`) and schedules a debounced
 * durable persist of the pair to the Python config; on the next
 * `showBubbleWindow()` (in `show-hide.ts`) we restore those coordinates
 * instead of re-centering — falling back to the durable config pair
 * after a restart (see `resolveRestoredBubblePosition`). A
 * `bubble:set-position` IPC (top/bottom toggle from the Settings page)
 * resets the saved position so the new edge default takes effect; the
 * Python side clears both keys server-side on the same toggle and the
 * cleared pair propagates back via `setPersistedBubblePosition`.
 */
import { BrowserWindow, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../../constants";
//converted from defensive `require("../../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { BUBBLE_CLR, log, RESET } from "../../logging";
import { sendToPython } from "../../python/send-to-python";
import { state } from "../../state";

//in-session persistence of the bubble's last user-positioned
// coordinates. `null` means "no saved position — use the default
// center-on-active-display placement". Updated by the BrowserWindow
// `moved` event (see createBubbleWindow in lifecycle.ts) and cleared
// by the `bubble:set-position` IPC handler (see bubble-handlers.ts) so
// a top/bottom toggle re-centers instead of stranding the bubble at
// the old y coordinate.
//
// Exported as a live binding so `show-hide.ts` can read the current
// value via `import { savedBubblePos }`. Writes go through
// `setSavedBubblePosition()` so the mutation stays inside this module
// (ES module live bindings only allow the exporting module to
// reassign).
export let savedBubblePos: { x: number; y: number } | null = null;

/**
 * Internal setter used by `lifecycle.ts` (the `moved` and
 * `display-removed` handlers) to update the saved position. Keeping
 * the write path inside this module preserves the ES-module live-
 * binding invariant (only the exporting module may reassign an
 * exported `let`).
 */
export function setSavedBubblePosition(
	pos: { x: number; y: number } | null,
): void {
	savedBubblePos = pos;
}

/**
 * Read the saved bubble position (if any).
 *
 * Test-observability export: production code reads the slot through
 * `resolveRestoredBubblePosition()` / the durable-persist path; this
 * getter exists so positioning/durable-position tests can assert
 * in-session slot state directly. Do NOT add new production callers —
 * extend the resolver instead.
 */
export function getSavedBubblePosition(): { x: number; y: number } | null {
	return savedBubblePos;
}

/**
 * : reset the saved bubble position so the next
 * `showBubbleWindow()` falls back to the default placement. Called by
 * the `bubble:set-position` IPC handler when the user toggles between
 * top/bottom in Settings.
 */
export function resetSavedBubblePosition(): void {
	savedBubblePos = null;
}

// Durable (cross-restart) persistence of the user's last drag position,
// mirrored from the Python config's optional `bubble_x` / `bubble_y`
// pair. The Python backend publishes the pair inside every
// `bubble_config` push; `handle-message.ts` feeds it here via
// `setPersistedBubblePosition()`. Unlike `savedBubblePos` (in-session),
// this value survives app restarts — it IS the persisted config.
//
// `null` means "never dragged" or "the user toggled top/bottom in
// Settings, which clears both keys server-side". A coordinate of `0`
// is legitimate, so consumers must only check for `null`, never
// truthiness.
let persistedBubblePos: { x: number; y: number } | null = null;

/**
 * Update the durable position cache from a `bubble_config` push payload.
 * Called by `handle-message.ts`. Both fields must be finite numbers for
 * the pair to be stored; anything else (`null`, one-sided, non-numeric)
 * clears the cache so an edge-toggle reset propagates in-session.
 */
export function setPersistedBubblePosition(
	pos: { x: number; y: number } | null,
): void {
	persistedBubblePos = pos;
}

/**
 * Read the durable position cache. Exposed for tests.
 */
export function getPersistedBubblePosition(): { x: number; y: number } | null {
	return persistedBubblePos;
}

/**
 * Resolve the position the bubble should be restored to: the in-session
 * saved drag position wins (fast path); without one, fall back to the
 * durable pair from the Python config (restart restore). Either way the
 * candidate must lie on a currently-attached display — a stale/off-screen
 * position returns `null` and the caller re-centers.
 */
export function resolveRestoredBubblePosition(): {
	x: number;
	y: number;
} | null {
	const candidate = savedBubblePos ?? persistedBubblePos;
	if (!candidate) return null;
	return isPositionOnAnyDisplay(candidate) ? candidate : null;
}

// Debounced durable-persist machinery. Every user drag ends in a `moved`
// event; writing to the Python backend on each one would spam set_config
// during a drag (~60 Hz). The last candidate wins after a 500ms quiet
// period. Programmatic placements (show-time centering/restore, the
// Settings top/bottom toggle) suppress the write entirely so they don't
// overwrite the just-cleared/restored config with their own computed
// coordinates.
const PERSIST_DEBOUNCE_MS = 500;
const SUPPRESS_WINDOW_MS = 1500;
let persistTimer: ReturnType<typeof setTimeout> | null = null;
let persistSuppressedUntil = 0;

/**
 * Schedule the debounced durable persist of a dragged position via the
 * main-process → Python request path (`sendToPython` with no sender id).
 * Fire-and-forget: failures are logged, never thrown, and the move event
 * that triggered this is never blocked.
 */
function scheduleDurablePersist(pos: { x: number; y: number }): void {
	if (persistTimer !== null) clearTimeout(persistTimer);
	persistTimer = setTimeout(() => {
		persistTimer = null;
		if (Date.now() < persistSuppressedUntil) return;
		void sendToPython({
			type: "set_config",
			data: { bubble_x: pos.x, bubble_y: pos.y },
		})
			.then(() => {
				log.info(
					`${BUBBLE_CLR}[BUBBLE]${RESET} persisted bubble position (${pos.x}, ${pos.y})`,
				);
			})
			.catch((e: unknown) => {
				log.warn(
					`${BUBBLE_CLR}[BUBBLE]${RESET} persisting bubble position failed:`,
					e,
				);
			});
	}, PERSIST_DEBOUNCE_MS);
}

/**
 * Cancel any pending durable persist. Called by the Settings top/bottom
 * toggle path (bubble-handlers.ts) BEFORE its programmatic reposition so
 * a stale drag write can't race the server-side reset.
 */
export function cancelScheduledDurablePersist(): void {
	if (persistTimer !== null) {
		clearTimeout(persistTimer);
		persistTimer = null;
	}
}

/**
 * Suppress durable persists for a short window. Called by programmatic
 * placement sites (show-time placement, the Settings edge toggle) so the
 * `moved` events those placements emit are never mistaken for user drags.
 */
export function suppressDurablePersistFor(ms = SUPPRESS_WINDOW_MS): void {
	persistSuppressedUntil = Date.now() + ms;
}

/**
 * Handle a bubble-window move: update the in-session saved position and
 * schedule the debounced durable persist. Skips off-screen positions and
 * suppressed windows (programmatic placements).
 *
 * Shared by the `moved` handler in lifecycle.ts so the in-session write
 * and the durable schedule can't drift apart.
 */
export function recordBubbleMoved(pos: { x: number; y: number }): void {
	if (!isPositionOnAnyDisplay(pos)) {
		// Window ended up off-screen — don't poison either store. The
		// next show falls back to centering.
		setSavedBubblePosition(null);
		return;
	}
	setSavedBubblePosition(pos);
	if (Date.now() >= persistSuppressedUntil) {
		scheduleDurablePersist(pos);
	}
}

/**
 * Reset the debounced-persist machinery (pending timer + suppression
 * window) to a clean slate. Underscore-prefixed to signal
 * "internal/test-only" — mirrors the existing `_resetIpcBackpressure`
 * convention. Production code never needs this: both fields converge on
 * their own (the timer fires, the suppression window expires).
 */
export function _resetDurablePersistStateForTest(): void {
	if (persistTimer !== null) {
		clearTimeout(persistTimer);
		persistTimer = null;
	}
	persistSuppressedUntil = 0;
}

/**
 * : validate a candidate bubble position against the current
 * set of displays' work areas. Returns true if the position's top-left
 * corner lies inside at least one display's work area. Used by the
 * `moved` handler (lifecycle.ts) to skip saving stale coordinates
 * from a window that ended up off-screen (e.g. after a monitor
 * unplug) and by `showBubbleWindow` (show-hide.ts) to discard a saved
 * position whose display no longer exists.
 *
 * Best-effort: if `screen.getAllDisplays()` throws (headless test
 * environment), return true so the caller falls back to the existing
 * "save whatever the OS gave us" behavior.
 */
export function isPositionOnAnyDisplay(pos: { x: number; y: number }): boolean {
	try {
		const displays = screen.getAllDisplays();
		for (const d of displays) {
			const wa = d.workArea;
			if (
				pos.x >= wa.x &&
				pos.x < wa.x + wa.width &&
				pos.y >= wa.y &&
				pos.y < wa.y + wa.height
			) {
				return true;
			}
		}
		return false;
	} catch {
		// Headless / no screen — be permissive so tests that mock
		// `screen` minimally don't break.
		return true;
	}
}

//the Electron `state.ts` default for `bubblePosition` now
// matches the Python config default ("bottom"). Previously the
// Electron default was "top" and this module flipped it to "bottom"
// at module load — a fragile one-shot override that masked the
// inconsistency. The canonical default now lives in `state.ts`; the
// runtime override block has been removed so `state.bubblePosition`
// always reflects the last explicit user choice (or the canonical
// default on first run).

// SEC-025: helper that detects whether the foreground window is in
// exclusive fullscreen mode. Returns false if detection fails (we err
// on the side of NOT painting over fullscreen).
export function isForegroundFullscreen(): boolean {
	try {
		// Electron doesn't expose a direct "is foreground fullscreen" API,
		// but we can check every screen's workspace for a fullscreen window.
		const displays = screen.getAllDisplays();
		for (const _display of displays) {
			// On macOS, BrowserWindow.getAllWindows() lets us inspect each
			// window's fullscreen state. On Windows / Linux this is a no-op
			// (we just return false and let setVisibleOnAllWorkspaces run).
			if (process.platform === "darwin") {
				const win = BrowserWindow.getFocusedWindow();
				if (win?.isFullScreen()) {
					return true;
				}
			}
		}
	} catch (e) {
		// Best-effort detection — `screen.getAllDisplays()` / `BrowserWindow.getFocusedWindow()`
		// can throw in headless test environments or if the GPU process is gone.
		// Non-fatal: we err on the side of NOT painting over fullscreen apps.
		//route through structured `log` so the failure
		// persists in `electron-main.log` (5 MiB rotation) instead of being
		// lost in packaged builds where `console.warn` has no terminal.
		log.warn("[bubble-window] isForegroundFullscreen detection failed:", e);
	}
	return false;
}

/**
 * Resolve the display the user is currently on (multi-monitor aware).
 * Falls back to the primary display if `getCursorScreenPoint()` throws
 * (e.g. headless test environment without a real screen).
 *
 * : previously the bubble always centered on the *primary*
 * display, which stranded the bubble on the wrong screen when the user
 * was working on a secondary monitor. Using the cursor's current
 * screen makes the bubble follow the user.
 */
export function getActiveDisplay(): Electron.Display {
	try {
		const cursor = screen.getCursorScreenPoint();
		// Electron's getDisplayMatching takes a Rectangle (x, y, width,
		// height) — pass a 1×1 rect at the cursor location to find the
		// display that contains the cursor.
		return screen.getDisplayMatching({
			x: cursor.x,
			y: cursor.y,
			width: 1,
			height: 1,
		});
	} catch {
		return screen.getPrimaryDisplay();
	}
}

/**
 * Center the bubble on the primary display (legacy behavior, preserved
 * for callers that explicitly want the primary screen — e.g. tests
 * that mock `screen.getPrimaryDisplay()`).
 */
export function centerOnPrimaryDisplay(): { x: number; y: number } {
	const display = screen.getPrimaryDisplay();
	const wa = display.workArea;
	const y =
		state.bubblePosition === "top"
			? Math.round(wa.y + 48)
			: Math.round(wa.y + wa.height - BUBBLE_HEIGHT - 48);
	return {
		x: Math.round(wa.x + (wa.width - BUBBLE_WIDTH) / 2),
		y,
	};
}

/**
 * : center the bubble on the display the user is currently on
 * (multi-monitor aware). Falls back to `centerOnPrimaryDisplay()` if
 * the active display can't be determined.
 */
export function centerOnActiveDisplay(): { x: number; y: number } {
	const display = getActiveDisplay();
	const wa = display.workArea;
	const y =
		state.bubblePosition === "top"
			? Math.round(wa.y + 48)
			: Math.round(wa.y + wa.height - BUBBLE_HEIGHT - 48);
	return {
		x: Math.round(wa.x + (wa.width - BUBBLE_WIDTH) / 2),
		y,
	};
}
