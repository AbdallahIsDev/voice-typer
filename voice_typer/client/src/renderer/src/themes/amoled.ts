/**
 * "Amoled" theme preset — true-black backgrounds for OLED displays.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const amoledTheme: ThemePreset = {
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
};
