/**
 * Electron main-process entry point.
 *
 * REF-2: this file is now wiring-only (≤300 lines). All cohesive
 * function groups have been extracted into focused modules:
 *
 *   - `./state`             — shared mutable state (pythonProcess, tcpSocket,
 *                             mainWindow, bubbleWindow, pendingRequests, …)
 *   - `./logging`           — `ts()`, `cleanConsoleMsg()`, ANSI color constants
 *   - `./constants`         — `IPC_PORT`, `IPC_TOKEN`, `START_HIDDEN`,
 *                             `BUBBLE_WIDTH`, `BUBBLE_HEIGHT`, `HEARTBEAT_INTERVAL_MS`
 *   - `./single_instance`   — `computeConfigDir`, `electronPidFile*`,
 *                             `acquireSingleInstanceLock` (+ `app.on("second-instance")`)
 *   - `./windows/`          — `createMainWindow`, `createBubbleWindow`,
 *                             `showBubbleWindow`, `hideBubbleWindow`,
 *                             `centerOnPrimaryDisplay`, `showMainWindow`,
 *                             `createWindows` aggregator
 *   - `./python/`           — `pythonArgs`, `startPython`, `stopPython`,
 *                             `tcpConnect`, `sendToPython`, `handleMessage`,
 *                             `relaunchApp`
 *   - `./ipc/`              — `registerIpcHandlers()` (window controls, config
 *                             export, bubble IPC, python-call bridge)
 *   - `./bootstrap`         — `bootstrapRuntime()` (sessionNonce, userData,
 *                             CSP, error handlers)
 *
 * What stays here:
 *   - Security-warning suppression.
 *   - `app.setAppUserModelId("VoiceTyper")`.
 *   - `acquireSingleInstanceLock()` + `registerIpcHandlers()` calls.
 *   - `app.whenReady()` → `bootstrapRuntime()` + VT_BUBBLE_TEST + `startPython()`.
 *   - `app.on("before-quit" | "will-quit" | "window-all-closed" | "activate", …)`.
 */
import { app } from "electron";
import { bootstrapRuntime, setupUserData } from "./bootstrap";
import { runBubbleTestDiagnostics } from "./dev/bubble-test";
import { registerIpcHandlers } from "./ipc";
//route main-process lifecycle messages through
// the structured `log` logger so they persist to `electron-main.log`
// (with 5 MiB rotation) and `electron-lifecycle.log` (opt-in INFO
// persistence) instead of being lost in packaged builds where
// `console.warn` has no terminal attached.
import { BUBBLE_CLR, log, RESET, sweepStaleLogs, ts } from "./logging";
// powerMonitor suspend/resume/on-battery handlers. Registered
// after `app.whenReady()` (see call site below) — `powerMonitor` is
// not usable before the app is ready.
import { registerPowerMonitorHandlers } from "./power";
import { startPython, stopPython } from "./python";
import { ESCALATE_TIMER_MS, KILL_TIMER_MS } from "./python/stop-python";
import {
	acquireSingleInstanceLock,
	clearElectronPidFile,
} from "./single_instance";
import { state } from "./state";
import { isLinuxWaylandWithoutSni } from "./tray_available";
import { createWindows, showMainWindow } from "./windows";

// Suppress Electron's built-in security-warning console spam in dev mode
// (the "Insecure Content-Security-Policy" message about unsafe-eval).
// Vite dev mode needs unsafe-eval for sourcemaps — this is expected.
if (process.env.npm_lifecycle_event === "dev" || !app.isPackaged) {
	process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = "true";
}

// Startup-timeline marker (read by the Python backend at its first log
// line — see `voice_typer/server/startup_timeline.py`): module-eval time
// ≈ Electron process boot. Together with VOICE_TYPER_SPAWN_EPOCH_MS
// (set in start-python.ts right before the spawn) it lets the backend
// log one line attributing the launch gap: electron boot vs backend init.
process.env.VOICE_TYPER_BOOT_EPOCH_MS ??= String(Date.now());

// Prevent Chromium from persisting its HTTP + V8-code caches into the
// shared `electron-profile/`. The renderer only ever loads the local
// bundle (`file://` via `loadFile` in production, `http://localhost:5173`
// in dev) — it never fetches remote content — yet the disk cache still
// accumulated ~400 MB of stale entries there (212 MB HTTP `Cache` +
// 180 MB V8 `Code Cache` from dev-server URLs). Both switches are
// documented Chromium content-layer switches:
//   - `disable-http-cache` — disables the DISK cache for HTTP requests
//     (the in-memory cache stays, so HMR / repeated loads are unaffected).
//   - `v8-cache-options=none` — disables V8's on-disk script code cache
//     (`Code Cache/`); production loads via `file://` where code cache
//     is not used anyway (https URLs only), so nothing is lost.
// Must be appended before `app.whenReady()` — the switches are parsed
// by Chromium's browser process at startup.
app.commandLine.appendSwitch("disable-http-cache");
app.commandLine.appendSwitch("v8-cache-options", "none");

