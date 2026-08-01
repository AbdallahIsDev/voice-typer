/**
 * Tests for ``checkScreenReaderConflict`` — the heuristic, OFFLINE
 * screen-reader-conflict detector added alongside ``checkHotkeyConflict``.
 *
 * Coverage:
 *   - macOS (``navigator.platform = "MacIntel"``) + ``<caps_lock>`` →
 *     conflict, srSoftware = ["VoiceOver"].
 *   - Windows (``navigator.platform = "Win32"``) + ``<caps_lock>`` →
 *     conflict, srSoftware = ["Narrator", "NVDA", "JAWS"].
 *   - Linux (``navigator.platform = "Linux x86_64"``) +
 *     ``<caps_lock>`` → NO conflict (Orca uses Insert by default; Caps
 *     Lock is not reserved).
 *   - Empty hotkey / unknown platform → no conflict, no crash.
 *   - Non-caps hotkey (e.g. ``<f2>``) → no conflict on any platform.
 *   - Case- and bracket-insensitive matching: ``"Caps_Lock"`` (no
 *     brackets, mixed case) still matches the JSON entry
 *     ``"<caps_lock>"``.
 *   - Defensive copy: mutating the returned ``srSoftware`` array does
 *     NOT mutate the internal JSON lookup table (so a second call
 *     still returns the original list).
 *
 * The check is pure-offline per C-DATA-1 (CONSTRAINTS.md): no network
 * calls, no OS query. ``navigator.platform`` is the only external
 * signal read; each test stubs it deterministically.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { checkScreenReaderConflict } from "../hotkey/checkHotkeyConflict";

/**
 * Stub ``navigator.platform`` to a known value. ``vi.stubGlobal`` is
 * used (rather than ``Object.defineProperty(navigator, ...)``) because
 * the existing hotkey-utils tests use this pattern and because some
 * jsdom versions have ``navigator`` defined as a non-configurable
 * getter — ``vi.stubGlobal`` sidesteps that by replacing the entire
 * ``globalThis.navigator`` reference.
 *
 * ``userAgent`` is preserved (copied from the real jsdom navigator)
 * so any code that happens to read it during the test doesn't crash
 * on ``undefined``.
 */
function stubPlatform(platform: string): void {
	const realUserAgent =
		typeof navigator !== "undefined" ? navigator.userAgent : "";
	vi.stubGlobal("navigator", {
		platform,
		userAgent: realUserAgent,
	});
}

