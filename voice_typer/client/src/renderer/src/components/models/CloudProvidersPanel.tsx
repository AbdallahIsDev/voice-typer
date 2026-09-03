/**
 * CloudModelsPanel — cloud ASR providers tab content for the Models page.
 *
 * (UI/UX overhaul 2026-08-20, point 11): rebuilt to follow the SAME
 * structural pattern as the Local Models tab:
 *   • Each provider (OpenAI, Groq, Deepgram) is a collapsible group
 *     header inside the shared `ModelGroupList` accordion primitives
 *     (same icon, name, spacing, hover behavior as the local-model
 *     families).
 *   • Expanding a provider group reveals its API model as a list row
 *     (name + metadata tags) with a "Configure" action that reveals
 *     the API key input + Save Key + Test Connection controls —
 *     the existing API-key-entry UI, now triggered from within the
 *     consistent group/list pattern instead of being permanently
 *     visible in a separate card style.
 *   • Tab renamed "Cloud Providers" → "Cloud Models" (point 12); the
 *     heading + description below the tab switcher match.
 *
 * The visual language (borders, spacing, backgrounds, icons,
 * typography) is fully unified with Local Models because BOTH tabs
 * compose the same `ModelGroupList` components — future style updates
 * happen in one place.
 *
 * Pure presentational — receives all state + handlers as props from
 * `useModelLifecycle`.
 */

