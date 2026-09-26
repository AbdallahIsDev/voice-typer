/**
 * Built-in theme presets for Lausu.
 *
 * Each preset defines CSS variable overrides for **both** light and dark
 * colour-scheme variants.  When a preset is active, the variables are
 * applied to ``document.documentElement`` via ``style.setProperty()``,
 * layered on top of the app's default CSS (index.css) and the current
 * ``.dark`` / ``:root`` class.
 *
 * The ``default`` preset is a no-op, it means "use whatever is in the
 * stylesheet" (no overrides needed).
 *
 * presets split into ./themes/ for lazy loading. Each preset
 * now lives in its own file under ``./themes/``; this module re-exports
 * the aggregated ``THEMES`` array and ``THEME_PRESETS`` record so
 * existing consumers can keep importing from ``@/themes`` unchanged.
 */

import { contrastRatio } from "@/lib/color-utils";
import { DEFAULT_THEME_PRESET, THEME_PRESETS, THEMES } from "./themes/index";

export interface ThemePreset {
	/** Unique identifier stored in config (e.g. ``"amoled"``). */
	id: string;
	/** Human-readable label shown in the Settings dropdown. */
	name: string;
	/**
	 * i18n key for the localised preset name (e.g. ``"theme.preset.amoled"``).
	 *
	 * populated by the ``themes/index.ts`` aggregator for every
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
	// Surfaces (page canvas, raised panels, quiet zones, hover fill)
	"--background",
	"--surface",
	"--surface-subtle",
	"--surface-hover",

	// Text tiers (must-read / body / hints)
	"--foreground",
	"--text-secondary",
	"--muted-foreground",

	// Brand (primary fill + accent tints)
	"--primary",
	"--primary-foreground",
	"--accent",
	"--accent-foreground",
	"--accent-soft",
	"--accent-muted",

	// Neutral interactive fill
	"--muted",

	// Borders / inputs / rings
	"--border",
	"--input",
	"--ring",

	// Destructive
	"--destructive",
	"--destructive-foreground",

	// status tokens (success / warning / info). Must be in
	// THEME_VARIABLES so applyThemeVars clears them on theme switch
	// (clearThemeVars iterates this list) and so custom-theme
	// overrides are accepted (applyThemeVars gates on this list).
	"--success",
	"--warning",
	"--info",

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

// ─── Legacy custom-property migration ───────────────────────────────
// Custom themes saved before the token consolidation carry the old
// duplicate/alias names. Translate them to the current tokens so
// stored themes keep applying unchanged.
const LEGACY_THEME_VAR_MAP: Record<string, string> = {
	"--bg": "--background",
	"--surface-page": "--background",
	"--bg-subtle": "--surface-subtle",
	"--text-primary": "--foreground",
	"--text-muted": "--muted-foreground",
	"--card": "--surface",
	"--popover": "--surface",
	"--card-foreground": "--foreground",
	"--popover-foreground": "--foreground",
	"--secondary": "--muted",
	"--secondary-foreground": "--foreground",
};

/** Pre-consolidation tokens with no current equivalent; dropped. */
const RETIRED_THEME_VARS = new Set([
	"--text",
	"--sidebar",
	"--sidebar-foreground",
	"--sidebar-primary",
	"--sidebar-primary-foreground",
	"--sidebar-accent",
	"--sidebar-accent-foreground",
	"--sidebar-border",
	"--sidebar-ring",
]);

/**
 * Translate legacy key names to current tokens and drop retired ones.
 * An explicitly present current key always wins over a legacy alias
 * mapping to it. Pure function; safe on any custom-theme map.
 */
export function normalizeThemeVars(
	vars: Record<string, string>,
): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [key, value] of Object.entries(vars)) {
		if (RETIRED_THEME_VARS.has(key) || key in LEGACY_THEME_VAR_MAP) continue;
		out[key] = value;
	}
	for (const [legacy, current] of Object.entries(LEGACY_THEME_VAR_MAP)) {
		const value = vars[legacy];
		if (value !== undefined && out[current] === undefined) {
			out[current] = value;
		}
	}
	return out;
}

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
/**
 * Event name dispatched on `window` after every `applyThemeVars` run.
 * Subscribers that render from the LIVE CSS variables (e.g. the
 * share-stats image palette in `lib/theme-palette.ts`) listen for this
 * to re-read the tokens exactly when they change, the CSS variables
 * on `document.documentElement` are the single source of truth, and
 * this event is the change signal.
 */
