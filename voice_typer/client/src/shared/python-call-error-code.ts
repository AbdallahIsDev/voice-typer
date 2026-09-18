/**
 * Canonical PythonCallErrorCode union, shared between the Tauri host
 * bridge and the renderer.
 *
 * The host `dispatch` path stamps a structured `_code` field on its
 * `{_error, _code}` error envelope so the renderer can branch on the
 * failure class (timeout vs. not-connected vs. backend-exited)
 * without parsing the human-readable message text.
 *
 * Stability contract: these codes are stable across versions, never
 * rename an existing code (only add new ones). The renderer's
 * `usePython().call(...)` wrapper narrows `_code` against this union.
 */
export const PYTHON_CALL_ERROR_CODES = [
	"backend_not_connected",
	"backend_exited_early",
	"command_failed",
	"command_timeout",
	// MO-122: pending-map backpressure. Same code the Tauri host emits
	// (`PENDING_FULL_CODE` in `src-tauri/commands/sidecar_cmds/allowlist.rs`)
	// so a renderer branching on `_code === "pending_full"` sees the
	// same value under both hosts.
	"pending_full",
] as const;
export type PythonCallErrorCode = (typeof PYTHON_CALL_ERROR_CODES)[number];
