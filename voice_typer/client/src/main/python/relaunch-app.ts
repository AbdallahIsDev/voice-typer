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

import fs from "node:fs";
import path from "node:path";
import { app, dialog } from "electron";
import { log } from "../logging";
import { computeConfigDir } from "../single_instance";
import { state } from "../state";
import { _resetIpcBackpressure } from "./send-to-python";
import { startPython } from "./start-python";
// XZ-R18-07: clear the TCP startup timeout timer so the 60s deadline
// doesn't fire AFTER we've torn down Python (which would trip the
// premature "Python backend failed to start" dialog + `app.quit()`
// mid-restart). Mirrors the same call in `stop-python.ts` (ER-29)
// so both teardown paths — stopPython() and relaunchApp() — clear
// the timer. Placed at the TOP of the function (before any work)
// so it runs even if a downstream step throws — the worst case
// without this is a stale timer that fires the false-positive
// "Python backend failed to start" dialog while a new backend is
// mid-spawn, then calls `app.quit()`, tearing down the Electron
// shell mid-restart. The `if (_tcpStartupTimeoutTimer === null)`
// guard in `tcpConnect()` lets the next attempt arm a fresh timer.
import { clearTcpStartupTimeout } from "./tcp-connect";

// XZ-R18-11: production-mode restart counter to break crash loops.
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
		fs.writeFileSync(file, JSON.stringify(pruned), {
			encoding: "utf-8",
			flag: "w",
			mode: 0o600,
		});
	} catch (e) {
		// Best-effort: if we can't persist the counter, the worst
		// case is the cap not firing this round — the underlying
		// restart still proceeds (better than bricking the user's
		// only recovery path).
		log.warn("[RESTART] failed to persist restart_history.json:", e);
	}
}

