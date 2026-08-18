import { Search01Icon } from "@hugeicons/core-free-icons";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { SearchField } from "@/components/common/SearchField";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
// amber banner shown when the OS has not granted the
// keyboard-monitoring (Accessibility / input-group) permission. Mirrors
// the MicrophonePermissionBanner placement on the Microphone page.
import { KeyboardPermissionBanner } from "@/components/KeyboardPermissionBanner";
import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
import { DiagnosticsSettingsSection } from "@/components/settings/DiagnosticsSettingsSection";
import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import { ModelSettingsSection } from "@/components/settings/ModelSettingsSection";
import PrewarmAndUpdates, {
	getPrewarmAndUpdatesLabels,
} from "@/components/settings/PrewarmAndUpdates";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { ResourcesSettingsSection } from "@/components/settings/ResourcesSettingsSection";
import {
	getTabLabels,
	type SettingsTab,
} from "@/components/settings/settingsTabLabels";
import { ThemeSettingsSection } from "@/components/settings/ThemeSettingsSection";
import { TroubleshootingSettingsSection } from "@/components/settings/TroubleshootingSettingsSection";
import { useSettingsConfig } from "@/components/settings/useSettingsConfig";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { useTheme } from "@/hooks/useTheme";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

//1-C Finding 11: keys excluded from reset-to-defaults because they
// encode one-time state (schema version, onboarding flag, OS-specific
// warning dismissal) that must survive a factory reset of user-tunable
// preferences. Hoisted to module scope so `resetToDefaults` can be a
// stable useCallback without re-allocating the list each render.
const CONFIG_PROTECTED_KEYS = [
	"schema_version",
	"wayland_warned",
	"onboarding_completed",
] as const;

// Map a Settings sub-page literal (the new sidebar nav target) to the
// SettingsTab union (the section-renderer's internal discriminator).
// The mapping is the source of truth for "which sub-page shows which
// sections" — derived from the old `activeTab === "..."` switch that
// used to live inline in the JSX.
//
// The `settings` parent literal is intentionally NOT in the map —
// `useNavigation.navigate("settings")` redirects it to
// `settingsGeneral` before this component ever sees it (see
// useNavigation.ts navigate action). If somehow `settings` reaches
// here anyway (e.g. a stale persisted nav state from an older build),
// the fallback in `pageToTab` resolves to `general` so the page
// renders instead of crashing.
const PAGE_TO_TAB: Partial<Record<Page, SettingsTab>> = {
	settingsGeneral: "general",
	settingsAiAudio: "aiAudio",
	settingsAppearance: "appearance",
	settingsPrivacy: "privacy",
};

function pageToTab(page: Page): SettingsTab {
	return PAGE_TO_TAB[page] ?? "general";
}

// Reverse mapping for the search auto-switch: when the search finds
// a match in another Settings section, it needs to navigate to the
// right sub-page. Returns the Page literal the navigate action wants.
function tabToPage(tab: SettingsTab): Page {
	switch (tab) {
		case "general":
			return "settingsGeneral";
		case "aiAudio":
			return "settingsAiAudio";
		case "appearance":
			return "settingsAppearance";
		case "privacy":
			return "settingsPrivacy";
	}
}

interface SettingsPageProps {
	// The active Settings sub-page literal. The Settings page no
	// longer owns tab state locally — the navigation store does (so
	// the sidebar's nested Settings submenu stays in sync with the
	// page content). Defaults to "settingsGeneral" when not provided
	// (legacy callers that still pass `<SettingsPage />` without a
	// prop fall through to General, matching the previous default-tab
	// behavior).
	page?: Page;
}

