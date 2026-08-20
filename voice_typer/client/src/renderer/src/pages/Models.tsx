/**
 * ModelsPage — thin composition root for the ASR Models page.
 *
 *  fix #1: previously a 1448-line monolith. After the split:
 *  • `useModelLifecycle` owns all state + IPC actions.
 *  • `LocalModelsPanel` renders the local-models tab (family cards,
 *    disk-space warning, open-folder button).
 *  • `CloudProvidersPanel` renders the cloud-models tab (provider
 *    groups with API key + test + consent).
 *  • `ModelCardActions` is the pure presentational 4-branch button
 *    row used by each model card.
 *
 * This file is now ~150 lines: it consumes the hook, renders the
 * page heading + in-flow tab switcher, mounts the active panel, and
 * renders the ConfirmDialog for model deletion.
 *
 * (UI/UX overhaul 2026-08-20):
 *  • point 1 — the "Last updated / refresh" indicator was REMOVED.
 *  • point 2 — the tab switcher is no longer sticky; it sits in the
 *    page flow below the title/description and scrolls with content.
 *  • point 3 — "Import Model" renders only on the Local Models tab.
 *  • point 4 — downloads route through
 *    `lifecycle.handleDownloadModel`, the just-in-time HuggingFace
 *    consent gate (toast with a one-click "Grant consent" action).
 */

import { AiBrain03Icon, Folder02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useMemo, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
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
import { tabPageIndicatorClassName } from "./_tabBarStyles";

export default function ModelsPage() {
	const lifecycle = useModelLifecycle();
	const [activeTab, setActiveTab] = useState<"local" | "cloud">("local");

	const tabOptions: SegmentedControlOption<string>[] = [
		{ value: "local", label: t("models.localModels") },
		//(overhaul point 12): renamed "Cloud Providers" → "Cloud Models"
		// for naming consistency with "Local Models".
		{ value: "cloud", label: t("models.cloudModels") },
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
			{/*
				(UI/UX overhaul 2026-08-20):
				• points 1+2 — the sticky top-of-viewport tab bar was
				  REMOVED; the SegmentedControl now sits in the page flow
				  below the title/description (where the "Last updated"
				  indicator used to sit) and scrolls with the content.
				  The "Last updated / refresh" indicator was removed
				  entirely (model availability/install state doesn't
				  change moment-to-moment; a manual refresh serves no
				  purpose here).
				• point 3 — the "Import Model" button only renders on the
				  Local Models tab (importing a local model file has no
				  meaning on the Cloud Models tab).
			*/}
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-8 pb-6">
				<PageHeading
					title={t("models.asrTitle")}
					description={t("models.asrSubtitle")}
				>
					{activeTab === "local" && (
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
					)}
				</PageHeading>

				{/* Tab switcher — in the page flow (not sticky), below the
				    page title/description and above the model list. */}
				<div className="pb-4">
					<SegmentedControl
						variant="tabs"
						options={tabOptions}
						value={activeTab}
						onChange={(v) => setActiveTab(v as "local" | "cloud")}
						ariaLabel={t("models.title")}
						indicatorClassName={tabPageIndicatorClassName}
						labelClassName="flex-1 text-center"
						className="w-full rounded-lg bg-(--bg-subtle)"
						getTabId={(v) => `models-tab-${v}`}
						getPanelId={(v) => `models-panel-${v}`}
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
							{/* Genuine "no model selected" state (the backend's
							        NO_MODEL_SIZE sentinel, model_size === "") —
							        nothing is active and the app will not try to
							        load a model until the user picks one below.
							        Rendered via the shared EmptyState component
							        (variant="info") so the visual treatment matches
							        Dashboard / Settings / Vocabulary — the title is
							        wrapped in an <h3> so SR users can navigate by
							        heading. */}
							{lifecycle.config.model_size === "" && (
								<EmptyState
									variant="info"
									icon={AiBrain03Icon}
									title={t("models.noModelSelected")}
								/>
							)}
							<LocalModelsPanel
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
								//(overhaul point 4): route downloads through the
								// just-in-time HuggingFace-consent gate.
								onDownloadModel={lifecycle.handleDownloadModel}
								onDeleteModel={lifecycle.requestDeleteModel}
								onInstallDeps={lifecycle.installDeps}
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
