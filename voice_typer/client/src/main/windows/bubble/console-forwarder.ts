/**
 * Shared renderer `console-message` forwarder (DR-3 sub-finding 1-B-10).
 *
 * Both the bubble window (`lifecycle.ts`) and the main window
 * (`main-window.ts`) install a `webContents.on("console-message", …)`
 * handler that routes renderer console output through the structured
 * main-process logger. The two handlers were near-duplicates; this
 * module factors out the shared level-routing logic so future window
 * creations (and the eventual main-window.ts migration) reuse a
 * single implementation.
 *
 * CONSOLE-FIX: Electron 30+ deprecated the multi-argument
 * `console-message` signature `(_e, level, message, line, source)`.
 * The new signature is a single Event object with properties:
 * `e.level`, `e.message`, `e.lineNumber`, `e.sourceId`. The helper
 * reads the new shape.
 *
 * PVT-G5-081 sub-finding: lower the forwarder gate from
 * `level >= 2` (WARN and above only) to `level >= 1` so INFO-
 * level renderer telemetry reaches the main process log too.
 * VERBOSE (level 0) is still dropped — too noisy for the main
 * log. Routing through the structured logger (PVT-G5-080) so
 * WARN/ERROR lines also land in electron-runtime.log.
 *
 * Note: the main-window.ts handler ALSO persists ERROR-level lines
 * to `electron-renderer-errors.log` via `appendRendererError`. That
 * step is intentionally NOT in this helper — it's specific to the
 * main window (the bubble renderer's errors are not interesting
 * enough to warrant a separate log file, and pulling
 * `appendRendererError` in would couple this helper to
 * `rotation.ts`). main-window.ts will keep its appendRendererError
 * call when it migrates to this helper.
 */
import type { BrowserWindow } from "electron";
import { cleanConsoleMsg, log, RESET } from "../../logging";

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
 *   - unknown level   → `log.info` (tagged `LOG`)
 */
export function attachConsoleForwarder(
	win: BrowserWindow,
	options: ConsoleForwarderOptions,
): void {
	const { tag, colorPrefix } = options;
	win.webContents.on("console-message", (e) => {
		const level = Number(e.level);
		if (level >= 1) {
			const levelTag = ["VRB", "INFO", "WARN", "ERROR"][level] ?? "LOG";
			const msg = `${colorPrefix}${tag} ${levelTag}${RESET} ${cleanConsoleMsg(e.message)} (${e.sourceId}:${e.lineNumber})`;
			if (level >= 3) log.error(msg);
			else if (level === 2) log.warn(msg);
			else log.info(msg);
		}
	});
}
