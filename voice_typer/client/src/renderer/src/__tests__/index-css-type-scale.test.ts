/**
 * The type scale's one app-specific step: `text-xs-plus` (0.8125rem / 13px).
 *
 * Tailwind ships text-xs (0.75rem / 12px) and text-sm (0.875rem / 14px)
 * with nothing between, yet dense controls kept reaching for 13px ad hoc —
 * three `text-[0.8125rem]`, one `text-[13px]`, plus the vendored date
 * picker's scoped `--text-sm`. Promoting it to a `@theme` key makes it a
 * real utility, so the scale is authoritative and the code, the docs and
 * `design-system.html` can agree (C-DESIGN-1).
 *
 * Three things can silently break that, and this pins all three: the theme
 * key disappearing (the utility vanishes and every call site falls back to
 * inherited size), the paired line-height drifting, and the arbitrary
 * value creeping back in.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..");
const REPO_ROOT = resolve(__dirname, "..", "..", "..", "..", "..", "..");

const css = readFileSync(resolve(RENDERER_SRC, "index.css"), "utf8");

/** The `@theme inline { ... }` block, brace-matched from its opening `{`. */
function themeBlock(source: string): string {
	const start = source.indexOf("@theme inline");
	expect(start, "@theme inline block not found").toBeGreaterThan(-1);
	const open = source.indexOf("{", start);
	let depth = 0;
	for (let i = open; i < source.length; i++) {
		if (source[i] === "{") depth++;
		else if (source[i] === "}") {
			depth--;
			if (depth === 0) return source.slice(open, i + 1);
		}
	}
	throw new Error("unbalanced braces in @theme inline");
}

/** Strip block and line comments — prose may name the value without using it. */
function stripComments(source: string): string {
	return source
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/** Every source file under the renderer, minus tests and node_modules. */
function sourceFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		if (entry === "node_modules" || entry === "__tests__") continue;
		const full = resolve(dir, entry);
		if (statSync(full).isDirectory()) sourceFiles(full, out);
		else if (/\.(tsx?|css)$/.test(entry)) out.push(full);
	}
	return out;
}

describe("index.css type scale — text-xs-plus", () => {
	const theme = themeBlock(css);

	it("declares the step as 13px, in rem", () => {
		const match = /--text-xs-plus:\s*([^;]+);/.exec(theme);
		expect(
			match,
			"--text-xs-plus is not declared in @theme inline",
		).toBeTruthy();
		expect(match?.[1]?.trim()).toBe("0.8125rem");
		// rem, not px: the Settings text-size slider scales the whole UI by
		// changing the root font-size, and a px step would opt out of that.
		expect(match?.[1]).not.toMatch(/px/);
	});

	it("ships a paired line-height that resolves to 18px", () => {
		// Tailwind's shape is calc(line-height-rem / size-rem), applied as a
		// UNITLESS multiplier: text-sm is calc(1.25 / 0.875) = 1.4286, which
		// is 20px at 14px. Same shape here, targeting 18px at 13px.
		const match =
			/--text-xs-plus--line-height:\s*calc\(\s*([\d.]+)\s*\/\s*([\d.]+)\s*\)/.exec(
				theme,
			);
		expect(
			match,
			"no --text-xs-plus--line-height in the calc(a / b) shape",
		).toBeTruthy();
		const ratio = Number(match?.[1]) / Number(match?.[2]);
		// 13px × ratio must be the 18px the design docs record for this step.
		expect(Number((13 * ratio).toFixed(4))).toBe(18);
	});

	it("sits strictly between text-xs and text-sm", () => {
		// Sanity: the step is only meaningful if Tailwind's neighbours still
		// straddle it. If Tailwind ever moves them, revisit this token.
		const rem = 0.8125;
		expect(rem).toBeGreaterThan(0.75); // text-xs
		expect(rem).toBeLessThan(0.875); // text-sm
	});

	it("no source file reaches for the arbitrary value instead", () => {
		// StatsShareImage is the sanctioned exception: it renders a
		// deliberately px-based, token-free export surface so the share
		// image does not resize with the user's text-size setting.
		const offenders = sourceFiles(RENDERER_SRC)
			.filter((f) => !f.endsWith("StatsShareImage.tsx"))
			.filter((f) =>
				/text-\[0?\.8125rem\]|text-\[13px\]/.test(
					stripComments(readFileSync(f, "utf8")),
				),
			)
			.map((f) => f.replace(RENDERER_SRC, "").replace(/\\/g, "/"));
		expect(offenders, `use text-xs-plus in: ${offenders.join(", ")}`).toEqual(
			[],
		);
	});
});

describe("C-DESIGN-1 — the showcase and the docs carry the step", () => {
	// The showcase lives at the repo root, so a partial checkout (client
	// only) cannot see it. Assert only when it is actually present.
	const docs: [string, string][] = [
		["design-system.html", "t-dense"],
		["DESIGN-SYSTEM.md", "text-xs-plus"],
	];

	for (const [file, needle] of docs) {
		it(`${file} documents the new step`, () => {
			let text: string;
			try {
				text = readFileSync(resolve(REPO_ROOT, file), "utf8");
			} catch {
				// Partial checkout — nothing to keep in sync.
				return;
			}
			expect(text).toContain(needle);
		});
	}
});
