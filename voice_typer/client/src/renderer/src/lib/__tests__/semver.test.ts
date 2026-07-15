/**
 * Tests for the semver comparison utility (b-review Finding 9 / F3).
 *
 * Lexicographic comparison breaks for normal semver ordering:
 *   "1.10.0" < "1.9.0" lexicographically (because "1" < "9" at index 2),
 *   but 1.10.0 > 1.9.0 numerically.
 *
 * `compareSemver` must split on `.`, parse each part as an integer, and
 * compare pairwise so the ordering matches what users expect from
 * version numbers.
 */
import { describe, expect, it } from "vitest";

import { compareSemver } from "@/lib/semver";

describe("compareSemver", () => {
	it("returns 0 for identical versions", () => {
		expect(compareSemver("1.0.0", "1.0.0")).toBe(0);
		expect(compareSemver("2.5.7", "2.5.7")).toBe(0);
	});

	it("returns -1 when a < b (1.9.0 < 1.10.0)", () => {
		// This is the regression case from the bug report — lexicographic
		// comparison would return "1.9.0" > "1.10.0" (wrong).
		expect(compareSemver("1.9.0", "1.10.0")).toBe(-1);
	});

	it("returns 1 when a > b (1.10.0 > 1.9.0)", () => {
		expect(compareSemver("1.10.0", "1.9.0")).toBe(1);
	});

	it("returns -1 when a < b (1.0.0 < 1.0.1)", () => {
		expect(compareSemver("1.0.0", "1.0.1")).toBe(-1);
	});

	it("returns 1 when a > b (2.0.0 > 1.99.99)", () => {
		// Major version bump beats any minor/patch increment.
		expect(compareSemver("2.0.0", "1.99.99")).toBe(1);
	});

	it("handles unequal lengths by treating missing parts as 0", () => {
		// "1.0" → [1, 0, 0]; "1.0.0" → [1, 0, 0] — equal.
		expect(compareSemver("1.0", "1.0.0")).toBe(0);
		// "1.0.0.0" → [1, 0, 0, 0]; "1.0" → [1, 0, 0, 0] — equal.
		expect(compareSemver("1.0.0.0", "1.0")).toBe(0);
		// "1.0.1" > "1.0" because the third part [1] > [0].
		expect(compareSemver("1.0.1", "1.0")).toBe(1);
		// "1.0" < "1.0.1".
		expect(compareSemver("1.0", "1.0.1")).toBe(-1);
	});

	it("handles single-component versions", () => {
		expect(compareSemver("2", "1")).toBe(1);
		expect(compareSemver("1", "2")).toBe(-1);
		expect(compareSemver("1", "1")).toBe(0);
	});

	it("handles major version differences", () => {
		expect(compareSemver("2.0.0", "1.0.0")).toBe(1);
		expect(compareSemver("1.0.0", "2.0.0")).toBe(-1);
	});

	it("handles minor version differences", () => {
		expect(compareSemver("1.5.0", "1.4.0")).toBe(1);
		expect(compareSemver("1.4.0", "1.5.0")).toBe(-1);
	});

	it("handles patch version differences", () => {
		expect(compareSemver("1.0.5", "1.0.4")).toBe(1);
		expect(compareSemver("1.0.4", "1.0.5")).toBe(-1);
	});

	it("handles double-digit patch versions", () => {
		// Another lexicographic-vs-numeric edge case:
		// "1.0.10" vs "1.0.2" — lexicographically "1.0.10" < "1.0.2"
		// because "1" < "2" at index 4.
		expect(compareSemver("1.0.10", "1.0.2")).toBe(1);
		expect(compareSemver("1.0.2", "1.0.10")).toBe(-1);
	});

	it("treats non-numeric parts as 0 (graceful degradation)", () => {
		// Pre-release tags like "-rc1" aren't honoured here. The
		// About-page use case compares against GitHub release tags
		// which are always clean "vMAJOR.MINOR.PATCH", so we just
		// need graceful degradation rather than full semver-spec
		// prerelease ordering.
		expect(compareSemver("1.0.0-rc1", "1.0.0")).toBe(0);
		expect(compareSemver("1.0.0", "1.0.0-rc1")).toBe(0);
	});

	it("works for the exact About.tsx use case (remote newer)", () => {
		// APP_VERSION comes from package.json; remote is the GitHub
		// releases tag. When remote is newer, compareSemver(remote, APP_VERSION)
		// should return > 0 so the "new version available" toast fires.
		const APP_VERSION = "1.0.0";
		const remote = "1.2.0";
		expect(compareSemver(remote, APP_VERSION)).toBeGreaterThan(0);
	});

	it("works for the exact About.tsx use case (on latest)", () => {
		const APP_VERSION = "1.0.0";
		const remote = "1.0.0";
		expect(compareSemver(remote, APP_VERSION)).toBe(0);
	});

	it("works for the exact About.tsx use case (installed newer)", () => {
		const APP_VERSION = "1.2.0";
		const remote = "1.0.0";
		expect(compareSemver(remote, APP_VERSION)).toBeLessThan(0);
	});
});
