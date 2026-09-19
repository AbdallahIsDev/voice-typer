// ThemeSettingsSection.tsx ( / Fix-M).
// These are pure functions with no React dependency, so they belong in a
// utility module rather than a 890-LOC component file.  The component
// now imports them via `import { cssColorToHex } from "@/lib/color-utils"`.
// All functions are written defensively (try/catch around DOM access,
// explicit fallbacks for unparseable values) so a malformed CSS color
// string never throws, it returns ``#000000`` instead.  This matches
// the original contract in ThemeSettingsSection.tsx.
// ``pickBestForeground`` and ``passesWCAG`` extend the
// public API so the custom theme editor (and any future caller) can
// compute the best foreground for a given background by trying a
// list of candidates (e.g. ``["#ffffff", "#000000"]``) and picking
// the one with the highest contrast ratio. This replaces the
// hardcoded ``#ffffff`` for primary/accent/destructive foregrounds
// that broke AA contrast on light primary colors (e.g. monokai
// ``--primary: oklch(0.7 0.18 250)`` against white text → 2.5:1).
// ``contrastRatio`` (and its private
// ``_relativeLuminance`` helper) implements the WCAG 2.1 contrast
// ratio calculation so the custom theme editor can validate
// foreground / background pairs against the AA (4.5:1) and AAA
// (7:1) thresholds without pulling in a third-party a11y library.
// the underscore-prefixed helpers
// (``_srgbGamma``, ``_cssColorToHexViaOklch``, ``_cssColorToHexViaDOM``,
// ``_relativeLuminance``, ``_parseHex``) are NOT exported, they are
// internal implementation details. Only the public API (``cssColorToHex``,
// ``contrastRatio``, ``pickBestForeground``) is exported; ``passesWCAG``
// is a test/validator convenience wrapper marked ``@internal`` (no production
// caller). External callers were checked: the only consumer
// outside this file (``ThemeSettingsSection.tsx``) had a comment
// referencing these names but did not import them.

// ── WCAG 2.1 contrast ───────────────────────────────────────────────
// the custom theme editor needs to validate
// foreground / background pairs against the AA (4.5:1 for normal
// text, 3:1 for large text / UI components) and AAA (7:1 / 4.5:1)
// thresholds. We implement the contrast ratio directly rather than
// pulling in a third-party a11y library, the formula is small,
// well-specified, and only depends on the sRGB → relative luminance
// transform.
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
	// `match` is `RegExpMatchArray | null` and `match[1]` is
	// `string | undefined` under `noUncheckedIndexedAccess`; the regex
	// above guarantees the capture group exists whenever match is
	// non-null, so the explicit guards satisfy the strict checker
	// without resorting to non-null assertions.
	if (match === null || match[1] === undefined) return [0, 0, 0];
	const hex = match[1];
	if (hex.length === 3) {
		const r = hex[0];
		const g = hex[1];
		const b = hex[2];
		if (r === undefined || g === undefined || b === undefined) {
			return [0, 0, 0];
		}
		return [parseInt(r + r, 16), parseInt(g + g, 16), parseInt(b + b, 16)];
	}
	return [
		parseInt(hex.slice(0, 2), 16),
		parseInt(hex.slice(2, 4), 16),
		parseInt(hex.slice(4, 6), 16),
	];
}

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

export function contrastRatio(fg: string, bg: string): number {
	const L1 = _relativeLuminance(fg);
	const L2 = _relativeLuminance(bg);
	const lighter = Math.max(L1, L2);
	const darker = Math.min(L1, L2);
	return (lighter + 0.05) / (darker + 0.05);
}

export function mixHexColors(a: string, b: string, weight: number): string {
	const [ar, ag, ab] = _parseHex(a);
	const [br, bg2, bb] = _parseHex(b);
	const w = Math.min(1, Math.max(0, weight));
	const mix = (x: number, y: number): number => Math.round(x + (y - x) * w);
	return (
		"#" +
		[mix(ar, br), mix(ag, bg2), mix(ab, bb)]
			.map((c) => c.toString(16).padStart(2, "0"))
			.join("")
	);
}

