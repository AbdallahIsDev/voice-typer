import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "..", "index.css"), "utf8");
const showcase = readFileSync(
	resolve(__dirname, "../../../../../../design-system.html"),
	"utf8",
);

function blockAfter(source: string, start: number): string {
	const open = source.indexOf("{", start);
	expect(open).toBeGreaterThan(start);
	let depth = 1;
	for (let index = open + 1; index < source.length; index += 1) {
		if (source[index] === "{") depth += 1;
		if (source[index] === "}") depth -= 1;
		if (depth === 0) return source.slice(open + 1, index);
	}
	throw new Error("unclosed CSS block");
}

function blockEndAfter(source: string, start: number): number {
	const open = source.indexOf("{", start);
	let depth = 1;
	for (let index = open + 1; index < source.length; index += 1) {
		if (source[index] === "{") depth += 1;
		if (source[index] === "}") depth -= 1;
		if (depth === 0) return index + 1;
	}
	throw new Error("unclosed CSS block");
}

describe("corner shape design token", () => {
	it("gates every renderer declaration behind native support", () => {
		const gateStart = css.indexOf("@supports (corner-shape: squircle)");
		expect(gateStart).toBeGreaterThan(-1);
		const gate = blockAfter(css, gateStart);
		const gateEnd = blockEndAfter(css, gateStart);
		expect(gate).toContain("corner-shape:");
		const outsideGate = css.slice(0, gateStart) + css.slice(gateEnd);
		expect(outsideGate).not.toMatch(/(?:^|[;{}])\s*corner-shape\s*:/m);
	});

	it("uses the regular radius as fallback and compensates only with native support", () => {
		expect(css).toMatch(/--radius:\s*0\.625rem;/);
		const gateStart = css.indexOf("@supports (corner-shape: squircle)");
		const gate = blockAfter(css, gateStart);
		const gateEnd = blockEndAfter(css, gateStart);
		expect(gate).toMatch(
			/:root\s*\{[^}]*--radius:\s*1\.625rem(\s*!important)?;/s,
		);
		expect(css.slice(0, gateStart) + css.slice(gateEnd)).not.toMatch(
			/--radius:\s*1\.625rem(\s*!important)?;/,
		);
		expect(showcase).toMatch(/--radius:\s*0\.625rem;/);
		const showcaseGateStart = showcase.indexOf(
			"@supports (corner-shape: squircle)",
		);
		const showcaseGate = blockAfter(showcase, showcaseGateStart);
		const showcaseGateEnd = blockEndAfter(showcase, showcaseGateStart);
		expect(showcaseGate).toMatch(/:root\s*\{[^}]*--radius:\s*1\.625rem/s);
		expect(
			showcase.slice(0, showcaseGateStart) + showcase.slice(showcaseGateEnd),
		).not.toMatch(/--radius:\s*1\.625rem/);
	});

	it("applies the shared token to radius utilities and resets full circles", () => {
		const gateStart = css.indexOf("@supports (corner-shape: squircle)");
		const gate = blockAfter(css, gateStart);
		const familyStart = gate.indexOf(".rounded-lg");
		const circlesStart = gate.indexOf(".rounded-full");
		expect(familyStart).toBeGreaterThan(-1);
		expect(circlesStart).toBeGreaterThan(familyStart);
		expect(gate.slice(familyStart, circlesStart)).toContain(
			"corner-shape: var(--corner-shape",
		);
		expect(gate.slice(circlesStart)).toContain("corner-shape: round");
		expect(css).toMatch(/--corner-shape:\s*squircle;/);
	});

	it("keeps the showcase token and radius comparison in sync", () => {
		expect(showcase).toMatch(/--corner-shape:\s*squircle;/);
		const gateStart = showcase.indexOf("@supports (corner-shape: squircle)");
		expect(gateStart).toBeGreaterThan(-1);
		const gate = blockAfter(showcase, gateStart);
		const gateEnd = blockEndAfter(showcase, gateStart);
		expect(gate).toContain("corner-shape:");
		expect(showcase.slice(0, gateStart) + showcase.slice(gateEnd)).not.toMatch(
			/(?:^|[;{}])\s*corner-shape\s*:/m,
		);
		expect(showcase).toContain('data-corner-shape="round"');
		expect(showcase).toContain('data-corner-shape="squircle"');
	});
});
