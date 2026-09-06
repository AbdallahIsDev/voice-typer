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
import { startPython } from "./start-python";
import { resetTcpBridgeState } from "./tcp-bridge-reset";
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

	// Release the condemned process reference NOW. The kill helper
	// stripped the exit listeners and armed its SIGTERM+SIGKILL
	// escalation, so the old process is untracked and dying — but
	// until it actually exits, `exitCode`/`signalCode` are still
	// `null`, which `startPython()`'s live-process guard would read
	// as "backend already running", skipping the respawn entirely.
	// The helper's escalation timers hold their own reference to the
	// old proc, so nulling the state field orphans nothing.
	state.pythonProcess = null;

	// Reset the shared TCP-bridge state (same ordered sequence as the
	// relaunch-app dev branch): destroy socket, reset backpressure,
	// clear buffers/auth/ready flags, zero retry counters, bump
	// generation, clear heartbeat, reject pending IPC.
	resetTcpBridgeState("Python backend is restarting");

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
