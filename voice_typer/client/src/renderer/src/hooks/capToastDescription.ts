// capToastDescription — bounds backend-provided strings that flow
// into sonner toast descriptions.
//
// Backend payloads (``failure_reason``, tray ``title``/``message``,
// cloud ``reason`` …) are free-form exception text with no length
// guarantee; the cloud emitter truncates to 200 chars server-side,
// but the other emitters do not. A multi-KB stack trace would blow
// the toast's layout. This renderer-side cap is defense in depth so
// EVERY degradation-toast description is bounded regardless of the
// emitter's discipline. 200 chars matches the server's existing
// truncation precedent (cloud ``reason``).

/** Maximum description length before truncation (matches the
 * backend's 200-char truncation of cloud failure reasons). */
export const TOAST_DESCRIPTION_MAX_CHARS = 200;

/**
 * Cap a backend-provided string to the toast-description budget.
 * Strings within the budget pass through unchanged; longer strings
 * are sliced to 199 chars + a single ellipsis character (200 total).
 */
export function capToastDescription(text: string): string {
	if (text.length <= TOAST_DESCRIPTION_MAX_CHARS) {
		return text;
	}
	return `${text.slice(0, TOAST_DESCRIPTION_MAX_CHARS - 1)}…`;
}
