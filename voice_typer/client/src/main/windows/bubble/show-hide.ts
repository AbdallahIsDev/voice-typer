/**
 * Bubble overlay show/hide with rapid-toggle guard + renderer-driven
 * exit animation ( extract from `bubble-window.ts`).
 *
 * Owns:
 *   - `showBubbleWindow()` — center/restore position, raise the
 *     always-on-top pill, signal the renderer to play its enter
 *     animation, and (via `setImmediate`) re-affirm visibility +
 *     z-order in case the OS hid the window between `show()` and
 *     the next tick.
 *   - `hideBubbleWindow()` — ask the renderer to play its exit
 *     animation, register a single-shot hide callback via
 *     `onHideAnimationComplete`, and arm a 300ms fallback timeout
 *     that hides the window directly if the renderer never signals
 *     back (e.g. it crashed mid-animation).
 *
 * : both functions were inlined in `bubble-window.ts`. They are
 * the largest methods in the module and together carry all 7+
 * try/catches that defend against the window being destroyed
 * mid-call (the bubble is re-created on render-process-gone, so
 * `state.bubbleWindow` can flip to a destroyed window between an
 * `isDestroyed()` check and the next Electron API call). All
 * try/catches and the animation + fallback state machine are
 * preserved verbatim.
 *
 *  / : every try/catch routes through the
 * structured `log` so failures persist in `electron-main.log`
 * (5 MiB rotation) instead of being lost in packaged builds where
 * `console.warn` has no terminal.
 */
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../../constants";
import { BubbleChannels } from "../../ipc/channels";
//converted from defensive `require("../../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { BUBBLE_CLR, log, RESET } from "../../logging";
import { state } from "../../state";
import {
	clearCurrentHideAnimationCallback,
	onHideAnimationComplete,
} from "./hide-animation";
import { createBubbleWindow } from "./lifecycle";
import {
	centerOnActiveDisplay,
	isForegroundFullscreen,
	resolveRestoredBubblePosition,
	suppressDurablePersistFor,
} from "./positioning";

/**
 * Run a best-effort window operation inside a try/catch and route the
 * failure through the structured `log` with a consistent `[BUBBLE]` tag +
 * caller-supplied label. The bubble is re-created on `render-process-gone`,
 * so `state.bubbleWindow` can flip to a destroyed window between an
 * `isDestroyed()` check and the next Electron API call — every win-op
 * call site is best-effort and must not throw to the caller.
 *
 * replaces the per-call-site `try { … } catch (e) { log.warn(...) }`
 * boilerplate that previously grew to 7+ near-identical blocks inside
 * `showBubbleWindow`. The default log level is `warn` (the dominant case
 * for best-effort retries); callers that need `error` (e.g. `show()` itself
 * — if showing the window fails, the bubble never appears) pass
 * `{ level: "error" }`.
 */
function _tryWinOp(
	label: string,
	fn: () => void,
	options: { level?: "warn" | "error" } = {},
): void {
	try {
		fn();
	} catch (e) {
		const level = options.level ?? "warn";
		log[level](`${BUBBLE_CLR}[BUBBLE]${RESET} ${label} failed:`, e);
	}
}

export function showBubbleWindow(): void {
	if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) {
		createBubbleWindow();
	}
	const win = state.bubbleWindow;
	if (!win) {
		//failure — log.error.
		log.error(
			`${BUBBLE_CLR}[BUBBLE]${RESET} showBubbleWindow: no window to show`,
		);
		return;
	}

	// Rapid-toggle guard: cancel any pending hide timeout/animation so the
	// bubble doesn't flicker when show is called while a hide is in flight.
	if (state._hideTimeout) {
		clearTimeout(state._hideTimeout);
		state._hideTimeout = null;
		// Drop the pending hide-animation callback so a stale
		// renderer "bubble:hidden" signal can't fire `onHidden` after
		// the show has already started. The persistent IPC listener
		// in bubble-handlers.ts stays installed; only this slot is
		//cleared. : log on failure so a stuck callback is
		// debuggable instead of silently swallowed.
		_tryWinOp("clearCurrentHideAnimationCallback()", () => {
			clearCurrentHideAnimationCallback();
		});
	}

	//restore the user's last drag position if we have one;
	// otherwise fall back to multi-monitor-aware centering on the
	// display the user is currently on. Previously this always called
	// `centerOnPrimaryDisplay()`, which stranded the bubble on the primary
	// screen when the user was working on a secondary monitor
	// AND blew away the user's last drag position on every show.
	//
	//discard a restored position if it no longer lies on any
	// currently-attached display (the saved display may have been
	// unplugged since the position was saved). The `display-removed`
	// listener also clears it on unplug, but this is the defensive
	// second line for the case where the app was offline during the
	// unplug event.
	//
	// The restore source prefers the in-session drag position and falls
	// back to the durable pair from the Python config, so a drag
	// survives an app restart.
	// This setBounds is a PROGRAMMATIC placement — suppress the debounced
	// durable persist around it so the `moved` events it emits are never
	// mistaken for a fresh user drag (they would rewrite the config with
	// coordinates we just restored from it).
	suppressDurablePersistFor();
	const restored = resolveRestoredBubblePosition();
	const fallback = restored ?? centerOnActiveDisplay();
	win.setBounds({
		x: fallback.x,
		y: fallback.y,
		width: BUBBLE_WIDTH,
		height: BUBBLE_HEIGHT,
	});

	_tryWinOp("showBubbleWindow setAlwaysOnTop", () => {
		win.setAlwaysOnTop(true, "screen-saver");
	});
	// SEC-025: conditionally enable visibleOnFullScreen based on
	// foreground fullscreen state.
	_tryWinOp("showBubbleWindow setVisibleOnAllWorkspaces", () => {
		if (!isForegroundFullscreen()) {
			win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
		}
	});

	_tryWinOp(
		"show()",
		() => {
			if (!win.isVisible()) {
				win.show();
			}
			// Signal the renderer to reset its closing state and play enter animation
			win.webContents.send(BubbleChannels.show);
			// Sync the current draggable state on every show (handles initial state
			// and ensures the bubble renderer is always in sync with the backend)
			win.webContents.send(BubbleChannels.draggable, state.bubbleDraggable);
		},
		{ level: "error" },
	);
	_tryWinOp("showBubbleWindow moveTop", () => {
		win.moveTop();
	});
	//dropped the second redundant setAlwaysOnTop call that
	// previously re-affirmed the always-on-top flag immediately after
	// moveTop(). The first call above (before show()) already set the
	// flag, and the setImmediate fallback below re-applies it IF
	// `!win.isVisible()` (defensive retry on the unhappy path only).
	// The redundant happy-path call was a Win32/Cocoa round-trip with
	// no behavioral benefit on every dictation start.

	setImmediate(() => {
		if (!win || win.isDestroyed()) return;
		if (!win.isVisible()) {
			//unexpected but non-fatal — log.warn.
			log.warn(
				`${BUBBLE_CLR}[BUBBLE]${RESET} not visible after show() -- retrying`,
			);
			_tryWinOp("setImmediate retry show()", () => {
				win.show();
			});
			_tryWinOp("setImmediate retry moveTop", () => {
				win.moveTop();
			});
			_tryWinOp("setImmediate retry setAlwaysOnTop", () => {
				win.setAlwaysOnTop(true, "screen-saver");
			});
		}
	});
}

