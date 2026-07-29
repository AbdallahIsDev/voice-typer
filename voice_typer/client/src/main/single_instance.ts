/**
 * Single-instance gate + stale-lock recovery.
 *
 * Extracted from `index.ts` (REF-2). Owns:
 *   - `computeConfigDir()` — shared config-directory resolver (mirrors
 *     the Python backend's `_config_dir()` in `voice_typer/server/config.py`).
 *   - `electronPidFile()` / `writeElectronPidFile()` / `clearElectronPidFile()`
 *     / `readStaleElectronPid()` — PID-file management for stale-lock
 *     detection.
 *   - `acquireSingleInstanceLock()` — top-level gate that must run
 *     before `app.whenReady()`. On a duplicate launch it calls
 *     `app.exit(0)`; on the primary instance it writes the PID file and
 *     registers `app.on("second-instance", …)` to show + focus the
 *     dashboard window.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { app } from "electron";
import { showMainWindow } from "./windows";

/**
 * Compute the config dir the same way `app.whenReady()` does so the
 * `electron.pid` file lives alongside the `backend.pid` file written by
 * Python.
 *
 * Mirrors `_config_dir()` in `voice_typer/server/config.py`:
 *   1. `VOICE_TYPER_CONFIG_DIR` env var (if set)
 *   2. Legacy `~/.voice-typer` (if it exists — migration path)
 *   3. Platform-appropriate path (`%APPDATA%/voice-typer` on Windows,
 *      `~/Library/Application Support/voice-typer` on macOS,
 *      `$XDG_DATA_HOME/voice-typer` on Linux)
 */
export function computeConfigDir(): string {
	const envOverride = process.env.VOICE_TYPER_CONFIG_DIR;
	if (envOverride) return envOverride;
	const legacy = path.join(os.homedir(), ".voice-typer");
	try {
		if (fs.existsSync(legacy)) return legacy;
	} catch (e) {
		// ignore — fs.existsSync may throw on permission denied or
		// if the homedir is unreachable. Fall through to the
		// platform-appropriate default path below.
		console.warn("[single_instance] legacy config dir probe failed:", e);
	}
	if (process.platform === "win32") {
		return path.join(process.env.APPDATA || os.homedir(), "voice-typer");
	}
	if (process.platform === "darwin") {
		return path.join(
			os.homedir(),
			"Library",
			"Application Support",
			"voice-typer",
		);
	}
	return path.join(
		process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"),
		"voice-typer",
	);
}

export function electronPidFile(): string {
	return path.join(computeConfigDir(), "electron.pid");
}

export function writeElectronPidFile(): void {
	try {
		const dir = path.dirname(electronPidFile());
		fs.mkdirSync(dir, { recursive: true });
		fs.writeFileSync(electronPidFile(), `${process.pid}\n`, {
			encoding: "utf-8",
			flag: "w",
			mode: 0o600,
		});
	} catch (e) {
		// best-effort: PID file is only used for stale-lock detection
		// on the next launch; a missing file just means a future launch
		// can't auto-recover from a hard crash (it will fall back to
		// exiting as a duplicate instance).
		console.warn("[single_instance] writeElectronPidFile failed:", e);
	}
}

export function clearElectronPidFile(): void {
	try {
		if (fs.existsSync(electronPidFile())) fs.unlinkSync(electronPidFile());
	} catch (e) {
		// best-effort: file may already be gone (race with another
		// process) or the FS may be read-only. Non-fatal — a leftover
		// PID file just means the next launch does stale-lock detection.
		console.warn("[single_instance] clearElectronPidFile failed:", e);
	}
}

/**
 * Read the PID file and return the PID if the process it points to is DEAD
 * (stale lock).  Returns null if the file doesn't exist, is unreadable, or
 * the PID is still alive.
 */
export function readStaleElectronPid(): number | null {
	try {
		if (!fs.existsSync(electronPidFile())) return null;
		const content = fs.readFileSync(electronPidFile(), "utf-8").trim();
		if (!content) return null;
		const pid = parseInt(content, 10);
		if (!Number.isFinite(pid) || pid <= 0) return null;
		try {
			// process.kill(pid, 0) throws if the process doesn't exist.
			process.kill(pid, 0);
			return null; // still alive
		} catch {
			return pid; // stale — process is gone
		}
	} catch {
		return null;
	}
}

