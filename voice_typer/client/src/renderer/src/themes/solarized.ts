/**
 * "Solarized" theme preset — balanced palette with warm yellows and cool teals.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const solarizedTheme: ThemePreset = {
	id: "solarized",
	name: "Solarized",
	description:
		"A balanced palette with warm yellows and cool teals — designed for readability and reduced eye strain.",
	swatch: "oklch(0.6 0.1 200)",
	light: {
		// Core
		"--background": "oklch(0.96 0.02 90)",
		"--foreground": "oklch(0.2 0.015 250)",
		"--bg-subtle": "oklch(0.93 0.02 90)",
		"--surface-hover": "oklch(0.89 0.02 90)",
		// PVT-001: backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.96 0.02 90)",
		// Text (PVT-001: backfill --text-primary).
		"--text-primary": "oklch(0.2 0.015 250)",
		"--text-muted": "oklch(0.45 0.01 250)",
		"--text-secondary": "oklch(0.35 0.01 250)",
		// Borders / inputs / rings
		"--border": "oklch(0.84 0.015 90)",
		"--input": "oklch(0.84 0.015 90)",
		/* PVT-044: bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.08 200)",
		// Cards / popovers
		"--card": "oklch(0.97 0.02 90)",
		"--card-foreground": "oklch(0.2 0.015 250)",
		"--popover": "oklch(0.97 0.02 90)",
		"--popover-foreground": "oklch(0.2 0.015 250)",
		// Primary / accent
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
		// Destructive (PVT-001/PVT-002: backfill --destructive + --destructive-foreground.)
		"--destructive": "oklch(0.55 0.25 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.02 90)",
		"--sidebar-foreground": "oklch(0.2 0.015 250)",
		"--sidebar-primary": "oklch(0.55 0.1 200)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.9 0.015 90)",
		"--sidebar-accent-foreground": "oklch(0.22 0.015 250)",
		"--sidebar-border": "oklch(0.85 0.015 90)",
		"--sidebar-ring": "oklch(0.6 0.08 200)",
		// Charts
		"--chart-1": "oklch(0.6 0.12 200)",
		"--chart-2": "oklch(0.55 0.1 160)",
		"--chart-3": "oklch(0.5 0.08 100)",
		"--chart-4": "oklch(0.55 0.1 50)",
		"--chart-5": "oklch(0.6 0.1 340)",
		// Scrollbar (PVT-001: backfill --scrollbar-thumb + --scrollbar-thumb-hover.)
		"--scrollbar-thumb": "oklch(0.82 0.015 90)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.015 90)",
	},
	dark: {
		"--background": "oklch(0.15 0.015 240)",
		"--foreground": "oklch(0.9 0.015 90)",
		"--bg-subtle": "oklch(0.12 0.012 240)",
		"--surface-hover": "oklch(0.19 0.015 240)",
		// PVT-001: backfill --surface-page so dark matches light coverage.
		"--surface-page": "oklch(0.15 0.015 240)",
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
		// PVT-002: backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.65 0.12 200)",
		"--chart-2": "oklch(0.6 0.1 160)",
		"--chart-3": "oklch(0.55 0.08 100)",
		"--chart-4": "oklch(0.6 0.1 50)",
		"--chart-5": "oklch(0.65 0.1 340)",
		"--scrollbar-thumb": "oklch(0.3 0.015 240)",
		"--scrollbar-thumb-hover": "oklch(0.4 0.015 240)",
	},
};
