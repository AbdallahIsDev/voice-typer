// lib/theme-contrast.ts — WCAG contrast-ratio helpers extracted from
// ThemeSettingsSection.tsx (partial split).
//
// The actual WCAG math (``contrastRatio`` and its private
// ``_relativeLuminance`` / ``_parseHex`` helpers) already lives in
// ``@/lib/color-utils`` — we re-export ``contrastRatio`` from there
// rather than duplicating the implementation. This module bundles the
// contrast-related constants and helpers that are specific to the
// custom-theme editor: the AA threshold, the hex-input validation
// regexes, and the row → {fg, bg} mapping used by the contrast
// warning in the colour picker grid.
//
// All functions here are pure (no React, no DOM) so they can be
// unit-tested in isolation and reused by any caller.

import { contrastRatio, cssColorToHex } from "@/lib/color-utils";
import {
	type CustomThemeData,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	pickContrastForeground,
} from "@/themes";

// Re-export so consumers can import all contrast-related helpers from a
// single module. ``contrastRatio`` is the only WCAG math function the
// custom-theme editor needs; the lower-level helpers
// (``_relativeLuminance``, ``_parseHex``) stay private to
// ``color-utils``.
export { contrastRatio };

/** WCAG AA normal-text contrast threshold. */
export const CONTRAST_AA_THRESHOLD = 4.5;

// ── Hex input validation regex ───────────────────
// Loose regex (allows partial typing):  #  followed by 0–6 hex digits.
export const HEX_PARTIAL_RE = /^#[0-9a-fA-F]{0,6}$/;
// Strict regex (used for commit-on-blur): # followed by exactly 6 hex digits.
export const HEX_STRICT_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * Pick the foreground (white or black) that yields the higher
 * WCAG 2.1 contrast ratio against ``bgHex``. Used by ``getContrastPair``
 * for the ``--primary`` row so that mid-tone user-chosen primaries
 * (green, amber, teal, pastel) get a readable foreground instead of an
 * unreadable white-on-light-primary pair.
 *
 * Ported from the fix that already lives in ``themes.ts``
 * (``deriveCustomVars`` uses the same helper to pick
 * ``--primary-foreground``). When the input is unparseable
 * (``contrastRatio`` treats it as black), white wins and is returned.
 * The function never throws.
 */

/**
 * Return the {fg, bg} colour pair used to evaluate
 * WCAG contrast for a given custom-colour row.  Returns ``null`` for
 * rows where contrast validation doesn't apply (e.g. ``--border``,
 * which is a divider colour, not a text/background pair).
 *
 * The mapping is:
 *   - ``--background``   → foreground vs background (text on page bg)
 *   - ``--foreground``   → foreground vs background (same pair, shown
 *                          on the foreground row too so editing either
 *                          colour surfaces the warning)
 *   - ``--primary``      → contrast-picked foreground vs primary
 *                          (The foreground is whichever of
 *                          white/black has higher contrast against the
 *                          user-chosen primary, mirroring
 *                          ``deriveCustomVars``'s --primary-foreground
 *                          derivation. The warning fires only when
 *                          NEITHER clears AA — i.e. the user picked a
 *                          mid-tone primary that can't carry either
 *                          text colour.)
 *   - ``--bg-subtle``    → foreground vs bg-subtle (text on cards)
 *   - ``--text-muted``   → text-muted vs background (secondary text)
 *   - ``--border``       → null (no text-on-border pair)
 *
 * Falls back to the DEFAULT_CUSTOM_LIGHT/DARK value when the draft
 * is missing a key, so the warning still fires for the default theme.
 */
export function getContrastPair(
	varName: string,
	draft: CustomThemeData | null,
	mode: "light" | "dark",
): { fg: string; bg: string } | null {
	const src = draft?.[mode];
	const fallback =
		mode === "light" ? DEFAULT_CUSTOM_LIGHT : DEFAULT_CUSTOM_DARK;
	const get = (k: string): string => src?.[k] ?? fallback[k] ?? "#000000";
	switch (varName) {
		case "--background":
			return { fg: get("--foreground"), bg: get("--background") };
		case "--foreground":
			return { fg: get("--foreground"), bg: get("--background") };
		case "--primary": {
			// deriveCustomVars now picks the foreground dynamically
			// (white or black, whichever has higher contrast). The warning
			// fires only when NEITHER clears AA — i.e. the user picked a
			// mid-tone primary that can't carry either text colour. We
			// normalise the primary to hex first so oklch/hsl/named colours
			// the user picked in the editor are scored correctly (the
			// underlying ``contrastRatio`` only parses #rrggbb).
			const primaryHex = cssColorToHex(get("--primary"));
			return {
				fg: pickContrastForeground(primaryHex),
				bg: primaryHex,
			};
		}
		case "--bg-subtle":
			return {
				fg: pickContrastForeground(cssColorToHex(get("--bg-subtle"))),
				bg: cssColorToHex(get("--bg-subtle")),
			};
		case "--text-muted":
			return {
				fg: pickContrastForeground(cssColorToHex(get("--background"))),
				bg: cssColorToHex(get("--background")),
			};
		case "--border":
			return null;
		default:
			return null;
	}
}

/**
 * Compute the contrast-ratio info for a single colour-picker row.
 * Returns ``{ ratio, ratioRounded, showWarning }`` where ``ratio`` is
 * ``null`` when no contrast pair applies to the row (e.g. ``--border``).
 *
 * Bundles the per-row computation that the custom-theme editor
 * previously inlined inside the ``CUSTOM_COLOR_KEYS.map`` callback —
 * the component now calls this helper once per row instead of
 * re-doing the pair-lookup + ratio + threshold-check inline.
 */
export function computeRowContrast(
	varName: string,
	draft: CustomThemeData | null,
	mode: "light" | "dark",
): {
	ratio: number | null;
	ratioRounded: number | null;
	showWarning: boolean;
} {
	const pair = getContrastPair(varName, draft, mode);
	if (pair === null) {
		return { ratio: null, ratioRounded: null, showWarning: false };
	}
	const ratio = contrastRatio(pair.fg, pair.bg);
	const ratioRounded = Math.round(ratio * 10) / 10;
	return {
		ratio,
		ratioRounded,
		showWarning: ratio < CONTRAST_AA_THRESHOLD,
	};
}
