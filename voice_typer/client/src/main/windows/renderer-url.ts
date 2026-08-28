export function sanitizeRendererUrl(
	raw: string | undefined,
): string | undefined {
	if (!raw) return undefined;
	try {
		const parsed = new URL(raw);
		if (parsed.protocol === "http:" || parsed.protocol === "https:") return raw;
	} catch {
		// invalid URL — fall through to undefined
	}
	return undefined;
}
