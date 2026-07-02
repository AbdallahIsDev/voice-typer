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
 */

export interface ThemePreset {
	/** Unique identifier stored in config (e.g. ``"amoled"``). */
	id: string;
	/** Human-readable label shown in the Settings dropdown. */
	name: string;
	/** Short description shown as tooltip/hint. */
	description: string;
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

	// Text
	"--text-primary",
	"--text-muted",
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

export const THEMES: ThemePreset[] = [
	// ── Default ──────────────────────────────────────────────────────────
	{
		id: "default",
		name: "Default",
		description:
			"The standard Voice Typer look — clean, neutral, and familiar.",
		swatch: "oklch(0.488 0.243 264.376)", // primary blue
		light: {}, // no overrides → use what's in the stylesheet
		dark: {},
	},

	// ── Amoled ───────────────────────────────────────────────────────────
	{
		id: "amoled",
		name: "Amoled",
		description:
			"True-black backgrounds for deep contrast on OLED displays. Pure blacks save battery on AMOLED screens.",
		swatch: "#000000",
		light: {
			"--background": "oklch(1 0 0)",
			"--foreground": "oklch(0.1 0 0)",
			"--bg-subtle": "oklch(0.96 0 0)",
			"--surface-hover": "oklch(0.92 0 0)",
			"--text-muted": "oklch(0.5 0 0)",
			"--text-secondary": "oklch(0.4 0 0)",
			"--border": "oklch(0.9 0 0)",
			"--card": "oklch(1 0 0)",
			"--card-foreground": "oklch(0.1 0 0)",
			"--popover": "oklch(1 0 0)",
			"--popover-foreground": "oklch(0.1 0 0)",
			"--primary": "oklch(0.488 0.243 264.376)",
			"--primary-foreground": "oklch(0.97 0.014 254.604)",
			"--sidebar": "oklch(0.98 0 0)",
			"--sidebar-foreground": "oklch(0.1 0 0)",
			"--sidebar-border": "oklch(0.9 0 0)",
		},
		dark: {
			"--background": "oklch(0 0 0)",
			"--foreground": "oklch(0.985 0 0)",
			"--bg-subtle": "oklch(0.04 0 0)",
			"--surface-hover": "oklch(0.1 0 0)",
			"--surface-page": "oklch(0 0 0)",
			"--text-primary": "oklch(0.985 0 0)",
			"--text-muted": "oklch(0.55 0 0)",
			"--text-secondary": "oklch(0.8 0 0)",
			"--card": "oklch(0.04 0 0)",
			"--card-foreground": "oklch(0.985 0 0)",
			"--popover": "oklch(0.04 0 0)",
			"--popover-foreground": "oklch(0.985 0 0)",
			"--muted": "oklch(0.08 0 0)",
			"--muted-foreground": "oklch(0.55 0 0)",
			"--secondary": "oklch(0.1 0 0)",
			"--secondary-foreground": "oklch(0.985 0 0)",
			"--border": "oklch(0.15 0 0)",
			"--input": "oklch(0.18 0 0)",
			"--ring": "oklch(0.4 0 0)",
			"--sidebar": "oklch(0.02 0 0)",
			"--sidebar-foreground": "oklch(0.985 0 0)",
			"--sidebar-primary": "oklch(0.546 0.245 262.881)",
			"--sidebar-primary-foreground": "oklch(0.97 0.014 254.604)",
			"--sidebar-accent": "oklch(0.08 0 0)",
			"--sidebar-accent-foreground": "oklch(0.985 0 0)",
			"--sidebar-border": "oklch(0.1 0 0)",
			"--sidebar-ring": "oklch(0.4 0 0)",
			"--accent-soft": "oklch(0.424 0.199 265.638 / 0.08)",
			"--accent-muted": "oklch(0.546 0.245 262.881 / 0.3)",
			"--scrollbar-thumb": "oklch(0.2 0 0)",
			"--scrollbar-thumb-hover": "oklch(0.3 0 0)",
		},
	},

	// ── Nord ────────────────────────────────────────────────────────────
	{
		id: "nord",
		name: "Nord",
		description:
			"Cool arctic blues and frosty greys — inspired by the popular Nord colour palette.",
		swatch: "oklch(0.5 0.06 240)",
		light: {
			"--background": "oklch(0.97 0.006 240)",
			"--foreground": "oklch(0.2 0.015 240)",
			"--bg-subtle": "oklch(0.94 0.008 240)",
			"--surface-hover": "oklch(0.9 0.008 240)",
			"--text-muted": "oklch(0.5 0.01 240)",
			"--text-secondary": "oklch(0.4 0.01 240)",
			"--border": "oklch(0.88 0.008 240)",
			"--card": "oklch(0.98 0.006 240)",
			"--popover": "oklch(0.98 0.006 240)",
			"--primary": "oklch(0.5 0.08 240)",
			"--primary-foreground": "oklch(0.97 0.014 254.604)",
			"--secondary": "oklch(0.92 0.006 240)",
			"--secondary-foreground": "oklch(0.25 0.01 240)",
			"--accent": "oklch(0.45 0.08 240)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.5 0.08 240 / 0.1)",
			"--accent-muted": "oklch(0.5 0.08 240 / 0.35)",
			"--muted": "oklch(0.93 0.006 240)",
			"--muted-foreground": "oklch(0.5 0.01 240)",
			"--sidebar": "oklch(0.94 0.008 240)",
			"--sidebar-foreground": "oklch(0.2 0.015 240)",
			"--sidebar-accent": "oklch(0.9 0.008 240)",
			"--sidebar-border": "oklch(0.86 0.008 240)",
			"--input": "oklch(0.88 0.008 240)",
			"--ring": "oklch(0.6 0.06 240)",
			"--chart-1": "oklch(0.6 0.15 240)",
			"--chart-2": "oklch(0.55 0.1 200)",
			"--chart-3": "oklch(0.5 0.08 280)",
			"--chart-4": "oklch(0.55 0.1 160)",
			"--chart-5": "oklch(0.6 0.12 40)",
		},
		dark: {
			"--background": "oklch(0.18 0.01 240)",
			"--foreground": "oklch(0.92 0.008 240)",
			"--bg-subtle": "oklch(0.14 0.008 240)",
			"--surface-hover": "oklch(0.22 0.008 240)",
			"--text-primary": "oklch(0.92 0.008 240)",
			"--text-muted": "oklch(0.6 0.01 240)",
			"--text-secondary": "oklch(0.8 0.008 240)",
			"--card": "oklch(0.2 0.01 240)",
			"--card-foreground": "oklch(0.92 0.008 240)",
			"--popover": "oklch(0.2 0.01 240)",
			"--popover-foreground": "oklch(0.92 0.008 240)",
			"--primary": "oklch(0.6 0.08 240)",
			"--primary-foreground": "oklch(0.97 0.014 254.604)",
			"--secondary": "oklch(0.24 0.008 240)",
			"--secondary-foreground": "oklch(0.92 0.008 240)",
			"--muted": "oklch(0.22 0.008 240)",
			"--muted-foreground": "oklch(0.6 0.01 240)",
			"--accent": "oklch(0.6 0.08 240)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.6 0.08 240 / 0.12)",
			"--accent-muted": "oklch(0.6 0.08 240 / 0.4)",
			"--border": "oklch(0.26 0.01 240)",
			"--input": "oklch(0.28 0.01 240)",
			"--ring": "oklch(0.5 0.05 240)",
			"--sidebar": "oklch(0.15 0.01 240)",
			"--sidebar-foreground": "oklch(0.92 0.008 240)",
			"--sidebar-primary": "oklch(0.6 0.08 240)",
			"--sidebar-primary-foreground": "oklch(0.97 0.014 254.604)",
			"--sidebar-accent": "oklch(0.22 0.008 240)",
			"--sidebar-accent-foreground": "oklch(0.92 0.008 240)",
			"--sidebar-border": "oklch(0.22 0.01 240)",
			"--sidebar-ring": "oklch(0.5 0.05 240)",
			"--destructive": "oklch(0.55 0.25 27)",
			"--chart-1": "oklch(0.65 0.12 240)",
			"--chart-2": "oklch(0.6 0.08 200)",
			"--chart-3": "oklch(0.55 0.06 280)",
			"--chart-4": "oklch(0.6 0.08 160)",
			"--chart-5": "oklch(0.65 0.1 40)",
			"--scrollbar-thumb": "oklch(0.3 0.01 240)",
			"--scrollbar-thumb-hover": "oklch(0.4 0.01 240)",
		},
	},

	// ── Dracula ──────────────────────────────────────────────────────────
	{
		id: "dracula",
		name: "Dracula",
		description:
			"Rich purples and deep magentas — a dark-contrast theme inspired by the Dracula colour scheme.",
		swatch: "oklch(0.5 0.16 320)",
		light: {
			"--background": "oklch(0.96 0.01 320)",
			"--foreground": "oklch(0.18 0.015 320)",
			"--bg-subtle": "oklch(0.93 0.012 320)",
			"--surface-hover": "oklch(0.89 0.014 320)",
			"--text-muted": "oklch(0.48 0.02 320)",
			"--text-secondary": "oklch(0.38 0.018 320)",
			"--border": "oklch(0.86 0.012 320)",
			"--card": "oklch(0.97 0.01 320)",
			"--popover": "oklch(0.97 0.01 320)",
			"--primary": "oklch(0.5 0.18 320)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.9 0.014 320)",
			"--secondary-foreground": "oklch(0.22 0.015 320)",
			"--accent": "oklch(0.5 0.16 320)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.5 0.16 320 / 0.1)",
			"--accent-muted": "oklch(0.5 0.16 320 / 0.35)",
			"--muted": "oklch(0.92 0.012 320)",
			"--muted-foreground": "oklch(0.48 0.02 320)",
			"--sidebar": "oklch(0.93 0.012 320)",
			"--sidebar-foreground": "oklch(0.18 0.015 320)",
			"--sidebar-accent": "oklch(0.88 0.014 320)",
			"--sidebar-border": "oklch(0.84 0.012 320)",
			"--input": "oklch(0.86 0.012 320)",
			"--ring": "oklch(0.6 0.12 320)",
			"--chart-1": "oklch(0.6 0.18 320)",
			"--chart-2": "oklch(0.55 0.15 280)",
			"--chart-3": "oklch(0.5 0.12 340)",
			"--chart-4": "oklch(0.55 0.1 240)",
			"--chart-5": "oklch(0.6 0.14 30)",
		},
		dark: {
			"--background": "oklch(0.15 0.015 320)",
			"--foreground": "oklch(0.9 0.01 320)",
			"--bg-subtle": "oklch(0.11 0.012 320)",
			"--surface-hover": "oklch(0.19 0.014 320)",
			"--text-primary": "oklch(0.9 0.01 320)",
			"--text-muted": "oklch(0.55 0.02 320)",
			"--text-secondary": "oklch(0.75 0.015 320)",
			"--card": "oklch(0.17 0.015 320)",
			"--card-foreground": "oklch(0.9 0.01 320)",
			"--popover": "oklch(0.17 0.015 320)",
			"--popover-foreground": "oklch(0.9 0.01 320)",
			"--primary": "oklch(0.6 0.16 320)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.22 0.014 320)",
			"--secondary-foreground": "oklch(0.9 0.01 320)",
			"--muted": "oklch(0.2 0.012 320)",
			"--muted-foreground": "oklch(0.55 0.02 320)",
			"--accent": "oklch(0.6 0.14 320)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.5 0.16 320 / 0.12)",
			"--accent-muted": "oklch(0.5 0.16 320 / 0.4)",
			"--border": "oklch(0.24 0.015 320)",
			"--input": "oklch(0.26 0.015 320)",
			"--ring": "oklch(0.5 0.1 320)",
			"--sidebar": "oklch(0.12 0.015 320)",
			"--sidebar-foreground": "oklch(0.9 0.01 320)",
			"--sidebar-primary": "oklch(0.6 0.16 320)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.2 0.014 320)",
			"--sidebar-accent-foreground": "oklch(0.9 0.01 320)",
			"--sidebar-border": "oklch(0.2 0.015 320)",
			"--sidebar-ring": "oklch(0.5 0.1 320)",
			"--destructive": "oklch(0.55 0.25 27)",
			"--chart-1": "oklch(0.65 0.16 320)",
			"--chart-2": "oklch(0.6 0.12 280)",
			"--chart-3": "oklch(0.55 0.1 340)",
			"--chart-4": "oklch(0.6 0.08 240)",
			"--chart-5": "oklch(0.65 0.12 30)",
			"--scrollbar-thumb": "oklch(0.3 0.015 320)",
			"--scrollbar-thumb-hover": "oklch(0.4 0.015 320)",
		},
	},

	// ── Sepia ────────────────────────────────────────────────────────────
	{
		id: "sepia",
		name: "Sepia",
		description:
			"Warm amber tones and cream backgrounds — easy on the eyes, like reading a well-loved book.",
		swatch: "oklch(0.6 0.08 50)",
		light: {
			"--background": "oklch(0.96 0.025 75)",
			"--foreground": "oklch(0.18 0.025 40)",
			"--bg-subtle": "oklch(0.93 0.02 75)",
			"--surface-hover": "oklch(0.89 0.02 75)",
			"--text-muted": "oklch(0.45 0.02 40)",
			"--text-secondary": "oklch(0.35 0.02 40)",
			"--border": "oklch(0.84 0.02 75)",
			"--card": "oklch(0.97 0.025 75)",
			"--card-foreground": "oklch(0.18 0.025 40)",
			"--popover": "oklch(0.97 0.025 75)",
			"--popover-foreground": "oklch(0.18 0.025 40)",
			"--primary": "oklch(0.55 0.1 50)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.9 0.02 75)",
			"--secondary-foreground": "oklch(0.22 0.025 40)",
			"--accent": "oklch(0.55 0.1 50)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.55 0.1 50 / 0.1)",
			"--accent-muted": "oklch(0.55 0.1 50 / 0.35)",
			"--muted": "oklch(0.92 0.02 75)",
			"--muted-foreground": "oklch(0.45 0.02 40)",
			"--input": "oklch(0.84 0.02 75)",
			"--ring": "oklch(0.6 0.08 50)",
			"--sidebar": "oklch(0.94 0.02 75)",
			"--sidebar-foreground": "oklch(0.18 0.025 40)",
			"--sidebar-primary": "oklch(0.55 0.1 50)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.9 0.02 75)",
			"--sidebar-accent-foreground": "oklch(0.22 0.025 40)",
			"--sidebar-border": "oklch(0.85 0.02 75)",
			"--sidebar-ring": "oklch(0.6 0.08 50)",
			"--chart-1": "oklch(0.6 0.12 50)",
			"--chart-2": "oklch(0.55 0.1 160)",
			"--chart-3": "oklch(0.5 0.08 280)",
			"--chart-4": "oklch(0.55 0.1 200)",
			"--chart-5": "oklch(0.6 0.12 30)",
		},
		dark: {
			"--background": "oklch(0.14 0.02 40)",
			"--foreground": "oklch(0.93 0.015 75)",
			"--bg-subtle": "oklch(0.11 0.015 40)",
			"--surface-hover": "oklch(0.18 0.02 40)",
			"--text-primary": "oklch(0.93 0.015 75)",
			"--text-muted": "oklch(0.5 0.015 40)",
			"--text-secondary": "oklch(0.75 0.01 75)",
			"--card": "oklch(0.16 0.02 40)",
			"--card-foreground": "oklch(0.93 0.015 75)",
			"--popover": "oklch(0.16 0.02 40)",
			"--popover-foreground": "oklch(0.93 0.015 75)",
			"--primary": "oklch(0.6 0.1 50)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.22 0.02 40)",
			"--secondary-foreground": "oklch(0.93 0.015 75)",
			"--muted": "oklch(0.2 0.015 40)",
			"--muted-foreground": "oklch(0.5 0.015 40)",
			"--accent": "oklch(0.6 0.08 50)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.55 0.1 50 / 0.12)",
			"--accent-muted": "oklch(0.55 0.1 50 / 0.4)",
			"--border": "oklch(0.22 0.02 40)",
			"--input": "oklch(0.24 0.02 40)",
			"--ring": "oklch(0.5 0.06 50)",
			"--sidebar": "oklch(0.12 0.02 40)",
			"--sidebar-foreground": "oklch(0.93 0.015 75)",
			"--sidebar-primary": "oklch(0.6 0.1 50)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.18 0.02 40)",
			"--sidebar-accent-foreground": "oklch(0.93 0.015 75)",
			"--sidebar-border": "oklch(0.2 0.02 40)",
			"--sidebar-ring": "oklch(0.5 0.06 50)",
			"--destructive": "oklch(0.55 0.25 27)",
			"--chart-1": "oklch(0.65 0.12 50)",
			"--chart-2": "oklch(0.6 0.1 160)",
			"--chart-3": "oklch(0.55 0.08 280)",
			"--chart-4": "oklch(0.6 0.1 200)",
			"--chart-5": "oklch(0.65 0.12 30)",
			"--scrollbar-thumb": "oklch(0.3 0.02 40)",
			"--scrollbar-thumb-hover": "oklch(0.4 0.02 40)",
		},
	},

	// ── Custom ───────────────────────────────────────────────────────────
	{
		id: "custom",
		name: "Custom",
		description:
			"Your own personalised colours — configure each colour in the editor below.",
		swatch: "oklch(0.6 0.15 280)", // gradient-like purple to hint at "customisable"
		light: {}, // handled via custom_theme data
		dark: {},
	},

	// ── Monokai ──────────────────────────────────────────────────────────
	{
		id: "monokai",
		name: "Monokai",
		description:
			"High-contrast dark base with vivid yellow, green, and pink accents — inspired by the classic syntax theme.",
		swatch: "oklch(0.75 0.15 100)",
		light: {
			"--background": "oklch(0.97 0.008 85)",
			"--foreground": "oklch(0.2 0.01 0)",
			"--bg-subtle": "oklch(0.93 0.008 80)",
			"--surface-hover": "oklch(0.89 0.008 80)",
			"--text-muted": "oklch(0.5 0.008 0)",
			"--text-secondary": "oklch(0.38 0.008 0)",
			"--border": "oklch(0.85 0.008 80)",
			"--card": "oklch(0.98 0.006 85)",
			"--card-foreground": "oklch(0.2 0.01 0)",
			"--popover": "oklch(0.98 0.006 85)",
			"--popover-foreground": "oklch(0.2 0.01 0)",
			"--primary": "oklch(0.68 0.18 135)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.91 0.008 80)",
			"--secondary-foreground": "oklch(0.22 0.01 0)",
			"--accent": "oklch(0.75 0.15 100)",
			"--accent-foreground": "oklch(0.1 0 0)",
			"--accent-soft": "oklch(0.75 0.15 100 / 0.12)",
			"--accent-muted": "oklch(0.68 0.18 135 / 0.35)",
			"--muted": "oklch(0.92 0.008 80)",
			"--muted-foreground": "oklch(0.52 0.008 0)",
			"--input": "oklch(0.85 0.008 80)",
			"--ring": "oklch(0.6 0.12 135)",
			"--sidebar": "oklch(0.94 0.008 80)",
			"--sidebar-foreground": "oklch(0.2 0.01 0)",
			"--sidebar-primary": "oklch(0.68 0.18 135)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.91 0.008 80)",
			"--sidebar-accent-foreground": "oklch(0.22 0.01 0)",
			"--sidebar-border": "oklch(0.84 0.008 80)",
			"--sidebar-ring": "oklch(0.6 0.12 135)",
			"--destructive": "oklch(0.6 0.2 0)",
			"--chart-1": "oklch(0.68 0.18 135)",
			"--chart-2": "oklch(0.75 0.15 100)",
			"--chart-3": "oklch(0.6 0.2 0)",
			"--chart-4": "oklch(0.65 0.12 210)",
			"--chart-5": "oklch(0.72 0.14 70)",
		},
		dark: {
			"--background": "oklch(0.16 0.008 340)",
			"--foreground": "oklch(0.93 0.008 80)",
			"--bg-subtle": "oklch(0.12 0.006 340)",
			"--surface-hover": "oklch(0.2 0.01 340)",
			"--text-primary": "oklch(0.93 0.008 80)",
			"--text-muted": "oklch(0.55 0.006 0)",
			"--text-secondary": "oklch(0.75 0.006 80)",
			"--card": "oklch(0.18 0.008 340)",
			"--card-foreground": "oklch(0.93 0.008 80)",
			"--popover": "oklch(0.18 0.008 340)",
			"--popover-foreground": "oklch(0.93 0.008 80)",
			"--primary": "oklch(0.68 0.18 135)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.22 0.01 340)",
			"--secondary-foreground": "oklch(0.93 0.008 80)",
			"--muted": "oklch(0.2 0.008 340)",
			"--muted-foreground": "oklch(0.55 0.006 0)",
			"--accent": "oklch(0.75 0.15 100)",
			"--accent-foreground": "oklch(0.1 0 0)",
			"--accent-soft": "oklch(0.75 0.15 100 / 0.15)",
			"--accent-muted": "oklch(0.68 0.18 135 / 0.35)",
			"--border": "oklch(0.24 0.01 340)",
			"--input": "oklch(0.26 0.01 340)",
			"--ring": "oklch(0.55 0.1 135)",
			"--sidebar": "oklch(0.12 0.008 340)",
			"--sidebar-foreground": "oklch(0.93 0.008 80)",
			"--sidebar-primary": "oklch(0.68 0.18 135)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.2 0.01 340)",
			"--sidebar-accent-foreground": "oklch(0.93 0.008 80)",
			"--sidebar-border": "oklch(0.22 0.01 340)",
			"--sidebar-ring": "oklch(0.55 0.1 135)",
			"--destructive": "oklch(0.6 0.2 0)",
			"--chart-1": "oklch(0.68 0.18 135)",
			"--chart-2": "oklch(0.75 0.15 100)",
			"--chart-3": "oklch(0.6 0.2 0)",
			"--chart-4": "oklch(0.65 0.12 210)",
			"--chart-5": "oklch(0.72 0.14 70)",
			"--scrollbar-thumb": "oklch(0.28 0.01 340)",
			"--scrollbar-thumb-hover": "oklch(0.38 0.01 340)",
			"--surface-page": "oklch(0.14 0.008 340)",
		},
	},

	// ── Ayu ──────────────────────────────────────────────────────────────
	{
		id: "ayu",
		name: "Ayu",
		description:
			"Warm amber and blue-grey tones with soft contrast — inspired by the Ayu colour scheme family.",
		swatch: "oklch(0.7 0.14 70)",
		light: {
			"--background": "oklch(0.97 0.012 85)",
			"--foreground": "oklch(0.22 0.02 240)",
			"--bg-subtle": "oklch(0.93 0.01 85)",
			"--surface-hover": "oklch(0.88 0.01 85)",
			"--text-muted": "oklch(0.5 0.015 240)",
			"--text-secondary": "oklch(0.4 0.015 240)",
			"--border": "oklch(0.84 0.012 85)",
			"--card": "oklch(0.98 0.01 85)",
			"--card-foreground": "oklch(0.22 0.02 240)",
			"--popover": "oklch(0.98 0.01 85)",
			"--popover-foreground": "oklch(0.22 0.02 240)",
			"--primary": "oklch(0.65 0.16 70)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.9 0.012 85)",
			"--secondary-foreground": "oklch(0.25 0.015 240)",
			"--accent": "oklch(0.55 0.1 210)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.55 0.1 210 / 0.1)",
			"--accent-muted": "oklch(0.65 0.16 70 / 0.3)",
			"--muted": "oklch(0.92 0.01 85)",
			"--muted-foreground": "oklch(0.52 0.015 240)",
			"--input": "oklch(0.84 0.012 85)",
			"--ring": "oklch(0.6 0.12 70)",
			"--sidebar": "oklch(0.94 0.012 85)",
			"--sidebar-foreground": "oklch(0.22 0.02 240)",
			"--sidebar-primary": "oklch(0.65 0.16 70)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.9 0.012 85)",
			"--sidebar-accent-foreground": "oklch(0.25 0.015 240)",
			"--sidebar-border": "oklch(0.84 0.012 85)",
			"--sidebar-ring": "oklch(0.6 0.12 70)",
			"--destructive": "oklch(0.55 0.2 30)",
			"--chart-1": "oklch(0.65 0.16 70)",
			"--chart-2": "oklch(0.55 0.1 210)",
			"--chart-3": "oklch(0.6 0.12 160)",
			"--chart-4": "oklch(0.6 0.14 300)",
			"--chart-5": "oklch(0.65 0.12 30)",
		},
		dark: {
			"--background": "oklch(0.12 0.015 255)",
			"--foreground": "oklch(0.92 0.01 85)",
			"--bg-subtle": "oklch(0.09 0.012 255)",
			"--surface-hover": "oklch(0.16 0.015 255)",
			"--text-primary": "oklch(0.92 0.01 85)",
			"--text-muted": "oklch(0.5 0.01 240)",
			"--text-secondary": "oklch(0.75 0.008 85)",
			"--card": "oklch(0.14 0.015 255)",
			"--card-foreground": "oklch(0.92 0.01 85)",
			"--popover": "oklch(0.14 0.015 255)",
			"--popover-foreground": "oklch(0.92 0.01 85)",
			"--primary": "oklch(0.7 0.16 70)",
			"--primary-foreground": "oklch(0.1 0 0)",
			"--secondary": "oklch(0.18 0.015 255)",
			"--secondary-foreground": "oklch(0.92 0.01 85)",
			"--muted": "oklch(0.16 0.012 255)",
			"--muted-foreground": "oklch(0.5 0.01 240)",
			"--accent": "oklch(0.6 0.1 210)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.6 0.1 210 / 0.12)",
			"--accent-muted": "oklch(0.7 0.16 70 / 0.35)",
			"--border": "oklch(0.22 0.015 255)",
			"--input": "oklch(0.24 0.015 255)",
			"--ring": "oklch(0.5 0.08 70)",
			"--sidebar": "oklch(0.1 0.015 255)",
			"--sidebar-foreground": "oklch(0.92 0.01 85)",
			"--sidebar-primary": "oklch(0.7 0.16 70)",
			"--sidebar-primary-foreground": "oklch(0.1 0 0)",
			"--sidebar-accent": "oklch(0.16 0.015 255)",
			"--sidebar-accent-foreground": "oklch(0.92 0.01 85)",
			"--sidebar-border": "oklch(0.2 0.015 255)",
			"--sidebar-ring": "oklch(0.5 0.08 70)",
			"--destructive": "oklch(0.55 0.2 30)",
			"--chart-1": "oklch(0.7 0.16 70)",
			"--chart-2": "oklch(0.6 0.1 210)",
			"--chart-3": "oklch(0.65 0.12 160)",
			"--chart-4": "oklch(0.65 0.14 300)",
			"--chart-5": "oklch(0.7 0.12 30)",
			"--scrollbar-thumb": "oklch(0.28 0.015 255)",
			"--scrollbar-thumb-hover": "oklch(0.38 0.015 255)",
		},
	},

	// ── GitHub ───────────────────────────────────────────────────────────
	{
		id: "github",
		name: "GitHub",
		description:
			"Clean, neutral greys with blue accents — the familiar look from GitHub's interface.",
		swatch: "oklch(0.5 0.12 260)",
		light: {
			"--background": "oklch(0.99 0 0)",
			"--foreground": "oklch(0.12 0.008 0)",
			"--bg-subtle": "oklch(0.96 0.004 0)",
			"--surface-hover": "oklch(0.92 0.004 0)",
			"--surface-page": "oklch(0.99 0 0)",
			"--text-primary": "oklch(0.12 0.008 0)",
			"--text-muted": "oklch(0.5 0.006 0)",
			"--text-secondary": "oklch(0.38 0.006 0)",
			"--card": "oklch(1 0 0)",
			"--card-foreground": "oklch(0.12 0.008 0)",
			"--popover": "oklch(1 0 0)",
			"--popover-foreground": "oklch(0.12 0.008 0)",
			"--primary": "oklch(0.5 0.14 260)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.94 0.004 0)",
			"--secondary-foreground": "oklch(0.22 0.008 0)",
			"--muted": "oklch(0.95 0.004 0)",
			"--muted-foreground": "oklch(0.52 0.006 0)",
			"--accent": "oklch(0.45 0.12 260)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.5 0.14 260 / 0.1)",
			"--accent-muted": "oklch(0.5 0.14 260 / 0.3)",
			"--border": "oklch(0.88 0.004 0)",
			"--input": "oklch(0.88 0.004 0)",
			"--ring": "oklch(0.55 0.1 260)",
			"--sidebar": "oklch(0.96 0.004 0)",
			"--sidebar-foreground": "oklch(0.12 0.008 0)",
			"--sidebar-primary": "oklch(0.5 0.14 260)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.93 0.004 0)",
			"--sidebar-accent-foreground": "oklch(0.22 0.008 0)",
			"--sidebar-border": "oklch(0.85 0.004 0)",
			"--sidebar-ring": "oklch(0.55 0.1 260)",
			"--destructive": "oklch(0.55 0.2 30)",
			"--chart-1": "oklch(0.55 0.14 260)",
			"--chart-2": "oklch(0.5 0.1 200)",
			"--chart-3": "oklch(0.5 0.12 320)",
			"--chart-4": "oklch(0.55 0.12 160)",
			"--chart-5": "oklch(0.6 0.14 40)",
			"--scrollbar-thumb": "oklch(0.82 0.004 0)",
			"--scrollbar-thumb-hover": "oklch(0.72 0.004 0)",
		},
		dark: {
			"--background": "oklch(0.11 0.006 0)",
			"--foreground": "oklch(0.94 0.004 0)",
			"--bg-subtle": "oklch(0.08 0.006 0)",
			"--surface-hover": "oklch(0.15 0.006 0)",
			"--surface-page": "oklch(0.09 0.006 0)",
			"--text-primary": "oklch(0.94 0.004 0)",
			"--text-muted": "oklch(0.5 0.004 0)",
			"--text-secondary": "oklch(0.78 0.004 0)",
			"--card": "oklch(0.13 0.006 0)",
			"--card-foreground": "oklch(0.94 0.004 0)",
			"--popover": "oklch(0.13 0.006 0)",
			"--popover-foreground": "oklch(0.94 0.004 0)",
			"--primary": "oklch(0.6 0.12 260)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.16 0.006 0)",
			"--secondary-foreground": "oklch(0.94 0.004 0)",
			"--muted": "oklch(0.15 0.006 0)",
			"--muted-foreground": "oklch(0.5 0.004 0)",
			"--accent": "oklch(0.55 0.1 260)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.6 0.12 260 / 0.12)",
			"--accent-muted": "oklch(0.6 0.12 260 / 0.35)",
			"--border": "oklch(0.2 0.006 0)",
			"--input": "oklch(0.22 0.006 0)",
			"--ring": "oklch(0.45 0.08 260)",
			"--sidebar": "oklch(0.1 0.006 0)",
			"--sidebar-foreground": "oklch(0.94 0.004 0)",
			"--sidebar-primary": "oklch(0.6 0.12 260)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.15 0.006 0)",
			"--sidebar-accent-foreground": "oklch(0.94 0.004 0)",
			"--sidebar-border": "oklch(0.18 0.006 0)",
			"--sidebar-ring": "oklch(0.45 0.08 260)",
			"--destructive": "oklch(0.55 0.2 30)",
			"--chart-1": "oklch(0.6 0.12 260)",
			"--chart-2": "oklch(0.55 0.1 200)",
			"--chart-3": "oklch(0.55 0.1 320)",
			"--chart-4": "oklch(0.6 0.1 160)",
			"--chart-5": "oklch(0.65 0.12 40)",
			"--scrollbar-thumb": "oklch(0.25 0.006 0)",
			"--scrollbar-thumb-hover": "oklch(0.35 0.006 0)",
		},
	},

	// ── Catppuccin ───────────────────────────────────────────────────────
	{
		id: "catppuccin",
		name: "Catppuccin",
		description:
			"Soft, warm pastels with mauve, peach, and teal accents — inspired by the Catppuccin colour scheme.",
		swatch: "oklch(0.65 0.12 330)",
		light: {
			"--background": "oklch(0.96 0.012 80)",
			"--foreground": "oklch(0.2 0.015 350)",
			"--bg-subtle": "oklch(0.93 0.01 80)",
			"--surface-hover": "oklch(0.88 0.01 80)",
			"--text-muted": "oklch(0.48 0.01 350)",
			"--text-secondary": "oklch(0.35 0.012 350)",
			"--border": "oklch(0.85 0.01 80)",
			"--card": "oklch(0.97 0.01 80)",
			"--card-foreground": "oklch(0.2 0.015 350)",
			"--popover": "oklch(0.97 0.01 80)",
			"--popover-foreground": "oklch(0.2 0.015 350)",
			"--primary": "oklch(0.6 0.14 330)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.9 0.01 80)",
			"--secondary-foreground": "oklch(0.22 0.015 350)",
			"--accent": "oklch(0.55 0.12 190)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.6 0.14 330 / 0.1)",
			"--accent-muted": "oklch(0.6 0.14 330 / 0.35)",
			"--muted": "oklch(0.91 0.01 80)",
			"--muted-foreground": "oklch(0.48 0.01 350)",
			"--input": "oklch(0.85 0.01 80)",
			"--ring": "oklch(0.6 0.1 330)",
			"--sidebar": "oklch(0.94 0.012 80)",
			"--sidebar-foreground": "oklch(0.2 0.015 350)",
			"--sidebar-primary": "oklch(0.6 0.14 330)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.9 0.01 80)",
			"--sidebar-accent-foreground": "oklch(0.22 0.015 350)",
			"--sidebar-border": "oklch(0.84 0.01 80)",
			"--sidebar-ring": "oklch(0.6 0.1 330)",
			"--destructive": "oklch(0.55 0.22 30)",
			"--chart-1": "oklch(0.6 0.14 330)",
			"--chart-2": "oklch(0.55 0.12 190)",
			"--chart-3": "oklch(0.6 0.14 50)",
			"--chart-4": "oklch(0.55 0.1 280)",
			"--chart-5": "oklch(0.6 0.1 150)",
			"--scrollbar-thumb": "oklch(0.82 0.01 80)",
			"--scrollbar-thumb-hover": "oklch(0.72 0.01 80)",
		},
		dark: {
			"--background": "oklch(0.14 0.015 340)",
			"--foreground": "oklch(0.92 0.008 80)",
			"--bg-subtle": "oklch(0.11 0.012 340)",
			"--surface-hover": "oklch(0.18 0.015 340)",
			"--text-primary": "oklch(0.92 0.008 80)",
			"--text-muted": "oklch(0.52 0.008 340)",
			"--text-secondary": "oklch(0.76 0.006 80)",
			"--card": "oklch(0.16 0.015 340)",
			"--card-foreground": "oklch(0.92 0.008 80)",
			"--popover": "oklch(0.16 0.015 340)",
			"--popover-foreground": "oklch(0.92 0.008 80)",
			"--primary": "oklch(0.65 0.13 330)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.2 0.015 340)",
			"--secondary-foreground": "oklch(0.92 0.008 80)",
			"--muted": "oklch(0.18 0.012 340)",
			"--muted-foreground": "oklch(0.52 0.008 340)",
			"--accent": "oklch(0.6 0.1 190)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.65 0.13 330 / 0.12)",
			"--accent-muted": "oklch(0.65 0.13 330 / 0.38)",
			"--border": "oklch(0.23 0.015 340)",
			"--input": "oklch(0.25 0.015 340)",
			"--ring": "oklch(0.55 0.08 330)",
			"--sidebar": "oklch(0.12 0.015 340)",
			"--sidebar-foreground": "oklch(0.92 0.008 80)",
			"--sidebar-primary": "oklch(0.65 0.13 330)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.18 0.015 340)",
			"--sidebar-accent-foreground": "oklch(0.92 0.008 80)",
			"--sidebar-border": "oklch(0.2 0.015 340)",
			"--sidebar-ring": "oklch(0.55 0.08 330)",
			"--destructive": "oklch(0.55 0.22 30)",
			"--chart-1": "oklch(0.65 0.13 330)",
			"--chart-2": "oklch(0.6 0.1 190)",
			"--chart-3": "oklch(0.65 0.12 50)",
			"--chart-4": "oklch(0.6 0.1 280)",
			"--chart-5": "oklch(0.65 0.1 150)",
			"--scrollbar-thumb": "oklch(0.3 0.015 340)",
			"--scrollbar-thumb-hover": "oklch(0.4 0.015 340)",
		},
	},

	// ── Tokyo Night ──────────────────────────────────────────────────────
	{
		id: "tokyo-night",
		name: "Tokyo Night",
		description:
			"Deep blue-black backgrounds with vibrant cyan, purple, and pink highlights — inspired by the Tokyo Night code theme.",
		swatch: "oklch(0.55 0.14 280)",
		light: {
			"--background": "oklch(0.96 0.01 250)",
			"--foreground": "oklch(0.18 0.015 260)",
			"--bg-subtle": "oklch(0.93 0.008 250)",
			"--surface-hover": "oklch(0.88 0.008 250)",
			"--text-muted": "oklch(0.45 0.01 260)",
			"--text-secondary": "oklch(0.35 0.01 260)",
			"--border": "oklch(0.84 0.008 250)",
			"--card": "oklch(0.97 0.008 250)",
			"--card-foreground": "oklch(0.18 0.015 260)",
			"--popover": "oklch(0.97 0.008 250)",
			"--popover-foreground": "oklch(0.18 0.015 260)",
			"--primary": "oklch(0.55 0.16 280)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.9 0.008 250)",
			"--secondary-foreground": "oklch(0.22 0.015 260)",
			"--accent": "oklch(0.6 0.12 210)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.55 0.16 280 / 0.1)",
			"--accent-muted": "oklch(0.55 0.16 280 / 0.35)",
			"--muted": "oklch(0.91 0.008 250)",
			"--muted-foreground": "oklch(0.45 0.01 260)",
			"--input": "oklch(0.84 0.008 250)",
			"--ring": "oklch(0.6 0.1 280)",
			"--sidebar": "oklch(0.94 0.01 250)",
			"--sidebar-foreground": "oklch(0.18 0.015 260)",
			"--sidebar-primary": "oklch(0.55 0.16 280)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.9 0.008 250)",
			"--sidebar-accent-foreground": "oklch(0.22 0.015 260)",
			"--sidebar-border": "oklch(0.84 0.008 250)",
			"--sidebar-ring": "oklch(0.6 0.1 280)",
			"--destructive": "oklch(0.55 0.22 15)",
			"--chart-1": "oklch(0.55 0.16 280)",
			"--chart-2": "oklch(0.6 0.12 210)",
			"--chart-3": "oklch(0.6 0.14 330)",
			"--chart-4": "oklch(0.65 0.14 50)",
			"--chart-5": "oklch(0.6 0.1 160)",
		},
		dark: {
			"--background": "oklch(0.12 0.02 270)",
			"--foreground": "oklch(0.92 0.01 250)",
			"--bg-subtle": "oklch(0.09 0.015 270)",
			"--surface-hover": "oklch(0.16 0.02 270)",
			"--text-primary": "oklch(0.92 0.01 250)",
			"--text-muted": "oklch(0.48 0.015 270)",
			"--text-secondary": "oklch(0.78 0.01 250)",
			"--card": "oklch(0.14 0.02 270)",
			"--card-foreground": "oklch(0.92 0.01 250)",
			"--popover": "oklch(0.14 0.02 270)",
			"--popover-foreground": "oklch(0.92 0.01 250)",
			"--primary": "oklch(0.65 0.14 280)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.18 0.018 270)",
			"--secondary-foreground": "oklch(0.92 0.01 250)",
			"--muted": "oklch(0.16 0.015 270)",
			"--muted-foreground": "oklch(0.48 0.015 270)",
			"--accent": "oklch(0.65 0.12 210)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.65 0.14 280 / 0.12)",
			"--accent-muted": "oklch(0.65 0.14 280 / 0.38)",
			"--border": "oklch(0.22 0.02 270)",
			"--input": "oklch(0.24 0.02 270)",
			"--ring": "oklch(0.55 0.08 280)",
			"--sidebar": "oklch(0.1 0.02 270)",
			"--sidebar-foreground": "oklch(0.92 0.01 250)",
			"--sidebar-primary": "oklch(0.65 0.14 280)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.16 0.02 270)",
			"--sidebar-accent-foreground": "oklch(0.92 0.01 250)",
			"--sidebar-border": "oklch(0.2 0.02 270)",
			"--sidebar-ring": "oklch(0.55 0.08 280)",
			"--destructive": "oklch(0.55 0.22 15)",
			"--chart-1": "oklch(0.65 0.14 280)",
			"--chart-2": "oklch(0.65 0.12 210)",
			"--chart-3": "oklch(0.65 0.14 330)",
			"--chart-4": "oklch(0.7 0.12 50)",
			"--chart-5": "oklch(0.65 0.1 160)",
			"--scrollbar-thumb": "oklch(0.28 0.02 270)",
			"--scrollbar-thumb-hover": "oklch(0.38 0.02 270)",
			"--surface-page": "oklch(0.11 0.02 270)",
		},
	},

	// ── Solarized ────────────────────────────────────────────────────────
	{
		id: "solarized",
		name: "Solarized",
		description:
			"A balanced palette with warm yellows and cool teals — designed for readability and reduced eye strain.",
		swatch: "oklch(0.6 0.1 200)",
		light: {
			"--background": "oklch(0.96 0.02 90)",
			"--foreground": "oklch(0.2 0.015 250)",
			"--bg-subtle": "oklch(0.93 0.02 90)",
			"--surface-hover": "oklch(0.89 0.02 90)",
			"--text-muted": "oklch(0.45 0.01 250)",
			"--text-secondary": "oklch(0.35 0.01 250)",
			"--border": "oklch(0.84 0.015 90)",
			"--card": "oklch(0.97 0.02 90)",
			"--card-foreground": "oklch(0.2 0.015 250)",
			"--popover": "oklch(0.97 0.02 90)",
			"--popover-foreground": "oklch(0.2 0.015 250)",
			"--primary": "oklch(0.55 0.1 200)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.9 0.015 90)",
			"--secondary-foreground": "oklch(0.22 0.015 250)",
			"--accent": "oklch(0.5 0.08 160)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.55 0.1 200 / 0.1)",
			"--accent-muted": "oklch(0.55 0.1 200 / 0.35)",
			"--muted": "oklch(0.92 0.015 90)",
			"--muted-foreground": "oklch(0.45 0.01 250)",
			"--input": "oklch(0.84 0.015 90)",
			"--ring": "oklch(0.6 0.08 200)",
			"--sidebar": "oklch(0.94 0.02 90)",
			"--sidebar-foreground": "oklch(0.2 0.015 250)",
			"--sidebar-primary": "oklch(0.55 0.1 200)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.9 0.015 90)",
			"--sidebar-accent-foreground": "oklch(0.22 0.015 250)",
			"--sidebar-border": "oklch(0.85 0.015 90)",
			"--sidebar-ring": "oklch(0.6 0.08 200)",
			"--chart-1": "oklch(0.6 0.12 200)",
			"--chart-2": "oklch(0.55 0.1 160)",
			"--chart-3": "oklch(0.5 0.08 100)",
			"--chart-4": "oklch(0.55 0.1 50)",
			"--chart-5": "oklch(0.6 0.1 340)",
		},
		dark: {
			"--background": "oklch(0.15 0.015 240)",
			"--foreground": "oklch(0.9 0.015 90)",
			"--bg-subtle": "oklch(0.12 0.012 240)",
			"--surface-hover": "oklch(0.19 0.015 240)",
			"--text-primary": "oklch(0.9 0.015 90)",
			"--text-muted": "oklch(0.5 0.01 240)",
			"--text-secondary": "oklch(0.75 0.01 90)",
			"--card": "oklch(0.17 0.015 240)",
			"--card-foreground": "oklch(0.9 0.015 90)",
			"--popover": "oklch(0.17 0.015 240)",
			"--popover-foreground": "oklch(0.9 0.015 90)",
			"--primary": "oklch(0.6 0.1 200)",
			"--primary-foreground": "oklch(0.97 0 0)",
			"--secondary": "oklch(0.22 0.015 240)",
			"--secondary-foreground": "oklch(0.9 0.015 90)",
			"--muted": "oklch(0.2 0.012 240)",
			"--muted-foreground": "oklch(0.5 0.01 240)",
			"--accent": "oklch(0.55 0.08 160)",
			"--accent-foreground": "oklch(0.97 0 0)",
			"--accent-soft": "oklch(0.6 0.1 200 / 0.12)",
			"--accent-muted": "oklch(0.6 0.1 200 / 0.4)",
			"--border": "oklch(0.23 0.015 240)",
			"--input": "oklch(0.25 0.015 240)",
			"--ring": "oklch(0.5 0.06 200)",
			"--sidebar": "oklch(0.13 0.015 240)",
			"--sidebar-foreground": "oklch(0.9 0.015 90)",
			"--sidebar-primary": "oklch(0.6 0.1 200)",
			"--sidebar-primary-foreground": "oklch(0.97 0 0)",
			"--sidebar-accent": "oklch(0.2 0.015 240)",
			"--sidebar-accent-foreground": "oklch(0.9 0.015 90)",
			"--sidebar-border": "oklch(0.21 0.015 240)",
			"--sidebar-ring": "oklch(0.5 0.06 200)",
			"--destructive": "oklch(0.55 0.25 27)",
			"--chart-1": "oklch(0.65 0.12 200)",
			"--chart-2": "oklch(0.6 0.1 160)",
			"--chart-3": "oklch(0.55 0.08 100)",
			"--chart-4": "oklch(0.6 0.1 50)",
			"--chart-5": "oklch(0.65 0.1 340)",
			"--scrollbar-thumb": "oklch(0.3 0.015 240)",
			"--scrollbar-thumb-hover": "oklch(0.4 0.015 240)",
		},
	},
];

/** Look up a theme preset by id. Returns the default theme if not found. */
export function getThemeById(id: string): ThemePreset {
	return THEMES.find((t) => t.id === id) ?? THEMES[0];
}

/**
 * Core CSS variables exposed in the custom-theme colour picker.
 * Other variables (card, popover, sidebar, chart, scrollbar) are
 * auto-derived from these core values.
 */
export const CUSTOM_COLOR_KEYS: {
	var: string;
	label: string;
	description: string;
}[] = [
	{
		var: "--background",
		label: "Background",
		description: "Main page and app background",
	},
	{
		var: "--foreground",
		label: "Text",
		description: "Primary text colour",
	},
	{
		var: "--primary",
		label: "Accent",
		description: "Primary accent / highlight colour",
	},
	{
		var: "--bg-subtle",
		label: "Surface",
		description: "Card / sidebar / secondary background",
	},
	{
		var: "--border",
		label: "Border",
		description: "Lines, dividers, and input borders",
	},
	{
		var: "--text-muted",
		label: "Muted Text",
		description: "Secondary / dimmed text colour",
	},
];

// ─── Default values for each custom colour key ────────────────────────
// These are the "reset" values for the stock light/dark mode.

export const DEFAULT_CUSTOM_LIGHT: Record<string, string> = {
	"--background": "#ffffff",
	"--foreground": "#0a0a0a",
	"--primary": "#5469d4",
	"--bg-subtle": "#f5f5f5",
	"--border": "#e5e5e5",
	"--text-muted": "#737373",
};

export const DEFAULT_CUSTOM_DARK: Record<string, string> = {
	"--background": "#1a1b1e",
	"--foreground": "#ededed",
	"--primary": "#6b7fd4",
	"--bg-subtle": "#232428",
	"--border": "#2e2f33",
	"--text-muted": "#888",
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
		"--text-muted": muted,
		"--text-secondary": isDark ? lighten(muted, 0.3) : darken(muted, 0.2),
		"--card": isDark ? lighten(bg, 0.03) : darken(bg, 0.02),
		"--card-foreground": fg,
		"--popover": isDark ? lighten(bg, 0.03) : darken(bg, 0.02),
		"--popover-foreground": fg,
		"--primary": primary,
		"--primary-foreground": isDark ? "#ffffff" : "#ffffff",
		"--secondary": isDark ? lighten(bg, 0.05) : darken(subtle, 0.03),
		"--secondary-foreground": fg,
		"--muted": isDark ? lighten(bg, 0.05) : darken(subtle, 0.02),
		"--muted-foreground": muted,
		"--accent": primary,
		"--accent-foreground": "#ffffff",
		"--accent-soft": `${primary}1a`,
		"--accent-muted": `${primary}66`,
		"--border": border,
		"--input": border,
		"--ring": `${primary}80`,
		"--sidebar": isDark ? subtle : lighten(subtle, 0.03),
		"--sidebar-foreground": fg,
		"--sidebar-primary": primary,
		"--sidebar-primary-foreground": "#ffffff",
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
