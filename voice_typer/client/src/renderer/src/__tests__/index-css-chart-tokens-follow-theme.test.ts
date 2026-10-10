import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const cssPath = resolve(__dirname, "..", "index.css");
const css = readFileSync(cssPath, "utf8");

/** The `:root { ... }` block (light scheme + the derived tokens). */
function rootBlock(): string {
	const start = css.indexOf(":root {");
	expect(start).toBeGreaterThan(-1);
	return css.slice(start, css.indexOf(".dark {"));
}

/** Extract the raw `.dark { ... }` block from the stylesheet. */
function darkBlock(): string {
	const start = css.indexOf(".dark {");
	expect(start).toBeGreaterThan(-1);
	const end = css.indexOf("}", start);
	expect(end).toBeGreaterThan(start);
	return css.slice(start, end);
}

/** Strip CSS comments — several tokens are discussed by name in prose
 *  above their declaration, and a bare regex would match the prose. */
function stripComments(block: string): string {
	return block.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Read a `--token: <value>;` declaration out of a CSS block. */
function tokenIn(block: string, name: string): string {
	const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(stripComments(block));
	const value = match?.[1];
	if (!value) throw new Error(`no --${name} declaration in block`);
	return value.trim();
}

/** A bare `oklch(0.5 …)` colour literal — as opposed to the `oklch` in
 *  `color-mix(in oklch, …)`, which is a colour space, not a value. */
const LITERAL_OKLCH = /oklch\(\s*[\d.]/;

/** Every `--chart-*` token the heatmap card actually renders with. */
const RENDERED_CHART_TOKENS = [
	"chart-background",
	"chart-foreground",
	"chart-foreground-muted",
	"chart-crosshair",
	"chart-grid",
	"chart-tooltip-background",
	"chart-tooltip-foreground",
	"chart-tooltip-muted",
	"chart-marker-background",
	"chart-marker-border",
	"chart-marker-foreground",
	"chart-label",
] as const;

describe("index.css Bklit chart tokens follow the theme", () => {
	it("every rendered chart token is DERIVED from a semantic token", () => {
		// The Bklit registry ships literal greys for these. A literal
		// would freeze the chart on the default palette: the 12 theme
		// presets override --accent / --foreground / --surface / etc.
		// inline on <html> and never a --chart-* token. Same trap the
		// --sidebar token documents.
		const root = rootBlock();
		for (const name of RENDERED_CHART_TOKENS) {
			const value = tokenIn(root, name);
			expect(value, `--${name} must reference a token`).toContain("var(--");
			expect(
				LITERAL_OKLCH.test(value),
				`--${name} is a literal, so it cannot follow a theme preset: ${value}`,
			).toBe(false);
		}
	});

	it("step 01 is the neutral empty cell, not a weak accent tint", () => {
		// Level 0 means "no dictations". A day with nothing to show is
		// not a small amount of data, so it must not be drawn in the
		// data's own colour — it wears the same hairline the app draws
		// every border with (--border at 8%, the default border weight),
		// which also flips from black to white with the scheme.
		const value = tokenIn(rootBlock(), "chart-scale-01");
		expect(value).toContain("color-mix(");
		expect(value).toContain("var(--border)");
		expect(value).toContain("8%");
		expect(value).toContain("transparent");
		expect(value).not.toContain("var(--accent)");
		expect(LITERAL_OKLCH.test(value)).toBe(false);
	});

	it("the data ramp runs canvas → full accent", () => {
		const root = rootBlock();
		expect(tokenIn(root, "chart-scale-05")).toBe("var(--accent)");
		for (const step of ["02", "03", "04"] as const) {
			const value = tokenIn(root, `chart-scale-${step}`);
			expect(value).toContain("color-mix(");
			expect(value).toContain("var(--accent)");
			expect(value).toContain("var(--background)");
			expect(LITERAL_OKLCH.test(value)).toBe(false);
		}
	});

	it("the ramp's accent share increases step by step", () => {
		// A ramp that is not monotonic renders as noise rather than as
		// "more dictation = darker". Level 0 sits OUTSIDE the ramp (it
		// is the neutral empty cell), so the run starts at step 02.
		const root = rootBlock();
		const shares = (["02", "03", "04"] as const).map((step) => {
			const value = tokenIn(root, `chart-scale-${step}`);
			const pct = /var\(--accent\)\s+([\d.]+)%/.exec(value)?.[1];
			if (pct === undefined) {
				throw new Error(`no accent mix percentage in: ${value}`);
			}
			return Number.parseFloat(pct);
		});
		for (let i = 1; i < shares.length; i += 1) {
			expect(shares[i]).toBeGreaterThan(shares[i - 1] as number);
		}
		// Step 5 is the full accent, so the top of the ramp must be the
		// strongest mix (100%).
		expect(shares[shares.length - 1]).toBeLessThan(100);
	});

	it("mixes in sRGB — oklch sweeps the ramp's low steps through pink", () => {
		// `color-mix(in oklch, #1447e6 12%, #ffffff)` resolves to
		// #fbe3ee — PINK — because an achromatic colour's hue reads as 0
		// and oklch interpolates hue. Every chart mix here that tints a
		// chromatic token toward an achromatic one must therefore stay
		// in sRGB (which resolves that same mix to #e3e9fc). Measured in
		// Chrome; see the note in index.css. Step 01 mixes two
		// achromatic colours, where the space is moot — it stays sRGB so
		// the whole scale is written one way.
		const root = rootBlock();
		for (const name of [
			"chart-grid",
			"chart-marker-border",
			"chart-scale-01",
			"chart-scale-02",
			"chart-scale-03",
			"chart-scale-04",
		] as const) {
			const value = tokenIn(root, name);
			expect(value, `--${name} must mix in srgb`).toContain("in srgb");
			expect(value, `--${name} must not mix in oklch`).not.toContain(
				"in oklch",
			);
		}
	});

	it("the dark scheme does NOT restate any chart token", () => {
		// One definition, in :root, that both schemes inherit — so the
		// ramp cannot drift between light and dark, and a preset needs
		// no per-scheme chart overrides.
		const dark = stripComments(darkBlock());
		expect(dark).not.toMatch(/--chart-[a-z0-9-]+\s*:/);
	});

	it("the chart tooltip reuses the app's tooltip surface tokens", () => {
		// ui/tooltip.tsx renders `bg-surface text-foreground`, so the
		// chart tooltip must resolve to the same pair or it reads as a
		// foreign object on every theme.
		const root = rootBlock();
		expect(tokenIn(root, "chart-tooltip-background")).toBe("var(--surface)");
		expect(tokenIn(root, "chart-tooltip-foreground")).toBe("var(--foreground)");
	});
});
