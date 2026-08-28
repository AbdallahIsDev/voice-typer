/**
 * "Sepia" theme preset — warm amber tones and cream backgrounds.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 *
 * light and dark now define the SAME superset of CSS vars.
 * Previously the dark map defined ``--destructive``, ``--destructive-foreground``,
 * ``--scrollbar-thumb``, ``--scrollbar-thumb-hover``, ``--surface-page``,
 * and ``--text-primary`` that the light map did not — components
 * reading those vars in light mode silently fell back to the
 * stylesheet default. Both maps now cover the same key set.
 */
import type { ThemePreset } from "../themes";

export const sepiaTheme: Omit<ThemePreset, "nameKey"> = {
	id: "sepia",
	name: "Sepia",
	swatch: "oklch(0.6 0.08 50)",
	light: {
		// Core
		"--background": "oklch(0.96 0.025 75)",
		"--foreground": "oklch(0.18 0.025 40)",
		"--bg-subtle": "oklch(0.93 0.02 75)",
		"--surface-hover": "oklch(0.89 0.02 75)",
		"--surface-page": "oklch(0.96 0.025 75)",
		// Text
		"--text-primary": "oklch(0.18 0.025 40)",
		"--text-secondary": "oklch(0.35 0.02 40)",
		// Cards / popovers
		"--card": "oklch(0.97 0.025 75)",
		"--card-foreground": "oklch(0.18 0.025 40)",
		"--popover": "oklch(0.97 0.025 75)",
		"--popover-foreground": "oklch(0.18 0.025 40)",
		// Primary / accent
		"--primary": "oklch(0.55 0.1 50)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--accent": "oklch(0.55 0.1 50)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.55 0.1 50 / 0.1)",
		"--accent-muted": "oklch(0.55 0.1 50 / 0.35)",
		// Secondary / muted
		"--secondary": "oklch(0.9 0.02 75)",
		"--secondary-foreground": "oklch(0.22 0.025 40)",
		"--muted": "oklch(0.92 0.02 75)",
		"--muted-foreground": "oklch(0.45 0.02 40)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L lowered from 0.84 to 0.62 so the border clears
		   3:1 contrast against the near-white background. */
		"--border": "oklch(0 0 0)",
		"--input": "oklch(0.62 0.02 75)",
		/* bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.08 50)",
		// Destructive (added so light matches dark coverage.)
		"--destructive": "oklch(0.55 0.22 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.02 75)",
		"--sidebar-foreground": "oklch(0.18 0.025 40)",
		"--sidebar-primary": "oklch(0.55 0.1 50)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.9 0.02 75)",
		"--sidebar-accent-foreground": "oklch(0.22 0.025 40)",
		"--sidebar-border": "oklch(0.85 0.02 75)",
		"--sidebar-ring": "oklch(0.48 0.08 50)",
		// Charts
		"--chart-1": "oklch(0.6 0.12 50)",
		"--chart-2": "oklch(0.55 0.1 160)",
		"--chart-3": "oklch(0.5 0.08 280)",
		"--chart-4": "oklch(0.55 0.1 200)",
		"--chart-5": "oklch(0.6 0.12 30)",
		// Scrollbar (added so light matches dark coverage.)
		"--scrollbar-thumb": "oklch(0.78 0.02 75)",
		"--scrollbar-thumb-hover": "oklch(0.7 0.02 75)",
		// status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		// Core
		"--background": "oklch(0.14 0.02 40)",
		"--foreground": "oklch(0.93 0.015 75)",
		"--bg-subtle": "oklch(0.11 0.015 40)",
		"--surface-hover": "oklch(0.18 0.02 40)",
		"--surface-page": "oklch(0.14 0.02 40)",
		// Text
		"--text-primary": "oklch(0.93 0.015 75)",
		"--text-secondary": "oklch(0.75 0.01 75)",
		// Cards / popovers
		"--card": "oklch(0.16 0.02 40)",
		"--card-foreground": "oklch(0.93 0.015 75)",
		"--popover": "oklch(0.16 0.02 40)",
		"--popover-foreground": "oklch(0.93 0.015 75)",
		// Primary / accent
		"--primary": "oklch(0.6 0.1 50)",
		"--primary-foreground": "oklch(0.1 0 0)",
		/* WCAG AA: --accent L=0.6 in dark mode means the default white
		   --accent-foreground only reaches ~3.7:1. Switching to
		   near-black clears AA 4.5:1 against the bright accent. */
		"--accent": "oklch(0.6 0.08 50)",
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.55 0.1 50 / 0.12)",
		"--accent-muted": "oklch(0.55 0.1 50 / 0.4)",
		// Secondary / muted
		"--secondary": "oklch(0.22 0.02 40)",
		"--secondary-foreground": "oklch(0.93 0.015 75)",
		"--muted": "oklch(0.2 0.015 40)",
		"--muted-foreground": "oklch(0.65 0.015 40)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L raised from 0.22 to 0.52 so the border clears
		   3:1 contrast against the dark background. */
		"--border": "oklch(1 0 0)",
		"--input": "oklch(0.54 0.02 40)",
		"--ring": "oklch(0.7 0.08 60)",
		// Destructive (added --destructive-foreground so dark
		// matches light coverage; previously only --destructive was set.)
		"--destructive": "oklch(0.55 0.25 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.12 0.02 40)",
		"--sidebar-foreground": "oklch(0.93 0.015 75)",
		"--sidebar-primary": "oklch(0.6 0.1 50)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.18 0.02 40)",
		"--sidebar-accent-foreground": "oklch(0.93 0.015 75)",
		"--sidebar-border": "oklch(0.2 0.02 40)",
		"--sidebar-ring": "oklch(0.7 0.08 60)",
		// Charts
		"--chart-1": "oklch(0.65 0.12 50)",
		"--chart-2": "oklch(0.6 0.1 160)",
		"--chart-3": "oklch(0.55 0.08 280)",
		"--chart-4": "oklch(0.6 0.1 200)",
		"--chart-5": "oklch(0.65 0.12 30)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.3 0.02 40)",
		"--scrollbar-thumb-hover": "oklch(0.4 0.02 40)",
		// status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
