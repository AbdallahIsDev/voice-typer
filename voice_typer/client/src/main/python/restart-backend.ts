/**
 * Restart ONLY the Python backend process, keeping Electron alive.
 *
 * This is the "Restart backend" recovery option that the renderer's
 * "Lost connection" Retry button escalates to when a plain reconnect
 * probe fails (see `useConnection.ts` `handleRetryConnection`).
 *
 * Contrast with `relaunch-app.ts`:
 *   - `relaunchApp()` — full Electron + Python restart (tray "Restart").
 *     Production mode calls `app.relaunch()` + `app.exit(0)`, replacing
 *     the whole OS process.
 *   - `restartBackend()` — recycles ONLY the Python sidecar: kill +
 *     respawn. Electron, the renderer, and the TCP bridge all stay
 *     alive; the fresh backend connects through the normal
 *     `tcpConnect()` lifecycle when it comes up.
 *
 * Why this is needed beyond a TCP probe: the "Lost connection" screen
 * appears when the TCP bridge dropped. If the backend process itself
 * died or hung (no longer draining its event loop), a reconnect probe
 * can never succeed — the process must be recreated. `restartBackend()`
 * provides that. The renderer only relays here after its probe fails,
 * so a transient TCP flap (socket blip, GC pause) still recovers by
 * plain reconnect without paying the cost of a process kill.
 *
 * Result contract:
 *   - `{ ok: true }` — respawn initiated (or already effectively
 *     handled; the function is idempotent w.r.t. spawn).
 *   - `{ ok: false, reason: "relaunching" }` — a full app relaunch is
 *     already in flight; respawning a Python now would be a wasted
 *     child. Renderer should keep the current behavior (probe only).
 *
 * The kill path mirrors `relaunch-app.ts` dev branch: it uses
 * `killPythonProcessWithSigkillFallback`, which removes all `exit`
 * listeners from the old proc before signalling — so the
 * `start-python.ts` exit handler's "Python backend crashed" dialog +
 * `app.quit()` is NOT triggered by an intentional kill.
 *
 * Security note: this is a renderer→main IPC channel (not a Python
 * IPC command), so it is NOT part of the SEC-002 server command
 * allowlist (`ALLOWED_COMMANDS` parity does not apply). The renderer
 * can only trigger it via the explicit `window.window_.restartBackend`
 * preload exposure — no new Python-side surface is added.
 */
import { log } from "../logging";
import { state } from "../state";
import { killPythonProcessWithSigkillFallback } from "./kill-python";
import { _resetIpcBackpressure } from "./send-to-python";
import { startPython } from "./start-python";
import { clearTcpStartupTimeout } from "./tcp-connect";

export type BackendRestartResult = { ok: boolean; reason?: string };

/**
 * Restart the Python backend in place. Returns the result envelope
 * described in the module doc.
 */
export function restartBackend(): BackendRestartResult {
	// Only Electron-spawned backends can be restarted here. If the
	// backend spawned us (standalone mode, VT_PYTHON_PORT set), the
	// backend is our parent — killing it takes the whole app down.
	if (process.env.VT_PYTHON_PORT && process.env.VT_IPC_TOKEN) {
		log.info(
			"[RESTART-BACKEND] adopted mode (VT_PYTHON_PORT set) — cannot restart parent backend",
		);
		return { ok: false, reason: "adopted" };
	}

	// A full app relaunch replaces the whole process; spawning a
	// backend now would leak an orphan into the moment of teardown.
	if (state._relaunching) {
		log.info(
			"[RESTART-BACKEND] full app relaunch in flight — skipping backend-only restart",
		);
		return { ok: false, reason: "relaunching" };
	}

	//clear the TCP startup timeout FIRST so the 60s "backend failed
	// to start" deadline can't fire mid-restart and trip the false
	// dialog + app.quit() (same ordering as relaunch-app.ts).
	clearTcpStartupTimeout();

	// Kill old Python via the shared SIGTERM->SIGKILL-fallback helper.
	// It strips the exit listeners first, so start-python.ts's
	// crash-exit handler doesn't fire for this intentional kill.
	killPythonProcessWithSigkillFallback("dev");

	// ── Reset TCP bridge state (mirrors relaunch-app.ts dev branch) ──
	try {
		if (state.tcpSocket) state.tcpSocket.destroy();
	} catch (e) {
		log.warn("[RESTART-BACKEND] tcpSocket.destroy failed:", e);
	}
	state.tcpSocket = null;
	//clear per-renderer rate-limit map so destroyed-window entries
	// don't accumulate across backend restarts.
	_resetIpcBackpressure();
	//reset the TCP line buffer so stale partial frames from the old
	// backend don't bleed into the next connection.
	state.tcpBuffer = Buffer.alloc(0);
	state._tcpAuthed = false;
	state.pythonReady = false;
	state.pythonExitedEarly = false;
	state._hadConnectedBefore = false;
	state._tcpRetryCount = 0;
	// R6-F6: clear the pending TCP retry timer BEFORE bumping the
	// generation, so a stale tryConnect closure can't fire once more.
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	state._tcpRetryGeneration++;
	//clear the heartbeat interval — the next connect callback will
	// arm a fresh one.
	if (state.heartbeatInterval) {
		clearInterval(state.heartbeatInterval);
		state.heartbeatInterval = null;
	}

	// Reject pending IPC immediately with a clear error.
	for (const [id, entry] of state.pendingRequests) {
		state.pendingRequests.delete(id);
		entry.reject(new Error("Python backend is restarting"));
	}

	// Spawn a fresh Python. Wrap in try/finally so a spawn-throw can't
	// leave anything half-cleaned (startPython is synchronous in
	// spawning; the connection happens async via tcpConnect).
	try {
		startPython();
		log.info("[RESTART-BACKEND] fresh Python backend spawned");
		return { ok: true };
	} catch (e) {
		log.error("[RESTART-BACKEND] startPython() threw during restart:", e);
		return { ok: false, reason: "relaunching" };
	}
}
