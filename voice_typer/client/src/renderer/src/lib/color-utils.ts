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
export function _srgbGamma(c: number): number {
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
export function _cssColorToHexViaOklch(color: string): string | null {
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
export function _cssColorToHexViaDOM(color: string): string | null {
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
	} catch {
		// Fall through to next attempt
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
