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
 * FR-36: the rotation logic was previously inlined here (a `statSync`
 * + `renameSync` + `appendFileSync` sequence). This bypassed the
 * XV-154 file-size cache (`fileSizeCache.ts`) — every INFO write did
 * a synchronous `fs.statSync` on the main process event loop, the
 * exact perf bug XV-154 was designed to eliminate for
 * `electron-main.log`. On a busy session (30 Hz bubble events) under
 * `VOICE_TYPER_ELECTRON_INFO_LOG=1`, this was 30 `statSync` calls/sec
 * on the main thread. Replacing the inline rotation with a call to
 * `appendLogLine(p, line, 1024 * 1024)` routes the writes through the
 * XV-154 cache and the shared rotation primitive. The `mode: 0o600`
 * parity (FR-9) is also picked up for free via `appendLogLine`.
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
		// FR-36: delegate to the shared `appendLogLine` helper so the
		// XV-154 file-size cache eliminates the per-write `statSync`
		// and the rotation logic stays in one place. The 1 MiB cap
		// matches the prior inline rotation's threshold.
		appendLogLine(p, line, 1024 * 1024);
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
 * PI-6: GDPR Art. 17 erasure for the Electron main-process log files.
 *
 * The Python `service.delete_all_personal_data()` walks
 * `_GDPR_PERSONAL_FILES` / `_GDPR_PERSONAL_GLOBS` against the Python
 * backend's `_config_dir()` root only — it cannot reach Electron's
 * `app.getPath("userData")` (a DIFFERENT directory on disk). Per
 * XZ-LOG-03 the Electron loggers have no PII redaction, so
 * dictated-text fragments may be present in these files.
 *
 * This helper unlinks every Electron-side log file the app writes:
 *
 *   * `<userData>/electron-main.log` (+ rotated `.1`..`.5` produced by
 *     `rotateIfNeeded` in `./rotation.ts`).
 *   * `<userData>/electron-renderer-errors.log` (no rotation — single
 *     file, overwritten by `appendLogLine` until the 5 MiB cap is
 *     reached, then truncated — but still unlinked here for symmetry).
 *
 * Returns an object the caller can merge into the Python
 * `delete_all_personal_data` response shape (erased / failed lists).
 *
 * Best-effort: per-file `OSError` (file locked by another process on
 * Windows, EBUSY on a rare mount point) is surfaced in `failed` rather
 * than aborting — matching the Python side's `for name in
 * _GDPR_PERSONAL_FILES: try path.unlink() except ...` discipline.
 *
 * NOT WIRED to an IPC handler yet — see `docs/privacy/gdpr-delete.md`
 * "Electron logs gap (PI-6 — known limitation)". The intended wiring
 * is a `deleteAllPersonalData` IPC handler that calls this helper AND
 * proxies to the Python `delete_all_personal_data` command, then
 * merges both responses for the renderer.
 */
export function deleteElectronPersonalDataLogs(): {
	erased: string[];
	failed: Record<string, string>;
} {
	const erased: string[] = [];
	const failed: Record<string, string> = {};
	const userData = app.getPath("userData");
	// Glob both the active log file and rotated backups. The rotation
	// module writes `electron-main.log.1`..`electron-main.log.5` (5 MiB
	// cap × 5 backups). `electron-renderer-errors.log` has no rotation
	// (single file), but the glob `electron-renderer-errors.log*` also
	// matches the bare name so a single loop covers both.
	const candidates = [
		...fs
			.readdirSync(userData)
			.filter(
				(name) =>
					name === "electron-main.log" ||
					name.startsWith("electron-main.log.") ||
					name === "electron-renderer-errors.log" ||
					name.startsWith("electron-renderer-errors.log."),
			)
			.map((name) => path.join(userData, name)),
	];
	for (const p of candidates) {
		try {
			fs.unlinkSync(p);
			erased.push(p);
		} catch (err) {
			// ENOENT is a no-op (file already gone — fresh install, or
			// a previous GDPR delete). Other errors surface in `failed`
			// so the renderer can tell the user to delete the file
			// manually (matches the Python side's "no silent swallows"
			// rule).
			if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
				failed[p] = `${(err as Error).name}: ${(err as Error).message}`;
			}
		}
	}
	return { erased, failed };
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
