/**
 * Dashboard (main) BrowserWindow creation + helpers.
 *
 * Extracted from `index.ts` (REF-2). Owns:
 *   - `createMainWindow(forceShow?)` — lazy-creates the dashboard window.
 *   - `showMainWindow()` — show + focus the dashboard, creating it if needed.
 *     Used by the `second-instance` event and the tray "Open app" path.
 *   - `broadcastMaximized(bool)` — fans out `window:maximized-changed` to
 *     every open BrowserWindow (used by the main window's maximize /
 *     unmaximize event listeners).
 *   - `_nativeThemeHandler` + `registerNativeThemeListener()` — R6-F3:
 *     the `nativeTheme.on("updated", ...)` listener is registered ONCE
 *     at module load and cleaned up when the main window is destroyed.
 */
import path from "node:path";
import { app, BrowserWindow, dialog, Menu, nativeTheme } from "electron";
import { START_HIDDEN } from "../constants";
import { cleanConsoleMsg, RENDERER_CLR, RESET } from "../logging";
import { state } from "../state";

// PVT-G5-080: structured logger. Resolved defensively via `require()`
// so unit-test environments that mock `../logging` minimally (without
// the new `log` export, e.g. main-window-native-theme.test.ts) still
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
	} catch (e) {
		// ignore — fall through to fallback. `require()` may fail in
		// bundlers that strip the dynamic require; the fallback logger
		// below is sufficient for those environments.
		console.warn(
			"[main-window] structured logger require failed, using fallback:",
			e,
		);
	}
	return {
		info: (...args: unknown[]) => console.log(...args),
		warn: (...args: unknown[]) => console.warn(...args),
		error: (...args: unknown[]) => console.error(...args),
	};
})();

// G4-M-67: defensive resolution of the renderer-error persistence
// helpers from session 4's logging.ts additions. Resolved via
// `require()` so this file compiles whether or not the merged
// logging.ts keeps session 4's `appendLogLine` / `rendererErrorsLogPath`
// exports. When unavailable, `appendRendererError` is a no-op.
type _AppendLogLine = (filePath: string, line: string) => void;
type _RendererErrorsLogPath = () => string;
const _appendLogLine: _AppendLogLine | null = (() => {
	try {
		// eslint-disable-next-line @typescript-eslint/no-var-requires, @typescript-eslint/no-require-imports
		const mod = require("../logging") as unknown as {
			appendLogLine?: _AppendLogLine;
		};
		return mod.appendLogLine ?? null;
	} catch {
		return null;
	}
})();
const _rendererErrorsLogPath: _RendererErrorsLogPath | null = (() => {
	try {
		// eslint-disable-next-line @typescript-eslint/no-var-requires, @typescript-eslint/no-require-imports
		const mod = require("../logging") as unknown as {
			rendererErrorsLogPath?: _RendererErrorsLogPath;
		};
		return mod.rendererErrorsLogPath ?? null;
	} catch {
		return null;
	}
})();

/**
 * G4-M-67: persist a renderer-error line to
 * `electron-renderer-errors.log` (when session 4's logging helpers are
 * available). Best-effort: silently no-ops if the helpers aren't
 * merged into the final logging.ts.
 */
function appendRendererError(line: string): void {
	if (!_appendLogLine || !_rendererErrorsLogPath) return;
	try {
		_appendLogLine(_rendererErrorsLogPath(), line);
	} catch (e) {
		// Best-effort: a logging failure must not cascade into a runtime
		// failure of the calling code. The console.warn keeps the failure
		// visible without breaking the renderer console forwarding path.
		console.warn("[main-window] appendRendererError failed:", e);
	}
}

/**
 * Show + focus the dashboard window, creating it if needed.
 *
 * Used by:
 *   • second-instance event  (Start Menu / Desktop click while running)
 *   • tray "Open app" IPC path (see showMainWindow IPC handler below)
 */
export function showMainWindow(): void {
	if (!state.mainWindow || state.mainWindow.isDestroyed()) {
		createMainWindow(/* forceShow */ true);
		return;
	}
	if (!state.mainWindow.isVisible()) {
		state.mainWindow.show();
	}
	if (state.mainWindow.isMinimized()) {
		state.mainWindow.restore();
	}
	state.mainWindow.focus();
}

/**
 * Fan out `window:maximized-changed` to every open BrowserWindow so the
 * custom title bar's maximize/restore button stays in sync.
 */
