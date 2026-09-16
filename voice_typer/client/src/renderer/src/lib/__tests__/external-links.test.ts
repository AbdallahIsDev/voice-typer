/**
 * Tests for `lib/external-links.ts` (MO-118).
 *
 * The helper is the ONE route every external https link uses. Under
 * Tauri it must invoke the bridge's `openExternalUrl` (the Rust
 * `open_external_url_command`, which enforces https-only and opens the
 * OS default browser); when the bridge is absent (tests, bubble
 * runtime) it must fall back to the classic `window.open` behavior so
 * nothing regresses on a runtime that omits the namespace.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { openExternalUrl } from "@/lib/external-links";

/**
 * Install a PARTIAL bridge stub.
 *
 * `window.window_` is typed as the full `WindowBridge`; these tests only
 * need the one method they exercise, so the holder is narrowed to
 * `unknown` rather than asserting a complete bridge shape (a
 * `as unknown as WindowBridge` cast would silently accept a typo'd or
 * missing method on every other test in this file).
 */
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
