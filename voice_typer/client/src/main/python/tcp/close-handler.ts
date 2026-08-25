/**
 * TCP close handler: ownership-scoped teardown.
 *
 * Split out of `tcp-connect.ts`. Owns the `client.on("close", ...)`
 * registration: the ownership-scoped buffer/socket/auth/heartbeat
 * cleanup, the pending-request rejection, the outbound replay-queue
 * reset, and the delegation to the retry scheduler.
 */
import type { Socket } from "node:net";
import { state } from "../../state";
import { _resetPendingOutbound } from "../send-to-python";
import { scheduleTcpRetryAfterClose } from "./retry-scheduler";

export function installTcpCloseHandler(opts: {
	client: Socket;
	port: number;
	retryGen: number;
	tryConnect: () => void;
}): void {
	const { client, port, retryGen, tryConnect } = opts;
	client.on("close", () => {
		//reset the TCP line buffer on close
		// so stale partial frames from the previous
		// connection don't bleed into the next one.
		//
		//scope the buffer clear to the socket that actually owns
		// the current state. Previously the clear ran unconditionally — a
		// STALE socket close (e.g. an old retry-generation socket finishing
		// TCP teardown after a newer socket already connected) would wipe
		// the live socket's in-flight partial frame.
		//
		//the heartbeat-interval clear and pending-request reject
		// loop were left UNCONDITIONAL in the scoping fix. A stale socket
		// closing AFTER a newer socket had installed a heartbeat + pending
		// requests would wipe the live socket's heartbeat (→ Python-side
		// heartbeat watchdog fires after ~120s → Python exits) AND reject
		// the live socket's in-flight IPC calls. Scoping them to
		//`state.tcpSocket === client` (matching the  pattern for
		// buffer/socket/auth) ensures only the owning socket's teardown
		// releases these resources.
		if (state.tcpSocket === client) {
			state.tcpBuffer = Buffer.alloc(0);
			state.tcpSocket = null;
			state._tcpAuthed = false;
			//stop the heartbeat interval — the socket is
			// dead, so further sendToPython() calls would just
			// queue up rejected promises.  A fresh interval is
			// started in the connect callback when the next
			// retry succeeds.
			if (state.heartbeatInterval) {
				clearInterval(state.heartbeatInterval);
				state.heartbeatInterval = null;
			}
			// reject all outstanding pendingRequests so the UI
			// doesn't hang forever. Without this, every `await
			// window.electronAPI.python(...)` would leak when the socket
			// died - the renderer's loading spinners would never resolve.
			const closeErr = state._relaunching
				? new Error("Application is restarting")
				: new Error("Python socket closed");
			for (const [id, entry] of state.pendingRequests) {
				state.pendingRequests.delete(id);
				entry.reject(closeErr);
			}
			// Transient-disconnect replay queue:
			// when ``state._relaunching`` is true the
			// process is about to exit — queued
			// idempotent commands would never be
			// flushed (no reconnect will happen), so
			// reject them with the same "Application
			// is restarting" error so the caller's
			// promise settles. When ``_relaunching``
			// is false, the queue is PRESERVED so it
			// can be flushed on the next successful
			// reconnect (the whole point of the
			// queue — see ``send-to-python.ts``'s
			// ``_pendingOutbound``).
			if (state._relaunching) {
				_resetPendingOutbound("Application is restarting");
			}
		}
		// If a newer retry generation is active, stop retrying.
		if (retryGen !== state._tcpRetryGeneration) return;
		scheduleTcpRetryAfterClose(port, tryConnect);
	});
}
