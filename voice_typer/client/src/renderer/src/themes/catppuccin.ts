/**
 * "Catppuccin" theme preset — soft warm pastels with mauve, peach, and teal.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const catppuccinTheme: Omit<ThemePreset, "nameKey"> = {
	id: "catppuccin",
	name: "Catppuccin",
	swatch: "oklch(0.65 0.12 330)",
	light: {
		// Core
		"--background": "oklch(0.96 0.012 80)",
		"--foreground": "oklch(0.2 0.015 350)",
		"--bg-subtle": "oklch(0.93 0.01 80)",
		"--surface-hover": "oklch(0.88 0.01 80)",
		// backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.96 0.012 80)",
		// Text (backfill --text-primary).
		"--text-primary": "oklch(0.2 0.015 350)",
		"--text-secondary": "oklch(0.35 0.012 350)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L lowered from 0.85 to 0.62 so the border clears
		   3:1 contrast against the near-white background. */
		"--border": "oklch(0.62 0.01 80)",
		"--input": "oklch(0.62 0.01 80)",
		/* bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.1 330)",
		// Cards / popovers
		"--card": "oklch(0.97 0.01 80)",
		"--card-foreground": "oklch(0.2 0.015 350)",
		"--popover": "oklch(0.97 0.01 80)",
		"--popover-foreground": "oklch(0.2 0.015 350)",
		// Primary / accent
		"--primary": "oklch(0.45 0.14 330)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.9 0.01 80)",
		"--secondary-foreground": "oklch(0.22 0.015 350)",
		"--accent": "oklch(0.55 0.12 190)",
		/* WCAG AA: --accent L=0.55 + chroma 0.12/H=190 means white
		   --accent-foreground only reaches ~4.0:1. Switching to
		   near-black clears AA 4.5:1 against the bright cyan accent. */
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.6 0.14 330 / 0.1)",
		"--accent-muted": "oklch(0.6 0.14 330 / 0.35)",
		"--muted": "oklch(0.91 0.01 80)",
		"--muted-foreground": "oklch(0.48 0.01 350)",
		// Destructive (backfill --destructive-foreground.)
		"--destructive": "oklch(0.55 0.22 30)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.012 80)",
		"--sidebar-foreground": "oklch(0.2 0.015 350)",
		"--sidebar-primary": "oklch(0.45 0.14 330)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.9 0.01 80)",
		"--sidebar-accent-foreground": "oklch(0.22 0.015 350)",
		"--sidebar-border": "oklch(0.84 0.01 80)",
		"--sidebar-ring": "oklch(0.48 0.1 330)",
		// Charts
		"--chart-1": "oklch(0.6 0.14 330)",
		"--chart-2": "oklch(0.55 0.12 190)",
		"--chart-3": "oklch(0.6 0.14 50)",
		"--chart-4": "oklch(0.55 0.1 280)",
		"--chart-5": "oklch(0.6 0.1 150)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.82 0.01 80)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.01 80)",
		// status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		"--background": "oklch(0.14 0.015 340)",
		"--foreground": "oklch(0.92 0.008 80)",
		"--bg-subtle": "oklch(0.11 0.012 340)",
		"--surface-hover": "oklch(0.18 0.015 340)",
		// backfill --surface-page so dark matches light coverage.
		"--surface-page": "oklch(0.14 0.015 340)",
		"--text-primary": "oklch(0.92 0.008 80)",
		"--text-secondary": "oklch(0.76 0.006 80)",
		"--card": "oklch(0.16 0.015 340)",
		"--card-foreground": "oklch(0.92 0.008 80)",
		"--popover": "oklch(0.16 0.015 340)",
		"--popover-foreground": "oklch(0.92 0.008 80)",
		"--primary": "oklch(0.5 0.13 330)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.2 0.015 340)",
		"--secondary-foreground": "oklch(0.92 0.008 80)",
		"--muted": "oklch(0.18 0.012 340)",
		"--muted-foreground": "oklch(0.65 0.008 340)",
		"--accent": "oklch(0.6 0.1 190)",
		/* WCAG AA: --accent L=0.6 in dark mode means the default white
		   --accent-foreground only reaches ~3.4:1. Switching to
		   near-black clears AA 4.5:1 against the bright accent. */
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.65 0.13 330 / 0.12)",
		"--accent-muted": "oklch(0.65 0.13 330 / 0.38)",
		/* WCAG 1.4.11: L raised from 0.23 to 0.52 so the border clears
		   3:1 contrast against the dark background. */
		"--border": "oklch(0.52 0.015 340)",
		"--input": "oklch(0.54 0.015 340)",
		"--ring": "oklch(0.7 0.13 330)",
		"--sidebar": "oklch(0.12 0.015 340)",
		"--sidebar-foreground": "oklch(0.92 0.008 80)",
		"--sidebar-primary": "oklch(0.5 0.13 330)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.18 0.015 340)",
		"--sidebar-accent-foreground": "oklch(0.92 0.008 80)",
		"--sidebar-border": "oklch(0.2 0.015 340)",
		"--sidebar-ring": "oklch(0.7 0.13 330)",
		"--destructive": "oklch(0.55 0.22 30)",
		// backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.65 0.13 330)",
		"--chart-2": "oklch(0.6 0.1 190)",
		"--chart-3": "oklch(0.65 0.12 50)",
		"--chart-4": "oklch(0.6 0.1 280)",
		"--chart-5": "oklch(0.65 0.1 150)",
		"--scrollbar-thumb": "oklch(0.3 0.015 340)",
		"--scrollbar-thumb-hover": "oklch(0.4 0.015 340)",
		// status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
