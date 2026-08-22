/**
 * Parse a Tauri ``invoke`` rejection string into a real ``Error``.
 *
 * On Tauri v2, when the Rust ``dispatch`` command returns ``Err``, the
 * ``invoke`` promise rejects with the raw ``e.to_string()`` — which for
 * structured errors is the JSON-serialized envelope
 * ``{"type":"error","data":{"code":"...","message":"..."}}``
 * (see ``src-tauri/src/commands/sidecar_cmds/dispatch.rs``). The
 * Electron path resolves the SAME envelope shape as a successful value,
 * which the ``type === "error"`` check in the ``call`` wrapper turns
 * into an ``Error`` with ``err.code`` stamped. This helper makes the
 * Tauri path behave identically, so callers branching on ``err.code``
 * (e.g. ``command_timeout`` vs ``backend_not_connected``) work on BOTH
 * runtimes instead of silently falling through to a generic error on
 * Tauri.
 *
 * Returns ``null`` when the string is NOT a structured error envelope
 * (e.g. the Rust ``dispatch timeout (120s)`` plain-string rejection) —
 * the caller falls back to ``new Error(raw)``.
 */
export function parseTauriErrorEnvelope(raw: string): Error | null {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		// Not JSON — a plain-string rejection (e.g. "dispatch timeout (120s)").
		return null;
	}
	if (typeof parsed !== "object" || parsed === null) return null;
	const envelope = parsed as {
		type?: unknown;
		data?: { code?: unknown; message?: unknown };
	};
	if (envelope.type !== "error" || !envelope.data) return null;
	const msg =
		typeof envelope.data.message === "string" ? envelope.data.message : raw;
	const err = new Error(msg);
	const code = envelope.data.code;
	if (typeof code === "string" && code.length > 0) {
		(err as { code?: string }).code = code;
	}
	return err;
}
