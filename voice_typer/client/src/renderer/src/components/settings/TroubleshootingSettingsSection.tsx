// TroubleshootingSettingsSection — the "Troubleshooting" block of the
// Settings → Privacy tab.
//
//extracted from src/renderer/src/pages/Settings.tsx (which was
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
	ArrowTurnBackwardIcon,
	Book02Icon,
	Bug02Icon,
	Delete02Icon,
	File02Icon,
	InformationCircleIcon,
	ShieldBanIcon,
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
		const { call } = usePython();
		const { showSnack } = useSnackbar();

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
		const resetAccessibilityLabel = t(
			"settings.troubleshooting.resetAccessibility",
		);
		const resetLinuxLabel = t("settings.troubleshooting.resetLinux");

		// macOS-only: the stale-TCC-entry reset (``tccutil reset
		// Accessibility <bundle-id>``) is meaningless on Windows / Linux.
		// Linux-only: the stale-polkit-authorization reset is meaningless
		// elsewhere. Same UA probe as KeyboardPermissionBanner.
		const ua =
			typeof navigator === "undefined" ? "" : navigator.userAgent.toLowerCase();
		const isMac = ua.includes("mac");
		const isLinux = ua.includes("linux");

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
				...(isMac ? [resetAccessibilityLabel] : []),
				...(isLinux ? [resetLinuxLabel] : []),
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
		//
		//also call the `onboarding_reset` IPC so the backend clears
		// its `.onboarding_started` marker (otherwise the auto-heal in
		// `startup_sequence.py` would treat onboarding as already-complete
		// and skip the wizard). Previously this IPC handler + its
		// `reset_onboarding_complete` Python function were dead code.
		const handleReRunWizard = async () => {
			try {
				await call("onboarding_reset");
			} catch (err) {
				console.warn("onboarding_reset IPC failed (non-fatal):", err);
			}
			await updateConfig({ onboarding_completed: false });
			showSnack(t("settings.troubleshooting.reRunWizardToast"), "success");
			onNavigate?.("onboarding");
		};

		// Reset a stale macOS Accessibility TCC entry: the backend runs
		// `tccutil reset Accessibility <bundle-id>` (bundle ID resolved at
		// runtime, so the command matches the actually-running host —
		// Electron or Tauri) and re-opens System Settings so the user can
		// re-grant. The success toast surfaces the RUNTIME-RESOLVED
		// command the backend actually ran (finding #127 part b /
		// #919 part a) when the backend returned one.
		const handleResetAccessibility = async () => {
			try {
				const result = (await call("reset_macos_accessibility")) as {
					ok?: boolean;
					command?: string | null;
					error?: string | null;
				};
				if (result?.ok) {
					showSnack(
						result.command
							? t(
									"settings.troubleshooting.resetAccessibilityToastWithCommand",
									{
										command: result.command,
									},
								)
							: t("settings.troubleshooting.resetAccessibilityToast"),
						"success",
					);
				} else {
					showSnack(
						result?.error ||
							t("settings.troubleshooting.resetAccessibilityFailed"),
						"error",
					);
				}
			} catch (err) {
				console.error("reset_macos_accessibility failed:", err);
				showSnack(
					t("settings.troubleshooting.resetAccessibilityFailed"),
					"error",
				);
			}
		};

		// Reset a stale Linux polkit authorization: the backend restarts
		// the polkit daemon via pkexec (pkaction enumerates the Voice
		// Typer actions, pkcheck verifies the post-reset state) so the
		// next "Grant permission" re-prompts. Mirrors the macOS reset —
		// the success toast surfaces the command that was run.
		const handleResetLinuxPermissions = async () => {
			try {
				const result = (await call("reset_linux_permissions")) as {
					ok?: boolean;
					command?: string | null;
					error?: string | null;
				};
				if (result?.ok) {
					showSnack(
						result.command
							? t("settings.troubleshooting.resetLinuxToastWithCommand", {
									command: result.command,
								})
							: t("settings.troubleshooting.resetLinuxToast"),
						"success",
					);
				} else {
					showSnack(
						result?.error || t("settings.troubleshooting.resetLinuxFailed"),
						"error",
					);
				}
			} catch (err) {
				console.error("reset_linux_permissions failed:", err);
				showSnack(t("settings.troubleshooting.resetLinuxFailed"), "error");
			}
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
							icon={ArrowTurnBackwardIcon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{reRunWizardLabel}
					</Button>
					<p className="mt-1 text-xs text-muted-foreground">
						{t("settings.troubleshooting.reRunWizardHint")}
					</p>
					{isMac && (
						<Button
							variant="outline"
							className="gap-2"
							onClick={handleResetAccessibility}
							aria-label={t("settings.troubleshooting.resetAccessibilityAria")}
							title={t("settings.troubleshooting.resetAccessibilityHint")}
						>
							<HugeiconsIcon
								icon={ShieldBanIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{resetAccessibilityLabel}
						</Button>
					)}
					{isLinux && (
						<Button
							variant="outline"
							className="gap-2"
							onClick={handleResetLinuxPermissions}
							aria-label={t("settings.troubleshooting.resetLinuxAria")}
							title={t("settings.troubleshooting.resetLinuxHint")}
						>
							<HugeiconsIcon
								icon={ShieldBanIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{resetLinuxLabel}
						</Button>
					)}
					{/*visually separate the destructive Reset to Defaults
                                                button from the 5 non-destructive buttons above with a
                                                top border + padding so users don't click it by accident. */}
					<div className="mt-4 border-t border-border pt-3">
						<Button
							variant="destructive"
							className="gap-2"
							onClick={onResetClick}
							aria-label={t("settings.troubleshooting.resetToDefaultsAria")}
							title={t("settings.troubleshooting.resetToDefaultsHint")}
						>
							<HugeiconsIcon
								//use a trash/delete icon
								// for the destructive Reset to Defaults
								// action so it's visually distinct from
								// the non-destructive "Re-run Wizard"
								// button (ArrowTurnBackwardIcon). The
								// previous RefreshIcon was too similar
								// to a benign "reload" affordance.
								icon={Delete02Icon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{resetToDefaultsLabel}
						</Button>
						<p className="mt-1 text-xs text-muted-foreground">
							{t("settings.troubleshooting.resetToDefaultsHint")}
						</p>
					</div>
				</div>
			</SettingsSection>
		);
	},
);
