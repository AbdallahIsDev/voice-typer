/**
 * Bubble-window IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - bubble:move-by — keyboard nudge ()
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
//converted from defensive `require("../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { log } from "../logging";
import { sendToPython } from "../python";
import { state } from "../state";
import {
	centerOnActiveDisplay,
	consumeHideAnimationCallback,
	hideBubbleWindow,
	resetSavedBubblePosition,
	showBubbleWindow,
} from "../windows/bubble-window";
import { BubbleChannels } from "./channels";

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
 * : the 5 bubble-only Python event types. These events must NOT be
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
 * Track the last-known bubble mode so the dismiss handler can cancel
 * in-flight recordings before hiding. When the mode is "recording" or
 * "transcribing", dismiss sends `toggle_dictation` first to stop the
 * pipeline.
 *
 * Updated at the SOURCE — `handle-message.ts` calls
 * `setLastKnownBubbleMode()` when it dispatches a `bubble_set_state`
 * event to the bubble renderer (BEFORE the `webContents.send`). The
 * previous design monkey-patched `webContents.send` inside the
 * `bubble:ready` handler to intercept outgoing `bubble:set-state`
 * sends; that patch accumulated on every bubble reload (each reload
 * wrapped the already-wrapped `send`, producing exponential call
 * growth). Moving the update to the source eliminates the patch
 * entirely.
 */
let _lastKnownBubbleMode: string | null = null;

/**
 * Set the last-known bubble mode. Called from `handle-message.ts`
 * when a `bubble_set_state` push event is dispatched to the bubble
 * window — BEFORE the `webContents.send` so the dismiss handler sees
 * the new mode even if the renderer hasn't acknowledged it yet.
 */
export function setLastKnownBubbleMode(mode: string): void {
	_lastKnownBubbleMode = mode;
}

/**
 * Read the last-known bubble mode. Exported for test observability
 * (the dismiss handler reads the module-level variable directly).
 */
export function getLastKnownBubbleMode(): string | null {
	return _lastKnownBubbleMode;
}

/**
 * Reset the cached bubble mode. Used by tests to isolate scenarios;
 * production code does not need to call this (the mode is overwritten
 * on the next `bubble_set_state` event).
 */
