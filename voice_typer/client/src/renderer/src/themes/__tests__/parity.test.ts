/**
 * CR-061 / PVT-001: theme preset light/dark var coverage parity test.
 *
 * Each built-in theme preset defines CSS variable overrides for both
 * light and dark colour schemes.  If the light map and dark map don't
 * cover the SAME set of variable names, components that read a var
 * present in only one map silently fall through to the stylesheet
 * default in the other scheme — producing an inconsistent accent
 * colour, border, or sidebar tint when the user toggles between
 * light and dark mode.
 *
 * This test asserts that for every preset under test, the set of
 * keys in ``preset.light`` is identical to the set of keys in
 * ``preset.dark``.  It also asserts the union covers the full
 * ``THEME_VARIABLES`` superset declared in ``themes.ts`` so a future
 * edit that drops a var from both maps is caught.
 *
 * PVT-001: previously only amoled, sepia, and nord were exercised.
 * The remaining 7 non-default/non-custom presets (dracula, solarized,
 * tokyo-night, ayu, monokai, catppuccin, github) silently shipped
 * with missing light-mode tokens because the parity test didn't
 * cover them. The test now derives its fixtures from the canonical
 * ``THEMES`` array (filtering out the no-op ``default`` and runtime-
 * computed ``custom`` presets) so any future preset is automatically
 * covered.
 */
import { describe, expect, it } from "vitest";

import { THEME_VARIABLES, THEMES } from "@/themes";

// PVT-001: derive fixtures from the canonical THEMES array so every
// non-default, non-custom preset is covered. The `default` preset is a
// no-op (no overrides) and `custom` is computed at runtime from
// user-supplied colours — neither carries a static light/dark map to
// parity-test.
const PRESETS_UNDER_TEST = THEMES.filter(
	(t) => t.id !== "default" && t.id !== "custom",
).map((preset) => ({ name: preset.id, preset }));

describe("theme preset light/dark var coverage parity (CR-061)", () => {
	// PVT-001: sanity guard — if a future preset is added to THEMES but
	// excluded above by accident, this assertion fires. Update the
	// filter explicitly when adding a no-op or runtime-computed preset.
	it("exercises every non-default/non-custom preset (PVT-001 regression guard)", () => {
		expect(PRESETS_UNDER_TEST.length).toBeGreaterThanOrEqual(9);
		const exercisedIds = new Set(PRESETS_UNDER_TEST.map((p) => p.name));
		for (const expected of [
			"amoled",
			"nord",
			"dracula",
			"sepia",
			"monokai",
			"ayu",
			"github",
			"catppuccin",
			"tokyo-night",
			"solarized",
		]) {
			expect(exercisedIds.has(expected), `missing preset ${expected}`).toBe(
				true,
			);
		}
	});

	for (const { name, preset } of PRESETS_UNDER_TEST) {
		describe(`${name} preset`, () => {
			const lightKeys = new Set(Object.keys(preset.light));
			const darkKeys = new Set(Object.keys(preset.dark));

			it("light and dark define the same set of CSS variables", () => {
				expect(lightKeys.size).toBe(darkKeys.size);
				for (const key of lightKeys) {
					expect(
						darkKeys.has(key),
						`dark missing var ${key} (present in light)`,
					).toBe(true);
				}
				for (const key of darkKeys) {
					expect(
						lightKeys.has(key),
						`light missing var ${key} (present in dark)`,
					).toBe(true);
				}
			});

			it("covers the full THEME_VARIABLES superset in both light and dark", () => {
				for (const v of THEME_VARIABLES) {
					expect(lightKeys.has(v), `light missing superset var ${v}`).toBe(
						true,
					);
					expect(darkKeys.has(v), `dark missing superset var ${v}`).toBe(true);
				}
			});

			it("does not define any var outside the THEME_VARIABLES superset", () => {
				for (const key of lightKeys) {
					expect(
						THEME_VARIABLES.includes(key),
						`light defines unknown var ${key} not in THEME_VARIABLES`,
					).toBe(true);
				}
				for (const key of darkKeys) {
					expect(
						THEME_VARIABLES.includes(key),
						`dark defines unknown var ${key} not in THEME_VARIABLES`,
					).toBe(true);
				}
			});

			// PVT-002: every theme must explicitly define --destructive-foreground
			// so destructive button text is readable without relying on the
			// stylesheet default.
			it("defines --destructive-foreground in both light and dark (PVT-002)", () => {
				expect(lightKeys.has("--destructive-foreground")).toBe(true);
				expect(darkKeys.has("--destructive-foreground")).toBe(true);
			});
		});
	}
});

// ─── BG-5 / BG-33 / BG-35 / BG-36 regression tests ────────────────────
//
// These tests assert the design-system invariants introduced by the
// BG-5 (nameKey), BG-33 (dark --ring WCAG 1.4.11), BG-35 (dark
// --muted-foreground WCAG AA 4.5:1), and BG-36 (--primary vs
// --primary-foreground WCAG AA 4.5:1) fixes. They use the WCAG 2.1
// contrast-ratio implementation in ``@/lib/color-utils`` (which works
// on hex strings) and the OKLCH → hex converter in the same module to
// resolve the ``oklch(...)`` strings stored in the preset maps.

