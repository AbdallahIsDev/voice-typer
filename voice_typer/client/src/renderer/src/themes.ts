/**
 * Built-in theme presets for Voice Typer.
 *
 * Each preset defines CSS variable overrides for **both** light and dark
 * colour-scheme variants.  When a preset is active, the variables are
 * applied to ``document.documentElement`` via ``style.setProperty()``,
 * layered on top of the app's default CSS (index.css) and the current
 * ``.dark`` / ``:root`` class.
 *
 * The ``default`` preset is a no-op — it means "use whatever is in the
 * stylesheet" (no overrides needed).
 *
 * PERF-001: presets split into ./themes/ for lazy loading. Each preset
 * now lives in its own file under ``./themes/``; this module re-exports
 * the aggregated ``THEMES`` array and ``THEME_PRESETS`` record so
 * existing consumers can keep importing from ``@/themes`` unchanged.
 */

import { contrastRatio } from "@/lib/color-utils";
import { THEME_PRESETS, THEMES } from "./themes/index";

export interface ThemePreset {
	/** Unique identifier stored in config (e.g. ``"amoled"``). */
	id: string;
	/** Human-readable label shown in the Settings dropdown. */
	name: string;
	/**
	 * i18n key for the localised preset name (e.g. ``"theme.preset.amoled"``).
	 *
	 * BG-5: populated by the ``themes/index.ts`` aggregator for every
	 * built-in preset. Consumers (``ThemeSettingsSection.tsx``) render
	 * ``t(preset.nameKey)`` for the visible label, falling back to
	 * ``preset.name`` when the key is missing.
	 */
	nameKey: string;
	/** A CSS colour value used as a preview swatch in the dropdown. */
	swatch: string;
	/** CSS variable overrides for light mode (``:root``). */
	light: Record<string, string>;
	/** CSS variable overrides for dark mode (``.dark`` selector). */
	dark: Record<string, string>;
}

/** Identifier used for the user-customised theme preset. */
export const CUSTOM_THEME_ID = "custom";

/** Shape of user-defined custom theme data stored in config. */
export interface CustomThemeData {
	light: Record<string, string>;
	dark: Record<string, string>;
}

/**
 * The set of CSS custom properties that themes are allowed to override.
 * Keeping a central list makes it easy to clear old overrides when
 * switching presets or reverting to default.
 */
export const THEME_VARIABLES: readonly string[] = [
	// Core background / foreground
	"--background",
	"--foreground",
	"--bg-subtle",
	"--surface-hover",
	"--surface-page",

	// Text (BG-80: --text-muted removed — aliased to --muted-foreground
	// in index.css. Only --muted-foreground is the canonical token now.)
	"--text-primary",
	"--text-secondary",

	// Cards, popovers, dialogs
	"--card",
	"--card-foreground",
	"--popover",
	"--popover-foreground",

	// Primary / accent
	"--primary",
	"--primary-foreground",
	"--accent",
	"--accent-foreground",
	"--accent-soft",
	"--accent-muted",

	// Secondary / muted
	"--secondary",
	"--secondary-foreground",
	"--muted",
	"--muted-foreground",

	// Borders / inputs / rings
	"--border",
	"--input",
	"--ring",

	// Destructive
	"--destructive",
	"--destructive-foreground",

	// Sidebar
	"--sidebar",
	"--sidebar-foreground",
	"--sidebar-primary",
	"--sidebar-primary-foreground",
	"--sidebar-accent",
	"--sidebar-accent-foreground",
	"--sidebar-border",
	"--sidebar-ring",

	// Charts (kept in sync with default palette)
	"--chart-1",
	"--chart-2",
	"--chart-3",
	"--chart-4",
	"--chart-5",

	// Scrollbar
	"--scrollbar-thumb",
	"--scrollbar-thumb-hover",
];

// ─── Helper: apply a theme preset to the document ──────────────────────

/**
 * Apply the CSS variable overrides for the given preset and colour scheme.
 * Clears any previously-applied theme overrides first.
 *
 * @param presetId    The theme preset id (or ``"default"`` to clear overrides).
 * @param isDark      Whether the dark-mode variant should be used.
 * @param customVars  Optional custom-theme variable map for the
 *                    current mode (only used when ``presetId === 'custom'``).
 */
export function applyThemeVars(
	presetId: string,
	isDark: boolean,
	customVars?: Record<string, string> | null,
): void {
	const root = document.documentElement;

	// Always clear previous overrides first
	clearThemeVars();

	if (presetId === "default") return;

	// Custom theme — use the passed-in variable map directly
	if (presetId === CUSTOM_THEME_ID && customVars) {
		for (const [key, value] of Object.entries(customVars)) {
			// Only set variables that are in our known list
			if ((THEME_VARIABLES as readonly string[]).includes(key)) {
				root.style.setProperty(key, value);
			}
		}
		return;
	}

	const theme = THEMES.find((t) => t.id === presetId);
	if (!theme) return;

	const vars = isDark ? theme.dark : theme.light;
	for (const [key, value] of Object.entries(vars)) {
		root.style.setProperty(key, value);
	}
}

