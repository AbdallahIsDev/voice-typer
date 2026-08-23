// lib/errors/userFacingErrorMessage.ts
//
// Maps KNOWN structured error codes from a failed `usePython().call()`
// to localized, user-actionable messages. Everything else falls back to
// the caller's contextual message.
//
// Why this exists (HP-6): `usePython.call` stamps structured fields onto
// thrown Errors — `.code` (the server's error-envelope code or the
// Electron main's `_code`: ``command_timeout`` /
// ``backend_not_connected`` / ...), `.errors` (multi-field validation
// lists) — but most catch-callers showed a generic "X failed" toast,
// so a timeout, an unreachable backend, and a rejected value were all
// indistinguishable to the user. This helper converts the failure CLASS
// into words; callers keep their context-specific fallback so unknown
// codes never lose their surrounding story ("Failed to start microphone
// test").
//
// Raw backend messages are intentionally NOT echoed here: they are
// English developer text (exception strings, field paths), which would
// leak into non-English UIs. Only codes with a curated localized body
// map to something; everything else gets the caller's (already
// localized) fallback.

/**
 * Minimal `t` function type matching i18n's canonical translate
 * signature (`i18n/translate.ts`).
 */
type TFn = (key: string, params?: Record<string, string>) => string;

/** Codes whose envelope carries a multi-error list (`data.errors`). */
const VALIDATION_CODES = new Set([
	"validation_error",
	"invalid_field",
	"missing_field",
	"invalid_payload",
	"client.invalid_field",
	"client.missing_field",
	"client.invalid_payload",
]);

/** Codes meaning "the backend service is not reachable". */
const BACKEND_NOT_CONNECTED_CODES = new Set([
	"backend_not_connected",
	"backend_exited_early",
	"sidecar_disconnected",
	"server.not_initialized",
	"not_initialized",
]);

/** Codes meaning "too many requests — slow down". */
const RATE_LIMITED_CODES = new Set([
	"rate_limited",
	"client.rate_limited",
	"server.cloud_rate_limited",
	"cloud_rate_limited",
]);

/**
 * Read the structured `code` off any thrown value. The bridge stamps
 * `.code` on real Error instances; non-Error rejections (raw Tauri
 * string envelopes are already normalized by the bridge, but defensive
 * reads cost nothing) may carry it on plain objects.
 */
function errorCode(err: unknown): string | null {
	if (err instanceof Error) {
		const code = (err as { code?: unknown }).code;
		return typeof code === "string" && code.length > 0 ? code : null;
	}
	if (err && typeof err === "object") {
		const code = (err as { code?: unknown }).code;
		return typeof code === "string" && code.length > 0 ? code : null;
	}
	return null;
}

/** Count the bridge-preserved `errors[]` list on a thrown value. */
function errorListLength(err: unknown): number {
	if (err instanceof Error || (err && typeof err === "object")) {
		const errors = (err as { errors?: unknown }).errors;
		if (Array.isArray(errors)) return errors.length;
	}
	return 0;
}

/**
 * Return a localized user-facing message for a failed IPC call.
 *
 * @param err the caught rejection (any shape).
 * @param t i18n translate function.
 * @param fallback the caller's contextual localized message, used for
 *   every code WITHOUT a curated mapping. Never undefined — callers
 *   must supply their own story so context survives ("Failed to start
 *   microphone test", "Failed to reset settings", …).
 * @returns the localized message to show.
 */
export function userFacingErrorMessage(
	err: unknown,
	t: TFn,
	fallback: string,
): string {
	const code = errorCode(err);
	if (code === "command_timeout") {
		return t("errors.commandTimeout");
	}
	if (code !== null && BACKEND_NOT_CONNECTED_CODES.has(code)) {
		return t("errors.backendNotConnected");
	}
	if (code !== null && RATE_LIMITED_CODES.has(code)) {
		return t("errors.rateLimited");
	}
	if (code !== null && VALIDATION_CODES.has(code)) {
		const count = errorListLength(err);
		return count > 0
			? t("errors.invalidFields", { count: String(count) })
			: t("errors.invalidValue");
	}
	return fallback;
}
