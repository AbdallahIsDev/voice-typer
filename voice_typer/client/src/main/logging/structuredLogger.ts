/**
 * Message-first structured logger for the Electron main process.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). Owns:
 *
 *   - `logger` — message-first API:
 *     `logger.info("TCP connected", { port: 7001 })`. Writes to
 *     `<userData>/electron-main.log` with 5 MiB rotation. DEBUG is
 *     dev-only (gated by `!app.isPackaged`). INFO is dev-only in file
 *     output by default (production writes only WARN/ERROR to file).
 *     Set `VOICE_TYPER_ELECTRON_INFO_LOG=1` to opt in to production INFO
 *     persistence (routes to `electron-lifecycle.log`).
 *   - `mainLogPath()` — resolves `<userData>/electron-main.log`.
 *   - `rendererErrorsLogPath()` — resolves
 *     `<userData>/electron-renderer-errors.log` (consumed by the
 *     main-window `console-message` handler via `window-handlers.ts`).
 *   - `lifecycleLogPath()` + `appendLifecycleLine()` — the opt-in
 *     INFO persistence target (1 MiB × 1 backup).
 *   - `PERSIST_INFO` — the env-var-gated flag consumed by both this
 *     module's `logger.info` AND by `printfLogger.ts`'s `log.info`
 *     (the printf-style logger mirrors the opt-in here).
 *   - `formatLine` — local helper that renders a file-friendly line
 *     (ISO-8601 timestamp + level tag + JSON-stringified args).
 *
 * Imports: `fs`, `path`, Electron's `app`, and `appendLogLine` from
 * `./rotation`.
 */
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";

import { appendLogLine } from "./rotation";

// Opt-in INFO persistence for support/enterprise deployments.
// Set VOICE_TYPER_ELECTRON_INFO_LOG=1 to route INFO logs to
// `electron-lifecycle.log` (1 MiB × 1 backup). When unset, INFO
// persistence is unchanged (dev-only file write, production no-op).
//
// Production Electron builds have no terminal attached, so `console.info`
// is a no-op. Without this opt-in, lifecycle events (TCP connect, Python
// sidecar spawned, bubble shown, window created) leave ZERO durable trace
// in packaged builds — making support triage impossible. The opt-in
// default-off preserves the original disk-space-conservative behavior
// for the 99% case while letting enterprise/support deployments flip a
// single env var to get the lifecycle trail.
//
// Exported (not in the public barrel) so `printfLogger.ts` can mirror
// the opt-in on its `log.info` without re-reading `process.env`.
export const PERSIST_INFO = process.env.VOICE_TYPER_ELECTRON_INFO_LOG === "1";

/**
 * Resolve the path to `electron-lifecycle.log` under the Electron
 * userData dir. Kept separate from `mainLogPath` so the opt-in
 * INFO stream never competes with the WARN/ERROR stream for the 5 MiB
 * `electron-main.log` rotation window.
 */
export function lifecycleLogPath(): string {
	return path.join(app.getPath("userData"), "electron-lifecycle.log");
}

/**
 * Append a single INFO line to `electron-lifecycle.log` with a
 * 1 MiB rotation (single `.1` backup). Best-effort — any I/O error is
 * swallowed so logging can never crash the caller's code path.
 *
 * Mirrors the rotate-then-append pattern of `appendLogLine` but
 * uses a tighter 1 MiB cap (vs the 5 MiB cap on `electron-main.log`)
 * because the INFO stream is higher-volume and would otherwise push
 * WARN/ERROR context out of the smaller log too quickly. Total disk
 * usage is bounded at ~2 MiB worst case (active file + `.1` backup).
 *
 * Exported (not in the public barrel) so `printfLogger.ts`'s `log.info`
 * can route its opt-in INFO persistence through the same writer.
 */
export function appendLifecycleLine(
	level: string,
	msg: string,
	args: unknown[],
): void {
	try {
		const tsStr = new Date().toISOString();
		const formatted =
			args.length > 0
				? `${msg} ${args
						.map((a) => {
							try {
								return JSON.stringify(a);
							} catch {
								return String(a);
							}
						})
						.join(" ")}`
				: msg;
		const line = `${tsStr} [${level.toUpperCase()}] ${formatted}\n`;
		const p = lifecycleLogPath();
		// 1 MiB rotation × 1 backup (same strategy as rotateIfNeeded
		// but inlined to keep this helper self-contained and avoid the
		// XV-154 file-size cache — the INFO stream is lower priority
		// than WARN/ERROR and the extra stat on each write is
		// acceptable for an opt-in diagnostic path).
		try {
			const stat = fs.statSync(p);
			if (stat.size > 1024 * 1024) {
				try {
					fs.renameSync(p, `${p}.1`);
				} catch {
					/* ignore — best-effort rotation */
				}
			}
		} catch {
			/* file doesn't exist yet — fine, append will create it */
		}
		fs.appendFileSync(p, line, { flag: "a", mode: 0o600 });
	} catch {
		// Never let logging crash the app.
	}
}

