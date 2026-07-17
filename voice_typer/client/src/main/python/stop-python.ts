/**
 * Stop the Python backend: send `quit_app` over TCP, then force-kill
 * after a 3s grace period.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * RW-10: stops the heartbeat interval first so we don't queue a
 * heartbeat onto the dying socket while `quit_app` is in flight.
 * The interval is also cleared in the TCP close handler, but stopping
 * it here avoids the race where the 5s tick fires between
 * sendToPython("quit_app") and the socket actually closing.
 */
import { state } from "../state";
import { sendToPython } from "./send-to-python";

export function stopPython() {
	// RW-10: stop the heartbeat interval first so we don't queue a
	// heartbeat onto the dying socket while ``quit_app`` is in
	// flight.  The interval is also cleared in the TCP close
	// handler, but stopping it here avoids the race where the
	// 5s tick fires between sendToPython("quit_app") and the
	// socket actually closing.
	if (state.heartbeatInterval) {
		clearInterval(state.heartbeatInterval);
		state.heartbeatInterval = null;
	}
	if (!state.pythonProcess) return;
	sendToPython({ type: "quit_app" }).catch(() => {});
	const killTimer = setTimeout(() => {
		if (state.pythonProcess) {
			state.pythonProcess.kill();
			state.pythonProcess = null;
		}
	}, 3000);
	// P1-2c (Round 0 forward-port): use `.once` so the listener is
	// auto-removed after firing. `stopPython()` may be called more than
	// once for the same live `pythonProcess` (e.g. shutdown sequence +
	// before-quit handler), and `.on` would accumulate a fresh listener
	// per call — eventually tripping Node's default maxListeners=10
	// warning. `.once` ensures each registered listener fires at most
	// one time and is then cleaned up.
	state.pythonProcess.once("exit", () => clearTimeout(killTimer));
}