function _srgbGamma(c: number): number {
	c = Math.min(1, Math.max(0, c));
	if (c <= 0.0031308) return 12.92 * c;
	return 1.055 * c ** (1 / 2.4) - 0.055;
}

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
		// Fall through to next attempt, the regex match / parse / hex
		// conversion can fail on malformed inputs; the next strategy
		// (DOM-based getComputedStyle) is more permissive.
		console.warn(
			"[renderer:color-utils] hex-parse strategy failed, trying next:",
			e,
		);
	}
	return null;
}

export function cssColorToHex(color: string): string {
	if (!color) return "#000000";

	const cached = _cssColorToHexCache.get(color);
	if (cached !== undefined) return cached ?? "#000000";

	const resolved = _resolveCssColorToHex(color);
	if (_cssColorToHexCache.size >= _CSS_COLOR_TO_HEX_CACHE_MAX) {
		// Defensive bound: inputs are a bounded set of CSS color strings,
		// but evict the OLDEST entry when full anyway (Map preserves
		// insertion order, so delete-first-key-then-set is FIFO).
		const oldest = _cssColorToHexCache.keys().next();
		if (!oldest.done) _cssColorToHexCache.delete(oldest.value);
	}
	_cssColorToHexCache.set(color, resolved);
	return resolved ?? "#000000";
}

// ── per-input resolution cache ──────────────────────────────
// Resolution is deterministic per input string (getComputedStyle
// resolves a given color string to the same rgb()/rgba() value every
// time), so the full chain, hex fast-path, DOM probe, oklch
// fallback, is memoized by the raw input. ``null`` marks a
// KNOWN-UNPARSEABLE input (including a missing-DOM environment,
// where the probe always fails) so repeated bad values skip the DOM
// probe too; a missing key (``undefined``) means "not resolved yet".
// Same style as the NumberFormat cache in lib/format.ts.

const _cssColorToHexCache = new Map<string, string | null>();
const _CSS_COLOR_TO_HEX_CACHE_MAX = 256;

function _resolveCssColorToHex(color: string): string | null {
	// Already a clean hex colour, normalise and return.
	const hexMatch = color.match(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/);
	if (hexMatch && hexMatch[1] !== undefined) {
		// Capture group 1 exists whenever `hexMatch` is non-null (the
		// regex guarantees it); the explicit narrow satisfies
		// `noUncheckedIndexedAccess` without a non-null assertion
		// (which biome's `noNonNullAssertion` rule forbids).
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
	return oklchHex ?? null;
}

// ── foreground-selection helpers ────────────────────────────
// hardcoded ``#ffffff`` for ``--primary-foreground``,
// ``--accent-foreground``, and ``--destructive-foreground`` regardless
// of the corresponding background's lightness. That broke AA contrast
// (4.5:1) on themes with light primary/accent/destructive colors —
// e.g. monokai's ``--primary: oklch(0.7 0.18 250)`` against white
// foreground = 2.5:1 (fails AA), but against black = 8.3:1 (passes
// AAA). The two helpers below let the editor pick the best foreground
// from a candidate list (typically ``["#ffffff", "#000000"]``) so the
// 4.5:1 AA threshold is met even on light backgrounds.
// These are pure functions with no DOM dependency, so they can run in
// the bootstrap module (before React mounts) and in Vitest unit tests
// without jsdom.

/**
 * Default candidate list for ``pickBestForeground``. White + black
 * covers ~99% of cases, any colour with luminance > 0.18 will pick
 * black, any colour with luminance < 0.18 will pick white. Themes
 * with very narrow luminance ranges (e.g. amoled dark) may want to
 * pass a wider candidate list.
 */
export const DEFAULT_FOREGROUND_CANDIDATES: readonly string[] = [
	"#ffffff",
	"#000000",
] as const;

export function pickBestForeground(
	bg: string,
	candidates: readonly string[] = DEFAULT_FOREGROUND_CANDIDATES,
): string {
	if (candidates.length === 0) return "#000000";
	// Biome lint/style/noNonNullAssertion: avoid `!`, the length check
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

export function passesWCAG(fg: string, bg: string, threshold: number): boolean {
	return contrastRatio(fg, bg) >= threshold;
}
