// TroubleshootingSettingsSection — the "Troubleshooting" block of the
// Settings → Privacy tab.
//
// PVT-028: extracted from src/renderer/src/pages/Settings.tsx (which was
// a 1125-line monolith). This component owns the six-button
// "Diagnostic tools, help, and support" section: Open Log Folder,
// Diagnostics, Help & FAQ, Report a Bug, Re-run setup wizard, and
// Reset to Defaults. Behaviour is identical to the previous inline
// implementation, including the `isVisible`-based search filter so the
// whole section hides when no row inside it matches the active query.
//
// The "Reset to Defaults" flow uses a parent-owned ConfirmDialog (the
// dialog itself lives in Settings.tsx so the page can coordinate the
// `resetToDefaults` async handler with the page-level `config` state
// and `updateConfig` callback). This component just calls
// `onResetClick` to request the dialog.

import {
	Book02Icon,
	Bug02Icon,
	File02Icon,
	InformationCircleIcon,
	RefreshIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";
import type { IsVisibleFn } from "./types";

interface TroubleshootingSettingsSectionProps {
	/** Search-filter predicate — same shape as the page-level helper. */
	isVisible: IsVisibleFn;
	/** Used by the "Re-run setup wizard" button to flip
	 *  `onboarding_completed` to false before navigating. */
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
	/** Routes the user to the About page (diagnostics) or the Onboarding
	 *  wizard (re-run setup). */
	onNavigate?: (page: Page) => void;
	/** Opens the parent-owned "Reset to Defaults" ConfirmDialog. */
	onResetClick: () => void;
}

export const TroubleshootingSettingsSection = memo(
	function TroubleshootingSettingsSection({
		isVisible,
		updateConfig,
		onNavigate,
		onResetClick,
	}: TroubleshootingSettingsSectionProps) {
		const { call: _call } = usePython();
		const { showSnack } = useSnackbar();
		// `_call` is currently unused — kept in scope so future diagnostics
		// (e.g. an "Run diagnostics" button that hits an IPC) can use it
		// without re-plumbing the hook. Marked void to satisfy the
		// `no-unused-vars` rule without stripping the hook entirely.
		void _call;

		// Resolve translated strings once per render so the search-visible
		// predicate and the rendered labels share the same values.
		const title = t("settings.troubleshooting.title");
		const description = t("settings.troubleshooting.description");
		const openLogFolderLabel = t("settings.troubleshooting.openLogFolder");
		const diagnosticsLabel = t("settings.troubleshooting.diagnostics");
		const helpFaqLabel = t("settings.troubleshooting.helpFaq");
		const reportBugLabel = t("settings.troubleshooting.reportBug");
		const reRunWizardLabel = t("settings.troubleshooting.reRunWizard");
		const resetToDefaultsLabel = t("settings.troubleshooting.resetToDefaults");

		// Section-level hide-when-empty: hide the whole section unless the
		// title OR at least one button label matches the active search query.
		const sectionVisible =
			isVisible(title, description, title) ||
			[
				openLogFolderLabel,
				diagnosticsLabel,
				helpFaqLabel,
				reportBugLabel,
				reRunWizardLabel,
				resetToDefaultsLabel,
			].some((label) => isVisible(label, undefined, title));

		if (!sectionVisible) return null;

		// Open the Python backend's log folder via the main process IPC.
		// Falls back to a snackbar with the error message if the IPC fails.
		const handleOpenLogs = async () => {
			try {
				const result = await window.window_?.openLogs?.();
				if (result?.success) {
					showSnack(t("settings.logFolderOpened"), "success");
				} else {
					showSnack(
						result?.error || t("settings.couldNotOpenLogFolder"),
						"error",
					);
				}
			} catch (err) {
				console.error("Failed to open logs:", err);
				showSnack(t("settings.couldNotOpenLogFolder"), "error");
			}
		};

		// Re-run the onboarding wizard: synchronously flip
		// `onboarding_completed` to false (so App.tsx's route guard lets the
		// user land on the wizard page) then navigate. The toast confirms
		// the action.
		const handleReRunWizard = async () => {
			await updateConfig({ onboarding_completed: false });
			showSnack(t("settings.troubleshooting.reRunWizardToast"), "success");
			onNavigate?.("onboarding");
		};

		return (
			<SettingsSection title={title} description={description}>
				<div className="px-3.5 py-3.5 flex flex-wrap gap-3">
					<Button
						variant="outline"
						className="gap-2"
						onClick={handleOpenLogs}
						aria-label={t("settings.troubleshooting.openLogFolderAria")}
						title={t("settings.troubleshooting.openLogFolderHint")}
					>
						<HugeiconsIcon
							icon={File02Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{openLogFolderLabel}
					</Button>
					<Button
						variant="outline"
						className="gap-2"
						onClick={() => onNavigate?.("about")}
						aria-label={t("settings.troubleshooting.diagnosticsAria")}
						title={t("settings.troubleshooting.diagnosticsHint")}
					>
						<HugeiconsIcon
							icon={InformationCircleIcon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{diagnosticsLabel}
					</Button>
					<Button
						variant="outline"
						className="gap-2"
						onClick={() =>
							window.open(
								"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md",
								"_blank",
								"noopener,noreferrer",
							)
						}
						aria-label={t("settings.troubleshooting.openDocsAria")}
						title={t("settings.troubleshooting.openDocsHint")}
					>
						<HugeiconsIcon
							icon={Book02Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{helpFaqLabel}
					</Button>
					<Button
						variant="outline"
						className="gap-2"
						onClick={() =>
							window.open(
								"https://github.com/AbdallahIsDev/voice-typer/issues",
								"_blank",
								"noopener,noreferrer",
							)
						}
						aria-label={t("settings.troubleshooting.reportBugAria")}
						title={t("settings.troubleshooting.reportBugHint")}
					>
						<HugeiconsIcon
							icon={Bug02Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{reportBugLabel}
					</Button>
					<Button
						variant="outline"
						className="gap-2"
						onClick={handleReRunWizard}
						aria-label={t("settings.troubleshooting.reRunWizardAria")}
						title={t("settings.troubleshooting.reRunWizardHint")}
					>
						<HugeiconsIcon
							icon={RefreshIcon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{reRunWizardLabel}
					</Button>
					<Button
						variant="destructive"
						className="gap-2"
						onClick={onResetClick}
						aria-label={t("settings.troubleshooting.resetToDefaultsAria")}
						title={t("settings.troubleshooting.resetToDefaultsHint")}
					>
						<HugeiconsIcon
							icon={RefreshIcon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{resetToDefaultsLabel}
					</Button>
				</div>
			</SettingsSection>
		);
	},
);
