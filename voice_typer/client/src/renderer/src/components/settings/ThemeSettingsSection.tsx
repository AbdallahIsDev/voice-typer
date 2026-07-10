// ThemeSettingsSection — Appearance section of the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx to keep the page
// file under ~500 lines.  Renders the "Appearance" SettingsSection
// (Color Scheme, Theme Preset, Custom Theme color picker, Text Size).
//
// Behaviour is identical to the previous monolithic implementation:
// - Hover-preview of built-in presets (reverts on close/leave).
// - Custom-theme color picker with light/dark tabs and a 6-color grid.
// - localStorage draft backup so unsaved colour picks survive a crash.
// - 300 ms debounced save of the custom-theme draft.
// - Per-row search-filter visibility via the `isVisible` prop.
//
// All theme-only helpers (cssColorToHex, getCurrentThemeColors, the
// localStorage draft helpers) live here because no other section uses
// them.

import {
	ModernTvIcon,
	Moon02Icon,
	Sun01Icon,
} from "@hugeicons/core-free-icons";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RangeSlider } from "@/components/common/RangeSlider";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { t } from "@/i18n/i18n";
import {
	applyThemeVars,
	CUSTOM_COLOR_KEYS,
	CUSTOM_THEME_ID,
	type CustomThemeData,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	deriveCustomVars,
	THEMES,
} from "@/themes";
import type { VoiceTyperConfig } from "@/types/config";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

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

/** Convert any CSS color value to #rrggbb hex using a hidden DOM element.
 *  Uses getComputedStyle(backgroundColor) which reliably resolves oklch(),
 *  hsl(), rgb(), named colors, etc. to an rgba() string that the browser
 *  engine can compute, unlike the canvas 2d context which may fail on
 *  oklch() values in some Electron/Chromium versions.
 *
 *  Falls back to a manual oklch→sRGB→hex converter when the DOM approach
 *  fails or returns transparent black (indicating the browser couldn't
 *  parse the color).  This ensures the custom theme editor always receives
 *  valid hex values regardless of Chromium version.
 */
function cssColorToHex(color: string): string {
	if (!color) return "#000000";

	// Already a clean hex colour — normalise and return.
	const hexMatch = color.match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
	if (hexMatch) {
		const hex = hexMatch[1].toLowerCase();
		if (hex.length === 3) {
			return `#${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}`;
		}
		return `#${hex}`;
	}

	// Attempt 1: DOM-based resolution (works in modern browsers)
	const domHex = _cssColorToHexViaDOM(color);
	if (domHex && domHex !== "#000000") return domHex;

	// Attempt 2: Manual oklch() → sRGB → hex parser (works everywhere)
	const oklchHex = _cssColorToHexViaOklch(color);
	if (oklchHex) return oklchHex;

	return "#000000";
}

/** Try resolving a CSS color via a hidden DOM element. */
function _cssColorToHexViaDOM(color: string): string | null {
	try {
		const temp = document.createElement("div");
		temp.style.backgroundColor = color;
		temp.style.position = "absolute";
		temp.style.left = "-9999px";
		temp.style.width = "1px";
		temp.style.height = "1px";
		document.body.appendChild(temp);
		const computed = getComputedStyle(temp).backgroundColor;
		document.body.removeChild(temp);

		const match = computed.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
		if (match) {
			return (
				"#" +
				[1, 2, 3]
					.map((i) =>
						Math.round(Number(match[i])).toString(16).padStart(2, "0"),
					)
					.join("")
			);
		}
	} catch {
		// Fall through to next attempt
	}
	return null;
}

/**
 * Manual oklch() to sRGB hex converter.
 * Parses "oklch(L C H)" and "oklch(L C H / alpha)" formats,
 * converts OKLCH → OKLab → linear sRGB via the LMS cube-root
 * approach (Björn Ottosson's method), applies sRGB gamma, and
 * returns a #rrggbb hex string.
 */
