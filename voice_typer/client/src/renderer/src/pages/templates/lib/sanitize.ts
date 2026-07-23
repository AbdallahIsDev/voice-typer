/**
 * SEC-027: strip characters that would allow HTML/script injection.
 * Removes ``<``, ``>``, ``\u0000``, and attribute-delimiter quotes.
 * Plain text and template variables ({today}, {clipboard}, etc.) are
 * preserved. The result is safe to render even via
 * ``dangerouslySetInnerHTML`` (though we still avoid that pattern).
 *
 * Extracted verbatim from the former ``pages/Templates.tsx`` (where it
 * was a private ``_sanitizeTemplateField`` helper) so the storage and
 * import-paths can share one sanitiser. Renamed to drop the leading
 * underscore because it's now an exported module member, not a private
 * file-local helper.
 */
export function sanitizeTemplateField(value: unknown): string {
	if (typeof value !== "string") return "";
	// Use String.fromCharCode(0) to avoid the no-control-regex lint rule
	// (a literal /\u0000/ in source would trigger it). The NUL byte is
	// a real XSS vector because browsers truncate attribute strings at
	// NUL — injecting `value="\u0000onload=alert(1)"` would let the
	// `onload=alert(1)` portion execute as an attribute.
	const nul = String.fromCharCode(0);
	return value
		.replace(/</g, "")
		.replace(/>/g, "")
		.replace(/"/g, "")
		.replace(/'/g, "")
		.split(nul)
		.join("");
}