/**
 * Acquire the single-instance lock with at most one stale-lock retry.
 *
 * MUST run before `app.whenReady()` — the lock is checked at process start.
 *
 * Behaviour:
 *   - If the first `requestSingleInstanceLock()` fails AND the existing
 *     PID is stale (previous instance crashed hard), release the stale
 *     lock and retry once.
 *   - If still no lock OR `VT_FOCUS_ONLY=1`, call `app.exit(0)` to
 *     terminate immediately (the primary instance has already received
 *     or is about to receive the `second-instance` event and will show
 *     itself).
 *   - Otherwise (we got the lock), write our PID file and register
 *     `app.on("second-instance", …)` to show + focus the dashboard.
 *
 * `VT_FOCUS_ONLY` is set by `autostart_launcher._focus_running_app()`
 * as a defensive guard: if this env var is set, this process is a
 * lightweight duplicate that must exit without doing ANY heavy init.
 */
export function acquireSingleInstanceLock(): void {
	let gotTheLock = app.requestSingleInstanceLock();
	if (!gotTheLock) {
		const stalePid = readStaleElectronPid();
		if (stalePid !== null) {
			console.warn(
				`[STARTUP] single-instance lock held by dead PID ${stalePid} — ` +
					"clearing stale PID file and retrying",
			);
			clearElectronPidFile();
			// AC-120: ``app.releaseSingleInstanceLock()`` releases the lock
			// held by THIS process. We never held the lock (the first
			// ``requestSingleInstanceLock()`` returned false), so this call
			// is a no-op on most platforms. On Linux, Electron's single-
			// instance lock is a ``SingletonLock`` symlink in the userData
			// directory; ``releaseSingleInstanceLock`` deletes it. If the
			// dead process's lock file is still on disk, we manually
			// delete it as a defense-in-depth measure so the retry has a
			// clean slate. On Windows, the named mutex is auto-released
			// when the owning process exits (no manual cleanup needed).
			// On macOS, the file lock is released by the OS on process
			// exit (no manual cleanup needed).
			if (process.platform === "linux") {
				try {
					const userDataPath = app.getPath("userData");
					const singletonLock = path.join(userDataPath, "SingletonLock");
					if (fs.existsSync(singletonLock)) {
						fs.unlinkSync(singletonLock);
						console.warn(
							`[single_instance] deleted stale Linux SingletonLock symlink at ${singletonLock}`,
						);
					}
				} catch (e) {
					/* best-effort — the retry below will fail if the lock is still held */
					console.warn(
						"[single_instance] Linux SingletonLock cleanup failed:",
						e,
					);
				}
			}
			try {
				app.releaseSingleInstanceLock();
			} catch (e) {
				/* ignore — Electron may reject if we never held the lock */
				console.warn("[single_instance] releaseSingleInstanceLock failed:", e);
			}
			gotTheLock = app.requestSingleInstanceLock();
			// AC-120: log the result of the retry so operators can see
			// whether the stale-lock recovery succeeded. Previously the
			// result was silently assigned to ``gotTheLock`` and the only
			// observable signal was whether the process exited (duplicate)
			// or continued (primary). A retry that failed but didn't exit
			// (e.g. because VT_FOCUS_ONLY was not set) would silently run
			// as a "ghost" instance with no lock.
			console.warn(
				`[single_instance] retry requestSingleInstanceLock() returned ${gotTheLock}`,
			);
		}
	}
	if (!gotTheLock || process.env.VT_FOCUS_ONLY === "1") {
		// We are the duplicate (or a focus-only probe).  The first instance has
		// already received (or is about to receive) the "second-instance" event
		// and will show itself.
		//
		// Use app.exit(0) instead of app.quit() to guarantee immediate
		// termination — app.quit() allows the event loop to drain (which can
		// fire whenReady and start Python before the process exits), while
		// app.exit(0) terminates without waiting.
		app.exit(0);
	} else {
		// P1-1.4: we got the lock — write our PID so a future launch can
		// detect if we've crashed hard and release the stale lock.
		writeElectronPidFile();
		app.on("second-instance", () => {
			// Another launch attempt happened.  Show + focus the dashboard so it
			// feels like the app "opened."  Create it lazily if autostart started
			// us hidden and the user never opened it yet.
			showMainWindow();
		});
	}
}
