/**
 * Spawn (or adopt) the Python backend and connect to it over TCP.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * Two modes:
 *   - If `VT_PYTHON_PORT` + `VT_IPC_TOKEN` are set, a Python backend
 *     spawned us — skip spawning and connect directly to the existing
 *     backend (we must not kill our parent).
 *   - Otherwise, spawn a fresh backend via `pythonArgs()` and connect.
 *
 * The exit handler distinguishes:
 *   - Early exit (before first connect) → show "only one instance" dialog
 *     and quit.
 *   - Non-zero exit (crash) → quit so the user isn't left with a broken UI.
 *   - Clean exit (code 0) → tray "Restart" path; trigger `relaunchApp()`
 *     unless `app.isQuitting` is already set (tray "Quit" also exits 0).
 */
import { spawn } from "node:child_process";
import { app, dialog } from "electron";
//route lifecycle messages through the structured
// `log` logger so they persist to `electron-main.log` (with 5 MiB
// rotation) and `electron-lifecycle.log` (opt-in INFO persistence)
// instead of being lost in packaged builds where `console.warn` has
// no terminal attached.
import { APP_NAME } from "../branding";
import { computeConfigDir } from "../config-dir";
import { IPC_PORT, IPC_TOKEN } from "../constants";
import { mainT } from "../i18n";
import { log } from "../logging";
import { state } from "../state";
import { pythonArgs } from "./python-args";
import { relaunchApp } from "./relaunch-app";
import { _resetStopPythonFlagsForRestart } from "./stop-python";
import { clearTcpStartupTimeout, tcpConnect } from "./tcp-connect";

/**
 * RELIABILITY-002: the old `killStalePython()` function used `wmic`
 * (deprecated in Win11 24H2+) and `taskkill /T /F` to scan for and
 * kill stale Python backend processes.  This was fragile and
 * dangerous.  The function has been removed.  Single-instance
 * enforcement is now handled by two independent mechanisms:
 *
 *   - Electron side: `app.requestSingleInstanceLock()` ensures
 *     only one Electron process runs.
 *   - Python side: `_ensure_single_instance()` in app.py uses a Win32
 *     named mutex (`VoiceTyperSingleInstance`) to ensure only one
 *     Python backend runs.
 *
 * If Electron starts and a Python backend is already running (e.g.
 * from autostart), `tcpConnect()` will successfully connect to it and
 * adopt it — no killing needed.  If no Python is listening, Electron
 * spawns a new one via `startPython()`.
 */
