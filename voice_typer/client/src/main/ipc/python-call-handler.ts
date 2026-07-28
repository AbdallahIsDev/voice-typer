/**
 * `python-call` IPC bridge: renderer → Electron main → Python backend.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * SEC-022: catch errors in the python-call handler so they are
 * returned as structured error responses instead of throwing an
 * unhandled exception that Electron logs as:
 *   "Error occurred in handler for 'python-call': Error: Timeout"
 *
 * Structured-error-response hardening: the structured error response now
 * carries both a human-readable `_error` message (localized via `mainT()`
 * so non-English users see the same string the dialogs use) AND a machine-
 * readable `_code` field so the renderer can branch on the failure class
 * (timeout vs. not-connected vs. backend-exited) without parsing the
 * message text. Failures are also logged via `logger.warn` so they land
 * in `electron-main.log` for post-mortem diagnosis — previously they
 * vanished silently into the renderer's `.catch(() => {})` swallows.
 *
 * Type-safety pass: Electron's `ipcMain.handle` listener signature types
 * `msg` as `any`. Annotate explicitly so the parameter type flows
 * into `sendToPython(msg: Record<string, unknown>)` instead of being
 * silently satisfied by `any`. This is the primary renderer→main IPC
 * boundary; tightening it catches shape drift at compile time.
 * T2-005: explicit `Promise<unknown>` return type so the handler's
 * resolved value (either the Python response envelope or an
 * `{ _error: string, _code: PythonCallErrorCode }` shape) is documented.
 */
import { ipcMain } from "electron";
import { logger } from "../logging";
import { sendToPython } from "../python";
import { state } from "../state";

/**
 * Structured error codes for the `python-call` envelope.
 *
 * The renderer's `usePython().call(...)` wrapper inspects `_code` to
 * decide whether to retry (timeout), surface a "backend offline" toast
 * (not-connected), or escalate to a full app-restart prompt
 * (backend-exited). The codes are stable across versions — never rename
 * an existing code (only add new ones).
 *
 * Renderer-narrowing export: exported so the renderer (`usePython.ts`)
 * can narrow the `_code` field against this union.
 */
export type PythonCallErrorCode =
	| "backend_not_connected"
	| "backend_exited_early"
	| "command_failed"
	| "command_timeout";

/**
 * Per-code English fallback messages for `_error` (log/dev-facing).
 * The renderer uses `_code` for its own localized lookup.
 * "Critical Error" string is now RESERVED for the breaker dialog only.
 */
const ERROR_MESSAGES: Record<PythonCallErrorCode, string> = {
	backend_not_connected: "Python backend is not connected.",
	backend_exited_early: "Python backend exited during startup.",
	command_failed: "Python command failed.",
	command_timeout: "Python command timed out.",
};

export function registerPythonCallHandler(): void {
	ipcMain.handle(
		"python-call",
		async (event, msg: Record<string, unknown>): Promise<unknown> => {
			const cmd =
				msg && typeof msg === "object" && "type" in msg
					? String((msg as { type: unknown }).type)
					: "<unknown>";

			if (!state.tcpSocket) {
				if (state.pythonExitedEarly) {
					const code: PythonCallErrorCode = "backend_exited_early";
					logger.warn("python-call rejected", { cmd, code });
					// Per-code message, NOT "Critical Error".
					return { _error: ERROR_MESSAGES[code], _code: code };
				}
				const code: PythonCallErrorCode = "backend_not_connected";
				logger.warn("python-call rejected", { cmd, code });
				return { _error: ERROR_MESSAGES[code], _code: code };
			}
			try {
				// Pass the sender's WebContents ID so sendToPython
				// can apply the per-renderer rate limit. Main-process-
				// internal callers (no event) pass null and skip the limit
				// but still hit the global MAX_PENDING_REQUESTS cap.
				const senderId =
					event?.sender && typeof event.sender.id === "number"
						? event.sender.id
						: null;
				return await sendToPython(msg, senderId);
			} catch (err) {
				const errMsg = (err as Error).message ?? String(err);
				const isTimeout = /timeout/i.test(errMsg);
				const code: PythonCallErrorCode = isTimeout
					? "command_timeout"
					: "command_failed";
				logger.warn("python-call failed", { cmd, code, error: errMsg });
				return {
					// DE-86: for command_failed, return the generic localized
					// message (NOT the raw Python traceback with filesystem
					// paths).  The full errMsg is still logged server-side
					// via logger.warn above.  For timeout the errMsg is safe
					// (it's just "Request Timeout") so append it for clarity.
					_error: isTimeout
						? `${ERROR_MESSAGES[code]} ${errMsg}`
						: ERROR_MESSAGES[code],
					_code: code,
				};
			}
		},
	);
}
