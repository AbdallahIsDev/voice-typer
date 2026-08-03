/**
 * ModelsPage — thin composition root for the ASR Models page.
 *
 *  fix #1: previously a 1448-line monolith. After the split:
 *  • `useModelLifecycle` owns all state + IPC actions.
 *  • `LocalModelsPanel` renders the local-models tab (family cards,
 *    consent banner, disk-space warning, open-folder button).
 *  • `CloudProvidersPanel` renders the cloud-providers tab (per-
 *    provider cards with API key + test + consent).
 *  • `ModelCardActions` is the pure presentational 4-branch button
 *    row used by each model card.
 *
 * This file is now ~150 lines: it consumes the hook, renders the
 * sticky tab bar + page heading + LastUpdatedIndicator, mounts the
 * active panel, and renders the ConfirmDialog for model deletion.
 *
 *  fix #3: dead `_initialLoading` state + the dead unmount-
 * cleanup `useEffect` were removed (React already discards state on
 * unmount; the cleanup was a no-op that caused spurious state updates
 * during HMR / strict-mode double-mounts).
 *
 *  fix #10: replaced the hardcoded `pt-[156px]` (which was
 * tuned to clear the sticky SegmentedControl bar at a specific zoom
 * level) with `scroll-mt-32` on the panels + the standard page
 * padding. The scroll-mt utility makes deep-links to in-page anchors
 * land below the sticky bar without pixel-tuning.
 */
