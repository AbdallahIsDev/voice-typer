// Settings → Privacy tab.
//
// a 1125-line monolith). This component owns the six-button
// whole section hides when no row inside it matches the active query.
//
// `resetToDefaults` async handler with the page-level `config` state
// `onResetClick` to request the dialog.

import {
	ArrowTurnBackwardIcon,
	Book02Icon,
	Bug02Icon,
	Delete02Icon,
	File02Icon,
	KeyboardIcon,
	ShieldBanIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useEffect, useState } from "react";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { openExternalUrl } from "@/lib/external-links";
import type { LausuConfig } from "@/types/config";
import type { Page } from "@/types/ipc";
import { anyRowVisible } from "./settingsRowGating";
import type { IsVisibleFn } from "./types";

interface TroubleshootingSettingsSectionProps {
	/** Search-filter predicate, same shape as the page-level helper. */
	isVisible: IsVisibleFn;
	/** Used by the "Re-run setup wizard" button to flip
	 *  `onboarding_completed` to false before navigating. */
	updateConfig: (updates: Partial<LausuConfig>) => void;
	/** Routes the user to the About page (diagnostics) or the Onboarding
	 *  wizard (re-run setup). */
	onNavigate?: (page: Page) => void;
	/** Opens the parent-owned "Reset to Defaults" ConfirmDialog. */
	onResetClick: () => void;
	/** Opens the parent-owned HelpOverlay (keyboard-shortcut +
	 *  punctuation-cheat-sheet reference). The overlay instance itself
	 *  lives in Settings.tsx, the section only requests it. */
	onOpenHelp: () => void;
}

