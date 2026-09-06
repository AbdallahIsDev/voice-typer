/**
 * Bubble-window IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - bubble:draggable — toggle draggability (synced to bubble renderer)
 *   - bubble:resize — fit pill content exactly (clamped to min/max)
 *   - bubble:show-from-renderer — show from the bubble's own UI
 *   - bubble:set-position — top/bottom config (synced to bubble renderer)
 *     (Channel-rename: previously `set_bubble_position` (snake_case);
 *     migrated to `bubble:set-position` to match the bubble:* convention.
 *     The legacy listener was removed once the preload files were migrated.)
 *   - bubble:toggle-dictation — bubble mic button → Python backend
 *   - bubble:ready — renderer readiness signal
 *   - bubble:dismiss — '×' button (cancel-then-hide; the same body is
 *     shared with the main-process global dismiss shortcut)
 *   - bubble:hidden — exit-animation-complete signal
 *
 * The `bubble:move-by` keyboard-nudge handler was removed — it had no
 * production caller (the bubble window is `focusable: false`, so the
 * renderer-side keydown handler that fed this channel was dead code).
 *
 * SEC-016: `assertFromBubble()` rejects IPC messages not coming from the
 * bubble window's webContents, so a compromised main window can't
 * hijack the always-on-top bubble as a phishing overlay.
 */