try {
	// Best-effort — only matters on Windows 7+.
	app.setAppUserModelId("VoiceTyper");
} catch (e) {
	// setAppUserModelId can throw on non-Windows or if the registry
	// write fails; non-fatal — Windows taskbar grouping falls back
	// to the default (app.exe name) which is acceptable.
	log.warn("[main] setAppUserModelId failed (non-fatal):", e);
}

// Override Electron's userData directory to the shared
// `electron-profile` subfolder AT MODULE-LOAD TIME (not inside
// `app.whenReady()`).
//
// Chromium spawns its GPU + network-service utility processes while
// the app is still initializing — before `whenReady()` resolves. If
// `app.setPath("userData", …)` runs inside `bootstrapRuntime()`, those
// early processes are spawned with the DEFAULT userData
// (`%APPDATA%/voice-typer-desktop`), leaving a mixed profile: the
// renderer (created later) uses `electron-profile/` while the GPU /
// network processes keep writing Cache / GPU Cache / Network state to
// the old default dir. Calling `setupUserData()` here — before
// `acquireSingleInstanceLock()` and `app.whenReady()` — guarantees
// EVERY Chromium child process inherits the unified data root.
// Idempotent: `bootstrapRuntime()` re-invokes it inside `whenReady`
// (harmless re-set of the same path).
setupUserData();

// Single-instance gate + `app.on("second-instance")` handler. Must run
// before `app.whenReady()` — the lock is checked at process start.
// On a duplicate launch it calls `app.exit(0)`; on the primary instance
// it writes the PID file and registers the second-instance → showMainWindow
// handler. See `./single_instance.ts` for the stale-PID recovery path.
acquireSingleInstanceLock();

// Startup log sweep — Tiers 1 (age, 7 days) + 2 (size fallback, 25 MB)
// of the three-tier cleanup design. Runs AFTER the single-instance gate
// (only the primary instance sweeps) and BEFORE any log writes
// (error handlers install later, inside `whenReady()`), so stale /
// oversized logs are deleted and this session starts fresh. Mirrors the
// Python `_sweep_stale_logs` and the Rust host's startup sweep.
sweepStaleLogs();

// Register every `ipcMain.on` / `ipcMain.handle` listener (window
// controls, bubble IPC, config/history/vocabulary/templates export,
// python-call bridge). See `./ipc/`.
registerIpcHandlers();

// Tracks a genuine quit (tray Quit / Cmd+Q) so the close-to-tray handler
// on the window knows to let the close proceed instead of hiding.
app.isQuitting = false;

//cleanup handle for the dev-only `VT_BUBBLE_TEST=1` bubble
// diagnostic harness (see `./dev/bubble-test.ts`). Assigned inside
// `app.whenReady()` when the env var is set; invoked from the
// `before-quit` handler so the diagnostic's 3 timers (1 setTimeout +
// 1 setInterval + 1 setTimeout-stop) don't outlive a normal app
// shutdown. `null` in production (env var never set) and on any code
// path that didn't enter the diagnostic branch.
let bubbleTestCleanup: (() => void) | null = null;

