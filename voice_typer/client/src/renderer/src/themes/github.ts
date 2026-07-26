/**
 * "GitHub" theme preset — clean neutral greys with blue accents.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const githubTheme: Omit<ThemePreset, "nameKey"> = {
	id: "github",
	name: "GitHub",
	swatch: "oklch(0.5 0.12 260)",
	light: {
		// Core
		"--background": "oklch(0.99 0 0)",
		"--foreground": "oklch(0.12 0.008 0)",
		"--bg-subtle": "oklch(0.96 0.004 0)",
		"--surface-hover": "oklch(0.92 0.004 0)",
		"--surface-page": "oklch(0.99 0 0)",
		// Text
		"--text-primary": "oklch(0.12 0.008 0)",
		"--text-secondary": "oklch(0.38 0.006 0)",
		// Cards / popovers
		"--card": "oklch(1 0 0)",
		"--card-foreground": "oklch(0.12 0.008 0)",
		"--popover": "oklch(1 0 0)",
		"--popover-foreground": "oklch(0.12 0.008 0)",
		// Primary / accent
		"--primary": "oklch(0.5 0.14 260)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.94 0.004 0)",
		"--secondary-foreground": "oklch(0.22 0.008 0)",
		/* PVT-042: bump L from 0.52 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the near-white background. */
		"--muted": "oklch(0.95 0.004 0)",
		"--muted-foreground": "oklch(0.48 0.006 0)",
		"--accent": "oklch(0.45 0.12 260)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.5 0.14 260 / 0.1)",
		"--accent-muted": "oklch(0.5 0.14 260 / 0.3)",
		// Borders / inputs / rings
		"--border": "oklch(0.88 0.004 0)",
		"--input": "oklch(0.88 0.004 0)",
		/* PVT-044: bump L from 0.55 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.1 260)",
		// Destructive (PVT-002: backfill --destructive-foreground.)
		"--destructive": "oklch(0.55 0.2 30)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.96 0.004 0)",
		"--sidebar-foreground": "oklch(0.12 0.008 0)",
		"--sidebar-primary": "oklch(0.5 0.14 260)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.93 0.004 0)",
		"--sidebar-accent-foreground": "oklch(0.22 0.008 0)",
		"--sidebar-border": "oklch(0.85 0.004 0)",
		"--sidebar-ring": "oklch(0.48 0.1 260)",
		// Charts
		"--chart-1": "oklch(0.55 0.14 260)",
		"--chart-2": "oklch(0.5 0.1 200)",
		"--chart-3": "oklch(0.5 0.12 320)",
		"--chart-4": "oklch(0.55 0.12 160)",
		"--chart-5": "oklch(0.6 0.14 40)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.82 0.004 0)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.004 0)",
		// NH-16: status tokens for light mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.62 0.17 149)",
		"--warning": "oklch(0.7 0.16 70)",
		"--info": "oklch(0.62 0.14 240)",
	},
	dark: {
		"--background": "oklch(0.11 0.006 0)",
		"--foreground": "oklch(0.94 0.004 0)",
		"--bg-subtle": "oklch(0.08 0.006 0)",
		"--surface-hover": "oklch(0.15 0.006 0)",
		"--surface-page": "oklch(0.09 0.006 0)",
		"--text-primary": "oklch(0.94 0.004 0)",
		"--text-secondary": "oklch(0.78 0.004 0)",
		"--card": "oklch(0.13 0.006 0)",
		"--card-foreground": "oklch(0.94 0.004 0)",
		"--popover": "oklch(0.13 0.006 0)",
		"--popover-foreground": "oklch(0.94 0.004 0)",
		"--primary": "oklch(0.6 0.12 260)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--secondary": "oklch(0.16 0.006 0)",
		"--secondary-foreground": "oklch(0.94 0.004 0)",
		"--muted": "oklch(0.15 0.006 0)",
		"--muted-foreground": "oklch(0.65 0.004 0)",
		"--accent": "oklch(0.55 0.1 260)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.6 0.12 260 / 0.12)",
		"--accent-muted": "oklch(0.6 0.12 260 / 0.35)",
		"--border": "oklch(0.2 0.006 0)",
		"--input": "oklch(0.22 0.006 0)",
		"--ring": "oklch(0.7 0.08 260)",
		"--sidebar": "oklch(0.1 0.006 0)",
		"--sidebar-foreground": "oklch(0.94 0.004 0)",
		"--sidebar-primary": "oklch(0.6 0.12 260)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.15 0.006 0)",
		"--sidebar-accent-foreground": "oklch(0.94 0.004 0)",
		"--sidebar-border": "oklch(0.18 0.006 0)",
		"--sidebar-ring": "oklch(0.7 0.08 260)",
		"--destructive": "oklch(0.55 0.2 30)",
		// PVT-002: backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.6 0.12 260)",
		"--chart-2": "oklch(0.55 0.1 200)",
		"--chart-3": "oklch(0.55 0.1 320)",
		"--chart-4": "oklch(0.6 0.1 160)",
		"--chart-5": "oklch(0.65 0.12 40)",
		"--scrollbar-thumb": "oklch(0.25 0.006 0)",
		"--scrollbar-thumb-hover": "oklch(0.35 0.006 0)",
		// NH-16: status tokens for dark mode. Semantic
		// green/amber/blue so status meaning is preserved on
		// this theme's palette (overrides the stylesheet default).
		"--success": "oklch(0.7 0.16 149)",
		"--warning": "oklch(0.78 0.15 70)",
		"--info": "oklch(0.7 0.13 240)",
	},
};