export function _resetLastKnownBubbleMode(): void {
	_lastKnownBubbleMode = null;
}

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
	//keyboard-based bubble repositioning for accessibility.
	// Arrow keys move the bubble by 10px; Shift+Arrow moves by 1px (fine).
	// Renderer-keyboard-move note: the renderer-side keydown handler that
	// USED to feed this channel was dead code (the bubble window is
	// `focusable: false`).
	// To re-enable keyboard-move, register a main-process global hotkey
	// (Electron globalShortcut) that sends `bubble:move-by` — see the
	// comment in `Bubble.tsx` for details. The handler is preserved so
	// a future global-hotkey wiring can drive it directly.
	// Validate the IPC payload at runtime with `typeof`-narrowing
	// checks. The previous `(event, { deltaX, deltaY }: …)` annotation
	// was a compile-time hint only — Electron types the `ipcMain.on`
	// listener's second argument as `any`, so a malformed payload (or a
	// hostile renderer) would have slipped past `tsc` and crashed the
	// main process when destructuring `undefined`. The runtime checks
	// silently drop bad payloads instead.
	ipcMain.on(BubbleChannels.moveBy, (event, payload: unknown) => {
		if (!assertFromBubble(event)) return;
		if (typeof payload !== "object" || payload === null) return;
		const { deltaX, deltaY } = payload as Record<string, unknown>;
		if (typeof deltaX !== "number" || typeof deltaY !== "number") return;
		if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return;
		const [x, y] = state.bubbleWindow.getPosition() as [number, number];
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
	});

	// Runtime-`typeof`-narrow the payload. The previous `(_event,
	// draggable: boolean)` annotation was compile-time only; a
	// non-boolean payload would have silently set `bubbleDraggable`
	// to a string/object and then echoed it back to the bubble.
	ipcMain.on(BubbleChannels.draggable, (_event, payload: unknown) => {
		// The draggable toggle is a config value that BOTH the main window
		// (Settings page, via window.bubble.setDraggable) and the bubble
		// renderer need to sync, so it is NOT restricted to the bubble frame.
		// (Position/draggable are config values, not hijack vectors — unlike
		// the drag-move commands below, which stay bubble-only.)
		if (typeof payload !== "boolean") return;
		const draggable = payload;
		state.bubbleDraggable = draggable;
		if (state.bubbleWindow && !state.bubbleWindow.isDestroyed()) {
			state.bubbleWindow.webContents.send(BubbleChannels.draggable, draggable);
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
	// Runtime-`typeof`-narrow the payload. A malformed (or hostile)
	// payload could previously crash `clampBubbleSize` by passing
	// `undefined`; the runtime check drops it instead.
	ipcMain.on(BubbleChannels.resize, (event, payload: unknown) => {
		if (!assertFromBubble(event)) return;
		if (typeof payload !== "object" || payload === null) return;
		const { width, height } = payload as Record<string, unknown>;
		if (typeof width !== "number" || typeof height !== "number") return;
		if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) return;
		const [x, y] = state.bubbleWindow.getPosition();
		const clamped = clampBubbleSize(width, height);
		state.bubbleWindow.setBounds({
			x,
			y,
			width: clamped.width,
			height: clamped.height,
		});
	});

	ipcMain.on(BubbleChannels.showFromRenderer, (event) => {
		// SEC-016: bubble show/hide from the bubble's own UI is allowed;
		// the main window uses `set_config` (allowlisted) for global toggle.
		if (!assertFromBubble(event)) return;
		showBubbleWindow();
	});

	//toggle dictation from the bubble's mic button. The bubble
	// renderer is sandboxed (SEC-026) and has NO `python.call`, so it
	// cannot invoke `toggle_dictation` directly. This channel is the
	// single-purpose bridge: the bubble sends `bubble:toggle-dictation`,
	// the main process forwards it to the Python backend as the
	// allowlisted `toggle_dictation` command. SEC-016: restricted to the
	// bubble frame so only the bubble can trigger dictation this way.
	ipcMain.on(BubbleChannels.toggleDictation, (event) => {
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
	//when the user toggles top/bottom, the previous saved
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
	// Runtime-narrow the payload. The previous `(_event, position:
	// "top" | "bottom")` annotation was compile-time only — a
	// non-matching payload would have been passed to
	// `applyBubblePosition` and silently ignored by the inner
	// `if (position === "top" || position === "bottom")` guard.
	// The runtime check makes the drop explicit at the boundary.
	ipcMain.on(BubbleChannels.setPosition, (_event, payload: unknown) => {
		if (payload !== "top" && payload !== "bottom") return;
		applyBubblePosition(payload);
	});

	ipcMain.on(BubbleChannels.ready, (event) => {
		// SEC-016: only the bubble window signals readiness.
		if (!assertFromBubble(event)) return;
		//previously set `state._bubblePageReady = true`
		// here, but `showBubbleWindow()` never consulted the
		// field — it was dead write-only state. The dead write
		// is removed here; the field definition in state.ts and
		// the reset-on-close in bubble-window.ts are owned by
		// other agents and flagged cross_file_deferred.
		// The readiness log is kept for diagnostics — operators
		// can grep the runtime log to confirm the bubble
		// renderer booted past its React mount.
		log.warn("[BUBBLE] renderer reports ready");
		// The bubble mode is now tracked at the source — see
		// `setLastKnownBubbleMode()` above (called from
		// `handle-message.ts` when `bubble_set_state` is
		// dispatched). No `webContents.send` monkey-patch is
		// needed here; the previous patch accumulated on every
		// bubble reload (wrapping the already-wrapped `send`).
	});

	//dismiss the bubble from its own '×' button. The bubble
	// preload's `dismiss()` method sends this IPC; before this handler
	// existed, the message was silently dropped by Electron's default
	// ipcMain behavior (no registered listener). Now it routes to
	// `hideBubbleWindow()` — the same path used by every other hide
	// trigger (timeout fallback, set_config, etc.), so the bubble
	// plays its exit animation and the rapid-toggle guard correctly
	// cancels any in-flight show. SEC-016: restricted to the bubble
	// frame so only the bubble can dismiss itself.
	//
	// ZU-15: when the bubble is in "recording" or "transcribing"
	// mode, dismiss first sends `toggle_dictation` to the Python
	// backend (which stops the audio pipeline) before hiding.
	// Without this, clicking ✕ while recording would vanish the
	// bubble but the finalized text would still get pasted —
	// violating the user's "stop this" intent.
	ipcMain.on(BubbleChannels.dismiss, (event) => {
		if (!assertFromBubble(event)) return;
		const mode = _lastKnownBubbleMode;
		if (mode === "recording" || mode === "transcribing") {
			void sendToPython({ type: "toggle_dictation" }).catch((err) => {
				log.warn("[BUBBLE] dismiss toggle_dictation failed:", String(err));
			});
		}
		hideBubbleWindow();
	});

	// Persistent listener for the renderer's exit-animation-complete
	// signal. The previous design called `ipcMain.once("bubble:hidden",
	// onHidden)` from inside `hideBubbleWindow()` per hide cycle (a
	// global side effect that `showBubbleWindow()` had to defensively
	// `removeAllListeners` to clear). Now this listener stays
	// installed exactly once for the whole app lifetime; the per-hide
	// callback is stored in a module-level slot in bubble-window.ts
	// (registered via `onHideAnimationComplete`) and consumed
	// atomically here. If the fallback timeout already ran and
	// cleared the slot, this event becomes a no-op (and vice versa).
	// SEC-016: restricted to the bubble frame so a compromised main
	// renderer can't fire a fake "animation complete" signal.
	ipcMain.on(BubbleChannels.hidden, (event) => {
		if (!assertFromBubble(event)) return;
		const cb = consumeHideAnimationCallback();
		if (cb) cb();
	});
}
