/**
 * "Ayu" theme preset — warm amber and blue-grey tones with soft contrast.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const ayuTheme: Omit<ThemePreset, "nameKey"> = {
	id: "ayu",
	name: "Ayu",
	swatch: "oklch(0.7 0.14 70)",
	light: {
		// Core
		"--background": "oklch(0.97 0.012 85)",
		"--foreground": "oklch(0.22 0.02 240)",
		"--bg-subtle": "oklch(0.93 0.01 85)",
		"--surface-hover": "oklch(0.88 0.01 85)",
		// backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.97 0.012 85)",
		// Text (backfill --text-primary).
		"--text-primary": "oklch(0.22 0.02 240)",
		"--text-secondary": "oklch(0.4 0.015 240)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L lowered from 0.84 to 0.62 so the border clears
		   3:1 contrast against the near-white background. */
		"--border": "oklch(0.62 0.012 85)",
		"--input": "oklch(0.62 0.012 85)",
		/* bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.12 70)",
		// Cards / popovers
		"--card": "oklch(0.98 0.01 85)",
		"--card-foreground": "oklch(0.22 0.02 240)",
		"--popover": "oklch(0.98 0.01 85)",
		"--popover-foreground": "oklch(0.22 0.02 240)",
		// Primary / accent
		"--primary": "oklch(0.65 0.16 70)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--secondary": "oklch(0.9 0.012 85)",
		"--secondary-foreground": "oklch(0.25 0.015 240)",
		/* WCAG AA: --accent L=0.55 + chroma 0.1/H=210 in light mode means
		   white --accent-foreground only reaches ~4.2:1. Raising the
		   accent L to 0.57 and switching to near-black foreground clears
		   AA 4.5:1 against the bright blue accent. */
		"--accent": "oklch(0.57 0.1 210)",
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.55 0.1 210 / 0.1)",
		"--accent-muted": "oklch(0.65 0.16 70 / 0.3)",
		"--muted": "oklch(0.92 0.01 85)",
		/* bump L from 0.52 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the near-white background. */
		"--muted-foreground": "oklch(0.48 0.015 240)",
		// Destructive (backfill --destructive-foreground.)
		"--destructive": "oklch(0.55 0.2 30)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.012 85)",
		"--sidebar-foreground": "oklch(0.22 0.02 240)",
		"--sidebar-primary": "oklch(0.65 0.16 70)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.9 0.012 85)",
		"--sidebar-accent-foreground": "oklch(0.25 0.015 240)",
		"--sidebar-border": "oklch(0.84 0.012 85)",
		"--sidebar-ring": "oklch(0.48 0.12 70)",
		// Charts
		"--chart-1": "oklch(0.65 0.16 70)",
		"--chart-2": "oklch(0.55 0.1 210)",
		"--chart-3": "oklch(0.6 0.12 160)",
		"--chart-4": "oklch(0.6 0.14 300)",
		"--chart-5": "oklch(0.65 0.12 30)",
		// Scrollbar (backfill --scrollbar-thumb + --scrollbar-thumb-hover.)
		"--scrollbar-thumb": "oklch(0.82 0.012 85)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.012 85)",
		// status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		"--background": "oklch(0.12 0.015 255)",
		"--foreground": "oklch(0.92 0.01 85)",
		"--bg-subtle": "oklch(0.09 0.012 255)",
		"--surface-hover": "oklch(0.16 0.015 255)",
		// backfill --surface-page so dark matches light coverage.
		"--surface-page": "oklch(0.12 0.015 255)",
		"--text-primary": "oklch(0.92 0.01 85)",
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
		"--muted-foreground": "oklch(0.65 0.01 240)",
		"--accent": "oklch(0.6 0.1 210)",
		/* WCAG AA: --accent L=0.6 in dark mode means the default white
		   --accent-foreground only reaches ~3.5:1. Switching to
		   near-black clears AA 4.5:1 against the bright accent. */
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.6 0.1 210 / 0.12)",
		"--accent-muted": "oklch(0.7 0.16 70 / 0.35)",
		/* WCAG 1.4.11: L raised from 0.22 to 0.52 so the border clears
		   3:1 contrast against the dark background. */
		"--border": "oklch(0.52 0.015 255)",
		"--input": "oklch(0.54 0.015 255)",
		"--ring": "oklch(0.7 0.15 70)",
		"--sidebar": "oklch(0.1 0.015 255)",
		"--sidebar-foreground": "oklch(0.92 0.01 85)",
		"--sidebar-primary": "oklch(0.7 0.16 70)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.16 0.015 255)",
		"--sidebar-accent-foreground": "oklch(0.92 0.01 85)",
		"--sidebar-border": "oklch(0.2 0.015 255)",
		"--sidebar-ring": "oklch(0.7 0.15 70)",
		"--destructive": "oklch(0.55 0.2 30)",
		// backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.7 0.16 70)",
		"--chart-2": "oklch(0.6 0.1 210)",
		"--chart-3": "oklch(0.65 0.12 160)",
		"--chart-4": "oklch(0.65 0.14 300)",
		"--chart-5": "oklch(0.7 0.12 30)",
		"--scrollbar-thumb": "oklch(0.28 0.015 255)",
		"--scrollbar-thumb-hover": "oklch(0.38 0.015 255)",
		// status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
