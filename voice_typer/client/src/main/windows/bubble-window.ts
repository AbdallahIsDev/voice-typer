/**
 * Bubble overlay BrowserWindow creation + helpers.
 *
 * Extracted from `index.ts` (REF-2). Owns:
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
import path from "node:path";
import { BrowserWindow, dialog, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../constants";
// DT-13: converted from defensive `require("../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { BUBBLE_CLR, cleanConsoleMsg, log, RESET } from "../logging";
import { state } from "../state";
import { recordBubbleRenderCrash } from "./main-window";

// PVT-068: in-session persistence of the bubble's last user-positioned
// coordinates. `null` means "no saved position — use the default
// center-on-active-display placement". Updated by the BrowserWindow
// `moved` event (see createBubbleWindow) and cleared by the
// `bubble:set-position` IPC handler (see bubble-handlers.ts) so a
// top/bottom toggle re-centers instead of stranding the bubble at the
// old y coordinate.
let savedBubblePos: { x: number; y: number } | null = null;

// Single-slot callback for the renderer's "exit animation complete"
// signal. The `bubble:hidden` IPC handler (registered once in
// bubble-handlers.ts) consumes this callback atomically. The previous
// design used `ipcMain.once("bubble:hidden", onHidden)` per hide,
// which mutated the global IPC bus — `showBubbleWindow` had to
// defensively `ipcMain.removeAllListeners("bubble:hidden")` to avoid
// stale callbacks. Concentrating the registration in a module-level
// variable removes that global side effect: the show/hide paths now
// just clear or replace the variable, and the persistent
// `bubble:hidden` listener stays installed exactly once for the whole
// app lifetime.
let currentHideAnimationCallback: (() => void) | null = null;

/**
 * Register the renderer's exit-animation-complete callback for the
 * current hide cycle. Returns an unsubscribe function that clears the
 * slot only if it still points at `cb` (defensive against a stale
 * unsubscriber firing after a newer hide cycle has already replaced
 * the callback). Called by `hideBubbleWindow()` once per hide.
 */
export function onHideAnimationComplete(cb: () => void): () => void {
	currentHideAnimationCallback = cb;
	return () => {
		if (currentHideAnimationCallback === cb) {
			currentHideAnimationCallback = null;
		}
	};
}

/**
 * Clear the current hide-animation callback unconditionally. Called by
 * `showBubbleWindow()`'s rapid-toggle guard to drop a stale pending
 * callback from an in-flight hide that's being cancelled.
 */
export function clearCurrentHideAnimationCallback(): void {
	currentHideAnimationCallback = null;
}

/**
 * Atomically retrieve AND clear the current hide-animation callback.
 * Called by the persistent `bubble:hidden` IPC handler in
 * bubble-handlers.ts so a single `bubble:hidden` event fires the
 * callback exactly once — even if the fallback timeout already ran,
 * the slot is already null and the IPC event becomes a no-op (and
 * vice versa: the timeout's `unsubscribe()` clears the slot before the
 * IPC event arrives).
 */
