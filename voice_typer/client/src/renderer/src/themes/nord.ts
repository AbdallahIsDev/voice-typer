import type { ThemePreset } from "../themes";

export const nordTheme: Omit<ThemePreset, "nameKey"> = {
	id: "nord",
	name: "Nord",
	swatch: "oklch(0.5 0.06 240)",
	light: {
		// Core
		"--background": "oklch(0.97 0.006 240)",
		"--foreground": "oklch(0.2 0.015 240)",
		"--surface-subtle": "oklch(0.94 0.008 240)",
		"--surface-hover": "oklch(0.9 0.008 240)",
		// Text
		"--text-secondary": "oklch(0.4 0.01 240)",
		"--surface": "oklch(0.98 0.006 240)",
		// Primary / accent
		"--primary": "oklch(0.5 0.08 240)",
		"--primary-foreground": "oklch(0.97 0.014 254.604)",
		"--accent": "oklch(0.45 0.08 240)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.5 0.08 240 / 0.1)",
		"--accent-muted": "oklch(0.5 0.08 240 / 0.35)",
		"--muted": "oklch(0.93 0.006 240)",
		/* bump L from 0.5 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the near-white background. */
		"--muted-foreground": "oklch(0.48 0.01 240)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L lowered from 0.88 to 0.62 so the border clears
		   3:1 contrast against the near-white background. */
		"--border": "oklch(0 0 0)",
		"--input": "oklch(0.62 0.008 240)",
		/* bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.06 240)",
		// Destructive (added so light matches dark coverage.)
		"--destructive": "oklch(0.55 0.22 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Charts
		"--chart-1": "oklch(0.6 0.15 240)",
		"--chart-2": "oklch(0.55 0.1 200)",
		"--chart-3": "oklch(0.5 0.08 280)",
		"--chart-4": "oklch(0.55 0.1 160)",
		"--chart-5": "oklch(0.6 0.12 40)",
		// Scrollbar (added so light matches dark coverage.)
		"--scrollbar-thumb": "oklch(0.82 0.008 240)",
		"--scrollbar-thumb-hover": "oklch(0.74 0.008 240)",
		// status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		"--background": "oklch(0.18 0.01 240)",
		"--foreground": "oklch(0.92 0.008 240)",
		"--surface-subtle": "oklch(0.14 0.008 240)",
		"--surface-hover": "oklch(0.22 0.008 240)",
		// Text
		"--text-secondary": "oklch(0.8 0.008 240)",
		"--surface": "oklch(0.2 0.01 240)",
		// Primary / accent
		"--primary": "oklch(0.6 0.08 240)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--accent": "oklch(0.6 0.08 240)",
		/* WCAG AA: --accent L=0.6 in dark mode means the default white
		   --accent-foreground only reaches ~3.6:1. Switching to
		   near-black clears AA 4.5:1 against the bright accent. */
		"--accent-foreground": "oklch(0.1 0 0)",
		"--accent-soft": "oklch(0.6 0.08 240 / 0.12)",
		"--accent-muted": "oklch(0.6 0.08 240 / 0.4)",
		"--muted": "oklch(0.22 0.008 240)",
		"--muted-foreground": "oklch(0.6 0.01 240)",
		// Borders / inputs / rings
		/* WCAG 1.4.11: L raised from 0.26 to 0.52 so the border clears
		   3:1 contrast against the dark background. */
		"--border": "oklch(1 0 0)",
		"--input": "oklch(0.54 0.01 240)",
		"--ring": "oklch(0.7 0.1 240)",
		// Destructive (added --destructive-foreground so dark
		"--destructive": "oklch(0.55 0.25 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Charts
		"--chart-1": "oklch(0.65 0.12 240)",
		"--chart-2": "oklch(0.6 0.08 200)",
		"--chart-3": "oklch(0.55 0.06 280)",
		"--chart-4": "oklch(0.6 0.08 160)",
		"--chart-5": "oklch(0.65 0.1 40)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.3 0.01 240)",
		"--scrollbar-thumb-hover": "oklch(0.4 0.01 240)",
		// status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
