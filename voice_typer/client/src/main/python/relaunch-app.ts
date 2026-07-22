/**
 * Relaunch the entire Electron application: kill any lingering Python
 * backend, then ask the OS to start a fresh Electron process and exit
 * the current one immediately.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * This is the implementation of the tray "Restart" menu item.  The
 * user explicitly requested that "everything should be closed and
 * opened again" — both the Python backend AND the Electron shell.
 * This eliminates all cross-process state coordination races that
 * the old "Python-only restart" design suffered from.
 *
 * Idempotent: if called twice (e.g. once from the "relaunch_electron"
 * TCP event handler and once from the pythonProcess exit handler), the
 * second call is a no-op because `_relaunching` is already true.
 *
 * `app.exit(0)` is used (not `app.quit()`) because we want immediate
 * termination without firing `before-quit` (which would call
 * `stopPython()` and try to send `quit_app` to a Python that's
 * already exiting — a waste of 3 seconds on the kill timer).  The
 * Python process is force-killed directly here instead.
 */
import path from "node:path";
import { app } from "electron";
import { state } from "../state";
import { startPython } from "./start-python";

export function relaunchApp(): void {
	// Idempotency guard: if a relaunch is already in flight, do nothing.
	if (state._relaunching) {
		console.warn(
			"[RESTART] relaunchApp() called but already relaunching — no-op",
		);
		return;
	}
	state._relaunching = true;
	state._restartTriggered = true;
	// HIGH-31 / ELEC-1: ``app.isQuitting = true`` is needed ONLY for
	// the production ``app.exit(0)`` path (so the close handler
	// doesn't preventDefault during teardown).  Setting it here
	// unconditionally leaks into the dev-mode branch — after a
	// dev-mode "Restart", ``app.isQuitting`` stays ``true`` for the
	// rest of the process lifetime, so the next X-click DESTROYS
	// the window instead of hiding it (close-to-tray).  Moved into
	// the production-only branch below.

	// ── Dev mode: keep Electron alive, just restart Python ──────────
	// Production: app.relaunch() + app.exit(0) fully replaces the OS process.
	if (!app.isPackaged) {
		console.warn(
			"[RESTART] Dev mode: restarting Python backend (Electron stays alive)",
		);

		// Kill old Python (remove exit listener first to prevent race)
		try {
			if (state.pythonProcess) {
				const proc = state.pythonProcess;
				proc.removeAllListeners("exit");
				if (!proc.killed) proc.kill("SIGTERM");
				// PVT-G5-039: SIGKILL fallback — if Python
				// doesn't exit within 3s (stuck in a C
				// extension like torch/sounddevice),
				// force-kill it so the old process
				// doesn't survive and hold the
				// VoiceTyperSingleInstance mutex.
				const killTimer = setTimeout(() => {
					if (!proc.killed) {
						try {
							proc.kill("SIGKILL");
						} catch {
							/* best-effort */
						}
					}
				}, 3000);
				proc.once("exit", () => clearTimeout(killTimer));
			}
		} catch {
			/* best-effort */
		}
		state.pythonProcess = null;

		// Clean up TCP + state
		try {
			if (state.tcpSocket) state.tcpSocket.destroy();
		} catch {}
		state.tcpSocket = null;
		// PVT-G5-007: reset the TCP line buffer so stale partial
		// frames from the previous backend don't bleed into the
		// next connection.
		state.tcpBuffer = "";
		state._tcpAuthed = false;
		state.pythonReady = false;
		state.pythonExitedEarly = false;
		state._hadConnectedBefore = false;
		state._tcpRetryCount = 0;
		// R6-F6: clear the pending TCP retry timer BEFORE bumping the
		// generation, otherwise the stale `tryConnect()` invocation
		// would fire once more (creating a fresh socket that
		// immediately hits the generation mismatch and bails — wasted
		// work + a brief window of "extra" socket churn).
		if (state._tcpRetryTimer) {
			clearTimeout(state._tcpRetryTimer);
			state._tcpRetryTimer = null;
		}
		state._tcpRetryGeneration++;
		// RW-10: clear the heartbeat interval — the next connect
		// callback will start a fresh one when the new backend
		// accepts our TCP connection.
		if (state.heartbeatInterval) {
			clearInterval(state.heartbeatInterval);
			state.heartbeatInterval = null;
		}

		// Reject pending IPC, reload renderer, spawn fresh Python
		for (const [id, entry] of state.pendingRequests) {
			state.pendingRequests.delete(id);
			entry.reject(new Error("Application is restarting"));
		}
		try {
			if (state.mainWindow && !state.mainWindow.isDestroyed()) {
				if (process.env.ELECTRON_RENDERER_URL) {
					state.mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
				} else {
					state.mainWindow.loadFile(
						path.join(__dirname, "../renderer/index.html"),
					);
				}
			}
		} catch {}

		startPython();
		state._relaunching = false;
		console.warn(
			"[RESTART] Dev mode restart complete -- waiting for new backend",
		);
		return;
	}

	console.warn(
		"[RESTART] Production mode: relaunching entire Electron application",
	);

	// HIGH-31 / ELEC-1: set isQuitting ONLY in the production branch
	// so the close handler doesn't preventDefault during app.exit(0)
	// teardown.  The dev branch above does NOT set it (dev mode keeps
	// Electron alive and must preserve close-to-tray behavior).
	app.isQuitting = true;

	// Kill old Python (remove exit listener first to prevent race)
	try {
		if (state.pythonProcess) {
			const proc = state.pythonProcess;
			proc.removeAllListeners("exit");
			if (!proc.killed) proc.kill();
			// PVT-G5-039: SIGKILL fallback — same pattern as
			// the dev branch above and stop-python.ts:38-43.
			// If Python is stuck in a C extension and ignores
			// SIGTERM, force-kill after 3s so the old process
			// doesn't hold the single-instance mutex.
			const killTimer = setTimeout(() => {
				if (!proc.killed) {
					try {
						proc.kill("SIGKILL");
					} catch {
						/* best-effort */
					}
				}
			}, 3000);
			proc.once("exit", () => clearTimeout(killTimer));
		}
	} catch {}
	state.pythonProcess = null;
	try {
		if (state.tcpSocket) state.tcpSocket.destroy();
	} catch {}
	state.tcpSocket = null;
	// PVT-G5-007: reset the TCP line buffer (see dev branch above).
	state.tcpBuffer = "";
	state._tcpAuthed = false;
	// R6-F6: clear the pending TCP retry timer so a stale
	// `tryConnect()` doesn't fire during the brief exit window.
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	// RW-10: clear the heartbeat interval — process is exiting and
	// we don't want the timer to fire sendToPython() against a
	// dead socket during the brief exit window.
	if (state.heartbeatInterval) {
		clearInterval(state.heartbeatInterval);
		state.heartbeatInterval = null;
	}

	// Reject pending IPC and spawn a brand new OS process
	for (const [id, entry] of state.pendingRequests) {
		state.pendingRequests.delete(id);
		entry.reject(new Error("Application is restarting"));
	}
	app.relaunch({ args: process.argv.slice(1) });
	app.exit(0);
}
