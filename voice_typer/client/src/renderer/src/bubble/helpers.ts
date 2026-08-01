/**
 * Bubble overlay package — shared pure helpers.
 *
 * The helpers here have no React state and no IPC side effects —
 * they are pure functions safe to unit-test in isolation.
 */
import { t } from "@/i18n/i18n";
import type { BubbleMode } from "./constants";

/**
 * Translation-with-fallback helper. The i18n `t()` returns the raw key
 * string when the key is missing from every locale dictionary. We fall
 * back to a sensible English label instead of rendering the raw key.
 */
export function tf(key: string, fallback: string): string {
	const v = t(key);
	return v === key ? fallback : v;
}

/**
 * RMS → normalised level [0, 1]. Speech RMS typically lives in
 * [0, ~0.3]; we apply a soft compressor so loud transients don't peg
 * every bar. Multiplier 8 (was 5) so quiet speech visibly animates.
 */
export function rmsToNorm(rms: number): number {
	return Math.min(1, rms * 8);
}

/**
 * State-aware aria-label for the bubble `<output aria-live="polite">`
 * wrapper so screen-reader users hear the current bubble mode
 * ("recording" / "transcribing" / "error" / "idle" / etc.) instead of
 * always hearing "recording".
 *
 * Encapsulates the 7-deep ternary over `mode` that used to live inline
 * in `Bubble.tsx`'s `<output>` JSX. The switch form is byte-equivalent
 * to the previous ternary chain for every mode — the only change is
 * shape, not output.
 *
 * The `fading` mode is a brief transcribing → exit transition; it
 * shares the transcribing label. The idle label is the catch-all for
 * any unexpected future mode (the `default` branch).
 *
 * `errorMessage` is currently unused — it's accepted in the signature
 * (typed `string | null` to match `useBubbleStateMachine`'s return
 * type) so a future a11y improvement can surface the error reason to
 * AT users (e.g. `"Voice Typer error indicator: microphone
 * disconnected"`) without churning call sites. The current output is
 * intentionally identical to the previous inline ternary chain in
 * `Bubble.tsx`.
 */
export function getBubbleAriaLabel(
	mode: BubbleMode,
	errorMessage?: string | null,
): string {
	// Acknowledge the reserved param without using it — see the
	// docstring above for the rationale.
	void errorMessage;
	switch (mode) {
		case "recording":
			return t("bubble.recordingIndicatorAria");
		case "transcribing":
		case "fading":
			return t("bubble.transcribingAria");
		case "error":
			return t("bubble.errorIndicatorAria");
		case "blocked":
			return tf("bubble.blockedIndicatorAria", "Voice Typer blocked indicator");
		case "cancelling":
			return tf(
				"bubble.cancellingIndicatorAria",
				"Voice Typer cancelling indicator",
			);
		case "permission_revoked":
			return tf(
				"bubble.permissionRevokedIndicatorAria",
				"Voice Typer microphone permission revoked indicator",
			);
		case "paste_failed":
			return tf(
				"bubble.pasteFailedIndicatorAria",
				"Voice Typer paste failed indicator",
			);
		default:
			return t("bubble.idleIndicatorAria");
	}
}
