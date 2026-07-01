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
import { RangeSlider } from "@/components/RangeSlider";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NumberInput } from "@/components/ui/number-input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { usePython } from "@/hooks/usePython";
// NEW-TS-004: use the shared useSnackbar hook instead of re-implementing
// the useState + setTimeout + JSX pattern inline.  Previously this page
// had its own ``showSnack`` function with a setTimeout that wasn't
// cleared on unmount (a leak risk if the page unmounted mid-toast).
import { useSnackbar } from "@/hooks/useSnackbar";
import { cn } from "@/lib/utils";
import {
	applyThemeVars,
	CUSTOM_COLOR_KEYS,
	type CustomThemeData,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	deriveCustomVars,
	THEMES,
} from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

// ── localStorage draft backup ───────────────────────────────────────
// Persists the custom theme color picker draft to localStorage on every
// change.  If the backend save fails (process crash, network blip, etc.),
// the user's unsaved colors are recovered on the next page visit.
// Cleared when the backend confirms the save.
const _LS_DRAFT_KEY = "vt_custom_theme_draft";

function _saveDraftToLS(data: CustomThemeData): void {
	try {
		localStorage.setItem(_LS_DRAFT_KEY, JSON.stringify(data));
	} catch {
		// localStorage may be full or unavailable — non-fatal
	}
}

function _loadDraftFromLS(): CustomThemeData | null {
	try {
		const raw = localStorage.getItem(_LS_DRAFT_KEY);
		if (!raw) return null;
		return JSON.parse(raw) as CustomThemeData;
	} catch {
		return null;
	}
}

function _clearDraftLS(): void {
	try {
		localStorage.removeItem(_LS_DRAFT_KEY);
	} catch {
		// non-fatal
	}
}

/** Convert any CSS color value to #rrggbb hex using a temporary canvas. */
function cssColorToHex(color: string): string {
	if (!color) return "#000000";
	if (color.startsWith("#")) return color.slice(0, 7);
	const ctx = document.createElement("canvas").getContext("2d");
	if (!ctx) return "#000000";
	ctx.fillStyle = color;
	const normalized = ctx.fillStyle;
	if (normalized.startsWith("#")) return normalized.slice(0, 7);
	const match = normalized.match(/^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/);
	if (match) {
		return (
			"#" +
			match
				.slice(1, 4)
				.map((c) => Number(c).toString(16).padStart(2, "0"))
				.join("")
		);
	}
	return "#000000";
}

/**
 * Read the 6 core theme colors for BOTH light and dark modes of the
 * currently-selected built-in preset.
 *
 * Uses the THEMES array directly (converting OKLCH → hex) instead of
 * reading from the DOM.  This is necessary because preset CSS vars are
 * applied as inline styles on ``document.documentElement`` — toggling
 * the ``.dark`` class doesn't change which inline vars are set, so
 * ``getComputedStyle`` returns the same values for both modes.
 *
 * Falls back to DOM reading for ``'default'`` and ``'custom'`` presets
 * (which have no defined vars in the THEMES array).
 */
function getCurrentThemeColors(currentPresetId: string): {
	light: Record<string, string>;
	dark: Record<string, string>;
} {
	const keys = CUSTOM_COLOR_KEYS.map((k) => k.var);

	// Built-in preset with defined vars — read from THEMES array directly
	if (
		currentPresetId &&
		currentPresetId !== "default" &&
		currentPresetId !== "custom"
	) {
		const theme = THEMES.find((t) => t.id === currentPresetId);
		if (theme) {
			const light: Record<string, string> = {};
			const dark: Record<string, string> = {};
			for (const key of keys) {
				light[key] = cssColorToHex(theme.light[key] ?? "");
				dark[key] = cssColorToHex(theme.dark[key] ?? "");
			}
			return { light, dark };
		}
	}

	// Fallback: read from DOM (default/custom preset, or lookup failed)
	const root = document.documentElement;
	const hadDark = root.classList.contains("dark");

	root.classList.remove("dark");
	const lightStyle = getComputedStyle(root);
	const light: Record<string, string> = {};
	for (const key of keys) {
		light[key] = cssColorToHex(lightStyle.getPropertyValue(key).trim());
	}

	root.classList.add("dark");
	const darkStyle = getComputedStyle(root);
	const dark: Record<string, string> = {};
	for (const key of keys) {
		dark[key] = cssColorToHex(darkStyle.getPropertyValue(key).trim());
	}

	root.classList.toggle("dark", hadDark);
	return { light, dark };
}

import { HotkeyPicker } from "@/components/HotkeyPicker";
import { SearchField } from "@/components/SearchField";
import { Spinner } from "@/components/Spinner";
import {
	getLocale,
	getLocaleLabel,
	SUPPORTED_LOCALES,
	setLocale,
	t,
} from "@/i18n/i18n";

// Module-level cache — persists across page navigations so settings render
// instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null;

const LANGUAGE_OPTIONS = [
	{
		value: "auto",
		label: "Auto-detect",
		description: "Any language — no hallucination filtering",
	},
	{
		value: "en",
		label: "English",
		description: "Enables Latin-script hallucination filter",
	},
	{ value: "zh", label: "Chinese" },
	{ value: "es", label: "Spanish" },
	{ value: "ar", label: "Arabic" },
	{ value: "fr", label: "French" },
	{ value: "ru", label: "Russian" },
	{ value: "pt", label: "Portuguese" },
	{ value: "de", label: "German" },
	{ value: "ja", label: "Japanese" },
	{ value: "ko", label: "Korean" },
	{ value: "it", label: "Italian" },
	{ value: "nl", label: "Dutch" },
	{ value: "pl", label: "Polish" },
	{ value: "tr", label: "Turkish" },
	{ value: "vi", label: "Vietnamese" },
	{ value: "th", label: "Thai" },
	{ value: "hi", label: "Hindi" },
	{ value: "id", label: "Indonesian" },
	{ value: "sv", label: "Swedish" },
	{ value: "da", label: "Danish" },
	{ value: "fi", label: "Finnish" },
	{ value: "no", label: "Norwegian" },
	{ value: "cs", label: "Czech" },
	{ value: "ro", label: "Romanian" },
	{ value: "hu", label: "Hungarian" },
	{ value: "el", label: "Greek" },
	{ value: "he", label: "Hebrew" },
];

const AUTO_STOP_OPTIONS = [
	{ value: 60, label: "1 minute" },
	{ value: 120, label: "2 minutes" },
	{ value: 180, label: "3 minutes" },
	{ value: 300, label: "5 minutes" },
];

