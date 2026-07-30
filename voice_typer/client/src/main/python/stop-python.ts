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
 * The flags reset automatically when this module is re-evaluated
 * (e.g. by `vi.resetModules()` in tests, or by Electron's renderer
 * reload in dev mode), so a freshly-spawned backend can be stopped
 * again after a relaunch. `state._stopPythonCalled` is mirrored
 * alongside the flags for cross-module observability; resetting it on
 * `startPython()` is handled by `_resetStopPythonFlagsForRestart()` (GT-A3-10,
 * exported below) which `start-python.ts` should call at the top of
 * `startPython()`.
 *
 * GT-71: the armed `killTimer` is NOT `.unref()`'d. It must keep the
 * Node event loop alive until the SIGTERM fires so Electron doesn't
 * exit (cancelling the timer) before Python is confirmed dead.
 */
import { state } from "../state";
import { _resetIpcBackpressure, sendToPython } from "./send-to-python";
// ER-29: clear the TCP startup timeout so the 60s timer doesn't fire
// during teardown (after Python is already dead) and trip the
// premature "Python backend failed to start" dialog + `app.quit()`.
import { clearTcpStartupTimeout } from "./tcp-connect";

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
//
// GT-A3-10: `_resetStopPythonFlagsForRestart()` is exported below.
let isStopping = false;
let isStopped = false;

// XV-157 (XZ-14): track the armed killTimer so we can clear any
// previously-armed timer before setting a new one. The top-of-function
// `isStopping` guard already prevents a second call from reaching the
// `setTimeout` line, but this clear-before-set is defense-in-depth for
// the case where the guard is bypassed (e.g. by a future code path
// that resets `isStopping`/`isStopped` mid-cycle).
let armedKillTimer: ReturnType<typeof setTimeout> | null = null;

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

	// TY-35: clear the per-renderer rate-limit Map. Previously this Map
	// was never cleared in production — each destroyed BrowserWindow
	// leaked its `webContents.id` entry forever (the docstring on
	// `_resetIpcBackpressure` claimed it was called from here, but the
	// call site was missing). Clearing on stop_python bounds the Map's
	// growth to "at most one entry per currently-live renderer at any
	// moment between stop_python calls". Idempotent + safe to call
	// before the early-return paths below.
	_resetIpcBackpressure();

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
	// No live process to kill — shutdown is "complete" immediately.
	// Flip the flags so any subsequent call (e.g. will-quit firing
	// after before-quit) is a no-op.
	if (!state.pythonProcess) {
		isStopping = false;
		isStopped = true;
		return;
	}
	// ER-29: clear the TCP startup timeout timer so the 60s deadline
	// doesn't fire AFTER Python is already gone (or never came up).
	// Placed AFTER the `!state.pythonProcess` early-return so the
	// "no proc" path stays a true no-op (the startup timer was
	// never armed in that case — `tcpConnect()` only arms it when
	// a connect attempt is in flight, which requires a proc to
	// have been spawned). Without this clear, the timer set in
	// `tcpConnect()` continues counting; if it fires while
	// `state.pythonProcess` is non-null but the proc is exiting
	// via the `quit_app` we just sent, the safety check inside
	// the callback short-circuits — BUT the timer still pins the
	// event loop alive for up to 60s after the app should have
	// exited. (The ER-29 plan also called for `.unref()`-ing the
	// timer in tcp-connect.ts, but that part is intentionally
	// skipped — see tcp-connect.ts:33-61 for the full rationale
	// and the xv-fa19-fixes.test.ts guard. The explicit clear
	// here is the belt-and-suspenders guarantee.)
	clearTcpStartupTimeout();
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
			const proc = state.pythonProcess;
			// XE-15-5: escalate SIGTERM → SIGKILL after a
			// short grace. ``proc.kill()`` with no argument
			// sends SIGTERM, which is BLOCKED when Python is
			// stuck in a C extension (torch model load,
			// sounddevice buffer hold) — the signal is
			// queued but never delivered until the GIL is
			// released. SIGKILL is unblockable at the kernel
			// level. Pre-fix, stop-python.ts sent SIGTERM
			// and immediately nulled ``state.pythonProcess``
			// + flipped ``isStopped`` — Electron proceeded
			// to exit thinking Python was dead, but Python
			// was actually orphaned, still holding the
			// VoiceTyperSingleInstance mutex. The next app
			// launch failed to acquire the mutex and showed
			// a misleading "Only one instance can run"
			// dialog. Mirrors the escalation pattern in
			// ``relaunch-app.ts::_killPythonProcessWithSigkillFallback``.
			try {
				if (!proc.killed) {
					proc.kill("SIGTERM");
				}
				// Give SIGTERM 1.5s to take effect
				// (Python got 3s of quit_app grace
				// already). If still alive, SIGKILL.
				const escalateTimer = setTimeout(() => {
					if (!proc.killed) {
						try {
							proc.kill("SIGKILL");
						} catch {
							/* best-effort */
						}
					}
				}, 1500);
				proc.once("exit", () => clearTimeout(escalateTimer));
			} catch {
				/* best-effort — proc may have already exited */
			}
			state.pythonProcess = null;
		}
		armedKillTimer = null;
		isStopping = false;
		isStopped = true;
	}, 3000);
	// GT-71: do NOT `.unref()` the killTimer — it must keep Electron
	// alive until Python is confirmed dead.
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

/**
 * GT-A3-10: reset the idempotency flags so a freshly-spawned backend
 * can be stopped again. `startPython()` should call this BEFORE
 * assigning `state.pythonProcess`.
 * @internal
 */
export function _resetStopPythonFlagsForRestart(): void {
	isStopping = false;
	isStopped = false;
	if (armedKillTimer) {
		clearTimeout(armedKillTimer);
		armedKillTimer = null;
	}
	state._stopPythonCalled = false;
}
