// ModelSettingsSection — Post-Processing + LLM Polishing sections of the
// Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "Post-Processing" (Language, Auto Punctuation,
// Text Cleanup, Text Snippets, Vocabulary) and "LLM Polishing" (Enable,
// API Key, API URL, Model, Preset). Behaviour is identical to the
// previous monolithic implementation; both sections are always rendered
// (no search-filter hide-when-empty wrapper, matching the original).

import { useState } from "react";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
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

import type { SettingsSectionSharedProps } from "./types";

const LANGUAGE_OPTIONS = [
	{
		value: "auto",
		label: "Auto-detect",
		description: "Any language — no hallucination filtering",
	},
	{
		value: "en",
		label: "English",
		description: "Enables Latin-script hallucination filter",
	},
	{ value: "zh", label: "Chinese" },
	{ value: "es", label: "Spanish" },
	{ value: "ar", label: "Arabic" },
	{ value: "fr", label: "French" },
	{ value: "ru", label: "Russian" },
	{ value: "pt", label: "Portuguese" },
	{ value: "de", label: "German" },
	{ value: "ja", label: "Japanese" },
	{ value: "ko", label: "Korean" },
	{ value: "it", label: "Italian" },
	{ value: "nl", label: "Dutch" },
	{ value: "pl", label: "Polish" },
	{ value: "tr", label: "Turkish" },
	{ value: "vi", label: "Vietnamese" },
	{ value: "th", label: "Thai" },
	{ value: "hi", label: "Hindi" },
	{ value: "id", label: "Indonesian" },
	{ value: "sv", label: "Swedish" },
	{ value: "da", label: "Danish" },
	{ value: "fi", label: "Finnish" },
	{ value: "no", label: "Norwegian" },
	{ value: "cs", label: "Czech" },
	{ value: "ro", label: "Romanian" },
	{ value: "hu", label: "Hungarian" },
	{ value: "el", label: "Greek" },
	{ value: "he", label: "Hebrew" },
];

const LLM_PRESET_OPTIONS = [
	{ value: "professional", label: "Professional" },
	{ value: "casual", label: "Casual" },
	{ value: "email", label: "Email" },
	{ value: "code", label: "Code" },
] as const;

export function ModelSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
}: SettingsSectionSharedProps) {
	// LLM API key visibility toggle (show/hide the password-style input).
	const [llmKeyVisible, setLlmKeyVisible] = useState(false);

	if (!config) return null;

	return (
		<>
			{/* ── SECTION: Post-Processing ──────────────────────────── */}
			<SettingsSection
				title="Post-Processing"
				description="Cleanup, corrections, and language."
			>
				<SettingRow
					label="Language"
					info="Auto-detect the spoken language, or pick one for better accuracy."
				>
					<Select
						value={config.language || "auto"}
						onValueChange={(v) =>
							updateConfig({ language: v === "auto" ? "" : v })
						}
					>
						<SelectTrigger className="w-44" aria-label="Language">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							{LANGUAGE_OPTIONS.map((lang) => (
								<SelectItem key={lang.value} value={lang.value}>
									<span>{lang.label}</span>
									{lang.description && (
										<span className="ml-2 text-[10px] text-(--text-muted)">
											{lang.description}
										</span>
									)}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				</SettingRow>

				<SettingRow
					label="Auto Punctuation"
					info="Add periods, commas, and question marks automatically."
				>
					<Switch
						checked={config.auto_punctuation ?? false}
						onCheckedChange={(checked) =>
							updateConfig({ auto_punctuation: checked })
						}
						aria-label="Auto Punctuation"
					/>
				</SettingRow>

				<SettingRow
					label="Text Cleanup"
					info="Fix common misspellings, remove repeated words, and capitalize sentences."
				>
					<Switch
						checked={config.text_cleanup_enabled}
						onCheckedChange={(checked) =>
							updateConfig({ text_cleanup_enabled: checked })
						}
						aria-label="Text Cleanup"
					/>
				</SettingRow>

				<SettingRow
					label="Text Snippets"
					info="Use voice commands to insert pre-written text snippets with placeholders."
				>
					<Switch
						checked={config.templates_enabled ?? true}
						onCheckedChange={(checked) =>
							updateConfig({ templates_enabled: checked })
						}
						aria-label="Text Snippets"
					/>
				</SettingRow>

				<SettingRow
					label="Vocabulary"
					info="Custom word replacements so the transcription uses your preferred terms."
				>
					<Switch
						checked={config.vocabulary_enabled ?? true}
						onCheckedChange={(checked) =>
							updateConfig({ vocabulary_enabled: checked })
						}
						aria-label="Vocabulary"
					/>
				</SettingRow>
			</SettingsSection>

			{/* ── SECTION: LLM Polishing ────────────────────────────── */}
			<SettingsSection
				title="LLM Polishing"
				description="AI-powered transcription enhancement."
			>
				<SettingRow
					label="Enable"
					info="Use an AI language model to clean up and improve the transcribed text. Requires an API key."
				>
					<Switch
						checked={config.llm_polish ?? false}
						onCheckedChange={(checked) => updateConfig({ llm_polish: checked })}
						aria-label="LLM Polishing"
					/>
				</SettingRow>

				{config.llm_polish && (
					<div className="animate-fade-in space-y-0 divide-y divide-border">
						<SettingRow
							label="API Key"
							info="Your OpenAI-compatible API key for the polishing service."
						>
							<div className="relative">
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
									onChange={(e) =>
										updateConfigDebounced("llm_api_key", e.target.value)
									}
									placeholder={
										config.llm_api_key === "<redacted>"
											? "•••••••• (configured)"
											: ""
									}
									className="w-56 pr-8"
									aria-label="LLM API Key"
								/>
								<Button
									variant="ghost"
									size="xs"
									onClick={() => setLlmKeyVisible(!llmKeyVisible)}
									className="absolute right-1 top-1/2 -translate-y-1/2 text-xs"
									aria-label={llmKeyVisible ? "Hide API key" : "Show API key"}
								>
									{llmKeyVisible ? "Hide" : "Show"}
								</Button>
							</div>
						</SettingRow>

						<SettingRow
							label="API URL"
							info="The endpoint URL for the AI language model service."
						>
							<Input
								value={
									config.llm_api_url ??
									"https://api.openai.com/v1/chat/completions"
								}
								onChange={(e) =>
									updateConfigDebounced("llm_api_url", e.target.value)
								}
								className="w-64"
								aria-label="LLM API URL"
							/>
						</SettingRow>

						<SettingRow
							label="Model"
							info="The AI model to use for polishing (e.g., gpt-4o-mini)."
						>
							<Input
								value={config.llm_model ?? "gpt-4o-mini"}
								onChange={(e) =>
									updateConfigDebounced("llm_model", e.target.value)
								}
								className="w-44"
								aria-label="LLM Model"
							/>
						</SettingRow>

						<SettingRow
							label="Preset"
							info="The writing style to apply — professional, casual, email, or code."
						>
							<Select
								value={config.llm_preset ?? "professional"}
								onValueChange={(v) => updateConfig({ llm_preset: v })}
							>
								<SelectTrigger className="w-40" aria-label="LLM Preset">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{LLM_PRESET_OPTIONS.map((opt) => (
										<SelectItem key={opt.value} value={opt.value}>
											{opt.label}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</SettingRow>
					</div>
				)}
			</SettingsSection>
		</>
	);
}
