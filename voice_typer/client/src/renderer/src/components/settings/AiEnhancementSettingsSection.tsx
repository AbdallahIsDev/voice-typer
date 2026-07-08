// AiEnhancementSettingsSection — AI Enhancement + Vocabulary Automation
// sections of the Settings page.
//
// P4 (Task 7): rule-based grammar / punctuation / capitalization applied
// AFTER LLM polish and BEFORE the result is pasted. Master toggle
// (ai_enhancement_enabled) defaults OFF — the user must explicitly opt in.
// The three sub-toggles default ON so enabling the master toggle "just works".
//
// P5 (Task 8): confidence-score-based vocabulary correction suggestions.
// When ON, the dictation pipeline analyzes each transcription for
// low-confidence words and suggests vocabulary corrections; suggestions
// above the auto-apply threshold are added to the vocabulary automatically,
// the rest are queued for user review via the get_vocabulary_suggestions /
// apply_vocabulary_suggestion / dismiss_vocabulary_suggestion IPC commands.
//
// Both sections are placed together because they share the "smart post-
// processing" theme.  The user sees them as a single "AI Enhancement" group
// in Settings, even though the implementation lives in two separate server
// modules (ai_enhancement.py and vocabulary_automation.py).
//
// Pattern follows AudioSettingsSection.tsx: memo'd function component that
// accepts the shared SettingsSection props (config, updateConfig,
// updateConfigDebounced, isVisible) and renders a SettingsSection with
// SettingRow children.

