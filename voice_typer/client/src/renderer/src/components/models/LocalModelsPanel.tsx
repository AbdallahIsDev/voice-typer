/**
 * LocalModelsPanel — local-models tab content for the Models page.
 *
 * extracted from `pages/Models.tsx`. Renders:
 *   • Disk-space warning banner (when `diskInfo` is available
 *     and free space is below 1GB).
 *   • "Open models folder" button (when the backend exposes
 *     the `open_models_folder` IPC).
 *   • The model-family accordion + per-variant cards (each card mounts
 *     `ModelCardActions` for the action button row, and a
 *     `DownloadProgressBar` when its model is actively downloading).
 *
 * (UI/UX overhaul 2026-08-20):
 *   • The persistent HuggingFace consent banner was REMOVED — consent
 *     is now checked just-in-time in the download flow
 *     (`useModelLifecycle.handleDownloadModel`), which shows a
 *     transient toast with a "Grant consent" action instead. This
 *     panel no longer renders any consent UI.
 *   • The group accordion + variant rows now compose the shared
 *     `ModelGroupList` primitives (same components as the Cloud Models
 *     tab) so both tabs share one visual system.
 *   • The metadata line distinguishes label+value pairs (VRAM, WER —
 *     muted label, colon, primary value) from standalone tags
 *     (Multilingual / English Only / speed / Distilled — neutral
 *     pills).
 *   • Model size moved out of the metadata line into the download
 *     button (see `ModelCardActions`).
 *   • Display names are derived: family header = company ("OpenAI"),
 *     variant names = "Whisper Tiny" / "Whisper Large V3" etc. via
 *     `getModelVariantDisplayName` (display-layer only — slugs,
 *     repo_ids and config keys are untouched).
 *
 * This panel is a pure presentational component — it receives all
 * state + handlers as props from `useModelLifecycle`. No IPC, no
 * useState (except the accordion open-state which is purely local UI).
 */
import { Alert02Icon, Folder02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Fragment, useState } from "react";
import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";
import { FamilyLogo } from "@/components/models/FamilyLogo";
import { ModelCardActions } from "@/components/models/ModelCardActions";
import {
	MetadataPair,
	MetadataTag,
	ModelGroupAccordion,
	ModelGroupContent,
	ModelGroupItem,
	ModelGroupTrigger,
	ModelVariantRow,
} from "@/components/models/ModelGroupList";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { formatBytes } from "@/lib/format";
import {
	type DiskInfo,
	formatModelSpeed,
	formatVram,
	formatWer,
	getModelVariantDisplayName,
	hasInsufficientDiskSpace,
	type ModelFamily,
	type ModelInfo,
	type ModelMetadata,
} from "@/lib/utils/models";

// Minimum free-disk threshold for the global warning banner. Picked to
// catch "disk almost full" states without false-positiving on systems
// with a comfortable buffer. 1GB = 1024 * 1024 * 1024 bytes.
const LOW_DISK_THRESHOLD_BYTES = 1024 * 1024 * 1024;

export interface LocalModelsPanelProps {
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
				{/* Model Cards — grouped by family (shared ModelGroupList
                                    primitives, same as the Cloud Models tab). */}
				<ModelGroupAccordion
					type="multiple"
					value={accordionValue}
					onValueChange={setAccordionValue}
				>
					{modelFamilies.map((family) => (
						<ModelGroupItem key={family.id} value={family.id}>
							<ModelGroupTrigger>
								<FamilyLogo family={family.id} />
								{family.name}
							</ModelGroupTrigger>
							<ModelGroupContent>
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
											<ModelVariantRow
												name={getModelVariantDisplayName(model, meta)}
												headingExtra={
													<>
														{badge && (
															<span
																className="shrink-0 inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold border"
																style={{
																	backgroundColor: badge.bg,
																	color: badge.color,
																	borderColor: `${badge.color}40`,
																}}
															>
																{badge.label}
															</span>
														)}
														{insufficientSpace && (
															<span
																className="shrink-0 inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold border"
																style={{
																	backgroundColor:
																		"color-mix(in srgb, #ef4444 15%, transparent)",
																	color: "#ef4444",
																	borderColor: "#ef444440",
																}}
															>
																{t("models.status.insufficientDisk")}
															</span>
														)}
													</>
												}
												meta={meta && <ModelMetadataLine meta={meta} />}
												actions={
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
												}
											/>
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
							</ModelGroupContent>
						</ModelGroupItem>
					))}
				</ModelGroupAccordion>
			</div>
		</div>
	);
}

// ── Metadata line (points 6 + 8) ──────────────────────────────────────
//
// Distinguishes label+value pairs (VRAM, WER — muted label, colon,
// primary value) from standalone descriptive tags (Multilingual /
// English Only, speed, Distilled — neutral pills). Size is NOT part of
// this line anymore (moved into the download button).
function ModelMetadataLine({ meta }: { meta: ModelMetadata }) {
	return (
		<>
			<MetadataPair
				label={t("models.card.vramLabel")}
				value={`~${formatVram(meta.required_vram_mb)}`}
			/>
			{/* WER — only when the backend catalog supplies a real,
			    published figure (meta.wer). Never guessed. */}
			{typeof meta.wer === "number" && meta.wer !== null && (
				<MetadataPair
					label={t("models.card.werLabel")}
					value={formatWer(meta.wer)}
				/>
			)}
			{/* (2026-08-21): the metadata line is now TWO independent
			    groups — the information group (VRAM/WER pairs above) and
			    this label group (all descriptive tags). The outer flex
			    (`ModelVariantRow`) keeps `gap-x-3` between the last
			    information pair and this group; the tags WITHIN the group
			    use the tighter `gap-x-1.5` so "Multilingual" / "Fast
			    Speed" / "Distilled" read as one cluster instead of being
			    spaced as far apart as the VRAM/WER metrics. */}
			<span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1.5">
				<MetadataTag>
					{meta.multilingual
						? t("models.card.multilingual")
						: t("models.card.englishOnly")}
				</MetadataTag>
				<MetadataTag>
					{t("models.card.speedSuffix", {
						rating: formatModelSpeed(meta.speed_rating),
					})}
				</MetadataTag>
				{meta.is_distilled && (
					<MetadataTag>{t("models.card.distilled")}</MetadataTag>
				)}
			</span>
		</>
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
