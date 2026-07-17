/**
 * TCP client: connect to the Python backend's IPC server with retry.
 *
 * Extracted from `index.ts` (REF-2). Owns:
 *   - `tcpConnect(port)` — top-level entry; starts the first `tryConnect()`.
 *   - `tryConnect()` (nested) — creates a `net.Socket`, performs the
 *     SEC-018 auth handshake, wires `data` / `error` / `close` handlers,
 *     and schedules exponential-backoff retries on close.
 */
import net from "node:net";
import { HEARTBEAT_INTERVAL_MS, IPC_PORT, IPC_TOKEN } from "../constants";
import { state } from "../state";
import { createWindows } from "../windows";
import { handleMessage } from "./handle-message";
import { sendToPython } from "./send-to-python";

export function tcpConnect(port: number) {
	function tryConnect() {
		const client = new net.Socket();
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
			console.warn(`[TCP] connected to Python backend (127.0.0.1:${port})`);
			if (state._restartTriggered) {
				state._restartTriggered = false;
				console.warn(
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
			if (
				state._hadConnectedBefore &&
				state.mainWindow &&
				!state.mainWindow.isDestroyed()
			) {
				state.mainWindow.webContents.send("python-event", {
					type: "reconnected",
					_session_nonce: state.sessionNonce,
				});
			}
			state._hadConnectedBefore = true;
			// RW-10: start the heartbeat interval now that the
			// backend is connected.  Send an immediate heartbeat
			// so the backend's watchdog arms quickly (otherwise
			// the first 5s tick would be the only thing arming
			// it, and a fast Electron crash in the first 5s
			// would be undetected until 15s later).
			if (state.heartbeatInterval) clearInterval(state.heartbeatInterval);
			sendToPython({ type: "heartbeat" }).catch(() => {
				/* best-effort — will retry on next tick */
			});
			state.heartbeatInterval = setInterval(() => {
				sendToPython({ type: "heartbeat" }).catch(() => {
					/* best-effort — close handler will clear the interval */
				});
			}, HEARTBEAT_INTERVAL_MS);
		});

		client.on("data", (chunk: Buffer) => {
			// SEC-023: cap tcpBuffer at 4 MB to prevent unbounded memory
			// growth from malformed frames (e.g. a chunk with no newline
			// that never gets split). Drop the connection on overflow.
			state.tcpBuffer += chunk.toString();
			if (state.tcpBuffer.length > 4 * 1024 * 1024) {
				console.error(
					"[TCP] tcpBuffer exceeded 4 MB without a newline - dropping connection (possible malformed frame)",
				);
				state.tcpBuffer = "";
				client.destroy();
				return;
			}
			const lines = state.tcpBuffer.split("\n");
			state.tcpBuffer = lines.pop() ?? "";
			for (const line of lines) {
				if (!line.trim()) continue;
				try {
					const msg = JSON.parse(line);
					handleMessage(msg);
				} catch {
					console.error("Invalid JSON from Python:", line);
				}
			}
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
				console.warn("[TCP] connection reset by Python backend");
			} else if (err.code !== "ECONNREFUSED") {
				console.error("[TCP] error:", err);
			}
			// Only destroy the socket here - do NOT schedule retries!!
			// The close handler (below) is the sole retry scheduler.
			// If BOTH this error handler AND the close handler scheduled
			// retries, each error would create 2 retries, each creating
			// 2 more, leading to exponential retry multiplication.
			client.destroy();
		});

		client.on("close", () => {
			if (state.tcpSocket === client) {
				state.tcpSocket = null;
				state._tcpAuthed = false;
			}
			// RW-10: stop the heartbeat interval — the socket is
			// dead, so further sendToPython() calls would just
			// queue up rejected promises.  A fresh interval is
			// started in the connect callback when the next
			// retry succeeds.
			if (state.heartbeatInterval) {
				clearInterval(state.heartbeatInterval);
				state.heartbeatInterval = null;
			}
			// SEC-022: reject all outstanding pendingRequests so the UI
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
			// If a newer retry generation is active, stop retrying.
			if (retryGen !== state._tcpRetryGeneration) return;
			// If a full app relaunch is in flight, the process is
			// about to exit — no point scheduling retries.
			if (state._relaunching) {
				return;
			}
			// Only retry while a Python process is alive.
			// Do NOT check pythonReady here - that's false until the first
			// successful connection.  The error handler no longer schedules
			// retries (to prevent exponential multiplication), so the close
			// handler must cover the initial startup case too.
			if (state.pythonProcess !== null) {
				state._tcpRetryCount++;
				if (state._tcpRetryCount === 1) {
					console.warn(
						`[TCP] waiting for Python backend (127.0.0.1:${port})...`,
					);
				} else {
					console.warn(
						"[TCP] Python backend not ready yet (127.0.0.1:" +
							port +
							") -- retrying (attempt " +
							state._tcpRetryCount +
							")",
					);
				}
				// Exponential backoff capped at 2s: the first retry
				// happens quickly (250ms) so a fast Python startup
				// doesn't wait a full second, but subsequent retries
				// back off to avoid hammering the port during a slow
				// torch import.  This shaves 2-4 seconds off the
				// typical cold-start reconnection window.
				const delay = Math.min(250 * 2 ** (state._tcpRetryCount - 1), 2000);
				setTimeout(tryConnect, delay);
			}
		});
	}

	tryConnect();
}
