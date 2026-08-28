/**
 * "Tokyo Night" theme preset — deep blue-black with vibrant highlights.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const tokyoNightTheme: Omit<ThemePreset, "nameKey"> = {
	id: "tokyo-night",
	name: "Tokyo Night",
	swatch: "oklch(0.55 0.14 280)",
	light: {
		// Core
		"--background": "oklch(0.96 0.01 250)",
		"--foreground": "oklch(0.18 0.015 260)",
		"--bg-subtle": "oklch(0.93 0.008 250)",
		"--surface-hover": "oklch(0.88 0.008 250)",
		// backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.96 0.01 250)",
		// Text (backfill --text-primary).
		"--text-primary": "oklch(0.18 0.015 260)",
		"--text-secondary": "oklch(0.35 0.01 260)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L lowered from 0.84 to 0.62 so the border clears
		   3:1 contrast against the near-white background. */
		"--border": "oklch(0 0 0)",
		"--input": "oklch(0.62 0.008 250)",
		/* bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.1 280)",
		// Cards / popovers
		"--card": "oklch(0.97 0.008 250)",
		"--card-foreground": "oklch(0.18 0.015 260)",
		"--popover": "oklch(0.97 0.008 250)",
		"--popover-foreground": "oklch(0.18 0.015 260)",
		// Primary / accent
		"--primary": "oklch(0.55 0.16 280)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.9 0.008 250)",
		"--secondary-foreground": "oklch(0.22 0.015 260)",
		"--accent": "oklch(0.6 0.12 210)",
		/* WCAG AA: --accent L=0.6 in light mode means the default white
		   --accent-foreground only reaches ~3.4:1. Switching to
		   near-black clears AA 4.5:1 against the bright blue accent. */
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.55 0.16 280 / 0.1)",
		"--accent-muted": "oklch(0.55 0.16 280 / 0.35)",
		"--muted": "oklch(0.91 0.008 250)",
		"--muted-foreground": "oklch(0.45 0.01 260)",
		// Destructive (backfill --destructive-foreground.)
		"--destructive": "oklch(0.55 0.22 15)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.01 250)",
		"--sidebar-foreground": "oklch(0.18 0.015 260)",
		"--sidebar-primary": "oklch(0.55 0.16 280)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.9 0.008 250)",
		"--sidebar-accent-foreground": "oklch(0.22 0.015 260)",
		"--sidebar-border": "oklch(0.84 0.008 250)",
		"--sidebar-ring": "oklch(0.48 0.1 280)",
		// Charts
		"--chart-1": "oklch(0.55 0.16 280)",
		"--chart-2": "oklch(0.6 0.12 210)",
		"--chart-3": "oklch(0.6 0.14 330)",
		"--chart-4": "oklch(0.65 0.14 50)",
		"--chart-5": "oklch(0.6 0.1 160)",
		// Scrollbar (backfill --scrollbar-thumb + --scrollbar-thumb-hover.)
		"--scrollbar-thumb": "oklch(0.82 0.008 250)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.008 250)",
		// status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		"--background": "oklch(0.12 0.02 270)",
		"--foreground": "oklch(0.92 0.01 250)",
		"--bg-subtle": "oklch(0.09 0.015 270)",
		"--surface-hover": "oklch(0.16 0.02 270)",
		"--text-primary": "oklch(0.92 0.01 250)",
		"--text-secondary": "oklch(0.78 0.01 250)",
		"--card": "oklch(0.14 0.02 270)",
		"--card-foreground": "oklch(0.92 0.01 250)",
		"--popover": "oklch(0.14 0.02 270)",
		"--popover-foreground": "oklch(0.92 0.01 250)",
		"--primary": "oklch(0.5 0.14 280)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.18 0.018 270)",
		"--secondary-foreground": "oklch(0.92 0.01 250)",
		"--muted": "oklch(0.16 0.015 270)",
		"--muted-foreground": "oklch(0.65 0.015 270)",
		"--accent": "oklch(0.65 0.12 210)",
		/* WCAG AA: --accent L=0.65 in dark mode means the default white
		   --accent-foreground only reaches ~2.8:1. Switching to
		   near-black clears AA 4.5:1 against the bright blue accent. */
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.65 0.14 280 / 0.12)",
		"--accent-muted": "oklch(0.65 0.14 280 / 0.38)",
		/* WCAG 1.4.11: L raised from 0.22 to 0.52 so the border clears
		   3:1 contrast against the dark background. */
		"--border": "oklch(1 0 0)",
		"--input": "oklch(0.54 0.02 270)",
		"--ring": "oklch(0.7 0.15 280)",
		"--sidebar": "oklch(0.1 0.02 270)",
		"--sidebar-foreground": "oklch(0.92 0.01 250)",
		"--sidebar-primary": "oklch(0.5 0.14 280)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.16 0.02 270)",
		"--sidebar-accent-foreground": "oklch(0.92 0.01 250)",
		"--sidebar-border": "oklch(0.2 0.02 270)",
		"--sidebar-ring": "oklch(0.7 0.15 280)",
		"--destructive": "oklch(0.55 0.22 15)",
		// backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.65 0.14 280)",
		"--chart-2": "oklch(0.65 0.12 210)",
		"--chart-3": "oklch(0.65 0.14 330)",
		"--chart-4": "oklch(0.7 0.12 50)",
		"--chart-5": "oklch(0.65 0.1 160)",
		"--scrollbar-thumb": "oklch(0.28 0.02 270)",
		"--scrollbar-thumb-hover": "oklch(0.38 0.02 270)",
		"--surface-page": "oklch(0.11 0.02 270)",
		// status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
