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

		// UX-028: section-level visibility check for AI Enhancement section.
		const aiSectionTitle = "AI Enhancement";
		const aiItems = [
			{
				label: "Enable AI Enhancement",
				info: "Apply rule-based grammar fixes, auto-punctuation, and auto-capitalization.",
			},
			{
				label: "Fix Grammar Basics",
				info: "Capitalize the pronoun 'i', restore missing apostrophes.",
			},
			{
				label: "Auto-Punctuate",
				info: "Add a period at the end of sentences.",
			},
			{
				label: "Auto-Capitalize",
				info: "Capitalize the first letter of each sentence.",
			},
		];
		const aiVisible = aiItems.some((item) =>
			isVisible(item.label, item.info, aiSectionTitle),
		);

		// UX-028: section-level visibility check for Vocabulary Automation section.
		const vocabSectionTitle = "Vocabulary Automation";
		const vocabItems = [
			{
				label: "Enable Vocabulary Automation",
				info: "After each dictation, analyze low-confidence words.",
			},
			{
				label: "Suggest-Below Confidence",
				info: "Words transcribed with confidence below this threshold.",
			},
			{
				label: "Auto-Apply Confidence",
				info: "Suggestions with confidence at or above this threshold.",
			},
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
						description="Rule-based grammar, punctuation, and capitalization. Runs offline — no cloud API required."
					>
						<div className="animate-fade-in space-y-0 divide-y divide-border">
							{/* ── Master toggle ── */}
							<SettingRow
								label="Enable AI Enhancement"
								info="Apply rule-based grammar fixes, auto-punctuation, and auto-capitalization to your transcriptions. Runs entirely on-device — no text is sent to any cloud API. Off by default; turn on to apply the three sub-features below."
							>
								<Switch
									checked={aiMasterOn}
									onCheckedChange={(checked) =>
										updateConfig({ ai_enhancement_enabled: checked })
									}
									aria-label="Enable AI Enhancement"
								/>
							</SettingRow>

							{/* ── Sub-toggles (disabled when master is off) ── */}
							<SettingRow
								label="Fix Grammar Basics"
								info="Capitalize the pronoun 'i', restore missing apostrophes in common contractions (dont → don't, cant → can't), and collapse double spaces."
							>
								<Switch
									checked={config.fix_grammar_basics ?? true}
									onCheckedChange={(checked) =>
										updateConfig({ fix_grammar_basics: checked })
									}
									disabled={!aiMasterOn}
									aria-label="Fix Grammar Basics"
								/>
							</SettingRow>

							<SettingRow
								label="Auto-Punctuate"
								info="Add a period at the end of sentences that don't already have terminal punctuation, and insert a comma at natural breath breaks (e.g. before 'and I', 'but you'). Skips URLs, file paths, and code."
							>
								<Switch
									checked={config.auto_punctuate ?? true}
									onCheckedChange={(checked) =>
										updateConfig({ auto_punctuate: checked })
									}
									disabled={!aiMasterOn}
									aria-label="Auto-Punctuate"
								/>
							</SettingRow>

							<SettingRow
								label="Auto-Capitalize"
								info="Capitalize the first letter of each sentence and a small set of proper nouns (weekday and month names, language names). Leaves URLs and existing capitalization untouched."
							>
								<Switch
									checked={config.auto_capitalize ?? true}
									onCheckedChange={(checked) =>
										updateConfig({ auto_capitalize: checked })
									}
									disabled={!aiMasterOn}
									aria-label="Auto-Capitalize"
								/>
							</SettingRow>
						</div>
					</SettingsSection>
				)}

				{/* ── SECTION: Vocabulary Automation (P5) ───────────────── */}
				{vocabVisible && (
					<SettingsSection
						title={vocabSectionTitle}
						description="Suggest vocabulary corrections based on transcription confidence. Off by default — enable to start collecting suggestions."
					>
						<div className="animate-fade-in space-y-0 divide-y divide-border">
							{/* ── Master toggle ── */}
							<SettingRow
								label="Enable Vocabulary Automation"
								info="After each dictation, analyze low-confidence words and suggest vocabulary corrections. Suggestions above the auto-apply threshold are added to your vocabulary automatically; the rest are queued for you to review on the Vocabulary page."
							>
								<Switch
									checked={vocabMasterOn}
									onCheckedChange={(checked) =>
										updateConfig({
											vocabulary_automation_enabled: checked,
										})
									}
									aria-label="Enable Vocabulary Automation"
								/>
							</SettingRow>

							{/* ── Confidence threshold ── */}
							<SettingRow
								label="Suggest-Below Confidence"
								info="Words transcribed with confidence below this threshold are flagged for review. 0.7 is a good default — lower values (e.g. 0.5) flag only very uncertain words; higher values (e.g. 0.85) flag more aggressively but may produce false positives."
							>
								<RangeSlider
									value={config.vocabulary_auto_confidence_threshold ?? 0.7}
									min={0}
									max={1}
									step={0.05}
									onChange={(v) =>
										updateConfigDebounced(
											"vocabulary_auto_confidence_threshold",
											v,
										)
									}
									ariaLabel="Suggest-Below Confidence"
									disabled={!vocabMasterOn}
									suffix=""
								/>
							</SettingRow>

							{/* ── Auto-apply threshold ── */}
							<SettingRow
								label="Auto-Apply Confidence"
								info="Suggestions with confidence at or above this threshold are added to your vocabulary without asking. 0.95 is conservative — only very high-confidence corrections are auto-applied. Set to 1.0 to disable auto-apply entirely (review every suggestion manually)."
							>
								<RangeSlider
									value={config.vocabulary_auto_apply_threshold ?? 0.95}
									min={0}
									max={1}
									step={0.05}
									onChange={(v) =>
										updateConfigDebounced("vocabulary_auto_apply_threshold", v)
									}
									ariaLabel="Auto-Apply Confidence"
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
