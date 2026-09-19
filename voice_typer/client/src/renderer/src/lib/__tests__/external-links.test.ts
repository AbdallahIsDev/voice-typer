import { afterEach, describe, expect, it, vi } from "vitest";

import { openExternalUrl } from "@/lib/external-links";

function setBridge(stub: unknown): void {
	(window as unknown as { window_?: unknown }).window_ = stub;
}

afterEach(() => {
	vi.restoreAllMocks();
	setBridge(undefined);
});

describe("openExternalUrl", () => {
	it("routes through the bridge when present (Tauri path)", async () => {
		const opener = vi.fn().mockResolvedValue({ success: true });
		setBridge({ openExternalUrl: opener });
		const openSpy = vi.spyOn(window, "open");

		const result = await openExternalUrl("https://example.com/docs");

		expect(opener).toHaveBeenCalledWith("https://example.com/docs");
		expect(result).toEqual({ success: true });
		// The bridge owns the open: no window.open fallback on this path.
		expect(openSpy).not.toHaveBeenCalled();
	});

	it("surfaces a bridge rejection as success:false, never throwing", async () => {
		setBridge({
			openExternalUrl: vi.fn().mockRejectedValue(new Error("blocked")),
		});

		const result = await openExternalUrl("https://example.com/blocked");

		expect(result.success).toBe(false);
		expect(result.error).toBe("blocked");
	});

	it("falls back to window.open when the bridge is absent", async () => {
		setBridge(undefined);
		const openSpy = vi.spyOn(window, "open").mockReturnValue({} as Window);

		const result = await openExternalUrl("https://example.com/fallback");

		expect(openSpy).toHaveBeenCalledWith(
			"https://example.com/fallback",
			"_blank",
			"noopener,noreferrer",
		);
		expect(result).toEqual({ success: true });
	});

	it("reports a blocked fallback popup instead of throwing", async () => {
		setBridge(undefined);
		vi.spyOn(window, "open").mockReturnValue(null);

		const result = await openExternalUrl("https://example.com/popup");

		expect(result.success).toBe(false);
		expect(result.error).toBe("popup-blocked");
	});
});
