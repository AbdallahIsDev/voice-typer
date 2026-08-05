/**
 * : shared SIGTERM+SIGKILL-fallback helper for killing the
 * Python backend process.
 *
 * Extracted from `relaunch-app.ts`'s `_killPythonProcessWithSigkillFallback`
 * so both `relaunch-app.ts` (dev + prod kill branches) and `stop-python.ts`
 * (3s `killTimer` fallback) use the same kill-escalation path. Previously,
 * `stop-python.ts` sent only a bare `proc.kill()` (SIGTERM on POSIX,
 * `TerminateProcess` on Windows) with NO SIGKILL escalation — a Python
 * process stuck in a C extension (torch/sounddevice) would ignore the
 * SIGTERM and survive as a zombie holding the `VoiceTyperSingleInstance`
 * mutex, blocking the next launch.
 *
 * Contract
 * --------
 * - ``mode === "dev"``: sends ``SIGTERM`` first (graceful shutdown)
 *   then ``SIGKILL`` after 3 s if the proc hasn't exited.
 * - ``mode === "prod"``: sends the default ``.kill()`` (SIGTERM on
 *   POSIX, ``TerminateProcess`` on Windows) then ``SIGKILL`` after 3 s
 *   if the proc hasn't exited.
 * - Removes ALL existing ``"exit"`` listeners from the proc before
 *   killing (the ``start-python.ts`` exit handler would otherwise show
 *   a misleading "Python backend crashed" dialog when the proc is
 *   killed by signal). Callers that need to observe the exit event
 *   should pass an ``onExit`` callback — the helper registers its own
 *   ``proc.once("exit", ...)`` listener that invokes ``onExit`` after
 *   clearing the internal SIGKILL fallback timer.
 * - The ```` fix: the SIGKILL fallback checks
 *   ``proc.exitCode === null && proc.signalCode === null`` (the proc
 *   has NOT actually exited) instead of ``!proc.killed`` (which only
 *   indicates a signal was sent, not that the proc exited). The old
 *   check was dead code — ``proc.killed`` is ``true`` immediately
 *   after ``proc.kill("SIGTERM")``, so the SIGKILL fallback never
 *   fired.
 *
 * Best-effort: all errors are logged at WARN. The caller proceeds
 * regardless — the worst case is the old Python process surviving
 * (and the new one failing to bind the single-instance mutex, which
 * forces the next relaunch to clean it up).
 *
 * @param mode  "dev" (SIGTERM first) or "prod" (default kill first).
 * @param onExit  Optional callback invoked when the proc emits its
 *                ``"exit"`` event (after the helper has armed its
 *                SIGKILL fallback). Use this to preserve caller-side
 *                flag-clearing semantics (e.g. ``stop-python.ts``'s
 *                ``isStopping`` / ``isStopped`` flags).
 */
import { log } from "../logging";
import { state } from "../state";

export function killPythonProcessWithSigkillFallback(
	mode: "dev" | "prod",
	onExit?: () => void,
): void {
	try {
		if (state.pythonProcess) {
			const proc = state.pythonProcess;
			// Remove existing exit listeners (notably start-python.ts's
			// exit handler, which would show a misleading "crash" dialog
			// when the proc is killed by signal). Callers that need
			// exit-event observability pass an `onExit` callback below.
			proc.removeAllListeners("exit");
			//check `exitCode` / `signalCode` (the proc has
			// actually exited) instead of `proc.killed` (a signal was
			// sent). `proc.killed` becomes true immediately after
			// `proc.kill(...)` even if the proc ignores the signal, so
			// the old `!proc.killed` check was dead code — the SIGKILL
			// fallback never fired.
			if (proc.exitCode === null && proc.signalCode === null) {
				if (mode === "dev") {
					proc.kill("SIGTERM");
				} else {
					proc.kill();
				}
			}
			// SIGKILL fallback — if Python doesn't exit within 3 s
			// (stuck in a C extension like torch/sounddevice),
			// force-kill so the old process doesn't survive and hold
			// the VoiceTyperSingleInstance mutex.
			const killTimer = setTimeout(() => {
				//same exitCode/signalCode check as above.
				// `proc.killed` would be true (we sent SIGTERM), but
				// the proc may still be alive.
				if (proc.exitCode === null && proc.signalCode === null) {
					try {
						proc.kill("SIGKILL");
					} catch (e) {
						/* best-effort — proc may have already exited.
						 * Log at debug so the failure is
						 * observable in the diagnostic log without spamming
						 * the default level. */
						log.debug("[KILL] SIGKILL fallback failed (non-fatal):", e);
					}
				}
			}, 3000);
			proc.once("exit", () => {
				clearTimeout(killTimer);
				onExit?.();
			});
		}
	} catch (e) {
		log.warn(`[KILL] kill old Python (${mode}) failed:`, e);
	}
}
