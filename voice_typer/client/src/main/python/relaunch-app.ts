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
 * Idempotent: if called twice (e.g. once from the "relaunch_app"
 * TCP event handler and once from the pythonProcess exit handler), the
 * second call is a no-op because `_relaunching` is already true.
 *
 * `app.exit(0)` is used (not `app.quit()`) because we want immediate
 * termination without firing `before-quit` (which would call
 * `stopPython()` and try to send `quit_app` to a Python that's
 * already exiting — a waste of 3 seconds on the kill timer).  The
 * Python process is force-killed directly here instead.
 *
 * ER-26: dev-mode relaunch now AWAITS the old proc's exit event (with
 * a 3s SIGKILL fallback) BEFORE spawning a fresh backend, reuses
 * `stopPython()` instead of duplicating the kill logic, and resets
 * `_relaunching` only AFTER `startPython()` completes. Production
 * mode still uses the synchronous `app.relaunch()` + `app.exit(0)`
 * path. The function is `async` so the dev-mode branch can `await`
 * the proc exit.
 *
 * ER-29: calls `clearTcpStartupTimeout()` before `startPython()` so
 * the fresh backend gets a fresh 60s startup window.
 */
import path from "node:path";
import { app } from "electron";
import { log } from "../logging";
import { state } from "../state";
import { startPython } from "./start-python";
import { stopPython } from "./stop-python";
import { clearTcpStartupTimeout } from "./tcp-connect";

const RESTART_KILL_TIMEOUT_MS = 3000;

export async function relaunchApp(): Promise<void> {
	// Idempotency guard: if a relaunch is already in flight, do nothing.
	if (state._relaunching) {
		log.warn(
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

	// Reject pending IPC and clear TCP retry timer synchronously
	// (before the await) so the tcp-retry-timer contract holds.
	for (const [id, entry] of state.pendingRequests) {
		state.pendingRequests.delete(id);
		entry.reject(new Error("Application is restarting"));
	}
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}

	// ── Dev mode: keep Electron alive, just restart Python ──────────
	// Production: app.relaunch() + app.exit(0) fully replaces the OS process.
	if (!app.isPackaged) {
		log.warn(
			"[RESTART] Dev mode: restarting Python backend (Electron stays alive)",
		);

		// ER-26: reuse stopPython() to kill the old proc instead of
		// duplicating the SIGTERM + 3s SIGKILL fallback logic. This
		// guarantees `quit_app` is sent once, the killTimer is armed
		// once, and the idempotency flags are flipped correctly.
		// stopPython() returns synchronously but arms an async kill.
		// We then await the proc's exit event (or the 3s SIGKILL
		// fallback) before spawning the fresh backend so the old proc
		// doesn't hold the VoiceTyperSingleInstance mutex when the new
		// one tries to acquire it.
		const oldProc = state.pythonProcess;
		if (oldProc) {
			stopPython();
			await new Promise<void>((resolve) => {
				let settled = false;
				const done = () => {
					if (settled) return;
					settled = true;
					resolve();
				};
				oldProc.once("exit", () => done());
				// SIGKILL fallback if the proc is stuck in a C
				// extension and ignores the SIGTERM from stopPython().
				setTimeout(() => {
					if (state.pythonProcess) {
						try {
							state.pythonProcess.kill("SIGKILL");
						} catch {
							/* best-effort */
						}
					}
					done();
				}, RESTART_KILL_TIMEOUT_MS).unref();
			});
		}
		state.pythonProcess = null;

		// Clean up TCP + state
		try {
			if (state.tcpSocket) state.tcpSocket.destroy();
		} catch (e) {
			// GT-B3-7: surface the destroy failure instead of swallowing.
			log.warn("[RESTART] dev: tcpSocket.destroy failed:", e);
		}
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

		// ER-29: clear the TCP startup timeout so the fresh backend
		// gets a fresh 60s startup window.
		clearTcpStartupTimeout();

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
		} catch (e) {
			// GT-B3-7: surface the reload failure instead of swallowing.
			log.warn("[RESTART] dev: mainWindow reload failed:", e);
		}

		startPython();
		// ER-26: reset _relaunching only AFTER startPython() completes.
		state._relaunching = false;
		log.warn(
			"[RESTART] Dev mode restart complete -- waiting for new backend",
		);
		return;
	}

	log.warn(
		"[RESTART] Production mode: relaunching entire Electron application",
	);

	// HIGH-31 / ELEC-1: set isQuitting ONLY in the production branch
	// so the close handler doesn't preventDefault during app.exit(0)
	// teardown.  The dev branch above does NOT set it (dev mode keeps
	// Electron alive and must preserve close-to-tray behavior).
	app.isQuitting = true;

	// ER-29: clear the TCP startup timeout (production exit path).
	clearTcpStartupTimeout();

	// Kill old Python (remove exit listener first to prevent race)
	try {
		if (state.pythonProcess) {
			const proc = state.pythonProcess;
			proc.removeAllListeners("exit");
			if (!proc.killed) proc.kill();
			// PVT-G5-039: SIGKILL fallback — same pattern as
			// the dev branch above and stop-python.ts.
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
			}, RESTART_KILL_TIMEOUT_MS);
			proc.once("exit", () => clearTimeout(killTimer));
		}
	} catch (e) {
		// GT-B3-7: surface the kill failure instead of swallowing.
		log.warn("[RESTART] prod: kill old Python failed:", e);
	}
	state.pythonProcess = null;
	try {
		if (state.tcpSocket) state.tcpSocket.destroy();
	} catch (e) {
		// GT-B3-7: surface the destroy failure instead of swallowing.
		log.warn("[RESTART] prod: tcpSocket.destroy failed:", e);
	}
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
