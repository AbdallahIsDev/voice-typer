/**
 *  ( High) — regression tests for the contrast-picker bugfix
 * ported from the dead theme utility helper module into the live
 * `lib/theme-contrast.ts` consumer.
 *
 * Background: `deriveCustomVars` (in `@/themes`) was fixed in  to
 * pick `--primary-foreground` dynamically (whichever of `#ffffff` /
 * `#000000` has higher WCAG contrast against the user-chosen primary).
 * The live contrast-picker helper in `lib/theme-contrast.ts` was not
 * updated and kept hardcoding `fg: "#ffffff"` for the `--primary` row,
 * so the rendered text (black on a light primary) disagreed with the
 * contrast-picker grid (which still showed white-on-primary) — yielding
 * a spurious "fails AA" warning for light-tone primaries that actually
 * render fine, and no warning at all for the inverse case.
 *
 * These tests pin the ported behaviour:
 *   - Light-tone primary  → `#000000` foreground (not `#ffffff`).
 *   - Dark-tone primary   → `#ffffff` foreground.
 *   - The `bg` returned for `--primary` is hex-normalised so the
 *     downstream `contrastRatio` call scores the actual rendered pair.
 *   - `computeRowContrast` no longer surfaces a spurious warning for a
 *     light-tone primary whose dynamically-picked foreground clears AA.
 */
import { describe, expect, it } from "vitest";
import { contrastRatio } from "@/lib/color-utils";
import {
	CONTRAST_AA_THRESHOLD,
	computeRowContrast,
	getContrastPair,
} from "@/lib/theme-contrast";
import type { CustomThemeData } from "@/themes";

// ── Test fixtures ────────────────────────────────────────────────────
// A custom draft where the user has picked a very light, low-chroma
//blue primary (`oklch(0.9 0.05 250)` ≈ `#c6e1ff`). Pre- this
// would have forced `fg: "#ffffff"` → contrast ratio ≈ 1.35 (fails AA
// by a wide margin) and surfaced a spurious warning even though the
// rendered text (black on light blue, picked by the fixed
// `deriveCustomVars`) clears AA at ~15.6:1.
const LIGHT_PRIMARY_OKLCH = "oklch(0.9 0.05 250)";
const LIGHT_PRIMARY_HEX = "#c6e1ff"; // ≈ oklch(0.9 0.05 250) in sRGB
const DARK_PRIMARY_HEX = "#1447e6"; // DEFAULT_CUSTOM_LIGHT["--primary"]

const draftWithLightPrimary: CustomThemeData = {
	light: {
		"--background": "#ffffff",
		"--foreground": "#09090b",
		"--primary": LIGHT_PRIMARY_OKLCH,
		"--bg-subtle": "#f5f5f5",
		"--border": "#e4e4e7",
		"--text-muted": "#71717b",
	},
	dark: {
		"--background": "#131313",
		"--foreground": "#fafafa",
		"--primary": LIGHT_PRIMARY_OKLCH,
		"--bg-subtle": "#0f0f0f",
		"--border": "#1f1f1f",
		"--text-muted": "#9f9fa9",
	},
};

const draftWithDarkPrimary: CustomThemeData = {
	light: {
		"--background": "#ffffff",
		"--foreground": "#09090b",
		"--primary": DARK_PRIMARY_HEX,
		"--bg-subtle": "#f5f5f5",
		"--border": "#e4e4e7",
		"--text-muted": "#71717b",
	},
	dark: {
		"--background": "#131313",
		"--foreground": "#fafafa",
		"--primary": DARK_PRIMARY_HEX,
		"--bg-subtle": "#0f0f0f",
		"--border": "#1f1f1f",
		"--text-muted": "#9f9fa9",
	},
};

