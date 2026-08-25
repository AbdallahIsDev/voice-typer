/**
 * Renderer crash/failure recovery for the dashboard window.
 *
 * Extracted from `main-window.ts`. Owns the `did-fail-load` single-retry
 * reload, the `render-process-gone` crash-storm-glued reload, and the
 * `preload-error` localized dialog + quit path.
 */
import { app, type BrowserWindow, dialog } from "electron";
// `APP_NAME` is interpolated into the preload-error dialog body
// (the existing locale keys don't fit a packaging-bug message; the body
// is English-only — see the preload-error handler for rationale).
import { APP_NAME } from "../branding";
import { RENDER_RELOAD_BACKOFF_MS } from "../constants";
// `mainT` provides the localized title for the preload-error
// dialog (the body is hardcoded English — see the preload-error handler
// for rationale; the existing `dialog.criticalError.title` key is
// reused because it's already translated in all 8 locales).
import { mainT } from "../i18n";
import { log } from "../logging";
import { state } from "../state";
import { recordMainWindowRenderCrash } from "./crash-storm";

/**
 * Register the did-fail-load / render-process-gone / preload-error
 * handlers on the dashboard window.
 *
 * Render-process-gone recovery: main window lacked render-process-gone recovery (the
 * bubble window already had all three handlers — see
 * bubble-window.ts:127-159). Without these, a main-renderer
 * crash left the user with a blank/frozen dashboard while the
 * tray icon + Python backend kept running; "Open app" from the
 * tray showed the same dead window.
 */
export function registerRendererRecovery(win: BrowserWindow): void {
	// `did-fail-load` fires when the renderer fails to load its
	// initial HTML (e.g. the bundled index.html is missing or the
	// dev server returned 500). Logging the error code + URL lets
	// support staff diagnose packaging / dev-server issues.
	//
	// previously this handler only logged.  A transient
	// dev-server 500 or a one-shot file:// race left the dashboard
	// blank with no recovery — the user had to find the tray icon
	// and Quit + relaunch manually.  Mirroring the
	// `render-process-gone` pattern below, we now schedule a
	// single 2s-backoff `reload()` so the user gets a second
	// chance.  The retry is capped at 1 to avoid reload loops on a
	// genuinely broken packaging job (missing index.html in the
	// asar — the reload would just fail-load again forever).
	let didFailLoadRetried = false;
	win.webContents.on("did-fail-load", (_e, code, desc, url) => {
		log.error("[MAIN] did-fail-load", { code, desc, url });
		if (didFailLoadRetried) {
			log.warn(
				"[MAIN] did-fail-load retry already attempted — not retrying again (avoid loop)",
			);
			return;
		}
		didFailLoadRetried = true;
		setTimeout(() => {
			try {
				if (state.mainWindow && !state.mainWindow.isDestroyed()) {
					log.warn(
						"[MAIN] reloading after did-fail-load (2s backoff, single retry)",
					);
					state.mainWindow.reload();
				}
			} catch (err) {
				log.error("[MAIN] failed to reload after did-fail-load", {
					error: (err as Error).message,
				});
			}
		}, RENDER_RELOAD_BACKOFF_MS);
	});

	// `render-process-gone` fires when the renderer process crashes
	// (GPU process OOM, native module segfault, v8 heap exhaustion).
	// Without a reload, the BrowserWindow stays alive with a blank
	// webContents — the user sees a frozen window with no way to
	// recover short of quitting via the tray. We reload the window
	// so the user gets a fresh renderer (the Python backend keeps
	// running, so session state is preserved on the backend side).
	win.webContents.on("render-process-gone", (_e, details) => {
		log.error("[MAIN] render-process-gone", details);
		// Crash-storm detection: sliding-window crash storm detection.
		const inStorm = recordMainWindowRenderCrash();
		if (inStorm) {
			try {
				dialog.showErrorBox(
					mainT("dialog.crashLoop.title", { appName: APP_NAME }),
					mainT("dialog.crashLoop.mainBody", { appName: APP_NAME }),
				);
			} catch {
				// dialog may not be available in headless mode.
			}
			return;
		}
		// Crash-storm backoff: 2s backoff before reload to avoid CPU-bound crash loops.
		setTimeout(() => {
			try {
				if (state.mainWindow && !state.mainWindow.isDestroyed()) {
					log.warn("[MAIN] reloading after render-process-gone (2s backoff)");
					state.mainWindow.reload();
				}
			} catch (err) {
				log.error("[MAIN] failed to reload after render-process-gone", {
					error: (err as Error).message,
				});
			}
		}, RENDER_RELOAD_BACKOFF_MS);
	});

	// `preload-error` fires when the preload script throws at module
	// eval time. This is almost always a packaging bug (preload path
	// mismatch, missing dependency). Logging the file + error makes
	// the root cause obvious instead of presenting as a blank window.
	//
	// previously this handler only logged — the user was left
	// with a blank dashboard and no indication that the app had
	// failed to start. Preload errors are always packaging bugs
	// (a missing/incorrect preload bundle in the asar); retrying
	// won't help because the same packaging defect will reproduce
	// on every reload. We now show a localized error dialog and
	// quit the app so the user gets a clear "please reinstall"
	// message instead of a frozen window.
	//
	// The title uses the existing `dialog.criticalError.title` key
	// (already translated in all 8 locales — see
	// `src/main/i18n/locales/*.json`). The body uses the dedicated
	// `dialog.preloadError.body` key, also translated in all 8
	// locales, with `{appName}` and `{file}` placeholder tokens
	// substituted at runtime via `mainT`.
	win.webContents.on("preload-error", (_e, file, err) => {
		log.error(`[MAIN] preload-error in ${file}`, err);
		try {
			dialog.showErrorBox(
				mainT("dialog.criticalError.title", { appName: APP_NAME }),
				mainT("dialog.preloadError.body", { appName: APP_NAME, file }),
			);
		} catch {
			// dialog may not be available in headless mode.
		}
		try {
			app.quit();
		} catch (e) {
			log.error("[MAIN] app.quit() failed after preload-error:", e);
			// Last-resort backstop: if app.quit() itself threw,
			// force-exit so we don't strand the user with a
			// half-started app.
			process.exit(1);
		}
	});
}
