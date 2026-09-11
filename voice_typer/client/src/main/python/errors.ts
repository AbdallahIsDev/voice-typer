/**
 * Typed IPC error class for the Python backend bridge.
 *
 * `sendToPython` rejects its returned Promise with a `PythonIpcError`
 * instead of a bare `new Error(string)` so downstream consumers (notably
 * the `python-call` IPC bridge in `../ipc/python-call-handler.ts`) can
 * branch on the typed `.code` field rather than regex-matching the
 * human-readable message string.
 *
 * Previous contract: 6 reject sites in `send-to-python.ts` threw bare
 * `new Error(...)`; only the timeout site set `err.code = "timeout"`.
 * The `python-call` handler therefore fell back to a fragile
 * `/timeout/i` regex on the message text to classify timeouts, which
 * would silently break if the message wording ever changed
 * (localization, rewording, unit change from seconds to ms).
 *
 * Current contract (scoped truthfully to the code as it exists):
 *
 *  - Every Python-bridge reject site whose construction lives in
 *    `send-to-python.ts` (including `resetPendingOutbound`),
 *    `tcp/close-handler.ts`, `tcp/frame-reader.ts`,
 *    `start-python.ts` (the process-exit handler), `relaunch-app.ts`
 *    (the production relaunch teardown), `tcp-bridge-reset.ts` (the
 *    shared restart teardown used by the relaunch-app dev branch and
 *    `restart-backend.ts`), and `handle-message.ts`
 *    (Python-side error replies) constructs a
 *    `PythonIpcError(code, message)` with a `PythonCallErrorCode`:
 *      - `backend_not_connected`  , pre-flight no-socket, mid-flight
 *                                    socket close / backend crash /
 *                                    backend-only restart teardown;
 *      - `backend_exited_early`   , backend died during startup;
 *      - `command_failed`         , allowlist/rate-limit/cap gates,
 *                                    oversized replies, restart
 *                                    teardowns;
 *      - `command_timeout`        , per-command deadline.
 *  - The handler checks `err instanceof PythonIpcError`, verifies
 *    `err.code` against the canonical `PYTHON_CALL_ERROR_CODES` union
 *    (backend-emitted codes can carry out-of-union strings, see
 *    `handle-message.ts`'s cast), passes the in-union code through to
 *    the renderer's `_code`, and falls back to `"command_failed"` for
 *    any non-typed error or out-of-union code (defense-in-depth for
 *    callers that throw a bare `Error`).
 *
 * The `import type` below is erased at compile time, so there is no
 * runtime circular dependency between `errors.ts` and
 * `python-call-handler.ts` (which imports `PythonIpcError` for the
 * `instanceof` check).
 */
import type { PythonCallErrorCode } from "../ipc/python-call-handler";

export class PythonIpcError extends Error {
	constructor(
		public code: PythonCallErrorCode,
		message: string,
	) {
		super(message);
		this.name = "PythonIpcError";
	}
}
