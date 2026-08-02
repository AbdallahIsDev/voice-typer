/**
 * Renderer-error persistence helper.
 *
 * Extracted from `main-window.ts` (split). Persists a
 * renderer-error line to `electron-renderer-errors.log` so support
 * staff can grep renderer crashes without fishing through DevTools or
 * the noisy `electron-main.log`.
 *
 * Best-effort: any I/O error is swallowed — logging must never break
 * the renderer console forwarding path.
 */
import { appendLogLine, rendererErrorsLogPath } from "../logging";

export function appendRendererError(line: string): void {
	try {
		appendLogLine(rendererErrorsLogPath(), line);
	} catch (e) {
		// Best-effort: a logging failure must not cascade into a runtime
		// failure of the calling code. The console.warn keeps the failure
		// visible without breaking the renderer console forwarding path.
		console.warn("[renderer-error-persistence] appendRendererError failed:", e);
	}
}
