import { afterEach, describe, expect, it, vi } from "vitest";

import { cssColorToHex, mixHexColors } from "@/lib/color-utils";

describe("mixHexColors", () => {
	it("returns a unchanged at weight 0 and b unchanged at weight 1", () => {
		expect(mixHexColors("#112233", "#aabbcc", 0)).toBe("#112233");
		expect(mixHexColors("#112233", "#aabbcc", 1)).toBe("#aabbcc");
	});

	it("returns the exact midpoint at weight 0.5", () => {
		expect(mixHexColors("#000000", "#ffffff", 0.5)).toBe("#808080");
	});

	it("supports 3-digit hex input", () => {
		expect(mixHexColors("#000", "#fff", 0.5)).toBe("#808080");
	});

	it("clamps weight to [0, 1]", () => {
		expect(mixHexColors("#000000", "#ffffff", 2)).toBe("#ffffff");
		expect(mixHexColors("#000000", "#ffffff", -1)).toBe("#000000");
	});

	it("treats unparseable input as black (defensive contract)", () => {
		expect(mixHexColors("not-a-color", "#ffffff", 0.5)).toBe("#808080");
		expect(mixHexColors("#ffffff", "not-a-color", 0.5)).toBe("#808080");
	});

	it("lifts a dark surface toward the foreground (share-image card framing)", () => {
		// card #17171c → 6% toward foreground #ececf1.
		expect(mixHexColors("#17171c", "#ececf1", 0.06)).toBe("#242429");
	});
});

describe("cssColorToHex — per-input cache", () => {
	// The resolution cache is module-level, so every test below uses
	// DISTINCT color strings — a string already resolved by an earlier
	// test would return from the cache and the DOM-probe spy would
	// never fire.
	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("resolves the same input twice with only one DOM probe", () => {
		// Spy on the exact DOM surface _cssColorToHexViaDOM uses
		// (createElement + body.appendChild) — the second call must be
		// served from the cache without touching the DOM again.
		const createSpy = vi.spyOn(document, "createElement");
		const appendSpy = vi.spyOn(document.body, "appendChild");

		const first = cssColorToHex("rgb(10, 20, 30)");
		const second = cssColorToHex("rgb(10, 20, 30)");

		expect(first).toBe("#0a141e");
		expect(second).toBe(first);
		expect(createSpy).toHaveBeenCalledTimes(1);
		expect(appendSpy).toHaveBeenCalledTimes(1);
	});

	it("keeps the hex passthrough behaviour (cache does not alter output)", () => {
		expect(cssColorToHex("#AaBbCc")).toBe("#aabbcc");
		expect(cssColorToHex("#abc")).toBe("#aabbcc");
		expect(cssColorToHex("#112233")).toBe("#112233");
	});

	it("caches unparseable input so repeated bad values skip the DOM probe", () => {
		const createSpy = vi.spyOn(document, "createElement");
		const appendSpy = vi.spyOn(document.body, "appendChild");

		// Public contract: unparseable input resolves to #000000 (the
		// unparseable marker cached internally is null).
		const first = cssColorToHex("definitely-not-a-colour $$");
		const second = cssColorToHex("definitely-not-a-colour $$");

		expect(first).toBe("#000000");
		expect(second).toBe("#000000");
		expect(createSpy).toHaveBeenCalledTimes(1);
		expect(appendSpy).toHaveBeenCalledTimes(1);
	});
});
