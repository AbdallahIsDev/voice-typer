import type { Ref } from "react";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { FamilyLogo } from "@/components/models/FamilyLogo";
import {
	DOWNLOAD_CONTENT_ALIGNMENT,
	DOWNLOAD_SIZE_BUTTON_WIDTH,
} from "@/components/models/ModelCardActions";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
	SegmentedControl,
	type SegmentedControlOption,
} from "@/components/ui/segmented-control";
import { t } from "@/i18n/i18n";
import { formatVram } from "@/lib/format";
import { formatModelSpeed } from "@/lib/utils/models";
import type { BackendChoice } from "../hooks/useOnboardingWizard";
import { HEADING_CLASS } from "../lib/constants";
import type { ModelOption } from "../lib/types";

// Family grouping for the onboarding accordion, mirrors the Models
// page family headers: whisper → OpenAI, parakeet → NVIDIA, qwen →
// Qwen. Local helper (the Models page's groupModelsByFamily consumes
// the richer ModelInfo shape with disk state the wizard doesn't
// fetch); a future family maps through the same two tables.
function familyForModelName(name: string): string {
	if (name === "qwen") return "qwen";
	if (name === "parakeet") return "parakeet";
	return "whisper";
}

// Family display labels — brand names (proper nouns, kept literal like
// the Models page family headers). No "Powered by …" strip: the
// wizard is multi-provider and the family headers already carry the
// brand logos (2026-09-14 Model-step rebuild).
const FAMILY_LABELS: Record<string, string> = {
	whisper: "OpenAI",
	qwen: "Qwen",
	parakeet: "NVIDIA",
};

export interface ModelStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	modelOptions: ModelOption[];
	selectedModel: string;
	setSelectedModel: (v: string) => void;
	// Local-vs-cloud choice (Model step). The app NEVER auto-downloads a
	// model, the user either picks a local model and downloads it
	// explicitly per item, or connects a cloud transcription API.
	selectedBackend: BackendChoice;
	setSelectedBackend: (v: BackendChoice) => void;
	// Explicit per-model download state + handler. HuggingFace consent
	// is requested at the point of use through the shared consent gate
	// (openConsentGate), NOT a checkbox on this step (the consent is
	// granted on the Privacy step / gate dialog; the wizard never
	// duplicates it).
	downloadingModel: string | null;
	downloadProgress: number;
	downloadFailed: boolean;
	onDownload: (model: string) => void;
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

/** Small info chip used for the VRAM / language badges on a model row. */
function ModelBadge({ children }: { children: string }) {
	return (
		<span className="rounded-full bg-surface-subtle px-1.5 py-0.5 text-[0.625rem] font-medium uppercase tracking-wide text-muted-foreground">
			{children}
		</span>
	);
}

