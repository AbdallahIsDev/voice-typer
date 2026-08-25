/**
 * Electron userData override.
 *
 * Split out of `bootstrap.ts`. Unifies Electron's userData directory
 * with the Python backend's config directory (step 2 of the bootstrap
 * sequence).
 */
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import { log } from "../logging";
import { computeConfigDir } from "../single_instance";

/**
 * Dedupes the ``[MAIN] userData set to: ...`` lifecycle line across
 * `setupUserData()`'s two intentional call sites (`index.ts` module-load
 * + `bootstrapRuntime()` inside `app.whenReady()`). Both calls re-set the
 * SAME path, so the line must appear only once per process — otherwise
 * `electron-stdout.log` shows a duplicate pair on every boot. Module-level
 * state is safe here: tests use `vi.resetModules()` so each test gets a
 * fresh module instance.
 */
let _loggedUserDataPath: string | undefined;

/**
 * : unify Electron's userData directory with the Python
 * backend's config directory, in a dedicated ``electron-profile``
 * subfolder.  Previously these were two separate directories:
 *   - Python: ~/.voice-typer (legacy) or platform-appropriate path
 *     (see voice_typer/server/config.py:_config_dir())
 *   - Electron: app.getPath('userData') which defaults to
 *     %APPDATA%/voice-typer-desktop (based on package.json "name")
 *
 * This caused user confusion ("where is my data?") and made GDPR
 * right-to-portability harder (two locations to scrub).  We now
 * explicitly set Electron's userData to the Python config dir's
 * ``electron-profile`` subfolder so both sides share one data root
 * (uninstall / factory-reset still wipes everything) while the
 * Chromium browser-profile noise stays out of the data-dir root.
 */
export function setupUserData(): void {
	try {
		const configDir = computeConfigDir();
		// Electron's Chromium profile (caches, Local Storage, Network
		// state, Crashpad, ...) lives in a subfolder so the data-dir
		// root stays a readable mix of user data + app logs.
		const electronProfileDir = path.join(configDir, "electron-profile");
		// Ensure the directory exists before Electron tries to use it.
		try {
			fs.mkdirSync(configDir, { recursive: true });
		} catch (e) {
			// Previously this catch was silent (`/* ignore */`),
			// which masked three distinct failure modes:
			//   1. Permissions error on a shared / multi-user install
			//      (e.g. /opt owned by root).
			//   2. Disk full / read-only filesystem (Live USB).
			//   3. Path-too-long on Windows (MAX_PATH=260).
			// All three surfaced downstream as a cryptic
			// `app.setPath("userData", ...)` failure with no
			// upstream context. Logging here gives operators a
			// breadcrumb pointing at the real cause. The mkdir
			// is still best-effort — Electron falls back to its
			// default userData if `app.setPath` is never called.
			log.warn("[MAIN] mkdirSync for userData failed:", e);
		}
		app.setPath("userData", electronProfileDir);
		//route through the structured `log` logger so
		// the lifecycle message persists to `electron-runtime.log` instead
		// of being lost in packaged builds where `console.warn` has no
		// terminal attached. Log once per process — the second call
		// (idempotent re-set of the same path from `bootstrapRuntime()`)
		// must not duplicate the line.
		if (_loggedUserDataPath !== electronProfileDir) {
			_loggedUserDataPath = electronProfileDir;
			log.info(`[MAIN] userData set to: ${electronProfileDir}`);
		}
	} catch (e) {
		log.warn("[MAIN] Failed to override userData path:", e);
		// Non-fatal — Electron falls back to its default userData location.
	}
}
