/**
 * Bubble overlay BrowserWindow creation + helpers.
 *
 * Extracted from `index.ts` (REF-2). Owns:
 *   - `centerOnPrimaryDisplay()` — top/bottom centered position for the bubble.
 *   - `isForegroundFullscreen()` — best-effort exclusive-fullscreen detection
 *     (SEC-025) so we don't paint over fullscreen apps.
 *   - `createBubbleWindow()` — lazy-creates the always-on-top transparent pill.
 *   - `showBubbleWindow()` / `hideBubbleWindow()` — animated show/hide with
 *     rapid-toggle guard + renderer-driven exit animation.
 */
import path from "node:path";
import { BrowserWindow, ipcMain, screen } from "electron";
import { BUBBLE_HEIGHT, BUBBLE_WIDTH } from "../constants";
import { BUBBLE_CLR, cleanConsoleMsg, RESET, ts } from "../logging";
import { state } from "../state";

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

export function createBubbleWindow(): BrowserWindow {
	if (state.bubbleWindow && !state.bubbleWindow.isDestroyed()) {
		return state.bubbleWindow;
	}
	const { x, y } = centerOnPrimaryDisplay();
	console.warn(
		`${ts()}  ${BUBBLE_CLR}[BUBBLE] creating window at (${x}, ${y}) ${BUBBLE_WIDTH}x${BUBBLE_HEIGHT}${RESET}`,
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
		console.warn(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] screen-saver failed, trying floating:${RESET}`,
			e,
		);
		try {
			win.setAlwaysOnTop(true, "floating");
		} catch {}
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
		console.error(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] did-fail-load code=${code} desc=${desc} url=${url}${RESET}`,
		);
	});
	win.webContents.on("did-finish-load", () => {
		console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] did-finish-load${RESET}`);
	});
	win.webContents.on("render-process-gone", (_e, details) => {
		console.error(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] render-process-gone:${RESET}`,
			details,
		);
		// SEC-024: reload the bubble window so it doesn't stay as a
		// blank, invisible, always-on-top overlay. Without this, a
		// crashed bubble renderer leaves a stuck overlay on the screen.
		try {
			if (!win.isDestroyed()) {
				console.warn(
					`${ts()}  ${BUBBLE_CLR}[BUBBLE] reloading after render-process-gone${RESET}`,
				);
				win.reload();
			}
		} catch (e) {
			console.error("[BUBBLE] failed to reload after render-process-gone:", e);
		}
	});
	win.webContents.on("preload-error", (_e, file, err) => {
		console.error(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] preload-error file=${file}${RESET}`,
			err,
		);
	});
	// CONSOLE-FIX: new Electron console-message signature.
	win.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 2) {
			const tag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			console.warn(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] renderer ${tag}${RESET} ${cleanConsoleMsg(e.message)} (${e.sourceId}:${e.lineNumber})`,
			);
		}
	});

	const loadTarget = process.env.ELECTRON_RENDERER_URL
		? `${process.env.ELECTRON_RENDERER_URL}/bubble.html`
		: path.join(__dirname, "../renderer/bubble.html");
	console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] loading ${loadTarget}${RESET}`);
	if (process.env.ELECTRON_RENDERER_URL) {
		void win.loadURL(loadTarget);
	} else {
		void win.loadFile(loadTarget);
	}

	state.bubbleWindow = win;
	win.on("closed", () => {
		console.warn(`${ts()}  ${BUBBLE_CLR}[BUBBLE] closed${RESET}`);
		if (state.bubbleWindow === win) state.bubbleWindow = null;
		state._bubblePageReady = false;
	});
	return win;
}

export function showBubbleWindow(): void {
	if (!state.bubbleWindow || state.bubbleWindow.isDestroyed()) {
		createBubbleWindow();
	}
	const win = state.bubbleWindow;
	if (!win) {
		console.error(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] showBubbleWindow: no window to show${RESET}`,
		);
		return;
	}

	// Rapid-toggle guard: cancel any pending hide timeout/animation so the
	// bubble doesn't flicker when show is called while a hide is in flight.
	if (state._hideTimeout) {
		clearTimeout(state._hideTimeout);
		state._hideTimeout = null;
		// Remove any pending one-shot listener to prevent stale hide callbacks.
		try {
			ipcMain.removeAllListeners("bubble:hidden");
		} catch {}
	}

	const c = centerOnPrimaryDisplay();
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
		console.error(`${ts()}  ${BUBBLE_CLR}[BUBBLE] show() failed:${RESET}`, e);
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
			console.warn(
				`${ts()}  ${BUBBLE_CLR}[BUBBLE] not visible after show() -- retrying${RESET}`,
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
	try {
		win.webContents.send("bubble:hide");
	} catch {}

	// Use a timeout as fallback in case the renderer is unresponsive.
	state._hideTimeout = setTimeout(() => {
		state._hideTimeout = null;
		try {
			if (!win.isDestroyed() && win.isVisible()) {
				win.hide();
				console.warn(
					`${ts()}  ${BUBBLE_CLR}[BUBBLE] hidden (fallback)${RESET}`,
				);
			}
		} catch {}
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
				console.warn(
					`${ts()}  ${BUBBLE_CLR}[BUBBLE] hidden (animated)${RESET}`,
				);
			}
		} catch {}
	};
	ipcMain.once("bubble:hidden", onHidden);
}
