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
 *
 * XV-157 (XZ-14): idempotency guard. `stopPython()` is invoked from up
 * to 4 distinct sites during a single circuit-breaker trip:
 *   1. `bootstrap.ts::onUncaught` / `onRejection` inline defensive call,
 *   2. `bootstrap.ts::_productionExit` (called by `exit(1)` from #1),
 *   3. `index.ts::before-quit` handler (fired by `app.quit()` from #2),
 *   4. `index.ts::will-quit` belt-and-suspenders handler.
 * Without the guard below, each call would send a fresh `quit_app`
 * write over the dying TCP socket AND arm a fresh `killTimer` +
 * `.once('exit')` listener on `pythonProcess`. P1-2c's `.once`
 * mitigation prevented listener accumulation across calls, but the
 * duplicate `quit_app` writes still raced on the half-closed socket
 * and the duplicate `killTimer`s leaked handles into the event loop
 * (each holding a strong ref to `state.pythonProcess` for 3s). The
 * module-level `isStopping` / `isStopped` flags ensure only the FIRST
 * call performs any work; subsequent calls return immediately.
 *
 * Flags are reset by `_resetStopPythonFlagsForRestart()`, called from
 * `startPython()` after the retry-generation bump. Tests can call it
 * directly instead of relying on `vi.resetModules()`.
 * `state._stopPythonCalled` is mirrored alongside the flags for
 * cross-module observability and is also reset by
 * `_resetStopPythonFlagsForRestart()`.
 *
 * XV-156: the armed `killTimer` is `.unref()`'d so it doesn't keep the
 * Node event loop alive if the process is otherwise ready to exit
 * (e.g. Python exits cleanly before the 3s grace period elapses).
 */
import { state } from "../state";
import { clearTcpStartupTimeout } from "./tcp-connect";
import { sendToPython } from "./send-to-python";

// XV-157 (XZ-14): idempotency state. `isStopping` is true while a stop
// is in flight (between the top-of-function guard and either the
// killTimer callback or the `.once('exit')` callback). `isStopped`
// becomes true once shutdown is complete (Python exited or was
// force-killed). Together they prevent the breaker-trip cascade from
// sending duplicate `quit_app` writes / arming duplicate killTimers.
//
// Module-scoped (not on `state`) so a `vi.resetModules()` re-import
// resets them — this is what makes dev-mode `startPython()` re-spawn
// re-stoppable without requiring `start-python.ts` to know about the
// flags.
let isStopping = false;
let isStopped = false;

// XV-157 (XZ-14): track the armed killTimer so we can clear any
// previously-armed timer before setting a new one. The top-of-function
// `isStopping` guard already prevents a second call from reaching the
// `setTimeout` line, but this clear-before-set is defense-in-depth for
// the case where the guard is bypassed (e.g. by a future code path
// that resets `isStopping`/`isStopped` mid-cycle).
let armedKillTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Reset the idempotency flags so a freshly-spawned backend can be
 * stopped again after a relaunch. Called from `startPython()` (after the
 * retry-generation bump) so each new backend lifecycle starts with a
 * clean stop state. Tests can call this directly instead of relying on
 * `vi.resetModules()` to re-evaluate the module.
 *
 * Also clears any armed `killTimer` left over from a prior stop cycle
 * (defense-in-depth — the timer should already have been cleared by the
 * `killTimer` callback or the `.once('exit')` listener, but if neither
 * fired e.g. because the prior backend was adopted via `VT_PYTHON_PORT`,
 * the handle would otherwise leak).
 *
 * ER-29: also clears the TCP startup timeout timer so a fresh 60s
 * window begins with the new backend lifecycle.
 */
export function _resetStopPythonFlagsForRestart(): void {
	if (armedKillTimer) {
		clearTimeout(armedKillTimer);
		armedKillTimer = null;
	}
	isStopping = false;
	isStopped = false;
	state._stopPythonCalled = false;
	clearTcpStartupTimeout();
}

export function stopPython() {
	// XV-157 (XZ-14): idempotency guard. If a stop is already in
	// flight (`isStopping`) OR has already completed (`isStopped`),
	// return immediately. This prevents the breaker-trip cascade
	// (bootstrap onUncaught → _productionExit → index before-quit →
	// index will-quit) from sending duplicate `quit_app` writes
	// and arming multiple killTimers.
	if (isStopping || isStopped) return;
	isStopping = true;
	// Mirror on `state` for cross-module observability (the
	// module-level flags above remain the source of truth for the
	// idempotency decision).
	state._stopPythonCalled = true;

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
	// R6-F6: clear any pending TCP retry timer so a stale
	// `tryConnect()` invocation doesn't fire after we've begun
	// shutdown. `stopPython()` is called from `before-quit`,
	// `will-quit` (R6-F7), and the uncaughtException handler
	// (R6-F7), so this guards all three shutdown paths.
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	// ER-29: clear the TCP startup timeout so the 60s startup
	// dialog doesn't fire while we're shutting down.
	clearTcpStartupTimeout();
	// No live process to kill — shutdown is "complete" immediately.
	// Flip the flags so any subsequent call (e.g. will-quit firing
	// after before-quit) is a no-op.
	if (!state.pythonProcess) {
		isStopping = false;
		isStopped = true;
		return;
	}
	// XV-157 (XZ-14): the top-of-function `isStopping` guard above
	// is what guarantees `quit_app` is sent at most once per stop
	// cycle — a second call never reaches this line. The
	// `.catch(() => {})` swallows the rejection that fires when
	// the socket is already half-closed (which is exactly the case
	// during the breaker-trip cascade).
	sendToPython({ type: "quit_app" }).catch(() => {});

	// XV-157 (XZ-14): guard against duplicate killTimer creation.
	// Clear any previously-armed timer before setting a new one.
	// (Defense-in-depth — the `isStopping` guard at the top is the
	// primary protection; this backstop covers a future code path
	// that resets the flags mid-cycle.)
	if (armedKillTimer) {
		clearTimeout(armedKillTimer);
		armedKillTimer = null;
	}
	const killTimer = setTimeout(() => {
		if (state.pythonProcess) {
			// DE-84: use SIGKILL (not the default SIGTERM) to
			// match the file-header docstring's "force-kill"
			// promise and the parallel pattern in
			// `relaunch-app.ts`. A Python backend stuck in a C
			// extension (torch/sounddevice import) will ignore
			// SIGTERM exactly as it ignored `quit_app`; SIGKILL is
			// uncatchable and guarantees the process exits,
			// releasing the single-instance mutex.
			state.pythonProcess.kill("SIGKILL");
			state.pythonProcess = null;
		}
		armedKillTimer = null;
		// XV-157 (XZ-14): shutdown complete — flip the flags so
		// any subsequent stopPython() call (e.g. will-quit
		// firing after the killTimer) is a no-op.
		isStopping = false;
		isStopped = true;
	}, 3000);
	// XV-156: unref the killTimer so it doesn't keep the event
	// loop alive if the process is otherwise ready to exit (e.g.
	// Python exits cleanly before the 3s grace period elapses, or
	// the breaker-trip `app.quit()` succeeds before the timer
	// fires).
	killTimer.unref();
	armedKillTimer = killTimer;
	// P1-2c (Round 0 forward-port): use `.once` so the listener is
	// auto-removed after firing. `stopPython()` may be called more than
	// once for the same live `pythonProcess` (e.g. shutdown sequence +
	// before-quit handler), and `.on` would accumulate a fresh listener
	// per call — eventually tripping Node's default maxListeners=10
	// warning. `.once` ensures each registered listener fires at most
	// one time and is then cleaned up.
	state.pythonProcess.once("exit", () => {
		clearTimeout(killTimer);
		armedKillTimer = null;
		// GT-60: null `state.pythonProcess` so downstream callers
		// (notably `index.ts::will-quit`) see that Python is already
		// gone — previously this only flipped `isStopped`, causing
		// will-quit to register a stale `.once("exit")` listener.
		state.pythonProcess = null;
		// XV-157 (XZ-14): Python exited gracefully — shutdown
		// is complete. Flip the flags so subsequent stopPython()
		// calls are no-ops.
		isStopping = false;
		isStopped = true;
	});
}
