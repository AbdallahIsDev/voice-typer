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
 * The Rust host passes the sidecar's error envelope through VERBATIM
 * (the ``VoiceTyperError`` passthrough — see ``src-tauri/src/error.rs``),
 * so every structured field the Python backend attaches to
 * ``data`` reaches this parser. The fields stamped onto the returned
 * ``Error`` mirror the Electron-path semantics in ``usePython.ts``
 * EXACTLY (same non-empty-string / non-empty-array guards):
 *
 * - ``code`` — non-empty string only.
 * - ``errors`` — non-empty string array only (multi-field validation
 *   failures; ``data.message`` stays the ``Error.message``).
 * - ``consent_field`` / ``engine_name`` / ``model_id`` — non-empty
 *   strings only, carried by ``client.consent_required`` envelopes so
 *   callers can deep-link to the exact Settings toggle. A JSON ``null``
 *   ``model_id`` is NOT stamped (stays ``undefined``), matching the
 *   Electron path's normalization.
 * - ``legacy_code`` — Tauri-only superset: the transitional alias the
 *   server emits alongside the canonical namespaced ``code`` (see the
 *   error-envelope contract doc). The Electron path does not surface
 *   it (its envelopes resolve as values, not rejection strings), but
 *   stamping it here is harmless and lets Tauri-side callers observe
 *   both spellings during the migration window.
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
		data?: {
			code?: unknown;
			message?: unknown;
			errors?: unknown;
			consent_field?: unknown;
			engine_name?: unknown;
			model_id?: unknown;
			legacy_code?: unknown;
		};
	};
	if (envelope.type !== "error" || !envelope.data) return null;
	const data = envelope.data;
	const msg = typeof data.message === "string" ? data.message : raw;
	const err = new Error(msg);
	const code = data.code;
	if (typeof code === "string" && code.length > 0) {
		(err as { code?: string }).code = code;
	}
	// Multi-field validation failures: stamp the FULL ``errors`` list
	// when present (non-empty array only) so batched saves don't
	// require N fix-and-resubmit cycles. Mirrors the Electron path.
	const errs = Array.isArray(data.errors)
		? (data.errors as string[])
		: undefined;
	if (errs && errs.length > 0) {
		(err as { errors?: string[] }).errors = errs;
	}
	// Consent fields carried by ``client.consent_required`` envelopes.
	// Same guards as the Electron path: non-empty strings only, so a
	// JSON ``null`` model_id stays ``undefined`` on the thrown Error.
	const consentField = data.consent_field;
	const engineName = data.engine_name;
	const modelId = data.model_id;
	if (typeof consentField === "string" && consentField.length > 0) {
		(err as { consent_field?: string }).consent_field = consentField;
	}
	if (typeof engineName === "string" && engineName.length > 0) {
		(err as { engine_name?: string }).engine_name = engineName;
	}
	if (typeof modelId === "string" && modelId.length > 0) {
		(err as { model_id?: string }).model_id = modelId;
	}
	// Transitional alias the server emits alongside the canonical
	// namespaced ``code`` (one release cycle). Tauri-only superset —
	// documented in the error-envelope contract doc.
	const legacyCode = data.legacy_code;
	if (typeof legacyCode === "string" && legacyCode.length > 0) {
		(err as { legacy_code?: string }).legacy_code = legacyCode;
	}
	return err;
}
