import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TodayStats } from "@/types/ipc";

vi.mock("html-to-image", () => ({
	toPng: vi.fn(),
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

import { toPng } from "html-to-image";
import {
	canShareStats,
	computeShareStats,
	useStatsShare,
} from "../useStatsShare";

const captureFailedKey = "stats.shareImage.captureFailed";

function makeElement(overrides?: Partial<HTMLDivElement>): HTMLDivElement {
	return {
		getBoundingClientRect: () => ({ width: 600, height: 500 }),
		offsetWidth: 600,
		offsetHeight: 500,
		...overrides,
	} as unknown as HTMLDivElement;
}

describe("canShareStats", () => {
	it("is false when there is neither today nor historical transcription", () => {
		expect(canShareStats({ todayCount: 0, totalCount: 0 })).toBe(false);
	});

	it("is true when the user has dictated today", () => {
		expect(canShareStats({ todayCount: 1, totalCount: 0 })).toBe(true);
	});

	it("is true when the user has historical transcriptions", () => {
		expect(canShareStats({ todayCount: 0, totalCount: 5 })).toBe(true);
	});
});

describe("computeShareStats", () => {
	it("computes wpm, minutes saved, and faster-than-avg for a dictation", () => {
		const stats = computeShareStats(
			{ duration: 60, word_count: 100 } as TodayStats,
			"openai",
		);
		expect(stats.wpm).toBe(100);
		expect(stats.wpmDisplay).toBe("100");
		expect(stats.minutesSaved).toBe(2);
		expect(stats.minutesSavedDisplay).toBe("2");
		expect(stats.fasterThanAvg).toBe("stats.shareImage.fasterThanAvg");
		expect(stats.modeDisplay).toBe("stats.shareImage.cloudMode");
		expect(stats.modeDetail).toBe("stats.shareImage.cloudApi");
	});

	it("returns zeros when there is no active duration", () => {
		const stats = computeShareStats(
			{ duration: 0, word_count: 0 } as TodayStats,
			"local",
		);
		expect(stats.wpm).toBe(0);
		expect(stats.wpmDisplay).toBe("0");
		expect(stats.minutesSaved).toBe(0);
		expect(stats.fasterThanAvg).toBe("stats.shareImage.fasterThanAvg");
	});

	it("labels offline backends as local-model mode", () => {
		const stats = computeShareStats(
			{ duration: 0, word_count: 0 } as TodayStats,
			"local",
		);
		expect(stats.modeDisplay).toBe("stats.shareImage.offlineMode");
		expect(stats.modeDetail).toBe("stats.shareImage.localModel");
	});
});

describe("useStatsShare hook", () => {
	afterEach(() => {
		delete (navigator as { canShare?: unknown }).canShare;
		delete (navigator as { share?: unknown }).share;
		vi.unstubAllGlobals();
		// Restores the HTMLAnchorElement.prototype.click spy created in
		// the anchor-download test so it cannot leak into later tests.
		vi.restoreAllMocks();
		vi.clearAllMocks();
	});

	it("reports an error when no ref is attached", async () => {
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));

		await act(async () => {
			await result.current.shareAsImage();
		});

		expect(onError).toHaveBeenCalledWith(captureFailedKey);
		expect(toPng).not.toHaveBeenCalled();
	});

	it("reports an error when the target element has zero size", async () => {
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement({
			offsetWidth: 0,
			offsetHeight: 0,
		});

		await act(async () => {
			await result.current.shareAsImage();
		});

		expect(onError).toHaveBeenCalledWith(captureFailedKey);
		expect(toPng).not.toHaveBeenCalled();
	});

	it("downloads the PNG via an anchor when native share is unavailable", async () => {
		vi.mocked(toPng).mockResolvedValue("data:image/png;base64,AA==");
		const clickSpy = vi
			.spyOn(HTMLAnchorElement.prototype, "click")
			.mockImplementation(() => {});
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			await result.current.shareAsImage("my-stats");
		});

		expect(toPng).toHaveBeenCalled();
		expect(clickSpy).toHaveBeenCalled();
		expect(onError).not.toHaveBeenCalled();
	});

	it("uses the native share sheet when navigator.share is available", async () => {
		vi.mocked(toPng).mockResolvedValue("data:image/png;base64,AA==");
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({ blob: async () => new Blob(["x"]) }),
		);
		const canShare = vi.fn(() => true);
		const share = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, "canShare", {
			value: canShare,
			configurable: true,
		});
		Object.defineProperty(navigator, "share", {
			value: share,
			configurable: true,
		});
		const { result } = renderHook(() => useStatsShare());
		result.current.imageRef.current = makeElement();

		await act(async () => {
			await result.current.shareAsImage("my-stats");
		});

		expect(canShare).toHaveBeenCalled();
		expect(share).toHaveBeenCalled();
	});

	it("reports an error when toPng throws", async () => {
		vi.mocked(toPng).mockRejectedValue(new Error("paint failure"));
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			await result.current.shareAsImage();
		});

		expect(onError).toHaveBeenCalledWith(captureFailedKey);
	});
});
