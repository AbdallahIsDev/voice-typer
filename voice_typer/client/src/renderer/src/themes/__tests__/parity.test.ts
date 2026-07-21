/**
 * CR-061: theme preset light/dark var coverage parity test.
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
 */
import { describe, expect, it } from "vitest";

import { THEME_VARIABLES } from "@/themes";
import { amoledTheme } from "../amoled";
import { nordTheme } from "../nord";
import { sepiaTheme } from "../sepia";

const PRESETS_UNDER_TEST = [
	{ name: "amoled", preset: amoledTheme },
	{ name: "sepia", preset: sepiaTheme },
	{ name: "nord", preset: nordTheme },
];

describe("theme preset light/dark var coverage parity (CR-061)", () => {
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
		});
	}
});