export const THEME_APPLIED_EVENT = "vt:theme-applied";

export function applyThemeVars(
	presetId: string,
	isDark: boolean,
	customVars?: Record<string, string> | null,
): void {
	const root = document.documentElement;

	// Always clear previous overrides first
	clearThemeVars();

	if (presetId !== "default") {
		// Custom theme, use the passed-in variable map directly
		if (presetId === CUSTOM_THEME_ID && customVars) {
			for (const [key, value] of Object.entries(
				normalizeThemeVars(customVars),
			)) {
				// Only set variables that are in our known list
				if ((THEME_VARIABLES as readonly string[]).includes(key)) {
					root.style.setProperty(key, value);
				}
			}
		} else {
			const theme = THEMES.find((t) => t.id === presetId);
			if (theme) {
				const vars = isDark ? theme.dark : theme.light;
				for (const [key, value] of Object.entries(vars)) {
					root.style.setProperty(key, value);
				}
			}
		}
	}

	// Notify palette readers (and any future CSS-var subscriber) that
	// the applied tokens changed, including the "default" clear path,
	// which also changes what the variables resolve to.
	if (typeof window !== "undefined") {
		window.dispatchEvent(new CustomEvent(THEME_APPLIED_EVENT));
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
// presets split into ./themes/ for lazy loading.
// Each preset now lives in its own file under ./themes/<preset>.ts and is
// aggregated by ./themes/index.ts. The re-exports below preserve the
// pre-refactor public API: consumers can keep importing `THEMES` (ordered
// array) or use the new `THEME_PRESETS` record for O(1) id → preset
// lookups. Callers that want to lazy-load a single preset can dynamically
// `import()` the individual file (e.g. `await import("./themes/amoled")`).
export { DEFAULT_THEME_PRESET, THEME_PRESETS, THEMES };

/** Look up a theme preset by id. Returns the default theme if not found. */
export function getThemeById(id: string): ThemePreset {
	// `THEMES` is a non-empty array exported from `./themes/index.ts`,
	// so index 0 always exists; the explicit fallback keeps TypeScript
	// happy under `noUncheckedIndexedAccess` and documents the intent.
	return THEMES.find((t) => t.id === id) ?? THEMES[0] ?? DEFAULT_THEME_PRESET;
}

/**
 * Core CSS variables exposed in the custom-theme colour picker (6
 * entries; each carries ``labelKey`` / ``descriptionKey``, i18n keys
 * resolved via ``t()`` in ``ThemeSettingsSection.tsx``). Other
 * tokens (surface fill, charts, scrollbar) are auto-derived from
 * these core values. The legacy ``label`` / ``description`` English
 * strings are kept as fallbacks for the rare caller that reads them
 * outside a React context (e.g. tests).
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
		var: "--surface-subtle",
		label: "Surface",
		description: "Cards, panels, and quiet surfaces",
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
		var: "--muted-foreground",
		label: "Muted Text",
		description: "Secondary / dimmed text colour",
		labelKey: "settings.appearance.colorLabel.text-muted",
		descriptionKey: "settings.appearance.colorDescription.text-muted",
	},
];

// ─── Default values for each custom colour key ────────────────────────
// These are the "reset" values for the stock light/dark mode.

// Default colour-key set. Typed loosely so consumers (ThemeSettingsSection,
// useThemeSettings, theme-contrast) can index these with arbitrary
// `string` keys without TypeScript flagging them under
// `noUncheckedIndexedAccess`; the keys the renderer actually writes are
// constrained by `CUSTOM_COLOR_KEYS` above. Reads inside `deriveCustomVars`
// narrow with a `?? ""` fallback to keep the function self-contained
// under strict mode.
export const DEFAULT_CUSTOM_LIGHT: Record<string, string> = {
	"--background": "#ffffff",
	"--foreground": "#09090b",
	"--primary": "#1447e6",
	"--surface-subtle": "#f5f5f5",
	"--border": "#e4e4e7",
	"--muted-foreground": "#71717b",
};

export const DEFAULT_CUSTOM_DARK: Record<string, string> = {
	"--background": "#131313",
	"--foreground": "#fafafa",
	"--primary": "#193cb8",
	"--surface-subtle": "#0f0f0f",
	"--border": "#1f1f1f",
	"--muted-foreground": "#9f9fa9",
};

/** Build a full set of CSS var overrides from the 6 core custom colours. */
export function deriveCustomVars(
	core: Record<string, string>,
	isDark: boolean,
): Record<string, string> {
	// `Record<string, string>` reads under `noUncheckedIndexedAccess`
	// widen to `string | undefined`; `?? ""` keeps the rest of the
	// function free of null-checks while preserving the original
	// behaviour (defaults below are guaranteed by the literals above,
	// so the empty-string fallback only kicks in if a caller passes a
	// dict missing one of the canonical keys, which downstream
	// darken/lighten/contrast calls already treat as black). The
	// normalize pass translates pre-consolidation key names.
	const vars = normalizeThemeVars(core);
	const defaults = isDark ? DEFAULT_CUSTOM_DARK : DEFAULT_CUSTOM_LIGHT;
	const bg = vars["--background"] ?? defaults["--background"] ?? "";
	const fg = vars["--foreground"] ?? defaults["--foreground"] ?? "";
	const primary = vars["--primary"] ?? defaults["--primary"] ?? "";
	const subtle = vars["--surface-subtle"] ?? defaults["--surface-subtle"] ?? "";
	const border = vars["--border"] ?? defaults["--border"] ?? "";
	const muted =
		vars["--muted-foreground"] ?? defaults["--muted-foreground"] ?? "";

	const destructive = isDark ? "#ef4444" : "#dc2626";
	const scrollbar = isDark ? darken(bg, -0.15) : darken(subtle, 0.1);
	const scrollbarHover = isDark ? darken(bg, -0.25) : darken(subtle, 0.2);

	return {
		"--background": bg,
		"--foreground": fg,
		"--surface": isDark ? lighten(bg, 0.03) : darken(bg, 0.02),
		"--surface-subtle": subtle,
		"--surface-hover": isDark ? lighten(subtle, 0.08) : darken(subtle, 0.06),
		"--text-secondary": isDark ? lighten(muted, 0.3) : darken(muted, 0.2),
		"--primary": primary,
		/* deriveCustomVars used to hardcode --primary-foreground to
		   white in both modes. For mid-tone primaries (green/amber/teal),
		   white fails WCAG AA 4.5:1. Pick whichever of white/black has
		   better contrast with the user-chosen primary. */
		"--primary-foreground": pickContrastForeground(primary),
		"--muted": isDark ? lighten(bg, 0.05) : darken(subtle, 0.02),
		"--muted-foreground": muted,
		"--accent": primary,
		"--accent-foreground": pickContrastForeground(primary),
		"--accent-soft": `${primary}1a`,
		"--accent-muted": `${primary}66`,
		"--border": border,
		"--input": border,
		"--ring": `${primary}80`,
		"--destructive": destructive,
		"--destructive-foreground": "#ffffff",
		/* emit the three status tokens so custom themes don't fall
		   back to the stylesheet default. The colours are derived from the
		   user's chosen destructive hue where possible (success/warning/info
		   stay semantic, green/amber/blue, so status meaning is preserved
		   even on user-customised palettes). */
		"--success": isDark ? "#22c55e" : "#16a34a",
		"--warning": isDark ? "#f59e0b" : "#d97706",
		"--info": isDark ? "#3b82f6" : "#2563eb",
		"--scrollbar-thumb": scrollbar,
		"--scrollbar-thumb-hover": scrollbarHover,
	};
}

/**
 * pick the foreground (white or black) that yields the higher
 * WCAG 2.1 contrast ratio against ``bgHex``. Used by ``deriveCustomVars``
 * so that mid-tone user-chosen primaries (green, amber, teal) get a
 * readable foreground instead of an unreadable white-on-yellow pair.
 *
 * When the input is unparseable (``contrastRatio`` treats it as black),
 * white wins and is returned. The function never throws.
 */
export function pickContrastForeground(bgHex: string): string {
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
