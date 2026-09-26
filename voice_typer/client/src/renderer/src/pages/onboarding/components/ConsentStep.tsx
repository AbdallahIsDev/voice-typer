// ConsentStep — consolidated first-run consent step (the "everything
// on ONE page" grant). Lists every consent-gated feature with its
// label + a question-mark InfoTooltip carrying the plain-language
// description (reusing the settings.privacy.* strings so the wizard
// and the Settings Privacy page can't drift), plus an "Agree to All"
// convenience button. Toggles persist immediately via set_config (the
// wizard hook owns the persistence); the user can revoke any consent
// later in Settings → Privacy (GDPR Art. 7(3), revocation stays as
// easy as granting).
// Layout contract (2026-09-14 redesign): the rows are FULL-WIDTH and
// separated by standard dividing borders (the Settings / Models page
// pattern, `divide-y` on the rows container + border-b between
// siblings) — the previous per-row `rounded-lg border p-4` boxes
// nested a card inside the step card. Descriptions moved out of the
// card view behind the shared InfoTooltip (C-MIC-11 pattern).

import type { Ref } from "react";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { t, useT } from "@/i18n/i18n";
import { HEADING_CLASS } from "../lib/constants";

/** Consent fields surfaced on this step (Settings-row subset).
 *  `offline_pack_consent` is always-on (no UI, not disableable). */
const CONSENT_STEP_FIELDS: {
	field: string;
	labelKey: string;
	infoKey: string;
}[] = [
	{
		field: "voice_biometric_consent",
		labelKey: "settings.privacy.voiceBiometricLabel",
		infoKey: "settings.privacy.voiceBiometricProcessingInfo",
	},
	{
		field: "huggingface_consent",
		labelKey: "settings.privacy.huggingFaceDownloadsLabel",
		infoKey: "settings.privacy.huggingFaceDownloadsInfo",
	},
	{
		field: "cloud_openai_consent",
		labelKey: "settings.privacy.openaiCloudAsrLabel",
		infoKey: "settings.privacy.openaiCloudAsrInfo",
	},
	{
		field: "cloud_groq_consent",
		labelKey: "settings.privacy.groqCloudAsrLabel",
		infoKey: "settings.privacy.groqCloudAsrInfo",
	},
	{
		field: "cloud_deepgram_consent",
		labelKey: "settings.privacy.deepgramCloudAsrLabel",
		infoKey: "settings.privacy.deepgramCloudAsrInfo",
	},
	{
		field: "llm_polish_consent",
		labelKey: "settings.privacy.llmTextPolishingLabel",
		infoKey: "settings.privacy.llmTextPolishingInfo",
	},
];

export interface ConsentStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	/** Current consent state keyed by config field. */
	consents: Record<string, boolean>;
	/** Persist a single consent toggle (immediate, via set_config). */
	onToggleConsent: (field: string, value: boolean) => void;
	/** Grant every consent at once (single batched set_config). */
	onAgreeToAll: () => void;
}

export default function ConsentStep({
	headingRef,
	consents,
	onToggleConsent,
	onAgreeToAll,
}: ConsentStepProps) {
	useT();
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.consentTitle")}
			</h2>
			<p className="text-sm text-muted-foreground">
				{t("onboarding.consentDescription")}
			</p>

			{/* Agree-to-All banner — grants every consent at once. The
			    wizard defaults stay privacy-first (all off); this is a
			    convenience, not an implicit grant. */}
			<div className="flex items-center justify-between gap-3 rounded-lg border border-border/5 bg-surface-subtle px-3.5 py-3">
				<p className="text-xs text-muted-foreground">
					{t("settings.privacy.privacyDescription")}
				</p>
				<Button
					variant="default"
					size="sm"
					className="shrink-0"
					onClick={onAgreeToAll}
					aria-label={t("settings.privacy.agreeToAllAria")}
				>
					{t("settings.privacy.agreeToAll")}
				</Button>
			</div>

			{/* Full-width consent rows separated by standard dividing
			    borders (Settings / Models page pattern). NO inner-card
			    padding boxes: the rows span the container edge-to-edge.
			    Descriptions live behind the per-row `?` InfoTooltip. */}
			<div className="flex flex-col divide-y divide-border/5">
				{CONSENT_STEP_FIELDS.map(({ field, labelKey, infoKey }) => {
					const label = t(labelKey);
					return (
						<div
							key={field}
							className="flex items-center justify-between gap-3 py-3.5"
							data-testid={`onboarding-consent-row-${field}`}
						>
							<div className="flex min-w-0 flex-1 items-center gap-1.5">
								<span className="truncate text-sm font-medium text-foreground">
									{label}
								</span>
								<InfoTooltip text={t(infoKey)} contextLabel={label} />
							</div>
							<Switch
								checked={consents[field] ?? false}
								onCheckedChange={(v) => onToggleConsent(field, v)}
								aria-label={label}
							/>
						</div>
					);
				})}
			</div>
		</>
	);
}
