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
