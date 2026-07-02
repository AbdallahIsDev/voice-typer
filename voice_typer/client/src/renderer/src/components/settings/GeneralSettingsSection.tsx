// GeneralSettingsSection — General + Overlay sections of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "General" (Launch at Login, UI Language,
// Notifications, Tray Click) and "Overlay" (Bubble Behavior, Bubble
// Position, Show on App Startup, Drag to Move). Behaviour is identical
// to the previous monolithic implementation, including the per-row
// search-filter visibility via the `isVisible` prop and the section-
// level "hide if no items match" check for the General section.

import { memo } from "react";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
	getLocale,
	getLocaleLabel,
	SUPPORTED_LOCALES,
	setLocale,
	t,
} from "@/i18n/i18n";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

const TRAY_CLICK_OPTIONS = [
	{ value: "open_app", label: "Open App" },
	{ value: "toggle_dictation", label: "Toggle Dictation" },
] as const;

const BUBBLE_POSITION_OPTIONS = [
	{ value: "top", label: "Top Center" },
	{ value: "bottom", label: "Bottom Center" },
] as const;

const BUBBLE_BEHAVIOR_OPTIONS = [
	{ value: "show_on_record", label: "Show on Record" },
	{ value: "always_visible", label: "Always Visible" },
] as const;

export const GeneralSettingsSection = memo(function GeneralSettingsSection({
	config,
	updateConfig,
	isVisible,
}: SettingsSectionSharedProps) {
	if (!config) return <SettingsSkeleton rows={3} />;

	// UX-028: section-level visibility check for the General section.
	const generalItems = [
		{
			label: "Launch at Login",
			info: "Automatically start Voice Typer when you log into Windows.",
		},
		{
			label: "Notifications",
			info: "Show a desktop notification when transcription completes or an error occurs.",
		},
		{
			label: "Tray Click",
			info: "What happens when you left-click the Voice Typer icon in the system tray.",
		},
	];
	const generalVisible = generalItems.some((item) =>
		isVisible(item.label, item.info),
	);

	return (
		<>
			{/* ── SECTION: General ──────────────────────────────────── */}
			{generalVisible && (
				<SettingsSection
					title={t("settings.general")}
					description="Behavior, startup, and appearance."
				>
					{isVisible(
						"Launch at Login",
						"Automatically start Voice Typer when you log into Windows.",
					) && (
						<SettingRow
							label="Launch at Login"
							info="Automatically start Voice Typer when you log into Windows."
						>
							<Switch
								checked={config.autostart}
								onCheckedChange={(checked) =>
									updateConfig({ autostart: checked })
								}
								aria-label="Launch at Login"
							/>
						</SettingRow>
					)}

					{/* UX-015: UI Language selector — distinct from the spoken-language
                                                selector in Post-Processing. This controls the Electron UI
                                                language via the i18n framework. The choice is persisted to
                                                localStorage so it survives restarts, and pushed to the
                                                Python backend so the tray menu labels also switch language. */}
					<SettingRow
						label="UI Language"
						info="Choose the interface language for Voice Typer."
					>
						<Select
							value={getLocale()}
							onValueChange={(v) => {
								setLocale(v as "en" | "es");
								// Persist to localStorage so the choice survives restarts
								try {
									localStorage.setItem("voice-typer-ui-locale", v);
								} catch {
									// localStorage may be unavailable in some contexts
								}
								// TRAY-008: push the locale to the Python backend so
								// the tray menu labels also switch language.
								try {
									void window.python?.call({
										type: "set_tray_locale",
										data: { locale: v },
									});
								} catch {
									// IPC may not be available during startup
								}
								// Force a re-render so all t() calls update
								window.location.reload();
							}}
						>
							<SelectTrigger className="w-44" aria-label="UI Language">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{SUPPORTED_LOCALES.map((locale) => (
									<SelectItem key={locale} value={locale}>
										<span>{getLocaleLabel(locale)}</span>
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>

					{isVisible("Notifications", "Show a desktop notification") && (
						<SettingRow
							label="Notifications"
							info="Show a desktop notification when transcription completes or an error occurs."
						>
							<Switch
								checked={config.show_notifications}
								onCheckedChange={(checked) =>
									updateConfig({ show_notifications: checked })
								}
								aria-label="Notifications"
							/>
						</SettingRow>
					)}

					{isVisible("Tray Click", "What happens when you left-click") && (
						<SettingRow
							label="Tray Click"
							info="What happens when you left-click the Voice Typer icon in the system tray."
						>
							<Select
								value={config.tray_left_click_action ?? "open_app"}
								onValueChange={(v) =>
									updateConfig({
										tray_left_click_action: v as
											| "open_app"
											| "toggle_dictation",
									})
								}
							>
								<SelectTrigger className="w-40" aria-label="Tray Click">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{TRAY_CLICK_OPTIONS.map((opt) => (
										<SelectItem key={opt.value} value={opt.value}>
											{opt.label}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</SettingRow>
					)}
				</SettingsSection>
			)}

			{/* ── SECTION: Overlay ──────────────────────────────────── */}
			<SettingsSection title="Overlay" description="Floating recording bubble.">
				{/* ── Dropdowns ──────────────────────────────────────── */}
				<SettingRow
					label="Bubble Behavior"
					info="Show the bubble only while recording, or keep it visible at all times."
				>
					<Select
						value={config.bubble_behavior ?? "show_on_record"}
						onValueChange={(v) => {
							updateConfig({
								bubble_behavior: v as "show_on_record" | "always_visible",
							});
						}}
					>
						<SelectTrigger className="w-40" aria-label="Bubble Behavior">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							{BUBBLE_BEHAVIOR_OPTIONS.map((opt) => (
								<SelectItem key={opt.value} value={opt.value}>
									{opt.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				</SettingRow>

				<SettingRow
					label="Bubble Position"
					info="Where the bubble appears on screen — top or bottom center."
				>
					<Select
						value={config.bubble_position ?? "bottom"}
						onValueChange={(v) => {
							updateConfig({ bubble_position: v as "top" | "bottom" });
							// Notify the main process immediately so the bubble repositions.
							window.bubble?.setPosition?.(v);
						}}
					>
						<SelectTrigger className="w-40" aria-label="Bubble Position">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							{BUBBLE_POSITION_OPTIONS.map((opt) => (
								<SelectItem key={opt.value} value={opt.value}>
									{opt.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				</SettingRow>

				{/* ── Switches ───────────────────────────────────────── */}
				{/* Show on app startup toggle — only visible when Always Visible is selected */}
				{config.bubble_behavior === "always_visible" && (
					<SettingRow
						label="Show on App Startup"
						info="Show the bubble as soon as the app opens. When off, it appears only when you start recording."
					>
						<Switch
							checked={config.bubble_show_on_startup ?? true}
							onCheckedChange={(checked) =>
								updateConfig({ bubble_show_on_startup: checked })
							}
							aria-label="Show on App Startup"
						/>
					</SettingRow>
				)}

				<SettingRow
					label="Drag to Move"
					info="Allow dragging the bubble with your mouse to reposition it on screen."
				>
					<Switch
						checked={config.bubble_draggable ?? true}
						onCheckedChange={(checked) => {
							updateConfig({ bubble_draggable: checked });
							// Notify the main process immediately so the bubble responds.
							window.bubble?.setDraggable?.(checked);
						}}
						aria-label="Drag to Move"
					/>
				</SettingRow>
			</SettingsSection>
		</>
	);
});
