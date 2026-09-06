/**
 * Shared ordered teardown for the TCP bridge, used by every restart
 * path that keeps the Electron process alive:
 *   - `relaunch-app.ts` dev branch (tray "Restart" in dev mode).
 *   - `restart-backend.ts` (renderer "Lost connection" escalation).
 *
 * Both paths previously carried a copy-pasted ~12-step sequence:
 * destroy the socket, reset backpressure flags, clear buffers / auth /
 * ready flags, zero retry counters, clear the retry timer, bump the
 * generation, clear the heartbeat, and reject all pending requests.
 * Any new `MainState` lifecycle field had to be added in both places —
 * a miss created silently divergent restart paths. This module is the
 * single owner of that sequence (DRY): the callers perform their
 * branch-specific extras (kill, await-exit, renderer reload, env-var
 * cleanup, fresh spawn) inline around a single call here.
 *
 * Ordering contract (do not reorder — the retry-timer clear MUST stay
 * before the generation bump so a stale `tryConnect()` closure can't
 * fire once more against the new generation, and pending rejections
 * MUST come last so callers never observe half-reset state):
 *
 *   1. destroy `state.tcpSocket` (best-effort, failure logged)
 *   2. `state.tcpSocket = null`
 *   3. `_resetIpcBackpressure()` — drop per-renderer rate-limit entries
 *   4. `state.tcpBuffer = Buffer.alloc(0)` — drop stale partial frames
 *   5. `state._tcpAuthed = false`
 *   6. `state.pythonReady = false`
 *   7. `state.pythonExitedEarly = false`
 *   8. `state._hadConnectedBefore = false`
 *   9. `state._tcpRetryCount = 0`
 *  10. clear `state._tcpRetryTimer` (before the generation bump)
 *  11. `state._tcpRetryGeneration++` — invalidate stale retry loops
 *  12. clear `state.heartbeatInterval`
 *  13. reject + delete every `state.pendingRequests` entry with
 *      `new Error(reason)` — `reason` is caller-supplied so each
 *      restart path keeps its own user-facing message ("Application is
 *      restarting" vs "Python backend is restarting").
 *
 * The production relaunch branch in `relaunch-app.ts` deliberately does
 * NOT call this function: it tears the process down via `app.exit(0)`
 * and only resets the subset of state that matters for the exit window
 * (bumping the generation or resetting ready flags there would be dead
 * work).
 */
import { log } from "../logging";
import { state } from "../state";
import { _resetIpcBackpressure } from "./send-to-python";

export function resetTcpBridgeState(reason: string): void {
	try {
		if (state.tcpSocket) state.tcpSocket.destroy();
	} catch (e) {
		// Surface the destroy failure instead of swallowing it.
		log.warn("[BRIDGE-RESET] tcpSocket.destroy failed:", e);
	}
	state.tcpSocket = null;
	// Clear the per-renderer rate-limit Map so destroyed-window
	// entries don't accumulate across backend restarts (each restart
	// creates a fresh BrowserWindow with a fresh webContents.id; the
	// old id's entry would otherwise leak forever).
	_resetIpcBackpressure();
	// Reset the TCP line buffer so stale partial frames from the old
	// backend don't bleed into the next connection.
	state.tcpBuffer = Buffer.alloc(0);
	state._tcpAuthed = false;
	state.pythonReady = false;
	state.pythonExitedEarly = false;
	state._hadConnectedBefore = false;
	state._tcpRetryCount = 0;
	// Clear the pending TCP retry timer BEFORE bumping the
	// generation, so a stale tryConnect closure can't fire once more
	// (creating a fresh socket that immediately hits the generation
	// mismatch and bails — wasted work + brief socket churn).
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	state._tcpRetryGeneration++;
	// Clear the heartbeat interval — the next connect callback will
	// arm a fresh one when the new backend accepts our TCP
	// connection. Without this clear the timer would fire
	// sendToPython() against a dead socket mid-restart.
	if (state.heartbeatInterval) {
		clearInterval(state.heartbeatInterval);
		state.heartbeatInterval = null;
	}
	// Reject pending IPC immediately with the caller's reason so
	// callers don't sit out the per-command timeout on a bridge that
	// is being rebuilt.
	for (const [id, entry] of state.pendingRequests) {
		state.pendingRequests.delete(id);
		entry.reject(new Error(reason));
	}
}
