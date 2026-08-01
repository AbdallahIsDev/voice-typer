/**
 * Stop the Python backend: send `quit_app` over TCP, then force-kill
 * after a 3s grace period.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * : stops the heartbeat interval first so we don't queue a
 * heartbeat onto the dying socket while `quit_app` is in flight.
 * The interval is also cleared in the TCP close handler, but stopping
 * it here avoids the race where the 5s tick fires between
 * sendToPython("quit_app") and the socket actually closing.
 *
 *  (): idempotency guard. `stopPython()` is invoked from up
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
 * `startPython()` is handled by `_resetStopPythonFlagsForRestart()` (,
 * exported below) which `start-python.ts` should call at the top of
 * `startPython()`.
 *
 * : the armed `killTimer` is NOT `.unref()`'d. It must keep the
 * Node event loop alive until the SIGTERM fires so Electron doesn't
 * exit (cancelling the timer) before Python is confirmed dead.
 *
 * SIGTERM→SIGKILL escalation contract:
 *  - ``killTimer`` fires at 3s. On POSIX it sends ``SIGTERM`` (graceful —
 *    Python's signal handlers flush history_db, close audio streams,
 *    release the single-instance mutex). On Windows it sends
 *    ``taskkill /T /PID`` (no ``/F``) — the closest equivalent to
 *    "polite termination" for a console process tree. ``proc.kill()``
 *    on Windows is ``TerminateProcess`` on the IMMEDIATE process only,
 *    which would orphan the native hotkey binary child spawned by the
 *    Python sidecar; ``/T`` walks the toolhelp snapshot and reaps the
 *    whole tree. (The killTimer delay was kept at 3s — not extended
 *    to the 5-10s range the finding suggested — because
 *    ``xv-fa19-fixes.test.ts`` pins the 3s contract via
 *    ``vi.advanceTimersByTime(3000)``; extending it would require
 *    updating that test file, which is outside this task's owned
 *    files. The total grace — 3s killTimer + 3s escalate = 6s — is
 *    within the finding's spirit ("matches the sidecar's typical
 *    cleanup time").)
 *  - ``escalateTimer`` fires 3s later (6s after ``quit_app``). On POSIX
 *    it sends ``SIGKILL`` (unblockable — used when Python is stuck in a
 *    C extension holding the GIL, e.g. torch model load / sounddevice
 *    buffer hold). On Windows it sends ``taskkill /F /T /PID`` (force
 *    kill the tree). The escalation is the unblockable fallback for
 *    the case where the graceful signal was queued but never delivered.
 *    The previous 1.5s escalation was too tight for the sidecar's
 *    typical cleanup time (history_db flush + audio stream close +
 *    single-instance mutex release can take 2-3s on a cold disk); 3s
 *    matches the contract used by the shared
 *    ``killPythonProcessWithSigkillFallback`` helper in ``kill-python.ts``.
 */
import { spawnSync } from "node:child_process";
import { state } from "../state";
import { _resetIpcBackpressure, sendToPython } from "./send-to-python";
//clear the TCP startup timeout so the 60s timer doesn't fire
// during teardown (after Python is already dead) and trip the
// premature "Python backend failed to start" dialog + `app.quit()`.
import { clearTcpStartupTimeout } from "./tcp-connect";

//(): idempotency state. `isStopping` is true while a stop
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
//`_resetStopPythonFlagsForRestart()` is exported below.
let isStopping = false;
let isStopped = false;

//(): track the armed killTimer so we can clear any
// previously-armed timer before setting a new one. The top-of-function
// `isStopping` guard already prevents a second call from reaching the
// `setTimeout` line, but this clear-before-set is defense-in-depth for
// the case where the guard is bypassed (e.g. by a future code path
// that resets `isStopping`/`isStopped` mid-cycle).
let armedKillTimer: ReturnType<typeof setTimeout> | null = null;

// Constants for the killTimer + escalateTimer delays. Exported so the
// test in ``shutdown-hooks.test.ts`` can pin the contract (the test
// asserts that the SIGKILL escalation fires at
// ``KILL_TIMER_MS + ESCALATE_TIMER_MS`` after ``stopPython()``).
//
// ``KILL_TIMER_MS`` is the grace period after ``quit_app`` is sent
// before SIGTERM / ``taskkill /T`` is sent. Kept at 3s for backward
// compat with ``xv-fa19-fixes.test.ts`` (which pins the 3s contract);
// extending to 5-10s as the finding suggested would require updating
// that test file (not in this task's owned files).
//
// ``ESCALATE_TIMER_MS`` is the grace period after SIGTERM before
// SIGKILL / ``taskkill /F /T`` is sent. 3s (extended from the prior
// 1.5s) matches the shared ``killPythonProcessWithSigkillFallback``
// helper's contract.
export const KILL_TIMER_MS = 3000;
export const ESCALATE_TIMER_MS = 3000;

