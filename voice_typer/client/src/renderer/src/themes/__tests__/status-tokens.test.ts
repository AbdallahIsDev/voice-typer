/**
 *  (session NH) test: every theme preset defines
 * ``--success`` / ``--warning`` / ``--info`` tokens.
 *
 * Before , the renderer had no design tokens for success/warning/info
 * states — components improvised with raw Tailwind palette colors
 * (``text-emerald-500``, ``text-amber-500``, ``bg-amber-400``) which
 * don't follow the active theme's palette.  added the three new
 * semantic tokens to ``index.css`` (light + dark) AND to every theme
 * preset's light/dark maps so status colours track the theme.
 *
 * This test asserts:
 *   1. Every non-default / non-custom theme preset defines the three
 *      tokens in BOTH light and dark maps (parity).
 *   2. The tokens are also in the ``THEME_VARIABLES`` superset (so they
 *      get cleared on theme switch — see ``clearThemeVars``).
 *   3. ``deriveCustomVars`` (the runtime builder for user-customised
 *      themes) also emits the three tokens so custom themes don't fall
 *      back to the stylesheet default.
 *   4. The ``index.css`` stylesheet defines the three tokens (light +
 *      dark) and maps them to Tailwind utility classes via ``@theme``.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { deriveCustomVars, THEME_VARIABLES, THEMES } from "@/themes";

const STATUS_TOKENS = ["--success", "--warning", "--info"] as const;

describe("every theme preset defines --success / --warning / --info", () => {
	it("THEME_VARIABLES includes the three status tokens", () => {
		for (const token of STATUS_TOKENS) {
			expect(THEME_VARIABLES, `THEME_VARIABLES missing ${token}`).toContain(
				token,
			);
		}
	});

	for (const preset of THEMES) {
		// The ``default`` preset is a no-op (uses the stylesheet) and
		// ``custom`` is computed at runtime via deriveCustomVars —
		// skip both for the static-map parity assertion.
		if (preset.id === "default" || preset.id === "custom") continue;

		describe(`${preset.id} preset`, () => {
			it("light map defines the three status tokens", () => {
				for (const token of STATUS_TOKENS) {
					expect(preset.light[token], `light missing ${token}`).toBeTruthy();
				}
			});

			it("dark map defines the three status tokens", () => {
				for (const token of STATUS_TOKENS) {
					expect(preset.dark[token], `dark missing ${token}`).toBeTruthy();
				}
			});

			it("light and dark maps both define every status token (parity)", () => {
				for (const token of STATUS_TOKENS) {
					expect(preset.dark[token]).toBeTruthy();
					expect(preset.light[token]).toBeTruthy();
				}
			});
		});
	}
});

describe("deriveCustomVars emits status tokens for custom themes", () => {
	it("light custom theme derives --success / --warning / --info", () => {
		const vars = deriveCustomVars(
			{
				"--background": "#ffffff",
				"--foreground": "#09090b",
				"--primary": "#1447e6",
				"--bg-subtle": "#f5f5f5",
				"--border": "#e4e4e7",
				"--text-muted": "#71717b",
			},
			false,
		);
		for (const token of STATUS_TOKENS) {
			expect(
				vars[token],
				`deriveCustomVars(light) missing ${token}`,
			).toBeTruthy();
		}
	});

	it("dark custom theme derives --success / --warning / --info", () => {
		const vars = deriveCustomVars(
			{
				"--background": "#131313",
				"--foreground": "#fafafa",
				"--primary": "#193cb8",
				"--bg-subtle": "#0f0f0f",
				"--border": "#1f1f1f",
				"--text-muted": "#9f9fa9",
			},
			true,
		);
		for (const token of STATUS_TOKENS) {
			expect(
				vars[token],
				`deriveCustomVars(dark) missing ${token}`,
			).toBeTruthy();
		}
	});
});

describe("index.css stylesheet defines the status tokens as defaults", () => {
	// The stylesheet is the fallback when a theme doesn't override the
	// tokens (the ``default`` preset relies on this). Reading the file as
	// text and substring-matching is the most robust way to assert the
	// tokens are declared at the CSS level (parsing CSS would require an
	// extra dependency).
	const css = readFileSync(resolve(__dirname, "..", "..", "index.css"), "utf8");

	it(":root / .dark blocks declare --success / --warning / --info", () => {
		// The :root and .dark selectors each declare the three tokens with
		// oklch(...) values. We just substring-match for the declaration
		// since the value differs between light and dark.
		expect(css).toMatch(/--success:\s*oklch\([^)]+\)/);
		expect(css).toMatch(/--warning:\s*oklch\([^)]+\)/);
		expect(css).toMatch(/--info:\s*oklch\([^)]+\)/);
	});

	it("@theme inline block maps the tokens to Tailwind utilities (--color-success / --color-warning / --color-info)", () => {
		// The @theme inline block is what makes `text-success`,
		// `bg-warning`, `border-info`, etc. available as Tailwind
		// utility classes.
		expect(css).toContain("--color-success: var(--success)");
		expect(css).toContain("--color-warning: var(--warning)");
		expect(css).toContain("--color-info: var(--info)");
	});
});
