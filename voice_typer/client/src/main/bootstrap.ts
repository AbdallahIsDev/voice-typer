/**
 * Runtime setup executed inside `app.whenReady()`.
 *
 * Extracted from `index.ts` (REF-2). `bootstrapRuntime()` performs:
 *   1. SEC-029 per-session nonce generation (stored in `state.sessionNonce`).
 *   2. NEW-PRIV-010 userData override so Electron and Python share one
 *      config directory.
 *   3. SEC-012 / NEW-SEC-002 Content-Security-Policy headers (HTTP).
 *   4. SEC-021 uncaughtException / unhandledRejection handlers with a
 *      crash log + 5-error circuit breaker.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { app, dialog, session } from "electron";
import { APP_NAME } from "./branding";
import { computeConfigDir } from "./single_instance";
import { state } from "./state";

/**
 * SEC-029: generate a per-session nonce. Use crypto.randomUUID()
 * when available (Node 14.17+/Electron 12+), fall back to a
 * timestamp+random string. Stored in `state.sessionNonce` and tagged
 * onto every python-event so the renderer can reject replayed frames.
 */
function generateSessionNonce(): void {
	try {
		const cryptoMod = require("node:crypto") as { randomUUID?: () => string };
		state.sessionNonce =
			cryptoMod.randomUUID?.() ||
			`${Date.now()}-${Math.random().toString(36).slice(2)}`;
	} catch {
		state.sessionNonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
	}
}

/**
 * NEW-PRIV-010: unify Electron's userData directory with the Python
 * backend's config directory.  Previously these were two separate
 * directories:
 *   - Python: ~/.voice-typer (legacy) or platform-appropriate path
 *     (see voice_typer/server/config.py:_config_dir())
 *   - Electron: app.getPath('userData') which defaults to
 *     %APPDATA%/voice-typer-desktop (based on package.json "name")
 *
 * This caused user confusion ("where is my data?") and made GDPR
 * right-to-portability harder (two locations to scrub).  We now
 * explicitly set Electron's userData to match the Python config dir
 * so both sides read/write the same location.
 */
function setupUserData(): void {
	try {
		const configDir = computeConfigDir();
		// Ensure the directory exists before Electron tries to use it.
		try {
			fs.mkdirSync(configDir, { recursive: true });
		} catch {
			/* ignore */
		}
		app.setPath("userData", configDir);
		console.warn(`[MAIN] userData set to: ${configDir}`);
	} catch (e) {
		console.warn("[MAIN] Failed to override userData path:", e);
		// Non-fatal — Electron falls back to its default userData location.
	}
}

/**
 * SEC-012 / NEW-SEC-002: Content Security Policy (HTTP headers).
 *
 * CSP is also set via <meta> tags in index.html and bubble.html for
 * production file:// loads, but certain directives (frame-ancestors,
 * form-action) are only honored when delivered as actual HTTP headers.
 * Setting them here via Electron's onHeadersReceived ensures they're
 * properly enforced in dev mode (http://localhost:5173) and in production.
 *
 * In dev mode (app.isPackaged === false), Vite's dev server injects
 * inline scripts (React Refresh preamble + HMR client) and uses eval
 * for sourcemaps.  We add 'unsafe-inline' and 'unsafe-eval' only in
 * dev mode to allow these.  Production builds have no inline scripts
 * or eval, so the strict 'self' directive applies and inline event
 * handlers (onclick="...") remain blocked.
 */
function setupCsp(): void {
	const CSP = [
		"default-src 'self'",
		`script-src 'self'${app.isPackaged === false ? " 'unsafe-eval' 'unsafe-inline'" : ""}`,
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data:",
		"font-src 'self' data:",
		"media-src 'self' data:",
		"connect-src 'self' https://api.github.com",
		"frame-ancestors 'none'",
		"form-action 'none'",
		"base-uri 'self'",
	].join("; ");

	session.defaultSession.webRequest.onHeadersReceived(
		(
			details: Electron.OnHeadersReceivedListenerDetails,
			callback: (headers: Electron.HeadersReceivedResponse) => void,
		) => {
			callback({
				responseHeaders: {
					...details.responseHeaders,
					"Content-Security-Policy": [CSP],
				},
			});
		},
	);
}

/**
 * SEC-021: previously the uncaughtException handler just console.error'd
 * and continued, leaving the process in a half-broken state (locked
 * mutex, half-written config). We now log to file, count occurrences,
 * and exit non-zero after N consecutive errors so the user sees the
 * crash instead of a silent zombie.
 */
let uncaughtCount = 0;
const MAX_UNCAUGHT = 5;

function setupErrorHandlers(): void {
	const crashLogPath = path.join(
		app?.getPath("userData") ?? process.cwd(),
		"electron-crashes.log",
	);
	const logCrash = (kind: string, err: unknown) => {
		try {
			const ts = new Date().toISOString();
			const line = `${ts} [${kind}] ${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`;
			fs.appendFileSync(crashLogPath, line, { encoding: "utf-8" });
		} catch {
			// Logging is best-effort.
		}
	};
	process.on("uncaughtException", (err) => {
		console.error("[VT] uncaughtException:", err);
		logCrash("uncaughtException", err);
		uncaughtCount++;
		if (uncaughtCount >= MAX_UNCAUGHT) {
			console.error(
				`[VT] ${uncaughtCount} uncaught exceptions — exiting to avoid zombie state`,
			);
			try {
				dialog.showErrorBox(
					`${APP_NAME} — Critical Error`,
					`The app encountered ${uncaughtCount} uncaught exceptions and will exit.\n` +
						`Crash log: ${crashLogPath}\n` +
						`Please restart ${APP_NAME}.`,
				);
			} catch {
				// dialog may not be available in headless mode
			}
			process.exit(1);
		}
	});
	process.on("unhandledRejection", (err) => {
		console.error("[VT] unhandledRejection:", err);
		logCrash("unhandledRejection", err);
	});
}

/**
 * Run all the one-shot runtime setup steps. Called once from
 * `app.whenReady()` in `index.ts`.
 */
export function bootstrapRuntime(): void {
	generateSessionNonce();
	setupUserData();
	setupCsp();
	setupErrorHandlers();
}
