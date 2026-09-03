// ConsentStep — consolidated first-run consent step (the "everything
// on ONE page" grant). Lists every consent-gated feature with its
// label + plain-language description (reusing the settings.privacy.*
// strings so the wizard and the Settings Privacy page can't drift),
// plus an "Agree to All" convenience button. Toggles persist
// immediately via set_config (the wizard hook owns the persistence);
// the user can revoke any consent later in Settings → Privacy (GDPR
// Art. 7(3) — revocation stays as easy as granting).

import type { Ref } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { t, useT } from "@/i18n/i18n";

const HEADING_CLASS = "text-2xl font-bold text-(--text-primary) outline-none";

/** The six consent fields surfaced on this step (Settings-row subset —
 *  `offline_pack_consent` has no Settings row and is granted at the
 *  pack-download point of use instead). Labels + descriptions reuse
 *  the settings.privacy.* keys (single source of truth, 8 locales). */
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
			<p className="text-sm text-(--text-muted)">
				{t("onboarding.consentDescription")}
			</p>

			{/* Agree-to-All banner — grants every consent at once. The
			    wizard defaults stay privacy-first (all off); this is a
			    convenience, not an implicit grant. */}
			<div className="flex items-center justify-between gap-3 rounded-lg border border-border/5 bg-(--bg-subtle) px-3.5 py-3">
				<p className="text-xs text-(--text-muted)">
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

			<div className="flex flex-col gap-3">
				{CONSENT_STEP_FIELDS.map(({ field, labelKey, infoKey }) => (
					<div key={field} className="rounded-lg border border-border/5 p-4">
						<div className="flex items-start justify-between gap-3">
							<div className="min-w-0 flex-1">
								<span className="block text-sm font-medium text-(--text-primary)">
									{t(labelKey)}
								</span>
								<span className="mt-0.5 block text-xs text-(--text-muted)">
									{t(infoKey)}
								</span>
							</div>
							<Switch
								checked={consents[field] ?? false}
								onCheckedChange={(v) => onToggleConsent(field, v)}
								aria-label={t(labelKey)}
							/>
						</div>
					</div>
				))}
			</div>
		</>
	);
}