/**
 * Remove all theme CSS variable overrides from the document element,
 * reverting to whatever the stylesheet defines.
 */
export function clearThemeVars(): void {
	const root = document.documentElement;
	for (const key of THEME_VARIABLES) {
		root.style.removeProperty(key);
	}
}

// ─── Theme presets ──────────────────────────────────────────────────────
// PERF-001: presets split into ./themes/ for lazy loading.
// Each preset now lives in its own file under ./themes/<preset>.ts and is
// aggregated by ./themes/index.ts. The re-exports below preserve the
// pre-refactor public API: consumers can keep importing `THEMES` (ordered
// array) or use the new `THEME_PRESETS` record for O(1) id → preset
// lookups. Callers that want to lazy-load a single preset can dynamically
// `import()` the individual file (e.g. `await import("./themes/amoled")`).
export { THEME_PRESETS, THEMES };

/** Look up a theme preset by id. Returns the default theme if not found. */
export function getThemeById(id: string): ThemePreset {
	return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

/**
 * Core CSS variables exposed in the custom-theme colour picker.
 * Other variables (card, popover, sidebar, chart, scrollbar) are
 * auto-derived from these core values.
 */
/**
 * BG-18: each entry now carries ``labelKey`` / ``descriptionKey`` — i18n
 * keys resolved via ``t()`` in ``ThemeSettingsSection.tsx``. The legacy
 * ``label`` / ``description`` English strings are kept as fallbacks for
 * the rare caller that reads them outside a React context (e.g. tests).
 */
export const CUSTOM_COLOR_KEYS: {
	var: string;
	label: string;
	description: string;
	labelKey: string;
	descriptionKey: string;
}[] = [
	{
		var: "--background",
		label: "Background",
		description: "Main page and app background",
		labelKey: "settings.appearance.colorLabel.background",
		descriptionKey: "settings.appearance.colorDescription.background",
	},
	{
		var: "--foreground",
		label: "Text",
		description: "Primary text colour",
		labelKey: "settings.appearance.colorLabel.foreground",
		descriptionKey: "settings.appearance.colorDescription.foreground",
	},
	{
		var: "--primary",
		label: "Accent",
		description: "Primary accent / highlight colour",
		labelKey: "settings.appearance.colorLabel.primary",
		descriptionKey: "settings.appearance.colorDescription.primary",
	},
	{
		var: "--bg-subtle",
		label: "Surface",
		description: "Card / sidebar / secondary background",
		labelKey: "settings.appearance.colorLabel.bg-subtle",
		descriptionKey: "settings.appearance.colorDescription.bg-subtle",
	},
	{
		var: "--border",
		label: "Border",
		description: "Lines, dividers, and input borders",
		labelKey: "settings.appearance.colorLabel.border",
		descriptionKey: "settings.appearance.colorDescription.border",
	},
	{
		var: "--text-muted",
		label: "Muted Text",
		description: "Secondary / dimmed text colour",
		labelKey: "settings.appearance.colorLabel.text-muted",
		descriptionKey: "settings.appearance.colorDescription.text-muted",
	},
];

// ─── Default values for each custom colour key ────────────────────────
// These are the "reset" values for the stock light/dark mode.

export const DEFAULT_CUSTOM_LIGHT: Record<string, string> = {
	"--background": "#ffffff",
	"--foreground": "#09090b",
	"--primary": "#1447e6",
	"--bg-subtle": "#f5f5f5",
	"--border": "#e4e4e7",
	"--text-muted": "#71717b",
};

export const DEFAULT_CUSTOM_DARK: Record<string, string> = {
	"--background": "#131313",
	"--foreground": "#fafafa",
	"--primary": "#193cb8",
	"--bg-subtle": "#0f0f0f",
	"--border": "#1f1f1f",
	"--text-muted": "#9f9fa9",
};

/** Build a full set of CSS var overrides from the 6 core custom colours. */
export function deriveCustomVars(
	core: Record<string, string>,
	isDark: boolean,
): Record<string, string> {
	const bg =
		core["--background"] ??
		(isDark
			? DEFAULT_CUSTOM_DARK["--background"]
			: DEFAULT_CUSTOM_LIGHT["--background"]);
	const fg =
		core["--foreground"] ??
		(isDark
			? DEFAULT_CUSTOM_DARK["--foreground"]
			: DEFAULT_CUSTOM_LIGHT["--foreground"]);
	const primary =
		core["--primary"] ??
		(isDark
			? DEFAULT_CUSTOM_DARK["--primary"]
			: DEFAULT_CUSTOM_LIGHT["--primary"]);
	const subtle =
		core["--bg-subtle"] ??
		(isDark
			? DEFAULT_CUSTOM_DARK["--bg-subtle"]
			: DEFAULT_CUSTOM_LIGHT["--bg-subtle"]);
	const border =
		core["--border"] ??
		(isDark
			? DEFAULT_CUSTOM_DARK["--border"]
			: DEFAULT_CUSTOM_LIGHT["--border"]);
	const muted =
		core["--text-muted"] ??
		(isDark
			? DEFAULT_CUSTOM_DARK["--text-muted"]
			: DEFAULT_CUSTOM_LIGHT["--text-muted"]);

	const destructive = isDark ? "#ef4444" : "#dc2626";
	const scrollbar = isDark ? darken(bg, -0.15) : darken(subtle, 0.1);
	const scrollbarHover = isDark ? darken(bg, -0.25) : darken(subtle, 0.2);

	return {
		"--background": bg,
		"--foreground": fg,
		"--bg-subtle": subtle,
		"--surface-hover": isDark ? lighten(subtle, 0.08) : darken(subtle, 0.06),
		"--surface-page": isDark ? darken(bg, 0.03) : lighten(bg, 0.005),
		"--text-primary": fg,
		"--text-secondary": isDark ? lighten(muted, 0.3) : darken(muted, 0.2),
		"--card": isDark ? lighten(bg, 0.03) : darken(bg, 0.02),
		"--card-foreground": fg,
		"--popover": isDark ? lighten(bg, 0.03) : darken(bg, 0.02),
		"--popover-foreground": fg,
		"--primary": primary,
		/* BG-R18: deriveCustomVars used to hardcode --primary-foreground to
		   white in both modes. For mid-tone primaries (green/amber/teal),
		   white fails WCAG AA 4.5:1. Pick whichever of white/black has
		   better contrast with the user-chosen primary. */
		"--primary-foreground": _pickContrastForeground(primary),
		"--secondary": isDark ? lighten(bg, 0.05) : darken(subtle, 0.03),
		"--secondary-foreground": fg,
		"--muted": isDark ? lighten(bg, 0.05) : darken(subtle, 0.02),
		"--muted-foreground": muted,
		"--accent": primary,
		"--accent-foreground": _pickContrastForeground(primary),
		"--accent-soft": `${primary}1a`,
		"--accent-muted": `${primary}66`,
		"--border": border,
		"--input": border,
		"--ring": `${primary}80`,
		"--sidebar": isDark ? subtle : lighten(subtle, 0.03),
		"--sidebar-foreground": fg,
		"--sidebar-primary": primary,
		"--sidebar-primary-foreground": _pickContrastForeground(primary),
		"--sidebar-accent": isDark ? lighten(subtle, 0.05) : darken(subtle, 0.03),
		"--sidebar-accent-foreground": fg,
		"--sidebar-border": border,
		"--sidebar-ring": `${primary}80`,
		"--destructive": destructive,
		"--destructive-foreground": "#ffffff",
		"--scrollbar-thumb": scrollbar,
		"--scrollbar-thumb-hover": scrollbarHover,
	};
}

/**
 * BG-R18: pick the foreground (white or black) that yields the higher
 * WCAG 2.1 contrast ratio against ``bgHex``. Used by ``deriveCustomVars``
 * so that mid-tone user-chosen primaries (green, amber, teal) get a
 * readable foreground instead of an unreadable white-on-yellow pair.
 *
 * When the input is unparseable (``contrastRatio`` treats it as black),
 * white wins and is returned. The function never throws.
 */
function _pickContrastForeground(bgHex: string): string {
	const whiteRatio = contrastRatio("#ffffff", bgHex);
	const blackRatio = contrastRatio("#000000", bgHex);
	return whiteRatio >= blackRatio ? "#ffffff" : "#000000";
}

// ─── Tiny colour helpers ───────────────────────────────────────────────

function darken(hex: string, amount: number): string {
	const { r, g, b } = parseHex(hex);
	return toHex(
		Math.round(r * (1 - amount)),
		Math.round(g * (1 - amount)),
		Math.round(b * (1 - amount)),
	);
}

function lighten(hex: string, amount: number): string {
	const { r, g, b } = parseHex(hex);
	return toHex(
		Math.min(255, Math.round(r + (255 - r) * amount)),
		Math.min(255, Math.round(g + (255 - g) * amount)),
		Math.min(255, Math.round(b + (255 - b) * amount)),
	);
}

function parseHex(hex: string): { r: number; g: number; b: number } {
	const clean = hex.replace("#", "");
	const n = Number.parseInt(clean, 16);
	return {
		r: (n >> 16) & 0xff,
		g: (n >> 8) & 0xff,
		b: n & 0xff,
	};
}

function toHex(r: number, g: number, b: number): string {
	return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
}
