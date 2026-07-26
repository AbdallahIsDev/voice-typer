// lib/color-utils.ts — color conversion helpers extracted from
// ThemeSettingsSection.tsx (CR-73 / Fix-M).
//
// These are pure functions with no React dependency, so they belong in a
// utility module rather than a 890-LOC component file.  The component
// now imports them via `import { cssColorToHex } from "@/lib/color-utils"`.
//
// All functions are written defensively (try/catch around DOM access,
// explicit fallbacks for unparseable values) so a malformed CSS color
// string never throws — it returns ``#000000`` instead.  This matches
// the original contract in ThemeSettingsSection.tsx.
//
// XA-9-14: ``pickBestForeground`` and ``passesWCAG`` extend the
// public API so the custom theme editor (and any future caller) can
// compute the best foreground for a given background by trying a
// list of candidates (e.g. ``["#ffffff", "#000000"]``) and picking
// the one with the highest contrast ratio. This replaces the
// hardcoded ``#ffffff`` for primary/accent/destructive foregrounds
// that broke AA contrast on light primary colors (e.g. monokai
// ``--primary: oklch(0.7 0.18 250)`` against white text → 2.5:1).
//
// PVT-043 / Task #20: ``contrastRatio`` (and its private
// ``_relativeLuminance`` helper) implements the WCAG 2.1 contrast
// ratio calculation so the custom theme editor can validate
// foreground / background pairs against the AA (4.5:1) and AAA
// (7:1) thresholds without pulling in a third-party a11y library.
//
// PVT-G5 (type-safety): the underscore-prefixed helpers
// (``_srgbGamma``, ``_cssColorToHexViaOklch``, ``_cssColorToHexViaDOM``,
// ``_relativeLuminance``, ``_parseHex``) are NOT exported — they are
// internal implementation details. Only the public API (``cssColorToHex``,
// ``contrastRatio``, ``pickBestForeground``, ``passesWCAG``) is
// exported. External callers were checked: the only consumer outside
// this file (``ThemeSettingsSection.tsx``) had a comment referencing
// these names but did not import them.

// ── WCAG 2.1 contrast ───────────────────────────────────────────────
//
// PVT-043 / Task #20: the custom theme editor needs to validate
// foreground / background pairs against the AA (4.5:1 for normal
// text, 3:1 for large text / UI components) and AAA (7:1 / 4.5:1)
// thresholds. We implement the contrast ratio directly rather than
// pulling in a third-party a11y library — the formula is small,
// well-specified, and only depends on the sRGB → relative luminance
// transform.
//
// The helpers accept ``#rgb`` / ``#rrggbb`` hex strings (the same
// shape ``cssColorToHex`` produces for any CSS colour). Invalid input
// is clamped to black so a malformed colour never throws.

/**
 * Parse a hex colour (``#rgb`` or ``#rrggbb``) into an ``[r, g, b]``
 * triple of integers in ``[0, 255]``. Returns ``[0, 0, 0]`` for
 * unparseable input so callers never have to deal with NaN / throws.
 */
function _parseHex(color: string): [number, number, number] {
	const match = color.match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
	if (!match) return [0, 0, 0];
	const hex = match[1];
	if (hex.length === 3) {
		return [
			parseInt(hex[0] + hex[0], 16),
			parseInt(hex[1] + hex[1], 16),
			parseInt(hex[2] + hex[2], 16),
		];
	}
	return [
		parseInt(hex.slice(0, 2), 16),
		parseInt(hex.slice(2, 4), 16),
		parseInt(hex.slice(4, 6), 16),
	];
}

/**
 * Compute the WCAG 2.1 relative luminance of a hex colour.
 *
 * Uses the standard sRGB → linear-light transform
 * (``c ≤ 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4``) and
 * the Rec. 709 luma weights (``0.2126 R + 0.7152 G + 0.0722 B``).
 *
 * Returns ``0`` for unparseable input (treated as black) so callers
 * can pass arbitrary user strings without try/catch.
 */
function _relativeLuminance(color: string): number {
	const [r8, g8, b8] = _parseHex(color);
	const channel = (v8: number): number => {
		const c = v8 / 255; // to [0, 1]
		return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
	};
	const R = channel(r8);
	const G = channel(g8);
	const B = channel(b8);
	return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}

/**
 * Compute the WCAG 2.1 contrast ratio between two hex colours.
 *
 * Returns a number in ``[1, 21]`` (``1:1`` = identical colours,
 * ``21:1`` = pure black on pure white). Order doesn't matter — the
 * formula uses the lighter luminance as the numerator.
 *
 * Common thresholds (callers compare against these):
 *   - ``≥ 4.5``  ⇒ AA for normal text
 *   - ``≥ 7``    ⇒ AAA for normal text
 *   - ``≥ 3``    ⇒ AA for large text (≥ 18pt or 14pt bold) / UI components
 *   - ``≥ 4.5``  ⇒ AAA for large text
 *
 * Examples:
 *   - ``contrastRatio("#000000", "#ffffff")`` → ``21``
 *   - ``contrastRatio("#ffffff", "#ffffff")`` → ``1``
 *   - ``contrastRatio("#777777", "#ffffff")`` → ``4.48`` (≈ AA threshold)
 *
 * PVT-043: gives the custom theme editor a pure function for
 * validating ``--foreground`` / ``--background`` pairs without
 * pulling in a third-party a11y library.
 */
