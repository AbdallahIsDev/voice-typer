// GeneralSettingsSection — General + Overlay sections of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "General" (Launch at Login, App Language,
// Notifications, Tray Click) and "Overlay" (Bubble Behavior, Bubble
// Position, Show on App Startup, Drag to Move). Behaviour is identical
// to the previous monolithic implementation, including the per-row
// search-filter visibility via the `isVisible` prop and the section-
// level "hide if no items match" check for the General section.

import { memo } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { SegmentedControl } from "@/components/ui/segmented-control";
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
	type Locale,
	SUPPORTED_LOCALES,
	setLocale,
	useT,
} from "@/i18n/i18n";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

const TRAY_CLICK_OPTIONS = [
	{ value: "toggle_dictation", labelKey: "settings.trayClickToggleDictation" },
	{ value: "open_app", labelKey: "settings.trayClickOpenApp" },
] as const;

const BUBBLE_BEHAVIOR_OPTIONS = [
	{ value: "always_visible", labelKey: "settings.bubbleBehaviorAlwaysVisible" },
	{ value: "show_on_record", labelKey: "settings.bubbleBehaviorShowOnRecord" },
] as const;

// Locale selector options — derived from SUPPORTED_LOCALES so adding a
// new locale in i18n.ts automatically appears here. The labels are
// locale-name strings ("English", "العربية", …) which don't go through
// t() — they're the same in every UI language.
const LOCALE_OPTIONS = SUPPORTED_LOCALES.map((locale) => ({
	value: locale,
	label: getLocaleLabel(locale),
}));

// B-REVIEW-3 (Finding 3): the 10 *_LABEL / *_INFO constants below USED TO
// live at module scope. Because ``t()`` is a plain function that reads a
// module-level ``_currentLocale`` variable, evaluating them at import time
// FROZE the strings to whatever locale was active on first import.
//
// They are now computed INSIDE the component body, so each render
// re-resolves them against the CURRENT locale. The locale switcher no
// longer calls ``window.location.reload()``: ``setLocale`` (i18n.ts)
// notifies subscribers via ``subscribeLocale``, and ``useT()``
// (useSyncExternalStore) re-renders this section — and every other
// subscribed component — in place when the locale changes. The App root
// subscribes so the whole tree cascades a re-render; memoized sections
// (like this one) subscribe directly via ``useT()``. Covered by
// GeneralSettingsSection.test.tsx.

