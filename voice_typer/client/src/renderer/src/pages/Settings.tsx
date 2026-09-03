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
import PrewarmAndUpdates, {
	getPrewarmAndUpdatesLabels,
} from "@/components/settings/PrewarmAndUpdates";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { ResourcesSettingsSection } from "@/components/settings/ResourcesSettingsSection";
import { SettingsHub } from "@/components/settings/SettingsHub";
import {
	isSettingsSectionPage,
	SECTION_TITLE_BY_PAGE,
	type SettingsSectionPage,
} from "@/components/settings/settingsSections";
import { getSectionLabels } from "@/components/settings/settingsTabLabels";
import { ThemeSettingsSection } from "@/components/settings/ThemeSettingsSection";
import { TroubleshootingSettingsSection } from "@/components/settings/TroubleshootingSettingsSection";
import { useSettingsConfig } from "@/components/settings/useSettingsConfig";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { useTheme } from "@/hooks/useTheme";
import { t } from "@/i18n/i18n";
import { userFacingErrorMessage } from "@/lib/errors/userFacingErrorMessage";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

// keys excluded from reset-to-defaults because they
// encode one-time state (schema version, onboarding flag, OS-specific
// warning dismissal) that must survive a factory reset of user-tunable
// preferences. Hoisted to module scope so `resetToDefaults` can be a
// stable useCallback without re-allocating the list each render.
const CONFIG_PROTECTED_KEYS = [
	"schema_version",
	"wayland_warned",
	"onboarding_completed",
] as const;

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
	const {
		navigate,
		pendingConsentField,
		consumeConsentField,
		pendingSettingsScrollTarget,
		consumeSettingsScrollTarget,
	} = useNavigation();
	const { showSnack } = useSnackbar();
	const [showResetDialog, setShowResetDialog] = useState(false);
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
	const scrollPositionsRef = useRef<Record<string, number>>({});
	const prevSurfaceRef = useRef<Page>(page);

	// Consent deep-link (``client.consent_required`` path). A consent
	// refusal elsewhere (mic test / level monitor / dictation gate)
	// navigates here with ``{ consentField }`` (see NavigateOptions in
	// useNavigation.ts); the field is staged in the nav store as
	// ``pendingConsentField`` and consumed ONCE on the Privacy section
	// page — the only surface that renders the consent toggles.
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
	// Cross-page search deep-link target hint (consumed on section-page
	// mount + on page change). The Settings search may fire this from
	// any section page — the source page sets it via
	// `navigate(bestPage, { settingsScrollTarget: { rowHint } })`,
	// the destination page consumes it here.
	const [searchScrollHint, setSearchScrollHint] = useState<string | null>(null);

	// Consume the pending consent deep-link target: clear any active
	// search filter (so the consent row is visible) and arm the
	// highlight state. The nav store routed the user to
	// "settingsPrivacy"; we just arm the highlight here.
	useEffect(() => {
		if (!pendingConsentField) return;
		const field = consumeConsentField();
		if (!field) return;
		clearQuery();
		scrollPositionsRef.current.settingsPrivacy = 0;
		setFocusedConsentField(field);
	}, [pendingConsentField, consumeConsentField, clearQuery]);

	// Consume the pending cross-page Settings search deep-link target.
	// Mirrors the consent-deep-link consumption: clear the search filter
	// (so the matched row is visible) + arm the highlight state with the
	// rowHint (the matched label string) so the scroll effect can find
	// + ring the matching element by visible-text content.
	useEffect(() => {
		if (!pendingSettingsScrollTarget) return;
		const target = consumeSettingsScrollTarget();
		if (!target) return;
		clearQuery();
		const hint = target.rowHint;
		if (hint) setSearchScrollHint(hint);
	}, [pendingSettingsScrollTarget, consumeSettingsScrollTarget, clearQuery]);

	// Scroll the deep-linked consent row into view once it's rendered
	// (Privacy section page active + config loaded). The row is rendered
	// by PrivacySettingsSection with a ``data-consent-field`` attribute;
	// retry until found (bounded) in case the lazy page / config fetch
	// is still settling. The scroll is ONE-SHOT per deep-link target
	// (``scrolledTargetRef``) so a config identity change — e.g. the
	// user toggling the just-highlighted consent — doesn't re-trigger a
	// smooth re-center. The highlight ring's lifetime starts when the
	// row is actually found, so a slow ``get_config`` can't clear the
	// ring before the row renders.
	useEffect(() => {
		if (!focusedConsentField || !config || page !== "settingsPrivacy") return;
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
	}, [focusedConsentField, config, page]);

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

	// label-based search auto-switch (SECTION PAGES ONLY — on the hub a
	// query filters the section rows instead of yanking the user to a
	// section page mid-typing). Score each section page by counting
	// label matches and navigate to the highest-scoring one. Requires
	// q.length >= 2 to avoid jarring switches as the user types.
	//
	// The Advanced page's label set is supplemented with PrewarmAndUpdates
	// row labels (e.g. "Prewarm cache status", "Installed version",
	// "Latest release") so queries like "prewarm", "cache", "version",
	// "update" route to the page where the PrewarmAndUpdates component
	// lives. The labels are translated at the moment the user types via
	// getPrewarmAndUpdatesLabels().
	//
	// When the best-matching page is DIFFERENT from the current section
	// page, navigate + carry the matched label as a settingsScrollTarget
	// rowHint so the destination can scroll to + highlight the matched
	// row. When it IS the current page, no navigation is needed — the
	// local filter predicate (`_filter_settings`) handles in-page
	// filtering.
	//
	// The very first render is skipped so a stale query left in the
	// store by a previous visit doesn't yank the user to another page
	// on mount.
	const searchNavFirstRenderRef = useRef(true);
	useEffect(() => {
		if (searchNavFirstRenderRef.current) {
			searchNavFirstRenderRef.current = false;
			return;
		}
		if (!activeSection) return;
		const q = settingsFilter.toLowerCase().trim();
		if (!q || q.length < 2) return;
		let bestPage: SettingsSectionPage | null = null;
		let bestScore = 0;
		let bestLabel = "";
		const sectionLabels = getSectionLabels();
		sectionLabels.settingsAdvanced = [
			...sectionLabels.settingsAdvanced,
			...getPrewarmAndUpdatesLabels(),
		];
		for (const [sectionPage, labels] of Object.entries(sectionLabels)) {
			for (const label of labels) {
				const matches =
					label.toLowerCase().includes(q) || q.includes(label.toLowerCase());
				if (matches) {
					const score = label.length; // prefer the longest (most specific) match
					if (score > bestScore) {
						bestScore = score;
						bestPage = sectionPage as SettingsSectionPage;
						bestLabel = label;
					}
				}
			}
		}
		if (bestPage && bestScore > 0 && bestPage !== activeSection) {
			// Cross-page navigation — carry the matched label as a
			// rowHint so the destination page can scroll + ring.
			navigate(bestPage, {
				settingsScrollTarget: { rowHint: bestLabel },
			});
		}
	}, [settingsFilter, activeSection, navigate]);

	// Restore scroll position when the active surface changes (hub ↔
	// section page ↔ another section page).
	useEffect(() => {
		if (prevSurfaceRef.current !== page) {
			prevSurfaceRef.current = page;
			const saved = scrollPositionsRef.current[page] ?? 0;
			if (saved > 0) {
				requestAnimationFrame(() => {
					const mainEl = document.getElementById("main-content");
					if (mainEl) mainEl.scrollTop = saved;
				});
			}
		}
	}, [page]);

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
			// Known failure classes (timeout / backend unreachable) get
			// their curated localized message; unknown ones keep the
			// contextual fallback.
			showSnack(
				userFacingErrorMessage(err, t, t("settings.resetFailed")),
				"error",
			);
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

	//empty-state sentinel — derived purely from `settingsFilter` via
	// useMemo: if no section label (across all section pages + the
	// PrewarmAndUpdates rows) matches the query, the empty banner is
	// shown on section pages. The label sets come from the same
	// `getSectionLabels()` / `getPrewarmAndUpdatesLabels()` helpers used
	// by the auto-switch effect, so the derivation stays consistent with
	// the auto-switch scoring. (The hub renders its own empty state
	// inside SettingsHub.)
	const hasAnyVisibleRow = useMemo(() => {
		if (!settingsFilter.trim()) return true;
		const q = settingsFilter.toLowerCase();
		const sectionLabels = getSectionLabels();
		const allLabels = [
			...Object.values(sectionLabels).flat(),
			...getPrewarmAndUpdatesLabels(),
		];
		return allLabels.some((label) => label.toLowerCase().includes(q));
	}, [settingsFilter]);

	// "Results from other section pages" (search grouping): matches the
	// SAME label sets as the empty-state + auto-switch derivations above,
	// but keeps every match from every OTHER section page (the active
	// page's own matches are filtered inline by the sections). Each
	// entry navigates to its page with a rowHint so the destination
	// scrolls to + rings the matched row (the proven search deep-link
	// path). Only rendered on section pages — the hub's rows already
	// list their matched labels inline.
	const otherSectionGroups = useMemo(() => {
		if (!activeSection || !settingsFilter.trim()) return [];
		const q = settingsFilter.toLowerCase();
		const sectionLabels = getSectionLabels();
		sectionLabels.settingsAdvanced = [
			...sectionLabels.settingsAdvanced,
			...getPrewarmAndUpdatesLabels(),
		];
		return Object.entries(sectionLabels)
			.filter(([sectionPage]) => sectionPage !== activeSection)
			.map(([sectionPage, labels]) => ({
				sectionPage: sectionPage as SettingsSectionPage,
				// Different section titles can render the same translated
				// word — dedupe so a match produces ONE chip per unique
				// label (and unique React keys).
				labels: [...new Set(labels)].filter((label) =>
					label.toLowerCase().includes(q),
				),
			}))
			.filter((g) => g.labels.length > 0);
	}, [settingsFilter, activeSection]);

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
