/**
 * Tests for `lib/theme-palette.ts` — the live-theme palette reader for
 * the share-stats image.
 *
 * Verifies:
 *   1. `readThemePalette` resolves the CSS custom properties currently
 *      applied on `document.documentElement` into a hex palette.
 *   2. Missing / unparseable tokens fall back to the stock palette (the
 *      image must never render broken/transparent colours).
 *   3. `legibleOn` enforces the minimum WCAG contrast: an accent too
 *      close to its background falls back to the legible foreground.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { contrastRatio } from "@/lib/color-utils";
import {
	FALLBACK_THEME_PALETTE,
	legibleOn,
	readThemePalette,
} from "@/lib/theme-palette";

describe("readThemePalette", () => {
	beforeEach(() => {
		// Reset any inline theme variables set by earlier tests.
		document.documentElement.removeAttribute("style");
	});

	it("reads the applied CSS custom properties into hex colours", () => {
		const root = document.documentElement;
		root.style.setProperty("--background", "#0d1117");
		root.style.setProperty("--foreground", "#e6edf3");
		root.style.setProperty("--card", "#161b22");
		root.style.setProperty("--muted-foreground", "#8b949e");
		root.style.setProperty("--primary", "#58a6ff");
		root.style.setProperty("--border", "#30363d");
		root.style.setProperty("--success", "#3fb950");
		root.style.setProperty("--warning", "#d29922");
		root.style.setProperty("--destructive", "#f85149");
		root.style.setProperty("--chart-1", "#58a6ff");
		root.style.setProperty("--chart-2", "#d2a8ff");

		const palette = readThemePalette();

		expect(palette.background).toBe("#0d1117");
		expect(palette.foreground).toBe("#e6edf3");
		expect(palette.card).toBe("#161b22");
		expect(palette.mutedForeground).toBe("#8b949e");
		expect(palette.primary).toBe("#58a6ff");
		expect(palette.border).toBe("#30363d");
		expect(palette.success).toBe("#3fb950");
		expect(palette.warning).toBe("#d29922");
		expect(palette.destructive).toBe("#f85149");
		expect(palette.charts[0]).toBe("#58a6ff");
		expect(palette.charts[1]).toBe("#d2a8ff");
		// Unset chart tokens fall back to the stock palette.
		expect(palette.charts[4]).toBe(FALLBACK_THEME_PALETTE.charts[4]);
	});

	it("falls back to the stock palette when no variables are set", () => {
		const palette = readThemePalette();
		expect(palette.background).toBe(FALLBACK_THEME_PALETTE.background);
		expect(palette.foreground).toBe(FALLBACK_THEME_PALETTE.foreground);
		expect(palette.primary).toBe(FALLBACK_THEME_PALETTE.primary);
	});
});

describe("legibleOn — minimum-contrast accent fallback", () => {
	it("keeps an accent that clears the 3:1 threshold", () => {
		// White on near-black → high contrast.
		expect(legibleOn("#ffffff", "#101014", "#ececf1")).toBe("#ffffff");
		expect(contrastRatio("#ffffff", "#101014")).toBeGreaterThanOrEqual(3);
	});

	it("falls back to the foreground when the accent is too close to the background", () => {
		// Same-ish colours → ratio ~1 → must fall back.
		expect(legibleOn("#17171c", "#101014", "#ececf1")).toBe("#ececf1");
	});

	it("returns the accent when it exactly meets the threshold", () => {
		// A mid-grey on white is ~4.6:1 — above 3:1.
		expect(legibleOn("#666666", "#ffffff", "#111111")).toBe("#666666");
	});
});
