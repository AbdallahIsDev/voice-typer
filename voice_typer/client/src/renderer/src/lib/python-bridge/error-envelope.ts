export function parseTauriErrorEnvelope(raw: string): Error | null {
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		// Not JSON, a plain-string rejection (e.g. "dispatch timeout (120s)").
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
	// require N fix-and-resubmit cycles. Mirrors the predecessor path.
	const errs = Array.isArray(data.errors)
		? (data.errors as string[])
		: undefined;
	if (errs && errs.length > 0) {
		(err as { errors?: string[] }).errors = errs;
	}
	// Consent fields carried by ``client.consent_required`` envelopes.
	// Same guards as the predecessor path: non-empty strings only, so a
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