app.whenReady().then(() => {
	//SEC-029 nonce,  userData, SEC-012 CSP, SEC-021 error handlers.
	bootstrapRuntime();
	// register powerMonitor suspend/resume/on-battery
	// listeners. Must run after `app.whenReady()` — powerMonitor
	// is not usable before the app is ready. Idempotent: safe to
	// call more than once (tests, future double-call sites).
	registerPowerMonitorHandlers();

	//pre-create the dashboard BrowserWindow IMMEDIATELY after
	// bootstrapRuntime, BEFORE startPython(). Previously the window
	// was created lazily by `tcp-connect.ts:158`'s `createWindows()`
	// call — which fires only after the Python backend has spawned,
	// bound its TCP port, accepted our socket, AND completed the
	// SEC-018 auth handshake. Cold-start first paint was therefore
	// gated end-to-end by Python spawn + torch import + TCP accept
	// + auth round-trip — typically 2–5s on warm cache, 8–10s+ on
	// cold cache / AV scan. During that entire window the user saw
	// NO UI at all (no window, no tray icon yet because the tray is
	// created by the Python backend, no taskbar entry), with up to
	// 60s of "Python backend failed to start" risk if torch import
	// hung.
	//
	// Pre-creating the window here lets the React bundle start
	// loading immediately so the renderer's "connecting" spinner
	// (App.tsx) actually has a chance to render — turning a
	// multi-second "is this thing even running?" silence into a
	// visible "connecting to backend…" state.
	//
	// `createMainWindow`'s `if (state.mainWindow) return` early
	// return (main-window.ts:206) makes the subsequent
	// `createWindows()` call from tcp-connect.ts:158 a no-op, so the
	// connect callback no longer recreates the window. The
	// early-exit branch in start-python.ts:161-177 already handles
	// `state.mainWindow` being non-null (calls `.destroy()`), so the
	// early-exit dialog path stays correct when Python fails to
	// start.
	//
	// `createWindows()` defaults `forceShow` to `false`, which
	// preserves the START_HIDDEN behavior — an autostarted
	// background instance (`VT_START_HIDDEN=1`) still creates the
	// BrowserWindow off-screen (so opening it later is instant via
	// second-instance / tray "Open app") but leaves no taskbar
	// entry. A normal launch (`START_HIDDEN=false`) shows the
	// window immediately.
	createWindows();

	if (process.env.VT_BUBBLE_TEST === "1") {
		log.warn(
			`${ts()}  ${BUBBLE_CLR}[BUBBLE] VT_BUBBLE_TEST=1 -- showing bubble for diagnostics${RESET}`,
		);
		//delegate the 3-timer diagnostic to `dev/bubble-test.ts`
		// so the production wiring entry point stays wiring-only and the
		// timers are tracked for cleanup on shutdown.
		bubbleTestCleanup = runBubbleTestDiagnostics(state).cleanup;
	}
	startPython();
	//pre-warm the Wayland-without-SNI cache so the
	// `window-all-closed` handler returns instantly instead of
	// blocking on the D-Bus subprocess check (up to 4s worst-case
	// if neither `gdbus` nor `dbus-send` is installed). The check
	// is ~1ms on a warm session bus.
	//
	// Deferred via `setImmediate` so the synchronous
	// `execFileSync` D-Bus probe does NOT block the boot path.
	// `createWindows()` (above) already kicked off the dashboard
	// BrowserWindow creation; `startPython()` (above) returned
	// after spawning the backend without awaiting its readiness.
	// Synchronously calling `isLinuxWaylandWithoutSni()` here
	// would shell out to `gdbus`/`dbus-send` on the same
	// event-loop tick — stalling the `app.whenReady().then(...)`
	// resolution and delaying the dashboard's first `loadURL` /
	// `loadFile` microtask. By deferring to the next tick, the
	// BrowserWindow's `loadURL` Promise gets its first event-loop
	// turn BEFORE we shell out. The cache will still be populated
	// long before the user can possibly trigger
	// `window-all-closed` (a window close is a user-initiated
	// event that requires the React dashboard to have rendered
	// first, which itself requires the Python backend's TCP
	// handshake — both >1 event-loop tick away).
	//
	// Tradeoff vs. an async `execFile` refactor (the "Option B"
	// alternative considered): the probe itself stays
	// synchronous, so the function signature stays synchronous
	// and no caller needs to be refactored — but the probe runs
	// on the next event-loop tick instead of blocking the
	// `whenReady` Promise resolution. The `setImmediate` callback
	// is wrapped in `try/catch` because, unlike the inline call,
	// a throw here would surface as an uncaught exception on the
	// next tick instead of bubbling out of `whenReady().then()`.
	setImmediate(() => {
		try {
			isLinuxWaylandWithoutSni();
		} catch (e) {
			log.warn("[main] tray_available pre-warm (deferred) failed:", e);
		}
	});
});

