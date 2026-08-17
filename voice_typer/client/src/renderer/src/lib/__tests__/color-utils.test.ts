import { describe, expect, it } from "vitest";

import { mixHexColors } from "@/lib/color-utils";

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