describe("getContrastPair('--primary') — BG-R18 dynamic foreground", () => {
	it("picks a DARK foreground for a light-tone oklch primary (not #ffffff)", () => {
		const pair = getContrastPair("--primary", draftWithLightPrimary, "light");
		expect(pair).not.toBeNull();
		expect(pair?.fg).toBe("#000000");
	});

	it("picks a DARK foreground for a light-tone hex primary", () => {
		const draft: CustomThemeData = {
			light: { ...draftWithLightPrimary.light, "--primary": LIGHT_PRIMARY_HEX },
			dark: draftWithLightPrimary.dark,
		};
		const pair = getContrastPair("--primary", draft, "light");
		expect(pair).not.toBeNull();
		expect(pair?.fg).toBe("#000000");
	});

	it("picks a WHITE foreground for a dark-tone primary", () => {
		const pair = getContrastPair("--primary", draftWithDarkPrimary, "light");
		expect(pair).not.toBeNull();
		expect(pair?.fg).toBe("#ffffff");
	});

	it("returns the hex-normalised primary as `bg` so downstream contrast math works", () => {
		// The raw draft value is an oklch() string that `_parseHex` cannot
		// parse — if `getContrastPair` returned it verbatim, the downstream
		// `contrastRatio(fg, bg)` call would treat it as black (#000000)
		// and produce a ratio of 1.0, masking the real contrast. The
		// hex-normalised value must come back instead.
		const pair = getContrastPair("--primary", draftWithLightPrimary, "light");
		expect(pair).not.toBeNull();
		expect(pair?.bg).toMatch(/^#[0-9a-fA-F]{6}$/);
		expect(pair?.bg).not.toBe(LIGHT_PRIMARY_OKLCH);
		// The hex should match the manual oklch→sRGB conversion
		// (≈ #c6e1ff for oklch(0.9 0.05 250)).
		expect(pair?.bg.toLowerCase()).toBe(LIGHT_PRIMARY_HEX);
	});

	it("applies the same dynamic selection in dark mode", () => {
		// Light primary in dark mode should still pick black fg.
		const pair = getContrastPair("--primary", draftWithLightPrimary, "dark");
		expect(pair).not.toBeNull();
		expect(pair?.fg).toBe("#000000");
	});

	it("falls back to DEFAULT_CUSTOM_LIGHT when draft is null", () => {
		// Default light primary is #1447e6 (dark blue) → white fg.
		const pair = getContrastPair("--primary", null, "light");
		expect(pair).not.toBeNull();
		expect(pair?.fg).toBe("#ffffff");
		expect(pair?.bg).toBe(DARK_PRIMARY_HEX);
	});
});

describe("computeRowContrast('--primary') — BG-R18 warning accuracy", () => {
	it("does NOT warn for a light-tone primary whose dynamic (black) fg clears AA", () => {
		// Pre-fix: fg was hardcoded to #ffffff → ratio ≈ 1.35 → warning fired
		// even though the rendered text (black) clears AA at ~15.6:1.
		// Post-fix: fg is #000000 → ratio ≈ 15.6 → no warning.
		const info = computeRowContrast(
			"--primary",
			draftWithLightPrimary,
			"light",
		);
		expect(info.ratio).not.toBeNull();
		expect(info.ratio).toBeGreaterThan(CONTRAST_AA_THRESHOLD);
		expect(info.showWarning).toBe(false);
	});

	it("does NOT warn for a dark-tone primary whose white fg clears AA", () => {
		const info = computeRowContrast("--primary", draftWithDarkPrimary, "light");
		expect(info.ratio).not.toBeNull();
		expect(info.ratio).toBeGreaterThan(CONTRAST_AA_THRESHOLD);
		expect(info.showWarning).toBe(false);
	});

	it("ratio matches a direct contrastRatio call against the picked pair", () => {
		const pair = getContrastPair("--primary", draftWithLightPrimary, "light");
		const info = computeRowContrast(
			"--primary",
			draftWithLightPrimary,
			"light",
		);
		expect(pair).not.toBeNull();
		expect(info.ratio).toBeCloseTo(
			contrastRatio(pair?.fg ?? "#000000", pair?.bg ?? "#000000"),
			5,
		);
	});
});

describe("BG-R18 — non-primary rows are untouched (never downgrade)", () => {
	// The fix must not change behaviour for other CSS variables. These
	// snapshots pin the pre-fix pair shape so a future regression to
	// the --primary case can't accidentally bleed into sibling rows.
	it("--background still returns the raw foreground/background pair", () => {
		const pair = getContrastPair("--background", draftWithDarkPrimary, "light");
		expect(pair).toEqual({ fg: "#09090b", bg: "#ffffff" });
	});

	it("--border still returns null (no text-on-border pair)", () => {
		const pair = getContrastPair("--border", draftWithDarkPrimary, "light");
		expect(pair).toBeNull();
		const info = computeRowContrast("--border", draftWithDarkPrimary, "light");
		expect(info.ratio).toBeNull();
		expect(info.showWarning).toBe(false);
	});
});
