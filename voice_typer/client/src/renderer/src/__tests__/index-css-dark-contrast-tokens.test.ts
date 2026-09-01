/**
 * Dark-mode `--input` / `--sidebar-border` contrast tokens.
 *
 * WCAG 1.4.11 (Non-Text Contrast, 3:1) — the base `.dark` block in
 * index.css must define these tokens as OPAQUE values, matching the
 * opaque values every per-preset dark theme already carries (so the
 * base fallback == preset treatment). The previous alpha-composited
 * values (white at 15% / 10% over a near-black background) computed
 * to ~1.5:1.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const cssPath = resolve(__dirname, "..", "index.css");
const css = readFileSync(cssPath, "utf8");

/** Extract the raw `.dark { ... }` block from the stylesheet. */
function darkBlock(): string {
	const start = css.indexOf(".dark {");
	expect(start).toBeGreaterThan(-1);
	const end = css.indexOf("}", start);
	expect(end).toBeGreaterThan(start);
	return css.slice(start, end);
}

describe("index.css .dark — opaque input/sidebar-border tokens", () => {
	it("--input is opaque (no alpha) at the preset dark lightness", () => {
		const match = /--input:\s*([^;]+);/.exec(darkBlock());
		const value = match?.[1];
		if (!value) throw new Error("no --input declaration in the .dark block");
		expect(value.trim()).not.toContain("/");
		// Preset dark themes use L 0.52–0.54; the base fallback sits at
		// the same lightness band (github.ts dark uses 0.52).
		expect(value.trim()).toMatch(/^oklch\(0\.5[234]/);
	});

	it("--sidebar-border is opaque (no alpha) at the preset dark lightness", () => {
		const match = /--sidebar-border:\s*([^;]+);/.exec(darkBlock());
		const value = match?.[1];
		if (!value) {
			throw new Error("no --sidebar-border declaration in the .dark block");
		}
		expect(value.trim()).not.toContain("/");
		// Preset dark themes use L 0.18–0.22 (dark-on-dark divider
		// treatment); the base fallback sits inside that band.
		expect(value.trim()).toMatch(/^oklch\(0\.(1[89]|2[0-2]?)(\D|$)/);
	});

	it("no alpha-composited oklch(1 0 0 / N%) tokens remain in the .dark block", () => {
		expect(darkBlock()).not.toMatch(/oklch\(1 0 0 \//);
	});
});
