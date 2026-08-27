/**
 * Relaunch the entire Electron application: kill any lingering Python
 * backend, then ask the OS to start a fresh Electron process and exit
 * the current one immediately.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * This is the implementation of the tray "Restart" menu item.  The
 * user explicitly requested that "everything should be closed and
 * opened again" — both the Python backend AND the Electron shell.
 * This eliminates all cross-process state coordination races that
 * the old "Python-only restart" design suffered from.
 *
 * Idempotent: if called twice (e.g. once from the "relaunch_app"
 * TCP event handler and once from the pythonProcess exit handler), the
 * second call is a no-op because `_relaunching` is already true.
 *
 * `app.exit(0)` is used (not `app.quit()`) because we want immediate
 * termination without firing `before-quit` (which would call
 * `stopPython()` and try to send `quit_app` to a Python that's
 * already exiting — a waste of 3 seconds on the kill timer).  The
 * Python process is force-killed directly here instead.
 */

// Bounded wait for the old Python process to actually exit
// between the SIGTERM and the fresh spawn. Node's ChildProcess shape
// is all we need (exitCode/signalCode/once) — no other API surface.
import type { ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { app, dialog } from "electron";
import { APP_NAME } from "../branding";
import { mainT } from "../i18n";
import { log } from "../logging";
import { computeConfigDir } from "../single_instance";
import { state } from "../state";
// `atomicWriteFile` is the shared temp+fsync+rename helper
// (mirrors the Rust `atomic_write_bytes` canonical implementation).
// Previously `_appendRestartTimestamp` inlined an equivalent
// write-tmp / fsync / rename sequence — a duplicate of the helper in
// `atomic-write.ts`. The inline copy drifted (e.g. the helper's
// `finally { closeSync }` was replicated as try/finally here, but a
// future fix to the helper's fsync error handling would not propagate).
// Replacing the inline copy with the shared helper makes
// `atomic-write.ts` the single source of truth for the atomic-write
// pattern across the main process.
import { atomicWriteFile } from "./atomic-write";
import { killPythonProcessWithSigkillFallback } from "./kill-python";
import { _resetIpcBackpressure } from "./send-to-python";
import { startPython } from "./start-python";
//clear the TCP startup timeout timer so the 60s deadline
// doesn't fire AFTER we've torn down Python (which would trip the
// premature "Python backend failed to start" dialog + `app.quit()`
//mid-restart). Mirrors the same call in `stop-python.ts` ()
// so both teardown paths — stopPython() and relaunchApp() — clear
// the timer. Placed at the TOP of the function (before any work)
// so it runs even if a downstream step throws — the worst case
// without this is a stale timer that fires the false-positive
// "Python backend failed to start" dialog while a new backend is
// mid-spawn, then calls `app.quit()`, tearing down the Electron
// shell mid-restart. The `if (_tcpStartupTimeoutTimer === null)`
// guard in `tcpConnect()` lets the next attempt arm a fresh timer.
import { clearTcpStartupTimeout } from "./tcp-connect";

//production-mode restart counter to break crash loops.
//
// Without a cap, a deterministic Python-side `sys.exit(0)` (or an
// OS-level relaunch race) makes `relaunchApp()` fire repeatedly:
// each new Electron process spawns Python, Python immediately
// requests `relaunch_electron`, Electron calls `app.relaunch()` +
// `app.exit(0)`, the new process repeats the cycle. The loop
// burns battery, spams the system, and on Windows holds the
// single-instance mutex across rapid re-acquire / release cycles
// (which can leak via the OS's cleanup timer).
//
// The cap is 3 production-mode relaunches within a rolling 60s
// window. If exceeded, we surface a modal error dialog and call
// `app.quit()` (NOT `app.relaunch()`) so the user can investigate
// the underlying Python crash log before retrying.
//
// State is persisted to ``restart_history.json`` in the config dir
// (NOT in-memory) because `app.relaunch()` + `app.exit(0)` spawns
// a fresh OS process — in-memory state would be lost. The file
// holds a small JSON array of recent restart timestamps
// (epoch millis). Entries older than the 60s window are pruned
// on each read so the file does not grow unboundedly.
const MAX_RESTARTS_PER_WINDOW = 3;
const RESTART_WINDOW_MS = 60_000;

// Persistence ownership: ``restart_history.json`` is the ELECTRON-only
// production app-relaunch crash-loop breaker (a small array of epoch-ms
// relaunch timestamps, pruned to the 60s window). It is intentionally
// INDEPENDENT of the Tauri runtime's ``restart_counter.json``
// (src-tauri/src/sidecar/supervisor.rs: a {count, ts} sidecar-respawn
// circuit breaker with a 10-minute staleness window). The two runtimes
// never coexist and their schemas / semantics / lifecycles differ — do
// NOT merge them into one "restart" file.

function _restartHistoryPath(): string {
	return path.join(computeConfigDir(), "restart_history.json");
}

function _readRestartHistory(): number[] {
	try {
		const file = _restartHistoryPath();
		if (!fs.existsSync(file)) return [];
		const raw = fs.readFileSync(file, "utf-8").trim();
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		const now = Date.now();
		const cutoff = now - RESTART_WINDOW_MS;
		// Keep only timestamps inside the rolling 60s window.
		return parsed
			.map((t) => (typeof t === "number" && Number.isFinite(t) ? t : NaN))
			.filter((t) => Number.isFinite(t) && t > cutoff);
	} catch (e) {
		// Best-effort: corrupt / unreadable file → treat as empty so
		// a transient FS issue never blocks a legitimate restart.
		log.warn("[RESTART] failed to read restart_history.json:", e);
		return [];
	}
}

/**
 * Shared SIGTERM+SIGKILL-fallback helper. The inline copy that used to
 * live here was removed and replaced with an import from `./kill-python`
 * — the helper is now the single source of truth for the kill-escalation
 * pattern (DRY). See `kill-python.ts` for the full contract.
 *
 * The helper's `onExit` callback is NOT used here — `relaunchApp()`
 * proceeds regardless of when the old proc actually exits (the dev
 * branch spawns a fresh Python immediately; the prod branch calls
 * `app.exit(0)` immediately). The helper's internal SIGKILL fallback
 * timer handles the worst case (stuck in a C extension).
 */

function _appendRestartTimestamp(history: number[]): void {
	try {
		const file = _restartHistoryPath();
		const dir = path.dirname(file);
		fs.mkdirSync(dir, { recursive: true });
		const now = Date.now();
		const cutoff = now - RESTART_WINDOW_MS;
		const pruned = history.filter((t) => t > cutoff);
		pruned.push(now);
		// mode 0o600: the file records restart cadence (operational
		// telemetry) — no PII, but tighten perms anyway to match the
		// rest of the config dir (electron.pid is also 0o600).
		//
		// delegate to the shared `atomicWriteFile` helper
		// (temp + fsync + rename). Pre-fix, this function inlined
		// an equivalent sequence (write `<file>.tmp` with
		// `flag: "w"`, `fsyncSync`, `renameSync`) — a duplicate of
		// `atomic-write.ts`'s helper. The inline copy was a
		// truncate-then-write whose partial-write window (crash
		// mid-write) could leave a corrupted JSON body that
		// `_readRestartHistory`'s `JSON.parse` rejected, silently
		// returning `[]` and BYPASSING the loop-breaker on the next
		// launch (re-entering the crash loop with no "cannot restart
		// safely" dialog). The Rust mirror
		// (`supervisor.rs::write_restart_counter`) already used
		// `atomic_write_bytes` for this exact reason; the shared
		// JS helper now mirrors that pattern in one place. A crash
		// mid-write now leaves the previous (complete) history file
		// intact — the `.tmp` is the only casualty, and it's
		// overwritten on the next attempt.
		atomicWriteFile(file, JSON.stringify(pruned), { mode: 0o600 });
	} catch (e) {
		// Best-effort: if we can't persist the counter, the worst
		// case is the cap not firing this round — the underlying
		// restart still proceeds (better than bricking the user's
		// only recovery path).
		log.warn("[RESTART] failed to persist restart_history.json:", e);
	}
}

/**
 * Wait (bounded) for the old Python process to actually exit.
 *
 * The dev-mode restart used to call `startPython()` immediately after
 * `killPythonProcessWithSigkillFallback()` — but the old backend may
 * still be alive for up to the 3 s SIGKILL fallback window, and the
 * fresh backend cannot bind IPC_PORT until the dying one releases the
 * listening socket. Callers perceived this as a multi-second
 * "Restarting…" hang while `tcpConnect()` hammered a held port.
 *
 * Resolves when the process emits "exit" OR once the SIGKILL fallback
 * has fired (3.5 s — slightly past the helper's 3 s escalation so we
 * observe its effect), whichever comes first. If the proc already
 * exited (`exitCode`/`signalCode` non-null) it resolves immediately.
 * Never rejects — worst case is proceeding after the timeout, which
 * is strictly better than the old always-immediate spawn.
 */
const RESTART_EXIT_WAIT_MS = 3_500;

function _waitForProcessExit(proc: ChildProcess): Promise<void> {
	return new Promise<void>((resolve) => {
		if (proc.exitCode !== null || proc.signalCode !== null) {
			resolve();
			return;
		}
		let settled = false;
		const done = () => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			// Defensive: some test doubles expose `once`/`on` but not
			// `removeListener`. Cleanup is best-effort — resolving
			// matters more than deregistering.
			try {
				proc.removeListener("exit", onExit);
			} catch {
				/* ignore */
			}
			resolve();
		};
		const onExit = () => done();
		const timer = setTimeout(done, RESTART_EXIT_WAIT_MS);
		proc.once("exit", onExit);
	});
}

