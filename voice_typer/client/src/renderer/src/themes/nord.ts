/**
 * "Nord" theme preset — cool arctic blues and frosty greys.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 *
 * CR-061: light and dark now define the SAME superset of CSS vars.
 * Previously the light map was missing --card-foreground,
 * --popover-foreground, --surface-page, --text-primary,
 * --sidebar-primary, --sidebar-primary-foreground,
 * --sidebar-accent-foreground, --sidebar-ring, --destructive,
 * --destructive-foreground, --scrollbar-thumb, and
 * --scrollbar-thumb-hover — components reading those vars in light
 * mode silently fell back to the stylesheet default. Both maps now
 * cover the same key set.
 */
import type { ThemePreset } from "../themes";

export const nordTheme: Omit<ThemePreset, "nameKey"> = {
	id: "nord",
	name: "Nord",
	swatch: "oklch(0.5 0.06 240)",
	light: {
		// Core
		"--background": "oklch(0.97 0.006 240)",
		"--foreground": "oklch(0.2 0.015 240)",
		"--bg-subtle": "oklch(0.94 0.008 240)",
		"--surface-hover": "oklch(0.9 0.008 240)",
		"--surface-page": "oklch(0.97 0.006 240)",
		// Text
		"--text-primary": "oklch(0.2 0.015 240)",
		"--text-secondary": "oklch(0.4 0.01 240)",
		// Cards / popovers (CR-061: added --card-foreground +
		// --popover-foreground so light matches dark coverage.)
		"--card": "oklch(0.98 0.006 240)",
		"--card-foreground": "oklch(0.2 0.015 240)",
		"--popover": "oklch(0.98 0.006 240)",
		"--popover-foreground": "oklch(0.2 0.015 240)",
		// Primary / accent
		"--primary": "oklch(0.5 0.08 240)",
		"--primary-foreground": "oklch(0.97 0.014 254.604)",
		"--accent": "oklch(0.45 0.08 240)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.5 0.08 240 / 0.1)",
		"--accent-muted": "oklch(0.5 0.08 240 / 0.35)",
		// Secondary / muted
		"--secondary": "oklch(0.92 0.006 240)",
		"--secondary-foreground": "oklch(0.25 0.01 240)",
		"--muted": "oklch(0.93 0.006 240)",
		/* PVT-042: bump L from 0.5 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the near-white background. */
		"--muted-foreground": "oklch(0.48 0.01 240)",
		// Borders / inputs / rings
		"--border": "oklch(0.88 0.008 240)",
		"--input": "oklch(0.88 0.008 240)",
		/* PVT-044: bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.06 240)",
		// Destructive (CR-061: added so light matches dark coverage.)
		"--destructive": "oklch(0.55 0.22 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar (CR-061: added --sidebar-primary,
		// --sidebar-primary-foreground, --sidebar-accent-foreground,
		// --sidebar-ring so light matches dark coverage.)
		"--sidebar": "oklch(0.94 0.008 240)",
		"--sidebar-foreground": "oklch(0.2 0.015 240)",
		"--sidebar-primary": "oklch(0.5 0.08 240)",
		"--sidebar-primary-foreground": "oklch(0.97 0.014 254.604)",
		"--sidebar-accent": "oklch(0.9 0.008 240)",
		"--sidebar-accent-foreground": "oklch(0.25 0.01 240)",
		"--sidebar-border": "oklch(0.86 0.008 240)",
		"--sidebar-ring": "oklch(0.48 0.06 240)",
		// Charts
		"--chart-1": "oklch(0.6 0.15 240)",
		"--chart-2": "oklch(0.55 0.1 200)",
		"--chart-3": "oklch(0.5 0.08 280)",
		"--chart-4": "oklch(0.55 0.1 160)",
		"--chart-5": "oklch(0.6 0.12 40)",
		// Scrollbar (CR-061: added so light matches dark coverage.)
		"--scrollbar-thumb": "oklch(0.82 0.008 240)",
		"--scrollbar-thumb-hover": "oklch(0.74 0.008 240)",
	},
	dark: {
		// Core (CR-061: added --surface-page so dark matches light coverage.)
		"--background": "oklch(0.18 0.01 240)",
		"--foreground": "oklch(0.92 0.008 240)",
		"--bg-subtle": "oklch(0.14 0.008 240)",
		"--surface-hover": "oklch(0.22 0.008 240)",
		"--surface-page": "oklch(0.18 0.01 240)",
		// Text
		"--text-primary": "oklch(0.92 0.008 240)",
		"--text-secondary": "oklch(0.8 0.008 240)",
		// Cards / popovers
		"--card": "oklch(0.2 0.01 240)",
		"--card-foreground": "oklch(0.92 0.008 240)",
		"--popover": "oklch(0.2 0.01 240)",
		"--popover-foreground": "oklch(0.92 0.008 240)",
		// Primary / accent
		"--primary": "oklch(0.6 0.08 240)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--accent": "oklch(0.6 0.08 240)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.6 0.08 240 / 0.12)",
		"--accent-muted": "oklch(0.6 0.08 240 / 0.4)",
		// Secondary / muted
		"--secondary": "oklch(0.24 0.008 240)",
		"--secondary-foreground": "oklch(0.92 0.008 240)",
		"--muted": "oklch(0.22 0.008 240)",
		"--muted-foreground": "oklch(0.6 0.01 240)",
		// Borders / inputs / rings
		"--border": "oklch(0.26 0.01 240)",
		"--input": "oklch(0.28 0.01 240)",
		"--ring": "oklch(0.7 0.1 240)",
		// Destructive (CR-061: added --destructive-foreground so dark
		// matches light coverage; previously only --destructive was set.)
		"--destructive": "oklch(0.55 0.25 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.15 0.01 240)",
		"--sidebar-foreground": "oklch(0.92 0.008 240)",
		"--sidebar-primary": "oklch(0.6 0.08 240)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.22 0.008 240)",
		"--sidebar-accent-foreground": "oklch(0.92 0.008 240)",
		"--sidebar-border": "oklch(0.22 0.01 240)",
		"--sidebar-ring": "oklch(0.7 0.1 240)",
		// Charts
		"--chart-1": "oklch(0.65 0.12 240)",
		"--chart-2": "oklch(0.6 0.08 200)",
		"--chart-3": "oklch(0.55 0.06 280)",
		"--chart-4": "oklch(0.6 0.08 160)",
		"--chart-5": "oklch(0.65 0.1 40)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.3 0.01 240)",
		"--scrollbar-thumb-hover": "oklch(0.4 0.01 240)",
	},
};
