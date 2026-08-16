// lib/theme-palette.ts — resolve the app's live theme tokens into
// concrete hex colours for the share-stats image.
//
// The stats image is captured via html-to-image, which serializes the
// DOM into an SVG foreignObject. Chromium's SVG-as-image context
// cannot parse oklch() values, and the app's theme presets store their
// tokens as oklch() strings (see `themes/`), so the image must render
// with RESOLVED hex colours, not `var(--…)` references.
//
// Single source of truth: the CSS custom properties currently applied
// on `document.documentElement` — the same tokens every themed surface
// in the app renders with (theme presets set them via `applyThemeVars`,
// custom themes via `applyThemeVars(presetId='custom', …, customVars)`).
// We read them live at render time and convert each oklch/rgb/hsl value
// to hex via `cssColorToHex` (which resolves through a hidden DOM
// probe — the browser's own var()/oklch resolution — with a manual
// oklch parser fallback). A future theme change automatically flows
// into the exported image with no edits in the stats-image module.
import { useEffect, useMemo, useState } from "react";
import { contrastRatio, cssColorToHex } from "@/lib/color-utils";
import { THEME_APPLIED_EVENT } from "@/themes";

/** Resolved hex palette for the share-stats image. */
export interface StatsThemePalette {
	/** Main page background (--background). */
	background: string;
	/** Card / panel surface (--card). */
	card: string;
	/** Primary text (--foreground). */
	foreground: string;
	/** Secondary / dimmed text (--muted-foreground). */
	mutedForeground: string;
	/** Accent / highlight (--primary). */
	primary: string;
	/** Border / divider (--border). */
	border: string;
	/** Semantic status colours (--success / --warning / --destructive). */
	success: string;
	warning: string;
	destructive: string;
	/** Chart palette (--chart-1 … --chart-5). */
	charts: readonly [string, string, string, string, string];
}

/** Fallback palette used when the DOM / CSS variables are unavailable
 * (SSR, jsdom tests without theme variables). Matches the app's stock
 * dark theme so the export never renders broken/empty colours. */
export const FALLBACK_THEME_PALETTE: StatsThemePalette = {
	background: "#131313",
	card: "#171717",
	foreground: "#fafafa",
	mutedForeground: "#9f9fa9",
	primary: "#193cb8",
	border: "#1f1f1f",
	success: "#22c55e",
	warning: "#f59e0b",
	destructive: "#ef4444",
	charts: ["#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#a78bfa"],
};

/**
 * Read the currently-applied theme tokens into a hex palette.
 *
 * Pure DOM read (no React) so it can be called from tests and from
 * non-component code. Falls back to `FALLBACK_THEME_PALETTE` per-token
 * when a variable is missing or unparseable — the image must never
 * render with `transparent`/broken colours because a token is absent.
 */
export function readThemePalette(): StatsThemePalette {
	const style =
		typeof document !== "undefined"
			? getComputedStyle(document.documentElement)
			: null;

	const read = (name: string, fallback: string): string => {
		if (!style) return fallback;
		const raw = style.getPropertyValue(name).trim();
		if (!raw) return fallback;
		const hex = cssColorToHex(raw);
		// cssColorToHex returns #000000 for unparseable input — treat
		// that as a miss for the background/surface tokens (a genuinely
		// black theme would still set its own --background explicitly).
		if (hex === "#000000" && name !== "--foreground" && name !== "--card") {
			return fallback;
		}
		return hex;
	};

	const chart = (i: number): string =>
		read(`--chart-${i}`, FALLBACK_THEME_PALETTE.charts[i - 1] ?? "#60a5fa");

	return {
		background: read("--background", FALLBACK_THEME_PALETTE.background),
		card: read("--card", FALLBACK_THEME_PALETTE.card),
		foreground: read("--foreground", FALLBACK_THEME_PALETTE.foreground),
		mutedForeground: read(
			"--muted-foreground",
			FALLBACK_THEME_PALETTE.mutedForeground,
		),
		primary: read("--primary", FALLBACK_THEME_PALETTE.primary),
		border: read("--border", FALLBACK_THEME_PALETTE.border),
		success: read("--success", FALLBACK_THEME_PALETTE.success),
		warning: read("--warning", FALLBACK_THEME_PALETTE.warning),
		destructive: read("--destructive", FALLBACK_THEME_PALETTE.destructive),
		charts: [
			chart(1),
			chart(2),
			chart(3),
			chart(4),
			chart(5),
		] as StatsThemePalette["charts"],
	};
}

/**
 * React hook: the resolved palette for the CURRENTLY ACTIVE theme.
 *
 * Subscribes to the `vt:theme-applied` event that `applyThemeVars`
 * dispatches after every theme-application run (preset switch, custom
 * theme edit, mode flip, revert-to-default) so the returned palette
 * object is stable across unrelated re-renders and refreshes exactly
 * when the applied CSS variables change. The palette is read from the
 * live CSS variables on `document.documentElement` — the same tokens
 * every themed surface renders with — so the ground truth is the
 * applied on-screen state, not a cached config value.
 */
export function useThemePalette(): StatsThemePalette {
	// Bump on every theme-applied event; used as the memo key so the
	// palette re-reads exactly when the CSS variables change.
	const [version, setVersion] = useState(0);

	useEffect(() => {
		const onThemeApplied = () => setVersion((v) => v + 1);
		window.addEventListener(THEME_APPLIED_EVENT, onThemeApplied);
		return () =>
			window.removeEventListener(THEME_APPLIED_EVENT, onThemeApplied);
	}, []);

	// `version` is the reactive trigger for re-reading the live CSS
	// variables — it isn't a body value (same documented pattern as
	// App.tsx:183).
	// biome-ignore lint/correctness/useExhaustiveDependencies: version is the reactive trigger for re-reading the live CSS variables
	return useMemo(() => readThemePalette(), [version]);
}

/**
 * Return `accent` when it clears the given minimum WCAG contrast ratio
 * against `background`, otherwise `fallback`. Used for accent-coloured
 * stat values on themed surfaces — if a theme's primary colour happens
 * to be too close to its card background, the value degrades to the
 * (guaranteed-legible) foreground colour instead of becoming unreadable.
 * Minimum for large text / UI is 3:1 (WCAG 1.4.11).
 */
export function legibleOn(
	accent: string,
	background: string,
	fallback: string,
	minRatio = 3,
): string {
	return contrastRatio(accent, background) >= minRatio ? accent : fallback;
}
