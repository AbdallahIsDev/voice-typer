// ModelSettingsSection — Post-Processing + LLM Polishing sections of the
// Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "Post-Processing" (Transcription Language, Auto
// Punctuation, Text Cleanup, Text Snippets, Vocabulary) and "LLM Polishing"
// (Enable, API Key, API URL, Model, Preset). Behaviour is identical to
// the previous monolithic implementation; both sections are always rendered
// (no search-filter hide-when-empty wrapper, matching the original).

import { memo, useState } from "react";
import { KeyringStatusBadge } from "@/components/common/KeyringStatusBadge";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n/i18n";
import { LANGUAGE_OPTIONS } from "@/lib/utils/languages";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

const LLM_PRESET_OPTIONS = [
	{ value: "professional", labelKey: "settings.presetProfessional" },
	{ value: "casual", labelKey: "settings.presetCasual" },
	{ value: "email", labelKey: "settings.presetEmail" },
	{ value: "code", labelKey: "settings.presetCode" },
] as const;
export const ModelSettingsSection = memo(function ModelSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
}: SettingsSectionSharedProps) {
	// LLM API key visibility toggle (show/hide the password-style input).
	const [llmKeyVisible, setLlmKeyVisible] = useState(false);

	const handleApiKeyChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		updateConfigDebounced("llm_api_key", e.target.value);

	const handleToggleLlmKey = () => setLlmKeyVisible(!llmKeyVisible);

	const handleApiUrlChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		updateConfigDebounced("llm_api_url", e.target.value);

	const handleModelChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		updateConfigDebounced("llm_model", e.target.value);

	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	// ── Inline handler extraction ─────────────────────────────────
	const handleLanguageChange = (v: string) =>
		updateConfig({ language: v === "auto" ? "" : v });
	const handleAutoPunctuationChange = (checked: boolean) =>
		updateConfig({ auto_punctuation: checked });
	const handleTextCleanupChange = (checked: boolean) =>
		updateConfig({ text_cleanup_enabled: checked });
	const handleTextSnippetsChange = (checked: boolean) =>
		updateConfig({ templates_enabled: checked });
	const handleVocabularyChange = (checked: boolean) =>
		updateConfig({ vocabulary_enabled: checked });
	const handleLlmPolishChange = (checked: boolean) =>
		updateConfig({ llm_polish: checked });
	const handleLlmPresetChange = (v: string) => updateConfig({ llm_preset: v });

	//section-level visibility check for Post-Processing section.
	const postProcessingTitle = t("settings.postProcessing");
	const postProcessingItems = [
		{
			label: t("settings.transcriptionLanguage"),
			info: t("settings.transcriptionLanguageDescription"),
		},
		{
			label: t("settings.autoPunctuation"),
			info: t("settings.autoPunctuationInfo"),
		},
		{
			label: t("settings.textCleanupLabel"),
			info: t("settings.textCleanupInfo"),
		},
		{ label: t("settings.textSnippets"), info: t("settings.textSnippetsInfo") },
		{ label: t("settings.vocabulary"), info: t("settings.vocabularyInfo") },
	];
	const postProcessingVisible = postProcessingItems.some((item) =>
		isVisible(item.label, item.info, postProcessingTitle),
	);

	//section-level visibility check for LLM Polishing section.
	const llmPolishingTitle = t("settings.llmPolishing");
	const llmPolishingItems = [
		{ label: t("settings.enable"), info: t("settings.enableInfo") },
		{ label: t("settings.apiKey"), info: t("settings.apiKeyInfo") },
		{ label: t("settings.apiUrl"), info: t("settings.apiUrlInfo") },
		{ label: t("settings.model"), info: t("settings.modelInfo") },
		{ label: t("settings.preset"), info: t("settings.presetInfo") },
	];
	const llmPolishingVisible = llmPolishingItems.some((item) =>
		isVisible(item.label, item.info, llmPolishingTitle),
	);

	return (
		<>
			{/* ── SECTION: Post-Processing ──────────────────────────── */}
			{postProcessingVisible && (
				<SettingsSection
					title={postProcessingTitle}
					description={t("settings.postProcessingDescription")}
				>
					{isVisible(
						t("settings.transcriptionLanguage"),
						t("settings.transcriptionLanguageDescription"),
						postProcessingTitle,
					) && (
						<SettingRow
							label={t("settings.transcriptionLanguage")}
							info={t("settings.transcriptionLanguageDescription")}
						>
							<Select
								value={config.language || "auto"}
								onValueChange={handleLanguageChange}
							>
								<SelectTrigger
									className="w-44"
									aria-label={t("settings.transcriptionLanguage")}
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{LANGUAGE_OPTIONS.map((lang) => (
										<SelectItem key={lang.value} value={lang.value}>
											<span>{t(lang.labelKey)}</span>
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</SettingRow>
					)}

					{isVisible(
						t("settings.autoPunctuation"),
						t("settings.autoPunctuationInfo"),
						postProcessingTitle,
					) && (
						<SettingRow
							label={t("settings.autoPunctuation")}
							info={t("settings.autoPunctuationInfo")}
						>
							<Switch
								checked={config.auto_punctuation ?? false}
								onCheckedChange={handleAutoPunctuationChange}
								aria-label={t("settings.autoPunctuation")}
							/>
						</SettingRow>
					)}

					{isVisible(
						t("settings.textCleanupLabel"),
						t("settings.textCleanupInfo"),
						postProcessingTitle,
					) && (
						<SettingRow
							label={t("settings.textCleanupLabel")}
							info={t("settings.textCleanupInfo")}
						>
							<Switch
								checked={config.text_cleanup_enabled}
								onCheckedChange={handleTextCleanupChange}
								aria-label={t("settings.textCleanupLabel")}
							/>
						</SettingRow>
					)}

					{isVisible(
						t("settings.textSnippets"),
						t("settings.textSnippetsInfo"),
						postProcessingTitle,
					) && (
						<SettingRow
							label={t("settings.textSnippets")}
							info={t("settings.textSnippetsInfo")}
						>
							<Switch
								checked={config.templates_enabled ?? true}
								onCheckedChange={handleTextSnippetsChange}
								aria-label={t("settings.textSnippets")}
							/>
						</SettingRow>
					)}

					{isVisible(
						t("settings.vocabulary"),
						t("settings.vocabularyInfo"),
						postProcessingTitle,
					) && (
						<SettingRow
							label={t("settings.vocabulary")}
							info={t("settings.vocabularyInfo")}
						>
							<Switch
								checked={config.vocabulary_enabled ?? true}
								onCheckedChange={handleVocabularyChange}
								aria-label={t("settings.vocabulary")}
							/>
						</SettingRow>
					)}
				</SettingsSection>
			)}

			{/* ── SECTION: LLM Polishing ────────────────────────────── */}
			{llmPolishingVisible && (
				<SettingsSection
					title={llmPolishingTitle}
					description={t("settings.llmPolishingDescription2")}
				>
					<SettingRow
						label={t("settings.enable")}
						info={t("settings.enableInfo")}
					>
						<Switch
							checked={config.llm_polish ?? false}
							onCheckedChange={handleLlmPolishChange}
							aria-label={t("settings.llmPolishing")}
						/>
					</SettingRow>

					{config.llm_polish && (
						<div className="animate-fade-in space-y-0 divide-y divide-border/10">
							<SettingRow
								label={t("settings.apiKey")}
								info={t("settings.apiKeyInfo")}
							>
								<div className="relative">
									{/*keyring status indicator next to the LLM API
									 * key input. Shows a green lock icon when the secret
									 * is stored in the OS keychain, or an amber warning
									 * when only the plaintext fallback is available. */}
									<div className="mb-1.5">
										<KeyringStatusBadge status={config.keyring_status} />
									</div>
									<Input
										type={llmKeyVisible ? "text" : "password"}
										/* SEC-003: backend redacts the key to '<redacted>' in
										 * get_config responses.  Show empty in that case so
										 * the user isn't tempted to "save" the sentinel back.
										 * When the user types a real key, updateConfig sends
										 * it via set_config (which is allowlisted). */
										value={
											config.llm_api_key && config.llm_api_key !== "<redacted>"
												? config.llm_api_key
												: ""
										}
										onChange={handleApiKeyChange}
										placeholder={
											config.llm_api_key === "<redacted>"
												? "•••••••• (configured)"
												: t("settings.apiKeyPlaceholder")
										}
										className="w-56 pe-8"
										aria-label={t("settings.apiKey")}
									/>
									<Button
										variant="ghost"
										size="xs"
										onClick={handleToggleLlmKey}
										className="absolute right-1 top-1/2 -translate-y-1/2 text-xs"
										aria-label={
											llmKeyVisible ? t("settings.hide") : t("settings.show")
										}
									>
										{llmKeyVisible ? t("settings.hide") : t("settings.show")}
									</Button>
								</div>
							</SettingRow>

							<SettingRow
								label={t("settings.apiUrl")}
								info={t("settings.apiUrlInfo")}
							>
								<Input
									value={
										config.llm_api_url ??
										"https://api.openai.com/v1/chat/completions"
									}
									onChange={handleApiUrlChange}
									placeholder={t("settings.apiUrlPlaceholder")}
									className="w-64"
									aria-label={t("settings.apiUrl")}
								/>
							</SettingRow>

							<SettingRow
								label={t("settings.model")}
								info={t("settings.modelInfo")}
							>
								<Input
									value={config.llm_model ?? "gpt-4o-mini"}
									onChange={handleModelChange}
									placeholder={t("settings.modelPlaceholder")}
									className="w-44"
									aria-label={t("settings.model")}
								/>
							</SettingRow>

							<SettingRow
								label={t("settings.preset")}
								info={t("settings.presetInfo")}
							>
								<Select
									value={config.llm_preset ?? "professional"}
									onValueChange={handleLlmPresetChange}
								>
									<SelectTrigger
										className="w-40"
										aria-label={t("settings.preset")}
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{LLM_PRESET_OPTIONS.map((opt) => (
											<SelectItem key={opt.value} value={opt.value}>
												{t(opt.labelKey)}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</SettingRow>
						</div>
					)}
				</SettingsSection>
			)}
		</>
	);
});
