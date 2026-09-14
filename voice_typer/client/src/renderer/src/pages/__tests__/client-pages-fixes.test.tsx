/**
 * Regression tests for the client-pages fix batch.
 * Each describe block pins one fix so a future
 * regression points at the exact contract that broke.
 *
 *   -  History "Clear All" button has a permanent destructive
 *               visual cue (text-destructive + border-destructive at
 *               rest, not just on hover).
 *   -      Home.tsx extraction: the page imports the extracted
 *               subcomponents from `./home/` AND keeps the
 *               `debouncedRefreshFromEvent` declaration in the
 *               composition root (R7-F13 contract preserved).
 *
 * The S2-CR-39 onboarding-mic block was removed with the Microphone
 * step itself (2026-09-14 onboarding overhaul).
 */
import { describe, expect, it } from "vitest";

//mic auto-select prefers `default: true` ─────────────────

// ── S5-CR-104: History Clear All, muted at rest, solid destructive on hover (updated 2026-08-30) ────────────
// Original test pinned a permanently tinted Clear All (text-destructive/80
// at rest). The UI-consistency pass (2026-08-30) standardized ALL Clear All
// controls (History, Vocabulary, Templates) to the shared muted-at-rest →
// solid-red-hover pattern used by ConfirmDialog's destructive action
// (bg-destructive + text-destructive-foreground). This keeps Favorites
// (warning tint) visually distinct from the destructive wipe and makes the
// hover read as solid red + white, not a 5% wash.

describe("S5-CR-104: History Clear All button uses shared muted→solid-destructive hover", () => {
	it("History.tsx Clear All is muted at rest and solid destructive on hover (shared pattern)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/History.tsx", "utf8");
		// Shared pattern: muted at rest, solid red bg + white text on hover.
		expect(src).toContain(
			"text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-destructive-foreground",
		);
		// dark:hover:bg-destructive must ride along: the outline variant's
		// dark:hover:bg-input/30 out-specifies a plain hover:bg-destructive
		// (Tailwind v4 dark = `&:is(.dark *)`), so without the restatement
		// dark mode hovers a translucent grey instead of solid red.
		expect(src).toContain(
			"hover:text-destructive-foreground dark:hover:bg-destructive",
		);
		// Permanent tint must be gone, History used to carry
		// border-destructive/40 text-destructive/80 at rest and a 5% wash
		// on hover (hover:bg-destructive/5) which the pass removed.
		expect(src).not.toContain("text-destructive/80");
		expect(src).not.toContain("border-destructive/40");
		expect(src).not.toContain("hover:bg-destructive/5");
	});

	it("Clear All button className remains distinct from Favorites toggle", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/History.tsx", "utf8");
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		// The Clear All block (onClick={handleClearAll}) must carry the
		// shared destructive hover token, while Favorites (onClick={toggleFavorites})
		// carries warning tokens, they must stay distinct.
		const onClickIdx = stripped.indexOf("onClick={handleClearAll}");
		expect(onClickIdx).toBeGreaterThan(-1);
		const slice = stripped.slice(onClickIdx, onClickIdx + 1200);
		expect(slice).toContain("hover:bg-destructive");
		expect(slice).toContain("hover:text-destructive-foreground");
		expect(slice).toContain("hover:border-destructive");
		// Must NOT still contain the old permanent tint in the active code.
		expect(slice).not.toContain("text-destructive/80");
	});

	it("every shared-pattern destructive-hover button restates dark:hover:bg-destructive", async () => {
		// History Clear All + the shared collection toolbar (which
		// renders the Vocabulary / Templates toolbars' Clear All —
		// the former per-page mirrors were deleted) + the Home
		// discard button share the muted-at-rest →
		// solid-red-hover contract. The outline/ghost variants
		// carry dark:hover:bg-input/30 (resp. dark:hover:bg-muted/50),
		// which out-specifies a plain hover:bg-destructive under
		// Tailwind v4's `&:is(.dark *)` dark variant, each call
		// site must therefore restate dark:hover:bg-destructive or
		// dark mode hovers grey, not red.
		const fs = await import("node:fs");
		const files = [
			"src/renderer/src/pages/History.tsx",
			"src/renderer/src/components/common/CollectionToolbar.tsx",
		];
		for (const file of files) {
			const src = fs.readFileSync(file, "utf8");
			expect(
				src,
				`${file} must restate dark:hover:bg-destructive after hover:text-destructive-foreground`,
			).toContain(
				"hover:text-destructive-foreground dark:hover:bg-destructive",
			);
		}
	});
});

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
