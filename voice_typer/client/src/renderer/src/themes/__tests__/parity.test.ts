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
