// Voice-consent gate banner for the Microphone page.
//
// The level monitor + mic test open a continuous biometric-capture
// stream, so the backend refuses them while
// ``config.voice_biometric_consent`` is off (GDPR Art. 9). Before this
// banner existed the client-side gate silently skipped monitoring —
// the meter sat at zero with NO explanation, indistinguishable from a
// broken mic. This banner names the actual state and deep-links to the
// exact Settings toggle (the unified consent dialog remains the
// point-of-use path via "Start Test"; this covers the page-level dead
// meter).
//
// Presentational only — visibility + the navigation callback are
// passed in by the page.

import { Settings03Icon, ViewOffIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t } from "@/i18n/i18n";

export interface MicrophoneConsentGateBannerProps {
	visible: boolean;
	/** Deep-link to the Settings privacy row for the consent toggle. */
	onOpenSettings: () => void;
}

export function MicrophoneConsentGateBanner({
	visible,
	onOpenSettings,
}: MicrophoneConsentGateBannerProps) {
	if (!visible) return null;

	return (
		<div
			role="alert"
			className="rounded-lg border border-warning/30 bg-warning/10 p-4 space-y-2"
		>
			<div className="flex items-start gap-2">
				<HugeiconsIcon
					icon={ViewOffIcon}
					strokeWidth={1.625}
					className="h-4 w-4 shrink-0 mt-0.5 text-warning"
				/>
				<div className="flex-1 space-y-1">
					<p className="text-sm font-semibold text-warning">
						{t("microphone.consentRequiredTitle")}
					</p>
					<p className="text-xs text-(--text-primary)">
						{t("microphone.consentRequired")}
					</p>
				</div>
			</div>
			<button
				type="button"
				onClick={onOpenSettings}
				aria-label={t("microphone.consentRequiredAction")}
				className="inline-flex items-center gap-1.5 rounded-md border border-warning/30 bg-warning/5 px-3 py-1.5 text-xs font-medium text-warning hover:bg-warning/10 transition-colors"
			>
				<HugeiconsIcon
					icon={Settings03Icon}
					strokeWidth={1.625}
					className="h-3.5 w-3.5"
				/>
				{t("microphone.consentRequiredAction")}
			</button>
		</div>
	);
}
