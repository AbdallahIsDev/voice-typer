/**
 * LocalModelsPanel — local-models tab content for the Models page.
 *
 * extracted from `pages/Models.tsx`. Renders:
 *   • HuggingFace consent banner (when consent hasn't been granted).
 *   • Disk-space warning banner (when `diskInfo` is available
 *     and free space is below 1GB).
 *   • "Open models folder" button (when the backend exposes
 *     the `open_models_folder` IPC).
 *   • The model-family accordion + per-variant cards (each card mounts
 *     `ModelCardActions` for the action button row, and a
 *     `DownloadProgressBar` when its model is actively downloading).
 *
 * This panel is a pure presentational component — it receives all
 * state + handlers as props from `useModelLifecycle`. No IPC, no
 * useState (except the accordion open-state which is purely local UI).
 */
import { Alert02Icon, Folder02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Fragment, useState } from "react";
import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";
import { ModelCardActions } from "@/components/models/ModelCardActions";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { formatBytes } from "@/lib/format";
import {
	type DiskInfo,
	formatModelSize,
	formatVram,
	hasInsufficientDiskSpace,
	type ModelFamily,
	type ModelInfo,
	type ModelMetadata,
} from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// Minimum free-disk threshold for the global warning banner. Picked to
// catch "disk almost full" states without false-positiving on systems
// with a comfortable buffer. 1GB = 1024 * 1024 * 1024 bytes.
const LOW_DISK_THRESHOLD_BYTES = 1024 * 1024 * 1024;

export interface LocalModelsPanelProps {
	config: VoiceTyperConfig | null;
	modelFamilies: ModelFamily[];
	modelCatalog: Record<string, ModelMetadata>;
	selectingModel: string | null;
	downloadingModel: string | null;
	// download-progress props (passed through to DownloadProgressBar)
	downloadProgress: number;
	downloadStatus: string;
	isPaused: boolean;
	downloadedBytes: number | null;
	totalBytes: number | null;
	speedBps: number | null;
	etaSeconds: number | null;
	//priority #3: when set, the in-flight download has failed
	// and the `<DownloadProgressBar>` enters its inline error state.
	// The bar stays mounted because `downloadingModel` is NOT cleared
	// on failure. Wired through to the bar's `error` + `modelName` props.
	// Optional so consumers that haven't been updated yet (e.g. Models.tsx)
	// don't break the build — when absent, the bar's error UI simply
	//doesn't render (matching the pre- behaviour).
	failedDownload?: { modelName: string; error: string } | null;
	//name of the model currently installing deps (drives the
	// `isInstallingDepsThis` prop on `<ModelCardActions>`). Optional for
	// the same backwards-compat reason as `failedDownload`.
	installingDepsModel?: string | null;
	// handlers
	onSelectModel: (model: ModelInfo) => void;
	onDownloadModel: (model: ModelInfo) => void;
	onDeleteModel: (model: ModelInfo) => void;
	onInstallDeps: (model: ModelInfo) => void;
	//priority #3: wired to <DownloadProgressBar>'s Retry button.
	// Optional so consumers that haven't been updated yet don't break
	// the build — when absent, the bar's Retry button simply doesn't
	// render (the toast's Retry action button still works as a fallback).
	onRetryDownload?: (model: ModelInfo) => void;
	onGrantConsent: () => void;
	onTogglePause: () => void;
	onCancelDownload: () => void;
	// diskInfo + low-disk-threshold props
	diskInfo: DiskInfo | null;
	modelsFolderSupported: boolean;
	onOpenModelsFolder: () => void;
	// optional initial open-accordion state (the active family)
	initialAccordionValue?: string[];
}