const RECORDING_MODE_OPTIONS = [
	{ value: "toggle", label: "Toggle (F2)" },
	{ value: "push_to_talk", label: "Push-to-Talk" },
] as const;

const _THEME_OPTIONS = [
	{ value: "system", label: "System Default" },
	{ value: "light", label: "Light" },
	{ value: "dark", label: "Dark" },
] as const;

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

const LLM_PRESET_OPTIONS = [
	{ value: "professional", label: "Professional" },
	{ value: "casual", label: "Casual" },
	{ value: "email", label: "Email" },
	{ value: "code", label: "Code" },
] as const;

interface SettingsPageProps {
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
	// NEW-UX-025: navigation callback so the Troubleshooting section can
	// route the user to the About page (which has full diagnostics).
	// NEW-TS-ERR-R2-001: typed as `Page` (not `string`).
	onNavigate?: (page: Page) => void;
}

export default function SettingsPage({
	onThemeChange,
	onNavigate,
}: SettingsPageProps) {
	const { call } = usePython();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [saving, setSaving] = useState(false);
	const [showResetDialog, setShowResetDialog] = useState(false);
	// UX-028: search/filter state for settings
	const [settingsFilter, setSettingsFilter] = useState("");
	// Track the last saved theme preset so hover previews can revert
	// to the user's saved choice (not the initial default) if they
	// hover without clicking.
	const savedPresetRef = useRef(config?.theme_preset ?? "default");
	if (config) savedPresetRef.current = config.theme_preset ?? "default";
	// Track the last non-custom preset so we can revert when the
	// custom-theme toggle is turned off.
	const lastNonCustomRef = useRef(
		config?.theme_preset && config.theme_preset !== "custom"
			? config.theme_preset
			: "default",
	);
	if (config?.theme_preset && config.theme_preset !== "custom") {
		lastNonCustomRef.current = config.theme_preset;
	}

	// NEW-TS-004: use the shared useSnackbar hook.  The hook manages the
	// timer ref and clears it on unmount, fixing the leak risk of the
	// previous inline setTimeout (which wasn't cleared if the page
	// unmounted mid-toast).
	const { showSnack, Snackbar } = useSnackbar();
	const [llmKeyVisible, setLlmKeyVisible] = useState(false);
	// Volume backend status — fetched from the Python backend so the UI
	// can show "Volume Backend: pycaw (WASAPI)" / "CoreAudio" / "disabled"
	// and disable the Per-Session Duck toggle on platforms that don't
	// support it (macOS, Linux).  See architecture doc §7.9.
	const [volumeBackend, setVolumeBackend] = useState<{
		available: boolean;
		name: string;
		supports_per_session: boolean;
		is_windows: boolean;
	} | null>(null);

	// NEW-TS-004: removed the inline ``showSnack`` function — the shared
	// ``useSnackbar`` hook now provides it (with proper timer cleanup on
	// unmount).

	const loadConfig = useCallback(async () => {
		try {
			const result = await call<VoiceTyperConfig>("get_config");
			_cachedConfig = result;
			setConfig(result);
		} catch (err) {
			console.error("Failed to load config:", err);
		}
	}, [call]);

	// Fetch the active volume backend so the UI can show its name and
	// disable the Per-Session Duck toggle on platforms that don't support
	// it (macOS, Linux).  Best-effort: if the call fails we leave
	// `volumeBackend` as null and the toggle stays enabled-but-server-
	// validated (the Python side also gates on `supports_per_session`).
	const loadVolumeBackend = useCallback(async () => {
		try {
			const result = await call<{
				available: boolean;
				name: string;
				supports_per_session: boolean;
				is_windows: boolean;
			}>("get_volume_backend_status");
			setVolumeBackend(result);
		} catch (err) {
			console.warn("Failed to load volume backend status:", err);
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
		loadVolumeBackend();
	}, [loadConfig, loadVolumeBackend]);

	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>) => {
			if (!config) return;
			setSaving(true);
			try {
				const newConfig = { ...config, ...updates };
				_cachedConfig = newConfig;
				setConfig(newConfig);
				await call("set_config", updates);

				// If the custom theme was successfully saved to the backend,
				// clear the localStorage draft — it's now safely persisted.
				if ("custom_theme" in updates) {
					_clearDraftLS();
				}

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
			}
		},
		[config, call, loadConfig, showSnack],
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

	// Cleanup pending debounced timers on unmount.  We intentionally read
	// .current at cleanup time (not effect-run time) so ALL timers added
	// during the component's lifetime are cleared.
	useEffect(() => {
		return () => {
			// eslint-disable-next-line react-hooks/exhaustive-deps -- read ref at cleanup time, not effect-run time
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

	const _handleThemeChange = (mode: string) => {
		const m = mode as VoiceTyperConfig["theme_mode"];
		// Keep local state in sync so the Select doesn't revert and updateConfig doesn't overwrite
		setConfig((prev) => (prev ? { ...prev, theme_mode: m } : prev));
		if (_cachedConfig) _cachedConfig = { ..._cachedConfig, theme_mode: m };
		// Theme is saved and applied by the App-level handler (which updates state + saves)
		onThemeChange?.(m);
	};

	// ── Custom theme editor state ───────────────────────────────────
	// Local draft state for the custom theme color picker, initialised
	// from config on mount.  Changes preview immediately via
	// applyThemeVars + deriveCustomVars; saving is debounced to 300ms.
	// NOTE: must be declared before the early return below — hooks must
	// NOT be conditionally called (Rules of Hooks).
	const [customEditorMode, setCustomEditorMode] = useState<"light" | "dark">(
		"light",
	);
	const [customDraft, setCustomDraft] = useState<CustomThemeData | null>(null);

	// Initialise customDraft from config when it loads.
	// Prefer the localStorage draft over the config value — the draft
	// is more recent (it includes any unsaved color picker changes
	// from a previous session where the backend save failed).
	useEffect(() => {
		const draft = _loadDraftFromLS();
		if (draft) {
			setCustomDraft(draft);
		} else if (config?.custom_theme) {
			setCustomDraft(config.custom_theme);
		} else {
			// Seed with defaults so the color picker always has values
			setCustomDraft({
				light: { ...DEFAULT_CUSTOM_LIGHT },
				dark: { ...DEFAULT_CUSTOM_DARK },
			});
		}
	}, [config?.custom_theme]); // only on first load when config becomes available

	// Apply a custom color change immediately for preview, then debounce save
	const handleCustomColorChange = useCallback(
		(mode: "light" | "dark", colorKey: string, hex: string) => {
			setCustomDraft((prev) => {
				if (!prev) return prev;
				const updated: CustomThemeData = {
					...prev,
					[mode]: { ...prev[mode], [colorKey]: hex },
				};
				// Preview immediately on the document
				const isDark = document.documentElement.classList.contains("dark");
				const modeVars = isDark ? updated.dark : updated.light;
				const derived = deriveCustomVars(modeVars, isDark);
				applyThemeVars("custom", isDark, derived);

				// Persist to localStorage immediately (before the backend
				// save completes) so the draft survives a crash or disconnect.
				_saveDraftToLS(updated);

				// Debounced save to backend
				if (debouncedTimers.current.custom_theme) {
					clearTimeout(debouncedTimers.current.custom_theme);
				}
				debouncedTimers.current.custom_theme = setTimeout(() => {
					updateConfig({ custom_theme: updated });
					delete debouncedTimers.current.custom_theme;
				}, 300);

				return updated;
			});
		},
		[updateConfig],
	);

	// ── Theme preset hover preview ─────────────────────────────────
	// Immediately preview a theme preset when the user hovers over it
	// in the dropdown, without waiting for a click.  If the user moves
	// away or closes the dropdown without clicking, revert to the last
	// saved preset.  Preview uses the currently-active dark/light mode.
	const applyHoverPreview = useCallback((presetId: string) => {
		const isDark = document.documentElement.classList.contains("dark");
		applyThemeVars(presetId, isDark);
	}, []);

	const revertToSavedPreset = useCallback(() => {
		const isDark = document.documentElement.classList.contains("dark");
		applyThemeVars(savedPresetRef.current, isDark);
	}, []);

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

	// UX-028: filter settings sections by label/description
	const _filter_settings = (label: string, info?: string): boolean => {
		if (!settingsFilter.trim()) return true;
		const q = settingsFilter.toLowerCase();
		return (
			label.toLowerCase().includes(q) ||
			info?.toLowerCase().includes(q) ||
			false
		);
	};

	// UX-028: check if a section has any visible settings
	const _section_has_visible_items = (
		items: Array<{ label: string; info?: string }>,
	): boolean => {
		if (!settingsFilter.trim()) return true;
		return items.some((item) => _filter_settings(item.label, item.info));
	};

	return (
		<div className="min-h-full">
			<div className="mx-auto max-w-2xl space-y-8 px-6 pt-28 pb-6">
				{/* Header */}
				<PageHeading
					title={t("settings.title")}
					description="Adjust Voice Typer to your preferences."
				/>

				{/* UX-028: Settings search/filter */}
				<SearchField
					value={settingsFilter}
					onChange={setSettingsFilter}
					placeholder={t("settings.searchPlaceholder")}
				/>

				{/* ── SECTION: Appearance ──────────────────────────────── */}
				{_section_has_visible_items([
					{
						label: "Color Scheme",
						info: "Switch between light, dark, or follow your system setting.",
					},
					{
						label: "Theme Preset",
						info: "A built-in color scheme applied on top of your chosen mode.",
					},
					{
						label: "Custom Theme",
						info: "Create your own color scheme with the color picker.",
					},
					{
						label: "Text Size",
						info: "Adjust the UI text size for better readability.",
					},
				]) && (
					<SettingsSection
						title="Appearance"
						description="Color scheme, theme preset, and text sizing."
					>
						{/* ── Color Scheme (light / dark / system) ───────────── */}
						{_filter_settings(
							"Color Scheme",
							"Switch between light, dark, or follow your system setting.",
						) && (
							<SettingRow
								label="Color Scheme"
								info="Switch between light, dark, or follow your system setting."
							>
								<Select
									value={config.theme_mode}
									onValueChange={(v) => {
										const m = v as VoiceTyperConfig["theme_mode"];
										_handleThemeChange(m);
									}}
								>
									<SelectTrigger className="w-40" aria-label="Color Scheme">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{_THEME_OPTIONS.map((opt) => (
											<SelectItem key={opt.value} value={opt.value}>
												{opt.label}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</SettingRow>
						)}

						{/* ── Theme Preset selector with hover preview ──────── */}
						{_filter_settings(
							"Theme Preset",
							"A built-in color scheme applied on top of your chosen mode.",
						) && (
							<SettingRow
								label="Theme Preset"
								info="Choose a built-in color scheme. Hover to preview, click to apply permanently."
							>
								<Select
									value={config.theme_preset ?? "default"}
									onValueChange={(v) => {
										const preset = v as VoiceTyperConfig["theme_preset"];
										savedPresetRef.current = preset;
										updateConfig({ theme_preset: preset });
									}}
									onOpenChange={(open) => {
										if (!open) revertToSavedPreset();
									}}
								>
									<SelectTrigger className="w-44" aria-label="Theme Preset">
										{(() => {
											const current =
												THEMES.find(
													(t) => t.id === (config.theme_preset ?? "default"),
												) ?? THEMES[0];
											return (
												<span className="flex items-center gap-2">
													<span
														className="inline-block h-3.5 w-3.5 shrink-0 rounded-full"
														style={{ backgroundColor: current.swatch }}
													/>
													<span>{current.name}</span>
												</span>
											);
										})()}
									</SelectTrigger>
									<SelectContent
										position="popper"
										align="start"
										onMouseLeave={revertToSavedPreset}
									>
										{/* Filter out 'custom' from the dropdown — custom is now
                    a toggle switch below this row. */}
										{THEMES.filter((t) => t.id !== "custom").map((theme) => (
											<SelectItem
												key={theme.id}
												value={theme.id}
												onMouseEnter={() => applyHoverPreview(theme.id)}
											>
												<span className="flex items-center gap-2.5">
													<span
														className="inline-block h-4 w-4 shrink-0 rounded-full border border-border"
														style={{ backgroundColor: theme.swatch }}
													/>
													<div className="flex flex-col">
														<span className="text-sm font-medium">
															{theme.name}
														</span>
														<span className="text-[10px] text-(--text-muted) leading-tight max-w-48">
															{theme.description}
														</span>
													</div>
												</span>
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</SettingRow>
						)}

						{/* ── Custom Theme toggle ────────────────────────────── */}
						{/* A switch that enables/disables the custom color editor.
              When ON, theme_preset is forced to 'custom' and the color
              picker appears.  When OFF, the preset reverts to the
              previously-selected preset. */}
						{_filter_settings(
							"Custom Theme",
							"Create your own color scheme with the color picker.",
						) && (
							<SettingRow
								label="Custom Theme"
								info="Create your own color scheme — choose each color manually."
							>
								<Switch
									checked={config.theme_preset === "custom"}
									onCheckedChange={(enabled) => {
										if (enabled) {
											// Switch ON → enable custom theme
											// Read the current applied theme colors from the DOM
											// so the color picker starts with the active preset's
											// colors instead of hardcoded defaults.
											const currentColors = getCurrentThemeColors(
												config.theme_preset ?? "default",
											);
											setCustomDraft(currentColors);
											_saveDraftToLS(currentColors);
											updateConfig({
												theme_preset: "custom",
												custom_theme: currentColors,
											});
										} else {
											// Switch OFF → revert to last non-custom preset.
											// Also clear the localStorage draft since the custom
											// colors are no longer active.
											const fallback = lastNonCustomRef.current ?? "default";
											savedPresetRef.current = fallback;
											_clearDraftLS();
											updateConfig({ theme_preset: fallback });
										}
									}}
									aria-label="Custom Theme"
								/>
							</SettingRow>
						)}

						{/* ── Custom Theme color Picker ─────────────────────── */}
						{/* Only visible when the custom theme toggle is ON */}
						{config.theme_preset === "custom" && customDraft && (
							<div className="animate-fade-in px-3.5 pb-4">
								{/* Light / Dark mode tabs */}
								<div className="mb-3 flex gap-1 rounded-lg bg-(--bg-subtle) p-0.5">
									<button
										type="button"
										onClick={() => setCustomEditorMode("light")}
										className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
											customEditorMode === "light"
												? "bg-(--bg) text-(--text-primary) shadow-xs"
												: "text-(--text-muted) hover:text-(--text-primary)"
										}`}
									>
										Light
									</button>
									<button
										type="button"
										onClick={() => setCustomEditorMode("dark")}
										className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
											customEditorMode === "dark"
												? "bg-(--bg) text-(--text-primary) shadow-xs"
												: "text-(--text-muted) hover:text-(--text-primary)"
										}`}
									>
										Dark
									</button>
								</div>

								{/* Color swatch grid — 6 core colors */}
								<div className="grid grid-cols-2 gap-2.5">
									{CUSTOM_COLOR_KEYS.map(
										({ var: varName, label, description }) => {
											const currentHex =
												customDraft[customEditorMode]?.[varName] ??
												(customEditorMode === "light"
													? DEFAULT_CUSTOM_LIGHT[varName]
													: DEFAULT_CUSTOM_DARK[varName]);
											return (
												<div
													key={varName}
													className="flex items-center gap-2.5 rounded-lg border border-border bg-(--bg) p-2"
												>
													<div className="relative shrink-0">
														<input
															type="color"
															value={currentHex}
															onChange={(e) =>
																handleCustomColorChange(
																	customEditorMode,
																	varName,
																	e.target.value,
																)
															}
															className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
															aria-label={`${label} color`}
														/>
														<div
															className="h-8 w-8 rounded-md border border-border shadow-xs"
															style={{ backgroundColor: currentHex }}
														/>
													</div>
													<div className="min-w-0 flex-1">
														<span className="block text-xs font-medium text-(--text-primary)">
															{label}
														</span>
														<p className="truncate text-[10px] text-(--text-muted)">
															{description}
														</p>
													</div>
													<input
														type="text"
														value={currentHex}
														onChange={(e) => {
															const val = e.target.value;
															if (
																/^#[0-9a-fA-F]{0,6}$/.test(val) ||
																val === "#"
															) {
																handleCustomColorChange(
																	customEditorMode,
																	varName,
																	val || "#000000",
																);
															}
														}}
														className="w-18 shrink-0 rounded border border-border bg-(--bg-subtle) px-1.5 py-1 text-center text-[11px] font-mono text-(--text-primary) outline-none focus:border-ring focus:ring-1 focus:ring-ring"
														spellCheck={false}
														aria-label={`${label} hex value`}
													/>
												</div>
											);
										},
									)}
								</div>

								{/* Reset to defaults */}
								<button
									type="button"
									onClick={() => {
										const defaults = {
											light: { ...DEFAULT_CUSTOM_LIGHT },
											dark: { ...DEFAULT_CUSTOM_DARK },
										};
										setCustomDraft(defaults);
										_saveDraftToLS(defaults);
										// Apply default preview
										const isDark =
											document.documentElement.classList.contains("dark");
										const modeVars = isDark ? defaults.dark : defaults.light;
										const derived = deriveCustomVars(modeVars, isDark);
										applyThemeVars("custom", isDark, derived);
										// Save
										updateConfig({ custom_theme: defaults });
									}}
									className="mt-3 w-full rounded-lg border border-border px-3 py-2 text-xs text-(--text-muted) transition-colors hover:bg-(--surface-hover) hover:text-(--text-primary)"
								>
									Reset to Default Colors
								</button>
							</div>
						)}

						{/* ── Text Size ──────────────────────────────────────── */}
						<SettingRow
							label="Text Size"
							info="Adjust the UI text size for better readability. Default is 14px."
						>
							<div className="flex items-center gap-3 w-44">
								<RangeSlider
									value={config.text_size ?? 14}
									onChange={(v) => updateConfig({ text_size: v })}
									min={10}
									max={24}
									step={1}
									ariaLabel="Text Size"
									suffix="px"
								/>
							</div>
						</SettingRow>
					</SettingsSection>
				)}

				{/* ── SECTION: General ──────────────────────────────────── */}
				{_section_has_visible_items([
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
				]) && (
					<SettingsSection
						title={t("settings.general")}
						description="Behavior, startup, and appearance."
					>
						{_filter_settings(
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

						{_filter_settings(
							"Notifications",
							"Show a desktop notification",
						) && (
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

						{_filter_settings(
							"Tray Click",
							"What happens when you left-click",
						) && (
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
				<SettingsSection
					title="Overlay"
					description="Floating recording bubble."
				>
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

				{/* ── SECTION: Hotkey ───────────────────────────────────── */}
				<SettingsSection
					title="Hotkey"
					description="Key to start and stop dictation."
				>
					<SettingRow
						label="Dictation Key"
						info="The keyboard key used to start and stop recording. Click the button to record a new key, or pick from the preset list. Supports F1-F19, Caps Lock, Print Screen, and more."
					>
						<HotkeyPicker
							value={config.hotkey}
							onChange={(h) => updateConfig({ hotkey: h })}
							mode="single"
							aria-label="Dictation key"
						/>
					</SettingRow>
				</SettingsSection>

				{/* ── SECTION: Recording ─────────────────────────────────── */}
				<SettingsSection
					title="Recording"
					description="Behavior, shortcuts, and silence handling."
				>
					{/* ── Dropdowns ──────────────────────────────────────── */}
					<SettingRow
						label="Recording Mode"
						info="Toggle: press the key once to start and again to stop. Push-to-talk: hold the key while speaking."
					>
						<Select
							value={config.recording_mode ?? "toggle"}
							onValueChange={(v) =>
								updateConfig({ recording_mode: v as "toggle" | "push_to_talk" })
							}
						>
							<SelectTrigger className="w-40" aria-label="Recording Mode">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{RECORDING_MODE_OPTIONS.map((opt) => (
									<SelectItem key={opt.value} value={opt.value}>
										{opt.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>

					<SettingRow
						label="Auto-Stop"
						info="Automatically stop recording after this many seconds of silence."
					>
						<Select
							value={String(config.silence_auto_stop_seconds ?? 60)}
							onValueChange={(v) =>
								updateConfig({ silence_auto_stop_seconds: Number(v) })
							}
						>
							<SelectTrigger className="w-36" aria-label="Auto-Stop">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{AUTO_STOP_OPTIONS.map((opt) => (
									<SelectItem key={opt.value} value={String(opt.value)}>
										{opt.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>

					{/* ── Switches ───────────────────────────────────────── */}
					<SettingRow
						label="ESC to Cancel"
						info="Press Escape to cancel an active recording."
					>
						<Switch
							checked={config.esc_cancel_enabled ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ esc_cancel_enabled: checked })
							}
							aria-label="ESC to Cancel"
						/>
					</SettingRow>

					<SettingRow
						label="Auto-Paste"
						info="Automatically paste transcribed text into the currently focused field."
					>
						<Switch
							checked={config.paste_on_stop}
							onCheckedChange={(checked) =>
								updateConfig({ paste_on_stop: checked })
							}
							aria-label="Auto-Paste"
						/>
					</SettingRow>

					{/* NEW-UX-029: Audio cue on record start/stop for accessibility
              and confirmation.  Especially useful for blind users who
              can't see the visual indicator change. */}
					<SettingRow
						label="Sound Feedback"
						info="Play a short audio cue when recording starts and stops. Useful for accessibility and confirmation."
					>
						<Switch
							checked={config.sound_feedback_enabled ?? true}
							onCheckedChange={(checked) => {
								updateConfig({ sound_feedback_enabled: checked });
								// NEW-UX-029: mirror to localStorage so Home.tsx's
								// playSoundCue() can read it without an IPC round-trip
								// (the cue needs to play instantly on record start/stop).
								try {
									localStorage.setItem(
										"vt_sound_feedback_enabled",
										checked ? "1" : "0",
									);
								} catch {
									// localStorage unavailable — non-fatal; the cue just
									// won't play until the next Settings page mount.
								}
							}}
							aria-label="Sound Feedback"
						/>
					</SettingRow>

					{/* ── Inputs ─────────────────────────────────────────── */}
					<SettingRow
						label="Re-Paste Key"
						info="Keyboard shortcut to re-paste the last transcription. Click the button to record a new combo, or pick from the preset list."
					>
						<HotkeyPicker
							value={config.repaste_hotkey ?? "<ctrl>+<alt>+v"}
							onChange={(h) => updateConfig({ repaste_hotkey: h })}
							mode="combo"
							aria-label="Re-paste key"
						/>
					</SettingRow>

					<SettingRow
						label="Silence Warning"
						info="Seconds of silence before showing a warning to help catch microphone issues."
					>
						<div className="flex items-center gap-2">
							<NumberInput
								min={3}
								max={30}
								step={1}
								value={String(config.silence_warning_seconds)}
								onChange={(e) =>
									updateConfigDebounced(
										"silence_warning_seconds",
										Number(e.target.value),
									)
								}
								className="w-20 text-center"
								aria-label="Silence Warning Seconds"
							/>
							<span className="text-sm text-(--text-muted)">sec</span>
						</div>
					</SettingRow>

					<SettingRow
						label="Max Duration"
						info="Maximum recording length. Set to 0 for automatic (varies by device)."
					>
						<div className="flex items-center gap-2">
							<NumberInput
								min={0}
								max={7200}
								step={1}
								value={String(config.max_recording_seconds)}
								onChange={(e) =>
									updateConfigDebounced(
										"max_recording_seconds",
										Number(e.target.value),
									)
								}
								className="w-20 text-center"
								aria-label="Max Recording Duration"
							/>
							<span className="text-sm text-(--text-muted)">sec</span>
						</div>
					</SettingRow>

					{/* AUDIO-DEAD: dead-air timeout — auto-stop after silence follows speech */}
					<SettingRow
						label="Dead-Air Timeout"
						info="Seconds of silence after speech is detected before auto-stopping. 0 = disabled (never auto-stop on silence)."
					>
						<div className="flex items-center gap-2">
							<NumberInput
								min={0}
								max={600}
								step={5}
								value={String(config.dead_air_timeout ?? 30)}
								onChange={(e) =>
									updateConfigDebounced(
										"dead_air_timeout",
										Number(e.target.value),
									)
								}
								className="w-20 text-center"
								aria-label="Dead-Air Timeout Seconds"
							/>
							<span className="text-sm text-(--text-muted)">sec</span>
						</div>
					</SettingRow>
				</SettingsSection>

				{/* ── SECTION: Post-Processing ──────────────────────────── */}
				<SettingsSection
					title="Post-Processing"
					description="Cleanup, corrections, and language."
				>
					<SettingRow
						label="Language"
						info="Auto-detect the spoken language, or pick one for better accuracy."
					>
						<Select
							value={config.language || "auto"}
							onValueChange={(v) =>
								updateConfig({ language: v === "auto" ? "" : v })
							}
						>
							<SelectTrigger className="w-44" aria-label="Language">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{LANGUAGE_OPTIONS.map((lang) => (
									<SelectItem key={lang.value} value={lang.value}>
										<span>{lang.label}</span>
										{lang.description && (
											<span className="ml-2 text-[10px] text-(--text-muted)">
												{lang.description}
											</span>
										)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>

					<SettingRow
						label="Auto Punctuation"
						info="Add periods, commas, and question marks automatically."
					>
						<Switch
							checked={config.auto_punctuation ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ auto_punctuation: checked })
							}
							aria-label="Auto Punctuation"
						/>
					</SettingRow>

					<SettingRow
						label="Text Cleanup"
						info="Fix common misspellings, remove repeated words, and capitalize sentences."
					>
						<Switch
							checked={config.text_cleanup_enabled}
							onCheckedChange={(checked) =>
								updateConfig({ text_cleanup_enabled: checked })
							}
							aria-label="Text Cleanup"
						/>
					</SettingRow>

					<SettingRow
						label="Text Snippets"
						info="Use voice commands to insert pre-written text snippets with placeholders."
					>
						<Switch
							checked={config.templates_enabled ?? true}
							onCheckedChange={(checked) =>
								updateConfig({ templates_enabled: checked })
							}
							aria-label="Text Snippets"
						/>
					</SettingRow>

					<SettingRow
						label="Vocabulary"
						info="Custom word replacements so the transcription uses your preferred terms."
					>
						<Switch
							checked={config.vocabulary_enabled ?? true}
							onCheckedChange={(checked) =>
								updateConfig({ vocabulary_enabled: checked })
							}
							aria-label="Vocabulary"
						/>
					</SettingRow>
				</SettingsSection>

				{/* ── SECTION: LLM Polishing ────────────────────────────── */}
				<SettingsSection
					title="LLM Polishing"
					description="AI-powered transcription enhancement."
				>
					<SettingRow
						label="Enable"
						info="Use an AI language model to clean up and improve the transcribed text. Requires an API key."
					>
						<Switch
							checked={config.llm_polish ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ llm_polish: checked })
							}
							aria-label="LLM Polishing"
						/>
					</SettingRow>

					{config.llm_polish && (
						<div className="animate-fade-in space-y-0 divide-y divide-border">
							<SettingRow
								label="API Key"
								info="Your OpenAI-compatible API key for the polishing service."
							>
								<div className="relative">
									<Input
										type={llmKeyVisible ? "text" : "password"}
										/* SEC-003: backend redacts the key to '<redacted>' in
										 * get_config responses.  Show empty in that case so
										 * the user isn't tempted to "save" the sentinel back.
										 * When the user types a real key, updateConfig sends
										 * it via set_config (which is allowlisted). */
										value={
											config.llm_api_key && config.llm_api_key !== "<redacted>"
												? config.llm_api_key
												: ""
										}
										onChange={(e) =>
											updateConfigDebounced("llm_api_key", e.target.value)
										}
										placeholder={
											config.llm_api_key === "<redacted>"
												? "•••••••• (configured)"
												: ""
										}
										className="w-56 pr-8"
										aria-label="LLM API Key"
									/>
									<Button
										variant="ghost"
										size="xs"
										onClick={() => setLlmKeyVisible(!llmKeyVisible)}
										className="absolute right-1 top-1/2 -translate-y-1/2 text-xs"
										aria-label={llmKeyVisible ? "Hide API key" : "Show API key"}
									>
										{llmKeyVisible ? "Hide" : "Show"}
									</Button>
								</div>
							</SettingRow>

							<SettingRow
								label="API URL"
								info="The endpoint URL for the AI language model service."
							>
								<Input
									value={
										config.llm_api_url ??
										"https://api.openai.com/v1/chat/completions"
									}
									onChange={(e) =>
										updateConfigDebounced("llm_api_url", e.target.value)
									}
									className="w-64"
									aria-label="LLM API URL"
								/>
							</SettingRow>

							<SettingRow
								label="Model"
								info="The AI model to use for polishing (e.g., gpt-4o-mini)."
							>
								<Input
									value={config.llm_model ?? "gpt-4o-mini"}
									onChange={(e) =>
										updateConfigDebounced("llm_model", e.target.value)
									}
									className="w-44"
									aria-label="LLM Model"
								/>
							</SettingRow>

							<SettingRow
								label="Preset"
								info="The writing style to apply — professional, casual, email, or code."
							>
								<Select
									value={config.llm_preset ?? "professional"}
									onValueChange={(v) => updateConfig({ llm_preset: v })}
								>
									<SelectTrigger className="w-40" aria-label="LLM Preset">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{LLM_PRESET_OPTIONS.map((opt) => (
											<SelectItem key={opt.value} value={opt.value}>
												{opt.label}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</SettingRow>
						</div>
					)}
				</SettingsSection>

				{/* ── SECTION: Audio & Recovery ─────────────────────────── */}
				<SettingsSection
					title="Audio & Recovery"
					description="Quality monitoring and safety."
				>
					<SettingRow
						label="Crash Recovery"
						info="Save recent transcriptions so they can be recovered if the app crashes before you paste them."
					>
						<Switch
							checked={config.crash_recovery_enabled ?? true}
							onCheckedChange={(checked) =>
								updateConfig({ crash_recovery_enabled: checked })
							}
							aria-label="Crash Recovery"
						/>
					</SettingRow>
				</SettingsSection>

				{/* ── SECTION: Audio Enhancement ─────────────────────────── */}
				<SettingsSection
					title="Audio Enhancement"
					description="Volume ducking and noise filtering for cleaner dictation."
				>
					<div className="animate-fade-in space-y-0 divide-y divide-border">
						{/* ── Volume Backend status ── */}
						<SettingRow
							label="Volume Backend"
							info="The active audio control backend. 'disabled' means ducking won't work on this platform — install the platform's optional dependency (pycaw on Windows, pyobjc on macOS)."
						>
							<span className="text-sm text-(--text-muted) tabular-nums">
								{volumeBackend
									? volumeBackend.available
										? volumeBackend.name
										: `${volumeBackend.name} (unavailable)`
									: "Detecting…"}
							</span>
						</SettingRow>

						{/* ── Auto Duck Volume ── */}
						<SettingRow
							label="Auto Duck Volume"
							info="Reduce system volume during dictation to prevent speaker bleed into the mic. Smart Duck is built-in: if no audio is playing, the volume won't change. Cross-platform — works on Windows, macOS, and Linux."
						>
							<Switch
								checked={config.volume_duck_enabled ?? true}
								onCheckedChange={(checked) =>
									updateConfig({ volume_duck_enabled: checked })
								}
								aria-label="Auto Duck Volume"
							/>
						</SettingRow>
						<SettingRow
							label="Duck Level"
							info="How quiet to make system audio. 25% = whisper-quiet, 50% = slight dip."
						>
							<RangeSlider
								value={config.volume_duck_level ?? 0.2}
								min={0}
								max={0.5}
								step={0.05}
								onChange={(v) => updateConfigDebounced("volume_duck_level", v)}
								ariaLabel="Duck Level"
								suffix="%"
							/>
						</SettingRow>

						{/* ── ADR 0007: Audio Preset ── */}
						<SettingRow
							label="Microphone Quality"
							info="Presets configure the entire filter chain for common scenarios. Choose 'Custom' for advanced control of individual filters."
						>
							<Select
								value={config.audio_preset ?? "auto"}
								onValueChange={(v) =>
									updateConfig({
										audio_preset: v as VoiceTyperConfig["audio_preset"],
									})
								}
							>
								<SelectTrigger
									className="w-48"
									aria-label="Microphone Quality Preset"
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="auto">Auto (recommended)</SelectItem>
									<SelectItem value="studio">
										Studio (clean environment)
									</SelectItem>
									<SelectItem value="noisy_room">
										Noisy Room (keyboard/fan/HVAC)
									</SelectItem>
									<SelectItem value="off">Off (raw audio)</SelectItem>
									<SelectItem value="custom">Custom (advanced)</SelectItem>
								</SelectContent>
							</Select>
						</SettingRow>

						{/* ── ADR 0007: Custom filter controls (only when preset === 'custom') ── */}
						{config.audio_preset === "custom" && (
							<>
								<SettingRow
									label="High-Pass Filter"
									info="Remove low-frequency rumble (HVAC, traffic) below the cutoff frequency."
								>
									<Switch
										checked={config.noise_filter_highpass ?? true}
										onCheckedChange={(checked) =>
											updateConfig({ noise_filter_highpass: checked })
										}
										aria-label="High-Pass Filter"
									/>
								</SettingRow>
								<SettingRow
									label="High-Pass Cutoff"
									info="Frequencies below this are attenuated. 80Hz removes HVAC rumble. 100–150Hz also removes traffic."
								>
									<RangeSlider
										value={config.noise_filter_highpass_cutoff_hz ?? 80}
										min={20}
										max={500}
										step={10}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_highpass_cutoff_hz",
												v,
											)
										}
										ariaLabel="High-Pass Cutoff"
										suffix="Hz"
									/>
								</SettingRow>
								<SettingRow
									label="Noise Suppression"
									info="Neural network denoiser. RNNoise (default, lightweight). DeepFilterNet (premium, better quality, requires torch). Speex (lightest CPU)."
								>
									<Select
										value={config.noise_suppression_method ?? "rnnoise"}
										onValueChange={(v) =>
											updateConfig({
												noise_suppression_method:
													v as VoiceTyperConfig["noise_suppression_method"],
											})
										}
									>
										<SelectTrigger
											className="w-40"
											aria-label="Noise Suppression Method"
										>
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="rnnoise">RNNoise</SelectItem>
											<SelectItem value="deepfilternet">
												DeepFilterNet
											</SelectItem>
											<SelectItem value="speex">Speex</SelectItem>
											<SelectItem value="none">None</SelectItem>
										</SelectContent>
									</Select>
								</SettingRow>
								<SettingRow
									label="Noise Gate"
									info="Silence audio below a threshold to remove idle hiss. Uses OBS-style open/close thresholds with attack/hold/release."
								>
									<Switch
										checked={config.noise_filter_gate ?? true}
										onCheckedChange={(checked) =>
											updateConfig({ noise_filter_gate: checked })
										}
										aria-label="Noise Gate"
									/>
								</SettingRow>
								<SettingRow
									label="Gate Open Threshold"
									info="Level above which the gate opens (passes audio). -26dB is a good default for speech."
								>
									<RangeSlider
										value={config.noise_filter_gate_open_threshold_db ?? -26}
										min={-96}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_gate_open_threshold_db",
												v,
											)
										}
										ariaLabel="Gate Open Threshold"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Gate Close Threshold"
									info="Level below which the gate closes (attenuates audio). Should be 5-10dB below open threshold."
								>
									<RangeSlider
										value={config.noise_filter_gate_close_threshold_db ?? -32}
										min={-96}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_gate_close_threshold_db",
												v,
											)
										}
										ariaLabel="Gate Close Threshold"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Equalizer"
									info="3-band EQ: boost mid (speech intelligibility), cut low (rumble), slight high (presence). OBS-style crossover."
								>
									<Switch
										checked={config.noise_filter_eq ?? true}
										onCheckedChange={(checked) =>
											updateConfig({ noise_filter_eq: checked })
										}
										aria-label="Equalizer"
									/>
								</SettingRow>
								<SettingRow
									label="EQ — Low (bass)"
									info="Boost/cut below 800Hz. -3dB removes rumble and proximity effect."
								>
									<RangeSlider
										value={config.noise_filter_eq_low_db ?? -3}
										min={-20}
										max={20}
										step={1}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_eq_low_db", v)
										}
										ariaLabel="EQ Low"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="EQ — Mid (speech)"
									info="Boost/cut 800Hz–5kHz (speech intelligibility band). +3dB improves consonant clarity."
								>
									<RangeSlider
										value={config.noise_filter_eq_mid_db ?? 3}
										min={-20}
										max={20}
										step={1}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_eq_mid_db", v)
										}
										ariaLabel="EQ Mid"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="EQ — High (treble)"
									info="Boost/cut above 5kHz. +2dB adds presence and brightness."
								>
									<RangeSlider
										value={config.noise_filter_eq_high_db ?? 2}
										min={-20}
										max={20}
										step={1}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_eq_high_db", v)
										}
										ariaLabel="EQ High"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Compressor"
									info="Evens out loud/quiet speech for consistent ASR accuracy. OBS-style peak envelope with threshold/ratio/attack/release."
								>
									<Switch
										checked={config.noise_filter_compressor ?? true}
										onCheckedChange={(checked) =>
											updateConfig({ noise_filter_compressor: checked })
										}
										aria-label="Compressor"
									/>
								</SettingRow>
								<SettingRow
									label="Compressor Threshold"
									info="Level above which compression starts. -18dB is a good default for speech."
								>
									<RangeSlider
										value={config.noise_filter_compressor_threshold_db ?? -18}
										min={-60}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_compressor_threshold_db",
												v,
											)
										}
										ariaLabel="Compressor Threshold"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Compressor Ratio"
									info="How hard to compress. 3:1 is gentle. 10:1 is aggressive (limiter-like)."
								>
									<RangeSlider
										value={config.noise_filter_compressor_ratio ?? 3}
										min={1}
										max={32}
										step={0.5}
										onChange={(v) =>
											updateConfigDebounced("noise_filter_compressor_ratio", v)
										}
										ariaLabel="Compressor Ratio"
										suffix=":1"
									/>
								</SettingRow>
								<SettingRow
									label="Limiter"
									info="Brick-wall ceiling to prevent clipping. Catches transient clicks/pops before they reach ASR."
								>
									<Switch
										checked={config.noise_filter_limiter ?? true}
										onCheckedChange={(checked) =>
											updateConfig({ noise_filter_limiter: checked })
										}
										aria-label="Limiter"
									/>
								</SettingRow>
								<SettingRow
									label="Limiter Ceiling"
									info="Absolute maximum output level. -6dB prevents clipping while allowing headroom."
								>
									<RangeSlider
										value={config.noise_filter_limiter_ceiling_db ?? -6}
										min={-60}
										max={0}
										step={1}
										onChange={(v) =>
											updateConfigDebounced(
												"noise_filter_limiter_ceiling_db",
												v,
											)
										}
										ariaLabel="Limiter Ceiling"
										suffix="dB"
									/>
								</SettingRow>
								<SettingRow
									label="Notch Filter (hum)"
									info="Remove 50/60Hz electrical mains hum. Off by default — only enable if you hear a persistent low buzz."
								>
									<Switch
										checked={config.noise_filter_notch ?? false}
										onCheckedChange={(checked) =>
											updateConfig({ noise_filter_notch: checked })
										}
										aria-label="Notch Filter"
									/>
								</SettingRow>
							</>
						)}
					</div>
				</SettingsSection>

				{/* ── SECTION: Privacy & Consent ─────────────────────────── */}
				{/* NEW-PRIV-005/006/009: centralized consent management.
            All four consent flags live in the Python Config and are
            enforced by the backend (HuggingFace download refusal,
            CloudEngine ConsentRequiredError, etc.).  This section
            gives the user a single place to view and revoke any
            consent they've previously granted.  Initial grant
            happens contextually (HuggingFace banner on Models page,
            per-provider toggles on Models page) — this section is
            primarily for review/revocation. */}
				<SettingsSection
					title="Privacy & Consent"
					description="Review and revoke consent for data processing."
				>
					{/* HuggingFace consent */}
					<SettingRow
						label="HuggingFace model downloads"
						info="Allows downloading Whisper model weights from huggingface.co. Reveals your IP to a US-headquartered third party (Hugging Face, Inc.). Audio itself is never sent."
					>
						<Switch
							checked={config.huggingface_consent ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ huggingface_consent: checked })
							}
							aria-label="HuggingFace download consent"
						/>
					</SettingRow>

					{/* Voice biometric consent */}
					<SettingRow
						label="Voice biometric processing"
						info="Allows Voice Typer to process your voice recordings locally for transcription. Voice recordings may be considered biometric data under Illinois BIPA and GDPR Article 9. Voice Typer does not store raw audio after transcription — only the transcribed text is kept."
					>
						<Switch
							checked={config.voice_biometric_consent ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ voice_biometric_consent: checked })
							}
							aria-label="Voice biometric processing consent"
						/>
					</SettingRow>

					{/* Per-provider cloud ASR consent — mirrors Models page toggles */}
					<SettingRow
						label="OpenAI cloud ASR"
						info="Allows sending audio recordings to OpenAI's Whisper API for transcription. Only takes effect when OpenAI is the active ASR backend AND an API key is configured."
					>
						<Switch
							checked={config.cloud_openai_consent ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ cloud_openai_consent: checked })
							}
							aria-label="OpenAI cloud ASR consent"
						/>
					</SettingRow>
					<SettingRow
						label="Groq cloud ASR"
						info="Allows sending audio recordings to Groq's Whisper API for transcription. Only takes effect when Groq is the active ASR backend AND an API key is configured."
					>
						<Switch
							checked={config.cloud_groq_consent ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ cloud_groq_consent: checked })
							}
							aria-label="Groq cloud ASR consent"
						/>
					</SettingRow>
					<SettingRow
						label="Deepgram cloud ASR"
						info="Allows sending audio recordings to Deepgram's nova-2 API for transcription. Only takes effect when Deepgram is the active ASR backend AND an API key is configured."
					>
						<Switch
							checked={config.cloud_deepgram_consent ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ cloud_deepgram_consent: checked })
							}
							aria-label="Deepgram cloud ASR consent"
						/>
					</SettingRow>

					{/* LLM polish consent (existing field, surfaced here for completeness) */}
					<SettingRow
						label="LLM text polishing"
						info="Allows sending transcribed TEXT (not audio) to an OpenAI-compatible LLM API for polishing. Requires an LLM API key in the Post-Processing section."
					>
						<Switch
							checked={config.llm_polish_consent ?? false}
							onCheckedChange={(checked) =>
								updateConfig({ llm_polish_consent: checked })
							}
							aria-label="LLM polish consent"
						/>
					</SettingRow>

					{/* NEW-PRIV-007: GDPR right-to-export (Art. 15/20).
              Previously only history + vocabulary were exportable.
              Templates and config are also user data and must be
              exportable on request.  The handlers live in
              main/index.ts (templates:export, config:export) and
              are exposed via the preload bridge. */}
					<SettingRow
						label="Export all data (GDPR Art. 15/20)"
						info="Download your templates and full configuration as JSON files. API keys are redacted in the config export."
					>
						<div className="flex gap-2">
							<Button
								variant="outline"
								size="sm"
								onClick={async () => {
									try {
										const templates = await call("get_templates");
										const result = await (
											window.window_ as {
												exportTemplates?: (data: unknown) => Promise<{
													success: boolean;
													path?: string;
													error?: string;
												}>;
											}
										).exportTemplates?.(templates);
										if (result?.success) {
											showSnack(
												`Templates exported: ${result.path?.split(/[\\/]/).pop() ?? "file"}`,
												"success",
											);
										} else if (result?.error) {
											showSnack(`Export failed: ${result.error}`, "error");
										}
									} catch (err) {
										showSnack(`Export failed: ${err}`, "error");
									}
								}}
								aria-label="Export templates as JSON"
							>
								Export Templates
							</Button>
							<Button
								variant="outline"
								size="sm"
								onClick={async () => {
									try {
										const cfg = await call("get_config");
										const result = await (
											window.window_ as {
												exportConfig?: (data: unknown) => Promise<{
													success: boolean;
													path?: string;
													error?: string;
												}>;
											}
										).exportConfig?.(cfg);
										if (result?.success) {
											showSnack(
												`Config exported: ${result.path?.split(/[\\/]/).pop() ?? "file"}`,
												"success",
											);
										} else if (result?.error) {
											showSnack(`Export failed: ${result.error}`, "error");
										}
									} catch (err) {
										showSnack(`Export failed: ${err}`, "error");
									}
								}}
								aria-label="Export configuration as JSON"
							>
								Export Config
							</Button>
						</div>
					</SettingRow>
				</SettingsSection>

				{/* ── SECTION: Troubleshooting ──────────────────────────── */}
				{/* NEW-UX-025: previously only had "View Logs" (which lied —
            it opened the log FOLDER, not a log viewer) and "Reset to
            Defaults" (destructive, no undo).  We now also surface:
              - Diagnostics link (opens the About page's diagnostics
                section which has version, ASR backend, device, etc.)
              - Help / FAQ link
              - Report a Bug link
            And clarify the "View Logs" label. */}
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
