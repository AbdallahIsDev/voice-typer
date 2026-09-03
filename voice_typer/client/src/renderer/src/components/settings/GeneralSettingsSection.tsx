// GeneralSettingsSection — the General section of the Settings surface.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders one
// SettingsSection block: "General" (Launch at Login, Fast Startup,
// Offline Engine Pack, App Language, Notifications, Tray Click).
// The Overlay card that used to be rendered beneath it moved to its own
// component (OverlaySettingsSection.tsx) and its own section page
// (settingsOverlay). Behaviour is identical to the previous combined
// implementation, including the per-row search-filter visibility via the
// `isVisible` prop and the section-level "hide if no items match" check.

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
	pushLocaleToPythonBackend,
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

// Locale selector options — derived from SUPPORTED_LOCALES so adding a
// new locale in i18n.ts automatically appears here. The labels are
// locale-name strings ("English", "العربية", …) which don't go through
// t() — they're the same in every UI language.
const LOCALE_OPTIONS = SUPPORTED_LOCALES.map((locale) => ({
	value: locale,
	label: getLocaleLabel(locale),
}));

// B-REVIEW-3 (Finding 3): the *_LABEL / *_INFO constants below USED TO
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
	//prewarm / fast_startup toggle. Lives under General because
	// it's a startup-behaviour setting alongside "Launch at Login".
	// Defaults ON.
	const FAST_STARTUP_LABEL = t("settings.fastStartup");
	const FAST_STARTUP_INFO = t("settings.fastStartupDescription");
	//runtime-pack download consent (auto-update feature,
	// docs/auto-update-feature.md §8.4). Defaults OFF — the pack is
	// never downloaded without explicit opt-in (C-DATA-1 consent gate).
	const RUNTIME_PACK_CONSENT_LABEL = t("settings.offlinePackConsent");
	const RUNTIME_PACK_CONSENT_INFO = t("settings.offlinePackConsentDescription");

	//section-level visibility check for the General section. The title
	// constant feeds BOTH the `<SettingsSection title>` prop AND the
	// `isVisible` third parameter, so search matches the heading the
	// user actually sees.
	const generalSectionTitle = t("settings.general");
	const generalItems = [
		{ label: LAUNCH_AT_LOGIN_LABEL, info: LAUNCH_AT_LOGIN_INFO },
		{ label: APP_LANGUAGE_LABEL, info: APP_LANGUAGE_INFO },
		{ label: NOTIFICATIONS_LABEL, info: NOTIFICATIONS_INFO },
		{ label: TRAY_CLICK_LABEL, info: TRAY_CLICK_INFO },
		{ label: FAST_STARTUP_LABEL, info: FAST_STARTUP_INFO },
		{ label: RUNTIME_PACK_CONSENT_LABEL, info: RUNTIME_PACK_CONSENT_INFO },
	];
	const generalVisible = generalItems.some((item) =>
		isVisible(item.label, item.info, generalSectionTitle),
	);

	// ── Inline handler extraction ─────────────────────────────────
	const handleAutostartChange = (checked: boolean) =>
		updateConfig({ autostart: checked });
	const handleFastStartupChange = (checked: boolean) =>
		updateConfig({ fast_startup: checked });
	const handleRuntimePackConsentChange = (checked: boolean) =>
		updateConfig({ offline_pack_consent: checked });
	const handleNotificationsChange = (checked: boolean) =>
		updateConfig({ show_notifications: checked });
	const handleTrayClickChange = (v: string) =>
		updateConfig({
			tray_left_click_action: v as "open_app" | "toggle_dictation",
		});

	if (!generalVisible) return null;

	return (
		<SettingsSection
			title={generalSectionTitle}
			description={t("settings.generalDescription")}
		>
			{isVisible(
				LAUNCH_AT_LOGIN_LABEL,
				LAUNCH_AT_LOGIN_INFO,
				generalSectionTitle,
			) && (
				<SettingRow label={LAUNCH_AT_LOGIN_LABEL} info={LAUNCH_AT_LOGIN_INFO}>
					<Switch
						checked={config.autostart}
						onCheckedChange={handleAutostartChange}
						aria-label={LAUNCH_AT_LOGIN_LABEL}
					/>
				</SettingRow>
			)}
			{/*Fast Startup (prewarm) toggle — defaults ON.
                                Disabling saves ~6 GB of disk reads at boot for users who
                                don't want the prewarm process (gamers, low-RAM machines).
                                : the "Run Prewarm Now" button lives in
                                Settings → Advanced → Cache Status
                                (PrewarmAndUpdates.tsx), NOT on the About page. The
                                `fastStartupDescription` i18n string now points users at
                                "Settings → Advanced → Cache Status" across all
                                8 locale files (en/ar/de/es/fr/hi/ru/zh). */}
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
			{/*Offline engine pack download consent (auto-update feature,
                                docs/auto-update-feature.md §8.4). Defaults OFF — the pack is
                                never downloaded without explicit opt-in. When enabled, the
                                network-is-back trigger (useNetworkOnline) can start a
                                consent-gated background download of the offline engines. */}
			{isVisible(
				RUNTIME_PACK_CONSENT_LABEL,
				RUNTIME_PACK_CONSENT_INFO,
				generalSectionTitle,
			) && (
				<SettingRow
					label={RUNTIME_PACK_CONSENT_LABEL}
					info={RUNTIME_PACK_CONSENT_INFO}
				>
					<Switch
						checked={config.offline_pack_consent ?? false}
						onCheckedChange={handleRuntimePackConsentChange}
						aria-label={RUNTIME_PACK_CONSENT_LABEL}
					/>
				</SettingRow>
			)}
			{/*App Language selector — distinct from the spoken-language
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
							} catch (e) {
								// localStorage may be unavailable in some contexts
								// (SSR, sandboxed renderer, quota exceeded).
								console.warn(
									"[renderer:GeneralSettingsSection] setItem locale failed:",
									e,
								);
							}
							// Delegate tray-locale dispatch to the i18n module's
							// `pushLocaleToPythonBackend` helper so this component
							// does not invoke the Python bridge directly
							// (the PythonBridge type only exposes `call` and
							// `onEvent` — direct calls bypass the i18n contract
							// and re-introduce the delegation-boundary violation).
							try {
								pushLocaleToPythonBackend(v as Locale);
							} catch (e) {
								// IPC may not be available during startup or the
								// backend may not yet have registered the route.
								console.warn(
									"[renderer:GeneralSettingsSection] set_tray_locale IPC failed:",
									e,
								);
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
			)}
			{isVisible(TRAY_CLICK_LABEL, TRAY_CLICK_INFO, generalSectionTitle) && (
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
	);
});
