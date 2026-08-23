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
import { app, BrowserWindow, dialog, Menu, nativeTheme, shell } from "electron";
// `APP_NAME` is interpolated into the preload-error dialog body
// (the existing locale keys don't fit a packaging-bug message; the body
// is English-only — see the preload-error handler for rationale).
import { APP_NAME } from "../branding";
import { RENDER_RELOAD_BACKOFF_MS, START_HIDDEN } from "../constants";
// `mainT` provides the localized title for the preload-error
// dialog (the body is hardcoded English — see the preload-error handler
// for rationale; the existing `dialog.criticalError.title` key is
// reused because it's already translated in all 8 locales).
import { mainT } from "../i18n";
import { WindowChannels } from "../ipc/channels";
import {
	cleanConsoleMsg,
	fileTimestamp,
	log,
	RENDERER_CLR,
	RESET,
	redactPii,
} from "../logging";
// `_removeRendererFromBackpressure` clears the per-renderer rate-limit
// Map entry when the window is destroyed, preventing the
// `_rendererCallTimestamps` Map from leaking one entry per destroyed
// BrowserWindow (each `webContents.id` is a fresh integer).
import { _removeRendererFromBackpressure } from "../python/send-to-python";
import { state } from "../state";
// `isLinuxWaylandWithoutSni` is consulted in the main window's
// `close` handler so that on Linux Wayland WITHOUT StatusNotifierItem
// (Sway/Hyprland/dwl/river — no tray icon to dismiss the app to),
// closing the dashboard window flows through to `window-all-closed` →
// `app.quit()` instead of hiding to a tray that doesn't exist.
import { isLinuxWaylandWithoutSni } from "../tray_available";
// The three concerns below were previously inlined in this file. They
// are now in sibling modules so the window-creation logic in
// `createMainWindow()` reads as a flat sequence of "register listener →
// create window → attach handlers" without interleaved definitions of
// the crash-storm tracker / theme listener / renderer-error sink.
// Re-exported below for backward compat with existing import sites
// (notably `tcp-connect.ts`, `handle-message.ts`, `windows/index.ts`,
// and the test files `crash-storm-recovery.test.ts` /
// `main-window-native-theme.test.ts`).
import {
	_resetRenderCrashTrackingForTest as _resetRenderCrashTrackingForTestImpl,
	recordBubbleRenderCrash as recordBubbleRenderCrashImpl,
	recordMainWindowRenderCrash,
} from "./crash-storm";
import { appendRendererError } from "./renderer-error-persistence";
import {
	_nativeThemeListenerRegistered as _nativeThemeListenerRegisteredImpl,
	_resetNativeThemeListenerForTest as _resetNativeThemeListenerForTestImpl,
	registerNativeThemeListener as registerNativeThemeListenerImpl,
} from "./theme-listener";

// Backward-compat re-exports. External consumers (and tests) import
// these names from `./main-window`; the implementations now live in the
// sibling modules above. Re-exporting here means no caller needs to
// change its import path.
export const registerNativeThemeListener = registerNativeThemeListenerImpl;
export const _resetNativeThemeListenerForTest =
	_resetNativeThemeListenerForTestImpl;
export const _nativeThemeListenerRegistered =
	_nativeThemeListenerRegisteredImpl;
export const recordBubbleRenderCrash = recordBubbleRenderCrashImpl;
export const _resetRenderCrashTrackingForTest =
	_resetRenderCrashTrackingForTestImpl;

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
	const win = state.mainWindow;
	if (win.isMinimized()) {
		win.restore();
	}
	if (!win.isVisible()) {
		win.show();
	}
	// RAISE-TO-FRONT: `focus()` alone does NOT bring the window above
	// other applications' windows. Every OS enforces a foreground lock
	// (Windows: SetForegroundWindow from a background process is
	// refused and the taskbar button merely flashes; macOS/Linux window
	// managers behave similarly) — so a tray click left the dashboard
	// visible but buried at the bottom of the z-order. The standard
	// cross-platform workaround is a momentary always-on-top raise:
	// lift the window above everything, focus it, then immediately
	// drop the flag so normal z-order behavior resumes. `moveTop()`
	// additionally raises the z-order without stealing focus on
	// platforms where the WM refuses the focus steal.
	win.setAlwaysOnTop(true, "screen-saver");
	win.show();
	win.focus();
	win.moveTop();
	win.setAlwaysOnTop(false);
	log.info(
		"[MAIN] Dashboard window shown + raised to front (show_window request)",
	);
}