export function ModelStep({
	headingRef,
	modelOptions,
	selectedModel,
	setSelectedModel,
	selectedBackend,
	setSelectedBackend,
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

	// Group the offered local models by family for the accordion.
	// Families appear in first-seen order (whisper, qwen, parakeet).
	const families: { id: string; models: ModelOption[] }[] = [];
	for (const m of modelOptions) {
		const id = familyForModelName(m.name);
		let bucket = families.find((f) => f.id === id);
		if (!bucket) {
			bucket = { id, models: [] };
			families.push(bucket);
		}
		bucket.models.push(m);
	}

	const backendOptions: SegmentedControlOption<BackendChoice>[] = [
		{ value: "local", label: t("onboarding.backendLocalLabel") },
		{ value: "cloud", label: t("onboarding.backendCloudLabel") },
	];

	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.modelTitle")}
			</h2>
			<p className="text-sm text-muted-foreground">
				{t("onboarding.modelDescription")}
			</p>

			{/* Local vs cloud choice — the SAME SegmentedControl the Models
			    page uses for its Local/Cloud tabs (identical tokens:
			    rounded-lg border border-border/10 bg-surface-subtle container
			    + bg-surface bordered active segment, C-MODELS-1). This is the
			    single place where the user decides how transcription runs;
			    the app never downloads a model on its own. */}
			<SegmentedControl
				variant="tabs"
				options={backendOptions}
				value={selectedBackend}
				onChange={setSelectedBackend}
				ariaLabel={t("onboarding.backendAria")}
				indicatorClassName="bg-surface border border-border/10"
				labelClassName="flex-1 text-center"
				className="w-full rounded-lg border border-border/10 bg-surface-subtle"
				getTabId={(v) => `onboarding-backend-tab-${v}`}
				getPanelId={(v) => `onboarding-backend-panel-${v}`}
			/>

			{selectedBackend === "local" ? (
				<div
					role="tabpanel"
					id="onboarding-backend-panel-local"
					aria-labelledby="onboarding-backend-tab-local"
					className="flex flex-col gap-3"
				>
					{/* Family accordion — the Models-page structure: one
					    AccordionItem per provider family (persistent `+`
					    affordance per C-MODELS-4), each model item shows its
					    VRAM / language badges and carries its OWN download
					    button on the right (the standalone blue Download
					    button + duplicate HF consent checkbox were removed
					    2026-09-14). Selecting a model = clicking its row. */}
					<Accordion
						type="multiple"
						// The first family (whisper) starts expanded, matching the
						// Models page's expand-the-active-family default.
						defaultValue={families[0] ? [families[0].id] : []}
						className="rounded-lg border border-border/10 bg-surface-subtle"
						data-testid="onboarding-model-accordion"
					>
						{families.map((family) => (
							<AccordionItem key={family.id} value={family.id}>
								<AccordionTrigger className="px-4 hover:no-underline">
									<span className="flex items-center gap-2">
										<FamilyLogo family={family.id} />
										<span className="text-sm font-medium text-foreground">
											{FAMILY_LABELS[family.id] ?? family.id}
										</span>
									</span>
								</AccordionTrigger>
								<AccordionContent className="px-4 pb-2">
									<div className="flex flex-col divide-y divide-border/5">
										{family.models.map((m) => {
											const langKey = languageBadgeKey(m.languages);
											const isSelected = selectedModel === m.name;
											const isDownloadingThis = downloadingModel === m.name;
											return (
												<div
													key={m.name}
													className="flex flex-wrap items-center justify-between gap-2 py-2.5"
													data-testid={`onboarding-model-item-${m.name}`}
												>
													{/* Row-select button (toggle pattern): the
													    name + badges are one selectable unit;
													    the Download button is a SIBLING, never
													    nested (invalid DOM inside a button).
													    aria-pressed conveys the selection state
													    (a native <input type=radio> cannot carry
													    the badge layout inside the row). */}
													<button
														type="button"
														aria-pressed={isSelected}
														onClick={() => setSelectedModel(m.name)}
														className="flex min-w-0 flex-1 flex-col items-start gap-1 rounded-lg p-1 text-start outline-none focus-visible:ring-1 focus-visible:ring-ring"
														aria-label={t("onboarding.modelSelectAria", {
															name: m.name,
														})}
														data-testid={`onboarding-model-select-${m.name}`}
													>
														<span
															className={`text-sm font-medium ${isSelected ? "text-accent" : "text-foreground"}`}
														>
															{m.name}
															{isSelected ? " ✓" : ""}
														</span>
														<span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
															<span>
																{m.description ?? ""}
																{m.description && m.size ? ", " : ""}
																{m.size ?? ""}
																{(() => {
																	const speedLabel = formatModelSpeed(m.speed);
																	return speedLabel ? ` (${speedLabel})` : "";
																})()}
															</span>
															{m.vram_gb != null && (
																<ModelBadge>
																	{t("onboarding.vramBadge", {
																		vram: formatVram(m.vram_gb * 1024),
																	})}
																</ModelBadge>
															)}
															{langKey != null && (
																<ModelBadge>{t(langKey)}</ModelBadge>
															)}
														</span>
													</button>
													<Button
														type="button"
														variant="outline"
														size="sm"
														// Same fixed-width, left-aligned, compact-icon
														// tokens as the Models page download buttons
														// (C-MODELS-2), imported (no duplication).
														className={`gap-2 text-xs whitespace-nowrap ${DOWNLOAD_SIZE_BUTTON_WIDTH} ${DOWNLOAD_CONTENT_ALIGNMENT}`}
														onClick={() => onDownload(m.name)}
														disabled={isDownloading}
														aria-busy={isDownloadingThis}
														aria-label={t("onboarding.modelSizeDownloadAria", {
															name: m.name,
														})}
														data-testid={`onboarding-download-button-${m.name}`}
													>
														{isDownloadingThis ? (
															<>
																<span
																	className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
																	role="presentation"
																/>
																{progressPct}%
															</>
														) : (
															t("onboarding.downloadModel")
														)}
													</Button>
												</div>
											);
										})}
									</div>
								</AccordionContent>
							</AccordionItem>
						))}
					</Accordion>

					{/* Inline download-failure hint. The per-item button is
					    the retry affordance (click Download again); the old
					    standalone blue Download button + its adjacent hint
					    text are gone (downloads are per item now). */}
					{downloadFailed && !isDownloading && (
						<p
							className="text-xs text-destructive"
							data-testid="onboarding-download-error"
						>
							{t("onboarding.downloadFailedHint")}
						</p>
					)}

					{/* In-wizard download progress (kept from the previous
					    flow, driven by download_progress push events). */}
					{isDownloading && (
						<div
							role="progressbar"
							aria-valuenow={progressPct}
							aria-valuemin={0}
							aria-valuemax={100}
							aria-label={t("onboarding.downloadProgressAria", {
								percent: String(progressPct),
							})}
							className="flex flex-col gap-2 rounded-lg border border-accent/40 bg-accent/5 p-3"
							data-testid="onboarding-download-progress"
						>
							<div className="flex items-center justify-between text-xs text-(--text-secondary)">
								<span className="flex items-center gap-2">
									<span
										className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
										role="presentation"
									/>
									{t("onboarding.downloadingModel")}
								</span>
								<span>{progressPct}%</span>
							</div>
							<div className="h-1.5 w-full rounded-full bg-surface-subtle">
								<div
									className="h-1.5 rounded-full bg-accent transition-all duration-300"
									style={{ width: `${Math.min(100, downloadProgress)}%` }}
								/>
							</div>
						</div>
					)}
				</div>
			) : (
				<div
					role="tabpanel"
					id="onboarding-backend-panel-cloud"
					aria-labelledby="onboarding-backend-tab-cloud"
					className="flex flex-col gap-4"
				>
					{/* Cloud provider selection, mirrors the Models page
					    cloud tab (same config fields). */}
					<div className="flex flex-col gap-1">
						<label
							htmlFor="onboarding-cloud-provider"
							className="text-sm font-medium text-foreground"
						>
							{t("onboarding.cloudProviderLabel")}
						</label>
						<select
							id="onboarding-cloud-provider"
							value={cloudProvider}
							onChange={(e) => setCloudProvider(e.target.value)}
							aria-label={t("onboarding.cloudProviderLabel")}
							className="w-full rounded-lg border border-border/5 bg-surface px-3 py-2 text-sm text-foreground"
							data-testid="onboarding-cloud-provider"
						>
							{CLOUD_PROVIDERS.map((p) => (
								<option key={p} value={p}>
									{providerLabel(p)}
								</option>
							))}
						</select>
					</div>

					<div className="flex flex-col gap-1">
						<label
							htmlFor="onboarding-cloud-api-key"
							className="text-sm font-medium text-foreground"
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

					<div className="flex items-start gap-3">
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
						<label
							htmlFor="onboarding-cloud-consent"
							className="flex min-w-0 flex-1 flex-col gap-1 text-sm"
						>
							<span className="font-medium text-foreground">
								{t("models.cloud.consentTitle")}
								<InfoTooltip
									text={t("models.cloud.consentDescription", {
										provider: providerLabel(cloudProvider),
									})}
									contextLabel={t("models.cloud.consentTitle")}
								/>
							</span>
						</label>
					</div>

					<p className="text-xs text-muted-foreground">
						{t("onboarding.cloudNote")}
					</p>
				</div>
			)}
		</>
	);
}

export default ModelStep;
