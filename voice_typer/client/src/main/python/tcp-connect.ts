/**
 * TCP client: connect to the Python backend's IPC server with retry.
 *
 * Extracted from `index.ts` (REF-2). Owns:
 *   - `tcpConnect(port)` — top-level entry; starts the first `tryConnect()`.
 *   - `tryConnect()` (nested) — creates a `net.Socket`, performs the
 *     SEC-018 auth handshake, wires `data` / `error` / `close` handlers,
 *     and schedules exponential-backoff retries on close.
 *
 * The handler concerns live in leaves under `tcp/`:
 *   - `startup-watchdog.ts` — the 60s startup timeout window.
 *   - `frame-reader.ts`    — newline-framed JSON dispatch (`data` handler).
 *   - `close-handler.ts`   — ownership-scoped teardown on socket close.
 *   - `retry-scheduler.ts` — generation-checked exponential backoff.
 */
import net from "node:net";

import { HEARTBEAT_INTERVAL_MS, IPC_TOKEN } from "../constants";
import { PythonChannels } from "../ipc/channels";
import { log } from "../logging";
import { state } from "../state";
import { createWindows } from "../windows";
import { broadcastToMainWindow } from "../windows/main-window";
import { flushPendingOutbound, sendToPython } from "./send-to-python";
import { installTcpCloseHandler } from "./tcp/close-handler";
import { handleTcpData } from "./tcp/frame-reader";
// Re-exported below as a declared function so source-text pins that
// match `export function clearTcpStartupTimeout(` keep resolving
// against this module (stop-python / start-python / relaunch-app /
// restart-backend import it from here).
import {
	clearTcpStartupTimeout as _clearTcpStartupTimeoutImpl,
	armTcpStartupTimeout,
} from "./tcp/startup-watchdog";

export function clearTcpStartupTimeout(): void {
	_clearTcpStartupTimeoutImpl();
}

