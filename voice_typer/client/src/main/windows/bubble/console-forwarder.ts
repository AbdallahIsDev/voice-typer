/**
 * Shared renderer `console-message` forwarder.
 *
 * Both the bubble window (`lifecycle.ts`) and the main window path
 * (`windows/renderer-telemetry.ts`) install a `webContents.on(
 * "console-message", …)` handler that routes renderer console output
 * through the structured main-process logger. The handlers were
 * near-duplicates; this module factors out the shared level-routing
 * logic so every window creation reuses a single implementation.
 *
 * CONSOLE-FIX: Electron 30+ deprecated the multi-argument
 * `console-message` signature `(_e, level, message, line, source)`.
 * The new signature is a single Event object with properties:
 * `e.level`, `e.message`, `e.lineNumber`, `e.sourceId`. The helper
 * reads the new shape.
 *
 * Forwarder gate: `level >= 1` (INFO and above) instead of the old
 * `level >= 2`, so INFO-level renderer telemetry reaches the main
 * process log too. VERBOSE (level 0) is still dropped, too noisy
 * for the main log. Routing goes through the structured logger so
 * WARN/ERROR lines also land in electron-runtime.log.
 *
 * ERROR-level persistence is opt-in via `ConsoleForwarderOptions.onError`:
 * the main window path (`renderer-telemetry.ts`) passes a sink that
 * persists ERROR lines to `electron-renderer-errors.log`. The sink
 * receives the already-cleaned message so callers never re-run
 * `cleanConsoleMsg` (it ran twice per ERROR line before the
 * consolidation). The bubble deliberately does NOT pass `onError` —
 * its renderer errors aren't interesting enough to warrant the
 * separate log file, and wiring it would couple this helper to the
 * persistence module.
 */
import type { BrowserWindow } from "electron";
import { cleanConsoleMsg, log, RESET } from "../../logging";

/**
 * Detail handed to `ConsoleForwarderOptions.onError` for an
 * ERROR-channel console event. `message` is the ALREADY-CLEANED text
 * (`cleanConsoleMsg` output, printf specifiers and `%c` style
 * prefixes stripped) so the sink does not re-run the cleaning pass.
 */
export interface ConsoleForwarderErrorDetail {
	/** printf-specifier-stripped message (`cleanConsoleMsg` output). */
	message: string;
	/** Renderer source URL of the console message. */
	sourceId: string;
	/** Renderer line number of the console message. */
	lineNumber: number;
}

export interface ConsoleForwarderOptions {
	/**
	 * Bracketed prefix label inserted between the color code and the
	 * level tag, e.g. `"[BUBBLE] renderer"` or `"[MAIN renderer]"`.
	 * Mirrors the per-window tag the legacy inline handlers hard-coded.
	 */
	tag: string;
	/**
	 * ANSI color escape sequence (e.g. `BUBBLE_CLR`, `RENDERER_CLR`)
	 * prepended to the message so the bubble's lines are visually
	 * distinct from the main window's in the terminal.
	 */
	colorPrefix: string;
	/**
	 * Optional ERROR-level sink. Invoked (exactly once, AFTER the
	 * forwarded `log.error` call) for every console event at
	 * `level >= 3`, including unknown high levels tagged `LOG`.
	 * Receives the already-cleaned message plus its location so
	 * persistence paths (e.g. `renderer-telemetry.ts`'s PII-redacted
	 * `electron-renderer-errors.log` append) never re-run
	 * `cleanConsoleMsg`. The sink must be best-effort and must not
	 * throw, logging must never break the forwarding path.
	 */
	onError?: (detail: ConsoleForwarderErrorDetail) => void;
}

/**
 * Attach a `console-message` handler to `win.webContents` that routes
 * renderer console output through the structured main-process logger.
 *
 * Level routing (preserved exactly from the legacy inline handlers):
 *   - level 0 (VRB)   → dropped (too noisy)
 *   - level 1 (INFO)  → `log.info`
 *   - level 2 (WARN)  → `log.warn`
 *   - level 3 (ERROR) → `log.error`
 *   - unknown level (≥ 4) → `log.error` (tagged `LOG`, gated by the
 *     `level >= 3` branch)
 */
export function attachConsoleForwarder(
	win: BrowserWindow,
	options: ConsoleForwarderOptions,
): void {
	const { tag, colorPrefix, onError } = options;
	win.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 1) {
			const levelTag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			const cleaned = cleanConsoleMsg(e.message);
			const msg = `${colorPrefix}${tag} ${levelTag}${RESET} ${cleaned} (${e.sourceId}:${e.lineNumber})`;
			if (level >= 3) log.error(msg);
			else if (level === 2) log.warn(msg);
			else log.info(msg);
			if (level >= 3 && onError) {
				// Hand the already-cleaned text to the sink, ERROR
				// persistence must not re-run cleanConsoleMsg (it ran
				// twice per ERROR line before this hook existed).
				onError({
					message: cleaned,
					sourceId: e.sourceId,
					lineNumber: e.lineNumber,
				});
			}
		}
	});
}
