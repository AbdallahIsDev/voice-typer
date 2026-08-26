/**
 * Renderer console telemetry forwarding for the dashboard window.
 *
 * Extracted from `main-window.ts`. Owns the `console-message` forwarder
 * (renderer console → structured main-process log, gated at INFO+) and
 * the PII-redacted ERROR-level persistence into
 * `electron-renderer-errors.log`.
 */
import type { BrowserWindow } from "electron";
import {
	cleanConsoleMsg,
	fileTimestamp,
	log,
	RENDERER_CLR,
	RESET,
	redactPii,
} from "../logging";
import { appendRendererError } from "./renderer-error-persistence";

/**
 * Register the renderer-console forwarder on the dashboard window.
 *
 * CONSOLE-FIX: Electron 30+ deprecated the multi-argument
 * console-message signature `(_e, level, message, line, source)`.
 * The new signature is a single Event object with properties:
 *   e.level, e.message, e.lineNumber, e.sourceId
 * The old signature emitted a deprecation warning on every app start.
 *
 * Renderer-error persistence: when level >= 3 (ERROR), also persist the renderer
 * console error to `electron-renderer-errors.log` under the
 * Electron userData dir. Previously the handler only re-emitted
 * the message to the main-process terminal (lost when the
 * terminal closed) — operators had no way to see renderer
 * crashes post-mortem. The persist call is best-effort: any I/O
 * error is swallowed by `appendRendererError` so logging can
 * never break the renderer console forwarding path.
 *
 * Forwarder-gate widening: lower the forwarder gate from
 * `level >= 2` (WARN and above only) to `level >= 1` so INFO-
 * level renderer telemetry (e.g. lifecycle logs from the
 * renderer) reaches the main process log too. VERBOSE (level
 * 0) is still dropped — it's too noisy for the main log.
 * Structured logger routing: route through the structured logger so WARN/ERROR
 * lines also land in electron-runtime.log.
 */
export function registerRendererTelemetry(win: BrowserWindow): void {
	win.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 1) {
			const tag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			const msg = `${RENDERER_CLR}[MAIN renderer] ${tag}${RESET} ${cleanConsoleMsg(e.message)} (${e.sourceId}:${e.lineNumber})`;
			if (level >= 3) log.error(msg);
			else if (level === 2) log.warn(msg);
			else log.info(msg);
		}
		if (level >= 3) {
			// Renderer-error persistence: ERROR-level renderer console output is
			// almost always a real bug (uncaught exception,
			// failed prop type, broken invariant). Persist it
			// to its own log file so support staff can grep
			// renderer crashes without fishing through
			// DevTools or the noisy `electron-main.log`.
			//
			//apply `redactPii` to the persisted line
			// so user-spoken text fragments / API keys / URL
			// credentials in renderer error messages don't
			// land unredacted in `electron-renderer-errors.log`.
			// The stdout path above (via `log.error(msg)`)
			// already goes through `redactArgsForFile`'s
			// redaction, but `appendRendererError` writes via
			// direct `appendLogLine` and bypasses that — so the
			// redaction must be applied explicitly here.
			// `cleanConsoleMsg` runs first (strips printf
			// specifiers), then `redactPii` runs on the
			// cleaned text (idempotent on already-redacted
			// text so the double-chain is safe).
			const cleaned = cleanConsoleMsg(e.message);
			const line = `${fileTimestamp()}  ERROR  [renderer-error] ${redactPii(
				cleaned,
			)} (${e.sourceId}:${e.lineNumber})\n`;
			appendRendererError(line);
		}
	});
}
