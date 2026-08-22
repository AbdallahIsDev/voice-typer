/**
 * CSS contract for the mic-button idle glow pulse.
 *
 * The glow animation MUST stay repaint-free: the old implementation
 * animated `box-shadow` inside the keyframes, forcing the browser to
 * re-rasterize the shadow (paint) on every frame of the infinite
 * loop. The current contract:
 *
 *   1. `@keyframes glowPulse` animates OPACITY ONLY — never
 *      box-shadow (or any paint-triggering property).
 *   2. The glow itself is a STATIC box-shadow painted once on the
 *      `.animate-glow-pulse::after` pseudo-element (compositor-only
 *      opacity modulation on top).
 *   3. The pseudo-element must not intercept pointer input and must
 *      inherit the host's border-radius.
 *   4. The prefers-reduced-motion block still covers the pseudo-
 *      element (via the `*::after` selector) so the pulse stops for
 *      users who opt out of motion.
 *
 * Static-source assertions (same pattern as the i18n CSS guards): the
 * stylesheet is small and locally owned, so string-level checks keep
 * this fast and dependency-free.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const cssPath = resolve(__dirname, "..", "index.css");
const css = readFileSync(cssPath, "utf8");

/** Extract a top-level `@keyframes <name> { ... }` block (brace-balanced). */
function extractKeyframes(name: string): string | null {
	const marker = `@keyframes ${name} {`;
	const start = css.indexOf(marker);
	if (start === -1) return null;
	let depth = 0;
	for (let i = start + marker.length - 1; i < css.length; i++) {
		const ch = css[i];
		if (ch === "{") depth++;
		else if (ch === "}") {
			depth--;
			if (depth === 0) return css.slice(start, i + 1);
		}
	}
	return null;
}

/** Extract the first `.selector { ... }` rule block (brace-balanced). */
function extractRule(selector: string): string | null {
	const start = css.indexOf(`${selector} {`);
	if (start === -1) return null;
	let depth = 0;
	for (let i = start + selector.length; i < css.length; i++) {
		const ch = css[i];
		if (ch === "{") depth++;
		else if (ch === "}") {
			depth--;
			if (depth === 0) return css.slice(start, i + 1);
		}
	}
	return null;
}

describe("glowPulse — compositor-only glow animation contract", () => {
	it("the keyframes animate opacity ONLY (no box-shadow repaint per frame)", () => {
		const keyframes = extractKeyframes("glowPulse");
		expect(keyframes).toBeTruthy();
		expect(keyframes).toContain("opacity");
		expect(keyframes).not.toContain("box-shadow");
	});

	it("the glow is a static box-shadow painted once on the ::after pseudo-element", () => {
		const rule = extractRule(".animate-glow-pulse::after");
		expect(rule).toBeTruthy();
		expect(rule).toContain("box-shadow:");
		expect(rule).toContain("content:");
		expect(rule).toContain("position: absolute");
		// Inherit the host radius (the mic button is rounded-full) and
		// never intercept clicks.
		expect(rule).toContain("border-radius: inherit");
		expect(rule).toContain("pointer-events: none");
	});

	it("the host selector opts the element into positioning (relative) for the pseudo overlay", () => {
		const host = extractRule(".animate-glow-pulse");
		expect(host).toBeTruthy();
		expect(host).toContain("position: relative");
	});

	it("the animation rhythm is preserved (2.5s ease-in-out infinite)", () => {
		const rule = extractRule(".animate-glow-pulse::after");
		expect(rule).toContain("animation: glowPulse 2.5s ease-in-out infinite");
	});

	it("prefers-reduced-motion still covers the pseudo-element (pulse stops)", () => {
		const reduceIdx = css.indexOf("@media (prefers-reduced-motion: reduce)");
		expect(reduceIdx).toBeGreaterThanOrEqual(0);
		const reduceBlock = css.slice(reduceIdx, css.indexOf("}", reduceIdx) + 200);
		// The global selector list includes *::after, which matches the
		// glow pseudo-element — the 0.01ms/1-iteration clamp settles it
		// onto its base (static, dim) opacity instead of looping.
		expect(reduceBlock).toContain("*::after");
		expect(reduceBlock).toContain("animation-duration: 0.01ms");
		expect(reduceBlock).toContain("animation-iteration-count: 1");
	});
});