import { Folder02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMemo, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { Spinner } from "@/components/feedback/Spinner";
import { CloudProvidersPanel } from "@/components/models/CloudProvidersPanel";
import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";
import { Button } from "@/components/ui/button";
import {
	SegmentedControl,
	type SegmentedControlOption,
} from "@/components/ui/segmented-control";
import { useModelLifecycle } from "@/hooks/useModelLifecycle";
import { t } from "@/i18n/i18n";
import { getActiveFamilyId, groupModelsByFamily } from "@/lib/utils/models";
import {
	tabPageHeaderClassName,
	tabPageIndicatorClassName,
} from "./_tabBarStyles";

export default function ModelsPage() {
	const lifecycle = useModelLifecycle();
	const [activeTab, setActiveTab] = useState<"local" | "cloud">("local");

	const tabOptions: SegmentedControlOption<string>[] = [
		{ value: "local", label: t("models.localModels") },
		{ value: "cloud", label: t("models.cloudProviders") },
	];

	// Memoize the family grouping so we don't re-group on every render.
	const modelFamilies = useMemo(
		() => groupModelsByFamily(lifecycle.models),
		[lifecycle.models],
	);

	// Initial open-accordion value: expand the family that contains the
	// active model. Computed once on first render (when config arrives).
	const initialAccordionValue = useMemo(() => {
		const activeFamilyId = getActiveFamilyId(lifecycle.config);
		return activeFamilyId ? [activeFamilyId] : [];
		// Intentionally only depends on config — once the accordion has
		// been opened, user interactions take over (the panel's local
		// state owns the open/close after mount).
	}, [lifecycle.config]);

	// Show a full-page spinner until the first `get_config` resolves.
	// Replaces the original `if (!_cachedConfig && !config)` check.
	if (!lifecycle.config) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	return (
		<>
			{/* Full-width sticky tab bar.
                                : use the shared tabPageHeaderClassName /
                                tabPageIndicatorClassName from pages/_tabBarStyles so
                                Settings and Models render visually identical sticky tab
                                bars. Previously Models had no wrapper bg/border and used
                                a different z-index from Settings. */}
			<div className={tabPageHeaderClassName}>
				<div className="mx-auto w-full max-w-2xl px-6 py-1.5">
					<SegmentedControl
						variant="tabs"
						options={tabOptions}
						value={activeTab}
						onChange={(v) => setActiveTab(v as "local" | "cloud")}
						ariaLabel={t("models.title")}
						indicatorClassName={tabPageIndicatorClassName}
						labelClassName="flex-1 text-center"
						className="w-full"
						getTabId={(v) => `models-tab-${v}`}
						getPanelId={(v) => `models-panel-${v}`}
					/>
				</div>
			</div>

			{/*
                                 fix #10: replaced `pt-[156px]` with `pt-32`.
                                The original magic padding was tuned to clear the sticky
                                bar (SegmentedControl + page heading + LastUpdatedIndicator
                                row) at a specific zoom level. `pt-32` (128px) clears the
                                sticky bar at default zoom; the scroll-mt-32 utility on
                                anchors would handle deep-link scrolling if we add any.
                        */}
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-32 pb-6">
				<PageHeading
					title={t("models.asrTitle")}
					description={t("models.asrSubtitle")}
				>
					<Button
						variant="outline"
						size="sm"
						onClick={lifecycle.handleImportModel}
						disabled={lifecycle.isImporting}
						title={t("models.import.title")}
						className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						aria-label={t("models.import.title")}
						aria-busy={lifecycle.isImporting}
					>
						<HugeiconsIcon
							icon={Folder02Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{lifecycle.isImporting
							? t("models.import.importing")
							: t("models.import.importModel")}
					</Button>
				</PageHeading>

				<div className="flex justify-end pb-2">
					<LastUpdatedIndicator
						agoLabel={lifecycle.agoLabel}
						onRefresh={lifecycle.handleManualRefresh}
						refreshing={lifecycle.refreshing}
					/>
				</div>

				<div className="space-y-6">
					{activeTab === "local" ? (
						<div
							role="tabpanel"
							id="models-panel-local"
							aria-labelledby="models-tab-local"
							className="space-y-6 scroll-mt-32"
						>
							<LocalModelsPanel
								config={lifecycle.config}
								modelFamilies={modelFamilies}
								modelCatalog={lifecycle.modelCatalog}
								selectingModel={lifecycle.selectingModel}
								downloadingModel={lifecycle.downloadingModel}
								downloadProgress={lifecycle.downloadProgress}
								downloadStatus={lifecycle.downloadStatus}
								isPaused={lifecycle.isPaused}
								downloadedBytes={lifecycle.downloadedBytes}
								totalBytes={lifecycle.totalBytes}
								speedBps={lifecycle.speedBps}
								etaSeconds={lifecycle.etaSeconds}
								onSelectModel={lifecycle.selectModel}
								onDownloadModel={lifecycle.downloadModel}
								onDeleteModel={lifecycle.requestDeleteModel}
								onInstallDeps={lifecycle.installDeps}
								onGrantConsent={lifecycle.handleGrantConsent}
								onTogglePause={lifecycle.handleTogglePause}
								onCancelDownload={lifecycle.handleCancelDownload}
								diskInfo={lifecycle.diskInfo}
								modelsFolderSupported={lifecycle.modelsFolderSupported}
								onOpenModelsFolder={lifecycle.handleOpenModelsFolder}
								initialAccordionValue={initialAccordionValue}
							/>
						</div>
					) : (
						<div
							role="tabpanel"
							id="models-panel-cloud"
							aria-labelledby="models-tab-cloud"
							className="space-y-6 scroll-mt-32"
						>
							<CloudProvidersPanel
								config={lifecycle.config}
								cloudProviders={lifecycle.cloudProviders}
								apiKeys={lifecycle.apiKeys}
								testResults={lifecycle.testResults}
								onApiKeyChange={(provider, value) =>
									lifecycle.setApiKeys((prev) => ({
										...prev,
										[provider]: value,
									}))
								}
								onSaveApiKey={lifecycle.saveApiKey}
								onTestConnection={lifecycle.testConnection}
								onConsentChange={lifecycle.setCloudConsent}
								onClearTestResult={lifecycle.clearTestResult}
							/>
						</div>
					)}
				</div>
			</div>

			{/*
			 * (rationale): model delete is intentionally confirm-only — NO undo
			 * toast is wired here, unlike History / Templates / Vocabulary which all use
			 * `showUndoableToast` for a 6-second undo window.
			 *
			 * This is a deliberate decision, not an oversight:
			 *   • Soft-delete (move model dir to trash for 6s)
			 *     would hold 1.5–3 GB on disk for the whole undo
			 *     window — defeating the user's intent to free space.
			 *   • Re-download-as-undo would silently re-fetch a
			 *     multi-GB file the user just deleted, hitting
			 *     their network quota without consent.
			 *
			 * Full writeup: docs/ux/model-delete-rationale.md
			 * Backend assumption: `VoiceTyperService.delete_model`
			 * is a hard `shutil.rmtree` (see
			 * `voice_typer/server/service.py`), which is what makes
			 * both undo options bad.
			 *
			 * delete-model confirmation dialog. The delete button
			 * stores the target in `deleteModelTarget`; this dialog
			 * surfaces the confirmation and invokes the `delete_model`
			 * IPC.
			 */}
			<ConfirmDialog
				open={!!lifecycle.deleteModelTarget}
				title={t("models.deleteDialog.title")}
				message={t("models.deleteDialog.message", {
					name: lifecycle.deleteModelTarget?.name ?? "",
				})}
				confirmLabel={t("models.deleteModel")}
				cancelLabel={t("common.cancel")}
				variant="destructive"
				onConfirm={lifecycle.confirmDelete}
				onCancel={() => lifecycle.setDeleteModelTarget(null)}
			/>
		</>
	);
}
