/**
 * Canonical PythonCallErrorCode union — shared between main process and renderer.
 *
 * Extracted from `python-call-handler.ts` (main scope) and
 * `types/ipc/enums.ts` (renderer scope) to eliminate the duplicate
 * declaration that previously required both files to carry a comment
 * pointing at each other. Both `tsconfig.web.json` and
 * `tsconfig.node.json` recursively include everything under
 * `src/shared` (their `include` arrays carry the `src/shared` glob),
 * so a single import resolves in either scope.
 *
 * The Electron main process's `python-call` IPC handler stamps a
 * structured `_code` field on its `{_error, _code}` error envelope so
 * the renderer can branch on the failure class (timeout vs.
 * not-connected vs. backend-exited) without parsing the human-readable
 * message text.
 *
 * Stability contract: these codes are stable across versions — never
 * rename an existing code (only add new ones). The renderer's
 * `usePython().call(...)` wrapper narrows `_code` against this union.
 */
export const PYTHON_CALL_ERROR_CODES = [
	"backend_not_connected",
	"backend_exited_early",
	"command_failed",
	"command_timeout",
] as const;
export type PythonCallErrorCode = (typeof PYTHON_CALL_ERROR_CODES)[number];
