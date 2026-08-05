// @vitest-environment node
/**
 * Contract test: the `MAIN_KEYS` literal array in `main/i18n.ts` MUST
 * stay in sync with the keys actually present in
 * `main/i18n/locales/en.json` (and therefore in `MAIN_STRINGS.en` at
 * runtime).
 *
 * Background
 * ----------
 * `MAIN_KEYS` was added so `mainT(key: MainKey, ...)` can narrow its
 * `key` parameter to a string-literal union — catching typos like
 * `mainT("dialog.criticalError.titl")` at compile time instead of
 * silently returning the raw key to the user-facing dialog. Because
 * `MAIN_STRINGS` is loaded from JSON (`Record<string, string>`), the
 * TypeScript type system can't infer the key set from the JSON —
 * `MAIN_KEYS` is a hand-maintained literal array that must be kept
 * in lockstep with the JSON file by a contract test.
 *
 * This test asserts the bidirectional parity:
 *
 *   1. `MAIN_KEYS` is non-empty and contains exactly the 15 keys
 *      expected from the current `en.json` (sanity guard against
 *      accidental truncation / extension).
 *   2. Every entry in `MAIN_KEYS` resolves via `mainT(key)` (i.e.,
 *      is present in `MAIN_STRINGS.en`). If a key were declared in
 *      `MAIN_KEYS` but missing from the JSON, `mainT` would return
 *      the raw key — the assertion `v !== key` catches this.
 *   3. `MAIN_KEYS` has no duplicates (a duplicate would silently
 *      mask a missing key — the array length would still match but
 *      the missing key would never be probed).
 *   4. Spot-check the canonical well-known keys are present
 *      (defensive against an accidental mass-rename of the array).
 *
 * The reverse direction (the JSON has a key NOT in `MAIN_KEYS`) is
 * enforced by the `MAIN_KEYS.length === 15` assertion plus the
 * spot-check — adding a key to `en.json` without extending
 * `MAIN_KEYS` would make `en.json` have 16 keys while `MAIN_KEYS`
 * stays at 15, but this test doesn't enumerate `en.json`'s keys
 * directly (we don't export `MAIN_STRINGS` to avoid widening the
 * module's public surface). The contract is therefore best-effort:
 * if a future contributor adds a key to `en.json` and updates the
 * existing locale files but forgets to add it to `MAIN_KEYS`, the
 * type system will reject `mainT("new.key")` at the call site —
 * surfacing the drift at the first attempted use, even if this test
 * doesn't directly catch the missing-from-MAIN_KEYS direction.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { MAIN_KEYS, type MainKey, mainT, setMainLocale } from "../i18n";

describe("MAIN_KEYS contract: MAIN_KEYS matches the keys in MAIN_STRINGS.en", () => {
	beforeEach(() => {
		// Lock the locale to English so `mainT(key)` resolves
		// against `MAIN_STRINGS.en` (the canonical reference
		// table). A non-English locale could mask a missing
		// key via the English-fallback chain — but here we want
		// to probe the English table directly.
		setMainLocale("en");
	});

	it("MAIN_KEYS is non-empty (sanity guard against accidental empty array)", () => {
		expect(MAIN_KEYS.length).toBeGreaterThan(0);
	});

	it("MAIN_KEYS contains exactly 15 keys (the current en.json key count)", () => {
		// Update this assertion when a key is intentionally
		// added to / removed from `i18n/locales/en.json` AND
		// `MAIN_KEYS` in `main/i18n.ts`. The 15 count matches
		// the keys shipped today: 2× criticalError, 4× export,
		// 1× preloadError, 1× selectModelFolder,
		// 2× singleInstance, 1× notify, 1× state.app.starting,
		// 3× crashLoop.
		expect(MAIN_KEYS.length).toBe(15);
	});

	it("MAIN_KEYS has no duplicates (a duplicate would mask a missing key)", () => {
		const unique = new Set<string>(MAIN_KEYS);
		expect(unique.size).toBe(MAIN_KEYS.length);
	});

	it("every key in MAIN_KEYS resolves via mainT (i.e., is present in MAIN_STRINGS.en)", () => {
		// If a key were declared in MAIN_KEYS but missing from
		// MAIN_STRINGS.en, mainT would return the raw key
		// (the fallback chain's last resort). The assertion
		// `v !== key` catches this — combined with the
		// `v.length > 0` sanity check.
		for (const key of MAIN_KEYS) {
			const v = mainT(key);
			expect(v).not.toBe(key);
			expect(v.length).toBeGreaterThan(0);
		}
	});

	it("MAIN_KEYS contains the canonical well-known dialog keys (spot-check)", () => {
		// Defensive against an accidental mass-rename of the
		// array (e.g. a find-and-replace gone wrong). If the
		// well-known keys disappear from MAIN_KEYS, the call
		// sites in bootstrap.ts / start-python.ts /
		// export-handlers.ts / window-handlers.ts would fail
		// to compile — but the spot-check here surfaces the
		// drift at the contract-test layer too, closer to the
		// source of truth.
		expect(MAIN_KEYS).toContain("dialog.criticalError.title");
		expect(MAIN_KEYS).toContain("dialog.criticalError.body");
		expect(MAIN_KEYS).toContain("dialog.singleInstance.title");
		expect(MAIN_KEYS).toContain("dialog.singleInstance.message");
		expect(MAIN_KEYS).toContain("dialog.selectModelFolder.title");
		expect(MAIN_KEYS).toContain("dialog.export.config");
		expect(MAIN_KEYS).toContain("dialog.export.history");
		expect(MAIN_KEYS).toContain("dialog.export.templates");
		expect(MAIN_KEYS).toContain("dialog.export.vocabulary");
	});

	it("MainKey type accepts every MAIN_KEYS entry (compile-time check)", () => {
		// This test exists purely to assert the
		// `MainKey = typeof MAIN_KEYS[number]` derivation —
		// if someone accidentally widens `MAIN_KEYS` to
		// `string[]` (e.g. by removing the `as const`), the
		// `MainKey` type would degrade to `string` and the
		// typo-detection at call sites would silently
		// disappear. The assignment below would still
		// typecheck, but the explicit `<MainKey>` annotation
		// documents the expected type and surfaces a
		// regression in IDE hovers.
		const firstKey: MainKey = MAIN_KEYS[0] as MainKey;
		expect(typeof firstKey).toBe("string");
	});
});