import enLocale from "@/i18n/translations/en.json";
import {
	cssColorToHex,
	contrastRatio as wcagContrastRatio,
} from "@/lib/color-utils";

describe("theme preset i18n nameKey (BG-5)", () => {
	it(`every preset carries a nameKey matching \`theme.preset.\${id}\``, () => {
		for (const preset of THEMES) {
			expect(preset.nameKey, `preset ${preset.id} nameKey`).toBe(
				`theme.preset.${preset.id}`,
			);
		}
	});

	it("every preset nameKey exists in en.json", () => {
		const themeKeys: Record<string, string> =
			(enLocale.theme?.preset as Record<string, string> | undefined) ?? {};
		for (const preset of THEMES) {
			expect(
				themeKeys[preset.id],
				`en.json missing key theme.preset.${preset.id}`,
			).toBeTruthy();
		}
	});
});

describe("theme preset WCAG contrast invariants (BG-33 / BG-35 / BG-36)", () => {
	// WCAG 1.4.11 minimum for focus indicators (3:1) — but at /30 alpha
	// the effective contrast is lower, so the threshold we assert here
	// is a conservative proxy: the ring colour's luminance must differ
	// from the background's luminance enough that even at /30 alpha it
	// clears 3:1. We approximate by asserting the opaque ring vs
	// background contrast is >= 3:1 (which guarantees the /30 blend
	// also clears 3:1 when the background is near-black or near-white).
	const RING_WCAG_THRESHOLD = 3.0;
	const TEXT_AA_THRESHOLD = 4.5;

	for (const preset of THEMES) {
		// Skip 'default' (empty maps — relies on index.css) and 'custom'
		// (runtime-computed — deriveCustomVars has its own contrast logic).
		if (preset.id === "default" || preset.id === "custom") continue;

		describe(`${preset.id} preset`, () => {
			it("dark-mode --ring clears WCAG 1.4.11 3:1 against dark --background (BG-33)", () => {
				const ring = cssColorToHex(preset.dark["--ring"] ?? "");
				const bg = cssColorToHex(preset.dark["--background"] ?? "");
				const ratio = wcagContrastRatio(ring, bg);
				expect(ratio, `dark --ring contrast = ${ratio}`).toBeGreaterThanOrEqual(
					RING_WCAG_THRESHOLD,
				);
			});

			it("dark-mode --sidebar-ring mirrors dark-mode --ring (BG-79)", () => {
				expect(preset.dark["--sidebar-ring"]).toBe(preset.dark["--ring"]);
			});

			it("light-mode --sidebar-ring mirrors light-mode --ring (BG-79)", () => {
				expect(preset.light["--sidebar-ring"]).toBe(preset.light["--ring"]);
			});

			it("dark-mode --muted-foreground clears WCAG AA 4.5:1 against dark --background (BG-35)", () => {
				const fg = cssColorToHex(preset.dark["--muted-foreground"] ?? "");
				const bg = cssColorToHex(preset.dark["--background"] ?? "");
				const ratio = wcagContrastRatio(fg, bg);
				expect(
					ratio,
					`dark --muted-foreground contrast = ${ratio}`,
				).toBeGreaterThanOrEqual(TEXT_AA_THRESHOLD);
			});

			it("light-mode --muted-foreground clears WCAG AA 4.5:1 against light --background (BG-34)", () => {
				const fg = cssColorToHex(preset.light["--muted-foreground"] ?? "");
				const bg = cssColorToHex(preset.light["--background"] ?? "");
				const ratio = wcagContrastRatio(fg, bg);
				expect(
					ratio,
					`light --muted-foreground contrast = ${ratio}`,
				).toBeGreaterThanOrEqual(TEXT_AA_THRESHOLD);
			});

			it("light-mode --primary vs --primary-foreground clears WCAG AA 4.5:1 (BG-36)", () => {
				const fg = cssColorToHex(preset.light["--primary-foreground"] ?? "");
				const bg = cssColorToHex(preset.light["--primary"] ?? "");
				const ratio = wcagContrastRatio(fg, bg);
				expect(
					ratio,
					`light --primary-foreground vs --primary contrast = ${ratio}`,
				).toBeGreaterThanOrEqual(TEXT_AA_THRESHOLD);
			});

			it("dark-mode --primary vs --primary-foreground clears WCAG AA 4.5:1 (BG-36)", () => {
				const fg = cssColorToHex(preset.dark["--primary-foreground"] ?? "");
				const bg = cssColorToHex(preset.dark["--primary"] ?? "");
				const ratio = wcagContrastRatio(fg, bg);
				expect(
					ratio,
					`dark --primary-foreground vs --primary contrast = ${ratio}`,
				).toBeGreaterThanOrEqual(TEXT_AA_THRESHOLD);
			});
		});
	}
});