export function contrastRatio(fg: string, bg: string): number {
	const L1 = _relativeLuminance(fg);
	const L2 = _relativeLuminance(bg);
	const lighter = Math.max(L1, L2);
	const darker = Math.min(L1, L2);
	return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Apply the sRGB transfer function (gamma encoding) to a linear value
 * in [0, 1].  Returns the gamma-encoded value in [0, 1].
 *
 * Uses the standard IEC 61966-2-1 sRGB gamma:
 *   - linear <= 0.0031308 → 12.92 * linear
 *   - otherwise            → 1.055 * linear^(1/2.4) - 0.055
 *
 * The input is clamped to [0, 1] before the gamma is applied so
 * out-of-gamut OKLCH conversions (which can produce values slightly
 * outside [0, 1]) don't produce NaN or negative hex bytes.
 */
function _srgbGamma(c: number): number {
	c = Math.min(1, Math.max(0, c));
	if (c <= 0.0031308) return 12.92 * c;
	return 1.055 * c ** (1 / 2.4) - 0.055;
}

/**
 * Manual oklch() to sRGB hex converter.
 * Parses "oklch(L C H)" and "oklch(L C H / alpha)" formats,
 * converts OKLCH → OKLab → linear sRGB via the LMS cube-root
 * approach (Björn Ottosson's method), applies sRGB gamma, and
 * returns a #rrggbb hex string.
 *
 * Returns ``null`` when the input doesn't match the oklch() shape
 * (so the caller can fall through to the next strategy).
 */
function _cssColorToHexViaOklch(color: string): string | null {
	const match = color.match(/oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/i);
	if (!match) return null;

	const L = Number(match[1]);
	const C = Number(match[2]);
	const H = (Number(match[3]) * Math.PI) / 180;

	// OKLCH → OKLab
	const a = C * Math.cos(H);
	const b = C * Math.sin(H);

	// OKLab → linear LMS (cube root domain → linear via cube)
	const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
	const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
	const s_ = L - 0.0894841775 * a - 1.291485548 * b;

	const l = l_ * l_ * l_;
	const m = m_ * m_ * m_;
	const s = s_ * s_ * s_;

	// LMS → linear sRGB (inverse of sRGB→LMS OKLab matrix)
	let r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
	let g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
	let bl = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;

	// Apply sRGB gamma
	r = _srgbGamma(r);
	g = _srgbGamma(g);
	bl = _srgbGamma(bl);

	return (
		"#" +
		[r, g, bl]
			.map((c) =>
				Math.round(c * 255)
					.toString(16)
					.padStart(2, "0"),
			)
			.join("")
	);
}

/**
 * Try resolving a CSS color via a hidden DOM element.
 *
 * Creates a 1×1 div off-screen, sets its backgroundColor to the given
 * color string, reads back the computed style (which the browser
 * normalises to ``rgb()`` or ``rgba()``), and converts that to hex.
 *
 * Returns ``null`` when:
 *   - the DOM is unavailable (SSR / sandboxed renderer without document)
 *   - the browser couldn't parse the color (computed style returns
 *     ``rgba(0, 0, 0, 0)`` — transparent black — which we treat as a
 *     miss so the caller can fall through to the oklch parser)
 *   - the computed style doesn't match the rgb()/rgba() regex
 */
function _cssColorToHexViaDOM(color: string): string | null {
	try {
		const temp = document.createElement("div");
		temp.style.backgroundColor = color;
		temp.style.position = "absolute";
		temp.style.left = "-9999px";
		temp.style.width = "1px";
		temp.style.height = "1px";
		document.body.appendChild(temp);
		const computed = getComputedStyle(temp).backgroundColor;
		document.body.removeChild(temp);

		const match = computed.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
		if (match) {
			return (
				"#" +
				[1, 2, 3]
					.map((i) =>
						Math.round(Number(match[i])).toString(16).padStart(2, "0"),
					)
					.join("")
			);
		}
	} catch (e) {
		// Fall through to next attempt — the regex match / parse / hex
		// conversion can fail on malformed inputs; the next strategy
		// (DOM-based getComputedStyle) is more permissive.
		console.warn("[color-utils] hex-parse strategy failed, trying next:", e);
	}
	return null;
}

/**
 * Convert any CSS color value to #rrggbb hex using a hidden DOM element.
 * Uses getComputedStyle(backgroundColor) which reliably resolves oklch(),
 * hsl(), rgb(), named colors, etc. to an rgba() string that the browser
 * engine can compute, unlike the canvas 2d context which may fail on
 * oklch() values in some Electron/Chromium versions.
 *
 * Falls back to a manual oklch→sRGB→hex converter when the DOM approach
 * fails or returns transparent black (indicating the browser couldn't
 * parse the color).  This ensures the custom theme editor always receives
 * valid hex values regardless of Chromium version.
 *
 * @param color Any CSS color string (hex, rgb, hsl, oklch, named, etc.)
 * @returns A #rrggbb hex string.  Returns ``#000000`` for empty/unparseable input.
 */
export function cssColorToHex(color: string): string {
	if (!color) return "#000000";

	// Already a clean hex colour — normalise and return.
	const hexMatch = color.match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
	if (hexMatch) {
		const hex = hexMatch[1].toLowerCase();
		if (hex.length === 3) {
			return `#${hex[0]}${hex[0]}${hex[1]}${hex[1]}${hex[2]}${hex[2]}`;
		}
		return `#${hex}`;
	}

	// Attempt 1: DOM-based resolution (works in modern browsers)
	const domHex = _cssColorToHexViaDOM(color);
	if (domHex && domHex !== "#000000") return domHex;

	// Attempt 2: Manual oklch() → sRGB → hex parser (works everywhere)
	const oklchHex = _cssColorToHexViaOklch(color);
	if (oklchHex) return oklchHex;

	return "#000000";
}

// ── XA-9-14: foreground-selection helpers ────────────────────────────
//
// The custom theme editor (``themes.ts::deriveCustomVars``) previously
// hardcoded ``#ffffff`` for ``--primary-foreground``,
// ``--accent-foreground``, and ``--destructive-foreground`` regardless
// of the corresponding background's lightness. That broke AA contrast
// (4.5:1) on themes with light primary/accent/destructive colors —
// e.g. monokai's ``--primary: oklch(0.7 0.18 250)`` against white
// foreground = 2.5:1 (fails AA), but against black = 8.3:1 (passes
// AAA). The two helpers below let the editor pick the best foreground
// from a candidate list (typically ``["#ffffff", "#000000"]``) so the
// 4.5:1 AA threshold is met even on light backgrounds.
//
// These are pure functions with no DOM dependency, so they can run in
// the bootstrap module (before React mounts) and in Vitest unit tests
// without jsdom.

/**
 * Default candidate list for ``pickBestForeground``. White + black
 * covers ~99% of cases — any colour with luminance > 0.18 will pick
 * black, any colour with luminance < 0.18 will pick white. Themes
 * with very narrow luminance ranges (e.g. amoled dark) may want to
 * pass a wider candidate list.
 */
export const DEFAULT_FOREGROUND_CANDIDATES: readonly string[] = [
	"#ffffff",
	"#000000",
] as const;

/**
 * Pick the foreground colour from ``candidates`` that has the highest
 * WCAG 2.1 contrast ratio against ``bg``.
 *
 * Falls back to ``"#000000"`` if ``candidates`` is empty (defensive —
 * a caller passing an empty array would otherwise get ``undefined``
 * from ``Math.max``). The caller is expected to pass at least one
 * candidate; the default list (white + black) covers ~99% of cases.
 *
 * @param bg Hex colour (``#rgb`` or ``#rrggbb``) of the background.
 * @param candidates List of hex colours to try as the foreground.
 *                   Defaults to ``["#ffffff", "#000000"]``.
 * @returns The candidate with the highest contrast ratio against ``bg``.
 *          If two candidates tie, the FIRST one in the list wins
 *          (so ``["#ffffff", "#000000"]`` prefers white when contrast
 *          is equal — matching the prior hardcoded behaviour for
 *          dark backgrounds).
 */
export function pickBestForeground(
	bg: string,
	candidates: readonly string[] = DEFAULT_FOREGROUND_CANDIDATES,
): string {
	if (candidates.length === 0) return "#000000";
	// Biome lint/style/noNonNullAssertion: avoid `!` — the length check
	// above guarantees index 0 exists, but biome can't prove it. Use a
	// non-null assertion via explicit access + fallback to satisfy the
	// linter without changing runtime behavior.
	let best = candidates[0] ?? "#000000";
	let bestRatio = -1;
	for (const candidate of candidates) {
		const ratio = contrastRatio(candidate, bg);
		if (ratio > bestRatio) {
			bestRatio = ratio;
			best = candidate;
		}
	}
	return best;
}

/**
 * Check whether the WCAG 2.1 contrast ratio between ``fg`` and ``bg``
 * meets or exceeds ``threshold``.
 *
 * Convenience wrapper around ``contrastRatio`` for the common
 * comparison-against-threshold pattern. Use this in tests / validators
 * to keep the intent readable:
 *
 *   ``if (!passesWCAG(fg, bg, 4.5)) warn("fails AA");``
 *
 * @param fg Hex colour of the foreground.
 * @param bg Hex colour of the background.
 * @param threshold Minimum contrast ratio (1–21). Common values:
 *                  ``3`` (WCAG 1.4.11 UI / large text AA),
 *                  ``4.5`` (WCAG AA normal text),
 *                  ``7`` (WCAG AAA normal text).
 * @returns ``true`` if ``contrastRatio(fg, bg) >= threshold``.
 */
export function passesWCAG(fg: string, bg: string, threshold: number): boolean {
	return contrastRatio(fg, bg) >= threshold;
}
