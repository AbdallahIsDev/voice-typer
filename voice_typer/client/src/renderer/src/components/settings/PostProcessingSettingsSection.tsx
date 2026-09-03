// PostProcessingSettingsSection — the Post-Processing section of the
// Settings surface.
//
// Extracted from the former ModelSettingsSection (which stacked the
// Post-Processing and LLM Polishing cards on one page) so each domain
// gets its own focused section page (settingsTranscription for this
// card). Renders one SettingsSection block: "Post-Processing"
// (Transcription Language, Auto Punctuation, Text Cleanup, Text
// Snippets, Vocabulary). Behaviour is identical to the previous combined
// implementation, including the per-row search-filter visibility via the
// `isVisible` prop and the section-level "hide if no items match" check.

import { memo } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
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

export const PostProcessingSettingsSection = memo(
	function PostProcessingSettingsSection({
		config,
		updateConfig,
		isVisible,
	}: SettingsSectionSharedProps) {
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

		//section-level visibility check for the Post-Processing section.
		// The title constant feeds BOTH the `<SettingsSection title>` prop
		// AND the `isVisible` third parameter, so search matches the
		// heading the user actually sees.
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
			{
				label: t("settings.textSnippets"),
				info: t("settings.textSnippetsInfo"),
			},
			{ label: t("settings.vocabulary"), info: t("settings.vocabularyInfo") },
		];
		const postProcessingVisible = postProcessingItems.some((item) =>
			isVisible(item.label, item.info, postProcessingTitle),
		);

		if (!postProcessingVisible) return null;

		return (
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
		);
	},
);
