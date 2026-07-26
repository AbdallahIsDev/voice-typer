/**
 * Bubble-window IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - bubble:move-by — keyboard nudge (NEW-A11Y-006)
 *   - bubble:draggable — toggle draggability (synced to bubble renderer)
 *   - bubble:resize — fit pill content exactly (clamped to min/max)
 *   - bubble:show-from-renderer — show from the bubble's own UI
 *   - bubble:set-position — top/bottom config (synced to bubble renderer)
 *     (Channel-rename: previously `set_bubble_position` (snake_case);
 *     migrated to `bubble:set-position` to match the bubble:* convention.
 *     The legacy listener was removed once the preload files were migrated.)
 *   - bubble:ready — renderer readiness signal
 *
 * SEC-016: `assertFromBubble()` rejects IPC messages not coming from the
 * bubble window's webContents, so a compromised main window can't
 * hijack the always-on-top bubble as a phishing overlay.
 */
import { ipcMain, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../constants";
// DT-13: converted from defensive `require("../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { log } from "../logging";
import { sendToPython } from "../python";
import { state } from "../state";
import {
	centerOnActiveDisplay,
	resetSavedBubblePosition,
	showBubbleWindow,
} from "../windows/bubble-window";

// Bubble resize bounds: min/max resize constraints for the bubble pill. The
// renderer's auto-resize useLayoutEffect measures the pill content and
// sends a `bubble:resize` IPC with the measured width/height. Without
// clamps, a runaway measurement (e.g. a long transcription preview, a
// CSS bug, or a compromised renderer) could shrink the bubble to 0×0
// (disappearing pill) or grow it to cover the user's screen (phishing
// overlay). These bounds keep the pill within a sensible pill-shaped
// range while still accommodating the transcribing text and mic button.
export const MIN_BUBBLE_W = 40;
export const MIN_BUBBLE_H = 24;
export const MAX_BUBBLE_W = 400;
export const MAX_BUBBLE_H = 200;

/**
 * ER-22: the 5 bubble-only Python event types. These events must NOT be
 * broadcast to the main window — they are consumed exclusively by the
 * bubble window. `handle-message.ts` imports this set to filter events.
 */
export const BUBBLE_ONLY_TYPES: ReadonlySet<string> = new Set([
	"bubble_show",
	"bubble_hide",
	"bubble_set_state",
	"bubble_level",
	"bubble_config",
]);

/**
 * SEC-016: helper that rejects IPC messages not coming from the bubble
 * window's webContents.  Without this check, any XSS'd renderer (or a
 * malicious third party that got code into the main window) could
 * hijack the always-on-top bubble as a phishing overlay by sending
 * drag/position commands.
 */
function assertFromBubble(event: Electron.IpcMainEvent): boolean {
	if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return false;
	// Compare senderFrame to the bubble window's main frame.  Electron
	// exposes event.senderFrame (an Electron.WebFrameMain) which is the
	// origin of the IPC message.
	return event.senderFrame === state.bubbleWindow.webContents.mainFrame;
}

/**
 * Bubble resize bounds: clamp a requested resize to the min/max bounds.
 * Centralised here so the same logic applies to every resize path
 * (currently only `bubble:resize`, but a future programmatic resize
 * would reuse it).
 */
function clampBubbleSize(
	width: number,
	height: number,
): {
	width: number;
	height: number;
} {
	return {
		width: Math.max(MIN_BUBBLE_W, Math.min(MAX_BUBBLE_W, Math.round(width))),
		height: Math.max(MIN_BUBBLE_H, Math.min(MAX_BUBBLE_H, Math.round(height))),
	};
}

export function registerBubbleHandlers(): void {
	// NEW-A11Y-006: keyboard-based bubble repositioning for accessibility.
	// Arrow keys move the bubble by 10px; Shift+Arrow moves by 1px (fine).
	// Renderer-keyboard-move note: the renderer-side keydown handler that
	// USED to feed this channel was dead code (the bubble window is
	// `focusable: false`).
	// To re-enable keyboard-move, register a main-process global hotkey
	// (Electron globalShortcut) that sends `bubble:move-by` — see the
	// comment in `Bubble.tsx` for details. The handler is preserved so
	// a future global-hotkey wiring can drive it directly.
	ipcMain.on(
		"bubble:move-by",
		(event, { deltaX, deltaY }: { deltaX: number; deltaY: number }) => {
			if (!assertFromBubble(event)) return;
			if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return;
			const [x, y] = state.bubbleWindow.getPosition();
			const bubbleW = state.bubbleWindow.getBounds().width;
			const bubbleH = state.bubbleWindow.getBounds().height;
			// T2-003: previously used the inline `require("electron").screen`
			// (untyped `any`), and called `getDisplayMatching(x, y)` with two
			// numbers — but Electron's `getDisplayMatching` expects a single
			// `Rectangle` argument. The legacy call relied on Electron's
			// tolerance (it coerced the leading-numeric positional args into
			// a degenerate 0x0 rect, then fell back to the primary display).
			// Replaced with a typed top-level `import { screen }` and a proper
			// `Rectangle` so the call signature matches the API and `tsc` can
			// verify it. The original (x, y) anchor point is preserved by
			// passing the bubble's actual width/height in the rect so the
			// display match is at least as accurate as before (and strictly
			// typed) instead of the previous degenerate 0x0 match.
			const display = screen.getDisplayMatching({
				x,
				y,
				width: bubbleW,
				height: bubbleH,
			});
			const bounds = display.workArea;
			const newX = Math.max(
				bounds.x,
				Math.min(bounds.x + bounds.width - bubbleW, x + deltaX),
			);
			const newY = Math.max(
				bounds.y,
				Math.min(bounds.y + bounds.height - bubbleH, y + deltaY),
			);
			state.bubbleWindow.setPosition(newX, newY);
		},
	);

	ipcMain.on("bubble:draggable", (_event, draggable: boolean) => {
		// The draggable toggle is a config value that BOTH the main window
		// (Settings page, via window.bubble.setDraggable) and the bubble
		// renderer need to sync, so it is NOT restricted to the bubble frame.
		// (Position/draggable are config values, not hijack vectors — unlike
		// the drag-move commands below, which stay bubble-only.)
		state.bubbleDraggable = draggable;
		if (state.bubbleWindow && !state.bubbleWindow.isDestroyed()) {
			state.bubbleWindow.webContents.send("bubble:draggable", draggable);
		}
	});

	// ── Auto-resize bubble window to fit pill content exactly ────────────
	// The pill content is smaller than the default 74x27 BrowserWindow.
	// Without resizing, the transparent window area around the pill
	// intercepts OS mouse events and blocks clicks to windows underneath.
	//
	// Bubble resize bounds: clamp the requested width/height to MIN/MAX bounds
	// before applying. This prevents a runaway measurement (or a
	// compromised renderer) from shrinking the bubble to invisible or
	// growing it to cover the screen.
	ipcMain.on(
		"bubble:resize",
		(event, { width, height }: { width: number; height: number }) => {
			if (!assertFromBubble(event)) return;
			if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return;
			const [x, y] = state.bubbleWindow.getPosition();
			const clamped = clampBubbleSize(width, height);
			state.bubbleWindow.setBounds({
				x,
				y,
				width: clamped.width,
				height: clamped.height,
			});
		},
	);

	ipcMain.on("bubble:show-from-renderer", (event) => {
		// SEC-016: bubble show/hide from the bubble's own UI is allowed;
		// the main window uses `set_config` (allowlisted) for global toggle.
		if (!assertFromBubble(event)) return;
		showBubbleWindow();
	});

	// UX-10: toggle dictation from the bubble's mic button. The bubble
	// renderer is sandboxed (SEC-026) and has NO `python.call`, so it
	// cannot invoke `toggle_dictation` directly. This channel is the
	// single-purpose bridge: the bubble sends `bubble:toggle-dictation`,
	// the main process forwards it to the Python backend as the
	// allowlisted `toggle_dictation` command. SEC-016: restricted to the
	// bubble frame so only the bubble can trigger dictation this way.
	ipcMain.on("bubble:toggle-dictation", (event) => {
		if (!assertFromBubble(event)) return;
		// `toggle_dictation` is in ALLOWED_COMMANDS, so this is a
		// sanctioned backend call (never an arbitrary command).
		void sendToPython({ type: "toggle_dictation" }).catch((err) => {
			log.warn("[BUBBLE] toggle_dictation failed:", String(err));
		});
	});

	// Channel rename: bubble position channel renamed from `set_bubble_position`
	// (snake_case) to `bubble:set-position` (matching the `bubble:*`
	// kebab-case convention used by every other bubble IPC channel:
	// `bubble:draggable`, `bubble:show-from-renderer`,
	// `bubble:toggle-dictation`, `bubble:ready`). The migration is
	// complete: both preload files (`src/preload/index.ts`,
	// `src/preload/bubble.ts`) now send on `bubble:set-position`.
	// The legacy `set_bubble_position` listener was removed once the
	// preload files stopped sending on it.
	//
	// Position is a config value that BOTH the main window (Settings
	// page, via window.bubble.setPosition) and the bubble renderer need
	// to sync, so it is NOT restricted to the bubble frame.  It is a
	// benign enum ('top' | 'bottom'), not a hijack vector.
	//
	// XA-6-4: when the user toggles top/bottom, the previous saved
	// drag position is no longer meaningful (its Y coordinate was
	// computed against the OTHER edge). Reset it and re-center on the
	// display the user is currently on (multi-monitor aware) instead
	// of always stranding the bubble on the primary display.
	const applyBubblePosition = (position: "top" | "bottom") => {
		if (position === "top" || position === "bottom") {
			state.bubblePosition = position;
			resetSavedBubblePosition();
			// If the bubble window is visible, reposition it immediately.
			if (
				state.bubbleWindow &&
				!state.bubbleWindow.isDestroyed() &&
				state.bubbleWindow.isVisible()
			) {
				const c = centerOnActiveDisplay();
				state.bubbleWindow.setBounds({
					x: c.x,
					y: c.y,
					width: BUBBLE_WIDTH,
					height: BUBBLE_HEIGHT,
				});
			}
		}
	};

	// Canonical channel (kebab-case `bubble:*` convention).
	ipcMain.on("bubble:set-position", (_event, position: "top" | "bottom") => {
		applyBubblePosition(position);
	});

	ipcMain.on("bubble:ready", (event) => {
		// SEC-016: only the bubble window signals readiness.
		if (!assertFromBubble(event)) return;
		log.warn("[BUBBLE] renderer reports ready");
		state._bubblePageReady = true;
	});
}