/**
 * Fan out `window:maximized-changed` to every open BrowserWindow so the
 * custom title bar's maximize/restore button stays in sync.
 */
export function broadcastMaximized(maximized: boolean): void {
	BrowserWindow.getAllWindows().forEach((win) => {
		win.webContents.send(WindowChannels.maximizedChanged, maximized);
	});
}

/**
 * Broadcast helper refactor: explicit broadcast helper for the main
 * window. Replaces the
 * previous `webContents.send` monkey-patch that intercepted outbound
 * `python-event` messages. Centralizes the  `pythonReady` flip on
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
 * at module load (see `registerNativeThemeListener()` in
 * `./theme-listener.ts`) instead of being re-registered inside
 * `createMainWindow()` on every window recreation. Previously each call
 * to `createMainWindow()` added a NEW listener to `nativeTheme` without
 * ever removing the previous one — so after N window recreations
 * (dev-mode `relaunchApp()` + tray "Restart"), there were N listeners
 * all firing on every theme change, each holding a stale reference to a
 * destroyed BrowserWindow.
 *
 * The single module-level handler (in `./theme-listener.ts`) reads
 * `state.mainWindow` live (so it always operates on the current window)
 * and is removed once via `nativeTheme.off(...)` when the window is
 * destroyed (in case the module is hot-reloaded in dev — in production
 * the listener lives for the process lifetime, which is correct since
 * `state.mainWindow` is the canonical window reference).
 */

