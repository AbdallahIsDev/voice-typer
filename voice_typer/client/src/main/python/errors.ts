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
 * `/timeout/i` regex on the message text to classify timeouts — which
 * would silently break if the message wording ever changed
 * (localization, rewording, unit change from seconds to ms).
 *
 * New contract: every `sendToPython` reject site constructs a
 * `PythonIpcError(code, message)` where `code` is one of the existing
 * `PythonCallErrorCode` union values. The handler checks
 * `err instanceof PythonIpcError` and reads `err.code` directly,
 * falling back to `"command_failed"` for any non-typed error
 * (defense-in-depth for callers that throw a bare `Error`).
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
