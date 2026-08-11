// @vitest-environment node
/**
 * HU-29 regression tests for the bubble crash-storm tracker factory.
 *
 * `createCrashStormTracker` takes a 4th `prefix` argument so bubble
 * crash storms log with `[BUBBLE]` instead of the legacy hardcoded
 * `[MAIN]` (which misattributed bubble crash storms to the main
 * window in the logs — the original HU-29 defect).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../logging", () => ({
	log: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { log } from "../../../logging";
import { createCrashStormTracker } from "../crash-storm";

describe("HU-29: bubble crash-storm factory prefix", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("returns false under the threshold and true once exceeded", () => {
		const tracker = createCrashStormTracker("Bubble", 5, 60_000, "[BUBBLE]");
		for (let i = 0; i < 5; i++) {
			expect(tracker.record()).toBe(false);
		}
		// 6th crash trips the threshold (length > 5).
		expect(tracker.record()).toBe(true);
	});

	it("logs the storm line with the [BUBBLE] prefix (not [MAIN])", () => {
		const tracker = createCrashStormTracker("Bubble", 5, 60_000, "[BUBBLE]");
		for (let i = 0; i < 6; i++) {
			tracker.record();
		}
		const calls = (log.error as ReturnType<typeof vi.fn>).mock.calls.map((c) =>
			String(c[0]),
		);
		expect(
			calls.some((m) =>
				m.includes("[BUBBLE] Bubble render-process-gone storm"),
			),
		).toBe(true);
		expect(calls.some((m) => m.includes("[MAIN]"))).toBe(false);
	});

	it("logs with a custom prefix when passed (e.g. [MAIN] for the main window)", () => {
		const tracker = createCrashStormTracker("Main", 5, 60_000, "[MAIN]");
		for (let i = 0; i < 6; i++) {
			tracker.record();
		}
		const calls = (log.error as ReturnType<typeof vi.fn>).mock.calls.map((c) =>
			String(c[0]),
		);
		expect(
			calls.some((m) => m.includes("[MAIN] Main render-process-gone storm")),
		).toBe(true);
	});

	it("reset() clears the sliding window", () => {
		const tracker = createCrashStormTracker("Bubble", 5, 60_000, "[BUBBLE]");
		for (let i = 0; i < 6; i++) {
			tracker.record();
		}
		expect(tracker.record()).toBe(true);
		tracker.reset();
		// Back under threshold after a reset.
		expect(tracker.record()).toBe(false);
	});
});
