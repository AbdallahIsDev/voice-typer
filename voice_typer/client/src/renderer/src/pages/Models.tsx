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
 *    consent gate (shared point-of-use consent dialog via
 *    `openConsentGate`; Allow persists the consent and continues the
 *    download).
 */

import {
	AiBrain03Icon,
	AlertCircleIcon,
	Cancel01Icon,
	Folder02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useMemo, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ModelsSkeleton } from "@/components/feedback/skeletons";
import { CloudProvidersPanel } from "@/components/models/CloudProvidersPanel";
import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";
import { Button } from "@/components/ui/button";
import {
	SegmentedControl,
	type SegmentedControlOption,
} from "@/components/ui/segmented-control";
import { useFilterState } from "@/hooks/useFilterState";
import { useModelLifecycle } from "@/hooks/useModelLifecycle";
import { t } from "@/i18n/i18n";
import { getActiveFamilyId, groupModelsByFamily } from "@/lib/utils/models";
import { tabPageIndicatorClassName } from "./_tabBarStyles";

export default function ModelsPage() {
	const lifecycle = useModelLifecycle();
	// Persist the active tab (Local / Cloud) across page
	// navigation via sessionStorage — a user who picked the Cloud
	// tab to configure an API key expects to still be on it when
	// they navigate away and back.
	const [activeTab, setActiveTab] = useFilterState<"local" | "cloud">(
		"models",
		"activeTab",
		"local",
	);

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
		// been opened, user interactions take over (the controlled value
		// below owns the open/close from then on).
	}, [lifecycle.config]);

	// Controlled accordion state — lives HERE, not inside
	// LocalModelsPanel, so the open families survive the local↔cloud tab
	// switch (the panel unmounts on switch; panel-internal state would
	// reset). Falls back to initialAccordionValue (expand the active
	// model's family) until the user interacts with the accordion.
	const [userAccordionValue, setUserAccordionValue] = useState<string[] | null>(
		null,
	);
	const effectiveAccordionValue = userAccordionValue ?? initialAccordionValue;

	// Dismissible "no model selected" banner — compact, sticky, independent
	// of the main content flow. Replaces the former centered EmptyState which
	// consumed ~120px vertical space (py-16 + icon). Uses the precise C-UI-2
	// copy "No speech model is selected. Select a model below." (key
	// models.noModelBanner) — precise per C-UI-2, actionable on the Models
	// page itself (vs "Open Models" which would be redundant here).
	// Dismiss is session-scoped via sessionStorage (cleared when a model is
	// selected), matching VocabDuplicateBanner's per-session pattern.
	const [noModelBannerDismissed, setNoModelBannerDismissed] = useState<boolean>(
		() => {
			try {
				return sessionStorage.getItem("models:noModelBannerDismissed") === "1";
			} catch {
				return false;
			}
		},
	);
	useEffect(() => {
		// Only clear the dismissed flag when a model is actually selected
		// (model_size is a non-empty string). The previous check
		// `lifecycle.config?.model_size !== ""` was truthy when config was
		// still `null` (initial load: `undefined !== ""`), which cleared the
		// sessionStorage immediately on mount and made the banner reappear on
		// every reload / navigation. Guard with a truthy check so the flag
		// survives reloads and page changes within the same app session and
		// is only cleared when the user picks a model, or when the app is
		// fully closed (sessionStorage is then discarded by the browser).
		if (lifecycle.config?.model_size) {
			if (noModelBannerDismissed) setNoModelBannerDismissed(false);
			try {
				sessionStorage.removeItem("models:noModelBannerDismissed");
			} catch {
				// ignore storage errors (e.g. blocked in some contexts)
			}
		}
	}, [lifecycle.config?.model_size, noModelBannerDismissed]);
	const showNoModelBanner =
		lifecycle.config?.model_size === "" && !noModelBannerDismissed;

	// Show a full-page spinner until the first `get_config` resolves.
	// Replaces the original `if (!_cachedConfig && !config)` check.
	// When the initial load FAILED, render the load-failure EmptyState
	// (variant="error" + Retry) instead — without it, a rejected
	// `get_config` left the page spinning forever with no recovery
	// path. Mirrors the History page's established error EmptyState.
	// Revisit case: `config` is non-null (SWR seed) while `loadError`
	// reports the revalidation failure — the stale seed still renders
	// (better than a blank page) but the failure is surfaced via the
	// banner below so it is never silently silent.
	if (!lifecycle.config) {
		if (lifecycle.loadError) {
			return (
				<div className="flex h-full items-center justify-center">
					<EmptyState
						variant="error"
						icon={AlertCircleIcon}
						title={t("models.loadFailedTitle")}
						description={t("models.loadFailedDescription")}
						actionLabel={t("models.retry")}
						onAction={() => {
							void lifecycle.loadConfig();
						}}
					/>
				</div>
			);
		}
		return <ModelsSkeleton />;
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
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6">
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

				{/* Revisit revalidation failure: the SWR seed keeps the page
				    usable, but a failed revalidation must not be silent —
				    this banner surfaces it with an inline Retry (the
				    full-page error EmptyState only covers the cold-start
				    case, where config itself is null). Reuses the same
				    load-failure copy as that EmptyState. */}
				{lifecycle.loadError && (
					<div
						role="alert"
						className="flex flex-wrap items-center gap-2 rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2"
					>
						<HugeiconsIcon
							icon={AlertCircleIcon}
							strokeWidth={2}
							aria-hidden="true"
							className="size-4 shrink-0 text-destructive"
						/>
						<p className="min-w-0 flex-1 text-xs font-medium text-destructive">
							{t("models.loadFailedTitle")}
						</p>
						<Button
							variant="outline"
							size="sm"
							className="gap-2"
							onClick={() => {
								void lifecycle.loadConfig();
							}}
						>
							{t("models.retry")}
						</Button>
					</div>
				)}

				{/* Compact dismissible "no model" banner — replaces the former
                                    centered EmptyState (py-16) that pushed model cards below the
                                    fold. Uses the shared design-system tokens
                                    (rounded-xl border-border/10 bg-(--bg-subtle)
                                    text-(--text-primary)) so it matches model cards,
                                    SegmentedControl and other subtle surfaces in every
                                    theme (light/dark/Dracula/Monokai/etc. via CSS vars).
                                    Positioned in the normal page flow (not sticky) between
                                    the active-model summary and the tab switcher, with the
                                    same close control as VocabDuplicateBanner
                                    (Cancel01Icon far right). */}
				<div className="flex flex-col gap-3">
					{showNoModelBanner && (
						<div
							data-testid="models-no-model-banner"
							role="status"
							aria-live="polite"
							aria-atomic="true"
							className="flex flex-wrap items-center gap-2 rounded-xl border border-border/5 bg-(--bg-subtle) px-3 py-2"
						>
							<HugeiconsIcon
								icon={AiBrain03Icon}
								strokeWidth={2}
								aria-hidden="true"
								className="size-4 shrink-0 text-(--text-muted)"
							/>
							<p className="min-w-0 flex-1 text-xs font-medium text-(--text-primary)">
								{t("models.noModelBanner")}
							</p>
							<button
								type="button"
								onClick={() => {
									setNoModelBannerDismissed(true);
									try {
										sessionStorage.setItem(
											"models:noModelBannerDismissed",
											"1",
										);
									} catch {
										// ignore
									}
								}}
								aria-label={t("common.close")}
								title={t("common.close")}
								className="cursor-pointer rounded-lg p-1 text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-(--text-primary) focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
							>
								<HugeiconsIcon
									icon={Cancel01Icon}
									strokeWidth={2.25}
									aria-hidden="true"
									className="size-4"
								/>
							</button>
						</div>
					)}

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
							//(2026-08-21): the outer tab container now carries the
							// SAME card/surface border treatment as the model
							// cards below it (`rounded-xl border border-border/5
							// bg-(--bg-subtle)` — the app-wide page-card token),
							// so the segmented control reads as one card among
							// the model cards instead of a borderless strip.
							// The active segment uses the matching
							// `border-border/5` treatment via
							// `tabPageIndicatorClassName`.
							className="w-full rounded-xl border border-border/5 bg-(--bg-subtle)"
							getTabId={(v) => `models-tab-${v}`}
							getPanelId={(v) => `models-panel-${v}`}
						/>
					</div>
				</div>

				<div className="flex flex-col gap-6">
					{activeTab === "local" ? (
						<div
							role="tabpanel"
							id="models-panel-local"
							aria-labelledby="models-tab-local"
							className="flex flex-col gap-6 scroll-mt-32"
						>
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
								failedDownload={lifecycle.failedDownload}
								installingDepsModel={lifecycle.installingDepsModel}
								onSelectModel={lifecycle.selectModel}
								//(overhaul point 4): route downloads through the
								// just-in-time HuggingFace-consent gate.
								onDownloadModel={lifecycle.handleDownloadModel}
								onDeleteModel={lifecycle.requestDeleteModel}
								onInstallDeps={lifecycle.installDeps}
								onTogglePause={lifecycle.handleTogglePause}
								onCancelDownload={lifecycle.handleCancelDownload}
								onRetryDownload={lifecycle.retryDownload}
								diskInfo={lifecycle.diskInfo}
								modelsFolderSupported={lifecycle.modelsFolderSupported}
								onOpenModelsFolder={lifecycle.handleOpenModelsFolder}
								accordionValue={effectiveAccordionValue}
								onAccordionValueChange={setUserAccordionValue}
							/>
						</div>
					) : (
						<div
							role="tabpanel"
							id="models-panel-cloud"
							aria-labelledby="models-tab-cloud"
							className="flex flex-col gap-6 scroll-mt-32"
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
