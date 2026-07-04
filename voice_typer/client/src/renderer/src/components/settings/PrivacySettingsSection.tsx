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
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
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

	// UX-028: section-level visibility check for Audio & Recovery section.
	const audioRecoveryTitle = "Audio & Recovery";
	const audioRecoveryItems = [
		{
			label: "Crash Recovery",
			info: "Save recent transcriptions so they can be recovered.",
		},
	];
	const audioRecoveryVisible = audioRecoveryItems.some((item) =>
		isVisible(item.label, item.info, audioRecoveryTitle),
	);

	// UX-028: section-level visibility check for Privacy & Consent section.
	const privacyTitle = "Privacy & Consent";
	const privacyItems = [
		{
			label: "HuggingFace model downloads",
			info: "Allows downloading Whisper model weights from huggingface.co.",
		},
		{
			label: "Voice biometric processing",
			info: "Allows Voice Typer to process your voice recordings locally.",
		},
		{
			label: "OpenAI cloud ASR",
			info: "Allows sending audio recordings to OpenAI's Whisper API.",
		},
		{
			label: "Groq cloud ASR",
			info: "Allows sending audio recordings to Groq's Whisper API.",
		},
		{
			label: "Deepgram cloud ASR",
			info: "Allows sending audio recordings to Deepgram's nova-2 API.",
		},
		{
			label: "LLM text polishing",
			info: "Allows sending transcribed text to an LLM API.",
		},
		{
			label: "Export all data (GDPR Art. 15/20)",
			info: "Download your templates and full configuration as JSON files.",
		},
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
					description="Quality monitoring and safety."
				>
					<SettingRow
						label="Crash Recovery"
						info="Save recent transcriptions so they can be recovered if the app crashes before you paste them."
					>
						<Switch
							checked={config.crash_recovery_enabled ?? true}
							onCheckedChange={(checked) =>
								updateConfig({ crash_recovery_enabled: checked })
							}
							aria-label="Crash Recovery"
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
						title="Privacy & Consent"
						description="Grant or revoke consent for data processing. All consents default to off — enable them individually below or use 'Agree to All' for convenience."
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
									<p>
										Voice Typer processes voice, text, and metadata locally by
										default. The features below require sending specific data to
										third parties — click
										<strong> Agree to All </strong> to enable every consent at
										once, or toggle individual features.
									</p>
									<ul className="list-disc pl-4 space-y-0.5 text-xs">
										<li>
											<strong>HuggingFace</strong>: downloads Whisper model
											weights (reveals your IP to a US third party; audio never
											leaves your machine).
										</li>
										<li>
											<strong>Cloud ASR</strong> (OpenAI / Groq / Deepgram):
											sends audio recordings for transcription when that
											provider is the active backend.
										</li>
										<li>
											<strong>LLM polish</strong>: sends transcribed
											<em> text </em>(not audio) to an OpenAI-compatible LLM API
											for refinement.
										</li>
										<li>
											<strong>Voice biometric</strong>: acknowledges that local
											voice recordings may be considered biometric data under
											BIPA / GDPR Art. 9.
										</li>
									</ul>
									<p className="text-xs">
										You can revoke any consent at any time by toggling it off
										below. Cloud features will refuse to run until the relevant
										consent is re-granted.
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
										return `${granted} of 6 consents granted`;
									})()}
								</div>
								<Button
									variant="default"
									size="sm"
									className="gap-1.5"
									onClick={() => {
										updateConfig({
											huggingface_consent: true,
											voice_biometric_consent: true,
											cloud_openai_consent: true,
											cloud_groq_consent: true,
											cloud_deepgram_consent: true,
											llm_polish_consent: true,
										});
									}}
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
									aria-label="Agree to all privacy consents"
									title="Enable all six consent flags below. You can revoke individual consents afterward."
								>
									<HugeiconsIcon
										icon={CheckmarkCircle01Icon}
										strokeWidth={2}
										className="h-4 w-4"
									/>
									Agree to All
								</Button>
							</div>
						</div>

						{/* HuggingFace consent */}
						<SettingRow
							label="HuggingFace model downloads"
							info="Allows downloading Whisper model weights from huggingface.co. Reveals your IP to a US-headquartered third party (Hugging Face, Inc.). Audio itself is never sent."
						>
							<Switch
								checked={config.huggingface_consent ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ huggingface_consent: checked })
								}
								aria-label="HuggingFace download consent"
							/>
						</SettingRow>

						{/* Voice biometric consent */}
						<SettingRow
							label="Voice biometric processing"
							info="Allows Voice Typer to process your voice recordings locally for transcription. Voice recordings may be considered biometric data under Illinois BIPA and GDPR Article 9. Voice Typer does not store raw audio after transcription — only the transcribed text is kept."
						>
							<Switch
								checked={config.voice_biometric_consent ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ voice_biometric_consent: checked })
								}
								aria-label="Voice biometric processing consent"
							/>
						</SettingRow>

						{/* Per-provider cloud ASR consent — mirrors Models page toggles */}
						<SettingRow
							label="OpenAI cloud ASR"
							info="Allows sending audio recordings to OpenAI's Whisper API for transcription. Only takes effect when OpenAI is the active ASR backend AND an API key is configured."
						>
							<Switch
								checked={config.cloud_openai_consent ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ cloud_openai_consent: checked })
								}
								aria-label="OpenAI cloud ASR consent"
							/>
						</SettingRow>
						<SettingRow
							label="Groq cloud ASR"
							info="Allows sending audio recordings to Groq's Whisper API for transcription. Only takes effect when Groq is the active ASR backend AND an API key is configured."
						>
							<Switch
								checked={config.cloud_groq_consent ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ cloud_groq_consent: checked })
								}
								aria-label="Groq cloud ASR consent"
							/>
						</SettingRow>
						<SettingRow
							label="Deepgram cloud ASR"
							info="Allows sending audio recordings to Deepgram's nova-2 API for transcription. Only takes effect when Deepgram is the active ASR backend AND an API key is configured."
						>
							<Switch
								checked={config.cloud_deepgram_consent ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ cloud_deepgram_consent: checked })
								}
								aria-label="Deepgram cloud ASR consent"
							/>
						</SettingRow>

						{/* LLM polish consent (existing field, surfaced here for completeness) */}
						<SettingRow
							label="LLM text polishing"
							info="Allows sending transcribed TEXT (not audio) to an OpenAI-compatible LLM API for polishing. Requires an LLM API key in the Post-Processing section."
						>
							<Switch
								checked={config.llm_polish_consent ?? false}
								onCheckedChange={(checked) =>
									updateConfig({ llm_polish_consent: checked })
								}
								aria-label="LLM polish consent"
							/>
						</SettingRow>

						{/* NEW-PRIV-007: GDPR right-to-export (Art. 15/20).
                                        Previously only history + vocabulary were exportable.
                                        Templates and config are also user data and must be
                                        exportable on request.  The handlers live in
                                        main/index.ts (templates:export, config:export) and
                                        are exposed via the preload bridge. */}
						<SettingRow
							label="Export all data (GDPR Art. 15/20)"
							info="Download your templates and full configuration as JSON files. API keys are redacted in the config export."
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
													`Templates exported: ${result.path?.split(/[\\/]/).pop() ?? "file"}`,
													"success",
												);
											} else if (result?.error) {
												showSnack(`Export failed: ${result.error}`, "error");
											}
										} catch (err) {
											showSnack(`Export failed: ${err}`, "error");
										}
									}}
									aria-label="Export templates as JSON"
								>
									Export Templates
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
													`Config exported: ${result.path?.split(/[\\/]/).pop() ?? "file"}`,
													"success",
												);
											} else if (result?.error) {
												showSnack(`Export failed: ${result.error}`, "error");
											}
										} catch (err) {
											showSnack(`Export failed: ${err}`, "error");
										}
									}}
									aria-label="Export configuration as JSON"
								>
									Export Config
								</Button>
							</div>
						</SettingRow>
					</SettingsSection>
				</>
			)}
		</>
	);
});