export function createMainWindow(forceShow = false): void {
	if (state.mainWindow) return;
	//do NOT set `state.pythonReady = true` here.
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
		), // Cross-platform window chrome. The app uses a custom title bar
		// everywhere (the OS frame doesn't blend with the app theme), but
		// the window-control BUTTONS are platform-convention-dependent:
		//   - macOS: native traffic lights (red/yellow/green) on the LEFT,
		//     drawn by the OS. `titleBarStyle: "hiddenInset"` hides the
		//     bar while keeping the dots (the renderer's TitleBar then
		//     omits its minimize/maximize/close and reserves a gutter).
		//     `frame: false` would strip the traffic lights entirely.
		//   - Windows/Linux: frameless + the renderer draws the three
		//     buttons on the right (the convention on both platforms).
		// `titleBarStyle` is macOS-only and ignored on Windows/Linux, so
		// the branch is explicit for readability.
		...(process.platform === "darwin"
			? {
					titleBarStyle: "hiddenInset" as const,
					trafficLightPosition: { x: 12, y: 10 },
				}
			: { frame: false as const }),
		hasShadow: false,
		// Always create hidden and gate the first `.show()` on the
		// `ready-to-show` event. Previously `show: shouldShow` flashed a
		// blank white BrowserWindow for the 200-800ms between BrowserWindow
		// construction and the renderer's first paint. With `show: false` +
		// the `ready-to-show` listener below, the window appears only once
		// the renderer has actually painted.
		show: false,
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

	// Broadcast helper refactor: the previous `webContents.send` monkey-patch has been
	// replaced with the explicit `broadcastToMainWindow(channel, msg)`
	// helper (see the export above). Callers in handle-message.ts and
	// tcp-connect.ts now route their `python-event` broadcasts through
	//that helper, which centralizes the  `pythonReady` flip and
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

	// Gate the first `.show()` on the `ready-to-show` event so the window
	// appears only once the renderer has painted. Pairs with `show: false`
	// in the BrowserWindow ctor above. `shouldShow` preserves the
	// START_HIDDEN autostart path.
	state.mainWindow.once("ready-to-show", () => {
		if (shouldShow && state.mainWindow && !state.mainWindow.isDestroyed()) {
			state.mainWindow.show();
		}
	});

	// Close-to-tray: the X button hides the window instead of quitting the
	// app.  The process (tray icon, Python backend, bubble) stays alive.
	// Full quit only happens via the tray "Quit" menu item → stopPython().
	//
	// on Linux Wayland WITHOUT StatusNotifierItem (Sway/Hyprland/
	// dwl/river) the Python tray backend sets `_tray_unavailable = True`
	// and creates NO tray icon.  Hiding the last window here would strand
	// the user — there's no tray icon to re-open the dashboard or quit
	// the app.  Consulting `isLinuxWaylandWithoutSni()` lets the close
	// flow through to `closed` → `window-all-closed` → `app.quit()` so
	// the process exits cleanly instead of becoming an invisible zombie.
	// On all other platforms (Windows, macOS, Linux-with-SNI tray icon),
	// the close-to-tray behavior is preserved.  The `app.isQuitting`
	// short-circuit is kept so the tray "Quit" menu item (which sets
	// `isQuitting = true`) still lets the close flow through to a real
	// destroy + quit.
	state.mainWindow.on("close", (event) => {
		if (!app.isQuitting && !isLinuxWaylandWithoutSni()) {
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

	// macOS fullscreen sync: the green traffic light enters a fullscreen
	// space on macOS instead of emitting maximize/unmaximize. Mirror it
	// onto the same maximized-changed channel so the renderer drops
	// rounded corners / updates chrome state in fullscreen. These events
	// only fire on macOS — no-ops on Windows/Linux.
	state.mainWindow.on("enter-full-screen", () => broadcastMaximized(true));
	// Query the real state on leave: macOS fullscreen can return to a
	// PRE-maximized window (Option+click green = zoom, then green again =
	// fullscreen). Hardcoding `false` would leave the renderer's
	// `is-maximized` class stale — rounded corners would reappear on a
	// still-maximized window.
	state.mainWindow.on("leave-full-screen", () => {
		broadcastMaximized(state.mainWindow?.isMaximized() ?? false);
	});

	// Window-state cleanup: null out `state.mainWindow` once the window is actually
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
	// Capture the destroyed window's `webContents.id` BEFORE nulling
	// `state.mainWindow` so we can clean up the per-renderer rate-limit
	// entry in `send-to-python.ts`'s `_rendererCallTimestamps` Map.
	// Without this, each destroyed BrowserWindow leaks one Map entry
	// (keyed by its now-defunct `webContents.id`) forever.
	//
	// XV-??: capture the webContents id AT CREATION TIME (not in the
	// "closed" callback, where the window is already destroyed and
	// `.webContents` throws "TypeError: Object has been destroyed").
	const mainWebContentsId = state.mainWindow.webContents.id;
	state.mainWindow.on("closed", () => {
		const deadWebContentsId = mainWebContentsId;
		state.mainWindow = null;
		if (typeof deadWebContentsId === "number") {
			try {
				_removeRendererFromBackpressure(deadWebContentsId);
			} catch (e) {
				log.warn(
					"[main-window] _removeRendererFromBackpressure failed on closed:",
					e,
				);
			}
		}
	});

	// CONSOLE-FIX: Electron 30+ deprecated the multi-argument
	// console-message signature `(_e, level, message, line, source)`.
	// The new signature is a single Event object with properties:
	//   e.level, e.message, e.lineNumber, e.sourceId
	// The old signature emitted a deprecation warning on every app start.
	//
	// Renderer-error persistence: when level >= 3 (ERROR), also persist the renderer
	// console error to `electron-renderer-errors.log` under the
	// Electron userData dir. Previously the handler only re-emitted
	// the message to the main-process terminal (lost when the
	// terminal closed) — operators had no way to see renderer
	// crashes post-mortem. The persist call is best-effort: any I/O
	// error is swallowed by `appendRendererError` so logging can
	// never break the renderer console forwarding path.
	//
	// Forwarder-gate widening: lower the forwarder gate from
	// `level >= 2` (WARN and above only) to `level >= 1` so INFO-
	// level renderer telemetry (e.g. lifecycle logs from the
	// renderer) reaches the main process log too. VERBOSE (level
	// 0) is still dropped — it's too noisy for the main log.
	// Structured logger routing: route through the structured logger so WARN/ERROR
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
			// Renderer-error persistence: ERROR-level renderer console output is
			// almost always a real bug (uncaught exception,
			// failed prop type, broken invariant). Persist it
			// to its own log file so support staff can grep
			// renderer crashes without fishing through
			// DevTools or the noisy `electron-main.log`.
			//
			//apply `redactPii` to the persisted line
			// so user-spoken text fragments / API keys / URL
			// credentials in renderer error messages don't
			// land unredacted in `electron-renderer-errors.log`.
			// The stdout path above (via `log.error(msg)`)
			// already goes through `formatArgsForFile`'s
			// redaction, but `appendRendererError` writes via
			// direct `appendLogLine` and bypasses that — so the
			// redaction must be applied explicitly here.
			// `cleanConsoleMsg` runs first (strips printf
			// specifiers), then `redactPii` runs on the
			// cleaned text (idempotent on already-redacted
			// text so the double-chain is safe).
			const cleaned = cleanConsoleMsg(e.message);
			const line = `${fileTimestamp()}  ERROR  [renderer-error] ${redactPii(
				cleaned,
			)} (${e.sourceId}:${e.lineNumber})\n`;
			appendRendererError(line);
		}
	});

	// Render-process-gone recovery: main window lacked render-process-gone recovery (the
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
	//
	// previously this handler only logged.  A transient
	// dev-server 500 or a one-shot file:// race left the dashboard
	// blank with no recovery — the user had to find the tray icon
	// and Quit + relaunch manually.  Mirroring the
	// `render-process-gone` pattern below, we now schedule a
	// single 2s-backoff `reload()` so the user gets a second
	// chance.  The retry is capped at 1 to avoid reload loops on a
	// genuinely broken packaging job (missing index.html in the
	// asar — the reload would just fail-load again forever).
	let didFailLoadRetried = false;
	state.mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) => {
		log.error("[MAIN] did-fail-load", { code, desc, url });
		if (didFailLoadRetried) {
			log.warn(
				"[MAIN] did-fail-load retry already attempted — not retrying again (avoid loop)",
			);
			return;
		}
		didFailLoadRetried = true;
		setTimeout(() => {
			try {
				if (state.mainWindow && !state.mainWindow.isDestroyed()) {
					log.warn(
						"[MAIN] reloading after did-fail-load (2s backoff, single retry)",
					);
					state.mainWindow.reload();
				}
			} catch (err) {
				log.error("[MAIN] failed to reload after did-fail-load", {
					error: (err as Error).message,
				});
			}
		}, RENDER_RELOAD_BACKOFF_MS);
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
		// Crash-storm detection: sliding-window crash storm detection.
		const inStorm = recordMainWindowRenderCrash();
		if (inStorm) {
			try {
				dialog.showErrorBox(
					mainT("dialog.crashLoop.title", { appName: APP_NAME }),
					mainT("dialog.crashLoop.mainBody", { appName: APP_NAME }),
				);
			} catch {
				// dialog may not be available in headless mode.
			}
			return;
		}
		// Crash-storm backoff: 2s backoff before reload to avoid CPU-bound crash loops.
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
		}, RENDER_RELOAD_BACKOFF_MS);
	});

	// `preload-error` fires when the preload script throws at module
	// eval time. This is almost always a packaging bug (preload path
	// mismatch, missing dependency). Logging the file + error makes
	// the root cause obvious instead of presenting as a blank window.
	//
	// previously this handler only logged — the user was left
	// with a blank dashboard and no indication that the app had
	// failed to start. Preload errors are always packaging bugs
	// (a missing/incorrect preload bundle in the asar); retrying
	// won't help because the same packaging defect will reproduce
	// on every reload. We now show a localized error dialog and
	// quit the app so the user gets a clear "please reinstall"
	// message instead of a frozen window.
	//
	// The title uses the existing `dialog.criticalError.title` key
	// (already translated in all 8 locales — see
	// `src/main/i18n/locales/*.json`). The body uses the dedicated
	// `dialog.preloadError.body` key, also translated in all 8
	// locales, with `{appName}` and `{file}` placeholder tokens
	// substituted at runtime via `mainT`.
	state.mainWindow.webContents.on("preload-error", (_e, file, err) => {
		log.error(`[MAIN] preload-error in ${file}`, err);
		try {
			dialog.showErrorBox(
				mainT("dialog.criticalError.title", { appName: APP_NAME }),
				mainT("dialog.preloadError.body", { appName: APP_NAME, file }),
			);
		} catch {
			// dialog may not be available in headless mode.
		}
		try {
			app.quit();
		} catch (e) {
			log.error("[MAIN] app.quit() failed after preload-error:", e);
			// Last-resort backstop: if app.quit() itself threw,
			// force-exit so we don't strand the user with a
			// half-started app.
			process.exit(1);
		}
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

		// F11 → toggle fullscreen. `Menu.setApplicationMenu(null)` above
		// strips the default menu's "Toggle Full Screen" F11 accelerator,
		// so without this F11 is a silent no-op. Match the standard
		// desktop convention (browser-style full viewport) instead. This
		// can't conflict with a user-assigned F11 dictation hotkey: the
		// OS-level global listener consumes the key before it reaches the
		// app when F11 is bound, so this handler only fires when F11 is
		// unbound. `isAutoRepeat` is skipped so holding F11 doesn't
		// thrash the toggle, and only keyDown is handled (keyUp would
		// double-toggle on a single press).
		if (
			input.type === "keyDown" &&
			!input.isAutoRepeat &&
			input.key.toLowerCase() === "f11"
		) {
			const win = state.mainWindow;
			if (win && !win.isDestroyed()) {
				win.setFullScreen(!win.isFullScreen());
			}
		}
	});

	// Window-open hardening: deny every renderer-initiated window.open() /
	// target=_blank navigation by default. Without this handler, a
	// compromised renderer (XSS, dependency supply-chain, malicious
	// transcription payload that reaches an innerHTML sink) can pop an
	// arbitrary external URL inside a fresh Electron BrowserWindow —
	// bypassing the renderer sandbox and exposing Node primitives to
	// untrusted content.
	//
	// Behavior:
	//   • https URLs → routed to the user's default browser via
	//     `shell.openExternal` and the in-app window is denied.
	//   • All other schemes (file://, javascript:, data:, blob:) →
	//     denied silently with a WARN log so a redirected/typo'd URL
	//     is visible without crashing the renderer.
	//   • `shell.openExternal` failures are logged but never block
	//     the deny (the URL was already going to be denied anyway).
	state.mainWindow.webContents.setWindowOpenHandler(({ url }) => {
		if (/^https?:\/\//i.test(url)) {
			// Fire-and-forget; openExternal is async but the
			// handler must return synchronously. A rejection
			// (e.g. no default browser configured on a fresh
			// OS install) is logged but does not change the
			// deny verdict.
			void shell.openExternal(url).catch((err: unknown) =>
				log.warn("[MAIN] setWindowOpenHandler: shell.openExternal failed", {
					url,
					error: (err as Error)?.message,
				}),
			);
		} else {
			log.warn(
				"[MAIN] setWindowOpenHandler: denied non-https window.open target",
				{ url },
			);
		}
		return { action: "deny" };
	});

	if (process.env.ELECTRON_RENDERER_URL) {
		// void + .catch: loadURL returns a Promise that rejects on
		// load failure (dev server 500, malformed URL, network
		// unreachable). Without .catch the rejection would bubble
		// up as an unhandled-rejection and feed the SEC-021 breaker
		// (which trips on repeated unhandled rejections and force-
		// quits the app). The `did-fail-load` handler above already
		// logs the failure with structured detail; this .catch only
		// suppresses the unhandled rejection so the breaker stays
		// calm. `void` discards the .catch's resolved value.
		void state.mainWindow
			.loadURL(process.env.ELECTRON_RENDERER_URL)
			.catch((e) => {
				log.warn("[MAIN] loadURL rejected:", e);
			});
	} else {
		void state.mainWindow
			.loadFile(path.join(__dirname, "../renderer/index.html"))
			.catch((e) => {
				log.warn("[MAIN] loadFile rejected:", e);
			});
	}
}