//NOTE: App.tsx prop passing will be removed by
//(BACKLOG-004): SettingsPage now obtains `navigate` via
// useNavigation and theme state via useTheme directly, eliminating the
// `onNavigate` / `themeMode` / `onThemeChange` prop drills from App.tsx.
//
//The 4-tab SegmentedControl that used to live at the top of the
// Settings page has been removed — the tab navigation now lives in
// the application Sidebar as a nested submenu (see Sidebar.tsx
// NavSubmenu + ADR-0021). This page renders only the active tab's
// sections + the SearchField (which stays in the sticky header so
// the user can search across ALL Settings content, not just the
// current sub-page). The search auto-switch now navigates to the
// best-matching Settings sub-page (via `navigate(tabToPage(bestTab),
// { settingsScrollTarget: { rowHint } })`) instead of locally
// switching `setActiveTab(bestTab)` — the transient
// `pendingSettingsScrollTarget` field is consumed by this component
// on mount + tab change to scroll to + briefly highlight the matched
// row (mirrors the proven consent-deep-link pattern).
export default function SettingsPage({
	page = "settingsGeneral",
}: SettingsPageProps) {
	const {
		config,
		updateConfig,
		updateConfigDebounced,
		loadConfig,
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
	const {
		navigate,
		pendingConsentField,
		consumeConsentField,
		pendingSettingsScrollTarget,
		consumeSettingsScrollTarget,
	} = useNavigation();
	const { showSnack } = useSnackbar();
	const [showResetDialog, setShowResetDialog] = useState(false);
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const [settingsFilter, setSettingsFilter] = useState("");
	// The active tab is DERIVED from the current Settings sub-page
	// (passed in as `page` prop by App.tsx's renderPage switch). No
	// more local tab state + localStorage persistence — the nav
	// store is the single source of truth. The scroll-positions ref
	// survives across sub-page transitions inside the Settings
	// surface (all 4 sub-pages share this component instance via the
	// `page` prop swap), so the per-tab scroll-restore behavior is
	// preserved.
	const activeTab = pageToTab(page);
	const scrollPositionsRef = useRef<Record<SettingsTab, number>>({
		appearance: 0,
		general: 0,
		aiAudio: 0,
		privacy: 0,
	});
	const prevTabRef = useRef(activeTab);

	// Consent deep-link (``client.consent_required`` path). A consent
	// refusal elsewhere (mic test / level monitor / dictation gate)
	// navigates here with ``{ consentField }`` (see NavigateOptions in
	// useNavigation.ts); the field is staged in the nav store as
	// ``pendingConsentField`` and consumed ONCE here. Cleared
	// highlight state so the ring only shows for the toggle the
	// refusal actually named. NOTE: consent deep-links always target
	// the Privacy sub-page — the navigate call now goes through
	// `navigate("settingsPrivacy", { consentField })` (the nav store
	// handles routing + the `pendingConsentField` arming), but this
	// component still consumes the field once the Privacy sub-page
	// mounts.
	const [focusedConsentField, setFocusedConsentField] = useState<string | null>(
		null,
	);
	// One-shot scroll guard + ring-lifetime timer for the consent
	// deep-link highlight (see the scroll effect below). Also reused
	// for the cross-page Settings search deep-link highlight — both
	// share the same ring-lifetime mechanism since only one deep-link
	// target can be active at a time.
	const scrolledTargetRef = useRef<string | null>(null);
	const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	// Cross-page search deep-link target hint (consumed on sub-page
	// mount + on tab change). The Settings search may fire this from
	// any Settings sub-page — the source page sets it via
	// `navigate(tabToPage(bestTab), { settingsScrollTarget: { rowHint } })`,
	// the destination sub-page consumes it here.
	const [searchScrollHint, setSearchScrollHint] = useState<string | null>(null);

	// Consume the pending consent deep-link target: clear any active
	// search filter (so the consent row is visible) and arm the
	// highlight state. The Privacy tab is now routed via the `page`
	// prop (the nav store sent the user to "settingsPrivacy"), so we
	// don't need to call setActiveTab("privacy") here anymore — just
	// arm the highlight.
	useEffect(() => {
		if (!pendingConsentField) return;
		const field = consumeConsentField();
		if (!field) return;
		setSettingsFilter("");
		scrollPositionsRef.current.privacy = 0;
		setFocusedConsentField(field);
	}, [pendingConsentField, consumeConsentField]);

	// Consume the pending cross-page Settings search deep-link target.
	// Mirrors the consent-deep-link consumption: clear the search filter
	// (so the matched row is visible) + arm the highlight state with the
	// rowHint (the matched label string) so the scroll effect can find
	// + ring the matching element by visible-text content.
	useEffect(() => {
		if (!pendingSettingsScrollTarget) return;
		const target = consumeSettingsScrollTarget();
		if (!target) return;
		setSettingsFilter("");
		const hint = target.rowHint;
		if (hint) setSearchScrollHint(hint);
	}, [pendingSettingsScrollTarget, consumeSettingsScrollTarget]);

	// Scroll the deep-linked consent row into view once it's rendered
	// (Privacy sub-page active + config loaded). The row is rendered by
	// PrivacySettingsSection with a ``data-consent-field`` attribute;
	// retry until found (bounded) in case the lazy page / config fetch
	// is still settling. The scroll is ONE-SHOT per deep-link target
	// (``scrolledTargetRef``) so a config identity change — e.g. the
	// user toggling the just-highlighted consent — doesn't re-trigger a
	// smooth re-center. The highlight ring's lifetime starts when the
	// row is actually found, so a slow ``get_config`` can't clear the
	// ring before the row renders.
	useEffect(() => {
		if (!focusedConsentField || !config || activeTab !== "privacy") return;
		if (scrolledTargetRef.current === focusedConsentField) return;
		let attempts = 0;
		let cancelled = false;
		const tryScroll = () => {
			if (cancelled) return;
			// Match by attribute VALUE rather than interpolating the
			// field into a selector — the field comes from the backend
			// envelope, and value-filtering avoids any selector
			// injection edge.
			const el = Array.from(
				document.querySelectorAll<HTMLElement>("[data-consent-field]"),
			).find(
				(node) =>
					node.getAttribute("data-consent-field") === focusedConsentField,
			);
			if (el) {
				scrolledTargetRef.current = focusedConsentField;
				el.scrollIntoView?.({ behavior: "smooth", block: "center" });
				// Ring lifetime starts now (row actually visible).
				if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
				highlightTimerRef.current = setTimeout(() => {
					setFocusedConsentField(null);
					scrolledTargetRef.current = null;
				}, 2600);
				return;
			}
			// Bounded retry (~3s) — a stale target can't spin forever.
			if (attempts < 60) {
				attempts += 1;
				setTimeout(tryScroll, 50);
			}
		};
		const timer = setTimeout(tryScroll, 0);
		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	}, [focusedConsentField, config, activeTab]);

	// Cross-page Settings search deep-link scroll + highlight. Mirrors
	// the consent-deep-link scroll logic but matches by VISIBLE TEXT
	// (the rowHint string) rather than by attribute value — the search
	// deep-link carries the matched label text (translated at the
	// moment the user typed), so we walk rendered SettingRow elements
	// and pick the first whose label text contains the hint. The match
	// is intentionally substring + case-insensitive so a partial hint
	// (e.g. trailing whitespace) still resolves.
	useEffect(() => {
		if (!searchScrollHint || !config) return;
		if (scrolledTargetRef.current === searchScrollHint) return;
		let attempts = 0;
		let cancelled = false;
		const tryScroll = () => {
			if (cancelled) return;
			const hint = searchScrollHint.toLowerCase();
			// SettingRow renders the row label inside a <span> with class
			// `text-(--text-primary)`. We walk all rows on the page and
			// pick the first whose label text contains the hint.
			const candidates = Array.from(
				document.querySelectorAll<HTMLElement>("[data-settings-row-label]"),
			);
			const el = candidates.find((node) =>
				(node.textContent ?? "").toLowerCase().includes(hint),
			);
			if (el) {
				scrolledTargetRef.current = searchScrollHint;
				el.scrollIntoView?.({ behavior: "smooth", block: "center" });
				el.classList.add(
					"ring-2",
					"ring-ring",
					"ring-offset-2",
					"ring-offset-background",
				);
				if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
				highlightTimerRef.current = setTimeout(() => {
					setSearchScrollHint(null);
					scrolledTargetRef.current = null;
					el.classList.remove(
						"ring-2",
						"ring-ring",
						"ring-offset-2",
						"ring-offset-background",
					);
				}, 2600);
				return;
			}
			if (attempts < 60) {
				attempts += 1;
				setTimeout(tryScroll, 50);
			}
		};
		const timer = setTimeout(tryScroll, 0);
		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	}, [searchScrollHint, config]);

	// Max-lifetime safety net: even if the target row never renders
	// (e.g. an unknown ``consent_field`` or a stale search hint), the
	// highlight can't linger indefinitely.
	useEffect(() => {
		if (!focusedConsentField && !searchScrollHint) return;
		const timer = setTimeout(() => {
			setFocusedConsentField(null);
			setSearchScrollHint(null);
			scrolledTargetRef.current = null;
		}, 5000);
		return () => clearTimeout(timer);
	}, [focusedConsentField, searchScrollHint]);

	// label-based search auto-switch. Score each tab by counting
	// label matches and switch to the highest-scoring one. Requires
	// q.length >= 2 to avoid jarring switches as the user types.
	//
	// Supplement the privacy tab labels with PrewarmAndUpdates
	// row labels (e.g. "Prewarm cache status", "Installed version",
	// "Latest release") so the auto-switch routes queries like "prewarm",
	// "cache", "version", "update" to the privacy tab where the
	// PrewarmAndUpdates component lives. The labels are translated at the
	// moment the user types via getPrewarmAndUpdatesLabels().
	//
	// When the best-matching tab is DIFFERENT from the current sub-page,
	// navigate to the corresponding sub-page + carry the matched label
	// as a settingsScrollTarget rowHint so the destination can scroll to
	// + highlight the matched row. When the best-matching tab IS the
	// current sub-page, no navigation is needed — the local filter
	// predicate (`_filter_settings`) handles the in-page filtering.
	const handleSearchChange = useCallback(
		(value: string) => {
			setSettingsFilter(value);
			const q = value.toLowerCase().trim();
			if (!q || q.length < 2) return;
			let bestTab: SettingsTab | null = null;
			let bestScore = 0;
			let bestLabel = "";
			const tabLabels = getTabLabels();
			tabLabels.privacy = [
				...tabLabels.privacy,
				...getPrewarmAndUpdatesLabels(),
			];
			for (const [tab, labels] of Object.entries(tabLabels)) {
				for (const label of labels) {
					const matches =
						label.toLowerCase().includes(q) || q.includes(label.toLowerCase());
					if (matches) {
						const score = label.length; // prefer the longest (most specific) match
						if (score > bestScore) {
							bestScore = score;
							bestTab = tab as SettingsTab;
							bestLabel = label;
						}
					}
				}
			}
			if (bestTab && bestScore > 0 && bestTab !== activeTab) {
				// Cross-page navigation — carry the matched label as a
				// rowHint so the destination sub-page can scroll + ring.
				navigate(tabToPage(bestTab), {
					settingsScrollTarget: { rowHint: bestLabel },
				});
			}
		},
		[activeTab, navigate],
	);

	// Restore scroll position when the active tab changes (i.e. the
	// user navigated between Settings sub-pages via the sidebar).
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

	//reset-to-defaults wrapped in useCallback so ConfirmDialog's
	// `onConfirm` prop identity stays stable across renders. The
	// CONFIG_PROTECTED_KEYS blocklist (above) is hoisted to module scope so
	// it doesn't need to be a dep.
	const resetToDefaults = useCallback(async () => {
		if (!config) return;
		setShowResetDialog(false);
		try {
			const defaults = await call<Record<string, unknown>>("get_defaults");
			if (defaults && typeof defaults === "object") {
				const safeDefaults: Record<string, unknown> = {};
				for (const [key, value] of Object.entries(defaults)) {
					if (value === "<redacted>") continue;
					if ((CONFIG_PROTECTED_KEYS as readonly string[]).includes(key))
						continue;
					safeDefaults[key] = value;
				}
				await updateConfig(safeDefaults as Partial<VoiceTyperConfig>);
				showSnack(t("settings.resetToDefaultsToast"), "success");
			} else {
				showSnack(t("settings.fetchDefaultsFailed"), "error");
			}
		} catch (err) {
			console.error("[renderer:Settings] Failed to reset to defaults:", err);
			showSnack(t("settings.resetFailed"), "error");
		}
	}, [config, call, updateConfig, showSnack]);

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

	//empty-state sentinel — previously a render-phase mutation
	// anti-pattern (a ref bumped during children's render + a no-deps
	// useEffect that flipped `hasAnyVisibleRow` state). Now derived
	// purely from `settingsFilter` via useMemo: if no tab label (across
	// all four tabs + the PrewarmAndUpdates rows) matches the query, the
	// empty banner is shown. The label sets come from the same
	// `getTabLabels()` / `getPrewarmAndUpdatesLabels()` helpers used by
	// `handleSearchChange`, so the derivation stays consistent with the
	// auto-switch scoring.
	const hasAnyVisibleRow = useMemo(() => {
		if (!settingsFilter.trim()) return true;
		const q = settingsFilter.toLowerCase();
		const tabLabels = getTabLabels();
		const allLabels = [
			...Object.values(tabLabels).flat(),
			...getPrewarmAndUpdatesLabels(),
		];
		return allLabels.some((label) => label.toLowerCase().includes(q));
	}, [settingsFilter]);

	// Filter predicate — wrapped in useCallback with
	// [settingsFilter] deps so memoized section children don't re-render
	//unless the query actually changes. : this is now a PURE
	// predicate (no render-phase side effect) — `hasAnyVisibleRow` is
	// derived above via useMemo from the same label set. NOTE: declared
	// BEFORE the `if (!config)` early return so React's Rules of Hooks are
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

	// renderTabPanel wraps the section children in a `role="tabpanel"`
	// container. The `id` / `aria-labelledby` pair is kept for
	// backward compat with screen readers that expect the tablist
	// contract — even though the SegmentedControl tab bar is gone,
	// each Settings sub-page is still a "panel" that the sidebar
	// submenu item controls (aria-controls on the sidebar child
	// button is wired via the `id` here).
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
			{/* Sticky header: SearchField only (the 4-tab
                                 SegmentedControl has been removed — the tabs now live in
                                 the application Sidebar as a nested Settings submenu, see
                                 ADR-0021). The SearchField stays inside the Settings
                                 sticky header so it remains visible while scrolling and
                                 searches across ALL Settings content (not just the
                                 current sub-page). When the search finds a match in
                                 another sub-page, `handleSearchChange` navigates there
                                 via `navigate(tabToPage(bestTab), { settingsScrollTarget })`. */}
			<div className="sticky top-0 z-10 border-b border-border/10 bg-(--bg-subtle)/95 backdrop-blur supports-backdrop-filter:bg-(--bg-subtle)/80">
				<div className="mx-auto w-full max-w-4xl px-16 py-3">
					<div className="flex items-center gap-3">
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
			<div className="mx-auto w-full max-w-4xl flex-1 space-y-8 px-16 pt-6 pb-6">
				<PageHeading
					title={t("settings.title")}
					description={t("settings.description")}
				/>
				{/* amber keyboard-permission banner —
                                                placed immediately under PageHeading so the user sees the
                                                "click to fix" prompt before scrolling into the tab panels.
                                                Renders null when permission is granted / not needed, so the
                                                layout is unchanged on platforms where the banner doesn't
                                                apply (Windows). */}
				<KeyboardPermissionBanner />
				<div className="flex justify-end pb-2">
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={handleManualRefresh}
						refreshing={refreshing}
					/>
				</div>

				{/* Empty-state banner rendered via the shared
                                                EmptyState component (variant="info") so the visual
                                                treatment matches Dashboard / Models / Vocabulary.
                                                Reuses the existing searchNoMatch / noResultsMessage /
                                                a11y.clearSearch i18n keys — `searchNoMatch` preserves
                                                the "{query}" interpolation so screen readers + sighted
                                                users see what they searched for; `noResultsMessage`
                                                adds the actionable hint; the action button gives a
                                                one-click escape hatch. The EmptyState wraps its title
                                                in an <h3> so SR users can navigate empty-state cards
                                                by heading. */}
				{showEmptyBanner && (
					<EmptyState
						variant="info"
						icon={Search01Icon}
						title={t("settings.searchNoMatch", {
							query: settingsFilter.trim(),
						})}
						description={t("settings.noResultsMessage")}
						actionLabel={t("a11y.clearSearch")}
						onAction={() => setSettingsFilter("")}
					/>
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
							<PrivacySettingsSection
								{...sectionProps}
								consentFocusField={focusedConsentField}
							/>
							<TroubleshootingSettingsSection
								isVisible={_filter_settings}
								updateConfig={updateConfig}
								onNavigate={navigate}
								onResetClick={() => setShowResetDialog(true)}
							/>
							<DiagnosticsSettingsSection isVisible={_filter_settings} />
							<ResourcesSettingsSection isVisible={_filter_settings} />
							<PrewarmAndUpdates isVisible={_filter_settings} />
						</>,
					)}
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