import {
	Loading03Icon,
	Settings03Icon,
	Shield01Icon,
	SparklesIcon,
	ViewIcon,
	ViewOffIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type React from "react";
import { useState } from "react";
import { KeyringStatusBadge } from "@/components/common/KeyringStatusBadge";
import { FamilyLogo } from "@/components/models/FamilyLogo";
import {
	MetadataTag,
	ModelGroupAccordion,
	ModelGroupContent,
	ModelGroupItem,
	ModelGroupTrigger,
	ModelVariantRow,
} from "@/components/models/ModelGroupList";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { consentKeyFor } from "@/hooks/models/useCloudProviders";
import type { ApiTestResult } from "@/hooks/useModelLifecycle";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import {
	type CloudProvider,
	formatModelDisplayName,
	getProviderLabel,
} from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

export interface CloudProvidersPanelProps {
	config: VoiceTyperConfig | null;
	cloudProviders: readonly CloudProvider[];
	apiKeys: Record<string, string>;
	testResults: Record<string, ApiTestResult>;
	onApiKeyChange: (provider: string, value: string) => void;
	onSaveApiKey: (provider: string) => void;
	onTestConnection: (provider: string) => void;
	onConsentChange: (provider: string, granted: boolean) => void;
	/** : clear the test result for a single provider. Wired to
	 * the API-key Input's onChange so stale "Success" badges don't
	 * linger after the user edits the key. Optional so the panel can
	 * be mounted without it (the consumer is responsible for wiring
	 * it; without it, stale results simply don't auto-clear). */
	onClearTestResult?: (provider: string) => void;
}

export function CloudProvidersPanel({
	config,
	cloudProviders,
	apiKeys,
	testResults,
	onApiKeyChange,
	onSaveApiKey,
	onTestConnection,
	onConsentChange,
	onClearTestResult,
}: CloudProvidersPanelProps) {
	// Which providers' API-key forms are currently revealed (via their
	// "Configure" action). A SET so multiple provider forms can be open
	// at once — matching the accordion's `type="multiple"` behavior.
	// Purely local UI state.
	const [configuredProviders, setConfiguredProviders] = useState<string[]>([]);

	const toggleConfigured = (key: string) => {
		setConfiguredProviders((prev) =>
			prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
		);
	};

	return (
		<div className="flex flex-col gap-4">
			<p className="text-sm text-(--text-muted)">
				{t("models.cloudModelsDescription")}
			</p>
			<ModelGroupAccordion type="multiple">
				{cloudProviders.map((provider) => {
					const isConfigured = configuredProviders.includes(provider.key);
					return (
						<ModelGroupItem key={provider.key} value={provider.key}>
							<ModelGroupTrigger>
								{provider.key === "groq" ? (
									<HugeiconsIcon
										icon={Shield01Icon}
										strokeWidth={2}
										className="h-4 w-4 text-accent"
									/>
								) : (
									<FamilyLogo family={provider.key} />
								)}
								{getProviderLabel(provider.key)}
							</ModelGroupTrigger>
							<ModelGroupContent>
								{/* One list row per provider: the API model
                                                                    name + descriptive tags + the Configure
                                                                    action (point 11). */}
								<ModelVariantRow
									name={formatModelDisplayName(provider.model)}
									meta={
										<>
											<MetadataTag>{t("models.cloud.tagCloud")}</MetadataTag>
											<MetadataTag>{t("models.card.multilingual")}</MetadataTag>
										</>
									}
									actions={
										<Button
											variant="outline"
											size="sm"
											className="gap-2"
											onClick={() => toggleConfigured(provider.key)}
											aria-expanded={isConfigured}
											aria-label={
												isConfigured
													? t("models.cloud.hideConfigureAria", {
															provider: getProviderLabel(provider.key),
														})
													: t("models.cloud.configureAria", {
															provider: getProviderLabel(provider.key),
														})
											}
										>
											<HugeiconsIcon
												icon={Settings03Icon}
												strokeWidth={2}
												className="h-4 w-4"
											/>
											{isConfigured
												? t("models.cloud.hideConfigure")
												: t("models.cloud.configure")}
										</Button>
									}
								/>
								{isConfigured && (
									<div className="p-4">
										<ProviderConfigForm
											provider={provider}
											config={config}
											apiKeyValue={apiKeys[provider.key] ?? ""}
											testResult={testResults[provider.key]}
											onApiKeyChange={(v) => {
												onApiKeyChange(provider.key, v);
												//clear the stale test result whenever the
												// user edits the key — otherwise the previous
												// "Success" badge stays visible during a re-test.
												onClearTestResult?.(provider.key);
											}}
											onSaveApiKey={() => onSaveApiKey(provider.key)}
											onTestConnection={() => onTestConnection(provider.key)}
											onConsentChange={(granted) =>
												onConsentChange(provider.key, granted)
											}
										/>
									</div>
								)}
							</ModelGroupContent>
						</ModelGroupItem>
					);
				})}
			</ModelGroupAccordion>
		</div>
	);
}

// ── Sub-component: API-key entry + test + consent form ────────────────
//
// The existing API-key-entry UI (extracted from the pre-overhaul
// provider card), triggered by the Configure action.

interface ProviderConfigFormProps {
	provider: CloudProvider;
	config: VoiceTyperConfig | null;
	apiKeyValue: string;
	testResult?: ApiTestResult;
	onApiKeyChange: (value: string) => void;
	onSaveApiKey: () => void;
	onTestConnection: () => void;
	onConsentChange: (granted: boolean) => void;
}

function ProviderConfigForm({
	provider,
	config,
	apiKeyValue,
	testResult,
	onApiKeyChange,
	onSaveApiKey,
	onTestConnection,
	onConsentChange,
}: ProviderConfigFormProps) {
	const consentKey = consentKeyFor(provider.key);
	const consentGranted = Boolean(
		(config?.[consentKey] as boolean | undefined) ?? false,
	);
	const showConsent = Boolean(apiKeyValue) || consentGranted;

	// Show/hide toggle for the API key input. Default is
	// hidden (type="password"); clicking the eye-icon button reveals
	// the plain-text value for verification. The reveal state is purely
	// local — every ProviderConfigForm instance owns its own toggle so
	// opening one provider's key does NOT reveal another's.
	const [revealKey, setRevealKey] = useState(false);

	//pending state — disable the Test button + show a spinner.
	const isPending = testResult?.status === "pending";
	//disable Save Key when the input is empty (prevents
	// silently clobbering a stored secret with the empty string that
	// `safeApiKey` substitutes for the `<redacted>` sentinel on every
	// config fetch).
	const saveDisabled = !apiKeyValue.trim();

	return (
		<div className="flex flex-col gap-4 rounded-lg border border-border/5 bg-(--bg) p-4">
			<div className="flex flex-col gap-2">
				<div className="flex items-center gap-2">
					<label
						htmlFor={`api-key-input-${provider.key}`}
						className="text-sm font-medium text-(--text-primary)"
					>
						{t("models.cloud.apiKey")}
					</label>
					<KeyringStatusBadge status={config?.keyring_status} compact />
				</div>
				<div className="relative w-full max-w-md">
					<Input
						id={`api-key-input-${provider.key}`}
						type={revealKey ? "text" : "password"}
						value={apiKeyValue}
						onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
							onApiKeyChange(e.target.value)
						}
						placeholder={t("models.apiKeyPlaceholder")}
						className="w-full pe-10"
						autoComplete="off"
						spellCheck={false}
					/>
					<button
						type="button"
						onClick={() => setRevealKey((v) => !v)}
						className="absolute inset-e-2 top-1/2 -translate-y-1/2 inline-flex size-6 items-center justify-center rounded-md text-(--text-muted) hover:text-(--text-primary) hover:bg-foreground/5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						aria-label={
							revealKey
								? t("models.cloud.apiKeyHideAria", {
										provider: getProviderLabel(provider.key),
									})
								: t("models.cloud.apiKeyShowAria", {
										provider: getProviderLabel(provider.key),
									})
						}
						aria-pressed={revealKey}
					>
						<HugeiconsIcon
							icon={revealKey ? ViewOffIcon : ViewIcon}
							strokeWidth={2}
							className="h-4 w-4"
							aria-hidden="true"
						/>
					</button>
				</div>
				{/* Subtle format hint below the input so users
                                        know the expected key shape without trial-and-error.
                                        Generic hint covers the common provider key conventions;
                                        the per-provider text is intentionally minimal so it
                                        doesn't mislead when a provider changes their key
                                        format. */}
				<p className="text-xs text-(--text-muted)">
					{t("models.cloud.apiKeyFormatHint", {
						provider: getProviderLabel(provider.key),
					})}
				</p>
			</div>
			<div className="flex flex-wrap items-center gap-3">
				<Button
					variant="default"
					size="sm"
					onClick={onSaveApiKey}
					//disable when the input is empty. Prevents the
					// "Save Key → clobber stored secret with ''" data-loss
					//path documented in the  finding.
					disabled={saveDisabled}
					aria-label={t("models.cloud.saveKeyAria", {
						provider: getProviderLabel(provider.key),
					})}
				>
					{t("models.cloud.saveKey")}
				</Button>
				<Button
					variant="outline"
					size="sm"
					className="gap-2"
					onClick={onTestConnection}
					//disable while a test is in flight so concurrent
					// clicks don't fire overlapping fetches (race conditions
					// on `setTestResults`).
					disabled={isPending}
					aria-busy={isPending}
					aria-label={t("models.cloud.testConnectionAria", {
						provider: getProviderLabel(provider.key),
					})}
				>
					<HugeiconsIcon
						//swap the static SparklesIcon for a spinning
						// Loading03Icon while the test is in flight so users
						// get immediate visual feedback that the click
						// registered (the fetch can take 500ms–5s).
						icon={isPending ? Loading03Icon : SparklesIcon}
						strokeWidth={2}
						className={cn("h-4 w-4", isPending && "animate-spin")}
					/>
					{t("models.cloud.testConnection")}
				</Button>
				{testResult && (
					<output
						//aria-live=polite so SR
						// users hear the test-connection outcome as it arrives
						// (no manual focus required). <output> is the semantic
						// element for role=status — it's a proper live region
						// with the correct SR semantics.
						aria-live="polite"
						className={cn(
							"text-xs",
							testResult.status === "success"
								? "text-primary"
								: testResult.status === "failure"
									? "text-destructive"
									: //`text-[(--text-muted)]` is invalid Tailwind
										// v4 syntax. The canonical form is
										// `text-(--text-muted)` — matches every other call
										// site in the codebase.
										"text-(--text-muted)",
						)}
					>
						{testResult.message}
					</output>
				)}
			</div>
			{showConsent && (
				<div className="rounded-lg border border-border/5 bg-(--bg-subtle) p-4">
					<div className="flex items-start justify-between gap-4">
						<div className="flex flex-1 flex-col gap-2">
							<div className="flex flex-col gap-1">
								<h4 className="text-sm font-semibold text-(--text-primary)">
									{t("models.cloud.consentTitle")}
								</h4>
								<p className="text-xs leading-relaxed text-(--text-muted)">
									{t("models.cloud.consentDescription", {
										provider: getProviderLabel(provider.key),
									})}
								</p>
							</div>
							<p className="text-xs text-(--text-muted)">
								{t("models.cloud.statusLabel")}{" "}
								{consentGranted ? (
									<span className="font-medium text-success">
										{t("models.cloud.consentGrantedStatus")}
									</span>
								) : (
									<span className="font-medium text-warning">
										{t("models.cloud.consentNotGrantedStatus")}
									</span>
								)}
							</p>
						</div>
						<Switch
							checked={consentGranted}
							onCheckedChange={onConsentChange}
							aria-label={t("models.cloud.consentAria", {
								provider: getProviderLabel(provider.key),
							})}
						/>
					</div>
				</div>
			)}
		</div>
	);
}

// ── Local helpers ─────────────────────────────────────────────────────
//
// `consentKeyFor` is imported from the hook (single source of truth) —
// a local duplicate drifted if a provider was ever added.
