/**
 * Bubble overlay BrowserWindow creation + webContents lifecycle
 * handlers ( extract from `bubble-window.ts`).
 *
 * Owns:
 *   - `createBubbleWindow()` — lazy-creates the always-on-top
 *     transparent pill and wires the 5 webContents event handlers
 *     (`did-fail-load`, `did-finish-load`, `render-process-gone`,
 *     `preload-error`, `console-message`).
 *   - `notifyBubbleLocaleChanged(locale)` — forwards locale changes
 *     to the bubble renderer's separate JS context.
 *
 * : the original `createBubbleWindow` body lived inline in
 * `bubble-window.ts`. The `console-message` handler is now routed
 * through the shared `attachConsoleForwarder` helper ( sub-
 * finding 1-B-10) and the `render-process-gone` storm detection is
 * now backed by `createCrashStormTracker` ( sub-finding 1-B-11)
 * instead of the `recordBubbleRenderCrash` import from
 * `main-window.ts`. Both substitutions are behavior-preserving: the
 * log messages, threshold (5), window (60s), and 2s reload backoff
 * are identical to the legacy implementation.
 */
import path from "node:path";
import { BrowserWindow, dialog, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../../constants";
import { BubbleChannels } from "../../ipc/channels";
//converted from defensive `require("../../logging")` to a static
// ESM import — the previous try/catch + console.* fallback was added
// to tolerate minimal test mocks, but the real logging module is now
// always present and the test mocks have been updated to expose `log`.
import { BUBBLE_CLR, log, RESET } from "../../logging";
import { state } from "../../state";
import { attachConsoleForwarder } from "./console-forwarder";
//sliding 60s window; if >5 crashes land in that window, stop
// reloading and show a recovery dialog. Threshold + window length
// match the legacy `RENDER_CRASH_THRESHOLD` / `RENDER_CRASH_WINDOW_MS`
// constants previously defined in `main-window.ts`.
import { createCrashStormTracker } from "./crash-storm";
import {
	centerOnActiveDisplay,
	getSavedBubblePosition,
	isForegroundFullscreen,
	isPositionOnAnyDisplay,
	setSavedBubblePosition,
} from "./positioning";

const bubbleCrashTracker = createCrashStormTracker("Bubble", 5, 60_000);

// Tracked handle for the `screen.on("display-removed", ...)` listener so it
// can be removed by reference (via `screen.off`) instead of the too-aggressive
// `screen.removeAllListeners("display-removed")` anti-pattern that used to
// evict listeners registered by other parts of the app.
let _displayRemovedHandler: (() => void) | null = null;

/**
 * Register (or re-register) the bubble's `display-removed` listener using a
 * tracked-handle pattern. If a handler is already tracked, it is first
 * detached via `screen.off("display-removed", handler)` so exactly ONE bubble
 * listener is ever registered — even across bubble window re-creations (e.g.
 * render-process-gone destroy + re-create).
 */
function attachDisplayRemovedHandler(): void {
	if (_displayRemovedHandler) {
		try {
			screen.off("display-removed", _displayRemovedHandler);
		} catch {
			// Best-effort — screen may be partially mocked in tests.
		}
		_displayRemovedHandler = null;
	}
	const handler = () => {
		setSavedBubblePosition(null);
		log.info(
			`${BUBBLE_CLR}[BUBBLE]${RESET} display-removed: cleared saved bubble position`,
		);
	};
	screen.on("display-removed", handler);
	_displayRemovedHandler = handler;
}

/**
 * Detach the tracked `display-removed` listener (if any) via `screen.off` and
 * clear the slot. No-op when nothing is registered.
 */
export function detachDisplayRemovedHandler(): void {
	if (!_displayRemovedHandler) return;
	try {
		screen.off("display-removed", _displayRemovedHandler);
	} catch {
		// Best-effort — screen may be partially mocked in tests.
	}
	_displayRemovedHandler = null;
}

/**
 * Test-only accessor for the currently tracked `display-removed` handler.
 */
export function __getDisplayRemovedHandlerForTest(): (() => void) | null {
	return _displayRemovedHandler;
}

export function createBubbleWindow(): BrowserWindow {
	if (state.bubbleWindow && !state.bubbleWindow.isDestroyed()) {
		return state.bubbleWindow;
	}
	//use the multi-monitor-aware placement for the initial
	// position so the bubble appears on the screen the user is currently
	// on, not always the primary display.
	//discard a saved position that no longer lies on any
	// currently-attached display (monitor-unplug safety).
	// `getSavedBubblePosition()` reads the live binding owned by
	// positioning.ts (writes go through `setSavedBubblePosition()` so
	// the mutation stays inside the owning module — ES module live
	// bindings only allow the exporting module to reassign).
	const savedPos = getSavedBubblePosition();
	const initialPos =
		savedPos && isPositionOnAnyDisplay(savedPos)
			? savedPos
			: centerOnActiveDisplay();
	const { x, y } = initialPos;
	//routine lifecycle event — log.info (not console.warn).
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
		//unexpected but non-fatal — log.warn.
		log.warn(
			`${BUBBLE_CLR}[BUBBLE]${RESET} screen-saver failed, trying floating:`,
			e,
		);
		try {
			win.setAlwaysOnTop(true, "floating");
		} catch (e2) {
			//secondary fallback also failed — log so
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
		//failure — log.error.
		log.error(
			`${BUBBLE_CLR}[BUBBLE]${RESET} did-fail-load code=${code} desc=${desc} url=${url}`,
		);
	});
	win.webContents.on("did-finish-load", () => {
		//routine lifecycle event — log.info.
		log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} did-finish-load`);
	});
	win.webContents.on("render-process-gone", (_e, details) => {
		//failure — log.error.
		log.error(`${BUBBLE_CLR}[BUBBLE]${RESET} render-process-gone:`, details);
		//sliding-window crash storm detection (shared with main window).
		const inStorm = bubbleCrashTracker.record();
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
		//SEC-024: reload the bubble window. : 2s backoff.
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
		//failure — log.error.
		log.error(`${BUBBLE_CLR}[BUBBLE]${RESET} preload-error file=${file}`, err);
	});
	// CONSOLE-FIX: console-message forwarder is now installed via the
	//shared `attachConsoleForwarder` helper ( sub-finding 1-B-10).
	// The level-routing (level >= 1 gate, INFO/WARN/ERROR routing
	// through the structured logger) is preserved exactly — see
	// `console-forwarder.ts` for the rationale comments that used to
	// live inline here.
	attachConsoleForwarder(win, {
		tag: "[BUBBLE] renderer",
		colorPrefix: BUBBLE_CLR,
	});

	const loadTarget = process.env.ELECTRON_RENDERER_URL
		? `${process.env.ELECTRON_RENDERER_URL}/bubble.html`
		: path.join(__dirname, "../renderer/bubble.html");
	//routine lifecycle event — log.info.
	log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} loading ${loadTarget}`);
	if (process.env.ELECTRON_RENDERER_URL) {
		void win.loadURL(loadTarget);
	} else {
		void win.loadFile(loadTarget);
	}

	state.bubbleWindow = win;
	win.on("closed", () => {
		//routine lifecycle event — log.info.
		log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} closed`);
		if (state.bubbleWindow === win) state.bubbleWindow = null;
		state._bubblePageReady = false;
		// Clean up the tracked `display-removed` listener (by reference) so
		// the bubble never leaks listeners after teardown.
		detachDisplayRemovedHandler();
	});
	//persist the user's last drag position so the next
	// `showBubbleWindow()` restores it instead of re-centering. The
	// `moved` event fires after the user finishes dragging the
	// always-on-top pill (the pill uses a CSS `-webkit-app-region: drag`
	//region so Electron handles the drag natively). : skip
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
				setSavedBubblePosition(null);
				return;
			}
			setSavedBubblePosition(candidate);
		} catch (e) {
			// Best-effort — ignore read failures (e.g. window destroyed
			// mid-event between the isDestroyed() check and getPosition()).
			log.warn(`${BUBBLE_CLR}[BUBBLE]${RESET} 'moved' getPosition failed:`, e);
		}
	});

	//when a display is removed (monitor unplug / display
	// reconfiguration), invalidate the saved bubble position so the
	// next `showBubbleWindow()` re-centers on a display that still
	// exists. Without this, the bubble would re-appear at the saved
	// coordinates — which may now be off-screen if the saved display
	// was the one that got unplugged — and the user would have no way
	// to interact with it (the bubble is `focusable: false`).
	//
	// The tracked-handle pattern (attach + screen.off by reference)
	// ensures we never accumulate duplicate bubble listeners across
	// window re-creations (destroy + re-create on render-process-gone)
	// WITHOUT evicting `display-removed` listeners registered by other
	// parts of the app (the old `screen.removeAllListeners` approach).
	attachDisplayRemovedHandler();
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
		state.bubbleWindow.webContents.send(BubbleChannels.localeChanged, locale);
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