export function startPython() {
	// Idempotence guard: if the previously-spawned backend is still
	// running, do NOT start a second one. Node's ChildProcess reports
	// `exitCode: null` while the process is running and `signalCode:
	// null` until it is terminated by a signal — both null means
	// alive (the same liveness check used by kill-python.ts and
	// stop-python.ts). Without this guard, a spurious or fast
	// suspend/resume cycle (powerMonitor `resume` while the old
	// backend is still alive — e.g. suspend inhibited on AC power, or
	// resume firing while stopPython's force-kill grace period is
	// still in flight) would spawn a SECOND backend. The second
	// backend cannot acquire the Python-side `VoiceTyperSingleInstance`
	// mutex, exits early, and the early-exit handler tears the whole
	// app down with a misleading "only one instance" dialog — the app
	// quitting ITSELF over its own double-spawn. All other spawn
	// paths are unaffected: `state.pythonProcess` is null on the
	// initial boot, the dev-mode relaunch awaits the old process's
	// exit before calling this, and restart-backend releases the
	// condemned process reference before respawning.
	if (
		state.pythonProcess &&
		state.pythonProcess.exitCode === null &&
		state.pythonProcess.signalCode === null
	) {
		log.info(
			"[STARTUP] startPython() called while the backend is still running — no-op",
		);
		return;
	}
	// Increment the retry generation to stop any stale TCP retry loops.
	// Each tryConnect() closure captures this value at creation time;
	// after incrementing, all existing close/error handlers will see
	// a mismatch and stop retrying.
	// R6-F6: clear any pending retry timer BEFORE bumping the
	// generation, so a stale `tryConnect()` doesn't fire once more
	// against the new generation (which would create a fresh socket
	// that immediately bails on the generation mismatch).
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	state._tcpRetryGeneration++;

	//reset the stop-python idempotency flags so the new
	// backend lifecycle starts with a clean stop state. Without
	// this, any prior `stopPython()` call (e.g. from a circuit-
	// breaker trip during the previous backend lifecycle) would
	// leave `isStopping`/`isStopped` latched, making all future
	// `stopPython()` calls permanent no-ops — the backend could
	// not be stopped again after a relaunch. Also clears any
	// armed `killTimer` left over from the prior stop cycle.
	_resetStopPythonFlagsForRestart();
	//clear the TCP startup timeout so the next
	// `tcpConnect()` arms a fresh 60s window. Without this clear,
	// `tcpConnect()`'s `if (_tcpStartupTimeoutTimer === null)`
	// guard sees the timer from the PREVIOUS connect attempt
	// (which may have been ~50s into its 60s window) and skips
	// arming a fresh one — so a dev-mode restart that takes >10s
	// to spawn + import torch would trip the stale timer's
	// "Python backend failed to start" dialog + `app.quit()`
	//prematurely. This is the exact race  documents.
	clearTcpStartupTimeout();

	// P1-1.2: if VT_PYTHON_PORT is set, a Python backend spawned us
	// (standalone mode — user ran `VoiceTyper` from a terminal).
	// The backend is already listening on VT_PYTHON_PORT with the
	// session token from VT_IPC_TOKEN.  Skip spawning a fresh
	// backend and connect directly.  pythonProcess stays null so
	// stopPython() becomes a no-op and we don't try to kill the
	// backend (which is our parent).
	if (process.env.VT_PYTHON_PORT && process.env.VT_IPC_TOKEN) {
		log.info(
			`[STARTUP] VT_PYTHON_PORT=${process.env.VT_PYTHON_PORT} set — ` +
				"connecting to existing backend (no spawn)",
		);
		// tcpConnect will use IPC_PORT (which reads VT_PYTHON_PORT at
		// module load) and IPC_TOKEN (which reads VT_IPC_TOKEN at
		// module load) for the auth line.
		tcpConnect(IPC_PORT);
		return;
	}

	const [exe, args] = pythonArgs();
	// Spawn with inherit stdio — stdout/stderr go to the Electron
	// console (terminal), NOT to pipes.  This eliminates the
	// unbuffered-pipe-write slowdown during torch import.
	// IPC happens via TCP instead of pipe parsing.
	const proc = spawn(exe, args, {
		stdio: "inherit",
		env: {
			...process.env,
			// KMP_DUPLICATE_LIB_OK avoids libiomp5 deadlock when process
			// has no console stdin.
			KMP_DUPLICATE_LIB_OK: "TRUE",
			// SEC-018: per-launch IPC session token.  The Python IPC server
			// reads this from the env and requires the Electron client to
			// send a matching {"type":"auth","token":"..."} message as the
			// first TCP line.
			VOICE_TYPER_IPC_TOKEN: IPC_TOKEN,
			// Startup-timeline marker (see startup_timeline.py): lets the
			// backend log how long it took from THIS spawn to its first
			// log line, splitting the launch gap into electron-boot vs
			// backend-init on every startup.
			VOICE_TYPER_SPAWN_EPOCH_MS: String(Date.now()),
			// SPLIT-BRAIN FIX: pin the backend's config dir to the SAME
			// directory the Electron main resolved.  The Python
			// `_config_dir()` prefers legacy `~/.voice-typer` when it
			// exists, but a STALE packaged build (PyInstaller backend
			// built before the legacy-first resolution, or running with a
			// different default) can fall through to `%APPDATA%/voice-typer`
			// — giving the installed app a DIFFERENT config.json than the
			// dev app.  The user then sees settings "come back" (each app
			// reads/writes its own config).  Explicitly setting
			// `VOICE_TYPER_CONFIG_DIR` here makes the spawned backend use
			// exactly what the Electron main resolved, so dev and
			// production can never diverge.
			VOICE_TYPER_CONFIG_DIR: computeConfigDir(),
		},
	});
	state.pythonProcess = proc;

	log.info(`spawned Python backend (PID=${proc.pid})`);

	// Connect via TCP (will retry until Python's TCP server is ready).
	tcpConnect(IPC_PORT);

	proc.on("exit", (code) => {
		//log the exit code prominently so support staff
		// can diagnose early exits without fishing through stdout.
		// `code` is `null` when the process was killed by a signal
		// (Node surfaces this as `null` rather than the negative
		// signal number); log it as `"signal"` in that case so the
		// log line is unambiguous.
		const codeStr = code === null ? "signal" : String(code);
		log.info(`Python process exited (code=${codeStr})`);
		//short-circuit when the spawn-failure `error`
		// handler has already fired. Node emits `error` then
		// `exit` (with a negative code) on spawn failure
		// (ENOENT/EACCES). The `error` handler sets
		// `state.pythonExitedEarly = true` and shows a clear
		// "backend not found" dialog. Without this guard the
		// `exit` handler would fall through to the early-exit
		// branch below and show a *second*, misleading
		// "single instance already running" dialog. Both
		// handlers fire synchronously in the same tick; this
		// check makes the second a no-op.
		if (state.pythonExitedEarly) return;
		if (!state.pythonReady) {
			state.pythonExitedEarly = true;
			state.pythonProcess = null;
			for (const [id, entry] of state.pendingRequests) {
				state.pendingRequests.delete(id);
				entry.reject(new Error("Python backend exited early"));
			}
			if (state.mainWindow) {
				//(fix): use `.destroy()` instead of `.close()`.
				// `.close()` fires the `close` event, which is intercepted
				// by the close-to-tray handler in
				// `windows/main-window.ts:125-132` (`event.preventDefault()`
				// when `!app.isQuitting`). At this point `app.isQuitting`
				// is false (we call `app.quit()` below, but `before-quit`
				// hasn't fired yet), so `.close()` is intercepted and the
				// window is merely HIDDEN, not destroyed. Setting
				// `state.mainWindow = null` then orphans a hidden
				// BrowserWindow (with its `nativeTheme.on("updated")`
				// listener, webContents, and renderer process still
				// attached). `.destroy()` bypasses the close event and
				// actually tears down the window + renderer.
				state.mainWindow.destroy();
				state.mainWindow = null;
			}
			//include the actual exit code in the
			// dialog body so the user (and support staff)
			// can distinguish single-instance collisions from
			// missing-model / port-collision / token-mismatch /
			// syntax-error / OOM crashes. The prior dialog
			// showed the same "Only one instance can run"
			// message for ALL early exits — misleading for
			// every non-single-instance failure mode. The
			// localized single-instance message stays as the
			// first line (preserving the existing translation
			// surface), with the exit code appended as a
			// trailing diagnostic line. Exit code is also
			// surfaced in the log line above so post-mortem
			// diagnosis doesn't depend on the user transcribing
			// the dialog.
			const baseMsg = mainT("dialog.singleInstance.message");
			const earlyExitMsg = `${baseMsg}\n\n${mainT("dialog.pythonBackend.earlyExitSuffix", { code: codeStr })}`;
			dialog.showErrorBox(mainT("dialog.singleInstance.title"), earlyExitMsg);
			app.quit();
		} else if (code !== 0) {
			// Non-zero exit code = crash. Shut down Electron so the user
			// isn't left with a broken UI that spams TCP reconnect errors
			// (ENOBUFS on Windows).
			//surface a user-visible error dialog before quitting so
			// the user has an actionable message instead of a silent app
			// exit. Distinguish `code === null` (POSIX signal-based exit,
			//e.g. SIGSEGV/SIGABRT — `null !== 0` evaluates true, so
			// signal-based crashes used to silently fall through this
			// branch with no distinguishing message) from `code !== 0`
			// (numeric exit) with separate message bodies so signal
			// diagnostics are not lost. Both bodies are localized via
			// `dialog.pythonBackend.*` keys.
			const crashTitle = mainT("dialog.pythonBackend.crashTitle");
			const crashBody =
				code === null
					? mainT("dialog.pythonBackend.crashSignalBody", {
							appName: APP_NAME,
						})
					: mainT("dialog.pythonBackend.crashExitBody", {
							appName: APP_NAME,
							code: String(code),
						});
			try {
				dialog.showErrorBox(crashTitle, crashBody);
			} catch (e) {
				// dialog may not be available in headless mode
				// (CI, `DISPLAY` unset, or pre-app-ready). Log at debug so
				// the failure is observable in the diagnostic log without
				// spamming the default level — mirrors the debug-log pattern
				// used in `relaunch-app.ts:351`.
				log.debug("[PYTHON] crash dialog.showErrorBox failed (non-fatal):", e);
			}
			state.pythonProcess = null;
			state.tcpSocket = null;
			state._tcpAuthed = false;
			for (const [id, entry] of state.pendingRequests) {
				state.pendingRequests.delete(id);
				entry.reject(new Error("Python backend disconnected"));
			}
			app.quit();
		} else {
			// Exit code 0 = clean restart requested by the user via the
			// tray "Restart" menu item.  Python's restart_app() sends a
			// "relaunch_app" event (handled in handleMessage above)
			// and then exits cleanly via sys.exit(0).
			//
			// We respond by relaunching the ENTIRE Electron process
			// (which in turn spawns a fresh Python backend).  This is the
			// user's explicit request: "close the entire process, the
			// entire backend, and the entire Electron application;
			// everything should be closed and opened again."
			//
			// The full-relaunch design replaces the old "Python-only
			// restart" which tried to keep Electron alive while swapping
			// the Python backend.  That design had multiple race
			// conditions (TCP close racing with restart_ack delivery,
			// tcpSocket set before connect causing auth failures,
			// _restarting flag cleared too early) that produced the
			// cascading "Error: Timeout" and "Python socket closed"
			// errors the user observed.
			//
			// If the "relaunch_app" event was already received and
			// relaunchApp() was called, _relaunching is true and we skip
			// the duplicate call (relaunchApp is idempotent).  This branch
			// is the FALLBACK for the race where the event was lost (TCP
			// closed before Electron processed the data).
			state.pythonProcess = null;
			// P1-2c (Round 0 forward-port): also guard against
			// `app.isQuitting` — the tray "Quit" path sends
			// `quit_app` to Python, which then exits with code 0.
			// Without this check, the clean-exit branch would
			// treat the tray Quit as a lost "relaunch_app"
			// event and trigger an unwanted full app relaunch
			// instead of letting the user-initiated quit
			// complete.  The `app.isQuitting` flag is set in
			// the `before-quit` handler in `index.ts` which
			// fires synchronously when the `quit_app` IPC
			// handler calls `app.quit()`.
			if (!state._relaunching && !app.isQuitting) {
				log.info(
					"[RESTART] Python exited cleanly (code 0) — triggering full app relaunch",
				);
				relaunchApp();
			}
		}
	});

	//handle spawn failures (ENOENT — Python not on PATH,
	// bundled exe missing, EACCES, etc.). Without this listener, Node
	// emits the 'error' event with no listener → uncaughtException →
	// the crash circuit breaker in bootstrap.ts trips after 5 errors.
	// The 'exit' event then fires with a negative code, hitting the
	// early-exit branch above and showing the misleading "single
	// instance already running" dialog. Show a clear error instead.
	proc.on("error", (err: NodeJS.ErrnoException) => {
		log.error("[PYTHON] spawn failed:", err);
		state.pythonProcess = null;
		state.pythonExitedEarly = true;
		try {
			dialog.showErrorBox(
				mainT("dialog.pythonBackend.notFoundTitle"),
				mainT("dialog.pythonBackend.notFoundBody", {
					appName: APP_NAME,
					message: err.message,
				}),
			);
		} catch (e) {
			// dialog may not be available in headless mode
			// (CI, `DISPLAY` unset, or pre-app-ready). Log at debug so
			// the failure is observable in the diagnostic log without
			// spamming the default level — mirrors the debug-log pattern
			// used in `relaunch-app.ts:351`.
			log.debug(
				"[PYTHON] spawn-failure dialog.showErrorBox failed (non-fatal):",
				e,
			);
		}
		app.quit();
	});
}
