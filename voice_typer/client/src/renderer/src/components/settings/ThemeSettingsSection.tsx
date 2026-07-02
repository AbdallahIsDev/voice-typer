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

import { useCallback, useEffect, useRef, useState } from "react";
import { RangeSlider } from "@/components/RangeSlider";
import { SettingRow } from "@/components/SettingRow";
import { SettingsSection } from "@/components/SettingsSection";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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

const _THEME_OPTIONS = [
	{ value: "system", label: "System Default" },
	{ value: "light", label: "Light" },
	{ value: "dark", label: "Dark" },
] as const;

interface ThemeSettingsSectionProps extends SettingsSectionSharedProps {
	/** Theme mode provided by the App-level useTheme hook (overrides config while a save is in-flight). */
	themeModeProp?: VoiceTyperConfig["theme_mode"];
	/** App-level theme-change handler — persists the mode via the debounced save in useTheme. */
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
}

export function ThemeSettingsSection({
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
		applyThemeVars(savedPresetRef.current, isDark);
	}, []);

	if (!config) return null;

	// UX-028: section-level visibility check — if no row matches the
	// search filter, hide the entire section.
	const sectionItems = [
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
	];
	if (!sectionItems.some((item) => isVisible(item.label, item.info))) {
		return null;
	}

	return (
		<SettingsSection
			title="Appearance"
			description="Color scheme, theme preset, and text sizing."
		>
			{/* ── Color Scheme (light / dark / system) ───────────── */}
			{isVisible(
				"Color Scheme",
				"Switch between light, dark, or follow your system setting.",
			) && (
				<SettingRow
					label="Color Scheme"
					info="Switch between light, dark, or follow your system setting."
				>
					<Select
						value={themeModeProp ?? config.theme_mode}
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
			{isVisible(
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
											<span className="text-sm font-medium">{theme.name}</span>
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
			{isVisible(
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
											if (/^#[0-9a-fA-F]{0,6}$/.test(val) || val === "#") {
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
						})}
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
	);
}