export function consumeHideAnimationCallback(): (() => void) | null {
	const cb = currentHideAnimationCallback;
	currentHideAnimationCallback = null;
	return cb;
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
 * `moved` handler to skip saving stale coordinates from a window that
 * ended up off-screen (e.g. after a monitor unplug) and by
 * `showBubbleWindow` to discard a saved position whose display no
 * longer exists.
 *
 * Best-effort: if `screen.getAllDisplays()` throws (headless test
 * environment), return true so the caller falls back to the existing
 * "save whatever the OS gave us" behavior.
 */
function isPositionOnAnyDisplay(pos: { x: number; y: number }): boolean {
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

/**
 * PVT-068: read the saved bubble position (if any). Exposed for IPC
 * consumers (e.g. a future Settings-page "reset position" affordance)
 * and for tests.
 */
export function getSavedBubblePosition(): { x: number; y: number } | null {
	return savedBubblePos;
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
function getActiveDisplay(): Electron.Display {
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

export function createBubbleWindow(): BrowserWindow {
	if (state.bubbleWindow && !state.bubbleWindow.isDestroyed()) {
		return state.bubbleWindow;
	}
	// PVT-068: use the multi-monitor-aware placement for the initial
	// position so the bubble appears on the screen the user is currently
	// on, not always the primary display.
	// XA-6-5: discard a saved position that no longer lies on any
	// currently-attached display (monitor-unplug safety).
	const initialPos =
		savedBubblePos && isPositionOnAnyDisplay(savedBubblePos)
			? savedBubblePos
			: centerOnActiveDisplay();
	const { x, y } = initialPos;
	// PVT-G5-080: routine lifecycle event — log.info (not console.warn).
	log.info(
		`${BUBBLE_CLR}[BUBBLE]${RESET} creating window at (${x}, ${y}) ${BUBBLE_WIDTH}x${BUBBLE_HEIGHT}`,
	);

	const win = new BrowserWindow({
		width: BUBBLE_WIDTH,
		height: BUBBLE_HEIGHT,
		x,
		y,
		show: false,
		frame: false,
		transparent: true,
		backgroundColor: "#00000000",
		resizable: false,
		minimizable: false,
		maximizable: false,
		fullscreenable: false,
		skipTaskbar: true,
		alwaysOnTop: true,
		hasShadow: false,
		focusable: false,
		webPreferences: {
			// SEC-026: dedicated bubble preload — exposes ONLY the `bubble:*`
			// IPC channels. The bubble renderer cannot invoke `python.call`
			// (which sends arbitrary commands to the Python backend) or
			// `window_.*` (which controls the main window). A compromised
			// bubble is now confined to waveform-level operations.
			preload: path.join(__dirname, "../preload/index.js"),
			contextIsolation: true,
			nodeIntegration: false,
			backgroundThrottling: false,
			// SEC-014: harden the bubble window the same way as the main
			// window.  The bubble renders waveform data and is always-on-top;
			// an XSS'd renderer hijacking it as a phishing overlay is a
			// real risk (see SEC-016 for the senderFrame check on its IPC
			// handlers).
			sandbox: true,
			webSecurity: true,
			allowRunningInsecureContent: false,
			spellcheck: false,
		},
	});

	try {
		win.setAlwaysOnTop(true, "screen-saver");
	} catch (e) {
		// PVT-G5-080: unexpected but non-fatal — log.warn.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} screen-saver failed, trying floating:`,
			e,
		);
		try {
			win.setAlwaysOnTop(true, "floating");
		} catch (e2) {
			// PVT-G5-081: secondary fallback also failed — log so
			// the bubble's always-on-top state is debuggable.
			log.warn(
				`${BUBBLE_CLR}[BUBBLE]${RESET} floating always-on-top also failed:`,
				e2,
			);
		}
	}
	// SEC-025: visibleOnFullScreen can leave the bubble painted on top of
	// exclusive fullscreen apps (games, video players) on some GPUs. We
	// only enable it when the foreground window is NOT in exclusive
	// fullscreen mode. Detection is best-effort; if we can't tell, we
	// err on the side of NOT painting over fullscreen.
	try {
		const foregroundFullscreen = isForegroundFullscreen();
		if (!foregroundFullscreen) {
			win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
		}
	} catch (e) {
		// best-effort — window may be destroyed mid-call (e.g. user
		// closed the app between the createBubbleWindow() guard and here).
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} setVisibleOnAllWorkspaces failed:`,
			e,
		);
	}

	win.webContents.on("did-fail-load", (_e, code, desc, url) => {
		// PVT-G5-080: failure — log.error.
		log.error(
			`${BUBBLE_CLR}[BUBBLE]${RESET} did-fail-load code=${code} desc=${desc} url=${url}`,
		);
	});
	win.webContents.on("did-finish-load", () => {
		// PVT-G5-080: routine lifecycle event — log.info.
		log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} did-finish-load`);
	});
	win.webContents.on("render-process-gone", (_e, details) => {
		// PVT-G5-080: failure — log.error.
		log.error(`${BUBBLE_CLR}[BUBBLE]${RESET} render-process-gone:`, details);
		// GT-10: sliding-window crash storm detection (shared with main window).
		const inStorm = recordBubbleRenderCrash();
		if (inStorm) {
			try {
				dialog.showErrorBox(
					"Voice Typer — Bubble crash loop",
					"The bubble overlay renderer has crashed repeatedly and cannot recover.\n\nPlease use the tray icon to Restart or Quit, then relaunch Voice Typer.",
				);
			} catch {
				// dialog may not be available in headless mode.
			}
			return;
		}
		// SEC-024: reload the bubble window. GT-10: 2s backoff.
		setTimeout(() => {
			try {
				if (!win.isDestroyed()) {
					log.warn(
						`${BUBBLE_CLR}[BUBBLE]${RESET} reloading after render-process-gone (2s backoff)`,
					);
					win.reload();
				}
			} catch (e) {
				log.error("[BUBBLE] failed to reload after render-process-gone:", e);
			}
		}, 2000);
	});
	win.webContents.on("preload-error", (_e, file, err) => {
		// PVT-G5-080: failure — log.error.
		log.error(`${BUBBLE_CLR}[BUBBLE]${RESET} preload-error file=${file}`, err);
	});
	// CONSOLE-FIX: new Electron console-message signature.
	//
	// PVT-G5-081 sub-finding: lower the forwarder gate from
	// `level >= 2` (WARN and above only) to `level >= 1` so INFO-
	// level renderer telemetry reaches the main process log too.
	// VERBOSE (level 0) is still dropped — too noisy for the main
	// log. Routing through the structured logger (PVT-G5-080) so
	// WARN/ERROR lines also land in electron-runtime.log.
	win.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 1) {
			const tag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			const msg = `${BUBBLE_CLR}[BUBBLE] renderer ${tag}${RESET} ${cleanConsoleMsg(e.message)} (${e.sourceId}:${e.lineNumber})`;
			if (level >= 3) log.error(msg);
			else if (level === 2) log.warn(msg);
			else log.info(msg);
		}
	});

	const loadTarget = process.env.ELECTRON_RENDERER_URL
		? `${process.env.ELECTRON_RENDERER_URL}/bubble.html`
		: path.join(__dirname, "../renderer/bubble.html");
	// PVT-G5-080: routine lifecycle event — log.info.
	log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} loading ${loadTarget}`);
	if (process.env.ELECTRON_RENDERER_URL) {
		void win.loadURL(loadTarget);
	} else {
		void win.loadFile(loadTarget);
	}

	state.bubbleWindow = win;
	win.on("closed", () => {
		// PVT-G5-080: routine lifecycle event — log.info.
		log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} closed`);
		if (state.bubbleWindow === win) state.bubbleWindow = null;
		state._bubblePageReady = false;
	});
	// PVT-068: persist the user's last drag position so the next
	// `showBubbleWindow()` restores it instead of re-centering. The
	// `moved` event fires after the user finishes dragging the
	// always-on-top pill (the pill uses a CSS `-webkit-app-region: drag`
	// region so Electron handles the drag natively). XA-6-5: skip
	// positions that are off-screen (defensive — a multi-monitor unplug
	// could leave the window stranded on a display that no longer
	// exists; saving those coords would make the bubble invisible on
	// the next show).
	win.on("moved", () => {
		try {
			if (win.isDestroyed()) return;
			const [px, py] = win.getPosition();
			const candidate = { x: px, y: py };
			if (!isPositionOnAnyDisplay(candidate)) {
				// Window ended up off-screen — don't poison the saved
				// state. The next `showBubbleWindow()` will fall back
				// to `centerOnActiveDisplay()`.
				savedBubblePos = null;
				return;
			}
			savedBubblePos = candidate;
		} catch (e) {
			// Best-effort — ignore read failures (e.g. window destroyed
			// mid-event between the isDestroyed() check and getPosition()).
			log.warn(`${BUBBLE_CLR}[BUBBLE]${RESET} 'moved' getPosition failed:`, e);
		}
	});

	// XA-6-5: when a display is removed (monitor unplug / display
	// reconfiguration), invalidate the saved bubble position so the
	// next `showBubbleWindow()` re-centers on a display that still
	// exists. Without this, the bubble would re-appear at the saved
	// coordinates — which may now be off-screen if the saved display
	// was the one that got unplugged — and the user would have no way
	// to interact with it (the bubble is `focusable: false`).
	//
	// `removeAllListeners` first ensures we don't accumulate duplicate
	// listeners across bubble window re-creations (the bubble window is
	// destroyed + re-created on render-process-gone).
	try {
		screen.removeAllListeners("display-removed");
	} catch {
		// removeAllListeners can throw in test environments where
		// `screen` is partially mocked — non-fatal.
	}
	screen.on("display-removed", () => {
		savedBubblePos = null;
		log.info(
			`${BUBBLE_CLR}[BUBBLE]${RESET} display-removed: cleared saved bubble position`,
		);
	});
	return win;
}

