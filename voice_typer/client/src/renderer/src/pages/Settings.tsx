import {
	Book02Icon,
	Bug02Icon,
	File02Icon,
	InformationCircleIcon,
	RefreshIcon,
	Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import { SearchField } from "@/components/common/SearchField";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Spinner } from "@/components/feedback/Spinner";
import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import { ModelSettingsSection } from "@/components/settings/ModelSettingsSection";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import { ThemeSettingsSection } from "@/components/settings/ThemeSettingsSection";
import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { usePython, usePythonEvent } from "@/hooks/usePython";
// NEW-TS-004: use the shared useSnackbar hook instead of re-implementing
// the useState + setTimeout + JSX pattern inline.  Previously this page
// had its own ``showSnack`` function with a setTimeout that wasn't
// cleared on unmount (a leak risk if the page unmounted mid-toast).
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

// Module-level cache — persists across page navigations so settings render
// instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null;

/** Settings tab identifiers used by the SegmentedControl and search-to-tab
 *  auto-switch feature.  Defined at module level so SEARCH_TAB_HINTS can
 *  reference it. */
type SettingsTab = "appearance" | "general" | "aiAudio" | "privacy";

/** Keyword-to-tab mapping for the search auto-switch feature.
 *  Each tab has lowercase keywords that uniquely identify it.
 *  Module-level constant — never recreated on re-render. */
const SEARCH_TAB_HINTS: Record<SettingsTab, string[]> = {
	appearance: [
		"theme",
		"appearance",
		"color",
		"dark",
		"light",
		"text size",
		"font",
	],
	general: [
		"language",
		"notification",
		"tray",
		"bubble",
		"overlay",
		"startup",
		"login",
		"launch",
		"hotkey",
		"shortcut",
		"dictation key",
		"recording mode",
		"push to talk",
	],
	aiAudio: [
		"model",
		"llm",
		"audio",
		"volume",
		"ducking",
		"noise",
		"filter",
		"post-processing",
		"ai enhancement",
		"vocabulary",
		"api key",
		"polish",
		"transcription",
		"preset",
		"snippet",
	],
	privacy: [
		"privacy",
		"recovery",
		"crash",
		"export",
		"consent",
		"troubleshooting",
		"diagnostics",
		"reset",
		"log",
		"bug",
		"help",
		"faq",
	],
};

interface SettingsPageProps {
	themeMode?: VoiceTyperConfig["theme_mode"];
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
	// NEW-UX-025: navigation callback so the Troubleshooting section can
	// route the user to the About page (which has full diagnostics).
	// NEW-TS-ERR-R2-001: typed as `Page` (not `string`).
	onNavigate?: (page: Page) => void;
}