export const GeneralSettingsSection = memo(function GeneralSettingsSection({
	config,
	updateConfig,
	isVisible,
}: SettingsSectionSharedProps) {
	// F-3: subscribe to locale changes so this section repaints in the
	// new language without a full page reload.
	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	// B-REVIEW-3: resolve label/info strings INSIDE the component
	// body so they follow the current locale. Module-level consts
	// froze them at import time.
	const LAUNCH_AT_LOGIN_LABEL = t("settings.launchAtLogin");
	const LAUNCH_AT_LOGIN_INFO = t("settings.launchAtLoginDescription");
	const NOTIFICATIONS_LABEL = t("settings.notifications");
	const NOTIFICATIONS_INFO = t("settings.notificationsDescription");
	const TRAY_CLICK_LABEL = t("settings.trayClick");
	const TRAY_CLICK_INFO = t("settings.trayClickDescription");
	const APP_LANGUAGE_LABEL = t("settings.appLanguage");
	const APP_LANGUAGE_INFO = t("settings.appLanguageDescription");
	// PW-3: prewarm / fast_startup toggle. Lives under General because
	// it's a startup-behaviour setting alongside "Launch at Login".
	// Defaults ON.
	const FAST_STARTUP_LABEL = t("settings.fastStartup");
	const FAST_STARTUP_INFO = t("settings.fastStartupDescription");

	// UX-028: section-level visibility check for the General section.
	const generalSectionTitle = t("settings.general");
	const generalItems = [
		{ label: LAUNCH_AT_LOGIN_LABEL, info: LAUNCH_AT_LOGIN_INFO },
		{ label: APP_LANGUAGE_LABEL, info: APP_LANGUAGE_INFO },
		{ label: NOTIFICATIONS_LABEL, info: NOTIFICATIONS_INFO },
		{ label: TRAY_CLICK_LABEL, info: TRAY_CLICK_INFO },
		{ label: FAST_STARTUP_LABEL, info: FAST_STARTUP_INFO },
	];
	const generalVisible = generalItems.some((item) =>
		isVisible(item.label, item.info, generalSectionTitle),
	);

	// UX-028: section-level visibility check for the Overlay section.
	const overlaySectionTitle = t("settings.overlay");
	const overlayItems = [
		{
			label: t("settings.bubbleBehaviorLabel"),
			info: t("settings.bubbleBehaviorInfo"),
		},
		{
			label: t("settings.bubblePositionLabel"),
			info: t("settings.bubblePositionInfo"),
		},
		{
			label: t("settings.showOnAppStartup"),
			info: t("settings.showOnAppStartupInfo"),
		},
		{
			label: t("settings.dragToMove"),
			info: t("settings.dragToMoveInfo"),
		},
	];
	const overlayVisible = overlayItems.some((item) =>
		isVisible(item.label, item.info, overlaySectionTitle),
	);

	// ── Inline handler extraction ─────────────────────────────────
	const handleAutostartChange = (checked: boolean) =>
		updateConfig({ autostart: checked });
	const handleFastStartupChange = (checked: boolean) =>
		updateConfig({ fast_startup: checked });
	const handleNotificationsChange = (checked: boolean) =>
		updateConfig({ show_notifications: checked });
	const handleTrayClickChange = (v: string) =>
		updateConfig({
			tray_left_click_action: v as "open_app" | "toggle_dictation",
		});
	const handleBubbleBehaviorChange = (v: string) =>
		updateConfig({
			bubble_behavior: v as "show_on_record" | "always_visible",
		});
	const handleBubblePositionChange = (v: string) => {
		updateConfig({ bubble_position: v as "top" | "bottom" });
		window.bubble?.setPosition?.(v);
	};
	const handleBubbleStartupChange = (checked: boolean) =>
		updateConfig({ bubble_show_on_startup: checked });
	const handleDragToMoveChange = (checked: boolean) => {
		updateConfig({ bubble_draggable: checked });
		window.bubble?.setDraggable?.(checked);
	};

	return (
		<>
			{/* ── SECTION: General ──────────────────────────────────── */}
			{generalVisible && (
				<SettingsSection
					title={t("settings.general")}
					description={t("settings.generalDescription")}
				>
					{isVisible(
						LAUNCH_AT_LOGIN_LABEL,
						LAUNCH_AT_LOGIN_INFO,
						generalSectionTitle,
					) && (
						<SettingRow
							label={LAUNCH_AT_LOGIN_LABEL}
							info={LAUNCH_AT_LOGIN_INFO}
						>
							<Switch
								checked={config.autostart}
								onCheckedChange={handleAutostartChange}
								aria-label={LAUNCH_AT_LOGIN_LABEL}
							/>
						</SettingRow>
					)}
					{/* PW-3: Fast Startup (prewarm) toggle — defaults ON.
						Disabling saves ~6 GB of disk reads at boot for users who
						don't want the prewarm process (gamers, low-RAM machines).
						The "Run Prewarm Now" button on the About page remains
						available for on-demand warming. */}
					{isVisible(
						FAST_STARTUP_LABEL,
						FAST_STARTUP_INFO,
						generalSectionTitle,
					) && (
						<SettingRow label={FAST_STARTUP_LABEL} info={FAST_STARTUP_INFO}>
							<Switch
								checked={config.fast_startup ?? true}
								onCheckedChange={handleFastStartupChange}
								aria-label={FAST_STARTUP_LABEL}
							/>
						</SettingRow>
					)}
					{/* UX-015: App Language selector — distinct from the spoken-language
						selector in Post-Processing. This controls the Electron UI
						language via the i18n framework. The choice is persisted to
						localStorage so it survives restarts, and pushed to the
						Python backend so the tray menu labels also switch language. */}
					{isVisible(
						APP_LANGUAGE_LABEL,
						APP_LANGUAGE_INFO,
						generalSectionTitle,
					) && (
						<SettingRow label={APP_LANGUAGE_LABEL} info={APP_LANGUAGE_INFO}>
							<Select
								value={getLocale()}
								onValueChange={(v) => {
									setLocale(v as Locale);
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
								}}
							>
								<SelectTrigger className="w-44" aria-label={APP_LANGUAGE_LABEL}>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{LOCALE_OPTIONS.map((opt) => (
										<SelectItem key={opt.value} value={opt.value}>
											<span>{opt.label}</span>
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</SettingRow>
					)}
					{isVisible(
						NOTIFICATIONS_LABEL,
						NOTIFICATIONS_INFO,
						generalSectionTitle,
					) && (
						<SettingRow label={NOTIFICATIONS_LABEL} info={NOTIFICATIONS_INFO}>
							<Switch
								checked={config.show_notifications}
								onCheckedChange={handleNotificationsChange}
								aria-label={NOTIFICATIONS_LABEL}
							/>
						</SettingRow>
					)}{" "}
					{isVisible(
						TRAY_CLICK_LABEL,
						TRAY_CLICK_INFO,
						generalSectionTitle,
					) && (
						<SettingRow label={TRAY_CLICK_LABEL} info={TRAY_CLICK_INFO}>
							<SegmentedControl
								options={TRAY_CLICK_OPTIONS.map((opt) => ({
									value: opt.value,
									label: t(opt.labelKey),
								}))}
								value={config.tray_left_click_action ?? "open_app"}
								onChange={handleTrayClickChange}
								ariaLabel={TRAY_CLICK_LABEL}
							/>
						</SettingRow>
					)}
				</SettingsSection>
			)}

			{/* ── SECTION: Overlay ──────────────────────────────────── */}
			{overlayVisible && (
				<SettingsSection
					title={overlaySectionTitle}
					description={t("settings.overlayDescription")}
				>
					{/* ── Dropdowns ──────────────────────────────────────── */}
					<SettingRow
						label={t("settings.bubbleBehaviorLabel")}
						info={t("settings.bubbleBehaviorInfo")}
					>
						<SegmentedControl
							options={BUBBLE_BEHAVIOR_OPTIONS.map((opt) => ({
								value: opt.value,
								label: t(opt.labelKey),
							}))}
							value={config.bubble_behavior ?? "show_on_record"}
							onChange={handleBubbleBehaviorChange}
							ariaLabel={t("settings.bubbleBehaviorLabel")}
						/>
					</SettingRow>

					<SettingRow
						label={t("settings.bubblePositionLabel")}
						info={t("settings.bubblePositionInfo")}
					>
						<SegmentedControl
							options={[
								{ value: "top", label: t("settings.bubblePositionTop") },
								{ value: "bottom", label: t("settings.bubblePositionBottom") },
							]}
							value={config.bubble_position ?? "bottom"}
							onChange={handleBubblePositionChange}
							ariaLabel={t("settings.bubblePositionLabel")}
						/>
					</SettingRow>

					{/* ── Switches ───────────────────────────────────────── */}
					{/* Show on app startup toggle — only visible when Always Visible is selected */}
					{config.bubble_behavior === "always_visible" && (
						<SettingRow
							label={t("settings.showOnAppStartup")}
							info={t("settings.showOnAppStartupInfo")}
						>
							<Switch
								checked={config.bubble_show_on_startup ?? true}
								onCheckedChange={handleBubbleStartupChange}
								aria-label={t("settings.showOnAppStartup")}
							/>
						</SettingRow>
					)}

					<SettingRow
						label={t("settings.dragToMove")}
						info={t("settings.dragToMoveInfo")}
					>
						<Switch
							checked={config.bubble_draggable ?? true}
							onCheckedChange={handleDragToMoveChange}
							aria-label={t("settings.dragToMove")}
						/>
					</SettingRow>
				</SettingsSection>
			)}
		</>
	);
});
