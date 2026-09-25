import { describe, expect, it } from "vitest";

//mic auto-select prefers `default: true` ─────────────────

//Home.tsx extraction preserves the R7-F13 contract ──────────

describe("EC-12: Home.tsx extraction (subcomponents moved to ./home/)", () => {
	it("Home.tsx imports the extracted subcomponents from ./home/", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		// Each extracted piece still used by Home must be imported (not
		// inlined). RecordingErrorCard is no longer mounted by Home
		// (errors now live in the single dynamic status line below the
		// mic button), so its import is gone by design; the status pill
		// is imported from ./home/components.
		expect(src).toContain("./home/lib/cache");
		expect(src).toContain("./home/lib/constants");
		expect(src).toContain("./home/lib/status");
		expect(src).toContain("./home/components/MicToggleButton");
		expect(src).toContain("./home/components/RecordingStatusPill");
		expect(src).toContain("./home/hooks/useFirstRecordingCelebration");
	});

	it("Home.tsx source declares debouncedRefreshFromEvent via useCallback (R7-F13 preserved)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		// R7-F13 contract: the shared refresh callback is declared via
		// useCallback in Home.tsx (NOT extracted to a hook) so the test
		// can grep for the declaration.
		expect(src).toContain("const debouncedRefreshFromEvent = useCallback(");
		// And passed to both usePythonEvent subscriptions.
		const uses = src.match(/debouncedRefreshFromEvent\b/g) ?? [];
		// 1 declaration + at least 2 subscription uses.
		expect(uses.length).toBeGreaterThanOrEqual(3);
	});

	it("Home.tsx no longer inlines RecordingStatusPill / MicToggleButton / RecordingErrorCard", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		// Strip comments before checking, the extraction leaves a
		// header comment naming the extracted files.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		// The inline function declarations must be gone.
		expect(stripped).not.toMatch(/function RecordingStatusPill\b/);
		expect(stripped).not.toMatch(/function MicToggleButton\b/);
		expect(stripped).not.toMatch(/function RecordingErrorCard\b/);
		// The module-level cache helpers must be gone (they live in
		// ./home/lib/cache now).
		expect(stripped).not.toMatch(/function loadCachedRecent\b/);
		expect(stripped).not.toMatch(/function persistRecent\b/);
		// The module-level STATUS_COLORS const must be gone (it lives
		// in ./home/lib/constants now).
		expect(stripped).not.toMatch(/const STATUS_COLORS\s*:/);
	});

	it("extracted constants module exports the expected keys", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/home/lib/constants.ts",
			"utf8",
		);
		expect(src).toContain("RECENT_CACHE_KEY");
		expect(src).toContain("STATS_CACHE_KEY");
		expect(src).toContain("FIRST_RECORD_CELEBRATED_KEY");
		expect(src).toContain("FORCE_CANCEL_DELAY_MS");
		expect(src).toContain("STATUS_COLORS");
	});

	it("extracted cache module declares the four pure helpers", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/home/lib/cache.ts",
			"utf8",
		);
		expect(src).toContain("export function loadCachedRecent");
		expect(src).toContain("export function loadCachedStats");
		expect(src).toContain("export function persistRecent");
		expect(src).toContain("export function persistStats");
	});

	it("extracted status module declares normalizeHotkey / statusLabelFor / statusKeyFor", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/home/lib/status.ts",
			"utf8",
		);
		expect(src).toContain("export function normalizeHotkey");
		expect(src).toContain("export function statusLabelFor");
		expect(src).toContain("export function statusKeyFor");
	});
});
