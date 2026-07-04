import {
	Book02Icon,
	Bug02Icon,
	File02Icon,
	InformationCircleIcon,
	RefreshIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import PageHeading from "@/components/PageHeading";
import { SearchField } from "@/components/SearchField";
import { SettingsSection } from "@/components/SettingsSection";
import { Spinner } from "@/components/Spinner";
import { AiEnhancementSettingsSection } from "@/components/settings/AiEnhancementSettingsSection";
import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import { HotkeySettingsSection } from "@/components/settings/HotkeySettingsSection";
import { ModelSettingsSection } from "@/components/settings/ModelSettingsSection";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
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
			setActiveTab((prev) => (prev !== bestTab ? bestTab! : prev));
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
			showSnack("Saved", "success");
		} catch (err) {
			console.error("Failed to update config:", err);
			await loadConfig();
			// NEW-UX-014: also surface failures so the user knows.
			showSnack("Failed to save setting", "error");
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
			if (!config) return;
			setSaving(true);
			// Update local state immediately for responsive UI.
			const newConfig = { ...config, ...updates };
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
					void flushPendingUpdates();
				});
			}

			await flushPromise;
		},
		[config, flushPendingUpdates],
	);

	// UX-007: debounced update for text inputs that fire on every keystroke.
	// Keeps a local draft in component state; commits via updateConfig after
	// 500ms of idle.  Prevents 11 IPC roundtrips when typing "gpt-4o-mini".
	const debouncedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>(
		{},
	);
	const updateConfigDebounced = useCallback(
		(key: keyof VoiceTyperConfig, value: unknown, delayMs = 500) => {
			// Update local state immediately for responsive UI
			if (config) {
				const newConfig = { ...config, [key]: value };
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
		[config, updateConfig],
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
		};
	}, []);

	const viewLogs = async () => {
		// UX-008: actually open the log folder via the main process.
		// Previously this just showed a snackbar without opening anything.
		try {
			const result = await window.window_?.openLogs?.();
			if (result?.success) {
				showSnack("Log folder opened", "success");
			} else {
				showSnack(result?.error || "Could not open log folder", "error");
			}
		} catch (err) {
			console.error("Failed to open logs:", err);
			showSnack("Could not open log folder", "error");
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
				showSnack("Settings reset to defaults", "success");
			} else {
				showSnack("Failed to fetch defaults from backend", "error");
			}
		} catch (err) {
			console.error("Failed to reset to defaults:", err);
			showSnack("Failed to reset to defaults", "error");
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
					<p className="text-sm text-(--text-muted)">Loading settings...</p>
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
			<div className="mx-auto max-w-2xl space-y-8 px-6 pt-28 pb-6">
				{/* Header */}
				<PageHeading
					title={t("settings.title")}
					description="Adjust Voice Typer to your preferences."
				/>

				{/* UX-028: Settings search/filter — also auto-switches to the
                                relevant tab when the query matches a hint keyword. */}
				<SearchField
					value={settingsFilter}
					onChange={handleSearchChange}
					placeholder={t("settings.searchPlaceholder")}
				/>

				{/* NEW: Settings tab navigation — SegmentedControl at the top switches between tab groups */}
				<div className="flex justify-center">
					<SegmentedControl<SettingsTab>
						variant="tabs"
						options={[
							{ value: "appearance", label: t("settings.tabs.appearance") },
							{ value: "general", label: t("settings.tabs.general") },
							{ value: "aiAudio", label: t("settings.tabs.aiAudio") },
							{ value: "privacy", label: t("settings.tabs.privacy") },
						]}
						value={activeTab}
						onChange={setActiveTab}
						ariaLabel="Settings tabs"
					/>
				</div>

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
						<HotkeySettingsSection
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
							"Troubleshooting",
							"Diagnostic tools, help, and support.",
							"Troubleshooting",
						) ||
							[
								"Open Log Folder",
								"Diagnostics",
								"Help & FAQ",
								"Report a Bug",
								"Reset to Defaults",
							].some((label) =>
								_filter_settings(label, undefined, "Troubleshooting"),
							)) && (
							<SettingsSection
								title="Troubleshooting"
								description="Diagnostic tools, help, and support."
							>
								<div className="px-3.5 py-3.5 flex flex-wrap gap-3">
									<Button
										variant="outline"
										className="gap-2"
										onClick={viewLogs}
										aria-label="Open log folder"
										title="Open the folder containing the Python backend's log files"
									>
										<HugeiconsIcon
											icon={File02Icon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										Open Log Folder
									</Button>
									<Button
										variant="outline"
										className="gap-2"
										onClick={() => onNavigate?.("about")}
										aria-label="Open Diagnostics"
										title="Open the About page with version, backend status, and config info"
									>
										<HugeiconsIcon
											icon={InformationCircleIcon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										Diagnostics
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
										aria-label="Open documentation"
										title="Open the project README in your browser"
									>
										<HugeiconsIcon
											icon={Book02Icon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										Help & FAQ
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
										aria-label="Report a bug"
										title="Open the GitHub issue tracker"
									>
										<HugeiconsIcon
											icon={Bug02Icon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										Report a Bug
									</Button>
									<Button
										variant="destructive"
										className="gap-2"
										onClick={() => setShowResetDialog(true)}
										aria-label="Reset to Defaults"
										title="Reset all settings to their default values (cannot be undone)"
									>
										<HugeiconsIcon
											icon={RefreshIcon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										Reset to Defaults
									</Button>
								</div>
							</SettingsSection>
						)}
					</>
				)}

				{/* BUGFIX: replaced the fixed bottom-right banner with a subtle
                                        header subtitle that's barely visible — shows "Auto-save" in
                                        dim text only during the brief save operation, then fades to
                                        invisible. The old design was distracting and always visible. */}
				<p className="-mt-6 mb-0 text-[10px] text-(--text-muted)/40 text-right">
					{saving ? (
						<span className="inline-flex items-center gap-1">
							<span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
							Saving...
						</span>
					) : (
						<span className="inline-flex items-center gap-1">Auto-save</span>
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
							Reset to Defaults
						</h2>
						<p className="text-sm text-(--text-muted) mb-6">
							Are you sure you want to reset all settings to their default
							values? This cannot be undone.
						</p>
						<div className="flex justify-end gap-3">
							<Button
								variant="ghost"
								onClick={() => setShowResetDialog(false)}
								aria-label="Cancel reset"
							>
								Cancel
							</Button>
							<Button
								variant="destructive"
								onClick={resetToDefaults}
								autoFocus
								aria-label="Confirm reset to defaults"
							>
								Reset to Defaults
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
