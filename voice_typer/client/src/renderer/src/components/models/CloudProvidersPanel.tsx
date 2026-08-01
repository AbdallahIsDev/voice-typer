/**
 * CloudProvidersPanel — cloud ASR providers tab content for the Models page.
 *
 *  fix #1: extracted from `pages/Models.tsx`. Renders the three
 * cloud ASR provider cards (OpenAI / Groq / Deepgram) with:
 *   • API key input (with unique per-provider HTML id, MDL-5).
 *   • "Save Key" + "Test Connection" buttons.
 *   • Audio-transmission consent Switch (only shown when an API key
 *     is set OR consent has been granted — preserving the original
 *     progressive-disclosure behavior).
 *
 * Pure presentational — receives all state + handlers as props from
 * `useModelLifecycle`.
 *
 * : the "Save Key" button is disabled when the input is empty
 * (`!apiKeyValue.trim()`). Prevents silently clobbering a stored
 * secret with the empty string that `safeApiKey` substitutes for the
 * `<redacted>` sentinel on every config fetch.
 *
 * : the "Test Connection" button shows a spinner + is disabled
 * while a test is in flight (`testResult?.status === "pending"`).
 * The result span exposes `role="status"` + `aria-live="polite"` so
 * SR users hear the outcome. Stale results are cleared via
 * `onClearTestResult` whenever the API-key Input changes.
 */
import {
	Loading03Icon,
	Shield01Icon,
	SparklesIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type React from "react";
import { KeyringStatusBadge } from "@/components/common/KeyringStatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { ApiTestResult } from "@/hooks/useModelLifecycle";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import { type CloudProvider, getProviderLabel } from "@/lib/utils/models";
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
	return (
		<div className="space-y-4">
			<h2 className="font-sans text-lg font-semibold text-(--text-primary)">
				{t("models.cloudProviders")}
			</h2>
			<p className="text-sm text-(--text-muted) -mt-3">
				{t("models.cloudProvidersDescription")}
			</p>
			<div className="space-y-4">
				{cloudProviders.map((provider) => (
					<ProviderCard
						key={provider.key}
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
				))}
			</div>
		</div>
	);
}

// ── Sub-component: single provider card ───────────────────────────────

interface ProviderCardProps {
	provider: CloudProvider;
	config: VoiceTyperConfig | null;
	apiKeyValue: string;
	testResult?: ApiTestResult;
	onApiKeyChange: (value: string) => void;
	onSaveApiKey: () => void;
	onTestConnection: () => void;
	onConsentChange: (granted: boolean) => void;
}

function ProviderCard({
	provider,
	config,
	apiKeyValue,
	testResult,
	onApiKeyChange,
	onSaveApiKey,
	onTestConnection,
	onConsentChange,
}: ProviderCardProps) {
	const consentKey = consentKeyFor(provider.key);
	const consentGranted = Boolean(
		(config?.[consentKey] as boolean | undefined) ?? false,
	);
	const showConsent = Boolean(apiKeyValue) || consentGranted;

	//pending state — disable the Test button + show a spinner.
	const isPending = testResult?.status === "pending";
	//disable Save Key when the input is empty (prevents
	// silently clobbering a stored secret with the empty string that
	// `safeApiKey` substitutes for the `<redacted>` sentinel on every
	// config fetch).
	const saveDisabled = !apiKeyValue.trim();

	return (
		<div className="rounded-xl border border-border bg-(--bg-subtle) p-6">
			<div className="flex items-center gap-2.5 mb-4">
				<HugeiconsIcon
					icon={Shield01Icon}
					strokeWidth={2}
					className="h-4 w-4 text-accent"
				/>
				<h3 className="text-base font-semibold text-(--text-primary)">
					{t("models.cloud.providerSettings", {
						provider: getProviderLabel(provider.key),
					})}
				</h3>
			</div>
			<div className="mb-4">
				<div className="mb-1.5 flex items-center gap-2">
					<label
						htmlFor={`api-key-input-${provider.key}`}
						className="text-sm font-medium text-(--text-primary)"
					>
						{t("models.cloud.apiKey")}
					</label>
					<KeyringStatusBadge status={config?.keyring_status} compact />
				</div>
				<Input
					id={`api-key-input-${provider.key}`}
					type="password"
					value={apiKeyValue}
					onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
						onApiKeyChange(e.target.value)
					}
					placeholder={t("models.apiKeyPlaceholder")}
					className="w-full max-w-md"
				/>
			</div>
			<div className="flex items-center gap-3">
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
					<span
						//aria-live=polite so SR
						// users hear the test-connection outcome as it arrives
						// (no manual focus required). role=status is implicit
						// on <span> when combined with aria-live — but the
						// <span> isn't a semantic live-region element per se,
						// so we downgrade to a <p> which has implicit
						// role=status naturally.
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
					</span>
				)}
			</div>
			{showConsent && (
				<div className="mt-4 rounded-lg border border-border bg-(--bg) p-4">
					<div className="flex items-start justify-between gap-4">
						<div className="flex-1">
							<h4 className="text-sm font-semibold text-(--text-primary)">
								{t("models.cloud.consentTitle")}
							</h4>
							<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
								{t("models.cloud.consentDescription", {
									provider: getProviderLabel(provider.key),
								})}
							</p>
							<p className="mt-2 text-xs text-(--text-muted)">
								{t("models.cloud.statusLabel")}{" "}
								{consentGranted ? (
									<span className="font-medium text-emerald-500">
										{t("models.cloud.consentGrantedStatus")}
									</span>
								) : (
									<span className="font-medium text-amber-500">
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

// ── Local helpers (mirror the hook's internal helpers — kept local so
//     the panel can render the consent UI without depending on the
//     hook's internals). ────────────────────────────────────────────────

function consentKeyFor(provider: string): keyof VoiceTyperConfig {
	if (provider === "openai") return "cloud_openai_consent";
	if (provider === "groq") return "cloud_groq_consent";
	return "cloud_deepgram_consent";
}
