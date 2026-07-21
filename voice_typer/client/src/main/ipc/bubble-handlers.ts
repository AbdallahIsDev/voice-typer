/**
 * Bubble-window IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - bubble:move-by — keyboard nudge (NEW-A11Y-006)
 *   - bubble:draggable — toggle draggability (synced to bubble renderer)
 *   - bubble:resize — fit pill content exactly
 *   - bubble:show-from-renderer — show from the bubble's own UI
 *   - set_bubble_position — top/bottom config (synced to bubble renderer)
 *   - bubble:ready — renderer readiness signal
 *
 * SEC-016: `assertFromBubble()` rejects IPC messages not coming from the
 * bubble window's webContents, so a compromised main window can't
 * hijack the always-on-top bubble as a phishing overlay.
 */
import { ipcMain } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../constants";
import { BUBBLE_CLR, RESET, ts } from "../logging";
import { sendToPython } from "../python";
import { state } from "../state";
import { centerOnPrimaryDisplay, showBubbleWindow } from "../windows";

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

export function registerBubbleHandlers(): void {
	// NEW-A11Y-006: keyboard-based bubble repositioning for accessibility.
	// Arrow keys move the bubble by 10px; Shift+Arrow moves by 1px (fine).
	// The bubble renderer listens for keydown when focused and sends these.
	ipcMain.on(
		"bubble:move-by",
		(event, { deltaX, deltaY }: { deltaX: number; deltaY: number }) => {
			if (!assertFromBubble(event)) return;
			if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return;
			const [x, y] = state.bubbleWindow.getPosition();
			// Clamp to screen bounds so the bubble doesn't move off-screen.
			// NOTE: preserved verbatim from the original index.ts — the inline
			// `require("electron").screen` keeps the call untyped (the original
			// passed (x, y) to getDisplayMatching, which expects a Rectangle;
			// changing the call signature would be a behavior change).
			const screen = require("electron").screen;
			const display = screen.getDisplayMatching(x, y);
			const bounds = display.workArea;
			const bubbleW = state.bubbleWindow.getBounds().width;
			const bubbleH = state.bubbleWindow.getBounds().height;
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
	ipcMain.on(
		"bubble:resize",
		(event, { width, height }: { width: number; height: number }) => {
			if (!assertFromBubble(event)) return;
			if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return;
			const [x, y] = state.bubbleWindow.getPosition();
			state.bubbleWindow.setBounds({
				x,
				y,
				width: Math.round(width),
				height: Math.round(height),
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
			console.warn(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] toggle_dictation failed: ${String(err)}${RESET}`,
			);
		});
	});

	ipcMain.on("set_bubble_position", (_event, position: "top" | "bottom") => {
		// Position is a config value that BOTH the main window (Settings
		// page, via window.bubble.setPosition) and the bubble renderer need
		// to sync, so it is NOT restricted to the bubble frame.  It is a
		// benign enum ('top' | 'bottom'), not a hijack vector.
		if (position === "top" || position === "bottom") {
			state.bubblePosition = position;
			// If the bubble window is visible, reposition it immediately.
			if (
				state.bubbleWindow &&
				!state.bubbleWindow.isDestroyed() &&
				state.bubbleWindow.isVisible()
			) {
				const c = centerOnPrimaryDisplay();
				state.bubbleWindow.setBounds({
					x: c.x,
					y: c.y,
					width: BUBBLE_WIDTH,
					height: BUBBLE_HEIGHT,
				});
			}
		}
	});

	ipcMain.on("bubble:ready", (event) => {
		// SEC-016: only the bubble window signals readiness.
		if (!assertFromBubble(event)) return;
		console.warn(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] renderer reports ready${RESET}`,
		);
		state._bubblePageReady = true;
	});
}
