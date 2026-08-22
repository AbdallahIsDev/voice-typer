/**
 * Behavioral + performance regression tests for the extracted
 * `RecordingTimer` component (`pages/home/components/RecordingTimer.tsx`).
 *
 * The MM:SS timer previously lived inline in Home.tsx: its
 * elapsed-seconds state + 1s `setInterval` sat in the page component,
 * so EVERY per-second tick re-rendered the entire Home tree (stat
 * cards, activity list, share image, …) just to bump two digits.
 *
 * The extraction moves the interval + state into a `React.memo`'d leaf:
 *
 *   1. Rendered output is unchanged — role="timer",
 *      aria-live="off" (explicit), the localized
 *      "Recording duration: MM:SS" aria-label, zero-padded MM:SS text.
 *   2. The per-second tick re-renders ONLY the timer — the parent's
 *      render count must stay flat while the displayed time advances
 *      (render-counting pattern from
 *      components/dashboard/__tests__/stats-share-image-memo.test.tsx,
 *      using the i18n `t()` call count as the render proxy plus a
 *      parent probe component).
 *   3. The memo gates unrelated parent re-renders (same `isRecording`
 *      → no timer re-render; changed `isRecording` → timer re-renders,
 *      NEVER-DOWNGRADE).
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Count `t()` invocations — RecordingTimer calls `t("home.timerAria", …)`
// exactly once per render while visible, so the count is a faithful
// render-count proxy (same technique as stats-share-image-memo.test.tsx).
let tCallCount = 0;
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			tCallCount++;
			return actual.t(key, params);
		},
	};
});

import RecordingTimer from "@/pages/home/components/RecordingTimer";

// Parent probe that exposes a forced re-render AND counts its own
// renders, so the tests can assert both directions of the isolation
// contract.
let forceParentRerender: () => void = () => {};
let parentRenderCount = 0;
function TestParent({ isRecording }: { isRecording: boolean }) {
	parentRenderCount++;
	const [, setTick] = useState(0);
	forceParentRerender = () => setTick((t) => t + 1);
	return <RecordingTimer isRecording={isRecording} />;
}

describe("RecordingTimer — rendered output contract", () => {
	beforeEach(() => {
		cleanup();
		tCallCount = 0;
	});

	afterEach(() => {
		cleanup();
		vi.useRealTimers();
	});

	it("renders 00:00 with role='timer', explicit aria-live='off', and the localized duration label while recording", () => {
		render(<RecordingTimer isRecording />);
		const timer = screen.getByLabelText(/Recording duration:/i);
		expect(timer.getAttribute("role")).toBe("timer");
		expect(timer.getAttribute("aria-live")).toBe("off");
		expect(timer.textContent).toBe("00:00");
	});

	it("renders nothing when not recording", () => {
		render(<RecordingTimer isRecording={false} />);
		expect(screen.queryByLabelText(/Recording duration:/i)).toBeNull();
	});

	it("advances one second per tick in MM:SS format", async () => {
		vi.useFakeTimers();
		render(<RecordingTimer isRecording />);
		const timer = screen.getByLabelText(/Recording duration:/i);

		act(() => {
			vi.advanceTimersByTime(1000);
		});
		expect(timer.textContent).toBe("00:01");
		expect(timer.getAttribute("aria-label")).toBe("Recording duration: 00:01");

		act(() => {
			vi.advanceTimersByTime(2000);
		});
		expect(timer.textContent).toBe("00:03");

		act(() => {
			vi.advanceTimersByTime(57_000);
		});
		expect(timer.textContent).toBe("01:00");
	});

	it("stops advancing and resets after recording stops, then restarts from 00:00", async () => {
		vi.useFakeTimers();
		const { rerender } = render(<RecordingTimer isRecording />);
		act(() => {
			vi.advanceTimersByTime(5000);
		});
		expect(screen.getByLabelText(/Recording duration:/i).textContent).toBe(
			"00:05",
		);

		rerender(<RecordingTimer isRecording={false} />);
		act(() => {
			vi.advanceTimersByTime(10_000);
		});
		expect(screen.queryByLabelText(/Recording duration:/i)).toBeNull();

		rerender(<RecordingTimer isRecording />);
		expect(screen.getByLabelText(/Recording duration:/i).textContent).toBe(
			"00:00",
		);
		act(() => {
			vi.advanceTimersByTime(1000);
		});
		expect(screen.getByLabelText(/Recording duration:/i).textContent).toBe(
			"00:01",
		);
	});
});

describe("RecordingTimer — per-second tick must NOT re-render the parent", () => {
	beforeEach(() => {
		cleanup();
		tCallCount = 0;
		parentRenderCount = 0;
		forceParentRerender = () => {};
	});

	afterEach(() => {
		cleanup();
		vi.useRealTimers();
	});

	it("keeps the parent render count flat while the displayed time advances", async () => {
		vi.useFakeTimers();
		render(<TestParent isRecording />);
		expect(parentRenderCount).toBe(1);
		const rendersAfterMount = tCallCount;
		expect(rendersAfterMount).toBeGreaterThan(0);
		const timer = screen.getByLabelText(/Recording duration:/i);

		// Three seconds of ticking — each advanced in its own act() so
		// every tick commits separately: the timer's own state updates,
		// but the parent must not re-render (the whole point of owning
		// the interval inside the leaf). Each tick still re-renders the
		// timer itself — pinned by the growing t() call count.
		act(() => {
			vi.advanceTimersByTime(1000);
		});
		act(() => {
			vi.advanceTimersByTime(1000);
		});
		act(() => {
			vi.advanceTimersByTime(1000);
		});
		expect(timer.textContent).toBe("00:03");
		expect(parentRenderCount).toBe(1);
		expect(tCallCount).toBe(rendersAfterMount + 3);
	});

	it("memo gate: an unrelated parent re-render with unchanged isRecording skips the timer", async () => {
		render(<TestParent isRecording />);
		const rendersAfterMount = tCallCount;
		expect(rendersAfterMount).toBeGreaterThan(0);

		act(() => {
			forceParentRerender();
		});
		// Parent re-rendered…
		expect(parentRenderCount).toBe(2);
		// …but the memo'd timer did not (no additional t() calls).
		expect(tCallCount).toBe(rendersAfterMount);
		// The timer is still mounted and correct.
		expect(screen.getByLabelText(/Recording duration:/i).textContent).toBe(
			"00:00",
		);
	});

	it("NEVER-DOWNGRADE: changing isRecording re-renders the timer (mount/unmount still works)", async () => {
		const { rerender } = render(<TestParent isRecording={false} />);
		expect(screen.queryByLabelText(/Recording duration:/i)).toBeNull();
		const rendersWhileHidden = tCallCount;

		rerender(<TestParent isRecording />);
		expect(tCallCount).toBeGreaterThan(rendersWhileHidden);
		expect(screen.getByLabelText(/Recording duration:/i)).toBeTruthy();
	});
});