export function tcpConnect(port: number): void {
	armTcpStartupTimeout();

	function tryConnect(): void {
		const client = new net.Socket();
		//disable Nagle's algorithm so small push events
		// (bubble_level at 15-50 Hz, heartbeat_ack) are not
		// coalesced into larger segments — eliminates up to 40ms
		// of per-write latency on the waveform-bubble hot path.
		// The matching server-side setsockopt(TCP_NODELAY) lives
		// in transport_tcp.py:_handle_tcp_connection.
		client.setNoDelay(true);
		// CRITICAL: do NOT set `tcpSocket = client` here.  Setting it
		// before the socket is connected and authed means sendToPython()
		// would write to the unconnected socket; Node.js buffers those
		// bytes and flushes them on connect BEFORE the auth line written
		// in the connect callback.  The Python server then reads the
		// command as the auth line, fails auth, and drops the connection.
		// Instead, we set `tcpSocket = client` only after the auth line
		// has been written (in the connect callback below).  Until then,
		// sendToPython() sees tcpSocket === null and rejects with
		// "Python backend is not connected" — a clear, immediate error
		// instead of a mysterious 5-second timeout.
		state._tcpAuthed = false;

		// Capture the current retry generation at socket creation time.
		// If startPython() fires while we're retrying, the generation
		// increments and our close/error handlers will know to stop.
		const retryGen = state._tcpRetryGeneration;

		client.connect(port, "127.0.0.1", () => {
			//if startPython() bumped the retry generation while our
			// client.connect() handshake was in flight, destroy this stale
			// socket and bail.  Without this guard, the stale socket would
			// be installed as state.tcpSocket, write the auth line, call
			// createWindows(), and start a heartbeat — all racing the fresh
			// tcpConnect() that startPython() just issued.  The two sockets
			// would then fight for the Python backend's single TCP accept
			// slot.  The error/close handlers already check the generation
			// (lines below); the connect callback is the last gap.
			if (retryGen !== state._tcpRetryGeneration) {
				client.destroy();
				return;
			}
			//clear the startup timeout — Python
			// connected successfully, no need to fire the
			// 60s error dialog.
			clearTcpStartupTimeout();
			state._tcpRetryCount = 0;
			// SEC-018: send the auth message as the first line.  The Python
			// IPC server reads this before processing any other commands.
			// If the token doesn't match, the server drops the connection.
			client.write(`${JSON.stringify({ type: "auth", token: IPC_TOKEN })}\n`);
			// Auth line has been written — it's now safe to expose the
			// socket to sendToPython().  Any subsequent writes will be
			// appended after the auth line in the send buffer, which is
			// the correct order (Python reads auth first, then commands).
			state.tcpSocket = client;
			state._tcpAuthed = true;
			// Python is running and its TCP server accepted us.
			// Create the main window immediately.
			log.warn(`[TCP] connected to Python backend (127.0.0.1:${port})`);
			if (state._restartTriggered) {
				state._restartTriggered = false;
				log.warn(
					"[RESTART] New Python backend connected -- restart cycle complete",
				);
			}
			createWindows();
			// On every connect AFTER the first one, notify the renderer
			// that the TCP channel is back up.  This handles transient
			// disconnects (sleep/resume, network blips) so the renderer's
			// connectionStatus doesn't get stuck on "disconnected".
			// (The full-restart flow no longer needs this — the renderer
			// is reloaded fresh — but it's still useful for transient
			// TCP drops that don't warrant a full process restart.)
			if (state._hadConnectedBefore) {
				//route through broadcastToMainWindow.
				broadcastToMainWindow(PythonChannels.event, {
					type: "reconnected",
					_session_nonce: state.sessionNonce,
				});
			}
			state._hadConnectedBefore = true;
			// Flush any idempotent commands that were queued while
			// the socket was null (transient-disconnect replay queue
			// — see ``send-to-python.ts``'s ``_pendingOutbound``).
			// The queue is drained in FIFO order; each entry is
			// re-sent via ``sendToPython`` (which now has a non-null
			// ``state.tcpSocket``) and the new promise's resolution
			// is forwarded to the original caller's ``resolve`` /
			// ``reject``.
			//
			// Safe to call when the queue is empty (no-op). Errors
			// from a re-sent entry (e.g. allowlist drift, rate limit,
			// MAX_PENDING_REQUESTS cap) surface to the original
			// caller's ``reject`` — the flush loop does not swallow
			// them.
			flushPendingOutbound();
			//start the heartbeat interval now that the
			// backend is connected.  Send an immediate heartbeat
			// so the backend's watchdog arms quickly (otherwise
			// the first 5s tick would be the only thing arming
			// it, and a fast Electron crash in the first 5s
			// would be undetected until 15s later).
			if (state.heartbeatInterval) clearInterval(state.heartbeatInterval);
			sendToPython({ type: "heartbeat" }).catch(() => {
				/* best-effort — will retry on next tick */
			});
			//unref the heartbeat interval so it doesn't keep the Node.js
			// event loop alive on its own. Without .unref(), a hidden background
			// instance would never exit when the user closes all windows.
			const h = setInterval(() => {
				sendToPython({ type: "heartbeat" }).catch(() => {
					/* best-effort — close handler will clear the interval */
				});
			}, HEARTBEAT_INTERVAL_MS);
			h.unref();
			state.heartbeatInterval = h;
		});

		client.on("data", (chunk: Buffer) => {
			handleTcpData(client, chunk);
		});

		client.on("error", (err: NodeJS.ErrnoException) => {
			// If a newer retry generation is active, stop retrying.
			// This prevents stale TCP loops from multiplying after
			// startPython() re-spawns the backend.
			if (retryGen !== state._tcpRetryGeneration) return;
			// If a full app relaunch is in flight, Python's
			// sys.exit(0) closes the TCP socket from its end.
			// Node.js surfaces this either as an ECONNRESET error or
			// as a 'close' event.  Neither is a real error — the
			// process is about to exit.  Suppress the noisy log.
			if (state._relaunching) {
				client.destroy();
				return;
			}
			if (err.code === "ECONNRESET") {
				log.warn("[TCP] connection reset by Python backend");
			} else if (err.code !== "ECONNREFUSED") {
				log.error("[TCP] error:", err);
			}
			// Only destroy the socket here - do NOT schedule retries!!
			// The close handler (below) is the sole retry scheduler.
			// If BOTH this error handler AND the close handler scheduled
			// retries, each error would create 2 retries, each creating
			// 2 more, leading to exponential retry multiplication.
			client.destroy();
		});

		installTcpCloseHandler({ client, port, retryGen, tryConnect });
	}

	tryConnect();
}
