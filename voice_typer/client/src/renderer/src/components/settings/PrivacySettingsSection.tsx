// PrivacySettingsSection — Audio & Recovery + Privacy & Consent sections of
// the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "Audio & Recovery" (Crash Recovery) and
// "Privacy & Consent" (HuggingFace / Voice biometric / OpenAI / Groq /
// Deepgram / LLM polish consents, Agree-to-All banner, Export Templates
// and Config buttons). Behaviour is identical to the previous
// monolithic implementation; this section owns its own `usePython` and
// `useSnackbar` hooks (per the refactor spec) so it can issue the
// `get_templates` / `get_config` IPC calls and surface their results
// without needing the parent to forward `call` or `showSnack` as props.

import {
	CheckmarkCircle01Icon,
	InformationCircleIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

export const PrivacySettingsSection = memo(function PrivacySettingsSection({
	config,
	updateConfig,
	isVisible,
}: SettingsSectionSharedProps) {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	if (!config) return <SettingsSkeleton rows={3} />;

	const handleCrashRecoveryChange = (checked: boolean) =>
		updateConfig({ crash_recovery_enabled: checked });

	const handleHuggingFaceConsentChange = (checked: boolean) =>
		updateConfig({ huggingface_consent: checked });

	const handleVoiceBiometricChange = (checked: boolean) =>
		updateConfig({ voice_biometric_consent: checked });

	const handleOpenAiConsentChange = (checked: boolean) =>
		updateConfig({ cloud_openai_consent: checked });

	const handleGroqConsentChange = (checked: boolean) =>
		updateConfig({ cloud_groq_consent: checked });

	const handleDeepgramConsentChange = (checked: boolean) =>
		updateConfig({ cloud_deepgram_consent: checked });

	const handleLlmPolishConsentChange = (checked: boolean) =>
		updateConfig({ llm_polish_consent: checked });

	const handleAgreeToAll = () => {
		updateConfig({
			huggingface_consent: true,
			voice_biometric_consent: true,
			cloud_openai_consent: true,
			cloud_groq_consent: true,
			cloud_deepgram_consent: true,
			llm_polish_consent: true,
		});
	};

	// IMPL-C: resolve the translated search-visible labels once per render so
	// the section-level isVisible check and the rendered SettingRow labels
	// share the same strings.
	const crashRecoveryLabel = t("settings.privacy.crashRecovery");
	const crashRecoveryInfoSearch = t("settings.privacy.crashRecoveryInfoSearch");
	const huggingFaceLabel = t("settings.privacy.huggingFaceDownloadsLabel");
	const huggingFaceInfoSearch = t(
		"settings.privacy.huggingFaceDownloadsInfoSearch",
	);
	const voiceBiometricLabel = t("settings.privacy.voiceBiometricLabel");
	const voiceBiometricInfoSearch = t(
		"settings.privacy.voiceBiometricInfoSearch",
	);
	const openaiCloudAsrLabel = t("settings.privacy.openaiCloudAsrLabel");
	const openaiCloudAsrInfoSearch = t(
		"settings.privacy.openaiCloudAsrInfoSearch",
	);
	const groqCloudAsrLabel = t("settings.privacy.groqCloudAsrLabel");
	const groqCloudAsrInfoSearch = t("settings.privacy.groqCloudAsrInfoSearch");
	const deepgramCloudAsrLabel = t("settings.privacy.deepgramCloudAsrLabel");
	const deepgramCloudAsrInfoSearch = t(
		"settings.privacy.deepgramCloudAsrInfoSearch",
	);
	const llmTextPolishingLabel = t("settings.privacy.llmTextPolishingLabel");
	const llmTextPolishingInfoSearch = t(
		"settings.privacy.llmTextPolishingInfoSearch",
	);
	const exportAllDataLabel = t("settings.privacy.exportAllDataLabel");
	const exportAllDataInfoSearch = t("settings.privacy.exportAllDataInfoSearch");

	// UX-028: section-level visibility check for Audio & Recovery section.
	const audioRecoveryTitle = t("settings.privacy.audioRecoveryTitle");
	const audioRecoveryItems = [
		{ label: crashRecoveryLabel, info: crashRecoveryInfoSearch },
	];
	const audioRecoveryVisible = audioRecoveryItems.some((item) =>
		isVisible(item.label, item.info, audioRecoveryTitle),
	);

	// UX-028: section-level visibility check for Privacy & Consent section.
	const privacyTitle = t("settings.privacy.privacyTitle");
	const privacyItems = [
		{ label: huggingFaceLabel, info: huggingFaceInfoSearch },
		{ label: voiceBiometricLabel, info: voiceBiometricInfoSearch },
		{ label: openaiCloudAsrLabel, info: openaiCloudAsrInfoSearch },
		{ label: groqCloudAsrLabel, info: groqCloudAsrInfoSearch },
		{ label: deepgramCloudAsrLabel, info: deepgramCloudAsrInfoSearch },
		{ label: llmTextPolishingLabel, info: llmTextPolishingInfoSearch },
		{ label: exportAllDataLabel, info: exportAllDataInfoSearch },
	];
	const privacyVisible = privacyItems.some((item) =>
		isVisible(item.label, item.info, privacyTitle),
	);

	return (
		<>
			{/* ── SECTION: Audio & Recovery ─────────────────────────── */}
			{audioRecoveryVisible && (
				<SettingsSection
					title={audioRecoveryTitle}
					description={t("settings.privacy.audioRecoveryDescription")}
				>
					<SettingRow
						label={crashRecoveryLabel}
						info={t("settings.privacy.crashRecoveryInfo")}
					>
						{" "}
						<Switch
							checked={config.crash_recovery_enabled ?? true}
							onCheckedChange={handleCrashRecoveryChange}
							aria-label={t("settings.privacy.crashRecoveryAria")}
						/>
					</SettingRow>
				</SettingsSection>
			)}

			{/* ── SECTION: Privacy & Consent ─────────────────────────── */}
			{privacyVisible && (
				<>
					{/* NEW-PRIV-005/006/009: centralized consent management.
                                All four consent flags live in the Python Config and are
                                enforced by the backend (HuggingFace download refusal,
                                CloudEngine ConsentRequiredError, etc.).  This section
                                gives the user a single place to view and revoke any
                                consent they've previously granted.  Initial grant
                                happens contextually (HuggingFace banner on Models page,
                                per-provider toggles on Models page) — this section is
                                primarily for review/revocation.

                                PRIV-AGREE-ALL (fix-quit-and-privacy): an "Agree to All"
                                affordance at the top lets the user enable every consent
                                flag at once without clicking six toggles.  Defaults stay
                                False (privacy-by-default); the button is purely a UX
                                convenience, not an implicit grant.  Individual toggles
                                below remain for granular control / revocation. */}
					<SettingsSection
						title={privacyTitle}
						description={t("settings.privacy.privacyDescription")}
					>
						{/* PRIV-AGREE-ALL: header banner + Agree to All button.
                                        Explains what "agreeing" means in plain language so
                                        the user can make an informed decision before
                                        clicking.  The banner sits inside the same
                                        bordered container as the toggles (visually grouped
                                        with them) but uses a slightly different background
                                        to distinguish it from per-flag rows. */}
						<div className="px-3.5 py-3.5 space-y-3 bg-(--bg-subtle)/60">
							<div className="flex items-start gap-2">
								<HugeiconsIcon
									icon={InformationCircleIcon}
									strokeWidth={2}
									className="h-4 w-4 mt-0.5 shrink-0 text-(--text-muted)"
								/>
								<div className="text-sm text-(--text-muted) space-y-1.5 min-w-0">
									<p>{t("settings.privacy.consentBannerDesc")}</p>
									<ul className="list-disc pl-4 space-y-0.5 text-xs">
										<li>{t("settings.privacy.huggingFaceItem")}</li>
										<li>{t("settings.privacy.cloudAsrItem")}</li>
										<li>{t("settings.privacy.llmPolishItem")}</li>
										<li>{t("settings.privacy.voiceBiometricItem")}</li>
									</ul>
									<p className="text-xs">
										{t("settings.privacy.revokeNotice")}
									</p>
								</div>
							</div>
							<div className="flex items-center justify-between gap-3">
								<div className="text-xs text-(--text-muted)">
									{(() => {
										if (!config) return "";
										const granted = [
											config.huggingface_consent,
											config.voice_biometric_consent,
											config.cloud_openai_consent,
											config.cloud_groq_consent,
											config.cloud_deepgram_consent,
											config.llm_polish_consent,
										].filter(Boolean).length;
										return t("settings.privacy.consentsGranted", {
											granted: String(granted),
										});
									})()}
								</div>
								<Button
									variant="default"
									size="sm"
									className="gap-1.5"
									onClick={handleAgreeToAll}
									disabled={
										config
											? Boolean(
													config.huggingface_consent &&
														config.voice_biometric_consent &&
														config.cloud_openai_consent &&
														config.cloud_groq_consent &&
														config.cloud_deepgram_consent &&
														config.llm_polish_consent,
												)
											: false
									}
									aria-label={t("settings.privacy.agreeToAllAria")}
									title={t("settings.privacy.agreeToAllHint")}
								>
									<HugeiconsIcon
										icon={CheckmarkCircle01Icon}
										strokeWidth={2}
										className="h-4 w-4"
									/>
									{t("settings.privacy.agreeToAll")}
								</Button>
							</div>
						</div>

						{/* HuggingFace consent */}
						<SettingRow
							label={huggingFaceLabel}
							info={t("settings.privacy.huggingFaceDownloadsInfo")}
						>
							<Switch
								checked={config.huggingface_consent ?? false}
								onCheckedChange={handleHuggingFaceConsentChange}
								aria-label={t("settings.privacy.huggingFaceDownloadsAria")}
							/>
						</SettingRow>

						{/* Voice biometric consent */}
						<SettingRow
							label={voiceBiometricLabel}
							info={t("settings.privacy.voiceBiometricProcessingInfo")}
						>
							<Switch
								checked={config.voice_biometric_consent ?? false}
								onCheckedChange={handleVoiceBiometricChange}
								aria-label={t("settings.privacy.voiceBiometricProcessingAria")}
							/>
						</SettingRow>

						{/* Per-provider cloud ASR consent — mirrors Models page toggles */}
						<SettingRow
							label={openaiCloudAsrLabel}
							info={t("settings.privacy.openaiCloudAsrInfo")}
						>
							<Switch
								checked={config.cloud_openai_consent ?? false}
								onCheckedChange={handleOpenAiConsentChange}
								aria-label={t("settings.privacy.openaiCloudAsrAria")}
							/>
						</SettingRow>
						<SettingRow
							label={groqCloudAsrLabel}
							info={t("settings.privacy.groqCloudAsrInfo")}
						>
							<Switch
								checked={config.cloud_groq_consent ?? false}
								onCheckedChange={handleGroqConsentChange}
								aria-label={t("settings.privacy.groqCloudAsrAria")}
							/>
						</SettingRow>
						<SettingRow
							label={deepgramCloudAsrLabel}
							info={t("settings.privacy.deepgramCloudAsrInfo")}
						>
							<Switch
								checked={config.cloud_deepgram_consent ?? false}
								onCheckedChange={handleDeepgramConsentChange}
								aria-label={t("settings.privacy.deepgramCloudAsrAria")}
							/>
						</SettingRow>

						{/* LLM polish consent (existing field, surfaced here for completeness) */}
						<SettingRow
							label={llmTextPolishingLabel}
							info={t("settings.privacy.llmTextPolishingInfo")}
						>
							<Switch
								checked={config.llm_polish_consent ?? false}
								onCheckedChange={handleLlmPolishConsentChange}
								aria-label={t("settings.privacy.llmTextPolishingAria")}
							/>
						</SettingRow>

						{/* NEW-PRIV-007: GDPR right-to-export (Art. 15/20).
                                        Previously only history + vocabulary were exportable.
                                        Templates and config are also user data and must be
                                        exportable on request.  The handlers live in
                                        main/index.ts (templates:export, config:export) and
                                        are exposed via the preload bridge. */}
						<SettingRow
							label={exportAllDataLabel}
							info={t("settings.privacy.exportAllDataInfo")}
						>
							<div className="flex gap-2">
								<Button
									variant="outline"
									size="sm"
									onClick={async () => {
										try {
											const templates = await call("get_templates");
											const result = await (
												window.window_ as {
													exportTemplates?: (data: unknown) => Promise<{
														success: boolean;
														path?: string;
														error?: string;
													}>;
												}
											).exportTemplates?.(templates);
											if (result?.success) {
												showSnack(
													t("settings.privacy.templatesExported", {
														filename:
															result.path?.split(/[\\/]/).pop() ??
															t("settings.privacy.fileFallback"),
													}),
													"success",
												);
											} else if (result?.error) {
												showSnack(
													t("settings.privacy.exportFailedError", {
														error: result.error,
													}),
													"error",
												);
											}
										} catch (err) {
											showSnack(
												t("settings.privacy.exportFailedError", {
													error:
														err instanceof Error ? err.message : String(err),
												}),
												"error",
											);
										}
									}}
									aria-label={t("settings.privacy.exportTemplatesAria")}
								>
									{t("settings.privacy.exportTemplates")}
								</Button>
								<Button
									variant="outline"
									size="sm"
									onClick={async () => {
										try {
											const cfg = await call("get_config");
											const result = await (
												window.window_ as {
													exportConfig?: (data: unknown) => Promise<{
														success: boolean;
														path?: string;
														error?: string;
													}>;
												}
											).exportConfig?.(cfg);
											if (result?.success) {
												showSnack(
													t("settings.privacy.configExported", {
														filename:
															result.path?.split(/[\\/]/).pop() ??
															t("settings.privacy.fileFallback"),
													}),
													"success",
												);
											} else if (result?.error) {
												showSnack(
													t("settings.privacy.exportFailedError", {
														error: result.error,
													}),
													"error",
												);
											}
										} catch (err) {
											showSnack(
												t("settings.privacy.exportFailedError", {
													error:
														err instanceof Error ? err.message : String(err),
												}),
												"error",
											);
										}
									}}
									aria-label={t("settings.privacy.exportConfigAria")}
								>
									{t("settings.privacy.exportConfig")}
								</Button>
							</div>
						</SettingRow>
					</SettingsSection>
				</>
			)}
		</>
	);
});
