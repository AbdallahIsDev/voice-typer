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