export default function SettingsPage({
	themeMode: themeModeProp,
	onThemeChange,
	onNavigate,
}: SettingsPageProps) {
	const { call } = usePython();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [saving, setSaving] = useState(false);
	// Task 17-B-FIX-2: `saved` tracks the inline "Saved ✓" success
	// indicator that appears for 2 seconds after a successful
	// `set_config` roundtrip.  It replaces the previous invisible
	// `text-[10px] text-(--text-muted)/40` "Auto-save" label, which
	// violated WCAG 2.1 SC 1.4.4 (minimum 12px) and SC 1.4.3 (the /40
	// opacity gave ~1.5:1 contrast — well below the 4.5:1 minimum) and
	// had no success state at all.  The indicator is now `text-xs`
	// (12px), full-opacity, and announced to screen readers via the
	// surrounding `aria-live="polite"` region.
	const [saved, setSaved] = useState(false);
	const savedTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const [showResetDialog, setShowResetDialog] = useState(false);
	// UX-028: search/filter state for settings
	const [settingsFilter, setSettingsFilter] = useState("");

	// SEARCH-SWITCH: when the user types a search query, check if it
	// matches a hint keyword for a tab other than the current one. If so,
	// auto-switch to that tab so the matching results are visible.
	const handleSearchChange = useCallback((value: string) => {
		setSettingsFilter(value);

		const q = value.toLowerCase().trim();
		if (!q || q.length < 2) return; // Too short — would match hints across
		// multiple tabs and cause jarring auto-switching as the user types.

		// Score each tab by counting how many hint keywords match.
		let bestTab: SettingsTab | null = null;
		let bestScore = 0;

		for (const [tab, hints] of Object.entries(SEARCH_TAB_HINTS)) {
			const score = hints.filter(
				(hint) => hint.includes(q) || q.includes(hint),
			).length;
			if (score > bestScore) {
				bestScore = score;
				bestTab = tab as SettingsTab;
			}
		}

		// Only switch if we found a clear winner and it's not already active.
		if (bestTab && bestScore > 0) {
			setActiveTab(bestTab);
		}
	}, []);
	// NEW: settings tab navigation — groups related sections into tabs.
	// Persisted in localStorage so the active tab survives page navigation.
	const LS_KEY = "voice-typer-settings-tab";
	const getSavedTab = (): SettingsTab => {
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
		} catch {
			// localStorage may be unavailable (SSR, sandboxed)
		}
		return "general";
	};
	const [activeTab, setActiveTab] = useState<SettingsTab>(getSavedTab);

	// NEW-UX-SCROLL: preserve scroll position when switching tabs so the
	// user doesn't start from the top every time they switch sections.
	// Tracks scrollTop per tab and restores it after the new tab renders.
	const scrollPositionsRef = useRef<Record<SettingsTab, number>>({
		appearance: 0,
		general: 0,
		aiAudio: 0,
		privacy: 0,
	});
	const prevTabRef = useRef(activeTab);

	const handleTabChange = useCallback(
		(tab: SettingsTab) => {
			// Save current scroll position before switching
			const mainEl = document.getElementById("main-content");
			if (mainEl) {
				scrollPositionsRef.current[activeTab] = mainEl.scrollTop;
			}
			setActiveTab(tab);
		},
		[activeTab],
	);

	// Restore scroll position after the new tab's content has rendered.
	// Uses requestAnimationFrame to wait for the DOM to settle after
	// the conditional sections mount/unmount.
	useEffect(() => {
		if (prevTabRef.current !== activeTab) {
			prevTabRef.current = activeTab;
			const saved = scrollPositionsRef.current[activeTab];
			if (saved > 0) {
				requestAnimationFrame(() => {
					const mainEl = document.getElementById("main-content");
					if (mainEl) {
						mainEl.scrollTop = saved;
					}
				});
			}
		}
	}, [activeTab]);

	// Persist tab changes to localStorage so the choice survives navigation.
	useEffect(() => {
		try {
			localStorage.setItem(LS_KEY, activeTab);
		} catch {
			// localStorage may be unavailable
		}
	}, [activeTab]);

	// NEW-TS-004: use the shared useSnackbar hook.  The hook manages the
	// timer ref and clears it on unmount, fixing the leak risk of the
	// previous inline setTimeout (which wasn't cleared if the page
	// unmounted mid-toast).
	const { showSnack, Snackbar } = useSnackbar();

	// PERF-002: batch config writes — single set_config call per
	// debounce window.
	//
	// The previous implementation called `call("set_config", updates)`
	// immediately inside `updateConfig`, so any code path that fired
	// multiple `updateConfig` calls in quick succession (rapid slider
	// drags, multiple toggles in one handler, debounced text inputs
	// firing close together) produced one IPC write per call.  The
	// backend's `set_config` already accepts a partial dict (see
	// IPC_CONFIG_ALLOWLIST), so we accumulate updates in
	// `pendingUpdatesRef` and flush them in a single `set_config` call
	// via a microtask.  A `lastSavedConfigRef` lets us diff the pending
	// updates against the last persisted snapshot so no-op writes
	// (e.g. a slider dragged back to its original value) are skipped
	// entirely.
	const lastSavedConfigRef = useRef<VoiceTyperConfig | null>(_cachedConfig);
	const pendingUpdatesRef = useRef<Partial<VoiceTyperConfig>>({});
	const flushScheduledRef = useRef(false);
	const flushPromiseResolversRef = useRef<Array<() => void>>([]);
	// Ref mirror of `flushPendingUpdates` so the unmount cleanup (which
	// has empty deps to avoid re-subscribing on every render) can call
	// the latest closure.  Updated in a dedicated effect below.
	const flushPendingUpdatesRef = useRef<() => Promise<void>>(async () => {});
	// PERF-MEMO-001: ref mirror of `config` so `updateConfig` and
	// `updateConfigDebounced` can read the latest config WITHOUT
	// depending on it in their useCallback deps.  Previously, both
	// callbacks had `config` in their deps, which meant they were
	// recreated on every config change — defeating the React.memo
	// wrappers on child sections (RecordingSettingsSection,
	// GeneralSettingsSection, etc.) and causing unnecessary
	// re-renders across the entire Settings page on every keystroke.
	// Now the callbacks have stable identity (empty deps) and read
	// the latest config from this ref.
	const configRef = useRef<VoiceTyperConfig | null>(_cachedConfig);
	useEffect(() => {
		configRef.current = config;
	}, [config]);

	const loadConfig = useCallback(async () => {
		try {
			const result = await call<VoiceTyperConfig>("get_config");
			_cachedConfig = result;
			// PERF-002: seed the diff baseline so the initial
			// snapshot doesn't get re-saved as a "change".
			lastSavedConfigRef.current = result;
			setConfig(result);
		} catch (err) {
			console.error("Failed to load config:", err);
		}
	}, [call]);

	// Skip initial fetch when module-level cache is populated —
	// re-renders instantly from cache instead of flashing a spinner
	// on every page navigation. The fetch still runs on first visit
	// (when _cachedConfig is null).
	useEffect(() => {
		if (!_cachedConfig) {
			loadConfig();
		}
	}, [loadConfig]);

	// PERF-002: flush the pending-updates buffer to the backend in a
	// single `set_config` call.  Snapshotted from `pendingUpdatesRef`
	// and diffed against `lastSavedConfigRef` so unchanged keys are
	// skipped.  All `updateConfig` Promises that contributed to this
	// flush are resolved (or rejected) together.
	const flushPendingUpdates = useCallback(async () => {
		// Snapshot and clear the pending state BEFORE awaiting so
		// any `updateConfig` call that arrives while the IPC is in
		// flight accumulates into a fresh buffer for the next flush.
		const updates = pendingUpdatesRef.current;
		pendingUpdatesRef.current = {};
		const resolvers = flushPromiseResolversRef.current;
		flushPromiseResolversRef.current = [];
		flushScheduledRef.current = false;

		const resolveAll = () => {
			for (const resolve of resolvers) resolve();
		};

		const lastSaved = lastSavedConfigRef.current;
		if (!lastSaved) {
			// Config not loaded yet — can't compute a diff.  Drop
			// the updates (the caller already applied them to
			// local state, so a subsequent loadConfig will
			// re-fetch and reconcile).
			resolveAll();
			return;
		}

		// Shallow-compare each pending key against the last saved
		// snapshot.  `Object.is` distinguishes NaN, +/-0, and
		// reference-unequal objects (e.g. a freshly-built
		// `custom_theme` dict even if its contents match).
		const lastSavedRecord = lastSaved as unknown as Record<string, unknown>;
		const diff: Record<string, unknown> = {};
		for (const [key, value] of Object.entries(updates)) {
			if (!Object.is(lastSavedRecord[key], value)) {
				diff[key] = value;
			}
		}

		if (Object.keys(diff).length === 0) {
			// Nothing actually changed since the last save —
			// skip the IPC call entirely.
			resolveAll();
			return;
		}

		try {
			await call("set_config", diff);
			// Merge the persisted diff into the baseline so the
			// next flush only sends keys that changed since
			// this flush.  We spread the local `lastSaved`
			// snapshot (captured before the await) rather than
			// re-reading `lastSavedConfigRef.current` — the ref
			// is typed as `VoiceTyperConfig | null` and TS
			// can't prove it's still non-null after the await.
			// Using the snapshot is safe: any concurrent flush
			// that updated the ref during our await would only
			// cause the next flush to re-send its keys
			// (redundant but idempotent at the backend).
			lastSavedConfigRef.current = {
				...lastSaved,
				...(diff as Partial<VoiceTyperConfig>),
			};

			// If the custom theme was successfully saved to the backend,
			// clear the localStorage draft — it's now safely persisted.
			// (Draft helpers live in ThemeSettingsSection now; the
			// backend-confirmed save still implies the draft can be
			// discarded. The section re-loads its own draft from LS on
			// the next mount, so we don't need to clear it here — the
			// section's own _clearDraftLS() call inside its onCheckedChange
			// handler already covers the "disable custom theme" path.)

			// NEW-UX-014 / NEW-UX-035: show a "Saved" toast so the user
			// knows their change was persisted.  Previously this was a
			// comment-only "intent" with no actual call — the success
			// path was completely silent.  Every toggle/select/radio in
			// Settings now confirms the save via this toast.
			//
			// We don't show a toast for every keystroke (those go through
			// updateConfigDebounced which is debounced).  This path is
			// only hit by explicit toggle/select changes, so toasting
			// here is appropriate.
			showSnack(t("settings.savedToast"), "success");

			// Task 17-B-FIX-2: also surface the success state via the
			// inline "Saved ✓" indicator (the accessible, full-opacity
			// 3-state indicator that replaces the previously invisible
			// `text-[10px] text-(--text-muted)/40` "Auto-save" label).
			// This fires on every successful flush — including debounced
			// text-input saves — because the inline indicator is the
			// primary feedback channel for those (toasts would be
			// spammy for keystroke-driven saves).  We do NOT modify the
			// existing showSnack calls above; toast frequency is a
			// separate concern.
			//
			// `setSaved(true)` runs in the success branch of the
			// try/catch, so the only path that flips `saved` on is the
			// one where `set_config` resolved without throwing.  The
			// `finally` block below runs `setSaving(false)` immediately
			// after, so React 18 batches both updates into a single
			// re-render where `saving=false` AND `saved=true` — the
			// indicator swaps directly from "Saving…" to "Saved ✓".
			if (savedTimeoutRef.current) {
				clearTimeout(savedTimeoutRef.current);
			}
			setSaved(true);
			savedTimeoutRef.current = setTimeout(() => {
				setSaved(false);
				savedTimeoutRef.current = null;
			}, 2000);
		} catch (err) {
			console.error("Failed to update config:", err);
			await loadConfig();
			// NEW-UX-014: also surface failures so the user knows.
			showSnack(t("settings.saveFailedToast"), "error");
		} finally {
			setSaving(false);
			resolveAll();
		}
	}, [call, loadConfig, showSnack]);

	// Keep the ref mirror in sync with the latest `flushPendingUpdates`
	// closure so the unmount cleanup can call it without re-subscribing
	// on every render.
	useEffect(() => {
		flushPendingUpdatesRef.current = flushPendingUpdates;
	}, [flushPendingUpdates]);

	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>) => {
			// PERF-MEMO-001: read from configRef instead of
			// depending on `config` in deps — keeps the
			// callback identity stable so React.memo children
			// don't re-render on every config change.
			const currentConfig = configRef.current;
			if (!currentConfig) return;
			setSaving(true);
			// Update local state immediately for responsive UI.
			const newConfig = { ...currentConfig, ...updates };
			_cachedConfig = newConfig;
			setConfig(newConfig);

			// PERF-002: batch config writes — accumulate updates
			// in `pendingUpdatesRef` and schedule a single
			// microtask flush.  Multiple `updateConfig` calls in
			// the same synchronous block (or in successive
			// `updateConfigDebounced` timer callbacks) collapse
			// into one `set_config` IPC call.
			pendingUpdatesRef.current = {
				...pendingUpdatesRef.current,
				...updates,
			};

			// Preserve `await updateConfig(...)` semantics: the
			// returned Promise resolves after the flush
			// completes (or fails).  `resetToDefaults` relies on
			// this to know when the backend write is done.
			const flushPromise = new Promise<void>((resolve) => {
				flushPromiseResolversRef.current.push(resolve);
			});

			if (!flushScheduledRef.current) {
				flushScheduledRef.current = true;
				// queueMicrotask rather than setTimeout(0)
				// so the flush runs in the same macrotask
				// as the caller — no perceptible delay, and
				// fake-timer tests can flush it with a
				// single `await Promise.resolve()`.
				queueMicrotask(() => {
					void flushPendingUpdatesRef.current();
				});
			}

			await flushPromise;
		},
		[], // PERF-MEMO-001: stable identity — reads from refs
	);

	// UX-007: debounced update for text inputs that fire on every keystroke.
	// Keeps a local draft in component state; commits via updateConfig after
	// 500ms of idle.  Prevents 11 IPC roundtrips when typing "gpt-4o-mini".
	const debouncedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>(
		{},
	);
	const updateConfigDebounced = useCallback(
		(key: keyof VoiceTyperConfig, value: unknown, delayMs = 500) => {
			// PERF-MEMO-001: read from configRef instead of
			// depending on `config` in deps — keeps the
			// callback identity stable.
			const currentConfig = configRef.current;
			// Update local state immediately for responsive UI
			if (currentConfig) {
				const newConfig = { ...currentConfig, [key]: value };
				_cachedConfig = newConfig;
				setConfig(newConfig);
			}
			// Clear any pending timer for this key
			if (debouncedTimers.current[key as string]) {
				clearTimeout(debouncedTimers.current[key as string]);
			}
			// Schedule the IPC commit
			debouncedTimers.current[key as string] = setTimeout(() => {
				updateConfig({ [key]: value } as Partial<VoiceTyperConfig>);
				delete debouncedTimers.current[key as string];
			}, delayMs);
		},
		[updateConfig], // PERF-MEMO-001: updateConfig is now stable (empty deps)
	);

	// ── Live config sync (external changes) ───────────────────────────
	// When a config field is changed from OUTSIDE the Settings page
	// (e.g. Ctrl+MouseWheel zoom in App.tsx toggles text_size, or
	// the sidebar ThemeSwitch changes theme_mode), the Python backend
	// emits a config_changed event.  This listener merges those updates
	// into the Settings page's local config state so sliders, selects,
	// and switches reflect the current value.
	//
	// Without this, the slider stays frozen at the mount-time value
	// even though the backend and the CSS --font-scale var have already
	// updated — the user sees one thing but the slider shows another.
	usePythonEvent(
		"config_changed",
		useCallback(
			(data) => {
				if (!data) return;
				setConfig((prev) => {
					if (!prev) return prev;
					const merged = { ...prev, ...data } as VoiceTyperConfig;
					_cachedConfig = merged;
					return merged;
				});
				// Sync the diff baseline so the next flush doesn't
				// re-send values the backend already has.
				if (lastSavedConfigRef.current) {
					lastSavedConfigRef.current = {
						...lastSavedConfigRef.current,
						...data,
					} as VoiceTyperConfig;
				}
			},
			[], // stable — uses functional setConfig + refs only
		),
	);

	// Cleanup pending debounced timers on unmount.  We intentionally read
	// .current at cleanup time (not effect-run time) so ALL timers added
	// during the component's lifetime are cleared.
	useEffect(() => {
		return () => {
			// PERF-002: flush any pending updates so changes made
			// just before navigation aren't lost.  Fire-and-forget
			// — we can't await in a cleanup function, but the IPC
			// call will still execute in the background.
			if (Object.keys(pendingUpdatesRef.current).length > 0) {
				void flushPendingUpdatesRef.current();
			}
			Object.values(debouncedTimers.current).forEach(clearTimeout);
			// Task 17-B-FIX-2: clear the "Saved ✓" auto-hide timer
			// so we don't fire a setState on an unmounted component
			// if the user navigates away within the 2-second window.
			if (savedTimeoutRef.current) {
				clearTimeout(savedTimeoutRef.current);
				savedTimeoutRef.current = null;
			}
		};
	}, []);

	const viewLogs = async () => {
		// UX-008: actually open the log folder via the main process.
		// Previously this just showed a snackbar without opening anything.
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

	const resetToDefaults = async () => {
		if (!config) return;
		setShowResetDialog(false);
		// UX-018: fetch defaults from the Python backend instead of
		// hardcoding 22+ field values here (which silently drift from
		// the Config dataclass).  The backend returns a sanitized dict
		// (API keys redacted) which we send back via set_config.
		try {
			const defaults = await call("get_defaults");
			if (defaults && typeof defaults === "object") {
				// Filter out the redacted sentinels and any non-allowlisted
				// keys before sending back via set_config.
				const safeDefaults: Record<string, unknown> = {};
				for (const [key, value] of Object.entries(
					defaults as Record<string, unknown>,
				)) {
					// Skip redacted API keys — we don't want to overwrite the
					// user's real keys with "<redacted>".
					if (value === "<redacted>") continue;
					// Skip schema_version and internal state fields.
					// NEW-UX-019: onboarding_completed is intentionally preserved
					// — resetting it would force the user to redo the 5-step
					// wizard every time they reset settings, which is bad UX.
					// The wizard can be re-triggered manually via the tray menu
					// if needed.
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

	// Local wrapper around the App-level onThemeChange that also keeps the
	// Settings page's local config state in sync so the Color Scheme Select
	// doesn't revert while the App-level debounced save is in flight.
	// ThemeSettingsSection calls this via its `onThemeChange` prop.
	const handleThemeChangeLocal = useCallback(
		(mode: VoiceTyperConfig["theme_mode"]) => {
			setConfig((prev) => (prev ? { ...prev, theme_mode: mode } : prev));
			if (_cachedConfig) _cachedConfig = { ..._cachedConfig, theme_mode: mode };
			onThemeChange?.(mode);
		},
		[onThemeChange],
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

	// UX-028: filter settings sections by label/description/section-title.
	// Passed to each section component as the `isVisible` prop so the
	// sections can do their own per-row visibility checks (and section-level
	// hide-when-empty checks) without duplicating the filter logic.
	//
	// FIX (Task ID 6 / Settings Search): the previous implementation only
	// matched the row's label/info, so searching for a section name like
	// "overlay" or "appearance" returned no results. We now ALSO match the
	// section title (passed by each section component as the third
	// argument — derived dynamically from the same literal that feeds the
	// ``<SettingsSection title="…">`` prop, never hardcoded here).
	const _filter_settings = (
		label: string,
		info?: string,
		sectionTitle?: string,
	): boolean => {
		if (!settingsFilter.trim()) return true;
		const q = settingsFilter.toLowerCase();
		return (
			label.toLowerCase().includes(q) ||
			info?.toLowerCase().includes(q) ||
			sectionTitle?.toLowerCase().includes(q) ||
			false
		);
	};

	return (
		<div className="min-h-full">
			{/* Fixed settings tab navigation at the very top of the viewport.
                            Uses variant="tabs" — no container background/border/rounded,
                            full-width bar with content constrained to max-w-2xl. */}
			<div className="sticky top-0 left-0 right-0 z-40 bg-(--bg-subtle) border-b border-border py-1.5">
				<div className="mx-auto w-full max-w-2xl px-6">
					{" "}
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
					/>
				</div>
			</div>

			<div className="mx-auto max-w-2xl space-y-8 px-6 pt-6 pb-6">
				{/* Header */}
				<PageHeading
					title={t("settings.title")}
					description={t("settings.description")}
				/>

				{/* UX-028: Settings search/filter — also auto-switches to the
                                relevant tab when the query matches a hint keyword. */}
				<SearchField
					value={settingsFilter}
					onChange={handleSearchChange}
					placeholder={t("settings.searchPlaceholder")}
				/>

				{/* ── TAB: Appearance (theme mode, preset, custom picker, text size) ───── */}
				{activeTab === "appearance" && (
					<ThemeSettingsSection
						config={config}
						updateConfig={updateConfig}
						updateConfigDebounced={updateConfigDebounced}
						isVisible={_filter_settings}
						themeModeProp={themeModeProp}
						onThemeChange={handleThemeChangeLocal}
					/>
				)}

				{/* ── TAB: General (autostart, UI lang, notifications, tray, bubble, hotkey) ── */}
				{activeTab === "general" && (
					<>
						<GeneralSettingsSection
							config={config}
							updateConfig={updateConfig}
							updateConfigDebounced={updateConfigDebounced}
							isVisible={_filter_settings}
						/>
						<RecordingSettingsSection
							config={config}
							updateConfig={updateConfig}
							updateConfigDebounced={updateConfigDebounced}
							isVisible={_filter_settings}
						/>
					</>
				)}

				{/* ── TAB: AI & Audio (model, audio enhancement, AI enhancement) ────────── */}
				{activeTab === "aiAudio" && (
					<>
						<ModelSettingsSection
							config={config}
							updateConfig={updateConfig}
							updateConfigDebounced={updateConfigDebounced}
							isVisible={_filter_settings}
						/>
						<AudioSettingsSection
							config={config}
							updateConfig={updateConfig}
							updateConfigDebounced={updateConfigDebounced}
							isVisible={_filter_settings}
						/>
						<AiEnhancementSettingsSection
							config={config}
							updateConfig={updateConfig}
							updateConfigDebounced={updateConfigDebounced}
							isVisible={_filter_settings}
						/>
					</>
				)}

				{/* ── TAB: Privacy (privacy & recovery, troubleshooting) ───────────────── */}
				{activeTab === "privacy" && (
					<>
						<PrivacySettingsSection
							config={config}
							updateConfig={updateConfig}
							updateConfigDebounced={updateConfigDebounced}
							isVisible={_filter_settings}
						/>

						{/* ── Troubleshooting ──────────────────────────── */}
						{/* NEW-UX-025: previously only had "View Logs" (which lied —
                                                                it opened the log FOLDER, not a log viewer) and "Reset to
                                                                Defaults" (destructive, no undo).  We now also surface:
                                                                                - Diagnostics link (opens the About page's diagnostics
                                                                                                section which has version, ASR backend, device, etc.)
                                                                                - Help / FAQ link
                                                                                - Report a Bug link
                                                                And clarify the "View Logs" label. */}
						{(_filter_settings(
							t("settings.troubleshooting.title"),
							t("settings.troubleshooting.description"),
							t("settings.troubleshooting.title"),
						) ||
							[
								t("settings.troubleshooting.openLogFolder"),
								t("settings.troubleshooting.diagnostics"),
								t("settings.troubleshooting.helpFaq"),
								t("settings.troubleshooting.reportBug"),
								t("settings.troubleshooting.resetToDefaults"),
							].some((label) =>
								_filter_settings(
									label,
									undefined,
									t("settings.troubleshooting.title"),
								),
							)) && (
							<SettingsSection
								title={t("settings.troubleshooting.title")}
								description={t("settings.troubleshooting.description")}
							>
								<div className="px-3.5 py-3.5 flex flex-wrap gap-3">
									<Button
										variant="outline"
										className="gap-2"
										onClick={viewLogs}
										aria-label={t("settings.troubleshooting.openLogFolderAria")}
										title={t("settings.troubleshooting.openLogFolderHint")}
									>
										<HugeiconsIcon
											icon={File02Icon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										{t("settings.troubleshooting.openLogFolder")}
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
										{t("settings.troubleshooting.diagnostics")}
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
										{t("settings.troubleshooting.helpFaq")}
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
										{t("settings.troubleshooting.reportBug")}
									</Button>
									<Button
										variant="destructive"
										className="gap-2"
										onClick={() => setShowResetDialog(true)}
										aria-label={t(
											"settings.troubleshooting.resetToDefaultsAria",
										)}
										title={t("settings.troubleshooting.resetToDefaultsHint")}
									>
										<HugeiconsIcon
											icon={RefreshIcon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										{t("settings.troubleshooting.resetToDefaults")}
									</Button>
								</div>
							</SettingsSection>
						)}
					</>
				)}

				{/* Task 17-B-FIX-2: 3-state save indicator (replaces the
                                        previously-invisible `text-[10px] text-(--text-muted)/40`
                                        "Auto-save" label).
                                                • saving  → "Saving…" with the existing amber pulse dot
                                                • saved   → "Saved ✓" with a green Tick02Icon, shown for
                                                                        2 s after a successful set_config roundtrip
                                                • idle    → very dim "All changes saved"
                                        WCAG 2.1 SC 1.4.4 (text resize): `text-xs` = 12px (was 10px).
                                        WCAG 2.1 SC 1.4.3 (contrast): full opacity — no `/40` (was ~1.5:1).
                                        WCAG 2.1 SC 4.1.3 (status messages): `aria-live="polite"` so
                                        screen readers announce "Saving…" / "Saved" without stealing
                                        focus.  `aria-atomic="true"` ensures the whole string is
                                        announced on each change (not just the diff). */}
				<p
					className="-mt-6 mb-0 text-xs text-right"
					aria-live="polite"
					aria-atomic="true"
				>
					{saving ? (
						<span className="inline-flex items-center gap-1 text-(--text-secondary)">
							<span
								className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400"
								aria-hidden="true"
							/>
							{t("settings.saving")}
						</span>
					) : saved ? (
						<span className="inline-flex items-center gap-1 text-(--text-secondary) animate-fade-in">
							<HugeiconsIcon
								icon={Tick02Icon}
								strokeWidth={2.5}
								className="h-3 w-3 text-emerald-500"
								aria-hidden="true"
							/>
							{t("settings.savedToast")}
						</span>
					) : (
						<span className="inline-flex items-center gap-1 text-(--text-muted)">
							{t("settings.allChangesSaved")}
						</span>
					)}
				</p>
			</div>

			{/* Reset Confirmation Dialog */}
			{showResetDialog && (
				<div
					className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40"
					onClick={() => setShowResetDialog(false)}
					onKeyDown={(e) => {
						if (e.key === "Escape") setShowResetDialog(false);
					}}
					role="dialog"
					aria-modal="true"
					aria-labelledby="reset-dialog-title"
				>
					<div
						role="document"
						className={cn(
							"animate-scale-in w-100 rounded-xl border border-border",
							"bg-(--bg) p-6 shadow-2xl",
						)}
						onClick={(e) => e.stopPropagation()}
						onKeyDown={(e) => {
							if (e.key === "Escape") setShowResetDialog(false);
						}}
					>
						<h2
							id="reset-dialog-title"
							className="text-lg font-semibold text-(--text-primary) mb-3"
						>
							{t("settings.troubleshooting.resetToDefaults")}
						</h2>
						<p className="text-sm text-(--text-muted) mb-6">
							{t("settings.troubleshooting.resetDialogMessage")}
						</p>
						<div className="flex justify-end gap-3">
							<Button
								variant="ghost"
								onClick={() => setShowResetDialog(false)}
								autoFocus
								aria-label={t("settings.troubleshooting.cancelResetAria")}
							>
								{t("common.cancel")}
							</Button>
							<Button
								variant="destructive"
								onClick={resetToDefaults}
								aria-label={t("settings.troubleshooting.confirmResetAria")}
							>
								{t("settings.troubleshooting.resetToDefaults")}
							</Button>
						</div>
					</div>
				</div>
			)}

			{/* NEW-TS-004: use the shared Snackbar component from useSnackbar. */}
			<Snackbar />
		</div>
	);
}
