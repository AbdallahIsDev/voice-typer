/**
 * Strip NUL bytes from a template field value.
 *
 * Templates are plain text — the UI never renders them via
 * ``dangerouslySetInnerHTML`` (they are emitted as text into the typed
 * buffer and shown as ``<p>`` text content in the list/dialog). The
 * previous implementation also stripped ``<``, ``>``, ``"``, and ``'``
 * under a SEC-027 "stored-XSS" rationale, but that rationale was
 * overstated for plain-text rendering: React escapes text content
 * automatically, so angle brackets / quotes cannot break out of the
 * DOM context they are inserted into. Worse, the load-side-only strip
 * caused displayed text to silently diverge from the saved text (the
 * save path did not strip, so a template saved with ``<3`` re-rendered
 * as ``3`` on next load — corrupting user data).
 *
 * NUL bytes are still removed because browsers truncate attribute
 * strings at NUL — if a value were ever flowed into an attribute
 * (e.g. ``aria-label``), an injected ``\u0000`` could let the
 * trailing portion execute as a separate attribute. Plain-text
 * rendering is unaffected because NUL has no legitimate use in
 * user-authored template text.
 *
 * The function name and signature are preserved so the storage /
 * transform callers keep working unchanged.
 *
 * Extracted from the former ``pages/Templates.tsx`` (where it was a
 * private ``_sanitizeTemplateField`` helper) so the storage and
 * import-paths can share one sanitiser.
 */
export function sanitizeTemplateField(value: unknown): string {
	if (typeof value !== "string") return "";
	// Use String.fromCharCode(0) to avoid the no-control-regex lint rule
	// (a literal /\u0000/ in source would trigger it).
	const nul = String.fromCharCode(0);
	return value.split(nul).join("");
}
