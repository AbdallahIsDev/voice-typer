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

import { app, dialog } from "electron";
import {
	HEARTBEAT_INTERVAL_MS,
	IPC_TOKEN,
	TCP_FRAME_MAX_BYTES,
} from "../constants";
import { PythonChannels } from "../ipc/channels";
import { log } from "../logging";
import { state } from "../state";
import { createWindows } from "../windows";
import { broadcastToMainWindow } from "../windows/main-window";
import { handleMessage } from "./handle-message";
import {
	_flushPendingOutbound,
	_resetPendingOutbound,
	sendToPython,
} from "./send-to-python";

//startup timeout. If Python doesn't connect within 60s
// of the first tryConnect(), show a clear error dialog and quit.
// This covers the case where Python spawns successfully but hangs
// during torch import without exiting — the retry loop would otherwise
// run forever with no window and no error. The timer is cleared on
// successful connect. The callback also safety-checks state to avoid
// firing after stopPython / during quit (stop-python.ts is responsible
// for clearing the retry timer; this startup timer is independent and
// guarded by the checks below).
const TCP_STARTUP_TIMEOUT_MS = 60_000;
let _tcpStartupTimeoutTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * : clear the TCP startup timeout timer. Exported so
 * `stopPython()` / `relaunchApp()` / `startPython()` can clear the
 * 60s window: `tcpConnect()`'s `if (_tcpStartupTimeoutTimer === null)`
 * guard then lets the next `tcpConnect()` arm a fresh timer.
 *
 * Without this export, the timer set in `tcpConnect()` continues
 * counting even after Python is already gone (stopPython path) or
 * a dev-mode restart is in flight (relaunchApp path). If it fires
 * while `state.pythonProcess` is non-null but the proc is exiting
 * via `quit_app`, the safety check inside the callback short-circuits
 * — BUT the timer still pins the event loop alive for up to 60s after
 * the app should have exited.
 *
 * The `.unref()` part of the  plan is INTENTIONALLY SKIPPED:
 * `tests/__tests__/main-process-fixes.test.ts` asserts
 * `_tcpStartupTimeoutTimer is NOT unref'd` ( rationale: the
 * timer must keep Electron alive so the "Python backend failed to
 * start" dialog actually renders before exit). The explicit
 * `clearTcpStartupTimeout()` calls from every teardown / restart
 * path are the belt-and-suspenders guarantee that the timer doesn't
 * leak past shutdown.
 */
export function clearTcpStartupTimeout(): void {
	if (_tcpStartupTimeoutTimer !== null) {
		clearTimeout(_tcpStartupTimeoutTimer);
		_tcpStartupTimeoutTimer = null;
	}
}

export function tcpConnect(port: number): void {
	// Start the startup timeout on the first connect attempt. If
	// tcpConnect is called again (e.g. after a dev-mode restart),
	// the timer is already cleared from the prior successful
	// connect, so a fresh timer starts.
	if (_tcpStartupTimeoutTimer === null) {
		_tcpStartupTimeoutTimer = setTimeout(() => {
			_tcpStartupTimeoutTimer = null;
			// Safety checks: if Python already connected,
			// the app is quitting, or there's no Python
			// process, skip the error dialog.
			if (
				state.tcpSocket !== null ||
				app.isQuitting ||
				state.pythonProcess === null
			) {
				return;
			}
			log.error(
				`[TCP] Python backend failed to start within ${
					TCP_STARTUP_TIMEOUT_MS / 1000
				}s`,
			);
			try {
				dialog.showErrorBox(
					"Python backend failed to start",
					`Voice Typer could not connect to its Python backend within ${
						TCP_STARTUP_TIMEOUT_MS / 1000
					} seconds.\n\nPlease check the logs and try again.`,
				);
			} catch (e) {
				// dialog may not be available in headless mode
				// (CI, `DISPLAY` unset, or pre-app-ready). Log at debug so
				// the failure is observable in the diagnostic log without
				// spamming the default level — mirrors the debug-log pattern
				// used in `relaunch-app.ts:351`.
				log.debug(
					"[TCP] startup-timeout dialog.showErrorBox failed (non-fatal):",
					e,
				);
			}
			app.quit();
		}, TCP_STARTUP_TIMEOUT_MS);
	}

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
			_flushPendingOutbound();
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
			// SEC-023: cap tcpBuffer at 4 MB to prevent unbounded memory
			// growth from malformed frames (e.g. a chunk with no newline
			// that never gets split). Drop the connection on overflow.
			state.tcpBuffer = state.tcpBuffer
				? Buffer.concat([state.tcpBuffer as Buffer, chunk])
				: chunk;
			if (state.tcpBuffer.length > TCP_FRAME_MAX_BYTES) {
				const capMiB = TCP_FRAME_MAX_BYTES / (1024 * 1024);
				log.error(
					`[TCP] tcpBuffer exceeded ${capMiB} MiB without a newline - dropping connection (possible malformed frame or oversized Python reply)`,
				);
				// surface a structured "reply too large"
				// error to the renderer BEFORE destroying the socket.
				// Without this, the close handler would reject
				// pending requests with the generic "Python socket
				// closed" message — the renderer would log a
				// confusing socket-closed error and the user would
				// never learn the real cause (a too-large Python
				// reply, e.g. get_history / export_diagnostics on a
				// power-user dataset). Pre-rejecting here means the
				// close handler's `state.pendingRequests` loop finds
				// an empty map (we delete each entry as we reject it)
				// and skips its own rejection.
				const overflowErr = new Error(
					`Python reply exceeded ${capMiB} MiB limit (possible malformed frame or oversized reply)`,
				);
				for (const [id, entry] of state.pendingRequests) {
					state.pendingRequests.delete(id);
					entry.reject(overflowErr);
				}
				state.tcpBuffer = Buffer.alloc(0);
				client.destroy();
				return;
			}
			let newlineIdx: number;
			// biome-ignore lint/suspicious/noAssignInExpressions: classic buffer-scan idiom — assign + test in one expression
			while ((newlineIdx = state.tcpBuffer.indexOf(0x0a)) !== -1) {
				const lineBuf = state.tcpBuffer.subarray(0, newlineIdx);
				state.tcpBuffer = state.tcpBuffer.subarray(newlineIdx + 1);
				const line = lineBuf.toString("utf8");
				if (!line.trim()) continue;
				try {
					//JSON.parse returns
					// `any`; cast to `unknown` and
					// narrow before passing to
					// handleMessage. A non-object
					// payload (array, primitive)
					// would otherwise satisfy the
					// Record<string, unknown> type
					// but break runtime access to
					// .type / .id / .data.
					const msg = JSON.parse(line) as unknown;
					if (typeof msg !== "object" || msg === null) {
						log.warn("[TCP] non-object frame from Python, skipping");
						continue;
					}
					handleMessage(msg as Record<string, unknown>);
				} catch {
					//never log the raw
					// TCP line — invalid-JSON lines
					// may contain transcription_final
					// events with user speech (PII).
					// Log only the length and, when
					// VOICE_TYPER_DEBUG is explicitly
					// enabled, a redacted preview
					// (first 80 chars with control
					// chars stripped) so a developer
					// can still triage framing bugs.
					log.error(
						"[TCP] invalid JSON from Python, skipping line (len=%d)",
						line.length,
					);
					if (process.env.VOICE_TYPER_DEBUG === "1") {
						// biome-ignore lint/suspicious/noControlCharactersInRegex: intentional — strip control chars for safe console preview
						const preview = line.slice(0, 80).replace(/[\x00-\x1f\x7f]/g, "?");
						log.error("[TCP] invalid JSON preview: %s", preview);
					}
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
			//loop were left UNCONDITIONAL in the scoping fix. A stale socket
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
					log.warn(`[TCP] waiting for Python backend (127.0.0.1:${port})...`);
				} else {
					log.warn(
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
				// store the retry timer on shared state so
				// `stopPython()` and `relaunchApp()` can clearTimeout
				// it before bumping `_tcpRetryGeneration`. Previously
				// the timer was a fire-and-forget local — even after
				// `state._tcpRetryGeneration++` invalidated the
				// generation check at the top of `tryConnect()`, the
				// pending timer still fired `tryConnect()` once more
				// (creating a fresh socket that immediately hit the
				// generation mismatch and bailed). Clearing the timer
				// explicitly in `stopPython()` / `relaunchApp()`
				// short-circuits this.
				if (state._tcpRetryTimer) {
					clearTimeout(state._tcpRetryTimer);
				}
				state._tcpRetryTimer = setTimeout(() => {
					state._tcpRetryTimer = null;
					tryConnect();
				}, delay);
			}
		});
	}

	tryConnect();
}