type Level = "debug" | "info" | "warn" | "error";

/**
 * Format a log line for the file. Always ends with `\n` so `tail -f`
 * shows lines as they're written.
 */
function formatLine(level: Level, msg: string, args: unknown[]): string {
	const tsStr = new Date().toISOString();
	const formatted =
		args.length > 0
			? `${msg} ${args
					.map((a) => {
						try {
							return JSON.stringify(a);
						} catch {
							return String(a);
						}
					})
					.join(" ")}`
			: msg;
	return `${tsStr} [${level.toUpperCase()}] ${formatted}\n`;
}

/**
 * Resolve the path to `electron-main.log` under the Electron userData dir.
 *
 * Originally module-private in `main/logging.ts` ("verified zero external
 * importers — only used internally by `logger.warn` / `logger.error` /
 * `logger.debug` / `logger.info`"). Exported from this split module so
 * the barrel can re-export it; external consumers may now read it
 * directly, but no behavior change is implied.
 */
export function mainLogPath(): string {
	return path.join(app.getPath("userData"), "electron-main.log");
}

/**
 * Resolve the path to `electron-renderer-errors.log` under the Electron
 * userData dir. The main-window `console-message` handler
 * appends level>=3 (ERROR) renderer messages to this file so support
 * staff can see renderer crashes without fishing through DevTools.
 */
export function rendererErrorsLogPath(): string {
	return path.join(app.getPath("userData"), "electron-renderer-errors.log");
}

/**
 * The structured main-process logger. Mirrors its calls to the
 * matching `console.*` method (so dev-mode terminal output is unchanged)
 * AND appends a structured line to `electron-main.log`.
 *
 * In production, `debug` and `info` are NOT written to the file (only
 * to the terminal, which is closed in packaged builds) to avoid 5 MB
 * of DEBUG noise per session. `warn` and `error` always go to the file.
 *
 * Usage:
 *   logger.info("TCP connected", { port: 7001 });
 *   logger.warn("model:import-dialog failed", { error: err.message });
 *   logger.error("render-process-gone", details);
 */
export const logger: {
	debug: (msg: string, ...args: unknown[]) => void;
	info: (msg: string, ...args: unknown[]) => void;
	warn: (msg: string, ...args: unknown[]) => void;
	error: (msg: string, ...args: unknown[]) => void;
} = {
	debug(msg: string, ...args: unknown[]): void {
		// Debug is dev-only — both terminal and file are gated by
		// `!app.isPackaged` so production never writes DEBUG noise.
		if (!app.isPackaged) {
			console.debug(msg, ...args);
			appendLogLine(mainLogPath(), formatLine("debug", msg, args));
		}
	},
	info(msg: string, ...args: unknown[]): void {
		console.info(msg, ...args);
		if (!app.isPackaged) {
			// Dev: persist INFO so the dev can grep the file.
			appendLogLine(mainLogPath(), formatLine("info", msg, args));
		}
		// Production: INFO is too chatty for the rotating file —
		// it would push WARN/ERROR out of the 5 MB window too
		// fast. Skip the file write; the console call above is a
		// no-op in packaged builds (no terminal attached).
		//
		// Opt-in INFO persistence. When
		// `VOICE_TYPER_ELECTRON_INFO_LOG=1` is set (support /
		// enterprise deployments), route INFO through a dedicated
		// `electron-lifecycle.log` so support staff can correlate
		// startup milestones (TCP connect, Python spawned, window
		// created, bubble shown) with crash logs. Default-off
		// preserves the disk-space-conservative behavior for the
		// 99% case.
		if (PERSIST_INFO) {
			appendLifecycleLine("info", msg, args);
		}
	},
	warn(msg: string, ...args: unknown[]): void {
		console.warn(msg, ...args);
		appendLogLine(mainLogPath(), formatLine("warn", msg, args));
	},
	error(msg: string, ...args: unknown[]): void {
		console.error(msg, ...args);
		appendLogLine(mainLogPath(), formatLine("error", msg, args));
	},
};