export function hideBubbleWindow(): void {
	const win = state.bubbleWindow;
	if (!win || win.isDestroyed() || !win.isVisible()) return;

	// Rapid-toggle guard: cancel any previous hide timeout to avoid overlap.
	if (state._hideTimeout) {
		clearTimeout(state._hideTimeout);
		state._hideTimeout = null;
	}

	// Send hide animation event to the renderer, then wait for it to
	// signal back before actually hiding the window.
	//log on failure so a dead webContents doesn't silently
	// leave the bubble stuck visible.
	try {
		win.webContents.send(BubbleChannels.hide);
	} catch (err) {
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} webContents.send('bubble:hide') failed:`,
			err,
		);
	}

	// Listen for the renderer's animation-complete signal (once per hide).
	// The persistent `bubble:hidden` IPC listener in bubble-handlers.ts
	// consumes this callback via `consumeHideAnimationCallback()` when the
	// renderer signals it. Storing the callback in a module-level slot
	// (instead of registering a fresh `ipcMain.once` listener per hide)
	// avoids mutating the global IPC bus on every show/hide cycle.
	const onHidden = () => {
		if (state._hideTimeout) {
			clearTimeout(state._hideTimeout);
			state._hideTimeout = null;
		}
		try {
			if (!win.isDestroyed()) {
				win.hide();
				//routine lifecycle event — log.info.
				log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} hidden (animated)`);
			}
		} catch (err) {
			//outer hide-animated failure — log so a
			// stuck-visible bubble is debuggable instead of silent.
			log.warn(`${BUBBLE_CLR}[BUBBLE]${RESET} hide animated failed:`, err);
		}
	};
	const unsubscribe = onHideAnimationComplete(onHidden);

	// Use a timeout as fallback in case the renderer is unresponsive.
	state._hideTimeout = setTimeout(() => {
		state._hideTimeout = null;
		try {
			if (!win.isDestroyed() && win.isVisible()) {
				// Drop the pending hide-animation callback
				// BEFORE calling `win.hide()`. Previously the
				// fallback timeout only called `win.hide()` and
				// left the `ipcMain.once("bubble:hidden", …)`
				// listener registered. If the renderer later
				// DID emit `bubble:hidden` (e.g. it was just
				// slow, not dead), the `onHidden` callback
				// fired on an already-hidden window. Calling
				// `unsubscribe()` here clears the slot so the
				// persistent IPC listener becomes a no-op for
				// this stale signal.
				try {
					unsubscribe();
				} catch (e) {
					/* slot already cleared or replaced */
					log.warn(
						`${BUBBLE_CLR}[BUBBLE]${RESET} unsubscribe hide-callback pre-hide failed:`,
						e,
					);
				}
				win.hide();
				//routine lifecycle event — log.info.
				log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} hidden (fallback)`);
			} else {
				// Window is already hidden or destroyed — still
				// clear the slot so a stale callback can't fire.
				try {
					unsubscribe();
				} catch (e) {
					/* slot already cleared or replaced */
					log.warn(
						`${BUBBLE_CLR}[BUBBLE]${RESET} unsubscribe hide-callback post-hide failed:`,
						e,
					);
				}
			}
		} catch (err) {
			//outer hide-fallback failure — log so a
			// stuck-visible bubble is debuggable instead of silent.
			log.warn(`${BUBBLE_CLR}[BUBBLE]${RESET} hide fallback failed:`, err);
		}
	}, 300);
}
