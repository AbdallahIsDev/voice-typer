/**
 * "Amoled" theme preset — true-black backgrounds for OLED displays.
 *
 * See ``themes.ts`` for the ``ThemePreset`` interface and how presets
 * are consumed.
 *
 * CR-061: light and dark now define the SAME superset of CSS vars.
 * Previously the light map covered only 16 vars while the dark map
 * covered 31 — components reading e.g. ``--sidebar-ring`` in light
 * mode fell through to the stylesheet default, producing an
 * inconsistent accent colour when toggling schemes. The light map
 * now mirrors the dark map's var coverage so the visual contract
 * holds in both colour schemes.
 */
import type { ThemePreset } from "../themes";

export const amoledTheme: ThemePreset = {
	id: "amoled",
	name: "Amoled",
	description:
		"True-black backgrounds for deep contrast on OLED displays. Pure blacks save battery on AMOLED screens.",
	swatch: "#000000",
	light: {
		// Core
		"--background": "oklch(1 0 0)",
		"--foreground": "oklch(0.1 0 0)",
		"--bg-subtle": "oklch(0.96 0 0)",
		"--surface-hover": "oklch(0.92 0 0)",
		"--surface-page": "oklch(1 0 0)",
		// Text
		"--text-primary": "oklch(0.1 0 0)",
		"--text-muted": "oklch(0.5 0 0)",
		"--text-secondary": "oklch(0.4 0 0)",
		// Cards / popovers
		"--card": "oklch(1 0 0)",
		"--card-foreground": "oklch(0.1 0 0)",
		"--popover": "oklch(1 0 0)",
		"--popover-foreground": "oklch(0.1 0 0)",
		// Primary / accent
		"--primary": "oklch(0.488 0.243 264.376)",
		"--primary-foreground": "oklch(0.97 0.014 254.604)",
		"--accent": "oklch(0.488 0.243 264.376)",
		"--accent-foreground": "oklch(0.97 0.014 254.604)",
		"--accent-soft": "oklch(0.488 0.243 264.376 / 0.08)",
		"--accent-muted": "oklch(0.488 0.243 264.376 / 0.3)",
		// Secondary / muted
		"--secondary": "oklch(0.92 0 0)",
		"--secondary-foreground": "oklch(0.1 0 0)",
		"--muted": "oklch(0.96 0 0)",
		/* PVT-042: bump L from 0.5 to 0.48 so --muted-foreground clears WCAG AA
		   4.5:1 against the white background. */
		"--muted-foreground": "oklch(0.48 0 0)",
		// Borders / inputs / rings
		"--border": "oklch(0.9 0 0)",
		"--input": "oklch(0.9 0 0)",
		/* PVT-045: drop the `/0.5` alpha — when combined with tailwind's
		   focus-visible:ring-ring/30 the effective opacity fell to 15%, rendering
		   the focus indicator invisible on white. Use the opaque primary blue
		   (matches PVT-044's default light --ring value) which already clears
		   3:1 contrast at /30 alpha. */
		"--ring": "oklch(0.488 0.243 264.376)",
		// Destructive
		"--destructive": "oklch(0.58 0.22 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.98 0 0)",
		"--sidebar-foreground": "oklch(0.1 0 0)",
		"--sidebar-primary": "oklch(0.488 0.243 264.376)",
		"--sidebar-primary-foreground": "oklch(0.97 0.014 254.604)",
		"--sidebar-accent": "oklch(0.92 0 0)",
		"--sidebar-accent-foreground": "oklch(0.1 0 0)",
		"--sidebar-border": "oklch(0.9 0 0)",
		"--sidebar-ring": "oklch(0.488 0.243 264.376 / 0.5)",
		// Charts
		"--chart-1": "oklch(0.55 0.22 264)",
		"--chart-2": "oklch(0.6 0.18 145)",
		"--chart-3": "oklch(0.55 0.18 30)",
		"--chart-4": "oklch(0.55 0.18 290)",
		"--chart-5": "oklch(0.6 0.2 200)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.85 0 0)",
		"--scrollbar-thumb-hover": "oklch(0.75 0 0)",
	},
	dark: {
		// Core
		"--background": "oklch(0 0 0)",
		"--foreground": "oklch(0.985 0 0)",
		"--bg-subtle": "oklch(0.04 0 0)",
		"--surface-hover": "oklch(0.1 0 0)",
		"--surface-page": "oklch(0 0 0)",
		// Text
		"--text-primary": "oklch(0.985 0 0)",
		"--text-muted": "oklch(0.55 0 0)",
		"--text-secondary": "oklch(0.8 0 0)",
		// Cards / popovers
		"--card": "oklch(0.04 0 0)",
		"--card-foreground": "oklch(0.985 0 0)",
		"--popover": "oklch(0.04 0 0)",
		"--popover-foreground": "oklch(0.985 0 0)",
		// Primary / accent (CR-061: added --primary + --primary-foreground
		// so the light/dark var coverage matches; previously the dark map
		// relied on the stylesheet default for these).
		"--primary": "oklch(0.546 0.245 262.881)",
		"--primary-foreground": "oklch(0.97 0.014 254.604)",
		"--accent": "oklch(0.546 0.245 262.881)",
		"--accent-foreground": "oklch(0.97 0.014 254.604)",
		"--accent-soft": "oklch(0.424 0.199 265.638 / 0.08)",
		"--accent-muted": "oklch(0.546 0.245 262.881 / 0.3)",
		// Secondary / muted
		"--secondary": "oklch(0.1 0 0)",
		"--secondary-foreground": "oklch(0.985 0 0)",
		"--muted": "oklch(0.08 0 0)",
		"--muted-foreground": "oklch(0.55 0 0)",
		// Borders / inputs / rings
		"--border": "oklch(0.15 0 0)",
		"--input": "oklch(0.18 0 0)",
		"--ring": "oklch(0.4 0 0)",
		// Destructive (CR-061: added so dark matches light coverage.)
		"--destructive": "oklch(0.55 0.22 27)",
		"--destructive-foreground": "oklch(0.97 0 0)",
		// Sidebar
		"--sidebar": "oklch(0.02 0 0)",
		"--sidebar-foreground": "oklch(0.985 0 0)",
		"--sidebar-primary": "oklch(0.546 0.245 262.881)",
		"--sidebar-primary-foreground": "oklch(0.97 0.014 254.604)",
		"--sidebar-accent": "oklch(0.08 0 0)",
		"--sidebar-accent-foreground": "oklch(0.985 0 0)",
		"--sidebar-border": "oklch(0.1 0 0)",
		"--sidebar-ring": "oklch(0.4 0 0)",
		// Charts (CR-061: added so dark matches light coverage.)
		"--chart-1": "oklch(0.6 0.22 264)",
		"--chart-2": "oklch(0.65 0.18 145)",
		"--chart-3": "oklch(0.6 0.18 30)",
		"--chart-4": "oklch(0.6 0.18 290)",
		"--chart-5": "oklch(0.65 0.2 200)",
		// Scrollbar
		"--scrollbar-thumb": "oklch(0.2 0 0)",
		"--scrollbar-thumb-hover": "oklch(0.3 0 0)",
	},
};