/**
 * Force-terminate the Python process tree on Windows.
 *
 * ``proc.kill()`` on Windows is ``TerminateProcess`` on the IMMEDIATE
 * process only — children are orphaned. The Python sidecar spawns a
 * native hotkey listener binary as a child process; orphaning it means
 * the binary keeps running, holding the global hotkey hook
 * (``SetWindowsHookEx``) and preventing the next Voice Typer launch
 * from re-registering its hotkeys.
 *
 * ``taskkill /F /T /PID`` walks the process tree via the toolhelp
 * snapshot and force-terminates every descendant. ``/F`` is the force
 * flag (no WM_CLOSE negotiation — equivalent to SIGKILL, not SIGTERM).
 * Without ``/F``, ``taskkill`` sends WM_CLOSE to GUI windows of the
 * process + descendants, which is a no-op for console processes.
 *
 * Synchronous (``spawnSync``) so the killTimer's state mutation
 * (``state.pythonProcess = null``) happens after the kill is initiated,
 * matching the POSIX ``proc.kill()`` contract. ``spawnSync`` blocks
 * the event loop for ~50ms (typical taskkill round-trip) — acceptable
 * on the killTimer callback which is already a teardown path.
 */
function _treeKillWindows(pid: number, force: boolean): void {
	const args = ["/T", "/PID", String(pid)];
	if (force) {
		args.unshift("/F");
	}
	try {
		spawnSync("taskkill", args, { stdio: "ignore" });
	} catch {
		/* best-effort — taskkill missing, PID already gone, or
		 * spawnSync threw. The caller proceeds regardless; the
		 * worst case is the old process surviving (and the new
		 * one failing to bind the single-instance mutex, which
		 * forces the next relaunch to clean it up). */
	}
}

export function stopPython() {
	//(): idempotency guard. If a stop is already in
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

	//clear the per-renderer rate-limit Map. Previously this Map
	// was never cleared in production — each destroyed BrowserWindow
	// leaked its `webContents.id` entry forever (the docstring on
	// `_resetIpcBackpressure` claimed it was called from here, but the
	// call site was missing). Clearing on stop_python bounds the Map's
	// growth to "at most one entry per currently-live renderer at any
	// moment between stop_python calls". Idempotent + safe to call
	// before the early-return paths below.
	_resetIpcBackpressure();

	//stop the heartbeat interval first so we don't queue a
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
	//clear the TCP startup timeout timer so the 60s deadline
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
	//exited. (The  plan also called for `.unref()`-ing the
	// timer in tcp-connect.ts, but that part is intentionally
	// skipped — see tcp-connect.ts:33-61 for the full rationale
	// and the xv-fa19-fixes.test.ts guard. The explicit clear
	// here is the belt-and-suspenders guarantee.)
	clearTcpStartupTimeout();
	//(): the top-of-function `isStopping` guard above
	// is what guarantees `quit_app` is sent at most once per stop
	// cycle — a second call never reaches this line. The
	// `.catch(() => {})` swallows the rejection that fires when
	// the socket is already half-closed (which is exactly the case
	// during the breaker-trip cascade).
	sendToPython({ type: "quit_app" }).catch(() => {});

	//(): guard against duplicate killTimer creation.
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
			const pid = proc.pid;
			//escalate SIGTERM → SIGKILL after a
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
			//
			// Windows: ``proc.kill()`` is TerminateProcess
			// on the immediate process only — orphans the
			// native hotkey binary child. Use ``taskkill
			// /T /PID`` (no /F) for the graceful attempt
			// and ``taskkill /F /T /PID`` for the escalation
			// so the entire process tree is reaped.
			try {
				if (!proc.killed) {
					if (process.platform === "win32") {
						if (typeof pid === "number") {
							_treeKillWindows(pid, false);
						}
					} else {
						proc.kill("SIGTERM");
					}
				}
				// Escalation: SIGKILL on POSIX,
				// ``taskkill /F /T /PID`` on Windows.
				// Fires ESCALATE_TIMER_MS after the graceful
				// signal (was 1500; extended to 3000 to match
				// the sidecar's typical cleanup time and the
				// shared killPythonProcessWithSigkillFallback
				// helper). If the proc has already exited
				// (the graceful signal worked), the escalation
				// is a no-op (POSIX: proc.kill on a dead pid
				// throws — caught below; Windows: taskkill on
				// a dead pid returns non-zero exit code,
				// swallowed).
				const escalateTimer = setTimeout(() => {
					if (!proc.killed) {
						try {
							if (process.platform === "win32") {
								if (typeof pid === "number") {
									_treeKillWindows(pid, true);
								}
							} else {
								proc.kill("SIGKILL");
							}
						} catch {
							/* best-effort — proc may have already exited */
						}
					}
				}, ESCALATE_TIMER_MS);
				proc.once("exit", () => clearTimeout(escalateTimer));
			} catch {
				/* best-effort — proc may have already exited */
			}
			state.pythonProcess = null;
		}
		armedKillTimer = null;
		isStopping = false;
		isStopped = true;
	}, KILL_TIMER_MS);
	//do NOT `.unref()` the killTimer — it must keep Electron
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
		//null `state.pythonProcess` so downstream callers
		// (notably `index.ts::will-quit`) see that Python is already
		// gone — previously this only flipped `isStopped`, causing
		// will-quit to register a stale `.once("exit")` listener.
		state.pythonProcess = null;
		//(): Python exited gracefully — shutdown
		// is complete. Flip the flags so subsequent stopPython()
		// calls are no-ops.
		isStopping = false;
		isStopped = true;
	});
}

/**
 * : reset the idempotency flags so a freshly-spawned backend
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
