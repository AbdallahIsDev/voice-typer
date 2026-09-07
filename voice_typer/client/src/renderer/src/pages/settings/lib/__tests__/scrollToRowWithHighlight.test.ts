/**
 * Focused tests for `scrollToRowWithHighlight` — the shared Settings
 * deep-link "scroll to a row and ring it" machinery extracted from the
 * two near-twin effects in `useSettingsDeepLinks`.
 *
 * Pins the byte-identical behavior contract:
 *   - first attempt on a 0ms timer, bounded retries 50ms apart (max 60),
 *   - one-shot per target via the shared scrolledTarget guard,
 *   - `scrollIntoView({ behavior: "smooth", block: "center" })` on find,
 *   - ring lifetime starts at FOUND time (not at effect time),
 *   - a previously armed highlight timer is cleared before re-arming,
 *   - the cleanup cancels pending retries (stale scrollIntoView no-op).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { scrollToRowWithHighlight } from "@/pages/settings/lib/scrollToRowWithHighlight";

interface Shared {
	scrolledTarget: { current: string | null };
	highlightTimer: { current: ReturnType<typeof setTimeout> | null };
}

function makeShared(): Shared {
	return {
		scrolledTarget: { current: null },
		highlightTimer: { current: null },
	};
}

beforeEach(() => {
	vi.useFakeTimers();
	document.body.innerHTML = "";
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = "";
});

describe("scrollToRowWithHighlight", () => {
	it("scrolls the matched row into view on the 0ms first attempt", () => {
		const scrollSpy = vi.fn();
		(
			Element.prototype as unknown as { scrollIntoView: unknown }
		).scrollIntoView = scrollSpy;
		const row = document.createElement("div");
		row.textContent = "Prewarm Status";
		document.body.appendChild(row);
		const shared = makeShared();

		const cleanup = scrollToRowWithHighlight({
			shared,
			target: "prewarm",
			matchFn: () =>
				(row.textContent ?? "").includes("Prewarm") ? row : undefined,
			onExpire: () => {},
		});

		vi.advanceTimersByTime(1);
		expect(scrollSpy).toHaveBeenCalledWith(
			expect.objectContaining({ behavior: "smooth", block: "center" }),
		);
		expect(shared.scrolledTarget.current).toBe("prewarm");
		cleanup();
	});

	it("retries every 50ms until the row renders (bounded to 60 attempts)", () => {
		const scrollSpy = vi.fn();
		(
			Element.prototype as unknown as { scrollIntoView: unknown }
		).scrollIntoView = scrollSpy;
		const row = document.createElement("div");
		const shared = makeShared();
		let rowRendered = false;

		const cleanup = scrollToRowWithHighlight({
			shared,
			target: "late-row",
			matchFn: () => (rowRendered ? row : undefined),
			onExpire: () => {},
		});

		// Row not rendered yet — attempts at 50ms intervals must NOT scroll.
		vi.advanceTimersByTime(150);
		expect(scrollSpy).not.toHaveBeenCalled();

		// Row renders; the next retry finds it.
		document.body.appendChild(row);
		rowRendered = true;
		vi.advanceTimersByTime(50);
		expect(scrollSpy).toHaveBeenCalledTimes(1);

		// Stale target: after the 60-attempt budget, a still-missing row
		// stops the loop (no spin forever).
		const shared2 = makeShared();
		const cleanup2 = scrollToRowWithHighlight({
			shared: shared2,
			target: "ghost",
			matchFn: () => undefined,
			onExpire: () => {},
		});
		vi.advanceTimersByTime(10_000);
		expect(scrollSpy).toHaveBeenCalledTimes(1); // no extra calls
		cleanup();
		cleanup2();
	});

	it("applies the ring onFound and clears it via onExpire after the lifetime", () => {
		const row = document.createElement("div");
		document.body.appendChild(row);
		const shared = makeShared();
		const onFound = vi.fn();
		const onExpire = vi.fn();

		const cleanup = scrollToRowWithHighlight({
			shared,
			target: "row",
			matchFn: () => row,
			onFound,
			onExpire,
		});

		vi.advanceTimersByTime(1);
		expect(onFound).toHaveBeenCalledWith(row);
		expect(onExpire).not.toHaveBeenCalled();
		expect(shared.scrolledTarget.current).toBe("row");

		// Ring lifetime starts when the row is FOUND.
		vi.advanceTimersByTime(2600);
		expect(onExpire).toHaveBeenCalledWith(row);
		// The guard resets when the ring expires — a later identical
		// deep-link can re-arm.
		expect(shared.scrolledTarget.current).toBeNull();
		cleanup();
	});

	it("honors a custom highlightLifetimeMs", () => {
		const row = document.createElement("div");
		document.body.appendChild(row);
		const shared = makeShared();
		const onExpire = vi.fn();

		const cleanup = scrollToRowWithHighlight({
			shared,
			target: "row",
			matchFn: () => row,
			onExpire,
			highlightLifetimeMs: 1000,
		});

		vi.advanceTimersByTime(1);
		vi.advanceTimersByTime(998);
		expect(onExpire).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(onExpire).toHaveBeenCalledTimes(1);
		cleanup();
	});

	it("is one-shot per target (shared guard) — a second call for the same target no-ops", () => {
		const scrollSpy = vi.fn();
		(
			Element.prototype as unknown as { scrollIntoView: unknown }
		).scrollIntoView = scrollSpy;
		const row = document.createElement("div");
		const shared = makeShared();
		shared.scrolledTarget.current = "same-target";

		const cleanup = scrollToRowWithHighlight({
			shared,
			target: "same-target",
			matchFn: () => row,
			onExpire: () => {},
		});

		vi.advanceTimersByTime(10_000);
		expect(scrollSpy).not.toHaveBeenCalled();
		cleanup();
	});

	it("clears a previously armed highlight timer before re-arming", () => {
		const rowA = document.createElement("div");
		const rowB = document.createElement("div");
		document.body.append(rowA, rowB);
		const shared = makeShared();
		const expireA = vi.fn();
		const expireB = vi.fn();

		const cleanupA = scrollToRowWithHighlight({
			shared,
			target: "a",
			matchFn: () => rowA,
			onExpire: expireA,
		});
		vi.advanceTimersByTime(1);
		const firstTimer = shared.highlightTimer.current;
		expect(firstTimer).not.toBeNull();

		// New target before A's lifetime elapses — A's timer is replaced.
		const cleanupB = scrollToRowWithHighlight({
			shared,
			target: "b",
			matchFn: () => rowB,
			onExpire: expireB,
		});
		vi.advanceTimersByTime(1);
		expect(shared.highlightTimer.current).not.toBe(firstTimer);

		vi.advanceTimersByTime(2600);
		expect(expireA).not.toHaveBeenCalled(); // replaced, not fired
		expect(expireB).toHaveBeenCalledTimes(1);
		cleanupA();
		cleanupB();
	});

	it("cleanup cancels pending retries (no scroll after cleanup)", () => {
		const scrollSpy = vi.fn();
		(
			Element.prototype as unknown as { scrollIntoView: unknown }
		).scrollIntoView = scrollSpy;
		const row = document.createElement("div");
		const shared = makeShared();
		let rowRendered = false;

		const cleanup = scrollToRowWithHighlight({
			shared,
			target: "cancelled",
			matchFn: () => (rowRendered ? row : undefined),
			onExpire: () => {},
		});

		vi.advanceTimersByTime(100);
		rowRendered = true;
		cleanup();
		vi.advanceTimersByTime(10_000);
		expect(scrollSpy).not.toHaveBeenCalled();
	});
});