export function LocalModelsPanel({
	config,
	modelFamilies,
	modelCatalog,
	selectingModel,
	downloadingModel,
	downloadProgress,
	downloadStatus,
	isPaused,
	downloadedBytes,
	totalBytes,
	speedBps,
	etaSeconds,
	failedDownload,
	installingDepsModel,
	onSelectModel,
	onDownloadModel,
	onDeleteModel,
	onInstallDeps,
	onRetryDownload,
	onGrantConsent,
	onTogglePause,
	onCancelDownload,
	diskInfo,
	modelsFolderSupported,
	onOpenModelsFolder,
	initialAccordionValue,
}: LocalModelsPanelProps) {
	const [accordionValue, setAccordionValue] = useState<string[]>(
		initialAccordionValue ?? [],
	);

	const showConsentBanner = Boolean(config && !config.huggingface_consent);
	const showLowDiskWarning = Boolean(
		diskInfo && diskInfo.free_bytes < LOW_DISK_THRESHOLD_BYTES,
	);

	return (
		<div className="space-y-6">
			{/* low-disk warning banner. Only shown when the backend
                            exposes `get_disk_info` AND free space is below the threshold. */}
			{showLowDiskWarning && diskInfo && (
				<div className="rounded-lg border border-warning/40 bg-warning/5 p-4">
					<div className="flex items-start gap-3">
						<HugeiconsIcon
							icon={Alert02Icon}
							strokeWidth={2}
							className="mt-0.5 h-5 w-5 shrink-0 text-warning"
						/>
						<div className="flex-1">
							<h3 className="text-sm font-semibold text-(--text-primary)">
								{t("models.disk.lowSpaceTitle")}
							</h3>
							<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
								{t("models.disk.lowSpaceBody")}{" "}
								{t("models.disk.freeSpace", {
									space: formatBytes(diskInfo.free_bytes),
								})}
							</p>
						</div>
					</div>
				</div>
			)}

			{/* HuggingFace consent banner */}
			{showConsentBanner && (
				<div className="rounded-lg border border-warning/40 bg-warning/5 p-4">
					<div className="flex items-start gap-3">
						<HugeiconsIcon
							icon={Alert02Icon}
							strokeWidth={2}
							className="mt-0.5 h-5 w-5 shrink-0 text-warning"
						/>
						<div className="flex-1">
							{/* promoted from <h3> to <h2> so the
                                                            heading hierarchy stays h1 (PageHeading) → h2 (consent
                                                            banner) — fixes the axe-core heading-order violation
                                                            documented in a11y/axe-core.test.tsx. */}
							<h2 className="text-sm font-semibold text-(--text-primary)">
								{t("models.hfConsent.title")}
							</h2>
							<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
								{t("models.hfConsent.description")}
							</p>
							<div className="mt-3 flex items-center gap-3">
								<Button
									variant="default"
									size="sm"
									onClick={onGrantConsent}
									aria-label={t("models.hfConsent.grantAria")}
								>
									{t("models.hfConsent.grant")}
								</Button>
								<span className="text-xs text-(--text-muted)">
									{t("models.hfConsent.blockedHint")}
								</span>
							</div>
						</div>
					</div>
				</div>
			)}

			{/* "Open models folder" button. Only rendered when the
                            backend exposes the `open_models_folder` IPC (probed on mount
                            by the hook). Uses distinct `models.openFolder.label` /
                            `models.openFolder.aria` i18n keys so the button is
                            disambiguated from the page-header "Import Model" button
                            (which opens a file-picker dialog). */}
			{modelsFolderSupported && (
				<div className="flex justify-end">
					<Button
						variant="outline"
						size="sm"
						onClick={onOpenModelsFolder}
						className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						aria-label={t("models.openFolder.aria")}
					>
						<HugeiconsIcon
							icon={Folder02Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{t("models.openFolder.label")}
					</Button>
				</div>
			)}

			<div className="space-y-6">
				{/* Model Cards — grouped by family */}
				<Accordion
					type="multiple"
					value={accordionValue}
					onValueChange={setAccordionValue}
					className="rounded-lg border border-border bg-(--bg-subtle)"
				>
					{modelFamilies.map((family) => (
						<AccordionItem
							key={family.id}
							value={family.id}
							className="border-border data-open:bg-transparent"
						>
							<AccordionTrigger className="px-3.5 py-2.5 text-sm font-semibold text-(--text-primary) hover:no-underline hover:bg-foreground/5 data-open:bg-transparent">
								{family.name}
							</AccordionTrigger>
							<AccordionContent className="px-0 pb-0 divide-y divide-border">
								{family.variants.map((model) => {
									const badge = getStatusBadge(model);
									const meta = modelCatalog[model.name];
									const isSelectingThis = selectingModel === model.name;
									const isDownloadingThis = downloadingModel === model.name;
									const isInstallingDepsThis =
										installingDepsModel === model.name;
									const anyDownloading = downloadingModel !== null;
									//priority #3: the bar's error prop is
									// populated only when the failure is for THIS
									// model. Failures for other models don't render
									// an error UI on this card (the bar would be
									// mounted on the other card instead).
									const failedThis =
										failedDownload?.modelName === model.name
											? failedDownload.error
											: null;

									// per-model disk-space pre-flight indicator.
									// We don't block the download here (the user might
									// know better — e.g. they're about to free up space).
									// We just visually flag the model when its catalog
									// size exceeds free disk space.
									const insufficientSpace =
										!!meta &&
										!!diskInfo &&
										hasInsufficientDiskSpace(diskInfo, meta.download_size_mb);

									return (
										<Fragment key={model.name}>
											<div className="flex items-center gap-3 px-3.5 py-2.5">
												<div className="flex-1 min-w-0">
													<div className="flex items-center gap-2">
														<h4 className="text-sm font-semibold text-(--text-primary) truncate">
															{meta?.display_name ?? model.name}
														</h4>
														{badge && (
															<output
																className="shrink-0 inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold border"
																aria-live="polite"
																style={{
																	backgroundColor: badge.bg,
																	color: badge.color,
																	borderColor: `${badge.color}40`,
																}}
															>
																{badge.label}
															</output>
														)}
														{insufficientSpace && (
															<output
																className="shrink-0 inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold border"
																aria-live="polite"
																style={{
																	backgroundColor:
																		"color-mix(in srgb, #ef4444 15%, transparent)",
																	color: "#ef4444",
																	borderColor: "#ef444440",
																}}
															>
																{t("models.status.insufficientDisk")}
															</output>
														)}
													</div>
													<p className="text-xs text-(--text-muted) mt-0.5">
														{t("models.card.size", {
															size: formatModelSize(model.size),
														})}
														{meta && (
															<span className="text-(--text-muted)">
																{"  ·  "}
																{t("models.card.vram", {
																	vram: formatVram(meta.required_vram_mb),
																})}
																{"  ·  "}
																{meta.multilingual
																	? t("models.card.multilingual")
																	: t("models.card.englishOnly")}
																{"  ·  "}
																{t("models.card.speedSuffix", {
																	rating: meta.speed_rating,
																})}
																{meta.is_distilled
																	? t("models.card.distilled")
																	: ""}
															</span>
														)}
													</p>
												</div>
												<ModelCardActions
													model={model}
													isSelectingThis={isSelectingThis}
													isDownloadingThis={isDownloadingThis}
													anyDownloading={anyDownloading}
													isInstallingDepsThis={isInstallingDepsThis}
													onSelect={() => onSelectModel(model)}
													onDownload={() => onDownloadModel(model)}
													onDelete={() => onDeleteModel(model)}
													onInstallDeps={() => onInstallDeps(model)}
												/>
											</div>
											{isDownloadingThis && (
												<div className="px-3.5 pb-3">
													<DownloadProgressBar
														progress={downloadProgress}
														status={downloadStatus}
														isPaused={isPaused}
														downloadedBytes={downloadedBytes}
														totalBytes={totalBytes}
														speedBps={speedBps}
														etaSeconds={etaSeconds}
														onTogglePause={onTogglePause}
														onCancel={onCancelDownload}
														//priority #3 + #4: forward the model
														// name + error state + retry handler so the
														// bar can render the inline error UI + Retry
														// button instead of vanishing on failure.
														modelName={model.name}
														error={failedThis}
														onRetry={
															onRetryDownload
																? () => onRetryDownload(model)
																: undefined
														}
													/>
												</div>
											)}
										</Fragment>
									);
								})}
							</AccordionContent>
						</AccordionItem>
					))}
				</Accordion>
			</div>
		</div>
	);
}

// ── Local helper: status badge for dep-required models ────────────────
//
// Kept inside the panel (not in lib/utils/models.ts) because it's
// purely presentational — it returns CSS color strings tied to the
// amber-400 token used by the consent banner. The lib module stays
// free of styling concerns.

function getStatusBadge(
	model: ModelInfo,
): { label: string; bg: string; color: string } | null {
	if (!model.depsOk)
		return {
			label: t("models.status.depsRequired"),
			bg: "color-mix(in srgb, #f59e0b 15%, transparent)",
			color: "#f59e0b",
		};
	return null;
}