export function relaunchApp(): void {
	// XZ-R18-07: clear the TCP startup timeout timer FIRST so it
	// doesn't fire mid-restart and trip the false-positive
	// "Python backend failed to start" dialog + `app.quit()`.
	clearTcpStartupTimeout();
	// Idempotency guard: if a relaunch is already in flight, do nothing.
	if (state._relaunching) {
		// DE-87 / S2-CR-75: route through the structured `log` logger so
		// the no-op is captured in `electron-runtime.log` (warn level —
		// a duplicate relaunch call indicates a logic race worth
		// surfacing, not a routine lifecycle event).
		log.warn("[RESTART] relaunchApp() called but already relaunching — no-op");
		return;
	}
	state._relaunching = true;
	state._restartTriggered = true;
	// HIGH-31 / ELEC-1: ``app.isQuitting = true`` is needed ONLY for
	// the production ``app.exit(0)`` path (so the close handler
	// doesn't preventDefault during teardown).  Setting it here
	// unconditionally leaks into the dev-mode branch — after a
	// dev-mode "Restart", ``app.isQuitting`` stays ``true`` for the
	// rest of the process lifetime, so the next X-click DESTROYS
	// the window instead of hiding it (close-to-tray).  Moved into
	// the production-only branch below.
	//
	// AC-119: this is the documented dev/prod ASYMMETRY — the dev
	// branch intentionally does NOT set ``app.isQuitting`` (it
	// would break close-to-tray on subsequent X-clicks) and the
	// prod branch does set it (because ``app.exit(0)`` bypasses
	// the close handler anyway via the S3-CR-34 ``.destroy()``
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

		// Kill old Python (remove exit listener first to prevent race)
		try {
			if (state.pythonProcess) {
				const proc = state.pythonProcess;
				proc.removeAllListeners("exit");
				if (!proc.killed) proc.kill("SIGTERM");
				// PVT-G5-039: SIGKILL fallback — if Python
				// doesn't exit within 3s (stuck in a C
				// extension like torch/sounddevice),
				// force-kill it so the old process
				// doesn't survive and hold the
				// VoiceTyperSingleInstance mutex.
				const killTimer = setTimeout(() => {
					if (!proc.killed) {
						try {
							proc.kill("SIGKILL");
						} catch {
							/* best-effort */
						}
					}
				}, 3000);
				proc.once("exit", () => clearTimeout(killTimer));
			}
		} catch (e) {
			// GT-B3-7: surface the kill failure instead of swallowing.
			log.warn("[RESTART] dev: kill old Python failed:", e);
		}
		state.pythonProcess = null;

		// Clean up TCP + state
		try {
			if (state.tcpSocket) state.tcpSocket.destroy();
		} catch (e) {
			// GT-B3-7: surface the destroy failure instead of swallowing.
			log.warn("[RESTART] dev: tcpSocket.destroy failed:", e);
		}
		state.tcpSocket = null;
		// TY-35: clear the per-renderer rate-limit Map so destroyed-window
		// entries don't accumulate across dev-mode restarts (each restart
		// creates a fresh BrowserWindow with a fresh webContents.id; the
		// old id's entry would otherwise leak forever).
		_resetIpcBackpressure();
		// PVT-G5-007: reset the TCP line buffer so stale partial
		// frames from the previous backend don't bleed into the
		// next connection.
		state.tcpBuffer = "";
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
		// RW-10: clear the heartbeat interval — the next connect
		// callback will start a fresh one when the new backend
		// accepts our TCP connection.
		if (state.heartbeatInterval) {
			clearInterval(state.heartbeatInterval);
			state.heartbeatInterval = null;
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
			// GT-B3-7: surface the reload failure instead of swallowing.
			log.warn("[RESTART] dev: mainWindow reload failed:", e);
		}

		startPython();
		state._relaunching = false;
		log.info("[RESTART] Dev mode restart complete -- waiting for new backend");
		return;
	}

	log.info(
		"[RESTART] Production mode: relaunching entire Electron application",
	);

	// XZ-R18-11: cap production-mode restarts to MAX_RESTARTS_PER_WINDOW
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
				"Voice Typer cannot restart safely",
				`Voice Typer has been asked to restart ${recentRestarts.length} times ` +
					`in the last ${RESTART_WINDOW_MS / 1000} seconds, which suggests ` +
					`the Python backend is crashing on launch.\n\n` +
					`To avoid a crash loop, the automatic restart has been cancelled. ` +
					`Please check the log files in your Voice Typer config directory ` +
					`(python_crash.*.txt and voice-typer.log), then start Voice Typer ` +
					`manually once you've addressed the underlying issue.`,
			);
		} catch {
			// dialog may be unavailable in headless mode — the
			// log.error above is the primary signal in that case.
		}
		// Break the loop: quit WITHOUT relaunching. The user must
		// start the app again manually after investigating.
		app.isQuitting = true;
		app.exit(1);
		return;
	}
	// Persist this restart attempt so the next process knows about it.
	_appendRestartTimestamp(recentRestarts);

	// HIGH-31 / ELEC-1: set isQuitting ONLY in the production branch
	// so the close handler doesn't preventDefault during app.exit(0)
	// teardown.  The dev branch above does NOT set it (dev mode keeps
	// Electron alive and must preserve close-to-tray behavior).
	app.isQuitting = true;

	// Kill old Python (remove exit listener first to prevent race)
	try {
		if (state.pythonProcess) {
			const proc = state.pythonProcess;
			proc.removeAllListeners("exit");
			if (!proc.killed) proc.kill();
			// PVT-G5-039: SIGKILL fallback — same pattern as
			// the dev branch above and stop-python.ts:38-43.
			// If Python is stuck in a C extension and ignores
			// SIGTERM, force-kill after 3s so the old process
			// doesn't hold the single-instance mutex.
			const killTimer = setTimeout(() => {
				if (!proc.killed) {
					try {
						proc.kill("SIGKILL");
					} catch {
						/* best-effort */
					}
				}
			}, 3000);
			proc.once("exit", () => clearTimeout(killTimer));
		}
	} catch (e) {
		// GT-B3-7: surface the kill failure instead of swallowing.
		log.warn("[RESTART] prod: kill old Python failed:", e);
	}
	state.pythonProcess = null;
	try {
		if (state.tcpSocket) state.tcpSocket.destroy();
	} catch (e) {
		// GT-B3-7: surface the destroy failure instead of swallowing.
		log.warn("[RESTART] prod: tcpSocket.destroy failed:", e);
	}
	state.tcpSocket = null;
	// TY-35: clear the per-renderer rate-limit Map on production relaunch
	// too. The next process will start with a fresh Map; without this
	// call, the entries from this process would survive until the OS
	// reclaims the process memory (harmless on exit, but the call is
	// here for symmetry with the dev branch and with stop-python.ts).
	_resetIpcBackpressure();
	// PVT-G5-007: reset the TCP line buffer (see dev branch above).
	state.tcpBuffer = "";
	state._tcpAuthed = false;
	// R6-F6: clear the pending TCP retry timer so a stale
	// `tryConnect()` doesn't fire during the brief exit window.
	if (state._tcpRetryTimer) {
		clearTimeout(state._tcpRetryTimer);
		state._tcpRetryTimer = null;
	}
	// RW-10: clear the heartbeat interval — process is exiting and
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