export const TroubleshootingSettingsSection = memo(
	function TroubleshootingSettingsSection({
		isVisible,
		updateConfig,
		onNavigate,
		onResetClick,
		onOpenHelp,
	}: TroubleshootingSettingsSectionProps) {
		const { call } = usePython();
		const { showSnack } = useSnackbar();

		// Ref mirror of `call` so the mount probe effect depends only on
		// `isMac`. Test mocks may return a fresh `call` per render, an
		// effect dep on it would re-fire the probe (check_accessibility →
		// setStaleResetCommand → re-render → new call → loop). Same
		// pattern as useVocabulary.ts.
		const callRef = useLatestRef(call);

		// Resolve translated strings once per render so the search-visible
		// predicate and the rendered labels share the same values.
		const title = t("settings.troubleshooting.title");
		const description = t("settings.troubleshooting.description");
		const openLogFolderLabel = t("settings.troubleshooting.openLogFolder");
		const helpFaqLabel = t("settings.troubleshooting.helpFaq");
		const reportBugLabel = t("settings.troubleshooting.reportBug");
		const reRunWizardLabel = t("settings.troubleshooting.reRunWizard");
		const resetToDefaultsLabel = t("settings.troubleshooting.resetToDefaults");
		// Keyboard Shortcuts button, opens the shared HelpOverlay
		// existing `help.title` key ("Keyboard Shortcuts").
		const keyboardShortcutsLabel = t("help.title");
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

		// Finding #919 part b: on a CONFIRMED stale Accessibility grant
		// (``AXIsProcessTrusted()`` returned False) the backend echoes
		// command still being decommissioned on an older backend) must
		// silently mean "no suggestion".
		const [staleResetCommand, setStaleResetCommand] = useState<string | null>(
			null,
		);
		useEffect(() => {
			if (!isMac) return;
			let cancelled = false;
			(async () => {
				try {
					const result = (await callRef.current("check_accessibility")) as {
						suggest_reset?: boolean;
						reset_command?: string;
					};
					if (
						!cancelled &&
						result?.suggest_reset === true &&
						typeof result.reset_command === "string"
					) {
						setStaleResetCommand(result.reset_command);
					}
				} catch (err) {
					console.warn(
						"[renderer:TroubleshootingSettingsSection] check_accessibility probe failed (non-fatal):",
						err,
					);
				}
			})();
			return () => {
				cancelled = true;
			};
		}, [isMac, callRef]);

		// title OR at least one button label matches the active search query.
		const sectionVisible =
			isVisible(title, description, title) ||
			anyRowVisible(isVisible, title, [
				{ label: openLogFolderLabel },
				{ label: helpFaqLabel },
				{ label: keyboardShortcutsLabel },
				{ label: reportBugLabel },
				{ label: reRunWizardLabel },
				{ label: resetToDefaultsLabel },
				...(isMac ? [{ label: resetAccessibilityLabel }] : []),
				...(isLinux ? [{ label: resetLinuxLabel }] : []),
			]);

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
				console.error(
					"[renderer:TroubleshootingSettingsSection] Failed to open logs:",
					err,
				);
				showSnack(t("settings.couldNotOpenLogFolder"), "error");
			}
		};

		// Re-run the onboarding wizard: synchronously flip
		// user land on the wizard page) then navigate. The toast confirms
		//
		//also call the `onboarding_reset` IPC so the backend clears
		// its `.onboarding_started` marker (otherwise the auto-heal in
		// `startup_sequence.py` would treat onboarding as already-complete
		// `reset_onboarding_complete` Python function were dead code.
		const handleReRunWizard = async () => {
			try {
				await call("onboarding_reset");
			} catch (err) {
				console.warn(
					"[renderer:TroubleshootingSettingsSection] onboarding_reset IPC failed (non-fatal):",
					err,
				);
			}
			await updateConfig({ onboarding_completed: false });
			showSnack(t("settings.troubleshooting.reRunWizardToast"), "success");
			onNavigate?.("onboarding");
		};

		// Reset a stale macOS Accessibility TCC entry: the backend runs
		// `tccutil reset Accessibility <bundle-id>` (bundle ID resolved at
		// predecessor or Tauri) and re-opens System Settings so the user can
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
				console.error(
					"[renderer:TroubleshootingSettingsSection] reset_macos_accessibility failed:",
					err,
				);
				showSnack(
					t("settings.troubleshooting.resetAccessibilityFailed"),
					"error",
				);
			}
		};

		// Reset a stale Linux polkit authorization: the backend restarts
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
				console.error(
					"[renderer:TroubleshootingSettingsSection] reset_linux_permissions failed:",
					err,
				);
				showSnack(t("settings.troubleshooting.resetLinuxFailed"), "error");
			}
		};

		return (
			<SettingsSection title={title} description={description}>
				<div className="px-3.5 py-3.5 flex flex-wrap gap-3">
					{isVisible(openLogFolderLabel, undefined, title) && (
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
					)}
					{isVisible(helpFaqLabel, undefined, title) && (
						<Button
							variant="outline"
							className="gap-2"
							onClick={() =>
								void openExternalUrl(
									"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md",
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
					)}
					{isVisible(reportBugLabel, undefined, title) && (
						<Button
							variant="outline"
							className="gap-2"
							onClick={() =>
								void openExternalUrl(
									"https://github.com/AbdallahIsDev/voice-typer/issues",
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
					)}
					{isVisible(keyboardShortcutsLabel, undefined, title) && (
						<Button
							variant="outline"
							className="gap-2"
							onClick={onOpenHelp}
							aria-label={t("help.title")}
							title={t("help.description")}
							data-testid="keyboard-shortcuts-button"
						>
							<HugeiconsIcon
								icon={KeyboardIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{keyboardShortcutsLabel}
						</Button>
					)}
					{isVisible(reRunWizardLabel, undefined, title) && (
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
					)}
					{isVisible(reRunWizardLabel, undefined, title) && (
						<p className="text-xs text-muted-foreground">
							{t("settings.troubleshooting.reRunWizardHint")}
						</p>
					)}
					{isMac && isVisible(resetAccessibilityLabel, undefined, title) && (
						<div className="flex flex-col gap-1">
							<Button
								variant="outline"
								className="gap-2 self-start"
								onClick={handleResetAccessibility}
								aria-label={t(
									"settings.troubleshooting.resetAccessibilityAria",
								)}
								title={t("settings.troubleshooting.resetAccessibilityHint")}
							>
								<HugeiconsIcon
									icon={ShieldBanIcon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								{resetAccessibilityLabel}
							</Button>
							{staleResetCommand && (
								<p className="text-xs text-muted-foreground">
									{t("settings.troubleshooting.resetAccessibilitySuggestion")}
									<code className="ms-1 rounded-lg bg-muted px-1 py-0.5 font-mono text-xs">
										{staleResetCommand}
									</code>
								</p>
							)}
						</div>
					)}
					{isLinux && isVisible(resetLinuxLabel, undefined, title) && (
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
					{isVisible(resetToDefaultsLabel, undefined, title) && (
						<div className="flex w-full flex-col gap-1 border-t border-border/5 pt-3">
							<Button
								variant="destructive"
								className="gap-2 self-start"
								onClick={onResetClick}
								aria-label={t("settings.troubleshooting.resetToDefaultsAria")}
								title={t("settings.troubleshooting.resetToDefaultsHint")}
							>
								<HugeiconsIcon
									//use a trash/delete icon
									// action so it's visually distinct from
									// button (ArrowTurnBackwardIcon). The
									// previous RefreshIcon was too similar
									icon={Delete02Icon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								{resetToDefaultsLabel}
							</Button>
							<p className="text-xs text-muted-foreground">
								{t("settings.troubleshooting.resetToDefaultsHint")}
							</p>
						</div>
					)}
				</div>
			</SettingsSection>
		);
	},
);
