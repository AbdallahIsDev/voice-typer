/**
 * TCP startup watchdog: the 60s "backend failed to start" window.
 *
 * Split out of `tcp-connect.ts`. Owns the module-level timer, its
 * clearing helper, and the arm/fire/dialog logic.
 */

import { app, dialog } from "electron";
import { log } from "../../logging";
import { state } from "../../state";

//startup timeout. If Python doesn't connect within 60s
// of the first tryConnect(), show a clear error dialog and quit.
// This covers the case where Python spawns successfully but hangs
// during torch import without exiting — the retry loop would otherwise
// run forever with no window and no error. The timer is cleared on
// successful connect. The callback also safety-checks state to avoid
// firing after stopPython / during quit (stop-python.ts is responsible
// for clearing the retry timer; this startup timer is independent and
// guarded by the checks below).
export const TCP_STARTUP_TIMEOUT_MS = 60_000;
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

/**
 * Arm the startup timeout on the first connect attempt. If
 * tcpConnect is called again (e.g. after a dev-mode restart),
 * the timer is already cleared from the prior successful
 * connect, so a fresh timer starts.
 */
export function armTcpStartupTimeout(): void {
	if (_tcpStartupTimeoutTimer === null) {
		_tcpStartupTimeoutTimer = setTimeout(() => {
			_tcpStartupTimeoutTimer = null;
			// Safety checks: if Python already connected, the app is
			// quitting, or a stop was explicitly initiated, skip the
			// error dialog. NOTE: ``state.pythonProcess === null`` is
			// deliberately NOT a short-circuit here — a null process
			// with no stop in flight means the spawn failed (or the
			// adopted backend never appeared) and the TCP retry loop
			// would otherwise run FOREVER, leaving a hidden zombie
			// holding the single-instance lock that swallows every
			// later launch (incl. OS autostart at login). The 60s
			// timeout must fire + quit in that case (autostart-zombie
			// fix, diagnosed on-device 2026-08-15: a zombie Electron
			// from Aug 14 23:36 held the lock and killed every launch
			// attempt since).
			if (
				state.tcpSocket !== null ||
				app.isQuitting ||
				state._stopPythonCalled
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
}
