import {
	AlertCircleIcon,
	ArrowLeft01Icon,
	Search01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { SettingsPageSkeleton } from "@/components/feedback/skeletons";
import { HelpOverlay } from "@/components/help/HelpOverlay";
import { configHotkeyLabels } from "@/components/hotkey/hotkey-format";
// amber banner shown when the OS has not granted the
// keyboard-monitoring (Accessibility / input-group) permission. Mirrors
// the MicrophonePermissionBanner placement on the Microphone page.
import { KeyboardPermissionBanner } from "@/components/KeyboardPermissionBanner";
import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
import { DiagnosticsSettingsSection } from "@/components/settings/DiagnosticsSettingsSection";
import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import { LinuxWindowButtonsSettingsSection } from "@/components/settings/LinuxWindowButtonsSettingsSection";
import { LlmPolishingSettingsSection } from "@/components/settings/LlmPolishingSettingsSection";
import { OverlaySettingsSection } from "@/components/settings/OverlaySettingsSection";
import { PostProcessingSettingsSection } from "@/components/settings/PostProcessingSettingsSection";
import PrewarmAndUpdates from "@/components/settings/PrewarmAndUpdates";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { ResourcesSettingsSection } from "@/components/settings/ResourcesSettingsSection";
import { SettingsHub } from "@/components/settings/SettingsHub";
import {
	isSettingsSectionPage,
	SECTION_TITLE_BY_PAGE,
	type SettingsSectionPage,
} from "@/components/settings/settingsSections";
import { ThemeSettingsSection } from "@/components/settings/ThemeSettingsSection";
import { TroubleshootingSettingsSection } from "@/components/settings/TroubleshootingSettingsSection";
import { useSettingsConfig } from "@/components/settings/useSettingsConfig";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { useTheme } from "@/hooks/useTheme";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";
import { useSettingsDeepLinks } from "./settings/hooks/useSettingsDeepLinks";
import { useSettingsReset } from "./settings/hooks/useSettingsReset";
import { useSettingsSearch } from "./settings/hooks/useSettingsSearch";
import { useSettingsSurfaceScroll } from "./settings/hooks/useSettingsSurfaceScroll";

/**
 * Back affordance for a Settings section page: a compact ghost row that
 * returns to the Settings hub. Deliberately NOT a PageHeading — every
 * section component renders its own `<SettingsSection title>` card
 * header, so a page-level heading would duplicate the title right
 * below it. Top-level (not inline in the page component) so React can
 * skip re-creating the element type on every render.
 */
function SectionBackButton({ onBack }: { onBack: () => void }) {
	return (
		<button
			type="button"
			data-testid="settings-back-to-hub"
			aria-label={t("settings.hub.backToSettings")}
			className="inline-flex items-center gap-2 rounded-md px-2 py-1 text-sm text-(--text-muted) transition-colors duration-150 hover:bg-foreground/5 hover:text-(--text-primary) focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
			onClick={onBack}
		>
			{/* Left-pointing chevron — mirrored in RTL by the shared
                            directional-icon rule (index.css) so it always points
                            "back". */}
			<HugeiconsIcon
				icon={ArrowLeft01Icon}
				strokeWidth={2}
				aria-hidden="true"
				className="nav-directional-icon h-4 w-4"
			/>
			{t("nav.settings")}
		</button>
	);
}

interface SettingsPageProps {
	/**
	 * The active Settings surface: `"settings"` (the hub — one card of
	 * section rows) or one of the section pages (a focused page rendering
	 * only that domain's cards). The nav store is the source of truth;
	 * App.tsx's route switch passes the literal. Defaults to the hub.
	 */
	page?: Page;
}

// Settings page = HUB + nested section pages. The hub (`page ===
// "settings"`) renders ONE card whose rows open the section pages
// (see SettingsHub + settingsSections.ts). Each section page renders
// only its own domain's cards, so the user edits one concern at a time
// instead of scrolling a stack of unrelated sections.
//
// The per-page SearchField + sticky header are gone — the search query
// lives in the global `useGlobalSearch` store (title-bar
// GlobalSearchBar). On the hub a query FILTERS the section rows (and
// lists the matched row labels under each row); on a section page the
// query filters rows in place via the sections' `isVisible` predicate,
// the auto-switch navigates to a better-matching section page (via
// `navigate(bestPage, { settingsScrollTarget: { rowHint } })`), and the
// cross-section results card lists matches from OTHER section pages.
// The transient `pendingSettingsScrollTarget` field is consumed on
// mount + page change to scroll to + briefly highlight the matched row.
export default function SettingsPage({ page = "settings" }: SettingsPageProps) {
	const {
		config,
		updateConfig,
		updateConfigDebounced,
		loadConfig,
		loadError,
		error: saveError,
		mergeExternalConfig,
	} = useSettingsConfig();
	const { call } = usePython();
	//subscribe to the theme hook directly instead of
	// receiving themeMode / onThemeChange as props from App.tsx. The
	// hook is the canonical source of theme state; calling it here
	// (in addition to App.tsx) is safe because theme state is
	// synchronised across instances via the config_changed event
	// subscription and localStorage cache (see useTheme.ts).
	const { themeMode: themeModeProp, handleThemeChange } = useTheme(call);
	//obtain `navigate` directly from the navigation hook
	// instead of receiving it as an `onNavigate` prop from App.tsx.
	// (The consent + search deep-link channels are consumed inside
	// useSettingsDeepLinks — this page only navigates.)
	const { navigate } = useNavigation();
	const { showSnack } = useSnackbar();
	// Local help-overlay state for the Troubleshooting "Keyboard
	// Shortcuts" button. The app-level instance lives in App.tsx
	// (`useHelpOverlayShortcut`), which this page can't reach — a second
	// mount of the SAME shared component keeps the mechanism reused
	// without a global event bus. The `?`-shortcut's dialog guard
	// (`[role="dialog"][data-state="open"]`) prevents the two instances
	// from ever stacking.
	const [helpOpen, setHelpOpen] = useState(false);
	const settingsFilter = useGlobalSearch((s) => s.query);
	const clearQuery = useGlobalSearch((s) => s.clearQuery);
	// The active surface is DERIVED from the current page literal
	// (passed in as `page` prop by the route switch). The nav store is
	// the single source of truth. The scroll-positions ref survives
	// across surface transitions inside the Settings component instance
	// (hub ↔ section pages share it via the `page` prop swap), so the
	// per-surface scroll-restore behavior is preserved. Keyed by the raw
	// page literal (including the hub's "settings") so every surface
	// remembers its own scroll offset.
	const activeSection: SettingsSectionPage | null = isSettingsSectionPage(page)
		? page
		: null;
	// Per-surface scroll-offset memory, keyed by the raw page literal
	// (including the hub's "settings"). Owned by the page and shared by
	// the scroll hooks below: the deep-link consumption zeroes the
	// Privacy surface's saved offset BEFORE the surface-scroll restore
	// effect reads it, so the hook call order here is load-bearing.
	const scrollPositionsRef = useRef<Record<string, number>>({});

	// Deep-link machinery — consent + cross-page search targets
	// (consume → scroll-to-row → highlight ring, with the shared
	// one-shot guard, ring-lifetime timer, and max-lifetime safety
	// net). See useSettingsDeepLinks; must be called BEFORE
	// useSettingsSurfaceScroll so the consumption runs first.
	const { focusedConsentField } = useSettingsDeepLinks({
		config,
		page,
		scrollPositionsRef,
	});
	// Search derivations + label auto-switch: ONE memoized label
	// universe + ONE shared match predicate feeding the empty-banner
	// sentinel, the cross-section result groups, and the auto-switch
	// (see useSettingsSearch).
	const { hasAnyVisibleRow, otherSectionGroups } = useSettingsSearch({
		query: settingsFilter,
		activeSection,
		navigate,
	});
	// Restore the active surface's saved scroll offset on hub ↔ section
	// transitions (see useSettingsSurfaceScroll).
	useSettingsSurfaceScroll({ page, scrollPositionsRef });
	// Reset-to-defaults flow — confirm-dialog state + the guarded
	// defaults fetch/apply (see useSettingsReset).
	const { showResetDialog, setShowResetDialog, resetToDefaults } =
		useSettingsReset({ config, call, updateConfig, showSnack });

	// Always re-fetch on mount, even when the module-level cache is
	// populated. Pre-fix, the `if (!config)` guard short-circuited the
	// fetch whenever `_cachedConfig` was non-null — so a user who
	// changed `audio_preset` (or any audio filter) on the Microphone
	// page, or `model_size` / `asr_backend` on the Models page, would
	// see the STALE cached value when they navigated to Settings. The
	// `mergeExternalConfig` subscription (config_changed → cache
	// update) only fires while Settings is mounted, so cross-page
	// edits made while Settings was unmounted were lost on re-mount.
	// The page still renders instantly from the cached value (the
	// state initializer seeds `config` from `_cachedConfig`), then
	// re-renders with the fresh value when `loadConfig` resolves —
	// matching how `useModelConfig` (Models page) and the Microphone
	// page already behave.
	useEffect(() => {
		void loadConfig();
	}, [loadConfig]);

	// Live config sync — merge external `config_changed` pushes (e.g.
	// Ctrl+MouseWheel zoom, sidebar ThemeSwitch) into local state.
	usePythonEvent(
		"config_changed",
		useCallback(
			(data): (() => void) | undefined => {
				if (!data) return undefined;
				mergeExternalConfig(data as Partial<VoiceTyperConfig>);
				return undefined;
			},
			[mergeExternalConfig],
		),
	);

	// Local wrapper around the useTheme handleThemeChange so the Color
	// Scheme Select doesn't revert while the debounced save is in flight.
	//`onThemeChange` is now obtained from the useTheme hook
	// directly (no longer a prop from App.tsx).
	const handleThemeChangeLocal = useCallback(
		(mode: VoiceTyperConfig["theme_mode"]) => {
			mergeExternalConfig({ theme_mode: mode } as Partial<VoiceTyperConfig>);
			handleThemeChange(mode);
		},
		[mergeExternalConfig, handleThemeChange],
	);

	// Help-overlay labels derived from the user's config via the shared
	// configHotkeyLabels helper (same source App.tsx's overlay uses).
	const helpLabels = useMemo(
		() =>
			configHotkeyLabels({
				hotkey: config?.hotkey ?? null,
				repaste_hotkey: config?.repaste_hotkey ?? null,
			}),
		[config?.hotkey, config?.repaste_hotkey],
	);

	// Filter predicate — wrapped in useCallback with
	// [settingsFilter] deps so memoized section children don't re-render
	// unless the query actually changes. This is a PURE predicate (no
	// render-phase side effect) — `hasAnyVisibleRow` is derived above via
	// useMemo from the same label set. NOTE: declared BEFORE the
	// `if (!config)` early return so React's Rules of Hooks are
	// satisfied (useCallback is a hook and must be called unconditionally
	// on every render).
	const _filter_settings = useCallback(
		(label: string, info?: string, sectionTitle?: string): boolean => {
			if (!settingsFilter.trim()) return true;
			const q = settingsFilter.toLowerCase();
			return (
				label.toLowerCase().includes(q) ||
				info?.toLowerCase().includes(q) ||
				sectionTitle?.toLowerCase().includes(q) ||
				false
			);
		},
		[settingsFilter],
	);

	const sectionProps = useMemo(
		() => ({
			config,
			updateConfig,
			updateConfigDebounced,
			isVisible: _filter_settings,
		}),
		[config, updateConfig, updateConfigDebounced, _filter_settings],
	);

	// Which section page's cards render — a single data-driven switch so
	// the hub/section split stays declarative. Top-level (not inline in
	// JSX) so no component types are re-created during render.
	const renderSectionCards = (active: SettingsSectionPage) => {
		switch (active) {
			case "settingsGeneral":
				return <GeneralSettingsSection {...sectionProps} />;
			case "settingsOverlay":
				return <OverlaySettingsSection {...sectionProps} />;
			case "settingsHotkeys":
				return <RecordingSettingsSection {...sectionProps} />;
			case "settingsTranscription":
				return <PostProcessingSettingsSection {...sectionProps} />;
			case "settingsAI":
				return (
					<>
						<LlmPolishingSettingsSection {...sectionProps} />
						<AiEnhancementSettingsSection {...sectionProps} />
					</>
				);
			case "settingsAudio":
				return <AudioSettingsSection {...sectionProps} />;
			case "settingsAppearance":
				return (
					<>
						<ThemeSettingsSection
							{...sectionProps}
							themeModeProp={themeModeProp}
							onThemeChange={handleThemeChangeLocal}
						/>
						{/* Linux-only (returns null elsewhere): the frameless
                                                    title bar's window-button layout — follow the desktop's
                                                    button-layout or pick a custom side/visibility. */}
						<LinuxWindowButtonsSettingsSection {...sectionProps} />
					</>
				);
			case "settingsPrivacy":
				return (
					<PrivacySettingsSection
						{...sectionProps}
						consentFocusField={focusedConsentField}
					/>
				);
			case "settingsAdvanced":
				return (
					<>
						<TroubleshootingSettingsSection
							isVisible={_filter_settings}
							updateConfig={updateConfig}
							onNavigate={navigate}
							onResetClick={() => setShowResetDialog(true)}
							onOpenHelp={() => setHelpOpen(true)}
						/>
						<DiagnosticsSettingsSection isVisible={_filter_settings} />
						<ResourcesSettingsSection isVisible={_filter_settings} />
						<PrewarmAndUpdates isVisible={_filter_settings} />
					</>
				);
		}
	};

	if (!config) {
		// Initial-load failure: render the load-failure EmptyState
		// (variant="error" + Retry) instead of an endless "Loading…"
		// spinner. Mirrors the History/Models load-failure pattern.
		if (loadError) {
			return (
				<div className="flex h-full items-center justify-center">
					<EmptyState
						variant="error"
						icon={AlertCircleIcon}
						title={t("settings.loadFailedTitle")}
						description={t("settings.loadFailedDescription")}
						actionLabel={t("settings.retry")}
						onAction={() => {
							void loadConfig();
						}}
					/>
				</div>
			);
		}
		return <SettingsPageSkeleton />;
	}

	// The empty banner only applies on section pages when the query
	// matched NOTHING anywhere — if other section pages have matches, the
	// cross-section results section below replaces it (a bare "no
	// settings match" would be misleading copy when other pages do
	// match).
	const showEmptyBanner =
		activeSection !== null &&
		settingsFilter.trim() !== "" &&
		!hasAnyVisibleRow &&
		otherSectionGroups.length === 0;

	return (
		<div className="flex min-h-full flex-col">
			<div className="mx-auto w-full max-w-4xl flex-1 flex flex-col gap-8 px-16 pt-28 pb-6">
				{page === "settings" ? (
					<PageHeading
						title={t("settings.title")}
						description={t("settings.description")}
					/>
				) : (
					activeSection !== null && (
						<SectionBackButton onBack={() => navigate("settings")} />
					)
				)}
				{/* amber keyboard-permission banner — placed immediately under
                                    the page heading (hub) / back button (section pages) so the
                                    user sees the "click to fix" prompt before the settings
                                    content. Renders null when permission is granted / not
                                    needed, so the layout is unchanged on platforms where the
                                    banner doesn't apply (Windows). */}
				<KeyboardPermissionBanner />

				{/* Save-failure banner — the REAL save-status surface.
                                    useSettingsConfig auto-saves silently (no success
                                    indicator, by design), but a failed write must not be
                                    invisible: the hook's per-flush `error` carries the
                                    backend's specific validator text and is shown here
                                    until the next successful save clears it. aria-live so
                                    screen readers announce the failure. data-testid pins
                                    the contract for tests. */}
				{saveError && (
					<div
						role="status"
						aria-live="polite"
						data-testid="settings-save-error"
						className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive"
					>
						{saveError}
					</div>
				)}

				{/* Empty-state banner (section pages only — the hub renders
                                    its own inside SettingsHub) via the shared EmptyState
                                    component (variant="info") so the visual treatment
                                    matches Dashboard / Models / Vocabulary. Reuses the
                                    existing searchNoMatch / noResultsMessage /
                                    a11y.clearSearch i18n keys — `searchNoMatch` preserves
                                    the "{query}" interpolation so screen readers +
                                    sighted users see what they searched for;
                                    `noResultsMessage` adds the actionable hint; the action
                                    button gives a one-click escape hatch. The EmptyState
                                    wraps its title in an <h3> so SR users can navigate
                                    empty-state cards by heading. */}
				{showEmptyBanner && (
					<EmptyState
						variant="info"
						icon={Search01Icon}
						title={t("settings.searchNoMatch", {
							query: settingsFilter.trim(),
						})}
						description={t("settings.noResultsMessage")}
						actionLabel={t("a11y.clearSearch")}
						onAction={clearQuery}
					/>
				)}

				{/* The active surface's content. HUB: the single card of
                                    section rows. SECTION PAGE: the cross-section search
                                    results card (when a query matches elsewhere) followed
                                    by the page's own section cards. */}
				{page === "settings" ? (
					<SettingsHub
						config={config}
						onNavigateSection={(sectionPage) => navigate(sectionPage)}
					/>
				) : (
					activeSection !== null && (
						<>
							{otherSectionGroups.length > 0 && (
								<section
									aria-label={t("settings.otherTabsResults")}
									data-testid="settings-other-tabs-results"
									className="rounded-lg border border-border/10 bg-(--bg-subtle) px-3.5 py-3"
								>
									<h2 className="text-sm font-medium text-(--text-primary)">
										{t("settings.otherTabsResults")}
									</h2>
									<div className="flex flex-col gap-2">
										{otherSectionGroups.map((group) => (
											<div
												key={group.sectionPage}
												className="flex flex-col gap-1"
											>
												<p className="text-xs font-medium text-(--text-muted)">
													{t(SECTION_TITLE_BY_PAGE[group.sectionPage])}
												</p>
												<div className="flex flex-wrap gap-2">
													{group.labels.map((label) => (
														<button
															key={`${group.sectionPage}-${label}`}
															type="button"
															className="rounded-md border border-border/10 bg-(--bg) px-2 py-1 text-xs text-(--text-primary) transition-colors hover:bg-foreground/5 focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
															onClick={() =>
																navigate(group.sectionPage, {
																	settingsScrollTarget: { rowHint: label },
																})
															}
														>
															{label}
														</button>
													))}
												</div>
											</div>
										))}
									</div>
								</section>
							)}
							{renderSectionCards(activeSection)}
						</>
					)
				)}
			</div>

			{/* Reset-confirm + page-level help overlay serve the Advanced
                            page's Troubleshooting section only — gated so the hub and
                            other section pages don't mount them. The hooks backing them
                            (showResetDialog / helpOpen) stay at the top of the component
                            per the Rules of Hooks. */}
			{page === "settingsAdvanced" && (
				<>
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

					{/* Page-level help overlay for the Troubleshooting section's
                                            "Keyboard Shortcuts" button. Same shared component App.tsx
                                            mounts for the `?` shortcut; labels come from the user's
                                            config via the shared configHotkeyLabels helper so both
                                            instances can never drift. */}
					<HelpOverlay
						open={helpOpen}
						onClose={() => setHelpOpen(false)}
						dictationLabel={helpLabels.dictationLabel}
						repasteLabel={helpLabels.repasteLabel}
					/>
				</>
			)}
		</div>
	);
}