import { ipcMain } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../constants";
//converted from defensive `require("../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { log } from "../logging";
import { sendToPython } from "../python";
import { state } from "../state";
import {
	cancelScheduledDurablePersist,
	centerOnActiveDisplay,
	consumeHideAnimationCallback,
	hideBubbleWindow,
	resetSavedBubblePosition,
	showBubbleWindow,
	suppressDurablePersistFor,
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
 *
 * The `channel` argument is included in the rejection log so operators
 * can grep the runtime log to identify WHICH bubble IPC was rejected
 * (previously the rejection was silent — a misconfigured preload or a
 * hostile renderer could send packets that were silently dropped, with
 * no log trail to diagnose). The `senderUrl` field surfaces the origin
 * frame's URL so a cross-origin attempt is visible at diagnosis time.
 */
function assertFromBubble(
	event: Electron.IpcMainEvent,
	channel: string,
): boolean {
	if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) {
		log.warn("bubble IPC rejected: no bubble window", {
			channel,
			senderUrl: event.senderFrame?.url,
		});
		return false;
	}
	// Compare senderFrame to the bubble window's main frame.  Electron
	// exposes event.senderFrame (an Electron.WebFrameMain) which is the
	// origin of the IPC message.
	if (event.senderFrame !== state.bubbleWindow.webContents.mainFrame) {
		log.warn("bubble IPC rejected: senderFrame mismatch", {
			channel,
			senderUrl: event.senderFrame?.url,
		});
		return false;
	}
	return true;
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

/**
 * Cancel-then-hide body shared by the `bubble:dismiss` IPC handler and
 * the main-process global shortcut (see
 * `shortcuts/global-shortcuts.ts`). Extracted from the IPC handler so
 * a keyboard-triggered dismiss routes through EXACTLY the same
 * semantics as the bubble's own '×' button.
 *
 * When the bubble is in "recording" or "transcribing" mode, dismiss
 * first sends `toggle_dictation` to the Python backend (which stops
 * the audio pipeline) before hiding. Without this, dismissing while
 * recording would vanish the bubble but the finalized text would still
 * get pasted — violating the user's "stop this" intent.
 *
 * Idempotency: `toggle_dictation` is non-idempotent (a second toggle
 * re-starts recording). A rapid double-dismiss would fire two
 * toggle_dictation calls — the first stops the recording, the second
 * starts a new one. Clear the cached mode to "idle" immediately AFTER
 * firing the dismiss-triggered toggle so a second dismiss sees "idle"
 * and skips the toggle. The next `bubble_set_state` push from the
 * backend (after the toggle takes effect) overwrites this with the
 * actual new mode.
 *
 * The Python backend's structured `_error` reply envelope is inspected
 * on resolve (see the `bubble:toggle-dictation` handler for the same
 * pattern) so a backend-side failure is logged for diagnosis instead
 * of being silently swallowed.
 */
export function dismissAndHideBubble(): void {
	const mode = _lastKnownBubbleMode;
	if (mode === "recording" || mode === "transcribing") {
		void sendToPython({ type: "toggle_dictation" })
			.then((resp) => {
				if (resp && typeof resp === "object" && "_error" in resp) {
					log.warn("[BUBBLE] dismiss toggle_dictation backend error", resp);
				}
			})
			.catch((err) => {
				log.warn("[BUBBLE] dismiss toggle_dictation failed:", String(err));
			});
		// Clear the cached mode so a rapid second dismiss
		// sees "idle" and skips the toggle (non-idempotent:
		// a second toggle would re-start recording).
		_lastKnownBubbleMode = "idle";
	}
	hideBubbleWindow();
}

export function registerBubbleHandlers(): void {
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
		if (!assertFromBubble(event, BubbleChannels.resize)) return;
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
		if (!assertFromBubble(event, BubbleChannels.showFromRenderer)) return;
		showBubbleWindow();
	});

	//toggle dictation from the bubble's mic button. The bubble
	// renderer is sandboxed (SEC-026) and has NO `python.call`, so it
	// cannot invoke `toggle_dictation` directly. This channel is the
	// single-purpose bridge: the bubble sends `bubble:toggle-dictation`,
	// the main process forwards it to the Python backend as the
	// allowlisted `toggle_dictation` command. SEC-016: restricted to the
	// bubble frame so only the bubble can trigger dictation this way.
	//
	// The Python backend's reply is a Promise<unknown> — `sendToPython`
	// RESOLVES even when the backend returns a structured `{ _error, _code }`
	// envelope (the rejection path is reserved for transport-level
	// failures: TCP disconnect, timeout, disallowed command). Inspect the
	// resolved value for the `_error` key so a backend-side failure (e.g.
	// audio device unavailable, model load error) is logged for diagnosis
	// instead of being silently swallowed by the `.catch`-only handler.
	ipcMain.on(BubbleChannels.toggleDictation, (event) => {
		if (!assertFromBubble(event, BubbleChannels.toggleDictation)) return;
		// `toggle_dictation` is in ALLOWED_COMMANDS, so this is a
		// sanctioned backend call (never an arbitrary command).
		void sendToPython({ type: "toggle_dictation" })
			.then((resp) => {
				if (resp && typeof resp === "object" && "_error" in resp) {
					log.warn("[BUBBLE] toggle_dictation backend error", resp);
				}
			})
			.catch((err) => {
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
	//
	// Durable half of the reset: the Python side clears
	// `bubble_x` / `bubble_y` server-side when this same toggle's
	// set_config lands there. Locally we (a) cancel any pending debounced
	// persist so a stale drag write can't race that reset and (b)
	// suppress persists around our own programmatic reposition so its
	// `moved` events don't rewrite the freshly-cleared config with the
	// centered coordinates.
	const applyBubblePosition = (position: "top" | "bottom") => {
		if (position === "top" || position === "bottom") {
			state.bubblePosition = position;
			resetSavedBubblePosition();
			cancelScheduledDurablePersist();
			suppressDurablePersistFor();
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
		if (!assertFromBubble(event, BubbleChannels.ready)) return;
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
	ipcMain.on(BubbleChannels.dismiss, (event) => {
		if (!assertFromBubble(event, BubbleChannels.dismiss)) return;
		dismissAndHideBubble();
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
		if (!assertFromBubble(event, BubbleChannels.hidden)) return;
		const cb = consumeHideAnimationCallback();
		if (cb) cb();
	});
}
