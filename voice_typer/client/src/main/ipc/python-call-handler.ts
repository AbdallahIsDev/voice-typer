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
 * G4-M-68: the structured error response now carries both a human-
 * readable `_error` message (localized via `mainT()` so non-English
 * users see the same string the dialogs use) AND a machine-readable
 * `_code` field so the renderer can branch on the failure class
 * (timeout vs. not-connected vs. backend-exited) without parsing
 * the message text. Failures are also logged via `logger.warn` so
 * they land in `electron-main.log` for post-mortem diagnosis —
 * previously they vanished silently into the renderer's
 * `.catch(() => {})` swallows (G4-H-25).
 *
 * PVT-G5-052: Electron's `ipcMain.handle` listener signature types
 * `msg` as `any`. Annotate explicitly so the parameter type flows
 * into `sendToPython(msg: Record<string, unknown>)` instead of being
 * silently satisfied by `any`. This is the primary renderer→main IPC
 * boundary; tightening it catches shape drift at compile time.
 * T2-005: explicit `Promise<unknown>` return type so the handler's
 * resolved value (either the Python response envelope or an
 * `{ _error: string, _code: PythonCallErrorCode }` shape) is documented.
 */
import { ipcMain } from "electron";
import { mainT } from "../i18n";
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
 */
type PythonCallErrorCode =
	| "backend_not_connected"
	| "backend_exited_early"
	| "command_failed"
	| "command_timeout";

export function registerPythonCallHandler(): void {
	ipcMain.handle(
		"python-call",
		async (_event, msg: Record<string, unknown>): Promise<unknown> => {
			const cmd =
				msg && typeof msg === "object" && "type" in msg
					? String((msg as { type: unknown }).type)
					: "<unknown>";

			if (!state.tcpSocket) {
				if (state.pythonExitedEarly) {
					// G4-M-68: log the failure for post-mortem
					// diagnosis (previously vanished silently).
					logger.warn("python-call rejected", {
						cmd,
						code: "backend_exited_early",
					});
					return {
						_error: mainT("dialog.criticalError.title"),
						_code: "backend_exited_early" satisfies PythonCallErrorCode,
					};
				}
				logger.warn("python-call rejected", {
					cmd,
					code: "backend_not_connected",
				});
				return {
					_error: mainT("dialog.criticalError.title"),
					_code: "backend_not_connected" satisfies PythonCallErrorCode,
				};
			}
			try {
				return await sendToPython(msg);
			} catch (err) {
				// Distinguish timeout from other failures — the
				// `sendToPython` rejection message contains the
				// word "Timeout" (see send-to-python.ts's reject
				// call). The renderer can use this to decide
				// whether to retry vs. surface an error toast.
				const errMsg = (err as Error).message ?? String(err);
				const isTimeout = /timeout/i.test(errMsg);
				const code: PythonCallErrorCode = isTimeout
					? "command_timeout"
					: "command_failed";
				logger.warn("python-call failed", {
					cmd,
					code,
					error: errMsg,
				});
				return {
					_error: errMsg,
					_code: code,
				};
			}
		},
	);
}
