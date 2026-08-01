/**
 * Bubble overlay package — `BubbleMicButton`.
 *
 * The always-visible mic toggle button. Shown only when the bubble is
 * in always_visible mode AND both `bubble_mic_button` and
 * `bubble_click_to_toggle` are on (gated by the parent). When
 * recording, shows a stop affordance; otherwise a mic. Clicking
 * toggles dictation via the sandboxed `bubble:toggle-dictation`
 * channel.
 *
 * A11Y TRADE-OFF (focusable:false — keyboard inaccessible):
 * The bubble `BrowserWindow` is created with `focusable: false` in
 * `main/windows/bubble-window.ts` (intentional — prevents the bubble
 * from stealing keyboard focus from the user's active text field).
 * Because the window is non-focusable, this real `<button>` element
 * is UNREACHABLE via Tab and cannot be activated via Enter/Space in
 * the shipped app. It is effectively mouse-only.
 *
 * Decision (keep focusable:false, document trade-off): we accept the
 * mouse-only limitation for now because making the bubble focusable
 * would harm the primary UX (dictation into the user's active text
 * field). The recommended future solution is a MAIN-PROCESS global
 * hotkey (e.g. Ctrl+Shift+M) that routes to the same
 * `bubble:toggle-dictation` channel. When that hotkey lands, the
 * BubbleMicButton's `aria-label` and `title` will already be correct;
 * only the wiring changes.
 *
 * Note: this button still renders with `type="button"` and an
 * `aria-label` so AT users navigating via screen-reader cursor (not
 * keyboard focus) can still discover it, and so automated a11y
 * audits (axe-core) see a properly-labelled control.
 */
import { t } from "@/i18n/i18n";
import { BUBBLE_BUTTON_CLASS, type BubbleMode } from "./constants";

export function BubbleMicButton({
	mode,
	onClick,
}: {
	mode: BubbleMode;
	onClick: () => void;
}) {
	const isRecording = mode === "recording";
	const label = isRecording
		? t("bubble.micButtonStopAria")
		: t("bubble.micButtonStartAria");
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={label}
			title={label}
			className={BUBBLE_BUTTON_CLASS}
		>
			{isRecording ? (
				<svg
					width="12"
					height="12"
					viewBox="0 0 24 24"
					fill="currentColor"
					aria-hidden="true"
				>
					<rect x="6" y="6" width="12" height="12" rx="2" />
				</svg>
			) : (
				<svg
					width="13"
					height="13"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					strokeWidth="2"
					strokeLinecap="round"
					strokeLinejoin="round"
					aria-hidden="true"
				>
					<rect x="9" y="2" width="6" height="12" rx="3" />
					<path d="M5 11a7 7 0 0 0 14 0" />
					<line x1="12" y1="18" x2="12" y2="22" />
				</svg>
			)}
		</button>
	);
}