function _cssColorToHexViaOklch(color: string): string | null {
	const match = color.match(/oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/i);
	if (!match) return null;

	const L = Number(match[1]);
	const C = Number(match[2]);
	const H = (Number(match[3]) * Math.PI) / 180;

	// OKLCH → OKLab
	const a = C * Math.cos(H);
	const b = C * Math.sin(H);

	// OKLab → linear LMS (cube root domain → linear via cube)
	const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
	const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
	const s_ = L - 0.0894841775 * a - 1.291485548 * b;

	const l = l_ * l_ * l_;
	const m = m_ * m_ * m_;
	const s = s_ * s_ * s_;

	// LMS → linear sRGB (inverse of sRGB→LMS OKLab matrix)
	let r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
	let g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
	let bl = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;

	// Apply sRGB gamma
	r = _srgbGamma(r);
	g = _srgbGamma(g);
	bl = _srgbGamma(bl);

	return (
		"#" +
		[r, g, bl]
			.map((c) =>
				Math.round(c * 255)
					.toString(16)
					.padStart(2, "0"),
			)
			.join("")
	);
}

function _srgbGamma(c: number): number {
	c = Math.min(1, Math.max(0, c));
	if (c <= 0.0031308) return 12.92 * c;
	return 1.055 * c ** (1 / 2.4) - 0.055;
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
// PERF: module-level cache for getCurrentThemeColors results, keyed by
// preset ID.  The DOM-reading path (for 'default' and 'custom' presets)
// does 2× getComputedStyle calls + N cssColorToHex conversions per call,
// which is expensive.  Since the preset's colors don't change between
// renders (they only change when the user picks a different preset or
// edits a custom color), we cache the result and invalidate it when
// the custom draft changes.
//
// The cache is defined in a separate file (themeColorCache.ts) to avoid
// the react-refresh/only-export-components lint warning that would occur
// if we exported both the cache and the component from the same file.
import { _themeColorCache } from "./themeColorCache";

function getCurrentThemeColors(currentPresetId: string): {
	light: Record<string, string>;
	dark: Record<string, string>;
} {
	const cached = _themeColorCache.get(currentPresetId);
	if (cached) return cached;

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
			const result = { light, dark };
			_themeColorCache.set(currentPresetId, result);
			return result;
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
	const result = { light, dark };
	_themeColorCache.set(currentPresetId, result);
	return result;
}

/**
 * Compute the { background, foreground } pair shown inside the square
 * preview next to each theme in the dropdown and in the trigger.
 *
 * The square background uses the theme's **primary/accent** colour so
 * the user immediately sees the dominant accent of each preset.  The
 * "A" letter is rendered in the theme's **foreground/text** colour so
 * it stays readable against the primary background.
 *
 * For built-in presets the values come straight from the theme's light /
 * dark var maps.  For 'default' (which has empty var maps) we fall back
 * to the theme's ``swatch`` field (the primary blue from the stylesheet).
 * For 'custom', we use the primary colour from the user's draft.
 */
function getThemePreviewColors(
	themeId: string,
	isDark: boolean,
	customDraft: CustomThemeData | null,
): { bg: string; fg: string } {
	// Custom theme — use the primary/accent colour from the custom draft.
	if (themeId === "custom" && customDraft) {
		const vars = isDark ? customDraft.dark : customDraft.light;
		return {
			bg: vars["--primary"] ?? (isDark ? "#6b7fd4" : "#5469d4"),
			fg: vars["--foreground"] ?? (isDark ? "#ededed" : "#0a0a0a"),
		};
	}
	// Default preset — no CSS var overrides, use the swatch as primary.
	if (themeId === "default") {
		const defaultTheme = THEMES[0];
		const swatch = defaultTheme?.swatch ?? "oklch(0.488 0.243 264.376)";
		const fgSwatch = isDark ? "oklch(0.985 0 0)" : "oklch(0.141 0.005 285.823)";
		return { bg: swatch, fg: fgSwatch };
	}
	const theme = THEMES.find((t) => t.id === themeId);
	if (!theme) {
		return { bg: "#5469d4", fg: "#000000" };
	}
	const vars = isDark ? theme.dark : theme.light;
	return {
		bg: vars["--primary"] ?? (isDark ? "#6b7fd4" : "#5469d4"),
		fg: vars["--foreground"] ?? (isDark ? "#ededed" : "#0a0a0a"),
	};
}

// IMPL-C: option value → icon + aria-label for the icon-only theme toggle.
// The visible label is empty — only the icon is shown, matching the
// ThemeSwitch in the sidebar.  The aria-label on the radiogroup and the
// title on each option provide screen-reader context.
const _THEME_OPTION_KEYS = [
	{
		value: "system",
		icon: ModernTvIcon,
		labelKey: "settings.appearance.systemDefault",
	},
	{ value: "light", icon: Sun01Icon, labelKey: "settings.appearance.light" },
	{ value: "dark", icon: Moon02Icon, labelKey: "settings.appearance.dark" },
] as const;

interface ThemeSettingsSectionProps extends SettingsSectionSharedProps {
	/** Theme mode provided by the App-level useTheme hook (overrides config while a save is in-flight). */
	themeModeProp?: VoiceTyperConfig["theme_mode"];
	/** App-level theme-change handler — persists the mode via the debounced save in useTheme. */
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
}

export const ThemeSettingsSection = memo(function ThemeSettingsSection({
	config,
	updateConfig,
	updateConfigDebounced,
	isVisible,
	themeModeProp,
	onThemeChange,
}: ThemeSettingsSectionProps) {
	// Track the last saved theme preset so hover previews can revert
	// to the user's saved choice (not the initial default) if they
	// hover without clicking.
	const savedPresetRef = useRef(config?.theme_preset ?? "default");
	if (config) savedPresetRef.current = config.theme_preset ?? "default";
	// Task ID 7 / Part C3: track whether the user has actually moved the
	// mouse inside the dropdown content.  Radix Select mounts the content
	// portal directly under the cursor when the dropdown opens, which can
	// fire a spurious `onMouseEnter` on the first item — applying that
	// item's theme as a "hover preview" even though the user hasn't
	// interacted.  We only honour `onMouseEnter` after the first real
	// `onMouseMove` inside the content, so opening + closing the dropdown
	// without moving the mouse leaves the applied theme untouched.
	const userHoveredRef = useRef(false);
	// ESC-THEME-FIX-003: ref mirror of ``customDraft`` so the stable
	// ``revertToSavedPreset`` callback (empty deps) can re-apply custom
	// vars when the saved preset is ``CUSTOM_THEME_ID`` without needing
	// ``customDraft`` as a dependency (which would make the callback
	// unstable and churn the mouseleave listener on every color edit).
	// ESC-THEME-FIX-003-TDZ: initialized with ``null`` instead of
	// ``customDraft`` because the ``customDraft`` ``const`` is declared
	// later (via ``useState``) and is in the temporal dead zone here.
	// The tracking effect below updates the ref with the real value
	// after every render.
	const customDraftRef = useRef<CustomThemeData | null>(null);
	useEffect(() => {
		customDraftRef.current = customDraft;
	});
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

	// ── Custom theme editor state ───────────────────────────────────
	// Task ID 7 / Part C4: initial tab matches the user's current dark/light
	// mode so the editor opens on the tab the user is most likely to edit
	// (was always "light" — Dark Mode users had to switch tabs manually).
	const [customEditorMode, setCustomEditorMode] = useState<"light" | "dark">(
		() =>
			typeof document !== "undefined" &&
			document.documentElement.classList.contains("dark")
				? "dark"
				: "light",
	);
	const [customDraft, setCustomDraft] = useState<CustomThemeData | null>(null);
	const customThemeInitRef = useRef(false);

	// Part C6: ``customDraftIsDefault`` is true when the draft matches the
	// built-in DEFAULT_CUSTOM_LIGHT / DEFAULT_CUSTOM_DARK maps.  The Reset
	// button is disabled in that state — re-enabled the moment the user
	// edits any colour.  Compared by JSON.stringify on the 6 core vars
	// (DEFAULT_CUSTOM_* only contain those 6 keys, so this is exact).
	const customDraftIsDefault = useMemo(() => {
		if (!customDraft) return false;
		const lightKeys = Object.keys(customDraft.light).sort();
		const darkKeys = Object.keys(customDraft.dark).sort();
		const defaultKeys = Object.keys(DEFAULT_CUSTOM_LIGHT).sort();
		if (
			lightKeys.length !== defaultKeys.length ||
			darkKeys.length !== defaultKeys.length
		) {
			return false;
		}
		for (const k of defaultKeys) {
			if (customDraft.light[k] !== DEFAULT_CUSTOM_LIGHT[k]) return false;
			if (customDraft.dark[k] !== DEFAULT_CUSTOM_DARK[k]) return false;
		}
		return true;
	}, [customDraft]);

	// One-time init during render (not in effect) — avoids extra render
	// with stale null. Prefers localStorage draft over config value.
	if (config && !customThemeInitRef.current) {
		customThemeInitRef.current = true;
		const draft = _loadDraftFromLS();
		if (draft) {
			setCustomDraft(draft);
		} else if (config.custom_theme) {
			setCustomDraft(config.custom_theme);
		} else {
			setCustomDraft({
				light: { ...DEFAULT_CUSTOM_LIGHT },
				dark: { ...DEFAULT_CUSTOM_DARK },
			});
		}
	}

	// PERF: clear the color cache on unmount so stale entries don't
	// persist across page navigations. The cache is module-level (shared
	// across all instances), so this ensures the next time the user
	// visits Settings, colors are re-read from the DOM/THEMES array
	// rather than using potentially stale cached values.
	useEffect(() => {
		return () => {
			_themeColorCache.clear();
		};
	}, []);

	const _handleThemeChange = (mode: string) => {
		const m = mode as VoiceTyperConfig["theme_mode"];
		// App-level handler owns the save + state update.
		onThemeChange?.(m);
	};

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

				_themeColorCache.delete("custom");
				_themeColorCache.delete("default");

				// Persist to localStorage immediately (before the backend
				// save completes) so the draft survives a crash or disconnect.
				_saveDraftToLS(updated);

				// Debounced save to backend
				updateConfigDebounced("custom_theme", updated, 300);

				return updated;
			});
		},
		[updateConfigDebounced],
	);

	// ── Theme preset hover preview ─────────────────────────────────
	const applyHoverPreview = useCallback((presetId: string) => {
		const isDark = document.documentElement.classList.contains("dark");
		applyThemeVars(presetId, isDark);
	}, []);

	const revertToSavedPreset = useCallback(() => {
		const isDark = document.documentElement.classList.contains("dark");
		const preset = savedPresetRef.current;
		if (preset === CUSTOM_THEME_ID) {
			// ESC-THEME-FIX-003: ``applyThemeVars("custom", isDark)``
			// without a third argument clears all custom vars from the DOM
			// (via ``clearThemeVars()``) but then can't re-apply them because
			// the ``custom`` ThemePreset has empty light/dark var maps — the
			// user's actual colors live in the config/customDraft.  Derive
			// the full var set from the draft so the revert actually restores
			// the saved custom theme.
			const draft = customDraftRef.current;
			if (draft) {
				const modeVars = isDark ? draft.dark : draft.light;
				const derived = deriveCustomVars(modeVars, isDark);
				applyThemeVars(CUSTOM_THEME_ID, isDark, derived);
			} else {
				// No draft available — fall back to default (no vars to apply).
				applyThemeVars("default", isDark);
			}
		} else {
			applyThemeVars(preset, isDark);
		}
	}, []);

	if (!config) return <SettingsSkeleton rows={3} />;

	// IMPL-C: resolve i18n keys once per render so the isVisible predicate
	// and the rendered output share the same translated strings.
	const colorSchemeLabel = t("settings.appearance.colorScheme");
	const colorSchemeInfoSearch = t("settings.appearance.colorSchemeInfo");
	const themePresetLabel = t("settings.appearance.themePreset");
	const themePresetInfoSearch = t("settings.appearance.themePresetInfo");
	const customThemeLabel = t("settings.appearance.customTheme");
	const customThemeInfoSearch = t("settings.appearance.customThemeInfo");
	const textSizeLabel = t("settings.appearance.textSize");
	const textSizeInfoSearch = t("settings.appearance.textSizeInfo");
	const themeOptions = _THEME_OPTION_KEYS.map((opt) => ({
		value: opt.value,
		// Icon-only toggle — no visible text, matching the sidebar ThemeSwitch.
		label: "",
		icon: opt.icon,
		title: t(opt.labelKey),
	}));

	// FIX (Task ID 6 / Settings Search): capture the section title in a
	// local constant so the SAME value feeds both the
	// ``<SettingsSection title="…">`` prop AND the ``isVisible(...)``
	// predicate's new third argument. This lets the search match the
	// section heading (e.g. typing "appearance" surfaces every row in
	// this section even if the row's own label/info don't contain it).
	const sectionTitle = t("settings.appearance.title");

	// UX-028: section-level visibility check — if no row matches the
	// search filter, hide the entire section.
	const sectionItems = [
		{ label: colorSchemeLabel, info: colorSchemeInfoSearch },
		{ label: themePresetLabel, info: themePresetInfoSearch },
		{ label: customThemeLabel, info: customThemeInfoSearch },
		{ label: textSizeLabel, info: textSizeInfoSearch },
	];
	if (
		!sectionItems.some((item) => isVisible(item.label, item.info, sectionTitle))
	) {
		return null;
	}

	// ── Inline handler extraction ─────────────────────────────────
	const handleColorSchemeChange = (v: string) => {
		const m = v as VoiceTyperConfig["theme_mode"];
		_handleThemeChange(m);
	};
	const handleThemePresetChange = (v: string) => {
		const preset = v as VoiceTyperConfig["theme_preset"];
		savedPresetRef.current = preset;
		updateConfig({ theme_preset: preset });
	};
	const handleSelectOpenChange = (open: boolean) => {
		if (open) {
			userHoveredRef.current = false;
		} else {
			revertToSavedPreset();
		}
	};
	const handleThemeHover = (themeId: string) => () => {
		if (userHoveredRef.current) {
			applyHoverPreview(themeId);
		}
	};
	const handleCustomThemeToggle = (enabled: boolean) => {
		if (enabled) {
			const isDarkOn = document.documentElement.classList.contains("dark");
			setCustomEditorMode(isDarkOn ? "dark" : "light");
			const currentColors = getCurrentThemeColors(
				config.theme_preset ?? "default",
			);
			setCustomDraft(currentColors);
			_saveDraftToLS(currentColors);
			updateConfig({ theme_preset: "custom", custom_theme: currentColors });
		} else {
			const fallback = lastNonCustomRef.current ?? "default";
			savedPresetRef.current = fallback;
			_clearDraftLS();
			updateConfig({ theme_preset: fallback });
		}
	};
	const handleSetLightMode = () => setCustomEditorMode("light");
	const handleSetDarkMode = () => setCustomEditorMode("dark");
	const handleColorInputChange =
		(varName: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
			handleCustomColorChange(customEditorMode, varName, e.target.value);
	const handleHexInputChange =
		(varName: string) => (e: React.ChangeEvent<HTMLInputElement>) => {
			const val = e.target.value;
			if (/^#[0-9a-fA-F]{0,6}$/.test(val) || val === "#") {
				handleCustomColorChange(customEditorMode, varName, val || "#000000");
			}
		};
	const handleResetCustomColors = () => {
		const defaults: CustomThemeData = {
			light: { ...DEFAULT_CUSTOM_LIGHT },
			dark: { ...DEFAULT_CUSTOM_DARK },
		};
		setCustomDraft(defaults);
		_saveDraftToLS(defaults);
		const isDark = document.documentElement.classList.contains("dark");
		const modeVars = isDark ? defaults.dark : defaults.light;
		const derived = deriveCustomVars(modeVars, isDark);
		applyThemeVars("custom", isDark, derived);
		_themeColorCache.delete("custom");
		_themeColorCache.delete("default");
		updateConfig({ custom_theme: defaults });
	};
	const handleTextSizeChange = (v: number) => updateConfig({ text_size: v });
	const handleSelectMouseMove = () => {
		userHoveredRef.current = true;
	};

	return (
		<SettingsSection
			title={sectionTitle}
			description={t("settings.appearance.description")}
		>
			{/* ── Color Scheme (light / dark / system) ───────────── */}
			{isVisible(colorSchemeLabel, colorSchemeInfoSearch, sectionTitle) && (
				<SettingRow
					label={colorSchemeLabel}
					info={t("settings.appearance.colorSchemeInfo")}
				>
					<SegmentedControl
						options={themeOptions}
						value={themeModeProp ?? config.theme_mode}
						onChange={handleColorSchemeChange}
						ariaLabel={t("settings.appearance.colorSchemeAria")}
					/>
				</SettingRow>
			)}

			{/* ── Theme Preset selector with hover preview ──────── */}
			{isVisible(themePresetLabel, themePresetInfoSearch, sectionTitle) && (
				<SettingRow
					label={themePresetLabel}
					info={t("settings.appearance.themePresetInfoRendered")}
				>
					<Select
						value={config.theme_preset ?? "default"}
						onValueChange={handleThemePresetChange}
						onOpenChange={handleSelectOpenChange}
					>
						<SelectTrigger
							className="w-44"
							aria-label={t("settings.appearance.themePresetAria")}
						>
							{(() => {
								const currentId = config.theme_preset ?? "default";
								const current =
									THEMES.find((t) => t.id === currentId) ?? THEMES[0];
								// Part C1/C2: rounded-rectangle "E" preview.  For the
								// custom theme, use the actual custom colours from the
								// in-memory draft instead of the static placeholder swatch.
								const isDark =
									document.documentElement.classList.contains("dark");
								const { bg, fg } = getThemePreviewColors(
									currentId,
									isDark,
									customDraft,
								);
								return (
									<span className="flex items-center gap-2">
										<span
											className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold"
											style={{ backgroundColor: bg, color: fg }}
										>
											A
										</span>
										<span>{current.name}</span>
									</span>
								);
							})()}
						</SelectTrigger>
						<SelectContent
							position="popper"
							align="start"
							onMouseMove={handleSelectMouseMove}
							onMouseLeave={revertToSavedPreset}
						>
							{/* Filter out 'custom' from the dropdown — custom is now
                                                                a toggle switch below this row. */}
							{THEMES.filter((t) => t.id !== "custom").map((theme) => {
								const isDark =
									document.documentElement.classList.contains("dark");
								const { bg, fg } = getThemePreviewColors(
									theme.id,
									isDark,
									customDraft,
								);
								return (
									<SelectItem
										key={theme.id}
										value={theme.id}
										onMouseEnter={handleThemeHover(theme.id)}
									>
										<span className="flex items-center gap-2.5">
											<span
												className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-semibold"
												style={{ backgroundColor: bg, color: fg }}
											>
												A
											</span>
											<span className="text-sm font-medium">{theme.name}</span>
										</span>
									</SelectItem>
								);
							})}
						</SelectContent>
					</Select>
				</SettingRow>
			)}

			{/* ── Custom Theme toggle ────────────────────────────── */}
			{/* A switch that enables/disables the custom color editor.
                                When ON, theme_preset is forced to 'custom' and the color
                                picker appears.  When OFF, the preset reverts to the
                                previously-selected preset. */}
			{isVisible(customThemeLabel, customThemeInfoSearch, sectionTitle) && (
				<SettingRow
					label={customThemeLabel}
					info={t("settings.appearance.customThemeInfoRendered")}
				>
					<Switch
						checked={config.theme_preset === "custom"}
						onCheckedChange={handleCustomThemeToggle}
						aria-label={t("settings.appearance.customThemeAria")}
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
							onClick={handleSetLightMode}
							className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
								customEditorMode === "light"
									? "bg-(--bg) text-(--text-primary) shadow-xs"
									: "text-(--text-muted) hover:text-(--text-primary)"
							}`}
						>
							{t("settings.appearance.light")}
						</button>
						<button
							type="button"
							onClick={handleSetDarkMode}
							className={`flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
								customEditorMode === "dark"
									? "bg-(--bg) text-(--text-primary) shadow-xs"
									: "text-(--text-muted) hover:text-(--text-primary)"
							}`}
						>
							{t("settings.appearance.dark")}
						</button>
					</div>

					{/* Color swatch grid — 6 core colors */}
					<div className="grid grid-cols-2 gap-2.5">
						{CUSTOM_COLOR_KEYS.map(({ var: varName, label, description }) => {
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
										<Input
											type="color"
											value={currentHex}
											onChange={handleColorInputChange(varName)}
											className="absolute inset-0 h-full w-full cursor-pointer opacity-0 p-0 border-none"
											aria-label={t("settings.appearance.colorAria", { label })}
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
									<Input
										type="text"
										value={currentHex}
										onChange={handleHexInputChange(varName)}
										className="w-18 shrink-0 text-center text-[11px] font-mono text-(--text-primary)"
										spellCheck={false}
										aria-label={t("settings.appearance.hexValueAria", {
											label,
										})}
									/>
								</div>
							);
						})}
					</div>

					{/* Reset to defaults.
                                                Part C5: the previously-broken "#888" 3-digit hex in
                                                DEFAULT_CUSTOM_DARK["--text-muted"] is now "#888888" (6-digit)
                                                so the validator accepts the payload — no more "Failed to
                                                save settings" toast.
                                                Part C6: button is disabled while the draft already matches
                                                the defaults (re-enables the moment the user edits a color). */}
					<button
						type="button"
						disabled={customDraftIsDefault}
						onClick={handleResetCustomColors}
						className="mt-3 w-full rounded-lg border border-border px-3 py-2 text-xs text-(--text-muted) transition-colors hover:bg-(--surface-hover) hover:text-(--text-primary) disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-(--text-muted)"
					>
						{t("settings.appearance.resetToDefaultColors")}
					</button>
				</div>
			)}

			{/* ── Text Size ──────────────────────────────────────── */}
			{isVisible(textSizeLabel, textSizeInfoSearch, sectionTitle) && (
				<SettingRow
					label={textSizeLabel}
					info={t("settings.appearance.textSizeInfoRendered")}
				>
					<div className="flex items-center gap-3 w-44">
						<RangeSlider
							value={config.text_size ?? 14}
							onChange={handleTextSizeChange}
							min={10}
							max={20}
							step={1}
							ariaLabel={t("settings.appearance.textSizeAria")}
							suffix="px"
							// UX (Task ID 5): defer the IPC write until the user releases
							// the slider thumb.  Without this, each pixel of drag fires
							// a separate updateConfig({ text_size }) IPC call, flooding
							// the backend with set_config writes and re-rendering the
							// entire UI on every step (the --font-scale CSS var
							// propagates to rem-based sizes).
							deferApply
						/>
					</div>
				</SettingRow>
			)}
		</SettingsSection>
	);
});
