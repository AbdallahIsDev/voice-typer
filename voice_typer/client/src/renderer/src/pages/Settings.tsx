import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { SearchField } from "@/components/common/SearchField";
import { Spinner } from "@/components/feedback/Spinner";
import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import { ModelSettingsSection } from "@/components/settings/ModelSettingsSection";
import PrewarmAndUpdates, {
	getPrewarmAndUpdatesLabels,
} from "@/components/settings/PrewarmAndUpdates";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { SettingsSaveIndicator } from "@/components/settings/SettingsSaveIndicator";
import {
	getTabLabels,
	type SettingsTab,
} from "@/components/settings/settingsTabLabels";
import { ThemeSettingsSection } from "@/components/settings/ThemeSettingsSection";
import { TroubleshootingSettingsSection } from "@/components/settings/TroubleshootingSettingsSection";
import { useSettingsConfig } from "@/components/settings/useSettingsConfig";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { useTheme } from "@/hooks/useTheme";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

const LS_KEY = "voice-typer-settings-tab";

function getSavedTab(): SettingsTab {
	try {
		const saved = localStorage.getItem(LS_KEY);
		if (
			saved === "appearance" ||
			saved === "general" ||
			saved === "aiAudio" ||
			saved === "privacy"
		) {
			return saved;
		}
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed)
		console.warn("[Settings] loadActiveTab failed:", e);
	}
	return "general";
}

