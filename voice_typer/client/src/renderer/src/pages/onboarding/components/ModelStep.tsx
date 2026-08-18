import type { Ref } from "react";
import { FamilyLogo } from "@/components/models/FamilyLogo";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";
import { formatVram } from "@/lib/format";
import { formatModelSpeed } from "@/lib/utils/models";
import type { BackendChoice } from "../hooks/useOnboardingWizard";
import { HEADING_CLASS } from "../lib/constants";
import type { ModelOption } from "../lib/types";

// The onboarding wizard only offers the curated MODEL_OPTIONS subset
// (currently the multilingual Whisper variants + Parakeet), so the
// brand strip is derived from the options actually present rather than
// a static list — a future option (e.g. Qwen) shows up automatically.
// Mirrors the Models page family grouping: whisper → OpenAI, parakeet →
// NVIDIA, qwen → Qwen.
function familyForModelName(name: string): string | null {
	if (name === "qwen") return "qwen";
	if (name === "parakeet") return "parakeet";
	if (name === "tiny" || name === "large-v3" || name === "large-v3-turbo")
		return "whisper";
	return null;
}

// Family display labels — brand names (proper nouns, kept literal like
// the Models page family headers).
const FAMILY_STRIP_LABELS: Record<string, string> = {
	whisper: "Whisper",
	qwen: "Qwen",
	parakeet: "Nvidia",
};

export interface ModelStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	modelOptions: ModelOption[];
	selectedModel: string;
	setSelectedModel: (v: string) => void;
	// Local-vs-cloud choice (Model step). The app NEVER auto-downloads a
	// model — the user either picks a local model and clicks Download
	// explicitly, or connects a cloud transcription API.
	selectedBackend: BackendChoice;
	setSelectedBackend: (v: BackendChoice) => void;
	// Local branch: HuggingFace consent gates the explicit download.
	hfConsent: boolean;
	setHfConsent: (v: boolean) => void;
	downloadingModel: string | null;
	downloadProgress: number;
	downloadFailed: boolean;
	onDownload: () => Promise<void>;
	// Cloud branch: provider + API key + consent (persisted on Continue).
	cloudProvider: string;
	setCloudProvider: (v: string) => void;
	cloudApiKey: string;
	setCloudApiKey: (v: string) => void;
	cloudConsent: boolean;
	setCloudConsent: (v: boolean) => void;
}

const CLOUD_PROVIDERS = ["openai", "groq", "deepgram"] as const;

function providerLabel(provider: string): string {
	if (provider === "openai") return t("models.providers.openai.label");
	if (provider === "groq") return t("models.providers.groq.label");
	return t("models.providers.deepgram.label");
}

/**
 * : derive the language-coverage badge key for a model option.
 *
 * `languages` follows the same convention as
 * `ModelMetadata.supported_languages` in `lib/utils/models.ts`:
 *   - `undefined` → field not sent by backend → no badge (caller skips)
 *   - `null`      → all languages (multilingual)
 *   - `[]`        → treat as "no explicit list" → multilingual fallback
 *   - `['en']` (length 1, only English) → English-only badge
 *   - any other non-empty array → multilingual badge
 */
function languageBadgeKey(
	languages: string[] | null | undefined,
): string | null {
	if (languages === undefined) return null;
	if (languages === null) return "onboarding.multilingualBadge";
	if (languages.length === 0) return "onboarding.multilingualBadge";
	const onlyEnglish = languages.every((l) => l.toLowerCase() === "en");
	return onlyEnglish
		? "onboarding.englishOnlyBadge"
		: "onboarding.multilingualBadge";
}

/** Styling for the two backend-choice cards (radio group). */
function backendCardClass(active: boolean): string {
	return [
		"flex flex-col items-start gap-1 rounded-lg border p-4 text-left",
		"transition-colors duration-150",
		active
			? "border-accent bg-accent/5"
			: "border-border/10 bg-(--bg-subtle) hover:border-accent/50",
	].join(" ");
}

