import { t } from "@/i18n/i18n";
import type { BubbleMode } from "./constants";

export function tf(key: string, fallback: string): string {
	const v = t(key);
	return v === key ? fallback : v;
}

export function rmsToNorm(rms: number): number {
	return Math.min(1, rms * 8);
}

export function getBubbleAriaLabel(
	mode: BubbleMode,
	errorMessage?: string | null,
): string {
	// Acknowledge the reserved param without using it, see the
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
			return t("bubble.blockedIndicatorAria");
		case "cancelling":
			return t("bubble.cancellingIndicatorAria");
		case "permission_revoked":
			return t("bubble.permissionRevokedIndicatorAria");
		case "paste_failed":
			return t("bubble.pasteFailedIndicatorAria");
		default:
			return t("bubble.idleIndicatorAria");
	}
}
