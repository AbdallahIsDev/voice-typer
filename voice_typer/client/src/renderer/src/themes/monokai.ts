/**
 * "Monokai" theme preset — high-contrast dark base with vivid accents.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const monokaiTheme: Omit<ThemePreset, "nameKey"> = {
	id: "monokai",
	name: "Monokai",
	swatch: "oklch(0.75 0.15 100)",
	light: {
		// Core
		"--background": "oklch(0.97 0.008 85)",
		"--foreground": "oklch(0.2 0.01 0)",
		"--bg-subtle": "oklch(0.93 0.008 80)",
		"--surface-hover": "oklch(0.89 0.008 80)",
		// backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.97 0.008 85)",
		// Text (backfill --text-primary).
		"--text-primary": "oklch(0.2 0.01 0)",
		"--text-secondary": "oklch(0.38 0.008 0)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L lowered from 0.85 to 0.62 so the border clears
		   3:1 contrast against the near-white background. */
		"--border": "oklch(0.62 0.008 80)",
		"--input": "oklch(0.62 0.008 80)",
		/* bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.12 135)",
		// Cards / popovers
		"--card": "oklch(0.98 0.006 85)",
		"--card-foreground": "oklch(0.2 0.01 0)",
		"--popover": "oklch(0.98 0.006 85)",
		"--popover-foreground": "oklch(0.2 0.01 0)",
		// Primary / accent
		"--primary": "oklch(0.68 0.18 135)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--secondary": "oklch(0.91 0.008 80)",
		"--secondary-foreground": "oklch(0.22 0.01 0)",
		"--accent": "oklch(0.75 0.15 100)",
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.75 0.15 100 / 0.12)",
		"--accent-muted": "oklch(0.68 0.18 135 / 0.35)",
		"--muted": "oklch(0.92 0.008 80)",
		/* bump L from 0.52 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the near-white background. */
		"--muted-foreground": "oklch(0.48 0.008 0)",
		// Destructive (backfill --destructive-foreground.)
		/* WCAG AA: --destructive L=0.6 means the default white
		   --destructive-foreground only reaches ~4.0:1. Switching to
		   near-black clears AA 4.5:1 against the bright red. */
		"--destructive": "oklch(0.6 0.2 0)",
		"--destructive-foreground": "oklch(0.1 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.008 80)",
		"--sidebar-foreground": "oklch(0.2 0.01 0)",
		"--sidebar-primary": "oklch(0.68 0.18 135)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.91 0.008 80)",
		"--sidebar-accent-foreground": "oklch(0.22 0.01 0)",
		"--sidebar-border": "oklch(0.84 0.008 80)",
		"--sidebar-ring": "oklch(0.48 0.12 135)",
		// Charts
		"--chart-1": "oklch(0.68 0.18 135)",
		"--chart-2": "oklch(0.75 0.15 100)",
		"--chart-3": "oklch(0.6 0.2 0)",
		"--chart-4": "oklch(0.65 0.12 210)",
		"--chart-5": "oklch(0.72 0.14 70)",
		// Scrollbar (backfill --scrollbar-thumb + --scrollbar-thumb-hover.)
		"--scrollbar-thumb": "oklch(0.82 0.008 80)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.008 80)",
		// status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		"--background": "oklch(0.16 0.008 340)",
		"--foreground": "oklch(0.93 0.008 80)",
		"--bg-subtle": "oklch(0.12 0.006 340)",
		"--surface-hover": "oklch(0.2 0.01 340)",
		"--text-primary": "oklch(0.93 0.008 80)",
		"--text-secondary": "oklch(0.75 0.006 80)",
		"--card": "oklch(0.18 0.008 340)",
		"--card-foreground": "oklch(0.93 0.008 80)",
		"--popover": "oklch(0.18 0.008 340)",
		"--popover-foreground": "oklch(0.93 0.008 80)",
		"--primary": "oklch(0.68 0.18 135)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--secondary": "oklch(0.22 0.01 340)",
		"--secondary-foreground": "oklch(0.93 0.008 80)",
		"--muted": "oklch(0.2 0.008 340)",
		"--muted-foreground": "oklch(0.65 0.006 0)",
		"--accent": "oklch(0.75 0.15 100)",
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.75 0.15 100 / 0.15)",
		"--accent-muted": "oklch(0.68 0.18 135 / 0.35)",
		/* WCAG 1.4.11: L raised from 0.24 to 0.52 so the border clears
		   3:1 contrast against the dark background. */
		"--border": "oklch(0.52 0.01 340)",
		"--input": "oklch(0.54 0.01 340)",
		"--ring": "oklch(0.7 0.15 135)",
		"--sidebar": "oklch(0.12 0.008 340)",
		"--sidebar-foreground": "oklch(0.93 0.008 80)",
		"--sidebar-primary": "oklch(0.68 0.18 135)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.2 0.01 340)",
		"--sidebar-accent-foreground": "oklch(0.93 0.008 80)",
		"--sidebar-border": "oklch(0.22 0.01 340)",
		"--sidebar-ring": "oklch(0.7 0.15 135)",
		"--destructive": "oklch(0.6 0.2 0)",
		/* WCAG AA: --destructive L=0.6 means the default white
		   --destructive-foreground only reaches ~4.0:1. Switching to
		   near-black clears AA 4.5:1 against the bright red. */
		"--destructive-foreground": "oklch(0.1 0 0)",
		"--chart-1": "oklch(0.68 0.18 135)",
		"--chart-2": "oklch(0.75 0.15 100)",
		"--chart-3": "oklch(0.6 0.2 0)",
		"--chart-4": "oklch(0.65 0.12 210)",
		"--chart-5": "oklch(0.72 0.14 70)",
		"--scrollbar-thumb": "oklch(0.28 0.01 340)",
		"--scrollbar-thumb-hover": "oklch(0.38 0.01 340)",
		"--surface-page": "oklch(0.14 0.008 340)",
		// status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