describe("checkScreenReaderConflict", () => {
	beforeEach(() => {
		// jsdom defaults navigator.platform to "Linux x86_64" on Linux
		// runners — restore between tests so a forgotten stub doesn't
		// leak into the next test.
		vi.unstubAllGlobals();
	});
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	// ── macOS (VoiceOver) ────────────────────────────────────────────────
	describe("macOS (navigator.platform = 'MacIntel')", () => {
		beforeEach(() => stubPlatform("MacIntel"));

		it("returns conflict for <caps_lock> with srSoftware=['VoiceOver']", () => {
			const result = checkScreenReaderConflict("<caps_lock>");
			expect(result.conflict).toBe(true);
			expect(result.platform).toBe("darwin");
			expect(result.srSoftware).toEqual(["VoiceOver"]);
		});

		it("returns no conflict for <f2> (no SR uses F2 as a default modifier)", () => {
			const result = checkScreenReaderConflict("<f2>");
			expect(result.conflict).toBe(false);
			expect(result.platform).toBe("darwin");
			expect(result.srSoftware).toEqual([]);
		});

		it("returns no conflict for a modifier-only combo like <ctrl>+<shift>", () => {
			const result = checkScreenReaderConflict("<ctrl>+<shift>");
			expect(result.conflict).toBe(false);
			expect(result.srSoftware).toEqual([]);
		});
	});

	// ── Windows (Narrator / NVDA / JAWS) ─────────────────────────────────
	describe("Windows (navigator.platform = 'Win32')", () => {
		beforeEach(() => stubPlatform("Win32"));

		it("returns conflict for <caps_lock> with srSoftware=['Narrator','NVDA','JAWS']", () => {
			const result = checkScreenReaderConflict("<caps_lock>");
			expect(result.conflict).toBe(true);
			expect(result.platform).toBe("win32");
			expect(result.srSoftware).toEqual(["Narrator", "NVDA", "JAWS"]);
		});

		it("returns no conflict for <insert> (Narrator's OTHER default modifier — not in our table)", () => {
			// Insert is a Narrator alternative, but we only flag Caps Lock
			// (the most common default + the one users actually pick).
			// Insert is also the default for Orca on Linux; flagging it
			// would produce false positives for non-SR users who bind it.
			const result = checkScreenReaderConflict("<insert>");
			expect(result.conflict).toBe(false);
			expect(result.srSoftware).toEqual([]);
		});

		it("returns no conflict for <ctrl>+<alt>+<v>", () => {
			const result = checkScreenReaderConflict("<ctrl>+<alt>+<v>");
			expect(result.conflict).toBe(false);
			expect(result.srSoftware).toEqual([]);
		});
	});

	// ── Linux (Orca uses Insert; Caps Lock NOT reserved) ─────────────────
	describe("Linux (navigator.platform = 'Linux x86_64')", () => {
		beforeEach(() => stubPlatform("Linux x86_64"));

		it("returns NO conflict for <caps_lock> (Orca uses Insert, not Caps Lock)", () => {
			// This is the key Linux invariant: Caps Lock is safe to assign
			// on Linux because no mainstream Linux SR uses it as a
			// default modifier. The empty ``linux`` array in
			// hotkey_reserved.json::screen_reader_conflicts pins this.
			const result = checkScreenReaderConflict("<caps_lock>");
			expect(result.conflict).toBe(false);
			expect(result.platform).toBe("linux");
			expect(result.srSoftware).toEqual([]);
		});

		it("returns no conflict for <insert> (Orca's default — but we don't flag Insert)", () => {
			const result = checkScreenReaderConflict("<insert>");
			expect(result.conflict).toBe(false);
			expect(result.srSoftware).toEqual([]);
		});
	});

	// ── Edge cases ───────────────────────────────────────────────────────
	describe("edge cases", () => {
		it("returns no conflict for empty hotkey string", () => {
			stubPlatform("MacIntel");
			const result = checkScreenReaderConflict("");
			expect(result.conflict).toBe(false);
			expect(result.srSoftware).toEqual([]);
		});

		it("returns platform='unknown' and no conflict when navigator is unavailable", () => {
			// Simulate an SSR / non-DOM environment where navigator is
			// undefined. The function must NOT crash — it returns
			// platform="unknown" and skips the lookup.
			vi.stubGlobal("navigator", undefined);
			const result = checkScreenReaderConflict("<caps_lock>");
			expect(result.conflict).toBe(false);
			expect(result.platform).toBe("unknown");
			expect(result.srSoftware).toEqual([]);
		});

		it("returns platform='unknown' for an unrecognized navigator.platform value", () => {
			// e.g. FreeBSD, Haiku, or a future platform not in our table.
			stubPlatform("FreeBSD amd64");
			const result = checkScreenReaderConflict("<caps_lock>");
			expect(result.conflict).toBe(false);
			expect(result.platform).toBe("unknown");
			expect(result.srSoftware).toEqual([]);
		});

		it("matches case- and bracket-insensitively (Caps_Lock without brackets)", () => {
			stubPlatform("MacIntel");
			// The pynput convention is <caps_lock> (lowercase, brackets).
			// But callers might pass the bare token (e.g. when comparing
			// a preset dropdown value that strips brackets). The check
			// must still match.
			const result = checkScreenReaderConflict("Caps_Lock");
			expect(result.conflict).toBe(true);
			expect(result.platform).toBe("darwin");
			expect(result.srSoftware).toEqual(["VoiceOver"]);
		});

		it("matches when navigator.platform has different case ('macintel')", () => {
			// navigator.platform is officially case-sensitive ("MacIntel"
			// with capital M and I), but we lowercase before comparing
			// so a future Chromium change to lowercase the value doesn't
			// silently skip the SR warning.
			stubPlatform("macintel");
			const result = checkScreenReaderConflict("<caps_lock>");
			expect(result.conflict).toBe(true);
			expect(result.platform).toBe("darwin");
		});

		it("returns a defensive copy of srSoftware (mutating the result does not affect subsequent calls)", () => {
			stubPlatform("Win32");
			const r1 = checkScreenReaderConflict("<caps_lock>");
			expect(r1.srSoftware).toEqual(["Narrator", "NVDA", "JAWS"]);
			// Mutate the returned array — push, pop, sort.
			r1.srSoftware.push("BogusSR");
			r1.srSoftware.sort();
			// A second call must still return the original, unmutated list.
			const r2 = checkScreenReaderConflict("<caps_lock>");
			expect(r2.srSoftware).toEqual(["Narrator", "NVDA", "JAWS"]);
		});
	});
});