export function ModelStep({
	headingRef,
	modelOptions,
	selectedModel,
	setSelectedModel,
	selectedBackend,
	setSelectedBackend,
	hfConsent,
	setHfConsent,
	downloadingModel,
	downloadProgress,
	downloadFailed,
	onDownload,
	cloudProvider,
	setCloudProvider,
	cloudApiKey,
	setCloudApiKey,
	cloudConsent,
	setCloudConsent,
}: ModelStepProps) {
	const isDownloading = downloadingModel !== null;
	const progressPct = Math.round(downloadProgress);
	// Families present in the offered local models (whisper → OpenAI,
	// parakeet → NVIDIA, qwen → Qwen) — drives the brand strip above
	// the picker. Derived from the options so a catalog change
	// automatically updates the strip.
	const localFamilies = Array.from(
		new Set(
			modelOptions
				.map((m) => familyForModelName(m.name))
				.filter((f): f is string => f !== null),
		),
	);

	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.modelTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.modelDescription")}
			</p>

			{/* Local vs cloud choice. This is the single place where the
                            user decides how transcription will run — the app never
                            downloads a model on its own. */}
			<div
				role="radiogroup"
				aria-label={t("onboarding.backendAria")}
				className="grid grid-cols-1 gap-3 sm:grid-cols-2"
			>
				{/* biome-ignore lint/a11y/useSemanticElements: custom-styled radio card — a native <input type="radio"> cannot render the card layout; role="radio" + aria-checked in a radiogroup is the correct ARIA pattern */}
				<button
					type="button"
					role="radio"
					aria-checked={selectedBackend === "local"}
					onClick={() => setSelectedBackend("local")}
					className={backendCardClass(selectedBackend === "local")}
					data-testid="onboarding-backend-local"
				>
					<span className="text-sm font-medium text-(--text-primary)">
						{t("onboarding.backendLocalLabel")}
					</span>
					<span className="text-xs text-(--text-muted)">
						{t("onboarding.backendLocalDescription")}
					</span>
				</button>
				{/* biome-ignore lint/a11y/useSemanticElements: custom-styled radio card — a native <input type="radio"> cannot render the card layout; role="radio" + aria-checked in a radiogroup is the correct ARIA pattern */}
				<button
					type="button"
					role="radio"
					aria-checked={selectedBackend === "cloud"}
					onClick={() => setSelectedBackend("cloud")}
					className={backendCardClass(selectedBackend === "cloud")}
					data-testid="onboarding-backend-cloud"
				>
					<span className="text-sm font-medium text-(--text-primary)">
						{t("onboarding.backendCloudLabel")}
					</span>
					<span className="text-xs text-(--text-muted)">
						{t("onboarding.backendCloudDescription")}
					</span>
				</button>
			</div>

			{selectedBackend === "local" ? (
				<div className="mt-5 space-y-4">
					{/* Brand strip — lets the user see which families the
                                            local models come from BEFORE opening the picker.
                                            Families are derived from the options list so the
                                            strip stays accurate if the catalog changes. */}
					{localFamilies.length > 0 && (
						<div
							className="flex items-center gap-3"
							data-testid="onboarding-family-strip"
						>
							<span className="text-xs text-(--text-muted)">
								{t("onboarding.familyStripLabel")}
							</span>
							<div className="flex items-center gap-4">
								{localFamilies.map((family) => (
									<span key={family} className="flex items-center gap-1.5">
										<FamilyLogo family={family} />
										<span className="text-xs font-medium text-(--text-secondary)">
											{FAMILY_STRIP_LABELS[family] ?? family}
										</span>
									</span>
								))}
							</div>
						</div>
					)}

					<Select value={selectedModel} onValueChange={setSelectedModel}>
						<SelectTrigger
							className="w-full"
							aria-label={t("onboarding.modelSelectAria", {
								name: selectedModel,
							})}
						>
							<SelectValue
								placeholder={t("onboarding.modelSelectAria", {
									name: selectedModel,
								})}
							/>
						</SelectTrigger>
						<SelectContent>
							{modelOptions.map((m) => {
								const langKey = languageBadgeKey(m.languages);
								return (
									<SelectItem
										key={m.name}
										value={m.name}
										textValue={`${m.description} — ${m.size} (${formatModelSpeed(m.speed)})`}
									>
										<span className="flex flex-wrap items-center gap-1.5">
											<span>
												{m.description} — {m.size} ({formatModelSpeed(m.speed)})
											</span>
											{/*per-option badge row showing VRAM
                                                                                        requirement and language coverage. Both
                                                                                        badges are optional — older backends don't
                                                                                        return these fields. */}
											{m.vram_gb != null && (
												<span className="rounded-full bg-bg-subtle px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-(--text-muted)">
													{t("onboarding.vramBadge", {
														vram: formatVram(m.vram_gb * 1024),
													})}
												</span>
											)}
											{langKey != null && (
												<span className="rounded-full bg-bg-subtle px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-(--text-muted)">
													{t(langKey)}
												</span>
											)}
										</span>
									</SelectItem>
								);
							})}
						</SelectContent>
					</Select>

					{/* HuggingFace consent — gates the EXPLICIT download. */}
					<div className="rounded-lg border border-border/10 bg-(--bg-subtle) p-4">
						<label
							className="flex items-start gap-3 text-sm"
							htmlFor="onboarding-hf-consent"
						>
							<Checkbox
								id="onboarding-hf-consent"
								className="mt-0.5 cursor-pointer"
								checked={hfConsent}
								onCheckedChange={(v) => setHfConsent(v === true)}
								aria-label={t("onboarding.consentHuggingFace")}
								data-testid="onboarding-hf-consent"
							/>
							<span className="flex-1">
								<span className="block font-medium text-(--text-primary)">
									{t("onboarding.consentHuggingFace")}
								</span>
								<span className="mt-1 block text-xs text-(--text-muted)">
									{t("onboarding.consentHuggingFaceInfo")}
								</span>
							</span>
						</label>
					</div>

					{/* Explicit download area — the ONLY way a model is
                                            downloaded from this wizard. */}
					{isDownloading ? (
						<div
							role="progressbar"
							aria-valuenow={progressPct}
							aria-valuemin={0}
							aria-valuemax={100}
							aria-label={t("onboarding.downloadProgressAria", {
								percent: String(progressPct),
							})}
							className="rounded-lg border border-accent/40 bg-accent/5 p-3"
							data-testid="onboarding-download-progress"
						>
							<div className="mb-1.5 flex items-center justify-between text-xs text-(--text-secondary)">
								<span className="flex items-center gap-2">
									<span
										className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
										role="presentation"
									/>
									{t("onboarding.downloadingModel")}
								</span>
								<span>{progressPct}%</span>
							</div>
							<div className="h-1.5 w-full rounded-full bg-(--bg-subtle)">
								<div
									className="h-1.5 rounded-full bg-accent transition-all duration-300"
									style={{ width: `${Math.min(100, downloadProgress)}%` }}
								/>
							</div>
						</div>
					) : downloadFailed ? (
						<div
							className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-(--text-secondary)"
							data-testid="onboarding-download-error"
						>
							<p className="mb-2 text-(--text-secondary)">
								{t("onboarding.downloadFailedHint")}
							</p>
							<Button
								type="button"
								variant="secondary"
								size="sm"
								onClick={() => void onDownload()}
								aria-label={t("onboarding.downloadModelAria")}
							>
								{t("onboarding.downloadRetry")}
							</Button>
						</div>
					) : (
						<div className="flex items-center gap-3">
							<Button
								type="button"
								variant="default"
								onClick={() => void onDownload()}
								disabled={!hfConsent}
								aria-label={t("onboarding.downloadModelAria")}
								data-testid="onboarding-download-button"
							>
								{t("onboarding.downloadModel")}
							</Button>
							<span className="text-xs text-(--text-muted)">
								{t("onboarding.modelDownloadingHint")}
							</span>
						</div>
					)}
				</div>
			) : (
				<div className="mt-5 space-y-4">
					{/* Cloud provider selection — mirrors the Models page
                                            cloud tab (same config fields). */}
					<div>
						<label
							htmlFor="onboarding-cloud-provider"
							className="mb-1 block text-sm font-medium text-(--text-primary)"
						>
							{t("onboarding.cloudProviderLabel")}
						</label>
						<Select value={cloudProvider} onValueChange={setCloudProvider}>
							<SelectTrigger
								id="onboarding-cloud-provider"
								className="w-full"
								aria-label={t("onboarding.cloudProviderLabel")}
							>
								<SelectValue placeholder={providerLabel(cloudProvider)} />
							</SelectTrigger>
							<SelectContent>
								{CLOUD_PROVIDERS.map((p) => (
									<SelectItem key={p} value={p} textValue={providerLabel(p)}>
										{providerLabel(p)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>

					<div>
						<label
							htmlFor="onboarding-cloud-api-key"
							className="mb-1 block text-sm font-medium text-(--text-primary)"
						>
							{t("models.cloud.apiKey")}
						</label>
						<Input
							id="onboarding-cloud-api-key"
							type="password"
							value={cloudApiKey}
							onChange={(e) => setCloudApiKey(e.target.value)}
							placeholder={t("models.cloud.apiKeyPlaceholder")}
							autoComplete="off"
							spellCheck={false}
							data-testid="onboarding-cloud-api-key"
						/>
					</div>

					<div className="rounded-lg border border-border/10 bg-(--bg-subtle) p-4">
						<label
							className="flex items-start gap-3 text-sm"
							htmlFor="onboarding-cloud-consent"
						>
							<Checkbox
								id="onboarding-cloud-consent"
								className="mt-0.5 cursor-pointer"
								checked={cloudConsent}
								onCheckedChange={(v) => setCloudConsent(v === true)}
								aria-label={t("models.cloud.consentAria", {
									provider: providerLabel(cloudProvider),
								})}
								data-testid="onboarding-cloud-consent"
							/>
							<span className="flex-1">
								<span className="block font-medium text-(--text-primary)">
									{t("models.cloud.consentTitle")}
								</span>
								<span className="mt-1 block text-xs text-(--text-muted)">
									{t("models.cloud.consentDescription", {
										provider: providerLabel(cloudProvider),
									})}
								</span>
							</span>
						</label>
					</div>

					<p className="text-xs text-(--text-muted)">
						{t("onboarding.cloudNote")}
					</p>
				</div>
			)}
		</>
	);
}

export default ModelStep;
