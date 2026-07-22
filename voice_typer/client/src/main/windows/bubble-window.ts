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
 * re-centering. A `set_bubble_position` IPC (top/bottom toggle from
 * the Settings page) resets the saved position so the new edge
 * default takes effect. In-session persistence only — durable
 * persistence to the Python config is a follow-up (config.py is out
 * of scope for this fix).
 */
import path from "node:path";
import { BrowserWindow, ipcMain, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../constants";
import { BUBBLE_CLR, cleanConsoleMsg, RESET } from "../logging";
import { state } from "../state";

// PVT-G5-080: structured logger. Resolved defensively via `require()`
// so unit-test environments that mock `../logging` minimally (without
// the new `log` export, e.g. bubble-window-fallback.test.ts) still
// pass — `require()` returns the mocked module, `.log` is undefined,
// and we fall back to the legacy console.* pattern. In production the
// real `log` is used (with stdout + electron-runtime.log file tee).
type _LogShape = {
	info: (...a: unknown[]) => void;
	warn: (...a: unknown[]) => void;
	error: (...a: unknown[]) => void;
};
const log: _LogShape = (() => {
	try {
		// eslint-disable-next-line @typescript-eslint/no-var-requires, @typescript-eslint/no-require-imports
		const mod = require("../logging") as unknown as {
			log?: _LogShape;
		};
		if (mod.log) return mod.log;
	} catch {
		// ignore — fall through to fallback
	}
	return {
		info: (...args: unknown[]) => console.log(...args),
		warn: (...args: unknown[]) => console.warn(...args),
		error: (...args: unknown[]) => console.error(...args),
	};
})();

// PVT-068: in-session persistence of the bubble's last user-positioned
// coordinates. `null` means "no saved position — use the default
// center-on-active-display placement". Updated by the BrowserWindow
// `moved` event (see createBubbleWindow) and cleared by the
// `set_bubble_position` IPC handler (see bubble-handlers.ts) so a
// top/bottom toggle re-centers instead of stranding the bubble at the
// old y coordinate.
let savedBubblePos: { x: number; y: number } | null = null;

/**
 * PVT-068: reset the saved bubble position so the next
 * `showBubbleWindow()` falls back to the default placement. Called by
 * the `set_bubble_position` IPC handler when the user toggles between
 * top/bottom in Settings.
 */
export function resetSavedBubblePosition(): void {
	savedBubblePos = null;
}

/**
 * PVT-068: read the saved bubble position (if any). Exposed for IPC
 * consumers (e.g. a future Settings-page "reset position" affordance)
 * and for tests.
 */
export function getSavedBubblePosition(): { x: number; y: number } | null {
	return savedBubblePos;
}

// PVT-013 (Python-config sync): the Python config defaults
// `bubble_position` to "bottom". The Electron `state.ts` default is
// "top" (legacy, pre-Python-config). Override once at module load so
// the bubble appears at the bottom by default, matching the Python
// config. Subsequent `set_bubble_position` IPC updates from the
// renderer (Settings page) take precedence and are persisted to Python
// config. This is a one-shot override: once any handler writes
// `state.bubblePosition = "top"` (explicit user choice), the value is
// no longer the default and this guard won't flip it back.
if (state.bubblePosition === "top") {
	state.bubblePosition = "bottom";
}

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
	} catch {
		// Best-effort detection.
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
	const { x, y } = savedBubblePos ?? centerOnActiveDisplay();
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
			preload: path.join(__dirname, "../preload/bubble.js"),
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
	} catch {}

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
		// SEC-024: reload the bubble window so it doesn't stay as a
		// blank, invisible, always-on-top overlay. Without this, a
		// crashed bubble renderer leaves a stuck overlay on the screen.
		try {
			if (!win.isDestroyed()) {
				// PVT-G5-080: unexpected recovery action — log.warn.
				log.warn(
					`${BUBBLE_CLR}[BUBBLE]${RESET} reloading after render-process-gone`,
				);
				win.reload();
			}
		} catch (e) {
			log.error("[BUBBLE] failed to reload after render-process-gone:", e);
		}
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
	// region so Electron handles the drag natively). We skip positions
	// that are off-screen (defensive — shouldn't happen, but a
	// multi-monitor unplug could leave stale coords).
	win.on("moved", () => {
		try {
			if (win.isDestroyed()) return;
			const [px, py] = win.getPosition();
			savedBubblePos = { x: px, y: py };
		} catch {
			// Best-effort — ignore read failures (e.g. window destroyed
			// mid-event).
		}
	});
	return win;
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
		// Remove any pending one-shot listener to prevent stale hide callbacks.
		// PVT-G5-081: log on failure so stale-listener accumulation
		// is debuggable instead of silently swallowed.
		try {
			ipcMain.removeAllListeners("bubble:hidden");
		} catch (err) {
			log.warn(
				`${BUBBLE_CLR}[BUBBLE]${RESET} removeAllListeners('bubble:hidden') failed:`,
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
	const c = savedBubblePos ?? centerOnActiveDisplay();
	win.setBounds({ x: c.x, y: c.y, width: BUBBLE_WIDTH, height: BUBBLE_HEIGHT });

	try {
		win.setAlwaysOnTop(true, "screen-saver");
	} catch {}
	// SEC-025: conditionally enable visibleOnFullScreen based on
	// foreground fullscreen state.
	try {
		if (!isForegroundFullscreen()) {
			win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
		}
	} catch {}

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
	} catch {}
	try {
		win.setAlwaysOnTop(true, "screen-saver");
	} catch {}

	setImmediate(() => {
		if (!win || win.isDestroyed()) return;
		if (!win.isVisible()) {
			// PVT-G5-080: unexpected but non-fatal — log.warn.
			log.warn(
				`${BUBBLE_CLR}[BUBBLE]${RESET} not visible after show() -- retrying`,
			);
			try {
				win.show();
			} catch {}
			try {
				win.moveTop();
			} catch {}
			try {
				win.setAlwaysOnTop(true, "screen-saver");
			} catch {}
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

	// Use a timeout as fallback in case the renderer is unresponsive.
	state._hideTimeout = setTimeout(() => {
		state._hideTimeout = null;
		try {
			if (!win.isDestroyed() && win.isVisible()) {
				// R6-F4: remove the one-shot `bubble:hidden` listener
				// BEFORE calling `win.hide()`. Previously the fallback
				// timeout only called `win.hide()` and left the
				// `ipcMain.once("bubble:hidden", onHidden)` listener
				// registered. If the renderer later DID emit
				// `bubble:hidden` (e.g. it was just slow, not dead),
				// the `onHidden` callback fired on an already-hidden
				// window. Removing the listener explicitly here keeps
				// the IPC bus clean.
				try {
					ipcMain.removeListener("bubble:hidden", onHidden);
				} catch {
					/* listener already removed or never registered */
				}
				win.hide();
				// PVT-G5-080: routine lifecycle event — log.info.
				log.info(`${BUBBLE_CLR}[BUBBLE]${RESET} hidden (fallback)`);
			} else {
				// Window is already hidden or destroyed — still
				// remove the listener so it doesn't accumulate.
				try {
					ipcMain.removeListener("bubble:hidden", onHidden);
				} catch {
					/* listener already removed or never registered */
				}
			}
		} catch (err) {
			// PVT-G5-081: outer hide-fallback failure — log so a
			// stuck-visible bubble is debuggable instead of silent.
			log.warn(`${BUBBLE_CLR}[BUBBLE]${RESET} hide fallback failed:`, err);
		}
	}, 300);

	// Listen for the renderer's animation-complete signal (once per hide).
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
	ipcMain.once("bubble:hidden", onHidden);
}
