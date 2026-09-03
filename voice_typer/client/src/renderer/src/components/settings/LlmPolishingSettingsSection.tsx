// LlmPolishingSettingsSection — the LLM Polishing section of the
// Settings surface.
//
// Extracted from the former ModelSettingsSection (which stacked the
// Post-Processing and LLM Polishing cards on one page) so each domain
// gets its own focused section page (settingsAI groups this card with
// AI Enhancement + Vocabulary Automation). Renders one SettingsSection
// block: "LLM Polishing" (Enable, API Key, API URL, Model, Preset).
// Behaviour is identical to the previous combined implementation,
// including the point-of-use consent gate on the master toggle and the
// URL-format validation draft state.

import { memo, useEffect, useRef, useState } from "react";
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
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

const LLM_PRESET_OPTIONS = [
	{ value: "professional", labelKey: "settings.presetProfessional" },
	{ value: "casual", labelKey: "settings.presetCasual" },
	{ value: "email", labelKey: "settings.presetEmail" },
	{ value: "code", labelKey: "settings.presetCode" },
] as const;

// URL-format validation for the LLM API URL (mirrors the server's
// `_make_url_validator`): must parse as an absolute http/https URL.
// An empty value is VALID here — the input falls back to the default
// endpoint server-side and the placeholder shows it.
function isValidLlmApiUrl(value: string): boolean {
	if (value.trim() === "") return true;
	try {
		const parsed = new URL(value);
		return parsed.protocol === "http:" || parsed.protocol === "https:";
	} catch {
		return false;
	}
}

export const LlmPolishingSettingsSection = memo(
	function LlmPolishingSettingsSection({
		config,
		updateConfig,
		updateConfigDebounced,
		isVisible,
	}: SettingsSectionSharedProps) {
		// LLM API key visibility toggle (show/hide the password-style input).
		const [llmKeyVisible, setLlmKeyVisible] = useState(false);
		// URL-format validation state. Typing is NEVER blocked (the debounced
		// save fires as before — the server allowlist re-validates); the
		// inline error appears on blur (and stays while the value is
		// invalid) so the user learns the format contract without being
		// mid-keystroke interrupted. The draft mirrors the live input value
		// so blur validates what the user actually typed, not the last
		// committed config snapshot.
		const [showUrlError, setShowUrlError] = useState(false);
		const [urlDraft, setUrlDraft] = useState<string | null>(null);
		// Whether the URL input currently holds focus. While the user is
		// typing, an external config push (or our own debounced save echo)
		// must never clobber the draft mid-edit.
		const urlInputFocusedRef = useRef(false);
		const llmApiUrlValue = urlDraft ?? config?.llm_api_url ?? "";
		const llmApiUrlInvalid = showUrlError && !isValidLlmApiUrl(llmApiUrlValue);

		// Reset-to-defaults / config_changed pushes can change the
		// committed `llm_api_url` behind the dialog. A stale draft would
		// keep showing the pre-reset text, so when the committed value
		// changes while the input is NOT focused, drop the draft (the
		// input then renders the new committed value). While focused the
		// draft is protected — our own debounced echo landing mid-typing
		// must not snap the input back and lose keystrokes; the next blur
		// reconciles against the committed value.
		const lastCommittedUrlRef = useRef(config?.llm_api_url ?? "");
		useEffect(() => {
			const committed = config?.llm_api_url ?? "";
			if (committed === lastCommittedUrlRef.current) return;
			lastCommittedUrlRef.current = committed;
			if (!urlInputFocusedRef.current) {
				setUrlDraft(null);
			}
		}, [config?.llm_api_url]);

		const handleApiKeyChange = (e: React.ChangeEvent<HTMLInputElement>) =>
			updateConfigDebounced("llm_api_key", e.target.value);

		const handleToggleLlmKey = () => setLlmKeyVisible(!llmKeyVisible);

		const handleApiUrlChange = (e: React.ChangeEvent<HTMLInputElement>) => {
			setUrlDraft(e.target.value);
			updateConfigDebounced("llm_api_url", e.target.value);
		};

		const handleApiUrlFocus = () => {
			urlInputFocusedRef.current = true;
		};

		const handleApiUrlBlur = () => {
			urlInputFocusedRef.current = false;
			setShowUrlError(!isValidLlmApiUrl(llmApiUrlValue));
		};

		const handleModelChange = (e: React.ChangeEvent<HTMLInputElement>) =>
			updateConfigDebounced("llm_model", e.target.value);

		const t = useT();

		if (!config) return <SettingsSkeleton rows={3} />;

		// Point-of-use consent gate: turning LLM polishing ON sends
		// transcribed text to the configured LLM provider, which requires
		// `llm_polish_consent`. When the consent is missing, ask via the
		// SHARED consent dialog at this exact moment instead of enabling a
		// flow that would silently refuse (or nag) on every transcription:
		//   • Allow → persists `llm_polish_consent=true`, then enables the
		//     feature (the dialog's retry below);
		//   • Cancel → the toggle stays off — no consent, no enablement;
		//   • already granted (or switching OFF) → behave as before.
		const handleLlmPolishChange = (checked: boolean) => {
			if (!checked || config.llm_polish_consent) {
				updateConfig({ llm_polish: checked });
				return;
			}
			openConsentGate({
				consentField: "llm_polish_consent",
				bodyKey: consentBodyKey("llm_polish_consent"),
				onAllow: () => updateConfig({ llm_polish: true }),
			});
		};
		const handleLlmPresetChange = (v: string) =>
			updateConfig({ llm_preset: v });

		//section-level visibility check for LLM Polishing section. The
		// title constant feeds BOTH the `<SettingsSection title>` prop AND
		// the `isVisible` third parameter, so search matches the heading
		// the user actually sees.
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

		if (!llmPolishingVisible) return null;

		return (
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
					<div className="animate-fade-in flex flex-col gap-0 divide-y divide-border/5">
						<SettingRow
							label={t("settings.apiKey")}
							info={t("settings.apiKeyInfo")}
						>
							<div className="relative flex flex-col gap-2">
								{/*keyring status indicator next to the LLM API
								 * key input. Shows a green lock icon when the secret
								 * is stored in the OS keychain, or an amber warning
								 * when only the plaintext fallback is available. */}
								<div>
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
									className="absolute end-1 top-1/2 -translate-y-1/2 text-xs"
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
							<div className="flex flex-col items-end gap-1">
								<Input
									value={
										urlDraft ??
										config.llm_api_url ??
										"https://api.openai.com/v1/chat/completions"
									}
									onChange={handleApiUrlChange}
									onFocus={handleApiUrlFocus}
									onBlur={handleApiUrlBlur}
									placeholder={t("settings.apiUrlPlaceholder")}
									className="w-64"
									aria-label={t("settings.apiUrl")}
									aria-invalid={llmApiUrlInvalid || undefined}
									aria-describedby={
										llmApiUrlInvalid ? "llm-api-url-error" : undefined
									}
								/>
								{llmApiUrlInvalid && (
									<span
										id="llm-api-url-error"
										role="alert"
										data-testid="llm-api-url-error"
										className="text-xs text-destructive"
									>
										{t("settings.apiUrlInvalid")}
									</span>
								)}
							</div>
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
		);
	},
);
