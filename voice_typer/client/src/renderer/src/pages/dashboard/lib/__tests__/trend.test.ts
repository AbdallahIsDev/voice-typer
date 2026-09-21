import { describe, expect, it } from "vitest";

import { computeTrend } from "../trend";

describe("computeTrend", () => {
	it("returns null without a usable baseline", () => {
		expect(computeTrend(10, null)).toBeNull();
		expect(computeTrend(10, undefined)).toBeNull();
		expect(computeTrend(10, 0)).toBeNull();
		expect(computeTrend(0, -1)).toBeNull();
	});

	it("reports a flat trend as 0% up", () => {
		expect(computeTrend(10, 10)).toEqual({ pct: 0, up: true });
	});

	it("reports growth as a rounded percentage", () => {
		expect(computeTrend(15, 10)).toEqual({ pct: 50, up: true });
		expect(computeTrend(13, 10)).toEqual({ pct: 30, up: true });
	});

	it("reports decline with the absolute percentage", () => {
		expect(computeTrend(5, 10)).toEqual({ pct: 50, up: false });
	});
});
