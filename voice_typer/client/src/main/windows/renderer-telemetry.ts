/**
 * Renderer console telemetry forwarding for the dashboard window.
 *
 * Extracted from `main-window.ts`. Owns the main-window wiring of the
 * shared console-message forwarder (renderer console → structured
 * main-process log, gated at INFO+; see `bubble/console-forwarder.ts`
 * for the single shared implementation) and the PII-redacted
 * ERROR-level persistence into `electron-renderer-errors.log`.
 */
import type { BrowserWindow } from "electron";
import { fileTimestamp, RENDERER_CLR, redactPii } from "../logging";
import { attachConsoleForwarder } from "./bubble/console-forwarder";
import { appendRendererError } from "./renderer-error-persistence";

/**
 * Register the renderer-console forwarder on the dashboard window.
 *
 * Delegates the forwarding itself (level gate, tag/color prefix,
 * INFO/WARN/ERROR routing through the structured logger, the same
 * byte format the bubble window gets) to `attachConsoleForwarder`,
 * keeping only the ERROR-persistence sink that is specific to the
 * main window's telemetry:
 *
 * Renderer-error persistence: when level >= 3 (ERROR), also persist
 * the renderer console error to `electron-renderer-errors.log` under
 * the Electron userData dir. Previously the forwarder only re-emitted
 * the message to the main-process terminal (lost when the terminal
 * closed), operators had no way to see renderer crashes post-mortem.
 * The persist call is best-effort: any I/O error is swallowed by
 * `appendRendererError` so logging can never break the renderer
 * console forwarding path.
 *
 * `redactPii` is applied to the persisted line so user-spoken text
 * fragments / API keys / URL credentials in renderer error messages
 * don't land unredacted in `electron-renderer-errors.log`. The
 * forwarded line above (via `log.error(msg)`) already goes through
 * `redactArgsForFile`'s redaction, but `appendRendererError` writes
 * via direct `appendLogLine` and bypasses that, so the redaction is
 * applied explicitly here. `cleanConsoleMsg` runs once inside the
 * shared forwarder (strips printf specifiers) and hands the cleaned
 * text to this sink, so the clean/redact chain runs a single pass
 * per ERROR line.
 */
export function registerRendererTelemetry(win: BrowserWindow): void {
	attachConsoleForwarder(win, {
		tag: "[MAIN renderer]",
		colorPrefix: RENDERER_CLR,
		onError: ({ message, sourceId, lineNumber }) => {
			const line = `${fileTimestamp()}  ERROR  [renderer-error] ${redactPii(message)} (${sourceId}:${lineNumber})\n`;
			appendRendererError(line);
		},
	});
}
