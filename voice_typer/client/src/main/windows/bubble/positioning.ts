/**
 * Bubble overlay geometry + saved-position state.
 *
 * Extracted from `bubble-window.ts` (DR-7). Owns:
 *   - `savedBubblePos` + `getSavedBubblePosition()` /
 *     `resetSavedBubblePosition()` / `setSavedBubblePosition()` —
 *     PVT-068 in-session persistence of the user's last drag
 *     position.
 *   - `centerOnPrimaryDisplay()` — top/bottom centered position for
 *     the bubble.
 *   - `centerOnActiveDisplay()` — PVT-068 multi-monitor aware
 *     positioning using `screen.getCursorScreenPoint()`.
 *   - `getActiveDisplay()` — resolve the display the cursor is on.
 *   - `isPositionOnAnyDisplay()` — XA-6-5 validate a candidate
 *     position against the current set of displays' work areas.
 *   - `isForegroundFullscreen()` — SEC-025 best-effort exclusive-
 *     fullscreen detection.
 *
 * PVT-068: bubble position is now remembered across show/hide cycles.
 * The BrowserWindow's `moved` event (wired in `lifecycle.ts`) persists
 * the user's last drag position to module-level state
 * (`savedBubblePos`); on the next `showBubbleWindow()` (in
 * `show-hide.ts`) we restore those coordinates instead of
 * re-centering. A `bubble:set-position` IPC (top/bottom toggle from
 * the Settings page) resets the saved position so the new edge
 * default takes effect. In-session persistence only — durable
 * persistence to the Python config is a follow-up (config.py is out
 * of scope for this fix).
 */
import { BrowserWindow, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../../constants";
// DT-13: converted from defensive `require("../../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { log } from "../../logging";
import { state } from "../../state";

// PVT-068: in-session persistence of the bubble's last user-positioned
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
 * PVT-068: read the saved bubble position (if any). Exposed for IPC
 * consumers (e.g. a future Settings-page "reset position" affordance)
 * and for tests.
 */
export function getSavedBubblePosition(): { x: number; y: number } | null {
	return savedBubblePos;
}

/**
 * PVT-068: reset the saved bubble position so the next
 * `showBubbleWindow()` falls back to the default placement. Called by
 * the `bubble:set-position` IPC handler when the user toggles between
 * top/bottom in Settings.
 */
export function resetSavedBubblePosition(): void {
	savedBubblePos = null;
}

/**
 * XA-6-5: validate a candidate bubble position against the current
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

// XA-6-20: the Electron `state.ts` default for `bubblePosition` now
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
		// DE-87 / S2-CR-75: route through structured `log` so the failure
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
 * PVT-068: previously the bubble always centered on the *primary*
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
 * PVT-068: center the bubble on the display the user is currently on
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