export function broadcastMaximized(maximized: boolean): void {
	BrowserWindow.getAllWindows().forEach((win) => {
		win.webContents.send("window:maximized-changed", maximized);
	});
}

/**
 * GT-A3-8: explicit broadcast helper for the main window. Replaces the
 * previous `webContents.send` monkey-patch that intercepted outbound
 * `python-event` messages. Centralizes the CR-28 `pythonReady` flip on
 * the first `{ type: "ready" }` push and the destroyed-window guard.
 */
export function broadcastToMainWindow(channel: string, msg: unknown): void {
	if (
		!state.pythonReady &&
		channel === "python-event" &&
		typeof msg === "object" &&
		msg !== null &&
		(msg as Record<string, unknown>).type === "ready"
	) {
		state.pythonReady = true;
		log.info(
			"[STARTUP] backend sent {type:'ready'} — pythonReady = true (backend fully initialized)",
		);
	}
	if (state.mainWindow && !state.mainWindow.isDestroyed()) {
		state.mainWindow.webContents.send(channel, msg);
	}
}

/**
 * R6-F3: the `nativeTheme.on("updated", ...)` listener is registered ONCE
 * at module load (see `registerNativeThemeListener()` below) instead of
 * being re-registered inside `createMainWindow()` on every window
 * recreation. Previously each call to `createMainWindow()` added a NEW
 * listener to `nativeTheme` without ever removing the previous one —
 * so after N window recreations (dev-mode `relaunchApp()` + tray
 * "Restart"), there were N listeners all firing on every theme change,
 * each holding a stale reference to a destroyed BrowserWindow.
 *
 * The single module-level handler reads `state.mainWindow` live (so it
 * always operates on the current window) and is removed once via
 * `nativeTheme.off(...)` when the window is destroyed (in case the
 * module is hot-reloaded in dev — in production the listener lives
 * for the process lifetime, which is correct since `state.mainWindow`
 * is the canonical window reference).
 */
let _nativeThemeHandler: (() => void) | null = null;

/**
 * Register the global `nativeTheme.on("updated", ...)` listener exactly
 * once. Idempotent — safe to call multiple times. Exported for tests
 * (R6-F3) so we can assert it's only registered once across multiple
 * `createMainWindow()` calls.
 */
export function registerNativeThemeListener(): void {
	if (_nativeThemeHandler) return;
	_nativeThemeHandler = () => {
		if (state.mainWindow && !state.mainWindow.isDestroyed()) {
			const name = nativeTheme.shouldUseDarkColors
				? "icon-dark.png"
				: "icon.png";
			state.mainWindow.setIcon(path.join(__dirname, `../../resources/${name}`));
		}
	};
	nativeTheme.on("updated", _nativeThemeHandler);
}

/**
 * Test-only: remove the global `nativeTheme.on("updated", ...)` listener
 * and reset internal state so a test can verify registration happens
 * exactly once across `createMainWindow()` calls.
 */
export function _resetNativeThemeListenerForTest(): void {
	if (_nativeThemeHandler) {
		try {
			nativeTheme.off("updated", _nativeThemeHandler);
		} catch (e) {
			/* best-effort: test-only cleanup; listener may already be gone */
			console.warn(
				"[main-window] _resetNativeThemeListenerForTest off() failed:",
				e,
			);
		}
		_nativeThemeHandler = null;
	}
}

/**
 * Test-only: return whether the module-level `nativeTheme.on("updated")`
 * listener is currently registered. Used by R6-F3 unit tests.
 */
export function _nativeThemeListenerRegistered(): boolean {
	return _nativeThemeHandler !== null;
}

// GT-10: render-process-gone crash-storm tracking. Sliding 60s window;
// if >5 crashes land in that window, stop reloading and show a dialog.
const RENDER_CRASH_WINDOW_MS = 60_000;
const RENDER_CRASH_THRESHOLD = 5;
const _mainWindowCrashTimestamps: number[] = [];
const _bubbleWindowCrashTimestamps: number[] = [];