export async function relaunchApp(): Promise<void> {
	//clear the TCP startup timeout timer FIRST so it
	// doesn't fire mid-restart and trip the false-positive
	// "Python backend failed to start" dialog + `app.quit()`.
	clearTcpStartupTimeout();
	// Idempotency guard: if a relaunch is already in flight, do nothing.
	if (state._relaunching) {
		//route through the structured `log` logger so
		// the no-op is captured in `electron-runtime.log` (warn level —
		// a duplicate relaunch call indicates a logic race worth
		// surfacing, not a routine lifecycle event).
		log.warn("[RESTART] relaunchApp() called but already relaunching — no-op");
		return;
	}
	state._relaunching = true;
	state._restartTriggered = true;
	//ELEC-1: ``app.isQuitting = true`` is needed ONLY for
	// the production ``app.exit(0)`` path (so the close handler
	// doesn't preventDefault during teardown).  Setting it here
	// unconditionally leaks into the dev-mode branch — after a
	// dev-mode "Restart", ``app.isQuitting`` stays ``true`` for the
	// rest of the process lifetime, so the next X-click DESTROYS
	// the window instead of hiding it (close-to-tray).  Moved into
	// the production-only branch below.
	//
	//this is the documented dev/prod ASYMMETRY — the dev
	// branch intentionally does NOT set ``app.isQuitting`` (it
	// would break close-to-tray on subsequent X-clicks) and the
	// prod branch does set it (because ``app.exit(0)`` bypasses
	//the close handler anyway via the  ``.destroy()``
	// path, so the only consumer of ``app.isQuitting`` in the prod
	// branch is the early-exit handler in ``start-python.ts:108-196``
	// that needs to know "we're tearing down, don't try to
	// reconnect"). Any code that checks ``app.isQuitting`` during
	// a dev-mode restart's failure path will see ``false`` — that
	// is intentional, because the dev-mode restart PRESERVES the
	// Electron process (no teardown is happening; only Python is
	// being recycled). Error paths that need to distinguish "dev
	// restart in flight" from "real quit" should check
	// ``state._restartTriggered`` (set above) — it is set in BOTH
	// branches and cleared by the dev branch's
	// ``state._relaunching = false`` line at the bottom of the
	// dev path. The prod branch never clears it because the
	// process exits.

	// ── Dev mode: keep Electron alive, just restart Python ──────────
	// Production: app.relaunch() + app.exit(0) fully replaces the OS process.
	if (!app.isPackaged) {
		log.info(
			"[RESTART] Dev mode: restarting Python backend (Electron stays alive)",
		);

		// Kill old Python via the shared SIGTERM+SIGKILL-fallback
		// helper (imported from `./kill-python`). The state-reset
		// block below is branch-specific (dev mode clears more
		// state than prod).
		killPythonProcessWithSigkillFallback("dev");

		// Clean up TCP + state
		try {
			if (state.tcpSocket) state.tcpSocket.destroy();
		} catch (e) {
			//surface the destroy failure instead of swallowing.
			log.warn("[RESTART] dev: tcpSocket.destroy failed:", e);
		}
		state.tcpSocket = null;
		//clear the per-renderer rate-limit Map so destroyed-window
		// entries don't accumulate across dev-mode restarts (each restart
		// creates a fresh BrowserWindow with a fresh webContents.id; the
		// old id's entry would otherwise leak forever).
		_resetIpcBackpressure();
		//reset the TCP line buffer so stale partial
		// frames from the previous backend don't bleed into the
		// next connection.
		state.tcpBuffer = Buffer.alloc(0);
		state._tcpAuthed = false;
		state.pythonReady = false;
		state.pythonExitedEarly = false;
		state._hadConnectedBefore = false;
		state._tcpRetryCount = 0;
		// R6-F6: clear the pending TCP retry timer BEFORE bumping the
		// generation, otherwise the stale `tryConnect()` invocation
		// would fire once more (creating a fresh socket that
		// immediately hits the generation mismatch and bails — wasted
		// work + a brief window of "extra" socket churn).
		if (state._tcpRetryTimer) {
			clearTimeout(state._tcpRetryTimer);
			state._tcpRetryTimer = null;
		}
		state._tcpRetryGeneration++;
		//clear the heartbeat interval — the next connect
		// callback will start a fresh one when the new backend
		// accepts our TCP connection.
		if (state.heartbeatInterval) {
			clearInterval(state.heartbeatInterval);
			state.heartbeatInterval = null;
		}

		// Wait for the OLD backend to actually release the IPC
		// port before spawning its replacement. All synchronous
		// teardown above has already run; from here until the fresh
		// spawn we are purely waiting on the dying process. Bounded at
		// 3.5 s so a wedged process can't stall the restart forever
		// (the kill helper's own SIGKILL escalation fires at 3 s).
		const oldProc = state.pythonProcess;
		if (oldProc) {
			await _waitForProcessExit(oldProc);
			log.info("[RESTART] dev: old Python exited — spawning replacement");
		}

		// Reject pending IPC, reload renderer, spawn fresh Python
		for (const [id, entry] of state.pendingRequests) {
			state.pendingRequests.delete(id);
			entry.reject(new Error("Application is restarting"));
		}
		try {
			if (state.mainWindow && !state.mainWindow.isDestroyed()) {
				if (process.env.ELECTRON_RENDERER_URL) {
					state.mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
				} else {
					state.mainWindow.loadFile(
						path.join(__dirname, "../renderer/index.html"),
					);
				}
			}
		} catch (e) {
			//surface the reload failure instead of swallowing.
			log.warn("[RESTART] dev: mainWindow reload failed:", e);
		}

		// Clear VT_PYTHON_PORT / VT_IPC_TOKEN before calling startPython()
		// so the spawn branch runs (not the "connect to existing backend"
		// branch).  In standalone/terminal mode the original Python CLI
		// set these env vars when spawning Electron; if they survive into
		// the dev-mode restart, startPython() skips the spawn and just
		// tcpConnect()s to the old (dying) backend — no new Python is
		// ever created and the app is left headless (Electron alive with
		// no backend process).
		delete process.env.VT_PYTHON_PORT;
		delete process.env.VT_IPC_TOKEN;

		//wrap ``startPython()`` in try/finally so
		// ``state._relaunching`` is cleared even if startPython
		// throws (e.g. pythonArgs() throws on unrecognized
		// platform, spawn() throws on invalid arguments,
		// tcpConnect() throws on invalid port). Pre-fix, a
		// throw left ``_relaunching = true`` permanently —
		// every subsequent ``relaunchApp()`` call was a no-op
		// (the idempotency guard at the top of this function
		// short-circuits), bricking the Restart tray menu
		// item for the rest of the Electron process lifetime.
		// The production branch (``app.exit(0)``) doesn't have
		// this issue because the process terminates.
		try {
			startPython();
		} finally {
			state._relaunching = false;
		}
		log.info("[RESTART] Dev mode restart complete -- waiting for new backend");
		return;
	}

	log.info(
		"[RESTART] Production mode: relaunching entire Electron application",
	);

	//cap production-mode restarts to MAX_RESTARTS_PER_WINDOW
	// (3) within a rolling RESTART_WINDOW_MS (60s) window. Without this
	// cap, a deterministic Python-side `sys.exit(0)` loop (or an
	// OS-level race) would burn battery + spam the system. When
	// exceeded, show a modal dialog explaining the situation and call
	// `app.quit()` (NOT `app.relaunch()`) so the loop is broken.
	//
	// The counter persists to ``restart_history.json`` in the config
	// dir — see the docstring at the top of this file for the
	// rationale. The check happens BEFORE we mutate any other state
	// (kill old Python, clear tcpSocket, etc.) so a rejected restart
	// leaves the running process in a consistent state until
	// `app.quit()` finishes.
	const recentRestarts = _readRestartHistory();
	if (recentRestarts.length >= MAX_RESTARTS_PER_WINDOW) {
		log.error(
			"[RESTART] production restart cap exceeded: %d restarts in the last %ds — refusing to relaunch (loop breaker)",
			recentRestarts.length,
			RESTART_WINDOW_MS / 1000,
		);
		try {
			dialog.showErrorBox(
				mainT("dialog.pythonBackend.restartLoopTitle", {
					appName: APP_NAME,
				}),
				mainT("dialog.pythonBackend.restartLoopBody", {
					appName: APP_NAME,
					count: String(recentRestarts.length),
					seconds: String(RESTART_WINDOW_MS / 1000),
				}),
			);
		} catch (e) {
			// dialog may be unavailable in headless mode (CI,
			// `DISPLAY` unset, or pre-app-ready) — the log.error
			// above is the primary signal in that case. Log at
			// debug so a dialog failure is observable in the
			// diagnostic log without spamming the default level.
			log.debug("[RESTART] crash-loop dialog.show failed:", e);
		}
		// Break the loop: quit WITHOUT relaunching. The user must
		// start the app again manually after investigating.
		app.isQuitting = true;
		app.exit(1);
		return;
	}
	// Persist this restart attempt so the next process knows about it.
	_appendRestartTimestamp(recentRestarts);

	//ELEC-1: set isQuitting ONLY in the production branch
	// so the close handler doesn't preventDefault during app.exit(0)
	// teardown.  The dev branch above does NOT set it (dev mode keeps
	// Electron alive and must preserve close-to-tray behavior).
	app.isQuitting = true;

	// Kill old Python via the shared SIGTERM+SIGKILL-fallback helper
	// (imported from `./kill-python` — same pattern as the dev branch
	// above).
	killPythonProcessWithSigkillFallback("prod");
	try {
		if (state.tcpSocket) state.tcpSocket.destroy();
	} catch (e) {
		//surface the destroy failure instead of swallowing.
		log.warn("[RESTART] prod: tcpSocket.destroy failed:", e);
	}
	state.tcpSocket = null;
	//clear the per-renderer rate-limit Map on production relaunch
	// too. The next process will start with a fresh Map; without this
	// call, the entries from this process would survive until the OS
	// reclaims the process memory (harmless on exit, but the call is
	// here for symmetry with the dev branch and with stop-python.ts).
	_resetIpcBackpressure();
	//reset the TCP line buffer (see dev branch above).
	state.tcpBuffer = Buffer.alloc(0);
	state._tcpAuthed = false;
	// R6-F6: clear the pending TCP retry timer so a stale
	// `tryConnect()` doesn't fire during the brief exit window.
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	//clear the heartbeat interval — process is exiting and
	// we don't want the timer to fire sendToPython() against a
	// dead socket during the brief exit window.
	if (state.heartbeatInterval) {
		clearInterval(state.heartbeatInterval);
		state.heartbeatInterval = null;
	}

	// Reject pending IPC and spawn a brand new OS process
	for (const [id, entry] of state.pendingRequests) {
		state.pendingRequests.delete(id);
		entry.reject(new Error("Application is restarting"));
	}
	app.relaunch({ args: process.argv.slice(1) });
	app.exit(0);
}
