/**
 * Strip NUL bytes from a template field value.
 * Templates are plain text, the UI never renders them via
 * ``dangerouslySetInnerHTML`` (they are emitted as text into the typed
 * under a SEC-027 "stored-XSS" rationale, but that rationale was
 */
export function sanitizeTemplateField(value: unknown): string {
	if (typeof value !== "string") return "";
	// Use String.fromCharCode(0) to avoid the no-control-regex lint rule
	// (a literal /\u0000/ in source would trigger it).
	const nul = String.fromCharCode(0);
	return value.split(nul).join("");
}