function recordRenderCrash(timestamps: number[], label: string): boolean {
	const now = Date.now();
	timestamps.push(now);
	while (timestamps.length > 0 && now - timestamps[0]! > RENDER_CRASH_WINDOW_MS) {
		timestamps.shift();
	}
	if (timestamps.length > RENDER_CRASH_THRESHOLD) {
		log.error(
			`[MAIN] ${label} render-process-gone storm: ${timestamps.length} crashes in ${RENDER_CRASH_WINDOW_MS / 1000}s - stopping reload`,
		);
		return true;
	}
	return false;
}

export function _resetRenderCrashTrackingForTest(): void {
	_mainWindowCrashTimestamps.length = 0;
	_bubbleWindowCrashTimestamps.length = 0;
}

/** GT-10: bubble-window-side wrapper (imported by bubble-window.ts). */
export function recordBubbleRenderCrash(): boolean {
	return recordRenderCrash(_bubbleWindowCrashTimestamps, "Bubble");
}

export function createMainWindow(forceShow = false): void {
	if (state.mainWindow) return;
	// CR-28: do NOT set `state.pythonReady = true` here.
	//
	// The BrowserWindow is created on the first successful TCP
	// connect (see tcp-connect.ts:59 `createWindows()`), but "TCP
	// alive" only means the Python IPC server has bound its port
	// and accepted our auth line. The backend's heavier subsystems
	// — model load, history DB schema init, tray, hotkey
	// registration, dictation pipeline — may still be
	// initializing on the Python side. Treating that as
	// "pythonReady" masked real backend-startup failures: if
	// Python crashed *after* TCP accept but *before* finishing
	// init, start-python.ts's exit handler saw `pythonReady ===
	// true` and classified the crash as a "real crash during
	// operation" instead of the more accurate "early exit during
	// backend init".
	//
	// We instead defer `state.pythonReady = true` until the
	// backend sends its `{"type":"ready"}` push event (emitted by
	// ipc_server.py:2557 right before `app.start()` enters the
	// tray event loop). The interceptor installed below catches
	// that event the first time the main process broadcasts it
	// to the renderer via `webContents.send("python-event", msg)`
	// (see handle-message.ts:115) and flips the flag.
	//
	// The distinction matters for start-python.ts's exit
	// handler: if Python exits before sending `ready`, it's an
	// "early exit" (clear single-instance / setup-failure dialog);
	// after `ready`, it's a "crash" (silent quit). This matches
	// the user's mental model and surfaces real init failures
	// instead of swallowing them.

	// When autostarted hidden, the window is created but not shown — the
	// React app still boots (so opening it later is instant) while staying
	// off the taskbar.  forceShow overrides this (second-instance / tray
	// open) so the window appears immediately.
	const shouldShow = forceShow || !START_HIDDEN;

	state.mainWindow = new BrowserWindow({
		width: 1000,
		height: 700,
		minWidth: 850,
		minHeight: 550,
		icon: path.join(
			__dirname,
			`../../resources/icon${nativeTheme.shouldUseDarkColors ? "-dark" : ""}.png`,
		),
		frame: false,
		hasShadow: false,
		show: shouldShow,
		// Set the window background color to match the app theme so the
		// rounded corners (border-radius on the wrapper div) don't reveal
		// a white flash when the window is hidden on close.  The renderer
		// applies its own background via CSS variables, but the area behind
		// the web content (the corners outside border-radius) shows through
		// to the Electron window background.
		backgroundColor: nativeTheme.shouldUseDarkColors ? "#1a1b1e" : "#ffffff",
		// skipTaskbar when hidden so an autostarted background instance leaves
		// no taskbar entry until the user actually opens it.
		skipTaskbar: !shouldShow,
		webPreferences: {
			preload: path.join(__dirname, "../preload/index.js"),
			backgroundThrottling: false,
			// SOUND-FIX: allow AudioContext / HTMLAudioElement to play
			// without a prior user gesture in the renderer.  The user
			// has explicitly launched VoiceTyper as a desktop app, so
			// the implicit "user gesture" of installing + running the
			// app satisfies the trust requirement.  Without this, the
			// start/stop recording audio cues don't play when the user
			// triggers recording via the GLOBAL hotkey (which fires
			// from the OS-level backend, NOT from a renderer gesture).
			// The default Chromium policy ("document-user-activation-
			// required") causes the intermittent "sometimes no sound"
			// bug — the cue plays only if the user happened to click
			// in the Electron window before pressing the hotkey.
			autoplayPolicy: "no-user-gesture-required",
			// SEC-014: explicit hardening.  These are Electron defaults
			// for most fields, but setting them explicitly guards against
			// future Electron version changes flipping a default to a
			// less-safe value.
			contextIsolation: true, // renderer can't touch Node require
			nodeIntegration: false, // no require() in renderer
			sandbox: true, // preload runs in sandboxed context
			webSecurity: true, // enforce same-origin policy
			allowRunningInsecureContent: false, // block mixed-content
			// spellcheck adds a tiny IPC surface; we don't need it.
			spellcheck: false,
		},
	});

	// GT-A3-8: the previous `webContents.send` monkey-patch has been
	// replaced with the explicit `broadcastToMainWindow(channel, msg)`
	// helper (see the export above). Callers in handle-message.ts and
	// tcp-connect.ts now route their `python-event` broadcasts through
	// that helper, which centralizes the CR-28 `pythonReady` flip and
	// the destroyed-window guard.

	// R6-F3 (session-3): register the module-level nativeTheme
	// listener once. Previously this was an inline
	// `nativeTheme.on("updated", ...)` inside `createMainWindow()`,
	// which leaked a new listener per window recreation (dev-mode
	// `relaunchApp()` + tray "Restart").
	// `registerNativeThemeListener()` is idempotent — the second
	// call is a no-op, so subsequent `createMainWindow()`
	// invocations don't stack duplicate handlers.
	//
	// Note: the inline `nativeTheme.on("updated", ...)` listener
	// that was previously here has been REMOVED — it is now
	// registered inside `registerNativeThemeListener()` to prevent
	// the per-recreation leak. Keeping both would register TWO
	// listeners (the idempotent one + the leaked inline one).
	registerNativeThemeListener();

	Menu.setApplicationMenu(null);

	// Close-to-tray: the X button hides the window instead of quitting the
	// app.  The process (tray icon, Python backend, bubble) stays alive.
	// Full quit only happens via the tray "Quit" menu item → stopPython().
	state.mainWindow.on("close", (event) => {
		if (!app.isQuitting) {
			event.preventDefault();
			state.mainWindow?.hide();
			// Remove from taskbar while hidden.
			state.mainWindow?.setSkipTaskbar(true);
		}
	});

	// When the window is shown again (second-instance / tray open), restore
	// the taskbar entry.
	state.mainWindow.on("show", () => {
		state.mainWindow?.setSkipTaskbar(false);
	});

	state.mainWindow.on("maximize", () => broadcastMaximized(true));
	state.mainWindow.on("unmaximize", () => broadcastMaximized(false));

	// PVT-12: null out `state.mainWindow` once the window is actually
	// destroyed. The `close` handler above intercepts the X button to
	// hide-to-tray (preventDefault), so it never reaches `closed`. But
	// the real destroy paths (tray "Quit" → `app.isQuitting = true` →
	// close flows through; `win.destroy()` from start-python.ts:132;
	// app teardown on quit) DO reach `closed`, and without this handler
	// `state.mainWindow` keeps pointing at a destroyed BrowserWindow
	// until process exit. Any later read of `state.mainWindow` (e.g.
	// `showMainWindow()`'s `isDestroyed()` check, or the
	// `nativeTheme.on("updated")` handler) then trips over a dead
	// reference. Nulling here keeps the state invariant honest:
	// `state.mainWindow` is non-null iff a live window exists.
	state.mainWindow.on("closed", () => {
		state.mainWindow = null;
	});

	// CONSOLE-FIX: Electron 30+ deprecated the multi-argument
	// console-message signature `(_e, level, message, line, source)`.
	// The new signature is a single Event object with properties:
	//   e.level, e.message, e.lineNumber, e.sourceId
	// The old signature emitted a deprecation warning on every app start.
	//
	// G4-M-67: when level >= 3 (ERROR), also persist the renderer
	// console error to `electron-renderer-errors.log` under the
	// Electron userData dir. Previously the handler only re-emitted
	// the message to the main-process terminal (lost when the
	// terminal closed) — operators had no way to see renderer
	// crashes post-mortem. The persist call is best-effort: any I/O
	// error is swallowed by `appendRendererError` so logging can
	// never break the renderer console forwarding path.
	//
	// PVT-G5-081 sub-finding: lower the forwarder gate from
	// `level >= 2` (WARN and above only) to `level >= 1` so INFO-
	// level renderer telemetry (e.g. lifecycle logs from the
	// renderer) reaches the main process log too. VERBOSE (level
	// 0) is still dropped — it's too noisy for the main log.
	// PVT-G5-080: route through the structured logger so WARN/ERROR
	// lines also land in electron-runtime.log.
	state.mainWindow.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 1) {
			const tag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			const msg = `${RENDERER_CLR}[MAIN renderer] ${tag}${RESET} ${cleanConsoleMsg(e.message)} (${e.sourceId}:${e.lineNumber})`;
			if (level >= 3) log.error(msg);
			else if (level === 2) log.warn(msg);
			else log.info(msg);
		}
		if (level >= 3) {
			// G4-M-67: ERROR-level renderer console output is
			// almost always a real bug (uncaught exception,
			// failed prop type, broken invariant). Persist it
			// to its own log file so support staff can grep
			// renderer crashes without fishing through
			// DevTools or the noisy `electron-main.log`.
			const line = `${new Date().toISOString()} [renderer-error] ${cleanConsoleMsg(
				e.message,
			)} (${e.sourceId}:${e.lineNumber})\n`;
			appendRendererError(line);
		}
	});

	// G4-H-23: main window lacked render-process-gone recovery (the
	// bubble window already had all three handlers — see
	// bubble-window.ts:127-159). Without these, a main-renderer
	// crash left the user with a blank/frozen dashboard while the
	// tray icon + Python backend kept running; "Open app" from the
	// tray showed the same dead window.
	//
	// `did-fail-load` fires when the renderer fails to load its
	// initial HTML (e.g. the bundled index.html is missing or the
	// dev server returned 500). Logging the error code + URL lets
	// support staff diagnose packaging / dev-server issues.
	state.mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) => {
		log.error("[MAIN] did-fail-load", { code, desc, url });
	});

	// `render-process-gone` fires when the renderer process crashes
	// (GPU process OOM, native module segfault, v8 heap exhaustion).
	// Without a reload, the BrowserWindow stays alive with a blank
	// webContents — the user sees a frozen window with no way to
	// recover short of quitting via the tray. We reload the window
	// so the user gets a fresh renderer (the Python backend keeps
	// running, so session state is preserved on the backend side).
	state.mainWindow.webContents.on("render-process-gone", (_e, details) => {
		log.error("[MAIN] render-process-gone", details);
		// GT-10: sliding-window crash storm detection.
		const inStorm = recordRenderCrash(_mainWindowCrashTimestamps, "Main");
		if (inStorm) {
			try {
				dialog.showErrorBox(
					"Voice Typer — Renderer crash loop",
					"The main window renderer has crashed repeatedly and cannot recover.\n\nPlease use the tray icon to Restart or Quit, then relaunch Voice Typer.",
				);
			} catch {
				// dialog may not be available in headless mode.
			}
			return;
		}
		// GT-10: 2s backoff before reload to avoid CPU-bound crash loops.
		setTimeout(() => {
			try {
				if (state.mainWindow && !state.mainWindow.isDestroyed()) {
					log.warn("[MAIN] reloading after render-process-gone (2s backoff)");
					state.mainWindow.reload();
				}
			} catch (err) {
				log.error("[MAIN] failed to reload after render-process-gone", {
					error: (err as Error).message,
				});
			}
		}, 2000);
	});

	// `preload-error` fires when the preload script throws at module
	// eval time. This is almost always a packaging bug (preload path
	// mismatch, missing dependency). Logging the file + error makes
	// the root cause obvious instead of presenting as a blank window.
	state.mainWindow.webContents.on("preload-error", (_e, file, err) => {
		log.error(`[MAIN] preload-error in ${file}`, err);
	});

	state.mainWindow.webContents.on("before-input-event", (_event, input) => {
		if (
			(input.control || input.meta) &&
			input.shift &&
			input.key.toLowerCase() === "i"
		) {
			// SEC-013: DevTools should only be available in dev builds.
			// In production (app.isPackaged === true), the toggle is a
			// no-op so end users (and any XSS that tries to trigger it
			// via synthetic keyboard events) can't open DevTools.
			if (!app.isPackaged) {
				state.mainWindow?.webContents.toggleDevTools();
			}
		}
	});

	if (process.env.ELECTRON_RENDERER_URL) {
		state.mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
	} else {
		state.mainWindow.loadFile(path.join(__dirname, "../renderer/index.html"));
	}
}
