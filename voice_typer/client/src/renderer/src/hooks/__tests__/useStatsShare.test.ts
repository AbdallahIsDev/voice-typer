import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("html-to-image", () => ({
	toPng: vi.fn(),
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

import { toPng } from "html-to-image";
import { canShareStats, useStatsShare } from "../useStatsShare";

const captureFailedKey = "stats.shareImage.captureFailed";

function makeElement(overrides?: Partial<HTMLDivElement>): HTMLDivElement {
	return {
		getBoundingClientRect: () => ({ width: 1200, height: 630 }),
		offsetWidth: 1200,
		offsetHeight: 630,
		...overrides,
	} as unknown as HTMLDivElement;
}

/** A valid-looking PNG data URL (content is mocked away — only the
 * string shape matters to the bridge calls). */
const PNG_DATA_URL = "data:image/png;base64,AA==";

function installWindowBridge(overrides?: {
	saveStatsImage?: (dataUrl: string, name: string, mode: string) => Promise<unknown>;
	copyStatsImage?: (dataUrl: string) => Promise<unknown>;
	revealStatsImage?: (path: string) => Promise<unknown>;
}) {
	const bridge = {
		saveStatsImage: vi.fn().mockResolvedValue({ success: true, path: "/tmp/x.png" }),
		copyStatsImage: vi.fn().mockResolvedValue({ success: true }),
		revealStatsImage: vi.fn().mockResolvedValue({ success: true }),
		...overrides,
	};
	(window as unknown as { window_?: unknown }).window_ = bridge;
	return bridge;
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

describe("useStatsShare hook", () => {
	afterEach(() => {
		delete (window as unknown as { window_?: unknown }).window_;
		vi.restoreAllMocks();
		vi.clearAllMocks();
	});

	it("reports an error when no ref is attached", async () => {
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));

		await act(async () => {
			const dataUrl = await result.current.captureImage();
			expect(dataUrl).toBeNull();
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
			const dataUrl = await result.current.captureImage();
			expect(dataUrl).toBeNull();
		});

		expect(onError).toHaveBeenCalledWith(captureFailedKey);
		expect(toPng).not.toHaveBeenCalled();
	});

	it("captureImage returns the PNG data URL on success", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		const { result } = renderHook(() => useStatsShare());
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const dataUrl = await result.current.captureImage();
			expect(dataUrl).toBe(PNG_DATA_URL);
		});
		expect(toPng).toHaveBeenCalled();
	});

	it("reports an error when toPng throws", async () => {
		vi.mocked(toPng).mockRejectedValue(new Error("paint failure"));
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const dataUrl = await result.current.captureImage();
			expect(dataUrl).toBeNull();
		});

		expect(onError).toHaveBeenCalledWith(captureFailedKey);
	});

	it("downloadImage saves to Downloads via the bridge and returns the path", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		const bridge = installWindowBridge();
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const path = await result.current.downloadImage("my-stats");
			expect(path).toBe("/tmp/x.png");
		});

		expect(bridge.saveStatsImage).toHaveBeenCalledWith(
			PNG_DATA_URL,
			"my-stats",
			"downloads",
		);
		expect(onError).not.toHaveBeenCalled();
	});

	it("downloadImage surfaces the bridge error via onError", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		installWindowBridge({
			saveStatsImage: vi.fn().mockResolvedValue({ success: false, error: "boom" }),
		});
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const path = await result.current.downloadImage();
			expect(path).toBeNull();
		});
		expect(onError).toHaveBeenCalledWith("boom");
	});

	it("downloadImage falls back to an anchor download when no bridge exists", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		const clickSpy = vi
			.spyOn(HTMLAnchorElement.prototype, "click")
			.mockImplementation(() => {});
		const { result } = renderHook(() => useStatsShare());
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const path = await result.current.downloadImage("my-stats");
			expect(path).toBeNull();
		});
		expect(clickSpy).toHaveBeenCalled();
	});

	it("saveImageAs opens the native Save As dialog via the bridge", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		const bridge = installWindowBridge({
			saveStatsImage: vi
				.fn()
				.mockResolvedValue({ success: true, path: "/pick/here.png" }),
		});
		const { result } = renderHook(() => useStatsShare());
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const path = await result.current.saveImageAs();
			expect(path).toBe("/pick/here.png");
		});
		expect(bridge.saveStatsImage).toHaveBeenCalledWith(
			PNG_DATA_URL,
			"voice-typer-stats",
			"saveAs",
		);
	});

	it("saveImageAs is silent when the user cancels the dialog", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		installWindowBridge({
			saveStatsImage: vi.fn().mockResolvedValue({ success: false, canceled: true }),
		});
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const path = await result.current.saveImageAs();
			expect(path).toBeNull();
		});
		expect(onError).not.toHaveBeenCalled();
	});

	it("copyImageToClipboard copies via the bridge and returns true", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		const bridge = installWindowBridge();
		const { result } = renderHook(() => useStatsShare());
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const ok = await result.current.copyImageToClipboard();
			expect(ok).toBe(true);
		});
		expect(bridge.copyStatsImage).toHaveBeenCalledWith(PNG_DATA_URL);
	});

	it("copyImageToClipboard surfaces failures via onError", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		installWindowBridge({
			copyStatsImage: vi.fn().mockResolvedValue({ success: false, error: "nope" }),
		});
		const onError = vi.fn();
		const { result } = renderHook(() => useStatsShare({ onError }));
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const ok = await result.current.copyImageToClipboard();
			expect(ok).toBe(false);
		});
		expect(onError).toHaveBeenCalledWith("nope");
	});

	it("copyImageToClipboard uses navigator.clipboard when no bridge exists", async () => {
		vi.mocked(toPng).mockResolvedValue(PNG_DATA_URL);
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({ blob: async () => new Blob(["x"]) }),
		);
		class FakeClipboardItem {
			constructor(public data: Record<string, Blob>) {}
		}
		vi.stubGlobal("ClipboardItem", FakeClipboardItem);
		const write = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, "clipboard", {
			value: { write },
			configurable: true,
		});
		const { result } = renderHook(() => useStatsShare());
		result.current.imageRef.current = makeElement();

		await act(async () => {
			const ok = await result.current.copyImageToClipboard();
			expect(ok).toBe(true);
		});
		expect(write).toHaveBeenCalled();
	});
});
