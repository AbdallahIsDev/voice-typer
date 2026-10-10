/**
 * Card-look contract for the vendored picker.
 *
 * The calendar is a card, so every resting edge in it must be the app-wide
 * card border — `border-border/8`, i.e. 8% of `--border` — and its corners
 * must be the app's single radius step, `rounded-lg`. The vendor shipped
 * its dividers at full-strength `var(--border)` (a hard black hairline in
 * light mode) and painted its own `border-radius` by hand, which meant the
 * panel missed the global squircle corner-shape: measured in Chromium the
 * panel rendered `superellipse(1)` while every card renders
 * `superellipse(2)`, so the calendar read as a different material.
 *
 * Both are pure CSS/class facts that no DOM assertion can catch (jsdom has
 * no layout and no colour resolution), so this is a source-shape guard —
 * the same approach the months-ref guard next door takes, and the same
 * pattern this repo uses for other "silently degrades" contracts.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const read = (...segments: string[]) =>
	readFileSync(path.resolve(__dirname, "..", ...segments), "utf8");

const MODULE_CSS = read(
	"date-range-picker-utils",
	"date-range-picker.module.css",
);
const PICKER_TSX = read("date-range-picker.tsx");

/** Every declaration that paints a border edge, minus radii and resets. */
function borderEdgeDeclarations(css: string): string[] {
	return (
		css
			.split("\n")
			.map((line) => line.trim())
			// border / border-top / border-right / border-color, but NOT
			// border-radius and NOT the `border: 0` resets.
			.filter((line) =>
				/^border(-(top|right|bottom|left))?(-color)?\s*:/.test(line),
			)
			.filter((line) => !line.includes("border-radius"))
			.filter((line) => !/:\s*0\s*;?$/.test(line))
			// `border: 1px solid …` and `border-color: …`
			.filter((line) => line.includes("solid") || line.includes("border-color"))
	);
}

describe("DateRangePicker card-look contract", () => {
	it("declares the card border as the shared 8% mix of --border", () => {
		expect(MODULE_CSS).toMatch(
			/--border-card:\s*color-mix\(\s*in oklch,\s*var\(--border\)\s*8%,\s*transparent\s*\)/,
		);
	});

	it("paints every border edge with that token", () => {
		const decls = borderEdgeDeclarations(MODULE_CSS);
		// Guard the guard: if the scan stops finding declarations the
		// assertion below would pass vacuously.
		expect(decls.length).toBeGreaterThanOrEqual(5);
		const offenders = decls.filter((d) => !d.includes("var(--border-card)"));
		// `--border-strong` is allowed ONLY inside the prefers-contrast
		// block, where a deliberately heavier edge is the whole point.
		const outsideContrast = offenders.filter(
			(d) => !d.includes("var(--border-strong)"),
		);
		expect(
			outsideContrast,
			`non-card borders: ${outsideContrast.join(" | ")}`,
		).toEqual([]);
	});

	it("does not resurrect the full-strength divider token", () => {
		// The vendor's `--border-subtle: var(--border)` was a 100% black
		// hairline; it must stay gone.
		expect(MODULE_CSS).not.toMatch(/--border-subtle/);
	});

	it("gives the panel the rounded-lg class so it gets the app's squircle corners", () => {
		// A bare `border-radius` declaration sets the radius but NOT the
		// corner shape: index.css shapes corners via :where(.rounded-lg).
		expect(PICKER_TSX).toMatch(
			/className=\{`\$\{styles\.panel\} rounded-lg`\}/,
		);
	});

	it("leaves the radius to the class, not a duplicate module declaration", () => {
		const floating = MODULE_CSS.slice(
			MODULE_CSS.indexOf(".panel[data-floating]"),
		).slice(0, 600);
		expect(floating).not.toMatch(/border-radius/);
	});
});
