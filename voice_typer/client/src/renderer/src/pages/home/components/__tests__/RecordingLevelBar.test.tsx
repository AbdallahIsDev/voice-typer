/**
 * RecordingLevelBar — compact live audio-level indicator fed by the
 * `recording_level` push event while recording. Pins:
 *  - The raw RMS payload is display-gained and clamped to 0..1 before
 *    reaching the shared LevelBar (aria-valuenow = percentage).
 *  - Non-numeric / missing payloads render as level 0.
 *  - React state sync is throttled: events inside the sync window
 *    update the latest-ref but do NOT re-render.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	pythonMock,
	resetStableMocks,
} from "@/__tests__/helpers/stableMocks";

const { eventHandlers } = vi.hoisted(() => ({
	eventHandlers: {} as Record<string, (data: unknown) => void>,
}));

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("@/hooks/usePython", () =>
	pythonMock({ captureEvents: eventHandlers }),
);

import { RecordingLevelBar } from "@/pages/home/components/RecordingLevelBar";

afterEach(() => {
	cleanup();
	resetStableMocks();
});

function fireBubbleLevel(data: unknown) {
	const handler = eventHandlers.recording_level;
	if (!handler) throw new Error("recording_level handler not registered");
	act(() => {
		handler(data);
	});
}

function progressBar(): HTMLElement {
	return screen.getByRole("progressbar");
}

describe("RecordingLevelBar", () => {
	it("renders the shared LevelBar with a progressbar and starts at 0", () => {
		render(<RecordingLevelBar />);
		expect(screen.getByTestId("recording-level-bar")).toBeTruthy();
		expect(progressBar().getAttribute("aria-valuenow")).toBe("0");
	});

	it("display-gains the raw RMS (x8) and clamps to 0..1", () => {
		// Fresh render per case: the ~8 Hz throttle drops events inside
		// its 120 ms sync window, so each level is asserted on its own
		// component instance.
		const { unmount: u1 } = render(<RecordingLevelBar />);
		// 0.05 * 8 = 0.4 → aria-valuenow 40
		fireBubbleLevel({ rms: 0.05, peak: 0.1 });
		expect(progressBar().getAttribute("aria-valuenow")).toBe("40");
		u1();
		cleanup();

		render(<RecordingLevelBar />);
		// Raw speech max ~0.3 → 0.9*8 clamps to 1.0 → 100
		fireBubbleLevel({ rms: 0.9, peak: 1 });
		expect(progressBar().getAttribute("aria-valuenow")).toBe("100");
	});

	it("treats non-numeric or missing rms as level 0", () => {
		render(<RecordingLevelBar />);
		fireBubbleLevel({ rms: "not-a-number" });
		expect(progressBar().getAttribute("aria-valuenow")).toBe("0");
		fireBubbleLevel(undefined);
		expect(progressBar().getAttribute("aria-valuenow")).toBe("0");
	});

	it("throttles React-state syncs inside the 120 ms window", () => {
		render(<RecordingLevelBar />);
		// First event syncs immediately.
		fireBubbleLevel({ rms: 0.05 });
		expect(progressBar().getAttribute("aria-valuenow")).toBe("40");
		// Second event inside the window: latestRef updates but the
		// rendered value stays at the last synced level.
		fireBubbleLevel({ rms: 0.2 });
		expect(progressBar().getAttribute("aria-valuenow")).toBe("40");
	});
});
