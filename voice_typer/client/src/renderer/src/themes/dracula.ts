/**
 * "Dracula" theme preset — rich purples and deep magentas.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 */
import type { ThemePreset } from "../themes";

export const draculaTheme: Omit<ThemePreset, "nameKey"> = {
	id: "dracula",
	name: "Dracula",
	swatch: "oklch(0.5 0.16 320)",
	light: {
		// Core
		"--background": "oklch(0.96 0.01 320)",
		"--foreground": "oklch(0.18 0.015 320)",
		"--bg-subtle": "oklch(0.93 0.012 320)",
		"--surface-hover": "oklch(0.89 0.014 320)",
		// PVT-001: backfill --surface-page (was missing in light, present in dark).
		"--surface-page": "oklch(0.96 0.01 320)",
		// Text (PVT-001: backfill --text-primary).
		"--text-primary": "oklch(0.18 0.015 320)",
		"--text-secondary": "oklch(0.38 0.018 320)",
		// Borders / inputs / rings
		"--border": "oklch(0.86 0.012 320)",
		"--input": "oklch(0.86 0.012 320)",
		/* PVT-044: bump L from 0.6 to 0.48 so the focus ring (combined with
		   focus-visible:ring-ring/30) clears WCAG 1.4.11's 3:1 minimum. */
		"--ring": "oklch(0.48 0.12 320)",
		// Cards / popovers (PVT-001: backfill --card-foreground + --popover-foreground).
		"--card": "oklch(0.97 0.01 320)",
		"--card-foreground": "oklch(0.18 0.015 320)",
		"--popover": "oklch(0.97 0.01 320)",
		"--popover-foreground": "oklch(0.18 0.015 320)",
		// Primary / accent
		"--primary": "oklch(0.5 0.18 320)",
		"--primary-foreground": "oklch(0.97 0 0)",
		"--secondary": "oklch(0.9 0.014 320)",
		"--secondary-foreground": "oklch(0.22 0.015 320)",
		"--accent": "oklch(0.5 0.16 320)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.5 0.16 320 / 0.1)",
		"--accent-muted": "oklch(0.5 0.16 320 / 0.35)",
		"--muted": "oklch(0.92 0.012 320)",
		"--muted-foreground": "oklch(0.48 0.02 320)",
		// Destructive (PVT-001/PVT-002: backfill --destructive + --destructive-foreground.)
		"--destructive": "oklch(0.55 0.25 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar (PVT-001: backfill --sidebar-primary, --sidebar-primary-foreground,
		// --sidebar-accent-foreground, --sidebar-ring.)
		"--sidebar": "oklch(0.93 0.012 320)",
		"--sidebar-foreground": "oklch(0.18 0.015 320)",
		"--sidebar-primary": "oklch(0.5 0.18 320)",
		"--sidebar-primary-foreground": "oklch(0.97 0 0)",
		"--sidebar-accent": "oklch(0.88 0.014 320)",
		"--sidebar-accent-foreground": "oklch(0.22 0.015 320)",
		"--sidebar-border": "oklch(0.84 0.012 320)",
		"--sidebar-ring": "oklch(0.48 0.12 320)",
		// Charts
		"--chart-1": "oklch(0.6 0.18 320)",
		"--chart-2": "oklch(0.55 0.15 280)",
		"--chart-3": "oklch(0.5 0.12 340)",
		"--chart-4": "oklch(0.55 0.1 240)",
		"--chart-5": "oklch(0.6 0.14 30)",
		// Scrollbar (PVT-001: backfill --scrollbar-thumb + --scrollbar-thumb-hover.)
		"--scrollbar-thumb": "oklch(0.82 0.012 320)",
		"--scrollbar-thumb-hover": "oklch(0.72 0.012 320)",
	},
	dark: {
		"--background": "oklch(0.15 0.015 320)",
		"--foreground": "oklch(0.9 0.01 320)",
		"--bg-subtle": "oklch(0.11 0.012 320)",
		"--surface-hover": "oklch(0.19 0.014 320)",
		// PVT-001: backfill --surface-page so dark matches light coverage.
		"--surface-page": "oklch(0.15 0.015 320)",
		"--text-primary": "oklch(0.9 0.01 320)",
		"--text-secondary": "oklch(0.75 0.015 320)",
		"--card": "oklch(0.17 0.015 320)",
		"--card-foreground": "oklch(0.9 0.01 320)",
		"--popover": "oklch(0.17 0.015 320)",
		"--popover-foreground": "oklch(0.9 0.01 320)",
		"--primary": "oklch(0.6 0.16 320)",
		"--primary-foreground": "oklch(0.1 0 0)",
		"--secondary": "oklch(0.22 0.014 320)",
		"--secondary-foreground": "oklch(0.9 0.01 320)",
		"--muted": "oklch(0.2 0.012 320)",
		"--muted-foreground": "oklch(0.65 0.02 320)",
		"--accent": "oklch(0.6 0.14 320)",
		"--accent-foreground": "oklch(0.97 0 0)",
		"--accent-soft": "oklch(0.5 0.16 320 / 0.12)",
		"--accent-muted": "oklch(0.5 0.16 320 / 0.4)",
		"--border": "oklch(0.24 0.015 320)",
		"--input": "oklch(0.26 0.015 320)",
		"--ring": "oklch(0.7 0.15 320)",
		"--sidebar": "oklch(0.12 0.015 320)",
		"--sidebar-foreground": "oklch(0.9 0.01 320)",
		"--sidebar-primary": "oklch(0.6 0.16 320)",
		"--sidebar-primary-foreground": "oklch(0.1 0 0)",
		"--sidebar-accent": "oklch(0.2 0.014 320)",
		"--sidebar-accent-foreground": "oklch(0.9 0.01 320)",
		"--sidebar-border": "oklch(0.2 0.015 320)",
		"--sidebar-ring": "oklch(0.7 0.15 320)",
		"--destructive": "oklch(0.55 0.25 27)",
		// PVT-002: backfill --destructive-foreground so destructive button text
		// is readable without relying on the stylesheet default.
		"--destructive-foreground": "oklch(0.97 0 0)",
		"--chart-1": "oklch(0.65 0.16 320)",
		"--chart-2": "oklch(0.6 0.12 280)",
		"--chart-3": "oklch(0.55 0.1 340)",
		"--chart-4": "oklch(0.6 0.08 240)",
		"--chart-5": "oklch(0.65 0.12 30)",
		"--scrollbar-thumb": "oklch(0.3 0.015 320)",
		"--scrollbar-thumb-hover": "oklch(0.4 0.015 320)",
	},
};