/**
 * Notify the bubble BrowserWindow's renderer that the active locale changed
 * so its separate JS context can re-render in the new locale without a full
 * reload. Best-effort: no-op if the bubble window is not yet created, already
 * destroyed, or its webContents is not yet ready.
 *
 * Called from `i18n:set-locale` IPC handler in `window-handlers.ts` after the
 * main-process locale bundle has been updated via `setMainLocale`.
 */
export function notifyBubbleLocaleChanged(locale: string): void {
	if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) {
		// Bubble not yet created (early startup) or already torn down.
		// The locale will be applied when the bubble is next created via
		// the preload-injected initial locale.
		return;
	}
	try {
		if (!state.bubbleWindow.webContents) {
			return;
		}
		state.bubbleWindow.webContents.send("bubble:locale-changed", locale);
	} catch (e) {
		// Best-effort — webContents may be in a transitional state
		// (e.g. mid-navigation). The renderer will pick up the new locale
		// on its next mount via the main-process locale getter.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} notifyBubbleLocaleChanged failed:`,
			e,
		);
	}
}

export function showBubbleWindow(): void {
	if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) {
		createBubbleWindow();
	}
	const win = state.bubbleWindow;
	if (!win) {
		// PVT-G5-080: failure — log.error.
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
		// cleared. PVT-G5-081: log on failure so a stuck callback is
		// debuggable instead of silently swallowed.
		try {
			clearCurrentHideAnimationCallback();
		} catch (err) {
			log.warn(
				`${BUBBLE_CLR}[BUBBLE]${RESET} clearCurrentHideAnimationCallback() failed:`,
				err,
			);
		}
	}

	// PVT-068: restore the user's last drag position if we have one;
	// otherwise fall back to multi-monitor-aware centering on the
	// display the user is currently on. Previously this always called
	// `centerOnPrimaryDisplay()`, which stranded the bubble on the
	// primary screen when the user was working on a secondary monitor
	// AND blew away the user's last drag position on every show.
	//
	// XA-6-5: discard the saved position if it no longer lies on any
	// currently-attached display (the saved display may have been
	// unplugged since the position was saved). The `display-removed`
	// listener also clears it on unplug, but this is the defensive
	// second line for the case where the app was offline during the
	// unplug event.
	const savedPos =
		savedBubblePos && isPositionOnAnyDisplay(savedBubblePos)
			? savedBubblePos
			: null;
	const c = savedPos ?? centerOnActiveDisplay();
	win.setBounds({ x: c.x, y: c.y, width: BUBBLE_WIDTH, height: BUBBLE_HEIGHT });

	try {
		win.setAlwaysOnTop(true, "screen-saver");
	} catch (e) {
		// best-effort — window may be destroyed mid-call.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} showBubbleWindow setAlwaysOnTop failed:`,
			e,
		);
	}
	// SEC-025: conditionally enable visibleOnFullScreen based on
	// foreground fullscreen state.
	try {
		if (!isForegroundFullscreen()) {
			win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
		}
	} catch (e) {
		// best-effort — window may be destroyed mid-call.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} showBubbleWindow setVisibleOnAllWorkspaces failed:`,
			e,
		);
	}

	try {
		if (!win.isVisible()) {
			win.show();
		}
		// Signal the renderer to reset its closing state and play enter animation
		win.webContents.send("bubble:show");
		// Sync the current draggable state on every show (handles initial state
		// and ensures the bubble renderer is always in sync with the backend)
		win.webContents.send("bubble:draggable", state.bubbleDraggable);
	} catch (e) {
		// PVT-G5-080: failure — log.error.
		log.error(`${BUBBLE_CLR}[BUBBLE]${RESET} show() failed:`, e);
	}
	try {
		win.moveTop();
	} catch (e) {
		// best-effort — window may be destroyed mid-call.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} showBubbleWindow moveTop failed:`,
			e,
		);
	}
	try {
		win.setAlwaysOnTop(true, "screen-saver");
	} catch (e) {
		// best-effort — window may be destroyed mid-call.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} showBubbleWindow re-affirm setAlwaysOnTop failed:`,
			e,
		);
	}

	setImmediate(() => {
		if (!win || win.isDestroyed()) return;
		if (!win.isVisible()) {
			// PVT-G5-080: unexpected but non-fatal — log.warn.
			log.warn(
				`${BUBBLE_CLR}[BUBBLE]${RESET} not visible after show() -- retrying`,
			);
			try {
				win.show();
			} catch (e) {
				// best-effort — window may be destroyed mid-call.
				log.warn(
					`${BUBBLE_CLR}[BUBBLE]${RESET} setImmediate retry show() failed:`,
					e,
				);
			}
			try {
				win.moveTop();
			} catch (e) {
				// best-effort — window may be destroyed mid-call.
				log.warn(
					`${BUBBLE_CLR}[BUBBLE]${RESET} setImmediate retry moveTop failed:`,
					e,
				);
			}
			try {
				win.setAlwaysOnTop(true, "screen-saver");
			} catch (e) {
				// best-effort — window may be destroyed mid-call.
				log.warn(
					`${BUBBLE_CLR}[BUBBLE]${RESET} setImmediate retry setAlwaysOnTop failed:`,
					e,
				);
			}
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
	// PVT-G5-081: log on failure so a dead webContents doesn't silently
	// leave the bubble stuck visible.
	try {
		win.webContents.send("bubble:hide");
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
				// PVT-G5-080: routine lifecycle event — log.info.
				log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} hidden (animated)`);
			}
		} catch (err) {
			// PVT-G5-081: outer hide-animated failure — log so a
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
				// PVT-G5-080: routine lifecycle event — log.info.
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
			// PVT-G5-081: outer hide-fallback failure — log so a
			// stuck-visible bubble is debuggable instead of silent.
			log.warn(`${BUBBLE_CLR}[BUBBLE]${RESET} hide fallback failed:`, err);
		}
	}, 300);
}
