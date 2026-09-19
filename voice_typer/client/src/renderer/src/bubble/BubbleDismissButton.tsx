/**
 * Bubble overlay package, `BubbleDismissButton`.
 * The dismiss '×' button. Shown whenever the bubble is in
 * `always_visible` mode (gated by the parent via the `dismissable`
 * `require_bubble_window` (SEC-016).
 */
import { t } from "@/i18n/i18n";
import { BUBBLE_BUTTON_CLASS } from "./constants";

export function BubbleDismissButton({ onClick }: { onClick: () => void }) {
	const label = t("bubble.dismissAria");
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={label}
			title={label}
			// Matches the BubbleMicButton sizing/styling so the two
			// affordances look like siblings.
			className={BUBBLE_BUTTON_CLASS}
		>
			<svg
				width="10"
				height="10"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				strokeWidth="3"
				strokeLinecap="round"
				strokeLinejoin="round"
				aria-hidden="true"
			>
				<line x1="6" y1="6" x2="18" y2="18" />
				<line x1="18" y1="6" x2="6" y2="18" />
			</svg>
		</button>
	);
}
