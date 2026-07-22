/**
 * "Ayu" theme preset — warm amber and blue-grey tones with soft contrast.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const ayuTheme: ThemePreset = {
	id: "ayu",
	name: "Ayu",
	description:
		"Warm amber and blue-grey tones with soft contrast — inspired by the Ayu colour scheme family.",
	swatch: "oklch(0.7 0.14 70)",
	light: {
		// Core
		"--background": "oklch(0.97 0.012 85)",
		"--foreground": "oklch(0.22 0.02 240)",
		"--bg-subtle": "oklch(0.93 0.01 85)",
		"--surface-hover": "oklch(0.88 0.01 85)",
		// PVT-001: backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.97 0.012 85)",
		// Text (PVT-001: backfill --text-primary).
		"--text-primary": "oklch(0.22 0.02 240)",
		"--text-muted": "oklch(0.5 0.015 240)",
		"--text-secondary": "oklch(0.4 0.015 240)",
		// Borders / inputs / rings
		"--border": "oklch(0.84 0.012 85)",
		"--input": "oklch(0.84 0.012 85)",
		/* PVT-044: bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.12 70)",
		// Cards / popovers
		"--card": "oklch(0.98 0.01 85)",
		"--card-foreground": "oklch(0.22 0.02 240)",
		"--popover": "oklch(0.98 0.01 85)",
		"--popover-foreground": "oklch(0.22 0.02 240)",
		// Primary / accent
		"--primary": "oklch(0.65 0.16 70)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.9 0.012 85)",
		"--secondary-foreground": "oklch(0.25 0.015 240)",
		"--accent": "oklch(0.55 0.1 210)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.55 0.1 210 / 0.1)",
		"--accent-muted": "oklch(0.65 0.16 70 / 0.3)",
		"--muted": "oklch(0.92 0.01 85)",
		/* PVT-042: bump L from 0.52 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the near-white background. */
		"--muted-foreground": "oklch(0.48 0.015 240)",
		// Destructive (PVT-002: backfill --destructive-foreground.)
		"--destructive": "oklch(0.55 0.2 30)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.94 0.012 85)",
		"--sidebar-foreground": "oklch(0.22 0.02 240)",
		"--sidebar-primary": "oklch(0.65 0.16 70)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.9 0.012 85)",
		"--sidebar-accent-foreground": "oklch(0.25 0.015 240)",
		"--sidebar-border": "oklch(0.84 0.012 85)",
		"--sidebar-ring": "oklch(0.6 0.12 70)",
		// Charts
		"--chart-1": "oklch(0.65 0.16 70)",
		"--chart-2": "oklch(0.55 0.1 210)",
		"--chart-3": "oklch(0.6 0.12 160)",
		"--chart-4": "oklch(0.6 0.14 300)",
		"--chart-5": "oklch(0.65 0.12 30)",
		// Scrollbar (PVT-001: backfill --scrollbar-thumb + --scrollbar-thumb-hover.)
		"--scrollbar-thumb": "oklch(0.82 0.012 85)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.012 85)",
	},
	dark: {
		"--background": "oklch(0.12 0.015 255)",
		"--foreground": "oklch(0.92 0.01 85)",
		"--bg-subtle": "oklch(0.09 0.012 255)",
		"--surface-hover": "oklch(0.16 0.015 255)",
		// PVT-001: backfill --surface-page so dark matches light coverage.
		"--surface-page": "oklch(0.12 0.015 255)",
		"--text-primary": "oklch(0.92 0.01 85)",
		"--text-muted": "oklch(0.5 0.01 240)",
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
		"--muted-foreground": "oklch(0.5 0.01 240)",
		"--accent": "oklch(0.6 0.1 210)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.6 0.1 210 / 0.12)",
		"--accent-muted": "oklch(0.7 0.16 70 / 0.35)",
		"--border": "oklch(0.22 0.015 255)",
		"--input": "oklch(0.24 0.015 255)",
		"--ring": "oklch(0.5 0.08 70)",
		"--sidebar": "oklch(0.1 0.015 255)",
		"--sidebar-foreground": "oklch(0.92 0.01 85)",
		"--sidebar-primary": "oklch(0.7 0.16 70)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.16 0.015 255)",
		"--sidebar-accent-foreground": "oklch(0.92 0.01 85)",
		"--sidebar-border": "oklch(0.2 0.015 255)",
		"--sidebar-ring": "oklch(0.5 0.08 70)",
		"--destructive": "oklch(0.55 0.2 30)",
		// PVT-002: backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.7 0.16 70)",
		"--chart-2": "oklch(0.6 0.1 210)",
		"--chart-3": "oklch(0.65 0.12 160)",
		"--chart-4": "oklch(0.65 0.14 300)",
		"--chart-5": "oklch(0.7 0.12 30)",
		"--scrollbar-thumb": "oklch(0.28 0.015 255)",
		"--scrollbar-thumb-hover": "oklch(0.38 0.015 255)",
	},
};