// NOTE: App.tsx prop passing will be removed by EC-FIX-13.
// EC-FIX-14 (BACKLOG-004): SettingsPage now obtains `navigate` via
// useNavigation and theme state via useTheme directly, eliminating the
// `onNavigate` / `themeMode` / `onThemeChange` prop drills from App.tsx.
export default function SettingsPage() {
	const {
		config,
		saving,
		pending,
		saved,
		updateConfig,
		updateConfigDebounced,
		loadConfig,
		mergeExternalConfig,
	} = useSettingsConfig();
	const { call } = usePython();
	// EC-FIX-14: subscribe to the theme hook directly instead of
	// receiving themeMode / onThemeChange as props from App.tsx. The
	// hook is the canonical source of theme state; calling it here
	// (in addition to App.tsx) is safe because theme state is
	// synchronised across instances via the config_changed event
	// subscription and localStorage cache (see useTheme.ts).
	const { themeMode: themeModeProp, handleThemeChange } = useTheme(call);
	// EC-FIX-14: obtain `navigate` directly from the navigation hook
	// instead of receiving it as an `onNavigate` prop from App.tsx.
	const { navigate } = useNavigation();
	const { showSnack } = useSnackbar();
	const [showResetDialog, setShowResetDialog] = useState(false);
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const [settingsFilter, setSettingsFilter] = useState("");
	// UX-18: render-phase counter for the empty-state sentinel.
	const visibleMatchCountRef = useRef(0);
	const [hasAnyVisibleRow, setHasAnyVisibleRow] = useState(true);
	const [activeTab, setActiveTab] = useState<SettingsTab>(getSavedTab);
	const scrollPositionsRef = useRef<Record<SettingsTab, number>>({
		appearance: 0,
		general: 0,
		aiAudio: 0,
		privacy: 0,
	});
	const prevTabRef = useRef(activeTab);

	// PVT-029: label-based search auto-switch. Score each tab by counting
	// label matches and switch to the highest-scoring one. Requires
	// q.length >= 2 to avoid jarring switches as the user types.
	//
	// Fix #2: supplement the privacy tab labels with PrewarmAndUpdates
	// row labels (e.g. "Prewarm cache status", "Installed version",
	// "Latest release") so the auto-switch routes queries like "prewarm",
	// "cache", "version", "update" to the privacy tab where the
	// PrewarmAndUpdates component lives. The labels are translated at the
	// moment the user types via getPrewarmAndUpdatesLabels().
	const handleSearchChange = useCallback((value: string) => {
		setSettingsFilter(value);
		const q = value.toLowerCase().trim();
		if (!q || q.length < 2) return;
		let bestTab: SettingsTab | null = null;
		let bestScore = 0;
		const tabLabels = getTabLabels();
		tabLabels.privacy = [...tabLabels.privacy, ...getPrewarmAndUpdatesLabels()];
		for (const [tab, labels] of Object.entries(tabLabels)) {
			const score = labels.filter(
				(label) =>
					label.toLowerCase().includes(q) || q.includes(label.toLowerCase()),
			).length;
			if (score > bestScore) {
				bestScore = score;
				bestTab = tab as SettingsTab;
			}
		}
		if (bestTab && bestScore > 0) setActiveTab(bestTab);
	}, []);

	const handleTabChange = useCallback(
		(tab: SettingsTab) => {
			const mainEl = document.getElementById("main-content");
			if (mainEl) scrollPositionsRef.current[activeTab] = mainEl.scrollTop;
			setActiveTab(tab);
		},
		[activeTab],
	);

	// Restore scroll position when the active tab changes.
	useEffect(() => {
		if (prevTabRef.current !== activeTab) {
			prevTabRef.current = activeTab;
			const saved = scrollPositionsRef.current[activeTab];
			if (saved > 0) {
				requestAnimationFrame(() => {
					const mainEl = document.getElementById("main-content");
					if (mainEl) mainEl.scrollTop = saved;
				});
			}
		}
	}, [activeTab]);

	useEffect(() => {
		try {
			localStorage.setItem(LS_KEY, activeTab);
		} catch (e) {
			// localStorage may be unavailable
			console.warn("[Settings] persistActiveTab failed:", e);
		}
	}, [activeTab]);

	// Skip the initial fetch when the module-level cache is populated —
	// re-renders instantly from cache instead of flashing a spinner.
	useEffect(() => {
		if (!config) loadConfig();
	}, [config, loadConfig]);

	// Live config sync — merge external `config_changed` pushes (e.g.
	// Ctrl+MouseWheel zoom, sidebar ThemeSwitch) into local state.
	usePythonEvent(
		"config_changed",
		useCallback(
			(data): (() => void) | undefined => {
				if (!data) return undefined;
				mergeExternalConfig(data as Partial<VoiceTyperConfig>);
				markUpdated();
				return undefined;
			},
			[mergeExternalConfig, markUpdated],
		),
	);

	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await loadConfig();
		} finally {
			setRefreshing(false);
		}
	}, [loadConfig]);

	const resetToDefaults = async () => {
		if (!config) return;
		setShowResetDialog(false);
		try {
			const defaults = await call<Record<string, unknown>>("get_defaults");
			if (defaults && typeof defaults === "object") {
				const safeDefaults: Record<string, unknown> = {};
				for (const [key, value] of Object.entries(defaults)) {
					if (value === "<redacted>") continue;
					if (
						[
							"schema_version",
							"wayland_warned",
							"onboarding_completed",
						].includes(key)
					)
						continue;
					safeDefaults[key] = value;
				}
				await updateConfig(safeDefaults as Partial<VoiceTyperConfig>);
				showSnack(t("settings.resetToDefaultsToast"), "success");
			} else {
				showSnack(t("settings.fetchDefaultsFailed"), "error");
			}
		} catch (err) {
			console.error("Failed to reset to defaults:", err);
			showSnack(t("settings.resetFailed"), "error");
		}
	};

	// Local wrapper around the useTheme handleThemeChange so the Color
	// Scheme Select doesn't revert while the debounced save is in flight.
	// EC-FIX-14: `onThemeChange` is now obtained from the useTheme hook
	// directly (no longer a prop from App.tsx).
	const handleThemeChangeLocal = useCallback(
		(mode: VoiceTyperConfig["theme_mode"]) => {
			mergeExternalConfig({ theme_mode: mode } as Partial<VoiceTyperConfig>);
			handleThemeChange(mode);
		},
		[mergeExternalConfig, handleThemeChange],
	);

	// UX-18: empty-state sentinel — recompute on every render and push
	// the boolean into state when it changes.
	useEffect(() => {
		const next = visibleMatchCountRef.current > 0;
		setHasAnyVisibleRow((prev) => (prev === next ? prev : next));
	});
	useEffect(() => {
		if (!settingsFilter.trim()) setHasAnyVisibleRow(true);
	}, [settingsFilter]);

	// Fix #1: filter predicate — wrapped in useCallback with
	// [settingsFilter] deps so memoized section children don't re-render
	// unless the query actually changes. Bumps a render-phase counter on
	// every positive match; the layout effect above reads the counter to
	// derive `hasAnyVisibleRow`. NOTE: declared BEFORE the `if (!config)`
	// early return so React's Rules of Hooks are satisfied (useCallback
	// is a hook and must be called unconditionally on every render).
	visibleMatchCountRef.current = 0;
	const _filter_settings = useCallback(
		(label: string, info?: string, sectionTitle?: string): boolean => {
			if (!settingsFilter.trim()) return true;
			const q = settingsFilter.toLowerCase();
			const match =
				label.toLowerCase().includes(q) ||
				info?.toLowerCase().includes(q) ||
				sectionTitle?.toLowerCase().includes(q) ||
				false;
			if (match) visibleMatchCountRef.current++;
			return match;
		},
		[settingsFilter],
	);

	if (!config) {
		return (
			<div className="flex h-full items-center justify-center">
				<div className="space-y-2 text-center">
					<Spinner size={24} className="mx-auto" />
					<p className="text-sm text-(--text-muted)">{t("settings.loading")}</p>
				</div>
			</div>
		);
	}

	const showEmptyBanner = settingsFilter.trim() !== "" && !hasAnyVisibleRow;

	const sectionProps = {
		config,
		updateConfig,
		updateConfigDebounced,
		isVisible: _filter_settings,
	};

	const renderTabPanel = (tab: SettingsTab, children: ReactNode) => (
		<div
			role="tabpanel"
			id={`panel-${tab}`}
			aria-labelledby={`tab-${tab}`}
			className="space-y-8 focus-visible:outline-none"
		>
			{children}
		</div>
	);

	return (
		<div className="flex min-h-full flex-col">
			{/* Sticky header: tabs + search (Fix #3 — SearchField
				moved inside the sticky header below the tab bar
				so it stays visible while scrolling settings). */}
			<div className="sticky top-0 left-0 right-0 z-40 bg-(--bg-subtle) border-b border-border">
				<div className="mx-auto w-full max-w-2xl px-6 py-1.5">
					<SegmentedControl<SettingsTab>
						variant="tabs"
						options={[
							{ value: "general", label: t("settings.tabs.general") },
							{ value: "aiAudio", label: t("settings.tabs.aiAudio") },
							{ value: "appearance", label: t("settings.tabs.appearance") },
							{ value: "privacy", label: t("settings.tabs.privacy") },
						]}
						value={activeTab}
						onChange={handleTabChange}
						ariaLabel={t("settings.tabsAria")}
						indicatorClassName="bg-input/50"
						labelClassName="flex-1 text-center"
						className="w-full"
						getTabId={(v: SettingsTab) => `tab-${v}`}
						getPanelId={(v: SettingsTab) => `panel-${v}`}
					/>
					<div className="flex items-center gap-3 pb-2 pt-1">
						<div className="flex-1">
							<SearchField
								value={settingsFilter}
								onChange={handleSearchChange}
								placeholder={t("settings.searchPlaceholder")}
								ariaLabel={t("settings.searchPlaceholder")}
							/>
						</div>
					</div>
				</div>
			</div>
			<div className="mx-auto w-full max-w-2xl flex-1 space-y-8 px-6 pt-6 pb-6">
				<PageHeading
					title={t("settings.title")}
					description={t("settings.description")}
				/>
				<div className="flex justify-end pb-2">
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={handleManualRefresh}
						refreshing={refreshing}
					/>
				</div>

				{/* Fix #12: empty-state banner with Clear filter button using
					the existing searchNoMatch / noResultsMessage / a11y.clearSearch
					i18n keys. `searchNoMatch` preserves the original "{query}"
					interpolation so screen readers + sighted users see what they
					searched for; `noResultsMessage` adds the actionable hint
					("Try a different search term or clear the filter..."); the
					button gives a one-click escape hatch. */}
				{showEmptyBanner && (
					<output
						aria-live="polite"
						className="block rounded-lg border border-dashed border-border bg-(--bg-subtle) px-6 py-10 text-center space-y-3"
					>
						<p className="text-sm font-medium text-(--text-primary)">
							{t("settings.searchNoMatch", { query: settingsFilter.trim() })}
						</p>
						<p className="text-sm text-(--text-muted)">
							{t("settings.noResultsMessage")}
						</p>
						<button
							type="button"
							onClick={() => setSettingsFilter("")}
							className="inline-flex items-center justify-center rounded-md border border-border bg-background px-3 py-1.5 text-sm font-medium text-(--text-primary) hover:bg-(--bg-subtle) focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none"
						>
							{t("a11y.clearSearch")}
						</button>
					</output>
				)}

				{activeTab === "appearance" &&
					renderTabPanel(
						"appearance",
						<ThemeSettingsSection
							{...sectionProps}
							themeModeProp={themeModeProp}
							onThemeChange={handleThemeChangeLocal}
						/>,
					)}

				{activeTab === "general" &&
					renderTabPanel(
						"general",
						<>
							<GeneralSettingsSection {...sectionProps} />
							<RecordingSettingsSection {...sectionProps} />
						</>,
					)}

				{activeTab === "aiAudio" &&
					renderTabPanel(
						"aiAudio",
						<>
							<ModelSettingsSection {...sectionProps} />
							<AudioSettingsSection {...sectionProps} />
							<AiEnhancementSettingsSection {...sectionProps} />
						</>,
					)}

				{activeTab === "privacy" &&
					renderTabPanel(
						"privacy",
						<>
							<PrivacySettingsSection {...sectionProps} />
							<TroubleshootingSettingsSection
								isVisible={_filter_settings}
								updateConfig={updateConfig}
								onNavigate={navigate}
								onResetClick={() => setShowResetDialog(true)}
							/>
							<PrewarmAndUpdates isVisible={_filter_settings} />
						</>,
					)}
			</div>

			{/* Fix #4: sticky-bottom save indicator — stays pinned to
				the bottom of the viewport while scrolling so
				the user always sees the pending/saving/saved
				state. Mirrors the sticky-top header (z-40,
				bg-(--bg-subtle), border-border) for visual
				rhythm. */}
			<div className="sticky bottom-0 left-0 right-0 z-40 border-t border-border bg-(--bg-subtle)">
				<div className="mx-auto flex w-full max-w-2xl justify-end px-6 py-2">
					<SettingsSaveIndicator
						saving={saving}
						pending={pending}
						saved={saved}
					/>
				</div>
			</div>

			<ConfirmDialog
				open={showResetDialog}
				title={t("settings.troubleshooting.resetToDefaults")}
				message={t("settings.troubleshooting.resetDialogMessage")}
				confirmLabel={t("settings.troubleshooting.resetToDefaults")}
				cancelLabel={t("common.cancel")}
				variant="destructive"
				onConfirm={resetToDefaults}
				onCancel={() => setShowResetDialog(false)}
			/>
		</div>
	);
}