import { memo } from "react";
import { RangeSlider } from "@/components/RangeSlider";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import { Switch } from "@/components/ui/switch";
import { t } from "@/i18n/i18n";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";
export const AiEnhancementSettingsSection = memo(
	function AiEnhancementSettingsSection({
		config,
		updateConfig,
		updateConfigDebounced,
		isVisible,
	}: SettingsSectionSharedProps) {
		if (!config) return <SettingsSkeleton rows={3} />;

		// Read the master toggle once so we can disable the sub-toggles
		// when it's off.  This mirrors the server-side behavior in
		// enhance_transcription() — when ai_enhancement_enabled is
		// False, the sub-toggles have no effect.
		const aiMasterOn = config.ai_enhancement_enabled ?? false;
		const vocabMasterOn = config.vocabulary_automation_enabled ?? false;

		// IMPL-C: resolve i18n keys once per render so the isVisible predicate
		// and the rendered output share the same translated strings.
		const aiEnableLabel = t("settings.aiEnhancement.enable");
		const aiEnableInfoSearch = t("settings.aiEnhancement.enableInfoSearch");
		const aiFixGrammarLabel = t("settings.aiEnhancement.fixGrammar");
		const aiFixGrammarInfoSearch = t(
			"settings.aiEnhancement.fixGrammarInfoSearch",
		);
		const aiAutoPunctuateLabel = t("settings.aiEnhancement.autoPunctuate");
		const aiAutoPunctuateInfoSearch = t(
			"settings.aiEnhancement.autoPunctuateInfoSearch",
		);
		const aiAutoCapitalizeLabel = t("settings.aiEnhancement.autoCapitalize");
		const aiAutoCapitalizeInfoSearch = t(
			"settings.aiEnhancement.autoCapitalizeInfoSearch",
		);

		const vocabEnableLabel = t("settings.vocabAutomation.enable");
		const vocabEnableInfoSearch = t(
			"settings.vocabAutomation.enableInfoSearch",
		);
		const vocabSuggestLabel = t(
			"settings.vocabAutomation.suggestBelowConfidence",
		);
		const vocabSuggestInfoSearch = t(
			"settings.vocabAutomation.suggestBelowConfidenceInfoSearch",
		);
		const vocabAutoApplyLabel = t(
			"settings.vocabAutomation.autoApplyConfidence",
		);
		const vocabAutoApplyInfoSearch = t(
			"settings.vocabAutomation.autoApplyConfidenceInfoSearch",
		);

		// ── Inline handler extraction ─────────────────────────────────
		const handleAiEnableChange = (checked: boolean) =>
			updateConfig({ ai_enhancement_enabled: checked });
		const handleFixGrammarChange = (checked: boolean) =>
			updateConfig({ fix_grammar_basics: checked });
		const handleAutoPunctuateChange = (checked: boolean) =>
			updateConfig({ auto_punctuate: checked });
		const handleAutoCapitalizeChange = (checked: boolean) =>
			updateConfig({ auto_capitalize: checked });
		const handleVocabEnableChange = (checked: boolean) =>
			updateConfig({ vocabulary_automation_enabled: checked });
		const handleSuggestConfidenceChange = (v: number) =>
			updateConfigDebounced("vocabulary_auto_confidence_threshold", v);
		const handleAutoApplyConfidenceChange = (v: number) =>
			updateConfigDebounced("vocabulary_auto_apply_threshold", v);

		// UX-028: section-level visibility check for AI Enhancement section.
		const aiSectionTitle = t("settings.aiEnhancement.title");
		const aiItems = [
			{ label: aiEnableLabel, info: aiEnableInfoSearch },
			{ label: aiFixGrammarLabel, info: aiFixGrammarInfoSearch },
			{ label: aiAutoPunctuateLabel, info: aiAutoPunctuateInfoSearch },
			{ label: aiAutoCapitalizeLabel, info: aiAutoCapitalizeInfoSearch },
		];
		const aiVisible = aiItems.some((item) =>
			isVisible(item.label, item.info, aiSectionTitle),
		);

		// UX-028: section-level visibility check for Vocabulary Automation section.
		const vocabSectionTitle = t("settings.vocabAutomation.title");
		const vocabItems = [
			{ label: vocabEnableLabel, info: vocabEnableInfoSearch },
			{ label: vocabSuggestLabel, info: vocabSuggestInfoSearch },
			{ label: vocabAutoApplyLabel, info: vocabAutoApplyInfoSearch },
		];
		const vocabVisible = vocabItems.some((item) =>
			isVisible(item.label, item.info, vocabSectionTitle),
		);

		return (
			<>
				{/* ── SECTION: AI Enhancement (P4) ──────────────────────── */}
				{aiVisible && (
					<SettingsSection
						title={aiSectionTitle}
						description={t("settings.aiEnhancement.description")}
					>
						<div className="animate-fade-in space-y-0 divide-y divide-border">
							{/* ── Master toggle ── */}
							<SettingRow
								label={aiEnableLabel}
								info={t("settings.aiEnhancement.enableInfo")}
							>
								<Switch
									checked={aiMasterOn}
									onCheckedChange={handleAiEnableChange}
									aria-label={t("settings.aiEnhancement.enableAria")}
								/>
							</SettingRow>

							{/* ── Sub-toggles (disabled when master is off) ── */}
							<SettingRow
								label={aiFixGrammarLabel}
								info={t("settings.aiEnhancement.fixGrammarInfo")}
							>
								<Switch
									checked={config.fix_grammar_basics ?? true}
									onCheckedChange={handleFixGrammarChange}
									disabled={!aiMasterOn}
									aria-label={t("settings.aiEnhancement.fixGrammarAria")}
								/>
							</SettingRow>

							<SettingRow
								label={aiAutoPunctuateLabel}
								info={t("settings.aiEnhancement.autoPunctuateInfo")}
							>
								<Switch
									checked={config.auto_punctuate ?? true}
									onCheckedChange={handleAutoPunctuateChange}
									disabled={!aiMasterOn}
									aria-label={t("settings.aiEnhancement.autoPunctuateAria")}
								/>
							</SettingRow>

							<SettingRow
								label={aiAutoCapitalizeLabel}
								info={t("settings.aiEnhancement.autoCapitalizeInfo")}
							>
								<Switch
									checked={config.auto_capitalize ?? true}
									onCheckedChange={handleAutoCapitalizeChange}
									disabled={!aiMasterOn}
									aria-label={t("settings.aiEnhancement.autoCapitalizeAria")}
								/>
							</SettingRow>
						</div>
					</SettingsSection>
				)}

				{/* ── SECTION: Vocabulary Automation (P5) ───────────────── */}
				{vocabVisible && (
					<SettingsSection
						title={vocabSectionTitle}
						description={t("settings.vocabAutomation.description")}
					>
						<div className="animate-fade-in space-y-0 divide-y divide-border">
							{/* ── Master toggle ── */}
							<SettingRow
								label={vocabEnableLabel}
								info={t("settings.vocabAutomation.enableInfo")}
							>
								<Switch
									checked={vocabMasterOn}
									onCheckedChange={handleVocabEnableChange}
									aria-label={t("settings.vocabAutomation.enableAria")}
								/>
							</SettingRow>

							{/* ── Confidence threshold ── */}
							<SettingRow
								label={vocabSuggestLabel}
								info={t("settings.vocabAutomation.suggestBelowConfidenceInfo")}
							>
								<RangeSlider
									value={config.vocabulary_auto_confidence_threshold ?? 0.7}
									min={0}
									max={1}
									step={0.05}
									onChange={handleSuggestConfidenceChange}
									ariaLabel={t(
										"settings.vocabAutomation.suggestBelowConfidenceAria",
									)}
									disabled={!vocabMasterOn}
									suffix=""
								/>
							</SettingRow>

							{/* ── Auto-apply threshold ── */}
							<SettingRow
								label={vocabAutoApplyLabel}
								info={t("settings.vocabAutomation.autoApplyConfidenceInfo")}
							>
								<RangeSlider
									value={config.vocabulary_auto_apply_threshold ?? 0.95}
									min={0}
									max={1}
									step={0.05}
									onChange={handleAutoApplyConfidenceChange}
									ariaLabel={t(
										"settings.vocabAutomation.autoApplyConfidenceAria",
									)}
									disabled={!vocabMasterOn}
									suffix=""
								/>
							</SettingRow>
						</div>
					</SettingsSection>
				)}
			</>
		);
	},
);