//SIGTERM/SIGINT → app.quit() → before-quit → stopPython().
// Hard backstop if before-quit hangs. The delay is sized so the
// SIGTERM→SIGKILL escalation in `stop-python.ts` has a guaranteed
// window to fire BEFORE Electron exits:
//   - t=KILL_TIMER_MS                     : killTimer sends SIGTERM
//   - t=KILL_TIMER_MS+ESCALATE_TIMER_MS   : escalateTimer sends SIGKILL
// Pre-fix the backstop was a hardcoded 3000ms — equal to the killTimer
// delay — so on SIGTERM-with-Python-stuck-in-C-extension the unref'd
// backstop fired at t=3s, exited Electron, and the escalateTimer
// (scheduled for t=6s) NEVER fired. Python was orphaned, still holding
// the single-instance mutex. The extra +500ms is a safety margin so the
// backstop can't race ahead of the escalateTimer if the Node timer
// wheel is briefly delayed under load.
//
// The timer is `.unref()`'d so it does NOT keep the event loop alive
// on its own — if all other handles (including the non-`.unref()`'d
// killTimer in stop-python.ts) have settled and Python has exited
// cleanly, Electron can exit promptly without waiting the full 6.5s.
let _signalQuitFired = false;
const signalQuitHandler = () => {
	if (_signalQuitFired) return;
	_signalQuitFired = true;
	try {
		app.quit();
	} catch (e) {
		log.warn("[main] app.quit() from signal handler failed:", e);
		process.exit(0);
	}
	setTimeout(
		() => process.exit(0),
		KILL_TIMER_MS + ESCALATE_TIMER_MS + 500,
	).unref();
};
process.on("SIGTERM", signalQuitHandler);
process.on("SIGINT", signalQuitHandler);

app.on("before-quit", () => {
	app.isQuitting = true;
	stopPython();
	//clear the dev-only bubble-test diagnostic timers so they
	// don't fire `webContents.send` against a destroyed window during
	// slow shutdown. Best-effort — `bubbleTestCleanup` is `null` in
	// production (env var never set) and the cleanup function itself
	// is idempotent (safe to call multiple times).
	if (bubbleTestCleanup) bubbleTestCleanup();
	// P1-1.4: clear our PID file so the next launch doesn't think
	// we're still alive.  Best-effort — if the disk is gone, the
	// stale-PID recovery path will handle it on next start.
	clearElectronPidFile();
});

//(R6-F7): belt-and-suspenders `will-quit` handler.
//removed the 3s forceExitTimer that raced with stopPython's
// killTimer. Now relies on killTimer (no longer .unref()'d) +
// pythonProcess.once('exit') to call app.exit(0).
//if pythonProcess is already null, exit immediately.
let _willQuitStopPythonFired = false;
app.on("will-quit", (event) => {
	if (_willQuitStopPythonFired) return;
	_willQuitStopPythonFired = true;
	event.preventDefault();
	try {
		stopPython();
	} catch (err) {
		log.warn("[main] stopPython failed during will-quit:", err);
	}
	if (state.pythonProcess) {
		state.pythonProcess.once("exit", () => {
			app.exit(0);
		});
	} else {
		app.exit(0);
	}
});

// With close-to-tray, closing the dashboard window just hides it — the
// process keeps running.  So window-all-closed only fires on a real quit
// (last window destroyed) or on macOS when all windows are closed by the
// user.  Guard accordingly.
//
//on Linux Wayland WITHOUT StatusNotifierItem (Sway/Hyprland/dwl/
// river), the Python tray backend sets `_tray_unavailable = True` and
// creates NO tray icon.  Without a tray icon, the user has no UI
// affordance to quit the app after closing the last window.  Detect this
// case on the Electron side (mirroring tray.py::_is_linux_wayland_without_sni)
// and call `app.quit()` so the user isn't stranded.
app.on("window-all-closed", () => {
	if (app.isQuitting) return;
	if (process.platform !== "darwin") {
		//if there's no tray icon to fall back to (Wayland-
		// without-SNI), quit instead of leaving the user stranded.
		if (isLinuxWaylandWithoutSni()) {
			app.quit();
			return;
		}
		// Don't quit: the tray icon + backend keep the app alive.  Quit only
		// happens explicitly via the tray menu.
	}
});

// macOS: clicking the dock icon when no windows are open should re-show
// the dashboard (mirrors second-instance on the other platforms).
app.on("activate", () => {
	if (!state.mainWindow || state.mainWindow.isDestroyed()) {
		createWindows(/* forceShow */ true);
	} else {
		showMainWindow();
	}
});

//the legacy `export { APP_NAME } from "./branding";` re-export
// was removed. The original justification (preserve a stale "lazy-import
// behaviour" from the pre-split 2,321-line `index.ts`) was already
// obsolete after REF-2 split it into submodules. The follow-up
//comment claimed the re-export preserved "the public API
// surface so any external consumer importing from `./index` still
//resolves `APP_NAME`" — but a repo-wide audit
// found ZERO such consumers: every APP_NAME import goes directly to
// `./branding`. Keeping a dead re-export on the wiring-only entry
// point risks confusion (the canonical declaration lives in
// `./branding` per the top-of-file `ALLOWED_COMMANDS` precedent).
// `i18n.ts` already imports APP_NAME directly from `./branding`.
