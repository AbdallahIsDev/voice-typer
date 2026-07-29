/**
 * Bubble overlay package — `BubbleStopButton` (XA-6-1 / XA-6-13).
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16).
 *
 * The stop '■' / retry '↻' affordance shown at the trailing edge of
 * the pill. In `recording` mode it is always rendered (independent of
 * `always_visible`) and clicking it sends `bubble:toggle-dictation`,
 * which the main process forwards to the Python `toggle_dictation`
 * command — the same channel `BubbleMicButton` uses. When recording,
 * `toggle_dictation` stops the recording and triggers transcription.
 *
 * This is the highest-impact XA-6 sub-fix: previously the only way to
 * stop a recording was the global hotkey, which is invisible to a
 * user who has forgotten the binding. The pill's `focusable: false`
 * BrowserWindow means a keyboard handler is impossible (PVT-048), so
 * a visible mouse-only button is the only viable in-bubble
 * affordance.
 *
 * In `error` mode the same component is rendered with a refresh icon
 * and a different aria-label so the user can retry the failed
 * transcription. The i18n keys fall back to English when the
 * dictionaries have not yet been updated (`tf` helper).
 *
 * A11Y: same `focusable: false` trade-off as `BubbleDismissButton` —
 * the button is mouse-only in the shipped app; `aria-label` and
 * `title` are populated so AT users navigating via screen-reader
 * cursor can still discover it.
 */
import { BUBBLE_BUTTON_CLASS } from "./constants";
import { tf } from "./helpers";

export function BubbleStopButton({
	onClick,
	mode,
}: {
	onClick: () => void;
	mode: "recording" | "error";
}) {
	// `tf` (translation-with-fallback) so a missing i18n key falls back
	// to a sensible English label instead of the raw key string.
	const label =
		mode === "error"
			? tf("bubble.retryAria", "Retry transcription")
			: tf("bubble.stopRecordingAria", "Stop recording");
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={label}
			title={label}
			// Same sizing/styling as BubbleDismissButton so the three
			// affordances (mic / stop / dismiss) look like siblings.
			className={BUBBLE_BUTTON_CLASS}
		>
			{mode === "error" ? (
				// Retry: a circular arrow (Material-style "refresh").
				<svg
					width="12"
					height="12"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					strokeWidth="2.5"
					strokeLinecap="round"
					strokeLinejoin="round"
					aria-hidden="true"
				>
					<polyline points="23 4 23 10 17 10" />
					<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
				</svg>
			) : (
				// Stop: a filled square (media "stop" iconography).
				<svg
					width="10"
					height="10"
					viewBox="0 0 24 24"
					fill="currentColor"
					stroke="none"
					aria-hidden="true"
				>
					<rect x="5" y="5" width="14" height="14" rx="2" ry="2" />
				</svg>
			)}
		</button>
	);
}
