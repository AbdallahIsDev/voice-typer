// Theme-editor contrast helpers. WCAG math (contrastRatio) re-exported
// from @/lib/color-utils; this module holds AA threshold + hex validation +
// row→{fg,bg} mapping for the colour picker.

import { contrastRatio, cssColorToHex } from "@/lib/color-utils";
import {
	type CustomThemeData,
	DEFAULT_CUSTOM_DARK,
	DEFAULT_CUSTOM_LIGHT,
	pickContrastForeground,
} from "@/themes";

// Re-export contrastRatio so consumers have one import site.
export { contrastRatio };

/** WCAG AA normal-text contrast threshold. */
export const CONTRAST_AA_THRESHOLD = 4.5;

// ── Hex input validation regex ───────────────────
// Loose regex (allows partial typing):  #  followed by 0–6 hex digits.
// Strict regex (used for commit-on-blur): # followed by exactly 6 hex digits.
export const HEX_STRICT_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * WCAG contrast for a given custom-colour row.  Returns ``null`` for
 * rows where contrast validation doesn't apply (e.g. ``--border``,
 * which is a divider colour, not a text/background pair).
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
 *                          NEITHER clears AA, i.e. the user picked a
 *                          mid-tone primary that can't carry either
 *                          text colour.)
 *   - ``--bg-subtle``    → foreground vs bg-subtle (text on cards)
 *   - ``--text-muted``   → text-muted vs background (secondary text)
 *   - ``--border``       → null (no text-on-border pair)
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
			// fires only when NEITHER clears AA, i.e. the user picked a
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
